"""GI-024 / FR-008 / AC-011 / OT-010 — the consolidated run-table readers.

A-026: "All of the survey's duplication inventory, into `foundry_state`
readers (Recommended)."

OT-010 states the property this module pins: "Spend, inspect modes, unreported
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

from foundry_mcp.schemas.vocab import (
    ESCALATION_EXIT_REASONS,
    ESCALATION_STATUSES,
    INSPECT_MODES,
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
# The stream-coverage rows (CT-003 / AC-030 / OT-028).
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
    """CT-003 / ST-009 — a second record for one (stream, cycle) REPLACES the first.

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
    """FR-054 — a bucket that reads above 100% is what daring-orca contains.

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
    presence check built a set of every trimmed line. Both halves of GI-006 now
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

    GI-006 licenses the lead to APPEND, so the match is on the whole trimmed
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
# `prove_is_clean` (FR-020 / AC-025).
# ---------------------------------------------------------------------------


def test_prove_is_clean_on_a_full_clean_tranche_set() -> None:
    """The derived answer: 0 findings AND >=95% requirement coverage."""
    assert fs.prove_is_clean(
        totals={"items_checked": 68, "items_total": 71, "findings": 0},
        spec_requirement_count=71,
    ) is True


def test_prove_is_clean_refuses_a_spec_that_parses_to_zero_requirements() -> None:
    """FR-020 / AC-025 — the >=95% check used to be SKIPPED at zero.

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
