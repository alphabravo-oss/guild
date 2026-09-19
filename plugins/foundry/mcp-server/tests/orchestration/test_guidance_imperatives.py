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

import asyncio
import contextlib
import json
import re
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from tests.orchestration._env import (  # noqa: F401
    _defect_ledger,
    _escalated_fixture,
    _halted_run,
    _progressing_ledger,
    _record_full_inspect_mode,
    _stale_stall_clock,
    _teams_active,
    _tiered,
    _write_manifest_with_castings,
    _write_prove,
    _write_spec,
    _write_state,
    _write_verdicts,
    patch_everywhere,
    run_env,
)

from foundry_mcp.tools import foundry_state
from foundry_mcp.tools.artifacts import (
    CAST_COMPLETE_MARKER,
    GATE_PASSED_MARKER,
    _hash_file,
    foundry_spec_hash,
)
from foundry_mcp.tools.evidence import foundry_accept_casting
from foundry_mcp.tools.foundry_spawn import foundry_cast_wave, foundry_spawn_teammate
from foundry_mcp.tools.orchestration import guidance as _guidance
from foundry_mcp.tools.orchestration.guidance import (  # noqa: F401
    _ACCEPTANCE_EVENT,
    _ACCEPTANCE_VERDICTS,
    _ACTION_CROSSINGS,
    _ACTION_IMPERATIVES,
    _A_SERVED_LIST_IS_ONE_MOVE,
    _BRANCH_CLOSE,
    _BRANCH_FALLBACK,
    _BRANCH_OPEN,
    _BRANCHED_ACTION_CONTEXT,
    _END_TURN,
    _GATE_THEN_PHASE_NOTE,
    _GRIND_SEALS_AT_CAP,
    _IMPERATIVES,
    _LEAD_CALLS,
    _SPAWN_DOORS,
    _SPAWN_IS_ONE_MOVE,
    _Step,
    _WAITING_IS_NOT_STOPPING,
    _WAITING_REPORTS_ONLY,
    _acceptance_destination,
    _acceptance_state,
    _acceptance_verdicts,
    _branch_state,
    _branch_states,
    _casting_slot,
    _chosen_branch,
    _call_record,
    _compute_next_action,
    _emitted_imperative,
    _format_imperative_header,
    _generic_header,
    _grind_start_seals,
    _parse_branches,
    _render_call,
    _resolved_imperative,
    _select_branch,
    _waiting_on_agents,
    foundry_next_action,
)
from foundry_mcp.tools.orchestration.teams import (
    SERVED_LIST_IS_ONE_MOVE,
    foundry_register_team,
    foundry_unregister_team,
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
#:
#: lead-stalls D-023 — AND A STEP WHOSE EXECUTION WAITS ON AN EARLIER CALL'S
#: ANSWER, HOWEVER THE CONDITION IS WORDED. The alternatives above see a line
#: that OPENS with IF and four fixed phrases, so they returned zero over
#: "(2) Only a send that answers that the teammate cannot be reached takes this
#: step" — a condition the lead evaluates by reading step (1)'s reply, shipped
#: in the refused branch at acdc00b. That is lead-stalls D-004's shape (a zero
#: computed over a space the defect cannot appear in) one detector over. The
#: last alternative is the structure rather than a spelling: inside ONE
#: sentence, a conditional word (if / unless / when / only / once / in case /
#: should / whether / depending) and then a word for what a call answered
#: (answers, reports, returns, replies, fails, succeeds, unreachable, cannot be
#: reached, ...) — in its third-person or past form, because the bare "report"
#: and "answer" are nouns in steps that are fine ("cite that sha in the
#: report"). `[^.\n]` keeps it to one sentence, so a qualifier in one
#: sentence and a call's return value named in the next are not joined.
#:
#: lead-stalls D-032 — AND THE SERVER'S OWN WORDS FOR NO, AND THE CONDITION'S
#: SHAPE RATHER THAN ITS VERB. The answer-verb list above left out the
#: vocabulary every refusal door here answers in (refused, rejected, errors,
#: ok False), so "(2) Unless step (1) is refused, END YOUR TURN." passed —
#: D-023's word list, one list over. Two changes. The vocabulary is added to
#: the list. And a last alternative keys on STRUCTURE: a conditional word
#: whose subject is an earlier STEP or a named CALL ("after step (1) ...",
#: "where Foundry-Gate ...", "in the event step (2) ..."), followed by any
#: predicate at all. A subject spelled as a noun ("when the spawn response
#: carries one") is a qualifier on data the lead appends, not a step waiting
#: on a step, and stays with the verb alternative above. What the step did is then irrelevant — a step made to wait on
#: another step's outcome is the defect however that outcome is spelled.
#: The subject has to follow the conditional word directly, so "where it does
#: not answer, the number falls back ... and step (3) refuses" — a fact about
#: the manifest the lead is told to ignore ("either way") — is not one.
_CONDITIONAL_WORD = (
    r"(?:if|unless|when|whenever|only|once|in case|in the event(?: that)?"
    r"|should|whether|depending|after|where|wherever|until|provided(?: that)?)"
)
_EARLIER_CALL = r"(?:step \(\d+\)|Foundry-[A-Z][A-Za-z-]*(?:\([^)]*\))?)"
#: lead-stalls D-034 — the bare-IF alternative opens a SENTENCE, not a LINE.
#: It was anchored `^`, so "... two accounts of one run. If an AGENT stream
#: finished ..." — IF opening the third sentence of a line, as `run_streams`
#: emitted it — passed while the same sentence on a line of its own was
#: matched. Where a sentence starts is a fact about the emitted text, and a
#: line break is only one of the ways it can start.
_CONDITION_SHAPES = re.compile(
    r"(?m)(?:^|(?<=[.!?:])[ \t]+)\s*(?:[-*•]|\(\d+\))?\s*(?:IF|UNLESS)\b"
    r"|\bdepends on\b"
    r"|\bwhichever\b"
    r"|\bdecide whether\b"
    r"|\bif you (?:are|have|think|judge)\b"
    r"|\b(?:if|unless|when|whenever|only|once|in case|should|whether|depending)\b"
    r"[^.\n]{0,160}?"
    r"\b(?:answers|answered|reports|reported|returns|returned|replies|replied"
    r"|responds|responded|fails|failed|succeeds|succeeded|unreachable"
    r"|refused|refuses|rejected|rejects|errors|errored|ok (?:False|True)"
    r"|comes back|came back"
    r"|cannot be reached|can(?:no|')t reach|not (?:be )?(?:reached|delivered))\b"
    r"|\b" + _CONDITIONAL_WORD + r"\s+" + _EARLIER_CALL
    + r"(?:'s (?:answer|reply|result|response|verdict))?\s+(?!\()[A-Za-z]",
    re.IGNORECASE,
)

#: lead-stalls D-034 — AND A CONDITION THAT CHOOSES BETWEEN TWO MOVES, JUDGED
#: PER SENTENCE AS EMITTED. Every alternative above keys on a spelling: IF at
#: an opening, an outcome verb, or an earlier step as the subject. "If an AGENT
#: stream finished and no record exists, that is a finding about the stream —
#: re-dispatch it, or file it — not a gap for you to fill in" has an outcome
#: in none of those spellings, and what makes it the defect is not its words
#: but its shape: a sentence that OPENS on a condition and then offers the lead
#: two moves joined by "or". So that shape is checked on its own, whatever the
#: conditional word, over each sentence the lead reads. A move is a clause
#: `_order_openers` reads as an order and that carries an object ("file it"),
#: so a condition over a list of stream names ("trace, prove, or test") is not
#: a choice. The wake event ("When the notification arrives, call Foundry-Next
#: and follow what it says then.") names one move and stays legal.
_EMITTED_SENTENCE = re.compile(r"(?<=[.!?])\s+|\n")
_OPENS_ON_A_CONDITION = re.compile(
    r"\s*(?:[-*•]|\(\d+\))?\s*" + _CONDITIONAL_WORD + r"\b", re.IGNORECASE,
)
_ALTERNATIVE = re.compile(r",?\s+or\s+")
_CLAUSE_EDGE = re.compile(r"\s*[,;:—]\s*")


def _is_a_move(clause: str) -> bool:
    """A clause the lead would execute: an order with an object."""
    return len(clause.split()) >= 2 and bool(_order_openers(clause))


def _offers_a_choice(sentence: str) -> bool:
    """Does ``sentence`` open on a condition and join two moves with "or"?"""
    if not _OPENS_ON_A_CONDITION.match(sentence):
        return False
    parts = _ALTERNATIVE.split(sentence)
    return any(
        _is_a_move(_CLAUSE_EDGE.split(left)[-1])
        and _is_a_move(_CLAUSE_EDGE.split(right.rstrip("."))[0])
        for left, right in zip(parts, parts[1:])
    )


class _ConditionDetector:
    """`_CONDITION_SHAPES`, then the per-sentence choice (lead-stalls D-034).

    One object with the regex's `search` so every caller keeps asking one
    question; a second detector beside the first is how D-023, D-032 and D-034
    each found a conditional one of them could not see. The choice match is a
    real `re.Match` over the sentence's span, so the audit quotes it the same
    way.
    """

    def search(self, text: str) -> re.Match | None:
        hit = _CONDITION_SHAPES.search(text)
        if hit:
            return hit
        for sentence in _EMITTED_SENTENCE.split(text):
            if _offers_a_choice(sentence):
                return re.compile(re.escape(sentence.strip())).search(text)
        return None


_HANDS_OVER_THE_CONDITION = _ConditionDetector()

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

#: Every placeholder `_format_imperative_header` resolves. A slot still present
#: in an emitted header is a call the lead cannot make -- `Foundry-Gate(phase=
#: '{gate}')` and `TeamCreate('grind-r-cycle-{cycle}')` are both strings a lead
#: trained to execute the first call mentioned will try to execute literally.
#: Named rather than matched as `{...}`, because `add_castings` carries the
#: literal prose `casting-{id}-prompt.md` and that is not a slot.
_SUBSTITUTED_SLOTS = (
    "{run}", "{gate}", "{token}", "{halt_cause}",
    "{wave}", "{built_wave}", "{cycle}", "{casting}",
)

#: Every team name an emitted header QUOTES, and the shape one has to have for
#: the lead to be able to create it (lead-stalls D-012).
#:
#: This is the decidable half of "a call the lead cannot make". A general hunt
#: for placeholder-looking tokens is NOT decidable here and must not be
#: attempted: `Foundry-Spawn-Teammate(casting_id=N, phase='grind')` uses the
#: same `N` legitimately, as prose for "per casting", so a detector that flagged
#: it would flag the correct entry and the defective one alike. A quoted TEAM
#: NAME is different: it is a literal the lead passes to `TeamCreate`, it has
#: one grammar, and `grind-{run}-cycle-N` fails that grammar while every
#: correct spelling passes it.
_QUOTED_TEAM_NAME = re.compile(
    r"(?:TeamCreate\(|team_name=)'([^']*)'"
)
_CREATABLE_TEAM_NAME = re.compile(
    r"^(?:cast|grind)-[A-Za-z0-9][A-Za-z0-9-]*-(?:wave|cycle)-\d+$"
)

#: The `run_name` every emission site is formatted with. Named once because
#: `_emitted_branch` has to resolve `{run}` the same way the formatter did, and
#: a second literal here is how those two drift apart.
_AUDIT_RUN = "audit"


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
_LIVENESS_READINGS: tuple[tuple[str, object, str | tuple[str, ...]], ...] = (
    # waiting -> live, whatever the other two fields say.
    ("live", {"waiting": True, "count": 2, "detail": "oldest progress 1m 0s ago"}, "live"),
    # roster 3 / team registered: the wave is dispatched and nothing advances.
    # lead-stalls D-013 — `cast_wave_pending: 0` is STATED rather than left
    # absent. This row means "a measured reading showing every wave done", and
    # an absent field says the opposite thing: the manifest did not answer. The
    # row pinned the defect while it carried neither.
    ("idle", {"waiting": False, "roster_agents": 3, "teams_active": True,
              "cast_wave_pending": 0, "cast_wave_built": 1}, "idle"),
    # roster 3 / team torn down: between Foundry-Team-Down and the crossing.
    # The ledger outlives the team, so this is still the wave-complete state --
    # and what makes it the wave-complete state is the measured 0, not the
    # roster count (lead-stalls D-013).
    ("torn-down", {"waiting": False, "roster_agents": 3, "teams_active": False,
                   "cast_wave_pending": 0, "cast_wave_built": 1}, "idle"),
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
    # lead-stalls D-009 — THE TWO READINGS THE SWEEP COULD NOT TELL APART, AND
    # THE WHOLE REASON THE ROWS ABOVE WERE NOT ENOUGH.
    # ------------------------------------------------------------------------
    # Every row above varies `roster_agents` x `teams_active` and NOTHING ELSE,
    # so the sweep's zero was computed over a space in which wave position does
    # not exist -- and `{"waiting": False, "roster_agents": >0}` is byte-
    # identical at "wave 1 finished, wave 2 never dispatched" and at "the final
    # wave is done". The first of those is the state THIS run stood in between
    # its own two waves while being handed the teardown. An audit that cannot
    # vary wave position cannot discharge lead-stalls OT-002 for that class, so
    # these two rows vary it and each names the branch it is owed.
    (
        "wave-boundary",
        {"waiting": False, "roster_agents": 2, "teams_active": False,
         "cast_wave_pending": 2, "cast_wave_built": 1},
        "undispatched",
    ),
    (
        "final-wave-done",
        {"waiting": False, "roster_agents": 3, "teams_active": True,
         "cast_wave_pending": 0, "cast_wave_built": 2},
        "idle",
    ),
    # lead-stalls D-013 — THE READING THE TABLE HAD NO HONEST ROW FOR, AND THE
    # ONE THE DEFECT WAS DRIVEN ON.
    # ------------------------------------------------------------------------
    # A ROSTER that answered and a MANIFEST that did not. `unmeasured` above is
    # the watchdog failing entirely (`liveness is None`); this is the watchdog
    # succeeding and the castings manifest coming back unreadable underneath it
    # -- truncated mid-write, `waves` reset to the `[]` Foundry-Init seeds, or
    # wave numbers written as strings. The two rows the roster arm used to serve
    # now state a measured 0, so without this row nothing in the sweep exercises
    # an absent position at all, and lead-stalls FR-008's zero would once again
    # be computed over a space the defect cannot appear in -- which is D-004's
    # and D-009's failure a third time.
    #
    # It is owed `undispatched`, on the asymmetry `_branch_state`'s docstring
    # states: a spurious dispatch costs a `TeamCreate` that answers "already
    # registered", a spurious teardown crosses a phase gate over castings
    # nothing built.
    (
        "unreadable-manifest",
        {"waiting": False, "roster_agents": 3, "teams_active": False},
        "undispatched",
    ),
    # lead-stalls D-020 — THE ACCEPTANCE AXIS, WHICH NO ROW ABOVE VARIES.
    # ------------------------------------------------------------------------
    # A done line was a built casting, so every row above that measured
    # `cast_wave_pending == 0` was owed the teardown — including the state a
    # REFUSED acceptance leaves. These rows name a casting refused, a casting
    # done and not yet accepted, and both under a running teammate. The owed
    # column is a PREFERENCE ORDER here, derived by hand from the run state: an
    # entry that declares the first name is owed it, and one that does not is
    # owed the next — `fix_defects` and `run_streams` declare no acceptance
    # branch, so they are owed the base state exactly as before.
    (
        "refused",
        {"waiting": False, "roster_agents": 2, "teams_active": True,
         "teams_registered": [f"cast-{_AUDIT_RUN}-wave-1"],
         "cast_wave_pending": 0, "cast_wave_built": 1,
         "cast_refused": ["2"], "cast_unaccepted": ["1"],
         "cast_refused_team": f"cast-{_AUDIT_RUN}-wave-1"},
        ("refused", "idle"),
    ),
    # lead-stalls D-022 — the same refusal with the team torn down. The two
    # rows differ in the team scan ONLY, and each is owed its own branch: the
    # server picks send-back or re-dispatch, so the lead never has to.
    (
        "refused-team-down",
        {"waiting": False, "roster_agents": 2, "teams_active": False,
         "teams_registered": [],
         "cast_wave_pending": 0, "cast_wave_built": 1,
         "cast_refused": ["2"], "cast_unaccepted": ["1"],
         "cast_refused_team": f"cast-{_AUDIT_RUN}-wave-1"},
        ("redispatch", "idle"),
    ),
    # lead-stalls D-031 — `teams_active` True with the refused casting's OWN
    # team absent: a live pane in another project's tmux session, or another
    # wave's team. Owed the re-dispatch, because the teammate the send-back
    # would address is not registered anywhere.
    (
        "refused-foreign-pane",
        {"waiting": False, "roster_agents": 2, "teams_active": True,
         "teams_registered": [],
         "cast_wave_pending": 0, "cast_wave_built": 1,
         "cast_refused": ["2"], "cast_unaccepted": ["1"],
         "cast_refused_team": f"cast-{_AUDIT_RUN}-wave-1"},
        ("redispatch", "idle"),
    ),
    (
        "refused-other-team",
        {"waiting": False, "roster_agents": 2, "teams_active": True,
         "teams_registered": [f"cast-{_AUDIT_RUN}-wave-2"],
         "cast_wave_pending": 0, "cast_wave_built": 2,
         "cast_refused": ["2"], "cast_unaccepted": [],
         "cast_refused_team": f"cast-{_AUDIT_RUN}-wave-1"},
        ("redispatch", "idle"),
    ),
    (
        "unaccepted",
        {"waiting": False, "roster_agents": 1, "teams_active": False,
         "cast_wave_pending": 2, "cast_wave_built": 1,
         "cast_refused": [], "cast_unaccepted": ["1"]},
        ("unaccepted", "undispatched"),
    ),
    # A running teammate outranks both: the woken lead ends its turn, and the
    # acceptance is still owed on the reading the last notification brings.
    (
        "live-with-owed-acceptance",
        {"waiting": True, "count": 1, "detail": "oldest progress 0m 5s ago",
         "cast_wave_pending": 1, "cast_wave_built": 1,
         "cast_refused": ["2"], "cast_unaccepted": ["1"]},
        "live",
    ),
)


def _owed_label(owed: str | tuple[str, ...]) -> str:
    """One spelling of a row's owed column, for the report and its floor."""
    return owed if isinstance(owed, str) else ">".join(owed)


def _owed_branch(action: str, owed: str | tuple[str, ...]) -> str:
    """The branch name `action` is OWED on a reading whose run state is `owed`.

    A state an entry declares no branch for resolves through `_BRANCH_FALLBACK`,
    which is the `.get(..., default)` shape `_halt_cause` uses — so `fix_defects`
    on an `undispatched` reading is owed its `idle` branch, and that is a
    property of what the entry DECLARES rather than of what the selector did.
    A tuple is a preference order (lead-stalls D-020): the first name the entry
    declares.
    """
    branches = _parse_branches(_ACTION_IMPERATIVES.get(action, ""))
    for name in ((owed,) if isinstance(owed, str) else owed):
        if branches.get(name):
            return name
    return _BRANCH_FALLBACK


def _emitted_branch(
    action: str, text: str, run_name: str, liveness: object = None,
    cycle: object = None, details: dict | None = None,
) -> str:
    """Which declared branch of `action` the lead ACTUALLY received.

    Every declared branch is rendered for this reading through
    `_resolved_imperative` — the resolver the lead's header takes, so a slot
    added to an entry resolves here the same way — and matched by EQUALITY
    against the served header. Equality rather than a first-line or keyword
    match because `_CAST_WAVE_COMPLETE` and `_CAST_WAVE_UNDISPATCHED` open on
    the identical line, and a discriminator that could not tell those two
    apart is exactly the one lead-stalls D-003 needed. What the branch is OWED
    is never asked of the router: the owed columns are written by hand.

    ``"?"`` when the text matches no declared branch, which is itself a finding.
    """
    entry = _IMPERATIVES.get(action)
    for name, branch in (entry.items() if isinstance(entry, dict) else ()):
        rendered = _resolved_imperative(
            branch, action, details or {}, run_name, "", liveness, cycle,
        )[1]
        if rendered == text:
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


#: lead-stalls D-040 — the roster this run's own cycle-10 INSPECT recorded,
#: which is the reading the idle `run_streams` header was driven on and named
#: no call for `test01` under.
_C10_ROSTER = ("trace", "prove", "test", "test01")


def _site_details(action: str) -> dict:
    """The `details` an emission site is formatted with: the router's own
    shape, so the stream steps expand from a roster and not from the
    template fallback."""
    if action == "run_streams":
        return {"missing_streams": list(_C10_ROSTER)}
    # lead-stalls D-050 — `run_temper` ends in the gate out of F5, which the
    # router names on `details["crossing"]`: `done` on a --temper run.
    if action == "run_temper":
        return {"crossing": {"gate": "done", "token": "done"}}
    return {}


def _audit_sites() -> list[tuple[str, str, str, str, object]]:
    """(action, site label, emitted text, owed branch, reading) for every string
    the table can emit.

    One enumeration, two readers: the detector below and the report it feeds.
    A second copy of this loop in the evidence command would be a second
    spelling of what the audit's population IS.

    The fourth element is lead-stalls D-004's addition — the branch this
    reading is owed, so the sweep can judge ROUTING and not only shape. The
    fifth is lead-stalls D-009's: the READING the site was emitted on, which
    `_emitted_branch` needs to resolve the wave slots the same way the formatter
    did. Carried rather than looked up by label, so the two cannot drift.
    ``""`` for the twenty unbranched entries, which have nothing to route.
    """
    return [
        (
            action,
            f"{action}@{phase}[{label}]",
            _format_imperative_header(
                action, "", _site_details(action), run_name=_AUDIT_RUN,
                phase=phase, liveness=liveness,
            ),
            _owed_branch(action, owed) if _parse_branches(
                _ACTION_IMPERATIVES[action]
            ) else "",
            liveness,
        )
        for action in sorted(_ACTION_IMPERATIVES)
        for phase in _emission_phases(action)
        for label, liveness, owed in _LIVENESS_READINGS
    ]


# --------------------------------------------------------------------------- #
# lead-stalls D-015..D-020 — the ROUTER, driven with a REALLY registered team
# --------------------------------------------------------------------------- #
#
# Every sweep above hands `_format_imperative_header` a synthetic reading, and
# every `foundry_next_action` test before cycle 7 left the team scan inactive.
# So the arm that preempted all of it — `_compute_next_action` answering
# `cleanup_teams` for ANY registered team, ahead of F1 and F3 — was invisible:
# the header sweep showed branches the router never served (D-018), and the
# stall notice was never judged beside the header it was printed over (D-019).
#
# These drives go through the door the lead calls. The team is registered by
# the real `Foundry-Team-Up` handler into a scratch HOME holding the directory
# `TeamCreate` makes; only the tmux pane scan is stubbed. Plain functions, not
# fixtures, so the evidence logs can re-execute them outside pytest.

_ROUTE_RUN = "route"
_CAST_TEAM = f"cast-{_ROUTE_RUN}-wave-1"
_GRIND_TEAM = f"grind-{_ROUTE_RUN}-cycle-0"
_NEXT_ACTION_MARKER = "═══ YOUR NEXT ACTION ═══"
_NOTICE_OPENERS = ("⏳", "⚠", "✅")
_TEARDOWN_WORDS = ("TeamDelete", "Foundry-Team-Down", "stop working")


#: Where an arrange writes the machine's tmux answer, under the scratch HOME.
_PANES_FILE = "live-panes.json"


def _no_panes(*_args, **_kwargs) -> dict:
    """The tmux pane scan, stubbed: no tmux, unless the arrange wrote the
    live panes this machine answers with (lead-stalls D-031)."""
    written = Path.home() / _PANES_FILE
    if written.is_file():
        live = [tuple(row) for row in json.loads(written.read_text(encoding="utf-8"))]
        return {"available": True, "live": live, "zombie": [], "user": [], "lead": None}
    return {"available": False, "live": [], "zombie": [], "user": [], "lead": None}


@contextlib.contextmanager
def _router_run(base: Path):
    """A live run under ``base`` whose team question is answered for real.

    Yields ``(project_root, fdir, teams_dir)``. HOME is ``base/home``, so the
    registration and the router's scan both look in a directory this drive
    owns, and nothing the machine's own `~/.claude/teams` holds can leak in.
    """
    with pytest.MonkeyPatch.context() as mp:
        home = base / "home"
        teams_dir = home / ".claude" / "teams"
        teams_dir.mkdir(parents=True)
        mp.setenv("HOME", str(home))
        patch_everywhere(mp, "live_teammate_panes", _no_panes)
        root = base / "proj"
        fdir = root / "foundry-archive" / _ROUTE_RUN
        (fdir / "castings").mkdir(parents=True)
        previous = foundry_state.get_active_run()
        foundry_state.set_active_run(_ROUTE_RUN)
        try:
            yield str(root), fdir, teams_dir
        finally:
            if previous:
                foundry_state.set_active_run(previous)
            else:
                foundry_state.clear_active_run()


def _register(root: str, teams_dir: Path, name: str) -> None:
    """TeamCreate (the directory) and the real Foundry-Team-Up."""
    (teams_dir / name).mkdir(exist_ok=True)
    up = foundry_register_team(name, project_root=root)
    assert up.get("ok") is True, up


def _worked(fdir: Path, cid: str, *, done: bool, at: datetime | None = None) -> None:
    """A casting ledger the teammate itself wrote, dated ``at`` (default now)."""
    stamp = (at or datetime.now(timezone.utc)).isoformat()
    lines = [{"timestamp": stamp, "phase": "cast", "step": "writing the handler"}]
    if done:
        lines.append({"timestamp": stamp, "phase": "cast", "step": "committed",
                      "done": True})
    (fdir / "progress").mkdir(parents=True, exist_ok=True)
    (fdir / "progress" / f"casting-{cid}.jsonl").write_text(
        "".join(json.dumps(line) + "\n" for line in lines), encoding="utf-8"
    )


def _cast(fdir: Path, waves: dict[int, list[str]]) -> None:
    _write_state(fdir, phase="F1", cycle=0)
    _wave_manifest(fdir, waves)


def _grind(fdir: Path, *, open_defect: bool) -> None:
    _write_state(fdir, phase="F3", cycle=0)
    _defect_ledger(fdir, [
        _tiered("D-001", "LIVE", status="open" if open_defect else "fixed")
    ])


def _inspect(fdir: Path, roster: tuple[str, ...] = ("trace", "prove", "test")) -> None:
    _write_spec(fdir, ["FR-1"])
    _write_state(fdir, phase="F2", cycle=1)
    _record_full_inspect_mode(fdir, cycle=1, required_streams=roster)
    _write_manifest_with_castings(fdir, ["src/api/a.py"], no_ui=True)


def _arrange_cast_live(root, fdir, teams):
    _cast(fdir, {1: ["1", "2"]})
    _register(root, teams, _CAST_TEAM)
    _worked(fdir, "1", done=True)
    _verdict(fdir, "1", "accepted")
    _worked(fdir, "2", done=False)


def _arrange_cast_live_owed(root, fdir, teams):
    # PROVE's D-015 drive: casting 1's notification woke the lead, casting 2 is
    # still writing, and casting 1 is not accepted yet.
    _cast(fdir, {1: ["1", "2"]})
    _register(root, teams, _CAST_TEAM)
    _worked(fdir, "1", done=True)
    _worked(fdir, "2", done=False)


def _arrange_cast_not_spawned(root, fdir, teams):
    _cast(fdir, {1: ["1", "2"]})
    _register(root, teams, _CAST_TEAM)


def _arrange_cast_unaccepted(root, fdir, teams):
    _cast(fdir, {1: ["1"], 2: ["2"]})
    _register(root, teams, _CAST_TEAM)
    _worked(fdir, "1", done=True, at=datetime.now(timezone.utc) - timedelta(minutes=1))


def _arrange_cast_refused(root, fdir, teams):
    _arrange_cast_unaccepted(root, fdir, teams)
    _verdict(fdir, "1", "refused")


def _arrange_cast_refused_team_down(root, fdir, teams):
    # lead-stalls D-022 — the refusal after TeamDelete: its teammate is gone.
    _arrange_cast_refused(root, fdir, teams)
    (teams / _CAST_TEAM).rmdir()


def _arrange_cast_refused_foreign_pane(root, fdir, teams):
    # lead-stalls D-031 / D-028 — PROVE's drive: no team registered at all, and
    # one live teammate pane in ANOTHER project's tmux session. `teams_active`
    # reads True; the refused casting's own team is not up, so the send-back
    # has nobody to address. The cleanup arm fires on the pane, and only
    # `redispatch` counting as owed work keeps it from answering the teardown.
    _cast(fdir, {1: ["1"], 2: ["2"]})
    _worked(fdir, "1", done=True, at=datetime.now(timezone.utc) - timedelta(minutes=1))
    _verdict(fdir, "1", "refused")
    (teams.parent.parent / _PANES_FILE).write_text(
        json.dumps([["other-project:1.1", "@researcher", "claude"]]),
        encoding="utf-8",
    )


def _arrange_cast_refused_other_team(root, fdir, teams):
    # lead-stalls D-031 / D-028 — a team IS registered, but it is not the
    # refused casting's: wave 2's. The name is the reading's, not the scan's.
    _cast(fdir, {1: ["1"], 2: ["2"]})
    _register(root, teams, f"cast-{_ROUTE_RUN}-wave-2")
    _worked(fdir, "1", done=True, at=datetime.now(timezone.utc) - timedelta(minutes=1))
    _verdict(fdir, "1", "refused")


def _arrange_cast_unmeasured_team_up(root, fdir, teams):
    # lead-stalls D-021 — a registered CAST team and a manifest that does not
    # answer: the `waves: []` Foundry-Init seeds, so no wave position is
    # measured. The `_team_work_in_flight` rung for this reading had no drive.
    _write_state(fdir, phase="F1", cycle=0)
    (fdir / "castings" / "manifest.json").write_text(
        json.dumps({"castings": [], "waves": []}), encoding="utf-8",
    )
    _register(root, teams, _CAST_TEAM)
    _worked(fdir, "1", done=True)


def _arrange_cast_refusal_answered(root, fdir, teams):
    _cast(fdir, {1: ["1"], 2: ["2"]})
    _register(root, teams, _CAST_TEAM)
    now = datetime.now(timezone.utc)
    _verdict(fdir, "1", "refused", at=(now - timedelta(minutes=5)).isoformat())
    _worked(fdir, "1", done=True, at=now)


def _arrange_cast_boundary_team_up(root, fdir, teams):
    _cast(fdir, {1: ["1"], 2: ["2"]})
    _register(root, teams, _CAST_TEAM)
    _worked(fdir, "1", done=True)
    _verdict(fdir, "1", "accepted")


def _arrange_cast_boundary(root, fdir, teams):
    _arrange_cast_boundary_team_up(root, fdir, teams)
    (teams / _CAST_TEAM).rmdir()                    # TeamDelete


def _arrange_cast_built_team_up(root, fdir, teams):
    _cast(fdir, {1: ["1"]})
    _register(root, teams, _CAST_TEAM)
    _worked(fdir, "1", done=True)
    _verdict(fdir, "1", "accepted")


def _arrange_cast_built(root, fdir, teams):
    _arrange_cast_built_team_up(root, fdir, teams)
    (teams / _CAST_TEAM).rmdir()


def _arrange_grind_live(root, fdir, teams):
    _grind(fdir, open_defect=True)
    _register(root, teams, _GRIND_TEAM)
    _worked(fdir, "1", done=False)


def _arrange_grind_live_all_fixed(root, fdir, teams):
    # The window between a teammate's last Foundry-Fix and its done line.
    _grind(fdir, open_defect=False)
    _register(root, teams, _GRIND_TEAM)
    _worked(fdir, "1", done=False)


def _arrange_grind_finished_team_up(root, fdir, teams):
    _grind(fdir, open_defect=True)
    _register(root, teams, _GRIND_TEAM)
    _worked(fdir, "1", done=True)


def _arrange_grind_idle(root, fdir, teams):
    _grind(fdir, open_defect=True)


def _arrange_grind_all_fixed(root, fdir, teams):
    _grind(fdir, open_defect=False)


def _arrange_inspect_live(root, fdir, teams):
    _inspect(fdir)
    _progressing_ledger(fdir, agent="prove")
    _progressing_ledger(fdir, agent="trace")


def _arrange_inspect_idle(root, fdir, teams):
    # lead-stalls D-040 — this run's own cycle-10 roster, test01 included.
    _inspect(fdir, _C10_ROSTER)


def _prompt_files(fdir: Path, ids) -> None:
    for cid in ids:
        (fdir / "castings" / f"casting-{cid}-prompt.md").write_text(
            _ROUTE_PROMPT, encoding="utf-8",
        )


#: lead-stalls D-038 — THE THREE STATES A SPAWN DOOR LEAVES BEFORE ITS AGENT
#: HAS WRITTEN A LINE, each reached through the REAL door, so the only ledger
#: line each casting has is the FRESH seed the door wrote. Under
#: `_SPAWN_IS_ONE_MOVE` the Agent call was made in the same move as the door,
#: so each is owed the `live` branch — and the audit's ONE_MOVE and SPAWN
#: checks are what hold the header that named the door to saying so.
def _arrange_cast_dispatched_fresh_seed(root, fdir, teams):
    # (A) CAST: wave 1 = castings 1 and 2, a real Team-Up, a real Cast-Wave.
    _cast(fdir, {1: ["1", "2"]})
    _prompt_files(fdir, ["1", "2"])
    _register(root, teams, _CAST_TEAM)
    assert foundry_cast_wave(1, "cast", project_root=root)["ok"] is True


def _arrange_grind_dispatched_fresh_seed(root, fdir, teams):
    # (B) GRIND: D-001 open, a real Team-Up, a real Spawn-Teammate(grind).
    _grind(fdir, open_defect=True)
    _wave_manifest(fdir, {1: ["1"]})
    _prompt_files(fdir, ["1"])
    _register(root, teams, _GRIND_TEAM)
    assert foundry_spawn_teammate(1, "grind", project_root=root)["ok"] is True


def _arrange_cast_redispatched_fresh_seed(root, fdir, teams):
    # (C) The refusal route: casting 1 done a minute ago, refused, no team, and
    # the redispatch branch's step (1) — the real Spawn-Teammate(cast) — made,
    # which appends the seed after the done line.
    _cast(fdir, {1: ["1"], 2: ["2"]})
    _prompt_files(fdir, ["1", "2"])
    _worked(fdir, "1", done=True,
            at=datetime.now(timezone.utc) - timedelta(minutes=1))
    _verdict(fdir, "1", "refused")
    assert foundry_spawn_teammate(1, "cast", project_root=root)["ok"] is True


def _arrange_inspect_stale_team(root, fdir, teams):
    # A GRIND team nobody tore down, and streams running beside it. The team is
    # stale by construction at F2; the streams are not its teammates.
    _inspect(fdir)
    _register(root, teams, _GRIND_TEAM)
    _progressing_ledger(fdir, agent="prove")


#: lead-stalls D-050 — F5 and F5.5, each at the three points the router must
#: tell apart: the phase's own list owed, its gate passed (the record the
#: server writes, dropped here as the gate writes it), and a blocking defect
#: filed — which outranks a gate that passed before it was filed.
def _post_assay(
    fdir: Path, phase: str, *, temper: bool, nyquist: bool,
    passed: str | None = None, filed: bool = False,
) -> None:
    _write_spec(fdir, ["FR-1"])
    _write_state(fdir, phase=phase, cycle=1, temper=temper, nyquist=nyquist)
    _defect_ledger(
        fdir, [_tiered("D-001", "LIVE", status="open")] if filed else [],
    )
    if passed:
        (fdir / GATE_PASSED_MARKER).write_text(
            json.dumps({"phase": passed, "at": _LONG_AGO}), encoding="utf-8",
        )


def _arrange_temper_owed(root, fdir, teams):
    _post_assay(fdir, "F5", temper=True, nyquist=False)


def _arrange_temper_other_gate(root, fdir, teams):
    # A passed gate that is not the one out of F5 is not the exit.
    _post_assay(fdir, "F5", temper=True, nyquist=False, passed="grind")


def _arrange_temper_gate_passed(root, fdir, teams):
    _post_assay(fdir, "F5", temper=True, nyquist=False, passed="done")


def _arrange_temper_nyquist_passed(root, fdir, teams):
    _post_assay(fdir, "F5", temper=True, nyquist=True, passed="nyquist")


def _arrange_temper_filed(root, fdir, teams):
    _post_assay(fdir, "F5", temper=True, nyquist=False, passed="done", filed=True)


def _arrange_nyquist_owed(root, fdir, teams):
    _post_assay(fdir, "F5.5", temper=True, nyquist=True)


def _arrange_nyquist_gate_passed(root, fdir, teams):
    _post_assay(fdir, "F5.5", temper=True, nyquist=True, passed="done")


def _arrange_nyquist_filed(root, fdir, teams):
    _post_assay(fdir, "F5.5", temper=False, nyquist=True, filed=True)


#: lead-stalls GI-008 / FR-007 (D-051..D-053) — EVERY OTHER ACTION THE ROUTER
#: RETURNS. The rows above reached six of the twenty-two, so the CONTEXT of
#: F0, of a clean or filed F2, and of every F4 crossing was never read: the
#: ASSAY crossing's CONTEXT ordered the calls the opposite way from its header
#: (D-051), the F4 -> F6 CONTEXT opened on the gate its report must precede
#: (D-052), and the ASSAY-rejection list could not succeed with nothing filed
#: (D-053). Each state is built the way the run leaves it.
def _arrange_no_run(root, fdir, teams):
    foundry_state.clear_active_run()


def _arrange_decompose_empty(root, fdir, teams):
    _write_spec(fdir, ["FR-1"])
    _write_state(fdir, phase="F0", cycle=0)


def _arrange_decompose_done(root, fdir, teams):
    _write_spec(fdir, ["FR-1"])
    _write_state(fdir, phase="F0", cycle=0)
    _write_manifest_with_castings(fdir, ["src/api/a.py"], no_ui=True)


#: lead-stalls D-056 — a decomposition writer's ledger, at the path and in the
#: shape the `add_castings` idle branch's prompt tells each writer to keep.
def _decompose_ledger(fdir: Path, domain: str, *, done: bool) -> None:
    stamp = datetime.now(timezone.utc).isoformat()
    lines = [{"timestamp": stamp, "phase": "decompose", "step": "reading the spec"}]
    if done:
        lines.append({"timestamp": stamp, "phase": "decompose",
                      "step": "wrote the casting prompt", "done": True})
    (fdir / "progress").mkdir(parents=True, exist_ok=True)
    (fdir / "progress" / f"decompose-{domain}.jsonl").write_text(
        "".join(json.dumps(line) + "\n" for line in lines), encoding="utf-8"
    )


def _arrange_decompose_writing(root, fdir, teams):
    # Two writers spawned, neither has written its manifest entry yet.
    _arrange_decompose_empty(root, fdir, teams)
    _decompose_ledger(fdir, "api", done=False)
    _decompose_ledger(fdir, "ui", done=False)


def _arrange_decompose_first_writer_done(root, fdir, teams):
    # PROVE's s3 state (D-056): writer A wrote casting 1 and its done line and
    # its notification woke the lead; writer B is still writing.
    _arrange_decompose_done(root, fdir, teams)
    _decompose_ledger(fdir, "api", done=True)
    _decompose_ledger(fdir, "ui", done=False)


def _arrange_decompose_writers_done(root, fdir, teams):
    _arrange_decompose_done(root, fdir, teams)
    _decompose_ledger(fdir, "api", done=True)
    _decompose_ledger(fdir, "ui", done=True)


def _arrange_cast_complete(root, fdir, teams):
    _write_spec(fdir, ["FR-1"])
    _write_state(fdir, phase="F1", cycle=0)
    _write_manifest_with_castings(fdir, ["src/api/a.py"], no_ui=True)
    (fdir / CAST_COMPLETE_MARKER).write_text("x\n", encoding="utf-8")


def _arrange_inspect_width_unrecorded(root, fdir, teams):
    _write_spec(fdir, ["FR-1"])
    _write_state(fdir, phase="F2", cycle=1)
    _write_manifest_with_castings(fdir, ["src/api/a.py"], no_ui=True)


def _clean_inspect(fdir: Path, mode: str, *, filed: bool = False) -> None:
    """Every stream the recorded roster requires has recorded this cycle."""
    _write_spec(fdir, ["FR-1"])
    _write_state(fdir, phase="F2", cycle=1)
    _write_manifest_with_castings(fdir, ["src/api/a.py"], no_ui=True)
    recorded = _record_full_inspect_mode(fdir, cycle=1)
    if mode != "FULL":
        state = json.loads((fdir / "state.json").read_text(encoding="utf-8"))
        state["inspect_modes"][-1].update(mode=mode, rule="delta")
        (fdir / "state.json").write_text(json.dumps(state), encoding="utf-8")
    for stream in recorded["required_streams"]:
        (fdir / f".{stream}-complete").write_text(
            "2020-01-01T00:00:00+00:00 cycle=1\nitems_checked=10\n"
            "items_total=10\ncoverage=100%\nfindings=0\n",
            encoding="utf-8",
        )
    _defect_ledger(fdir, [_tiered("D-001", "LIVE")] if filed else [])


def _arrange_inspect_filed(root, fdir, teams):
    _clean_inspect(fdir, "FULL", filed=True)


def _arrange_inspect_clean_full(root, fdir, teams):
    _clean_inspect(fdir, "FULL")


def _arrange_inspect_clean_delta(root, fdir, teams):
    _clean_inspect(fdir, "DELTA")


def _escalated(fdir: Path, *, tier: str) -> None:
    """The class FDC persisted ESCALATED over three instances of ``tier``.

    A LATENT backlog leaves a clean INSPECT that convergence ST-010 still
    holds DONE open for, which is where `_still_escalated_notice` rides the
    CONTEXT; LIVE instances are the GRIND arm's `_escalation_notice`."""
    _escalated_fixture(fdir, open_instances=True)
    ledger = json.loads((fdir / "defects.json").read_text(encoding="utf-8"))
    for record in ledger["defects"]:
        record["tier"] = tier
    (fdir / "defects.json").write_text(json.dumps(ledger), encoding="utf-8")


def _arrange_inspect_escalated_full(root, fdir, teams):
    _clean_inspect(fdir, "FULL")
    _escalated(fdir, tier="LATENT")


def _arrange_inspect_escalated_delta(root, fdir, teams):
    _clean_inspect(fdir, "DELTA")
    _escalated(fdir, tier="LATENT")


def _arrange_inspect_escalated_filed(root, fdir, teams):
    _clean_inspect(fdir, "FULL")
    _escalated(fdir, tier="LIVE")


#: lead-stalls D-055 — THE CLASS PERSISTED ESCALATED WITH EVERY INSTANCE
#: CLOSED, which is the ordinary state one INSPECT after a GRIND fixes an
#: escalated class, and every phase a run can stand in with it: a clean FULL
#: F2, and past ASSAY. Nothing is open, so past ASSAY no accepted transition
#: advances the counter; with a record open the GRIND crossing does. At F2 the
#: re-open does either way (lead-stalls D-057).
def _arrange_inspect_escalated_closed(root, fdir, teams):
    _clean_inspect(fdir, "FULL")
    _escalated_fixture(fdir, open_instances=False)


def _arrange_assay_passed_escalated(root, fdir, teams):
    _assay(fdir, ["VERIFIED"], temper=True)
    _escalated_fixture(fdir, open_instances=False)


def _arrange_assay_passed_escalated_open(root, fdir, teams):
    _assay(fdir, ["VERIFIED"])
    _escalated(fdir, tier="LATENT")


def _arrange_temper_escalated(root, fdir, teams):
    _post_assay(fdir, "F5", temper=True, nyquist=False)
    _escalated_fixture(fdir, open_instances=False)


def _arrange_nyquist_escalated_open(root, fdir, teams):
    _post_assay(fdir, "F5.5", temper=True, nyquist=True)
    _escalated(fdir, tier="LATENT")


def _assay(
    fdir: Path, verdicts: list[str], *, temper: bool = False,
    nyquist: bool = False, filed: bool = False,
) -> None:
    """F4 with the verdicts ASSAY recorded, one per requirement FR-1..FR-n."""
    ids = [f"FR-{n}" for n in range(1, max(len(verdicts), 1) + 1)]
    _write_spec(fdir, ids)
    _write_state(fdir, phase="F4", cycle=1, temper=temper, nyquist=nyquist)
    _write_verdicts(fdir, [
        {"id": rid, "verdict": verdict, "evidence": "read at HEAD"}
        for rid, verdict in zip(ids, verdicts)
    ])
    _defect_ledger(fdir, [_tiered("D-001", "LIVE")] if filed else [])


def _arrange_assay_empty(root, fdir, teams):
    _assay(fdir, [])


def _arrange_assay_failed_filed(root, fdir, teams):
    _assay(fdir, ["VERIFIED", "PARTIAL"], filed=True)


def _arrange_assay_failed_unfiled(root, fdir, teams):
    # PROVE's D-053 state: a verdict recorded, and no defect filed for it.
    _assay(fdir, ["VERIFIED", "PARTIAL"])


#: lead-stalls D-054 — PROVE's s1 and s1b states, and the HARDENING variant:
#: a non-VERIFIED verdict that no open BLOCKING defect names, beside an open
#: record the ledger-wide count used to read as "filed".
def _arrange_assay_failed_unrelated_latent(root, fdir, teams):
    _assay(fdir, ["VERIFIED", "PARTIAL"])
    _defect_ledger(fdir, [_tiered(
        "D-001", "LATENT", spec_ref="FR-1",
        reproduction_attempted="drove every caller; none reach the branch",
    )])


def _arrange_assay_failed_unrelated_hardening(root, fdir, teams):
    _assay(fdir, ["VERIFIED", "PARTIAL"])
    record = _tiered(
        "D-001", "HARDENING",
        reproduction_attempted="drove an empty payload; the door answered 500",
    )
    record.pop("spec_ref")
    _defect_ledger(fdir, [record])


def _arrange_assay_failed_partly_filed(root, fdir, teams):
    _assay(fdir, ["VERIFIED", "PARTIAL", "WRONG"])
    _defect_ledger(fdir, [_tiered(
        "D-002", "LIVE", spec_ref="FR-2", source="assay", type="PARTIAL",
    )])


def _arrange_assay_passed(root, fdir, teams):
    _assay(fdir, ["VERIFIED"])


def _arrange_assay_passed_temper(root, fdir, teams):
    _assay(fdir, ["VERIFIED"], temper=True)


def _arrange_assay_passed_nyquist(root, fdir, teams):
    _assay(fdir, ["VERIFIED"], nyquist=True)


def _arrange_assay_passed_both(root, fdir, teams):
    _assay(fdir, ["VERIFIED"], temper=True, nyquist=True)


#: lead-stalls D-058 — PROVE's drive_s9 state: every requirement VERIFIED
#: and a blocking defect open that no non-VERIFIED verdict carries, which is
#: what an assayer's RESEARCH_DEVIATION filing or an adjudicated DEFECT
#: observation leaves beside a passing ASSAY. No row reached it, so the
#: F4 arm's crossings over an open blocking defect were never judged. One row
#: per crossing the flags pick, and the untiered bucket, which blocks too.
def _assay_passed_filed(fdir: Path, *, temper: bool, nyquist: bool,
                        tier: str | None = "LIVE") -> None:
    _assay(fdir, ["VERIFIED"], temper=temper, nyquist=nyquist)
    _defect_ledger(fdir, [_tiered(
        "D-001", tier, source="assay", type="RESEARCH_DEVIATION",
    )])


def _arrange_assay_passed_filed(root, fdir, teams):
    _assay_passed_filed(fdir, temper=False, nyquist=False)


def _arrange_assay_passed_temper_filed(root, fdir, teams):
    _assay_passed_filed(fdir, temper=True, nyquist=False)


def _arrange_assay_passed_nyquist_untiered(root, fdir, teams):
    _assay_passed_filed(fdir, temper=False, nyquist=True, tier=None)


#: lead-stalls D-060 — PROVE's drive_coverage state: every RECORDED verdict
#: VERIFIED, nothing open, and fewer verdicts than the spec declares (an
#: assayer group that recorded part of its ids). `_assay` writes one spec id
#: per verdict, so no row held a short ledger and the DONE gate's coverage
#: refusal was never judged. One row per flag combination, the on-route shape
#: (a PROVE record that is not clean, so the auto-pass leaves the ledger
#: short), and its control (a clean PROVE, whose auto-pass fills it).
def _assay_short(fdir: Path, *, temper: bool = False, nyquist: bool = False,
                 prove_findings: int | None = None) -> None:
    _write_spec(fdir, ["FR-1", "FR-2"])
    _write_state(fdir, phase="F4", cycle=1, temper=temper, nyquist=nyquist)
    _write_verdicts(fdir, [
        {"id": "FR-1", "verdict": "VERIFIED", "evidence": "read at HEAD"},
    ])
    _defect_ledger(fdir, [])
    if prove_findings is not None:
        _write_prove(fdir, items_checked=2, items_total=2,
                     findings=prove_findings)


def _arrange_assay_short(root, fdir, teams):
    _assay_short(fdir)


def _arrange_assay_short_temper(root, fdir, teams):
    _assay_short(fdir, temper=True)


def _arrange_assay_short_nyquist(root, fdir, teams):
    _assay_short(fdir, nyquist=True)


def _arrange_assay_short_both(root, fdir, teams):
    _assay_short(fdir, temper=True, nyquist=True)


def _arrange_assay_short_prove_filed(root, fdir, teams):
    _assay_short(fdir, temper=True, prove_findings=1)


def _arrange_assay_short_prove_clean(root, fdir, teams):
    _assay_short(fdir, prove_findings=0)


#: lead-stalls D-061 — PROVE's drive_cap states: a GRIND crossing served at
#: the --max-cycles cap, where `grind_start` seals the run HALTED. No row was
#: at the cap, so nothing judged the dispatch served after the sealing step.
#: F2, F4, F5 and F5.5, each with one open LIVE defect.
def _capped(fdir: Path) -> None:
    """The run's cap set to the cycle it stands in: the next GRIND door seals."""
    state = json.loads((fdir / "state.json").read_text(encoding="utf-8"))
    state["max_cycles"] = state["cycle"]
    (fdir / "state.json").write_text(json.dumps(state), encoding="utf-8")


def _arrange_inspect_filed_capped(root, fdir, teams):
    _clean_inspect(fdir, "FULL", filed=True)
    _capped(fdir)


def _arrange_assay_passed_filed_capped(root, fdir, teams):
    _assay_passed_filed(fdir, temper=False, nyquist=False)
    _capped(fdir)


def _arrange_temper_filed_capped(root, fdir, teams):
    _post_assay(fdir, "F5", temper=True, nyquist=False, passed="done", filed=True)
    _capped(fdir)


def _arrange_nyquist_filed_capped(root, fdir, teams):
    _post_assay(fdir, "F5.5", temper=False, nyquist=True, filed=True)
    _capped(fdir)


def _arrange_halted_reported(root, fdir, teams):
    _halted_run(fdir)
    (fdir / "REPORT.md").write_text("# report\n", encoding="utf-8")


def _arrange_halted_unreported(root, fdir, teams):
    _halted_run(fdir)


def _arrange_done(root, fdir, teams):
    _write_state(fdir, phase="F6", cycle=1)


def _arrange_unknown_phase(root, fdir, teams):
    _write_state(fdir, phase="F9", cycle=1)


#: (state, the transition(s) it is the server-side half of, arrange, the
#: action OWED, the branch OWED or None for an unbranched action). The owed
#: columns are derived by hand from the run state, never from the router — a
#: table that asked the function under audit would agree with it by
#: construction (lead-stalls D-004).
_ROUTER_STATES = (
    ("cast-live", "ST-001", _arrange_cast_live, "build_castings", "live"),
    ("cast-live-owed", "ST-002", _arrange_cast_live_owed, "build_castings", "live"),
    ("cast-not-spawned", "ST-003", _arrange_cast_not_spawned, "build_castings", "undispatched"),
    ("cast-unaccepted", "ST-004", _arrange_cast_unaccepted, "build_castings", "unaccepted"),
    ("cast-refused", "ST-004", _arrange_cast_refused, "build_castings", "refused"),
    ("cast-refused-team-down", "ST-004", _arrange_cast_refused_team_down, "build_castings", "redispatch"),
    ("cast-refused-foreign-pane", "ST-004", _arrange_cast_refused_foreign_pane, "build_castings", "redispatch"),
    ("cast-refused-other-team", "ST-004", _arrange_cast_refused_other_team, "build_castings", "redispatch"),
    ("cast-unmeasured-team-up", "ST-003", _arrange_cast_unmeasured_team_up, "build_castings", "undispatched"),
    ("cast-refusal-answered", "ST-004", _arrange_cast_refusal_answered, "build_castings", "unaccepted"),
    ("cast-boundary-team-up", "ST-003", _arrange_cast_boundary_team_up, "cleanup_teams", None),
    ("cast-boundary", "ST-003", _arrange_cast_boundary, "build_castings", "undispatched"),
    ("cast-built-team-up", "ST-003", _arrange_cast_built_team_up, "cleanup_teams", None),
    ("cast-built", "ST-003", _arrange_cast_built, "build_castings", "idle"),
    ("cast-dispatched-fresh-seed", "ST-001", _arrange_cast_dispatched_fresh_seed, "build_castings", "live"),
    ("cast-redispatched-fresh-seed", "ST-004", _arrange_cast_redispatched_fresh_seed, "build_castings", "live"),
    ("grind-live", "ST-001", _arrange_grind_live, "fix_defects", "live"),
    ("grind-live-all-fixed", "ST-001", _arrange_grind_live_all_fixed, "fix_defects", "live"),
    ("grind-finished-team-up", "ST-003", _arrange_grind_finished_team_up, "cleanup_teams", None),
    ("grind-idle", "ST-003", _arrange_grind_idle, "fix_defects", "idle"),
    ("grind-dispatched-fresh-seed", "ST-001", _arrange_grind_dispatched_fresh_seed, "fix_defects", "live"),
    ("grind-all-fixed", "ST-003", _arrange_grind_all_fixed, "transition_to_inspect", None),
    ("inspect-live", "ST-001", _arrange_inspect_live, "run_streams", "live"),
    ("inspect-idle", "ST-003", _arrange_inspect_idle, "run_streams", "idle"),
    ("inspect-stale-team", "ST-003", _arrange_inspect_stale_team, "cleanup_teams", None),
    # lead-stalls D-050 — the post-ASSAY phases, which no row reached, so the
    # sweep never asked what Foundry-Next answers after `run_temper`'s list.
    ("temper-owed", "GI-008", _arrange_temper_owed, "run_temper", None),
    ("temper-other-gate", "GI-008", _arrange_temper_other_gate, "run_temper", None),
    ("temper-gate-passed", "GI-008", _arrange_temper_gate_passed, "transition_to_done", None),
    ("temper-nyquist-passed", "GI-008", _arrange_temper_nyquist_passed, "transition_to_nyquist", None),
    ("temper-filed", "GI-008", _arrange_temper_filed, "transition_to_grind", None),
    ("nyquist-owed", "GI-008", _arrange_nyquist_owed, "run_nyquist", None),
    ("nyquist-gate-passed", "GI-008", _arrange_nyquist_gate_passed, "transition_to_done", None),
    ("nyquist-filed", "GI-008", _arrange_nyquist_filed, "transition_to_grind", None),
    # lead-stalls D-051..D-053 — the rest of the population, so every action
    # the router returns is served here at least once and its CONTEXT judged.
    ("no-run", "GI-008", _arrange_no_run, "init", None),
    ("decompose-empty", "GI-008", _arrange_decompose_empty, "add_castings", "idle"),
    ("decompose-done", "GI-008", _arrange_decompose_done, "transition_to_cast", None),
    # lead-stalls D-056 — the writers are agents, and while one is writing the
    # decomposition is not finished, whatever the manifest already lists.
    ("decompose-writing", "ST-001", _arrange_decompose_writing, "add_castings", "live"),
    ("decompose-first-writer-done", "ST-002", _arrange_decompose_first_writer_done, "add_castings", "live"),
    ("decompose-writers-done", "ST-003", _arrange_decompose_writers_done, "transition_to_cast", None),
    ("cast-complete", "GI-008", _arrange_cast_complete, "transition_to_inspect", None),
    ("inspect-width-unrecorded", "GI-008", _arrange_inspect_width_unrecorded, "record_inspect_width", None),
    ("inspect-filed", "GI-008", _arrange_inspect_filed, "transition_to_grind", None),
    ("inspect-clean-full", "GI-008", _arrange_inspect_clean_full, "transition_to_assay", None),
    ("inspect-clean-delta", "GI-008", _arrange_inspect_clean_delta, "widen_inspect", None),
    # lead-stalls D-055 — a held class is not sent on to ASSAY: with a record
    # open the GRIND crossing closes a clean cycle, and with none the run is
    # held, which the header says rather than serving a list that is refused.
    # lead-stalls D-057 — at F2 the re-open is accepted from a clean FULL
    # cycle too, so every held class there is served it, record open or not;
    # the GRIND crossing and the held answer are past ASSAY only.
    ("inspect-escalated-full", "GI-008", _arrange_inspect_escalated_full, "widen_inspect", None),
    ("inspect-escalated-delta", "GI-008", _arrange_inspect_escalated_delta, "widen_inspect", None),
    ("inspect-escalated-filed", "GI-008", _arrange_inspect_escalated_filed, "transition_to_grind", None),
    ("inspect-escalated-closed", "GI-008", _arrange_inspect_escalated_closed, "widen_inspect", None),
    ("assay-passed-escalated", "GI-008", _arrange_assay_passed_escalated, "escalation_held", None),
    ("assay-passed-escalated-open", "GI-008", _arrange_assay_passed_escalated_open, "transition_to_grind", None),
    ("temper-escalated", "GI-008", _arrange_temper_escalated, "escalation_held", None),
    ("nyquist-escalated-open", "GI-008", _arrange_nyquist_escalated_open, "transition_to_grind", None),
    ("assay-empty", "GI-008", _arrange_assay_empty, "run_assay", None),
    ("assay-failed-filed", "GI-008", _arrange_assay_failed_filed, "assay_failed_loop_back", None),
    ("assay-failed-unfiled", "GI-008", _arrange_assay_failed_unfiled, "assay_failed_loop_back", None),
    ("assay-failed-unrelated-latent", "GI-008", _arrange_assay_failed_unrelated_latent, "assay_failed_loop_back", None),
    ("assay-failed-unrelated-hardening", "GI-008", _arrange_assay_failed_unrelated_hardening, "assay_failed_loop_back", None),
    ("assay-failed-partly-filed", "GI-008", _arrange_assay_failed_partly_filed, "assay_failed_loop_back", None),
    ("assay-passed", "GI-008", _arrange_assay_passed, "transition_to_done", None),
    ("assay-passed-temper", "GI-008", _arrange_assay_passed_temper, "transition_to_temper", None),
    ("assay-passed-nyquist", "GI-008", _arrange_assay_passed_nyquist, "transition_to_nyquist", None),
    ("assay-passed-both", "GI-008", _arrange_assay_passed_both, "transition_to_temper", None),
    # lead-stalls D-058 — a blocking defect open beside a passing ASSAY is the
    # GRIND crossing, whichever crossing the flags would otherwise pick.
    ("assay-passed-filed", "GI-008", _arrange_assay_passed_filed, "transition_to_grind", None),
    ("assay-passed-temper-filed", "GI-008", _arrange_assay_passed_temper_filed, "transition_to_grind", None),
    ("assay-passed-nyquist-untiered", "GI-008", _arrange_assay_passed_nyquist_untiered, "transition_to_grind", None),
    # lead-stalls D-060 — a short ledger is an ASSAY that has not finished,
    # whichever crossing the flags would otherwise pick; a clean PROVE's
    # auto-pass fills it, and then the flags' crossing is owed.
    ("assay-short", "GI-008", _arrange_assay_short, "run_assay", None),
    ("assay-short-temper", "GI-008", _arrange_assay_short_temper, "run_assay", None),
    ("assay-short-nyquist", "GI-008", _arrange_assay_short_nyquist, "run_assay", None),
    ("assay-short-both", "GI-008", _arrange_assay_short_both, "run_assay", None),
    ("assay-short-prove-filed", "GI-008", _arrange_assay_short_prove_filed, "run_assay", None),
    ("assay-short-prove-clean", "GI-008", _arrange_assay_short_prove_clean, "transition_to_done", None),
    # lead-stalls D-061 — the GRIND crossing at the cap, from every phase that
    # serves it; the structure audit's SERVED_PAST_THE_SEAL judges its list.
    ("inspect-filed-capped", "GI-008", _arrange_inspect_filed_capped, "transition_to_grind", None),
    ("assay-passed-filed-capped", "GI-008", _arrange_assay_passed_filed_capped, "transition_to_grind", None),
    ("temper-filed-capped", "GI-008", _arrange_temper_filed_capped, "transition_to_grind", None),
    ("nyquist-filed-capped", "GI-008", _arrange_nyquist_filed_capped, "transition_to_grind", None),
    ("halted-reported", "GI-008", _arrange_halted_reported, "halted", None),
    ("halted-unreported", "GI-008", _arrange_halted_unreported, "halted", None),
    ("done", "GI-008", _arrange_done, "done", None),
    ("unknown-phase", "GI-008", _arrange_unknown_phase, "unknown", None),
)

#: The two clocks every state is driven at: the lead's last Foundry-Next just
#: now, and ten minutes ago — past `STALL_NOTICE_SECONDS`, where the notice
#: that D-019 caught contradicting the header is printed.
_CLOCKS = (("fresh", None), ("stale", 600))


#: lead-stalls D-005 / D-024 / D-029 — what the CONTEXT of a branched action
#: may not say: an ORDER. A sequence here is a second imperative beside the one
#: chosen from the roster.
#:
#: This was a word list — `("Spawn ", "spawn every", "Foundry-Phase(",
#: "TeamDelete", "Foundry-Team-Down")` — and so it saw only the orders someone
#: had already written down. Re-inserting the acdc00b sentence "SIGHT runs in
#: MAIN THREAD ... — navigate to URL, snapshot every page, exercise all
#: elements, check console." or rewording "every reading stream is bound to
#: pin its findings" back to "tell every reading stream to pin its findings"
#: left the whole suite green (D-029), which is D-023's word-list failure one
#: detector over.
#:
#: So the check is now the STRUCTURE of an English order: a clause with no
#: subject, which opens on an open-class word the way "navigate to URL",
#: "tell every reading stream" and "Spawn agents" do, or on a call name the way
#: a header step does ("Then TeamDelete + Foundry-Team-Down"). What CAN open a
#: declarative clause is a CLOSED grammatical class (articles, determiners,
#: pronouns, prepositions, conjunctions, negation), listed below; that list is
#: the grammar's, not a list of moves, so a new verb is caught the day it is
#: written. A prohibition ("never record on an AGENT's behalf") opens on
#: negation and names no move, so it stays legal — that standing rule is
#: fallout D-165's. Identifiers, numbers and ALL-CAPS phase names open on no
#: word at all. A call named INSIDE a statement ("records its OWN run with
#: Foundry-Stream") is a fact about a door, not a step, and is not an order.
_CLAUSE_OPENERS = frozenset("""
    a an the this that these those each every all any no some both either
    neither its their your our his her my it they you we he she there here
    nothing nobody none one which what who whose
    after before on in at for from to with without by of under over between
    during until since per as unlike like into onto than
    so and then but or yet because while where when whenever if unless once
    whereas though although
    never not do don't also just now still already only even
""".split())
_STRIPPED_OPENERS = frozenset(("so", "and", "then", "but", "or", "yet",
                               "also", "just", "now", "still", "already",
                               "even"))
#: Where a new clause starts: a sentence end, a dash, a semicolon, and a comma
#: before `so` / `then` / `but` / `yet` — the coordinators that open an
#: independent clause and never close a list, unlike `and` / `or` ("trace,
#: prove, and test" is one noun phrase).
_SENTENCE_BREAK = re.compile(
    r"(?<=[.!?])\s+(?=[A-Z`'\"(0-9])|\s+—\s+|;\s*"
    r"|,\s+(?=(?:so|then|but|yet)\s)"
)
_PARENTHESISED = re.compile(r"\([^()]*\)")
_OPENING_WORD = re.compile(r"[A-Za-z][A-Za-z'-]*")


def _order_openers(context: str) -> list[str]:
    """Every clause of ``context`` that opens as an order, quoted from its
    first words (lead-stalls D-029). Empty when the text only states facts."""
    text = context
    while _PARENTHESISED.search(text):
        text = _PARENTHESISED.sub("", text)
    orders: list[str] = []
    for clause in _SENTENCE_BREAK.split(text):
        words = clause.strip().split()
        while words and words[0].lower().strip(",") in _STRIPPED_OPENERS:
            words = words[1:]
        if not words:
            continue
        first = words[0]
        if _NAMES_A_CALL.match(first):
            orders.append(" ".join(words[:4]))
            continue
        word = _OPENING_WORD.fullmatch(first.rstrip(",:"))
        if not word:
            continue      # an identifier, a number, a quoted or backticked term
        spelled = word.group(0)
        if spelled.isupper() and len(spelled) > 1:
            continue      # a phase or stream name: CAST, GRIND, SIGHT, TEST
        lowered = spelled.lower()
        if lowered in _CLAUSE_OPENERS:
            continue
        if len(lowered) > 4 and lowered.endswith(("ed", "ing")) and not lowered.endswith("eed"):
            continue      # a participle: "Required this cycle", "Waiting on"
        orders.append(" ".join(words[:4]))
    return orders


#: lead-stalls D-051..D-053 — the one verbless HEADING a CONTEXT carries: the
#: convergence ST-010 notice's distances, which
#: `escalation._escalation_exit_distances` renders as "Distance to each exit —
#: <class>: ..." (a module outside this casting's files, and outside
#: lead-stalls NFR-001's source cap). The order detector reads a clause by its
#: first word, and a heading's first word is a noun, so the heading is removed
#: before the judgement; the escalated router states pin that the renderer
#: still opens with exactly this text.
_DISTANCES_HEADING = "Distance to each exit —"


def _context_orders(context: str) -> list[str]:
    """What a branched action's CONTEXT orders, with the deferral sentence the
    check requires removed first (it states a fact about the header)."""
    return _order_openers(context.replace(_BRANCHED_ACTION_CONTEXT, " "))


def _context_of(instructions: str) -> str:
    """The `CONTEXT:` half of an assembled payload, or ``""``."""
    return instructions.split("\nCONTEXT:", 1)[1] if "\nCONTEXT:" in instructions else ""


def _split_payload(instructions: str) -> tuple[str, str]:
    """``(block, header)``: everything the lead is told between the marker and
    CONTEXT, and the imperative header alone (the notice lines removed)."""
    tail = instructions.split(_NEXT_ACTION_MARKER, 1)[-1]
    block = tail.split("\nCONTEXT:", 1)[0].strip("\n")
    lines = block.split("\n")
    while lines and lines[0].startswith(_NOTICE_OPENERS):
        lines.pop(0)
    return block, "\n".join(lines).strip("\n")


def drive_router(arrange, clock_seconds: int | None) -> dict:
    """Build one run state in a scratch directory and call Foundry-Next on it."""
    with tempfile.TemporaryDirectory() as tmp, _router_run(Path(tmp)) as (
        root, fdir, teams,
    ):
        arrange(root, fdir, teams)
        if clock_seconds is not None:
            _stale_stall_clock(fdir, clock_seconds)
        capped = _at_cap(fdir)
        nxt = foundry_next_action(root)
        cycle = foundry_state.current_cycle(fdir)
    block, header = _split_payload(nxt.get("instructions", ""))
    liveness = nxt.get("agent_liveness")
    action = nxt.get("action", "")
    branched = bool(_parse_branches(_ACTION_IMPERATIVES.get(action, "")))
    details = nxt.get("details") or {}
    return {
        "action": action,
        "branch": (
            _emitted_branch(action, header, _ROUTE_RUN, liveness, cycle, details)
            if branched else None
        ),
        # lead-stalls GI-008 / OT-013 — the structure the header was rendered
        # from, and the rules printed above it, for the structure audit.
        "next_calls": nxt.get("next_calls"),
        "rules": _rules_of(nxt.get("instructions", "")),
        "details": details,
        "branched": branched,
        "block": block,
        "header": header,
        "context": _context_of(nxt.get("instructions", "")),
        "agent_liveness": liveness,
        "waiting_on_agents": nxt.get("waiting_on_agents"),
        "capped": capped,
    }


def _at_cap(fdir: Path) -> bool:
    """lead-stalls D-061 — the next GRIND door seals the run HALTED: a cap is
    set and the cycle has reached it. Read off the arranged state.json by
    hand, never off the router or its outlook, which is what is judged."""
    try:
        state = json.loads((fdir / "state.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    cap, cycle = state.get("max_cycles"), state.get("cycle", 0)
    return (
        isinstance(cap, int) and cap > 0
        and isinstance(cycle, int) and cycle >= cap
    )


def _router_drives() -> list[tuple[str, str, str, str, object, dict]]:
    """(site, state, transitions, owed action, owed branch, drive) per cell."""
    return [
        (f"payload:{state}[{clock}]", state, transitions, action, branch,
         drive_router(arrange, seconds))
        for state, transitions, arrange, action, branch in _ROUTER_STATES
        for clock, seconds in _CLOCKS
    ]


# --------------------------------------------------------------------------- #
# lead-stalls GI-008 / OT-002 / OT-013 / FR-008 — THE STRUCTURE AUDIT
# (D-038..D-043, on the user's ruling of 2026-09-17)
# --------------------------------------------------------------------------- #
#
# The sweep below used to discharge lead-stalls FR-008 on a prose detector, and its zero was
# false five times over a space the defect could not appear in (D-004, D-023,
# D-032, D-034, D-041). The lead's next calls are now a `_Step` list the router
# emits and publishes as `next_calls`, and THIS is what the zero is computed
# over: at every emission site and every router state, the list the header was
# rendered from is judged as data.
#
#   STEP_LIST         a mapped action served no step list at all
#   RENDERING         the header's numbered lines are not exactly the list
#   NONE              an empty list without "YOUR NEXT CALL: NONE", or the reverse
#   UNKNOWN_TOOL      a step names a tool outside `_LEAD_CALLS`, or a Foundry door
#                     the MCP server does not register
#   YIELD_NOT_LAST    a step follows END YOUR TURN
#   CALL_IN_PROSE     a lead call written in call syntax outside the step lines,
#                     which is the only way prose could add or choose a call
#   RULE_*            the standing spawn rule printed on the payload lacks a
#                     phase's order, ends one anywhere but progress_protocol, or
#                     lacks the one-move sentence (D-038 / D-039)
#   DOOR_WITHOUT_SPAWN / SPAWN_WITHOUT_DOOR
#                     a spawn door and its teammate Agent call are not adjacent
#   SPAWN_ORDER       a teammate spawn's block order is not the order the rule
#                     printed ABOVE it names for its phase (D-039). Found by
#                     tool name over whatever is served, and compared with the
#                     order parsed OUT OF THE RULE TEXT, so a sixth dispatch
#                     step is judged the day it is written.
#   SPAWN_NOT_ONE_MOVE / SPAWN_NOT_YIELDED
#                     a spawn step without the one-move sentence, or a list that
#                     spawns a teammate and does not end in the yield (D-038)
#   RULE_WITHOUT_LIST_MOVE / *_ORDERS_NEXT_MID_LIST / NEXT_MID_LIST
#                     the rules, the header prose or a step orders a
#                     Foundry-Next before the served list's last step, or the
#                     rules lack the one-move sentence (D-044 / D-045)
#   ANSWER_BREAKS_LIST
#                     a step's door, driven for real, answers with an order to
#                     call Foundry-Next while the list's next step is not one
#   STREAM_*          an unrecorded roster stream with no call, the wrong agent,
#                     or no `agent_configs` entry; or a call for a stream the
#                     roster does not owe (D-040)
#   SERVED_PAST_THE_SEAL
#                     at the --max-cycles cap, a step follows the GRIND door
#                     that seals the run HALTED (D-061)
#
# The prose detector `_HANDS_OVER_THE_CONDITION` still runs beside these as a
# BACKSTOP and is not widened again (D-043): a sentence that names no call
# cannot add a step or reorder one, which the D-043 test below pins.

#: lead-stalls D-040 — the agent each stream is OWED, written by hand from
#: `agents/*.md` and commands/start.md's F2 roster rather than read off the
#: table under audit, which would agree with itself by construction.
_OWED_STREAM_AGENT = {
    "trace": "foundry:tracer",
    "flow_trace": "foundry:flow-tracer",
    "prove": "foundry:assayer",
    "research_audit": "foundry:research-auditor",
    "coverage_diff": "foundry:coverage-diff",
    "test01": "foundry:spec-test-deriver",
    "test": "general-purpose",
    "probe": "general-purpose",
}
_STEP_LINE = re.compile(r"^  \((\d+)\) (.*)$", re.MULTILINE)
_RULE_ORDER = re.compile(r"\b(CAST|GRIND): its prompt is .*?Order: ([^.]*)\.")
_STEP_ORDER = re.compile(r"Order: ([^.]*)\.")
_ARG_PHASE = re.compile(r"phase='([a-z_]+)'")
_ARG_SUBAGENT = re.compile(r"subagent_type='([^']+)'")
#: A call written as one: a Foundry door or a harness tool, then "(".
_CALL_SYNTAX = re.compile(
    r"\b(Foundry-[A-Z][A-Za-z-]*|Agent|Skill|Bash|SendMessage|TeamCreate"
    r"|TeamDelete|TaskOutput)\("
)
_TEAMMATE = "foundry:teammate"


def _mcp_tool_names() -> frozenset[str]:
    """Every tool the MCP server registers, asked of the server itself."""
    from foundry_mcp import server

    return frozenset(tool.name for tool in asyncio.run(server.list_tools()))


def _spawn_rule_line(rules: str) -> str:
    """The standing rule on passing a spawn prompt, or ``""``."""
    return next(
        (line for line in rules.splitlines()
         if "prompt returned by Foundry-Spawn-Teammate" in line),
        "",
    )


def _order_of(text: str) -> tuple[str, ...]:
    return tuple(label.strip() for label in text.split("→"))


def _as_step(call: object) -> _Step:
    """A `next_calls` record, or a `_Step`, as a `_Step`."""
    if isinstance(call, _Step):
        return call
    row = call if isinstance(call, dict) else {}
    return _Step(
        str(row.get("tool")), row.get("args"), row.get("each") or "",
        tuple(row.get("prompt_blocks") or ()), row.get("note") or "",
    )


#: lead-stalls GI-008 / FR-015 / CT-003 / US-003 (D-044, D-045) — THE ONE-MOVE
#: WALK. The rules block said "Call Foundry-Next after each step and follow
#: it", and a Foundry-Next answers from the run state rather than from how far
#: through a list the lead has got: driven, the CAST wave-complete and GRIND
#: dispatch lists came back from step (1) after their Team-Down (whose own
#: answer said "Call Foundry-Next now."), the re-dispatch lost its SendMessage
#: and the GRIND dispatch was torn down after its Team-Up. No detector walked a
#: list step by step. This one does, over every site and every payload: an
#: order to call Foundry-Next is legal only where it places the call after a
#: list's LAST step, or as the last step itself. A prohibition ("do NOT call
#: Foundry-Next in a loop") orders nothing.
_CALLS_FOUNDRY_NEXT = re.compile(
    r"(?<!not )(?<!never )\bcall Foundry-Next\b", re.IGNORECASE
)
_AFTER_THE_LAST_STEP = "last step"


def _orders_foundry_next_mid_list(text: str) -> list[str]:
    """Each sentence of ``text`` that orders a Foundry-Next without placing it
    after a served list's last step."""
    return [
        sentence.strip() for sentence in _EMITTED_SENTENCE.split(text)
        if _CALLS_FOUNDRY_NEXT.search(sentence)
        and _AFTER_THE_LAST_STEP not in sentence
    ]


def _step_answers() -> dict[str, object]:
    """The `next_call` a list step's door REALLY answers with, per tool.

    Read off the real handlers in a scratch run rather than typed here, so a
    door whose answer reverts to "Call Foundry-Next now." is judged the day it
    does: Team-Down succeeding on a torn-down team, and Accept-Casting refusing
    a casting (its answer is the same on every path, lead-stalls FR-011).
    """
    with tempfile.TemporaryDirectory() as tmp, _router_run(Path(tmp)) as (
        root, fdir, teams,
    ):
        _cast(fdir, {1: ["1"]})
        _worked(fdir, "1", done=True)
        _register(root, teams, _CAST_TEAM)
        (teams / _CAST_TEAM).rmdir()
        down = foundry_unregister_team(_CAST_TEAM, project_root=root)
        refused = _accept(root, fdir, "1", prompt_hash="sha256:" + "0" * 16)
    assert down.get("ok") is True, down
    assert refused.get("next_call"), refused
    return {
        "Foundry-Team-Down": down.get("next_call"),
        "Foundry-Accept-Casting": refused.get("next_call"),
    }


#: lead-stalls D-061 — the two GRIND doors, each bounded by the --max-cycles
#: cap (`grind_start`, and `assay_fail` through the ASSAY-rejection door).
_SEALING_AT_CAP = ("phase='grind_start'", "phase='assay_fail'")


def judge_next_calls(
    site: str,
    action: str,
    calls: object,
    header: str,
    rules: str,
    details: dict,
    configs: dict | None,
    tools: frozenset[str],
    answers: dict[str, object] | None = None,
    capped: bool = False,
) -> list[str]:
    """The structure findings for one served step list (see the table above).
    ``answers`` maps a step's tool to the `next_call` its door really answers
    with (`_step_answers`). ``capped`` is `_at_cap` of the run state served."""
    if action not in _IMPERATIVES:
        return []
    if not isinstance(calls, (list, tuple)):
        return [f"{site}: STEP_LIST — the lead's header was rendered from no step list"]
    steps = [_as_step(call) for call in calls]
    findings: list[str] = []

    served = _STEP_LINE.findall(header)
    if [int(n) for n, _ in served] != list(range(1, len(served) + 1)) or [
        line for _n, line in served
    ] != [_render_call(step) for step in steps]:
        findings.append(
            f"{site}: RENDERING — the header's call lines are not the "
            f"{len(steps)}-step list it was served with"
        )
    if bool(steps) == bool(_NAMES_NO_CALL.search(header)):
        findings.append(
            f"{site}: NONE — {len(steps)} step(s) beside "
            f"{'a' if _NAMES_NO_CALL.search(header) else 'no'} NONE"
        )
    for number, step in enumerate(steps, 1):
        if step.tool not in _LEAD_CALLS or (
            step.tool.startswith("Foundry-") and step.tool not in tools
        ):
            findings.append(
                f"{site}: UNKNOWN_TOOL — step ({number}) names {step.tool!r}"
            )
        if step.tool == _END_TURN and number != len(steps):
            findings.append(
                f"{site}: YIELD_NOT_LAST — step ({number}) yields and "
                f"{len(steps) - number} step(s) follow it"
            )
    for call in sorted(set(_CALL_SYNTAX.findall(_STEP_LINE.sub("", header)))):
        findings.append(
            f"{site}: CALL_IN_PROSE — {call}( is written outside the step list"
        )

    # D-044 / D-045 — the served list is ONE move: nothing it names, nothing
    # the rules above it say, and nothing a step's own door answers may put a
    # Foundry-Next before its last step.
    if steps:
        # The sentence itself, which every rules block carrying a list states;
        # the spawn clause `_A_SERVED_LIST_IS_ONE_MOVE` adds to the standing
        # one is judged below, on the lists that spawn.
        if SERVED_LIST_IS_ONE_MOVE not in rules:
            findings.append(
                f"{site}: RULE_WITHOUT_LIST_MOVE — the rules above the header "
                f"do not say the served list is one move"
            )
        for sentence in _orders_foundry_next_mid_list(rules):
            findings.append(
                f"{site}: RULE_ORDERS_NEXT_MID_LIST — {sentence[:72]!r}"
            )
        for sentence in _orders_foundry_next_mid_list(_STEP_LINE.sub("", header)):
            findings.append(
                f"{site}: PROSE_ORDERS_NEXT_MID_LIST — {sentence[:72]!r}"
            )
    for number, step in enumerate(steps[:-1], 1):
        if step.tool == "Foundry-Next":
            findings.append(
                f"{site}: NEXT_MID_LIST — step ({number}) is a Foundry-Next "
                f"and {len(steps) - number} step(s) follow it"
            )
        for sentence in _orders_foundry_next_mid_list(_render_call(step)):
            findings.append(
                f"{site}: STEP_ORDERS_NEXT_MID_LIST — step ({number}) says "
                f"{sentence[:72]!r}"
            )
        answer = (answers or {}).get(step.tool)
        if (
            isinstance(answer, str)
            and _orders_foundry_next_mid_list(answer)
            and steps[number].tool != "Foundry-Next"
        ):
            findings.append(
                f"{site}: ANSWER_BREAKS_LIST — step ({number}) {step.tool} "
                f"answers {answer!r} and step ({number + 1}) is "
                f"{steps[number].tool}"
            )

    # D-061 — nothing past a transition that seals the run. At the cap both
    # GRIND doors seal HALTED, so a step after one registers a team or spawns
    # a teammate into a sealed run, and no served step ever tears it down.
    if capped:
        for number, step in enumerate(steps[:-1], 1):
            if step.tool == "Foundry-Phase" and step.args in _SEALING_AT_CAP:
                findings.append(
                    f"{site}: SERVED_PAST_THE_SEAL — step ({number}) "
                    f"Foundry-Phase({step.args}) seals the run HALTED at its "
                    f"cap and {len(steps) - number} step(s) follow it"
                )

    # D-038 / D-039 — every teammate spawn against the rule printed above it.
    rule = _spawn_rule_line(rules)
    orders = {
        phase.lower(): _order_of(order)
        for phase, order in _RULE_ORDER.findall(rule)
    }
    spawns = [
        (number, step) for number, step in enumerate(steps, 1)
        if step.tool == "Agent"
        and _ARG_SUBAGENT.search(step.args or "")
        and _ARG_SUBAGENT.search(step.args or "").group(1) == _TEAMMATE
    ]
    if spawns:
        if _SPAWN_IS_ONE_MOVE not in rule:
            findings.append(
                f"{site}: RULE_WITHOUT_ONE_MOVE — the spawn rule above the "
                f"header does not say a door and its Agent call are one move"
            )
        if not steps or steps[-1].tool != _END_TURN:
            findings.append(
                f"{site}: SPAWN_NOT_YIELDED — a teammate is spawned and the "
                f"list does not end in END YOUR TURN"
            )
    for number, step in enumerate(steps, 1):
        if step.tool in _SPAWN_DOORS and not (
            number < len(steps) and (number + 1, steps[number]) in spawns
        ):
            findings.append(
                f"{site}: DOOR_WITHOUT_SPAWN — step ({number}) {step.tool} is "
                f"not followed by its teammate Agent call"
            )
    lines = dict((int(n), line) for n, line in served)
    for number, step in spawns:
        door = steps[number - 2] if number > 1 else None
        if door is None or door.tool not in _SPAWN_DOORS:
            findings.append(
                f"{site}: SPAWN_WITHOUT_DOOR — step ({number}) spawns a "
                f"teammate with no spawn door straight before it"
            )
            continue
        phase_match = _ARG_PHASE.search(door.args or "")
        phase = phase_match.group(1) if phase_match else "?"
        owed = orders.get(phase)
        order_match = _STEP_ORDER.search(lines.get(number, ""))
        got = _order_of(order_match.group(1)) if order_match else ()
        if owed is None:
            findings.append(
                f"{site}: RULE_WITHOUT_PHASE — the spawn rule names no "
                f"{phase!r} order for step ({number})"
            )
        elif owed[-1:] != ("progress_protocol",):
            findings.append(
                f"{site}: RULE_NOT_LEDGER_LAST — the rule's {phase} order is "
                f"{' → '.join(owed)}"
            )
        elif got != owed:
            findings.append(
                f"{site}: SPAWN_ORDER — step ({number}) passes "
                f"{' → '.join(got) or 'no block order'} under a rule naming "
                f"{' → '.join(owed)}"
            )
        if _SPAWN_IS_ONE_MOVE not in lines.get(number, ""):
            findings.append(
                f"{site}: SPAWN_NOT_ONE_MOVE — step ({number}) does not say it "
                f"is one move with step ({number - 1})"
            )

    # D-040 — one call per unrecorded roster stream, and a config for each.
    if action == "run_streams" and steps:
        roster = details.get("missing_streams") or []
        named: dict[str, _Step] = {}
        for step in steps:
            if step.tool == "Agent" and step.each.startswith("for "):
                named[step.each[len("for "):]] = step
        sight = any(
            step.tool == "Skill" and "foundry:sight" in (step.args or "")
            for step in steps
        )
        for stream in roster:
            if stream == "sight":
                if not sight:
                    findings.append(f"{site}: STREAM_WITHOUT_CALL — sight")
                continue
            step = named.get(stream)
            if step is None:
                findings.append(f"{site}: STREAM_WITHOUT_CALL — {stream}")
            else:
                agent = _ARG_SUBAGENT.search(step.args or "")
                if not agent or agent.group(1) != _OWED_STREAM_AGENT.get(stream):
                    findings.append(
                        f"{site}: STREAM_WRONG_AGENT — {stream} is spawned as "
                        f"{agent.group(1) if agent else None!r}"
                    )
            if configs is not None and (configs.get(stream) or {}).get(
                "subagent_type"
            ) != _OWED_STREAM_AGENT.get(stream):
                findings.append(
                    f"{site}: STREAM_WITHOUT_CONFIG — agent_configs has no "
                    f"{_OWED_STREAM_AGENT.get(stream)} entry for {stream}"
                )
        for stream in sorted(set(named) - set(roster)):
            findings.append(
                f"{site}: STREAM_NOT_OWED — a call for {stream}, which the "
                f"roster does not list as unrecorded"
            )
    return findings


def audit_next_call_structure(drives=None) -> list[str]:
    """The structure audit over every emission site and every router payload."""
    tools = _mcp_tool_names()
    answers = _step_answers()
    findings: list[str] = []
    for action, site, text, _owed, liveness in _audit_sites():
        phase = site.split("@", 1)[1].split("[", 1)[0]
        details = _site_details(action)
        calls, header = _emitted_imperative(
            action, details, run_name=_AUDIT_RUN, phase=phase, liveness=liveness,
        )
        if header != text:
            findings.append(f"{site}: RENDERING — the site's header moved")
        findings += judge_next_calls(
            site, action, calls, header, _guidance._STANDING_CRITICAL_RULES,
            details, None, tools, answers,
        )
    for site, _state, _tr, _oa, _ob, d in (
        drives if drives is not None else _router_drives()
    ):
        configs = (
            d["details"].get("agent_configs") or {}
            if d["action"] == "run_streams" else None
        )
        findings += judge_next_calls(
            site, d["action"], d["next_calls"], d["header"], d["rules"],
            d["details"], configs, tools, answers, capped=d.get("capped", False),
        )
    return findings


def audit_assembled_payloads(drives=None) -> list[str]:
    """lead-stalls GI-008 / D-018 / D-019 — the sweep over what the lead READS.

    The header sweep judges `_format_imperative_header` alone, so it could not
    see the router answering `cleanup_teams` ahead of every branch, nor the
    stall notice printing END YOUR TURN above a header that names calls. This
    judges the assembled payload, per run state, at both clocks.
    """
    findings: list[str] = []
    for site, _state, _tr, owed_action, owed_branch, d in (
        drives if drives is not None else _router_drives()
    ):
        if (d["action"], d["branch"]) != (owed_action, owed_branch):
            findings.append(
                f"{site}: WRONG_ROUTE — the lead received "
                f"{d['action']!r}/{d['branch']!r} on a state owed "
                f"{owed_action!r}/{owed_branch!r}"
            )
        # The NOTICE may tell the lead to end its turn only above a header
        # that says so itself — the live branch, or the refused branch, whose
        # one message is followed by the same yield. Anything else is two
        # imperatives in one payload.
        # lead-stalls D-034 — the header as the router SERVED it, judged by the
        # same detector the table sweep uses. The run_streams idle branch
        # handed the lead "If an AGENT stream finished ... re-dispatch it, or
        # file it" and this sweep read its route and its notice, never its
        # sentences.
        hit = _HANDS_OVER_THE_CONDITION.search(d["header"])
        if hit:
            header = d["header"]
            quoted = " ".join(
                header[max(0, hit.start() - 40):hit.end() + 60].split()
            )
            findings.append(
                f"{site}: CONDITIONAL — the lead must evaluate {quoted!r}"
            )
        notice = d["block"][: len(d["block"]) - len(d["header"])]
        if "END YOUR TURN" in notice and "END YOUR TURN" not in d["header"]:
            findings.append(
                f"{site}: CONTRADICTION — the notice says END YOUR TURN above a "
                f"header that does not"
            )
        live = d["agent_liveness"] if isinstance(d["agent_liveness"], dict) else {}
        if live.get("waiting") and any(w in d["block"] for w in _TEARDOWN_WORDS):
            findings.append(
                f"{site}: TEARDOWN_OVER_RUNNING_AGENTS — agents are progressing "
                f"and the lead is told to tear down"
            )
        # lead-stalls D-024 — the CONTEXT half of the same payload. D-019
        # branched the `run_streams` header and left its CONTEXT saying "Spawn
        # agents using the agent_configs below" beneath "your INSPECT streams
        # are running ... spawning it again runs it twice". The D-005 rule was
        # asserted for two of the three branched actions and read by no sweep.
        if d["branched"]:
            moves = _context_orders(d["context"])
            if moves or _BRANCHED_ACTION_CONTEXT not in d["context"]:
                findings.append(
                    f"{site}: CONTEXT_SEQUENCE — a branched action's CONTEXT "
                    f"names {moves or 'no deferral to the header'}"
                )
        # lead-stalls GI-008 / FR-007 (D-051..D-053) — AND EVERY OTHER
        # ACTION'S CONTEXT. The rule above was asserted for the branched
        # actions alone, so twelve step-list arms kept a sequence of their own
        # beneath the header: `transition_to_assay` in the opposite order,
        # `transition_to_done` opening on a gate its report must precede, and
        # `assay_failed_loop_back` naming a filing no step made. Judged by the
        # same order detector, plus two it could not stand in for: a call
        # written in call syntax, and the prose conditional backstop.
        if not d["branched"]:
            moves = _order_openers(d["context"].replace(_DISTANCES_HEADING, " "))
            if moves:
                findings.append(
                    f"{site}: CONTEXT_SEQUENCE — the CONTEXT orders {moves}"
                )
        written = sorted(set(_CALL_SYNTAX.findall(d["context"])))
        if written:
            findings.append(
                f"{site}: CONTEXT_CALL — the CONTEXT writes out "
                f"{', '.join(name + '(' for name in written)}"
            )
        hit = _HANDS_OVER_THE_CONDITION.search(d["context"])
        if hit:
            context = d["context"]
            quoted = " ".join(
                context[max(0, hit.start() - 40):hit.end() + 60].split()
            )
            findings.append(
                f"{site}: CONTEXT_CONDITIONAL — the lead must evaluate {quoted!r}"
            )
        if d["branched"] and not isinstance(d["agent_liveness"], dict):
            findings.append(
                f"{site}: NO_LIVENESS — a branched action's payload carries no "
                f"agent_liveness"
            )
        if (
            isinstance(d["waiting_on_agents"], dict)
            and isinstance(d["agent_liveness"], dict)
            and d["waiting_on_agents"] != d["agent_liveness"]
        ):
            findings.append(
                f"{site}: SPLIT_READING — the notice and the payload report "
                f"two different readings"
            )
    return findings


def audit_action_imperatives(drives=None) -> list[str]:
    """lead-stalls FR-007's audit over every string `_ACTION_IMPERATIVES`
    can emit, and over every payload the router serves (lead-stalls D-018).

    Returns one human-readable line per DEFECTIVE emission — empty when the
    table is clean, which is what lead-stalls FR-008 discharges on.
    Deterministic in order and in content so the evidence log re-executes
    byte-identically.
    """
    findings: list[str] = []
    for action, site, text, owed, liveness in _audit_sites():
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
        # lead-stalls D-012 — THE SLOT THAT REACHED THE LEAD AS ITSELF.
        # `_GRIND_DISPATCH` named `grind-{run}-cycle-N`, and `N` was a LITERAL:
        # `{run}` resolved, `N` did not, and the `fix_defects` payload carries
        # no cycle number in any field for the lead to resolve it from. So the
        # lead was handed a TeamCreate it could not make and supplied the digit
        # from its own reckoning, which is the judgment task lead-stalls FR-007
        # defines as the defect. Every slot the formatter resolves is checked
        # by name rather than by a `\{[a-z_]+\}` sweep, because `add_castings`
        # legitimately carries the literal prose `casting-{id}-prompt.md`.
        leaked = [slot for slot in _SUBSTITUTED_SLOTS if slot in text]
        if leaked:
            findings.append(
                f"{site}: UNRESOLVED_SLOT — {', '.join(leaked)} survived to "
                f"the lead"
            )
        for name in _QUOTED_TEAM_NAME.findall(text):
            if not _CREATABLE_TEAM_NAME.match(name):
                findings.append(
                    f"{site}: UNCREATABLE_TEAM_NAME — the lead is told to pass "
                    f"{name!r}, which is not a team name"
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
            got = _emitted_branch(
                action, text, _AUDIT_RUN, liveness, None, _site_details(action)
            )
            if got != owed:
                findings.append(
                    f"{site}: WRONG_BRANCH — the lead received the {got!r} "
                    f"branch on a reading owed {owed!r}"
                )
    # lead-stalls D-018 / D-019 — and the payload the router actually serves,
    # so the zero lead-stalls FR-008 discharges on is a zero over what the lead
    # reads and not only over what the formatter would print for a reading.
    findings.extend(audit_assembled_payloads(drives))
    # lead-stalls D-038..D-043 — and the STEP LIST both were rendered from,
    # which is what the zero is computed over now (see the structure audit).
    findings.extend(audit_next_call_structure(drives))
    return findings


def audit_report() -> list[str]:
    """The finding list lead-stalls OT-002 requires recorded as evidence.

    The SAME sweep the test above asserts is empty, rendered per key. A log
    reading "0 findings" and nothing else is not a finding list -- it cannot be
    told apart from a sweep that ran over an empty roster, which is the
    green-over-nothing state the floor test beside it exists to catch. So the
    record names every key it judged and what it judged there.
    """
    drives = _router_drives()
    findings = audit_action_imperatives(drives)
    sites = _audit_sites()
    lines = [
        f"keys in _ACTION_IMPERATIVES: {len(_ACTION_IMPERATIVES)}",
        f"emission sites swept:        {len(sites)}",
        f"router payloads swept:       {len(drives)}",
        f"defective emissions:         {len(findings)}",
        "",
        "header detectors: CONDITIONAL, NO_LITERAL_CALL, UNRESOLVED_BRANCH, "
        "UNRESOLVED_SLOT, WRONG_BRANCH, UNCREATABLE_TEAM_NAME",
        "payload detectors: WRONG_ROUTE, CONDITIONAL, CONTRADICTION, CONTEXT_SEQUENCE, "
        "CONTEXT_CALL, CONTEXT_CONDITIONAL, TEARDOWN_OVER_RUNNING_AGENTS, "
        "NO_LIVENESS, SPLIT_READING",
        "CONTEXT_SEQUENCE, CONTEXT_CALL and CONTEXT_CONDITIONAL judge the CONTEXT of "
        "EVERY payload (lead-stalls D-051..D-053): it states the run state and "
        "names no call, no order and no condition.",
        "step-list detectors (every site and every payload, lead-stalls D-038..D-043): "
        "STEP_LIST, RENDERING, NONE, UNKNOWN_TOOL, YIELD_NOT_LAST, CALL_IN_PROSE, "
        "RULE_WITHOUT_PHASE, RULE_NOT_LEDGER_LAST, RULE_WITHOUT_ONE_MOVE, "
        "DOOR_WITHOUT_SPAWN, SPAWN_WITHOUT_DOOR, SPAWN_ORDER, SPAWN_NOT_ONE_MOVE, "
        "SPAWN_NOT_YIELDED, STREAM_WITHOUT_CALL, STREAM_WRONG_AGENT, "
        "STREAM_WITHOUT_CONFIG, STREAM_NOT_OWED, SERVED_PAST_THE_SEAL",
        "one-move walk (every site and every payload, lead-stalls D-044 / D-045): "
        "RULE_WITHOUT_LIST_MOVE, RULE_ORDERS_NEXT_MID_LIST, "
        "PROSE_ORDERS_NEXT_MID_LIST, NEXT_MID_LIST, STEP_ORDERS_NEXT_MID_LIST, "
        "ANSWER_BREAKS_LIST",
        "door answers the walk judged, read off the real handlers: "
        + "; ".join(
            f"{tool} -> {answer!r}" for tool, answer in sorted(_step_answers().items())
        ),
        "CONDITIONAL is the prose backstop; the zero is computed over the step lists.",
        "run_streams sites expand the stream steps from this run's cycle-10 roster: "
        + ", ".join(_C10_ROSTER),
        "",
        "site = action@emitting-phase[liveness reading]; readings swept: "
        + ", ".join(label for label, _, _ in _LIVENESS_READINGS),
        "",
        "readings are the four roster_agents x teams_active combinations, the "
        "unmeasured one, and",
        "the two that vary WAVE POSITION; each names the branch it is OWED so "
        "the sweep judges",
        "ROUTING and not only shape:",
        "",
        "wave_pending is the lead-stalls D-009 column: '-' is a manifest that "
        "did not answer,",
        "0 is every wave done, and N>0 is the wave still to dispatch. A sweep "
        "that held this",
        "column constant could not tell 'wave 1 done, wave 2 pending' from "
        "'the final wave is done'.",
        "refused / unaccepted are the lead-stalls D-020 columns: castings done "
        "and refused, and done",
        "with no verdict since. owed 'a>b' is a preference order: the first "
        "branch the entry declares.",
        "A refusal owes 'refused' (send back) when the refused casting's own "
        "wave team is among",
        "teams_registered, and 'redispatch' otherwise, whatever teams_active "
        "says (D-022, D-031).",
        "",
    ]
    for label, liveness, owed in _LIVENESS_READINGS:
        row = liveness if isinstance(liveness, dict) else {}
        lines.append(
            f"  {label:<26} waiting={str(bool(row.get('waiting'))):<5} "
            f"roster_agents={str(row.get('roster_agents', '-')):<4} "
            f"teams_active={str(row.get('teams_active', '-')):<5} "
            f"own_team_registered={_own_team_label(row):<3} "
            f"wave_pending={str(row.get('cast_wave_pending', '-')):<4} "
            f"wave_built={str(row.get('cast_wave_built', '-')):<4} "
            f"refused={','.join(row.get('cast_refused') or []) or '-':<3} "
            f"unaccepted={','.join(row.get('cast_unaccepted') or []) or '-':<3} "
            f"owed={_owed_label(owed)}"
        )
    lines.append("")
    for action in sorted(_ACTION_IMPERATIVES):
        hits = [f for f in findings if f.startswith(f"{action}@")]
        lines.append(f"  {action:<24} {'DEFECTIVE' if hits else 'ok'}")
    lines += [
        "",
        "ROUTER PAYLOADS — lead-stalls D-018 / D-019: Foundry-Next called on a real",
        "run state (a real Foundry-Team-Up into a scratch HOME where a team is up),",
        "at a fresh stall clock and at 600s. received and owed are action/branch;",
        "notice is what printed above the header.",
        "",
    ]
    for site, _state, _tr, owed_action, owed_branch, d in drives:
        hits = [f for f in findings if f.startswith(f"{site}:")]
        lines.append(
            f"  {site:<38} received {d['action']}/{d['branch'] or '-'}"
            f"  owed {owed_action}/{owed_branch or '-'}"
            f"  notice {_notice_kind(d['block'])}"
            f"  calls {'>'.join(c['tool'] for c in d['next_calls'] or []) or 'NONE'}"
            f"  {'DEFECTIVE' if hits else 'ok'}"
        )
    lines.append("")
    lines.extend(findings or ["(no entry hands the lead a conditional)"])
    return lines


def _own_team_label(row: dict) -> str:
    """The D-031 column: is the refused casting's own wave team registered?
    ``-`` when the reading names no refused casting's team."""
    own = row.get("cast_refused_team")
    if not isinstance(own, str):
        return "-"
    return "yes" if own in (row.get("teams_registered") or []) else "no"


def _notice_kind(block: str) -> str:
    """What printed above the header: nothing, the stall accusation, or the
    waiting notice — with the wait policy, or as a report only."""
    first = block.split("\n", 1)[0]
    if first.startswith("⏳"):
        return "waiting+end-turn" if "END YOUR TURN" in first else "waiting-report"
    if first.startswith("⚠"):
        return "stall"
    return "none"


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
    for action, site, text, owed, liveness in _audit_sites():
        head = " ".join(text.split("\n", 1)[0].split())
        answer = "NONE" if _NAMES_NO_CALL.search(text) else "CALL"
        branch = (
            f"{_emitted_branch(action, text, _AUDIT_RUN, liveness, None, _site_details(action))}/{owed}"
            if owed else "-"
        )
        # Two literal spaces on BOTH sides of the answer, independent of how
        # far the site label padded: the floor test beside this reads the
        # answer column by that separator, and a site label longer than the
        # pad width would otherwise drop its own row out of the count.
        lines.append(f"  {site:<48}  {answer}  {branch:<28} {head[:52]}")
    return lines


def turn_boundary_report(drives=None) -> list[str]:
    """What the server tells the lead at each end of the turn boundary.

    lead-stalls ST-001 / ST-002 / ST-003 / ST-004 are transitions of the LEAD'S
    SESSION, and a server test cannot drive the session: nothing here can make
    it idle or deliver a completion notification. What this casting
    contributes is the INSTRUCTION the lead is standing on when it decides
    whether to end its turn, and that is what this report shows -- the
    server-side half, named as such rather than as a proof of the whole.

    lead-stalls D-018 — COMPUTED THROUGH THE ROUTER. This report handed
    synthetic readings to `_format_imperative_header` and so showed branches
    `Foundry-Next` never served: with a team registered — the state the
    protocol produces — the router answered `cleanup_teams` ahead of every
    one of them. Every row below is a real `Foundry-Next` call on a real run
    state, a real `Foundry-Team-Up` included where a team is up.

      lead-stalls ST-001  agents running -> the lead ends its turn:
              ends-turn=yes.
      lead-stalls ST-002  the completion notification re-enters the lead:
              names-wake=yes, and the call the woken lead is told to make
              tears nothing down (`cast-live-owed` is PROVE's D-015 state:
              casting 1 just finished, casting 2 still writing, a real team
              up).
      lead-stalls ST-003  nothing running -> the run must not park:
              names-call=yes.
      lead-stalls ST-004  a refused casting -> re-dispatched, then
              re-accepted: the `cast-refused` row names the send-back to the
              registered team's teammate, `cast-refused-team-down` the fresh
              dispatch (D-022), `cast-refused-foreign-pane` and
              `cast-refused-other-team` the fresh dispatch while some OTHER
              team or pane is up (D-031), and `cast-refusal-answered` the
              acceptance.
    """
    drives = drives if drives is not None else _router_drives()
    lines = [
        "the server-side half of the turn boundary, per run state, through Foundry-Next.",
        "running: the reading the router routed on says an agent is progressing.",
        "ends-turn: the header answers 'YOUR NEXT CALL: NONE'.",
        "names-wake: it names the completion notification as what resumes the lead.",
        "names-call: it names at least one literal tool call.",
        "denies-poll: it forbids a sleep, a poll and a re-call loop by name.",
        "teardown: TeamDelete, Foundry-Team-Down or a shutdown appears above CONTEXT.",
        "",
    ]
    for site, state, transitions, _oa, _ob, d in drives:
        header = d["header"]
        reading = d["agent_liveness"]
        running = (
            ("yes" if reading.get("waiting") else "no")
            if isinstance(reading, dict) else "-"
        )
        clock = site.rsplit("[", 1)[1].rstrip("]")
        lines.append(
            f"  {transitions:<7} {state:<24} {clock:<5} "
            f"{d['action'] + '/' + (d['branch'] or '-'):<34} "
            f"running={running:<3} "
            f"ends-turn={'yes' if _NAMES_NO_CALL.search(header) else 'no':<3} "
            f"names-wake={'yes' if 'completion notification' in header else 'no':<3} "
            f"names-call={'yes' if _NAMES_A_CALL.search(header) else 'no':<3} "
            f"denies-poll={'yes' if 'do NOT poll' in header else 'n/a':<3} "
            f"teardown={'yes' if any(w in d['block'] for w in _TEARDOWN_WORDS) else 'no'}"
        )
    lines += [
        "",
        "ST-001 and ST-002 are discharged by 'ends-turn=yes AND names-wake=yes AND",
        "teardown=no' on every row where running=yes: ending the turn is stated as",
        "correct, the thing that ends the idle is named in the same breath, and the",
        "call the woken lead is told to make stops nobody.",
        "ST-003 is discharged by 'names-call=yes' on every row where running is not",
        "yes and the run is not DONE or HALTED: a lead that is handed a call cannot",
        "park for want of a move. The escalation_held rows are the exception, and the",
        "header says why: a class still ESCALATED past ASSAY with no defect open, where",
        "no transition the server accepts advances the cycle counter (lead-stalls",
        "D-055); at F2 the re-open is accepted and served instead (lead-stalls D-057).",
        "ST-004 is the cast-refused, cast-refused-team-down, cast-refused-foreign-pane,",
        "cast-refused-other-team and cast-refusal-answered rows: a refusal goes back to",
        "a teammate (the one that built it while its OWN wave team is registered, a",
        "fresh one otherwise, whatever else the machine's team scan sees), and a",
        "refusal the teammate has answered is accepted again.",
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

    lead-stalls D-055 — 22 IS THE POPULATION THE SPEC WAS WRITTEN AGAINST, AND
    EVERY ONE OF THEM IS STILL SWEPT. The audit iterates the table, so "all 22
    keys" is a floor on what it covers, not a ceiling on what the table may
    hold: `escalation_held` joined for the one run state no accepted
    transition leaves (a class still ESCALATED with nothing open), which has
    no calls to share with any other entry and so could not be a slot in one.
    The spec's 22 are pinned by name, so a key lost is caught as surely as by
    the count, and the one key added is pinned too.
    """
    assert set(_ACTION_IMPERATIVES) == _SPEC_KEYS | {"escalation_held"}, sorted(
        set(_ACTION_IMPERATIVES) ^ (_SPEC_KEYS | {"escalation_held"})
    )
    assert len(_SPEC_KEYS) == 22
    assert audit_action_imperatives() == []


#: The 22 keys lead-stalls FR-008 / OT-002 counted, by name.
_SPEC_KEYS = frozenset({
    "init", "halted", "cleanup_teams", "add_castings", "transition_to_cast",
    "build_castings", "transition_to_inspect", "run_streams",
    "transition_to_grind", "fix_defects", "transition_to_assay", "run_assay",
    "transition_to_done", "transition_to_temper", "run_temper",
    "transition_to_nyquist", "run_nyquist", "assay_failed_loop_back",
    "widen_inspect", "record_inspect_width", "done", "unknown",
})


def test_the_recorded_evidence_shows_the_population_it_judged():
    """Floor for the two report generators the evidence logs re-execute.

    A generator that quietly stopped emitting rows would leave a log that still
    re-executes byte-identically -- against its own emptiness. So the reports
    are pinned to the population they claim: one row per emission site, and one
    row per (audited action x run state).
    """
    dump = audit_evidence()
    for _action, site, _text, _owed, _reading in _audit_sites():
        assert any(site in line for line in dump), site
    assert sum(1 for l in dump if "  CALL  " in l or "  NONE  " in l) == len(
        _audit_sites()
    )
    # lead-stalls D-004 — the readings table is IN the log, so a reader can see
    # which run states the zero was computed over rather than trusting that it
    # covered them. The reading D-003 misrouted is named in it.
    for label, _liveness, owed in _LIVENESS_READINGS:
        assert any(
            line.strip().startswith(label)
            and f"owed={_owed_label(owed)}" in line
            for line in dump
        ), label
    assert any("registered-not-spawned" in line for line in dump)

    boundary = turn_boundary_report()
    rows = [line for line in boundary if "running=" in line and "names-call=" in line]
    # One row per (router state x clock), each naming its state.
    assert len(rows) == len(_ROUTER_STATES) * len(_CLOCKS), rows
    for state, *_ in _ROUTER_STATES:
        assert sum(f" {state} " in line for line in rows) == len(_CLOCKS), state
    # The claims the log is bound to lead-stalls ST-001 / ST-002 / ST-003 for.
    assert any("running=yes" in line for line in rows), rows
    for line in rows:
        if "running=yes" in line:
            assert "ends-turn=yes" in line, line
            assert "names-wake=yes" in line, line
            assert "teardown=no" in line, line
        elif " done/" in line or " halted/" in line:
            # lead-stalls ST-003's guard is "run not DONE or HALTED": a
            # terminal ends the run, and names a call only when one is owed.
            assert "ends-turn=yes" in line or "names-call=yes" in line, line
        elif " escalation_held/" in line:
            # lead-stalls D-055 — the one non-terminal state that ends the
            # turn by design: no transition the server accepts leaves it.
            assert "ends-turn=yes" in line, line
        else:
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
    sites = [site for _, site, _, _, _ in _audit_sites()]
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
    # would make `WRONG_BRANCH` unfalsifiable. lead-stalls D-020: and both
    # acceptance states, each backed by a base state for the entries that do
    # not declare them.
    owed_names = set()
    for _l, _lv, owed in _LIVENESS_READINGS:
        owed_names.update((owed,) if isinstance(owed, str) else owed)
    assert owed_names == {
        "live", "idle", "undispatched", "refused", "redispatch", "unaccepted",
    }, owed_names
    # lead-stalls D-009 — AND THE WAVE POSITION IS VARIED, which is the axis the
    # product above does not contain at all. The three values that matter are a
    # manifest that did not answer (the roster fallback), every wave done, and a
    # LATER wave still pending: the last is the only one in which "wave N
    # finished" can be told from "wave N+1 not started", so a sweep missing it
    # returns zero over a space D-009 cannot appear in.
    positions = {
        (liveness if isinstance(liveness, dict) else {}).get("cast_wave_pending")
        for _l, liveness, _o in _LIVENESS_READINGS
    }
    assert None in positions, positions
    assert 0 in positions, positions
    assert any(
        isinstance(value, int) and not isinstance(value, bool) and value > 1
        for value in positions
    ), positions
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
        # lead-stalls D-013 — the wave-complete branch is reached by a MEASURED
        # position and by nothing else now, so the reading that stands on it
        # here has to carry one. A bare roster used to reach this text, and the
        # text it reached says "this server read the manifest".
        liveness={"waiting": False, "roster_agents": 4, "teams_active": True,
                  "cast_wave_pending": 0, "cast_wave_built": 1},
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


#: lead-stalls D-013 — the readings a caller can hand the selector, beyond the
#: ones the sweep already varies: hostile types, missing keys, and the two bool
#: values `isinstance(x, int)` would otherwise read as wave 1 and wave 0.
#: Enumerated here rather than in the test body so both claims below are made
#: over the same space and cannot drift apart.
_UNMEASURED_READINGS = (
    None, {}, "not a dict", 0, [], {"waiting": None},
    {"waiting": False},
    {"waiting": False, "roster_agents": 0, "teams_active": False},
    {"waiting": False, "roster_agents": 1, "teams_active": False},
    {"waiting": False, "roster_agents": 9, "teams_active": True},
    {"waiting": False, "roster_agents": 9, "cast_wave_pending": None},
    {"waiting": False, "roster_agents": 9, "cast_wave_pending": True},
    {"waiting": False, "roster_agents": 9, "cast_wave_pending": False},
    {"waiting": False, "roster_agents": 9, "cast_wave_pending": "0"},
    {"waiting": False, "roster_agents": 9, "cast_wave_built": 2},
)


def test_an_unmeasured_position_never_reaches_the_wave_complete_branch():
    """lead-stalls D-013's remedy, as a property of the selector rather than of
    one reading.

    `idle` IS `_CAST_WAVE_COMPLETE` for `build_castings` -- tear the team down,
    gate, cross into F2 -- so a reading that measured NO wave position must not
    be able to produce it. The roster fallback could, and did: a non-empty
    roster cannot tell "wave 1 done, wave 2 never dispatched" from "the final
    wave is done", which is the reading D-009 and D-010 were filed against, and
    the arm below it was left on the pre-change contract.

    Swept over every unmeasured shape rather than asserted on one, because the
    defect was never about a particular manifest failure -- a truncated write,
    the `waves: []` Foundry-Init seeds, and wave numbers typed as strings all
    arrive here as the same absence.
    """
    for reading in _UNMEASURED_READINGS:
        assert _branch_state(reading) == "undispatched", reading
        text = _format_imperative_header(
            "build_castings", "", {}, run_name="vm", phase="F1",
            liveness=reading,
        )
        # The two wrong results one emission carried, each pinned by what the
        # lead would have been told to DO about it.
        assert "this server read the manifest and the progress ledgers" not in (
            text
        ), reading
        assert "Foundry-Team-Down(" not in text, reading
        assert "Foundry-Gate(phase='inspect')" not in text, reading
        assert "Foundry-Phase(phase='cast')" not in text, reading

    # Not vacuous: a MEASURED zero still reaches it, so this is a claim about
    # the measurement and not a claim that the branch became unreachable.
    measured = _format_imperative_header(
        "build_castings", "", {}, run_name="vm", phase="F1",
        liveness={"waiting": False, "roster_agents": 9, "teams_active": False,
                  "cast_wave_pending": 0, "cast_wave_built": 2},
    )
    assert "this server read the manifest and the progress ledgers" in measured
    assert "Foundry-Team-Down(team_name='cast-vm-wave-2')" in measured, measured


def test_the_wave_complete_sentence_is_true_wherever_it_is_emitted():
    """lead-stalls D-013 remedy (4): `_CAST_WAVE_COMPLETE` closes with "this
    server read the manifest and the progress ledgers on this call", and an
    imperative that asserts a measurement it never made is a defect on its own
    terms -- the lead is routed through a phase gate on the strength of it.

    Read off the EMITTED headers rather than off the constant, over every
    reading the sweep varies plus every unmeasured shape above: the claim is
    about what reaches the lead, so the population is the emission sites and not
    the table entry. Any site making the claim must carry a measured, non-bool
    `cast_wave_pending` -- which is the only input from which
    `_cast_wave_position` publishes a number at all.
    """
    claim = "this server read the manifest and the progress ledgers"
    readings = [liveness for _l, liveness, _o in _LIVENESS_READINGS]
    readings += list(_UNMEASURED_READINGS)
    claimed = 0
    for reading in readings:
        for action in ("build_castings", "fix_defects"):
            text = _format_imperative_header(
                action, "", {}, run_name="vm", phase="F1", liveness=reading,
            )
            if claim not in text:
                continue
            claimed += 1
            row = reading if isinstance(reading, dict) else {}
            pending = row.get("cast_wave_pending")
            assert isinstance(pending, int) and not isinstance(pending, bool), (
                action, reading, "claims a manifest read it did not make",
            )
    assert claimed, "no emission makes the claim; the guard cannot bite"


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
    streams = _parse_branches(_ACTION_IMPERATIVES["run_streams"])
    assert sorted(grind) == ["idle", "live"], sorted(grind)
    # lead-stalls D-020 — and the acceptance states only F1 has: a casting
    # done but refused, and a casting done and not yet accepted. D-022 made the
    # refusal two, the send-back and the fresh dispatch, chosen by the server.
    assert sorted(cast) == [
        "idle", "live", "redispatch", "refused", "unaccepted", "undispatched",
    ], sorted(cast)
    # lead-stalls D-019 — `run_streams` is the third action whose work is
    # agents, and it takes the `fix_defects` shape: running, or not.
    assert sorted(streams) == ["idle", "live"], sorted(streams)
    # lead-stalls D-056 — and `add_castings` the fourth: the decomposition
    # writers are running, or none is.
    castings = _parse_branches(_ACTION_IMPERATIVES["add_castings"])
    assert sorted(castings) == ["idle", "live"], sorted(castings)
    # Every branched entry declares the fallback, so `_select_branch` never has
    # to reach its total tail on shipped input.
    for action, branches in (
        ("fix_defects", grind), ("build_castings", cast), ("run_streams", streams),
        ("add_castings", castings),
    ):
        assert branches.get(_BRANCH_FALLBACK), action
    # And no OTHER entry is branched.
    branched = sorted(
        a for a, t in _ACTION_IMPERATIVES.items() if _parse_branches(t)
    )
    assert branched == [
        "add_castings", "build_castings", "fix_defects", "run_streams",
    ], branched


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
    The rest emit exactly as they did and carry no new field, so a
    Foundry-Next at F4 costs no ledger scan.

    lead-stalls D-056 — this was pinned at F0, and F0 was the defect: its
    decomposition writers are agents the lead is woken by one at a time, and
    with no reading there the first writer's manifest entry sent the lead on
    to validation while the rest were still writing. F0 is scanned now (see
    the decompose router states), so the unscanned phase pinned here is one
    whose answer no running agent changes.
    """
    project_root, fdir = run_env
    _assay(fdir, ["VERIFIED"])
    _progressing_ledger(fdir)

    nxt = foundry_next_action(project_root)

    assert nxt["action"] == "transition_to_done"
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

    def _counted(pr, **kwargs):
        calls.append(pr)
        return real(pr, **kwargs)

    monkeypatch.setattr(_g, "_waiting_on_agents", _counted)
    nxt = foundry_next_action(project_root)

    assert len(calls) == 1, calls
    assert "WAITING ON" in nxt["instructions"]
    assert nxt["agent_liveness"]["waiting"] is True


def test_the_roster_outlives_the_teardown_the_imperative_itself_names(run_env):
    """Why the ROSTER is read, and why `teams_active` could not stand in.

    The wave-complete branch tells the lead to `Foundry-Team-Down` and THEN
    `Foundry-Phase(phase='cast')`. Between those two calls a team scan reads
    exactly like a wave that was never dispatched -- and a selector keyed on the
    team scan alone would answer 'undispatched' there and send the lead to spawn
    a fresh CAST wave over castings it had just accepted. A progress ledger is
    written once and stays written, so the roster still remembers.

    lead-stalls D-013 -- AND WHAT THE ROSTER IS READ *FOR* IS THE WAVE POSITION,
    NOT THE BRANCH.
    ---------------------------------------------------------------------------
    This stood on a bare `roster_agents >= 1` with no manifest at all, so what
    it actually pinned was the fallback arm: an UNMEASURED reading answering
    `idle`, which is the defect. The property it was written for is real and
    survives -- the teardown unregisters the team and the ledger outlives it --
    but the thing that carries it across the teardown is the terminal `"done":
    true` line the position reader walks. So the run below is the same run state
    with its manifest present: team torn down, ledger done, position measured 0.
    """
    project_root, fdir = run_env
    _write_state(fdir, phase="F1", cycle=0)
    _wave_manifest(fdir, {1: ["3"]})
    _ledger(fdir, "casting-3", done=True)   # finished: in the roster, not live
    _verdict(fdir, "3", "accepted")         # and accepted (lead-stalls D-020)
    _teams_active(False)                    # torn down

    reading = _waiting_on_agents(project_root)

    assert reading["waiting"] is False
    assert reading["teams_active"] is False
    assert reading["roster_agents"] >= 1
    # The measured 0 is what makes this the wave-complete state; the roster is
    # how the 0 was reached, not a second route to the same branch.
    assert reading["cast_wave_pending"] == 0, reading
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
        # The SHIPPED selector with the disjunct put back and nothing else
        # changed. A variant that also dropped the lead-stalls D-009 wave read
        # would misroute the wave-boundary reading too, and this control would
        # stop being a control for the disjunct.
        #
        # lead-stalls D-013 — AND THAT RULE IS WHY THE DISJUNCT IS NOW THE WHOLE
        # ARM. D-003's line read `roster_agents or teams_active`; D-013 deleted
        # the `roster_agents` term from the shipped selector, so restoring the
        # historical line verbatim would restore TWO defects and misroute the
        # `unreadable-manifest` reading as well — the same "stops being a
        # control for the disjunct" the paragraph above forbids. The disjunct
        # was always the `teams_active` term; that term, alone, on today's
        # selector, is D-003 and nothing else.
        row = liveness if isinstance(liveness, dict) else {}
        if row.get("waiting"):
            return "live"
        pending = row.get("cast_wave_pending")
        if isinstance(pending, int) and not isinstance(pending, bool):
            return "undispatched" if pending > 0 else "idle"
        if row.get("teams_active"):
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
# lead-stalls D-009 / D-010 / D-012 — wave position, and the slots that carry it
# --------------------------------------------------------------------------- #

#: A timestamp old enough that nothing here depends on wall-clock drift. A
#: terminal `"done": true` line makes an agent DONE whatever its age, which is
#: the property `_cast_wave_position` reads.
_LONG_AGO = "2020-01-01T00:00:00+00:00"


def _wave_manifest(fdir, waves: dict[int, list[str]]) -> None:
    """A castings manifest with the given ``{wave number: [casting ids]}``."""
    (fdir / "castings").mkdir(parents=True, exist_ok=True)
    (fdir / "castings" / "manifest.json").write_text(
        json.dumps({
            "castings": [
                {"id": cid, "title": cid, "wave": number}
                for number, ids in sorted(waves.items()) for cid in ids
            ],
            "waves": [
                {"wave": number, "casting_ids": list(ids)}
                for number, ids in sorted(waves.items())
            ],
        }),
        encoding="utf-8",
    )


def _ledger(fdir, agent: str, *, done: bool) -> None:
    """A casting's progress ledger, WORKED (``done``) or merely SEEDED.

    The seeded form is what `Foundry-Cast-Wave` writes at dispatch, before the
    lead has spawned a single Agent — which is why neither it nor `spawns.log`
    can answer "was this wave built". The terminal `"done": true` line the
    progress protocol requires is the only signal in the run that a teammate
    actually worked the casting.
    """
    (fdir / "progress").mkdir(parents=True, exist_ok=True)
    lines = [{"timestamp": _LONG_AGO, "phase": "cast", "step": "dispatched",
              "agent": agent, "seeded_by": "server"}]
    if done:
        lines.append({"timestamp": _LONG_AGO, "phase": "cast",
                      "step": "committed", "done": True})
    (fdir / "progress" / f"{agent}.jsonl").write_text(
        "".join(json.dumps(line) + "\n" for line in lines), encoding="utf-8",
    )


def _verdict(
    fdir, casting_id: str, verdict: str, *, at: str | None = None
) -> None:
    """Append the acceptance verdict `Foundry-Accept-Casting` records.

    lead-stalls D-020 — a done line is not a built casting until it is
    accepted, so every fixture below that means "this casting is BUILT" says so
    the way the run does. The record's shape is pinned against the REAL writer
    by `test_the_reader_reads_the_verdict_the_acceptance_door_writes`, so this
    helper cannot drift into a spelling the door never writes.
    """
    with (fdir / "handoffs.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({
            "event": _ACCEPTANCE_EVENT,
            "source": f"castings/casting-{casting_id}-prompt.md",
            "destination": _acceptance_destination(casting_id, verdict),
            "timestamp": at or datetime.now(timezone.utc).isoformat(),
        }) + "\n")


def test_a_finished_wave_with_a_wave_still_pending_is_not_a_finished_build(
    run_env
):
    """lead-stalls US-001 / D-009 — THE READING THIS RUN ITSELF STOOD IN.

    `spawns.log` records wave 1 dispatched at 00:58 and wave 2 at 02:03; in
    between, the lead had accepted both wave-1 castings and torn the team down.
    `_branch_state` read `roster_agents` alone, which counts every ledger in the
    run, so that state read `idle` -- and the lead was handed
    `_CAST_WAVE_COMPLETE`: TeamDelete, Foundry-Team-Down, gate, cross into F2,
    with a third of the castings never built. lead-stalls US-001 requires the
    opposite in the same turn: "the lead dispatches the next wave".

    Driven through `_waiting_on_agents` against a real two-wave manifest rather
    than off a synthetic dict, so what is pinned is the reading the watchdog
    actually produces at that boundary.
    """
    project_root, fdir = run_env
    _write_state(fdir, phase="F1", cycle=0)
    _wave_manifest(fdir, {1: ["imperatives", "payloads"], 2: ["release"]})
    _ledger(fdir, "casting-imperatives", done=True)
    _ledger(fdir, "casting-payloads", done=True)
    _verdict(fdir, "imperatives", "accepted")
    _verdict(fdir, "payloads", "accepted")
    _teams_active(False)           # the team of wave 1 is already torn down

    reading = _waiting_on_agents(project_root)

    # The two fields the pre-change selector had, unchanged -- this is the
    # reading it called `idle`, and it still looks exactly like that in them.
    assert reading["waiting"] is False
    assert reading["roster_agents"] == 2
    # The field that tells the two states apart.
    assert reading["cast_wave_pending"] == 2
    assert reading["cast_wave_built"] == 1
    assert _branch_state(reading) == "undispatched"

    text = _format_imperative_header(
        "build_castings", "", {}, run_name="vm", phase="F1", liveness=reading,
    )
    # What it is owed: wave TWO dispatched, named as a call it can make.
    assert "TeamCreate('cast-vm-wave-2')" in text, text
    assert "Foundry-Cast-Wave(wave=2, phase='cast')" in text, text
    # What it must never be handed here: the teardown and the F2 crossing.
    assert "TeamDelete" not in text, text
    assert "Foundry-Gate(phase='inspect')" not in text, text
    assert "Foundry-Phase(phase='cast')" not in text, text
    # And never wave 1, whose castings are already accepted.
    assert "wave-1" not in text and "wave=1" not in text, text


def test_the_final_wave_finishing_is_a_finished_build(run_env):
    """The other side of the same field: `cast_wave_pending == 0` is the only
    reading that earns the teardown, and the team it names is the wave that was
    actually built rather than the literal `wave-1` this branch carried.

    Same run, same manifest, one more ledger -- so what the assertion isolates
    is the wave position and not the fixture.
    """
    project_root, fdir = run_env
    _write_state(fdir, phase="F1", cycle=0)
    _wave_manifest(fdir, {1: ["imperatives", "payloads"], 2: ["release"]})
    for cid in ("imperatives", "payloads", "release"):
        _ledger(fdir, f"casting-{cid}", done=True)
        _verdict(fdir, cid, "accepted")
    _teams_active(True)

    reading = _waiting_on_agents(project_root)

    assert reading["cast_wave_pending"] == 0
    assert reading["cast_wave_built"] == 2
    assert _branch_state(reading) == "idle"

    text = _format_imperative_header(
        "build_castings", "", {}, run_name="vm", phase="F1", liveness=reading,
    )
    assert "Foundry-Team-Down(team_name='cast-vm-wave-2')" in text, text
    assert "Foundry-Gate(phase='inspect')" in text, text
    assert "Foundry-Phase(phase='cast')" in text, text
    assert "TeamCreate(" not in text, text


def test_a_seeded_ledger_is_a_dispatch_and_not_a_built_wave(run_env):
    """Why the terminal `"done": true` and not `spawns.log`.

    `Foundry-Cast-Wave` writes the spawn record AND seeds the ledger before the
    lead spawns a single Agent (`foundry_spawn.py#foundry_cast_wave`, the
    buffered `seeds` list). So a wave whose dispatch was recorded but whose
    teammates never worked must still read as PENDING -- otherwise the lead is
    sent to dispatch the NEXT wave over a wave nothing built, which is D-009's
    own failure with the waves shifted by one.
    """
    project_root, fdir = run_env
    _write_state(fdir, phase="F1", cycle=0)
    _wave_manifest(fdir, {1: ["imperatives", "payloads"], 2: ["release"]})
    _ledger(fdir, "casting-imperatives", done=True)
    _verdict(fdir, "imperatives", "accepted")
    _ledger(fdir, "casting-payloads", done=False)   # seeded, never worked

    reading = _waiting_on_agents(project_root)

    assert reading["cast_wave_pending"] == 1, reading
    assert _branch_state(reading) == "undispatched"
    text = _format_imperative_header(
        "build_castings", "", {}, run_name="vm", phase="F1", liveness=reading,
    )
    assert "Foundry-Cast-Wave(wave=1, phase='cast')" in text, text


def test_an_unreadable_manifest_falls_back_rather_than_claims_a_finished_wave(
    run_env
):
    """lead-stalls GI-008's totality, on the new field.

    A manifest that does not answer must leave `cast_wave_pending` ABSENT and
    not `0`: `0` reads as "every wave is done" and hands the lead the teardown,
    which is the worst answer a failed read could give.

    lead-stalls D-013 — AND THE SELECTOR HAS TO HONOUR THE ABSENCE, WHICH IS THE
    HALF THIS TEST USED TO ASSERT THE VIOLATION OF.
    ---------------------------------------------------------------------------
    The field went absent and the assertion below read `== "idle"`, because the
    selector fell back to the roster and `idle` IS `_CAST_WAVE_COMPLETE`. So a
    test named for "falls back rather than CLAIMS A FINISHED WAVE" pinned the
    lead being handed exactly that -- tear the team down, gate, cross into F2 --
    off a manifest read that failed. Absent now routes to the DISPATCH branch,
    which is the cheap direction of the asymmetry: a redundant `TeamCreate` that
    answers "already registered" against a phase crossing over unbuilt castings.
    The name is what the assertion says now.
    """
    project_root, fdir = run_env
    _write_state(fdir, phase="F1", cycle=0)
    (fdir / "castings").mkdir(parents=True, exist_ok=True)
    (fdir / "castings" / "manifest.json").write_text("{ truncated", encoding="utf-8")
    _ledger(fdir, "casting-imperatives", done=True)

    reading = _waiting_on_agents(project_root)

    assert reading["cast_wave_pending"] is None, reading
    assert reading["cast_wave_built"] is None, reading
    assert reading["roster_agents"] == 1
    assert _branch_state(reading) == "undispatched"
    # The two wrong results the roster arm produced together, both closed by the
    # one branch. The lead is no longer told the manifest was read, and no
    # longer told to tear down a team named from a defaulted wave number.
    text = _format_imperative_header(
        "build_castings", "", {}, run_name="vm", phase="F1", liveness=reading,
    )
    assert "this server read the manifest" not in text, text
    assert "Foundry-Team-Down(" not in text, text
    assert "Foundry-Gate(phase='inspect')" not in text, text

    # And a manifest whose `waves` list holds nothing usable is the same case,
    # not a run with no waves left. So is the `waves: []` that Foundry-Init
    # seeds, and so are wave numbers written as strings -- three shapes, one
    # reading, one branch (lead-stalls D-013).
    for hostile in (
        {"castings": [], "waves": [{"wave": "one"}, "junk"]},
        {"castings": [], "waves": []},
        {"castings": [], "waves": [{"wave": "1", "casting_ids": ["imperatives"]}]},
    ):
        (fdir / "castings" / "manifest.json").write_text(
            json.dumps(hostile), encoding="utf-8",
        )
        again = _waiting_on_agents(project_root)
        assert again["cast_wave_pending"] is None, hostile
        assert _branch_state(again) == "undispatched", hostile


def test_the_liveness_result_carries_the_field_the_branch_is_chosen_from(
    run_env
):
    """lead-stalls FR-015 / CT-008 / D-010 — THE DECLARED INPUT HAS TO CONTAIN
    THE ANSWER.

    lead-stalls FR-015 is Locked: "the server substitutes the right one using
    `_waiting_on_agents` at emission". lead-stalls CT-008 declares that result
    as the whole input to the branch choice. The result carried no wave field
    at all, so the declared input could not decide the branch the requirement
    names -- lead-stalls GI-008
    held (one unconditional string, well-formed) while the CHOICE was wrong,
    which is a defect no shape check can see.

    Pinned as a property of the RESULT rather than of the selector: the two run
    states must differ in what `_waiting_on_agents` returns, or nothing
    downstream of it can tell them apart however it is written.
    """
    project_root, fdir = run_env
    _write_state(fdir, phase="F1", cycle=0)
    _wave_manifest(fdir, {1: ["imperatives", "payloads"], 2: ["release"]})
    _ledger(fdir, "casting-imperatives", done=True)
    _ledger(fdir, "casting-payloads", done=True)
    boundary = _waiting_on_agents(project_root)

    _ledger(fdir, "casting-release", done=True)
    finished = _waiting_on_agents(project_root)

    # The fields the pre-change selector read agree on both readings, which is
    # exactly why it could not tell them apart.
    assert boundary["waiting"] == finished["waiting"] is False
    assert bool(boundary["roster_agents"]) == bool(finished["roster_agents"])
    # The declared input now distinguishes them.
    assert boundary["cast_wave_pending"] != finished["cast_wave_pending"]
    assert _branch_state(boundary) != _branch_state(finished)
    # And it is on the payload the lead can read, not only in the selector.
    for key in ("cast_wave_pending", "cast_wave_built"):
        assert key in boundary and key in finished, key


def test_fix_defects_is_unmoved_by_the_wave_field(run_env):
    """The coupling lead-stalls D-009 introduces, bounded.

    `_cast_wave_position` is measured for BOTH branched actions, because the
    reading is taken once per call. In F3 the ledgers have been re-seeded for
    the GRIND dispatch and carry no terminal line, so `cast_wave_pending` reads
    as a pending wave on every `fix_defects` emission -- and that must change
    nothing. It does not: `fix_defects` declares no `undispatched` branch, so
    the state resolves through `_BRANCH_FALLBACK` to the same `idle` body every
    other non-live reading gets. Pinned rather than argued, because "the new
    field cannot reach the other action" is exactly the kind of claim that stops
    being true when somebody adds the branch.
    """
    bodies = {
        _format_imperative_header(
            "fix_defects", "", {}, run_name="r", phase="F3", cycle=3,
            liveness={"waiting": False, "roster_agents": 2, **extra},
        )
        for extra in (
            {},
            {"cast_wave_pending": 0, "cast_wave_built": 2},
            {"cast_wave_pending": 1, "cast_wave_built": 1},
            {"cast_wave_pending": 9, "cast_wave_built": 8},
        )
    }
    assert len(bodies) == 1, bodies
    assert "Foundry-Spawn-Teammate" in bodies.pop()


def test_the_audit_sees_a_wave_boundary_handed_the_teardown(monkeypatch):
    """lead-stalls D-009 — the positive control, and lead-stalls OT-002's
    honesty.

    The recorded audit's own `owed` column assigned `roster_agents=3 -> idle`,
    which ENCODES the assumption the defective code made: 138/138 emissions
    passed while wave position was never varied once, so the sweep's zero was
    computed over a space the defect could not appear in. The
    `wave-boundary` reading is what varies it; this asserts the sweep BITES
    when the selector goes back to reading the roster alone, at that site and
    nowhere else.
    """
    import foundry_mcp.tools.orchestration.guidance as _g

    def _roster_only(liveness: object) -> str:
        # The shipped selector as it stood before lead-stalls D-009: the wave
        # field is simply not consulted.
        row = liveness if isinstance(liveness, dict) else {}
        if row.get("waiting"):
            return "live"
        if row.get("roster_agents"):
            return "idle"
        return "undispatched"

    monkeypatch.setattr(_g, "_branch_state", _roster_only)

    misrouted = [f for f in audit_action_imperatives() if "WRONG_BRANCH" in f]

    assert misrouted, "the sweep cannot see a wave-position misrouting"
    assert any(
        "build_castings@F1[wave-boundary]" in f
        and "'idle'" in f and "'undispatched'" in f
        for f in misrouted
    ), misrouted
    # lead-stalls D-013 — TWO SITES NOW, AND THE SECOND IS NOT SLACK IN THE
    # CONTROL. The roster-only selector is the shipped code before D-009 AND
    # before D-013, because D-013 is the deletion of that same roster arm: there
    # is no variant that drops the wave read and keeps the D-013 fix, since
    # without the wave read the arm is all that is left. So this control now
    # reproduces both defects and bites at both readings that vary in exactly
    # what each defect was about — wave position, and a position that was never
    # measured. Both are named, so neither can go quiet unnoticed.
    assert {f.split(":")[0] for f in misrouted} == {
        "build_castings@F1[wave-boundary]",
        "build_castings@F1[unreadable-manifest]",
    }, sorted({f.split(":")[0] for f in misrouted})


def test_no_emitted_header_leaves_a_substitution_slot_literal():
    """lead-stalls OT-013 / D-012 — a slot that reached the lead as itself.

    `_GRIND_DISPATCH` named `grind-{run}-cycle-N`. `{run}` resolved and `N` did
    not, because it was a literal character rather than a slot, and the
    `fix_defects` payload carries no cycle number in any field for the lead to
    resolve it from. Driven end-to-end in a real run, the lead received
    `TeamCreate('grind-bold-wren-cycle-N')` -- a call it cannot make -- and
    supplied the digit from its own reckoning, which is the judgment task
    lead-stalls FR-007 defines as the defect. The controlled contrast in the
    same run was `build_castings`, whose team name rendered fully literal.

    Asserted over every emission site rather than over the one entry, because
    which key held the literal is not what was wrong with it.
    """
    for _action, site, text, _owed, _reading in _audit_sites():
        for slot in _SUBSTITUTED_SLOTS:
            assert slot not in text, (site, slot)

    # Positive control: the detector bites on the entry as it stood. A guard
    # that stopped matching would report a clean table for the reason that it
    # can no longer see one.
    assert [s for s in _SUBSTITUTED_SLOTS if s in "TeamCreate('grind-{run}-cycle-{cycle}')"]


def test_the_audit_sees_a_team_name_the_lead_cannot_create():
    """lead-stalls FR-008 / D-012 — the positive control for
    `UNCREATABLE_TEAM_NAME`, and the answer to "the audit must be able to catch
    a recurrence".

    The sweep returned zero over `TeamCreate('grind-bold-wren-cycle-N')` because
    every detector it had was about CONDITIONALS and BRANCHES: the string names
    a literal tool call, hands over no condition, leaks no marker and routes to
    the right arm. What was wrong with it is that the argument is not a value.
    So the audit gained the one check that expresses that decidably -- a quoted
    team name has one grammar -- and this asserts it bites on the entry as it
    stood rather than being a check that never fires.
    """
    branches = _IMPERATIVES["fix_defects"]
    original = branches["idle"]
    try:
        branches["idle"] = original._replace(steps=tuple(
            step._replace(args=step.args.replace(
                "grind-{run}-cycle-{cycle}", "grind-{run}-cycle-N"
            )) if step.args else step
            for step in original.steps
        ))
        assert branches["idle"] != original
        findings = [
            f for f in audit_action_imperatives()
            if "UNCREATABLE_TEAM_NAME" in f
        ]
    finally:
        branches["idle"] = original

    assert findings, "the sweep cannot see an uncreatable team name"
    assert all(f.startswith("fix_defects@") for f in findings), findings
    assert any("grind-audit-cycle-N" in f for f in findings), findings
    # And the restored table is clean again, so the control left nothing behind.
    assert audit_action_imperatives() == []


def test_the_grind_team_name_is_a_number_the_lead_can_create(run_env):
    """lead-stalls D-012 — the cycle the server owns, not the one the lead
    invents.

    `current_cycle` is the counter `Foundry-Phase(phase='inspect_start')`
    advances, the one `Foundry-Next` displays, and the one the same payload
    tells the lead to pass as `Foundry-Fix(cycle=...)`. Substituting it makes
    the team name agree with the `fixed_in_cycle` the ledger then records -- one
    number, three places -- where the lead's own reckoning agreed with none of
    them.

    Driven through `foundry_next_action` and not only through the formatter, so
    what is pinned is the string the lead RECEIVES on a real run.
    """
    project_root, fdir = run_env
    _write_state(fdir, phase="F3", cycle=4)
    _defect_ledger(fdir, [_tiered("D-1", "LIVE", status="open")])

    result = foundry_next_action(project_root)

    assert result["action"] == "fix_defects", result["action"]
    text = result["instructions"]
    assert "grind-c3-test-run-cycle-4" in text, text
    assert "cycle-N" not in text, text
    # Every mention is the SAME number: step (1) tears down what steps (3) and
    # (4) create, so a second spelling here would leave a team registered.
    assert text.count("grind-c3-test-run-cycle-4") == 3, text


def test_transition_to_grind_names_the_same_cycle_the_dispatch_does():
    """The sibling entry carried the identical `cycle-N` literal, and
    lead-stalls FR-007 is explicit that the audit fixes any others found. Both
    resolve through one helper off one scalar, so the team the F2->F3 crossing
    creates is the team the F3 dispatch tears down.
    """
    for action in ("transition_to_grind", "fix_defects"):
        text = _format_imperative_header(
            action, "", {}, run_name="r", phase="F2",
            liveness={"waiting": False, "roster_agents": 0}, cycle=7,
        )
        assert "grind-r-cycle-7" in text, action
        assert "cycle-N" not in text, action


def test_the_cycle_slot_resolves_for_every_input(run_env):
    """`_grind_cycle`'s totality, on `_halt_cause`'s side of the line. An
    unresolved `{cycle}` would discard the imperative and take the generic
    "Execute the first tool call mentioned. Do not deliberate." header -- the
    conditional-judgment push lead-stalls GI-008 forbids -- over a branch the
    lead never chose.
    """
    for hostile in (None, "4", -1, True, False, 2.5, [], {}, object()):
        text = _format_imperative_header(
            "fix_defects", "", {}, run_name="r", phase="F3",
            liveness={"waiting": False, "roster_agents": 0}, cycle=hostile,
        )
        assert "{cycle}" not in text, hostile
        assert "Execute the first tool call mentioned" not in text, hostile
        assert "TeamCreate('grind-r-cycle-" in text, hostile


def test_the_wave_slots_resolve_for_every_input():
    """The same claim for `{wave}` and `{built_wave}`. `bool` is excluded on
    purpose: `True` is not wave 1 and `False` is not "every wave is done", and
    a selector that read them as such would route on a type accident.
    """
    for hostile in (
        None, {}, "not a dict", {"cast_wave_pending": "2"},
        {"cast_wave_pending": True}, {"cast_wave_built": False},
        {"cast_wave_pending": -1}, {"cast_wave_built": None},
    ):
        for action in ("build_castings", "fix_defects"):
            text = _format_imperative_header(
                action, "", {}, run_name="r", phase="F1", liveness=hostile,
            )
            assert "{wave}" not in text and "{built_wave}" not in text, hostile
            assert "Execute the first tool call mentioned" not in text, hostile
    # `True` must not be read as wave 1 by accident of `isinstance(True, int)`,
    # and `False` must not be read as "every wave is done". Neither is a
    # measurement, so both owe the dispatch branch however full the roster is
    # (lead-stalls D-013 -- the second of these asserted `idle`, which is the
    # roster arm answering for a reading that measured no wave at all).
    assert _branch_state({"waiting": False, "cast_wave_pending": True}) == "undispatched"
    assert _branch_state(
        {"waiting": False, "roster_agents": 2, "cast_wave_pending": False}
    ) == "undispatched"


def test_transition_to_cast_keeps_wave_one_as_a_literal():
    """lead-stalls D-014 / D-009 — the literal `1` in `transition_to_cast` is
    a decision, and until this test it was a decision only a comment held.

    `_compute_next_action` returns `transition_to_cast` from F0 and from
    nowhere else, so the only wave the entry can describe is the first, and the
    comment above it in `guidance.py` forbids turning its `cast-{run}-wave-1`
    into a `{wave}` slot: that slot would resolve off a liveness reading taken
    before any wave exists. The entry that serves later waves is
    `build_castings`'s dispatch branch, which carries the slot.

    WHY THIS READS THE TABLE ENTRY AND NOT THE HEADER, AGAINST THIS MODULE'S
    OWN RULE. The entry is unbranched, so `foundry_next_action` measures no
    liveness for it and the formatter resolves `{wave}` and `{built_wave}`
    through their totals' default -- `1`. The header a lead receives is
    therefore byte-identical with or without the slot, which is exactly why
    the whole suite, including `test_the_wave_slots_resolve_for_every_input`
    and `test_the_audit_sees_a_team_name_the_lead_cannot_create` above, stayed
    green over the forbidden edit: a resolvable slot is what they check for.
    The only place the edit is visible is the template, so the template is what
    this judges.
    """
    entry = _ACTION_IMPERATIVES["transition_to_cast"]
    # Steps (4), (5) and (6): the three places the first wave is named.
    literal_calls = (
        "TeamCreate('cast-{run}-wave-1')",
        "Foundry-Team-Up(team_name='cast-{run}-wave-1')",
        "Foundry-Cast-Wave(wave=1, ",
    )
    # Derived from the one tuple of slots the formatter resolves, so a wave
    # slot added later is forbidden here the day it lands.
    wave_slots = tuple(s for s in _SUBSTITUTED_SLOTS if "wave" in s)
    assert {"{wave}", "{built_wave}"} <= set(wave_slots), wave_slots

    def findings(text: str) -> list[str]:
        return (
            [f"missing {call}" for call in literal_calls if call not in text]
            + [f"holds {slot}" for slot in wave_slots if slot in text]
        )

    assert findings(entry) == [], findings(entry)

    # Positive control: the edit the comment forbids, once per wave slot, is
    # seen by the check above. A check that could not see it would pass a
    # table that had made it.
    for slot in ("{wave}", "{built_wave}"):
        mutated = entry.replace("cast-{run}-wave-1'", "cast-{run}-wave-" + slot + "'")
        assert mutated.count(slot) == 2, (slot, mutated)
        assert f"holds {slot}" in findings(mutated), (slot, findings(mutated))


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

    # lead-stalls D-024 — the third branched action (D-019), whose CONTEXT no
    # sweep read.
    _write_spec(fdir, ["FR-1"])
    _write_state(fdir, phase="F2", cycle=1)
    _record_full_inspect_mode(fdir, cycle=1)
    result = _compute_next_action(project_root)
    assert result["action"] == "run_streams", result
    blocks["run_streams"] = result["instructions"]

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
    assert "CONTEXT:run_streams" in surfaces
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
    assert set(blocks) == {"build_castings", "run_streams", "fix_defects"}, blocks

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
    # and the per-defect bookkeeping door's argument shape — stated as a fact
    # about the door, never written out as a call (lead-stalls D-051..D-053).
    assert "blocking defect(s) to fix" in blocks["fix_defects"]
    assert (
        "Foundry-Fix door, whose required arguments are defect_id, cycle and "
        "authored_by"
    ) in blocks["fix_defects"]
    assert "Foundry-Fix(" not in blocks["fix_defects"]
    # lead-stalls D-024 — the F2 block names no spawn: the header above it
    # says whether the unrecorded streams are running or owed a dispatch.
    streams = blocks["run_streams"]
    assert "spawn" not in streams.lower(), streams
    assert "Agent(" not in streams, streams
    assert "Missing:" not in streams, streams
    assert "Required this cycle: " in streams, streams


def test_the_payload_states_one_sequence_for_the_f1_crossing(run_env):
    """lead-stalls D-005, driven end to end through the door the lead reads.

    The header and the CONTEXT ship concatenated in `result["instructions"]`,
    so "two sequences for one crossing" is a property of the WHOLE payload and
    not of either half. With the wave complete, exactly one surface names the
    F1 -> F2 crossing, and it is the one that names the gate that guards it.
    """
    project_root, fdir = run_env
    _write_state(fdir, phase="F1", cycle=0)
    # lead-stalls D-013 — "the wave is done" is a claim about the MANIFEST and
    # the ledgers, so the run below states both. A stalled ledger with no wave
    # manifest is a roster that finished and a position that was never measured,
    # and that reading is now owed the dispatch branch, not this crossing.
    _wave_manifest(fdir, {1: ["3"]})
    _ledger(fdir, "casting-3", done=True)   # the wave is done, and measurably
    _verdict(fdir, "3", "accepted")         # and accepted (lead-stalls D-020)

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
    # lead-stalls D-038 — every list that spawns an agent now ends in the
    # yield, so the dispatch sequences carry it too.
    assert sorted(carriers) == [
        "add_castings", "build_castings", "fix_defects", "run_streams",
        "transition_to_cast", "transition_to_grind",
    ], carriers
    # No imperative says it in its own words: the denials that make the policy
    # a policy appear exactly where the constant put them — once per branch
    # that yields (lead-stalls D-020 gave `build_castings` a second one, and
    # D-022's split a third).
    for action, text in sorted(_ACTION_IMPERATIVES.items()):
        expected = text.count(_WAITING_IS_NOT_STOPPING)
        assert text.count("Do NOT sleep") == expected, action
        assert text.count("do NOT poll") == expected, action
    # live, undispatched, refused and redispatch.
    assert _ACTION_IMPERATIVES["build_castings"].count(_WAITING_IS_NOT_STOPPING) == 4


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
    # The NOTICE is the accusation and nothing softer. The header beneath it
    # is the dispatch, whose last step is the yield after the spawn
    # (lead-stalls D-038), so the policy is judged on the notice alone.
    block, header = _split_payload(nxt["instructions"])
    notice = block[: len(block) - len(header)]
    assert "STALL DETECTED" in notice, block
    assert _WAITING_IS_NOT_STOPPING not in notice, notice
    assert _chosen_branch("build_castings", nxt["agent_liveness"]) == "undispatched"


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


# --------------------------------------------------------------------------- #
# lead-stalls D-015 / D-016 / D-017 / D-019 / D-020 — router-level regressions
# --------------------------------------------------------------------------- #
#
# Each drives `foundry_next_action` on a run whose team was registered by the
# REAL `Foundry-Team-Up` (see `_router_run`), because the defects these pin
# were invisible to every test that answered the team question with a fake.

_ROUTE_PROMPT = (
    "# casting\n\n<spec_requirements>\n- build the thing\n</spec_requirements>\n"
)


def _drive(tmp_path, arrange, clock=None) -> dict:
    """`drive_router` inside pytest's own scratch directory."""
    with _router_run(tmp_path) as (root, fdir, teams):
        arrange(root, fdir, teams)
        if clock is not None:
            _stale_stall_clock(fdir, clock)
        nxt = foundry_next_action(root)
        cycle = foundry_state.current_cycle(fdir)
    block, header = _split_payload(nxt.get("instructions", ""))
    action = nxt.get("action", "")
    liveness = nxt.get("agent_liveness")
    branch = (
        _emitted_branch(action, header, _ROUTE_RUN, liveness, cycle,
                        nxt.get("details") or {})
        if _parse_branches(_ACTION_IMPERATIVES.get(action, "")) else None
    )
    return {"nxt": nxt, "action": action, "branch": branch, "block": block,
            "header": header, "liveness": liveness,
            "context": _context_of(nxt.get("instructions", ""))}


def _accept(root: str, fdir: Path, cid: str, **overrides) -> dict:
    """The REAL `Foundry-Accept-Casting`, honest unless overridden. A v2.0 spec
    takes the evidence rung's stream-skip branch, so no git tree is needed and
    the verdict is reached through the door itself."""
    (fdir / "spec.md").write_text("# Spec\n\n- build the thing\n", encoding="utf-8")
    prompt = fdir / "castings" / f"casting-{cid}-prompt.md"
    prompt.write_text(_ROUTE_PROMPT, encoding="utf-8")
    args = {
        "casting_id": cid,
        "spec_hash": foundry_spec_hash(project_root=root)["spec_hash"],
        "prompt_hash": _hash_file(prompt),
        "completion_report": "built the thing",
        "project_root": root,
        "casting_commit": "0" * 40,
    }
    args.update(overrides)
    return foundry_accept_casting(**args)


@pytest.mark.parametrize("clock", [None, 600], ids=["fresh", "stale"])
@pytest.mark.parametrize(
    "arrange", [_arrange_cast_live, _arrange_cast_live_owed],
    ids=["casting-1-accepted", "casting-1-owed"],
)
def test_a_registered_team_leaves_teammates_that_are_still_building_alone(
    tmp_path, arrange, clock
):
    """lead-stalls FR-002 / CT-006 / ST-002 — D-015 and D-016, as filed.

    Wave 1 = castings 1 and 2, a real `Foundry-Team-Up`, casting 1 done,
    casting 2 still writing. The router answered `cleanup_teams` — "send
    shutdown to each teammate ... TeamDelete ... Foundry-Team-Down" — ahead of
    the F1 arm, so the teammate still building was ordered stopped and the
    payload carried no `agent_liveness` (the read was taken only for branched
    actions, and `cleanup_teams` returned first). The same run with no team
    registered got the live branch, which is the only state any test drove.

    Casting 1 is driven both accepted and not: a woken lead is owed END YOUR
    TURN while casting 2 runs either way, and the acceptance is named by the
    reading the LAST notification brings.
    """
    d = _drive(tmp_path, arrange, clock)

    assert d["action"] == "build_castings", d["action"]
    assert d["branch"] == "live", d["header"]
    assert "END YOUR TURN" in d["header"], d["header"]
    for word in _TEARDOWN_WORDS:
        assert word not in d["block"], (word, d["block"])
    # lead-stalls CT-006: the status the lead actually receives, from the reading the
    # router routed on, and it names the registered team as registered.
    assert d["liveness"]["waiting"] is True, d["liveness"]
    assert d["liveness"]["count"] == 1, d["liveness"]
    assert d["liveness"]["teams_active"] is True, d["liveness"]
    # lead-stalls D-037 — and the live-agents return publishes the registry.
    assert d["liveness"]["teams_registered"] == [_CAST_TEAM], d["liveness"]
    if clock:
        assert "WAITING ON 1 AGENT" in d["block"], d["block"]
        assert d["nxt"]["waiting_on_agents"] == d["liveness"]


def test_team_up_with_nothing_spawned_is_handed_the_dispatch(tmp_path):
    """lead-stalls FR-015 / D-015 — the D-003 fix, reachable through the door.

    `transition_to_cast` steps (3)-(4) made — TeamCreate and a real Team-Up —
    and no Agent spawned yet. The selector already routed this reading to the
    dispatch branch, but the router tore the team down before the selector was
    asked.
    """
    d = _drive(tmp_path, _arrange_cast_not_spawned)

    assert (d["action"], d["branch"]) == ("build_castings", "undispatched"), d
    assert f"Foundry-Team-Up(team_name='{_CAST_TEAM}')" in d["header"]
    assert "Foundry-Cast-Wave(wave=1, phase='cast')" in d["header"]
    for word in _TEARDOWN_WORDS:
        assert word not in d["block"], word
    assert d["liveness"]["teams_active"] is True


@pytest.mark.parametrize("clock", [None, 600], ids=["fresh", "stale"])
@pytest.mark.parametrize(
    "arrange", [_arrange_grind_live, _arrange_grind_live_all_fixed],
    ids=["defect-open", "last-fix-recorded"],
)
def test_a_registered_grind_team_leaves_teammates_that_are_still_fixing_alone(
    tmp_path, arrange, clock
):
    """lead-stalls CT-003 — D-017, and the window beside it.

    F3, a real GRIND team registered, a teammate's ledger advancing. With a
    LIVE defect open the router answered `cleanup_teams`. With the count at
    zero — the teammate has recorded its last `Foundry-Fix` and is still
    writing its report and done line — it answered `transition_to_inspect`,
    whose CONTEXT opens "Shut down grind team". Both are a teardown over a
    running teammate; the branched answer is owed until nobody runs.
    """
    d = _drive(tmp_path, arrange, clock)

    assert (d["action"], d["branch"]) == ("fix_defects", "live"), d["header"]
    assert "YOUR NEXT CALL: NONE. Your GRIND teammates are running" in d["header"]
    assert d["liveness"]["waiting"] is True
    for word in _TEARDOWN_WORDS:
        assert word not in d["block"], word
    context = d["nxt"]["instructions"].split("\nCONTEXT:", 1)[1]
    assert "Shut down grind team" not in context, context


@pytest.mark.parametrize(
    "arrange",
    [
        _arrange_cast_boundary_team_up,
        _arrange_cast_built_team_up,
        _arrange_grind_finished_team_up,
        _arrange_inspect_stale_team,
    ],
    ids=["wave-1-team-at-wave-2", "wave-built", "grind-finished", "f2-stale-team"],
)
def test_a_finished_or_stale_team_is_still_cleaned_up(tmp_path, arrange):
    """The adjacent path the gate must not close: `cleanup_teams` is RESERVED,
    not retired. A team whose wave is accepted, a GRIND team nobody is running
    under, and a team still standing at F2 are finished or stale, and the lead
    is still told to take them down — the next wave's Team-Up refuses while a
    previous wave's team stands."""
    d = _drive(tmp_path, arrange)

    assert d["action"] == "cleanup_teams", d["action"]
    assert "TeamDelete" in d["header"], d["header"]


@pytest.mark.parametrize("team_up", [True, False], ids=["team-up", "team-down"])
def test_a_refused_casting_goes_back_to_its_teammate_not_to_the_teardown(
    tmp_path, team_up
):
    """lead-stalls ST-004 / US-003 — D-020, through the real acceptance door.

    Casting 1 done, `Foundry-Accept-Casting` WARNED (ok False, "Do NOT accept
    this casting"), and the lead follows the payload's `next_call` —
    "Call Foundry-Next now." At c5045c2 that answered `cleanup_teams` with the
    team up and the wave-complete teardown with it down; neither mentioned
    Accept-Casting. The router now reads the verdict the door recorded.
    """
    with _router_run(tmp_path) as (root, fdir, teams):
        _cast(fdir, {1: ["1"], 2: ["2"]})
        if team_up:
            _register(root, teams, _CAST_TEAM)
        _worked(fdir, "1", done=True,
                at=datetime.now(timezone.utc) - timedelta(minutes=1))
        refused = _accept(root, fdir, "1",
                          completion_report="built it; the tests are deferred")
        nxt = foundry_next_action(root)
    _block, header = _split_payload(nxt["instructions"])

    assert refused["ok"] is False, refused
    assert nxt["action"] == "build_castings", nxt["action"]
    assert nxt["agent_liveness"]["cast_refused"] == ["1"], nxt["agent_liveness"]
    assert nxt["agent_liveness"]["teams_active"] is team_up, nxt["agent_liveness"]
    # The refusal travels as a message — no prompt is augmented, which the
    # standing rules above it forbid for a CAST dispatch — and the turn then
    # ends on the yield its answer wakes the lead from.
    assert "Foundry-Accept-Casting" in header and "casting 1" in header, header
    assert _WAITING_IS_NOT_STOPPING in header, header
    assert header.index("SendMessage(to=") < header.index("END YOUR TURN"), header
    # lead-stalls D-022 — ONE of the two, chosen by the server from the team
    # scan on the same reading: never both, and no step that waits on reading
    # what an earlier step answered.
    assert not _HANDS_OVER_THE_CONDITION.search(header), header
    if team_up:
        # Back to the teammate that built it (lead ruling, GRIND cycle 7).
        assert header.startswith(
            "YOUR NEXT CALLS (in order):\n  (1) SendMessage(to=<the teammate "
            "you spawned for casting 1>"
        ), header
        assert "(2) " + _WAITING_IS_NOT_STOPPING in header, header
        assert "casting 1's own wave team is still registered" in header, header
        assert "Foundry-Spawn-Teammate" not in header, header
    else:
        # Its team is gone, so a fresh dispatch passed verbatim, and the
        # refusal as a SEPARATE message to the new teammate.
        assert header.startswith(
            "YOUR NEXT CALLS (in order):\n"
            "  (1) Foundry-Spawn-Teammate(casting_id=1, phase='cast')\n"
        ), header
        # lead-stalls D-030 — and the ledger protocol under it, LAST, in
        # commands/start.md rule 1's order: the done line that block makes the
        # teammate write is what routes the lead back to acceptance.
        assert "VERBATIM with nothing appended" not in header, header
        # lead-stalls D-039 — in the CAST order the rule above declares.
        assert (
            "(a) the returned `dispatch` field VERBATIM" in header
            and "(b) LAST, the returned `progress_protocol` block VERBATIM" in header
            and "Order: dispatch → progress_protocol." in header
        ), header
        assert "the refusal never rides inside the dispatch" in header, header
        assert "(3) SendMessage(to=<the teammate step (2) spawned>" in header
        assert "(4) " + _WAITING_IS_NOT_STOPPING in header, header
        assert "casting 1's own wave team is not registered" in header, header
        assert "<the teammate you spawned for casting 1>" not in header, header
        assert header.index("Foundry-Spawn-Teammate") < header.index(
            "SendMessage(to="
        ), header
    for forbidden in (*_TEARDOWN_WORDS, "Foundry-Cast-Wave(wave=2",
                      "Foundry-Gate(phase='inspect')", "APPEND",
                      "cannot be reached", "expected to reach"):
        assert forbidden not in header, (forbidden, header)


@pytest.mark.parametrize("team_up", [True, False], ids=["team-up", "team-down"])
def test_a_refused_call_is_answered_by_accepting_again(tmp_path, team_up):
    """lead-stalls ST-004 — D-020's own drive: a STALE SPEC HASH.

    That rung refuses the CALL and records nothing, so the casting reads as
    done and never judged. What it owes is the acceptance made correctly —
    Spec-Hash, then Accept-Casting — and not a re-dispatch of work nobody has
    judged, nor the teardown it was handed.
    """
    with _router_run(tmp_path) as (root, fdir, teams):
        _cast(fdir, {1: ["1"], 2: ["2"]})
        if team_up:
            _register(root, teams, _CAST_TEAM)
        _worked(fdir, "1", done=True)
        refused = _accept(root, fdir, "1", spec_hash="0" * 64)
        nxt = foundry_next_action(root)
    _block, header = _split_payload(nxt["instructions"])

    assert refused["error"] == "stale_spec_hash", refused
    assert nxt["action"] == "build_castings", nxt["action"]
    assert header.startswith("YOUR NEXT CALLS (in order):\n  (1) Foundry-Spec-Hash")
    assert "Foundry-Accept-Casting(casting_id=1, " in header, header
    for forbidden in (*_TEARDOWN_WORDS, "Foundry-Cast-Wave(wave=2",
                      "Foundry-Spawn-Teammate"):
        assert forbidden not in header, (forbidden, header)


def test_a_refusal_the_teammate_has_answered_is_accepted_again(tmp_path):
    """lead-stalls ST-004 — "casting rejected -> lead re-accepting".

    Once the re-dispatched teammate declares itself done again, the refusal is
    older than its done line and the casting is owed the acceptance, not a
    second re-dispatch — otherwise the refused branch would loop.
    """
    d = _drive(tmp_path, _arrange_cast_refusal_answered)

    assert (d["action"], d["branch"]) == ("build_castings", "unaccepted"), d
    assert d["liveness"]["cast_refused"] == [], d["liveness"]
    assert d["liveness"]["cast_unaccepted"] == ["1"], d["liveness"]


@pytest.mark.parametrize("team_up", [True, False], ids=["team-up", "team-down"])
def test_an_accepted_casting_lets_the_wave_move_on(tmp_path, team_up):
    """The adjacent path the reader must not block: casting 1 ACCEPTED through
    the real door. With the wave-1 team up it is torn down; with it down, wave
    2 is dispatched. A reader that treated a recorded acceptance as owed would
    park the run at exactly the wave boundary lead-stalls US-001 is about."""
    with _router_run(tmp_path) as (root, fdir, teams):
        _cast(fdir, {1: ["1"], 2: ["2"]})
        if team_up:
            _register(root, teams, _CAST_TEAM)
        _worked(fdir, "1", done=True)
        assert _accept(root, fdir, "1")["ok"] is True
        nxt = foundry_next_action(root)
    _block, header = _split_payload(nxt["instructions"])

    if team_up:
        assert nxt["action"] == "cleanup_teams", nxt["action"]
    else:
        assert nxt["action"] == "build_castings", nxt["action"]
        assert "Foundry-Cast-Wave(wave=2, phase='cast')" in header, header


def test_the_reader_reads_the_verdict_the_acceptance_door_writes(tmp_path):
    """lead-stalls D-020 — ONE SPELLING, PINNED FROM THIS SIDE TOO.

    `evidence.py` (a verifier) writes the verdict and this module (lifecycle)
    reads it; neither may import the other's spelling. So the pin is a drive:
    the real door writes, and this reader must find exactly the destination it
    builds from the manifest id.
    """
    with _router_run(tmp_path) as (root, fdir, _teams):
        _cast(fdir, {1: ["1"]})
        assert _accept(root, fdir, "1")["ok"] is True
        after_accept = _acceptance_verdicts(fdir)
        assert _accept(root, fdir, "1",
                       completion_report="partial coverage only")["ok"] is False
        after_refuse = _acceptance_verdicts(fdir)

    assert _ACCEPTANCE_VERDICTS == ("accepted", "refused")
    assert set(after_accept) == {_acceptance_destination("1", "accepted")}
    assert set(after_refuse) == {
        _acceptance_destination("1", "accepted"),
        _acceptance_destination("1", "refused"),
    }
    # File order is what makes the refusal the LATEST verdict.
    assert (
        after_refuse[_acceptance_destination("1", "refused")][0]
        > after_refuse[_acceptance_destination("1", "accepted")][0]
    )
    records = [
        json.loads(line)
        for line in (fdir / "handoffs.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert {r["event"] for r in records} >= {_ACCEPTANCE_EVENT}


def test_the_acceptance_reader_orders_a_refusal_against_the_done_line(tmp_path):
    """`_owed_acceptances` over every ordering it has to decide."""
    from foundry_mcp.tools.orchestration.guidance import _owed_acceptances

    fdir = tmp_path
    now = datetime.now(timezone.utc)
    early, late = (now - timedelta(minutes=5)).isoformat(), now.isoformat()
    castings = [(c, f"casting-{c}") for c in ("a", "b", "c", "d", "e", "f", "g")]
    _verdict(fdir, "a", "refused", at=late)          # done early: untouched since
    _verdict(fdir, "b", "refused", at=early)         # done late: answered
    _verdict(fdir, "c", "refused", at="not a time")  # cannot be ordered
    _verdict(fdir, "d", "refused", at=early)
    _verdict(fdir, "d", "accepted", at=late)         # accepted last: built
    _verdict(fdir, "e", "accepted", at=early)
    _verdict(fdir, "e", "refused", at=late)          # refused last: refused
    # f: done, no verdict.  g: no verdict and not done — not owed at all.
    done_at = {"casting-a": early, "casting-b": late, "casting-c": early,
               "casting-d": early, "casting-e": early, "casting-f": early}

    owed = _owed_acceptances(fdir, castings, done_at)

    assert owed == {
        "cast_refused": ["a", "e"],
        "cast_unaccepted": ["b", "c", "f"],
    }, owed

    # A ledger that cannot be decoded is not "nothing accepted": the keys are
    # absent, so no acceptance branch can be chosen off a failed read.
    (fdir / "handoffs.jsonl").write_bytes(b"\xff\xfe\x00 not utf-8\n")
    assert _owed_acceptances(fdir, castings, done_at) == {}


def test_the_casting_slot_and_the_acceptance_state_are_total():
    """`{casting}` resolves for every reading, and a malformed list selects no
    acceptance branch — the `_halt_cause` side of the line
    (lead-stalls GI-008)."""
    hostile = (
        None, {}, "x", [], {"cast_refused": None}, {"cast_refused": [True]},
        {"cast_refused": [None, ""]}, {"cast_unaccepted": "1"},
        {"cast_refused": [], "cast_unaccepted": [0]},
        {"waiting": True, "cast_refused": ["2"]},
    )
    for reading in hostile:
        slot = _casting_slot(reading)
        assert slot and "{" not in slot, reading
        for action in ("build_castings", "fix_defects", "run_streams"):
            text = _format_imperative_header(
                action, "", {}, run_name="r", phase="F1", liveness=reading,
            )
            assert "{casting}" not in text, (action, reading)
            assert "Execute the first tool call mentioned" not in text, reading
    assert _casting_slot({"cast_unaccepted": [0]}) == "0"
    # lead-stalls D-022 / D-031 — only the refused casting's OWN team, among
    # the MEASURED registered names, sends the refusal back; anything else owes
    # the fresh dispatch, and `teams_active` decides nothing.
    own = "cast-r-wave-1"
    for own_team, registered in (
        (own, "yes"), (own, None), (own, [True]), (own, own), (None, [own]),
        (1, [1]), (own, []), (own, ["cast-r-wave-2"]),
    ):
        reading = {"cast_refused": ["1"], "teams_active": True,
                   "cast_refused_team": own_team, "teams_registered": registered}
        assert _acceptance_state(reading) == "redispatch", reading
    assert _acceptance_state({"cast_refused": ["1"]}) == "redispatch"
    assert _acceptance_state({"cast_refused": ["1"], "teams_active": True}) == "redispatch"
    assert _acceptance_state(
        {"cast_refused": ["1"], "teams_active": False,
         "cast_refused_team": own, "teams_registered": ["x", own]}
    ) == "refused"
    assert _acceptance_state(
        {"cast_refused": [], "cast_unaccepted": ["1"],
         "cast_refused_team": own, "teams_registered": [own]}
    ) == "unaccepted"
    assert _branch_states({"waiting": False, "cast_refused": [True]}) == (
        "undispatched",
    )
    # Live outranks both acceptance states.
    assert _branch_states(
        {"waiting": True, "cast_refused": ["2"], "cast_unaccepted": ["1"]}
    ) == ("live",)


def test_the_notice_and_the_header_are_one_selection():
    """lead-stalls GI-008 / D-019 — the stall notice asks `_chosen_branch`, so
    it must name the branch `_format_imperative_header` actually prints, on
    every reading the sweep varies."""
    for _label, reading, _owed in _LIVENESS_READINGS:
        for action in sorted(_ACTION_IMPERATIVES):
            chosen = _chosen_branch(action, reading)
            text = _format_imperative_header(
                action, "", {}, run_name=_AUDIT_RUN, phase="F1", liveness=reading,
            )
            if not _parse_branches(_ACTION_IMPERATIVES[action]):
                assert chosen is None, action
                continue
            assert chosen == _emitted_branch(
                action, text, _AUDIT_RUN, reading
            ), (action, reading)


@pytest.mark.parametrize(
    "arrange, expect_action",
    [
        (_arrange_inspect_stale_team, "cleanup_teams"),
        (lambda root, fdir, teams: (
            _write_spec(fdir, ["FR-1"]),
            _write_state(fdir, phase="F2", cycle=1),
            _write_manifest_with_castings(fdir, ["src/api/a.py"], no_ui=True),
            _progressing_ledger(fdir, agent="prove"),
        ), "record_inspect_width"),
    ],
    ids=["cleanup-over-stale-team", "unrecorded-width"],
)
def test_the_waiting_notice_names_no_move_above_a_header_that_does(
    tmp_path, arrange, expect_action
):
    """lead-stalls GI-008 — D-019 (1), on the actions that do not choose from
    the roster. Agents progressing, the stall clock ten minutes old: the notice
    still refuses to call the gap deliberation, and it no longer tells the lead
    to END ITS TURN above a header that names calls — it reports, and hands the
    move to the header."""
    d = _drive(tmp_path, arrange, 600)

    assert d["action"] == expect_action, d["action"]
    assert d["block"].startswith("⏳ WAITING ON"), d["block"]
    assert "END YOUR TURN" not in d["block"], d["block"]
    assert _WAITING_REPORTS_ONLY in d["block"], d["block"]
    assert "silently deliberating" not in d["block"]
    assert not _NAMES_NO_CALL.search(d["header"]), d["header"]


def test_running_streams_are_not_spawned_again(tmp_path):
    """lead-stalls GI-008 / FR-007 — D-019 (2), as filed.

    F2, FULL width recorded, `prove` and `trace` progressing and not yet
    recorded, the stall clock ten minutes old. One payload carried the WAITING
    notice listing both and, under it, "spawn every missing INSPECT stream in a
    SINGLE parallel message" beside a CONTEXT naming them as missing.
    """
    d = _drive(tmp_path, _arrange_inspect_live, 600)

    assert (d["action"], d["branch"]) == ("run_streams", "live"), d["header"]
    assert "WAITING ON 2 AGENT" in d["block"], d["block"]
    assert "END YOUR TURN" in d["header"], d["header"]
    assert "spawn every missing INSPECT stream" not in d["block"], d["block"]
    assert d["liveness"]["count"] == 2, d["liveness"]


def test_the_payload_sweep_bites_when_the_team_arm_preempts_again(monkeypatch):
    """Positive control for the payload sweep (lead-stalls D-018): put back the
    unconditional team arm and the sweep must name the states it misroutes and
    the teardown it prints over running teammates."""
    import foundry_mcp.tools.orchestration.guidance as _g

    monkeypatch.setattr(_g, "_team_work_in_flight", lambda *a, **k: False)
    findings = audit_assembled_payloads()

    misrouted = {f.split(":", 2)[1] for f in findings if "WRONG_ROUTE" in f}
    for state in ("cast-live", "cast-live-owed", "cast-not-spawned",
                  "cast-unaccepted", "cast-refused", "cast-unmeasured-team-up",
                  "grind-live", "grind-live-all-fixed"):
        assert f"{state}[fresh]" in misrouted, (state, sorted(misrouted))
    assert any(
        "TEARDOWN_OVER_RUNNING_AGENTS" in f and "cast-live-owed" in f
        for f in findings
    ), findings


def test_the_payload_sweep_bites_when_the_notice_ignores_the_header(monkeypatch):
    """Positive control for CONTRADICTION (lead-stalls D-019): a notice that
    appends the wait policy whatever the header says must be seen."""
    import foundry_mcp.tools.orchestration.guidance as _g

    monkeypatch.setattr(_g, "_chosen_branch", lambda *a, **k: "live")
    findings = [f for f in audit_assembled_payloads() if "CONTRADICTION" in f]

    assert {f.split(":", 2)[1] for f in findings} == {"inspect-stale-team[stale]"}, findings


# --------------------------------------------------------------------------- #
# lead-stalls GRIND cycle 8 — D-021 .. D-027
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("clock", [None, 600], ids=["fresh", "stale"])
def test_a_registered_team_over_an_unmeasured_wave_is_left_up(tmp_path, clock):
    """lead-stalls D-021 — the D-013 rung of `_team_work_in_flight`, driven.

    F1, a real `Foundry-Team-Up`, a casting's done line, and a manifest that
    does not answer (`waves: []`), so the reading carries no wave position and
    the branch is `undispatched`. "When unsure, keep the team up": a team torn
    down over a wave nobody measured may be tearing down work. With the rung's
    `return True` flipped this answered `cleanup_teams`, and no test drove it.
    """
    d = _drive(tmp_path, _arrange_cast_unmeasured_team_up, clock)

    assert d["liveness"]["teams_active"] is True, d["liveness"]
    assert d["liveness"].get("cast_wave_pending") is None, d["liveness"]
    assert d["action"] != "cleanup_teams", d["header"]
    assert (d["action"], d["branch"]) == ("build_castings", "undispatched"), d
    for word in _TEARDOWN_WORDS:
        assert word not in d["block"], (word, d["block"])


def test_the_in_flight_rung_for_an_unmeasured_position_keeps_the_team():
    """lead-stalls D-021 — the same rung, on every shape of absent position."""
    from foundry_mcp.tools.orchestration.guidance import _team_work_in_flight

    team = [f"cast-{_ROUTE_RUN}-wave-1"]
    checked = 0
    for pending in (None, True, False, "1", 0.5, 0):
        reading = {"waiting": False, "teams_active": True, "roster_agents": 1}
        if pending is not None:
            reading["cast_wave_pending"] = pending
        if _branch_state(reading) != "undispatched":
            continue          # 0 is a measured "every wave done": `idle`
        checked += 1
        for names in ([], team):
            assert _team_work_in_flight(
                reading, names, _ROUTE_RUN, cast_open=True
            ) is True, (pending, names)
    assert checked == 5, checked


@pytest.mark.parametrize("clock", [None, 600], ids=["fresh", "stale"])
def test_running_streams_get_a_context_that_spawns_nothing(tmp_path, clock):
    """lead-stalls GI-008 — D-024, as filed.

    F2, FULL width, `prove` and `trace` progressing, no stream recorded. The
    header said END YOUR TURN and "spawning it again runs it twice"; the
    CONTEXT directly beneath it said "Missing: trace prove test ... Spawn
    agents using the agent_configs below". Both clocks, because the filing
    saw it at both.
    """
    d = _drive(tmp_path, _arrange_inspect_live, clock)

    assert (d["action"], d["branch"]) == ("run_streams", "live"), d["header"]
    assert "END YOUR TURN" in d["header"], d["header"]
    context = d["context"]
    assert context.strip(), d["nxt"]["instructions"]
    assert _BRANCHED_ACTION_CONTEXT in context, context
    assert "spawn" not in context.lower(), context
    assert "Missing:" not in context, context
    assert _context_orders(context) == [], context
    # What the header cannot carry is still here: the counts, the enforced
    # configs, and the two standing rules (fallout D-165 / D-169).
    assert "Required this cycle: " in context, context
    assert d["nxt"]["details"]["agent_configs"], d["nxt"]["details"]
    assert "never record on an AGENT's behalf" in context, context
    assert "pin its findings to the HEAD sha" in context, context


def test_the_payload_sweep_bites_when_a_context_stops_deferring(monkeypatch):
    """Positive control for CONTEXT_SEQUENCE (lead-stalls D-024): a branched
    CONTEXT that stops deferring to the header must be named for every branched
    router state."""
    import foundry_mcp.tools.orchestration.guidance as _g

    monkeypatch.setattr(_g, "_BRANCHED_ACTION_CONTEXT", " Do as the CONTEXT says.")
    findings = [f for f in audit_assembled_payloads() if "CONTEXT_SEQUENCE" in f]

    named = {f.split(":", 2)[1].split("[")[0] for f in findings}
    branched = {state for state, _t, _a, _action, branch in _ROUTER_STATES if branch}
    assert named == branched, (sorted(named), sorted(branched))


#: lead-stalls D-029 — the two acdc00b CONTEXT sentences the word list let
#: back in, quoted as they shipped.
_ACDC00B_SIGHT = (
    "SIGHT runs in MAIN THREAD (Playwright MCP only works here) — navigate to "
    "URL, snapshot every page, exercise all elements, check console."
)
_ACDC00B_PIN = (
    "TEST rewrites the shared tree to verify the GRIND's fixes, so tell every "
    "reading stream to pin its findings to the HEAD sha and verify them "
    "against a snapshot, not the live tree."
)


def test_the_context_check_reads_the_structure_of_an_order():
    """lead-stalls D-029 — positive and negative controls for `_order_openers`.

    The word list returned nothing for either acdc00b sentence. The structural
    check must name both, and the older moves the list held, while every
    statement the shipped CONTEXTs carry — a prohibition, a fact about a door
    named mid-sentence, a participle, a phase name — stays clean.
    """
    for order, opens in (
        (_ACDC00B_SIGHT, "navigate to URL, snapshot"),
        (_ACDC00B_PIN, "tell every reading stream"),
        ("Missing: trace prove test. Spawn agents using the agent_configs "
         "below.", "Spawn agents using the"),
        ("The wave is built. Then TeamDelete + Foundry-Team-Down + "
         "Foundry-Phase(phase='cast').", "TeamDelete + Foundry-Team-Down +"),
        ("Two streams are unrecorded, so spawn every missing INSPECT stream.",
         "spawn every missing INSPECT"),
        ("Streams run in the background; pin each finding to the HEAD sha.",
         "pin each finding to"),
    ):
        assert opens in _order_openers(order), (order, _order_openers(order))

    for statement in (
        "Each AGENT stream records its OWN run with Foundry-Stream (fallout "
        "GI-016); never record on an AGENT's behalf.",
        "SIGHT is the exception and the reason is that you EXECUTED it: there "
        "is no sight agent, so its record is yours to make from what you "
        "measured.",
        "TEST rewrites the shared tree to verify the GRIND's fixes, so every "
        "reading stream is bound to pin its findings to the HEAD sha.",
        "Required this cycle: trace, prove, test.",
        "GRIND phase: 1 blocking defect(s) to fix (1 LIVE, 0 untiered). After "
        "each fix call Foundry-Fix(defect_id, cycle, authored_by, ...): "
        "authored_by is 'teammate' or 'lead'.",
        _BRANCHED_ACTION_CONTEXT,
    ):
        assert _order_openers(statement) == [], statement


@pytest.mark.parametrize("sentence", [_ACDC00B_SIGHT, _ACDC00B_PIN],
                         ids=["sight-main-thread", "tell-every-stream"])
def test_the_payload_sweep_bites_on_the_acdc00b_context_sentences(
    monkeypatch, sentence
):
    """lead-stalls D-029 — the mutations TEST drove, through the payload sweep.

    Either sentence back in a branched CONTEXT, under a header that says END
    YOUR TURN, must be a CONTEXT_SEQUENCE finding on every branched router
    state that prints it. The deferral sentence is kept, so the finding is
    the order itself and not a missing deferral.
    """
    import foundry_mcp.tools.orchestration.guidance as _g

    monkeypatch.setattr(
        _g, "_BRANCHED_ACTION_CONTEXT",
        " " + sentence + _BRANCHED_ACTION_CONTEXT,
    )
    findings = [f for f in audit_assembled_payloads() if "CONTEXT_SEQUENCE" in f]

    named = {f.split(":", 2)[1].split("[")[0] for f in findings}
    branched = {state for state, _t, _a, _action, branch in _ROUTER_STATES if branch}
    assert named == branched, (sorted(named), sorted(branched))
    assert all("no deferral" not in f for f in findings), findings


def test_a_refusal_stamped_at_the_done_line_is_still_unanswered(tmp_path):
    """lead-stalls D-025 — `_owed_acceptances`' `>=`, at its boundary.

    The teammate's done line is written BEFORE the call that refuses it and
    may carry only whole seconds, so a refusal stamped at the SAME instant is
    a refusal nobody has answered. With `>` it read as `cast_unaccepted`, and
    the lead was routed to re-accept an unchanged casting — the outcome the
    refused branch forbids.
    """
    from foundry_mcp.tools.orchestration.guidance import _owed_acceptances

    instant = datetime.now(timezone.utc).replace(microsecond=0)
    whole = instant.isoformat()                          # ...T12:00:00+00:00
    zulu = instant.strftime("%Y-%m-%dT%H:%M:%SZ")        # same instant, Z form
    _verdict(tmp_path, "a", "refused", at=whole)
    _verdict(tmp_path, "b", "refused", at=zulu)
    _verdict(tmp_path, "c", "refused", at=whole)
    castings = [("a", "casting-a"), ("b", "casting-b"), ("c", "casting-c")]
    done_at = {
        "casting-a": whole,
        "casting-b": whole,
        # One second later: the teammate answered the refusal.
        "casting-c": (instant + timedelta(seconds=1)).isoformat(),
    }

    owed = _owed_acceptances(tmp_path, castings, done_at)

    assert owed == {"cast_refused": ["a", "b"], "cast_unaccepted": ["c"]}, owed


@pytest.mark.parametrize("key, branch", [
    ("cast_refused", "refused"),
    ("cast_unaccepted", "unaccepted"),
])
def test_the_casting_slot_names_the_first_owed_casting(key, branch):
    """lead-stalls D-026 — `{casting}` names the FIRST id when several are
    owed, because `_cast_wave_position` lists them in manifest wave order and
    the lowest wave is settled first. Every earlier drive owed one id."""
    reading = {"waiting": False, "teams_active": True, "cast_wave_pending": 2,
               "cast_wave_built": 1, key: ["3", "7", "5"],
               "teams_registered": [f"cast-{_AUDIT_RUN}-wave-1"],
               "cast_refused_team": f"cast-{_AUDIT_RUN}-wave-1"}

    assert _casting_slot(reading) == "3", reading
    header = _format_imperative_header(
        "build_castings", "", {}, run_name=_AUDIT_RUN, phase="F1",
        liveness=reading,
    )
    assert _chosen_branch("build_castings", reading) == branch, reading
    assert "casting 3" in header, header
    for later in ("7", "5"):
        assert f"casting {later}" not in header, (later, header)
        assert f"casting_id={later}" not in header, (later, header)


@pytest.mark.parametrize("team_up, branch", [
    (True, "refused"), (False, "redispatch"),
])
def test_the_casting_slot_names_a_casting_from_the_chosen_branch(team_up, branch):
    """lead-stalls D-027 — both lists non-empty and nobody running: a refusal
    branch is chosen, so `{casting}` must name the REFUSED casting. With the
    slot reading `cast_unaccepted` first, casting a's refusal was sent to
    casting b's teammate. The expected ids are written by hand here rather
    than asked of `_casting_slot` — the sweep's resolver calls that same
    function, so it agrees with it by construction and cannot see this."""
    reading = {"waiting": False, "teams_active": team_up,
               "teams_registered": [f"cast-{_AUDIT_RUN}-wave-1"] if team_up else [],
               "cast_refused_team": f"cast-{_AUDIT_RUN}-wave-1",
               "cast_wave_pending": 0, "cast_wave_built": 1,
               "cast_refused": ["a"], "cast_unaccepted": ["b"]}

    header = _format_imperative_header(
        "build_castings", "", {}, run_name=_AUDIT_RUN, phase="F1",
        liveness=reading,
    )

    assert _chosen_branch("build_castings", reading) == branch, reading
    assert "REFUSED casting a " in header, header
    assert "returned for casting a," in header, header
    assert "casting b" not in header, header
    if team_up:
        assert "<the teammate you spawned for casting a>" in header, header
    else:
        assert "Foundry-Spawn-Teammate(casting_id=a, phase='cast')" in header


@pytest.mark.parametrize("scan", ["answers", "raises"])
def test_the_refusal_choice_and_the_team_arm_read_one_scan(tmp_path, scan):
    """lead-stalls D-028 / D-031 — ONE `_check_active_teams` call per
    Foundry-Next, read by the reading AND by the cleanup arm.

    With two scans, the reading's could fail (read as "no team") while the
    arm's saw `cast-{run}-wave-1` registered: the reading chose `redispatch`,
    and the arm's answer then hung on one tuple member. One scan cannot
    disagree with itself. A scan that raises reads as "no team" for both, so
    the refused casting's team is never torn down and the router does not
    raise; the reading and the arm agree on what they were told.
    """
    from foundry_mcp.tools.orchestration import teams as _teams

    calls: list[str] = []
    with _router_run(tmp_path) as (root, fdir, teams), \
            pytest.MonkeyPatch.context() as mp:
        _arrange_cast_refused(root, fdir, teams)
        real = _teams._check_active_teams

        def _counted(project_root: str) -> dict:
            calls.append(project_root)
            if scan == "raises":
                raise OSError("team scan failed")
            return real(project_root)

        patch_everywhere(mp, "_check_active_teams", _counted)
        # The ROUTER alone is counted: the status display beside the header
        # takes its own scan to print the team list, and decides nothing.
        nxt = _compute_next_action(root)
    reading = nxt["agent_liveness"]
    header = _format_imperative_header(
        nxt["action"], "", {}, run_name=_ROUTE_RUN, phase="F1", liveness=reading,
    )

    assert len(calls) == 1, calls
    assert nxt["action"] == "build_castings", nxt["action"]
    assert not any(w in header for w in _TEARDOWN_WORDS), header
    if scan == "answers":
        assert reading["teams_registered"] == [_CAST_TEAM], reading
        assert reading["cast_refused_team"] == _CAST_TEAM, reading
        assert _chosen_branch("build_castings", reading) == "refused", reading
    else:
        assert reading["teams_registered"] == [], reading
        assert reading["teams_active"] is False, reading
        assert _chosen_branch("build_castings", reading) == "redispatch", reading
        assert "casting 1's own wave team is not registered" in header, header


def test_the_condition_detector_bites_on_a_step_that_waits_on_an_answer():
    """lead-stalls D-023 — positive control for the answer-dependent step.

    The acdc00b refused branch passed `_HANDS_OVER_THE_CONDITION` although its
    step (2) ran only when step (1)'s send answered a certain way. Quoted as it
    shipped, with the rewordings the filing tried: only the IF-opening one was
    matched then. All of them must be matched now, and the phrasings that are
    NOT the defect — a return value named inside an unconditional step, a
    wake-up event, a qualifier on data, a noun — must not be.
    """
    shipped = (
        "YOUR NEXT CALLS (in order):\n"
        "  (1) SendMessage(to=<the teammate you spawned for casting 1>, "
        "message=<the refusal>).\n"
        "  (2) Only a send that answers that the teammate cannot be reached takes "
        "this step, and it is then the whole re-dispatch: "
        "Foundry-Spawn-Teammate(casting_id=1, phase='cast').\n"
        "  (3) END YOUR TURN."
    )
    rewordings = (
        "(2) IF the send answers that the teammate cannot be reached, take this "
        "step: Foundry-Spawn-Teammate(casting_id=1, phase='cast')",
        "(2) When step (1) reports the teammate unreachable, "
        "Foundry-Spawn-Teammate(casting_id=1, phase='cast')",
        "(2) Unless the teammate answers, "
        "Foundry-Spawn-Teammate(casting_id=1, phase='cast')",
        "(2) Should step (1) have failed, Foundry-Spawn-Teammate(casting_id=1)",
        "(2) Once Foundry-Gate returns ok, Foundry-Phase(phase='cast')",
    )
    for text in (shipped, *rewordings):
        assert _HANDS_OVER_THE_CONDITION.search(text), text

    # lead-stalls D-032 — the seven refusal-conditioned steps PROVE probed at
    # fbbda01, of which the detector matched only the IF-opening one.
    for text in (
        "(2) If step (1) is refused, Foundry-Spawn-Teammate(casting_id=1)",
        "(2) Unless step (1) is refused, END YOUR TURN.",
        "(3) Foundry-Spawn-Teammate(casting_id=1), but only after step (1) is "
        "refused.",
        "(2) After step (1) errors, Foundry-Spawn-Teammate(casting_id=1)",
        "(2) In the event step (1) is rejected, "
        "Foundry-Spawn-Teammate(casting_id=1)",
        "(2) Where step (1) refuses, Foundry-Spawn-Teammate(casting_id=1)",
        "(2) Foundry-Spawn-Teammate(casting_id=1), whenever step (1) comes "
        "back with ok False.",
        # And the structure with no outcome word at all.
        "(3) Once Foundry-Accept-Casting has spoken, Foundry-Next",
        "(2) Provided that step (1) went through, END YOUR TURN.",
        # And the vocabulary with a subject that is not a step.
        "(2) Unless the send is refused, END YOUR TURN.",
        "(2) Only a message that comes back rejected takes this step.",
        # lead-stalls D-034 — mid-step, after a call, with a noun subject: the
        # sentence-start IF/UNLESS alternative cannot see this one, so it is
        # the refusal vocabulary's own control.
        "(2) Foundry-Spawn-Teammate(casting_id=1), when the send is refused.",
        "(2) END YOUR TURN, unless the message is rejected.",
    ):
        assert _HANDS_OVER_THE_CONDITION.search(text), text

    for fine in (
        "(2) One foreground Agent(...), passed step (1)'s `dispatch` field "
        "VERBATIM, obeying the model clause in the `instructions` step (1) "
        "returned.",
        "When the notification arrives, call Foundry-Next and follow what it "
        "says then.",
        "(a) the `grind_cycle_context` block when the spawn response carries "
        "one",
        "take the HEAD sha once at start, and cite that sha in the report.",
        # A refusal named as a FACT, not as a condition on a step.
        "(3) Foundry-Next — the call step (2) names on every path, accepted "
        "or refused; the verdict it recorded is what that call reads.",
        "Foundry-Accept-Casting REFUSED casting 1 and its ledger has not moved "
        "since.",
        # A condition on the server's own reading, with every call made anyway.
        "where it does not answer, the number falls back to wave 1 and step "
        "(3) refuses and names the manifest. Make all four calls in order "
        "either way.",
        "passed step (1)'s `dispatch` field VERBATIM and then, BELOW it and "
        "LAST, step (1)'s `progress_protocol` block VERBATIM.",
    ):
        assert not _HANDS_OVER_THE_CONDITION.search(fine), fine

    # And over what the table emits today, on every reading, it finds nothing:
    # the D-022 split is what makes that zero true rather than blind.
    for _action, site, text, _owed, _reading in _audit_sites():
        assert not _HANDS_OVER_THE_CONDITION.search(text), site


#: The run_streams idle sentence as b358445 emitted it: the third sentence of
#: its line, after two that open on nothing conditional (lead-stalls D-034).
_RUN_STREAMS_AT_B358445 = (
    "YOUR NEXT CALLS: spawn every missing INSPECT stream in a SINGLE parallel "
    "message.\n"
    "Every verifying stream calls Foundry-Stream itself, with the counts it "
    "actually measured, and a second record for the same (stream, cycle) "
    "REPLACES the first rather than summing with it. A lead that records on "
    "an agent's behalf is asserting numbers it did not measure, and when the "
    "agent then records its own the cycle carries two accounts of one run. "
    "If an AGENT stream finished and no record exists, that is a finding "
    "about the stream — re-dispatch it, or file it — not a gap for you to "
    "fill in.\n"
)


def test_the_condition_detector_judges_the_sentence_where_it_was_emitted():
    """lead-stalls D-034 — the third miss was POSITIONAL.

    The D-033 sentence was matched on its own and missed where `run_streams`
    printed it: mid-line, after two other sentences, with no outcome verb and
    no step as its subject. Quoted here in that position, with rewordings that
    keep the shape — a sentence opening on a condition that offers two moves —
    and the one-move sentences that are not the defect.
    """
    hit = _HANDS_OVER_THE_CONDITION.search(_RUN_STREAMS_AT_B358445)
    assert hit, _RUN_STREAMS_AT_B358445
    assert _RUN_STREAMS_AT_B358445[hit.start():].lstrip().startswith(
        "If an AGENT stream finished"
    ), hit
    # The choice shape sees it too, without the IF: the same sentence under
    # "When" is still the defect.
    assert _offers_a_choice(
        "When an AGENT stream finished and no record exists, that is a finding "
        "about the stream — re-dispatch it, or file it — not a gap for you to "
        "fill in."
    )

    for text in (
        # Mid-line IF with one move: a condition, whatever follows it.
        "Spawn the streams. If one is missing, call Foundry-Next.",
        # The choice shape under the other conditional words.
        "Record nothing. When a stream finished unrecorded, re-spawn it or "
        "file it.",
        "Read the roster. Once the send fails over, message the teammate or "
        "spawn a fresh one.",
        "Confirm the record. Should a stream end silently, re-run the stream, "
        "or file a defect.",
    ):
        assert _HANDS_OVER_THE_CONDITION.search(text), text

    for fine in (
        "Waiting is not stopping. When the notification arrives, call "
        "Foundry-Next and follow what it says then.",
        "After all complete, call Foundry-Validate-Castings.",
        "When each background stream's completion notification fires: call "
        "TaskOutput(task_id) to retrieve its findings.",
        "When you have driven the sight skill, the numbers you report are "
        "numbers YOU measured.",
        # A condition over a list of streams is not a choice between moves.
        "When the INSPECT is FULL, spawn trace, prove, or test in one message.",
        # The replacement, which states the one move.
        "An AGENT stream that finished without its own record is one of the "
        "unrecorded streams the CONTEXT below names, and the parallel message "
        "above re-spawns it with the rest.",
    ):
        assert not _HANDS_OVER_THE_CONDITION.search(fine), fine


#: lead-stalls D-043 — the six probe strings the filing drove through the
#: prose detector, five of which it did not match. Quoted as filed.
_D043_PROBES = (
    "Record nothing yourself; when an AGENT stream finished with no record, "
    "re-dispatch it or file it.",
    "Record nothing yourself; if an AGENT stream finished with no record, "
    "re-dispatch it or file it.",
    "Record nothing yourself. When an AGENT stream finished with no record, "
    "re-dispatch it or file it.",
    "Record nothing yourself — when a stream finished with no record, "
    "re-dispatch it or file it.",
    "Re-dispatch the stream or file it when it finished with no record.",
    "Call Foundry-Stream. (If a stream has no record, re-dispatch it, or file "
    "it.)",
)


def _with_idle_trailer(sentence: str):
    """`run_streams`' idle branch with ``sentence`` appended to its prose."""
    idle = _IMPERATIVES["run_streams"]["idle"]
    return idle._replace(trailer=idle.trailer + "\n" + sentence)


def test_a_conditional_sentence_cannot_add_or_move_a_call(monkeypatch):
    """lead-stalls D-043 — CLOSED THROUGH THE STRUCTURE, NOT A WIDER REGEX (the
    user's ruling of 2026-09-17).

    The D-033 sentence the filing targeted is gone: the move it stated is now
    `run_streams`' step list, one Agent call per unrecorded stream (D-040),
    and the payload publishes that list as `next_calls`. So each of the six
    probe sentences, put back into the served header, changes NO step: the
    lead's calls are the list, and the list is identical with or without it.
    A probe that DOES write a call — in the only form a lead can make one — is
    a CALL_IN_PROSE finding, which is the half of the shape the structure can
    decide. What it cannot decide is an English order with no call in it, and
    the prose detector stays beside it as an unwidened backstop for that.
    """
    baseline = drive_router(_arrange_inspect_idle, None)
    assert baseline["branch"] == "idle", baseline["header"]
    assert baseline["next_calls"], baseline

    for probe in _D043_PROBES:
        monkeypatch.setitem(
            _IMPERATIVES["run_streams"], "idle", _with_idle_trailer(probe),
        )
        driven = drive_router(_arrange_inspect_idle, None)
        assert probe in driven["header"], probe
        assert driven["next_calls"] == baseline["next_calls"], probe
        # The probe that names Foundry-Stream only names it; it is not a call.
        calls_in_prose = [
            f for f in audit_next_call_structure([
                ("payload:inspect-idle[fresh]", "inspect-idle", "",
                 "run_streams", "idle", driven),
            ]) if "payload:" in f and "CALL_IN_PROSE" in f
        ]
        assert calls_in_prose == [], (probe, calls_in_prose)

    # And the same shape spelled as calls is seen, whatever the conditional.
    for probe in (
        "When an AGENT stream finished with no record, "
        "Agent(subagent_type='foundry:tracer') or Foundry-Next().",
        "Record nothing yourself; if a stream has no record, "
        "SendMessage(to=<it>) or TeamDelete().",
    ):
        monkeypatch.setitem(
            _IMPERATIVES["run_streams"], "idle", _with_idle_trailer(probe),
        )
        driven = drive_router(_arrange_inspect_idle, None)
        findings = audit_next_call_structure([
            ("payload:inspect-idle[fresh]", "inspect-idle", "", "run_streams",
             "idle", driven),
        ])
        assert any(
            f.startswith("payload:inspect-idle[fresh]: CALL_IN_PROSE")
            for f in findings
        ), (probe, findings)


# --------------------------------------------------------------------------- #
# lead-stalls GRIND cycle 10 — D-035 .. D-037
# --------------------------------------------------------------------------- #


def _rules_of(instructions: str) -> str:
    """The standing CRITICAL RULES block, as printed above the marker."""
    return instructions.split(_NEXT_ACTION_MARKER, 1)[0]


def _spawn_rule(rules: str) -> str:
    """The one standing rule on passing a Foundry-Spawn-Teammate prompt."""
    lines = [
        line for line in rules.splitlines()
        if "prompt returned by Foundry-Spawn-Teammate" in line
    ]
    assert len(lines) == 1, rules
    return lines[0]


def test_the_rules_above_a_redispatch_sanction_the_block_it_appends(tmp_path):
    """lead-stalls GI-008 / ST-004 / US-003 (D-035) — one payload, one contract.

    The no-team refusal route, through the real acceptance door: the payload's
    `redispatch` step (2) orders the `progress_protocol` block BELOW the
    dispatch, LAST, and at b358445 the standing rule printed above it on the
    SAME payload said "Pass it to Agent VERBATIM. GRIND is the only exception".
    A lead obeying the rule spawned a teammate that was never told its ledger
    (D-030 again). Read off the assembled payload, not the constant, so a
    rules block assembled from anything else is judged too.
    """
    with _router_run(tmp_path) as (root, fdir, teams):
        _cast(fdir, {1: ["1"], 2: ["2"]})
        _worked(fdir, "1", done=True,
                at=datetime.now(timezone.utc) - timedelta(minutes=1))
        refused = _accept(root, fdir, "1",
                          completion_report="built it; the tests are deferred")
        nxt = foundry_next_action(root)
    _block, header = _split_payload(nxt["instructions"])
    rule = _spawn_rule(_rules_of(nxt["instructions"]))

    assert refused["ok"] is False, refused
    assert nxt["action"] == "build_castings", nxt["action"]
    assert _chosen_branch("build_castings", nxt["agent_liveness"]) == "redispatch"
    assert "LAST, the returned `progress_protocol` block VERBATIM" in header, header
    # The rule sanctions that very append, for every phase...
    assert "Every spawn, CAST and GRIND" in rule, rule
    assert "CAST: its prompt is" in rule and "GRIND: its prompt is" in rule, rule
    assert "Order: dispatch → progress_protocol." in rule, rule
    # ...and no longer reserves every append to GRIND.
    assert "Pass it to Agent VERBATIM. GRIND is the only exception" not in rule, rule
    # lead-stalls D-039 — and the step under it is the rule's own rendering.
    assert "Order: dispatch → progress_protocol." in header, header


def test_the_grind_dispatch_order_ends_in_the_ledger_protocol():
    """lead-stalls D-035 — the sibling surface on the same contract: the GRIND
    dispatch order the rule quotes ends in the `progress_protocol` block."""
    order = (
        "Order: dispatch → cycle_context → defects → alignment "
        "→ progress_protocol."
    )
    # lead-stalls D-039 — BOTH GRIND dispatch sequences, not only this one.
    for text in (
        _ACTION_IMPERATIVES["transition_to_grind"],
        _parse_branches(_ACTION_IMPERATIVES["fix_defects"])["idle"],
    ):
        assert order in text, text
        assert "LAST, the returned `progress_protocol` block VERBATIM" in text


def _arrange_cast_refused_two_waves(root, fdir, teams):
    # lead-stalls D-036 — castings 1 and 2 refused, one per wave, and only the
    # LATER wave's team registered. `{casting}` names casting 1 (the first
    # refused id), so the team the choice reads must be wave 1's.
    _cast(fdir, {1: ["1"], 2: ["2"]})
    _register(root, teams, f"cast-{_ROUTE_RUN}-wave-2")
    earlier = datetime.now(timezone.utc) - timedelta(minutes=1)
    _worked(fdir, "1", done=True, at=earlier)
    _worked(fdir, "2", done=True, at=earlier)
    for cid in ("2", "1"):
        refused = _accept(root, fdir, cid,
                          completion_report="built it; the tests are deferred")
        assert refused["ok"] is False, refused


def test_two_refusals_in_two_waves_key_on_the_named_castings_team(tmp_path):
    """lead-stalls D-036 — the FIRST refused id's team, pinned.

    8fa7740 names the wave of `refused[0]` "because that is the casting
    `{casting}` names", and no drive had two refused castings in two waves, so
    `refused[-1]` left every test green. Under that mutation this state reads
    wave 2's team — registered — and hands the send-back to casting 1's
    teammate, whose wave-1 team is gone: a message to nobody, then END YOUR
    TURN over nothing running (lead-stalls ST-003).
    """
    d = _drive(tmp_path, _arrange_cast_refused_two_waves)

    assert d["liveness"]["cast_refused"] == ["1", "2"], d["liveness"]
    assert d["liveness"]["teams_registered"] == [f"cast-{_ROUTE_RUN}-wave-2"], d["liveness"]
    assert d["liveness"]["cast_refused_team"] == f"cast-{_ROUTE_RUN}-wave-1", d["liveness"]
    assert (d["action"], d["branch"]) == ("build_castings", "redispatch"), d["header"]
    assert "Foundry-Spawn-Teammate(casting_id=1, phase='cast')" in d["header"], d["header"]
    assert "casting 1's own wave team is not registered" in d["header"], d["header"]


@pytest.mark.parametrize(
    "arrange, waiting",
    [(_arrange_cast_live, True), (_arrange_cast_not_spawned, False)],
    ids=["live-agents", "no-live-agents"],
)
def test_both_liveness_returns_publish_the_registered_teams(tmp_path, arrange, waiting):
    """lead-stalls D-037 — `teams_registered` on BOTH returns of
    `_waiting_on_agents`, as its docstring's return contract lists it.

    8fa7740 added the field to the no-live-agents early return and to the
    live-agents `result.update`, and only the first was tested: deleting the
    second left the suite green while Foundry-Next's `agent_liveness` in a live
    CAST state stopped carrying the field.
    """
    d = _drive(tmp_path, arrange)

    assert d["liveness"]["waiting"] is waiting, d["liveness"]
    assert d["liveness"]["teams_registered"] == [_CAST_TEAM], d["liveness"]


# --------------------------------------------------------------------------- #
# lead-stalls GRIND cycle 10 — D-038 .. D-043, the structural remedy
# --------------------------------------------------------------------------- #


def _drives_for(*states: str) -> list:
    """`_router_drives` rows for the named router states only."""
    return [
        (f"payload:{state}[{clock}]", state, transitions, action, branch,
         drive_router(arrange, seconds))
        for state, transitions, arrange, action, branch in _ROUTER_STATES
        if state in states
        for clock, seconds in _CLOCKS
    ]


def _replace_spawns(imperative, **changes):
    """``imperative`` with every teammate spawn step changed by ``changes``."""
    return imperative._replace(steps=tuple(
        step._replace(**changes) if step.blocks else step
        for step in imperative.steps
    ))


def test_every_site_and_every_payload_is_judged_as_a_step_list():
    """lead-stalls OT-002 / FR-008 — the floor under the structure audit.

    Every emission site and every router payload carries the step list its
    header was rendered from, and the router states include the three a
    spawn door leaves before its Agent has written a line (D-038 / D-041).
    """
    for action, site, text, _owed, liveness in _audit_sites():
        phase = site.split("@", 1)[1].split("[", 1)[0]
        calls, header = _emitted_imperative(
            action, _site_details(action), run_name=_AUDIT_RUN, phase=phase,
            liveness=liveness,
        )
        assert isinstance(calls, tuple), site
        assert header == text, site
    states = {state for state, *_ in _ROUTER_STATES}
    assert {
        "cast-dispatched-fresh-seed", "grind-dispatched-fresh-seed",
        "cast-redispatched-fresh-seed",
    } <= states, sorted(states)
    drives = _router_drives()
    for site, _s, _t, _a, _b, d in drives:
        assert isinstance(d["next_calls"], list), site
        assert d["rules"].strip(), site
    assert audit_next_call_structure(drives) == []


def test_the_structure_audit_bites_on_the_dispatch_only_spawn_steps(monkeypatch):
    """lead-stalls D-039 — positive control, the shipped defect put back.

    `transition_to_cast`, `build_castings`/undispatched and `fix_defects`/idle
    passed the `dispatch` block alone under a rule naming the ledger block
    LAST. With those three steps reverted, the audit names SPAWN_ORDER at every
    site and payload that serves them, and at nothing else — the redispatch
    and `transition_to_grind` steps still agree with the rule.
    """
    monkeypatch.setitem(
        _IMPERATIVES, "transition_to_cast",
        _replace_spawns(_IMPERATIVES["transition_to_cast"], blocks=("dispatch",)),
    )
    for action, branch in (("build_castings", "undispatched"), ("fix_defects", "idle")):
        monkeypatch.setitem(
            _IMPERATIVES[action], branch,
            _replace_spawns(_IMPERATIVES[action][branch], blocks=("dispatch",)),
        )
    findings = audit_next_call_structure(
        _drives_for("cast-not-spawned", "grind-idle", "cast-refused-team-down")
    )
    order = {f.split(":")[0] if not f.startswith("payload:") else ":".join(f.split(":")[:2])
             for f in findings if "SPAWN_ORDER" in f}

    assert order, findings
    assert {site.split("@")[0] for site in order if "@" in site} == {
        "transition_to_cast", "build_castings", "fix_defects",
    }, sorted(order)
    assert {
        "payload:cast-not-spawned[fresh]", "payload:grind-idle[fresh]",
    } <= order, sorted(order)
    assert not any("cast-refused-team-down" in site for site in order), order
    assert not any(site.startswith("transition_to_grind@") for site in order)
    # And the audit lead-stalls FR-008 discharges on carries the finding.
    assert any("SPAWN_ORDER" in f for f in audit_action_imperatives(drives=[]))
    assert all("passes dispatch under a rule naming dispatch →" in f
               for f in findings if "SPAWN_ORDER" in f), findings


def test_the_structure_audit_reads_the_rule_printed_above_the_step(monkeypatch):
    """lead-stalls D-039 / D-041 — the rule side of the comparison is the TEXT
    on the payload, so a rules block that says something else is seen with no
    step changed: the D-035-era sentence, and one without the one-move rule."""
    before_d035 = _guidance._STANDING_CRITICAL_RULES.replace(
        _guidance._SPAWN_PROMPT_RULE,
        "Pass it to Agent VERBATIM. GRIND is the only exception: append the "
        "'## Defects to fix this cycle:' block BELOW the dispatch.",
    )
    monkeypatch.setattr(_guidance, "_STANDING_CRITICAL_RULES", before_d035)
    findings = audit_next_call_structure(_drives_for("cast-not-spawned"))
    assert any(
        f.startswith("payload:cast-not-spawned[fresh]: RULE_WITHOUT_PHASE")
        for f in findings
    ), findings
    assert any(
        f.startswith("build_castings@F1[undispatched]: RULE_WITHOUT_PHASE")
        for f in findings
    ), findings

    monkeypatch.undo()
    stripped = _guidance._STANDING_CRITICAL_RULES.replace(_SPAWN_IS_ONE_MOVE, "")
    assert stripped != _guidance._STANDING_CRITICAL_RULES
    monkeypatch.setattr(_guidance, "_STANDING_CRITICAL_RULES", stripped)
    findings = audit_next_call_structure(_drives_for("grind-idle"))
    assert any(
        f.startswith("payload:grind-idle[fresh]: RULE_WITHOUT_ONE_MOVE")
        for f in findings
    ), findings
    assert _guidance._SPAWN_PROMPT_RULE in stripped


def test_the_structure_audit_bites_on_a_spawn_that_is_not_one_move(monkeypatch):
    """lead-stalls D-038 — positive control for SPAWN_NOT_ONE_MOVE and
    SPAWN_NOT_YIELDED: the undispatched branch with the sentence dropped from
    its spawn step, and with its yield removed."""
    undispatched = _IMPERATIVES["build_castings"]["undispatched"]
    monkeypatch.setitem(
        _IMPERATIVES["build_castings"], "undispatched",
        _replace_spawns(undispatched, note="")._replace(
            steps=_replace_spawns(undispatched, note="").steps[:-1],
        ),
    )
    findings = audit_next_call_structure(_drives_for("cast-not-spawned"))
    for kind in ("SPAWN_NOT_ONE_MOVE", "SPAWN_NOT_YIELDED"):
        assert any(
            f.startswith(f"payload:cast-not-spawned[fresh]: {kind}")
            for f in findings
        ), (kind, findings)


def test_the_structure_audit_bites_when_a_stream_has_no_call(monkeypatch):
    """lead-stalls D-040 — positive control: `test01` with no step and no
    `agent_configs` entry, which is the idle header as it shipped."""
    real_steps = _guidance._stream_steps
    real_config = _guidance._stream_agent_config
    monkeypatch.setattr(
        _guidance, "_stream_steps",
        lambda streams: real_steps(
            [s for s in (streams or []) if s != "test01"] or None
        ),
    )
    monkeypatch.setattr(
        _guidance, "_stream_agent_config",
        lambda stream: {} if stream == "test01" else real_config(stream),
    )
    findings = audit_next_call_structure(_drives_for("inspect-idle"))

    for kind in ("STREAM_WITHOUT_CALL — test01", "STREAM_WITHOUT_CONFIG"):
        assert any(
            f.startswith("payload:inspect-idle[fresh]: ") and kind in f
            for f in findings
        ), (kind, findings)
    assert any(
        f.startswith("run_streams@F1[idle]: STREAM_WITHOUT_CALL — test01")
        for f in findings
    ), findings


def test_the_idle_streams_header_names_one_call_per_unrecorded_stream(tmp_path):
    """lead-stalls GI-008 / OT-013 / D-040, as filed: F2, FULL width, this
    run's own roster (trace, prove, test, test01), nothing recorded."""
    d = _drive(tmp_path, _arrange_inspect_idle)

    assert (d["action"], d["branch"]) == ("run_streams", "idle"), d["header"]
    calls = d["nxt"]["next_calls"]
    assert [c["each"] for c in calls if c["tool"] == "Agent"] == [
        "for trace", "for prove", "for test", "for test01",
    ], calls
    assert calls[-1]["tool"] == _END_TURN, calls
    assert "subagent_type='foundry:spec-test-deriver'" in d["header"], d["header"]
    # TEST keeps its baseline model and type (test_model_config.py's pin).
    assert "Agent(model='opus', subagent_type='general-purpose'" in d["header"]
    configs = d["nxt"]["details"]["agent_configs"]
    assert configs["test01"]["subagent_type"] == "foundry:spec-test-deriver"
    # The qualifiers the recorded roster settles are gone.
    for qualifier in ("MIGRATION only", "may also run", "TEST / PROBE",
                      "COVERAGE_DIFF", "RESEARCH_AUDIT"):
        assert qualifier not in d["header"], (qualifier, d["header"])


#: lead-stalls D-038 — (A), (B) and (C): the state before the door, the door,
#: and the payload after it. Each tuple is (the before-door arrange, the owed
#: action/branch before, the door call, the owed action after).
def _before_cast_wave(root, fdir, teams):
    _arrange_cast_not_spawned(root, fdir, teams)
    _prompt_files(fdir, ["1", "2"])


def _before_grind_spawn(root, fdir, teams):
    _grind(fdir, open_defect=True)
    _wave_manifest(fdir, {1: ["1"]})
    _prompt_files(fdir, ["1"])


def _before_redispatch(root, fdir, teams):
    _cast(fdir, {1: ["1"], 2: ["2"]})
    _prompt_files(fdir, ["1", "2"])
    _worked(fdir, "1", done=True,
            at=datetime.now(timezone.utc) - timedelta(minutes=1))
    _verdict(fdir, "1", "refused")


_DOOR_ROUTES = {
    "A-cast-wave": (
        _before_cast_wave, ("build_castings", "undispatched"),
        lambda root, teams: foundry_cast_wave(1, "cast", project_root=root),
        "build_castings", "Foundry-Cast-Wave",
    ),
    "B-grind-spawn": (
        _before_grind_spawn, ("fix_defects", "idle"),
        lambda root, teams: (
            _register(root, teams, _GRIND_TEAM),
            foundry_spawn_teammate(1, "grind", project_root=root),
        )[1],
        "fix_defects", "Foundry-Spawn-Teammate",
    ),
    "C-redispatch": (
        _before_redispatch, ("build_castings", "redispatch"),
        lambda root, teams: foundry_spawn_teammate(1, "cast", project_root=root),
        "build_castings", "Foundry-Spawn-Teammate",
    ),
}


@pytest.mark.parametrize("route", sorted(_DOOR_ROUTES))
def test_a_fresh_seed_is_the_teammate_the_one_move_spawned(tmp_path, route):
    """lead-stalls D-038 — drives (A), (B) and (C) with a FRESH seed.

    THE REMEDY CHOSEN, AND WHY. A seed-only ledger is both "door returned, no
    Agent yet" and "Agent spawned, still reading its prompt", and nothing a
    guidance read can see separates them — `seeded_by: server` is on the line
    in both. Answering the seed with "spawn it now" would duplicate EVERY spawn
    on the ordinary path, where the lead calls Foundry-Next seconds after its
    Agent call. So the header that names the door names the Agent call as the
    same move, with no Foundry-Next between them and the yield after it, and
    the rules above say so too; under that rule a fresh seed IS a spawned
    teammate, and `live` is the true answer for it.
    """
    before, owed_before, door, owed_after, door_tool = _DOOR_ROUTES[route]
    with _router_run(tmp_path) as (root, fdir, teams):
        before(root, fdir, teams)
        routed = foundry_next_action(root)
        header = _split_payload(routed["instructions"])[1]
        calls = routed["next_calls"]
        opened = door(root, teams)
        after = foundry_next_action(root)
    rules = _spawn_rule(_rules_of(routed["instructions"]))

    assert (routed["action"], _chosen_branch(
        routed["action"], routed["agent_liveness"]
    )) == owed_before, header
    tools = [c["tool"] for c in calls]
    at = tools.index(door_tool)
    assert tools[at + 1] == "Agent", calls
    assert _SPAWN_IS_ONE_MOVE in calls[at + 1]["note"], calls[at + 1]
    assert calls[at + 1]["prompt_blocks"][-1] == "progress_protocol", calls
    assert tools[-1] == _END_TURN and "Foundry-Next" not in tools[at:], calls
    assert _SPAWN_IS_ONE_MOVE in rules, rules

    assert opened["ok"] is True, opened
    live = after["agent_liveness"]
    assert after["action"] == owed_after, after["action"]
    assert _chosen_branch(owed_after, live) == "live", live
    assert live["waiting"] is True, live
    assert {a["step"] for a in live["agents"]} == {"dispatched"}, live
    assert after["next_calls"] == [], after["next_calls"]
    assert "END YOUR TURN" in _split_payload(after["instructions"])[1]


def test_the_naive_seed_fix_would_spawn_every_teammate_twice(monkeypatch):
    """lead-stalls D-038 / D-041 — positive control for the fresh-seed rows.

    The alternative the defect record warns about — read a seed-only reading as
    not dispatched — sends the lead to dispatch again over teammates its own
    Agent call already made. The payload sweep names that at all three rows.
    """
    real = _guidance._branch_state

    def _seeds_ignored(liveness: object) -> str:
        row = liveness if isinstance(liveness, dict) else {}
        agents = row.get("agents") or []
        if row.get("waiting") and agents and all(
            a.get("step") == "dispatched" for a in agents
        ):
            return "undispatched"
        return real(liveness)

    monkeypatch.setattr(_guidance, "_branch_state", _seeds_ignored)
    states = ("cast-dispatched-fresh-seed", "grind-dispatched-fresh-seed",
              "cast-redispatched-fresh-seed")
    findings = [
        f for f in audit_assembled_payloads(_drives_for(*states))
        if "WRONG_ROUTE" in f
    ]
    assert {f.split(":", 2)[1].split("[")[0] for f in findings} == set(states), findings


def test_the_payload_publishes_the_steps_its_header_renders(run_env):
    """lead-stalls GI-008 / OT-013 — `next_calls` beside the header, as data:
    one record per numbered line, in order, for an unbranched action too."""
    project_root, fdir = run_env
    _write_state(fdir, phase="F0", cycle=0)
    _write_manifest_with_castings(fdir, ["src/api/a.py"], no_ui=True)

    nxt = foundry_next_action(project_root)
    header = _split_payload(nxt["instructions"])[1]

    assert nxt["action"] == "transition_to_cast", nxt["action"]
    assert [c["tool"] for c in nxt["next_calls"]] == [
        "Foundry-Validate-Castings", "Foundry-Gate", "Foundry-Phase",
        "TeamCreate", "Foundry-Team-Up", "Foundry-Cast-Wave", "Agent", _END_TURN,
    ], nxt["next_calls"]
    assert [line for _n, line in _STEP_LINE.findall(header)] == [
        _render_call(_as_step(c)) for c in nxt["next_calls"]
    ]
    assert set(nxt["next_calls"][0]) == {"tool", "args", "each", "prompt_blocks", "note"}
    assert {c["tool"] for c in nxt["next_calls"]} <= _LEAD_CALLS
    assert _call_record(_as_step(nxt["next_calls"][6])) == nxt["next_calls"][6]


#: lead-stalls GI-008 / FR-007 — every entry's calls, in order, written by
#: hand. The structure audit judges SHAPE (a real tool, no step after the
#: yield, spawns against the rule); this is the table of WHAT each run state
#: owes, so a step dropped, split, merged or reordered is named by key.
_OWED_CALLS = {
    "init": ["Foundry-Init"],
    # The template shows the report a halt could not write; with it written
    # the served list is empty and the header reads NONE.
    "halted": ["Foundry-Report"],
    "done": [],
    "unknown": ["Foundry-Context", "Foundry-Next"],
    "cleanup_teams": ["SendMessage", "TeamDelete", "Foundry-Team-Down"],
    # The validation "after all complete" is the next action's first step,
    # never a step behind the yield; and while a writer runs, the answer is
    # the yield alone (lead-stalls D-056).
    "add_castings/live": [],
    "add_castings/idle": ["Agent", _END_TURN],
    "transition_to_cast": [
        "Foundry-Validate-Castings", "Foundry-Gate", "Foundry-Phase",
        "TeamCreate", "Foundry-Team-Up", "Foundry-Cast-Wave", "Agent", _END_TURN,
    ],
    "build_castings/live": [],
    "build_castings/idle": [
        "TeamDelete", "Foundry-Team-Down", "Foundry-Gate", "Foundry-Phase",
    ],
    "build_castings/undispatched": [
        "TeamCreate", "Foundry-Team-Up", "Foundry-Cast-Wave", "Agent", _END_TURN,
    ],
    "build_castings/refused": ["SendMessage", _END_TURN],
    "build_castings/redispatch": [
        "Foundry-Spawn-Teammate", "Agent", "SendMessage", _END_TURN,
    ],
    "build_castings/unaccepted": [
        "Foundry-Spec-Hash", "Foundry-Accept-Casting", "Foundry-Next",
    ],
    "transition_to_inspect": ["Foundry-Gate", "Foundry-Phase"],
    "run_streams/live": [],
    # The template expands every stream (the prose sweeps read all of them).
    "run_streams/idle": ["Agent"] * 8 + ["Skill", _END_TURN],
    "transition_to_grind": [
        "Foundry-Tasks", "Foundry-Gate", "Foundry-Phase", "TeamCreate",
        "Foundry-Team-Up", "Foundry-Spawn-Teammate", "Agent", _END_TURN,
    ],
    "fix_defects/live": [],
    "fix_defects/idle": [
        "TeamDelete", "Foundry-Team-Down", "Foundry-Tasks", "TeamCreate",
        "Foundry-Team-Up", "Foundry-Spawn-Teammate", "Agent", _END_TURN,
    ],
    # The gate before the transition it guards, and no "update state" step.
    "transition_to_assay": ["Foundry-Gate", "Foundry-Phase", "Agent"],
    "run_assay": ["Agent"],
    # The strip and its commit are two calls.
    "transition_to_done": [
        "Foundry-Report", "Foundry-Gate", "Bash", "Bash", "Foundry-Phase",
    ],
    "transition_to_temper": ["Foundry-Gate", "Foundry-Phase"],
    # lead-stalls D-050 — each post-ASSAY phase's list ends in the gate out
    # of it, whose record is what the router reads to serve the crossing.
    "run_temper": ["Skill", "Foundry-Report", "Foundry-Gate"],
    "transition_to_nyquist": ["Foundry-Gate", "Foundry-Phase"],
    "run_nyquist": ["Agent", "Foundry-Report", "Foundry-Gate"],
    # lead-stalls D-053 — the template shows the filing its list opens with
    # when no open defect carries ASSAY's verdicts.
    "assay_failed_loop_back": [
        "Foundry-Sync", "Foundry-Tasks", "Foundry-Gate", "Foundry-Phase",
    ],
    "widen_inspect": ["Foundry-Gate", "Foundry-Phase"],
    "record_inspect_width": ["Foundry-Gate", "Foundry-Phase"],
    # lead-stalls D-055 — no accepted transition leaves this state.
    "escalation_held": [],
}


def _served_tables() -> dict[str, list[str]]:
    served = {}
    for action, entry in _IMPERATIVES.items():
        for name, imperative in (
            entry.items() if isinstance(entry, dict) else (("", entry),)
        ):
            key = f"{action}/{name}" if name else action
            served[key] = [
                step.tool for step in _guidance._template_steps(imperative.steps)
            ]
    return served


def test_every_imperative_names_its_calls_in_order():
    """lead-stalls GI-008 / FR-007 / FR-008 — the owed call table, per branch."""
    assert _served_tables() == _OWED_CALLS


def test_every_gate_step_precedes_the_transition_it_guards():
    """lead-stalls FR-007 — `transition_to_assay` named the ASSAY gate AFTER
    the `inspect_clean` transition it guards. Every list that holds both a
    gate and a phase call names the gate first, and the gate guards it."""
    from foundry_mcp.tools.orchestration.gates import GATE_TO_TRANSITION

    checked = 0
    for action, entry in _IMPERATIVES.items():
        for imperative in (entry.values() if isinstance(entry, dict) else (entry,)):
            tools = [step.tool for step in imperative.steps]
            if "Foundry-Gate" not in tools or "Foundry-Phase" not in tools:
                continue
            checked += 1
            gate = imperative.steps[tools.index("Foundry-Gate")]
            phase = imperative.steps[tools.index("Foundry-Phase")]
            assert tools.index("Foundry-Gate") < tools.index("Foundry-Phase"), action
            if "{" in (gate.args or ""):
                continue      # transition_to_inspect: one crossing row, pinned elsewhere
            guarded = GATE_TO_TRANSITION[_ARG_PHASE.search(gate.args).group(1)]
            assert _ARG_PHASE.search(phase.args).group(1) in guarded, action
    assert checked >= 9, checked


def test_the_prose_the_structure_moved_still_says_what_it_said():
    """The sentences the rework rewrote or moved, pinned where they now live:
    the live branch's seed sentence (D-038), the dispatch branch's stale-seed
    sentence and its dropped claim (D-038), the rules line's one-step clause
    (D-038, now inside the one-move sentence of D-044), and the two trailers
    that lost a call written in prose."""
    live = _parse_branches(_ACTION_IMPERATIVES["build_castings"])["live"]
    assert "holds only the line its spawn door wrote counts as running" in live
    assert "holds only the line its spawn door wrote counts as running" in (
        _parse_branches(_ACTION_IMPERATIVES["fix_defects"])["live"]
    )
    undispatched = _parse_branches(_ACTION_IMPERATIVES["build_castings"])["undispatched"]
    assert "reads here only once its seeds are stale" in undispatched
    assert "between steps (4) and (6)" not in undispatched
    rule = next(
        line for line in _guidance._STANDING_CRITICAL_RULES.splitlines()
        if line.startswith("- NEVER stop between phases")
    )
    assert "A spawn door and the Agent call it feeds are one step of that move" in (
        rule
    ), rule
    # lead-stalls D-056 — the trailer handed the lead a count of
    # notifications ("the last notification wakes you for"); the writers'
    # ledgers answer that now, and the prompt says where they are.
    assert "last notification" not in _ACTION_IMPERATIVES["add_castings"]
    assert "No team is needed" in _ACTION_IMPERATIVES["add_castings"]
    writers = _parse_branches(_ACTION_IMPERATIVES["add_castings"])["idle"]
    assert "progress/decompose-<domain>.jsonl" in writers, writers
    assert '"done": true' in writers, writers
    assert "cleanup failure mode" in _ACTION_IMPERATIVES["cleanup_teams"]
    assert "Do NOT wait for 'shutdown_response' events" in (
        _ACTION_IMPERATIVES["cleanup_teams"]
    )
    for action in ("add_castings", "run_streams"):
        assert "TaskOutput(" not in _ACTION_IMPERATIVES[action], action
    streams = _ACTION_IMPERATIVES["run_streams"]
    # lead-stalls D-033 / D-040 — the finished-unrecorded stream is in the list.
    assert "A stream that finished without its own record is in that list" in streams
    # fallout D-169 — the snapshot rule rides in every reading stream's prompt.
    trace = _guidance._stream_agent_step("trace").args
    assert "`git archive HEAD`" in trace and "cite that sha" in trace, trace
    assert "PIN ITS WORK TO A SNAPSHOT" in streams


# --------------------------------------------------------------------------- #
# lead-stalls GI-008 / FR-015 / CT-003 / US-003 (D-044, D-045) — ONE LIST, ONE MOVE
# --------------------------------------------------------------------------- #

#: The rules line as it stood at 373e2d1, which the one-move walk must refuse.
_AFTER_EACH_STEP_RULE = (
    "\n- NEVER stop between phases. Call Foundry-Next after each step and "
    "follow it; a spawn door and the Agent call it feeds are one step (the "
    "spawn rule below). REQUIRED everywhere except exactly one place: "
)

#: The three lists PROVE drove wrong at 373e2d1, each with a step after a
#: call whose answer, or the old rule, put a Foundry-Next in the middle.
_ONE_MOVE_STATES = {
    "cast-built": _arrange_cast_built,
    "grind-idle": _arrange_grind_idle,
    "cast-refused-team-down": _arrange_cast_refused_team_down,
}


def _one_move_drive(state: str) -> dict:
    return drive_router(_ONE_MOVE_STATES[state], None)


def _one_move_findings(d: dict, answers: dict, rules: str | None = None,
                       calls: list | None = None) -> list[str]:
    return judge_next_calls(
        "one-move", d["action"],
        d["next_calls"] if calls is None else calls,
        d["header"], d["rules"] if rules is None else rules,
        d["details"], None, _mcp_tool_names(), answers,
    )


def test_a_served_list_is_one_move_in_the_rules_and_in_every_answer():
    """lead-stalls D-044 / D-045 — the rules, the lists and the door answers
    agree that Foundry-Next comes after a served list's last step.

    At 373e2d1 the rules said "Call Foundry-Next after each step", Team-Down
    answered "Call Foundry-Next now." from the middle of two lists, and a
    Foundry-Next taken there re-served each list from step (1). The walk
    reads Team-Down's answer off the real door."""
    from foundry_mcp.tools.orchestration.teams import (
        SERVED_LIST_IS_ONE_MOVE,
        TEAM_DOWN_NEXT_CALL,
    )

    rules = _guidance._STANDING_CRITICAL_RULES
    assert _A_SERVED_LIST_IS_ONE_MOVE in rules
    assert SERVED_LIST_IS_ONE_MOVE in _A_SERVED_LIST_IS_ONE_MOVE
    assert "after each step" not in rules
    assert _orders_foundry_next_mid_list(rules) == []
    # The one Foundry-Next a list tolerates is a read that replaces nothing;
    # "call it there when you want them" read as licence to restart the list.
    exception = _guidance._GATE_THEN_PHASE_EXCEPTION
    assert "its answer never replaces the list you are making" in exception
    assert "the step after it is still the step your list numbers after that Foundry-Gate" in exception
    assert "that list's Foundry-Phase" not in exception
    assert "call it there when you want them" not in exception

    answers = _step_answers()
    assert answers["Foundry-Team-Down"] == TEAM_DOWN_NEXT_CALL
    assert _orders_foundry_next_mid_list(TEAM_DOWN_NEXT_CALL) == []

    for state in _ONE_MOVE_STATES:
        d = _one_move_drive(state)
        tools = [call["tool"] for call in d["next_calls"]]
        assert "Foundry-Next" not in tools[:-1], (state, tools)
        assert _one_move_findings(d, answers) == [], state
        assert _A_SERVED_LIST_IS_ONE_MOVE in d["rules"], state
    # D-045 (1): the refusal is a step of the move the spawn opened, before
    # the yield, with no Foundry-Next anywhere in between.
    tools = [c["tool"] for c in _one_move_drive("cast-refused-team-down")["next_calls"]]
    assert tools == [
        "Foundry-Spawn-Teammate", "Agent", "SendMessage", _END_TURN,
    ], tools


def test_the_one_move_walk_bites_on_every_shape_the_defects_took():
    """Positive controls for the D-044 / D-045 walk, on real served lists."""
    answers = _step_answers()
    for state in ("cast-built", "grind-idle"):
        d = _one_move_drive(state)
        assert _one_move_findings(d, answers) == [], state

        # The 373e2d1 rules line restored.
        old = d["rules"].replace(
            "\n- NEVER stop between phases. " + _A_SERVED_LIST_IS_ONE_MOVE,
            _AFTER_EACH_STEP_RULE,
        )
        assert old != d["rules"], state
        kinds = " ".join(_one_move_findings(d, answers, rules=old))
        assert "RULE_WITHOUT_LIST_MOVE" in kinds, state
        assert "RULE_ORDERS_NEXT_MID_LIST" in kinds, state

        # Team-Down's 373e2d1 answer.
        reverted = {**answers, "Foundry-Team-Down": "Call Foundry-Next now."}
        assert any(
            "ANSWER_BREAKS_LIST — step (2) Foundry-Team-Down" in f
            for f in _one_move_findings(d, reverted)
        ), state

        # A Foundry-Next step, and a step note ordering one, mid-list.
        calls = list(d["next_calls"])
        assert any(
            "NEXT_MID_LIST — step (2)" in f
            for f in _one_move_findings(
                d, answers, calls=calls[:1] + [{"tool": "Foundry-Next"}] + calls[1:]
            )
        ), state
        noted = [dict(calls[0], note="then call Foundry-Next and follow it")]
        assert any(
            "STEP_ORDERS_NEXT_MID_LIST — step (1)" in f
            for f in _one_move_findings(d, answers, calls=noted + calls[1:])
        ), state

    # Negative control: Accept-Casting's "Call Foundry-Next now." is the
    # acceptance list's own next and last step, so it breaks nothing.
    d = drive_router(_arrange_cast_unaccepted, None)
    assert [c["tool"] for c in d["next_calls"]][1:] == [
        "Foundry-Accept-Casting", "Foundry-Next",
    ]
    assert answers["Foundry-Accept-Casting"] == "Call Foundry-Next now."
    assert _one_move_findings(d, answers) == []
    # And a prohibition orders nothing.
    assert _orders_foundry_next_mid_list(
        "Do NOT call Foundry-Next in a loop. never call Foundry-Next twice."
    ) == []


# --------------------------------------------------------------------------- #
# lead-stalls GI-008 (D-048) — THE GATE-THEN-PHASE NOTE, WALKED PAST ITS GATE
# --------------------------------------------------------------------------- #
#
# The note said "the step after it is still that list's Foundry-Phase" and
# "Foundry-Phase straight after a passing Foundry-Gate is accepted". Ten of the
# eleven lists carrying it put the phase straight after the gate; the eleventh,
# `transition_to_done`, puts the strip and its commit there, and a lead obeying
# the note sealed F6 over a committed corpus. The one-move test only asked
# whether the clause was present and the payload walk stopped AT the gate, so
# nothing read the list past it. This walk does, for the note and for the
# advance notice a passed gate prints.

#: The note's claim about the step after the read, captured to its full stop.
_STEP_AFTER_THE_READ = re.compile(r"the step after it is still (?P<claim>[^.]+)\.")
#: A tool the note puts "straight after a passing Foundry-Gate". Foundry-Next
#: there is the read itself, not a claim about the list.
_STRAIGHT_AFTER_THE_GATE = re.compile(
    r"(?P<tool>Foundry-[A-Za-z-]+) straight after a passing Foundry-Gate"
)


def _note_carrying_lists() -> list[tuple[str, tuple]]:
    """(site, steps) for every rendered branch whose header carries the note."""
    note = _GATE_THEN_PHASE_NOTE.lstrip("\n")
    sites = []
    for action, entry in sorted(_IMPERATIVES.items()):
        branches = entry.items() if isinstance(entry, dict) else (("-", entry),)
        for name, imperative in branches:
            for phase in _emission_phases(action):
                steps, header = _resolved_imperative(
                    imperative, action, _site_details(action), _AUDIT_RUN,
                    phase, None, 3,
                )
                if steps and note in header:
                    sites.append((f"{action}[{name}]@{phase}", steps))
    return sites


def _gate_of(step) -> str:
    match = re.search(r"phase='([a-z_]+)'", step.args or "")
    return match.group(1) if match else ""


def _past_the_gate_findings(site: str, steps: tuple, note: str,
                            notice=None) -> list[str]:
    """Every claim the note or the advance notice makes about the step after a
    list's Foundry-Gate that the list itself contradicts."""
    notice = notice or _guidance._gate_advance_notice
    claim = _STEP_AFTER_THE_READ.search(note)
    if claim is None:
        return [f"{site}: the note names no step after the read"]
    # Foundry-Gate in the claim is its anchor ("after that Foundry-Gate"),
    # never the step it names.
    named = {
        tool for tool in _LEAD_CALLS
        if tool in claim["claim"] and tool != "Foundry-Gate"
    }
    named |= {
        match["tool"] for match in _STRAIGHT_AFTER_THE_GATE.finditer(note)
        if match["tool"] != "Foundry-Next"
    }
    findings = []
    gates = [n for n, step in enumerate(steps, 1) if step.tool == "Foundry-Gate"]
    if not gates:
        findings.append(f"{site}: carries the note and names no Foundry-Gate")
    for number in gates:
        if number == len(steps):
            findings.append(f"{site}: Foundry-Gate is the list's last step")
            continue
        after = steps[number]
        for tool in sorted(named - {after.tool}):
            findings.append(
                f"NOTE_CLAIM_FALSE {site}: the note puts {tool} after the "
                f"read, the list numbers ({number + 1}) {after.tool}"
            )
        call = after.tool if after.args is None else f"{after.tool}({after.args})"
        told = notice(_gate_of(steps[number - 1]), steps)
        if f"step ({number + 1}) {call} " not in told:
            findings.append(
                f"NOTICE_CLAIM_FALSE {site}: the advance notice does not name "
                f"({number + 1}) {call}: {told}"
            )
    return findings


#: The two sentences D-048 found, as 621318d shipped them.
_NOTE_AT_621318D = (
    "Foundry-Next between a passing Foundry-Gate and its Foundry-Phase is "
    "OPTIONAL — the gate no longer consumes the ordering token, so "
    "Foundry-Phase straight after a passing Foundry-Gate is accepted. It is a "
    "read and not a move: that is where the INSPECT mode (its width) and the "
    "rule that fired are announced, its answer never replaces the list you "
    "are making, and the step after it is still that list's Foundry-Phase. "
    "Never call it to satisfy the protocol."
)


def _notice_at_621318d(gate: str, _steps) -> str:
    return (
        f"✅ Foundry-Gate(phase='{gate}') ALREADY PASSED — do NOT re-run it. "
        "Proceed directly to the transition step (Foundry-Phase / state "
        "update) in the imperative below."
    )


def test_the_gate_then_phase_note_holds_past_the_gate_on_every_list():
    """D-048: walked past its Foundry-Gate, every list that carries the note
    has the next step the note and the advance notice say it has."""
    sites = _note_carrying_lists()
    assert len(sites) >= 11, [site for site, _ in sites]
    note = _guidance._GATE_THEN_PHASE_EXCEPTION
    findings = [
        finding for site, steps in sites
        for finding in _past_the_gate_findings(site, steps, note)
    ]
    assert findings == [], findings

    # transition_to_done by name: the strip, not the phase, follows the gate.
    done = dict(sites)["transition_to_done[-]@F1"]
    assert [step.tool for step in done] == [
        "Foundry-Report", "Foundry-Gate", "Bash", "Bash", "Foundry-Phase",
    ]
    told = _guidance._gate_advance_notice("done", done)
    assert "step (3) Bash(command='git rm -r --cached evidence/" in told, told
    assert "Foundry-Phase" not in told.split("Proceed directly to", 1)[1], told


def test_the_past_the_gate_walk_bites_on_the_621318d_sentences():
    """Revert controls: the shipped note and the shipped notice are each
    flagged on transition_to_done and on no other list."""
    sites = _note_carrying_lists()
    for reverted in (
        {"note": _NOTE_AT_621318D},
        {"note": _guidance._GATE_THEN_PHASE_EXCEPTION,
         "notice": _notice_at_621318d},
    ):
        flagged = {
            site for site, steps in sites
            if _past_the_gate_findings(site, steps, **reverted)
        }
        if "notice" in reverted:
            # The old notice named no step number on ANY list.
            assert "transition_to_done[-]@F1" in flagged, flagged
        else:
            assert flagged == {"transition_to_done[-]@F1"}, flagged


def test_a_passed_done_gate_points_the_lead_at_the_strip():
    """D-048, driven through Foundry-Next: at F4 with Gate(done) passed, the
    tolerated read answers with a notice naming step (3), the strip."""
    from tests.orchestration._env import _write_verdicts

    with tempfile.TemporaryDirectory() as tmp, _router_run(Path(tmp)) as (
        root, fdir, _teams,
    ):
        _write_spec(fdir, ["FR-1"])
        _write_state(fdir, phase="F4", cycle=3)
        _write_verdicts(fdir, [{
            "id": "FR-1", "verdict": "VERIFIED", "evidence": "x",
            "spec_text_cited": "x", "cycle": 3,
        }])
        (fdir / ".gate-passed").write_text(
            json.dumps({"phase": "done", "at": "2026-09-18T03:00:00+00:00"}),
            encoding="utf-8",
        )
        nxt = foundry_next_action(root)
    assert nxt["action"] == "transition_to_done", nxt["action"]
    assert nxt["gate_advanced"]["passed_gate"] == "done"
    tools = [call["tool"] for call in nxt["next_calls"]]
    assert tools[1:3] == ["Foundry-Gate", "Bash"], tools
    notice = next(
        line for line in nxt["instructions"].split("\n")
        if "ALREADY PASSED" in line
    )
    assert "step (3) Bash(command='git rm -r --cached evidence/" in notice, notice
    assert "(Foundry-Phase / state update)" not in nxt["instructions"]


def test_the_rules_block_states_the_one_move_and_spawn_clauses_once():
    """D-047, this side: the rules block `guidance.py` renders carries the
    served-list sentence and the spawn-pair clause exactly once. With
    6080974's teams.py hunk reverted the shared sentence carried the clause
    too, and the block printed it twice."""
    from foundry_mcp.tools.orchestration.teams import SERVED_LIST_IS_ONE_MOVE

    rules = _guidance._STANDING_CRITICAL_RULES
    assert rules.count(SERVED_LIST_IS_ONE_MOVE) == 1, rules
    spawn_clause = "a spawn door and the agent call it feeds are one step"
    assert rules.lower().count(spawn_clause) == 1, rules
    assert rules.count("spawn rule below") == 1, rules


# --------------------------------------------------------------------------- #
# lead-stalls GI-008 (D-046) — THE UNRESOLVED-CROSSING FALLBACK, PINNED
# --------------------------------------------------------------------------- #

#: Every phase the router knows, the empty one a caller that read nothing
#: passes, and one no ladder names — so the sweep reaches the crossings'
#: misses, which no router path does today (D-046's reachability note).
_ANY_PHASE = ("", "F0", "F0.5", "F1", "F2", "F3", "F4", "F5", "F5.5", "F6",
              "HALTED", "F9")

#: Any `{word}` left in text the lead receives. `{id}` is the one literal:
#: `add_castings` names the file `casting-{id}-prompt.md` the decomposition
#: writer fills in, and no resolver owns it.
_ANY_SLOT = re.compile(r"\{[a-z_ ]+\}")
_LITERAL_PROSE_SLOTS = frozenset({"{id}"})


def test_an_unresolved_crossing_takes_the_generic_header():
    """D-046: deleting `_resolved_imperative`'s `{gate}`/`{token}` fallback
    left the suite green, and `transition_to_inspect` from F2, F0 or ''
    then published `phase='{gate}'` in the header AND in next_calls."""
    crossings = _ACTION_CROSSINGS["transition_to_inspect"]
    missed = [phase for phase in _ANY_PHASE if phase not in crossings]
    assert {"", "F0", "F2"} <= set(missed)
    for phase in missed:
        steps, header = _emitted_imperative(
            "transition_to_inspect", {}, run_name="r", phase=phase,
        )
        assert steps is None, phase
        assert header == _generic_header("transition_to_inspect"), phase
        assert _format_imperative_header(
            "transition_to_inspect", "", {}, run_name="r", phase=phase,
        ) == header, phase
        assert "{" not in header, phase
        nxt_calls = [_call_record(step) for step in steps or ()]
        assert nxt_calls == [], phase


def test_no_emission_carries_an_unresolved_slot_in_any_phase():
    """D-046's structural half: a guard no router path reaches is unpinned,
    so every action is emitted from every phase and every reading, and
    neither the header nor any published call field may keep a `{slot}`."""
    leaks: dict[str, list[str]] = {}
    for action in sorted(_IMPERATIVES):
        for phase in _ANY_PHASE:
            for label, liveness, _owed in _LIVENESS_READINGS:
                steps, header = _emitted_imperative(
                    action, _site_details(action), run_name=_AUDIT_RUN,
                    phase=phase, liveness=liveness,
                )
                texts = [header] + [
                    str(value)
                    for step in steps or ()
                    for value in _call_record(step).values()
                ]
                found = sorted({
                    slot for text in texts for slot in _ANY_SLOT.findall(text)
                } - _LITERAL_PROSE_SLOTS)
                if found:
                    leaks[f"{action}@{phase}[{label}]"] = found
    assert leaks == {}, leaks

    # Positive control: the sweep sees the leak D-046's mutation produced.
    assert _ANY_SLOT.findall("Foundry-Gate(phase='{gate}')") == ["{gate}"]


# --------------------------------------------------------------------------- #
# lead-stalls GI-008 / FR-007 (D-050) — THE POST-ASSAY PHASES LEAVE BY A LIST
# --------------------------------------------------------------------------- #
#
# `run_temper` and `run_nyquist` each named one literal call, so every sweep
# above read them as clean; the exit sat in the CONTEXT ("When clean, call
# Foundry-Gate(phase='done'), update to F6.") and the Foundry-Next owed after
# the list re-served the same list. Driven at 67c58b5, B4, B5, B6 and B9 all
# answered `run_temper`, so no --temper run reached F6 by following the served
# steps. The walk below is that drive, through the real doors, with the lead
# doing exactly what the rules say: every step of a served list, then
# Foundry-Next. A step no door can take in a test (the Skill, the auditor
# Agent, TeamCreate) is the lead's harness and is passed over; the defect
# ledger is written where TEMPER would have filed.


def _walk_served_lists(root: str, *, filed_by_phase_work: Path | None = None,
                       lists: int = 8,
                       record_streams: bool = False) -> list[tuple[str, str, list[str]]]:
    """Follow served lists until F6 or a teammate spawn; return what was served.

    ``filed_by_phase_work`` is the run dir whose ledger gets a LIVE defect the
    first time a `run_temper` / `run_nyquist` list's phase-work step is made —
    what TEMPER or an ESCALATE_IMPL_BUG filing leaves.

    ``record_streams`` (lead-stalls D-057) stands in for the stream agents a
    `run_streams` list spawns: each stream the payload names as missing records
    a clean run of its own through the Foundry-Stream door, as the agent would,
    and the walk moves on — so a walk can cross an INSPECT it opened.
    """
    import subprocess

    from foundry_mcp.tools.foundry_report import foundry_report
    from foundry_mcp.tools.orchestration.directives import foundry_defects_to_tasks
    from foundry_mcp.tools.orchestration.fix_gate import foundry_sync_defects
    from foundry_mcp.tools.orchestration.gates import foundry_gate
    from foundry_mcp.tools.orchestration.streams import foundry_mark_stream
    from foundry_mcp.tools.orchestration.transitions import (
        foundry_mark_phase_complete,
    )

    served: list[tuple[str, str, list[str]]] = []
    for _ in range(lists):
        nxt = foundry_next_action(root)
        calls = nxt.get("next_calls") or []
        served.append((str(nxt.get("phase")), nxt.get("action", ""),
                       [c["tool"] for c in calls]))
        if nxt.get("action") in ("init", "done", "halted"):
            break
        if record_streams and nxt.get("action") == "run_streams":
            cycle = json.loads(
                (foundry_state.get_run_dir(root) / "state.json").read_text(encoding="utf-8")
            )["cycle"]
            for stream in nxt["details"]["missing_streams"]:
                marked = foundry_mark_stream(
                    stream=stream, cycle=cycle, items_checked=3, items_total=3,
                    findings_count=0, project_root=root,
                )
                assert "error" not in marked, (stream, marked)
            continue
        start = 0
        if nxt.get("gate_advanced"):
            start = [c["tool"] for c in calls].index("Foundry-Gate") + 1
        for call in calls[start:]:
            tool, args = call["tool"], call["args"] or ""
            arg = args.split("'")[1] if "'" in args else ""
            if tool in ("Skill", "Agent") and filed_by_phase_work is not None:
                _defect_ledger(
                    filed_by_phase_work, [_tiered("D-001", "LIVE", status="open")],
                )
                filed_by_phase_work = None
            if tool == "TeamCreate":
                return served
            if tool == "Foundry-Gate":
                if not foundry_gate(arg, project_root=root)["passed"]:
                    break
            elif tool == "Foundry-Phase":
                assert foundry_mark_phase_complete(arg, project_root=root)["ok"], arg
            elif tool == "Foundry-Report":
                assert foundry_report(project_root=root)["ok"]
            elif tool == "Foundry-Tasks":
                foundry_defects_to_tasks(project_root=root)
            elif tool == "Foundry-Sync":
                # lead-stalls D-053 — the filing, made with exactly the fields
                # the step names and nothing else (`_sync_findings`).
                filed = foundry_sync_defects(
                    int(args.split("cycle=", 1)[1].split(",", 1)[0]),
                    _sync_findings(args, root),
                    project_root=root,
                )
                assert "error" not in filed, filed
            elif tool == "Bash":
                command = args.split("command='", 1)[1].rsplit("'", 1)[0]
                subprocess.run(command, shell=True, cwd=root, check=True,
                               capture_output=True)
    return served


#: One field a Foundry-Sync step names: `key='literal'` or `key=<recipe>`.
_SYNC_FIELD = re.compile(r"\b([a-z_]+)=(?:'([^']*)'|<([^>]*)>)")


def _sync_findings(args: str, root: str) -> list[dict]:
    """The findings a lead builds by following a Foundry-Sync step to the
    letter: one per requirement the step lists, each carrying the step's
    fields — a literal as written, a `<recipe>` resolved from that
    requirement's row in verdicts.json. A field the step omits is omitted
    here, so the door's refusal is the step's own."""
    listed = args.split("per requirement (", 1)[1].split(")", 1)[0].split(", ")
    fields = _SYNC_FIELD.findall(args.split("findings=", 1)[1])
    rows = {
        row.get("id"): row
        for row in json.loads(
            (foundry_state.get_run_dir(root) / "verdicts.json").read_text(
                encoding="utf-8",
            )
        )["requirements"]
    }
    findings = []
    for rid in listed:
        row = rows[rid]
        recipes = {
            "that requirement id": rid,
            "that requirement's verdict": row["verdict"],
        }
        finding = {}
        for key, literal, recipe in fields:
            if recipe.startswith("that requirement's verdict and evidence"):
                finding[key] = f"{row['verdict']}: {row.get('evidence', '')}"
            else:
                finding[key] = literal if not recipe else recipes[recipe]
        findings.append(finding)
    return findings


def _post_assay_run(root: str, fdir: Path, *, phase: str, temper: bool,
                    nyquist: bool) -> None:
    from tests.orchestration._env import (
        _committed_evidence, _evidence_repo, _write_verdicts,
    )

    _evidence_repo(root)
    _committed_evidence(root, "casting-1-handler.log", "echo reproduces",
                        "reproduces\n")
    _write_spec(fdir, ["FR-1"])
    _write_state(fdir, phase=phase, cycle=1, temper=temper, nyquist=nyquist)
    _write_verdicts(fdir, [{"requirement_id": "FR-1", "verdict": "VERIFIED"}])
    _defect_ledger(fdir, [])


_DONE_LIST = ["Foundry-Report", "Foundry-Gate", "Bash", "Bash", "Foundry-Phase"]


@pytest.mark.parametrize(
    "temper, nyquist, owed",
    [
        # B1..B9 of the drive: F4 -> F5 -> F6 on a --temper run.
        (True, False, [
            ("F4", "transition_to_temper", ["Foundry-Gate", "Foundry-Phase"]),
            ("F5", "run_temper", ["Skill", "Foundry-Report", "Foundry-Gate"]),
            ("F5", "transition_to_done", _DONE_LIST),
        ]),
        # drive_nyquist_exit.py: the --temper --nyquist composition.
        (True, True, [
            ("F4", "transition_to_temper", ["Foundry-Gate", "Foundry-Phase"]),
            ("F5", "run_temper", ["Skill", "Foundry-Report", "Foundry-Gate"]),
            ("F5", "transition_to_nyquist", ["Foundry-Gate", "Foundry-Phase"]),
            ("F5.5", "run_nyquist", ["Agent", "Foundry-Report", "Foundry-Gate"]),
            ("F5.5", "transition_to_done", _DONE_LIST),
        ]),
        # lead-stalls D-052 — and neither flag: F4 -> F6 by the list whose
        # CONTEXT used to open on the gate its report must precede.
        (False, False, [
            ("F4", "transition_to_done", _DONE_LIST),
        ]),
        # and --nyquist alone, which reaches F5.5 from F4.
        (False, True, [
            ("F4", "transition_to_nyquist", ["Foundry-Gate", "Foundry-Phase"]),
            ("F5.5", "run_nyquist", ["Agent", "Foundry-Report", "Foundry-Gate"]),
            ("F5.5", "transition_to_done", _DONE_LIST),
        ]),
    ],
    ids=["temper", "temper-nyquist", "done", "nyquist"],
)
def test_a_post_assay_run_reaches_f6_by_the_served_lists_alone(
    run_env, temper, nyquist, owed,
):
    """lead-stalls GI-008 / FR-007 (D-050): 'the LEAD always receives one
    unconditional imperative', and no sequence of them reached F6 from F5."""
    root, fdir = run_env
    _post_assay_run(root, fdir, phase="F4", temper=temper, nyquist=nyquist)

    served = _walk_served_lists(root)

    assert served[: len(owed)] == owed, served
    state = json.loads((fdir / "state.json").read_text(encoding="utf-8"))
    assert state["phase"] == "F6", served


@pytest.mark.parametrize(
    "phase, temper, nyquist, work",
    [("F5", True, False, "run_temper"), ("F5.5", False, True, "run_nyquist")],
    ids=["temper", "nyquist"],
)
def test_what_the_phase_work_files_is_served_the_grind_crossing(
    run_env, phase, temper, nyquist, work,
):
    """B6: a blocking defect filed during the phase's work refuses its gate,
    and the Foundry-Next owed after that refusal is the GRIND crossing — the
    `transition_to_grind` list, whose `grind_start` enters F3 from here."""
    root, fdir = run_env
    _post_assay_run(root, fdir, phase=phase, temper=temper, nyquist=nyquist)

    served = _walk_served_lists(root, filed_by_phase_work=fdir)

    assert [action for _p, action, _c in served[:2]] == [
        work, "transition_to_grind",
    ], served
    assert served[1][2][:3] == [
        "Foundry-Tasks", "Foundry-Gate", "Foundry-Phase",
    ], served
    state = json.loads((fdir / "state.json").read_text(encoding="utf-8"))
    assert state["phase"] == "F3", served


@pytest.mark.parametrize(
    "temper, nyquist",
    [(False, False), (True, False), (False, True), (True, True)],
    ids=["done", "temper", "nyquist", "temper-nyquist"],
)
def test_a_blocking_defect_beside_a_passing_assay_leaves_f4_by_the_served_list(
    run_env, temper, nyquist,
):
    """lead-stalls D-058, driven as PROVE drove it (drive_s9): F4, every
    requirement VERIFIED, and one open LIVE RESEARCH_DEVIATION an assayer
    filed. At 62d2834 the list served was the flags' crossing:
    `transition_to_temper`, whose `temper` gate refused the open defect, or
    `transition_to_done`, whose strip and commit a lead following the list ran
    after the DONE gate refused. The next Foundry-Next served the same list on
    every lap, and the exit (Tasks, the GRIND gate and `grind_start`) was named
    only in a refusal hint. That exit is the served list now. Following it
    alone enters F3, and the evidence corpus is untouched."""
    root, fdir = run_env
    _post_assay_run(root, fdir, phase="F4", temper=temper, nyquist=nyquist)
    _defect_ledger(fdir, [_tiered(
        "D-001", "LIVE", source="assay", type="RESEARCH_DEVIATION",
    )])

    served = _walk_served_lists(root, lists=1)

    assert served[0][:2] == ("F4", "transition_to_grind"), served
    assert served[0][2][:3] == [
        "Foundry-Tasks", "Foundry-Gate", "Foundry-Phase",
    ], served
    state = json.loads((fdir / "state.json").read_text(encoding="utf-8"))
    assert state["phase"] == "F3", served
    assert (Path(root) / "evidence" / "casting-1-handler.log").exists()


def test_every_arm_past_assay_asks_the_one_grind_question():
    """lead-stalls D-058 — F5 and F5.5 read the blocking count before their
    crossing, through `_post_assay_crossing`, and F4 did not. The GRIND answer
    for a blocking defect past ASSAY now has one body,
    `_blocking_grind_crossing`, asked by the F4 arm and by
    `_post_assay_crossing`, so no two arms can answer that state in two ways.
    This reads the source, because a copy of the body answers identically
    until it drifts. The only other payloads serving `transition_to_grind`
    are `_escalation_hold`'s clean-cycle GRIND, which has nothing blocking to
    serve, and the F2 arm's, which is before ASSAY."""
    import ast
    import inspect

    from foundry_mcp.tools.orchestration import guidance

    functions = [
        node for node in ast.walk(ast.parse(inspect.getsource(guidance)))
        if isinstance(node, ast.FunctionDef)
    ]
    spelled = {
        func.name
        for func in functions
        for node in ast.walk(func)
        if isinstance(node, ast.Dict) and any(
            isinstance(key, ast.Constant) and key.value == "action"
            and isinstance(value, ast.Constant)
            and value.value == "transition_to_grind"
            for key, value in zip(node.keys, node.values)
        )
    }
    assert spelled == {
        "_blocking_grind_crossing", "_escalation_hold", "_compute_next_action",
    }, spelled
    asked = sorted(
        (func.name, ast.unparse(node.args[0]))
        for func in functions
        for node in ast.walk(func)
        if isinstance(node, ast.Call)
        and getattr(node.func, "id", None) == "_blocking_grind_crossing"
    )
    assert asked == [
        ("_compute_next_action", "'F4'"), ("_post_assay_crossing", "phase"),
    ], asked


def test_the_gate_out_of_f5_is_the_one_the_run_flags_name(run_env):
    """`run_temper` ends in `done` on a --temper run and in `nyquist` on a
    --temper --nyquist one, resolved from `details["crossing"]` by the one
    resolver the header takes — never a literal `{gate}`."""
    root, fdir = run_env
    for nyquist, gate in ((False, "done"), (True, "nyquist")):
        _post_assay(fdir, "F5", temper=True, nyquist=nyquist)
        nxt = foundry_next_action(root)
        assert nxt["action"] == "run_temper", nxt["action"]
        assert nxt["next_calls"][-1]["tool"] == "Foundry-Gate", nxt["next_calls"]
        assert nxt["next_calls"][-1]["args"] == f"phase='{gate}'", nyquist
        assert f"Foundry-Gate(phase='{gate}')" in nxt["instructions"], nyquist
    # Without the row the entry takes the generic header, as an unresolved
    # phase-keyed crossing does, rather than handing the lead `{gate}`.
    steps, header = _emitted_imperative("run_temper", {}, run_name="r", phase="F5")
    assert steps is None and header == _generic_header("run_temper"), header


def test_the_post_assay_context_names_no_call_and_no_condition(run_env):
    """D-050's defect sat in the CONTEXT, so it is judged here directly: at
    every F5 / F5.5 state the router serves, the CONTEXT writes out no call at
    all — the header's list is the move, and the "When clean" tail is gone."""
    root, fdir = run_env
    contexts = {}
    for state, _transitions, arrange, _action, _branch in _ROUTER_STATES:
        if not state.startswith(("temper-", "nyquist-")):
            continue
        arrange(root, fdir, None)
        contexts[state] = _context_of(foundry_next_action(root)["instructions"])
        (fdir / GATE_PASSED_MARKER).unlink(missing_ok=True)
        (fdir / "escalation.json").unlink(missing_ok=True)
    # Eight D-050 states, the two lead-stalls D-055 added: a held class at
    # F5 with nothing open, and at F5.5 with a record open; and the two
    # lead-stalls D-061 added, a filed F5 and F5.5 at the --max-cycles cap.
    assert len(contexts) == 12, sorted(contexts)
    problems = {
        state: _CALL_SYNTAX.findall(text)
        for state, text in contexts.items() if _CALL_SYNTAX.search(text)
    }
    assert problems == {}, problems
    # Positive control: the tail the defect was filed on writes out a call.
    # (The prose backstop does not match it, which is why this checks the
    # call and not the condition.)
    old = "When clean, call Foundry-Gate(phase='done'), update to F6."
    assert _CALL_SYNTAX.findall(old) == ["Foundry-Gate"]
    assert _HANDS_OVER_THE_CONDITION.search(old) is None



# --------------------------------------------------------------------------- #
# lead-stalls GI-008 / FR-007 (D-051..D-053) — EVERY CONTEXT, AND THE THREE
# LISTS ITS SEQUENCES DISAGREED WITH, WALKED
# --------------------------------------------------------------------------- #


def test_every_action_the_router_returns_is_served_by_a_router_state():
    """Floor for the CONTEXT sweep: D-051..D-053 sat at F2 and F4 states no
    row reached, so no detector ever read their CONTEXT. Every action the
    table holds is owed by at least one router state, and each state is driven
    at both clocks by `audit_assembled_payloads`."""
    owed = {action for _s, _t, _a, action, _b in _ROUTER_STATES}
    assert owed == set(_IMPERATIVES), sorted(set(_IMPERATIVES) ^ owed)
    # ...and F4 in every flag combination, both ASSAY-rejection states.
    states = {state for state, *_ in _ROUTER_STATES}
    assert {
        "assay-passed", "assay-passed-temper", "assay-passed-nyquist",
        "assay-passed-both", "assay-failed-filed", "assay-failed-unfiled",
        "inspect-clean-full", "inspect-clean-delta", "decompose-done",
        # lead-stalls D-058 — the F4 state the zero used to be computed without.
        "assay-passed-filed", "assay-passed-temper-filed",
        "assay-passed-nyquist-untiered",
        # lead-stalls D-060 — the short ledger, in every flag combination.
        "assay-short", "assay-short-temper", "assay-short-nyquist",
        "assay-short-both", "assay-short-prove-filed",
        # lead-stalls D-061 — the GRIND crossing at the cap, F2 to F5.5.
        "inspect-filed-capped", "assay-passed-filed-capped",
        "temper-filed-capped", "nyquist-filed-capped",
    } <= states, sorted(states)


def test_the_heading_the_context_sweep_removes_is_the_one_printed():
    """`_DISTANCES_HEADING` is removed before the order judgement, so it must
    be the heading the convergence ST-010 notice really prints — at both widths — and
    nothing else in that CONTEXT may open as an order."""
    for arrange in (_arrange_inspect_escalated_full, _arrange_inspect_escalated_delta):
        context = drive_router(arrange, None)["context"]
        assert context.count(_DISTANCES_HEADING) == 1, context
        assert _order_openers(context.replace(_DISTANCES_HEADING, " ")) == [], context
        assert _CALL_SYNTAX.findall(context) == [], context


def test_the_context_sweep_bites_on_the_three_filed_contexts():
    """Positive control: each CONTEXT the three defects were filed on, as it
    shipped at 810009d, is flagged by the sweep that now reads every payload."""
    d051 = (
        "INSPECT clean: zero blocking defects, at FULL width (rule "
        "final_gate). Call Foundry-Phase(phase='inspect_clean'), then "
        "Foundry-Gate(phase='assay'). Spawn 4 parallel assayer agents using "
        "the config below."
    )
    d052 = (
        "ASSAY passed: all requirements verified. Call "
        "Foundry-Gate(phase='done'), update state to F6. Generate report, "
        "append lessons, archive."
    )
    d053 = (
        "ASSAY found 1/2 non-verified requirements. Sync findings as defects "
        "(Foundry-Sync), call Foundry-Gate(phase='grind') then "
        "Foundry-Phase(phase='assay_fail') — the ASSAY-rejection door into F3."
    )
    for text in (d051, d052, d053):
        drive = {
            "action": "transition_to_done", "branch": None, "branched": False,
            "block": "", "header": "", "context": text,
            "agent_liveness": None, "waiting_on_agents": None,
        }
        findings = audit_assembled_payloads(
            [("payload:x[fresh]", "x", "GI-008", "transition_to_done", None, drive)]
        )
        kinds = {f.split(": ", 1)[1].split(" ", 1)[0] for f in findings}
        assert {"CONTEXT_SEQUENCE", "CONTEXT_CALL"} <= kinds, (text, findings)


def test_the_assay_crossing_is_walked_in_its_header_order(run_env):
    """D-051, driven: following the CONTEXT's order the Phase call passed and
    the Gate was then refused. The served list, followed alone from a clean
    FULL F2, enters F4."""
    root, fdir = run_env
    _post_assay_run(root, fdir, phase="F2", temper=False, nyquist=False)
    _clean_inspect(fdir, "FULL")

    served = _walk_served_lists(root, lists=1)

    assert served[0] == ("F2", "transition_to_assay", [
        "Foundry-Gate", "Foundry-Phase", "Agent",
    ]), served
    state = json.loads((fdir / "state.json").read_text(encoding="utf-8"))
    assert state["phase"] == "F4", served


def test_an_unfiled_assay_rejection_leaves_f4_by_the_served_list(run_env):
    """D-053, driven: a non-VERIFIED verdict with no defect filed. The list
    served was Tasks, Gate(grind), Phase(assay_fail); Tasks returned nothing,
    the gate refused "No open defects to grind", and the next Foundry-Next
    served the same list, so the run never left F4. The list now opens by
    filing ASSAY's findings and the run enters F3 on it alone."""
    root, fdir = run_env
    _post_assay_run(root, fdir, phase="F4", temper=False, nyquist=False)
    _assay(fdir, ["VERIFIED", "PARTIAL"])

    served = _walk_served_lists(root)

    assert served[0] == ("F4", "assay_failed_loop_back", [
        "Foundry-Sync", "Foundry-Tasks", "Foundry-Gate", "Foundry-Phase",
    ]), served
    state = json.loads((fdir / "state.json").read_text(encoding="utf-8"))
    assert state["phase"] == "F3", served
    defects = json.loads((fdir / "defects.json").read_text(encoding="utf-8"))
    assert [d.get("spec_ref") for d in defects["defects"]] == ["FR-2"], defects


def test_a_filed_assay_rejection_is_not_served_a_second_filing(run_env):
    """D-053's other state, and lead-stalls D-054's correction of it: a
    rejection is FILED when an open BLOCKING defect's `spec_ref` names that
    requirement — per requirement, never "some record is open".

    This test used to hand the arm an open D-001 carrying FR-1, have ASSAY
    reject FR-2, and assert the state was "filed". That is the gap PROVE drove:
    the ledger-wide count read any open record as ASSAY's filing, so FR-2 was
    carried by nothing, the gate passed on D-001, the GRIND dispatched nobody
    and ASSAY rejected FR-2 again on the next lap.
    """
    root, fdir = run_env
    # A LIVE defect naming the rejected requirement carries it: no filing.
    _assay(fdir, ["VERIFIED", "PARTIAL"])
    _defect_ledger(fdir, [_tiered("D-001", "LIVE", spec_ref="FR-2")])
    nxt = foundry_next_action(root)
    assert nxt["action"] == "assay_failed_loop_back", nxt["action"]
    assert nxt["details"]["unfiled_verdicts"] == [], nxt["details"]
    assert nxt["details"]["carrying_defects"] == ["D-001"], nxt["details"]
    assert [c["tool"] for c in nxt["next_calls"]] == [
        "Foundry-Tasks", "Foundry-Gate", "Foundry-Phase",
    ]
    context = _context_of(nxt["instructions"])
    assert "1 of them are carried by open blocking defect(s) (D-001)" in context, context
    # So does one naming it inside a list of ids.
    _defect_ledger(fdir, [_tiered("D-001", "LIVE", spec_ref="GI-008, FR-2")])
    assert foundry_next_action(root)["details"]["unfiled_verdicts"] == []

    # Nothing else carries it: a LATENT defect naming it (the GRIND dispatches
    # nobody for LATENT), an unrelated LATENT or LIVE one, a HARDENING one.
    hardening = _tiered("D-001", "HARDENING", reproduction_attempted="probe")
    hardening.pop("spec_ref")
    for record in (
        _tiered("D-001", "LATENT", spec_ref="FR-2", reproduction_attempted="AST sweep finds 0 sites"),
        _tiered("D-001", "LATENT", spec_ref="FR-1", reproduction_attempted="AST sweep finds 0 sites"),
        _tiered("D-001", "LIVE", spec_ref="FR-1"),
        _tiered("D-001", "LIVE", spec_ref="FR-20"),
        hardening,
    ):
        _defect_ledger(fdir, [record])
        nxt = foundry_next_action(root)
        assert nxt["details"]["unfiled_verdicts"] == ["FR-2"], (record, nxt["details"])
        assert [c["tool"] for c in nxt["next_calls"]] == [
            "Foundry-Sync", "Foundry-Tasks", "Foundry-Gate", "Foundry-Phase",
        ], record
        assert "(FR-2)" in nxt["next_calls"][0]["args"], nxt["next_calls"][0]
        assert "carry ASSAY's findings" not in nxt["instructions"], record
        # The step's own reason is the per-requirement one: the GRIND gate
        # counting open records is exactly what let the unrelated one pass.
        note = nxt["next_calls"][0]["note"]
        assert "no open blocking defect carries them" in note, note
        assert "counts open defects" not in note, note

    # PROVE's s1b: FR-2 filed, FR-3 not — the filing names FR-3 alone, and the
    # CONTEXT's counts are the same comparison's.
    _assay(fdir, ["VERIFIED", "PARTIAL", "WRONG"])
    _defect_ledger(fdir, [_tiered("D-002", "LIVE", spec_ref="FR-2", source="assay")])
    nxt = foundry_next_action(root)
    assert nxt["details"]["unfiled_verdicts"] == ["FR-3"], nxt["details"]
    assert "(FR-3)" in nxt["next_calls"][0]["args"], nxt["next_calls"][0]
    context = _context_of(nxt["instructions"])
    assert "1 of them are carried by open blocking defect(s) (D-002)" in context, context
    assert "1 are carried by no open blocking defect (FR-3)" in context, context

    # And the filing names exactly the non-VERIFIED requirements when none is.
    _assay(fdir, ["PARTIAL", "VERIFIED", "HOLLOW"])
    nxt = foundry_next_action(root)
    assert nxt["details"]["unfiled_verdicts"] == ["FR-1", "FR-3"], nxt["details"]
    assert "(FR-1, FR-3)" in nxt["next_calls"][0]["args"], nxt["next_calls"][0]


def test_an_unrelated_backlog_item_no_longer_swallows_the_filing(run_env):
    """lead-stalls D-054, driven the way PROVE drove it (s1): an open LATENT
    D-001 on FR-1 beside FR-2 PARTIAL with nothing filed. At 6c350c0 the list
    was Tasks, Gate, Phase, the GRIND that followed dispatched nobody, and two
    full laps later FR-2 had still not reached the ledger. Following the
    served list now files FR-2 as a LIVE defect, enters F3, and the F3 answer
    is the dispatch of a GRIND that has it to fix."""
    root, fdir = run_env
    _post_assay_run(root, fdir, phase="F4", temper=False, nyquist=False)
    _arrange_assay_failed_unrelated_latent(root, fdir, None)

    served = _walk_served_lists(root, lists=2)

    assert served[0] == ("F4", "assay_failed_loop_back", [
        "Foundry-Sync", "Foundry-Tasks", "Foundry-Gate", "Foundry-Phase",
    ]), served
    assert served[1][:2] == ("F3", "fix_defects"), served
    defects = json.loads((fdir / "defects.json").read_text(encoding="utf-8"))
    filed = [d for d in defects["defects"] if d.get("spec_ref") == "FR-2"]
    assert [(d.get("tier"), d.get("status")) for d in filed] == [("LIVE", "open")], defects


def test_a_held_class_with_a_record_open_closes_a_clean_cycle_by_the_served_lists(
    run_env,
):
    """lead-stalls D-055, the half the server's graph already allowed: a class
    persisted ESCALATED with its LATENT instances open. The CONTEXT used to
    name `grind_start` then `inspect_start` beside a header serving ASSAY.

    lead-stalls D-057 — past ASSAY that is still the crossing: the header
    serves Tasks, the GRIND gate and `grind_start` with no teammate to
    dispatch, and the F3 answer after it is `inspect_start`, which advances
    the counter and moves the clean arm. At a clean FULL F2 the re-open is
    one crossing where this was two, so F2 is served that instead."""
    root, fdir = run_env
    _post_assay_run(root, fdir, phase="F4", temper=False, nyquist=False)
    _write_state(fdir, phase="F4", cycle=4)
    _record_full_inspect_mode(fdir, cycle=4)
    _escalated(fdir, tier="LATENT")

    served = _walk_served_lists(root, lists=1)
    # At F3 nothing blocks and nothing was dispatched: the CONTEXT says so,
    # rather than "all defects fixed" over a LATENT backlog still open.
    context = _context_of(foundry_next_action(root)["instructions"])
    assert "all defects fixed" not in context, context
    assert "no blocking defect is open" in context, context
    assert "3 LATENT defect(s) stay open and block nothing" in context, context
    served += _walk_served_lists(root, lists=1)

    assert served == [
        ("F4", "transition_to_grind", ["Foundry-Tasks", "Foundry-Gate", "Foundry-Phase"]),
        ("F3", "transition_to_inspect", ["Foundry-Gate", "Foundry-Phase"]),
    ], served
    state = json.loads((fdir / "state.json").read_text(encoding="utf-8"))
    assert (state["phase"], state["cycle"]) == ("F2", 5), state
    entry = json.loads((fdir / "escalation.json").read_text(encoding="utf-8"))["classes"]["FDC"]
    assert entry["live_clean_cycles_counted"] == [4], entry


@pytest.mark.parametrize(
    "phase, temper, nyquist",
    [("F4", False, False), ("F4", True, False),
     ("F5", True, False), ("F5.5", True, True)],
    ids=["f4", "f4-temper", "f5", "f5.5"],
)
def test_a_held_class_with_nothing_open_is_answered_none_not_a_refused_list(
    run_env, phase, temper, nyquist,
):
    """lead-stalls D-055, the half the graph does not allow. With every
    instance fixed, the lists served at 6c350c0 — `transition_to_done` (whose
    strip and commit ran past the refused gate) or `run_temper` (one whole
    TEMPER per lap) — each ended in a DONE gate that refused the class,
    forever. From here every crossing that would advance the counter is
    refused, which this test drives at the real doors, so the honest answer is
    the one the header now gives: NONE, and why. The CONTEXT names no crossing
    to make, and the evidence corpus is untouched.

    lead-stalls D-057 — PAST ASSAY ONLY. The clean FULL F2 this test also
    drove is re-opened now (see the walk below), so NONE is the answer only
    where the graph really has no exit, and the header and CONTEXT say so
    without claiming a FULL INSPECT has nothing to widen."""
    from foundry_mcp.tools.orchestration.gates import foundry_gate
    from foundry_mcp.tools.orchestration.transitions import (
        foundry_mark_phase_complete,
    )

    root, fdir = run_env
    _post_assay_run(root, fdir, phase=phase, temper=temper, nyquist=nyquist)
    _escalated_fixture(fdir, open_instances=False)

    nxt = foundry_next_action(root)

    assert nxt["action"] == "escalation_held", nxt["action"]
    assert nxt["next_calls"] == [], nxt["next_calls"]
    assert "YOUR NEXT CALL: NONE" in nxt["instructions"]
    assert "past ASSAY with no defect open" in nxt["instructions"]
    assert nxt["details"]["still_escalated_classes"] == ["FDC"], nxt["details"]
    context = _context_of(nxt["instructions"])
    for token in ("grind_start", "inspect_start", "inspect_clean", "cheapest"):
        assert token not in context, (token, context)
    assert "nothing to widen" not in context, context
    assert "accepted from a clean FULL F2 and not from past ASSAY" in context, context
    assert "`escalation-override: FDC`" in context, context
    # The served list walks nowhere, so nothing strips the corpus.
    served = _walk_served_lists(root, lists=2)
    assert all(action == "escalation_held" for _p, action, _c in served), served
    assert (Path(root) / "evidence" / "casting-1-handler.log").exists()
    # And the state really is one no accepted transition leaves.
    for gate, token in (("grind", "grind_start"), ("inspect_start", "inspect_start")):
        assert foundry_gate(gate, project_root=root)["passed"] is False, gate
        assert foundry_mark_phase_complete(token, project_root=root).get("ok") is not True, token
    state = json.loads((fdir / "state.json").read_text(encoding="utf-8"))
    assert state["phase"] == phase, state


@pytest.mark.parametrize(
    "open_instances", [False, True], ids=["nothing-open", "latent-open"],
)
def test_a_held_class_at_a_clean_full_f2_is_walked_to_done_by_the_served_lists(
    run_env, open_instances,
):
    """lead-stalls D-057, driven end to end through the real doors: a class
    persisted ESCALATED (at cycle 3) at a clean FULL F2, cycle 4. At 3a24e45
    this was `escalation_held`, NONE, and every crossing that would advance the
    counter was refused. Following only what Foundry-Next serves — every step
    of each list, and every stream the INSPECT it opens requires — the run now
    re-opens INSPECT (cycle 4 closes, one clean cycle), re-opens it again once
    that INSPECT has run (cycle 5 closes, the class CLEARS), opens ASSAY and
    seals DONE with the DONE gate passing. With the class's LATENT instances
    open the walk is the same: no GRIND is opened on work nobody is
    dispatched for."""
    root, fdir = run_env
    _post_assay_run(root, fdir, phase="F2", temper=False, nyquist=False)
    _clean_inspect(fdir, "FULL")
    _write_state(fdir, phase="F2", cycle=4)
    _record_full_inspect_mode(fdir, cycle=4)
    if open_instances:
        _escalated(fdir, tier="LATENT")
    else:
        _escalated_fixture(fdir, open_instances=False)

    served = _walk_served_lists(root, lists=10, record_streams=True)

    reopen = ("F2", "widen_inspect", ["Foundry-Gate", "Foundry-Phase"])
    assert served[0] == reopen, served
    assert served[1][:2] == ("F2", "run_streams"), served
    assert served[2] == reopen, served
    assert served[3][:2] == ("F2", "run_streams"), served
    assert served[4] == ("F2", "transition_to_assay", ["Foundry-Gate", "Foundry-Phase", "Agent"]), served
    assert served[5] == ("F4", "transition_to_done", _DONE_LIST), served
    # The DONE list's own `done` transition sealed and archived the run, so the
    # Foundry-Next after it finds no active run.
    assert served[6][:2] == ("none", "init"), served
    assert "escalation_held" not in [action for _p, action, _c in served], served

    entry = json.loads((fdir / "escalation.json").read_text(encoding="utf-8"))["classes"]["FDC"]
    assert (entry["status"], entry["exit_reason"]) == ("CLEARED", "clean_cycles"), entry
    assert entry["live_clean_cycles_counted"] == [4, 5], entry
    assert entry["cleared_at_cycle"] == 6, entry
    state = json.loads((fdir / "state.json").read_text(encoding="utf-8"))
    assert state["phase"] == "F6", state


def test_the_held_class_leaves_the_route_once_it_is_cleared(run_env):
    """The discrimination, so the hold is not a blanket stop: the same F4
    state with the class CLEARED is served its ordinary crossing, and a DELTA
    F2 with the class held keeps the widening re-open, which advances the
    counter itself."""
    root, fdir = run_env
    _post_assay_run(root, fdir, phase="F4", temper=False, nyquist=False)
    _escalated_fixture(fdir, open_instances=False)
    entry = json.loads((fdir / "escalation.json").read_text(encoding="utf-8"))
    entry["classes"]["FDC"].update(status="CLEARED", exit_reason="clean_cycles")
    (fdir / "escalation.json").write_text(json.dumps(entry), encoding="utf-8")
    assert foundry_next_action(root)["action"] == "transition_to_done"

    _arrange_inspect_escalated_delta(root, fdir, None)
    nxt = foundry_next_action(root)
    assert nxt["action"] == "widen_inspect", nxt["action"]
    assert "widening re-open the list above makes is one of those crossings" in (
        _context_of(nxt["instructions"])
    )


def test_a_held_full_f2_is_told_why_it_re_opens_rather_than_opens_assay(run_env):
    """lead-stalls D-057 — the payload the lead reads at a clean FULL F2 with a
    class held. The header's list is the DELTA re-open's, so its note may not
    say "The DELTA cycle came back clean" over a FULL cycle, and the CONTEXT
    states the FULL arm's reason — the DONE gate refuses the class — rather
    than the DELTA arm's width sentence."""
    root, fdir = run_env
    _arrange_inspect_escalated_closed(root, fdir, None)

    nxt = foundry_next_action(root)

    assert nxt["action"] == "widen_inspect", nxt["action"]
    note = nxt["next_calls"][1]["note"]
    assert "The DELTA cycle came back clean" not in note, note
    assert "the re-open this clean INSPECT owes before ASSAY" in note, note
    context = _context_of(nxt["instructions"])
    assert context.strip().startswith(
        "INSPECT clean: zero blocking defects, at FULL width"
    ), context
    assert "The DONE gate refuses every class still ESCALATED" in context, context
    assert "ST-001's clean arm counts the cycle it closes" in context, context
    assert "at DELTA width" not in context, context
    assert nxt["details"]["still_escalated_classes"] == ["FDC"], nxt["details"]
    assert nxt["details"]["inspect_mode"] == "FULL", nxt["details"]


def test_the_router_and_the_re_open_door_read_one_held_class_union():
    """lead-stalls D-057 — the router serves the F2 re-open when a class is
    held, and `transitions._inspect_start_preconditions` accepts it when a
    class is held. Two spellings of "held" is how the router would serve a
    re-open the door refuses, so both read the ONE function, in the leaf both
    layers may import (fallout GI-033), and no second body of it survives in either."""
    from foundry_mcp.tools.orchestration import escalation, guidance, transitions

    assert guidance._still_escalated_classes is escalation._still_escalated_classes
    assert transitions._still_escalated_classes is escalation._still_escalated_classes


def test_the_escalation_hold_is_asked_only_past_assay():
    """lead-stalls D-057 — `_escalation_hold` answers `escalation_held` (NONE)
    with nothing open, which is honest only where no transition leaves: past
    ASSAY. At F2 the re-open is accepted, so the F2 arm answers every held
    class itself and never asks the hold; a call site passing F2 would put
    NONE back on a state the server now leaves. Read off the router's source,
    because the arm it forbids is one no fixture reaches while the re-open
    branch above it holds."""
    import ast
    import inspect
    import textwrap

    from foundry_mcp.tools.orchestration import guidance

    tree = ast.parse(textwrap.dedent(inspect.getsource(guidance._compute_next_action)))
    phases = [
        node.args[2].value
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and getattr(node.func, "id", None) == "_escalation_hold"
    ]
    assert sorted(phases) == ["F4", "F5", "F5.5"], phases


def test_a_decomposition_is_not_validated_while_a_writer_is_still_writing(
    run_env,
):
    """lead-stalls D-056, driven as PROVE drove it (s3). Two writers; writer A
    finishes (casting 1 in the manifest, its prompt file, its done line) while
    writer B is still writing, and A's notification wakes the lead. At
    6c350c0 that Foundry-Next answered `transition_to_cast`, "DECOMPOSE
    complete: 1 casting(s)", and following it entered F1 with B's domain
    missing. The woken lead is answered END YOUR TURN now, and B's own
    notification is the one whose Foundry-Next opens the validation."""
    root, fdir = run_env
    _arrange_decompose_empty(root, fdir, None)
    nxt = foundry_next_action(root)
    assert (nxt["action"], [c["tool"] for c in nxt["next_calls"]]) == (
        "add_castings", ["Agent", _END_TURN],
    ), nxt["action"]

    _decompose_ledger(fdir, "api", done=False)
    _decompose_ledger(fdir, "ui", done=False)
    _write_manifest_with_castings(fdir, ["src/api/a.py"], no_ui=True)
    _decompose_ledger(fdir, "api", done=True)
    woken_by_a = foundry_next_action(root)
    assert woken_by_a["action"] == "add_castings", woken_by_a["action"]
    assert woken_by_a["next_calls"] == [], woken_by_a["next_calls"]
    assert "END YOUR TURN" in woken_by_a["instructions"]
    assert woken_by_a["agent_liveness"]["waiting"] is True
    assert "DECOMPOSE complete" not in woken_by_a["instructions"]

    _decompose_ledger(fdir, "ui", done=True)
    woken_by_b = foundry_next_action(root)
    assert woken_by_b["action"] == "transition_to_cast", woken_by_b["action"]
    assert woken_by_b["next_calls"][0]["tool"] == "Foundry-Validate-Castings"


def test_a_halt_without_its_report_is_served_the_report_and_nothing_else(run_env):
    """The halted payload's CONTEXT ordered Foundry-Report beneath a header
    reading NONE and "The report is generated." The call is the header's one
    step now, and the rules block no longer asserts the missing report."""
    root, fdir = run_env
    _halted_run(fdir)
    missing = foundry_next_action(root)
    assert [c["tool"] for c in missing["next_calls"]] == ["Foundry-Report"]
    assert "The report is generated" not in missing["instructions"]
    assert "The report has been generated" not in missing["instructions"]
    assert "Foundry-Report" not in _context_of(missing["instructions"])

    (fdir / "REPORT.md").write_text("# report\n", encoding="utf-8")
    written = foundry_next_action(root)
    assert written["next_calls"] == [], written["next_calls"]
    assert "YOUR NEXT CALL: NONE" in written["instructions"]
    assert "The report has been generated" in written["instructions"]



# --------------------------------------------------------------------------- #
# lead-stalls GI-008 / FR-007 (D-060 / D-061) — THE SHORT LEDGER AND THE CAP
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "temper, nyquist",
    [(False, False), (True, False), (False, True), (True, True)],
    ids=["done", "temper", "nyquist", "temper-nyquist"],
)
def test_a_short_assay_ledger_is_served_the_assay_and_then_leaves_f4(
    run_env, temper, nyquist,
):
    """lead-stalls D-060, driven as PROVE drove it (drive_coverage): F4, the
    spec declares FR-1 and FR-2, verdicts.json holds FR-1 VERIFIED, nothing
    is open. At 3184d1c the list served was the flags' crossing: with no
    flag, `transition_to_done`, whose strip and commit a lead following the
    list ran after the DONE gate refused 'Only 1 verdicts but spec has 2
    requirements'; with --temper or --nyquist, a gate with no coverage rung
    and then `run_temper` / `run_nyquist` re-served behind the same refusal.
    The exit — assay the unrecorded id — was only in the refusal hint. It is
    the served list now, on every lap until the verdict is recorded, and once
    the assayer records it through the Foundry-Verdict door the served lists
    alone reach F6."""
    from foundry_mcp.tools.foundry import foundry_add_verdict

    root, fdir = run_env
    _post_assay_run(root, fdir, phase="F4", temper=temper, nyquist=nyquist)
    _write_spec(fdir, ["FR-1", "FR-2"])
    _write_verdicts(fdir, [
        {"id": "FR-1", "verdict": "VERIFIED", "evidence": "read at HEAD"},
    ])

    for _lap in range(2):
        nxt = foundry_next_action(root)
        assert (nxt["phase"], nxt["action"]) == ("F4", "run_assay"), nxt["action"]
        assert [c["tool"] for c in nxt["next_calls"]] == ["Agent"], nxt["next_calls"]
        assert nxt["details"]["unrecorded_requirements"] == ["FR-2"], nxt["details"]
        assert nxt["details"]["spec_requirements"] == 2, nxt["details"]
        assert "FR-2" in _context_of(nxt["instructions"]), nxt["instructions"]

    recorded = foundry_add_verdict(
        "FR-2", "VERIFIED", "read at HEAD", project_root=root,
    )
    assert "error" not in recorded, recorded

    served = _walk_served_lists(root)

    assert all(action != "run_assay" for _p, action, _c in served), served
    state = json.loads((fdir / "state.json").read_text(encoding="utf-8"))
    assert state["phase"] == "F6", served


def test_a_short_ledger_a_clean_prove_fills_is_not_sent_back_to_assay(run_env):
    """The short-ledger answer is asked AFTER the auto-pass: a clean PROVE
    tops the ledger up to every declared id, and the flags' crossing is then
    owed. With a PROVE record that is not clean (PROVE's drive_coverage2
    on-route shape, one HARDENING filing) the ledger stays short and ASSAY's
    list is served."""
    root, fdir = run_env
    _assay_short(fdir, prove_findings=0)
    assert foundry_next_action(root)["action"] == "transition_to_done"
    rows = json.loads((fdir / "verdicts.json").read_text(encoding="utf-8"))
    assert {r["id"] for r in rows["requirements"]} == {"FR-1", "FR-2"}, rows

    _assay_short(fdir, prove_findings=1)
    nxt = foundry_next_action(root)
    assert nxt["action"] == "run_assay", nxt["action"]
    assert nxt["details"]["unrecorded_requirements"] == ["FR-2"], nxt["details"]


def _capped_run(root: str, fdir: Path, phase: str, *, temper: bool,
                nyquist: bool) -> None:
    """A committed evidence corpus, one open LIVE defect, and the cap at the
    cycle the run stands in, at ``phase``."""
    _post_assay_run(root, fdir, phase=phase, temper=temper, nyquist=nyquist)
    if phase == "F2":
        _clean_inspect(fdir, "FULL", filed=True)
    else:
        _defect_ledger(fdir, [_tiered("D-001", "LIVE", status="open")])
    _capped(fdir)


@pytest.mark.parametrize(
    "phase, temper, nyquist",
    [("F2", False, False), ("F4", False, False), ("F5", True, False),
     ("F5.5", False, True)],
    ids=["f2", "f4", "f5", "f5.5"],
)
def test_a_grind_crossing_at_the_cap_ends_at_the_transition_that_seals(
    run_env, phase, temper, nyquist,
):
    """lead-stalls D-061, driven as PROVE drove it (drive_cap): an open
    blocking defect at the --max-cycles cap. At 3184d1c `transition_to_grind`
    served Tasks, the GRIND gate, `grind_start` — which SEALS THE RUN
    HALTED here — and then TeamCreate, Team-Up, Spawn-Teammate and the
    teammate Agent: Team-Up registered a team on the HALTED run and the spawn
    door answered with a dispatch. The list ends at the sealing call now, the
    CONTEXT states the seal, and the next Foundry-Next is the halted answer."""
    root, fdir = run_env
    _capped_run(root, fdir, phase, temper=temper, nyquist=nyquist)
    assert _grind_start_seals(fdir)

    nxt = foundry_next_action(root)
    assert nxt["details"]["seals_halted"] is True, nxt["details"]
    assert nxt["heading_for"] == "HALTED" and nxt["cycles_to_cap"] == 0, nxt
    assert _GRIND_SEALS_AT_CAP.strip() in _context_of(nxt["instructions"])

    served = _walk_served_lists(root, lists=2)

    assert served[0] == (phase, "transition_to_grind", [
        "Foundry-Tasks", "Foundry-Gate", "Foundry-Phase",
    ]), served
    assert [action for _p, action, _c in served] == [
        "transition_to_grind", "halted",
    ], served
    state = json.loads((fdir / "state.json").read_text(encoding="utf-8"))
    assert state["phase"] == "HALTED", served


def test_below_the_cap_the_grind_crossing_still_dispatches(run_env):
    """The control: one cycle short of the cap, and with no cap at all, the
    same state is served the dispatch after `grind_start`, and the CONTEXT
    does not claim a seal."""
    root, fdir = run_env
    _capped_run(root, fdir, "F4", temper=False, nyquist=False)
    for cap in (2, None):
        state = json.loads((fdir / "state.json").read_text(encoding="utf-8"))
        if cap is None:
            state.pop("max_cycles")
        else:
            state["max_cycles"] = cap
        (fdir / "state.json").write_text(json.dumps(state), encoding="utf-8")
        assert not _grind_start_seals(fdir), cap
        nxt = foundry_next_action(root)
        assert nxt["action"] == "transition_to_grind", cap
        assert nxt["details"]["seals_halted"] is False, cap
        tools = [c["tool"] for c in nxt["next_calls"]]
        assert tools[3:5] == ["TeamCreate", "Foundry-Team-Up"], (cap, tools)
        assert _GRIND_SEALS_AT_CAP.strip() not in nxt["instructions"], cap


def test_the_seal_detector_bites_on_the_list_served_before_the_fix():
    """SERVED_PAST_THE_SEAL is not vacuous: the `transition_to_grind` list as
    3184d1c served it at the cap (no ``seals_halted`` published) is a finding
    on a capped run and none below the cap, and the list the router serves
    now is clean at the cap."""
    tools = _mcp_tool_names()
    rules = _guidance._STANDING_CRITICAL_RULES

    def judged(details: dict, capped: bool) -> list[str]:
        calls, header = _emitted_imperative(
            "transition_to_grind", details, run_name=_AUDIT_RUN, phase="F4",
        )
        return [
            finding for finding in judge_next_calls(
                "bite", "transition_to_grind", calls, header, rules, details,
                None, tools, capped=capped,
            )
            if "SERVED_PAST_THE_SEAL" in finding
        ]

    old = {"open_defects": 1}
    assert judged(old, capped=True) == [
        "bite: SERVED_PAST_THE_SEAL — step (3) Foundry-Phase(phase='grind_start') "
        "seals the run HALTED at its cap and 5 step(s) follow it"
    ]
    assert judged(old, capped=False) == []
    assert judged({"open_defects": 1, "seals_halted": True}, capped=True) == []



# --------------------------------------------------------------------------- #
# lead-stalls D-051..D-053 — EVERY SOURCE HUNK, REVERTED ON ITS OWN
# --------------------------------------------------------------------------- #


#: What `hunk_revert_report` reverts and runs by default: this casting's source
#: file, judged by this module. lead-stalls D-057 widened the report to the
#: other two source files that fix touched, each judged by the suites that
#: drive it, so the defaults are the D-051..D-053 report exactly.
_GUIDANCE_REL = "src/foundry_mcp/tools/orchestration/guidance.py"
_THIS_MODULE = "tests/orchestration/test_guidance_imperatives.py"


def _diff_hunks(base: str, head: str | None, path: str) -> list[dict]:
    """The unified-diff hunks of ``path`` between two commits — or, with no
    ``head``, between ``base`` and the working tree: each one's header and
    the text of its new and old sides."""
    import subprocess

    diff = subprocess.run(
        ["git", "diff", "--no-color", "-U3", base, *([head] if head else []),
         "--", f":(top){path}"],
        cwd=Path(__file__).parent, capture_output=True, text=True, check=True,
    ).stdout
    hunks: list[dict] = []
    for line in diff.splitlines():
        if line.startswith("@@"):
            hunks.append({"header": line.split(" @@", 1)[0] + " @@", "lines": []})
        elif hunks and line[:1] in (" ", "+", "-"):
            hunks[-1]["lines"].append(line)
    for hunk in hunks:
        hunk["new"] = "".join(l[1:] + "\n" for l in hunk["lines"] if l[0] in " +")
        hunk["old"] = "".join(l[1:] + "\n" for l in hunk["lines"] if l[0] in " -")
    return hunks


def _without_docstrings(source: str) -> str | None:
    """``source``'s syntax tree with every bare string statement removed —
    docstrings and the like — dumped; ``None`` when it does not parse.

    lead-stalls D-057 — two texts with equal dumps run identically, so a hunk
    whose revert leaves the dump unchanged has no behaviour to revert. The
    line-prefix test this replaces saw comments but not docstrings, so every
    docstring-only hunk was run, went green, and was reported as a code hunk
    no test pinned."""
    import ast

    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None

    class _Strip(ast.NodeTransformer):
        def visit_Expr(self, node):  # noqa: N802 - the ast visitor's own name
            if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                return None
            return node

        def generic_visit(self, node):
            super().generic_visit(node)
            # A body that held only a docstring keeps a statement, as it must.
            if getattr(node, "body", None) == []:
                node.body = [ast.Pass()]
            return node

    return ast.dump(_Strip().visit(tree))


def _run_with(project: Path, rel: str, text: str,
              tests: tuple[str, ...] = (_THIS_MODULE,)) -> list[str]:
    """Copy the project, replace ``rel`` with ``text``, and run ``tests``
    there: one line per failed test, then the counts."""
    import shutil
    import subprocess
    import sys

    with tempfile.TemporaryDirectory() as tmp:
        for part in ("src", "tests"):
            shutil.copytree(
                project / part, Path(tmp) / part,
                ignore=shutil.ignore_patterns("__pycache__"),
            )
        shutil.copy(project / "pyproject.toml", tmp)
        (Path(tmp) / rel).write_text(text, encoding="utf-8")
        out = subprocess.run(
            [sys.executable, "-m", "pytest", "-p", "no:cacheprovider",
             "--no-header", "--color=no", "--tb=no", "-q", "-rfE", *tests],
            cwd=tmp, capture_output=True, text=True,
        ).stdout
    # A test named after `::`; a module that no longer imports is an ERROR
    # naming the file alone, and every test in it is red. With more than one
    # module run, a test is named with its module so two same-named tests in
    # different suites stay two lines.
    qualified = len(tests) > 1
    failed = sorted(
        (
            (line.split(" ", 2)[1].split("::", 1)[0].rsplit("/", 1)[-1] + "::"
             if qualified else "")
            + line.split(" ", 1)[1].split("::", 1)[1].split(" - ", 1)[0]
        )
        if "::" in line else
        "collection of " + line.split(" ", 2)[1].rsplit("/", 1)[-1]
        for line in out.splitlines()
        if line.startswith(("FAILED ", "ERROR "))
    )
    passed = re.search(r"(\d+) passed", out)
    ran = (int(passed.group(1)) if passed else 0) + len(failed)
    return [f"  FAILED {name}" for name in failed] + [
        f"  tests run: {ran}, failed or errored: {len(failed)}"
    ]


def hunk_revert_report(
    base: str, head: str | None, rel: str = _GUIDANCE_REL,
    tests: tuple[str, ...] = (_THIS_MODULE,),
) -> list[str]:
    """lead-stalls D-051..D-053's revert evidence: every hunk of ``rel``
    (`guidance.py` by default) between ``base`` and ``head``, reverted ALONE
    on the current tree, and the tests of ``tests`` that go red for it. A
    hunk whose revert leaves the syntax tree unchanged but for docstrings —
    comments and docstrings only — cannot change behaviour and is named as
    such rather than run (lead-stalls D-057, which also opened ``rel`` and
    ``tests`` to the transition-graph files that fix touched)."""
    from concurrent.futures import ThreadPoolExecutor

    project = Path(__file__).resolve().parents[2]
    current = (project / rel).read_text(encoding="utf-8")
    hunks = _diff_hunks(base, head, f"plugins/foundry/mcp-server/{rel}")
    assert hunks, (base, head)
    for hunk in hunks:
        assert current.count(hunk["new"]) == 1, hunk["header"]
        reverted = current.replace(hunk["new"], hunk["old"])
        hunk["reverted"] = reverted
        tree = _without_docstrings(current)
        hunk["comment_only"] = tree is not None and tree == _without_docstrings(reverted)

    # lead-stalls D-057 — THE COPY'S OWN FAILURES ARE NOT A HUNK'S. A suite
    # that reads files outside `src` and `tests` (the shipped CLIs, the plugin
    # prose) fails in the copy with nothing reverted, and a test that is red
    # before the revert says nothing about the hunk. So the copy is run once
    # unreverted, and only what goes red BEYOND that is charged to a hunk.
    baseline = [
        line for line in _run_with(project, rel, current, tests)
        if line.startswith("  FAILED")
    ]

    def judged(hunk: dict) -> list[str]:
        if hunk["comment_only"]:
            return ["  comment- or docstring-only: no behaviour to revert"]
        result = _run_with(project, rel, hunk["reverted"], tests)
        red = [line for line in result if line.startswith("  FAILED") and line not in baseline]
        ran = result[-1].split(",", 1)[0]
        return red + [f"{ran}, red beyond the baseline: {len(red)}"]

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(judged, hunks))
    lines = [
        f"{Path(rel).name} hunks {base}..{head or 'working tree'}: {len(hunks)}",
        f"comment- or docstring-only: {sum(h['comment_only'] for h in hunks)}",
        f"judged by: {', '.join(tests)}",
        f"red in the copy with nothing reverted, not charged to any hunk: {len(baseline)}",
        *baseline,
        "",
    ]
    for number, (hunk, result) in enumerate(zip(hunks, results), 1):
        first = next(
            (l[1:].strip() for l in hunk["lines"] if l[0] in "+-" and l[1:].strip()),
            "",
        )
        lines.append(f"== hunk {number:02d} {hunk['header']} {first[:60]}")
        lines.extend(result)
    green = [
        f"hunk {n:02d}" for n, (h, r) in enumerate(zip(hunks, results), 1)
        if not h["comment_only"] and not any(l.startswith("  FAILED") for l in r)
    ]
    lines += ["", f"code hunks with no test red when reverted: {', '.join(green) or 'none'}"]
    return lines
