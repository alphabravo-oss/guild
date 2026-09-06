"""fallout GI-024 / FR-008 / AC-011 / OT-010 — the consolidated run-table readers.

A-026: "All of the survey's duplication inventory, into `foundry_state`
readers (Recommended)."

fallout OT-010 states the property this module pins: "Spend, inspect modes,
unreported
dispatches, escalated classes and md-section splitting each have exactly one
implementation, in `foundry_state`."

`survey/architecture.md` §3.2 is the inventory those two sentences empty, and
§3.3 is the count they answer — "Three renderings, two derivations." Every row
of both ends in `foundry_mcp/tools/foundry_state.py`, and every row of both is
driven here.

THREE DRIVES PER READER, BECAUSE THE CONTRACT HAS THREE CLAUSES
---------------------------------------------------------------
Each reader promises to be TOTAL — an empty derived table rather than a raise
— and to document its return shape. So each one is driven three ways:

  1. the DERIVED TABLE, against a synthetic run directory that carries the
     ledger shapes a real archive carries;
  2. the EMPTY-LEDGER answer, against a run directory that carries nothing.
     "Nobody recorded any spend" is a measurement and must not read as a fault;
  3. the MALFORMED-DOCUMENT answer, against a ledger that will not parse or
     parses to the wrong type. This is the clause the module comment (D-137)
     was written for: no reader here may raise across the MCP boundary, and a
     reader that raised on a torn `state.json` would take `Foundry-Next` — the
     mandatory handshake before every transition — down with it.

The cross-surface equality assertions (the report and the status display
reading the SAME number from the SAME reader) live at the bottom of this
module, beside the sections that consume them.

Shape follows `tests/test_stream_rollup.py`, the per-concern analog: a module
docstring quoting the requirement, the module's OWN `run_env` fixture rather
than a shared `conftest.py` one, then direct calls against a `tmp_path` run
directory.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from foundry_mcp.schemas import vocab
from foundry_mcp.schemas.vocab import (
    DEFECT_TIERS,
    ESCALATION_EXIT_REASONS,
    ESCALATION_STATUSES,
    INSPECT_MODES,
    TIER_UNKNOWN,
    defect_tier,
    escalation_status,
)
from foundry_mcp.tools import foundry_state as fs


@pytest.fixture
def run_env(tmp_path):
    """An empty run directory under tmp_path; yields the run dir.

    Deliberately EMPTY. Every test below writes exactly the ledgers its own
    clause needs, so a test that passes because some other test's fixture data
    happened to be there is not representable — which matters most for the
    empty-ledger clause, whose whole subject is what a reader does with nothing.
    """
    run_dir = tmp_path / "foundry-archive" / "readers-run"
    run_dir.mkdir(parents=True)
    return run_dir


def _write_json(run_dir: Path, name: str, data: object) -> None:
    (run_dir / name).write_text(json.dumps(data, indent=2), encoding="utf-8")


def _write_lines(run_dir: Path, name: str, rows: list[dict]) -> None:
    (run_dir / name).write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
    )


def _torn(run_dir: Path, name: str) -> None:
    """A document that is present and will not parse. Not an absent one."""
    (run_dir / name).write_text('{"cycles": {"1": ', encoding="utf-8")


# ---------------------------------------------------------------------------
# The scalar rules: `now_iso`, `as_count`, `cycle_sort_key`, `current_cycle`.
# ---------------------------------------------------------------------------


def test_now_iso_keeps_both_precisions_the_two_copies_published() -> None:
    """The reconciliation is a NAMED argument, not a silent pick.

    `foundry_orchestrator._now` published full precision and
    `foundry_report._now` published `timespec="seconds"`, and both answers are
    load-bearing: the ledger stamps this function writes are what
    `handoffs_wall_clock_seconds` measures a SPAN between, so seconds would
    make two records appended inside one second span 0.0 — the fabrication that
    function's docstring refuses by name.
    """
    seconds = fs.now_iso(timespec="seconds")
    auto = fs.now_iso()

    assert seconds.endswith("+00:00"), seconds
    assert auto.endswith("+00:00"), auto
    # The seconds spelling carries no sub-second field at all; the default one
    # is free to (and on any real clock does).
    assert "." not in seconds.split("+")[0], seconds
    assert len(seconds) == len("2026-09-05T04:11:07+00:00"), seconds


def test_as_count_refuses_a_bool_and_a_negative() -> None:
    """`True` is not a count of anything, and neither is -3."""
    assert fs.as_count(7) == 7
    assert fs.as_count(0) == 0
    assert fs.as_count(True) == 0
    assert fs.as_count(False) == 0
    assert fs.as_count(-3) == 0
    assert fs.as_count("7") == 0
    assert fs.as_count(None) == 0
    assert fs.as_count(2.5) == 0


def test_cycle_sort_key_orders_the_counter_numerically() -> None:
    """D-220 — `sorted()` on the stringified key put cycle 21 before cycle 3."""
    keys = ["0", "1", "10", "11", "2", "20", "3", "F1", "F5.5"]
    assert sorted(keys, key=fs.cycle_sort_key) == [
        "0", "1", "2", "3", "10", "11", "20", "F1", "F5.5",
    ]


def test_current_cycle_reads_the_counter(run_env) -> None:
    """The derived table: the server-owned counter, as written."""
    _write_json(run_env, "state.json", {"cycle": 4})
    assert fs.current_cycle(run_env) == 4


def test_current_cycle_answers_zero_on_an_empty_run(run_env) -> None:
    """The empty-ledger clause: no state.json at all is cycle 0, not a raise."""
    assert fs.current_cycle(run_env) == 0


@pytest.mark.parametrize(
    "value", [None, "3", -3, 2.5, True, [], {}], ids=repr
)
def test_current_cycle_is_total_over_a_malformed_counter(run_env, value) -> None:
    """D-059 — a raw read handed on whatever the file held.

    A str/None/list/dict raised an unhandled TypeError out of Foundry-Next, the
    mandatory handshake before every phase transition and gate, while -3 and
    2.5 propagated silently into responses and onto every row of a synthesized
    verdict.
    """
    _write_json(run_env, "state.json", {"cycle": value})
    assert fs.current_cycle(run_env) == 0


def test_current_cycle_is_total_over_a_torn_state_document(run_env) -> None:
    """The malformed-document clause. A torn state.json is cycle 0, never a raise."""
    _torn(run_env, "state.json")
    assert fs.current_cycle(run_env) == 0


# ---------------------------------------------------------------------------
# The spend bucket and the spend roll-up (survey/architecture.md §3.2, rows 1-2).
# ---------------------------------------------------------------------------


def test_spend_bucket_names_its_two_variants() -> None:
    """The persisted seed and the derived seed differ in exactly two fields.

    `records` and `minutes` are derived-only, and `agents` seeds 0 on the
    persisted bucket (the door counted them) and None on the derived one
    ("nobody recorded how many agents" and "no agents ran" are different facts
    — D-090).
    """
    persisted = fs.spend_bucket(persisted=True)
    derived = fs.spend_bucket()

    assert set(persisted) == {"tokens", "duration_ms", "agents", "unreported"}
    assert set(derived) == {
        "tokens", "duration_ms", "minutes", "records", "agents", "unreported",
    }
    assert persisted["agents"] == 0
    assert derived["agents"] is None
    assert persisted["unreported"] == 0 and derived["unreported"] == 0


def test_spend_rollup_aggregates_the_ledger(run_env) -> None:
    """The derived table: tokens and milliseconds are the LEDGER's sums."""
    rows = [
        {"agent": "casting-1", "phase": "F1", "cycle": 0,
         "tokens": 100, "duration_ms": 60_000},
        {"agent": "casting-2", "phase": "F1", "cycle": 0,
         "tokens": 50, "duration_ms": 30_000},
        {"agent": "casting-1", "phase": "F3", "cycle": 1,
         "tokens": 25, "duration_ms": 30_000},
    ]
    table = fs.spend_rollup(spend_rows=rows, state_rollup=None)

    assert table["records"] == 3
    assert table["total"]["tokens"] == 175
    assert table["total"]["duration_ms"] == 120_000
    assert table["total"]["minutes"] == 2.0
    assert table["by_phase"]["F1"]["tokens"] == 150
    assert table["by_phase"]["F1"]["records"] == 2
    assert table["by_phase"]["F3"]["tokens"] == 25
    assert table["by_cycle"]["0"]["tokens"] == 150
    assert table["by_cycle"]["1"]["tokens"] == 25
    # `agents` is the ROLL-UP's number or it is nothing (D-090). This run has no
    # roll-up, so it is None everywhere — never the ledger's row count.
    assert table["total"]["agents"] is None
    assert table["state_rollup"] is None
    assert table["disagreements"] == []


def test_spend_rollup_names_a_disagreement_rather_than_publishing_one_side(
    run_env,
) -> None:
    """D-038 / D-090 — where the ledger and the roll-up both answer, both are read.

    `agents` is checked against the ledger's DISTINCT NAMES and never against
    its row count, because publishing the row count under that name is exactly
    what D-090 filed.
    """
    rows = [
        {"agent": "casting-1", "phase": "F1", "tokens": 100, "duration_ms": 0},
        {"agent": "casting-1", "phase": "F1", "tokens": 100, "duration_ms": 0},
    ]
    table = fs.spend_rollup(
        spend_rows=rows,
        state_rollup={"by_phase": {"F1": {"tokens": 999, "agents": 1}}},
    )

    assert table["by_phase"]["F1"]["tokens"] == 200, "the ledger is the authority"
    assert table["by_phase"]["F1"]["records"] == 2, "rows, not agents"
    assert table["by_phase"]["F1"]["agents"] == 1, "the roll-up is the authority"
    assert {
        (d["scope"], d["key"], d["field"], d["ledger"], d["state_rollup"])
        for d in table["disagreements"]
    } == {("by_phase", "F1", "tokens", 200, 999)}


def test_spend_rollup_derives_unreported_and_never_copies_it(run_env) -> None:
    """D-163 — the roll-up's copy of this ONE field is structurally 0.

    Its only writer seeds it and never increments it, and the overlay that
    would fill it runs on a throwaway copy. One report published
    `total.unreported: 0` beside its own `unreported_dispatches {"count": 1}`.
    """
    summary = {
        "count": 2,
        "by_phase": {"F1": ["casting-2"], "F3": ["casting-2"]},
        "by_cycle": {},
        "pairs_without_cycle": [{"agent": "casting-2", "phase": "F1"}],
    }
    table = fs.spend_rollup(
        spend_rows=[{"agent": "casting-1", "phase": "F1", "tokens": 1}],
        state_rollup={"total": {"unreported": 0}},
        dispatch_summary=summary,
    )

    assert table["total"]["unreported"] == 2
    assert table["by_phase"]["F1"]["unreported"] == 1
    # The phase the dispatch record knows and the ledger does not still gets a
    # row: with no bucket, the phase whose every dispatch went unreported would
    # not appear in this table at all.
    assert table["by_phase"]["F3"]["unreported"] == 1
    assert table["by_phase"]["F3"]["tokens"] == 0
    assert table["unreported_without_cycle"] == 1


def test_spend_rollup_answers_an_empty_ledger_with_an_empty_table() -> None:
    """The empty-ledger clause. Nobody spent anything: that is a measurement."""
    table = fs.spend_rollup(spend_rows=[], state_rollup=None)

    assert table["records"] == 0
    assert table["by_phase"] == {}
    assert table["by_cycle"] == {}
    assert table["total"]["tokens"] == 0
    assert table["total"]["agents"] is None
    assert table["disagreements"] == []


@pytest.mark.parametrize("rollup", [None, [], "a string", 42, True], ids=repr)
def test_spend_rollup_is_total_over_a_malformed_state_rollup(rollup) -> None:
    """The malformed-document clause: a wrong-TYPED roll-up reads as absent."""
    table = fs.spend_rollup(
        spend_rows=[{"agent": "a", "phase": "F1", "tokens": 5, "duration_ms": 0}],
        state_rollup=rollup,
    )
    assert table["total"]["tokens"] == 5
    assert table["state_rollup"] is None
    assert table["disagreements"] == []


def test_spend_rollup_skips_a_row_that_is_not_a_mapping() -> None:
    """A bare string on line 40 is debris, not a spend record."""
    table = fs.spend_rollup(
        spend_rows=[{"phase": "F1", "tokens": 5}, "junk", None, 42],  # type: ignore[list-item]
        state_rollup=None,
    )
    assert table["records"] == 1
    assert table["total"]["tokens"] == 5


def test_overlay_unreported_prunes_an_all_zero_cycle_bucket() -> None:
    """D-229 — a bucket with nothing in it is not a measurement, it is a claim.

    Driven on the live run, `Foundry-Next` returned 23 all-zero `by_cycle`
    buckets and the report wrote 23 rows reading `| cycle | 0 | 0 | 0.0 | 0 |`
    while stream-rollup.json records the full roster running. An ABSENT row is
    honest where a zero row is a claim.
    """
    spend = {
        "by_phase": {"F1": {"tokens": 0, "duration_ms": 0, "agents": 0,
                            "unreported": 0}},
        "by_cycle": {
            "0": {"tokens": 0, "duration_ms": 0, "agents": 0, "unreported": 0},
            "1": {"tokens": 10, "duration_ms": 0, "agents": 1, "unreported": 0},
        },
        "total": {"tokens": 10, "duration_ms": 0, "agents": 1, "unreported": 0},
    }
    out = fs.overlay_unreported(spend, {"count": 0, "by_phase": {}, "by_cycle": {}})

    assert sorted(out["by_cycle"]) == ["1"], "the all-zero cycle row is pruned"
    assert "F1" in out["by_phase"], "the phase axis is never pruned"


def test_overlay_unreported_is_total_over_a_malformed_document() -> None:
    """The malformed-document clause: wrong-typed sections are replaced, not raised on."""
    out = fs.overlay_unreported(
        {"by_phase": "nope", "by_cycle": 42, "total": None},
        {"count": 1, "by_phase": {"F1": ["casting-1"]}, "by_cycle": {}},
    )
    assert out["by_phase"]["F1"]["unreported"] == 1
    assert out["total"]["unreported"] == 1
    assert out["by_cycle"] == {}


# ---------------------------------------------------------------------------
# The inspect-mode census (survey/architecture.md §3.3, row 2).
# ---------------------------------------------------------------------------


def _mode(cycle: int, phase: str, mode: str, rule: str) -> dict:
    return {"cycle": cycle, "phase": phase, "mode": mode, "rule": rule,
            "decided_by": f"{phase}-entry", "required_streams": ["trace"]}


def test_inspect_mode_rows_keeps_one_row_per_decision() -> None:
    """D-119 — a census that keeps one of two answers is not a census.

    The collapse was the F2-to-F5 path: the counter does not advance entering
    F5, so TEMPER's entry is stamped with the cycle the preceding F2 INSPECT
    already used. On the four real decisions the filing names, the truth is
    FULL 2 and DELTA 2 over three cycles.
    """
    state = {"inspect_modes": [
        _mode(1, "F2", "FULL", "first_of_phase"),
        _mode(2, "F2", "DELTA", "delta"),
        _mode(3, "F2", "DELTA", "delta"),
        _mode(3, "F5", "FULL", "first_of_phase"),
    ]}
    table = fs.inspect_mode_rows(
        state=state, derived={"index": 3}, modes=INSPECT_MODES
    )

    assert table["count"] == 4, "decisions"
    assert table["cycle_count"] == 3, "cycles that carry one"
    assert table["by_mode"] == {"DELTA": 2, "FULL": 2}
    assert [d["mode"] for d in table["per_cycle"]["3"]] == ["DELTA", "FULL"]
    assert fs.inspect_decisions(table) == [
        d for group in table["per_cycle"].values() for d in group
    ]


def test_inspect_mode_rows_takes_its_axis_from_the_counter() -> None:
    """D-193 — the section counted its own rows and called that the run's cycles.

    `cycle_count` counts the cycles this LEDGER has a decision for, and the
    document published it as the width of an axis over the run's CYCLES. Cycles
    0-9 carrying no recorded decision is a fact the axis has to be able to show.
    """
    state = {"inspect_modes": [_mode(10, "F2", "FULL", "first_of_phase")]}
    table = fs.inspect_mode_rows(
        state=state, derived={"index": 14}, modes=INSPECT_MODES
    )

    assert table["cycle_axis_length"] == 15
    assert table["cycles_without_decision"] == [0, 1, 2, 3, 4, 5, 6, 7, 8, 9,
                                                11, 12, 13, 14]
    assert table["axis_top"] == 14
    assert table["axis_extended"] is False


def test_inspect_mode_rows_widens_the_axis_to_cover_its_own_table() -> None:
    """A decision above the counter-derived top is direct evidence that cycle ran.

    The axis widens rather than publishing a length shorter than its own table,
    and `axis_extended` is what lets the caller NAME the widening instead of
    hiding it.
    """
    state = {"inspect_modes": [_mode(7, "F2", "FULL", "first_of_phase")]}
    table = fs.inspect_mode_rows(
        state=state, derived={"index": 3}, modes=INSPECT_MODES
    )

    assert table["axis_top"] == 7
    assert table["cycle_axis_length"] == 8
    assert table["axis_extended"] is True


def test_inspect_mode_rows_answers_an_empty_ledger() -> None:
    """The empty-ledger clause: no decisions, and every mode present at zero."""
    table = fs.inspect_mode_rows(state={}, derived={"index": None},
                                 modes=INSPECT_MODES)

    assert table["count"] == 0
    assert table["cycle_count"] == 0
    assert table["per_cycle"] == {}
    assert table["by_mode"] == dict.fromkeys(sorted(INSPECT_MODES), 0)
    # An underivable counter is None, never a fallback onto the highest cycle
    # THIS ledger names — an axis taken from the ledger rendered against it is
    # complete by construction, which is fabrication wearing a different hat.
    assert table["cycle_axis_length"] is None
    assert table["cycles_without_decision"] is None


@pytest.mark.parametrize(
    "entries", ["a string", 42, None, {"cycle": 1}, [None, 7, "x"]], ids=repr
)
def test_inspect_mode_rows_is_total_over_a_malformed_ledger(entries) -> None:
    """The malformed-document clause: a wrong-typed `inspect_modes` is empty."""
    table = fs.inspect_mode_rows(
        state={"inspect_modes": entries}, derived={"index": 1},
        modes=INSPECT_MODES,
    )
    assert table["count"] == 0
    assert table["per_cycle"] == {}


def test_inspect_mode_rows_skips_an_entry_with_no_usable_cycle() -> None:
    """A bool is not a cycle, and an entry without one cannot land on the axis."""
    state = {"inspect_modes": [
        _mode(1, "F2", "FULL", "first_of_phase"),
        {"cycle": True, "mode": "FULL"},
        {"cycle": "2", "mode": "DELTA"},
        {"mode": "DELTA"},
    ]}
    table = fs.inspect_mode_rows(
        state=state, derived={"index": 1}, modes=INSPECT_MODES
    )
    assert table["count"] == 1
    assert sorted(table["per_cycle"]) == ["1"]
    assert len(table["entries"]) == 4, "the raw list is carried whole"


def test_inspect_decisions_is_total_over_a_malformed_section() -> None:
    """The flattening lives beside the census, so nothing re-decides it."""
    assert fs.inspect_decisions({}) == []
    assert fs.inspect_decisions({"per_cycle": None}) == []
    assert fs.inspect_decisions("nope") == []  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# The stream-coverage rows (fallout CT-003 / AC-030 / OT-028).
# ---------------------------------------------------------------------------


def _tranche(checked: int, total: int, findings: int, records: int) -> dict:
    return {
        "items_checked": checked,
        "items_total": total,
        "findings": findings,
        "records": [
            {"recorded_at": f"2026-09-0{i + 1}T00:00:00+00:00",
             "items_checked": checked, "items_total": total,
             "findings": findings, "declared_cycle": 1}
            for i in range(records)
        ],
    }


def test_stream_rollup_rows_names_what_was_replaced(run_env) -> None:
    """fallout CT-003 / ST-009 — a second record for one (stream, cycle) REPLACES it.

    The first record replaced nothing and every later one replaced exactly the
    record before it, so `replaced_count` is `len(records) - 1` and a bucket
    recorded once reads as coverage rather than as a replacement.
    """
    _write_json(run_env, "stream-rollup.json", {"cycles": {
        "1": {
            "trace": _tranche(48, 48, 3, 1),
            "prove": _tranche(71, 71, 0, 3),
            # The C-6 cycle-level facts sitting in the same mapping. D-182: the
            # test is on the VALUE, never a denylist of key names.
            "inspect_mode": "FULL",
            "stream_scope": {"trace": {"scope": "delta"}},
            "temper_entry": {"opened": True},
        },
    }})
    table = fs.stream_rollup_rows(run_env)

    assert sorted(table["cycles"]["1"]) == ["prove", "trace"]
    assert table["cycles"]["1"]["prove"]["replaced_count"] == 2
    assert table["cycles"]["1"]["trace"]["replaced_count"] == 0
    assert table["replaced"] == [
        {"cycle": "1", "stream": "prove", "replaced_count": 2}
    ]
    assert table["cycle_count"] == 1
    assert table["stream_count"] == 2
    assert table["problem"] is None


def test_stream_rollup_rows_renders_an_over_total_bucket_and_names_it(
    run_env,
) -> None:
    """fallout FR-054 — a bucket that reads above 100% is what daring-orca has.

    Render what is there and name it; do not silently normalise. A coverage
    figure quietly clamped to its total is a measurement replaced by an
    assertion, and the whole point of the replace semantics is that the run can
    see which records were superseded.
    """
    _write_json(run_env, "stream-rollup.json", {"cycles": {
        "3": {"prove": {"items_checked": 213, "items_total": 71,
                        "findings": 0, "records": []}},
    }})
    table = fs.stream_rollup_rows(run_env)

    row = table["cycles"]["3"]["prove"]
    assert row["items_checked"] == 213 and row["items_total"] == 71
    assert row["over_total"] is True
    assert table["over_total"] == [
        {"cycle": "3", "stream": "prove", "items_checked": 213,
         "items_total": 71}
    ]
    # No `records` list at all is the ADDITIVE writer's shape, and it stays
    # distinguishable from "recorded once".
    assert row["record_count"] == 0
    assert table["buckets_without_records"] == [{"cycle": "3", "stream": "prove"}]


def test_stream_rollup_rows_answers_an_empty_run(run_env) -> None:
    """The empty-ledger clause: no roll-up at all is an empty table, not a fault."""
    table = fs.stream_rollup_rows(run_env)

    assert table["cycles"] == {}
    assert table["cycle_count"] == 0
    assert table["stream_count"] == 0
    assert table["replaced"] == []
    assert table["over_total"] == []
    assert table["problem"] is None, "an ABSENT ledger is not a problem"


def test_stream_rollup_rows_reports_a_torn_document_as_a_problem(run_env) -> None:
    """The malformed-document clause: named, and still an empty table."""
    _torn(run_env, "stream-rollup.json")
    table = fs.stream_rollup_rows(run_env)

    assert table["cycles"] == {}
    assert table["problem"] is not None
    assert "stream-rollup.json" in table["problem"]


@pytest.mark.parametrize("cycles", ["nope", 42, [], None], ids=repr)
def test_stream_rollup_rows_is_total_over_a_wrong_typed_cycles_key(
    run_env, cycles
) -> None:
    """A well-formed document of the wrong SHAPE is empty, never a raise."""
    _write_json(run_env, "stream-rollup.json", {"cycles": cycles})
    assert fs.stream_rollup_rows(run_env)["cycles"] == {}


def test_stream_rollup_rows_orders_cycles_by_the_counter(run_env) -> None:
    """The same key function `cycle_sort_key` gives every other cycle axis."""
    _write_json(run_env, "stream-rollup.json", {"cycles": {
        str(c): {"trace": _tranche(1, 1, 0, 1)} for c in (10, 2, 1, 21, 3)
    }})
    assert list(fs.stream_rollup_rows(run_env)["cycles"]) == [
        "1", "2", "3", "10", "21",
    ]


# ---------------------------------------------------------------------------
# The unreported-dispatch input assembly (D-047 / D-048 / D-163 / D-172).
# ---------------------------------------------------------------------------


def test_unreported_dispatch_inputs_builds_the_roster_and_the_cycle_map(
    run_env,
) -> None:
    """The derived table: the roster and the cycle map come off ONE walk.

    They are the same fact one key up — the roll-up's cycle bucket — and
    walking the document twice is how the two would come to disagree about
    which cycles a stream ran in.
    """
    _write_lines(run_env, "spawns.log", [
        {"casting_id": 1, "phase": "cast", "timestamp": "t"},
        {"casting_id": 1, "phase": "grind", "timestamp": "t"},
    ])
    _write_lines(run_env, "spend.jsonl", [
        {"agent": "casting-1", "phase": "F1", "tokens": 1},
    ])
    _write_json(run_env, "stream-rollup.json", {"cycles": {
        "0": {"trace": _tranche(1, 1, 0, 1), "inspect_mode": "FULL"},
        "1": {"trace": _tranche(1, 1, 0, 1), "prove": _tranche(1, 1, 0, 1)},
    }})

    inputs = fs.unreported_dispatch_inputs(run_env)

    assert inputs["problem"] is None
    assert len(inputs["dispatch_rows"]) == 2
    assert len(inputs["spend_rows"]) == 1
    assert sorted(inputs["stream_roster"]) == ["F2"]
    assert sorted(inputs["stream_roster"]["F2"]) == ["prove", "trace", "trace"]
    assert inputs["cycles_of_agent"] == {"trace": ["0", "1"], "prove": ["1"]}
    # The C-6 cycle-level key is not a stream (D-182).
    assert "inspect_mode" not in inputs["cycles_of_agent"]


def test_unreported_dispatch_inputs_feed_the_rule_that_was_already_shared(
    run_env,
) -> None:
    """The assembly moved; the RULE did not, and this is the join.

    `unreported_dispatch_summary` has been the one rule since D-047/D-048.
    Hosting its inputs beside it is what makes "one derivation, two renderings"
    true of the input as well as the output.
    """
    _write_lines(run_env, "spawns.log", [
        {"casting_id": 2, "phase": "cast", "timestamp": "t"},
    ])
    _write_lines(run_env, "spend.jsonl", [])
    _write_json(run_env, "stream-rollup.json", {"cycles": {}})

    inputs = fs.unreported_dispatch_inputs(run_env)
    summary = fs.unreported_dispatch_summary(
        dispatch_rows=inputs["dispatch_rows"],
        stream_roster=inputs["stream_roster"],
        spend_rows=inputs["spend_rows"],
        phase_of_dispatch=fs.DISPATCH_PHASE_TO_RUN_PHASE,
        agent_id_of=lambda cid: f"casting-{cid}",
        cycles_of_agent=inputs["cycles_of_agent"],
    )

    # D-048: the dispatch VERB `cast` is reconciled onto the run phase `F1`, so
    # `by_phase` never grows a bucket keyed by a verb that is not a phase.
    assert summary["count"] == 1
    assert summary["by_phase"] == {"F1": ["casting-2"]}
    assert summary["pairs_without_cycle"] == [{"agent": "casting-2",
                                               "phase": "F1"}]


def test_unreported_dispatch_inputs_answer_an_empty_run(run_env) -> None:
    """The empty-ledger clause: three absent ledgers are not three problems."""
    inputs = fs.unreported_dispatch_inputs(run_env)

    assert inputs == {
        "dispatch_rows": [], "spend_rows": [], "stream_roster": {},
        "cycles_of_agent": {}, "problem": None,
    }


@pytest.mark.parametrize(
    "name", ["spawns.log", "spend.jsonl", "stream-rollup.json"]
)
def test_unreported_dispatch_inputs_name_the_ledger_that_would_not_read(
    run_env, name
) -> None:
    """The malformed-document clause, per ledger.

    The caller refuses with the ledger NAMED, so a lead hitting this at F6
    knows which of three files to repair. A ledger whose BYTES will not decode
    is a problem; a torn LINE inside a readable one is skipped (`read_jsonl`'s
    documented asymmetry).
    """
    (run_env / name).write_bytes(b"\xff\xfe not utf-8 at all")
    inputs = fs.unreported_dispatch_inputs(run_env)

    assert inputs["problem"] is not None
    assert name in inputs["problem"]
    assert inputs["dispatch_rows"] == [] and inputs["spend_rows"] == []


def test_unreported_dispatch_inputs_skip_a_torn_ledger_line(run_env) -> None:
    """A torn final line is an ordinary crash artifact under an flock."""
    (run_env / "spawns.log").write_text(
        json.dumps({"casting_id": 1, "phase": "cast"}) + "\n{\"casting_id\": ",
        encoding="utf-8",
    )
    inputs = fs.unreported_dispatch_inputs(run_env)

    assert inputs["problem"] is None
    assert len(inputs["dispatch_rows"]) == 1


# ---------------------------------------------------------------------------
# The escalated-class rows (D-214 / D-215).
# ---------------------------------------------------------------------------


def _escalated_rows(document: dict) -> dict:
    return fs.escalated_class_rows(
        document,
        statuses=ESCALATION_STATUSES,
        exit_reasons=ESCALATION_EXIT_REASONS,
        status_of=escalation_status,
    )


def test_escalated_class_rows_resolve_status_through_the_vocabulary() -> None:
    """D-214 — the vocabulary decides, and it decides on the RAW entry.

    The read this replaces was a shape test wearing the vocabulary's default,
    so `{"status": "BOGUS"}` passed through verbatim: `count` 3 while
    `by_status` summed to 2, and a status no writer in this system emits
    published in a terminal artifact.
    """
    table = _escalated_rows({"classes": {
        "clean-class": {"status": "CLEARED", "exit_reason": "clean_cycles"},
        "bogus-class": {"status": "BOGUS"},
        "live-class": {"status": "ESCALATED"},
    }})

    assert table["count"] == 3
    assert sum(table["by_status"].values()) == 3, "count == sum, by construction"
    assert table["by_status"] == {"CLEARED": 1, "ESCALATED": 2}
    assert {r["class"]: r["status"] for r in table["classes"]} == {
        "clean-class": "CLEARED", "bogus-class": "ESCALATED",
        "live-class": "ESCALATED",
    }
    assert table["by_exit_reason"] == {"budget": 0, "clean_cycles": 1}


def test_escalated_class_rows_never_drop_a_non_mapping_entry() -> None:
    """D-212 — nothing pre-empts the resolver the way a `continue` did.

    An entry that is not a mapping carries no CLEARED and resolves to
    ESCALATED; defaulting it to CLEARED would silently retire a class nobody
    ever cleared.
    """
    table = _escalated_rows({"classes": {"a": "nope", "b": None, "c": 42}})

    assert table["count"] == 3
    assert table["by_status"]["ESCALATED"] == 3
    assert all(r["exit_reason"] is None for r in table["classes"])


def test_escalated_class_rows_answer_an_empty_document() -> None:
    """The empty-ledger clause: every status key present, including zeros."""
    table = _escalated_rows({})

    assert table["count"] == 0
    assert table["classes"] == []
    assert table["by_status"] == dict.fromkeys(sorted(ESCALATION_STATUSES), 0)
    assert table["by_exit_reason"] == dict.fromkeys(
        sorted(ESCALATION_EXIT_REASONS), 0
    )


@pytest.mark.parametrize(
    "document", [{"classes": "nope"}, {"classes": 42}, {"classes": []},
                 {}, "a string", 42, None],
    ids=repr,
)
def test_escalated_class_rows_are_total_over_a_malformed_document(document) -> None:
    """The malformed-document clause: a wrong-typed document is an empty table."""
    assert _escalated_rows(document)["count"] == 0


# ---------------------------------------------------------------------------
# The markdown section split (Holmes `share-10`).
# ---------------------------------------------------------------------------


_REPORT_FIXTURE = """# Foundry run report — a-run

Generated 2026-09-05 by Foundry-Report. Every section below is generated.

## Verdict matrix

3 requirements.

## LATENT backlog

_None recorded._

## Lead notes (carried by the seal)

my own prose
"""


def test_markdown_sections_and_headings_are_one_rule() -> None:
    """Holmes `share-10` — "same effective rule, coded independently".

    The seal split REPORT.md on a line that STARTS a block and the DONE gate's
    presence check built a set of every trimmed line. Both halves of
    convergence GI-006 now
    read the document by ONE rule, so the seal can never preserve something the
    gate would then call a missing section.
    """
    header, blocks = fs.markdown_sections(_REPORT_FIXTURE)

    assert header[0].startswith("# Foundry run report")
    assert [heading for heading, _ in blocks] == [
        "## Verdict matrix", "## LATENT backlog",
        "## Lead notes (carried by the seal)",
    ]
    assert blocks[0][1] == ["", "3 requirements.", ""]
    assert fs.markdown_headings(_REPORT_FIXTURE) == {
        heading for heading, _ in blocks
    }


def test_markdown_sections_match_a_whole_trimmed_line() -> None:
    """A prefix match would count `## Verdict matrix (see below)` as the section.

    convergence GI-006 licenses the lead to APPEND, so the match is on the whole
    trimmed
    line: a lead's own `## Appendix` never counts as a generated section, and a
    generated heading with a suffix bolted on reads as the edit it is.
    """
    headings = fs.markdown_headings(
        "## Verdict matrix (see below)\n   ## LATENT backlog   \n###  Deeper\n"
    )
    assert headings == {"## Verdict matrix (see below)", "## LATENT backlog"}
    assert "## Verdict matrix" not in headings


def test_markdown_sections_answer_an_empty_document() -> None:
    """The empty-ledger clause: no document is no header and no blocks."""
    assert fs.markdown_sections("") == ([], [])
    assert fs.markdown_headings("") == set()


def test_markdown_sections_keep_prose_that_precedes_the_first_heading() -> None:
    """The header block is what the seal carries verbatim; it cannot be lost."""
    header, blocks = fs.markdown_sections("lead prose\n\n## A\nbody\n")
    assert header == ["lead prose", ""]
    assert blocks == [("## A", ["body"])]


# ---------------------------------------------------------------------------
# `prove_is_clean` (process-fixes FR-020 / AC-025).
# ---------------------------------------------------------------------------


def test_prove_is_clean_on_a_full_clean_tranche_set() -> None:
    """The derived answer: 0 findings AND >=95% requirement coverage."""
    assert fs.prove_is_clean(
        totals={"items_checked": 68, "items_total": 71, "findings": 0},
        spec_requirement_count=71,
    ) is True


def test_prove_is_clean_refuses_a_spec_that_parses_to_zero_requirements() -> None:
    """process-fixes FR-020 / AC-025 — the >=95% check used to be SKIPPED at zero.

    So any `.prove-complete` with `findings=0` on an unresolvable or
    unparseable spec drove the F4 auto-VERIFY path, manufacturing a passing run
    out of a spec nothing had actually been proved against.
    """
    assert fs.prove_is_clean(
        totals={"items_checked": 0, "items_total": 0, "findings": 0},
        spec_requirement_count=0,
    ) is False


def test_prove_is_clean_refuses_findings_and_thin_coverage() -> None:
    """Either clause alone is enough to say no."""
    assert fs.prove_is_clean(
        totals={"items_checked": 71, "items_total": 71, "findings": 1},
        spec_requirement_count=71,
    ) is False
    assert fs.prove_is_clean(
        totals={"items_checked": 40, "items_total": 71, "findings": 0},
        spec_requirement_count=71,
    ) is False


@pytest.mark.parametrize(
    "totals",
    [None, {}, {"items_checked": 71}, {"findings": None, "items_checked": 71},
     "a string", 42],
    ids=repr,
)
def test_prove_is_clean_is_total_over_a_malformed_tranche(totals) -> None:
    """The malformed-document clause. `findings` absent is not `findings` zero:
    a tranche that recorded no finding count has not been shown to be clean."""
    assert fs.prove_is_clean(
        totals=totals, spec_requirement_count=71  # type: ignore[arg-type]
    ) is False


# ---------------------------------------------------------------------------
# The leaf contract itself (survey/infra.md §9).
# ---------------------------------------------------------------------------


def test_the_leaf_module_still_imports_nothing_from_the_package() -> None:
    """`scripts/measure-run.py` depends on this being true, so it is asserted.

    The module docstring's contract — `json` and `pathlib` and nothing from the
    package — is what lets that CLI state a stdlib-only, package-free import
    cost and what keeps every caller free of an import cycle. A MODULE-level
    import of a `tools/` module would break both; a CALL-time one adds nothing
    to the module's import list, which is why `now_iso` and
    `handoffs_wall_clock_seconds` reach `datetime` that way.
    """
    import ast

    tree = ast.parse(Path(fs.__file__).read_text(encoding="utf-8"))
    module_level = [
        node for node in tree.body
        if isinstance(node, (ast.Import, ast.ImportFrom))
    ]
    names: set[str] = set()
    for node in module_level:
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif node.module:
            names.add(node.module)

    assert names == {"__future__", "json", "pathlib"}, (
        f"{sorted(names)} — the leaf contract is `json` and `pathlib`. A name "
        f"from `vocab` or a `tools/` module is passed IN, the way "
        f"`unreported_dispatch_pairs` takes `phase_of_dispatch`."
    )


def test_no_fourth_spec_path_resolver_grew_here() -> None:
    """Holmes `share-7` routes `_resolve_spec_path` into `tools/artifacts.py`.

    The finding is that the ladder is "re-inlined in three modules"; a fourth
    copy in the module written to end second derivations would be the finding
    answering itself. `prove_is_clean` takes the requirement COUNT instead, and
    a teammate who finds they need the path has a `Foundry-Concern` naming
    casting 7, not a copy.
    """
    source = Path(fs.__file__).read_text(encoding="utf-8")
    tree = __import__("ast").parse(source)
    defined = {
        node.name for node in tree.body
        if isinstance(node, __import__("ast").FunctionDef)
    }
    assert not {n for n in defined if "spec_path" in n or "spec_dir" in n}, (
        "a spec-path resolver in the leaf module is Holmes share-7's fourth copy"
    )


# ---------------------------------------------------------------------------
# fallout AC-011 / OT-010 — THE CROSS-SURFACE ASSERTION.
#
# The point of the consolidation is not that the readers exist; it is that the
# surfaces which used to answer a question twice now read ONE object. So each
# register below drives `generate_report` against a real run directory and
# asserts the section it wrote is what the leaf reader returns — not "a number
# that matches", but the same derivation, so a future edit cannot make them
# agree by coincidence.
#
# WHAT THIS CAN AND CANNOT PIN AT WAVE 1. `foundry_orchestrator` still holds
# its own copies of these rules; that is deliberate and named — the monolith is
# casting 2's file, and its group 0 replaces those copies with imports from
# here. So the assertions below are on the half that exists now: the REPORT
# reads through the leaf. Casting 2's own report closes the other half, and the
# AST no-second-definition pin lands with it.
# ---------------------------------------------------------------------------


@pytest.fixture
def report_run(tmp_path):
    """A copy of the report fixture archive; yields the run dir.

    The same `tests/fixtures/escalation/finer_boundary_run/` the report suite
    drives, because a cross-surface assertion is only worth making against the
    ledger shapes a real archive carries — a synthetic run with one clean row
    in each ledger would pass however the two surfaces derived it.
    """
    fixture = Path(__file__).parent / "fixtures" / "escalation" / "finer_boundary_run"
    run_dir = tmp_path / "foundry-archive" / "finer-boundary-run"
    run_dir.mkdir(parents=True)
    for src in fixture.iterdir():
        (run_dir / src.name).write_bytes(src.read_bytes())
    return run_dir


def _generated(run_dir: Path) -> dict:
    from foundry_mcp.tools.foundry_report import generate_report

    result = generate_report(run_dir.parent.parent, run_dir)
    assert result["ok"] is True, result
    return json.loads((run_dir / "report.json").read_text(encoding="utf-8"))


def test_the_report_spend_table_is_the_leafs_spend_rollup(report_run) -> None:
    """§3.2's live row: three copies of the spend arithmetic, now one.

    `_read_spend`'s own docstring documents FOUR defects caused by the two live
    copies disagreeing — D-038, D-090, D-162, D-163. The section is asserted
    field by field against the reader rather than only on the total, because
    every one of those four was a single field parting company.
    """
    from foundry_mcp.tools import foundry_report as fr

    section = _generated(report_run)["spend_per_phase_and_cycle"]

    spend_rows, _ = fs.read_jsonl(report_run / "spend.jsonl")
    state, _ = fs.read_document(report_run / "state.json")
    summary, problem = fr._read_dispatch_summary(report_run)
    assert problem is None
    table = fs.spend_rollup(
        spend_rows=spend_rows,
        state_rollup=state.get("spend"),
        dispatch_summary=summary,
    )

    for field in ("records", "by_phase", "by_cycle", "total", "state_rollup",
                  "disagreements", "unreported_without_cycle"):
        assert section[field] == table[field], field
    # The section adds prose and NOTHING else.
    assert set(section) == set(table) | {"note"}


def test_the_spend_unreported_column_and_the_dispatch_section_are_one_object(
    report_run,
) -> None:
    """D-163 — `total.unreported` IS the `unreported_dispatches` count.

    One report published `total.unreported: 0` beside its own
    `unreported_dispatches {"count": 1}` because the two reached the number
    two ways. They are the same integer by construction now, not by agreement.
    """
    document = _generated(report_run)

    assert (
        document["spend_per_phase_and_cycle"]["total"]["unreported"]
        == document["unreported_dispatches"]["count"]
    )


def test_the_report_inspect_section_is_the_leafs_census(report_run) -> None:
    """D-119 / D-193 — one census, and the axis it is measured against.

    `cycle_count` counts the cycles this ledger has a decision for and
    `cycle_axis_length` counts the cycles the run RAN; publishing the first as
    the second is what put two numbers for one axis in one document.
    """
    from foundry_mcp.schemas.vocab import INSPECT_MODES as MODES

    section = _generated(report_run)["inspect_modes_per_cycle"]
    state, _ = fs.read_document(report_run / "state.json")
    table = fs.inspect_mode_rows(
        state=state, derived=fs.derive_cycle_count(report_run), modes=MODES
    )

    for field in ("count", "cycle_count", "cycle_axis_length",
                  "cycles_without_decision", "by_mode", "per_cycle", "entries"):
        assert section[field] == table[field], field
    # The section adds the disclosure sentence and the acceptance ratio; the
    # axis numbers the sentence is built from came back WITH the rows, so the
    # prose and the table cannot describe two different axes.
    assert set(section) - set(table) == {"note", "full_cycle_ratio"}


def test_the_full_cycle_ratio_is_derived_once(report_run) -> None:
    """fallout AC-046 — `measure-run.py` and the report publish the same figure.

    Two derivations of one acceptance criterion is the one place a
    disagreement is unarguable: the archive either meets the target or it does
    not, and it cannot do both depending on which command printed it.
    """
    section = _generated(report_run)["inspect_modes_per_cycle"]

    assert section["full_cycle_ratio"] == fs.full_cycle_ratio(
        {"per_cycle": section["per_cycle"]}
    )
    assert section["full_cycle_ratio"]["total_cycles"] == section["cycle_count"]


# --------------------------------------------------------------------------- #
# fallout GI-033 / D-080 (concern C-059) — the symbols hoisted out of the
# verifier layer. Each was read by a verifier module and a lifecycle module at
# once, which fallout GI-033's arithmetic makes impossible anywhere but a leaf.
# --------------------------------------------------------------------------- #


def _run(tmp_path):
    """A run directory with a castings/ dir, which every reader below expects."""
    (tmp_path / "castings").mkdir(parents=True, exist_ok=True)
    return tmp_path


def test_git_changed_paths_tells_a_broken_git_from_a_clean_tree(tmp_path) -> None:
    """fallout D-080 row 1 — the diff reader, in the leaf both layers can reach.

    Driven against a directory that is not a repository, because that is the
    case the return shape exists for: ok=False with a NAMED error, never a
    raise and never an ok=True empty list. Those two are different facts and a
    caller reading the second as the first would treat a broken git as a tree
    with nothing changed — which is the direction that fails open.
    """
    out = fs.git_changed_paths(str(_run(tmp_path)), "HEAD~1", "HEAD")

    assert set(out) == {"ok", "files", "error"}
    assert out["files"] == []
    assert out["ok"] is False and out["error"]


def test_git_touching_commit_never_claims_a_commit_it_could_not_read(
    tmp_path,
) -> None:
    """fallout D-080 row 2 — total, and empty never invents a commit."""
    assert fs.git_touching_commit(str(_run(tmp_path)), "HEAD~1", "some/path.py") == ""


def test_boundary_base_sha_prefers_the_newest_marker(tmp_path) -> None:
    """fallout D-080 row 3 — which commit a cycle's diff is measured from.

    The three markers are tried in the order they become true about a run, so
    the newest fact wins and a stale marker cannot answer for it. The marker
    BASENAMES are passed in because they are declared in `artifacts.py`, which
    this stdlib-only module may not import.
    """
    fdir = _run(tmp_path)
    markers = dict(boundary_marker=".inspect-boundary-sha",
                   trace_marker=".trace-clean-at",
                   cast_marker=".cast-baseline-sha")

    assert fs.boundary_base_sha(fdir, **markers) == ("", ""), "no marker, no sha"

    (fdir / ".cast-baseline-sha").write_text("deadbee\n")
    assert fs.boundary_base_sha(fdir, **markers) == ("deadbee", ".cast-baseline-sha")

    (fdir / ".trace-clean-at").write_text(json.dumps({"head_sha": "cafe999"}))
    assert fs.boundary_base_sha(fdir, **markers) == ("cafe999", ".trace-clean-at")

    (fdir / ".inspect-boundary-sha").write_text("abc1234\n")
    assert fs.boundary_base_sha(fdir, **markers) == ("abc1234", ".inspect-boundary-sha"), (
        "the INSPECT boundary is the newest fact and outranks both"
    )

    # A torn marker reads as ABSENT rather than raising, and the next one answers.
    (fdir / ".inspect-boundary-sha").write_text("   \n")
    assert fs.boundary_base_sha(fdir, **markers) == ("cafe999", ".trace-clean-at")


def test_blocking_defects_counts_live_and_unknown_and_not_the_rest(tmp_path) -> None:
    """fallout D-080 row 6 / fallout AC-022 — the gate's FACTS, without its prose.

    LIVE and untiered both block and are counted SEPARATELY (fallout CT-008: the two
    need different remedies). LATENT and HARDENING block nothing — the tier
    added for off-spec driven failures must not hold a gate shut, which is
    fallout AC-022's second clause read from the other end.

    The refusal sentence is deliberately not here: a hint naming both filing
    doors and the GRIND phase is lifecycle knowledge, and this module's own
    contract keeps the READ here and the sentence there.
    """
    fdir = _run(tmp_path)
    (fdir / "defects.json").write_text(json.dumps({"defects": [
        {"id": "D-1", "status": "open", "tier": "LIVE"},
        {"id": "D-2", "status": "open", "tier": "HARDENING"},
        {"id": "D-3", "status": "open"},
        {"id": "D-4", "status": "open", "tier": "LATENT"},
        {"id": "D-5", "status": "closed", "tier": "LIVE"},
    ]}))

    out = fs.blocking_defects(
        fdir, tiers=DEFECT_TIERS, unknown_tier=TIER_UNKNOWN, tier_of=defect_tier
    )

    assert out == {"blocking": 2, "live": ["D-1"], "unknown": ["D-3"],
                   "latent": ["D-4"]}
    assert "D-2" not in out["live"], (
        "the HARDENING record is not a LIVE instance (fallout AC-022) and blocks nothing"
    )
    assert set(out) == {"blocking", "live", "unknown", "latent"}, (
        "no reason/hint: the sentence stays in the layer that speaks it"
    )


def test_blocking_defects_reads_an_absent_and_a_torn_ledger_as_nothing_open(
    tmp_path,
) -> None:
    """The empty and malformed drives this module owes every reader."""
    fdir = _run(tmp_path)
    kw = dict(tiers=DEFECT_TIERS, unknown_tier=TIER_UNKNOWN, tier_of=defect_tier)
    empty = {"blocking": 0, "live": [], "unknown": [], "latent": []}

    assert fs.blocking_defects(fdir, **kw) == empty, "absent ledger"
    (fdir / "defects.json").write_text("{ truncated")
    assert fs.blocking_defects(fdir, **kw) == empty, "torn ledger, no raise"


def test_skipped_stream_ids_maps_both_spellings_and_degrades(tmp_path) -> None:
    """fallout D-080 row 7 — the declared skip list, read in the leaf.

    Canonical spellings map back through the vocabulary rather than by
    lowercasing, because the two are not related by case alone (`TEST-01` /
    `test01`). Both the mapping and the wire-id set are passed IN, for the same
    reason every other reader here takes its vocabulary as an argument.
    """
    fdir = _run(tmp_path)
    kw = dict(wire_ids=vocab.STREAM_WIRE_IDS,
              wire_to_canonical=vocab.WIRE_TO_CANONICAL,
              shape_problem=lambda _m: None)

    assert fs.skipped_stream_ids(fdir, **kw) == set(), "no manifest, no declared skips"

    (fdir / "castings" / "manifest.json").write_text(json.dumps(
        {"castings": [], "stream_skips": [{"stream_id": "TEST-01"}, "sight"]}))
    got = fs.skipped_stream_ids(fdir, **kw)
    assert "sight" in got, "a wire id passes through as itself"
    assert "test01" in got, (
        "TEST-01 maps to test01 through WIRE_TO_CANONICAL, which lowercasing "
        "would have spelled 'test-01'"
    )

    # D-132: a non-indexable shape is decided by the SHARED validator, and the
    # reader degrades to "no declared skips" rather than raising out of a door.
    (fdir / "castings" / "manifest.json").write_text(json.dumps(
        {"castings": [], "stream_skips": 42}))
    assert fs.skipped_stream_ids(fdir, **kw) == set()
    (fdir / "castings" / "manifest.json").write_text("{ truncated")
    assert fs.skipped_stream_ids(fdir, **kw) == set()


def test_the_leaf_still_imports_nothing_from_the_package(tmp_path) -> None:
    """fallout GI-033 / FR-008 — the hoist did not cost the leaf its contract.

    `scripts/measure-run.py` reads this module with no package on the path, so
    a hoisted symbol that imported `vocab` for a tier set or `artifacts` for a
    marker name would end that. Every one of them takes its vocabulary as an
    argument instead, and this is what holds the line.
    """
    import ast
    from pathlib import Path as _P

    tree = ast.parse(_P(fs.__file__).read_text())
    reached = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            reached.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            reached.add(node.module or "")

    assert reached, "the walk must SEE something or it proves nothing"
    assert not [m for m in reached if "foundry_mcp" in m], (
        f"{sorted(m for m in reached if 'foundry_mcp' in m)} — the leaf reaches "
        f"into the package, so measure-run.py's package-free read is over"
    )


def test_the_full_cycle_ratio_threshold_is_spelled_once(monkeypatch) -> None:
    """fallout D-095 / fallout NFR-011 (concern C-056) — the printed figure IS the applied one.

    The threshold used to be typed TWICE — once as the `threshold` this reports
    and once inside the `ratio < 0.5` the verdict applies — so the F6 report
    could state one figure against a verdict computed from another. Nothing
    caught it, because two literals agree right up until someone edits one of
    them. That is fallout NFR-011's shape and `foundry_validate.REQUIREMENT_SPAN_MAX` is
    the precedent: one spelling feeding both the message and the table.

    THE PIN IS DERIVATION, NOT EQUALITY. Asserting that both are 0.5 passes just
    as happily against two literals, so it would not have caught the defect it
    exists for. Instead the constant is MOVED and the verdict has to move with
    it. A comparison that re-types the number keeps judging against 0.5 and
    fails below.

    Moved in BOTH directions, because a hard-coded `< 0.5` still happens to
    agree with the constant in one of them: at 0.75 the same ratio must pass and
    at 0.25 it must fail.
    """
    doc = {"per_cycle": {"1": [{"mode": "FULL"}], "2": [{"mode": "delta"}]}}

    shipped = fs.full_cycle_ratio(doc)
    assert shipped["ratio"] == 0.5, "one FULL cycle of two"
    assert shipped["threshold"] == fs.FULL_CYCLE_RATIO_THRESHOLD, (
        "the REPORTED threshold reads the module constant rather than a "
        "literal of its own"
    )

    monkeypatch.setattr(fs, "FULL_CYCLE_RATIO_THRESHOLD", 0.75)
    above = fs.full_cycle_ratio(doc)
    assert above["threshold"] == 0.75
    assert above["passes"] is True, (
        "the APPLIED threshold reads the same constant: with the bound at 0.75 "
        "a ratio of 0.5 is below it. A re-typed `ratio < 0.5` answers False "
        "here while the line above still reports 0.75 — the drift C-056 names"
    )

    monkeypatch.setattr(fs, "FULL_CYCLE_RATIO_THRESHOLD", 0.25)
    below = fs.full_cycle_ratio(doc)
    assert below["threshold"] == 0.25
    assert below["passes"] is False, (
        "and back: with the bound at 0.25 the same ratio is not below it. Both "
        "directions are driven because a literal 0.5 agrees with the constant "
        "in one of them by coincidence"
    )


def test_the_report_escalated_rows_are_the_leafs_rows(report_run) -> None:
    """D-214 / D-215 — one resolver, and now one row builder over it.

    `count == sum(by_status.values())` is the property the resolver buys, and
    it is asserted here as well as inside the reader because the report is the
    surface where the two were observed to disagree.
    """
    from foundry_mcp.schemas.vocab import (
        ESCALATION_EXIT_REASONS as REASONS,
        ESCALATION_STATUSES as STATUSES,
        escalation_status as resolver,
    )

    section = _generated(report_run)["escalated_classes"]
    document, _ = fs.read_document(report_run / "escalation.json")
    table = fs.escalated_class_rows(
        document, statuses=STATUSES, exit_reasons=REASONS, status_of=resolver
    )

    assert section == table
    assert section["count"] == sum(section["by_status"].values())


def test_the_report_dispatch_inputs_are_the_leafs_assembly(report_run) -> None:
    """D-047 / D-048 / D-163 — the RULE was shared; now the INPUTS are too.

    The roster and the cycle map come off ONE walk of the roll-up, so the two
    surfaces cannot disagree about which cycles a stream ran in — which is the
    disagreement `by_cycle` is keyed on.
    """
    from foundry_mcp.tools import foundry_report as fr

    summary, problem = fr._read_dispatch_summary(report_run)
    assert problem is None

    inputs = fs.unreported_dispatch_inputs(report_run)
    assert inputs["problem"] is None
    rebuilt = fs.unreported_dispatch_summary(
        dispatch_rows=inputs["dispatch_rows"],
        stream_roster=inputs["stream_roster"],
        spend_rows=inputs["spend_rows"],
        phase_of_dispatch=fs.DISPATCH_PHASE_TO_RUN_PHASE,
        agent_id_of=fr._agent_id_for_casting,
        cycles_of_agent=inputs["cycles_of_agent"],
    )
    assert summary == rebuilt


def test_the_report_stream_coverage_is_the_leafs_rollup_rows(report_run) -> None:
    """fallout CT-003 — the section renders what the reader derived, adds prose."""
    section = _generated(report_run)["stream_coverage_per_cycle"]
    table = fs.stream_rollup_rows(report_run)

    assert section["cycle_count"] == table["cycle_count"]
    assert section["stream_count"] == table["stream_count"]
    assert section["replaced"] == table["replaced"]
    assert section["over_total"] == table["over_total"]
    assert section["row_count"] == sum(
        len(streams) for streams in table["cycles"].values()
    )


def test_the_report_fallout_section_is_the_leafs_rows(report_run) -> None:
    """fallout FR-025 — `measure-run.py` counts the same field per cycle (casting 3).

    The axis is `derive_cycle_count`'s index, which is the SAME reading
    `inspect_modes_per_cycle` and `baseline_comparison` sit on, so the three
    sections cannot publish three different ideas of which cycles ran.
    """
    section = _generated(report_run)["fallout_per_cycle"]
    table = fs.fallout_rows(
        report_run, axis_top=fs.derive_cycle_count(report_run)["index"]
    )

    assert section == table


def test_the_done_gate_and_the_seal_apply_one_heading_rule(report_run) -> None:
    """Holmes `share-10` — the presence check is DERIVED from the splitter.

    `_markdown_missing_sections` decides which sections a reader can still
    find and the seal's splitter -- now `foundry_state.markdown_sections` --
    decides which blocks the seal preserves. They were "the same effective
    rule, coded independently", so the gate could call a section present that
    the seal did not treat as one.
    """
    from foundry_mcp.tools import artifacts
    from foundry_mcp.tools import foundry_report as fr

    _generated(report_run)
    text = (report_run / "REPORT.md").read_text(encoding="utf-8")

    # fallout GI-033: the presence check is the LEAF's now (concern C-060 row
    # 2) — the DONE gate is a verifier and could not reach it here. The rule it
    # applies is unchanged, which is the whole point of asserting it against
    # the seal's splitter below.
    missing, problem = artifacts._markdown_missing_report_sections(report_run)
    assert problem is None and missing == []
    assert fs.markdown_headings(text) >= {
        f"## {vocab.REPORT_SECTION_TITLES[key]}" for key in fr.REPORT_REQUIRED_SECTIONS
    }
    # And the two really are one walk: every heading the splitter starts a
    # block on is a heading the presence check sees, and nothing else is.
    assert fs.markdown_headings(text) == {
        heading for heading, _ in fs.markdown_sections(text)[1]
    }


# ---------------------------------------------------------------------------
# fallout D-012 / D-013 — the two derivations still held OUTSIDE this module.
#
# Both are shrink-only inventories, the shape the package's own boundary guard
# already uses (`_KNOWN_DUPLICATION`): every row is a REAL
# second derivation of a rule this module hosts, named with the module that
# holds it and why this casting could not remove it. A NEW second derivation
# fails immediately, and a row whose duplicate is GONE fails too — so the table
# shrinks and never grows quietly, and the casting that owns the named file
# closes its row by deleting both the copy and the row.
#
# They are not exemptions. The rule they stand in for is asserted right beside
# them: exactly one implementation, in `foundry_state`, plus whatever this
# table admits by name.
# ---------------------------------------------------------------------------

#: fallout D-012 — EMPTY, which is the state this table was written to reach.
#: `tools/foundry.py#_server_cycle` was the second reader of
#: `state.json["cycle"]`: byte-equivalent to `current_cycle` — same read, same
#: coercion, same 0 for missing/absent/malformed — kept by a justification
#: ("importing back would close a cycle in the import graph") that stopped
#: being true when the orchestrator was deleted and `tools/foundry.py` began
#: importing this module at module top. It is deleted and its six call sites
#: call `current_cycle` (concern C-008). The row goes with it; the guard below
#: stays, and fails on the next second reader whoever writes it.
_SECOND_CYCLE_READERS: dict[str, str] = {}

#: fallout D-013 — the second assembly of `unreported_dispatch_inputs`' three
#: ledgers, by module and symbol. `foundry_report._read_dispatch_summary`
#: closed its half by calling the leaf; these four survived the split and each
#: still re-spells `read_jsonl`'s line loop or walks the roll-up again.
#: KEYED ON THE MODULE, NOT ON EACH SYMBOL, and that is a correction rather
#: than a convenience. This table was written with one row per function and
#: shrunk against the SHARED WORKING TREE, where the casting that owns
#: `orchestration/spend.py` had already deleted two of the four — uncommitted.
#: Driven from a clean worktree at the same commit, the two rows I had removed
#: were still needed and the pin failed. A pin keyed on a sibling's in-flight
#: edits measures a tree no gate will ever see; the only state that is real is
#: the committed one, and a module row is the same claim at a grain that does
#: not flicker while the other casting lands its functions one at a time.
#:
#: The row goes stale — and this test fails — when that module assembles
#: NOTHING any more, which is the moment the concern closes. It lives in THIS
#: casting's file, so removing it is this casting's last step on the concern,
#: not the repointing casting's: they cannot edit here.
_SECOND_DISPATCH_ASSEMBLIES: dict[str, str] = {
    "orchestration/spend.py": (
        "the ledger walks feeding `_dispatch_pairs` — each re-spelling "
        "`read_jsonl`'s splitlines/json.loads loop or walking the roll-up a "
        "second time. Another casting's file; raised as a concern, and this "
        "row is deleted here once it reports the repoint landed."
    ),
}


def _package_modules() -> list[Path]:
    """Every shipped `.py` under the installed package, `__pycache__` aside."""
    pkg = Path(fs.__file__).resolve().parent.parent
    return sorted(p for p in pkg.rglob("*.py") if "__pycache__" not in p.parts)


def _module_key(path: Path, pkg_root: Path) -> str:
    rel = path.relative_to(pkg_root)
    return "/".join(rel.parts[1:]) if rel.parts[0] == "tools" else str(rel)


def _defined_functions(path: Path) -> set[str]:
    import ast

    return {
        node.name for node in ast.parse(path.read_text(encoding="utf-8")).body
        if isinstance(node, ast.FunctionDef)
    }


def test_the_cycle_counter_has_one_reader_and_any_second_is_named() -> None:
    """fallout GI-024 / OT-010 — `current_cycle` is the implementation.

    The subject is the SYMBOL, not the behaviour: two readers that agree today
    are two readers that can disagree tomorrow, and D-119 is what that cost
    when they did — the same finding filed at two doors landed in two cycles
    and a class that recurred three straight cycles evaded process-fixes
    ST-002 escalation.
    So the pin counts definitions rather than comparing outputs.
    """
    pkg_root = Path(fs.__file__).resolve().parent.parent
    found: dict[str, str] = {}
    for module in _package_modules():
        for name in _defined_functions(module):
            if name in ("current_cycle", "_server_cycle", "server_cycle"):
                found[f"{_module_key(module, pkg_root)}#{name}"] = name

    assert "foundry_state.py#current_cycle" in found, (
        "the leaf no longer defines `current_cycle`; it is the implementation "
        "every other reader is repointed ONTO, so its absence is the defect "
        "and not the fix."
    )
    second = {k for k in found if k != "foundry_state.py#current_cycle"}

    unnamed = second - set(_SECOND_CYCLE_READERS)
    assert not unnamed, (
        f"{sorted(unnamed)} reads the cycle counter beside "
        f"`foundry_state.current_cycle` and is named nowhere. Call the leaf, "
        f"or add a row to _SECOND_CYCLE_READERS saying why you could not."
    )
    stale = set(_SECOND_CYCLE_READERS) - second
    assert not stale, (
        f"{sorted(stale)} is named in _SECOND_CYCLE_READERS and no longer "
        f"exists. The table only shrinks: delete the row with the copy."
    )


def test_the_dispatch_input_assembly_has_one_home_and_any_second_is_named() -> None:
    """fallout GI-024 / FR-008 — `unreported_dispatch_inputs` is the assembly.

    The RULE has been shared since D-047/D-048; what was derived twice was the
    QUESTION — which ledgers, walked how, into which roster and cycle map. One
    derivation of the answer over two derivations of its inputs is the same
    defect, and the report's half is closed by calling the leaf.
    """
    pkg_root = Path(fs.__file__).resolve().parent.parent
    watched = {
        "_spawn_rows", "_spend_ledger_rows", "_stream_dispatch_cycles",
        "_dispatch_pairs",
    }
    found = {
        _module_key(module, pkg_root)
        for module in _package_modules()
        if _defined_functions(module) & watched
    }

    assert "foundry_state.py" not in found
    assert hasattr(fs, "unreported_dispatch_inputs"), (
        "the leaf no longer hosts the assembly the rule runs over"
    )

    unnamed = found - set(_SECOND_DISPATCH_ASSEMBLIES)
    assert not unnamed, (
        f"{sorted(unnamed)} assembles the dispatch inputs beside "
        f"`foundry_state.unreported_dispatch_inputs` and is named nowhere. "
        f"Call the leaf, or add a row saying why you could not."
    )
    stale = set(_SECOND_DISPATCH_ASSEMBLIES) - found
    assert not stale, (
        f"{sorted(stale)} is named in _SECOND_DISPATCH_ASSEMBLIES and no "
        f"longer exists. The table only shrinks: delete the row with the copy."
    )


def test_the_report_assembles_no_dispatch_inputs_of_its_own() -> None:
    """fallout D-013 — the half this casting owns, asserted as a property.

    `_read_dispatch_summary` supplies the run's inputs and judges nothing; the
    walk is the leaf's. Driven rather than read: the summary the report builds
    must equal the one built straight off the leaf's assembly, so a walk
    re-inlined into the report would have to reproduce the leaf byte for byte
    to pass — which is the drift, not an escape from it.
    """
    import inspect

    from foundry_mcp.tools import foundry_report as fr

    source = inspect.getsource(fr._read_dispatch_summary)
    assert "unreported_dispatch_inputs(" in source, (
        "the report assembles the dispatch inputs itself again (D-013)"
    )
    for walked in ("splitlines()", 'read_jsonl(', 'read_document('):
        assert walked not in source, (
            f"{walked!r} in `_read_dispatch_summary`: the ledger walk is "
            f"`foundry_state.unreported_dispatch_inputs`', not this module's."
        )


# ---------------------------------------------------------------------------
# fallout GI-033 / D-021 / D-035 — the reads the layering moved into the leaf.
#
# Each is driven three ways, like every other reader here: the derived answer
# over a synthetic run dir, the empty-ledger answer, and the malformed-document
# answer. The fourth clause for this family is the VOCABULARY one — every
# closed-set value these take is PASSED IN, because the leaf contract keeps
# this module free of package imports, so a literal spelled here would be the
# second opinion of a field that D-210 and D-212 were each filed over.
# ---------------------------------------------------------------------------

_TIERS = frozenset({"LIVE", "LATENT", "HARDENING"})
_UNKNOWN = "unknown"


def _tier_of(record: dict) -> str:
    """A stand-in for `vocab.defect_tier`: total, unknown for anything else."""
    raw = record.get("tier")
    return raw if isinstance(raw, str) and raw in _TIERS else _UNKNOWN


def test_current_inspect_mode_returns_the_last_entry_stamped_for_the_cycle(
    run_env,
) -> None:
    """fallout GI-008 / GI-009 — the width is READ, never re-derived."""
    _write_json(run_env, "state.json", {"cycle": 2, "inspect_modes": [
        {"cycle": 1, "mode": "DELTA", "rule": "no_verifier_touched"},
        {"cycle": 2, "mode": "FULL", "rule": "verifier_touched"},
    ]})
    entry = fs.current_inspect_mode(run_env, modes=frozenset({"FULL", "DELTA"}))

    assert entry["mode"] == "FULL"
    assert entry["rule"] == "verifier_touched"
    # The narrow readers ask about a NAMED cycle instead, and get that cycle's
    # answer only when the LAST entry is the one stamped for it.
    assert fs.current_inspect_mode(
        run_env, 1, modes=frozenset({"FULL", "DELTA"})
    ) is None


def test_current_inspect_mode_reads_an_unusable_record_as_no_record(
    run_env,
) -> None:
    """fallout D-212 / D-216 — three axes, each of which was a defect first.

    A mode outside the vocabulary, an entry that is not a mapping, and an entry
    stamped for another cycle each read as NO record — D-117's ruling ("an
    unrecorded width is not full width") applied to a value that is present and
    wrong rather than to one that is absent. Walking BACK to an older valid
    entry is the alternative, and it would report cycle N-1's FULL as cycle N's
    width and pass the ASSAY gate that refuses today.
    """
    modes = frozenset({"FULL", "DELTA"})
    for entries in (
        [{"cycle": 0, "mode": "delta"}],          # lowercase: not a member
        [{"cycle": 0, "mode": "BOGUS"}],
        ["not a mapping"],
        [{"cycle": 0, "mode": "FULL"}, {"cycle": 0, "mode": True}],
        [{"cycle": True, "mode": "FULL"}],        # bool is not a cycle
        [{"mode": "FULL"}],                       # no stamp at all
        [{"cycle": 9, "mode": "FULL"}],           # another cycle's decision
        [{"cycle": 0, "mode": "FULL"}, {"cycle": 1, "mode": "DELTA"}],
    ):
        _write_json(run_env, "state.json", {"cycle": 0, "inspect_modes": entries})
        assert fs.current_inspect_mode(run_env, modes=modes) is None, entries


def test_current_inspect_mode_is_none_on_an_empty_or_malformed_document(
    run_env,
) -> None:
    """Absent, empty and unreadable all read as "no width recorded"."""
    modes = frozenset({"FULL", "DELTA"})
    assert fs.current_inspect_mode(run_env, modes=modes) is None
    _write_json(run_env, "state.json", {"cycle": 0, "inspect_modes": []})
    assert fs.current_inspect_mode(run_env, modes=modes) is None
    _write_json(run_env, "state.json", {"cycle": 0, "inspect_modes": "nope"})
    assert fs.current_inspect_mode(run_env, modes=modes) is None
    (run_env / "state.json").write_text("{ not json", encoding="utf-8")
    assert fs.current_inspect_mode(run_env, modes=modes) is None


def test_open_defects_by_tier_derives_its_buckets_from_the_vocabulary(
    run_env,
) -> None:
    """fallout GI-014 / AC-011 — every member is a key, always, even empty.

    The buckets were three hand-typed keys while the resolver was total over
    the whole tier set, so the moment HARDENING joined that frozenset this
    raised `KeyError: 'HARDENING'` on any ledger carrying one — and the tier
    exists precisely so streams will file into it. An absent bucket also reads
    as zero blocking defects, which is the direction that fails open.
    """
    _write_json(run_env, "defects.json", {"defects": [
        {"id": "D-1", "status": "open", "tier": "LIVE"},
        {"id": "D-2", "status": "open", "tier": "LATENT"},
        {"id": "D-3", "status": "open", "tier": "HARDENING"},
        {"id": "D-4", "status": "open"},               # untiered: unknown
        {"id": "D-5", "status": "open", "tier": "BOGUS"},
        {"id": "D-6", "status": "fixed", "tier": "LIVE"},
        "not a mapping",
    ]})
    buckets = fs.open_defects_by_tier(
        run_env, tiers=_TIERS, unknown_tier=_UNKNOWN, tier_of=_tier_of
    )

    assert set(buckets) == set(_TIERS) | {_UNKNOWN}
    assert [d["id"] for d in buckets["LIVE"]] == ["D-1"]
    assert [d["id"] for d in buckets["HARDENING"]] == ["D-3"]
    # An untiered record and one carrying a string no vocabulary knows land on
    # the SAME sentinel, and it is not LATENT: reading them as LATENT would
    # silently clear every gate on records nobody ever classified
    # (convergence FR-051).
    assert [d["id"] for d in buckets[_UNKNOWN]] == ["D-4", "D-5"]
    assert buckets["LATENT"] and "D-6" not in {d["id"] for d in buckets["LATENT"]}


def test_open_defects_by_tier_answers_an_empty_or_malformed_ledger(
    run_env,
) -> None:
    """Every bucket present and empty — never a missing key."""
    for document in ({}, {"defects": []}, {"defects": "nope"}, {"defects": None}):
        _write_json(run_env, "defects.json", document)
        buckets = fs.open_defects_by_tier(
            run_env, tiers=_TIERS, unknown_tier=_UNKNOWN, tier_of=_tier_of
        )
        assert set(buckets) == set(_TIERS) | {_UNKNOWN}
        assert all(bucket == [] for bucket in buckets.values()), document
    (run_env / "defects.json").write_text("{ not json", encoding="utf-8")
    assert all(
        bucket == []
        for bucket in fs.open_defects_by_tier(
            run_env, tiers=_TIERS, unknown_tier=_UNKNOWN, tier_of=_tier_of
        ).values()
    )


def test_open_defect_ids_by_tier_is_the_same_buckets_reduced_to_ids(
    run_env,
) -> None:
    """convergence CT-008's refusal names the ids; the SENTENCE is not here.

    A LIVE defect needs fixing and an unknown-tier one needs a stream to
    re-file it with a tier, so the gate's prose must tell them apart — and that
    prose names both filing doors and the GRIND phase, which is protocol
    knowledge. The ids come from the leaf; the sentence stays with the gate.
    """
    _write_json(run_env, "defects.json", {"defects": [
        {"id": "D-1", "status": "open", "tier": "LIVE"},
        {"status": "open", "tier": "LIVE"},            # no id at all
        {"id": "D-9", "status": "open"},
    ]})
    ids = fs.open_defect_ids_by_tier(
        run_env, tiers=_TIERS, unknown_tier=_UNKNOWN, tier_of=_tier_of
    )
    assert ids["LIVE"] == ["D-1", "?"]
    assert ids[_UNKNOWN] == ["D-9"]
    assert ids["LATENT"] == []


def test_halted_state_reads_both_spellings_of_the_reason(run_env) -> None:
    """fallout CT-004 / FR-054 — the structured record and the legacy string.

    The member is never GUESSED out of a pre-release sentence: that is how a
    run's ending gets reclassified by a reader. "" means "this record carries
    text and no member", which is what every archive written before
    fallout FR-019 carries.
    """
    reason_of = lambda v: v if v in {"cap_reached", "lead_ruling"} else None
    max_cycles_of = lambda state: state.get("max_cycles") or 0

    _write_json(run_env, "state.json", {
        "phase": "HALTED", "halted_at_cycle": 4, "max_cycles": 4,
        "halted_reason": {"reason": "cap_reached", "text": "cycle 5 would exceed it"},
    })
    record = fs.halted_state(
        run_env, halted_phase="HALTED", reason_of=reason_of,
        max_cycles_of=max_cycles_of,
    )
    assert record["halted_reason_member"] == "cap_reached"
    assert record["halted_reason"] == "cap_reached: cycle 5 would exceed it"
    assert record["max_cycles"] == 4
    assert record["halted_report_error"] == ""

    _write_json(run_env, "state.json", {
        "phase": "HALTED", "halted_at_cycle": 2,
        "halted_reason": "--max-cycles 2 reached: opening GRIND cycle 3 would exceed it",
    })
    legacy = fs.halted_state(
        run_env, halted_phase="HALTED", reason_of=reason_of,
        max_cycles_of=max_cycles_of,
    )
    assert legacy["halted_reason_member"] == ""
    assert legacy["halted_reason"].startswith("--max-cycles 2 reached")


def test_halted_state_is_none_when_the_run_is_not_halted(run_env) -> None:
    """The terminal state is decided ONCE, so no door can decide it twice."""
    reason_of = lambda v: None
    max_cycles_of = lambda state: 0
    for state in ({}, {"phase": "F3"}, {"phase": "DONE"}, {"phase": None}):
        _write_json(run_env, "state.json", state)
        assert fs.halted_state(
            run_env, halted_phase="HALTED", reason_of=reason_of,
            max_cycles_of=max_cycles_of,
        ) is None, state
    (run_env / "state.json").write_text("{ not json", encoding="utf-8")
    assert fs.halted_state(
        run_env, halted_phase="HALTED", reason_of=reason_of,
        max_cycles_of=max_cycles_of,
    ) is None


def test_registered_team_dirs_is_the_roster_intersected_with_the_disk(
    run_env, tmp_path,
) -> None:
    """A team is active while the roster names it AND its directory is there.

    The directory going away is what `TeamDelete` does, so a name with no
    directory is a roster entry nobody cleaned up — not a team holding the
    tree. The OTHER half, a scan for live teammate panes, reads no run artifact
    and stays with the module that knows how to look.
    """
    teams_dir = tmp_path / "teams"
    (teams_dir / "cast-run-wave-1").mkdir(parents=True)
    _write_json(run_env, "state.json", {"active_teams": [
        "cast-run-wave-1", "grind-run-cycle-1", 7, "",
    ]})
    assert fs.registered_team_dirs(run_env, teams_dir=teams_dir) == [
        "cast-run-wave-1"
    ]


def test_registered_team_dirs_answers_an_empty_or_malformed_roster(
    run_env, tmp_path,
) -> None:
    teams_dir = tmp_path / "teams"
    teams_dir.mkdir()
    for state in ({}, {"active_teams": []}, {"active_teams": "nope"},
                  {"active_teams": None}):
        _write_json(run_env, "state.json", state)
        assert fs.registered_team_dirs(run_env, teams_dir=teams_dir) == [], state
    (run_env / "state.json").write_text("{ not json", encoding="utf-8")
    assert fs.registered_team_dirs(run_env, teams_dir=teams_dir) == []


def test_sight_required_honours_the_no_ui_declaration(run_env) -> None:
    """fallout AC-052 / FR-055 — the flag is read BEFORE the extension scan.

    The previous shape implemented the opposite: with the flag set and any UI
    extension in scope it answered required True / blocked True, so declaring a
    run had no browsable UI was the one way to make the browser audit mandatory
    AND unsatisfiable. Driven here with a `.tsx` key file, which is the shape
    every earlier `no_ui` fixture was missing.
    """
    (run_env / "castings").mkdir()
    _write_json(run_env, "castings/manifest.json", {
        "no_ui": True,
        "castings": [{"id": 1, "key_files": ["src/App.tsx"]}],
    })
    answer = fs.sight_required(
        run_env, shape_problem=lambda _d: None, no_ui_meaning="no browsable UI",
    )
    assert answer == {
        "required": False, "blocked": False, "no_ui": True,
        "ui_files": 1, "reason": "no browsable UI",
    }


def test_sight_required_reads_the_extensions_when_nothing_was_declared(
    run_env,
) -> None:
    """Absence of the flag is not a claim either way, so the files decide."""
    (run_env / "castings").mkdir()
    _write_json(run_env, "castings/manifest.json", {
        "castings": [{"id": 1, "key_files": ["src/App.tsx", "src/api.py"]}],
    })
    blocked = fs.sight_required(
        run_env, shape_problem=lambda _d: None, no_ui_meaning="unused"
    )
    assert blocked["required"] is True and blocked["blocked"] is True
    assert blocked["ui_files"] == 1

    _write_json(run_env, "castings/manifest.json", {
        "target_url": "http://localhost:3000",
        "castings": [{"id": 1, "key_files": ["src/App.tsx"]}],
    })
    ready = fs.sight_required(
        run_env, shape_problem=lambda _d: None, no_ui_meaning="unused"
    )
    assert ready == {
        "required": True, "blocked": False,
        "url": "http://localhost:3000", "ui_files": 1,
    }

    _write_json(run_env, "castings/manifest.json", {
        "castings": [{"id": 1, "key_files": ["src/api.py"]}],
    })
    assert fs.sight_required(
        run_env, shape_problem=lambda _d: None, no_ui_meaning="unused"
    ) == {"required": False, "reason": "No frontend files in castings"}


def test_sight_required_requires_nothing_of_an_absent_or_unusable_manifest(
    run_env,
) -> None:
    """fallout D-134 — the RECORDS, not just the container.

    `castings: "nope"` used to meet `.get()` and raise AttributeError out of
    Foundry-Next. A run with no manifest yet requires nothing, which is what is
    true of it, and one whose records are unusable says so.
    """
    assert fs.sight_required(
        run_env, shape_problem=lambda _d: None, no_ui_meaning="unused"
    ) == {"required": False}

    (run_env / "castings").mkdir()
    _write_json(run_env, "castings/manifest.json", {"castings": "nope"})
    assert fs.sight_required(
        run_env, shape_problem=lambda _d: "records are not a list",
        no_ui_meaning="unused",
    ) == {
        "required": False,
        "reason": "castings/manifest.json records are unreadable",
    }

    (run_env / "castings" / "manifest.json").write_text("{ not json",
                                                        encoding="utf-8")
    assert fs.sight_required(
        run_env, shape_problem=lambda _d: None, no_ui_meaning="unused"
    )["required"] is False


def test_persisted_escalated_classes_resolves_every_shape_through_the_resolver(
    run_env,
) -> None:
    """fallout D-210 / D-212 — nothing pre-filters the shape ahead of it.

    The comprehension tested `isinstance(entry, dict)` BEFORE the resolver, so
    its third rung — an entry that is not a mapping reads as ESCALATED — was
    unreachable through this door, and a non-mapping entry was DROPPED from a
    list the DONE gate refuses on. Driven at cdb9322: `{"status": "BOGUS"}`
    blocked correctly while `"just a string"`, `["ESCALATED"]` and `null` each
    let the run reach DONE. convergence ST-010 is "every escalated class
    CLEARED", and an entry that is not a mapping carries no CLEARED.
    """
    def status_of(entry):
        if isinstance(entry, dict) and entry.get("status") == "CLEARED":
            return "CLEARED"
        return "ESCALATED"

    classes = {
        "cleared-class": {"status": "CLEARED"},
        "live-class": {"status": "ESCALATED"},
        "bogus-class": {"status": "BOGUS"},
        "shapeless-class": "just a string",
        "listy-class": ["ESCALATED"],
        "null-class": None,
    }
    assert fs.persisted_escalated_classes(
        classes, overrides=set(), status_of=status_of, escalated="ESCALATED"
    ) == ["bogus-class", "listy-class", "live-class", "null-class",
          "shapeless-class"]


def test_persisted_escalated_classes_honours_the_operators_overrides() -> None:
    """An arm reading the document directly would bypass the filter.

    It would eventually stamp a class CLEARED with an exit reason no rule
    earned — recording the operator's decision as the machine's, irreversibly,
    since CLEARED is terminal and a withdrawn directive could never bring the
    class back. `"*"` clears every class.
    """
    status_of = lambda _entry: "ESCALATED"
    classes = {"a": {}, "b": {}}
    assert fs.persisted_escalated_classes(
        classes, overrides={"a"}, status_of=status_of, escalated="ESCALATED"
    ) == ["b"]
    assert fs.persisted_escalated_classes(
        classes, overrides={"*"}, status_of=status_of, escalated="ESCALATED"
    ) == []
    for malformed in ({}, None, "nope", []):
        assert fs.persisted_escalated_classes(
            malformed, overrides=set(), status_of=status_of,
            escalated="ESCALATED",
        ) == [], malformed


# ---------------------------------------------------------------------------
# THE MOVE IS FAITHFUL, ASSERTED AS AN EQUALITY RATHER THAN CLAIMED.
#
# The point of moving a read down is that the surfaces which used to answer a
# question through a cross-layer import now answer it from one place. That is
# only true if the leaf's answer IS the answer the orchestration reader gave,
# so each register below drives BOTH over the same synthetic run directory and
# asserts they agree — not "a number that matches", but the same document
# resolved the same way, so a future edit to either cannot make them agree by
# coincidence.
#
# Scoped to the three that take a run directory. `_check_active_teams` and
# `_check_sight_required` take a project root and resolve it through the
# active-run lookup, which is machine state rather than a run artifact; their
# leaf halves are driven directly above.
# ---------------------------------------------------------------------------


def test_the_leafs_width_read_is_the_width_readers_answer(run_env) -> None:
    """fallout GI-033 — `width._current_inspect_mode`'s answer, from the leaf.

    AN AGREEMENT PIN, so it retires the day its subject does. It compares the
    leaf against the reader it was hoisted from while BOTH exist — the only
    thing showing the hoist preserved behaviour rather than merely compiling —
    and casting 2 deletes the width copy as it repoints. A hard import would
    turn this red on that commit and read as a regression in the leaf, which is
    the opposite of what it would mean, so it skips with a reason naming why.
    """
    from foundry_mcp.schemas.vocab import INSPECT_MODES

    try:
        from foundry_mcp.tools.orchestration.width import _current_inspect_mode
    except ImportError:
        pytest.skip(
            "width._current_inspect_mode is gone — casting 2 repointed to the "
            "leaf (C-059 row 4). Nothing is left to agree with, and the leaf "
            "reader is driven directly above."
        )

    for entries in (
        [{"cycle": 0, "mode": "FULL", "rule": "first_of_phase"}],
        [{"cycle": 0, "mode": "DELTA", "rule": "no_verifier_touched"}],
        [{"cycle": 1, "mode": "FULL"}],          # another cycle's decision
        [{"cycle": 0, "mode": "BOGUS"}],
        ["not a mapping"],
        [],
    ):
        _write_json(run_env, "state.json", {"cycle": 0, "inspect_modes": entries})
        assert fs.current_inspect_mode(
            run_env, modes=INSPECT_MODES
        ) == _current_inspect_mode(run_env), entries


def test_the_leafs_tier_buckets_are_the_gates_buckets(run_env) -> None:
    """fallout GI-033 — `gates._open_defects_by_tier`'s answer, from the leaf.

    This is the edge fallout GI-033 forbids with no exception at all: the guidance
    module is lifecycle and the gate module is a verifier, so a lifecycle
    module importing the gate to read the defect ledger is a
    LIFECYCLE-TO-VERIFIER import. Reading it from the leaf removes the edge
    without moving the judgement anywhere.
    """
    from foundry_mcp.schemas.vocab import DEFECT_TIERS, TIER_UNKNOWN, defect_tier
    from foundry_mcp.tools.orchestration.gates import _open_defects_by_tier

    _write_json(run_env, "defects.json", {"defects": [
        {"id": "D-1", "status": "open", "tier": "LIVE"},
        {"id": "D-2", "status": "open", "tier": "LATENT"},
        {"id": "D-3", "status": "open", "tier": "HARDENING"},
        {"id": "D-4", "status": "open"},
        {"id": "D-5", "status": "open", "tier": "BOGUS"},
        {"id": "D-6", "status": "fixed", "tier": "LIVE"},
        "not a mapping",
    ]})
    assert fs.open_defects_by_tier(
        run_env, tiers=DEFECT_TIERS, unknown_tier=TIER_UNKNOWN,
        tier_of=defect_tier,
    ) == _open_defects_by_tier(run_env)


def test_the_halt_modules_read_is_the_leafs_and_supplies_the_vocabulary(
    run_env,
) -> None:
    """fallout GI-033 — the DELEGATION is the subject now, not an equality.

    THIS PIN WENT TAUTOLOGICAL AND IS REBUILT RATHER THAN DELETED (concern
    C-021). It asserted `fs.halted_state(...) == halt._halted_state(run_env)`,
    which held two real implementations against each other until casting 2's
    da97739 made `_halted_state` precisely that call with those three
    arguments. Both sides then evaluated the same expression, so the assertion
    was true for every state in the loop whatever either implementation did —
    including the malformed and wrong-cycle shapes the loop existed to cover.
    Green, and pinning nothing.

    What is still worth protecting is one level out. The leaf cannot know what
    a HALTED phase, a reason vocabulary or a cap is — its contract is `json`
    and `pathlib` — so the DELEGATION supplies all three, and a delegation that
    passed a different reason vocabulary would answer differently with nothing
    to say so. So the subject is the call itself: it reaches `halted_state`,
    and it binds the three closed-set values by the names that mean them.

    Structural rather than a substring match on the source, because a comment
    naming `halt_reason` would satisfy a grep and prove nothing.
    """
    import ast
    import inspect

    # fallout GI-033 (concern C-027) — THE DELEGATION MOVED WITH THE READ.
    # `_halted_state` sat in halt.py while gates.py and transitions.py — both
    # VERIFIER — were its only remaining callers, so casting 2 moved it to
    # gates.py the moment `persisted_max_cycles` landed here. This pin follows
    # the delegation rather than the module it used to live in; what it asserts
    # is unchanged.
    from foundry_mcp.tools.orchestration import gates

    tree = ast.parse(inspect.getsource(gates._halted_state).strip())
    calls = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "halted_state"
    ]
    assert len(calls) == 1, (
        "gates._halted_state no longer delegates to `foundry_state.halted_state`. "
        "If it grew its own implementation back, that is a SECOND read of the "
        "terminal state and the thing GI-033 moved down; if it delegates by "
        "another spelling, point this pin at that spelling."
    )
    bound = {kw.arg: kw.value for kw in calls[0].keywords}
    assert set(bound) == {"halted_phase", "reason_of", "max_cycles_of"}, sorted(bound)
    assert isinstance(bound["halted_phase"], ast.Name)
    assert bound["halted_phase"].id == "RUN_PHASE_HALTED"
    assert bound["reason_of"].id == "halt_reason"
    # The cap read is the LEAF's now, under its unprefixed name — halt.py's
    # private copy is gone (C-027), which is the whole point of the move.
    assert bound["max_cycles_of"].id == "persisted_max_cycles"


def test_halted_state_answers_a_hand_built_expected_record(run_env) -> None:
    """The value half, against an expectation this file OWNS.

    The retired equality could never assert a VALUE: both of its sides came
    from the same code, so it could only ever say "these two agree", which
    became "this agrees with itself". Six states, each with the record spelled
    out here, is what an independent expectation looks like — a change in the
    leaf's coercion now fails against a number written down rather than
    against a second copy of itself.

    The vocabulary is the delegation's own, so the drive is the live one:
    fallout FR-019's four members from `vocab`, and the cap normaliser from
    this module's own `persisted_max_cycles` — which is where it lives since
    concern C-027, halt.py's private copy having been the thing that move
    deleted.
    """
    from foundry_mcp.schemas.vocab import RUN_PHASE_HALTED, halt_reason

    def read(state: dict):
        _write_json(run_env, "state.json", state)
        return fs.halted_state(
            run_env, halted_phase=RUN_PHASE_HALTED, reason_of=halt_reason,
            max_cycles_of=fs.persisted_max_cycles,
        )

    assert read({
        "phase": "HALTED", "halted_at_cycle": 4, "max_cycles": 4,
        "halted_reason": {"reason": "cap_reached", "text": "cycle 5 exceeds it"},
    }) == {
        "halted_at_cycle": 4,
        "halted_reason": "cap_reached: cycle 5 exceeds it",
        "halted_reason_member": "cap_reached",
        "max_cycles": 4,
        "halted_report_error": "",
    }

    # The spelling that predates fallout FR-019: a bare sentence, and NO
    # member guessed out of it. "" is the real answer: text, no member.
    assert read({
        "phase": "HALTED", "halted_at_cycle": 2,
        "halted_reason": "--max-cycles 2 reached",
    }) == {
        "halted_at_cycle": 2,
        "halted_reason": "--max-cycles 2 reached",
        "halted_reason_member": "",
        "max_cycles": 0,
        "halted_report_error": "",
    }

    # A structured record whose member is outside the vocabulary keeps its
    # TEXT and loses the member, for the same reason: the sentence is what an
    # operator reads, and no grouper should key on a value nothing declared.
    assert read({
        "phase": "HALTED", "halted_reason": {"reason": "BOGUS", "text": "x"},
    }) == {
        "halted_at_cycle": None,
        "halted_reason": "x",
        "halted_reason_member": "",
        "max_cycles": 0,
        "halted_report_error": "",
    }

    # HALTED with nothing recorded still reads as halted, with the default
    # sentence — never as "not halted", which is the direction that fails open.
    assert read({"phase": "HALTED"}) == {
        "halted_at_cycle": None,
        "halted_reason": "the configured cycle cap was reached",
        "halted_reason_member": "",
        "max_cycles": 0,
        "halted_report_error": "",
    }

    assert read({"phase": "F3"}) is None
    assert read({}) is None


# ---------------------------------------------------------------------------
# fallout GI-033 / FR-063 / AC-061 / D-021 / D-035 (concerns C-027, C-030) —
# the leaf moves.
#
# Each reader below was declared in an `orchestration/` module and read from
# BOTH layers the boundary guard keeps apart, so it could live in neither. The
# expected values here are HAND-BUILT and never compared against the copy each
# one came from: castings 2 and 1 delete those copies and repoint in the same
# wave, so a parity assertion would be comparing an expression to itself and
# then, days later, to nothing at all.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "persisted,expected",
    [
        ({"max_cycles": 3}, 3),
        ({"max_cycles": 0}, 0),
        ({}, 0),
        # D-225: the door validates with a JSON-Schema `integer`, in which a
        # zero-fraction float IS one. `2.0` was ACCEPTED there and read as
        # absent here, so a run capped at 2 opened GRIND cycle 100.
        ({"max_cycles": 2.0}, 2),
        ({"max_cycles": 2.5}, 0),
        # `bool` is an `int` subclass and `True` is not a cap of one.
        ({"max_cycles": True}, 0),
        ({"max_cycles": False}, 0),
        ({"max_cycles": -1}, 0),
        ({"max_cycles": "2"}, 0),
        ({"max_cycles": None}, 0),
    ],
)
def test_persisted_max_cycles_reads_a_cap_or_says_there_is_none(
    persisted, expected
) -> None:
    """convergence CT-016 — one read of the cap, in the door's terms (D-225).

    Zero means unbounded, and anything that is not a usable cap reads as zero
    because this is consulted inside a transition whose only other answer is
    "proceed". The float row is the defect: it was ACCEPTED at the door and
    discarded here, which is the one combination that loses a cap silently.
    """
    assert fs.persisted_max_cycles(persisted) == expected


def test_finalize_open_phase_entry_closes_an_open_entry_with_its_duration() -> None:
    """The open entry gets `ended_at` and a rendered `duration`, in place."""
    entry = {"started_at": "2026-09-05T10:00:00+00:00"}
    assert fs.finalize_open_phase_entry(entry, "2026-09-05T10:03:07+00:00") is None
    assert entry == {
        "started_at": "2026-09-05T10:00:00+00:00",
        "ended_at": "2026-09-05T10:03:07+00:00",
        "duration": "3m 7s",
    }


def test_finalize_open_phase_entry_leaves_a_closed_or_unstarted_entry_alone() -> None:
    """Closing twice would overwrite when a phase ENDED with when someone looked.

    And an entry with no `started_at` was never open, so there is nothing to
    close — a phase this run has not entered must not acquire an end time.
    """
    closed = {"started_at": "2026-09-05T10:00:00+00:00", "ended_at": "ALREADY"}
    fs.finalize_open_phase_entry(closed, "2026-09-05T11:00:00+00:00")
    assert closed == {"started_at": "2026-09-05T10:00:00+00:00", "ended_at": "ALREADY"}

    unstarted: dict = {}
    fs.finalize_open_phase_entry(unstarted, "2026-09-05T11:00:00+00:00")
    assert unstarted == {}


def test_an_unparseable_start_still_closes_the_entry_without_a_duration() -> None:
    """The stamp comes before the arithmetic, and that ordering is the point.

    A hand-edited archive whose `started_at` is not ISO-8601 would otherwise
    leave an entry that stays open forever and is re-closed at every later
    transition.
    """
    entry = {"started_at": "not-a-timestamp"}
    fs.finalize_open_phase_entry(entry, "2026-09-05T10:03:07+00:00")
    assert entry == {
        "started_at": "not-a-timestamp",
        "ended_at": "2026-09-05T10:03:07+00:00",
    }
    assert "duration" not in entry


# --- the concern-ledger read (C-030's mechanical half) ---------------------

OPEN = "open"


def _concern(**fields) -> dict:
    record = {
        "id": "C-1", "cycle": 2, "source_casting": 10,
        "target_casting_id": 4, "status": OPEN, "text": "t",
    }
    record.update(fields)
    return record


def test_open_cross_casting_concerns_names_only_the_ones_landing_elsewhere(
    run_env,
) -> None:
    """fallout GI-023 / ST-005 — the list the INSPECT door refuses on.

    Four records, one qualifying: a concern a casting filed against ITSELF
    lands on nobody else, and one whose target resolved to no casting lands on
    nobody at all. Refusing INSPECT for either would block the run over work
    that has no other owner.
    """
    _write_json(run_env, "concerns.json", {"concerns": [
        _concern(id="C-1", source_casting=10, target_casting_id=4),
        _concern(id="C-2", source_casting=10, target_casting_id=10),
        _concern(id="C-3", source_casting=10, target_casting_id=None),
        _concern(id="C-4", source_casting=10, target_casting_id=2, status="closed"),
    ]})
    got = fs.open_cross_casting_concerns(run_env, status_open=OPEN)
    assert [c["id"] for c in got] == ["C-1"]


def test_a_dispatched_concern_is_addressed_and_leaves_the_list(run_env) -> None:
    """fallout FR-039 — `dispatched` is the mark Foundry-Tasks leaves when the
    concern reached the casting that owns it, so it is addressed by
    definition. Only `open` qualifies; nothing else is treated as open by
    omission.
    """
    _write_json(run_env, "concerns.json", {"concerns": [
        _concern(id="C-1", status="dispatched"),
        _concern(id="C-2", status="closed"),
        _concern(id="C-3", status=OPEN),
    ]})
    assert [c["id"] for c in fs.open_cross_casting_concerns(run_env, status_open=OPEN)] == ["C-3"]


def test_the_concern_read_scopes_to_one_cycle_when_asked(run_env) -> None:
    """The door refuses on the CLOSING GRIND's concerns, not the run's history."""
    _write_json(run_env, "concerns.json", {"concerns": [
        _concern(id="C-1", cycle=1), _concern(id="C-2", cycle=2),
    ]})
    assert [c["id"] for c in fs.open_cross_casting_concerns(
        run_env, status_open=OPEN, cycle=2)] == ["C-2"]
    assert [c["id"] for c in fs.open_cross_casting_concerns(
        run_env, status_open=OPEN)] == ["C-1", "C-2"]


def test_a_casting_id_typed_as_an_int_and_as_a_string_are_the_same_casting(
    run_env,
) -> None:
    """A manifest may carry ids as integers and a filing the same id as a string.

    Comparing them raw would make a self-targeting concern look cross-casting
    and refuse INSPECT over a concern that lands on its own filer.
    """
    _write_json(run_env, "concerns.json", {"concerns": [
        _concern(id="C-1", source_casting=10, target_casting_id="10"),
    ]})
    assert fs.open_cross_casting_concerns(run_env, status_open=OPEN) == []


@pytest.mark.parametrize("document", [
    {}, {"concerns": "nope"}, {"concerns": ["a string", 7, None]},
], ids=["no-collection", "not-a-list", "non-dict-members"])
def test_the_concern_read_is_total_over_a_malformed_ledger(run_env, document) -> None:
    """No reader here may raise across the MCP boundary, and this one is
    consulted from inside a transition — a raise takes the door down."""
    _write_json(run_env, "concerns.json", document)
    assert fs.open_cross_casting_concerns(run_env, status_open=OPEN) == []


def test_an_absent_and_a_torn_concern_ledger_both_read_as_nothing_open(
    run_env,
) -> None:
    """A run that filed no concern has no concerns.json, and the door must open."""
    assert fs.open_cross_casting_concerns(run_env, status_open=OPEN) == []
    (run_env / "concerns.json").write_text('{"concerns": [', encoding="utf-8")
    assert fs.open_cross_casting_concerns(run_env, status_open=OPEN) == []


# --- the two halves of "is a team still holding the tree" -------------------


def _scan(*, available=True, live=()) -> object:
    return lambda: {
        "available": available, "live": list(live),
        "zombie": [], "user": [], "lead": None,
    }


def test_active_teams_is_active_when_either_half_says_so(run_env, tmp_path) -> None:
    """BOTH halves must be clear for a gate to pass (C-020's drive).

    The artifact half alone passes while teammates are still running; the pane
    half alone passes while a roster entry nobody cleaned up still names a team.
    """
    teams_dir = tmp_path / "teams"
    (teams_dir / "cast-team").mkdir(parents=True)
    _write_json(run_env, "state.json", {"active_teams": ["cast-team"]})

    only_dirs = fs.active_teams(run_env, teams_dir=teams_dir, scan=_scan())
    assert only_dirs == {
        "active": True, "teams": ["cast-team"], "live_panes": [],
    }

    _write_json(run_env, "state.json", {"active_teams": []})
    only_panes = fs.active_teams(
        run_env, teams_dir=teams_dir,
        scan=_scan(live=[("%1", "@grind-c10", "2.1.80")]),
    )
    assert only_panes == {
        "active": True, "teams": [], "live_panes": ["@grind-c10"],
    }


def test_active_teams_is_clear_only_when_both_halves_are(run_env, tmp_path) -> None:
    """A roster naming a team whose directory TeamDelete removed is an entry
    nobody cleaned up, not a live team — so with no panes the gate may pass."""
    teams_dir = tmp_path / "teams"
    teams_dir.mkdir()
    _write_json(run_env, "state.json", {"active_teams": ["deleted-team"]})
    assert fs.active_teams(run_env, teams_dir=teams_dir, scan=_scan()) == {
        "active": False, "teams": [], "live_panes": [],
    }


def test_the_shutdown_hint_is_offered_only_for_the_case_it_explains(
    run_env, tmp_path
) -> None:
    """Panes running with no team registered is the state an operator cannot
    work out from the two lists — the roster says gone, the machine says not.

    The sentence itself is passed in: it names SendMessage, TeamDelete and
    `tmux kill-pane`, which is the lifecycle layer's protocol and not this
    module's to spell.
    """
    teams_dir = tmp_path / "teams"
    (teams_dir / "cast-team").mkdir(parents=True)
    _write_json(run_env, "state.json", {"active_teams": ["cast-team"]})
    hint_for = lambda panes: f"shut down {len(panes)}"

    with_team = fs.active_teams(
        run_env, teams_dir=teams_dir, hint_for=hint_for,
        scan=_scan(live=[("%1", "@grind-c10", "2.1.80")]),
    )
    assert "hint" not in with_team

    _write_json(run_env, "state.json", {"active_teams": []})
    without_team = fs.active_teams(
        run_env, teams_dir=teams_dir, hint_for=hint_for,
        scan=_scan(live=[("%1", "@grind-c10", "2.1.80"), ("%2", "@grind-c4", "2.1.80")]),
    )
    assert without_team["hint"] == "shut down 2"

    # No hint asked for, no hint invented.
    assert "hint" not in fs.active_teams(
        run_env, teams_dir=teams_dir,
        scan=_scan(live=[("%1", "@grind-c10", "2.1.80")]),
    )


def test_a_scan_that_could_not_look_contributes_no_live_panes(
    run_env, tmp_path
) -> None:
    """`available: False` is "this half could not be checked", and it must not
    be read as "this half is clear" — nor may it invent panes from a listing
    the scan could not make. A machine with no tmux is the common case."""
    teams_dir = tmp_path / "teams"
    teams_dir.mkdir()
    _write_json(run_env, "state.json", {"active_teams": []})
    unavailable = _scan(available=False, live=[("%1", "@grind-c10", "2.1.80")])
    assert fs.active_teams(run_env, teams_dir=teams_dir, scan=unavailable) == {
        "active": False, "teams": [], "live_panes": [],
    }


def test_the_pane_scan_is_the_default_and_never_raises_on_this_machine() -> None:
    """The real reader, driven. It shells out, so the only assertion that holds
    on every machine is the SHAPE — a host with no tmux answers
    `available: False` and a host with one answers with four lists."""
    panes = fs.live_teammate_panes()
    assert set(panes) == {"available", "live", "zombie", "user", "lead"}
    assert isinstance(panes["available"], bool)
    for key in ("live", "zombie", "user"):
        assert isinstance(panes[key], list)


def test_a_pane_pid_that_is_not_a_pid_is_never_handed_to_pgrep() -> None:
    """The PID comes off a tmux format string, so it is text until checked."""
    for value in ("", "   ", "not-a-pid", "12x", "-1"):
        assert fs._pane_pid_has_children(value) is False


# --- the streams-complete read chain ---------------------------------------

MARKER_OF = lambda stream: f".{stream}-complete"
FALLBACK_STREAMS = ("trace", "prove", "test")
INSPECT_PHASES = ("F2", "F5")


def _decision(**fields) -> dict:
    entry = {
        "cycle": 1, "mode": "FULL", "rule": "first_of_phase",
        "required_streams": ["trace", "prove"], "stream_scope": {"prove": "all"},
    }
    entry.update(fields)
    return entry


def _marker(run_dir: Path, stream: str, **counts) -> None:
    body = "".join(f"{k}={v}\n" for k, v in counts.items())
    (run_dir / MARKER_OF(stream)).write_text(body, encoding="utf-8")


def test_marker_counts_reads_the_key_value_body(run_env) -> None:
    """The load path for archives written before the per-cycle roll-up existed."""
    _marker(run_env, "trace", items_checked=40, items_total=42, findings=3)
    assert fs.marker_counts(run_env / MARKER_OF("trace")) == {
        "items_checked": 40, "items_total": 42, "findings": 3,
    }


def test_an_absent_marker_and_an_unreadable_one_are_different_answers(
    run_env,
) -> None:
    """D-098 — None means "no marker"; a PRESENT marker whose numbers cannot be
    read must FAIL the coverage threshold rather than skip it, so it yields the
    zero-counts record. UnicodeDecodeError is a ValueError and an `except
    OSError` around this raised straight through.
    """
    assert fs.marker_counts(run_env / MARKER_OF("trace")) is None

    (run_env / MARKER_OF("trace")).write_bytes(b"items_checked=\xff\xfe4\n")
    assert fs.marker_counts(run_env / MARKER_OF("trace")) == {
        "items_checked": 0, "items_total": 0, "findings": None,
    }

    _marker(run_env, "prove", items_checked="not-a-number")
    assert fs.marker_counts(run_env / MARKER_OF("prove")) == {
        "items_checked": 0, "items_total": 0, "findings": None,
    }


def test_rollup_totals_reads_one_streams_bucket_and_counts_its_tranches(
    run_env,
) -> None:
    """`records` is how many tranches the bucket kept, which is what makes a
    replaced record visible to a reader that only has the totals."""
    _write_json(run_env, "stream-rollup.json", {"cycles": {"2": {"prove": {
        "items_checked": 38, "items_total": 40, "findings": 2,
        "records": [{"items_checked": 20}, {"items_checked": 38}],
    }}}})
    assert fs.rollup_totals(run_env, 2, "prove") == {
        "items_checked": 38, "items_total": 40, "findings": 2, "records": 2,
    }


def test_no_record_for_a_stream_is_none_and_a_recorded_zero_is_not(
    run_env,
) -> None:
    """"Nobody ran it" and "it ran and found nothing" are different answers, and
    only the first may fall back to the marker."""
    _write_json(run_env, "stream-rollup.json", {"cycles": {"2": {"prove": {
        "items_checked": 0, "items_total": 0, "findings": 0,
    }}}})
    assert fs.rollup_totals(run_env, 2, "prove") == {
        "items_checked": 0, "items_total": 0, "findings": 0, "records": 0,
    }
    assert fs.rollup_totals(run_env, 2, "trace") is None
    assert fs.rollup_totals(run_env, 9, "prove") is None


@pytest.mark.parametrize("document", [
    {}, {"cycles": "nope"}, {"cycles": {"2": "nope"}}, {"cycles": {"2": {"prove": 7}}},
], ids=["empty", "cycles-not-a-map", "bucket-not-a-map", "entry-not-a-map"])
def test_rollup_totals_is_total_over_a_malformed_document(run_env, document) -> None:
    _write_json(run_env, "stream-rollup.json", document)
    assert fs.rollup_totals(run_env, 2, "prove") is None


def test_the_prove_roster_is_read_only_from_this_cycles_delta_decision(
    run_env,
) -> None:
    """D-216 — a width is a fact about ONE crossing.

    A roster decided for cycle 2 says nothing about what cycle 1's PROVE owed,
    and a FULL cycle has no roster at all. None means "measure against the
    spec"; an empty LIST means "the recorded roster is empty", which is a
    different instruction.
    """
    _write_json(run_env, "state.json", {"cycle": 2, "inspect_modes": [
        _decision(cycle=2, mode="DELTA", prove_sample=["FR-1", "AC-2", 7]),
    ]})
    assert fs.recorded_prove_roster(run_env, 2, modes=INSPECT_MODES) == ["FR-1", "AC-2"]
    assert fs.recorded_prove_roster(run_env, 1, modes=INSPECT_MODES) is None

    _write_json(run_env, "state.json", {"cycle": 2, "inspect_modes": [
        _decision(cycle=2, mode="FULL", prove_sample=["FR-1"]),
    ]})
    assert fs.recorded_prove_roster(run_env, 2, modes=INSPECT_MODES) is None

    _write_json(run_env, "state.json", {"cycle": 2, "inspect_modes": [
        _decision(cycle=2, mode="DELTA", prove_sample=[]),
    ]})
    assert fs.recorded_prove_roster(run_env, 2, modes=INSPECT_MODES) == []

    _write_json(run_env, "state.json", {"cycle": 2, "inspect_modes": [
        _decision(cycle=2, mode="DELTA"),
    ]})
    assert fs.recorded_prove_roster(run_env, 2, modes=INSPECT_MODES) is None


def _shortfall(run_env, stream, cycle, spec_count=40):
    return fs.coverage_shortfall(
        run_env, stream, cycle, marker_of=MARKER_OF,
        modes=INSPECT_MODES, spec_requirement_count=spec_count,
    )


def test_a_delta_cycle_owes_its_recorded_roster_and_nothing_else(run_env) -> None:
    """D-080 — the DELTA arm, and the reason DELTA was unreachable without it.

    Measuring a DELTA cycle against the whole spec meant a PROVE that checked
    exactly the roster the server itself recorded was reported incomplete
    forever. No 0.95 slack here: the roster is a NAMED, FINITE list the server
    drew, so "which of these did you not check" has an answer.
    """
    _write_json(run_env, "state.json", {"cycle": 5, "inspect_modes": [
        _decision(cycle=5, mode="DELTA", prove_sample=["FR-1", "AC-2", "AC-3"]),
    ]})
    _write_json(run_env, "stream-rollup.json", {"cycles": {"5": {"prove": {
        "items_checked": 3, "items_total": 3,
    }}}})
    assert _shortfall(run_env, "prove", 5) is None

    _write_json(run_env, "stream-rollup.json", {"cycles": {"5": {"prove": {
        "items_checked": 2, "items_total": 3,
    }}}})
    short = _shortfall(run_env, "prove", 5)
    assert short["stream"] == "prove"
    assert short["mode"] == "DELTA"
    assert short["checked"] == 2
    assert short["required"] == 3
    assert short["coverage"] == "67%"
    assert short["roster"] == ["FR-1", "AC-2", "AC-3"]
    assert "recorded DELTA roster names" in short["reason"]


def test_a_full_cycle_measures_prove_against_the_spec_with_five_percent_slack(
    run_env,
) -> None:
    """At FULL the denominator is the whole spec and the 5% is tolerance for a
    matrix that moved under a long stream. The count is PASSED IN: which spec
    is this run's belongs to the module that climbs to it."""
    _write_json(run_env, "state.json", {"cycle": 1})
    _write_json(run_env, "stream-rollup.json", {"cycles": {"1": {"prove": {
        "items_checked": 38, "items_total": 40,
    }}}})
    assert _shortfall(run_env, "prove", 1, spec_count=40) is None

    _write_json(run_env, "stream-rollup.json", {"cycles": {"1": {"prove": {
        "items_checked": 12, "items_total": 40,
    }}}})
    short = _shortfall(run_env, "prove", 1, spec_count=40)
    assert short["checked"] == 12 and short["required"] == 40
    assert short["coverage"] == "30%"
    assert "must be ≥95%" in short["reason"]

    # A spec that parses to nothing has no threshold to fall short of.
    assert _shortfall(run_env, "prove", 1, spec_count=0) is None


def test_trace_measures_against_the_symbols_it_declared(run_env) -> None:
    """TRACE's denominator is its own `items_total`, not the spec's."""
    _write_json(run_env, "state.json", {"cycle": 1})
    _write_json(run_env, "stream-rollup.json", {"cycles": {"1": {"trace": {
        "items_checked": 96, "items_total": 100,
    }}}})
    assert _shortfall(run_env, "trace", 1) is None

    _write_json(run_env, "stream-rollup.json", {"cycles": {"1": {"trace": {
        "items_checked": 50, "items_total": 100,
    }}}})
    short = _shortfall(run_env, "trace", 1)
    assert short["coverage"] == "50%"
    assert "Must check ≥95% of declared symbols" in short["reason"]


def test_a_cycle_with_no_rollup_entry_falls_back_to_the_marker(run_env) -> None:
    """Without the fallback an archive written before the roll-up existed had a
    marker the check counted as PRESENT while the threshold evaluated nothing,
    so 40% coverage passed. "No numbers" means "read them from the marker"."""
    _write_json(run_env, "state.json", {"cycle": 1})
    _marker(run_env, "trace", items_checked=40, items_total=100)
    short = _shortfall(run_env, "trace", 1)
    assert short["checked"] == 40 and short["required"] == 100

    # And a stream with neither has no numbers at all, which is not a shortfall.
    assert _shortfall(run_env, "prove", 1) is None


def _complete(run_env, **overrides):
    kwargs = dict(
        modes=INSPECT_MODES, marker_of=MARKER_OF, sight={"required": False},
        spec_requirement_count=40, inspect_phases=INSPECT_PHASES,
        fallback_streams=FALLBACK_STREAMS,
    )
    kwargs.update(overrides)
    return fs.check_streams_complete(run_env, **kwargs)


def test_the_streams_check_reads_the_recorded_roster_and_reports_the_width(
    run_env,
) -> None:
    """convergence GI-008 / AC-017 — the roster is READ, never recomputed.

    The transition that opened this INSPECT decided which streams it requires
    and recorded them; re-deriving here would let the check disagree with the
    cycle that actually ran. `missing` is a SPACE-JOINED STRING because every
    caller blocks on it being non-empty.
    """
    _write_json(run_env, "state.json", {"phase": "F2", "cycle": 1, "inspect_modes": [
        _decision(cycle=1, mode="DELTA", rule="delta",
                  required_streams=["trace", "prove"],
                  stream_scope={"prove": "sampled"}),
    ]})
    _marker(run_env, "trace", items_checked=100, items_total=100)

    result = _complete(run_env)
    assert result["required"] == ["trace", "prove"]
    assert result["missing"] == "prove"
    assert result["complete"] is False
    assert result["inspect_mode"] == "DELTA"
    assert result["inspect_rule"] == "delta"
    assert result["stream_scope"] == {"prove": "sampled"}

    _marker(run_env, "prove", items_checked=40, items_total=40)
    assert _complete(run_env)["complete"] is True
    assert _complete(run_env)["missing"] == ""


def test_a_stream_that_recorded_but_fell_short_is_missing_and_detailed(
    run_env,
) -> None:
    """process-fixes CT-003 — the thresholds are evaluated HERE, once per cycle,
    at the one point where every tranche of a partially-delivered stream is
    in hand. The stream is reported in `missing` so existing callers keep
    blocking on it, and detailed in `shortfalls` so the lead learns which
    number was short."""
    _write_json(run_env, "state.json", {"phase": "F2", "cycle": 1, "inspect_modes": [
        _decision(cycle=1, required_streams=["trace"]),
    ]})
    _marker(run_env, "trace", items_checked=50, items_total=100)
    _write_json(run_env, "stream-rollup.json", {"cycles": {"1": {"trace": {
        "items_checked": 50, "items_total": 100,
    }}}})

    result = _complete(run_env)
    assert result["complete"] is False
    assert result["missing"] == "trace"
    assert [s["stream"] for s in result["shortfalls"]] == ["trace"]
    assert result["shortfalls"][0]["coverage"] == "50%"


def test_an_unrecorded_width_inside_an_inspect_blocks_with_the_shapers_words(
    run_env,
) -> None:
    """D-117 — an unrecorded width is not FULL width.

    `missing` carries the sentinel `inspect_mode` so every existing caller
    blocks without being taught a new key. The refusal's own sentences are the
    SHAPER's — it names the transitions that record a width and the remedy,
    which is door protocol — so it is passed in rather than spelled here.
    """
    _write_json(run_env, "state.json", {"phase": "F2", "cycle": 3})
    shaper = lambda _run_dir: {"reason": "no width for cycle 3", "hint": "cross a boundary"}

    result = _complete(run_env, unrecorded_width_problem=shaper)
    assert result["complete"] is False
    assert result["missing"] == "inspect_mode"
    assert result["required"] == []
    assert result["unrecorded_width"] is True
    assert result["reason"] == "no width for cycle 3"
    assert result["hint"] == "cross a boundary"


def test_the_width_arm_is_scoped_to_a_run_that_is_in_an_inspect(run_env) -> None:
    """A run in no INSPECT has no INSPECT whose width could be missing — and
    this is also the plain "did these markers record" query, which a run that
    never entered F2 may ask."""
    _write_json(run_env, "state.json", {"phase": "F3", "cycle": 3})
    shaper = lambda _run_dir: {"reason": "r", "hint": "h"}
    assert "unrecorded_width" not in _complete(run_env, unrecorded_width_problem=shaper)


def test_a_reporting_caller_omits_the_shaper_and_the_arm_is_skipped(
    run_env,
) -> None:
    """The two doors that REFUSE ask the shaper themselves; a caller that only
    reports passes none, and then gets the roster answer rather than a refusal
    it has no business making."""
    _write_json(run_env, "state.json", {"phase": "F2", "cycle": 3})
    result = _complete(run_env)
    assert "unrecorded_width" not in result
    assert result["required"] == list(FALLBACK_STREAMS)


def test_a_recorded_mode_with_no_usable_roster_rebuilds_the_manifest_answer(
    run_env,
) -> None:
    """The width IS recorded, so this is not the D-117 hole: the roster is
    rebuilt from the run's own manifest exactly as it was before the width
    existed. `sight` is the already-computed answer, so this makes no second
    decision about whether the run has a browsable UI."""
    _write_json(run_env, "state.json", {"phase": "F2", "cycle": 1, "inspect_modes": [
        _decision(cycle=1, mode="FULL", rule="first_of_phase", required_streams="nope"),
    ]})
    (run_env / "castings").mkdir(exist_ok=True)
    _write_json(run_env, "castings/manifest.json", {"target_url": ""})

    assert _complete(run_env)["required"] == ["trace", "prove", "test"]
    assert _complete(run_env, sight={"required": True})["required"] == [
        "trace", "prove", "test", "sight",
    ]

    _write_json(run_env, "castings/manifest.json", {"target_url": "http://localhost:3000"})
    assert _complete(run_env, sight={"required": True})["required"] == [
        "trace", "prove", "test", "sight", "probe",
    ]
    assert _complete(run_env)["inspect_mode"] == "FULL"


def test_the_streams_check_is_total_over_an_empty_run(run_env) -> None:
    """No state, no manifest, no markers. Nothing may raise: this is consulted
    from inside a transition whose failure takes the door down."""
    result = _complete(run_env)
    assert result["complete"] is False
    assert result["required"] == list(FALLBACK_STREAMS)
    assert result["missing"] == "trace prove test"
    assert result["inspect_mode"] == "" and result["stream_scope"] == {}
