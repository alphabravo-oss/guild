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

THE ONE EXCEPTION TO THE IMPORT RULE, AND WHY IT IS NOT ONE.
``_manifest_shape_problem_lazy`` keeps its LAZY in-function import of
``foundry_spawn``. That laziness is what keeps the import graph acyclic —
``foundry_spawn`` imports the orchestrator at module top — and it is a call-time
edge, not a load-time one, so this module still loads with nothing above the
leaf layer in scope.

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

The carve has landed and the orchestrator is gone: these are the only
definitions now, which
``tests/orchestration/test_module_boundaries.py::test_the_helpers_group_zero_consolidated_have_exactly_one_definition``
holds for ``_save_json``, ``_document_transaction``, ``_resolve_spec_path``,
``_declared_external_inputs`` and ``_run_artifact_problems`` by name. The
paragraph above stays because the lock filename is still load-bearing for the
reason it names — two processes on one repository contend through that file —
and because a reader who finds a second copy one day should know this was
already paid for once.
"""

from __future__ import annotations

import fcntl
import json
import os
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from foundry_mcp.schemas.vocab import STREAM_WIRE_IDS
from foundry_mcp.tools.foundry_state import (
    ARCHIVE_DIR,
    get_run_dir,
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
#                         the operator. ``test_orchestrator_gates`` derives the
#                         entry-point set from server.py's _DISPATCH and fails on
#                         the next one added without it.
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
# This mirrors ``foundry.py``'s ``ledger_transaction`` BY CONVENTION, not by
# import: that primitive yields a list under a collection key, which fits
# defects.json and observations.json but not state.json / stream-rollup.json /
# escalation.json, whose payload is the document itself. The refusal shape and
# locking discipline are deliberately identical so a later consolidation is
# mechanical. The flock — not the RLock — is what binds across MODULES: two
# separate open() calls contend even inside one process, and verdicts.json has
# a writer in each module.
# --------------------------------------------------------------------------- #

# Threads inside one server process; the flock sidecar covers a second server
# process on the same repo. Re-entrant so a nested transaction on the same
# thread cannot deadlock against itself.
_ARTIFACT_LOCK = threading.RLock()

# path -> in-flight document, per thread. A nested transaction on a path already
# open on this thread yields the SAME dict and defers the write to the outermost
# exit, so nesting composes instead of deadlocking on our own flock.
_ARTIFACT_TX = threading.local()


#: D-137 — this module's ``_read_document`` was one of TWO byte-identical
#: bodies, and the second copy is the shape four prior fixes in this file left
#: behind every time. It is now the CANONICAL primitive in ``foundry_state``,
#: the package's leaf module, imported here under the same private name so
#: every existing caller and test keeps resolving. ``foundry.py`` keeps its own
#: body because THIS module imports THAT one (reading back would close a cycle
#: in the import graph) — and it passes the package-wide decode scan on its own
#: merits, which is the only reason it is allowed to stay.
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
    """D-132's shared nested-shape validator, reached without an import cycle.

    ``foundry_spawn`` imports THIS module at module top, so reading back at
    module scope would close the graph. The lazy in-function import is the
    house pattern already used by ``foundry_sync_defects`` for ``foundry.py``.

    D-134: the validator's membership used to be derived over ``foundry_spawn``
    's OWN functions rather than over every reader of castings/manifest.json in
    the package — so the four doors in that module were guarded while
    ``_check_sight_required``, ``_trace_skip_check``, ``foundry_gate`` and both
    ``foundry_validate`` readers indexed the same records with a top-rung-only
    guard. ``castings: "nope"`` met ``.get()`` and raised AttributeError out of
    Foundry-Next, the mandatory handshake before EVERY phase transition.
    """
    from foundry_mcp.tools.foundry_spawn import _manifest_shape_problem

    return _manifest_shape_problem(manifest)


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

#: The HEAD recorded at each INSPECT boundary, so the next crossing knows what
#: "since the last boundary" means. A marker rather than a state key because
#: ``_trace_skip_check``'s ``.trace-clean-at`` is the established shape for
#: exactly this fact, and the two are read by the same fallback ladder.
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

def _resolve_spec_path(project_root: str) -> Path | None:
    """Resolve the active run's spec.md path, or None if unresolvable.

    Prefers ``<run_dir>/spec.md``; falls back to ``state.json['spec_path']``
    resolved against ``project_root``. Single code path shared by the
    requirement COUNT and the requirement-ID LIST so the two never drift.
    """
    fdir = get_run_dir(project_root)
    if not fdir:
        return None
    spec_path = fdir / "spec.md"
    if spec_path.exists():
        return spec_path
    state = _load_json(fdir / "state.json")
    sp = state.get("spec_path", "")
    if sp:
        candidate = Path(project_root) / sp
        if candidate.exists():
            return candidate
    return None
