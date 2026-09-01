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
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from foundry_mcp.schemas.vocab import (
    DEFECT_SOURCE_IDS,
    DEFECT_TYPES,
    STREAM_WIRE_IDS,
    canonical_defect_type,
    observation_class,
)
from foundry_mcp.tools.citation import iter_symbol_cites
from foundry_mcp.tools.foundry_state import (
    clear_active_run,
    get_run_dir,
)
from foundry_mcp.tools.display import foundry_hammer, FOUNDRY_SEP

# ANSI colors — shared with display.py
_RESET = "\033[0m"
_BOLD = "\033[1m"
_DIM = "\033[2m"
_RED = "\033[31m"
_GREEN = "\033[32m"
_YELLOW = "\033[33m"
_BLUE = "\033[34m"
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


def _read_document(path: Path) -> tuple[dict, str | None]:
    """Read a JSON object. Returns ``(data, problem)``; never raises.

    ``problem`` is a human-readable string NAMING THE FILE when the artifact
    exists but is not a readable JSON object, else None. An absent file is not
    a problem — a run legitimately has artifacts it has not written yet.
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
    if not path.exists():
        return ""
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""


def _save_json(path: Path, data: dict) -> None:
    """Atomic JSON write — write to a UNIQUE .tmp, then rename.

    The tmp name carries pid and thread id. The old ``path.with_suffix(".tmp")``
    was shared by every concurrent writer of the same artifact, so a peer's
    rename could move this call's half-written payload into place, or delete it
    mid-write (the 98 FileNotFoundError in the D-103 drive).
    """
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
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

    lock_path = path.with_name(path.name + ".lock")
    with _ARTIFACT_LOCK:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with open(lock_path, "a+", encoding="utf-8") as lock_file:
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


# Artifacts whose corruption the guard reports. DERIVED, not a hand-kept list:
# every top-level *.json in the run dir plus the castings manifest. A new
# artifact is covered the moment it is written, which is the property the
# hand-kept marker lists in this module have repeatedly failed to hold.
def _run_artifact_problems(fdir: Path) -> list[str]:
    """Named problems for every unreadable run artifact, in stable order."""
    if not fdir or not fdir.exists():
        return []
    candidates = sorted(p for p in fdir.glob("*.json") if p.is_file())
    manifest = fdir / "castings" / "manifest.json"
    if manifest.is_file():
        candidates.append(manifest)
    return [p for p in (_document_problem(c) for c in candidates) if p]


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
_REQ_ID_RE = re.compile(r"\b(?:US|FR|NFR|AC|VC|IR|TR)-\d+(?:\.\d+)?\b")


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
    """Return the sorted unique requirement IDs (US/FR/NFR/AC/VC/IR/TR-N).

    The id source for P3 verdict synthesis. Uses the SAME path resolution
    and regex as ``_count_spec_requirements`` (which now delegates here) so
    synthesizing one VERIFIED row per id keeps ``verdict_coverage`` in
    lock-step with the DONE gate's ``_count_spec_requirements`` read.
    """
    spec_path = _resolve_spec_path(project_root)
    if spec_path is None:
        return []
    try:
        text = spec_path.read_text(encoding="utf-8")
    except OSError:
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
    marker = fdir / ".prove-complete"
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


# --- Phase gate ---


def _done_preconditions(fdir: Path, project_root: str) -> dict:
    """Evaluate the substantive preconditions for entering F6 DONE.

    Returns ``{"passed": bool, "reason": str, "hint": str, "checklist": [...]}``.

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
    """
    passed = True
    reason = ""
    hint = ""
    checklist: list[dict] = []

    verdicts = _load_json(fdir / "verdicts.json")
    verdict_list = verdicts.get("requirements", [])
    non_verified = sum(1 for r in verdict_list if r.get("verdict") != "VERIFIED")
    defects = _load_json(fdir / "defects.json")
    open_count = sum(1 for d in defects.get("defects", []) if d.get("status") == "open")
    teams_result = _check_active_teams(project_root)
    spec_count = _count_spec_requirements(project_root)

    # FR-020 / AC-025 — the auto-VERIFY hole, closed at the gate.
    #
    # Every other check below is vacuously satisfied by a spec that parses
    # to ZERO requirements: no requirement can be non-VERIFIED, and the
    # verdict_coverage check guarded itself with `spec_count > 0` and so
    # skipped. A run whose spec is unresolvable or carries no tagged
    # requirement IDs therefore sailed through DONE having proved nothing.
    # Stated first so a more specific failure below still claims `reason`.
    if spec_count <= 0:
        passed = False
        reason = (
            "The spec parses to ZERO requirement IDs — nothing has been "
            "verified, so DONE is vacuous."
        )
        hint = (
            "Check that the run's spec resolves (foundry-archive/{run}/spec.md, "
            "else state.json's spec_path) and that it carries tagged "
            "requirement IDs (US-N / FR-N / NFR-N / AC-N / VC-N / IR-N / TR-N)."
        )

    if non_verified > 0:
        passed = False
        reason = f"{non_verified} requirement(s) not VERIFIED — THIN/PARTIAL are defects, not follow-ups"
        hint = "Fix all non-VERIFIED requirements. Every THIN item must be fully implemented."
    if open_count > 0:
        passed = False
        reason = f"{open_count} open defect(s) remain"

    # AC-011 / ST-003: escalation NEVER waives closure. Swapping N
    # per-instance packets for one structural packet changes the shape of
    # the work, not whether every instance must reach fixed. Stated as its
    # own named check so the guarantee is visible in the checklist rather
    # than merely implied by the open-defect count above.
    escalated_open = _escalated_classes(fdir, project_root)
    if escalated_open:
        passed = False
        reason = (
            f"{len(escalated_open)} escalated defect class(es) still have open "
            f"instances: {', '.join(sorted(escalated_open))}"
        )
        hint = (
            "A structural fix must still close every defect of the class. "
            "Escalation is not a waiver."
        )
    checklist.append({
        "check": f"escalated_classes_closed (open classes={len(escalated_open)})",
        "ok": not escalated_open,
        "classes": sorted(escalated_open),
    })

    if teams_result["active"]:
        passed = False
        reason = f"Active teams: {', '.join(teams_result['teams'])}"

    verdict_count = len(verdict_list)
    verdicts_complete = True
    if spec_count > 0 and verdict_count < spec_count:
        passed = False
        skipped = spec_count - verdict_count
        reason = f"Only {verdict_count} verdicts but spec has {spec_count} requirements. {skipped} skipped."
        hint = "ASSAY must write ALL verdicts to verdicts.json — including THIN/PARTIAL, not just VERIFIED."
        verdicts_complete = False

    checklist.append({
        "check": f"spec_requirements_parsed (count={spec_count})",
        "ok": spec_count > 0,
    })
    checklist.append({"check": f"all_verified (non_verified={non_verified})", "ok": non_verified == 0})
    checklist.append({"check": f"zero_defects (open={open_count})", "ok": open_count == 0})
    checklist.append({"check": "no_active_teams", "ok": not teams_result["active"]})
    checklist.append({"check": f"verdict_coverage ({verdict_count}/{spec_count})", "ok": verdicts_complete})

    return {"passed": passed, "reason": reason, "hint": hint, "checklist": checklist}


def foundry_gate(
    phase: str,
    project_root: str = ".",
) -> dict:
    """Check if preconditions are met to enter a phase."""
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

    checklist: list[dict] = []
    passed = True
    reason = ""
    hint = ""

    nac = fdir / ".next-action-called"
    if not nac.exists():
        return {
            "phase": phase,
            "passed": False,
            "reason": "Must call Foundry-Next before any gate check",
            "hint": "Call Foundry-Next first — it shows the status display and tells you what to do next.",
            "checklist": [{"check": "next_action_called", "ok": False}],
        }
    nac.unlink(missing_ok=True)

    if phase == "validate":
        # Gate for F0.9 VALIDATE — castings must exist
        manifest = fdir / "castings" / "manifest.json"
        if not manifest.exists():
            return {"phase": phase, "passed": False, "reason": "No manifest.json", "hint": "Run DECOMPOSE first to create castings"}
        data = _load_json(manifest)
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
            passed = False
            names = ", ".join(f"#{c['id']} ({c['key_files']} files)" for c in oversized)
            reason = f"Oversized castings: {names}. Max 8 key_files per casting."
            hint = "Split large castings into smaller ones (2-5 tasks, 2-8 files each). No teammate should get 1000 lines of work."
            checklist.append({"check": "casting_size", "ok": False, "oversized": oversized})

        file_to_casting: dict[str, list[int]] = {}
        for c in data.get("castings", []):
            cid = c.get("id", 0)
            for f in c.get("key_files", []):
                file_to_casting.setdefault(f, []).append(cid)
        overlaps = {f: cids for f, cids in file_to_casting.items() if len(cids) > 1}
        if overlaps:
            overlap_details = [f"{f}: castings {cids}" for f, cids in overlaps.items()]
            passed = False
            reason = f"File overlap between castings: {'; '.join(overlap_details)}"
            hint = "Two castings editing the same file will cause conflicts. Move shared files to an earlier casting or merge the overlapping castings."
            checklist.append({"check": "no_file_overlap", "ok": False, "overlaps": overlaps})
        else:
            checklist.append({"check": "no_file_overlap", "ok": True})

    elif phase == "inspect":
        if not (fdir / ".cast-complete").exists():
            passed = False
            reason = "CAST not complete"
            hint = "Complete all CAST tasks and call Foundry-Phase(phase='cast')"
            checklist.append({"check": "cast_complete", "ok": False})
        else:
            checklist.append({"check": "cast_complete", "ok": True})

        teams_result = _check_active_teams(project_root)
        if teams_result["active"]:
            passed = False
            parts = []
            if teams_result["teams"]:
                parts.append(f"Team dirs: {', '.join(teams_result['teams'])}")
            if teams_result.get("live_panes"):
                parts.append(f"Live panes: {', '.join(teams_result['live_panes'])}")
            reason = f"Active teammates: {'; '.join(parts)}"
            hint = teams_result.get("hint", "Shut down all teammates, call TeamDelete, then Foundry-Team-Down")
            checklist.append({"check": "no_active_teams", "ok": False,
                            "teams": teams_result["teams"],
                            "live_panes": teams_result.get("live_panes", [])})
        else:
            checklist.append({"check": "no_active_teams", "ok": True})

        sight = _check_sight_required(project_root)
        if sight.get("required") and sight.get("blocked"):
            passed = False
            reason = sight["reason"]
            hint = "Provide --url for SIGHT audit or update manifest.json target_url"
            checklist.append({"check": "sight_url", "ok": False, "reason": sight["reason"]})
        else:
            checklist.append({"check": "sight_url", "ok": True})

    elif phase == "grind":
        defects = _load_json(fdir / "defects.json")
        open_count = sum(1 for d in defects.get("defects", []) if d.get("status") == "open")
        if open_count < 1:
            passed = False
            reason = "No open defects to grind"
            hint = "Nothing to fix — skip to ASSAY"
        checklist.append({"check": f"open_defects={open_count}", "ok": open_count >= 1})

        teams_result = _check_active_teams(project_root)
        if teams_result["active"]:
            passed = False
            parts = []
            if teams_result["teams"]:
                parts.append(f"Team dirs: {', '.join(teams_result['teams'])}")
            if teams_result.get("live_panes"):
                parts.append(f"Live panes: {', '.join(teams_result['live_panes'])}")
            reason = f"Active teammates: {'; '.join(parts)}"
            hint = teams_result.get("hint", "Shut down INSPECT teams first")
        checklist.append({"check": "no_active_teams", "ok": not teams_result["active"],
                         "live_panes": teams_result.get("live_panes", [])})

        if not (fdir / ".tasks-generated").exists():
            passed = False
            reason = "defects-to-tasks has not been run"
            hint = "Call Foundry-Tasks before entering GRIND"
        checklist.append({"check": "tasks_generated", "ok": (fdir / ".tasks-generated").exists()})

    elif phase == "assay":
        defects = _load_json(fdir / "defects.json")
        open_count = sum(1 for d in defects.get("defects", []) if d.get("status") == "open")
        if open_count > 0:
            passed = False
            reason = f"{open_count} open defect(s) remain"
            hint = "Fix all defects in GRIND first"
        checklist.append({"check": f"zero_open_defects (have {open_count})", "ok": open_count == 0})

        streams = _check_streams_complete(project_root)
        if not streams["complete"]:
            passed = False
            reason = f"Verification streams incomplete: {streams.get('missing', '')}"
            hint = "All streams (trace, prove, sight, test) must complete before ASSAY"
        checklist.append({"check": "all_streams_complete", "ok": streams["complete"],
                         "missing": streams.get("missing", "")})

        if not (fdir / ".inspect-clean").exists():
            has_fixed = sum(1 for d in defects.get("defects", []) if d.get("status") == "fixed")
            if has_fixed > 0:
                passed = False
                reason = "GRIND fixed defects but INSPECT has not re-verified"
                hint = "Run full INSPECT cycle after GRIND. Call foundry_mark_inspect_clean when clean."
            checklist.append({"check": "inspect_clean", "ok": False})
        else:
            checklist.append({"check": "inspect_clean", "ok": True})

        teams_result = _check_active_teams(project_root)
        if teams_result["active"]:
            passed = False
            reason = f"Active teams: {', '.join(teams_result['teams'])}"
        checklist.append({"check": "no_active_teams", "ok": not teams_result["active"]})

    elif phase == "temper":
        verdicts = _load_json(fdir / "verdicts.json")
        non_verified = sum(1 for r in verdicts.get("requirements", []) if r.get("verdict") != "VERIFIED")
        if non_verified > 0:
            passed = False
            reason = f"{non_verified} requirement(s) not verified"
        checklist.append({"check": f"all_verified (non_verified={non_verified})", "ok": non_verified == 0})

    elif phase == "nyquist":
        # F5.5 generates regression tests for VERIFIED requirements, so the
        # same precondition as TEMPER applies: there is nothing to lock in
        # until every requirement has passed ASSAY. Additionally the flag must
        # actually be set — entering F5.5 on a run that never asked for it
        # would spawn auditors the invocation did not request.
        verdicts = _load_json(fdir / "verdicts.json")
        non_verified = sum(1 for r in verdicts.get("requirements", []) if r.get("verdict") != "VERIFIED")
        if non_verified > 0:
            passed = False
            reason = f"{non_verified} requirement(s) not verified"
        checklist.append({"check": f"all_verified (non_verified={non_verified})", "ok": non_verified == 0})

        state = _load_json(fdir / "state.json")
        nyquist_on = state.get("nyquist", False)
        if not nyquist_on:
            passed = False
            reason = "F5.5 NYQUIST is opt-in and this run was not started with --nyquist"
            hint = "Re-run with --nyquist, or skip F5.5: call Foundry-Gate(phase='done')."
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
        passed = outcome["passed"]
        reason = outcome["reason"]
        hint = outcome["hint"]
        checklist.extend(outcome["checklist"])

    else:
        return {"phase": phase, "passed": False, "reason": f"Unknown phase: {phase}",
                "hint": ("Valid phases: validate, cast, inspect, grind, assay, "
                         "temper, nyquist, nyquist_done, done")}

    result = {"phase": phase, "passed": passed, "checklist": checklist}
    if not passed:
        result["reason"] = reason
        result["hint"] = hint
    else:
        # P4 (FR-005 / ST-002): a passing gate advances the guidance state.
        # Record which gate passed so the next Foundry-Next emits the
        # transition step instead of re-running this now-satisfied gate.
        # Cleared by _update_phase when the phase actually advances.
        try:
            (fdir / ".gate-passed").write_text(
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
    """
    totals = _rollup_totals(fdir, cycle, stream) or _marker_counts(
        fdir / f".{stream}-complete"
    )
    if totals is None:
        return None
    checked = totals["items_checked"]

    if stream == "prove":
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
    coverage_warning = ""
    if prev_totals is not None and prev_totals["items_checked"] > 0:
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

    marker = fdir / f".{stream}-complete"
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
                (fdir / ".trace-clean-at").write_text(
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
    marker = fdir / ".trace-clean-at"
    if not marker.exists():
        return {"skip": False, "reason": "no prior clean TRACE to compare against"}
    try:
        marker_data = json.loads(marker.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"skip": False, "reason": "unreadable .trace-clean-at marker"}
    clean_sha = marker_data.get("head_sha", "")
    if not clean_sha:
        return {"skip": False, "reason": "no head_sha recorded"}

    manifest = _load_json(fdir / "castings" / "manifest.json")
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
    """
    if not fdir or not fdir.exists():
        return None
    if (fdir / ".trace-complete").exists():
        return None
    state = _load_json(fdir / "state.json")
    if state.get("phase") != "F2":
        return None

    decision = _trace_skip_check(fdir, project_root)
    if not decision.get("skip"):
        return decision

    (fdir / ".trace-complete").write_text(
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

    required = ["trace", "prove", "test"]

    url = ""
    if manifest.exists():
        url = _load_json(manifest).get("target_url", "")

    if _check_sight_required(project_root).get("required"):
        required.append("sight")

    if url:
        required.append("probe")

    missing = [s for s in required if not (fdir / f".{s}-complete").exists()]

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
    (fdir / ".gate-passed").unlink(missing_ok=True)


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
    """Mark a phase transition. Validates preconditions AND updates state.json.phase."""
    fdir = get_run_dir(project_root)
    if not fdir or not fdir.exists():
        return {"error": "No active foundry run"}
    if (corrupt := _artifact_guard(fdir)):
        return corrupt

    nac = fdir / ".next-action-called"
    if not nac.exists():
        return {
            "error": "Must call Foundry-Next before phase transitions",
            "hint": "Call Foundry-Next first \u2014 it shows status and guides you.",
        }
    nac.unlink(missing_ok=True)

    if phase == "start_cast":
        _update_phase(fdir, "F1")
        return {"ok": True, "phase": "F1", "message": "Phase is now F1 (CAST). Create team and build."}

    elif phase == "cast":
        teams = _check_active_teams(project_root)
        if teams["active"]:
            return {"error": f"Cannot mark CAST complete \u2014 active teams: {', '.join(teams['teams'])}",
                    "hint": "Shut down all teammates and TeamDelete before marking CAST complete"}
        (fdir / ".cast-complete").write_text(f"{_now()}\n", encoding="utf-8")
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
                (fdir / ".cast-baseline-sha").write_text(_rev.stdout.strip(), encoding="utf-8")
        except (FileNotFoundError, _sp.TimeoutExpired, OSError):
            pass
        _update_phase(fdir, "F2")
        return {"ok": True, "phase": "F2", "message": "CAST complete \u2192 phase is now F2 (INSPECT)"}

    elif phase == "inspect_clean":
        streams = _check_streams_complete(project_root)
        if not streams["complete"]:
            return {"error": f"Cannot mark INSPECT clean \u2014 streams incomplete: {streams['missing']}",
                    "hint": "Run all required verification streams first"}
        defects = _load_json(fdir / "defects.json")
        open_count = sum(1 for d in defects.get("defects", []) if d.get("status") == "open")
        if open_count > 0:
            return {"error": f"Cannot mark INSPECT clean \u2014 {open_count} open defect(s) remain",
                    "hint": "All defects must be fixed before marking clean"}
        (fdir / ".inspect-clean").write_text(f"{_now()}\n", encoding="utf-8")
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
        # D-103: read-phase, advance-phase and increment are ONE critical
        # section. They used to be three separate read-modify-writes over the
        # same file (the increment re-read state.json AFTER _update_phase had
        # written it), so a concurrent writer landing between them lost either
        # the phase or the counter. _update_phase nests inside this transaction
        # and mutates the same in-flight document — which is what the
        # re-entrancy in _document_transaction exists for.
        with _document_transaction(state_path) as state:
            prev_phase = state.get("phase", "")
            _update_phase(fdir, "F2")
            if prev_phase == "F3":
                # _current_cycle still reads from disk, and that is correct
                # here: the flock guarantees no peer is mid-write and this
                # transaction has not flushed, so disk still holds the
                # pre-increment counter. Keeping the read on the ONE guarded
                # reader is what D-059's derived-membership test requires.
                state["cycle"] = _current_cycle(fdir) + 1
                state["updated_at"] = _now()
        cycle = _current_cycle(fdir)
        return {
            "ok": True,
            "phase": "F2",
            "cycle": cycle,
            "message": (
                f"GRIND complete → phase is now F2 (INSPECT), cycle {cycle}. "
                "Run the FULL stream set again — no spot checking."
            ),
        }

    elif phase == "grind_start":
        # Every recordable stream marker is cleared (derived from the canonical
        # stream vocabulary so new streams cannot go stale across GRIND cycles),
        # not just the required subset — completion state must stay honest.
        stream_markers = [f".{s}-complete" for s in sorted(VALID_STREAMS)]
        for marker in stream_markers + [".inspect-clean", ".tasks-generated"]:
            (fdir / marker).unlink(missing_ok=True)
        _update_phase(fdir, "F3")
        return {"ok": True, "phase": "F3",
                "message": "All markers cleared \u2192 phase is now F3 (GRIND). Full INSPECT must re-run after."}

    elif phase == "assay_fail":
        stream_markers = [f".{s}-complete" for s in sorted(VALID_STREAMS)]
        for marker in stream_markers + [".inspect-clean", ".tasks-generated"]:
            (fdir / marker).unlink(missing_ok=True)
        _update_phase(fdir, "F3")
        return {"ok": True, "phase": "F3",
                "message": "ASSAY failed \u2192 phase is now F3 (GRIND). Fix defects, then full INSPECT, then ASSAY again."}

    elif phase == "temper":
        _update_phase(fdir, "F5")
        return {"ok": True, "phase": "F5", "message": "Phase is now F5 (TEMPER)"}

    elif phase == "nyquist":
        # Enter F5.5. Mirrors the "temper" token: the phase mark is what makes
        # _compute_next_action's F5.5 branch reachable at all, since it
        # dispatches on state["phase"].
        _update_phase(fdir, "F5.5")
        return {"ok": True, "phase": "F5.5", "message": "Phase is now F5.5 (NYQUIST)"}

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
        outcome = _done_preconditions(fdir, project_root)
        if not outcome["passed"]:
            return {
                "error": f"Cannot leave NYQUIST for DONE — {outcome['reason']}",
                "hint": (
                    outcome["hint"]
                    or "Call Foundry-Gate(phase='nyquist_done') for the full checklist."
                ),
                "checklist": outcome["checklist"],
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
        outcome = _done_preconditions(fdir, project_root)
        if not outcome["passed"]:
            return {
                "error": f"Cannot mark the run DONE — {outcome['reason']}",
                "hint": (
                    outcome["hint"]
                    or "Call Foundry-Gate(phase='done') for the full checklist."
                ),
                "checklist": outcome["checklist"],
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

    data = _load_json(manifest)
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
_OVERRIDE_LINE_PREFIX = r"^[\s>]*(?:[-*+]\s*)?"
_OVERRIDE_SCOPED_RE = re.compile(
    rf"{_OVERRIDE_LINE_PREFIX}{ESCALATION_OVERRIDE_TOKEN}\s*[:=]\s*(\S+)\s*$",
    re.IGNORECASE | re.MULTILINE,
)
_OVERRIDE_BARE_RE = re.compile(
    rf"{_OVERRIDE_LINE_PREFIX}{ESCALATION_OVERRIDE_TOKEN}\s*[:=]?\s*$",
    re.IGNORECASE | re.MULTILINE,
)

# The scoped form's value spelled as "every class" rather than a class key.
_OVERRIDE_ALL_VALUES = frozenset({"*", "all", "any", "every"})  # 4 spellings


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


def _escalation_overrides(project_root: str) -> set[str]:
    """Class keys the human has explicitly de-escalated, or {"*"} for all.

    Recognises ONLY the line-anchored marker grammar (D-101). A directive that
    merely mentions the token — including one forbidding its use — returns the
    empty set, so escalation stays on.
    """
    directives = _read_directives(project_root)
    text = "\n".join(directives.get("urgent", []) + directives.get("normal", []))

    scoped: set[str] = set()
    override_all = False
    for m in _OVERRIDE_SCOPED_RE.finditer(text):
        value = m.group(1).strip(" .,;:'\"`")
        if not value:
            continue
        if value.lower() in _OVERRIDE_ALL_VALUES:
            override_all = True
        else:
            scoped.add(value)

    if _OVERRIDE_BARE_RE.search(text):
        override_all = True

    if override_all:
        return {"*"}
    return scoped


def _escalated_classes(fdir: Path, project_root: str) -> dict[str, dict]:
    """Classes that have recurred for ESCALATION_CYCLES consecutive cycles.

    A class qualifies while it still has OPEN defects: once every defect of the
    class closes, the class is cleared (ST-003) and stops producing a
    structural packet. Escalation therefore never waives closure — it changes
    the SHAPE of the work, not whether it must be done (AC-011).
    """
    defects = _load_json(fdir / "defects.json").get("defects", [])
    overrides = _escalation_overrides(project_root)
    recorded = _load_json(fdir / ESCALATION_FILENAME).get("classes", {})

    # D-102: resolve every record's class in ONE pass over the whole ledger, so
    # a cluster split across declared and undeclared records still accumulates
    # as one class. Per-record `_defect_class` cannot see the ledger, and that
    # blindness is what let a mixed cluster escape.
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

    escalated: dict[str, dict] = {}
    for key, bucket in buckets.items():
        if not bucket["open"]:
            continue
        if "*" in overrides or key in overrides:
            continue
        run_len, run_end = _consecutive_run(bucket["cycles"])
        if run_len < ESCALATION_CYCLES:
            continue
        escalated[key] = {
            "class": key,
            "declared": bucket["declared"],
            "cycles": sorted(bucket["cycles"]),
            "consecutive_cycles": run_len,
            "escalated_at_cycle": run_end,
            "defect_ids": [d["id"] for d in bucket["open"]],
            "open_count": len(bucket["open"]),
            "total_count": bucket["total"],
            "files": sorted(bucket["files"]),
            "symbols": sorted(bucket["symbols"]),
            "spec_refs": sorted(bucket["spec_refs"]),
            "sources": sorted(bucket["sources"]),
            "proposal": (recorded.get(key) or {}).get("proposal", ""),
        }
    return escalated


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
    return (
        f"STRUCTURAL FIX REQUIRED — defect class '{info['class']}' ({origin}) has "
        f"recurred for {info['consecutive_cycles']} consecutive cycles "
        f"(cycles seen: {cycles}) and still has {info['open_count']} open "
        f"instance(s) across {spread}. Per-instance fixes have not held. Find the "
        f"single root cause these instances share and fix it there, then confirm "
        f"every listed defect closes as a consequence. Closure is NOT waived: all "
        f"of {', '.join(info['defect_ids'])} must reach fixed. If this class is "
        f"genuinely not systemic, the lead can restore per-instance packets with "
        f"Foundry-Directive('{ESCALATION_OVERRIDE_TOKEN}: {info['class']}')."
    )


def _record_escalation_proposals(fdir: Path, escalated: dict[str, dict]) -> None:
    """Persist each escalated class's proposal so it survives the tool call.

    ST-003 requires the proposal to be RECORDED on the class's single packet;
    keeping it only in the returned task list would lose it the moment the lead
    moved on.
    """
    path = fdir / ESCALATION_FILENAME
    with _document_transaction(path) as data:
        classes = data.setdefault("classes", {})
        if not isinstance(classes, dict):
            classes = data["classes"] = {}
        for key, info in escalated.items():
            entry = classes.setdefault(key, {})
            entry["proposal"] = info["proposal"]
            entry["escalated_at_cycle"] = info["escalated_at_cycle"]
            entry["consecutive_cycles"] = info["consecutive_cycles"]
            entry["defect_ids"] = info["defect_ids"]
            entry["recorded_at"] = _now()
        data["updated_at"] = _now()


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


def _ref_names_a_test_function(ref: str) -> bool:
    """True when the reference singles out a test INSIDE a file.

    The leaf of the qualified name carries no file extension:
    ``tests/test_auth.py::test_sweeper`` and ``auth::sweeper::tests::evicts``
    do, ``tests/test_auth.py`` and ``sweeper.spec.ts`` do not.
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
    if not statement or not _ref_names_a_test_function(ref):
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


def foundry_mark_defect_fixed(
    defect_id: str,
    cycle: int,
    adjacent_path_statement: str = "",
    adjacent_path_test: str = "",
    project_root: str = ".",
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
    """
    from foundry_mcp.tools.foundry import ledger_transaction

    fdir = get_run_dir(project_root)
    if not fdir:
        return {"error": "No active foundry run."}
    if (corrupt := _artifact_guard(fdir)):
        return corrupt
    defects_path = fdir / "defects.json"

    statement = (adjacent_path_statement or "").strip()
    test_ref = (adjacent_path_test or "").strip()
    server_cycle = _current_cycle(fdir)

    # Every branch runs INSIDE the transaction: the not-found and adjacency
    # checks both read the target record, so evaluating them outside would
    # re-open the same read-then-write window this lock exists to close. A
    # refusal mutates nothing, and writing back an unmodified document is a
    # no-op.
    refusal: dict | None = None
    open_count = 0
    with ledger_transaction(defects_path, "defects") as records:
        target = None
        for d in records:
            if isinstance(d, dict) and d.get("id") == defect_id:
                target = d
                break

        missing = []
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
        if statement:
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
        if test_ref:
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

        if target is None:
            refusal = {"error": f"Defect {defect_id} not found"}
        elif missing:
            refusal = {
                "error": (
                    f"Cannot mark {defect_id} fixed — missing required field(s): "
                    f"{', '.join(missing)}. adjacent_path_statement must name who else "
                    "calls this, what else transitions here, or what runs concurrently. "
                    "adjacent_path_test must reference a test that drives at least one "
                    "of those adjacent paths."
                ),
                "missing_fields": missing,
                "hint": (
                    "Write the adjacent-path test first, then re-call Foundry-Fix with "
                    "both declarations. A fix whose blast radius is undeclared is how a "
                    "defect closes and a regression opens in the same cycle."
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
                "hint": (
                    "Name a second path that touches this code, then reference the "
                    "test that drives THAT path — a locator such as "
                    "tests/test_auth.py::test_sweeper_evicts_stale_sessions, not a "
                    "note to yourself."
                ),
            }
        else:
            target["status"] = "fixed"
            target["fixed_in_cycle"] = server_cycle
            target["declared_fixed_cycle"] = cycle
            target["adjacent_path_statement"] = statement
            target["adjacent_path_test"] = test_ref

        open_count = sum(
            1 for d in records if isinstance(d, dict) and d.get("status") == "open"
        )

    if refusal is not None:
        return refusal

    forge_log = fdir / "forge-log.md"
    if forge_log.exists():
        with open(forge_log, "a", encoding="utf-8") as f:
            f.write(f"\n**{defect_id} FIXED** in cycle {server_cycle} ({_now()})\n")
            f.write(f"- **Adjacent paths:** {statement}\n")
            f.write(f"- **Adjacent-path test:** {test_ref}\n\n")

    return {
        "ok": True,
        "defect_id": defect_id,
        "fixed_in_cycle": server_cycle,
        "declared_cycle": cycle,
        "adjacent_path_statement": statement,
        "adjacent_path_test": test_ref,
        "remaining_open": open_count,
    }


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


def _route_to_observations(
    finding: dict, cycle: int, klass: str, project_root: str
) -> dict:
    """Record a refused comment-prose finding in the observations ledger.

    ``observations.json`` and its writer are owned by tools/foundry.py \u2014 there
    is one ledger writer, not one per filing path \u2014 and observations are NEVER
    mixed into defects.json. The writer re-runs the never-demote denylist
    itself and can refuse; the caller must honour that refusal by keeping the
    finding a defect (AC-002 is a never-weaken guarantee, so the fail-safe
    direction is always "stays a defect").
    """
    from foundry_mcp.tools.foundry import foundry_add_observation

    return foundry_add_observation(
        cycle=cycle,
        source=finding.get("source", ""),
        description=finding.get("description", ""),
        classification=klass,
        target_kind=(finding.get("target_kind") or "comment"),
        spec_ref=finding.get("spec_ref", ""),
        symbol=finding.get("symbol", ""),
        file_path=finding.get("file", ""),
        project_root=project_root,
    )


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
        asserts_code_behaviour,
        ledger_transaction,
        record_denylist_tripwire,
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

        normalized.append({"source": source.strip() if isinstance(source, str) else source,
                           "type": canonical})

    if refusals:
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
    observations = 0
    regressions: list[str] = []
    observed: list[dict] = []
    tripwires: list[dict] = []

    # One exclusive critical section over defects.json for the whole batch.
    # AC-025's uniqueness guarantee comes from THIS lock: allocate_record_id is
    # pure, so minting inside the transaction is what stops two concurrent
    # filers reading the same snapshot and claiming the same id.
    with ledger_transaction(defects_path, "defects") as records:
        fixed = [d for d in records if d.get("status") == "fixed"]

        for finding, norm in zip(findings, normalized):
            symbol = finding.get("symbol", "")
            desc = finding.get("description", "")

            match_id = None
            for fd in fixed:
                if _is_regression_of(finding, norm, fd):
                    match_id = fd["id"]
                    break

            if match_id:
                for d in records:
                    if d["id"] == match_id:
                        d["status"] = "open"
                        d["regression"] = True
                        d["reopened_in_cycle"] = server_cycle
                        d["fixed_in_cycle"] = None
                        break
                reopened += 1
                regressions.append(match_id)
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
                elif asserts_code_behaviour(finding):
                    # D-094 — the promote-direction fail-safe, wired at the
                    # one point `_observation_refusal` applies it on the
                    # Foundry-Defect path: AFTER the denylist (so a denylisted
                    # finding still trips the audit signal above) and BEFORE
                    # the observation class (so a behavioural claim is never
                    # demoted on the strength of its prose).
                    #
                    # a5d715a added this guard and wired it into
                    # `_observation_refusal`, which only `foundry_add_defect`
                    # calls, while its docstring named this branch as the
                    # second consumer it was exported for. That wiring was
                    # never made, so the two filing paths disagreed about what
                    # a defect is: `foundry_add_defect` filed
                    # NO_SECURITY_VOCABULARY as D-001, and the same finding
                    # through Foundry-Sync was silently demoted to an
                    # observation — with no tripwire either, since no denylist
                    # entry matches it. A stream that files through the wrong
                    # door lost a real defect and was told the call succeeded.
                    #
                    # Imported, never re-derived: a fail-safe with two copies
                    # is a fail-safe that drifts, and the disagreement above is
                    # what that costs.
                    #
                    # There is nothing to DO here — falling out of the branch
                    # is the decision. The finding drops through to the defect
                    # append below, which is exactly what the promote path's
                    # `return None` means.
                    pass
                else:
                    klass = observation_class(finding)
                    if klass is not None:
                        outcome = _route_to_observations(
                            finding, server_cycle, klass, project_root
                        )
                        if not outcome.get("error"):
                            observations += 1
                            observed.append(
                                {"classification": klass, "description": desc[:120]}
                            )
                            continue
                        tripwires.append({
                            "description": desc[:120],
                            "attempted_class": klass,
                            "refusal": outcome.get("error", ""),
                            "tripwire": outcome.get("tripwire", ""),
                        })

            defect = {
                "id": _mint_defect_id(records),
                "cycle": server_cycle,
                # D-119: the caller's asserted cycle is persisted beside the
                # server's, never instead of it. The two filing doors used to
                # disagree about WHICH cycle a record belonged to whenever the
                # counter was malformed — one fell back to the caller's value,
                # this one resolved to 0 — so the same findings filed through
                # different doors produced different cycle runs, and a class
                # that recurred three straight cycles escaped escalation while
                # the AC-011 DONE guard passed. Both doors now resolve to 0 and
                # both record what the caller claimed, so the divergence is
                # visible to migrate/escalation tooling instead of silent.
                "declared_cycle": cycle,
                "source": norm["source"],
                "type": norm["type"],
                "description": desc,
                "spec_ref": finding.get("spec_ref", ""),
                "symbol": symbol,
                "file": finding.get("file", ""),
                "status": "open",
                "fixed_in_cycle": None,
                "created_at": _now(),
            }
            # FR-007: the optional stream-declared class travels with the
            # record; escalation keys on it when present.
            declared_class = finding.get(DEFECT_CLASS_FIELD)
            if isinstance(declared_class, str) and declared_class.strip():
                defect[DEFECT_CLASS_FIELD] = declared_class.strip()
            records.append(defect)
            added += 1

        total_open = sum(1 for d in records if d.get("status") == "open")

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
        "observations": observations,
        "observed": observed,
        "regressions": regressions,
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

    if not open_defects:
        return {"ok": True, "tasks": [], "count": 0, "escalated_classes": []}

    escalated = _escalated_classes(fdir, project_root)
    for key, info in escalated.items():
        info["proposal"] = info.get("proposal") or _structural_proposal(info)
    if escalated:
        _record_escalation_proposals(fdir, escalated)

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

    (fdir / ".tasks-generated").write_text(f"{_now()} count={len(tasks)}\n", encoding="utf-8")

    return {
        "ok": True,
        "tasks": tasks,
        "count": len(tasks),
        "escalated_classes": sorted(escalated),
        "structural_tasks": sum(1 for t in tasks if t["structural"]),
    }


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
    validate_passed_marker = fdir / ".validate-passed"

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
            try:
                entry["validate_passed_at"] = validate_passed_marker.read_text(encoding="utf-8").strip() or now
            except OSError:
                entry["validate_passed_at"] = now
            changed = True

    if changed:
        state["phase_times"] = phase_times


def foundry_next_action(
    project_root: str = ".",
) -> dict:
    """Determine what the lead should do next based on current foundry state."""
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
            gp_marker = fdir_stamp / ".gate-passed"
            if gp_marker.exists():
                try:
                    gp_data = json.loads(gp_marker.read_text(encoding="utf-8"))
                except (json.JSONDecodeError, OSError):
                    gp_data = {}
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
        marker = fdir_stall / ".last-next-at"
        if marker.exists():
            try:
                prev_iso = marker.read_text(encoding="utf-8").strip()
                prev = datetime.fromisoformat(prev_iso)
                delta = (datetime.now(timezone.utc) - prev).total_seconds()
                if delta >= 180:  # 3 minutes
                    minutes = int(delta // 60)
                    seconds = int(delta % 60)
                    stall_warning = (
                        f"\u26a0\ufe0f STALL DETECTED: {minutes}m {seconds}s since your last Foundry-Next call. "
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

    critical_rules = (
        "\n\nCRITICAL RULES:"
        "\n- NEVER ask 'Want me to proceed?' or 'Should I continue?' \u2014 just do it."
        "\n- NEVER stop between phases. Call Foundry-Next after each step and follow it."
        "\n- NEVER deliberate for more than 30 seconds between tool calls. If you catch yourself thinking, call Foundry-Next and execute whatever it says."
        "\n- NEVER narrate progress as 'Checkpoint \u2014 X complete', 'Checkpoint reached', 'Milestone \u2014 X', or similar. Foundry has NO checkpoints. You are not a checkpointing orchestrator. Execute the next tool call silently and keep moving."
        "\n- NEVER skip SIGHT because 'no URL.' If frontend files exist, you need a URL. Gate will block."
        "\n- NEVER spawn foundry:teammate agents (CAST or GRIND) with run_in_background=true. They are foreground, TeamCreate-managed, and must run through Foundry-Cast-Wave or Foundry-Spawn-Teammate + verbatim Agent. Background-spawning bypasses the router architecture and breaks spec fidelity."
        "\n- NEVER modify, paraphrase, or augment a prompt returned by Foundry-Spawn-Teammate. Pass it to Agent VERBATIM. GRIND is the only exception: append (a) the `grind_cycle_context` block if returned (prior-cycle file changes) and (b) the '## Defects to fix this cycle:' block BELOW the prompt, in that order. Never inside the prompt."
        "\n- If the user typed a message, treat it as a directive. Absorb and keep going."
        "\n- Zero approval gates. The foundry runs until F6 DONE or an error stops it."
        "\n- NEVER wait for teammate 'shutdown_response', 'shutdown_ack', idle-confirmation, or any reply after "
        "issuing shutdown. The ONLY shutdown signals foundry recognizes are (a) TeamDelete returning ok and "
        "(b) Foundry-Team-Down succeeding. Narrating 'awaiting shutdown approvals' is a stall \u2014 call TeamDelete "
        "immediately. Idle / terminated panes ARE the signal; TeamDelete cleans them."
    )

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

    # Context budget tracking
    fdir_cb = get_run_dir(project_root)
    if fdir_cb and fdir_cb.exists():
        cycle = _current_cycle(fdir_cb)
        # Estimate context usage based on cycle count
        if cycle >= 3:
            usage = "critical"
        elif cycle >= 2:
            usage = "high"
        elif cycle >= 1:
            usage = "moderate"
        else:
            usage = "low"
        result["context_budget"] = {
            "cycles_completed": cycle,
            "estimated_usage": usage,
            "recommendation": "Consider /clear and /foundry:resume if quality is degrading" if usage in ("high", "critical") else "Context budget healthy",
        }

    result["display"] = _format_status_display(project_root)

    fdir = get_run_dir(project_root)
    if fdir and fdir.exists():
        now_stamp = f"{_now()}\n"
        # Ordering token: armed here, consumed (unlinked) by foundry_gate /
        # foundry_mark_phase_complete to prove Foundry-Next preceded a gate
        # or phase transition.
        (fdir / ".next-action-called").write_text(now_stamp, encoding="utf-8")
        # Stall timestamp: written on EVERY Foundry-Next, read on the NEXT
        # Foundry-Next to measure the gap. Never unlinked by gate/phase, so
        # the watchdog is decoupled from ordering-token consumption (FR-005 /
        # FR-008). Read-only intervening calls do not touch it.
        (fdir / ".last-next-at").write_text(now_stamp, encoding="utf-8")

    return result


# Action → imperative-header map. Each action returned by
# _compute_next_action maps to a "YOUR NEXT CALL(S)" directive that the lead
# can execute without re-reading paragraph instructions. Multi-step actions
# MUST enumerate every call — compressing a multi-step sequence into a
# single-line imperative causes the lead to follow the first tool call
# literally and improvise the rest by guessing.
_ACTION_IMPERATIVES = {
    "init": "YOUR NEXT CALL: Foundry-Init (start a new run)",
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
        "  (5) Foundry-Cast-Wave(wave=1, phase='cast') \u2014 returns ALL prompts for wave 1 in ONE call.\n"
        "  (6) In a SINGLE message (parallel tool use), spawn one Agent per returned casting: "
        "subagent_type='foundry:teammate', mode='bypassPermissions', "
        "prompt=<that casting's prompt text VERBATIM \u2014 no edits>. "
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
        "carries model/effort/tools."
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
        "prompt=<returned prompt VERBATIM, then APPEND (a) the `grind_cycle_context` block from the spawn "
        "response if present \u2014 lists files changed in prior cycles so the teammate reads current state "
        "before acting, then (b) the defect list in a '## Defects to fix this cycle:' block. Order: prompt \u2192 "
        "cycle_context \u2192 defects. Both appended BELOW the prompt, never inside it.>). "
        "Same foreground rule as CAST \u2014 never background-spawn GRIND teammates. "
        "For the model: obey the model clause in the `instructions` Foundry-Spawn-Teammate "
        "returned \u2014 pass the model it names, or no model parameter when it names none. This "
        "server owns that decision; never re-derive it here."
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

    phase_names = dict(phases)
    phase_name = phase_names.get(phase, phase)
    run_name = fdir.name

    lines = [foundry_hammer(f"F O U N D R Y  {_BCYAN}{phase} {phase_name}{_RESET}  Cycle: {cycle}  {elapsed}")]

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
    return (
        f" ESCALATED: {len(escalated)} defect class(es) have recurred for "
        f"{ESCALATION_CYCLES}+ consecutive cycles ({names}). Foundry-Tasks will "
        "emit ONE structural-fix packet per escalated class instead of "
        "per-instance packets — dispatch that packet as a single task and do not "
        "split it back apart. Every listed defect must still close. To restore "
        f"per-instance packets, inject Foundry-Directive('{ESCALATION_OVERRIDE_TOKEN}: "
        "<class>')."
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

    defects = _load_json(fdir / "defects.json")
    open_count = sum(1 for d in defects.get("defects", []) if d.get("status") == "open")

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
        if not (fdir / ".cast-complete").exists():
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
        return {
            "phase": "F1",
            "action": "transition_to_inspect",
            "instructions": (
                "CAST complete. Call Foundry-Gate(phase='inspect') to validate preconditions, "
                "then update state to F2. Spawn verification agents for TRACE, PROVE. "
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
        if not streams["complete"]:
            return {
                "phase": "F2",
                "action": "run_streams",
                "instructions": (
                    f"INSPECT phase: verification streams incomplete. Missing: {streams['missing']}. "
                    "Spawn agents using the agent_configs below (model and type are ENFORCED). "
                    "SIGHT runs in MAIN THREAD (Playwright MCP only works here) \u2014 "
                    "navigate to URL, snapshot every page, exercise all elements, check console. "
                    "After each stream, call Foundry-Stream(stream, cycle, items_checked)."
                ),
                "details": {
                    "missing_streams": streams["missing"].split(),
                    "required": streams["required"],
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
                    f"INSPECT complete: {open_count} open defect(s) found."
                    + _escalation_notice(fdir, project_root)
                    + " Call Foundry-Tasks to generate task list, "
                    "then Foundry-Gate(phase='grind'), then update state to F3. "
                    "Call Foundry-Phase(phase='grind_start') to clear markers. "
                    "Create grind team, assign tasks."
                ),
                "details": {
                    "open_defects": open_count,
                    "agent_config": GRIND_AGENT_CONFIG,
                    "escalation": _escalated_classes(fdir, project_root),
                },
            }

        return {
            "phase": "F2",
            "action": "transition_to_assay",
            "instructions": (
                "INSPECT clean: zero defects. Call Foundry-Phase(phase='inspect_clean'), "
                "then Foundry-Gate(phase='assay'), then update state to F4. "
                "Spawn 4 parallel assayer agents using the config below (subagent_type='foundry:assayer' — frontmatter carries opus + effort=max)."
            ),
            "details": {"open_defects": 0, "agent_config": ASSAY_AGENT_CONFIG},
        }

    elif phase == "F3":
        if open_count > 0:
            return {
                "phase": "F3",
                "action": "fix_defects",
                "instructions": (
                    f"GRIND phase: {open_count} defect(s) to fix. "
                    "Teammates are fixing. Wait for completion. "
                    "After each fix, call Foundry-Fix(defect_id, cycle, "
                    "adjacent_path_statement, adjacent_path_test) — the two "
                    "declarations are required and the call is refused without them. "
                    "When all done: shut down team, Foundry-Phase(phase='inspect_start'), "
                    "run full INSPECT again."
                ),
                "details": {"open_defects": open_count, "agent_config": GRIND_AGENT_CONFIG},
            }
        return {
            "phase": "F3",
            "action": "transition_to_inspect",
            "instructions": (
                "GRIND complete: all defects fixed. Shut down grind team, "
                "Foundry-Team-Down, then Foundry-Phase(phase='inspect_start') to "
                "cross back into F2 — that call is what advances the run's cycle "
                "counter, so skipping it leaves every subsequent record stamped "
                "with the previous cycle. Run FULL INSPECT again (all streams). "
                "No spot checking."
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
        directives_path.write_text(
            "# Foundry Directives\n\nHuman steering inputs \u2014 read at every phase transition.\n\n",
            encoding="utf-8",
        )

    with open(directives_path, "a", encoding="utf-8") as f:
        header = _DIRECTIVE_HEADER_URGENT if priority == "urgent" else _DIRECTIVE_HEADER_NORMAL
        f.write(f"\n{header} {_now()}\n\n{directive}\n")

    return {"ok": True, "priority": priority, "message": "Directive injected \u2014 lead will read it at next phase transition"}


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
        directives_path.write_text(
            "# Foundry Directives\n\nHuman steering inputs \u2014 read at every phase transition.\n\n",
            encoding="utf-8",
        )

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

    findings_excerpt = ""
    findings_path = fdir / "forge-findings.md"
    if findings_path.exists():
        text = findings_path.read_text(encoding="utf-8")
        findings_excerpt = text[:2000] + ("..." if len(text) > 2000 else "")

    lessons_excerpt = ""
    lessons_path = fdir / "lessons.md"
    if lessons_path.exists():
        text = lessons_path.read_text(encoding="utf-8")
        lessons_excerpt = text[:2000] + ("..." if len(text) > 2000 else "")

    teams = _check_active_teams(project_root)
    streams = _check_streams_complete(project_root)
    next_act = foundry_next_action(project_root)

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
