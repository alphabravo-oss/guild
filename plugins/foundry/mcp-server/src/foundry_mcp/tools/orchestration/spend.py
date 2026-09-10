"""The per-agent spend ledger and its roll-ups.

Survey block R. The arithmetic is `foundry_state.spend_rollup`; this owns the
ledger append, the door, and the run's own inputs to that one derivation.
"""
from __future__ import annotations

import json
from pathlib import Path

from foundry_mcp.schemas.vocab import SPEND_LEDGER_FILENAME
from foundry_mcp.tools.artifacts import (
    _artifact_guard,
    _document_transaction,
    _load_json,
)
from foundry_mcp.tools.foundry_state import (
    DISPATCH_PHASE_TO_RUN_PHASE,
    current_cycle,
    get_run_dir,
    now_iso,
    overlay_unreported,
    read_jsonl,
    spend_bucket,
    unreported_dispatch_inputs,
)
from pathlib import Path




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




def _spend_ledger_rows(fdir: Path) -> list[dict]:
    """Every line of `spend.jsonl`, skipping any that will not decode.

    A malformed line is skipped rather than raised on, for the reason every
    reader in this package skips a malformed record: this is advisory data, and
    losing a cost report to one bad line would be a strictly worse outcome than
    a cost report missing one row.

    fallout GI-024 / D-013 (concern C-009) — THE LINE LOOP IS `read_jsonl`'S.
    This spelled its own splitlines / json.loads / isinstance walk, which is the
    third of the three that reader exists to replace, and it had already made
    the asymmetry that reader documents by hand: undecodable BYTES are a
    problem, a torn LINE is skipped. Calling the reader keeps the two rules one
    rule; the `problem`-to-empty degradation is this caller's, because a cost
    report is advisory and refusing one over a corrupt ledger is worse than
    reporting none of it.
    """
    rows, problem = read_jsonl(fdir / SPEND_LEDGER_FILENAME)
    return [] if problem is not None else rows




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


def _dispatch_inputs(fdir: Path) -> dict:
    """The three dispatch ledgers, assembled ONCE by the leaf that owns them.

    fallout GI-024 / D-013 (casting 10's concern C-009) — ONE ASSEMBLY, TWO
    RENDERINGS.
    ---------------------------------------------------------------------
    `foundry_state.unreported_dispatch_pairs` was already the one RULE, and
    what stayed derived twice was the ASSEMBLY of its inputs: this module
    walked `spawns.log`, `spend.jsonl` and the roll-up through `_spawn_rows`,
    `_spend_ledger_rows`, `_stream_roster` and `_stream_dispatch_cycles`, while
    `foundry_report._read_dispatch_summary` walked the same three files again.
    Casting 10 made `foundry_state.unreported_dispatch_inputs` the single
    assembler and repointed the report at it; this is the other caller.

    Returns the reader's own shape —
    ``{dispatch_rows, spend_rows, stream_roster, cycles_of_agent, problem}``.
    ``problem`` names the FIRST unreadable ledger and is the reader's contract,
    not this module's: a caller that degrades reads the empty lists beside it,
    which is what every consumer below does, because a cost report is advisory
    and never refuses.

    THE ROSTER AND THE CYCLE MAP COME OFF ONE WALK of the roll-up, which is
    what keeps them from disagreeing about which cycles a stream ran in — the
    two used to be two walks of one document here.
    """
    return unreported_dispatch_inputs(fdir)




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

    inputs = _dispatch_inputs(fdir)
    return unreported_dispatch_pairs(
        dispatch_rows=inputs["dispatch_rows"],
        stream_roster=inputs["stream_roster"],
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
    cycles = _dispatch_inputs(fdir)["cycles_of_agent"]
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

    inputs = _dispatch_inputs(fdir)
    return unreported_dispatch_summary(
        dispatch_rows=inputs["dispatch_rows"],
        stream_roster=inputs["stream_roster"],
        spend_rows=inputs["spend_rows"],
        phase_of_dispatch=DISPATCH_PHASE_TO_RUN_PHASE,
        agent_id_of=_agent_id_for_casting,
        cycles_of_agent=inputs["cycles_of_agent"],
    )






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
    spend = overlay_unreported(json.loads(json.dumps(spend)), summary)
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

    server_cycle = current_cycle(fdir)
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
        "recorded_at": now_iso(),
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
            spend["total"] = spend_bucket(persisted=True)

        for bucket, agent_count in (
            (
                spend["by_phase"].setdefault(row["phase"], spend_bucket(persisted=True)),
                _distinct_agents(
                    lambda r: str(r.get("phase", "")) == row["phase"]
                ),
            ),
            (
                spend["by_cycle"].setdefault(
                    str(server_cycle), spend_bucket(persisted=True)
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
        # permanent zero `spend_bucket` used to leave there.
        overlay_unreported(spend, _dispatch_summary(fdir))

        state["spend"] = spend
        state["updated_at"] = now_iso()

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
