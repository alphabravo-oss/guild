"""GI-006 / CT-014 / AC-036 — the generated end-of-run report.

`Foundry-Report` writes two documents into the run directory: `REPORT.md` for
the operator and `report.json` for anything mechanical. Both carry the same
eleven sections, in the same order, derived from the run's own ledgers.

WHY THE REPORT IS GENERATED AND NOT WRITTEN
-------------------------------------------
Every prior run in this archive ended with a hand-authored REPORT.md, and the
failure mode was always the same: the sections an author found interesting got
written and the ones that would have been embarrassing got dropped. thunder-
viper's REPORT.md names its 22 cycles but not its per-phase spend; grand-
vulture's names neither. A report whose completeness depends on the author's
mood is not evidence, it is a press release.

So the SECTIONS are generated and the lead may only APPEND. GI-006 states the
rule ("the lead may append prose but cannot omit a section") and
`Foundry-Phase('done')` enforces it by calling `report_status` below — which is
why that function reads `report.json` back off disk rather than trusting
anything this module remembers about what it just wrote.

WHAT THIS MODULE MAY IMPORT
---------------------------
`schemas.vocab` and `tools.foundry_state`, and nothing else from the package.

— at MODULE level. That is not stylistic. `foundry_orchestrator` imports this
module (the `Foundry-Report` tool and the `done` transition both call into it),
and `foundry_spawn` imports `foundry_orchestrator`, so a module-level import of
`foundry_spawn` from here would close a cycle in the import graph — the exact
cycle `foundry_state`'s own leaf-module contract exists to keep open.

A FUNCTION-LOCAL import closes nothing: it runs at call time, when every module
in that chain is already built. `_agent_id_for_casting` uses one, and D-013 is
why it is no longer a hand-typed copy — see that function.

NFR-002: cost is TOKENS and MINUTES. There is no dollar figure, no currency
symbol and no price table anywhere in either output. A second hand-kept price
table is the house anti-pattern this section was written to avoid.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from foundry_mcp.schemas.vocab import (
    CONVERGENCE_TARGET,
    DEFECT_TIER_OR_UNKNOWN,
    ESCALATION_EXIT_REASONS,
    ESCALATION_STATUSES,
    HANDOFF_EVENT_LEAD_FIX,
    INSPECT_MODES,
    REPORT_JSON_FILENAME,
    REPORT_MD_FILENAME,
    REPORT_REQUIRED_SECTIONS,
    RUN_PHASE_HALTED,
    SPEND_LEDGER_FILENAME,
    THUNDER_VIPER_BASELINE,
    TIER_UNKNOWN,
    defect_tier,
)
from foundry_mcp.tools.foundry_state import (
    derive_cycle_count,
    read_document,
    read_jsonl,
    read_text_file,
    unreported_dispatch_pairs,
)

# ---------------------------------------------------------------------------
# Ledger filenames. Named here rather than spelled at each reader so a rename
# is one edit and a typo is a NameError instead of a silently empty section.
# `spend.jsonl` and the two report filenames come from vocab, because those
# three are also spelled by tools outside this module.
# ---------------------------------------------------------------------------
VERDICTS_FILENAME = "verdicts.json"
DEFECTS_FILENAME = "defects.json"
ESCALATION_FILENAME = "escalation.json"
HANDOFFS_FILENAME = "handoffs.jsonl"
SPAWNS_FILENAME = "spawns.log"
STATE_FILENAME = "state.json"
ROLLUP_FILENAME = "stream-rollup.json"


def _agent_id_for_casting(casting_id: int | str) -> str:
    """The ledger agent id for a casting's teammate — the CANONICAL spelling.

    Delegates to `foundry_spawn._agent_id_for_casting`, which owns it because
    it is the door that seeds the progress ledger with it.

    WHY THE IMPORT IS LAZY, AND WHY IT IS AN IMPORT AT ALL (D-013)
    --------------------------------------------------------------
    This was a hand-typed copy, excused in its own docstring as "a DELIBERATE
    second spelling" because a module-level import would close a cycle:
    `foundry_orchestrator` imports this module and `foundry_spawn` imports
    `foundry_orchestrator`. The excuse was sound about the cycle and wrong
    about the remedy — a function-local import runs at CALL time, when every
    module in that chain is already built, so the cycle never forms and the
    second derivation is simply unnecessary.

    It was not harmless. Two derivations of one fact disagreed in production:
    this module keyed a live `spawns.log` row (which carries no `agent` key)
    as `casting-1` while `foundry_orchestrator._dispatched_agents` fell back to
    the bare `casting_id` and keyed the SAME row as `1`. No single
    `Foundry-Spend` call could clear both surfaces, so Foundry-Next and the
    report named different agents as unreported and the operator had no
    spelling that satisfied either.
    """
    from foundry_mcp.tools.foundry_spawn import _agent_id_for_casting as spelling

    return spelling(casting_id)


def _now() -> str:
    return datetime.now(tz=timezone.utc).isoformat(timespec="seconds")


def _refusal(ledger: str, problem: str) -> dict:
    """The house named refusal, naming the ledger that would not read.

    Mirrors `foundry_state.document_refusal` — `ok`, an `error` naming the
    fault and the file, a `hint` naming the action — because a lead who hits
    this at F6 is looking at the same class of fault a spawn door reports at
    F1 and must not have to learn a second story about it.

    The distinction this refusal turns on, and the reason `_read_*` below
    return `(value, problem)` pairs rather than raising: an ABSENT ledger is
    not unreadable. A run that recorded no spend has no `spend.jsonl`, and
    refusing to generate its report would make `Foundry-Phase('done')`
    unreachable for every run that never called `Foundry-Spend`. Absent means
    an empty section; present-and-undecodable means this.
    """
    return {
        "ok": False,
        "error": f"{problem}",
        "hint": (
            f"Repair or delete {ledger} in the run directory, then call "
            f"Foundry-Report again. A corrupt run artifact is never guessed "
            f"at, and a report generated around one would be worse than no "
            f"report: it would look complete."
        ),
        "ledger": ledger,
    }


# ---------------------------------------------------------------------------
# One reader per artifact. Each returns ``(section_value, problem)`` and NEVER
# raises — the shape `measure-run.py::_extract_per_run` established, for the
# same reason: the assembly below has to be able to name every fault it found,
# and an exception can only ever name the first.
# ---------------------------------------------------------------------------


def _read_verdict_matrix(run_dir: Path) -> tuple[dict, str | None]:
    """AC-036 — every requirement id with its verdict, evidence and location."""
    data, problem = read_document(run_dir / VERDICTS_FILENAME)
    if problem is not None:
        return {}, problem
    rows = data.get("requirements")
    if not isinstance(rows, list):
        rows = []
    requirements = []
    by_verdict: dict[str, int] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        verdict = row.get("verdict")
        verdict = verdict if isinstance(verdict, str) else None
        requirements.append(
            {
                "id": row.get("id"),
                "verdict": verdict,
                "evidence": row.get("evidence"),
                "spec_text_cited": row.get("spec_text_cited"),
                "code_location": row.get("code_location"),
                "cycle": row.get("cycle"),
            }
        )
        by_verdict[verdict or "unrecorded"] = by_verdict.get(verdict or "unrecorded", 0) + 1
    return {
        "cycle": data.get("cycle"),
        "count": len(requirements),
        "by_verdict": dict(sorted(by_verdict.items())),
        "requirements": requirements,
    }, None


def _read_defect_sections(run_dir: Path) -> tuple[dict[str, Any], str | None]:
    """Three sections off ONE parse of `defects.json` (FR-051 / NFR-003).

    `defects_by_tier_and_status` cross-tabulates COUNTS and IDS. Carrying the
    ids and not only the counts is what makes the HALTED clause hold without a
    fourth section: a halted run's report has to name every open LIVE and every
    open LATENT defect, and a table of counts names nothing. The ids are here,
    so they are named on every run, halted or not.

    `unknown_tier_defects` is listed SEPARATELY rather than folded in with LIVE
    (FR-051). The gates treat an unknown tier exactly like LIVE — that is the
    correct blocking behaviour and casting 3's to enforce — but a report that
    printed them as LIVE would tell an operator that a stream classified them,
    which is the one thing that did not happen. "Nobody ever tiered this" and
    "a stream drove the door and saw it fail" are different facts and get
    different rows.

    Every member of `DEFECT_TIER_OR_UNKNOWN` is always present, including
    zeros: "0 LATENT defects" is a measurement, and omitting the key would make
    it indistinguishable from the unmeasured case.
    """
    data, problem = read_document(run_dir / DEFECTS_FILENAME)
    if problem is not None:
        return {}, problem
    records = data.get("defects")
    if not isinstance(records, list):
        records = []

    cross: dict[str, dict[str, dict[str, Any]]] = {
        tier: {} for tier in sorted(DEFECT_TIER_OR_UNKNOWN)
    }
    latent_backlog: list[dict] = []
    unknown_rows: list[dict] = []
    for record in records:
        if not isinstance(record, dict):
            continue
        tier = defect_tier(record)
        status = record.get("status")
        status = status if isinstance(status, str) and status else "unrecorded"
        bucket = cross[tier].setdefault(status, {"count": 0, "ids": []})
        bucket["count"] += 1
        bucket["ids"].append(record.get("id"))

        if tier == "LATENT" and status == "open":
            # D-029 — `file` and `symbol` are part of the backlog row, not
            # decoration. NFR-003 makes this list the artifact that carries
            # LATENT work ACROSS runs, and the next run's lead receives it with
            # no defects.json to join against: a row saying only "the delta arm
            # under-selects" names a fault with no location, and re-finding the
            # site costs more than the fix. `source` and `type` come with them
            # because they are what the receiving lead routes the item on.
            latent_backlog.append(
                {
                    "id": record.get("id"),
                    "class": record.get("class"),
                    "file": record.get("file"),
                    "symbol": record.get("symbol"),
                    "source": record.get("source"),
                    "type": record.get("type"),
                    "spec_ref": record.get("spec_ref"),
                    "description": record.get("description"),
                    "reproduction_attempted": record.get("reproduction_attempted"),
                    "cycle": record.get("cycle"),
                }
            )
        if tier == TIER_UNKNOWN:
            unknown_rows.append(
                {
                    "id": record.get("id"),
                    "class": record.get("class"),
                    "description": record.get("description"),
                    "status": status,
                    "cycle": record.get("cycle"),
                }
            )

    totals = {
        tier: sum(b["count"] for b in statuses.values())
        for tier, statuses in cross.items()
    }
    return {
        "defects_by_tier_and_status": {
            "total": len(records),
            "by_tier": totals,
            "cross_tab": {tier: dict(sorted(v.items())) for tier, v in cross.items()},
        },
        "latent_backlog": {
            "open_count": len(latent_backlog),
            "defects": latent_backlog,
        },
        "unknown_tier_defects": {
            "count": len(unknown_rows),
            "defects": unknown_rows,
            "note": (
                "A record with no tier key, or tier null, reads as "
                f"{TIER_UNKNOWN!r}. It blocks the gates exactly like LIVE and "
                "is listed here rather than among the LIVE rows because "
                "nobody ever classified it."
            ),
        },
    }, None


def _read_escalated_classes(run_dir: Path) -> tuple[dict, str | None]:
    """AC-004 — per class: status, exit reason, cleared cycle, packets used."""
    data, problem = read_document(run_dir / ESCALATION_FILENAME)
    if problem is not None:
        return {}, problem
    classes = data.get("classes")
    if not isinstance(classes, dict):
        classes = {}

    rows: list[dict] = []
    by_status = dict.fromkeys(sorted(ESCALATION_STATUSES), 0)
    by_exit_reason = dict.fromkeys(sorted(ESCALATION_EXIT_REASONS), 0)
    for name, entry in sorted(classes.items()):
        if not isinstance(entry, dict):
            continue
        # A class with no `status` predates this release's fields and is
        # reported in the state it was written in — ESCALATED. Defaulting it
        # to CLEARED would silently retire a class nobody ever cleared.
        status = entry.get("status")
        status = status if isinstance(status, str) else "ESCALATED"
        reason = entry.get("exit_reason")
        rows.append(
            {
                "class": name,
                "status": status,
                "exit_reason": reason if isinstance(reason, str) else None,
                "escalated_at_cycle": entry.get("escalated_at_cycle"),
                "cleared_at_cycle": entry.get("cleared_at_cycle"),
                "structural_packets_dispatched": entry.get(
                    "structural_packets_dispatched"
                ),
                "structural_packet_cycles": entry.get("structural_packet_cycles"),
                "live_clean_cycles": entry.get("live_clean_cycles"),
                "open_latent_defect_ids": entry.get("open_latent_defect_ids"),
                "defect_ids": entry.get("defect_ids"),
                "proposal": entry.get("proposal"),
            }
        )
        if status in by_status:
            by_status[status] += 1
        if isinstance(reason, str) and reason in by_exit_reason:
            by_exit_reason[reason] += 1
    return {
        "count": len(rows),
        "by_status": by_status,
        "by_exit_reason": by_exit_reason,
        "classes": rows,
    }, None


def _read_lead_fix_records(run_dir: Path) -> tuple[dict, str | None]:
    """AC-022 / GI-003 — every `lead_fix` handoff the server appended.

    The event token is READ from vocab (`HANDOFF_EVENT_LEAD_FIX`) rather than
    typed as the literal `"lead_fix"`, because the writer in
    `foundry_handoff.py` reads the same constant and a report that spelled it
    itself would silently list nothing the day the token changed.
    """
    records, problem = read_jsonl(run_dir / HANDOFFS_FILENAME)
    if problem is not None:
        return {}, problem
    rows = [
        {
            "handoff_id": r.get("handoff_id"),
            "timestamp": r.get("timestamp"),
            "defect_id": r.get("defect_id"),
            "tier": r.get("tier"),
            "file": r.get("file"),
            "line_count": r.get("line_count"),
            "test": r.get("test"),
            "fix_commit": r.get("fix_commit"),
        }
        for r in records
        if r.get("event") == HANDOFF_EVENT_LEAD_FIX
    ]
    return {"count": len(rows), "records": rows}, None


def _read_state(run_dir: Path) -> tuple[dict, str | None]:
    data, problem = read_document(run_dir / STATE_FILENAME)
    if problem is not None:
        return {}, problem
    return data, None


def _inspect_modes_section(state: dict) -> dict:
    """AC-036 — the FULL/DELTA decision per cycle, with the rule that fired.

    `inspect_modes` is an append-only LIST and one cycle can appear twice: an
    F2 INSPECT and a later F5 one each record their own entry. The per-cycle
    map therefore takes the LAST entry for a cycle, which is C-4's own "the
    current decision is the last entry" rule; `entries` keeps the full history
    beside it so nothing is lost to that collapse.
    """
    entries = state.get("inspect_modes")
    if not isinstance(entries, list):
        entries = []
    per_cycle: dict[str, dict] = {}
    by_mode = dict.fromkeys(sorted(INSPECT_MODES), 0)
    history: list[dict] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        history.append(entry)
        cycle = entry.get("cycle")
        if isinstance(cycle, bool) or not isinstance(cycle, int):
            continue
        per_cycle[str(cycle)] = {
            "cycle": cycle,
            "phase": entry.get("phase"),
            "mode": entry.get("mode"),
            "rule": entry.get("rule"),
            "decided_by": entry.get("decided_by"),
            "required_streams": entry.get("required_streams"),
        }
    for decision in per_cycle.values():
        if decision["mode"] in by_mode:
            by_mode[decision["mode"]] += 1
    return {
        "count": len(per_cycle),
        "by_mode": by_mode,
        "per_cycle": {k: per_cycle[k] for k in sorted(per_cycle, key=_cycle_sort_key)},
        "entries": history,
    }


def _cycle_sort_key(raw: str) -> tuple[int, int | str]:
    """Numeric order for cycle keys, with any non-numeric key sorted after."""
    try:
        return (0, int(raw))
    except (TypeError, ValueError):
        return (1, raw)


def _as_count(value: Any) -> int:
    """A non-negative int, or 0. Bools are not counts (`True` is not 1 here)."""
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return 0
    return value


def _new_spend_bucket() -> dict[str, Any]:
    return {"tokens": 0, "duration_ms": 0, "minutes": 0.0, "agents": 0}


def _read_spend(run_dir: Path, state: dict) -> tuple[dict, str | None]:
    """NFR-002 — tokens and MINUTES per phase, per cycle, and the run total.

    Minutes are reported beside milliseconds because the question an operator
    actually asks is "how long did F3 take", and asking them to divide
    34_620_000 by 60_000 in their head is how a column stops being read.

    There is no third column. NFR-002's whole point is that this run does NOT
    keep a price table: a token count and a wall-clock figure are facts the
    ledger recorded, and a dollar figure would be a per-model rate table
    maintained by hand in a second place — the house anti-pattern, and one
    that is wrong the day a model's price changes.

    The ledger is the authority and `state.json.spend` is carried beside it as
    `state_rollup`, never instead of it: the server writes that roll-up as it
    goes, and the two disagreeing is a fact worth being able to see rather than
    one to resolve silently here.
    """
    records, problem = read_jsonl(run_dir / SPEND_LEDGER_FILENAME)
    if problem is not None:
        return {}, problem

    by_phase: dict[str, dict[str, Any]] = {}
    by_cycle: dict[str, dict[str, Any]] = {}
    total = _new_spend_bucket()
    agents: set[str] = set()
    for entry in records:
        tokens = _as_count(entry.get("tokens"))
        duration_ms = _as_count(entry.get("duration_ms"))
        phase = entry.get("phase")
        cycle = entry.get("cycle")

        buckets = [total]
        if isinstance(phase, str) and phase:
            buckets.append(by_phase.setdefault(phase, _new_spend_bucket()))
        if isinstance(cycle, int) and not isinstance(cycle, bool) and cycle >= 0:
            buckets.append(by_cycle.setdefault(str(cycle), _new_spend_bucket()))
        for bucket in buckets:
            bucket["tokens"] += tokens
            bucket["duration_ms"] += duration_ms
            bucket["agents"] += 1

        agent = entry.get("agent")
        if isinstance(agent, str) and agent:
            agents.add(agent)

    for bucket in (*by_phase.values(), *by_cycle.values(), total):
        bucket["minutes"] = round(bucket["duration_ms"] / 60_000.0, 2)
    total["distinct_agents"] = len(agents)

    state_rollup = state.get("spend")
    return {
        "records": len(records),
        "by_phase": {k: by_phase[k] for k in sorted(by_phase)},
        "by_cycle": {k: by_cycle[k] for k in sorted(by_cycle, key=_cycle_sort_key)},
        "total": total,
        "state_rollup": state_rollup if isinstance(state_rollup, dict) else None,
    }, None


def _read_unreported_dispatches(run_dir: Path) -> tuple[dict, str | None]:
    """AC-034 / CT-013 — dispatched agents with no spend record. ADVISORY.

    "Never a refusal" is the load-bearing half of AC-034 and it is enforced
    here by construction: this section is a list, nothing reads it but the
    report, and no gate in the protocol takes it as an input. An unreported
    dispatch means a lead forgot to call `Foundry-Spend`, which is a gap in the
    measurement, not a defect in the build — and a run that could not reach
    DONE over a missed bookkeeping call would teach the lead to stop measuring.

    Two sources, because neither alone sees every agent. `spawns.log` records
    teammate dispatches and nothing else; the F2 stream roster lives in
    `stream-rollup.json`, and a prover that ran and never reported spend
    appears in no spawn record at all.

    D-047 / D-048 — THE RULE IS NOT DECIDED HERE ANY MORE.
    ------------------------------------------------------
    It moved to `foundry_state.unreported_dispatch_pairs`, which
    `foundry_orchestrator._unreported_dispatches` calls too, so `Foundry-Next`
    and the report cannot answer the same question two ways. D-013 had already
    unified the agent-ID spelling between these two surfaces; what it unified
    was the WRONG rule, adopted verbatim on both sides so that they agreed
    with each other while both disagreed with FR-022:

      * the axis (D-047) — "an agent that reported spend in ANY phase is a
        reported agent" cleared every dispatch of that agent, so an agent
        dispatched at two phases and accounted for at one appeared nowhere.
        FR-022 wants the gap visible PER PHASE.
      * the vocabulary (D-048) — that agent-wide clause was a workaround, not a
        judgement. `spawns.log` records the dispatch VERB (`cast`, `grind`)
        while `Foundry-Spend`'s schema documents the phase as "e.g. F1, F2,
        F3", so the exact pair could never match a teammate dispatch and the
        fallback was the only clause that ever cleared one — while `by_phase`
        grew a bucket keyed `grind`, which is not a phase.

    `DISPATCH_PHASE_TO_RUN_PHASE` is `foundry_orchestrator`'s constant, read
    through a FUNCTION-LOCAL import for the reason the module comment gives:
    that module imports this one, so a module-level import would close a cycle.
    Read, never re-typed — a local copy of the mapping is the same drift in a
    new place. Its absence degrades to "no mapping" rather than raising: the
    verbs then pass through as spelled, which is the pre-fix rendering and a
    visible sign the constant went missing, and this section refuses on nothing
    (AC-034).
    """
    from foundry_mcp.tools import foundry_orchestrator

    spawns, problem = read_jsonl(run_dir / SPAWNS_FILENAME)
    if problem is not None:
        return {}, problem
    spend, problem = read_jsonl(run_dir / SPEND_LEDGER_FILENAME)
    if problem is not None:
        return {}, problem
    rollup, problem = read_document(run_dir / ROLLUP_FILENAME)
    if problem is not None:
        return {}, problem

    stream_roster: dict[str, list[str]] = {}
    cycles = rollup.get("cycles")
    if isinstance(cycles, dict):
        for bucket in cycles.values():
            if not isinstance(bucket, dict):
                continue
            for stream in bucket:
                # The C-6 additions (`inspect_mode`, `stream_scope`,
                # `evidence_sweep`, ...) live in the same bucket as the stream
                # records, so a key whose value is not a stream record is not a
                # stream. Testing the VALUE rather than keeping a denylist of
                # non-stream keys is what stops this from needing an edit every
                # time the roll-up gains a field.
                entry = bucket.get(stream)
                if isinstance(entry, dict) and "records" in entry:
                    stream_roster.setdefault("F2", []).append(str(stream))

    dispatch_phases = getattr(
        foundry_orchestrator, "DISPATCH_PHASE_TO_RUN_PHASE", {}
    )
    missing = unreported_dispatch_pairs(
        dispatch_rows=spawns,
        stream_roster=stream_roster,
        spend_rows=spend,
        phase_of_dispatch=dispatch_phases,
        agent_id_of=_agent_id_for_casting,
    )
    # The DENOMINATOR comes from the same function with an empty spend ledger —
    # "every pair, nothing cleared" — rather than from a second walk of
    # `spawns.log` here. A count and a list that disagreed about what a
    # dispatch IS is the shape this whole section keeps being fixed for.
    dispatched = unreported_dispatch_pairs(
        dispatch_rows=spawns,
        stream_roster=stream_roster,
        spend_rows=[],
        phase_of_dispatch=dispatch_phases,
        agent_id_of=_agent_id_for_casting,
    )
    by_phase: dict[str, list[str]] = {}
    for row in missing:
        by_phase.setdefault(row["phase"], []).append(row["agent"])
    return {
        "count": len(missing),
        "dispatched": len(dispatched),
        "reported": len(dispatched) - len(missing),
        "by_phase": {k: sorted(v) for k, v in sorted(by_phase.items())},
        "note": (
            "Advisory only. An agent listed here was dispatched and never "
            "reported spend for THAT phase; no gate refuses on it (AC-034)."
        ),
    }, None


def _executing_versions_section(state: dict) -> dict:
    """GI-004 / CT-010 — which server actually executed this run."""
    return {
        "server_version": state.get("server_version"),
        "plugin_version": state.get("plugin_version"),
        "server_root": state.get("server_root"),
        "server_commit": state.get("server_commit"),
        "self_target": state.get("self_target"),
    }


def _wall_clock_minutes(run_dir: Path) -> float | None:
    """Minutes between the first and last `handoffs.jsonl` timestamp, or None.

    The handoff ledger is the only artifact stamped across the whole run, so
    its first and last records bound the wall clock. None — never 0.0 — when
    the ledger is absent, empty or carries no parseable timestamp: a run that
    took no measurable time and a run nobody measured are different facts, and
    NFR-001's comparison is unreadable if they print the same.
    """
    records, problem = read_jsonl(run_dir / HANDOFFS_FILENAME)
    if problem is not None or not records:
        return None
    moments: list[datetime] = []
    for record in records:
        raw = record.get("timestamp")
        if not isinstance(raw, str) or not raw:
            continue
        try:
            moments.append(datetime.fromisoformat(raw.replace("Z", "+00:00")))
        except ValueError:
            continue
    if len(moments) < 2:
        return None
    span = (max(moments) - min(moments)).total_seconds()
    return None if span < 0 else round(span / 60.0, 1)


def _archive_metrics(
    run_dir: Path, *, state: dict | None = None, inspect_modes: dict | None = None
) -> dict:
    """NFR-001's four columns, derived from ONE archive. D-037.

    "The report prints both runs side by side (cycles, defects by tier, tokens,
    wall clock)" — four metrics, and only the first had a value on either side
    before this. The other three were computed for the current run and never
    for the baseline, so the comparison the requirement names could not be read
    off the report at all.

    Both columns are derived by THIS function, from whichever archive directory
    they name. That is the D-036 lesson applied to the remaining three metrics
    before it can be re-learned: two columns of one comparison computed by two
    different readers is how the cycle count came to disagree with itself, and
    a side-by-side table is exactly the surface where a half-unit of drift is
    invisible and decisive.

    Every metric is None when its ledger cannot supply it. thunder-viper ran on
    the 4.7.3 server cache and wrote no `spend.jsonl`, so its token column is
    honestly null and stays null — a fabricated 0 would read as "that run cost
    nothing", which is the one thing it certainly did not.
    """
    if state is None:
        state, _problem = read_document(run_dir / STATE_FILENAME)
    if inspect_modes is None:
        inspect_modes = _inspect_modes_section(state)

    cycles = derive_cycle_count(run_dir)

    # None, not 0, when the archive recorded no `inspect_modes` at all: a run
    # that wrote the list and never opened an F5 INSPECT really did do zero
    # post-verification cycles, while a pre-release run that never wrote the
    # list cannot say. `_inspect_modes_section` keeps the raw entries, so the
    # two are distinguishable here and nowhere else.
    recorded_modes = state.get("inspect_modes")
    post_verification: int | None = None
    if isinstance(recorded_modes, list):
        post_verification = len(
            {
                decision.get("cycle")
                for decision in inspect_modes.get("per_cycle", {}).values()
                if decision.get("phase") == "F5"
            }
        )

    defects, problem = read_document(run_dir / DEFECTS_FILENAME)
    by_tier: dict[str, int] | None = None
    if problem is None and isinstance(defects.get("defects"), list):
        by_tier = dict.fromkeys(sorted(DEFECT_TIER_OR_UNKNOWN), 0)
        for record in defects["defects"]:
            if isinstance(record, dict):
                by_tier[defect_tier(record)] += 1

    spend_rows, problem = read_jsonl(run_dir / SPEND_LEDGER_FILENAME)
    tokens: int | None = None
    if problem is None and spend_rows:
        tokens = sum(_as_count(row.get("tokens")) for row in spend_rows)

    return {
        "run": run_dir.name,
        "grind_cycles": cycles["count"],
        "post_verification_cycles": post_verification,
        "defects_by_tier": by_tier,
        "tokens": tokens,
        "wall_clock_minutes": _wall_clock_minutes(run_dir),
    }


def _baseline_comparison_section(
    run_dir: Path, inspect_modes: dict, state: dict
) -> dict:
    """NFR-001 / AC-036 — this run's numbers beside thunder-viper's.

    The two CYCLE numbers are READ from vocab: thunder-viper's 22 GRIND cycles
    and 8 post-verification cycles are what OT-030 names, and reading them from
    the constant is what makes this report and `measure-run.py` incapable of
    disagreeing about the target they measure against.

    The archive itself is read too, when it is beside this run under the same
    `foundry-archive/` root, and that is what supplies NFR-001's other three
    columns (D-037). Every value it yields is derived by `_archive_metrics`,
    the same function that derives this run's — see its docstring. The recorded
    constants remain the FLOOR for the baseline's own cycles: if its archive
    derives fewer than the 22 it is on record as having run, the derivation is
    measuring wrong, not measuring a better run, and the recorded number wins.

    `dict(...)` copies rather than embedding the module objects. The two
    constants are plain dicts (they have to be — `report.json` is
    `json.dumps`'d wholesale and a `MappingProxyType` is not serializable), so
    embedding them would put a mutable module global into the document.

    THE CYCLE COUNT IS `derive_cycle_count`'s, NOT `state["cycle"]` (D-036)
    ----------------------------------------------------------------------
    This section published the raw counter while `measure-run.py` published
    that counter + 1, and the two straddle `CONVERGENCE_TARGET["grind_cycles"]`
    differently: a run at index 12 met the target here and missed it there. One
    derivation now, hosted in `foundry_state`, called by both.

    NFR-001: "Numbers are the target, not a gate." Nothing here refuses
    anything. `meets_target` is None for a number this archive could not
    supply, because "did not meet" and "cannot say" are different answers —
    and it is never True on an unavailable number, which is how the CLI came to
    certify thunder-viper itself as meeting the target its 22 cycles created.
    """
    target = dict(CONVERGENCE_TARGET)
    baseline_recorded = dict(THUNDER_VIPER_BASELINE)
    current = _archive_metrics(run_dir, state=state, inspect_modes=inspect_modes)

    baseline_dir = run_dir.parent / str(baseline_recorded.get("run", ""))
    baseline_note = (
        f"cycles from vocab.THUNDER_VIPER_BASELINE; the other columns are "
        f"derived from {baseline_dir.name}/ when that archive sits beside this "
        f"run."
    )
    if baseline_dir.is_dir() and baseline_dir.resolve() != run_dir.resolve():
        baseline = _archive_metrics(baseline_dir)
    else:
        baseline = {
            "run": baseline_recorded.get("run"),
            "grind_cycles": None,
            "post_verification_cycles": None,
            "defects_by_tier": None,
            "tokens": None,
            "wall_clock_minutes": None,
        }
        baseline_note += (
            " That archive is not present here, so those three columns are "
            "null rather than fabricated."
        )
    # The recorded numbers are the floor for the baseline's own cycles.
    for key in ("grind_cycles", "post_verification_cycles"):
        recorded = baseline_recorded.get(key)
        derived = baseline.get(key)
        baseline[key] = (
            recorded if not isinstance(derived, int) else max(derived, recorded)
        )

    def _meets(value: object, limit: object) -> bool | None:
        if not isinstance(value, int) or isinstance(value, bool):
            return None
        return value <= limit

    return {
        "baseline": baseline_recorded,
        "baseline_metrics": baseline,
        "target": target,
        "current": current,
        "meets_target": {
            "grind_cycles": _meets(
                current["grind_cycles"], target["grind_cycles"]
            ),
            "post_verification_cycles": _meets(
                current["post_verification_cycles"],
                target["post_verification_cycles"],
            ),
        },
        "metrics": ("grind_cycles", "post_verification_cycles",
                    "defects_by_tier", "tokens", "wall_clock_minutes"),
        "baseline_note": baseline_note,
        "note": "Numbers are the target, not a gate (NFR-001).",
    }


# ---------------------------------------------------------------------------
# Markdown rendering (FR-038 — layout is this module's discretion; the SET of
# `## ` headings and their ORDER are not).
# ---------------------------------------------------------------------------

#: Human titles for the eleven section keys. A dict rather than a prettifier
#: over the key names, because `latent_backlog` prettifies to "Latent Backlog"
#: but `unknown_tier_defects` prettifies to "Unknown Tier Defects", which reads
#: as a tier called "Unknown Tier". Derived from REPORT_REQUIRED_SECTIONS at
#: import time so a section added to vocab without a title here fails loudly at
#: the assertion below instead of rendering a blank heading.
_SECTION_TITLES: dict[str, str] = {
    "verdict_matrix": "Verdict matrix",
    "defects_by_tier_and_status": "Defects by tier and status",
    "latent_backlog": "LATENT backlog",
    "unknown_tier_defects": "Unknown-tier defects",
    "escalated_classes": "Escalated classes",
    "lead_fix_records": "Lead fix records",
    "inspect_modes_per_cycle": "INSPECT mode per cycle",
    "spend_per_phase_and_cycle": "Spend per phase and cycle",
    "unreported_dispatches": "Unreported dispatches",
    "executing_versions": "Executing server and plugin versions",
    "baseline_comparison": "Baseline comparison",
}

assert set(_SECTION_TITLES) == set(REPORT_REQUIRED_SECTIONS), (
    "every REPORT_REQUIRED_SECTIONS member needs a markdown title: "
    f"{sorted(set(REPORT_REQUIRED_SECTIONS) ^ set(_SECTION_TITLES))}"
)


def _md_table(headers: list[str], rows: list[list[Any]]) -> list[str]:
    """A markdown table, or a single italic line when there are no rows.

    An empty table with only its header row reads as a rendering bug. "None
    recorded." reads as a measurement, which is what an empty section IS.
    """
    if not rows:
        return ["_None recorded._"]
    out = ["| " + " | ".join(headers) + " |",
           "|" + "|".join("---" for _ in headers) + "|"]
    for row in rows:
        cells = ["" if c is None else str(c).replace("|", "\\|").replace("\n", " ")
                 for c in row]
        out.append("| " + " | ".join(cells) + " |")
    return out


def _render_markdown(run_name: str, generated_at: str, sections: dict) -> str:
    """One `## ` heading per section, in `REPORT_REQUIRED_SECTIONS` order.

    The lead may append prose BELOW this document; `report_status` reads the
    JSON, not the markdown, so appended prose can never make a section look
    missing. What the lead cannot do is delete a heading and still reach DONE.
    """
    lines: list[str] = [
        f"# Foundry run report — {run_name}",
        "",
        f"Generated {generated_at} by Foundry-Report. Every section below is "
        "generated from the run's own ledgers (GI-006): prose may be appended, "
        "no section may be removed.",
        "",
    ]
    for key in REPORT_REQUIRED_SECTIONS:
        lines.append(f"## {_SECTION_TITLES[key]}")
        lines.append("")
        lines.extend(_render_section(key, sections.get(key) or {}))
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _render_section(key: str, value: dict) -> list[str]:
    if key == "verdict_matrix":
        return (
            [f"{value.get('count', 0)} requirements, verdicts at cycle "
             f"{value.get('cycle')}: {value.get('by_verdict') or '{}'}", ""]
            + _md_table(
                ["ID", "Verdict", "Code location", "Cycle", "Evidence"],
                [[r.get("id"), r.get("verdict"), r.get("code_location"),
                  r.get("cycle"), r.get("evidence")]
                 for r in value.get("requirements", [])],
            )
        )
    if key == "defects_by_tier_and_status":
        rows = []
        for tier, statuses in (value.get("cross_tab") or {}).items():
            for status, bucket in statuses.items():
                rows.append([tier, status, bucket.get("count"),
                             ", ".join(str(i) for i in bucket.get("ids", []))])
        return (
            [f"{value.get('total', 0)} defects; by tier: {value.get('by_tier')}", ""]
            + _md_table(["Tier", "Status", "Count", "IDs"], rows)
        )
    if key == "latent_backlog":
        return (
            [f"{value.get('open_count', 0)} open LATENT defects carried forward "
             "(NFR-003). Each row names where the work is, because this list "
             "is read by a lead who has no defects.json to join against "
             "(D-029).", ""]
            + _md_table(
                ["ID", "Class", "Cycle", "File", "Symbol", "Source",
                 "Reproduction attempted", "Description"],
                [[d.get("id"), d.get("class"), d.get("cycle"), d.get("file"),
                  d.get("symbol"), d.get("source"),
                  d.get("reproduction_attempted"), d.get("description")]
                 for d in value.get("defects", [])],
            )
        )
    if key == "unknown_tier_defects":
        return (
            [str(value.get("note", "")), ""]
            + _md_table(
                ["ID", "Class", "Status", "Cycle", "Description"],
                [[d.get("id"), d.get("class"), d.get("status"), d.get("cycle"),
                  d.get("description")] for d in value.get("defects", [])],
            )
        )
    if key == "escalated_classes":
        return (
            [f"{value.get('count', 0)} classes; by status: "
             f"{value.get('by_status')}; by exit reason: "
             f"{value.get('by_exit_reason')}", ""]
            + _md_table(
                ["Class", "Status", "Exit reason", "Escalated", "Cleared",
                 "Packets", "Open LATENT ids"],
                [[c.get("class"), c.get("status"), c.get("exit_reason"),
                  c.get("escalated_at_cycle"), c.get("cleared_at_cycle"),
                  c.get("structural_packets_dispatched"),
                  ", ".join(str(i) for i in (c.get("open_latent_defect_ids") or []))]
                 for c in value.get("classes", [])],
            )
        )
    if key == "lead_fix_records":
        return (
            [f"{value.get('count', 0)} lead-authored fixes (GI-003 / AC-022).", ""]
            + _md_table(
                ["Defect", "Tier", "File", "Lines", "Test", "Fix commit"],
                [[r.get("defect_id"), r.get("tier"), r.get("file"),
                  r.get("line_count"), r.get("test"), r.get("fix_commit")]
                 for r in value.get("records", [])],
            )
        )
    if key == "inspect_modes_per_cycle":
        return (
            [f"{value.get('count', 0)} cycles; by mode: {value.get('by_mode')}", ""]
            + _md_table(
                ["Cycle", "Phase", "Mode", "Rule", "Decided by", "Required streams"],
                [[d.get("cycle"), d.get("phase"), d.get("mode"), d.get("rule"),
                  d.get("decided_by"),
                  ", ".join(str(s) for s in (d.get("required_streams") or []))]
                 for d in (value.get("per_cycle") or {}).values()],
            )
        )
    if key == "spend_per_phase_and_cycle":
        total = value.get("total") or {}
        rows = [["phase", k, v.get("tokens"), v.get("minutes"), v.get("agents")]
                for k, v in (value.get("by_phase") or {}).items()]
        rows += [["cycle", k, v.get("tokens"), v.get("minutes"), v.get("agents")]
                 for k, v in (value.get("by_cycle") or {}).items()]
        rows.append(["run", "total", total.get("tokens"), total.get("minutes"),
                     total.get("agents")])
        return (
            # "Reported as", not "Cost is": NFR-002 bans the money frame, and
            # the word invites a reader to supply the rate table the section
            # deliberately does not keep. `test_report.py`'s currency scan
            # drove this — it matched the generator's own sentence.
            [f"{value.get('records', 0)} spend records; "
             f"{total.get('distinct_agents', 0)} distinct agents. Reported as "
             "tokens and minutes only (NFR-002).", ""]
            + _md_table(["Scope", "Key", "Tokens", "Minutes", "Records"], rows)
        )
    if key == "unreported_dispatches":
        rows = [[phase, ", ".join(agents)]
                for phase, agents in (value.get("by_phase") or {}).items()]
        return (
            [f"{value.get('count', 0)} of {value.get('dispatched', 0)} dispatches "
             f"reported no spend. {value.get('note', '')}", ""]
            + _md_table(["Phase", "Agents"], rows)
        )
    if key == "executing_versions":
        return _md_table(
            ["Field", "Value"],
            [[k, value.get(k)] for k in
             ("server_version", "plugin_version", "server_root", "server_commit",
              "self_target")],
        )
    if key == "baseline_comparison":
        baseline = value.get("baseline") or {}
        metrics = value.get("baseline_metrics") or {}
        target = value.get("target") or {}
        current = value.get("current") or {}

        def _tiers(counts: object) -> str | None:
            if not isinstance(counts, dict):
                return None
            return ", ".join(f"{k} {v}" for k, v in counts.items())

        # NFR-001's four columns, side by side: "cycles, defects by tier,
        # tokens, wall clock". A row whose baseline cell is blank is a column
        # thunder-viper's archive cannot supply, not one nobody thought to
        # print — `baseline_note` below says which.
        return (
            _md_table(
                ["Metric", f"Baseline ({baseline.get('run')})", "Target",
                 f"This run ({current.get('run')})"],
                [["GRIND cycles", metrics.get("grind_cycles"),
                  target.get("grind_cycles"), current.get("grind_cycles")],
                 ["Post-verification cycles",
                  metrics.get("post_verification_cycles"),
                  target.get("post_verification_cycles"),
                  current.get("post_verification_cycles")],
                 ["Defects by tier", _tiers(metrics.get("defects_by_tier")),
                  None, _tiers(current.get("defects_by_tier"))],
                 ["Tokens", metrics.get("tokens"), None,
                  current.get("tokens")],
                 ["Wall clock (minutes)", metrics.get("wall_clock_minutes"),
                  None, current.get("wall_clock_minutes")]],
            )
            + ["", str(value.get("baseline_note", "")),
               "", str(value.get("note", ""))]
        )
    return ["_None recorded._"]


# ---------------------------------------------------------------------------
# The two public entry points (C-10).
# ---------------------------------------------------------------------------


def generate_report(project_root: Path, run_dir: Path) -> dict:
    """Write `REPORT.md` and `report.json` from the run's own ledgers.

    Args:
        project_root: the repo the run is building in. Carried for symmetry
            with every other tool entry point and so a future section can reach
            the working tree; the sections that exist today all read the run
            directory.
        run_dir: `foundry-archive/{run}/`.

    Returns:
        `{'ok': True, 'report_md': str, 'report_json': str, 'sections': [...]}`
        on success, or a named refusal — NEVER a raise — carrying the ledger
        that would not read. See `_refusal` for why an ABSENT ledger is not
        that case.

    `report.json`'s top-level keys are exactly `REPORT_REQUIRED_SECTIONS` plus
    `generated_at` and `run`. That is asserted here rather than left to a test,
    because `report_status` compares the SAME tuple and a report written with a
    twelfth key would pass generation and then be unreadable at the DONE gate.
    """
    run_dir = Path(run_dir)
    if not run_dir.is_dir():
        return {
            "ok": False,
            "error": f"run directory does not exist: {run_dir}",
            "hint": (
                "Foundry-Report generates from a run's ledgers, so the run "
                "directory has to exist. Check the active run name."
            ),
            "ledger": str(run_dir),
        }

    state, problem = _read_state(run_dir)
    if problem is not None:
        return _refusal(STATE_FILENAME, problem)

    verdict_matrix, problem = _read_verdict_matrix(run_dir)
    if problem is not None:
        return _refusal(VERDICTS_FILENAME, problem)

    defect_sections, problem = _read_defect_sections(run_dir)
    if problem is not None:
        return _refusal(DEFECTS_FILENAME, problem)

    escalated, problem = _read_escalated_classes(run_dir)
    if problem is not None:
        return _refusal(ESCALATION_FILENAME, problem)

    lead_fixes, problem = _read_lead_fix_records(run_dir)
    if problem is not None:
        return _refusal(HANDOFFS_FILENAME, problem)

    spend, problem = _read_spend(run_dir, state)
    if problem is not None:
        return _refusal(SPEND_LEDGER_FILENAME, problem)

    unreported, problem = _read_unreported_dispatches(run_dir)
    if problem is not None:
        return _refusal(f"{SPAWNS_FILENAME} / {SPEND_LEDGER_FILENAME} / {ROLLUP_FILENAME}",
                        problem)

    inspect_modes = _inspect_modes_section(state)

    sections: dict[str, Any] = {
        "verdict_matrix": verdict_matrix,
        "defects_by_tier_and_status": defect_sections["defects_by_tier_and_status"],
        "latent_backlog": defect_sections["latent_backlog"],
        "unknown_tier_defects": defect_sections["unknown_tier_defects"],
        "escalated_classes": escalated,
        "lead_fix_records": lead_fixes,
        "inspect_modes_per_cycle": inspect_modes,
        "spend_per_phase_and_cycle": spend,
        "unreported_dispatches": unreported,
        "executing_versions": _executing_versions_section(state),
        "baseline_comparison": _baseline_comparison_section(
            run_dir, inspect_modes, state
        ),
    }
    assert tuple(sections) == REPORT_REQUIRED_SECTIONS, (
        "report.json's top-level section keys must be exactly "
        f"REPORT_REQUIRED_SECTIONS in order; got {tuple(sections)}"
    )

    generated_at = _now()
    phase = state.get("phase")
    document: dict[str, Any] = {
        "generated_at": generated_at,
        "run": {
            "name": run_dir.name,
            "project_root": str(project_root),
            "phase": phase,
            "cycle": state.get("cycle"),
            # ST-008: HALTED is a named terminal state, not DONE. The section
            # bodies name every open LIVE and every open LATENT defect on every
            # run, so a halted run's report is auditable without a twelfth
            # section — this flag only tells the reader which kind of ending
            # they are looking at.
            "halted": phase == RUN_PHASE_HALTED,
            "halted_at_cycle": state.get("halted_at_cycle"),
            "halted_reason": state.get("halted_reason"),
            "max_cycles": state.get("max_cycles"),
        },
    }
    document.update(sections)

    md_path = run_dir / REPORT_MD_FILENAME
    json_path = run_dir / REPORT_JSON_FILENAME
    try:
        # D-015 — THE MARKDOWN IS WRITTEN FIRST, and the order is the point.
        #
        # `report_status` now requires BOTH documents, so whichever is written
        # second is the one whose absence holds the DONE gate shut. Writing the
        # JSON first meant an OSError on the markdown left a complete
        # `report.json` behind and a gate that opened on it — a run reaching
        # DONE with no REPORT.md at all, which is GI-006's violation column
        # word for word. Write the markdown first and the same failure leaves
        # no JSON, so the gate stays shut and `Foundry-Report` is simply run
        # again.
        md_path.write_text(
            _render_markdown(run_dir.name, generated_at, sections), encoding="utf-8"
        )
        json_path.write_text(
            json.dumps(document, indent=2, sort_keys=False, default=str) + "\n",
            encoding="utf-8",
        )
    except OSError as exc:
        return {
            "ok": False,
            "error": f"could not write the report into {run_dir}: {exc}",
            "hint": "Check the run directory is writable, then retry.",
            "ledger": str(run_dir),
        }

    return {
        "ok": True,
        "report_md": str(md_path),
        "report_json": str(json_path),
        "sections": list(REPORT_REQUIRED_SECTIONS),
        "generated_at": generated_at,
    }


def _markdown_missing_sections(run_dir: Path) -> tuple[list[str], str | None]:
    """The required `## ` headings REPORT.md does not carry (D-015).

    Returns ``(missing_section_keys, problem)``. A heading counts as present
    when a line reading exactly `## <title>` is there, at any depth in the
    document and in any order — because GI-006 licenses the lead to APPEND
    prose, and appended prose can put arbitrary text between, above and below
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
    headings = {line.strip() for line in text.splitlines()}
    return [
        key
        for key in REPORT_REQUIRED_SECTIONS
        if f"## {_SECTION_TITLES[key]}" not in headings
    ], None


def report_status(run_dir: Path) -> dict:
    """`{'present': bool, 'missing_sections': [...]}` — the DONE gate's read.

    GI-006 gives the lead permission to APPEND prose and no permission to omit
    a section, and this is where the second half is checked. Both documents are
    read off disk every time rather than trusting anything `generate_report`
    returned, because the gap the check exists to close is exactly the one
    where somebody edited a file after it was generated.

    BOTH DOCUMENTS, NOT JUST THE JSON (D-015)
    -----------------------------------------
    This read the JSON alone, and the docstring argued the case: the JSON's
    keys are machine-written and machine-read, so they answer the question
    exactly, while a markdown scan could be confused by a reflowed table.

    The argument was for the wrong question. GI-006's violation column names
    "a lead-authored REPORT.md that lacks the generated sections" in those
    words, and REPORT.md is the document a human actually reads — the JSON
    exists for tools. Driven: delete REPORT.md outright and the DONE gate still
    passed, so a run could reach DONE with no operator-readable report at all,
    which is the exact outcome GI-006 exists to prevent.

    So the JSON answers "which sections were generated" and the markdown
    answers "which sections a reader can still find", and `missing_sections` is
    the union. The confusability worry is handled by matching whole heading
    lines (see `_markdown_missing_sections`) rather than by not looking.

    A document that is absent, unreadable, or not an object reports every
    section missing. That is the honest answer: no section can be shown to be
    there. `problem` carries the reason when there is one, so a caller refusing
    the DONE transition can say whether the report was never generated or is
    corrupt.
    """
    run_dir = Path(run_dir)
    path = run_dir / REPORT_JSON_FILENAME
    md_path = run_dir / REPORT_MD_FILENAME
    md_missing, md_problem = _markdown_missing_sections(run_dir)

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
