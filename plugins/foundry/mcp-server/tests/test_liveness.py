"""Foundry-Liveness — per-agent progress ages against a stall threshold.

This module covers the READ half of the FR-015 loop: given progress ledgers
under ``foundry-archive/{run}/progress/``, ``foundry_liveness`` must tell the
lead which agents are advancing, which are silent, and which are alive but
stuck. ``test_spawn_progress.py`` covers the WRITE half (the protocol block the
spawn output hands to the agent) and closes the loop end to end.

What is pinned here
-------------------
- **AC-021 / OT-010** — an agent past the stall threshold is flagged with a
  DIFFERENT status from one making progress, and the report carries each
  agent's last-progress age. Both sides of the threshold boundary are
  exercised against the same ledger so the test cannot pass by accident of a
  wrongly-signed comparison.
- **A-025 / the "heartbeats prove liveness, not progress" ruling** — a ledger
  whose timestamps keep advancing while ``step`` never changes reports as
  ``no_progress``, distinctly from both ``progressing`` and ``stalled``. This
  is the case a bare heartbeat cannot express, and it is why every line
  carries ``step``. Without a test here, the ledger could silently degrade
  into a heartbeat and every other assertion in this file would still pass.
- **CT-004** — all three input forms: no identifier (whole roster), an
  identifier (that agent only), and a run with no ledger at all (empty roster,
  ``ok: True``, NOT an error).
- **House rule 1** — the run-directory guard and named-refusal shape.
- **FR-025** — the threshold is a named constant with a derivation, not a
  literal, and is overridable per call.

Why relative timestamps rather than a frozen clock
--------------------------------------------------
Every ledger below is written as an offset from ``datetime.now(timezone.utc)``
at test time, mirroring ``test_orchestrator_gates.py``'s stall-watchdog tests.
That keeps the production code free of a test-only clock seam: the thing under
test reads real wall-clock time exactly as it will in a run. Offsets are
chosen far enough from each threshold that the milliseconds of drift between
writing the file and reading it back cannot flip an assertion.

Run with the rest of the suite: ``uv run --with pytest pytest`` from
``plugins/foundry/mcp-server``.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from foundry_mcp.tools import foundry_spawn as fs
from foundry_mcp.tools import foundry_state


# --------------------------------------------------------------------------- #
# Fixtures & helpers
# --------------------------------------------------------------------------- #


@pytest.fixture
def run_env(tmp_path):
    """Activate a foundry run under tmp_path; yield (project_root, fdir).

    Mirrors ``test_orchestrator_gates.run_env`` minus the ``_check_active_teams``
    patch, which liveness never consults — it reads the progress directory and
    nothing else, so there is no ambient tmux or ~/.claude/teams state to
    isolate it from.
    """
    project_root = tmp_path
    run_name = "c4-liveness-run"
    fdir = project_root / "foundry-archive" / run_name
    fdir.mkdir(parents=True, exist_ok=True)

    foundry_state.set_active_run(run_name)
    try:
        yield str(project_root), fdir
    finally:
        foundry_state.clear_active_run()


def _write_ledger(fdir: Path, agent: str, lines: list[tuple[float, str, str]]) -> Path:
    """Write one agent's ledger from (age_seconds, phase, step) tuples.

    ``age_seconds`` is how long ago the line was written, so a test states
    "this line is 20 minutes old" rather than computing absolute times.
    """
    now = datetime.now(timezone.utc)
    pdir = fdir / "progress"
    pdir.mkdir(parents=True, exist_ok=True)
    path = pdir / f"{agent}.jsonl"
    body = "".join(
        json.dumps(
            {
                "timestamp": (now - timedelta(seconds=age)).isoformat(),
                "phase": phase,
                "step": step,
            }
        )
        + "\n"
        for age, phase, step in lines
    )
    path.write_text(body, encoding="utf-8")
    return path


def _append_terminal_line(fdir: Path, agent: str, age_seconds: float, step: str) -> None:
    """Append the ``"done": true`` line an agent writes when it finishes."""
    path = fdir / "progress" / f"{agent}.jsonl"
    moment = datetime.now(timezone.utc) - timedelta(seconds=age_seconds)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(
            json.dumps(
                {
                    "timestamp": moment.isoformat(),
                    "phase": "cast",
                    "step": step,
                    "done": True,
                }
            )
            + "\n"
        )


def _enter_inspect(fdir: Path, minutes_ago: float, stream_skips=None) -> None:
    """Put the run in F2 as of ``minutes_ago``, with an optional skip list.

    Mirrors the two fields ``_missing_stream_records`` reads out of a real
    ``state.json``: the current phase, and when that phase was entered.
    """
    entered = datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)
    (fdir / "state.json").write_text(
        json.dumps(
            {
                "phase": "F2",
                "phase_times": {"F2": {"started_at": entered.isoformat()}},
            }
        ),
        encoding="utf-8",
    )
    if stream_skips is not None:
        castings = fdir / "castings"
        castings.mkdir(parents=True, exist_ok=True)
        (castings / "manifest.json").write_text(
            json.dumps({"stream_skips": stream_skips}), encoding="utf-8"
        )


def _by_agent(result: dict) -> dict[str, dict]:
    return {record["agent"]: record for record in result["agents"]}


# --------------------------------------------------------------------------- #
# FR-025 — the threshold is derived, named, and overridable
# --------------------------------------------------------------------------- #


def test_threshold_and_cadence_are_named_constants_not_literals() -> None:
    """FR-025: both live at module top so the derivation has somewhere to sit."""
    assert fs.PROGRESS_CADENCE_SECONDS == 300
    assert fs.STALL_THRESHOLD_SECONDS == 900


def test_threshold_is_not_the_lead_watchdog_constant() -> None:
    """The 180s lead watchdog is a SHAPE to copy, not a constant to copy.

    It measures a lead's tool-call cadence, where three quiet minutes is
    anomalous. A teammate reading ten files before writing a line would trip
    it constantly, so reusing 180 here would make the tool cry wolf.
    """
    assert fs.STALL_THRESHOLD_SECONDS != 180


def test_threshold_sits_between_the_measured_batch_extremes() -> None:
    """The derivation, asserted rather than only claimed in a comment.

    From grand-vulture's spawns.log: the shortest genuine working batch is
    1.1 min and the median is 56 min. A usable threshold must be far enough
    above the short batch that real work never trips it, and far enough below
    the median that a dead agent is caught mid-batch rather than after it.
    """
    shortest_real_batch = 1.1 * 60
    median_batch = 56.0 * 60
    assert fs.STALL_THRESHOLD_SECONDS > shortest_real_batch * 5
    assert fs.STALL_THRESHOLD_SECONDS < median_batch / 2


def test_threshold_is_a_multiple_of_the_cadence() -> None:
    """Three consecutive missed lines, not one slow file read."""
    assert fs.STALL_THRESHOLD_SECONDS == 3 * fs.PROGRESS_CADENCE_SECONDS


def test_status_vocabulary_is_closed_and_complete() -> None:
    """House rule 3 — a frozenset, and every status the code can emit is in it."""
    assert isinstance(fs.PROGRESS_STATUSES, frozenset)
    assert fs.PROGRESS_STATUSES == {
        "progressing", "no_progress", "stalled", "unknown", "done", "no_ledger"
    }
    assert fs.NEEDS_ATTENTION_STATUSES < fs.PROGRESS_STATUSES
    assert fs.STATUS_PROGRESSING not in fs.NEEDS_ATTENTION_STATUSES


def test_neither_healthy_status_calls_for_attention() -> None:
    """D-022: DONE joins PROGRESSING as a status that is not a call to action.

    Putting DONE in the attention set would rebuild the exact defect the
    status was added to fix — a roster where finished work accumulates until
    the lead stops reading it.
    """
    assert fs.STATUS_DONE not in fs.NEEDS_ATTENTION_STATUSES
    assert fs.NEEDS_ATTENTION_STATUSES == {
        "no_progress", "stalled", "unknown", "no_ledger"
    }


def test_the_inspect_stream_roster_is_derived_from_the_canonical_vocabulary() -> None:
    """The stream ids are vocab.py's to own; this module selects, never re-declares.

    A drifted spelling here would produce a ledger filename no stream agent
    ever writes, so the roster would report four permanently-missing agents
    that are in fact working.
    """
    from foundry_mcp.schemas import vocab

    assert set(fs.INSPECT_STREAM_AGENT_IDS) <= set(vocab.STREAM_WIRE_IDS)
    # GI-001's four defect-filing streams, and only those.
    assert set(fs.INSPECT_STREAM_AGENT_IDS) == {
        "trace", "flow_trace", "prove", "research_audit"
    }


# --------------------------------------------------------------------------- #
# House rule 1 — run-directory guard and refusal shape
# --------------------------------------------------------------------------- #


def test_refuses_when_no_run_is_active(tmp_path) -> None:
    foundry_state.clear_active_run()
    result = fs.foundry_liveness(project_root=str(tmp_path))
    assert result["ok"] is False
    assert "No active foundry run" in result["error"]
    assert "Foundry-Init" in result["hint"]


def test_refuses_when_the_run_directory_is_missing(tmp_path) -> None:
    foundry_state.set_active_run("run-that-was-deleted")
    try:
        result = fs.foundry_liveness(project_root=str(tmp_path))
    finally:
        foundry_state.clear_active_run()
    assert result["ok"] is False
    assert "not found" in result["error"]
    assert "run-that-was-deleted" in result["hint"]


@pytest.mark.parametrize("bad", [0, -1, -900, "soon", [], True])
def test_refuses_a_non_positive_or_non_numeric_threshold(run_env, bad) -> None:
    """A named refusal that states the offending value AND the default.

    ``True`` is in the list deliberately: ``isinstance(True, int)`` holds in
    Python, so a bool slipping through a JSON-Schema gap would silently become
    a 1-second threshold and report every agent as stalled.
    """
    project_root, _fdir = run_env
    result = fs.foundry_liveness(stall_seconds=bad, project_root=project_root)
    assert result["ok"] is False
    assert "stall_seconds" in result["error"]
    assert str(fs.STALL_THRESHOLD_SECONDS) in result["hint"]


def test_never_raises_across_the_boundary(run_env) -> None:
    """A torn ledger is data, not an exception. Nothing here may propagate."""
    project_root, fdir = run_env
    pdir = fdir / "progress"
    pdir.mkdir(parents=True)
    (pdir / "casting-9.jsonl").write_text(
        '{"timestamp": "2026-08-31T00:00:00+00:00", "phase": "cast", "step": "ok"}\n'
        "{not json at all\n"
        '["a list, not an object"]\n'
        '{"no_timestamp": true}\n'
        '{"timestamp": "not-a-date", "step": "x"}\n',
        encoding="utf-8",
    )
    result = fs.foundry_liveness(project_root=project_root)
    assert result["ok"] is True
    # The one good line survives; the four bad ones are skipped, not fatal.
    assert _by_agent(result)["casting-9"]["lines"] == 1


# --------------------------------------------------------------------------- #
# CT-004 — the three input forms
# --------------------------------------------------------------------------- #


def test_no_ledger_at_all_is_an_empty_roster_not_an_error(run_env) -> None:
    """CT-004 + truth 2: an unstarted ledger is a normal early-run state.

    The progress directory is created lazily by the first agent to write, so
    on a fresh run it does not exist. Reporting that as a failure would make
    the lead's first liveness query look like a broken tool.
    """
    project_root, fdir = run_env
    assert not (fdir / "progress").exists()

    result = fs.foundry_liveness(project_root=project_root)

    assert result["ok"] is True
    assert result["agents"] == []
    assert result["needs_attention"] == []
    assert "progress" in result["note"]


def test_no_identifier_returns_every_agent_in_the_run(run_env) -> None:
    project_root, fdir = run_env
    _write_ledger(fdir, "casting-1", [(30, "cast", "writing tests")])
    _write_ledger(fdir, "casting-2", [(30, "cast", "read floor")])
    _write_ledger(fdir, "casting-3", [(30, "cast", "committing")])

    result = fs.foundry_liveness(project_root=project_root)

    assert result["ok"] is True
    assert [r["agent"] for r in result["agents"]] == [
        "casting-1", "casting-2", "casting-3"
    ]


def test_an_identifier_returns_only_that_agent(run_env) -> None:
    project_root, fdir = run_env
    _write_ledger(fdir, "casting-1", [(30, "cast", "writing tests")])
    _write_ledger(fdir, "casting-2", [(30, "cast", "read floor")])

    result = fs.foundry_liveness(agent="casting-2", project_root=project_root)

    assert result["ok"] is True
    assert len(result["agents"]) == 1
    assert result["agents"][0]["agent"] == "casting-2"
    assert result["agents"][0]["step"] == "read floor"


def test_an_unknown_identifier_is_a_named_refusal_listing_the_known(run_env) -> None:
    """Asking about a specific worker that has no ledger is a caller error.

    Distinct from the empty-roster case above: there the caller asked "who is
    out there", here the caller asserted a worker exists. Returning an empty
    success would let a typo read as "that agent is fine".
    """
    project_root, fdir = run_env
    _write_ledger(fdir, "casting-1", [(30, "cast", "writing tests")])

    result = fs.foundry_liveness(agent="casting-99", project_root=project_root)

    assert result["ok"] is False
    assert "casting-99" in result["error"]
    assert "casting-1" in result["hint"]


def test_an_identifier_on_a_run_with_no_ledgers_names_the_expected_path(run_env) -> None:
    """The refusal has to be actionable when there is no roster to list."""
    project_root, _fdir = run_env
    result = fs.foundry_liveness(agent="casting-4", project_root=project_root)
    assert result["ok"] is False
    assert "progress/casting-4.jsonl" in result["hint"]
    assert "progress_protocol" in result["hint"]


# --------------------------------------------------------------------------- #
# AC-021 / OT-010 — stalled is flagged distinctly from progressing
# --------------------------------------------------------------------------- #


def test_a_recent_line_reports_progressing_with_its_age(run_env) -> None:
    """AC-021: the report carries the agent's last-progress age."""
    project_root, fdir = run_env
    _write_ledger(fdir, "casting-4", [(120, "grind", "reading D-3"), (20, "grind", "fixing D-3")])

    record = _by_agent(fs.foundry_liveness(project_root=project_root))["casting-4"]

    assert record["status"] == fs.STATUS_PROGRESSING
    assert 15 <= record["last_progress_age_seconds"] <= 40
    assert record["phase"] == "grind"
    assert record["step"] == "fixing D-3"
    assert record["lines"] == 2


def test_a_silent_agent_reports_stalled(run_env) -> None:
    """OT-010: past the threshold with no line at all — the no-heartbeat case."""
    project_root, fdir = run_env
    _write_ledger(fdir, "casting-4", [(2400, "cast", "read floor")])

    record = _by_agent(fs.foundry_liveness(project_root=project_root))["casting-4"]

    assert record["status"] == fs.STATUS_STALLED
    assert record["last_progress_age_seconds"] >= fs.STALL_THRESHOLD_SECONDS


def test_stalled_and_progressing_are_distinct_statuses_in_one_report(run_env) -> None:
    """OT-010 stated as the lead actually consumes it: one query, two verdicts.

    A tool that reported both agents identically would satisfy every
    single-agent assertion above and still be useless.
    """
    project_root, fdir = run_env
    _write_ledger(fdir, "casting-live", [(20, "cast", "writing tests")])
    _write_ledger(fdir, "casting-dead", [(3600, "cast", "read floor")])

    result = fs.foundry_liveness(project_root=project_root)
    records = _by_agent(result)

    assert records["casting-live"]["status"] != records["casting-dead"]["status"]
    assert records["casting-live"]["status"] == fs.STATUS_PROGRESSING
    assert records["casting-dead"]["status"] == fs.STATUS_STALLED
    assert result["needs_attention"] == ["casting-dead"]


def test_the_threshold_boundary_decides_the_status(run_env) -> None:
    """The same ledger, read either side of the boundary, flips the verdict.

    This is what makes the comparison itself the thing under test: a
    wrongly-signed or off-by-a-unit threshold cannot satisfy both halves.
    """
    project_root, fdir = run_env
    _write_ledger(fdir, "casting-4", [(100, "cast", "read floor")])

    at_or_past = fs.foundry_liveness(stall_seconds=100, project_root=project_root)
    inside = fs.foundry_liveness(stall_seconds=300, project_root=project_root)

    assert _by_agent(at_or_past)["casting-4"]["status"] == fs.STATUS_STALLED
    assert _by_agent(inside)["casting-4"]["status"] == fs.STATUS_PROGRESSING


def test_the_override_is_echoed_so_the_lead_sees_what_was_applied(run_env) -> None:
    project_root, fdir = run_env
    _write_ledger(fdir, "casting-4", [(20, "cast", "read floor")])

    default = fs.foundry_liveness(project_root=project_root)
    overridden = fs.foundry_liveness(stall_seconds=60, project_root=project_root)

    assert default["stall_threshold_seconds"] == fs.STALL_THRESHOLD_SECONDS
    assert overridden["stall_threshold_seconds"] == 60
    assert default["progress_cadence_seconds"] == fs.PROGRESS_CADENCE_SECONDS


# --------------------------------------------------------------------------- #
# A-025 — heartbeat-but-no-progress, the case a bare ping cannot express
# --------------------------------------------------------------------------- #


def test_fresh_lines_on_an_unchanged_step_report_no_progress(run_env) -> None:
    """The agent keeps writing but has not moved: alive, not advancing.

    Every line here is a valid heartbeat, and the most recent one is 10
    seconds old — any liveness scheme that only timestamps a ping would call
    this agent healthy. It has been on the same step for 40 minutes.
    """
    project_root, fdir = run_env
    _write_ledger(
        fdir,
        "casting-4",
        [
            (2400, "grind", "reading defect D-17"),
            (1200, "grind", "reading defect D-17"),
            (600, "grind", "reading defect D-17"),
            (10, "grind", "reading defect D-17"),
        ],
    )

    record = _by_agent(fs.foundry_liveness(project_root=project_root))["casting-4"]

    assert record["status"] == fs.STATUS_NO_PROGRESS
    # The heartbeat is fresh; the progress is not. Two different numbers is
    # the entire point of carrying `step` on every line.
    assert record["last_line_age_seconds"] < fs.STALL_THRESHOLD_SECONDS
    assert record["last_progress_age_seconds"] >= 2400


def test_no_progress_is_distinct_from_both_other_live_statuses(run_env) -> None:
    """Three agents, three verdicts, one query."""
    project_root, fdir = run_env
    _write_ledger(fdir, "casting-a", [(1200, "grind", "step one"), (10, "grind", "step two")])
    _write_ledger(fdir, "casting-b", [(1200, "grind", "step one"), (10, "grind", "step one")])
    _write_ledger(fdir, "casting-c", [(1200, "grind", "step one")])

    records = _by_agent(fs.foundry_liveness(project_root=project_root))

    assert records["casting-a"]["status"] == fs.STATUS_PROGRESSING
    assert records["casting-b"]["status"] == fs.STATUS_NO_PROGRESS
    assert records["casting-c"]["status"] == fs.STATUS_STALLED
    assert len({r["status"] for r in records.values()}) == 3


def test_progress_age_dates_the_step_change_not_the_last_restatement(run_env) -> None:
    """``progress_since`` marks when the CURRENT step was first reached."""
    project_root, fdir = run_env
    _write_ledger(
        fdir,
        "casting-4",
        [
            (900, "cast", "read floor"),
            (600, "cast", "writing the handler"),
            (300, "cast", "writing the handler"),
            (30, "cast", "writing the handler"),
        ],
    )

    record = _by_agent(fs.foundry_liveness(project_root=project_root))["casting-4"]

    # Not 30 (the last restatement) and not 900 (a step it has left behind).
    assert 580 <= record["last_progress_age_seconds"] <= 620
    assert record["last_line_age_seconds"] <= 60
    assert record["step"] == "writing the handler"


def test_a_ledger_with_no_parseable_line_reports_unknown(run_env) -> None:
    """We cannot date progress that was never recorded — so we do not claim to."""
    project_root, fdir = run_env
    pdir = fdir / "progress"
    pdir.mkdir(parents=True)
    (pdir / "casting-4.jsonl").write_text("{partial write, no newline", encoding="utf-8")

    record = _by_agent(fs.foundry_liveness(project_root=project_root))["casting-4"]

    assert record["status"] == fs.STATUS_UNKNOWN
    assert record["last_progress_age_seconds"] is None
    assert "no parseable progress line" in record["detail"]


# --------------------------------------------------------------------------- #
# Ledger-shape tolerance
# --------------------------------------------------------------------------- #


def test_a_naive_timestamp_is_read_as_utc_rather_than_discarded(run_env) -> None:
    """An agent that forgot the offset still gets counted, not silently dropped."""
    project_root, fdir = run_env
    naive = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=30)
    pdir = fdir / "progress"
    pdir.mkdir(parents=True)
    (pdir / "casting-4.jsonl").write_text(
        json.dumps({"timestamp": naive.isoformat(), "phase": "cast", "step": "x"}) + "\n",
        encoding="utf-8",
    )

    record = _by_agent(fs.foundry_liveness(project_root=project_root))["casting-4"]

    assert record["status"] == fs.STATUS_PROGRESSING
    assert record["last_progress_age_seconds"] <= 60


def test_only_jsonl_files_are_treated_as_ledgers(run_env) -> None:
    """A stray note in progress/ must not become a phantom agent."""
    project_root, fdir = run_env
    _write_ledger(fdir, "casting-4", [(20, "cast", "x")])
    (fdir / "progress" / "README.md").write_text("scratch\n", encoding="utf-8")

    result = fs.foundry_liveness(project_root=project_root)

    assert [r["agent"] for r in result["agents"]] == ["casting-4"]


# --------------------------------------------------------------------------- #
# OT-010 — the distinction, rendered as the lead actually sees it
# --------------------------------------------------------------------------- #


def test_demo_report_renders_the_three_statuses_side_by_side(run_env) -> None:
    """OT-010 is an OBSERVABLE truth, so this one prints what the lead gets.

    Run it with ``-s`` to read the report:

        uv run --with pytest pytest tests/test_liveness.py -q -s -k demo_report

    Ages are printed in whole minutes. Every ledger offset below is an exact
    multiple of 60s and the real age is always that offset plus a few
    milliseconds, so the floor division is stable run to run — which is what
    lets this double as re-executable evidence rather than a one-off print.

    The fourth row is D-058. ``casting-delta`` was dispatched an hour ago and
    has written nothing at all, so it has no ledger to read and every
    ledger-derived age is honestly blank — and it is on the report anyway,
    because the roster is built from what the run dispatched. Before the fix
    that row did not exist and ``needs_attention`` named three agents, none of
    them the one that was actually dead.
    """
    project_root, fdir = run_env
    _enter_phase(fdir, "F3", minutes_ago=300)
    _dispatch(fdir, "delta", seconds_ago=3600)
    _write_ledger(
        fdir,
        "casting-alpha",
        [(2400, "grind", "read floor"), (60, "grind", "writing the handler")],
    )
    _write_ledger(
        fdir,
        "casting-beta",
        [
            (2400, "grind", "reading defect D-17"),
            (1200, "grind", "reading defect D-17"),
            (600, "grind", "reading defect D-17"),
            (60, "grind", "reading defect D-17"),
        ],
    )
    _write_ledger(fdir, "casting-gamma", [(3600, "grind", "read floor")])

    result = fs.foundry_liveness(project_root=project_root)

    rows = [
        "",
        f"Foundry-Liveness — stall threshold {result['stall_threshold_seconds']}s, "
        f"progress cadence {result['progress_cadence_seconds']}s",
        "",
        f"{'agent':<16}{'status':<14}{'last_progress':>14}{'last_line':>11}"
        f"{'dispatched':>12}  step",
        f"{'-' * 15:<16}{'-' * 13:<14}{'-' * 13:>14}{'-' * 9:>11}"
        f"{'-' * 10:>12}  {'-' * 20}",
    ]

    def _mins(seconds: int | None) -> str:
        """Whole minutes, or a dash when the ledger cannot date it.

        A blank is the honest rendering for an agent with no ledger: printing
        0m would assert a last line it never wrote.
        """
        return "—" if seconds is None else f"{seconds // 60}m"

    for record in result["agents"]:
        rows.append(
            f"{record['agent']:<16}"
            f"{record['status']:<14}"
            f"{_mins(record['last_progress_age_seconds']):>13}"
            f"{_mins(record['last_line_age_seconds']):>11}"
            f"{_mins(record.get('dispatched_age_seconds')):>12}"
            f"  {record['step'] or ''}"
        )
    rows += ["", f"needs_attention: {result['needs_attention']}", ""]

    out = "\n".join(rows)
    print(out)

    # The report distinguishes all four, and says so in plain text.
    assert "progressing" in out
    assert "no_progress" in out
    assert "stalled" in out
    assert "no_ledger" in out
    # beta's heartbeat is as fresh as alpha's; only its PROGRESS age differs.
    assert re.search(r"casting-beta\s+no_progress\s+40m\s+1m", out)
    assert re.search(r"casting-alpha\s+progressing\s+1m\s+1m", out)
    # delta has no ledger to date, so both ledger ages are blank while the
    # DISPATCH age carries the whole signal — the shape D-058 added.
    assert re.search(r"casting-delta\s+no_ledger\s+—\s+—\s+60m", out)
    assert result["needs_attention"] == [
        "casting-beta", "casting-delta", "casting-gamma"
    ]


def test_the_record_names_the_ledger_it_was_read_from(run_env) -> None:
    """So a lead acting on a stall can go read the file without guessing."""
    project_root, fdir = run_env
    _write_ledger(fdir, "casting-4", [(20, "cast", "x")])

    record = _by_agent(fs.foundry_liveness(project_root=project_root))["casting-4"]

    assert record["ledger"] == "foundry-archive/c4-liveness-run/progress/casting-4.jsonl"
    assert (Path(project_root) / record["ledger"]).exists()


# --------------------------------------------------------------------------- #
# D-022 — a finished agent is DONE, not eternally STALLED
# --------------------------------------------------------------------------- #


def test_a_finished_agent_reports_done_however_long_ago_it_finished(run_env) -> None:
    """D-022: the defect in one assertion.

    Finishing does not take an agent off the watchlist — it just stops the
    ledger. Without a terminal branch this agent's last line ages past the
    threshold and it reports `stalled` for the rest of the run.
    """
    project_root, fdir = run_env
    _write_ledger(fdir, "casting-4", [(9000, "cast", "self-check run")])
    _append_terminal_line(fdir, "casting-4", 8700, "committed 9f21ac3")

    record = _by_agent(fs.foundry_liveness(project_root=project_root))["casting-4"]

    assert record["status"] == fs.STATUS_DONE
    # The age is still reported — DONE says "finished", not "unknown".
    assert record["last_progress_age_seconds"] > fs.STALL_THRESHOLD_SECONDS


def test_a_finished_agent_does_not_fill_needs_attention(run_env) -> None:
    """D-022's actual cost: the watchlist silting up with completed work."""
    project_root, fdir = run_env
    for casting in ("casting-1", "casting-2", "casting-3"):
        _write_ledger(fdir, casting, [(9000, "cast", "self-check run")])
        _append_terminal_line(fdir, casting, 8700, "committed")
    _write_ledger(fdir, "casting-4", [(9000, "cast", "reading the spec")])

    result = fs.foundry_liveness(project_root=project_root)

    # Only the agent that really went silent is on the list.
    assert result["needs_attention"] == ["casting-4"]
    assert _by_agent(result)["casting-4"]["status"] == fs.STATUS_STALLED


def test_done_outranks_every_age_check(run_env) -> None:
    """Terminal is terminal — neither stall nor step-repeat can override it."""
    project_root, fdir = run_env
    # A ledger that would otherwise read no_progress: fresh lines, frozen step.
    _write_ledger(
        fdir,
        "casting-4",
        [(3000, "cast", "fixing D-021"), (60, "cast", "fixing D-021")],
    )
    _append_terminal_line(fdir, "casting-4", 30, "fixing D-021")

    record = _by_agent(fs.foundry_liveness(project_root=project_root))["casting-4"]
    assert record["status"] == fs.STATUS_DONE


def test_a_resumed_agent_leaves_done_behind(run_env) -> None:
    """A casting re-dispatched for GRIND writes into the SAME ledger.

    Terminal is a property of the LAST line, not a latch on the file, or a
    teammate that finished CAST could never be watched again in GRIND.
    """
    project_root, fdir = run_env
    _write_ledger(fdir, "casting-4", [(9000, "cast", "self-check run")])
    _append_terminal_line(fdir, "casting-4", 8700, "committed 9f21ac3")
    # Re-dispatched, and now silent again.
    _append_line = fdir / "progress" / "casting-4.jsonl"
    _append_line.write_text(
        _append_line.read_text(encoding="utf-8")
        + json.dumps(
            {
                "timestamp": (
                    datetime.now(timezone.utc) - timedelta(seconds=4000)
                ).isoformat(),
                "phase": "grind",
                "step": "reading defect D-021",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    record = _by_agent(fs.foundry_liveness(project_root=project_root))["casting-4"]
    assert record["status"] == fs.STATUS_STALLED
    assert record["phase"] == "grind"


def test_a_non_boolean_done_value_does_not_retire_an_agent(run_env) -> None:
    """A ledger is hand-written prose; only a real ``true`` is terminal."""
    project_root, fdir = run_env
    path = fdir / "progress"
    path.mkdir(parents=True, exist_ok=True)
    (path / "casting-4.jsonl").write_text(
        json.dumps(
            {
                "timestamp": (
                    datetime.now(timezone.utc) - timedelta(seconds=9000)
                ).isoformat(),
                "phase": "cast",
                "step": "waiting on a build",
                "done": "not yet",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    record = _by_agent(fs.foundry_liveness(project_root=project_root))["casting-4"]
    assert record["status"] == fs.STATUS_STALLED


def test_the_protocol_block_tells_agents_to_write_the_terminal_line() -> None:
    """The write half of D-022 — the read half is worthless without it."""
    block = fs._progress_protocol_block("some-run", "casting-4")
    assert '"done": true' in block
    assert fs.STATUS_DONE in block
    # And it says WHY, because an agent that thinks this is bookkeeping skips it.
    assert "needs_attention" in block
    assert fs.STATUS_STALLED in block


# --------------------------------------------------------------------------- #
# D-021 — the INSPECT stream agents are visible to liveness
# --------------------------------------------------------------------------- #


def test_expected_stream_agents_with_no_ledger_are_reported(run_env) -> None:
    """D-021: the four stream agents were absent from the roster entirely.

    They are spawned from the F2 roster rather than from either tool in this
    module, so nothing hands them the protocol and a glob over progress/ has
    no row for them at all — silence indistinguishable from success.
    """
    project_root, fdir = run_env
    _enter_inspect(fdir, minutes_ago=40)

    result = fs.foundry_liveness(project_root=project_root)

    records = _by_agent(result)
    assert records["trace"]["status"] == fs.STATUS_NO_LEDGER
    assert records["prove"]["status"] == fs.STATUS_NO_LEDGER
    assert "trace" in result["needs_attention"]
    assert "prove" in result["needs_attention"]


def test_a_missing_stream_record_carries_the_block_that_fixes_it(run_env) -> None:
    """Reporting the gap is only half of it — the remedy travels with it."""
    project_root, fdir = run_env
    _enter_inspect(fdir, minutes_ago=40)

    record = _by_agent(fs.foundry_liveness(project_root=project_root))["trace"]

    block = record["progress_protocol"]
    # The block names the exact path this tool reads back for that agent.
    assert "foundry-archive/c4-liveness-run/progress/trace.jsonl" in block
    assert record["ledger"].endswith("progress/trace.jsonl")
    # And it is the STREAM variant, not the teammate one — a stream agent told
    # its phase is `cast` writes a truthful-looking ledger about work it is
    # not doing.
    assert "`inspect`" in block
    assert "Read Floor" not in block


def test_the_response_tells_the_lead_what_to_do_about_a_missing_stream(run_env) -> None:
    """The imperative channel this module already uses for spawn output."""
    project_root, fdir = run_env
    _enter_inspect(fdir, minutes_ago=40)

    result = fs.foundry_liveness(project_root=project_root)

    assert "progress_protocol" in result["instructions"]
    assert "F2 roster" in result["instructions"]


def test_a_stream_that_writes_a_ledger_is_read_from_it_like_anyone_else(run_env) -> None:
    """No duplicate row, and no no_ledger for an agent that is obeying."""
    project_root, fdir = run_env
    _enter_inspect(fdir, minutes_ago=40)
    _write_ledger(fdir, "trace", [(60, "inspect", "sweeping casting 3")])

    result = fs.foundry_liveness(project_root=project_root)

    records = [r for r in result["agents"] if r["agent"] == "trace"]
    assert len(records) == 1
    assert records[0]["status"] == fs.STATUS_PROGRESSING
    assert "trace" not in result["needs_attention"]


def test_streams_are_not_expected_outside_inspect(run_env) -> None:
    """Outside F2 they are not late, they are not spawned.

    A permanent false alarm is the same disease D-022 names: a needs_attention
    list that is always wrong about the same agent stops being read.
    """
    project_root, fdir = run_env
    (fdir / "state.json").write_text(
        json.dumps(
            {
                "phase": "F3",
                "phase_times": {
                    "F2": {
                        "started_at": (
                            datetime.now(timezone.utc) - timedelta(hours=3)
                        ).isoformat()
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    result = fs.foundry_liveness(project_root=project_root)
    assert result["agents"] == []
    assert "instructions" not in result


def test_a_just_spawned_stream_is_not_yet_late(run_env) -> None:
    """Below the threshold the honest answer is 'too early to say'."""
    project_root, fdir = run_env
    _enter_inspect(fdir, minutes_ago=2)

    result = fs.foundry_liveness(project_root=project_root)
    assert result["agents"] == []


def test_a_declared_stream_skip_is_honoured(run_env) -> None:
    """manifest.stream_skips is the run's own statement of what it will spawn.

    Entries carry the canonical UPPERCASE spelling while the ledger id is the
    lowercase wire one, so this also pins the mapping rather than a lowercase().
    """
    project_root, fdir = run_env
    _enter_inspect(
        fdir,
        minutes_ago=40,
        stream_skips=[{"stream_id": "TRACE", "reason": "spec_format_version"}],
    )

    records = _by_agent(fs.foundry_liveness(project_root=project_root))
    assert "trace" not in records
    assert records["prove"]["status"] == fs.STATUS_NO_LEDGER


def test_conditional_streams_are_only_expected_when_their_input_exists(run_env) -> None:
    """FLOW_TRACE is V3-only; RESEARCH_AUDIT needs research to audit.

    Reporting either on a run that never runs them would put a permanently
    missing agent on every single liveness call.
    """
    project_root, fdir = run_env
    _enter_inspect(fdir, minutes_ago=40)

    without = _by_agent(fs.foundry_liveness(project_root=project_root))
    assert "flow_trace" not in without
    assert "research_audit" not in without

    (fdir / "flow-delta.json").write_text("{}", encoding="utf-8")
    (fdir / "research").mkdir()

    with_inputs = _by_agent(fs.foundry_liveness(project_root=project_root))
    assert with_inputs["flow_trace"]["status"] == fs.STATUS_NO_LEDGER
    assert with_inputs["research_audit"]["status"] == fs.STATUS_NO_LEDGER


def test_an_expected_stream_can_be_queried_by_identifier(run_env) -> None:
    """CT-004's 'an agent identifier' form must reach the new rows too.

    Otherwise the roster names an agent the lead cannot then ask about.
    """
    project_root, fdir = run_env
    _enter_inspect(fdir, minutes_ago=40)

    result = fs.foundry_liveness(agent="prove", project_root=project_root)

    assert result["ok"] is True
    assert [r["agent"] for r in result["agents"]] == ["prove"]
    assert result["agents"][0]["status"] == fs.STATUS_NO_LEDGER


def test_the_empty_roster_case_survives_the_expected_roster(run_env) -> None:
    """Truth 2 is load-bearing and must not regress.

    A run with no ledgers and no overdue stream still reports ok:True with an
    empty roster, not an error and not four phantom rows.
    """
    project_root, fdir = run_env
    assert not (fdir / "progress").exists()

    result = fs.foundry_liveness(project_root=project_root)

    assert result["ok"] is True
    assert result["agents"] == []
    assert result["needs_attention"] == []


# --------------------------------------------------------------------------- #
# D-058 — the roster is what the run DISPATCHED, not what left a file
# --------------------------------------------------------------------------- #


def _dispatch(
    fdir: Path,
    casting_id,
    seconds_ago: float,
    phase: str = "grind",
    **extra,
) -> None:
    """Append one spawns.log record, in the shape the spawn tools write.

    Field-for-field what ``foundry_spawn_teammate`` and ``foundry_cast_wave``
    emit — a test that invented its own shape would pass against a reader that
    could never parse a real log. ``test_spawn_progress`` closes that by
    driving the real writer into the real reader.
    """
    moment = datetime.now(timezone.utc) - timedelta(seconds=seconds_ago)
    entry = {
        "timestamp": moment.isoformat(),
        "casting_id": casting_id,
        "phase": phase,
        "prompt_hash": "sha256:0123456789abcdef",
        "prompt_path": f"castings/casting-{casting_id}-prompt.md",
    }
    entry.update(extra)
    with (fdir / "spawns.log").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry) + "\n")


def _enter_phase(fdir: Path, phase: str, minutes_ago: float) -> None:
    """Put the run in ``phase`` as of ``minutes_ago``."""
    entered = datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)
    (fdir / "state.json").write_text(
        json.dumps(
            {"phase": phase, "phase_times": {phase: {"started_at": entered.isoformat()}}}
        ),
        encoding="utf-8",
    )


def test_a_teammate_that_died_before_line_one_is_on_the_roster(run_env) -> None:
    """D-058 in one assertion — the scenario PROVE drove at the MCP boundary.

    A run in F1 whose spawns.log records three castings dispatched 4000s ago,
    where casting-2 died before writing its first ledger line. Before the fix
    the roster was ['casting-1', 'casting-3'] and needs_attention was empty:
    the deadest teammate there is read as "nothing to see".
    """
    project_root, fdir = run_env
    _enter_phase(fdir, "F1", minutes_ago=5000 / 60)
    for casting in (1, 2, 3):
        _dispatch(fdir, casting, seconds_ago=4000, phase="cast")
    # Two of the three are obeying the protocol. casting-2 never wrote.
    _write_ledger(fdir, "casting-1", [(60, "cast", "writing tests")])
    _write_ledger(fdir, "casting-3", [(60, "cast", "read floor")])

    result = fs.foundry_liveness(project_root=project_root)

    assert [r["agent"] for r in result["agents"]] == [
        "casting-1", "casting-2", "casting-3"
    ]
    assert _by_agent(result)["casting-2"]["status"] == fs.STATUS_NO_LEDGER
    assert result["needs_attention"] == ["casting-2"]


def test_the_dead_teammates_row_dates_the_dispatch_it_never_answered(
    run_env,
) -> None:
    """A row saying only "missing" is not actionable; the age is the signal."""
    project_root, fdir = run_env
    _enter_phase(fdir, "F3", minutes_ago=90)
    _dispatch(fdir, 4, seconds_ago=3600)

    record = _by_agent(fs.foundry_liveness(project_root=project_root))["casting-4"]

    assert record["status"] == fs.STATUS_NO_LEDGER
    assert 3500 <= record["dispatched_age_seconds"] <= 3700
    assert record["phase"] == "grind"
    assert record["ledger"].endswith("progress/casting-4.jsonl")
    # The ledger-derived ages are honestly absent rather than faked to zero.
    assert record["last_progress_age_seconds"] is None
    assert record["last_line_age_seconds"] is None


def test_a_dispatched_teammate_with_a_ledger_appears_exactly_once(run_env) -> None:
    """The two halves of the roster must key agents identically.

    The glob half takes ``path.stem`` (``casting-4``) and the spawns.log half
    builds its key with ``_agent_id_for_casting``. If those two spellings ever
    diverge, one agent silently becomes two rows — a live one and a phantom
    ``no_ledger`` twin — which is worse than the invisibility D-058 fixed.
    """
    project_root, fdir = run_env
    _enter_phase(fdir, "F3", minutes_ago=90)
    _dispatch(fdir, 4, seconds_ago=3600)
    _write_ledger(fdir, "casting-4", [(30, "grind", "fixing D-058")])

    result = fs.foundry_liveness(project_root=project_root)

    assert [r["agent"] for r in result["agents"]] == ["casting-4"]
    assert result["agents"][0]["status"] == fs.STATUS_PROGRESSING
    assert result["needs_attention"] == []
    # The live row still carries the dispatch, so the lead can see that the
    # agent started writing AFTER it was asked to.
    assert result["agents"][0]["dispatched_age_seconds"] > 3000


def test_a_string_casting_id_keys_the_same_row_as_an_int(run_env) -> None:
    """``foundry_spawn_teammate`` takes ``int | str`` and logs it verbatim.

    Both spellings must land on one agent id, or a casting dispatched once by
    each path appears twice.
    """
    project_root, fdir = run_env
    _enter_phase(fdir, "F3", minutes_ago=90)
    _dispatch(fdir, 4, seconds_ago=3600)
    _dispatch(fdir, "4", seconds_ago=3000)

    result = fs.foundry_liveness(project_root=project_root)

    assert [r["agent"] for r in result["agents"]] == ["casting-4"]


def test_only_the_latest_dispatch_dates_the_silence(run_env) -> None:
    """A casting's GRIND re-dispatches are one worker resuming.

    The oldest record would report an alarming age for an agent that was in
    fact asked to work again five minutes ago.
    """
    project_root, fdir = run_env
    _enter_phase(fdir, "F3", minutes_ago=200)
    _dispatch(fdir, 4, seconds_ago=9000)
    _dispatch(fdir, 4, seconds_ago=5000)
    _dispatch(fdir, 4, seconds_ago=1800)

    record = _by_agent(fs.foundry_liveness(project_root=project_root))["casting-4"]

    assert 1700 <= record["dispatched_age_seconds"] <= 1900


def test_a_dispatch_younger_than_the_threshold_is_not_reported(run_env) -> None:
    """"Too early to say" is silence, exactly as it is for a stream agent.

    Without this gate every bulk wave would light up its own roster the second
    it was spawned, and the lead would learn to ignore the list.
    """
    project_root, fdir = run_env
    _enter_phase(fdir, "F3", minutes_ago=200)
    _dispatch(fdir, 4, seconds_ago=60)

    result = fs.foundry_liveness(project_root=project_root)

    assert result["agents"] == []
    assert result["needs_attention"] == []


def test_the_threshold_override_moves_the_dispatch_gate_too(run_env) -> None:
    """One threshold governs the whole tool, not just the ledger half."""
    project_root, fdir = run_env
    _enter_phase(fdir, "F3", minutes_ago=200)
    _dispatch(fdir, 4, seconds_ago=300)

    assert fs.foundry_liveness(project_root=project_root)["agents"] == []
    swept = fs.foundry_liveness(stall_seconds=120, project_root=project_root)
    assert [r["agent"] for r in swept["agents"]] == ["casting-4"]


def test_a_finished_wave_does_not_accumulate_on_the_roster(run_env) -> None:
    """The anti-noise pin, and the reason this union is gated on the ledger.

    A spawn record never expires. If the roster were bounded by a clock alone,
    every casting the run ever dispatched would sit in needs_attention for the
    rest of the run — the exact silting-up D-022 removed. What retires a
    dispatch is the agent answering it with a terminal line.
    """
    project_root, fdir = run_env
    _enter_phase(fdir, "F3", minutes_ago=300)
    for casting in (1, 2, 3):
        _dispatch(fdir, casting, seconds_ago=9000)
        _write_ledger(fdir, f"casting-{casting}", [(8900, "grind", "fixing")])
        _append_terminal_line(fdir, f"casting-{casting}", 8600, "committed")

    result = fs.foundry_liveness(project_root=project_root)

    assert [r["status"] for r in result["agents"]] == [fs.STATUS_DONE] * 3
    assert result["needs_attention"] == []
    assert "instructions" not in result


def test_a_redispatched_agent_stops_reading_as_finished(run_env) -> None:
    """The same invisibility, one dispatch on.

    casting-4 finished CAST, wrote its terminal line, and was re-dispatched for
    GRIND — then died before writing anything. Its ledger still ends with
    ``done: true``, so a reader that trusts the file alone reports `done` and
    the lead is told a dead teammate's work is over.
    """
    project_root, fdir = run_env
    _enter_phase(fdir, "F3", minutes_ago=300)
    _write_ledger(fdir, "casting-4", [(9000, "cast", "self-check run")])
    _append_terminal_line(fdir, "casting-4", 8700, "committed 9f21ac3")
    _dispatch(fdir, 4, seconds_ago=3600)

    record = _by_agent(fs.foundry_liveness(project_root=project_root))["casting-4"]

    assert record["status"] == fs.STATUS_STALLED
    assert "detail" in record
    assert record["dispatched_age_seconds"] < record["last_line_age_seconds"]


def test_a_terminal_line_written_after_the_dispatch_still_retires_it(
    run_env,
) -> None:
    """The other side of the boundary, and the one that keeps the fix quiet.

    Same ledger, same dispatch, opposite order: the agent answered the
    dispatch and then finished. Read as anything but `done` this would put
    every completed GRIND casting back on the watchlist.
    """
    project_root, fdir = run_env
    _enter_phase(fdir, "F3", minutes_ago=300)
    _dispatch(fdir, 4, seconds_ago=9000)
    _write_ledger(fdir, "casting-4", [(8000, "grind", "fixing D-058")])
    _append_terminal_line(fdir, "casting-4", 3600, "committed 9f21ac3")

    record = _by_agent(fs.foundry_liveness(project_root=project_root))["casting-4"]

    assert record["status"] == fs.STATUS_DONE
    assert "detail" not in record


def test_dispatches_are_invisible_outside_cast_and_grind(run_env) -> None:
    """Outside F1/F3 no teammate is in flight, so the roster stays quiet.

    At F2 the streams are the agents working, and reporting last cycle's
    teammates alongside them would bury the stream rows in stale ones.
    """
    project_root, fdir = run_env
    _dispatch(fdir, 4, seconds_ago=9000)

    for phase in ("F0", "F2", "F4", "F6"):
        _enter_phase(fdir, phase, minutes_ago=300)
        agents = [r["agent"] for r in fs.foundry_liveness(project_root=project_root)["agents"]]
        assert "casting-4" not in agents, f"a teammate row leaked into {phase}"


def test_a_cast_dispatch_is_not_evidence_of_work_during_grind(run_env) -> None:
    """The spawn record names the phase it was for, and that is honoured.

    A casting built in CAST and never re-dispatched has nothing owing in
    GRIND. Reading its CAST record as an outstanding dispatch would put every
    casting the run ever built on the GRIND roster forever.
    """
    project_root, fdir = run_env
    _enter_phase(fdir, "F3", minutes_ago=300)
    _dispatch(fdir, 5, seconds_ago=9000, phase="cast")
    _dispatch(fdir, 6, seconds_ago=9000, phase="grind")

    agents = [r["agent"] for r in fs.foundry_liveness(project_root=project_root)["agents"]]

    assert agents == ["casting-6"]


def test_a_missing_teammate_can_be_queried_by_identifier(run_env) -> None:
    """CT-004's identifier form must reach the dispatch-derived rows too."""
    project_root, fdir = run_env
    _enter_phase(fdir, "F3", minutes_ago=300)
    _dispatch(fdir, 4, seconds_ago=3600)

    result = fs.foundry_liveness(agent="casting-4", project_root=project_root)

    assert result["ok"] is True
    assert [r["agent"] for r in result["agents"]] == ["casting-4"]
    assert result["agents"][0]["status"] == fs.STATUS_NO_LEDGER


def test_the_teammate_instruction_names_the_teammates_own_remedy(run_env) -> None:
    """A dead teammate must not be described with the stream agent's fix.

    The two kinds are invisible for different reasons: nothing hands a stream
    its protocol block, while a teammate is handed one beside its prompt. One
    blended instruction would send the lead to edit a stream agent's prompt
    over a teammate that simply died.
    """
    project_root, fdir = run_env
    _enter_phase(fdir, "F3", minutes_ago=300)
    _dispatch(fdir, 4, seconds_ago=3600)

    instructions = fs.foundry_liveness(project_root=project_root)["instructions"]

    assert "casting-4" in instructions
    assert "Foundry-Cast-Wave" in instructions
    assert "spawns.log" in instructions
    assert "F2 roster" not in instructions


def test_both_kinds_of_missing_agent_get_their_own_clause(run_env) -> None:
    """A GRIND-phase run cannot produce both, but the composition must hold.

    Keyed on which kind is missing rather than on the shared status, so
    neither clause can swallow the other's agents.
    """
    project_root, fdir = run_env
    # F2 with an overdue phase produces stream rows; a grind dispatch produces
    # none here, so this pins that the stream clause still stands alone.
    _enter_inspect(fdir, minutes_ago=40)
    _dispatch(fdir, 4, seconds_ago=3600)

    result = fs.foundry_liveness(project_root=project_root)
    instructions = result["instructions"]

    assert "F2 roster" in instructions
    assert "progress_protocol" in instructions
    assert "casting-4" not in instructions


def test_a_torn_or_alien_spawn_log_never_fails_the_call(run_env) -> None:
    """The log is a best-effort append that may be interrupted mid-line.

    One torn write must not blind the lead to every good record around it, and
    must never raise across the MCP boundary.
    """
    project_root, fdir = run_env
    _enter_phase(fdir, "F3", minutes_ago=300)
    (fdir / "spawns.log").write_text(
        "\n".join(
            [
                "{not json at all",
                json.dumps(["a", "list", "not", "an", "object"]),
                json.dumps({"casting_id": 7, "phase": "grind"}),  # no timestamp
                json.dumps({"phase": "grind", "timestamp": "nonsense"}),
                json.dumps(
                    {"casting_id": True, "phase": "grind", "timestamp": "2026-01-01T00:00:00+00:00"}
                ),
                json.dumps(
                    {"casting_id": "  ", "phase": "grind", "timestamp": "2026-01-01T00:00:00+00:00"}
                ),
                "",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    _dispatch(fdir, 4, seconds_ago=3600)

    result = fs.foundry_liveness(project_root=project_root)

    assert result["ok"] is True
    assert [r["agent"] for r in result["agents"]] == ["casting-4"]


def test_no_spawn_log_at_all_is_an_empty_roster(run_env) -> None:
    """A run in F1 before its first dispatch has nothing to report."""
    project_root, fdir = run_env
    _enter_phase(fdir, "F1", minutes_ago=300)
    assert not (fdir / "spawns.log").exists()

    result = fs.foundry_liveness(project_root=project_root)

    assert result["ok"] is True
    assert result["agents"] == []


# --------------------------------------------------------------------------- #
# D-114 — a ledger dated in the FUTURE
# --------------------------------------------------------------------------- #
#
# `_write_ledger` takes each line's AGE, so a NEGATIVE age is a line dated that
# many seconds in the future. Nothing else about these fixtures is unusual:
# every line below is a well-formed ledger line whose only fault is its clock.
#
# The defect: a future timestamp makes the ages negative, a negative age is
# below every threshold, and the fall-through verdict was `progressing`. So a
# clock-skewed agent read healthy for as long as the skew lasted — and the
# further ahead the clock, the more certain the wrong answer.


def test_a_future_dated_ledger_is_not_reported_as_progressing(run_env) -> None:
    """D-114 in one assertion.

    An hour into the future: `now - moment` is -3599, no comparison against a
    positive threshold can fire, and the agent used to fall through to
    `progressing` no matter how dead it was.
    """
    project_root, fdir = run_env
    _write_ledger(fdir, "casting-4", [(-3600, "cast", "read floor")])

    record = _by_agent(fs.foundry_liveness(project_root=project_root))["casting-4"]

    assert record["status"] != fs.STATUS_PROGRESSING
    assert record["status"] == fs.STATUS_STALLED
    # The ages stay negative because they are the evidence of the skew.
    # Clamping them to zero would hide the very thing the row is reporting.
    assert record["last_line_age_seconds"] < 0
    assert record["last_progress_age_seconds"] < 0


def test_a_future_dated_agent_reaches_the_lead_as_a_call_to_action(run_env) -> None:
    """The defect's real cost: `needs_attention` is what the lead reads.

    A status nobody looks at is not a report. `progressing` kept the skewed
    agent off this list entirely, so the lead was told nothing was wrong.
    """
    project_root, fdir = run_env
    _write_ledger(fdir, "casting-skewed", [(-3600, "cast", "read floor")])
    _write_ledger(fdir, "casting-live", [(20, "cast", "writing tests")])

    result = fs.foundry_liveness(project_root=project_root)
    records = _by_agent(result)

    assert result["needs_attention"] == ["casting-skewed"]
    assert records["casting-skewed"]["status"] != records["casting-live"]["status"]
    assert records["casting-live"]["status"] == fs.STATUS_PROGRESSING


def test_the_size_of_the_skew_cannot_buy_a_healthy_verdict(run_env) -> None:
    """A year ahead is the same anomaly as an hour ahead, not a better agent.

    Pins the direction of the comparison: an implementation that tested the
    skew against a tolerance, or that only caught small skews, would satisfy
    the hour-ahead test above and fail here.
    """
    project_root, fdir = run_env
    _write_ledger(fdir, "casting-4", [(-31_536_000, "cast", "read floor")])

    record = _by_agent(fs.foundry_liveness(project_root=project_root))["casting-4"]

    assert record["status"] == fs.STATUS_STALLED
    assert record["last_line_age_seconds"] <= -31_000_000


def test_a_future_dated_row_says_why_it_was_flagged(run_env) -> None:
    """`stalled` alone would send the lead hunting a live agent's pane.

    The status is shared with genuine silence, so the row has to carry the
    difference: this agent may be fine and its CLOCK is the problem.
    """
    project_root, fdir = run_env
    _write_ledger(fdir, "casting-4", [(-3600, "cast", "read floor")])

    record = _by_agent(fs.foundry_liveness(project_root=project_root))["casting-4"]

    assert "FUTURE" in record["detail"]
    assert "progressing" in record["detail"]
    # The remedy is the protocol block's own rule, so the lead can act on it.
    assert "UTC" in record["detail"]


def test_a_naive_local_timestamp_is_the_realistic_skew(run_env) -> None:
    """Not an adversarial input — the parser's own documented leniency.

    `_parse_progress_timestamp` reads a naive timestamp as UTC rather than
    discarding the line, so an agent that wrote `datetime.now().isoformat()`
    anywhere east of Greenwich future-dates EVERY line it will ever write.
    Before the fix that agent was reported healthy for the whole run.
    """
    project_root, fdir = run_env
    local_now_two_hours_east = (
        datetime.now(timezone.utc) + timedelta(hours=2)
    ).replace(tzinfo=None)
    pdir = fdir / "progress"
    pdir.mkdir(parents=True, exist_ok=True)
    (pdir / "casting-4.jsonl").write_text(
        json.dumps(
            {
                "timestamp": local_now_two_hours_east.isoformat(),
                "phase": "cast",
                "step": "read floor",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    record = _by_agent(fs.foundry_liveness(project_root=project_root))["casting-4"]

    assert record["lines"] == 1  # the line was parsed, not discarded
    assert record["status"] == fs.STATUS_STALLED


def test_a_future_progress_line_is_caught_behind_a_recent_last_line(run_env) -> None:
    """Why the check is on BOTH ages rather than only the heartbeat.

    Here the last line is a healthy 10 seconds old, but the line that dates
    when the current step was first reached is 30 minutes in the future. The
    heartbeat age alone is unremarkable; the progress age — the number AC-021
    actually asks for — is negative and untrustworthy.
    """
    project_root, fdir = run_env
    _write_ledger(
        fdir,
        "casting-4",
        [(-1800, "grind", "fixing D-114"), (10, "grind", "fixing D-114")],
    )

    record = _by_agent(fs.foundry_liveness(project_root=project_root))["casting-4"]

    assert record["last_line_age_seconds"] >= 0
    assert record["last_progress_age_seconds"] < 0
    assert record["status"] == fs.STATUS_STALLED


def test_a_terminal_line_still_outranks_a_future_timestamp(run_env) -> None:
    """The precedence this fix deliberately did NOT change.

    An agent that declared itself finished said so in words, not in a
    timestamp, and a terminal line already outranks every age check including
    a non-monotonic one. Moving the skew check above it would put every
    finished agent with a fast clock back into `needs_attention` — rebuilding
    D-022's silting, the disease `done` exists to cure.
    """
    project_root, fdir = run_env
    _write_ledger(fdir, "casting-4", [(-3600, "cast", "writing the handler")])
    _append_terminal_line(fdir, "casting-4", age_seconds=-3500, step="committed 9f21ac3")

    result = fs.foundry_liveness(project_root=project_root)
    record = _by_agent(result)["casting-4"]

    assert record["status"] == fs.STATUS_DONE
    assert "detail" not in record
    assert result["needs_attention"] == []


# --------------------------------------------------------------------------- #
# D-117 — a non-finite threshold
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("bad", [float("inf"), float("nan")])
def test_refuses_a_non_finite_threshold(run_env, bad) -> None:
    """The two values that pass every other gate and are still not a threshold.

    `inf > 0` holds and EVERY comparison against `nan` is False — including
    `nan <= 0` — so neither is caught by the positivity gate. Left to run,
    both make every agent read `progressing` (nothing is ever `>= inf`, or
    `>= nan`) and then `int(threshold)` raises `cannot convert float infinity
    to integer` across the MCP boundary, where this module owes a refusal.
    """
    project_root, _fdir = run_env
    result = fs.foundry_liveness(stall_seconds=bad, project_root=project_root)
    assert result["ok"] is False
    assert "stall_seconds" in result["error"]
    assert "finite" in result["error"]
    assert str(fs.STALL_THRESHOLD_SECONDS) in result["hint"]


def test_infinity_is_reachable_over_the_wire_as_valid_json(run_env) -> None:
    """The reachability argument, driven rather than asserted in a comment.

    `1e400` is strictly valid JSON that Python's own parser turns into `inf`,
    so this arrives through the real SDK path from a client that never typed
    the word "infinity". A gate that only refused a literal `float('inf')`
    typed in a test would be guarding a door nobody uses.
    """
    project_root, _fdir = run_env
    from_the_wire = json.loads('{"stall_seconds": 1e400}')["stall_seconds"]
    assert from_the_wire == float("inf")

    result = fs.foundry_liveness(stall_seconds=from_the_wire, project_root=project_root)

    assert result["ok"] is False
    assert "finite" in result["error"]


def test_negative_infinity_keeps_the_refusal_it_already_had(run_env) -> None:
    """A path that was already correct must not be re-routed by the fix.

    `-inf <= 0` is True, so it is refused by the positivity gate and never
    reaches the finiteness one. Both refusals are named and both are honest;
    this pins which one answers, so the finiteness check cannot quietly
    swallow the more specific message.
    """
    project_root, _fdir = run_env
    result = fs.foundry_liveness(stall_seconds=float("-inf"), project_root=project_root)
    assert result["ok"] is False
    assert "greater than 0" in result["error"]


def test_a_non_finite_threshold_is_refused_before_any_agent_is_judged(run_env) -> None:
    """The refusal is a gate, not a late failure after a wrong report.

    With `inf` accepted, no age is ever `>= threshold`, so a stalled agent and
    a live one both read `progressing` — the report would be wrong before the
    raise ever arrived.
    """
    project_root, fdir = run_env
    _write_ledger(fdir, "casting-dead", [(9000, "cast", "read floor")])

    result = fs.foundry_liveness(stall_seconds=float("inf"), project_root=project_root)

    assert result["ok"] is False
    assert "agents" not in result


# --------------------------------------------------------------------------- #
# D-115 adjacent path — the manifest reader liveness owns
# --------------------------------------------------------------------------- #


def test_a_wrong_typed_manifest_does_not_disturb_the_stream_roster(run_env) -> None:
    """The THIRD caller of a manifest read in this module, driven end to end.

    D-115 is about the two spawn doors, which `test_spawn_progress.py` covers.
    `_skipped_stream_ids` is the reader that has always held this guard, and
    it is reached from a different tool entirely — so this is the adjacent
    path: a manifest that is valid JSON of the wrong type must degrade to "no
    declared skips" and leave the expected-stream roster intact, never raise
    out through `Foundry-Liveness`.
    """
    project_root, fdir = run_env
    _enter_inspect(fdir, minutes_ago=40)
    castings = fdir / "castings"
    castings.mkdir(parents=True, exist_ok=True)
    (castings / "manifest.json").write_text("[1, 2, 3]", encoding="utf-8")

    result = fs.foundry_liveness(project_root=project_root)

    assert result["ok"] is True
    # No skips could be read, so every unconditional stream is still expected.
    assert {r["agent"] for r in result["agents"]} >= {"trace", "prove"}
    assert all(r["status"] == fs.STATUS_NO_LEDGER for r in result["agents"])


# --------------------------------------------------------------------------- #
# D-132 adjacent path — the SKIP LIST inside the manifest liveness owns
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "stream_skips",
    [42, {"TRACE": "spec_format_version"}, "TRACE"],
    ids=["a number", "an object keyed by stream", "a bare string"],
)
def test_a_wrong_typed_skip_list_degrades_instead_of_raising(
    run_env, stream_skips
) -> None:
    """The rung below the one D-115 fixed, in the reader that "always guarded".

    ``_skipped_stream_ids`` is the reader this module's own prose holds up as
    the one that has always held the manifest shape guard — and it held it at
    the CONTAINER only, exactly as D-115 held the manifest at the container
    only. Each row is a different consequence of that:

    ``42`` reached ``for entry in 42`` and raised ``TypeError: 'int' object is
    not iterable`` straight out of Foundry-Liveness — the diagnostic a lead
    reaches for precisely when a run has already gone wrong.

    ``{"TRACE": "..."}`` is the quieter and worse failure, and the reason "no
    raise" is not a sufficient assertion. Writing the skip list as a map from
    stream to reason is an ordinary authoring slip; iterating a dict yields its
    KEYS, so this parsed as a skip list containing TRACE and the tool silently
    STOPPED REPORTING the trace stream. A liveness tool that hides an agent
    because its input was misread is worse than one that crashes.

    ``"TRACE"`` iterates its characters, none of which is a wire id, so today
    it degrades to nothing by luck rather than by contract. It is here to pin
    the contract, not because it ever broke.

    D-132's fix routes this reader through the shared validator, so the shape
    it tolerates is now the same shape both spawn doors accept — one policy,
    four readers, rather than four readers agreeing by hand.
    """
    project_root, fdir = run_env
    _enter_inspect(fdir, minutes_ago=40, stream_skips=stream_skips)

    result = fs.foundry_liveness(project_root=project_root)

    assert result["ok"] is True
    # TRACE appears in every row, so a skip list read out of a corrupt document
    # would have silently removed it from the roster.
    assert {r["agent"] for r in result["agents"]} >= {"trace", "prove"}


def test_a_well_formed_skip_list_still_reaches_the_roster(run_env) -> None:
    """The guard must not cost the working path, which is the whole feature.

    A validator wired in to stop a raise is worth nothing if it also stops the
    ordinary document — and a skip list is exactly the shape most at risk of
    that, because its entries are legitimately EITHER an object or a bare
    string and the shape table has to say so on the record.
    """
    project_root, fdir = run_env
    _enter_inspect(
        fdir,
        minutes_ago=40,
        stream_skips=[{"stream_id": "TRACE", "reason": "spec_format_version"}, "PROVE"],
    )

    agents = {r["agent"] for r in fs.foundry_liveness(project_root=project_root)["agents"]}

    assert "trace" not in agents
    assert "prove" not in agents
