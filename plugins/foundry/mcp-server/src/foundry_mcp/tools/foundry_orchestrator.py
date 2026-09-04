"""Foundry orchestrator tools — phase enforcement, team lifecycle, and guided execution.

Replaces bash script enforcement with typed MCP tools that guide the lead agent
through the foundry loop. Every critical action (phase transitions, team management,
stream verification) goes through these tools instead of raw bash commands.

All operations are local file reads/writes. Zero API calls. Zero cost.
"""

from __future__ import annotations

import fcntl
import json
import os
import re
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from foundry_mcp.schemas.vocab import (
    DEFECT_SOURCE_IDS,
    DEFECT_TYPES,
    DELTA_CONDITIONAL_STREAMS,
    ESCALATION_STATUS_CLEARED,
    ESCALATION_STATUS_ESCALATED,
    FIX_AUTHORS,
    FULL_ROSTER_STREAMS,
    INSPECT_DELTA_RULE,
    INSPECT_MODES,
    LEAD_LANE_MAX_FILES,
    LEAD_LANE_MAX_LINES,
    LIVE_CLEAN_CYCLES_TO_CLEAR,
    OBSERVATION_CLASSES,
    PROVE_DELTA_SAMPLE_SIZE,
    REPORT_JSON_FILENAME,
    REPORT_MD_FILENAME,
    REQUIREMENT_ID_RE,
    RUN_PHASE_HALTED,
    SPEND_LEDGER_FILENAME,
    STREAM_WIRE_IDS,
    STRUCTURAL_PASS_BUDGET,
    TIER_UNKNOWN,
    canonical_defect_type,
    defect_tier,
    escalation_status as _escalation_status,
    is_test_file,
    is_verifier_path,
)
from foundry_mcp.tools.citation import iter_symbol_cites
from foundry_mcp.tools.foundry_state import (
    ARCHIVE_DIR,
    clear_active_run,
    get_run_dir,
    is_stream_record,
    read_document,
    read_text_file,
)

# D-127: the house rule is that a tool never raises across the MCP boundary, it
# returns {error, hint}. A ledger whose record container is the wrong shape is
# discovered INSIDE the locked primitive, several frames below the entry point,
# and trusting each entry point to remember a pre-flight check for it is what
# D-127 cost: this module's two ledger writers did not, so Foundry-Sync and
# Foundry-Fix shipped call_tool's unhandled-error banner instead of the house
# refusal — the very refusal shape D-095/D-096 were filed to establish.
#
# A module-top import, not the lazy one used for foundry_spawn: THIS module
# imports foundry.py (never the reverse), so there is no cycle to open.
from foundry_mcp.tools.foundry import ledger_refusals
from foundry_mcp.tools.display import foundry_hammer, FOUNDRY_SEP

# ANSI colors — shared with display.py. D-202: `_BLUE` had no reader in either
# copy, and a name-keyed reachability pin means one dead copy vouches for the
# other, so both went. Add a code back when a renderer needs it.
_RESET = "\033[0m"
_BOLD = "\033[1m"
_DIM = "\033[2m"
_RED = "\033[31m"
_GREEN = "\033[32m"
_YELLOW = "\033[33m"
_CYAN = "\033[36m"
_WHITE = "\033[37m"
_BCYAN = f"{_BOLD}{_CYAN}"
_BGREEN = f"{_BOLD}{_GREEN}"
_BYELLOW = f"{_BOLD}{_YELLOW}"
_BRED = f"{_BOLD}{_RED}"
_BWHITE = f"{_BOLD}{_WHITE}"


# --------------------------------------------------------------------------- #
# Model selection policy — the MCP server owns it (GI-003 / FR-009 / A-012).
#
# Delivery: foundry's plugin manifest declares a ``model`` userConfig option and
# this MCP server's declaration substitutes ``${user_config.model}`` into its
# ``env`` as FOUNDRY_MODEL. That is the ONLY path that works — ``${user_config
# .KEY}`` never interpolates into agent frontmatter (an agent pinned
# ``model: ${user_config.model}`` dies at spawn with "There's an issue with the
# selected model"), so every agent keeps a literal frontmatter pin as its floor
# and any override is applied at spawn time on top of it.
#
# The one dangerous detail: an UNSET option substitutes as the EMPTY STRING,
# not as an absent variable. Absence and "" must therefore resolve identically,
# and ``"model": ""`` must never reach an agent config — that is a malformed
# spawn, not a no-op (FR-003 "Absence = no override", FR-004 "Emit no model key
# at all", CT-002, CT-003, OT-001, OT-004).
# --------------------------------------------------------------------------- #

MODEL_ENV_VAR = "FOUNDRY_MODEL"

# CT-001 / FR-002 "Aliases + inherit". ``inherit`` is a real, forwardable value
# — the sentinel that makes an agent follow the session model. It is NOT a
# synonym for unset, and must not be collapsed into the empty-string path.
ACCEPTED_MODELS = ("opus", "sonnet", "haiku", "fable", "inherit")

# FR-005 "The good fits only" (AC-001). EXACTLY these foundry agents follow the
# option. Everything else this server configures keeps its own baseline at every
# setting (AC-004): ``foundry:assayer`` and ``foundry:tracer`` hold their
# frontmatter pins, and the ``general-purpose`` decompose / test / temper agents
# hold the explicit opus baseline they have always carried.
#
# ``foundry:flow-mapper`` has no spawn site in this server — forge's plan.md
# spawns it during V3 R0 — but it belongs in the set so the policy states the
# full foundry membership in one place rather than implying it.
STEERABLE_SUBAGENT_TYPES = ("foundry:teammate", "foundry:flow-mapper")


def configured_model() -> str:
    """Return the validated configured model, or ``""`` when unconfigured.

    ``""`` means "the user configured nothing" — both an absent FOUNDRY_MODEL
    and the empty string the harness substitutes for an unset option.

    Raises:
        ValueError: the value is outside ACCEPTED_MODELS. The message names the
            accepted set (CT-001, OT-003, FR-020). Refusal is loud rather than
            silently degrading, so a typo surfaces at the first tool call
            instead of as a confusing mid-run API error.
    """
    raw = os.environ.get(MODEL_ENV_VAR, "")
    value = raw.strip()
    if not value:
        return ""
    if value not in ACCEPTED_MODELS:
        raise ValueError(
            f"{MODEL_ENV_VAR}={raw!r} is not an accepted model. "
            f"Accepted values: {', '.join(ACCEPTED_MODELS)}."
        )
    return value


def agent_model(subagent_type: str, baseline: str = "") -> dict:
    """Return the ``{"model": ...}`` fragment to splat into one agent config.

    Args:
        subagent_type: the agent this config spawns. Only members of
            STEERABLE_SUBAGENT_TYPES follow the configured value.
        baseline: the model this site emitted before the option existed, or
            ``""`` when the site emitted no model key and the agent's own
            frontmatter pin governs.

    Returns:
        ``{"model": <value>}``, or ``{}`` when nothing resolves. An empty
        fragment emits NO key at all, so an unset option is indistinguishable
        from a build where this feature was never implemented (A-023, AC-003,
        CT-003, OT-004).
    """
    configured = configured_model()
    if configured and subagent_type in STEERABLE_SUBAGENT_TYPES:
        resolved = configured
    else:
        resolved = baseline
    return {"model": resolved} if resolved else {}


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


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# --------------------------------------------------------------------------- #
# Server-owned cycle counter (FR-005 / ST-001 / AC-008).
#
# Before this, ``state.json["cycle"]`` was written once as 0 by foundry_init and
# never incremented by any code path, so every cycle number in the data model
# was an integer the LEAD asserted as a tool argument. grand-vulture's state.json
# reads "cycle": 0 while its defects.json spans caller-asserted cycles 0-17.
#
# The counter now advances as an effect of handling the F3 GRIND -> F2 INSPECT
# boundary in ``foundry_mark_phase_complete`` (the ``inspect_start`` token). It
# is the key for the per-cycle stream roll-up (FR-014) and for the per-class
# consecutive-cycle escalation count (FR-006), both of which are meaningless
# against a caller-asserted number. Tools that used to trust a caller-supplied
# ``cycle`` for persistence now stamp this value instead.
# --------------------------------------------------------------------------- #


def _current_cycle(fdir: Path) -> int:
    """Return the server-owned cycle counter. Never caller-supplied.

    Returns 0 for a missing, absent, or malformed value so every reader gets a
    usable integer rather than having to guard the state file's shape.

    "Every reader" is enforced, not aspirational: no other function in this
    package may read ``state.json["cycle"]`` directly. Four once did (D-059),
    and a raw read hands on whatever the file holds — a str/None/list/dict
    raised an unhandled TypeError out of Foundry-Next, the mandatory handshake
    before every phase transition and gate, while -3 and 2.5 propagated
    silently into responses and onto every row of a synthesized verdict.
    ``test_orchestrator_gates.test_every_state_cycle_read_goes_through_a_
    guarded_reader`` derives the reader set from the source and fails on the
    next one added.
    """
    value = _load_json(fdir / "state.json").get("cycle", 0)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return 0
    return value


# Single compiled source of truth for the requirement-ID grammar. Used by
# BOTH the requirement count and the requirement-ID list so the P3 verdict
# synthesis writes exactly one row per ID the DONE gate's verdict_coverage
# check counts (analog note 3: do not fork a second regex/path resolver).
# D-150: the requirement-ID families are declared ONCE, in the vocabulary
# module, and read from there. This was a hand-typed literal, one of six copies
# across four modules, and every copy knew the same seven families and not
# OT- or GI- — 15 of this spec's 71 IDs. So the DONE gate's requirement count
# and the verdict-coverage synthesis below it could not see an observable truth
# at all, and no evidence could ever bind to one. NFR-002: the canonical
# pattern is a strict SUPERSET of what this copy matched, so nothing that was
# counted before stops being counted.
_REQ_ID_RE = REQUIREMENT_ID_RE


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


def _spec_requirement_ids(project_root: str) -> list[str]:
    """Return the sorted unique requirement IDs the vocabulary declares.

    The id source for P3 verdict synthesis. Uses the SAME path resolution
    and regex as ``_count_spec_requirements`` (which now delegates here) so
    synthesizing one VERIFIED row per id keeps ``verdict_coverage`` in
    lock-step with the DONE gate's ``_count_spec_requirements`` read.

    D-150: which FAMILIES count is no longer decided here. It was a literal
    naming seven of them, and this spec has 71 IDs of which 15 are the two it
    did not name — so an observable truth could not be counted, could not be
    verified, and could not have evidence bound to it. The families are one
    declaration in ``schemas.vocab`` now, and it is a strict superset of what
    this copy matched (NFR-002: nothing counted before stops being counted).
    """
    spec_path = _resolve_spec_path(project_root)
    if spec_path is None:
        return []
    # D-137's residual, one rung out from the loads: `except OSError` does not
    # name UnicodeDecodeError, so one non-UTF-8 byte in the spec raised out of
    # the DONE gate's requirement count. The spec is the external input D-145
    # brings inside the artifact guard, and this is its second reader.
    text, problem = read_text_file(spec_path)
    if problem is not None:
        return []
    return sorted(set(_REQ_ID_RE.findall(text)))


def _count_spec_requirements(project_root: str) -> int:
    """Count requirement IDs (US-N, FR-N, NFR-N, AC-N, VC-N) in the spec file."""
    return len(_spec_requirement_ids(project_root))


def _marker_counts(marker: Path) -> dict | None:
    """Parse a ``.{stream}-complete`` marker's ``key=value`` body.

    Returns ``{"items_checked", "items_total", "findings"}`` with ``findings``
    possibly None (the key absent), or None when the marker cannot be read.
    Kept as the load path for archives written before the per-cycle roll-up
    existed — those runs have markers and no ``stream-rollup.json``.
    """
    if not marker.exists():
        return None
    # D-098: UnicodeDecodeError is a ValueError, not an OSError, so a marker
    # with one non-UTF-8 byte raised straight through the old `except OSError`.
    # An unreadable marker still yields the zero-counts record rather than None:
    # None means "no marker", and a PRESENT marker whose numbers cannot be read
    # must fail the coverage threshold, not skip it.
    text = _read_text(marker)
    counts: dict = {"items_checked": 0, "items_total": 0, "findings": None}
    for line in text.splitlines():
        for key in ("items_checked", "items_total", "findings"):
            if line.startswith(f"{key}="):
                try:
                    counts[key] = int(line.split("=", 1)[1].strip())
                except (ValueError, IndexError):
                    pass
    return counts


def _prove_is_clean(fdir: Path, project_root: str) -> bool:
    """True when the recorded PROVE stream is clean: 0 findings AND >=95%
    requirement coverage across THIS cycle's records.

    Coverage is read from the per-cycle roll-up (FR-014) so a PROVE run
    delivered as several partial tranches is judged on its cycle TOTAL rather
    than on whichever tranche happened to be written last. Archives predating
    the roll-up fall back to the marker's aggregate counts.

    A spec that parses to ZERO requirements is never clean (FR-020 / AC-025).
    Previously the >=95% check was skipped when ``spec_count == 0``, so any
    ``.prove-complete`` with ``findings=0`` on an unresolvable or unparseable
    spec drove the F4 auto-VERIFY path — manufacturing a passing run out of a
    spec nothing had actually been proved against.
    """
    marker = fdir / _stream_marker("prove")
    if not marker.exists():
        return False
    totals = _rollup_totals(fdir, _current_cycle(fdir), "prove") or _marker_counts(marker)
    if totals is None:
        return False
    if totals.get("findings") is None or totals["findings"] != 0:
        return False
    spec_count = _count_spec_requirements(project_root)
    if spec_count <= 0:
        return False
    if totals["items_checked"] < spec_count * 0.95:
        return False
    return True


# CLOSED VOCABULARY — the verdict axis (FR-013 / CT-002). A requirement's
# verdict is either VERIFIED or one of the canonical defect types; that is the
# whole set, and it is DERIVED from schemas/vocab.py rather than hand-typed.
# server.py's Foundry-Verdict enum was a hand-typed baseline copy that rejected
# MISPLACED — a verdict agents/assayer.md mandates and commands/start.md routes
# into this very tool, so a verdict the protocol tells an agent to emit was
# unrepresentable on the surface that records it. "VERIFIED" is the one member
# that is not a defect type, which is why it is the one literal here.
# Extend the defect half via schemas/vocab.py, never here.
VERDICT_VALUES = frozenset({"VERIFIED"}) | DEFECT_TYPES


def _synthesize_clean_prove_verdicts(
    fdir: Path, project_root: str, cycle: int = 0
) -> int:
    """On a clean PROVE, write a VERIFIED verdict row for every spec
    requirement ID that lacks one. Returns the count synthesized.

    Rows match the Foundry-Verdict schema (foundry.py record shape):
    id / verdict / evidence / spec_text_cited / code_location / cycle /
    recorded_at. Existing rows are left untouched — never downgraded, never
    duplicated — so a real ASSAY verdict is preserved and ``verdict_coverage``
    never double-counts (analog note 7: preserve id-dedup).
    """
    ids = _spec_requirement_ids(project_root)
    if not ids:
        return 0
    verdicts_path = fdir / "verdicts.json"
    now = _now()
    synthesized = 0
    # D-103: verdicts.json is read-modify-written here AND by
    # foundry.py#foundry_add_verdict. Serializing this side removes the
    # orchestrator's contribution to the race; the flock is what would bind the
    # other side too, once that writer takes it (logged as a concern).
    with _document_transaction(verdicts_path) as verdicts:
        requirements = verdicts.get("requirements")
        if not isinstance(requirements, list):
            requirements = []
        existing_ids = {r.get("id") for r in requirements if isinstance(r, dict)}
        for rid in ids:
            if rid in existing_ids:
                continue
            requirements.append(
                {
                    "id": rid,
                    "verdict": "VERIFIED",
                    "evidence": (
                        "Auto-verified on clean PROVE "
                        "(≥95% coverage, 0 findings)."
                    ),
                    "spec_text_cited": "",
                    "code_location": "",
                    "cycle": cycle,
                    "recorded_at": now,
                }
            )
            synthesized += 1
        verdicts["requirements"] = requirements
    return synthesized


# --- P4 (FR-005 / ST-002): passing-gate → guidance-state advance ---
#
# Maps each _compute_next_action "transition_*" action to the Foundry-Gate
# phase whose passing should advance the guidance state. When that gate has
# passed (recorded via the ``.gate-passed`` marker), the next Foundry-Next
# tells the lead the gate is satisfied and to proceed to the transition step
# instead of re-running the now-satisfied gate.
_ACTION_TO_GATE = {
    "transition_to_cast": "cast",
    "transition_to_inspect": "inspect",
    "transition_to_grind": "grind",
    "transition_to_assay": "assay",
    "transition_to_temper": "temper",
    "transition_to_nyquist": "nyquist",
    "transition_to_done": "done",
}


def _expected_gate_for_action(action: str) -> str | None:
    """Return the gate phase a given transition action asks the lead to run."""
    return _ACTION_TO_GATE.get(action)


def _nyquist_transition(from_phase: str) -> dict:
    """Return the 'enter F5.5 NYQUIST' step, emitted from F4 or F5.

    Two entry points share one step: a --nyquist run without --temper arrives
    from F4 (ASSAY passed), and one with both arrives from F5 (TEMPER clean).
    ``from_phase`` is the phase the lead is currently IN, which the guidance
    display keys on.

    The agent config carries no ``model`` key on purpose: ``nyquist-auditor``
    is not in STEERABLE_SUBAGENT_TYPES and holds its own sonnet frontmatter
    pin, so this site emits nothing and lets the pin govern at every setting of
    the option (AC-004, FR-005) — the same shape as INSPECT_TRACE_CONFIG.
    """
    return {
        "phase": from_phase,
        "action": "transition_to_nyquist",
        "instructions": (
            "--nyquist is set. Call Foundry-Gate(phase='nyquist'), then "
            "Foundry-Phase(phase='nyquist') to enter F5.5. Batch VERIFIED "
            "requirements by 5 and spawn one foundry:nyquist-auditor agent per "
            "batch. Each classifies COVERED / UNTESTED / UNDERTESTED, generates "
            "minimal behavioral tests, runs them, and commits the passing ones. "
            "Any ESCALATE_IMPL_BUG result starts a new GRIND cycle. Never mark "
            "an untested requirement as passing."
        ),
        "details": {
            "agent_config": {
                "subagent_type": "foundry:nyquist-auditor",
                "description": "NYQUIST: regression tests for VERIFIED requirements",
            },
            "batch_size": 5,
        },
    }


# --------------------------------------------------------------------------- #
# Tier-aware defect reads (CT-008 / FR-006 / FR-051 / GI-001)
#
# ONE helper, consulted by every gate, because the alternative was measured on
# this exact module: `sum(1 for d in ... if d.get("status") == "open")` was
# hand-written at SIX sites (assay, grind, done, inspect_clean, next-action,
# the status display), and D-119 is the shipped instance of two of those copies
# disagreeing. Adding "and what tier is it" to six copies is that defect with a
# second field.
#
# THE TIER READ ITSELF IS NOT DECIDED HERE. `vocab.defect_tier` is total over
# DEFECT_TIER_OR_UNKNOWN and is the one place a missing key, a null, a non-string
# and an unknown string all resolve to TIER_UNKNOWN. FR-051 turns on that
# resolution: an untiered pre-change record blocks exactly like LIVE, and reading
# it as LATENT would silently clear every gate on records nobody ever classified.
# --------------------------------------------------------------------------- #

#: CT-008 — the tiers that BLOCK. LIVE is a reachable failure and blocks as it
#: always did; unknown is a record no stream has classified and is treated
#: identically until one re-files it with a tier (FR-051). LATENT blocks nothing:
#: it stays open, tracked, and named in the F6 backlog (FR-006).
BLOCKING_TIERS = ("LIVE", TIER_UNKNOWN)


def _open_defects_by_tier(fdir: Path) -> dict[str, list[dict]]:
    """Every OPEN defect in the ledger, bucketed by the tier it READS as.

    Keys are exactly LIVE, LATENT and TIER_UNKNOWN, always present and possibly
    empty — a caller that has to check whether a bucket exists before counting
    it will eventually forget to, and an absent bucket reads as zero blocking
    defects, which is the direction that fails open.

    Non-dict historical records are skipped, not guessed at: the same tolerance
    `_dict_records` holds for the ledger's writers (D-128).
    """
    buckets: dict[str, list[dict]] = {"LIVE": [], "LATENT": [], TIER_UNKNOWN: []}
    for d in _load_json(fdir / "defects.json").get("defects", []):
        if not isinstance(d, dict) or d.get("status") != "open":
            continue
        buckets[defect_tier(d)].append(d)
    return buckets


#: D-153 — the clean-cycle crossing, spelled for the phase the reader is IN.
#: The crossing itself is always an `inspect_start` OUT OF F3; what differs is
#: what it takes to be standing in F3, and naming only the destination is what
#: sent a lead at F2 into a refusal (see `_still_escalated_notice`).
_CLEAN_CYCLE_CROSSING_DEFAULT = (
    "Foundry-Phase(phase='inspect_start') out of F3 closes one"
)


def _escalation_exit_distances(
    fdir: Path, project_root: str, classes: list[str], *, crossing: str = ""
) -> str:
    """One sentence per still-escalated class: which arm clears it, and how far.

    D-111 — A REFUSAL THAT NAMES NO REACHABLE CALL IS NOT A REMEDY.
    --------------------------------------------------------------
    The DONE refusal named both arms in the abstract and left the lead to work
    out which one was closer, and for a class with no persisted
    `escalation.json` entry neither was reachable at all — following the hint
    six times moved nothing. With the record now written at the `inspect_start`
    boundary, both distances are readable off it, so the refusal states them:
    how many more clean crossings the clean arm needs (ST-001), and how many
    more structural packets the budget arm needs (ST-002). Whichever arm fires
    first wins, so both are quoted and the shorter one is obvious.

    D-154 — AND THE BUDGET ARM IS QUOTED ONLY WHERE IT CAN FIRE.
    -----------------------------------------------------------
    "N more structural packet(s)" was printed unconditionally, including for a
    class with every instance CLOSED — the state both of this run's escalated
    classes are in. Driven: the sentence offered "2 more structural packet(s)
    (budget arm, Foundry-Tasks emits one per class per cycle)" while
    `Foundry-Tasks` on the same run returned `structural_tasks: None`,
    `escalated_classes: []` and left `structural_packets_dispatched` at 0.
    `_spend_structural_budget` is fed `_escalated_classes`, which opens with
    `if not bucket["open"]: continue`, so a class with no open bucket can never
    consume a packet and the offered arm can never advance. It is not a
    deadlock — the clean-cycle arm still fires — but a refusal whose own remedy
    names a route that does not exist is D-111's defect one arm over.

    So the open-instance count is READ, from the same buckets the escalation
    arms read, and a class with nothing open is told that its budget arm cannot
    advance and why. ``crossing`` names the call that actually closes a clean
    cycle from where the READER is standing (D-153); it defaults to the
    destination alone, which is all a caller at a terminal phase can say.

    D-157 — AND THE CLEAN DISTANCE IS THE ARM'S OWN WALK, NOT A SECOND SUM.
    ----------------------------------------------------------------------
    This said `max(0, LIVE_CLEAN_CYCLES_TO_CLEAR - entry["live_clean_cycles"])`,
    which consults neither `escalated_at_cycle` nor `live_clean_cycles_counted`
    — both of which the arm evaluates on every crossing. Driven on AC-002's
    fixture (escalated at cycle 5, counter at 5, the class drawing zero LIVE
    instances in cycle 5): this printed "2 more INSPECT cycle(s)" while the arm
    needed THREE crossings, because the crossing closing cycle 5 is discarded by
    the escalated-before guard. The number is now produced by
    `_clean_arm_crossings_left`, which walks `_clean_arm_step` — the arm itself
    — with every future cycle assumed clean.

    `escalated_at_cycle` comes from the persisted entry when there is one and
    from the ledger derivation otherwise, because a class with no record yet has
    that exact value latched by `_record_escalation_proposals` at the next
    crossing, BEFORE the arm runs on it (see the `inspect_start` branch). When
    neither source knows it, the arm cannot advance and the sentence says so
    rather than printing a number no crossing will honour.

    Reads, never writes. A class with no entry yet reads as the full distance to
    each arm, which is exactly what it is.
    """
    if not classes:
        return ""
    recorded = _load_json(fdir / ESCALATION_FILENAME).get("classes", {})
    if not isinstance(recorded, dict):
        recorded = {}
    buckets = _class_buckets(_load_json(fdir / "defects.json").get("defects", []))
    derived = _escalated_classes(fdir, project_root)
    next_completed = _current_cycle(fdir)
    crossing = crossing or _CLEAN_CYCLE_CROSSING_DEFAULT
    parts: list[str] = []
    for key in classes:
        entry = recorded.get(key)
        entry = dict(entry) if isinstance(entry, dict) else {}
        _escalation_entry_defaults(entry)
        escalated_at = entry.get("escalated_at_cycle")
        if not isinstance(escalated_at, int) or isinstance(escalated_at, bool):
            escalated_at = (derived.get(key) or {}).get("escalated_at_cycle")
        clean_left = _clean_arm_crossings_left(entry, next_completed, escalated_at)
        packets_left = max(
            0, STRUCTURAL_PASS_BUDGET - entry["structural_packets_dispatched"]
        )
        open_count = len((buckets.get(key) or {}).get("open") or [])
        if clean_left is None:
            clean_arm = (
                f"{key}: the clean_cycles arm cannot advance — no escalation "
                f"cycle is recorded for this class, and ST-001 counts only "
                f"cycles that closed AFTER the one it escalated on. The next "
                f"crossing records it, and counting starts from the crossing "
                f"after that"
            )
        else:
            clean_arm = (
                f"{key}: {clean_left} more INSPECT crossing(s) drawing zero "
                f"LIVE instances (clean_cycles arm — {crossing}, and they must "
                f"be CONSECUTIVE)"
            )
            if next_completed <= escalated_at:
                # D-157: say which of those crossings banks nothing, so the
                # count and the guard cannot read as contradicting each other.
                clean_arm += (
                    f"; the first closes cycle {next_completed}, at or before "
                    f"the cycle this class escalated on ({escalated_at}), which "
                    f"ST-001's guard does not count"
                )
        if open_count:
            parts.append(
                clean_arm
                + f", or {packets_left} more structural packet(s) (budget arm, "
                f"Foundry-Tasks emits one per class per cycle)"
            )
        else:
            # D-154: no second route to offer. Said as a fact about THIS class
            # rather than omitted, so a lead who read the budget arm on an
            # earlier cycle learns why it stopped being available.
            parts.append(
                clean_arm
                + f". The budget arm cannot advance this class: every instance "
                f"of it is closed, and Foundry-Tasks emits a structural packet "
                f"only for a class with open instances, so its "
                f"{packets_left} unspent packet(s) stay unspent"
            )
    return " Distance to each exit — " + "; ".join(parts) + "."


def _blocking_defects(fdir: Path) -> dict:
    """The tier-aware "may this gate pass?" answer, with the refusal prose.

    Returns ``{"blocking": int, "live": [ids], "unknown": [ids],
    "latent": [ids], "reason": str, "hint": str}``. ``reason`` and ``hint`` are
    empty strings when nothing blocks.

    CT-008 requires the refusal to NAME the open LIVE and unknown-tier defects,
    and to tell them apart: they block for different reasons and the operator's
    next move differs. A LIVE defect needs fixing; an unknown-tier defect needs a
    stream to re-file it with a tier, after which it may well stop blocking. One
    undifferentiated count would send a lead hunting for a reproduction that no
    stream ever claimed to have.
    """
    buckets = _open_defects_by_tier(fdir)
    live = [d.get("id", "?") for d in buckets["LIVE"]]
    unknown = [d.get("id", "?") for d in buckets[TIER_UNKNOWN]]
    latent = [d.get("id", "?") for d in buckets["LATENT"]]

    parts = []
    if live:
        parts.append(f"{len(live)} open LIVE defect(s): {', '.join(live)}")
    if unknown:
        parts.append(
            f"{len(unknown)} open defect(s) with no tier, which block like LIVE "
            f"until a stream re-files them: {', '.join(unknown)}"
        )

    hint = ""
    if parts:
        hint = (
            "Fix the LIVE defects in GRIND. "
            if live
            else ""
        ) + (
            # D-077: the hint names BOTH doors, because both now honour it.
            # It used to say "have the filing stream re-file it" while only
            # `Foundry-Defect` matched an existing untiered record — so a
            # stream that followed the hint through `Foundry-Sync`, which is
            # the door a whole INSPECT stream files through, got a SECOND open
            # record beside the untiered one and the blocking count did not
            # move. Naming one door and meaning one door is what made the hint
            # actionable only by accident.
            "Have the filing stream re-file each untiered defect with tier=LIVE "
            "or tier=LATENT — through Foundry-Defect or Foundry-Sync; either "
            "door matches the open untiered record on (source, type, file, "
            "symbol) and re-tiers it IN PLACE, so it keeps its id and every "
            "citation naming it stays valid. An untiered record is not a "
            "judgement, it is a record made before the tier existed."
            if unknown
            else ""
        )
        if latent:
            hint += (
                f" The {len(latent)} open LATENT defect(s) do not block: they "
                "stay open, tracked, and named in the F6 backlog."
            )

    return {
        "blocking": len(live) + len(unknown),
        "live": live,
        "unknown": unknown,
        "latent": latent,
        "reason": "; ".join(parts),
        "hint": hint.strip(),
    }


# --------------------------------------------------------------------------- #
# Cross-casting seam: tools/foundry_report.py (C-10) and tools/evidence.py (C-7).
#
# LAZY, for the reason the seam at the bottom of this file spells out at length:
# `foundry_report` and `evidence` both import from this package, and a
# module-top import here closes an import cycle that takes EVERY tool in this
# server down at once. Unguarded, so a wiring break fails loudly at the one call
# site that needs the symbol instead of hiding behind a silent fallback.
#
# These are thin: they exist so the lazy import is written ONCE per symbol
# rather than at each of the four call sites, not to re-decide anything the
# owning module already decided.
# --------------------------------------------------------------------------- #


def _report_status(fdir: Path) -> dict:
    """C-10's ``report_status`` — {"present", "missing_sections", ...}."""
    from foundry_mcp.tools.foundry_report import report_status

    return report_status(fdir)


def _generate_report(project_root: str, fdir: Path) -> dict:
    """C-10's ``generate_report`` — writes REPORT.md and report.json."""
    from foundry_mcp.tools.foundry_report import generate_report

    return generate_report(Path(project_root), fdir)


# --- Phase gate ---


def _done_preconditions(fdir: Path, project_root: str) -> dict:
    """Evaluate the substantive preconditions for entering F6 DONE.

    Returns ``{"passed": bool, "reason": str, "hint": str, "checklist": [...],
    "refusals": [...]}``.

    WHY THIS IS A FUNCTION (AC-011 / D-037)
    ---------------------------------------
    These checks lived inline in ``foundry_gate``'s "done" branch, and
    ``foundry_mark_phase_complete("done")`` — the call that actually writes F6
    and archives the run — read NONE of them. It was an unconditional
    ``_update_phase`` + ``clear_active_run``. Driven: a run reached DONE with
    six open escalated-class defects and zero verdicts, straight past a gate
    that would have refused it, because consulting the gate was a convention
    the lead was trusted to follow rather than something the transition did.
    AC-011 says "the RUN cannot reach DONE while any escalated-class defect
    remains open" — that is a property of the transition, not of an advisory
    query about the transition.

    So there is one evaluation and two callers. Re-implementing the checks at
    the transition would have satisfied the same test today and drifted from
    the gate by the next cycle, which is the shape of the defect being fixed
    here, not a fix for it.

    Deliberately EXCLUDED, because they are ``foundry_gate``'s call-ordering
    protocol rather than preconditions of being done:

      - the ``.next-action-called`` handshake, which the transition has
        already consumed by the time it asks (re-checking it here would refuse
        every done transition on a token nobody re-armed);
      - the ``.gate-passed`` stamp, which records that an advisory gate ran.

    That exclusion is the whole discipline of this helper: it enforces exactly
    the gate's checks, no more.

    D-190 / D-191 — AND THE ORDERING IS DECLARED, NOT POSITIONAL.
    ------------------------------------------------------------
    This function used to run on the three locals ``passed`` / ``reason`` /
    ``hint`` down a ladder of ten independent ``if`` statements, so the LAST
    failing check owned the pair a terminal prints. That is the generator
    D-186 replaced inside ``foundry_gate``, in the one function that fix
    deliberately did not convert — and both halves of the harm were then driven
    through ``server.call_tool`` at the F6 doors:

      * D-190 (AC-003). A CLEARED class with one open LIVE instance D-900, a
        committed evidence log that no longer reproduces. The evidence rung
        claimed ``reason`` after the blocking-defect arm, so
        ``Foundry-Gate('done')`` answered "1 committed evidence log(s) no
        longer reproduce at HEAD" and named D-900 nowhere — a byte-identical
        refusal to the one the SAME state produces with no defect open at all,
        while ``Foundry-Gate('nyquist')``, whose defect read goes through
        ``_GateLadder``, named it. AC-003's two doors disagreed, and only this
        one was wrong.
      * D-191 (NFR-005 / FR-026 / CT-008). Four checks failed at once — no
        report, one open LIVE defect, a registered team, 2 verdicts of 5 — and
        the rendered remedy was the verdict-coverage arm's "ASSAY must write
        ALL verdicts", which ``Foundry-Gate('assay')`` then refuses at
        ``_GATE_RANK_DEFECTS`` for the very defect this door declined to
        mention. ``refusals`` published exactly ONE entry, the delegated
        verdict, so the three refusals the call had already computed were
        discarded. On that state the ladder now renders the team check, whose
        remedy nothing failing defeats, and publishes all four; the filing's
        parenthetical that "Call Foundry-Report" is the undefeated remedy is
        answered at ``_GATE_RANK_REPORT``, where the reasoning is set out.

    So every check now enters ``_GateLadder`` with its own rank and its own
    remedy, under that class's one rule: THE REFUSAL THAT SPEAKS IS THE ONE
    WHOSE REMEDY IS NOT DEFEATED BY ANOTHER FAILING CHECK ON THE SAME CALL.
    Nothing is discarded — every failing check is published under ``refusals``,
    and both F6 gates and both F6 transitions pass that list through.

    THE HALT STILL HAS THE LAST WORD, and no longer needs to be the last
    writer to get it. It used to be asserted TWICE — once in its own branch and
    again after the evidence rung — precisely because position was the only
    ordering this function had. It is now one ``fail`` at
    ``_GATE_RANK_HALTED``, the lowest rank there is, which says the same thing
    for a stated reason: on a run that has already stopped every other remedy
    is work that cannot be gated, so no other failing check can defeat it and
    it defeats them all.
    """
    checklist: list[dict] = []
    # D-190 / D-191: the three locals this used to carry (`passed`, `reason`,
    # `hint`) were a last-writer-wins ladder. Every failing check now enters
    # the ladder with its own rank and its own remedy; see `_GateLadder` and
    # the `_GATE_RANK_*` block below.
    ladder = _GateLadder()

    verdicts = _load_json(fdir / "verdicts.json")
    verdict_list = verdicts.get("requirements", [])
    non_verified = sum(1 for r in verdict_list if r.get("verdict") != "VERIFIED")
    blocking = _blocking_defects(fdir)
    open_count = blocking["blocking"]
    teams_result = _check_active_teams(project_root)
    spec_count = _count_spec_requirements(project_root)

    # GI-006 / CT-014 / AC-036 / ST-010 — DONE REQUIRES THE GENERATED REPORT.
    #
    # Its own named precondition, because it is the one refusal a lead clears
    # with a tool call rather than with work: `Foundry-Report`. Read through
    # casting 5's `report_status`, which answers "present, and which sections
    # are missing" from BOTH documents — `report.json` for "which sections were
    # generated" and REPORT.md for "which sections a reader can still find" —
    # and returns the union.
    #
    # D-050: this block used to claim `report_status` read the JSON "and never
    # from REPORT.md", and argued from that claim that markdown could not be
    # confused by appended prose. The claim outlived the code it described:
    # D-015 moved the read onto both documents after `rm REPORT.md` left the
    # DONE gate passing, and this block kept the case for the behaviour that
    # drive removed. A maintainer reading it would have concluded REPORT.md was
    # unchecked and could have reverted D-015 as redundant, which is the whole
    # cost of stale prose surviving beside new prose.
    #
    # The prose GI-006 explicitly permits the lead to append BELOW the sections
    # still cannot make a present section look absent, but that is now true for
    # a stated mechanism rather than by not looking: `_markdown_missing_sections`
    # matches whole heading lines, so appended paragraphs add headings without
    # removing any.
    #
    # RANKED LAST, AT `_GATE_RANK_REPORT`, which is what this block already
    # argued when the ordering was positional: "STATED FIRST, deliberately ...
    # the least specific check goes at the top", so that every later failing
    # branch overwrote it, because "a run short a report is usually also short
    # something more specific, and a lead should be told about the open defect
    # or the unparsed spec rather than about the paperwork". D-191 read the
    # defeat rule the other way — `Foundry-Report` is refused by no open
    # defect, no registered team and no unparsed spec, so its remedy looked
    # undefeated — and the CALL is indeed never refused. The DOCUMENT is:
    # `generate_report` derives every section from the same ledgers the checks
    # above read, so a report generated beside an open defect, a missing
    # verdict or an unparsed spec is a report the next cleared check
    # invalidates, and the lead must call it again. A remedy that has to be
    # repeated after another failing check is fixed is defeated by that check.
    # Three tests pinned that reading before this conversion and still do —
    # `test_a_more_specific_done_failure_still_names_itself` says it outright,
    # "the reason a lead reads is still the concrete one, not the report".
    # On an otherwise-clean run nothing outranks it and the refusal names the
    # report (OT-025); on a run failing four checks its sentence is published
    # under `refusals` rather than discarded, which is the half of D-191 that
    # was a defect either way.
    report = _report_status(fdir)
    report_json_path = fdir / REPORT_JSON_FILENAME
    if not report["present"]:
        missing_sections = report.get("missing_sections") or []
        if not report_json_path.exists():
            report_reason = (
                f"no generated report — {REPORT_MD_FILENAME} and "
                f"{REPORT_JSON_FILENAME} have not been written"
            )
        elif report.get("problem"):
            report_reason = f"the generated report is unusable — {report['problem']}"
        else:
            report_reason = (
                f"the generated report is missing {len(missing_sections)} "
                f"section(s): " + ", ".join(missing_sections)
            )
        ladder.fail(
            _GATE_RANK_REPORT,
            report_reason,
            "Call Foundry-Report. The report is generated, not written by hand: "
            "you may append prose below its sections, you may not omit one, and "
            "DONE is refused until every section is present.",
        )
    checklist.append({
        "check": (
            "report_generated "
            f"(missing_sections={len(report.get('missing_sections') or [])})"
        ),
        "ok": report["present"],
        "missing_sections": report.get("missing_sections") or [],
    })

    # FR-020 / AC-025 — the auto-VERIFY hole, closed at the gate.
    #
    # Every other check below is vacuously satisfied by a spec that parses
    # to ZERO requirements: no requirement can be non-VERIFIED, and the
    # verdict_coverage check guarded itself with `spec_count > 0` and so
    # skipped. A run whose spec is unresolvable or carries no tagged
    # requirement IDs therefore sailed through DONE having proved nothing.
    #
    # RANKED AT `_GATE_RANK_CONFIG`, which keeps exactly the standing this arm
    # was given when it was written FIRST in a last-writer ladder ("so a more
    # specific failure below still claims `reason`"): how the run was
    # configured, and the last thing a lead is told when anything more concrete
    # is also wrong. It contests nothing it used to contest — with `spec_count`
    # at zero `non_verified` is zero and `verdict_coverage` skips, so the two
    # verdict arms cannot fire beside it.
    if spec_count <= 0:
        ladder.fail(
            _GATE_RANK_CONFIG,
            "The spec parses to ZERO requirement IDs — nothing has been "
            "verified, so DONE is vacuous.",
            "Check that the run's spec resolves (foundry-archive/{run}/spec.md, "
            "else state.json's spec_path) and that it carries tagged "
            "requirement IDs (US-N / FR-N / NFR-N / AC-N / VC-N / IR-N / TR-N).",
        )

    if non_verified > 0:
        ladder.fail(
            _GATE_RANK_VERDICTS,
            f"{non_verified} requirement(s) not VERIFIED — THIN/PARTIAL are defects, not follow-ups",
            "Fix all non-VERIFIED requirements. Every THIN item must be fully implemented.",
        )
    if open_count > 0:
        # FR-026 — THE STALE HINT.
        #
        # This branch set `reason` and left `hint` alone, so whatever the
        # PREVIOUS check happened to write stayed attached to it. A run with
        # open defects and at least one non-VERIFIED requirement was refused
        # with reason "N open defect(s) remain" beside hint "Fix all
        # non-VERIFIED requirements. Every THIN item must be fully
        # implemented." — an instruction for a different check entirely, and
        # the lead's only stated next move. `hint` is now a REQUIRED positional
        # on `_GateLadder.fail`, so an arm that claims `reason` without stating
        # what clears IT is no longer expressible here either.
        ladder.fail(_GATE_RANK_DEFECTS, blocking["reason"], blocking["hint"])

    # AC-011 / ST-003: escalation NEVER waives closure. Swapping N
    # per-instance packets for one structural packet changes the shape of
    # the work, not whether every instance must reach fixed. Stated as its
    # own named check so the guarantee is visible in the checklist rather
    # than merely implied by the open-defect count above.
    #
    # D-002 / FR-006 — TIER-AWARE, LIKE EVERY OTHER BRANCH IN THIS FUNCTION.
    #
    # This branch counted `bucket["open"]` regardless of tier while every
    # sibling above reads `_blocking_defects`, so a class with three open LATENT
    # instances and nothing reproduced refused DONE — against FR-006 verbatim,
    # "INSPECT-clean, ASSAY, TEMPER, NYQUIST and DONE all pass when the only
    # open defects are LATENT". Worse, `foundry_gate("nyquist")` consults
    # `_escalated_classes` not at all, so the two F6 doors returned OPPOSITE
    # verdicts on identical run state — the drift shape
    # `test_both_doors_into_f6_enforce_the_same_preconditions` exists to prevent,
    # one door along.
    #
    # D-034 — LEAD RULING ON THE ST-010 / FR-006 TENSION: BOTH GUARDS APPLY.
    #
    # The D-002 fix keyed this branch on `open_live_defect_ids`, reasoning that
    # a class carrying only LATENT instances has no remaining work of either
    # shape and so should not hold DONE open. FR-006 supports that for the
    # DEFECTS; ST-010 is about the CLASS, and it says "every escalated class
    # CLEARED". Those are different axes, and the previous reconciliation
    # silently dropped the second one: a class could sit at status ESCALATED
    # forever, having had neither structural pass nor two clean cycles, and
    # DONE would pass it because its open instances all happened to be LATENT.
    #
    # The ruling (recorded SPEC_AMBIGUOUS in the run's state.json) is that DONE
    # requires BOTH: (a) no open LIVE or unknown-tier defect — the tier check
    # above, which still blocks a LIVE instance of a CLEARED class — and (b)
    # every class that is still escalating to have left escalation through an
    # arm. A LATENT-only backlog does not by itself clear a class; ST-001's two
    # clean cycles or ST-002's structural budget does, and both are mechanical,
    # so this cannot hold a run open indefinitely.
    #
    # KEYED ON THE PERSISTED STATUS, which is what `_escalated_classes` already
    # reads: it skips any class `escalation.json` records as CLEARED, and it
    # returns nothing for a class whose defects have all closed. So "still
    # returned here" IS "still ESCALATED with open work", and a class that
    # cleared — or emptied — does not appear.
    #
    # This block used to add that reading `escalation.json` directly would
    # DEADLOCK, because both exit arms iterated this same function and so a
    # class that escalated and was then fully fixed reached neither arm. That
    # was true when it was written and D-043 made it false: the arms now walk
    # the persisted entries themselves (see `_persisted_escalations`), so such a
    # class advances a clean cycle at every boundary and CLEARS with
    # `clean_cycles`. The guard nonetheless STAYS on `_escalated_classes`, per
    # the D-034 ruling — that is where the operator's escalation overrides are
    # filtered, and a DONE guard that bypassed them would refuse a run the
    # operator had explicitly de-escalated.
    # D-059 — THE GUARD READS THE PERSISTED STATUS, NOT ONLY THE LEDGER
    # RECURRENCE.
    # ------------------------------------------------------------------
    # ST-010's clause is "every escalated class CLEARED", and this measured
    # "still escalated" solely from `_escalated_classes`, which opens with
    # `if not bucket["open"]: continue` and then `if run_len < ESCALATION_CYCLES:
    # continue`. A class with zero open instances is invisible to both lines
    # WHATEVER escalation.json says, and only the boundary arms ever write
    # CLEARED — needing LIVE_CLEAN_CYCLES_TO_CLEAR crossings, while ONE clean
    # crossing is enough to pass ASSAY/TEMPER/NYQUIST and reach DONE. Driven:
    # class SCAN_GAP persisted `status: ESCALATED` with live_clean_cycles 1 and
    # all three instances fixed -> Foundry-Gate('done') PASSED with checklist
    # "escalated_classes_cleared (still escalated=0)" and Foundry-Phase('done')
    # returned ok, phase F6, while escalation.json on disk still read ESCALATED
    # and the run's OWN report said by_status {CLEARED: 0, ESCALATED: 1} with a
    # null exit reason. The run's two artifacts contradicted each other about
    # whether it had finished.
    #
    # The union is what ST-010 asks for. Overrides stay honoured on both halves:
    # `_persisted_escalations` filters them exactly as `_escalated_classes`
    # does, so a class the operator de-escalated still does not block (D-034).
    # This cannot deadlock — a fully fixed class draws zero LIVE instances by
    # construction, so the clean arm clears it within two boundaries.
    escalated_open = _escalated_classes(fdir, project_root)
    persisted_classes = _load_json(fdir / ESCALATION_FILENAME).get("classes", {})
    if not isinstance(persisted_classes, dict):
        persisted_classes = {}
    persisted_escalated = _persisted_escalations(
        fdir, project_root, persisted_classes
    )
    still_escalated = sorted(set(escalated_open) | set(persisted_escalated))
    latent_only_classes = sorted(
        key for key, info in escalated_open.items()
        if not info.get("open_live_defect_ids")
    )
    if still_escalated:
        blocking_ids = sorted(
            did
            for info in escalated_open.values()
            for did in info.get("open_live_defect_ids", [])
        )
        ladder.fail(
            # RANKED ABOVE THE DEFECT LEDGER, and this is the one place at this
            # door where the two contest each other. "Fix the LIVE defects in
            # GRIND" does NOT clear a class — ST-010 needs an arm to fire — so
            # a lead who follows the defect remedy and returns is refused again
            # by THIS check: that remedy is defeated. The escalation remedy is
            # defeated by nothing here, and it already names the blocking LIVE
            # instances below, so following it does the defect work too. It is
            # also the only remedy at this door measured in CYCLES rather than
            # in calls, and D-129 is what learning that at F5.5 costs: every
            # remaining boundary becomes a full post-verification loop instead
            # of one crossing from F2.
            _GATE_RANK_ESCALATION,
            f"{len(still_escalated)} defect class(es) are still ESCALATED: "
            f"{', '.join(still_escalated)}",
            "ST-010: every escalated class must be CLEARED before DONE. A "
            "class leaves escalation mechanically — two consecutive INSPECT "
            f"cycles drawing zero LIVE instances (ST-001), or "
            f"{STRUCTURAL_PASS_BUDGET} structural packets dispatched (ST-002) "
            "— and a LATENT-only backlog does not by itself clear one. A "
            "structural fix must still close every LIVE defect of the class; "
            "escalation is not a waiver."
            + (f" Blocking LIVE instances: {', '.join(blocking_ids)}."
               if blocking_ids else "")
            # D-111: the arm and the distance, PER CLASS. This hint used to
            # offer one undifferentiated sentence — "cross the GRIND->INSPECT
            # boundary so the clean-cycle arm counts, or spend the structural
            # budget" — and a lead that followed it six times walked the counter
            # from 4 to 9 with `escalation.json` still absent, because for a
            # class with no persisted record BOTH named routes were no-ops. The
            # record is now written at the boundary (see the `inspect_start`
            # branch), so the distance to each arm is a number this can state.
            + _escalation_exit_distances(fdir, project_root, still_escalated),
        )
    checklist.append({
        "check": (
            f"escalated_classes_cleared (still escalated={len(still_escalated)}, "
            f"latent_only={len(latent_only_classes)})"
        ),
        "ok": not still_escalated,
        "classes": still_escalated,
        # Named apart so a reader can tell WHY a class is still escalating: it
        # is recurring in the ledger, or escalation.json has never recorded an
        # exit for it, or both.
        "recurring_classes": sorted(escalated_open),
        "persisted_escalated_classes": persisted_escalated,
        # Named separately even though both block now, because "escalated and
        # carrying only a LATENT backlog" and "escalated with LIVE work open"
        # clear by different routes: the first waits for an arm, the second
        # needs the defects fixed first.
        "latent_only_classes": latent_only_classes,
    })

    if teams_result["active"]:
        # FR-026, same stale-hint repair as the open-defect branch above, and
        # the same rank the three `foundry_gate` branches give it: a registered
        # team holds the tree, every remedy that spawns an agent is refused
        # until it is down, and shutting it down is refused by nothing.
        ladder.fail(
            _GATE_RANK_TEAMS,
            f"Active teams: {', '.join(teams_result['teams'])}",
            teams_result.get("hint") or _TEAMS_DOWN_HINT,
        )

    verdict_count = len(verdict_list)
    verdicts_complete = True
    if spec_count > 0 and verdict_count < spec_count:
        skipped = spec_count - verdict_count
        ladder.fail(
            _GATE_RANK_VERDICTS,
            f"Only {verdict_count} verdicts but spec has {spec_count} requirements. {skipped} skipped.",
            "ASSAY must write ALL verdicts to verdicts.json — including THIN/PARTIAL, not just VERIFIED.",
        )
        verdicts_complete = False

    # ST-008 / CT-016 / FR-024 / D-081 — "HALTED IS NOT DONE", AS A PRECONDITION
    # OF BEING DONE.
    #
    # `_GATE_RANK_HALTED` is the lowest rank there is, which is how this keeps
    # the last word now that the ordering is declared rather than positional.
    # It used to be asserted TWICE — here, and again after the evidence rung —
    # because source position was the only ordering this function had, and the
    # rung below it would otherwise have overwritten `reason`. One `fail` says
    # the same thing for a stated reason: on a halted run every other failure
    # is a detail of a run that has already stopped, so no other failing
    # check's remedy can defeat this one and this one defeats them all.
    # Telling a lead to fix three open LIVE defects, or to re-capture an
    # evidence log, on a run that ended two cycles ago sends them to do work
    # that cannot be gated.
    #
    # Its own named branch here, and not merely covered by the blanket guards at
    # the gate and the transition, because THIS is the shared evaluation of "may
    # this run finish" that both F6 doors consult (D-037 / D-043 / D-044). A
    # precondition of finishing that only two of its three readers can see is
    # the drift shape this helper exists to prevent, and the checklist a lead
    # reads is produced here.
    halted = _halted_state(fdir)
    if halted is not None:
        refusal = _halted_refusal(fdir, "Foundry-Phase(phase='done')") or {}
        ladder.fail(
            _GATE_RANK_HALTED,
            refusal.get("error") or "the run is HALTED",
            refusal.get("hint")
            or "HALTED is terminal. Start a NEW run with a higher --max-cycles.",
        )

    checklist.append({
        "check": (
            "run_not_halted"
            + (f" (halted_at_cycle={halted['halted_at_cycle']})" if halted else "")
        ),
        "ok": halted is None,
        "halted_reason": (halted or {}).get("halted_reason", ""),
    })

    checklist.append({
        "check": f"spec_requirements_parsed (count={spec_count})",
        "ok": spec_count > 0,
    })
    checklist.append({"check": f"all_verified (non_verified={non_verified})", "ok": non_verified == 0})
    checklist.append({
        # CT-008: the count is the BLOCKING count, and the LATENT backlog rides
        # alongside it so a passing checklist still says what is being carried.
        "check": (
            f"zero_blocking_defects (live={len(blocking['live'])} "
            f"unknown_tier={len(blocking['unknown'])} latent_backlog={len(blocking['latent'])})"
        ),
        "ok": open_count == 0,
        "live": blocking["live"],
        "unknown_tier": blocking["unknown"],
        "latent_backlog": blocking["latent"],
    })
    checklist.append({"check": "no_active_teams", "ok": not teams_result["active"]})
    checklist.append({"check": f"verdict_coverage ({verdict_count}/{spec_count})", "ok": verdicts_complete})

    # GI-002 / FR-042 / FR-009 — THE CORPUS RE-EXECUTES BEFORE DONE. D-133.
    #
    # GI-002 (Locked) sweeps the whole corpus "before ASSAY/NYQUIST/DONE" and
    # this checklist had no evidence rung at all: report_generated,
    # escalated_classes_cleared, run_not_halted, spec_requirements_parsed,
    # all_verified, zero_blocking_defects, no_active_teams, verdict_coverage.
    # So a fix landing in F5 or F5.5 — which is exactly what the lead lane
    # US-005 opens is for — reached DONE with the committed evidence never
    # re-executed over it, and `fixes_after_decision`, the stamp that catches
    # the same shape one phase earlier, is read only by `inspect_clean`.
    #
    # STATED HERE rather than only in the two F6 transitions, because this
    # function IS the shared evaluation both F6 doors and both gates consult
    # (D-037 / D-043 / D-044). An evidence rung present in the transition and
    # absent from the gate would be the drift this helper exists to prevent:
    # the gate would pass a run the transition then refuses.
    #
    # The sweep is memoised on HEAD (see `_terminal_evidence_sweep`), so the
    # gate-then-transition pair pays for one re-execution rather than two, and
    # any commit between them invalidates the memo and re-runs it.
    #
    # RANKED AT `_GATE_RANK_EVIDENCE`, below the defect and verdict ledgers and
    # above nothing else at this door: re-capturing a log is defeated by every
    # open defect and every missing verdict, because the corpus moves again
    # when those are fixed and the log has to be captured a second time. That
    # is the same judgement the retired ladder made by putting this branch
    # LAST — D-190 is what it cost to make it by source position, where the
    # blocking-defect arm above simply lost its `reason` to this one and the
    # open LIVE defect the door was refusing on went unnamed.
    # D-149 — AND A SWEEP THAT COVERED NOTHING IS NOT A SWEEP THAT PASSED.
    #
    # `commands/start.md` mandates `git rm -r evidence/` as an F6 step, so the
    # tree this rung asks about may have no corpus in it at all. Driven at
    # cycle 8: the same non-reproducing log refused DONE before that strip and
    # passed after it, because `select_sweep_scope` reads the evidence
    # directory in the TREE and a deleted directory yields no logs — zero
    # mismatches, ok True, over nothing. The rung reported only the mismatch
    # count, so a vacuous sweep was indistinguishable from a clean whole-corpus
    # one in the very checklist the lead reads.
    #
    # Three states, and only three (the same three start.md documents):
    # corpus present, swept now; corpus absent with a recorded pre-strip pass,
    # which is what the mandated order EARNS by running Gate(done) before the
    # `git rm`; corpus absent with no such pass, which is refused. A run that
    # committed no evidence at all still passes — it has no corpus history to
    # have stripped, which is what `_evidence_corpus_existed` separates.
    # D-159: the three states are derived by `_terminal_evidence_state` and by
    # nothing else, so the NYQUIST entry — the third crossing GI-002 names —
    # refuses on exactly this evaluation rather than on `ok` alone.
    evidence_state = _terminal_evidence_state(fdir, project_root)
    evidence = evidence_state["evidence"]
    sweep_record = evidence_state["record"]
    logs_reexecuted = evidence_state["logs_reexecuted"]
    corpus_size = evidence_state["corpus_size"]
    prior_pass = evidence_state["prior_pass"]
    stripped = evidence_state["stripped"]
    evidence_ok = evidence_state["ok"]
    if not evidence_ok:
        refusal = _terminal_evidence_refusal(evidence_state, "mark the run DONE")
        ladder.fail(
            _GATE_RANK_EVIDENCE,
            (refusal or {})["error"].replace("Cannot mark the run DONE — ", ""),
            (refusal or {})["hint"],
        )
    checklist.append({
        # D-149: the number of logs, beside the mismatch count. A vacuous sweep
        # is visibly logs=0; the two numbers used to be one number.
        "check": (
            f"evidence_reproduces_at_head (logs={logs_reexecuted}, "
            f"mismatches={len(evidence.get('mismatches') or [])})"
        ),
        "ok": evidence_ok,
        "token": EVIDENCE_STRIPPED_TOKEN if stripped else "",
        "logs_reexecuted": logs_reexecuted,
        "corpus_size": corpus_size,
        "scope": sweep_record.get("scope", ""),
        "mismatches": [
            m.get("log", "?") for m in (evidence.get("mismatches") or [])
        ],
        "swept_head": evidence.get("head", ""),
        "cached": bool(evidence.get("cached")),
        # Present only when this door passed on the RECORDED pass rather than
        # on a sweep it just took, so a reader can never mistake one for the
        # other.
        "pre_strip_pass": prior_pass if corpus_size == 0 else None,
    })

    outcome = {
        "passed": ladder.passed,
        "checklist": checklist,
        # D-191: every failing check's own sentence, ranked, so the four the
        # F6 doors used to compute and discard are PUBLISHED. Both gates and
        # both transitions pass this through.
        "refusals": ladder.refusals(),
    }
    # Written through the RESULT rather than through two locals, exactly as
    # `foundry_gate` renders its own ladder: a function that never names
    # `reason` or `hint` as locals has nothing a later arm could overwrite,
    # which is the property `test_the_done_evaluation_ranks_every_arm_through_
    # named_constants` derives from this function's own AST.
    outcome["reason"], outcome["hint"] = ladder.outcome()
    return outcome


# --------------------------------------------------------------------------- #
# D-186 / D-183 / NFR-005 / FR-011 / AC-016 — ONE REFUSAL SPEAKS, AND IT IS THE
# ONE THAT HAS TO BE ACTED ON FIRST.
#
# `foundry_gate`'s branches were ladders of independent checks, each writing the
# function-locals `passed`, `reason` and `hint`, so the LAST failing check owned
# the two strings a terminal prints. That is a generator, not a bug in one rung:
# D-183 fixed the `.inspect-clean` rung by guarding it, and D-186 was the rung
# BELOW it — `no_active_teams` — doing the same thing one cycle later, and doing
# it while assigning `reason` and NO hint at all.
#
# Driven, before this change, through `server.call_tool` on synthetic runs:
#   * FULL/final_gate, one fixed defect, a registered team -> reason "Active
#     teams: foundry-cast" beside hint "Re-run the INSPECT this GRIND owes, then
#     close it with Foundry-Phase(phase='inspect_clean')". The hint answered a
#     different check than the reason, and FOLLOWING it was harmful: on the same
#     run `Foundry-Phase(phase='inspect_clean')` returned ok True and set F4, so
#     a lead reading the refusal crossed into ASSAY with the team still
#     registered — defeating the very check that refused.
#   * DELTA/delta, one fixed defect, a registered team -> reason "Active teams:
#     foundry-cast", with the FR-011 / AC-016 width refusal the arm above had
#     already computed discarded. D-183's displacement, one rung lower.
#   * teams-active as the only failure -> reason "Active teams: foundry-cast"
#     with hint "" — a refusal with no remedy at all.
#
# So the ordering is DECLARED rather than left to source position, and it is
# declared by one rule: THE REFUSAL THAT SPEAKS IS THE ONE WHOSE REMEDY IS NOT
# DEFEATED BY ANOTHER FAILING CHECK ON THE SAME CALL. A remedy the server would
# reject, or that another open check makes impossible, is D-011's shape — a
# refusal with no usable next move — and that is what ranking prevents. Ties
# break on declaration order, so a branch's own source order still decides
# between two checks of equal standing.
#
# `hint` is a REQUIRED positional on `fail`, so an arm cannot claim `reason`
# without stating what clears it; `test_every_gate_refusal_states_a_remedy`
# derives that obligation from this module's own AST rather than from a list.
#
# D-190 / D-191 — AND IT IS APPLIED TO `_done_preconditions` TOO.
#
# This block used to end "NOT applied to `_done_preconditions`, deliberately",
# on three grounds: that function documented an explicit ordering discipline of
# its own ("LAST of the reason-claiming branches ... means it WINS"), every one
# of its arms already carried a hint, and no instance of this class had been
# driven there. The third ground is what the other two rested on, and PROVE
# removed it by driving two — at the F6 doors, through `server.call_tool`:
#
#   * D-190. A CLEARED class with one open LIVE instance and a committed
#     evidence log that no longer reproduces. `Foundry-Gate('done')` answered
#     with the evidence rung — which claims `reason` after the blocking-defect
#     arm — and named the defect NOWHERE, returning a refusal byte-identical to
#     the one the same state produces with no defect open at all, while
#     `Foundry-Gate('nyquist')` named it. AC-003's two doors disagreed.
#   * D-191. Four checks failing at once rendered the verdict-coverage remedy,
#     which `Foundry-Gate('assay')` then refuses at `_GATE_RANK_DEFECTS` for
#     the very defect this door declined to mention; and `refusals` carried
#     exactly ONE entry — the single delegated verdict — so the three other
#     computed refusals were discarded at the two doors D-186 had not reached.
#
# "Every arm carries a hint" was never the guarantee: the guarantee is that the
# hint a lead READS belongs to the check that speaks, and a positional ladder
# cannot make that true. So the discipline is the same one, in the same
# mechanism, with the same rule.
# --------------------------------------------------------------------------- #

#: Remedy precedence at a gate. LOWER WINS. Each rank names the KIND of thing
#: that is wrong, and the ordering is the order in which a lead can actually act:
#: a remedy at rank N is not defeated by any failing check at a rank above it.
#:
#: D-190/D-191 added the four `_done_preconditions` needs and retired
#: `_GATE_RANK_DELEGATED` (0, "a verdict another evaluation already ordered"):
#: that rank existed because the delegated evaluation had no ranks of its own to
#: offer, and it now offers one per CHECK. `_GateLadder.absorb` re-enters those.
_GATE_RANK_HALTED = 0      # the run has already stopped: no other remedy can be
                           # gated at all, so this defeats every check and is
                           # defeated by none
_GATE_RANK_ESCALATION = 5  # a class still ESCALATED — the only remedy measured
                           # in CYCLES rather than calls. Fixing every LIVE
                           # defect does not clear one (ST-010 needs an arm), so
                           # the defect remedy is defeated by it and not the
                           # reverse; and every other remedy at the door can be
                           # executed inside the cycles this one costs
_GATE_RANK_WIDTH = 10      # the recorded INSPECT width — `inspect_start` clears
                           # it and no other open check can refuse that call
_GATE_RANK_TEAMS = 20      # a registered team still holds the tree; every
                           # remedy that spawns an agent is refused until it is
                           # down, and shutting it down is refused by nothing
_GATE_RANK_CONFLICT = 30   # two castings own one file — teammates about to
                           # corrupt each other's work
_GATE_RANK_DEFECTS = 40    # the defect ledger: what GRIND is for
_GATE_RANK_VERDICTS = 50   # the verdict ledger: what ASSAY is for
_GATE_RANK_EVIDENCE = 55   # a committed evidence log that no longer reproduces:
                           # re-capturing it is defeated by every open defect
                           # and every missing verdict, because the corpus moves
                           # again when those are fixed
_GATE_RANK_STREAMS = 60    # a required stream has not reported
_GATE_RANK_MARKER = 70     # a phase marker the previous transition writes
_GATE_RANK_CONFIG = 80     # how the run was configured or sized
_GATE_RANK_REPORT = 90     # the generated report: DERIVED from every ledger
                           # above it, so `Foundry-Report` succeeds whatever
                           # else is failing and produces a document the next
                           # cleared check invalidates. A remedy that must be
                           # repeated after another failing check is fixed is
                           # defeated by that check, which is why the cheapest
                           # call at this door is also the last thing to say

#: The one spelling of "shut the teammates down", so the three gate arms that
#: fall back to it cannot drift apart again (D-186: the assay copy had no
#: fallback at all, and the other two spelled theirs differently).
_TEAMS_DOWN_HINT = (
    "Shut down all teammates, call TeamDelete, then Foundry-Team-Down"
)


class _GateLadder:
    """The failing checks of one `foundry_gate` branch, ranked by remedy.

    ``fail(rank, reason, hint)`` records one failing check. ``outcome()``
    returns the ``(reason, hint)`` of the LOWEST-ranked failure — ties broken by
    declaration order — and ``refusals()`` returns every failure in that same
    order, so a computed refusal that loses the one rendered line is still
    PUBLISHED rather than discarded. "The width refusal the arm above computed
    is discarded" is how D-186 names the harm; nothing is discarded now, one
    thing simply speaks.
    """

    def __init__(self) -> None:
        # (rank, declaration order, reason, hint) — the second element is what
        # keeps the sort stable without depending on the sort's own guarantees.
        self._failures: list[tuple[int, int, str, str]] = []

    def fail(self, rank: int, reason: str, hint: str) -> None:
        """Record one failing check. ``hint`` is required, and is what clears
        THIS check — never what clears the one above or below it."""
        self._failures.append(
            (int(rank), len(self._failures), str(reason), str(hint))
        )

    def absorb(self, refusals: list[dict]) -> None:
        """Re-enter a DELEGATED evaluation's failures at their own ranks.

        `_done_preconditions` is one evaluation with four callers, and it ranks
        its own checks (D-190 / D-191). Before that it produced a single opaque
        verdict, so `foundry_gate`'s "done" branch entered it at a rank invented
        for the purpose — `_GATE_RANK_DELEGATED` — and published ONE refusals
        entry for a call that had computed four. Absorbing the ranked list
        instead keeps the delegated ordering intact (the ranks and the order are
        the ones that evaluation declared) AND publishes every one of them, so
        "nothing is discarded, one thing speaks" holds at the F6 doors on the
        same terms as everywhere else.

        A malformed entry is not silently dropped: `rank`, `reason` and `hint`
        are read positionally through the same `fail`, so an entry missing one
        raises here rather than losing a refusal downstream.
        """
        for refusal in refusals:
            self.fail(refusal["rank"], refusal["reason"], refusal["hint"])

    @property
    def passed(self) -> bool:
        return not self._failures

    def _ordered(self) -> list[tuple[int, int, str, str]]:
        return sorted(self._failures, key=lambda item: (item[0], item[1]))

    def outcome(self) -> tuple[str, str]:
        """``(reason, hint)`` for the refusal that speaks, or two empty
        strings when nothing failed."""
        ordered = self._ordered()
        if not ordered:
            return "", ""
        return ordered[0][2], ordered[0][3]

    def refusals(self) -> list[dict]:
        """Every failing check, highest-standing first."""
        return [
            {"rank": rank, "reason": reason, "hint": hint}
            for rank, _seq, reason, hint in self._ordered()
        ]


def foundry_gate(
    phase: str,
    project_root: str = ".",
) -> dict:
    """Check if preconditions are met to enter a phase."""
    # D-134: the shared nested-shape validator, lazily imported because
    # foundry_spawn imports this module at module top.
    from foundry_mcp.tools.foundry_spawn import _manifest_shape_problem

    fdir = get_run_dir(project_root)
    if not fdir:
        return {"phase": phase, "passed": False, "reason": "No active foundry run", "hint": "Call Foundry-Init first"}

    if not fdir.exists():
        return {"phase": phase, "passed": False, "reason": "foundry directory not found", "hint": "Run foundry_init first"}
    if (corrupt := _artifact_guard(fdir)):
        return {
            "phase": phase,
            "passed": False,
            "reason": corrupt["error"],
            "hint": corrupt["hint"],
            "corrupt_artifacts": corrupt["corrupt_artifacts"],
        }

    # ST-008 / CT-016 / D-081 — EVERY GATE REFUSES FROM HALTED, NAMING THE HALT.
    #
    # Ahead of the ordering-token check for the same reason the transition puts
    # it there: "you must call Foundry-Next first" is an instruction to prepare
    # for a call that cannot succeed. `done` and `nyquist_done` are the two that
    # made this a defect — `Foundry-Gate('done')` returned passed True on a
    # HALTED run, so the gate agreed the run could finish while state.json said
    # it had already stopped — but the answer is the same for every phase: a
    # halted run has no next gate, so no gate may report itself passed.
    #
    # Reshaped from `_halted_refusal`'s strings rather than re-worded: the gate
    # and the transition answering the same question in different words is the
    # drift this module has paid for at both F6 doors already.
    if (halted := _halted_refusal(fdir, f"Foundry-Gate(phase='{phase}')")) is not None:
        return {
            "phase": phase,
            "passed": False,
            "reason": halted["error"],
            "hint": halted["hint"],
            "checklist": [{
                "check": (
                    f"run_not_halted (halted_at_cycle="
                    f"{halted['halted_at_cycle']})"
                ),
                "ok": False,
                "halted_reason": halted["halted_reason"],
            }],
            "halted": True,
            "halted_at_cycle": halted["halted_at_cycle"],
            "halted_reason": halted["halted_reason"],
        }

    checklist: list[dict] = []
    # D-186: the three locals this used to carry (`passed`, `reason`, `hint`)
    # were a last-writer-wins ladder. Every failing check now enters the ladder
    # with its own rank and its own remedy; see `_GateLadder` above.
    ladder = _GateLadder()

    nac = fdir / NEXT_ACTION_CALLED_MARKER
    if not nac.exists():
        return {
            "phase": phase,
            "passed": False,
            "reason": "Must call Foundry-Next before any gate check",
            "hint": "Call Foundry-Next first — it shows the status display and tells you what to do next.",
            "checklist": [{"check": "next_action_called", "ok": False}],
        }
    # ST-011 / FR-044 / AC-035 / OT-028 — THE GATE NO LONGER CONSUMES THE TOKEN.
    #
    # This read `nac.unlink(missing_ok=True)`, so the documented sequence
    # Gate -> Phase could not be executed: `foundry_gate` destroyed the very
    # marker `foundry_mark_phase_complete` demands, and the lead's next call was
    # refused with "Must call Foundry-Next before phase transitions" for having
    # done exactly what start.md told it to. The workaround it forced — a
    # Foundry-Next between every gate and its transition — is what made
    # Foundry-Next look mandatory there, and FR-044 says it is optional.
    #
    # `foundry_mark_phase_complete` remains the ONE consumer. The token means
    # "a Foundry-Next preceded this transition", and a gate check is not a
    # transition: it writes no phase, advances no counter and, on the passing
    # path, only stamps `.gate-passed`. Reading a marker it does not act on and
    # then deleting it was never the handshake, it was a side effect of one.
    if phase == "validate":
        # Gate for F0.9 VALIDATE — castings must exist
        manifest = fdir / "castings" / "manifest.json"
        if not manifest.exists():
            return {"phase": phase, "passed": False, "reason": "No manifest.json", "hint": "Run DECOMPOSE first to create castings"}
        data = _load_json(manifest)
        if (records := _manifest_shape_problem(data)) is not None:
            return {"phase": phase, "passed": False, "reason": records,
                    "hint": "Re-run F0.5 DECOMPOSE — the manifest's records are unusable"}
        count = len(data.get("castings", []))
        checklist.append({"check": "manifest_exists", "ok": True})
        if count < 1:
            return {"phase": phase, "passed": False, "reason": "No castings in manifest", "hint": "Add castings before validating"}
        checklist.append({"check": f"castings_count={count}", "ok": True})

    elif phase == "cast":
        manifest = fdir / "castings" / "manifest.json"
        if not manifest.exists():
            return {"phase": phase, "passed": False, "reason": "No manifest.json", "hint": "Run foundry_init and add castings"}
        data = _load_json(manifest)
        if (records := _manifest_shape_problem(data)) is not None:
            return {"phase": phase, "passed": False, "reason": records,
                    "hint": "Re-run F0.5 DECOMPOSE — the manifest's records are unusable"}
        count = len(data.get("castings", []))
        checklist.append({"check": "manifest_exists", "ok": True})
        if count < 1:
            return {"phase": phase, "passed": False, "reason": "No castings in manifest", "hint": "Add castings before CAST"}
        checklist.append({"check": f"castings_count={count}", "ok": True})

        oversized = []
        for c in data.get("castings", []):
            kf = len(c.get("key_files", []))
            if kf > 8:
                oversized.append({"id": c.get("id"), "title": c.get("title", ""), "key_files": kf})
        if oversized:
            names = ", ".join(f"#{c['id']} ({c['key_files']} files)" for c in oversized)
            ladder.fail(
                _GATE_RANK_CONFIG,
                f"Oversized castings: {names}. Max 8 key_files per casting.",
                "Split large castings into smaller ones (2-5 tasks, 2-8 files "
                "each). No teammate should get 1000 lines of work.",
            )
            checklist.append({"check": "casting_size", "ok": False, "oversized": oversized})

        file_to_casting: dict[str, list[int]] = {}
        for c in data.get("castings", []):
            cid = c.get("id", 0)
            for f in c.get("key_files", []):
                file_to_casting.setdefault(f, []).append(cid)
        overlaps = {f: cids for f, cids in file_to_casting.items() if len(cids) > 1}
        if overlaps:
            overlap_details = [f"{f}: castings {cids}" for f, cids in overlaps.items()]
            # Ranked ABOVE the size check: an oversized casting is a slow wave,
            # a shared file is two teammates overwriting each other, and the
            # second has to be resolved before the first is worth resizing.
            ladder.fail(
                _GATE_RANK_CONFLICT,
                f"File overlap between castings: {'; '.join(overlap_details)}",
                "Two castings editing the same file will cause conflicts. Move "
                "shared files to an earlier casting or merge the overlapping "
                "castings.",
            )
            checklist.append({"check": "no_file_overlap", "ok": False, "overlaps": overlaps})
        else:
            checklist.append({"check": "no_file_overlap", "ok": True})

    elif phase == "inspect":
        if not (fdir / CAST_COMPLETE_MARKER).exists():
            ladder.fail(
                _GATE_RANK_MARKER,
                "CAST not complete",
                "Complete all CAST tasks and call Foundry-Phase(phase='cast')",
            )
            checklist.append({"check": "cast_complete", "ok": False})
        else:
            checklist.append({"check": "cast_complete", "ok": True})

        teams_result = _check_active_teams(project_root)
        if teams_result["active"]:
            parts = []
            if teams_result["teams"]:
                parts.append(f"Team dirs: {', '.join(teams_result['teams'])}")
            if teams_result.get("live_panes"):
                parts.append(f"Live panes: {', '.join(teams_result['live_panes'])}")
            ladder.fail(
                _GATE_RANK_TEAMS,
                f"Active teammates: {'; '.join(parts)}",
                teams_result.get("hint", _TEAMS_DOWN_HINT),
            )
            checklist.append({"check": "no_active_teams", "ok": False,
                            "teams": teams_result["teams"],
                            "live_panes": teams_result.get("live_panes", [])})
        else:
            checklist.append({"check": "no_active_teams", "ok": True})

        sight = _check_sight_required(project_root)
        if sight.get("required") and sight.get("blocked"):
            ladder.fail(
                _GATE_RANK_CONFIG,
                sight["reason"],
                "Provide --url for SIGHT audit or update manifest.json target_url",
            )
            checklist.append({"check": "sight_url", "ok": False, "reason": sight["reason"]})
        else:
            checklist.append({"check": "sight_url", "ok": True})

    elif phase == "grind":
        defects = _load_json(fdir / "defects.json")
        open_count = sum(1 for d in defects.get("defects", []) if d.get("status") == "open")
        if open_count < 1:
            # Ranked ABOVE the tasks marker: on a ledger with nothing open,
            # `Foundry-Tasks` has nothing to packet, so telling the lead to call
            # it — which is what the last-writer-wins ladder did — sends them to
            # do work that cannot change this gate's answer.
            ladder.fail(
                _GATE_RANK_DEFECTS,
                "No open defects to grind",
                "Nothing to fix — skip to ASSAY",
            )
        checklist.append({"check": f"open_defects={open_count}", "ok": open_count >= 1})

        teams_result = _check_active_teams(project_root)
        if teams_result["active"]:
            parts = []
            if teams_result["teams"]:
                parts.append(f"Team dirs: {', '.join(teams_result['teams'])}")
            if teams_result.get("live_panes"):
                parts.append(f"Live panes: {', '.join(teams_result['live_panes'])}")
            ladder.fail(
                _GATE_RANK_TEAMS,
                f"Active teammates: {'; '.join(parts)}",
                teams_result.get("hint", _TEAMS_DOWN_HINT),
            )
        checklist.append({"check": "no_active_teams", "ok": not teams_result["active"],
                         "live_panes": teams_result.get("live_panes", [])})

        if not (fdir / TASKS_GENERATED_MARKER).exists():
            ladder.fail(
                _GATE_RANK_MARKER,
                "defects-to-tasks has not been run",
                "Call Foundry-Tasks before entering GRIND",
            )
        checklist.append({"check": "tasks_generated", "ok": (fdir / TASKS_GENERATED_MARKER).exists()})

    elif phase == "assay":
        # CT-008 / FR-006: LIVE and unknown-tier defects block ASSAY exactly as
        # every open defect used to; a LATENT-only backlog does not. The tier is
        # an evidence grade, never a severity — both tiers are defects and both
        # get fixed — but a scan-derivation gap with no reachable instance no
        # longer holds the whole run at the gate a forged evidence log holds it.
        blocking = _blocking_defects(fdir)
        defects = _load_json(fdir / "defects.json")
        open_count = blocking["blocking"]
        if open_count > 0:
            ladder.fail(_GATE_RANK_DEFECTS, blocking["reason"], blocking["hint"])
        checklist.append({
            "check": (
                f"zero_blocking_defects (live={len(blocking['live'])} "
                f"unknown_tier={len(blocking['unknown'])} "
                f"latent_backlog={len(blocking['latent'])})"
            ),
            "ok": open_count == 0,
            "live": blocking["live"],
            "unknown_tier": blocking["unknown"],
            "latent_backlog": blocking["latent"],
        })

        streams = _check_streams_complete(project_root)
        if not streams["complete"]:
            # D-122 / FR-012 / AC-017 — THE HINT NAMES THE ROSTER THE
            # TRANSITION RECORDED, NOT THE ONE THAT PRE-DATES THE WIDTH.
            #
            # This read "All streams (trace, prove, sight, test) must complete
            # before ASSAY" — the roster this gate required before FR-012 made
            # research_audit and test01 required in FULL mode. Driven on a FULL
            # cycle whose recorded `required_streams` are trace, prove, test,
            # research_audit, test01 with only the last two unmarked: the
            # refusal said "incomplete: research_audit test01" and the hint
            # beside it named NEITHER of them and named `sight`, which this
            # run's recorded roster does not require. AC-017's clause is that
            # the required set is named EXACTLY, and a hint listing a different
            # set than the reason it sits under sends the lead to run streams
            # the cycle does not owe while the two it does owe go unmentioned.
            #
            # Read from `streams["required"]`, which `_check_streams_complete`
            # takes off the recorded decision (GI-008) — the same list the
            # reason's `missing` is computed from, so the two halves of one
            # refusal cannot disagree again.
            required_now = streams.get("required") or []
            ladder.fail(
                _GATE_RANK_STREAMS,
                f"Verification streams incomplete: {streams.get('missing', '')}",
                "This INSPECT's recorded roster is "
                + (", ".join(required_now) if required_now else "not recorded")
                + f" — every one of them must complete before ASSAY. Missing: "
                f"{streams.get('missing', '') or 'none'}. Run each missing "
                "stream, then Foundry-Stream(stream, cycle, items_checked).",
            )
        checklist.append({"check": "all_streams_complete", "ok": streams["complete"],
                         "missing": streams.get("missing", "")})

        # AC-016 / D-068 — THE WIDTH THIS GATE WAS HANDED, CHECKED BY NAME.
        #
        # US-004's premise is "every final gate still runs everything at full
        # width", and this gate read the ROSTER's completeness without ever
        # asking how wide that roster was. Stated as its own named check beside
        # the transition's identical refusal, so the two doors into ASSAY agree
        # — the drift shape this module has paid for twice already.
        #
        # D-117 — AND "unrecorded" IS NOT full width EITHER.
        #
        # The test was `mode != "DELTA"`, which "unrecorded" passes, so this
        # gate PASSED carrying its own checklist line
        # `inspect_ran_at_full_width (mode=unrecorded rule=unrecorded) ok=True`
        # — the check naming, in the same string, the fact that made its verdict
        # false. The width is now asserted POSITIVELY: FULL, or refuse.
        assay_mode = _current_inspect_mode(fdir) or {}
        assay_unrecorded = _unrecorded_width_problem(fdir)
        assay_width_ok = assay_mode.get("mode") == "FULL"
        if assay_unrecorded is not None:
            ladder.fail(
                _GATE_RANK_WIDTH,
                f"Cannot open ASSAY — {assay_unrecorded['reason']}",
                assay_unrecorded["hint"],
            )
        # D-169 — AND THE SENTENCE STATES THE PREDICATE THIS CODE EVALUATES.
        #
        # The check is `assay_width_ok = mode == "FULL"`, and its checklist
        # entry is literally named `inspect_ran_at_full_width` — WIDTH. The
        # refusal beside it said "ASSAY is only opened by an INSPECT whose
        # recorded rule is final_gate", which is a condition on the RULE and is
        # false: driven at this door on synthetic runs recorded FULL/final_gate,
        # FULL/first_of_phase and FULL/verifier_touched, all three returned
        # ok True. A verifier-touching GRIND is the ordinary case for a run that
        # builds this plugin, so a lead reading the old sentence believed a
        # clean FULL/verifier_touched INSPECT still owed a widening cycle — one
        # more cycle of ceremony, which is the cost US-004 exists to remove.
        #
        # D-152 fixed the rule PRECEDENCE so the recorded rule names the arm
        # that fired, and left every stated condition naming a rule no code
        # reads. So the mode and the rule are now reported as the FACTS they
        # are, and the condition quoted is the one evaluated one line above.
        elif not assay_width_ok:
            ladder.fail(
                _GATE_RANK_WIDTH,
                f"cycle {assay_mode.get('cycle', '?')} ran at "
                f"{assay_mode.get('mode') or 'unrecorded'} width (rule "
                f"{assay_mode.get('rule') or 'unrecorded'}) — ASSAY is only "
                "opened by an INSPECT whose recorded mode is FULL",
                "Call Foundry-Phase(phase='inspect_start') again from F2: the "
                "widening re-open advances the cycle, sweeps the whole evidence "
                "corpus and records FULL. Which rule it records — final_gate "
                "from the widening re-open, verifier_touched when the diff "
                "cannot be measured — does not enter this check; the width "
                "does.",
            )
        checklist.append({
            "check": (
                f"inspect_ran_at_full_width (mode={assay_mode.get('mode') or 'unrecorded'} "
                f"rule={assay_mode.get('rule') or 'unrecorded'})"
            ),
            "ok": assay_width_ok,
        })

        if not (fdir / INSPECT_CLEAN_MARKER).exists():
            has_fixed = sum(1 for d in defects.get("defects", []) if d.get("status") == "fixed")
            if has_fixed > 0:
                # D-183 — AND IT DOES NOT DISPLACE THE WIDTH REFUSAL ABOVE IT.
                #
                # `has_fixed > 0` is true of every ordinary GRIND cycle — a
                # GRIND that fixed nothing is not a GRIND — so under the
                # last-writer-wins ladder this arm overwrote the width refusal
                # on the normal path.
                #
                # Driven through server.call_tool on a DELTA cycle carrying one
                # fixed defect: the gate answered "GRIND fixed defects but
                # INSPECT has not re-verified" with the hint below, and
                # following that hint Foundry-Phase(phase='inspect_clean') was
                # REFUSED — "ran at DELTA width (rule delta), and ASSAY is only
                # opened by an INSPECT whose recorded mode is FULL". The gate
                # had computed that very sentence one check earlier, in the
                # `elif not assay_width_ok` arm above, and thrown it away. The
                # control drive on the identical run with zero fixed defects
                # surfaced the width refusal correctly, which is what proved the
                # arm right and merely shadowed. This is D-123's own class —
                # a remedy naming a call the server rejects — reopened on
                # D-123's own symbol by a later check.
                #
                # Ruling 4 in the run's spec_ambiguities and start.md's
                # ASSAY-door paragraph both make the recorded WIDTH the whole
                # condition: "From a cycle recorded `DELTA`: call
                # `Foundry-Phase(phase='inspect_start')` AGAIN, from F2."
                #
                # D-186 — AND THE GUARD THAT SAID SO IS NOW THE RANK.
                #
                # D-183 expressed that ruling as an `if assay_unrecorded is None
                # and assay_width_ok:` wrapper around these two strings, which
                # fixed this rung and left the rung below it — `no_active_teams`
                # — displacing the width refusal in exactly the same way. The
                # ordering is declared once, in `_GATE_RANK_WIDTH` versus
                # `_GATE_RANK_MARKER`, so this arm states its own refusal
                # unconditionally and the ladder decides which one speaks. The
                # check is not weakened: it still fails, and its checklist entry
                # below is still `ok: False`.
                ladder.fail(
                    _GATE_RANK_MARKER,
                    "GRIND fixed defects but INSPECT has not re-verified",
                    # D-123 / FR-044 / AC-035 — THE REMEDY NAMES A CALL THAT
                    # EXISTS.
                    #
                    # This hint read "Call foundry_mark_inspect_clean when
                    # clean." `grep -rn foundry_mark_inspect_clean
                    # plugins/foundry` found the name in this string and NOWHERE
                    # else: no MCP tool, no Python function, no prose surface
                    # carries it. The door that closes an INSPECT is
                    # Foundry-Phase(phase='inspect_clean'), which FR-006 and
                    # AC-008 name and which FR-044's Gate-then-Phase sequence
                    # relies on. A refusal whose only stated next move is a call
                    # the server would reject is a refusal with no remedy — the
                    # D-011 shape, on this door. It is a truthful remedy only at
                    # a FULL width and with the teammates down, which is what
                    # ranks WIDTH and TEAMS above this arm expresses.
                    "Re-run the INSPECT this GRIND owes, then close it with "
                    "Foundry-Phase(phase='inspect_clean') — that transition "
                    "writes the .inspect-clean marker this check reads, and it "
                    "is the only call that does.",
                )
            checklist.append({"check": "inspect_clean", "ok": False})
        else:
            checklist.append({"check": "inspect_clean", "ok": True})

        teams_result = _check_active_teams(project_root)
        if teams_result["active"]:
            # D-186 — THIS ARM SET `reason` AND NO HINT AT ALL.
            #
            # Its two siblings in the `inspect` and `grind` branches both read
            # their remedy off `teams_result`; this copy read neither, so a run
            # whose ONLY failure was a registered team was refused with an empty
            # `hint` — a refusal with no stated next move — and a run with a
            # second failure inherited whatever string the arm above happened to
            # leave behind. Both halves are closed by the same line: the remedy
            # comes from the scan, with the ONE shared fallback the other two
            # arms now also use.
            ladder.fail(
                _GATE_RANK_TEAMS,
                f"Active teams: {', '.join(teams_result['teams'])}",
                teams_result.get("hint", _TEAMS_DOWN_HINT),
            )
        checklist.append({"check": "no_active_teams", "ok": not teams_result["active"]})

    elif phase == "temper":
        verdicts = _load_json(fdir / "verdicts.json")
        non_verified = sum(1 for r in verdicts.get("requirements", []) if r.get("verdict") != "VERIFIED")
        if non_verified > 0:
            ladder.fail(
                _GATE_RANK_VERDICTS,
                f"{non_verified} requirement(s) not verified",
                "Every THIN / PARTIAL requirement is a defect, not a follow-up. "
                "Fix them and re-run ASSAY before entering TEMPER.",
            )
        checklist.append({"check": f"all_verified (non_verified={non_verified})", "ok": non_verified == 0})

        # CT-008 / FR-006 — TEMPER gains the defect read it never had.
        # AC-008 names temper alongside assay, nyquist and done: all four pass
        # on a LATENT-only backlog, and all four refuse on a LIVE or
        # unknown-tier defect. A gate that reads no defects at all cannot honour
        # either half of that.
        blocking = _blocking_defects(fdir)
        if blocking["blocking"] > 0:
            ladder.fail(_GATE_RANK_DEFECTS, blocking["reason"], blocking["hint"])
        checklist.append({
            "check": (
                f"zero_blocking_defects (live={len(blocking['live'])} "
                f"unknown_tier={len(blocking['unknown'])} "
                f"latent_backlog={len(blocking['latent'])})"
            ),
            "ok": blocking["blocking"] == 0,
            "live": blocking["live"],
            "unknown_tier": blocking["unknown"],
            "latent_backlog": blocking["latent"],
        })

    elif phase == "nyquist":
        # F5.5 generates regression tests for VERIFIED requirements, so the
        # same precondition as TEMPER applies: there is nothing to lock in
        # until every requirement has passed ASSAY. Additionally the flag must
        # actually be set — entering F5.5 on a run that never asked for it
        # would spawn auditors the invocation did not request.
        verdicts = _load_json(fdir / "verdicts.json")
        non_verified = sum(1 for r in verdicts.get("requirements", []) if r.get("verdict") != "VERIFIED")
        if non_verified > 0:
            ladder.fail(
                _GATE_RANK_VERDICTS,
                f"{non_verified} requirement(s) not verified",
                "Every THIN / PARTIAL requirement is a defect, not a follow-up. "
                "Fix them and re-run ASSAY before entering NYQUIST.",
            )
        checklist.append({"check": f"all_verified (non_verified={non_verified})", "ok": non_verified == 0})

        # FR-006 / CT-008 / OT-006 — NYQUIST GAINS THE MISSING DEFECT READ.
        #
        # This branch read verdicts and the --nyquist flag and NOTHING ELSE, so
        # a run could enter F5.5 and generate regression tests with open defects
        # in the ledger — locking in behaviour a stream had already ruled wrong.
        # FR-006 names it outright: "NYQUIST gains the missing defect read so
        # one open LIVE now blocks it." A LATENT-only backlog still passes,
        # which is the whole point of grading the evidence.
        blocking = _blocking_defects(fdir)
        if blocking["blocking"] > 0:
            ladder.fail(_GATE_RANK_DEFECTS, blocking["reason"], blocking["hint"])
        checklist.append({
            "check": (
                f"zero_blocking_defects (live={len(blocking['live'])} "
                f"unknown_tier={len(blocking['unknown'])} "
                f"latent_backlog={len(blocking['latent'])})"
            ),
            "ok": blocking["blocking"] == 0,
            "live": blocking["live"],
            "unknown_tier": blocking["unknown"],
            "latent_backlog": blocking["latent"],
        })

        state = _load_json(fdir / "state.json")
        nyquist_on = state.get("nyquist", False)
        if not nyquist_on:
            # Ranked BELOW the defect read, and that is a change from the
            # source-order ladder this replaced. The remedy here offers
            # "call Foundry-Gate(phase='done')" as the way past F5.5, and that
            # call is refused while a LIVE or unknown-tier defect is open
            # (CT-008) — so on a run failing both checks the old last-writer
            # rendered the remedy the other failing check would reject. Same
            # shape as D-183 and D-186, one branch over.
            ladder.fail(
                _GATE_RANK_CONFIG,
                "F5.5 NYQUIST is opt-in and this run was not started with --nyquist",
                "Re-run with --nyquist, or skip F5.5: call Foundry-Gate(phase='done').",
            )
        checklist.append({"check": "nyquist_enabled", "ok": nyquist_on})

    elif phase in ("done", "nyquist_done"):
        # Every check lives in _done_preconditions, which the transitions that
        # actually enter F6 call too (AC-011 / D-037 / D-043 / D-044). This
        # branch is the advisory half of one shared evaluation, not a second
        # opinion.
        #
        # BOTH terminal tokens land here. F6 has two doors — Foundry-Phase
        # "done" and, on a --nyquist run, "nyquist_done" — and only the first
        # had a gate case at all, so a lead following start.md:578
        # (Foundry-Gate("done") -> Foundry-Phase("nyquist_done")) was gating a
        # token other than the one it was about to call. There is one
        # definition of "the run may finish"; asking about either door asks it.
        outcome = _done_preconditions(fdir, project_root)
        # D-186 / D-190 / D-191: `_done_preconditions` applies the SAME ladder
        # to its own arms, so what arrives here is already ranked per check —
        # not one opaque verdict. `absorb` re-enters every one of them at the
        # rank that evaluation declared, so the ordering is still owned by the
        # evaluation that owns the checks AND this door publishes all of them
        # under `refusals`. It used to publish exactly one entry for a call that
        # had computed four, which is how "Call Foundry-Report" — the only
        # remedy on D-191's drive that no other failing check defeats — was
        # stated nowhere a lead could read it.
        ladder.absorb(outcome["refusals"])
        checklist.extend(outcome["checklist"])

    else:
        return {"phase": phase, "passed": False, "reason": f"Unknown phase: {phase}",
                "hint": ("Valid phases: validate, cast, inspect, grind, assay, "
                         "temper, nyquist, nyquist_done, done")}

    result = {"phase": phase, "passed": ladder.passed, "checklist": checklist}
    if not ladder.passed:
        result["reason"], result["hint"] = ladder.outcome()
        # D-186: every failing check's own sentence, in the same order, so a
        # refusal that loses the one rendered line is PUBLISHED rather than
        # discarded — "the width refusal the arm above computed is discarded"
        # is how the defect names the harm. `reason` and `hint` are still the
        # single pair a terminal prints (NFR-005); this is the machine-readable
        # rest of what the gate worked out.
        result["refusals"] = ladder.refusals()
    else:
        # P4 (FR-005 / ST-002): a passing gate advances the guidance state.
        # Record which gate passed so the next Foundry-Next emits the
        # transition step instead of re-running this now-satisfied gate.
        # Cleared by _update_phase when the phase actually advances.
        try:
            (fdir / GATE_PASSED_MARKER).write_text(
                json.dumps({"phase": phase, "at": _now()}),
                encoding="utf-8",
            )
        except OSError:
            pass
    return result


# --- Stream markers ---

# Verification streams recordable via Foundry-Stream.
#
# FR-013 / CT-002: this is no longer a declaration, it is a READ of the one
# canonical vocabulary module. The set used to be re-typed here, in server.py's
# JSON-Schema enum, in foundry_sync_defects' local `valid_sources`, in two
# display loops and in the marker-clear lists — six copies that drifted
# independently (the schema advertised streams the runtime rejected; the sync
# coercion set disagreed with both). Every one of those sites now reads
# schemas/vocab.py.
#
# The name is retained as an alias because it is part of this module's public
# surface. Recordable is NOT required: the required-stream computation in
# _check_streams_complete is intentionally independent.
VALID_STREAMS = STREAM_WIRE_IDS


# --------------------------------------------------------------------------- #
# Per-cycle stream roll-up (FR-014 / CT-003 / AC-020 / OT-009).
#
# The `.{stream}-complete` marker is a single file OVERWRITTEN on every record,
# so it can only ever carry the last write. That is why the old drop-warning
# compared against "the previous write of this file" rather than against cycle
# N-1, and why a PROVE run delivered as two partial tranches lost the first one.
#
# The roll-up is the accumulation surface the marker cannot be: records are
# appended under the SERVER cycle counter, partial records are accepted and
# stored as they arrive, coverage thresholds are evaluated exactly once per
# cycle at the streams-complete check (where the full picture exists), and drop
# warnings compare cycle N's total against cycle N-1's total.
#
# Accumulation rule across the records of one cycle:
#   items_checked, findings -> SUM  (each record covers a disjoint tranche)
#   items_total             -> MAX  (every tranche reports the same population
#                                    denominator; summing would multiply it)
# --------------------------------------------------------------------------- #

ROLLUP_FILENAME = "stream-rollup.json"


def _recorded_stream_scope(fdir: Path, cycle: int, stream: str) -> str:
    """The scope THIS cycle's recorded decision drew for one stream (D-139).

    ``"full"`` / ``"delta"`` / ``"skipped"`` as `_decide_inspect_mode` wrote
    them, or ``""`` when no decision applies to this cycle — an older archive,
    a run whose INSPECT was opened before the width existed, or a decision
    stamped with a different cycle number.

    The cycle stamp is checked, not assumed: a caller reading a scope off a
    decision made for another cycle would be narrowing this one against a width
    nothing recorded for it — GI-008's named violation, arriving through a
    helper instead of through a recomputation.

    D-216: that check now lives in the read, as its `cycle` argument, rather
    than being re-stated here. It was one of the two places the module already
    held the rule while `_current_inspect_mode` itself returned the newest entry
    whatever crossing wrote it, so the narrow readers were guarded and the doors
    that decide on the width were not. One subject, passed in; not two readers.
    """
    recorded = _current_inspect_mode(fdir, cycle)
    if not isinstance(recorded, dict):
        return ""
    scope = recorded.get("stream_scope")
    if not isinstance(scope, dict):
        return ""
    entry = scope.get(stream)
    if not isinstance(entry, dict):
        return ""
    value = entry.get("scope")
    return value if isinstance(value, str) else ""


def _rollup_totals(fdir: Path, cycle: int, stream: str) -> dict | None:
    """Return this cycle's accumulated totals for one stream, or None.

    None means the cycle has no record for that stream at all \u2014 distinct from
    a recorded zero, which callers must be able to tell apart.
    """
    data = _load_json(fdir / ROLLUP_FILENAME)
    entry = data.get("cycles", {}).get(str(cycle), {}).get(stream)
    if not isinstance(entry, dict):
        return None
    return {
        "items_checked": entry.get("items_checked", 0),
        "items_total": entry.get("items_total", 0),
        "findings": entry.get("findings", 0),
        "records": len(entry.get("records", [])),
    }


def _record_stream_rollup(
    fdir: Path,
    cycle: int,
    stream: str,
    items_checked: int,
    items_total: int,
    findings_count: int,
    declared_cycle: int,
) -> dict:
    """Append one (possibly partial) stream record to the cycle's roll-up.

    ``declared_cycle`` is what the caller asserted; it is retained per record
    for audit but is NEVER the key \u2014 the key is the server counter (FR-005).
    Returns the cycle's totals AFTER this record.
    """
    path = fdir / ROLLUP_FILENAME
    # D-103: THE concurrency site. F2 runs 4-8 parallel streams and each calls
    # Foundry-Stream as it finishes, so the read-modify-write here is the
    # designed path, not an edge case. Unlocked, a 4-process x 40-call drive
    # lost 107 of 160 tranches — a direct violation of CT-003's "accepted and
    # stored as they arrive".
    with _document_transaction(path) as data:
        cycles = data.setdefault("cycles", {})
        if not isinstance(cycles, dict):
            cycles = data["cycles"] = {}
        bucket = cycles.setdefault(str(cycle), {})
        entry = bucket.setdefault(
            stream, {"items_checked": 0, "items_total": 0, "findings": 0, "records": []}
        )

        entry["records"].append(
            {
                "recorded_at": _now(),
                "items_checked": items_checked,
                "items_total": items_total,
                "findings": findings_count,
                "declared_cycle": declared_cycle,
            }
        )
        entry["items_checked"] = entry.get("items_checked", 0) + items_checked
        entry["items_total"] = max(entry.get("items_total", 0), items_total)
        entry["findings"] = entry.get("findings", 0) + findings_count

        data["updated_at"] = _now()
    return {
        "items_checked": entry["items_checked"],
        "items_total": entry["items_total"],
        "findings": entry["findings"],
        "records": len(entry["records"]),
    }


def _recorded_prove_roster(fdir: Path, cycle: int) -> list[str] | None:
    """The PROVE rows THIS cycle's recorded DELTA decision named, or None.

    None means "no DELTA roster applies to this cycle" — the run is at FULL, the
    run predates the width, or the newest recorded decision belongs to another
    cycle — and the caller then measures against the spec exactly as it did
    before the width existed. An empty LIST is a different answer from None: the
    decision recorded a roster and the roster is empty, which
    `_prove_delta_sample` produces only when the spec parses to zero rows.

    THE CYCLE MUST MATCH, and that is not defensive padding. A width is a fact
    about one crossing: a roster decided for cycle 5 says nothing about what
    cycle 4's PROVE owed. On the live path the two are equal by construction —
    `inspect_start` stamps `entry["cycle"]` from the same counter
    `_check_streams_complete` and `foundry_mark_stream` read — so the check
    costs nothing there, and a resumed archive whose counter and ledger
    disagree falls back rather than measuring one cycle's work against another
    cycle's roster.

    D-216: the match is made by the read, which takes the cycle as its subject,
    rather than being re-stated here against an entry the read had already
    handed back unconditionally. This helper and `_recorded_stream_scope` were
    the two places the module held the rule while the read that every DECIDING
    door goes through did not.
    """
    recorded = _current_inspect_mode(fdir, cycle)
    if not recorded or recorded.get("mode") != "DELTA":
        return None
    sample = recorded.get("prove_sample")
    if not isinstance(sample, list):
        return None
    return [row for row in sample if isinstance(row, str)]


def _coverage_shortfall(fdir: Path, project_root: str, stream: str, cycle: int) -> dict | None:
    """Evaluate this stream's per-cycle coverage threshold, once, on the total.

    Returns a named shortfall dict, or None when the stream either has no
    threshold or clears it. Called from ``_check_streams_complete`` \u2014 the
    streams-complete check is the single point where the whole cycle's records
    are in hand (CT-003). ``foundry_mark_stream`` deliberately does NOT
    evaluate it: a partial tranche must be stored, not refused.

    Falls back to the marker's aggregate counts when this cycle has no roll-up
    entry, exactly as ``_prove_is_clean`` does. Without the fallback an archive
    written before the roll-up existed — or one whose roll-up was lost — had a
    marker the streams-complete check counted as PRESENT while the threshold
    silently evaluated nothing, so 40% coverage passed. "No numbers" must mean
    "read them from the marker", never "assume the threshold is met".

    D-080 — THE PROVE THRESHOLD IS MEASURED AGAINST THE WIDTH THE SERVER
    RECORDED, AND UNTIL IT WAS, DELTA WAS UNREACHABLE ON THE GUIDED PATH.
    -------------------------------------------------------------------
    `_check_streams_complete` was made to READ the recorded roster — GI-008
    names "a streams-complete check that reads a roster nothing recorded" as
    the violation — and then handed each member of that roster to this
    function, which measured PROVE against
    `_count_spec_requirements(project_root) * 0.95` and consulted no recorded
    decision at all. So the one check that CONSUMES the roster ignored the
    width the `inspect_start` transition had just decided, and a PROVE that
    checked exactly the roster the server itself recorded was reported
    incomplete forever.

    Driven end to end on a 40-requirement spec with a recorded DELTA decision
    whose `prove_sample` is 12 rows: `Foundry-Stream(prove, items_checked=12)`
    warned "PROVE checked 12 requirements across cycle 5 but the spec has 40.
    Coverage is 30%"; `_check_streams_complete` returned complete False,
    missing 'prove'; `Foundry-Phase('inspect_clean')` refused with "streams
    incomplete: prove"; and `Foundry-Next`'s F2 branch returned
    action=run_streams with "Missing: prove" and kept returning it. Re-marking
    prove at 38/40 cleared it immediately — that is, a DELTA cycle could only
    be closed by running PROVE at FULL width, the lead never reached the
    DELTA-width refusal that would have told them to widen, and AC-018's
    saving was exactly zero.

    NO 0.95 SLACK ON THE DELTA ARM. At FULL the denominator is the whole spec
    and the 5% is tolerance for a matrix that moved under a long stream. A
    DELTA roster is a NAMED, FINITE list of rows the server itself drew — the
    rows tied to the defects the preceding GRIND fixed, plus the sampled
    remainder, by id — so "which of these did you not check" has an answer and
    there is nothing to be tolerant of. `checked >= len(roster)` is the whole
    test.
    """
    totals = _rollup_totals(fdir, cycle, stream) or _marker_counts(
        fdir / _stream_marker(stream)
    )
    if totals is None:
        return None
    checked = totals["items_checked"]

    if stream == "prove":
        # AC-018 / FR-012 / FR-013 / AC-017: the recorded roster first. A DELTA
        # cycle owes the rows its own decision named and nothing else; only a
        # cycle with no DELTA roster falls through to the spec-wide threshold.
        roster = _recorded_prove_roster(fdir, cycle)
        if roster is not None:
            required = len(roster)
            if required > 0 and checked < required:
                return {
                    "stream": "prove",
                    "checked": checked,
                    "required": required,
                    "coverage": f"{checked / required * 100:.0f}%",
                    "mode": "DELTA",
                    "roster": roster,
                    "reason": (
                        f"PROVE checked {checked} of the {required} requirement "
                        f"row(s) cycle {cycle}'s recorded DELTA roster names "
                        f"({', '.join(roster[:12])}"
                        + (", ..." if len(roster) > 12 else "")
                        + "). That roster IS this INSPECT's width — the rows "
                        "tied to the defects the preceding GRIND fixed plus the "
                        "sampled remainder — so every row in it is owed, and no "
                        "row outside it is."
                    ),
                }
            return None

        spec_count = _count_spec_requirements(project_root)
        if spec_count > 0 and checked < spec_count * 0.95:
            return {
                "stream": "prove",
                "checked": checked,
                "required": spec_count,
                "coverage": f"{checked / spec_count * 100:.0f}%",
                "reason": (
                    f"PROVE checked {checked} requirements across cycle {cycle} but the "
                    f"spec has {spec_count}. Coverage is {checked / spec_count * 100:.0f}% "
                    "\u2014 must be \u226595%."
                ),
            }
        return None

    if stream == "trace":
        declared = totals["items_total"]
        if declared > 0 and checked < declared * 0.95:
            return {
                "stream": "trace",
                "checked": checked,
                "required": declared,
                "coverage": f"{checked / declared * 100:.0f}%",
                "reason": (
                    f"TRACE checked {checked}/{declared} symbols across cycle {cycle} "
                    f"({checked / declared * 100:.0f}%). Must check \u226595% of declared symbols."
                ),
            }
    return None


def foundry_mark_stream(
    stream: str,
    cycle: int,
    items_checked: int = 0,
    items_total: int = 0,
    findings_count: int = 0,
    project_root: str = ".",
) -> dict:
    """Record a verification stream's coverage for this cycle.

    Partial records are ACCEPTED and stored (CT-003 / AC-020). The >=95%
    coverage thresholds are no longer enforced here \u2014 enforcing them at record
    time discarded the tranche entirely, so a PROVE run split across two calls
    lost its first half. They are evaluated once per cycle in
    ``_check_streams_complete`` against the cycle TOTAL instead.
    """
    if stream not in STREAM_WIRE_IDS:
        return {"error": f"Invalid stream: {stream}. Must be one of: {', '.join(sorted(STREAM_WIRE_IDS))}"}

    fdir = get_run_dir(project_root)
    if not fdir or not fdir.exists():
        return {"error": "No active foundry run"}
    if (corrupt := _artifact_guard(fdir)):
        return corrupt

    if items_checked <= 0:
        return {
            "error": f"Cannot mark {stream} complete with items_checked={items_checked}. "
                     "You must report how many items were actually checked. "
                     "trace: symbols checked. prove: requirements checked. "
                     "sight: pages/elements exercised. test: tests run. "
                     "probe: endpoints hit. research_audit: recommendations audited. "
                     "flow_trace: flow-delta packets verified. "
                     "coverage_diff: coverage_list source items diffed. "
                     "test01: derived spec-test hypotheses executed.",
            "hint": "If the stream genuinely checked 0 items, the scope may be wrong.",
        }

    # D-100: the counts accumulate by ADDITION across a cycle's tranches, so a
    # negative value does not record a tranche — it ERASES earlier ones. The
    # guard above refused items_checked <= 0 while findings_count had no lower
    # bound at all, and that asymmetry was load-bearing:
    # mark_stream("prove", 2, findings=9) then mark_stream("prove", 1,
    # findings=-9) cancels the cycle's findings to 0 and flips _prove_is_clean
    # False -> True. On TRACE the same cancellation stamps .trace-clean-at —
    # the anchor that lets a LATER cycle skip the TRACE stream outright.
    if findings_count < 0:
        return {
            "error": (
                f"Cannot record {stream} with findings_count={findings_count}. "
                "A cycle's findings accumulate across tranches, so a negative "
                "count would erase findings an earlier record of this cycle "
                "already reported."
            ),
            "hint": "Report the findings THIS tranche produced — zero or more, never negative.",
        }

    if items_total < 0:
        return {
            "error": (
                f"Cannot record {stream} with items_total={items_total}. "
                "The population a tranche was drawn from cannot be negative."
            ),
            "hint": "Report the size of the population, or 0 when this stream has no fixed denominator.",
        }

    # The near-miss beside the same guard: 1667% coverage was accepted in
    # silence and trivially satisfied the >=95% gate. Judged PER RECORD, where
    # "checked more than exist" is unambiguous — the cycle TOTAL is deliberately
    # not judged here, because a legitimate re-record of a cycle would trip it
    # and CT-003 requires tranches be stored, not refused.
    if items_total > 0 and items_checked > items_total:
        return {
            "error": (
                f"Cannot record {stream} with items_checked={items_checked} against "
                f"items_total={items_total}: a tranche cannot check more items than "
                "the population it declares."
            ),
            "hint": "Either items_checked is overstated or items_total understates the population.",
        }

    # The roll-up is keyed by the SERVER counter, never by the caller's `cycle`
    # (FR-005). The caller's value is kept on the record for audit only.
    server_cycle = _current_cycle(fdir)
    prev_totals = _rollup_totals(fdir, server_cycle - 1, stream) if server_cycle > 0 else None
    totals = _record_stream_rollup(
        fdir, server_cycle, stream, items_checked, items_total, findings_count, cycle
    )

    # Drop warning: cycle N's TOTAL against cycle N-1's TOTAL (CT-003). The
    # old comparison read the previous write of this same marker file, which
    # made a second partial tranche in the SAME cycle look like a collapse.
    #
    # D-139 / FR-012 / AC-018 — AND IT NEVER FIRES AGAINST A WIDTH THE SERVER
    # ITSELF DREW.
    # ----------------------------------------------------------------------
    # This rung consulted no recorded decision at all, so on every DELTA cycle
    # that followed a FULL one it accused a stream of rushing for running
    # EXACTLY the roster the server handed it. Driven: cycle 1 prove recorded
    # 172/172 at FULL; cycle 2's `inspect_start` recorded a DELTA roster of 13
    # rows; `Foundry-Stream(prove, cycle 2, items_checked=13, items_total=13)`
    # returned ok with `coverage_shortfall` null — the rung that DOES read the
    # roster was satisfied — beside the warning "Coverage dropped: prove
    # checked 13 items in cycle 2 vs 172 in cycle 1. Are you rushing?". FR-012
    # fixes the DELTA PROVE width as the fixed-defect rows plus ten sampled
    # rows, so 13 of 13 is complete delivery of the declared width, and the
    # same false accusation fired for TRACE on every DELTA cycle after a FULL
    # one.
    #
    # The recorded per-stream SCOPE is what decides. `_decide_inspect_mode`
    # writes `stream_scope[wire]["scope"]` as "full", "delta" or "skipped" at
    # the transition that opened this INSPECT, and a stream recorded "delta"
    # is one whose population the server deliberately narrowed — there is
    # nothing to compare against last cycle's, because they are not the same
    # denominator. `_coverage_shortfall` already measures a DELTA stream
    # against its own roster (D-080), so the width is not going unchecked; it
    # is being checked by the rung that knows what the width IS.
    #
    # A stream recorded "full" on a DELTA cycle — TEST, which AC-019 keeps full
    # and cold — still gets the drop rung, because its denominator did not
    # move. So does every stream on a FULL cycle, and every stream on a run
    # with no recorded decision.
    coverage_warning = ""
    recorded_scope = _recorded_stream_scope(fdir, server_cycle, stream)
    narrowed_by_the_server = recorded_scope == "delta"
    if (
        not narrowed_by_the_server
        and prev_totals is not None
        and prev_totals["items_checked"] > 0
    ):
        if totals["items_checked"] < prev_totals["items_checked"] * 0.7:
            coverage_warning = (
                f"Coverage dropped: {stream} checked {totals['items_checked']} items in "
                f"cycle {server_cycle} vs {prev_totals['items_checked']} in cycle "
                f"{server_cycle - 1}. Are you rushing?"
            )

    coverage_pct = (
        f"{totals['items_checked'] / totals['items_total'] * 100:.0f}%"
        if totals["items_total"] > 0
        else "N/A"
    )

    marker = fdir / _stream_marker(stream)
    marker.write_text(
        f"{_now()} cycle={server_cycle}\n"
        f"items_checked={totals['items_checked']}\n"
        f"items_total={totals['items_total']}\n"
        f"coverage={coverage_pct}\n"
        f"findings={totals['findings']}\n",
        encoding="utf-8",
    )

    # TRACE skip-gate anchor: when TRACE passes with zero findings, stamp the
    # current HEAD SHA. Future F2 entries can compare HEAD vs this SHA
    # restricted to manifest key_files — if no overlap, skip TRACE.
    # Deterministic, verbatim the same as re-running LSP: topology unchanged.
    if stream == "trace" and totals["findings"] == 0:
        import subprocess
        try:
            rev = subprocess.run(
                ["git", "-C", project_root, "rev-parse", "HEAD"],
                capture_output=True, text=True, timeout=5,
            )
            if rev.returncode == 0 and rev.stdout.strip():
                import json as _json
                (fdir / TRACE_CLEAN_AT_MARKER).write_text(
                    _json.dumps({
                        "head_sha": rev.stdout.strip(),
                        "stamped_at": _now(),
                        "cycle": server_cycle,
                        "items_checked": totals["items_checked"],
                    }),
                    encoding="utf-8",
                )
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            pass

    result: dict = {
        "ok": True,
        "stream": stream,
        "cycle": server_cycle,
        "declared_cycle": cycle,
        "items_checked": totals["items_checked"],
        "items_total": totals["items_total"],
        "coverage": coverage_pct,
        "findings": totals["findings"],
        "records_this_cycle": totals["records"],
        "recorded": {
            "items_checked": items_checked,
            "items_total": items_total,
            "findings": findings_count,
        },
    }

    # A shortfall is REPORTED here so the lead sees it immediately, but it does
    # not refuse the record — the threshold is enforced once per cycle at the
    # streams-complete check (CT-003), where a later tranche can still clear it.
    shortfall = _coverage_shortfall(fdir, project_root, stream, server_cycle)
    warnings = [w for w in (coverage_warning, shortfall["reason"] if shortfall else "") if w]
    if shortfall:
        result["coverage_shortfall"] = shortfall
    if warnings:
        result["warning"] = " | ".join(warnings)

    return result


def _trace_skip_check(fdir: Path, project_root: str) -> dict:
    """Decide whether the current F2 INSPECT can skip the TRACE stream.

    Rationale: TRACE is LSP-heavy (EXISTS / SUBSTANTIVE / WIRED / PLACED
    across every manifest symbol). A cycle of TRACE routinely runs 100+
    Serena IPC calls over several minutes. Topology is a pure function of
    the code on disk — if no file owning a manifest symbol has changed
    since the last clean TRACE, the verdicts are provably identical.

    Returns {skip: bool, reason: str, details?: {...}}.
    """
    marker = fdir / TRACE_CLEAN_AT_MARKER
    if not marker.exists():
        return {"skip": False, "reason": "no prior clean TRACE to compare against"}
    marker_data, marker_problem = read_document(marker)
    if marker_problem is not None:
        return {"skip": False, "reason": "unreadable .trace-clean-at marker"}
    clean_sha = marker_data.get("head_sha", "")
    if not clean_sha:
        return {"skip": False, "reason": "no head_sha recorded"}

    from foundry_mcp.tools.foundry_spawn import _manifest_shape_problem

    manifest = _load_json(fdir / "castings" / "manifest.json")
    # D-134: same shared validator as every other reader of this document, so
    # "unusable" means one thing across the package.
    if _manifest_shape_problem(manifest) is not None:
        return {"skip": False, "reason": "castings/manifest.json records are unreadable"}
    key_files: set[str] = set()
    for c in manifest.get("castings", []):
        for f in (c.get("key_files") or []):
            if isinstance(f, str) and f.strip():
                key_files.add(f.strip())
    if not key_files:
        return {"skip": False, "reason": "no key_files declared in manifest — cannot scope diff"}

    import subprocess
    try:
        result = subprocess.run(
            ["git", "-C", project_root, "diff", "--name-only", clean_sha, "HEAD"],
            capture_output=True, text=True, timeout=10,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return {"skip": False, "reason": "git unavailable"}
    if result.returncode != 0:
        return {"skip": False, "reason": f"git diff failed: {result.stderr.strip()[:120]}"}

    changed_files = {line.strip() for line in result.stdout.splitlines() if line.strip()}
    overlap = changed_files & key_files
    if overlap:
        return {
            "skip": False,
            "reason": f"{len(overlap)} manifest key_file(s) changed since {clean_sha[:8]}",
            "details": {"changed_keyfiles": sorted(overlap)[:10]},
        }
    return {
        "skip": True,
        "reason": f"no manifest key_files changed since clean TRACE at {clean_sha[:8]}",
        "details": {
            "clean_sha": clean_sha,
            "total_changed": len(changed_files),
            "manifest_key_files": len(key_files),
        },
    }


def _maybe_skip_trace(fdir: Path, project_root: str) -> dict | None:
    """If the TRACE skip gate fires, auto-stamp .trace-complete as skipped.

    Called from foundry_next_action so the decision is made deterministically
    before any stream dispatching instructions go out. No-op when TRACE is
    already complete or skip preconditions aren't met.

    D-071 — THE RECORDED WIDTH FENCES THE SKIP.
    -------------------------------------------
    This skip predates the FULL/DELTA rule and referenced it not at all, so it
    satisfied a FULL roster's trace requirement without TRACE running — at the
    INSPECT before ASSAY included. Driven on a cycle recorded FULL/final_gate
    whose GRIND touched a non-key_file: Foundry-Next auto-stamped
    `.trace-complete` and `_check_streams_complete` returned complete True,
    missing "", with TRACE never run. AC-017 and FR-012 sanction exactly two
    exceptions to the FULL roster — a stream in `manifest.stream_skips`, or a
    `research_skipped` record — and this is neither; GI-007 forbids any casting
    from disabling or downweighting a stream; US-004's premise is that "every
    final gate still runs everything at full width".

    So the new rule fences it rather than removing it. At FULL the skip never
    fires. At DELTA it fires only when the GRIND diff is EMPTY — TRACE's DELTA
    scope is "the symbols the GRIND commits touched" (AC-019), and when nothing
    was touched there are no symbols to walk, which is the one case where
    skipping and running are the same answer. A run with NO recorded decision
    keeps the pre-change `_trace_skip_check` behaviour, which is what a resumed
    archive should get.
    """
    if not fdir or not fdir.exists():
        return None
    if (fdir / _stream_marker("trace")).exists():
        return None
    state = _load_json(fdir / "state.json")
    if state.get("phase") != "F2":
        return None

    recorded = _current_inspect_mode(fdir)
    mode = (recorded or {}).get("mode", "")
    if mode == "FULL":
        return {
            "skip": False,
            "reason": (
                "this INSPECT is recorded FULL (rule "
                f"{(recorded or {}).get('rule', '?')}) — the full roster runs, "
                "and TRACE is in it"
            ),
        }
    if mode == "DELTA":
        touched = [
            f for f in (recorded or {}).get("touched_files") or []
            if isinstance(f, str) and f.strip()
        ]
        if touched:
            return {
                "skip": False,
                "reason": (
                    f"DELTA width over {len(touched)} touched file(s) — TRACE "
                    "runs over the symbols the GRIND commits touched"
                ),
                "details": {"touched_files": sorted(touched)[:10]},
            }
        decision = {
            "skip": True,
            "reason": (
                "DELTA width and the GRIND diff is empty — there are no touched "
                "symbols for TRACE to walk"
            ),
            "details": {"inspect_mode": "DELTA", "touched_files": []},
        }
        (fdir / _stream_marker("trace")).write_text(
            f"{_now()} cycle=skipped\n"
            f"items_checked=0\n"
            f"items_total=0\n"
            f"coverage=SKIPPED\n"
            f"findings=0\n"
            f"skipped=true\n"
            f"reason={decision['reason']}\n",
            encoding="utf-8",
        )
        return decision

    # D-117 — AN UNRECORDED WIDTH NO LONGER FALLS THROUGH TO THE LEGACY RULE.
    #
    # This arm read "A run with NO recorded decision keeps the pre-change
    # `_trace_skip_check` behaviour, which is what a resumed archive should
    # get", and `_trace_skip_check` skips on a `.trace-clean-at` marker plus an
    # untouched key_file set — so a resumed legacy archive auto-stamped
    # `.trace-complete` and TRACE never ran, reaching D-071's own defect through
    # the width the fence forgot to name. FULL and DELTA are the only two
    # answers that license a decision about TRACE's scope; there is no third.
    #
    # `_trace_skip_check` keeps its definition: it is the pre-width rule this
    # fence replaced, it is directly tested, and `tests/test_spawn_progress.py`
    # names it in the D-134 manifest-reader roster that scan asserts it still
    # sees.
    if (unrecorded := _unrecorded_width_problem(fdir)) is not None:
        return {"skip": False, "reason": unrecorded["reason"], "hint": unrecorded["hint"]}

    decision = _trace_skip_check(fdir, project_root)
    if not decision.get("skip"):
        return decision

    (fdir / _stream_marker("trace")).write_text(
        f"{_now()} cycle=skipped\n"
        f"items_checked=0\n"
        f"items_total=0\n"
        f"coverage=SKIPPED\n"
        f"findings=0\n"
        f"skipped=true\n"
        f"reason={decision['reason']}\n",
        encoding="utf-8",
    )
    return decision


# --------------------------------------------------------------------------- #
# INSPECT WIDTH (C-12 / ST-006 / ST-007 / GI-008 / GI-009 / FR-011 / FR-012)
#
# WHERE THE DECISION LIVES, AND WHY IT IS NOT NEGOTIABLE
# -----------------------------------------------------
# GI-009: "whichever Foundry-Phase transition opens an INSPECT records the
# mode; Foundry-Next only reports. No decision ever lives in Foundry-Next."
# GI-008 says the same thing from the other side: "A FULL versus DELTA decision
# computed inside Foundry-Next" is the named violation.
#
# So every function below COMPUTES and nothing below WRITES except through the
# transition that called it. `_check_streams_complete` and
# `_compute_next_action` read `state.json.inspect_modes[-1]` and re-derive
# nothing — a roster recomputed at display time is a roster that can disagree
# with the one the cycle actually ran, and a streams-complete check reading a
# roster nothing recorded is the failure ST-007 describes.
#
# The whole point is cost. thunder-viper ran 22 GRIND cycles at full INSPECT
# width, so a three-file GRIND was followed by a re-verification of everything.
# DELTA makes the second and later INSPECTs of a phase proportional to what
# changed, while every FULL rule keeps the gates that matter at full width.
# --------------------------------------------------------------------------- #

#: ``INSPECT_BOUNDARY_SHA_MARKER`` — the HEAD recorded at each INSPECT boundary,
#: so the next crossing knows what "since the last boundary" means — is declared
#: with the other run markers beside ``_RUN_MARKER_NAMES``, because D-201's
#: guard has to know the whole set BEFORE any of them is written and a set built
#: from names declared further down the file cannot be evaluated up there.


def _head_sha(project_root: str) -> str:
    """HEAD of the shared tree, or "" when git cannot answer. Never raises."""
    import subprocess

    try:
        result = subprocess.run(
            ["git", "-C", project_root, "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=10,
        )
    except (FileNotFoundError, OSError, subprocess.SubprocessError):
        return ""
    return result.stdout.strip() if result.returncode == 0 else ""


def _boundary_base_sha(fdir: Path) -> tuple[str, str]:
    """The commit a GRIND diff is measured from, and where it came from.

    Three sources in descending precedence, all of them commits this run itself
    recorded:

      1. the previous INSPECT boundary — the exact answer to "since the last
         sweep" (CT-007);
      2. the last clean TRACE, which `_trace_skip_check` already keeps;
      3. the CAST baseline, written when the run left F1.

    Returns ``("", "")`` when none exists, which is a run whose first INSPECT
    has not happened — and that INSPECT is FULL by `first_of_phase` anyway.
    """
    marker = fdir / INSPECT_BOUNDARY_SHA_MARKER
    if marker.exists():
        sha = _read_text(marker).strip()
        if sha:
            return sha, INSPECT_BOUNDARY_SHA_MARKER
    trace_marker = fdir / TRACE_CLEAN_AT_MARKER
    if trace_marker.exists():
        data, problem = read_document(trace_marker)
        if problem is None and data.get("head_sha"):
            return str(data["head_sha"]), TRACE_CLEAN_AT_MARKER
    cast_marker = fdir / CAST_BASELINE_SHA_MARKER
    if cast_marker.exists():
        sha = _read_text(cast_marker).strip()
        if sha:
            return sha, CAST_BASELINE_SHA_MARKER
    return "", ""


def _grind_diff(fdir: Path, project_root: str) -> dict:
    """Repo-relative paths the GRIND touched since the last boundary.

    Returns ``{"files": [...], "base": sha, "source": marker, "problem": str}``.
    A non-empty ``problem`` means the diff is UNKNOWN — not empty. The two are
    opposite answers and the callers must not confuse them: an unknown diff
    cannot support a delta roster, so it forces FULL and a whole-corpus sweep.
    """
    import subprocess

    base, source = _boundary_base_sha(fdir)
    if not base:
        return {"files": [], "base": "", "source": "",
                "problem": "no recorded boundary, clean TRACE or CAST baseline to diff from"}
    try:
        result = subprocess.run(
            ["git", "-C", project_root, "diff", "--name-only", base, "HEAD"],
            capture_output=True, text=True, timeout=30,
        )
    except (FileNotFoundError, OSError, subprocess.SubprocessError) as exc:
        return {"files": [], "base": base, "source": source,
                "problem": f"git unavailable: {type(exc).__name__}"}
    if result.returncode != 0:
        return {"files": [], "base": base, "source": source,
                "problem": f"git diff failed: {result.stderr.strip()[:120]}"}
    files = sorted({line.strip() for line in result.stdout.splitlines() if line.strip()})
    return {"files": files, "base": base, "source": source, "problem": ""}


def _repo_relative(project_root: str, path: Path) -> str:
    """`path` spelled relative to `project_root`, or unchanged when it is not under it."""
    try:
        return str(path.resolve().relative_to(Path(project_root).resolve()))
    except (ValueError, OSError):
        return str(path)


def _spec_relative_paths(project_root: str) -> list[str]:
    """EVERY repo-relative spelling of this run's spec, for `is_verifier_path`.

    The spec is NOT a member of VERIFIER_PATH_PATTERNS on purpose — a run's spec
    lives wherever `state.json.spec_path` says, and a static pattern would
    either miss it or sweep in every unrelated spec.md in the tree. So it is
    passed at call time, which means resolving it here. FR-032's "one constant
    rather than typed in several places" is why this is the ONLY resolver: a
    singular `_spec_relative_path` was written first, superseded by this one
    when D-102 landed, and left in the file with a docstring presenting it as a
    live sibling — so the next reader had to derive from call-site absence that
    it was dead (D-196). It is gone;
    `test_every_private_function_the_plugin_ships_is_reachable` is what keeps
    the next superseded helper from being left behind the same way — over every
    file the plugin ships, since D-199 found the class living in a script and
    in the test corpus while the pin's subject set was this module alone.

    D-102 — THE SPEC HAS TWO LEGAL SPELLINGS AND THE FULL RULE SAW ONE.
    ------------------------------------------------------------------
    FR-011 is Locked and verbatim: "FULL when: … or the GRIND diff touches
    vocab.py, schemas/, gate/orchestrator code, agent/skill prose, or the spec."
    C-1 says the spec "is matched by `spec_path` (the run's
    `state.json.spec_path` AND `foundry-archive/{run}/spec.md`), passed at call
    time" — two paths, and `is_verifier_path` takes one.

    `_resolve_spec_path` PREFERS `<run_dir>/spec.md` and only falls back to
    `state.json.spec_path`, so on the ordinary run — which has both, because
    `foundry_init` copies the spec into the archive — the singular resolver
    returns the archive copy and the authored spec at `state.json.spec_path` was
    invisible to the rule. Driven at three spec locations including
    `forge-specs/subject/spec.md`: a GRIND diff whose ONLY touched file was the
    path `state.json` records as `spec_path` opened the next INSPECT at DELTA,
    rule `delta`, while every other FULL trigger (vocab.py, schemas/findings.py,
    the orchestrator module, agents/assayer.md) recorded FULL/`verifier_touched`
    correctly. The same record showed `test01` treating that spec as "a covered
    file was touched", so the two consumers of one touched-files list disagreed
    about whether the spec had moved.

    Both spellings are returned, deduplicated and in a stable order, and the
    caller asks `is_verifier_path` about each. Widening the vocab helper to take
    a sequence was the alternative and is not ours to make: `vocab.py` is
    casting 1's file and its signature is LOCKED by C-1.
    """
    spellings: list[str] = []
    fdir = get_run_dir(project_root)
    if fdir:
        archived = fdir / "spec.md"
        if archived.exists():
            spellings.append(_repo_relative(project_root, archived))
        declared = _load_json(fdir / "state.json").get("spec_path", "")
        if isinstance(declared, str) and declared.strip():
            # Recorded as a repo-relative path already; normalised through the
            # same resolver anyway so an absolute `spec_path` compares equal to
            # the relative diff entries `git diff --name-only` produces.
            spellings.append(
                _repo_relative(project_root, Path(project_root) / declared.strip())
            )
    resolved = _resolve_spec_path(project_root)
    if resolved is not None:
        spellings.append(_repo_relative(project_root, resolved))
    seen: list[str] = []
    for spelling in spellings:
        if spelling and spelling not in seen:
            seen.append(spelling)
    return seen


def _research_skipped(fdir: Path) -> bool:
    """Does this run carry a record that RESEARCH was skipped (AC-017)?

    Read from three places because no single one owns it: `castings/manifest.json`
    and `state.json` are where `foundry_init` writes run-level flags, and a
    `.research-skipped` marker is the shape every other per-run fact in this
    directory takes. Any of them saying so is enough — a run that recorded the
    skip anywhere recorded it.
    """
    if (fdir / RESEARCH_SKIPPED_MARKER).exists():
        return True
    for document in ("state.json", "castings/manifest.json"):
        if bool(_load_json(fdir / document).get("research_skipped")):
            return True
    return False


def _skipped_streams(fdir: Path) -> set[str]:
    """`manifest.stream_skips` as wire ids — casting 4's reader, lazily."""
    from foundry_mcp.tools.foundry_spawn import _skipped_stream_ids

    return _skipped_stream_ids(fdir)


def _research_scope_touched(
    fdir: Path, project_root: str, touched: list[str]
) -> dict:
    """Did the GRIND diff touch a file RESEARCH_AUDIT covers (ST-007)?

    A casting declaring a `research_context` is a casting whose code was written
    against research recommendations, so its key_files are the files an audit of
    those recommendations reads. A diff touching none of them cannot have
    deviated from research that no longer applies to anything that moved.

    Returns the shared conditional answer — `{"touched", "computable",
    "source", "detail"}` — because it is one arm of `DELTA_CONDITIONAL_STREAMS`
    and every arm answers the same three-valued question through
    `_delta_conditional_scope`. `source` names WHICH source answered:
    `no diff`, `manifest`, or `unknown`. The detail names WHICH casting and
    WHICH file matched on a hit, and WHY the set was not computable on an
    unknown; it is empty on a COMPUTED miss, because the skip's provenance is
    the caller's own sentence and a second, unread one is a second thing that
    can drift.

    ``project_root`` is not read here. It is in the signature because every
    conditional arm is called through ONE signature by the shared path, and an
    arm whose shape the shared path cannot call is an arm that gets called
    some other way — which is the whole of D-208.

    D-208 — A MANIFEST THIS SERVER NEVER READ IS NOT A MANIFEST DECLARING NO
    RESEARCH.
    -----------------------------------------------------------------------
    This returned a two-valued `{touched, detail}` with no third value, and
    took its covered set from `_load_json(castings/manifest.json)`, which
    yields `{}` for an ABSENT document — so `manifest.get("castings", [])` was
    empty and the arm returned `touched: False`, INDISTINGUISHABLE from a
    manifest that was read and declares no `research_context`.

    Driven at the wire at 916c1ca: a non-self-targeting run, manifest declaring
    casting 1 with `research_context` and key_files ["src/api/users.py"], a
    control cycle touching that key_file recorded `research_audit`
    {scope full, detail "casting 1 declares research_context and the diff
    touched its key_file src/api/users.py"}. With `castings/manifest.json`
    DELETED and the identical diff, `Foundry-Phase('inspect_start')` returned
    ok and recorded `research_audit` {scope skipped, detail "no file
    research_audit covers was touched"} — a NEGATIVE about a set it had not
    read — while `test01`, in the SAME `stream_scope`, recorded "could not be
    computed". Two arms of one decision, one input, opposite failure
    directions. The artifact guard does not close it: an invalid-JSON or
    wrong-shape manifest IS named and refuses at the entry point, so the ABSENT
    manifest is the one uncomputable shape that reaches this arm.

    The line is drawn at whether the document was READ, per the lead ruling:
    absent (or unreadable, or with a `castings` cell of the wrong type) is
    UNKNOWN and therefore required; read, with no casting declaring a
    `research_context`, is a computed miss and therefore skipped. The cycle-20
    docstring in `test_the_research_audit_arm_is_unmoved_by_the_test01_source_
    ladder` asserted this set was "computable on every run"; that premise is
    what this drive falsified, and it is corrected there.

    THE TWO AXES ARE UNCHANGED AND WERE ALWAYS RIGHT, which is why D-204 named
    only the sibling. WHICH names: the manifest's own `key_files` cells, a
    declared list, never prose. HOW they map to files: they ARE files, compared
    by whole-string equality against the diff, so there is no mapping step to
    get wrong. What moved is a third axis the sibling already had — HOW MANY
    ANSWERS the question has.
    """
    if not touched:
        return {
            "touched": False,
            "computable": True,
            "source": "no diff",
            "detail": "",
        }

    manifest_path = fdir / "castings" / "manifest.json"
    if not manifest_path.exists():
        # `_load_json` cannot tell this from an empty document BY DESIGN (read
        # its docstring), so the absence is tested here rather than inferred
        # from a `{}` that means four different things.
        return _covered_set_unknown(
            "research_audit",
            "the run has no castings/manifest.json, so which castings declare "
            "a research_context is unknown",
        )
    problem = _document_problem(manifest_path)
    if problem is not None:
        # `_artifact_guard` names this at the MCP entry point and refuses, so
        # in practice it does not reach here. It is answered anyway: a reader
        # that would return a negative for a document it could not read is the
        # defect, whether or not some caller happens to shield it.
        return _covered_set_unknown("research_audit", problem)

    manifest = _load_json(manifest_path)
    castings = manifest.get("castings")
    if castings is None:
        castings = []
    if not isinstance(castings, list):
        return _covered_set_unknown(
            "research_audit",
            f"castings/manifest.json's castings cell is a "
            f"{type(castings).__name__}, not a list of castings",
        )

    touched_set = set(touched)
    for casting in castings:
        if not isinstance(casting, dict) or not casting.get("research_context"):
            continue
        for f in casting.get("key_files") or []:
            if isinstance(f, str) and f.strip() in touched_set:
                return {
                    "touched": True,
                    "computable": True,
                    "source": "manifest",
                    "detail": (
                        f"casting {casting.get('id', '?')} declares "
                        f"research_context and the diff touched its key_file "
                        f"{f.strip()}"
                    ),
                }
    # READ, and it declares no covered file the diff touched. A computed miss.
    return {
        "touched": False,
        "computable": True,
        "source": "manifest",
        "detail": "",
    }


def _contracts_surface_cells(project_root: str) -> tuple[list[tuple[str, str]], str | None]:
    """The spec's Contracts table as `(row id, surface cell)` pairs, and why not.

    Returns `(rows, problem)`. `problem` is None when the spec was READ — even
    if it holds no Contracts table at all — and a named reason when it could
    not be, so the caller can tell "this spec declares no surfaces" from "this
    server cannot see what it declares".

    The table is read as CELLS, not as text. Rows are the pipe-delimited lines
    of the `## Contracts` section, the surface column is located by its header
    name rather than by index (a table that gains a column ahead of `surface`
    must not silently start returning `input`), and the alignment row is
    skipped.

    D-207 — `[]` USED TO MEAN BOTH THINGS, AND THE CALLER READ IT AS THE
    HARMLESS ONE.
    ---------------------------------------------------------------------
    The docstring said it outright: "Returns [] when the spec cannot be read or
    has no such table — an unreadable spec names no surfaces, so nothing is
    covered by it." A spec with no table genuinely covers nothing and TEST-01
    SKIPs on it with a reason; a spec that could not be read covers an UNKNOWN
    set, and the two arrived at `_test01_scope_touched` as the same empty list,
    where the second was asserted as a negative. A covered set the server cannot
    compute is not an empty covered set — the same sentence that governs the
    arm below, applied to the document this one reads.
    """
    spec_path = _resolve_spec_path(project_root)
    if spec_path is None:
        return [], "the run records no readable spec path"
    if not spec_path.exists():
        return [], f"the run's spec {spec_path.name} does not exist"
    text, problem = read_text_file(spec_path)
    if problem is not None:
        return [], problem

    lines = text.splitlines()
    lowered = [line.strip().lower() for line in lines]
    start = next(
        (i for i, line in enumerate(lowered) if line.startswith("## contracts")),
        None,
    )
    if start is None:
        # READ, and it declares no surfaces. That is an answer, not a failure.
        return [], None
    end = next(
        (i for i in range(start + 1, len(lines)) if lowered[i].startswith("## ")),
        len(lines),
    )

    rows: list[tuple[str, str]] = []
    column: int | None = None
    for i in range(start + 1, end):
        line = lines[i].strip()
        if not line.startswith("|"):
            # A blank line or prose ends the table; a later table in the same
            # section starts its own header search.
            column = None
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if column is None:
            header = [c.lower() for c in cells]
            if "surface" in header:
                column = header.index("surface")
            continue
        if set(line.replace("|", "").strip()) <= set("-: "):
            continue  # the alignment row
        if column < len(cells):
            rows.append((cells[0], cells[column]))
    return rows, None


def _registry_tool_modules() -> dict[str, list[str]]:
    """Tool name -> the plugin modules that implement it, from the REGISTRY.

    `server.py`'s `_DISPATCH` IS the tool-name-to-handler binding, so it is the
    only thing that knows which module answers `Foundry-Report`. The mapping is
    read out of the dispatch entry's own code object rather than out of prose:
    a name it loads from the server's globals that resolves to a function of a
    `foundry_mcp` module contributes that module, and a dotted `foundry_mcp.*`
    name it names contributes that module directly — which is how a lazy
    in-function import is followed. Entries that dispatch through a helper
    defined in `server.py` itself are walked one more hop, because that helper
    is where the real handler is named: `"Foundry-Report": lambda args:
    _dispatch_report()` reaches `generate_report` only inside
    `_dispatch_report`, and stopping at the lambda would map CT-014 to
    `server.py` and leave `tools/foundry_report.py` uncovered.

    Returns `{}` when the server module cannot be imported. Nothing is claimed
    as covered in that case, which is the honest answer: without the registry
    there is no evidence of what implements what, and the alternative — guessing
    from names — is the D-204 defect itself.
    """
    from types import FunctionType

    try:
        from foundry_mcp import server as _server
    except Exception:  # pragma: no cover - registry unavailable
        return {}

    package_root = Path(__file__).resolve().parent.parent  # .../foundry_mcp

    def module_file(dotted: str) -> str | None:
        if dotted == "foundry_mcp":
            candidate = package_root / "__init__.py"
            return str(candidate) if candidate.exists() else None
        if not dotted.startswith("foundry_mcp."):
            return None
        rel = dotted[len("foundry_mcp.") :].replace(".", "/")
        for candidate in (package_root / f"{rel}.py", package_root / rel / "__init__.py"):
            if candidate.exists():
                return str(candidate)
        return None

    def walk(code, found: set[str], depth: int, seen: set[str]) -> None:
        if depth > 3:
            return
        for name in code.co_names:
            if name.startswith("foundry_mcp"):
                found.add(name)
                continue
            obj = _server.__dict__.get(name)
            if not isinstance(obj, FunctionType):
                continue
            module = getattr(obj, "__module__", "") or ""
            if not module.startswith("foundry_mcp"):
                continue
            if module == "foundry_mcp.server":
                if name not in seen:
                    seen.add(name)
                    walk(obj.__code__, found, depth + 1, seen)
            else:
                found.add(module)

    registry: dict[str, list[str]] = {}
    dispatch = getattr(_server, "_DISPATCH", None)
    if not isinstance(dispatch, dict):
        return {}
    for tool, handler in dispatch.items():
        code = getattr(handler, "__code__", None)
        if code is None:
            continue
        modules: set[str] = set()
        walk(code, modules, 0, set())
        files = {f for m in modules if (f := module_file(m)) is not None}
        if files:
            registry[str(tool)] = sorted(files)
    return registry


def _path_matches(covered: str, candidate: str) -> bool:
    """Do two path spellings name the same file, one possibly abbreviated?

    Equality, or one being a SEGMENT-ANCHORED suffix of the other. Both
    directions are needed because the two sides are spelled by different
    authorities: `git diff --name-only` yields repo-relative paths, and the
    registry yields the executing package's absolute paths, which are only
    repo-relative on a self-targeting run. The anchor on `/` is what keeps this
    from being the substring search D-204 was filed on — `src/a.py` matches no
    covered path, where an unanchored `in` made `a` match anything.
    """
    left = covered.replace("\\", "/").lstrip("./")
    right = candidate.replace("\\", "/").lstrip("./")
    if not left or not right:
        return False
    return left == right or left.endswith(f"/{right}") or right.endswith(f"/{left}")


#: THE ANSWER SHAPE every member of ``DELTA_CONDITIONAL_STREAMS`` returns.
#: Named once, here, because ``_delta_conditional_scope`` validates against
#: this tuple: an arm that answers with fewer keys has not given a narrower
#: answer, it has left the question unanswered, and D-208 is what reading the
#: first as the second costs.
_CONDITIONAL_ANSWER_KEYS = ("touched", "computable", "source", "detail")


def _covered_set_unknown(wire: str, reason: str) -> dict:
    """The THIRD answer, for ANY conditional stream: the covered set could not
    be computed, so require the stream.

    Mirrors `_decide_inspect_mode`'s uncomputable-diff arm one function over —
    "the GRIND diff could not be computed ..., so the verifier cannot be shown
    to be untouched" — because it is the same sentence about a different set,
    and the two must not disagree about which way an unknown fails.

    D-208 — ONE CONSTRUCTOR FOR EVERY ARM, NOT ONE PER ARM.
    ------------------------------------------------------
    This was `_test01_covered_set_unknown`, and the class it belongs to
    (`inspect-mode-rule-is-a-proxy-not-the-fact`) recurred for three straight
    cycles because each fix taught ONE arm of the shared DELTA branch a lesson
    its sibling never heard: D-204 anchored test01's WHICH and HOW, D-207 gave
    test01 the third value, and D-208 then found `_research_scope_touched`
    still answering a two-valued question about a manifest it had not read.
    The wire id is a parameter so the sentence is the SAME sentence whichever
    stream could not be computed — for `test01` it is byte-identical to the one
    D-207 shipped.
    """
    return {
        "touched": True,
        "computable": False,
        "source": "unknown",
        "detail": (
            f"the set of files {wire} covers could not be computed ({reason}), "
            f"so {wire} cannot be shown to be untouched"
        ),
    }


def _declared_test01_scope(fdir: Path) -> list[str]:
    """The paths the RUN ITSELF declares TEST-01 covers, in declared order.

    Read from `castings/manifest.json` — the document the decompose step writes
    and the one artifact that describes THIS run's target rather than the
    program the server happens to be executing. Both homes the lead ruling
    named are accepted, because either is the run "already recording" it: a
    top-level `test01_scope` list, and a `test01_scope` list on any casting row
    of the same manifest. Additive on both, so `migrate-archive.py` learns
    nothing and a pre-change archive simply declares none.
    """
    manifest = _load_json(fdir / "castings" / "manifest.json")
    declared: list[str] = []

    def _absorb(cells: object) -> None:
        if not isinstance(cells, list):
            return
        declared.extend(
            c.strip() for c in cells if isinstance(c, str) and c.strip()
        )

    _absorb(manifest.get("test01_scope"))
    for casting in manifest.get("castings", []) or []:
        if isinstance(casting, dict):
            _absorb(casting.get("test01_scope"))
    return declared


def _test01_scope_touched(fdir: Path, project_root: str, touched: list[str]) -> dict:
    """Did the GRIND diff touch a file TEST-01 covers (ST-007)?

    TEST-01 derives property tests from the spec's Contracts table and drives
    the surfaces that table names, so its scope is the modules that IMPLEMENT
    those surfaces, plus the schemas the surfaces validate against. Returns
    `{"touched": bool, "computable": bool, "source": str, "detail": str}`; on a
    match the detail names what matched, so the provenance recorded beside the
    roster can be checked rather than believed, and it is empty on a computed
    miss for the reason `_research_scope_touched` states.

    `source` names WHICH of the three sources answered — `declared`, `registry`,
    `schemas`, `no diff`, or `unknown` — so the recorded scope says where its
    answer came from and not merely what it was.

    D-207 — A COVERED SET THE SERVER CANNOT COMPUTE IS NOT AN EMPTY COVERED SET.
    ---------------------------------------------------------------------------
    D-204's fix moved this question onto the EXECUTING SERVER's own tool
    registry, which answers correctly on exactly one kind of run: one whose
    target IS this plugin. `_registry_tool_modules` yields foundry tool names
    bound to paths under the executing package's `src/foundry_mcp/`, while
    `_contracts_surface_cells` reads the TARGET run's spec. Off a self-target
    the two sides describe DIFFERENT PROGRAMS and can never intersect, so the
    only arm that could fire was the `schemas/` short-circuit — and a touched
    `schemas/` path already trips `verifier_touched` into FULL one function
    over, so in practice nothing could fire at all.

    Driven at the wire at 31cc192: a non-self-targeting run whose spec Contracts
    names `POST /api/users (create)` and `DELETE /api/users/:id`, whose casting
    key_file is `src/api/users.py`, and whose GRIND diff touched exactly that
    module recorded DELTA with `required_streams` ['trace','prove','test'] and
    `stream_scope.test01` = {scope: 'skipped', detail: 'no file test01 covers
    was touched'}. At cb77e83 the retired predicate returned True for that input
    and False for `src/api/other.py` and `src/zzz.py` — so the D-204 fix
    NARROWED a behaviour that was already correct on the non-self-target path
    while widening the self-target one. The same fail-open sat behind the
    registry's own `except ImportError -> {}`.

    THE ASYMMETRY IS THE DEFECT. The decision function one rung over fails
    CLOSED on an uncomputable GRIND diff — FULL, "the GRIND diff could not be
    computed" — and this one failed OPEN on an uncomputable covered set. ST-007,
    Locked FR-047 and AC-017 require EXACTLY the covered set; an unknown one is
    not the empty one.

    BOTH AXES, per the lead ruling. WHERE THE COVERED SET COMES FROM, in
    precedence order:

      1. `test01_scope` DECLARED by the run's own manifest, when present. The
         run's statement about its own target, true whatever the server is.
      2. otherwise the REGISTRY mapping — but only when the executing server can
         SHOW the target is this plugin, which is `state.json`'s `self_target`,
         the fact `foundry._self_target_preflight` computed and the run recorded
         at init. Recorded rather than recomputed: recomputing spawns git and
         reads plugin manifests on a per-boundary path, and it answers about NOW
         rather than about what this run was admitted on.
      3. otherwise UNKNOWN, and unknown is required, never skipped.

    WHAT AN UNKNOWN SET MEANS: `_covered_set_unknown` — required, scope
    `full`, and a detail that says the set was not computable and why, in the
    same words the uncomputable-diff arm uses. A missing key on a pre-change
    archive reads as absent and therefore as unknown, which is the fail-closed
    direction, so archive compatibility costs a stream and never a false skip.

    D-204 — A RULE ABOUT THE FACT IS NOT THE FACT, AND BOTH AXES WERE PROSE.
    ----------------------------------------------------------------------
    This asked whether the spec's Contracts SECTION — 6147 characters of prose,
    read whole — contained the touched file's basename or its stem, unanchored.
    Driven at the wire on a GRIND diff touching exactly one file, `src/a.py`:
    `Foundry-Phase('inspect_start')` returned DELTA with `test01` REQUIRED and
    the detail "a covered file was touched", because the stem `a` occurs in the
    section; `Foundry-Phase('inspect_clean')` then refused "streams incomplete:
    test01" for a stream no rule required. `src/fix.py`, `src/next.py`,
    `n/gate.py`, `lib/init.go`, `x/report.rb`, `webapp/spend.ts` and
    `tools/id.py` (the `| ID |` header) all read as covered the same way, while
    `tools/foundry_report.py` — which implements CT-014 — read as NOT covered,
    because the table spells the surface `Foundry-Report`. A predicate that
    answers yes for a file named nowhere and no for the file the row is about is
    not a narrow rule, it is a different question.

    BOTH AXES MOVE, because moving one is how this class returns. WHICH NAMES:
    the surface COLUMN's cells, parsed as table cells by
    `_contracts_surface_cells`, matched against the closed set of tool names the
    server registers — never the section's prose. HOW THEY MAP TO FILES: through
    `_registry_tool_modules`, the server's own `_DISPATCH` binding, never a stem
    search. Anchoring the cells alone would have left `src/fix.py` covered,
    since CT-004/005/006 all spell `Foundry-Fix` and `fix` is that file's stem.
    """
    if not touched:
        return {"touched": False, "computable": True, "source": "no diff", "detail": ""}
    candidates = [t for t in touched if t]

    # Source-independent and first: a `schemas/` path is inside TEST-01's scope
    # by construction, whoever the target is, because every Contracts surface
    # validates against the schemas. No registry and no declaration is consulted
    # to know that, so nothing about the ladder below can make it unknown.
    schema_hits = [t for t in candidates if "schemas/" in f"/{t}"]
    if schema_hits:
        return {
            "touched": True,
            "computable": True,
            "source": "schemas",
            "detail": (
                f"{schema_hits[0]} is a schemas/ file, which every Contracts "
                "surface validates against"
            ),
        }

    # (1) The run's own declaration wins outright when it exists — it describes
    # THIS run's target, so neither the executing server's identity nor its
    # registry is consulted, and a miss against it is a computed miss.
    declared = _declared_test01_scope(fdir)
    if declared:
        for covered in declared:
            for candidate in candidates:
                if _path_matches(covered, candidate):
                    return {
                        "touched": True,
                        "computable": True,
                        "source": "declared",
                        "detail": (
                            f"the run's manifest declares test01_scope "
                            f"{covered}, and the diff touched {candidate}"
                        ),
                    }
        return {"touched": False, "computable": True, "source": "declared", "detail": ""}

    # (2) The registry, and ONLY on a run this server can show it is the target
    # of. Off a self-target the registry describes a different program than the
    # spec does, so an empty intersection is evidence of nothing.
    if _load_json(fdir / "state.json").get("self_target") is not True:
        return _covered_set_unknown(
            "test01",
            "the run's manifest declares no test01_scope and state.json does "
            "not record self_target, so the executing server's tool registry "
            "describes a different program than the one under test"
        )

    rows, spec_problem = _contracts_surface_cells(project_root)
    if spec_problem is not None:
        return _covered_set_unknown(
            "test01",
            f"the run's Contracts table could not be read ({spec_problem})"
        )
    if not rows:
        # READ, and it names no surfaces. TEST-01 itself SKIPs with a reason on
        # such a spec, so this is a genuinely empty covered set — the one case
        # the D-207 sentence does NOT reach.
        return {"touched": False, "computable": True, "source": "registry", "detail": ""}

    registry = _registry_tool_modules()
    if not registry:
        return _covered_set_unknown(
            "test01",
            "the executing server's tool registry could not be read, so which "
            "module implements a named surface is unknown"
        )

    for row_id, surface in rows:
        for tool in sorted(registry):
            if not re.search(
                rf"(?<![A-Za-z0-9_-]){re.escape(tool)}(?![A-Za-z0-9_-])", surface
            ):
                continue
            for module_file in registry[tool]:
                for candidate in candidates:
                    if _path_matches(module_file, candidate):
                        return {
                            "touched": True,
                            "computable": True,
                            "source": "registry",
                            "detail": (
                                f"{row_id} names surface {tool}, which "
                                f"{_repo_relative(project_root, Path(module_file))} "
                                f"implements, and the diff touched {candidate}"
                            ),
                        }
    return {"touched": False, "computable": True, "source": "registry", "detail": ""}


#: WIRE ID -> the predicate that answers "did the diff touch a file this
#: stream covers". Every member of `DELTA_CONDITIONAL_STREAMS` (casting 1's
#: vocabulary) belongs here, under ONE signature — `(fdir, project_root,
#: touched)` — so `_delta_conditional_scope` can call any of them without
#: knowing which it is holding. `_research_scope_touched` does not read
#: `project_root`; taking it anyway is the price of there being one signature
#: rather than one call site per stream, and it is a price worth paying: an
#: `if wire == "research_audit" else` ladder in the caller is exactly where the
#: two arms drifted into different answer shapes for three cycles.
_DELTA_CONDITIONAL_PREDICATES = {
    "research_audit": _research_scope_touched,
    "test01": _test01_scope_touched,
}


def _delta_conditional_scope(
    fdir: Path, project_root: str, wire: str, touched: list[str]
) -> dict:
    """The ONE path every conditional stream's answer comes through (ST-007).

    Returns the shared three-valued answer — `{"touched", "computable",
    "source", "detail"}` — for any member of `DELTA_CONDITIONAL_STREAMS`, and
    routes every way of NOT having an answer to the same place:
    `_covered_set_unknown`, which is `touched: True` and therefore REQUIRED
    with a detail naming why. Never raises, so a malformed arm degrades the
    width to "run it" rather than taking the transition down.

    D-208 — THE STRUCTURAL FIX. THE CLASS WAS NEVER ABOUT ONE PREDICATE.
    -------------------------------------------------------------------
    `inspect-mode-rule-is-a-proxy-not-the-fact` recurred at cycles 3, 5, 18, 19
    and 20 and was escalated for three consecutive cycles. Each instance was a
    conditional arm answering a TWO-VALUED question — touched / not touched —
    about a covered set it had derived by proxy (D-204: a substring search of
    the spec's prose; D-207: the executing server's own tool registry, which
    describes a different program off a self-target) or could not derive at all
    (D-208: `castings/manifest.json` absent, read as a manifest declaring no
    research). Each fix repaired ONE arm. The lead's ruling names the root
    cause the three share: the answer SHAPE was per-arm, so a lesson taught to
    one arm reached no other, and the caller's `if wire == ... else ...` ladder
    let two arms of one branch disagree about how many answers the question
    has.

    BOTH AXES, and this function is the second of them:

      WHAT an arm answers  three values, never two. `touched` is the width
                           decision, `computable` says whether it was derived
                           or defaulted, `source` names which source derived
                           it, `detail` is the sentence recorded into
                           `state.json.inspect_modes` and `stream-rollup.json`.
      HOW it is shared     this function. Every arm is reached through the
                           `_DELTA_CONDITIONAL_PREDICATES` registry under one
                           signature, and its answer is CHECKED against
                           `_CONDITIONAL_ANSWER_KEYS` before the caller reads
                           `touched` out of it. So a fourth conditional stream
                           added to the vocabulary with no predicate, or with a
                           two-valued one, is REQUIRED with an honest detail —
                           it cannot be silently skipped on a negative nobody
                           computed, which is the only failure this class has
                           ever had.

    A stream registered here but absent from `DELTA_CONDITIONAL_STREAMS` is
    simply never asked; the vocabulary decides which streams are conditional,
    this registry decides how each is answered, and neither is derived from the
    other.
    """
    predicate = _DELTA_CONDITIONAL_PREDICATES.get(wire)
    if predicate is None:
        return _covered_set_unknown(
            wire,
            f"{wire} is a conditional stream with no registered predicate in "
            "this server, so what it covers is not something this server can "
            "state",
        )
    try:
        answer = predicate(fdir, project_root, touched)
    except Exception as exc:  # pragma: no cover - defensive; see the house rule
        # The house rule is that a tool never raises across the MCP boundary.
        # An arm that raises has not answered, and an unanswered question is
        # the unknown, not the negative.
        return _covered_set_unknown(
            wire, f"its predicate raised {type(exc).__name__}"
        )
    if not isinstance(answer, dict):
        return _covered_set_unknown(
            wire,
            f"its predicate answered with a {type(answer).__name__} rather "
            "than the shared conditional answer",
        )
    missing = [k for k in _CONDITIONAL_ANSWER_KEYS if k not in answer]
    if missing:
        return _covered_set_unknown(
            wire,
            "its predicate answered without "
            f"{', '.join(missing)} — the shared conditional answer is "
            f"{', '.join(_CONDITIONAL_ANSWER_KEYS)}, and an arm short of it "
            "has not said whether its covered set was computed at all",
        )
    return answer


def _prove_delta_sample(
    fdir: Path, project_root: str, cycle: int, fixed_in_cycle: int | None = None
) -> dict:
    """The requirement rows PROVE checks on a DELTA cycle (FR-013 / AC-018).

    The rows tied to the defects the preceding GRIND fixed — those are the rows
    whose verdicts the fixes could have changed — plus up to
    `PROVE_DELTA_SAMPLE_SIZE` more, drawn from the sorted remainder with
    `random.Random(cycle)`.

    Returns `{"rows", "tied", "sampled"}`: the roster and the two halves it was
    built from, `rows == tied + sampled`.

    D-177 — THE HALVES ARE RETURNED BECAUSE THE CALLER STATES THEM.
    --------------------------------------------------------------
    This returned the concatenated list alone, so `_decide_inspect_mode` — the
    one caller — had the roster and no way to say how it was made. It wrote the
    breakdown as f"{len(prove_sample)} row(s): rows tied to the fixed defects
    plus {PROVE_DELTA_SAMPLE_SIZE} sampled", interpolating the CONSTANT as
    though it were the measurement. The draw is `min(PROVE_DELTA_SAMPLE_SIZE,
    len(remaining))`, so the two clauses of that one sentence disagree by
    construction whenever the remaining pool is smaller than the ceiling.
    Driven end to end on a two-requirement spec with no fixed-defect rows:
    `prove_sample` recorded ['FR-001', 'FR-002'] and the stream_scope detail
    beside it read "2 row(s): rows tied to the fixed defects plus 10 sampled".
    That string is recorded into state.json and stream-rollup.json and is read
    back by the PROVE stream as its width statement, so a stream that trusts
    the breakdown looks for eight rows that were never drawn.

    TWO CYCLE NUMBERS, DELIBERATELY. ``cycle`` is the cycle this roster is FOR
    and is the seed; ``fixed_in_cycle`` is the cycle whose GRIND just ended and
    is where the tied rows come from. At the `inspect_start` boundary those
    differ by one — the counter has already been advanced for the INSPECT about
    to run, while "the defects fixed in the preceding GRIND" are stamped with
    the cycle that just closed. Seeding from one and selecting from the other is
    the whole of AC-018's sentence; collapsing them to a single number selects
    rows tied to a GRIND that has not happened, which is silently empty rather
    than wrong-looking. Defaults to ``cycle`` when the caller has only one.

    REPRODUCIBLE FROM THE CYCLE NUMBER ALONE (FR-033). The seed is ``cycle`` and
    nothing else, and the population is SORTED before the draw, so nothing
    depends on dict ordering, ledger order or filesystem order. Two calls in the
    same cycle return the same ten rows because the same seed draws from the
    same sequence — and the recorded roster means no caller ever draws twice
    anyway (AC-018 / OT-015).

    The sample is a floor on coverage, not a ceiling on it: a DELTA cycle checks
    what the fixes could have broken plus a random slice of everything else, so
    a regression outside the fixed rows is still found, just not in one cycle.
    """
    import random

    all_rows = sorted(set(_spec_requirement_ids(project_root)))
    if not all_rows:
        return {"rows": [], "tied": [], "sampled": []}

    fixed_rows: set[str] = set()
    for d in _load_json(fdir / "defects.json").get("defects", []):
        if not isinstance(d, dict) or d.get("status") != "fixed":
            continue
        if d.get("fixed_in_cycle") != (
            cycle if fixed_in_cycle is None else fixed_in_cycle
        ):
            continue
        ref = d.get("spec_ref")
        if isinstance(ref, str):
            fixed_rows.update(_REQ_ID_RE.findall(ref))

    tied = sorted(fixed_rows & set(all_rows))
    remaining = [r for r in all_rows if r not in set(tied)]
    # The ceiling, applied. `draw` is the number actually taken and is what the
    # caller states; `PROVE_DELTA_SAMPLE_SIZE` is the most it can be.
    draw = min(PROVE_DELTA_SAMPLE_SIZE, len(remaining))
    sampled = sorted(random.Random(cycle).sample(remaining, draw)) if draw else []
    return {"rows": tied + sampled, "tied": tied, "sampled": sampled}


def _base_required_streams(project_root: str) -> list[str]:
    """`sight` and `probe`, which are per-run facts rather than width facts.

    Both conditions are unchanged from what `_check_streams_complete` has always
    applied, and both hold at FULL and DELTA alike: a run with frontend files in
    scope needs SIGHT however narrow the diff, and a run with a target_url needs
    PROBE. Width decides how much of the spec PROVE reads, never whether the run
    has a UI.
    """
    fdir = get_run_dir(project_root)
    extra: list[str] = []
    if fdir and _check_sight_required(project_root).get("required"):
        extra.append("sight")
    if fdir and _load_json(fdir / "castings" / "manifest.json").get("target_url"):
        extra.append("probe")
    return extra


def _decide_inspect_mode(
    fdir: Path,
    project_root: str,
    *,
    decided_by: str,
    phase: str,
    cycle: int,
    widening: bool = False,
) -> dict:
    """Compute one `state.json.inspect_modes` entry. Writes nothing.

    ``decided_by`` is the phase token that performed the transition — `cast`,
    `temper` or `inspect_start` — and it is recorded, so "which crossing decided
    this width" is answerable from the artifact.

    THE RULES, EVALUATED IN ORDER (FR-011):

      first_of_phase    the phase-entry transition into F2 or F5. The first
                        INSPECT of a phase has no previous INSPECT to be a delta
                        FROM, so it is FULL by construction rather than by
                        policy.
      final_gate        this INSPECT precedes ASSAY, NYQUIST or DONE. The gates
                        that end a run are never handed a narrow answer. TWO
                        ways to be that INSPECT, and only two: the GRIND that
                        just closed was entered from ASSAY / TEMPER / NYQUIST
                        feedback, or this crossing is the F2->F2 WIDENING
                        re-open the lead makes to open ASSAY (``widening``).
      verifier_touched  the GRIND diff moved the machinery that JUDGES the
                        build — vocab, schemas, gate/orchestrator code, agent or
                        skill prose, or the spec. A delta roster is only as
                        trustworthy as the verifier it runs, so when the
                        verifier itself moved, nothing narrower than everything
                        is honest.
      delta             none of the above.

    D-068 — `blocking == 0` WAS THE final_gate TEST, AND IT MADE DELTA
    UNREACHABLE.
    -----------------------------------------------------------------
    The arm read `elif _blocking_defects(fdir)["blocking"] == 0`, which is true
    after ANY GRIND that fixed what INSPECT filed — which is exactly the state
    `_compute_next_action`'s F3 branch instructs the lead to reach before
    crossing ("GRIND complete: all defects fixed ... then
    Foundry-Phase(phase='inspect_start')"). So the ordinary cycle recorded
    FULL / final_gate every time and US-004 delivered nothing. Driven, cycle 1
    to 2, one-file handler diff: cleared ledger -> FULL/final_gate; empty ledger
    -> FULL/final_gate; a LATENT-only backlog -> FULL/final_gate; only an OPEN
    LIVE defect yielded DELTA. The shipped fixture conceded it in its own
    docstring — "One OPEN LIVE defect — enough to keep final_gate from firing.
    Every DELTA fixture needs this." — i.e. DELTA fired only when the lead
    crossed with LIVE defects still open, which no guidance instructs and which
    `Foundry-Gate('assay')` refuses anyway.

    LEAD RULING, GRIND cycle 4 (SPEC_AMBIGUOUS, run state.json entry 4): "the
    next gate is ASSAY" is not a fact about the DEFECT LEDGER, it is a fact
    about the TRANSITION. A DELTA INSPECT that comes back clean does not open
    ASSAY; it earns the right to re-open INSPECT at full width, and THAT
    crossing is the final gate. So `blocking == 0` is no longer a condition
    here, `_entered_grind_from_feedback` and `widening` are, and an ordinary
    GRIND that fixed everything now yields DELTA — which is the whole of
    AC-016's "and DELTA otherwise".

    D-069 — PRECEDENCE, SO THE RECORDED RULE NAME IS TRUE. The final_gate arm
    sat as an `elif` ahead of the `is_verifier_path` scan, so with a
    schemas/vocab.py diff and a cleared ledger it recorded
    `final_gate` / "no blocking defects remain" and the verifier scan never
    ran — OT-013's second half ("after one whose diff touches schemas/vocab.py
    it records FULL with rule verifier_touched") was unreachable and the F6
    report's per-cycle rule column was false. The mode was still FULL, so no
    verification was lost; the PROVENANCE was wrong, which is what FR-011's
    "Foundry-Next names which rule fired" is about. With `blocking` gone from
    the condition, final_gate now fires only on the two transition facts above,
    and an ordinary verifier-touching GRIND reaches the scan and records it.

    An UNKNOWN diff (git unavailable, no baseline to measure from) is FULL, and
    is recorded as `verifier_touched` with the real cause in ``rule_detail``.
    That is the closest member of the closed `INSPECT_FULL_RULES` vocabulary and
    the reason is the same one: nothing can show the verifier did NOT move, so
    the honest width is everything. See the concerns file — the vocabulary has
    no member meaning "the diff could not be computed", and inventing one here
    would be a seventh copy of a vocabulary casting 1 owns.

    D-152 — AND THE UNKNOWN-DIFF ARM IS EVALUATED LAST OF THE FULL ARMS, SO IT
    SUBSTITUTES FOR NO RULE THAT ACTUALLY FIRED.
    -------------------------------------------------------------------------
    That arm sat AHEAD of both `final_gate` arms, and the two facts they read —
    `widening`, and the phase history — are known whether or not a diff can be
    computed. So a GRIND entered from ASSAY feedback on a run whose run dir
    carries no `.inspect-boundary-sha`, `.trace-clean-at` or `.cast-baseline-sha`
    recorded `verifier_touched` with `rule_detail` "the GRIND diff could not be
    computed ... so the verifier cannot be shown to be untouched" — for the very
    cycle that opens ASSAY, whose own gate refused, at the time, by naming the
    recorded rule rather than the recorded width. (That refusal has since been
    corrected — D-169: both ASSAY doors read the MODE and always did, so a
    sentence turning on the rule was false about every FULL cycle the rule did
    not happen to name. It is quoted here only as the state of the tree D-152
    was filed against.) Driven at cycle 8 on a
    phase_history of F2 -> F4 -> F3; the same substitution occurs on the F2->F2
    widening re-open and wherever git is unavailable. The mode is FULL either
    way, so no verification is lost; what was wrong is the PROVENANCE, which is
    the whole of FR-011's "Foundry-Next names which rule fired" and which the F6
    report carries in its per-cycle rule column.

    This is D-069's failure shape one arm over — the same lesson, that a FULL
    arm placed ahead of another does not merely decide the width, it decides
    what the artifact SAYS decided the width. The order is therefore fixed as
    the lead ruling states it and as the docstring above lists it:
    first_of_phase, then BOTH final_gate facts, then verifier_touched (the
    uncomputable diff, then the scan), then DELTA. An uncomputable diff still
    yields FULL; it just no longer speaks over a rule that fired.
    """
    now = _now()
    diff = _grind_diff(fdir, project_root)
    touched = diff["files"]
    skips = _skipped_streams(fdir)
    research_skipped = _research_skipped(fdir)

    rule = ""
    rule_detail = ""
    if decided_by in ("cast", "temper"):
        rule = "first_of_phase"
        rule_detail = f"phase-entry transition into {phase}"
    elif widening:
        rule = "final_gate"
        rule_detail = (
            "the F2->F2 widening re-open: the preceding DELTA INSPECT came back "
            "clean, so this crossing is the INSPECT before ASSAY"
        )
    elif _entered_grind_from_feedback(fdir):
        rule = "final_gate"
        rule_detail = "this GRIND was entered from ASSAY, TEMPER or NYQUIST feedback"
    elif diff["problem"]:
        # D-152: LAST of the FULL arms. Both final_gate facts above are known
        # without a diff, so consulting the diff first substituted
        # `verifier_touched` for a rule that had already fired.
        rule = "verifier_touched"
        rule_detail = (
            f"the GRIND diff could not be computed ({diff['problem']}), so the "
            "verifier cannot be shown to be untouched"
        )
    else:
        # D-102: EVERY spelling of the run's spec, not just the one
        # `_resolve_spec_path` prefers. See `_spec_relative_paths`.
        spec_spellings = _spec_relative_paths(project_root) or [None]
        hits = [
            f for f in touched
            if any(is_verifier_path(f, spec) for spec in spec_spellings)
        ]
        if hits:
            rule = "verifier_touched"
            rule_detail = (
                f"{len(hits)} verifier file(s) changed: {', '.join(hits[:5])}"
            )

    full = bool(rule)
    if not rule:
        rule = INSPECT_DELTA_RULE

    scope: dict[str, dict] = {}
    required: list[str] = []
    if full:
        for wire in FULL_ROSTER_STREAMS:
            if wire in skips:
                scope[wire] = {"scope": "skipped", "detail": "manifest.stream_skips"}
                continue
            if wire == "research_audit" and research_skipped:
                scope[wire] = {"scope": "skipped", "detail": "research_skipped record"}
                continue
            required.append(wire)
            scope[wire] = {"scope": "full", "detail": "every item in scope"}
        prove_sample: list[str] = []
    else:
        # D-208: EVERY conditional arm is answered through one path, before the
        # roster loop, so the loop below selects an answer by wire id rather
        # than naming each stream and its predicate in one breath. That naming
        # is where the two arms drifted into different answer shapes.
        conditional = {
            wire: _delta_conditional_scope(fdir, project_root, wire, touched)
            for wire in sorted(DELTA_CONDITIONAL_STREAMS)
        }
        # The GRIND that just ended is `cycle - 1` on the `inspect_start`
        # boundary, where the counter has already been advanced for the INSPECT
        # this roster is for.
        prove_draw = _prove_delta_sample(
            fdir, project_root, cycle, fixed_in_cycle=max(0, cycle - 1)
        )
        prove_sample = prove_draw["rows"]
        for wire in FULL_ROSTER_STREAMS:
            if wire in skips:
                scope[wire] = {"scope": "skipped", "detail": "manifest.stream_skips"}
                continue
            if wire in DELTA_CONDITIONAL_STREAMS:
                # ST-007: required ONLY when the diff touches a file they cover.
                decision = conditional[wire]
                if wire == "research_audit" and research_skipped:
                    # A RECORDED skip is a computed fact about the run and
                    # outranks anything the covered set says, an unknown one
                    # included — the run has no research to audit against
                    # whatever moved. Stated in its own words: "no file
                    # research_audit covers was touched" would be a reason this
                    # branch never evaluated, and D-208 is precisely what
                    # recording an unevaluated reason costs. Same sentence the
                    # FULL branch writes for the same fact.
                    scope[wire] = {
                        "scope": "skipped",
                        "detail": "research_skipped record",
                    }
                    continue
                if not decision["touched"]:
                    scope[wire] = {
                        "scope": "skipped",
                        "detail": f"no file {wire} covers was touched",
                    }
                    continue
                required.append(wire)
                # D-204: the provenance NAMES what matched. This recorded the
                # bare claim "a covered file was touched", which is persisted
                # into state.json's inspect_modes and stream-rollup.json and
                # read back by the F6 per-cycle scope column — so when the
                # predicate answered yes for a file named nowhere in the spec,
                # nothing downstream could tell that from a real hit.
                #
                # D-207: `full` here now covers TWO things, and the detail is
                # what separates them — a computed hit naming what matched, or a
                # covered set the server could not compute at all, naming why.
                # Both are REQUIRED, which is the whole point: an unknown set
                # fails closed exactly as an uncomputable GRIND diff does. The
                # detail is the only place that distinction is written down, so
                # it is copied through verbatim rather than re-summarised.
                #
                # D-208: and BOTH arms now reach this line the same way — the
                # answer is selected out of `conditional` by wire id, so
                # whichever stream could not compute its covered set writes the
                # same shaped sentence here, and neither can reach the skip
                # above on a negative it never computed.
                scope[wire] = {"scope": "full", "detail": decision["detail"]}
                continue
            required.append(wire)
            if wire == "test":
                # AC-019: TEST runs FULL and COLD on a DELTA cycle. A narrowed
                # suite cannot see a regression the GRIND opened in a module it
                # did not edit, and that is the exact failure a delta INSPECT is
                # most exposed to.
                scope[wire] = {
                    "scope": "full",
                    "detail": "whole suite, cold, from a clean worktree",
                }
            elif wire == "trace":
                scope[wire] = {
                    "scope": "delta",
                    "detail": f"symbols in the {len(touched)} file(s) the GRIND touched",
                }
            else:
                # D-177: BOTH numbers are measured. This interpolated
                # `PROVE_DELTA_SAMPLE_SIZE` — the ceiling — as though it were
                # the count drawn, so on any spec whose remaining pool is
                # smaller than the constant the sentence contradicted the
                # roster printed beside it ("2 row(s) ... plus 10 sampled").
                # The draw is `min(ceiling, len(remaining))`, and the caller
                # states what the draw returned.
                scope[wire] = {
                    "scope": "delta",
                    "detail": (
                        f"{len(prove_sample)} row(s): "
                        f"{len(prove_draw['tied'])} tied to the fixed defects "
                        f"plus {len(prove_draw['sampled'])} sampled"
                    ),
                }

    for wire in _base_required_streams(project_root):
        if wire in skips or wire in required:
            continue
        required.append(wire)
        scope[wire] = {"scope": "full", "detail": "required by this run's manifest"}

    return {
        "cycle": cycle,
        "phase": phase,
        "mode": "FULL" if full else "DELTA",
        "rule": rule,
        "rule_detail": rule_detail,
        "decided_by": decided_by,
        "decided_at": now,
        "required_streams": required,
        "stream_scope": scope,
        "touched_files": touched,
        "prove_sample": prove_sample,
        "diff_base": diff["base"],
    }


def _entered_grind_from_feedback(fdir: Path) -> bool:
    """Was the GRIND that just ended entered from ASSAY / TEMPER / NYQUIST?

    Read from `state.json.phase_history`, which `_update_phase` appends to on
    every transition, rather than from a marker this would otherwise have to
    invent: the history already records exactly the fact being asked about, and
    a second record of it is a second thing that can drift.

    The walk is backwards from the end, skipping the F3 entries themselves, and
    the first non-F3 phase found is the one the GRIND was entered from. F4, F5
    and F5.5 all mean the same thing here — a gate rejected the run and sent it
    back — and the INSPECT that follows that GRIND is the one those gates will
    read next, so it runs at full width.
    """
    history = _load_json(fdir / "state.json").get("phase_history", [])
    if not isinstance(history, list):
        return False
    for entry in reversed(history):
        if not isinstance(entry, dict):
            continue
        phase = entry.get("phase")
        if phase == "F3":
            continue
        return phase in ("F4", "F5", "F5.5")
    return False


#: D-070 — the sub-bucket the F5 (TEMPER) entry's decision is recorded under.
#: The server cycle counter does NOT advance entering F5, so the temper
#: transition lands in the same `cycles[<cycle>]` bucket as the `inspect_start`
#: that opened that cycle. Its own key is what keeps both records.
TEMPER_ENTRY_ROLLUP_KEY = "temper_entry"

#: D-133 — and the same reasoning one crossing later. Entering F5.5 does not
#: advance the counter either, so the NYQUIST boundary's whole-corpus sweep is
#: recorded under its own key rather than overwriting the temper entry's.
NYQUIST_ENTRY_ROLLUP_KEY = "nyquist_entry"


def _record_cycle_rollup(fdir: Path, cycle: int, *, sub: str = "", **fields) -> None:
    """Write CYCLE-level facts into `stream-rollup.json` (C-6).

    Sibling of `_record_stream_rollup`, not an extension of it. That function
    accumulates one STREAM's tranches inside `cycles[<cycle>][<stream>]`; these
    keys — `inspect_mode`, `inspect_rule`, `stream_scope`, `evidence_sweep` —
    describe the cycle itself and sit beside the stream buckets rather than
    inside one. Widening the stream writer with cycle-level kwargs would give
    one function two jobs and make `cycles[<cycle>]["inspect_mode"]` look, to
    every existing reader, like a stream called `inspect_mode`.

    ``sub`` NESTS THE WRITE, AND EXISTS FOR EXACTLY ONE CALLER (D-070).
    ------------------------------------------------------------------
    This keyed the bucket by `str(cycle)` and did `bucket.update(fields)`, and
    the F5 entry decides its mode with `cycle=_current_cycle(fdir)` — a counter
    that does NOT advance entering F5. So the temper decision landed in the
    same bucket as the last `inspect_start` and OVERWROTE it. Driven:
    `inspect_start` recorded cycle 2 as DELTA/delta; after
    `Foundry-Phase('temper')`, `cycles['2']` read FULL/first_of_phase and the
    DELTA `stream_scope` was gone. CT-009 requires the decision recorded "in
    state AND stream-rollup at the transition"; the state list survived because
    it is append-only, but any reader taking the roll-up as the per-cycle width
    — the F6 report's cycle table among them — saw a fabricated FULL for a
    cycle that ran DELTA.

    The F5 entry now writes under `TEMPER_ENTRY_ROLLUP_KEY` and both records
    survive. A nested bucket rather than a `"<cycle>:F5"` sibling key, so
    `cycles` stays keyed by cycle number alone and no existing reader has to
    learn a second key grammar.

    Shares the same `_document_transaction`, which is the part that matters:
    D-103's concurrency site is this file, and a second unlocked writer of it
    would lose records exactly as the unlocked stream writer did.
    """
    with _document_transaction(fdir / ROLLUP_FILENAME) as data:
        cycles = data.setdefault("cycles", {})
        if not isinstance(cycles, dict):
            cycles = data["cycles"] = {}
        bucket = cycles.setdefault(str(cycle), {})
        if not isinstance(bucket, dict):
            bucket = cycles[str(cycle)] = {}
        if sub:
            nested = bucket.setdefault(sub, {})
            if not isinstance(nested, dict):
                nested = bucket[sub] = {}
            bucket = nested
        bucket.update(fields)
        data["updated_at"] = _now()


#: FR-036 (Flexible) — how long a gap between Foundry-Next calls has to be
#: before the watchdog says anything at all. Unchanged from the 180 seconds this
#: has always used: the threshold was never the defect, the accusation was.
#: A gap this long is worth REMARKING on either way; what changed is that the
#: remark now depends on whether an agent is actually running.
STALL_NOTICE_SECONDS = 180


def _waiting_on_agents(project_root: str) -> dict:
    """Is the lead waiting on live agents, or is it deliberating (FR-020)?

    Returns ``{"waiting": bool, "count": int, "detail": str, "agents": [...]}``.

    BOTH DECLARED INPUTS ARE READ; PROGRESS IS WHAT DECIDES (D-076, D-127).
    ----------------------------------------------------------------------
    CT-012 declares this check's inputs as ".last-next-at, ACTIVE TEAMS,
    Foundry-Liveness roster"; FR-020 (Locked, verbatim) reads "Before accusing,
    Foundry-Next checks ACTIVE TEAMS AND Foundry-Liveness ... IF AGENTS ARE
    RUNNING it reports 'waiting on N agents'". Both sources are read. What they
    are read FOR is the question the two defects here disagreed about.

    D-076 removed the team scan entirely and the suite asserted its own absence
    with an AST walk, so a declared contract input had a test guarding the fact
    that nothing consulted it. The GRIND cycle-4 repair restored the scan and
    ANDed it: waiting required a registered ACTIVE team **and** a progressing
    ledger.

    D-127 — THE AND ACCUSED THE LEAD WHILE ITS OWN LIVENESS READ SHOWED AGENTS
    WORKING. The F2 INSPECT streams are background Agents, never tmux
    teammates, so `_check_active_teams` cannot see them — the cycle-4 comment
    recorded that as a "known consequence" and the consequence is the defect.
    Driven: no registered team, two progress ledgers written seconds earlier,
    `.last-next-at` 600s old -> `waiting` False, `progressing_agents` 2, and
    Foundry-Next emitted `stall_detected_seconds` 600 beside the notice "NO
    agent is running. You were silently deliberating" — asserting deliberation
    over two agents the same call had just measured progressing. FR-020 is
    Locked and FR-036's proviso is that the notice NEVER asserts deliberation
    while an agent is progressing.

    LEAD RULING, GRIND cycle 7 (state.json `spec_ambiguities` entry 7,
    superseding the cycle-4 AND where they conflict): "if agents are running"
    is decided by EVIDENCE OF PROGRESS.

      * a progressing ledger, no registered team  -> WAITING   (D-127)
      * a registered but DEAD team, no ledger     -> STALL     (D-021)
      * neither                                    -> STALL     (AC-032 arm 2)

    So a progressing roster is SUFFICIENT whether or not a team is registered,
    and a registered team is never sufficient on its own. AC-032's "with no
    active teams it reports the stall" means no RUNNING AGENTS by either input,
    which is what the roster measures. D-021's cause stays closed for the same
    reason it was closed: a stale team directory alone still cannot suppress
    the warning, because the ledgers are what answer.

    `teams_active` is still read and still REPORTED on the result, so CT-012's
    input set is unchanged and a caller can still tell a registered team from a
    progressing one.

    NEVER RAISES AND NEVER BLOCKS (CT-012). Every failure path answers "not
    waiting", which degrades to the pre-change behaviour — the watchdog warns —
    rather than to silence. A liveness reader that cannot answer must not be
    able to suppress a real stall warning.
    """
    result = {"waiting": False, "count": 0, "detail": "", "agents": []}

    # CT-012's second declared input. Read FIRST and never raised through: a
    # scan that cannot answer must not be able to suppress a stall warning
    # either, so an unusable answer reads as "no active team".
    try:
        teams = _check_active_teams(project_root)
    except Exception:  # noqa: BLE001 - a watchdog never raises into its caller
        teams = {"active": False, "teams": []}
    teams_active = bool(teams.get("active"))

    try:
        from foundry_mcp.tools.foundry_spawn import (
            STATUS_NO_PROGRESS,
            STATUS_PROGRESSING,
            foundry_liveness,
        )

        liveness = foundry_liveness(None, None, project_root=project_root)
    except Exception:  # noqa: BLE001 - a watchdog never raises into its caller
        liveness = {"ok": False}

    live_agents = []
    if liveness.get("ok"):
        for row in liveness.get("agents", []) or []:
            if not isinstance(row, dict):
                continue
            # PROGRESSING and NO_PROGRESS both mean lines are still ARRIVING;
            # they differ only in whether the `step` field moved. STALLED means
            # no line at all for the threshold, DONE means finished, and
            # NO_LEDGER / UNKNOWN mean there is no evidence — none of which is
            # an agent to wait for.
            if row.get("status") in (STATUS_PROGRESSING, STATUS_NO_PROGRESS):
                live_agents.append(row)

    # D-127 / FR-020, stated once: PROGRESS decides. An EMPTY roster is the one
    # answer that lets the watchdog speak. A registered team with nothing
    # progressing is D-021's stale directory rather than an agent to wait for,
    # and it can no longer suppress the warning because it never reaches past
    # this line on its own.
    if not live_agents:
        result["teams_active"] = teams_active
        result["progressing_agents"] = 0
        return result

    # FR-036: the oldest progress age is what the lead actually needs — the
    # agent least recently heard from is the one that decides whether this
    # wait is healthy.
    ages = [
        row.get("last_progress_age_seconds", 0)
        for row in live_agents
        if isinstance(row.get("last_progress_age_seconds"), int)
    ]
    oldest = max(ages) if ages else 0
    result.update({
        "waiting": True,
        # D-127: REPORTED, not asserted. This used to be the literal `True` the
        # AND had already proved; waiting no longer implies a registered team,
        # so the field carries what the scan actually answered and CT-012's
        # input set stays visible to every reader.
        "teams_active": teams_active,
        "progressing_agents": len(live_agents),
        "count": len(live_agents),
        "detail": f"oldest progress {oldest // 60}m {oldest % 60}s ago",
        "agents": [
            {"agent": r.get("agent"), "status": r.get("status"),
             "step": r.get("step")}
            for r in live_agents
        ],
        "oldest_progress_seconds": oldest,
    })
    return result


def _halted_state(fdir: Path) -> dict | None:
    """The run's HALTED record, or None when the run is not halted.

    THE ONLY READ of `state.json.phase == RUN_PHASE_HALTED`, for the reason
    `_current_inspect_mode` is the only read of the recorded width: a terminal
    state that each door decides for itself is a terminal state each door can
    decide differently.
    """
    state = _load_json(fdir / "state.json")
    if state.get("phase") != RUN_PHASE_HALTED:
        return None
    return {
        "halted_at_cycle": state.get("halted_at_cycle"),
        "halted_reason": (
            str(state.get("halted_reason") or "").strip()
            or "the configured cycle cap was reached"
        ),
        "max_cycles": state.get("max_cycles", 0),
        # D-165: what the halt transition's own report generation did. Recorded
        # by `_halt_if_capped` and read here so the refusal cannot promise a
        # document the transition failed to write.
        "halted_report_error": str(state.get("halted_report_error") or "").strip(),
    }


def _halted_refusal(fdir: Path, surface: str) -> dict | None:
    """ST-008 / CT-016 / FR-024 / FR-045 / FR-052 — HALTED is terminal, and
    every door that could leave it reads THIS.

    Returns None when the run may proceed, otherwise the refusal `surface`
    should return, carrying `error` / `hint` in the house shape plus the halt
    record. `foundry_gate` reshapes the same two strings into its
    `reason` / `hint` pair; nothing recomputes the judgement.

    D-081 / D-082 — HALTED WAS WRITTEN AND READ BY NOTHING.
    ------------------------------------------------------
    ST-008 says "HALTED is not DONE", CT-016 calls it "a named terminal state
    distinct from DONE", FR-024 says "the run ends in a named HALTED state
    rather than DONE" — and `_halt_if_capped` was the only code in the server
    that mentioned the state at all. It wrote `phase = HALTED` from the two
    doors that open a GRIND and then nothing, anywhere, asked.

    Driven, both halves. (1) max_cycles 2 at cycle 2 with one open LATENT
    defect: `Foundry-Phase('grind_start')` returned ok / halted True and
    state.phase HALTED; two calls later `Foundry-Gate('done')` returned passed
    True with reason None and `Foundry-Phase('done')` returned ok, phase F6,
    "Run archived." The cap exists precisely so the run does not end as DONE,
    and the F6 state recorded nothing about it having fired. (2) From
    state.phase HALTED: `Foundry-Phase('inspect_start')` returned ok, set F2
    and ADVANCED the cycle counter; `cast` returned ok and set F2; `temper`
    returned ok and set F5; `nyquist` returned ok and set F5.5. Only
    `grind_start` and `assay_fail` re-halted, because only they call
    `_halt_if_capped`; every other branch had no HALTED precondition at all. So
    a halted run resumed and kept dispatching with no refusal and no record
    that the cap had been overridden — and FR-052's "Foundry-Next reports
    halted and stops dispatching" rested on lead discipline, which is the thing
    the cap exists to replace.

    NOT A WAIVER AND NOT A RESUME PATH. There is deliberately no token, flag or
    argument that leaves HALTED, because "the operator may override the cap
    in-place" is how a bounded run becomes an unbounded one. The remedy the
    hint names is a NEW run with a higher `--max-cycles`, which starts a fresh
    counter and a fresh state file, so the override is a decision someone makes
    on the record rather than one more call in the loop.
    """
    halted = _halted_state(fdir)
    if halted is None:
        return None
    cycle_text = (
        f"cycle {halted['halted_at_cycle']}"
        if isinstance(halted["halted_at_cycle"], int)
        else "its cycle cap"
    )
    # D-165 — THE REPORT IS ASSERTED ONLY WHERE IT WAS WRITTEN.
    #
    # This said "the report says what" and pointed at REPORT.md unconditionally,
    # because `_halt_if_capped` asserted the same thing unconditionally. On a
    # halt whose report generation failed (CT-014's designed unreadable-ledger
    # branch) the operator was sent to read a file that does not exist, from a
    # state with no exit, with no reason given to regenerate it. Both halves are
    # read from the record the transition now leaves: the presence of the file,
    # and the error the generator returned.
    #
    # THE FILE'S PRESENCE IS THE GROUND TRUTH, and the recorded error only
    # supplies the REASON when it is absent. Reading the recorded error as
    # authoritative would go stale the moment the operator follows this hint and
    # calls Foundry-Report — a refusal that then still said "NOT written" about
    # a file sitting on disk would be this same defect with the sign flipped.
    report_path = fdir / REPORT_MD_FILENAME
    report_error = halted["halted_report_error"]
    report_present = report_path.exists()
    return {
        "error": (
            f"Cannot call {surface} — this run is HALTED. It stopped at "
            f"{cycle_text} because {halted['halted_reason']}. HALTED is a "
            "terminal state and it is NOT DONE: the run ended with open work"
            + (
                " and the report says what."
                if report_present
                else (
                    f", and the report was NOT written — {report_error or 'it is not present at ' + str(report_path)}."
                )
            )
        ),
        "hint": (
            (
                f"Nothing leaves HALTED — no phase token, no gate. Read "
                f"{REPORT_MD_FILENAME}, tell the user what remains open by "
                "tier, and stop. To carry the remaining work forward, start a "
                "NEW run (Foundry-Init) with a higher --max-cycles; the cap is "
                "not overridden in place."
            )
            if report_present
            else (
                "Nothing leaves HALTED — no phase token, no gate — but the "
                "report is not a phase transition and Foundry-Report still "
                "runs on a halted run. Repair what the error above names, call "
                f"Foundry-Report to write {REPORT_MD_FILENAME}, then read it, "
                "tell the user what remains open by tier, and stop. Until it "
                "is written, read defects.json directly: the open work is "
                "recorded there whatever the report generator could not "
                "render. To carry the remaining work forward, start a NEW run "
                "(Foundry-Init) with a higher --max-cycles; the cap is not "
                "overridden in place."
            )
        ),
        "halted": True,
        "phase": RUN_PHASE_HALTED,
        "halted_at_cycle": halted["halted_at_cycle"],
        "halted_reason": halted["halted_reason"],
        "max_cycles": halted["max_cycles"],
        # Named as what it IS rather than always as a path, so a caller cannot
        # read a promise out of the field's presence.
        "report": str(report_path) if report_present else None,
        "report_generated": report_present,
        "report_error": report_error,
    }


def _halt_if_capped(fdir: Path, project_root: str, token: str) -> dict | None:
    """ST-008 / CT-016 — halt the run instead of opening GRIND number N+1.

    Returns None when the run may proceed, otherwise the SUCCESS result of the
    HALTED transition. Called from both transitions that open a GRIND.

    THIS IS A TRANSITION, NOT A REFUSAL, and the distinction is the whole
    requirement (FR-045 / A-048). A refusal would leave the run sitting in F2
    with the lead free to call the same token again, having produced nothing —
    a cap that only annoys. Instead `state.json` becomes HALTED, the report is
    generated naming every open LIVE and LATENT defect, and the call returns
    ``ok: True``. HALTED is a named terminal state and is emphatically NOT DONE:
    it is where a run that ran out of cycles stops, with its open work written
    down.

    THE ARITHMETIC. The server counter advances only at `inspect_start`, so the
    GRIND a call is about to open is always `cycle + 1`. With `max_cycles` 2:
    GRIND 1 opens at counter 0, GRIND 2 at counter 1, and the call at counter 2
    would open GRIND 3 — which is the one that halts (AC-037 / OT-026).
    ``max_cycles`` 0 is unbounded and is the default, so a run that never passed
    the flag is unaffected.
    """
    state = _load_json(fdir / "state.json")
    max_cycles = state.get("max_cycles", 0)
    if not isinstance(max_cycles, int) or isinstance(max_cycles, bool) or max_cycles <= 0:
        return None
    cycle = _current_cycle(fdir)
    opening = cycle + 1
    if opening <= max_cycles:
        return None

    reason = (
        f"--max-cycles {max_cycles} reached: opening GRIND cycle {opening} would "
        f"exceed it"
    )
    with _document_transaction(fdir / "state.json") as doc:
        doc["phase"] = RUN_PHASE_HALTED
        doc["halted_at_cycle"] = cycle
        doc["halted_reason"] = reason
        doc["updated_at"] = _now()

    # FR-045: "the report is written naming every open LIVE and LATENT defect".
    # Generated as PART of this transition rather than left to the lead, because
    # a halted run whose open work was never written down is the outcome the cap
    # is supposed to prevent, not a variant of it.
    #
    # D-165 — AND ITS VERDICT IS READ, BECAUSE THE MESSAGE ASSERTS IT.
    # ---------------------------------------------------------------
    # `report` was discarded. Driven at the real door: max_cycles 2 at cycle 2,
    # one open LIVE and one open LATENT defect, and a deliberately corrupt
    # verdicts.json — this returned ok True, wrote phase HALTED and said "The
    # report has been generated naming 1 open LIVE, 0 untiered and 1 open LATENT
    # defect(s)", while REPORT.md did not exist on disk and the nested result
    # carried ok False, "verdicts.json is not valid JSON". Every later call then
    # compounded it: `_halted_refusal` told the operator to read a report that
    # was never written, and HALTED has no exit by design, so nothing would ever
    # regenerate it. CT-014 SPECIFIES that failure branch (the unreadable-ledger
    # refusal), so it is designed and reachable, not a theoretical one.
    #
    # The transition still HAPPENS — FR-045 is explicit that the cap is "not a
    # refusal", and a run that ran out of cycles has run out of cycles whether
    # or not its ledgers can be rendered. What changes is that the outcome is
    # RECORDED and SAID: the halt names the failure, and every later refusal
    # names the one call that can still write the report.
    report = _generate_report(project_root, fdir)
    report_ok = bool(report.get("ok"))
    report_error = "" if report_ok else str(
        report.get("error") or "the report generator returned no reason"
    )
    if not report_ok:
        # A second short transaction rather than one around the generator: the
        # report is generated AFTER `phase` is HALTED so that it renders the
        # halted run, and `_document_transaction` is an fcntl-locked critical
        # section that must not be held across it.
        with _document_transaction(fdir / "state.json") as doc:
            doc["halted_report_error"] = report_error
            doc["updated_at"] = _now()
    blocking = _blocking_defects(fdir)

    counts = (
        f"{len(blocking['live'])} open LIVE, "
        f"{len(blocking['unknown'])} untiered and "
        f"{len(blocking['latent'])} open LATENT defect(s)"
    )
    return {
        "ok": True,
        "halted": True,
        "phase": RUN_PHASE_HALTED,
        "cycle": cycle,
        "max_cycles": max_cycles,
        "halted_reason": reason,
        "requested_token": token,
        "report": report,
        "report_generated": report_ok,
        "report_error": report_error,
        "open_live_defects": blocking["live"],
        "open_unknown_tier_defects": blocking["unknown"],
        "open_latent_defects": blocking["latent"],
        "message": (
            (
                f"Run HALTED — {reason}. The report has been generated naming "
                f"{counts}. HALTED is not DONE: this run stopped with open "
                "work, and the report says what."
            )
            if report_ok
            else (
                f"Run HALTED — {reason}. The report could NOT be generated: "
                f"{report_error}. The {counts} named above are read from "
                "defects.json, which is intact; it is the report that is "
                "missing. Repair what the error names, then call Foundry-Report "
                "— it is not a phase transition, so it still runs on a halted "
                "run. HALTED is not DONE: this run stopped with open work."
            )
        ),
    }


# --------------------------------------------------------------------------- #
# Spend (CT-013 / GI-005 / FR-021 / FR-022 / FR-037)
#
# GI-005 IS A CONSTRAINT ON THIS SERVER'S INPUTS, NOT ON THE FEATURE.
# "Parser stays out of the server; the fragile block is only ever read by the
# lead." Nothing below tails a transcript JSONL, regex-parses an Agent usage
# block, or reads any harness-owned format. The LEAD reads its own usage block
# and types the two numbers into `Foundry-Spend`; that is the ONLY channel by
# which a token count enters this run's artifacts. A parser for a format nobody
# owns does not fail loudly — it silently starts reporting a wrong number, which
# is worse than reporting none.
#
# NOTHING HERE REFUSES (CT-013 / FR-022 / AC-034). A forgotten Foundry-Spend is
# a gap in a cost report, not a defect in the build, and a gate that blocked on
# one would make an accounting omission stop a run. Unreported dispatches are
# DERIVED and LISTED instead, so the gap is visible rather than invisible.
# --------------------------------------------------------------------------- #


def _empty_spend_bucket() -> dict:
    return {"tokens": 0, "duration_ms": 0, "agents": 0, "unreported": 0}


def _spend_ledger_rows(fdir: Path) -> list[dict]:
    """Every line of `spend.jsonl`, skipping any that will not decode.

    A malformed line is skipped rather than raised on, for the reason every
    reader in this package skips a malformed record: this is advisory data, and
    losing a cost report to one bad line would be a strictly worse outcome than
    a cost report missing one row.
    """
    text, problem = read_text_file(fdir / SPEND_LEDGER_FILENAME)
    if problem is not None:
        return []
    rows: list[dict] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


# D-048 — ONE ROLL-UP DICT, ONE PHASE VOCABULARY.
#
# `spawns.log` records the DISPATCH VERB a teammate was handed out under —
# this run's own log carries `cast` 18 times and `grind` 7 — while
# `Foundry-Spend`'s schema documents the phase as a RUN PHASE ID and the
# ledger buckets under `F1`. So `state.json.spend.by_phase` grew a bucket keyed
# `grind`, which is not a phase, carrying tokens 0; and the exact
# `(agent, phase)` pair could never match for a teammate dispatch, which is
# what the agent-wide fallback in `_unreported_dispatches` was written to work
# around (D-047). Reconciling the two vocabularies is what makes the exact test
# the workable one and removes the need for the fallback at all.
#
# OWNED HERE because this module owns the dispatch side: `foundry_spawn` writes
# the verbs and this module maps them. `foundry_report` READS this constant
# through a function-local import rather than re-typing the mapping, so both
# surfaces bucket identically or neither does.
DISPATCH_PHASE_TO_RUN_PHASE = {"cast": "F1", "grind": "F3"}


def _spawn_rows(fdir: Path) -> list[dict]:
    """The `spawns.log` rows, exactly as casting 4's `foundry_spawn.py` wrote them.

    Handed to `unreported_dispatch_pairs` unchanged — the verb-to-phase mapping
    and the agent-id spelling are both applied inside it, from the constant and
    the minting function passed in, so nothing is normalised twice or normalised
    differently by the two surfaces that read this log.
    """
    rows: list[dict] = []
    text, problem = read_text_file(fdir / "spawns.log")
    if problem is not None:
        return rows
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def _stream_dispatch_cycles(fdir: Path) -> dict[str, list[str]]:
    """`{stream wire id: [server cycles it ran in]}` from `stream-rollup.json`.

    The F2 stream agents appear in no `spawns.log` row at all, so this is the
    only record that they were dispatched. A stream is a bucket key whose VALUE
    is a record carrying `records` — testing the value rather than keeping a
    denylist of non-stream keys is what keeps the C-6 additions
    (`inspect_mode`, `stream_scope`, `evidence_sweep`) out of the roster without
    an edit every time the roll-up gains a field.

    D-182 — AND THAT TEST IS READ, NOT RE-TYPED. The same predicate was spelled
    out inline here and in `measure-run.py` and `foundry_report.py`, three
    derivations of one rule over one document, and D-182 was the third of them
    disagreeing. `foundry_state.is_stream_record` is now the single definition
    and every reader calls it, so a C-6 addition that has to be excluded is
    excluded everywhere by one edit rather than by three that must agree.

    The cycle is kept as its own axis and never folded into the phase: every
    INSPECT stream shares the phase `F2`, and `cycle-1` is a cycle, not a phase
    a lead could ever type into `Foundry-Spend`.
    """
    cycles: dict[str, list[str]] = {}
    rollup = _load_json(fdir / ROLLUP_FILENAME).get("cycles", {})
    if isinstance(rollup, dict):
        for cycle_key, bucket in rollup.items():
            if not isinstance(bucket, dict):
                continue
            for stream, entry in bucket.items():
                if is_stream_record(entry):
                    cycles.setdefault(str(stream), []).append(str(cycle_key))
    return {stream: sorted(seen) for stream, seen in cycles.items()}


def _stream_roster(fdir: Path) -> dict[str, list[str]]:
    """C-5's `{run phase id: [F2 stream agent ids]}`, as the helper wants it."""
    streams = sorted(_stream_dispatch_cycles(fdir))
    return {"F2": streams} if streams else {}


def _dispatch_pairs(fdir: Path, spend_rows: list[dict]) -> list[dict]:
    """`unreported_dispatch_pairs` for this run — THE one rule, called once.

    D-047 / D-048: the rule used to live here AND in
    `foundry_report._read_unreported_dispatches`, two derivations of one
    question that D-013 had already unified on the wrong answer. It is now
    casting 5's pure helper in `foundry_state`, the only module both readers
    already import; this function supplies the run's four inputs and nothing
    else, so `Foundry-Next` and the F6 report cannot drift apart again.

    Passing an EMPTY `spend_rows` asks the same helper the denominator question
    — "every dispatched pair, nothing cleared" — rather than walking the two
    sources a second time here with a second set of rules.

    Both imports are lazy for this module's standing reason: `foundry_spawn`
    imports this module, so a module-level import closes the cycle, while a
    call-time one runs when every module in the chain is already built.
    """
    from foundry_mcp.tools.foundry_spawn import _agent_id_for_casting
    from foundry_mcp.tools.foundry_state import unreported_dispatch_pairs

    return unreported_dispatch_pairs(
        dispatch_rows=_spawn_rows(fdir),
        stream_roster=_stream_roster(fdir),
        spend_rows=spend_rows,
        phase_of_dispatch=DISPATCH_PHASE_TO_RUN_PHASE,
        agent_id_of=_agent_id_for_casting,
    )


def _dispatched_agents(fdir: Path) -> list[dict]:
    """Every `(agent, phase)` this run DISPATCHED, with its cycle where known.

    Two sources, because neither sees every agent (C-5): `spawns.log` covers
    every CAST and GRIND teammate, and the F2 stream agents are spawned from the
    roster the INSPECT-opening transition recorded and appear in `spawns.log`
    never. Both are reconciled by `_dispatch_pairs`, so the phase on every row
    here is a RUN PHASE ID.

    The cycle rides beside the pair rather than inside it. One stream agent
    unreported across three cycles is three rows here and one pair there, which
    is what lets `by_cycle` carry a per-cycle count while `by_phase` and the F6
    report read the pair.
    """
    cycles = _stream_dispatch_cycles(fdir)
    rows: list[dict] = []
    for pair in _dispatch_pairs(fdir, []):
        stamps = cycles.get(pair["agent"], []) if pair["phase"] == "F2" else []
        if stamps:
            rows.extend({**pair, "cycle": stamp} for stamp in stamps)
        else:
            rows.append(dict(pair))
    return rows


def _dispatched_agent_ids(fdir: Path) -> set[str]:
    """Every agent id this run's DISPATCH RECORD names (CT-013 / D-189).

    The same two sources `_unreported_dispatches` reconciles — `spawns.log`'s
    CAST and GRIND teammates and the F2 stream roster recorded in
    `stream-rollup.json` — read through the same `_dispatch_pairs` derivation,
    with the phase axis dropped. CT-013's condition is "unknown AGENT", not
    "unknown agent-phase pair": a dispatched agent whose spend is filed under a
    phase it did not run in is a different mistake, and reporting it as an
    unknown agent would be the false positive that teaches a lead to ignore
    the warning.

    A run with no dispatch record at all returns the empty set, and every agent
    is then unknown, which is the honest answer: nothing was dispatched through
    a door that records one, so there is nothing to reconcile any spend against.
    """
    return {
        str(pair["agent"])
        for pair in _dispatch_pairs(fdir, [])
        if pair.get("agent")
    }


def _unreported_dispatches(fdir: Path) -> list[dict]:
    """Dispatched agents with no `spend.jsonl` line for THAT phase (AC-034).

    DERIVED, never refused on. FR-022 is explicit: "A forgotten Foundry-Spend
    never blocks a gate; the report shows N agents unreported per phase so the
    gap is visible." An accounting omission is a thing to SEE, not a thing to
    stop a run over.

    D-047 — PER PHASE, WHICH IS WHAT FR-022 ASKS FOR. This carried a second
    clause, `or row["agent"] in reported_agents`, which cleared EVERY dispatch
    of any agent that reported spend once anywhere. Driven: casting-3
    dispatched at two phases with spend reported for one of them appeared
    nowhere in the list, so the very gap the section exists to show was the one
    shape it could not show. The clause is gone; a pair is unreported when no
    spend row carries that exact `(agent, phase)`.
    """
    unreported = {
        (pair["agent"], pair["phase"])
        for pair in _dispatch_pairs(fdir, _spend_ledger_rows(fdir))
    }
    return sorted(
        (
            row
            for row in _dispatched_agents(fdir)
            if (row["agent"], row["phase"]) in unreported
        ),
        key=lambda r: (r["agent"], r["phase"], str(r.get("cycle", ""))),
    )


def _dispatch_summary(fdir: Path) -> dict:
    """C-5's unreported COUNTS for this run — casting 5's one deriver, called.

    `foundry_state.unreported_dispatch_summary` is the arithmetic over
    `unreported_dispatch_pairs`, which is still the RULE. This supplies the
    run's inputs and nothing else, exactly as `_dispatch_pairs` does for the
    rule, so `Foundry-Next`, `report.json` and `REPORT.md` publish ONE integer
    because it is one derivation.

    `cycles_of_agent` is the F2 stream-dispatch cycle map, which is what keeps
    `by_cycle` a per-cycle axis while `count` and `by_phase` stay keyed on the
    pair (D-162).
    """
    from foundry_mcp.tools.foundry_spawn import _agent_id_for_casting
    from foundry_mcp.tools.foundry_state import unreported_dispatch_summary

    return unreported_dispatch_summary(
        dispatch_rows=_spawn_rows(fdir),
        stream_roster=_stream_roster(fdir),
        spend_rows=_spend_ledger_rows(fdir),
        phase_of_dispatch=DISPATCH_PHASE_TO_RUN_PHASE,
        agent_id_of=_agent_id_for_casting,
        cycles_of_agent=_stream_dispatch_cycles(fdir),
    )


def _overlay_unreported(spend: dict, summary: dict) -> dict:
    """Write the DERIVED unreported counts onto the C-4 buckets (D-031).

    ``summary`` is `_dispatch_summary`' output — casting 5's
    `unreported_dispatch_summary`. This distributes its counts across
    `by_phase`, `by_cycle` and `total`, and returns the same document it was
    handed.

    D-162 — TWO DERIVATIONS OF ONE NUMBER, AND THE PAIR WAS THE RIGHT ONE.
    ---------------------------------------------------------------------
    This took `_unreported_dispatches`' ROW list and incremented `by_phase`
    once per row, with `total["unreported"] = len(rows)`. `_dispatched_agents`
    re-expands the pair set into one row per cycle stamp for every F2 stream
    agent, so a stream agent unreported across nine cycles was NINE rows and
    ONE pair — and `_dispatched_agents`' own docstring states the intended rule
    as "by_cycle carry a per-cycle count while by_phase and the F6 report read
    the pair". Driven through the real doors on a copy of this run's archive:
    `foundry_next_action` returned `spend.unreported_count 51` and rendered
    "Unreported: 51", with `by_phase unreported {F1 8, F3 6, F2 37}`, while
    `foundry_report._read_unreported_dispatches` on the same archive returned
    `count 19` with `by_phase {F1 8, F2 5, F3 6}`. Two surfaces, one run, one
    question, two numbers — and `commands/start.md`'s SPEND ACCOUNTING section
    describes them as one set ("Foundry-Next shows the count and the F6 report
    lists each unreported agent by name and phase"). The trigger is any F2
    stream agent unreported across more than one cycle, which is the normal
    shape of a real run.

    So the counts are READ off the one deriver: `total` and each `by_phase`
    bucket count PAIRS, `by_cycle` counts the per-cycle appearances, and
    nothing here re-derives either axis.

    D-031 — A FIELD THAT IS INITIALISED AND NORMALISED BUT NEVER WRITTEN.
    --------------------------------------------------------------------
    `_empty_spend_bucket` has carried an `unreported` key since C-4 named it,
    `foundry_record_spend` re-coerced it to an int on every call, and NOTHING
    in the tree ever incremented it. It was permanently 0, so a consumer
    reading a per-bucket unreported count read a number that could not be
    distinguished from "every dispatch in this phase reported" — the exact
    reading FR-022 exists to make available, returning the exact opposite of
    the truth on a run where nobody called `Foundry-Spend` at all.

    DERIVED HERE, NOT ACCUMULATED AT THE DOOR. An unreported dispatch is the
    ABSENCE of a record, so it cannot be counted when a record arrives: the
    number changes when an agent is DISPATCHED, which is a different tool's
    call, and a counter incremented at the spend door would be wrong from the
    next spawn onward. One derivation, applied both to the summary a reader
    gets and to the persisted document, so state.json holds the count C-4 names
    instead of a zero that means nothing.

    A phase with unreported dispatches and NO recorded spend gets a bucket
    created for it. That is the whole point: the run where the lead forgot
    every `Foundry-Spend` call is the one where the gap most needs a line, and
    a bucket that only exists once someone reports would hide exactly that run.
    """
    for section in ("by_phase", "by_cycle"):
        if not isinstance(spend.get(section), dict):
            spend[section] = {}
    if not isinstance(spend.get("total"), dict):
        spend["total"] = _empty_spend_bucket()

    for bucket in (
        *spend["by_phase"].values(), *spend["by_cycle"].values(), spend["total"],
    ):
        if isinstance(bucket, dict):
            bucket["unreported"] = 0

    for phase, agents in (summary.get("by_phase") or {}).items():
        bucket = spend["by_phase"].setdefault(str(phase), _empty_spend_bucket())
        bucket["unreported"] = len(agents)
    for cycle, agents in (summary.get("by_cycle") or {}).items():
        bucket = spend["by_cycle"].setdefault(str(cycle), _empty_spend_bucket())
        bucket["unreported"] = len(agents)
    spend["total"]["unreported"] = int(summary.get("count") or 0)
    return spend


def _spend_summary(fdir: Path) -> dict:
    """The C-4 roll-ups plus the unreported list, for display and the report."""
    state = _load_json(fdir / "state.json")
    spend = state.get("spend")
    if not isinstance(spend, dict):
        spend = {}
    unreported = _unreported_dispatches(fdir)
    summary = _dispatch_summary(fdir)
    # Overlaid on a COPY of what state.json holds: this is a read, and a reader
    # that mutated the document it read would make every display call a write.
    spend = _overlay_unreported(json.loads(json.dumps(spend)), summary)
    return {
        "by_phase": spend["by_phase"],
        "by_cycle": spend["by_cycle"],
        "total": spend["total"],
        # The LIST stays row-shaped — a reader wants to see the stream agent
        # under each cycle it was missed in — while the COUNT is the pair count
        # the F6 report publishes. D-162: they are different axes, and the
        # count is the one both surfaces state.
        "unreported_dispatches": unreported,
        "unreported_count": int(summary.get("count") or 0),
        "unreported_rows": len(unreported),
        # D-189 — THE OTHER HALF OF THE SAME RECONCILIATION.
        #
        # `unreported_dispatches` names dispatches with no spend; this names
        # spend with no dispatch. Without it, a single typo'd Foundry-Spend
        # renders as "over 1 reported agent(s)" beside "Unreported: 1
        # casting-1@F3" — two agents on the display for what was one dispatch,
        # with nothing on the wire saying which of the two is a phantom. The
        # count itself is deliberately NOT filtered (see `foundry_record_spend`'s
        # docstring); what changes is that the display can say which agents the
        # count could not match, so the phantom never passes as an attributed
        # one.
        "unmatched_agents": sorted(
            {
                str(r.get("agent", ""))
                for r in _spend_ledger_rows(fdir)
                if r.get("agent")
            }
            - _dispatched_agent_ids(fdir)
        ),
    }


def foundry_record_spend(
    agent: str,
    phase: str,
    tokens: int,
    duration_ms: int,
    cycle: int | None = None,
    project_root: str = ".",
) -> dict:
    """Record one agent's token and time cost (CT-013 / FR-021 / FR-037).

    Appends a line to `foundry-archive/{run}/spend.jsonl` and updates the
    per-phase, per-cycle and run-total roll-ups in `state.json`.

    THE CYCLE BUCKET IS KEYED BY THE SERVER COUNTER (FR-037). A caller may pass
    ``cycle`` and it is recorded as the claim, but the bucket key is
    `_current_cycle` — the same authority every other record in this run is
    stamped from. Rolling cost up under a lead-asserted cycle is how D-119's
    class of divergence starts, and a cost report that disagrees with the cycle
    ledger about which cycle a run was in is worse than no cost report.

    TOKENS AND SECONDS SIT SIDE BY SIDE in every bucket, because the two
    questions a lead actually asks — "what did this phase cost" and "how long
    did it take" — are asked together, and a roll-up that answers only one sends
    them back to a second artifact.

    NEVER REFUSES. A malformed count is coerced to 0 and recorded; a missing run
    is the only thing that returns an error, and that is a "there is nothing to
    record against", not a judgement about the numbers.

    AN UNKNOWN AGENT IS RECORDED AND COUNTED, WITH A WARNING (CT-013 / D-189).
    CT-013's errors cell reads "none; unknown agent is recorded with a warning",
    and RECORDED is the operative word: an agent id that matches no row in this
    run's dispatch record gets its `spend.jsonl` line, its tokens, its
    milliseconds and its place in every `agents` count, exactly like a
    recognised one. The tokens were really spent — only the attribution is in
    doubt — and a roll-up that silently dropped them would answer "what did this
    phase cost" with a number that is wrong in the other direction. The WARNING
    is the whole signal, and `_spend_summary` publishes `unmatched_agents` so
    the display can name such an agent rather than let it read as an attributed
    one. Two further reasons the count is not filtered here: `foundry_report`
    reads `agents` off this roll-up and cross-checks it against the ledger's
    distinct names, so a filter on one side manufactures a false disagreement on
    the other (D-038 / D-090); and an agent legitimately spawned outside
    Foundry-Spawn-Teammate — an ASSAY or TEMPER agent — is unmatched too, and it
    is a real agent whose cost belongs in the total.
    """
    fdir = get_run_dir(project_root)
    if not fdir or not fdir.exists():
        return {"error": "No active foundry run.",
                "hint": "Call Foundry-Init first, or foundry_init(resume='run-name')."}
    # The house guard every entry point in this module runs, and CT-013's "never
    # refuses" does not exempt it. That clause is about the NUMBERS — a
    # forgotten spend record, a zero, an unreported dispatch — none of which may
    # block anything. A run artifact that will not decode is a different
    # statement: this call is about to read and rewrite `state.json`, and
    # rolling spend into a document nobody can parse would either lose the
    # ledger or silently overwrite it. `test_every_orchestrator_entry_point_runs_
    # the_artifact_guard` derives this obligation from the module rather than
    # from a list, which is how the omission was caught.
    if (corrupt := _artifact_guard(fdir)):
        return corrupt

    # D-004 — A COERCION NOBODY IS TOLD ABOUT IS A SILENT MIS-ATTRIBUTION.
    #
    # CT-013's "none" errors column is load-bearing and stays: a forgotten or
    # fat-fingered spend record must never block a gate (FR-022). But "never
    # refuses" was implemented as "never says anything", and the two are not the
    # same. A lead who omits the agent name gets a row filed under "unknown", a
    # lead who pastes a token count with a comma in it gets a row reading 0, and
    # in both cases the response says `ok: True` and nothing else — so the cost
    # report is quietly wrong and the one person who could correct it has no
    # signal. Every coercion this function performs is now NAMED in the result,
    # as a warning that blocks nothing.
    #
    # Derived over the coerced fields rather than written per field, because the
    # cause is the absence of surfacing, not the absence of surfacing for
    # `agent`: a one-field fix ships the sibling defect on `phase` the same day.
    warnings: list[str] = []

    def _identity(value, field: str) -> str:
        named = str(value or "").strip()
        if not named:
            warnings.append(
                f"{field} was empty or missing, so this spend is recorded "
                f"against \"unknown\" — the row still counts toward the totals, "
                f"but it cannot be attributed. Re-record it with the {field} "
                f"named if you want the roll-up to be readable."
            )
            return "unknown"
        return named

    def _count(value, field: str) -> int:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            # `None` warns like any other non-number: both counts are REQUIRED
            # parameters with no default, so a None arriving here is a value the
            # caller got wrong, never a field they declined to fill in.
            warnings.append(
                f"{field}={value!r} is not a number, so it is recorded as 0. "
                f"The dispatch is still counted; only its {field} is lost."
            )
            return 0
        if value < 0:
            warnings.append(
                f"{field}={value!r} is negative, so it is recorded as 0."
            )
        return max(0, int(value))

    server_cycle = _current_cycle(fdir)
    # D-048: the LEDGER STORES A RUN PHASE ID. A lead typing the dispatch verb
    # they can see in `spawns.log` — `cast`, `grind` — used to have it recorded
    # verbatim, which grew a `by_phase` bucket keyed by a verb that is not a
    # phase and cleared no dispatch, because the dispatch side is keyed `F1` /
    # `F3`. Mapped through the constant this module owns rather than refused,
    # because CT-013 says this door never refuses and a rejected cost report is
    # a cost report nobody files twice. An unknown value is still recorded as
    # spelled: it is somebody's phase, and dropping it would lose the number.
    recorded_phase = _identity(phase, "phase")
    recorded_phase = DISPATCH_PHASE_TO_RUN_PHASE.get(recorded_phase, recorded_phase)

    # D-189 — CT-013's SIGNAL, FIRING ON CT-013's CONDITION.
    #
    # CT-013's errors cell is "none; unknown agent is recorded with a warning",
    # and the door warned on a DIFFERENT condition than the one the contract
    # names, so the one signal that catches a mistyped agent never fired.
    # Driven through server.call_tool on a synthetic run before this change:
    # Foundry-Spend(agent='casting-99-never-dispatched') returned ok True with
    # NO warnings key at all, and so did agent='who-is-this'. The only call that
    # produced a warning was agent='' — an EMPTY name, which `_identity` files
    # under the literal id "unknown". So the implemented condition was "the
    # caller named no agent" while CT-013's condition is "the agent is unknown",
    # and an agent name this run has never dispatched is exactly the latter and
    # passed in silence. End to end: a run with one real dispatch of casting-1,
    # then one typo — Foundry-Spend(agent='casting-l') — was accepted silently,
    # and the next Foundry-Next rendered "over 1 reported agent(s)" AND
    # "Unreported: 1 casting-1@F3" for what was ONE dispatch.
    #
    # THE TWO CONDITIONS STAY TWO WARNINGS. An empty name is a field the caller
    # left blank; an unrecognised name is a field the caller filled in wrongly.
    # They have different remedies — supply the name, versus correct it — so
    # folding them into one sentence would hand the lead the wrong instruction
    # half the time. `_identity` has already warned about the blank, and
    # "unknown" is by construction absent from every dispatch record, so this
    # check runs only on a name that was actually given.
    #
    # THE ROW IS STILL RECORDED AND STILL COUNTED, which is CT-013's own wording
    # — "unknown agent is RECORDED with a warning" — and not merely the
    # never-refuses clause. The tokens were really spent; only the attribution
    # is in doubt, and dropping the row would lose a real number to fix a naming
    # error. `spend.total.agents` therefore counts this agent like any other:
    # `foundry_report._read_spend` cross-checks that roll-up against the
    # ledger's DISTINCT NAMES and publishes a `disagreements` entry when the two
    # differ, so filtering here and not there would manufacture a permanent
    # false "stale roll-up" finding on every run carrying a typo — D-038 and
    # D-090's class, which is two derivations of one number. What the display
    # does instead is NAME the unmatched agents beside the count, so a phantom
    # is never read as an attributed agent (see `_spend_summary`).
    recorded_agent = _identity(agent, "agent")
    if recorded_agent != "unknown":
        known_agents = _dispatched_agent_ids(fdir)
        if recorded_agent not in known_agents:
            shown = sorted(known_agents)
            named = ", ".join(shown[:6]) + (
                f" (+{len(shown) - 6} more)" if len(shown) > 6 else ""
            )
            warnings.append(
                f"agent={recorded_agent!r} matches no dispatch this run "
                f"recorded, so this spend cannot be reconciled against any "
                f"agent. The dispatch record (spawns.log plus the F2 stream "
                f"roster) names "
                + (named if shown else "nothing yet")
                + ". The row is recorded and counted either way — re-record it "
                "under the dispatched id if this was a typo, and disregard "
                "this if the agent was spawned outside Foundry-Spawn-Teammate "
                "and Foundry-Cast-Wave."
            )

    row = {
        "agent": recorded_agent,
        "phase": recorded_phase,
        "cycle": server_cycle,
        "declared_cycle": cycle,
        "tokens": _count(tokens, "tokens"),
        "duration_ms": _count(duration_ms, "duration_ms"),
        "recorded_at": _now(),
    }

    try:
        with open(fdir / SPEND_LEDGER_FILENAME, "a", encoding="utf-8") as f:
            f.write(json.dumps(row) + "\n")
    except OSError as exc:
        # Still roll it up: a run whose ledger file cannot be appended to should
        # not also lose the totals. The problem is named in the result.
        row["ledger_problem"] = f"{type(exc).__name__}: {exc}"

    # D-038 — `agents` COUNTS AGENTS, AND AN AGENT THAT REPORTS TWICE IS ONE.
    #
    # This was `bucket["agents"] += 1`, once per CALL, while
    # `foundry_report` counts DISTINCT agent ids over the same ledger. Two
    # derivations of one number that disagree the moment a teammate reports its
    # spend twice — a re-dispatched GRIND teammate, or a lead correcting a
    # fat-fingered token count — with the orchestrator inflated and the report
    # not. The count is therefore taken over a SET, from the ledger the report
    # reads, so the two surfaces cannot drift apart again.
    #
    # Unioned with this call's own agent rather than read from the ledger
    # alone: the append above may have failed (`ledger_problem`), and the
    # documented property that a run whose ledger cannot be written still keeps
    # its totals has to hold for the agent count as well as for the tokens.
    ledger_rows = _spend_ledger_rows(fdir)

    def _distinct_agents(predicate) -> int:
        names = {
            str(r.get("agent", ""))
            for r in ledger_rows
            if r.get("agent") and predicate(r)
        }
        names.add(row["agent"])
        return len(names)

    with _document_transaction(fdir / "state.json") as state:
        spend = state.get("spend")
        if not isinstance(spend, dict):
            spend = {}
        for section in ("by_phase", "by_cycle"):
            if not isinstance(spend.get(section), dict):
                spend[section] = {}
        if not isinstance(spend.get("total"), dict):
            spend["total"] = _empty_spend_bucket()

        for bucket, agent_count in (
            (
                spend["by_phase"].setdefault(row["phase"], _empty_spend_bucket()),
                _distinct_agents(
                    lambda r: str(r.get("phase", "")) == row["phase"]
                ),
            ),
            (
                spend["by_cycle"].setdefault(
                    str(server_cycle), _empty_spend_bucket()
                ),
                _distinct_agents(lambda r: r.get("cycle") == server_cycle),
            ),
            (spend["total"], _distinct_agents(lambda _r: True)),
        ):
            for field in ("tokens", "duration_ms", "agents", "unreported"):
                if not isinstance(bucket.get(field), int) or isinstance(
                    bucket.get(field), bool
                ):
                    bucket[field] = 0
            bucket["tokens"] += row["tokens"]
            bucket["duration_ms"] += row["duration_ms"]
            bucket["agents"] = agent_count

        # D-031: the persisted document carries the unreported counts C-4 names,
        # refreshed from the dispatch record on every spend call, instead of the
        # permanent zero `_empty_spend_bucket` used to leave there.
        _overlay_unreported(spend, _dispatch_summary(fdir))

        state["spend"] = spend
        state["updated_at"] = _now()

    summary = _spend_summary(fdir)
    result = {
        "ok": True,
        "recorded": row,
        "by_phase": summary["by_phase"],
        "by_cycle": summary["by_cycle"],
        "total": summary["total"],
        "unreported_dispatches": summary["unreported_dispatches"],
        # D-179 — THIS DOOR PUBLISHED THE LIST AND NOT THE COUNT, SO ITS
        # DISPLAY HAD NOTHING TO READ AND DERIVED ONE.
        #
        # `_fmt_foundry_record_spend` rendered "N dispatch(es) still have no
        # spend record" from `len(unreported_dispatches)`, the per-cycle ROW
        # list, while `Foundry-Next` beside it published the PAIR count — one
        # question, one run, two numbers. Both keys come off the SAME
        # `_spend_summary` call already made above, so this adds a statement
        # and no second derivation: `unreported_count` is the pair count the
        # F6 report also publishes and `unreported_rows` is the row list's
        # length, named so the two axes are distinguishable rather than
        # confusable (D-162).
        "unreported_count": summary["unreported_count"],
        "unreported_rows": summary["unreported_rows"],
    }
    # D-004: present ONLY when something was coerced, so a clean call's result
    # carries no empty key for a reader to interpret, and `ok` stays True either
    # way — this is a notice, never a refusal.
    if warnings:
        result["warnings"] = warnings
    return result


def _note_fix_after_inspect_decision(fdir: Path, defect_id: str) -> None:
    """Mark the open INSPECT's recorded width as superseded by a fix (D-035).

    D-035 — A DELTA-SWEPT INSPECT COULD OPEN ASSAY.
    ----------------------------------------------
    `foundry_mark_defect_fixed` has no phase guard, so a fix landing while the
    run sits in F2 flips the blocking count to zero AFTER the width was already
    decided and the sweep already taken. `inspect_clean` then passes and ASSAY
    opens on a cycle whose sweep never covered the surface that fix changed —
    the one crossing GI-002 exists to make honest.

    Recorded rather than refused. The fix itself is legitimate work and
    refusing it would push the lead to fix the defect and not say so, which is
    strictly worse. What is not legitimate is CARRYING that cycle's decision
    forward as though it still described the tree, so the entry is stamped and
    `inspect_clean` refuses until a fresh `inspect_start` re-decides the width
    and re-sweeps at the new HEAD.

    A no-op outside F2: in F3 GRIND, which is where fixes normally land, the
    next `inspect_start` decides a width that already accounts for them.
    """
    state_path = fdir / "state.json"
    with _document_transaction(state_path) as state:
        if state.get("phase") != "F2":
            return
        modes = state.get("inspect_modes")
        if not isinstance(modes, list) or not modes:
            return
        current = modes[-1]
        if not isinstance(current, dict):
            return
        superseded = current.get("fixes_after_decision")
        if not isinstance(superseded, list):
            superseded = []
        if defect_id not in superseded:
            superseded.append(defect_id)
        current["fixes_after_decision"] = superseded
        state["inspect_modes"] = modes
        state["updated_at"] = _now()


def _record_inspect_mode(fdir: Path, entry: dict) -> None:
    """Append one decision to `state.json.inspect_modes` and mirror it (C-4/C-6).

    Used by the two PHASE-ENTRY transitions. `inspect_start` does the same work
    inline instead, because it already holds the state transaction open for the
    counter advance and opening a second one would reintroduce exactly the
    read-modify-write window D-103 closed.

    D-070: the F5 entry mirrors into its own sub-bucket, because the counter
    does not advance entering F5 and a flat write would overwrite the preceding
    INSPECT's row. `state.json.inspect_modes` is append-only and already kept
    both; the roll-up now does too.
    """
    with _document_transaction(fdir / "state.json") as state:
        modes = state.get("inspect_modes")
        if not isinstance(modes, list):
            modes = []
        modes.append(entry)
        state["inspect_modes"] = modes
        state["updated_at"] = _now()
    _record_cycle_rollup(
        fdir,
        entry["cycle"],
        sub=_rollup_sub_for(entry),
        inspect_mode=entry["mode"],
        inspect_rule=entry["rule"],
        stream_scope=entry["stream_scope"],
    )


def _rollup_sub_for(entry: dict) -> str:
    """The `cycles[<cycle>]` sub-bucket one decision is mirrored under (D-070).

    Empty — a flat write — for every crossing that OWNS its cycle number: the
    `cast` entry (the run's first INSPECT) and `inspect_start` (which advanced
    the counter for the INSPECT it is opening). Only the F5 entry shares a
    cycle number with a decision already recorded, so only it nests.
    """
    return (
        TEMPER_ENTRY_ROLLUP_KEY if entry.get("decided_by") == "temper" else ""
    )


def _sweep_evidence_at_boundary(
    fdir: Path, project_root: str, entry: dict, *, full: bool
) -> dict:
    """Re-execute the in-scope evidence corpus at HEAD (GI-002 / ST-005).

    Returns ``{"ok": bool, "record": {...}, "mismatches": [...], "error": str}``
    where ``record`` is the C-6 `evidence_sweep` object the cycle roll-up
    carries.

    GI-002 IS A STATEMENT ABOUT WHO SWEEPS. "The SERVER sweeps at the boundary
    and refuses on mismatch" — not a teammate reporting that it swept, not a
    lead running a shell loop over `evidence/`. Both of those are claims; this
    is a measurement, taken at the one crossing where HEAD is the tree every
    later gate will judge.

    SCOPE IS DELTA BY DEFAULT. `select_sweep_scope` returns the logs whose
    casting's key_files intersect the diff plus any log whose own
    `# evidence-cmd:` references a touched file, and an empty list is a
    COMPLETE answer — a GRIND that touched nothing in scope re-executes zero
    logs, spawns no worktree and no subprocess (AC-014). The whole corpus is
    swept whenever a FULL rule fired, which by ST-006 includes every INSPECT
    before ASSAY, NYQUIST or DONE.
    """
    from foundry_mcp.tools.evidence import select_sweep_scope, sweep_evidence_at_head

    # D-117 — AN ENTRY WITH NO MODE SWEEPS EVERYTHING.
    #
    # The scope is DELTA by default and delta is a claim: "only these logs can
    # have been invalidated by this GRIND". That claim rests on the width
    # decision, so an entry carrying no `mode` — the shape every D-117 door was
    # admitting as full width — must not also narrow the sweep. The honest
    # reading of an unknown width is the whole corpus, which is the same reading
    # `_decide_inspect_mode` gives an uncomputable diff.
    if not entry.get("mode"):
        full = True

    manifest = _load_json(fdir / "castings" / "manifest.json")
    evidence_dir = Path(project_root) / "evidence"
    logs = select_sweep_scope(
        manifest=manifest,
        evidence_dir=evidence_dir,
        touched_files=list(entry.get("touched_files") or []),
        full=full,
    )
    outcome = sweep_evidence_at_head(
        project_root=Path(project_root), run_dir=fdir, logs=logs
    )
    record = {
        "scope": "full" if full else "delta",
        # D-149 — HOW MANY LOGS WERE IN SCOPE AT ALL, recorded beside how many
        # re-executed. At a FULL boundary these are the same number and that
        # number IS the corpus, which is the only way a reader can tell "the
        # whole corpus reproduced" from "there was no corpus": a run whose
        # `evidence/` has been deleted sweeps zero logs and passes on nothing,
        # and `ok` alone says the same word for both.
        "corpus_size": len(logs),
        "logs_reexecuted": [str(p) for p in outcome.get("logs_reexecuted", [])],
        # CT-007 — THE PER-LOG COLUMN, CARRIED THROUGH TO THE ARTIFACT.
        #
        # "sweep result recorded per log with scope (delta or full) and elapsed
        # seconds" is one requirement with two halves, and this wrapper used to
        # land only the first. `sweep_evidence_at_head` computed `per_log` — a
        # row for EVERY log in scope, matched or not, each with its own elapsed
        # seconds — and this function copied six sibling fields and dropped it,
        # so the column reached neither `stream-rollup.json`, nor the transition
        # result, nor the report. Driven (D-044): the persisted record's keys
        # were exactly ['elapsed_seconds', 'logs_reexecuted', 'mismatches',
        # 'pool_size', 'scope', 'swept_at'] with `elapsed_seconds` a single run
        # total, while one layer down the producer had the per-log rows in hand.
        # `test_evidence.py` asserted against the producer directly, which is
        # how a whole-suite pass sat on top of the gap.
        #
        # Copied field by field rather than passed through whole, for the same
        # reason `mismatches` is: this is the C-6 document shape and a producer
        # that grows a field does not silently widen a persisted artifact.
        "per_log": [
            {
                "log": row.get("log", ""),
                "elapsed_seconds": row.get("elapsed_seconds", 0.0),
                "matched": bool(row.get("matched")),
                "exit_code": row.get("exit_code"),
                "failure_token": row.get("failure_token"),
            }
            for row in outcome.get("per_log", [])
            if isinstance(row, dict)
        ],
        "mismatches": [
            {"log": m.get("log", ""), "reason": m.get("reason", "")}
            for m in outcome.get("mismatches", [])
        ],
        "elapsed_seconds": outcome.get("elapsed_seconds", 0.0),
        "pool_size": outcome.get("pool_size", 0),
        "swept_at": _now(),
    }
    return {
        "ok": bool(outcome.get("ok")),
        "record": record,
        "mismatches": outcome.get("mismatches", []),
        "error": outcome.get("error") or "",
    }


#: D-133 — where the whole-corpus sweep taken at a TERMINAL boundary is
#: remembered, keyed by the HEAD it was taken at. D-149 adds the durable
#: `last_full_pass` entry, which survives the memo's per-HEAD rewrites.
TERMINAL_SWEEP_FILENAME = ".evidence-swept-at-head.json"

#: D-149 — the token the DONE / NYQUIST_DONE doors refuse a stripped corpus
#: with. Named in `commands/start.md`'s F6 step and pinned there by
#: `tests/test_lead_prose.py`, so the lead reads the same word in the guidance
#: and in the refusal.
EVIDENCE_STRIPPED_TOKEN = "EVIDENCE_CORPUS_STRIPPED_BEFORE_SWEEP"


def _evidence_corpus_existed(fdir: Path, project_root: str) -> bool:
    """Did this run ever have a committed evidence corpus?

    D-149's discriminator between the two ways a terminal whole-corpus sweep
    can cover zero logs: a run that committed no evidence at all — honest,
    there is nothing to sweep and nothing to have stripped — and a run whose
    committed corpus was DELETED before this door was asked. `ok` says the same
    word for both, and only the second is a defect.

    TWO INDEPENDENT SOURCES, because neither alone answers the driven case.

    GIT IS THE AUTHORITY. `git rev-list -1 HEAD -- evidence/` names the last
    commit that touched the path, and the strip commit ITSELF touches it — it
    is a deletion of tracked files. So a non-empty answer means the corpus was
    tracked at some point on this history, whether or not it is in the tree
    now, which is exactly the question. This half is what catches a lead who
    strips BEFORE asking any terminal door, where the run directory has no
    record that a corpus ever existed.

    THE RUN'S OWN RECORDS answer where git cannot — no repository, a shallow
    clone, a run whose evidence lives outside this tree. `stream-rollup.json`
    carries every INSPECT boundary's sweep (recursively: the rollup nests
    records under per-cycle sub-keys such as `temper_entry` and
    `nyquist_entry`), and the terminal marker carries `corpus_seen`, the
    high-water mark a terminal sweep records whether or not it passed.
    """
    import subprocess

    try:
        rev = subprocess.run(
            ["git", "-C", project_root, "rev-list", "-1", "HEAD", "--", "evidence/"],
            capture_output=True, text=True, timeout=10,
        )
        if rev.returncode == 0 and rev.stdout.strip():
            return True
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        pass

    def _walk(node: object, depth: int = 0) -> bool:
        if depth > 6 or not isinstance(node, dict):
            return False
        sweep = node.get("evidence_sweep")
        if isinstance(sweep, dict):
            if sweep.get("logs_reexecuted") or int(sweep.get("corpus_size") or 0) > 0:
                return True
        return any(_walk(child, depth + 1) for child in node.values())

    if _walk(_load_json(fdir / ROLLUP_FILENAME)):
        return True
    memo = _load_json(fdir / TERMINAL_SWEEP_FILENAME)
    if not isinstance(memo, dict):
        return False
    # `corpus_seen` as well as `last_full_pass`, because the driven case is a
    # corpus that MISMATCHED and was then stripped: no pass was ever recorded,
    # and reading that absence as "there was never a corpus" would let exactly
    # the failing run through.
    return bool(memo.get("last_full_pass") or memo.get("corpus_seen"))


def _terminal_evidence_sweep(fdir: Path, project_root: str) -> dict:
    """GI-002's whole-corpus sweep at the NYQUIST and DONE boundaries.

    Returns ``{"ok", "record", "mismatches", "error", "head", "cached"}``.

    D-133 — "BEFORE ASSAY/NYQUIST/DONE" WAS ONE BOUNDARY OF THREE.
    -------------------------------------------------------------
    GI-002 (Locked) names the whole corpus as the sweep scope "when the FULL
    rule fires or BEFORE ASSAY/NYQUIST/DONE". Only the ASSAY path was covered,
    and it was covered indirectly — through the `final_gate` INSPECT that
    precedes it. The terminal doors swept nothing, and `fixes_after_decision`
    (the D-035 stamp that catches a fix landing mid-INSPECT) is read only by
    `inspect_clean`, which a run reaching NYQUIST from F5 never calls again.

    Driven: a run at F5 with the temper entry recorded and swept; a commit
    during F5 changed a file `evidence/casting-2-beta.log`'s command reads, so
    that log no longer reproduces; `_sweep_evidence_at_boundary(full=True)`
    returned ok False naming it — and `Foundry-Phase('nyquist')` then returned
    ok True, phase F5.5, and `_done_preconditions`' checklist (report_generated,
    escalated_classes_cleared, run_not_halted, spec_requirements_parsed,
    all_verified, zero_blocking_defects, no_active_teams, verdict_coverage)
    carried no evidence rung at all. A run reached DONE with its committed
    corpus never re-executed over the fixes F5 and F5.5 landed. The gap was
    flagged in concerns.md at GRIND cycle 2 and no ruling closed it.

    KEYED ON HEAD, AND MEMOISED, so the GATE and the TRANSITION can both ask.
    `_done_preconditions` is ONE evaluation with two callers by design — the
    D-037 discipline that the transition and the gate cannot disagree about
    what "done" means — and an evidence rung present in only one of them would
    be that drift restored. But a whole-corpus re-execution is minutes, and
    `Foundry-Gate('done')` then `Foundry-Phase('done')` would pay it twice.
    So the sweep is taken once per HEAD and the result is written to
    ``.evidence-swept-at-head.json``; a later caller at the SAME HEAD reads the
    verdict back instead of re-running it. That is not a weaker claim: the
    corpus really was re-executed at exactly this tree. Any commit — which is
    what a fix landing in F5 or F5.5 is — moves HEAD and invalidates the
    memo, which is the case the defect was filed on.

    A HEAD that cannot be read is not a cache key, so nothing is memoised and
    the sweep runs; a sweep that could not RUN is not a sweep that passed, and
    both are reported through the same `ok` the callers refuse on.

    D-149 — AND THE PASS IS RECORDED AGAINST THE COMMIT THAT STILL CARRIED THE
    CORPUS, BECAUSE THE F6 STEP DELETES IT.
    -------------------------------------------------------------------------
    `commands/start.md` mandates, verbatim, `git rm -r evidence/ && git commit
    -m "chore(foundry): strip consumed run evidence" -- evidence/` as an F6
    step, and this repo's own history carries that commit. Driven at cycle 8 on
    a run at F5.5 with one committed log whose command no longer reproduced:
    BEFORE the strip this returned ok False naming
    `evidence/casting-1-handler.log` and `Foundry-Phase('done')` was refused;
    AFTER the identical strip the same door returned ok True, scope full,
    `logs_reexecuted` [], `mismatches` [] — because `select_sweep_scope` reads
    the evidence directory in the TREE, and a directory that is gone yields no
    logs. The identical regression refused DONE before the strip and passed
    after it, so GI-002's terminal sweep passed over nothing on the guided path.

    `sweep_evidence_at_head` cannot tell "zero logs because the DELTA diff
    touched nothing" from "zero logs because the corpus was deleted", and at a
    `full=True` terminal boundary only the second is possible. So this function
    records `last_full_pass` — head, corpus size and timestamp — every time a
    whole-corpus sweep PASSES over a non-empty corpus, and carries it forward
    across the per-HEAD memo rewrites so the strip commit cannot erase it. The
    door then has three distinguishable states, which `_done_preconditions`
    refuses on (see its rung):

      * corpus present at HEAD          -> swept now, logs=N;
      * corpus absent, pass recorded    -> passes ON THE RECORDED PRE-STRIP
                                           PASS, naming the commit it swept;
      * corpus absent, no pass recorded -> refused,
                                           EVIDENCE_CORPUS_STRIPPED_BEFORE_SWEEP.

    `commands/start.md` documents that order — Foundry-Gate(phase='done') and
    only THEN the strip — and `tests/test_lead_prose.py#
    test_the_f6_sequence_sweeps_before_it_strips` fails if the two ever swap.
    """
    import subprocess

    head = ""
    try:
        rev = subprocess.run(
            ["git", "-C", project_root, "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=5,
        )
        if rev.returncode == 0:
            head = rev.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        head = ""

    memo_path = fdir / TERMINAL_SWEEP_FILENAME
    memo = _load_json(memo_path)
    if not isinstance(memo, dict):
        memo = {}
    # D-149: the durable half of the marker, read before the memo can be
    # replaced and carried onto every result so the callers never re-read it.
    prior_pass = memo.get("last_full_pass")
    if not isinstance(prior_pass, dict):
        prior_pass = None
    # The high-water mark: the largest corpus a terminal sweep has SELECTED in
    # this run, whether or not it passed. `last_full_pass` alone cannot answer
    # "did this run have a corpus", because the driven case is a corpus that
    # MISMATCHED and was then stripped — no pass was ever recorded, and reading
    # its absence as "there was never a corpus" is the defect one step over.
    corpus_seen = memo.get("corpus_seen")
    if not isinstance(corpus_seen, dict):
        corpus_seen = None
    if head:
        if memo.get("head") == head and memo.get("ok"):
            return {
                "ok": True,
                "record": memo.get("record") or {},
                "mismatches": [],
                "error": "",
                "head": head,
                "cached": True,
                "last_full_pass": prior_pass,
            }

    # `entry` carries no mode on purpose: `_sweep_evidence_at_boundary` reads an
    # entry with no `mode` as "sweep everything", which is the scope this
    # boundary owes anyway, and there is no width decision to invent here.
    sweep = _sweep_evidence_at_boundary(fdir, project_root, {}, full=True)
    corpus_size = int(sweep["record"].get("corpus_size") or 0)
    if corpus_size > 0:
        # Seen, whatever the verdict.
        corpus_seen = {
            "head": head,
            "corpus_size": max(
                corpus_size, int((corpus_seen or {}).get("corpus_size") or 0)
            ),
            "seen_at": _now(),
        }
        # D-149: a PASS over a NON-EMPTY corpus is what earns the durable
        # record the strip then spends. A pass over nothing earns nothing —
        # that is the whole distinction.
        if head and sweep["ok"]:
            prior_pass = {
                "head": head,
                "corpus_size": corpus_size,
                "swept_at": _now(),
            }
    result = {
        "ok": sweep["ok"],
        "record": sweep["record"],
        "mismatches": sweep["mismatches"],
        "error": sweep["error"],
        "head": head,
        "cached": False,
        "last_full_pass": prior_pass,
        "corpus_seen": corpus_seen,
    }
    # Written whenever there is anything durable to keep, not only on a pass:
    # the memo half is keyed on HEAD and the strip commit MOVES HEAD, so a
    # record that lived only in the replaced document would be deleted by the
    # very commit it exists to survive (D-149).
    memo_out: dict = {}
    if head and sweep["ok"]:
        memo_out = {
            "head": head,
            "ok": True,
            "record": sweep["record"],
            "swept_at": _now(),
        }
    if prior_pass is not None:
        memo_out["last_full_pass"] = prior_pass
    if corpus_seen is not None:
        memo_out["corpus_seen"] = corpus_seen
    if memo_out:
        _save_json(memo_path, memo_out)
    return result


def _terminal_evidence_state(fdir: Path, project_root: str) -> dict:
    """GI-002's terminal evidence rung, evaluated ONCE for every crossing.

    Returns the sweep result plus the three-state discrimination D-149
    established: ``{"evidence", "record", "logs_reexecuted", "corpus_size",
    "prior_pass", "stripped", "ok"}``.

      * corpus present at HEAD          -> swept now, ok is the sweep's verdict;
      * corpus absent, pass recorded    -> ok, ON THE RECORDED PRE-STRIP PASS;
      * corpus absent, no pass recorded -> not ok, `stripped` is True and the
                                           refusal names
                                           EVIDENCE_CORPUS_STRIPPED_BEFORE_SWEEP.

    D-159 — THE RUNG WAS APPLIED TO TWO CROSSINGS OF THE THREE GI-002 NAMES.
    -----------------------------------------------------------------------
    D-149 gave `_done_preconditions` the three states, which covers `done`,
    `nyquist_done` and `Foundry-Gate('done')` because all three read that one
    evaluation. The `nyquist` branch — the F5 -> F5.5 entry, and the ONE
    boundary GI-002 names by the word NYQUIST — calls `_terminal_evidence_sweep`
    itself and refused on `ok` alone, so it kept the pre-D-149 rule.

    Driven through the real door on a run at F5 with one committed log
    `evidence/casting-1-handler.log` whose command no longer reproduced, then
    the F6 strip `commands/start.md` mandates verbatim (`git rm -r evidence/ &&
    git commit -m 'chore(foundry): strip consumed run evidence' -- evidence/`):
    BEFORE the strip `Foundry-Phase(phase='nyquist')` was refused naming the
    log; AFTER the identical strip the same call returned ok True, phase F5.5,
    `evidence_sweep {scope: full, corpus_size: 0, logs_reexecuted: [],
    mismatches: []}` — a whole-corpus sweep passing over nothing, with no
    `last_full_pass` ever recorded because the corpus had mismatched and so
    never earned one. On the same stripped run `nyquist_done` and `done` both
    refused, naming the token. A --nyquist run had a second route around the
    rung: enter F5.5 through the door that did not apply it.

    Stated as ONE function with three callers rather than as a second copy in
    the `nyquist` branch, for the reason `_done_preconditions` itself exists:
    a terminal evidence rule each door evaluates for itself is a rule each door
    can evaluate differently, which is precisely how this crossing kept the old
    one through a whole-suite pass.
    """
    evidence = _terminal_evidence_sweep(fdir, project_root)
    record = evidence.get("record") or {}
    prior_pass = evidence.get("last_full_pass")
    prior_pass = prior_pass if isinstance(prior_pass, dict) else None
    corpus_size = int(record.get("corpus_size") or 0)
    stripped = (
        bool(evidence["ok"])
        and corpus_size == 0
        and prior_pass is None
        and _evidence_corpus_existed(fdir, project_root)
    )
    return {
        "evidence": evidence,
        "record": record,
        "logs_reexecuted": len(record.get("logs_reexecuted") or []),
        "corpus_size": corpus_size,
        "prior_pass": prior_pass,
        "stripped": stripped,
        "ok": bool(evidence["ok"]) and not stripped,
    }


def _stripped_corpus_reason(state: dict) -> tuple[str, str]:
    """The (reason, hint) pair a stripped corpus earns — worded ONCE (D-159).

    Every terminal crossing says the same words, because they are refusing the
    same fact. `_done_preconditions` uses the pair as its `reason` / `hint`;
    a transition wraps the reason in its own "Cannot <door> — " prefix.
    """
    head = (state["evidence"].get("head") or "unknown")[:8]
    return (
        (
            f"{EVIDENCE_STRIPPED_TOKEN}: the committed evidence corpus is not "
            f"present at HEAD ({head}) and this run recorded no whole-corpus "
            "sweep pass at a commit that carried it"
        ),
        (
            "Sweep first, strip second. Restore evidence/ (git revert the strip "
            "commit, or git checkout <pre-strip commit> -- evidence/ and commit "
            "it), call Foundry-Gate(phase='done') so the whole corpus "
            "re-executes and the pass is recorded in "
            f"{TERMINAL_SWEEP_FILENAME} under last_full_pass, and only THEN "
            "strip. A sweep over a corpus that is no longer there proves "
            "nothing, and passing on it is how a log that stopped reproducing "
            "during F5 or F5.5 reaches DONE unread."
        ),
    )


def _terminal_evidence_refusal(state: dict, door: str) -> dict | None:
    """The refusal a terminal crossing owes, or None when the rung passes.

    ``door`` is the phrase that follows "Cannot " — "enter NYQUIST", "mark the
    run DONE". One entry point for both failing states so no crossing can adopt
    the mismatch half and miss the stripped half, which is the D-159 shape.
    """
    if state["ok"]:
        return None
    if state["stripped"]:
        reason, hint = _stripped_corpus_reason(state)
        return {
            "error": f"Cannot {door} — {reason}",
            "hint": hint,
            "token": EVIDENCE_STRIPPED_TOKEN,
            "evidence_sweep": state["record"],
        }
    return _terminal_sweep_refusal(state["evidence"], door)


def _terminal_sweep_refusal(sweep: dict, door: str) -> dict:
    """The named refusal a mismatched TERMINAL sweep produces (GI-002/CT-007).

    Names each log, exactly as `_sweep_refusal` does at the INSPECT boundaries,
    and names the door it refused so the lead knows which call to re-make.
    """
    if sweep["error"]:
        return {
            "error": (
                f"Cannot {door} — the evidence sweep could not run: "
                f"{sweep['error']}"
            ),
            "hint": (
                "A sweep that could not run is not a sweep that passed. Fix the "
                "condition named above and retry."
            ),
            "evidence_sweep": sweep["record"],
        }
    named = [m.get("log", "?") for m in sweep["mismatches"]]
    return {
        "error": (
            f"Cannot {door} — {len(named)} committed evidence log(s) no longer "
            f"reproduce at HEAD: {', '.join(named)}"
        ),
        "hint": (
            "GI-002 sweeps the WHOLE corpus before ASSAY, NYQUIST and DONE. "
            "Each log's `# evidence-cmd:` was re-executed in a detached "
            "worktree at HEAD and its output no longer matches what was "
            "committed — most often because a fix landed after the log was "
            "captured. Either the behaviour regressed, or the owning casting "
            "must re-capture the log; then retry."
        ),
        "mismatches": sweep["mismatches"],
        "evidence_sweep": sweep["record"],
    }


def _sweep_refusal(sweep: dict, cycle: int, token: str = "inspect_start") -> dict:
    """The named refusal a mismatched evidence sweep produces (CT-007).

    NAMES EACH LOG. A sweep that says "something no longer reproduces" sends
    the lead to re-run the whole corpus by hand to find out which; the sweep
    already knows, and CT-007 says to say so.

    Also names the counter it did NOT advance, because the operator's next
    question after a refused transition is always whether the run moved.

    ``token`` is the phase token that was refused, because D-014 gave this
    refusal three callers rather than one: every transition that OPENS an
    INSPECT sweeps, and a hint telling a lead to re-call `inspect_start` after
    a refused `temper` names a transition that is not the one it was making.
    """
    if sweep["error"]:
        return {
            "error": (
                f"Cannot cross into INSPECT — the evidence sweep could not run: "
                f"{sweep['error']}"
            ),
            "hint": (
                "A sweep that could not run is not a sweep that passed. Fix the "
                f"condition named above and re-call Foundry-Phase"
                f"(phase='{token}')."
            ),
            "cycle": cycle,
            "evidence_sweep": sweep["record"],
        }
    named = [m.get("log", "?") for m in sweep["mismatches"]]
    return {
        "error": (
            f"Cannot cross into INSPECT — {len(named)} committed evidence log(s) "
            f"no longer reproduce at HEAD: {', '.join(named)}"
        ),
        "hint": (
            "Each log's `# evidence-cmd:` was re-executed in a detached "
            "worktree at HEAD and its output no longer matches what was "
            "committed. Either the behaviour it demonstrates regressed — fix "
            "that — or the log is stale and its owning casting must re-capture "
            f"it. The phase has NOT advanced and the cycle counter has NOT "
            f"moved; re-call Foundry-Phase(phase='{token}') when it reproduces."
        ),
        "cycle": cycle,
        "mismatches": sweep["mismatches"],
        "evidence_sweep": sweep["record"],
    }


def _current_inspect_mode(fdir: Path, cycle: int | None = None) -> dict | None:
    """The decision the last INSPECT-opening transition recorded, or None.

    THE ONLY READ. GI-008 and GI-009 both name a lazily-computed mode as the
    violation, so every consumer — the streams-complete check, the guidance
    engine, the status display — comes through here and none of them re-derives
    anything. None means a run whose INSPECT has not been opened since this
    landed, and each caller degrades to its pre-change behaviour rather than
    guessing a width.

    ``cycle`` is WHICH crossing is being asked about, and it defaults to the
    server counter (D-216). Almost every caller is asking "what width is the
    INSPECT this run is in", which is the counter's crossing and nobody's
    judgement call, so it passes nothing. The two NARROW readers —
    `_recorded_stream_scope` and `_recorded_prove_roster` — are asking about a
    named cycle instead, because their callers measure one cycle's coverage
    against that cycle's roster, and they pass it. Making the subject an
    argument rather than a second reader is what keeps this THE only read: the
    alternative was a helper that resolves the entry a different way, which is
    the two-readers-disagree shape D-117 was filed to close.
    """
    modes = _load_json(fdir / "state.json").get("inspect_modes")
    if not isinstance(modes, list) or not modes:
        return None
    # D-212 (same class as the escalation-status read) — THE WIDTH IS RESOLVED
    # AGAINST `INSPECT_MODES`, AND THE LAST ENTRY IS THE ONE THAT DECIDES.
    #
    # `inspect_modes` is append-only and the last entry IS the current
    # decision — `_note_fix_after_inspect_decision` stamps `fixes_after_decision`
    # onto that same entry, reading it the same way and testing neither the
    # vocabulary nor the cycle stamp itself. That stays inert because the stamp
    # is read back only HERE, at `inspect_clean`, which asks
    # `_unrecorded_width_problem` first: the states where a bare `modes[-1]` and
    # this function would disagree are exactly the states that door has already
    # refused. This walked BACKWARDS past any entry with a falsy `mode`, and tested that
    # `mode` for truthiness rather than for membership of the vocabulary that
    # spells it, so a hand-edited or foreign-written `"delta"` (lowercase) or
    # `"BOGUS"` was a recorded width. `_unrecorded_width_problem` then answered
    # None, and `inspect_clean`'s refusal — `recorded_mode.get("mode") ==
    # "DELTA"` — is false for such a value, so a narrow INSPECT closed and the
    # run reached F4.
    #
    # BOTH AXES. WHAT is compared: `INSPECT_MODES`, by membership, so the
    # vocabulary decides rather than the two literals typed beside it at five
    # doors. WHICH entry answers: the last one, so a malformed current decision
    # cannot be answered with an older cycle's valid one — walking back would
    # report cycle N-1's FULL as cycle N's width and PASS the ASSAY gate that
    # refuses today, which is a worse door than the one being closed.
    #
    # An unusable record reads as NO record, which is D-117's ruling ("an
    # unrecorded width is not full width") applied to the value that is present
    # and wrong rather than to the one that is absent: every door refuses
    # through `_unrecorded_width_problem`, naming the transition that records a
    # width. Every entry this module writes carries a member of the vocabulary,
    # so a well-formed archive reads exactly as before.
    entry = modes[-1]
    if not isinstance(entry, dict):
        return None
    if entry.get("mode") not in INSPECT_MODES:
        return None
    # D-216 — AND IT IS THE ENTRY STAMPED FOR **THIS** CYCLE.
    #
    # THE THIRD AXIS. D-212 settled WHICH entry answers (the last one) and WHAT
    # its mode is resolved against (`INSPECT_MODES`). It left WHICH CYCLE the
    # entry belongs to, and this returned the newest entry whatever crossing had
    # written it — so cycle 1's decision answered "what width is cycle 2" at
    # every door that decides on one.
    #
    # The module already held the rule twice, in the two helpers that read a
    # NARROW slice of a decision: `_recorded_stream_scope` and
    # `_recorded_prove_roster` both compare the entry's cycle against the cycle
    # being measured, and the first names the omission as GI-008's violation in
    # its own docstring — "a caller reading a scope off a decision made for
    # another cycle would be narrowing this one against a width nothing recorded
    # for it". The narrow helpers guarded it; the read they are built on did not.
    #
    # Driven through `server.call_tool` at F2 cycle 2 with a single
    # `{cycle: 1, mode: FULL, rule: first_of_phase}` entry and every stream
    # complete: `Foundry-Gate('assay')` PASSED carrying
    # `inspect_ran_at_full_width (mode=FULL rule=first_of_phase) ok=True`, an
    # assertion about cycle 2 answered by cycle 1's record. With a cycle-1 DELTA
    # entry, `_check_streams_complete` reported complete True over cycle 1's
    # roster — GI-008's named violation verbatim — and the F2->F2 widening arm
    # accepted the re-open and advanced the counter to 3, stamping a FULL
    # decision for a crossing whose own width nothing had recorded.
    #
    # EARLIER OR LATER, both. Every entry this module writes carries the counter
    # the crossing that wrote it holds (`cast` and `temper` stamp
    # `_current_cycle`; `inspect_start` stamps the counter it advanced inside the
    # same transaction), so a stamp ahead of the counter cannot arise on the live
    # path at all — which is why tolerating one is tolerating something no
    # transition produced. A stale stamp is D-117's ruling ("an unrecorded width
    # is not full width") applied to the value that belongs to a different
    # crossing: it reads as NO record, and every door refuses through
    # `_unrecorded_width_problem`.
    stamped = entry.get("cycle")
    if isinstance(stamped, bool) or not isinstance(stamped, int):
        return None
    if stamped != (_current_cycle(fdir) if cycle is None else cycle):
        return None
    return entry


#: The transitions GI-009 names as the ones that record a width, quoted in
#: every refusal that finds none, so the remedy is always a call the lead can
#: make rather than a fact about the archive.
_WIDTH_RECORDING_TRANSITIONS = (
    "Foundry-Phase(phase='cast') for the F2 entry, "
    "Foundry-Phase(phase='temper') for the F5 entry, or "
    "Foundry-Phase(phase='inspect_start') for a GRIND->INSPECT crossing"
)


def _inspect_mode_gap(fdir: Path, cycle: int) -> str:
    """Why the newest `inspect_modes` entry is not cycle `cycle`'s width.

    Diagnosis only — `_current_inspect_mode` is still the one place the question
    is DECIDED, and this is called only after it has already answered None. It
    exists because D-216's refusal reads very differently depending on which of
    the three ways an archive can fail to carry this INSPECT's width it hit, and
    "cycle 2 has no recorded width" on a run whose state.json visibly holds
    thirteen inspect_modes entries sends the lead hunting for a file that is
    right there. NFR-005: the one line a terminal prints has to say what was
    found, not only what was missing.
    """
    modes = _load_json(fdir / "state.json").get("inspect_modes")
    if not isinstance(modes, list) or not modes:
        return "nothing has recorded an inspect_modes entry for it"
    entry = modes[-1]
    if not isinstance(entry, dict) or entry.get("mode") not in INSPECT_MODES:
        return (
            "the newest inspect_modes entry records no width this server "
            f"spells — {', '.join(sorted(INSPECT_MODES))} are the only two"
        )
    stamped = entry.get("cycle")
    if isinstance(stamped, bool) or not isinstance(stamped, int):
        return "the newest inspect_modes entry carries no usable cycle stamp"
    return (
        f"the newest inspect_modes entry is stamped for cycle {stamped}, and a "
        f"width is a fact about ONE crossing — cycle {stamped}'s decision says "
        f"nothing about what cycle {cycle} was opened with"
    )


def _unrecorded_width_problem(fdir: Path) -> dict | None:
    """`{"reason", "hint"}` when this INSPECT has NO recorded width, else None.

    D-117 — AN UNRECORDED WIDTH IS NOT FULL WIDTH.
    ---------------------------------------------
    GI-009's named violation is "A first INSPECT of a phase with no recorded
    mode" and GI-008's is "a streams-complete check that reads a roster nothing
    recorded". Every consumer degraded PERMISSIVELY instead of refusing, and
    each degradation was individually defensible — "a resumed archive should get
    the pre-change behaviour" — while together they admitted a mode-less INSPECT
    as full width at every door at once:

      `_check_streams_complete` fell back to the pre-width roster
      trace/prove/test, so `research_audit` and `test01` were never required;
      `foundry_gate('assay')` tested `mode != "DELTA"`, which "unrecorded"
      passes; `inspect_clean`'s DELTA refusal tested `== "DELTA"`, which
      "unrecorded" also passes; and `_maybe_skip_trace`'s D-071 fence handled
      FULL and DELTA explicitly then fell through to the legacy
      `_trace_skip_check` and auto-stamped `.trace-complete`.

    Driven end to end through the shipped `Foundry-Init(resume=...)`, which
    reactivates any archive with whatever `state.json` it holds, on a
    legacy-shaped archive with the committed evidence deliberately stale at
    HEAD: streams-complete required only trace, prove and test and reported
    complete; `inspect_clean` returned ok and moved the run to F4; and
    `Foundry-Gate('assay')` PASSED carrying the checklist line
    `inspect_ran_at_full_width (mode=unrecorded rule=unrecorded) ok=True` — the
    assertion that is false. With a `.trace-clean-at` marker present TRACE never
    ran either. ASSAY opened having run PROVE and TEST only, over an evidence
    corpus no boundary sweep had ever re-executed.

    ONE PREDICATE, SIX CALLERS. The permissive fallbacks were six separate
    judgement calls in six functions, which is exactly how they came to agree on
    the wrong answer without any of them saying so. The refusal names the
    missing record AND the transition that writes it, because "there is no
    recorded width" is not an action.
    """
    if _current_inspect_mode(fdir) is not None:
        return None
    cycle = _current_cycle(fdir)
    return {
        "reason": (
            f"this INSPECT (cycle {cycle}) has no recorded width — "
            f"{_inspect_mode_gap(fdir, cycle)}, so the roster, the rule and the "
            "evidence sweep it was opened with are all unknown"
        ),
        "hint": (
            "An INSPECT is opened by the transition that decides and records "
            "its width (GI-009), and an unrecorded width is never read as FULL: "
            "a roster nothing recorded is not a roster that ran. Cross the "
            f"boundary that records one — {_WIDTH_RECORDING_TRANSITIONS} — "
            "which also sweeps the evidence corpus at HEAD, then run exactly "
            "the roster it names."
        ),
    }


def _check_streams_complete(project_root: str) -> dict:
    """Check if all required verification streams have completed for this cycle.

    Two behaviours land here.

    SIGHT (FR-020 / AC-025). ``sight`` used to be appended UNCONDITIONALLY
    whenever ``manifest.no_ui`` was false — which is the default — so a run with
    zero frontend files in scope still had to produce a sight marker it had no
    way to earn. That is the grand-vulture deadlock: a fully clean cycle-17
    INSPECT blocked on ``sight``. The requirement is now driven by the same
    ``_check_sight_required`` evidence the inspect gate already uses (do any
    casting key_files actually carry a UI extension?), which also collapses the
    ``no_ui`` divergence between state.json and castings/manifest.json — the
    flag is read from the manifest here and from state.json elsewhere, and
    foundry_init writes both. A UI run is unaffected: frontend files in scope
    still make sight required, and still make it BLOCKED when no url is set.

    COVERAGE (FR-014 / CT-003 / AC-020). The >=95% PROVE and TRACE thresholds
    are evaluated HERE, once per cycle, against the cycle's roll-up total —
    the one point where every tranche of a partially-delivered stream is in
    hand. A stream that recorded but fell short is reported in ``missing`` (so
    every existing caller keeps blocking on it) and detailed in ``shortfalls``.
    """
    fdir = get_run_dir(project_root)
    if not fdir:
        return {"complete": False, "missing": "all", "required": [], "shortfalls": []}
    manifest = fdir / "castings" / "manifest.json"

    # AC-017 / ST-006 / ST-007 / GI-008 — THE ROSTER IS READ, NOT RECOMPUTED.
    #
    # The transition that opened this INSPECT already decided which streams it
    # requires and at what scope, and recorded both. Re-deriving them here would
    # let the check disagree with the cycle that actually ran: the mode is a
    # function of a GRIND diff measured at the boundary, and by the time this is
    # called the tree has moved on. GI-008 names the shape outright — "a
    # streams-complete check that reads a roster nothing recorded".
    #
    # D-117 — A RUN WITH NO RECORDED DECISION IS INCOMPLETE, NOT PRE-CHANGE.
    #
    # This fell back to the roster the function required before the width
    # existed — trace/prove/test — and called that "the pre-change behaviour,
    # which is what a resumed archive should get". It is also, exactly,
    # GI-008's named violation: "a streams-complete check that reads a roster
    # nothing recorded". The fallback silently dropped `research_audit` and
    # `test01` from every mode-less INSPECT and reported `complete: True`, which
    # is how ASSAY came to open on a three-stream roster. See
    # `_unrecorded_width_problem` for the end-to-end drive.
    #
    # `missing` carries the sentinel `inspect_mode` so every existing caller —
    # each of which blocks on a non-empty `missing` — blocks here too without
    # being taught a new key, and `unrecorded_width` plus `hint` are there for
    # the callers that name the remedy.
    # SCOPED TO A RUN THAT IS ACTUALLY IN AN INSPECT. The width belongs to an
    # INSPECT, so a run that is in none has no INSPECT whose width could be
    # missing — and this function is also the plain "did these markers record
    # and clear the coverage threshold" query, which `tests/test_stream_rollup.py`
    # asks of a run that never entered F2. The two doors that matter, `Foundry-
    # Gate('assay')` and `inspect_clean`, ask `_unrecorded_width_problem`
    # themselves rather than inferring it from this result, so nothing rests on
    # the scope being wider than the phase.
    recorded = _current_inspect_mode(fdir)
    inspect_mode = ""
    inspect_rule = ""
    stream_scope: dict = {}
    in_inspect = _load_json(fdir / "state.json").get("phase") in ("F2", "F5")
    unrecorded = _unrecorded_width_problem(fdir) if in_inspect else None
    if unrecorded is not None:
        return {
            "complete": False,
            "missing": "inspect_mode",
            "required": [],
            "shortfalls": [],
            "inspect_mode": "",
            "inspect_rule": "",
            "stream_scope": {},
            "unrecorded_width": True,
            "reason": unrecorded["reason"],
            "hint": unrecorded["hint"],
        }
    if isinstance(recorded, dict) and isinstance(recorded.get("required_streams"), list):
        required = [s for s in recorded["required_streams"] if isinstance(s, str)]
        inspect_mode = recorded.get("mode", "")
        inspect_rule = recorded.get("rule", "")
        raw_scope = recorded.get("stream_scope")
        stream_scope = raw_scope if isinstance(raw_scope, dict) else {}
    else:
        # A recorded entry that carries a mode but no usable roster. The width
        # IS recorded, so this is not the D-117 hole; the roster is rebuilt from
        # the run's own manifest exactly as it was before the width existed.
        required = ["trace", "prove", "test"]

        url = ""
        if manifest.exists():
            url = _load_json(manifest).get("target_url", "")

        if _check_sight_required(project_root).get("required"):
            required.append("sight")

        if url:
            required.append("probe")

        inspect_mode = (recorded or {}).get("mode", "")
        inspect_rule = (recorded or {}).get("rule", "")

    missing = [s for s in required if not (fdir / _stream_marker(s)).exists()]

    cycle = _current_cycle(fdir)
    shortfalls = []
    for s in required:
        if s in missing:
            continue
        shortfall = _coverage_shortfall(fdir, project_root, s, cycle)
        if shortfall:
            shortfalls.append(shortfall)
            missing.append(s)

    return {
        "complete": len(missing) == 0,
        "missing": " ".join(missing),
        "required": required,
        "shortfalls": shortfalls,
        # Reported, never decided here (GI-008). Empty strings on a run with no
        # recorded decision, which is how a caller tells "this run predates the
        # width" from "this run is at FULL".
        "inspect_mode": inspect_mode,
        "inspect_rule": inspect_rule,
        "stream_scope": stream_scope,
    }


# --- Phase lifecycle markers ---


def _finalize_open_phase_entry(entry: dict, now: str) -> None:
    """If `entry` has started_at but no ended_at, stamp ended_at + duration."""
    if "started_at" in entry and "ended_at" not in entry:
        entry["ended_at"] = now
        try:
            start = datetime.fromisoformat(entry["started_at"])
            end = datetime.fromisoformat(now)
            delta = end - start
            mins = int(delta.total_seconds() // 60)
            secs = int(delta.total_seconds() % 60)
            entry["duration"] = f"{mins}m {secs}s"
        except (ValueError, KeyError):
            pass


def _update_phase(fdir: Path, new_phase: str) -> None:
    """Update state.json with the new phase. Tracks timing per phase.

    Closes EVERY still-open phase_times entry before opening the new one.
    Passive sub-phase stamping (see _stamp_subphase_transitions) opens
    F0.5/F0.9 based on file-state signals, so a single `prev_phase` close
    isn't sufficient — the F0 → F1 jump skips F0.5/F0.9 at the state-level
    even though those sub-phases did elapse in wall time.
    """
    state_path = fdir / "state.json"
    now = _now()

    with _document_transaction(state_path) as state:
        phase_times = state.get("phase_times", {})
        if not isinstance(phase_times, dict):
            phase_times = {}
        for entry in phase_times.values():
            _finalize_open_phase_entry(entry, now)

        phase_times[new_phase] = {"started_at": now}

        state["phase"] = new_phase
        state["updated_at"] = now
        state["phase_times"] = phase_times
        history = state.get("phase_history", [])
        if not isinstance(history, list):
            history = []
        history.append({"phase": new_phase, "entered_at": now})
        state["phase_history"] = history

        if new_phase == "F6":
            state["ended_at"] = now
            started = state.get("started_at", "")
            if started:
                try:
                    start = datetime.fromisoformat(started)
                    end = datetime.fromisoformat(now)
                    delta = end - start
                    hours = int(delta.total_seconds() // 3600)
                    mins = int((delta.total_seconds() % 3600) // 60)
                    secs = int(delta.total_seconds() % 60)
                    state["total_duration"] = f"{hours}h {mins}m {secs}s"
                except (ValueError, TypeError):
                    pass

    # P4 (ST-002): a real phase advance supersedes any pending gate-passed
    # guidance marker. Clear it so the next Foundry-Next emits the NEW phase's
    # fresh imperative rather than a stale "gate already passed" note.
    (fdir / GATE_PASSED_MARKER).unlink(missing_ok=True)


# CLOSED VOCABULARY — every phase token ``foundry_mark_phase_complete`` handles,
# in lifecycle order. The handler is the authority for this one (unlike the wire
# vocabularies, which come from schemas/vocab.py): a token means something only
# because a branch below implements it.
#
# server.py's Foundry-Phase enum is the second copy, and it had drifted in BOTH
# directions at once — advertising research_done / decompose_done /
# validate_done, which this handler has no branch for and refuses, while
# OMITTING inspect_start, the one token whose branch advances the cycle counter.
# The MCP SDK validates arguments against the advertised enum BEFORE dispatch,
# so that omission made the counter unable to leave 0 over MCP however this
# handler behaved.
#
# Adding a token here without a branch below (or vice versa, or without the
# schema entry) fails the drift guard in tests/test_orchestrator_gates.py, which
# derives the accepted set from this function's own AST and asserts all three
# copies are equal.
PHASE_TOKENS = (
    "start_cast",
    "cast",
    "inspect_start",
    "inspect_clean",
    "grind_start",
    "assay_fail",
    "temper",
    "nyquist",
    "nyquist_done",
    "done",
)


def foundry_mark_phase_complete(
    phase: str,
    project_root: str = ".",
) -> dict:
    """Mark a phase transition. Validates preconditions AND updates state.json.phase.

    D-067 \u2014 THE ORDERING TOKEN IS CONSUMED BY A TRANSITION THAT HAPPENED.
    --------------------------------------------------------------------
    This unlinked `.next-action-called` before evaluating ANY branch, so a
    REFUSED transition burned it, and every refusal whose hint says "fix this
    and re-call Foundry-Phase" named a call that was then refused for a second,
    different reason. Driven on the evidence sweep: broke a committed log,
    called `Foundry-Phase(inspect_start)`, got the correct refusal naming the
    log, repaired the log, and did exactly what the hint said -> "Must call
    Foundry-Next before phase transitions". `_sweep_refusal` carries a `token`
    parameter for no purpose other than naming the right transition to retry,
    so the remedy was engineered to be actionable and the token consumption
    made it not.

    The check stays where it was \u2014 a transition still requires that a
    Foundry-Next preceded it \u2014 and only the CONSUMPTION moves, to the one place
    that knows the transition succeeded. Keyed on `ok` rather than unlinked at
    each success return, because there are a dozen of those and the next branch
    added would forget one; an unknown-token refusal leaves the token in place
    too, which is right for the same reason (a typo is not a transition).
    """
    fdir = get_run_dir(project_root)
    if not fdir or not fdir.exists():
        return {"error": "No active foundry run"}
    if (corrupt := _artifact_guard(fdir)):
        return corrupt

    # ST-008 / CT-016 \u2014 ASKED BEFORE THE ORDERING TOKEN, AND THE ORDER MATTERS.
    #
    # The token handshake is a protocol precondition of a transition; the halt
    # is the fact that there are no more transitions. A halted run whose lead
    # called Foundry-Phase without a preceding Foundry-Next would otherwise be
    # told "Must call Foundry-Next before phase transitions" \u2014 an instruction to
    # go and arm a token for a call that can never succeed. This is the SAME
    # `_halted_refusal` the transition itself calls; one rule, one
    # implementation, and the second call site below is what holds if anything
    # ever reaches `_phase_transition` by another route.
    if (halted := _halted_refusal(fdir, f"Foundry-Phase(phase='{phase}')")) is not None:
        return halted

    nac = fdir / NEXT_ACTION_CALLED_MARKER
    if not nac.exists():
        return {
            "error": "Must call Foundry-Next before phase transitions",
            "hint": "Call Foundry-Next first \u2014 it shows status and guides you.",
        }

    result = _phase_transition(phase, project_root, fdir)
    if result.get("ok"):
        nac.unlink(missing_ok=True)
    return result


#: D-113 / D-114 / D-116 — the transition that actually applies from each phase
#: `inspect_start` is refused from. A refusal that only says "not from here"
#: leaves the lead to guess, and the guess that cost these three defects was
#: "call it anyway"; every entry below names a call the server accepts in that
#: phase.
_INSPECT_START_SOURCE_HINTS = {
    "F0": (
        "The run has not entered CAST. Call Foundry-Phase(phase='start_cast') "
        "to enter F1, build, then Foundry-Phase(phase='cast') — that is the "
        "transition that opens the run's FIRST INSPECT and records its width."
    ),
    "F1": (
        "CAST is still open. Call Foundry-Phase(phase='cast') when the wave is "
        "down — that transition enters F2, records FULL / first_of_phase and "
        "sweeps the evidence corpus. inspect_start would skip CAST entirely."
    ),
    "F4": (
        "The run is in ASSAY. Call Foundry-Phase(phase='assay_fail') to open a "
        "GRIND from an ASSAY rejection (that is the door that enters F3), then "
        "Foundry-Phase(phase='inspect_start') when the GRIND is done."
    ),
    "F5": (
        "The run is in TEMPER. Call Foundry-Phase(phase='grind_start') to open "
        "a GRIND on what TEMPER filed, then Foundry-Phase(phase='inspect_start') "
        "to close it. TEMPER's own INSPECT was opened and recorded by the "
        "Foundry-Phase(phase='temper') transition."
    ),
    "F5.5": (
        "The run is in NYQUIST. Call Foundry-Phase(phase='grind_start') to open "
        "a GRIND on what NYQUIST filed, then Foundry-Phase(phase='inspect_start') "
        "to close it."
    ),
    "F6": (
        "The run is DONE. There is no further INSPECT to open; start a new run "
        "rather than re-opening this one."
    ),
}


#: D-124 / D-125 — the source phases the OTHER TWO INSPECT-opening tokens are
#: accepted from, and the call that applies instead.
#:
#: GI-009 names three transitions that open an INSPECT — the F2 entry (`cast`),
#: the F5 entry (`temper`) and `inspect_start` — and the cycle-6 ruling gave a
#: source-phase precondition to exactly ONE of them. The other two kept
#: D-116's failure shape verbatim, one door over:
#:
#:   D-124  `cast` from F4 (ASSAY), cycle 2, with a `final_gate` decision
#:          already recorded for cycle 2: returned ok, phase F2, mode FULL,
#:          rule first_of_phase. Afterwards state.json carried a THIRD width
#:          decision stamped onto cycle 2, `.cast-baseline-sha` had been
#:          overwritten from the CAST baseline commit to HEAD — destroying the
#:          cycle-context baseline every GRIND prompt is built from — and the
#:          stream roll-up's cycle 2 row read `first_of_phase` where it had
#:          read `final_gate`. CT-009 records ONE decision per INSPECT-opening
#:          transition and FR-023 reports them per cycle; a run in ASSAY was
#:          pulled back into F2 with a fabricated first-of-phase record.
#:
#:   D-125  `temper` from F2, cycle 2, on a run whose recorded width was DELTA
#:          with zero verdicts: returned ok, phase F5, and stamped a SECOND
#:          decision (FULL / first_of_phase / decided_by temper) onto cycle 2
#:          beside the DELTA one. `Foundry-Gate('nyquist')` then passed and
#:          `Foundry-Phase('nyquist')` reached F5.5 — so the last INSPECT
#:          before NYQUIST ran at DELTA width, the widening re-open the cycle-3
#:          ruling requires never happened, and no verdict was ever written.
#:          FR-011 makes the INSPECT before ASSAY/NYQUIST/DONE FULL and US-004
#:          says every final gate still runs everything at full width; neither
#:          held, because the door never asked what phase the run was in.
#:
#: Stated as a TABLE consulted by one helper rather than as an arm inside each
#: branch, because "three transitions open an INSPECT and each guards its own
#: source phase" is precisely the three-copies shape that let two of the three
#: go unguarded for six cycles. `inspect_start` keeps its own inline guard: it
#: admits two phases with different meanings (F3 crossing, F2 widening) and
#: threads `widening` into the decision, which a table cannot express.
#:
#: D-164 — AND THE TWO TERMINAL TOKENS WERE NEVER BROUGHT UNDER IT.
#:
#: ST-010's from-state is "F5.5 or F5 complete", and neither `done` nor
#: `nyquist_done` read `state.phase` at all: both called `_done_preconditions`,
#: whose checklist (report_generated, escalated_classes_cleared, run_not_halted,
#: spec_requirements_parsed, all_verified, zero_blocking_defects,
#: no_active_teams, verdict_coverage, evidence_reproduces_at_head) has no
#: source-phase entry. Driven twice at the real door:
#:
#:   (1) a run at F4 with `temper` and `nyquist` both set, 172/172 VERIFIED,
#:       zero open defects and a generated report: `Foundry-Phase('done')`
#:       returned ok True, phase F6, "Run archived." — straight out of ASSAY,
#:       skipping both post-verification phases the run was STARTED with.
#:   (2) the same run at F2: `Foundry-Phase('nyquist_done')` returned ok True,
#:       phase F6, "NYQUIST complete -> phase is now F6 (DONE)" — a message
#:       asserting a phase that never ran, from inside INSPECT.
#:
#: So `accepted_from` may be a CALLABLE of `state.json`, because `done`'s
#: source phase is a fact about the run's own flags rather than a constant:
#: the terminal phase is F5.5 when `--nyquist` was passed, else F5 when
#: `--temper` was, else F4. A static tuple would either refuse every plain run
#: at F4 or admit the two the defect drove. `nyquist_done` stays a constant —
#: F5.5 is the only phase NYQUIST can be finished from, whatever the flags.
_PHASE_ENTRY_SOURCES: dict[str, dict] = {
    "cast": {
        "accepted_from": ("F1",),
        "opens": "F2",
        "what": (
            "the CAST->INSPECT crossing: it closes F1, stamps the CAST "
            "baseline SHA every GRIND cycle-context block is built from, and "
            "records the run's FIRST INSPECT at FULL / first_of_phase"
        ),
        "why": (
            "From any other phase it would record a second INSPECT decision "
            "against a cycle that already has one."
        ),
        "hints": {
            "F0": (
                "The run has not entered CAST. Call "
                "Foundry-Phase(phase='start_cast') to enter F1 and build the "
                "wave; `cast` is what closes it."
            ),
            "F2": (
                "The run is already in INSPECT. To widen a DELTA cycle before "
                "ASSAY call Foundry-Phase(phase='inspect_start'); to open a "
                "GRIND call Foundry-Phase(phase='grind_start'). Re-running "
                "`cast` would overwrite the CAST baseline SHA with HEAD."
            ),
            "F3": (
                "The run is in GRIND. Close it with "
                "Foundry-Phase(phase='inspect_start') — that is the crossing "
                "that advances the cycle counter and decides this INSPECT's "
                "width."
            ),
            "F4": (
                "The run is in ASSAY. Call Foundry-Phase(phase='assay_fail') "
                "to open a GRIND from an ASSAY rejection, or "
                "Foundry-Phase(phase='temper') to enter TEMPER. `cast` would "
                "pull the run back to F2 and destroy the CAST baseline."
            ),
            "F5": (
                "The run is in TEMPER. Call Foundry-Phase(phase='grind_start') "
                "to open a GRIND on what TEMPER filed, or "
                "Foundry-Phase(phase='nyquist') to enter NYQUIST."
            ),
            "F5.5": (
                "The run is in NYQUIST. Call Foundry-Phase(phase='grind_start') "
                "to open a GRIND on what NYQUIST filed, or "
                "Foundry-Phase(phase='nyquist_done') to finish."
            ),
            "F6": (
                "The run is DONE. Start a new run rather than re-opening this "
                "one."
            ),
        },
    },
    "temper": {
        "accepted_from": ("F4",),
        "opens": "F5",
        "what": (
            "the ASSAY->TEMPER crossing: it enters F5 and opens TEMPER's own "
            "INSPECT at FULL / first_of_phase"
        ),
        "why": (
            "From any other phase it would record a second INSPECT decision "
            "against a cycle that already has one."
        ),
        "hints": {
            "F0": (
                "The run has not started. Call "
                "Foundry-Phase(phase='start_cast') and build first."
            ),
            "F1": (
                "CAST is still open. Call Foundry-Phase(phase='cast') when the "
                "wave is down."
            ),
            "F2": (
                "The run is in INSPECT, and TEMPER is reached THROUGH ASSAY. "
                "Close this INSPECT with "
                # D-169: the width, which is what that door reads. Naming the
                # rule here made this hint the fourth surface stating a
                # condition no code evaluates.
                "Foundry-Phase(phase='inspect_clean') — which refuses a DELTA "
                "cycle until the widening re-open records FULL — "
                "then Foundry-Gate(phase='assay'), run ASSAY, and only then "
                "Foundry-Phase(phase='temper'). Entering F5 from here would "
                "leave the last INSPECT before NYQUIST at DELTA width with no "
                "verdict written (FR-011 / US-004)."
            ),
            "F3": (
                "The run is in GRIND. Close it with "
                "Foundry-Phase(phase='inspect_start'), run the roster it "
                "names, then inspect_clean and ASSAY."
            ),
            "F5": (
                "The run is already in TEMPER. Call "
                "Foundry-Phase(phase='nyquist') to enter NYQUIST, or "
                "Foundry-Phase(phase='grind_start') to open a GRIND on what "
                "TEMPER filed."
            ),
            "F5.5": (
                "The run is in NYQUIST, which follows TEMPER. Call "
                "Foundry-Phase(phase='nyquist_done') to finish, or "
                "Foundry-Phase(phase='grind_start') to open a GRIND."
            ),
            "F6": (
                "The run is DONE. Start a new run rather than re-opening this "
                "one."
            ),
        },
    },
    "nyquist_done": {
        "accepted_from": ("F5.5",),
        "opens": "F6",
        "what": (
            "the NYQUIST->DONE crossing: it closes F5.5, enters F6 and archives "
            "the run"
        ),
        "why": (
            "From any other phase its own success message — \"NYQUIST complete\" "
            "— asserts a phase that never ran."
        ),
        "hints": {
            "F0": (
                "The run has not started. Call "
                "Foundry-Phase(phase='start_cast') and build first."
            ),
            "F1": (
                "CAST is still open. Call Foundry-Phase(phase='cast') when the "
                "wave is down."
            ),
            "F2": (
                "The run is in INSPECT. NYQUIST is reached THROUGH ASSAY and "
                "TEMPER: close this INSPECT with "
                "Foundry-Phase(phase='inspect_clean'), pass "
                "Foundry-Gate(phase='assay'), run ASSAY, and follow "
                "Foundry-Next from there. `nyquist_done` claims a phase this "
                "run has not entered."
            ),
            "F3": (
                "The run is in GRIND. Close it with "
                "Foundry-Phase(phase='inspect_start'), run the roster it names, "
                "then inspect_clean and ASSAY."
            ),
            "F4": (
                "The run is in ASSAY. NYQUIST has not been entered, so it "
                "cannot be finished. Follow Foundry-Next: it names "
                "Foundry-Phase(phase='temper') or "
                "Foundry-Phase(phase='nyquist') according to the flags this "
                "run was started with."
            ),
            "F5": (
                "The run is in TEMPER. Enter NYQUIST first with "
                "Foundry-Phase(phase='nyquist') — which sweeps the whole "
                "evidence corpus — and finish it with `nyquist_done`."
            ),
            "F6": (
                "The run is already DONE. Start a new run rather than "
                "re-closing this one."
            ),
        },
    },
    "done": {
        # D-164: a CALLABLE, because the terminal phase is the run's own flags.
        "accepted_from": lambda state: (
            ("F5.5",)
            if state.get("nyquist")
            else ("F5",) if state.get("temper") else ("F4",)
        ),
        "opens": "F6",
        "what": (
            "the run's terminal transition: it enters F6, archives the run and "
            "clears the active-run marker"
        ),
        "why": (
            "ST-010 crosses into F6 from the LAST phase this run's own flags "
            "make terminal — F5.5 with --nyquist, else F5 with --temper, else "
            "F4 — so from any earlier phase it would finish the run without "
            "the post-verification phases it was started with."
        ),
        "hints": {
            "F0": (
                "The run has not started. Call "
                "Foundry-Phase(phase='start_cast') and build first."
            ),
            "F1": (
                "CAST is still open. Call Foundry-Phase(phase='cast') when the "
                "wave is down."
            ),
            "F2": (
                "The run is in INSPECT. Close it with "
                "Foundry-Phase(phase='inspect_clean') and pass "
                "Foundry-Gate(phase='assay'); DONE is reached through ASSAY, "
                "never from inside an INSPECT."
            ),
            "F3": (
                "The run is in GRIND. Close it with "
                "Foundry-Phase(phase='inspect_start'), run the roster it names, "
                "then inspect_clean and ASSAY."
            ),
            "F4": (
                "ASSAY has passed, but this run was started with "
                "post-verification phases it has not run. --temper enters "
                "TEMPER through Foundry-Phase(phase='temper'); --nyquist enters "
                "NYQUIST through Foundry-Phase(phase='nyquist') and is finished "
                "with Foundry-Phase(phase='nyquist_done'). Foundry-Next names "
                "which applies here."
            ),
            "F5": (
                "The run is in TEMPER and --nyquist is set, so F5 is not the "
                "terminal phase. Call Foundry-Phase(phase='nyquist') to enter "
                "NYQUIST, then Foundry-Phase(phase='nyquist_done')."
            ),
            "F5.5": (
                "NYQUIST is open. Finish it with "
                "Foundry-Phase(phase='nyquist_done'), which is the F6 door from "
                "F5.5."
            ),
            "F6": (
                "The run is already DONE. Start a new run rather than "
                "re-closing this one."
            ),
        },
    },
}


def _phase_entry_source_problem(fdir: Path, token: str) -> dict | None:
    """The refusal a guarded token owes from a wrong source phase.

    None when the run is in a phase `token` is accepted from. See
    `_PHASE_ENTRY_SOURCES` for the four defects this closes and for why the
    table is one table.

    Reads the phase from state.json at call time, exactly as `inspect_start`'s
    inline guard does, and refuses BEFORE any decision, sweep or marker write —
    so a transition the server refused leaves no trace it was attempted.

    D-164: `accepted_from` is a tuple, or a callable of the state document for a
    token whose source phase depends on the run's own flags. Resolved HERE, so
    every branch that consults the table gets the same resolution and no branch
    re-derives "which phase is terminal for this run".
    """
    spec = _PHASE_ENTRY_SOURCES.get(token)
    if spec is None:
        return None
    state = _load_json(fdir / "state.json")
    current = state.get("phase", "")
    accepted = spec["accepted_from"]
    if callable(accepted):
        accepted = accepted(state)
    if current in accepted:
        return None
    return {
        "error": (
            f"Cannot enter {spec['opens']} from phase {current or 'F0'} — "
            f"Foundry-Phase(phase='{token}') is {spec['what']}, accepted from "
            f"{' or '.join(accepted)} and from nowhere else. "
            + spec["why"]
        ),
        "hint": spec["hints"].get(
            current,
            f"Reach {' or '.join(accepted)} first; "
            f"Foundry-Next names the transition that applies in {current or 'F0'}.",
        ),
        "phase": current,
        "accepted_from": list(accepted),
    }


def _phase_transition(phase: str, project_root: str, fdir: Path) -> dict:
    """The branch chain behind `foundry_mark_phase_complete`.

    Split out for D-067 alone: the caller owns the ordering-token handshake and
    consumes the token only when this returns a success. Every branch is
    verbatim what lived in the public function, and the guards that derive the
    accepted phase-token set from an AST walk read THIS function, because this
    is where the branches are.

    D-082 — NO TRANSITION LEAVES HALTED, AND THE GUARD IS STATED ONCE.
    -----------------------------------------------------------------
    Stated here, above the chain, rather than as an arm inside each of the ten
    branches. Ten copies of one precondition is exactly the shape that produced
    the defect: `_halt_if_capped` was wired into `grind_start` and `assay_fail`
    and every other branch silently resumed the run. The next branch added to
    this chain inherits the guard by standing below it.

    It compares no phase literal, so the AST drift guard that derives the
    accepted token set from this function's own `phase == "<literal>"`
    comparisons still reads exactly the ten branches and no eleventh.
    """
    if (halted := _halted_refusal(fdir, f"Foundry-Phase(phase='{phase}')")) is not None:
        return halted

    if phase == "start_cast":
        _update_phase(fdir, "F1")
        return {"ok": True, "phase": "F1", "message": "Phase is now F1 (CAST). Create team and build."}

    elif phase == "cast":
        # D-124 — THE SOURCE PHASE IS A PRECONDITION HERE TOO.
        # Stated FIRST, before the team scan: a run in ASSAY is not a run whose
        # CAST wave might still be up, and the honest answer to
        # `cast` from F4 is "this is not the transition that applies", not
        # "shut down your teammates". See `_PHASE_ENTRY_SOURCES`.
        if (wrong := _phase_entry_source_problem(fdir, "cast")) is not None:
            return wrong
        teams = _check_active_teams(project_root)
        if teams["active"]:
            return {"error": f"Cannot mark CAST complete \u2014 active teams: {', '.join(teams['teams'])}",
                    "hint": "Shut down all teammates and TeamDelete before marking CAST complete"}
        # GI-009 / ST-006 / AC-016 / OT-012 \u2014 THE F2 ENTRY RECORDS FULL.
        #
        # This branch, not `start_cast`: `start_cast` calls
        # `_update_phase(fdir, "F1")` and enters CAST, opening no INSPECT at
        # all. `cast` is the token that enters F2, so it is the transition that
        # opens the run's first INSPECT and therefore the one that decides its
        # width \u2014 recorded here, before any Foundry-Next is called (OT-012).
        entry = _decide_inspect_mode(
            fdir, project_root, decided_by="cast", phase="F2",
            cycle=_current_cycle(fdir),
        )
        # D-014 / FR-009 / GI-002 \u2014 THE FULL RULE FIRES HERE, SO THE SWEEP RUNS
        # HERE. Decided and swept BEFORE the first marker is written, so a
        # refused transition leaves no trace it was attempted \u2014 the same
        # ordering `inspect_start` holds against its state transaction.
        sweep = _sweep_evidence_at_boundary(fdir, project_root, entry, full=True)
        if not sweep["ok"]:
            return _sweep_refusal(sweep, _current_cycle(fdir), token="cast")

        (fdir / CAST_COMPLETE_MARKER).write_text(f"{_now()}\n", encoding="utf-8")
        # Stamp the CAST baseline HEAD SHA so GRIND cycles can show teammates
        # what has changed since CAST ended. Used by foundry_spawn_teammate
        # (phase='grind') to build a cycle-context block the lead appends to
        # the GRIND prompt, saving redundant re-exploration of files that
        # earlier cycles already touched.
        import subprocess as _sp
        try:
            _rev = _sp.run(
                ["git", "-C", project_root, "rev-parse", "HEAD"],
                capture_output=True, text=True, timeout=5,
            )
            if _rev.returncode == 0 and _rev.stdout.strip():
                (fdir / CAST_BASELINE_SHA_MARKER).write_text(_rev.stdout.strip(), encoding="utf-8")
        except (FileNotFoundError, _sp.TimeoutExpired, OSError):
            pass
        _update_phase(fdir, "F2")
        _record_inspect_mode(fdir, entry)
        _record_cycle_rollup(
            fdir, _current_cycle(fdir), evidence_sweep=sweep["record"]
        )
        return {
            "ok": True,
            "phase": "F2",
            "inspect_mode": entry["mode"],
            "inspect_rule": entry["rule"],
            "required_streams": entry["required_streams"],
            "evidence_sweep": sweep["record"],
            "message": (
                f"CAST complete \u2192 phase is now F2 (INSPECT), mode {entry['mode']} "
                f"(rule {entry['rule']}). Required streams: "
                f"{', '.join(entry['required_streams'])}. Evidence sweep "
                f"re-executed {len(sweep['record']['logs_reexecuted'])} log(s) "
                f"at {sweep['record']['scope']} scope."
            ),
        }

    elif phase == "inspect_clean":
        # D-117 \u2014 THE WIDTH RECORD IS A PRECONDITION OF CLOSING AN INSPECT.
        #
        # Stated FIRST, before the roster is even consulted: "which streams did
        # this INSPECT require" has no answer without it, so a roster read here
        # would be the roster nothing recorded that GI-008 names. The refusal
        # below tested `== "DELTA"` and let "unrecorded" through, which is how a
        # resumed legacy archive closed its INSPECT and moved the run to F4
        # having run three streams.
        if (unrecorded := _unrecorded_width_problem(fdir)) is not None:
            return {
                "error": f"Cannot mark INSPECT clean \u2014 {unrecorded['reason']}",
                "hint": unrecorded["hint"],
                "unrecorded_width": True,
            }
        streams = _check_streams_complete(project_root)
        if not streams["complete"]:
            return {"error": f"Cannot mark INSPECT clean \u2014 streams incomplete: {streams['missing']}",
                    "hint": streams.get("hint")
                            or "Run all required verification streams first"}
        # CT-008 / AC-008: "clean" is the tier-aware question. A LATENT-only
        # backlog passes here for the same reason it passes ASSAY \u2014 nobody drove
        # a failing instance \u2014 and it is carried, named, to the F6 backlog
        # rather than silently closed. LIVE and unknown-tier still refuse.
        blocking = _blocking_defects(fdir)
        if blocking["blocking"] > 0:
            return {"error": f"Cannot mark INSPECT clean \u2014 {blocking['reason']}",
                    "hint": blocking["hint"]}
        # D-035 / GI-002: a fix that landed DURING this INSPECT changed the tree
        # after the width was decided and the sweep taken, so this cycle's
        # evidence no longer covers what ASSAY is about to be opened on. The
        # remedy is a boundary crossing, which re-decides and re-sweeps.
        recorded_mode = _current_inspect_mode(fdir) or {}
        superseded = recorded_mode.get("fixes_after_decision") or []
        if superseded:
            return {
                "error": (
                    f"Cannot mark INSPECT clean \u2014 {len(superseded)} defect(s) "
                    f"were fixed after this INSPECT's width was decided: "
                    f"{', '.join(superseded)}"
                ),
                "hint": (
                    f"This cycle was swept at {recorded_mode.get('mode', '?')} "
                    "width before those fixes landed, so its evidence does not "
                    "cover the surface they changed. Cross the boundary again \u2014 "
                    "Foundry-Phase(phase='grind_start') then "
                    "Foundry-Phase(phase='inspect_start') \u2014 which re-decides the "
                    "width and re-sweeps at the new HEAD, and re-run the streams."
                ),
                "fixes_after_decision": list(superseded),
            }
        # AC-016 / D-068 — A DELTA CYCLE DOES NOT OPEN ASSAY.
        #
        # LEAD RULING, GRIND cycle 4: "every final gate still runs everything at
        # full width" (US-004) is a property of the INSPECT that PRECEDES the
        # gate, so a DELTA cycle coming back clean earns the widening re-open,
        # not the gate.
        #
        # D-169 — AND THE CONDITION NAMED IS THE ONE TESTED ONE LINE ABOVE.
        #
        # This said "ASSAY is only opened by an INSPECT whose recorded rule is
        # final_gate", which named the RULE while the `if` beside it reads the
        # MODE — and the sibling gate's checklist entry that decides the same
        # question is named `inspect_ran_at_full_width`. Driven at the ASSAY
        # door, a FULL INSPECT recorded with rule verifier_touched opens ASSAY,
        # so the sentence was false about the very run this plugin builds, where
        # a GRIND touching the verifier is the ordinary cycle. The width is
        # reported as the fact it is, and the rule beside it as provenance.
        #
        # LAST of the three refusals, deliberately. Open defects and
        # fixes-landed-mid-INSPECT are both more specific than "this cycle was
        # narrow", and a lead told about the width when a defect is open would
        # widen an INSPECT it is about to invalidate.
        if recorded_mode.get("mode") == "DELTA":
            return {
                "error": (
                    f"Cannot mark INSPECT clean — cycle {recorded_mode.get('cycle', '?')} "
                    f"ran at DELTA width (rule {recorded_mode.get('rule', '?')}), and "
                    "ASSAY is only opened by an INSPECT whose recorded mode is "
                    "FULL."
                ),
                "hint": (
                    "The DELTA cycle came back clean, which earns the widening "
                    "re-open rather than the gate: call "
                    "Foundry-Phase(phase='inspect_start') again from F2. That "
                    "crossing advances the cycle counter, sweeps the whole "
                    "evidence corpus, records FULL and requires the full roster "
                    "— then inspect_clean opens ASSAY. The rule that re-open "
                    "records is final_gate, or verifier_touched when the diff "
                    "cannot be measured; either satisfies this door, which "
                    "reads the width."
                ),
                "inspect_mode": "DELTA",
                "inspect_rule": recorded_mode.get("rule", ""),
            }
        (fdir / INSPECT_CLEAN_MARKER).write_text(f"{_now()}\n", encoding="utf-8")
        _update_phase(fdir, "F4")
        return {"ok": True, "phase": "F4", "message": "INSPECT clean \u2192 phase is now F4 (ASSAY)"}

    elif phase == "inspect_start":
        # ST-001 / FR-005 / AC-008 — the GRIND -> INSPECT boundary.
        #
        # This token did not exist. The guidance engine told the lead to
        # "update state to F2" with no tool that does it, so a run looping
        # GRIND -> INSPECT never left F3 in state.json and the cycle counter
        # never moved: grand-vulture recorded "cycle": 0 across 18 cycles while
        # its defects carried lead-asserted cycles 0-17.
        #
        # The increment is an EFFECT of handling the boundary crossing, not a
        # value any caller supplies: this handler takes no cycle argument and
        # consults none. Only F3 -> F2 advances it — the F1 -> F2 entry from
        # CAST is the run's first INSPECT, not a new cycle.
        state_path = fdir / "state.json"
        completed_cycle = _current_cycle(fdir)

        # D-113 / D-114 / D-116 — THE SOURCE PHASE IS A PRECONDITION.
        # ----------------------------------------------------------
        # LEAD RULING, GRIND cycle 6: `inspect_start` is accepted from EXACTLY
        # TWO source phases — F3, the GRIND->INSPECT crossing ST-005 names, and
        # F2, the widening re-open the cycle-4 ruling added — and BOTH advance
        # the counter. From every other phase it is refused, naming the phase
        # the run is actually in and the transition that applies there.
        #
        # It had no such precondition, and the counter-advance arm below
        # (`prev_phase == "F3" or (prev_phase == "F2" and widening)`) silently
        # absorbed the difference: entered from F1, F4, F5, F5.5 or F6 the call
        # ran the whole decision, sweep and record, returned ok, and moved NO
        # counter. Three defects came out of that one hole, all driven through
        # the real doors:
        #
        #   D-113  `_advance_escalation_exits` was then called with
        #          `completed_cycle` equal to the cycle STILL IN FLIGHT. From
        #          F5.5 with cycle 6 open the clean arm evaluated cycle 6,
        #          latched it into `live_clean_cycles_counted` and stamped the
        #          class CLEARED / clean_cycles / cleared_at 6, counted
        #          [4, 5, 6]. TEMPER then filed a LIVE instance of that class
        #          INSIDE cycle 6, and CLEARED is terminal, so the boundary that
        #          really closed cycle 6 could not undo it. This is precisely
        #          the call the DONE and nyquist_done refusal hints instruct the
        #          lead to make.
        #   D-114  the budget arm's guard is `completed_cycle >=
        #          packet_cycles[BUDGET - 1]`, satisfied by EQUALITY — which is
        #          what every non-advancing `inspect_start` produces. Packet 2
        #          emitted in cycle 4 at F5.5, `inspect_start` from F5.5, and
        #          the class was stamped CLEARED / budget / cleared_at 4: the
        #          packet retracted inside the cycle that emitted it, D-058's
        #          exact symptom one door over.
        #   D-116  a second INSPECT decision stamped onto a cycle that already
        #          had one — `_record_cycle_rollup`'s flat write replaced
        #          cycle 2's row, so `state.inspect_modes` held two decisions for
        #          one cycle and the rollup held the later — while the run was
        #          also pulled out of F4/F5/F5.5/F6 back into F2. From F1 it
        #          returned ok at cycle 0 with `.cast-complete` absent, skipping
        #          CAST entirely. And `_decide_inspect_mode` was called with
        #          `cycle = completed_cycle + 1` while the entry landed on the
        #          unadvanced counter, so the roster recorded for cycle N was the
        #          sample drawn with seed N+1 (AC-018 / FR-033).
        #
        # ONE precondition closes all three, and it is stated here rather than
        # as three separate guards inside the arms, because the arms are correct
        # given a real boundary crossing: what was wrong is that this branch
        # called them when no cycle had ended.
        prev_phase_now = _load_json(state_path).get("phase", "")
        if prev_phase_now not in ("F3", "F2"):
            return {
                "error": (
                    f"Cannot start an INSPECT from phase {prev_phase_now or 'F0'} — "
                    "Foundry-Phase(phase='inspect_start') is the GRIND->INSPECT "
                    "crossing (ST-005), accepted from F3, and from F2 as the "
                    "widening re-open of a DELTA cycle. It is the call that "
                    "ADVANCES the cycle counter, so from any other phase it "
                    "would record a second INSPECT decision against a cycle that "
                    "has not ended and evaluate the escalation exit arms on a "
                    "cycle still in flight."
                ),
                "hint": _INSPECT_START_SOURCE_HINTS.get(
                    prev_phase_now,
                    "Reach F3 first: Foundry-Phase(phase='grind_start') opens a "
                    "GRIND, and its completion is what this transition closes.",
                ),
                "phase": prev_phase_now,
                "cycle": completed_cycle,
                "accepted_from": ["F3", "F2"],
            }

        # AC-016 / D-068 — THE F2->F2 WIDENING RE-OPEN.
        #
        # LEAD RULING, GRIND cycle 4: a DELTA INSPECT that completes with zero
        # new defects has not earned ASSAY — it has earned the right to re-open
        # INSPECT at FULL width, and THAT crossing is the final gate. So an
        # `inspect_start` called while the run is already at F2 is that
        # widening: it advances the counter, records FULL / final_gate, sweeps
        # the whole corpus and requires the full roster.
        #
        # It is refused on two conditions, and both matter. Open LIVE or
        # unknown-tier defects go to GRIND first — widening an INSPECT over
        # known-broken code re-verifies a tree the lead is about to change. And
        # a cycle whose recorded mode is NOT DELTA has nothing to widen: that
        # branch is a stray second `inspect_start`, which is exactly the call
        # D-057 used to double-count a clean cycle with, so it is named as the
        # mistake it is rather than silently advancing the run a cycle.
        widening = prev_phase_now == "F2"
        if widening:
            recorded_now = _current_inspect_mode(fdir) or {}
            if recorded_now.get("mode") != "DELTA":
                return {
                    "error": (
                        "Cannot re-open INSPECT — this cycle's recorded width is "
                        f"{recorded_now.get('mode') or 'unrecorded'}"
                        + (f" (rule {recorded_now['rule']})"
                           if recorded_now.get("rule") else "")
                        + ", so there is nothing to widen."
                    ),
                    "hint": (
                        "The F2->F2 re-open exists to widen a DELTA INSPECT to "
                        "FULL before ASSAY. From a FULL cycle, call "
                        "Foundry-Phase(phase='inspect_clean') to open ASSAY, or "
                        "Foundry-Phase(phase='grind_start') to open a GRIND. The "
                        "cycle counter has NOT moved."
                    ),
                    "cycle": completed_cycle,
                    "inspect_mode": recorded_now.get("mode", ""),
                }
            widen_blocking = _blocking_defects(fdir)
            if widen_blocking["blocking"] > 0:
                return {
                    "error": (
                        "Cannot re-open INSPECT at full width — "
                        f"{widen_blocking['reason']}"
                    ),
                    "hint": (
                        "Fix them in GRIND first: Foundry-Tasks, then "
                        "Foundry-Gate(phase='grind'), then "
                        "Foundry-Phase(phase='grind_start'). Widening an INSPECT "
                        "over code the run is about to change re-verifies a tree "
                        "that will not exist. The cycle counter has NOT moved."
                    ),
                    "cycle": completed_cycle,
                    "live": widen_blocking["live"],
                    "unknown_tier": widen_blocking["unknown"],
                }

        # ST-006 / GI-002 / ST-005 / CT-007 — DECIDE, THEN SWEEP, THEN
        # TRANSACT. IN THAT ORDER, AND OUTSIDE THE LOCK.
        #
        # The counter advance below lives inside `_document_transaction`, an
        # fcntl-locked critical section over state.json that every other tool
        # call on this run contends on. `sweep_evidence_at_head` creates a
        # detached worktree and runs a bounded thread pool of subprocesses
        # inside it — seconds to minutes. Holding the flock across that would
        # serialise the entire run for the duration, for no benefit: neither
        # the mode decision nor the sweep reads or writes state.json.
        #
        # It also gets the refusal semantics right for free. A mismatched log
        # returns BEFORE the transaction opens, so the cycle counter is
        # untouched (AC-013 / OT-008) and no mode is recorded for a transition
        # that did not happen. A transition the server refused must leave no
        # trace that it was attempted.
        entry = _decide_inspect_mode(
            fdir, project_root, decided_by="inspect_start", phase="F2",
            cycle=completed_cycle + 1, widening=widening,
        )
        sweep = _sweep_evidence_at_boundary(
            fdir, project_root, entry, full=entry["mode"] == "FULL"
        )
        if not sweep["ok"]:
            return _sweep_refusal(sweep, completed_cycle)

        # D-103: read-phase, advance-phase and increment are ONE critical
        # section. They used to be three separate read-modify-writes over the
        # same file (the increment re-read state.json AFTER _update_phase had
        # written it), so a concurrent writer landing between them lost either
        # the phase or the counter. _update_phase nests inside this transaction
        # and mutates the same in-flight document — which is what the
        # re-entrancy in _document_transaction exists for.
        advanced = False
        with _document_transaction(state_path) as state:
            prev_phase = state.get("phase", "")
            _update_phase(fdir, "F2")
            # F3 is the ordinary GRIND->INSPECT crossing; F2 is the widening
            # re-open, which the ruling makes a cycle of its own — it sweeps the
            # whole corpus and runs the full roster, so every record it produces
            # belongs to a new cycle number rather than overwriting the DELTA
            # cycle's.
            #
            # D-113 / D-114: the source-phase precondition above admits exactly
            # these two, so this condition is now always true and `advanced` is
            # always True. Both are KEPT rather than simplified away — the
            # counter advance is the fact both escalation exit arms are stated
            # in terms of, and reading it off the transaction that performed it
            # is what makes "the arms run only on a transition that advanced the
            # counter" a property of the code rather than of a comment.
            advanced = prev_phase == "F3" or (prev_phase == "F2" and widening)
            if advanced:
                # _current_cycle still reads from disk, and that is correct
                # here: the flock guarantees no peer is mid-write and this
                # transaction has not flushed, so disk still holds the
                # pre-increment counter. Keeping the read on the ONE guarded
                # reader is what D-059's derived-membership test requires.
                state["cycle"] = _current_cycle(fdir) + 1
                state["updated_at"] = _now()
            modes = state.get("inspect_modes")
            if not isinstance(modes, list):
                modes = []
            entry["cycle"] = state.get("cycle", completed_cycle + 1)
            modes.append(entry)
            state["inspect_modes"] = modes

        cycle = _current_cycle(fdir)
        _record_cycle_rollup(
            fdir,
            cycle,
            inspect_mode=entry["mode"],
            inspect_rule=entry["rule"],
            stream_scope=entry["stream_scope"],
            evidence_sweep=sweep["record"],
        )
        # The commit this boundary crossed at, so the NEXT crossing's diff is
        # measured from here rather than from whatever older marker happened to
        # survive. Written after the transition commits: a marker naming a
        # boundary the server refused would silently narrow the next sweep.
        head = _head_sha(project_root)
        if head:
            (fdir / INSPECT_BOUNDARY_SHA_MARKER).write_text(
                f"{head}\n", encoding="utf-8"
            )

        # D-057 / D-058: BOTH exit arms, at the one event that knows a cycle
        # has ended. `completed_cycle` is the cycle that just closed — the one
        # whose filings are all in and whose structural packet has now closed —
        # and `cycle` is the counter this crossing produced, which is what
        # `cleared_at_cycle` records.
        #
        # D-113 / D-114: GUARDED ON THE ADVANCE. Both arms are stated in terms
        # of a cycle having ENDED ("two consecutive INSPECT cycles", "the second
        # structural packet CLOSED"), and the only evidence a cycle ended is the
        # counter moving. A crossing that did not move it has closed nothing, so
        # there is nothing for either arm to evaluate.
        cleared: list[dict] = []
        if advanced:
            # D-111 — THE RECORD BOTH ARMS NEED IS WRITTEN AT THIS BOUNDARY.
            #
            # `_advance_escalation_exits` returns immediately unless
            # `_persisted_escalations` finds a record, and the ONLY writer of
            # `escalation.json` was `_record_escalation_proposals`, reached only
            # from `foundry_defects_to_tasks`. So an escalated class whose open
            # backlog is entirely LATENT never acquired an entry: the DONE guard
            # blocks on `_escalated_classes`, which is derived from ledger
            # recurrence and is NOT tier-aware, while Foundry-Next's
            # transition_to_grind branch — the only caller of
            # `_escalation_notice` and the only branch naming Foundry-Tasks — is
            # guarded by `open_count > 0`, which counts LIVE and untiered only.
            #
            # Driven: three LATENT instances of class FDC at server cycles 1, 2
            # and 3 gave `escalated ['FDC']` with `escalation.json` ABSENT;
            # Foundry-Next never named the class or Foundry-Tasks; and
            # `Foundry-Gate('done')` refused "1 defect class(es) are still
            # ESCALATED: FDC". Following that refusal's own hint six times
            # (inspect_start) walked the counter from 4 to 9 with
            # `escalation.json` still absent and the class still escalated — the
            # hint named a call that was provably a no-op in that state, and its
            # alternative named no call at all. D-034's ruling promises the block
            # is bounded at "at most two more INSPECT cycles or one structural
            # packet"; there it was unbounded.
            #
            # Recorded through the ONE writer rather than synthesised here: a
            # second creator of an escalation.json entry is the drift shape this
            # module has paid for in D-001, D-043 and D-058, and
            # `_record_escalation_proposals` already writes the full C-3 entry
            # with `escalated_at_cycle` latched by `setdefault`.
            _record_escalation_proposals(
                fdir, _escalated_classes(fdir, project_root)
            )
            cleared = _advance_escalation_exits(
                fdir, project_root, completed_cycle, cycle
            )

        result = {
            "ok": True,
            "phase": "F2",
            "cycle": cycle,
            "inspect_mode": entry["mode"],
            "inspect_rule": entry["rule"],
            "required_streams": entry["required_streams"],
            "evidence_sweep": sweep["record"],
            "widened": widening,
            "message": (
                (
                    "INSPECT re-opened at full width → "
                    if widening
                    else "GRIND complete → "
                )
                + f"phase is now F2 (INSPECT), cycle {cycle}, "
                f"mode {entry['mode']} (rule {entry['rule']}). Required "
                f"streams: {', '.join(entry['required_streams'])}. Evidence "
                f"sweep re-executed {len(sweep['record']['logs_reexecuted'])} "
                f"log(s) at {sweep['record']['scope']} scope."
            ),
        }
        if cleared:
            result["escalation_cleared"] = cleared
        return result

    elif phase == "grind_start":
        # ST-008 / CT-016 - THE CYCLE CAP, ON EVERY DOOR THAT OPENS A GRIND.
        if (halt := _halt_if_capped(fdir, project_root, "grind_start")) is not None:
            return halt
        # Every recordable stream marker is cleared (derived from the canonical
        # stream vocabulary so new streams cannot go stale across GRIND cycles),
        # not just the required subset — completion state must stay honest.
        stream_markers = [_stream_marker(s) for s in sorted(VALID_STREAMS)]
        for marker in stream_markers + [INSPECT_CLEAN_MARKER, TASKS_GENERATED_MARKER]:
            (fdir / marker).unlink(missing_ok=True)
        _update_phase(fdir, "F3")
        return {"ok": True, "phase": "F3",
                "message": "All markers cleared \u2192 phase is now F3 (GRIND). Full INSPECT must re-run after."}

    elif phase == "assay_fail":
        # ST-008 / CT-016 - AND ON THIS DOOR TOO.
        #
        # `assay_fail` clears the same markers and calls the same
        # `_update_phase(fdir, "F3")` as `grind_start`; it is a second door into
        # the same phase, not a different kind of transition. A cap wired to one
        # of them would let a run looping back through ASSAY failure run forever
        # while a run looping through GRIND halts - and the ASSAY loop is
        # exactly the one --max-cycles exists to bound.
        if (halt := _halt_if_capped(fdir, project_root, "assay_fail")) is not None:
            return halt
        stream_markers = [_stream_marker(s) for s in sorted(VALID_STREAMS)]
        for marker in stream_markers + [INSPECT_CLEAN_MARKER, TASKS_GENERATED_MARKER]:
            (fdir / marker).unlink(missing_ok=True)
        _update_phase(fdir, "F3")
        return {"ok": True, "phase": "F3",
                "message": "ASSAY failed \u2192 phase is now F3 (GRIND). Fix defects, then full INSPECT, then ASSAY again."}

    elif phase == "temper":
        # D-125 — AND HERE, ON THE SAME TERMS.
        # F5 is reached THROUGH ASSAY. Without this, a DELTA INSPECT reached
        # NYQUIST with ASSAY never opened and no verdict written — the drive is
        # recorded on `_PHASE_ENTRY_SOURCES`.
        if (wrong := _phase_entry_source_problem(fdir, "temper")) is not None:
            return wrong
        # GI-009 / AC-016: the F5 entry opens TEMPER's first INSPECT, so it
        # records FULL / first_of_phase on exactly the same terms as the F2
        # entry above. One rule, two doors — which is what GI-009 means by
        # "whichever Foundry-Phase transition opens an INSPECT records the
        # mode".
        entry = _decide_inspect_mode(
            fdir, project_root, decided_by="temper", phase="F5",
            cycle=_current_cycle(fdir),
        )
        # D-014 — AND HERE, ON THE SAME TERMS.
        #
        # FR-009: the whole corpus is swept "whenever the FULL rule fires", and
        # this entry records FULL / first_of_phase exactly as the F2 entry does.
        # It swept nothing, and `_sweep_evidence_at_boundary` had exactly ONE
        # call site in the server — so on the clean path ASSAY → TEMPER →
        # NYQUIST → DONE no sweep ran at all, while the lead-lane fixes that
        # US-005 exists to enable were landing commits throughout F5. The
        # boundary that opens the LAST inspection of a run was the one boundary
        # not checking that the run's committed evidence still reproduces.
        sweep = _sweep_evidence_at_boundary(fdir, project_root, entry, full=True)
        if not sweep["ok"]:
            return _sweep_refusal(sweep, _current_cycle(fdir), token="temper")

        _update_phase(fdir, "F5")
        _record_inspect_mode(fdir, entry)
        # D-070: under the F5 entry's own key, beside the mode it just
        # recorded — this sweep belongs to the TEMPER crossing, not to the
        # INSPECT cycle whose number it happens to share.
        _record_cycle_rollup(
            fdir,
            _current_cycle(fdir),
            sub=TEMPER_ENTRY_ROLLUP_KEY,
            evidence_sweep=sweep["record"],
        )
        return {
            "ok": True,
            "phase": "F5",
            "inspect_mode": entry["mode"],
            "inspect_rule": entry["rule"],
            "required_streams": entry["required_streams"],
            "evidence_sweep": sweep["record"],
            "message": (
                f"Phase is now F5 (TEMPER), mode {entry['mode']} "
                f"(rule {entry['rule']}). Evidence sweep re-executed "
                f"{len(sweep['record']['logs_reexecuted'])} log(s) at "
                f"{sweep['record']['scope']} scope."
            ),
        }

    elif phase == "nyquist":
        # Enter F5.5. Mirrors the "temper" token: the phase mark is what makes
        # _compute_next_action's F5.5 branch reachable at all, since it
        # dispatches on state["phase"].
        #
        # D-133 / GI-002 — AND THE CORPUS IS SWEPT BEFORE NYQUIST, BY NAME.
        #
        # GI-002 names three terminal boundaries — "before ASSAY/NYQUIST/DONE"
        # — and this was the one crossing between them that re-executed
        # nothing. TEMPER lands lead-lane fixes throughout F5 (that is what
        # US-005 exists to enable), and each one changes the tree the committed
        # evidence was captured against. Refused BEFORE `_update_phase`, so a
        # refused crossing leaves the run in F5 with no trace it was attempted
        # — the same ordering the INSPECT boundaries hold.
        #
        # D-159 — AND IT TAKES THE SAME THREE-STATE RUNG THE OTHER TWO DO.
        #
        # This read `_terminal_evidence_sweep(...)["ok"]` alone, which is the
        # pre-D-149 rule: a whole-corpus sweep over a corpus that is no longer
        # in the tree yields zero logs, zero mismatches and ok True. Driven at
        # cycle 9 through the real door — a run at F5 with one non-reproducing
        # committed log was REFUSED before the F6 strip and ADMITTED after the
        # identical strip, phase F5.5, `corpus_size 0`, while `nyquist_done` and
        # `done` on the same tree both refused naming
        # EVIDENCE_CORPUS_STRIPPED_BEFORE_SWEEP. GI-002 names three boundaries
        # and this is the one it names by the word NYQUIST; a --nyquist run
        # entered F5.5 through the door that did not apply the rule.
        nyq_state = _terminal_evidence_state(fdir, project_root)
        if (refusal := _terminal_evidence_refusal(nyq_state, "enter NYQUIST")):
            return refusal
        nyq_sweep = nyq_state["evidence"]
        _update_phase(fdir, "F5.5")
        _record_cycle_rollup(
            fdir,
            _current_cycle(fdir),
            sub=NYQUIST_ENTRY_ROLLUP_KEY,
            evidence_sweep=nyq_sweep["record"],
        )
        return {
            "ok": True,
            "phase": "F5.5",
            "evidence_sweep": nyq_sweep["record"],
            "message": (
                "Phase is now F5.5 (NYQUIST). Evidence sweep re-executed "
                f"{len(nyq_sweep['record'].get('logs_reexecuted', []))} log(s) "
                "at full scope."
            ),
        }

    elif phase == "nyquist_done":
        # Leave F5.5 for F6. Distinct from the "temper" shape, which exits via
        # "done", because NYQUIST has its own completion semantics: auditors
        # can escalate ESCALATE_IMPL_BUG back into GRIND, so "the phase ran"
        # and "the run is finished" are separate facts.
        #
        # AC-011 / D-043 / D-044 — F6 HAS TWO DOORS AND BOTH ARE LOCKED.
        #
        # This comment used to assert "The DONE gate still runs first" while
        # the branch below it was an unconditional _update_phase +
        # clear_active_run consulting nothing: the claim rested entirely on the
        # lead following guidance prose. D-037 bound _done_preconditions to the
        # `done` branch and left this sibling terminal branch unbound, so the
        # fix covered one door of two — and on a --nyquist run, which
        # commands/start.md:578 routes through this token, it was the door the
        # run would actually use. Driven at the MCP boundary: a state with six
        # open escalated-class defects and no verdicts.json at all was refused
        # by Foundry-Phase("done") and admitted by Foundry-Phase("nyquist_done")
        # in the same breath.
        #
        # AC-011 constrains THE RUN — "the run cannot reach DONE while any
        # escalated-class defect remains open" — with no exception for how F6
        # is entered. So this calls the same _done_preconditions the `done`
        # branch and the gate call, on the same terms. Not a copy of the
        # checks: a second implementation of "done" is the drift that produced
        # this defect, not a fix for it.
        #
        # D-164 — AND WHICH PHASE IT IS LEAVING IS A PRECONDITION TOO.
        # `_done_preconditions` reads the ledgers and never `state.phase`, so
        # this returned ok True from F2 — "NYQUIST complete → phase is now F6"
        # asserted from inside an INSPECT, on a run where NYQUIST had never
        # opened. Guarded through the SAME table the three INSPECT-opening
        # tokens use; see `_PHASE_ENTRY_SOURCES`.
        if (wrong := _phase_entry_source_problem(fdir, "nyquist_done")) is not None:
            return wrong
        outcome = _done_preconditions(fdir, project_root)
        if not outcome["passed"]:
            return {
                "error": f"Cannot leave NYQUIST for DONE — {outcome['reason']}",
                "hint": (
                    outcome["hint"]
                    or "Call Foundry-Gate(phase='nyquist_done') for the full checklist."
                ),
                "checklist": outcome["checklist"],
                # D-191: every failing check, ranked, not only the one that
                # speaks. The transition publishes exactly what the gate does,
                # because it is exactly the same evaluation (D-037).
                "refusals": outcome["refusals"],
            }
        _update_phase(fdir, "F6")
        clear_active_run()
        return {"ok": True, "phase": "F6",
                "message": "NYQUIST complete → phase is now F6 (DONE). Run archived."}

    elif phase == "done":
        # AC-011 / D-037 — the transition, not just the gate, enforces closure.
        #
        # This branch was an unconditional _update_phase + clear_active_run: it
        # read no verdicts, no open defects and no escalated classes, so the
        # careful checks in foundry_gate("done") were advisory and a run
        # reached F6 with six open escalated-class defects and zero verdicts by
        # simply not calling the gate. AC-011 constrains the RUN ("the run
        # cannot reach DONE"), which is this call.
        #
        # It consults _done_preconditions — the SAME evaluation foundry_gate
        # runs, exactly its checks and no others. A precondition the gate does
        # not enforce must not be invented here: the transition and the gate
        # disagreeing about what "done" means is the failure this is fixing.
        #
        # D-164 — EXCEPT THE ONE THING _done_preconditions CANNOT SEE.
        # Its checklist reads the ledgers; ST-010 also constrains the FROM-state
        # ("F5.5 or F5 complete"), and this branch read `state.phase` never.
        # Driven: a run at F4 with `temper` and `nyquist` both set, every
        # requirement VERIFIED and no open defects, returned ok True and phase
        # F6 — out of ASSAY, skipping both post-verification phases the run was
        # started with. The guard is the same table the INSPECT-opening tokens
        # use, and it is a SOURCE-PHASE check, not a second closure check: what
        # "done" means is still `_done_preconditions` and only that.
        if (wrong := _phase_entry_source_problem(fdir, "done")) is not None:
            return wrong
        outcome = _done_preconditions(fdir, project_root)
        if not outcome["passed"]:
            return {
                "error": f"Cannot mark the run DONE — {outcome['reason']}",
                "hint": (
                    outcome["hint"]
                    or "Call Foundry-Gate(phase='done') for the full checklist."
                ),
                "checklist": outcome["checklist"],
                # D-191: every failing check, ranked, not only the one that
                # speaks. The transition publishes exactly what the gate does,
                # because it is exactly the same evaluation (D-037).
                "refusals": outcome["refusals"],
            }
        _update_phase(fdir, "F6")
        # Clear the active run — session is done with this run
        clear_active_run()
        return {"ok": True, "phase": "F6", "message": "Phase is now F6 (DONE). Run archived. Start a new run with foundry_init."}

    else:
        return {"error": f"Invalid phase: {phase}. Valid: {', '.join(PHASE_TOKENS)}"}


# --- Team lifecycle ---


def _scan_tmux_panes() -> dict:
    """Scan all tmux panes and classify them.

    Claude Code spawns teammates as PANES within the lead's tmux session
    (via split-window). Pane titles are set to the agent name (e.g., "@cast-c1").

    IMPORTANT: pane_current_command for a live teammate is the Claude Code
    VERSION NUMBER (e.g., "2.1.80"), NOT "claude" or "node" or "bash".
    A zombie pane shows "bash"/"zsh" because the agent exited and the shell
    is all that's left. But a live teammate's bash shell has the agent as a
    child process, so pane_current_command reflects the agent binary.

    We use pane title + child process check for definitive classification:
    - LEAD: the active pane
    - LIVE: teammate pane whose bash PID has child processes (agent running)
    - ZOMBIE: teammate pane that is dead OR whose bash PID has NO children
    - USER: non-lead pane that doesn't look like a teammate (left alone)

    Teammate detection: Claude Code sets pane titles via `select-pane -T`.
    Teammate panes have titles starting with "@" or matching agent naming
    patterns (cast-, grind-, etc.). User's personal panes are never touched.

    Returns {
        "available": bool,
        "live": [(id, title, cmd)],
        "zombie": [(id, title, cmd)],
        "user": [(id, title, cmd)],   # user's panes — never touched
        "lead": (id, title) | None,
    }
    """
    import subprocess
    import re

    empty: dict = {"available": False, "live": [], "zombie": [], "user": [], "lead": None}
    try:
        check = subprocess.run(["tmux", "list-sessions"], capture_output=True, timeout=5)
        if check.returncode != 0:
            return empty
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return empty

    # Patterns that identify a pane as a Claude Code teammate
    _TEAMMATE_RE = re.compile(
        r"^@|"                                 # Claude Code prefixes teammate titles with @
        r"cast[-_]|grind[-_]|inspect[-_]|"     # foundry phase agents
        r"assay[-_]|temper[-_]|decompose[-_]|" # foundry phase agents
        r"trace[-_]|prove[-_]|sight[-_]|"      # verification stream agents
        r"test[-_]|probe[-_]|"                 # verification stream agents
        r"^teammate-|^agent-",                 # generic teammate patterns
        re.IGNORECASE,
    )

    try:
        result = subprocess.run(
            ["tmux", "list-panes", "-a", "-F",
             "#{session_name}:#{window_index}.#{pane_index}\t"
             "#{pane_title}\t#{pane_dead}\t#{pane_current_command}\t"
             "#{pane_active}\t#{pane_pid}"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode != 0:
            return empty
    except (subprocess.TimeoutExpired, OSError):
        return empty

    live = []
    zombie = []
    user = []
    lead = None

    for line in result.stdout.strip().split("\n"):
        if not line.strip():
            continue
        parts = line.split("\t", 5)
        if len(parts) < 6:
            continue
        pane_id, title, dead, cmd, active, pid = parts

        if active == "1":
            lead = (pane_id, title)
            continue

        # Only touch panes that look like teammates
        if not _TEAMMATE_RE.search(title):
            user.append((pane_id, title, cmd))
            continue

        # Dead panes are always zombies
        if dead == "1":
            zombie.append((pane_id, title, cmd))
            continue

        # Check if the pane's process has children (= agent still running)
        has_children = _pid_has_children(pid)
        if has_children:
            live.append((pane_id, title, cmd))
        else:
            zombie.append((pane_id, title, cmd))

    return {"available": True, "live": live, "zombie": zombie, "user": user, "lead": lead}


def _pid_has_children(pid: str) -> bool:
    """Check if a PID has child processes (i.e., agent is still running)."""
    import subprocess

    if not pid or not pid.strip().isdigit():
        return False
    try:
        # pgrep -P returns 0 if children exist, 1 if none
        result = subprocess.run(
            ["pgrep", "-P", pid],
            capture_output=True, timeout=3,
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return False


def _kill_panes(panes: list[tuple[str, str, str]]) -> int:
    """Kill a list of (pane_id, title, cmd) tuples.

    Kills in REVERSE order to avoid index shifting — tmux reindexes
    panes when siblings are killed, so killing from highest index
    first prevents targeting the wrong pane.

    Returns count killed.
    """
    import subprocess

    # Sort by pane index descending so kills don't shift targets
    sorted_panes = sorted(panes, key=lambda p: p[0], reverse=True)
    killed = 0
    for pane_id, _title, _cmd in sorted_panes:
        try:
            subprocess.run(["tmux", "kill-pane", "-t", pane_id],
                           capture_output=True, timeout=5)
            killed += 1
        except (subprocess.TimeoutExpired, OSError):
            pass
    return killed


def _check_active_teams(project_root: str) -> dict:
    """Check if any registered teams still have directories OR live tmux panes.

    Two-layer check:
    1. Team directory exists in ~/.claude/teams/ (TeamDelete wasn't called)
    2. Live teammate tmux panes exist (teammates haven't exited yet)

    BOTH must be clear for the gate to pass. This prevents the lead from
    progressing to the next phase while teammates are still running.
    """
    fdir = get_run_dir(project_root)
    if not fdir:
        return {"active": False, "teams": [], "live_panes": []}
    state = _load_json(fdir / "state.json")
    teams = state.get("active_teams", [])

    teams_dir = Path.home() / ".claude" / "teams"
    active = [t for t in teams if (teams_dir / t).is_dir()]

    # Also check for live teammate tmux panes — even if TeamDelete was called,
    # the claude processes might still be running
    live_panes = []
    scan = _scan_tmux_panes()
    if scan["available"] and scan["live"]:
        live_panes = [title for _, title, _ in scan["live"]]

    is_active = len(active) > 0 or len(live_panes) > 0

    result: dict = {"active": is_active, "teams": active, "live_panes": live_panes}
    if live_panes and not active:
        result["hint"] = (
            f"{len(live_panes)} teammate pane(s) still running: {', '.join(live_panes)}. "
            "Send 'All work complete, stop working.' to each teammate in a parallel SendMessage batch, "
            "then TeamDelete immediately \u2014 do NOT wait for acks. "
            "If panes won't terminate, run: tmux kill-pane -t <pane_id>"
        )
    return result


def foundry_register_team(
    team_name: str,
    project_root: str = ".",
) -> dict:
    """Register a team for lifecycle tracking."""
    fdir = get_run_dir(project_root)
    if not fdir:
        return {"error": "No active foundry run. Call Foundry-Init first."}
    if (corrupt := _artifact_guard(fdir)):
        return corrupt
    state_path = fdir / "state.json"
    # D-103: the roster check and the roster write are one critical section \u2014
    # two concurrent registrations both passed the "no active teams" check
    # against the same snapshot and the second write dropped the first.
    refusal: dict | None = None
    total_teams = 0
    with _document_transaction(state_path) as state:
        teams = state.get("active_teams", [])
        if not isinstance(teams, list):
            teams = []

        teams_dir = Path.home() / ".claude" / "teams"
        still_active = [t for t in teams if t != team_name and (teams_dir / t).is_dir()]
        if still_active:
            refusal = {
                "error": f"Cannot register '{team_name}' \u2014 active teams exist: {', '.join(still_active)}",
                "hint": "Shut down existing teammates (SendMessage + TeamDelete) and Foundry-Team-Down before creating a new team. One team at a time.",
                "active_teams": still_active,
            }
        else:
            if team_name not in teams:
                teams.append(team_name)
            state["active_teams"] = teams
            total_teams = len(teams)

    if refusal:
        return refusal
    return {"ok": True, "registered": team_name, "total_teams": total_teams}


def foundry_unregister_team(
    team_name: str,
    project_root: str = ".",
) -> dict:
    """Unregister a team with verified teardown.

    Three-phase verification:
    1. CHECK: team directory gone (TeamDelete was called)
    2. CHECK: no live claude processes in non-lead panes
    3. CLEAN: kill zombie panes (dead + idle shells)
    4. UNREGISTER: remove from foundry state

    Blocks if steps 1 or 2 fail — forces proper shutdown ordering.
    """
    import time

    fdir = get_run_dir(project_root)
    if not fdir:
        return {"error": "No active foundry run."}
    if (corrupt := _artifact_guard(fdir)):
        return corrupt

    # ── Phase 1: Verify TeamDelete was called ────────────────────────
    teams_dir = Path.home() / ".claude" / "teams"
    if (teams_dir / team_name).is_dir():
        return {
            "error": f"Team directory still exists: ~/.claude/teams/{team_name}/",
            "hint": (
                "TeamDelete must be called BEFORE Foundry-Team-Down. "
                "Proper order: SendMessage(shutdown) to each teammate in ONE parallel batch -> "
                "TeamDelete immediately (do NOT wait for shutdown acks \u2014 idle panes ARE the signal) "
                "-> Foundry-Team-Down."
            ),
            "phase": "team_dir_exists",
        }

    # ── Phase 2: Verify no live teammate processes ───────────────────
    scan = _scan_tmux_panes()
    if scan["available"] and scan["live"]:
        live_titles = [title for _, title, _cmd in scan["live"]]
        return {
            "error": f"{len(scan['live'])} teammate pane(s) still running: {', '.join(live_titles)}",
            "hint": (
                "Teammates are still alive \u2014 they have active claude processes. "
                "Send 'All work complete, stop working.' to each teammate (parallel SendMessage), "
                "then TeamDelete immediately (do NOT wait for acks). Re-run Foundry-Team-Down after."
            ),
            "phase": "live_teammates",
            "live_panes": live_titles,
        }

    # ── Phase 3: Kill zombie panes ───────────────────────────────────
    killed = 0
    if scan["available"] and scan["zombie"]:
        killed = _kill_panes(scan["zombie"])
        # Brief wait + re-scan to verify
        time.sleep(1)
        rescan = _scan_tmux_panes()
        remaining_zombie = len(rescan.get("zombie", []))
        remaining_live = len(rescan.get("live", []))
        if remaining_zombie > 0 or remaining_live > 0:
            # Retry once
            if rescan.get("zombie"):
                killed += _kill_panes(rescan["zombie"])
            time.sleep(1)
            rescan = _scan_tmux_panes()
            remaining_zombie = len(rescan.get("zombie", []))
            remaining_live = len(rescan.get("live", []))
            if remaining_zombie > 0 or remaining_live > 0:
                return {
                    "error": (
                        f"Panes still alive after cleanup: "
                        f"{remaining_live} live, {remaining_zombie} zombie. "
                        "Kill manually: tmux kill-server"
                    ),
                    "phase": "cleanup_failed",
                    "killed": killed,
                }

    # ── Phase 4: Unregister from foundry state ───────────────────────
    state_path = fdir / "state.json"
    with _document_transaction(state_path) as state:
        teams = state.get("active_teams", [])
        if not isinstance(teams, list):
            teams = []
        teams = [t for t in teams if t != team_name]
        state["active_teams"] = teams

    return {
        "ok": True,
        "unregistered": team_name,
        "remaining_teams": len(teams),
        "tmux_panes_killed": killed,
        "verified_clean": True,
    }


# --- SIGHT enforcement ---


def _check_sight_required(project_root: str) -> dict:
    """Check if SIGHT audit is required based on frontend files in castings."""
    fdir = get_run_dir(project_root)
    if not fdir:
        return {"required": False}
    manifest = fdir / "castings" / "manifest.json"

    if not manifest.exists():
        return {"required": False}

    from foundry_mcp.tools.foundry_spawn import _manifest_shape_problem

    data = _load_json(manifest)
    # D-134: the records, not just the container. `castings: "nope"` used to
    # meet `.get()` two lines down and raise AttributeError out of Foundry-Next.
    # The guard at every MCP door names the file; this keeps the reader itself
    # total for the paths that reach it without one.
    if _manifest_shape_problem(data) is not None:
        return {"required": False, "reason": "castings/manifest.json records are unreadable"}
    ui_exts = {".tsx", ".jsx", ".vue", ".svelte", ".css", ".scss", ".html", ".astro"}

    ui_files = []
    for casting in data.get("castings", []):
        for f in casting.get("key_files", []):
            if any(f.endswith(ext) for ext in ui_exts):
                ui_files.append(f)

    if not ui_files:
        return {"required": False, "reason": "No frontend files in castings"}

    url = data.get("target_url", "")
    no_ui = data.get("no_ui", False)

    if no_ui:
        return {"required": True, "blocked": True, "ui_files": len(ui_files),
                "reason": f"--no-ui set but {len(ui_files)} frontend files in scope"}
    if not url:
        return {"required": True, "blocked": True, "ui_files": len(ui_files),
                "reason": f"No --url provided but {len(ui_files)} frontend files in scope"}

    return {"required": True, "blocked": False, "url": url, "ui_files": len(ui_files)}


# --------------------------------------------------------------------------- #
# Recurring-class escalation (FR-006 / FR-007 / FR-008 / FR-024,
# ST-002 / ST-003, AC-009 / AC-010 / AC-011, OT-003).
#
# grand-vulture ran FALSE_DOCUMENTED_CONTRACT for eight consecutive cycles
# (9-16), 42 defects, because foundry_defects_to_tasks groups by LOCATION
# (file or symbol) rather than by cause: one systemic class spread over 11
# files became 11 unrelated packets, each fixed per-instance, the class itself
# never addressed. The three-cycle rule would have caught it at cycle 11.
#
# The consecutive-cycle count is DERIVED from defects.json rather than kept as
# an incremental counter. Every defect record already carries its filing cycle
# and (optionally) its class, so the derivation covers defects filed through
# EVERY path — Foundry-Sync, Foundry-Defect, and migrated archives alike —
# without a counter that can desync from the ledger it describes. Only the
# operator-supplied parts (the recorded structural proposal) are persisted.
# --------------------------------------------------------------------------- #

ESCALATION_FILENAME = "escalation.json"

# ST-002 / FR-006 / A-012: escalation fires on the THIRD consecutive cycle in
# which new defects of a class are filed. Two consecutive cycles do not fire it.
ESCALATION_CYCLES = 3

# FR-007 / A-013: the optional stream-declared field on a defect record. Stream
# agents already emit systemic_patterns[] that nothing consumed; this is the
# key they write when instances share a root cause.
DEFECT_CLASS_FIELD = "class"

# FR-024 (Flexible — implementer-tunable): the fallback used when a stream
# declares no class. A-013/A-033 specify "type + file-cluster", and either the
# declared class or this fallback can accumulate the three-cycle count.
#
# THE TUNING KNOB IS THIS CONSTANT: the number of leading path segments that
# define one file cluster. The choice is a balance:
#   depth 0  = type only        -> over-clusters; unrelated subsystems merge
#   depth 2  = "src/api", ...   -> a class spread across sibling modules of one
#                                  subsystem still clusters, while frontend and
#                                  backend defects of the same type stay apart
#   full dir = "src/api/auth"   -> under-clusters; the grand-vulture failure
#                                  mode, where every file is its own class and
#                                  escalation can never accumulate
# 2 is the middle that groups a subsystem. Raise it for a deep monorepo, lower
# it for a flat one.
FALLBACK_CLUSTER_DEPTH = 2

# AC-010 / FR-008 / ST-003: the explicit directive that restores per-instance
# packets. Bare token overrides every class; "escalation-override: <class>"
# overrides exactly that class.
ESCALATION_OVERRIDE_TOKEN = "escalation-override"

# D-101: the override is a MARKER GRAMMAR on its own line, not a substring.
#
# The old test was `ESCALATION_OVERRIDE_TOKEN not in text.lower()` followed by
# `return scoped or {"*"}`, so a directive that FORBADE the override
# de-escalated everything: Foundry-Directive("Never apply an
# escalation-override. I want real structural fixes.") produced {"*"} and
# emptied the escalated set — semantics exactly inverted from operator intent,
# with no signal. It also fired on "the escalation-overrides list is empty" and
# on the token in uppercase prose. ST-003 makes the override an EXPLICIT
# directive action, and a substring match is not explicit.
#
# Recognised forms, each as a whole line (an optional markdown bullet or blank
# space may precede it, nothing may follow it):
#     escalation-override: <class>     — de-escalate exactly that class
#     escalation-override: *           — de-escalate every class
#     escalation-override              — de-escalate every class
# Anything else mentioning the token is prose and does nothing.
# D-133 — two residual holes in the same grammar, both "invisible rather than
# refused", and one root under them.
#
# (1) THE VALUE. The group was `(\S+)`, so a class key containing a SPACE could
#     never be overridden — and FR-007 makes `class` free text that a stream
#     writes. Driven: the class "SHARED RESOURCE LEAK" escalates;
#     Foundry-Directive("escalation-override: SHARED RESOURCE LEAK") returned
#     ok:true "injected", `_escalation_overrides()` returned set(), and the
#     class stayed escalated. Worse than inert: `_structural_proposal`
#     interpolates the class key into the instruction it hands the lead, so
#     the tool was telling the operator to send a string it could not read.
#     The value now runs to end of line and may be quoted.
#
# (2) THE PREFIX. `[\s>]*` admitted the blockquote character, so QUOTING an
#     escalation packet into a directive — "> escalation-override: X", even
#     buried at line 8 of a 9-line note — silently de-escalated the class. A
#     quotation reports what someone else wrote; it is never a request. The
#     quoted form is still MATCHED, by its own pattern, so that it can be
#     reported as ignored rather than vanish.
#
# (3) THE ROOT. Nothing reported an override either way. See `_override_report`.
_OVERRIDE_LINE_PREFIX = r"^[ \t]*(?:[-*+][ \t]*)?"
_OVERRIDE_QUOTED_PREFIX = r"^[ \t]*>[ \t>]*(?:[-*+][ \t]*)?"
_OVERRIDE_SCOPED_RE = re.compile(
    rf"{_OVERRIDE_LINE_PREFIX}{ESCALATION_OVERRIDE_TOKEN}\s*[:=][ \t]*(\S.*?)[ \t]*$",
    re.IGNORECASE | re.MULTILINE,
)
_OVERRIDE_BARE_RE = re.compile(
    rf"{_OVERRIDE_LINE_PREFIX}{ESCALATION_OVERRIDE_TOKEN}[ \t]*[:=]?[ \t]*$",
    re.IGNORECASE | re.MULTILINE,
)
# Recognised ONLY to be reported as ignored — never to honour.
_OVERRIDE_QUOTED_RE = re.compile(
    rf"{_OVERRIDE_QUOTED_PREFIX}{ESCALATION_OVERRIDE_TOKEN}"
    rf"(?:[ \t]*[:=][ \t]*\S.*?)?[ \t]*$",
    re.IGNORECASE | re.MULTILINE,
)

# The scoped form's value spelled as "every class" rather than a class key.
_OVERRIDE_ALL_VALUES = frozenset({"*", "all", "any", "every"})  # 4 spellings

# Quote characters a class key may be wrapped in. A quoted key keeps its inner
# punctuation verbatim — that is what quoting it is FOR — while a bare key
# keeps the D-101 trailing-punctuation strip so "escalation-override: AUTH."
# still names AUTH.
_OVERRIDE_QUOTES = ('"', "'", "`")


def _override_value_quoting(raw: str) -> tuple[str, bool]:
    """The class key a scoped marker names, and whether it was QUOTE-WRAPPED.

    D-139: the two halves used to be one function, ``_override_value``, that
    returned only the value, so the wildcard test ran on the ALREADY-UNWRAPPED
    string and quoting could not protect a class key spelled like a wildcard.
    ``escalation-override: "all"`` read back as ``{"*"}`` — every escalated
    class — when the operator had named the single class ``all``. Driven end to
    end with two escalated classes, ``AUTH_CONTRACT`` and ``all``: sending the
    tool's own rendered instruction de-escalated BOTH.

    D-196's second instance: that split left ``_override_value`` behind as a
    one-line wrapper over this function with no caller anywhere, named only in
    a test docstring narrating the defect above. It is gone; the pin
    ``test_every_private_function_the_plugin_ships_is_reachable`` is what found
    it, which is the whole reason the pin reads CODE rather than prose.

    Whether the key was quoted is what distinguishes "this value IS the
    wildcard spelling" from "this value is a class key that LOOKS like one",
    and it is only knowable before the quotes come off. That is what quoting a
    key is FOR.
    """
    value = raw.strip()
    for quote in _OVERRIDE_QUOTES:
        if len(value) >= 2 and value.startswith(quote) and value.endswith(quote):
            return value[1:-1].strip(), True
    return value.strip(" .,;:'\"`"), False


def _fallback_class(defect: dict) -> str:
    """The ``type + file-cluster`` key, computed IGNORING any declared class.

    Kept separate from ``_defect_class`` because D-102 needs both keys for the
    same record: the declared identity, and the cluster it would have landed in
    had no stream declared one.
    """
    dtype = canonical_defect_type(defect.get("type", "")) or defect.get("type") or "UNTYPED"
    path = defect.get("file") or ""
    segments = [p for p in str(path).replace("\\", "/").split("/") if p][:-1]
    cluster = "/".join(segments[:FALLBACK_CLUSTER_DEPTH]) if segments else ""
    return f"{dtype}@{cluster or '-'}"


def _defect_class(defect: dict) -> str:
    """Return the class key a defect belongs to.

    The stream-declared ``class`` field when present (FR-007), otherwise the
    tunable ``type + file-cluster`` fallback (FR-024). Either can accumulate
    the three-cycle count (ST-002 / A-033).
    """
    declared = defect.get(DEFECT_CLASS_FIELD)
    if isinstance(declared, str) and declared.strip():
        return declared.strip()
    return _fallback_class(defect)


def _resolve_defect_classes(defects: list) -> dict[int, str]:
    """Assign every defect a class key such that the buckets stay a PARTITION.

    D-102 / FR-024 (implementer-tunable). ``class`` is OPTIONAL, so one stream
    omitting it on an otherwise identical finding used to split a real cluster
    in two: three defects on one file, same type, cycles 1/2/3, of which two
    carried class "SHARED", bucketed as SHARED{1,3} and MISSING@src{2}. Neither
    reached three consecutive cycles, so a class that genuinely recurred three
    straight cycles escaped escalation in silence. ST-002 says EITHER the
    declared field or the fallback accumulates the count — a mixed cluster
    accumulated in neither.

    THE RULE: an UNDECLARED defect joins the declared class that owns its
    fallback cluster.

      1. Declared defects keep their declared class, always. A stream that
         named a class meant it, and two differently-declared classes are never
         merged just because they share a file.
      2. Each fallback cluster maps to the declared classes seen on defects in
         that cluster. When exactly ONE declared class owns the cluster, the
         cluster's undeclared defects join it.
      3. When a cluster is owned by two or more declared classes the mapping is
         ambiguous, so undeclared defects stay in their own fallback bucket.
         Guessing between rival declared classes would invent a cluster no
         stream asserted; refusing to guess only costs the accumulation the old
         code was already failing to make.

    Returns ``{id(defect): class_key}`` — keyed by identity so two structurally
    identical dicts are still two records.
    """
    owners: dict[str, set[str]] = {}
    for d in defects:
        if not isinstance(d, dict) or not _class_declared(d):
            continue
        owners.setdefault(_fallback_class(d), set()).add(_defect_class(d))

    resolved: dict[int, str] = {}
    for d in defects:
        if not isinstance(d, dict):
            continue
        if _class_declared(d):
            resolved[id(d)] = _defect_class(d)
            continue
        cluster = _fallback_class(d)
        claimants = owners.get(cluster, set())
        resolved[id(d)] = next(iter(claimants)) if len(claimants) == 1 else cluster
    return resolved


def _class_declared(defect: dict) -> bool:
    declared = defect.get(DEFECT_CLASS_FIELD)
    return isinstance(declared, str) and bool(declared.strip())


def _consecutive_run(cycles: set[int]) -> tuple[int, int | None]:
    """Longest run of consecutive cycles, and the cycle that run ends on."""
    if not cycles:
        return 0, None
    best_len, best_end = 0, None
    run_len, prev = 0, None
    for c in sorted(cycles):
        run_len = run_len + 1 if prev is not None and c == prev + 1 else 1
        prev = c
        if run_len > best_len:
            best_len, best_end = run_len, c
    return best_len, best_end


def _directives_text(project_root: str) -> str:
    """Every active directive's body, urgent first, as one block of text."""
    directives = _read_directives(project_root)
    return "\n".join(directives.get("urgent", []) + directives.get("normal", []))


def _override_markers(text: str) -> dict:
    """Every escalation-override marker in ``text``, and how each was read.

    Returns ``{"overrides": set[str], "scoped": list[str], "quoted":
    list[str]}``. ``overrides`` is ``{"*"}`` for the every-class forms. The
    quoted markers are NOT in ``overrides`` — they are carried so the decision
    to ignore them can be reported instead of being silent.
    """
    scoped: list[str] = []
    override_all = False
    for match in _OVERRIDE_SCOPED_RE.finditer(text):
        value, was_quoted = _override_value_quoting(match.group(1))
        if not value:
            continue
        # D-139: the wildcard test runs on the RAW quoting, before the quotes
        # come off. A bare `all` is the every-class spelling; a quoted `"all"`
        # names the class whose key is the word "all".
        if not was_quoted and value.lower() in _OVERRIDE_ALL_VALUES:
            override_all = True
        else:
            scoped.append(value)

    if _OVERRIDE_BARE_RE.search(text):
        override_all = True

    quoted = [m.group(0).strip() for m in _OVERRIDE_QUOTED_RE.finditer(text)]

    return {
        "overrides": {"*"} if override_all else set(scoped),
        "scoped": sorted(set(scoped)),
        "quoted": quoted,
    }


def _escalation_overrides(project_root: str) -> set[str]:
    """Class keys the human has explicitly de-escalated, or {"*"} for all.

    Recognises ONLY the line-anchored marker grammar (D-101), and only
    UNQUOTED (D-133). A directive that merely mentions the token — including
    one forbidding its use, and including one quoting an escalation packet
    back — returns the empty set, so escalation stays on.
    """
    return _override_markers(_directives_text(project_root))["overrides"]


def _escalated_classes(
    fdir: Path, project_root: str, *, apply_overrides: bool = True
) -> dict[str, dict]:
    """Classes that have recurred for ESCALATION_CYCLES consecutive cycles.

    A class qualifies while it still has OPEN defects: once every defect of the
    class closes, the class is cleared (ST-003) and stops producing a
    structural packet. Escalation therefore never waives closure — it changes
    the SHAPE of the work, not whether it must be done (AC-011).

    ``apply_overrides=False`` answers "what WOULD be escalated if the human had
    sent no override?" — which is the only way to report what an override
    actually did (D-133). Every production caller leaves it True.
    """
    defects = _load_json(fdir / "defects.json").get("defects", [])
    overrides = _escalation_overrides(project_root) if apply_overrides else set()
    recorded = _load_json(fdir / ESCALATION_FILENAME).get("classes", {})
    # D-212: `_done_preconditions`, `_still_escalated_classes` and
    # `_advance_escalation_exits` all guard this container; this deciding read
    # did not, so a document whose `classes` is a list reached `recorded.get`
    # and raised AttributeError across the MCP boundary instead of returning
    # the house refusal shape (the D-127 failure, one artifact over).
    if not isinstance(recorded, dict):
        recorded = {}

    buckets = _class_buckets(defects)

    escalated: dict[str, dict] = {}
    for key, bucket in buckets.items():
        if not bucket["open"]:
            continue
        if "*" in overrides or key in overrides:
            continue
        # ST-001 / ST-002 — THE MECHANICAL EXIT, READ FROM THE LEDGER THAT
        # RECORDS IT.
        #
        # This function had exactly one exit: a class stopped escalating when
        # every instance of it closed. thunder-viper is what that costs. An
        # adversarial prover with no convergence criterion re-filed the same
        # class at a finer boundary in cycles 19, 20 and 21 — never driving a
        # live instance — so the class never emptied, never stopped drawing a
        # structural packet, and the run ended only when a human told the lead
        # to fix the last defects directly.
        #
        # Two exits now sit beside closure, and CLEARED is the persisted answer
        # to both. Whichever fired is recorded with its reason, so "why did this
        # class stop escalating" is answerable from an artifact rather than from
        # a status flag alone. CLEARED is terminal and outranks the cycle count:
        # a class that has left escalation does not re-enter it because three
        # more instances arrive — its open LIVE instances are ordinary blocking
        # defects fixed one at a time (AC-003), which is exactly what escalation
        # was an alternative to.
        # D-210: through the vocabulary. This read `.get("status") ==
        # "CLEARED"` — correct for the two spellings this module writes and
        # silently wrong for every other, since a value that is not the literal
        # simply fell through as still-escalated at THIS door while
        # `_persisted_escalations` dropped it at the other. Both doors now
        # resolve the same way, so the union in `_done_preconditions` cannot be
        # assembled from two different opinions of one field.
        if _escalation_status(recorded.get(key)) == ESCALATION_STATUS_CLEARED:
            continue
        run_len, _run_end = _consecutive_run(bucket["cycles"])
        if run_len < ESCALATION_CYCLES:
            continue
        escalated[key] = _class_info(key, bucket, recorded)
    return escalated


def _class_buckets(defects: list) -> dict[str, dict]:
    """Aggregate every defect record by resolved class, open and closed alike.

    D-102: resolve every record's class in ONE pass over the whole ledger, so a
    cluster split across declared and undeclared records still accumulates as
    one class. Per-record `_defect_class` cannot see the ledger, and that
    blindness is what let a mixed cluster escape.

    D-043 — LIFTED OUT OF `_escalated_classes` SO THE EXIT ARMS CAN SEE A CLASS
    WITH NOTHING OPEN. `_escalated_classes` drops such a class by design (it has
    no work to packet), and while the aggregation lived inside it the arms could
    reach the CURRENT open counts of a class only through the one caller that
    filters it out. The buckets themselves make no judgement about escalation;
    they are just "what does the ledger say about each class right now".
    """
    resolved = _resolve_defect_classes(defects)

    buckets: dict[str, dict] = {}
    for d in defects:
        if not isinstance(d, dict):
            continue
        key = resolved[id(d)]
        bucket = buckets.setdefault(
            key,
            {
                "class": key,
                "cycles": set(),
                "declared": False,
                "open": [],
                "total": 0,
                "files": set(),
                "symbols": set(),
                "spec_refs": set(),
                "sources": set(),
            },
        )
        bucket["total"] += 1
        bucket["declared"] = bucket["declared"] or _class_declared(d)
        # A filing cycle and a regression-reopen cycle both count: a class
        # reopening IS the class recurring, which is what escalation exists to
        # catch. Non-integer values are ignored rather than guessed at.
        for field in ("cycle", "reopened_in_cycle"):
            value = d.get(field)
            if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                bucket["cycles"].add(value)
        if d.get("file"):
            bucket["files"].add(d["file"])
        if d.get("symbol"):
            bucket["symbols"].add(d["symbol"])
        if d.get("spec_ref"):
            bucket["spec_refs"].add(d["spec_ref"])
        if d.get("source"):
            bucket["sources"].add(d["source"])
        if d.get("status") == "open":
            bucket["open"].append(d)
    return buckets


def _class_info(key: str, bucket: dict, recorded: dict) -> dict:
    """One class's CURRENT view, as every escalation writer and reader wants it.

    Derived from the ledger on every call and never cached, so "how many
    instances of this class are open" is answered by the ledger rather than by
    whatever the last recording happened to write. That is the property D-043
    needed: a class whose instances have all been fixed reports zero open here,
    and the proposal regenerated from it cannot go on asserting they are open.
    """
    run_len, run_end = _consecutive_run(bucket["cycles"])
    # D-212: the recorded entry may not be a mapping at all. `_escalation_status`
    # now resolves such an entry to ESCALATED rather than dropping it, so this
    # read is reached with one. `or {}` covered `None` and covered nothing else:
    # a string or a list fell through it and raised AttributeError on `.get`
    # several frames below the entry point.
    entry = recorded.get(key) if isinstance(recorded, dict) else None
    entry = entry if isinstance(entry, dict) else {}
    return {
        "class": key,
        "declared": bucket["declared"],
        "cycles": sorted(bucket["cycles"]),
        "consecutive_cycles": run_len,
        "escalated_at_cycle": run_end,
        "defect_ids": [d["id"] for d in bucket["open"]],
        # FR-001 / C-3: the two halves of "what is still open", split by the
        # axis that decides what happens to each. Open LIVE (and untiered)
        # instances stay blocking defects fixed per-instance; open LATENT
        # instances go to the F6 named backlog and block nothing. Computed
        # here, where the bucket is already in hand, rather than re-derived
        # by every reader of the escalation record.
        "open_live_defect_ids": [
            d["id"] for d in bucket["open"] if defect_tier(d) != "LATENT"
        ],
        "open_latent_defect_ids": [
            d["id"] for d in bucket["open"] if defect_tier(d) == "LATENT"
        ],
        "open_count": len(bucket["open"]),
        "total_count": bucket["total"],
        "files": sorted(bucket["files"]),
        "symbols": sorted(bucket["symbols"]),
        "spec_refs": sorted(bucket["spec_refs"]),
        "sources": sorted(bucket["sources"]),
        "proposal": entry.get("proposal", ""),
    }


def _empty_class_bucket(key: str) -> dict:
    """The bucket of a class the defect ledger no longer carries at all.

    An escalation.json entry can outlive every record that produced it — a
    resumed archive whose defects.json was truncated, a class key renamed by a
    later filing. The arms still have to be able to reach that entry, and
    "nothing open, nothing seen" is the honest reading rather than a KeyError.
    """
    return {
        "class": key,
        "cycles": set(),
        "declared": False,
        "open": [],
        "total": 0,
        "files": set(),
        "symbols": set(),
        "spec_refs": set(),
        "sources": set(),
    }


def _override_instruction(class_key: str) -> str:
    """The exact directive text that de-escalates ``class_key``.

    RENDERED and verified against the reader, never typed beside it. D-133's
    first hole was `_structural_proposal` interpolating a class key into an
    instruction the grammar could not parse back — the tool telling the
    operator to send a string that could not work.

    D-139: it then verified ONLY THE BARE BRANCH and returned the quoted
    fallback unverified — so for the class keys spelled like a wildcard (`all`,
    `any`, `every`) the tool printed a restore marker it would not honour, and
    the mis-read was not a no-op but an over-broad WILDCARD: following the
    tool's own printed instruction to restore per-instance packets for the
    single class named `all` de-escalated EVERY escalated class. Broader than
    AC-010 licenses, which is "restores per-instance packets" for the class
    NAMED.

    Every branch is verified now, and the candidates are DERIVED from the quote
    characters the reader itself recognises rather than typed here — a quote
    style the reader learns to accept becomes a candidate the same day. A key
    that survives none of them returns None, because a caller that prints
    nothing is strictly better than one that prints an instruction the reader
    will act on differently.
    """
    candidates = [f"{ESCALATION_OVERRIDE_TOKEN}: {class_key}"]
    candidates += [
        f"{ESCALATION_OVERRIDE_TOKEN}: {q}{class_key}{q}" for q in _OVERRIDE_QUOTES
    ]
    for candidate in candidates:
        if _override_markers(candidate)["overrides"] == {class_key}:
            return candidate
    return None


def _override_offer(class_key: str) -> str:
    """The restore offer to print for ``class_key``, or why there is none.

    Both places that offer the operator a way back to per-instance packets go
    through here, so neither can print an unverified marker — and a class key
    no marker can name says so, rather than being handed an instruction that
    would act on something else (D-139).
    """
    instruction = _override_instruction(class_key)
    if instruction is None:
        return (
            f"no override marker can name the class {class_key!r} — the "
            f"directive grammar cannot read that spelling back as this class, "
            f"so the class needs renaming before it can be overridden"
        )
    return f"Foundry-Directive('{instruction}')"


def _override_report(fdir: Path, project_root: str) -> dict:
    """Every escalation-override DECISION, reported rather than left silent.

    D-133's COMMON ROOT, and the reason its other two halves stayed invisible
    for a whole cycle. Nothing reported an override in either direction:
    ``foundry_inject_directive`` returned ok:true "Directive injected" and
    named no recognised override, and the consumer in ``_escalated_classes``
    silently ``continue``d past the class it dropped. So all four outcomes —
    the override worked, the class key was mistyped, the key carried a space
    the grammar could not read, the marker was quoted out of an escalation
    packet — produced BYTE-IDENTICAL output. An operator had no way to tell a
    working override from a dead one except by watching what the next
    Foundry-Tasks emitted, which is the shape of every defect in this class.

    Four decisions, each named:
      * de_escalated — the marker matched an escalated class, which is now off
      * unmatched    — a marker was read, and no escalated class has that key
      * quoted       — a marker was seen and IGNORED because it was quoted
      * escalated_classes — what is escalated with no override applied, so an
                       unmatched key can be compared against real ones
    """
    markers = _override_markers(_directives_text(project_root))
    candidates = set(_escalated_classes(fdir, project_root, apply_overrides=False))
    overrides = markers["overrides"]
    wildcard = "*" in overrides

    de_escalated = sorted(candidates) if wildcard else sorted(overrides & candidates)
    unmatched = [] if wildcard else sorted(overrides - candidates)

    known = (
        f"currently escalated: {', '.join(sorted(candidates))}"
        if candidates
        else "no class is currently escalated"
    )

    decisions: list[str] = []
    if wildcard:
        decisions.append(
            f"escalation-override (every class) — de-escalated "
            f"{len(de_escalated)} class(es): "
            + (", ".join(de_escalated) if de_escalated else f"none ({known})")
        )
    for key in de_escalated if not wildcard else []:
        decisions.append(f"escalation-override: {key!r} — de-escalated")
    for key in unmatched:
        decisions.append(
            f"escalation-override: {key!r} — matched NO escalated class ({known}). "
            f"The class key is free text a stream declares; it must match "
            f"EXACTLY, spaces included."
        )
    for line in markers["quoted"]:
        decisions.append(
            f"{line!r} — IGNORED: a blockquoted marker is a quotation of what "
            f"someone else wrote, not a request. Repeat it unquoted to apply it."
        )

    return {
        "decisions": decisions,
        "de_escalated": de_escalated,
        "unmatched": unmatched,
        "quoted_ignored": markers["quoted"],
        "escalated_classes": sorted(candidates),
    }


def _structural_proposal(info: dict) -> str:
    """Compose the structural-fix proposal recorded on the class's packet.

    FR-008 / ST-003: an escalated class gets ONE packet carrying a recorded
    proposal, not N per-instance packets. The proposal states the evidence that
    made this systemic — which cycles it recurred in, how wide it spreads — and
    names the obligation that every instance still closes (AC-011).
    """
    origin = "stream-declared" if info["declared"] else "clustered by type + file"
    spread = f"{len(info['files'])} file(s)" if info["files"] else "no file attribution"
    if info["symbols"]:
        spread += f", {len(info['symbols'])} symbol(s)"
    cycles = ", ".join(str(c) for c in info["cycles"])
    # D-043: the zero-open reading has its own sentence, because the general one
    # below interpolates a defect list and an open count and reads as a demand
    # when both are empty — "still has 0 open instance(s) ... all of  must reach
    # fixed" was in this run's own report about a class whose every instance had
    # been fixed. A class with nothing open is a statement of fact, not a packet.
    if not info["open_count"]:
        return (
            f"NO STRUCTURAL WORK OPEN — defect class '{info['class']}' ({origin}) "
            f"recurred for {info['consecutive_cycles']} consecutive cycles "
            f"(cycles seen: {cycles}) across {spread}, and every instance of it "
            f"is now closed. Recorded so the class's history stays readable; "
            f"there is nothing here to dispatch."
        )
    return (
        f"STRUCTURAL FIX REQUIRED — defect class '{info['class']}' ({origin}) has "
        f"recurred for {info['consecutive_cycles']} consecutive cycles "
        f"(cycles seen: {cycles}) and still has {info['open_count']} open "
        f"instance(s) across {spread}. Per-instance fixes have not held. Find the "
        f"single root cause these instances share and fix it there, then confirm "
        f"every listed defect closes as a consequence. Closure is NOT waived: all "
        f"of {', '.join(info['defect_ids'])} must reach fixed. If this class is "
        f"genuinely not systemic, the lead can restore per-instance packets with "
        f"{_override_offer(info['class'])}."
    )


def _escalation_entry_defaults(entry: dict) -> dict:
    """Fill an escalation.json class entry's C-3 keys, preserving what is there.

    A PRE-CHANGE ARCHIVE HAS NONE OF THEM, and every default here is chosen so
    such an entry reads as "escalated, nothing has happened yet" rather than as
    anything the run must act on: status ESCALATED, no exit reason, zero packets
    dispatched, zero clean cycles. Reading a missing `live_clean_cycles` as
    anything but zero would clear classes in an old archive that nothing ever
    measured.
    """
    entry.setdefault("status", ESCALATION_STATUS_ESCALATED)
    entry.setdefault("exit_reason", None)
    entry.setdefault("cleared_at_cycle", None)
    entry.setdefault("structural_packets_dispatched", 0)
    if not isinstance(entry.get("structural_packet_cycles"), list):
        entry["structural_packet_cycles"] = []
    if not isinstance(entry.get("live_clean_cycles"), int) or isinstance(
        entry.get("live_clean_cycles"), bool
    ):
        entry["live_clean_cycles"] = 0
    # D-057: which CLOSED cycles the clean arm has already evaluated, so a
    # second `inspect_start` in the same server cycle cannot count one twice.
    # The budget arm's `structural_packet_cycles` is the same guard for the same
    # reason; the clean arm shipped without one and cleared a class after ONE
    # real cycle. Empty on a pre-change archive, which reads as "nothing
    # counted yet" exactly like every other default here.
    if not isinstance(entry.get("live_clean_cycles_counted"), list):
        entry["live_clean_cycles_counted"] = []
    if not isinstance(entry.get("open_latent_defect_ids"), list):
        entry["open_latent_defect_ids"] = []
    return entry


def _record_escalation_proposals(fdir: Path, escalated: dict[str, dict]) -> None:
    """Persist each escalated class's proposal so it survives the tool call.

    ST-003 requires the proposal to be RECORDED on the class's single packet;
    keeping it only in the returned task list would lose it the moment the lead
    moved on.

    FR-028 / C-3 — THE RECORD IS NOW STATEFUL, NOT MERELY DESCRIPTIVE.
    ------------------------------------------------------------------
    It used to hold only what `_escalated_classes` re-derives from the ledger on
    every call, so nothing was lost by losing it. ST-001 changed that: "two
    consecutive INSPECT cycles with zero LIVE instances" is not a question
    `defects.json` can answer. The ledger carries each record's filing cycle and
    its reopen cycle, and neither can distinguish "cycle N ran and this class
    drew nothing" from "cycle N never ran" — and `_escalated_classes` holds no
    memory between calls. So `live_clean_cycles` and
    `structural_packets_dispatched` are COUNTERS kept here and advanced at the
    boundaries that own them, which is what "counted on the server cycle stamp"
    requires.

    Every write preserves an existing value: this function records proposals and
    refreshes the derived views, and must never reset a counter another boundary
    advanced.

    D-043 — EVERY RECORDED CLASS IS REFRESHED, NOT ONLY THE ESCALATING ONES.
    -----------------------------------------------------------------------
    The derived views — the proposal, `defect_ids`, `open_latent_defect_ids` —
    are answers to "what is open in this class RIGHT NOW", and this function
    used to refresh them only for the classes `_escalated_classes` returned. A
    class drops out of that set the moment its last instance is fixed, so the
    last thing ever written about it was written when it still had open work,
    and the F6 report went on printing that. The counters are untouched here as
    before; only the derived views are re-derived, for every entry the document
    already carries.
    """
    path = fdir / ESCALATION_FILENAME
    recorded = _load_json(path).get("classes", {})
    if not escalated and not (isinstance(recorded, dict) and recorded):
        # Nothing escalating and nothing on record: return before opening the
        # transaction, so a run that never escalated anything never grows an
        # `escalation.json` to say so.
        return
    defects = _load_json(fdir / "defects.json").get("defects", [])
    buckets = _class_buckets(defects)
    with _document_transaction(path) as data:
        classes = data.setdefault("classes", {})
        if not isinstance(classes, dict):
            classes = data["classes"] = {}
        keys = list(escalated) + [k for k in classes if k not in escalated]
        for key in keys:
            info = escalated.get(key) or _class_info(
                key, buckets.get(key) or _empty_class_bucket(key), classes
            )
            if key not in escalated:
                info["proposal"] = _structural_proposal(info)
            entry = classes.setdefault(key, {})
            if not isinstance(entry, dict):
                entry = classes[key] = {}
            _escalation_entry_defaults(entry)
            entry["proposal"] = info["proposal"]
            # D-001 — `escalated_at_cycle` IS A LATCH, AND THIS WRITER MOVED IT.
            #
            # `info["escalated_at_cycle"]` is `_consecutive_run`'s CURRENT run
            # end, recomputed from the ledger on every call, so a bare
            # assignment re-dates the escalation to the newest filing.
            # `foundry_defects_to_tasks` calls this recorder on every GRIND and
            # that call is mandatory — `foundry_gate`'s grind branch refuses
            # without `.tasks-generated`, which only that tool writes — so the
            # marker walked forward once per cycle, and ST-001's guard
            # (`completed_cycle <= escalated_at`), which `_clean_arm_step`
            # applies for `_advance_escalation_exits` at each boundary, then
            # skipped the count forever.
            #
            # Driven: class escalated at cycle 3, one LATENT instance filed at a
            # finer boundary each later cycle -> escalated_at walked 4, 5, 6
            # while live_clean_cycles stayed 0 and status stayed ESCALATED. Only
            # the budget arm could still terminate a class, so the exact
            # finer-boundary loop this exit exists to end could not converge on
            # the clean arm at all.
            #
            # `setdefault` is what the only other writer of this key already
            # does (`_spend_structural_budget`); this one was the odd writer
            # out, which is why the record disagreed with itself depending on
            # which boundary touched it last.
            entry.setdefault("escalated_at_cycle", info["escalated_at_cycle"])
            entry["consecutive_cycles"] = info["consecutive_cycles"]
            entry["defect_ids"] = info["defect_ids"]
            # FR-001: refreshed on every recording, because the backlog the F6
            # report names has to be the CURRENT set of never-reproduced
            # instances, not the set as of whenever the class first escalated.
            entry["open_latent_defect_ids"] = info.get("open_latent_defect_ids", [])
            entry["recorded_at"] = _now()
        data["updated_at"] = _now()


# D-043 — THE EXIT ARMS WALK THE LEDGER THAT RECORDS ESCALATION, NOT THE
# LEDGER THAT RECORDS WORK.
# ---------------------------------------------------------------------------
# Both arms used to iterate `_escalated_classes`, whose very first line is
# `if not bucket["open"]: continue`. So a class that escalated and then had
# every instance FIXED was invisible to both: `live_clean_cycles` stayed 0
# across every later boundary, `status` stayed ESCALATED, `exit_reason` stayed
# null, and both F6 artifacts reported it as unresolved work forever — the
# REPORT.md row carrying a blank exit reason, report.json carrying
# `by_status {"CLEARED": 0, "ESCALATED": 1}`.
#
# Driven (D-043): class escalated through the real door over three consecutive
# cycles, every instance then set to fixed, then four real GRIND->INSPECT
# crossings. live_clean_cycles 0, 0, 0, 0.
#
# Termination did still happen, because `_escalated_classes` returns nothing
# for such a class and so the DONE guard passed it — but that is CLOSURE, the
# pre-existing exit, and it leaves no record of itself. ST-001 and ST-002 name
# two arms and AC-004 requires the exit reason to be ON the record;
# `ESCALATION_EXIT_REASONS` is casting 1's frozenset {clean_cycles, budget} and
# a third reason is not ours to add. So the arms are made REACHABLE instead:
# they walk the persisted entries, and a class with nothing open draws zero LIVE
# instances by definition, advances a clean cycle at every boundary, and CLEARS
# with `clean_cycles` — which is exactly what happened to it.
#
# The DONE guard is unchanged and still keyed on what `_escalated_classes`
# returns (the D-034 ruling), so nothing here can deadlock it.


# D-210 / D-212 / D-214 / D-215 — THE CLOSED VOCABULARY, ENFORCED ON EVERY READ.
#
# The resolver and its two comparands were HERE, private to this module, while
# `escalation.json` has THREE readers: this module's two deciding reads,
# `foundry_report.py#_read_escalated_classes` and
# `scripts/measure-run.py#_read_escalation`. D-210 and D-212 fixed this copy of
# the bug; D-214 and D-215 were the same bug in the other two, and the class
# `closed-vocabulary-not-enforced-on-the-deciding-read` recurred for three
# consecutive cycles (20, 21, 22) because every cycle fixed a copy and the
# resolver could not reach the readers that had none.
#
# So `escalation_status` is PUBLIC in `schemas/vocab.py` now, beside the
# frozenset it enforces and beside `defect_tier`, and all three readers import
# it. Its docstring carries the full history. `tests/test_vocab.py` discovers
# the readers by AST over the shipped tree and fails any module holding a bare
# "ESCALATED"/"CLEARED" literal of its own, so a FOURTH reader cannot grow a
# fourth opinion of the field.
#
# THE IMPORT ALIASES IT TO `_escalation_status`, and deliberately: every call
# site in this module names it that, and so do the AST pins in
# `tests/test_escalation.py` that assert both deciding reads CALL it rather
# than re-deciding inline. The alias binds the same function object, and no
# logic for this field is left in this file.



def _persisted_escalations(
    fdir: Path, project_root: str, classes: dict
) -> list[str]:
    """The class keys `escalation.json` currently records as ESCALATED.

    Sorted, so both arms walk in one order and the document they write is
    stable across runs.

    OVERRIDES ARE HONOURED HERE TOO. `_escalated_classes` filters a class the
    operator de-escalated with a directive, and an arm reading the file directly
    would bypass that filter and eventually stamp the class CLEARED with an exit
    reason no rule earned — recording the operator's decision as the machine's,
    irreversibly, since CLEARED is terminal and a withdrawn directive could
    never bring the class back.
    """
    overrides = _escalation_overrides(project_root)
    if "*" in overrides:
        return []
    return sorted(
        key
        for key, entry in classes.items()
        if key not in overrides
        # D-210: through the vocabulary. This read `(entry.get("status") or
        # "ESCALATED") == "ESCALATED"`, so a status of `"BOGUS"` was not
        # ESCALATED and this door let it past.
        #
        # D-212 — AND NOTHING PRE-FILTERS THE SHAPE AHEAD OF THE RESOLVER.
        #
        # This comprehension tested `isinstance(entry, dict)` BEFORE calling
        # `_escalation_status`, so the resolver's third rung — "present and NOT
        # a member, OR AN ENTRY THAT IS NOT A MAPPING AT ALL -> ESCALATED" —
        # was unreachable through this door. A non-mapping entry was DROPPED
        # from the ESCALATED list rather than resolved into it, and this list
        # is one half of the union `_done_preconditions` and
        # `_still_escalated_classes` refuse on.
        #
        # Driven at cdb9322 through `server.call_tool` `Foundry-Gate('done')`
        # on a run whose class K has every instance fixed and verdicts
        # complete: entry `{"status": "BOGUS"}` blocked DONE naming K (correct,
        # post-D-210), while entry `"just a string"`, entry `["ESCALATED"]` and
        # entry `null` each rendered `escalated_classes_cleared` ABSENT from
        # the failing checks and the run proceeded to DONE. ST-010 is "every
        # escalated class CLEARED", and an entry that is not a mapping carries
        # no CLEARED — it must block exactly as an out-of-vocabulary status
        # now does.
        #
        # BOTH AXES. WHAT decides: `ESCALATION_STATUSES` by membership, as
        # D-210 established. WHERE the shape test lives: INSIDE the resolver,
        # where its docstring already said it lived, so no caller can reach the
        # field ahead of it. D-210 put the vocabulary in one place and left
        # this comprehension holding its own opinion of the shape one line
        # above it.
        #
        # TRUE POSITIVES KEPT: an absent entry, `{"status": null}`,
        # `{"status": ""}` and `{"status": "BOGUS"}` all still resolve to
        # ESCALATED and still block; `{"status": "CLEARED"}` still clears.
        and _escalation_status(entry) == ESCALATION_STATUS_ESCALATED
    )


def _spend_structural_budget(
    fdir: Path,
    project_root: str,
    escalated: dict[str, dict],
    packet_cycle: int,
) -> list[str]:
    """Count this dispatch against each class's structural-pass budget (ST-002).

    Called from `foundry_defects_to_tasks` immediately before it emits the
    packets, so `structural_packets_dispatched` counts what the run actually
    handed out rather than what some reader later inferred. Returns the class
    keys a packet was counted against on THIS call.

    DISPATCHES AND COUNTS. IT DOES NOT CLEAR (D-058).
    ------------------------------------------------
    ST-002's trigger is "the structural-pass budget for the class is exhausted
    (second structural packet CLOSED)" and AC-002 says "CLEARED after the second
    structural packet CLOSES". This function ran the CLEAR check too, from
    `foundry_defects_to_tasks`, BEFORE the packets were built — so the class was
    retracted inside the very call that emitted packet 2. Driven: escalate at
    cycle 3, advance to cycle 4, `Foundry-Tasks` -> structural_tasks 1 and status
    CLEARED; call it AGAIN in the SAME cycle -> structural_tasks 0 and
    escalated_classes [], while the packet just dispatched was still being
    worked. The old comment conceded the shape ("It is the NEXT call that emits
    nothing") — true only ACROSS cycles, and `Foundry-Tasks` is explicitly a
    tool a lead may call twice in one cycle, which is why
    `structural_packet_cycles` exists at all.

    A packet CLOSES when its GRIND cycle ends, and the event that knows a cycle
    ended is the `inspect_start` boundary. So both exit arms now live in
    `_advance_escalation_exits` and this function only ever hands work out.

    AT MOST ONE PACKET PER CLASS PER SERVER CYCLE. Re-reading the task list is
    not a second structural pass, and a budget a double-click could exhaust
    would end escalation after one real attempt. The recorded cycle list is the
    guard, which also makes the record legible: `structural_packet_cycles`
    reads as "the cycles this class was worked structurally in".
    """
    counted: list[str] = []
    if not escalated:
        # Nothing escalating: return before opening the transaction, so an
        # ordinary run never grows an `escalation.json` it has nothing to put in.
        return counted
    with _document_transaction(fdir / ESCALATION_FILENAME) as data:
        classes = data.setdefault("classes", {})
        if not isinstance(classes, dict):
            classes = data["classes"] = {}
        for key in sorted(escalated):
            entry = classes.setdefault(key, {})
            if not isinstance(entry, dict):
                entry = classes[key] = {}
            _escalation_entry_defaults(entry)
            entry.setdefault("escalated_at_cycle", escalated[key]["escalated_at_cycle"])
            if packet_cycle not in entry["structural_packet_cycles"]:
                entry["structural_packet_cycles"].append(packet_cycle)
                entry["structural_packets_dispatched"] = (
                    entry["structural_packets_dispatched"] + 1
                )
                counted.append(key)
        data["updated_at"] = _now()
    return counted


def _class_drew_live_in_cycle(defects: list, class_key: str, cycle: int) -> bool:
    """Did `class_key` draw a LIVE-or-untiered instance stamped `cycle`?

    ST-001 counts cycles in which the class drew ZERO LIVE instances, and
    LATENT instances explicitly do not reset the count — that exemption is the
    whole mechanism. A prover re-filing the same class at a finer boundary every
    cycle, never driving a live instance, is precisely the thunder-viper
    behaviour the exit exists to terminate, and if a LATENT filing reset the
    counter the class could be held open forever by findings nobody reproduced.
    An untiered record counts as LIVE here for the same reason it blocks every
    gate (FR-051): nobody classified it, so it is not evidence of a clean cycle.

    Both the filing cycle and the reopen cycle count. A class REOPENING is the
    class recurring, which is what escalation exists to catch, and a regression
    landing in cycle N is emphatically not a cycle in which N drew nothing.

    Reads the SERVER stamp (`cycle` / `reopened_in_cycle`) and never
    `declared_cycle`: the caller's asserted cycle is audit data kept beside the
    authority, and accumulating against it is how escalation counted wrong while
    the server counter sat at 0 (D-119).
    """
    resolved = _resolve_defect_classes(defects)
    for d in defects:
        if not isinstance(d, dict) or resolved.get(id(d)) != class_key:
            continue
        if defect_tier(d) == "LATENT":
            continue
        for field in ("cycle", "reopened_in_cycle"):
            value = d.get(field)
            if isinstance(value, int) and not isinstance(value, bool) and value == cycle:
                return True
    return False


def _clean_arm_step(
    entry: dict, completed_cycle: int, drew_live: Callable[[int], bool]
) -> bool:
    """ST-001's clean arm applied to ONE closed cycle. True when it CLEARS.

    Mutates `entry`'s `live_clean_cycles` and `live_clean_cycles_counted` in
    place; `drew_live` is called at most once, with `completed_cycle`, and only
    after the two guards have admitted the cycle — so a caller projecting a
    hypothetical future passes a constant and a caller judging a real crossing
    passes the ledger read.

    D-157 — ONE DERIVATION OF "HOW FAR IS THE CLEAN ARM", NOT TWO.
    -------------------------------------------------------------
    This body was inline in `_advance_escalation_exits`, and
    `_escalation_exit_distances` — the sentence the DONE refusal and the F2
    notice both print — computed the SAME number a second way, as
    `max(0, LIVE_CLEAN_CYCLES_TO_CLEAR - entry["live_clean_cycles"])`. That
    expression consults neither guard below, so the two derivations disagree in
    exactly the state AC-002 describes. Driven on AC-002's own fixture (a class
    escalated at cycle 5, one LATENT instance per cycle at a finer boundary,
    counter at 5): `_class_drew_live_in_cycle(defects, key, 5)` is False, so
    cycle 5 drew zero LIVE instances, and the hint said "2 more INSPECT
    cycle(s)". The arm needs THREE crossings from there — the one closing cycle
    5 is discarded by the escalated-before guard, the one closing 6 banks one,
    and only the one closing 7 clears. The lead was told a distance the arm it
    names would not honour.

    So the distance is now WALKED THROUGH THIS FUNCTION
    (`_clean_arm_crossings_left`) rather than computed beside it. A guard added
    here changes both answers at once, which is the only arrangement in which
    they cannot come apart.
    """
    # ST-001's guard: "the class must have been escalated before the two
    # cycles began". The cycle a class escalated ON is the cycle whose
    # third consecutive filing escalated it, so it is by construction not
    # a clean one, and counting cycles at or before it would let a class
    # clear on history that predates the escalation entirely.
    escalated_at = entry.get("escalated_at_cycle")
    if not isinstance(escalated_at, int) or completed_cycle <= escalated_at:
        return False
    # D-057: at most once per closed cycle, whichever way it goes.
    if completed_cycle in entry["live_clean_cycles_counted"]:
        return False

    # D-112 — "CONSECUTIVE" IS A TEST THIS ARM DID NOT MAKE.
    # -----------------------------------------------------
    # FR-003 is Locked and verbatim: "Two consecutive INSPECT cycles
    # with zero LIVE instances of the class", and ST-001 repeats "the
    # second CONSECUTIVE INSPECT cycle". `live_clean_cycles` was a bare
    # accumulator with no adjacency test at all, so a class cleared on
    # two clean cycles separated by cycles that drew LIVE instances of
    # it.
    #
    # TWO WAYS THE RUN BREAKS, AND ONLY ONE OF THEM IS A LIVE DRAW.
    # `_persisted_escalations` filters out an operator-overridden class,
    # so every boundary crossed while an `escalation-override` directive
    # is active is skipped ENTIRELY — the arm is never reached, so a
    # LIVE-draw reset could never fire for those cycles, and the
    # accumulator survived them untouched. Driven: class FDC escalated at
    # cycle 3, clean at 4, then Foundry-Directive('escalation-override:
    # FDC') — the marker the server's OWN structural proposal prints —
    # held across cycles 5, 6 and 7, each of which drew a LIVE instance
    # (D-004, D-005, D-006). After Foundry-Clear-Directives the next
    # crossing recorded status CLEARED, exit_reason clean_cycles,
    # cleared_at_cycle 9, live_clean_cycles 2,
    # live_clean_cycles_counted [4, 8] — stamped on the clean arm while
    # six LIVE instances stood open and three intervening cycles had
    # drawn them.
    #
    # So adjacency is tested against the record that already says which
    # cycles were EVALUATED. A gap in that list means cycles passed this
    # arm never judged, and a streak cannot be claimed across them.
    counted = [
        c for c in entry["live_clean_cycles_counted"]
        if isinstance(c, int) and not isinstance(c, bool)
    ]
    # The cycle the class escalated ON is the last one before counting
    # starts, so it is the anchor an empty list measures adjacency from.
    last_counted = max(counted) if counted else escalated_at
    contiguous = completed_cycle == last_counted + 1

    if drew_live(completed_cycle):
        # The streak ENDS here: this cycle is evaluated and dirty. The
        # counted list is reset to this cycle alone so the next crossing
        # measures adjacency from the break rather than from a clean
        # cycle on the far side of it.
        entry["live_clean_cycles"] = 0
        entry["live_clean_cycles_counted"] = [completed_cycle]
    elif not contiguous:
        # A clean cycle, but cycles between it and the last evaluated
        # one were never judged. The streak we can VOUCH for is this
        # cycle alone, so it RESTARTS at 1 rather than resuming at
        # whatever the accumulator held — and rather than at 0, which
        # would assert this cycle was dirty when it was not.
        entry["live_clean_cycles"] = 1
        entry["live_clean_cycles_counted"] = [completed_cycle]
    else:
        entry["live_clean_cycles"] = entry["live_clean_cycles"] + 1
        entry["live_clean_cycles_counted"].append(completed_cycle)

    return entry["live_clean_cycles"] >= LIVE_CLEAN_CYCLES_TO_CLEAR


def _clean_arm_crossings_left(
    entry: dict, next_completed_cycle: int, escalated_at: object
) -> int | None:
    """How many more crossings ST-001's clean arm needs, WALKED not computed.

    `next_completed_cycle` is the cycle the NEXT crossing will close — which is
    `_current_cycle(fdir)`, since `inspect_start` reads the counter before it
    advances. `escalated_at` is the cycle the class escalated on, taken from the
    persisted entry when it has one and from the ledger derivation when it does
    not (a class with no `escalation.json` record yet has that value latched by
    `_record_escalation_proposals` at the very next crossing, before the arm
    runs, so it is the value the arm will see).

    Returns None when the arm cannot advance at all — no escalation cycle is
    knowable, so there is no number to state and the caller must say that
    instead of printing one.

    D-157: every future cycle is assumed CLEAN, which is what "distance to the
    exit" means — the shortest walk from here. The walk is bounded because each
    crossing consumes one cycle number, the escalated-before guard can skip only
    the cycles at or before `escalated_at`, and the already-counted guard can
    skip only cycles the record already lists.
    """
    if not isinstance(escalated_at, int) or isinstance(escalated_at, bool):
        return None
    probe = {
        "escalated_at_cycle": escalated_at,
        "live_clean_cycles": entry["live_clean_cycles"],
        "live_clean_cycles_counted": list(entry["live_clean_cycles_counted"]),
    }
    completed = next_completed_cycle
    ceiling = (
        max(0, escalated_at - next_completed_cycle + 1)
        + len(probe["live_clean_cycles_counted"])
        + LIVE_CLEAN_CYCLES_TO_CLEAR
    )
    for crossings in range(1, ceiling + 1):
        cleared = _clean_arm_step(probe, completed, lambda _c: False)
        completed += 1
        if cleared:
            return crossings
    return None


def _advance_escalation_exits(
    fdir: Path, project_root: str, completed_cycle: int, boundary_cycle: int
) -> list[dict]:
    """Apply BOTH escalation exit arms at the INSPECT boundary (ST-001/ST-002).

    Called from `foundry_mark_phase_complete("inspect_start")` and nowhere else.
    THAT is the point: both arms are stated in terms of CYCLES — "two
    consecutive INSPECT cycles" and "the second structural packet CLOSED" — and
    the boundary crossing is the only event that knows a cycle has ended.

    `completed_cycle` is the counter BEFORE the increment: the cycle whose
    INSPECT and GRIND have just finished, so every filing that cycle will ever
    receive is already in the ledger, and any structural packet dispatched in it
    has now closed. `boundary_cycle` is the counter AFTER the crossing, which is
    what `cleared_at_cycle` records for both arms — the exit is applied BY this
    boundary, and dating it to the cycle that just ended would put the exit
    inside the cycle whose work produced it.

    D-057 — THE CLEAN ARM COUNTED CALLS, NOT CYCLES.
    ------------------------------------------------
    `foundry_mark_phase_complete` increments `state["cycle"]` only when the
    previous phase was F3, but called the clean arm UNCONDITIONALLY, and the arm
    did `live_clean_cycles += 1` with no record of which cycles it had already
    counted. The budget arm has exactly the idempotence this lacked
    (`structural_packet_cycles`, guarded by `if packet_cycle not in ...`).
    Driven through real doors only: escalate at cycle 3, one honest crossing,
    then two further `Foundry-Phase(inspect_start)` calls (phase already F2, so
    the counter does not move, both ok) -> `live_clean_cycles` reached 2 and the
    class CLEARED with `clean_cycles` after ONE real cycle had ended. Reachable
    on the guided path, because `inspect_start` had no phase precondition and
    both Foundry-Next and Foundry-Context re-arm the ordering token — and every
    clean-arm test drove `_cross_boundary`, which force-writes phase F3 first,
    so the suite only ever walked the honest path.

    `live_clean_cycles_counted` is the fix and is the budget arm's guard one
    field over: a closed cycle is EVALUATED AT MOST ONCE, whichever way it goes.
    A cycle that drew a LIVE instance is recorded as counted too — it has been
    evaluated, and re-evaluating it on a second call would be the same defect
    with the sign flipped.

    D-043: the roster is the PERSISTED one — every class `escalation.json`
    records as ESCALATED — and not what `_escalated_classes` returns. The
    difference is a class whose instances have all been fixed: it has no open
    work, so `_escalated_classes` drops it, so it used to reach these arms never
    and sat at ESCALATED for the rest of the run while the F6 report called it
    unresolved. It has zero LIVE instances by construction, which is precisely
    the condition ST-001 counts, so it advances a clean cycle at every crossing
    and CLEARS with `clean_cycles` like any other quiet class.

    Returns the list of classes that CLEARED on this crossing, so the transition
    can report them.
    """
    recorded = _load_json(fdir / ESCALATION_FILENAME).get("classes", {})
    if not isinstance(recorded, dict):
        recorded = {}
    if not _persisted_escalations(fdir, project_root, recorded):
        return []
    defects = _load_json(fdir / "defects.json").get("defects", [])
    buckets = _class_buckets(defects)

    cleared: list[dict] = []
    with _document_transaction(fdir / ESCALATION_FILENAME) as data:
        classes = data.setdefault("classes", {})
        if not isinstance(classes, dict):
            classes = data["classes"] = {}
        for key in _persisted_escalations(fdir, project_root, classes):
            entry = classes[key]
            # D-212: the roster now carries a class whose entry is not a
            # mapping, because such an entry reads as ESCALATED rather than
            # vanishing. `_escalation_entry_defaults` calls `setdefault` on it.
            # Normalised exactly as `_record_escalation_proposals` already
            # normalises the same document, so the arms can advance it and the
            # class reaches a bounded exit instead of blocking DONE forever. A
            # non-mapping entry carries no field worth preserving.
            if not isinstance(entry, dict):
                entry = classes[key] = {}
            _escalation_entry_defaults(entry)

            def _clear(reason: str) -> None:
                info = _class_info(
                    key, buckets.get(key) or _empty_class_bucket(key), classes
                )
                entry["status"] = ESCALATION_STATUS_CLEARED
                entry["exit_reason"] = reason
                entry["cleared_at_cycle"] = boundary_cycle
                entry["open_latent_defect_ids"] = info["open_latent_defect_ids"]
                cleared.append({
                    "class": key,
                    "exit_reason": reason,
                    "cleared_at_cycle": boundary_cycle,
                    "structural_packets_dispatched": entry[
                        "structural_packets_dispatched"
                    ],
                    "live_clean_cycles": entry["live_clean_cycles"],
                    "open_live_defect_ids": info["open_live_defect_ids"],
                    "open_latent_defect_ids": info["open_latent_defect_ids"],
                })

            # ST-002's arm, evaluated first. The budget is exhausted when the
            # STRUCTURAL_PASS_BUDGET-th packet has CLOSED, and a packet closes
            # when the GRIND cycle it was dispatched in ends — which is the
            # cycle this boundary has just closed, or an earlier one.
            packet_cycles = sorted(
                c for c in entry["structural_packet_cycles"]
                if isinstance(c, int) and not isinstance(c, bool)
            )
            if len(packet_cycles) >= STRUCTURAL_PASS_BUDGET and (
                completed_cycle >= packet_cycles[STRUCTURAL_PASS_BUDGET - 1]
            ):
                _clear("budget")
                continue

            # D-157: the arm's guards, the adjacency test and the accumulator
            # all live in `_clean_arm_step` now, because the DONE refusal's
            # "N more crossings" sentence walks that same function to state its
            # distance. Two derivations of one number is what D-157 filed.
            if _clean_arm_step(
                entry,
                completed_cycle,
                lambda c, _key=key: _class_drew_live_in_cycle(defects, _key, c),
            ):
                _clear("clean_cycles")
        data["updated_at"] = _now()
    return cleared


# --- Defect lifecycle ---


# FR-010 / AC-013 — ONE normalisation layer for "is this the defect's own path?"
#
# Both ladders below ask that question, and both used to answer it with raw
# string equality against the two fields a defect record happens to carry. Two
# defects came out of that in one cycle and they are the same shape twice: a
# comparison site left on bytes while the protocol writes something richer.
#
#   D-088  `symbol` carries the durable `path#Symbol` cite form FR-004/AC-005
#          mandate and the four stream agent files instruct — 26 of this run's
#          own 87 records (30%) spell it that way. `_test_ref_name` reduces a
#          reference to a BARE leaf, so the own-symbol rule compared a bare
#          leaf to a path-qualified string and could NEVER be equal: AC-013's
#          distinctness rule was dead for 30% of real filings, and honouring
#          the cite policy was what disabled the fix gate. Driven as a matched
#          pair (same defect, same reference, only the symbol's shape moved):
#          `evict_stale` -> REFUSED, `src/auth/sweeper.py#evict_stale` ->
#          ACCEPTED.
#   D-089  a test INSIDE the defect's own file (`aaa/aaa.py::test_x`) cleared
#          the own-file rule, which compared the WHOLE reference to the WHOLE
#          path, so the `::test_x` suffix was enough to walk past it. And in
#          the other direction `./adjaa/aaa.py::test_x` — a legitimate relative
#          spelling of a genuinely adjacent path — was refused as "a separator
#          that delimits nothing", identically to a reference that really did
#          dangle. Normalisation therefore runs BEFORE the shape ladder, or the
#          adjacent-path answer cannot be written in the form a teammate types.
#
# Every comparison site is bound to these helpers — the reference's file, the
# reference's name, the statement's own-file and own-symbol restatements, and
# the paths a statement names — so a future edit cannot leave one of them on
# raw equality again. That binding is the point: this run's repeated failure is
# never one bad rule, it is one rule fixed in a single copy.
#
# Lexical, never filesystem. `citation.symbol_cite_resolves` exists and is
# deliberately NOT used here, for the same reason the reference ladder refuses
# to stat anything (see its comment below): the run's tests live in a target
# repo at paths this server cannot resolve, and a false refusal blocks a real
# fix behind an unfalsifiable gate. Parsing is shared with the cite grammar
# (`citation.iter_symbol_cites`) rather than re-typed, so `path#Symbol` means
# one thing in this repo.
_LINE_HINT_SUFFIX = re.compile(r":\d+(?:-\d+)?$")
_LEADING_RELATIVE = re.compile(r"^(?:\.{1,2}[/\\])+")
#: Trailing sentence punctuation on a path lifted out of prose — `tools/.` is
#: the directory plus a full stop, not a path segment named ".".
_PATH_TRAILING_PUNCT = ".,;:!?)'\""


def _strip_line_hint(value: str) -> str:
    """Drop a trailing ``:42`` / ``:42-68`` hint. AC-007: never compared."""
    return _LINE_HINT_SUFFIX.sub("", value.strip())


def _normalize_path(value: str) -> str:
    """Fold a path to the single form every own-path comparison judges.

    Drops a line hint and trailing sentence punctuation, folds ``\\`` to ``/``,
    drops a leading ``./`` or ``../``, collapses doubled slashes, drops a
    trailing slash, and casefolds. ``./src/Auth/Session.py:42`` and
    ``src/auth/session.py`` are the same path to this gate, and D-089 is what
    happens when they are not.
    """
    path = _strip_line_hint(value).rstrip(_PATH_TRAILING_PUNCT)
    path = path.replace("\\", "/")
    path = _LEADING_RELATIVE.sub("", path)
    path = re.sub(r"/{2,}", "/", path)
    return path.rstrip("/").casefold()


def _own_symbol_name(own_symbol: str) -> str:
    """The BARE symbol a ``symbol`` field names, whatever shape it was written in.

    ``src/auth/sweeper.py#evict_stale`` -> ``evict_stale``; ``evict_stale`` ->
    ``evict_stale``. D-088: the second spelling was judged and the first was
    not, though FR-004 asks every stream to write the first.
    """
    raw = _strip_line_hint(own_symbol)
    if not raw:
        return ""
    cites = iter_symbol_cites(raw)
    if cites:
        return cites[0]["symbol"]
    # A cite whose extension the grammar does not whitelist still splits at the
    # separator the protocol reserves for exactly this.
    if "#" in raw:
        raw = raw.rsplit("#", 1)[1]
    return _strip_line_hint(raw)


def _own_paths(own_file: str, own_symbol: str) -> set[str]:
    """Every normalised path the defect's own location names.

    The ``file`` field is one. A ``path#Symbol`` ``symbol`` field carries
    another — and on the records where ``file`` was left empty it is the only
    one there is, which is why both fields are read rather than just the
    obvious one.
    """
    paths = {_normalize_path(own_file)} if own_file.strip() else set()
    for cite in iter_symbol_cites(_strip_line_hint(own_symbol)):
        paths.add(_normalize_path(cite["file"]))
    paths.discard("")
    return paths


def _normalize_ref(ref: str) -> str:
    """Strip a leading ``./`` / ``../`` from a reference before it is judged.

    D-089's second half. The empty-segment rule reads a relative prefix as a
    dangling separator, so ``./adjaa/aaa.py::test_x`` was refused identically
    to ``./test`` — the relative spelling of a real adjacent path could not be
    written at all. Only the leading prefix is touched: ``./test`` still fails,
    now on the rule that actually applies to it (it names no location).
    """
    return _LEADING_RELATIVE.sub("", ref.strip())


def _ref_file_component(ref: str) -> str:
    """The FILE a reference names, or a bare qualified-name head.

    ``aaa/aaa.py::test_x`` -> ``aaa/aaa.py``; ``src/auth/s.py#refresh`` ->
    ``src/auth/s.py``; ``auth::sweeper::tests::x`` -> ``auth``, which is not a
    file and simply matches no own path.
    """
    head = ref.split("::", 1)[0]
    if "#" in head:
        head = head.split("#", 1)[0]
    return head


# AC-013 / FR-010 — what makes an `adjacent_path_test` a REAL reference.
#
# The gate examined only the statement, by exact equality against the defect's
# own symbol, and never looked at the test reference at all. Driven and
# accepted before this: "n/a", "TODO", "tested it manually", and a test named
# for the defect's own symbol. A-018 asks for "a reference to a test exercising
# at least one adjacent path" — a string that references no test satisfies the
# gate's letter and none of its purpose.
#
# Structural rules, each decidable from the string alone, each killing values
# observed being accepted:
#
#   1. A reference is a LOCATOR, not a sentence — no internal whitespace.
#      Kills "tested it manually".
#   2. It must carry a locator separator (:: / \ # or .). Kills "TODO".
#   3. It must name a test — a "test" or "spec" token somewhere. Kills "n/a",
#      which clears rule 2 on its slash while referencing nothing.
#   4. Every locator segment must be non-empty — no leading separator, no
#      trailing separator, no doubled separator. Kills "tests/", ".test",
#      "test.", "./test" and "spec.", each of which clears rules 2 and 3 on a
#      separator that delimits nothing (D-050).
#   5. The LEAF must survive normalisation as a name. Take the last part after
#      the qualified-name separators, drop one trailing file extension, strip
#      test/spec scaffolding affixes, and what remains must be a name of at
#      least _TEST_REF_MIN_NAME_CHARS characters that is not a placeholder
#      token. Kills "src/foo.py::test_" (strips to nothing), "x.test",
#      "a.spec", "t.test", "test/x" (one-character names), and
#      "manual-test/none", "foo.test.bar", "no.test.exists" (placeholder and
#      negation names) — all of which cleared rules 1-4 (D-050).
#
# Then one semantic rule, mirroring the statement check's existing philosophy
# (exact equality is the one thing decidable here): the reference must not name
# ONLY the path the defect was found on.
#
# Deliberately NOT a filesystem existence check. The run's tests live in the
# target repo at paths this server cannot resolve reliably — monorepo roots,
# language-specific discovery, tests generated at build time — and a false
# refusal here blocks a real fix behind an unfalsifiable gate. These rules
# reject non-answers; they do not certify that the test exists or passes.
#
# Kept deliberately language-agnostic: foundry runs against Go, JS and Rust
# repos, so `path::name`, `path/to/file.ext`, `Class#method` and dotted module
# paths all clear every rule. Each rule was checked against the cross-language
# accept fixture in tests/test_fix_gate.py before being added — a rule that
# refuses `auth::sweeper::tests::evicts_stale_sessions` or
# `src/auth/__tests__/sweeper.test.ts` is a worse defect than the one it fixes.
_TEST_REF_LOCATOR_CHARS = ("::", "/", "\\", "#", ".")
_TEST_REF_NAMES_A_TEST = re.compile(r"test|spec", re.IGNORECASE)
# The separators that split a QUALIFIED NAME into parts. `.` is excluded: it
# separates a file extension and a dotted module path alike, so the leaf of
# `src/auth/sweeper.spec.ts` is the whole `sweeper.spec.ts`, normalised below.
_TEST_REF_PART_SEPARATORS = ("::", "#", "/", "\\")
# Scaffolding affixes stripped before judging a test's NAME and before
# comparing it to the defect's own symbol, so `test_refresh_session` is
# recognised as naming `refresh_session` and `sweeper.test` as naming
# `sweeper`. `.` joined `_` and `-` here for the dotted JS/TS convention.
_TEST_NAME_AFFIX = re.compile(
    r"^(?:tests?|specs?|it)[_\-.]+|[_\-.]+(?:tests?|specs?)$", re.IGNORECASE
)
# One trailing file extension, dropped before affix stripping: `.py`, `.go`,
# `.ts`, `.rb`. Bounded at 6 characters so a dotted module path's final
# component is not mistaken for an extension.
_TEST_REF_EXTENSION = re.compile(r"\.[A-Za-z0-9]{1,6}$")
_TEST_REF_MIN_NAME_CHARS = 2
# Names that reference nothing. Every one of these was driven through the gate
# and accepted (D-050): `manual-test/none`, `foo.test.bar`, `no.test.exists`.
# Single characters are covered by _TEST_REF_MIN_NAME_CHARS and are not
# repeated here.
_PLACEHOLDER_NAMES = frozenset({
    "aa", "xx", "xxx", "asdf", "blah",
    "foo", "bar", "baz", "qux", "quux",
    "na", "nil", "null", "none", "no", "not", "nope", "nothing", "nada",
    "tbd", "todo", "fixme", "wip", "pending", "unknown", "unclear",
    "manual", "manually", "dummy", "fake", "placeholder", "example",
    "sample", "temp", "tmp",
})  # 34 names


def _locator_segments(ref: str) -> list[str]:
    """Split ``ref`` on every locator separator, ``::`` counting as one."""
    sentinel = "\x00"
    normalized = ref.replace("::", sentinel)
    for sep in ("/", "\\", "#", "."):
        normalized = normalized.replace(sep, sentinel)
    return normalized.split(sentinel)


def _test_ref_name(ref: str) -> str:
    """The bare NAME a reference resolves to, or "" if it names nothing.

    Leaf of the qualified name, minus one trailing file extension, minus
    test/spec scaffolding affixes. ``tests/test_auth.py`` -> ``auth``;
    ``src/auth/sweeper.spec.ts`` -> ``sweeper``; ``src/foo.py::test_`` -> "".
    """
    leaf = ref
    for sep in _TEST_REF_PART_SEPARATORS:
        if sep in leaf:
            leaf = leaf.rsplit(sep, 1)[1]
    leaf = _TEST_REF_EXTENSION.sub("", leaf)
    # Repeat to a fixed point so `sweeper.test` and `spec_helper_test` both
    # reduce, and a doubly-affixed name does not keep half its scaffolding.
    for _ in range(4):
        stripped = _TEST_NAME_AFFIX.sub("", leaf)
        if stripped == leaf:
            break
        leaf = stripped
    return leaf


# FR-010's load-bearing word: the test must drive a NAMED adjacent path — one
# the STATEMENT named. A-017 defines the statement as "who else calls this /
# what else transitions here / what runs concurrently", so the two declarations
# are a matched pair: the statement names the paths, the reference drives one
# of them.
#
# D-092: that coupling did not exist. `_test_ref_problem` took (ref, own_symbol,
# own_file) — the statement was not a parameter and was never read when judging
# the reference — so `foundry_mark_defect_fixed` ran two INDEPENDENT checks and
# never related them. Driven through server.py#_DISPATCH["Foundry-Fix"] against
# a defect on `refresh_session`: statement "login_handler also calls this and
# the sweeper runs concurrently", reference
# "tests/test_billing.py::test_invoice_totals_round_half_up" -> ACCEPTED. The
# statement named two adjacent paths and the referenced test drove neither.
#
# The rule is deliberately the weakest one that closes that: share ONE token
# and the reference is accepted. Refusing a real answer is this gate's
# characteristic failure — it is what D-076 and D-085 both were, and it fires
# in GRIND where the teammate has no way around it — so every judgement call
# here is resolved toward accepting:
#
#   * ANY overlap accepts. Not a majority, not the leading token, one token.
#   * It is the LAST rung, reached only after the whole structural ladder and
#     only when the statement itself cleared its own ladder (see the caller):
#     relating a refused statement would report the reference as unlinked when
#     the real problem is the statement the caller is already being told about.
#   * It judges only a reference that names a test FUNCTION. A reference that
#     names a whole test FILE names a container, and a container's name is not
#     a claim about which path is driven — judging it would be asserting
#     something the reference never said. That is the same ceiling the rules
#     above keep ("these reject non-answers; they do not certify"), and it is
#     why `tests/test_auth.py` stays acceptable against any statement.
#
# The tokens compared are the discriminating ones: locator scaffolding and the
# filler that appears in a path and in a sentence without linking them is
# dropped, because an overlap on "test" or "the" is not evidence of anything.
_LINKAGE_MIN_TOKEN_CHARS = 3
_LINKAGE_TOKEN_SPLIT = re.compile(r"[^A-Za-z0-9]+")
_LINKAGE_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
# Words a locator and a sentence share without the sharing meaning anything:
# test scaffolding, the conventional source directories, this gate's own
# vocabulary (every statement says "calls"/"path"/"concurrently"), and ordinary
# English filler. Dropping a word makes the rule STRICTER, so this set is kept
# to words that genuinely carry no linkage rather than extended for tidiness.
_LINKAGE_STOPWORDS = frozenset({
    # locator scaffolding and conventional roots
    "test", "tests", "testing", "spec", "specs", "src", "lib", "pkg",
    "internal", "cmd", "app", "main", "index",
    # this gate's own vocabulary — present in nearly every statement
    "path", "paths", "adjacent", "defect", "defects", "call", "calls",
    "called", "caller", "callers", "run", "runs", "running", "concurrent",
    "concurrently", "transition", "transitions", "code", "file", "files",
    "function", "functions", "method", "methods", "module", "modules",
    "line", "lines", "name", "named", "names", "check", "checks", "fix",
    "fixed", "branch", "case", "cases",
    # ordinary English filler
    "the", "and", "but", "for", "not", "are", "was", "were", "has", "have",
    "had", "its", "this", "that", "these", "those", "there", "their", "they",
    "them", "with", "from", "into", "onto", "also", "both", "all", "any",
    "some", "more", "most", "only", "just", "then", "than", "when", "where",
    "which", "while", "who", "what", "how", "why", "does", "did", "done",
    "can", "will", "would", "should", "could", "been", "being", "one", "two",
    "still", "same", "other", "others", "another", "each", "every", "via",
    "per", "out", "off", "yet", "now", "new", "old", "use", "used", "uses",
    "using", "here", "else", "way", "ways", "thing", "things",
})  # 127 words


def _content_tokens(text: str) -> set[str]:
    """The discriminating word-parts of ``text``, casefolded.

    Splits on every non-alphanumeric character and again at camelCase
    boundaries, so ``TestSweeperEvictsStale`` and ``test_sweeper_evicts_stale``
    yield the same set. Scaffolding, filler and anything under
    ``_LINKAGE_MIN_TOKEN_CHARS`` characters is dropped.
    """
    parts: list[str] = []
    for chunk in _LINKAGE_TOKEN_SPLIT.split(text):
        if chunk:
            parts.extend(_LINKAGE_CAMEL_BOUNDARY.split(chunk))
    return {
        folded
        for folded in (part.casefold() for part in parts)
        if len(folded) >= _LINKAGE_MIN_TOKEN_CHARS
        and folded not in _LINKAGE_STOPWORDS
    }


def _ref_singles_out_a_leaf(ref: str) -> bool:
    """True when the reference singles out a NAME INSIDE a file, not the file.

    The leaf of the qualified name carries no file extension:
    ``tests/test_auth.py::test_sweeper`` and ``auth::sweeper::tests::evicts``
    do, ``tests/test_auth.py`` and ``sweeper.spec.ts`` do not.

    D-065 — NAMED FOR WHAT IT MEASURES. This was called
    ``_ref_names_a_test_function`` and its whole body is
    ``_TEST_REF_EXTENSION.search(leaf) is None`` — "the leaf has no file
    extension". Nothing here asks whether the target is a TEST, and
    ``_regression_test_problem`` read the old name as though it did: it called
    this rung and NO other, so the LATENT lane accepted
    ``src/auth/session.py::refresh_session`` — the defect's own production
    symbol in its own file — as the regression test that holds the fix. The
    name is the whole of the defect; a predicate whose name overstates it is
    read as a check its caller never made.
    """
    leaf = ref
    for sep in _TEST_REF_PART_SEPARATORS:
        if sep in leaf:
            leaf = leaf.rsplit(sep, 1)[1]
    return _TEST_REF_EXTENSION.search(leaf) is None


def _linkage_problem(ref: str, statement: str) -> str | None:
    """D-092: name why ``ref`` drives no path ``statement`` named, else None.

    A pure string relation between two caller-supplied fields — no I/O, and no
    claim that either names anything real. See the block above for why every
    branch here resolves toward accepting.
    """
    if not statement or not _ref_singles_out_a_leaf(ref):
        return None
    ref_tokens = _content_tokens(ref)
    statement_tokens = _content_tokens(statement)
    if not ref_tokens or not statement_tokens or (ref_tokens & statement_tokens):
        return None
    named = ", ".join(sorted(statement_tokens)[:8])
    drives = ", ".join(sorted(ref_tokens)[:8])
    return (
        f"{ref!r} drives none of the paths the statement named. The statement "
        f"names {named}; the reference names {drives}. FR-010 asks for a test "
        "that drives a NAMED adjacent path — one the adjacent_path_statement "
        "named — so reference the test that drives one of those, or name the "
        "path this test actually drives in the statement"
    )


def _test_ref_problem(
    ref: str,
    own_symbol: str,
    own_file: str,
    statement: str = "",
) -> str | None:
    """Name why ``ref`` is not a usable adjacent-path test reference, else None.

    Returns a reason string suitable for a named refusal. Never raises: every
    branch is a pure string test over the caller's own input. ``statement`` is
    the caller's adjacent-path statement when it has already cleared its own
    ladder, and enables the linkage rung (D-092); passing "" skips that rung.

    The refusals quote the caller's OWN spelling. Normalisation (D-089) decides
    the verdict and must never decide what the caller is shown, or a teammate
    reads a refusal about a string they did not write.
    """
    if any(ch.isspace() for ch in ref):
        return (
            f"{ref!r} is prose, not a test reference. Give a locator such as "
            "tests/test_auth.py::test_sweeper_evicts_stale_sessions."
        )
    # D-089: the relative prefix is dropped BEFORE the shape ladder, so
    # `./adjaa/aaa.py::test_x` is judged as the locator it is. `./test` still
    # fails — on the locator rule immediately below, which is the rule that
    # actually applies to it.
    normalized_ref = _normalize_ref(ref)
    if not any(sep in normalized_ref for sep in _TEST_REF_LOCATOR_CHARS):
        return (
            f"{ref!r} names no location. A test reference carries a path or a "
            "qualified name (path/to/test_file.py::test_name)."
        )
    if not _TEST_REF_NAMES_A_TEST.search(normalized_ref):
        return (
            f"{ref!r} does not name a test. The reference must point at a test "
            "file or test function."
        )
    if any(seg == "" for seg in _locator_segments(normalized_ref)):
        return (
            f"{ref!r} has a separator that delimits nothing — a dangling or "
            "doubled '/', '.' or '::'. A reference names a test, not a "
            "directory or an extension on its own."
        )

    # The only FILE the reference names is the one the defect was found in —
    # whether it stops there or singles out a test within it. D-089: this
    # compared the whole reference to the whole path, so `aaa/aaa.py` was
    # refused and `aaa/aaa.py::test_aaa_adjacent`, a test in the defect's own
    # file, walked straight past on the strength of its suffix.
    own_paths = _own_paths(own_file, own_symbol)
    ref_file = _normalize_path(_ref_file_component(normalized_ref))
    if own_paths and ref_file and ref_file in own_paths:
        return (
            f"{ref!r} names no file but the defect's own ({own_file or ref_file}), "
            "so it drives no path adjacent to the one the defect was found on. "
            "AC-013 asks for a test on a DIFFERENT caller, transition or "
            "concurrent interaction — reference the test that drives it, or "
            "name it as a qualified name rather than a path into this file."
        )

    name = _test_ref_name(normalized_ref)
    if not name:
        return (
            f"{ref!r} is test scaffolding with no test name attached — it "
            "strips to nothing. Name the test, not the prefix."
        )
    if len(name) < _TEST_REF_MIN_NAME_CHARS:
        return (
            f"{ref!r} resolves to {name!r}, which names nothing specific. The "
            "reference must identify a test, not a single letter beside the "
            "word 'test'."
        )
    if name.casefold() in _PLACEHOLDER_NAMES:
        return (
            f"{ref!r} resolves to the placeholder {name!r}, which references "
            "no test. A-018 asks for a test that EXERCISES an adjacent path; "
            "a well-formed string that points at nothing is the same "
            "non-answer as 'n/a'."
        )

    # Compare the reference's NAME to the defect's own symbol. Exact equality
    # only, so a test like test_refresh_session_from_login_handler (a genuinely
    # adjacent caller) still passes while test_refresh_session does not.
    #
    # D-088: both sides are normalised to a bare name first. `own_symbol` was
    # compared verbatim, so a record spelling it as `path/f.py#evict_stale` —
    # the durable form FR-004 mandates and 30% of this run's records use —
    # could never equal a bare leaf, and this rule was inert for exactly the
    # spelling the protocol asks for.
    own_name = _own_symbol_name(own_symbol)
    if own_name and name.casefold() == own_name.casefold():
        return (
            f"{ref!r} names a test for the defect's own symbol "
            f"({own_symbol}). AC-013 requires a test driving a NAMED "
            "adjacent path — a DIFFERENT caller, transition, or concurrent "
            "interaction than the one the defect was found on."
        )

    # Last rung (D-092): the reference must drive a path the STATEMENT named.
    # Reached only for a statement that already cleared its own ladder.
    return _linkage_problem(ref, statement)


# FR-009 / AC-013 — what makes an `adjacent_path_statement` a REAL answer.
#
# D-050: the test-reference ladder above was built and the statement side was
# left exactly as cycle 2 found it — one check, exact equality against the
# defect's own symbol. Driven and ACCEPTED after that fix landed: 'x', 'none',
# 'n/a', 'no adjacent paths', 'the same path', 'nothing', '-', '0'. A
# declaration that there IS no adjacent path satisfied a gate whose entire
# purpose is to make the fixer name one.
#
# A-017 asks the statement to name "who else calls this / what else transitions
# here / what runs concurrently". Three rules, in the order a caller most needs
# to hear them:
#
#   1. It must not restate the defect's own path — its own symbol or its own
#      file (the pre-existing rule, now covering both), nor say so in words:
#      "the same path", "same as the defect".
#   1b. D-085 is that literal rule's own over-correction, and it is D-076 one
#      pattern over. The literal form matched on "same" ALONE, so a statement
#      whose SUBJECT is a shared resource —
#
#          "The same index.lock is taken by the pathspec commit path and by
#           foundry_validate's git query, which is the concurrent interaction."
#
#      — was told it "declares that there is no adjacent path", which is the
#      opposite of what it says. Two distinct real paths meeting at one lock,
#      one file or one record is "what runs concurrently" answered exactly:
#      sharing the resource IS the adjacency. Moving the resource off the front
#      of the sentence was always accepted ("The pathspec commit path and
#      foundry_validate's git query both take index.lock…"), and that is what
#      proved the rule lexical rather than semantic — same two paths, same
#      claim, only the first word moved. The NOUN AFTER "same" is what carries
#      the restatement, so that is what the pattern reads.
#   2. It must not LEAD with a negation. A statement that opens by asserting no
#      other path exists is a refusal to answer, not an answer; note the gate is
#      already unsatisfiable in that case, because a fixer with no adjacent path
#      has no adjacent-path test to reference either. These patterns are
#      ^-anchored, so `test_no_duplicate_ids` inside a longer statement is
#      untouched.
#   2b. A negation LATER in the statement is refused only when it has bounded
#      nothing — see `_unbounded_denial` below. This is D-076, and it is rule
#      2's over-correction: the two adjacency patterns used to be UNANCHORED
#      whole-string searches sitting in the tuple above under a comment
#      claiming "Anchored patterns only", which was false of exactly those two.
#      They therefore refused the MOST rigorous form of the answer A-017 asks
#      for — an enumeration followed by a clause CLOSING it:
#
#          "_current_cycle is also called by foundry_get_context and
#           _format_status_display; no other module reads state.json directly,
#           so those two are the adjacent callers."
#
#      That names two real adjacent callers and then states the radius is
#      closed, and the gate rejected it as "declares that there is no adjacent
#      path" — in GRIND, the phase where every defect must close. The property
#      that separates it from a genuine non-answer is positional and is true of
#      the language rather than of punctuation: A BOUND COMES AFTER WHAT IT
#      BOUNDS. So a trailing denial is an answer when something was named
#      before it, and a refusal when nothing was.
#   3. It must carry enough substance to have named something —
#      _STATEMENT_MIN_WORDS words of at least two letters. Kills 'x', '-', '0',
#      'none', 'n/a', 'nothing', 'no adjacent paths' and 'the same path' on
#      length alone, and is the floor rules 2 and 2b sit on top of.
#
# Like the reference rules, these reject non-answers; they cannot certify that
# the named path is real. That is the ceiling of what a string check can do,
# and the run's own INSPECT streams are what verify the rest. Rule 1's literal
# form shows the ceiling plainly: it catches the restatement that OPENS on
# those two nouns and never caught one buried mid-sentence ("It is the same
# file as the defect" is accepted here, at HEAD and before it). Widening it
# back toward that case is what refuses real answers, which is D-085, so the
# missed non-answer is the side to err on.
_STATEMENT_MIN_WORDS = 4
_STATEMENT_WORD = re.compile(r"[A-Za-z][A-Za-z0-9_]+")
_STATEMENT_NON_ANSWERS = (
    # Leading negation: "no other callers", "none", "nothing else touches it",
    # "there are no adjacent paths", "not applicable".
    re.compile(r"^(?:there\s+(?:are|is)\s+)?(?:no|none|not|nothing|never)\b", re.I),
    # Rule 1's literal form: "the same path", "same as the defect". Narrowed
    # from `^(?:the\s+)?same\b` by D-085 — see rule 1b above. It sits in this
    # tuple for the anchoring it needs, not because it is a negation; the two
    # nouns are what restate the defect's own path.
    re.compile(r"^(?:the\s+)?same\s+(?:path|as)\b", re.I),
    re.compile(r"^n\s*/?\s*a$", re.I),
)
# Rule 2b (D-076). A negation of adjacency ANYWHERE in the statement. These are
# deliberately unanchored — a bound is not expected at the start — and are
# judged by `_unbounded_denial`, never by a bare whole-string search.
_STATEMENT_ADJACENCY_DENIALS = (
    re.compile(r"\bno\s+(?:other|adjacent|additional|further)\b", re.I),
    re.compile(r"\bnothing\s+else\b", re.I),
)


def _unbounded_denial(normalized: str) -> bool:
    """True when a denial of adjacency has named nothing for it to bound.

    D-076. The test is the text BEFORE the first denial: a statement that
    enumerated callers and then closed the radius has cleared the same
    substance floor rule 3 applies to the whole statement, while "I found no
    other callers" and "the grep shows no other callers" have not — they open
    with a subject and a verb and then decline to answer.

    Deliberately positional and not a clause tokenizer: splitting English on
    punctuation would have to guess at the '.' inside ``state.json`` and at how
    deep a comma nests, and would still accept "Also, no other module calls
    this" on one word of filler. Counting the words a denial had available to
    bound needs neither guess.
    """
    starts = [
        match.start()
        for match in (pattern.search(normalized) for pattern in _STATEMENT_ADJACENCY_DENIALS)
        if match is not None
    ]
    if not starts:
        return False
    bounded = _STATEMENT_WORD.findall(normalized[: min(starts)])
    return len(bounded) < _STATEMENT_MIN_WORDS


# A path NAMED inside a statement: anything carrying a directory separator, or
# a bare filename with an extension. Used only to ask D-089's question — "is
# every path this statement names the defect's own?" — which is a property of
# the WHOLE declaration and so cannot refuse a statement that also names
# something else. A token this over-matches (`e.g`, `tools/.`) can only make
# the rule fire LESS, which is the direction to be wrong in.
_STATEMENT_NAMED_PATH = re.compile(
    r"(?:[\w.\-]+[/\\])+[\w.\-]*"
    r"|[\w\-]+\.[A-Za-z0-9]{1,6}\b"
)


def _statement_problem(statement: str, own_symbol: str, own_file: str) -> str | None:
    """Name why ``statement`` is not a usable adjacent-path statement, else None."""
    normalized = " ".join(statement.split())
    folded = normalized.casefold()
    # D-088: both own-path comparisons read the normalised forms, so a record
    # whose `symbol` is spelled `path/f.py#refresh_session` is judged the same
    # as one spelled `refresh_session`. The statement side is normalised only
    # when it is a single token — running the cite parser over prose would let
    # a statement that MENTIONS a cite and then names a real adjacent caller
    # compare equal to the defect's own symbol, which is a false refusal and
    # the failure mode this gate has already had twice.
    own_name = _own_symbol_name(own_symbol)
    own_paths = _own_paths(own_file, own_symbol)
    single_token = " " not in normalized
    statement_name = _own_symbol_name(normalized) if single_token else normalized

    if own_name and statement_name.casefold() == own_name.casefold():
        return (
            f"it just names the defect's own symbol ({own_symbol}). An "
            "adjacent path is a DIFFERENT caller, transition, or "
            "concurrent interaction than the one the defect was found on"
        )
    if own_paths and single_token and _normalize_path(normalized) in own_paths:
        return (
            f"it just names the defect's own file ({own_file or normalized}), "
            "which is the path the defect was found on rather than one "
            "adjacent to it"
        )
    for pattern in _STATEMENT_NON_ANSWERS:
        if pattern.search(normalized):
            return (
                f"{normalized!r} declares that there is no adjacent path. That "
                "is a refusal to answer, not an answer — and a fix with no "
                "adjacent path has no adjacent-path test to reference either. "
                "Name who ELSE calls this, what else transitions here, or what "
                "runs concurrently"
            )
    if _unbounded_denial(normalized):
        # D-076: name the REMEDY, which is not "delete the denial". Closing the
        # radius is the strongest form of the answer — it just has to come
        # after the answer it closes.
        return (
            f"{normalized!r} denies that an adjacent path exists without first "
            "naming one, so the denial bounds nothing. A closing clause like "
            '"no other module reads it" is welcome — and is the most rigorous '
            "form of the answer — but it belongs AFTER the enumeration it "
            "closes. Name who ELSE calls this, what else transitions here, or "
            "what runs concurrently, and then bound it"
        )
    if len(_STATEMENT_WORD.findall(normalized)) < _STATEMENT_MIN_WORDS:
        return (
            f"{normalized!r} is too thin to have named a path. State who else "
            "calls this, what else transitions here, or what runs concurrently "
            f"— at least {_STATEMENT_MIN_WORDS} words naming real callers, "
            "transitions or concurrent work"
        )

    # Last rung (D-089): every path the statement names is the defect's own, so
    # however many words it spent, it named no path beside the one the defect
    # was found on. Deliberately a SUBSET test over the whole declaration and
    # not a search: a statement that names the own file alongside another path
    # — "login_handler in src/auth/session.py also calls this" — names a real
    # adjacent caller and is accepted. The lexical `same` pattern above is
    # untouched; widening THAT is D-085, and this rule reaches the mid-sentence
    # restatement it deliberately cannot without reading phrases.
    named_paths = {
        _normalize_path(match) for match in _STATEMENT_NAMED_PATH.findall(normalized)
    }
    named_paths.discard("")
    if own_paths and named_paths and named_paths <= own_paths:
        return (
            f"the only path it names is the defect's own ({own_file or own_symbol}). "
            "Name who ELSE calls this, what else transitions here, or what runs "
            "concurrently — a path beside the one the defect was found on, not "
            "the one it was found on restated"
        )
    return None


# --------------------------------------------------------------------------- #
# The LATENT fix lane (CT-004 / ST-003 / FR-008 / AC-011 / AC-012 / OT-007)
# --------------------------------------------------------------------------- #

#: The two shapes a `def`-line can take for a name the locator points at.
#: A test may be a function (`def test_evicts`) or a method on a `Test` class
#: (`class TestSweeper:` / `def test_evicts`), and both are what pytest collects.
_REGRESSION_DEF_TEMPLATES = ("def {name}", "class {name}", "async def {name}")


def _split_pytest_node_id(ref: str) -> tuple[str, list[str], str]:
    """``(file path, name chain, parameter id)`` for one pytest node id.

    A node id is spelled ``relpath::Name::Name[param id]`` and this splits it
    the way pytest composes it, rather than by looking for separators:

      * the FILE PATH is everything before the FIRST ``::`` — a path may
        contain ``/``, ``.`` and spaces, and none of them ends it;
      * the PARAMETER ID is the whole tail from the FIRST ``[`` to the end,
        and only when the id ends with ``]``. ``[`` cannot occur in a Python
        identifier, so "the name runs up to the first bracket" is the grammar
        and not a heuristic;
      * the NAME CHAIN is what is left between them, split on ``::`` — one
        element for a module-level test, two for a method on a ``Test`` class.
        The TEST's own name is the last element.

    D-105 / D-134 / D-145 — THE ESCALATED CLASS `false-refusal-diagnostic`, AS
    A PARSER RATHER THAN AS STRING SURGERY.
    -------------------------------------------------------------------------
    Three cycles filed the same shape: the LATENT lane refusing a node id
    ``pytest --collect-only`` actually emits, each time because the locator was
    cut with a different pair of string operations. D-134 stripped a trailing
    ``[...]`` — but only AFTER ``name_part.split('::')[-1]`` had already cut
    the id at a ``::`` INSIDE the brackets, and it never taught the
    whitespace rung that a bracket payload is not prose. Both survivors were
    driven at GRIND cycle 8:

        tests/test_fix_gate.py::test_real_test_references_across_languages
            _are_accepted[tests/test_auth.py::test_sweeper_evicts_stale_sessions]
            -> "tests/test_fix_gate.py exists but defines no
               'test_sweeper_evicts_stale_sessions]'"

        tests/test_fix_gate.py::test_a_latent_locator_that_names_no_test_is
            _refused[a::b-two letters either side of a separator]
            -> "is prose, not a locator"

    Both name a test this suite runs. THIRTEEN of this repo's collected node
    ids carry a ``::`` inside their brackets and every one of them guards this
    very lane, so the door refused the tests written to hold it.

    pytest's ``idmaker`` keeps a string parameter verbatim — ``::``, ``/``,
    spaces and brackets included — which is exactly why no amount of further
    cutting terminates. The payload is arbitrary caller text and the only
    stable facts about it are its two delimiters. So the id is PARSED once,
    here, and every rung downstream judges the locator's own text and never the
    parameter set.
    """
    param = ""
    head = ref
    open_at = ref.find("[")
    if open_at != -1 and ref.endswith("]"):
        param = ref[open_at:]
        head = ref[:open_at]
    path_part, separator, rest = head.partition("::")
    chain = [segment.strip() for segment in rest.split("::")] if separator else []
    return path_part.strip(), chain, param


#: Directories a locator's file is never found in and which dominate the walk
#: cost of looking for it. Pruned by name at every level (D-131).
_TEST_SEARCH_PRUNE = frozenset({
    ".git", ".hg", ".svn", "node_modules", ".venv", "venv", "__pycache__",
    ".mypy_cache", ".pytest_cache", ".ruff_cache", "dist", "build",
    ".tox", ".nox", "site-packages", ".worktrees",
})


def _resolve_test_path(path_part: str, project_root: str) -> Path | None:
    """The file a `path::test` locator's path names, wherever it is rooted.

    D-131 — THE LANE'S EXISTENCE RUNG FIRED ONLY FOR ONE SPELLING OF THE ROOT.
    -------------------------------------------------------------------------
    `_regression_test_problem`'s provable half read `Path(project_root) /
    path_part` and, when that did not resolve, returned None — "I could not
    find your file" was treated as "no problem". This run's teammates cite
    mcp-server-relative paths (`tests/test_fix_gate.py`) while `project_root`
    is the repository root, so the rung never fired for anyone. Driven on a
    LATENT defect: `Foundry-Fix(regression_test='tests/test_ghost.py::test_ghost')`
    returned ok True and closed the defect with no such file anywhere in the
    tree, and `tests/test_fix_gate.py::test_totally_absent_name` — a REAL file
    that defines no such test — was accepted for the same reason.

    ST-003's guard is that "the locator names a real test", and
    `agents/teammate.md` Step 7 promises the path must exist and the test must
    be a `def test_`-shaped symbol inside it. Neither survives a rung that only
    fires when the caller happened to root its path the way this process did.

    So the path is resolved against the root it is relative to, in order:

      1. as given, if absolute;
      2. `project_root / path_part`, the spelling that already worked;
      3. the unique file BENEATH `project_root` whose path ends with
         `path_part` — which is how an mcp-server-relative or
         plugin-relative locator resolves. Ambiguity is not resolution: two
         files ending the same way mean the locator does not single one out,
         and the caller is told so rather than having one guessed for it.

    LEAD RULING, GRIND cycle 7: this REPLACES the D-065 note that the
    filesystem check "remains an addition, never a precondition". That note
    reasoned from a server running against a TARGET repo whose tests it cannot
    resolve; step 3 resolves exactly that case, and the disagreement is
    recorded in the run's concerns.md.

    Returns None when the path resolves nowhere — a distinct answer from "it
    resolved and the test is missing", which is why the caller branches on both.
    """
    import os

    candidate = Path(path_part)
    if candidate.is_absolute():
        return candidate if candidate.is_file() else None

    direct = Path(project_root) / path_part
    if direct.is_file():
        return direct

    # Step 3. Walked rather than globbed so the heavy directories are pruned
    # before they are descended into; a repo-wide `rglob` over `.git` and
    # `node_modules` is seconds this door does not have.
    suffix = "/" + path_part.strip("/")
    basename = os.path.basename(path_part.rstrip("/"))
    if not basename:
        return None
    found: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(project_root):
        dirnames[:] = [
            d for d in dirnames
            if d not in _TEST_SEARCH_PRUNE and not d.startswith(".worktree")
        ]
        if basename not in filenames:
            continue
        hit = Path(dirpath) / basename
        if hit.as_posix().endswith(suffix):
            found.append(hit)
            if len(found) > 1:
                # Ambiguous: two roots both answer, so the locator singles out
                # neither. Treated as unresolved rather than as a coin flip.
                return None
    return found[0] if found else None


def _regression_test_problem(ref: str, project_root: str) -> str | None:
    """The named reason a `regression_test` locator is unusable, else None.

    CT-004 is precise about the size of this gate: "within the lane, refusal
    only when the locator is absent or DOES NOT NAME A TEST". It is NOT the
    adjacent-path ladder — no linkage to a statement, no ten-minute apparatus —
    because a LATENT fix has no adjacent-path statement to link against and
    ST-003 says none is demanded. What it is, and must be, is a check that the
    thing named is a test.

    D-065 — THE ONE FIELD THE LANE RESTS ON VALIDATED NOTHING.
    ---------------------------------------------------------
    This ladder's only "is it a test" rung was `_ref_names_a_test_function`,
    whose body is `_TEST_REF_EXTENSION.search(leaf) is None` — "the leaf has no
    file extension" and nothing more (it is now named
    `_ref_singles_out_a_leaf` for that reason). Driven against the real handler
    on a LATENT defect, all of these were ACCEPTED and closed the defect:

        src/auth/session.py::refresh_session   <- the defect's OWN production
                                                  symbol in its OWN file
        src/auth/session.py::helper
        src/nonexistent.py::whatever
        a::b
        the fix::works now

    Worse than accepted: `_REGRESSION_DEF_TEMPLATES` resolved
    `src/auth/session.py`, found `def refresh_session`, and actively CONFIRMED
    the broken production function as the regression test holding its own fix.

    The sibling LIVE-lane checker in this module already had the missing rungs
    and this function called neither. Three are added, in the order a caller
    most needs to hear them:

      1. prose — a locator with whitespace in it is a note, not a reference
         (`_test_ref_problem`'s first rung, same wording shape);
      2. the PATH must satisfy vocab's `is_test_file`, which is the repo's own
         pytest-discovery predicate (FR-034). A production module is not a
         place a regression test can live, however the leaf is spelled;
      3. the NAME must read as a test (`_TEST_REF_NAMES_A_TEST`, the same
         `test|spec` pattern the LIVE ladder applies) and must not be the
         defect's own symbol — a fix cannot be held by the function it fixed.

    D-131 — AND THE FILESYSTEM CHECK IS NOW A PRECONDITION, BECAUSE IT CAN BE.
    -------------------------------------------------------------------------
    This block used to read "THE FILESYSTEM CHECK REMAINS AN ADDITION, NEVER A
    PRECONDITION", on the reasoning that a server running against a TARGET repo
    frequently cannot resolve its tests and "I could not find your file" is
    unfalsifiable from where the caller stands. The consequence was that the
    one field the lane rests on validated nothing whenever the caller rooted
    its path differently from this process: `tests/test_ghost.py::test_ghost`
    closed a LATENT defect with no such file in the tree.

    `_resolve_test_path` removes the premise — it resolves the locator against
    the root it is relative to, including by unique suffix beneath
    `project_root` — so an unresolvable path now means the file is not there,
    and ST-003's "the locator names a real test" is enforceable. LEAD RULING,
    GRIND cycle 7; the superseded note is recorded in the run's concerns.md.

    Rungs 2 and 3 stay LEXICAL and stay ahead of the filesystem, so a caller
    pointing into production code is told THAT rather than "file not found".

    D-145 — AND EVERY RUNG JUDGES THE LOCATOR, NEVER THE PARAMETER SET.
    ------------------------------------------------------------------
    The id is split ONCE by `_split_pytest_node_id` (read its block for the
    three-cycle history that makes the parser the deliverable), and the two
    rungs that used to read the whole string — the prose rung and the
    single-leaf rung — now read `locator`, the id with its parameter id
    removed. A bracket payload is arbitrary caller text that pytest keeps
    verbatim, so a `::` or a space inside it says nothing whatever about
    whether the caller wrote a locator or a note.
    """
    ref = (ref or "").strip()
    if not ref:
        return "absent — a LATENT fix closes on a named regression test"
    path_part, chain, param_id = _split_pytest_node_id(ref)
    # The caller's locator WITHOUT the parameter set — what the rungs below
    # judge. The refusals still quote `ref`, so a caller reads back the id they
    # actually wrote (D-145).
    locator = ref[: len(ref) - len(param_id)] if param_id else ref
    if any(ch.isspace() for ch in locator):
        return (
            f"{ref!r} is prose, not a locator. Give a reference such as "
            "tests/test_report.py::test_absent_section_is_named"
        )
    if "::" not in locator:
        return (
            f"{ref!r} is not a locator of the form path::test — a LATENT fix "
            "closes on a locator such as "
            "tests/test_report.py::test_absent_section_is_named, not on prose"
        )
    path_part = _normalize_path(path_part)
    # The TEST's own name is the last element of the name chain, so a method on
    # a `Test` class (`path::TestSweeper::test_evicts`) resolves to the method
    # pytest runs rather than to the class holding it.
    name = chain[-1] if chain else ""
    if not path_part or not name:
        return f"{ref!r} has an empty path or test name on one side of '::'"
    if not _ref_singles_out_a_leaf(locator):
        return (
            f"{ref!r} names a FILE, not a test inside one — point at the test "
            "that would fail if this defect came back"
        )
    # Rung 2 (D-065): the path is a test file by the repo's own discovery rule.
    if not is_test_file(path_part):
        return (
            f"{path_part!r} is not a test file — {ref!r} points into production "
            "code. A regression test lives where pytest collects it "
            "(test_*.py, *_test.py, conftest.py, or under a tests/ directory); "
            "name the test that would fail if this gap came back"
        )
    # Rung 3 (D-065): the name reads as a test, and is not the defect's own.
    if not _TEST_REF_NAMES_A_TEST.search(name):
        return (
            f"{name!r} does not name a test — {ref!r} points at some other "
            "symbol in the file. Name the test function or Test class that "
            "would fail if this gap came back"
        )
    if name.casefold() in _PLACEHOLDER_NAMES:
        return f"{name!r} is a placeholder, not the name of a test"

    # NO OWN-SYMBOL RUNG HERE, DELIBERATELY, and this is where the two ladders
    # legitimately differ. `_test_ref_problem` refuses a reference naming the
    # defect's own symbol because AC-013 asks the LIVE lane for a test driving
    # an ADJACENT path. ST-003 asks the LATENT lane for "the test that would
    # fail if this gap came back", which is a test OF the defect's own symbol —
    # `tests/test_session.py::test_refresh_session` for a gap in
    # `refresh_session` is the right answer, not the wrong one. The own-file
    # rung would be worse still: a defect filed against a test file has its
    # regression test in that same file. What D-065 needs is already complete
    # above — a production path is never a test file, so the defect's own
    # production symbol is refused by rung 2 naming exactly that reason.

    # The provable half (D-131). An unresolvable path is now a REFUSAL: the
    # resolver already tried the locator as given, relative to project_root, and
    # by unique suffix beneath it, so "nowhere" means the file is not there.
    candidate = _resolve_test_path(path_part, project_root)
    if candidate is None:
        return (
            f"{path_part!r} does not resolve to a file — searched it as given, "
            f"under the project root, and by unique path suffix beneath it. A "
            "LATENT fix closes on a test that EXISTS: commit the test first, "
            "then name it here (a locator for a file that is not in the tree "
            "names no test that could fail if this gap came back)"
        )
    text, problem = read_text_file(candidate)
    if problem is not None:
        # Resolved but unreadable — nothing was compared, and that is not the
        # same answer as "the test is missing". The house rule for a read this
        # door does not own: degrade to the lexical rungs, which already held.
        return None
    if any(
        template.format(name=name) in text for template in _REGRESSION_DEF_TEMPLATES
    ):
        return None
    return (
        f"{path_part} exists but defines no {name!r} — the locator names a test "
        "that is not in the file it points at"
    )


# --------------------------------------------------------------------------- #
# The lead lane's measurement (CT-006 / ST-004 / FR-016 / FR-046 / AC-021)
# --------------------------------------------------------------------------- #


#: D-075 — git's rename compaction, which numstat emits in the PATH field.
#: `git show --numstat` (no -z) renders a rename as one field, not two: either
#: the braced form with the common prefix and suffix factored out
#: (``src/{f20.py => f20_renamed.py}``, ``{src => tests}/a.py``) or, when there
#: is nothing in common, the bare ``old => new``. Both are DISPLAY strings; the
#: path they name is the destination.
_NUMSTAT_RENAME_BRACE = re.compile(r"^(.*)\{(.*?) => (.*?)\}(.*)$")
_NUMSTAT_RENAME_BARE = " => "


def _numstat_rename_paths(field: str) -> tuple[str, str]:
    """``(destination, source)`` for one numstat path field; source "" if none.

    D-075: the field was taken as a path verbatim, so a rename recorded the
    git RENDERING — `src/{f20.py => f20_renamed.py}` — as the `file` on the
    audit record GI-003 requires, and `is_test_file('{src => tests}/a.py')`
    answered False, so a commit renaming a source file INTO the tests tree
    escaped the exclusion FR-016 depends on and still consumed the one-file
    lead lane. Both halves come from the same unparsed string, so both are
    fixed by parsing it once, here, and nowhere else.
    """
    match = _NUMSTAT_RENAME_BRACE.match(field)
    if match:
        prefix, old, new, suffix = match.groups()
        def _join(middle: str) -> str:
            return re.sub(r"/{2,}", "/", f"{prefix}{middle}{suffix}")
        return _join(new), _join(old)
    if _NUMSTAT_RENAME_BARE in field:
        old, _, new = field.partition(_NUMSTAT_RENAME_BARE)
        return new.strip(), old.strip()
    return field, ""


#: D-105 — what `git show` may be handed as a revision on the lead lane.
#:
#: A git object name is 7-40 lowercase hex digits, which is what `git rev-parse`
#: prints and what a lead pastes out of a commit. Anchored with `fullmatch` at
#: the call site so nothing can lead with `-` and be read as an option; the lane
#: measures ONE commit, so a range (`A..B`) and a ref name are refused here
#: rather than measured.
_OBJECT_NAME_RE = re.compile(r"[0-9a-f]{7,40}")


def _numstat_measurement(fix_commit: str, project_root: str) -> dict:
    """`git show --numstat` on one commit, reduced to the lane's two numbers.

    Returns ``{"ok": bool, "files": [non-test paths], "lines": int,
    "per_file": [{"file", "renamed_from", "added", "deleted", "lines"}],
    "error": str}``. ``lines`` is added-plus-deleted over the NON-TEST files
    only (FR-016: "test files excluded from the count"), classified by vocab's
    `is_test_file` so the lane and the report agree about what a test is.

    RENAMES ARE PARSED, NOT COPIED (D-075). `_numstat_rename_paths` reduces
    git's display form to the destination path plus the source it came from, so
    the `files` list carries real paths a reader can resolve and `renamed_from`
    keeps what was lost. A rename is classified NON-TEST when EITHER side is a
    non-test path: moving `src/a.py` to `tests/a.py` deletes production code,
    which is exactly the change FR-016's exclusion must not wave through, and
    the symmetric direction (a test promoted into src) is a source change too.

    PER-FILE COUNTS ARE RETURNED (D-073). `lines` is a sum over every non-test
    file, and the caller that records the lead_fix audit row needs to know
    whether that sum belongs to one file or to five — a record naming the FIRST
    file beside the TOTAL count says something false about both.

    Binary files report ``-`` for both counts in numstat; they contribute a file
    to the count and zero lines, which is the honest reading — a binary blob is
    not twenty lines of anything.

    Runs OUTSIDE the ledger transaction, deliberately. It needs only the commit,
    never the record, so holding an flock that every concurrent GRIND fix
    contends on across a subprocess would buy nothing.
    """
    import subprocess

    result = {
        "ok": False, "files": [], "test_files": [], "lines": 0, "per_file": [],
        "error": "",
    }

    # D-105 — THE REVISION IS VALIDATED BEFORE git SEES IT.
    # ----------------------------------------------------
    # This interpolated the caller's `fix_commit` straight into
    # `git show --numstat --format= <value>` with no terminator and no
    # validation, so a LEADING-DASH value was consumed by git as an OPTION and
    # git was left with no revision at all — defaulting to HEAD. Driven on one
    # run, defect D-001, tier LIVE, authored_by=lead: the real 10-file/500-line
    # commit was correctly refused, while `fix_commit='-1'` returned ok=True and
    # wrote a `lead_fix` record naming D-001, file src/tiny.py, line_count 3 and
    # fix_commit '-1' — a measurement of HEAD, a commit the lead never named.
    # Confirmed at the git layer (2.50.1): `git show --numstat --format= -1`
    # prints HEAD's numstat with rc=0, and `--all`, `--quiet`, `--stat` and
    # `HEAD~1..HEAD` behave the same.
    #
    # That is GI-003's named violation verbatim — "a Foundry-Fix that accepts a
    # LIVE lead fix without measuring eligibility" — and it corrupts the audit
    # trail GI-003 exists to create: the server-written handoff, handoffs.md and
    # the F6 lead-fix row all assert a measurement of an object never named.
    #
    # BOTH halves, because either alone is a half-fix. The shape check refuses a
    # non-object-name by NAME, before any subprocess runs; `--end-of-options`
    # then guarantees that whatever passes the check is read as a revision and
    # never as a flag, so a future edit that loosens the pattern cannot
    # re-open the option channel. An object name is hex and lowercase — abbrev
    # or full — which is exactly what `git rev-parse` prints and what a lead
    # pastes; a ref name, a range or a relative spelling is refused here rather
    # than measured, since the lane is a measurement OF ONE COMMIT and a range
    # is not one.
    if not isinstance(fix_commit, str) or not _OBJECT_NAME_RE.fullmatch(fix_commit):
        result["error"] = (
            f"fix_commit is not a git object name: {fix_commit!r}. The lane is "
            "measured with `git show --numstat` on ONE commit, so fix_commit "
            "must be an abbreviated or full commit SHA (7-40 lowercase hex "
            "digits) — not a ref, a range, or an option"
        )
        result["field"] = "fix_commit"
        return result

    try:
        proc = subprocess.run(
            [
                "git", "-C", project_root, "show", "--numstat", "--format=",
                # Everything after this is a revision or a path, never a flag.
                "--end-of-options", fix_commit,
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
        )
    except (FileNotFoundError, OSError, subprocess.SubprocessError) as exc:
        result["error"] = f"could not run git show on {fix_commit}: {exc}"
        return result
    if proc.returncode != 0:
        result["error"] = (
            f"git show could not read {fix_commit}: "
            f"{proc.stderr.strip()[:160] or 'unknown commit'}"
        )
        return result

    for line in proc.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        added, deleted = parts[0], parts[1]
        # A rename with -z would arrive as two extra fields; without -z git
        # compacts it into the third. Both are handled, so the parse does not
        # depend on which spelling a future caller's flags produce.
        if len(parts) >= 4 and parts[3].strip():
            path, renamed_from = parts[3].strip(), parts[2].strip()
        else:
            path, renamed_from = _numstat_rename_paths(parts[2])
        # EITHER side non-test makes the entry non-test (D-075). A rename out of
        # the tests tree changes production code; a rename into it removes some.
        is_test = is_test_file(path) and (
            not renamed_from or is_test_file(renamed_from)
        )
        lines = sum(int(c) for c in (added, deleted) if c.isdigit())
        if is_test:
            # D-107: the test rows are COUNTED, not merely skipped. The
            # zero-non-test-file refusal explains itself with "this commit
            # touches only test files", and that sentence is true only when
            # there were test files to touch — see `_lead_lane_problem`.
            result["test_files"].append(path)
            continue
        result["files"].append(path)
        # Keyed `path`, `added`, `deleted`, `renamed_from` — C-8's row shape,
        # which `record_lead_fix_handoff` reads. Spelled the sibling's way
        # rather than this module's, because a second spelling of one record is
        # how the audit row and the lane measurement come to disagree.
        result["per_file"].append({
            "path": path,
            "renamed_from": renamed_from or None,
            "added": int(added) if added.isdigit() else None,
            "deleted": int(deleted) if deleted.isdigit() else None,
            "lines": lines,
        })
        result["lines"] += lines
    result["ok"] = True
    return result


def _lead_lane_problem(fix_commit: str, project_root: str) -> str | None:
    """The named reason a LIVE lead-authored fix exceeds its lane, else None.

    ST-004 / CT-006: exactly `LEAD_LANE_MAX_FILES` non-test file, at most
    `LEAD_LANE_MAX_LINES` added-plus-deleted lines over it. The refusal names
    the COUNT that exceeded the lane (AC-021), because "too big" tells a lead
    nothing about which half to shrink.

    A commit git cannot read is a refusal too. The lane is a MEASUREMENT, and a
    measurement that could not be taken is not a measurement that passed — the
    shape that would let an unbounded lead fix through by naming a SHA that does
    not exist.

    D-020 — EXACTLY ONE, AND THE REFUSAL SAYS WHICH DIRECTION IT MISSED BY.
    ----------------------------------------------------------------------
    LEAD RULING (recorded SPEC_AMBIGUOUS in the run's state.json): the lane
    requires EXACTLY one non-test file for a LIVE lead fix. FR-014 states the
    lane as "LIVE if one file and <= 20 lines"; FR-016's "more than one" names
    one refusal condition, not the only one. So the `!=` comparison is correct
    and stays, and a test-only LIVE fix is outside the lane and goes to a GRIND
    teammate.

    What was wrong is what the refusal SAID. A commit with zero non-test files
    was refused with "changes 0 non-test file(s) ... Dispatch this to a GRIND
    teammate instead" — the too-big message, on a commit that is too small,
    telling a lead to shrink something that is already empty. The two
    directions are now separate branches with separate remedies, because "add
    the source change this fix is missing" and "split this commit up" are
    opposite instructions and a lead acting on the wrong one loses a cycle.
    """
    measured = _numstat_measurement(fix_commit, project_root)
    if not measured["ok"]:
        return (
            f"{measured['error']}. The lane is measured from the commit, so a "
            "commit that cannot be read cannot be measured."
        )
    files = measured["files"]
    if not files:
        # D-107 — THE DIAGNOSIS IS CONDITIONAL ON THE THING IT DIAGNOSES.
        #
        # The "this commit touches only test files" clause was appended
        # UNCONDITIONALLY to every zero-non-test-file refusal, and is false
        # whenever the commit touches nothing at all. Driven with
        # `git commit --allow-empty` on a LIVE lead fix: "…ed2ad97df44d changes
        # 0 non-test file(s) — the lane requires exactly 1, and this commit
        # touches only test files. A LIVE defect whose fix is a test-only change
        # is outside the lane" — on a commit with no files of any kind, and the
        # same false clause fired for any commit git reports zero numstat rows
        # for. The refusal DIRECTION was right and the D-020 ruling's two
        # mandates held (the real count and the rule are both stated, and the
        # too-big message is never reused); only the explanation lied, on the
        # one refusal whose wording that ruling explicitly regulated.
        #
        # So the remedy splits with the diagnosis, because "name the commit that
        # carries the source change" and "this commit is empty" send a lead to
        # different places.
        if measured["test_files"]:
            return (
                f"{fix_commit[:12]} changes 0 non-test file(s) — the lane "
                f"requires exactly {LEAD_LANE_MAX_FILES}, and this commit "
                f"touches only test files ({', '.join(measured['test_files'][:5])}). "
                "A LIVE defect whose fix is a test-only change is outside the "
                "lane: name the commit that carries the source change, or "
                "dispatch it to a GRIND teammate."
            )
        return (
            f"{fix_commit[:12]} changes no files at all — the lane requires "
            f"exactly {LEAD_LANE_MAX_FILES} non-test file, and git reports no "
            "changed paths for this commit. Name the commit that actually "
            "carries the fix (an empty or already-merged commit measures "
            "nothing), or dispatch it to a GRIND teammate."
        )
    if len(files) != LEAD_LANE_MAX_FILES:
        return (
            f"{fix_commit[:12]} changes {len(files)} non-test file(s) "
            f"({', '.join(files[:5])}) — the lane requires exactly "
            f"{LEAD_LANE_MAX_FILES}. Dispatch this to a GRIND teammate instead."
        )
    if measured["lines"] > LEAD_LANE_MAX_LINES:
        return (
            f"{fix_commit[:12]} changes {measured['lines']} added-plus-deleted "
            f"line(s) in {files[0]} — the lead lane is at most "
            f"{LEAD_LANE_MAX_LINES}. Dispatch this to a GRIND teammate instead."
        )
    return None


def _check_reported_prompt_hash(fdir: Path, casting_id, reported_hash) -> dict | None:
    """C-8's ``check_reported_prompt_hash`` — None, or the refusal dict."""
    from foundry_mcp.tools.foundry_handoff import check_reported_prompt_hash

    return check_reported_prompt_hash(fdir, casting_id, reported_hash)


def _record_lead_fix_handoff(fdir: Path, **fields) -> dict:
    """C-8's ``record_lead_fix_handoff`` — the server's own audit record."""
    from foundry_mcp.tools.foundry_handoff import record_lead_fix_handoff

    return record_lead_fix_handoff(fdir, **fields)


@ledger_refusals
def foundry_mark_defect_fixed(
    defect_id: str,
    cycle: int,
    adjacent_path_statement: str = "",
    adjacent_path_test: str = "",
    project_root: str = ".",
    authored_by: str = "",
    regression_test: str = "",
    fix_commit: str = "",
    prompt_hash: str | None = None,
    casting_id: int | str | None = None,
) -> dict:
    """Mark a defect as fixed, declaring the fix's blast radius.

    FR-009 / CT-001 / ST-004. This call validated NOTHING before — it matched
    the first id and flipped status, which is how fixes kept opening
    regressions their own tests could not see. Two declarations are now
    preconditions of the transition:

      adjacent_path_statement — who ELSE calls this, what else transitions
          here, what runs concurrently (A-017).
      adjacent_path_test — a reference to a test that drives at least one
          NAMED adjacent path: a different caller, transition, or concurrent
          interaction than the path the defect was found on (A-018 / FR-010).

    A call missing either is refused with a message naming each missing field,
    in the same shape as the acceptance gate's rejections. The declarations are
    persisted on the defect record, so the blast radius a fix claimed to have
    considered is auditable after the fact.

    Both declarations are also checked for CONTENT, not merely presence: the
    statement must not restate the defect's own symbol, and the test reference
    must look like a test reference and must not name only the defect's own
    path (see ``_test_ref_problem``). A presence-only gate accepted "n/a" and
    "tested it manually", which is the gate passing while the guarantee it
    exists for does not hold. Every failing field is named in one refusal.

    The whole read-modify-write runs inside ``ledger_transaction`` (FR-020 /
    AC-025). It used to be an UNLOCKED load / mutate / save while every other
    writer of defects.json held that lock, so a fix landing between a peer's
    read and write was silently discarded by the peer's ``.tmp`` rename — the
    call returned ok and the defect stayed open. Concurrent fixes are the norm
    in GRIND, not an edge case: a whole wave of teammates closes defects in
    parallel against one ledger.

    ``fixed_in_cycle`` is stamped from the SERVER counter (FR-005 / ST-001);
    the caller's ``cycle`` is retained as ``declared_fixed_cycle`` for audit
    only. Escalation reads these numbers back, and it accumulated against
    lead-asserted cycles while the server counter sat at 0.

    CEREMONY IS PROPORTIONAL TO THE EVIDENCE TIER (US-003 / ST-003 / ST-004)
    -----------------------------------------------------------------------
    Every fix used to pay the full LIVE declaration, so a one-line change
    closing a scan-derivation gap nobody had reproduced cost the same apparatus
    as a fix for a driven, reachable failure. The check order is C-13's and it
    is LOCKED, so that two callers making the same mistake are told about the
    same field first:

      1. tool-wide — the defect exists; ``authored_by`` is present and in
         FIX_AUTHORS; a teammate names a matching ``prompt_hash``; a lead names
         a ``fix_commit``, whatever the tier (FR-053).
      2. the tier lane — LATENT closes on a ``regression_test`` locator alone
         (ST-003); LIVE keeps the full adjacent-path declaration, unweakened
         (FR-015 / AC-023). An UNKNOWN-tier record takes the LIVE lane: it is a
         record nobody classified, and the fail-safe direction is the stricter
         ceremony, never the looser one.
      3. the lead lane's measurement — LIVE lead fixes only (FR-046 / CT-006).
      4. persist, then the server's own ``lead_fix`` handoff record (GI-003).

    The failing-then-passing statement is NOT an input here (FR-041 / AC-012).
    It belongs in the teammate's completion report, where PROVE can drive the
    test to confirm it; demanding it as a string on this call would be a claim
    the server cannot check, which is the ceremony this requirement removes.
    """
    from foundry_mcp.tools.foundry import _dict_records, ledger_transaction

    fdir = get_run_dir(project_root)
    if not fdir:
        return {"error": "No active foundry run."}
    if (corrupt := _artifact_guard(fdir)):
        return corrupt
    defects_path = fdir / "defects.json"

    statement = (adjacent_path_statement or "").strip()
    test_ref = (adjacent_path_test or "").strip()
    author = (authored_by or "").strip()
    regression_ref = (regression_test or "").strip()
    commit = (fix_commit or "").strip()
    server_cycle = _current_cycle(fdir)

    # --- Tool-wide rungs that need no ledger read, evaluated before the lock --
    #
    # GI-003 / CT-005 / AC-020: `authored_by` is REQUIRED on every fix. A lead
    # fix recorded as free prose in a hand-written handoff is a fix nothing can
    # measure or count, and this field is what makes the difference visible.
    if not author:
        return {
            "error": (
                f"Cannot mark {defect_id} fixed — missing required field(s): "
                "authored_by. Every fix declares who wrote it."
            ),
            "missing_fields": ["authored_by"],
            "hint": (
                "Pass authored_by='teammate' for a fix a GRIND teammate wrote, "
                "or authored_by='lead' for one you wrote yourself in the bounded "
                "lead lane. A lead fix additionally requires fix_commit."
            ),
        }
    if author not in FIX_AUTHORS:
        return {
            "error": (
                f"Cannot mark {defect_id} fixed — unknown authored_by: "
                f"{author!r}. Must be one of: {', '.join(sorted(FIX_AUTHORS))}."
            ),
            "missing_fields": ["authored_by"],
            "invalid_fields": [{
                "field": "authored_by",
                "reason": f"{author!r} is not a member of FIX_AUTHORS",
            }],
            "hint": "Who wrote the fix is a closed vocabulary: lead or teammate.",
        }

    # AC-030 / CT-011 — POINTER DISPATCH'S OTHER HALF.
    #
    # Foundry-Spawn-Teammate hands a teammate a PATH and a HASH instead of the
    # prompt text; only an agent that actually read the file can state the hash
    # back. That is advice until a gate consumes it, and this is one of the two
    # gates that does (Foundry-Accept-Casting is the other). Both call the SAME
    # `check_reported_prompt_hash`, because a hash rung that exists at one door
    # and not the other lets an unread prompt through whichever door the lead
    # happens to walk.
    if author == "teammate":
        hash_missing = []
        if not (prompt_hash or "").strip():
            hash_missing.append("prompt_hash")
        if casting_id is None or str(casting_id).strip() == "":
            hash_missing.append("casting_id")
        if hash_missing:
            return {
                "error": (
                    f"Cannot mark {defect_id} fixed — missing required field(s): "
                    f"{', '.join(hash_missing)}. A teammate-authored fix states "
                    "back the hash of the prompt it was dispatched with."
                ),
                "missing_fields": hash_missing,
                "hint": (
                    "The teammate's completion report carries the sha256 it read; "
                    "pass it as prompt_hash together with the casting_id it was "
                    "dispatched for. Only reading the prompt file produces the "
                    "right answer, which is the point."
                ),
            }
        hash_refusal = _check_reported_prompt_hash(fdir, casting_id, prompt_hash)
        if hash_refusal is not None:
            return hash_refusal

    # FR-053 / CT-005 / AC-020: a lead fix always names its commit, whatever the
    # tier. The commit is what the `lead_fix` handoff record carries.
    #
    # D-194's sibling in this file, restated to the ruling's TWO HALVES. This
    # said the audit trail exists "even on a LATENT fix that is never
    # measured", and that is not what the door below does:
    #
    #   1. THE COMMIT IS MEASURED ON BOTH TIERS. `_numstat_measurement` runs on
    #      the LATENT arm too — that is why an unresolvable fix_commit is
    #      refused there (D-132) and why a LATENT `lead_fix` record carries a
    #      real per-file measurement rather than nulls (D-046 / D-073).
    #   2. THE LANE LIMIT REFUSES ONLY WHEN LIVE. `_lead_lane_problem`'s file
    #      and line bounds are what FR-046 / CT-006 / ST-004 scope to LIVE:
    #      "measures fix_commit only when the defect is LIVE" is about whether
    #      the lane REFUSES, not about whether the numbers are taken.
    #
    # "Never measured" was the retired spelling for "no limit applies", and a
    # comment that keeps it tells the next author the LATENT arm takes no git
    # read at all — which is precisely the reading D-132 was filed against.
    if author == "lead" and not commit:
        return {
            "error": (
                f"Cannot mark {defect_id} fixed — missing required field(s): "
                "fix_commit. Every lead-authored fix names the commit it landed "
                "in, whatever the defect's tier."
            ),
            "missing_fields": ["fix_commit"],
            "hint": (
                "Commit the fix first, then pass its SHA as fix_commit. The "
                "server writes a lead_fix handoff record carrying it, and on a "
                "LIVE defect it measures the commit against the lead lane."
            ),
        }

    # Every branch runs INSIDE the transaction: the not-found and adjacency
    # checks both read the target record, so evaluating them outside would
    # re-open the same read-then-write window this lock exists to close. A
    # refusal mutates nothing, and writing back an unmodified document is a
    # no-op.
    refusal: dict | None = None
    open_count = 0
    with ledger_transaction(defects_path, "defects") as records:
        target = None
        # D-128: this door's inline `isinstance(d, dict)` was CORRECT and was
        # still an instance of the class — a hand-applied copy of the filter
        # `_dict_records` exists to hold once. The batch door's identical scan
        # had no copy at all, which is how one door tolerated a malformed
        # record while the other lost the caller's filing to an AttributeError.
        for d in _dict_records(records):
            if d.get("id") == defect_id:
                target = d
                break

        # ST-003 / ST-004 — WHICH LANE THIS FIX IS IN.
        #
        # Read through vocab's `defect_tier`, the one total resolution of the
        # tier a record READS as. A missing key, a null and a string outside
        # DEFECT_TIERS all land on TIER_UNKNOWN, and unknown takes the LIVE
        # lane: a record nobody classified gets the stricter ceremony, never
        # the looser one. Reading it as LATENT would let a fix close on one
        # locator because an older archive never carried the field.
        tier = defect_tier(target) if target else TIER_UNKNOWN
        latent_lane = tier == "LATENT"

        missing = []
        if latent_lane:
            if not regression_ref:
                missing.append("regression_test")
        else:
            if not statement:
                missing.append("adjacent_path_statement")
            if not test_ref:
                missing.append("adjacent_path_test")
        own_symbol = (target.get("symbol") or "").strip() if target else ""
        own_file = (target.get("file") or "").strip() if target else ""

        # Present-but-inadequate declarations, gathered so ONE refusal names
        # every field that failed (CT-001's "naming each missing field" applies
        # to a junk declaration exactly as it does to an absent one — a caller
        # who supplied two non-answers should be told about both, not sent back
        # twice).
        invalid: list[dict] = []
        statement_is_usable = False
        if latent_lane:
            # CT-004: within the lane, refusal ONLY when the locator is absent
            # or does not name a test. No adjacent-path statement, no
            # adjacent-path test, no failing-then-passing prose — ST-003 says
            # none of it is demanded, and demanding it anyway is the ceremony
            # this requirement exists to remove.
            if regression_ref:
                problem = _regression_test_problem(regression_ref, project_root)
                if problem is not None:
                    invalid.append({"field": "regression_test", "reason": problem})
        elif statement:
            # ST-004 / AC-013: the declared path must be ADJACENT — distinct
            # from the path the defect itself was found on — and it must
            # actually be a declaration. The check here was exact equality
            # against the defect's own symbol and nothing else, so 'x', 'none',
            # 'no adjacent paths' and 'the same path' all closed defects
            # (D-050). See `_statement_problem` for the ladder.
            problem = _statement_problem(statement, own_symbol, own_file)
            if problem is not None:
                invalid.append({
                    "field": "adjacent_path_statement",
                    "reason": problem,
                })
            else:
                statement_is_usable = True
        if test_ref and not latent_lane:
            # D-092: the two declarations are a matched PAIR — the statement
            # names the adjacent paths, the reference drives one of them — so
            # the statement is an INPUT to judging the reference, not a
            # separate verdict beside it. It is withheld when it failed its own
            # ladder: a caller told "your reference drives none of the paths
            # your statement named" about a statement that named none would be
            # sent after the wrong problem.
            problem = _test_ref_problem(
                test_ref,
                own_symbol,
                own_file,
                statement=statement if statement_is_usable else "",
            )
            if problem is not None:
                invalid.append({"field": "adjacent_path_test", "reason": problem})

        # CT-006 / FR-046 — MEASURED ONLY WHEN LIVE AND ONLY WHEN LEAD.
        #
        # The lane is a bound on how much a lead may change without dispatching
        # a teammate, and it is measured from the commit rather than claimed in
        # prose. On a LATENT defect the required fix_commit is RECORDED and not
        # measured (CT-006's second clause): a LATENT gap can be a large,
        # mechanical, entirely safe change, and there is no reachable failure
        # whose blast radius the bound is protecting.
        # D-132 — "NOT MEASURED" IS NOT "NOT READ".
        #
        # CT-006's second clause is that on a LATENT defect the required
        # fix_commit "is RECORDED and not measured", and this branch read that
        # as "no git call at all". Driven on a LATENT defect with
        # authored_by=lead and a valid regression_test locator: fix_commit
        # 'not-a-commit', '-1', 'tbd' and forty zeros each returned ok True;
        # the record persisted fix_commit 'tbd'; handoffs.jsonl gained
        # {fix_commit 'tbd', file null, line_count null, files null}; and
        # report.json rendered the row "measurement unavailable - git could not
        # read the commit". `_numstat_measurement` refuses the shape by name
        # (the D-105 rung) and the LATENT path discarded that refusal at the
        # record step (`if not measured["ok"]: measured = None`).
        #
        # FR-053 is verbatim "authored_by=lead always needs fix_commit SO THE
        # lead_fix HANDOFF CARRIES THE COMMIT" — the field exists to make the
        # audit row point at a real object, and a string that names no object
        # carries nothing. CT-005 refuses "when fix_commit is absent on a lead
        # fix"; a value git cannot resolve is absent in every sense that
        # matters to the reader of the record.
        #
        # WHAT STAYS LIVE-ONLY IS THE LANE, exactly as FR-046 / CT-006 / ST-004
        # say: the file count and the line count are limits on how much a lead
        # may change without dispatching a teammate, and a LATENT gap can be a
        # large, mechanical, entirely safe change with no reachable failure
        # whose blast radius the bound protects. So the LATENT arm runs the
        # READ and refuses only on "this is not a commit"; it never compares a
        # count. `_lead_lane_problem` is the LIVE arm and is unchanged — it
        # already runs the same read as its first rung, so neither arm
        # re-implements the other.
        lane_problem = None
        if author == "lead" and target is not None:
            if not latent_lane:
                lane_problem = _lead_lane_problem(commit, project_root)
            else:
                unreadable = _numstat_measurement(commit, project_root)
                if not unreadable["ok"]:
                    lane_problem = (
                        f"{unreadable['error']}. A LATENT lead fix is not "
                        "MEASURED — no file or line limit applies to it — but "
                        "fix_commit is still the commit the lead_fix handoff "
                        "record carries (FR-053), so it must name a real "
                        "object."
                    )

        if target is None:
            refusal = {"error": f"Defect {defect_id} not found"}
        elif missing:
            refusal = {
                "error": (
                    f"Cannot mark {defect_id} fixed — missing required field(s): "
                    f"{', '.join(missing)}. "
                    + (
                        "A LATENT defect closes on a regression_test locator of "
                        "the form path::test — the test that would fail if this "
                        "gap came back."
                        if latent_lane
                        else "adjacent_path_statement must name who else calls "
                        "this, what else transitions here, or what runs "
                        "concurrently. adjacent_path_test must reference a test "
                        "that drives at least one of those adjacent paths."
                    )
                ),
                "missing_fields": missing,
                "tier": tier,
                "hint": (
                    "Write the regression test first, then re-call Foundry-Fix "
                    "with its locator. The failing-then-passing statement goes "
                    "in your completion report, not on this call."
                    if latent_lane
                    else "Write the adjacent-path test first, then re-call "
                    "Foundry-Fix with both declarations. A fix whose blast "
                    "radius is undeclared is how a defect closes and a "
                    "regression opens in the same cycle."
                ),
            }
        elif invalid:
            fields = [item["field"] for item in invalid]
            refusal = {
                "error": (
                    f"Cannot mark {defect_id} fixed — unusable declaration(s): "
                    + "; ".join(f"{item['field']} — {item['reason']}" for item in invalid)
                    + "."
                ),
                # Same key the absent-field refusal uses, so a caller has one
                # place to read "which fields must I supply or repair".
                "missing_fields": fields,
                "invalid_fields": invalid,
                "tier": tier,
                "hint": (
                    "Point at the test that would fail if this gap came back — a "
                    "locator such as tests/test_report.py::test_absent_section_"
                    "is_named, not a note to yourself."
                    if latent_lane
                    else "Name a second path that touches this code, then "
                    "reference the test that drives THAT path — a locator such "
                    "as tests/test_auth.py::test_sweeper_evicts_stale_sessions, "
                    "not a note to yourself."
                ),
            }
        elif lane_problem is not None:
            refusal = {
                "error": (
                    f"Cannot mark {defect_id} fixed by the lead — {lane_problem}"
                ),
                "missing_fields": ["fix_commit"],
                "invalid_fields": [{"field": "fix_commit", "reason": lane_problem}],
                "tier": tier,
                # D-132: the hint follows the arm that fired. On the LATENT
                # arm no limit was compared, so quoting the lane's file and
                # line ceilings there would send the lead to shrink a commit
                # nothing measured.
                "hint": (
                    (
                        "A LATENT lead fix is not measured — no file or line "
                        "limit applies to it — but fix_commit is the commit "
                        "the lead_fix handoff record carries, so it must be a "
                        "commit git can read: paste the SHA "
                        "`git rev-parse --short HEAD` printed for the fix."
                    )
                    if latent_lane
                    else (
                        "The lead lane exists so a cycle whose open defects are "
                        f"all small closes without a teammate spawn: "
                        f"{LEAD_LANE_MAX_FILES} non-test file, at most "
                        f"{LEAD_LANE_MAX_LINES} added-plus-deleted lines. "
                        "Anything larger is a GRIND task."
                    )
                ),
            }
        else:
            target["status"] = "fixed"
            target["fixed_in_cycle"] = server_cycle
            target["declared_fixed_cycle"] = cycle
            target["authored_by"] = author
            if latent_lane:
                target["regression_test"] = regression_ref
            else:
                target["adjacent_path_statement"] = statement
                target["adjacent_path_test"] = test_ref
                if regression_ref:
                    # Never demanded on the LIVE lane, always kept when offered:
                    # a LIVE fix that also names a regression test has said
                    # something true and the record should carry it.
                    target["regression_test"] = regression_ref
            if commit:
                target["fix_commit"] = commit

        open_count = sum(
            1 for d in _dict_records(records) if d.get("status") == "open"
        )

    if refusal is not None:
        return refusal

    # D-035: a fix that lands mid-INSPECT invalidates the width this cycle
    # already decided and swept at. Stamped after the ledger commits, so a
    # rolled-back transaction leaves no claim that a fix landed.
    _note_fix_after_inspect_decision(fdir, defect_id)

    # GI-003 / AC-022 — THE SERVER WRITES THE HANDOFF, NOT THE LEAD.
    #
    # "A lead fix recorded only as free prose in a hand-written handoff" is the
    # named violation, so the record is a side effect of the accepted call and
    # cannot be forgotten. It is written AFTER the ledger commits, because a
    # handoff naming a fix the transaction then rolled back would be the same
    # gap pointing the other way.
    #
    # D-046 — MEASURING IS NOT RECORDING, AND THE LANE TEST IS THE ONLY THING
    # THAT IS LIVE-ONLY.
    # ----------------------------------------------------------------------
    # This branch read `None if latent_lane else ...`, so a LATENT lead fix
    # emitted `{'file': None, 'line_count': None}` while the LIVE fix beside it
    # in the same run emitted the real file and count. GI-003 states the record
    # verbatim as "carrying the defect id, tier, file, line count and test",
    # AC-022 and OT-010 repeat that field list, and not one of the three carries
    # a tier carve-out. What IS LIVE-only is FR-046 / CT-006 / ST-004's
    # ELIGIBILITY test — "measures fix_commit only when the defect is LIVE" is
    # about whether the lane refuses, not about whether the numbers are written
    # down. `_numstat_measurement` is a pure read of the commit and
    # `_lead_lane_problem` is the separate limit check, so the two were already
    # separable and only this call site conflated them.
    #
    # Consequence of the conflation: AC-022's "the generated report lists it"
    # rendered blank file and line columns for every LATENT lead fix, and a
    # report reader could not tell a deliberately unmeasured fix from a
    # measurement nobody took.
    #
    # A commit git cannot read still records None, because there the
    # measurement genuinely could not be taken. D-132 made that unreachable on
    # BOTH lanes rather than only on the LIVE one: `_lead_lane_problem` refuses
    # an unreadable commit on LIVE and the LATENT arm beside it now runs the
    # same read and refuses on the shape, so by the time this executes the
    # commit has resolved. The None branch is kept as the honest answer for a
    # repository state that changes under the call, not as a live path.
    #
    # D-073 — THE ROWS ARE THE RECORD; `file` IS A CONVENIENCE THAT MUST NOT
    # LIE.
    # ----------------------------------------------------------------------
    # This passed `file=measured["files"][0]` beside `line_count=measured["lines"]`,
    # where `lines` is the sum over EVERY non-test file. On the LATENT lane,
    # where any number of files is legal, a 5-file 100-line-each commit was
    # therefore recorded as `{"file": "pkg/m0.py", "line_count": 500}` and
    # REPORT.md rendered `| D-001 | LATENT | pkg/m0.py | 500 |`. A reader
    # concludes 500 lines changed in pkg/m0.py; 100 did, and nothing in the
    # record said four other files were touched. GI-003 / AC-022 / OT-010 make
    # this the audit trail for a fix nobody else reviewed, and a field that is
    # wrong is worse than one that is absent.
    #
    # `files=` (C-8, widened by D-074) is now passed instead: the measurement
    # itself, one row per non-test file. The writer derives `file` — the single
    # path when there is exactly one, None otherwise — and `line_count` from
    # those same rows, so the two cannot disagree and no caller re-derives
    # either.
    # D-170 — `test` CARRIES THE TEST THE LANE MANDATED, NOT WHICHEVER ONE THE
    # CALLER ALSO SENT.
    # ----------------------------------------------------------------------
    # This read `test=regression_ref or test_ref`, so on the LIVE lane an
    # OPTIONAL `regression_test` displaced the MANDATED `adjacent_path_test`.
    # Driven at the door with authored_by=lead on a LIVE defect carrying the
    # full declaration plus an extra regression locator: the record's `test`
    # read `tests/test_lane.py::test_lane_holds` and the adjacent-path test
    # appeared nowhere in the record or in the handoffs.md mirror row.
    #
    # GI-003 / AC-022 make this the audit trail for a fix nobody else reviewed,
    # and the test is one of the five things a reader must be able to
    # re-derive. Which test HOLDS the fix is a property of the lane, not of what
    # the caller volunteered: on LIVE it is the adjacent-path test (AC-023 /
    # FR-015 demand it and nothing else), on LATENT it is the regression test
    # (ST-003 / CT-004 demand that one and no adjacent-path fields at all). So
    # the lane selects, exactly as the persist step above and the forge-log
    # write below already select on `latent_lane`.
    #
    # The optional locator is not dropped — the comment at the persist step
    # says a LIVE fix that also names a regression test "has said something
    # true and the record should carry it", and now it carries it IN ADDITION
    # rather than INSTEAD, under its own name. `regression_test` is casting 2's
    # keyword-only parameter on `record_lead_fix_handoff`; None when the lane
    # already reports it as `test`, so no record states the same locator twice.
    lead_fix_record = None
    if author == "lead":
        measured = _numstat_measurement(commit, project_root)
        if not measured["ok"]:
            measured = None
        lead_fix_record = _record_lead_fix_handoff(
            fdir,
            defect_id=defect_id,
            tier=tier,
            files=(measured["per_file"] if measured else None),
            test=regression_ref if latent_lane else test_ref,
            regression_test=(None if latent_lane else (regression_ref or None)),
            fix_commit=commit,
        )

    forge_log = fdir / "forge-log.md"
    if forge_log.exists():
        with open(forge_log, "a", encoding="utf-8") as f:
            f.write(
                f"\n**{defect_id} FIXED** ({tier}, by {author}) in cycle "
                f"{server_cycle} ({_now()})\n"
            )
            if latent_lane:
                f.write(f"- **Regression test:** {regression_ref}\n")
            else:
                f.write(f"- **Adjacent paths:** {statement}\n")
                f.write(f"- **Adjacent-path test:** {test_ref}\n")
            if commit:
                f.write(f"- **Fix commit:** {commit}\n")
            f.write("\n")

    result = {
        "ok": True,
        "defect_id": defect_id,
        "tier": tier,
        "authored_by": author,
        "fixed_in_cycle": server_cycle,
        "declared_cycle": cycle,
        "adjacent_path_statement": statement,
        "adjacent_path_test": test_ref,
        "regression_test": regression_ref,
        "fix_commit": commit,
        "remaining_open": open_count,
    }
    if lead_fix_record is not None:
        result["lead_fix"] = lead_fix_record
    return result


# --------------------------------------------------------------------------- #
# Cross-casting seam: the two helpers tools/foundry.py owns.
#
# Both are imported LAZILY and deliberately unguarded. Importing them at module
# top would make `import foundry_mcp.server` fail outright while the sibling
# casting that owns tools/foundry.py is still in flight, taking every other
# tool in this server down with it; swallowing the ImportError would instead
# hide a real wiring break behind a silent fallback. A lazy import fails loudly
# at exactly the one call site that needs the symbol, naming it.
#
# There must be ONE writer for each of these. Do not add a local ledger writer
# or a local ID mint here \u2014 that duplication is what FR-013 and FR-020 exist to
# remove.
# --------------------------------------------------------------------------- #


def _mint_defect_id(records: list) -> str:
    """Allocate a defect ID that is unique under concurrent filing (FR-020).

    Replaces the positional ``D-{len(defects) + 1:03d}`` mint, which hands the
    SAME id to two streams that read the same ledger snapshot, re-issues a live
    id whenever a record is removed, and wraps silently past D-999 \u2014 the AC-025
    race. ``allocate_record_id`` takes the highest existing suffix instead, and
    its uniqueness comes from the surrounding ``ledger_transaction`` lock, which
    is why every call below sits inside one.
    """
    from foundry_mcp.tools.foundry import allocate_record_id

    return allocate_record_id(records, prefix="D")


def new_defect_record(
    *,
    cycle: int,
    declared_cycle: int,
    source: str,
    defect_type: str,
    tier: str,
    defect_class: str,
    description: str,
    reproduction_attempted: str | None = "",
    spec_ref: str = "",
    symbol: str = "",
    file_path: str = "",
    target_kind: str = "",
    record_id: str = "",
    created_at: str | None = None,
) -> dict:
    """The persisted defect record — ONE shape definition, BOTH filing doors.

    C-2 / CT-001 / CT-002 / US-002. Returns the dict a filing door appends to
    ``defects.json``. The caller supplies the id — ``foundry_add_defect``
    assigns it inside its own transaction, ``foundry_sync_defects`` mints it as
    it builds — and every other field is derived here, once.

    WHY THIS IS A FUNCTION (D-192)
    ------------------------------
    The record was TWO hand-typed literals: one in ``foundry_add_defect``
    (tools/foundry.py), one in the write loop of ``foundry_sync_defects``
    below. The batch door's literal carried a comment asserting that "the batch
    door writes the SAME record shape as the single door, field for field", and
    that claim was false. Driven at HEAD 3584f55 with one finding carrying
    ``target_kind='code'`` through both doors: ``Foundry-Defect`` persisted
    D-001 with ``target_kind='code'``; ``Foundry-Sync`` persisted D-002 with no
    ``target_kind`` key at all. Enforcement was unaffected — both doors refuse
    comment prose identically across LINE_DRIFT_CITE, PROSE_COUNT,
    DIRECTION_WORD and ENUMERATION, and the LATENT security denylist fires at
    both — but the durable record was not: a Sync-filed defect carried no
    evidence that the declaration had ever been made, so it could not be
    audited for it while the same finding filed one door over could.

    That is D-119 (the two doors disagreeing about which cycle a record
    belonged to) and D-077 (the re-tier rule implemented at one door only) a
    third field along, and the answer is the one those two took: the rule is
    DERIVED ONCE and both doors call it. A COMMENT asserting that two literals
    agree was the alternative, and it is what stood here — it is not a
    mechanism, it goes stale the moment either literal gains a field, and
    nothing fails when it does.

    ``target_kind`` is written CONDITIONALLY, exactly as the single door writes
    it: absence stays absence. ``vocab.is_non_comment`` reads ``bool(kind)``, so
    ``""`` and absent answer alike today — but the guarantee this function
    exists to hold is field-for-field agreement with the SINGLE door's shipped
    output, not with a tidier shape, and a record that grows a key it never
    carried is a change to every pre-change archive read.

    WHERE THIS BELONGS (concerns.md, GRIND cycle 15)
    -----------------------------------------------
    The natural site is ``tools/foundry.py``, beside ``validate_defect_filing``
    and ``retier_matching_untiered`` — the two other rules both doors share —
    where neither door needs a new import edge. That file belongs to casting 2
    and this casting may not write it, so the definition lives here, the batch
    door calls it, and ``foundry_add_defect`` keeps its literal until casting 2
    imports this name. Until then the agreement is held by
    ``tests/test_orchestrator_gates.py`` — see
    ``test_both_filing_doors_persist_one_record_shape_over_the_wire``, which
    files ONE finding through both doors over MCP and refuses any difference
    outside ``id`` and ``created_at``.
    """
    record = {
        "id": record_id,
        # ST-001: the server's counter is the authority, full stop.
        "cycle": cycle,
        # D-119: the caller's asserted cycle is persisted beside the server's,
        # never instead of it, so a divergence is visible to migrate and
        # escalation tooling instead of silent.
        "declared_cycle": declared_cycle,
        "source": source,
        "type": defect_type,
        # CT-001 / FR-004: the evidence axis, on every record. Written from the
        # validated value, so a persisted record's tier is always a member of
        # DEFECT_TIERS — vocab.TIER_UNKNOWN is a READ-side sentinel for records
        # written before this release and is NEVER written by a door.
        "tier": tier,
        # CT-002 / FR-007: unconditional, because the validator has already
        # refused an absent or blank class. A record without the key can no
        # longer be produced, so escalation never has to handle one.
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
        # gets `None` from an open defect rather than a KeyError.
        "regression_test": None,
        "authored_by": None,
        "fix_commit": None,
        "created_at": created_at if created_at is not None else _now(),
    }
    if target_kind:
        record["target_kind"] = target_kind
    return record


# D-049 / CT-002 / AC-019 — what makes an incoming finding the SAME defect
# coming back.
#
# The matcher was `symbol == fixed.symbol OR description == fixed.description`,
# and a hit reopened the old record, DISCARDED the incoming finding entirely,
# and returned ok:true. Two drives at the MCP boundary:
#
#   - A prove/MISSING/FR-003 finding on symbol `submit_form` ("no CSRF
#     validation on the POST branch") was absorbed into a fixed
#     trace/UNWIRED/FR-001 record on the same symbol. added=0, reopened=1, one
#     record on disk still carrying the OLD source, type, spec_ref and
#     description. The new finding's four content fields were thrown away and
#     the caller was told the call succeeded.
#   - Description equality ALONE reopened a fixed defect across a different
#     file AND a different symbol.
#
# CT-002's contract is "records accepted and attributed to their true source"
# and AC-019's is "source attribution is preserved verbatim". Neither can hold
# for a record that was never written — this is worse than the source coercion
# the same function was fixed for, because coercion at least leaves a row.
#
# A regression is the same defect RECURRING, so identity is a conjunction:
#
#   1. No non-empty field may CONFLICT. A different symbol, file, type or
#      spec_ref means a different defect however much else agrees — that is
#      what killed the cross-file description match.
#   2. At least _REGRESSION_MIN_AGREEMENTS non-empty fields must AGREE. One
#      lone signal is a coincidence, not an identity — that is what killed the
#      same-symbol-different-everything match.
#
# Empty on either side is neither agreement nor conflict: an absent field
# carries no information and must not be allowed to manufacture either verdict
# (two records that both omit `file` have not thereby agreed about anything).
#
# Deliberately conservative, and its failure direction is the safe one: a
# genuine regression whose description was reworded between cycles is filed as
# a NEW defect — a record that exists, correctly attributed, at the cost of a
# `regressions` count that under-reports. The old failure direction was
# silent data loss on the highest-volume filing path in the protocol.
_REGRESSION_IDENTITY_FIELDS = ("symbol", "file", "type", "spec_ref", "description")
_REGRESSION_MIN_AGREEMENTS = 2


def _identity_value(raw) -> str:
    """Normalise an identity field for comparison: whitespace-folded, casefolded."""
    if not isinstance(raw, str):
        raw = "" if raw is None else str(raw)
    return " ".join(raw.split()).casefold()


def _is_regression_of(finding: dict, norm: dict, fixed_record: dict) -> bool:
    """Is ``finding`` the previously-fixed ``fixed_record`` recurring?

    See the block comment above for why this is a conjunction rather than the
    disjunction it replaces. ``norm`` carries the batch validator's canonical
    defect type so the incoming type is compared on the same footing as the
    stored one (MISPLACED and ARCHITECTURAL_PLACEMENT are one type under two
    spellings, and comparing raw would read them as a conflict).
    """
    agreements = 0
    for field in _REGRESSION_IDENTITY_FIELDS:
        if field == "type":
            incoming = _identity_value(norm.get("type"))
            existing = _identity_value(
                canonical_defect_type(fixed_record.get("type") or "") or ""
            )
        else:
            incoming = _identity_value(finding.get(field))
            existing = _identity_value(fixed_record.get(field))
        if not incoming or not existing:
            # Absent on either side: no information, so neither an agreement
            # nor a conflict.
            continue
        if incoming != existing:
            return False
        agreements += 1
    return agreements >= _REGRESSION_MIN_AGREEMENTS


@ledger_refusals
def foundry_sync_defects(
    cycle: int,
    findings: list[dict],
    project_root: str = ".",
) -> dict:
    """Sync new findings against existing defects. Detects regressions.

    FR-013 / CT-002 / NFR-002. This was the unvalidated door into the ledger:
    43 of grand-vulture's 168 defects (26%) entered through it. ``source`` was
    matched against a local set that agreed with neither the tool schema nor
    the stream vocabulary, and anything outside it was silently rewritten to
    "trace" \u2014 so a research_audit or coverage_diff finding was persisted as if
    TRACE had found it, and the run's evidence pointed at the wrong stream.
    ``type`` was written straight through with no validation at all.

    Both fields are now checked against the canonical vocabulary and the
    recorded source survives verbatim. Per A-035 the coercion was a bug, not a
    contract: values the old code accepted only by rewriting them are refused
    with a named error. NFR-002's no-narrowing guarantee covers calls that are
    valid under the reconciled vocabulary, and every such call still works.

    Validation is ALL-OR-NOTHING. A batch with one bad finding is refused whole
    rather than partly applied, so the caller never has to guess which of its
    findings landed.
    """
    from foundry_mcp.tools.foundry import (
        _dict_records,
        _observation_refusal,
        ledger_transaction,
        record_denylist_tripwire,
        retier_matching_untiered,
        tripwire_finding,
        validate_defect_filing,
    )

    fdir = get_run_dir(project_root)
    if not fdir:
        return {"error": "No active foundry run."}
    if (corrupt := _artifact_guard(fdir)):
        return corrupt
    defects_path = fdir / "defects.json"

    # --- Validate the whole batch before writing anything ------------------
    refusals: list[dict] = []
    normalized: list[dict] = []
    for i, finding in enumerate(findings):
        # CT-002 / D-064 — THE INPUT SIDE OF THE LOOP, GUARDED LIKE THE STORED
        # SIDE.
        #
        # D-128 celebrated closing exactly this class for the records ALREADY IN
        # the ledger (`_dict_records`) and left the caller's own list unguarded,
        # so `findings=['not-a-dict']` raised `AttributeError: 'str' object has
        # no attribute 'get'` on the very next line. `@ledger_refusals` does not
        # catch it, so server.py's outer net rendered "This is an unhandled
        # server-side error, not a refusal" — naming no index and no field,
        # against CT-002's requirement that the batch door refuse NAMING the
        # offending finding. `normalized` is appended to in the same breath so
        # the two lists stay index-aligned: the refusal loop below reads
        # `normalized[refused["index"]]["source"]`, and a `continue` that skipped
        # the append would make every later index name the wrong finding.
        if not isinstance(finding, dict):
            refusals.append({
                "index": i,
                "field": "finding",
                "value": repr(finding)[:120],
                "reason": (
                    f"findings[{i}] is {type(finding).__name__}, not an object. "
                    "Every finding is a mapping carrying at least source, type, "
                    "tier, defect_class and description."
                ),
            })
            normalized.append({
                "source": "", "type": None, "tier": None,
                "class": None, "reproduction_attempted": None,
            })
            continue

        source = finding.get("source", "")
        if not isinstance(source, str) or not source.strip():
            refusals.append({
                "index": i,
                "field": "source",
                "value": source,
                "reason": (
                    "source is required \u2014 an unattributed finding used to be "
                    "recorded as 'trace', which is the mis-attribution this "
                    "check exists to stop. Must be one of: "
                    f"{', '.join(sorted(DEFECT_SOURCE_IDS))}"
                ),
            })
        elif source.strip() not in DEFECT_SOURCE_IDS:
            refusals.append({
                "index": i,
                "field": "source",
                "value": source,
                "reason": (
                    f"Unknown source: {source}. Must be one of: "
                    f"{', '.join(sorted(DEFECT_SOURCE_IDS))}"
                ),
            })

        raw_type = finding.get("type") or "MISSING"
        canonical = canonical_defect_type(raw_type)
        if canonical is None:
            refusals.append({
                "index": i,
                "field": "type",
                "value": raw_type,
                "reason": (
                    f"Unknown defect_type: {raw_type}. Must be one of: "
                    f"{', '.join(sorted(DEFECT_TYPES))}"
                ),
            })

        # CT-001 / CT-002 / CT-003 — THE TIER, CLASS AND LATENT CHECKS, DECIDED
        # BY THE SHARED VALIDATOR AND NOT BY THIS DOOR.
        #
        # There are two filing doors in two modules — `foundry_add_defect` in
        # tools/foundry.py and this one — and every check they were each trusted
        # to remember has eventually diverged. D-119 is the shipped instance:
        # the two disagreed about which cycle a record belonged to, so identical
        # findings filed through different doors produced different cycle runs
        # and a systemic class escaped ST-002 escalation entirely. A filing whose
        # tier the single door demands and the batch door does not is that defect
        # one field along, and WORSE — this is the door a whole INSPECT stream
        # files through, so the gap would be the common path rather than the rare
        # one.
        #
        # `validate_defect_filing` is therefore called, never re-implemented. It
        # owns the locked check order — the security denylist FIRST (D-061 moved
        # it there: both doors fire `record_denylist_tripwire` only on a refusal
        # carrying `denylist_class`, so a security claim that tripped the tier
        # rung first was refused with no audit record at all), then tier, then
        # class, then reproduction_attempted — so both doors name the same field
        # first for the same
        # bad filing, and it reads the mapping and nothing else — no ledger, no
        # run dir — which is what lets this door run it once per finding BEFORE
        # opening its transaction. A refusal here costs nothing and writes
        # nothing.
        #
        # Stated AFTER the source/type rungs so a finding that is wrong in both
        # ways still names `source` first, exactly as it did before this landed.
        #
        # D-098 — ONE PIPELINE ORDER, OR THE TWO DOORS ARE NOT ONE RULE.
        # -------------------------------------------------------------
        # The comment-prose rung sat at a DIFFERENT POINT in each door's
        # pipeline, so the two doors disagreed about what a defect is.
        # `foundry_add_defect` runs `_observation_refusal` BEFORE
        # `validate_defect_filing` and long before the re-tier; this door ran
        # `validate_defect_filing`, then `retier_matching_untiered`, and reached
        # its declared-comment branch only for findings that matched neither.
        #
        # Driven on one seeded ledger holding an open untiered D-001, with a
        # `target_kind="comment"` LATENT finding whose description is
        # ENUMERATION-classed and whose (source, type, file, symbol) matches
        # D-001: `Foundry-Defect` returned "Refused: ENUMERATION is a
        # comment-prose observation class, not a defect" and left D-001 open and
        # untiered; `Foundry-Sync` returned {'ok': true, 'retiered': 1,
        # 'retiered_ids': ['D-001']} and D-001 came out carrying LATENT. Same
        # finding, same ledger, opposite outcomes — one door refused it as an
        # observation, the other converted a blocking untiered record into a
        # tracked defect and reported success. Driven again with tier='MEDIUM'
        # on the same finding, `Foundry-Defect` named the observation class and
        # this door named `tier`, contradicting `validate_defect_filing`'s own
        # pinned contract ("THE CHECK ORDER IS LOCKED, so that the two doors
        # name the same field first for the same bad filing") and reproducing
        # the exact drift its "WHY THIS IS A SHARED FUNCTION" section exists to
        # prevent.
        #
        # LEAD RULING, GRIND cycle 6: ONE order for both doors, hosted in one
        # place both call — comment-prose refusal, security denylist, tier,
        # class, reproduction_attempted, re-tier match, persist. So the rung
        # runs HERE, in the pre-transaction validation loop, ahead of
        # `validate_defect_filing`: putting it merely ahead of the re-tier
        # branch would not have closed the field-order half, because the tier
        # rung fires in this loop and the re-tier branch is two frames later,
        # inside the transaction.
        #
        # `_observation_refusal` is CALLED, not re-implemented — the same
        # function `foundry_add_defect` calls, so the two doors cannot drift
        # about which findings are comment prose. Its own three guards
        # (declared-comment subject, denylist outranks, promote-direction
        # fail-safe) all still apply, so a security claim, a spec-required
        # behaviour claim or a finding asserting what the code does still
        # reaches the tier rung below and is still filed as a defect.
        #
        # THE REFUSAL TEXT IS THE OTHER DOOR'S, VERBATIM. "Identical outcomes"
        # is the ruling's test, and a stream that files the same finding through
        # either door now reads the same sentence and is sent to the same place.
        refused_class = _observation_refusal(finding)
        if refused_class is not None:
            refusals.append({
                "index": i,
                "field": "description",
                "value": finding.get("description", ""),
                "reason": (
                    f"Refused: {refused_class} is a comment-prose observation "
                    f"class, not a defect. The comment-prose classes are: "
                    f"{', '.join(sorted(OBSERVATION_CLASSES))}."
                ),
                "refused_class": refused_class,
                "observation_classes": sorted(OBSERVATION_CLASSES),
            })
            # The tier rung is SKIPPED for it, exactly as it is skipped on the
            # other door: a finding that is comment prose is not a defect at
            # all, and telling its filer about a missing tier would send them to
            # add a field to a record that should never reach this ledger.
            # `normalized` is still appended below, because the refusal loop and
            # the write loop both index into it.
            filing_problem = None
        else:
            filing_problem = validate_defect_filing(finding)
        if filing_problem is not None:
            refusals.append({
                "index": i,
                "field": filing_problem.get("field", "tier"),
                "value": finding.get(filing_problem.get("field", "tier")),
                "reason": filing_problem.get("error", ""),
                **(
                    {"denylist_class": filing_problem["denylist_class"]}
                    if "denylist_class" in filing_problem
                    else {}
                ),
            })

        declared_class = finding.get(DEFECT_CLASS_FIELD)
        reproduction = finding.get("reproduction_attempted")
        normalized.append({
            "source": source.strip() if isinstance(source, str) else source,
            "type": canonical,
            "tier": finding.get("tier"),
            "class": (
                declared_class.strip() if isinstance(declared_class, str) else declared_class
            ),
            "reproduction_attempted": (
                reproduction.strip() if isinstance(reproduction, str) else reproduction
            ),
        })

    if refusals:
        # CT-003 \u2014 THE SECURITY REFUSAL ALSO WRITES THE TRIPWIRE.
        #
        # A LATENT filing matching the security-property predicate is refused,
        # and the ATTEMPT is what the audit record exists to capture: a stream
        # trying to file a security claim as a gap it merely reasoned about is
        # the thing an auditor needs to see, and the refusal alone leaves no
        # trace of it. Fired through the ONE exported writer that
        # `foundry_add_defect` and `foundry_add_observation` also call \u2014 a
        # second writer here would be an audit control that only records the
        # attempts one of its callers makes.
        #
        # D-147 / D-146 — AND THE RECORD NAMES THE CLASS THE REFUSAL NAMED.
        #
        # `record_denylist_tripwire` does not receive the refusal's class; it
        # RE-DERIVES one through `vocab.never_demote_class`, whose security
        # entry reads `description` alone, while the refusal above keys on ALL
        # the prose a filing carries. So a finding whose security claim lives
        # in some other key was refused SECURITY_PROPERTY_CLAIM and audited
        # NON_COMMENT — one event, two artifacts that contradict each other,
        # which is exactly what D-083 pinned may never happen. `foundry_add_defect`
        # already wraps its finding; this door did not, and two doors trusted
        # to remember one step each is the arrangement that keeps diverging.
        for refused in refusals:
            if refused.get("denylist_class"):
                record_denylist_tripwire(
                    fdir,
                    tripwire_finding(findings[refused["index"]]),
                    cycle=_current_cycle(fdir),
                    source=normalized[refused["index"]]["source"],
                )
        return {
            "error": (
                f"Refused {len(refusals)} finding(s) \u2014 no findings were recorded. "
                + "; ".join(f"findings[{r['index']}].{r['field']}: {r['reason']}" for r in refusals)
            ),
            "refusals": refusals,
            "hint": (
                "Fix the named fields and re-send the whole batch. Values are "
                "never coerced onto a known member \u2014 a finding attributed to the "
                "wrong stream is worse than a finding refused."
            ),
        }

    # The cycle a defect is stamped with is the SERVER's (FR-005). A
    # caller-asserted cycle cannot be trusted for persistence: the whole
    # three-cycle escalation rule reads these numbers back.
    server_cycle = _current_cycle(fdir)

    reopened = 0
    added = 0
    retiered = 0
    retiered_ids: list[str] = []
    regressions: list[str] = []
    tripwires: list[dict] = []

    # One exclusive critical section over defects.json for the whole batch.
    # AC-025's uniqueness guarantee comes from THIS lock: allocate_record_id is
    # pure, so minting inside the transaction is what stops two concurrent
    # filers reading the same snapshot and claiming the same id.
    with ledger_transaction(defects_path, "defects") as records:
        # D-128 — the half of D-097 that was never applied.
        #
        # `_dict_records` was added to foundry.py BY D-097, with the docstring
        # "Tolerating it in ONE place is what keeps the two halves of the same
        # scan consistent", and then applied to the single door only. This
        # batch door kept its raw `d.get(...)` over every historical record.
        # Driven with {"defects": [{"id":"D-001",...}, "not-a-dict"]}:
        # Foundry-Defect persisted D-002 and preserved the malformed record,
        # Foundry-Defects read clean, and Foundry-Sync raised AttributeError
        # 'str' object has no attribute 'get'. The raise lands AFTER the
        # in-transaction list is mutated, so `_save_json` never runs, the
        # filing the stream just made is GONE, and the escaped traceback names
        # no file -- the one row breaking D-095/D-098's 21/24 named-refusal
        # bar.
        #
        # THE PRIMITIVE NOW CLOSES THIS TOO. D-127 landed in foundry.py during
        # this same cycle and moved the filter INSIDE `ledger_transaction`,
        # which yields mapping records only and re-inserts the non-dicts by
        # index before the write. So this call is idempotent today rather than
        # load-bearing, and it is kept deliberately on both counts: it is what
        # the scan in test_orchestrator_gates.py asserts (an orchestrator that
        # iterates the binding directly has bypassed the guarantee however
        # careful its body is), and it keeps this door's correctness legible
        # here instead of resting silently on a sibling module's internals.
        # Two guards on one class from both sides is the shape the escalated
        # class asks for; one guard in a file this casting may not edit is not.
        fixed = [d for d in _dict_records(records) if d.get("status") == "fixed"]

        for finding, norm in zip(findings, normalized):
            symbol = finding.get("symbol", "")
            desc = finding.get("description", "")

            match_id = None
            for fd in fixed:
                if _is_regression_of(finding, norm, fd):
                    match_id = fd["id"]
                    break

            if match_id:
                for d in _dict_records(records):
                    if d.get("id") == match_id:
                        d["status"] = "open"
                        d["regression"] = True
                        d["reopened_in_cycle"] = server_cycle
                        d["fixed_in_cycle"] = None
                        break
                reopened += 1
                regressions.append(match_id)
                continue

            # FR-051 / D-062 — "BLOCKS LIKE LIVE UNTIL A STREAM RE-FILES IT
            # WITH A TIER", IMPLEMENTED AS AN ACTUAL EXIT.
            #
            # `_blocking_defects` tells the lead to "have the filing stream
            # re-file each untiered defect with tier=LIVE or tier=LATENT", and
            # following that hint made the ledger strictly worse: the batch door
            # only ever reopened a record already `fixed` or appended a new one,
            # so the identical finding came back as a SECOND open record beside
            # the untiered one. Driven: one open untiered D-001, the same finding
            # re-filed with tier LATENT -> {'added': 1, 'total_open': 2},
            # `blocking` unchanged at 1. Every `tier` write in src/ was on
            # new-record construction; no branch updated an existing open record.
            # A resumed pre-change run therefore had no cheap exit at all — only
            # Foundry-Fix, which resolves such a record to TIER_UNKNOWN and
            # demands the full LIVE ceremony on a finding no stream classified.
            #
            # Identity is (source, type, file, symbol): the four fields that say
            # WHICH finding this is. The description is deliberately excluded —
            # a re-filing stream rewrites its prose, and requiring the wording to
            # match would make the exit unreachable for the same reason the hint
            # was. The record KEEPS ITS ID, so every citation and every task
            # already naming it stays valid, and `retiered_in_cycle` records when
            # the classification arrived.
            # D-077 — ONE RULE, TWO DOORS, ONE IMPLEMENTATION.
            #
            # The match-and-re-tier rule lived twice: this loop, and a second
            # copy inside `foundry_add_defect`. Two copies of "which stored
            # record IS this finding" is the same drift shape the tier read, the
            # sweep, the handoff writer and the report generator are each
            # deliberately owned by ONE module — and it is worse here, because
            # the two doors disagreeing means a stream's exit from an untiered
            # record depends on WHICH door it happened to file through.
            # `foundry.retier_matching_untiered` is now that one implementation
            # (casting 2 owns `tools/foundry.py`); this door calls it and
            # re-implements nothing. It returns the id of the record it
            # re-tiered, or None when no open untiered record matches on
            # (source, type, file, symbol).
            retier_id = retier_matching_untiered(
                records,
                source=norm["source"],
                type=norm["type"],
                file=finding.get("file") or "",
                symbol=symbol or "",
                tier=norm["tier"],
                reproduction_attempted=norm["reproduction_attempted"],
                defect_class=norm["class"],
                cycle=server_cycle,
            )
            if retier_id is not None:
                retiered += 1
                retiered_ids.append(retier_id)
                continue

            # Comment-prose findings are OBSERVATIONS, not defects, and are
            # refused from this ledger. Four rules apply, in this order, and
            # they are the SAME rules the Foundry-Defect filing path applies —
            # two filing paths that disagree about what a defect is would be a
            # worse bug than the one being fixed:
            #
            #   1. The subject must be a DECLARED comment. An absent
            #      target_kind does not license a demotion: vocab's
            #      is_non_comment only matches a target_kind that is present
            #      and non-"comment", so absence has to be handled here or the
            #      NON_COMMENT denylist entry silently never fires.
            #   2. A denylist match OUTRANKS an observation match (vocab's
            #      precedence rule) — a security claim, a spec-required-
            #      behaviour claim or an unresolvable cite stays a defect even
            #      when its prose reads like drift.
            #   3. A finding that ASSERTS WHAT THE CODE DOES is not confined to
            #      comment prose, so no comment-prose refusal may fire against
            #      it however its wording reads (D-094).
            #   4. Only then does the observation class decide.
            #
            # If the ledger writer refuses the demotion anyway, its refusal
            # wins and the finding stays a defect too: AC-002 is a never-weaken
            # guarantee, so every branch fails safe toward "defect".
            declared_comment = (
                isinstance(finding.get("target_kind"), str)
                and finding["target_kind"].strip().lower() == "comment"
            )
            if declared_comment:
                # D-036 — the denylist decision AND its audit signal are ONE
                # exported call.
                #
                # This read `never_demote_class(finding) is None` and skipped
                # the whole branch on a match. The finding correctly stayed a
                # defect, but nothing downstream ran, so
                # `record_denylist_tripwire` — which tools/foundry.py exports
                # precisely for this call site, and whose own docstring names
                # it — never fired on the Sync path. Live-proved: a
                # SECURITY_PROPERTY_CLAIM comment finding filed through
                # Foundry-Sync stayed a defect (the enforcement half, correct)
                # and left observations.json's `tripwire` empty (the audit
                # half, dead). An audit signal that fires only for the filing
                # path that did not need auditing is not a control.
                #
                # Calling the helper makes the same decision the local check
                # made and writes the signal as it does. Its NON_COMMENT
                # fallback cannot fire under this guard — the helper's
                # `_subject_is_declared_comment` is the same predicate as
                # `declared_comment` above — so a non-None return means, still
                # and only, "a denylist entry matched: keep this a defect".
                #
                # It is scoped to declared_comment deliberately. A finding with
                # no `target_kind` is not attempting a demotion (Sync has no
                # classification argument, so target_kind is the only demotion
                # signal on this path), and auditing those would fire a
                # NON_COMMENT tripwire on every ordinary defect and bury the
                # real ones.
                denied = record_denylist_tripwire(
                    fdir, finding, cycle=server_cycle, source=norm["source"]
                )
                if denied is not None:
                    tripwires.append(denied)
                # D-098 — THE DEMOTION BRANCH IS GONE; THE AUDIT SIGNAL STAYS.
                #
                # What stood here was an `elif asserts_code_behaviour(finding)`
                # pass-through (D-094's promote-direction fail-safe) and an
                # `else` that routed the finding into `observations.json` and
                # `continue`d. Both are now decided one frame earlier, by
                # `_observation_refusal` in the validation loop above — which
                # applies the SAME three guards in the SAME order — so a finding
                # that reaches this line is one that rung already let through:
                # it is denylisted, it asserts what the code does, or its prose
                # matches no observation class. All three are DEFECTS, and
                # falling out of this branch into the append below is what each
                # of them means.
                #
                # The routing had to go because it was the last thing making the
                # two doors disagree: `foundry_add_defect` REFUSES comment prose
                # and names Foundry-Observation, and a batch door that silently
                # wrote it somewhere else instead was not the same rule applied
                # twice. Streams file comment prose through Foundry-Observation,
                # which is the channel `agents/*.md` already instructs and which
                # the refusal above names.
                #
                # `record_denylist_tripwire` stays exactly where D-036 put it.
                # It is the AUDIT half, not the enforcement half: it fires for a
                # declared-comment finding a denylist entry rescued, which is
                # precisely the case `_observation_refusal` returns None for and
                # therefore never refuses. Removing it would re-open D-036 — an
                # audit signal that fires only for the filing path that did not
                # need auditing.

            # C-2 / CT-001 / D-192 — THE SHAPE IS `new_defect_record`'S, NOT A
            # SECOND LITERAL THAT MERELY CLAIMS TO MATCH IT.
            #
            # What stood here was the same eighteen keys hand-typed a second
            # time, under a comment asserting "the batch door writes the SAME
            # record shape as the single door, field for field". The assertion
            # was false for `target_kind`, which the single door persisted and
            # this one dropped — see `new_defect_record` for the driven
            # divergence and for why a comment is not a mechanism.
            #
            # `validate_defect_filing` has already refused an absent or unknown
            # tier, an absent class and a LATENT filing with no negative result,
            # so every value passed below is a validated one.
            defect = new_defect_record(
                record_id=_mint_defect_id(records),
                cycle=server_cycle,
                declared_cycle=cycle,
                source=norm["source"],
                defect_type=norm["type"],
                tier=norm["tier"],
                defect_class=norm["class"],
                reproduction_attempted=norm["reproduction_attempted"],
                description=desc,
                spec_ref=finding.get("spec_ref", ""),
                symbol=symbol,
                file_path=finding.get("file", ""),
                # D-192: the declaration the caller made, carried ONTO the
                # record. This door read `target_kind` only as a demotion signal
                # (the `declared_comment` branch above) and then discarded it,
                # so a Sync-filed defect could not be audited for a declaration
                # the same finding preserves when it is filed one door over.
                target_kind=finding.get("target_kind") or "",
                created_at=_now(),
            )
            records.append(defect)
            added += 1

        total_open = sum(1 for d in _dict_records(records) if d.get("status") == "open")

    if regressions:
        forge_log = fdir / "forge-log.md"
        if forge_log.exists():
            with open(forge_log, "a", encoding="utf-8") as f:
                f.write(f"\n### REGRESSIONS in cycle {server_cycle}\n")
                for r in regressions:
                    f.write(f"- **{r}** reopened \u2014 fix was fragile\n")
                f.write("\n")

    result = {
        "ok": True,
        "cycle": server_cycle,
        "declared_cycle": cycle,
        "added": added,
        "reopened": reopened,
        # D-098: `observations` and `observed` are GONE from this result. They
        # counted a demotion this door no longer performs — comment prose is
        # refused here exactly as it is at `foundry_add_defect`, and the filer
        # is named Foundry-Observation. Reporting a permanently-zero count of a
        # thing that cannot happen is how a reader concludes the channel is
        # merely quiet.
        "regressions": regressions,
        # FR-051 / D-062: re-filings that CLASSIFIED an existing untiered record
        # rather than appending a duplicate beside it. Reported so a lead
        # following `_blocking_defects`' hint can see the exit happened.
        "retiered": retiered,
        "retiered_ids": retiered_ids,
        "total_open": total_open,
    }
    if tripwires:
        result["denylist_tripwires"] = tripwires
    return result


def foundry_defects_to_tasks(
    project_root: str = ".",
) -> dict:
    """Convert ALL open defects to grouped task descriptions for GRIND.

    FR-008 / ST-003 / AC-010. Defects of an ESCALATED class are lifted out of
    the location grouping and emitted as exactly ONE structural-fix packet per
    class, carrying a recorded proposal. Every other defect groups by location
    exactly as before — escalation changes the shape of one class's work and
    nothing else. An explicit ``escalation-override`` directive de-escalates a
    class, at which point its defects fall straight back into the per-instance
    grouping (AC-010).
    """
    fdir = get_run_dir(project_root)
    if not fdir:
        return {"error": "No active foundry run."}
    if (corrupt := _artifact_guard(fdir)):
        return corrupt
    data = _load_json(fdir / "defects.json")
    open_defects = [d for d in data.get("defects", []) if d.get("status") == "open"]

    escalated = _escalated_classes(fdir, project_root)
    # D-043: REGENERATED, never carried forward. This read
    # `info.get("proposal") or _structural_proposal(info)`, and `info["proposal"]`
    # is whatever `escalation.json` recorded on some earlier cycle — so the first
    # proposal a class ever drew was the one every later packet and every later
    # report carried, open counts and defect ids frozen at that moment. A class
    # whose three instances had since been FIXED still had the run's own final
    # report asserting it "still has 3 open instance(s)" and that all three
    # "must reach fixed". The string is cheap; the staleness was not.
    for key, info in escalated.items():
        info["proposal"] = _structural_proposal(info)
    # D-043: recorded BEFORE the nothing-to-do return below, not after. The
    # derived views on the escalation record — the proposal, `defect_ids`, the
    # LATENT backlog — are answers about the CURRENT ledger, and the run state
    # in which they most need refreshing is exactly the one this function used
    # to return from first: every instance fixed, nothing left to packet. The
    # record then kept the last thing said about the class while it still had
    # open work, and the F6 report printed it.
    _record_escalation_proposals(fdir, escalated)

    if not open_defects:
        return {"ok": True, "tasks": [], "count": 0, "escalated_classes": []}

    # ST-002 / FR-002 — THE STRUCTURAL-PASS BUDGET, SPENT HERE.
    #
    # One structural pass plus one retry, and then no more. The budget is
    # consumed by DISPATCH, so it is counted at the one place a structural
    # packet is ever emitted, and the class CLEARS on the crossing that spends
    # the last of it. Two passes is not a judgement that the class is fixed: it
    # is the statement that structural work has had its turn. Open LIVE
    # instances stay blocking defects and fall into the per-instance grouping
    # below on the very next call (AC-003); open LATENT instances go to the F6
    # named backlog (FR-001). Nothing is waived — only the SHAPE of the work
    # stops changing.
    #
    # D-058: this tool DISPATCHES and COUNTS. The exit itself is applied at the
    # `inspect_start` boundary that closes the cycle the budget-exhausting
    # packet was dispatched in — because ST-002's trigger is the second packet
    # CLOSING, and a packet dispatched by this call has not closed while this
    # call is still returning it. Clearing here retracted the class inside the
    # same call that emitted its packet, so a second `Foundry-Tasks` in the same
    # cycle — which a lead may make, and which is why `structural_packet_cycles`
    # exists — reported structural_tasks 0 for work still being done.
    packet_cycle = _current_cycle(fdir)
    packets_counted = _spend_structural_budget(
        fdir, project_root, escalated, packet_cycle
    )

    escalated_ids = {did for info in escalated.values() for did in info["defect_ids"]}

    tasks = []

    # One packet per escalated class, emitted first so it leads the GRIND wave.
    for key in sorted(escalated):
        info = escalated[key]
        members = [d for d in open_defects if d["id"] in set(info["defect_ids"])]
        tasks.append({
            "structural": True,
            "defect_class": key,
            "class_declared": info["declared"],
            "consecutive_cycles": info["consecutive_cycles"],
            "cycles_seen": info["cycles"],
            "proposal": info["proposal"],
            "defect_ids": info["defect_ids"],
            "description": info["proposal"],
            "instances": [
                {"id": d["id"], "description": d.get("description", ""),
                 "file": d.get("file", ""), "symbol": d.get("symbol", "")}
                for d in members
            ],
            "files": info["files"],
            "symbols": info["symbols"],
            "spec_refs": info["spec_refs"],
            "regression": any(d.get("regression") for d in members),
            "source": info["sources"][0] if info["sources"] else "unknown",
        })

    MAX_PER_GROUP = 3
    groups: dict[str, list[dict]] = {}
    for d in open_defects:
        if d["id"] in escalated_ids:
            continue
        key = d.get("file") or d.get("symbol") or d["id"]
        groups.setdefault(key, []).append(d)

    for key, defects in groups.items():
        for i in range(0, len(defects), MAX_PER_GROUP):
            chunk = defects[i:i + MAX_PER_GROUP]
            task = {
                "structural": False,
                "defect_ids": [d["id"] for d in chunk],
                "description": "; ".join(d["description"] for d in chunk),
                "files": list({d["file"] for d in chunk if d.get("file")}),
                "symbols": list({d["symbol"] for d in chunk if d.get("symbol")}),
                "spec_refs": list({d["spec_ref"] for d in chunk if d.get("spec_ref")}),
                "regression": any(d.get("regression") for d in chunk),
                "source": chunk[0].get("source", "unknown"),
            }
            tasks.append(task)

    (fdir / TASKS_GENERATED_MARKER).write_text(f"{_now()} count={len(tasks)}\n", encoding="utf-8")

    result = {
        "ok": True,
        "tasks": tasks,
        "count": len(tasks),
        "escalated_classes": sorted(escalated),
        "structural_tasks": sum(1 for t in tasks if t["structural"]),
    }
    if packets_counted:
        # AC-004: what this call SPENT, reported where it happened. A lead that
        # dispatched the budget-exhausting packet needs to know it was the last
        # one this class will get — the exit itself lands at the next
        # `inspect_start`, when the packet has closed, and is reported there.
        result["structural_packets_counted"] = sorted(packets_counted)
    return result


# --- The big one: next action ---


def _stamp_subphase_transitions(fdir: Path) -> None:
    """Auto-stamp F0 / F0.5 / F0.9 transitions based on file-state signals.

    The lead's `state.phase` stays "F0" through RESEARCH / DECOMPOSE / VALIDATE
    and jumps straight to "F1" on start_cast, so without this stamper the
    pre-F1 ~13 minutes appear as one unstructured block. Here we observe:

      - first `castings/casting-*.md` appearing → F0 ends, F0.5 starts
      - `castings/manifest.json` appearing      → F0.5 ends, F0.9 starts
      - `.validate-passed` marker               → F0.9 end time recorded
        (sub-phase still "open" until _update_phase fires at start_cast;
        the marker lets us report validator pass time separately)

    Called from foundry_next_action so every `Foundry-Next` call picks up
    transitions that happened since the last call. Idempotent — only
    writes when a new transition is detected.
    """
    if not fdir or not fdir.exists():
        return
    state_path = fdir / "state.json"
    if not state_path.exists():
        return
    with _document_transaction(state_path) as state:
        _stamp_subphases_in(state, fdir)


def _stamp_subphases_in(state: dict, fdir: Path) -> None:
    """The sub-phase stamping itself, over an already-open state document.

    Split out so the read-modify-write runs inside ``_document_transaction``
    (D-103) without indenting the whole body a level.
    """
    phase_times = state.get("phase_times", {})
    if not isinstance(phase_times, dict):
        phase_times = {}
    now = _now()
    changed = False

    castings_dir = fdir / "castings"
    has_casting_files = castings_dir.exists() and any(castings_dir.glob("casting-*.md"))
    has_manifest = (castings_dir / "manifest.json").exists()
    validate_passed_marker = fdir / VALIDATE_PASSED_MARKER

    def _close(pid: str) -> bool:
        entry = phase_times.get(pid)
        if entry and "started_at" in entry and "ended_at" not in entry:
            _finalize_open_phase_entry(entry, now)
            return True
        return False

    def _open(pid: str) -> bool:
        if pid not in phase_times:
            phase_times[pid] = {"started_at": now}
            return True
        return False

    if has_casting_files:
        changed |= _close("F0")
        changed |= _open("F0.5")
    if has_manifest:
        changed |= _close("F0.5")
        changed |= _open("F0.9")
    if validate_passed_marker.exists():
        entry = phase_times.get("F0.9")
        if entry and "validate_passed_at" not in entry:
            # `except OSError` left UnicodeDecodeError open on a marker file
            # the operator can corrupt as easily as any other (D-137's shape).
            # `_read_text` is total and degrades to "", which the `or now`
            # below already answers.
            entry["validate_passed_at"] = _read_text(validate_passed_marker).strip() or now
            changed = True

    if changed:
        state["phase_times"] = phase_times


def foundry_next_action(
    project_root: str = ".",
    *,
    _arm_stall_clock: bool = True,
) -> dict:
    """Determine what the lead should do next based on current foundry state.

    ``_arm_stall_clock`` is FALSE for exactly one caller, ``foundry_get_context``
    (AC-035 / OT-028). Underscore-prefixed and keyword-only because it is not
    part of the MCP surface: ``Foundry-Next`` has no such argument and never
    will. See the marker block at the end of this function for why the
    distinction exists at all.
    """
    fdir_stamp = get_run_dir(project_root)
    if fdir_stamp and (corrupt := _artifact_guard(fdir_stamp)):
        return corrupt
    trace_skip_decision: dict | None = None
    if fdir_stamp and fdir_stamp.exists():
        _stamp_subphase_transitions(fdir_stamp)
        trace_skip_decision = _maybe_skip_trace(fdir_stamp, project_root)
    result = _compute_next_action(project_root)
    if trace_skip_decision and trace_skip_decision.get("skip"):
        result["trace_skip"] = trace_skip_decision

    # P4 (FR-005 / ST-002): passing-gate → guidance-state advance. If the gate
    # for the current transition action already passed (recorded in
    # ``.gate-passed`` by foundry_gate), surface that the gate is satisfied so
    # the lead proceeds to the transition step rather than re-running the gate.
    gate_advance_note = None
    if fdir_stamp and fdir_stamp.exists():
        expected_gate = _expected_gate_for_action(result.get("action", ""))
        if expected_gate:
            gp_marker = fdir_stamp / GATE_PASSED_MARKER
            if gp_marker.exists():
                gp_data = read_document(gp_marker)[0]
                if gp_data.get("phase") == expected_gate:
                    result["gate_advanced"] = {
                        "passed_gate": expected_gate,
                        "action": result.get("action", ""),
                    }
                    gate_advance_note = (
                        f"✅ Foundry-Gate(phase='{expected_gate}') ALREADY "
                        f"PASSED — do NOT re-run it. Proceed directly to the "
                        f"transition step (Foundry-Phase / state update) in the "
                        f"imperative below."
                    )

    # Stall watchdog. Read the previous `.last-next-at` timestamp BEFORE
    # overwriting it, compute the delta, and if the gap is large surface a
    # visible STALL WARNING at the very top of the instructions. This converts
    # silent extended-thinking runaway into an explicit, logged event the lead
    # must acknowledge on its next turn. State tracking via the existing MCP
    # tool — no hooks.
    #
    # P4 (FR-005 / FR-008): the stall timestamp lives in its OWN marker
    # (``.last-next-at``), decoupled from the ``.next-action-called`` ordering
    # token that foundry_gate / foundry_mark_phase_complete unlink. Because
    # gate/phase no longer destroy the stall timestamp, the stall clock keeps
    # measuring true Foundry-Next → Foundry-Next gaps across intervening
    # gate/phase/read-only calls, so real stalls still warn (FR-008) while
    # ordering-token consumption no longer blinds the watchdog.
    stall_warning = None
    fdir_stall = get_run_dir(project_root)
    if fdir_stall and fdir_stall.exists():
        marker = fdir_stall / LAST_NEXT_AT_MARKER
        if marker.exists():
            try:
                prev_iso = marker.read_text(encoding="utf-8").strip()
                prev = datetime.fromisoformat(prev_iso)
                delta = (datetime.now(timezone.utc) - prev).total_seconds()
                if delta >= STALL_NOTICE_SECONDS:
                    # CT-012 / FR-020 / AC-032 — ASK WHO IS RUNNING BEFORE
                    # ACCUSING.
                    #
                    # This warning fired on the clock alone, so the most common
                    # multi-minute gap in a foundry run — the lead waiting,
                    # correctly, for eight CAST teammates to finish — was
                    # reported as "You were silently deliberating. Stop
                    # deliberating." The lead is trained to obey that literally,
                    # so the accusation actively pushed it to stop waiting and
                    # improvise over half-built work. A watchdog whose false
                    # positive is the NORMAL case is not a watchdog.
                    #
                    # The fix is evidence, not a longer timeout: are there live
                    # teams, and are their progress ledgers moving? When agents
                    # are progressing this reports what it is waiting on and
                    # sets NO stall warning — FR-036's proviso is that the
                    # notice never asserts deliberation while an agent is
                    # progressing. Only when nothing is running is the silence
                    # the lead's own. It never blocks either way (CT-012).
                    liveness = _waiting_on_agents(project_root)
                    minutes = int(delta // 60)
                    seconds = int(delta % 60)
                    if liveness["waiting"]:
                        result["waiting_on_agents"] = liveness
                        stall_warning = (
                            f"\u23f3 WAITING ON {liveness['count']} AGENT(S) "
                            f"({liveness['detail']}). {minutes}m {seconds}s since "
                            f"your last Foundry-Next call — that gap is the "
                            f"agents working, not you deliberating. Do NOT "
                            f"improvise over their half-finished work. Call "
                            f"Foundry-Liveness for per-agent detail, otherwise "
                            f"wait and call Foundry-Next again."
                        )
                    else:
                        stall_warning = (
                            f"\u26a0\ufe0f STALL DETECTED: {minutes}m {seconds}s since your "
                            f"last Foundry-Next call, and NO agent is running. "
                            f"You were silently deliberating. Stop deliberating. Execute the imperative below "
                            f"literally. Do NOT re-read start.md, do NOT run a compliance checklist, do NOT "
                            f"think through edge cases — just run the next tool call. If the imperative is "
                            f"ambiguous, pick any reasonable interpretation and proceed."
                        )
                        result["stall_detected_seconds"] = int(delta)
            except (ValueError, OSError):
                pass

    # Sharpened imperative — lead-line structure. Extract the first actionable
    # call from the computed instructions and emit it as a "YOUR NEXT CALL"
    # header. Context stays in the body for when the lead needs it, but the
    # first line is a single command.
    action = result.get("action", "")
    original_instructions = result.get("instructions", "")
    run_name_for_imperative = fdir_stall.name if fdir_stall and fdir_stall.exists() else ""
    imperative_header = _format_imperative_header(
        action, original_instructions, result.get("details", {}), run_name=run_name_for_imperative
    )

    directives = _read_directives(project_root)
    directive_block = ""
    if directives["has_directives"]:
        result["directives"] = {
            "urgent": directives["urgent"],
            "normal": directives["normal"],
        }
        # FR-019: urgent and normal directives are BOTH rendered. This used to
        # be `if urgent ... elif normal ...`, so a single urgent directive
        # suppressed every standing normal directive for the rest of the run \u2014
        # the human's steering silently stopped reaching the lead. Priority
        # still orders the block; it no longer discards.
        blocks = []
        if directives["urgent"]:
            urgent_text = " | ".join(directives["urgent"])
            blocks.append(
                f"HUMAN DIRECTIVE (urgent): {urgent_text}\n\n"
                "Incorporate the above into your current action."
            )
        if directives["normal"]:
            normal_text = " | ".join(directives["normal"])
            blocks.append(
                f"HUMAN DIRECTIVE: {normal_text} \u2014 incorporate into your approach."
            )
        if blocks:
            directive_block = "\n\n" + "\n\n".join(blocks)

    # D-133: every escalation-override decision is reported in the lead's own
    # output too, not only at the injecting call. A directive is standing text
    # — the lead that reads it may be a different session from the one that
    # sent it, and a marker that matched no escalated class must not look
    # identical to one that de-escalated three.
    if fdir_stall and fdir_stall.exists():
        override = _override_report(fdir_stall, project_root)
        if override["decisions"]:
            result["escalation_overrides"] = override
            directive_block += "\n\nESCALATION-OVERRIDE DECISIONS:\n- " + "\n- ".join(
                override["decisions"]
            )

    # FR-052 / FR-045 / AC-037 / NFR-005 \u2014 THE RULES BLOCK ON A HALTED RUN
    # SAYS WHAT A HALTED RUN NEEDS. D-136.
    #
    # The standing block heads EVERY Foundry-Next payload, and on a halted run
    # it contradicted the payload it was heading. Driven on a halted state, the
    # `instructions` string was the standing block \u2014 "NEVER stop between
    # phases. Call Foundry-Next after each step and follow it", "If you catch
    # yourself thinking, call Foundry-Next and execute whatever it says", "The
    # foundry runs until F6 DONE or an error stops it" \u2014 followed by the halted
    # imperative "YOUR NEXT CALL: NONE ... do NOT call Foundry-Next in a loop
    # ... stop". Dispatch was correctly withheld; the lead-facing TEXT told the
    # lead to do the opposite of the one thing the halt exists to make it do,
    # and the last of those three sentences was false outright \u2014 FR-024 made
    # HALTED a THIRD ending beside DONE and error.
    #
    # Two changes, because the defect has two halves. The standing line now
    # names all three endings, and a halted run gets its own block: the caching
    # argument for a byte-identical prefix (this block is a cache-hit-eligible
    # prefix across ~30-50 calls per run) does not apply to a run that has
    # stopped and will be called at most a handful more times.
    halted_now = result.get("phase") == RUN_PHASE_HALTED
    if halted_now:
        critical_rules = (
            "\n\nCRITICAL RULES \u2014 THIS RUN IS HALTED:"
            "\n- The run stopped at its configured --max-cycles. HALTED is a "
            "terminal state, distinct from DONE and reached by a successful "
            "transition rather than an error (FR-024 / CT-016)."
            "\n- Do NOT dispatch another wave, do NOT call Foundry-Phase, and "
            "do NOT call Foundry-Next in a loop. There is no next transition "
            "to make."
            "\n- The report has been generated as part of the halt and names "
            "every open LIVE and LATENT defect. Read it."
            "\n- Hand the remaining work to a new run, or re-run with a higher "
            "--max-cycles. Nothing below asks you to keep going."
        )
    else:
        critical_rules = _STANDING_CRITICAL_RULES

    # Assemble instructions with stable-first ordering for prompt caching.
    # Every Foundry-Next response is a user-turn message in the lead's single
    # conversation. A stable byte-identical prefix across calls is cache-hit-
    # eligible; the lead calls Foundry-Next ~30-50 times per run, so emitting
    # rules + framing FIRST (before the volatile imperative/CONTEXT/directives)
    # maximizes cache hits on input tokens.
    #
    # Lead attention is preserved by the explicit "═══ YOUR NEXT ACTION ═══"
    # marker: after the rules block, the imperative header's "YOUR NEXT CALL"
    # / "YOUR NEXT CALLS" lead-line remains the action-scanning target that
    # the lead has been trained to find.
    parts = [critical_rules.lstrip()]
    parts.append("\n═══ YOUR NEXT ACTION ═══\n")
    if stall_warning:
        parts.append(stall_warning)
    if gate_advance_note:
        parts.append(gate_advance_note)
    parts.append(imperative_header)
    parts.append("")
    parts.append("CONTEXT:")
    parts.append(original_instructions)
    if directive_block:
        parts.append(directive_block)
    result["instructions"] = "\n".join(parts)

    # AC-033 / CT-013 / FR-021 — MEASURED SPEND, NOT AN ESTIMATE FROM THE CYCLE
    # COUNT.
    #
    # This block used to map the cycle counter onto the words low / moderate /
    # high / critical and call the result "estimated_usage". It read no tokens,
    # no durations and no agent records: a run on cycle 3 was "critical" whether
    # it had spent four hundred thousand tokens or four million. A number that
    # is not measured is worse than no number, because it gets acted on.
    #
    # The roll-ups below are what the lead itself reported through
    # `Foundry-Spend`, per phase, per cycle and for the run. NO DOLLAR FIGURE
    # APPEARS ANYWHERE (AC-033 / OT-023) — this server does not know anyone's
    # rate card, and a cost in money would be a second invented number beside
    # the one just removed.
    fdir_spend = get_run_dir(project_root)
    if fdir_spend and fdir_spend.exists():
        summary = _spend_summary(fdir_spend)
        result["spend"] = summary
        state_spend = _load_json(fdir_spend / "state.json")
        # AC-027 / OT-018 — the executing build, displayed where the lead reads.
        # Written by foundry_init at F0; reported here and nowhere decided.
        result["executing_server"] = {
            "server_version": state_spend.get("server_version", ""),
            "plugin_version": state_spend.get("plugin_version", ""),
            "server_root": state_spend.get("server_root", ""),
            "server_commit": state_spend.get("server_commit", ""),
            "self_target": state_spend.get("self_target", False),
        }
        # GI-008 / GI-009 / CT-009 / FR-049 — REPORTED, NEVER COMPUTED. The
        # transition that opened this INSPECT already decided the width and
        # recorded it; this reads that record back and would return None rather
        # than derive one.
        recorded_mode = _current_inspect_mode(fdir_spend)
        if recorded_mode:
            result["inspect_mode"] = {
                "mode": recorded_mode.get("mode", ""),
                "rule": recorded_mode.get("rule", ""),
                "rule_detail": recorded_mode.get("rule_detail", ""),
                "decided_by": recorded_mode.get("decided_by", ""),
                "decided_at": recorded_mode.get("decided_at", ""),
                "cycle": recorded_mode.get("cycle"),
                "required_streams": recorded_mode.get("required_streams", []),
                "stream_scope": recorded_mode.get("stream_scope", {}),
                "prove_sample": recorded_mode.get("prove_sample", []),
                # AC-019 / FR-012 — THE TRACE HALF OF THE ROSTER, EMITTED.
                # D-140.
                #
                # The transition that opens an INSPECT records the TRACE scope
                # as "symbols in the N file(s) the GRIND touched" and records
                # `touched_files` beside it — and this payload carried cycle,
                # decided_at,
                # decided_by, mode, prove_sample, required_streams, rule,
                # rule_detail and stream_scope, and nothing naming a file. So
                # the roster's PROVE half reached its stream (D-104 wired
                # assayer.md to `prove_sample`) and its TRACE half reached no
                # consumer at all: `agents/tracer.md` and `skills/trace/SKILL.md`
                # contained no occurrence of touched_files, inspect_mode, DELTA
                # or width, and scoped the walk from the spec alone. AC-019 is
                # "in DELTA mode ... TRACE runs over the symbols the GRIND
                # commits touched", which is unreachable by a stream that
                # cannot see which files those are.
                #
                # Emitted from the recorded entry and nowhere computed
                # (GI-008 / GI-009), exactly as `prove_sample` is.
                "touched_files": recorded_mode.get("touched_files", []),
                "diff_base": recorded_mode.get("diff_base", ""),
            }

    result["display"] = _format_status_display(project_root)

    fdir = get_run_dir(project_root)
    if fdir and fdir.exists():
        now_stamp = f"{_now()}\n"
        # Ordering token: armed here, consumed (unlinked) by foundry_gate /
        # foundry_mark_phase_complete to prove Foundry-Next preceded a gate
        # or phase transition.
        (fdir / NEXT_ACTION_CALLED_MARKER).write_text(now_stamp, encoding="utf-8")
        # Stall timestamp: written on every REAL Foundry-Next, read on the next
        # one to measure the gap. Never unlinked by gate/phase, so the watchdog
        # is decoupled from ordering-token consumption (FR-005 / FR-008).
        #
        # AC-035 / OT-028 — AND NOT WRITTEN BY Foundry-Context.
        #
        # `foundry_get_context` calls this function for its `next_action` field,
        # so every Foundry-Context call reached this line and reset the stall
        # clock to now. FR-026 lists it as a ride-along fix because the effect
        # is the opposite of the watchdog's purpose: a lead that deliberates for
        # twenty minutes, calls Foundry-Context to reorient, and then
        # deliberates for twenty more is measured from the Context call and
        # never warned. The clock must measure Foundry-Next to Foundry-Next, and
        # a read-only reorientation call is not one of those.
        #
        # The ordering token above is deliberately still armed on both paths:
        # it answers "did the lead consult guidance before transitioning", and
        # Foundry-Context does return the full guidance payload. Only the
        # STALL measurement is Foundry-Next's alone.
        if _arm_stall_clock:
            (fdir / LAST_NEXT_AT_MARKER).write_text(now_stamp, encoding="utf-8")

    return result


# FR-044 / ST-011 / AC-035 / D-023 — GATE THEN PHASE, AND NOTHING REQUIRED IN
# BETWEEN.
#
# `Foundry-Gate` no longer unlinks the ordering token, so `Foundry-Phase` called
# straight after a passing gate still finds it. FR-044 names TWO surfaces that
# have to carry that rule — "the imperatives and start.md" — and the word
# "optional" appeared ZERO times in the imperatives, so the lead was told to
# make a call the spec had just made unnecessary, on every gate-then-phase path
# in the run.
#
# Written once and appended to each sequence that pairs them, rather than typed
# into three imperatives: this file's own history is that a rule stated in N
# copies becomes a rule stated N different ways.
# D-097 — THE EXCEPTION IS ONE STRING, AND BOTH SURFACES ARE THAT STRING.
#
# The global CRITICAL RULES block that heads EVERY Foundry-Next payload read
# "NEVER stop between phases. Call Foundry-Next after each step and follow it."
# A gate is a step, so the lead met an unconditional instruction at the top of
# the payload and the note that qualifies it at the tail of the imperative,
# hundreds of tokens further down and only on the three imperatives that carry
# it. FR-044's own rationale is that "the word optional appeared ZERO times in
# the imperatives"; adding the note fixed that surface and left the
# contradicting general rule standing above it, so the payload argued with
# itself on every call.
#
# Extracted rather than reworded in two places. `_GATE_THEN_PHASE_NOTE` is now
# a newline plus this constant and the rules block quotes the same constant, so
# the two surfaces are byte-identical by construction and cannot drift into two
# spellings of one rule — which is this file's documented failure mode and the
# whole reason the note was written once and appended.
#
# It names the announced thing BOTH ways ("mode (its width)" and "the rule that
# fired") because commands/start.md calls it the INSPECT "mode" while the
# imperatives called it the "width and rule", and a lead reading the two
# surfaces had to work out they meant the same field.
_GATE_THEN_PHASE_EXCEPTION = (
    "Foundry-Next between a passing Foundry-Gate and its Foundry-Phase is "
    "OPTIONAL — the gate no longer consumes the ordering token, so "
    "Foundry-Phase straight after a passing Foundry-Gate is accepted. That is "
    "where the INSPECT mode (its width) and the rule that fired are announced, "
    "so call it there when you want them; never call it to satisfy the "
    "protocol."
)

_GATE_THEN_PHASE_NOTE = "\n" + _GATE_THEN_PHASE_EXCEPTION


#: D-136 — the standing rules block, named once so the HALTED branch in
#: `foundry_next_action` can stand beside it instead of inside it.
#:
#: This literal used to live inline in that function, which is why nothing
#: could vary it: a halted run received, verbatim, "NEVER stop between
#: phases" and "If you catch yourself thinking, call Foundry-Next and
#: execute whatever it says" immediately above an imperative reading "YOUR
#: NEXT CALL: NONE ... stop". Hoisting it is what makes the two blocks two
#: things rather than one string with a conditional tail.
_STANDING_CRITICAL_RULES = (
    "\n\nCRITICAL RULES:"
    "\n- NEVER ask 'Want me to proceed?' or 'Should I continue?' \u2014 just do it."
    # FR-044 / AC-035 / OT-028 / D-097 — the rule and its ONE exception, in
    # the same sentence, quoting the same constant the imperatives append.
    # This line used to end at "follow it." full stop, which a lead reading
    # top-to-bottom took as unconditional and which contradicted the note
    # `_GATE_THEN_PHASE_NOTE` carries at the tail of the same payload.
    "\n- NEVER stop between phases. Call Foundry-Next after each step and "
    "follow it. REQUIRED everywhere except exactly one place: "
    + _GATE_THEN_PHASE_EXCEPTION
    + " Skipping it there is correct and is not a shortcut; skipping it "
    "anywhere else is."
    "\n- NEVER deliberate for more than 30 seconds between tool calls. If you catch yourself thinking, call Foundry-Next and execute whatever it says."
    "\n- NEVER narrate progress as 'Checkpoint \u2014 X complete', 'Checkpoint reached', 'Milestone \u2014 X', or similar. Foundry has NO checkpoints. You are not a checkpointing orchestrator. Execute the next tool call silently and keep moving."
    "\n- NEVER skip SIGHT because 'no URL.' If frontend files exist, you need a URL. Gate will block."
    "\n- NEVER spawn foundry:teammate agents (CAST or GRIND) with run_in_background=true. They are foreground, TeamCreate-managed, and must run through Foundry-Cast-Wave or Foundry-Spawn-Teammate + verbatim Agent. Background-spawning bypasses the router architecture and breaks spec fidelity."
    "\n- NEVER modify, paraphrase, or augment a prompt returned by Foundry-Spawn-Teammate. Pass it to Agent VERBATIM. GRIND is the only exception: append (a) the `grind_cycle_context` block if returned (prior-cycle file changes) and (b) the '## Defects to fix this cycle:' block BELOW the prompt, in that order. Never inside the prompt."
    "\n- If the user typed a message, treat it as a directive. Absorb and keep going."
    # D-136 — THE THIRD ENDING. This read "The foundry runs until F6 DONE or an
    # error stops it", which FR-024 made false: --max-cycles ends a run in a
    # named HALTED state reached by a SUCCESSFUL transition, which is neither
    # DONE nor an error. A lead reading the old sentence and then receiving a
    # halt has been told the halt cannot happen.
    "\n- Zero approval gates. The foundry runs until it ends: F6 DONE, a HALTED "
    "--max-cycles stop (a successful transition, not an error), or an error."
    "\n- NEVER wait for teammate 'shutdown_response', 'shutdown_ack', idle-confirmation, or any reply after "
    "issuing shutdown. The ONLY shutdown signals foundry recognizes are (a) TeamDelete returning ok and "
    "(b) Foundry-Team-Down succeeding. Narrating 'awaiting shutdown approvals' is a stall \u2014 call TeamDelete "
    "immediately. Idle / terminated panes ARE the signal; TeamDelete cleans them."
)

# Action → imperative-header map. Each action returned by
# _compute_next_action maps to a "YOUR NEXT CALL(S)" directive that the lead
# can execute without re-reading paragraph instructions. Multi-step actions
# MUST enumerate every call — compressing a multi-step sequence into a
# single-line imperative causes the lead to follow the first tool call
# literally and improvise the rest by guessing.
_ACTION_IMPERATIVES = {
    "init": "YOUR NEXT CALL: Foundry-Init (start a new run)",
    # ST-008 / AC-037: the one action whose imperative is to STOP. The generic
    # fallback header says "Execute the first tool call mentioned. Do not
    # deliberate.", which on a halted run would push the lead straight back into
    # the loop the cap ended — so this action gets an explicit entry.
    "halted": (
        "YOUR NEXT CALL: NONE. This run is HALTED — it reached its --max-cycles "
        "limit and stopped with open work. The report is generated. Do NOT "
        "dispatch a wave, do NOT call Foundry-Phase, do NOT call Foundry-Next in "
        "a loop. Read REPORT.md, tell the user what remains open by tier, and "
        "stop."
    ),
    "cleanup_teams": (
        "YOUR NEXT CALLS (in order \u2014 do NOT wait for shutdown acks):\n"
        "  (1) Send shutdown to each teammate: SendMessage(to=<teammate>, message='All work complete, stop working.') "
        "\u2014 one SendMessage per teammate in ONE parallel-tool-use message. Do not use structured messages with "
        "to='*' broadcast \u2014 broadcast rejects structured payloads.\n"
        "  (2) Immediately call TeamDelete for each active team. Do NOT wait for 'shutdown_response' events, "
        "'shutdown_ack' events, idle confirmations, or any teammate reply. Idle / terminated panes ARE the "
        "shutdown signal. TeamDelete cleans zombie panes.\n"
        "  (3) Foundry-Team-Down for each team name.\n"
        "Stalling here is the #1 cleanup failure mode: the lead sends shutdown, sees panes idle, and waits "
        "forever for a reply that never comes."
    ),
    "add_castings": (
        "YOUR NEXT CALL: Spawn 1-5 BACKGROUND Agents in a SINGLE parallel message \u2014 one per "
        "domain identified from the spec. Per-Agent params: model='opus', "
        "subagent_type='general-purpose', mode='bypassPermissions', run_in_background=true, "
        "prompt=<per commands/start.md \u00a7F0.5 DECOMPOSE: write the domain's entry into "
        "manifest.json AND write casting-{id}-prompt.md to foundry-archive/{run}/castings/ "
        "following the layout in start.md \u00a76>. "
        "No team needed \u2014 these are short-lived file writers; TeamCreate ceremony is skipped. "
        "You'll be notified as each completes; use TaskOutput(task_id) to retrieve any return "
        "message. After all complete, call Foundry-Validate-Castings."
    ),
    "transition_to_cast": (
        "YOUR NEXT CALLS (in order — bulk flow saves N-1 roundtrips):\n"
        "  (1) Foundry-Gate(phase='cast')\n"
        "  (2) Foundry-Phase(phase='start_cast')\n"
        "  (3) TeamCreate('cast-{run}-wave-1')\n"
        "  (4) Foundry-Team-Up(team_name='cast-{run}-wave-1')\n"
        "  (5) Foundry-Cast-Wave(wave=1, phase='cast') \u2014 returns ALL wave-1 dispatch blocks in ONE call.\n"
        "  (6) In a SINGLE message (parallel tool use), spawn one Agent per returned casting: "
        "subagent_type='foundry:teammate', mode='bypassPermissions', "
        "prompt=<that casting's `dispatch` field VERBATIM \u2014 it names the prompt FILE and the "
        "sha256 the teammate must read that file to obtain. The `prompt` field is null by "
        "default and is NOT what you pass; do not paste, summarise or augment the prompt text "
        "yourself>. "
        "For the model: obey the model clause in the `instructions` Foundry-Cast-Wave just "
        "returned \u2014 it names the model to pass when the foundry `model` option is configured "
        "(foundry:teammate follows that option) and tells you to pass no model parameter when it "
        "is not. This server owns that decision; never re-derive it here. (foundry:teammate's "
        "frontmatter carries effort=xhigh + all tools.) "
        "Do NOT send multiple messages with one Agent each \u2014 that serializes what should be parallel.\n"
        "Rules still apply: NEVER run_in_background=true for foundry:teammate. NEVER "
        "subagent_type='Explore' or 'general-purpose' for CAST. F0.5 DECOMPOSE uses background "
        "general-purpose Agents; F2 INSPECT and F4 ASSAY use named agents (foundry:tracer, "
        "foundry:assayer, foundry:research-auditor, foundry:coverage-diff) whose frontmatter "
        "carries model/effort/tools." + _GATE_THEN_PHASE_NOTE
    ),
    "build_castings": (
        "YOUR NEXT ACTION depends on wave state:\n"
        "  - IF no CAST team has been registered this wave yet (first entry to F1): follow the transition_to_cast sequence "
        "(TeamCreate \u2192 Foundry-Team-Up \u2192 Foundry-Spawn-Teammate per casting \u2192 Agent spawn VERBATIM, foreground).\n"
        "  - IF teammates are currently running: WAIT for all to complete, then TeamDelete + Foundry-Team-Down + "
        "Foundry-Phase(phase='cast'). Do NOT call Foundry-Next while waiting \u2014 it will re-emit this action."
    ),
    "transition_to_inspect": (
        "YOUR NEXT CALLS (in order):\n"
        "  (1) Foundry-Gate(phase='inspect')\n"
        "  (2) Foundry-Phase(phase='inspect_start') — crossing GRIND → INSPECT is "
        "what advances the server-side cycle counter. Every stream record, defect "
        "and roll-up entry after this point is stamped with the NEW cycle, so "
        "skipping this call silently files the next cycle's evidence under the "
        "last one and the recurring-class escalation never accumulates."
        + _GATE_THEN_PHASE_NOTE
    ),
    "run_streams": (
        "YOUR NEXT CALLS: spawn every missing INSPECT stream in a SINGLE parallel message. Stream-specific rules:\n"
        "All four streams spawn as BACKGROUND Agents (run_in_background=true) so SIGHT can run "
        "concurrently in the main thread instead of the main thread blocking on tool_results:\n"
        "  - TRACE: Agent(subagent_type='foundry:tracer', run_in_background=true, prompt='Run TRACE wiring verification for the active foundry run.')\n"
        "  - PROVE: Agent(subagent_type='foundry:assayer', run_in_background=true, prompt='Run PROVE (spec-to-code citation verification) for the active foundry run.')\n"
        "  - RESEARCH_AUDIT: Agent(subagent_type='foundry:research-auditor', run_in_background=true, prompt='Run RESEARCH_AUDIT for the active foundry run.')\n"
        "  - COVERAGE_DIFF (MIGRATION only): Agent(subagent_type='foundry:coverage-diff', run_in_background=true, prompt='Run COVERAGE_DIFF for the active foundry run.')\n"
        "  - SIGHT: runs in MAIN THREAD via Playwright \u2014 execute while the four background streams run\n"
        "  - TEST / PROBE: may also run as background Agents\n"
        "When each background stream's completion notification fires: call TaskOutput(task_id) "
        "to retrieve its findings, then call Foundry-Stream(stream, cycle, items_checked, "
        "items_total, findings_count) with the parsed counts. Do NOT poll \u2014 the harness notifies you."
    ),
    "transition_to_grind": (
        "YOUR NEXT CALLS (in order):\n"
        "  (1) Foundry-Tasks\n"
        "  (2) Foundry-Gate(phase='grind')\n"
        "  (3) Foundry-Phase(phase='grind_start')\n"
        "  (4) TeamCreate('grind-{run}-cycle-N')\n"
        "  (5) Foundry-Team-Up(team_name='grind-{run}-cycle-N')\n"
        "  (6) For each casting with open defects: Foundry-Spawn-Teammate(casting_id=N, phase='grind')\n"
        "  (7) Spawn Agent(subagent_type='foundry:teammate', mode='bypassPermissions', "
        "prompt=<the returned `dispatch` field VERBATIM \u2014 it names the prompt FILE and the sha256 the "
        "teammate must read that file to obtain; the `prompt` field is null by default and is NOT what "
        "you pass. Then APPEND (a) the `grind_cycle_context` block from the spawn "
        "response if present \u2014 lists files changed in prior cycles so the teammate reads current state "
        "before acting, then (b) the defect list in a '## Defects to fix this cycle:' block. Order: dispatch \u2192 "
        "cycle_context \u2192 defects. Both appended BELOW the dispatch block, never inside it.>). "
        "Same foreground rule as CAST \u2014 never background-spawn GRIND teammates. "
        "For the model: obey the model clause in the `instructions` Foundry-Spawn-Teammate "
        "returned \u2014 pass the model it names, or no model parameter when it names none. This "
        "server owns that decision; never re-derive it here." + _GATE_THEN_PHASE_NOTE
    ),
    "fix_defects": (
        "YOUR NEXT ACTION depends on GRIND state:\n"
        "  - IF no GRIND team registered yet: follow the transition_to_grind sequence.\n"
        "  - IF teammates are running: WAIT. When all report complete, TeamDelete + Foundry-Team-Down + "
        "Foundry-Phase(phase='inspect_start') + re-run INSPECT."
    ),
    "transition_to_assay": (
        "YOUR NEXT CALLS (in order):\n"
        "  (1) Foundry-Phase(phase='inspect_clean')\n"
        "  (2) Foundry-Gate(phase='assay')\n"
        "  (3) Update state to F4\n"
        "  (4) Spawn 4 parallel Agent(subagent_type='foundry:assayer', "
        "prompt='Assay requirement group N of 4 for the active foundry run. "
        "Spec-before-code; default posture is find the failure.') in a SINGLE message. "
        "(The assayer's frontmatter carries model=opus and effort=max.)"
    ),
    "run_assay": (
        "YOUR NEXT CALL: spawn 4 parallel Agent(subagent_type='foundry:assayer', "
        "prompt='Assay requirement group N of 4 for the active foundry run. "
        "Spec-before-code; default posture is find the failure.') in a SINGLE message. "
        "Each reads the spec FIRST, forms expectations, then reads code. "
        "(The assayer's frontmatter carries model=opus and effort=max.)"
    ),
    "transition_to_done": "YOUR NEXT CALL: Foundry-Phase(phase='done')",
}


def _format_imperative_header(action: str, instructions: str, details: dict, run_name: str = "") -> str:
    """Produce the one-line 'YOUR NEXT CALL' header for the given action.
    Falls back to a generic header if the action is unmapped.

    Substitutes `{run}` in the imperative with the active run slug so team
    names (cast-{run}-wave-N, grind-{run}-cycle-N) are distinguishable across
    concurrent runs. DECOMPOSE no longer uses a team — it spawns background
    Agents (per commands/start.md \u00a7F0.5).
    If no run is active, `{run}` is replaced with `active` as a safe default.
    """
    imperative = _ACTION_IMPERATIVES.get(action)
    if imperative:
        return imperative.replace("{run}", run_name or "active")
    return f"YOUR NEXT CALL: follow the CONTEXT below (action='{action}'). Execute the first tool call mentioned. Do not deliberate."


def _format_status_display(project_root: str) -> str:
    """Generate foundry status display with pixel-art hammer header."""
    fdir = get_run_dir(project_root)
    if not fdir or not fdir.exists():
        return ""

    state = _load_json(fdir / "state.json")
    phase = state.get("phase", "F0")
    phase_times = state.get("phase_times", {})
    started = state.get("started_at", "")
    cycle = _current_cycle(fdir)

    elapsed = ""
    if started:
        try:
            start = datetime.fromisoformat(started)
            now = datetime.now(timezone.utc)
            delta = now - start
            elapsed_secs = int(delta.total_seconds())
            h = elapsed_secs // 3600
            m = (elapsed_secs % 3600) // 60
            s = elapsed_secs % 60
            if h > 0:
                elapsed = f"{h}h {m}m {s}s"
            elif m > 0:
                elapsed = f"{m}m {s}s"
            else:
                elapsed = f"{s}s"
        except ValueError:
            pass

    phases = [
        ("F0", "RESEARCH"), ("F0.5", "DECOMPOSE"), ("F0.9", "VALIDATE"),
        ("F1", "CAST"), ("F2", "INSPECT"),
        ("F3", "GRIND"), ("F4", "ASSAY"), ("F5", "TEMPER"),
        ("F5.5", "NYQUIST"), ("F6", "DONE"),
    ]

    # NFR-005 / CT-016 — HALTED IS IN THE DISPLAY VOCABULARY. D-137.
    #
    # The header read `{phase} {phase_names.get(phase, phase)}`, whose fallback
    # is the token itself — fine for every member of `phases`, where the token
    # and the name differ ("F2 INSPECT"), and wrong for the one phase that has
    # no ladder row. Driven on a halted state, the banner read
    # "F O U N D R Y  HALTED HALTED". CT-016 makes HALTED a named terminal
    # state Foundry-Next reports and NFR-005 requires every new notice to read
    # correctly in the terminal; a stutter is what a fallback produces when a
    # value it never anticipated reaches it.
    #
    # The label is built as ONE string rather than by adding a HALTED row to
    # `phases`: the ladder below enumerates the phases a run PASSES THROUGH and
    # marks the current one, and HALTED is not a step on that path — it is
    # where a run stops instead of continuing along it. It is rendered as its
    # own line under the ladder for the same reason.
    phase_names = dict(phases)
    halted_display = phase == RUN_PHASE_HALTED
    phase_name = phase_names.get(phase, "")
    header_label = f"{phase} {phase_name}".strip() if phase_name else phase
    header_colour = _BRED if halted_display else _BCYAN
    run_name = fdir.name

    lines = [foundry_hammer(f"F O U N D R Y  {header_colour}{header_label}{_RESET}  Cycle: {cycle}  {elapsed}")]

    # Phase list
    for pid, pname in phases:
        timing = phase_times.get(pid, {})
        dur = timing.get("duration", "")

        if pid == phase:
            icon = f"{_BGREEN}\u25b6{_RESET}"
            label = f"{_BWHITE}{pid} {pname}{_RESET}"
            right = f"  {_BGREEN}\u25c0 {elapsed}{_RESET}"
        elif dur or timing.get("started_at"):
            icon = f"{_GREEN}\u2713{_RESET}"
            label = f"{_DIM}{pid} {pname}{_RESET}"
            right = f"  {_DIM}{dur}{_RESET}" if dur else ""
        elif (pid == "F5" and not state.get("temper", False)) or (
            pid == "F5.5" and not state.get("nyquist", False)
        ):
            icon = f"{_DIM}\u2500{_RESET}"
            label = f"{_DIM}{pid} {pname}{_RESET}"
            right = f"  {_DIM}skip{_RESET}"
        else:
            icon = f"{_DIM}\u25cb{_RESET}"
            label = f"{_DIM}{pid} {pname}{_RESET}"
            right = ""

        lines.append(f"  {icon} {label}{right}")

    # D-137 / D-018: the halt DETAIL line (reason, cycle) is display.py's —
    # `_fmt_foundry_next_lines` already draws it, and two derivations of one
    # rendered fact is what D-018 filed. What belongs to THIS renderer is the
    # banner, and the banner is fixed above. No halt line is drawn here.

    # Defects
    defects = _load_json(fdir / "defects.json")
    all_d = defects.get("defects", [])
    open_d = sum(1 for d in all_d if d.get("status") == "open")
    fixed_d = sum(1 for d in all_d if d.get("status") == "fixed")
    regressed = sum(1 for d in all_d if d.get("regression"))

    if all_d:
        defect_line = f"  {_BWHITE}Defects:{_RESET} {_BYELLOW}{open_d} open{_RESET}  {_BGREEN}{fixed_d} fixed{_RESET}"
        if regressed:
            defect_line += f"  {_BRED}{regressed} regressed{_RESET}"
        lines.append(defect_line)

    # Verdicts
    verdicts = _load_json(fdir / "verdicts.json")
    reqs = verdicts.get("requirements", [])
    if reqs:
        verified = sum(1 for r in reqs if r.get("verdict") == "VERIFIED")
        v_bar_len = 15
        v_filled = int((verified / len(reqs)) * v_bar_len) if reqs else 0
        v_bar = f"{_BGREEN}{'\u2588' * v_filled}{_DIM}{'\u2591' * (v_bar_len - v_filled)}{_RESET}"
        lines.append(f"  {_BWHITE}Verdicts:{_RESET} {v_bar} {verified}/{len(reqs)}")

    # Streams
    streams = _check_streams_complete(project_root)
    if phase in ("F2", "F4") or streams.get("required"):
        req_streams = streams.get("required", [])
        missing_s = streams.get("missing", "").split()
        stream_icons = []
        # FR-013: the rendered order comes from the canonical vocabulary, not
        # from a sixth hand-typed copy of the stream names that silently hid
        # any stream someone forgot to add here.
        for s in sorted(STREAM_WIRE_IDS):
            if s in req_streams:
                if s not in missing_s:
                    stream_icons.append(f"[{_GREEN}\u2713{_RESET}]{s}")
                else:
                    stream_icons.append(f"[{_DIM} {_RESET}]{s}")
        if stream_icons:
            lines.append(f"  {_BWHITE}Streams:{_RESET}  {' '.join(stream_icons)}")

    # D-018 — THE INSPECT / SPEND / SERVER / HALTED LINES ARE NOT DRAWN HERE.
    #
    # They used to be, AND `display._fmt_foundry_next_lines` drew them too, and
    # the two derivations had already drifted: the display.py copy named
    # `server_root` and this one did not. Only one of them was ever reachable —
    # this one, because `foundry_next_action` sets `display` unconditionally and
    # `_fmt_foundry_next_action` returned it INSTEAD of calling the other. So
    # the run shipped one live renderer, one dead renderer, and no way for the
    # per-phase and per-cycle spend roll-ups the dead one alone drew (FR-021 /
    # AC-033) to reach the lead at all.
    #
    # The renderer that survives is display.py's, for two reasons that both had
    # to hold: it is the one the casting's key_link names, and it is the only
    # one that can also repair `_fmt_foundry_init` — whose pre-rendered box is
    # built in `foundry.py`, a file this casting may not edit. `foundry_
    # next_action` puts `inspect_mode`, `spend`, `executing_server`,
    # `waiting_on_agents` and `phase` in the result dict; display.py reads them
    # from there and concatenates its lines BELOW this block. Do not re-add a
    # copy here: two renderers of one fact is the defect, not the layout.

    # Teams
    teams = _check_active_teams(project_root)
    if teams["active"]:
        team_str = ", ".join(teams["teams"])
        if len(team_str) > 40:
            team_str = team_str[:37] + "..."
        lines.append(f"  {_BWHITE}Teams:{_RESET}    {_BCYAN}{team_str}{_RESET}")

    lines.append(FOUNDRY_SEP)

    return "\n".join(lines)


def _escalation_notice(fdir: Path, project_root: str) -> str:
    """One sentence naming any escalated class, for the guidance instructions.

    FR-008 / AC-010: the lead has to know a class escalated BEFORE it dispatches
    the GRIND wave, because the packet shape it is about to hand out changed.
    Empty string when nothing is escalated, so the surrounding instructions read
    identically on a normal cycle.
    """
    escalated = _escalated_classes(fdir, project_root)
    if not escalated:
        return ""
    names = ", ".join(sorted(escalated))
    # Each restore instruction is rendered per class and verified to round-trip
    # (D-133), rather than offering a "<class>" placeholder the operator has to
    # fill in with a key the grammar may not read back.
    restores = "; ".join(
        _override_offer(key)
        for key in sorted(escalated)
    )
    return (
        f" ESCALATED: {len(escalated)} defect class(es) have recurred for "
        f"{ESCALATION_CYCLES}+ consecutive cycles ({names}). Foundry-Tasks will "
        "emit ONE structural-fix packet per escalated class instead of "
        "per-instance packets — dispatch that packet as a single task and do not "
        "split it back apart. Every listed defect must still close. To restore "
        f"per-instance packets: {restores}."
    )


def _still_escalated_classes(fdir: Path, project_root: str) -> list[str]:
    """The class keys ST-010 still holds DONE open for (D-129).

    The SAME union `_done_preconditions` refuses on — ledger recurrence
    (`_escalated_classes`) plus the persisted status (`_persisted_escalations`)
    — read through one function so the guidance engine and the gate can never
    name different sets. Overrides are honoured by both halves.
    """
    escalated_open = _escalated_classes(fdir, project_root)
    persisted = _load_json(fdir / ESCALATION_FILENAME).get("classes", {})
    if not isinstance(persisted, dict):
        persisted = {}
    return sorted(
        set(escalated_open) | set(_persisted_escalations(fdir, project_root, persisted))
    )


def _still_escalated_notice(
    fdir: Path, project_root: str, *, inspect_mode: str = ""
) -> str:
    """One sentence naming any class ST-010 will still hold DONE open for.

    Empty string when nothing is escalated, so a clean cycle reads identically.

    D-129 — THE CLEAN PATH LEARNED OF THE ST-010 BLOCK AT THE F6 DOOR.
    -----------------------------------------------------------------
    `_escalation_notice` (the sentence above) is wired into ONE arm: the
    `transition_to_grind` branch, which is reached only when
    `open_count > 0`. It also reads `_escalated_classes`, which opens with
    `if not bucket["open"]: continue` — so a class the server has PERSISTED as
    ESCALATED with every instance closed is invisible to it twice over.

    Driven: three LATENT filings of class FDC at cycles 1-3; the boundary
    closing cycle 3 wrote escalation.json status ESCALATED, escalated_at 3,
    packets 0; all five required streams marked, blocking 0.
    `foundry_next_action` returned action transition_to_assay with "INSPECT
    clean: zero blocking defects, at FULL width ... 3 LATENT defect(s) stay
    open ... they block nothing" and named neither FDC, nor ESCALATED, nor
    ST-010. Following it: inspect_clean ok, Gate assay passed, Gate temper
    passed, Phase temper ok, Gate nyquist passed, Phase nyquist ok — and then
    `Foundry-Gate('done')` refused "1 defect class(es) are still ESCALATED:
    FDC". Each boundary the class still needs is then reached from a
    post-verification phase and re-enters through final_gate FULL, ASSAY,
    TEMPER and NYQUIST again: two extra post-verification loops for a class
    that could have cleared in two INSPECT cycles from F2. NFR-001 targets
    exactly that axis, and US-001's premise is that the exit is MECHANICAL —
    which it is, and the lead could not see the meter running.

    READS THE SAME UNION `_done_preconditions` REFUSES ON — `_escalated_classes`
    (ledger recurrence) ∪ `_persisted_escalations` (the recorded status) — so
    the notice and the refusal cannot name different sets. Overrides are
    honoured by both halves, so a class the operator de-escalated is silent
    here exactly as it is at the gate.

    Carries `_escalation_exit_distances`, so the sentence states not just THAT
    a class blocks but how far each arm is: a lead reading "1 more clean cycle"
    at F2 crosses one boundary, where the same lead reading it at F5.5 pays a
    full post-verification loop for the same crossing.

    D-153 — AND IT NAMES THE CALL THE SERVER ACTUALLY ACCEPTS FROM HERE.
    -------------------------------------------------------------------
    "The cheapest place to make those crossings is HERE, from F2" was true and
    unactionable: the only crossing the sentence named was
    "Foundry-Phase(phase='inspect_start') from F3", which is where the crossing
    lands but not a call this arm's reader can make. Driven at cycle 8 on a run
    at F2 whose recorded width was FULL / final_gate: `inspect_start` is
    REFUSED — "this cycle's recorded width is FULL (rule final_gate), so there
    is nothing to widen" — and `live_clean_cycles` stayed 0. The crossing that
    works from a FULL F2 is `grind_start` and then `inspect_start`: a GRIND
    opened with nothing to fix, which no arm named and which reads as a mistake
    unless the prose says it is the crossing. The sibling `widen_inspect` arm
    names ITS re-open; this one named none.

    So ``inspect_mode`` — the width the transition RECORDED, which this arm's
    caller has already read — selects the spelling, and the two spellings are
    exactly the two the `inspect_start` refusal's own hint offers from F2.
    Reported, never decided (GI-008): the width is read back, not computed.
    """
    still = _still_escalated_classes(fdir, project_root)
    if not still:
        return ""
    if (inspect_mode or "").upper() == "DELTA":
        crossing = (
            "from F2 at DELTA width that is the widening re-open, "
            "Foundry-Phase(phase='inspect_start'), which advances the counter "
            "and closes one"
        )
    else:
        crossing = (
            "from F2 at FULL width Foundry-Phase(phase='inspect_start') is "
            "REFUSED (there is nothing to widen), so the crossing is "
            "Foundry-Phase(phase='grind_start') — a GRIND with nothing to fix "
            "is what a clean cycle IS — and then "
            "Foundry-Phase(phase='inspect_start'), which advances the counter "
            "and closes one"
        )
    return (
        f" ST-010: {len(still)} defect class(es) are still ESCALATED "
        f"({', '.join(still)}) and DONE is refused until every one of them is "
        "CLEARED — a LATENT-only backlog does not by itself clear a class. "
        "Clearing it is a boundary crossing, and the cheapest place to make "
        "those crossings is HERE, from F2: reaching ASSAY, TEMPER and NYQUIST "
        "first means every remaining crossing is paid for twice."
        + _escalation_exit_distances(fdir, project_root, still, crossing=crossing)
    )


def _compute_next_action(project_root: str) -> dict:
    """Internal: compute next action without directive overlay."""
    fdir = get_run_dir(project_root)

    if not fdir or not fdir.exists():
        return {
            "phase": "none",
            "action": "init",
            "instructions": "No active foundry run. Call Foundry-Init to start a new run, or foundry_init(resume='run-name') to resume.",
            "details": {},
        }

    state = _load_json(fdir / "state.json")
    phase = state.get("phase", "F0")

    # ST-008 / CT-016 / AC-037 — A HALTED RUN ISSUES NO DISPATCH.
    #
    # Checked before anything else, including the active-teams branch: a run
    # that hit its cycle cap is over, and the next thing to do is read the
    # report, not start another wave. Emitting the ordinary phase guidance here
    # would send the lead round the loop the cap just stopped.
    if phase == RUN_PHASE_HALTED:
        blocking = _blocking_defects(fdir)
        # D-171 — AND IT DOES NOT ASSERT A REPORT THE HALT MAY NEVER HAVE
        # WRITTEN. D-165, one surface along.
        # ------------------------------------------------------------------
        # This said "The report has been generated at REPORT.md and names every
        # open defect by tier" unconditionally, and set `details.report` to the
        # path unconditionally. Driven: a run at cycle 2 with max_cycles 2, one
        # open LIVE and one open LATENT defect, and a deliberately corrupt
        # verdicts.json. `_halt_if_capped` behaved correctly — ok True, phase
        # HALTED, `halted_report_error` recorded, and its own message said the
        # report could NOT be generated. THIS branch, on that same run, then
        # asserted the opposite and handed the lead a path to a file that does
        # not exist.
        #
        # D-165 reasoned that every later REFUSAL names the call that can still
        # write the report, and Foundry-Next is not a refusal — so it was
        # missed. It is also the ONE surface a lead consults next, and HALTED
        # has no exit by design, so nothing regenerates the report on its own:
        # a lead told to read a document that was never written has no next
        # move at all.
        #
        # Both halves come from `_halted_state` and the file itself, the same
        # two sources `_halted_refusal` reads, so the transition, the refusal
        # and this notice cannot state three different things about one file.
        # The FILE'S PRESENCE is the ground truth and the recorded error only
        # supplies the REASON when it is absent — read the other way, this
        # would still say "not written" about a report the lead had just
        # regenerated with Foundry-Report.
        halted = _halted_state(fdir) or {}
        report_path = fdir / REPORT_MD_FILENAME
        report_present = report_path.exists()
        report_error = halted.get("halted_report_error", "")
        return {
            "phase": RUN_PHASE_HALTED,
            "action": "halted",
            "instructions": (
                f"Run HALTED at cycle {state.get('halted_at_cycle', '?')} — "
                f"{state.get('halted_reason', 'the configured cycle cap was reached')}. "
                "HALTED is NOT DONE: this run stopped with open work. "
                + (
                    f"The report has been generated at {REPORT_MD_FILENAME} and "
                    "names every open defect by tier. Do NOT dispatch another "
                    "wave, do NOT call Foundry-Phase again — read the report "
                    "and hand the remaining work to a new run, or re-run with a "
                    "higher --max-cycles."
                    if report_present
                    else (
                        f"The report was NOT generated — "
                        f"{report_error or 'it is not present at ' + str(report_path)}"
                        ". Do NOT dispatch another wave and do NOT call "
                        "Foundry-Phase again; neither is what is missing. "
                        "Foundry-Report is not a phase transition and still "
                        "runs on a halted run: repair what the error names, "
                        f"call Foundry-Report to write {REPORT_MD_FILENAME}, "
                        "then read it. Until it exists, read defects.json "
                        "directly — the open work is recorded there whatever "
                        "the generator could not render — and hand the "
                        "remaining work to a new run, or re-run with a higher "
                        "--max-cycles."
                    )
                )
            ),
            "details": {
                "halted_at_cycle": state.get("halted_at_cycle"),
                "halted_reason": state.get("halted_reason", ""),
                "max_cycles": state.get("max_cycles", 0),
                "open_live_defects": blocking["live"],
                "open_unknown_tier_defects": blocking["unknown"],
                "open_latent_defects": blocking["latent"],
                # Named as what it IS rather than always as a path, exactly as
                # `_halted_refusal` names it, so a caller cannot read a promise
                # out of the field's presence.
                "report": str(report_path) if report_present else None,
                "report_generated": report_present,
                "report_error": report_error,
            },
        }

    teams = _check_active_teams(project_root)
    if teams["active"]:
        return {
            "phase": phase,
            "action": "cleanup_teams",
            "instructions": (
                f"Active teams detected: {', '.join(teams['teams'])}. "
                "Send 'All work complete, stop working.' to each teammate in ONE parallel SendMessage batch, "
                "then IMMEDIATELY call TeamDelete for each team \u2014 do NOT wait for shutdown_response, "
                "shutdown_ack, idle confirmations, or any teammate reply. Idle / terminated panes ARE the "
                "shutdown signal. TeamDelete cleans lingering tmux panes. Then Foundry-Team-Down for each team name."
            ),
            "details": {"active_teams": teams["teams"]},
        }

    # FR-006 / AC-008 / D-055 — THE ROUTER IS TIER-AWARE, LIKE THE GATES.
    #
    # This counted raw open records and the F2 branch routed on
    # `open_count > 0`, so a LATENT-ONLY backlog was sent back into GRIND
    # forever — the exact non-termination FR-006 exists to end. Every gate had
    # already been made tier-aware and only the router had not, and
    # commands/start.md orders the lead to follow Foundry-Next LITERALLY and not
    # deliberate, so the run could not reach ASSAY while any LATENT instance was
    # open. Driven: a synthetic run at F2 whose only open defect is LATENT with
    # a valid `reproduction_attempted`, streams complete, no active teams ->
    # `_blocking_defects` reports blocking 0 (every tier-aware gate passes) and
    # this returned `transition_to_grind`, contradicting AC-002's "the run
    # reaches NYQUIST" and NFR-003's "LATENT stays open, tracked, and listed in
    # the report".
    #
    # ONE read, shared by every branch below, so the router and the gate cannot
    # answer differently about the same ledger. The raw count is kept beside it
    # for display only — a lead still wants to know the backlog exists.
    blocking = _blocking_defects(fdir)
    open_count = blocking["blocking"]
    latent_backlog = blocking["latent"]

    # --- Agent config per phase (ENFORCED, not suggestions) ---
    # These are the exact parameters the lead MUST use when spawning agents.
    #
    # Every model decision routes through ``agent_model`` so this server is the
    # single source of truth (GI-003). A site passes its own ``baseline`` — the
    # model it emitted before the option existed — and only the steerable
    # subagent types can be moved off it. Sites with no baseline emit no
    # ``model`` key, leaving the agent's frontmatter pin in charge.
    CAST_AGENT_CONFIG = {
        "subagent_type": "foundry:teammate",
        **agent_model("foundry:teammate"),
        "mode": "bypassPermissions",
    }
    DECOMPOSE_AGENT_CONFIG = {
        **agent_model("general-purpose", baseline="opus"),
        "subagent_type": "general-purpose",
        "mode": "bypassPermissions",
        "run_in_background": True,
    }
    INSPECT_TRACE_CONFIG = {
        "subagent_type": "foundry:tracer",
        "run_in_background": True,
        "description": "TRACE: LSP wiring verification",
    }
    INSPECT_PROVE_CONFIG = {
        "subagent_type": "foundry:assayer",
        "run_in_background": True,
        "description": "PROVE: spec-to-code citation verification",
    }
    GRIND_AGENT_CONFIG = {
        "subagent_type": "foundry:teammate",
        **agent_model("foundry:teammate"),
        "mode": "bypassPermissions",
    }
    ASSAY_AGENT_CONFIG = {
        "subagent_type": "foundry:assayer",
        "description": "ASSAY: fresh-eyes spec-before-code verification",
    }

    if phase == "F0":
        manifest = _load_json(fdir / "castings" / "manifest.json")
        casting_count = len(manifest.get("castings", []))
        if casting_count == 0:
            return {
                "phase": "F0",
                "action": "add_castings",
                "instructions": (
                    f"DECOMPOSE: Spawn 1-5 BACKGROUND Agents to write casting files. No team needed.\n"
                    f"1. Identify 2-5 domains from the spec.\n"
                    f"2. Spawn one background Agent per domain in a SINGLE parallel message:\n"
                    f"     model='opus', subagent_type='general-purpose', mode='bypassPermissions',\n"
                    f"     run_in_background=true,\n"
                    f"     prompt='<per commands/start.md \u00a7F0.5: write manifest.json entry +\n"
                    f"              casting-<id>-prompt.md for your domain>'\n"
                    f"3. All files go under {fdir}/castings/ \u2014 NOT castings/ at project root.\n"
                    f"4. You'll be notified as each Agent completes; retrieve via TaskOutput(task_id).\n"
                    f"   After all complete, call Foundry-Validate-Castings."
                ),
                "details": {"foundry_dir": str(fdir), "agent_config": DECOMPOSE_AGENT_CONFIG},
            }
        return {
            "phase": "F0",
            "action": "transition_to_cast",
            "instructions": (
                f"Decomposition complete ({casting_count} castings). "
                "Call Foundry-Gate(phase='cast') to validate, then Foundry-Phase(phase='start_cast'). "
                "Create a CAST team (TeamCreate), register it (Foundry-Team-Up). "
                "Spawn ONE teammate per casting (or per wave of independent castings). "
                "Do NOT overload one teammate with many castings \u2014 distribute evenly."
            ),
            "details": {"casting_count": casting_count, "agent_config": CAST_AGENT_CONFIG},
        }

    elif phase == "F1":
        if not (fdir / CAST_COMPLETE_MARKER).exists():
            return {
                "phase": "F1",
                "action": "build_castings",
                "instructions": (
                    "CAST phase: teammates are building. Wait for all tasks to complete. "
                    "When done: shut down team, TeamDelete, Foundry-Team-Down, "
                    "then Foundry-Phase(phase='cast')."
                ),
                "details": {"agent_config": CAST_AGENT_CONFIG},
            }
        # D-072 / GI-009 — THE F2 ENTRY IS A TOOL CALL, AND THIS ARM NAMES IT.
        #
        # This said "then update state to F2", naming no tool. The ONLY thing
        # that records the F2 entry's inspect mode is
        # `Foundry-Phase(phase='cast')`, so a lead hand-editing state.json to
        # F2 produced exactly GI-009's named violation — "a first INSPECT of a
        # phase with no recorded mode" — and then `_check_streams_complete`
        # fell back to the pre-width roster while the report's cycle table
        # carried a blank. Every sibling arm was updated to name its transition
        # token; this one and the F3 arm above were not.
        return {
            "phase": "F1",
            "action": "transition_to_inspect",
            "instructions": (
                "CAST complete. Call Foundry-Gate(phase='inspect') to validate "
                "preconditions, then Foundry-Phase(phase='cast') — that call is "
                "what enters F2, sweeps the evidence corpus and RECORDS this "
                "INSPECT's width (FULL, rule first_of_phase) and its roster. "
                "Editing state.json to F2 by hand leaves the first INSPECT of "
                "the phase with no recorded mode. Then spawn verification "
                "agents for the roster it names: TRACE, PROVE. "
                "SIGHT runs in MAIN THREAD. TEST/PROBE run as background agents."
            ),
            "details": {
                "agent_configs": {
                    "trace": INSPECT_TRACE_CONFIG,
                    "prove": INSPECT_PROVE_CONFIG,
                    "test": {
                        **agent_model("general-purpose", baseline="opus"),
                        "subagent_type": "general-purpose",
                    },
                },
            },
        }

    elif phase == "F2":
        streams = _check_streams_complete(project_root)
        # D-117 / GI-008 — REPORTED, AND THE REMEDY IS A TRANSITION, NOT A
        # STREAM. Foundry-Next decides no width (GI-009), so when none is
        # recorded the only thing it can honestly say is which crossing records
        # one. Named apart from the run_streams arm below because "spawn the
        # agents for this roster" would be an instruction to run a roster
        # nothing recorded — this arm exists so the lead is never told to.
        if streams.get("unrecorded_width"):
            return {
                "phase": "F2",
                "action": "record_inspect_width",
                "instructions": (
                    f"INSPECT phase: {streams['reason']}. {streams['hint']}"
                ),
                "details": {
                    "unrecorded_width": True,
                    "inspect_mode": "",
                    "inspect_rule": "",
                    "missing_streams": ["inspect_mode"],
                },
            }
        if not streams["complete"]:
            return {
                "phase": "F2",
                "action": "run_streams",
                "instructions": (
                    f"INSPECT phase ({streams.get('inspect_mode') or 'FULL'} width"
                    f"{', rule ' + streams['inspect_rule'] if streams.get('inspect_rule') else ''}): "
                    f"verification streams incomplete. Missing: {streams['missing']}. "
                    f"Required this cycle: {', '.join(streams['required'])}. "
                    "Spawn agents using the agent_configs below (model and type are ENFORCED). "
                    "SIGHT runs in MAIN THREAD (Playwright MCP only works here) \u2014 "
                    "navigate to URL, snapshot every page, exercise all elements, check console. "
                    "After each stream, call Foundry-Stream(stream, cycle, items_checked)."
                ),
                "details": {
                    "missing_streams": streams["missing"].split(),
                    "required": streams["required"],
                    # GI-008 / CT-009: reported from the recorded decision.
                    "inspect_mode": streams.get("inspect_mode", ""),
                    "inspect_rule": streams.get("inspect_rule", ""),
                    "stream_scope": streams.get("stream_scope", {}),
                    "agent_configs": {
                        "trace": INSPECT_TRACE_CONFIG,
                        "prove": INSPECT_PROVE_CONFIG,
                        "test": {
                            **agent_model("general-purpose", baseline="opus"),
                            "subagent_type": "general-purpose",
                        },
                    },
                },
            }

        if open_count > 0:
            return {
                "phase": "F2",
                "action": "transition_to_grind",
                "instructions": (
                    f"INSPECT complete: {open_count} blocking defect(s) found "
                    f"({len(blocking['live'])} LIVE, "
                    f"{len(blocking['unknown'])} untiered)."
                    + (
                        f" {len(latent_backlog)} LATENT defect(s) do not block "
                        "and are carried to the F6 backlog."
                        if latent_backlog else ""
                    )
                    + _escalation_notice(fdir, project_root)
                    + " Call Foundry-Tasks to generate task list, "
                    "then Foundry-Gate(phase='grind'), then "
                    "Foundry-Phase(phase='grind_start') to clear markers and "
                    "enter F3. Create grind team, assign tasks."
                ),
                "details": {
                    "open_defects": open_count,
                    "live_defects": blocking["live"],
                    "unknown_tier_defects": blocking["unknown"],
                    "latent_backlog": latent_backlog,
                    "agent_config": GRIND_AGENT_CONFIG,
                    "escalation": _escalated_classes(fdir, project_root),
                },
            }

        # AC-016 / D-068 / D-072 — A CLEAN DELTA CYCLE WIDENS; IT DOES NOT OPEN
        # ASSAY. Reported, never decided (GI-008): the width was recorded by the
        # transition that opened this INSPECT, and this branch reads it back to
        # name the crossing that actually works. Naming inspect_clean here would
        # send the lead into the refusal the transition now returns.
        f2_mode = _current_inspect_mode(fdir) or {}
        carried = (
            f" {len(latent_backlog)} LATENT defect(s) stay open, tracked and "
            "named in the F6 backlog; they block nothing."
            if latent_backlog else ""
        )
        # D-129: and the ST-010 block a LATENT-only backlog does NOT clear,
        # said HERE — the last arm before the run leaves F2 — rather than at
        # the F6 door after ASSAY, TEMPER and NYQUIST have been spent.
        # D-153: the RECORDED width selects which crossing the notice names,
        # because it is the width that decides whether `inspect_start` from
        # here is the widening re-open or a refusal.
        still_escalated_note = _still_escalated_notice(
            fdir, project_root, inspect_mode=f2_mode.get("mode", "")
        )
        if f2_mode.get("mode") == "DELTA":
            return {
                "phase": "F2",
                "action": "widen_inspect",
                "instructions": (
                    f"INSPECT clean at DELTA width (cycle {f2_mode.get('cycle', '?')}, "
                    f"rule {f2_mode.get('rule', '?')}): zero blocking defects."
                    + carried
                    + still_escalated_note
                    # D-169: the condition stated is the one both ASSAY doors
                    # evaluate — the recorded MODE — not the rule. A FULL
                    # INSPECT recorded with rule verifier_touched opens ASSAY,
                    # and telling the lead otherwise buys a widening cycle
                    # nothing asked for.
                    + " ASSAY is only opened by an INSPECT whose recorded mode "
                    "is FULL, so call Foundry-Phase(phase='inspect_start') "
                    "again from F2. That crossing advances the cycle counter, "
                    "sweeps the WHOLE evidence corpus, records FULL and names "
                    "the full roster — run exactly the roster it names, then "
                    "Foundry-Phase(phase='inspect_clean')."
                ),
                "details": {
                    "open_defects": 0,
                    "latent_backlog": latent_backlog,
                    # D-129: machine-readable beside the sentence, so a reader
                    # never has to parse prose to learn what still blocks DONE.
                    "still_escalated_classes": _still_escalated_classes(
                        fdir, project_root
                    ),
                    "inspect_mode": f2_mode.get("mode", ""),
                    "inspect_rule": f2_mode.get("rule", ""),
                    "agent_configs": {
                        "trace": INSPECT_TRACE_CONFIG,
                        "prove": INSPECT_PROVE_CONFIG,
                        "test": {
                            **agent_model("general-purpose", baseline="opus"),
                            "subagent_type": "general-purpose",
                        },
                    },
                },
            }

        return {
            "phase": "F2",
            "action": "transition_to_assay",
            "instructions": (
                "INSPECT clean: zero blocking defects, at "
                f"{f2_mode.get('mode') or 'FULL'} width "
                f"(rule {f2_mode.get('rule') or 'unrecorded'})."
                + carried
                + still_escalated_note
                + " Call Foundry-Phase(phase='inspect_clean'), then "
                "Foundry-Gate(phase='assay'). "
                "Spawn 4 parallel assayer agents using the config below (subagent_type='foundry:assayer' — frontmatter carries opus + effort=max)."
            ),
            "details": {
                "open_defects": 0,
                "latent_backlog": latent_backlog,
                # D-129, same field on the arm that opens ASSAY.
                "still_escalated_classes": _still_escalated_classes(
                    fdir, project_root
                ),
                "inspect_mode": f2_mode.get("mode", ""),
                "inspect_rule": f2_mode.get("rule", ""),
                "agent_config": ASSAY_AGENT_CONFIG,
            },
        }

    elif phase == "F3":
        if open_count > 0:
            # D-056 / D-072 — EVERY IMPERATIVE NAMES A CALL THE SERVER ACCEPTS.
            #
            # This arm dictated `Foundry-Fix(defect_id, cycle,
            # adjacent_path_statement, adjacent_path_test)` and asserted beside
            # it that "the two declarations are required and the call is refused
            # without them". The shipped schema's required list is
            # ['defect_id', 'cycle', 'authored_by'], so that exact argument set
            # is refused — "unusable argument(s): authored_by — required, and
            # absent" — and a lead following Foundry-Next literally was refused
            # on its FIRST fix of every cycle. The sentence was also wrong for
            # LATENT defects, where AC-012 forbids demanding the adjacent-path
            # pair and the lane closes on a `regression_test` locator alone.
            #
            # And it ended "run full INSPECT again", naming a width this arm
            # cannot know: the NEXT INSPECT's roster is decided by the
            # `inspect_start` transition (GI-009), and this arm is the only
            # state DELTA is reachable from. So it names the recorded width of
            # the cycle just verified and defers the next one to the crossing
            # that decides it.
            f3_mode = _current_inspect_mode(fdir) or {}
            return {
                "phase": "F3",
                "action": "fix_defects",
                "instructions": (
                    f"GRIND phase: {open_count} blocking defect(s) to fix "
                    f"({len(blocking['live'])} LIVE, "
                    f"{len(blocking['unknown'])} untiered). "
                    + (
                        f"{len(latent_backlog)} LATENT defect(s) are open and "
                        "block nothing; fix them if they are cheap, carry them "
                        "otherwise. "
                        if latent_backlog else ""
                    )
                    + "Teammates are fixing. Wait for completion. "
                    "After each fix call Foundry-Fix(defect_id, cycle, "
                    "authored_by, ...): authored_by is 'teammate' (with the "
                    "prompt_hash and casting_id it was dispatched for) or "
                    "'lead' (with fix_commit). On a LIVE or untiered defect add "
                    "adjacent_path_statement and adjacent_path_test; on a "
                    "LATENT defect add regression_test alone — the "
                    "adjacent-path pair is NOT demanded there. "
                    "When all done: shut down team, then "
                    "Foundry-Phase(phase='inspect_start'), which decides and "
                    "records the next INSPECT's width and names the roster to "
                    "run — run exactly that roster. "
                    f"(The cycle just verified ran {f3_mode.get('mode') or 'FULL'} "
                    f"width, rule {f3_mode.get('rule') or 'unrecorded'}.)"
                ),
                "details": {
                    "open_defects": open_count,
                    "live_defects": blocking["live"],
                    "unknown_tier_defects": blocking["unknown"],
                    "latent_backlog": latent_backlog,
                    "inspect_mode": f3_mode.get("mode", ""),
                    "inspect_rule": f3_mode.get("rule", ""),
                    "agent_config": GRIND_AGENT_CONFIG,
                },
            }
        return {
            "phase": "F3",
            "action": "transition_to_inspect",
            "instructions": (
                "GRIND complete: all defects fixed. Shut down grind team, "
                "Foundry-Team-Down, then Foundry-Phase(phase='inspect_start') to "
                "cross back into F2 — that call is what advances the run's cycle "
                "counter, so skipping it leaves every subsequent record stamped "
                "with the previous cycle. That call also sweeps the evidence "
                "corpus and DECIDES the next INSPECT's width \u2014 run exactly the "
                "roster it names. On a FULL cycle that is every stream; on a "
                "DELTA cycle it is a reduced roster, and running more is wasted "
                "rather than safer. No spot checking either way: the width is "
                "the server's call, not yours."
            ),
            "details": {
                "agent_configs": {
                    "trace": INSPECT_TRACE_CONFIG,
                    "prove": INSPECT_PROVE_CONFIG,
                    "test": {
                        **agent_model("general-purpose", baseline="opus"),
                        "subagent_type": "general-purpose",
                    },
                },
            },
        }

    elif phase == "F4":
        verdicts = _load_json(fdir / "verdicts.json")
        non_verified = sum(1 for r in verdicts.get("requirements", []) if r.get("verdict") != "VERIFIED")
        total = len(verdicts.get("requirements", []))

        if non_verified > 0:
            return {
                "phase": "F4",
                "action": "assay_failed_loop_back",
                "instructions": (
                    f"ASSAY found {non_verified}/{total} non-verified requirements. "
                    "Sync findings as defects (Foundry-Sync), "
                    "call Foundry-Phase(phase='grind_start') to clear ALL markers, "
                    "update state to F3 (GRIND). Fix defects, then FULL INSPECT, then ASSAY again. "
                    "NO SPOT CORRECTIONS \u2014 the entire verification stack re-runs."
                ),
                "details": {
                    "non_verified": non_verified, "total": total,
                    "agent_config": GRIND_AGENT_CONFIG,
                },
            }

        # P3 (FR-003 / FR-004 / ST-001): the auto-pass path. ``.prove-complete``
        # stores only aggregate counts, so verdicts.json may be empty (or
        # partial) even after a clean PROVE — which would make the DONE gate's
        # verdict_coverage read 0/N and block the transition it just enabled.
        # On a clean PROVE, synthesize a VERIFIED verdict for every spec
        # requirement ID BEFORE emitting the auto-pass so the two gates agree.
        if _prove_is_clean(fdir, project_root):
            _synthesize_clean_prove_verdicts(
                fdir, project_root, cycle=_current_cycle(fdir)
            )

        temper = state.get("temper", False)
        if temper:
            return {
                "phase": "F4",
                "action": "transition_to_temper",
                "instructions": (
                    "ASSAY passed: all requirements verified. --temper is set. "
                    "Call Foundry-Gate(phase='temper'), update state to F5. "
                    "Run TEMPER micro-domain stress testing."
                ),
                "details": {
                    "agent_config": {
                        **agent_model("general-purpose", baseline="opus"),
                        "subagent_type": "general-purpose",
                    },
                },
            }

        # F5.5 is the second optional phase. It is reached from here when
        # --nyquist was set and --temper was not; the --temper path reaches it
        # from F5 instead, so the two options compose as F4 → F5 → F5.5 → F6.
        if state.get("nyquist", False):
            return _nyquist_transition("F4")

        return {
            "phase": "F4",
            "action": "transition_to_done",
            "instructions": (
                "ASSAY passed: all requirements verified. "
                "Call Foundry-Gate(phase='done'), update state to F6. "
                "Generate report, append lessons, archive."
            ),
            "details": {},
        }

    elif phase == "F5":
        # A --temper --nyquist run reaches F5.5 from here; --temper alone goes
        # straight to F6. Same guard as the F4 path so the two options compose.
        tail = (
            "When clean, call Foundry-Gate(phase='nyquist'), update to F5.5."
            if state.get("nyquist", False)
            else "When clean, call Foundry-Gate(phase='done'), update to F6."
        )
        return {
            "phase": "F5",
            "action": "run_temper",
            "instructions": (
                "TEMPER phase: micro-domain stress testing. "
                "Decompose into domains (min 15), probe each, cross-domain test, "
                "continuous sweep. Defects go through GRIND \u2192 INSPECT \u2192 ASSAY loop. "
                + tail
            ),
            "details": {},
        }

    elif phase == "F5.5":
        return {
            "phase": "F5.5",
            "action": "run_nyquist",
            "instructions": (
                "NYQUIST phase: regression tests for VERIFIED requirements that "
                "lack automated coverage. Batch requirements by 5 and spawn one "
                "foundry:nyquist-auditor agent per batch. Each classifies "
                "COVERED / UNTESTED / UNDERTESTED, generates minimal behavioral "
                "tests, runs them, and commits the passing ones. Any "
                "ESCALATE_IMPL_BUG result goes through the GRIND \u2192 INSPECT \u2192 "
                "ASSAY loop. Never mark an untested requirement as passing. "
                "When done, call Foundry-Gate(phase='done'), then "
                "Foundry-Phase(phase='nyquist_done') to enter F6."
            ),
            "details": {
                "agent_config": {
                    "subagent_type": "foundry:nyquist-auditor",
                    "description": "NYQUIST: regression tests for VERIFIED requirements",
                },
                "batch_size": 5,
            },
        }

    elif phase == "F6":
        return {
            "phase": "F6",
            "action": "done",
            "instructions": "Foundry complete. Generate report, archive state.",
            "details": {},
        }

    return {
        "phase": phase,
        "action": "unknown",
        "instructions": f"Unknown phase: {phase}. Check state.json.",
        "details": {},
    }


# --- Directives (non-blocking human steering) ---


# --------------------------------------------------------------------------- #
# The directives.md marker grammar (D-104).
#
# ONE definition, read by both sides: `_read_directives` splits the file on
# these prefixes, and `foundry_inject_directive` refuses a body that contains
# one. Two hand-kept copies of the same grammar is how the forgery worked in
# the first place \u2014 the writer did not know what the reader would treat as
# structure, so a priority="normal" body carrying a line beginning
# `### [URGENT]` was parsed back out as a SECOND, urgent directive, overriding
# the priority argument. Directive text was trusted end to end; combined with
# D-101 that let any normal-priority prose forge urgency and de-escalate
# classes.
# --------------------------------------------------------------------------- #

_DIRECTIVE_HEADER_URGENT = "### [URGENT]"
_DIRECTIVE_HEADER_NORMAL = "### [DIRECTIVE]"
_DIRECTIVE_HEADERS = (_DIRECTIVE_HEADER_URGENT, _DIRECTIVE_HEADER_NORMAL)  # 2 markers

# The bootstrap text of an empty directives.md. ONE definition: the injector
# wrote it and the clearer wrote it back, as two separate string literals, so
# "is this file empty of directives?" could not be asked without re-typing a
# third copy — and D-129's conservation guard below has to ask exactly that.
_DIRECTIVES_PREAMBLE = (
    "# Foundry Directives\n\n"
    "Human steering inputs — read at every phase transition.\n\n"
)


def _directive_header_count(text: str) -> int:
    """How many priority headers the parser can see in ``text``."""
    return sum(
        1
        for line in text.split("\n")
        if any(line.startswith(h) for h in _DIRECTIVE_HEADERS)
    )


def _unaccounted_directive_text(path: Path, parsed: dict) -> str | None:
    """Content Foundry-Clear would destroy without archiving it, or None.

    D-129's second half, and the reason the guard alone is not enough. The
    encoding rung is closed upstream: an undecodable directives.md is now a
    NAMED refusal from ``_artifact_guard`` at every door. But the harm —
    "reports success, truncates the file, writes no archive record" — is
    reachable without any encoding fault at all. Hand-edit a ``###`` to a
    ``##`` and the parser sees no header, returns no directives, and the
    clearer truncates a file full of live human steering.

    So the destructive write carries its own conservation check: Foundry-Clear
    may only destroy what it could account for.

      * No header the parser recognises — the whole file is preamble. Truncating
        rewrites the preamble verbatim, so it is safe if that is genuinely all
        the file holds, and a refusal otherwise.
      * Headers present — every one of them must have produced a directive the
        archive will carry. A count that does not match means text is sitting
        in the file that the archive would not receive.

    Returns the unaccounted text (for the refusal to quote), or None when the
    file is fully accounted for.

    D-136 — CONSERVE CHARACTERS, NOT HEADERS. This compared the header COUNT to
    the parsed-directive count, which is blind to text the parser drops BEFORE
    the first recognised header: with a second, well-formed directive present
    the two counts reconcile and the check passes. Driven on the ordinary
    live-run case (one urgent + one normal, then the same `### [URGENT]` ->
    `## [URGENT]` hand-edit the shipped test itself exercises): Foundry-Clear
    returned ok with cleared_count=1, the urgent directive was GONE from
    directives.md, and directives-cleared.md recorded only the normal one. That
    is D-129's filed harm word for word -- "reports success, truncates the
    file, writes no archive record" -- surviving the fix meant to end it,
    because the conservation check was bound to the fixture the defect was
    reported on (ONE directive) instead of derived from what the file holds.
    A one-directive fixture cannot distinguish the two rules; the two-directive
    fixture is the regression test.

    The rule now subtracts, rather than counts. Everything the archive WILL
    carry is removed from the file's text once each -- the preamble, every line
    the parser reads as STRUCTURE, and every directive body it actually parsed.
    Whatever is still standing is text no archive record would carry, whatever
    else in the file parsed cleanly.
    """
    if not path.exists():
        return None
    text = _read_text(path)

    remainder = text
    if remainder.startswith(_DIRECTIVES_PREAMBLE):
        remainder = remainder[len(_DIRECTIVES_PREAMBLE):]

    # Structure, not content: a line the parser reads as a priority header is
    # consumed by the parse and is not part of any directive's body.
    remainder = "\n".join(
        line
        for line in remainder.split("\n")
        if not any(line.startswith(h) for h in _DIRECTIVE_HEADERS)
    )

    # Longest first, so a directive that is a SUBSTRING of another cannot
    # consume the other's text and leave a mangled remainder behind.
    bodies = sorted(
        (b for b in (*parsed.get("urgent", []), *parsed.get("normal", [])) if b),
        key=len,
        reverse=True,
    )
    for body in bodies:
        remainder = remainder.replace(body, "", 1)

    return remainder.strip() or None


def _forged_header_lines(directive: str) -> list[str]:
    """Body lines that `_read_directives` would parse as a priority header.

    Both the raw line and its left-stripped form are checked: the parser keys
    on `str.startswith`, so an indented marker is inert TODAY, but a body that
    smuggles one is asking for exactly the reading this refuses, and the cost
    of declining it is a rephrase.
    """
    return [
        line
        for line in directive.split("\n")
        if any(
            line.startswith(h) or line.lstrip().startswith(h)
            for h in _DIRECTIVE_HEADERS
        )
    ]


def foundry_inject_directive(
    directive: str,
    priority: str = "normal",
    project_root: str = ".",
) -> dict:
    """Inject a human directive that the lead reads at every phase transition."""
    fdir = get_run_dir(project_root)
    if not fdir or not fdir.exists():
        return {"error": "No active foundry run"}
    if (corrupt := _artifact_guard(fdir)):
        return corrupt

    # D-104: refuse rather than escape. Escaping would silently alter the text
    # the human wrote, and a directive is a human instruction \u2014 the house
    # pattern for "this input cannot be honoured as given" is a named refusal
    # that quotes the offending value and says what to do instead.
    forged = _forged_header_lines(directive)
    if forged:
        return {
            "error": (
                "Directive body contains a line that would be read back as a "
                "priority header, which would split it into a second directive "
                "and override priority=" + repr(priority) + ": "
                + "; ".join(repr(line) for line in forged[:3])
                + ". Lines beginning "
                + " or ".join(repr(h) for h in _DIRECTIVE_HEADERS)
                + " are structure in directives.md, not content."
            ),
            "hint": (
                "Reword those lines \u2014 drop the leading '### ' or the square "
                "brackets. To file an urgent directive, pass priority='urgent'."
            ),
            "forged_header_lines": forged,
        }

    directives_path = fdir / "directives.md"
    if not directives_path.exists():
        directives_path.write_text(_DIRECTIVES_PREAMBLE, encoding="utf-8")

    with open(directives_path, "a", encoding="utf-8") as f:
        header = _DIRECTIVE_HEADER_URGENT if priority == "urgent" else _DIRECTIVE_HEADER_NORMAL
        f.write(f"\n{header} {_now()}\n\n{directive}\n")

    result = {
        "ok": True,
        "priority": priority,
        "message": "Directive injected \u2014 lead will read it at next phase transition",
    }

    # D-133: report the override decision HERE, at the call that made it. The
    # operator learns immediately whether the marker they just sent was read,
    # matched nothing, or was ignored as quoted \u2014 instead of discovering it by
    # watching what the next Foundry-Tasks emits.
    override = _override_report(fdir, project_root)
    if override["decisions"]:
        result["escalation_override"] = override
        result["message"] += " | escalation-override: " + "; ".join(
            override["decisions"]
        )
    return result


DIRECTIVES_CLEARED_FILENAME = "directives-cleared.md"


def foundry_clear_directives(
    project_root: str = ".",
) -> dict:
    """Clear active directives, preserving a record of what was cleared.

    FR-019. This used to truncate directives.md outright, leaving no record of
    what the human had asked for or whether it was ever honoured \u2014 the run's
    steering history was destroyed by the act of acknowledging it. The cleared
    text is now appended to ``directives-cleared.md`` first, so the audit trail
    survives and a later reader can check a directive against the work.
    """
    fdir = get_run_dir(project_root)
    if not fdir:
        return {"error": "No active foundry run."}
    if (corrupt := _artifact_guard(fdir)):
        return corrupt
    directives_path = fdir / "directives.md"

    active = _read_directives(project_root)
    urgent = active.get("urgent", [])
    normal = active.get("normal", [])
    cleared_count = len(urgent) + len(normal)

    # D-129: refuse rather than destroy. FR-019 makes preserving a record this
    # tool's whole contract, and a truncate that outruns the archive breaks it
    # silently — the operator is told "No active directives to clear" while the
    # directives are being deleted.
    if (unaccounted := _unaccounted_directive_text(directives_path, active)) is not None:
        return {
            "error": (
                f"directives.md holds {len(unaccounted)} characters of text that "
                f"this tool could not parse as directives, so clearing it would "
                f"DESTROY content no archive record would carry. Refused. "
                f"Unparsed text begins: {unaccounted[:160]!r}"
            ),
            "hint": (
                "The file's header grammar is broken — a directive block opens "
                + " or ".join(repr(h) for h in _DIRECTIVE_HEADERS)
                + " at the start of a line. Repair the headers (or move the text "
                "somewhere safe and reset the file) and retry. Nothing was "
                "cleared and nothing was written."
            ),
            "unaccounted_characters": len(unaccounted),
            "cleared_count": 0,
        }

    if cleared_count:
        archive = fdir / DIRECTIVES_CLEARED_FILENAME
        if not archive.exists():
            archive.write_text(
                "# Cleared Directives\n\nEvery directive Foundry-Clear has retired, "
                "with the time it was cleared. Nothing here is deleted.\n",
                encoding="utf-8",
            )
        with open(archive, "a", encoding="utf-8") as f:
            f.write(f"\n## Cleared {_now()}\n\n")
            for text in urgent:
                f.write(f"- **[URGENT]** {text}\n")
            for text in normal:
                f.write(f"- **[DIRECTIVE]** {text}\n")

    if directives_path.exists():
        directives_path.write_text(_DIRECTIVES_PREAMBLE, encoding="utf-8")

    return {
        "ok": True,
        "cleared_count": cleared_count,
        "urgent_cleared": len(urgent),
        "normal_cleared": len(normal),
        "record": str(fdir / DIRECTIVES_CLEARED_FILENAME) if cleared_count else "",
        "message": (
            f"{cleared_count} directive(s) cleared \u2014 recorded in "
            f"{DIRECTIVES_CLEARED_FILENAME}"
            if cleared_count
            else "No active directives to clear"
        ),
    }


def _read_directives(project_root: str) -> dict:
    """Read active directives."""
    fdir = get_run_dir(project_root)
    if not fdir:
        return {"has_directives": False, "urgent": [], "normal": [], "raw_text": ""}
    directives_path = fdir / "directives.md"

    if not directives_path.exists():
        return {"has_directives": False, "urgent": [], "normal": [], "raw_text": ""}

    # D-098: a non-UTF-8 byte in directives.md raised UnicodeDecodeError out of
    # here and therefore out of Foundry-Next, the mandatory handshake.
    text = _read_text(directives_path)

    urgent: list[str] = []
    normal: list[str] = []
    current_priority = None
    current_text: list[str] = []

    for line in text.split("\n"):
        if line.startswith(_DIRECTIVE_HEADER_URGENT):
            if current_priority and current_text:
                target = urgent if current_priority == "urgent" else normal
                target.append("\n".join(current_text).strip())
            current_priority = "urgent"
            current_text = []
        elif line.startswith(_DIRECTIVE_HEADER_NORMAL):
            if current_priority and current_text:
                target = urgent if current_priority == "urgent" else normal
                target.append("\n".join(current_text).strip())
            current_priority = "normal"
            current_text = []
        elif current_priority:
            current_text.append(line)

    if current_priority and current_text:
        target = urgent if current_priority == "urgent" else normal
        target.append("\n".join(current_text).strip())

    has = len(urgent) > 0 or len(normal) > 0
    return {"has_directives": has, "urgent": urgent, "normal": normal, "raw_text": text if has else ""}


# --- Context reload ---


def foundry_get_context(
    project_root: str = ".",
) -> dict:
    """Return all foundry state in one call. Use after compaction or session start."""
    fdir = get_run_dir(project_root)

    if not fdir or not fdir.exists():
        return {"error": "No active foundry run. Call Foundry-Init or foundry_init(resume='run-name').", "initialized": False}
    if (corrupt := _artifact_guard(fdir)):
        return {**corrupt, "initialized": False}

    state = _load_json(fdir / "state.json")
    defects = _load_json(fdir / "defects.json")
    verdicts = _load_json(fdir / "verdicts.json")

    all_defects = defects.get("defects", [])
    open_d = [d for d in all_defects if d.get("status") == "open"]
    fixed_d = [d for d in all_defects if d.get("status") == "fixed"]
    regression_d = [d for d in all_defects if d.get("regression")]

    all_reqs = verdicts.get("requirements", [])
    verified = sum(1 for r in all_reqs if r.get("verdict") == "VERIFIED")

    # Both reads carried NO handler at all, so a non-UTF-8 byte in either
    # markdown artifact raised UnicodeDecodeError straight out of
    # Foundry-Context. `_artifact_guard` names them at the top of this door
    # already; `_read_text` is what makes the reader itself total rather than
    # relying on a guard several statements above it (D-137's shape).
    findings_excerpt = ""
    findings_path = fdir / "forge-findings.md"
    if findings_path.exists():
        text = _read_text(findings_path)
        findings_excerpt = text[:2000] + ("..." if len(text) > 2000 else "")

    lessons_excerpt = ""
    lessons_path = fdir / "lessons.md"
    if lessons_path.exists():
        text = _read_text(lessons_path)
        lessons_excerpt = text[:2000] + ("..." if len(text) > 2000 else "")

    teams = _check_active_teams(project_root)
    streams = _check_streams_complete(project_root)
    # AC-035 / OT-028: Foundry-Context reorients; it does not reset the stall
    # clock. See the marker block at the end of `foundry_next_action`.
    next_act = foundry_next_action(project_root, _arm_stall_clock=False)

    return {
        "initialized": True,
        "state": {
            "phase": state.get("phase", "unknown"),
            "cycle": _current_cycle(fdir),
            "spec_path": state.get("spec_path", ""),
            "temper": state.get("temper", False),
            "nyquist": state.get("nyquist", False),
            "no_ui": state.get("no_ui", False),
            "started_at": state.get("started_at", ""),
            "total_duration": state.get("total_duration", ""),
            "phase_times": state.get("phase_times", {}),
        },
        "defects": {
            "total": len(all_defects),
            "open": len(open_d),
            "fixed": len(fixed_d),
            "regressions": len(regression_d),
            "open_ids": [d["id"] for d in open_d],
        },
        "verdicts": {
            "total": len(all_reqs),
            "verified": verified,
            "non_verified": len(all_reqs) - verified,
        },
        "streams": streams,
        "active_teams": teams,
        "directives": _read_directives(project_root),
        "forge_findings_excerpt": findings_excerpt,
        "lessons_excerpt": lessons_excerpt,
        "next_action": next_act,
    }
