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
    handoffs_wall_clock_seconds,
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

#: What a defect row prints where its filing recorded no location. D-103.
#:
#: FR-005 is Locked and the GRIND-5 ruling that briefly required `file_path` on
#: every LATENT filing was REVERSED (`state.json.spec_ambiguities[6]`): the
#: doors refuse a LATENT filing for a missing tier, a missing or placeholder
#: `reproduction_attempted`, a missing class or the SECURITY_PROPERTY_CLAIM
#: denylist, and never for a missing location. So an unlocated row is a LEGAL
#: filing, and the report has to render it as one. An empty cell reads as a
#: dropped value; this reads as the measurement it is.
NO_LOCATION_CELL = "(none recorded)"


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


def _location_fields(record: dict) -> dict[str, Any]:
    """`file` and `symbol`, plus a STATEMENT when the filing carried neither.

    Returns ``{"file", "symbol", "located", "location_note"}``. `file` and
    `symbol` are the record's own values, normalised to None when absent or
    blank — the same `(d.get("file") or "")` normalisation
    `foundry.retier_matching_untiered` matches on, so a row here cannot claim a
    location the re-tier door would treat as empty.

    WHY A ROW STATES WHAT IT DOES NOT HAVE (D-103, and the D-089 reversal)
    ----------------------------------------------------------------------
    D-089 saw a LATENT row with empty File and Symbol cells under a header
    reading "Each row names where the work is", and the GRIND-5 remedy was to
    require `file_path` at both filing doors. TEST-01 then showed that ruling
    contradicts FR-005, CT-001 and CT-003 — all Locked, all saying the server
    refuses a LATENT filing only for a security-property claim, a missing tier,
    a missing class or a missing reproduction statement — and the ruling was
    REVERSED (`state.json.spec_ambiguities[6]`). The rung came out of the doors
    and the header that motivated it survived, so the report was left promising
    a location the protocol does not collect.

    The reversal named the report-side close: "the LATENT backlog renders an
    unlocated row honestly (stating the filing carried no file) and its header
    does not promise what the door does not require". That is this function.
    `location_note` is the statement, carried in `report.json` so a mechanical
    reader gets the same answer the markdown cell gives — the filing observed
    both documents rendering `null` and blank with no statement anywhere.

    `located` keys on the FILE, because the file is the rung the reversal
    removed and the field a reader routes on; `location_note` names whichever
    of the two the filing actually lacked, so "no file, real symbol" and "no
    file, no symbol" are different sentences rather than one shrug.
    """
    file_value = record.get("file")
    symbol_value = record.get("symbol")
    file_value = (
        file_value if isinstance(file_value, str) and file_value.strip() else None
    )
    symbol_value = (
        symbol_value if isinstance(symbol_value, str) and symbol_value.strip() else None
    )
    missing = [
        name
        for name, value in (("file", file_value), ("symbol", symbol_value))
        if value is None
    ]
    return {
        "file": file_value,
        "symbol": symbol_value,
        "located": file_value is not None,
        "location_note": (
            None if not missing else "the filing carried no " + " and no ".join(missing)
        ),
    }


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
    different rows. Being the blocking section is also why its rows carry
    `source`, `type`, `file` and `symbol` (D-120): those four ARE the re-tier
    match key the DONE refusal's hint sends the lead to use, and a section a
    lead must act on has to be actionable from the document in front of them.

    Both row builders take their location through `_location_fields`, which
    states what a filing did not carry instead of printing an empty cell
    (D-103). A LATENT filing is never refused for a missing location (FR-005),
    so "unlocated" is a legal shape on both of these lists.

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
                    # D-103: `file` and `symbol` come from `_location_fields`,
                    # which also says so when the filing carried neither. The
                    # doors do not require a location on a LATENT filing
                    # (FR-005), so a row with none is a legal filing and this
                    # list has to render it as one.
                    **_location_fields(record),
                    "source": record.get("source"),
                    "type": record.get("type"),
                    "spec_ref": record.get("spec_ref"),
                    "description": record.get("description"),
                    "reproduction_attempted": record.get("reproduction_attempted"),
                    "cycle": record.get("cycle"),
                }
            )
        if tier == TIER_UNKNOWN:
            # D-120 — THE FOUR FIELDS THE REMEDY MATCHES ON TRAVEL WITH THE ROW.
            #
            # This is the one section that BLOCKS: an untiered record holds the
            # DONE gate shut exactly as a LIVE one does, and the refusal's hint
            # names the way out — "either door matches the open untiered record
            # on (source, type, file, symbol) and re-tiers it IN PLACE". The
            # row carried id, class, description, status and cycle, which is
            # none of those four, so the one section a lead must act on was the
            # one that could not be acted on from the report. The adjacent
            # LATENT backlog had carried its location since D-029 for a weaker
            # reason (it does not block), and the fix was never carried across.
            unknown_rows.append(
                {
                    "id": record.get("id"),
                    "class": record.get("class"),
                    "source": record.get("source"),
                    "type": record.get("type"),
                    **_location_fields(record),
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
                "nobody ever classified it. The way out is a re-filing "
                "through Foundry-Defect or Foundry-Sync: either door matches "
                "the open untiered record on (source, type, file, symbol) and "
                "re-tiers it IN PLACE, keeping its id — so those four fields "
                "travel with every row here (D-120)."
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
            # D-078: the numstat rows themselves, carried through instead of
            # dropped. `file` is None for every multi-file fix, so a report
            # built from `file` alone answered "in which file" with a blank
            # cell for exactly the fixes where the question is hardest — and
            # printed that same blank for a commit git could not read at all.
            "files": r.get("files"),
            "file_rows": _lead_fix_file_rows(r),
            "test": r.get("test"),
            "fix_commit": r.get("fix_commit"),
        }
        for r in records
        if r.get("event") == HANDOFF_EVENT_LEAD_FIX
    ]
    return {"count": len(rows), "records": rows}, None


def _lead_fix_file_rows(record: dict) -> list[dict]:
    """One row per non-test file a `lead_fix` record measured. AC-022 / D-078.

    Returns ``[{"path": str, "line_count": int | None}]`` — one entry per
    ``files`` row, so the markdown table can print a line per file and the JSON
    carries the same rows the markdown draws (FR-038).

    THE FOUR CASES ARE THE WRITER'S FOUR CASES, NOT A FIFTH OPINION (D-078)
    ----------------------------------------------------------------------
    `foundry_handoff.record_lead_fix_handoff` already distinguishes them in the
    handoffs.md mirror: the single path, the N-file list, "no non-test file in
    the commit", and MEASUREMENT_UNAVAILABLE. This report rendered ONE cell for
    all four — empty — so a five-file LATENT fix and a fix whose commit git
    could not read were the same row, while the mirror beside them said which
    was which. AC-022 makes this the audit trail for a fix nobody else
    reviewed and names "in which file" as one of the five things a reader must
    be able to re-derive.

    ``MEASUREMENT_UNAVAILABLE`` and ``_numstat_count`` are IMPORTED from the
    writer, not re-typed here, for the reason `_agent_id_for_casting` above
    imports its spelling: a second copy of the sentinel drifts silently the day
    the writer's wording changes, and re-parsing a numstat cell with a second
    parser is how the audit record comes to disagree with the lane measurement
    it exists to make re-derivable. The import is function-local and closes no
    cycle — it runs at call time, when every module in the chain is built.

    ``line_count`` is the availability signal, exactly as it is at the writer:
    a commit touching three non-test files has a real count and NO single path,
    and calling that "unavailable" would be the same lie one shape along.
    """
    from foundry_mcp.tools.foundry_handoff import (
        MEASUREMENT_UNAVAILABLE,
        _numstat_count,
    )

    files = record.get("files")
    line_count = record.get("line_count")
    measured = line_count is not None

    if isinstance(files, list):
        rows = [row for row in files if isinstance(row, dict)]
        if not rows:
            # `files: []` is a MEASURED commit that changed no non-test file —
            # a real answer, and a different one from "git could not read it".
            return [{"path": "no non-test file in the commit",
                     "line_count": line_count if measured else 0}]
        return [
            {
                "path": str(row.get("path")),
                "line_count": (
                    _numstat_count(row.get("added"))
                    + _numstat_count(row.get("deleted"))
                ),
            }
            for row in rows
        ]

    # No `files` key: a record written before D-074 added the rows. `file` and
    # `line_count` are all there is, and they still separate the three shapes
    # that pre-date the rows.
    if not measured:
        return [{"path": MEASUREMENT_UNAVAILABLE, "line_count": None}]
    if isinstance(record.get("file"), str) and record["file"]:
        return [{"path": record["file"], "line_count": line_count}]
    return [{"path": "more than one non-test file (no single path)",
             "line_count": line_count}]


def _read_state(run_dir: Path) -> tuple[dict, str | None]:
    data, problem = read_document(run_dir / STATE_FILENAME)
    if problem is not None:
        return {}, problem
    return data, None


def _inspect_modes_section(state: dict) -> dict:
    """FR-023 / AC-036 — EVERY recorded FULL/DELTA decision, per cycle and phase.

    `inspect_modes` is append-only and one cycle carries one entry per INSPECT
    it opened, so `per_cycle` maps a cycle to the LIST of its decisions, in the
    order the server recorded them. `count` counts DECISIONS, `cycle_count`
    counts cycles, and `by_mode` is a census of every decision.

    LAST-ENTRY-WINS FABRICATED A WIDTH FOR THE ORDINARY RUN (D-119)
    ---------------------------------------------------------------
    This kept one row per cycle, taking the last entry, on C-4's "the current
    decision is the last entry" rule. That rule is about which decision is
    CURRENT — what Foundry-Next reports and what the next gate reads — and this
    section is not that question. FR-023 asks for "the full-versus-delta
    decisions per cycle", and a census that keeps one of two answers is not a
    census.

    The collapse was not a corner case, it was the F2-to-F5 path: the server
    counter does not advance entering F5, so TEMPER's entry is stamped with the
    cycle the preceding F2 INSPECT already used. Driven on four real recorded
    decisions — (1, F2, FULL), (2, F2, DELTA), (3, F2, DELTA), (3, F5, FULL) —
    the truth is FULL 2 and DELTA 2 over three cycles; `report.json` returned
    `by_mode {DELTA 1, FULL 2}` with `count 3`, and REPORT.md's table showed
    cycle 3 as FULL / first_of_phase with no DELTA row for it anywhere. A lead
    auditing whether DELTA ever fired at cycle 3 was told FULL. `entries` was
    beside it the whole time and this docstring claimed nothing was lost to the
    collapse, which was false for `count`, for `by_mode` and for the table — a
    reader has no reason to re-derive a census the section says it computed.

    `entries` still carries the raw list, because it holds the fields this
    section does not lift (`stream_scope`, `touched_files`, `prove_sample`).
    """
    entries = state.get("inspect_modes")
    if not isinstance(entries, list):
        entries = []
    per_cycle: dict[str, list[dict]] = {}
    by_mode = dict.fromkeys(sorted(INSPECT_MODES), 0)
    history: list[dict] = []
    decisions = 0
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        history.append(entry)
        cycle = entry.get("cycle")
        if isinstance(cycle, bool) or not isinstance(cycle, int):
            continue
        decision = {
            "cycle": cycle,
            "phase": entry.get("phase"),
            "mode": entry.get("mode"),
            "rule": entry.get("rule"),
            "decided_by": entry.get("decided_by"),
            "required_streams": entry.get("required_streams"),
        }
        per_cycle.setdefault(str(cycle), []).append(decision)
        decisions += 1
        if decision["mode"] in by_mode:
            by_mode[decision["mode"]] += 1
    return {
        "count": decisions,
        "cycle_count": len(per_cycle),
        "by_mode": by_mode,
        "per_cycle": {k: per_cycle[k] for k in sorted(per_cycle, key=_cycle_sort_key)},
        "entries": history,
    }


def _inspect_decisions(inspect_modes: dict) -> list[dict]:
    """Every decision `_inspect_modes_section` recorded, cycle order preserved.

    The flattening lives here rather than in each reader, so the markdown table
    and `_archive_metrics` walk one list built one way. Two flattenings of one
    append-only ledger is how the collapse D-119 names got two different answers
    out of the same `inspect_modes` in the first place.
    """
    return [
        decision
        for group in (inspect_modes.get("per_cycle") or {}).values()
        for decision in group
    ]


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
    """A ledger bucket. ``records`` counts ROWS; ``agents`` counts AGENTS.

    D-090: these were one key. ``agents`` was incremented once per ledger row
    here while `foundry_orchestrator._spend_summary` published the same key as
    a count of DISTINCT agent ids, so report.json carried
    ``by_phase.F3.agents: 3`` beside ``state_rollup.agents: 2`` for the same
    phase — one field name, two meanings, side by side in one document, and
    they disagreed BY CONSTRUCTION on every run where any agent reported twice.
    ``agents`` is filled from the roll-up in `_read_spend`; nothing in this
    module counts it a second way.
    """
    return {"tokens": 0, "duration_ms": 0, "minutes": 0.0, "records": 0,
            "agents": None, "unreported": None}


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

    The ledger is the authority for TOKENS AND MINUTES and `state.json.spend`
    is carried beside it as `state_rollup`, never instead of it: the server
    writes that roll-up as it goes, and the two disagreeing is a fact worth
    being able to see rather than one to resolve silently here.

    ONE FIELD NAME, ONE MEANING (D-090)
    -----------------------------------
    "The two disagreeing is a fact worth seeing" was the right rule applied to
    the wrong pair. `foundry_orchestrator._spend_summary` counts `agents` as
    DISTINCT agents (D-038 made it so, over a set, from this same ledger); this
    reader counted `agents` as RECORDS. So report.json carried
    ``by_phase.F3 {tokens 240000, agents 3}`` beside
    ``state_rollup {tokens 240000, agents 2}`` — one key, two meanings, in one
    document — and they parted BY CONSTRUCTION the moment any agent reported
    twice. A drift signal that is permanently noisy detects no drift at all.

    So `agents` and `unreported` are READ from the roll-up, which is the
    orchestrator's derivation and the only one; the row count keeps its own
    honest name, `records`; and what is CHECKED is the pair that really is two
    derivations of one number — the roll-up's tokens and milliseconds against
    the ledger's. A mismatch is NAMED in `disagreements`, per bucket and per
    field, instead of being printed twice under one label.

    `agents` is None, never 0, on a run whose `state.json` carries no `spend`
    roll-up: "nobody recorded how many agents" and "no agents ran" are
    different facts, and this section already refuses to conflate that pair for
    the wall clock.
    """
    records, problem = read_jsonl(run_dir / SPEND_LEDGER_FILENAME)
    if problem is not None:
        return {}, problem

    state_rollup = state.get("spend")
    state_rollup = state_rollup if isinstance(state_rollup, dict) else None

    def _rollup_bucket(section: str | None, key: str | None) -> dict:
        if state_rollup is None:
            return {}
        if section is None:
            found = state_rollup.get("total")
        else:
            group = state_rollup.get(section)
            found = group.get(key) if isinstance(group, dict) else None
        return found if isinstance(found, dict) else {}

    by_phase: dict[str, dict[str, Any]] = {}
    by_cycle: dict[str, dict[str, Any]] = {}
    total = _new_spend_bucket()
    # Agent NAMES per bucket. NOT published — `agents` is the roll-up's number
    # and only the roll-up's. This is the CHECK: the orchestrator derives its
    # count as distinct names over this same ledger, so the two must agree, and
    # a bucket where they do not is a stale roll-up worth naming.
    seen: dict[tuple[str, str], set[str]] = {}
    for entry in records:
        tokens = _as_count(entry.get("tokens"))
        duration_ms = _as_count(entry.get("duration_ms"))
        phase = entry.get("phase")
        cycle = entry.get("cycle")
        agent = entry.get("agent")

        buckets = [(("run", "total"), total)]
        if isinstance(phase, str) and phase:
            buckets.append((("by_phase", phase),
                            by_phase.setdefault(phase, _new_spend_bucket())))
        if isinstance(cycle, int) and not isinstance(cycle, bool) and cycle >= 0:
            buckets.append((("by_cycle", str(cycle)),
                            by_cycle.setdefault(str(cycle), _new_spend_bucket())))
        for scope, bucket in buckets:
            bucket["tokens"] += tokens
            bucket["duration_ms"] += duration_ms
            bucket["records"] += 1
            if isinstance(agent, str) and agent:
                seen.setdefault(scope, set()).add(agent)

    # A phase or cycle the roll-up knows and the ledger does not is the run
    # where every `Foundry-Spend` call was forgotten — the one whose gap most
    # needs a line. `_overlay_unreported` creates exactly those buckets, so
    # dropping them here would hide the case the field exists for.
    for section, target in (("by_phase", by_phase), ("by_cycle", by_cycle)):
        group = (state_rollup or {}).get(section)
        if isinstance(group, dict):
            for key in group:
                if isinstance(key, str):
                    target.setdefault(key, _new_spend_bucket())

    disagreements: list[dict] = []
    for section, key, bucket in (
        *(("by_phase", k, v) for k, v in by_phase.items()),
        *(("by_cycle", k, v) for k, v in by_cycle.items()),
        (None, "total", total),
    ):
        bucket["minutes"] = round(bucket["duration_ms"] / 60_000.0, 2)
        scope = "run" if section is None else section
        recorded = _rollup_bucket(section, key)
        for field in ("agents", "unreported"):
            value = recorded.get(field)
            bucket[field] = (
                value if isinstance(value, int) and not isinstance(value, bool)
                else None
            )
        # `agents` is checked against the ledger's distinct names and `tokens`
        # and `duration_ms` against the ledger's sums: three fields the
        # orchestrator and this reader both derive from the same rows, so
        # three places a stale roll-up shows. The ledger's agent count is
        # NEVER written into the bucket — publishing it there is what made
        # `agents` mean two things (D-090).
        ledger_side = {
            "tokens": bucket["tokens"],
            "duration_ms": bucket["duration_ms"],
            "agents": len(seen.get((scope, key), ())),
        }
        for field, ledger_value in ledger_side.items():
            value = recorded.get(field)
            if (
                isinstance(value, int)
                and not isinstance(value, bool)
                and value != ledger_value
            ):
                disagreements.append({
                    "scope": scope,
                    "key": key,
                    "field": field,
                    "ledger": ledger_value,
                    "state_rollup": value,
                })

    return {
        "records": len(records),
        "by_phase": {k: by_phase[k] for k in sorted(by_phase)},
        "by_cycle": {k: by_cycle[k] for k in sorted(by_cycle, key=_cycle_sort_key)},
        "total": total,
        "state_rollup": state_rollup,
        "disagreements": disagreements,
        "note": (
            "Tokens and minutes are the ledger's; agents and unreported are "
            "state.json.spend's, which is the orchestrator's own derivation "
            "and counts DISTINCT agents. `records` counts ledger rows, which "
            "is a different number whenever an agent reported twice (D-090)."
        ),
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

    D-087 — THE READ IS NOT THIS MODULE'S ANY MORE, ONLY THE UNIT IS.
    ------------------------------------------------------------------
    The paragraph above was TRUE here and false one surface along:
    `measure-run.py` had its own copy of this walk and published `0.0` for the
    unmeasured run this one publishes `None` for. Both print NFR-001's
    wall-clock column, and on a run with no `handoffs.jsonl` they printed
    different facts. `foundry_state.handoffs_wall_clock_seconds` now owns the
    ledger walk and the None rule for both; what stays here is the conversion
    to MINUTES, which is this document's unit and nobody else's.
    """
    seconds, _problem = handoffs_wall_clock_seconds(run_dir)
    return None if seconds is None else round(seconds / 60.0, 1)


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
        # DISTINCT CYCLES THAT OPENED AN F5 INSPECT, over every recorded
        # decision (D-119). It used to walk the collapsed per-cycle map, which
        # answered this question by luck: the counter does not advance entering
        # F5, so a cycle's F5 entry is always recorded AFTER its F2 one and
        # therefore always survived the collapse.
        #
        # D-088 unified this number with `measure-run.py::_read_inspect_modes`,
        # which still counts off ITS collapsed map, and the two still agree on
        # every archive the server can write for exactly that reason. Where
        # they could part — a cycle whose F5 entry is followed by an F2 one —
        # this now counts the cycle and that surface does not, which is the
        # direction that is right: the cycle did open an F5 INSPECT.
        post_verification = len(
            {
                decision.get("cycle")
                for decision in _inspect_decisions(inspect_modes)
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
    run_dir: Path,
    inspect_modes: dict | None = None,
    state: dict | None = None,
) -> dict:
    """NFR-001 / AC-036 — this run's numbers beside thunder-viper's.

    The two CYCLE numbers are READ from vocab: thunder-viper's 22 GRIND cycles
    and 8 post-verification cycles are what OT-030 names, and reading them from
    the constant is what makes this report and `measure-run.py` incapable of
    disagreeing about the target they measure against.

    The archive itself is read too, when it is beside this run under the same
    `foundry-archive/` root, and that is what supplies NFR-001's other three
    columns (D-037). Every value it yields is derived by `_archive_metrics`,
    the same function that derives this run's — see its docstring.

    There are exactly three outcomes, named once by the local
    `baseline_source` and stated to the reader by `baseline_note`: `derived`
    (the archive is beside this run and was read), `self` (the archive IS this
    run — OT-030's own invocation, where the three columns stay null because a
    run is not its own baseline), and `absent` (no such directory). D-135: the
    note used to be written against the FALSE branch of a single `present`
    flag, so `self` printed `absent`'s sentence and the report denied reading
    the archive it had just read. The outcome is deliberately NOT published as
    a section key — no requirement names one, and `baseline_note` is the
    surface the requirement does name.

    THE CONSTANT IS NOT A FLOOR. IT IS THE BASELINE (D-085)
    -------------------------------------------------------
    The recorded numbers used to be a floor — `max(derived, recorded)` — which
    is one-sided in exactly the direction that breaks it: a derivation that
    comes in UNDER 22 loses, and a derivation that comes in OVER 22 silently
    REPLACES the constant this section names in its own footnote. Driven: a
    thunder-viper archive whose `state.json.cycle` had been migrated to 22
    rendered the GRIND-cycles baseline cell as 23 while the note under the same
    table still read "cycles from vocab.THUNDER_VIPER_BASELINE". A floor that
    can be exceeded does not protect the constant it names, and a table that
    contradicts its own footnote is worse than either number alone.

    So the baseline COLUMN is `THUNDER_VIPER_BASELINE`, always, for the two
    numbers that constant records. What the archive derives is published
    BESIDE it as `baseline_derived` and named in the note; when the two differ
    the difference is stated rather than resolved, because a derivation that
    disagrees with the recorded baseline is a fact about the archive (or about
    a migration that touched it) and not a better measurement of the run.

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
    if state is None:
        state, _problem = read_document(run_dir / STATE_FILENAME)
    if inspect_modes is None:
        inspect_modes = _inspect_modes_section(state)
    current = _archive_metrics(run_dir, state=state, inspect_modes=inspect_modes)

    baseline_dir = run_dir.parent / str(baseline_recorded.get("run", ""))
    absent = {
        "run": baseline_recorded.get("run"),
        "grind_cycles": None,
        "post_verification_cycles": None,
        "defects_by_tier": None,
        "tokens": None,
        "wall_clock_minutes": None,
    }
    # D-135 — ONE VALUE DECIDES BOTH THE READ AND THE SENTENCE ABOUT IT.
    #
    # `present` used to be the whole decision and the note was written against
    # its FALSE branch alone, so the two reasons for not deriving — the archive
    # is not here, and the archive IS this run — printed the same sentence. The
    # second one is a lie, and it is the one OT-030's own invocation hits:
    # `measure-run.py foundry-archive/thunder-viper` reads that archive as the
    # CURRENT column (162 defects, 1469 wall-clock minutes, all off disk) while
    # the note under the table read "That archive is not present here ... and
    # nothing was derived". A report that misdescribes the archive it just read
    # is worse than one that declines to read it.
    #
    # So the outcome is named ONCE, here, and both the derivation and the
    # sentence are driven from the name. A fourth outcome cannot be added
    # without giving it a sentence, which is the property that was missing.
    if not baseline_dir.is_dir():
        baseline_source = "absent"
    elif baseline_dir.resolve() == run_dir.resolve():
        baseline_source = "self"
    else:
        baseline_source = "derived"
    present = baseline_source == "derived"
    baseline_derived = _archive_metrics(baseline_dir) if present else dict(absent)

    # The published baseline column: the two recorded cycle numbers, never
    # overridden (D-085), and the three columns the constant does not record,
    # which can only come from the archive.
    baseline = dict(baseline_derived)
    for key in ("grind_cycles", "post_verification_cycles"):
        baseline[key] = baseline_recorded.get(key)
    baseline["run"] = baseline_recorded.get("run")

    baseline_note = (
        f"GRIND cycles and post-verification cycles in the Baseline column are "
        f"vocab.THUNDER_VIPER_BASELINE "
        f"({baseline_recorded.get('grind_cycles')} / "
        f"{baseline_recorded.get('post_verification_cycles')}) and are never "
        f"replaced by a derivation. Defects by tier, tokens and wall clock have "
        f"no recorded constant, so they are derived from {baseline_dir.name}/ "
        f"when that archive sits beside this run."
    )
    if baseline_source == "absent":
        # WORDING IS FROZEN. Two committed evidence logs quote this sentence
        # verbatim (`evidence/casting-1-baseline-comparison.log`,
        # `evidence/casting-1-convergence-columns.log`), both captured against
        # a run directory whose sibling really has no thunder-viper archive —
        # where the sentence is true. Re-word it and those logs stop
        # re-executing byte-identically at the GI-002 sweep boundary.
        baseline_note += (
            " That archive is not present here, so those three columns are "
            "null rather than fabricated, and nothing was derived."
        )
    elif baseline_source == "self":
        baseline_note += (
            f" This run IS {baseline_dir.name}: the archive is present and was "
            f"read for the Current column, but a run is never its own "
            f"baseline, so those three columns are null rather than a "
            f"comparison of the archive with itself."
        )
    else:
        differs = {
            key: baseline_derived.get(key)
            for key in ("grind_cycles", "post_verification_cycles")
            if baseline_derived.get(key) != baseline_recorded.get(key)
        }
        if differs:
            baseline_note += (
                " That archive currently DERIVES "
                + ", ".join(f"{k} {v}" for k, v in sorted(differs.items()))
                + ", shown in the Derived column. The recorded constant stands;"
                " a derivation that disagrees with it is a fact about the"
                " archive, not a better measurement of the run."
            )

    def _meets(value: object, limit: object) -> bool | None:
        if not isinstance(value, int) or isinstance(value, bool):
            return None
        return value <= limit

    return {
        "baseline": baseline_recorded,
        "baseline_metrics": baseline,
        # D-085: what the baseline's own archive says, published beside the
        # constant instead of allowed to overwrite it.
        "baseline_derived": baseline_derived if present else None,
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

    THE HEADING TEXT IS LOAD-BEARING, BECAUSE THIS DOCUMENT IS READ BACK
    -------------------------------------------------------------------
    `report_status` reads BOTH documents off disk — `report.json`'s top-level
    keys AND this file's headings (`_markdown_missing_sections`) — and the DONE
    gate refuses on the union. So a heading rendered here with different text
    than `_SECTION_TITLES` holds is a section the gate reports missing, even
    though the JSON carries it.

    What the lead may still do is APPEND prose (GI-006), and that stays safe
    because the markdown scan matches a whole trimmed `## <title>` line
    ANYWHERE in the document, at any depth and in any order — not a prefix and
    not a position. What the lead cannot do is delete a heading, or bolt a
    suffix onto one, and still reach DONE.

    D-141 — WHAT THIS PARAGRAPH USED TO SAY. It read "`report_status` reads the
    JSON, not the markdown, so appended prose can never make a section look
    missing", which was true until D-015 moved the read onto both documents and
    is now false on both halves: the markdown IS read, and the reason appended
    prose is harmless is the whole-line match, not an absence of looking.
    Driven at HEAD: generate, `rm REPORT.md`, and `report_status` returns
    `present False`, `missing_from_markdown 11`, problem "REPORT.md does not
    exist". A maintainer trusting the retired sentence would have treated the
    heading text as cosmetic.
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
        # D-103: the header says what the row CARRIES, not what it names. The
        # doors never require a location on a LATENT filing (FR-005), so a
        # promise that "each row names where the work is" was a promise the
        # protocol does not keep — and it stood directly above rows whose File
        # and Symbol cells were blank. The reason for carrying the location
        # when there is one (D-029) is unchanged and still stated.
        return (
            [f"{value.get('open_count', 0)} open LATENT defects carried forward "
             "(NFR-003). Each row carries the location its filing recorded, "
             "because this list is read by a lead who has no defects.json to "
             f"join against (D-029) — and a cell reading `{NO_LOCATION_CELL}` "
             "is a filing that named none, not a value this report dropped: "
             "FR-005 refuses a LATENT filing for a missing tier, reproduction "
             "statement or class and never for a missing location.", ""]
            + _md_table(
                ["ID", "Class", "Cycle", "File", "Symbol", "Source",
                 "Reproduction attempted", "Description"],
                [[d.get("id"), d.get("class"), d.get("cycle"),
                  d.get("file") or NO_LOCATION_CELL,
                  d.get("symbol") or NO_LOCATION_CELL, d.get("source"),
                  d.get("reproduction_attempted"), d.get("description")]
                 for d in value.get("defects", [])],
            )
        )
    if key == "unknown_tier_defects":
        # D-120: Source, Type, File and Symbol are the re-tier match key the
        # DONE refusal tells the lead to use. This is the section that BLOCKS,
        # so it is the section that has to be actionable without opening
        # defects.json — the argument D-029 made for the LATENT backlog, which
        # blocks nothing.
        return (
            [str(value.get("note", "")), ""]
            + _md_table(
                ["ID", "Class", "Status", "Cycle", "Source", "Type", "File",
                 "Symbol", "Description"],
                [[d.get("id"), d.get("class"), d.get("status"), d.get("cycle"),
                  d.get("source"), d.get("type"),
                  d.get("file") or NO_LOCATION_CELL,
                  d.get("symbol") or NO_LOCATION_CELL,
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
        # D-078: ONE ROW PER FILE. A multi-file fix has no single path, so the
        # record's `file` is None and a table built on it printed a blank cell
        # — the same blank a commit git could not read printed, which made the
        # two indistinguishable in the one document that is supposed to make a
        # lead fix re-derivable. `Lines` is that FILE's added-plus-deleted; the
        # record's total is the sum of its rows.
        rows = []
        for r in value.get("records", []):
            for entry in r.get("file_rows") or [{"path": r.get("file"),
                                                 "line_count": r.get("line_count")}]:
                rows.append([r.get("defect_id"), r.get("tier"),
                             entry.get("path"), entry.get("line_count"),
                             r.get("test"), r.get("fix_commit")])
        return (
            [f"{value.get('count', 0)} lead-authored fixes (GI-003 / AC-022), "
             f"{len(rows)} file rows. One row per non-test file the fix "
             "touched; `Lines` is that file's added-plus-deleted.", ""]
            + _md_table(
                ["Defect", "Tier", "File", "Lines", "Test", "Fix commit"], rows,
            )
        )
    if key == "inspect_modes_per_cycle":
        # D-119: ONE ROW PER DECISION. A cycle that opened two INSPECTs — the
        # F2 one and the F5 one TEMPER opens without advancing the counter —
        # has two rows here, because collapsing them printed one of the two
        # widths as though it were the cycle's answer.
        return (
            [f"{value.get('count', 0)} recorded INSPECT-opening decisions over "
             f"{value.get('cycle_count', 0)} cycles; by mode: "
             f"{value.get('by_mode')}. One row per decision: a cycle carries "
             "two rows when an F5 INSPECT reopened it after its F2 one, and "
             "neither is dropped.", ""]
            + _md_table(
                ["Cycle", "Phase", "Mode", "Rule", "Decided by", "Required streams"],
                [[d.get("cycle"), d.get("phase"), d.get("mode"), d.get("rule"),
                  d.get("decided_by"),
                  ", ".join(str(s) for s in (d.get("required_streams") or []))]
                 for d in _inspect_decisions(value)],
            )
        )
    if key == "spend_per_phase_and_cycle":
        total = value.get("total") or {}

        def _spend_row(scope: str, name: str, bucket: dict) -> list[Any]:
            return [scope, name, bucket.get("tokens"), bucket.get("minutes"),
                    bucket.get("records"), bucket.get("agents"),
                    bucket.get("unreported")]

        rows = [_spend_row("phase", k, v)
                for k, v in (value.get("by_phase") or {}).items()]
        rows += [_spend_row("cycle", k, v)
                 for k, v in (value.get("by_cycle") or {}).items()]
        rows.append(_spend_row("run", "total", total))
        # D-090: Records and Agents are DIFFERENT COLUMNS because they are
        # different numbers — a teammate that reported twice is two records and
        # one agent. The markdown already said "Records" over a cell the JSON
        # called `agents`; now both documents call each number what it is.
        disagreements = value.get("disagreements") or []
        trailer = []
        if disagreements:
            trailer = ["", "The ledger and `state.json.spend` disagree on:"] + [
                f"- {d.get('scope')} {d.get('key')} {d.get('field')}: "
                f"ledger {d.get('ledger')}, state.json.spend "
                f"{d.get('state_rollup')}"
                for d in disagreements
            ]
        return (
            # "Reported as", not "Cost is": NFR-002 bans the money frame, and
            # the word invites a reader to supply the rate table the section
            # deliberately does not keep. `test_report.py`'s currency scan
            # drove this — it matched the generator's own sentence.
            [f"{value.get('records', 0)} spend records over "
             f"{total.get('agents')} distinct agents. Reported as "
             "tokens and minutes only (NFR-002).", ""]
            + _md_table(
                ["Scope", "Key", "Tokens", "Minutes", "Records", "Agents",
                 "Unreported"],
                rows,
            )
            + trailer
            + ["", str(value.get("note", ""))]
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
        #
        # D-085: Baseline and Derived are SEPARATE COLUMNS. The Baseline cell
        # for the two cycle rows is vocab.THUNDER_VIPER_BASELINE and cannot be
        # overwritten by what the archive happens to derive today; Derived
        # shows that derivation beside it, so a migration that moved the
        # archive's counter is visible instead of silently becoming the
        # baseline the footnote still credits to the constant.
        derived = value.get("baseline_derived") or {}
        return (
            _md_table(
                ["Metric", f"Baseline ({baseline.get('run')}, recorded)",
                 "Baseline (derived)", "Target",
                 f"This run ({current.get('run')})"],
                [["GRIND cycles", metrics.get("grind_cycles"),
                  derived.get("grind_cycles"),
                  target.get("grind_cycles"), current.get("grind_cycles")],
                 ["Post-verification cycles",
                  metrics.get("post_verification_cycles"),
                  derived.get("post_verification_cycles"),
                  target.get("post_verification_cycles"),
                  current.get("post_verification_cycles")],
                 ["Defects by tier", None,
                  _tiers(derived.get("defects_by_tier")),
                  None, _tiers(current.get("defects_by_tier"))],
                 ["Tokens", None, derived.get("tokens"), None,
                  current.get("tokens")],
                 ["Wall clock (minutes)", None,
                  derived.get("wall_clock_minutes"),
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
