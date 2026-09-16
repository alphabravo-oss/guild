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
    _defect_ledger,
    _progressing_ledger,
    _stale_stall_clock,
    _stalled_ledger,
    _teams_active,
    _tiered,
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
    _BRANCHED_ACTION_CONTEXT,
    _GATE_THEN_PHASE_NOTE,
    _WAITING_IS_NOT_STOPPING,
    _branch_state,
    _compute_next_action,
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
#:
#: lead-stalls D-004 — THE READINGS ARE THE PRODUCT, AND EACH ONE CARRIES THE
#: BRANCH IT IS OWED.
#: ---------------------------------------------------------------------------
#: This tuple held four `roster_agents` x `teams_active` readings out of the
#: four that exist, and the one it omitted was `(0, True)` — the reading
#: lead-stalls D-003 misrouted. So the sweep's zero was computed over a space
#: the defect could not appear in, and lead-stalls OT-002's stopping condition
#: ("discharged when the sweep returns zero") was not actually met. The four
#: combinations are enumerated here rather than sampled, and `unmeasured`
#: rides alongside them as the reading a caller that measured nothing passes.
#:
#: WIDENING ALONE WOULD NOT HAVE CAUGHT IT, which is the other half of D-004.
#: The three detectors below are SHAPE-only: they ask whether the emitted
#: string names a literal call, hands over a condition, or leaks a marker.
#: `_CAST_WAVE_COMPLETE` names four literal calls and hands over nothing, so a
#: shape detector answers `ok` for it on EVERY reading — including the one that
#: must never receive it. A sweep can only see a misrouting if it knows which
#: branch each reading is OWED, so the third element of each row says so, and
#: `WRONG_BRANCH` below compares it against the branch the lead actually got.
#:
#: The owed branch is the one the RUN STATE calls for, derived from the two
#: fields by hand rather than from `_branch_state` — a table that asked the
#: function under audit what the right answer is would agree with it by
#: construction and could never disagree, which is the shape of a test that
#: cannot fail. `fix_defects` declares no `undispatched` branch and resolves it
#: through `_BRANCH_FALLBACK`; `_owed_branch` below is where that is reconciled.
_LIVENESS_READINGS: tuple[tuple[str, object, str], ...] = (
    # waiting -> live, whatever the other two fields say.
    ("live", {"waiting": True, "count": 2, "detail": "oldest progress 1m 0s ago"}, "live"),
    # roster 3 / team registered: the wave is dispatched and nothing advances.
    ("idle", {"waiting": False, "roster_agents": 3, "teams_active": True}, "idle"),
    # roster 3 / team torn down: between Foundry-Team-Down and the crossing.
    # The ledger outlives the team, so this is still the wave-complete state.
    ("torn-down", {"waiting": False, "roster_agents": 3, "teams_active": False}, "idle"),
    # lead-stalls D-003's reading. Roster 0 / team registered: the lead is
    # between steps (4) and (6) of `transition_to_cast` — Foundry-Team-Up made,
    # first Agent not yet spawned. Nothing has been dispatched, so the dispatch
    # branch is what it is owed; it was being handed the teardown.
    ("registered-not-spawned", {"waiting": False, "roster_agents": 0, "teams_active": True}, "undispatched"),
    # roster 0 / no team: nothing dispatched by either measure.
    ("undispatched", {"waiting": False, "roster_agents": 0, "teams_active": False}, "undispatched"),
    # the watchdog could not answer; a reading that failed must not be able to
    # claim a wave is finished, so it owes the dispatch branch too.
    ("unmeasured", None, "undispatched"),
)


def _owed_branch(action: str, owed: str) -> str:
    """The branch name `action` is OWED on a reading whose run state is `owed`.

    A state an entry declares no branch for resolves through `_BRANCH_FALLBACK`,
    which is the `.get(..., default)` shape `_halt_cause` uses — so `fix_defects`
    on an `undispatched` reading is owed its `idle` branch, and that is a
    property of what the entry DECLARES rather than of what the selector did.
    """
    branches = _parse_branches(_ACTION_IMPERATIVES.get(action, ""))
    return owed if branches.get(owed) else _BRANCH_FALLBACK


def _emitted_branch(action: str, text: str, run_name: str) -> str:
    """Which declared branch of `action` the lead ACTUALLY received.

    Matched by equality against the branch bodies `_parse_branches` returns,
    with the one placeholder a branched entry carries resolved the way
    `_format_imperative_header` resolves it. Equality rather than a first-line
    or keyword match because `_CAST_WAVE_COMPLETE` and `_CAST_WAVE_UNDISPATCHED`
    open on the identical line — "YOUR NEXT CALLS (in order):" — and a
    discriminator that could not tell those two apart is exactly the one
    lead-stalls D-003 needed it to tell apart.

    ``"?"`` when the text matches no declared branch, which is itself a finding.
    """
    for name, body in _parse_branches(_ACTION_IMPERATIVES.get(action, "")).items():
        if body.replace("{run}", run_name) == text:
            return name
    return "?"


def _emission_phases(action: str) -> tuple[str, ...]:
    """Every phase an action can be emitted FROM, for the crossing substitution.

    Only `transition_to_inspect` differs by phase today, and it is derived from
    `_ACTION_CROSSINGS` rather than listed so an action that gains a second
    crossing is swept the day it lands.
    """
    crossings = tuple(_ACTION_CROSSINGS.get(action, {}))
    return crossings or ("F1",)


#: The `run_name` every emission site is formatted with. Named once because
#: `_emitted_branch` has to resolve `{run}` the same way the formatter did, and
#: a second literal here is how those two drift apart.
_AUDIT_RUN = "audit"


def _audit_sites() -> list[tuple[str, str, str, str]]:
    """(action, site label, emitted text, owed branch) for every string the
    table can emit.

    One enumeration, two readers: the detector below and the report it feeds.
    A second copy of this loop in the evidence command would be a second
    spelling of what the audit's population IS.

    The fourth element is lead-stalls D-004's addition — the branch this
    reading is owed, so the sweep can judge ROUTING and not only shape.
    ``""`` for the twenty unbranched entries, which have nothing to route.
    """
    return [
        (
            action,
            f"{action}@{phase}[{label}]",
            _format_imperative_header(
                action, "", {}, run_name=_AUDIT_RUN, phase=phase,
                liveness=liveness,
            ),
            _owed_branch(action, owed) if _parse_branches(
                _ACTION_IMPERATIVES[action]
            ) else "",
        )
        for action in sorted(_ACTION_IMPERATIVES)
        for phase in _emission_phases(action)
        for label, liveness, owed in _LIVENESS_READINGS
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
    for action, site, text, owed in _audit_sites():
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
        # lead-stalls D-004 — THE ROUTING CHECK, WHICH IS THE ONE THE THREE
        # ABOVE CANNOT MAKE. They judge the SHAPE of whatever arrived; this
        # judges whether the thing that arrived is the thing this run state
        # was owed. lead-stalls D-003 shipped an unconditional string naming
        # four literal tool calls — clean on all three shape checks — to a
        # lead with zero castings built, telling it to tear the team down and
        # cross into F2. A sweep that cannot express "right text, wrong
        # reading" returns zero over that.
        if owed:
            got = _emitted_branch(action, text, _AUDIT_RUN)
            if got != owed:
                findings.append(
                    f"{site}: WRONG_BRANCH — the lead received the {got!r} "
                    f"branch on a reading owed {owed!r}"
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
        + ", ".join(label for label, _, _ in _LIVENESS_READINGS),
        "",
        "readings are the four roster_agents x teams_active combinations plus "
        "the unmeasured one,",
        "and each names the branch it is OWED so the sweep judges ROUTING and "
        "not only shape:",
        "",
    ]
    for label, liveness, owed in _LIVENESS_READINGS:
        row = liveness if isinstance(liveness, dict) else {}
        lines.append(
            f"  {label:<22} waiting={str(bool(row.get('waiting'))):<5} "
            f"roster_agents={str(row.get('roster_agents', '-')):<4} "
            f"teams_active={str(row.get('teams_active', '-')):<5} "
            f"owed={owed}"
        )
    lines.append("")
    for action in sorted(_ACTION_IMPERATIVES):
        hits = [f for f in findings if f.startswith(f"{action}@")]
        lines.append(f"  {action:<24} {'DEFECTIVE' if hits else 'ok'}")
    lines.append("")
    lines.extend(findings or ["(no entry hands the lead a conditional)"])
    return lines


def audit_evidence() -> list[str]:
    """The finding list, followed by every header the table can EMIT.

    lead-stalls OT-013 is a claim about the strings the lead RECEIVES, and a
    log reading "0 findings" asks the reader to take the detector's word for
    it. This dump is the population the detector judged, so the claim can be
    read rather than trusted: one line per emission site, each showing whether
    that site answers with a call or with an explicit NONE and how its text
    opens. lead-stalls GI-008 / CT-008 are the same claim from the other
    end -- one unconditional string per site, chosen server-side -- and they are
    read off the same dump.

    lead-stalls D-004 -- AND WHICH BRANCH ARRIVED, beside which one was owed.
    "CALL" was true of the wave-complete teardown on the reading that must not
    receive it, so the answer column alone cannot be read as evidence that the
    routing is right. The branch column is what makes that readable.
    """
    lines = audit_report()
    lines += [
        "",
        "EVERY EMITTED HEADER (site | answer | branch | opening of its first line).",
        "'NONE' is an explicit 'YOUR NEXT CALL: NONE' -- the register `done` and",
        "`halted` use, and what the teammates-live branch answers. 'CALL' names a",
        "literal tool call. There is no third answer, which is the whole claim.",
        "branch is 'received/owed' for the two branched entries, '-' for the",
        "twenty that have nothing to route.",
        "",
    ]
    for action, site, text, owed in _audit_sites():
        head = " ".join(text.split("\n", 1)[0].split())
        answer = "NONE" if _NAMES_NO_CALL.search(text) else "CALL"
        branch = (
            f"{_emitted_branch(action, text, _AUDIT_RUN)}/{owed}" if owed else "-"
        )
        # Two literal spaces on BOTH sides of the answer, independent of how
        # far the site label padded: the floor test beside this reads the
        # answer column by that separator, and a site label longer than the
        # pad width would otherwise drop its own row out of the count.
        lines.append(f"  {site:<48}  {answer}  {branch:<28} {head[:52]}")
    return lines


def turn_boundary_report() -> list[str]:
    """What the server tells the lead at each end of the turn boundary.

    lead-stalls ST-001 / ST-002 / ST-003 are transitions of the LEAD'S SESSION,
    and a server test cannot drive them: nothing here can make a session idle,
    and nothing here can deliver a completion notification. What this casting
    contributes to all three is the INSTRUCTION the lead is standing on when it
    decides whether to end its turn, and that is what this report shows --
    the server-side half, named as such rather than as a proof of the whole.

      lead-stalls ST-001  teammates live -> the lead ends its turn. The
              imperative has to say ending it is CORRECT, or a lead trained
              never to stop between phases will improvise over half-finished
              work instead.
      lead-stalls ST-002  the completion notification re-enters the lead. The
              imperative has to name the notification as what resumes it, or
              "end your turn" reads as "abandon the run".
      lead-stalls ST-003  nothing running -> the run PARKS, and that is the
              defect. The
              imperative must name a literal call in every state where no agent
              is running, so ending the turn is never the only move on offer.
    """
    # Derived from `_LIVENESS_READINGS` rather than re-typed, so the reading
    # lead-stalls D-004 added to the audit is shown here too and the two
    # populations cannot drift. `torn-down` and `unmeasured` carry the same
    # lead-stalls ST-003 claim as the readings they resolve with, so the rows the log
    # prints are the distinct RUN STATES and not every reading of them.
    _TRANSITIONS = {"live": "ST-001 / ST-002"}
    rows = [
        (_TRANSITIONS.get(label, "ST-003"), label, liveness)
        for label, liveness, _owed in _LIVENESS_READINGS
        if label in ("live", "idle", "registered-not-spawned", "undispatched")
    ]
    lines = [
        "the server-side half of the turn boundary, per run state.",
        "ends-turn: the imperative answers 'YOUR NEXT CALL: NONE'.",
        "names-wake: it names the completion notification as what resumes the lead.",
        "names-call: it names at least one literal tool call.",
        "denies-poll: it forbids a sleep, a poll and a re-call loop by name.",
        "",
    ]
    for transitions, state, liveness in rows:
        for action in ("build_castings", "fix_defects"):
            text = _format_imperative_header(
                action, "", {}, run_name="audit", phase="F1", liveness=liveness,
            )
            lines.append(
                f"  {transitions:<16} {action:<15} {state:<22} "
                f"ends-turn={'yes' if _NAMES_NO_CALL.search(text) else 'no ':<3} "
                f"names-wake={'yes' if 'completion notification' in text else 'no ':<3} "
                f"names-call={'yes' if _NAMES_A_CALL.search(text) else 'no ':<3} "
                f"denies-poll="
                f"{'yes' if 'do NOT poll' in text else 'n/a'}"
            )
    lines += [
        "",
        "ST-003 is discharged by 'names-call=yes in every state where no agent is",
        "running': a lead that is handed a call cannot park for want of a move.",
        "ST-001 and ST-002 are discharged by 'ends-turn=yes AND names-wake=yes'",
        "on the one state where an agent IS running -- ending the turn is stated",
        "as correct, and the thing that ends the idle is named in the same breath.",
        "The idle of ST-001 and the notification of ST-002 are the HARNESS's to",
        "perform; this casting owns only what the lead is told, and that is all",
        "this log claims.",
    ]
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


def test_the_recorded_evidence_shows_the_population_it_judged():
    """Floor for the two report generators the evidence logs re-execute.

    A generator that quietly stopped emitting rows would leave a log that still
    re-executes byte-identically -- against its own emptiness. So the reports
    are pinned to the population they claim: one row per emission site, and one
    row per (audited action x run state).
    """
    dump = audit_evidence()
    for _action, site, _text, _owed in _audit_sites():
        assert any(site in line for line in dump), site
    assert sum(1 for l in dump if "  CALL  " in l or "  NONE  " in l) == len(
        _audit_sites()
    )
    # lead-stalls D-004 — the readings table is IN the log, so a reader can see
    # which run states the zero was computed over rather than trusting that it
    # covered them. The reading D-003 misrouted is named in it.
    for label, _liveness, owed in _LIVENESS_READINGS:
        assert any(
            line.strip().startswith(label) and f"owed={owed}" in line
            for line in dump
        ), label
    assert any("registered-not-spawned" in line for line in dump)

    boundary = turn_boundary_report()
    for action in ("build_castings", "fix_defects"):
        for state in ("live", "idle", "undispatched", "registered-not-spawned"):
            assert any(
                action in line and f" {state:<22}" in line for line in boundary
            ), (action, state)
    # The claim the log is bound to lead-stalls ST-003 for: wherever no agent
    # is running,
    # the lead is handed a call rather than only an invitation to stop.
    for line in boundary:
        if "ends-turn=no" in line:
            assert "names-call=yes" in line, line


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

    lead-stalls D-004 — AND THE PRODUCT IS PINNED AS A PRODUCT, not as a count.
    The tuple held four of the four `roster_agents` x `teams_active`
    combinations and was missing `(0, True)`, so `len(...) >= 4` passed while
    the sweep's zero was being computed over a space the defect could not
    appear in. A floor that counts rows cannot tell a complete product from an
    incomplete one of the same size; this one enumerates the product and asks
    for each cell by name.
    """
    sites = [site for _, site, _, _ in _audit_sites()]
    assert len(sites) == len(set(sites)) >= 22 * len(_LIVENESS_READINGS)
    # Every cell of the product, named. `unmeasured` rides alongside as the
    # reading a caller that measured nothing passes.
    cells = {
        (bool(row.get("roster_agents")), bool(row.get("teams_active")))
        for _label, liveness, _owed in _LIVENESS_READINGS
        if isinstance(liveness, dict) and not liveness.get("waiting")
        for row in (liveness,)
    }
    assert cells == {(False, False), (False, True), (True, False), (True, True)}, cells
    assert any(liveness is None for _l, liveness, _o in _LIVENESS_READINGS)
    assert any(
        isinstance(liveness, dict) and liveness.get("waiting")
        for _l, liveness, _o in _LIVENESS_READINGS
    )
    # Every reading names the branch it is owed, and both dispatch and
    # wave-complete are among them — a table that owed one branch everywhere
    # would make `WRONG_BRANCH` unfalsifiable.
    assert {owed for _l, _lv, owed in _LIVENESS_READINGS} == {
        "live", "idle", "undispatched",
    }
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
    for _label, liveness, _owed in _LIVENESS_READINGS:
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
# lead-stalls D-003 / D-004 — the reading the selector misrouted, and the
# sweep that can now see a misrouting at all
# --------------------------------------------------------------------------- #


def test_a_registered_team_with_nothing_spawned_is_not_a_finished_wave(run_env):
    """lead-stalls FR-015 / CT-008 / US-001, and D-003.

    `{"waiting": False, "roster_agents": 0, "teams_active": True}` is the lead
    standing between steps (4) and (6) of the `transition_to_cast` sequence this
    same server hands out: `Foundry-Team-Up` made, first Agent not yet spawned.
    `_branch_state` read `roster_agents or teams_active`, so the registered team
    alone answered "dispatched" and the lead was handed `_CAST_WAVE_COMPLETE` --
    TeamDelete, Foundry-Team-Down, gate, and cross into F2 -- WITH ZERO CASTINGS
    BUILT. lead-stalls FR-015 locks "the server substitutes the RIGHT one using
    `_waiting_on_agents` at emission"; it substituted the wrong one.

    Driven at both ends: off a synthetic reading, and through `_waiting_on_agents`
    against a run with a registered team and no ledger, so the fix is pinned to
    the reading the watchdog actually produces and not only to a dict shape.
    """
    assert _branch_state(
        {"waiting": False, "roster_agents": 0, "teams_active": True}
    ) == "undispatched"

    project_root, fdir = run_env
    _write_state(fdir, phase="F1", cycle=0)
    _teams_active(True)            # TeamCreate + Foundry-Team-Up have happened
    # and no progress ledger: no Agent has been spawned yet.

    reading = _waiting_on_agents(project_root)

    assert reading["waiting"] is False
    assert reading["teams_active"] is True
    assert reading["roster_agents"] == 0
    assert _branch_state(reading) == "undispatched"

    text = _format_imperative_header(
        "build_castings", "", {}, run_name="vm", phase="F1", liveness=reading,
    )
    # What it is owed: get the wave dispatched.
    assert "Foundry-Cast-Wave(wave=1, phase='cast')" in text, text
    assert "Agent(subagent_type='foundry:teammate'" in text, text
    # What it must never be handed here: the teardown and the crossing.
    assert "TeamDelete" not in text, text
    assert "Foundry-Team-Down(" not in text, text
    assert "Foundry-Gate(phase='inspect')" not in text, text
    assert "Foundry-Phase(phase='cast')" not in text, text
    # And the branch does not claim something the server just measured false.
    assert "No CAST team is registered" not in text, text


def test_the_audit_sees_a_misrouted_branch(monkeypatch):
    """lead-stalls D-004 — the positive control for `WRONG_BRANCH`.

    The other three detectors are SHAPE-only, and `_CAST_WAVE_COMPLETE` is a
    clean shape: four literal calls, no conditional, no marker. So the sweep
    answered `ok` for it on the one reading that must never receive it, and
    lead-stalls FR-008's "discharged when the sweep returns zero" was being
    satisfied by a sweep that could not express the defect.

    Restores the `or teams_active` disjunct and asserts the sweep now BITES,
    naming the reading and both branches. Without this control, `WRONG_BRANCH`
    could be a check that never fires and nobody would know.
    """
    import foundry_mcp.tools.orchestration.guidance as _g

    def _with_the_disjunct_back(liveness: object) -> str:
        row = liveness if isinstance(liveness, dict) else {}
        if row.get("waiting"):
            return "live"
        if row.get("roster_agents") or row.get("teams_active"):
            return "idle"
        return "undispatched"

    monkeypatch.setattr(_g, "_branch_state", _with_the_disjunct_back)

    findings = audit_action_imperatives()

    misrouted = [f for f in findings if "WRONG_BRANCH" in f]
    assert misrouted, findings
    assert any(
        "build_castings@F1[registered-not-spawned]" in f
        and "'idle'" in f and "'undispatched'" in f
        for f in misrouted
    ), misrouted
    # And it bites at exactly ONE site, which is the claim the defect report
    # made off a side-by-side run of the shipped selector and a disjunct-free
    # variant: the other readings agree either way, so the disjunct's only
    # contribution was the reading it got wrong. `fix_defects` is untouched
    # even on that reading -- it declares no `undispatched` branch, so both
    # states resolve through `_BRANCH_FALLBACK` to its `idle` body and there is
    # nothing for a misrouting to change. That is why removing the disjunct is
    # safe rather than merely correct.
    assert {f.split(":")[0] for f in misrouted} == {
        "build_castings@F1[registered-not-spawned]",
    }, sorted({f.split(":")[0] for f in misrouted})


# --------------------------------------------------------------------------- #
# lead-stalls FR-006 / GI-004 / OT-003 — the wait policy, one spelling, no poll
# --------------------------------------------------------------------------- #

#: lead-stalls GI-004's violation column: 'Replacement text that tells the
#: lead to sleep, poll, or loop while waiting.' Each spelling is a phrase that
#: would INSTRUCT
#: one, not a word that merely mentions one -- the policy sentence itself has to
#: be able to say 'do NOT poll'.
#:
#: lead-stalls D-005 -- THE BARE WAIT IS THE OTHER HALF, AND IT WAS NOT LISTED.
#: Every spelling above names a MECHANISM the lead would use to pass the time,
#: so the list could only catch a wait that said HOW. The `_compute_next_action`
#: F1 arm said "Wait for all tasks to complete" and the F3 arm said "Wait for
#: completion" -- no mechanism, so nothing here matched, and a lead trained to
#: obey Foundry-Next literally was handed "wait" with no sanctioned way to do
#: it, printed directly beneath an imperative that had just said END YOUR TURN.
#: lead-stalls GI-004's violation column does not require the text to name the mechanism,
#: and neither does this list any more.
_INSTRUCTS_A_WAIT_LOOP = (
    "wait and call foundry-next again",
    "call foundry-next again",
    "poll until",
    "keep calling",
    "in a loop until",
    "sleep for",
    "check again in",
    "re-call foundry-next until",
    "wait for all",
    "wait for completion",
    "wait for them",
    "wait for the team",
    "wait until",
)


def _context_blocks(project_root, fdir) -> dict[str, str]:
    """The raw `CONTEXT:` text `_compute_next_action` emits for each branched
    action, keyed by action.

    Driven through the router rather than read off a constant, because these
    two strings are built inline from the counts the phase measured — that is
    the whole reason they are not constants, and a test that read a constant
    would not see what the lead is handed. Leaves the run at F3; callers that
    need an earlier phase read it before calling.
    """
    blocks: dict[str, str] = {}

    _write_state(fdir, phase="F1", cycle=0)
    blocks["build_castings"] = _compute_next_action(project_root)["instructions"]

    _write_state(fdir, phase="F3", cycle=2)
    _defect_ledger(fdir, [_tiered("D-001", "LIVE"), _tiered("D-002", "LATENT")])
    blocks["fix_defects"] = _compute_next_action(project_root)["instructions"]

    return blocks


def _lead_facing_wait_prose(project_root, fdir) -> dict[str, str]:
    """Every string this spec changed that a lead can read about waiting.

    lead-stalls D-005 -- AND THE `CONTEXT:` BLOCKS OF THE BRANCHED ACTIONS,
    WHICH SHIP IN THE SAME PAYLOAD AS THE IMPERATIVES ABOVE.
    ---------------------------------------------------------------------------
    This sweep read `_ACTION_IMPERATIVES` and the policy constant, so its
    population was the half of the payload this run rewrote. The other half --
    what `_compute_next_action` puts in `instructions`, printed beneath the
    header under the literal line "CONTEXT:" -- was never in it, and that is
    where the surviving bare wait was. A sweep over the surface that was fixed
    cannot see a defect on the surface beside it.
    """
    surfaces = {
        f"_ACTION_IMPERATIVES:{action}": text
        for action, text in sorted(_ACTION_IMPERATIVES.items())
    }
    surfaces["_WAITING_IS_NOT_STOPPING"] = _WAITING_IS_NOT_STOPPING
    surfaces["_BRANCHED_ACTION_CONTEXT"] = _BRANCHED_ACTION_CONTEXT
    for action, text in _context_blocks(project_root, fdir).items():
        surfaces[f"CONTEXT:{action}"] = text
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

    lead-stalls D-005: and over the `CONTEXT:` blocks that ship beneath the
    imperatives in the same payload, which is where the bare wait survived.
    """
    project_root, fdir = run_env
    _write_state(fdir, phase="F1", cycle=0)
    _write_manifest_with_castings(fdir, ["src/api/a.py"], no_ui=True)
    _progressing_ledger(fdir)
    _stale_stall_clock(fdir, 600)

    # Taken FIRST: `_lead_facing_wait_prose` leaves the run at F3 to reach the
    # `fix_defects` CONTEXT, and this notice is the F1 one.
    surfaces = {
        "stall_notice": foundry_next_action(project_root)["instructions"],
    }
    surfaces.update(_lead_facing_wait_prose(project_root, fdir))

    problems = {
        f"{name}:{spelling}": text
        for name, text in surfaces.items()
        for spelling in _INSTRUCTS_A_WAIT_LOOP
        if spelling in text.lower()
    }
    assert problems == {}, sorted(problems)
    # Floor: the CONTEXT blocks the sweep gained are actually IN it. A helper
    # that started answering `{}` would make this test pass over less.
    assert "CONTEXT:build_castings" in surfaces
    assert "CONTEXT:fix_defects" in surfaces
    assert surfaces["CONTEXT:fix_defects"].strip(), surfaces


def test_the_context_block_of_a_branched_action_names_no_sequence(run_env):
    """lead-stalls FR-006 / US-002 / GI-004, and D-005.

    ONE `Foundry-Next` payload carried both the new wait policy and its flat
    contradiction. The imperative said "END YOUR TURN ... Do NOT sleep, do NOT
    poll, do NOT re-call a tool in a loop"; the `CONTEXT:` block printed
    directly beneath it, from `_compute_next_action`'s F1 arm, said "CAST phase:
    teammates are building. Wait for all tasks to complete. When done: shut down
    team, TeamDelete, Foundry-Team-Down, then Foundry-Phase(phase='cast')."

    Three things wrong at once, and this pins all three:

      * a bare wait naming no mechanism -- lead-stalls GI-004's violation column;
      * "teammates are building" asserted on a reading where the same call had
        just measured that none are;
      * a crossing named WITHOUT `Foundry-Gate(phase='inspect')` -- two
        sequences for one crossing, in one payload, and the gate-less one was
        the unconditional one.

    It shipped on EVERY F1 `Foundry-Next` call, not only past the stall
    threshold, so it was read far more often than the WAITING arm this run had
    already reconciled. The sibling F3 arm carried the same defect verbatim, so
    both are driven here.
    """
    project_root, fdir = run_env
    _write_manifest_with_castings(fdir, ["src/api/a.py"], no_ui=True)

    blocks = _context_blocks(project_root, fdir)

    for action, text in blocks.items():
        lowered = text.lower()
        # No bare wait, by any spelling.
        assert "wait for" not in lowered, (action, text)
        assert "wait." not in lowered, (action, text)
        # No crossing, and so no second sequence to disagree with the one the
        # imperative above chose from the roster.
        assert "Foundry-Phase(" not in text, (action, text)
        assert "TeamDelete" not in text, (action, text)
        assert "Foundry-Team-Down" not in text, (action, text)
        # It defers instead, in the one spelling both arms quote.
        assert _BRANCHED_ACTION_CONTEXT in text, (action, text)

    # The F1 block no longer asserts an activity the server has not measured.
    assert "teammates are building" not in blocks["build_castings"].lower()
    # The F3 block keeps what the imperative CANNOT carry: the measured counts
    # and the per-defect bookkeeping call.
    assert "blocking defect(s) to fix" in blocks["fix_defects"]
    assert "Foundry-Fix(defect_id, cycle, authored_by, ...)" in blocks["fix_defects"]


def test_the_payload_states_one_sequence_for_the_f1_crossing(run_env):
    """lead-stalls D-005, driven end to end through the door the lead reads.

    The header and the CONTEXT ship concatenated in `result["instructions"]`,
    so "two sequences for one crossing" is a property of the WHOLE payload and
    not of either half. With the wave complete, exactly one surface names the
    F1 -> F2 crossing, and it is the one that names the gate that guards it.
    """
    project_root, fdir = run_env
    _write_state(fdir, phase="F1", cycle=0)
    _write_manifest_with_castings(fdir, ["src/api/a.py"], no_ui=True)
    _stalled_ledger(fdir)          # a roster that finished: the wave is done

    instructions = foundry_next_action(project_root)["instructions"]

    assert instructions.count("Foundry-Phase(phase='cast')") == 1, instructions
    assert "Foundry-Gate(phase='inspect')" in instructions, instructions
    # The gate is named BEFORE the phase call it guards, in the one place that
    # names either.
    assert instructions.index("Foundry-Gate(phase='inspect')") < instructions.index(
        "Foundry-Phase(phase='cast')"
    ), instructions
    # And the CONTEXT half of that payload names neither.
    context = instructions.split("CONTEXT:", 1)[1]
    assert "Foundry-Phase(" not in context, context
    assert "wait for" not in context.lower(), context


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
