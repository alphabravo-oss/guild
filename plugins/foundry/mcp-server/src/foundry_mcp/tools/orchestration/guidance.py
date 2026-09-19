"""Foundry-Next and Foundry-Context: what the lead does next.

Survey blocks D, EE and GG. Every response names the terminal state the run
is heading for, the backlog by tier, and the cycles left before the cap.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import NamedTuple

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
    REQUIREMENT_ID_RE,
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
    read_jsonl,
)
from pathlib import Path
from foundry_mcp.tools.orchestration.escalation import (
    ESCALATION_CYCLES,
    ESCALATION_FILENAME,
    _escalated_classes,
    _escalation_exit_distances,
    _override_instruction,
    _override_offer,
    _override_report,
    _persisted_escalations,
)
from foundry_mcp.tools.orchestration.streams import (
    _check_streams_complete,
    _prove_is_clean,
)
from foundry_mcp.tools.orchestration.teams import (
    SERVED_LIST_IS_ONE_MOVE,
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
            "--nyquist is set, so the run enters F5.5 (NYQUIST): regression "
            "tests for VERIFIED requirements that lack automated coverage."
        ),
        "details": {
            "agent_config": {
                "subagent_type": "foundry:nyquist-auditor",
                "description": "NYQUIST: regression tests for VERIFIED requirements",
            },
            "batch_size": 5,
        },
    }


def _post_assay_crossing(
    fdir: Path, phase: str, name: str, gate: str, grind_config: dict,
) -> dict | None:
    """The crossing out of F5 or F5.5, or ``None`` while the phase's own list
    (`run_temper` / `run_nyquist`) is still owed.

    lead-stalls GI-008 / FR-007 (D-050) — both arms returned their phase's
    work unconditionally and left the exit in the CONTEXT ("When clean, call
    Foundry-Gate(phase='done'), update to F6."), so the Foundry-Next owed after
    that list re-served the same list and no run with --temper or --nyquist
    reached F6 by following the served steps.

    WHAT SAYS THE WORK IS FINISHED. Each phase's list ends in ``gate``, the
    gate out of the phase, and a passing evaluation records it in
    `.gate-passed`, which every transition unlinks — so a record naming
    ``gate`` was written by the server in THIS phase, after the list's work.
    The ledgers cannot say it: a TEMPER pass that filed nothing leaves them
    exactly as ASSAY did, `temper` is not a stream wire id, and F5's recorded
    INSPECT roster names five streams no F5 list dispatches. Blocking defects
    are read FIRST, because a gate that passed before TEMPER filed does not
    make what it filed go away.

    The crossings are the existing lists, never a restatement: GRIND is
    `transition_to_grind` (`grind_start` has no source-phase rung, and the
    F5 / F5.5 hints in `transitions.py` name it), F5.5 is `_nyquist_transition`,
    and F6 is `transition_to_done`, whose `done` token is accepted from F5 on a
    --temper run and from F5.5 on a --nyquist one. D-048's advance notice then
    points past the gate the record names. The CONTEXT names no sequence.
    ``grind_config`` is the router's `GRIND_AGENT_CONFIG`, a local of
    `_compute_next_action`, passed so the two GRIND crossings carry one config.
    """
    blocking = _open_by_blocking_tier(fdir)
    if blocking["blocking"] > 0:
        return {
            "phase": phase,
            "action": "transition_to_grind",
            "instructions": (
                f"{name} left {blocking['blocking']} blocking defect(s) open "
                f"({len(blocking['live'])} LIVE, "
                f"{len(blocking['unknown'])} untiered). GRIND fixes them, and "
                "the run comes back through INSPECT and ASSAY."
            ),
            "details": {
                "open_defects": blocking["blocking"],
                "live_defects": blocking["live"],
                "unknown_tier_defects": blocking["unknown"],
                "latent_backlog": blocking["latent"],
                "agent_config": grind_config,
            },
        }
    marker = fdir / GATE_PASSED_MARKER
    passed = read_document(marker)[0].get("phase") if marker.exists() else None
    if passed != gate:
        return None
    finished = (
        f"{name} is finished: the gate out of {phase}, `{gate}`, passed in "
        f"{phase} and no blocking defect is open."
    )
    if gate == "nyquist":
        return {**_nyquist_transition(phase), "instructions": finished}
    return {
        "phase": phase,
        "action": "transition_to_done",
        "instructions": finished,
        "details": {},
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
    elif PHASE_NAMES.get(str(state.get("phase"))) == "DONE":
        # fallout D-232 — a run already in F6 has reached DONE, whatever the
        # cap arithmetic says about a GRIND it will never open.
        heading_for = "DONE"
    elif cycles_to_cap == 0 and _open_by_blocking_tier(fdir)["blocking"] > 0:
        # The next GRIND door would seal HALTED, and that is what the run is
        # heading for even though nothing has stopped yet. Saying so BEFORE the
        # door is the whole point of the field.
        #
        # fallout D-232 — AND ONLY WHILE THERE IS BLOCKING WORK TO OPEN ONE.
        # The cap seals at a GRIND door and nowhere else, and a GRIND opens for
        # blocking defects (a failing ASSAY files its own). Keyed on the cap
        # alone, a run converging on its last allowed cycle was told HALTED
        # through its final INSPECT and ASSAY, in the same payload that told it
        # to transition to DONE.
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
                    # Rendered below, once the steps it points into exist.
                    gate_advance_note = expected_gate

    # lead-stalls FR-005 / FR-015 / CT-003 / CT-006 — THE ROSTER IS READ
    # ONCE, FOR
    # THE TWO ACTIONS WHOSE IMPERATIVE DEPENDS ON IT.
    #
    # Until now `_waiting_on_agents` was consulted ONLY inside the stall
    # watchdog below, which fires only past `STALL_NOTICE_SECONDS` — so for the
    # first three minutes of every wave the server held the answer to "are my
    # teammates running" and never asked itself. lead-stalls FR-005's complaint
    # is what that
    # made `Foundry-Next` worth: a lead that re-called it during a wave got the
    # same frozen conditional back and learned nothing, which is a no-op dressed
    # as guidance.
    #
    # Read HERE and passed BOTH ways: down to `_format_imperative_header`, which
    # picks the arm, and into the watchdog, which would otherwise take a second
    # reading of the same roster and could disagree with the one the lead was
    # just given. Published under its own key rather than folded into
    # `waiting_on_agents`, which means something narrower and older — "the
    # watchdog fired and found agents progressing" — and whose ABSENCE four
    # tests read as "no stall notice was emitted".
    #
    # Scoped to the BRANCHED actions (lead-stalls D-019 made `run_streams` the
    # third, D-056 `add_castings` the fourth) and to the phases the router
    # itself now reads it for, so the rest cost no ledger scan (lead-stalls
    # GI-001). `_waiting_on_agents` never
    # raises and never blocks (lead-stalls CT-006), so this cannot take
    # `Foundry-Next` down with it.
    #
    # lead-stalls D-015 / D-016 / D-017 — AND WHEN THE ROUTER ALREADY READ IT,
    # THAT READING IS THE ONE. `_compute_next_action` now reads the roster in
    # F1 and F3 to decide whether a registered team is still holding work, and
    # a second reading here could disagree with the one that chose the arm.
    agent_liveness: dict | None = result.get("agent_liveness")
    if agent_liveness is None and _parse_branches(
        _ACTION_IMPERATIVES.get(result.get("action", ""), "")
    ):
        agent_liveness = _waiting_on_agents(project_root)
        result["agent_liveness"] = agent_liveness

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
                    # lead-stalls FR-005 — the reading taken above when this
                    # action has one, so the notice and the imperative
                    # printed beside it can never be two different answers
                    # about one roster.
                    liveness = (
                        agent_liveness if agent_liveness is not None
                        else _waiting_on_agents(project_root)
                    )
                    minutes = int(delta // 60)
                    seconds = int(delta % 60)
                    if liveness["waiting"]:
                        result["waiting_on_agents"] = liveness
                        # lead-stalls FR-006 / GI-004 / OT-003 — THE TAIL
                        # THAT TOLD THE LEAD TO WAIT AND THEN RE-ISSUE THE
                        # GUIDANCE CALL IS A POLL, AND IT CONTRADICTED THE
                        # IMPERATIVE PRINTED BELOW IT IN THE SAME PAYLOAD.
                        #
                        # One `Foundry-Next` response carried both that line
                        # and a `build_castings` arm forbidding the very
                        # same call while waiting, on the grounds that it
                        # would re-emit the action. FR-006 requires the two
                        # be reconciled and GI-004 decides the direction: no
                        # surviving text instructs a sleep, a poll or a wait
                        # loop, so this arm yields and QUOTES the one
                        # spelling of the policy rather than wording it a
                        # third time.
                        #
                        # lead-stalls GI-008 (D-019) — AND IT SAYS SO ONLY
                        # WHEN THE IMPERATIVE BELOW SAYS THE SAME. This arm
                        # appended the policy for EVERY action, and only the
                        # branched ones choose their imperative from this
                        # reading: driven with a team registered it printed
                        # "END YOUR TURN" directly above `cleanup_teams`, and
                        # at F2 above "spawn every missing INSPECT stream" —
                        # one payload, two imperatives. The notice now asks
                        # `_chosen_branch`, the selection the header is
                        # printed from; anywhere else it REPORTS the roster
                        # and names no move, so the header stays the one.
                        told_to_wait = (
                            _chosen_branch(result.get("action", ""), liveness)
                            == "live"
                        )
                        stall_warning = (
                            f"\u23f3 WAITING ON {liveness['count']} AGENT(S) "
                            f"({liveness['detail']}). {minutes}m {seconds}s since "
                            f"your last Foundry-Next call — that gap is the "
                            f"agents working, not you deliberating. "
                            + (
                                "Do NOT improvise over their half-finished "
                                "work. Foundry-Liveness answers per-agent "
                                "detail. " + _WAITING_IS_NOT_STOPPING
                                if told_to_wait
                                else "Foundry-Liveness answers per-agent "
                                "detail. " + _WAITING_REPORTS_ONLY
                            )
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
    next_calls, imperative_header = _emitted_imperative(
        action, result.get("details", {}),
        run_name=run_name_for_imperative,
        # fallout D-058 — the phase the action was emitted FROM, which is the
        # other half of the question for `transition_to_inspect`. It is already
        # on the result, so this reads the router's own answer rather than
        # re-deriving one that could disagree with it.
        phase=str(result.get("phase", "")),
        # lead-stalls FR-015 / CT-008 — the roster reading taken once above.
        # PASSED,
        # never re-measured here: the arm the lead is given and the
        # `agent_liveness` it can read on the same payload have to be one
        # answer, and two readings of a moving roster are not.
        liveness=agent_liveness,
        # lead-stalls D-012 — the server-owned cycle counter, read the same way
        # and at the same place as the run slug beside it. `current_cycle` is
        # total and never raises, so this cannot take Foundry-Next down and
        # cannot hand an entry an unresolvable `{cycle}`.
        cycle=(
            current_cycle(fdir_stall)
            if fdir_stall and fdir_stall.exists() else None
        ),
    )

    # lead-stalls GI-008 / OT-013 (D-038..D-043) — THE STEPS THE HEADER WAS
    # RENDERED FROM, published beside it. `None` only where the header is the
    # generic fallback, which names no step list of its own.
    if next_calls is not None:
        result["next_calls"] = [_call_record(step) for step in next_calls]
    if gate_advance_note:
        gate_advance_note = _gate_advance_notice(gate_advance_note, next_calls)

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
        report_owed = (
            (result.get("details") or {}).get("report_generated") is False
        )
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
            # fallout AC-022 / GI-014 (casting 10's concern C-077) — EVERY
            # TIER, because this file ASSERTS the sentence in its own voice
            # rather than quoting a locked requirement. `DEFECT_TIERS` has three
            # members and HARDENING is non-blocking by design, so this notice
            # and the halt seal are among the very few surfaces its records
            # reach the lead at all. Derived from the vocabulary, so a fourth
            # tier cannot be dropped the same way.
            #
            # lead-stalls GI-008 / FR-007 — and only when it WAS. A halt that
            # could not write its report is served `Foundry-Report` as its one
            # step (`_UNWRITTEN_REPORT`); this line asserted the report beside
            # that step, and the tail below said nothing asked the lead on.
            + (
                "\n- The halt could not write its report, which names every "
                "open " + ", ".join(sorted(DEFECT_TIERS)) + " and untiered "
                "defect once it exists, so the list below holds the one call "
                "that writes it. " + SERVED_LIST_IS_ONE_MOVE
                if report_owed else
                "\n- The report has been generated as part of the halt and "
                "names every open " + ", ".join(sorted(DEFECT_TIERS))
                + " and untiered defect. Read it."
            )
            # fallout US-006 (D-147): the cap RAISE is a remedy for exactly one
            # of the four endings. Offering it on a `spec_change_required` halt
            # tells the lead to re-run the work the ruling just said is not
            # useful until the spec moves — the cause claim above, said as
            # advice.
            + "\n- Hand the remaining work to a new run"
            + (
                ", or re-run with a higher --max-cycles"
                if (result.get("details") or {}).get("halted_reason_member")
                == "cap_reached"
                else ""
            )
            + (
                ". Nothing below asks you to go past that one call."
                if report_owed else
                ". Nothing below asks you to keep going."
            )
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
#
# lead-stalls D-044 / D-045 — IT IS A READ INSIDE A MOVE, NOT A MOVE. Every
# gate-then-phase pair sits inside a served list, and the rules block now says
# that list is one move (`_A_SERVED_LIST_IS_ONE_MOVE`). A Foundry-Next taken
# here answers with the same list from step (1), so "call it there when you
# want them" read as licence to restart the list; the sentence now says its
# answer replaces nothing and names the step that still comes next.
#
# lead-stalls GI-008 (D-048) — THE STEP AFTER THE READ IS THE LIST'S, NOT
# ALWAYS A Foundry-Phase. This said "the step after it is still that list's
# Foundry-Phase", true on the ten lists whose gate and phase are adjacent and
# false on `transition_to_done`, whose gate is followed by the strip and its
# commit. Driven, a lead obeying it sealed F6 with the corpus still committed:
# the note and the list gave two next calls. One sentence serves every list, so
# it names the step by its place in the list rather than by its tool, and the
# gate advance notice in `foundry_next_action` names the concrete step.
_GATE_THEN_PHASE_EXCEPTION = (
    "Foundry-Next straight after a passing Foundry-Gate is OPTIONAL — the "
    "gate no longer consumes the ordering token, so the list's Foundry-Phase "
    "is accepted without it. It is a read and not a move: that is where the "
    "INSPECT mode (its width) and the rule that fired are announced, its "
    "answer never replaces the list you are making, and the step after it is "
    "still the step your list numbers after that Foundry-Gate. Never call it "
    "to satisfy the protocol."
)



_GATE_THEN_PHASE_NOTE = "\n" + _GATE_THEN_PHASE_EXCEPTION



#: lead-stalls GI-008 / ST-003 / ST-004 / US-003 (D-039) — THE SPAWN-PROMPT
#: ORDER, DECLARED ONCE.
#:
#: The standing rule said every spawn passes the `dispatch` block and then,
#: LAST, the `progress_protocol` block (D-035). Three of the five dispatch
#: steps printed under that rule still said the dispatch block alone:
#: `transition_to_cast` (6), `build_castings`/undispatched (4) and
#: `fix_defects`/idle (6). Each payload therefore carried two answers to "what
#: is the Agent prompt", and a lead obeying the step spawned a teammate that
#: was never told its ledger. Driven, that parked a CAST wave (END YOUR TURN
#: over nothing, then a re-dispatch of a wave already accepted) and left a
#: GRIND cycle reading as live over no running agent.
#:
#: Five hand-typed copies of one order is how three of them went stale, so the
#: order is DATA here: each phase names its blocks, each block has one label
#: and one description, and `_spawn_prompt` renders both the rules block's
#: sentence and every spawn step's prompt clause. The two cannot disagree,
#: because they are one rendering of one tuple.
_PROMPT_BLOCKS: dict[str, tuple[str, str]] = {
    "dispatch": (
        "dispatch",
        "the returned `dispatch` field VERBATIM, which names the prompt FILE "
        "and the sha256 the teammate must read that file to obtain (the "
        "`prompt` field is null by default and is NOT what you pass)",
    ),
    "grind_cycle_context": (
        "cycle_context",
        "the returned `grind_cycle_context` block VERBATIM (the files earlier "
        "cycles changed; a response without one adds nothing here)",
    ),
    "defects": (
        "defects",
        "the defect list, in a '## Defects to fix this cycle:' block",
    ),
    "alignment_block": (
        "alignment",
        "that task's `alignment_block` from the Foundry-Tasks result, "
        "VERBATIM — server-generated, it names the originating defects, the "
        "requirement ids, the owning casting and file of the fix, and the "
        "sibling files this casting owns that cite those ids, so never "
        "summarise it and never compose your own (a task without one adds "
        "nothing here)",
    ),
    "progress_protocol": (
        "progress_protocol",
        "LAST, the returned `progress_protocol` block VERBATIM, which is what "
        "makes the teammate write its progress ledger and its done line",
    ),
}

_SPAWN_PROMPT_ORDER: dict[str, tuple[str, ...]] = {
    "cast": ("dispatch", "progress_protocol"),
    "grind": (
        "dispatch", "grind_cycle_context", "defects", "alignment_block",
        "progress_protocol",
    ),
}


def _spawn_prompt(blocks: tuple[str, ...]) -> str:
    """The prompt clause for a spawn passing ``blocks``, in their order."""
    listed = "; ".join(
        f"({chr(ord('a') + index)}) {_PROMPT_BLOCKS[block][1]}"
        for index, block in enumerate(blocks)
    )
    order = " → ".join(_PROMPT_BLOCKS[block][0] for block in blocks)
    return (
        "its prompt is these blocks, each BELOW the one before and never "
        f"inside another: {listed}. Order: {order}."
    )


#: lead-stalls FR-015 / US-003 / ST-004 / CT-003 (D-038) — A SPAWN DOOR AND
#: ITS AGENT CALL ARE ONE MOVE.
#:
#: Both spawn doors write each teammate's first ledger line
#: (`{"step": "dispatched", "seeded_by": "server"}`) before they return, and a
#: fresh line reads as a progressing agent. A lead that called Foundry-Next
#: between the door and its Agent call — which the standing "call Foundry-Next
#: after each step" told it to — was answered END YOUR TURN with no Agent
#: running: a CAST wave, a GRIND cycle and a refused casting's re-dispatch all
#: parked that way when driven.
#:
#: THE READING SIDE CANNOT SEPARATE THE TWO STATES, WHICH IS WHY THE REMEDY IS
#: THIS SENTENCE. A seed-only ledger is both "door returned, no Agent yet" and
#: "Agent spawned, still reading its prompt file", and nothing this server
#: records tells them apart: the seed is identical in both, and the Agent call
#: leaves no trace a guidance read can see. Answering the seed with "spawn it
#: now" would duplicate EVERY spawn on the ordinary path, where the lead calls
#: Foundry-Next seconds after its Agent call and before the teammate's first
#: line. So the reading keeps a fresh seed as running, and this makes that
#: true: the lead never stands between the door and the Agent, and the step
#: after the spawn is the served list's next step — END YOUR TURN, or the
#: `redispatch` list's SendMessage (D-045) — never a Foundry-Next. Quoted by the
#: rules block and by every spawn step, per the `_GATE_THEN_PHASE_EXCEPTION`
#: discipline.
_SPAWN_IS_ONE_MOVE = (
    "A spawn door (Foundry-Cast-Wave, Foundry-Spawn-Teammate) and the Agent "
    "call its answer feeds are ONE move: the Agent call comes straight after "
    "the door, with no Foundry-Next between them, because the door has "
    "already written each teammate's first ledger line and a Foundry-Next "
    "taken in between reads that teammate as running while no Agent exists."
)

#: lead-stalls GI-008 / FR-015 / CT-003 / US-003 (D-044, D-045) — THE WHOLE
#: SERVED LIST IS ONE MOVE, THE SPAWN PAIR ABOVE BEING ONE CASE OF IT.
#:
#: The rules block read "Call Foundry-Next after each step and follow it", and
#: a Foundry-Next answers from the run state, not from how far through a list
#: the lead has got. Driven at 373e2d1, three lists went wrong on it: the CAST
#: wave-complete and GRIND dispatch lists were served again from step (1)
#: after their Team-Down, so Foundry-Gate and Foundry-Tasks were never
#: reached; the refused casting's re-dispatch was answered END YOUR TURN after
#: its Agent call, so its SendMessage never went; and the GRIND dispatch was
#: answered `cleanup_teams` after its Team-Up, tearing down the team it had
#: just registered.
#:
#: The reading side cannot tell "step 2 of 8 made" from "nothing made yet" —
#: it has the same files either way — so, as for the spawn pair, the remedy is
#: the sentence: Foundry-Next comes after the list's last step. The
#: gate-then-phase read is the one Foundry-Next a list tolerates inside it,
#: and `_GATE_THEN_PHASE_EXCEPTION` says its answer replaces nothing.
#:
#: The sentence itself is `teams.py#SERVED_LIST_IS_ONE_MOVE`, because
#: Team-Down's `next_call` — a tool answer received mid-list — quotes it too,
#: and this module imports that one, never the reverse. Only the spawn clause,
#: which points at the rule printed beneath it here, is added on this side.
_A_SERVED_LIST_IS_ONE_MOVE = (
    SERVED_LIST_IS_ONE_MOVE
    + " A spawn door and the Agent call it feeds are one step of that move "
    "(the spawn rule below)."
)

#: The rules block's spawn sentence, rendered from the same declaration.
_SPAWN_PROMPT_RULE = (
    "Every spawn, CAST and GRIND, passes Agent exactly these blocks. CAST: "
    + _spawn_prompt(_SPAWN_PROMPT_ORDER["cast"])
    + " GRIND: "
    + _spawn_prompt(_SPAWN_PROMPT_ORDER["grind"])
)




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
    # lead-stalls D-038 — and a spawn door with its Agent call is ONE step,
    # which is what the spawn rule below says in full.
    # lead-stalls D-044 / D-045 — "Call Foundry-Next after each step" is gone:
    # the whole served list is the move, and the one Foundry-Next inside it is
    # the gate-then-phase read, which changes no step.
    "\n- NEVER stop between phases. " + _A_SERVED_LIST_IS_ONE_MOVE
    + " The one Foundry-Next a list tolerates between two of its steps is "
    "the gate-then-phase read: " + _GATE_THEN_PHASE_EXCEPTION
    + " Skipping it there is correct and is not a shortcut."
    "\n- NEVER deliberate for more than 30 seconds between tool calls. If you "
    "catch yourself thinking, make the next step of the list you were served, "
    "and call Foundry-Next after its last step."
    "\n- NEVER narrate progress as 'Checkpoint \u2014 X complete', 'Checkpoint reached', 'Milestone \u2014 X', or similar. Foundry has NO checkpoints. You are not a checkpointing orchestrator. Execute the next tool call silently and keep moving."
    "\n- NEVER skip SIGHT because 'no URL.' If frontend files exist, you need a URL. Gate will block."
    "\n- NEVER spawn foundry:teammate agents (CAST or GRIND) with run_in_background=true. They are foreground, TeamCreate-managed, and must run through Foundry-Cast-Wave or Foundry-Spawn-Teammate + verbatim Agent. Background-spawning bypasses the router architecture and breaks spec fidelity."
    # lead-stalls GI-008 / ST-004 / US-003 (D-035) — THE RULE NAMES THE BLOCK
    # EVERY SPAWN PASSES. This read "Pass it to Agent VERBATIM. GRIND is the
    # only exception: append ...", printed on every payload — including above
    # the CAST `redispatch` branch, whose step (2) orders the
    # `progress_protocol` block appended BELOW the dispatch (D-030). The two
    # could not both be obeyed, and a lead obeying this one spawned a teammate
    # that was never told its ledger: its seed line read as a running agent for
    # fifteen minutes, then as an undispatched wave, and re-acceptance was never
    # reached. `commands/start.md` rule 1 and `Foundry-Spawn-Teammate`'s own
    # `instructions` already say the block goes BELOW, LAST, in every phase, so
    # the rule says it too and GRIND's exception is only the defect material.
    #
    # lead-stalls D-039 / D-038 — rendered from `_SPAWN_PROMPT_ORDER` and
    # quoting `_SPAWN_IS_ONE_MOVE`, the two declarations every spawn step
    # renders from too, so this line and the steps under it are one text.
    "\n- NEVER modify, paraphrase, or augment a prompt returned by "
    "Foundry-Spawn-Teammate or Foundry-Cast-Wave. "
    + _SPAWN_PROMPT_RULE + " " + _SPAWN_IS_ONE_MOVE
    + "\n- If the user typed a message, treat it as a directive. Absorb and keep going."
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


#: lead-stalls GI-004 / FR-002 / FR-006 / OT-003 — ONE SPELLING OF THE WAIT
#: POLICY.
#:
#: Three lead-facing surfaces state it: the `build_castings` and `fix_defects`
#: teammates-live branches below, and the stall notice's WAITING arm in
#: `foundry_next_action`. lead-stalls FR-006 exists because the third of those
#: contradicted the first two: it ended by telling the lead to wait and then
#: re-issue the guidance call, which is a poll, while the imperative forbade
#: that same call outright while waiting. A lead reading ONE payload was handed
#: both instructions at once, and they cannot both be followed.
#:
#: Declared once and referenced, per the `_GATE_THEN_PHASE_EXCEPTION` /
#: `_STANDING_CRITICAL_RULES` discipline: this file's documented failure mode is
#: a rule stated in N copies becoming a rule stated N different ways, and
#: `tests/orchestration/test_guidance.py#test_the_optional_rule_has_one_spelling`
#: is the guard that was written the last time it happened.
#:
#: lead-stalls GI-004's violation column is "replacement text that tells the
#: lead to sleep,
#: poll, or loop while waiting", so the sentence names all three and denies
#: them. What replaces the poll is the harness's own completion notification
#: (lead-stalls ST-001 / ST-002): the lead ends its turn, the session idles
#: while the run
#: advances, and the notification re-enters it. That is a WAKE, not a WAIT LOOP,
#: which is why ending the turn here does not contradict the standing "NEVER
#: stop between phases" rule — the lead is not stopping, it is yielding the turn
#: and resuming on an event.
_WAITING_IS_NOT_STOPPING = (
    "Waiting on a running agent is not stopping the run and it is not a "
    "stall: END YOUR TURN, and the harness wakes you with a completion "
    "notification the moment an agent finishes. Do NOT sleep, do NOT poll, do "
    "NOT re-call a tool in a loop to pass the time. When the notification "
    "arrives, call Foundry-Next and follow what it says then."
)

#: lead-stalls GI-008 (D-019) — WHAT THE WAITING NOTICE SAYS WHEN THE
#: IMPERATIVE BENEATH IT WAS NOT CHOSEN FROM THE ROSTER IT REPORTS.
#:
#: The notice exists so a long gap with agents running is not called
#: deliberation (FR-020 / FR-036), and that half holds for every action. The
#: policy above is an IMPERATIVE, though, and appending it to a header that
#: names calls is two imperatives in one payload. So beside any header other
#: than a `live` branch the notice keeps its report and hands the move back.
_WAITING_REPORTS_ONLY = (
    "This notice is a report and names no move: the imperative below is this "
    "run state's one next call."
)


#: lead-stalls FR-006 / GI-004 / US-002 / OT-003 (D-005) — WHAT THE `CONTEXT:`
#: BLOCK OF A BRANCHED ACTION SAYS INSTEAD OF A SECOND SEQUENCE.
#:
#: `foundry_next_action` assembles ONE payload out of two lead-facing surfaces:
#: the imperative header, which `_select_branch` picks from the roster this
#: server read on THIS call, and the `CONTEXT:` block printed directly beneath
#: it, which is whatever `_compute_next_action` put in `instructions`. Only the
#: first is substituted on run state. So a CONTEXT that named the crossing
#: wrote a SECOND, unconditional sequence beside a state-chosen one, and the
#: two disagreed the moment the state moved.
#:
#: They did, on every F1 call. The arm read "CAST phase: teammates are
#: building. Wait for all tasks to complete. When done: shut down team,
#: TeamDelete, Foundry-Team-Down, then Foundry-Phase(phase='cast')." — shipped
#: immediately below an imperative that had just said END YOUR TURN, do NOT
#: poll, do NOT re-call a tool in a loop. Three defects in one block:
#:
#:   1. A bare wait naming no mechanism, which is lead-stalls GI-004's
#:      violation column verbatim and the same shape the Problem Statement
#:      condemns in the entry this run rewrote.
#:   2. "teammates are building" asserted while the roster read on the SAME
#:      call had just measured that none are.
#:   3. A crossing named WITHOUT the `Foundry-Gate(phase='inspect')` that
#:      guards it — the gate the wave-complete branch above had just added.
#:
#: THE RULE: the CONTEXT of a branched action names no next call and no wait.
#: lead-stalls GI-008 / FR-007 (D-051..D-053) extend it to EVERY action the
#: router returns, branched or not: twelve step-list arms still wrote out
#: calls of their own, and three of them disagreed with their header — the
#: ASSAY crossing in the opposite order, the F6 crossing opening on a gate the
#: report it named last must precede, and a filing no step made.
#: It carries what the imperative cannot — the counts and readings this phase
#: measured — and defers the sequence to the one surface that knows the run
#: state. Declared once and appended, per the `_GATE_THEN_PHASE_NOTE` /
#: `_WAITING_IS_NOT_STOPPING` discipline: two hand-typed copies of this is how
#: lead-stalls FR-006's contradiction arose in the first place.
#:
#: Nothing is lost by deferring. The F1 crossing is named by
#: `_CAST_WAVE_COMPLETE`, gate included; the F3 crossing by
#: `_ACTION_IMPERATIVES["transition_to_inspect"]`, which is the action this
#: router returns once the blocking-defect count is zero and no GRIND agent is
#: running (lead-stalls D-017).
_BRANCHED_ACTION_CONTEXT = (
    " Your next call is the imperative printed ABOVE this block: it was chosen "
    "from the progress ledgers this server read on THIS call, so it already "
    "answers whether your agents are still running. This block names no "
    "sequence of its own — a second one here could only disagree with the one "
    "above."
)


# --------------------------------------------------------------------------- #
# lead-stalls FR-015 / GI-008 / CT-008 — AN IMPERATIVE THAT SERVES TWO RUN
# STATES HOLDS
# BOTH TEXTS AND THE SERVER PICKS ONE.
#
# `build_castings` and `fix_defects` were the two entries that handed the LEAD
# the conditional instead: "YOUR NEXT ACTION depends on wave state: - IF ... -
# IF ...". lead-stalls FR-007's defect shape is exactly that — an entry that
# hands the lead a conditional or a judgment task instead of naming a literal
# tool call — and the FR-007 audit over all 22 keys found these two and
# nothing else.
#
# WHY MARKERS INSIDE THE ENTRY RATHER THAN A SIDE TABLE. `{gate}` / `{token}`
# keep their values in `_ACTION_CROSSINGS`, and the obvious mirror would put
# these branch texts in a dict beside it. That would move the most-read
# lead-facing prose in the run OUT of `_ACTION_IMPERATIVES.values()` — the
# population `tests/test_lead_prose.py#_lead_facing_prose` sweeps for denied
# spellings and `tests/test_model_config.py` sweeps for steerable-model claims.
# D-226 is the defect where a scan window was narrower than the rule it stated,
# and it was measured on THIS dict. So the entry holds every branch it can
# emit, both sweeps keep seeing all of it, and only the selection is code.
#
# WHY IT RESOLVES LIKE `{halt_cause}` AND NOT LIKE `{gate}`. An unresolved
# `{gate}` discards the imperative and takes the generic header, which reads
# "Execute the first tool call mentioned. Do not deliberate." — the exact
# conditional-judgment push lead-stalls GI-008 forbids the lead to receive.
# `_select_branch`
# is therefore TOTAL over every input, like `_halt_cause`: there is no liveness
# reading for which a branched entry emits a marker or falls through.
_BRANCH_OPEN = "[[branch:"
_BRANCH_CLOSE = "]]"

#: The branch every branched entry must declare, and the one a state with no
#: text of its own resolves to. `fix_defects` declares `live` and `idle` only:
#: with no agent running it is emitted ONLY while blocking defects are open, so
#: "no agent is running" means dispatch teammates whether they were never
#: spawned or finished with work still open, and one text serves both.
_BRANCH_FALLBACK = "idle"


def _parse_branches(text: str) -> dict[str, str]:
    """Split a branched imperative into ``{state: text}``; ``{}`` if unbranched."""
    if _BRANCH_OPEN not in text:
        return {}
    branches: dict[str, str] = {}
    for chunk in text.split(_BRANCH_OPEN)[1:]:
        name, _, body = chunk.partition(_BRANCH_CLOSE)
        branches[name.strip()] = body.strip("\n")
    return branches


def _branch_state(liveness: object) -> str:
    """Which run state the lead stands in, from `_waiting_on_agents`
    (lead-stalls CT-008).

    TOTAL over every input, including ``None`` and a reading that failed: a
    watchdog that cannot answer must not be able to leave the lead without an
    imperative, which is the same rule `_waiting_on_agents` states about its own
    failure paths.

    `cast_wave_pending` IS THE FIELD THAT ANSWERS, AND `roster_agents` IS ONLY
    ITS FALLBACK (lead-stalls D-009 / D-010).
    ---------------------------------------------------------------------------
    This read `roster_agents` alone, on the argument that a progress ledger is
    written once and stays written so the roster remembers a wave the teardown
    has already unregistered. That argument is sound and the field it chose is
    not: the roster counts EVERY ledger in the run, so it cannot tell "wave N
    finished" from "wave N+1 not started". The two readings are byte-identical
    in it — `{"waiting": False, "roster_agents": >0, "teams_active": *}` — and
    this run stood in the first of them between its own two waves while being
    handed `_CAST_WAVE_COMPLETE`: tear the team down, gate, cross into F2, with
    a third of the castings never built. lead-stalls US-001 requires the lead to
    dispatch the NEXT WAVE in that turn, and lead-stalls FR-015 locks that the
    server substitutes the RIGHT branch; a field that cannot express wave
    position cannot choose it.

    So `_waiting_on_agents` now measures the position and publishes it, and this
    reads that answer: `cast_wave_pending` is the lowest manifest wave holding a
    casting that has not declared itself done, `0` when every wave is done, and
    ABSENT when the manifest could not be read.

    lead-stalls D-013 — AND AN ABSENT POSITION OWES `undispatched`, BECAUSE THE
    ROSTER FALLBACK WAS THE SAME DEFECT WITH THE MANIFEST TAKEN AWAY.
    ---------------------------------------------------------------------------
    The absent case used to fall through to `if row.get("roster_agents"): return
    "idle"` — the pre-change reading, kept on the argument that a non-empty
    roster is right about the one thing it ever knew. It is not, and the
    argument is the one D-009 and D-010 were filed against, restated: a
    non-empty roster cannot tell "wave 1 done, wave 2 never dispatched" from
    "the final wave is done", and for `build_castings` `idle` IS
    `_CAST_WAVE_COMPLETE` — tear the team down, gate, cross into F2. So a
    manifest that did not answer was handed the teardown, and it was handed it
    with TWO wrong results, not one: `_CAST_WAVE_COMPLETE`'s closing sentence
    reads "this server read the manifest and the progress ledgers on this call"
    — the manifest read FAILING is what put the reading on that arm — and
    `{built_wave}` then defaulted to `_CAST_WAVE_DEFAULT`, naming
    `cast-{run}-wave-1` on a run whose last CAST wave was 2.

    THE ASYMMETRY IS WHAT DECIDES IT, and it is not close. A spurious
    `undispatched` costs a redundant `TeamCreate` / `Foundry-Team-Up` that
    answers "already registered" — `_CAST_WAVE_UNDISPATCHED` says so in as many
    words, and step (3) is still where the dispatch begins. A spurious `idle`
    crosses a phase gate over castings nothing built and tears down a team named
    after the wrong wave. One direction wastes a call; the other loses a third
    of the build. A watchdog that cannot answer takes the cheap direction.

    Nothing reads `roster_agents` here any more. It is still measured and still
    published on the result — `_cast_wave_position` needs the roster to place
    the wave at all, and lead-stalls FR-005 puts the count on the payload — but
    it decides no branch. The manifest and the ledgers do.

    lead-stalls D-003 — AND `teams_active` IS NOT READ HERE, BECAUSE A
    REGISTERED TEAM IS NOT DISPATCHED WORK.
    ---------------------------------------------------------------------------
    This line read `roster_agents or teams_active`, and the disjunct resolved
    `{"waiting": False, "roster_agents": 0, "teams_active": True}` to `idle`.
    That reading is the lead standing between `Foundry-Team-Up` and its FIRST
    Agent spawn — between steps (4) and (6) of the `transition_to_cast` sequence
    this same table hands out — so a lead with ZERO castings built was handed
    `_CAST_WAVE_COMPLETE` and told to tear the team down, gate, and cross into
    F2. lead-stalls FR-015 locks "the server substitutes the RIGHT one using
    `_waiting_on_agents` at emission"; the disjunct substituted the wrong one.

    The disjunct was added for the TEARDOWN reading, and `roster_agents` already
    carries that one on its own: the ledger outlives the team. So the disjunct
    only ever contributed the reading it got wrong. A registered team is
    evidence that a TEAM exists; it is never evidence that work was dispatched
    to it, and the ledgers are the only thing that answers that.
    """
    row = liveness if isinstance(liveness, dict) else {}
    if row.get("waiting"):
        return "live"
    # lead-stalls D-009 / D-010 — the wave position when the manifest answered,
    # and only then the roster. `bool` is excluded for `current_cycle`'s reason:
    # `True` is not wave 1, and `False` is not "every wave is done".
    pending = row.get("cast_wave_pending")
    if isinstance(pending, int) and not isinstance(pending, bool):
        return "undispatched" if pending > 0 else "idle"
    # lead-stalls D-013 — a reading that did not MEASURE a wave position owes
    # the dispatch branch, never the teardown. See the docstring above.
    return "undispatched"


def _casting_ids(value: object) -> list[str]:
    """A reading's casting-id list, total over every input (lead-stalls D-020).

    Only a non-empty string or a non-bool int is an id: `True` is not casting
    1, for `current_cycle`'s reason. Anything else answers ``[]``, which reads
    as "nothing owed", so a malformed reading can select neither acceptance
    branch and can never leave a `{casting}` slot to be filled from nothing.
    """
    if not isinstance(value, list):
        return []
    return [
        str(item) for item in value
        if (isinstance(item, str) and item)
        or (isinstance(item, int) and not isinstance(item, bool))
    ]


def _acceptance_state(liveness: object) -> str | None:
    """`refused` / `redispatch` / `unaccepted` when a CAST casting's acceptance
    is owed, else ``None`` (lead-stalls ST-004 / D-020).

    A-014's premise was "Foundry-Next already knows whether the casting was
    accepted", and it did not: `_cast_wave_position` counted a casting built on
    its own ledger `done` line, which the teammate writes BEFORE the lead calls
    `Foundry-Accept-Casting`. So a refused casting read as built, and the
    refusal's `next_call` — "Call Foundry-Next now." on every path, FR-011 —
    sent the lead to tear the wave down (team up: `cleanup_teams`; team down:
    `_CAST_WAVE_COMPLETE`), which is the one thing a refusal never owes.

    The two lists come from `_cast_wave_position`, which reads the verdict
    `foundry_accept_casting` records. `refused` outranks `unaccepted` because
    re-accepting a casting nobody has touched since its refusal only buys the
    same refusal again.

    lead-stalls D-022 — AND A REFUSAL IS TWO STATES, CHOSEN FROM THE SAME
    READING'S TEAM SCAN. The refused branch used to hold both answers —
    "send it back; only a send that answers that the teammate cannot be
    reached takes the re-dispatch step" — and a sentence predicting which one
    the lead would need. That is the conditional lead-stalls GI-008 forbids:
    the lead's call after the send depended on reading the send's answer. The
    server already held the input the prediction was made from, so it picks:
    `refused` (the team is registered, the teammate that built it is sent the
    refusal) or `redispatch` (it is not, and a fresh teammate is). A reading
    that did not measure the team scan owes `redispatch`, on the D-013
    asymmetry: a spare teammate costs a spawn, a message sent to a torn-down
    team parks the run with nobody told.

    lead-stalls D-031 — AND THE TEAM ASKED ABOUT IS THE REFUSED CASTING'S OWN.
    `teams_active` is any registered team directory OR any live teammate pane
    ON THE MACHINE, so one teammate pane in another project's tmux session read
    as "the CAST team is still registered" over a run that had registered
    none, and the lead was told to message a teammate that did not exist and
    end its turn. The choice is now the one `_team_work_in_flight` already
    makes: is `cast-{run}-wave-{W}`, W the refused casting's own wave, among
    the REGISTERED names. The panes decide nothing here. A name the reading did
    not publish, or a registry it did not read, answers `redispatch`, on the
    asymmetry above.
    """
    row = liveness if isinstance(liveness, dict) else {}
    if _casting_ids(row.get("cast_refused")):
        own = row.get("cast_refused_team")
        registered = row.get("teams_registered")
        return (
            "refused"
            if isinstance(own, str) and isinstance(registered, list)
            and own in registered
            else "redispatch"
        )
    if _casting_ids(row.get("cast_unaccepted")):
        return "unaccepted"
    return None


def _branch_states(liveness: object) -> tuple[str, ...]:
    """Every run state the reading stands in, most urgent first
    (lead-stalls FR-015 / CT-008 / ST-004).

    LIVE COMES FIRST, AND ACCEPTANCE ONLY WHEN NOTHING IS RUNNING. A lead woken
    by one teammate's completion while another is still building is owed END
    YOUR TURN (lead-stalls FR-002 / ST-002): the finished casting's acceptance
    is still owed when the last notification arrives, and the reading taken
    then names it. Putting acceptance first would turn every mid-wave wake into
    a second sequence beside a running wave, which is the shape lead-stalls
    GI-008 forbids a payload to carry.

    `_select_branch` takes the FIRST state the entry declares, so an entry that
    declares no acceptance branch — `fix_defects`, `run_streams` — answers on
    the base state exactly as it did before this reading existed.
    """
    base = _branch_state(liveness)
    if base == "live":
        return (base,)
    owed = _acceptance_state(liveness)
    return (owed, base) if owed else (base,)


def _branch_name(branches: dict[str, str], states: tuple[str, ...]) -> str:
    """The declared branch the first matching state resolves to. Total."""
    for candidate in (*states, _BRANCH_FALLBACK):
        if candidate and branches.get(candidate):
            return candidate
    # Total tail: a branched entry always holds at least one branch, so the
    # lead receives prose rather than a marker even if a later edit drops the
    # declared fallback. The suite pins that no shipped entry needs this.
    return next(iter(branches))


def _select_branch(text: str, *states: str) -> str:
    """The ONE branch of a multi-state imperative the lead receives
    (lead-stalls GI-008). ``states`` is a preference order; the first one the
    entry declares wins, then `_BRANCH_FALLBACK`."""
    branches = _parse_branches(text)
    if not branches:
        return text
    return branches[_branch_name(branches, states)]


def _chosen_branch(action: str, liveness: object) -> str | None:
    """Which declared branch `_format_imperative_header` hands the lead for
    ``action`` on this reading, or ``None`` for an unbranched entry.

    lead-stalls GI-008 / D-019 — the stall notice asks THIS, so the notice and
    the header are two readers of one selection and cannot disagree about
    whether the lead was told to end its turn.
    """
    branches = _parse_branches(_ACTION_IMPERATIVES.get(action, ""))
    if not branches:
        return None
    return _branch_name(branches, _branch_states(liveness))


#: lead-stalls D-009 / D-012 — THE THREE SLOTS A BRANCH CAN CARRY, AND WHY EACH
#: RESOLVES THROUGH A TOTAL HELPER RATHER THAN THROUGH A LOOKUP THAT CAN MISS.
#:
#: `{run}` was the only slot these entries had, so the wave and the cycle were
#: LITERALS beside it: `cast-{run}-wave-1`, `grind-{run}-cycle-N`. The first is
#: wrong the moment a run has two waves — corrected wave-2 routing would have
#: re-dispatched wave 1 over castings already accepted — and the second reached
#: the lead as the character `N`, which is not a team name and which the
#: `fix_defects` payload carries nothing to resolve. The lead supplied a number
#: from its own reckoning, which is the judgment task lead-stalls FR-007 defines
#: as the defect.
#:
#: All three answer for EVERY input, on `_halt_cause`'s side of the line rather
#: than `{gate}`'s: an unresolved slot here would discard the imperative and
#: take the generic "Execute the first tool call mentioned. Do not deliberate."
#: header, which is the conditional-judgment push lead-stalls GI-008 forbids.
#: The declared defaults are the values the entries held as literals, so a
#: reading that cannot answer emits exactly what shipped before this change.
_CAST_WAVE_DEFAULT = "1"
_GRIND_CYCLE_DEFAULT = "0"


def _wave_number(value: object, default: str) -> str:
    """A wave/cycle slot's value as a string, total over every input."""
    if isinstance(value, bool) or not isinstance(value, int):
        return default
    return str(value) if value >= 1 else default


def _cast_wave(liveness: object) -> str:
    """`{wave}` — the CAST wave to DISPATCH, from the reading `_branch_state`
    routed on (lead-stalls FR-015: the same `_waiting_on_agents` result, never
    a second measurement of a roster that has moved since)."""
    row = liveness if isinstance(liveness, dict) else {}
    return _wave_number(row.get("cast_wave_pending"), _CAST_WAVE_DEFAULT)


def _built_cast_wave(liveness: object) -> str:
    """`{built_wave}` — the HIGHEST CAST wave that was dispatched, which is the
    team the wave-complete branch tells the lead to tear down.

    A separate slot from `{wave}` deliberately. The two are equal only in a
    single-wave run, and one field meaning "the wave to dispatch" in one branch
    and "the wave just finished" in another is the shape lead-stalls D-009 is:
    a reading that two run states cannot be told apart in.
    """
    row = liveness if isinstance(liveness, dict) else {}
    return _wave_number(row.get("cast_wave_built"), _CAST_WAVE_DEFAULT)


def _grind_cycle(cycle: object) -> str:
    """`{cycle}` — the server-owned GRIND cycle counter, substituted the way
    `{run}` is: one run-level scalar the emitter reads once and every entry
    holding the slot receives.

    It is `current_cycle`'s answer unmodified, so the team name agrees with the
    `cycle` argument the same payload tells the lead to pass to `Foundry-Fix`
    and with the `fixed_in_cycle` the ledger then records. `0` is the default
    for the same reason `current_cycle` returns it: a counter that cannot be
    read is the run's first cycle as far as every other reader is concerned.
    """
    if isinstance(cycle, bool) or not isinstance(cycle, int):
        return _GRIND_CYCLE_DEFAULT
    return str(cycle) if cycle >= 0 else _GRIND_CYCLE_DEFAULT


#: lead-stalls D-020 — what `{casting}` resolves to when the reading names no
#: casting. `_branch_states` selects an acceptance branch ONLY when its list is
#: non-empty, and `_casting_slot` reads the same list first, so no shipped
#: route reaches this. It is the `casting_id=N` register `_GRIND_DISPATCH`
#: already uses, rather than an empty string that would print `casting_id=,`.
_CASTING_DEFAULT = "N"


def _casting_slot(liveness: object) -> str:
    """`{casting}` — the casting an acceptance branch is about, total.

    Read in `_acceptance_state`'s order, so the id always belongs to the branch
    that was chosen: `refused` or `redispatch` is chosen exactly when
    `cast_refused` is non-empty, and `unaccepted` only when it is empty. The FIRST id, because
    `_cast_wave_position` lists them in manifest wave order, and settling the
    lowest wave first is the order the waves were built in.
    """
    row = liveness if isinstance(liveness, dict) else {}
    for key in ("cast_refused", "cast_unaccepted"):
        ids = _casting_ids(row.get(key))
        if ids:
            return ids[0]
    return _CASTING_DEFAULT


# --------------------------------------------------------------------------- #
# lead-stalls GI-008 / FR-007 / FR-008 / FR-015 / OT-002 / OT-013 — THE NEXT
# CALLS ARE A STEP LIST THE ROUTER EMITS, AND THE HEADER IS ITS RENDERING
# (D-038..D-043, on the user's ruling of 2026-09-17).
#
# Every imperative was a string, and the audit FR-008 discharges on judged the
# string with a prose detector: a syntactic guard over a semantic property. It
# took six repairs (D-023, D-029, D-032, D-034, D-041, D-043), each a
# conditional shaped so the last widening could not see it, and the user ruled
# for a structure rather than a seventh widening.
#
# So a lead's next calls are `_Step`s: one tool, its literal arguments, how
# many, the prompt blocks a spawn passes, and a note. No field holds a
# condition, and a choice between two calls cannot be written down, because a
# step list only says "this, then this". `_emitted_imperative` picks the
# branch, expands and substitutes the steps, and renders the header from them;
# Foundry-Next publishes the same steps as `next_calls`, so the payload and the
# prose are one answer. The audit judges the steps.
#
# `_ACTION_IMPERATIVES` stays a table of STRINGS rendered from this one: two
# suites sweep its values for denied spellings and steerable-model claims, and
# D-226 is the defect where such a window was narrower than the rule. It is a
# rendering of `_IMPERATIVES`, never a second source.
# --------------------------------------------------------------------------- #


class _Step(NamedTuple):
    """One call the lead makes, in order. There is no field for a condition."""

    tool: str
    #: The literal argument list, rendered inside the parentheses; ``None``
    #: renders the bare tool name (TeamDelete takes none).
    args: str | None = None
    #: How many and in what message, e.g. "once per returned casting".
    each: str = ""
    #: A spawn's prompt blocks, keys of `_PROMPT_BLOCKS`, in order.
    blocks: tuple[str, ...] = ()
    note: str = ""


class _Imperative(NamedTuple):
    """One run state's next calls and the statement printed under them.
    An empty ``steps`` is the explicit NONE the terminals and the live
    branches answer with."""

    steps: tuple[_Step, ...]
    trailer: str = ""


#: The yield. Not a tool, and the only step with no call: it renders as the
#: wait policy, and it is always LAST, because whatever follows the wake is
#: the Foundry-Next that the policy itself names.
_END_TURN = "END YOUR TURN"
_YIELD = _Step(_END_TURN)

#: The step a `run_streams` idle branch holds in place of its stream calls,
#: expanded at emission from the roster the router published (D-040).
_EACH_UNRECORDED_STREAM = "{unrecorded streams}"

#: lead-stalls GI-008 / FR-007 (D-053) — the step `assay_failed_loop_back`
#: holds in place of the filing its list may owe, expanded at emission from
#: `details["unfiled_verdicts"]`: one Foundry-Sync naming those requirements,
#: or no step at all. `foundry_add_verdict` records a verdict and files no
#: defect, and `Foundry-Gate(phase='grind')` refuses "No open defects to
#: grind", so a list of Tasks, Gate and Phase served over a non-VERIFIED
#: verdict with nothing filed could never succeed — the next Foundry-Next
#: re-served it and the run never left F4. The filing was only in the CONTEXT
#: ("Sync findings as defects (Foundry-Sync)"), the one surface the lead is
#: told not to take calls from.
#:
#: WHY A STEP SLOT AND NOT A SECOND ACTION. The state differs only in whether
#: the ledger holds what ASSAY found, and the rest of the list — the ASSAY-
#: rejection door into F3 — is the same calls in the same order. A second
#: action would restate them and grow the table the FR-008 sweep is counted
#: over; the slot is `_EACH_UNRECORDED_STREAM`'s shape, where the router
#: publishes the fact and the resolver turns it into steps.
_EACH_UNFILED_VERDICT = "{unfiled verdicts}"

#: lead-stalls GI-008 / FR-007 (D-055) — the step `transition_to_grind` holds
#: in place of its teammate dispatch, expanded at emission from
#: `details["open_defects"]`, the BLOCKING count the router published.
#:
#: The list is served in two run states. The ordinary one has blocking
#: defects, and the dispatch is its point. The other is a clean INSPECT that a
#: still-ESCALATED class holds DONE shut over (`_escalation_hold`): the class
#: clears only at a crossing that advances the cycle counter, and from a FULL
#: F2 or from past ASSAY the one such crossing the server accepts is a GRIND
#: opened on whatever record is open, then `inspect_start` out of F3. There the
#: open records are LATENT or HARDENING, which no teammate is dispatched for —
#: the F3 arm answers `transition_to_inspect` as soon as nothing blocks — so a
#: spawn step would hand the lead teammates for work the GRIND does not do.
#:
#: WHY A SLOT AND NOT A SECOND ACTION, for `_EACH_UNFILED_VERDICT`'s reason:
#: the first three calls are the same calls in the same order, and a second
#: action would restate them and grow the table the FR-008 sweep counts. Only
#: an explicit integer 0 omits the dispatch; an absent or unreadable count
#: keeps it, which is the list every other router arm serves.
_BLOCKING_DISPATCH = "{blocking dispatch}"

#: lead-stalls GI-008 / FR-007 — the step `halted` holds in place of the
#: report the halt could not write, expanded from `details["report_generated"]`.
#: The CONTEXT used to say "call Foundry-Report to write REPORT.md" beneath a
#: header reading "YOUR NEXT CALL: NONE ... The report is generated.", which
#: is two answers in one payload about one file.
_UNWRITTEN_REPORT = "{unwritten report}"

#: Every name a step may carry as its tool. Closed, so a step naming something
#: the lead cannot call is caught where it is written, and pinned by the audit
#: against the MCP server's own tool list.
_LEAD_CALLS = frozenset({
    "Agent", "Bash", "SendMessage", "Skill", "TeamCreate", "TeamDelete",
    "Foundry-Accept-Casting", "Foundry-Cast-Wave", "Foundry-Context",
    "Foundry-Gate", "Foundry-Init", "Foundry-Next", "Foundry-Phase",
    "Foundry-Report", "Foundry-Spawn-Teammate", "Foundry-Spec-Hash",
    "Foundry-Sync", "Foundry-Tasks", "Foundry-Team-Down", "Foundry-Team-Up",
    "Foundry-Validate-Castings",
    _END_TURN,
})

#: The two doors that seed a ledger before they return (D-038).
_SPAWN_DOORS = ("Foundry-Cast-Wave", "Foundry-Spawn-Teammate")


def _render_call(step: _Step) -> str:
    """One step as the lead reads it."""
    if step.tool == _END_TURN:
        return _WAITING_IS_NOT_STOPPING
    call = step.tool if step.args is None else f"{step.tool}({step.args})"
    pieces = [f"{call} {step.each}" if step.each else call]
    if step.blocks:
        pieces.append(_spawn_prompt(step.blocks))
    if step.note:
        pieces.append(step.note)
    return " — ".join(pieces)


def _render_imperative(imperative: _Imperative) -> str:
    """The header for one run state: its numbered steps, then its trailer."""
    steps = imperative.steps
    if not steps:
        head = "YOUR NEXT CALL: NONE. "
    else:
        head = (
            "YOUR NEXT CALL:\n" if len(steps) == 1
            else "YOUR NEXT CALLS (in order):\n"
        ) + "".join(
            f"  ({number}) {_render_call(step)}\n"
            for number, step in enumerate(steps, 1)
        )
    return head + imperative.trailer


def _gate_advance_notice(gate: str, steps: tuple[_Step, ...] | None) -> str:
    """The notice a passed gate prints above its list: which numbered step
    comes next.

    lead-stalls GI-008 (D-048) — this said "Proceed directly to the transition
    step (Foundry-Phase / state update)", which is the step after the gate on
    ten lists and not on `transition_to_done`, where the strip and its commit
    come between them. So it names the list's own step after the gate, read
    off the steps the header below was rendered from, and falls back to the
    step's place in the list — never to a tool the list may not put there.
    """
    head = (
        f"✅ Foundry-Gate(phase='{gate}') ALREADY PASSED — do NOT re-run it. "
        "Proceed directly to "
    )
    calls = [
        (number, step) for number, step in enumerate(steps or (), 1)
        if step.tool == "Foundry-Gate"
    ]
    named = [pair for pair in calls if pair[1].args == f"phase='{gate}'"]
    for number, _ in named or calls:
        if number < len(steps):
            after = steps[number]
            call = after.tool if after.args is None else f"{after.tool}({after.args})"
            return (
                f"{head}step ({number + 1}) {call} in the imperative below, "
                "the step it numbers after that gate."
            )
    return f"{head}the step the imperative below numbers after that gate."


def _call_record(step: _Step) -> dict:
    """A step as `next_calls` publishes it."""
    return {
        "tool": step.tool,
        "args": step.args,
        "each": step.each,
        "prompt_blocks": list(step.blocks),
        "note": step.note,
    }


#: lead-stalls D-040 — ONE LITERAL CALL PER STREAM THE RECORDED ROSTER NAMES.
#:
#: The idle branch listed TRACE and PROVE with calls, three more with
#: qualifiers ("MIGRATION only"), and "TEST / PROBE: may also run as background
#: Agents" with no subagent_type and no prompt, and it never named `test01` at
#: all. Driven on this run's own roster (trace, prove, test, test01), the lead
#: had to find test01's agent type outside the payload, write TEST's prompt
#: itself, and check each bullet's qualifier against the CONTEXT — conditions
#: and a judgment task to find its next calls.
#:
#: The server already holds the recorded roster and which of it is
#: unrecorded, so the steps are that list: `(subagent_type, prompt)` per
#: stream, the reading streams told to pin a snapshot IN their prompt (D-169)
#: rather than asking the lead to add it. `sight` is the one stream the lead
#: executes, through its skill.
_PIN_A_SNAPSHOT = (
    " Pin your work to a snapshot: take the HEAD sha once at start, verify "
    "every finding against `git archive HEAD` rather than the live tree, and "
    "cite that sha in your report."
)
_STREAM_AGENTS: dict[str, tuple[str, str]] = {
    "trace": (
        "foundry:tracer",
        "Run TRACE wiring verification for the active foundry run."
        + _PIN_A_SNAPSHOT,
    ),
    "flow_trace": (
        "foundry:flow-tracer",
        "Run FLOW_TRACE (flow-delta wiring verification) for the active "
        "foundry run." + _PIN_A_SNAPSHOT,
    ),
    "prove": (
        "foundry:assayer",
        "Run PROVE (spec-to-code citation verification) for the active "
        "foundry run." + _PIN_A_SNAPSHOT,
    ),
    "research_audit": (
        "foundry:research-auditor",
        "Run RESEARCH_AUDIT for the active foundry run." + _PIN_A_SNAPSHOT,
    ),
    "coverage_diff": (
        "foundry:coverage-diff",
        "Run COVERAGE_DIFF for the active foundry run." + _PIN_A_SNAPSHOT,
    ),
    "test01": (
        "foundry:spec-test-deriver",
        "Run TEST-01 (spec-derived contract tests) for the active foundry "
        "run.",
    ),
    "test": (
        "general-purpose",
        "Run the TEST stream for the active foundry run: run the test suite, "
        "verify each GRIND fix by reverting it and re-running, and record the "
        "counts you measured through Foundry-Stream with stream 'test'.",
    ),
    "probe": (
        "general-purpose",
        "Run the PROBE stream for the active foundry run: smoke the API at the "
        "run's target_url, and record the counts you measured through "
        "Foundry-Stream with stream 'probe'.",
    ),
}

#: The baseline `general-purpose` stream agents keep (test_model_config.py
#: pins it; the model option steers only `STEERABLE_SUBAGENT_TYPES`).
_GENERAL_STREAM_MODEL = "opus"

_SIGHT_STEP = _Step(
    "Skill",
    "skill='foundry:sight'",
    note=(
        "SIGHT runs in this main thread, beside the background streams, "
        "because Playwright works nowhere else."
    ),
)

#: Every stream a step can name, in the order the template renders them. The
#: expansion falls back to it for a reading that published no roster, which
#: the router never does; it is the `{wave}` default's register, and it keeps
#: every stream prompt inside `_ACTION_IMPERATIVES` for the prose sweeps.
_STREAM_TEMPLATE_ROSTER = (*_STREAM_AGENTS, "sight")


def _stream_agent_step(stream: str) -> _Step:
    """The Agent call for one stream, total over every name."""
    subagent_type, prompt = _STREAM_AGENTS.get(
        stream,
        (
            "general-purpose",
            f"Run the {stream} verification stream for the active foundry run.",
        ),
    )
    model = (
        f"model='{_GENERAL_STREAM_MODEL}', "
        if subagent_type == "general-purpose" else ""
    )
    return _Step(
        "Agent",
        f"{model}subagent_type='{subagent_type}', run_in_background=true, "
        f'prompt="{prompt}"',
        each=f"for {stream}",
    )


def _stream_agent_config(stream: str) -> dict:
    """`details.agent_configs[stream]`, from the same row the step reads."""
    subagent_type = _STREAM_AGENTS.get(stream, ("general-purpose", ""))[0]
    if subagent_type == "general-purpose":
        return {
            **agent_model(subagent_type, baseline=_GENERAL_STREAM_MODEL),
            "subagent_type": subagent_type,
        }
    return {
        "subagent_type": subagent_type,
        "run_in_background": True,
        "description": f"{stream}: verification stream",
    }


def _stream_steps(streams: object) -> tuple[_Step, ...]:
    """The calls for the unrecorded streams, in roster order. Total.

    The yield closes the list whenever an agent was spawned: a stream writes no
    ledger line until it starts, so a Foundry-Next taken straight after the
    spawn reads it as unrecorded and would spawn it twice. The completion
    notification is the wake. SIGHT alone spawns nothing to wait for, so its
    list ends at the skill.
    """
    names: list[str] = []
    for name in streams if isinstance(streams, (list, tuple)) else ():
        if isinstance(name, str) and name and name not in names:
            names.append(name)
    if not names:
        names = list(_STREAM_TEMPLATE_ROSTER)
    agents = [_stream_agent_step(name) for name in names if name != "sight"]
    steps = list(agents)
    if "sight" in names:
        steps.append(_SIGHT_STEP)
    if agents:
        steps.append(_YIELD)
    return tuple(steps)


#: What a template rendering of `_EACH_UNFILED_VERDICT` names in place of the
#: requirement ids, so the prose sweeps over `_ACTION_IMPERATIVES` see the step.
_UNFILED_TEMPLATE = ("each non-VERIFIED requirement in verdicts.json",)


def _verdict_filing_steps(unfiled: object) -> tuple[_Step, ...]:
    """The filing `assay_failed_loop_back` owes first, or none. Total.

    ``unfiled`` is the router's `details["unfiled_verdicts"]`: the ids of the
    non-VERIFIED verdicts that no open BLOCKING defect's `spec_ref` names
    (`_carried_requirements`, D-054). No field is a choice handed over. The tier:
    ASSAY's verdict names a requirement, so the filing is on-row — `HARDENING`
    refuses a `spec_ref`, and `LATENT` needs a reproduction the verdict does
    not carry. The type: every verdict but VERIFIED is a `DEFECT_TYPES` member
    (`gates.VERDICT_VALUES`), so the verdict word IS the type.
    """
    ids = [
        rid for rid in unfiled if isinstance(rid, str) and rid
    ] if isinstance(unfiled, (list, tuple)) else []
    if not ids:
        return ()
    return (
        _Step(
            "Foundry-Sync",
            "cycle={cycle}, findings=[one finding per requirement ("
            + ", ".join(ids)
            + "), each with source='assay', tier='LIVE', type=<that "
            "requirement's verdict>, spec_ref=<that requirement id>, "
            "class=<that requirement id>, description=<that requirement's "
            "verdict and evidence, as verdicts.json records them>]",
            note=(
                "ASSAY recorded these verdicts and no open blocking defect "
                "carries them, so the GRIND below would have nothing to "
                "dispatch for them."
            ),
        ),
    )


def _report_steps(details: object) -> tuple[_Step, ...]:
    """The Foundry-Report a halted run owes when its halt wrote no report,
    or none. Total: only an explicit ``report_generated: False`` owes it."""
    if not isinstance(details, dict) or details.get("report_generated") is not False:
        return ()
    return (
        _Step(
            "Foundry-Report",
            note=(
                "the halt recorded no report, and this door still runs on a "
                "halted run; a report it still cannot write comes back "
                "refused with its cause named."
            ),
        ),
    )


def _expanded_steps(step: _Step, details: dict | None) -> tuple[_Step, ...]:
    """``step`` as the lead receives it: a slot step expanded from what the
    router published in ``details``, or the step itself. ``None`` is the
    template rendering, which shows every step a slot can expand to."""
    template = details is None
    details = details or {}
    if step.tool == _EACH_UNRECORDED_STREAM:
        return _stream_steps(None if template else details.get("missing_streams"))
    if step.tool == _EACH_UNFILED_VERDICT:
        return _verdict_filing_steps(
            _UNFILED_TEMPLATE if template else details.get("unfiled_verdicts")
        )
    if step.tool == _UNWRITTEN_REPORT:
        return _report_steps({"report_generated": False} if template else details)
    if step.tool == _BLOCKING_DISPATCH:
        count = details.get("open_defects")
        nothing_blocks = (
            not template and isinstance(count, int)
            and not isinstance(count, bool) and count == 0
        )
        return () if nothing_blocks else _GRIND_SPAWN_STEPS
    return (step,)


#: lead-stalls CT-002 / FR-002 — the teammates-live branch, for both audited
#: actions. It names NO next call ON PURPOSE, in the same register as the
#: `done` and `halted` terminals: "end your turn" IS the correct move here,
#: and an imperative that named a tool call would be telling the lead to
#: improvise over half-finished work. The survey counted `done` and `halted`
#: among the 20 already-correct entries for naming NONE, which is the
#: precedent.
#:
#: lead-stalls D-038 — the sentence says what "running" covers, because a
#: ledger holding only its door's seed counts: under `_SPAWN_IS_ONE_MOVE` that
#: teammate's Agent was made in the same move as the door.
_SEED_COUNTS_AS_RUNNING = (
    "A teammate whose ledger holds only the line its spawn door wrote counts "
    "as running, because the door and its Agent call are one move. "
)
_CAST_TEAMMATES_LIVE = _Imperative((), (
    "Your CAST teammates are running — this server read their progress "
    "ledgers on this call and measured them advancing. "
    + _SEED_COUNTS_AS_RUNNING + _WAITING_IS_NOT_STOPPING
))

_GRIND_TEAMMATES_LIVE = _Imperative((), (
    "Your GRIND teammates are running — this server read their progress "
    "ledgers on this call and measured them advancing. "
    + _SEED_COUNTS_AS_RUNNING + _WAITING_IS_NOT_STOPPING
))

#: lead-stalls GI-008 / FR-007 (D-019) — `run_streams` IS THE THIRD ACTION
#: WHOSE WORK IS AGENTS, AND THE ONE A LEAD RE-READS WHILE THEY RUN.
#:
#: Its streams are BACKGROUND agents, and every Foundry-Next taken while they
#: worked found them unrecorded — they record at the END — and was handed
#: "spawn every missing INSPECT stream" beside a CONTEXT naming the very
#: streams already running. FR-007 is "fix any others found"; this is the
#: other one, and it takes the same treatment.
_STREAMS_RUNNING = _Imperative((), (
    "Your INSPECT streams are running — this server read their progress "
    "ledgers on this call and measured them advancing. A stream records "
    "itself when it finishes, so an unrecorded stream that is running is not "
    "missing, and spawning it again runs it twice over one cycle. "
    + _WAITING_IS_NOT_STOPPING
))

#: lead-stalls GI-008 / FR-007 (D-056) — `add_castings` IS THE FOURTH ACTION
#: WHOSE WORK IS AGENTS, AND IT READ NO ROSTER.
#:
#: The decomposition writers are BACKGROUND agents, one per domain, and their
#: list ended in END YOUR TURN with a trailer saying the Foundry-Next "the last
#: notification wakes you for" opens the validation. Every writer's
#: notification wakes the lead, so the lead had to judge which one was the
#: last — the conditional GI-008 forbids — and the router could not help it:
#: F0 took no liveness reading, so the first writer's manifest entry turned
#: the answer into `transition_to_cast` while the others were still writing.
#: Driven, following that list validated one casting, crossed into F1 and
#: dispatched wave 1 over a decomposition still in progress.
#:
#: So each writer keeps a progress ledger (the idle branch's prompt says
#: where and how), F0 reads it through `_waiting_on_agents` like CAST and
#: GRIND, and while any writer is advancing this branch answers END YOUR TURN.
#: The ledger name is `decompose-<domain>`, which no casting's ledger
#: (`casting-<id>`) and no stream's can collide with.
_DECOMPOSITION_WRITERS_LIVE = _Imperative((), (
    "Your decomposition writers are running — this server read their "
    "progress ledgers on this call and measured them advancing, so the "
    "castings manifest is not finished yet. " + _WAITING_IS_NOT_STOPPING
))

_DECOMPOSITION_WRITERS = _Imperative(
    (
        _Step(
            "Agent",
            "model='opus', subagent_type='general-purpose', "
            "mode='bypassPermissions', run_in_background=true, "
            "prompt=<per commands/start.md §F0.5 DECOMPOSE: write the "
            "domain's entry into manifest.json AND write "
            "casting-{id}-prompt.md to foundry-archive/{run}/castings/ "
            "following the layout in start.md §6 — and keep a progress "
            "ledger at foundry-archive/{run}/progress/decompose-<domain>.jsonl: "
            "append one JSON line carrying timestamp (UTC ISO-8601), "
            "phase 'decompose' and step as the FIRST act, again after each "
            "file written, and a LAST line that also carries \"done\": true>",
            each=(
                "for each domain identified from the spec (1-5 of them), "
                "all in a SINGLE parallel message"
            ),
        ),
        _YIELD,
    ),
    "No team is needed: these are short-lived file writers, so the "
    "TeamCreate ceremony is skipped. Each writer's return message is its "
    "TaskOutput, and each keeps the progress ledger its prompt names, which "
    "is what the Foundry-Next every notification wakes you for reads to tell "
    "a finished decomposition from one still being written.",
)


#: The model clause every teammate spawn carries (test_model_config.py pins
#: the deferral on both transition entries).
_MODEL_CLAUSE = (
    "For the model: obey the model clause in the `instructions` {door} "
    "returned — this server owns that decision; never re-derive it here."
)


def _teammate_spawn(phase: str, each: str, door: str) -> _Step:
    """The Agent step a spawn door feeds, for ``phase`` (D-038 / D-039)."""
    return _Step(
        "Agent",
        "subagent_type='foundry:teammate', mode='bypassPermissions'",
        each=each,
        blocks=_SPAWN_PROMPT_ORDER[phase],
        note=_SPAWN_IS_ONE_MOVE + " " + _MODEL_CLAUSE.format(door=door),
    )


_CAST_WAVE_SPAWN = _teammate_spawn(
    "cast",
    "for each returned casting, all in a SINGLE message (parallel tool use), "
    "foreground, never run_in_background=true",
    "Foundry-Cast-Wave",
)

#: lead-stalls CT-001 — the wave-complete branch: the literal calls, in order.
#:
#: `Foundry-Gate(phase='inspect')` is named here because the F1 arm returns
#: `build_castings` for the WHOLE of F1 — `.cast-complete` is written BY the
#: `cast` transition — so this branch is the only lead-facing surface for the
#: F1 -> F2 crossing.
#:
#: lead-stalls D-013 — THE CLOSING SENTENCE ASSERTS A MEASUREMENT, AND IT IS
#: TRUE ON EVERY PATH THAT REACHES IT. The only route here is `_branch_state`
#: answering `idle`, which requires a measured `cast_wave_pending` of 0, and
#: `_cast_wave_position` publishes 0 only after loading the manifest AND
#: walking the ledger-derived done set. `refused` and `unaccepted` outrank
#: `idle` (D-020), so the teardown is reached only once every done casting is
#: also ACCEPTED.
_CAST_WAVE_COMPLETE = _Imperative(
    (
        _Step("TeamDelete", each="for the CAST team"),
        _Step(
            "Foundry-Team-Down", "team_name='cast-{run}-wave-{built_wave}'",
            note="the team this run registered for its last CAST wave",
        ),
        _Step("Foundry-Gate", "phase='inspect'"),
        _Step(
            "Foundry-Phase", "phase='cast'",
            note=(
                "the call that ENTERS F2. It sweeps the evidence corpus and "
                "RECORDS this INSPECT's width and roster; editing state.json "
                "by hand records no width at all, and every door that reads "
                "one then refuses."
            ),
        ),
    ),
    "Every casting of every wave has declared itself done and no agent is "
    "running — this server read the manifest and the progress ledgers on this "
    "call — so the build is finished and the teardown is yours to make now."
    + _GATE_THEN_PHASE_NOTE,
)

#: The dispatch state, which `fix_defects` has no equivalent of: a lead between
#: `Foundry-Phase(phase='start_cast')` and its first Agent spawn stands in
#: `build_castings` with nothing dispatched, and so does a lead at a wave
#: boundary.
#:
#: lead-stalls D-003 — it serves that lead before AND after it registers the
#: team, so step (1) is made unconditionally: a TeamCreate or Foundry-Team-Up
#: that answers "already registered" costs a call, and step (3) is still where
#: the dispatch begins.
_CAST_WAVE_UNDISPATCHED = _Imperative(
    (
        _Step("TeamCreate", "'cast-{run}-wave-{wave}'"),
        _Step("Foundry-Team-Up", "team_name='cast-{run}-wave-{wave}'"),
        _Step(
            "Foundry-Cast-Wave", "wave={wave}, phase='cast'",
            note="returns ALL wave-{wave} dispatch blocks in ONE call",
        ),
        _CAST_WAVE_SPAWN,
        _YIELD,
    ),
    "Wave {wave} is the LOWEST wave this server could place as still holding a "
    "casting that has not declared itself done, so it is the wave to dispatch "
    "and no wave beneath it is left open. Where the castings manifest answers, "
    "that placement is read from it and from the progress ledgers on this "
    "call; where it does not answer, the number falls back to wave 1 and step "
    "(3) refuses and names the manifest rather than dispatching the wrong "
    "wave. A TeamCreate or a Foundry-Team-Up that answers 'already registered' "
    "has cost one call, and the dispatch still begins at step (3). A wave "
    "whose door already returned reads here only once its seeds are stale — "
    "no teammate wrote a line in the stall window — and that wave is "
    "dispatched again.",
)

#: lead-stalls ST-004 / US-003 / D-020 — THE STATES BETWEEN A CASTING'S DONE
#: LINE AND ITS WAVE BEING BUILT.
#:
#: `unaccepted`: done, and no verdict since. That is the ordinary state of a
#: finished teammate AND the state a CALL-side refusal leaves (a stale spec
#: hash records nothing), and both owe the same call made correctly.
_CAST_ACCEPTANCE_DUE = _Imperative(
    (
        _Step(
            "Foundry-Spec-Hash",
            note="a fresh hash; acceptance refuses a stale one",
        ),
        _Step(
            "Foundry-Accept-Casting",
            "casting_id={casting}, spec_hash=<the hash step (1) returned>, "
            "prompt_hash=<the sha256 casting {casting}'s teammate stated in "
            "its completion report>, completion_report=<that report>, "
            "casting_commit=<the full SHA of casting {casting}'s commit>",
        ),
        _Step(
            "Foundry-Next",
            note=(
                "the call step (2) names on every path, accepted or refused; "
                "the verdict it recorded is what that call reads"
            ),
        ),
    ),
    "Casting {casting} has declared itself done and holds no acceptance "
    "verdict since — this server read the manifest, the progress ledgers and "
    "handoffs.jsonl on this call, and no agent is running. Its wave is not "
    "built until it is accepted, so neither a teardown nor the next wave is "
    "yours to make yet.",
)

#: `refused` and `redispatch`: `Foundry-Accept-Casting` recorded `-refused`
#: for this casting and its ledger has not moved since. The refusal goes to a
#: TEAMMATE, as a message, never inside a CAST dispatch block. The resumed or
#: re-spawned teammate writes its ledger again, and its next done line makes
#: the refusal OLDER than the work, which routes the lead to accept again.
#:
#: lead-stalls D-022 — TWO BRANCHES, BECAUSE ONE BRANCH HELD A CONDITIONAL.
#: The server chooses (`_acceptance_state`): with the refused casting's own
#: wave team registered (D-031), the teammate that built it is sent the
#: refusal; with it torn down, a fresh one is dispatched and sent the same
#: refusal as a separate message.
#:
#: lead-stalls D-030 — the re-spawned teammate is handed its ledger protocol;
#: the step renders the CAST order like every other spawn (D-039).
_REFUSED_IS_REWORKED = (
    "A refused casting is re-dispatched to be fixed: never torn down, never "
    "counted as built, never re-accepted unchanged."
)

_REFUSAL_MESSAGE = (
    "message=<the refusal Foundry-Accept-Casting returned for casting "
    "{casting}, verbatim — its error or failure_token, failure_detail, warning "
    "and hint>"
)

_CAST_REFUSED_SEND_BACK = _Imperative(
    (
        _Step(
            "SendMessage",
            "to=<the teammate you spawned for casting {casting}>, "
            + _REFUSAL_MESSAGE,
            note=(
                "casting {casting} goes back to the teammate that built it, "
                "which still holds its context, to fix what was refused and "
                "report again."
            ),
        ),
        _YIELD,
    ),
    "Foundry-Accept-Casting REFUSED casting {casting} and its ledger has not "
    "moved since — this server read handoffs.jsonl, the progress ledgers and "
    "the team registry on this call: no agent is running and casting "
    "{casting}'s own wave team is still registered, so the teammate that built "
    "it is the one to send it to. " + _REFUSED_IS_REWORKED,
)

_CAST_REFUSED_REDISPATCH = _Imperative(
    (
        _Step("Foundry-Spawn-Teammate", "casting_id={casting}, phase='cast'"),
        _teammate_spawn(
            "cast",
            "for casting {casting} alone, foreground, never "
            "run_in_background=true",
            "step (1)",
        ),
        _Step(
            "SendMessage",
            "to=<the teammate step (2) spawned>, " + _REFUSAL_MESSAGE,
            each="as a separate message",
            note="the refusal never rides inside the dispatch",
        ),
        _YIELD,
    ),
    "Foundry-Accept-Casting REFUSED casting {casting} and its ledger has not "
    "moved since — this server read handoffs.jsonl, the progress ledgers and "
    "the team registry on this call: no agent is running and casting "
    "{casting}'s own wave team is not registered, so the teammate that built "
    "it went with that team and casting {casting} goes to a fresh one. "
    + _REFUSED_IS_REWORKED,
)


#: The `fix_defects` idle branch, and its only one. With no agent running this
#: action is emitted ONLY while blocking defects are open (lead-stalls D-017),
#: so "no agent is running" always means the work is open and nobody is on
#: it. Steps (1) and (2) cost nothing when no team is registered, which lets
#: one list serve both the never-dispatched and the finished-with-work-open
#: readings.
_GRIND_SPAWN_STEPS = (
    _Step("TeamCreate", "'grind-{run}-cycle-{cycle}'"),
    _Step("Foundry-Team-Up", "team_name='grind-{run}-cycle-{cycle}'"),
    _Step(
        "Foundry-Spawn-Teammate", "casting_id=N, phase='grind'",
        each="for each casting carrying open defects",
    ),
    _teammate_spawn(
        "grind",
        "for each casting the step above dispatched, all in a SINGLE parallel "
        "message, foreground, never run_in_background=true",
        "Foundry-Spawn-Teammate",
    ),
    _YIELD,
)

_GRIND_DISPATCH = _Imperative(
    (
        _Step("TeamDelete", each="for this cycle's GRIND team"),
        _Step(
            "Foundry-Team-Down", "team_name='grind-{run}-cycle-{cycle}'",
            note="clears any team still registered from a previous dispatch",
        ),
        _Step("Foundry-Tasks"),
        *_GRIND_SPAWN_STEPS,
    ),
    "Blocking defects are open and no agent is running: this cycle's "
    "teammates are yours to dispatch now.",
)


_IMPERATIVES: dict[str, _Imperative | dict[str, _Imperative]] = {
    "init": _Imperative(
        (_Step("Foundry-Init", note="start a new run"),),
    ),
    # fallout FR-035 / AC-054 / CT-007: the one action whose imperative is to
    # STOP. The generic fallback header says "Execute the first tool call
    # mentioned. Do not deliberate.", which on a halted run would push the
    # lead straight back into the loop the cap ended.
    #
    # fallout US-006 (D-147): `{halt_cause}` is substituted from the RECORDED
    # reason member through `_halt_cause`, which is total, so it never falls
    # back to the generic header.
    #
    # lead-stalls GI-008 / FR-007 — and the report the halt could not write is
    # a served step, not a sentence in the CONTEXT (see `_UNWRITTEN_REPORT`).
    # With it written the list is empty and this reads NONE, as it always did.
    "halted": _Imperative((_Step(_UNWRITTEN_REPORT),), (
        "This run is HALTED — {halt_cause}. Do NOT dispatch a wave, do NOT "
        "call Foundry-Phase, do NOT call Foundry-Next in a loop. Read "
        "REPORT.md, tell the user what remains open by tier, and stop."
    )),
    "cleanup_teams": _Imperative(
        (
            _Step(
                "SendMessage",
                "to=<teammate>, message='All work complete, stop working.'",
                each="for each teammate, all in ONE parallel-tool-use message",
                note=(
                    "never a structured message with to='*' broadcast, which "
                    "rejects structured payloads"
                ),
            ),
            _Step(
                "TeamDelete",
                each="for each active team, immediately",
                note=(
                    "idle / terminated panes ARE the shutdown signal, and "
                    "TeamDelete cleans zombie panes"
                ),
            ),
            _Step("Foundry-Team-Down", each="for each team name"),
        ),
        "Do NOT wait for 'shutdown_response' events, 'shutdown_ack' events, "
        "idle confirmations, or any teammate reply. Stalling here is the #1 "
        "cleanup failure mode: the lead sends shutdown, sees panes idle, and "
        "waits forever for a reply that never comes.",
    ),
    # lead-stalls FR-007 — the validation "after all complete" is no longer a
    # step behind a wait: the yield ends this list, and `transition_to_cast`,
    # which is what the woken Foundry-Next answers, opens with it.
    #
    # lead-stalls GI-008 / FR-007 (D-056) — and WHICH woken Foundry-Next that
    # is, is the server's reading, not the lead's count of notifications:
    # branched like the other three agent-running actions, on the writers'
    # own progress ledgers.
    "add_castings": {
        "live": _DECOMPOSITION_WRITERS_LIVE,
        "idle": _DECOMPOSITION_WRITERS,
    },
    # lead-stalls D-009 — the literal `1` is correct here: `_compute_next_action`
    # returns this action from F0 and from nowhere else, so the only wave it
    # describes is the first. `build_castings`'s dispatch branch carries the
    # slot for every later wave.
    "transition_to_cast": _Imperative(
        (
            _Step(
                "Foundry-Validate-Castings",
                note="the check of what the decomposition wrote",
            ),
            _Step("Foundry-Gate", "phase='validate'"),
            _Step("Foundry-Phase", "phase='start_cast'"),
            _Step("TeamCreate", "'cast-{run}-wave-1'"),
            _Step("Foundry-Team-Up", "team_name='cast-{run}-wave-1'"),
            _Step(
                "Foundry-Cast-Wave", "wave=1, phase='cast'",
                note="returns ALL wave-1 dispatch blocks in ONE call",
            ),
            _CAST_WAVE_SPAWN,
            _YIELD,
        ),
        "(foundry:teammate's frontmatter carries effort=xhigh + all tools.) "
        "Do NOT send multiple messages with one Agent each — that serializes "
        "what should be parallel.\n"
        "Rules still apply: NEVER run_in_background=true for foundry:teammate. "
        "NEVER subagent_type='Explore' or 'general-purpose' for CAST. F0.5 "
        "DECOMPOSE uses background general-purpose Agents; F2 INSPECT and F4 "
        "ASSAY use named agents (foundry:tracer, foundry:assayer, "
        "foundry:research-auditor, foundry:coverage-diff) whose frontmatter "
        "carries model/effort/tools." + _GATE_THEN_PHASE_NOTE,
    ),
    # lead-stalls FR-001 / FR-002 / FR-015 / GI-008 / CT-001 / CT-002 — THE
    # LEAD RECEIVES ONE OF THESE, NEVER THE CHOICE BETWEEN THEM.
    #
    # This read "YOUR NEXT ACTION depends on wave state:" over two `IF` arms.
    # `_waiting_on_agents` already knew the answer, so it is read at emission
    # and one branch is chosen by the server that can measure the condition.
    # lead-stalls ST-004 / D-020 / D-022 add the acceptance branches, chosen
    # from the verdict `Foundry-Accept-Casting` records and only while no agent
    # is running (`_branch_states`).
    "build_castings": {
        "live": _CAST_TEAMMATES_LIVE,
        "idle": _CAST_WAVE_COMPLETE,
        "undispatched": _CAST_WAVE_UNDISPATCHED,
        "refused": _CAST_REFUSED_SEND_BACK,
        "redispatch": _CAST_REFUSED_REDISPATCH,
        "unaccepted": _CAST_ACCEPTANCE_DUE,
    },
    # fallout D-058 / AC-059 — ONE ACTION, TWO CROSSINGS: `{gate}` and
    # `{token}` come from ONE `_ACTION_CROSSINGS` row chosen by the emitting
    # phase, so step (1) and step (2) are two fields of one fact.
    "transition_to_inspect": _Imperative(
        (
            _Step("Foundry-Gate", "phase='{gate}'"),
            _Step(
                "Foundry-Phase", "phase='{token}'",
                note=(
                    "the transition that OPENS this INSPECT, and the gate "
                    "above is the one that guards it. That call sweeps the "
                    "evidence corpus and RECORDS this INSPECT's width and the "
                    "roster every stream then runs; from F3 it also advances "
                    "the server-side cycle counter, so skipping it files the "
                    "next cycle's stream records, defects and roll-up entries "
                    "under the last one and the recurring-class escalation "
                    "never accumulates. Editing state.json by hand records no "
                    "width at all, and every door that reads one then refuses."
                ),
            ),
        ),
        _GATE_THEN_PHASE_NOTE.lstrip("\n"),
    ),
    # lead-stalls GI-008 / FR-007 (D-019, D-040) — branched like the two
    # audited actions, and its idle steps are the recorded roster's.
    "run_streams": {
        "live": _STREAMS_RUNNING,
        "idle": _Imperative(
            (_Step(_EACH_UNRECORDED_STREAM),),
            "Each Agent step above is a stream this INSPECT's recorded roster "
            "requires and that has no record this cycle — the CONTEXT below "
            "lists the same streams — and the Agent steps go out together in "
            "ONE parallel message as BACKGROUND agents, so SIGHT can run in "
            "the main thread meanwhile. A stream that finished without its "
            "own record is in that list, because its own re-run is the one "
            "thing that records it. A stream's completion notification is the "
            "wake, and Foundry-Context shows the cycle's roll-up, which is "
            "where a stream's own record is confirmed.\n"
            "\n"
            # fallout AC-031 / GI-016 / FR-049 (D-169) — THE READING STREAMS
            # ARE TOLD THAT ONE OF THEIR PEERS REWRITES THE TREE. Driven in
            # cycle 5 of that run, TRACE hit an AttributeError that did not
            # reproduce at HEAD while TEST was reverting fixes in the shared
            # tree. Named rather than serialised, per GI-007: the parallel
            # dispatch is what makes an INSPECT one wall-clock unit. D-040
            # moved the order to pin INTO each reading stream's prompt.
            "THE TREE MOVES UNDER YOU WHILE THESE RUN. TEST verifies a "
            "GRIND's fixes by REVERTING each one and re-running, in the same "
            "working tree TRACE and PROVE are reading, so every reading "
            "stream's prompt above tells it to PIN ITS WORK TO A SNAPSHOT and "
            "cite that sha. A finding read off the live tree during an INSPECT "
            "may be a peer's mutation that is about to be reverted, and that "
            "is indistinguishable in the ledger from an honest one.\n"
            "\n"
            "fallout FR-023 / FR-049 / GI-016 — YOU DO NOT RECORD A STREAM. "
            "THE AGENT DOES.\n"
            "Every verifying stream calls Foundry-Stream itself, with the "
            "counts it actually measured, and a second record for the same "
            "(stream, cycle) REPLACES the first rather than summing with it. "
            "A lead that records on an agent's behalf is asserting numbers it "
            "did not measure, and when the agent then records its own the "
            "cycle carries two accounts of one run.\n"
            # fallout AC-031 / GI-016 / AC-030 (D-165) — AND THE ONE STREAM
            # WHOSE EXECUTOR IS YOU. SIGHT runs in the lead's own thread, so
            # the rule above, stated unqualified, closed every exit it had.
            "\n"
            "SIGHT IS THE ONE STREAM YOU EXECUTE, SO YOU ARE ITS EXECUTOR AND "
            "ITS RECORD IS YOURS. Playwright runs only in the main thread; "
            "there is no sight agent to re-dispatch and you must not spawn "
            "one. When you have driven the sight skill, the numbers you "
            "report are numbers YOU measured, which is the whole of what the "
            "rule above protects — it forbids recording on an AGENT's behalf, "
            "and there is no agent here. Every OTHER stream records its own "
            "and you only confirm.",
        ),
    },
    # lead-stalls GI-008 / FR-007 (D-055) — the dispatch is a slot, so the
    # clean-cycle GRIND a held escalation owes is this list without it.
    "transition_to_grind": _Imperative(
        (
            _Step("Foundry-Tasks"),
            _Step("Foundry-Gate", "phase='grind'"),
            _Step("Foundry-Phase", "phase='grind_start'"),
            _Step(_BLOCKING_DISPATCH),
        ),
        # fallout FR-038 / GI-021 / CT-008 / AC-002 — the alignment block
        # reaches the prompt through the step that builds it: step (1) is the
        # Foundry-Tasks call the block comes back on.
        "Same foreground rule as CAST: GRIND teammates are never "
        "background-spawned." + _GATE_THEN_PHASE_NOTE,
    ),
    # lead-stalls FR-015 / GI-008 / CT-003 / CT-008 — THE SAME TREATMENT AS
    # `build_castings`, AND THE BARE `WAIT.` IS GONE. With nobody running this
    # action is emitted only while blocking defects are open, and the crossing
    # the old arm trailed belongs to `transition_to_inspect`.
    "fix_defects": {
        "live": _GRIND_TEAMMATES_LIVE,
        "idle": _GRIND_DISPATCH,
    },
    # lead-stalls FR-007 — this read "(1) Foundry-Phase(phase='inspect_clean')
    # (2) Foundry-Gate(phase='assay') (3) Update state to F4": the gate after
    # the transition it guards (GATE_TO_TRANSITION maps `assay` to
    # `inspect_clean`), and a third step naming no tool at all. The
    # transition itself is what enters F4.
    "transition_to_assay": _Imperative(
        (
            _Step("Foundry-Gate", "phase='assay'"),
            _Step(
                "Foundry-Phase", "phase='inspect_clean'",
                note="the transition that enters F4 (ASSAY)",
            ),
            _Step(
                "Agent",
                "subagent_type='foundry:assayer', prompt='Assay requirement "
                "group N of 4 for the active foundry run. Spec-before-code; "
                "default posture is find the failure.'",
                each="four times, N = 1 to 4, all in a SINGLE message",
            ),
        ),
        "The assayer's frontmatter carries model=opus and effort=max."
        + _GATE_THEN_PHASE_NOTE,
    ),
    "run_assay": _Imperative(
        (
            _Step(
                "Agent",
                "subagent_type='foundry:assayer', prompt='Assay requirement "
                "group N of 4 for the active foundry run. Spec-before-code; "
                "default posture is find the failure.'",
                each="four times, N = 1 to 4, all in a SINGLE message",
            ),
        ),
        "Each reads the spec FIRST, forms expectations, then reads code. The "
        "assayer's frontmatter carries model=opus and effort=max.",
    ),
    # fallout FR-035 / AC-054 — THE F6 ORDER, STATED EXACTLY. The DONE
    # evaluation REQUIRES the generated report and sweeps the committed
    # evidence corpus, so Report, Gate, strip, Phase is the only order in
    # which each step's precondition still holds when the next one runs.
    "transition_to_done": _Imperative(
        (
            _Step(
                "Foundry-Report",
                note=(
                    "DONE is refused without the generated report, and it is "
                    "generated, never hand-written."
                ),
            ),
            _Step(
                "Foundry-Gate", "phase='done'",
                note=(
                    "this is where the evidence corpus is re-executed at HEAD. "
                    "It must pass BEFORE the strip, on the tree the corpus was "
                    "captured against."
                ),
            ),
            _Step(
                "Bash",
                "command='git rm -r --cached evidence/ && rm -rf evidence/'",
                note="the strip, AFTER the gate has judged the corpus and not before.",
            ),
            _Step(
                "Bash",
                "command='git commit -m \"chore: strip the evidence corpus\"'",
                note="commits the strip.",
            ),
            _Step(
                "Foundry-Phase", "phase='done'",
                note=(
                    "seals F6, carries your appended prose into the report and "
                    "archives the run."
                ),
            ),
        ),
        "Stripping before (2) refuses the gate for a corpus that is no longer "
        "there; stripping after (5) leaves the run sealed against a tree that "
        "no longer exists." + _GATE_THEN_PHASE_NOTE,
    ),
    # fallout FR-035 / AC-054 — the nine the survey counted, each of which fell
    # through to the generic header. The invariant test derives the emitted
    # set from `_compute_next_action`'s own AST.
    "transition_to_temper": _Imperative(
        (
            _Step("Foundry-Gate", "phase='temper'"),
            _Step(
                "Foundry-Phase", "phase='temper'",
                note=(
                    "that call enters F5, records TEMPER's own INSPECT at FULL "
                    "width and sweeps the whole evidence corpus. Editing "
                    "state.json by hand leaves the phase's first INSPECT with "
                    "no recorded mode."
                ),
            ),
        ),
        _GATE_THEN_PHASE_NOTE.lstrip("\n"),
    ),
    # lead-stalls GI-008 / FR-007 (D-050) — THE LIST ENDS IN THE GATE OUT OF
    # F5, BECAUSE THAT GATE'S RECORD IS HOW THE ROUTER KNOWS TEMPER RAN.
    # This list was the Skill alone, and the exit sat in the CONTEXT as "When
    # clean, call Foundry-Gate(phase='done'), update to F6." The Foundry-Next
    # owed after the Skill re-served the Skill, so no sequence of served lists
    # reached F6. The ledgers cannot answer "has TEMPER run" — a pass that
    # filed nothing leaves them as ASSAY left them, and `temper` is not a
    # stream wire id — so the list's last step is the crossing gate, whose
    # `.gate-passed` record only the server's own evaluation in F5 can write
    # (every transition unlinks it). `{gate}` comes on `details["crossing"]`.
    # Foundry-Report comes before it because the DONE gate refuses without one.
    "run_temper": _Imperative(
        (
            _Step(
                "Skill", "skill='foundry:temper'",
                note=(
                    "it spawns the TEMPER micro-domain agents, which zoom into "
                    "individual functions, single pages and specific flows and "
                    "ask whether they actually work."
                ),
            ),
            _Step(
                "Foundry-Report",
                note=(
                    "generated from the ledgers TEMPER has just written, and "
                    "the DONE gate refuses without it."
                ),
            ),
            _Step(
                "Foundry-Gate", "phase='{gate}'",
                note=(
                    "the gate out of F5. Its record is what the next "
                    "Foundry-Next reads to serve the crossing; a blocking "
                    "defect TEMPER filed is served the GRIND crossing instead."
                ),
            ),
        ),
        "TEMPER's roster is the open TEMPER_CANDIDATE observations plus its "
        "own micro-domains. Each agent records its OWN run with Foundry-Stream "
        "and files what it finds; a probe driven and found clean is a result, "
        "not a blank.",
    ),
    "transition_to_nyquist": _Imperative(
        (
            _Step("Foundry-Gate", "phase='nyquist'"),
            _Step(
                "Foundry-Phase", "phase='nyquist'",
                note=(
                    "that call enters F5.5 and sweeps the whole evidence "
                    "corpus first. It is refused unless this run was started "
                    "with --nyquist and every requirement is VERIFIED."
                ),
            ),
        ),
        _GATE_THEN_PHASE_NOTE.lstrip("\n"),
    ),
    # lead-stalls GI-008 / FR-007 (D-050) — the `run_temper` shape, for the
    # same reason: nothing an auditor writes is a server record, so the list
    # ends in the gate out of F5.5 and the router reads that. `done` is
    # accepted from F5.5 on a --nyquist run, so the crossing it leads to is
    # `transition_to_done` itself.
    "run_nyquist": _Imperative(
        (
            _Step(
                "Agent", "subagent_type='foundry:nyquist-auditor'",
                each=(
                    "for each batch of 5 VERIFIED requirements, all in a "
                    "SINGLE parallel message"
                ),
            ),
            _Step(
                "Foundry-Report",
                note="the DONE gate refuses without the generated report.",
            ),
            _Step(
                "Foundry-Gate", "phase='done'",
                note=(
                    "the gate out of F5.5. Its record is what the next "
                    "Foundry-Next reads to serve the crossing; a blocking "
                    "defect filed from an ESCALATE_IMPL_BUG result is served "
                    "the GRIND crossing instead."
                ),
            ),
        ),
        "Each classifies COVERED / UNTESTED / UNDERTESTED, generates minimal "
        "behavioural tests, runs them and commits the passing ones. An "
        "ESCALATE_IMPL_BUG result starts a new GRIND cycle. An untested "
        "requirement is never marked as passing.",
    ),
    # lead-stalls GI-008 / FR-007 (D-053) — the filing ASSAY's verdicts may
    # still owe is the list's first step, so the list succeeds on the state it
    # is served on (see `_EACH_UNFILED_VERDICT`).
    "assay_failed_loop_back": _Imperative(
        (
            _Step(_EACH_UNFILED_VERDICT),
            _Step("Foundry-Tasks"),
            _Step("Foundry-Gate", "phase='grind'"),
            _Step(
                "Foundry-Phase", "phase='assay_fail'",
                note=(
                    "the ASSAY-rejection door into F3. It is the same "
                    "transition `grind_start` is, through the other door, and "
                    "it is bounded by the same --max-cycles cap."
                ),
            ),
        ),
        _GATE_THEN_PHASE_NOTE.lstrip("\n"),
    ),
    "widen_inspect": _Imperative(
        (
            _Step("Foundry-Gate", "phase='inspect_start'"),
            _Step(
                "Foundry-Phase", "phase='inspect_start'",
                note=(
                    "AGAIN, from F2. The DELTA cycle came back clean, which "
                    "earns the widening re-open rather than the ASSAY gate: "
                    "that crossing advances the cycle counter, sweeps the "
                    "whole evidence corpus, records FULL and requires the full "
                    "roster. Then run every stream it names — a spot check is "
                    "not a FULL INSPECT."
                ),
            ),
        ),
        _GATE_THEN_PHASE_NOTE.lstrip("\n"),
    ),
    "record_inspect_width": _Imperative(
        (
            _Step("Foundry-Gate", "phase='inspect_start'"),
            _Step(
                "Foundry-Phase", "phase='inspect_start'",
                note=(
                    "from F2. This INSPECT has no recorded width, so the "
                    "roster, the rule and the evidence sweep it was opened "
                    "with are all unknown, and every door that reads the width "
                    "refuses. Do NOT edit state.json by hand — the transition "
                    "is what records the decision."
                ),
            ),
        ),
        _GATE_THEN_PHASE_NOTE.lstrip("\n"),
    ),
    # lead-stalls GI-008 / FR-007 (D-055) — THE RUN STATE NO ACCEPTED
    # TRANSITION LEAVES, ANSWERED AS ONE.
    #
    # A class still ESCALATED holds DONE shut until ST-001's clean arm or
    # ST-002's budget arm clears it, and the clean arm moves only at an
    # `inspect_start` crossing that advances the cycle counter. With no defect
    # open, from a FULL F2 or from anywhere past ASSAY, the server accepts no
    # such crossing: `grind_start` / `assay_fail` refuse "No open defects to
    # grind", `inspect_start` from a FULL F2 refuses "nothing to widen" and is
    # not accepted from F4 on. Driven, the lists served there instead —
    # `transition_to_assay`, then `transition_to_done` or `run_temper` —
    # each ended in a refused gate and came back identical, forever, and the
    # CONTEXT named `grind_start` then `inspect_start` from F2, both refused.
    #
    # Any list served here would be refused, so this answers NONE and says why,
    # in the register of `done` and `halted`. The graph change that gives the
    # state an exit is not this module's (a Foundry-Concern names it);
    # meanwhile the operator's escalation override is the one way out, and it
    # is the operator's, never a step of the lead's list — the CONTEXT names
    # its text the way `_escalation_notice` does.
    "escalation_held": _Imperative((), (
        "A defect class is still ESCALATED, DONE is refused until it is "
        "CLEARED, and from this run state no transition the server accepts "
        "can clear it — the CONTEXT below names the class, the refusals and "
        "the operator's override. That is a gap in the server's transition "
        "graph, the error ending the rules above name, and not a step left to "
        "you: do NOT file a defect to reopen a GRIND, do NOT call "
        "Foundry-Phase, do NOT call Foundry-Next in a loop. Tell the user the "
        "run is held on that escalation, and end your turn: nothing but the "
        "user moves this run from here."
    )),
    "done": _Imperative((), (
        "This run is DONE. Read REPORT.md and tell the user what shipped. Do "
        "NOT dispatch a wave, do NOT call Foundry-Phase, do NOT call "
        "Foundry-Next in a loop. Start a NEW run with Foundry-Init if there is "
        "more work."
    )),
    "unknown": _Imperative(
        (
            _Step(
                "Foundry-Context",
                note=(
                    "the guidance engine does not recognise this run's phase, "
                    "which means `state.json` carries a value no transition "
                    "writes."
                ),
            ),
            _Step("Foundry-Next"),
        ),
        "Do NOT guess a transition token — an unrecognised phase is a state to "
        "diagnose, not one to advance out of.",
    ),
}


def _template_steps(steps: tuple[_Step, ...]) -> tuple[_Step, ...]:
    """``steps`` with every slot step expanded to all it can name."""
    return tuple(
        expanded for step in steps for expanded in _expanded_steps(step, None)
    )


def _template(imperative: _Imperative) -> str:
    return _render_imperative(
        imperative._replace(steps=_template_steps(imperative.steps))
    )


#: The lead-facing TEMPLATE table, rendered from `_IMPERATIVES`: a branched
#: entry holds every branch between `_BRANCH_OPEN` / `_BRANCH_CLOSE` markers,
#: so the prose sweeps keep seeing all of it (see the section note above).
_ACTION_IMPERATIVES: dict[str, str] = {
    action: (
        "".join(
            _BRANCH_OPEN + name + _BRANCH_CLOSE + _template(branch)
            for name, branch in entry.items()
        )
        if isinstance(entry, dict) else _template(entry)
    )
    for action, entry in _IMPERATIVES.items()
}


def _chosen_imperative(action: str, liveness: object) -> _Imperative | None:
    """The one `_Imperative` the lead receives for ``action``, before slots."""
    entry = _IMPERATIVES.get(action)
    if isinstance(entry, dict):
        return entry[_branch_name(entry, _branch_states(liveness))]
    return entry


def _emitted_imperative(
    action: str,
    details: dict,
    run_name: str = "",
    phase: str = "",
    liveness: object = None,
    cycle: object = None,
) -> tuple[tuple[_Step, ...] | None, str]:
    """``(steps, header)`` for the lead: the chosen branch, expanded and
    substituted, and its rendering. ``(None, generic header)`` for an action
    with no entry or an unresolved crossing.

    Substitutes `{run}` with the active run slug so team names
    (cast-{run}-wave-{wave}, grind-{run}-cycle-{cycle}) are distinguishable
    across concurrent runs, and `active` when no run is active.

    fallout D-058 — ``{gate}`` / ``{token}`` ARE SUBSTITUTED FROM THE CROSSING
    THE EMITTING PHASE NAMES, from one `_ACTION_CROSSINGS` row, and a
    placeholder that does not resolve takes the generic fallback: printing a
    literal `{gate}` would hand the lead a call it would try to make.

    lead-stalls FR-015 / GI-008 / CT-008 — ``liveness`` CHOOSES THE BRANCH,
    before and outside that fallback. It is the `_waiting_on_agents` result the
    caller already measured, passed rather than recomputed so the payload the
    lead sees and the imperative it is given are one reading. `_branch_name` is
    total, like `_halt_cause`, so no reading sends a branched entry to the
    generic "Execute the first tool call mentioned. Do not deliberate."

    lead-stalls D-009 / D-012 / D-020 — ``{wave}``, ``{built_wave}`` and
    ``{casting}`` come off that same reading, and ``{cycle}`` is the run-level
    counter the caller read once; all four helpers are total, so no reading
    leaves one literal. `{wave}` is replaced before `{built_wave}` and cannot
    chew on it — the character before `wave}` there is an underscore.

    lead-stalls D-040 — the stream placeholder expands from
    ``details["missing_streams"]``, the roster the `run_streams` arm published
    beside this header.
    """
    imperative = _chosen_imperative(action, liveness)
    if imperative is None:
        return None, _generic_header(action)
    return _resolved_imperative(
        imperative, action, details, run_name, phase, liveness, cycle,
    )


def _generic_header(action: str) -> str:
    return (
        f"YOUR NEXT CALL: follow the CONTEXT below (action='{action}'). "
        "Execute the first tool call mentioned. Do not deliberate."
    )


def _resolved_imperative(
    imperative: _Imperative,
    action: str,
    details: dict,
    run_name: str,
    phase: str,
    liveness: object,
    cycle: object,
) -> tuple[tuple[_Step, ...] | None, str]:
    """`_emitted_imperative`'s second half, for one given `_Imperative`: its
    steps expanded and substituted, and its rendering. Split out so the audit
    can render every declared branch through the one resolver the lead's
    header takes, and name which branch arrived by equality.

    lead-stalls GI-008 / FR-007 (D-050) — a crossing that turns on the run's
    FLAGS rather than on the emitting phase comes on ``details["crossing"]``,
    the same `{"gate", "token"}` row shape: `run_temper` ends in the gate out
    of F5, which is `nyquist` on a --nyquist run and `done` otherwise, and F5
    is the only phase it is emitted from. A row that is absent still takes the
    generic fallback below, as an unresolved phase-keyed row does."""
    crossing = (
        _ACTION_CROSSINGS.get(action, {}).get(phase)
        or (details or {}).get("crossing")
        or {}
    )
    halt_cause = _halt_cause((details or {}).get("halted_reason_member"))

    def resolve(text: str) -> str:
        text = (
            text
            .replace("{wave}", _cast_wave(liveness))
            .replace("{built_wave}", _built_cast_wave(liveness))
            .replace("{cycle}", _grind_cycle(cycle))
            .replace("{casting}", _casting_slot(liveness))
            .replace("{halt_cause}", halt_cause)
        )
        if crossing:
            text = (
                text
                .replace("{gate}", crossing["gate"])
                .replace("{token}", crossing["token"])
            )
        return text.replace("{run}", run_name or "active")

    steps = [
        expanded for step in imperative.steps
        for expanded in _expanded_steps(step, details or {})
    ]
    resolved = tuple(
        step._replace(
            args=None if step.args is None else resolve(step.args),
            each=resolve(step.each),
            note=resolve(step.note),
        )
        for step in steps
    )
    header = _render_imperative(
        _Imperative(resolved, resolve(imperative.trailer))
    )
    if "{gate}" in header or "{token}" in header:
        return None, _generic_header(action)
    return resolved, header


def _format_imperative_header(
    action: str,
    instructions: str,
    details: dict,
    run_name: str = "",
    phase: str = "",
    liveness: object = None,
    cycle: object = None,
) -> str:
    """The 'YOUR NEXT CALL(S)' header for ``action`` — `_emitted_imperative`'s
    rendering. ``instructions`` is unused and kept for the callers that pass
    the CONTEXT beside it."""
    return _emitted_imperative(
        action, details, run_name=run_name, phase=phase, liveness=liveness,
        cycle=cycle,
    )[1]




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
    #
    # lead-stalls GI-008 / FR-007 — this notice rides in the
    # `transition_to_grind` CONTEXT, which names no call
    # (`_BRANCHED_ACTION_CONTEXT`), so the restore is named as the directive's
    # TEXT rather than as a Foundry-Directive call: it is the operator's
    # override, never a step of the lead's list.
    restores = "; ".join(
        f"`{text}`" if (text := _override_instruction(key)) is not None
        else _override_offer(key)
        for key in sorted(escalated)
    )
    return (
        f" ESCALATED: {len(escalated)} defect class(es) have recurred for "
        f"{ESCALATION_CYCLES}+ consecutive cycles ({names}). The Foundry-Tasks "
        "door emits ONE structural-fix packet per escalated class instead of "
        "per-instance packets, and that packet is ONE task, never split back "
        "apart. Every listed defect must still close. The Foundry-Directive "
        f"text that restores per-instance packets: {restores}."
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
        # Every open record, whatever its tier: the count
        # `Foundry-Gate(phase='grind')` refuses on when it is zero (D-053).
        "open": sum(len(ids) for ids in buckets.values()),
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
    fdir: Path, project_root: str, still: list[str], *, served: str
) -> str:
    """The facts about the classes ST-010 still holds DONE open for (D-129).

    D-129 put this sentence on the clean path: a class the server has
    PERSISTED as ESCALATED with every instance closed was named nowhere before
    the F6 door, and `_escalation_notice` (the sentence above) is blind to it
    twice over. It reads the same union `_done_preconditions` refuses on
    (``still``, from `_still_escalated_classes`), and carries
    `_escalation_exit_distances` so the reader sees how far each arm is.

    lead-stalls GI-008 / FR-007 (D-055) — IT STATES THE RUN STATE AND NAMES
    NO CROSSING OF ITS OWN.
    -------------------------------------------------------------------
    This rode in the CONTEXT of `transition_to_assay` saying "the cheapest
    place to make those crossings is HERE, from F2" and naming `grind_start`
    then `inspect_start` — a second sequence beside a header that served
    ASSAY, which the lead had to weigh against it. D-153 had chosen that
    spelling from the recorded width, and it was right only while some record
    was open: driven on a FULL F2 with the class's instances all fixed, both
    calls it named were refused, while the header's ASSAY list led on to a
    DONE gate that refused the same class forever.

    The router now serves the crossing itself (`_escalation_hold`,
    `widen_inspect`), so this says which list that is — ``served`` is
    ``"widen"``, ``"grind"`` or ``"held"``, chosen by the caller that chose the
    action — and the distances name the crossing by what it does, never as a
    call: a counter-advancing INSPECT crossing is the only event the clean arm
    counts, whichever door reaches it.
    """
    if not still:
        return ""
    names = ", ".join(still)
    closing = {
        "widen": (
            " The widening re-open the list above makes is one of those "
            "crossings."
        ),
        "grind": (
            " No blocking defect is open, so the list above opens a GRIND that "
            "dispatches nobody — the GRIND gate counts every open record, "
            "whatever its tier — and the INSPECT crossing that closes it is "
            "one of those crossings."
        ),
        "held": (
            " No defect is open for a GRIND to open on, and no other "
            "transition the server accepts from this phase advances the cycle "
            "counter: a FULL INSPECT has nothing to widen, and past ASSAY "
            "every road back to INSPECT runs through a GRIND. The operator's "
            "override that de-escalates a class, which the DONE guard "
            "honours, is the Foundry-Directive text "
            + "; ".join(
                f"`{text}`" if (text := _override_instruction(key)) is not None
                else _override_offer(key)
                for key in still
            )
            + "."
        ),
    }[served]
    return (
        f" ST-010: {len(still)} defect class(es) are still ESCALATED "
        f"({names}) and DONE is refused until every one of them is "
        "CLEARED — a LATENT-only backlog does not by itself clear a class."
        + _escalation_exit_distances(
            fdir, project_root, still, crossing=_CLEAN_CROSSING_PHRASE,
        )
        + closing
    )


#: How the distances name ST-001's crossing from a guidance surface: by what
#: it does, since which door reaches it depends on where the run stands and
#: the list above the CONTEXT already names that door (D-055).
_CLEAN_CROSSING_PHRASE = "each one an INSPECT crossing that advances the cycle counter"




def _escalation_hold(
    fdir: Path,
    project_root: str,
    phase: str,
    blocking: dict,
    opening: str,
    grind_config: dict,
) -> dict | None:
    """The answer for a clean run state a still-ESCALATED class holds DONE
    shut over, or ``None`` when no class is held or a blocking defect is open.

    lead-stalls GI-008 / FR-007 (D-055). A held class clears only at an
    `inspect_start` crossing that advances the cycle counter, so every list
    that walks on toward DONE — `transition_to_assay` from a FULL F2, and from
    F4, F5 and F5.5 the TEMPER, NYQUIST and DONE crossings — ends at a DONE
    gate that refuses the class, and the next Foundry-Next served it again.
    Driven, `run_temper` re-ran TEMPER every lap and `transition_to_done` ran
    its strip and commit after the gate refused.

    So wherever the run stands clean with a class held, this answers first,
    with the one crossing the server accepts from there:

      * some record is open (any tier; the GRIND gate counts them all)
                         -> `transition_to_grind` with its dispatch slot at
                            ``open_defects: 0``: Tasks, the GRIND gate and
                            `grind_start`, and then the F3 arm's
                            `transition_to_inspect` closes the cycle.
      * nothing is open  -> `escalation_held`: no accepted transition advances
                            the counter from here, and the header says so.

    A DELTA F2 is not asked: its `widen_inspect` re-open IS the crossing. With
    a blocking defect open the ordinary GRIND arms answer, and their crossing
    counts too. ``opening`` is the caller's own first sentence about the phase
    it measured; ``grind_config`` is the router's `GRIND_AGENT_CONFIG`.
    """
    if blocking["blocking"] > 0:
        return None
    still = _still_escalated_classes(fdir, project_root)
    if not still:
        return None
    backlog = (
        f" {len(blocking['latent'])} LATENT defect(s) stay open, tracked and "
        "named in the F6 backlog; they block nothing."
        if blocking["latent"] else ""
    )
    details = {
        "open_defects": 0,
        "live_defects": [],
        "unknown_tier_defects": [],
        "latent_backlog": blocking["latent"],
        "still_escalated_classes": still,
    }
    if blocking["open"] > 0:
        return {
            "phase": phase,
            "action": "transition_to_grind",
            "instructions": (
                opening + backlog
                + _still_escalated_notice(fdir, project_root, still, served="grind")
            ),
            "details": {**details, "agent_config": grind_config},
        }
    return {
        "phase": phase,
        "action": "escalation_held",
        "instructions": (
            opening + backlog
            + _still_escalated_notice(fdir, project_root, still, served="held")
        ),
        "details": details,
    }




def _carried_requirements(fdir: Path) -> dict[str, list[str]]:
    """``{requirement id: [open BLOCKING defect ids whose spec_ref names it]}``.

    lead-stalls GI-008 / FR-007 (D-054). The ASSAY-rejection arm published its
    filing only when the ledger held no open record at all, a count that never
    asked WHICH requirement a record carries — so an unrelated LATENT backlog
    item, or one assayer's filing of another requirement, suppressed the filing
    of every rejection. Driven: an open LATENT record on one requirement
    beside an unfiled PARTIAL on another, two full laps, and the rejection
    never reached the ledger.

    BLOCKING, not any tier, because the question is whether the GRIND the list
    opens has the rejection to work on, and a GRIND dispatches only for a
    blocking defect: the F3 arm answers `transition_to_inspect` the moment the
    blocking count is zero. A LATENT record naming the requirement leaves ASSAY
    rejecting it on the next lap with nothing dispatched in between, which is
    the lap D-054 drove. HARDENING refuses a `spec_ref` at filing, so it never
    carries one.

    `spec_ref` is prose — "GI-008, FR-007" is one — so the ids are read out of
    it with the one requirement-id grammar, `vocab.REQUIREMENT_ID_RE`, and the
    whole stripped value counts too, for an id that grammar does not spell.
    Total: a record with no string `spec_ref` carries nothing.
    """
    buckets = open_defects_by_tier(
        fdir, tiers=DEFECT_TIERS, unknown_tier=TIER_UNKNOWN, tier_of=defect_tier
    )
    carried: dict[str, list[str]] = {}
    for tier in BLOCKING_TIERS:
        for record in buckets.get(tier, []):
            ref = record.get("spec_ref")
            if not isinstance(ref, str) or not ref.strip():
                continue
            for rid in {*REQUIREMENT_ID_RE.findall(ref), ref.strip()}:
                carried.setdefault(rid, []).append(str(record.get("id", "?")))
    return carried




def _team_work_in_flight(
    reading: object, team_names: list, run_name: str, *, cast_open: bool
) -> bool:
    """Is a registered team still holding work, so `cleanup_teams` must wait?

    lead-stalls FR-002 / ST-002 / ST-004 (D-015 / D-017 / D-020). Asked of the
    SAME `_waiting_on_agents` reading the branched imperative is chosen from,
    and only where that reading was taken — F1 before the crossing and F3. In
    every other phase a registered team is stale by construction (the gates
    into them refuse an active team), so the arm keeps firing there exactly as
    it did.

    THE ASYMMETRY DECIDES EVERY UNCERTAIN CASE (lead-stalls D-013): a team
    left up one call too long costs a redundant call; a team torn down under
    running or owed work loses that work.

      * an agent is running                       -> in flight (both phases)
      * F1, a casting refused or owed acceptance  -> in flight: its teammate
        is the one who answers a refusal
      * F1, the pending wave's own team           -> in flight: Team-Up made,
        spawns not yet (`transition_to_cast` steps 4 to 6)
      * F1, no wave position measured             -> in flight
      * F1, a pending wave and an EARLIER wave's team -> finished: the next
        wave's Team-Up refuses while it stands
      * F1 every wave built, F3 nobody running    -> finished
    """
    if not isinstance(reading, dict):
        return False
    states = _branch_states(reading)
    if states[0] == "live":
        return True
    if not cast_open:
        return False
    # `redispatch` too (lead-stalls D-022 / D-028): the refused casting's own
    # team is not registered, but the arm fired on SOMETHING — another team, or
    # a live pane the scan cannot attribute — and tearing that down is not the
    # refusal's answer.
    if states[0] in ("refused", "redispatch", "unaccepted"):
        return True
    if states[0] == "idle":
        return False
    pending = reading.get("cast_wave_pending")
    if isinstance(pending, bool) or not isinstance(pending, int) or pending < 1:
        return True
    # The name the dispatch branch told the lead to create for this wave,
    # spelled from the same `{run}` / `{wave}` values the header substitutes.
    return f"cast-{run_name}-wave-{_cast_wave(reading)}" in team_names


def _compute_next_action(project_root: str) -> dict:
    """Internal: compute next action without directive overlay."""
    fdir = get_run_dir(project_root)

    if not fdir or not fdir.exists():
        return {
            "phase": "none",
            "action": "init",
            # lead-stalls GI-008 / FR-007 — this CONTEXT, like every one
            # below, states the run state and names no call: the numbered
            # list above it is the move (see `_BRANCHED_ACTION_CONTEXT`).
            "instructions": (
                "No active foundry run: no run directory is active in this "
                "project. The same door reopens an existing run with "
                "resume='<run-name>'."
            ),
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
                f"This run HALTED at cycle {halted_cycle if halted_cycle is not None else '?'} — "
                f"{halted_sentence}. "
                "HALTED is NOT DONE: this run stopped with open work. "
                # lead-stalls GI-008 / FR-007 — facts only. The report this
                # halt could not write is step (1) of the header above, served
                # from `report_generated` below (`_UNWRITTEN_REPORT`); this
                # block used to order that call itself, beneath a header that
                # said NONE and "The report is generated."
                + (
                    f"The report has been generated at {REPORT_MD_FILENAME} and "
                    "names every open defect by tier."
                    if report_present
                    else (
                        f"The report was NOT generated — "
                        f"{report_error or 'it is not present at ' + str(report_path)}"
                        ". The open work is recorded in defects.json whatever "
                        "the generator could not render."
                    )
                )
                + f" What remains is for the user: {hand_off}."
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

    # lead-stalls FR-002 / CT-003 / CT-006 / ST-002 / ST-004 (D-015..D-020) \u2014
    # THE ROSTER IS READ BEFORE THE TEAM ARM, AND THE TEAM ARM ASKS IT.
    #
    # This arm returned `cleanup_teams` for ANY registered team, ahead of the
    # F1 and F3 arms, and `transition_to_cast` / `transition_to_grind` order the
    # lead to register one before it spawns a single teammate. So for the whole
    # of a real wave the lead's `Foundry-Next` said "send shutdown to each
    # teammate, TeamDelete, Foundry-Team-Down" over teammates that were still
    # building; the branched `build_castings` / `fix_defects` answer, and the
    # `agent_liveness` it carries, were reachable only with NO team registered,
    # which is the one state the suite drove. `_WAITING_IS_NOT_STOPPING` then
    # told a woken lead to call Foundry-Next, which walked it straight here.
    #
    # The reading is taken ONCE, here, for the two phases whose answer depends
    # on it, published on the result and reused by `foundry_next_action`, so
    # the arm chosen, the imperative printed and the `agent_liveness` beside
    # them are one measurement of one roster.
    cast_open = phase == "F1" and not (fdir / CAST_COMPLETE_MARKER).exists()
    # lead-stalls D-028 — ONE team scan, read by the reading and by the arm.
    # Two scans could disagree (the first failing and reading as "no team",
    # or the registry changing between them), and the reading then chose
    # `redispatch` while the arm saw the refused casting's team registered.
    # A scan that cannot answer reads as "no team" for BOTH, on the D-013
    # asymmetry: a team left up one call longer costs a redundant call, while
    # an exception here would take Foundry-Next down with it.
    try:
        teams = _check_active_teams(project_root)
    except Exception:  # noqa: BLE001 - the router never raises into the lead
        teams = {"active": False, "teams": []}
    # lead-stalls D-056 — and F0, whose decomposition writers are background
    # agents the lead is woken by one at a time: the manifest cannot say
    # whether the writer that has not written yet is still writing.
    agent_liveness = (
        _waiting_on_agents(project_root, teams=teams)
        if cast_open or phase in ("F0", "F3") else None
    )

    if teams["active"] and not _team_work_in_flight(
        agent_liveness, teams.get("teams") or [], fdir.name, cast_open=cast_open,
    ):
        cleanup = {
            "phase": phase,
            "action": "cleanup_teams",
            "instructions": (
                f"Registered teams with no teammate of theirs running: "
                f"{', '.join(teams['teams'])}. Every idle or terminated pane "
                "IS the shutdown signal, and no shutdown_response, "
                "shutdown_ack or teammate reply is coming."
            ),
            "details": {"active_teams": teams["teams"]},
        }
        if agent_liveness is not None:
            cleanup["agent_liveness"] = agent_liveness
        return cleanup

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
        # lead-stalls GI-008 / FR-007 (D-056) — THE MANIFEST HOLDING A CASTING
        # IS NOT THE DECOMPOSITION BEING FINISHED. Each writer adds its own
        # entry when it is done, so the first writer to finish made this arm
        # answer `transition_to_cast` while the others were still writing,
        # and following it validated, entered F1 and dispatched wave 1 over a
        # partial manifest. The writers' progress ledgers answer instead: any
        # writer advancing is `add_castings`, whose `live` branch is END YOUR
        # TURN, whatever the manifest already holds.
        writing = bool((agent_liveness or {}).get("waiting"))
        if casting_count == 0 or writing:
            return {
                "phase": "F0",
                "action": "add_castings",
                "instructions": (
                    f"DECOMPOSE (F0): the castings manifest lists "
                    f"{casting_count} casting(s)"
                    + (
                        f", and {agent_liveness['count']} agent(s) are still "
                        "writing — this server read their progress ledgers "
                        "on this call."
                        if writing else " and no agent is writing."
                    )
                    + f" Every casting file goes under {fdir}/castings/, "
                    "never castings/ at the project root, and the "
                    "agent_config below is ENFORCED for each writer."
                    + _BRANCHED_ACTION_CONTEXT
                ),
                "details": {
                    "foundry_dir": str(fdir),
                    "casting_count": casting_count,
                    "agent_config": DECOMPOSE_AGENT_CONFIG,
                },
                "agent_liveness": agent_liveness,
            }
        return {
            "phase": "F0",
            "action": "transition_to_cast",
            "instructions": (
                f"DECOMPOSE complete: {casting_count} casting(s) in the "
                "manifest, and no agent is writing — this server read the "
                "progress ledgers on this call. One teammate builds one "
                "casting, never many."
            ),
            "details": {"casting_count": casting_count, "agent_config": CAST_AGENT_CONFIG},
            "agent_liveness": agent_liveness,
        }

    elif phase == "F1":
        if cast_open:
            return {
                "phase": "F1",
                "action": "build_castings",
                "instructions": (
                    "CAST phase (F1): this wave's castings are the work, and "
                    "`.cast-complete` is not written yet."
                    + _BRANCHED_ACTION_CONTEXT
                ),
                "details": {"agent_config": CAST_AGENT_CONFIG},
                "agent_liveness": agent_liveness,
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
                "CAST complete: `.cast-complete` is written. The `cast` "
                "transition is what enters F2, sweeps the evidence corpus and "
                "RECORDS this INSPECT's width (FULL, rule first_of_phase) and "
                "its roster; editing state.json to F2 by hand leaves the first "
                "INSPECT of the phase with no recorded mode. SIGHT runs in the "
                "MAIN THREAD, and the agent_configs below are the other "
                "streams'."
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
                # lead-stalls GI-008 / FR-007 — the reason, not the hint: the
                # hint is the remedy written out as calls, and the header
                # above is that remedy as the list the lead makes.
                "instructions": f"INSPECT phase: {streams['reason']}.",
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
                # lead-stalls GI-008 / D-024 — `run_streams` is BRANCHED (D-019),
                # so this block takes the `_BRANCHED_ACTION_CONTEXT` rule the two
                # other branched arms took in D-005: it names no next call. It
                # read "Missing: trace prove test ... Spawn agents using the
                # agent_configs below" directly beneath a `live` header saying
                # those streams were running and that spawning them again runs
                # them twice — one payload, two imperatives. A stream records
                # itself when it FINISHES, so "unrecorded" is what the count
                # measures, not "missing"; the header says which of running or
                # unspawned it is. The spawn rules live in the `idle` branch.
                "instructions": (
                    f"INSPECT phase ({streams.get('inspect_mode') or 'FULL'} width"
                    f"{', rule ' + streams['inspect_rule'] if streams.get('inspect_rule') else ''}): "
                    f"verification streams with no record this cycle: {streams['missing']}. "
                    f"Required this cycle: {', '.join(streams['required'])}. "
                    "The agent_configs below are ENFORCED (model and type) for "
                    "every stream agent. "
                    # fallout AC-031 / GI-016 (D-165) — the same qualification the
                    # `run_streams` imperative carries, on the OTHER surface that
                    # states this rule. Left unqualified here it would re-close the
                    # exits the imperative just opened, one field along, and this is
                    # the string a lead reads FIRST.
                    "Each AGENT stream records its OWN run with Foundry-Stream "
                    "(fallout GI-016); never record on an AGENT's behalf. SIGHT is "
                    "the exception and the reason is that you EXECUTED it: there is "
                    "no sight agent, so its record is yours to make from what you "
                    "measured. "
                    # fallout AC-031 (D-169) — and the hazard the parallel dispatch
                    # creates, stated as the rule every reading stream is bound by.
                    "TEST rewrites the shared tree to verify the GRIND's fixes, so "
                    "every reading stream is bound to pin its findings to the HEAD "
                    "sha and verify them against a snapshot, not the live tree."
                    + _BRANCHED_ACTION_CONTEXT
                ),
                "details": {
                    "missing_streams": streams["missing"].split(),
                    "required": streams["required"],
                    # GI-008 / CT-009: reported from the recorded decision.
                    "inspect_mode": streams.get("inspect_mode", ""),
                    "inspect_rule": streams.get("inspect_rule", ""),
                    "stream_scope": streams.get("stream_scope", {}),
                    # lead-stalls D-040 — one config per AGENT stream the
                    # recorded roster requires, from the row its step reads,
                    # so `test01` has one. SIGHT is the lead's own skill.
                    "agent_configs": {
                        "trace": INSPECT_TRACE_CONFIG,
                        "prove": INSPECT_PROVE_CONFIG,
                        "test": {
                            **agent_model("general-purpose", baseline="opus"),
                            "subagent_type": "general-purpose",
                        },
                        **{
                            stream: _stream_agent_config(stream)
                            for stream in streams["required"]
                            if stream not in ("trace", "prove", "test", "sight")
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
        # lead-stalls D-055: at DELTA width the widening re-open served below
        # is itself a crossing that counts, so the notice only says so.
        still = _still_escalated_classes(fdir, project_root)
        if f2_mode.get("mode") == "DELTA":
            return {
                "phase": "F2",
                "action": "widen_inspect",
                "instructions": (
                    f"INSPECT clean at DELTA width (cycle {f2_mode.get('cycle', '?')}, "
                    f"rule {f2_mode.get('rule', '?')}): zero blocking defects."
                    + carried
                    + _still_escalated_notice(
                        fdir, project_root, still, served="widen",
                    )
                    # D-169: the condition stated is the one both ASSAY doors
                    # evaluate — the recorded MODE — not the rule. A FULL
                    # INSPECT recorded with rule verifier_touched opens ASSAY,
                    # and telling the lead otherwise buys a widening cycle
                    # nothing asked for.
                    + " ASSAY is only opened by an INSPECT whose recorded mode "
                    "is FULL, and `inspect_start` from F2 is the widening "
                    "re-open: it advances the cycle counter, sweeps the WHOLE "
                    "evidence corpus, records FULL and names the full roster."
                ),
                "details": {
                    "open_defects": 0,
                    "latent_backlog": latent_backlog,
                    # D-129: machine-readable beside the sentence, so a reader
                    # never has to parse prose to learn what still blocks DONE.
                    "still_escalated_classes": still,
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

        opening = (
            "INSPECT clean: zero blocking defects, at "
            f"{f2_mode.get('mode') or 'FULL'} width "
            f"(rule {f2_mode.get('rule') or 'unrecorded'})."
        )
        # lead-stalls GI-008 / FR-007 (D-055) — A HELD CLASS DOES NOT GO ON TO
        # ASSAY. ASSAY, TEMPER and NYQUIST all lead to a DONE gate that refuses
        # it, and from past ASSAY every road back to the one crossing that
        # clears it is longer than from here.
        held = _escalation_hold(
            fdir, project_root, "F2", blocking, opening, GRIND_AGENT_CONFIG,
        )
        if held is not None:
            held["details"].update(
                inspect_mode=f2_mode.get("mode", ""),
                inspect_rule=f2_mode.get("rule", ""),
            )
            return held
        return {
            "phase": "F2",
            "action": "transition_to_assay",
            "instructions": (
                opening
                + carried
                + " The agent_config below is the assayer's, and its "
                "frontmatter carries opus and effort=max."
            ),
            "details": {
                "open_defects": 0,
                "latent_backlog": latent_backlog,
                # D-129, same field on the arm that opens ASSAY — empty on
                # every route here now, because a held class is answered above.
                "still_escalated_classes": still,
                "inspect_mode": f2_mode.get("mode", ""),
                "inspect_rule": f2_mode.get("rule", ""),
                "agent_config": ASSAY_AGENT_CONFIG,
            },
        }

    elif phase == "F3":
        # lead-stalls FR-002 / CT-003 — A GRIND TEAMMATE STILL RUNNING HOLDS
        # THE PHASE, WHATEVER THE COUNT SAYS.
        #
        # Teammates record their own fixes with `Foundry-Fix` and THEN write
        # their report and their done line, so the count reaches zero while the
        # last of them is still committing. A lead woken in that window by an
        # earlier teammate's notification was handed `transition_to_inspect` —
        # shut the grind team down — over the one still writing. The branched
        # answer is owed until nobody is running, and with a count of zero only
        # its `live` branch is reachable, so `_GRIND_DISPATCH`'s "blocking
        # defects are open" stays true wherever it is printed.
        grind_live = bool((agent_liveness or {}).get("waiting"))
        if open_count > 0 or grind_live:
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
            #
            # lead-stalls D-005 — AND IT NO LONGER NAMES THE CROSSING EITHER.
            # `fix_defects` is a BRANCHED action, so the imperative printed
            # above this block is chosen from the roster; this block used to
            # carry "Teammates are fixing. Wait for completion." beside it —
            # a bare wait naming no mechanism, under a header that had just
            # said END YOUR TURN — and then a teardown-plus-
            # `Foundry-Phase(phase='inspect_start')` sequence with no
            # `Foundry-Gate(phase='inspect_start')` in it. Identical to the F1
            # arm's defect, in the sibling. `_BRANCHED_ACTION_CONTEXT` states
            # the rule once for both; the crossing is named by
            # `_ACTION_IMPERATIVES["transition_to_inspect"]`, which is what
            # this router returns once `open_count` is zero and no GRIND agent
            # is running, and
            # it takes its gate and its token from one `_ACTION_CROSSINGS`
            # row so the two can never disagree.
            #
            # What STAYS is what the imperative cannot carry: the measured
            # counts, the recorded width of the cycle just verified, and the
            # `Foundry-Fix` argument shape above — bookkeeping for a call the
            # lead makes per defect, not a next-call sequence.
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
                        "block nothing, and the F6 backlog carries every one "
                        "still open. "
                        if latent_backlog else ""
                    )
                    # lead-stalls GI-008 / FR-007 — the Foundry-Fix argument
                    # shape, stated as a fact about the door rather than as a
                    # call written out in call syntax beside the header's list.
                    + "Each fix is recorded through the Foundry-Fix door, "
                    "whose required arguments are defect_id, cycle and "
                    "authored_by: authored_by is 'teammate' (with the "
                    "prompt_hash and casting_id it was dispatched for) or "
                    "'lead' (with fix_commit). A LIVE or untiered defect also "
                    "takes adjacent_path_statement and adjacent_path_test; a "
                    "LATENT defect takes regression_test alone — the "
                    "adjacent-path pair is NOT demanded there. "
                    f"(The cycle just verified ran {f3_mode.get('mode') or 'FULL'} "
                    f"width, rule {f3_mode.get('rule') or 'unrecorded'}.)"
                    + _BRANCHED_ACTION_CONTEXT
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
                "agent_liveness": agent_liveness,
            }
        return {
            "phase": "F3",
            "action": "transition_to_inspect",
            # Measured to decide this arm, so published like the one above:
            # the stall notice reads it rather than taking a second reading.
            "agent_liveness": agent_liveness,
            # lead-stalls D-054 / D-055 — "all defects fixed" was false with
            # a LATENT record open, which is this arm's ordinary state after
            # the clean-cycle GRIND `_escalation_hold` serves: nothing blocks,
            # so nothing is dispatched, and the backlog is still there.
            "instructions": (
                "GRIND complete: no blocking defect is open and no GRIND agent "
                "is running."
                + (
                    f" {len(latent_backlog)} LATENT defect(s) stay open and "
                    "block nothing."
                    if latent_backlog else ""
                )
                + " The crossing back into F2 is `inspect_start`, "
                "guarded by the gate of the same name — the `inspect` gate "
                "guards the F1 entry and is refused from F3. That transition "
                "advances the run's cycle counter, so without it every later "
                "record is stamped with the previous cycle, and it DECIDES the "
                "next INSPECT's width and roster: every stream on a FULL "
                "cycle, a reduced roster on a DELTA one, where running more is "
                "wasted rather than safer. The width is the server's call."
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
                    "yet to pass. Each assayer records its verdicts through "
                    "the Foundry-Verdict door. An empty verdict ledger is not "
                    "a passing ASSAY: a run that left F4 from here would carry "
                    "zero requirements verified, and nothing catches that "
                    "until the DONE gate, a whole phase later."
                ),
                # No `agent_config`: `foundry:assayer` holds its own opus /
                # effort=max frontmatter pin, so this site emits nothing and
                # lets the pin govern — the same reason `_nyquist_transition`
                # gives for the auditor.
                "details": {"non_verified": 0, "total": 0},
            }

        if non_verified > 0:
            # lead-stalls GI-008 / FR-007 (D-053) — TWO STATES, TOLD APART BY
            # THE COUNT THE GRIND GATE REFUSES ON.
            #
            # This arm chose the action from the verdict count alone and left
            # the filing in the CONTEXT ("Sync findings as defects
            # (Foundry-Sync)"). A verdict files no defect, so with nothing open
            # the served list's gate refused "No open defects to grind", its
            # phase call was refused behind it, and the next Foundry-Next
            # served the same list: the run never left F4. The requirements
            # whose verdicts nothing carries are published instead, and the
            # header's first step files them (`_EACH_UNFILED_VERDICT`).
            #
            # lead-stalls GI-008 / FR-007 (D-054) — PER REQUIREMENT, AND ONLY A
            # BLOCKING DEFECT CARRIES ONE. This published the filing only when
            # the ledger held no open record at all, so one unrelated LATENT
            # item — which the F2 arm lets through to ASSAY because it blocks
            # nothing — suppressed the filing of every rejection, the gate
            # passed on that item, the GRIND dispatched nobody, and ASSAY
            # rejected the same requirement on the next lap: driven, two full
            # laps and the rejection never filed. `_carried_requirements`
            # answers for each requirement, and the CONTEXT's counts come from
            # the same reading, so the sentence cannot claim a carrier the list
            # lacks.
            carried_by = _carried_requirements(fdir)
            rejected = [
                str(r.get("id"))
                for r in verdicts.get("requirements", [])
                if r.get("verdict") != "VERIFIED"
            ]
            unfiled = [rid for rid in rejected if rid not in carried_by]
            carriers = sorted({
                did for rid in rejected for did in carried_by.get(rid, [])
            })
            carried_count = len(rejected) - len(unfiled)
            return {
                "phase": "F4",
                "action": "assay_failed_loop_back",
                "instructions": (
                    f"ASSAY found {non_verified}/{total} non-verified "
                    "requirements. "
                    + (
                        f"{carried_count} of them are carried by open "
                        f"blocking defect(s) ({', '.join(carriers)}). "
                        if carriers else ""
                    )
                    + (
                        f"{len(unfiled)} are carried by no open blocking "
                        f"defect ({', '.join(unfiled)}), so the list above "
                        "opens by filing them."
                        if unfiled else
                        "No requirement ASSAY rejected is left without one."
                    )
                    + " The ASSAY-rejection door into F3 clears every stream "
                    "marker and is bounded by the same --max-cycles cap "
                    "`grind_start` is, and the whole verification stack "
                    "re-runs after the GRIND: a FULL INSPECT, then ASSAY "
                    "again. NO SPOT CORRECTIONS."
                ),
                "details": {
                    "non_verified": non_verified, "total": total,
                    "unfiled_verdicts": unfiled,
                    "carrying_defects": carriers,
                    "agent_config": GRIND_AGENT_CONFIG,
                },
            }

        # lead-stalls GI-008 / FR-007 (D-055) — every crossing below walks on
        # toward a DONE gate that refuses a held class, so a held class is
        # answered first, while the road back to its crossing is shortest.
        held = _escalation_hold(
            fdir, project_root, "F4",
            blocking, "ASSAY passed: all requirements verified.",
            GRIND_AGENT_CONFIG,
        )
        if held is not None:
            return held

        temper = state.get("temper", False)
        if temper:
            return {
                "phase": "F4",
                "action": "transition_to_temper",
                "instructions": (
                    "ASSAY passed: all requirements verified. --temper is set, "
                    "so F4 routes to F5 (TEMPER micro-domain stress testing)."
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
                "ASSAY passed: all requirements verified. Neither --temper "
                "nor --nyquist is set, so F4 crosses to F6 (DONE)."
            ),
            "details": {},
        }

    elif phase == "F5":
        # A --temper --nyquist run reaches F5.5 from here; --temper alone goes
        # straight to F6. Same guard as the F4 path so the two options compose.
        # lead-stalls GI-008 / FR-007 (D-050) — and the crossing is SERVED, as
        # a step list, once `run_temper`'s own list has ended in its gate.
        gate = "nyquist" if state.get("nyquist", False) else "done"
        # lead-stalls D-055 — `run_temper`'s list ends in a gate whose road
        # leads to a DONE gate that refuses a held class, so the Skill was
        # re-served every lap. Answered before the phase's work and before
        # its crossing, after blocking defects, which `_escalation_hold`
        # leaves to `_post_assay_crossing`'s GRIND arm.
        held = _escalation_hold(
            fdir, project_root, "F5", blocking,
            "TEMPER phase: no blocking defect is open.", GRIND_AGENT_CONFIG,
        )
        if held is not None:
            return held
        crossing = _post_assay_crossing(
            fdir, "F5", "TEMPER", gate, GRIND_AGENT_CONFIG,
        )
        if crossing is not None:
            return crossing
        return {
            "phase": "F5",
            "action": "run_temper",
            "instructions": (
                "TEMPER phase: micro-domain stress testing — at least 15 "
                "domains, each probed, with cross-domain tests and a "
                "continuous sweep over them. What TEMPER files is fixed through the "
                "GRIND \u2192 INSPECT \u2192 ASSAY loop. The list above ends in the "
                f"gate out of F5, `{gate}`, and the next Foundry-Next reads "
                "that gate's record beside the defect ledger to serve the "
                "crossing."
            ),
            "details": {"crossing": {"gate": gate, "token": gate}},
        }

    elif phase == "F5.5":
        # lead-stalls D-055 — `run_nyquist` ends in the DONE gate itself.
        held = _escalation_hold(
            fdir, project_root, "F5.5", blocking,
            "NYQUIST phase: no blocking defect is open.", GRIND_AGENT_CONFIG,
        )
        if held is not None:
            return held
        crossing = _post_assay_crossing(
            fdir, "F5.5", "NYQUIST", "done", GRIND_AGENT_CONFIG,
        )
        if crossing is not None:
            return crossing
        return {
            "phase": "F5.5",
            "action": "run_nyquist",
            "instructions": (
                "NYQUIST phase: regression tests for VERIFIED requirements that "
                "lack automated coverage, batched by 5 with one "
                "foundry:nyquist-auditor agent per batch. Each classifies "
                "COVERED / UNTESTED / UNDERTESTED, generates minimal behavioral "
                "tests, runs them, and commits the passing ones. Any "
                "ESCALATE_IMPL_BUG result goes through the GRIND \u2192 INSPECT \u2192 "
                "ASSAY loop, and an untested requirement is never marked as "
                "passing. The list above ends in the gate out of F5.5, `done`, and the "
                "next Foundry-Next reads that gate's record beside the defect "
                "ledger to serve the crossing."
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
            "instructions": (
                "F6 DONE: the run is sealed, and the `done` transition "
                "generated REPORT.md and archived the run."
            ),
            "details": {},
        }

    return {
        "phase": phase,
        "action": "unknown",
        "instructions": (
            f"The phase {phase!r} is not one the guidance engine knows: "
            "state.json carries a value no transition writes."
        ),
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




#: lead-stalls ST-004 / D-020 — THE VERDICT `Foundry-Accept-Casting` RECORDS,
#: AS THIS READER SPELLS IT.
#:
#: `evidence.py#_record_acceptance_verdict` writes one `handoffs.jsonl` record
#: per JUDGED acceptance, `event` "acceptance" and `destination`
#: ``casting-{id}-accepted`` or ``casting-{id}-refused``; the rungs that refuse
#: the CALL (no run, no `casting_commit`, a stale spec hash) write nothing. The
#: writer is a verifier module and this one is lifecycle, so neither may import
#: the other's spelling (AC-061): each is declared once on its own side and
#: `tests/orchestration/test_guidance_imperatives.py` drives the real writer
#: into this reader so the two cannot drift apart unseen.
_ACCEPTANCE_EVENT = "acceptance"
_ACCEPTANCE_VERDICTS = ("accepted", "refused")


def _acceptance_destination(casting_id: str, verdict: str) -> str:
    """The `destination` the writer records for ``verdict`` on ``casting_id``,
    built whole from the manifest id — nothing parses an id back out."""
    return f"casting-{casting_id}-{verdict}"


def _acceptance_verdicts(fdir: Path) -> dict[str, tuple[int, object]] | None:
    """``{destination: (file position, timestamp)}`` for the LAST acceptance
    record per destination, or ``None`` when the ledger cannot be decoded."""
    records, problem = read_jsonl(fdir / "handoffs.jsonl")
    if problem is not None:
        return None
    latest: dict[str, tuple[int, object]] = {}
    for position, record in enumerate(records):
        destination = record.get("destination")
        if record.get("event") == _ACCEPTANCE_EVENT and isinstance(destination, str):
            latest[destination] = (position, record.get("timestamp"))
    return latest


def _moment(value: object) -> datetime | None:
    """An ISO-8601 timestamp as an aware UTC datetime, or ``None``. Total."""
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.strip())
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _owed_acceptances(
    fdir: Path, castings: list[tuple[str, str]], done_at: dict[str, object]
) -> dict:
    """``{"cast_refused": [...], "cast_unaccepted": [...]}`` — lead-stalls D-020.

    For every casting whose ledger has declared itself done, the LAST verdict
    recorded for it decides:

      * ``-accepted``                              -> built; owes nothing.
      * ``-refused`` no older than the done line   -> ``cast_refused``: nobody
        has touched it since the refusal, so it is re-dispatched.
      * ``-refused`` OLDER than the done line      -> ``cast_unaccepted``: the
        teammate answered the refusal and declared itself done again.
      * no verdict                                 -> ``cast_unaccepted``.

    `>=` because the teammate's done line is written BEFORE the call that
    refuses it and may carry only whole seconds. A pair that cannot be ordered
    reads as ``cast_unaccepted``: re-accepting a refused casting buys one more
    refusal, which records a verdict that CAN be ordered, while re-dispatching a
    re-worked one buys a whole teammate cycle.

    NOT a reading at all (both keys absent) when `handoffs.jsonl` cannot be
    decoded — `_artifact_guard` refuses that run before the router is reached,
    and a reader that guessed "nothing accepted" would re-open every casting.
    """
    latest = _acceptance_verdicts(fdir)
    if latest is None:
        return {}
    refused: list[str] = []
    unaccepted: list[str] = []
    # `castings` is ``[(casting id, ledger agent id), ...]`` in manifest wave
    # order. Published as CASTING ids — the value `Foundry-Accept-Casting` and
    # `Foundry-Spawn-Teammate` take — never as ledger agent ids.
    for casting_id, agent_id in castings:
        if agent_id not in done_at:
            continue
        verdicts = [
            (*latest[destination], verdict)
            for verdict in _ACCEPTANCE_VERDICTS
            if (destination := _acceptance_destination(casting_id, verdict)) in latest
        ]
        if not verdicts:
            unaccepted.append(casting_id)
            continue
        _position, stamp, verdict = max(verdicts, key=lambda row: row[0])
        if verdict == "accepted":
            continue
        refused_at = _moment(stamp)
        finished_at = _moment(done_at[agent_id])
        if refused_at and finished_at and refused_at >= finished_at:
            refused.append(casting_id)
        else:
            unaccepted.append(casting_id)
    return {"cast_refused": refused, "cast_unaccepted": unaccepted}


def _cast_wave_position(
    project_root: str,
    roster_ids: set[str],
    done_ids: set[str],
    done_at: dict[str, object] | None = None,
) -> dict:
    """Where the run stands in its CAST waves (lead-stalls D-009 / D-010).

    Returns ``{"cast_wave_pending": int | None, "cast_wave_built": int | None}``,
    plus ``cast_refused`` / ``cast_unaccepted`` (lead-stalls D-020, see
    `_owed_acceptances`) whenever the manifest answered and ``done_at`` — the
    done line's timestamp per done agent — was supplied:

      * ``cast_wave_pending`` — the LOWEST manifest wave holding a casting that
        has not written a terminal ``"done": true`` line, and ``0`` when every
        wave has. That is the branch question `_branch_state` asks, and the
        number `{wave}` carries into the dispatch the lead is then handed.
      * ``cast_wave_built`` — the HIGHEST wave with a casting in the roster at
        all, which is the team name the wave-complete branch tears down.
      * ``None`` for both when no manifest answered, which routes `_branch_state`
        back to the roster reading it shipped with rather than to a guess.

    WHY THE LEDGER'S OWN TERMINAL LINE AND NOT `spawns.log`. A dispatch record
    and a seeded ledger are both written by `Foundry-Cast-Wave` BEFORE the lead
    spawns a single Agent, so either one read as "this wave is dispatched" would
    answer yes for a lead standing between steps (3) and (4) of its own
    sequence — and the wave it would then be sent to dispatch is the NEXT one,
    over a wave nothing has built. The teammate's own `"done": true` is the only
    signal in the run that a casting was WORKED, and the progress protocol every
    spawn prompt carries is what puts it there. A wave whose teammate died mid
    work therefore stays pending and is re-dispatched, which is the answer that
    state is owed.

    NEVER RAISES, for `_waiting_on_agents`'s reason: a reader that cannot answer
    must not be able to claim a wave is finished, so every failure path answers
    ``None`` and the caller falls back.
    """
    blank = {"cast_wave_pending": None, "cast_wave_built": None}
    try:
        # Lazily, like the `foundry_liveness` import below it and for the same
        # reason: this module is imported BY `foundry_spawn` at call time, and a
        # ledger id spelled a second time here is how the two drift apart.
        from foundry_mcp.tools.foundry_spawn import _agent_id_for_casting

        fdir = get_run_dir(project_root)
        if not fdir or not fdir.exists():
            return blank
        manifest = _load_json(fdir / "castings" / "manifest.json")
        waves = manifest.get("waves") if isinstance(manifest, dict) else None
        if not isinstance(waves, list):
            return blank
        numbered: list[tuple[int, list]] = []
        for entry in waves:
            if not isinstance(entry, dict):
                continue
            number = entry.get("wave")
            ids = entry.get("casting_ids")
            if isinstance(number, bool) or not isinstance(number, int):
                continue
            if number < 1 or not isinstance(ids, list) or not ids:
                continue
            numbered.append((number, ids))
        # A `waves` list that held nothing usable is a manifest that did not
        # answer, NOT a run with no waves left: `0` here would read as "every
        # wave is done" and hand the lead the teardown.
        if not numbered:
            return blank
        pending = [
            number for number, ids in numbered
            if any(_agent_id_for_casting(cid) not in done_ids for cid in ids)
        ]
        built = [
            number for number, ids in numbered
            if any(_agent_id_for_casting(cid) in roster_ids for cid in ids)
        ]
        position = {
            "cast_wave_pending": min(pending) if pending else 0,
            "cast_wave_built": max(built) if built else 0,
        }
        # lead-stalls D-020 — a done line is not a built casting until it is
        # accepted. Measured over the SAME manifest castings the position was,
        # in wave order, so `{casting}` names the lowest wave's first.
        if done_at is not None:
            position.update(_owed_acceptances(
                fdir,
                [
                    (str(cid), _agent_id_for_casting(cid))
                    for _number, ids in sorted(numbered, key=lambda w: w[0])
                    for cid in ids
                ],
                done_at,
            ))
        # lead-stalls D-031 — the team the refusal's teammate belongs to, named
        # the way `_CAST_WAVE_UNDISPATCHED` told the lead to create it, for the
        # FIRST refused id because that is the casting `{casting}` names.
        refused = position.get("cast_refused") or []
        own_wave = next(
            (
                number for number, ids in sorted(numbered, key=lambda w: w[0])
                if refused and refused[0] in {str(cid) for cid in ids}
            ),
            None,
        )
        if own_wave is not None:
            position["cast_refused_team"] = f"cast-{fdir.name}-wave-{own_wave}"
        return position
    except Exception:  # noqa: BLE001 - a watchdog never raises into its caller
        return blank


def _waiting_on_agents(project_root: str, teams: dict | None = None) -> dict:
    """Is the lead waiting on live agents, or is it deliberating (FR-020)?

    Returns ``{"waiting": bool, "count": int, "detail": str, "agents": [...],
    "teams_active": bool, "teams_registered": [team name, ...],
    "progressing_agents": int, "roster_agents": int,
    "cast_wave_pending": int | None, "cast_wave_built": int | None,
    "cast_refused": [casting id, ...], "cast_unaccepted": [casting id, ...],
    "cast_refused_team": str}`` — the refusal and acceptance lists only when
    the manifest answered (lead-stalls D-020), and `cast_refused_team` only
    when `cast_refused` is non-empty (lead-stalls D-031).

    lead-stalls D-028 / D-031 — ``teams`` is a `_check_active_teams` answer the
    caller already holds. The router passes the one it routes on, so the
    refusal branch chosen here and the team arm that reads the same scan can
    never be two answers about one registry: with two scans, a first scan that
    failed (read as "no team") and a second that saw the refused casting's team
    handed the lead `cleanup_teams` over the team its refusal was owed to.

    lead-stalls D-009 / D-010 — AND THE LAST TWO ARE WHY THIS ROUTINE ANSWERS
    THE BRANCH QUESTION AT ALL. lead-stalls FR-015 is Locked on "the server
    substitutes the right one using `_waiting_on_agents` at emission", and this
    result carried no wave field, so CT-008's declared input could not decide
    the branch FR-015 requires: `{"waiting": False, "roster_agents": >0}` is
    byte-identical at "wave 1 done, wave 2 pending" and at "the final wave is
    done". The position is measured here, on the ONE roster read this call
    takes, rather than in a second scan the imperative and the payload could
    disagree about.

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
    if not isinstance(teams, dict):
        try:
            teams = _check_active_teams(project_root)
        except Exception:  # noqa: BLE001 - a watchdog never raises into its caller
            teams = {"active": False, "teams": []}
    teams_active = bool(teams.get("active"))
    # lead-stalls D-031 — the REGISTRY half alone, which is the only half that
    # can name a team. A live pane says some teammate somewhere is up; it
    # cannot say whose.
    registered = teams.get("teams")
    teams_registered = [
        name for name in (registered if isinstance(registered, list) else [])
        if isinstance(name, str)
    ]

    try:
        from foundry_mcp.tools.foundry_spawn import (
            STATUS_DONE,
            STATUS_NO_PROGRESS,
            STATUS_PROGRESSING,
            foundry_liveness,
        )

        liveness = foundry_liveness(None, None, project_root=project_root)
    except Exception:  # noqa: BLE001 - a watchdog never raises into its caller
        liveness = {"ok": False}

    # lead-stalls FR-015 / CT-008 — THE WHOLE ROSTER, BESIDE THE PROGRESSING
    # SUBSET.
    #
    # `live_agents` answers "is the lead waiting", which is this routine's own
    # question. `roster` answers a different one that `_cast_wave_position`
    # needs: WHICH CASTINGS HAVE DECLARED THEMSELVES DONE. The two differ
    # exactly where it matters — a teammate that finished is out of
    # `live_agents` and still in the roster — and `teams_active` cannot stand in
    # for it, because the teardown the wave-complete imperative itself names
    # unregisters the team. Between `Foundry-Team-Down` and
    # `Foundry-Phase(phase='cast')` a team scan reads identically to a wave that
    # was never dispatched; a progress ledger is written once and stays written.
    #
    # lead-stalls D-013 — the roster is an INPUT to the position and no longer a
    # branch of its own. `_branch_state` used to fall back to `len(roster)` when
    # the manifest did not answer, and a count of every ledger in the run cannot
    # express wave position, so that fallback handed a wave boundary the
    # teardown. The count is still published for lead-stalls FR-005; it decides
    # nothing.
    roster = []
    if liveness.get("ok"):
        roster = [
            row for row in (liveness.get("agents", []) or [])
            if isinstance(row, dict)
        ]

    live_agents = []
    # lead-stalls D-009 — the two id sets `_cast_wave_position` reads, collected
    # in the loop that already walks the roster. Gathered HERE and not in the
    # helper because `STATUS_DONE` is bound by the import above, which the
    # failure path skips — and on that path `roster` is empty, so this loop does
    # not run and the helper is handed two empty sets, which is honest: nothing
    # was measured.
    roster_ids: set[str] = set()
    done_ids: set[str] = set()
    # lead-stalls D-020 — WHEN each done agent declared itself done, which is
    # what orders a refusal against the teammate's answer to it.
    done_at: dict[str, object] = {}
    for row in roster:
        agent_id = row.get("agent")
        if isinstance(agent_id, str):
            roster_ids.add(agent_id)
            if row.get("status") == STATUS_DONE:
                done_ids.add(agent_id)
                done_at[agent_id] = row.get("last_timestamp")
        # PROGRESSING and NO_PROGRESS both mean lines are still ARRIVING;
        # they differ only in whether the `step` field moved. STALLED means
        # no line at all for the threshold, DONE means finished, and
        # NO_LEDGER / UNKNOWN mean there is no evidence — none of which is
        # an agent to wait for.
        if row.get("status") in (STATUS_PROGRESSING, STATUS_NO_PROGRESS):
            live_agents.append(row)

    result["roster_agents"] = len(roster)
    # Published on BOTH return paths below, because the branch question is
    # asked in every run state and not only in the idle ones.
    result.update(
        _cast_wave_position(project_root, roster_ids, done_ids, done_at)
    )

    # D-127 / FR-020, stated once: PROGRESS decides. An EMPTY roster is the one
    # answer that lets the watchdog speak. A registered team with nothing
    # progressing is D-021's stale directory rather than an agent to wait for,
    # and it can no longer suppress the warning because it never reaches past
    # this line on its own.
    if not live_agents:
        result["teams_active"] = teams_active
        result["teams_registered"] = teams_registered
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
        "teams_registered": teams_registered,
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
