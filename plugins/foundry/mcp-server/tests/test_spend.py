"""Spend: what a run cost, measured rather than estimated, and never a gate.

CT-013 / GI-005 / FR-021 / FR-022 / FR-037 / AC-033 / AC-034 / NFR-002 /
OT-023 / OT-024.

WHAT THIS FILE IS ABOUT
-----------------------
Foundry-Next used to carry a `context_budget` block that mapped the cycle
counter onto the words low / moderate / high / critical and called the result
"estimated_usage". It read no tokens, no durations and no agent records: a run
on cycle 3 was "critical" whether it had spent four hundred thousand tokens or
four million. A number that is not measured is worse than no number, because it
gets acted on.

`Foundry-Spend` replaces it with what the lead actually reports. Two properties
matter more than the arithmetic, and most of this file is about them:

GI-005 — "Parser stays out of the server; the fragile block is only ever read by
the lead." Nothing in this server tails a transcript, regex-parses an Agent usage
block, or reads any harness-owned format. A parser for a format nobody owns does
not fail loudly; it silently starts reporting a wrong number.

CT-013 / FR-022 — nothing here ever refuses, and no gate ever blocks on a
missing record. A forgotten Foundry-Spend is a gap in a cost report, not a defect
in the build. Unreported dispatches are DERIVED and LISTED so the gap is visible
rather than invisible.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from foundry_mcp.schemas.vocab import SPEND_LEDGER_FILENAME
from foundry_mcp.tools import foundry_orchestrator as fo
from foundry_mcp.tools import foundry_state
from foundry_mcp.tools.foundry_orchestrator import (
    foundry_gate,
    foundry_next_action,
    foundry_record_spend,
)

RUN_NAME = "spend-run"


@pytest.fixture
def run_env(tmp_path, monkeypatch):
    """Activate a foundry run under tmp_path; yield (project_root, fdir).

    The same shape as `test_escalation.py`'s, including the `_check_active_teams`
    monkeypatch that keeps every test hermetic against the ambient tmux session.
    """
    project_root = tmp_path
    fdir = project_root / "foundry-archive" / RUN_NAME
    (fdir / "castings").mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(
        fo,
        "_check_active_teams",
        lambda _pr: {"active": False, "teams": [], "live_panes": []},
    )

    foundry_state.set_active_run(RUN_NAME)
    try:
        yield str(project_root), fdir
    finally:
        foundry_state.clear_active_run()


def _write_state(fdir: Path, phase: str = "F1", cycle: int = 0, **extra) -> None:
    state = {"phase": phase, "cycle": cycle}
    state.update(extra)
    (fdir / "state.json").write_text(json.dumps(state), encoding="utf-8")


def _read_state(fdir: Path) -> dict:
    return json.loads((fdir / "state.json").read_text(encoding="utf-8"))


def _set_position(fdir: Path, phase: str, cycle: int) -> None:
    """Move the run's phase and cycle WITHOUT resetting anything else.

    `_write_state` replaces the document, which would wipe the `spend` roll-ups
    the call under test just accumulated — a fixture that erased the thing being
    measured between measurements.
    """
    state = _read_state(fdir)
    state.update({"phase": phase, "cycle": cycle})
    (fdir / "state.json").write_text(json.dumps(state), encoding="utf-8")


def _ledger(fdir: Path) -> list[dict]:
    text = (fdir / SPEND_LEDGER_FILENAME).read_text(encoding="utf-8")
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def _write_spawns(fdir: Path, rows: list[dict]) -> None:
    (fdir / "spawns.log").write_text(
        "".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8"
    )


# --------------------------------------------------------------------------- #
# CT-013 / FR-021 — the per-agent ledger line
# --------------------------------------------------------------------------- #


def test_one_call_writes_one_ledger_line_in_the_declared_shape(run_env):
    """CT-013: 'Foundry-Spend records agent, phase, tokens and duration.'

    JSONL, one object per line, in the same shape `spawns.log` uses — because a
    run's ledgers are read together and a second format is a second parser for
    whoever reads them.
    """
    project_root, fdir = run_env
    _write_state(fdir, phase="F1", cycle=0)

    result = foundry_record_spend("casting-3", "F1", 412_000, 1_800_000,
                                  project_root=project_root)

    assert result["ok"] is True, result
    rows = _ledger(fdir)
    assert len(rows) == 1
    row = rows[0]
    assert row["agent"] == "casting-3"
    assert row["phase"] == "F1"
    assert row["tokens"] == 412_000
    assert row["duration_ms"] == 1_800_000
    assert row["cycle"] == 0
    assert row["recorded_at"]


def test_the_ledger_appends_and_never_rewrites(run_env):
    """A run's spend is a history, not a running total.

    The roll-ups are derived and could be recomputed; the per-agent lines are the
    evidence they were derived from, and a writer that rewrote the file would
    lose the only record of which agent cost what.
    """
    project_root, fdir = run_env
    _write_state(fdir)

    foundry_record_spend("casting-1", "F1", 100, 1000, project_root=project_root)
    foundry_record_spend("casting-2", "F1", 200, 2000, project_root=project_root)
    foundry_record_spend("trace", "F2", 300, 3000, project_root=project_root)

    assert [r["agent"] for r in _ledger(fdir)] == ["casting-1", "casting-2", "trace"]


# --------------------------------------------------------------------------- #
# FR-037 / AC-033 / OT-023 — the roll-ups
# --------------------------------------------------------------------------- #


def test_three_calls_in_one_phase_roll_up_per_phase_and_for_the_run(run_env):
    """OT-023 verbatim: 'After three Foundry-Spend calls in one phase,
    Foundry-Next shows that phase's token and minute totals and the run total.'

    FR-037 leaves the placement to the implementer with one proviso — tokens and
    seconds are stored SIDE BY SIDE. The two questions a lead asks ("what did
    this phase cost", "how long did it take") are asked together, and a roll-up
    answering only one sends them to a second artifact.
    """
    project_root, fdir = run_env
    _write_state(fdir, phase="F2", cycle=1)

    for agent, tokens, ms in (
        ("trace", 100_000, 60_000),
        ("prove", 250_000, 120_000),
        ("test", 50_000, 30_000),
    ):
        foundry_record_spend(agent, "F2", tokens, ms, project_root=project_root)

    spend = _read_state(fdir)["spend"]
    assert spend["by_phase"]["F2"] == {
        "tokens": 400_000, "duration_ms": 210_000, "agents": 3, "unreported": 0,
    }
    assert spend["total"]["tokens"] == 400_000
    assert spend["total"]["duration_ms"] == 210_000
    assert spend["total"]["agents"] == 3

    nxt = foundry_next_action(project_root)
    assert nxt["spend"]["total"]["tokens"] == 400_000
    assert nxt["spend"]["by_phase"]["F2"]["tokens"] == 400_000


def test_the_cycle_bucket_is_keyed_by_the_server_counter_not_the_claim(run_env):
    """FR-037's proviso: 'roll-ups are keyed by the server cycle counter.'

    The caller's asserted cycle is persisted BESIDE the server's, never instead
    of it — the same D-119 ruling the defect ledger already holds. A cost report
    that disagrees with the cycle ledger about which cycle a run was in is worse
    than no cost report, and this is the door that would introduce the
    divergence.
    """
    project_root, fdir = run_env
    _write_state(fdir, phase="F3", cycle=4)

    foundry_record_spend("casting-3", "F3", 10, 20, cycle=99,
                         project_root=project_root)

    row = _ledger(fdir)[0]
    assert row["cycle"] == 4, "the bucket key is the server's counter"
    assert row["declared_cycle"] == 99, "the claim is kept, for audit"
    assert set(_read_state(fdir)["spend"]["by_cycle"]) == {"4"}


def test_phases_and_cycles_accumulate_independently(run_env):
    """The two roll-ups answer different questions and must not share a bucket.

    "What did F2 cost across the whole run" and "what did cycle 2 cost across
    every phase" are both things a lead asks, and one number cannot be both.
    """
    project_root, fdir = run_env

    _write_state(fdir, phase="F2", cycle=1)
    foundry_record_spend("trace", "F2", 100, 1000, project_root=project_root)
    _set_position(fdir, "F3", 1)
    foundry_record_spend("casting-3", "F3", 200, 2000, project_root=project_root)
    _set_position(fdir, "F2", 2)
    foundry_record_spend("trace", "F2", 400, 4000, project_root=project_root)

    spend = _read_state(fdir)["spend"]
    assert spend["by_phase"]["F2"]["tokens"] == 500
    assert spend["by_phase"]["F3"]["tokens"] == 200
    assert spend["by_cycle"]["1"]["tokens"] == 300
    assert spend["by_cycle"]["2"]["tokens"] == 400
    assert spend["total"]["tokens"] == 700


# --------------------------------------------------------------------------- #
# AC-034 / FR-022 / OT-024 — unreported dispatches are LISTED, never refused on
# --------------------------------------------------------------------------- #


def test_a_dispatched_casting_with_no_spend_record_is_listed_as_unreported(run_env):
    """OT-024 verbatim: 'A dispatched casting with no Foundry-Spend record
    appears as unreported in Foundry-Next and the report, and no gate refuses.'

    DERIVED from `spawns.log` rather than tracked by a second counter, so the
    roster of who owes a number is the roster of who was actually dispatched.
    """
    project_root, fdir = run_env
    _write_state(fdir, phase="F1", cycle=0)
    _write_spawns(fdir, [
        {"agent": "casting-1", "phase": "cast"},
        {"agent": "casting-2", "phase": "cast"},
    ])

    foundry_record_spend("casting-1", "cast", 100, 1000, project_root=project_root)

    unreported = foundry_next_action(project_root)["spend"]["unreported_dispatches"]
    assert [u["agent"] for u in unreported] == ["casting-2"]


def test_no_gate_refuses_because_a_spend_record_is_missing(run_env):
    """FR-022 verbatim: 'A forgotten Foundry-Spend never blocks a gate.'

    Driven across every gate that has a defect or stream precondition, because
    the guarantee is about the WHOLE gate surface and a single spot check would
    miss whichever one later grew a spend read. An accounting omission must
    never stop a build.
    """
    project_root, fdir = run_env
    _write_state(fdir, phase="F4", cycle=1)
    (fdir / "defects.json").write_text(json.dumps({"defects": []}), encoding="utf-8")
    (fdir / "verdicts.json").write_text(
        json.dumps({"requirements": []}), encoding="utf-8"
    )
    _write_spawns(fdir, [{"agent": "casting-9", "phase": "cast"}])

    for phase in ("assay", "temper", "nyquist", "done"):
        (fdir / ".next-action-called").write_text(f"{fo._now()}\n", encoding="utf-8")
        result = foundry_gate(phase, project_root)
        reason = result.get("reason", "")
        assert "spend" not in reason.lower(), (phase, result)
        assert "unreported" not in reason.lower(), (phase, result)


def test_recording_the_missing_agent_removes_it_from_the_unreported_list(run_env):
    """The list is a live derivation, not a one-time snapshot: the gap closes
    when the number arrives, which is what makes the list worth reading."""
    project_root, fdir = run_env
    _write_state(fdir)
    _write_spawns(fdir, [{"agent": "casting-7", "phase": "cast"}])

    assert fo._unreported_dispatches(fdir)

    foundry_record_spend("casting-7", "cast", 1, 1, project_root=project_root)

    assert fo._unreported_dispatches(fdir) == []


def test_a_run_that_dispatched_nothing_reports_no_unreported_agents(run_env):
    """The empty case, stated so it cannot become a false alarm.

    A run with no `spawns.log` has dispatched nothing, and "nobody owes a
    number" must not render as "somebody is missing".
    """
    project_root, fdir = run_env
    _write_state(fdir)

    assert fo._unreported_dispatches(fdir) == []
    assert foundry_next_action(project_root)["spend"]["unreported_count"] == 0


# --------------------------------------------------------------------------- #
# CT-013 / NFR-002 — it never refuses
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "tokens, duration, expected_tokens, expected_duration",
    [
        (0, 0, 0, 0),
        (-5, -5, 0, 0),
        ("lots", None, 0, 0),
        (True, False, 0, 0),
        (1.9, 2.9, 1, 2),
    ],
    ids=["zero", "negative", "non-numeric", "bool", "float"],
)
def test_a_malformed_count_is_recorded_as_zero_and_never_refused(
    run_env, tokens, duration, expected_tokens, expected_duration
):
    """CT-013: 'none; unreported dispatches are listed, never refused.'

    A lead typing a number out of a usage block will occasionally type something
    that is not a number, and refusing the call would lose the record of the
    dispatch entirely — which is strictly worse than recording a zero beside an
    agent name a reader can see is wrong. `True` is coerced to 0 rather than 1:
    a bool is not a token count, and Python's `isinstance(True, int)` is exactly
    how a bool sneaks in as one.
    """
    project_root, fdir = run_env
    _write_state(fdir)

    result = foundry_record_spend("casting-3", "F1", tokens, duration,
                                  project_root=project_root)

    assert result["ok"] is True, result
    row = _ledger(fdir)[0]
    assert row["tokens"] == expected_tokens
    assert row["duration_ms"] == expected_duration


def test_an_empty_agent_or_phase_is_named_rather_than_dropped(run_env):
    """Same reasoning one field along: an unnamed dispatch is still a dispatch,
    and recording it as "unknown" keeps it countable. Dropping the row would
    make the run's total quietly too small."""
    project_root, fdir = run_env
    _write_state(fdir)

    foundry_record_spend("", "  ", 10, 10, project_root=project_root)

    row = _ledger(fdir)[0]
    assert row["agent"] == "unknown"
    assert row["phase"] == "unknown"


def test_with_no_active_run_it_says_so_rather_than_writing_anywhere(run_env):
    """The one thing it does refuse, and it is not about the numbers: there is
    no run to record against. The house shape — {error, hint}, never a raise."""
    project_root, _fdir = run_env
    foundry_state.clear_active_run()

    result = foundry_record_spend("casting-3", "F1", 1, 1, project_root=project_root)

    assert result.get("ok") is not True
    assert "No active foundry run" in result["error"]
    assert result["hint"]


# --------------------------------------------------------------------------- #
# GI-005 / AC-033 / OT-023 — no parser, and no money
# --------------------------------------------------------------------------- #


def test_the_server_parses_no_transcript_and_no_usage_block(run_env):
    """GI-005 verbatim: 'Parser stays out of the server; the fragile block is
    only ever read by the lead.'

    Asserted against the SOURCE of every module that touches spend, because the
    violation is the EXISTENCE of a parser rather than any particular output.
    The named violation is "Server code that tails transcript JSONL or
    regex-parses the Agent usage block", and both of those would work fine on
    the day they were written — the failure is that the format is nobody's
    contract, so it drifts and the server then reports a wrong number instead of
    no number.
    """
    import inspect

    # Comment lines are stripped first. The rule is about what the server DOES,
    # and the module's own prose explaining that it deliberately parses nothing
    # is not a parser — a scan that could not tell those apart would forbid
    # documenting the requirement it enforces.
    source = "\n".join(
        line for line in inspect.getsource(fo).splitlines()
        if not line.lstrip().startswith("#")
    )
    for forbidden in (
        "transcript",
        "Total cost",
        ".jsonl.gz",
        "~/.claude/projects",
    ):
        assert forbidden not in source, forbidden

    # The ONE channel: the lead types the numbers into this handler.
    params = inspect.signature(foundry_record_spend).parameters
    assert set(params) == {
        "agent", "phase", "tokens", "duration_ms", "cycle", "project_root",
    }


def test_no_dollar_figure_appears_in_any_rendered_output(run_env):
    """AC-033 / OT-023: 'no dollar figure appears anywhere.'

    This server does not know anyone's rate card, so a cost in money would be a
    second invented number beside the estimate that was just removed. Driven
    over the rendered display as well as the result dict, because rendering is
    where a helpful "$" would most plausibly be added.
    """
    from foundry_mcp.tools.display import format_result

    project_root, fdir = run_env
    _write_state(fdir, phase="F2", cycle=1)
    _write_spawns(fdir, [{"agent": "casting-9", "phase": "cast"}])
    recorded = foundry_record_spend("trace", "F2", 1_234_567, 654_321,
                                    project_root=project_root)
    nxt = foundry_next_action(project_root)

    rendered = "\n".join([
        format_result("Foundry-Spend", recorded),
        format_result("Foundry-Next", nxt),
        fo._format_status_display(project_root),
    ])

    assert "$" not in rendered
    for word in ("cost", "usd", "dollar", "price"):
        assert word not in rendered.lower(), word
    # ...and the numbers that SHOULD be there are.
    assert "1,234,567" in rendered
    assert "unreported" in rendered.lower()


def test_the_estimated_usage_block_is_gone(run_env):
    """NFR-002's no-narrowing rule cuts both ways: the replacement must actually
    replace, not sit beside the thing it replaces.

    `context_budget` mapped the cycle counter onto four words and called it
    usage. Leaving it in place next to measured spend would give a lead two
    numbers for one question, one of them fabricated — and the fabricated one is
    the shorter, more confident-looking answer.
    """
    project_root, fdir = run_env
    _write_state(fdir, phase="F3", cycle=3)

    nxt = foundry_next_action(project_root)

    assert "context_budget" not in nxt
    assert "spend" in nxt

    import inspect

    # Code only, for the same reason as the parser scan above: the comment
    # explaining why the block was removed necessarily names it.
    code = "\n".join(
        line for line in inspect.getsource(fo).splitlines()
        if not line.lstrip().startswith("#")
    )
    assert "estimated_usage" not in code
    assert "context_budget" not in code


def test_next_reports_the_executing_build_alongside_the_spend(run_env):
    """AC-027 / OT-018: 'state.json carries server_version, plugin_version,
    server_root and server_commit after init, and Foundry-Next displays them.'

    The display half. `foundry_init` writes the four facts; this path reports
    them and decides nothing — a run whose executing server is a stale cached
    copy cannot use the fixes it is itself shipping, and nothing on screen said
    so.
    """
    project_root, fdir = run_env
    _write_state(
        fdir, phase="F2", cycle=1,
        server_version="4.10.0", plugin_version="4.10.0",
        server_root="/repo/plugins/foundry", server_commit="3f9c1a284d6b",
        self_target=True,
    )

    nxt = foundry_next_action(project_root)

    assert nxt["executing_server"]["server_version"] == "4.10.0"
    assert nxt["executing_server"]["plugin_version"] == "4.10.0"
    assert nxt["executing_server"]["server_root"] == "/repo/plugins/foundry"
    assert nxt["executing_server"]["server_commit"] == "3f9c1a284d6b"
    assert "3f9c1a284d6b" in fo._format_status_display(project_root)


# --------------------------------------------------------------------------- #
# AC-028 / OT-019 / FR-017 — the MCP handshake carries foundry's own version
#
# The server reported no version at all, so a client could not tell which build
# it was talking to — and neither could a run. A plugin-targeting run whose
# executing server is a stale cached copy is exactly the failure the F0
# self-target preflight exists to catch, and that preflight compares a version
# the handshake never published.
# --------------------------------------------------------------------------- #


def test_the_initialize_handshake_reports_the_package_version():
    """OT-019 verbatim: 'The MCP initialize result reports serverInfo.version
    equal to foundry's `__version__`.'

    Asserted against the SERVER OBJECT the SDK builds its handshake from, and
    compared to the package's own `__version__` rather than to a literal — a
    hardcoded version string here would agree with itself forever while the
    package moved underneath it.
    """
    from foundry_mcp import __version__, server as foundry_server

    assert foundry_server.server.version == __version__
    assert __version__, "the package must actually declare a version"


def test_the_spend_and_report_tools_are_registered_and_dispatched():
    """CT-011's registration half. A handler nothing can call is not a tool, and
    this is the surface where an omission is silent: the MCP SDK validates
    arguments against the advertised schema BEFORE dispatch, so a tool missing
    from `list_tools` is unreachable however good its handler is."""
    import asyncio

    from foundry_mcp import server as foundry_server

    tools = {t.name: t for t in asyncio.run(foundry_server.list_tools())}
    for name in ("Foundry-Spend", "Foundry-Report"):
        assert name in tools, name
        assert name in foundry_server._DISPATCH, name

    spend = tools["Foundry-Spend"].inputSchema
    assert set(spend["required"]) == {"agent", "phase", "tokens", "duration_ms"}
    assert spend["properties"]["cycle"]["type"] == "integer"
    # GI-005 must be legible from the tool surface itself: the lead reads this
    # description at the moment of the call, and "the LEAD types the numbers" is
    # the whole protocol.
    described = tools["Foundry-Spend"].description.lower()
    assert "lead" in described
    assert "parses no transcript" in described
    assert "never refuses" in described
    assert "$" not in tools["Foundry-Spend"].description


def test_the_spend_dispatch_delivers_every_argument_to_the_handler(run_env):
    """The transport half, asserted by DRIVING the dispatcher rather than by
    grepping the lambda: the string can be present while the argument is
    dropped, and absent while the wiring is correct."""
    from foundry_mcp import server as foundry_server

    project_root, fdir = run_env
    _write_state(fdir, phase="F2", cycle=3)

    previous = foundry_server._project_root
    try:
        foundry_server._project_root = project_root
        result = foundry_server._DISPATCH["Foundry-Spend"]({
            "agent": "prove", "phase": "F2", "tokens": 900,
            "duration_ms": 4000, "cycle": 77,
        })
    finally:
        foundry_server._project_root = previous

    assert result["ok"] is True, result
    row = _ledger(fdir)[0]
    assert row["agent"] == "prove"
    assert row["tokens"] == 900
    assert row["duration_ms"] == 4000
    assert row["declared_cycle"] == 77
    assert row["cycle"] == 3


def test_the_next_display_fallback_renders_the_new_fields(run_env):
    """`display.py`'s fallback path, which is the one that runs when the
    orchestrator's pre-rendered box is absent.

    Every line is a LABELLED line and a missing key contributes none — this
    module's rule, and not a stylistic one: D-157 put a literal '?' on screen
    while the handler had returned a fully-worded refusal, because a formatter
    indexed keys a refusal does not carry.
    """
    from foundry_mcp.tools.display import format_result

    rendered = format_result("Foundry-Next", {
        "phase": "F2",
        "action": "run_streams",
        "instructions": "run the roster",
        "inspect_mode": {
            "mode": "DELTA", "rule": "delta", "decided_by": "inspect_start",
            "required_streams": ["trace", "prove", "test"],
            "stream_scope": {"test01": {"scope": "skipped", "detail": "untouched"}},
            "prove_sample": ["FR-001", "FR-002"],
        },
        "spend": {
            "total": {"tokens": 12_345, "duration_ms": 120_000, "agents": 2},
            "by_phase": {"F2": {"tokens": 12_345, "duration_ms": 120_000}},
            "by_cycle": {},
            "unreported_dispatches": [{"agent": "casting-9", "phase": "cast"}],
            "unreported_count": 1,
        },
        "executing_server": {
            "server_version": "4.10.0", "plugin_version": "4.10.0",
            "server_root": "/repo/plugins/foundry", "server_commit": "3f9c1a284d6b",
        },
        "waiting_on_agents": {"waiting": True, "count": 2,
                              "detail": "oldest progress 4m 0s ago"},
    })

    assert "DELTA" in rendered
    assert "trace, prove, test" in rendered
    assert "test01" in rendered
    assert "12,345" in rendered
    assert "casting-9@cast" in rendered
    assert "3f9c1a284d6b" in rendered
    assert "2 agent(s)" in rendered
    assert "$" not in rendered


def test_the_fallback_renders_a_result_carrying_none_of_the_new_fields(run_env):
    """The same claim from the other side: a key that is not there contributes
    no line, and rendering must not raise. A run resumed from before this landed
    carries none of these fields."""
    from foundry_mcp.tools.display import format_result

    rendered = format_result("Foundry-Next", {
        "phase": "F1", "action": "cast", "instructions": "build",
    })

    assert "cast" in rendered
    assert "Inspect:" not in rendered
    assert "Spend:" not in rendered
