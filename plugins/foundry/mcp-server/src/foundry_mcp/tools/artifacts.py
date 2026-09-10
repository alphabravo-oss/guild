"""The run-artifact leaf: how this package reads, writes and guards a document.

THE LEAF OF THE SPLIT. Every symbol below is Block B of
``foundry_mcp/tools/foundry_orchestrator.py``, MOVED here unchanged — the total
tolerant read, the atomic write, the re-entrant flock read-modify-write and its
lock scaffold, the corruption classification and the scan that walks a run
directory, the house artifact guard, the run-marker names, and
``_resolve_spec_path``. Bodies, docstrings and comments are byte-identical to
the ones they came from, because each records a defect this package paid for and
a reworded comment is a lost citation.

WHAT MAKES IT A LEAF, and it is the whole reason this module exists. It imports
the standard library, ``foundry_mcp.schemas.vocab`` and
``foundry_mcp.tools.foundry_state`` — and nothing else, ever. It names no gate,
no phase and no stream; it knows what a DOCUMENT is and nothing about what the
run does with one. Five sibling modules used to reach the top of the stack for
these primitives, which is what made a 15,639-line state machine the package's
de-facto persistence layer and forced the lazy imports that worked around the
cycles that created. See ``foundry_handoff``, ``intent_coverage``,
``forge_spec`` and ``foundry_validate``: each now takes its primitives from
here.

THE ONE EXCEPTION TO THE IMPORT RULE IS GONE, AND THIS RECORDS WHAT IT WAS.
``_manifest_shape_problem_lazy`` used to reach ``foundry_spawn`` from INSIDE its
body: a call-time edge rather than a load-time one, which is what kept the graph
acyclic while the manifest validator lived a layer above. Concern C-060 moved
that validator here (``manifest_shape_problem``), so the edge has no reason to
exist and does not: this module now reaches nothing above the leaf layer at ANY
depth, module-top and call-time alike, which is the property
``tests/test_artifacts.py`` asserts rather than the exception it used to name.

THE DUPLICATE WINDOW IS CLOSED, AND THIS RECORDS WHAT IT WAS. For one wave this
module and the orchestrator both defined every symbol here, and BOTH copies were
live in one process. They excluded each other correctly across threads, modules
and processes because both opened the SAME on-disk lock —
``path.with_name(path.name + _TX_LOCK_SUFFIX)``, byte-identical spelling and
suffix — and ``flock`` is what binds across modules. What they did NOT share was
the in-process re-entrancy map (``_ARTIFACT_TX``), so a transaction opened here
inside one opened there, on the same document on the same thread, would have
blocked on a lock that thread already held; no such nesting was reachable,
because the only caller that opens a transaction through this module is
``forge_spec``, and only on ``foundry-planning/<project>/state.json``, which no
orchestrator transaction ever opened.

The carve has landed and the orchestrator is gone. Of the write primitives
these are now the only definitions, which
``tests/orchestration/test_module_boundaries.py::test_the_helpers_group_zero_consolidated_have_exactly_one_definition``
holds for ``_save_json``, ``_document_transaction``, ``_resolve_spec_path``,
``_declared_external_inputs`` and ``_run_artifact_problems`` by name. The
paragraph above stays because the lock filename is still load-bearing for the
reason it names — two processes on one repository contend through that file —
and because a reader who finds a second copy one day should know this was
already paid for once.

AND THE OTHER SECOND COPY IS CLOSED TOO, WHICH THIS SAYS OUT LOUD RATHER THAN
LEAVING A READER TO INFER IT FROM AN ABSENCE. ``tools/foundry.py`` held a
parallel stack over the SAME documents: a byte-identical tolerant read
(fallout D-011) and a lock domain of its own — a module-top
``threading.RLock()`` / ``threading.local()`` pair under names of its own
(fallout D-010; the commit that deleted them spells them, which is where a dead
spelling belongs and why this sentence does not repeat it). Both closed onto
this module — it imports ``_load_json``, ``_read_document`` and
``_document_problem`` from here, binds
``_ARTIFACT_LOCK`` / ``_ARTIFACT_TX``, and derives its lock sidecar from
``_TX_LOCK_SUFFIX``. So those names are a package-wide contract and not this
module's private business, and there is now ONE in-flight map keyed on
``str(path)``: a ledger transaction and a document transaction on the same file
on the same thread compose into one write instead of blocking on a flock the
thread already holds.

THE ONE SHARED NAME IS GONE TOO, AND A RENAME IS WHAT CLOSED IT (fallout
D-061). ``foundry.py`` used to define ``_artifact_guard`` as well, and it was
never a second copy of this module's: that one takes ``*names`` and is scoped
to the artifacts the calling tool touches, so a corrupt roll-up cannot block a
filing that never opens it, and it runs a ledger-container rung that may not
move into a leaf (GI-033 — a leaf that knows what a ledger is has stopped being
one). This module's takes a run dir and scans the whole run. A sweep keyed on
names could not tell those two contracts apart and read them as one rule
duplicated — and neither deletion was on offer, because the arities differ and
folding that ledger rung in here is the thing GI-033 forbids. The third exit
was a name that states the narrower job, so that function is
``_named_artifact_guard`` now and ``_artifact_guard`` has exactly one
definition, here. Both inventories in ``tests/test_artifacts.py`` are empty as
a result, which is the state they were written to reach: each one FAILS the day
a row stops accounting for anything, and that is what took the last rows out.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from foundry_mcp.schemas.vocab import (
    REPORT_JSON_FILENAME,
    REPORT_MD_FILENAME,
    REPORT_REQUIRED_SECTIONS,
    # The heading each section key renders as. It lived in `foundry_report.py`
    # until casting 10 put it beside `REPORT_REQUIRED_SECTIONS`, where the
    # assertion pairing the two runs at VOCABULARY import — so the seal that
    # WRITES the headings and the read below that CHECKS them take the same
    # sixteen strings from one place. A copy of this table here would be the
    # defect the hoist exists to avoid: rename a heading and the gate reports a
    # section missing that the seal had just written.
    REPORT_SECTION_TITLES,
    REQUIREMENT_ID_RE,
    STREAM_WIRE_IDS,
)
from foundry_mcp.tools.foundry_state import (
    ARCHIVE_DIR,
    document_refusal,
    get_run_dir,
    # DERIVED FROM THE SEAL'S OWN SPLITTER, which is why the markdown read below
    # calls it rather than splitting lines itself: Holmes `share-10` found the
    # gate's heading rule and the seal's coded independently — the same
    # effective rule, free to part — so the gate could call a section present
    # that the seal did not treat as one.
    markdown_headings,
    read_document,
    read_text_file,
)


# --------------------------------------------------------------------------- #
# Run-artifact persistence (D-098 / D-103).
#
# Two failure classes, one layer, because they are the two halves of the same
# gap: the READ edge raised, and the window between the read and the write lost
# writes.
#
# READS. ``_load_json`` was ``json.loads(path.read_text())`` with no try/except
# and no shape check, and ``server.py``'s ``call_tool`` had none either — so a
# corrupt ``state.json`` raised out of Foundry-Next, the mandatory handshake
# before every phase transition, and the operator could not even read state to
# diagnose it. A 24-combination matrix over 6 artifacts x {truncated, [], null,
# "a string"} bricked a tool 24 times and named the offending file zero times.
# The counterpart one rung down was D-059: ``_current_cycle`` guarded the VALUE
# while nothing guarded the CONTAINER.
#
# The fix is split so that tolerance binds every reader with no per-site edit,
# and naming happens where a human is listening:
#   ``_read_document``  — the tolerant core: (data, named problem).
#   ``_load_json``      — total. {} for missing/unreadable/malformed. NEVER
#                         raises, so all of this module's readers are safe by
#                         construction rather than by 33 remembered try/excepts.
#   ``_artifact_guard`` — the named refusal, at the MCP entry points. Tolerance
#                         alone would silently read a corrupt state.json as
#                         cycle 0; the guard is what makes the file's name reach
#                         the operator.
#                         ``test_module_boundaries#test_every_orchestrator_entry_point_runs_the_artifact_guard``
#                         derives the entry-point set from server.py's _DISPATCH
#                         and fails on the next one added without it.
#
# WRITES. ``_save_json`` is atomic per write, but every caller read, mutated and
# wrote as three separate steps, and the tmp sidecar name was shared: a real
# 4-process x 40-call drive on ``foundry_mark_stream`` SILENTLY LOST 107 of 160
# tranches (67%) and raised 98 FileNotFoundError as one process renamed the
# shared tmp out from under another. That is the DESIGNED path — F2 runs 4-8
# parallel streams each calling Foundry-Stream as it finishes — and CT-003
# requires partial records be "accepted and stored as they arrive".
#   ``_save_json``            — unique tmp per writer, so no peer can rename it.
#   ``_document_transaction`` — the locked read-modify-write every run-artifact
#                               writer uses. RE-ENTRANT per path, which is not a
#                               nicety: ``foundry_mark_phase_complete`` already
#                               nests a state.json RMW inside ``_update_phase``'s
#                               (that nesting was itself a latent lost update).
#
# THERE WERE TWO LOCK DOMAINS OVER ONE DOCUMENT SET, AND THIS IS WHY THAT WAS A
# DEFECT AND NOT A DESIGN (fallout D-010, closed). ``foundry.py``'s
# ``_locked_document`` / ``ledger_transaction`` was a SECOND stack over these
# same paths, with a module-top ``threading.RLock()`` / ``threading.local()``
# pair of its own, under names of its own. The
# projection genuinely differs — that primitive yields a list under a collection
# key, which fits defects.json and observations.json but not state.json /
# stream-rollup.json / escalation.json, whose payload is the document itself —
# and that difference is why the two FUNCTIONS are not one function. It was
# never why the two LOCKS were not one lock, and that distinction is exactly
# what the fix acted on: the functions stayed two, the domain became one.
#
# What excluded, and what did not — kept because it is the reasoning that
# closed this, and because the next module tempted to open a domain of its own
# needs it. The flock — not the RLock — is what binds across MODULES: two
# separate open() calls contend even inside one process, and verdicts.json
# really does have a writer in each function
# (``orchestration/gates.py#_synthesize_clean_prove_verdicts`` opens
# ``_document_transaction``; ``foundry.py#foundry_add_verdict`` opens
# ``_locked_document``). So writes DID serialize — but only because two
# independent spellings of the lock filename happened to agree, one of them a
# bare ``".lock"`` literal. Two things that shared flock did not give: the
# RLock was per module, so nothing but the flock ordered threads across the
# two; and the in-flight maps were different ``threading.local()`` objects, so
# a transaction opened in one domain INSIDE a transaction open in the other, on
# the same document on the same thread, was not recognised as re-entrant and
# blocked forever on a lock that thread already held. No caller reached that
# nesting, which is why D-010 was LATENT rather than a hang — and a hazard one
# call away from reachable is not a convention.
#
# THIS module is the domain, singular. ``foundry.py`` binds ``_ARTIFACT_LOCK``
# and ``_ARTIFACT_TX`` from here and derives its sidecar from
# ``_TX_LOCK_SUFFIX``, so both stacks share one RLock, one in-flight map keyed
# on ``str(path)``, and one spelling of the lock filename — the nesting above
# now composes into a single write at the outermost exit. A locked writer added
# anywhere in this package binds these rather than re-declaring them, and
# ``tests/test_artifacts.py::test_the_leaf_is_the_one_run_artifact_lock_domain``
# fails on the next module that forgets. What the leaf must NOT do is grow the
# other half: ``LedgerShapeError``, ``_dict_records`` and the collection-key
# projection are ledger knowledge, and a leaf that knows what a ledger is has
# stopped being one (GI-033).
# --------------------------------------------------------------------------- #

# Threads inside one server process; the flock sidecar covers a second server
# process on the same repo. Re-entrant so a nested transaction on the same
# thread cannot deadlock against itself.
_ARTIFACT_LOCK = threading.RLock()

# Per thread: ``.docs`` maps path -> in-flight document, and ``.locks`` holds
# the paths ``_artifact_lock`` has taken. A nested transaction on a path already
# open on this thread yields the SAME dict and defers the write to the outermost
# exit, and a nested lock on a path either primitive already holds yields
# straight through — so nesting composes instead of deadlocking on our own
# flock. ONE thread-local, not one per primitive, for the reason the domain
# above is one and not two: two maps would let a path be held in one and unseen
# in the other, which is the whole of what fallout D-010 cost.
_ARTIFACT_TX = threading.local()


#: D-137 — this module's ``_read_document`` was one of TWO byte-identical
#: bodies, and the second copy is the shape four prior fixes in this file left
#: behind every time. It is now the CANONICAL primitive in ``foundry_state``,
#: the package's leaf module, imported here under the same private name so
#: every existing caller and test keeps resolving.
#:
#: fallout D-011 — THE REASON THIS COMMENT USED TO GIVE FOR THE SECOND COPY
#: DOES NOT SURVIVE THE MOVE. It read that ``foundry.py`` keeps its own body
#: "because THIS module imports THAT one (reading back would close a cycle in
#: the import graph)". That was true of the ORCHESTRATOR this block was carved
#: out of. It is false here: the leaf imports ``foundry.py`` nowhere and must
#: not, ``foundry.py`` sits above this layer, and an import running from there
#: to here closes no cycle — which is exactly what made ``foundry.py``'s
#: ``_read_document`` / ``_document_problem`` / ``_load_json`` a duplicate with
#: no reason left rather than an exception with one. That module now imports
#: all three from here, so THIS module is the one home of the tolerant read for
#: the whole package in fact and not only by declaration.
_read_document = read_document


def _document_problem(path: Path) -> str | None:
    """The named reason ``path`` is not a readable JSON object, or None."""
    return _read_document(path)[1]


def _load_json(path: Path) -> dict:
    """Total, tolerant read of a run artifact. Returns {} rather than raising.

    Every malformed-container shape — truncated, ``[]``, ``null``, ``42``,
    ``"a string"``, non-UTF-8 — reads as an empty document, so no reader in
    this module can raise across the MCP boundary. Callers that must TELL the
    operator which file is broken use ``_artifact_guard`` / ``_document_problem``
    rather than inspecting the return value, which cannot distinguish "absent"
    from "corrupt" by design.
    """
    return _read_document(path)[0]


def _read_text(path: Path) -> str:
    """Tolerant text read of a run artifact. "" rather than raising.

    ``directives.md`` is not JSON, so it needs the same container guard: a
    non-UTF-8 byte in it raised UnicodeDecodeError straight out of
    ``_read_directives`` and therefore out of Foundry-Next.
    """
    return read_text_file(path)[0]


# D-007 — THE SCAFFOLDING A WRITE PUTS BESIDE AN ARTIFACT IS NOT AN ARTIFACT.
#
# ``_save_json`` writes a sidecar and renames it into place; ``_document_
# transaction`` (and ``foundry.py``'s ledger primitives) open a ``.lock`` file
# whose bytes are never read. Neither is a document this run reads back, and
# neither has a state an operator could "repair".
#
# These two suffixes are declared HERE and consumed by BOTH the writers below
# and ``_run_artifact_problems``'s exclusion, so the scan's idea of what is
# scaffolding cannot drift from what the writers actually create — which is the
# drift ``_STRICT_ARTIFACT_DECODERS``'s D-138 note describes on the other axis.
#
# THE SCOPE IS THE PACKAGE, NOT THIS MODULE (fallout D-010). "The writers
# below" is where the drift was FOUND, not how far the rule reaches: every
# locked run-artifact writer anywhere in this package spells the lock name
# through ``_TX_LOCK_SUFFIX``, because the flock is what orders two writers
# against each other and two that agree on the name by coincidence agree on it
# only until one of them is edited. That rule now holds without exception:
# ``foundry.py#_locked_document`` and ``#write_document`` spelled a bare
# ``".lock"`` literal until D-010 closed and now build the name from this
# constant like everything else.
_TX_TMP_SUFFIX = ".tmp"
_TX_LOCK_SUFFIX = ".lock"


def _is_write_sidecar(path: Path) -> bool:
    """True when ``path`` is a write primitive's scaffolding, not an artifact."""
    return path.name.endswith((_TX_TMP_SUFFIX, _TX_LOCK_SUFFIX))


# D-206 — A CHECKOUT THIS PACKAGE NESTS UNDER THE RUN DIR IS NOT ONE OF THE
# RUN'S ARTIFACTS.
#
# The same D-007 principle as the sidecars above, one rung out: scaffolding a
# writer puts BESIDE an artifact is not an artifact, and neither is a whole
# second repository a writer puts UNDER one. ``_run_artifact_problems``
# rglobbed the entire run directory with no exclusion for it, and every
# worktree this package creates is rooted directly there:
# ``worktree_helpers._setup_worktree`` computes
# ``base = run_dir / "worktrees" / f"{dir_prefix}{casting_id}"``, and
# ``_sweep_evidence_at_boundary`` calls ``sweep_evidence_at_head(...,
# run_dir=fdir, ...)`` — so the GI-002 boundary sweep's detached checkout of the
# WHOLE project lands at ``fdir/worktrees/sweep-evidence``, virtualenvs,
# ``.dist-info`` trees, compiled extensions and non-UTF-8 test fixtures
# included, inside the tree the guard walks.
#
# Driven: one non-UTF-8 fixture under ``fdir/worktrees/sweep-evidence/`` made
# ``_artifact_guard`` refuse the whole run — "Run artifacts cannot be read: ...
# could not be read (UnicodeDecodeError)" — and the guard runs at the top of
# every MCP entry point in this module, so EVERY door was refusable for the
# duration of any sweep. CT-012's errors cell for Foundry-Next reads "none;
# never blocks" and CT-013's for Foundry-Spend reads "none; unreported
# dispatches are listed, never refused"; both were false while the server's own
# boundary sweep was in flight. Observed for real in GRIND cycle 19: casting 3's
# ``Foundry-Fix`` was refused while casting 5's sweep was running, naming ~40
# entries under ``worktrees/sweep-evidence/plugins/foundry/mcp-server/.venv/``.
# The hint made it worse than a bare false refusal — "repair or delete the named
# file(s) in the run directory" is destructive advice aimed at a peer's live
# checkout.
#
# BOTH AXES, because closing one is how this returns. WHAT IS WALKED: this run's
# own artifacts, never a checkout the package nests beneath them. HOW MEMBERSHIP
# IS DECIDED: by the position the WRITER declares it nests worktrees at. Not by
# the contents — probing each directory for a ``.git`` entry would fail OPEN the
# day a worktree's pointer file is absent or renamed, bringing the whole
# virtualenv back into scope silently, which is this defect again with a longer
# fuse. And not by any segment spelled this way: a ``worktrees`` directory
# somewhere else under the run dir is not a position ``_setup_worktree`` roots
# at, and unguarding it would be the same over-reach pointing the other way.
#
# ONE WORD, AND A PIN THAT WATCHES IT. The exclusion and ``_setup_worktree``
# must not drift, and the one-declaration shape — exporting this name from
# ``worktree_helpers`` and importing it — belongs to that module's owner, so it
# is recorded in ``foundry-archive/daring-orca/concerns.md`` rather than reached
# across for. Until then
# ``test_the_worktrees_exclusion_names_what_setup_worktree_roots_under``
# derives the directory name from ``_setup_worktree``'s own source and fails the
# day the two disagree — the drift-guard shape
# ``test_the_document_suffix_table_covers_every_declared_run_artifact`` already
# uses one axis over.
RUN_WORKTREES_DIRNAME = "worktrees"


def _is_run_worktrees_root(fdir: Path, candidate: Path) -> bool:
    """Is ``candidate`` the run's worktrees root — the subtree to walk past?

    The EXACT directory ``_setup_worktree`` roots under, tested by position
    (``fdir``'s own child) and not by name alone, so nothing deeper in the run
    tree can borrow the exclusion by being spelled the same way.
    """
    return candidate.name == RUN_WORKTREES_DIRNAME and candidate.parent == fdir


def _run_artifact_candidates(fdir: Path) -> list[Path]:
    """Every path under ``fdir`` the guard judges, in ``sorted(rglob)`` order.

    ``os.walk`` rather than ``rglob`` because the worktrees subtree has to be
    PRUNED, not filtered after the fact. It holds a whole project checkout —
    this repo's is tens of thousands of paths once its virtualenvs are in it —
    and ``_artifact_guard`` runs at the top of every MCP entry point, so a
    post-hoc filter would still pay the walk and a ``stat`` of every one of
    them on every tool call while a sweep is in flight.

    The closing ``sorted`` is what makes this a NARROWING of the old candidate
    set rather than a re-ordering of it: over the paths that remain it yields
    exactly what ``sorted(fdir.rglob("*"))`` yielded, so the guard's "in stable
    order" contract and every problem string's position are unchanged.
    """
    found: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(fdir):
        here = Path(dirpath)
        kept: list[str] = []
        for name in dirnames:
            child = here / name
            if _is_run_worktrees_root(fdir, child):
                continue
            found.append(child)
            kept.append(name)
        # In place, because that is the ONLY assignment ``os.walk`` reads back
        # to decide what it descends into.
        dirnames[:] = kept
        found.extend(here / name for name in filenames)
    return sorted(found)


def _save_json(path: Path, data: dict) -> None:
    """Atomic JSON write — write to a UNIQUE .tmp, then rename.

    The tmp name carries pid and thread id. The old ``path.with_suffix(".tmp")``
    was shared by every concurrent writer of the same artifact, so a peer's
    rename could move this call's half-written payload into place, or delete it
    mid-write (the 98 FileNotFoundError in the D-103 drive).
    """
    tmp = path.with_name(
        f"{path.name}.{os.getpid()}.{threading.get_ident()}{_TX_TMP_SUFFIX}"
    )
    try:
        tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        tmp.rename(path)
    finally:
        # A failed write must not leave a stray sidecar behind; the rename
        # consumes it on the success path, so this only fires on error.
        if tmp.exists():
            tmp.unlink(missing_ok=True)


@contextmanager
def _document_transaction(path: Path) -> Iterator[dict]:
    """Exclusive read-modify-write over a run-artifact JSON document.

    Yields the document as a dict. Mutate it in place; it is written back
    through ``_save_json`` on clean exit. An exception inside the block
    propagates and NOTHING is written, so a failed mutation cannot leave a
    half-updated artifact.

    Re-entrant per path: a nested transaction on a path this thread already
    holds yields the same in-flight dict and defers the write to the outermost
    exit. Without that, ``foundry_mark_phase_complete``'s existing nested
    state.json write would block forever on its own flock.

    A block that mutates NOTHING writes nothing: the document is snapshotted on
    entry and compared on exit. That keeps a no-op caller byte-identical on disk
    (verdict synthesis must not rewrite verdicts.json when every requirement
    already has a row), keeps mtimes honest, and means a corrupt artifact a
    caller only read is left intact rather than silently replaced by ``{}``.

    A malformed document otherwise reads as ``{}`` (``_load_json``'s tolerance)
    rather than raising, so a writer is never bricked by one. Callers that must
    refuse instead run ``_artifact_guard`` first.
    """
    held = getattr(_ARTIFACT_TX, "docs", None)
    if held is None:
        held = _ARTIFACT_TX.docs = {}
    key = str(path)
    if key in held:
        # Already open on this thread — same document, one write at the end.
        yield held[key]
        return

    lock_path = path.with_name(path.name + _TX_LOCK_SUFFIX)
    with _ARTIFACT_LOCK:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        # The lock handle exists for its FILE DESCRIPTOR and nothing else --
        # `flock` below is its only use, and no byte of this file is ever read
        # or written through it. Opened in binary because that is what is true:
        # a text handle claims a decode that never happens, and a claim a
        # reader has to disprove is the same cost as one that is wrong.
        with open(lock_path, "ab+") as lock_file:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            try:
                data = _load_json(path)
                before = json.dumps(data, indent=2, sort_keys=True)
                held[key] = data
                yield data
                if json.dumps(data, indent=2, sort_keys=True) != before:
                    _save_json(path, data)
            finally:
                held.pop(key, None)
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


@contextmanager
def _artifact_lock(path: Path) -> Iterator[None]:
    """Exclusive critical section over a run artifact that is NOT a document.

    ``_document_transaction`` is the read-modify-write for a JSON document. A
    run also holds APPEND-ONLY artifacts — ``handoffs.jsonl`` and its
    ``handoffs.md`` mirror — which have no document to read back and no
    snapshot to compare, and which therefore had no locked primitive to reach
    for at all. fallout D-123 is what that absence cost: ``foundry_handoff``
    was the one run-artifact writer in this package holding no lock, appending
    the human mirror through four to six separate ``f.write`` calls and
    bootstrapping its header on a bare ``not md_path.exists()`` — a TOCTOU
    between the check and the first write, in a package whose teammates share
    one working tree and whose GI-003 record added a SECOND writer to that one
    function.

    Same domain as the transaction above, deliberately: ``_ARTIFACT_LOCK``
    orders threads, the flock sidecar is spelled through ``_TX_LOCK_SUFFIX``,
    and the held map lives on ``_ARTIFACT_TX``. A second domain over the same
    documents is the D-010 shape, and a lock that excludes only writers who
    remembered to use it is not a lock.

    ONE LOCK FOR A RECORD THAT SPANS TWO FILES. A caller writing two channels
    that must agree takes the lock on ONE of them — the machine-read channel,
    by convention, since that is the one a reader joins on — and holds it
    across both. Locking each file separately would order each write and still
    let two records interleave between the channels, which is the failure the
    lock is being taken against.

    Re-entrant per path, against BOTH primitives: a path this thread already
    holds — through a nested ``_artifact_lock`` or through an open
    ``_document_transaction`` — yields immediately rather than blocking on a
    flock this thread will not release until the outer block exits. The one
    nesting this does NOT compose is a ``_document_transaction`` opened INSIDE
    an ``_artifact_lock`` on the same path; no caller does that (a document and
    an append-only channel are different files), and the day one wants to, the
    transaction grows the same check rather than this one growing a special
    case.
    """
    held_docs = getattr(_ARTIFACT_TX, "docs", None) or {}
    held_locks = getattr(_ARTIFACT_TX, "locks", None)
    if held_locks is None:
        held_locks = _ARTIFACT_TX.locks = set()
    key = str(path)
    if key in held_locks or key in held_docs:
        # Already exclusive on this thread for this path — same critical
        # section, and re-taking the flock would block on ourselves.
        yield
        return

    lock_path = path.with_name(path.name + _TX_LOCK_SUFFIX)
    with _ARTIFACT_LOCK:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        # Binary, and never read through: see the transaction above for why the
        # handle exists for its file descriptor and nothing else.
        with open(lock_path, "ab+") as lock_file:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            held_locks.add(key)
            try:
                yield
            finally:
                held_locks.discard(key)
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


# --------------------------------------------------------------------------- #
# THE PUBLISHED DIGEST SPELLING — ONE RULE, TWO ARITIES (fallout D-128 / D-117)
#
# These two were defined at the top of ``tools/foundry_handoff.py``, and both
# layers read them: ``foundry_handoff`` and ``foundry_validate`` from the
# lifecycle side, ``tools/evidence.py`` from the verifier side, and — since
# fallout D-117 bound the F0.7 marker to the matrix it was computed from —
# ``intent_coverage`` and ``foundry_validate``'s F0.9 rung need the FILE digest
# in one spelling on both sides of that comparison.
#
# GI-033's arithmetic is what decides the home, in the same words
# ``_spec_requirement_ids`` below records for the requirement-id climb: the two
# layers are mutually unreachable at module top, so a symbol read from BOTH can
# live in neither and belongs in a leaf. Reaching a digest helper out of a
# lifecycle module is the Holmes `pkg-1` disease exactly — "every sibling that
# imports the orchestrator at module top wants a leaf utility".
#
# THE PAIR MOVES TOGETHER because it is one rule. Each docstring names the
# other as the wrong choice for the other's input, and D-108 is the defect that
# pairing exists to prevent; splitting them across two modules would leave each
# warning pointing somewhere else. ``foundry_handoff`` imports both back and
# keeps using them, so every existing caller — including the one line in
# ``evidence.py`` this casting may not edit — still resolves while that
# module's owner repoints it at this leaf.
# --------------------------------------------------------------------------- #


def _hash_file(path: Path) -> str | None:
    """The published spelling of a FILE's digest: sha256 over its bytes.

    ``None`` when there is no file to hash. This is the value a shell's
    ``sha256sum`` / ``shasum -a 256`` prints (truncated to the published 16
    characters), which is why it — and never ``_hash_str`` on decoded text —
    is what ``check_reported_prompt_hash`` compares a teammate's report
    against (D-108). Text and bytes differ for any file whose line endings are
    not already LF, and only one of the two can be computed from outside this
    process.
    """
    if not path.exists() or not path.is_file():
        return None
    h = hashlib.sha256(path.read_bytes()).hexdigest()
    return f"sha256:{h[:16]}"


def _hash_str(text: str) -> str:
    """The published spelling of a STRING's digest.

    For values that are strings in the first place — a handoff id assembled
    from a timestamp and an event, an evidence log's redacted body. NOT for
    hashing a file: see ``_hash_file`` and D-108. Passing decoded file text
    here re-introduces the newline-translation gap that made an honest
    teammate's report of a CRLF prompt read as stale.
    """
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _text_problem(path: Path) -> str | None:
    """The named reason ``path`` is not readable UTF-8 text, or None.

    The non-JSON rung of the same ladder ``_document_problem`` occupies. An
    absent file is not a problem; an undecodable one is, and it must be named.

    Both rungs now share ONE read (D-137): ``read_document`` is
    ``read_text_file`` plus the decode, so a text artifact and a JSON artifact
    cannot disagree about whether the same file is readable.
    """
    return read_text_file(path)[1]


# D-138 — A DERIVED GUARD IS ONLY AS TOTAL AS ITS LEAST-DERIVED AXIS.
#
# This table used to be `{".json": ..., ".md": ...}` and a suffix outside it was
# skipped with a bare `continue`. That is the escalated class living inside the
# fix written to make the escalated class unrepresentable: membership was
# derived on the axis the defect was reported on (WHICH FILES in the top level)
# and hand-bound on the two axes it was not (WHICH SUFFIXES decode, and WHICH
# subdirectory files count — exactly one, appended by name).
#
# What that cost: `handoffs.jsonl` and `spawns.log` are artifacts foundry writes
# ITSELF, both present in every live run dir, both outside the table. A run dir
# holding a corrupt spawns.log returned NO problems, Foundry-Next named nothing,
# and Foundry-Liveness raised UnicodeDecodeError across the MCP boundary — D-098
# and D-129's harm re-created on an unenrolled file TYPE.
#
# All three axes are derived now:
#
#   WHICH FILES  — `rglob`, not `glob`. Every artifact under the run dir,
#                  at any depth. `castings/manifest.json` is no longer appended
#                  by name, and neither are traces/, proofs/, assay/, temper/
#                  or test_observations/, which were not members at all.
#
#   WHICH TYPES  — derived from CONTENT, not from a suffix table. An unknown
#                  suffix is no longer skipped: every artifact must at minimum
#                  decode as UTF-8 unless its own bytes say it is binary. So
#                  .jsonl, .log and the extensionless markers are covered the
#                  day they are written, with nothing to enrol.
#
#   WHICH RUNGS  — `.json` keeps a STRICTER rung on top of the text floor (it
#                  must also be a JSON mapping). That is the one remaining
#                  suffix key, and it is not a membership gate: a suffix absent
#                  from it falls THROUGH to the floor rather than out of the
#                  scan. This is the property the old table lacked, and the
#                  whole difference between an extension point and a hole.

#: Suffixes carrying a rung ABOVE "must be readable UTF-8". Not a membership
#: list — see the note above. Adding one TIGHTENS the check for that suffix;
#: removing one leaves the artifact on the text floor, never unchecked.
_STRICT_ARTIFACT_DECODERS = {
    ".json": _document_problem,
}

#: Leading bytes of the binary artifact types a run directory legitimately
#: holds — SIGHT screenshots, the odd PDF or archive.
#:
#: THE DIRECTION OF THIS TABLE IS THE WHOLE POINT. It is an EXEMPTION list, so
#: an unrecognised type falls INTO the guard and is REPORTED, never out of it
#: and skipped. That is the opposite of the suffix table it replaces, whose
#: unrecognised members hit a bare `continue` — and it is what D-138 asks for
#: in as many words: a new artifact type must announce itself rather than be
#: passed over. Over-reporting is recoverable (the operator moves the file);
#: under-reporting is D-098, D-129 and D-138.
#:
#: A CONTENT SNIFF WAS TRIED HERE FIRST AND IS WRONG. "A NUL byte in the first
#: 8000 means binary" is git's heuristic, and it reads a text artifact whose
#: CORRUPTION contains a NUL as a binary file to be skipped — silently losing
#: exactly the artifact this guard exists to name. Driven: a `.trace-clean-at`
#: overwritten with b"\xe9\x00..." came back clean.
_BINARY_ARTIFACT_SIGNATURES = (
    b"\x89PNG\r\n\x1a\n",
    b"\xff\xd8\xff",
    b"GIF8",
    b"BM",
    b"RIFF",
    b"%PDF-",
    b"PK\x03\x04",
    b"\x1f\x8b",
)


def _is_compiled_python(head: bytes) -> bool:
    """True for a CPython ``.pyc`` header, of ANY interpreter version.

    Not a prefix, so it cannot live in the signature tuple above. CPython's
    magic is ``<2-byte version little-endian> + b"\\r\\n"``, an invariant across
    versions (``importlib.util.MAGIC_NUMBER`` is this interpreter's value of
    it) — which matters because a run directory holds ``.pyc`` written by
    whatever interpreters have touched it: the live thunder-viper dir carries
    cpython-311 and cpython-314 side by side.

    Real run directories DO hold these. Measured before this was added: 13
    reported artifacts in thunder-viper and 317 in grand-vulture, every one a
    ``__pycache__`` entry — a guard that refuses on those does not harden
    Foundry-Next, it bricks it.
    """
    return len(head) >= 4 and head[2:4] == b"\r\n"


def _is_binary_artifact(path: Path) -> bool:
    """True when ``path``'s header says it is a binary artifact, not text.

    An unreadable file is NOT binary — it falls through to the text check,
    which names it. Neither is a file of a binary type nobody enrolled: it is
    reported, which is the direction this table is built to fail in.
    """
    try:
        with path.open("rb") as handle:
            head = handle.read(max(len(s) for s in _BINARY_ARTIFACT_SIGNATURES))
    except OSError:
        return False
    return head.startswith(_BINARY_ARTIFACT_SIGNATURES) or _is_compiled_python(head)


def _manifest_shape_problem_lazy(manifest: object) -> str | None:
    """D-132's shared nested-shape validator, now defined one screen below.

    THE NAME IS A RESIDUE, AND SAYING SO IS CHEAPER THAN LEAVING IT TO MISLEAD.
    It was lazy because the validator lived in ``foundry_spawn``, which imports
    this module at module top, so reading back at module scope would have closed
    the graph. Concern C-060 moved the predicate here — ``manifest_shape_problem``
    at the foot of this file — because verifier modules were reaching it through
    a lifecycle module, which is GI-033's violation column. There is no import
    left to be lazy about; what is kept is the NAME, because the artifact-problem
    table below binds this function by it, and the rename is a separate decision
    from the repoint.

    D-134: the validator's membership used to be derived over ``foundry_spawn``
    's OWN functions rather than over every reader of castings/manifest.json in
    the package — so the four doors in that module were guarded while
    ``_check_sight_required``, the width module's since-deleted TRACE-skip check
    (fallout D-057), ``foundry_gate`` and both ``foundry_validate`` readers
    indexed the same records with a top-rung-only guard. ``castings: "nope"`` met ``.get()`` and raised AttributeError out of
    Foundry-Next, the mandatory handshake before EVERY phase transition.
    """
    return manifest_shape_problem(manifest)


#: The rung BELOW "is this a readable JSON object", for the artifacts that have
#: one: keyed by path relative to the run dir, because that is what identifies
#: an artifact rather than its type. Like ``_STRICT_ARTIFACT_DECODERS`` this
#: TIGHTENS; an artifact absent from it still gets the full JSON-object and
#: text floors, so it is an extension point and never a hole.
#:
#: This is what makes D-134's refusal reach the operator in the house shape:
#: `_artifact_guard` runs at the top of every MCP entry point, so a manifest
#: whose records are unusable is named in `corrupt_artifacts` at every door at
#: once, rather than at each reader that happens to remember to look.
_ARTIFACT_RECORD_RUNGS = {
    ("castings", "manifest.json"): _manifest_shape_problem_lazy,
}


def _artifact_problem(path: Path, relative: tuple[str, ...] = ()) -> str | None:
    """The named reason this artifact is unreadable, or None.

    One decision per artifact, taken from the artifact rather than from a table
    of the types someone remembered.
    """
    strict = _STRICT_ARTIFACT_DECODERS.get(path.suffix.lower())
    if strict is not None:
        if (problem := strict(path)):
            return _unless_it_vanished(path, problem)
        rung = _ARTIFACT_RECORD_RUNGS.get(relative)
        return rung(_load_json(path)) if rung is not None else None
    if _is_binary_artifact(path):
        return None
    return _unless_it_vanished(path, _text_problem(path))


def _unless_it_vanished(path: Path, problem: str | None) -> str | None:
    """Drop a named problem for a file that is no longer there (D-007).

    ``read_text_file`` already rules that "an ABSENT file is not a problem — a
    run legitimately has artifacts it has not written yet". It decides that from
    a ``path.exists()`` taken BEFORE the read, so a file removed between the two
    lands in its OSError arm and is named as unreadable instead. This function
    holds the same rule for a file that became absent DURING the scan, which is
    the only way the two answers can disagree.

    Driven, at the site that made it matter: ``_run_artifact_problems`` lists the
    whole run dir and then reads each entry, while a peer process is renaming
    ``_save_json``'s sidecar into place. 62 of 23120 scans against a run with one
    concurrent writer returned "stream-rollup.json.<pid>.<tid>.tmp could not be
    read (FileNotFoundError)" — and since ``_artifact_guard`` runs at the top of
    EVERY MCP entry point, that named a healthy run corrupt and refused whatever
    tool the lead had called. F2 runs 4-8 parallel streams by design, so the
    concurrency is the designed path; the excluded-sidecar rule above is the
    other half, and this is the half that holds for any transient nobody has
    thought of yet.
    """
    if problem is not None and not path.exists():
        return None
    return problem


# Artifacts whose corruption the guard reports. DERIVED, not a hand-kept list:
# every top-level file in the run dir with a decoder, plus the castings
# manifest. A new artifact is covered the moment it is written, which is the
# property the hand-kept marker lists in this module have repeatedly failed to
# hold.
#
# D-129 — the glob was `*.json` alone, and that was the whole defect. D-098
# made `_read_text` tolerant so an undecodable directives.md stopped RAISING
# out of Foundry-Next, and added NO guard half: the file was simply read as
# EMPTY. So one stray non-UTF-8 byte silently voided a LIVE URGENT directive
# ("STOP the cast wave, the spec changed"), Foundry-Next reported
# directives=null with the text and the filename appearing nowhere, and
# Foundry-Clear then reported {"ok": true, "cleared_count": 0}, TRUNCATED the
# file and wrote no archive record. Destroyed data, reported as success.
#
# The fix is not "also check directives.md" — naming that file here is the
# escalated class, and the next non-JSON artifact would repeat it. The glob
# derives the members; the decoder table says how to read each type.
def _string_leaves(value: object) -> Iterator[str]:
    """Every string anywhere in a parsed JSON document, at any depth."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _string_leaves(item)
    elif isinstance(value, list):
        for item in value:
            yield from _string_leaves(item)


# D-145 — THE RUN'S INPUTS ARE NOT ALL INSIDE THE RUN DIRECTORY.
#
# `_run_artifact_problems` rglobbed the run dir and nothing else, so a file the
# run DECLARES and every door then READS was structurally outside every guard
# in this module. The spec is that file: `foundry_validate_castings` falls back
# to `Path(project_root) / state["spec_path"]` when the run dir holds no
# spec.md, and this very run's state.json carries
# "forge-specs/foundry-run-process-fixes/spec.md" — outside
# foundry-archive/thunder-viper/. Driven through the real _DISPATCH, one
# non-UTF-8 byte in that file raised UnicodeDecodeError across the MCP boundary
# while `_run_artifact_problems(fdir)` returned [] with the broken file sitting
# right there. The in-run-dir cases were all correctly refused; this was
# precisely the residue the rglob could not reach.
#
# THE AXIS IS DERIVED FROM THE STATE DOCUMENT, NOT FROM A KEY NAMED HERE.
# Naming `spec_path` would close this instance and leave the next declared
# input — a context file, a research root, a migration source — outside the
# guard on the day it is added, which is the escalated class exactly. Every
# STRING LEAF of state.json that resolves to an existing FILE is an input this
# run declared, so a key added to state.json is policed the day it is written
# and no reader has to remember to enrol it. A leaf that names no file is not
# an input and needs no classification; a leaf that names a DIRECTORY is a
# container, judged by what a reader opens inside it, not by itself.
def _declared_external_inputs(fdir: Path) -> list[Path]:
    """Every file OUTSIDE the run dir that the run's own state.json declares.

    Resolved against the project root, which is derived from ``fdir`` rather
    than passed in: ``get_run_dir`` builds ``<project_root>/<ARCHIVE_DIR>/<run>``,
    so the root is two levels up — and the ``ARCHIVE_DIR`` check below is what
    makes that a fact about the path rather than an assumption about it.
    """
    if fdir.parent.name != ARCHIVE_DIR or len(fdir.parents) < 2:
        return []
    project_root = fdir.parents[1]
    inside = fdir.resolve()
    found: dict[Path, None] = {}
    for leaf in _string_leaves(_load_json(fdir / "state.json")):
        if not leaf:
            continue
        try:
            candidate = (project_root / leaf).resolve()
            if not candidate.is_file():
                continue
        except (OSError, ValueError):  # a leaf that is not a usable path at all
            continue
        if candidate == inside or inside in candidate.parents:
            continue  # already a member through the rglob below
        found.setdefault(candidate, None)
    return sorted(found)


#: The document TYPES a reader in this package opens under a run directory.
#:
#: D-197 — the answer to "does a reader open this path" is not
#: ``_STRICT_ARTIFACT_DECODERS``. That table is the STRICT rung, and it holds
#: exactly ``.json`` because JSON is the only type with a rung above the text
#: floor. Every other run document is opened on the floor, so consulting the
#: strict table to decide DOCUMENT-NESS answered "only .json is a document" and
#: skipped six of the seven artifact types a run actually writes.
#:
#: Each member is here because a reader in this package opens that type under a
#: run directory, and each is derived from a filename this package DECLARES:
#:   ``.json``   state.json, defects.json, verdicts.json, escalation.json,
#:               stream-rollup.json, report.json, castings/manifest.json
#:   ``.jsonl``  handoffs.jsonl (HANDOFFS_FILENAME), spend.jsonl
#:               (SPEND_LEDGER_FILENAME), progress/<agent>.jsonl
#:               (``foundry_spawn._read_progress_ledger``)
#:   ``.log``    spawns.log (SPAWNS_FILENAME), evidence/*.log (the sweep's
#:               ``# evidence-cmd:`` reader)
#:   ``.md``     directives.md, forge-log.md, spec.md, REPORT.md
#:               (REPORT_MD_FILENAME), directives-cleared.md
#:               (DIRECTIVES_CLEARED_FILENAME), castings/casting-N-prompt.md
#:   ``.txt``    shared/global_invariants.txt and shared/mandatory_rules.txt,
#:               which a casting prompt reads; both present in the live corpus
#:
#: THE HOLE THIS SHAPE HAS, AND WHAT WATCHES IT. Membership here means a new
#: document TYPE is unguarded until it is enrolled — the direction
#: ``_BINARY_ARTIFACT_SIGNATURES`` warns about. It is accepted rather than
#: inverted because the inverse was measured and is worse: reporting every
#: suffixed directory refuses on ``.dist-info`` (30 of them in this repo's own
#: virtualenvs) and on ``node_modules/socket.io``, and a false refusal here
#: locks EVERY door at once — D-195 exactly. What keeps the hole from being a
#: silent one is
#: ``test_the_document_suffix_table_covers_every_declared_run_artifact``, which
#: derives the expected set from the ``*_FILENAME`` and ``*_MARKER`` constants
#: the package declares and fails the day a new artifact is declared without
#: being enrolled on the axis its name falls on.
_RUN_DOCUMENT_SUFFIXES = frozenset(_STRICT_ARTIFACT_DECODERS) | {
    ".jsonl",
    ".log",
    ".md",
    ".txt",
}


# --------------------------------------------------------------------------- #
# D-201 — THE SUFFIX-LESS HALF OF THE SAME QUESTION.
#
# D-197 widened the SUFFIX TABLE above and left the QUESTION keyed on the
# suffix, so ``_is_document_position`` still asked one axis of a two-axis
# predicate. ``Path(".last-next-at").suffix`` is ``""`` and ``""`` is in no
# table, so every sentinel this package writes and READS BACK was walked past
# and its door died with ``call_tool``'s unhandled-error banner instead of the
# guard's named refusal. Driven at the wire at f5b487b through
# ``server.call_tool``: a DIRECTORY on ``.last-next-at`` made ``Foundry-Next``
# return "Foundry-Next failed: IsADirectoryError ..." with ``corrupt_artifacts``
# ABSENT — while CT-012's errors cell for Foundry-Next reads, verbatim, "none;
# never blocks"; a directory on ``.inspect-boundary-sha`` let
# ``Foundry-Phase('inspect_start')`` transition with the GI-002/ST-005 boundary
# sweep never named. The suffixed controls in the same tree (``spawns.log``,
# ``directives.md``, ``handoffs.jsonl``, ``shared/global_invariants.txt``) were
# all correctly named, which is what makes the missing axis the finding rather
# than the guard as a whole.
#
# ONE DECLARATION, WHICH THE READERS ALSO USE. A frozenset of inline literals
# here would answer the guard and catch nothing: the sixteen sentinel names were
# spelled as bare literals at thirty-four call sites, so the set could list them
# all today and drift from the next one silently — the self-vouching shape the
# pin exists to prevent. Each name is therefore a ``*_MARKER`` constant (the
# spelling ``INSPECT_BOUNDARY_SHA_MARKER`` already established in this module),
# every reader in this file spells the constant, and
# ``test_the_document_suffix_table_covers_every_declared_run_artifact`` derives
# its expectation from every ``*_FILENAME`` and ``*_MARKER`` constant the
# package declares — a suffixed value must be in the table above, a suffix-less
# one must be in the set below.
#
# WHY THE STREAM FAMILY IS DERIVED AND NOT LISTED. ``.trace-complete`` and
# ``.prove-complete`` are two members of ``f".{stream}-complete"`` over
# ``STREAM_WIRE_IDS``; listing the members would leave the next wire id
# unguarded on the day the vocabulary gains it, which is the hand-kept-list
# defect D-129 and D-138 were both filed on.
#
# WHY THIS DOES NOT REOPEN D-195. Membership is by exact BASENAME, not by
# punctuation: ``14.0.0``, ``.hypothesis`` and ``node_modules`` are in neither
# axis, so the version-number scratch directory stays silent exactly as it does
# today.

#: The per-cycle stream roll-up document (fallout GI-033, concern C-018).
#:
#: A RUN-ARTIFACT FILENAME, WHICH IS WHAT THIS LAYER IS FOR. It was declared in
#: ``tools/orchestration/streams.py`` — a lifecycle module — and read by two
#: VERIFIER modules, ``evidence_boundary`` (which walks the roll-up to decide
#: whether a boundary sweep is owed) and ``width`` (which writes the cycle
#: roll-up the width decision produces). A verifier reaching into the lifecycle
#: layer for a filename is two rows of GI-033's layering debt, and both are one
#: constant: the NAME of a document is not lifecycle knowledge, it is the same
#: kind of fact as the marker names below it.
#:
#: A SECOND DECLARATION STANDS WHILE THIS IS BEING REPOINTED, and it is bounded
#: and deliberate. ``streams.py`` still declares the same value until the
#: casting that owns ``tools/orchestration/`` deletes it and points its readers
#: here; until then the package-wide single-definition guard reports the pair,
#: which is the transient window a lead ruling accepted rather than a state
#: anyone should preserve. The value is byte-identical to the one it will
#: replace, so nothing can read a different document through either name.
ROLLUP_FILENAME = "stream-rollup.json"


#: The HEAD recorded at each INSPECT boundary, so the next crossing knows what
#: "since the last boundary" means. A marker rather than a state key because
#: ``TRACE_CLEAN_AT_MARKER`` is the established shape for exactly this fact, and
#: the two are read by the same fallback ladder in the width module.
INSPECT_BOUNDARY_SHA_MARKER = ".inspect-boundary-sha"

#: HEAD at the CAST→INSPECT crossing, and at the last clean TRACE — the two
#: rungs of the "since when" ladder ``INSPECT_BOUNDARY_SHA_MARKER`` heads.
CAST_BASELINE_SHA_MARKER = ".cast-baseline-sha"
TRACE_CLEAN_AT_MARKER = ".trace-clean-at"

#: Phase-progress sentinels: written by a transition, read by the gate and by
#: ``_compute_next_action`` to decide what the lead is told to do next.
CAST_COMPLETE_MARKER = ".cast-complete"
INSPECT_CLEAN_MARKER = ".inspect-clean"
TASKS_GENERATED_MARKER = ".tasks-generated"
RESEARCH_SKIPPED_MARKER = ".research-skipped"

#: The Foundry-Next handshake pair: the ordering token a transition consumes,
#: and the stall clock CT-012's detector reads.
NEXT_ACTION_CALLED_MARKER = ".next-action-called"
LAST_NEXT_AT_MARKER = ".last-next-at"

#: The passing-gate stamp ``foundry_next_action`` reads back to advance the
#: guidance state (ST-011: the gate no longer unlinks the ordering token, so
#: this is how Gate-then-Phase is recognised).
GATE_PASSED_MARKER = ".gate-passed"

#: Written by sibling modules and read back by their own doors. Declared here
#: because ``_run_artifact_problems`` is what has to know a directory may not
#: occupy them; the owning modules still spell the literal (see
#: `foundry-archive/daring-orca/concerns.md`).
VALIDATE_PASSED_MARKER = ".validate-passed"          # tools/foundry_validate.py
INTENT_CLEAN_MARKER = ".f07-intent-clean"            # tools/intent_coverage.py
LEGACY_RUN_POINTER_MARKER = ".foundry-dir"           # tools/foundry.py


def _stream_marker(stream: str) -> str:
    """The completion sentinel ``stream`` writes, in the ONE spelling.

    Five call sites spelled ``f".{stream}-complete"`` and four more spelled two
    of its members as bare literals, so the guard's derived family and the
    readers agreed only by inspection. One function, and they cannot disagree.
    """
    return f".{stream}-complete"


#: Every suffix-less name a reader in this package opens under a run directory.
#: The second axis of ``_is_document_position``; the family at the end is
#: derived so a new stream wire id is covered the day the vocabulary gains it.
_RUN_MARKER_NAMES = frozenset({
    INSPECT_BOUNDARY_SHA_MARKER,
    CAST_BASELINE_SHA_MARKER,
    TRACE_CLEAN_AT_MARKER,
    CAST_COMPLETE_MARKER,
    INSPECT_CLEAN_MARKER,
    TASKS_GENERATED_MARKER,
    RESEARCH_SKIPPED_MARKER,
    NEXT_ACTION_CALLED_MARKER,
    LAST_NEXT_AT_MARKER,
    GATE_PASSED_MARKER,
    VALIDATE_PASSED_MARKER,
    INTENT_CLEAN_MARKER,
    LEGACY_RUN_POINTER_MARKER,
}) | {_stream_marker(stream) for stream in STREAM_WIRE_IDS}


def _is_document_position(candidate: Path) -> bool:
    """Is ``candidate`` a path a reader OPENS as a document?

    Asked of directories only — every FILE in the run tree is a member, on the
    text floor at worst, and that derivation is D-138's and is not this
    function's business.

    D-195 — A DIRECTORY WAS CLASSIFIED BY THE SHAPE OF ITS NAME, AND A VERSION
    NUMBER HAS THAT SHAPE.
    -------------------------------------------------------------------------
    The rule here was ``candidate.is_dir() and not candidate.suffix`` — walk
    past a directory only when its basename holds no dot. ``Path("14.0.0")
    .suffix`` is ``".0"``, so hypothesis's unicode cache at
    ``test_observations/generated/.hypothesis/unicode_data/14.0.0/`` was NOT
    walked past: it was opened as a run document, the read raised
    IsADirectoryError, and ``_artifact_guard`` — which runs at the top of all
    fourteen MCP entry points in this module — named a healthy run corrupt and
    refused every door at once. Driven over the real transport: Foundry-Next,
    Foundry-Spend and Foundry-Stream all returned "Run artifacts cannot be
    read: 14.0.0 could not be read (IsADirectoryError ...)" on a run whose every
    state document was valid and readable. Hit three times — twice by PROVE, and
    once by the lead on the live archive, who cleared it by deleting the
    directory by hand. ``traces/scratch/v1.2``, ``proofs/cache/node_modules/
    pkg-1.0.0`` and ``unicode_data/15.1.0`` are the same defect; the
    no-suffix control ``.hypothesis/examples`` was correctly silent, which is
    what makes the discriminator the basename rather than anything about the
    path.

    A DOT IS NOT A DOCUMENT TYPE. The guard's own decoder table is its
    statement of which names a reader opens AS a document — that is what
    ``_STRICT_ARTIFACT_DECODERS`` is for, and consulting it here is the same
    D-007 discipline that keeps the sidecar suffixes shared between the writers
    that create them and the scan that skips them: one declaration, so the two
    cannot drift. ``.0`` is not a type any reader in this package decodes;
    ``.json`` is, which is why D-140's ``state.json``-as-a-directory is still
    named (and ``defects.json``, and ``castings/manifest.json``) through the
    guarded ``_document_problem`` read that reports the OSError by name.

    WHY NOT EXCLUDE THE STREAM OUTPUT TREES INSTEAD. ``traces/``, ``proofs/``,
    ``temper/`` and ``test_observations/generated/`` are where all four observed
    instances sat, and naming them here would close those four and leave the
    fifth scratch directory outside the rule on the day a stream writes one —
    the hand-kept list D-129 and D-138 were both filed on. It would also be
    wrong in the other direction: ``castings/manifest.json`` is a real document
    position that is nested, so depth says nothing about whether a reader opens
    a path.

    WHY NOT DROP THE DIRECTORY CHECK ALTOGETHER. "Never classify a directory"
    fixes all four instances in one line and reopens D-140: a directory named
    ``state.json`` goes unnamed, ``_load_json`` returns ``{}``, and the doors
    act on a fabricated all-default state — reported as success, which is the
    harm the guard exists to prevent.

    D-197 — AND THEN THE TRUE-POSITIVE HALF WAS DELETED WITH THE FALSE ONE.
    ----------------------------------------------------------------------
    D-195's fix asked ``_STRICT_ARTIFACT_DECODERS`` — the STRICT rung, whose
    one member is ``.json`` — so every non-JSON document position went unnamed
    and the doors acted on the fabricated empty document D-140 describes,
    reported as success. Driven at the wire at d872362: a run with a readable
    ``spawns.log`` naming two dispatched castings and no spend record returned
    ``unreported_count 2``; with ``spawns.log`` occupying a DIRECTORY position
    the same ``Foundry-Next`` returned ``unreported_count 0`` with no error and
    no warning, while ``Foundry-Report`` on that same run refused naming
    "spawns.log could not be read (IsADirectoryError)" — one artifact, two
    doors, two stories, which is the shape D-140 was filed on. The same with
    ``directives.md``: an injected URGENT directive rendered by Foundry-Next,
    then silently gone the moment its name was occupied by a directory.

    THE QUESTION IS THE TYPE A READER OPENS, NOT THE RUNG IT OPENS IT ON.
    ``_RUN_DOCUMENT_SUFFIXES`` above is that set, and it is a superset of the
    strict table rather than a second copy of it, so the two cannot disagree
    about ``.json``. ``.0`` is still not a type any reader decodes — the D-195
    scratch directories stay silent — and ``.md``, ``.jsonl``, ``.log`` and
    ``.txt`` are types six of this run's seven artifact families are written
    in, so a directory occupying one of those names is named again.

    D-201 — AND THE QUESTION WAS STILL KEYED ON THE SUFFIX.
    ------------------------------------------------------
    A widened TABLE is not a widened QUESTION. ``Path(".last-next-at").suffix``
    is ``""``, ``""`` is in no table, and so every sentinel this package writes
    and reads back — the sixteen ``*_MARKER`` names declared above — was walked
    past by the same predicate that had just been fixed for suffixed names.
    Both axes are asked here now, and they are asked of different things: a
    SUFFIX for a typed document, an exact BASENAME for a sentinel. Keying the
    second on punctuation instead would be D-195 again, and keying the first on
    exact names would be the hand-kept list D-129 was filed on.
    """
    return (
        candidate.suffix.lower() in _RUN_DOCUMENT_SUFFIXES
        or candidate.name in _RUN_MARKER_NAMES
    )


def _run_artifact_problems(fdir: Path) -> list[str]:
    """Named problems for every unreadable run artifact, in stable order.

    Membership is the whole tree, not the top level plus one hand-named
    manifest — minus the one subtree that is not this run's artifacts at all,
    the checkouts ``_setup_worktree`` nests at ``fdir/worktrees`` (D-206;
    ``_run_artifact_candidates`` is where that is pruned and why). A DIRECTORY
    is a container and is walked past — except one occupying a path a reader
    OPENS as a document, which is a directory sitting where a reader will open a
    file (D-140: ``state.json`` as a directory passed the old ``is_file()``
    filter, so the guard saw nothing and the write raised IsADirectoryError
    instead of refusing by name). Which paths those are is
    ``_is_document_position``'s question, and D-195 is what answering it from
    the basename's punctuation cost.

    ...plus the run's DECLARED EXTERNAL INPUTS (D-145), because "the artifacts
    this run reads" and "the files under this run's directory" were never the
    same set, and the guard runs at the top of every MCP entry point precisely
    so no individual reader has to remember to look.
    """
    if not fdir or not fdir.exists():
        return []
    problems: list[str] = []
    for candidate in _run_artifact_candidates(fdir):
        if candidate.is_dir() and not _is_document_position(candidate):
            continue
        # D-007: a write primitive's own scaffolding is not one of this run's
        # artifacts. Nothing reads a `.tmp` sidecar or a `.lock` file back, both
        # are mid-flight by construction while a peer writes, and the guard's
        # hint — "repair or delete the named file" — is advice that races the
        # writer. Skipping them is what the guard MEANS by "run artifact", not a
        # tolerance added to quieten it.
        if _is_write_sidecar(candidate):
            continue
        if (problem := _artifact_problem(candidate, candidate.relative_to(fdir).parts)):
            problems.append(problem)
    for external in _declared_external_inputs(fdir):
        if (problem := _artifact_problem(external)):
            problems.append(problem)
    return problems


def _artifact_guard(fdir: Path) -> dict | None:
    """Named refusal when a run artifact cannot be read, else None.

    The house refusal shape: ``error`` names the offending FILES and what is
    wrong with each, ``hint`` names the action. Called at the top of the MCP
    entry points so the operator learns which file to repair instead of
    receiving a traceback from the handshake that was supposed to tell them.
    """
    problems = _run_artifact_problems(fdir)
    if not problems:
        return None
    return {
        "error": (
            "Run artifacts cannot be read: " + "; ".join(problems) + ". "
            "The run's state is unreadable, so this tool refuses rather than "
            "acting on a document it had to guess at."
        ),
        "hint": (
            "Repair or delete the named file(s) in the run directory, then "
            "retry. A deleted artifact is re-created empty; a corrupt one is "
            "not silently overwritten."
        ),
        "corrupt_artifacts": problems,
    }

def _spec_path_from(project_root, fdir: Path, state: dict) -> Path | None:
    """THE two rungs, over a run dir and a state document already read.

    Rung one is ``<run_dir>/spec.md``, the copy the run took for itself. Rung
    two is the ``spec_path`` the run recorded, resolved against
    ``project_root`` — the LIVE file, outside the run directory, which is why
    every reader of it owes D-145's tolerance.

    THE CLIMB IS HERE AND NOWHERE ELSE. "Which file is this run's spec" must
    have one answer: a surface that resolved it differently could count
    requirements the gate never saw, or refuse over a file the report never
    read. It takes the documents rather than reading them so a caller that has
    already opened ``state.json`` — the F0.9 gate has, in its prelude — reaches
    the one ladder without opening it twice.
    """
    spec_path = fdir / "spec.md"
    if spec_path.exists():
        return spec_path
    sp = state.get("spec_path", "")
    if sp:
        candidate = Path(project_root) / sp
        if candidate.exists():
            return candidate
    return None


def _resolve_spec_path(project_root: str) -> Path | None:
    """Resolve the active run's spec.md path, or None if unresolvable.

    Prefers ``<run_dir>/spec.md``; falls back to ``state.json['spec_path']``
    resolved against ``project_root``. Single code path shared by the
    requirement COUNT and the requirement-ID LIST so the two never drift — now
    literally so: the rungs are ``_spec_path_from``, which this reads the run
    dir and the state document for.
    """
    fdir = get_run_dir(project_root)
    if not fdir:
        return None
    return _spec_path_from(project_root, fdir, _load_json(fdir / "state.json"))


def _spec_requirement_ids(
    project_root, fdir: Path | None = None, state: dict | None = None
) -> tuple[str, set, Path | None, str | None]:
    """``(spec text, the ids it declares, the path read, a named problem)``.

    THE ONE CLIMB, AND WHAT IT IS FOR. Three surfaces need the ids the run's
    spec declares — F0.9's ownership dimensions, the F6 span section, and the
    DONE gate's requirement count and P3 verdict synthesis — and a second climb
    anywhere is a second answer to "which spec is this run's", free to read a
    different file than the gate refused on. Those surfaces sit in layers that
    may not import each other (GI-033: a verifier module and a lifecycle module
    have no legal edge between them), which is exactly what makes the ladder
    LEAF material rather than either one's private helper.

    ``fdir`` and ``state`` are optional: pass them when you have already read
    them and no second read happens, omit them for the active run. Both arms
    reach the same rungs.

    TOTAL, LIKE EVERY OTHER READ HERE. An absent spec answers ``("", set(),
    <the path tried>, None)`` rather than raising or refusing — a run legitimately
    has no spec before F0, and "no requirements declared" is what is true of it.
    The PATH TRIED comes back rather than ``None`` so a caller shaping a refusal
    out of ``problem`` has a file to name; ``_resolve_spec_path`` answers the
    narrower question "is there a spec at all" and keeps its ``None``.

    D-145: the second rung leaves the run directory, so ``_artifact_guard``'s
    rglob cannot reach the file and an unguarded ``read_text`` here raised
    UnicodeDecodeError across the MCP boundary. ``read_text_file`` is total and
    NAMES the file it cannot decode.
    """
    if fdir is None:
        fdir = get_run_dir(project_root)
        if not fdir:
            return "", set(), None, None
    if state is None:
        state = _load_json(fdir / "state.json")
    spec_path = _spec_path_from(project_root, fdir, state) or (fdir / "spec.md")
    spec_text, problem = read_text_file(spec_path)
    if problem is not None:
        return "", set(), spec_path, problem
    return spec_text, set(REQUIREMENT_ID_RE.findall(spec_text)), spec_path, None


# ── Hoisted here so both layers may reach them (concern C-060) ────────────
#
# WHY THEY MOVED. The boundary guard judged module-top edges only, and only
# among the orchestration modules, so real cross-layer edges passed unjudged
# (fallout D-080). Widening it to any depth and both directions makes it TRUE,
# and what it then reports is verifier modules reaching DOCUMENT READERS
# through lifecycle modules — GI-033's violation column verbatim. A document
# reader is leaf material, so the readers come here and both layers reach them
# legally instead of through each other.
#
# THE SECOND WINDOW, NAMED THE WAY THE FIRST ONE WAS. Between this commit and
# casting 2's repoint, `foundry_spawn` still defines the manifest shape rule and
# `orchestration/gates.py` still defines the requirement count. The copies here
# carry DIFFERENT SPELLINGS on purpose: the package-wide single-definition guard
# is keyed by NAME, so a same-name copy would turn the tree red the hour it
# landed, and this window is supposed to cost nothing until the originals are
# deleted and their callers repointed. When that happens, the two spellings
# below become the only ones and the old names stop existing — which is the
# point of spelling them differently rather than the price of it.


def count_spec_requirements(project_root: str) -> int:
    """How many requirement ids the run's spec declares.

    The count sat a layer above the ids, in `orchestration/gates.py`, where a
    lifecycle module had to reach a verifier module to ask it — so the answer
    to "how many requirements does this spec have" was owned by the module that
    guards the DONE transition rather than by the module that reads the spec.
    The ladder is already here (`_spec_requirement_ids`); this is the rest of
    it, and it is the whole body: sorting a set before counting it changes no
    count, so the count reads the set the ladder returns.

    D-150's ruling is unchanged and lives one layer further down still: which
    FAMILIES count is `schemas.vocab`'s declaration and never a literal here.
    """
    return len(_spec_requirement_ids(project_root)[1])

# fallout FR-063 / GI-033 (D-191, concern C-067) — THE THIRD HOISTED SYMBOL,
# AND THE ONE THE FIRST PASS COULD NOT TAKE.
#
# ``_hash_str`` and ``_hash_file`` came here out of ``tools/foundry_handoff.py``
# under C-067 because both layers read them. ``declared_requirement_ids`` was
# the third in that set and it stayed behind: the move needs the source module,
# this one, and the TWO AST pins that assert the function is defined exactly
# once — ``tests/test_handoff_records.py#test_neither_reader_derives_the_
# declared_set_inline`` and ``tests/test_evidence.py#test_no_reader_of_the_
# owned_set_derives_it_inline`` — and those scan sets name modules three
# castings own between them. A definition in a third module makes both pins
# read ZERO where they demand one, so the move was recorded as a deferral
# rather than half-taken.
#
# D-191 IS THAT DEFERRAL COMING DUE. ``tools/evidence.py`` is a verifier module
# by GI-033's dependency flow, by ``vocab.VERIFIER_PATH_PATTERNS`` and by the
# boundary guard's own ``_VERIFIER_MODULES``, and its module-top
# ``from foundry_mcp.tools.foundry_handoff import ...`` was the tree's one
# verifier-to-lifecycle crossing outside the named transitions-to-halt seam.
# GI-033's arithmetic leaves exactly one remedy — "the two layers are mutually
# unreachable, so a symbol read from BOTH can live in neither: it belongs in a
# leaf" — and a lazy import is explicitly not it: ``tests/orchestration/
# test_module_boundaries.py#test_no_verifier_module_reaches_a_lifecycle_module_
# lazily_either`` says a lazy import is still a reach, at no depth excepted.
#
# WHY THIS FUNCTION AND NOT ITS SIBLING. ``cited_requirement_ids`` answers the
# neighbouring question and STAYS in ``foundry_handoff.py``. The arithmetic
# above only reaches a symbol read from BOTH layers, and that sibling's two
# readers — the acceptance door and ``foundry_validate`` — are both lifecycle,
# so nothing forces it out of the module that uses it. The pair is split on the
# layering fact that separates them rather than on convenience, and both ends
# say so: ``_CROSS_REFERENCE_LINE_RE`` beside the sibling cites
# ``artifacts.py#_DECLARED_REQUIREMENT_ID_RE`` for the structural-markdown run
# the two rules read the same way.
#
# The body below is the body that was there, unchanged. This is a placement
# fix, and a placement fix that also edits behaviour is two changes wearing one
# defect id.


#: The DECLARATION grammar: a requirement ID in SUBJECT position on its line.
#:
#: Built from ``REQUIREMENT_ID_RE.pattern`` rather than re-typed beside it, so
#: the families stay the single axis this module already reads (D-150). What is
#: added here is only the POSITION: everything before the ID on the line must
#: be structural markdown — list bullets, blockquote markers, heading hashes,
#: table pipes, emphasis stars, whitespace — and nothing else. Prose before the
#: ID means the ID is being talked ABOUT, not declared.
_DECLARED_REQUIREMENT_ID_RE: re.Pattern[str] = re.compile(
    r"^[\s>|*+#-]*(" + REQUIREMENT_ID_RE.pattern + r")"
)


def declared_requirement_ids(block_text: str) -> list[str]:
    """The requirement IDs a casting is ANSWERABLE for, sorted and deduped.

    THE ONE DERIVATION (D-180)
    --------------------------
    This is the only answer in the tree to "which requirement IDs does this
    casting own", and it has two callers: the acceptance gate below, which
    demands a citation and an evidence binding for each of them, and
    ``foundry_validate``'s F0.9 coverage dimension, which asks whether every
    spec requirement landed in SOME casting. Those two used to compute it
    separately with a bare ``REQUIREMENT_ID_RE.findall`` over the whole block,
    which is not a second implementation of one rule so much as one rule with
    no owner: the gate and the validator could disagree about what a casting
    owns and nothing would ever compare them.

    WHY POSITION, AND NOT JUST THE FAMILY (D-180)
    ---------------------------------------------
    ``findall`` over the whole block cannot tell a requirement ASSIGNED to the
    casting from one merely QUOTED as an example inside another requirement's
    prose. Driven: casting 2's block mentions ``NFR-002`` exactly once, inside
    OT-005's own statement text ("...a LATENT filing citing NFR-002 with a
    scan-gap description is accepted"), and has no ``NFR-002`` requirement line
    of its own — yet the gate collected it as one of that casting's 45 demanded
    IDs and refused acceptance with ``EVIDENCE_REQUIREMENT_UNBOUND: NFR-002``
    for a requirement another casting owns. The teammate's only way through was
    to bind a knowingly false ``# evidence-for: NFR-002`` header to an
    unrelated log, which is a green gate recording a lie.

    A DECLARATION is the ID in subject position on its own line. All four
    shapes F0.5 DECOMPOSE emits qualify, because in each the ID is the first
    thing on the line that is not structural markdown::

        - **AC-006** [derived from A-008]: ...      bold bullet
        - FR-1: synthesized requirement            plain bullet
        | CT-001 | Foundry-Defect and ... |        typed-table row
        ### US-002: Every defect carries a tier    story heading

    ...and the two shapes that are NOT declarations stay out::

          - Maps to: US-003                        a cross-reference
        ...a LATENT filing citing NFR-002 with...  an example in prose

    WHY NOT NARROWER (the shape this rejects)
    -----------------------------------------
    Reading only ``- **ID**`` bullets — the obvious reading of "the
    requirement's own tag line" — drops every typed-table row and story
    heading. On casting 2 that is 10 of its 38 Locked requirement IDs,
    including every CT- contract and ST-009, silently no longer demanding
    evidence. Over-demanding costs a teammate an argument; under-demanding
    costs the run its gate, so the rule is anchored at the LINE, not at one
    markdown shape.

    Deduped and sorted because both callers compare it as a set and the gate
    reports it to the lead; order was never meaningful.
    """
    ids: set[str] = set()
    for line in block_text.splitlines():
        match = _DECLARED_REQUIREMENT_ID_RE.match(line)
        if match:
            ids.add(match.group(1))
    return sorted(ids)


#: Sentinel for a mapping key that must be PRESENT and non-null, whose value is
#: otherwise unconstrained. ``castings[].id`` and ``waves[].wave`` are the two:
#: every reader of this document locates its record by one of them, so an entry
#: without one is not a record the readers can address, whatever else it holds.
_REQUIRED_RUNG = object()

#: The manifest's structure AS THE PACKAGE'S READERS INDEX IT. Declared ONCE,
#: walked recursively by ``_document_shape_problem``, and consulted by every
#: manifest reader through ``manifest_shape_problem`` — so a corrupt document
#: produces the same named refusal at both spawn doors and the same silent
#: degrade in both tolerant readers BY CONSTRUCTION, not by four guards
#: agreeing with each other.
#:
#: This is the ESCALATED class (D-095 / D-098 / D-115 / D-132) answered
#: structurally. D-115 guarded the top-level container and stopped, so the
#: RECORDS the readers then index were never guarded: ``castings: "nope"``,
#: ``[1,2,3]`` and ``[null]`` each reached ``c.get("id")`` and raised
#: ``AttributeError`` out of Foundry-Spawn-Teammate while Foundry-Cast-Wave
#: tolerated the identical document — the D-097 asymmetry tell, two doors
#: disagreeing about one corrupt file. The fix is not a filter at the index
#: sites (that IS the class); it is one declaration of the shape.
#:
#: Grammar, read by ``_document_shape_problem``:
#:   ``[shape]``      a list; every element must satisfy the single inner shape
#:   ``{k: shape}``   a mapping; each key is OPTIONAL, and when present and
#:                    non-null its value must satisfy its shape
#:   ``_REQUIRED_RUNG`` the key must be present and non-null; value unconstrained
#:   ``None``         unconstrained from here down — an EXPLICIT statement that
#:                    the reader below this point is on its own. ``stream_skips``
#:                    entries are None because ``_skipped_stream_ids`` accepts
#:                    both a mapping and a bare string by documented contract and
#:                    isinstance-checks each entry itself.
#:
#: WHAT THIS COPY DOES NOT CARRY YET, said plainly rather than left to be found:
#: ``foundry_spawn``'s copy is pinned by a test that derives the indexed key set
#: from THAT module's AST, so a reader there cannot start indexing a key without
#: declaring it. This copy has no such pin, because the readers it serves are
#: spread across the package rather than gathered in one module. The casting
#: that deletes the original owes that pin a new subject.
_MANIFEST_DOCUMENT_SHAPE: dict = {
    "castings": [{"id": _REQUIRED_RUNG, "key_files": [None]}],
    "waves": [{"wave": _REQUIRED_RUNG, "casting_ids": [None]}],
    "stream_skips": [None],
}


def _document_shape_problem(value: object, shape: object, path: str) -> str | None:
    """The first named reason ``value`` does not satisfy ``shape``, else None.

    Recursive over the shape, so depth is a property of the DECLARATION rather
    than of this function: the rung below the one a defect was reported at is
    covered the moment it is declared, which is precisely what a hand-written
    ``isinstance`` chain at the reported rung cannot do.

    ``path`` is the dotted key path being validated, carried down so the
    message names WHICH rung failed. That is not cosmetic. The message this
    replaces was ``"manifest.json is not a JSON object — parsed as {type}"``,
    and reusing it one rung down produces the self-contradicting
    ``"manifest.json is not a JSON object — parsed as dict"`` — a refusal that
    sends the operator to look at a top-level object that is perfectly fine.
    Each branch below therefore states the shape IT expected.
    """
    if shape is None:
        return None

    if isinstance(shape, list):
        if not isinstance(value, list):
            return f"{path} is not a list — parsed as {type(value).__name__}"
        element = shape[0]
        for index, item in enumerate(value):
            problem = _document_shape_problem(item, element, f"{path}[{index}]")
            if problem is not None:
                return problem
        return None

    if not isinstance(value, dict):
        return f"{path} is not a JSON object — parsed as {type(value).__name__}"
    for key, sub in shape.items():
        member = value.get(key)
        if sub is _REQUIRED_RUNG:
            # No "parsed as" here, because there is nothing parsed to name —
            # so the actionable equivalent is the keys the object DOES carry.
            if member is None:
                return (
                    f"{path}.{key} is absent or null — {path} carries "
                    f"{sorted(str(k) for k in value)} and every reader of this "
                    f"manifest addresses its record by `{key}`"
                )
            continue
        if member is None:
            continue
        problem = _document_shape_problem(member, sub, f"{path}.{key}")
        if problem is not None:
            return problem
    return None


def manifest_shape_problem(manifest: object) -> str | None:
    """The named reason a parsed manifest is unusable, or None.

    The string half, beside ``_document_problem`` and ``_artifact_guard`` in
    this same module: the TOLERANT readers owe a degrade rather than a refusal,
    and must decide on exactly the same evidence the refusing doors use. Calling
    this rather than growing a private ``isinstance`` check is what keeps every
    reader of castings/manifest.json on one policy (D-132, D-134).

    IT IS HERE BECAUSE OF WHO ASKS. `orchestration/width.py` and
    `orchestration/transitions.py` are verifier modules and reached this
    predicate through `foundry_spawn`, which is lifecycle — the edge GI-033
    forbids. The predicate reads a DOCUMENT and knows nothing about a phase, a
    gate or a stream, so the leaf is where it always belonged; this module was
    already half-way to saying so, in ``_manifest_shape_problem_lazy``'s lazy
    back-reach for exactly this validator.
    """
    return _document_shape_problem(manifest, _MANIFEST_DOCUMENT_SHAPE, "manifest.json")


def _markdown_missing_report_sections(run_dir: Path) -> tuple[list[str], str | None]:
    """The required `## ` headings REPORT.md does not carry (D-015).

    Returns ``(missing_section_keys, problem)``. A heading counts as present
    when a line reading exactly `## <title>` is there, at any depth in the
    document and in any order — because `convergence GI-006` licenses the lead
    to APPEND prose, and appended prose can put arbitrary text between, above and below
    the generated headings without omitting one.

    The match is on the whole trimmed line rather than a prefix, so a lead's
    own `## Appendix` never counts as a generated section and a generated
    heading with a suffix bolted on ("## LATENT backlog (see below)") reads as
    the edit it is.
    """
    text, problem = read_text_file(run_dir / REPORT_MD_FILENAME)
    if problem is not None:
        return list(REPORT_REQUIRED_SECTIONS), problem
    if not (run_dir / REPORT_MD_FILENAME).exists():
        return (
            list(REPORT_REQUIRED_SECTIONS),
            f"{REPORT_MD_FILENAME} does not exist",
        )
    headings = markdown_headings(text)
    return [
        key
        for key in REPORT_REQUIRED_SECTIONS
        if f"## {REPORT_SECTION_TITLES[key]}" not in headings
    ], None


def report_document_status(run_dir: Path) -> dict:
    """`{'present': bool, 'missing_sections': [...]}` — the DONE gate's read.

    `convergence GI-006` gives the lead permission to APPEND prose and no
    permission to omit a section, and this is where the second half is checked. Both documents are
    read off disk every time rather than trusting anything the generator
    returned, because the gap the check exists to close is exactly the one
    where somebody edited a file after it was generated.

    IT IS HERE BECAUSE OF WHO ASKS (concern C-060). The DONE gate is a verifier
    module and this read lived in `foundry_report.py`, which is presentation —
    the edge GI-033's violation column names outright. It is a pure READ of two
    documents on disk and knows nothing the GENERATOR knows, which is what makes
    it leaf material while the generator stays where it is.

    BOTH DOCUMENTS, NOT JUST THE JSON (D-015)
    -----------------------------------------
    This read the JSON alone, and the docstring argued the case: the JSON's
    keys are machine-written and machine-read, so they answer the question
    exactly, while a markdown scan could be confused by a reflowed table.

    The argument was for the wrong question. `convergence GI-006`'s violation
    column names
    "a lead-authored REPORT.md that lacks the generated sections" in those
    words, and REPORT.md is the document a human actually reads — the JSON
    exists for tools. Driven: delete REPORT.md outright and the DONE gate still
    passed, so a run could reach DONE with no operator-readable report at all,
    which is the exact outcome `convergence GI-006` exists to prevent.

    So the JSON answers "which sections were generated" and the markdown
    answers "which sections a reader can still find", and `missing_sections` is
    the union. The confusability worry is handled by matching whole heading
    lines (see `_markdown_missing_report_sections`) rather than by not looking.

    A document that is absent, unreadable, or not an object reports every
    section missing. That is the honest answer: no section can be shown to be
    there. `problem` carries the reason when there is one, so a caller refusing
    the DONE transition can say whether the report was never generated or is
    corrupt.
    """
    run_dir = Path(run_dir)
    path = run_dir / REPORT_JSON_FILENAME
    md_path = run_dir / REPORT_MD_FILENAME
    md_missing, md_problem = _markdown_missing_report_sections(run_dir)

    data, problem = read_document(path)
    if problem is not None or not path.exists() or not data:
        json_problem = problem or (
            None if path.exists() else f"{REPORT_JSON_FILENAME} does not exist"
        )
        return {
            "present": False,
            "missing_sections": list(REPORT_REQUIRED_SECTIONS),
            "report_json": str(path),
            "report_md": str(md_path),
            "missing_from_json": list(REPORT_REQUIRED_SECTIONS),
            "missing_from_markdown": md_missing,
            "problem": json_problem or md_problem,
        }

    json_missing = [s for s in REPORT_REQUIRED_SECTIONS if s not in data]
    # Union, in REPORT_REQUIRED_SECTIONS order — a caller naming the missing
    # sections in a refusal reads them in the order the report declares them.
    both = set(json_missing) | set(md_missing)
    missing = [s for s in REPORT_REQUIRED_SECTIONS if s in both]
    return {
        "present": not missing,
        "missing_sections": missing,
        "report_json": str(path),
        "report_md": str(md_path),
        "missing_from_json": json_missing,
        "missing_from_markdown": md_missing,
        "problem": md_problem,
        "generated_at": data.get("generated_at"),
    }


# --------------------------------------------------------------------------- #
# fallout AC-061 / FR-063 / GI-033 (D-192, concern C-107) — THE FOUR THE
# ACCEPTANCE DOOR TOOK WITH IT WHEN IT LEFT THE LIFECYCLE LAYER.
#
# ``foundry_accept_casting`` now lives in ``tools/evidence.py``, beside the
# engine it runs. That is the whole of D-192: the door RUNS ``verify_evidence``,
# and GI-033's violation column is "any lifecycle module importing a verifier
# module", so a door that runs a verifier is a verifier — the edge could only be
# REVERSED or ELIMINATED, never relayed, and moving the door eliminates it.
#
# The move re-asked GI-033's arithmetic of everything the door reads. Four
# symbols changed answer, and every one of them changed it for the same reason:
# they are now read from BOTH layers, and "a symbol read from BOTH can live in
# neither" is the sentence the boundary guard's own failure message gives as the
# remedy.
#
#   ``_append_handoff_record`` — ``tools/concerns.py``,
#     ``orchestration/directives.py`` and ``foundry_handoff.py``'s own
#     ``record_lead_fix_handoff`` write through it from the lifecycle side; the
#     acceptance door writes through it from the verifier side.
#   ``record_handoff_event``  — the body of the ``Foundry-Handoff`` door,
#     which the acceptance door also calls. See its own docstring for why the
#     DOOR did not come with it.
#   ``check_reported_prompt_hash`` — ``orchestration/fix_gate.py`` (lifecycle)
#     and the acceptance door (verifier) are the two gates CT-011 / AC-030 exist
#     to keep identical.
#   ``foundry_spec_hash`` — the ``Foundry-Spec-Hash`` tool the registrar binds,
#     and the acceptance door's own spec resolution.
#
# The bodies below are the bodies that were in ``tools/foundry_handoff.py``,
# unchanged. This is a placement fix, and a placement fix that also edits
# behaviour is two changes wearing one defect id.
# --------------------------------------------------------------------------- #


def _append_handoff_record(
    fdir: Path,
    entry: dict,
    md_fields: list[tuple[str, str]],
) -> None:
    """Append one record to BOTH handoff channels — JSONL and the md mirror.

    WHY THIS IS A FUNCTION (GI-003)
    -------------------------------
    The audit log has two channels and they are only useful while they agree.
    While ``foundry_handoff`` was the sole writer, "append to both" was one
    block of straight-line code and could not disagree with itself. GI-003
    adds a SECOND writer — the server's own ``lead_fix`` record — and the
    moment there are two, "both channels, same format, header bootstrapped
    once" becomes a convention each is trusted to remember. That is the shape
    D-127 and D-119 both took (two doors, one remembered a step, the other did
    not), so it is factored here before it can happen a third time rather than
    after.

    ``entry`` is written to handoffs.jsonl verbatim, so each caller owns its
    own record shape — the lead_fix record carries defect_id/tier/file/
    line_count/files/test/regression_test/fix_commit as FIRST-CLASS keys, not
    prose squeezed into a summary field, because the F6 report reads them back
    by name. The
    two channels do NOT carry identical text: the JSONL keeps the raw values
    (None for an unavailable measurement) and ``md_fields`` carries the
    reader's rendering of them (D-074). ``md_fields``
    is the ordered human mirror; empty values are skipped, mirroring
    ``foundry._ledger_mirror``'s rule so an absent field prints nothing rather
    than an empty bullet.

    WHY THE LOCK (fallout D-123)
    ----------------------------
    Every other run-artifact write in this package goes through the leaf's
    flock'd primitives. This one went through a bare ``open("a")``, in a
    package whose teammates share one working tree and whose GI-003 record
    added a SECOND writer to this very function. The JSONL channel survived
    that on its own — one ``write`` per record, and an O_APPEND write of a
    short line does not interleave — but the md mirror is four to six separate
    ``f.write`` calls, so two concurrent ``grind_dispatched`` records could
    interleave mid-record in the human channel; and ``header_needed`` is a
    TOCTOU between the ``exists`` check and the first write, so both writers
    could emit the header.

    ONE LOCK FOR THE WHOLE RECORD, taken on the JSONL path and held across BOTH
    channels. It is a record that spans two files, so locking each file
    separately would order each write and still let two records interleave
    between the channels. The JSONL path is the lock's name because that is the
    channel a reader joins on — ``Foundry-Team-Down`` reads
    ``grind_dispatched`` out of it (FR-048) — and one name is what makes two
    writers exclude each other rather than agree by coincidence.
    """
    fdir.mkdir(parents=True, exist_ok=True)

    jsonl_path = fdir / "handoffs.jsonl"
    md_path = fdir / "handoffs.md"
    with _artifact_lock(jsonl_path):
        with jsonl_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")

        header_needed = not md_path.exists()
        with md_path.open("a", encoding="utf-8") as f:
            if header_needed:
                f.write("# Foundry Handoff Audit Log\n\n")
                f.write(
                    "Every transition between phases or artifacts is recorded here.\n\n"
                )
            f.write(f"## {entry['event']} — {entry['timestamp']}\n")
            for label, value in md_fields:
                if value:
                    f.write(f"- {label}: {value}\n")
            f.write("\n")


def record_handoff_event(
    event: str,
    source: str = "",
    destination: str = "",
    source_reread: bool = False,
    summary: str = "",
    information_loss: str = "",
    project_root: str = ".",
) -> dict:
    """Write one handoff record and return the ``Foundry-Handoff`` payload.

    THE BODY IS HERE AND THE DOOR IS NOT, AND THE SPLIT IS THE RESERVED TOKEN
    -------------------------------------------------------------------------
    ``foundry_handoff.py#foundry_handoff`` is the MCP door, and its first rung
    refuses ``HANDOFF_EVENT_LEAD_FIX`` in any spelling: only the server writes
    that record, through ``record_lead_fix_handoff``, on a successful
    Foundry-Fix (D-106 / D-227). That refusal is a POLICY OF THE DOOR, not of
    the writer — ``record_lead_fix_handoff`` reaches ``_append_handoff_record``
    below and writes exactly the record the door will not — so hoisting the
    door with the body would have moved a refusal into a layer that must not
    make it, and left the server's own writer refusing itself.

    So the door stays where the ``Foundry-Handoff`` tool has always been, keeps
    the rung, and calls this. Everything from the run-dir resolution onward is
    the body that was there, moved unchanged.

    WHY THE LEAF (fallout GI-033, D-192). ``tools/evidence.py#foundry_accept_casting``
    records ``evidence_verified`` and ``acceptance`` events, and it is a
    VERIFIER module — it runs ``verify_evidence``. The two layers are mutually
    unreachable at module top, so the shaping both of them need can live in
    neither of them; it belongs in a leaf, which is this module. The
    alternative — the door building its own entry from
    ``_append_handoff_record`` — is a second derivation of ``handoff_id``, the
    source/destination digests and the warning text, which is the duplication
    class this whole effort exists to close.
    """
    fdir = get_run_dir(project_root)
    if not fdir:
        return {"ok": False, "error": "No active foundry run"}
    if not fdir.exists():
        fdir.mkdir(parents=True, exist_ok=True)

    root = Path(project_root).resolve()
    source_path = (root / source) if source and not Path(source).is_absolute() else Path(source) if source else None
    dest_path = (root / destination) if destination and not Path(destination).is_absolute() else Path(destination) if destination else None

    source_hash = _hash_file(source_path) if source_path else None
    dest_hash = _hash_file(dest_path) if dest_path else None

    timestamp = datetime.now(timezone.utc).isoformat()
    handoff_id = _hash_str(f"{timestamp}|{event}|{source}|{destination}")

    entry = {
        "handoff_id": handoff_id,
        "timestamp": timestamp,
        "event": event,
        "source": source,
        "source_hash": source_hash,
        "destination": destination,
        "destination_hash": dest_hash,
        "source_reread": bool(source_reread),
        "summary": summary,
        "information_loss": information_loss,
    }

    warning = None
    if information_loss:
        warning = f"Information loss reported: {information_loss}. Lead must justify or re-decompose."
    if not source_reread and event in {"spec_to_casting", "spec_reread", "spec_to_decompose", "acceptance"}:
        warning = (warning + "; " if warning else "") + (
            f"source_reread=False for event '{event}'. Lead acted from memory, "
            f"not a fresh read of the source. Context rot risk."
        )

    # Both channels, through the writer the lead_fix record also uses, so the
    # two records cannot land in different files or in different formats.
    _append_handoff_record(
        fdir,
        entry,
        [
            ("handoff_id", f"`{handoff_id}`"),
            ("source", f"`{source}` ({source_hash or 'no file'})" if source else ""),
            (
                "destination",
                f"`{destination}` ({dest_hash or 'no file'})" if destination else "",
            ),
            ("source_reread", f"`{source_reread}`"),
            ("summary", summary),
            ("**information_loss**", information_loss),
            ("**WARNING**", warning or ""),
        ],
    )

    return {
        "ok": True,
        "handoff_id": handoff_id,
        "event": event,
        "source_hash": source_hash,
        "destination_hash": dest_hash,
        "source_reread": source_reread,
        "warning": warning,
        "log_entry": entry,
    }


def check_reported_prompt_hash(
    run_dir: Path,
    casting_id: int | str,
    reported_hash: str | None,
) -> dict | None:
    """None when the teammate's reported prompt hash is the file's, else a refusal.

    CT-011 / AC-030 — pointer dispatch hands the teammate a PATH and a HASH
    instead of the prompt text, and this is what turns that from advice into
    something checkable: only an agent that actually read the file can state
    the value back. Both consuming gates use this one function —
    ``foundry_accept_casting`` below and ``Foundry-Fix`` — because a hash rung
    that exists at one door and not the other lets an unread prompt through
    whichever door the lead happens to walk.

    THE COMPARISON VALUE IS THE PUBLISHED SPELLING, OVER THE FILE'S BYTES
    (D-108). ``"sha256:" + hexdigest()[:16]`` is byte-for-byte what
    ``foundry_spawn`` publishes as ``prompt_hash`` and what its dispatch block
    tells the teammate to state back "character for character". A bare
    hexdigest or the full 64 characters here would make every honest report a
    mismatch, so there is exactly one spelling in the package and this reads
    it rather than re-deriving one.

    WHAT IS HASHED IS THE BYTES, NOT THE DECODED TEXT (D-108). ``_hash_file``
    hashes ``path.read_bytes()``; ``_hash_str`` hashes ``text.encode("utf-8")``
    AFTER ``read_text_file`` has already translated newlines, which is what
    this rung used. On a prompt file written with CRLF line endings the two
    disagree — the text digest is taken over a document with every ``\\r``
    silently removed — and the teammate's own documented command
    (``sha256sum`` / ``shasum -a 256`` on the file) can only ever produce the
    BYTES digest. So the honest report of a CRLF prompt was refused as stale
    while nothing was stale. The bytes are what both sides can independently
    compute, so the bytes are what is compared.

    An unreadable or missing prompt file returns the house ``document_refusal``
    rather than None: nothing was compared, and "I could not read the file" is
    not the same answer as "the hashes agree". The DECODE guard below stays
    even though the digest no longer needs the text — a prompt this server
    cannot decode is one no teammate can be handed, and that answer is owed
    whether or not a hash would have matched.
    """
    prompt_path = run_dir / "castings" / f"casting-{casting_id}-prompt.md"
    if not prompt_path.exists():
        return document_refusal(prompt_path, f"{prompt_path.name} not found")

    _prompt_text, problem = read_text_file(prompt_path)
    if problem is not None:
        return document_refusal(prompt_path, problem)

    expected = _hash_file(prompt_path)
    if expected is None:
        # Lost between the exists check and the read: still "I could not read
        # the file", still not a match.
        return document_refusal(prompt_path, f"{prompt_path.name} not found")

    if reported_hash == expected:
        return None

    return {
        "ok": False,
        # The error TOKEN is unchanged from the inline rung this replaced.
        # Callers and tests key on it, and a rung that starts naming itself
        # differently the day it is shared is a behaviour change smuggled in
        # under a refactor.
        "error": "stale_prompt_hash",
        "hint": (
            f"Casting prompt hash mismatch. The file at {prompt_path.name} "
            f"hashes to {expected!r}; the value reported was "
            f"{reported_hash!r}. Call Foundry-Spawn-Teammate for a fresh "
            f"prompt hash — or, if the teammate reported it, have them re-read "
            f"the prompt file in full and state its hash character for "
            f"character. Only reading the file produces the right answer, "
            f"which is the point."
        ),
        "expected_hash": expected,
        "reported_hash": reported_hash,
    }


def foundry_spec_hash(project_root: str = ".") -> dict:
    """Return the current sha256 of spec.md. Lead calls this to obtain a
    hash that must be passed to `Foundry-Spawn-Teammate` and
    `Foundry-Accept-Casting`. The tools verify the hash matches the
    current file content, forcing the lead to actually Read the spec
    rather than relying on prior context.
    """
    fdir = get_run_dir(project_root)
    if not fdir:
        return {"ok": False, "error": "No active foundry run"}
    if (corrupt := _artifact_guard(fdir)):
        return {"ok": False, **corrupt}

    spec_path = fdir / "spec.md"
    if not spec_path.exists():
        state_path = fdir / "state.json"
        if state_path.exists():
            # D-130's class, found by the package-wide scan rather than by a
            # defect report: this was `json.loads(state_path.read_text(...))`,
            # so a corrupt state.json raised out of Foundry-Spec-Hash -- the
            # tool every Foundry-Spawn-Teammate and Foundry-Accept-Casting call
            # depends on -- as a traceback naming no file. Routed through the
            # orchestrator's tolerant loader; the guard above it names the file.
            state = _load_json(state_path)
            sp = state.get("spec_path", "")
            if sp:
                candidate = Path(project_root) / sp
                if candidate.exists():
                    spec_path = candidate

    if not spec_path.exists():
        return {"ok": False, "error": "spec.md not found in run directory or state"}

    h = _hash_file(spec_path)
    size = spec_path.stat().st_size
    mtime = datetime.fromtimestamp(spec_path.stat().st_mtime, tz=timezone.utc).isoformat()

    return {
        "ok": True,
        "spec_path": str(spec_path),
        "spec_hash": h,
        "size_bytes": size,
        "mtime": mtime,
        "instruction": (
            "Read the spec.md file now. Then pass the spec_hash to every "
            "Foundry-Spawn-Teammate and Foundry-Accept-Casting call. If you "
            "do not re-Read the spec first, you are acting from memory — "
            "this violates the context-rot prevention rule."
        ),
    }
