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
divergence is auditable rather than silent. The one reader is
``foundry_mcp/tools/foundry_state.py#current_cycle``, which states that
contract; this module holds no second copy of it (fallout D-012).

CONCURRENCY (FR-020 / AC-025)
-----------------------------
Every ledger write goes through ``ledger_transaction``, which holds both a
process-local ``threading.RLock`` (INSPECT's 4+ parallel streams are concurrent
tool calls inside ONE MCP server process) and an ``fcntl.flock`` (separate
server processes on the same repo). Ids come from ``allocate_record_id``, which
is max-suffix+1 rather than the positional ``len+1`` that made two simultaneous
filings collide. Both are exported: the second positional site lives in
``orchestration/fix_gate.py#foundry_sync_defects`` and must call these rather
re-derive them.
"""

from __future__ import annotations

import fcntl
import json
import re
import subprocess
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
    NON_COMMENT,
    OBSERVATION_CLASSES,
    SECURITY_PROPERTY_CLAIM,
    TEMPER_CANDIDATE,
    TIER_HARDENING,
    TIER_UNKNOWN,
    canonical_defect_type,
    defect_tier,
    is_security_property_text,
    never_demote_claim_class,
    never_demote_class,
    observation_class,
    reproduction_attempted_problem,
)
from foundry_mcp.schemas.vocab import REQUIREMENT_ID_RE
from foundry_mcp.tools.foundry_state import (
    ARCHIVE_DIR,
    ARCHIVE_SCHEMA_VERSION,
    current_cycle,
    document_refusal,
    get_run_dir,
    read_document,
    read_text_file,
    set_active_run,
)
from foundry_mcp.tools.artifacts import (
    _ARTIFACT_LOCK,
    _ARTIFACT_TX,
    _TX_LOCK_SUFFIX,
    _document_problem,
    _load_json,
    _read_document,
)
from foundry_mcp.tools.display import (
    FOUNDRY_SEP,
    _BCYAN,
    _BGREEN,
    _BWHITE,
    _DIM,
    _RESET,
    foundry_hammer,
)

# fallout D-034 — THE PALETTE IS ``display.py``'s, AND ONLY THE FIVE THIS
# MODULE ACTUALLY PRINTS ARE BOUND. Nine ANSI codes were re-declared here, a
# third copy of the same nine; ``display.py`` owns them and composes the bold
# variants there from the same base codes, so the two sets were identical right
# up until one of them was edited. Four of the nine — ``_BOLD``, ``_CYAN``,
# ``_GREEN``, ``_WHITE`` — existed ONLY to build ``_BCYAN`` / ``_BGREEN`` /
# ``_BWHITE``, and composing the bold variants HERE is what made the third copy
# look load-bearing. They are composed at their one home and imported already
# composed, so nothing in this module names a base code again.


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
# The split mirrors ``tools/artifacts.py``'s BY CONVENTION rather than by
# import — same refusal shape — and the two GUARDS stay two. Holmes `helper-1`
# records the policy difference as deliberate: THIS module fails CLOSED on a
# corrupt document (``LedgerShapeError``; D-096 / D-127) while the leaf fails
# OPEN with ``{}``.
#
# fallout D-011 — THE TOLERANT READ IS IMPORTED, NOT RE-DEFINED. This block
# used to carry a second body of ``_read_document`` / ``_document_problem`` /
# ``_load_json``, excused by "folding them would have to pick one failure
# direction". That reason does not survive reading: the failure direction lives
# in ``_locked_document`` and ``ledger_transaction``, which RAISE
# ``LedgerShapeError`` on a problem the tolerant read merely NAMES — so the
# read layer was never what differed, and two byte-identical bodies were being
# kept apart by a sentence about the layer above them. ``tools/artifacts.py``
# is the one home (GI-033) and this module imports from it; the leaf imports
# nothing here, so the import closes no cycle.
#
# WHAT STAYS, AND WHY IT IS NOT THE SAME FUNCTION AS THE LEAF'S. Three of the
# four names WERE one rule and are now imported. The fourth never was one rule,
# and as of D-061 it no longer shares a name with the leaf's either:
#
#   ``_read_document``   — the tolerant core: (data, named problem). IMPORTED.
#   ``_document_problem``— the problem alone. IMPORTED.
#   ``_load_json``       — total. {} for missing / unreadable / malformed.
#                          NEVER raises. IMPORTED.
#   ``_named_artifact_guard``
#                        — DEFINED HERE, and called ``_artifact_guard`` until
#                          D-061. The leaf's ``_artifact_guard`` takes a run dir
#                          alone and scans the WHOLE run; this one takes
#                          ``*names`` and guards only the artifacts the calling
#                          tool actually touches, because a corrupt roll-up must
#                          not block a defect filing that never opens it — which
#                          is what the name now says out loud. It also runs the
#                          ledger-container rung (``_LEDGER_KEYS`` /
#                          ``ledger_shape_problem``, D-096), which is LEDGER
#                          knowledge and so may not move into a leaf — the leaf
#                          says as much itself: a leaf that knows what a ledger
#                          is has stopped being one.
#
# D-061 — THE RENAME IS THE CLOSURE, AND THE OTHER TWO EXITS WERE BOTH SHUT.
# One name over two contracts reads to every name-keyed sweep as one rule
# duplicated, so for three cycles the finding could only be ACCOUNTED for:
# ``tests/orchestration/test_module_boundaries.py``'s ``_KNOWN_DUPLICATION``
# row and ``tests/test_artifacts.py``'s ``_SECOND_READ_LAYER`` row both existed
# to say "not that one", and an inventory that only ever grows is an inventory
# nobody closes. Deleting either function breaks the other's call sites on
# ARITY, and folding this one's ledger rung into the leaf is precisely what
# GI-033 forbids — so the third exit, a name that states the narrower job, is
# the only one that leaves the package with a single definition of
# ``_artifact_guard`` and no row for either table to carry.
#
# Tolerance ALONE would have turned D-095 into D-096: a corrupt defects.json
# reads as an empty one, and the next write then replaces the file and reports
# success. The guard is the half that stops the write and names the file, and
# neither half is sufficient without the other.
# ---------------------------------------------------------------------------


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
    ``_named_artifact_guard`` can name a broken ledger BEFORE a tool starts work,
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


def _named_artifact_guard(fdir: Path, *names: str) -> dict | None:
    """Named refusal when one of the NAMED artifacts is unreadable.

    The house refusal shape: ``error`` names the offending FILES and what is
    wrong with each, ``hint`` names the action. Scoped to the artifacts the
    calling tool actually reads or writes — a corrupt roll-up must not block a
    defect filing that never opens it.

    THE NAME IS THE DIFFERENCE FROM THE LEAF'S (D-061). ``artifacts.py`` has an
    ``_artifact_guard`` that takes a run dir alone and scans the WHOLE run; the
    two were name-alikes for three cycles and every sweep keyed on names read
    that as one rule copied twice. They are two rules: this one guards a NAMED
    subset, and it runs the ledger-container rung below (``_LEDGER_KEYS`` /
    ``ledger_shape_problem``, D-096), which is ledger knowledge GI-033 keeps
    out of a leaf. Both differences are why neither could be deleted for the
    other, and the arity is why the deletion would not even have type-checked.
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
    ``_named_artifact_guard`` and ``LedgerShapeError``, which carries it so a refusal
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
# `orchestration/gates.py#_synthesize_clean_prove_verdicts` — and only that one
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

# fallout D-010 — ONE LOCK DOMAIN FOR THE PACKAGE, IMPORTED FROM THE LEAF.
#
# This module used to declare ``_ARTIFACT_LOCK`` / ``_ARTIFACT_TX`` of its own and
# spell the sidecar name as a bare ``".lock"`` literal. A second domain over
# the same documents is not a second implementation of a convenience; it is a
# second answer to "who may write this file now", and verdicts.json has one
# writer in each. The two excluded each other on disk only because two
# independently typed spellings of one filename happened to agree, and the two
# in-flight maps were different objects, so a cross-domain nesting on one
# document on one thread blocked on a flock that thread already held.
#
# Binding the leaf's ``_ARTIFACT_LOCK`` / ``_ARTIFACT_TX`` / ``_TX_LOCK_SUFFIX``
# makes the agreement structural: one RLock orders the threads, one in-flight
# map keyed on ``str(path)`` — the same key both primitives already used — lets
# a nested pair compose into a single write, and the sidecar name is derived
# rather than retyped.
#
# What the RLock and the flock each cover is unchanged: INSPECT's parallel
# streams are concurrent tool calls inside ONE MCP server process, so an flock
# alone would not serialize them (it is advisory PER PROCESS and a second
# acquisition from the same process succeeds immediately); the RLock covers
# threads, the flock covers a second server process on the same repo. And the
# re-entrancy is still D-099's: a nested entry on a path this thread already
# holds yields the SAME document and defers the write to the outermost exit,
# because an flock belongs to the open file description and this module opens a
# fresh fd on every entry.

_RECORD_ID_RE = re.compile(r"\A([A-Za-z]+)-(\d+)\Z")


class LedgerRefusal(RuntimeError):
    """A house refusal DISCOVERED INSIDE a locked transaction, carried in-band.

    The house rule is that a tool returns ``{error, hint}`` and never raises
    across the MCP boundary. Some refusals cannot be decided before the lock is
    open: they are questions about the LEDGER, and the answer arrives several
    frames below the entry point, inside a transaction that must abort without
    writing. This is the carrier for those — ``.refusal`` is the dict the door
    returns, and ``@ledger_refusals`` is what turns the raise into that return
    for every path through a door, present and future.

    fallout D-157 — WHY THIS IS A BASE CLASS AND NOT A SECOND ``except`` NAME.
    ``LedgerShapeError`` was the only such refusal and the decorator named it
    directly, so a second one had to be remembered in the ``except`` clause by
    whoever added it. That is the arrangement this package keeps paying for
    (see ``validate_defect_filing``: "Every check those two doors were each
    trusted to remember has eventually diverged"), and the decorator's own
    docstring already promises the opposite — "every path through the
    function, present and future, converts". A base class makes the promise
    structural: a third in-transaction refusal joins by construction.

    ``message`` exists so a subclass whose ``str(exc)`` is load-bearing keeps
    it. Without it every subclass would stringify as its refusal's ``error``,
    which would silently rewrite what a traceback and a ``pytest.raises``
    match on.
    """

    def __init__(self, refusal: dict, message: str | None = None) -> None:
        super().__init__(
            message if message is not None else str(refusal.get("error", ""))
        )
        self.refusal = refusal


class LedgerShapeError(LedgerRefusal):
    """A run artifact cannot be read as the ledger a writer needs it to be.

    Raised inside the locked primitive rather than coerced away, because the
    coercion IS D-096: replacing a non-list ``defects`` container with ``[]``
    discarded every record the file held, re-minted ``D-001`` and reported
    success. It fails CLOSED — nothing is written — rather than open.

    CARRIES ITS OWN REFUSAL (D-127). The claim that "no production path reaches
    this raise" was false: it was reached by both of ``orchestration/fix_gate.py``'s
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
        # ``problem`` stays this exception's ``str()`` — it is what a traceback
        # and every existing reader show — while the refusal it carries is
        # built by ``artifact_refusal`` so it is word-for-word what the
        # pre-flight guard would have said about the same file.
        super().__init__(artifact_refusal([problem]), problem)
        self.problem = problem


def ledger_refusals(fn):
    """Return a tool entry point that answers ``LedgerRefusal`` in-band.

    The house rule is that a tool never raises across the MCP boundary: it
    returns ``{error, hint}``. A ledger whose container shape is wrong is
    discovered inside the locked primitive, several frames below the entry
    point, and D-127 is what happens when each entry point is trusted to
    remember a pre-flight check for it — the ones in this module remembered,
    the ones in ``orchestration/fix_gate.py`` did not, and the difference was
    invisible until it was driven.

    Wrapping is the binding that cannot be forgotten per-branch: every path
    through the function, present and future, converts. Which entry points
    need it is not a judgement either —
    ``test_every_ledger_writing_door_answers_in_band`` derives the set from
    ``server.py``'s ``_DISPATCH`` table crossed with this module's call graph,
    and fails on a door that reaches a transaction without it.

    fallout D-157 — IT CATCHES THE BASE, NOT ONE SUBCLASS. The shape error was
    the only in-transaction refusal when this was written, so the clause named
    it. `retier_matching_untiered`'s tier guard is the second: it can only ask
    its question with the ledger open, and it must abort the transaction rather
    than persist a record the doors would refuse. Naming each subclass here
    would make "every path converts" a promise somebody has to keep by hand;
    naming ``LedgerRefusal`` makes it the mechanism. Anything that is NOT a
    ledger refusal still propagates — the decorator is a translation, not a
    swallow, and a real bug returned as a tidy dict would be read as a refusal.
    """

    @wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except LedgerRefusal as exc:
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
    held = getattr(_ARTIFACT_TX, "docs", None)
    if held is None:
        held = _ARTIFACT_TX.docs = {}
    tx_key = str(path)
    if tx_key in held:
        # Already open on this thread — same document, one write at the end.
        yield held[tx_key]
        return

    lock_path = path.with_name(path.name + _TX_LOCK_SUFFIX)
    with _ARTIFACT_LOCK:
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
    held = getattr(_ARTIFACT_TX, "docs", None) or {}
    tx_key = str(path)
    if tx_key in held:
        held[tx_key].clear()
        held[tx_key].update(data)
        return
    lock_path = path.with_name(path.name + _TX_LOCK_SUFFIX)
    with _ARTIFACT_LOCK:
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
        each entry point and ``orchestration/fix_gate.py``'s two writers were
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
    ``orchestration/fix_gate.py``'s, which import it rather than re-deriving one.
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


#: The MCP argument names a single-door filing call carries, in the order
#: ``_finding_mapping`` takes them. Named ONCE (D-158) — see
#: ``filing_finding_mapping`` for why a second spelling is the whole defect
#: class.
_FILING_ARGUMENT_NAMES = (
    "description",
    "spec_ref",
    "target_kind",
    "symbol",
    "file_path",
    "tier",
    "defect_class",
    "reproduction_attempted",
)  # 8 items


def filing_finding_mapping(arguments: Mapping[str, object]) -> dict:
    """The finding mapping a single-door ``Foundry-Defect`` argument set files.

    WHY THIS IS PUBLIC AND WHY server.py CALLS IT (D-158, closing D-128 /
    D-146 / D-147's class)
    ---------------------------------------------------------------------
    Two rungs judge a single-door filing: ``foundry_add_defect`` here, and
    ``server.py``'s pre-dispatch rung, which must write the denylist tripwire
    for a filing refused against the advertised schema before any handler runs
    (see ``_audit_security_claim_on_refusal``). Both need the SAME mapping,
    because both hand it to ``validate_defect_filing`` and
    ``tripwire_finding``.

    They used to build it twice. This module built it from
    ``foundry_add_defect``'s parameters; ``server.py`` imported the private
    ``_finding_mapping`` and re-spelled the same eight names as
    ``args.get("...")`` string literals. Two spellings of "which arguments
    carry claim prose" is the arrangement that produced this defect class
    three cycles running — D-128 (the handler ladder returned early), D-146 /
    D-147 (the pre-dispatch rung and the one-field scan), D-158 (the scan
    over-reached into the negative-space field). A field added to one spelling
    and missed in the other is a rung reading a different filing than the rung
    beside it, which is the class by definition.

    So the spelling lives here, once, in ``_FILING_ARGUMENT_NAMES``, and the
    pre-dispatch rung calls this. Every value is coerced to ``str`` because
    the arguments arrive off the wire, where ``None`` and a stray number are
    both reachable and neither is prose.
    """
    args = arguments or {}
    values = {
        name: "" if args.get(name) is None else str(args.get(name))
        for name in _FILING_ARGUMENT_NAMES
    }
    return _finding_mapping(
        values["description"],
        values["spec_ref"],
        values["target_kind"],
        symbol=values["symbol"],
        file_path=values["file_path"],
        tier=values["tier"],
        defect_class=values["defect_class"],
        reproduction_attempted=values["reproduction_attempted"],
    )


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
    comment_subject_required: bool = True,
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
      * ``foundry_sync_defects``'s refusal loop in
        ``orchestration/fix_gate.py``
        — the same rung at the batch door, over every refused finding in the
        batch.
      * ``foundry_sync_defects``'s declared-comment branch in
        ``orchestration/fix_gate.py`` — audit only: the finding stays a defect
        either way, and the record captures that a denylist entry is what
        rescued it.
      * ``server.py``'s ``_audit_security_claim_on_refusal`` — the PRE-DISPATCH
        rung (D-146). `call_tool` validates arguments against the advertised
        schema before dispatch, so a filing refused there never reaches a
        handler; this fires the tripwire for a refused filing whose prose
        matches the security predicate, so a filer cannot switch the audit
        record off by also getting an unrelated field wrong.
      * ``retier_matching_untiered``'s tier guard (fallout D-157) — the one
        caller INSIDE a transaction, and the one that audits a claim the
        FILING does not carry. A re-tier keeps the stored record's own prose
        and spec_ref, so a denylisted claim already in the ledger could be
        classified into a non-blocking tier by an innocent re-filing that
        every rung above had already passed. It fires here and then raises, so
        the attempt is recorded on ``observations.json`` (a different path,
        written on its own clean exit) while ``defects.json`` is aborted
        unwritten. It takes the run dir as a defaulted argument, so the batch
        door audits once its call site passes one (concern C-075); the REFUSAL
        reaches both doors either way.

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
    denied = never_demote_claim_class(finding)

    # CT-017 / GI-027 / FR-017 — TWO QUESTIONS, ASKED SEPARATELY, AND
    # `comment_subject_required=False` IS THE TEMPER_CANDIDATE DOOR.
    #
    # The four never-demote entries are not one rule. Three of them read what
    # the finding CLAIMS — a security property, a spec-required behaviour, an
    # unresolvable cite — and they are absolute here as everywhere: vocab's
    # OBSERVATION_CLASSES block is explicit that the denylist "is UNCHANGED by
    # the fifth member and still outranks every one of them", and a probe idea
    # whose description makes a security-property claim is a DEFECT. That is
    # the question `never_demote_claim_class` above answers, for every caller
    # and every value of this flag.
    #
    # NON_COMMENT reads the finding's SUBJECT, and it is the one entry that
    # exists because recording an observation used to BE a demotion: the four
    # original classes are all "this comment no longer agrees with the code", so
    # a finding about code arriving in that ledger was a defect in hiding, and
    # the door had to fail closed on an undeclared subject. That is the
    # question the rung below asks, and it is asked ONLY of a caller that
    # requires a comment subject.
    #
    # A TEMPER_CANDIDATE is not a demotion of anything. It is "here is a
    # question nobody has asked yet" — nothing has been shown to be wrong, so
    # there is no defect for it to hide. Its subject is CODE by definition, and
    # casting 10's own report fixture seeds one with `target_kind: "code"`. Held
    # to the comment-subject rung the member would be unrecordable at the only
    # door that writes it, which would make OT-022 ("a TEMPER_CANDIDATE
    # observation is accepted") unsatisfiable and TEMPER's roster permanently
    # empty.
    #
    # THE SUBJECT RUNG IS REACHED ONLY ON AN EXPLICIT DECLARATION, never on an
    # inferred one — see the caller. Absence keeps today's behaviour to the
    # byte, which is D-069's ruling applied rather than re-argued: a default
    # that decides this question for a caller who said nothing is how the
    # fabricated declaration got in.
    #
    # HOW THIS USED TO BE SPELLED, AND WHY IT IS NOT (lead ruling, GRIND cycle
    # 4). The line above asked `never_demote_class` — the FULL dispatcher — and
    # a `denied == NON_COMMENT and not comment_subject_required` branch then
    # took the subject entry back out. That is the claim-vs-subject split
    # stated in this function's own voice, and D-078's rung in
    # `validate_defect_filing` stated the same ruling a second time, one
    # `!= NON_COMMENT` along. One ruling in two voices is the class this
    # package keeps paying for, so the split now lives in
    # `vocab.NEVER_DEMOTE_CLAIM_CLASSES` (DERIVED by subtraction from
    # `NEVER_DEMOTE_CLASSES`, so a fifth CLAIM entry joins both sites by
    # construction) and both sites ask the dispatcher narrowed to it. The lift
    # is GONE rather than repointed: with the claim question answered above,
    # an undeclared or non-comment subject is exactly what the rung below is
    # for, and the two spellings produced identical answers on every reachable
    # case — a claim always outranks the subject entry, so a NON_COMMENT
    # answer from the full dispatcher already MEANT "no claim matched".
    if (
        denied is None
        and comment_subject_required
        and not _subject_is_declared_comment(finding)
    ):
        # An undeclared subject cannot be SHOWN to be a comment, and "anything
        # non-comment" can never be an observation. Reported under the existing
        # NON_COMMENT entry rather than inventing a class name.
        denied = NON_COMMENT
    if denied is None:
        return None

    target_kind = finding.get("target_kind")
    if denied != NON_COMMENT:
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
        # fallout D-119 / GI-004 — THE TIER THE REFUSED FILING ATTEMPTED.
        #
        # GI-004's violation is stated OVER the attempted tier — "filing a
        # security claim or a spec-required behaviour failure as HARDENING or
        # LATENT" — so which of the two was attempted is part of the fact this
        # record exists to hold. Without it two materially different violations
        # were recorded identically: DRIVEN, the same never-demote description
        # filed once as LATENT and once as HARDENING was correctly refused both
        # times and wrote two records whose field set was exactly
        # ['cycle', 'denylist_class', 'description', 'detail', 'file',
        # 'fired_at', 'source', 'spec_ref', 'symbol'] — no tier key, and no
        # field anywhere holding either string. The refusal named the attempted
        # tier and the durable artifact did not, so `observations.json.tripwire`
        # could not answer the question GI-004 is written over.
        #
        # `""` WHEN THE FINDING DECLARES NONE, and that is honest rather than a
        # default: `foundry_add_observation` is a caller too, and an observation
        # attempts no tier at all. An empty string says "no tier was attempted";
        # it is not a tier this writer invented on the filing's behalf.
        "tier": finding.get("tier", ""),
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
            # The human mirror carries the attempted tier for the same reason
            # the record does, and beside the class so a lead reading
            # forge-log.md sees which demotion was refused rather than only
            # that one was. `_ledger_mirror` prints a row only for a truthy
            # value, so an observation's tripwire renders exactly as it did.
            ("Attempted tier", tripwire["tier"]),
            ("Description", tripwire["description"]),
            ("Spec ref", tripwire["spec_ref"]),
            ("Symbol", tripwire["symbol"]),
            ("File", tripwire["file"]),
        ],
    )
    return tripwire


# D-147 / D-158 — THE FILING FIELDS THE SECURITY PREDICATE MAY NOT READ.
#
# THE QUESTION THIS PARTITION ANSWERS. The predicate asks whether a filing
# CLAIMS a security property is broken. Every key a filing carries is
# therefore one of four things, and only the fourth is a claim:
#
#   vocabulary      a closed-vocabulary token the doors already validate;
#   locator         where the finding points;
#   escalation key  which sibling bucket it recurs in;
#   negative space  what the filer searched for and did NOT find;
#   claim prose     everything else — what the filing asserts.
#
# The first four are named below, member by member with the driven reason for
# each. The fifth is the complement, and it is a COMPLEMENT rather than an
# allowlist on purpose: an allowlist would have to name a claim key before a
# stream invents one, which is the race D-147 already lost once.
#
# D-147 — WHY THE COMPLEMENT IS WIDE. The gate used to read `description` and
# nothing else, so a LATENT filing whose security claim rode in ANY other key
# was accepted. Driven through `server.call_tool('Foundry-Sync', ...)` in the
# filing shape `agents/coverage-diff.md` documents — description '', the
# sentence in a `failure` key, tier LATENT — the filing was ACCEPTED (+1
# Added, persisted with tier LATENT) and `observations.json.tripwire` grew by
# 0. The batch door hands the caller's finding dict straight through, so every
# key a stream invents is a channel, and the shipped stream prose invents
# plenty: `failure`, `source_entry`, `expected_destination` (coverage-diff),
# `fix_hint` (flow-tracer), `spec_text_cited` (assayer), `evidence`, `page`,
# `element` (sight), `recommendation` (research-auditor).
#
# D-158 — AND WHY IT IS NOT WIDER STILL. Widening it to "every prose value"
# swept in `reproduction_attempted`, the one field whose documented job is to
# say what was searched for and NOT found — so the denylist began refusing the
# filings the spec requires it to ACCEPT. Driven on both real doors at
# 148b3ae: tier LATENT, spec_ref NFR-002, class report-renderer-gap,
# description "The report renderer omits the per-cycle minutes column.",
# reproduction_attempted "grepped both roots for a security section and found
# 0 sites" → `{ok: False, denylist_class: SECURITY_PROPERTY_CLAIM, field:
# 'description'}` at `foundry_add_defect` and a whole-batch refusal at
# `foundry_sync_defects`; the byte-identical filing whose statement read
# "...for a minutes column..." was accepted. Reproduced on a second subject:
# "drove the CLI with no credentials configured; 0 prompts appeared" refused,
# "with no config present" accepted. Two consequences, both shipped: the
# refusal named `description` while the description was innocent, and its hint
# reads "Do NOT re-word the filing to get past this refusal", so a stream that
# followed the documented LATENT protocol had no path to file at all.
#
# The members, and the driven reason each is here:
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
#                        exactly (a door refusing its own documented example).
#                        What fails if this line moves is the test below, cited
#                        on ONE line because a symbol broken across comment
#                        lines resolves to nothing (concern C-048):
#   `tests/test_protocol_prose.py#test_every_documented_latent_example_survives_the_filing_door`
#   negative space       reproduction_attempted — the field is a REPORT OF AN
#                        ABSENCE, not an assertion. FR-005 verbatim scopes the
#                        predicate to the description ("Server refuses LATENT
#                        only when the description matches the
#                        security-property regex"), AC-007 clause 2 and OT-005
#                        clause 2 require a LATENT filing with a spec_ref and
#                        a non-security description to be ACCEPTED, and CT-003
#                        says spec_ref alone never refuses. A statement that a
#                        search for a security property found nothing is the
#                        evidence CT-001 DEMANDS of a LATENT filing; refusing
#                        it makes the tier's own protocol unfileable whenever
#                        the subject is security-adjacent — which is the
#                        subject A-AUTO-005 most needs filed.
#
# WHAT IS DELIBERATELY LEFT OPEN, so nobody closes it by accident. A filer
# could put a bare claim in `reproduction_attempted` with an empty
# description. That is not smuggling past a rung — it is writing an assertion
# into a field labelled "what I drove and what it found", producing a backlog
# row whose description is empty. The alternative — a fifth LATENT rung
# demanding claim prose — is D-101's REVERSED shape exactly (FR-005's "only"
# is the whole word, and the `file_path` rung added in GRIND cycle 5 was
# reversed in cycle 6 for breaking it), so it is not added here.
#
# Over-matching costs the filer a refusal naming what to do (drive it, file
# LIVE); under-matching is A-AUTO-005's unacceptable failure mode. D-158 is
# the proof that over-matching is not free either: it cost the filer every
# path at once.
_FILING_VOCABULARY_KEYS = frozenset({
    "tier",
    "type",
    "source",
    "target_kind",
    "status",
})  # 5 items

_FILING_LOCATOR_KEYS = frozenset({
    "file",
    "symbol",
    "spec_ref",
    # CT-019 / FR-025 — the two provenance fields are LOCATORS: each names
    # another record by id and asserts nothing whatever about the code. Out of
    # the claim partition for the same driven reason `spec_ref` is out of it,
    # and the omission would bite in BOTH directions at the batch door, which
    # hands its caller's whole finding dict to `security_scan_text`: a `D-NNN`
    # can never match the security predicate, so nothing would be refused that
    # should not be — but the LIVE prose floor asks whether the filing carries
    # ANY prose at all, and a LIVE filing whose only text was `fallout_of:
    # "D-012"` would pass a floor whose whole subject is that the record states
    # something about what is wrong.
    "fallout_of",
    "supersedes",
})  # 5 items

_FILING_ESCALATION_KEYS = frozenset({
    "class",
})  # 1 item

_FILING_NEGATIVE_SPACE_KEYS = frozenset({
    "reproduction_attempted",
})  # 1 item

#: The four partitions above, unioned. PUBLIC because it is the field set the
#: whole class of defect turns on: every rung that consults the security
#: predicate reads THIS, and none of them re-types a member. Derived from the
#: four sets rather than re-listed, so a member cannot be added to one and
#: missed here.
NON_CLAIM_FILING_KEYS = frozenset(
    _FILING_VOCABULARY_KEYS
    | _FILING_LOCATOR_KEYS
    | _FILING_ESCALATION_KEYS
    | _FILING_NEGATIVE_SPACE_KEYS
)  # 12 items

#: fallout D-135 / D-152 — THE TWO PARTITIONS THAT ARE PROPERTIES OF A KEY AT A
#: TIER, NOT OF THE KEY.
#:
#: The union above was read as one flat set by every rung, and two of its four
#: partitions do not describe a key at all — they describe a key AS ONE TIER
#: USES IT, and the tier they describe is not HARDENING:
#:
#:   negative space   `reproduction_attempted` is out because it is "a REPORT
#:                    OF AN ABSENCE, not an assertion" — which is what the
#:                    field means for LATENT ("I looked for this and did not
#:                    find it"). For HARDENING the SAME field is the tier's
#:                    designated home for the CLAIM: `_HARDENING_REPRODUCTION_
#:                    HINT` tells the filer to "name the probe you ran and the
#:                    wrong result you observed", which is LIVE's evidence
#:                    standard, stated positively. So the one field HARDENING's
#:                    own wire contract points the claim into was the one field
#:                    the denylist never read.
#:   escalation       `class` is out because `agents/assayer.md`'s documented
#:                    LATENT example carries `no-auth-guard-on-destructive-
#:                    endpoints` and its LIVE sibling carries the same class,
#:                    so scanning it would refuse a shape the surface ships and
#:                    the filer's only escape would be renaming the escalation
#:                    key mid-run (CT-002). No surface documents a HARDENING
#:                    example at all, and no legitimate one can share a class
#:                    with a security finding — such a finding may never be
#:                    HARDENING in the first place — so at this tier the
#:                    exclusion protects nothing and the refusal's own remedy
#:                    ("file it as LIVE") keeps the class where it is.
#:
#: DRIVEN at both doors before the fix (D-135): tier HARDENING, description
#: "off-spec probe of the cookie issuer", reproduction_attempted "drove the
#: cookie issuer 40x: the session cookie is issued with no signature, so an
#: attacker can forge one" was ACCEPTED as D-001, open and non-blocking, with
#: `observations.json.tripwire` EMPTY; the control — the identical claim in
#: `description` — was refused naming SECURITY_PROPERTY_CLAIM and fired the
#: tripwire. D-152 drove the second window: `class=
#: "the-auth-token-signature-is-never-verified"` with innocent prose, likewise
#: accepted and unaudited. GI-004's violation column is "filing a security
#: claim or a spec-required behaviour failure as HARDENING or LATENT" and
#: vocab's own DEFECT_TIERS block promises "a tier is never a route around the
#: never-weaken guarantee"; via these two fields it was one.
#:
#: DERIVED BY SUBTRACTION from the two partitions rather than re-listed, so a
#: member joining either one joins this scoping by construction.
_CLAIM_BEARING_AT_HARDENING = frozenset(
    _FILING_ESCALATION_KEYS | _FILING_NEGATIVE_SPACE_KEYS
)  # 2 items


def non_claim_filing_keys(finding: Mapping[str, object]) -> frozenset[str]:
    """The keys the claim scan skips for a filing declaring this tier.

    ONE derivation, asked by `security_scan_text` and therefore by every rung
    that consults it — the security rung, the HARDENING never-demote rung (via
    `tripwire_finding`), the LIVE prose floor, the tripwire record's own
    re-derivation, and `server.py`'s pre-dispatch rung. A rung that decided
    the field set for itself is the security-denylist-tripwire-is-rung-
    dependent class (D-128, D-146/D-147, D-158, and now D-135), so the tier
    scoping lands HERE and nowhere else.

    Reads the tier the caller DECLARED, exactly as the denylist rung does and
    for the same reason: the rung that would validate the tier is a rung this
    must outrank. Any value the wire can carry arrives here, so the comparison
    is `==` against a string constant and never a set membership — an
    unhashable declared tier (a list) would make a bare `in` RAISE, which is
    the one thing a validator whose whole contract is "returns the house
    refusal, never raises" may not do.
    """
    if finding.get("tier") == TIER_HARDENING:
        return NON_CLAIM_FILING_KEYS - _CLAIM_BEARING_AT_HARDENING
    return NON_CLAIM_FILING_KEYS


def _collect_prose(
    value: object,
    into: list[str],
    skip: frozenset[str] = NON_CLAIM_FILING_KEYS,
    depth: int = 0,
) -> None:
    """Append every CLAIM-prose string reachable from ``value``.

    "Claim prose" is the complement of ``skip``, which `security_scan_text`
    derives once per filing through `non_claim_filing_keys` — see the
    ``NON_CLAIM_FILING_KEYS`` block for the four partitions and the driven
    reason for each, and `_CLAIM_BEARING_AT_HARDENING` for the two that are
    scoped by the declared tier.

    ``skip`` is THREADED rather than read from the module constant at each
    level, because the set is decided once for the whole filing and a level
    that consulted the constant instead would scan a nested mapping under a
    different rule than the top one — the same finding read two ways inside
    one call, which is the class this parameter exists to close.

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
            if isinstance(key, str) and key in skip:
                continue
            _collect_prose(item, into, skip, depth + 1)
        return
    if isinstance(value, (list, tuple)):
        for item in value:
            _collect_prose(item, into, skip, depth + 1)
        return
    if isinstance(value, (set, frozenset)):
        # Sorted by their text, because `security_scan_text` promises a
        # deterministic string for a given filing and a set's iteration order
        # is not. A set never arrives from decoded JSON; it arrives from a
        # hand-built mapping (a test, a caller assembling a finding in
        # Python), and that caller must get the same scan text twice — the
        # tripwire record quotes this string back.
        for item in sorted(value, key=repr):
            _collect_prose(item, into, skip, depth + 1)


def security_scan_text(finding: Mapping[str, object]) -> str:
    """Every CLAIM-prose value a filing carries, joined for the predicate.

    "Claim prose" is precisely the complement of `non_claim_filing_keys`: not
    the closed vocabularies and not the locators at any tier; and — at every
    tier but HARDENING — not the escalation `class` and not
    `reproduction_attempted`, whose documented job there is to report what a
    search did NOT find (D-158). Read the `NON_CLAIM_FILING_KEYS` block for
    the driven reason each partition is out, and `_CLAIM_BEARING_AT_HARDENING`
    for why two of the four come back in at the one tier whose own wire
    contract points a claim into them (fallout D-135 / D-152).

    THE SET IS DERIVED ONCE PER FILING, HERE, and threaded down through
    `_collect_prose`. Deciding it at each nesting level would let one call
    read the top of a finding under one rule and a nested mapping under
    another; deciding it in each RUNG is the class this function exists to
    close.

    This is what `is_security_property_text` is asked about (D-147), and it is
    ONE derivation because three rungs need the same answer: the LATENT
    denylist rung in `validate_defect_filing`, `tripwire_finding` below —
    which is how the audit record comes to name the class the refusal named —
    and `server.py`'s pre-dispatch rung, which reaches both of those through
    `filing_finding_mapping`. A rung that derived its own answer is the
    security-denylist-tripwire-is-rung-dependent class (D-128, D-146/D-147,
    D-158), so there is one derivation and no second reading of a filing.

    `description` leads, then the remaining keys in sorted order — so the
    joined text is deterministic for a given filing (a dict's insertion order
    is the caller's, and the tripwire record quotes this string back).
    """
    skip = non_claim_filing_keys(finding)
    parts: list[str] = []
    if "description" in finding:
        _collect_prose(finding["description"], parts, skip)
    _collect_prose(
        {k: v for k, v in sorted(finding.items(), key=lambda kv: str(kv[0]))
         if k != "description"},
        parts,
        skip,
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
    `orchestration/fix_gate.py` are the two call sites.

    Returns the finding unchanged when it carries no prose at all: nothing
    matched the security predicate in that case, and the tripwire is firing
    for some other denylist entry whose own reading must not be disturbed.
    """
    scanned = security_scan_text(finding)
    if not scanned:
        return dict(finding)
    return {**finding, "description": scanned}


#: AC-055 / GI-028 / CT-012 — the named refusal a HARDENING filing carrying any
#: `spec_ref` gets. Spelled ONCE, and carried in the `error` STRING rather than
#: in a key of its own, because `_merged_door_refusal` hoists three named keys
#: and no more: a token in a fourth key would be the one thing a filing that
#: failed two rungs loses on its way back to the caller.
TIER_NOT_ALLOWED = "TIER_NOT_ALLOWED"

#: GI-004 / GI-014 — the tiers a security-property claim may never be PARKED in.
#: DERIVED from the vocabulary rather than listed, so a fourth member joins this
#: rung by construction instead of by somebody remembering it: what the denylist
#: exists to stop is a claim filed where it holds no gate shut, and LIVE — the
#: one tier that does hold one shut — is therefore the exclusion. `BLOCKING_TIERS`
#: says the same thing one module over (`orchestration/gates.py`, LIVE plus the
#: unknown sentinel) and is NOT imported here: this module is the layer that one
#: imports from, and reading it back would close a cycle in the import graph.
NON_BLOCKING_TIERS = frozenset(DEFECT_TIERS - {"LIVE"})

#: The two reproduction hints, one per tier that demands the field. Same rung,
#: same refusal, different EVIDENCE: a LATENT filing owes a negative result, a
#: HARDENING filing owes the probe it drove and the wrong result it saw. One
#: hint serving both would tell half its readers to describe the wrong thing.
_LATENT_REPRODUCTION_HINT = (
    "A LATENT filing is a gap you REASONED about rather than "
    "reproduced, so the negative result is the whole evidence: "
    "say what you drove and what it found (e.g. 'AST sweep of "
    "both roots finds 0 sites'). If you did drive the failure, "
    "file it as LIVE with its reproduction instead."
)
_HARDENING_REPRODUCTION_HINT = (
    "A HARDENING filing is a probe you DROVE that failed on a path no "
    "requirement states, so it carries the same evidence a LIVE filing does: "
    "name the probe you ran and the wrong result you observed. A worry you did "
    "not drive is not a HARDENING record — record it as a TEMPER_CANDIDATE "
    "observation through Foundry-Observation instead."
)

#: FR-025 / CT-019 / ST-006 — the two OPTIONAL provenance fields a filing may
#: carry onto its record, named once so the doors, the record shape and casting
#: 3's `fallout_per_cycle` measurement read ONE spelling.
DEFECT_PROVENANCE_KEYS = ("fallout_of", "supersedes")


def _provenance_value(value: object) -> str | None:
    """One provenance field, normalised to a non-blank str or to None."""
    return value.strip() if isinstance(value, str) and value.strip() else None


def defect_provenance(finding: Mapping[str, object]) -> dict:
    """The two provenance keys, ALWAYS BOTH, for a new defect record.

    FR-025 / CT-019 — `fallout_of` names the D-NNN this finding is fallout of;
    `supersedes` names the open HARDENING record this filing promotes.

    BOTH KEYS ARE ALWAYS WRITTEN, `None` when the filing carried neither, and
    that is the whole contract rather than tidiness. `foundry_state.fallout_rows`
    reads the KEY's presence as "this record was measured" and its absence as
    "this record predates the field" — structurally unmeasured, never a measured
    zero — and a cycle holding one unmeasured record cannot contribute to
    FR-025's acceptance figure. A door that wrote the key only when the filer set
    it would make every post-change cycle read as unmeasured forever, which is
    the reader certifying nothing while looking like it certified something.

    Reads the mapping and nothing else, like `validate_defect_filing` beside it,
    so the batch door can build the same two keys from its caller's finding dict
    without a second spelling of what they are called or how they normalise.
    """
    return {key: _provenance_value(finding.get(key)) for key in DEFECT_PROVENANCE_KEYS}


def fallout_parent_problem(fallout_of: str | None, records: list) -> dict | None:
    """CT-019's one error: `fallout_of` naming an id the ledger does not hold.

    Returns the house refusal dict, or None when the field is absent or names a
    record that exists. Takes the RECORDS rather than a run dir because the
    decision belongs inside the caller's open transaction: deciding outside it
    would re-open the read-then-write window the ledger lock exists to close,
    and a parent filed by a concurrent door would read as unknown.

    CT-019 admits exactly ONE error on this contract, so there is no rung here
    for a `supersedes` naming an unknown or non-HARDENING id — see
    `close_superseded_record` for what happens instead and why. D-101 is the
    record of what inventing a rung a contract's errors column does not admit
    costs: the door refuses its own documented example and the stream's next
    move is to fabricate the field.
    """
    if not fallout_of:
        return None
    if any(d.get("id") == fallout_of for d in _dict_records(records)):
        return None
    return {
        "ok": False,
        "error": (
            f"Unknown fallout_of: {fallout_of!r} names no record in this run's "
            f"defect ledger."
        ),
        "hint": (
            "`fallout_of` cites the defect this finding is fallout OF — a "
            "D-NNN this run has already filed — so that measure-run can count "
            "fallout per cycle. Check the id against Foundry-Defects, or drop "
            "the field: a finding that is nobody's fallout carries it as null."
        ),
        "field": "fallout_of",
    }


def close_superseded_record(
    records: list,
    superseded_id: str | None,
    *,
    by_id: str,
    cycle: int,
) -> str | None:
    """ST-006 — close the cited open HARDENING record as SUPERSEDED, in place.

    Returns the id actually closed, or None when the field was absent or the
    cited record is not an open HARDENING one. Mutates ``records`` (the list a
    ``ledger_transaction`` is yielding) exactly as ``retier_matching_untiered``
    does, and for the same reason: the read and the write are one event.

    THE TIER IS NOT TOUCHED (OT-020 / GI-022). Promotion is a NEW filing that
    CITES the earlier record, never a rewrite of what a stream said it saw. So
    this sets `status`, and the record keeps the tier, the description and the
    reproduction it was filed with; the new record carries its own. There is no
    door in this package that re-tiers a record in place, and this is the
    function a reader looking for one arrives at.

    `"superseded"` is a THIRD status beside `"open"` and `"fixed"`, not either of
    them: every gate and census in the package counts `status == "open"`, so the
    closure stops it blocking; and `status == "fixed"` is what Foundry-Fix
    writes when a defect was repaired, which this was not.

    A cited id that is unknown, already closed, not HARDENING, or the id of the
    record this very filing is creating closes NOTHING and refuses nothing —
    CT-019's errors column admits one refusal and none of these is it. The
    caller reports the id it actually closed (`superseded` in the door's result,
    `None` here), so a filer who cited the wrong record sees that in the answer
    rather than being told the filing failed.
    """
    if not superseded_id:
        return None

    # fallout D-053 / ST-006 / GI-022 — THE RECORD THIS CALL IS CREATING IS NOT
    # A CANDIDATE FOR ITS OWN SUPERSESSION.
    #
    # Both doors call this AFTER the append, so `records` already holds the new
    # record and the scan below matched it whenever the filer cited the id the
    # call was about to mint. That is not an exotic input: on an EMPTY ledger
    # the mint is deterministic, so `supersedes="D-001"` on a run's first filing
    # names the record being filed. Driven at BOTH doors on an empty ledger —
    # `foundry_add_defect(tier="HARDENING", supersedes="D-001")` returned
    # `{defect_id: "D-001", superseded: "D-001", open_defects: 0}`, and
    # `foundry_sync_defects` with the same finding returned `{added: 1,
    # superseded_ids: ["D-001"], total_open: 0}`. Each persisted ONE record,
    # born `status: "superseded"` with `superseded_by` naming itself. It never
    # counted as open, never reached the F6 HARDENING backlog CT-012 promises
    # it, and the door reported the self-promotion as a success.
    #
    # ST-006 and GI-022 are both stated over TWO records — "a new filing on the
    # same path that CITES the HARDENING id", "promotion only by a NEW filing
    # that cites it" — so a record citing itself is not a transition this
    # function has a shape for, and the closure it produced was a record born
    # closed against the very filing that created it.
    #
    # BY ID, NOT BY OBJECT IDENTITY. The re-tier branch of the single door
    # reuses an EXISTING record object and passes that record's id as `by_id`,
    # so `d is <the appended dict>` would be False there while the self-closure
    # is exactly the same one. The id is what both branches share.
    #
    # A no-op rather than a refusal, exactly like the three abstentions below.
    # No refusal rung is being asked for here: CT-019's errors column admits one
    # error and this is not it, and D-101 is this package's record of what
    # inventing a rung a contract does not admit costs. The filer reads
    # `superseded: None` and learns the promotion did not land; the record it
    # filed stays OPEN, which is where a HARDENING finding with nothing behind
    # it belongs.
    if superseded_id == by_id:
        return None
    for d in _dict_records(records):
        if d.get("id") != superseded_id:
            continue
        if d.get("status") != "open" or defect_tier(d) != TIER_HARDENING:
            return None
        d["status"] = "superseded"
        d["superseded_by"] = by_id
        d["superseded_in_cycle"] = cycle
        return superseded_id
    return None


def validate_defect_filing(finding: Mapping[str, object]) -> dict | None:
    """The tier/class/LATENT checks both filing doors apply, decided in ONE place.

    Returns None when the filing may be persisted, otherwise the house refusal
    dict: ``{"ok": False, "error": ..., "hint": ..., "field": <"tier" | "class"
    | "reproduction_attempted" | "description" | "spec_ref">}`` plus, for the
    security refusal only, ``"denylist_class": SECURITY_PROPERTY_CLAIM``.

    Reads the mapping and nothing else — no ledger read, no run-dir resolution,
    no write — so the batch door can call it once per finding BEFORE it opens
    its transaction, and so a refusal costs nothing.

    WHY THIS IS A SHARED FUNCTION (CT-001 / CT-002 / CT-003 / AC-010)
    -----------------------------------------------------------------
    There are two filing doors and they live in different modules:
    ``foundry_add_defect`` here, ``foundry_sync_defects`` in
    ``orchestration/fix_gate.py``. Every check those two doors were each trusted
    to remember has eventually diverged — D-119 is the shipped instance (the
    two doors disagreed about which cycle a record belonged to, so identical
    findings filed through different doors produced different cycle runs and a
    systemic class escaped ST-002 escalation). A filing whose tier the single
    door demands and the batch door does not is that defect again, one field
    along, and it would be worse: the batch door is the one a whole INSPECT
    stream files through, so the gap would be the common path rather than the
    rare one.

    THE CHECK ORDER IS LOCKED, so that the two doors name the same field first
    for the same bad filing: the security denylist (over every NON-BLOCKING
    tier), then — for HARDENING only — the rest of the never-demote denylist
    read over the filing's PROSE (D-078), then tier, then class, then — for
    LIVE only — the prose floor (D-147), then — for HARDENING only — the
    `spec_ref` refusal, then — for LATENT and HARDENING —
    reproduction_attempted. There is no rung after those; see the D-101 block
    at the tail of this function for why a `file_path` rung was added in GRIND
    cycle 5 and reversed in cycle 6, and the D-147 block at the LIVE rung for
    why that one is scoped and shaped the way it is rather than as a
    `description`-key check.

    BOTH DENYLIST RUNGS LEAD for one reason (D-061): the audit tripwire may
    not be rung-dependent. A filing that asserts something it may never assert
    from a non-blocking tier is audited whatever ELSE it got wrong, so no
    filer switches the control off by also omitting `class`.

    WHAT THE HARDENING TIER ADDED, AND WHERE (FR-015 / FR-057 / FR-045 /
    GI-004 / GI-014 / GI-022 / GI-028 / CT-012 / AC-023 / AC-055)
    --------------------------------------------------------------------
    Three rungs, all of them placed INSIDE the locked order rather than beside
    it, because the order is the only reason the two doors name the same field
    first for the same bad filing:

      * the denylist rung widened from LATENT to `NON_BLOCKING_TIERS`. A
        security-property claim parked in a tier that holds no gate shut is the
        demotion the denylist exists to refuse, and which non-blocking tier it
        was parked in does not change that.
      * and, for HARDENING alone, the REST of that denylist — the claim
        entries `never_demote_class` ranks, read over the filing's prose
        (D-078). The rung above enforced one of the denylist's four entries;
        this one enforces the others for the tier whose definition they
        contradict, through `vocab.never_demote_claim_class`. See its own
        block for why `spec_ref` is neutralised and why the SUBJECT entry is
        no part of the question.
      * a `spec_ref` refusal reachable only from HARDENING, naming
        `TIER_NOT_ALLOWED`.
      * the reproduction rung widened to both tiers that owe evidence in that
        field.

    Nothing here decides whether HARDENING blocks a gate. `BLOCKING_TIERS` is
    LIVE plus the unknown sentinel, it lives in `orchestration/gates.py`, and it
    was not touched: the tier's gate semantics are entirely that it is absent
    from that tuple. And nothing here re-tiers a record — promotion is a NEW
    filing citing the earlier one through `supersedes` (see
    `close_superseded_record`), because a re-tier would rewrite what a stream
    said it saw.

    D-147 / D-158 — WHAT THE DENYLIST RUNG READS
    --------------------------------------------
    Every CLAIM-prose value the filing carries (`security_scan_text`) — not
    `description` alone, and not every value either.

    D-147: the batch door hands the caller's dict straight through, so a claim
    in any key a stream invents used to ride past this gate; the driven filing
    put it in `failure`, which is the key `agents/coverage-diff.md` documents.

    D-158: the widened reading then swept `reproduction_attempted`, the one
    field whose documented job is to say what was searched for and NOT found,
    so the rung began refusing the filings AC-007 clause 2 and OT-005 clause 2
    require it to ACCEPT — a LATENT filing about a security-adjacent subject
    became unfileable at both doors while its hint told the filer not to
    re-word it.

    The four excluded partitions (closed vocabularies, locators, the
    escalation `class`, and the negative-space `reproduction_attempted`) are
    named in `NON_CLAIM_FILING_KEYS`, with the driven reason for each.

    fallout D-135 / D-152 — AND WHY TWO OF THE FOUR COME BACK AT HARDENING.
    The exclusion of `reproduction_attempted` was keyed by FIELD while its
    whole justification was keyed by TIER: the field is negative space for
    LATENT, and for HARDENING it is the field the tier's own wire contract
    designates for the claim ("name the probe you ran and the wrong result you
    observed"). So the one field a HARDENING filing is TOLD to state its claim
    in was the one field the denylist never read, and a security-property
    claim placed there was accepted as a non-blocking record with an empty
    tripwire, while the byte-identical claim in `description` was refused.
    `class` was excluded on a documented LATENT example whose LIVE sibling
    shares the class; no surface documents a HARDENING example, and a finding
    that could legitimately share a class with a security finding may never be
    HARDENING at all. Both are now read AT HARDENING ONLY, through
    `non_claim_filing_keys` — one derivation, so this rung, the never-demote
    rung below, the LIVE prose floor, `tripwire_finding`'s re-derivation and
    `server.py`'s pre-dispatch rung all moved together rather than a fifth
    rung reading a fifth field set. D-158's LATENT shapes are untouched: the
    scoping fires on `tier == HARDENING` and on nothing else.

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
    ``never_demote_class`` — AND WHY THE HARDENING GATE DOES (CT-003 / D-078)
    --------------------------------------------------------------------------
    ``never_demote_class`` returns SPEC_REQUIRED_BEHAVIOUR_CLAIM for ANY
    finding carrying a non-empty ``spec_ref`` (see
    ``vocab.is_spec_required_behaviour_claim``). Routing the LATENT gate
    through it would refuse every LATENT filing that cites a requirement —
    which is the majority of them — and OT-005 requires the exact opposite: a
    LATENT filing citing NFR-002 with a scan-gap description is ACCEPTED. A
    spec_ref alone never refuses a LATENT filing. Only the security predicate
    does.

    NONE OF THAT IS TRUE OF HARDENING, and reading it as though it were is
    what left half the denylist unenforced at both doors (D-078). LATENT means
    "I looked for this stated failure and did not find one", so citing the
    requirement is the tier working; HARDENING means "I drove a probe of my
    own devising on a path NO requirement states", so citing one is the tier
    being contradicted. The clause that makes the LATENT argument bite —
    spec_ref alone — is neutralised at the HARDENING rung precisely so that
    the rung reads the CLAIM and not the citation, and the citation keeps its
    own named refusal (AC-055's ``TIER_NOT_ALLOWED``) below.

    fallout D-152 — SO THE TWO NON-BLOCKING TIERS ENFORCE DIFFERENT AMOUNTS OF
    THE DENYLIST, AND THAT IS A RULING RATHER THAN AN OVERSIGHT. Stated once,
    here, because an asymmetry nobody wrote down is read as a hole by the next
    stream that drives it — which is how it was filed. Entry by entry, at
    LATENT:

      SECURITY_PROPERTY_CLAIM       ENFORCED. FR-005 states it verbatim and
                                    A-AUTO-005 names it the mechanism.
      SPEC_REQUIRED_BEHAVIOUR_CLAIM NOT enforced, and cannot be: the predicate
                                    answers True for ANY non-empty
                                    ``spec_ref``, so enforcing it literally
                                    would refuse every LATENT filing that
                                    cites a requirement — which is most of
                                    them, and exactly what OT-005 and CT-003
                                    require ACCEPTED.
      UNRESOLVABLE_CITE             NOT enforced. FR-005 verbatim: "Server
      NON_COMMENT                   refuses LATENT ONLY when the description
                                    matches the security-property regex", and
                                    `only` is the whole word. D-101 is this
                                    package's record of what inventing a rung
                                    a Locked contract does not admit costs —
                                    a `file_path` rung added in GRIND cycle 5
                                    and reversed in cycle 6 for breaking this
                                    same word. Adding a second denylist entry
                                    at this tier is that reversal again, so it
                                    is refused here rather than shipped and
                                    reversed later.

    HARDENING is under no such sentence — no Locked requirement scopes ITS
    refusals to one predicate, and its own definition contradicts three of the
    four entries — so it asks `never_demote_claim_class`, the full dispatcher
    narrowed to the CLAIM entries, and enforces all of them.

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
    # D-147 / D-158: asked of `security_scan_text(finding)` — every CLAIM-prose
    # value the filing carries. Not `description` alone (D-147's driven filing
    # put the claim in `failure`), and not `reproduction_attempted` either
    # (D-158: that field reports what a search did NOT find, so scanning it
    # refused the protocol's own LATENT shapes). See `NON_CLAIM_FILING_KEYS`.
    # GI-004 / GI-014: HARDENING joined LATENT on this rung the moment it became
    # a tier. `NON_BLOCKING_TIERS` is the derivation, not a second list — vocab's
    # own DEFECT_TIERS block is explicit that "a security-property claim filed as
    # HARDENING is refused and the audit tripwire fires, exactly as it is when
    # filed as LATENT", and a tier is never a route around the never-weaken
    # guarantee. The rung reads the DECLARED tier, before the tier rung has
    # judged it, for the reason the docstring gives: the rung that would validate
    # it is the very rung this must outrank.
    # `isinstance` first: this rung reads the tier the caller DECLARED, which is
    # any value the wire can carry — and an unhashable one (a list) makes a bare
    # `in` against a frozenset RAISE, which is the one thing a validator whose
    # whole contract is "returns the house refusal, never raises" may not do.
    # The equality this widened from could not be reached that way.
    if (
        isinstance(tier, str)
        and tier in NON_BLOCKING_TIERS
        and is_security_property_text(security_scan_text(finding))
    ):
        return {
            "ok": False,
            "error": (
                f"Refused: {SECURITY_PROPERTY_CLAIM} — a security-property "
                f"claim may never be filed as {tier}."
            ),
            "hint": (
                "A claim that a security property is broken is never a gap "
                "reasoned about, and it is never off-spec: drive it, and file "
                "what you observed as LIVE. Do NOT re-word the claim to get "
                "past this refusal — "
                "every field that CARRIES a claim is read, not just the "
                "description (D-147); the same predicate guards the "
                "never-demote denylist, and an audit tripwire has already "
                "recorded this attempt. Fixing the other fields will not get "
                "you past it either: this rung is reached before them. What "
                "you say you SEARCHED for is never what is refused, so a "
                "reproduction_attempted statement naming a security term you "
                "looked for and did not find is fine as it stands (D-158)."
            ),
            "field": "description",
            "denylist_class": SECURITY_PROPERTY_CLAIM,
        }

    # AC-023 / GI-004 / OT-019 / FR-045 (D-078) — THE OTHER HALF OF THE
    # NEVER-DEMOTE DENYLIST, ENFORCED FOR HARDENING.
    #
    # The rung above asks ONE denylist question — is this a security-property
    # claim — while `never_demote_class` asks four. So of the denylist's
    # entries only SECURITY_PROPERTY_CLAIM was enforced at either filing door,
    # and a filing asserting that a STATED requirement's behaviour is absent
    # landed in the non-blocking tier unopposed. Driven at BOTH doors with
    # `spec_ref=""` and "AC-022 requires Foundry-Gate('done') to pass with open
    # HARDENING defects and the gate refuses; the required behaviour is
    # absent": `never_demote_class` answered SPEC_REQUIRED_BEHAVIOUR_CLAIM,
    # `foundry_add_defect` returned D-001, `foundry_sync_defects` returned
    # `{"ok": True, "added": 1}`, and `observations.json` tripwire stayed `[]`.
    # GI-004's violation column is "filing a security claim OR A SPEC-REQUIRED
    # BEHAVIOUR FAILURE as HARDENING or LATENT" — half of it was unguarded, so
    # the tier the release added for OFF-spec findings accepted on-spec ones.
    #
    # HARDENING ONLY, and the LATENT section of this docstring is why: routing
    # LATENT through this dispatcher would refuse every LATENT filing that
    # cites a requirement, which OT-005 requires ACCEPTED. For HARDENING there
    # is no such tension — the tier is DEFINED as a driven failure "on a path
    # NO requirement states", so a spec-required-behaviour claim filed here
    # contradicts the tier's own definition rather than sitting awkwardly
    # inside it.
    #
    # TWO NARROWINGS, EACH FORCED BY A LOCKED REQUIREMENT RATHER THAN CHOSEN:
    #
    #   `spec_ref` NEUTRALISED — `is_spec_required_behaviour_claim` answers
    #       True for ANY non-empty `spec_ref`, so asking the dispatcher the raw
    #       finding would refuse every spec_ref-carrying HARDENING filing HERE
    #       and the `spec_ref` rung below would become unreachable. AC-055
    #       requires that filing refused "naming the tier rule"
    #       (`TIER_NOT_ALLOWED`, field `spec_ref`), so the LOCATOR is
    #       neutralised and this rung reads the PROSE — which is exactly the
    #       half GI-028 does not already cover, and the half D-078 drove.
    #   THE SUBJECT ENTRY EXCLUDED — the four entries are not one rule: three
    #       read what the finding CLAIMS, and NON_COMMENT reads its SUBJECT,
    #       existing only because recording an OBSERVATION used to be a
    #       demotion. A defect's `target_kind` says what it is about, and this
    #       door's own contract is that "any other value, or none, means the
    #       finding is not demotable and is filed as a defect" — so refusing
    #       HARDENING for `target_kind="code"` would make OT-018 ("a HARDENING
    #       defect is accepted with a reproduction") unsatisfiable for the
    #       DEFAULT shape of every production-code filing.
    #
    #       THE SPLIT IS THE VOCABULARY'S, NOT THIS RUNG'S (lead ruling, GRIND
    #       cycle 4). This rung and `record_denylist_tripwire`'s subject rung
    #       are the two places that turn on it, and each used to spell the
    #       exclusion for itself — a `!= NON_COMMENT` here, a
    #       `== NON_COMMENT` lift there. One ruling in two voices is the class
    #       this package keeps paying for, so both now ask
    #       `never_demote_claim_class`, the dispatcher narrowed to
    #       `NEVER_DEMOTE_CLAIM_CLASSES` — which is DERIVED by subtraction
    #       from `NEVER_DEMOTE_CLASSES` rather than re-listed, so the "by
    #       construction and never by memory" this comment used to merely
    #       promise is now the mechanism.
    #
    # ASKED OF `tripwire_finding(finding)` — the exact shape the door hands
    # `record_denylist_tripwire` on the way out. So the class this refusal
    # names and the class the audit record RE-DERIVES are equal by
    # construction rather than by two readings agreeing, which is D-083's
    # property (and D-147's repair) held one entry along.
    if tier == TIER_HARDENING:
        denied = never_demote_claim_class({**tripwire_finding(finding), "spec_ref": ""})
        if denied is not None:
            return {
                "ok": False,
                "error": (
                    f"Refused: {denied} — a finding on the never-demote "
                    f"denylist may never be filed as {TIER_HARDENING}."
                ),
                "hint": (
                    "HARDENING is the tier for a driven failure on a path NO "
                    "requirement states. A claim that a STATED requirement's "
                    "behaviour is absent or wrong is on-spec by definition, "
                    "and this tier holds no gate shut — so filing it here is "
                    "the demotion the denylist exists to refuse. Drive it and "
                    "file what you observed as LIVE, citing the requirement "
                    "in spec_ref and putting the reproduction in the "
                    "description. Do NOT re-word the claim to get past this "
                    "refusal: every field that CARRIES a claim is read, not "
                    "just the description (D-147), and an audit tripwire has "
                    "already recorded this attempt. What you say you SEARCHED "
                    "for is never what is refused, so a "
                    "reproduction_attempted statement naming a requirement "
                    "you looked for is fine as it stands (D-158)."
                ),
                "field": "description",
                "denylist_class": denied,
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
                "— name what you drove in reproduction_attempted. HARDENING "
                "means you drove a probe of your own devising and it failed on "
                "a path NO requirement states — same reproduction standard as "
                "LIVE, and it carries no spec_ref. The tier is not a severity: "
                "all three are defects and all three get fixed."
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

    if tier == "LIVE":
        return None

    # AC-055 / GI-028 / CT-012 — HARDENING CARRYING ANY `spec_ref` IS REFUSED.
    #
    # Not down-ranked, refused, and refused here rather than at the vocabulary:
    # vocab's DEFECT_TIERS block says "that is a door rule and lives at the
    # doors; what lives here is the member". The discriminator is mechanical
    # because the claim is: a spec reference IS the statement that a requirement
    # is at stake, and a record making that statement from inside the
    # non-blocking tier is precisely the downgrade this axis exists to prevent.
    #
    # ANY VALUE AT ALL, not merely a well-formed requirement id. GI-028's
    # violation column is "a HARDENING record carrying a `spec_ref` for
    # context", and a reference offered as context is exactly the one that would
    # survive a well-formedness check. A blank string and an absent key are the
    # same thing — no reference was made — and every other value, of every type
    # the batch door's caller dict can hold, is one.
    #
    # AHEAD OF THE REPRODUCTION RUNG deliberately. A HARDENING filing carrying a
    # spec_ref does not belong in this tier at all, so naming the missing
    # reproduction first would send the filer to complete evidence for a record
    # that must be re-filed as LIVE either way.
    if tier == TIER_HARDENING:
        spec_ref = finding.get("spec_ref")
        carries_spec_ref = (
            bool(spec_ref.strip()) if isinstance(spec_ref, str) else spec_ref is not None
        )
        if carries_spec_ref:
            return {
                "ok": False,
                "error": (
                    f"Refused: {TIER_NOT_ALLOWED} — a {TIER_HARDENING} filing "
                    f"may carry no spec_ref, and this one carries "
                    f"{spec_ref!r}."
                ),
                "hint": (
                    "HARDENING is the tier for a driven failure on a path NO "
                    "requirement states, and a spec_ref is the statement that "
                    "a requirement IS at stake — the two cannot both be true "
                    "of one filing. If the requirement really is unmet, this "
                    "is a LIVE defect: re-file it with tier=LIVE, keeping the "
                    "spec_ref and putting the reproduction in the description. "
                    "If it is not, drop the spec_ref; a HARDENING record cites "
                    "no requirement, not even for context."
                ),
                "field": "spec_ref",
            }

    # FR-004 / FR-029 / GI-014 — the negative-result rung, now reached by the
    # two tiers that owe evidence in this field. LATENT owes what it DROVE and
    # did not find; HARDENING owes the probe it drove and the wrong result it
    # saw ("Same evidence standard as LIVE — a reproduction, not a worry", per
    # vocab's DEFECT_TIERS block). One rung, one refusal, two hints — because
    # the two tiers owe different evidence and a single hint would tell half
    # its readers to write the wrong thing.
    problem = reproduction_attempted_problem(finding.get("reproduction_attempted"))
    if problem is not None:
        return {
            "ok": False,
            "error": f"Invalid reproduction_attempted: {problem}",
            "hint": (
                _HARDENING_REPRODUCTION_HINT
                if tier == TIER_HARDENING
                else _LATENT_REPRODUCTION_HINT
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
    # fallout D-101 / FR-025 / CT-019 / ST-006 — the re-filing's provenance.
    # DEFAULTED so the batch door in `orchestration/fix_gate.py` keeps
    # compiling while it is repointed, and so the KEYS land on the record
    # either way: what makes a cycle measurable is the key's PRESENCE, not its
    # value, so a caller that passes neither still leaves a measured record
    # behind. See the write below.
    fallout_of: str | None = None,
    supersedes: str | None = None,
    # fallout D-157 / GI-004 — the run dir the denylist tripwire is written to,
    # and the ONLY reason this pure list mutator takes a path. DEFAULTED for
    # exactly the reason the two provenance keys above are: the batch door in
    # `orchestration/fix_gate.py` is casting 2's file and is repointed
    # separately, and a default keeps it compiling meanwhile. The ENFORCEMENT
    # half needs nothing from it — the refusal below is raised whether or not a
    # run dir was supplied — so a door that has not been repointed still cannot
    # park a denylisted claim in a non-blocking tier; what it cannot do yet is
    # AUDIT the attempt. See the block at the guard for why the two halves
    # separate cleanly here and did not at D-061.
    fdir: Path | None = None,
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

    fallout D-101 / FR-025 / CT-019 / AC-045 — THE PROVENANCE KEYS LAND HERE
    TOO, AND THE KEY'S PRESENCE IS THE MEASUREMENT
    -----------------------------------------------------------------------
    ``fallout_of`` is accepted and ledger-validated at BOTH filing doors —
    ``fallout_parent_problem`` runs inside the lock and refuses an unknown
    parent — and was then silently discarded whenever the filing took this
    exit. Driven at both doors with ``fallout_of="D-001"`` against a matching
    untiered record: both reported success, the persisted record carried no
    ``fallout_of`` KEY AT ALL, and neither result mentioned the loss.

    The absent key is not a measured zero. ``foundry_state.fallout_rows``
    counts key-PRESENCE as "measured" and returns verdict ``not_measurable``
    for any cycle pair holding such a record, so a re-tiering filing was
    exactly the shape that made every post-change cycle read as unmeasured
    forever — which is FR-025 / AC-045's acceptance figure. That is why both
    keys are written UNCONDITIONALLY below, on every record this classifies,
    exactly as the new-record literal in ``foundry_add_defect`` seeds them.

    FILLED ONLY WHEN THE RECORD DOES NOT ALREADY CARRY ONE, which is the
    ``class`` rule one field along and for the same reason: provenance the
    earlier filing declared is what a later reader has been citing, and
    overwriting it here would move it mid-run. A record that already carries
    the key keeps its value; a record that does not gets the re-filing's, or
    ``None``.

    ``type`` shadows the builtin inside this frame. The name is the record's
    own field name and is fixed by the cross-module contract casting 3 calls
    against; nothing in this body needs ``type()``.

    fallout D-157 / GI-004 / GI-028 / AC-055 / OT-019 — THE TIER RULES ARE
    ASKED OF THE RECORD THIS CLASSIFIES, AND NOT ONLY OF THE FILING
    ----------------------------------------------------------------------
    Both doors run ``validate_defect_filing`` over the INCOMING mapping before
    they open a transaction, and by contract that function "reads the mapping
    and nothing else". So every rung that judges CLAIM PROSE or a CITATION was
    asked about the filing and never about the stored record the filing
    classifies — and a re-tier keeps the record's own ``description`` and
    ``spec_ref`` exactly where the earlier filing put them. The tier arrives;
    the prose that tier forbids stays.

    DRIVEN twice, at both doors, before this guard:

      * open untiered D-001 carrying ``spec_ref="FR-014"``, re-filed on the
        same identity with ``tier=HARDENING`` and no spec_ref of its own —
        ``retiered: 1``, and the persisted record read
        ``{"id": "D-001", "tier": "HARDENING", "spec_ref": "FR-014",
        "status": "open"}``. GI-028's violation column is "a HARDENING record
        carrying a ``spec_ref`` for context", and this exit minted one.
      * open untiered D-001 whose OWN description read "the auth token is
        never verified so an attacker reaches the handler", re-filed with an
        innocent description and ``tier=HARDENING`` — ``retiered: 1``, tier
        HARDENING persisted with the security claim intact, and
        ``observations.json`` tripwire length 0. A security-property claim
        parked in a tier that holds no gate shut, with the audit control
        silent, is the one outcome the never-demote denylist is absolute
        about.

    The direct doors were airtight for both shapes; only this exit was open.
    So the guard asks THE ONE VALIDATOR — never a rung re-spelled here — about
    the record the mutation would leave behind, projected through
    ``_finding_mapping``, the same shape both doors and ``server.py``'s
    pre-dispatch rung judge. A rung added to that validator tomorrow guards
    this exit the same day, which is the whole reason the check order lives in
    one function.

    WHY THE PROJECTION AND NOT THE RAW MERGED RECORD. ``security_scan_text``
    partitions the keys a FILING carries; a persisted record also carries
    ``id``, ``created_at`` and ``retiered_in_cycle``, which are in no
    partition and would be scanned as claim prose. That costs nothing on the
    denylist rungs and would make the LIVE prose floor unfireable by accident
    rather than by rule. Nothing is lost by projecting: ``new_defect_record``
    takes named parameters and splats no caller dict, so ``description`` is a
    persisted record's only prose field.

    WHY IT IS SCOPED TO ``NON_BLOCKING_TIERS``, which is a rule and not a
    convenience. That constant's own block says what the denylist exists to
    stop: "a claim filed where it holds no gate shut, and LIVE — the one tier
    that does hold one shut — is therefore the exclusion". A re-tier to LIVE
    demotes nothing, so there is nothing here for this guard to prevent. It is
    also what keeps the LIVE prose floor unreachable BY CONSTRUCTION rather
    than by the accident above, so classifying a description-less record as
    LIVE behaves exactly as it did.

    IT RAISES, and that is the only shape that reaches both doors. This
    function is called from inside each door's transaction, and the batch door
    consumes its result as ``if retier_id is not None`` — so a refusal returned
    as a dict would be counted there as a successful re-tier. A raise instead
    travels the seam both doors already have: ``_locked_document`` writes
    NOTHING when an exception leaves the block, so the whole batch is refused
    atomically (which is what ``test_sync_refusal_is_all_or_nothing`` is
    about), and ``@ledger_refusals`` turns the raise into the house
    ``{error, hint}`` refusal at each door without either call site changing.

    THE TRIPWIRE IS FIRED HERE, NOT DEFERRED (D-061). "The audit tripwire may
    not be rung-dependent", and a control a filer can switch off by ALSO
    getting something else wrong is not a control. It goes through the one
    exported writer, on the same ``tripwire_finding`` shape both doors pass, so
    the class the refusal names and the class the audit record re-derives are
    equal by construction. It writes ``observations.json`` — a different path
    from the transaction this is called inside, whose lock is re-entrant per
    path — so the audit record persists on its own clean exit and the raise
    then aborts only ``defects.json``: the ATTEMPT is recorded and nothing is
    filed, which is exactly what the two direct doors already do.
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

        # fallout D-157 / GI-004 / GI-028 / AC-055 / OT-019 — THE RECORD THIS
        # WOULD CLASSIFY IS PUT THROUGH THE ONE VALIDATOR, UNDER THE TIER THE
        # RE-FILING DECLARES. See this function's docstring for both driven
        # before/afters, for why the projection rather than the raw record, for
        # why NON_BLOCKING_TIERS is the scope, and for why this raises.
        #
        # The class and reproduction values are the ones the mutation below
        # would leave: `class` is the record's when it declared one and the
        # re-filing's otherwise (the same `or` the fill uses, one expression
        # up), and `reproduction_attempted` is the re-filing's, since this arm
        # is by definition not LIVE.
        if tier in NON_BLOCKING_TIERS:
            classified = _finding_mapping(
                str(d.get("description") or ""),
                str(d.get("spec_ref") or ""),
                str(d.get("target_kind") or ""),
                symbol=str(d.get("symbol") or ""),
                file_path=str(d.get("file") or ""),
                tier=tier,
                defect_class=str(d.get("class") or "").strip() or defect_class,
                reproduction_attempted=reproduction_attempted,
            )
            refusal = validate_defect_filing(classified)
            if refusal is not None:
                if refusal.get("denylist_class") and fdir is not None:
                    record_denylist_tripwire(
                        fdir,
                        tripwire_finding(classified),
                        cycle=cycle,
                        source=source,
                    )
                raise LedgerRefusal({
                    **refusal,
                    # The refusal the validator wrote is about a filing, and the
                    # filer's own filing passed it at the door — so it is said
                    # again here NAMING THE RECORD, or the filer reads "this one
                    # carries 'FR-014'" against a filing that carries no
                    # spec_ref and has nothing to act on. The validator's own
                    # sentences are carried through verbatim rather than
                    # re-worded, so both doors and both paths still say one
                    # thing about one rule.
                    "error": (
                        f"Refused: the open untiered record {record_id} may not "
                        f"be classified {tier}. {refusal.get('error', '')}"
                    ),
                    "hint": (
                        f"A re-tier writes the tier onto {record_id} and leaves "
                        f"its description and spec_ref exactly where the "
                        f"earlier filing put them, so classifying it would "
                        f"persist a {tier} record both doors refuse. The field "
                        f"named above is {record_id}'s, not your filing's. "
                        f"{refusal.get('hint', '')}"
                    ),
                    "retier_target": record_id,
                })

        d["tier"] = tier
        # GI-014: `!= "LIVE"`, not `== "LATENT"`. HARDENING owes the same
        # reproduction LATENT does — vocab's DEFECT_TIERS block calls it "the
        # whole reason the record is trusted" and the report's HARDENING backlog
        # renders the column — so keying on LATENT alone would classify a record
        # into the new tier and throw away the evidence that classified it. LIVE
        # is the one tier whose reproduction lives in the description.
        d["reproduction_attempted"] = (
            reproduction_attempted if tier != "LIVE" else None
        )
        if not str(d.get("class") or "").strip():
            d["class"] = defect_class
        # fallout D-101 — BOTH KEYS, ALWAYS, filled where the record declares
        # nothing. The condition is the `class` rule above one field along:
        # fill when the record carries no value, leave a value an earlier
        # filing declared exactly where that filing put it, because provenance
        # a later reader has been citing may not move mid-run.
        #
        # ABSENT AND NULL ARE BOTH "DECLARES NOTHING", and they have to be:
        # `scripts/migrate-archive.py` fills `fallout_of: null` as a schema-4
        # default, so an untiered record raised through the migration path
        # arrives here with the key present and empty. Keying only on absence
        # (a bare `setdefault`) would leave that record permanently unable to
        # receive the provenance its re-filing declared — the same value lost
        # one shape along. Writing the key on BOTH paths is also what makes the
        # cycle measured either way: `fallout_rows` reads key-PRESENCE.
        #
        # `DEFECT_PROVENANCE_KEYS` and `defect_provenance` are the one spelling
        # of "the two provenance fields, normalised" — the same derivation the
        # new-record literal uses — so a third field joining the contract joins
        # this exit by construction rather than by somebody remembering it.
        refiled_provenance = defect_provenance(
            {"fallout_of": fallout_of, "supersedes": supersedes}
        )
        for key in DEFECT_PROVENANCE_KEYS:
            if not d.get(key):
                d[key] = refiled_provenance[key]
        d["retiered_in_cycle"] = cycle
        return record_id
    return None


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


#: FR-055 / AC-052 — THE ONE MEANING OF `--no-ui` USED TO BE DEFINED HERE, and
#: it now lives at `foundry_mcp/schemas/vocab.py#NO_UI_MEANING` (concern C-059
#: row 8, GI-033). The sentence is a closed-vocabulary value read ACROSS layers
#: — `orchestration/width.py` and `orchestration/teams.py` both take it — and
#: GI-033 puts such a value in the vocabulary rather than in the largest
#: lifecycle module in the tree. This module defined a byte-identical twin,
#: which is what the duplicate-symbol guard is for; the definition and every
#: word of its FI-2 rationale moved intact, so read it there.


def _max_cycles_problem(value: object) -> dict | None:
    """The cap rung: a value this door will not honour is refused, not stored.

    CT-006 / D-225 — the accepted set must EQUAL the honoured set. `Foundry-Init`
    is reachable in-process as well as over the advertised schema, and D-225 is
    the record of what the gap costs on this exact parameter: a NEGATIVE cap
    passed the wire schema, was persisted, and then read as "no cap" by
    `_persisted_max_cycles`, so an operator who typed -1 ran unbounded and was
    told nothing. The schema gained a `minimum`; this is the same rung at the
    handler, which is the door the resume path reaches through.

    `bool` is refused as a non-integer on purpose: `True` IS an `int` in Python
    and would persist a cap of 1, halting the run at the first GRIND door for a
    caller who passed a flag where a ceiling was asked for.

    D-061's sibling, D-067 — THE OMITTED FLAG NEVER REACHES THIS RUNG. "No cap
    was passed" is spelled `None` at the parameter and the caller skips this
    check for it; every value that DOES arrive here is one an operator typed,
    including 0. That separation is what lets the hints below promise an exit
    the resume branch actually takes: while 0 doubled as "absent", "Pass 0 for
    unbounded" was advice the door then declined to honour.
    """
    if isinstance(value, bool) or not isinstance(value, int):
        return {
            "error": (
                f"Invalid max_cycles: {value!r} is not an integer. The GRIND "
                f"cycle ceiling is a whole number of cycles."
            ),
            "hint": (
                "Pass an integer at least 0 — 0 means unbounded, and on a "
                "resume it is honoured as that rather than read as an absent "
                "flag. OMIT max_cycles to leave a resumed run's cap alone."
            ),
        }
    if value < 0:
        return {
            "error": (
                f"Invalid max_cycles: {value} is below zero. A negative "
                f"ceiling is not a smaller cap, it is no cap at all."
            ),
            "hint": (
                "Pass 0 for unbounded, or the number of GRIND cycles this run "
                "may open before it halts."
            ),
        }
    return None


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
    # D-067 — `None` is "no cap was passed", 0 is a cap of 0. They were one
    # value and the resume branch could not tell them apart; see the Args entry.
    max_cycles: int | None = None,
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
            castings/manifest.json. 0 means unbounded. The Foundry-Phase call
            that would exceed it SUCCEEDS, sets phase HALTED and generates the
            report; HALTED is a named terminal state and is not DONE.

            THE DEFAULT IS ``None``, NOT 0, AND THE TWO ARE DIFFERENT ANSWERS
            (D-067). ``None`` is "this call passed no cap"; 0 is "this call
            passed a cap of 0", which is the documented spelling of unbounded.
            A new run cannot tell the two apart and does not need to — it has no
            prior ceiling for an omitted flag to leave alone, so both persist 0.
            A RESUME can and must: ``None`` leaves the persisted cap exactly as
            it is, and 0 rewrites it to unbounded.

            CT-006 / ST-002 / FR-020 / AC-027 — ON A RESUME IT REWRITES THE CAP.
            ``Foundry-Init(resume=…, max_cycles=N)`` writes N to
            ``state.json.max_cycles`` inside the same locked document as the
            refreshed provenance, so an operator can lower a ceiling onto a
            running run and the next GRIND door halts it with reason
            ``cap_reached`` when N is below the cycle it is opening. This branch
            used to do ``document.update(version_fields)`` and nothing else,
            dropping a parameter of its own signature on the floor.

            D-067 — WHY THAT IS A FIX AND NOT A PREFERENCE. This branch read
            ``if max_cycles:`` against a default of 0, so a resume carrying an
            explicit 0 was indistinguishable from one carrying nothing and both
            left the cap alone. The shared cap rung's own hint said "Pass 0 for
            unbounded" while this door declined to honour it, and the result
            echoed the untouched cap back — so an operator who typed
            ``--max-cycles 0`` to lift a ceiling was told, accurately and
            uselessly, that the ceiling was still 5. A refusal hint may not name
            an exit the check never reads, and the reading was never the broken
            part: the WIRE was, because 0 was carrying two meanings.

            So the distinction is made where the two meanings arrive rather
            than guessed at further down. ``server.py``'s dispatch must send
            ``args.get("max_cycles")`` — the absent key as ``None``, not filled
            in as 0 — and the schema must not default it; the omitted-flag case
            then never reaches this branch's write at all. ``commands/resume.md``
            already passes the argument only when ``--max-cycles N`` was
            invoked, which is the shape this default was written to.
            A non-integer, or a value below 0, is refused at the door on BOTH
            branches (``_max_cycles_problem``); ``None`` reaches neither rung
            nor write, because it is the absence of a value rather than a bad
            one.
        no_ui: FR-055 / AC-052 — and this is the sentence every other surface
            quotes, spelled once at
            ``foundry_mcp/schemas/vocab.py#NO_UI_MEANING`` and reproduced here
            verbatim because this door is where the flag is received:

            `--no-ui` declares that this run has no browsable UI, so the SIGHT
            browser audit is not part of it.

            It does NOT suppress banners, and it is not a refusal. The flag meant
            all three at once (survey/surface.md FI-2) — setup-foundry.sh's
            "Skip browser audit (SIGHT)", README.md's "Suppress orchestrator
            banners", and a SIGHT check treating it as a hard block — which is
            three answers to one question an operator has to pick between
            without being told there is a choice. Persisted to BOTH state.json
            and castings/manifest.json, exactly as ``temper`` and ``nyquist``
            are; the surfaces that quote the sentence and the gate that acts on
            it are other castings' files, and they quote this one.

            fallout D-118 — ON A RESUME IT IS RAISED, and so are ``temper`` and
            ``nyquist``. All three used to be dropped on that branch. A resume
            carrying one writes it to BOTH stores — state.json and
            castings/manifest.json — because ``no_ui``'s only reader loads the
            manifest while the other two are read from state.json. There is no
            LOWERING door: these three are CLI switches with no off spelling,
            so a false is "not typed" rather than "turn it off", and a bare
            resume leaves a run's mode exactly as it found it. See the block at
            the resume branch's own write for the driven reproduction.
        ticket: Ticket ID (e.g., "AQUA-123") for name generation.
        description: Short description for name generation.
        url: Target URL for the SIGHT audit. Persisted to
            ``castings/manifest.json`` as ``target_url`` (the store of
            record the inspect gate readers load —
            ``orchestration/streams.py#_check_streams_complete`` and
            ``orchestration/teams.py#_check_sight_required``), mirroring the
            bash init write at foundry.sh:176.
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

    # CT-006 / D-225 — the cap rung, ahead of BOTH branches. One parameter, one
    # check: a value this door will not honour is refused rather than written,
    # and there is no reading under which a new run may store a cap the resume
    # door would refuse.
    #
    # D-067 — `None` is skipped because it is not a value. An omitted flag has
    # nothing to validate and nothing to write; every value that reaches the
    # rung is one a caller typed, 0 included, which is what makes the rung's
    # hints honest about 0 rather than describing an exit it declines to take.
    if max_cycles is not None:
        if (cap_problem := _max_cycles_problem(max_cycles)) is not None:
            return cap_problem

    # --- Resume mode ---
    if resume:
        run_dir = archive / resume
        state_path = run_dir / "state.json"
        if not state_path.exists():
            return {"error": f"Run '{resume}' not found in {ARCHIVE_DIR}/"}
        # D-095: a corrupt state.json used to raise here, and resuming is
        # exactly when an operator is trying to recover from whatever corrupted
        # it. Naming the file is the whole value of the call at that moment.
        if (corrupt := _named_artifact_guard(run_dir, "state.json")):
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
            # CT-006 / ST-002 / FR-020 — THE CAP THIS CALL CARRIES, WRITTEN.
            #
            # `survey/data.md:336`: this branch did `document.update(...)` and
            # nothing else, so `max_cycles` — a parameter of this very function,
            # advertised on the schema and forwarded by the dispatch lambda —
            # was accepted and dropped, and an operator lowering a ceiling onto
            # a running run was told the resume succeeded while the ceiling
            # stayed where it was.
            #
            # In THIS transaction rather than a second one: the provenance
            # refresh and the cap are one operator action, and a cap written
            # under its own lock could interleave with a concurrent phase
            # transition reading the old value.
            #
            # D-067 — `is not None`, not truthiness. `if max_cycles:` read an
            # explicit 0 as an absent flag, so the one value that MEANS
            # unbounded was the one value this door refused to write, and the
            # shared rung went on advertising "Pass 0 for unbounded" over it.
            # The absence is now spelled at the parameter (`None`), which is
            # the only place it can be spelled without losing a cap of 0 — a
            # bare resume still leaves the ceiling alone, and that property is
            # now carried by the default rather than by a coincidence of
            # falsiness. Both arms are pinned in tests/test_foundry_init.py.
            if max_cycles is not None:
                document["max_cycles"] = max_cycles

            # fallout D-118 / FR-055 / GI-015 — THE THREE RUN-MODE SWITCHES,
            # RAISED.
            #
            # `server.py` forwards `temper`, `nyquist`, `no_ui` and
            # `max_cycles` to this handler; this branch wrote `version_fields`
            # and `max_cycles` and returned, so three parameters of its own
            # signature were accepted and dropped without a word. DRIVEN on a
            # run whose persisted state had all four false/zero:
            # `foundry_init(resume="s", temper=True, nyquist=True,
            # no_ui=True, max_cycles=9)` left max_cycles 9 as asked while
            # temper, nyquist and no_ui were ALL STILL FALSE, and the result
            # carried no error and no note of the loss. Each dropped flag is
            # read at a phase decision — `state["nyquist"]` and
            # `state["temper"]` by the transition table in
            # `orchestration/transitions.py`, `manifest.no_ui` by the
            # SIGHT-requirement reader `foundry_state#...sight...` — so a
            # resume asking for a TEMPER pass, or declaring that this run has
            # no browsable UI, was acknowledged and had no effect.
            #
            # A RESUME RAISES A SWITCH AND THERE IS NO LOWERING DOOR. That is
            # the ruling, stated here so it is not read later as the same
            # oversight being fixed: every surface an operator meets these on
            # is a CLI switch — `--temper`, `--nyquist`, `--no-ui` — and none
            # of the three has a spelling for turning one off. `False` and
            # "not typed" are therefore the same answer on every surface, and
            # a branch that wrote the false as an instruction would turn a
            # bare `/foundry:resume` into a silent downgrade of an opted-in
            # TEMPER run (GI-007: a capability is never removed to satisfy an
            # item). `max_cycles` is treated differently because it DOES have
            # a lowering spelling — `--max-cycles 0` means unbounded — which is
            # why D-067 had to spell its absence as `None` at the parameter
            # and why these three do not.
            #
            # Whichever of the three were raised is reported back, so a caller
            # reads what was persisted rather than what it asked for.
            raised_flags = [
                name
                for name, value in (
                    ("temper", temper),
                    ("nyquist", nyquist),
                    ("no_ui", no_ui),
                )
                if value
            ]
            for name in raised_flags:
                document[name] = True

            # fallout FR-054 / D-052 — AND NOTHING STAMPS
            # `archive_schema_version` HERE, deliberately. The marker records
            # the generation a run was CREATED under, and a legacy archive
            # stamped on resume would stop being distinguishable from a new run
            # — which is the whole discrimination FR-054 rests on, destroyed by
            # the door an operator reaches for to recover a legacy run.
            # `scripts/migrate-archive.py` is what raises an old archive's
            # marker, on purpose and with a migration behind it.
            state = dict(document)

        # fallout D-118 — THE SECOND STORE, because `no_ui` is not read from
        # the first one. `temper` and `nyquist` are read out of state.json by
        # the transition table; `no_ui` is read out of `castings/manifest.json`
        # by the SIGHT-requirement reader, and `foundry_init` writes all three
        # to BOTH documents on a new run for exactly that reason. A resume that
        # raised `no_ui` in state.json alone would still have no effect on the
        # gate D-118 names, which is the defect with a write in front of it.
        #
        # A SEPARATE TRANSACTION, and after the first one closes, because the
        # two documents have separate locks and nesting them would introduce a
        # lock ORDER this module does not otherwise have — a second door
        # acquiring them the other way round is a deadlock nobody would find.
        # The two writes are not atomic with each other and do not need to be:
        # each store has its own single reader, and a raise landing in one and
        # not the other leaves that reader with the value it had.
        #
        # SKIPPED RATHER THAN REFUSED when the manifest is absent or
        # unreadable. Resume is the RECOVERY door: `_locked_document` fails
        # CLOSED on a corrupt document (Holmes helper-1), so reaching for it
        # unguarded would turn a resume that succeeds today into a refusal over
        # a file this branch never used to touch. `manifest_flags_raised` says
        # which store actually took the write.
        manifest_path = run_dir / "castings" / "manifest.json"
        manifest_flags_raised: list[str] = []
        if (
            raised_flags
            and manifest_path.exists()
            and _document_problem(manifest_path) is None
        ):
            with _locked_document(manifest_path) as manifest_document:
                for name in raised_flags:
                    manifest_document[name] = True
            manifest_flags_raised = list(raised_flags)

        return {
            "foundry_dir": str(run_dir),
            "run_name": resume,
            "resumed": True,
            "state": state,
            # AC-027 — the cap this run is NOW running under, echoed beside the
            # refreshed state exactly as the new-run result echoes its own, so a
            # caller reads what was persisted rather than what it asked for. A
            # resume that carried no cap reports the one the run already had.
            "max_cycles": state.get("max_cycles", 0),
            # fallout D-118 — the three run-mode switches AS PERSISTED, read
            # back out of the document rather than echoed from the arguments,
            # so a caller sees the run's actual mode and not its own request.
            # `raised` names what THIS call changed, which is what makes a
            # dropped parameter visible in the answer instead of silent.
            "temper": state.get("temper", False),
            "nyquist": state.get("nyquist", False),
            "no_ui": state.get("no_ui", False),
            "raised": raised_flags,
            "manifest_raised": manifest_flags_raised,
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

    # D-067 — HERE, AND ONLY HERE, `None` AND 0 ARE THE SAME ANSWER. A run
    # being created has no prior ceiling for an omitted flag to leave alone, so
    # "no cap was passed" and "a cap of 0 was passed" both persist 0, which is
    # the documented spelling of unbounded and what every reader below expects
    # to find in state.json and the manifest. Collapsed once, at the top of the
    # branch, rather than at each of the three writes that would otherwise have
    # to remember it.
    if max_cycles is None:
        max_cycles = 0

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

    # A FUNCTION-LOCAL IMPORT, and the cycle it avoids is named. Both
    # `tools/rosters.py` and `tools/concerns.py` import THIS module at their
    # module top — `_locked_document`, `ledger_transaction`, `allocate_record_id`
    # and `ledger_refusals` are all defined here — so importing them back at the
    # top of this file is an ImportError raised at server startup, behind which
    # every tool in the package lives. The names taken are the two artifacts'
    # own spellings of where they live and what their record container is
    # called: this function CREATES them empty and well-formed, casting 1's
    # writers own every subsequent write, and neither end re-types the other's
    # path (`rosters.roster_path`'s docstring names this caller).
    from foundry_mcp.tools.concerns import (
        CONCERNS_COLLECTION_KEY,
        CONCERNS_FILENAME,
    )
    from foundry_mcp.tools.rosters import ROSTERS_DIRNAME

    dirs = [
        fdir,
        fdir / "castings",
        fdir / "traces",
        fdir / "proofs",
        fdir / "proofs" / "screenshots",
        # CT-002 / FR-024 / ST-010 — one document per stream lands in here at
        # first derivation. Created at init, empty, so `Foundry-Roster` never has
        # to decide whether the directory exists and a lead listing the run
        # directory can see that rosters are a thing this run keeps before any
        # stream has reported.
        fdir / ROSTERS_DIRNAME,
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

    # concerns.json — always fresh. CT-001 / GI-013: the STRUCTURED half of the
    # concern channel, seeded beside the other ledgers for the same reason
    # observations.json is — a teammate filing the first concern of a run must
    # never be the caller that discovers the ledger does not exist. Written
    # through `write_document`, which takes the ledger lock and therefore leaves
    # the `.lock` sidecar every casting-1 writer flocks already on disk.
    # concerns.md stays prose and is rendered from this by the writers; nothing
    # here parses it.
    concerns_path = fdir / CONCERNS_FILENAME
    write_document(concerns_path, {CONCERNS_COLLECTION_KEY: []})
    files_created.append(CONCERNS_FILENAME)

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
        # fallout FR-054 / D-052 — THE MARKER THAT SAYS THIS IS A NEW RUN.
        #
        # `foundry_validate._archive_schema_version` reads THIS key and nothing
        # else to decide whether a manifest's missing `requirement_ids` is an
        # archive that predates the field (reported not computable, passes) or a
        # run created under the schema that mandates it (refused). Nothing wrote
        # it at init, so every run this server created answered 0 and F0.9's
        # fail-closed half was unreachable on exactly the runs FR-054 names.
        #
        # THE GENERATION, NOT THE FLOOR (fallout C-023). The value is
        # `foundry_state.ARCHIVE_SCHEMA_VERSION` — what this run's artefacts
        # ARE — and deliberately not `foundry_validate.REQUIREMENT_IDS_SCHEMA_FLOOR`,
        # which is merely where `requirement_ids` became mandatory. The two
        # integers are equal today and answer different questions, so writing
        # the floor here would make a later floor bump silently restate what
        # every already-created run had claimed about itself.
        #
        # state.json ONLY, matching the single reader and matching
        # `scripts/migrate-archive.py`, which writes the same key to the same
        # document. The manifest carries `temper` / `nyquist` / `no_ui` /
        # `max_cycles` because a second reader loads them from there; no reader
        # loads the schema marker from the manifest, and a second copy of a
        # generation number is a second thing to bump.
        "archive_schema_version": ARCHIVE_SCHEMA_VERSION,
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
    # The inspect gate readers (orchestration/streams.py#_check_streams_complete
    # and orchestration/teams.py#_check_sight_required) load target_url from HERE, not
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
    fallout_of: str = "",
    supersedes: str = "",
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
            absent or unusable (see
            ``foundry_mcp/tools/foundry_state.py#current_cycle``). Kept on the
            record as
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
            the failure and found none, HARDENING when it drove a probe of its
            own devising and that probe failed on a path NO requirement states.
            REQUIRED (CT-001 / FR-004 / CT-012). It is NOT a severity: all
            three are defects and all three get fixed; the axis decides only
            which gate a still-open instance blocks, and HARDENING holds none
            shut. A HARDENING filing carries NO ``spec_ref`` — a reference is
            the statement that a requirement is at stake, which is the one
            thing this tier says is not — and a filing whose prose claims a
            stated requirement's behaviour is absent is refused here for the
            same reason (D-078).

            D-093 — THIS PARAGRAPH IS A CONTRACT A STREAM READS, NOT A NOTE.
            It enumerated two tiers after the vocabulary held three, so a
            stream that read it learned neither what HARDENING means nor that
            it owes a reproduction, and was then refused for a field the prose
            had scoped to LATENT. ``server.py``'s ``list_tools`` descriptions
            are the wire-visible half of the same sentence.
        reproduction_attempted: for a LATENT filing, the statement naming what
            was driven and what it found (e.g. "AST sweep of both roots finds
            0 sites"); for a HARDENING filing, the probe that was driven and
            the wrong result it produced — the same evidence standard as LIVE,
            stated in this field rather than in the description. REQUIRED on
            BOTH of those tiers (not LATENT alone) and refused when it is a
            placeholder (``vocab.reproduction_attempted_problem`` is the
            check); stored as ``None`` on a LIVE record, whose reproduction
            lives in the description.

        fallout_of: optional (CT-019 / FR-025) — the ``D-NNN`` this finding is
            fallout OF: a defect this run already filed whose fix, or whose
            absence, produced this one. Refused when it names an id the ledger
            does not hold. Persisted on every record, ``None`` when unset,
            because casting 3's ``measure-run.py`` counts fallout per cycle and
            an ABSENT key reads as "never measured" rather than as a zero.
        supersedes: optional (CT-019 / ST-006 / GI-022) — the id of an open
            HARDENING record this filing PROMOTES. The cited record is closed
            with status ``superseded``; its tier, description and reproduction
            are left exactly as the stream filed them, because promotion is a
            new filing that cites the old one and never a re-tier in place.
            Citing an id that is unknown, already closed, or not HARDENING
            closes nothing and refuses nothing — the ``superseded`` key of the
            result names what was actually closed.

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
        retiered, retiered_ids, superseded}``, or a named refusal
        ``{error, hint, ...}``
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
    if (corrupt := _named_artifact_guard(fdir, "defects.json", "state.json")):
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
            # Held for this door by
            # `tests/test_vocab.py#test_the_defect_door_audits_under_the_class_it_refuses`
            # and for the batch one by its sibling
            # `tests/test_vocab.py#test_the_sync_door_audits_under_the_class_it_refuses`
            # — pinned at the doors rather than only at the predicate tuple,
            # because it is the DOOR that writes the two artifacts an auditor
            # later compares. Both names are written unbroken on purpose: a
            # symbol split across two comment lines is not a cite a reader or a
            # guard can resolve, and the halves this replaced resolved to
            # nothing at all (concern C-048).
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
                fdir, tripwire_finding(finding), cycle=current_cycle(fdir), source=source
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
        "cycle": current_cycle(fdir),
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
        # FR-004 / FR-029 / GI-014: the negative result a LATENT filing is
        # answerable for AND the probe a HARDENING filing drove, and explicitly
        # `None` on a LIVE record rather than "" — absent evidence and empty
        # evidence are different claims, and a LIVE record's reproduction lives
        # in the description where the stream put it. Keyed on `!= "LIVE"`
        # rather than on `== "LATENT"`: the validator now demands the field of
        # both non-blocking tiers, so keying on one of them would refuse a
        # HARDENING filing for omitting evidence and then drop the evidence it
        # supplied.
        "reproduction_attempted": reproduction_attempted if tier != "LIVE" else None,
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
        # FR-025 / CT-019 / ST-006 — the two provenance fields, ALWAYS both,
        # seeded null exactly as the three fix fields above are and for a
        # sharper reason than symmetry: `foundry_state.fallout_rows` reads an
        # absent `fallout_of` key as STRUCTURALLY UNMEASURED, so a door that
        # wrote the key only when a filer set it would make every cycle of every
        # post-change run read as "never measured" and FR-025's acceptance
        # figure permanently not_measurable.
        **defect_provenance({"fallout_of": fallout_of, "supersedes": supersedes}),
        "created_at": now,
    }
    if target_kind:
        defect["target_kind"] = target_kind

    defects_path = fdir / "defects.json"
    with ledger_transaction(defects_path, "defects") as defects:
        # CT-019 — the `fallout_of` rung, INSIDE the lock and ahead of every
        # mutation. It is the one rung `validate_defect_filing` cannot own: that
        # function reads the mapping and nothing else, by contract, so that the
        # batch door can call it once per finding before opening its
        # transaction. "Does the ledger hold this id" is a LEDGER question, and
        # asking it outside the lock would re-open the read-then-write window
        # the lock exists to close — a parent filed by a concurrent door would
        # read as unknown. Returning from inside the transaction is safe and
        # deliberate: nothing has been mutated yet, so the primitive's exit
        # compares an unchanged document and writes nothing.
        unknown_parent = fallout_parent_problem(defect["fallout_of"], defects)
        if unknown_parent is not None:
            return unknown_parent

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
            # fallout D-101 — the provenance the re-filing declared, taken from
            # the NORMALISED record literal rather than from the raw arguments,
            # so this exit and the append exit carry byte-identical values. A
            # filing that took this branch had `fallout_of` validated against
            # the ledger three lines up and then dropped on the floor.
            fallout_of=defect["fallout_of"],
            supersedes=defect["supersedes"],
            # fallout D-157 — the run dir the tier guard's denylist tripwire is
            # written to. This door already holds it (`defects_path` above is
            # derived from it), so passing it is not a second derivation of a
            # path; it is the same one the pre-transaction refusal three frames
            # up already audits through.
            fdir=fdir,
        )
        if retiered_id is not None:
            defect_id = retiered_id
        else:
            defect_id = allocate_record_id(defects, "D")
            defect["id"] = defect_id
            defects.append(defect)

        # ST-006 / GI-022 — the promotion, in the SAME transaction that
        # persisted the record making it. A closure written in a second
        # transaction would leave a window in which the citing record exists and
        # the record it supersedes is still open, and every gate and census that
        # counts `status == "open"` reads that window as one more open defect
        # than the run has. After the append, because the closure records the id
        # of the record that superseded it and that id is minted above.
        superseded_id = close_superseded_record(
            defects,
            defect["supersedes"],
            by_id=defect_id,
            cycle=defect["cycle"],
        )
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
        # GI-006 / D-079 — "run artefacts stay complete", and this row is
        # keyed the way the RECORD LITERAL above is keyed (`!= "LIVE"`) rather
        # than on `== "LATENT"`. The two disagreed: the validator demands a
        # reproduction of BOTH non-blocking tiers, the literal persists it for
        # both, and this row dropped it for HARDENING — so forge-log.md carried
        # the evidence row for a LATENT filing and no such row for a HARDENING
        # filing whose reproduction the door had just demanded as a condition
        # of acceptance. The literal's own comment already forbade exactly this
        # ("would refuse a HARDENING filing for omitting evidence and then drop
        # the evidence it supplied"); the mirror is the second place that
        # sentence has to be true. `""` on LIVE rather than `None` because
        # `_ledger_mirror` prints a row only for a truthy value and a LIVE
        # record's reproduction is in its description.
        ("Reproduction attempted", reproduction_attempted if tier != "LIVE" else ""),
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
        # ST-006 — the id this filing actually CLOSED, or None. Reported rather
        # than assumed, because `supersedes` naming an unknown, already-closed
        # or non-HARDENING record is not a refusal (CT-019's errors column
        # admits one error and this is not it): the filer learns here that the
        # promotion did not land, instead of learning nothing.
        "superseded": superseded_id,
    }


#: CT-017 / GI-027 — THE CANDIDATE LANE, IN ONE SPELLING, because BOTH of the
#: observation door's refusal arms have to offer it and neither of them did.
#:
#: GI-027's violation column names two harms and they are different. One is
#: "TEMPER ignoring recorded candidates". The other is "PROVE filing a
#: candidate as a defect" — and a refusal that hands a PROVE agent exactly two
#: destinations, neither of them this ledger, is that violation written as an
#: instruction rather than committed as a mistake. Both arms below used to do
#: precisely that: the tripwire arm said "comment prose, or Foundry-Defect",
#: the no-class arm said "only comment prose is an observation ... file it as a
#: defect", and the class that exists to catch an undriven probe idea appeared
#: in neither, on the door that is the only writer of it.
#:
#: One constant rather than two sentences, for the reason `_TEAMS_DOWN_HINT`
#: is one: two arms spelling the same routing rule is how they drift, and a
#: reader who meets one spelling here and another there learns that the lane is
#: approximate. `_HARDENING_REPRODUCTION_HINT` is this sentence's mirror on the
#: defect door ("A worry you did not drive is not a HARDENING record — record
#: it as a TEMPER_CANDIDATE observation through Foundry-Observation instead"),
#: so the two doors now point at each other with one rule between them.
_TEMPER_CANDIDATE_ROUTE = (
    "If nobody has DRIVEN it — a probe idea, a question about code you have "
    f'not yet answered — declare classification="{TEMPER_CANDIDATE}" and it '
    "belongs HERE: that class is not about comment prose at all, so the "
    "comment-subject rung does not apply to it and no target_kind is owed."
)


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
    """Record a comment-prose finding \u2014 or an undriven probe idea \u2014 in the
    run's observations ledger.

    The non-blocking half of the FR-001 split. Observations are typed,
    persisted per run in ``observations.json``, and NEVER mixed into
    ``defects.json``.

    TWO LANES REACH THIS DOOR, AND EVERY ABSOLUTE BELOW IS WRITTEN FOR THE
    FIRST (CT-017 / GI-027). The four COMMENT-PROSE classes are demotions of a
    finding about a comment, and the whole fail-closed regime exists for them.
    ``TEMPER_CANDIDATE`` is the fifth class and it demotes NOTHING: it is "here
    is a question nobody has asked yet", its subject is CODE by definition, and
    it is reachable only by DECLARING it. So a probe idea is not a defect and
    must not be filed as one \u2014 that is GI-027's named violation \u2014 and it owes
    no ``target_kind="comment"``. The three CLAIM entries of the never-demote
    denylist apply to it unchanged.

    The never-demote denylist is enforced here and is absolute (FR-002 /
    AC-002): a security-property claim, a spec-required-behaviour claim, an
    unresolvable cite, and \u2014 in the comment-prose lane \u2014 anything that is not
    a declared comment are rejected, and the audit tripwire fires \u2014 durably,
    into the ledger's ``tripwire`` array and forge-log.md \u2014 naming which entry
    matched. A ``spec_ref`` is itself a spec-required-behaviour claim, so
    citing a requirement is by construction enough to keep a finding a defect.

    RECORDING A COMMENT-PROSE OBSERVATION *IS* THE DEMOTION, so that lane fails
    CLOSED on an undeclared subject: the default is "not demotable unless
    declared", never "demotable unless declared". That is the opposite of
    ``foundry_add_defect`` on purpose \u2014 there, absence must not license a
    refusal, because refusing a defect is also a demotion. Both surfaces read
    the same predicate and both fail in the direction that keeps a finding
    blocking. The candidate lane sits outside that regime because it demotes
    nothing there is a finding to hide behind.

    Args:
        classification: optional; a member of ``vocab.OBSERVATION_CLASSES``.
            Derived from the description when omitted — and only the four
            COMMENT-PROSE classes are derivable, so ``TEMPER_CANDIDATE``
            (CT-017: a PROVE probe idea nobody has driven) is reachable only by
            declaring it. Declaring it also lifts the ``target_kind="comment"``
            requirement below, because a candidate's subject is code and nothing
            has been shown to be wrong about it; the three CLAIM entries of the
            never-demote denylist still apply unchanged.
        target_kind: REQUIRED in effect \u2014 must be the explicit string
            "comment". Omitting it is refused under the NON_COMMENT denylist
            entry and fires the tripwire, exactly as a present non-comment
            value does. The ONE exception is a declared
            ``classification="TEMPER_CANDIDATE"``: that class is not about
            comment prose at all, so the subject rung does not apply to it.

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
    if (corrupt := _named_artifact_guard(fdir, "observations.json", "state.json")):
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
        fdir,
        finding,
        cycle=current_cycle(fdir),
        source=source,
        # CT-017 / GI-027 — the one class whose subject is CODE. Keyed on the
        # classification the caller DECLARED, before `observation_class` has
        # inferred anything, and that is the whole of the narrowing: the four
        # inferable classes are comment-prose predicates and none of them can
        # ever return TEMPER_CANDIDATE, so an omitted `classification` reaches
        # the comment-subject rung exactly as it does today. Fail-closed in
        # D-069's direction — absence licenses nothing.
        comment_subject_required=classification != TEMPER_CANDIDATE,
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
                # an omitted target_kind is a caller bug with THREE legitimate
                # repairs, and the wrong one to guess is "re-word it".
                #
                # CT-017 \u2014 the third repair was missing for as long as the
                # fifth class has existed, and its absence made this sentence
                # read as "code means Foundry-Defect", which is GI-027's named
                # violation stated as an instruction to the one stream that
                # files candidates. "About code" is not the question; "did you
                # drive it" is.
                'Pass target_kind="comment" if the finding really is about '
                "comment prose. "
                + _TEMPER_CANDIDATE_ROUTE
                + " If you DROVE the code and it came back wrong \u2014 a function, "
                "a handler, a wiring path \u2014 it is a defect and belongs in "
                "Foundry-Defect. "
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

    # `observation_class` walks the four COMMENT-PROSE predicates and can never
    # return TEMPER_CANDIDATE (CT-017): a probe idea is a question somebody
    # thought of, not a pattern a regex finds, so the fifth class arrives only
    # when a caller declares it. That is also why the denylist call above can
    # key on the declared value alone.
    resolved = classification or observation_class(finding)
    if resolved is None:
        return {
            "error": (
                f"No comment-prose observation class matches this finding. "
                f"Must be one of: {', '.join(sorted(OBSERVATION_CLASSES))}"
            ),
            "hint": (
                # CT-017 — this used to open "Only comment prose is an
                # observation", one line above an error that lists
                # TEMPER_CANDIDATE among the classes the caller may declare.
                # The two sentences contradicted each other inside a single
                # refusal, and the false half is the one that routes: a PROVE
                # agent holding an undriven probe idea read "file it as a
                # defect" from the door that is the only writer of candidates.
                "Comment prose and undriven probe ideas are the two things "
                "this ledger holds. " + _TEMPER_CANDIDATE_ROUTE + " If you "
                "DROVE the behaviour, the wiring or a security property and it "
                "came back wrong, that is a finding rather than a question: "
                "file it as a defect via Foundry-Defect."
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
        "cycle": current_cycle(fdir),
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


#: FR-017 / GI-027 / ST-007 — THE KEY THE ROSTER COMES BACK UNDER, named once
#: so the hint below and the query result cannot spell it differently.
OPEN_CANDIDATES_KEY = "open_temper_candidates"

#: The one spelling of "go and read the candidate roster", so the three arms of
#: `foundry_drive_temper_candidate` that fall back to it cannot drift apart the
#: way D-186's three gate arms did.
#:
#: fallout D-114 — AND SO THAT IT NAMES AN EXIT ITS DOOR PROVIDES. The hint
#: said "read the OPEN candidates with Foundry-Observations(classification=
#: TEMPER_CANDIDATE)", and that call filtered on cycle, source and
#: classification and nothing else — so TEMPER's roster came back with the
#: already-driven candidates in it. DRIVEN: file one candidate, close it
#: through `foundry_drive_temper_candidate` (status becomes 'DRIVEN'), query
#: with classification=TEMPER_CANDIDATE — the record is still returned,
#: statuses ['DRIVEN']. FR-017 defines TEMPER's roster as the OPEN candidates,
#: and the undriven derivation existed only as a second private one in
#: `foundry_mcp/tools/foundry_report.py#_read_undriven_temper_candidates`, so
#: the surface TEMPER reads and the surface the report renders answered
#: different questions. A hint naming an exit its door does not provide is this
#: package's own recorded failure shape (D-101), so the DOOR now provides it
#: and the hint names the key it comes back under.
_CANDIDATE_ROSTER_HINT = (
    "Read the open candidates with "
    "Foundry-Observations(classification=TEMPER_CANDIDATE) — they come back "
    f"under `{OPEN_CANDIDATES_KEY}`, already filtered to the ones nobody has "
    "driven — and pass the O-NNN of the one you drove."
)


def temper_candidate_is_driven(record: Mapping[str, object]) -> bool:
    """ST-007 — has this candidate already been closed as DRIVEN?

    BOTH SPELLINGS, one derivation. `foundry_drive_temper_candidate` writes
    `status` and casting 11's TEMPER prose may write `driven`; the report
    reader (`foundry_report.py#_read_undriven_temper_candidates`) was written
    to tolerate whichever of the two castings landed first, and this door's
    idempotence rung had to see the same two. Spelled ONCE here so the
    idempotence rung and the roster the query hands TEMPER cannot answer
    differently about the same record — which is exactly what D-114 reports
    happening between this module and the report.

    A record carrying NEITHER marker is undriven, which is the correct reading
    of every archive written before this release: nothing recorded that it was
    driven, so nothing may claim it was.
    """
    return bool(
        record.get("driven") or str(record.get("status", "")).upper() == "DRIVEN"
    )


@ledger_refusals
def foundry_drive_temper_candidate(
    observation_id: str,
    filed: str = "",
    project_root: str = ".",
) -> dict:
    """ST-007 — close an open TEMPER_CANDIDATE observation as DRIVEN.

    The WRITE half of the transition ``foundry_add_observation`` above opens
    and ``foundry_query_observations`` below reads back. Until this door
    existed the terminal state had no writer anywhere in the package: the
    record literal carried neither marker and no parameter could set one, so
    every candidate was undriven BY CONSTRUCTION, TEMPER had no call to make,
    and ``foundry_mcp/tools/foundry_report.py#_read_undriven_temper_candidates``
    listed every recorded candidate on every run — a partition one side of
    which nothing could ever reach.

    FILED AND CLEAN ARE BOTH CLOSURES. A probe driven and filed against names
    the defect it produced in ``filed``; a probe driven and found sound omits
    it. A candidate found sound is a RESULT, and the only result that ever
    retires a question, so the record is DRIVEN either way and
    ``driven_finding`` carries the id or the empty string that says there was
    none.

    ``filed`` IS STATED EVIDENCE, NOT A RANKED CITE. It is recorded verbatim
    and never checked against the defect ledger. ST-007 admits no error on this
    field, and D-101 is this package's record of what inventing a rung a
    contract does not admit costs: the door refuses its own documented example
    and the stream's next move is to fabricate the field. It reads back exactly
    as the stream wrote it, the way ``reproduction_attempted`` does. Ranking it
    would also mean reading a SECOND ledger's file while holding this one's
    flock, which is a lock ordering this module does not have and a race it
    could not close: ``fallout_parent_problem`` can rank ``fallout_of`` only
    because the parent lives in the records its caller is already holding.

    RE-DRIVING IS IDEMPOTENT, NOT A REFUSAL. The first closure stands and the
    result says ``already_driven``, because a retry after a dropped answer must
    not report failure over work that landed, and the stream that drove the
    probe first is the one that drove it.

    ONE OF THE TWO SPELLINGS, PINNED BY THE READER RATHER THAN BY A SHARED
    CONSTANT. The report reader accepts ``driven`` truthy OR ``status``
    "DRIVEN" — it was written to tolerate whichever of the two castings
    landed first — so this door writes ``status`` alone, and
    ``tests/test_observations.py`` drives that reader over a record this door
    wrote. Two spellings agreed by inspection is what the tolerance was for;
    agreement driven end to end is what replaces it.

    Args:
        observation_id: the ``O-NNN`` of the candidate that was driven.
        filed: optional; the ``D-NNN`` this drive produced. Omitted means the
            probe was driven and found clean, which is a closure and not a
            blank.

    Returns:
        ``{observation_id, status, driven_in_cycle, driven_finding,
        already_driven}``, or a named refusal ``{error, hint, field}``.
    """
    fdir = get_run_dir(project_root)
    if not fdir:
        return {"error": "No active foundry run. Call Foundry-Init."}

    # Both containers, for the reason `foundry_add_observation` guards both:
    # the cycle this closure is stamped with comes out of state.json, and a
    # closure stamped from a corrupt counter is a record that cannot be joined
    # against the defects filed beside it.
    if (corrupt := _named_artifact_guard(fdir, "observations.json", "state.json")):
        return corrupt

    candidate_id = observation_id.strip()
    if not candidate_id:
        return {
            "error": "observation_id is required: name the candidate to close.",
            "hint": _CANDIDATE_ROSTER_HINT,
            "field": "observation_id",
        }

    cycle = current_cycle(fdir)
    finding = filed.strip()
    result: dict = {}
    closed = False
    closed_description = ""

    with ledger_transaction(fdir / "observations.json", "observations") as records:
        # `_dict_records`, never a bare scan over the binding: D-128's package
        # property. The elements are the SAME objects, so the mutation below
        # still reaches the ledger, and the filter is the ONE spelling every
        # scan in this module shares rather than an inline `isinstance` --
        # which is another copy of the same filter, which is the defect class.
        record = next(
            (o for o in _dict_records(records) if o.get("id") == candidate_id),
            None,
        )
        if record is None:
            # A call that closed nothing is a caller error worth naming, and
            # unlike `supersedes` -- which rides along a filing that succeeded
            # regardless -- there is no other answer this door could give.
            result = {
                "error": (
                    f"Unknown observation: {candidate_id!r} names no record in "
                    f"this run's observations ledger."
                ),
                "hint": _CANDIDATE_ROSTER_HINT,
                "field": "observation_id",
            }
        elif record.get("classification") != TEMPER_CANDIDATE:
            result = {
                "error": (
                    f"{candidate_id} is classified "
                    f"{record.get('classification')!r}, not {TEMPER_CANDIDATE}. "
                    f"Only a candidate is driven."
                ),
                "hint": (
                    "The driven transition is defined on the one observation "
                    "class whose subject is CODE. Every other class records a "
                    "finding about comment prose — a statement, not a "
                    "question — and there is nothing to drive. "
                    + _CANDIDATE_ROSTER_HINT
                ),
                "field": "classification",
            }
        elif temper_candidate_is_driven(record):
            # BOTH spellings read here, not just the one this door writes: a
            # record closed by a later tool, or read out of an archive that
            # spelled it the other way, is already driven and must not be
            # re-closed under a second cycle. The reader tolerates two
            # spellings, so the idempotence rung has to see the same two.
            #
            # fallout D-114 — ASKED OF THE SHARED PREDICATE rather than spelled
            # inline. This rung and the roster `foundry_query_observations`
            # hands TEMPER must agree about the same record, and a second
            # inline `or` here is how they would come to disagree.
            result = {
                "observation_id": candidate_id,
                "status": record.get("status"),
                "driven_in_cycle": record.get("driven_in_cycle"),
                "driven_finding": record.get("driven_finding", ""),
                "already_driven": True,
            }
        else:
            record["status"] = "DRIVEN"
            record["driven_in_cycle"] = cycle
            record["driven_finding"] = finding
            closed = True
            closed_description = str(record.get("description", ""))
            result = {
                "observation_id": candidate_id,
                "status": record["status"],
                "driven_in_cycle": cycle,
                "driven_finding": finding,
                "already_driven": False,
            }

    if closed:
        _ledger_mirror(
            fdir,
            f"Cycle {cycle} — temper: {candidate_id} driven (candidate)",
            [
                ("Closure", f"filed {finding}" if finding else "clean"),
                ("Description", closed_description),
            ],
        )

    return result


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

    fallout D-114 / FR-017 / GI-027 / ST-007 — AND ``open_temper_candidates``,
    WHICH IS TEMPER'S ROSTER
    -----------------------------------------------------------------------
    This is the door `_CANDIDATE_ROSTER_HINT` sends TEMPER to for "the OPEN
    candidates", and it filtered on cycle, source and classification and
    nothing else — so a candidate already closed as DRIVEN came back in the
    roster. Driven: file one candidate, close it through
    `foundry_drive_temper_candidate`, query with
    `classification=TEMPER_CANDIDATE`; the record is still returned, statuses
    ['DRIVEN']. FR-017 defines the roster as the open candidates, and the only
    "undriven" derivation in the package was a private one in
    `foundry_mcp/tools/foundry_report.py#_read_undriven_temper_candidates` —
    so the surface TEMPER read and the surface the report rendered answered
    different questions about the same ledger.

    DERIVED AND RETURNED BESIDE ``observations`` RATHER THAN FILTERING IT.
    Two reasons, both driven by callers that exist: a query named "query the
    observations ledger" that silently omitted records would be a worse
    surface than the one being fixed, and the report needs the FULL candidate
    list to count the driven ones. So the general query stays general and the
    ROSTER is a derived answer with its own key — which is also what lets the
    hint name an exit this door provides, over the schema as it ships, with no
    new parameter for a caller to know about.

    UNCONDITIONAL, exactly as ``tripwire`` is, and computed over ALL
    observations rather than over the filtered list: the roster is a property
    of the run, not of whatever filter this particular call passed, and a
    caller that narrowed by cycle must not be told the candidates from other
    cycles are driven.
    """
    fdir = get_run_dir(project_root)
    if not fdir:
        return {"error": "No active foundry run. Call Foundry-Init."}
    # D-095: the QUERY path was holed identically to the write path, so a
    # corrupt ledger could not even be looked at to diagnose which file to
    # repair. Guarded rather than merely tolerated, because a query that
    # silently answers "no observations" about an unreadable ledger is worse
    # than one that names the file.
    if (corrupt := _named_artifact_guard(fdir, "observations.json")):
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

    # fallout D-114 — TEMPER's roster: the recorded candidates nobody drove.
    # `TEMPER_CANDIDATE` comes from the vocabulary by name and the driven
    # question from `temper_candidate_is_driven`, the one predicate the
    # idempotence rung in `foundry_drive_temper_candidate` also asks — so the
    # roster and the door that closes an entry on it cannot disagree about a
    # record.
    candidates = [
        o
        for o in all_observations
        if o.get("classification") == TEMPER_CANDIDATE
    ]
    open_candidates = [o for o in candidates if not temper_candidate_is_driven(o)]

    return {
        "observations": observations,
        "tripwire": tripwire,
        OPEN_CANDIDATES_KEY: open_candidates,
        "summary": {
            "total": len(all_observations),
            "tripwire_fired": len(tripwire),
            "by_classification": by_classification,
            "by_source": by_source,
            # The two halves of ST-007's partition, reported beside each other
            # so a lead reading this result can see the debt without joining
            # two numbers by hand. `driven` is the complement rather than a
            # second scan, so the pair can never sum to something other than
            # the candidate count.
            "temper_candidates": len(candidates),
            "open_temper_candidates": len(open_candidates),
            "driven_temper_candidates": len(candidates) - len(open_candidates),
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
    if (corrupt := _named_artifact_guard(fdir, "defects.json")):
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
    ``orchestration/gates.py#_synthesize_clean_prove_verdicts`` wrote the SAME
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
    if (corrupt := _named_artifact_guard(fdir, "verdicts.json", "state.json")):
        return corrupt
    verdicts_path = fdir / "verdicts.json"

    # Read outside the critical section: it opens state.json, not this ledger,
    # and the stamp does not depend on anything the transaction reads.
    stamped_cycle = current_cycle(fdir)
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
    if (corrupt := _named_artifact_guard(fdir, "verdicts.json", "defects.json")):
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
