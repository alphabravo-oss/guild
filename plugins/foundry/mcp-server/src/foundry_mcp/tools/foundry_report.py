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
    TEMPER_CANDIDATE,
    THUNDER_VIPER_BASELINE,
    TIER_HARDENING,
    TIER_UNKNOWN,
    defect_tier,
    escalation_status,
    halt_reason,
)
from foundry_mcp.tools.foundry_state import (
    DISPATCH_PHASE_TO_RUN_PHASE,
    as_count,
    cycle_sort_key,
    derive_cycle_count,
    escalated_class_rows,
    fallout_rows,
    full_cycle_ratio,
    handoffs_wall_clock_seconds,
    inspect_decisions,
    inspect_mode_rows,
    markdown_headings,
    now_iso,
    read_document,
    read_jsonl,
    read_text_file,
    spend_rollup,
    stream_rollup_rows,
    unreported_dispatch_inputs,
    unreported_dispatch_summary,
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
    """The report's `generated_at` stamp — SECONDS precision, from the leaf.

    GI-024: this was one of two `_now` definitions in the package and they did
    not agree, the other returning full precision. `foundry_state.now_iso`
    holds the one implementation and the precision is its documented argument,
    for the reason recorded there: the ledger stamps need sub-second ordering
    and this operator-facing line does not.
    """
    return now_iso(timespec="seconds")


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

    And being the blocking section is why it lists OPEN records only (D-166).
    FR-051's gloss and AC-008 both scope the separate listing to an open
    pre-change record, and `foundry_orchestrator._open_defects_by_tier` — the
    gate that does the blocking — skips any record whose status is not open.
    The list was unscoped while the LATENT backlog beside it was scoped on the
    same parse, so on the live archive it printed 156 FIXED records under a
    note asserting they hold the gates shut and naming a re-filing as the way
    out: a blocking claim and a remedy, both false for every row, in the
    section this docstring calls the one a lead must act on. The closed records
    are counted in `closed_count` instead of listed.

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
    hardening_backlog: list[dict] = []
    unknown_rows: list[dict] = []
    closed_unknown: list[Any] = []
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
        if tier == TIER_HARDENING and status == "open":
            # AC-024 / AC-022 — the same row shape the LATENT backlog carries,
            # and for the same reason (D-029): this list is read by a lead who
            # has no defects.json to join against, so a row saying only "the
            # retry arm double-counts" names a fault with no location.
            #
            # `reproduction_attempted` is on the row because a HARDENING filing
            # is a DRIVEN failure — the stream ran a probe of its own devising
            # and saw the wrong result — so unlike a LATENT row this one always
            # has one, and it is the whole reason the record is trusted.
            # `supersedes` travels with it because GI-022 makes promotion a NEW
            # filing that cites this id, never a re-tier in place: a reader
            # asking "did anything supersede this?" is asking about the id.
            hardening_backlog.append(
                {
                    "id": record.get("id"),
                    "class": record.get("class"),
                    **_location_fields(record),
                    "source": record.get("source"),
                    "type": record.get("type"),
                    "description": record.get("description"),
                    "reproduction_attempted": record.get("reproduction_attempted"),
                    "supersedes": record.get("supersedes"),
                    "fallout_of": record.get("fallout_of"),
                    "cycle": record.get("cycle"),
                }
            )
        if tier == TIER_UNKNOWN and status != "open":
            # D-166 — A CLOSED RECORD IS NOT UNDER THE BLOCKING NOTE.
            #
            # The rows below stood under a note asserting "It blocks the gates
            # exactly like LIVE" and naming a re-filing as "the way out". Both
            # sentences are false of a record that is already fixed, and
            # `foundry_orchestrator._open_defects_by_tier` — the gate that
            # actually blocks — skips any record whose status is not open. So
            # this section listed 156 closed records under a claim the gate
            # disagreed with, on the live archive, in the one section its own
            # docstring calls "the one a lead must act on".
            #
            # They are COUNTED, not dropped. FR-051 scopes the separate listing
            # to the OPEN untiered record and AC-008 agrees, but a reader who
            # knows the run carried untiered records needs to see where they
            # went; a section that silently omitted them would answer "this run
            # had none" to a different question than the one it was asked.
            closed_unknown.append(record.get("id"))
        elif tier == TIER_UNKNOWN:
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
        "hardening_backlog": {
            "open_count": len(hardening_backlog),
            "defects": hardening_backlog,
            # AC-020's list is filled in by `generate_report`, which holds the
            # observations ledger; it is declared here so the section's shape
            # is the same on every run and a reader never has to test whether
            # the key exists before reading it.
            "undriven_temper_candidates": [],
            "temper_ran": None,
        },
        "unknown_tier_defects": {
            "count": len(unknown_rows),
            "defects": unknown_rows,
            # D-166: the rows are the OPEN untiered records, so the closed ones
            # are named as a count here rather than listed under a note that is
            # false of them. Always present, including zero, for the reason the
            # cross-tab's empty tiers are: "no closed untiered records" is a
            # measurement and an absent key is not.
            "closed_count": len(closed_unknown),
            "closed_ids": closed_unknown,
            "note": (
                "A record with no tier key, or tier null, reads as "
                f"{TIER_UNKNOWN!r}. An OPEN one blocks the gates exactly like "
                "LIVE and is listed here rather than among the LIVE rows "
                "because nobody ever classified it. The way out is a "
                "re-filing through Foundry-Defect or Foundry-Sync: either "
                "door matches the open untiered record on (source, type, "
                "file, symbol) and re-tiers it IN PLACE, keeping its id — so "
                "those four fields travel with every row here (D-120). "
                f"{len(closed_unknown)} untiered record(s) in this run are "
                "already closed; they are counted in `closed_count` and not "
                "listed, because a fixed record blocks no gate and has no "
                "re-filing to do (D-166)."
            ),
        },
    }, None


def _read_escalated_classes(run_dir: Path) -> tuple[dict, str | None]:
    """AC-004 — per class: status, exit reason, cleared cycle, packets used."""
    data, problem = read_document(run_dir / ESCALATION_FILENAME)
    if problem is not None:
        return {}, problem
    # GI-024 — the ROWS are `foundry_state.escalated_class_rows`, which the
    # orchestrator's own escalation surface reads too. The closed vocabularies
    # and the resolver are handed IN because the leaf module imports nothing
    # from the package (its own contract), and `vocab.escalation_status` stays
    # the ONE decider — D-214's fix, unmoved.
    return escalated_class_rows(
        data,
        statuses=ESCALATION_STATUSES,
        exit_reasons=ESCALATION_EXIT_REASONS,
        status_of=escalation_status,
    ), None


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


def _cycle_ranges(cycles: list[int]) -> str:
    """`[0, 1, 2, 4, 7, 8]` -> `"0-2, 4, 7-8"`. Empty -> `""`.

    The gap this renders is ten cycles wide on the run that filed D-193, and a
    ten-item comma list reads as a data dump rather than as the one fact it
    carries — "the first ten cycles recorded nothing". Runs of consecutive
    cycles are the shape the gap actually has, so they are the shape it prints.
    """
    out: list[str] = []
    for cycle in sorted(cycles):
        if out and cycle == out[-1][1] + 1:
            out[-1][1] = cycle
        else:
            out.append([cycle, cycle])
    return ", ".join(
        str(lo) if lo == hi else f"{lo}-{hi}" for lo, hi in out
    )


def _inspect_axis_note(
    *,
    axis_top: int | None,
    axis_length: int | None,
    recorded_cycles: int,
    without: list[int] | None,
    grind_cycles: int | None,
    halted: bool,
    extended: bool,
    decisions: int,
) -> str:
    """AC-036's disclosure sentence: which cycles carry no recorded decision.

    One sentence pair, built once and published in BOTH documents, because a
    disclosure that exists only in the markdown is a disclosure `report.json`'s
    reader never sees — and the JSON is the surface the DONE gate and every
    downstream tool read.
    """
    if axis_length is None or without is None or axis_top is None:
        return (
            "This run's cycle axis cannot be derived — "
            f"{_UNDERIVABLE_REASON['grind_cycles']} — so this section cannot "
            f"say which cycles carry no recorded decision. The {decisions} "
            "decision(s) below are every one the ledger holds, and no cycle is "
            "assumed either way."
        )

    # The two numbers come from ONE `derive_cycle_count` reading, so where they
    # differ the difference has a cause worth naming rather than a discrepancy
    # to hide. Both known causes are named; a third would print the bare
    # difference rather than a wrong reason for it.
    reasons: list[str] = []
    if halted:
        reasons.append(
            "the halt subtracts the GRIND cycle the cap refused to open"
        )
    if extended:
        reasons.append(
            f"a recorded decision names cycle {axis_top}, above the highest "
            "cycle this run's other ledgers reach"
        )
    if grind_cycles is None:
        # Unreachable while `derive_cycle_count` returns a count whenever it
        # returns an index — an archive with no index took the branch above.
        # Written as WORDS rather than an f-string anyway, because if that
        # coupling ever loosens the alternative is `None` standing in for a
        # number in operator prose, which is exactly D-151's class on this
        # module's other generated sentences.
        tail = "does not publish a GRIND cycle count."
    elif axis_length == grind_cycles:
        tail = f"publishes as {grind_cycles} GRIND cycles."
    elif reasons:
        tail = (
            f"publishes as {grind_cycles} GRIND cycles — the two differ "
            f"because {' and '.join(reasons)}."
        )
    else:
        tail = (
            f"publishes as {grind_cycles} GRIND cycles, which this axis does "
            "not match; the two derivations part here, and that is itself "
            "worth reading."
        )
    axis_sentence = (
        f"The axis is this run's own cycle counter, 0 through {axis_top} — "
        f"{axis_length} cycles, from the same `derive_cycle_count` reading "
        f"`baseline_comparison` {tail}"
    )

    if not without:
        return f"{axis_sentence} Recorded decisions cover every one of them."
    label = "cycle" if len(without) == 1 else "cycles"
    verb = "carries" if len(without) == 1 else "carry"
    return (
        f"{axis_sentence} Recorded decisions cover {recorded_cycles} of them; "
        f"{label} {_cycle_ranges(without)} {verb} none — no INSPECT-opening "
        "transition recorded a mode there, and none is inferred here."
    )


def _inspect_modes_section(state: dict, run_dir: Path) -> dict:
    """FR-023 / AC-036 — EVERY recorded FULL/DELTA decision, per cycle and phase.

    `inspect_modes` is append-only and one cycle carries one entry per INSPECT
    it opened, so `per_cycle` maps a cycle to the LIST of its decisions, in the
    order the server recorded them. `count` counts DECISIONS, `cycle_count`
    counts cycles THAT CARRY ONE, `cycle_axis_length` counts the cycles the run
    ran, `cycles_without_decision` names the difference, and `by_mode` is a
    census of every decision.

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

    THE SECTION COUNTED ITS OWN ROWS AND CALLED THAT THE RUN'S CYCLES (D-193)
    -------------------------------------------------------------------------
    `cycle_count` counts the cycles this LEDGER has a decision for, and the
    document published it as the width of the axis AC-036 names — "the FULL or
    DELTA decision per cycle", which is an axis over the run's CYCLES, not over
    the rows that happen to exist. Driven through `generate_report` on this
    run's own archive: REPORT.md read "5 recorded INSPECT-opening decisions
    over 5 cycles" and, 64 lines later, "| GRIND cycles | 22 | | 12 | 15 |",
    with `report.json` carrying `inspect_modes_per_cycle.cycle_count` 5 beside
    `baseline_comparison.current.grind_cycles` 15. Two numbers for one axis in
    one document, neither reconciling with the other, and no sentence anywhere
    saying that cycles 0-9 carry no recorded decision at all. PROVE confirmed
    the data is genuinely absent rather than dropped by this reader — neither
    `inspect_modes` nor the roll-up holds a mode for those cycles — so the
    section was under-reporting the axis, not losing rows.

    Four sibling sections in the same document each disclose their own gap:
    spend says the Agents cells are blank "for want of that source, not because
    they are zero", baseline says its null columns are "null rather than
    fabricated", `unknown_tier_defects` explains why closed records are counted
    but unlisted, and the LATENT backlog explains an empty location cell. This
    was the one partial section that stayed silent.

    So the axis is derived HERE, from `derive_cycle_count` — the same reading
    `_archive_metrics` publishes as `grind_cycles`, which is what makes the two
    numbers in the document incapable of coming from two derivations (D-036's
    lesson, applied to the third pair). What is NOT done is fabricate a
    decision for an unrecorded cycle, or an axis that cannot show a gap: when
    the counter cannot be derived at all, `cycle_axis_length` and
    `cycles_without_decision` are None and the note says so, rather than
    falling back on the highest cycle THIS ledger names — an axis taken from
    the ledger being rendered against it is complete by construction, which is
    the same fabrication wearing a different hat.
    """
    # GI-024 — THE CENSUS IS `foundry_state.inspect_mode_rows`; THE SENTENCE
    # IS THIS MODULE'S.
    #
    # The rows are a derivation three surfaces need — this section, the status
    # display's width line and (from casting 3) `measure-run.py` — and the
    # disclosure sentence is RENDERING, which is why only the first moved. The
    # axis numbers the sentence is built from (`axis_top`, `axis_extended`)
    # come back with the rows rather than being re-derived here, so the prose
    # and the table cannot describe two different axes.
    derived = derive_cycle_count(run_dir)
    table = inspect_mode_rows(state=state, derived=derived, modes=INSPECT_MODES)
    table["note"] = _inspect_axis_note(
        axis_top=table["axis_top"],
        axis_length=table["cycle_axis_length"],
        recorded_cycles=table["cycle_count"],
        without=table["cycles_without_decision"],
        grind_cycles=derived["count"],
        halted=bool(derived["halted"]),
        extended=bool(table["axis_extended"]),
        decisions=table["count"],
    )
    # AC-046 — the acceptance figure over the widths the census already holds,
    # derived in the leaf so `measure-run.py` publishes the SAME number rather
    # than a second ratio (casting 3). A ratio computed here and a ratio
    # computed there is D-036's shape applied to an acceptance criterion, which
    # is the one place a disagreement is unarguable.
    table["full_cycle_ratio"] = full_cycle_ratio(table)
    return table


#: GI-024 — the flattening is `foundry_state.inspect_decisions`, bound here so
#: the markdown table and `_archive_metrics` walk the object the status display
#: walks. Two flattenings of one append-only ledger is how the collapse D-119
#: names got two different answers out of the same `inspect_modes`.
_inspect_decisions = inspect_decisions

#: GI-024 — the cycle ordering and the count coercion are `foundry_state`'s
#: single implementations, bound to this module's names so every call site
#: below reads as it always did. They are BINDINGS, not wrappers: `display.py`
#: and `scripts/measure-run.py` reach the same objects, so the three surfaces
#: cannot order a cycle axis three ways again (D-220).
_cycle_sort_key = cycle_sort_key
_as_count = as_count


def _read_spend(
    run_dir: Path, state: dict, dispatch_summary: dict | None = None
) -> tuple[dict, str | None]:
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

    So `agents` is READ from the roll-up, which is the orchestrator's
    derivation of THAT number and the only one; the row count keeps its own
    honest name, `records`; and what is CHECKED is every field that really is
    two derivations of one number — the roll-up's tokens, milliseconds, agents
    and unreported against this reader's. A mismatch is NAMED in
    `disagreements`, per bucket and per field, instead of being printed twice
    under one label.

    `agents` is None, never 0, on a run whose `state.json` carries no `spend`
    roll-up: "nobody recorded how many agents" and "no agents ran" are
    different facts, and this section already refuses to conflate that pair for
    the wall clock.

    WHY `unreported` IS NOT ONE OF THE ROLL-UP'S NUMBERS (D-163)
    ------------------------------------------------------------
    It was, on the premise stated above — "the orchestrator's derivation and
    the only one" — and the premise was false for this one field. The
    orchestrator's `_overlay_unreported` runs inside `_spend_summary`, which
    applies it to a THROWAWAY DEEP COPY ("a reader that mutated the document it
    read would make every display call a write"), so the derived count never
    reached `state.json`. The only writer of the key in the persisted document
    is `foundry_record_spend`'s `_empty_spend_bucket`, which seeds it to 0 and
    never increments it. So the field this reader copied was structurally 0 on
    every run that recorded any spend, and one report.json published
    `total.unreported: 0` and `by_phase {"F1": {"unreported": 0}}` beside its
    own `unreported_dispatches {"count": 1, "by_phase": {"F1":
    ["casting-2"]}}` — a number that is provably false sitting beside the true
    one, in the same document.

    `unreported` is therefore DERIVED, from the summary `generate_report`
    hands in and hands to the `unreported_dispatches` section unchanged. Two
    renderings of one object cannot disagree; two readings of two ledgers can,
    and did. The roll-up's own claim is not discarded — it is checked, like
    tokens and milliseconds, so a stale roll-up shows in `disagreements`
    instead of being published as the answer.

    THE PAIR AXIS AND THE CYCLE AXIS ARE DIFFERENT MEASUREMENTS. `by_phase`
    and `total` count PAIRS, which is what FR-022 asks about; `by_cycle` names
    the cycles a pair's agent was dispatched in, so one stream agent unreported
    across three cycles is 1 in `total` and 1 in each of three cycle buckets.
    The section `note` says so, because a reader adding the cycle column up and
    finding more than the total is otherwise reading a contradiction.

    D-172 — AND THEY DIFFER IN COVERAGE, NOT ONLY IN KEYING.
    --------------------------------------------------------
    That last sentence was the whole of what the note said about the cycle
    axis, and it described a coverage the axis does not have. Only an agent
    whose dispatch record stamps a cycle can appear on it, and
    `_read_dispatch_summary`'s comment says why that is the F2 streams and
    nothing else. Driven at this door over the live archive: 19 unreported
    pairs, 14 of them CAST and GRIND teammates, and the cycle axis named ZERO
    teammates in any of cycles 0-9 — while the note beside the column stated
    that it "names the cycles those agents were dispatched in", false for 14 of
    the 19. Driven on a synthetic run with three teammates dispatched at cycle
    1 and one reporting spend, REPORT.md rendered the cycle row as 0 unreported
    while the phase row correctly rendered 2.

    So the note states the scope, and `unreported_without_cycle` publishes the
    size of the gap as a NUMBER the same summary derived — a zero in a cycle
    row is then readable rather than merely true. Prose that asserts a
    derivation without the archive having made it is the class; a count the
    archive did make is the answer to it.
    """
    records, problem = read_jsonl(run_dir / SPEND_LEDGER_FILENAME)
    if problem is not None:
        return {}, problem

    # GI-024 — THE ARITHMETIC IS `foundry_state.spend_rollup`, NOT A COPY HERE.
    #
    # There were three copies of it and the two live ones read DIFFERENT
    # sources: the orchestrator's `_spend_summary` read `state.json.spend` and
    # this reader re-aggregated `spend.jsonl`. The four defects named above are
    # what that cost, and the previous fix imported `_overlay_unreported` BACK
    # OUT of the orchestrator (`survey/architecture.md` §3.2: "closing the
    # import cycle rather than sharing the rule"). Both halves live in the leaf
    # now, so this module reaches only `schemas.vocab` and `tools.foundry_state`
    # — the contract its own header states — and the lazy back-import is gone.
    #
    # The reconciliation itself is documented at the reader, where the decision
    # belongs: the ledger is the authority for tokens, milliseconds and rows;
    # the roll-up is the authority for `agents` and the only source of it;
    # `unreported` is derived from neither; and where both answer, the
    # difference is NAMED in `disagreements` rather than one side being
    # published under a single label.
    table = spend_rollup(
        spend_rows=records,
        state_rollup=state.get("spend"),
        dispatch_summary=dispatch_summary or {},
    )
    without_cycle = table["unreported_without_cycle"]
    table["note"] = (
        "Tokens and minutes are the ledger's; `agents` is "
        "state.json.spend's, which is the orchestrator's own derivation "
        "and counts DISTINCT agents. `records` counts ledger rows, which "
        "is a different number whenever an agent reported twice (D-090). "
        "`unreported` is derived from the dispatch record and is the same "
        "derivation the Unreported dispatches section publishes — the run "
        "total is its `count` (D-163). Per phase it counts (agent, phase) "
        "pairs, and EVERY unreported pair is on that axis. The cycle rows "
        "are narrower: an agent reaches them only if its dispatch record "
        "stamps a cycle, and the only record that does is "
        "stream-rollup.json's own cycle bucket, so those rows cover the F2 "
        "stream agents and nothing else. spawns.log stamps a teammate "
        "dispatch with a timestamp and no cycle, so a CAST or GRIND "
        "teammate is counted per phase and appears in NO cycle row, "
        "however many cycles it ran in — of the "
        f"{_as_count((dispatch_summary or {}).get('count'))} unreported pairs "
        f"here, {without_cycle} are in that position and are absent "
        "from every cycle row below (D-172). One stream agent unreported "
        "across three cycles is 1 in the total and 1 in each of three "
        "cycle rows. So the cycle column neither adds up to the total nor "
        "is meant to."
    )
    return table, None


def _read_dispatch_summary(run_dir: Path) -> tuple[dict, str | None]:
    """AC-034 / CT-013 — the run's unreported-dispatch counts, derived ONCE.

    Returns ``(summary, problem)`` where ``summary`` is exactly
    `foundry_state.unreported_dispatch_summary`'s document. This function only
    supplies the run's inputs; every judgement about what an unreported
    dispatch IS, and every number derived from it, belongs to that helper.

    TWO SECTIONS, ONE OBJECT (D-163)
    --------------------------------
    `generate_report` calls this ONCE and hands the result to BOTH
    `_read_spend` (which writes the per-bucket `unreported` counts) and
    `_unreported_dispatches_section` (which lists the pairs). They used to
    reach the number two ways: the list came from this walk of the ledgers,
    while the spend buckets copied `state.json.spend[...]["unreported"]` — a
    field the spend door seeds to 0 and the orchestrator only ever overlays
    onto a throwaway copy, so it was structurally 0 on every run. One report
    therefore published `total.unreported: 0` and `by_phase {"F1":
    {"unreported": 0}}` beside `unreported_dispatches {"count": 1, "by_phase":
    {"F1": ["casting-2"]}}`. Reading one object is what makes that pair
    unrepresentable rather than merely fixed.

    Two SOURCES, because neither alone sees every agent. `spawns.log` records
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

    GI-024 — THE ASSEMBLY MOVED TOO, AND THE BACK-IMPORT WENT WITH IT.
    ------------------------------------------------------------------
    The RULE has been shared since D-047/D-048, but this function still built
    the summary's INPUTS with three inline ledger walks while
    `foundry_orchestrator` built the same inputs from four helpers of its own —
    one derivation of the answer over two derivations of the question. The
    walks are `foundry_state.unreported_dispatch_inputs` now, so the roster and
    the cycle map come off ONE walk of the roll-up for both surfaces.

    `DISPATCH_PHASE_TO_RUN_PHASE` moved with them. It was read out of
    `foundry_orchestrator` through a function-local import and a
    `getattr(..., {})` default, on the ground that a module-level import would
    close a cycle — true, and the reason the constant now lives in the leaf
    both surfaces already import. The `getattr` default is gone with it: a
    constant behind a default is a constant that can silently go missing, and
    the degradation it bought ("the verbs pass through as spelled") is a
    `by_phase` bucket keyed `grind`, which is not a phase.
    """
    inputs = unreported_dispatch_inputs(run_dir)
    if inputs["problem"] is not None:
        return {}, inputs["problem"]

    return unreported_dispatch_summary(
        dispatch_rows=inputs["dispatch_rows"],
        stream_roster=inputs["stream_roster"],
        spend_rows=inputs["spend_rows"],
        phase_of_dispatch=DISPATCH_PHASE_TO_RUN_PHASE,
        agent_id_of=_agent_id_for_casting,
        cycles_of_agent=inputs["cycles_of_agent"],
    ), None


def _unreported_dispatches_section(summary: dict) -> dict:
    """The `unreported_dispatches` section, rendered from the shared summary.

    Pure: it adds no number of its own. `count`, `dispatched`, `reported` and
    `by_phase` are `_read_dispatch_summary`'s, unchanged, so this section and
    the `unreported` column of the spend section are two RENDERINGS of one
    derivation rather than two derivations (D-163).
    """
    return {
        "count": summary.get("count", 0),
        "dispatched": summary.get("dispatched", 0),
        "reported": summary.get("reported", 0),
        "by_phase": summary.get("by_phase", {}),
        "note": (
            "Advisory only. An agent listed here was dispatched and never "
            "reported spend for THAT phase; no gate refuses on it (AC-034). "
            "The Unreported column of the spend section counts these same "
            "pairs — one derivation, two renderings (D-163)."
        ),
    }


def _read_unreported_dispatches(run_dir: Path) -> tuple[dict, str | None]:
    """The `unreported_dispatches` section for a run dir. ADVISORY (AC-034).

    `_read_dispatch_summary` plus `_unreported_dispatches_section`, for a
    caller that has a run directory and no summary in hand — `test_spend.py`'s
    cross-surface check is one. `generate_report` does NOT go through here: it
    holds the summary already, because `_read_spend` needs the same object
    (D-163), and calling this would re-read the three ledgers to re-derive a
    number it is holding.

    "Never a refusal" is the load-bearing half of AC-034 and it is enforced by
    construction: this section is a list, nothing reads it but the report, and
    no gate in the protocol takes it as an input. An unreported dispatch means
    a lead forgot to call `Foundry-Spend`, which is a gap in the measurement,
    not a defect in the build — and a run that could not reach DONE over a
    missed bookkeeping call would teach the lead to stop measuring. The
    ``problem`` this returns is an UNREADABLE LEDGER, which is the generator's
    refusal rule and not a judgement about the dispatches.
    """
    summary, problem = _read_dispatch_summary(run_dir)
    if problem is not None:
        return {}, problem
    return _unreported_dispatches_section(summary), None


OBSERVATIONS_FILENAME = "observations.json"


def _read_undriven_temper_candidates(run_dir: Path) -> tuple[dict, str | None]:
    """AC-020 / OT-022 / ST-007 — every recorded probe idea nobody drove.

    Returns ``({"candidates": [...], "count": int, "driven_count": int},
    problem)``.

    ST-007's transition is `TEMPER_CANDIDATE observation open` -> `DRIVEN
    (filed or clean)`, and its guard reads "a candidate never driven is listed
    in the F6 report". AC-020 narrows that to the case that actually happens:
    "When TEMPER never ran, the F6 report lists every TEMPER candidate that was
    not driven." Listing them on EVERY run is the superset and the honest one —
    a run where TEMPER ran and skipped three candidates has the same debt as a
    run where TEMPER never ran at all, and a section that fired only on the
    second would hide the first.

    WHY THIS SITS IN THE HARDENING BACKLOG AND NOT IN A SECTION OF ITS OWN.
    ----------------------------------------------------------------------
    AC-047 grows `REPORT_REQUIRED_SECTIONS` by exactly FOUR and this is not one
    of them, so it joins the section a lead already reads for carried-forward
    work that blocks nothing. The two lists answer one question about different
    evidence: a HARDENING row is a probe that WAS driven and failed, and a
    candidate is a probe that was never driven — "the research finding this
    run's own reality.md records, that a backlog tier with no promotion cadence
    becomes write-only debt", applies to both, and the cadence's visible half
    is that they are printed together.

    BOTH LEDGER SHAPES (FR-054). The observation record shipped today carries
    no driven marker at all, and casting 4's door plus casting 11's TEMPER add
    one. A record with neither `driven` nor `status` is UNDRIVEN, which is the
    correct reading of every archive written before this release: nothing
    recorded that it was driven, so nothing may claim it was. Both spellings
    are accepted because the two castings land at different waves and a report
    that knew only one would silently list a driven candidate as open.
    """
    document, problem = read_document(run_dir / OBSERVATIONS_FILENAME)
    if problem is not None:
        return {"candidates": [], "count": 0, "driven_count": 0}, problem
    records = document.get("observations")
    if not isinstance(records, list):
        records = []

    candidates: list[dict] = []
    driven = 0
    for record in records:
        if not isinstance(record, dict):
            continue
        if record.get("classification") != TEMPER_CANDIDATE:
            continue
        status = record.get("status")
        if record.get("driven") or (
            isinstance(status, str) and status.upper() == "DRIVEN"
        ):
            driven += 1
            continue
        candidates.append(
            {
                "id": record.get("id"),
                "cycle": record.get("cycle"),
                "source": record.get("source"),
                **_location_fields(record),
                "description": record.get("description"),
            }
        )
    return {
        "candidates": candidates,
        "count": len(candidates),
        "driven_count": driven,
    }, None


def _stream_coverage_section(run_dir: Path) -> tuple[dict, str | None]:
    """CT-003 / AC-030 / OT-028 — per (stream, cycle) coverage, replacements named.

    Every number comes from `foundry_state.stream_rollup_rows`; this function
    adds the section's prose and nothing else. The derivation is in the leaf
    because `Foundry-Next`'s streams-complete check walks the same buckets, and
    a report that re-walked them would be the third opinion about which keys in
    a cycle bucket are stream records — the D-182 shape, where two hand-typed
    copies of one rule left a third reader never learning it at all.

    WHAT IS RENDERED AND NOT NORMALISED (FR-054). A bucket written by the old
    ADDITIVE writer carries no `records[]`, and `daring-orca`'s buckets read
    ABOVE 100% because tranches were summed rather than replaced. Both are
    printed as they are and NAMED — `over_total` and `buckets_without_records`
    — because a coverage figure quietly clamped to its total is a measurement
    replaced by an assertion, and the whole point of the replace semantics
    casting 2 lands is that a run can see which records were superseded.
    """
    table = stream_rollup_rows(run_dir)
    problem = table.get("problem")
    if problem is not None:
        return {}, problem

    rows: list[dict] = []
    for cycle, streams in table["cycles"].items():
        for stream, row in streams.items():
            rows.append({
                "cycle": cycle,
                "stream": stream,
                "items_checked": row["items_checked"],
                "items_total": row["items_total"],
                "findings": row["findings"],
                "record_count": row["record_count"],
                "replaced_count": row["replaced_count"],
                "over_total": row["over_total"],
            })
    replaced_total = sum(r["replaced_count"] for r in table["replaced"])
    return {
        "cycle_count": table["cycle_count"],
        "stream_count": table["stream_count"],
        "row_count": len(rows),
        "rows": rows,
        "replaced": table["replaced"],
        "replaced_record_count": replaced_total,
        "over_total": table["over_total"],
        "buckets_without_records": table["buckets_without_records"],
        "note": (
            "One row per (stream, cycle). `Records` counts the tranches "
            "recorded for that pair and `Replaced` is one fewer — the FIRST "
            "record replaced nothing and every later one replaced exactly the "
            "record before it (ST-009), so a second recording reads as a "
            "REPLACEMENT and never as extra coverage. A row with 0 records was "
            "written by the additive writer that predates the replace "
            "semantics and is listed under `buckets_without_records`: it is "
            "not the same fact as a pair recorded once. A `Checked` above "
            f"`Total` ({len(table['over_total'])} row(s) here) is that same "
            "additive writer summing tranches; it is printed as recorded and "
            "never clamped, because a clamped coverage figure is an assertion "
            "wearing a measurement's clothes."
        ),
    }, None


def _halt_and_co_dispatch_section(run_dir: Path, state: dict) -> tuple[dict, str | None]:
    """CT-004 / AC-025's report half / CT-008 — how the run ended, and what it dispatched.

    Returns the halt reason as a MEMBER of `HALT_REASONS` plus the lead's own
    free text, and the co-dispatch set computed at each GRIND.

    BOTH SPELLINGS OF `halted_reason`, BECAUSE BOTH EXIST (FR-054). Today the
    field is a free f-string — "--max-cycles 2 reached: opening GRIND cycle 3
    would exceed it" — and FR-019 makes it `{reason, text}`. This reads both:
    the member when there is one, the raw text when there is not, and NEVER a
    member guessed out of a sentence. `vocab.halt_reason` returns None on an
    unrecognised value for exactly that reason, and None here means "this run
    recorded text and no member", which is what every pre-release archive
    carries.

    THE CO-DISPATCH SETS ARE READ, NEVER COMPUTED HERE (GI-021 / CT-008). The
    alignment block and the co-dispatch set are SERVER-GENERATED at
    `Foundry-Tasks` time and appended to `handoffs.jsonl`; a report that
    recomputed them from the manifest would be a second answer to "which
    castings were dispatched together", available to disagree with the one the
    lead actually acted on. A run with no such record renders an empty section
    — which is every run until casting 2 lands the writer, and is why this
    section reads a missing field as missing rather than as an error.
    """
    records, problem = read_jsonl(run_dir / HANDOFFS_FILENAME)
    if problem is not None:
        return {}, problem

    raw = state.get("halted_reason")
    text: str | None = None
    member: str | None = None
    if isinstance(raw, dict):
        member = halt_reason(raw.get("reason"))
        candidate = raw.get("text")
        text = candidate if isinstance(candidate, str) and candidate else None
    elif isinstance(raw, str) and raw:
        member = halt_reason(raw)
        # A recognised member as a BARE string carries no text of its own; an
        # unrecognised string IS the text. Printing the member twice — once as
        # the member and once as "the lead's own words" — would invent a
        # sentence the lead never typed.
        text = None if member else raw

    dispatched: list[dict] = []
    for record in records:
        sets = record.get("co_dispatch")
        if not sets:
            continue
        dispatched.append({
            "cycle": record.get("cycle"),
            "phase": record.get("phase"),
            "event": record.get("event"),
            "timestamp": record.get("timestamp"),
            "co_dispatch": sets,
            "defect_ids": record.get("defect_ids"),
            "requirement_ids": record.get("requirement_ids"),
        })

    halted = state.get("phase") == RUN_PHASE_HALTED
    return {
        "halted": halted,
        "halted_at_cycle": state.get("halted_at_cycle"),
        "reason": member,
        "reason_text": text,
        "reason_recorded": raw if isinstance(raw, (str, dict)) else None,
        "max_cycles": state.get("max_cycles"),
        "co_dispatch_count": len(dispatched),
        "co_dispatch": dispatched,
        "note": (
            "HALTED is a named terminal state and is NOT DONE (ST-008): the "
            "report is generated and every open defect is named in it. `Reason` "
            "is a member of the halt vocabulary and `Reason text` is the lead's "
            "own words; a run whose `halted_reason` predates FR-019 carries a "
            "free sentence and no member, and that sentence is printed as text "
            "rather than guessed onto a member. The co-dispatch rows are the "
            "sets the SERVER computed at each Foundry-Tasks call and the lead "
            "acted on — read from the handoff ledger, never recomputed here, "
            "because a second answer to 'which castings went out together' "
            "could disagree with the one that was actually dispatched."
        ),
    }, None


#: The five fields this section publishes, in the order both documents render
#: them. Named once so the reader, the note and the markdown table cannot come
#: to disagree about which fields the section is about.
_EXECUTING_VERSION_FIELDS = (
    "server_version", "plugin_version", "server_root", "server_commit",
    "self_target",
)  # 5 fields


def _executing_versions_section(state: dict) -> dict:
    """GI-004 / CT-010 — which server actually executed this run.

    D-168 — THE ONE SECTION THAT RENDERED BLANKS SILENTLY.
    ------------------------------------------------------
    This was five bare `state.get` calls under a two-column table with no
    prose. Driven over the live archive it printed five rows with every Value
    cell empty, and `report.json` carried null for each — the two documents
    agreed, which is why nothing was false, and a reader still could not tell
    "this run recorded no version fields" from "the report dropped them".

    That distinction is the whole reason AC-036 names this section for a
    self-targeting run: `Foundry-Init` writes these four fields exactly when it
    performed the self-target preflight (CT-010), so their ABSENCE is itself
    the finding — nothing compared the executing server against the working
    tree — and it is a different finding from a generator that lost them.

    Every sibling that can render an absent value already states why: the spend
    section says its Agents cells "are blank for want of that source, not
    because they are zero", the baseline section says its three columns "are
    null rather than fabricated, and nothing was derived", and the LATENT
    backlog explains its `(none recorded)` cells (D-103). This says it too, in
    `report.json` as well as in the markdown, so a mechanical reader gets the
    same statement the operator does — `_location_fields`' rule, applied one
    section over.

    `recorded` and `missing` are the fields, not a boolean over the section:
    "no commit but a version" and "nothing at all" are different states of a
    preflight and a lead routes on which.
    """
    values = {field: state.get(field) for field in _EXECUTING_VERSION_FIELDS}
    # A field is MISSING when the state document has no key for it or the key
    # is null — the two are one fact here, and splitting them would give the
    # markdown a blank cell the note said was recorded. The test is `is None`
    # and not truthiness: `self_target` is legitimately False on every run that
    # is not self-targeting, and `or`-style emptiness would report the
    # commonest healthy run as unmeasured.
    missing = [
        field for field in _EXECUTING_VERSION_FIELDS if values[field] is None
    ]
    if not missing:
        note = (
            "Foundry-Init recorded all five fields for this run (CT-010). "
            "`server_commit` reading 'unknown' is git being unavailable at "
            "init, which is never treated as a match."
        )
    else:
        note = (
            "state.json carries no " + ", ".join(missing) + " for this run, "
            "so the cell(s) below are blank because the run recorded nothing "
            "there — not because this report dropped a value. Foundry-Init "
            "writes these fields when it runs its self-target preflight "
            "(CT-010), so their absence is the finding: nothing compared the "
            "executing server against the working tree."
        )
    return {**values, "recorded": not missing, "missing": missing, "note": note}


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


#: Why `_archive_metrics` publishes None for each of the two cycle numbers the
#: baseline records — in the words a reader of the table needs, not the token a
#: reader of this module would need (D-151).
#:
#: It lives beside `_archive_metrics` because that is the function whose Nones
#: it explains, and each entry is a restatement of a rule stated in prose a few
#: lines down: `derive_cycle_count`'s "``count`` is None only when NO source
#: could supply a number", and this function's "None, not 0, when the archive
#: recorded no ``inspect_modes`` at all". A third cycle metric added to
#: `_archive_metrics` without an entry here raises `KeyError` at the note site
#: rather than printing a bare `None` again, which is the whole failure D-151
#: names.
_UNDERIVABLE_REASON: dict[str, str] = {
    "grind_cycles": (
        "neither its state counter, its stream roll-up nor its defect ledger "
        "can supply a cycle number"
    ),
    "post_verification_cycles": (
        "it recorded no inspect_modes list, so the F5 INSPECTs it opened "
        "cannot be counted"
    ),
}  # 2 entries — the two keys THUNDER_VIPER_BASELINE records


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
        inspect_modes = _inspect_modes_section(state, run_dir)

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
        inspect_modes = _inspect_modes_section(state, run_dir)
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
        # D-151 — "CANNOT DERIVE" IS NOT "DERIVES A DIFFERENT NUMBER".
        #
        # D-135 fixed the `self` branch of this note; this is the same class
        # one branch over, inside the branch D-135 left alone. `differs` was a
        # single dict keyed on `derived != recorded`, and None satisfies that
        # test against every recorded number — so an archive that CANNOT say
        # what it did was reported as having said something. Driven at HEAD,
        # through both doors that render this object:
        # `measure-run.py foundry-archive/daring-orca` and the REPORT.md
        # generator over the same archive both emitted "That archive currently
        # DERIVES post_verification_cycles None, shown in the Derived column"
        # — while the Derived cell on that row was EMPTY, because `_md_table`
        # renders None as a blank. One sentence naming a column that showed
        # nothing, asserting a derivation the archive never produced, with the
        # Python repr of the absence standing in for the number.
        #
        # `_archive_metrics`' docstring is the authority the sentence broke:
        # "Every metric is None when its ledger cannot supply it." So the two
        # outcomes are separated HERE and each gets its own sentence, which is
        # exactly the property D-135 installed one branch along — an outcome
        # that cannot be added without giving it words. Dropping the null keys
        # from the note instead would leave the empty cell explained by
        # nothing, which is the unexplained-blank failure D-029 and D-103
        # already had to fix in the LATENT backlog.
        derived_differs: dict[str, Any] = {}
        underivable: dict[str, str] = {}
        for key in ("grind_cycles", "post_verification_cycles"):
            derived_value = baseline_derived.get(key)
            if derived_value == baseline_recorded.get(key):
                continue
            if derived_value is None:
                underivable[key] = _UNDERIVABLE_REASON[key]
            else:
                derived_differs[key] = derived_value
        if derived_differs:
            # WORDING UNCHANGED on this arm. It is the arm the note always
            # described correctly, and `test_report.py` pins its spelling
            # ("DERIVES grind_cycles 31"); D-151 is about the arm that had no
            # sentence of its own, not about this one.
            baseline_note += (
                " That archive currently DERIVES "
                + ", ".join(f"{k} {v}" for k, v in sorted(derived_differs.items()))
                + ", shown in the Derived column. The recorded constant stands;"
                " a derivation that disagrees with it is a fact about the"
                " archive, not a better measurement of the run."
            )
        if underivable:
            baseline_note += (
                " That archive CANNOT DERIVE "
                + ", ".join(
                    f"{key} ({why})" for key, why in sorted(underivable.items())
                )
                + f", so the Derived column is empty on {'those rows' if len(underivable) > 1 else 'that row'}"
                " rather than showing a number — an absence of measurement,"
                " not a measurement of zero. The recorded constant is what the"
                " Baseline column shows, exactly as it would be if the"
                " derivation agreed."
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
    "hardening_backlog": "HARDENING backlog",
    "unknown_tier_defects": "Unknown-tier defects",
    "fallout_per_cycle": "Fallout per cycle",
    "escalated_classes": "Escalated classes",
    "lead_fix_records": "Lead fix records",
    "inspect_modes_per_cycle": "INSPECT mode per cycle",
    "stream_coverage_per_cycle": "Stream coverage per cycle",
    "spend_per_phase_and_cycle": "Spend per phase and cycle",
    "unreported_dispatches": "Unreported dispatches",
    "halt_and_co_dispatch": "Halt and co-dispatch",
    "executing_versions": "Executing server and plugin versions",
    "baseline_comparison": "Baseline comparison",
}

assert set(_SECTION_TITLES) == set(REPORT_REQUIRED_SECTIONS), (
    "every REPORT_REQUIRED_SECTIONS member needs a markdown title: "
    f"{sorted(set(REPORT_REQUIRED_SECTIONS) ^ set(_SECTION_TITLES))}"
)


def _full_ratio_sentence(ratio: dict) -> str:
    """AC-046 — the FULL-cycle ratio with pass/fail stated against 50%.

    A-037's figure verbatim: "Ratio: FULL cycles / total INSPECT cycles below
    50%". The sentence is built here from `foundry_state.full_cycle_ratio`'s
    document rather than computed, so `measure-run.py`'s `full_cycle_ratio` and
    this line are one derivation and two renderings.

    `passes` is None, never False, on a run where no INSPECT recorded a width,
    and this says so in WORDS rather than interpolating the None (D-151's
    class): "no INSPECT recorded a width" and "more than half were FULL" are
    different answers and a pass/fail marker cannot carry both.
    """
    passes = ratio.get("passes")
    total = ratio.get("total_cycles", 0)
    full = ratio.get("full_cycles", 0)
    if passes is None:
        return (
            "NOT MEASURABLE — no cycle carries a recorded INSPECT width, so "
            "the FULL-cycle ratio AC-046 measures cannot be derived. A run "
            "that recorded no width and a run that ran narrow are different "
            "facts and this line will not print one as the other."
        )
    marker = "PASS" if passes else "FAIL"
    return (
        f"{marker} — FULL-cycle ratio {ratio.get('ratio')} ({full} of {total} "
        f"cycles ran at FULL width), against AC-046's threshold of "
        f"{ratio.get('threshold')}. A cycle counts as FULL when ANY decision "
        f"in it was FULL, because a cycle that ran a five-stream INSPECT paid "
        f"for one however many times it was reopened."
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
        # D-234 — THE ONE SURFACE THE CYCLE-27 RULING DID NOT REACH.
        #
        # This sentence used to end "prose may be appended, no section may be
        # removed", which was GI-006 read as a merge: type anywhere, it
        # survives. The seal does not work that way and has not since the
        # cycle-27 ruling — `_carried_lead_prose` tells lead prose from
        # generated body by the HEADING, because any rule that tells them apart
        # by comparing VALUES re-emits a moved generated row as the lead's own
        # words (D-228). So a paragraph typed under `## Verdict matrix` is
        # regenerated away at the next terminal transition, silently:
        # `lead_prose_lines: 0`, no warning. Every other surface that states
        # the rule — the done-gate hint, the Foundry-Report tool description,
        # `display.py`'s footer, `commands/start.md`, both READMEs — was
        # rewritten then; this banner was not, and it is the sentence a lead
        # reads at the moment they choose where to type.
        #
        # ONE LINE, AND IT MUST CONTAIN " by Foundry-Report.".
        # `foundry_orchestrator._lead_header_lines` drops a header line that
        # carries that marker and keeps every other non-blank one as the
        # lead's. Split this sentence across two rendered lines and the half
        # without the marker is carried into the seal's Lead notes section on
        # every terminal transition — the document growing a paragraph of its
        # own banner per seal.
        #
        # THE LEAD-NOTES SECTION IS NAMED WITHOUT ITS `## ` PREFIX, which is
        # the spelling both READMEs already use for it. Spelled in full, this
        # sentence would contain `foundry_orchestrator._LEAD_NOTES_HEADING`
        # verbatim, and the seal's regression tests read "the seal appended
        # nothing" as that constant being ABSENT from the whole document
        # (`test_orchestrator_gates.py`, two sites). A banner that names the
        # section would then make a purely generated report indistinguishable
        # from a sealed one to those tests. The heading itself is still a whole
        # trimmed `## ` line and nothing else, which is what `_md_sections`
        # matches on.
        f"Generated {generated_at} by Foundry-Report. Every section below is "
        "generated from the run's own ledgers (GI-006) and no section may be "
        "removed. Append your prose under your OWN `## ` heading, or above the "
        "first generated section: the F6 seal regenerates this document from "
        "the ledgers and carries those lines verbatim into a trailing "
        "`Lead notes (carried by the seal)` section. Prose typed INSIDE a "
        "generated section's body is not carried — it is regenerated away.",
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
        # D-167, D-150's class one renderer over. `cycle` is `verdicts.json`'s
        # own key, read through `.get`, and this sentence interpolated it raw
        # while GUARDING its two neighbours in the same f-string — `count`
        # defaults to 0 and `by_verdict` falls back to '{}'. A verdicts.json
        # without the key rendered the operator-facing line "verdicts at cycle
        # None: {}": a Python None printed as a fact.
        #
        # `or` is not the guard. Cycle 0 is a real cycle — the counter is
        # 0-based (ST-001) and a first-INSPECT verdict set is stamped with it —
        # so `value.get('cycle') or ...` would report the run's first cycle as
        # unrecorded. The int test is the module's own, `_as_count`'s and
        # `derive_cycle_count`'s: an int that is not a bool.
        cycle = value.get("cycle")
        at_cycle = (
            f"at cycle {cycle}"
            if isinstance(cycle, int) and not isinstance(cycle, bool)
            else "at a cycle verdicts.json does not record"
        )
        return (
            [f"{value.get('count', 0)} requirements, verdicts {at_cycle}: "
             f"{value.get('by_verdict') or '{}'}", ""]
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
    if key == "hardening_backlog":
        # AC-024 — beside the LATENT backlog, and carrying the location for
        # D-029's reason: the next run's lead receives this list with no
        # defects.json to join against.
        #
        # `Reproduction` is not optional decoration here the way it is on a
        # LATENT row. A HARDENING filing is a DRIVEN failure, so the statement
        # of what was driven and what it did IS the evidence the tier rests on
        # (GI-014) — a row without one is a record that should never have been
        # accepted, and printing the column is what makes that visible.
        candidates = value.get("undriven_temper_candidates") or []
        temper_ran = value.get("temper_ran")
        if temper_ran is False:
            temper_clause = (
                f"TEMPER never ran on this run, so all {len(candidates)} "
                "recorded candidate(s) are undriven (AC-020)."
            )
        elif temper_ran is True:
            temper_clause = (
                f"TEMPER ran and left {len(candidates)} recorded candidate(s) "
                f"undriven; {value.get('driven_candidate_count', 0)} were "
                "closed as DRIVEN (ST-007)."
            )
        else:
            temper_clause = (
                f"{len(candidates)} recorded TEMPER candidate(s) were never "
                "driven; whether TEMPER ran at all is not recorded in this "
                "run's state."
            )
        return (
            [f"{value.get('open_count', 0)} open HARDENING defects carried "
             "forward. A HARDENING record is a DRIVEN failure that no "
             "requirement asks about (GI-014): the stream ran a probe of its "
             "own and observed a wrong result, so the Reproduction column is "
             "the evidence the row rests on. None of these blocks a gate — "
             "`BLOCKING_TIERS` holds LIVE and the unknown sentinel and nothing "
             "else — and promotion is a NEW filing that cites the id through "
             "`supersedes`, never a re-tier in place (GI-022).", ""]
            + _md_table(
                ["ID", "Class", "Cycle", "File", "Symbol", "Source",
                 "Reproduction attempted", "Supersedes", "Description"],
                [[d.get("id"), d.get("class"), d.get("cycle"),
                  d.get("file") or NO_LOCATION_CELL,
                  d.get("symbol") or NO_LOCATION_CELL, d.get("source"),
                  d.get("reproduction_attempted") or NO_LOCATION_CELL,
                  d.get("supersedes"), d.get("description")]
                 for d in value.get("defects", [])],
            )
            + ["",
               "### Undriven TEMPER candidates",
               "",
               temper_clause + " A candidate is a probe idea nobody has "
               "driven, so it is neither a defect nor a HARDENING record — it "
               "sits here because it is the same kind of debt: work this run "
               "identified and did not do, blocking nothing, received by the "
               "next run's lead.",
               ""]
            + _md_table(
                ["ID", "Cycle", "Source", "File", "Symbol", "Description"],
                [[c.get("id"), c.get("cycle"), c.get("source"),
                  c.get("file") or NO_LOCATION_CELL,
                  c.get("symbol") or NO_LOCATION_CELL, c.get("description")]
                 for c in candidates],
            )
        )
    if key == "fallout_per_cycle":
        # FR-025 / AC-046's sibling figure. The verdict is stated BESIDE the
        # counts rather than left for the reader to compute, because the
        # criterion is defined over a pair of cycles and a reader scanning a
        # column of zeros cannot see which pair is the closing one.
        verdict = str(value.get("verdict", "not_measurable"))
        marker = {"pass": "PASS", "fail": "FAIL"}.get(verdict, "NOT MEASURABLE")
        rows = [[cycle, b.get("fallout"), b.get("measured"), b.get("unmeasured"),
                 ", ".join(str(i) for i in (b.get("ids") or []))]
                for cycle, b in (value.get("per_cycle") or {}).items()]
        return (
            [f"{marker} — {value.get('verdict_reason', '')}", "",
             f"{value.get('total', 0)} filing(s) in this run are fallout of an "
             f"earlier defect. {value.get('measured_records', 0)} defect "
             f"record(s) carry the `fallout_of` field and "
             f"{value.get('unmeasured_records', 0)} carry no such key at all: "
             "the second group predates the field, which reads as "
             "STRUCTURALLY ABSENT and never as a measured zero, because "
             "certifying the criterion on an archive that never measured it "
             "would be the easiest pass in the document.", ""]
            + _md_table(
                ["Cycle", "Fallout", "Measured", "Unmeasured", "IDs"], rows,
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
        # D-193: the headline says "cycles that carry one", never "cycles",
        # because the two are different numbers on any run whose ledger starts
        # partway through — and the axis they differ against is the sentence
        # below, built once in the section so both documents carry it.
        return (
            [f"{value.get('count', 0)} recorded INSPECT-opening decisions over "
             f"the {value.get('cycle_count', 0)} cycles that carry one; by "
             f"mode: {value.get('by_mode')}. One row per decision: a cycle "
             "carries two rows when an F5 INSPECT reopened it after its F2 "
             "one, and neither is dropped.",
             "",
             _full_ratio_sentence(value.get("full_cycle_ratio") or {}),
             "",
             str(value.get("note", "")),
             ""]
            + _md_table(
                ["Cycle", "Phase", "Mode", "Rule", "Decided by", "Required streams"],
                [[d.get("cycle"), d.get("phase"), d.get("mode"), d.get("rule"),
                  d.get("decided_by"),
                  ", ".join(str(s) for s in (d.get("required_streams") or []))]
                 for d in _inspect_decisions(value)],
            )
        )
    if key == "stream_coverage_per_cycle":
        rows = [[r.get("cycle"), r.get("stream"), r.get("items_checked"),
                 r.get("items_total"), r.get("findings"), r.get("record_count"),
                 r.get("replaced_count"), "yes" if r.get("over_total") else ""]
                for r in value.get("rows", [])]
        return (
            [f"{value.get('row_count', 0)} (stream, cycle) pair(s) over "
             f"{value.get('cycle_count', 0)} cycle(s); "
             f"{value.get('replaced_record_count', 0)} record(s) were replaced "
             "by a later recording for the same pair.", "",
             str(value.get("note", "")), ""]
            + _md_table(
                ["Cycle", "Stream", "Checked", "Total", "Findings", "Records",
                 "Replaced", "Over total"],
                rows,
            )
        )
    if key == "halt_and_co_dispatch":
        halted = bool(value.get("halted"))
        reason = value.get("reason")
        text = value.get("reason_text")
        if not halted:
            headline = (
                "This run did not halt. A halt is a named terminal state "
                "reached by a SUCCESSFUL transition (ST-001), so its absence "
                "here means the run ended some other way — not that a halt "
                "was refused."
            )
        elif reason:
            headline = (
                f"HALTED at cycle {value.get('halted_at_cycle')} — reason "
                f"`{reason}`."
            )
            if text:
                headline += f" The lead's own words: {text}"
        else:
            # D-151's class: a None interpolated into operator prose reads as a
            # fact. The unknown is stated in WORDS, and the sentence names the
            # shape it is describing, exactly as the spend section's headline
            # does for its own null.
            headline = (
                f"HALTED at cycle {value.get('halted_at_cycle')}. This run's "
                "`halted_reason` carries no member of the halt vocabulary — it "
                "predates FR-019, when the field was a free sentence — so the "
                "text is printed as recorded rather than guessed onto a "
                f"member: {text or value.get('reason_recorded') or NO_LOCATION_CELL}"
            )
        rows = [[d.get("cycle"), d.get("phase"), d.get("event"),
                 ", ".join(str(c) for c in (d.get("co_dispatch") or [])),
                 ", ".join(str(i) for i in (d.get("defect_ids") or [])),
                 ", ".join(str(i) for i in (d.get("requirement_ids") or []))]
                for d in value.get("co_dispatch", [])]
        return (
            [headline, "", str(value.get("note", "")), ""]
            + _md_table(
                ["Cycle", "Phase", "Event", "Co-dispatched castings",
                 "Originating defects", "Requirement IDs"],
                rows,
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
        # D-151's class on this section's own headline (D-150). `agents` is the
        # ROLL-UP's number or it is nothing — `foundry_state.spend_bucket`
        # seeds it None and the roll-up fills it only from
        # `state.json.spend`, on the
        # stated ground that "nobody recorded how many agents" and "no agents
        # ran" are different facts. This sentence interpolated that None raw
        # while the adjacent `records` used `.get('records', 0)`, so at the
        # moment a --max-cycles halt generates a report over a run with no
        # spend roll-up — the normal state at a halt — operator-facing prose
        # read "0 spend records over None distinct agents." The Agents column
        # four lines below rendered the SAME null as a blank cell: one null,
        # two spellings, in one section, and the one the reader met first was a
        # Python repr.
        #
        # `or 0` is not the fix. It would print the fabrication `_read_spend`
        # refuses by name, and report.json would still carry null beside it —
        # trading a visible repr for two documents that disagree. So the
        # unknown is stated in WORDS, and the sentence names the blank cells it
        # is describing, which is how the LATENT backlog's prose already
        # handles its own null cells (D-103).
        #
        # D-163 narrowed this sentence to the ONE column it is still true of.
        # It used to say "the Agents and Unreported cells below are blank for
        # want of that source": `unreported` no longer comes from the roll-up
        # at all — it is derived from the dispatch record — so it is a real
        # number on exactly the runs this arm describes, and naming it here
        # would send a reader to look for a blank cell carrying a count.
        agents = total.get("agents")
        if isinstance(agents, int) and not isinstance(agents, bool):
            headline = (
                f"{value.get('records', 0)} spend records over {agents} "
                "distinct agents."
            )
        else:
            headline = (
                f"{value.get('records', 0)} spend records; how many DISTINCT "
                "agents produced them was not recorded, because "
                "`state.json.spend` — the roll-up that is the only source for "
                "that count — carries none. The Agents cells below are blank "
                "for want of that source, not because they are zero. The "
                "Unreported cells are derived from the dispatch record and "
                "are counts either way."
            )
        return (
            # "Reported as", not "Cost is": NFR-002 bans the money frame, and
            # the word invites a reader to supply the rate table the section
            # deliberately does not keep. `test_report.py`'s currency scan
            # drove this — it matched the generator's own sentence.
            [f"{headline} Reported as tokens and minutes only (NFR-002).", ""]
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
        # D-168: the note comes FIRST and the blank cells carry the same
        # `(none recorded)` spelling the LATENT backlog uses, so the two
        # documents make the same statement and an unrecorded field is
        # distinguishable from a dropped one in either. `is None` and not
        # falsiness: `self_target: false` is a recorded answer.
        missing = value.get("missing") or []
        return (
            [str(value.get("note", "")), ""]
            + _md_table(
                ["Field", "Value"],
                [[k, NO_LOCATION_CELL if k in missing or value.get(k) is None
                  else value.get(k)]
                 for k in _EXECUTING_VERSION_FIELDS],
            )
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

    # D-163 — ONE summary, TWO sections. `spend_per_phase_and_cycle`'s
    # `unreported` column and the `unreported_dispatches` section are rendered
    # from this single object, so the document cannot carry a count of 0 beside
    # a list of one. Read BEFORE the spend section, because that section needs
    # it and the refusal it would raise names these three ledgers rather than
    # the spend one.
    dispatch_summary, problem = _read_dispatch_summary(run_dir)
    if problem is not None:
        return _refusal(f"{SPAWNS_FILENAME} / {SPEND_LEDGER_FILENAME} / {ROLLUP_FILENAME}",
                        problem)
    unreported = _unreported_dispatches_section(dispatch_summary)

    spend, problem = _read_spend(run_dir, state, dispatch_summary)
    if problem is not None:
        return _refusal(SPEND_LEDGER_FILENAME, problem)

    inspect_modes = _inspect_modes_section(state, run_dir)

    stream_coverage, problem = _stream_coverage_section(run_dir)
    if problem is not None:
        return _refusal(ROLLUP_FILENAME, problem)

    halt_section, problem = _halt_and_co_dispatch_section(run_dir, state)
    if problem is not None:
        return _refusal(HANDOFFS_FILENAME, problem)

    candidates, problem = _read_undriven_temper_candidates(run_dir)
    if problem is not None:
        return _refusal(OBSERVATIONS_FILENAME, problem)

    # AC-020's list joins the HARDENING backlog, which is where a lead already
    # reads for carried-forward work that blocks nothing. `state.json.temper`
    # is the record of whether the phase ran at all; `None` when the run
    # recorded neither, which the section states in words rather than assuming
    # either way.
    hardening = dict(defect_sections["hardening_backlog"])
    hardening["undriven_temper_candidates"] = candidates["candidates"]
    hardening["driven_candidate_count"] = candidates["driven_count"]
    temper = state.get("temper")
    hardening["temper_ran"] = (
        bool(temper) if isinstance(temper, (bool, dict, str)) and temper != "" else None
    )

    # FR-025 / AC-046's sibling. The axis is `derive_cycle_count`'s index — the
    # SAME reading `inspect_modes_per_cycle` and `baseline_comparison` sit on —
    # so the three sections cannot publish three different ideas of which
    # cycles this run ran.
    fallout = fallout_rows(run_dir, axis_top=derive_cycle_count(run_dir)["index"])
    if fallout.get("problem") is not None:
        return _refusal(DEFECTS_FILENAME, fallout["problem"])

    sections: dict[str, Any] = {
        "verdict_matrix": verdict_matrix,
        "defects_by_tier_and_status": defect_sections["defects_by_tier_and_status"],
        "latent_backlog": defect_sections["latent_backlog"],
        "hardening_backlog": hardening,
        "unknown_tier_defects": defect_sections["unknown_tier_defects"],
        "fallout_per_cycle": fallout,
        "escalated_classes": escalated,
        "lead_fix_records": lead_fixes,
        "inspect_modes_per_cycle": inspect_modes,
        "stream_coverage_per_cycle": stream_coverage,
        "spend_per_phase_and_cycle": spend,
        "unreported_dispatches": unreported,
        "halt_and_co_dispatch": halt_section,
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
    # Holmes `share-10` — ONE heading rule, not two that "agree by convention".
    # This built `{line.strip() for line in text.splitlines()}` while the
    # seal's `_md_sections` matched a line that STARTS a block: the same
    # effective rule, coded independently, so the gate could call a section
    # present that the seal did not treat as one. `markdown_headings` is
    # DERIVED from the splitter, so the two cannot part.
    headings = markdown_headings(text)
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
