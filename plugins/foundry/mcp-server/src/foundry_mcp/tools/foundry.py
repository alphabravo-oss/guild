"""Foundry tools — defect tracking, observation recording, verdict recording,
and coverage verification.

All operations are local file reads/writes against the foundry-archive/{run}/ directory.
Zero API calls. Zero cost.

THE OBSERVATION / DEFECT SPLIT (FR-001 / FR-002 / US-001)
---------------------------------------------------------
Two ledgers, never mixed. ``defects.json`` blocks the run; ``observations.json``
does not. The rule that decides which a finding lands in is:

    DEMOTION REQUIRES AN EXPLICIT ``target_kind == "comment"``.

Both surfaces enforce that single rule, and both therefore fail in the safe
direction:

  * ``foundry_add_defect`` refuses a finding only when the caller has DECLARED
    the subject is a comment AND vocab names a comment-prose observation class
    AND no denylist entry matches AND the description asserts nothing about
    what the CODE does (``asserts_code_behaviour``). Refusing a defect is
    itself a demotion, so an undeclared subject is never refused — see the
    ``target_kind`` note in ``schemas/vocab.py``: "its absence is a caller bug,
    not a licence to demote". Without this, prose like "the handler below
    returns the wrong status" would match the loose DIRECTION_WORD regex and a
    real defect would be silently blocked.
  * ``foundry_add_observation`` rejects a finding when any denylist entry
    matches OR when the subject was not declared to be a comment, and fires the
    audit tripwire on the attempt (AC-002). It carries NO default for
    ``target_kind`` (D-069): recording an observation IS the demotion, so an
    undeclared subject must reach the NON_COMMENT branch rather than be handed
    a fabricated "comment" by the signature. Any wrapper that supplies its own
    fallback re-opens the hole — the declaration has to come from the stream
    that made the claim.

That second decision is EXPORTED as ``record_denylist_tripwire`` rather than
buried in the writer's body, because a caller that pre-filters on the denylist
before calling the writer silently bypasses the audit signal — which is exactly
what ``foundry_sync_defects`` did, leaving the tripwire empty across every
denylist scenario. Any path about to route a finding out of the defect ledger
calls it first. See its docstring.

THE PROMOTE-DIRECTION FAIL-SAFE (D-093)
---------------------------------------
vocab's precedence rule — a denylist match outranks an observation class —
protects the DEMOTE direction only. Nothing protected the PROMOTE direction,
where refusing a defect filing on an ``observation_class`` match turns a false
positive there into a blocked real defect. Eight of ten textbook
security-property claims were refused as DIRECTION_WORD or filed nowhere at
all, because their prose contained "above" near "but the" and the denylist did
not know the words "signature", "constant-time" or "plaintext".

``asserts_code_behaviour`` is the counterpart guard vocab's docstring says
callers owe that direction, and it is deliberately NOT a second security
vocabulary: widening the denylist fixes the phrasings someone has already
written down, not the next one. What makes a finding a defect is not which
nouns it uses but that it asserts something about what the CODE does, so that
is what this reads — and it is biased to over-match for the same reason the
denylist is, since its false positive costs one comment-prose finding that
stays a defect while its false negative would cost a real one.

Vocabulary — every class name, stream id and defect type — comes from
``schemas/vocab.py`` and is never re-declared here. Alias folding
(``MISPLACED`` -> ``ARCHITECTURAL_PLACEMENT``) uses vocab's
``canonical_defect_type`` so both filing paths persist one spelling.

CYCLE STAMPING (ST-001)
-----------------------
Records are stamped with the SERVER's cycle counter (``state.json['cycle']``,
advanced by the GRIND -> INSPECT boundary handler), never the caller's argument
— not even when the counter is missing or unusable, which resolves to 0. The
caller's assertion survives beside the stamp as ``declared_cycle`` so the
divergence is auditable rather than silent. See ``_server_cycle``.

CONCURRENCY (FR-020 / AC-025)
-----------------------------
Every ledger write goes through ``ledger_transaction``, which holds both a
process-local ``threading.RLock`` (INSPECT's 4+ parallel streams are concurrent
tool calls inside ONE MCP server process) and an ``fcntl.flock`` (separate
server processes on the same repo). Ids come from ``allocate_record_id``, which
is max-suffix+1 rather than the positional ``len+1`` that made two simultaneous
filings collide. Both are exported: the second positional site lives in
``foundry_orchestrator.foundry_sync_defects`` and must call these rather than
re-derive them.
"""

from __future__ import annotations

import fcntl
import json
import re
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path
from typing import Any

from foundry_mcp.schemas.vocab import (
    DEFECT_SOURCE_IDS,
    DEFECT_TYPES,
    NEVER_DEMOTE_CLASSES,
    OBSERVATION_CLASSES,
    canonical_defect_type,
    never_demote_class,
    observation_class,
)
from foundry_mcp.tools.foundry_state import (
    ARCHIVE_DIR,
    get_run_dir,
    set_active_run,
)
from foundry_mcp.tools.display import foundry_hammer, FOUNDRY_SEP

# ANSI colors
_RESET = "\033[0m"
_BOLD = "\033[1m"
_DIM = "\033[2m"
_GREEN = "\033[32m"
_CYAN = "\033[36m"
_WHITE = "\033[37m"
_BCYAN = f"{_BOLD}{_CYAN}"
_BWHITE = f"{_BOLD}{_WHITE}"
_BGREEN = f"{_BOLD}{_GREEN}"


def _format_init_display(run_name: str, temper: bool = False, nyquist: bool = False) -> str:
    """Foundry init display with pixel-art hammer."""
    phases = [
        ("F0",   "RESEARCH", True),
        ("F0.5", "DECOMPOSE", False),
        ("F0.9", "VALIDATE", False),
        ("F1",   "CAST", False),
        ("F2",   "INSPECT", False),
        ("F3",   "GRIND", False),
        ("F4",   "ASSAY", False),
        ("F5",   "TEMPER", False),
        ("F5.5", "NYQUIST", False),
        ("F6",   "DONE", False),
    ]
    lines = [foundry_hammer(f"F O U N D R Y  {run_name}")]
    for pid, pname, active in phases:
        skip = (pid == "F5" and not temper) or (pid == "F5.5" and not nyquist)
        if active:
            icon = f"{_BGREEN}\u25b6{_RESET}"
            label = f"{_BWHITE}{pid} {pname}{_RESET}"
            right = f"{_BGREEN}\u25c0 START{_RESET}"
        elif skip:
            icon = f"{_DIM}\u2500{_RESET}"
            label = f"{_DIM}{pid} {pname}{_RESET}"
            right = f"{_DIM}skip{_RESET}"
        else:
            icon = f"{_DIM}\u25cb{_RESET}"
            label = f"{_DIM}{pid} {pname}{_RESET}"
            right = ""
        lines.append(f"  {icon} {label}  {right}")
    lines.append(FOUNDRY_SEP)
    lines.append(f"Call {_BCYAN}Foundry-Next{_RESET} for instructions.")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Run-artifact reads (D-095 / D-096).
#
# ``_load_json`` was ``json.loads(path.read_text())`` with no try/except and no
# shape check. A git merge conflict, an editor-truncated save or a disk-full
# write leaves a run artifact that raised straight across the MCP boundary: 22
# of 24 corruption x entry-point combinations bricked a tool, and not one of
# them NAMED the file at fault — so the operator could not even open the ledger
# to find out which file to repair. The query path was holed identically, which
# is what removed the last way to diagnose it.
#
# The split mirrors ``foundry_orchestrator``'s BY CONVENTION rather than by
# import — same four names, same refusal shape, same tolerance contract — so
# the two copies can later be folded into one shared loader mechanically. It is
# not an import because that module imports THIS one, and reading back would
# close a cycle in the import graph (the same reason ``_server_cycle`` is a
# deliberate second copy).
#
#   ``_read_document``   — the tolerant core: (data, named problem).
#   ``_document_problem``— the problem alone.
#   ``_load_json``       — total. {} for missing / unreadable / malformed.
#                          NEVER raises, so every reader in this module is safe
#                          by construction rather than by remembered
#                          try/excepts at 13 call sites.
#   ``_artifact_guard``  — the named refusal, at the MCP entry points, which is
#                          where a human is listening.
#
# Tolerance ALONE would have turned D-095 into D-096: a corrupt defects.json
# reads as an empty one, and the next write then replaces the file and reports
# success. The guard is the half that stops the write and names the file, and
# neither half is sufficient without the other.
# ---------------------------------------------------------------------------


def _read_document(path: Path) -> tuple[dict, str | None]:
    """Read a JSON object. Returns ``(data, problem)``; never raises.

    ``problem`` is a human-readable string NAMING THE FILE when the artifact
    exists but is not a readable JSON object, else None. An ABSENT file is not
    a problem — a run legitimately has artifacts it has not written yet, and
    conflating "absent" with "corrupt" is what would make a fresh run refuse to
    start.
    """
    if not path.exists():
        return {}, None
    try:
        raw = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return {}, f"{path.name} could not be read ({type(exc).__name__}: {exc})"
    try:
        data = json.loads(raw)
    except ValueError as exc:
        return {}, f"{path.name} is not valid JSON ({exc})"
    if not isinstance(data, dict):
        return {}, (
            f"{path.name} is not a JSON object (found "
            f"{type(data).__name__}) — every run artifact is a mapping"
        )
    return data, None


def _document_problem(path: Path) -> str | None:
    """The named reason ``path`` is not a readable JSON object, or None."""
    return _read_document(path)[1]


def _load_json(path: Path) -> dict:
    """Total, tolerant read of a run artifact. Returns {} rather than raising.

    Every malformed-container shape — truncated, ``[]``, ``null``, ``42``,
    ``"a string"``, non-UTF-8 — reads as an empty document, so no reader in
    this module can raise across the MCP boundary. A caller that must TELL the
    operator which file is broken uses ``_artifact_guard`` /
    ``_document_problem`` rather than inspecting this return value, which
    cannot distinguish "absent" from "corrupt" by design.
    """
    return _read_document(path)[0]


def _container_shape_problem(
    path: Path, collection_key: str, records: Any
) -> str | None:
    """Named reason ``records`` cannot serve as ``collection_key``'s container.

    The one sentence that describes D-096, written ONCE. Both the pre-flight
    guard (``ledger_shape_problem``, reading from disk) and the primitive
    (``ledger_transaction``, holding the document it already read under the
    lock) ask this same question about the same value, and a run whose refusal
    text depended on which of them noticed first would be telling the operator
    two different stories about one file.
    """
    if records is None or isinstance(records, list):
        return None
    return (
        f"{path.name} has a {collection_key!r} key holding "
        f"{type(records).__name__}, not a list — its records cannot be read, "
        f"and writing over it would discard whatever it does hold"
    )


def ledger_shape_problem(path: Path, collection_key: str) -> str | None:
    """Named reason ``path`` is unusable as a ledger of ``collection_key``.

    One rung below ``_document_problem``: the document may be a perfectly good
    JSON object whose RECORD CONTAINER is not a list. That is D-096 — seeding
    ``defects.json`` with ``{"defects": {...}}`` made ``foundry_add_defect``
    discard every record the file held, re-mint ``D-001`` and return success,
    while a sibling key survived to prove the write had completed.

    A PRE-FLIGHT convenience, no longer the mechanism (D-127). It used to be
    exported on the theory that each ledger writer would remember to call it
    before opening a transaction, and the theory failed the way hand-bound
    guards fail: outside this module it acquired no callers at all, so
    ``foundry_sync_defects`` and ``foundry_mark_defect_fixed`` hit the
    primitive's backstop raise and returned an unhandled-error banner rather
    than the house refusal — the very refusal shape D-095/D-096 were filed to
    establish. The check now lives INSIDE ``ledger_transaction``, where no
    caller can skip it and none has to remember it. This function survives so
    ``_artifact_guard`` can name a broken ledger BEFORE a tool starts work,
    which is a better message than one raised halfway through.
    """
    data, problem = _read_document(path)
    if problem is not None:
        return problem
    return _container_shape_problem(path, collection_key, data.get(collection_key))


#: Which record container each ledger this module touches keeps its records in.
#: Declared ONCE so a guard and a writer cannot disagree about the key, and so
#: adding a ledger does not mean remembering a second list somewhere else.
_LEDGER_KEYS: dict[str, tuple[str, ...]] = {
    "defects.json": ("defects",),
    "observations.json": ("observations", "tripwire"),
    "verdicts.json": ("requirements",),
    "state.json": (),
}


def _artifact_guard(fdir: Path, *names: str) -> dict | None:
    """Named refusal when an artifact this tool must touch is unreadable.

    The house refusal shape: ``error`` names the offending FILES and what is
    wrong with each, ``hint`` names the action. Scoped to the artifacts the
    calling tool actually reads or writes — a corrupt roll-up must not block a
    defect filing that never opens it.
    """
    problems: list[str] = []
    for name in names:
        path = fdir / name
        problem = _document_problem(path)
        for key in _LEDGER_KEYS.get(name, ()):
            if problem is not None:
                break
            problem = ledger_shape_problem(path, key)
        if problem is not None:
            problems.append(problem)
    if not problems:
        return None
    return artifact_refusal(problems)


def artifact_refusal(problems: list[str]) -> dict:
    """The house refusal for an unreadable run artifact, shaped ONCE.

    ``error`` names the offending files and what is wrong with each; ``hint``
    names the action. Two sites raise this shape — the pre-flight
    ``_artifact_guard`` and ``LedgerShapeError``, which carries it so a refusal
    discovered inside the locked primitive reads identically to one caught
    before the tool started (D-127). Written here rather than at each so the
    two cannot drift into telling an operator two different stories about one
    broken file.
    """
    return {
        "error": (
            "Run artifacts cannot be read: " + "; ".join(problems) + ". "
            "This tool refuses rather than writing over a ledger whose "
            "contents it could not read."
        ),
        "hint": (
            "Repair or delete the named file(s) in the run directory, then "
            "retry. A deleted ledger is re-created empty; a corrupt one is "
            "never silently overwritten."
        ),
        "corrupt_artifacts": problems,
    }


def _dict_records(records: list) -> list[dict]:
    """The mapping records in ``records``, skipping anything else (D-097).

    ``allocate_record_id`` already skips a non-dict record; every OTHER scan
    over the same list assumed dicts. So one malformed historical record made
    ``d.get("status")`` raise — and it raised AFTER the new record had been
    appended, so the transaction aborted, the good filing was silently
    discarded, and the caller got a traceback instead of a refusal. Tolerating
    it in ONE place is what keeps the two halves of the same scan consistent.
    """
    return [r for r in records if isinstance(r, dict)]


def _atomic_rename_write(path: Path, data: dict) -> None:
    """Atomic JSON write — write to .tmp then rename. NOT A WRITE PATH.

    THE ONLY BARE RENAME-INTO-PLACE IN THIS MODULE, and the only one there may
    ever be. Every caller is inside ``_locked_document`` or ``write_document``,
    which is what makes "every run-artifact write from this module holds the
    lock" a property of the code rather than of everyone's memory — and it is
    enforced mechanically by ``test_no_unlocked_run_artifact_write_path``,
    which walks this module's AST for rename-into-place call sites and fails on
    one outside the primitives.
    """
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    tmp.rename(path)


# ---------------------------------------------------------------------------
# Locked run-artifact read-modify-write (FR-020 / AC-025).
#
# `_atomic_rename_write` is atomic per write, but the ledger writers read,
# mutate and write as three separate steps. Two Foundry-Defect calls landing
# between one another's read and write both computed the same positional id,
# and the second `.tmp` rename discarded the first record entirely — so the
# race lost a DEFECT, not just an id. Serializing the whole read-modify-write
# is what makes "both survive" true; a non-positional id alone would not have.
#
# D-125 is the same race reached through the LAST unlocked door. verdicts.json
# has two writers — this module's `foundry_add_verdict` and
# `foundry_orchestrator._synthesize_clean_prove_verdicts` — and only the second
# held a lock, so an F4 auto-VERIFY synthesis interleaving with a real
# Foundry-Verdict call silently discarded whichever row renamed first. One
# writer holding the lock is not a lock; it is a coincidence that has not
# failed yet.
#
# So the lock is no longer something a writer opts into. `_locked_document` is
# the read-modify-write, `write_document` is the whole-document replace, and
# `ledger_transaction` is `_locked_document` projected onto one record list.
# Nothing else in this module renames a run artifact into place, and a test
# derives that set from the AST rather than trusting this comment.
# ---------------------------------------------------------------------------

# INSPECT's parallel streams are concurrent tool calls inside ONE MCP server
# process (plugin.json launches one server per project), so an flock alone
# would not serialize them — flock is advisory PER PROCESS and a second
# acquisition from the same process succeeds immediately. The RLock covers
# threads; the flock covers a second server process on the same repo.
_LEDGER_LOCK = threading.RLock()

# path -> in-flight document, per thread (D-099). A nested transaction on a
# path this thread already holds yields the SAME document and defers the write
# to the outermost exit. Without it, nesting deadlocked against our OWN flock:
# an flock is held per open file description, and the transaction opens a fresh
# fd on every entry, so a second acquire from the same thread blocks forever
# rather than succeeding the way the RLock does.
_LEDGER_TX = threading.local()

_RECORD_ID_RE = re.compile(r"\A([A-Za-z]+)-(\d+)\Z")


class LedgerShapeError(RuntimeError):
    """A run artifact cannot be read as the ledger a writer needs it to be.

    Raised inside the locked primitive rather than coerced away, because the
    coercion IS D-096: replacing a non-list ``defects`` container with ``[]``
    discarded every record the file held, re-minted ``D-001`` and reported
    success. It fails CLOSED — nothing is written — rather than open.

    CARRIES ITS OWN REFUSAL (D-127). The claim that "no production path reaches
    this raise" was false: it was reached by both of ``foundry_orchestrator``'s
    ledger writers, whose own pre-flight guard inspects only the top-level
    object, and what the operator saw was ``call_tool``'s unhandled-error
    banner rather than the house ``{error, hint}`` refusal. A raise that
    escapes as a traceback is a raise that has not been given a way to become
    a refusal, so ``.refusal`` is that way: ``@ledger_refusals`` on a tool
    entry point returns it verbatim, and it is built by ``artifact_refusal``
    so it is word-for-word what the pre-flight guard would have said about the
    same file.
    """

    def __init__(self, problem: str) -> None:
        super().__init__(problem)
        self.problem = problem
        self.refusal = artifact_refusal([problem])


def ledger_refusals(fn):
    """Return a tool entry point that answers ``LedgerShapeError`` in-band.

    The house rule is that a tool never raises across the MCP boundary: it
    returns ``{error, hint}``. A ledger whose container shape is wrong is
    discovered inside the locked primitive, several frames below the entry
    point, and D-127 is what happens when each entry point is trusted to
    remember a pre-flight check for it — the ones in this module remembered,
    the ones in ``foundry_orchestrator`` did not, and the difference was
    invisible until it was driven.

    Wrapping is the binding that cannot be forgotten per-branch: every path
    through the function, present and future, converts. Which entry points
    need it is not a judgement either —
    ``test_every_ledger_writing_door_answers_in_band`` derives the set from
    ``server.py``'s ``_DISPATCH`` table crossed with this module's call graph,
    and fails on a door that reaches a transaction without it.
    """

    @wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except LedgerShapeError as exc:
            return exc.refusal

    return wrapper


def allocate_record_id(records: list, prefix: str = "D") -> str:
    """Return the next unused ``{prefix}-NNN`` id for ``records``.

    Highest-existing-suffix + 1, NOT ``len(records) + 1``: the positional form
    re-issued a live id whenever a record was removed or a prior collision left
    a duplicate, and it silently wrapped past its informal ``D-999`` ceiling.
    Width is a MINIMUM, so the sequence continues ``D-999``, ``D-1000``.

    Pure and total — a malformed or missing id contributes nothing rather than
    raising. Call it inside ``ledger_transaction`` (or any other exclusive
    section): uniqueness comes from the surrounding lock, not from this
    function.
    """
    highest = 0
    for record in records:
        if not isinstance(record, dict):
            continue
        rid = record.get("id")
        if not isinstance(rid, str):
            continue
        m = _RECORD_ID_RE.match(rid.strip())
        if m and m.group(1) == prefix:
            highest = max(highest, int(m.group(2)))
    return f"{prefix}-{highest + 1:03d}"


@contextmanager
def _locked_document(path: Path) -> Iterator[dict]:
    """Exclusive read-modify-write over one run-artifact JSON document.

    THE write path. Yields the whole parsed document; mutate it in place and it
    is written back through ``_atomic_rename_write``'s tmp+rename on clean
    exit. An exception inside the block propagates and NOTHING is written, so a
    failed classification cannot leave a half-updated artifact behind.

    Held locks: the module ``threading.RLock`` (threads inside one server
    process — INSPECT's parallel streams are concurrent tool calls in ONE
    process, and an flock alone would not order them) and an ``fcntl``
    exclusive lock on a ``{path}.lock`` sidecar (a second server process on the
    same repo). POSIX only, which matches the documented macOS/Linux runtime
    floor.

    RE-ENTRANT PER PATH (D-099). A nested entry on a path this thread already
    holds yields the same in-flight document and defers the single write to the
    outermost exit. The RLock alone did NOT make nesting safe: an ``fcntl``
    lock belongs to the open file description, and this function opens a fresh
    fd on every entry, so a same-thread re-acquire blocked forever on a lock the
    thread already held. Keying the in-flight map on the PATH rather than on
    anything narrower is deliberate — ``observations.json`` carries records
    under two keys, and a nested pair on those two keys must see one document
    and produce one write, not two racing ones.

    A document that cannot be parsed raises ``LedgerShapeError`` carrying its
    named refusal, and writes NOTHING: reading it as ``{}`` and writing over it
    is the data loss the guard exists to prevent.
    """
    held = getattr(_LEDGER_TX, "docs", None)
    if held is None:
        held = _LEDGER_TX.docs = {}
    tx_key = str(path)
    if tx_key in held:
        # Already open on this thread — same document, one write at the end.
        yield held[tx_key]
        return

    lock_path = path.with_name(path.name + ".lock")
    with _LEDGER_LOCK:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with open(lock_path, "a+", encoding="utf-8") as lock_file:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            try:
                # Read the SHAPE before the data: a corrupt document must abort
                # here rather than read as {} and be written over. Both reads
                # happen under the flock, so nothing can change between them.
                problem = _document_problem(path)
                if problem is not None:
                    raise LedgerShapeError(problem)
                data = _load_json(path)
                held[tx_key] = data
                yield data
                _atomic_rename_write(path, data)
            finally:
                held.pop(tx_key, None)
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def write_document(path: Path, data: dict) -> None:
    """Replace ``path``'s whole document under the same lock, without reading.

    The seeding counterpart of ``_locked_document``, for the one caller whose
    intent is genuinely "replace, whatever is there": ``foundry_init``, which
    documents each artifact it writes as "always fresh" and is also the repair
    path an operator reaches for when an artifact is corrupt. Routing it
    through the reading primitive would make a corrupt ledger refuse the very
    call that would have replaced it.

    It is still a LOCKED write, because that is the whole point of D-125 —
    "unlocked because this writer doesn't read first" is how a second writer
    ends up racing a first. A seed landing between a peer's read and write
    would be discarded exactly like any other lost update.

    Re-entrant with ``_locked_document``: if this thread already has the path
    open, the in-flight document is replaced in place and the single write
    still happens at the outermost exit, rather than a second write racing the
    transaction that is about to overwrite it.
    """
    held = getattr(_LEDGER_TX, "docs", None) or {}
    tx_key = str(path)
    if tx_key in held:
        held[tx_key].clear()
        held[tx_key].update(data)
        return
    lock_path = path.with_name(path.name + ".lock")
    with _LEDGER_LOCK:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with open(lock_path, "a+", encoding="utf-8") as lock_file:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            try:
                _atomic_rename_write(path, data)
            finally:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


@contextmanager
def ledger_transaction(path: Path, collection_key: str) -> Iterator[list[dict]]:
    """Exclusive read-modify-write over a run-artifact JSON ledger.

    ``_locked_document`` projected onto one record list, with every guarantee
    that primitive gives — the RLock, the flock, one write on clean exit,
    nothing written on exception, re-entrancy per path (D-099).

    YIELDS ONLY MAPPING RECORDS (D-127). Both checks that used to be the
    caller's to remember now happen here, because the class of bug they belong
    to is "a guard bound by hand to the site where a defect was reported":

      - The container-shape check (D-096). A non-list container raises
        ``LedgerShapeError`` and writes NOTHING. It is not coerced to ``[]``,
        because that coercion is what discarded a populated ledger and reported
        success. Bound here, not at each entry point, because it WAS bound at
        each entry point and ``foundry_orchestrator``'s two writers were not
        among them.
      - The non-dict record filter (D-097). One malformed historical record
        made ``d.get("status")`` raise mid-scan — and it raised AFTER the new
        record had been appended, so the transaction aborted and a good filing
        was silently discarded. ``_dict_records`` was applied at the call sites
        that had been bitten and nowhere else;
        ``foundry_sync_defects`` still scans with a bare ``d.get(...)`` and
        ``d["id"]``. A caller cannot be bitten by a record it is never handed.

    NOTHING IS LOST TO THE FILTER (NFR-002). Non-dict records are set aside by
    INDEX on entry and re-inserted at those indices before the write, so a
    ledger carrying junk is readable, appendable, and comes back off the lock
    with its junk intact and in place. Filtering that dropped them would be a
    quieter D-096: refusing to lose records to a bad container while losing
    them to a bad record.

    This is the write discipline every ledger writer must use — including
    ``foundry_orchestrator``'s, which import it rather than re-deriving one.
    """
    with _locked_document(path) as data:
        records = _ledger_records(data, path, collection_key)
        # Ascending by construction, which is what makes the re-insert below
        # the exact inverse of the removal.
        foreign = [(i, r) for i, r in enumerate(records) if not isinstance(r, dict)]
        if foreign:
            records = data[collection_key] = _dict_records(records)
        try:
            yield records
        finally:
            # Before ``_locked_document`` writes, and not after: the restored
            # document is the one that must reach disk. A nested entry finds
            # the cleaned list already installed, so its own ``foreign`` is
            # empty and this is a no-op — the restore belongs to the outermost
            # frame that removed anything.
            for index, record in foreign:
                records.insert(min(index, len(records)), record)


def _ledger_records(data: dict, path: Path, collection_key: str) -> list:
    """The record list under ``collection_key``, created if absent.

    Refuses a container that exists and is not a list, rather than replacing
    it. See ``LedgerShapeError``.
    """
    records = data.get(collection_key)
    if records is None:
        records = data[collection_key] = []
    else:
        problem = _container_shape_problem(path, collection_key, records)
        if problem is not None:
            raise LedgerShapeError(problem)
    return records


# ---------------------------------------------------------------------------
# The demotion gate (FR-001 / FR-002 / AC-001 / AC-002).
# ---------------------------------------------------------------------------


def _subject_is_declared_comment(finding: dict) -> bool:
    """True only when the caller DECLARED the finding's subject is a comment.

    ``vocab.is_non_comment`` answers a different question: it matches when
    ``target_kind`` is present and is something other than "comment", so an
    ABSENT ``target_kind`` does not match it. Absence must not license a
    demotion in either direction, which is why the positive check is explicit
    here rather than inferred from the denylist predicate.
    """
    value = finding.get("target_kind")
    return isinstance(value, str) and value.strip().lower() == "comment"


def _finding_mapping(
    description: str,
    spec_ref: str = "",
    target_kind: str = "",
    *,
    symbol: str = "",
    file_path: str = "",
) -> dict:
    """Build the mapping vocab's predicates read (see its module docstring).

    ``symbol`` and ``file`` are inert to every predicate but carried anyway, so
    that one mapping is both what the predicates judge AND what an audit record
    quotes back. Key names match the shape ``foundry_sync_defects`` already
    passes around, so the same mapping crosses both filing paths unchanged.
    """
    return {
        "description": description,
        "spec_ref": spec_ref,
        "target_kind": target_kind,
        "symbol": symbol,
        "file": file_path,
    }


# The promote-direction fail-safe (D-093). See the module docstring.
#
# Grouped by the kind of assertion each term makes about the code, not by any
# taxonomy of defects, because the point is coverage of how engineers actually
# write "the code does / does not do X" — not of a category list.
#
# Two rules keep the list from degenerating into matching everything, and both
# were found by driving the shipped drift phrasings through it:
#
#   1. VERBS ONLY, never the noun of the same stem. "the guard moved" and "no
#      such handler" name a code element and are still pure comment prose, so
#      `guard`, `handler` and their kin are absent — the ABSENCE branch below
#      is where those nouns legitimately appear.
#   2. INFLECTIONS ARE ENUMERATED, never `\w+`. The whole alternation sits
#      inside `\b(?:...)\b`, so `write` does not fire on "that writer now sits
#      at line 244" — but only because the pattern cannot itself consume the
#      "r". `dispatch\w+`, `encrypt\w+` and `serializ\w+` each matched their
#      own agent noun before they were spelled out.
_CODE_ACTION_VERB = r"""(?:
    # control flow and dispatch
      returns? | returned | returning
    | throws? | thrown | raises? | raised
    | calls? | called | invokes? | invoked | dispatch (?:es|ed)
    | runs? | ran | running | executes? | executed | fires? | fired
    | handles? | handled

    # what it does with data
    | stores? | stored | persists? | persisted
    | writes? | wrote | written | reads?
    | parses? | parsed | serialis (?:e|es|ed) | serializ (?:e|es|ed)
    | set \s+ (?:to|at) | sets | assigns? | assigned
    | mutates? | mutated | increments? | rebuilds? | rebuilt
    | uses? | used | applies | applied

    # what it does about correctness and trust
    | checks? | checked | verif (?:y|ies|ied) | validat (?:e|es|ed)
    | enforc (?:e|es|ed)
    | rejects? | rejected | accepts? | accepted | allows? | allowed
    | sanitis (?:e|es|ed) | sanitiz (?:e|es|ed) | escap (?:e|es|ed)
    | hash (?:es|ed) | encrypt (?:s|ed) | decrypt (?:s|ed)
    | signs? | signed | salt (?:s|ed)
    | limits? | limited | limiting | throttl (?:e|es|ed|ing)
    | implement (?:s|ed)?
)"""

# The other half: a claimed behaviour is stated to be ABSENT. This is what
# carries the phrasings that name no verb of their own — "but it is not",
# "there is no bounds check" — and it is where most real security claims live.
#
# The lookahead on the negated-copula branch is the one deliberate NARROWING:
# "X does not match / agree with / reflect Y" is a complaint that a comment
# disagrees with the code, which is the definition of a comment-prose
# observation (vocab's own `_MISMATCH` cue lists exactly those words), not an
# assertion that the code misbehaves.
_ABSENT_BEHAVIOUR = r"""(?:
      \b never \b
    | \b fails? \s+ to \b
    | \b (?: is|are|was|were|does|do|did|has|have|can|will|would|could )
        \s* n (?:o|') ? t \b
        (?! \s* (?: match\w* | agree\w* | reflect\w* | correspond\w*
                  | line \s+ up ) )
    | \b no \s+ (?: \w+ \s+ ){0,2}
        (?: check\w* | validation | guard\w* | limit\w* | handling | test\w*
          | sanitis\w* | sanitiz\w* | escaping | encryption | hashing
          | auth\w* | enforcement | implementation )
    | \b there \s+ is \s+ no \b
)"""

# The verb group is anchored on BOTH sides; the absence group carries its own
# anchors (and a lookahead that a trailing `\b` would sit awkwardly against).
_CODE_BEHAVIOUR_RE = re.compile(
    rf"\b{_CODE_ACTION_VERB}\b|{_ABSENT_BEHAVIOUR}",
    re.IGNORECASE | re.VERBOSE,
)


def asserts_code_behaviour(finding: dict) -> bool:
    """True when the finding asserts something about what the CODE does.

    The promote-direction counterpart to vocab's never-demote denylist, and
    the guard its module docstring says callers owe: "a caller that refuses a
    defect filing on an observation-class match alone turns a false positive
    here into a blocked real defect. Callers owe that direction a fail-safe of
    their own."

    A True result means the finding is NOT confined to comment prose, so no
    comment-prose refusal may fire against it however its wording reads. It is
    biased to OVER-match on purpose: a false positive files one stale-comment
    finding as a defect (ordinary friction, and the thing US-001 merely wants
    less of), while a false negative blocks a real defect (the failure mode
    vocab.py calls unacceptable).

    Exported because ``foundry_sync_defects``'s auto-demotion branch faces the
    mirror of the same question and must not re-derive an answer to it.

    Pure and total over a malformed mapping — a missing or non-``str``
    description is simply no match, matching vocab's never-raise contract.
    """
    description = finding.get("description")
    if not isinstance(description, str):
        return False
    return bool(_CODE_BEHAVIOUR_RE.search(description))


def _observation_refusal(finding: dict) -> str | None:
    """Name the observation class a defect filing must be refused for, else None.

    Applies vocab's precedence rule — ``never_demote_class`` OUTRANKS
    ``observation_class``, so a finding matching both stays a DEFECT — on top
    of the explicit-declaration rule above, and on top of the promote-direction
    fail-safe: a finding that asserts what the code does is never refused, no
    matter which observation regex its prose happened to trip.

    The three guards are independent, and a defect can only be blocked if ALL
    of them are wrong at once. That is the whole repair D-093 asked for; before
    it, one loose regex was sufficient on its own.
    """
    if not _subject_is_declared_comment(finding):
        return None
    if never_demote_class(finding) is not None:
        return None
    if asserts_code_behaviour(finding):
        return None
    return observation_class(finding)


def _ledger_mirror(fdir: Path, heading: str, fields: list[tuple[str, str]]) -> None:
    """Mirror a ledger write into forge-log.md.

    Machine-readable JSON *and* a human-readable markdown mirror, guarded by
    ``exists()`` so a missing log never fails the write.
    """
    forge_log = fdir / "forge-log.md"
    if not forge_log.exists():
        return
    with open(forge_log, "a", encoding="utf-8") as f:
        f.write(f"\n### {heading}\n")
        for label, value in fields:
            if value:
                f.write(f"- **{label}:** {value}\n")
        f.write("\n")


def record_denylist_tripwire(
    fdir: Path,
    finding: dict,
    *,
    cycle: int,
    source: str,
) -> dict | None:
    """Fire and persist the never-demote audit tripwire, iff it applies.

    Returns the tripwire record when ``finding`` may NEVER be recorded as an
    observation — a denylist entry matched, or its subject was not declared to
    be a comment — and ``None`` when demotion is legitimate. A non-None result
    means the caller must keep the finding a DEFECT; the audit signal has
    already been written by the time it returns.

    WHY THIS IS EXPORTED (FR-002 / AC-002)
    --------------------------------------
    The tripwire used to live inside ``foundry_add_observation``'s body, which
    made it reachable only by a caller that actually attempted the write. Every
    production caller pre-filtered instead: ``foundry_sync_defects`` calls the
    observation writer only once ``never_demote_class`` has already returned
    None, so the writer's inner denylist branch could not fire by construction
    and ``observations.json.tripwire`` stayed empty across every denylist
    scenario. An audit signal that only fires for callers who did not need
    auditing is not a control.

    So the decision and the signal are one exported call, and any path that is
    about to route a finding away from the defect ledger makes it FIRST —
    ``foundry_add_observation`` below, and ``foundry_sync_defects``'s
    auto-demotion branch in ``foundry_orchestrator.py``. Both attempts are
    audited by the same code, which is also what keeps the two filing paths
    from drifting apart about what a demotion is.

    Takes ``fdir`` rather than ``project_root`` because both callers already
    hold the resolved run dir; re-resolving it here would be a third derivation
    of a path the caller has.
    """
    denied = never_demote_class(finding)
    if denied is None and not _subject_is_declared_comment(finding):
        # An undeclared subject cannot be SHOWN to be a comment, and "anything
        # non-comment" can never be an observation. Reported under the existing
        # NON_COMMENT entry rather than inventing a class name.
        denied = "NON_COMMENT"
    if denied is None:
        return None

    target_kind = finding.get("target_kind")
    if denied != "NON_COMMENT":
        detail = f"the finding matches the {denied} denylist entry"
    elif not (isinstance(target_kind, str) and target_kind.strip()):
        # Absence is the common case and its own diagnosis: `target_kind` is
        # optional in the advertised schema, so omitting it is what a caller
        # does by DEFAULT. Name the missing field, not its empty value.
        detail = (
            "no target_kind was declared; demotion out of the defect ledger "
            'requires an explicit target_kind="comment"'
        )
    else:
        detail = f"target_kind={target_kind!r} is not a declared comment"
    tripwire = {
        "cycle": cycle,
        "source": source,
        "denylist_class": denied,
        "detail": detail,
        "description": finding.get("description", ""),
        "spec_ref": finding.get("spec_ref", ""),
        "symbol": finding.get("symbol", ""),
        "file": finding.get("file", ""),
        "fired_at": datetime.now(timezone.utc).isoformat(),
    }

    with ledger_transaction(fdir / "observations.json", "tripwire") as fired:
        fired.append(tripwire)
    _ledger_mirror(
        fdir,
        f"TRIPWIRE cycle {cycle} — {source}: {denied}",
        [
            ("Denylist class", denied),
            ("Detail", detail),
            ("Description", tripwire["description"]),
            ("Spec ref", tripwire["spec_ref"]),
            ("Symbol", tripwire["symbol"]),
            ("File", tripwire["file"]),
        ],
    )
    return tripwire


def _server_cycle(fdir: Path) -> int:
    """Return the server-owned cycle counter. Never caller-supplied.

    ``state.json['cycle']`` is maintained by the F3 GRIND -> F2 INSPECT
    boundary handler (``foundry_orchestrator.foundry_mark_phase_complete``,
    the ``inspect_start`` token). Per ST-001 it is the authority, and a
    caller-supplied ``cycle`` is not trusted against it: every cycle number in
    grand-vulture's data model was an integer the lead asserted, which is why
    its defects span cycles 0-17 while its state.json reads 0. Every writer of
    a cycle-stamped record in this module goes through this one reader, so
    "which cycle was this?" has one answer per run instead of one per caller.

    Missing, absent, or malformed resolves to 0 rather than to the caller's
    value (D-119). This function used to return None there and its callers took
    that as licence to stamp the number they were handed, while the counterpart
    reader ``foundry_orchestrator._current_cycle`` resolved the identical input
    to 0 — so the SAME finding filed through Foundry-Defect and through
    Foundry-Sync landed in different cycles, and a class that recurred three
    straight cycles evaded ST-002 escalation because mixed-door filing broke
    the consecutive run. Trusting the caller in the degraded case is exactly
    what ST-001 exists to remove, so the degraded case resolves to the same
    deterministic 0 both doors already agreed on for a corrupt CONTAINER. What
    the caller claimed is not discarded: both doors persist it beside the stamp
    as ``declared_cycle``.

    Read directly rather than through the orchestrator's private
    ``_current_cycle``: the orchestrator imports THIS module, so importing back
    would close a cycle in the import graph. The two are a deliberate second
    copy, held to one contract by ``test_escalation``'s cross-door parity pins.
    """
    value = _load_json(fdir / "state.json").get("cycle", 0)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return 0
    return value


_ADJECTIVES = [
    "ambitious", "blazing", "bold", "brave", "calm", "clever", "cosmic",
    "daring", "deft", "eager", "fierce", "flying", "golden", "grand",
    "heroic", "humble", "iron", "jolly", "keen", "lively", "lucky",
    "mighty", "noble", "plucky", "quick", "rapid", "roaring", "sharp",
    "silver", "sleek", "soaring", "steady", "steel", "stout", "swift",
    "thunder", "titan", "valiant", "vivid", "wild", "witty", "zesty",
]

_NOUNS = [
    "anvil", "arrow", "badger", "beacon", "bison", "bolt", "canyon",
    "cedar", "comet", "condor", "crane", "falcon", "forge", "fox",
    "glacier", "granite", "hawk", "helm", "heron", "jaguar", "kestrel",
    "lance", "leopard", "lynx", "mammoth", "mantis", "maple", "marble",
    "mustang", "oak", "orca", "osprey", "otter", "panther", "phoenix",
    "pike", "puma", "quartz", "raven", "ridge", "salmon", "sequoia",
    "squid", "stallion", "summit", "talon", "tiger", "trout", "viper",
    "vulture", "walrus", "wolf", "wren",
]


# The default F0 ruling every run starts with (FR-019 / AC-004). Body text
# only — `foundry_init` wraps it in the `### [DIRECTIVE] {iso}` header.
_F0_OBSERVATION_DEFECT_RULING = (
    "OBSERVATION/DEFECT SPLIT (F0 default ruling). Comment-prose findings — a "
    "drifted line number in a cite, a count stated in prose, a direction word "
    '("above", "below", "the following"), an enumeration that no longer '
    "matches what it enumerates — are OBSERVATIONS, not defects. Record them "
    "in the run's observations.json ledger; Foundry-Defect and Foundry-Sync "
    "refuse them as defects server-side. The never-demote denylist is "
    "absolute: a security-property claim, a spec-required-behaviour claim, an "
    "unresolvable cite, and anything that is not a comment can NEVER be "
    "recorded as an observation — each stays a defect whatever else is true "
    "about it, and an attempt to demote one is rejected and fires the audit "
    "tripwire. This is a channel, not a severity tier: it buys no discretion "
    "over any behavioural or security finding. The symbol is authoritative in "
    "a cite — no verifier judges the line component, a moved line alone "
    "produces no finding of any kind, and cite-refresh sweeps require an "
    "explicit directive."
)


def _generate_run_name(ticket: str = "", description: str = "") -> str:
    """Generate a human-friendly run name.

    Uses ticket/description when available, falls back to random adjective-noun.
    Examples: 'AQUA-123-login-flow', 'fix-broken-nav', 'bold-falcon'
    """
    parts: list[str] = []
    if ticket:
        parts.append(ticket)
    if description:
        slug = description.lower().replace(" ", "-")[:40]
        slug = "".join(c for c in slug if c.isalnum() or c == "-").strip("-")
        if slug:
            parts.append(slug)
    if parts:
        return "-".join(parts)
    # Fallback: random name when no context provided
    import random
    adj = random.choice(_ADJECTIVES)
    noun = random.choice(_NOUNS)
    return f"{adj}-{noun}"


@ledger_refusals
def foundry_init(
    spec_path: str | None = None,
    temper: bool = False,
    nyquist: bool = False,
    no_ui: bool = False,
    resume: str | None = None,
    ticket: str = "",
    description: str = "",
    url: str = "",
    project_root: str = ".",
) -> dict:
    """Initialize a foundry run under foundry-archive/.

    Args:
        resume: Name of existing run to resume (e.g. 'bold-falcon').
        ticket: Ticket ID (e.g., "AQUA-123") for name generation.
        description: Short description for name generation.
        url: Target URL for the SIGHT audit. Persisted to
            ``castings/manifest.json`` as ``target_url`` (the store of
            record the inspect gate readers load — foundry_orchestrator.py
            :531 / :1033), mirroring the bash init write at foundry.sh:176.
            NOT written into state.json.
        nyquist: Enable the optional F5.5 NYQUIST phase. Persisted to BOTH
            state.json and castings/manifest.json, mirroring ``temper`` and
            the bash init writes at foundry.sh:178 / :196. state.json is the
            store of record: ``_compute_next_action`` reads
            ``state["nyquist"]`` to decide whether F4/F5 transition into F5.5
            or straight to F6. Without this parameter the key was never
            written, so that read was permanently False and F5.5 was
            unreachable on the MCP path.

    Returns:
        {foundry_dir, run_name, files_created[], spec_copied}
    """
    root = Path(project_root)
    archive = root / ARCHIVE_DIR

    # --- Resume mode ---
    if resume:
        run_dir = archive / resume
        state_path = run_dir / "state.json"
        if not state_path.exists():
            return {"error": f"Run '{resume}' not found in {ARCHIVE_DIR}/"}
        # D-095: a corrupt state.json used to raise here, and resuming is
        # exactly when an operator is trying to recover from whatever corrupted
        # it. Naming the file is the whole value of the call at that moment.
        if (corrupt := _artifact_guard(run_dir, "state.json")):
            return corrupt
        set_active_run(resume)
        state = _load_json(state_path)
        return {
            "foundry_dir": str(run_dir),
            "run_name": resume,
            "resumed": True,
            "state": state,
            "display": (
                foundry_hammer(f"F O U N D R Y  Resumed: {resume}")
                + f"\n  Phase: {state.get('phase', '?')}  Cycle: {state.get('cycle', 0)}"
                + f"\n{FOUNDRY_SEP}"
                + f"\nCall Foundry-Next for instructions."
            ),
            "next_step": "Call Foundry-Next to see status and get instructions.",
        }

    # --- New run ---
    run_name = _generate_run_name(ticket=ticket, description=description)

    # Ensure unique name (don't collide with existing runs)
    archive.mkdir(parents=True, exist_ok=True)
    if (archive / run_name).exists():
        import random
        suffix = random.randint(100, 999)
        run_name = f"{run_name}-{suffix}"

    fdir = archive / run_name

    # Silently delete legacy .foundry-dir if it exists (one-time migration)
    legacy_pointer = root / ".foundry-dir"
    if legacy_pointer.exists():
        legacy_pointer.unlink(missing_ok=True)

    dirs = [
        fdir,
        fdir / "castings",
        fdir / "traces",
        fdir / "proofs",
        fdir / "proofs" / "screenshots",
    ]
    if temper:
        dirs.extend([fdir / "temper", fdir / "temper" / "probe-results"])

    for d in dirs:
        d.mkdir(parents=True, exist_ok=True)

    files_created = []

    # defects.json — always fresh
    defects_path = fdir / "defects.json"
    write_document(defects_path, {"defects": []})
    files_created.append("defects.json")

    # observations.json — always fresh. The typed non-blocking channel of
    # FR-001/FR-023, seeded alongside defects.json so a stream never has to
    # decide whether the ledger exists. `tripwire` is the durable audit signal
    # of FR-002: every rejected demotion attempt is appended there (and
    # mirrored into forge-log.md) where the lead and a validator can read it.
    observations_path = fdir / "observations.json"
    write_document(observations_path, {"observations": [], "tripwire": []})
    files_created.append("observations.json")

    # verdicts.json — always fresh
    verdicts_path = fdir / "verdicts.json"
    write_document(verdicts_path, {"cycle": 0, "requirements": []})
    files_created.append("verdicts.json")

    # state.json — always fresh
    state_path = fdir / "state.json"
    _init_now = datetime.now(timezone.utc).isoformat()
    state = {
        "phase": "F0",
        "cycle": 0,
        "spec_path": spec_path or "",
        "temper": temper,
        "nyquist": nyquist,
        "no_ui": no_ui,
        "started_at": _init_now,
        "phase_times": {
            # Stamp F0 start so sub-phase timing (F0 / F0.5 / F0.9) works
            # automatically; passive stamping in foundry_next_action closes
            # F0 when manifest.json appears, closes F0.5 on .validate-passed,
            # and closes F0.9 on Foundry-Phase(start_cast).
            "F0": {"started_at": _init_now},
        },
    }
    write_document(state_path, state)
    files_created.append("state.json")

    # castings/manifest.json — the store of record for target_url.
    # The inspect gate readers (_check_streams_complete / _check_sight_required
    # in foundry_orchestrator.py:531 / :1033) load target_url from HERE, not
    # from state.json. Mirrors the bash init write shape at foundry.sh:168-182
    # so the Python and bash init paths produce byte-compatible manifests.
    # `castings: []` at init keeps the F0 guidance engine emitting DECOMPOSE
    # (it keys on len(castings), not manifest existence); decompose fills in
    # the castings/waves later. A run without a url persists target_url="",
    # which keeps the inspect gate blocked when frontend files are present.
    manifest_path = fdir / "castings" / "manifest.json"
    manifest = {
        "created_at": _init_now,
        "updated_at": _init_now,
        "spec_path": spec_path or "",
        "temper": temper,
        "nyquist": nyquist,
        "no_ui": no_ui,
        "target_url": url,
        "max_cycles": 0,
        "current_cycle": 0,
        "status": "initialized",
        "castings": [],
        "waves": [],
    }
    write_document(manifest_path, manifest)
    files_created.append("castings/manifest.json")

    # forge-log.md — always fresh
    forge_log = fdir / "forge-log.md"
    forge_log.write_text(
        "# Forge Log\n\nCumulative record of all defects, fixes, and verdicts.\n\n---\n\n",
        encoding="utf-8",
    )
    files_created.append("forge-log.md")

    # directives.md — seeded with the F0 observation/defect ruling (FR-019 /
    # AC-004). Written in the exact grammar `_read_directives` parses (a
    # `### [DIRECTIVE] {iso}` header, a blank line, then the body), matching
    # `foundry_inject_directive` byte for byte — a body in any other shape
    # parses as no directive at all. Seeded at NORMAL priority, which is why
    # the Foundry-Next renderer must show normal directives alongside urgent
    # ones: an urgent injection must not silence this standing ruling.
    directives_path = fdir / "directives.md"
    directives_path.write_text(
        "# Foundry Directives\n\n"
        "Human steering inputs — read at every phase transition.\n\n"
        f"\n### [DIRECTIVE] {_init_now}\n\n"
        f"{_F0_OBSERVATION_DEFECT_RULING}\n",
        encoding="utf-8",
    )
    files_created.append("directives.md")

    # Copy spec
    spec_copied = False
    if spec_path:
        src = root / spec_path if not Path(spec_path).is_absolute() else Path(spec_path)
        dest = fdir / "spec.md"
        if src.exists() and not dest.exists():
            dest.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
            spec_copied = True
            files_created.append("spec.md")

    # Set this as the active run for this session
    set_active_run(run_name)

    return {
        "foundry_dir": str(fdir),
        "run_name": run_name,
        "files_created": files_created,
        "spec_copied": spec_copied,
        "display": _format_init_display(run_name, state.get("temper", False), state.get("nyquist", False)),
        "next_step": "Call Foundry-Next to get decomposition instructions. Print the display above FIRST.",
    }


@ledger_refusals
def foundry_add_defect(
    cycle: int,
    source: str,
    defect_type: str,
    description: str,
    spec_ref: str = "",
    symbol: str = "",
    file_path: str = "",
    target_kind: str = "",
    defect_class: str = "",
    project_root: str = ".",
) -> dict:
    """Add a defect to the foundry ledger.

    Args:
        source: a member of ``vocab.DEFECT_SOURCE_IDS``. Stored VERBATIM \u2014
            an unknown value is refused by name, never coerced onto "trace"
            (CT-002 / AC-019).
        defect_type: a member of ``vocab.DEFECT_TYPES``, which includes
            PARTIAL and both MISPLACED and ARCHITECTURAL_PLACEMENT. BOTH
            spellings of the alias pair are accepted on input and persisted
            under the canonical one (``vocab.canonical_defect_type``), so this
            path and ``foundry_sync_defects`` store the same type for the same
            finding. An UNKNOWN value is still refused by name rather than
            coerced onto a known one (CT-002).
        cycle: the caller's assertion. NEVER what is persisted as the record's
            ``cycle`` — ``state.json``'s counter is, resolving to 0 when it is
            absent or unusable (see ``_server_cycle``). Kept on the record as
            ``declared_cycle`` so the claim is auditable.
        target_kind: what the finding is ABOUT. Pass "comment" when the
            subject is a code comment \u2014 that declaration is what allows the
            comment-prose refusal of AC-001 to engage. Any other value, or
            none, means the finding is not demotable and is filed as a defect.
        defect_class: optional root-cause class shared by several instances,
            persisted under the key ``"class"``. Escalation keys on it.

    Returns:
        ``{defect_id, total_defects, open_defects}``, or a named refusal
        ``{error, hint, ...}`` when the vocabulary check or the comment-prose
        check rejects the filing.
    """
    fdir = get_run_dir(project_root)
    if not fdir:
        return {"error": "No active foundry run. Call Foundry-Init."}

    # D-095 / D-096: refuse BEFORE the transaction when the ledger this call
    # would write, or the counter it would stamp from, cannot be read. Without
    # this the corrupt file read as an empty one and the write replaced it —
    # the caller got `{"defect_id": "D-001", "total_defects": 1}` over the top
    # of a ledger that had held everything the run had found so far.
    if (corrupt := _artifact_guard(fdir, "defects.json", "state.json")):
        return corrupt

    # Server-side vocabulary validation (CT-002). Only the client schema
    # guarded these before, and `foundry_sync_defects` silently rewrote an
    # unknown source to "trace" \u2014 so a finding could be attributed to a stream
    # that never filed it. Rejection replaces coercion on both surfaces.
    if source not in DEFECT_SOURCE_IDS:
        return {
            "error": (
                f"Invalid source: {source!r}. Must be one of: "
                f"{', '.join(sorted(DEFECT_SOURCE_IDS))}"
            ),
            "hint": (
                "Source is stored verbatim and is never coerced onto another "
                "stream. File under the id of the stream that actually found "
                "this, or extend schemas/vocab.py via a phase-level RFC."
            ),
        }
    if defect_type not in DEFECT_TYPES:
        return {
            "error": (
                f"Invalid defect_type: {defect_type!r}. Must be one of: "
                f"{', '.join(sorted(DEFECT_TYPES))}"
            ),
            "hint": (
                "Use the closest member of the canonical set, or extend "
                "schemas/vocab.py via a phase-level RFC."
            ),
        }

    # Comment-prose refusal (AC-001). Engages only when the caller declared
    # the subject is a comment and no denylist entry outranks the class \u2014 see
    # the module docstring for why an undeclared subject is never refused.
    finding = _finding_mapping(
        description, spec_ref, target_kind, symbol=symbol, file_path=file_path
    )
    refused_class = _observation_refusal(finding)
    if refused_class is not None:
        return {
            "error": (
                f"Refused: {refused_class} is a comment-prose observation "
                f"class, not a defect. The comment-prose classes are: "
                f"{', '.join(sorted(OBSERVATION_CLASSES))}."
            ),
            "hint": (
                "Re-file the SAME fields through Foundry-Observation (handler "
                "foundry_add_observation): nothing about the finding changes "
                "but the ledger it lands in, and comment prose does not block "
                "the run. Do NOT re-word the description to get past this "
                "refusal. If the finding actually asserts a security property, "
                "a spec-required behaviour, or an unresolvable cite, then it "
                "is a defect and belongs here \u2014 cite the requirement in "
                "spec_ref, or drop target_kind if the subject is not a "
                "comment, and re-file."
            ),
            "refused_class": refused_class,
            "observation_classes": sorted(OBSERVATION_CLASSES),
        }

    # Canonical spelling, not the caller's (FR-013 / D-018). MISPLACED and
    # ARCHITECTURAL_PLACEMENT are ONE type under two live spellings \u2014 agent
    # contracts use both \u2014 and `foundry_sync_defects` already folds them, so
    # persisting the raw value here meant the same defect was two different
    # types depending on which door it came through. Folding a KNOWN alias onto
    # its canonical form is normalisation; CT-002's no-coercion rule is about
    # UNKNOWN values, which the membership check above has already refused.
    canonical_type = canonical_defect_type(defect_type) or defect_type

    now = datetime.now(timezone.utc).isoformat()
    defect = {
        "id": "",  # assigned inside the transaction
        # ST-001: the server's counter is the authority, full stop.
        "cycle": _server_cycle(fdir),
        # D-119: the caller's asserted cycle is persisted beside the server's,
        # never instead of it — the same field, in the same position, that
        # `foundry_sync_defects` writes on the batch door. The two doors used
        # to disagree about WHICH cycle a record belonged to whenever the
        # counter was malformed (this one fell back to the caller's value, the
        # other resolved to 0), so identical findings filed through different
        # doors produced different cycle runs and a systemic class escaped
        # ST-002 escalation while the AC-011 DONE guard passed. Both doors now
        # resolve to 0 and both record the claim, so a divergence is visible to
        # migrate/escalation tooling instead of silent.
        "declared_cycle": cycle,
        "source": source,
        "type": canonical_type,
        "description": description,
        "spec_ref": spec_ref,
        "symbol": symbol,
        "file": file_path,
        "status": "open",
        "fixed_in_cycle": None,
        "created_at": now,
    }
    if defect_class:
        defect["class"] = defect_class
    if target_kind:
        defect["target_kind"] = target_kind

    defects_path = fdir / "defects.json"
    with ledger_transaction(defects_path, "defects") as defects:
        defect_id = allocate_record_id(defects, "D")
        defect["id"] = defect_id
        defects.append(defect)
        total = len(defects)
        # D-097: skip a non-dict historical record here exactly as
        # `allocate_record_id` does four lines above. The raw `d.get(...)` used
        # to raise on one, and it raised AFTER the append — so the transaction
        # aborted and this filing was silently discarded, which is the one
        # outcome the lock exists to prevent.
        open_count = sum(
            1 for d in _dict_records(defects) if d.get("status") == "open"
        )

    _ledger_mirror(
        fdir,
        f"Cycle {defect['cycle']} \u2014 {source}: {defect_id}",
        [
            ("Type", canonical_type),
            ("Class", defect_class),
            ("Description", description),
            ("Spec ref", spec_ref),
            ("Symbol", symbol),
            ("File", file_path),
        ],
    )

    return {
        "defect_id": defect_id,
        "cycle": defect["cycle"],
        # Echoed for the same reason the batch door echoes it: the caller can
        # see, in the response, that the number it asserted was not the number
        # its record was stamped with.
        "declared_cycle": cycle,
        "type": canonical_type,
        "total_defects": total,
        "open_defects": open_count,
    }


@ledger_refusals
def foundry_add_observation(
    cycle: int,
    source: str,
    description: str,
    classification: str = "",
    # NO DEFAULT VALUE (D-069). A signature default of "comment" here made the
    # writer FABRICATE the very declaration the denylist checks, so a caller
    # that simply omitted the argument — the default behaviour of every caller,
    # since the field is optional in the advertised schema and named in no
    # agent prose — got a genuine code-behaviour finding demoted out of the
    # defect ledger with the tripwire silent. Absence must reach the
    # NON_COMMENT branch, not be papered over before it.
    target_kind: str = "",
    spec_ref: str = "",
    symbol: str = "",
    file_path: str = "",
    project_root: str = ".",
) -> dict:
    """Record a comment-prose finding in the run's observations ledger.

    The non-blocking half of the FR-001 split. Observations are typed,
    persisted per run in ``observations.json``, and NEVER mixed into
    ``defects.json``.

    The never-demote denylist is enforced here and is absolute (FR-002 /
    AC-002): a security-property claim, a spec-required-behaviour claim, an
    unresolvable cite, and anything that is not a declared comment are
    rejected, and the audit tripwire fires \u2014 durably, into the ledger's
    ``tripwire`` array and forge-log.md \u2014 naming which entry matched. A
    ``spec_ref`` is itself a spec-required-behaviour claim, so citing a
    requirement is by construction enough to keep a finding a defect.

    RECORDING AN OBSERVATION *IS* THE DEMOTION, so this path fails CLOSED on an
    undeclared subject: the default is "not demotable unless declared", never
    "demotable unless declared". That is the opposite of ``foundry_add_defect``
    on purpose \u2014 there, absence must not license a refusal, because refusing a
    defect is also a demotion. Both surfaces read the same predicate and both
    fail in the direction that keeps a finding blocking.

    Args:
        classification: optional; a member of ``vocab.OBSERVATION_CLASSES``.
            Derived from the description when omitted.
        target_kind: REQUIRED in effect \u2014 must be the explicit string
            "comment". Omitting it is refused under the NON_COMMENT denylist
            entry and fires the tripwire, exactly as a present non-comment
            value does.

    Returns:
        ``{observation_id, classification, total_observations}``, or a named
        refusal ``{error, hint, tripwire}``.
    """
    fdir = get_run_dir(project_root)
    if not fdir:
        return {"error": "No active foundry run. Call Foundry-Init."}

    # D-095 / D-096. Both containers, because this path writes `tripwire`
    # (through `record_denylist_tripwire`) before it ever reaches
    # `observations`, and a refusal after the audit signal has fired would
    # leave the two halves of one decision in different states.
    if (corrupt := _artifact_guard(fdir, "observations.json", "state.json")):
        return corrupt

    if source not in DEFECT_SOURCE_IDS:
        return {
            "error": (
                f"Invalid source: {source!r}. Must be one of: "
                f"{', '.join(sorted(DEFECT_SOURCE_IDS))}"
            ),
            "hint": (
                "Observations carry the same source vocabulary as defects, "
                "and are attributed verbatim to the stream that filed them."
            ),
        }

    finding = _finding_mapping(
        description, spec_ref, target_kind, symbol=symbol, file_path=file_path
    )

    # Denylist first \u2014 vocab's precedence rule. A finding matching both a
    # denylist entry and an observation class is a DEFECT, and the denylist
    # match is what the tripwire names. The decision and the audit signal are
    # one exported call so that every path into this ledger is audited by the
    # same code \u2014 see `record_denylist_tripwire`.
    tripwire = record_denylist_tripwire(
        fdir, finding, cycle=_server_cycle(fdir), source=source
    )
    if tripwire is not None:
        denied = tripwire["denylist_class"]
        undeclared = not target_kind.strip()
        return {
            "error": (
                f"Refused: {denied} can NEVER be recorded as an observation \u2014 "
                f"{tripwire['detail']}. The never-demote denylist is "
                f"{', '.join(sorted(NEVER_DEMOTE_CLASSES))}."
            ),
            "hint": (
                # Name the missing field and the action, not just the refusal:
                # an omitted target_kind is a caller bug with two legitimate
                # repairs, and the wrong one to guess is "re-word it".
                'Pass target_kind="comment" if the finding really is about '
                "comment prose; if it is about code \u2014 a function, a handler, a "
                "wiring path \u2014 it is a defect and belongs in Foundry-Defect. "
                "The audit tripwire has fired and is recorded in "
                "observations.json and forge-log.md."
                if undeclared
                else (
                    "File it as a defect via Foundry-Defect. The audit "
                    "tripwire has fired and is recorded in observations.json "
                    "and forge-log.md."
                )
            ),
            "denylist_class": denied,
            "missing_field": "target_kind" if undeclared else None,
            "tripwire": tripwire,
        }

    resolved = classification or observation_class(finding)
    if resolved is None:
        return {
            "error": (
                f"No comment-prose observation class matches this finding. "
                f"Must be one of: {', '.join(sorted(OBSERVATION_CLASSES))}"
            ),
            "hint": (
                "Only comment prose is an observation. If this finding is "
                "about behaviour, wiring, or a security property, file it as "
                "a defect via Foundry-Defect."
            ),
        }
    if resolved not in OBSERVATION_CLASSES:
        return {
            "error": (
                f"Invalid classification: {resolved!r}. Must be one of: "
                f"{', '.join(sorted(OBSERVATION_CLASSES))}"
            ),
            "hint": (
                "Use a canonical class name, or extend schemas/vocab.py via a "
                "phase-level RFC."
            ),
        }

    observation = {
        "id": "",  # assigned inside the transaction
        # Same authority as a defect's — the two ledgers must agree about which
        # cycle a finding belongs to or the per-cycle roll-up cannot join them.
        "cycle": _server_cycle(fdir),
        "source": source,
        "classification": resolved,
        "description": description,
        "spec_ref": spec_ref,
        "target_kind": target_kind,
        "symbol": symbol,
        "file": file_path,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

    observations_path = fdir / "observations.json"
    with ledger_transaction(observations_path, "observations") as observations:
        observation_id = allocate_record_id(observations, "O")
        observation["id"] = observation_id
        observations.append(observation)
        total = len(observations)

    _ledger_mirror(
        fdir,
        f"Cycle {observation['cycle']} \u2014 {source}: {observation_id} (observation)",
        [
            ("Classification", resolved),
            ("Description", description),
            ("Symbol", symbol),
            ("File", file_path),
        ],
    )

    return {
        "observation_id": observation_id,
        "cycle": observation["cycle"],
        "classification": resolved,
        "total_observations": total,
    }


def foundry_query_observations(
    cycle: int | None = None,
    source: str | None = None,
    classification: str | None = None,
    project_root: str = ".",
) -> dict:
    """Query the observations ledger, with the tripwire log alongside.

    The query half of the FR-023 ledger surface. ``tripwire`` is returned
    unconditionally so a validator checking whether any stream tried to demote
    a denylisted finding never has to know the ledger's file layout.
    """
    fdir = get_run_dir(project_root)
    if not fdir:
        return {"error": "No active foundry run. Call Foundry-Init."}
    # D-095: the QUERY path was holed identically to the write path, so a
    # corrupt ledger could not even be looked at to diagnose which file to
    # repair. Guarded rather than merely tolerated, because a query that
    # silently answers "no observations" about an unreadable ledger is worse
    # than one that names the file.
    if (corrupt := _artifact_guard(fdir, "observations.json")):
        return corrupt
    data = _load_json(fdir / "observations.json")
    all_observations = _dict_records(data.get("observations", []))
    tripwire = data.get("tripwire", [])

    observations = all_observations
    if cycle is not None:
        observations = [o for o in observations if o.get("cycle") == cycle]
    if source:
        observations = [o for o in observations if o.get("source") == source]
    if classification:
        observations = [
            o for o in observations if o.get("classification") == classification
        ]

    by_classification: dict[str, int] = {}
    by_source: dict[str, int] = {}
    for o in all_observations:
        c = o.get("classification", "unknown")
        by_classification[c] = by_classification.get(c, 0) + 1
        s = o.get("source", "unknown")
        by_source[s] = by_source.get(s, 0) + 1

    return {
        "observations": observations,
        "tripwire": tripwire,
        "summary": {
            "total": len(all_observations),
            "tripwire_fired": len(tripwire),
            "by_classification": by_classification,
            "by_source": by_source,
        },
    }


def foundry_query_defects(
    status: str | None = None,
    cycle: int | None = None,
    source: str | None = None,
    spec_ref: str | None = None,
    project_root: str = ".",
) -> dict:
    """Query defects with optional filters."""
    fdir = get_run_dir(project_root)
    if not fdir:
        return {"error": "No active foundry run. Call Foundry-Init."}
    # D-095 — same reasoning as the observations query above.
    if (corrupt := _artifact_guard(fdir, "defects.json")):
        return corrupt
    data = _load_json(fdir / "defects.json")
    defects = _dict_records(data.get("defects", []))

    if status:
        defects = [d for d in defects if d.get("status") == status]
    if cycle is not None:
        defects = [d for d in defects if d.get("cycle") == cycle]
    if source:
        defects = [d for d in defects if d.get("source") == source]
    if spec_ref:
        defects = [d for d in defects if d.get("spec_ref") == spec_ref]

    all_defects = _dict_records(data.get("defects", []))
    by_source: dict[str, int] = {}
    by_type: dict[str, int] = {}
    for d in all_defects:
        s = d.get("source", "unknown")
        by_source[s] = by_source.get(s, 0) + 1
        t = d.get("type", "unknown")
        by_type[t] = by_type.get(t, 0) + 1

    return {
        "defects": defects,
        "summary": {
            "total": len(all_defects),
            "open": sum(1 for d in all_defects if d.get("status") == "open"),
            "fixed": sum(1 for d in all_defects if d.get("status") == "fixed"),
            "by_source": by_source,
            "by_type": by_type,
        },
    }


@ledger_refusals
def foundry_add_verdict(
    requirement_id: str,
    verdict: str,
    evidence: str,
    spec_text_cited: str = "",
    code_location: str = "",
    cycle: int = 0,
    project_root: str = ".",
) -> dict:
    """Record a verdict for a requirement with spec citation and code evidence.

    Args:
        cycle: the caller's assertion, never persisted. ST-001 makes the
            server's counter the authority — a verdict stamped with a
            lead-asserted number cannot be joined against the defects filed in
            the same cycle, which is the whole point of stamping it.

    D-125: this ran as an UNLOCKED load / mutate / save while
    ``foundry_orchestrator._synthesize_clean_prove_verdicts`` wrote the SAME
    file through a locked transaction — so verdicts.json was the one shared run
    artifact whose read-modify-write window was still open, and an F4
    auto-VERIFY synthesis interleaving with a real Foundry-Verdict call
    discarded whichever row renamed first while reporting success to both. It
    now holds the ledger lock like every other writer of a run artifact.
    """
    fdir = get_run_dir(project_root)
    if not fdir:
        return {"error": "No active foundry run. Call Foundry-Init."}
    # D-095 / D-096 — verdicts.json is a ledger too, named here BEFORE the
    # transaction so a corrupt one is reported as a pre-flight refusal rather
    # than out of the primitive's backstop.
    if (corrupt := _artifact_guard(fdir, "verdicts.json", "state.json")):
        return corrupt
    verdicts_path = fdir / "verdicts.json"

    # Read outside the critical section: it opens state.json, not this ledger,
    # and the stamp does not depend on anything the transaction reads.
    stamped_cycle = _server_cycle(fdir)
    entry = {
        "id": requirement_id,
        "verdict": verdict,
        "evidence": evidence,
        "spec_text_cited": spec_text_cited,
        "code_location": code_location,
        "cycle": stamped_cycle,
        "recorded_at": datetime.now(timezone.utc).isoformat(),
    }

    # The document's own ``cycle`` counter is stamped in the SAME critical
    # section as the row it describes — a peer landing between the two would
    # leave verdicts.json claiming a cycle none of its rows was written in.
    # The nested pair is D-099's contract: one document, one write, at the
    # outermost exit.
    with _locked_document(verdicts_path) as document:
        with ledger_transaction(verdicts_path, "requirements") as requirements:
            replaced = False
            # Scanned through ``_dict_records`` rather than over the binding:
            # the transaction's dict-only yield (D-127) already makes this
            # total, but the package-wide rule is that the tolerance has ONE
            # home and every element scan goes through it, and a call site
            # exempting itself because it happens to know better is the
            # copy-per-site this class is made of.
            for req in _dict_records(requirements):
                if req.get("id") == requirement_id:
                    # Updated in place, not by index: ``req`` IS the record in
                    # the stored list, so this needs no assumption about the
                    # filtered list's indices lining up with the ledger's.
                    req.clear()
                    req.update(entry)
                    replaced = True
                    break
            if not replaced:
                requirements.append(entry)
            total = len(requirements)
            verified = sum(
                1
                for r in _dict_records(requirements)
                if r.get("verdict") == "VERIFIED"
            )
        document["cycle"] = stamped_cycle

    return {
        "requirement_id": requirement_id,
        "verdict": verdict,
        "cycle": stamped_cycle,
        "replaced_existing": replaced,
        "total_requirements": total,
        "verified_count": verified,
    }


def foundry_verify_coverage(
    spec_path: str | None = None,
    project_root: str = ".",
) -> dict:
    """Cross-reference spec -> verdicts -> defects for full traceability."""
    fdir = get_run_dir(project_root)
    if not fdir:
        return {"error": "No active foundry run. Call Foundry-Init."}
    root = Path(project_root)

    # D-095 — this tool reads both ledgers and reports coverage over them; an
    # unreadable one must be named, not silently counted as zero.
    if (corrupt := _artifact_guard(fdir, "verdicts.json", "defects.json")):
        return corrupt

    verdicts_data = _load_json(fdir / "verdicts.json")
    requirements = _dict_records(verdicts_data.get("requirements", []))
    # D-097: `r["id"]` raised on a record missing the key. A record with no id
    # cannot be joined to a requirement at all, so it is skipped rather than
    # allowed to brick the whole report.
    verdict_map = {r["id"]: r for r in requirements if r.get("id")}

    defects_data = _load_json(fdir / "defects.json")
    all_defects = _dict_records(defects_data.get("defects", []))
    open_defects = [d for d in all_defects if d.get("status") == "open"]

    defects_by_req: dict[str, list[dict]] = {}
    for d in open_defects:
        ref = d.get("spec_ref", "")
        if ref:
            defects_by_req.setdefault(ref, []).append({
                "id": d.get("id", ""),
                "type": d.get("type", ""),
                "description": d.get("description", ""),
            })

    spec_req_ids: list[str] = []
    if spec_path:
        spath = root / spec_path if not Path(spec_path).is_absolute() else Path(spec_path)
        if spath.exists():
            import re
            spec_text = spath.read_text(encoding="utf-8")
            spec_req_ids = list(dict.fromkeys(re.findall(r"\b(US-\d+|FR-\d+|NFR-\d+)\b", spec_text)))
    elif (fdir / "spec.md").exists():
        import re
        spec_text = (fdir / "spec.md").read_text(encoding="utf-8")
        spec_req_ids = list(dict.fromkeys(re.findall(r"\b(US-\d+|FR-\d+|NFR-\d+)\b", spec_text)))

    if not spec_req_ids:
        spec_req_ids = [r["id"] for r in requirements if r.get("id")]

    traceability = []
    gaps = []
    for req_id in spec_req_ids:
        v = verdict_map.get(req_id)
        entry = {
            "requirement_id": req_id,
            "verdict": v["verdict"] if v else None,
            "evidence": v.get("evidence", "") if v else "",
            "spec_text_cited": v.get("spec_text_cited", "") if v else "",
            "code_location": v.get("code_location", "") if v else "",
            "open_defects": defects_by_req.get(req_id, []),
            "status": "verified" if v and v["verdict"] == "VERIFIED" else (
                "non_verified" if v else "uncovered"
            ),
        }
        traceability.append(entry)
        if entry["status"] != "verified":
            gaps.append({
                "requirement_id": req_id,
                "status": entry["status"],
                "verdict": entry["verdict"],
                "open_defect_count": len(entry["open_defects"]),
            })

    verified = sum(1 for t in traceability if t["status"] == "verified")
    non_verified = sum(1 for t in traceability if t["status"] == "non_verified")
    uncovered = sum(1 for t in traceability if t["status"] == "uncovered")
    total = len(traceability)

    all_verified = verified == total and total > 0
    zero_open = len(open_defects) == 0

    return {
        "traceability": traceability,
        "coverage_summary": {
            "total_requirements": total,
            "verified": verified,
            "non_verified": non_verified,
            "uncovered": uncovered,
            "coverage_pct": f"{verified / total * 100:.0f}%" if total > 0 else "N/A",
        },
        "defect_summary": {
            "total": len(all_defects),
            "open": len(open_defects),
            "fixed": sum(1 for d in all_defects if d.get("status") == "fixed"),
        },
        "gaps": gaps,
        "pass": all_verified and zero_open,
    }
