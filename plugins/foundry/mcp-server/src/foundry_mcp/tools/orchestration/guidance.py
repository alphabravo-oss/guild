"""Foundry-Next and Foundry-Context: what the lead does next.

Survey blocks D, EE and GG. Every response names the terminal state the run
is heading for, the backlog by tier, and the cycles left before the cap.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from datetime import (
    datetime,
    timezone,
)
from foundry_mcp.schemas.vocab import (
    BLOCKING_TIERS,
    DEFECT_TIERS,
    INSPECT_MODES,
    PHASE_LADDER,
    PHASE_NAMES,
    REPORT_MD_FILENAME,
    RUN_PHASE_HALTED,
    STREAM_WIRE_IDS,
    TIER_UNKNOWN,
    defect_tier,
    halt_reason,
)
from foundry_mcp.tools.artifacts import (
    CAST_COMPLETE_MARKER,
    GATE_PASSED_MARKER,
    LAST_NEXT_AT_MARKER,
    NEXT_ACTION_CALLED_MARKER,
    VALIDATE_PASSED_MARKER,
    _artifact_guard,
    _document_transaction,
    _load_json,
    _read_text,
    _spec_requirement_ids,
    _stream_marker,
)
# fallout research/holmes-orchestrator.md#coh-8 (D-014) / GI-024 — THE PALETTE
# AND THE PHASE VOCABULARY ARE READ, NOT DECLARED.
#
# `_format_status_display` renders the lead's status banner, and a renderer that
# declares its own colours and its own phase labels is a second display module
# wearing a guidance module's name. Casting 10 owns both declarations and has
# published them: `display` names the eight codes this banner uses under their
# public spellings, and `schemas.vocab.PHASE_LADDER` is the ordered run-phase
# ladder with `PHASE_NAMES` derived from it. Both are imported here and neither
# is re-typed, so a colour or a phase label changes in one place.
from foundry_mcp.tools.display import (
    BCYAN,
    BGREEN,
    BRED,
    BWHITE,
    BYELLOW,
    DIM,
    FOUNDRY_SEP,
    GREEN,
    RESET,
    foundry_hammer,
)
from foundry_mcp.tools.foundry_state import (
    current_cycle,
    current_inspect_mode,
    finalize_open_phase_entry,
    halted_state,
    open_defect_ids_by_tier,
    open_defects_by_tier,
    persisted_max_cycles,
    get_run_dir,
    now_iso,
    read_document,
)
from pathlib import Path
from foundry_mcp.tools.orchestration.escalation import (
    ESCALATION_CYCLES,
    ESCALATION_FILENAME,
    _escalated_classes,
    _escalation_exit_distances,
    _override_offer,
    _override_report,
    _persisted_escalations,
)
from foundry_mcp.tools.orchestration.streams import (
    _check_streams_complete,
    _prove_is_clean,
)
from foundry_mcp.tools.orchestration.teams import (
    _check_active_teams,
    agent_model,
)
from foundry_mcp.tools.orchestration.spend import _spend_summary

from foundry_mcp.tools.orchestration.directives import _read_directives




# --- P4 (FR-005 / ST-002): passing-gate → guidance-state advance ---
#
# Maps each _compute_next_action "transition_*" action to the Foundry-Gate
# phase whose passing should advance the guidance state. When that gate has
# passed (recorded via the ``.gate-passed`` marker), the next Foundry-Next
# tells the lead the gate is satisfied and to proceed to the transition step
# instead of re-running the now-satisfied gate.
#
# fallout D-058 / AC-059 — A VALUE IS EITHER ONE GATE OR ONE GATE PER PHASE,
# AND THE SECOND SHAPE EXISTS BECAUSE ONE ACTION CAN SERVE TWO CROSSINGS.
#
# `transition_to_inspect` is the only action `_compute_next_action` emits from
# two phases, and the two are different crossings: from F1 the lead gates
# `inspect` and calls `Foundry-Phase(phase='cast')` (the F1 -> F2 entry, which
# records the first INSPECT's width), and from F3 it gates `inspect_start` and
# calls `Foundry-Phase(phase='inspect_start')` (the GRIND -> INSPECT re-open,
# which advances the cycle counter). A single-valued map cannot hold both, and
# holding only `inspect` made the pair incoherent at BOTH sites: driven from F3,
# `Foundry-Gate(phase='inspect')` refuses ("Cannot enter F2 from phase F3 ...
# accepted from F1 and from nowhere else") while `inspect_start` passes; driven
# from F1, the gate passes and the imperative's `inspect_start` call is refused.
#
# Modelled per PHASE, which is the mirror of `GATE_TO_TRANSITION`'s tuple: that
# table holds one gate mapping to two transitions (`grind` guards `grind_start`
# and `assay_fail`), and this one holds one action resolving to two gates. Both
# tables exist because the gate/transition relation is not one-to-one in either
# direction, and pretending otherwise is what each of these defects was.
#: fallout D-058 — THE CROSSING AN ACTION ASKS FOR, ONE ROW PER EMITTING PHASE.
#:
#: `{action: {phase: {"gate", "token"}}}`. Both halves of the pair live on one
#: row because both halves of the imperative are substituted from it: the gate
#: the lead is told to run and the transition token it is told to call after it
#: are two fields of one fact, and a row is the only place they can be kept
#: together. Two tables would be two things to update, which is how the pair
#: came apart in the first place.
#:
#: Only `transition_to_inspect` needs it, because it is the only action
#: `_compute_next_action` emits from more than one phase. The other six spell
#: their token literally in their own string and are pinned by the same
#: invariant test that walks these rows.
#:
#: `token` is not derived from `GATE_TO_TRANSITION` here and cannot be: that
#: table lives in `gates.py`, which is verifier-set, and this module is
#: lifecycle — GI-033 forbids the import outright. The agreement is asserted in
#: `tests/orchestration/test_gates.py`, which may import both, exactly as
#: `_ACTION_TO_GATE`'s membership in the table has always been.
_ACTION_CROSSINGS: dict[str, dict[str, dict[str, str]]] = {
    "transition_to_inspect": {
        # F1 -> F2: the phase ENTRY. `inspect` guards the `cast` transition,
        # which records the first INSPECT of the phase at FULL width.
        "F1": {"gate": "inspect", "token": "cast"},
        # F3 -> F2: the GRIND re-open, and the call that advances the cycle
        # counter. `inspect_start` guards itself.
        "F3": {"gate": "inspect_start", "token": "inspect_start"},
    },
}


_ACTION_TO_GATE: dict[str, str | dict[str, str]] = {
    # fallout AC-059 — `validate`, not `cast`. The gate this action asks for is
    # the one guarding the `start_cast` transition it then tells the lead to
    # call, and `GATE_TO_TRANSITION` assigns `start_cast` to the `validate`
    # token. `cast` maps same-name to the `cast` transition (F1 complete), which
    # is a different question asked one phase later.
    "transition_to_cast": "validate",
    # DERIVED from the crossings above rather than re-typed beside them, so the
    # gate `.gate-passed` is compared against and the gate the lead is told to
    # run are the same value and not two values that agree by inspection.
    "transition_to_inspect": {
        phase: crossing["gate"]
        for phase, crossing in _ACTION_CROSSINGS["transition_to_inspect"].items()
    },
    "transition_to_grind": "grind",
    "transition_to_assay": "assay",
    "transition_to_temper": "temper",
    "transition_to_nyquist": "nyquist",
    "transition_to_done": "done",
}


def _gate_tokens_named(entry: str | dict[str, str] | None) -> list[str]:
    """Every gate token an `_ACTION_TO_GATE` value can resolve to.

    The flattening the invariant test and the AC-059 membership check both
    walk, written once so neither has to know which of the two shapes a value
    carries. `set(_ACTION_TO_GATE.values())` was the spelling before the
    phase-keyed row existed, and it raises on one — a membership question about
    the gates a value RESOLVES to is not a question about the container holding
    them.
    """
    if isinstance(entry, dict):
        return [gate for gate in entry.values() if gate]
    return [entry] if entry else []




def _expected_gate_for_action(action: str, phase: str = "") -> str | None:
    """Return the gate phase a given transition action asks the lead to run.

    fallout AC-059: every value `_ACTION_TO_GATE` names must be a key of
    `GATE_TO_TRANSITION`, or the guidance sends a lead to a door that does not
    exist. Asserted by the invariant test rather than here — this stays a total
    lookup, because a guidance surface that raised would take Foundry-Next down
    with it.

    fallout D-058 — ``phase`` IS THE SECOND HALF OF THE QUESTION FOR EXACTLY ONE
    ACTION, AND OMITTING IT ANSWERS None RATHER THAN GUESSING.

    An action with one gate answers without a phase, which is why every existing
    caller that names only the action keeps working. An action whose gate
    depends on the phase it was emitted from CANNOT be answered without one, and
    the honest answer to an unanswerable question here is None: the one consumer
    compares this against the `.gate-passed` marker, and a wrong gate there tells
    a lead who ran the correct door to re-run the refusing one — which is the
    second half of what D-058 cost, on top of the imperative.
    """
    return _gate_for_entry(_ACTION_TO_GATE.get(action), phase)


def _gate_for_entry(entry: str | dict[str, str] | None, phase: str) -> str | None:
    """Resolve one `_ACTION_TO_GATE` value against the emitting phase."""
    if isinstance(entry, dict):
        return entry.get(phase)
    return entry




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
    now = now_iso()
    changed = False

    castings_dir = fdir / "castings"
    has_casting_files = castings_dir.exists() and any(castings_dir.glob("casting-*.md"))
    has_manifest = (castings_dir / "manifest.json").exists()
    validate_passed_marker = fdir / VALIDATE_PASSED_MARKER

    def _close(pid: str) -> bool:
        entry = phase_times.get(pid)
        if entry and "started_at" in entry and "ended_at" not in entry:
            finalize_open_phase_entry(entry, now)
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




#: fallout FR-034 / FR-055 / AC-053 — the caller argument that tells a lead's
#: Foundry-Next from a sub-agent's read. Only the LEAD's call arms the ordering
#: token and resets the stall clock; a sub-agent orienting itself does neither.
LEAD_CALLER = "lead"

#: The other member, and the value every sub-agent must pass. Named rather than
#: typed at each surface for the reason `_TEAMS_DOWN_HINT` is named: the wire
#: enum, the tool description, the spawn-time instruction and the guard all say
#: this word, and four spellings of one token is four chances to drift.
SUBAGENT_CALLER = "subagent"

#: fallout FR-034 / FR-055 / AC-053 — THE SENTENCE THAT HAS TO REACH A SUB-AGENT.
#:
#: The guard is correct and was unreachable: `caller` defaults to the lead value
#: at the server boundary, and a sub-agent that never learns the argument exists
#: takes the default and arms the lead's ordering token and stall clock on every
#: orienting read. Publishing the argument in the schema is not the same as
#: telling anyone to pass it, so the instruction is stated ONCE here and quoted
#: by every surface a sub-agent actually reads: the Foundry-Next tool
#: description it loads with the tool list, and the spawn-time protocol block
#: the lead appends to its prompt.
SUBAGENT_CALLER_INSTRUCTION = (
    f"If you are a SUB-AGENT rather than the lead, pass caller='{SUBAGENT_CALLER}' "
    "on every Foundry-Next call. The lead's call is a protocol step — it arms "
    "the ordering token the next Foundry-Gate requires and resets the stall "
    "clock; yours is a read, and passing the argument keeps it one."
)

#: fallout FR-055 / AC-053 (D-089, concern C-057) — THE DOORS THE ARGUMENT
#: SCOPES, DECLARED ONCE.
#:
#: `Foundry-Next` was the only one until D-089, and the sentence above says so
#: in as many words. `foundry_get_context` calls `foundry_next_action` for its
#: `next_action` field, so `Foundry-Context` arms the same ordering token and
#: takes the same argument — and `agents/assayer.md` and `skills/prove/SKILL.md`
#: both instruct a sub-agent to call THAT door at F2, which makes it the one
#: more likely to be reached by an agent that is not the lead.
#:
#: A SEPARATE constant rather than an edit to the sentence above, and the reason
#: is ownership rather than taste: that sentence is quoted BYTE-IDENTICALLY by
#: five stream agents, the assayer and four skills, and pinned against this
#: constant by `tests/test_protocol_prose.py` and `tests/test_skill_prose.py` —
#: files castings 6 and 11 own. Editing it here would redden their suites for a
#: change they have not made. So the wire surfaces this server owns gain the
#: clause now, and the quoted sentence gains it in the commit that updates the
#: files quoting it (concern raised on casting 6).
SUBAGENT_CALLER_DOORS = ("Foundry-Next", "Foundry-Context")

SUBAGENT_CALLER_DOOR_CLAUSE = (
    "The same argument scopes "
    + " and ".join(SUBAGENT_CALLER_DOORS)
    + f": both arm the lead's ordering token, so a sub-agent passes "
    f"caller='{SUBAGENT_CALLER}' on either door."
)




def _terminal_outlook(fdir: Path | None) -> dict:
    """fallout FR-021 / FR-047 / CT-007 / AC-028 / OT-026 — where this run ENDS.

    Returns ``{"heading_for", "open_by_tier", "cycles_to_cap"}`` for every
    `Foundry-Next` response, whatever the phase and whatever the action.

    fallout D-066 — TOTAL OVER ``fdir``, BECAUSE "EVERY RESPONSE" INCLUDES THE
    ONES THAT NEVER REACHED A RUN.

    This took a `Path` and was called from one place, below every early return,
    under a comment claiming it covered them. Driven: the corrupt-artifact
    response carried exactly `['corrupt_artifacts', 'error', 'hint']` and the
    no-active-run response carried none of the three — so the two responses a
    lead reads when something is already wrong were the two with no outlook on
    them. A field that is present on the easy path and absent on the hard one is
    a field a reader has to test for, which is the same as not having it.

    A CORRUPT RUN STILL HAS AN OUTLOOK, and it is computable: every read below
    is a tolerant one, so a run whose `verdicts.json` will not parse still
    answers about its cap, its phase and the defects it has. A run directory
    that does not exist has NO outlook, and the honest answer is a present key
    with a null value rather than an absent key: `heading_for` names where a run
    is heading, and there is no run.

    WHY EVERY RESPONSE, AND NOT A SECTION SOMEWHERE. A named backlog is a
    SUCCESSFUL end (FR-047): a run that halts with three LATENT and one
    HARDENING defect written down has done its job, and a lead that believes
    DONE is the only acceptable ending will grind cycles against a target it
    has already met. The three facts that decide which ending is coming — the
    backlog by tier, the cycles left, and which terminal state they add up to —
    are therefore on the same surface the lead reads before every single call,
    rather than in a report it reads once at the end.

    `cycles_to_cap` is None on an unbounded run, which is the default and is a
    REAL answer: "no cap" and "zero cycles left" are opposite facts and a
    reader that conflates them halts a run that had no cap at all.
    """
    if fdir is None or not fdir.exists():
        return {
            "heading_for": None,
            "open_by_tier": {},
            "cycles_to_cap": None,
            "report_written": None,
        }

    state = _load_json(fdir / "state.json")
    # fallout GI-033 / AC-061 (D-021 / D-035) — THE BUCKETS COME FROM THE LEAF.
    # `gates._open_defects_by_tier` is the same read behind a verifier-set
    # module, and Foundry-Next reports recorded decisions rather than reaching
    # into the gate that made them. The three closed-set values are supplied
    # because a leaf may not know a vocabulary.
    buckets = open_defects_by_tier(
        fdir, tiers=DEFECT_TIERS, unknown_tier=TIER_UNKNOWN, tier_of=defect_tier
    )
    open_by_tier = {tier: len(rows) for tier, rows in sorted(buckets.items())}

    max_cycles = persisted_max_cycles(state)
    cycles_to_cap = None if max_cycles <= 0 else max(0, max_cycles - current_cycle(fdir))

    halted = state.get("phase") == RUN_PHASE_HALTED
    if halted:
        heading_for = RUN_PHASE_HALTED
    elif cycles_to_cap == 0:
        # The next GRIND door would seal HALTED, and that is what the run is
        # heading for even though nothing has stopped yet. Saying so BEFORE the
        # door is the whole point of the field.
        heading_for = RUN_PHASE_HALTED
    else:
        heading_for = "DONE"
    return {
        "heading_for": heading_for,
        "open_by_tier": open_by_tier,
        "cycles_to_cap": cycles_to_cap,
        # fallout ST-001 / CT-004 / CT-007 (D-160, lead ruling
        # `lead_ruling_st_001_vs_fr_046`) — THE ENDING'S DELIVERABLE, ON THE
        # BLOCK THAT REPORTS THE ENDING.
        #
        # The ruling keeps the halt transition NON-REFUSING on a failed report
        # regeneration — FR-046 governs the refusal question — and asks in
        # exchange that the incompleteness be legible "at the surfaces a human
        # or a later door actually reads … so a halt with no report announces
        # itself as such rather than requiring someone to notice a False buried
        # in a payload". This is that block: `heading_for` says WHERE the run
        # ends, and HALTED exists to record a named backlog, so whether the
        # backlog was actually written down belongs beside it.
        #
        # THE SURFACE THAT MISSED IT WAS THIS ONE, and the case is not
        # hypothetical: the run most likely to halt with no report is the run
        # whose ledger will not render, and `foundry_next_action`'s
        # corrupt-artifact early return merges exactly this dict and never
        # reaches `_compute_next_action`'s HALTED branch — the branch that DOES
        # name the missing report (D-171). So on the one run where the fact
        # matters most, `heading_for: HALTED` was the whole of what a lead was
        # told.
        #
        # None on a run that has not ended, because "was the report written" is
        # not a question about a run still going: a live run's REPORT.md is
        # whatever the last Foundry-Report left, and reporting False for it
        # would read as a failure that has not happened. THE FILE'S PRESENCE is
        # the ground truth on a halted run, the same source `_halted_refusal`
        # and the HALTED branch read, so the three cannot say different things
        # about one file — and a lead who repairs the cause and calls
        # Foundry-Report sees this flip without anything having to re-record it.
        "report_written": (fdir / REPORT_MD_FILENAME).exists() if halted else None,
    }




def foundry_next_action(
    project_root: str = ".",
    *,
    caller: str = LEAD_CALLER,
    _arm_stall_clock: bool = True,
) -> dict:
    """Determine what the lead should do next based on current foundry state.

    ``caller`` is the MCP surface's own distinction (FR-034 / FR-055 / AC-053).
    Anything other than ``"lead"`` is a SUB-AGENT read: it returns the same
    guidance and arms NEITHER the `.next-action-called` ordering token NOR the
    `.last-next-at` stall clock.

    Both halves matter and they fail in opposite directions. A sub-agent that
    armed the ordering token would satisfy, on the lead's behalf, the handshake
    that proves the LEAD consulted guidance before a transition — so a lead
    could gate and transition having never called Foundry-Next, because a
    tracer did it. A sub-agent that reset the stall clock would restart the
    watchdog's measurement every time any agent oriented itself, which is the
    D-023 shape one surface over: the clock must measure the LEAD's silence,
    and a stream reading guidance is not the lead speaking.

    ``_arm_stall_clock`` is FALSE for exactly one caller, ``foundry_get_context``
    (AC-035 / OT-028). Underscore-prefixed and keyword-only because it is not
    part of the MCP surface, where the distinction that IS published is
    ``caller``. See the marker block at the end of this function for why the
    ordering distinction exists at all.
    """
    is_lead = caller == LEAD_CALLER
    fdir_stamp = get_run_dir(project_root)
    if fdir_stamp and (corrupt := _artifact_guard(fdir_stamp)):
        # fallout D-066 / AC-028 / CT-007 — THE OUTLOOK RIDES THIS RETURN TOO.
        #
        # This was the one early return in this function, and the merge below
        # sat under it claiming to cover it. The outlook is fully computable
        # here: `state.json` is what carries the cap and the phase, the reads
        # are tolerant, and a corrupt artifact somewhere else in the run does
        # not make "how many cycles are left" unanswerable. A lead reading a
        # refusal is exactly the lead who needs to know whether the run is
        # heading for DONE or HALTED.
        return {**corrupt, **_terminal_outlook(fdir_stamp)}
    trace_skip_decision: dict | None = None
    if fdir_stamp and fdir_stamp.exists():
        _stamp_subphase_transitions(fdir_stamp)
        trace_skip_decision = _stamp_trace_skip(fdir_stamp)
    result = _compute_next_action(project_root)
    if trace_skip_decision and trace_skip_decision.get("skip"):
        result["trace_skip"] = trace_skip_decision

    # P4 (FR-005 / ST-002): passing-gate → guidance-state advance. If the gate
    # for the current transition action already passed (recorded in
    # ``.gate-passed`` by foundry_gate), surface that the gate is satisfied so
    # the lead proceeds to the transition step rather than re-running the gate.
    gate_advance_note = None
    if fdir_stamp and fdir_stamp.exists():
        # fallout D-058 — resolved against the phase the action was emitted
        # from. At F3 this compared `.gate-passed` against `inspect`, so a lead
        # who ran the CORRECT door (`inspect_start`) got no advance signal and
        # was sent back to the one that refuses, while a stale `inspect` marker
        # produced "ALREADY PASSED — do NOT re-run it" for a gate that never
        # guarded this crossing.
        expected_gate = _expected_gate_for_action(
            result.get("action", ""), str(result.get("phase", ""))
        )
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
        action, original_instructions, result.get("details", {}),
        run_name=run_name_for_imperative,
        # fallout D-058 — the phase the action was emitted FROM, which is the
        # other half of the question for `transition_to_inspect`. It is already
        # on the result, so this reads the router's own answer rather than
        # re-deriving one that could disagree with it.
        phase=str(result.get("phase", "")),
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

    # fallout CT-007 / AC-028 / OT-026 \u2014 THE RULES BLOCK ON A HALTED RUN
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
    # and the last of those three sentences was false outright \u2014 ST-001 and
    # CT-004 make HALTED a THIRD ending beside DONE and error, reached by a
    # SUCCESSFUL transition.
    #
    # Two changes, because the defect has two halves. The standing line now
    # names all three endings, and a halted run gets its own block: the caching
    # argument for a byte-identical prefix (this block is a cache-hit-eligible
    # prefix across ~30-50 calls per run) does not apply to a run that has
    # stopped and will be called at most a handful more times.
    halted_now = result.get("phase") == RUN_PHASE_HALTED
    if halted_now:
        # fallout US-006 / FR-019 / CT-005 (D-147) \u2014 THE SECOND SURFACE THAT
        # ASSERTED THE CAP FOR EVERY ENDING, through the same one spelling the
        # imperative header takes. This line read "The run stopped at its
        # configured --max-cycles." and was emitted for `lead_ruling`,
        # `spec_change_required` and `user_stop` alike; see
        # `_HALT_CAUSE_SENTENCES` for the drive.
        critical_rules = (
            "\n\nCRITICAL RULES \u2014 THIS RUN IS HALTED:"
            "\n- The run stopped because "
            + _halt_cause((result.get("details") or {}).get("halted_reason_member"))
            + ". HALTED is a "
            "terminal state, distinct from DONE and reached by a successful "
            "transition rather than an error (ST-001 / CT-004 / CT-007)."
            "\n- Do NOT dispatch another wave, do NOT call Foundry-Phase, and "
            "do NOT call Foundry-Next in a loop. There is no next transition "
            "to make."
            "\n- The report has been generated as part of the halt and names "
            "every open LIVE and LATENT defect. Read it."
            # fallout US-006 (D-147): the cap RAISE is a remedy for exactly one
            # of the four endings. Offering it on a `spec_change_required` halt
            # tells the lead to re-run the work the ruling just said is not
            # useful until the spec moves — the cause claim above, said as
            # advice.
            "\n- Hand the remaining work to a new run"
            + (
                ", or re-run with a higher --max-cycles"
                if (result.get("details") or {}).get("halted_reason_member")
                == "cap_reached"
                else ""
            )
            + ". Nothing below asks you to keep going."
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
        recorded_mode = _recorded_inspect_mode(fdir_spend)
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
    # fallout FR-021 / CT-007 / AC-028 / OT-026 — on EVERY response, whatever the
    # phase and whatever the action. Placed here rather than in
    # `_compute_next_action` for exactly that reason: that function returns from
    # a dozen branches and a field added to one of them is a field absent from
    # eleven.
    #
    # fallout D-066 — AND OUTSIDE THE `fdir` GUARD, which is what made the claim
    # above false for two response shapes. The guard belongs to the marker
    # WRITES below it, which genuinely need a directory to write into; the
    # outlook is total and needs nothing. Standing inside it meant the
    # no-active-run response — the one whose action is `init` — carried none of
    # the three fields, and the comment saying otherwise was the only place the
    # promise was kept.
    result.update(_terminal_outlook(fdir))
    if fdir and fdir.exists():
        now_stamp = f"{now_iso()}\n"
        # Ordering token: armed here, consumed (unlinked) by foundry_gate /
        # foundry_mark_phase_complete to prove Foundry-Next preceded a gate
        # or phase transition.
        #
        # AC-053: the LEAD's call arms it. A sub-agent read arms nothing, so it
        # cannot satisfy the lead's handshake on the lead's behalf.
        if is_lead:
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
        # The ordering token above is armed on both paths FOR THE LEAD: it
        # answers "did the lead consult guidance before transitioning", and
        # Foundry-Context does return the full guidance payload. Only the
        # STALL measurement is Foundry-Next's alone.
        #
        # fallout FR-055 / AC-053 (D-089) — "both paths" means both of the
        # LEAD'S paths, and it did not, because `foundry_get_context` passed no
        # `caller` and every Foundry-Context call therefore read as the lead's.
        # A sub-agent reorienting itself now arms neither marker through either
        # door; see `foundry_get_context`.
        #
        # AC-053: and a sub-agent read does not reset it either. The clock
        # measures the LEAD's silence; a stream orienting itself is not the lead
        # speaking, and a watchdog any agent can silence is not a watchdog.
        if _arm_stall_clock and is_lead:
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
    # error stops it", which ST-001 / CT-004 made false: a halt ends a run in a
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
#: fallout US-006 / FR-019 / CT-005 (D-147) — ONE SPELLING OF WHY THIS RUN
#: ENDED, KEYED BY THE VOCABULARY MEMBER.
#:
#: Both lead-facing surfaces a halted `Foundry-Next` emits — the imperative
#: header below and the CRITICAL RULES block in `foundry_next_action` — asserted
#: the cap outright ("it reached its --max-cycles limit", "The run stopped at
#: its configured --max-cycles"). THREE OF THE FOUR MEMBERS ARE NOT THE CAP.
#: Driven on a run at F3 cycle 2 with `max_cycles` 3 — the cap NOT reached, one
#: cycle still left — sealed with `Foundry-Phase('halt',
#: reason='spec_change_required')`: `Foundry-Next` returned heading_for HALTED,
#: cycles_to_cap 1, and instructions opening "The run stopped at its configured
#: --max-cycles" followed by "it reached its --max-cycles limit". Both false.
#:
#: US-006 asks for "a halt door with a named reason ... SO THAT A RULING IS
#: RECORDED IN THE ARCHIVE INSTEAD OF A HAND-EDITED CAP". The archive was always
#: right — REPORT.md and report.json carry the member and the lead's text — so
#: what these two strings did was overwrite the ruling on the one surface the
#: lead is REQUIRED to read before its next call. Both predate FR-018, when the
#: cap was the only route to HALTED, and neither was revisited when the halt
#: door landed beside it.
#:
#: KEYED BY MEMBER, PINNED TO THE VOCABULARY. `tests/orchestration/
#: test_guidance.py#test_every_halt_reason_has_its_own_cause_sentence` derives
#: the key set from `HALT_REASONS`, so a member added to the closed vocabulary
#: fails there rather than falling through to a sentence written for a
#: different ending — the `_PYTEST_DISCOVERY_PHRASE` discipline, one rung along.
_HALT_CAUSE_SENTENCES = {
    "cap_reached": (
        "it reached its --max-cycles limit and stopped with open work"
    ),
    "lead_ruling": (
        "the lead ended it on a ruling and its open work is recorded"
    ),
    "spec_change_required": (
        "it ended because the spec has to change before more work is useful"
    ),
    "user_stop": (
        "the user stopped it and its open work is recorded"
    ),
}

#: The answer for a `halted_reason` carrying text and no member — the shape
#: every archive written before FR-019 carries. It says the run stopped and
#: declines to name WHICH of the four endings it was, which is the same
#: abstention `vocab.halt_reason` makes rather than guessing a member out of a
#: pre-release sentence. Naming the cap here is how the defect above started.
_HALT_CAUSE_UNNAMED = "it stopped with open work"


def _halt_cause(member: object) -> str:
    """The cause clause for a recorded halt reason member (fallout D-147)."""
    return _HALT_CAUSE_SENTENCES.get(member, _HALT_CAUSE_UNNAMED)


_ACTION_IMPERATIVES = {
    "init": "YOUR NEXT CALL: Foundry-Init (start a new run)",
    # fallout FR-035 / AC-054 / CT-007: the one action whose imperative is to
    # STOP. The generic
    # fallback header says "Execute the first tool call mentioned. Do not
    # deliberate.", which on a halted run would push the lead straight back into
    # the loop the cap ended — so this action gets an explicit entry.
    #
    # fallout US-006 (D-147): `{halt_cause}` is substituted from the RECORDED
    # reason member. Unlike `{gate}` / `{token}`, an unresolved `{halt_cause}`
    # may NOT fall back to the generic header — that header says "Execute the
    # first tool call mentioned. Do not deliberate.", which is the exact push
    # back into the loop this entry exists to stop — so `_halt_cause` is total
    # over every input and always resolves.
    "halted": (
        "YOUR NEXT CALL: NONE. This run is HALTED — {halt_cause}. The report is "
        "generated. Do NOT dispatch a wave, do NOT call Foundry-Phase, do NOT "
        "call Foundry-Next in a loop. Read REPORT.md, tell the user what "
        "remains open by tier, and stop."
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
        "  (1) Foundry-Gate(phase='validate')\n"
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
    # fallout D-058 / AC-059 — ONE ACTION, TWO CROSSINGS, AND THE STEPS ARE
    # SUBSTITUTED FROM ONE ROW SO THEY CANNOT NAME DOORS THAT DO NOT MATCH.
    #
    # This named `Foundry-Gate(phase='inspect')` above
    # `Foundry-Phase(phase='inspect_start')` — a pair refused at BOTH emission
    # sites, because that gate guards the `cast` transition and the call is
    # `inspect_start`. From F3 the gate is refused ("accepted from F1 and from
    # nowhere else"); from F1 the phase call is ("accepted from F3, and from
    # F2"). Its explanatory tail was F3's, printed verbatim to a lead standing in
    # F1 about a transition F1 does not take.
    #
    # `{gate}` and `{token}` come from ONE `_ACTION_CROSSINGS` row, chosen by the
    # phase the action was emitted from, so step (1) and step (2) are two fields
    # of one fact rather than two strings that have to be kept in agreement. The
    # phase-specific detail — what THIS crossing also does — is already on the
    # branch's own `instructions`, which is where it belongs: this header's job
    # is the two call names.
    "transition_to_inspect": (
        "YOUR NEXT CALLS (in order):\n"
        "  (1) Foundry-Gate(phase='{gate}')\n"
        "  (2) Foundry-Phase(phase='{token}') — the transition that OPENS this "
        "INSPECT, and the gate above is the one that guards it. That call sweeps "
        "the evidence corpus and RECORDS this INSPECT's width and the roster "
        "every stream then runs; from F3 it also advances the server-side cycle "
        "counter, so skipping it files the next cycle's stream records, defects "
        "and roll-up entries under the last one and the recurring-class "
        "escalation never accumulates. Editing state.json by hand records no "
        "width at all, and every door that reads one then refuses."
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
        "to retrieve its findings, then CONFIRM THE STREAM'S OWN RECORD EXISTS \u2014 "
        "Foundry-Context shows the cycle's roll-up. Do NOT poll \u2014 the harness notifies you.\n"
        "\n"
        "fallout FR-023 / FR-049 / GI-016 \u2014 YOU DO NOT RECORD A STREAM. THE AGENT "
        "DOES.\n"
        "Every verifying stream calls Foundry-Stream itself, with the counts it "
        "actually measured, and a second record for the same (stream, cycle) "
        "REPLACES the first rather than summing with it. A lead that records on "
        "an agent's behalf is asserting numbers it did not measure, and when the "
        "agent then records its own the cycle carries two accounts of one run. "
        "If a stream finished and no record exists, that is a finding about the "
        "stream \u2014 re-dispatch it, or file it \u2014 not a gap for you to fill in."
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
        "before acting, then (b) the defect list in a '## Defects to fix this cycle:' block, "
        # fallout FR-038 / GI-021 / CT-008 / AC-002 \u2014 THE ALIGNMENT BLOCK REACHES
        # THE PROMPT THROUGH THE STEP THAT BUILDS IT.
        #
        # `Foundry-Tasks` computes the block correctly and named it to
        # nobody: grep over src/, tests/, commands/ and agents/ returned
        # its definition, its one assignment and one test assertion, and
        # no consumer at all. FR-038 ends "the lead pastes it verbatim
        # into the dispatch prompt", and the lead pastes what THIS
        # imperative names \u2014 which is how `grind_cycle_context` and
        # `progress_protocol` already reach a teammate. Step (1) of this
        # same sequence is the Foundry-Tasks call the block comes back on,
        # so nothing has to be remembered between calls.
        "then (c) that task's `alignment_block` from the Foundry-Tasks result of step (1), "
        "VERBATIM, whenever the task carries one \u2014 it is server-generated and names the "
        "originating defects, the requirement ids, the owning casting and file of the fix, and "
        "the sibling files THIS casting owns that cite those ids, which is how one fix reaches "
        "every surface of its rule in the same GRIND. Do not summarise it and do not compose "
        "your own. "
        "Order: dispatch \u2192 cycle_context \u2192 defects \u2192 alignment. "
        "All appended BELOW the dispatch block, never inside it.>). "
        "Same foreground rule as CAST \u2014 never background-spawn GRIND teammates. "
        "For the model: obey the model clause in the `instructions` Foundry-Spawn-Teammate "
        "returned \u2014 pass the model it names, or no model parameter when it names none. This "
        "server owns that decision; never re-derive it here." + _GATE_THEN_PHASE_NOTE
    ),
    # fallout D-059 / GI-001 / AC-059 — AND THE THREE ARMS THAT REACHED
    # `inspect_start` WITHOUT NAMING ITS GATE.
    #
    # GI-001's violation column reads "a casting that ... lets a transition skip
    # its gate". AC-059 added `inspect_start` to `GATE_TO_TRANSITION` precisely
    # so that crossing would have one, and these three told the lead to make the
    # transition call with no gate before it — so the token existed and no
    # lead-facing surface named it. Each now names `Foundry-Gate(phase=
    # 'inspect_start')` first, which is the same pair `transition_to_inspect`'s
    # F3 half carries; the lead reaching this crossing by any of the four routes
    # is told to make the same two calls in the same order.
    "fix_defects": (
        "YOUR NEXT ACTION depends on GRIND state:\n"
        "  - IF no GRIND team registered yet: follow the transition_to_grind sequence.\n"
        "  - IF teammates are running: WAIT. When all report complete, TeamDelete + Foundry-Team-Down + "
        "Foundry-Gate(phase='inspect_start') + Foundry-Phase(phase='inspect_start') + re-run INSPECT."
        + _GATE_THEN_PHASE_NOTE
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
    # fallout FR-035 / AC-054 — THE F6 ORDER, STATED EXACTLY.
    #
    # This read "YOUR NEXT CALL: Foundry-Phase(phase='done')", which contradicts
    # the sequence the doors actually enforce: the DONE evaluation REQUIRES the
    # generated report, and it sweeps the committed evidence corpus — so a lead
    # that stripped `evidence/` before generating the report is refused for a
    # missing document, and one that strips AFTER the gate passes has changed
    # the tree the gate judged. Report, Gate, strip, Phase, in that order, is
    # the only sequence in which each step's precondition is still true when the
    # next one runs.
    "transition_to_done": (
        "YOUR NEXT CALLS (in order, and the order is the whole of it):\n"
        "  (1) Foundry-Report — DONE is refused without the generated report, "
        "and it is generated, never hand-written.\n"
        "  (2) Foundry-Gate(phase='done') — this is where the evidence corpus is "
        "re-executed at HEAD. It must pass BEFORE the strip, on the tree the "
        "corpus was captured against.\n"
        "  (3) `git rm -r --cached evidence/ && rm -rf evidence/` then commit — "
        "the strip, AFTER the gate has judged the corpus and not before.\n"
        "  (4) Foundry-Phase(phase='done') — seals F6, carries your appended "
        "prose into the report and archives the run.\n"
        "Stripping before (2) refuses the gate for a corpus that is no longer "
        "there; stripping after (4) leaves the run sealed against a tree that "
        "no longer exists." + _GATE_THEN_PHASE_NOTE
    ),
    # fallout FR-035 / AC-054 — THE NINE THE SURVEY COUNTED.
    #
    # `_compute_next_action` emitted these and `_ACTION_IMPERATIVES` had no
    # entry for any of them, so each fell through to the generic header —
    # "Execute the first tool call mentioned. Do not deliberate." — over an
    # instructions body that, for several of them, mentions no tool call at all.
    # An imperative table with holes is worse than none: the holes are invisible
    # and they are exactly where the lead improvises. The invariant test derives
    # the emitted set from this function's own AST, so the tenth is caught the
    # day it is written.
    "transition_to_temper": (
        "YOUR NEXT CALLS (in order):\n"
        "  (1) Foundry-Gate(phase='temper')\n"
        "  (2) Foundry-Phase(phase='temper') — that call enters F5, records "
        "TEMPER's own INSPECT at FULL width and sweeps the whole evidence "
        "corpus. Editing state.json by hand leaves the phase's first INSPECT "
        "with no recorded mode." + _GATE_THEN_PHASE_NOTE
    ),
    "run_temper": (
        "YOUR NEXT CALL: spawn the TEMPER micro-domain agents. TEMPER zooms into "
        "individual functions, single pages and specific flows and asks whether "
        "they actually work — its roster is the open TEMPER_CANDIDATE "
        "observations plus its own micro-domains. Each agent records its OWN "
        "run with Foundry-Stream and files what it finds; a probe driven and "
        "found clean is a result, not a blank."
    ),
    "transition_to_nyquist": (
        "YOUR NEXT CALLS (in order):\n"
        "  (1) Foundry-Gate(phase='nyquist')\n"
        "  (2) Foundry-Phase(phase='nyquist') — that call enters F5.5 and sweeps "
        "the whole evidence corpus first. It is refused unless this run was "
        "started with --nyquist and every requirement is VERIFIED."
        + _GATE_THEN_PHASE_NOTE
    ),
    "run_nyquist": (
        "YOUR NEXT CALL: batch the VERIFIED requirements by 5 and spawn one "
        "Agent(subagent_type='foundry:nyquist-auditor') per batch in a SINGLE "
        "parallel message. Each classifies COVERED / UNTESTED / UNDERTESTED, "
        "generates minimal behavioural tests, runs them and commits the passing "
        "ones. Any ESCALATE_IMPL_BUG result starts a new GRIND cycle. Never mark "
        "an untested requirement as passing."
    ),
    "assay_failed_loop_back": (
        "YOUR NEXT CALLS (in order):\n"
        "  (1) Foundry-Tasks\n"
        "  (2) Foundry-Gate(phase='grind')\n"
        "  (3) Foundry-Phase(phase='assay_fail') — the ASSAY-rejection door into "
        "F3. It is the same transition `grind_start` is, through the other door, "
        "and it is bounded by the same --max-cycles cap." + _GATE_THEN_PHASE_NOTE
    ),
    "widen_inspect": (
        "YOUR NEXT CALLS (in order):\n"
        "  (1) Foundry-Gate(phase='inspect_start')\n"
        "  (2) Foundry-Phase(phase='inspect_start') AGAIN, from F2. "
        "The DELTA cycle came back clean, which earns the widening re-open "
        "rather than the ASSAY gate: that crossing advances the cycle counter, "
        "sweeps the whole evidence corpus, records FULL and requires the full "
        "roster. Then run every stream it names — a spot check is not a FULL "
        "INSPECT."
        + _GATE_THEN_PHASE_NOTE
    ),
    "record_inspect_width": (
        "YOUR NEXT CALLS (in order):\n"
        "  (1) Foundry-Gate(phase='inspect_start')\n"
        "  (2) Foundry-Phase(phase='inspect_start') from F2. This "
        "INSPECT has no recorded width, so the roster, the rule and the evidence "
        "sweep it was opened with are all unknown, and every door that reads the "
        "width refuses. Do NOT edit state.json by hand — the transition is what "
        "records the decision."
        + _GATE_THEN_PHASE_NOTE
    ),
    "done": (
        "YOUR NEXT CALL: NONE. This run is DONE. Read REPORT.md and tell the "
        "user what shipped. Do NOT dispatch a wave, do NOT call Foundry-Phase, "
        "do NOT call Foundry-Next in a loop. Start a NEW run with Foundry-Init "
        "if there is more work."
    ),
    "unknown": (
        "YOUR NEXT CALL: Foundry-Context. The guidance engine does not recognise "
        "this run's phase, which means `state.json` carries a value no "
        "transition writes. Read the run's state, then Foundry-Next again. Do "
        "NOT guess a transition token — an unrecognised phase is a state to "
        "diagnose, not one to advance out of."
    ),
}




def _format_imperative_header(
    action: str,
    instructions: str,
    details: dict,
    run_name: str = "",
    phase: str = "",
) -> str:
    """Produce the one-line 'YOUR NEXT CALL' header for the given action.
    Falls back to a generic header if the action is unmapped.

    Substitutes `{run}` in the imperative with the active run slug so team
    names (cast-{run}-wave-N, grind-{run}-cycle-N) are distinguishable across
    concurrent runs. DECOMPOSE no longer uses a team — it spawns background
    Agents (per commands/start.md \u00a7F0.5).
    If no run is active, `{run}` is replaced with `active` as a safe default.

    fallout D-058 — AND ``{gate}`` / ``{token}`` ARE SUBSTITUTED FROM THE
    CROSSING THE EMITTING PHASE NAMES.

    `transition_to_inspect` is emitted from F1 and from F3, and the two are
    different crossings: F1 gates `inspect` and calls `Foundry-Phase('cast')`,
    F3 gates `inspect_start` and calls `Foundry-Phase('inspect_start')`. One
    frozen pair of literals is refused at BOTH sites, so the pair is taken from
    one `_ACTION_CROSSINGS` row at emission time. Step (1) and step (2) are two
    fields of that row, which is what makes them agree by construction rather
    than by anyone remembering to change both.

    A PLACEHOLDER THAT DOES NOT RESOLVE TAKES THE GENERIC FALLBACK. Printing a
    literal `{gate}` would be worse than saying nothing — the lead is instructed
    to "execute the first tool call mentioned", and `Foundry-Gate(phase=
    '{gate}')` is a call it would try to make. The fallback sends it to the
    CONTEXT below, which the branch already wrote for this phase.
    """
    imperative = _ACTION_IMPERATIVES.get(action)
    if imperative and "{halt_cause}" in imperative:
        # fallout US-006 / FR-019 (D-147) — substituted from the RECORDED
        # member, which the halted branch publishes in `details` beside the
        # sentence. Resolved BEFORE the crossing substitution and outside the
        # unresolved-placeholder fallback below, because `_halt_cause` is total:
        # there is no input for which this leaves a literal `{halt_cause}` in
        # the header, and a halted run must never take the generic fallback.
        imperative = imperative.replace(
            "{halt_cause}", _halt_cause(details.get("halted_reason_member"))
        )
    if imperative:
        crossing = _ACTION_CROSSINGS.get(action, {}).get(phase)
        if crossing:
            imperative = (
                imperative
                .replace("{gate}", crossing["gate"])
                .replace("{token}", crossing["token"])
            )
        if "{gate}" in imperative or "{token}" in imperative:
            imperative = None
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
    cycle = current_cycle(fdir)

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

    # fallout D-014 / D-015 — ONE LADDER, DECLARED IN THE VOCABULARY.
    # This was a second hand-typed copy of the ten rows `schemas.vocab`
    # declares, so a phase added there and not here would render as a run with
    # a step missing — and the two would agree only by inspection.
    phases = PHASE_LADDER

    # fallout CT-007 / AC-028 — HALTED IS IN THE DISPLAY VOCABULARY. D-137.
    #
    # The header read `{phase} {phase_names.get(phase, phase)}`, whose fallback
    # is the token itself — fine for every member of `phases`, where the token
    # and the name differ ("F2 INSPECT"), and wrong for the one phase that has
    # no ladder row. Driven on a halted state, the banner read
    # "F O U N D R Y  HALTED HALTED". CT-007 makes HALTED a named terminal
    # state every `Foundry-Next` response reports through `heading_for`, and a
    # notice a lead reads has to read correctly in a terminal;
    # a stutter is what a fallback produces when a
    # value it never anticipated reaches it.
    #
    # The label is built as ONE string rather than by adding a HALTED row to
    # `phases`: the ladder below enumerates the phases a run PASSES THROUGH and
    # marks the current one, and HALTED is not a step on that path — it is
    # where a run stops instead of continuing along it. It is rendered as its
    # own line under the ladder for the same reason.
    phase_names = PHASE_NAMES
    halted_display = phase == RUN_PHASE_HALTED
    phase_name = phase_names.get(phase, "")
    header_label = f"{phase} {phase_name}".strip() if phase_name else phase
    header_colour = BRED if halted_display else BCYAN
    run_name = fdir.name

    lines = [foundry_hammer(f"F O U N D R Y  {header_colour}{header_label}{RESET}  Cycle: {cycle}  {elapsed}")]

    # Phase list
    for pid, pname in phases:
        timing = phase_times.get(pid, {})
        dur = timing.get("duration", "")

        if pid == phase:
            icon = f"{BGREEN}\u25b6{RESET}"
            label = f"{BWHITE}{pid} {pname}{RESET}"
            right = f"  {BGREEN}\u25c0 {elapsed}{RESET}"
        elif dur or timing.get("started_at"):
            icon = f"{GREEN}\u2713{RESET}"
            label = f"{DIM}{pid} {pname}{RESET}"
            right = f"  {DIM}{dur}{RESET}" if dur else ""
        elif (pid == "F5" and not state.get("temper", False)) or (
            pid == "F5.5" and not state.get("nyquist", False)
        ):
            icon = f"{DIM}\u2500{RESET}"
            label = f"{DIM}{pid} {pname}{RESET}"
            right = f"  {DIM}skip{RESET}"
        else:
            icon = f"{DIM}\u25cb{RESET}"
            label = f"{DIM}{pid} {pname}{RESET}"
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
        defect_line = f"  {BWHITE}Defects:{RESET} {BYELLOW}{open_d} open{RESET}  {BGREEN}{fixed_d} fixed{RESET}"
        if regressed:
            defect_line += f"  {BRED}{regressed} regressed{RESET}"
        lines.append(defect_line)

    # Verdicts
    verdicts = _load_json(fdir / "verdicts.json")
    reqs = verdicts.get("requirements", [])
    if reqs:
        verified = sum(1 for r in reqs if r.get("verdict") == "VERIFIED")
        v_bar_len = 15
        v_filled = int((verified / len(reqs)) * v_bar_len) if reqs else 0
        v_bar = f"{BGREEN}{'\u2588' * v_filled}{DIM}{'\u2591' * (v_bar_len - v_filled)}{RESET}"
        lines.append(f"  {BWHITE}Verdicts:{RESET} {v_bar} {verified}/{len(reqs)}")

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
                    stream_icons.append(f"[{GREEN}\u2713{RESET}]{s}")
                else:
                    stream_icons.append(f"[{DIM} {RESET}]{s}")
        if stream_icons:
            lines.append(f"  {BWHITE}Streams:{RESET}  {' '.join(stream_icons)}")

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
        lines.append(f"  {BWHITE}Teams:{RESET}    {BCYAN}{team_str}{RESET}")

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




def _stamp_trace_skip(fdir: Path) -> dict | None:
    """Act on the trace-skip fact the width decision RECORDED. Reports, never decides.

    fallout GI-008 / GI-009 / GI-033 / AC-061 / FR-063 (D-021 / D-035, ruling
    `lead_ruling_gi_033_leaf_moves` item 5).

    Returns the recorded decision, or None when there is nothing to say. When it
    says skip, `.trace-complete` is stamped so the roster is satisfiable — the
    same effect this surface has always had, and the only thing left here.

    THE DECISION IS NOT MADE HERE, WHICH IS THE WHOLE CHANGE. It used to be:
    `guidance.py` imported `width._maybe_skip_trace`, a lifecycle module
    reaching into the verifier set to compute a width-derived answer at DISPLAY
    time. GI-008 names that shape outright — "a FULL versus DELTA decision
    computed inside Foundry-Next" — and GI-009 says the transition that opens
    an INSPECT records the mode and Foundry-Next only reports. So the fact is
    computed by `width._trace_skip_from_width` at the transition, written into
    the `inspect_modes` entry as `trace_skip`, and READ here through
    `foundry_state.current_inspect_mode`.

    A RUN WITH NO RECORDED FIELD STAMPS NOTHING, and that is D-117's direction
    rather than a gap: an entry written before this field existed licenses no
    answer about TRACE's scope, and guessing one is exactly how a resumed
    archive came to auto-stamp `.trace-complete` with TRACE never run.

    THE THREE GUARDS THE OLD FENCE HELD ARE ALL STILL HERE — already stamped,
    not in F2, no run — because they are facts about the moment of the call
    rather than about the width.
    """
    if not fdir.exists():
        return None
    if (fdir / _stream_marker("trace")).exists():
        return None
    if _load_json(fdir / "state.json").get("phase") != "F2":
        return None

    recorded = _recorded_inspect_mode(fdir) or {}
    decision = recorded.get("trace_skip")
    if not isinstance(decision, dict):
        return None
    if not decision.get("skip"):
        return decision

    (fdir / _stream_marker("trace")).write_text(
        f"{now_iso()} cycle=skipped\n"
        f"items_checked=0\n"
        f"items_total=0\n"
        f"coverage=SKIPPED\n"
        f"findings=0\n"
        f"skipped=true\n"
        f"reason={decision.get('reason', '')}\n",
        encoding="utf-8",
    )
    return decision




def _leaf_halted_state(fdir: Path) -> dict | None:
    """The run's HALTED record, read from the leaf.

    fallout GI-033 / AC-061 (D-021 / D-035, concern C-027) — `halt.py`'s own
    delegation moved to `gates.py`, where its two verifier callers are, and
    this module may not import a verifier. It reads the same leaf reader
    directly, supplying the same three closed-set values: the HALTED phase
    token, the reason vocabulary and `foundry_state.persisted_max_cycles`. One
    reader, two callers, no second judgement about what HALTED means.
    """
    return halted_state(
        fdir,
        halted_phase=RUN_PHASE_HALTED,
        reason_of=halt_reason,
        max_cycles_of=persisted_max_cycles,
    )




def _open_by_blocking_tier(fdir: Path) -> dict:
    """`{"blocking": int, "live": [ids], "unknown": [ids], "latent": [ids]}`.

    fallout GI-033 / AC-061 / GI-014 (D-021 / D-035, concern C-027) — THE
    COUNT, WITHOUT THE DOOR'S PROSE.
    -------------------------------------------------------------------
    `gates._blocking_defects` answers the same question AND builds the refusal
    the gate returns, and that prose is protocol: it names both filing doors and
    the GRIND phase. Foundry-Next needs none of it — it reads the ids and the
    count and writes its own guidance — so importing the gate for it made this
    lifecycle module reach the verifier set to obtain the two-thirds it throws
    away.

    THIS IS NOT A SECOND COPY OF THE BLOCKING RULE. The rule is
    `vocab.BLOCKING_TIERS`, which is where GI-014's applies-to column always
    said it lives and where C-027 landed it; the ids come from
    `foundry_state.open_defect_ids_by_tier`, the one ledger read. Both halves
    are shared with the gate by construction, so the two answers cannot drift —
    only the sentence differs, and only the gate has one.
    """
    buckets = open_defect_ids_by_tier(
        fdir, tiers=DEFECT_TIERS, unknown_tier=TIER_UNKNOWN, tier_of=defect_tier
    )
    blocking = sum(len(buckets[tier]) for tier in BLOCKING_TIERS)
    return {
        "blocking": blocking,
        "live": buckets["LIVE"],
        "unknown": buckets[TIER_UNKNOWN],
        "latent": buckets["LATENT"],
    }




def _recorded_inspect_mode(fdir: Path) -> dict | None:
    """The width the last INSPECT-opening transition RECORDED, from the leaf.

    fallout GI-033 / AC-061 / FR-063 (D-021 / D-035) — READ FROM THE LEAF,
    NOT FROM THE VERIFIER.
    ---------------------------------------------------------------------
    `Foundry-Next` reports recorded decisions; the server DECIDES at the
    transition. So the width this module prints is a read of `state.json`, and
    `foundry_state.current_inspect_mode` is the one reader of it — casting 10's
    leaf, which every consumer of the recorded width now comes through.
    Importing `width._current_inspect_mode` instead made this lifecycle module
    reach the verifier set, which GI-033 refuses with no exception, and a module
    that can call the width decider is a module that can take one.

    The vocabulary is supplied because a leaf may not know one: `INSPECT_MODES`
    is `schemas/vocab.py`'s, and passing it in is what keeps the set of values
    this HONOURS identical to the set the transitions WRITE.
    """
    return current_inspect_mode(fdir, modes=INSPECT_MODES)




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

    # fallout CT-007 / AC-028 / OT-026 — A HALTED RUN ISSUES NO DISPATCH.
    #
    # Checked before anything else, including the active-teams branch: a run
    # that hit its cycle cap is over, and the next thing to do is read the
    # report, not start another wave. Emitting the ordinary phase guidance here
    # would send the lead round the loop the cap just stopped.
    if phase == RUN_PHASE_HALTED:
        blocking = _open_by_blocking_tier(fdir)
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
        halted = _leaf_halted_state(fdir) or {}
        report_path = fdir / REPORT_MD_FILENAME
        report_present = report_path.exists()
        report_error = halted.get("halted_report_error", "")
        # fallout FR-019 / CT-004 / CT-007 (D-103) — THE NORMALISED SENTENCE IS
        # WHAT AN OPERATOR READS, AND IT WAS COMPUTED AND THEN DISCARDED.
        #
        # `_leaf_halted_state` folds BOTH persisted shapes — this release's
        # `{"reason": <member>, "text": <the lead's words>}` and every earlier
        # archive's bare f-string — through `vocab.halt_reason` into one
        # sentence, and this branch then interpolated `state["halted_reason"]`
        # RAW beside it. Driven on a halted run: the instruction read
        # "Run HALTED at cycle ? — {'reason': 'lead_ruling', 'text': 'the lead
        # stopped it'}." and `details.halted_reason` was the dict, which
        # `display.py#_fmt_foundry_next_lines` prints verbatim. Both surfaces a
        # lead actually reads rendered a Python dict repr as prose, and the
        # cycle rendered as "?" beside it because the raw read reached for a key
        # the leaf had already resolved.
        #
        # Read from `halted` on BOTH surfaces now, so the reader that knows how
        # to read the field is the only one that does.
        halted_cycle = halted.get("halted_at_cycle")
        halted_sentence = halted.get(
            "halted_reason", "the configured cycle cap was reached"
        )
        halted_member = halted.get("halted_reason_member", "")
        # fallout US-006 (D-147) — ONE SPELLING OF THE HAND-OFF, and the cap
        # RAISE is part of it only when the cap is what ended the run. Both
        # arms below offered "re-run with a higher --max-cycles" to every
        # ending, which on a `spec_change_required` halt tells the lead to
        # re-run the work the ruling just said is not useful until the spec
        # moves. Said once, so the two arms cannot come to offer different
        # remedies for one ending.
        hand_off = "hand the remaining work to a new run" + (
            ", or re-run with a higher --max-cycles"
            if halted_member == "cap_reached"
            else ""
        )
        return {
            "phase": RUN_PHASE_HALTED,
            "action": "halted",
            "instructions": (
                f"Run HALTED at cycle {halted_cycle if halted_cycle is not None else '?'} — "
                f"{halted_sentence}. "
                "HALTED is NOT DONE: this run stopped with open work. "
                + (
                    f"The report has been generated at {REPORT_MD_FILENAME} and "
                    "names every open defect by tier. Do NOT dispatch another "
                    "wave, do NOT call Foundry-Phase again — read the report "
                    f"and {hand_off}."
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
                        f"the generator could not render — and {hand_off}."
                    )
                )
            ),
            "details": {
                "halted_at_cycle": halted_cycle,
                "halted_reason": halted_sentence,
                # fallout FR-019 / CT-005 (D-147) — the MEMBER beside the
                # sentence, because the sentence is for a human and the member
                # is what the two lead-facing surfaces below key on. Published
                # rather than re-derived at each of them: a grouper reading
                # `halted_reason` prose to decide which of the four endings this
                # was is how a run's ending gets reclassified by a reader, which
                # is the thing `vocab.halt_reason` refuses to do.
                "halted_reason_member": halted_member,
                # D-225: `persisted_max_cycles`, the one read, so this display
                # cannot state a cap the halt did not act on.
                "max_cycles": persisted_max_cycles(state),
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
    blocking = _open_by_blocking_tier(fdir)
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
                "Call Foundry-Gate(phase='validate'), then Foundry-Phase(phase='start_cast'). "
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
                    "Each stream records its OWN run with Foundry-Stream (fallout "
                    "GI-016) — confirm the record exists when the stream reports; "
                    "do not record on its behalf."
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
        f2_mode = _recorded_inspect_mode(fdir) or {}
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
            f3_mode = _recorded_inspect_mode(fdir) or {}
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
                "Foundry-Team-Down, then Foundry-Gate(phase='inspect_start') — "
                "the gate that guards this crossing, and NOT "
                "Foundry-Gate(phase='inspect'), which guards the F1 entry and is "
                "refused from F3 — then Foundry-Phase(phase='inspect_start') to "
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

        # P3 (FR-003 / FR-004 / ST-001): the auto-pass path. ``.prove-complete``
        # stores only aggregate counts, so verdicts.json may be empty (or
        # partial) even after a clean PROVE — which would make the DONE gate's
        # verdict_coverage read 0/N and block the transition it just enabled.
        # On a clean PROVE, synthesize a VERIFIED verdict for every spec
        # requirement ID BEFORE emitting the auto-pass so the two gates agree.
        #
        # fallout GI-001 / NFR-001 (D-070) — HOISTED ABOVE THE COUNTS, BECAUSE
        # THE COUNTS ARE WHAT IT CHANGES.
        #
        # This stood BELOW the `non_verified > 0` return, which is the same
        # guard `non_verified == 0` states here, so the set of runs it acts on
        # is unchanged. What changes is that the counts are re-taken afterwards,
        # so the branch below can tell an EMPTY ledger a clean PROVE has just
        # filled from one nothing has written to at all. A ledger with
        # non-VERIFIED rows is still not topped up: ASSAY recorded those
        # failures, and marking the requirements it never reached VERIFIED on
        # PROVE's word is the auto-pass reaching past the state it is for.
        if non_verified == 0 and _prove_is_clean(fdir, project_root):
            _synthesize_clean_prove_verdicts(
                fdir, project_root, cycle=current_cycle(fdir)
            )
            verdicts = _load_json(fdir / "verdicts.json")
            requirements = verdicts.get("requirements", [])
            non_verified = sum(
                1 for r in requirements if r.get("verdict") != "VERIFIED"
            )
            total = len(requirements)

        # fallout GI-001 / NFR-001 (D-070) — AN EMPTY VERDICT LEDGER IS "ASSAY
        # HAS NOT RUN", NOT "ASSAY PASSED".
        # ---------------------------------------------------------------------
        # `non_verified` is a sum over the SAME list `total` counts, so an empty
        # ledger makes both zero and the `non_verified > 0` test below falls
        # straight through to the auto-pass tail. On ENTERING F4 that is the
        # ordinary state — no assayer has run yet — and Foundry-Next answered
        # "ASSAY passed: all requirements verified" and told the lead to
        # transition onward. The lead protocol is "call Foundry-Next after each
        # step and follow it", so the guidance engine instructed the lead to
        # skip ASSAY: driven with `state.phase` F4 and verdicts.json absent,
        # `--temper` runs entered F5 having recorded zero verdicts, and
        # `Foundry-Gate('temper')` passed because it reads the same empty
        # ledger. Only `Foundry-Gate('done')` caught it, one whole phase later.
        #
        # THE ZERO-DENOMINATOR CASE IS ITS OWN ANSWER, and it is the one
        # `_ACTION_IMPERATIVES["run_assay"]` was written for — an imperative no
        # branch of this function emitted, which is how a missing branch shows
        # up in a table that is complete (FR-035 / AC-054). It is asked AFTER
        # the auto-pass above, because a ledger that clean PROVE has just filled
        # is no longer empty and must not be answered with "go and run ASSAY".
        if total == 0:
            return {
                "phase": "F4",
                "action": "run_assay",
                "instructions": (
                    "ASSAY has NOT run: verdicts.json records 0 verdicts and "
                    "PROVE has not been recorded clean, so there is nothing "
                    "yet to pass. Spawn 4 parallel foundry:assayer agents in a "
                    "SINGLE message — each reads the spec FIRST, forms "
                    "expectations, then reads code — and record every verdict "
                    "with Foundry-Verdict. An empty verdict ledger is not a "
                    "passing ASSAY: transitioning onward from here leaves F4 "
                    "with zero requirements verified and the failure is not "
                    "caught until the DONE gate, a whole phase later."
                ),
                # No `agent_config`: `foundry:assayer` holds its own opus /
                # effort=max frontmatter pin, so this site emits nothing and
                # lets the pin govern — the same reason `_nyquist_transition`
                # gives for the auditor.
                "details": {"non_verified": 0, "total": 0},
            }

        if non_verified > 0:
            return {
                "phase": "F4",
                "action": "assay_failed_loop_back",
                "instructions": (
                    f"ASSAY found {non_verified}/{total} non-verified requirements. "
                    "Sync findings as defects (Foundry-Sync), "
                    "call Foundry-Gate(phase='grind') then "
                    "Foundry-Phase(phase='assay_fail') — the ASSAY-rejection door "
                    "into F3, which clears every stream marker and is bounded by "
                    "the same --max-cycles cap `grind_start` is. "
                    "Fix defects, then FULL INSPECT, then ASSAY again. "
                    "NO SPOT CORRECTIONS \u2014 the entire verification stack re-runs."
                ),
                "details": {
                    "non_verified": non_verified, "total": total,
                    "agent_config": GRIND_AGENT_CONFIG,
                },
            }

        temper = state.get("temper", False)
        if temper:
            return {
                "phase": "F4",
                "action": "transition_to_temper",
                "instructions": (
                    "ASSAY passed: all requirements verified. --temper is set, "
                    "so F4 routes to F5. Call Foundry-Gate(phase='temper'), then "
                    "Foundry-Phase(phase='temper') — that call is what enters "
                    "F5, records TEMPER's own INSPECT at FULL width and sweeps "
                    "the evidence corpus. Editing state.json to F5 by hand "
                    "leaves the phase's first INSPECT with no recorded mode. "
                    "Then run TEMPER micro-domain stress testing."
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




# --- Context reload ---


def foundry_get_context(
    project_root: str = ".",
    *,
    caller: str = LEAD_CALLER,
) -> dict:
    """Return all foundry state in one call. Use after compaction or session start.

    fallout FR-055 / FR-034 / AC-053 (D-089, fallout_of D-047) — THIS DOOR TAKES
    THE CALLER TOO, BECAUSE IT ARMS THE SAME TOKEN.
    ------------------------------------------------------------------------
    FR-055 is one sentence about a distinction, not about a tool: "sub-agent
    Foundry-Next reads are distinguished ... SO ONLY THE LEAD'S CALL ARMS
    `.next-action-called` AND RESETS `.last-next-at`". D-047 added `caller` to
    `Foundry-Next` and this door kept calling `foundry_next_action(project_root,
    _arm_stall_clock=False)` with no caller at all — so `caller` took its
    default, `is_lead` was True, and a SUB-AGENT's `Foundry-Context` armed the
    ordering token on the lead's behalf. That token is the sole precondition of
    `gates.py#foundry_gate` and `transitions.py#foundry_mark_phase_complete`, so
    a lead could gate and transition having never called Foundry-Next.

    Not hypothetical: `agents/assayer.md` and `skills/prove/SKILL.md` both
    instruct the sub-agent to call `Foundry-Context` at F2 to read
    `state.temper`. This run's own artifacts carry the signature — a
    `.next-action-called` stamp 19 seconds newer than `.last-next-at`, which is
    the asymmetric shape only the `_arm_stall_clock=False` path can write.

    ``_arm_stall_clock`` stays False for the reason AC-035 gives — a read-only
    reorientation call is not a Foundry-Next and must not restart the stall
    measurement — and is a different question from WHO called.
    """
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
    next_act = foundry_next_action(
        project_root, caller=caller, _arm_stall_clock=False
    )

    return {
        "initialized": True,
        "state": {
            "phase": state.get("phase", "unknown"),
            "cycle": current_cycle(fdir),
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


# --------------------------------------------------------------------------- #
# fallout GI-033 / AC-061 / FR-063 (D-021 / D-035) — MOVED HERE FROM
# `orchestration/width.py`, WHERE THEY WERE LODGERS.
#
# `width.py` is in the VERIFIER set and this module is not, so importing them
# from there was a lifecycle-to-verifier edge — the direction GI-033's violation
# column refuses with NO exception — excused by an allowlist row rather
# than removed. Neither is a width fact: "is the lead waiting on live agents or
# deliberating" is the watchdog line this module prints, and the threshold is
# how long a gap has to be before it prints it. Their SOLE consumer is
# `_compute_next_action` below, so they move to it and the edge goes with them.
#
# The move also closes ('width','teams'): `_waiting_on_agents` was the only
# reason the verifier-set width module reached `orchestration/teams.py` for the
# team scan, and reaching it from HERE is lifecycle-to-lifecycle, which the rule
# has never had anything to say about.
# --------------------------------------------------------------------------- #

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


# --------------------------------------------------------------------------- #
# fallout FR-003 / FR-004 / ST-001, and GI-033 / AC-061 (D-021 / D-035) —
# CLEAN-PROVE VERDICT SYNTHESIS, MOVED HERE FROM `orchestration/gates.py`.
#
# `gates.py` declared it and never called it; `_compute_next_action` below is
# its only caller in the tree, so every reach for it was a lifecycle module
# importing a verifier module — the direction GI-033 refuses with no exception.
# It is also a WRITER, which is the other reason it could not go to a leaf
# instead: it opens a `_document_transaction` on verdicts.json. Its one caller
# is here, so it is here.
# --------------------------------------------------------------------------- #

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

    fallout GI-033 / AC-061 (D-021 / D-035, concern C-027) — IT LIVES WITH ITS
    ONE CALLER. It was declared in `gates.py`, which never called it, and
    imported from here — a lifecycle module reaching the verifier set, which
    GI-033 refuses outright. The ids come from `artifacts._spec_requirement_ids`,
    the leaf ladder casting 7 owns: `sorted(...)` is not a rule, so reading the
    leaf directly is one spelling of the ladder rather than a second copy of it.
    """
    ids = sorted(_spec_requirement_ids(project_root)[1])
    if not ids:
        return 0
    verdicts_path = fdir / "verdicts.json"
    now = now_iso()
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
