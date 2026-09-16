"""Every imperative the LEAD receives names one unconditional next call.

lead-stalls FR-001 / FR-002 / FR-005 / FR-006 / FR-007 / FR-008 / FR-015 /
GI-004 / GI-008 / CT-001 / CT-002 / CT-003 / CT-006 / CT-008.

WHY THIS SUITE ASSERTS THE SUBSTITUTED HEADER AND NOT THE TABLE ENTRY.
----------------------------------------------------------------------
`_ACTION_IMPERATIVES` is a TEMPLATE table. Two of its entries hold more than one
branch, and the server picks one at emission from the `_waiting_on_agents`
reading. A test that read `_ACTION_IMPERATIVES[action]` alone would therefore
judge a string no lead ever receives — and lead-stalls OT-013 is a claim
about what the lead RECEIVES ("no imperative string as received by the lead
contains a conditional the lead must evaluate"). So every sweep below goes through
`_format_imperative_header`, which is the one function that turns the table into
the header, exactly as `tests/orchestration/test_gates.py` does for the
`{gate}` / `{token}` crossing pair.

THE AUDIT IS A FUNCTION, NOT A TEST BODY.
-----------------------------------------
lead-stalls FR-008 discharges when the 22-key sweep is re-run against the fix
and returns zero, AND when its finding list is recorded as evidence
(lead-stalls OT-002). Those are two consumers of one sweep, so the sweep is
`audit_action_imperatives()` and both read it: the test below asserts it
returns nothing, and
`evidence/casting-imperatives-audit.log` records what it printed. A second
hand-written copy of the detector in the evidence command would be a second
spelling of the rule, which is the failure mode this package documents most.
"""

from __future__ import annotations

import re

import pytest

from tests.orchestration._env import (  # noqa: F401
    _progressing_ledger,
    _stale_stall_clock,
    _stalled_ledger,
    _teams_active,
    _write_manifest_with_castings,
    _write_state,
    run_env,
)

from foundry_mcp.tools.orchestration.guidance import (  # noqa: F401
    _ACTION_CROSSINGS,
    _ACTION_IMPERATIVES,
    _BRANCH_CLOSE,
    _BRANCH_FALLBACK,
    _BRANCH_OPEN,
    _GATE_THEN_PHASE_NOTE,
    _WAITING_IS_NOT_STOPPING,
    _branch_state,
    _format_imperative_header,
    _parse_branches,
    _select_branch,
    _waiting_on_agents,
    foundry_next_action,
)


# --------------------------------------------------------------------------- #
# The lead-stalls FR-007 / FR-008 audit
# --------------------------------------------------------------------------- #

#: lead-stalls FR-007 states the defect shape verbatim: "entries that hand the
#: lead a conditional or a judgment task instead of naming a literal tool
#: call". These are the two halves of that sentence, as patterns.
#:
#: The conditional half is STRUCTURAL rather than a list of the two known bad
#: strings: a line or bullet that OPENS with `IF` is the lead being handed the
#: condition, and "depends on" is the header announcing that it is about to be.
#: A detector written to match only the two entries the audit already found
#: would return zero for the reason that it cannot see anything, which is the
#: green-over-nothing state this repo has been bitten by more than once.
_HANDS_OVER_THE_CONDITION = re.compile(
    r"(?m)^\s*(?:[-*•]|\(\d+\))?\s*IF\b"
    r"|\bdepends on\b"
    r"|\bwhichever\b"
    r"|\bdecide whether\b"
    r"|\bif you (?:are|have|think|judge)\b",
    re.IGNORECASE,
)

#: The other half: something the lead can execute. Either a literal tool call,
#: or an explicit declaration that there is none — which is how the correct
#: terminals `done` and `halted` have always answered, and how the
#: teammates-live branch answers now (lead-stalls CT-002).
_NAMES_A_CALL = re.compile(
    r"Foundry-[A-Z][A-Za-z-]*"
    r"|TeamCreate|TeamDelete|SendMessage|TaskOutput"
    r"|\bAgent\("
)
_NAMES_NO_CALL = re.compile(r"YOUR NEXT CALL:\s*NONE")

#: Every liveness reading the branch selector can be handed, so the sweep judges
#: what the lead receives in EVERY run state rather than in the one state a bare
#: call happens to resolve to. `None` is the reading a caller that measured
#: nothing passes.
_LIVENESS_READINGS: tuple[tuple[str, object], ...] = (
    ("live", {"waiting": True, "count": 2, "detail": "oldest progress 1m 0s ago"}),
    ("idle", {"waiting": False, "roster_agents": 3, "teams_active": True}),
    ("torn-down", {"waiting": False, "roster_agents": 3, "teams_active": False}),
    ("undispatched", {"waiting": False, "roster_agents": 0, "teams_active": False}),
    ("unmeasured", None),
)


def _emission_phases(action: str) -> tuple[str, ...]:
    """Every phase an action can be emitted FROM, for the crossing substitution.

    Only `transition_to_inspect` differs by phase today, and it is derived from
    `_ACTION_CROSSINGS` rather than listed so an action that gains a second
    crossing is swept the day it lands.
    """
    crossings = tuple(_ACTION_CROSSINGS.get(action, {}))
    return crossings or ("F1",)


def _audit_sites() -> list[tuple[str, str, str]]:
    """(action, site label, emitted text) for every string the table can emit.

    One enumeration, two readers: the detector below and the report it feeds.
    A second copy of this loop in the evidence command would be a second
    spelling of what the audit's population IS.
    """
    return [
        (
            action,
            f"{action}@{phase}[{label}]",
            _format_imperative_header(
                action, "", {}, run_name="audit", phase=phase,
                liveness=liveness,
            ),
        )
        for action in sorted(_ACTION_IMPERATIVES)
        for phase in _emission_phases(action)
        for label, liveness in _LIVENESS_READINGS
    ]


def audit_action_imperatives() -> list[str]:
    """lead-stalls FR-007's audit over every string `_ACTION_IMPERATIVES`
    can emit.

    Returns one human-readable line per DEFECTIVE emission — empty when the
    table is clean, which is what lead-stalls FR-008 discharges on.
    Deterministic in order and in content so the evidence log re-executes
    byte-identically.
    """
    findings: list[str] = []
    for _action, site, text in _audit_sites():
        hit = _HANDS_OVER_THE_CONDITION.search(text)
        if hit:
            quoted = " ".join(
                text[max(0, hit.start() - 40):hit.end() + 60].split()
            )
            findings.append(
                f"{site}: CONDITIONAL — the lead must evaluate {quoted!r}"
            )
        if not (_NAMES_A_CALL.search(text) or _NAMES_NO_CALL.search(text)):
            findings.append(
                f"{site}: NO_LITERAL_CALL — names neither a tool call nor "
                f"'YOUR NEXT CALL: NONE'"
            )
        if _BRANCH_OPEN in text or _BRANCH_CLOSE in text:
            findings.append(
                f"{site}: UNRESOLVED_BRANCH — a branch marker survived to the "
                f"lead"
            )
    return findings


def audit_report() -> list[str]:
    """The finding list lead-stalls OT-002 requires recorded as evidence.

    The SAME sweep the test above asserts is empty, rendered per key. A log
    reading "0 findings" and nothing else is not a finding list -- it cannot be
    told apart from a sweep that ran over an empty roster, which is the
    green-over-nothing state the floor test beside it exists to catch. So the
    record names every key it judged and what it judged there.
    """
    findings = audit_action_imperatives()
    sites = _audit_sites()
    lines = [
        f"keys in _ACTION_IMPERATIVES: {len(_ACTION_IMPERATIVES)}",
        f"emission sites swept:        {len(sites)}",
        f"defective emissions:         {len(findings)}",
        "",
        "site = action@emitting-phase[liveness reading]; readings swept: "
        + ", ".join(label for label, _ in _LIVENESS_READINGS),
        "",
    ]
    for action in sorted(_ACTION_IMPERATIVES):
        hits = [f for f in findings if f.startswith(f"{action}@")]
        lines.append(f"  {action:<24} {'DEFECTIVE' if hits else 'ok'}")
    lines.append("")
    lines.extend(findings or ["(no entry hands the lead a conditional)"])
    return lines


def test_the_audit_sweeps_every_key_and_returns_zero():
    """lead-stalls FR-008 verbatim: 'Builder fixes the two named, then
    re-runs the same audit across all 22 keys and records the finding list as
    evidence. Discharged when the sweep returns zero.'

    lead-stalls OT-002 is the recorded half; this is the zero half.
    lead-stalls OT-013 is why it sweeps the SUBSTITUTED header for every
    liveness reading rather than the table entry: the fix itself is subject to
    the same audit, so a substituted branch that still held a conditional would
    be flagged here by the guard that flagged the entry it replaced.
    """
    assert len(_ACTION_IMPERATIVES) == 22, sorted(_ACTION_IMPERATIVES)
    assert audit_action_imperatives() == []


def test_the_audit_is_not_vacuous_and_bites_on_the_entry_it_replaced():
    """Positive control. A detector that stopped matching would report a clean
    table for the reason that it can no longer see one — which is exactly how
    the pre-fix entries survived every existing sweep in this package (the
    survey found NO test asserting the text of either defective imperative).

    The strings below are the two shipped entries as they stood at 4.11.0,
    quoted so the guard is measured against the real defect rather than against
    a paraphrase of it.
    """
    build_castings_before = (
        "YOUR NEXT ACTION depends on wave state:\n"
        "  - IF no CAST team has been registered this wave yet (first entry to "
        "F1): follow the transition_to_cast sequence (TeamCreate → "
        "Foundry-Team-Up → Foundry-Spawn-Teammate per casting → Agent "
        "spawn VERBATIM, foreground).\n"
        "  - IF teammates are currently running: WAIT for all to complete, then "
        "TeamDelete + Foundry-Team-Down + Foundry-Phase(phase='cast'). Do NOT "
        "call Foundry-Next while waiting — it will re-emit this action."
    )
    fix_defects_before = (
        "YOUR NEXT ACTION depends on GRIND state:\n"
        "  - IF no GRIND team registered yet: follow the transition_to_grind "
        "sequence.\n"
        "  - IF teammates are running: WAIT. When all report complete, "
        "TeamDelete + Foundry-Team-Down + Foundry-Gate(phase='inspect_start') "
        "+ Foundry-Phase(phase='inspect_start') + re-run INSPECT."
    )
    for before in (build_castings_before, fix_defects_before):
        assert _HANDS_OVER_THE_CONDITION.search(before), before

    # And it spares the shapes that are NOT the defect: a qualifier inside a
    # step, and a terminal that names no call at all.
    assert not _HANDS_OVER_THE_CONDITION.search(
        "YOUR NEXT CALLS (in order):\n  (1) Foundry-Gate(phase='inspect')\n"
        "  (2) Foundry-Phase(phase='cast') — COVERAGE_DIFF (MIGRATION only)."
    )
    assert _NAMES_NO_CALL.search(_ACTION_IMPERATIVES["done"])
    assert not _NAMES_A_CALL.search("wait for the teammates to finish")


def test_every_action_is_swept_in_every_run_state():
    """Floor: a sweep whose roster silently emptied reports success.

    Pins the population rather than the result — 22 keys, every liveness
    reading, every emission phase — so a `_LIVENESS_READINGS` that lost its
    branches or an `_emission_phases` that started answering `()` fails here
    instead of turning the audit green over nothing.
    """
    sites = [site for _, site, _ in _audit_sites()]
    assert len(sites) == len(set(sites)) >= 22 * len(_LIVENESS_READINGS)
    assert len(_LIVENESS_READINGS) >= 4
    # `transition_to_inspect` is emitted from two phases, so the site count
    # must exceed a flat key-times-reading product.
    assert len(sites) > 22 * len(_LIVENESS_READINGS)


# --------------------------------------------------------------------------- #
# lead-stalls FR-001 / FR-002 / CT-002 / OT-001 / OT-004 — the rewritten pair
# --------------------------------------------------------------------------- #


def test_no_imperative_forbids_the_lead_to_call_foundry_next():
    """lead-stalls OT-001 verbatim: 'A search for the string `Do NOT call
    Foundry-Next` across the server source returns no matches.'

    lead-stalls FR-001: 'Rewrite build_castings so it never says "Do NOT call
    Foundry-Next"'. Asserted over the whole table and not just that one entry,
    because the sentence's defect is not which key held it — a lead forbidden
    the call that the standing rules require after every step has no sanctioned
    move at all, and lead-stalls ST-003 is the run that parks there.
    """
    for action, text in sorted(_ACTION_IMPERATIVES.items()):
        assert "Do NOT call Foundry-Next" not in text, action
        assert "Do not call Foundry-Next" not in text, action


@pytest.mark.parametrize("action", ["build_castings", "fix_defects"])
def test_the_teammates_live_branch_says_ending_the_turn_is_correct(action: str):
    """lead-stalls CT-002 / OT-004 / FR-002.

    lead-stalls FR-002 verbatim: 'waiting on agents and ending the turn is
    CORRECT, the notification wakes you'. lead-stalls CT-002's output column
    for the teammates-live branch is 'text affirming that ending the turn is
    correct'.

    It names NO tool call on purpose, in the register `done` and `halted`
    already use — the survey counted those two among the 20 correct entries for
    naming NONE. An imperative that named a call here would be telling the lead
    to improvise over half-finished work, which is the thing the WAITING notice
    beside it spends a sentence forbidding.
    """
    text = _format_imperative_header(
        action, "", {}, run_name="r", phase="F1",
        liveness={"waiting": True, "count": 2, "detail": "oldest progress 1m"},
    )
    assert _NAMES_NO_CALL.search(text), text
    assert "END YOUR TURN" in text, text
    assert "completion notification" in text, text
    assert not _HANDS_OVER_THE_CONDITION.search(text), text


def test_the_wave_complete_branch_names_the_literal_next_calls():
    """lead-stalls CT-001: the `build_castings` wave-complete branch emits an
    'imperative naming the literal next call'.

    `Foundry-Gate(phase='inspect')` is named and was not before. The F1 arm of
    `_compute_next_action` returns `build_castings` for the WHOLE of F1 --
    `.cast-complete` is written BY the `cast` transition -- so the sibling
    `transition_to_inspect` arm is unreachable until after the crossing, and
    this branch is the only lead-facing surface for it.
    """
    text = _format_imperative_header(
        "build_castings", "", {}, run_name="vm", phase="F1",
        liveness={"waiting": False, "roster_agents": 4, "teams_active": True},
    )
    for call in (
        "TeamDelete",
        "Foundry-Team-Down(team_name='cast-vm-wave-1')",
        "Foundry-Gate(phase='inspect')",
        "Foundry-Phase(phase='cast')",
    ):
        assert call in text, (call, text)
    assert "{run}" not in text, text
    assert not _HANDS_OVER_THE_CONDITION.search(text), text


def test_fix_defects_no_longer_ends_in_a_bare_wait():
    """The must_have truth: '`fix_defects` no longer terminates in a bare
    `WAIT.` with no next call named.'

    The old entry's second arm read 'IF teammates are running: WAIT.' and gave
    the lead one instruction -- wait -- which is not a tool call. Both branches
    now answer: one names the dispatch sequence, the other names NONE and says
    why that is right.
    """
    assert "WAIT." not in _ACTION_IMPERATIVES["fix_defects"]
    for _, liveness in _LIVENESS_READINGS:
        text = _format_imperative_header(
            "fix_defects", "", {}, run_name="r", phase="F3", liveness=liveness,
        )
        assert _NAMES_A_CALL.search(text) or _NAMES_NO_CALL.search(text), text


def test_fix_defects_holds_the_two_branches_and_build_castings_holds_three():
    """lead-stalls FR-015: 'The table entry holds both branches as a
    template'.

    `fix_defects` declares exactly two, and the reason is in the router rather
    than in the prose: `_compute_next_action` emits this action ONLY while
    blocking defects are open and returns `transition_to_inspect` the instant
    the count reaches zero, so every no-agent-running reading of it means the
    same thing and one text serves both. `build_castings` needs a third because
    F1 has no sibling action -- a lead standing between
    `Foundry-Phase(phase='start_cast')` and its first Agent spawn is inside
    `build_castings` with nothing dispatched, which is the case the entry's
    first `IF` arm used to cover.
    """
    grind = _parse_branches(_ACTION_IMPERATIVES["fix_defects"])
    cast = _parse_branches(_ACTION_IMPERATIVES["build_castings"])
    assert sorted(grind) == ["idle", "live"], sorted(grind)
    assert sorted(cast) == ["idle", "live", "undispatched"], sorted(cast)
    # Every branched entry declares the fallback, so `_select_branch` never has
    # to reach its total tail on shipped input.
    for action, branches in (("fix_defects", grind), ("build_castings", cast)):
        assert branches.get(_BRANCH_FALLBACK), action
    # And no OTHER entry is branched: the audit found these two and no more.
    branched = sorted(
        a for a, t in _ACTION_IMPERATIVES.items() if _parse_branches(t)
    )
    assert branched == ["build_castings", "fix_defects"], branched


# --------------------------------------------------------------------------- #
# lead-stalls FR-015 / GI-008 / CT-008 — the substitution itself
# --------------------------------------------------------------------------- #


def test_the_branch_selector_is_total_over_every_reading():
    """lead-stalls GI-008: 'the LEAD always receives one unconditional imperative'.

    `_halt_cause`'s precedent, one placeholder over. `{gate}` / `{token}` may
    fall through to the generic header when they do not resolve; a branched
    entry may NOT, because that header reads 'Execute the first tool call
    mentioned. Do not deliberate.' over an arm the lead never chose -- the exact
    conditional-judgment push lead-stalls GI-008 forbids. So the selector
    answers for every input, including the ones no caller should ever pass.
    """
    hostile = [
        None, {}, "not a dict", 0, [], {"waiting": None},
        {"waiting": False}, {"roster_agents": None, "teams_active": None},
        {"waiting": "yes"}, {"teams_active": True},
    ]
    for reading in hostile:
        state = _branch_state(reading)
        assert state in ("live", "idle", "undispatched"), reading
        for action in ("build_castings", "fix_defects"):
            text = _format_imperative_header(
                action, "", {}, run_name="r", phase="F1", liveness=reading,
            )
            assert text, reading
            assert _BRANCH_OPEN not in text, (reading, text)
            assert "Execute the first tool call mentioned" not in text, reading


def test_an_unbranched_entry_passes_through_untouched():
    """The other half of totality: `_select_branch` must not chew on the 20
    entries that have no branches. Their text is returned exactly."""
    for action, text in sorted(_ACTION_IMPERATIVES.items()):
        if _parse_branches(text):
            continue
        for state in ("live", "idle", "undispatched"):
            assert _select_branch(text, state) == text, action


def test_a_state_with_no_branch_of_its_own_falls_back_rather_than_empties():
    """`fix_defects` declares no `undispatched` branch, so the fallback is what
    answers -- the `.get(..., default)` shape `_halt_cause` uses. An empty
    string here would leave the lead with a header and no instruction."""
    idle = _format_imperative_header(
        "fix_defects", "", {}, run_name="r", phase="F3",
        liveness={"waiting": False, "roster_agents": 2, "teams_active": True},
    )
    undispatched = _format_imperative_header(
        "fix_defects", "", {}, run_name="r", phase="F3",
        liveness={"waiting": False, "roster_agents": 0, "teams_active": False},
    )
    assert idle == undispatched, (idle, undispatched)
    assert "Foundry-Spawn-Teammate" in idle


def test_the_branch_reaches_the_lead_through_the_ordinary_instructions_string(
    run_env
):
    """key_link: 'the substituted branch reaches the lead through the same
    `instructions` string every other action uses.'

    Driven through `foundry_next_action` rather than the formatter, because the
    formatter's answer is only half the path -- the header is PREPENDED to
    `result["instructions"]`, and that concatenation is what the lead reads.
    """
    project_root, fdir = run_env
    _write_state(fdir, phase="F1", cycle=0)
    _write_manifest_with_castings(fdir, ["src/api/a.py"], no_ui=True)
    _progressing_ledger(fdir)

    nxt = foundry_next_action(project_root)

    assert nxt["action"] == "build_castings"
    assert "YOUR NEXT CALL: NONE" in nxt["instructions"]
    assert "END YOUR TURN" in nxt["instructions"]
    assert _BRANCH_OPEN not in nxt["instructions"]


# --------------------------------------------------------------------------- #
# lead-stalls FR-005 / CT-003 / CT-006 / OT-007 — live status on the payload
# --------------------------------------------------------------------------- #


def test_the_audited_action_payloads_carry_live_agent_status(run_env):
    """lead-stalls OT-007: 'The `Foundry-Next` payload for the two audited
    actions carries live-agent status.'

    lead-stalls FR-005 verbatim: 'Put its answer on the
    build_castings/fix_defects payload so a re-emission returns live agent
    status - informative, not a no-op.'
    Before this, `_waiting_on_agents` was consulted ONLY inside the stall
    watchdog, which fires past 180 seconds -- so for the first three minutes of
    every wave the server held the answer and never asked itself, and a lead
    that re-called Foundry-Next got the same frozen conditional back.

    Published under `agent_liveness` and not folded into `waiting_on_agents`,
    which means something older and narrower -- 'the watchdog fired and found
    agents progressing' -- and whose ABSENCE four tests in `test_guidance.py`
    read as 'no stall notice was emitted'.
    """
    project_root, fdir = run_env
    _write_state(fdir, phase="F1", cycle=0)
    _write_manifest_with_castings(fdir, ["src/api/a.py"], no_ui=True)
    _progressing_ledger(fdir)

    nxt = foundry_next_action(project_root)

    assert nxt["action"] == "build_castings"
    live = nxt["agent_liveness"]
    assert live["waiting"] is True
    assert live["count"] >= 1
    assert live["roster_agents"] >= 1
    # lead-stalls CT-006: the stall notice's own key stays absent until the
    # watchdog fires, so the new field did not quietly repurpose the old one.
    assert "waiting_on_agents" not in nxt


def test_an_unaudited_action_pays_for_no_roster_scan(run_env):
    """lead-stalls GI-001 -- 'a lightweight solution ... not a big lift'.

    The roster read is scoped to the actions whose imperative depends on it.
    The other twenty emit exactly as they did and carry no new field, so a
    Foundry-Next at F0 costs no ledger scan.
    """
    project_root, fdir = run_env
    _write_state(fdir, phase="F0", cycle=0)
    _progressing_ledger(fdir)

    nxt = foundry_next_action(project_root)

    assert nxt["action"] == "add_castings"
    assert "agent_liveness" not in nxt


def test_the_liveness_reading_is_taken_once_and_shared_with_the_notice(
    run_env, monkeypatch
):
    """lead-stalls CT-008's input is 'the `_waiting_on_agents` result' -- singular.

    The stall notice and the imperative are two consumers of one question, and
    two readings of a moving roster can disagree: the lead would be told it is
    waiting on three agents by the notice and handed the wave-complete teardown
    by the imperative in the same payload. Counted rather than reasoned about.
    """
    from foundry_mcp.tools.orchestration import guidance as _g

    project_root, fdir = run_env
    _write_state(fdir, phase="F1", cycle=0)
    _write_manifest_with_castings(fdir, ["src/api/a.py"], no_ui=True)
    _progressing_ledger(fdir)
    _stale_stall_clock(fdir, 600)

    calls: list[str] = []
    real = _g._waiting_on_agents

    def _counted(pr):
        calls.append(pr)
        return real(pr)

    monkeypatch.setattr(_g, "_waiting_on_agents", _counted)
    nxt = foundry_next_action(project_root)

    assert len(calls) == 1, calls
    assert "WAITING ON" in nxt["instructions"]
    assert nxt["agent_liveness"]["waiting"] is True


def test_the_roster_outlives_the_teardown_the_imperative_itself_names(run_env):
    """Why `roster_agents` exists, and why `teams_active` could not stand in.

    The wave-complete branch tells the lead to `Foundry-Team-Down` and THEN
    `Foundry-Phase(phase='cast')`. Between those two calls a team scan reads
    exactly like a wave that was never dispatched -- and a selector keyed on the
    team scan alone would answer 'undispatched' there and send the lead to spawn
    a fresh CAST wave over castings it had just accepted. A progress ledger is
    written once and stays written, so the roster still remembers.
    """
    project_root, fdir = run_env
    _write_state(fdir, phase="F1", cycle=0)
    _stalled_ledger(fdir)          # finished/silent: in the roster, not live
    _teams_active(False)           # torn down

    reading = _waiting_on_agents(project_root)

    assert reading["waiting"] is False
    assert reading["teams_active"] is False
    assert reading["roster_agents"] >= 1
    assert _branch_state(reading) == "idle"
    text = _format_imperative_header(
        "build_castings", "", {}, run_name="vm", phase="F1", liveness=reading,
    )
    assert "Foundry-Phase(phase='cast')" in text
    assert "TeamCreate(" not in text, text


# --------------------------------------------------------------------------- #
# lead-stalls FR-006 / GI-004 / OT-003 — the wait policy, one spelling, no poll
# --------------------------------------------------------------------------- #

#: lead-stalls GI-004's violation column: 'Replacement text that tells the
#: lead to sleep, poll, or loop while waiting.' Each spelling is a phrase that
#: would INSTRUCT
#: one, not a word that merely mentions one -- the policy sentence itself has to
#: be able to say 'do NOT poll'.
_INSTRUCTS_A_WAIT_LOOP = (
    "wait and call foundry-next again",
    "call foundry-next again",
    "poll until",
    "keep calling",
    "in a loop until",
    "sleep for",
    "check again in",
    "re-call foundry-next until",
)


def _lead_facing_wait_prose(project_root: str) -> dict[str, str]:
    """Every string this spec changed that a lead can read about waiting."""
    surfaces = {
        f"_ACTION_IMPERATIVES:{action}": text
        for action, text in sorted(_ACTION_IMPERATIVES.items())
    }
    surfaces["_WAITING_IS_NOT_STOPPING"] = _WAITING_IS_NOT_STOPPING
    return surfaces


def test_no_lead_facing_string_instructs_a_sleep_a_poll_or_a_wait_loop(run_env):
    """lead-stalls OT-003 verbatim: 'No lead-facing string added by this
    change instructs a sleep, a poll, or a wait loop.' lead-stalls GI-004:
    'Never a wait loop, never a poll.'

    Swept over the whole imperative table AND over the stall notice the lead
    receives in the same payload, because lead-stalls FR-006's complaint is
    that the two contradicted each other: one said 'Do NOT call Foundry-Next
    while waiting', the other ended 'otherwise wait and call Foundry-Next
    again'. lead-stalls GI-004 decides the direction -- the poll is what
    yields.
    """
    project_root, fdir = run_env
    _write_state(fdir, phase="F1", cycle=0)
    _write_manifest_with_castings(fdir, ["src/api/a.py"], no_ui=True)
    _progressing_ledger(fdir)
    _stale_stall_clock(fdir, 600)

    surfaces = _lead_facing_wait_prose(project_root)
    surfaces["stall_notice"] = foundry_next_action(project_root)["instructions"]

    problems = {
        f"{name}:{spelling}": text
        for name, text in surfaces.items()
        for spelling in _INSTRUCTS_A_WAIT_LOOP
        if spelling in text.lower()
    }
    assert problems == {}, sorted(problems)


def test_the_wait_policy_has_one_spelling():
    """The `_GATE_THEN_PHASE_EXCEPTION` discipline, one rule over.

    This file's documented failure mode is a rule stated in N copies becoming a
    rule stated N different ways -- which is exactly how lead-stalls FR-006's
    contradiction arose, the imperative and the notice each having written the
    wait policy in
    its own words. So the policy is a constant, and every surface that states it
    quotes that constant rather than re-typing it.
    """
    carriers = [
        action for action, text in _ACTION_IMPERATIVES.items()
        if _WAITING_IS_NOT_STOPPING in text
    ]
    assert sorted(carriers) == ["build_castings", "fix_defects"], carriers
    # No imperative says it in its own words: the denials that make the policy
    # a policy appear exactly where the constant put them.
    for action, text in sorted(_ACTION_IMPERATIVES.items()):
        expected = 1 if _WAITING_IS_NOT_STOPPING in text else 0
        assert text.count("Do NOT sleep") == expected, action
        assert text.count("do NOT poll") == expected, action


def test_the_waiting_notice_quotes_the_policy_rather_than_rewording_it(run_env):
    """lead-stalls FR-006: 'Requires also softening the WAITING arm's "call
    Foundry-Next again" so the two don't contradict.'

    Driven through the door: a 600-second gap with an agent progressing is the
    arm, and what it emits must be the constant, verbatim.
    """
    project_root, fdir = run_env
    _write_state(fdir, phase="F1", cycle=0)
    _write_manifest_with_castings(fdir, ["src/api/a.py"], no_ui=True)
    _progressing_ledger(fdir)
    _stale_stall_clock(fdir, 600)

    instructions = foundry_next_action(project_root)["instructions"]

    assert "WAITING ON" in instructions
    assert _WAITING_IS_NOT_STOPPING in instructions
    assert "Foundry-Liveness answers per-agent detail" in instructions


def test_the_stall_accusation_arm_is_untouched(run_env):
    """convergence FR-036's other arm still accuses, and must: the WAITING
    arm softening is about the arm where an agent IS running. With nothing
    running, the silence is the lead's own and the watchdog still says so."""
    project_root, fdir = run_env
    _write_state(fdir, phase="F1", cycle=0)
    _write_manifest_with_castings(fdir, ["src/api/a.py"], no_ui=True)
    _stale_stall_clock(fdir, 600)

    nxt = foundry_next_action(project_root)

    assert nxt["stall_detected_seconds"] >= 600
    assert "STALL DETECTED" in nxt["instructions"]
    assert "NO agent is running" in nxt["instructions"]
    assert _WAITING_IS_NOT_STOPPING not in nxt["instructions"]


# --------------------------------------------------------------------------- #
# The pattern the run was told to mirror
# --------------------------------------------------------------------------- #


def test_transition_to_inspect_is_still_the_substitution_precedent():
    """key_link: '`transition_to_inspect` remains the substitution precedent the
    new code mirrors.'

    Pinned because the new mechanism is a SECOND substitution in this formatter,
    and a later edit that unified the two would take the branch selection with
    it into the `{gate}` fallback -- where an unresolved value discards the
    imperative and takes the generic header. The precedent has to stay visible
    and separate for that difference to keep being read.
    """
    entry = _ACTION_IMPERATIVES["transition_to_inspect"]
    assert "{gate}" in entry and "{token}" in entry
    assert not _parse_branches(entry)
    for phase, crossing in _ACTION_CROSSINGS["transition_to_inspect"].items():
        text = _format_imperative_header(
            "transition_to_inspect", "", {}, run_name="r", phase=phase,
        )
        assert f"Foundry-Gate(phase='{crossing['gate']}')" in text, phase
        assert f"Foundry-Phase(phase='{crossing['token']}')" in text, phase


def test_the_wave_complete_branch_carries_the_gate_then_phase_note_once():
    """It names a Gate before a Phase, so `test_guidance.py`'s existing
    one-spelling guard applies to it -- and the note is APPENDED, never
    re-typed. Asserted here too so the reason it is present is recorded beside
    the branch that needs it rather than only in the guard that counts it."""
    entry = _ACTION_IMPERATIVES["build_castings"]
    assert entry.count(_GATE_THEN_PHASE_NOTE) == 1
    assert entry.index("Foundry-Gate(") < entry.index("Foundry-Phase(")
    assert entry.count("OPTIONAL") == 1
    # The live branch is not the one that needs it.
    branches = _parse_branches(entry)
    assert "OPTIONAL" not in branches["live"]
    assert "OPTIONAL" in branches["idle"]
