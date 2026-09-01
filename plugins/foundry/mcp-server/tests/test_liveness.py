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
