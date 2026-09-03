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
import subprocess
import threading
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path
from typing import Any

from foundry_mcp.schemas.vocab import (
    DEFECT_SOURCE_IDS,
    DEFECT_TIERS,
    DEFECT_TYPES,
    NEVER_DEMOTE_CLASSES,
    OBSERVATION_CLASSES,
    SECURITY_PROPERTY_CLAIM,
    TIER_UNKNOWN,
    canonical_defect_type,
    defect_tier,
    is_security_property_text,
    never_demote_class,
    observation_class,
    reproduction_attempted_problem,
)
from foundry_mcp.schemas.vocab import REQUIREMENT_ID_RE
from foundry_mcp.tools.foundry_state import (
    ARCHIVE_DIR,
    document_refusal,
    get_run_dir,
    read_document,
    read_text_file,
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
    tier: str = "",
    defect_class: str = "",
    reproduction_attempted: str = "",
) -> dict:
    """Build the mapping vocab's predicates read (see its module docstring).

    ``symbol`` and ``file`` are inert to every predicate but carried anyway, so
    that one mapping is both what the predicates judge AND what an audit record
    quotes back. Key names match the shape ``foundry_sync_defects`` already
    passes around, so the same mapping crosses both filing paths unchanged.

    CT-001 / CT-002 / CT-003 — ``tier``, ``class`` and ``reproduction_attempted``
    are carried here rather than assembled into a second dict at the filing
    door, for the same reason ``symbol`` and ``file`` are: ONE mapping is what
    ``validate_defect_filing`` judges AND what ``record_denylist_tripwire``
    quotes back when the LATENT security gate refuses. A second shape would put
    the audit record and the refusal one edit apart from disagreeing about the
    finding they describe — and ``foundry_sync_defects`` (the batch door) hands
    its caller's finding dict straight to the validator, whose keys are already
    spelled exactly this way, so the two doors judge one shape.

    ``defect_class`` lands under the key ``"class"`` because that is the key the
    persisted record, the batch door's finding dicts and the escalation reader
    all already use; ``class`` is a Python keyword and cannot be the parameter.
    """
    return {
        "description": description,
        "spec_ref": spec_ref,
        "target_kind": target_kind,
        "symbol": symbol,
        "file": file_path,
        "tier": tier,
        "class": defect_class,
        "reproduction_attempted": reproduction_attempted,
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

    D-144 — WHO ACTUALLY CALLS THIS. Its only caller is ``_observation_refusal``
    below, which both filing doors run. This said it was "exported because
    ``foundry_sync_defects``'s auto-demotion branch faces the mirror of the same
    question and must not re-derive an answer to it" — the same retired branch
    ``record_denylist_tripwire``'s roster named, deleted by the same D-098 fix,
    which moved that decision into ``_observation_refusal`` precisely so no
    second caller re-derives it. The guarantee survived the caller; the sentence
    naming the caller did not.

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
    production caller pre-filtered instead: ``foundry_sync_defects`` then called
    the observation writer only once ``never_demote_class`` had already returned
    None, so the writer's inner denylist branch could not fire by construction
    and ``observations.json.tripwire`` stayed empty across every denylist
    scenario. An audit signal that only fires for callers who did not need
    auditing is not a control. (That pre-filtering caller is itself history now
    — D-098 removed the batch door's demotion routing entirely — but the reason
    this lives outside the writer survives it.)

    So the decision and the signal are one exported call, and every path that
    routes a finding away from the defect ledger — or refuses one on the
    denylist — makes it. All four callers are audited by the same code, which
    is what keeps the filing paths from drifting apart about what a denylisted
    finding is.

    THE CALLERS, AS THEY STAND (D-144)
    ----------------------------------
      * ``foundry_add_observation`` below — the one remaining DEMOTION path:
        the finding is about to be written to ``observations.json``, and a
        non-None return is what keeps it a defect instead.
      * ``foundry_add_defect``'s LATENT security refusal — fires when
        ``validate_defect_filing`` returns a refusal carrying
        ``denylist_class``, so the ATTEMPT is audited even though nothing is
        written to either ledger.
      * ``foundry_sync_defects``'s refusal loop in ``foundry_orchestrator.py``
        — the same rung at the batch door, over every refused finding in the
        batch.
      * ``foundry_sync_defects``'s declared-comment branch in
        ``foundry_orchestrator.py`` — audit only: the finding stays a defect
        either way, and the record captures that a denylist entry is what
        rescued it.
      * ``server.py``'s ``_audit_security_claim_on_refusal`` — the PRE-DISPATCH
        rung (D-146). `call_tool` validates arguments against the advertised
        schema before dispatch, so a filing refused there never reaches a
        handler; this fires the tripwire for a refused filing whose prose
        matches the security predicate, so a filer cannot switch the audit
        record off by also getting an unrelated field wrong.

    This roster is load-bearing and it has been WRONG once. It used to name
    "``foundry_sync_defects``'s auto-demotion branch", which D-098 deleted when
    the batch door stopped routing comment prose into ``observations.json`` and
    began refusing it in the validation loop instead. So the docstring
    advertised a demotion path that no longer existed, beside a shared
    validator it never mentioned, and a reader auditing "who can write a
    tripwire" was reading the pre-D-098 shape. Re-derive this list from
    ``grep -rn 'record_denylist_tripwire' src/`` when you change a caller;
    do not trust it because it is written down.

    Takes ``fdir`` rather than ``project_root`` because every caller above
    already holds the resolved run dir; re-resolving it here would be another
    derivation of a path the caller has.
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


# D-147 — THE KEYS THE SECURITY PREDICATE MAY NOT READ, AND WHY EACH IS HERE.
#
# The denylist gate used to read `description` and nothing else, so a LATENT
# filing whose security claim rode in ANY other key was accepted. Driven
# through `server.call_tool('Foundry-Sync', ...)` in the filing shape
# `agents/coverage-diff.md` documents — description '', the sentence in a
# `failure` key, tier LATENT — the filing was ACCEPTED (+1 Added, persisted
# with tier LATENT) and `observations.json.tripwire` grew by 0. The batch door
# hands the caller's finding dict straight through, so every key a stream
# invents is a channel, and the shipped stream prose invents plenty:
# `failure`, `source_entry`, `expected_destination` (coverage-diff),
# `fix_hint` (flow-tracer), `spec_text_cited` (assayer), `evidence`, `page`,
# `element` (sight), `recommendation` (research-auditor).
#
# So the scan is an EXCLUSION list, never an allowlist: an allowlist would
# have to name a key before a stream invents it, which is the same race
# D-147 already lost once. What is excluded, and the reason each is:
#
#   closed vocabularies  tier / type / source / target_kind / status — the
#                        caller does not write prose into them; the doors
#                        refuse anything outside the vocabulary anyway.
#   locators             file / symbol / spec_ref — a PATH is not a claim, and
#                        `_SECURITY_RE` matches bounded tokens inside one:
#                        `src/auth/login.py` and `tools/validate.py` both hit
#                        (the `/` and `.` are non-word chars, so both word
#                        boundaries hold). A filing about a file under auth/
#                        would be unfileable as LATENT, and its filer could
#                        not re-word the path to recover. CT-003 states the
#                        same promise for spec_ref one field along: "spec_ref
#                        alone never refuses a LATENT filing".
#   class                the ESCALATION KEY, shared with every sibling
#                        instance — and driven: `agents/assayer.md`'s
#                        documented LATENT example carries
#                        `class: "no-auth-guard-on-destructive-endpoints"`,
#                        which `_SECURITY_RE` matches on the bounded token
#                        `auth` (`-` is a non-word char on both sides). Its
#                        LIVE sibling carries the SAME class, so scanning it
#                        would refuse a shape the surface ships, and the
#                        filer's only escape would be to rename the class —
#                        moving the escalation key mid-run, which is what
#                        CT-002 exists to stop. That is D-099/D-101's shape
#                        exactly (a door refusing its own documented example),
#                        and `tests/test_protocol_prose.py#
#                        test_every_documented_latent_example_survives_the_
#                        filing_door` is what fails if this line moves.
#
# Everything else — known prose, and every key nobody has invented yet — is
# read. Over-matching costs the filer a refusal that names what to do (drive
# it, file LIVE); under-matching is A-AUTO-005's unacceptable failure mode.
_NON_PROSE_FILING_KEYS = frozenset({
    # closed vocabularies
    "tier",
    "type",
    "source",
    "target_kind",
    "status",
    # locators
    "file",
    "symbol",
    "spec_ref",
    # the escalation key
    "class",
})  # 9 items


def _collect_prose(value: object, into: list[str], depth: int = 0) -> None:
    """Append every prose string reachable from ``value``, locators skipped.

    Recurses because a finding is JSON the caller shaped: `{"evidence":
    {"note": "..."}}` and `{"observations": ["..."]}` are both a sentence a
    stream can write, and a scan that only reads top-level strings would admit
    either. The key filter is applied at EVERY level, not just the top, so a
    nested `{"file": "src/auth/x.py"}` is skipped for the same reason the
    top-level one is.

    Depth-bounded rather than cycle-tracked: a finding arrives as decoded JSON
    (no cycles by construction), and the bound is what keeps a hand-built
    Python mapping from recursing without end. Beyond the bound the scan stops
    reading rather than raising — a tool never raises across the MCP boundary,
    and a filing nested six deep is not a shape any surface documents.
    """
    if isinstance(value, str):
        into.append(value)
        return
    if depth >= 6:
        return
    if isinstance(value, Mapping):
        for key, item in value.items():
            if isinstance(key, str) and key in _NON_PROSE_FILING_KEYS:
                continue
            _collect_prose(item, into, depth + 1)
        return
    if isinstance(value, (list, tuple)):
        for item in value:
            _collect_prose(item, into, depth + 1)
        return
    if isinstance(value, (set, frozenset)):
        # Sorted by their text, because `security_scan_text` promises a
        # deterministic string for a given filing and a set's iteration order
        # is not. A set never arrives from decoded JSON; it arrives from a
        # hand-built mapping (a test, a caller assembling a finding in
        # Python), and that caller must get the same scan text twice — the
        # tripwire record quotes this string back.
        for item in sorted(value, key=repr):
            _collect_prose(item, into, depth + 1)


def security_scan_text(finding: Mapping[str, object]) -> str:
    """Every prose value a filing carries, joined for the security predicate.

    This is what `is_security_property_text` is asked about (D-147), and it is
    ONE derivation because two callers need the same answer: the LATENT
    denylist rung in `validate_defect_filing`, and `tripwire_finding` below,
    which is how the audit record comes to name the class the refusal named.

    `description` leads, then the remaining keys in sorted order — so the
    joined text is deterministic for a given filing (a dict's insertion order
    is the caller's, and the tripwire record quotes this string back).
    """
    parts: list[str] = []
    if "description" in finding:
        _collect_prose(finding["description"], parts)
    _collect_prose(
        {k: v for k, v in sorted(finding.items(), key=lambda kv: str(kv[0]))
         if k != "description"},
        parts,
    )
    return "\n".join(p for p in parts if p.strip())


def tripwire_finding(finding: Mapping[str, object]) -> dict:
    """The shape a door hands ``record_denylist_tripwire`` on a denylist refusal.

    WHY THE DESCRIPTION IS SUBSTITUTED (D-147, extending D-083)
    -----------------------------------------------------------
    `record_denylist_tripwire` does not receive the refusal's class; it
    RE-DERIVES one through `vocab.never_demote_class`, whose security entry
    reads `description` alone. D-083 pinned that the two artifacts of one
    event may not contradict each other — "the tripwire may not disagree with
    the refusal it was fired for" (`tests/test_vocab.py#
    test_the_generic_catch_all_is_evaluated_last`). Once the refusal keys on
    ALL the prose and the derivation keys on one field, they contradict each
    other for exactly the filing D-147 drove: refusal SECURITY_PROPERTY_CLAIM,
    tripwire NON_COMMENT (or SPEC_REQUIRED_BEHAVIOUR_CLAIM when a spec_ref is
    present), and an auditor querying the tripwire ledger by class finds
    nothing for the filings AC-007 is about.

    So the derivation is fed the text that actually matched. It stays ONE
    derivation — no second class-decision site, no override argument that
    would let a caller assert a class the predicate never found — and the
    record now quotes the sentence rather than the empty description the
    smuggling filing carried. Both doors pass this shape; `foundry_add_defect`
    below and `foundry_sync_defects`' refusal loop in
    `foundry_orchestrator.py` are the two call sites.

    Returns the finding unchanged when it carries no prose at all: nothing
    matched the security predicate in that case, and the tripwire is firing
    for some other denylist entry whose own reading must not be disturbed.
    """
    scanned = security_scan_text(finding)
    if not scanned:
        return dict(finding)
    return {**finding, "description": scanned}


def validate_defect_filing(finding: Mapping[str, object]) -> dict | None:
    """The tier/class/LATENT checks both filing doors apply, decided in ONE place.

    Returns None when the filing may be persisted, otherwise the house refusal
    dict: ``{"ok": False, "error": ..., "hint": ..., "field": <"tier" | "class"
    | "reproduction_attempted" | "description">}`` plus, for the security
    refusal only, ``"denylist_class": SECURITY_PROPERTY_CLAIM``.

    Reads the mapping and nothing else — no ledger read, no run-dir resolution,
    no write — so the batch door can call it once per finding BEFORE it opens
    its transaction, and so a refusal costs nothing.

    WHY THIS IS A SHARED FUNCTION (CT-001 / CT-002 / CT-003 / AC-010)
    -----------------------------------------------------------------
    There are two filing doors and they live in different modules:
    ``foundry_add_defect`` here, ``foundry_sync_defects`` in
    ``foundry_orchestrator.py``. Every check those two doors were each trusted
    to remember has eventually diverged — D-119 is the shipped instance (the
    two doors disagreed about which cycle a record belonged to, so identical
    findings filed through different doors produced different cycle runs and a
    systemic class escaped ST-002 escalation). A filing whose tier the single
    door demands and the batch door does not is that defect again, one field
    along, and it would be worse: the batch door is the one a whole INSPECT
    stream files through, so the gap would be the common path rather than the
    rare one.

    THE CHECK ORDER IS LOCKED, so that the two doors name the same field first
    for the same bad filing: the security denylist, then tier, then class,
    then — for LIVE only — the prose floor (D-147), then — for LATENT only —
    reproduction_attempted. There is no rung after those; see the D-101 block
    at the tail of this function for why a `file_path` rung was added in GRIND
    cycle 5 and reversed in cycle 6, and the D-147 block at the LIVE rung for
    why that one is scoped and shaped the way it is rather than as a
    `description`-key check.

    D-147 — WHAT THE DENYLIST RUNG READS
    ------------------------------------
    Every prose value the filing carries (`security_scan_text`), not
    `description` alone. The batch door hands the caller's dict straight
    through, so a claim in any key a stream invents used to ride past this
    gate; the driven filing put it in `failure`, which is the key
    `agents/coverage-diff.md` documents. Locators, closed vocabularies and the
    escalation `class` are excluded — see `_NON_PROSE_FILING_KEYS` for the
    driven reason each is.

    D-061 — THE AUDIT TRIPWIRE MAY NOT BE RUNG-DEPENDENT (AC-007 / OT-005 /
    CT-003)
    ----------------------------------------------------------------------
    The denylist rung used to sit LAST, behind tier, class and
    reproduction_attempted, and both doors fire ``record_denylist_tripwire``
    only when the refusal they got back carries ``denylist_class``. So a
    LATENT filing whose description asserted a security property escaped the
    audit control entirely whenever the SAME filing also failed an earlier
    rung. Driven on both doors, ``observations.json`` tripwire length 0 in
    every case: tier LATENT + "the login endpoint does not verify the
    authentication token signature" with no reproduction_attempted; the same
    with ``reproduction_attempted="n/a"``; the same with ``class=""``. That is
    precisely the shape a stream files — a hand-waved security claim with no
    negative-result evidence — so the hole was on the likely path, not a
    corner of it.

    AC-007 and OT-005 are unconditional on the DESCRIPTION matching the
    predicate ("A LATENT filing whose description matches the security-property
    predicate is refused naming SECURITY_PROPERTY_CLAIM and a tripwire record
    is written"), and A-AUTO-005 names this denylist as the mechanism that
    keeps a security-property claim from being demoted. An audit control a
    filer can switch off by ALSO omitting a field is not a control. So the
    predicate is consulted FIRST and the ordering property is preserved by the
    two doors still sharing this one function — LEAD RULING, GRIND cycle 4,
    superseding the tier-first order this docstring pinned before.

    It keys on the tier the caller DECLARED (``== "LATENT"``, before the tier
    rung has judged it) rather than on a validated one, because the rung that
    would validate it is the very rung this must outrank. A filing with no
    tier, or a tier outside DEFECT_TIERS, is not a LATENT filing and is refused
    naming ``tier`` as it always was — CT-003 scopes the denylist to LATENT.

    WHY THE LATENT GATE CONSULTS THE SECURITY PREDICATE AND NOT
    ``never_demote_class`` (CT-003)
    -----------------------------------------------------------
    ``never_demote_class`` returns SPEC_REQUIRED_BEHAVIOUR_CLAIM for ANY
    finding carrying a non-empty ``spec_ref`` (see
    ``vocab.is_spec_required_behaviour_claim``). Routing this gate through it
    would refuse every LATENT filing that cites a requirement — which is the
    majority of them — and OT-005 requires the exact opposite: a LATENT filing
    citing NFR-002 with a scan-gap description is ACCEPTED. A spec_ref alone
    never refuses a LATENT filing. Only the security predicate does.

    THE TRIPWIRE IS THE CALLER'S TO WRITE. ``record_denylist_tripwire`` needs
    the resolved run dir, the cycle and the source, none of which a pure
    validator has, and there is exactly one tripwire writer in this package by
    design (see that function's own docstring). So this returns the refusal
    carrying ``denylist_class`` and the calling door fires the audit record
    through the existing exported path before returning it.
    """
    tier = finding.get("tier")

    # D-061: FIRST rung, ahead of tier/class/reproduction_attempted, so the
    # audit record is written for every LATENT security claim rather than only
    # for the ones that were otherwise well-formed. See the docstring.
    #
    # D-147: asked of `security_scan_text(finding)` — every prose value the
    # filing carries — and no longer of `description` alone. See that
    # function for what is excluded and the driven filing that got past the
    # one-field reading.
    if tier == "LATENT" and is_security_property_text(security_scan_text(finding)):
        return {
            "ok": False,
            "error": (
                f"Refused: {SECURITY_PROPERTY_CLAIM} — a security-property "
                f"claim may never be filed as LATENT."
            ),
            "hint": (
                "A claim that a security property is broken is never a gap "
                "reasoned about: drive it, and file what you observed as "
                "LIVE. Do NOT re-word the filing to get past this "
                "refusal — every prose field is read, not just the "
                "description (D-147); the same predicate guards the "
                "never-demote "
                "denylist, and an audit tripwire has already recorded this "
                "attempt. Fixing the other fields will not get you past it "
                "either: this rung is reached before them."
            ),
            "field": "description",
            "denylist_class": SECURITY_PROPERTY_CLAIM,
        }

    if not isinstance(tier, str) or tier not in DEFECT_TIERS:
        return {
            "ok": False,
            "error": (
                f"Invalid tier: {tier!r}. Must be one of: "
                f"{', '.join(sorted(DEFECT_TIERS))}"
            ),
            "hint": (
                "Every defect carries the evidence tier the filing stream is "
                "answerable for. LIVE means you drove the door and observed "
                "the wrong result — put the reproduction in the description. "
                "LATENT means you looked for the failure and did not find one "
                "— name what you drove in reproduction_attempted. The tier is "
                "not a severity: both are defects and both get fixed."
            ),
            "field": "tier",
        }

    defect_class = finding.get("class")
    if not isinstance(defect_class, str) or not defect_class.strip():
        return {
            "ok": False,
            "error": (
                f"Missing class: {defect_class!r} is not a non-empty root-cause "
                f"class name."
            ),
            "hint": (
                "Escalation keys on the declared class, so a filing without "
                "one cannot recur as anything. Name the root cause this "
                "instance shares with its siblings (e.g. "
                "FALSE_DOCUMENTED_CONTRACT), not the symptom and not the file."
            ),
            "field": "class",
        }

    # D-147 — A LIVE FILING MUST STATE SOMETHING (CT-001 / FR-004).
    #
    # CT-001's input column: "for LIVE the door and observed wrong result in
    # the description". FR-004 verbatim: "LIVE needs the reproduction (door +
    # observed wrong result)." Both doors accepted `description=""` and
    # persisted a record that states nothing — the secondary consequence D-147
    # reports beside the denylist hole, and the reason a reader of
    # `defects.json` cannot tell what was wrong.
    #
    # WHY IT IS SCOPED TO LIVE, AND WHY IT ASKS FOR PROSE RATHER THAN FOR THE
    # DESCRIPTION KEY. Both narrowings are D-101's ruling applied before the
    # fact, not after it:
    #
    #   LIVE only  — FR-005 verbatim: "Server refuses LATENT only when the
    #                description matches the security-property regex". `only`
    #                is the whole word, and D-101 reversed a LATENT `file_path`
    #                rung for breaking it. This rung cannot refuse a LATENT
    #                filing by construction rather than by reachability, so
    #                FR-005's "only" is untouched. A LATENT filing states its
    #                evidence in `reproduction_attempted`, which the rung below
    #                already demands at length.
    #   any prose  — the shapes the streams actually file are the test. Two of
    #                `agents/coverage-diff.md`'s three documented defects carry
    #                tier LIVE and NO `description` key at all: the sentence is
    #                in `failure`, beside `source_entry` and
    #                `expected_destination`. A rung demanding the DESCRIPTION
    #                key would refuse a shape that surface ships, which is
    #                D-099's defect exactly. So the question is whether the
    #                filing says anything anywhere, asked of the same
    #                `security_scan_text` the denylist rung asks — one
    #                derivation of "what prose does this filing carry", used by
    #                both rungs that need it.
    #
    # A filing carrying only a class and a path names a bucket and a location
    # and asserts nothing about either, and `field` is `description` because
    # that is where a filing that reaches THIS door (whose `_finding_mapping`
    # has no `failure` key to route prose into) must put it.
    if tier == "LIVE" and not security_scan_text(finding):
        return {
            "ok": False,
            "error": (
                "Missing description: a LIVE filing carries no prose at all, "
                "so the record would state nothing about what is wrong."
            ),
            "hint": (
                "LIVE means you drove the door and observed the wrong result: "
                "name the door you drove and what it returned. A class and a "
                "file locate a finding; they do not state one. If you did not "
                "drive it, file LATENT with a reproduction_attempted statement "
                "instead."
            ),
            "field": "description",
        }

    if tier != "LATENT":
        return None

    problem = reproduction_attempted_problem(finding.get("reproduction_attempted"))
    if problem is not None:
        return {
            "ok": False,
            "error": f"Invalid reproduction_attempted: {problem}",
            "hint": (
                "A LATENT filing is a gap you REASONED about rather than "
                "reproduced, so the negative result is the whole evidence: "
                "say what you drove and what it found (e.g. 'AST sweep of "
                "both roots finds 0 sites'). If you did drive the failure, "
                "file it as LIVE with its reproduction instead."
            ),
            "field": "reproduction_attempted",
        }

    # D-101 — THERE IS NO `file_path` RUNG HERE, AND ADDING ONE IS A
    # REGRESSION (FR-005 / CT-001 / CT-003).
    #
    # GRIND cycle 5 added a fourth LATENT rung refusing a filing whose `file`
    # was absent or blank, to close D-089's observation that the report's
    # LATENT backlog rendered a row promising a location it did not have. The
    # lead REVERSED that ruling in cycle 6, because the rung contradicts a
    # Locked requirement three ways over:
    #
    #   FR-005 verbatim  "Server refuses LATENT only when the description
    #                    matches the security-property regex ... naming the
    #                    denylist class". `only` is the whole word.
    #   CT-001 errors    admits exactly two refusals — the missing tier and
    #                    the missing negative-result statement.
    #   CT-003 errors    "spec_ref alone never refuses a LATENT filing", the
    #                    same shape of promise one field along.
    #
    # Driven at the reversal (the pre-edit validator at 5dd9dad, run over the
    # six `tier: LATENT` examples DOCUMENTED on the filing surfaces the streams
    # copy from): two were refused by this rung, and they are precisely the two
    # whose subject HAS no file —
    #
    #   agents/coverage-diff.md   a casting that declares no coverage_list at
    #                             all, located by `casting_id: 7`;
    #   skills/sight/SKILL.md     an empty-state gap in a rendered view,
    #                             located by `page` and `element`.
    #
    # Neither is a filing that forgot its path. A coverage gap belonging to a
    # whole casting and a UI gap belonging to a route are located by the axis
    # their own stream works in, and the rung demanded a fifth axis that does
    # not exist for them. A door that refuses its own documented example
    # teaches the stream that the prose is wrong, and the stream's next move is
    # to invent a path — a fabricated location in the backlog is strictly worse
    # than an absent one, because it is read as evidence.
    #
    # D-089's real observation — a location-free LATENT row rendered under a
    # header promising a location — is closed on the REPORT side, by rendering
    # such a row honestly (naming that the filing carried no file) rather than
    # by refusing the filing. A validator is the wrong place to enforce a
    # renderer's promise: the filing is the evidence, and evidence is not
    # improved by being refused.
    #
    # The security denylist is NOT here either (D-061); it is the first rung of
    # this function. A LATENT filing that reaches this line named its negative
    # result and did not assert a security property. That is everything the
    # contract asks of it.
    return None


def retier_matching_untiered(
    records: list,
    *,
    source: str,
    type: str,  # noqa: A002 - the record's own field name; see below
    file: str,
    symbol: str,
    tier: str,
    reproduction_attempted: str,
    defect_class: str,
    cycle: int,
) -> str | None:
    """Classify an open untiered record in place. Returns its id, or None.

    Mutates ``records`` (the list a ``ledger_transaction`` is yielding) and
    returns the id of the record it re-tiered, or ``None`` when no open
    untiered record matches — in which case the caller files as it always did.

    WHY THIS IS A SHARED FUNCTION (FR-051 / AC-008 / CT-001 / CT-002, D-077)
    -----------------------------------------------------------------------
    D-062 implemented FR-051's untiered exit — "blocks like LIVE until a
    stream re-files it with a tier" — at ONE of the two filing doors. The
    batch door ``foundry_sync_defects`` re-tiered in place; ``foundry_add_defect``
    had no such branch, and the server's own hint named neither door. Driven,
    both doors, on the same seeded ledger (one open pre-change record D-001
    with no ``tier`` key, blocking 1): through Foundry-Sync the identical
    finding re-filed with tier LATENT returned ``{'retiered': 1,
    'retiered_ids': ['D-001'], 'added': 0, 'total_open': 1}``, D-001 carried
    LATENT and blocking dropped to 0; through Foundry-Defect the same finding
    returned ``{'defect_id': 'D-002'}`` — D-001 stayed open and untiered, D-002
    was appended beside it as a duplicate, and blocking was unchanged at 1.
    That is the exact wrong result D-062 recorded, one door over, and it
    reproduced through the door every stream's prose instructs
    (``skills/sight/SKILL.md``: file through "the foundry MCP ``Foundry-Defect``
    tool (one call per finding)").

    So the rule is DERIVED ONCE, here, and both doors call it — the same shape
    ``validate_defect_filing`` already uses for the tier/class/denylist rungs,
    and for the same reason its docstring gives: "Every check those two doors
    were each trusted to remember has eventually diverged."

    WHAT IDENTITY IS (and what it deliberately is not)
    --------------------------------------------------
    ``(source, type, file, symbol)`` — the four fields that say WHICH finding
    this is. The description is excluded on purpose: a re-filing stream
    rewrites its prose, and requiring the wording to match would make the exit
    unreachable for exactly the reason the hint was. ``type`` must be the
    CANONICAL spelling on both doors (``vocab.canonical_defect_type``), since
    that is what both doors persist — matching a raw alias would miss a record
    filed under the other spelling of the same type.

    The record KEEPS ITS ID, so every citation and every task already naming it
    stays valid, and ``retiered_in_cycle`` records when the classification
    arrived. ``class`` is filled only when ABSENT: a class the earlier filing
    declared is what escalation has been keying on, and overwriting it here
    would move a class mid-run.

    Only an UNTIERED record takes this path. A record some stream already
    classified is answerable for its evidence, and a re-filing must not
    silently rewrite that.

    ``type`` shadows the builtin inside this frame. The name is the record's
    own field name and is fixed by the cross-module contract casting 3 calls
    against; nothing in this body needs ``type()``.
    """
    for d in _dict_records(records):
        if d.get("status") != "open" or defect_tier(d) != TIER_UNKNOWN:
            continue
        if not (
            d.get("source") == source
            and d.get("type") == type
            and (d.get("file") or "") == (file or "")
            and (d.get("symbol") or "") == (symbol or "")
        ):
            continue
        record_id = d.get("id")
        if not isinstance(record_id, str) or not record_id:
            # A record with no usable id can be mutated but not NAMED, and a
            # caller told None files the duplicate anyway — the very outcome
            # D-077 reports. The match must be one this can both classify and
            # report, so an id-less historical record is left for the migration
            # path rather than half-handled here.
            continue
        d["tier"] = tier
        d["reproduction_attempted"] = (
            reproduction_attempted if tier == "LATENT" else None
        )
        if not str(d.get("class") or "").strip():
            d["class"] = defect_class
        d["retiered_in_cycle"] = cycle
        return record_id
    return None


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


# ---------------------------------------------------------------------------
# The F0 self-target preflight (GI-004 / ST-009 / CT-010 / FR-017 / FR-018 /
# FR-035 / FR-050 / AC-025 / AC-026 / AC-027 / OT-017 / OT-018).
#
# WHAT THIS EXISTS TO CATCH, CONCRETELY
# -------------------------------------
# The foundry plugin ships from a git-URL marketplace and installs
# version-namespaced at ~/.claude/plugins/cache/guild/foundry/<version>/, and
# `plugin.json.mcpServers.foundry` launches THAT tree's server per project. So
# a run whose TARGET is the foundry plugin routinely executes on a DIFFERENT
# build of foundry than the working tree it is editing: the process fixes the
# run ships are not available to the run that shipped them, and the F6 report
# has to carry a "dual reality" section explaining which of the two anything
# was true of. That is not a hypothetical — it is how the run that produced
# this code was itself launched.
#
# The remedy is a launch flag, not a mid-run repair (GI-004): nothing here
# calls /reload-plugins, rewrites .mcp.json, or installs a plugin. The
# preflight's whole job is to REFUSE early and print the exact command that
# starts a server on the working tree.
# ---------------------------------------------------------------------------

#: FR-035 — the commit of a server whose git could not be read. It is a
#: SENTINEL, not a value: `_commit_matches` refuses it against anything,
#: including itself, because "I could not learn either commit" is not
#: evidence that they agree. Reporting it as unknown rather than treating it
#: as a match is the whole of the flexible clause.
UNKNOWN_COMMIT = "unknown"


def _executing_server_root() -> Path:
    """The plugin directory the RUNNING server was imported from.

    Derived from ``foundry_mcp.__file__``, never from ``project_root``: the
    entire point of the preflight is that those two can differ, so deriving
    the executing root from the target's path would compare the working tree
    against itself and pass every time.

    ``foundry_mcp/__init__.py`` sits at
    ``<plugin>/mcp-server/src/foundry_mcp/__init__.py``, so the plugin
    directory is its fourth parent.
    """
    import foundry_mcp

    return Path(foundry_mcp.__file__).resolve().parents[3]


def _executing_server_version() -> str:
    """The running server's own ``__version__`` (FR-017)."""
    import foundry_mcp

    return getattr(foundry_mcp, "__version__", "")


def _plugin_manifest_version(plugin_dir: Path) -> str:
    """The ``version`` of ``<plugin_dir>/.claude-plugin/plugin.json``, or "".

    Read through the guarded reader: an unreadable or non-object manifest
    yields "" rather than a raise, and "" never compares equal to a real
    version string, so an unreadable manifest refuses a self-targeting run
    exactly as a mismatched one does.
    """
    data, problem = read_document(plugin_dir / ".claude-plugin" / "plugin.json")
    if problem is not None:
        return ""
    version = data.get("version")
    return version if isinstance(version, str) else ""


def _git_head(root: Path) -> str:
    """``git -C <root> rev-parse HEAD``, or ``UNKNOWN_COMMIT``. Never raises.

    FR-035 leaves HOW the server learns its commit to the implementer, with
    one proviso: a missing commit is REPORTED as unknown, never treated as a
    match. Every failure mode collapses to the sentinel — git absent from
    PATH, the directory not a work tree, a detached or empty repository, a
    hung invocation. ``capture_output`` is not optional: this server speaks
    JSON-RPC over stdio, and a subprocess inheriting stdout would write git's
    output into the protocol channel (D-149's class, one process out).
    """
    try:
        proc = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return UNKNOWN_COMMIT
    if proc.returncode != 0:
        return UNKNOWN_COMMIT
    return proc.stdout.strip() or UNKNOWN_COMMIT


def _commit_matches(server_commit: str, target_commit: str) -> bool:
    """True only when two REAL commits are the same one (FR-035).

    ``UNKNOWN_COMMIT`` on either side is a mismatch — including when it is on
    BOTH sides, where bare string equality would otherwise report agreement
    between two things nobody could read. That case is the dangerous one: it
    is what a server installed outside a git tree looks like, which is exactly
    the arrangement the preflight exists to catch.
    """
    if UNKNOWN_COMMIT in (server_commit, target_commit):
        return False
    return server_commit == target_commit


def _find_foundry_plugin_dir(project_root: Path) -> Path | None:
    """The directory under ``project_root`` holding a foundry ``plugin.json``.

    FR-018 / AC-025: self-target is EXACTLY the presence of a plugin.json
    named foundry under the project root — ``.claude-plugin/plugin.json`` or
    ``plugins/*/.claude-plugin/plugin.json``. There is no flag to remember and
    no run configuration to get wrong; the target's own tree answers the
    question.

    Returns the directory that CONTAINS the matched ``.claude-plugin``
    directory (the value the launch command names), or None when the project
    is not the foundry plugin — in which case nothing is compared and no
    warning is emitted (FR-050 / CT-010).

    A manifest that cannot be read or is not an object is SKIPPED rather than
    refused: it has not been shown to be foundry's, and refusing every run
    whose project happens to contain a corrupt third-party plugin manifest
    would make an unrelated file able to block an unrelated build.
    """
    candidates = [project_root / ".claude-plugin" / "plugin.json"]
    candidates.extend(sorted((project_root / "plugins").glob("*/.claude-plugin/plugin.json")))
    for manifest in candidates:
        data, problem = read_document(manifest)
        if problem is not None:
            continue
        if data.get("name") == "foundry":
            return manifest.parent.parent
    return None


def _self_target_preflight(project_root: Path) -> tuple[dict, dict | None]:
    """``(version fields for state.json, refusal or None)`` (CT-010 / ST-009).

    ALWAYS returns the four version fields plus ``self_target``, because
    FR-050 requires a run with no foundry manifest to RECORD and display what
    it executed on even though it compares nothing. The refusal is the second
    element and is non-None only for a self-targeting run whose executing
    server disagrees with the working tree.

    Called before any run directory, state.json or manifest is written: a
    refused init that has already created half a run is worse than the
    mismatch it caught, because the next thing the operator does is relaunch,
    and they would relaunch into an archive holding a stillborn run.
    """
    server_root = _executing_server_root()
    fields = {
        "server_version": _executing_server_version(),
        "plugin_version": _plugin_manifest_version(server_root),
        "server_root": str(server_root),
        "server_commit": _git_head(server_root),
        "self_target": False,
    }

    target_plugin_dir = _find_foundry_plugin_dir(project_root)
    if target_plugin_dir is None:
        return fields, None

    fields["self_target"] = True
    launch_command = f"claude --plugin-dir {target_plugin_dir}"
    hint = (
        "This run's TARGET is the foundry plugin, but the executing server is "
        "a different build of it — so the fixes this run ships would not be "
        "the ones it runs on. Quit, relaunch with the command above so the "
        "server is started from the working tree, and re-run Foundry-Init. "
        "The switch is NEVER attempted mid-run: no /reload-plugins, no "
        "rewriting .mcp.json, no installing a plugin while a run is open."
    )

    target_version = _plugin_manifest_version(target_plugin_dir)
    if target_version != fields["plugin_version"]:
        return fields, {
            "ok": False,
            "error": (
                f"Self-targeting run refused — version mismatch: the working "
                f"tree's plugin.json declares {target_version!r} but the "
                f"executing server was imported from {fields['server_root']} "
                f"whose plugin.json declares {fields['plugin_version']!r}."
            ),
            "hint": hint,
            "launch_command": launch_command,
            "mismatch": "version",
            **fields,
            "target_plugin_version": target_version,
        }

    target_commit = _git_head(project_root)
    if not _commit_matches(fields["server_commit"], target_commit):
        return fields, {
            "ok": False,
            "error": (
                f"Self-targeting run refused — commit mismatch: the working "
                f"tree is at {target_commit} but the executing server at "
                f"{fields['server_root']} is at {fields['server_commit']}. "
                f"({UNKNOWN_COMMIT!r} means git could not be read there, and "
                f"is never treated as a match.)"
            ),
            "hint": hint,
            "launch_command": launch_command,
            "mismatch": "commit",
            **fields,
            "target_commit": target_commit,
        }

    return fields, None


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
    max_cycles: int = 0,
) -> dict:
    """Initialize a foundry run under foundry-archive/.

    Args:
        resume: Name of existing run to resume (e.g. 'bold-falcon'). Runs the
            SAME self-target preflight as a fresh init (D-109): it refuses on a
            version or commit mismatch naming the reason and the launch
            command, and on success it refreshes ``server_version``,
            ``plugin_version``, ``server_root``, ``server_commit`` and
            ``self_target`` in state.json so the run's recorded provenance is
            the build it is executing on rather than the one that created it.
        max_cycles: CT-016 / FR-024 — the GRIND cycle ceiling from the
            ``--max-cycles`` flag. Persisted to BOTH state.json and
            castings/manifest.json. Default 0 means unbounded. The Foundry-Phase
            call that would exceed it SUCCEEDS, sets phase HALTED and generates
            the report; HALTED is a named terminal state and is not DONE.
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

        # D-109 — RESUME RUNS THE SAME PREFLIGHT AND RE-RECORDS THE SAME
        # FIELDS (FR-017 / FR-050 / CT-010 / ST-009 / US-006).
        #
        # This branch used to return before the preflight, and justified it
        # with "Resume also writes none of these fields, so there is nothing
        # it could record differently". That sentence was false in the only
        # way that mattered: the fields it does not write are STILL THERE,
        # written by the init that created the run, and every reader trusts
        # them. Driven without hand-editing any artifact — a run created while
        # the executing server root pointed at the 4.9.0 cache tree, then
        # resumed on the working-tree build — state.json still said
        # server_version 1.9.0, plugin_version 4.9.0, server_commit
        # 259f8402, server_root .../cache/foundry/4.9.0, while the executing
        # process was 1.9.0 at 5dd9dad under .../guild/plugins/foundry.
        # Foundry-Next then rendered that stale row as fact, and REPORT.md's
        # executing-versions table repeated it. US-006 exists precisely so the
        # report needs no dual-reality section; a resume that carried a run's
        # birth provenance forward as its execution provenance manufactured
        # exactly that dual reality.
        #
        # So resume re-records, and — because it re-records — it must also
        # refuse. The old carve-out ("resume is the RECOVERY door") was
        # defensible only while the fields stayed untouched: once they are
        # refreshed they are TRUE, and a true record of the wrong build is
        # still the wrong build shipping this run's fixes. The refusal's own
        # remedy is the launch command, which reaches the run's artifacts on
        # the next attempt — an operator is delayed one relaunch, not stranded.
        #
        # ORDER: after the exists and corrupt-state guards, before
        # `set_active_run`. D-095's property is that resuming names the
        # artifact the operator is reaching for, so "run not found" and
        # "state.json is corrupt" still outrank drift; and a refused resume
        # must not leave a run activated behind it.
        version_fields, preflight_refusal = _self_target_preflight(root)
        if preflight_refusal is not None:
            return preflight_refusal

        set_active_run(resume)
        with _locked_document(state_path) as document:
            document.update(version_fields)
            state = dict(document)
        return {
            "foundry_dir": str(run_dir),
            "run_name": resume,
            "resumed": True,
            "state": state,
            # D-109 — echoed beside the refreshed copy, exactly as the new-run
            # result echoes it, so a caller rendering either result reads what
            # the run is EXECUTING on rather than what it was born on.
            **version_fields,
            "display": (
                foundry_hammer(f"F O U N D R Y  Resumed: {resume}")
                + f"\n  Phase: {state.get('phase', '?')}  Cycle: {state.get('cycle', 0)}"
                + f"\n{FOUNDRY_SEP}"
                + f"\nCall Foundry-Next for instructions."
            ),
            "next_step": "Call Foundry-Next to see status and get instructions.",
        }

    # --- New run ---

    # ST-009 / CT-010 — the self-target preflight, BEFORE anything is created.
    #
    # Ordering is the requirement, not a nicety: `archive.mkdir` is four lines
    # below, and a refusal that has already made `foundry-archive/<name>/`
    # leaves the operator relaunching into an archive holding a stillborn run
    # they must now identify and delete.
    #
    # BOTH DOORS ARE GATED (D-109). This one is not the only entry into a run:
    # resume above calls the same helper, refuses on the same mismatch and
    # re-records the same fields, because a resumed run executes on the
    # server that resumed it and every reader of state.json is told so. The
    # carve-out that used to live here — "resume writes none of these fields,
    # so there is nothing it could record differently" — is the sentence
    # D-109 falsified; see the block in that branch.
    version_fields, preflight_refusal = _self_target_preflight(root)
    if preflight_refusal is not None:
        return preflight_refusal

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
        # CT-016 / FR-024 — the GRIND cycle ceiling. state.json is where the
        # phase handler and Foundry-Next read it; the manifest carries it too,
        # mirroring how temper/nyquist are written to both.
        "max_cycles": max_cycles,
        # FR-017 / FR-050 / AC-027 — what this run actually EXECUTED on:
        # server_version, plugin_version, server_root, server_commit and
        # self_target. Recorded on EVERY run, compared only on a self-targeting
        # one, so a report never has to guess which build produced a result.
        **version_fields,
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
        # CT-016 — the parameter, not the hardcoded 0 this literal carried
        # while no caller could set it.
        "max_cycles": max_cycles,
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
            # D-146: refuse rather than copy a spec we could not decode. The
            # old read raised UnicodeDecodeError across the MCP boundary; the
            # tempting alternative — errors="replace" — is worse than either,
            # because it would seed the run with a silently mangled spec that
            # every later phase hashes and trusts.
            spec_src_text, spec_src_problem = read_text_file(src)
            if spec_src_problem is not None:
                return document_refusal(src, spec_src_problem)
            dest.write_text(spec_src_text, encoding="utf-8")
            spec_copied = True
            files_created.append("spec.md")

    # Set this as the active run for this session
    set_active_run(run_name)

    return {
        "foundry_dir": str(fdir),
        "run_name": run_name,
        "files_created": files_created,
        "spec_copied": spec_copied,
        "max_cycles": max_cycles,
        # AC-027 — echoed beside the persisted copy so a caller rendering the
        # init result reads what the run recorded rather than re-deriving it.
        # `_fmt_foundry_init` returns `r["display"]` when present, so adding
        # keys here changes no rendered output.
        **version_fields,
        "display": _format_init_display(run_name, state.get("temper", False), state.get("nyquist", False)),
        "next_step": "Call Foundry-Next to get decomposition instructions. Print the display above FIRST.",
    }


def _merged_door_refusal(problems: list[dict]) -> dict:
    """One refusal naming EVERY rung a single filing failed, in rung order.

    D-128 — THE SINGLE DOOR ACCUMULATES ITS RUNGS, AS THE BATCH DOOR ALWAYS
    HAS (AC-007 / OT-005 / CT-003 / FR-005)
    ---------------------------------------------------------------------
    ``foundry_add_defect`` used to RETURN at the source and defect_type
    vocabulary rungs, so a filing that was wrong there never reached
    ``validate_defect_filing`` at all — and the D-061 ruling that moved the
    security denylist to the validator's FIRST rung, precisely so the audit
    tripwire could not be switched off by also failing an earlier rung, was
    silently undone one frame further out. ``foundry_sync_defects`` appends its
    source and type problems to ``refusals`` and runs the validator anyway, so
    the two doors disagreed about whether one filing is audited: driven
    in-process with tier LATENT, an authentication-property description and
    ``source="bogus"``, the single door refused naming ``source`` with
    ``observations.json`` tripwire delta 0, and the batch door refused the same
    filing naming BOTH ``source`` and SECURITY_PROPERTY_CLAIM with delta 1.

    So this door collects rather than returns, and this function is how the
    collection becomes one answer. A caller that fails exactly one rung gets
    that rung's refusal dict UNCHANGED — byte-identical to what this door
    returned before D-128 — because the merge is only ever reached by a filing
    that failed more than one, which is the only case that used to be lossy.

    ``field`` is the FIRST failing rung, so the two doors still name the same
    field first for the same bad filing (the property D-098 pinned); the whole
    ordered roster is in ``fields`` and ``refusals``. ``denylist_class``,
    ``refused_class`` and ``observation_classes`` are hoisted to the top level
    so a caller reads them off a merged refusal exactly where it reads them off
    a single-rung one — a key that moves depending on how many rungs failed is
    how the next reader comes to miss the audit class.
    """
    return {
        "ok": False,
        "error": (
            f"Refused on {len(problems)} rungs — nothing was recorded. "
            + "; ".join(f"{p.get('field', '?')}: {p['error']}" for p in problems)
        ),
        # Every rung's hint, not just the first: the filer has to fix all of
        # them to get this filing in, and a hint naming one rung reads as the
        # whole remedy.
        "hint": " ".join(p["hint"] for p in problems if p.get("hint")),
        "field": problems[0].get("field", ""),
        "fields": [p.get("field", "") for p in problems],
        # `field` + `reason` are the batch door's per-refusal key names, minus
        # the `index` that means nothing when there is one filing.
        "refusals": [
            {"field": p.get("field", ""), "reason": p["error"]} for p in problems
        ],
        **{
            key: p[key]
            for key in ("denylist_class", "refused_class", "observation_classes")
            for p in problems
            if key in p
        },
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
    tier: str = "",
    reproduction_attempted: str = "",
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
        defect_class: the root-cause class shared by several instances,
            persisted under the key ``"class"``. REQUIRED (CT-002 / FR-007):
            escalation keys on it, so a filing without one cannot recur as
            anything. The path fallback other readers carry survives only for
            READING pre-change archives.
        tier: a member of ``vocab.DEFECT_TIERS`` — LIVE when the stream drove
            the door and observed the wrong result, LATENT when it looked for
            the failure and found none. REQUIRED (CT-001 / FR-004). It is NOT
            a severity: both tiers are defects and both get fixed; the axis
            decides only which gate a still-open instance blocks.
        reproduction_attempted: for a LATENT filing, the statement naming what
            was driven and what it found (e.g. "AST sweep of both roots finds
            0 sites"). Required on LATENT and refused when it is a placeholder
            (``vocab.reproduction_attempted_problem`` is the check); stored as
            ``None`` on a LIVE record, whose reproduction lives in the
            description.

        file_path: the file the finding is in, bare and repo-relative, never
            carrying a line number. Optional on EVERY tier (D-101): FR-005
            refuses a LATENT filing "only when the description matches the
            security-property regex", so a location-free LATENT filing is
            accepted and the report renders it naming that it carried no file.
            Give one whenever you have one — a located backlog entry is the
            one a later cycle can actually go and drive — but a finding
            located by ``symbol`` alone is a filing, not an error.

    Returns:
        ``{defect_id, cycle, declared_cycle, type, total_defects, open_defects,
        retiered, retiered_ids}``, or a named refusal ``{error, hint, ...}``
        when the vocabulary check, the comment-prose check, or
        ``validate_defect_filing`` rejects the filing.

        D-128: the rungs are WALKED, not short-circuited. A filing that fails
        one rung gets that rung's refusal unchanged; one that fails several
        gets a single merged refusal naming every one of them (``fields`` and
        ``refusals`` carry the ordered roster, ``field`` the first). The
        security tripwire therefore fires for a LATENT security-property claim
        however else the same filing is malformed — see
        ``_merged_door_refusal`` for the divergence from the batch door that
        short-circuiting caused.

        FR-051 / D-077: when the filing matches an OPEN UNTIERED record on
        ``(source, type, file, symbol)``, that record is classified in place
        and no new one is appended — ``defect_id`` is the existing record's id,
        ``retiered`` is 1 and ``retiered_ids`` carries it. The two keys and
        their types are the batch door's, deliberately.
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

    # D-128 \u2014 EVERY RUNG BELOW APPENDS; NOTHING RETURNS UNTIL THE LADDER IS
    # WALKED. See `_merged_door_refusal` for the driven divergence this closes.
    # The batch door has always accumulated, and the D-061 ruling (the security
    # denylist is the validator's FIRST rung so the audit tripwire is never
    # rung-dependent) is only true of THIS door if the validator is reached
    # whatever else the filing got wrong.
    problems: list[dict] = []

    # Server-side vocabulary validation (CT-002). Only the client schema
    # guarded these before, and `foundry_sync_defects` silently rewrote an
    # unknown source to "trace" \u2014 so a finding could be attributed to a stream
    # that never filed it. Rejection replaces coercion on both surfaces.
    if source not in DEFECT_SOURCE_IDS:
        problems.append({
            "error": (
                f"Invalid source: {source!r}. Must be one of: "
                f"{', '.join(sorted(DEFECT_SOURCE_IDS))}"
            ),
            "hint": (
                "Source is stored verbatim and is never coerced onto another "
                "stream. File under the id of the stream that actually found "
                "this, or extend schemas/vocab.py via a phase-level RFC."
            ),
            # The field name the batch door's refusal also carries, so a caller
            # reading either door's answer keys on one spelling (D-128).
            "field": "source",
        })
    if defect_type not in DEFECT_TYPES:
        problems.append({
            "error": (
                f"Invalid defect_type: {defect_type!r}. Must be one of: "
                f"{', '.join(sorted(DEFECT_TYPES))}"
            ),
            "hint": (
                "Use the closest member of the canonical set, or extend "
                "schemas/vocab.py via a phase-level RFC."
            ),
            "field": "defect_type",
        })

    # Comment-prose refusal (AC-001). Engages only when the caller declared
    # the subject is a comment and no denylist entry outranks the class \u2014 see
    # the module docstring for why an undeclared subject is never refused.
    finding = _finding_mapping(
        description,
        spec_ref,
        target_kind,
        symbol=symbol,
        file_path=file_path,
        tier=tier,
        defect_class=defect_class,
        reproduction_attempted=reproduction_attempted,
    )
    refused_class = _observation_refusal(finding)
    if refused_class is not None:
        problems.append({
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
            # The field the batch door names for this same rung
            # (`foundry_sync_defects` appends `"field": "description"` beside
            # its own copy of this text), so the two doors' refusals key alike.
            "field": "description",
        })

    # The tier/class/LATENT gate (CT-001 / CT-002 / CT-003 / AC-006 / AC-007 /
    # AC-010). Shared with the batch door rather than re-spelled here — see
    # `validate_defect_filing` for why the two doors cannot be trusted to
    # remember a check each.
    #
    # It runs AFTER the comment-prose refusal deliberately: a finding that is
    # comment prose is not a defect at all, and telling its filer about a
    # missing tier would send them to add a field to a record that should never
    # reach this ledger. The refusal a caller gets first is the one that
    # decides which LEDGER the finding belongs in.
    #
    # D-128: SKIPPED only for comment prose, exactly as the batch door skips it
    # (`filing_problem = None` under its own `refused_class` branch) — and
    # reached for every OTHER failing rung, which is the divergence D-128
    # reports. A bad `source` is not a reason to leave a security claim
    # unaudited; it is a reason to name both faults at once.
    if refused_class is None:
        filing_refusal = validate_defect_filing(finding)
        if filing_refusal is not None:
            problems.append(filing_refusal)

    if problems:
        if any(p.get("denylist_class") for p in problems):
            # The audit signal is fired through the ONE exported tripwire
            # writer, before the refusal is returned, exactly as
            # `foundry_add_observation` and `foundry_sync_defects` fire it. A
            # second writer here would be the FR-002 defect returning: an audit
            # control that only records the attempts one of its callers makes.
            #
            # D-083 — THE TRIPWIRE RECORDS THE SAME CLASS THE REFUSAL NAMES.
            #
            # `record_denylist_tripwire` does not receive the refusal's class;
            # it re-derives one through `vocab.never_demote_class`. This
            # comment used to argue that the two answering differently "is not
            # a disagreement" — the refusal naming the predicate the LATENT
            # gate consulted, the tripwire naming why the finding could never
            # be demoted at all. That rationale was wrong and D-083 is the
            # defect it excused: `_NEVER_DEMOTE_PREDICATES` led with
            # NON_COMMENT, whose predicate fires for ANY declared non-comment
            # `target_kind`, so a filing that named a real subject persisted
            # NON_COMMENT while this very call refused it naming
            # SECURITY_PROPERTY_CLAIM. One event, two artifacts that
            # contradict each other — and the losing shapes (`code`, `test`)
            # are the DEFAULT shape of every production-code filing, so an
            # auditor querying observations.json.tripwire for
            # SECURITY_PROPERTY_CLAIM found nothing for exactly the filings
            # AC-007 is about.
            #
            # Casting 1 reordered that tuple most-specific-first, so the
            # security entry — the one a refusal also names, on the same
            # `_SECURITY_RE` this gate consults through
            # `is_security_property_text` — is now returned ahead of the
            # generic catch-all. Driven at this door across target_kind "",
            # code, test, comment and config: refusal and tripwire both read
            # SECURITY_PROPERTY_CLAIM in all five. The two artifacts of one
            # event agree, which is the only thing that makes the audit ledger
            # queryable by class.
            #
            # Held by `tests/test_vocab.py#test_the_defect_door_audits_under_
            # the_class_it_refuses` for this door and its `_the_sync_door_`
            # sibling for the batch one — pinned at the doors rather than only
            # at the predicate tuple, because it is the DOOR that writes the
            # two artifacts an auditor later compares.
            #
            # D-128: `source` here may be the very value the source rung just
            # refused. Recorded RAW, exactly as the batch door records it
            # (`normalized[...]["source"]` is the caller's stripped value, not a
            # validated one): the tripwire's subject is the ATTEMPT, and an
            # attempt made under an unknown stream id is one an auditor most
            # needs to see. Coercing or blanking it here would re-file the
            # attempt under a stream that did not make it, which is the
            # mis-attribution CT-002 exists to stop.
            #
            # D-147: the finding is handed over through `tripwire_finding`,
            # which puts the prose the refusal actually matched where
            # `never_demote_class` reads. Without it the widened denylist rung
            # and the one-field re-derivation disagree for exactly the filing
            # D-147 drove — refusal SECURITY_PROPERTY_CLAIM, tripwire
            # NON_COMMENT — which is D-083 returning one field along. The
            # batch door passes the same shape at its own refusal loop.
            record_denylist_tripwire(
                fdir, tripwire_finding(finding), cycle=_server_cycle(fdir), source=source
            )
        # D-128: a filing that failed ONE rung gets that rung's dict unchanged,
        # so every refusal this door returned before this change is
        # byte-identical after it. The merge is reached only by a filing that
        # failed several, which is the case that used to lose all but the first.
        return problems[0] if len(problems) == 1 else _merged_door_refusal(problems)

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
        # CT-001 / FR-004: the evidence axis, on every record. Written from
        # the validated value, so a persisted record's tier is always a member
        # of DEFECT_TIERS — `vocab.TIER_UNKNOWN` is a READ-side sentinel for
        # records written before this release and is NEVER written by a door.
        "tier": tier,
        # CT-002 / FR-007: `class` moved OUT of the trailing `if defect_class:`
        # block and into the literal. It was conditional because it was
        # optional; the validator above has already refused an absent or blank
        # one, so a record without the key can no longer be produced here — and
        # leaving the conditional would have meant escalation still had to
        # handle a keyless record it can never again be handed.
        "class": defect_class,
        # FR-004 / FR-029: the negative result a LATENT filing is answerable
        # for, and explicitly `None` on a LIVE record rather than "" — absent
        # evidence and empty evidence are different claims, and a LIVE record's
        # reproduction lives in the description where the stream put it.
        "reproduction_attempted": reproduction_attempted if tier == "LATENT" else None,
        "description": description,
        "spec_ref": spec_ref,
        "symbol": symbol,
        "file": file_path,
        "status": "open",
        "fixed_in_cycle": None,
        # C-2 / GI-003: the three fields Foundry-Fix sets when this defect is
        # closed, seeded null at filing so every record has one shape from the
        # moment it exists. A reader asking "who fixed this and with what test"
        # gets `None` from an open defect rather than a KeyError, which is what
        # lets the report and the lead_fix handoff read every record the same
        # way instead of branching on whether the fix door has run yet.
        "regression_test": None,
        "authored_by": None,
        "fix_commit": None,
        "created_at": now,
    }
    if target_kind:
        defect["target_kind"] = target_kind

    defects_path = fdir / "defects.json"
    with ledger_transaction(defects_path, "defects") as defects:
        # FR-051 / AC-008 / D-077 — THE UNTIERED EXIT, AT THIS DOOR TOO.
        #
        # D-062 gave the batch door this branch and left this one appending a
        # duplicate beside the untiered record it was meant to classify, so a
        # stream following the server's own hint ("re-file each untiered defect
        # with tier=LIVE or tier=LATENT") through the tool every stream's prose
        # names made the ledger strictly worse and moved no gate. Both doors now
        # call ONE derivation of the rule — see `retier_matching_untiered` for
        # the driven before/after and for why identity excludes the description.
        #
        # Inside the transaction because it reads and mutates the same records
        # the append would touch: deciding outside would re-open the
        # read-then-write window this lock exists to close, and two concurrent
        # filers could each conclude "no match" and append two duplicates.
        retiered_id = retier_matching_untiered(
            defects,
            source=source,
            # The CANONICAL spelling, matching what both doors persist. Passing
            # the caller's raw alias would miss a record filed as MISPLACED
            # when the re-filing says ARCHITECTURAL_PLACEMENT, which is the same
            # type under two live spellings (D-018).
            type=canonical_type,
            file=file_path,
            symbol=symbol,
            tier=tier,
            reproduction_attempted=reproduction_attempted,
            defect_class=defect_class,
            cycle=defect["cycle"],
        )
        if retiered_id is not None:
            defect_id = retiered_id
        else:
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

    # D-077 \u2014 a re-tier is a FILING and still belongs in the human log. Losing
    # the entry entirely would be a regression against today, where at least
    # the duplicate this defect reports appeared there. The heading says which
    # of the two happened; the `Class` row is omitted on the re-tier branch
    # because the record KEEPS the class it was filed with when it already had
    # one (see `retier_matching_untiered`), so quoting the re-filing's class
    # here could contradict defects.json one line later.
    mirror_rows = [
        ("Type", canonical_type),
        # CT-001 \u2014 the human mirror carries the evidence axis too. A lead
        # reading forge-log.md to decide what still blocks the run needs
        # LIVE vs LATENT there, not only in defects.json.
        ("Tier", tier),
        ("Reproduction attempted", reproduction_attempted if tier == "LATENT" else ""),
    ]
    if retiered_id is None:
        mirror_rows.append(("Class", defect_class))
    mirror_rows.extend([
        ("Description", description),
        ("Spec ref", spec_ref),
        ("Symbol", symbol),
        ("File", file_path),
    ])
    _ledger_mirror(
        fdir,
        (
            f"Cycle {defect['cycle']} \u2014 {source}: {defect_id}"
            + (f" re-tiered {tier}" if retiered_id is not None else "")
        ),
        mirror_rows,
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
        # FR-051 / D-077 \u2014 reported under the SAME two key names and the same
        # two types the batch door already returns (`retiered` a count,
        # `retiered_ids` a list), so a lead or a report reading either door's
        # result handles one shape. On this door the count can only be 0 or 1;
        # a bool would have been the natural spelling here and is exactly the
        # per-door divergence that keeps costing this package defects.
        "retiered": 1 if retiered_id is not None else 0,
        "retiered_ids": [retiered_id] if retiered_id is not None else [],
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

    # D-150 + D-146. This coverage count carried the NARROWEST copy of the
    # requirement-ID grammar in the tree — `US-\d+|FR-\d+|NFR-\d+`, three
    # families — so a spec's AC-, OT-, GI-, CT- and ST- rows were never
    # counted as requirements to be covered at all, and the traceability gaps
    # this function reports were computed over a third of the spec. Both reads
    # now go through the guarded primitive and the one canonical pattern; an
    # unreadable spec degrades to "no ids from the spec", which falls through
    # to the requirements-derived fallback below rather than raising.
    spec_req_ids: list[str] = []
    spec_source: Path | None = None
    if spec_path:
        spath = root / spec_path if not Path(spec_path).is_absolute() else Path(spec_path)
        if spath.exists():
            spec_source = spath
    elif (fdir / "spec.md").exists():
        spec_source = fdir / "spec.md"
    if spec_source is not None:
        spec_text, spec_problem = read_text_file(spec_source)
        if spec_problem is None:
            spec_req_ids = list(dict.fromkeys(REQUIREMENT_ID_RE.findall(spec_text)))

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
