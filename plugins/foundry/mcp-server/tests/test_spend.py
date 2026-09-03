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


# --------------------------------------------------------------------------- #
# D-004 — "NEVER REFUSES" WAS IMPLEMENTED AS "NEVER SAYS ANYTHING".
#
# The two are not the same. `str(agent or "").strip() or "unknown"` recorded a
# mis-attributed row and returned ok=True with no key in the result matching
# "warn", so a lead who mistyped or omitted an agent name had nothing in the
# response to notice. The refusal half of CT-013 is untouched — a coerced value
# still records, still counts, still blocks nothing.
# --------------------------------------------------------------------------- #


def test_a_coerced_agent_name_is_warned_about_rather_than_recorded_in_silence(run_env):
    """The defect: an unnamed dispatch is filed under "unknown" and the caller
    is TOLD, so the row can be re-recorded before the cost report is read."""
    project_root, fdir = run_env
    _write_state(fdir)

    result = foundry_record_spend("", "F1", 10, 10, project_root=project_root)

    assert result["ok"] is True, result
    assert "error" not in result
    warnings = result["warnings"]
    assert any("agent" in w and "unknown" in w for w in warnings), warnings
    # ...and the row is still there, still counted. The notice is not a refusal.
    assert _ledger(fdir)[0]["agent"] == "unknown"
    assert result["total"]["agents"] == 1


def test_every_field_this_tool_coerces_is_named_in_one_call(run_env):
    """The CAUSE was the absence of surfacing, not the absence of surfacing for
    `agent` — so the fix is derived over the coerced fields. A call that gets
    all four wrong is told about all four, in one result, and still succeeds.
    """
    project_root, fdir = run_env
    _write_state(fdir)

    result = foundry_record_spend("", "", "lots", None, project_root=project_root)

    assert result["ok"] is True, result
    said = " ".join(result["warnings"])
    for field in ("agent", "phase", "tokens", "duration_ms"):
        assert field in said, (field, said)


def test_a_clean_call_carries_no_warnings_key_at_all(run_env):
    """A notice that is always present is a notice nobody reads. The key exists
    only when something was actually coerced."""
    project_root, fdir = run_env
    _write_state(fdir)

    result = foundry_record_spend("casting-3", "F3", 1000, 2000,
                                  project_root=project_root)

    assert result["ok"] is True
    assert "warnings" not in result


def test_the_warning_reaches_the_lead_through_the_display(run_env):
    """THE ADJACENT PATH, and the reason a result key alone would not close this:
    the lead reads `display.py`'s rendered box, not the raw dict, so a warning
    the formatter drops is exactly as silent as no warning at all. Driven through
    `format_result`, the same entry point the MCP layer renders through.
    """
    from foundry_mcp.tools.display import format_result

    project_root, fdir = run_env
    _write_state(fdir)

    result = foundry_record_spend("", "F1", 10, 10, project_root=project_root)
    rendered = format_result("Foundry-Spend", result)

    # The WARNING TEXT itself, not merely the coerced value — the box already
    # printed "unknown" as the agent name before this fix and said nothing about
    # why, which is precisely the silence being closed.
    assert result["warnings"], result
    for warning in result["warnings"]:
        assert warning in rendered, (warning, rendered)
    # The box still reports the recorded spend — the notice is additive.
    assert "Spend recorded" in rendered


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

    D-018 — ASSERTED THROUGH `format_result`, WHICH IS WHAT THE LEAD READS.
    `server.py#call_tool` returns `format_result(name, result)` and nothing
    else, so a fact present in the result dict and absent from that string
    never reaches anybody. This used to assert against
    `_format_status_display`, one renderer deep, and so could not have caught
    the case it now pins: the pre-rendered block being returned INSTEAD of the
    lines that carry `server_root`.
    """
    from foundry_mcp.tools.display import format_result

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

    rendered = format_result("Foundry-Next", nxt)
    assert "3f9c1a284d6b" in rendered
    assert "4.10.0" in rendered
    # The field the two drifted derivations disagreed about: the dead renderer
    # carried `server_root` and the live one did not.
    assert "plugins/foundry" in rendered


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


# --------------------------------------------------------------------------- #
# D-018 / AC-033 / OT-023 / FR-021 — the per-fact lines reach the LEAD
#
# `server.py#call_tool` returns `format_result(name, result)` and nothing else,
# so every claim below is asserted against that string. A fact that is in the
# result dict and not in that string was computed for nobody.
# --------------------------------------------------------------------------- #


def test_a_pre_rendered_display_does_not_swallow_the_per_fact_lines(run_env):
    """D-018: 'Per-phase and per-cycle spend never reach the lead.'

    `foundry_next_action` sets `display` on EVERY call, and
    `_fmt_foundry_next_action` returned it instead of calling
    `_fmt_foundry_next_lines`. So the renderer that draws the per-phase and
    per-cycle roll-ups was unreachable in production and the lead saw the run
    total only. This drives the production shape — a result that HAS `display`
    — because the shape without one is the shape that already worked.
    """
    from foundry_mcp.tools.display import format_result

    rendered = format_result("Foundry-Next", {
        "phase": "F3",
        "action": "fix_defects",
        "display": "PRE-RENDERED STATUS BOX",
        "instructions": "fix them",
        "spend": {
            "total": {"tokens": 99_000, "duration_ms": 600_000, "agents": 3},
            "by_phase": {
                "F1": {"tokens": 60_000, "duration_ms": 360_000},
                "F3": {"tokens": 39_000, "duration_ms": 240_000},
            },
            "by_cycle": {"2": {"tokens": 39_000, "duration_ms": 240_000}},
            "unreported_dispatches": [],
            "unreported_count": 0,
        },
    })

    assert "PRE-RENDERED STATUS BOX" in rendered, "the box must survive too"
    assert "fix them" in rendered
    # The three numbers the dead renderer alone drew.
    assert "99,000" in rendered
    assert "60,000" in rendered, "per-phase roll-up missing"
    assert "39,000" in rendered, "per-cycle roll-up missing"
    assert "F1:" in rendered and "F3:" in rendered


def test_per_phase_and_per_cycle_spend_reach_the_lead_end_to_end(run_env):
    """AC-033 / OT-023: 'Foundry-Next shows tokens and minutes per phase and
    per cycle and the run total; no dollar figure appears anywhere.'

    Driven through the real doors rather than a hand-built result dict, so the
    roll-up arithmetic and the rendering are pinned by one test.
    """
    from foundry_mcp.tools.display import format_result

    project_root, fdir = run_env
    _write_state(fdir, phase="F1", cycle=0)
    foundry_record_spend("casting-1", "F1", 500_000, 900_000,
                         project_root=project_root)
    _set_position(fdir, "F3", 2)
    foundry_record_spend("casting-2", "F3", 250_000, 300_000,
                         project_root=project_root)

    rendered = format_result("Foundry-Next", foundry_next_action(project_root))

    assert "750,000" in rendered, "run total"
    assert "500,000" in rendered and "250,000" in rendered, "per-phase"
    assert "F1:" in rendered and "F3:" in rendered
    # Per cycle: cycle 0 took the first row, cycle 2 the second.
    assert "0:" in rendered and "2:" in rendered
    assert "$" not in rendered


def test_exactly_one_renderer_draws_each_of_the_four_fact_groups(run_env):
    """D-018's second half: 'The dead renderer also holds a second copy of the
    Inspect/Spend/Server lines _format_status_display already renders, and the
    two have ALREADY DRIFTED.'

    Two derivations of one rendered fact is the defect; a label appearing twice
    in one screen is what the operator would see if the repair had merely made
    the dead copy reachable.
    """
    from foundry_mcp.tools.display import format_result

    project_root, fdir = run_env
    _write_state(
        fdir, phase="F2", cycle=1,
        server_version="4.10.0", plugin_version="4.10.0",
        server_root="/repo/plugins/foundry", server_commit="3f9c1a284d6b",
        inspect_modes=[{
            "cycle": 1, "phase": "F2", "mode": "FULL", "rule": "first_of_phase",
            "decided_by": "cast", "required_streams": ["trace", "prove", "test"],
            "stream_scope": {}, "prove_sample": [],
        }],
    )
    foundry_record_spend("casting-1", "F2", 1_000, 1_000,
                         project_root=project_root)

    rendered = format_result("Foundry-Next", foundry_next_action(project_root))

    for label in ("Inspect:", "Spend:", "Server:"):
        assert rendered.count(label) == 1, f"{label} drawn {rendered.count(label)}x"

    # And the orchestrator's renderer is not one of the two. Source-asserted,
    # because "which module drew it" is not observable in the joined string.
    import inspect

    status_src = inspect.getsource(fo._format_status_display)
    code = "\n".join(
        line for line in status_src.splitlines()
        if not line.lstrip().startswith("#")
    )
    for label in ("Inspect:", "Spend:", "Server:", "HALTED"):
        assert label not in code, f"_format_status_display still draws {label}"


# --------------------------------------------------------------------------- #
# D-031 / D-038 — the two spend numbers that meant nothing
# --------------------------------------------------------------------------- #


def test_the_unreported_bucket_count_is_written_not_left_at_zero(run_env):
    """D-031: the `unreported` key 'is initialised and normalised but never
    incremented ... permanently 0, so any consumer reading the per-bucket
    unreported count reads a zero that means nothing.'

    FR-022: 'the report shows N agents unreported per phase so the gap is
    visible.' Per PHASE is the claim, so a per-phase zero on a run with three
    unreported dispatches is the exact opposite of the truth.
    """
    project_root, fdir = run_env
    _write_state(fdir, phase="F3", cycle=2)
    _write_spawns(fdir, [
        {"casting_id": 1, "phase": "cast"},
        {"casting_id": 2, "phase": "cast"},
        {"casting_id": 3, "phase": "grind"},
    ])
    foundry_record_spend("casting-1", "cast", 10, 10, project_root=project_root)

    summary = fo._spend_summary(fdir)

    assert summary["total"]["unreported"] == 2
    # D-048: bucketed under the RUN PHASE the verb maps to, never under the verb
    # itself. `by_phase["grind"]` was a phantom bucket keyed by a dispatch verb.
    assert summary["by_phase"]["F1"]["unreported"] == 1
    assert summary["by_phase"]["F3"]["unreported"] == 1
    assert "cast" not in summary["by_phase"] and "grind" not in summary["by_phase"]
    # ...and the persisted document carries it too, rather than the zero C-4's
    # shape would otherwise leave sitting in state.json forever.
    assert _read_state(fdir)["spend"]["total"]["unreported"] == 2


def test_a_phase_with_no_recorded_spend_still_gets_its_unreported_count(run_env):
    """The run the count matters most on: the lead called Foundry-Spend for
    nothing at all. A bucket that only exists once someone reports would hide
    exactly that run."""
    project_root, fdir = run_env
    _write_state(fdir, phase="F1", cycle=0)
    _write_spawns(fdir, [{"casting_id": 7, "phase": "cast"}])

    summary = fo._spend_summary(fdir)

    assert summary["by_phase"]["F1"]["unreported"] == 1
    assert summary["total"]["unreported"] == 1
    assert summary["total"]["tokens"] == 0


def test_an_agent_that_reports_spend_twice_counts_as_one_agent(run_env):
    """D-038: 'foundry_record_spend does bucket["agents"] += 1 per CALL, while
    the report counts DISTINCT agents. An agent that reports spend twice
    inflates the orchestrator's count and not the report's.'

    A re-dispatched GRIND teammate and a lead correcting a fat-fingered token
    count both produce a second row for one agent, so this is the normal case,
    not an edge one.
    """
    project_root, fdir = run_env
    _write_state(fdir, phase="F3", cycle=1)

    foundry_record_spend("casting-4", "F3", 100, 1_000, project_root=project_root)
    result = foundry_record_spend("casting-4", "F3", 250, 2_000,
                                  project_root=project_root)

    assert result["total"]["agents"] == 1, "one agent, two reports"
    assert result["by_phase"]["F3"]["agents"] == 1
    # Both rows still count toward the money-free numbers that ARE additive.
    assert result["total"]["tokens"] == 350
    assert result["total"]["duration_ms"] == 3_000
    assert len(_ledger(fdir)) == 2


def test_two_agents_in_one_phase_still_count_as_two(run_env):
    """The other direction of D-038: deduplicating by agent must not collapse
    genuinely distinct agents into one."""
    project_root, fdir = run_env
    _write_state(fdir, phase="F2", cycle=1)

    foundry_record_spend("trace", "F2", 10, 10, project_root=project_root)
    result = foundry_record_spend("prove", "F2", 10, 10, project_root=project_root)

    assert result["total"]["agents"] == 2
    assert result["by_phase"]["F2"]["agents"] == 2


# --------------------------------------------------------------------------- #
# D-011 / D-041 / ST-009 / CT-010 / AC-026 — Foundry-Init's refusal renders
# --------------------------------------------------------------------------- #


def _self_target_refusal() -> dict:
    """The shape `foundry.py`'s self-target preflight returns (two sites)."""
    return {
        "ok": False,
        "error": (
            "Self-targeting run refused — version mismatch: the working tree's "
            "plugin.json declares '9.9.9' but the executing server was imported "
            "from /cache/foundry whose plugin.json declares '4.10.0'."
        ),
        "hint": (
            "This run's TARGET is the foundry plugin, but the executing server "
            "is a different build of it. Quit, relaunch with the command above "
            "so the server is started from the working tree, and re-run "
            "Foundry-Init."
        ),
        "launch_command": "claude --plugin-dir /repo/plugins/foundry",
        "mismatch": "version",
        "server_version": "4.10.0",
        "plugin_version": "4.10.0",
        "server_root": "/cache/foundry",
        "server_commit": "aaaa1111bbbb",
        "self_target": True,
    }


def test_the_self_target_refusal_prints_the_launch_command(run_env):
    """D-011: 'The self-target refusal never prints the launch command ... the
    hint says "relaunch with the command above" and there is no command above.'

    `foundry.py` sets `launch_command` into the result dict at two sites and
    `server.py#call_tool` returns only `format_result(...)`, so the key never
    crossed the MCP boundary; grep found its only readers were test assertions.
    """
    from foundry_mcp.tools.display import format_result

    rendered = format_result("Foundry-Init", _self_target_refusal())

    assert "claude --plugin-dir /repo/plugins/foundry" in rendered
    assert "relaunch with the command above" in rendered


def test_the_house_refusal_net_also_prints_a_named_launch_command():
    """D-011 was filed against `_house_refusal_display`, and that is where the
    general repair belongs: it is the net EVERY formatter falls into when its
    own rendering does not repeat the refusal text. Driven directly, because
    the tool whose refusal carries the field today now renders its own."""
    from foundry_mcp.tools.display import _house_refusal_display

    rendered = _house_refusal_display(
        "Some-Tool",
        {"error": "refused", "hint": "use the command above",
         "launch_command": "claude --plugin-dir /repo/plugins/foundry"},
        "refused",
    )

    assert "claude --plugin-dir /repo/plugins/foundry" in rendered


def test_the_init_refusal_names_which_build_is_executing(run_env):
    """D-041: '_fmt_foundry_init's Server/Plugin/Root/Commit block is dead on
    both paths — dead on success because "display" is always set, and dead on
    refusal because the house renderer wins.'

    The refusal path. `_fmt_foundry_init` had no error branch, so it rendered
    no `error` text and `format_result` threw the whole rendering away for
    `_house_refusal_display` — which knows nothing about these four facts.
    """
    from foundry_mcp.tools.display import format_result

    rendered = format_result("Foundry-Init", _self_target_refusal())

    assert "4.10.0" in rendered, "executing server version"
    assert "aaaa1111bbbb" in rendered, "executing server commit"
    assert "/cache/foundry" in rendered or "cache/foundry" in rendered
    # The refusal itself must still survive — that is what `format_result`
    # checks for before it reaches for the house net.
    assert "version mismatch" in rendered


def test_the_init_success_names_which_build_is_executing(run_env):
    """D-041's other half: dead on SUCCESS, because `foundry_init` always sets
    `display` and the formatter returned it before reaching these lines.

    The pre-rendered box is built by `foundry.py#_format_init_display`, which
    draws none of the four, so appending is the only repair available from this
    side of the ownership boundary — and there is nothing to duplicate.
    """
    from foundry_mcp.tools.display import format_result

    rendered = format_result("Foundry-Init", {
        "foundry_dir": "/repo/foundry-archive/run",
        "run_name": "run",
        "display": "PRE-RENDERED INIT BOX",
        "server_version": "4.10.0",
        "plugin_version": "4.10.0",
        "server_root": "/repo/plugins/foundry",
        "server_commit": "3f9c1a284d6b",
        "self_target": True,
    })

    assert "PRE-RENDERED INIT BOX" in rendered
    assert "4.10.0" in rendered
    assert "3f9c1a284d6b" in rendered
    assert "self" in rendered


def test_the_spend_schema_documents_the_spellings_the_run_records(run_env):
    """D-013's documentation half, one vocabulary along (D-048).

    D-013 read "spawns.log records 'cast'" and documented the VERB. That put a
    dispatch verb in a field CT-013 calls a phase, and `by_phase` grew a bucket
    keyed `grind`. The reconciliation runs the other way now: the dispatch side
    maps its verbs to run phase ids, so the schema documents the RUN PHASE and
    names the verbs only as accepted input.

    A schema description is the only place a lead learns what to type, so a
    spelling no ledger writes is not a documentation nit — it is the reason no
    single Foundry-Spend call could clear both the report and Foundry-Next.
    """
    import asyncio

    from foundry_mcp import server as foundry_server

    tools = {t.name: t for t in asyncio.run(foundry_server.list_tools())}
    props = tools["Foundry-Spend"].inputSchema["properties"]

    phase_doc = props["phase"]["description"]
    for run_phase in ("F1", "F2", "F3"):
        assert f"'{run_phase}'" in phase_doc, phase_doc
    assert "'cast'" in phase_doc and "'grind'" in phase_doc, phase_doc
    agent_doc = props["agent"]["description"]
    assert "casting-" in agent_doc, agent_doc
    assert "spawns.log" in agent_doc, agent_doc


def test_the_documented_phase_spelling_actually_clears_a_dispatch(run_env):
    """The claim the documentation makes, driven: a lead who types what the
    schema says clears the dispatch on the exact pair.

    D-047 removed the forgiving fallback this used to lean on, so the pair is
    now the only thing that clears anything — which makes the documented
    spelling load-bearing rather than merely tidy.
    """
    project_root, fdir = run_env
    _write_state(fdir, phase="F1", cycle=0)
    _write_spawns(fdir, [{"casting_id": 3, "phase": "cast"}])

    assert fo._unreported_dispatches(fdir) == [
        {"agent": "casting-3", "phase": "F1"}
    ]

    foundry_record_spend("casting-3", "F1", 1_000, 1_000,
                         project_root=project_root)

    assert fo._unreported_dispatches(fdir) == []
    assert fo._spend_summary(fdir)["by_phase"]["F1"]["tokens"] == 1_000


def test_the_dispatch_verb_a_lead_can_see_also_clears_the_dispatch(run_env):
    """CT-013 verbatim: 'none; unreported dispatches are listed, never refused.'

    A lead reading `spawns.log` sees `cast`, and D-048's reconciliation must not
    turn that reasonable guess into a phantom bucket nothing clears. The verb is
    mapped to its run phase at the door, so the ledger holds one vocabulary
    whichever spelling arrives.
    """
    project_root, fdir = run_env
    _write_state(fdir, phase="F1", cycle=0)
    _write_spawns(fdir, [{"casting_id": 3, "phase": "cast"}])

    foundry_record_spend("casting-3", "cast", 1_000, 1_000,
                         project_root=project_root)

    assert fo._unreported_dispatches(fdir) == []
    assert fo._spend_summary(fdir)["by_phase"]["F1"]["tokens"] == 1_000
    assert "cast" not in fo._spend_summary(fdir)["by_phase"]
    row = json.loads(
        (fdir / "spend.jsonl").read_text(encoding="utf-8").splitlines()[0]
    )
    assert row["phase"] == "F1"


def test_a_phase_gap_is_visible_when_the_same_agent_reported_elsewhere(run_env):
    """FR-022 verbatim: 'the report shows N agents unreported per phase so the
    gap is visible.'

    D-047: `_unreported_dispatches` carried the clause
    `or row["agent"] in reported_agents`, so one spend call anywhere cleared
    EVERY dispatch of that agent. Driven: casting-3 dispatched at CAST and again
    at GRIND, spend reported for CAST only — the GRIND dispatch appeared
    nowhere, and the only agent the list could ever show was one that never
    reported at all. That is the exact measurement gap FR-022 names, invisible
    on the surface built to display it.
    """
    project_root, fdir = run_env
    _write_state(fdir, phase="F3", cycle=1)
    _write_spawns(fdir, [
        {"casting_id": 3, "phase": "cast"},
        {"casting_id": 3, "phase": "grind"},
    ])

    foundry_record_spend("casting-3", "F1", 500, 500, project_root=project_root)

    assert fo._unreported_dispatches(fdir) == [
        {"agent": "casting-3", "phase": "F3"}
    ]
    summary = fo._spend_summary(fdir)
    assert summary["by_phase"]["F3"]["unreported"] == 1
    assert summary["by_phase"]["F1"]["unreported"] == 0


def test_foundry_next_and_the_report_name_the_same_unreported_pairs(run_env):
    """THE ADJACENT PATH, and why D-047 and D-048 are one class rather than two.

    Two surfaces answer this question — Foundry-Next renders
    `_unreported_dispatches` and the F6 report renders
    `foundry_report._read_unreported_dispatches` — and D-013 unified their
    agent-ID spelling on a rule that was wrong on both. They call one pure
    helper now, so the projection to `(agent, phase)` must agree exactly.
    """
    from foundry_mcp.tools.foundry_report import _read_unreported_dispatches

    project_root, fdir = run_env
    _write_state(fdir, phase="F3", cycle=1)
    _write_spawns(fdir, [
        {"casting_id": 3, "phase": "cast"},
        {"casting_id": 4, "phase": "grind"},
    ])
    foundry_record_spend("casting-3", "F1", 500, 500, project_root=project_root)

    section, problem = _read_unreported_dispatches(fdir)
    assert problem is None, problem
    from_report = {
        (agent, phase)
        for phase, agents in section["by_phase"].items()
        for agent in agents
    }
    from_next = {
        (row["agent"], row["phase"]) for row in fo._unreported_dispatches(fdir)
    }
    assert from_next == from_report == {("casting-4", "F3")}
