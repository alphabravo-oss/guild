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
        "progressing", "no_progress", "stalled", "unknown"
    }
    assert fs.NEEDS_ATTENTION_STATUSES < fs.PROGRESS_STATUSES
    assert fs.STATUS_PROGRESSING not in fs.NEEDS_ATTENTION_STATUSES


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
    """
    project_root, fdir = run_env
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
        f"{'agent':<16}{'status':<14}{'last_progress':>14}{'last_line':>11}  step",
        f"{'-' * 15:<16}{'-' * 13:<14}{'-' * 13:>14}{'-' * 9:>11}  {'-' * 20}",
    ]
    for record in result["agents"]:
        rows.append(
            f"{record['agent']:<16}"
            f"{record['status']:<14}"
            f"{record['last_progress_age_seconds'] // 60:>12}m"
            f"{record['last_line_age_seconds'] // 60:>10}m"
            f"  {record['step']}"
        )
    rows += ["", f"needs_attention: {result['needs_attention']}", ""]

    out = "\n".join(rows)
    print(out)

    # The report distinguishes all three, and says so in plain text.
    assert "progressing" in out
    assert "no_progress" in out
    assert "stalled" in out
    # beta's heartbeat is as fresh as alpha's; only its PROGRESS age differs.
    assert re.search(r"casting-beta\s+no_progress\s+40m\s+1m", out)
    assert re.search(r"casting-alpha\s+progressing\s+1m\s+1m", out)
    assert result["needs_attention"] == ["casting-beta", "casting-gamma"]


def test_the_record_names_the_ledger_it_was_read_from(run_env) -> None:
    """So a lead acting on a stall can go read the file without guessing."""
    project_root, fdir = run_env
    _write_ledger(fdir, "casting-4", [(20, "cast", "x")])

    record = _by_agent(fs.foundry_liveness(project_root=project_root))["casting-4"]

    assert record["ledger"] == "foundry-archive/c4-liveness-run/progress/casting-4.jsonl"
    assert (Path(project_root) / record["ledger"]).exists()
