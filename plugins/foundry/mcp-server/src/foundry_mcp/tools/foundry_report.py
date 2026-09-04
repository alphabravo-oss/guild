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
    escalation_status,
)
from foundry_mcp.tools.foundry_state import (
    derive_cycle_count,
    handoffs_wall_clock_seconds,
    is_stream_record,
    read_document,
    read_jsonl,
    read_text_file,
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
    classes = data.get("classes")
    if not isinstance(classes, dict):
        classes = {}

    rows: list[dict] = []
    by_status = dict.fromkeys(sorted(ESCALATION_STATUSES), 0)
    by_exit_reason = dict.fromkeys(sorted(ESCALATION_EXIT_REASONS), 0)
    for name, entry in sorted(classes.items()):
        # D-214 — THE VOCABULARY DECIDES, AND IT DECIDES HERE FIRST.
        #
        # This read was `status if isinstance(status, str) else "ESCALATED"`,
        # which is not the closed vocabulary — it is a shape test wearing the
        # vocabulary's default. ANY string passed through verbatim, so on
        # classes {"clean-class": {"status": "CLEARED"}, "bogus-class":
        # {"status": "BOGUS"}, "live-class": {"status": "ESCALATED"}} the
        # section reported `count` 3 while `by_status` summed to 2 and the row
        # read "BOGUS" — a status no writer in this system emits, published in
        # a terminal artifact, counted in neither bucket.
        #
        # `vocab.escalation_status` is the ONE resolver now — the structural
        # fix closing D-214 and D-215 moved it out of `foundry_orchestrator`,
        # which was the only one of this file's three readers that had it.
        # It is total over `ESCALATION_STATUSES`, so the `by_status` increment
        # below needs no membership guard of its own and `count` is
        # `sum(by_status.values())` by construction.
        #
        # It is called on the RAW entry, BEFORE the normalisation below, so
        # nothing pre-empts it the way D-212's `continue` did — an entry that
        # is not a mapping carries no CLEARED and resolves to ESCALATED, and a
        # class with no `status` predates this release's fields and is reported
        # in the state it was written in. Defaulting either to CLEARED would
        # silently retire a class nobody ever cleared.
        status = escalation_status(entry)
        # Every OTHER field, read off the mapping or off nothing. The status is
        # already decided, so this cannot drop a class from the report.
        fields = entry if isinstance(entry, dict) else {}
        reason = fields.get("exit_reason")
        rows.append(
            {
                "class": name,
                "status": status,
                "exit_reason": reason if isinstance(reason, str) else None,
                "escalated_at_cycle": fields.get("escalated_at_cycle"),
                "cleared_at_cycle": fields.get("cleared_at_cycle"),
                "structural_packets_dispatched": fields.get(
                    "structural_packets_dispatched"
                ),
                "structural_packet_cycles": fields.get("structural_packet_cycles"),
                "live_clean_cycles": fields.get("live_clean_cycles"),
                "open_latent_defect_ids": fields.get("open_latent_defect_ids"),
                "defect_ids": fields.get("defect_ids"),
                "proposal": fields.get("proposal"),
            }
        )
        # No `if status in by_status` guard: the resolver is total over the
        # frozenset these keys come from, so every row lands in exactly one
        # bucket and `count == sum(by_status.values())` cannot drift (D-214).
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

    # D-193 — THE AXIS, from the one derivation `baseline_comparison` reads.
    # `index` is the counter's own highest value, so `0..index` is exactly the
    # set of counter values a decision could have been stamped with, and
    # `index + 1` is the number `derive_cycle_count` publishes as `count` on
    # every run the cap did not halt. The recorded cycles are compared against
    # THAT, never against themselves.
    recorded_cycles = {int(key) for key in per_cycle}
    derived = derive_cycle_count(run_dir)
    index = derived["index"]
    highest_recorded = max(recorded_cycles) if recorded_cycles else None
    axis_top: int | None = None
    axis_length: int | None = None
    without: list[int] | None = None
    extended = False
    if index is not None:
        # A decision above the counter-derived top is direct evidence that
        # cycle ran, so the axis widens to cover the row it is about to render
        # rather than publishing a length shorter than its own table. That is
        # not the self-fulfilling case the docstring rules out: it widens an
        # axis that already exists, and the note names the widening.
        extended = highest_recorded is not None and highest_recorded > index
        axis_top = index if highest_recorded is None else max(index, highest_recorded)
        axis_length = axis_top + 1
        without = [c for c in range(axis_length) if c not in recorded_cycles]

    return {
        "count": decisions,
        "cycle_count": len(per_cycle),
        "cycle_axis_length": axis_length,
        "cycles_without_decision": without,
        "note": _inspect_axis_note(
            axis_top=axis_top,
            axis_length=axis_length,
            recorded_cycles=len(per_cycle),
            without=without,
            grind_cycles=derived["count"],
            halted=bool(derived["halted"]),
            extended=extended,
            decisions=decisions,
        ),
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

    ``unreported`` seeds 0 rather than None because, unlike ``agents``, it is
    DERIVED here — `_read_spend` fills every bucket from the shared dispatch
    summary (D-163), so there is no run on which it is unknown. A run with no
    dispatch record at all has no unreported dispatch, and 0 is that fact.
    """
    return {"tokens": 0, "duration_ms": 0, "minutes": 0.0, "records": 0,
            "agents": None, "unreported": 0}


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
    dispatch_summary = dispatch_summary or {}
    unreported_by_phase = dispatch_summary.get("by_phase") or {}
    unreported_by_cycle = dispatch_summary.get("by_cycle") or {}

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

    # A phase or cycle the roll-up or the DISPATCH RECORD knows and the ledger
    # does not is the run where every `Foundry-Spend` call was forgotten — the
    # one whose gap most needs a line. `_overlay_unreported` creates exactly
    # those buckets, so dropping them here would hide the case the field exists
    # for: with no ledger row there is no bucket, and with no bucket the phase
    # whose every dispatch went unreported would not appear in this table at
    # all (D-163).
    #
    # D-232 / D-235 — AND THE SEEDING SOURCE IS THAT FUNCTION'S VIEW, NOT THE
    # RAW PERSISTED DOCUMENT.
    # ----------------------------------------------------------------------
    # This loop read `state.json.spend[section]` directly and seeded a bucket
    # for EVERY key in it. D-229 had already established that an all-zero
    # cycle bucket is not a measurement but a leftover — the unreported
    # summary seeds a cycle bucket, the pair then reports spend and clears
    # every cycle stamp it carried at once, and 0/0/0/0 is what is left — and
    # closed it in `_overlay_unreported`, which prunes those buckets. But that
    # prune runs on the DISPLAY's deep copy and on the persisted document only
    # when the NEXT `Foundry-Spend` call rewrites it. A run can reach DONE with
    # no further spend call, so the seeding here re-created every pruned bucket
    # from the unrepaired document. Driven at HEAD over this run's own archive:
    # `state.json.spend.by_cycle` 29 keys of which 23 all-zero,
    # `foundry_orchestrator._spend_summary` 6, this reader 29 — and REPORT.md
    # carried 23 rows reading `| cycle | 0 | 0 | 0.0 | 0 | 0 | 0 |` while
    # `Foundry-Next` showed six cycles, two surfaces stating different things
    # about one set of ledgers, and the report's version was the one sealed
    # into the run's final artifact.
    #
    # SO THE PREDICATE IS NOT RESTATED HERE. A mirrored copy of "nothing was
    # measured" is a second rule that agrees until the day one side is edited,
    # which is the whole shape of the defect. The overlay is handed a deep copy
    # of the roll-up — the same `json.loads(json.dumps(...))` spelling
    # `_spend_summary` uses, and for the same reason: it MUTATES what it is
    # given, and `state_rollup` is read again below for the disagreements
    # check — and the key sets it hands back are what this loop seeds from. One
    # derivation, two readers.
    #
    # WHAT SURVIVES IT, DELIBERATELY. A roll-up bucket that measured anything
    # (tokens, milliseconds, agents) is not pruned, so a cycle the roll-up
    # knows and the ledger does not still gets a bucket and still reaches the
    # `disagreements` list below. A cycle named only by the dispatch record
    # carries `unreported >= 1` — `unreported_dispatch_summary`'s `by_cycle`
    # values are non-empty lists by construction — so the forgotten-Foundry-
    # Spend run keeps its line. Seeding is `setdefault` over buckets the ledger
    # loop already built, so a row the LEDGER measured can never be pruned away
    # by a stale roll-up; that asymmetry is the ledger keeping its authority.
    #
    # The import is function-local. `foundry_orchestrator` imports THIS module
    # (lazily, at its own cross-casting seam) and a module-level import back
    # would close the cycle the header of this file spells out; at call time
    # every module in the chain is already built. Unguarded, so a rename fails
    # loudly at the one call site that needs the symbol rather than silently
    # restoring the zero rows.
    from foundry_mcp.tools.foundry_orchestrator import _overlay_unreported

    seed_view = _overlay_unreported(
        json.loads(json.dumps(state_rollup or {})), dispatch_summary
    )
    for section, target, unreported_keys in (
        ("by_phase", by_phase, unreported_by_phase),
        ("by_cycle", by_cycle, unreported_by_cycle),
    ):
        group = seed_view.get(section)
        keys = list(group) if isinstance(group, dict) else []
        keys += list(unreported_keys)
        for key in keys:
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
        agents = recorded.get("agents")
        bucket["agents"] = (
            agents if isinstance(agents, int) and not isinstance(agents, bool)
            else None
        )
        # D-163: `unreported` is DERIVED from the shared dispatch summary, not
        # copied from the roll-up, because the roll-up's copy of this one field
        # is structurally 0 — see the docstring. `total` is the PAIR count, so
        # it is the same integer the `unreported_dispatches` section publishes
        # as `count`, by construction and not by agreement.
        if section is None:
            bucket["unreported"] = _as_count(dispatch_summary.get("count"))
        else:
            bucket["unreported"] = len(
                (unreported_by_phase if section == "by_phase"
                 else unreported_by_cycle).get(key, ())
            )
        # `agents` is checked against the ledger's distinct names, `tokens` and
        # `duration_ms` against the ledger's sums, and `unreported` against the
        # derivation above: four fields the orchestrator and this reader both
        # produce from the same rows, so four places a stale roll-up shows. The
        # ledger's agent count is NEVER written into the bucket — publishing it
        # there is what made `agents` mean two things (D-090).
        ledger_side = {
            "tokens": bucket["tokens"],
            "duration_ms": bucket["duration_ms"],
            "agents": len(seen.get((scope, key), ())),
            "unreported": bucket["unreported"],
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

    # D-172: the pairs the cycle axis cannot carry, counted by the SAME summary
    # that built the axis. Published as data and named in the note, so the two
    # documents state one number and neither has to be parsed out of prose.
    without_cycle = dispatch_summary.get("pairs_without_cycle") or []
    return {
        "records": len(records),
        "by_phase": {k: by_phase[k] for k in sorted(by_phase)},
        "by_cycle": {k: by_cycle[k] for k in sorted(by_cycle, key=_cycle_sort_key)},
        "total": total,
        "state_rollup": state_rollup,
        "disagreements": disagreements,
        "unreported_without_cycle": len(without_cycle),
        "note": (
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
            f"{_as_count(dispatch_summary.get('count'))} unreported pairs "
            f"here, {len(without_cycle)} are in that position and are absent "
            "from every cycle row below (D-172). One stream agent unreported "
            "across three cycles is 1 in the total and 1 in each of three "
            "cycle rows. So the cycle column neither adds up to the total nor "
            "is meant to."
        ),
    }, None


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
    # The cycle each stream agent was dispatched in, for the helper's
    # `by_cycle` axis. It comes off the SAME walk as the roster because it is
    # the same fact one key up — the roll-up's cycle bucket — and walking the
    # document twice is how the two would come to disagree about which cycles
    # a stream ran in.
    #
    # D-172 — AND IT IS THE ONLY CYCLE THE ARCHIVE HAS, WHICH IS WHY THE AXIS
    # IS NARROWER THAN THE PAIR AXIS AND HAS TO SAY SO.
    # ----------------------------------------------------------------------
    # Of the two dispatch sources this function reads, only the roll-up stamps
    # a cycle: its buckets ARE keyed by the server counter. `spawns.log` rows
    # carry timestamp, casting_id, phase, wave and prompt_hash and no cycle at
    # all, so a CAST or GRIND teammate can never be attributed to one here.
    # Driven over the live archive: 19 unreported pairs, 14 of them teammates
    # (casting-1@F1, casting-1@F3, ...), and `by_cycle` named zero teammates in
    # any of cycles 0-9 — while the spend section's note beside that column
    # claimed it "names the cycles those agents were dispatched in".
    #
    # The fix is NOT a stamp invented here. Correlating a spawn timestamp
    # against `phase_history`'s cycle windows would be a second proxy, and "the
    # cycle axis is derived from a proxy" is the filing. The axis keeps the one
    # real source, `unreported_dispatch_summary` returns the pairs that source
    # cannot cover as `pairs_without_cycle`, and the prose states that scope
    # instead of a coverage it does not have. A per-dispatch `cycle` in the
    # spawn record is what would widen it, and that field belongs to the spawn
    # writer, not to a reader of its ledger.
    cycles_of_agent: dict[str, list[str]] = {}
    cycles = rollup.get("cycles")
    if isinstance(cycles, dict):
        for cycle_key, bucket in cycles.items():
            if not isinstance(bucket, dict):
                continue
            for stream in bucket:
                # The C-6 additions (`inspect_mode`, `stream_scope`,
                # `evidence_sweep`, `temper_entry`, ...) live in the same bucket
                # as the stream records, so a key whose value is not a stream
                # record is not a stream. Testing the VALUE rather than keeping
                # a denylist of non-stream keys is what stops this from needing
                # an edit every time the roll-up gains a field.
                #
                # D-182 — AND THE TEST ITSELF IS NOW READ, NOT RE-TYPED. This
                # spelled the rule inline, `foundry_orchestrator`
                # `_stream_dispatch_cycles` spelled it inline too, and
                # `measure-run.py` `_read_stream_rollup` — the third walker of
                # this same bucket — never learned it at all and reported this
                # run's own roll-up as eight failure tokens and exit 1. Two
                # hand-typed copies of one rule are how a third comes to be
                # missing, so the rule lives once in `foundry_state`, beside
                # `unreported_dispatch_summary`, for D-047's reason.
                entry = bucket.get(stream)
                if is_stream_record(entry):
                    stream_roster.setdefault("F2", []).append(str(stream))
                    cycles_of_agent.setdefault(str(stream), []).append(
                        str(cycle_key)
                    )

    return unreported_dispatch_summary(
        dispatch_rows=spawns,
        stream_roster=stream_roster,
        spend_rows=spend,
        phase_of_dispatch=getattr(
            foundry_orchestrator, "DISPATCH_PHASE_TO_RUN_PHASE", {}
        ),
        agent_id_of=_agent_id_for_casting,
        cycles_of_agent=cycles_of_agent,
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
        # ROLL-UP's number or it is nothing — `_new_spend_bucket` seeds it None
        # and `_read_spend` fills it only from `state.json.spend`, on the
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
