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

import ast
import json
from pathlib import Path

import pytest

import foundry_mcp
from foundry_mcp.schemas.vocab import SPEND_LEDGER_FILENAME
from foundry_mcp.tools import foundry_state

# fallout FR-005 / GI-010 / GI-026 / AC-014 — THE SPEND LEDGER HAS A MODULE.
#
# The `fo` alias reached three concerns through one name; the single
# orchestrator module it named is gone and GI-010 forbids a re-export shim
# standing in for it. The ledger, its door and its roll-up inputs are
# `orchestration/spend.py`; the gate ladder is `orchestration/gates.py`; the
# next-action guidance and the status renderer are `orchestration/guidance.py`.
# The two leaf facts this module reaches — the house timestamp and the
# unreported-dispatch overlay — are `tools/foundry_state.py` (GI-024, Holmes
# `share-2`), not an `orchestration/` module: reaching a split module for a leaf
# fact would recreate the second copy casting 10 just removed.
from foundry_mcp.tools import orchestration as _orchestration_pkg
from foundry_mcp.tools.orchestration import guidance as _guidance
from foundry_mcp.tools.orchestration import spend as _spend
from foundry_mcp.tools.orchestration.gates import foundry_gate
from foundry_mcp.tools.orchestration.guidance import foundry_next_action
from foundry_mcp.tools.orchestration.spend import foundry_record_spend

# `_check_active_teams` is bound by name in every orchestration module that
# reads it, so patching the one that DEFINES it leaves every importer on the
# real one.
from tests.orchestration._env import ORCHESTRATION, patch_everywhere

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

    patch_everywhere(
        monkeypatch,
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
        (fdir / ".next-action-called").write_text(f"{foundry_state.now_iso()}\n", encoding="utf-8")
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

    assert _spend._unreported_dispatches(fdir)

    foundry_record_spend("casting-7", "cast", 1, 1, project_root=project_root)

    assert _spend._unreported_dispatches(fdir) == []


def test_a_run_that_dispatched_nothing_reports_no_unreported_agents(run_env):
    """The empty case, stated so it cannot become a false alarm.

    A run with no `spawns.log` has dispatched nothing, and "nobody owes a
    number" must not render as "somebody is missing".
    """
    project_root, fdir = run_env
    _write_state(fdir)

    assert _spend._unreported_dispatches(fdir) == []
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
    only when something was actually coerced.

    D-189: a "clean call" is one whose agent this run actually dispatched, so
    the fixture says so. It previously recorded spend for `casting-3` on a run
    whose dispatch record was empty and called that clean — which is precisely
    the state CT-013 asks to be warned about, asserted as the silent case.
    """
    project_root, fdir = run_env
    _write_state(fdir)
    _write_spawns(fdir, [{"agent": "casting-3", "phase": "grind"}])

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
# D-189 — CT-013's ERRORS CELL NAMED A SIGNAL THE DOOR NEVER EMITTED.
#
# The cell reads "none; unknown agent is recorded with a warning". The door
# warned on a DIFFERENT condition than the one the contract names — an EMPTY
# agent name, which `_identity` files under the literal id "unknown" — so the
# one signal that catches a mistyped agent never fired. Driven through
# `server.call_tool` on a synthetic run: `agent='casting-99-never-dispatched'`
# returned ok True with no warnings key at all, and so did `agent='who-is-this'`.
#
# The implemented condition was "the caller named no agent"; CT-013's condition
# is "the agent is unknown", and a name this run has never dispatched is exactly
# the latter. Nothing refuses here and nothing should — the errors cell says
# "none". The missing half was the warning beside the acceptance.
# --------------------------------------------------------------------------- #


def test_an_agent_this_run_never_dispatched_is_warned_about(run_env):
    """CT-013 verbatim: 'none; unknown agent is recorded with a warning.'

    Both names PROVE drove, and the drive that localised the defect: this run
    dispatched casting-1 and nothing else, so an id that resolves to no row in
    the dispatch record is the contract's own "unknown agent".
    """
    project_root, fdir = run_env
    _write_state(fdir, phase="F3", cycle=1)
    _write_spawns(fdir, [{"agent": "casting-1", "phase": "grind"}])

    for unknown in ("casting-99-never-dispatched", "who-is-this", "casting-l"):
        result = foundry_record_spend(unknown, "F3", 1000, 2000,
                                      project_root=project_root)
        assert result["ok"] is True, result
        assert "error" not in result
        said = " ".join(result["warnings"])
        assert unknown in said, (unknown, said)
        assert "no dispatch" in said, (unknown, said)
        # The record this run DOES hold is named, so the lead can see the id
        # they meant beside the one they typed.
        assert "casting-1" in said, (unknown, said)

    # ...and the dispatched id itself is silent, so the warning discriminates.
    clean = foundry_record_spend("casting-1", "F3", 1000, 2000,
                                 project_root=project_root)
    assert "warnings" not in clean, clean


def test_the_unknown_agent_and_the_empty_name_are_two_separate_warnings(run_env):
    """CT-013 / D-004: an empty name and an unrecognised name are different
    mistakes with different remedies — supply the name, versus correct it — so
    they may not collapse into one sentence.

    The empty-name case keeps `_identity`'s own warning and gains no second one:
    "unknown" is the id the coercion writes, and reporting it as an agent the
    dispatch record does not name would be the same fact said twice.
    """
    project_root, fdir = run_env
    _write_state(fdir, phase="F3", cycle=1)
    _write_spawns(fdir, [{"agent": "casting-1", "phase": "grind"}])

    empty = foundry_record_spend("", "F3", 10, 10, project_root=project_root)
    unknown = foundry_record_spend("casting-l", "F3", 10, 10,
                                   project_root=project_root)

    assert empty["ok"] is True and unknown["ok"] is True
    assert len(empty["warnings"]) == 1, empty["warnings"]
    assert "empty or missing" in empty["warnings"][0]
    assert "no dispatch" not in " ".join(empty["warnings"]), empty["warnings"]

    assert len(unknown["warnings"]) == 1, unknown["warnings"]
    assert "no dispatch" in unknown["warnings"][0]
    assert "empty or missing" not in unknown["warnings"][0]


def test_the_unknown_agent_is_recorded_and_counted_not_dropped(run_env):
    """CT-013's operative word: the unknown agent is RECORDED with a warning.

    The tokens were really spent; only the attribution is in doubt. Dropping the
    row, or holding it out of `spend.total.agents`, would answer "what did this
    phase cost" with a number wrong in the other direction — and
    `foundry_report._read_spend` cross-checks that roll-up against the ledger's
    DISTINCT NAMES, so a filter on one side manufactures a false "stale
    roll-up" disagreement on the other (D-038 / D-090).
    """
    project_root, fdir = run_env
    _write_state(fdir, phase="F3", cycle=1)
    _write_spawns(fdir, [{"agent": "casting-1", "phase": "grind"}])

    result = foundry_record_spend("casting-l", "F3", 99_000, 600_000,
                                  project_root=project_root)

    assert result["warnings"], result
    rows = _ledger(fdir)
    assert [r["agent"] for r in rows] == ["casting-l"]
    assert result["total"]["tokens"] == 99_000
    assert result["total"]["agents"] == 1
    state = json.loads((fdir / "state.json").read_text(encoding="utf-8"))
    assert state["spend"]["total"]["agents"] == 1
    # And the ledger the report cross-checks against agrees with the roll-up.
    ledger_names = {r["agent"] for r in rows}
    assert len(ledger_names) == state["spend"]["total"]["agents"]


def test_the_warning_reaches_the_lead_through_the_spend_display(run_env):
    """NFR-005 — the lead reads the rendered box, not the raw dict, so a warning
    the formatter drops is exactly as silent as no warning at all. Same adjacent
    path D-004's own display test walks, on the new condition."""
    from foundry_mcp.tools.display import format_result

    project_root, fdir = run_env
    _write_state(fdir, phase="F3", cycle=1)
    _write_spawns(fdir, [{"agent": "casting-1", "phase": "grind"}])

    result = foundry_record_spend("casting-l", "F3", 99_000, 600_000,
                                  project_root=project_root)
    rendered = format_result("Foundry-Spend", result)

    for warning in result["warnings"]:
        assert warning in _plain(rendered), (warning, _plain(rendered))
    assert "Spend recorded" in rendered


def test_the_next_display_does_not_pass_a_phantom_off_as_an_attributed_agent(
    run_env,
):
    """AC-033 / AC-034 / FR-022 — the consequence PROVE drove end to end.

    One real dispatch of casting-1, then one typo. Before this change
    Foundry-Next rendered "over 1 reported agent(s)" AND "Unreported: 1
    casting-1@F3" — two agents on the screen for what was ONE dispatch, with
    nothing saying which of them is a phantom. The count is deliberately not
    filtered (see `foundry_record_spend`'s docstring); what the display now does
    is NAME the agents no dispatch matched, beside the count, so the two lines
    can be reconciled by the person reading them.
    """
    from foundry_mcp.tools.display import _fmt_foundry_next_lines

    project_root, fdir = run_env
    _write_state(fdir, phase="F3", cycle=1)
    _write_spawns(fdir, [{"agent": "casting-1", "phase": "grind"}])

    foundry_record_spend("casting-l", "F3", 99_000, 600_000,
                         project_root=project_root)

    result = foundry_next_action(project_root)
    assert result["spend"]["unmatched_agents"] == ["casting-l"]
    assert [u["agent"] for u in result["spend"]["unreported_dispatches"]] == [
        "casting-1"
    ]

    rendered = _plain("\n".join(_fmt_foundry_next_lines(result)))
    assert "over 1 reported agent(s)" in rendered, rendered
    assert "1 matching no dispatch" in rendered, rendered
    assert "casting-l" in rendered, rendered
    # The real dispatch is still shown as owing a number; the two facts sit
    # side by side rather than reading as two agents.
    assert "Unreported: 1" in rendered, rendered

    # No gate refuses on any of it (CT-013 / FR-022 / AC-034).
    (fdir / ".next-action-called").write_text(f"{foundry_state.now_iso()}\n", encoding="utf-8")
    assert "spend" not in json.dumps(foundry_gate("grind", project_root)).lower()


def test_a_run_that_dispatched_nothing_matches_no_agent_and_says_so(run_env):
    """The degenerate case, stated rather than exempted.

    A synthetic run — which is what PROVE drove — has no `spawns.log` and no
    stream roster, so NO agent resolves and every one of them is unknown. That
    is the honest answer, and exempting it would have left the filed drive
    unfixed while adding a second condition the contract does not state.
    """
    project_root, fdir = run_env
    _write_state(fdir, phase="F3", cycle=1)

    result = foundry_record_spend("casting-3", "F3", 10, 10,
                                  project_root=project_root)

    assert result["ok"] is True, result
    said = " ".join(result["warnings"])
    assert "no dispatch" in said and "nothing yet" in said, said


# --------------------------------------------------------------------------- #
# GI-005 / AC-033 / OT-023 — no parser, and no money
# --------------------------------------------------------------------------- #


def _server_package_modules() -> list[Path]:
    """Every module of the server package — GI-005's actual subject.

    D-226. This guard's docstring said it was asserted against the source of
    every module that touches spend, and its body read the orchestrator's
    source alone. `foundry_report.py`, `foundry_state.py` and
    `display.py` all touch spend and none was scanned, so a transcript tail
    added to any of them would not have tripped it.

    The subject here is WIDER than the docstring's old claim on purpose:
    GI-005 says "Parser stays out of the SERVER", and its violation column
    names "Server code that tails transcript JSONL or regex-parses the Agent
    usage block" without qualifying which module. A guard scoped to the
    spend-touching modules would be the same defect one step out — a parser in
    `foundry.py` or `evidence.py` is exactly as much a violation and would ship
    unchallenged.

    fallout FR-005 / OT-016 — THE ROOT IS DERIVED FROM THE PACKAGE, NOT COUNTED
    FROM A MODULE.

    This was `Path(fo.__file__).resolve().parents[1]`, which named
    `foundry_mcp` only because the single orchestrator module sat exactly two
    directories down under `tools/`. The thirteen split modules sit one deeper,
    so
    the same arithmetic now names `tools/` and the scan reads a third of the
    package — a silent shrinkage the `foundry_mcp` assertion below would not
    have caught, because `tools` is a real directory holding real modules.
    Derived from the PACKAGE's own `__file__` the way
    `test_spawn_progress.py#_plugin_root` already does, so no future move of a
    module can change what this names.
    """
    root = Path(foundry_mcp.__file__).resolve().parent
    assert root.name == "foundry_mcp", root
    modules = sorted(root.rglob("*.py"))
    assert len(modules) > 10, modules
    return modules


def _shipped_orchestration_modules() -> list[Path]:
    """Every module the orchestration package SHIPS — what `fo` used to mean.

    fallout FR-005 / AC-014 / OT-016 (D-183) — DERIVED FROM WHAT SHIPS, NOT
    FROM A ROSTER SOMEBODY HAS TO REMEMBER.

    The two scans below read `ORCHESTRATION`, a hand-typed tuple of module
    objects in `tests/orchestration/_env.py`, and said so in a docstring
    promising that "a fourteenth module is required to be scanned the day it
    lands". The fourteenth module had already landed unscanned: `keyfiles.py`
    shipped in cycle 5 and no rule required the tuple to grow, so a
    `context_budget` block or an unnamed module there passed both scans while
    the prose said neither could happen. That is the silent shrinkage OT-016
    exists to catch, in the two pins written to catch it.

    Same derivation as `_server_package_modules` above, one directory down: a
    module cannot ship without entering this corpus, so the promise costs
    nobody a memory.
    """
    pkg_dir = Path(_orchestration_pkg.__file__).resolve().parent
    assert pkg_dir.name == "orchestration", pkg_dir
    modules = sorted(p for p in pkg_dir.glob("*.py") if p.name != "__init__.py")
    assert modules, f"the orchestration package is empty; the scan reads nothing: {pkg_dir}"

    # The corpus can only be WIDER than the tuple the suite imports, never
    # narrower — the direction D-183 broke. Asserted rather than assumed, so a
    # glob that stopped matching is loud instead of green.
    unscanned = {Path(m.__file__).name for m in ORCHESTRATION} - {
        p.name for p in modules
    }
    assert not unscanned, sorted(unscanned)
    return modules


#: Literal spellings only a reader of a harness-owned format carries: the
#: session transcript's directory and compressed form, and the line the Agent
#: usage block prints. Scanned over source with comment lines stripped, because
#: a module's own prose explaining that it deliberately parses nothing is not a
#: parser and a scan that could not tell those apart would forbid documenting
#: the requirement it enforces.
_PARSER_LITERALS = (".claude/projects", ".jsonl.gz", "Total cost")

#: The names a transcript or usage-block reader gives its own helpers. Scanned
#: over IDENTIFIERS rather than over source, because at package width the bare
#: word is prose three times over — `server.py`'s Foundry-Intent-Coverage
#: description says "transcript-in-spec-appendix", its Foundry-Spend
#: description states this very rule, and `test_deriver.py` reads forge's
#: `transcript.md`, which is a spec artifact and not a session transcript. A
#: scan that flagged those would be unusable and would be switched off, which
#: is a worse outcome than a narrow one.
_PARSER_IDENTIFIER_TOKENS = ("transcript", "usage_block")


def _module_identifiers(source: str) -> set[str]:
    """Every name a module BINDS or REFERENCES, with no string or comment."""
    names: set[str] = set()
    tree = ast.parse(source)
    for node in ast.walk(tree):
        for attribute in ("name", "id", "attr", "arg", "module"):
            value = getattr(node, attribute, None)
            if isinstance(value, str):
                names.add(value)
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                names.add(alias.name)
                if alias.asname:
                    names.add(alias.asname)
    return names


def _parser_violations(path: Path) -> list[str]:
    """The GI-005 violations one module carries; empty when it carries none."""
    source = path.read_text(encoding="utf-8")
    code = "\n".join(
        line for line in source.splitlines()
        if not line.lstrip().startswith("#")
    )
    found = [
        f"{path.name}: literal {literal!r}"
        for literal in _PARSER_LITERALS
        if literal in code
    ]
    found.extend(
        f"{path.name}: identifier {name!r}"
        for name in sorted(_module_identifiers(source))
        for token in _PARSER_IDENTIFIER_TOKENS
        if token in name.lower()
    )
    return found


def test_the_server_parses_no_transcript_and_no_usage_block(run_env):
    """GI-005 verbatim: 'Parser stays out of the server; the fragile block is
    only ever read by the lead.'

    Asserted against the SOURCE of every module in the server package, because
    the violation is the EXISTENCE of a parser rather than any particular
    output, and the rule's subject is the server rather than any one module.
    The named violation is "Server code that tails transcript JSONL or
    regex-parses the Agent usage block", and both of those would work fine on
    the day they were written — the failure is that the format is nobody's
    contract, so it drifts and the server then reports a wrong number instead of
    no number.

    D-226 — THE SUBJECT USED TO BE ONE MODULE. This read the orchestrator's
    own source while claiming the scope above, so the four other
    spend-touching modules, and every module beyond them, were unguarded. See
    `_server_package_modules` for why the fix widens past the modules that
    touch spend rather than to exactly them.
    """
    import inspect

    violations = [
        finding
        for module in _server_package_modules()
        for finding in _parser_violations(module)
    ]
    assert violations == [], violations

    # The ONE channel: the lead types the numbers into this handler.
    params = inspect.signature(foundry_record_spend).parameters
    assert set(params) == {
        "agent", "phase", "tokens", "duration_ms", "cycle", "project_root",
    }


def test_the_parser_guard_covers_the_whole_server_package(run_env):
    """D-226's first half: the guard's SUBJECT is what its docstring claims.

    Named modules rather than a count alone, because a count passes on any
    roster of the right size. These are the ones the defect named as touching
    spend and going unscanned; `server.py` and `foundry.py` are here because
    GI-005's subject is the server and neither touches spend at all.

    fallout FR-005 / OT-016 — THE ORCHESTRATOR WAS ONE OF THESE NAMES.
    It is the whole `orchestration/` package now, and the roster names every
    module of it rather than only `orchestration/spend.py`: a roster narrowed
    to the split module that touches spend would be D-226 recommitted one
    directory down, which is the same defect the docstring above refuses one
    step out. Derived from the SHIPPED PACKAGE rather than typed, so a
    fourteenth module is required to be scanned the day it lands and nobody
    has to remember a second list — which is the promise D-183 found this
    roster making while `keyfiles.py` sat outside it.
    """
    scanned = {path.name for path in _server_package_modules()}
    orchestration = {path.name for path in _shipped_orchestration_modules()}
    assert orchestration, "the orchestration set is empty; this roster names nothing"
    assert "spend.py" in orchestration, sorted(orchestration)
    for module in (
        *sorted(orchestration), "foundry_report.py", "foundry_state.py",
        "display.py", "foundry_spawn.py", "server.py", "foundry.py",
        "evidence.py", "vocab.py",
    ):
        assert module in scanned, (module, sorted(scanned))


def test_the_parser_guard_would_catch_a_parser_wherever_it_was_added(tmp_path):
    """D-226's second half: the guard is not vacuous.

    A widened subject is worth nothing if the detector cannot fire, and this
    detector deliberately reads literals and identifiers on DIFFERENT surfaces
    — prose is exempt from the identifier scan and the literal scan is the only
    reader of strings — so each surface is driven on its own. The negative case
    is the one that matters most: prose stating the rule must stay legal, or
    the guard becomes something a future author switches off rather than obeys.
    """
    parser = tmp_path / "violator.py"
    for literal in _PARSER_LITERALS:
        parser.write_text(f'PATH = "{literal}/x"\n', encoding="utf-8")
        assert _parser_violations(parser), literal
    for token in _PARSER_IDENTIFIER_TOKENS:
        parser.write_text(f"def tail_{token}(path):\n    return path\n",
                          encoding="utf-8")
        assert _parser_violations(parser), token

    # ...and PROSE about the rule is not a violation of it.
    clean = tmp_path / "documented.py"
    clean.write_text(
        '"""This server parses no transcript and no Agent usage block, ever."""\n'
        "# tails no transcript, reads no usage block\n"
        'DESCRIPTION = "the transcript-in-spec-appendix, not a session transcript"\n',
        encoding="utf-8",
    )
    assert _parser_violations(clean) == []


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

    # Code only, for the same reason as the parser scan above: the comment
    # explaining why the block was removed necessarily names it.
    #
    # fallout FR-005 / OT-016 — READ OVER THE SET, NOT OVER ONE MODULE.
    # This read the orchestrator's own source, and the orchestrator is the
    # whole `orchestration/` package now. Concatenating every module it ships
    # catches a `context_budget` block reintroduced in `guidance.py` — the
    # module that actually renders the next action — where a scan repointed at
    # any single module would have missed it.
    #
    # fallout AC-014 (D-183) — CONCATENATED FROM WHAT SHIPS, NOT FROM A TUPLE.
    # This called `orchestration_source()`, whose corpus WAS the hand-typed
    # `ORCHESTRATION`, so `keyfiles.py` — shipped in cycle 5 and never added —
    # was outside the only scan that could have caught a budget block in it.
    # D-183 has since rederived that tuple off the package too, so the roster a
    # reader would go looking for no longer exists to be found.
    source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in _shipped_orchestration_modules()
    )
    assert source.strip(), "the orchestration source is empty; the scan reads nothing"
    code = "\n".join(
        line for line in source.splitlines()
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

    status_src = inspect.getsource(_guidance._format_status_display)
    code = "\n".join(
        line for line in status_src.splitlines()
        if not line.lstrip().startswith("#")
    )
    for label in ("Inspect:", "Spend:", "Server:", "HALTED:"):
        assert label not in code, f"_format_status_display still draws {label}"

    # D-137 — THE LABEL, NOT THE VOCABULARY CONSTANT.
    #
    # This checked the bare token "HALTED", which the halt DETAIL line draws as
    # "HALTED:" in display.py. That is the fact D-018 forbids a second copy of,
    # and the assertion now names it with its colon. The bare token also
    # matched `RUN_PHASE_HALTED` — vocab's own constant — so the guard could
    # not tell "this renderer draws the halt line" from "this renderer READS
    # the phase vocabulary", and the second is what every branch in this
    # module is required to do rather than re-type a literal.
    #
    # So the constant is asserted PRESENT: `_format_status_display` compares
    # the phase against it to render the banner ONCE. Before D-137 the banner
    # read "F O U N D R Y  HALTED HALTED", because `phase_names` has no entry
    # for the halt and the fallback repeats the token — a stutter that is this
    # renderer's own fact to get right (CT-016 / NFR-005), and not a second
    # copy of anyone else's.
    assert "RUN_PHASE_HALTED" in code, (
        "the banner must compare against vocab's constant, never a literal"
    )
    assert '"HALTED"' not in code and "'HALTED'" not in code, (
        "the phase token is READ from vocab here, never re-typed"
    )
    rendered_halt = format_result("Foundry-Next", foundry_next_action(project_root))
    assert "HALTED HALTED" not in rendered_halt


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

    summary = _spend._spend_summary(fdir)

    assert summary["total"]["unreported"] == 2
    # D-048: bucketed under the RUN PHASE the verb maps to, never under the verb
    # itself. `by_phase["grind"]` was a phantom bucket keyed by a dispatch verb.
    assert summary["by_phase"]["F1"]["unreported"] == 1
    assert summary["by_phase"]["F3"]["unreported"] == 1
    assert "cast" not in summary["by_phase"] and "grind" not in summary["by_phase"]
    # ...and the persisted document carries it too, rather than the zero C-4's
    # shape would otherwise leave sitting in state.json forever.
    assert _read_state(fdir)["spend"]["total"]["unreported"] == 2


def test_next_and_the_report_state_one_unreported_count(run_env):
    """AC-034 verbatim: 'A dispatched agent with no spend record is shown as
    unreported in Foundry-Next and the report, and no gate refuses on it.'

    ONE run, ONE question, and D-162 is the two numbers it used to get.
    `_overlay_unreported` consumed `_unreported_dispatches`' ROW list, which
    `_dispatched_agents` re-expands into one row per cycle stamp for every F2
    stream agent, and then incremented `by_phase` per ROW with
    `total["unreported"] = len(rows)`. The F6 report reads PAIRS. Driven
    through the real doors on a copy of this run's archive: Foundry-Next
    returned `unreported_count 51` and rendered "Unreported: 51" with
    `by_phase unreported {F1 8, F3 6, F2 37}`, while
    `foundry_report._read_unreported_dispatches` on the same archive returned
    `count 19` with `by_phase {F1 8, F2 5, F3 6}` — and start.md's SPEND
    ACCOUNTING section describes both surfaces as one set.

    The trigger is an F2 stream agent unreported across more than one cycle,
    which is the normal shape of a real run, so the fixture is exactly that:
    one stream that ran in three cycles and never reported.
    """
    from foundry_mcp.tools.foundry_report import _read_unreported_dispatches

    project_root, fdir = run_env
    _write_state(fdir, phase="F2", cycle=3)
    _write_spawns(fdir, [{"casting_id": 2, "phase": "cast"}])
    (fdir / "stream-rollup.json").write_text(
        json.dumps({"cycles": {
            str(c): {"trace": {"records": [{"cycle": c}]}} for c in (1, 2, 3)
        }}),
        encoding="utf-8",
    )

    summary = _spend._spend_summary(fdir)
    report_side, problem = _read_unreported_dispatches(fdir)
    assert problem is None, problem

    # ONE stream agent across THREE cycles is ONE unreported pair, and the two
    # surfaces say the same integer because they are one derivation.
    assert summary["unreported_count"] == report_side["count"], (
        summary["unreported_count"], report_side["count"]
    )
    assert summary["unreported_count"] == 2, summary        # trace + casting-2
    assert summary["by_phase"]["F2"]["unreported"] == 1, summary["by_phase"]
    assert summary["total"]["unreported"] == report_side["count"]

    # ...while `by_cycle` stays the per-cycle axis: the stream really was
    # missed in each of the three cycles, and that is what a cycle bucket is
    # for. The two axes are allowed to differ; what is not allowed is two
    # answers to the SAME axis.
    assert [
        summary["by_cycle"][str(c)]["unreported"] for c in (1, 2, 3)
    ] == [1, 1, 1], summary["by_cycle"]
    assert sum(
        bucket["unreported"] for bucket in summary["by_cycle"].values()
    ) >= summary["unreported_count"]

    # And the row list is still row-shaped, so a reader sees the stream under
    # each cycle it was missed in.
    assert len(summary["unreported_dispatches"]) == 4
    assert summary["unreported_rows"] == 4

    # AC-034's last clause, on the same fixture: no gate refuses on any of it.
    for phase in ("assay", "temper", "nyquist", "done"):
        result = foundry_gate(phase, project_root)
        assert "unreported" not in str(result.get("reason") or "").lower(), (
            phase, result
        )


def test_a_phase_with_no_recorded_spend_still_gets_its_unreported_count(run_env):
    """The run the count matters most on: the lead called Foundry-Spend for
    nothing at all. A bucket that only exists once someone reports would hide
    exactly that run."""
    project_root, fdir = run_env
    _write_state(fdir, phase="F1", cycle=0)
    _write_spawns(fdir, [{"casting_id": 7, "phase": "cast"}])

    summary = _spend._spend_summary(fdir)

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

    assert _spend._unreported_dispatches(fdir) == [
        {"agent": "casting-3", "phase": "F1"}
    ]

    foundry_record_spend("casting-3", "F1", 1_000, 1_000,
                         project_root=project_root)

    assert _spend._unreported_dispatches(fdir) == []
    assert _spend._spend_summary(fdir)["by_phase"]["F1"]["tokens"] == 1_000


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

    assert _spend._unreported_dispatches(fdir) == []
    assert _spend._spend_summary(fdir)["by_phase"]["F1"]["tokens"] == 1_000
    assert "cast" not in _spend._spend_summary(fdir)["by_phase"]
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

    assert _spend._unreported_dispatches(fdir) == [
        {"agent": "casting-3", "phase": "F3"}
    ]
    summary = _spend._spend_summary(fdir)
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
        (row["agent"], row["phase"]) for row in _spend._unreported_dispatches(fdir)
    }
    assert from_next == from_report == {("casting-4", "F3")}


# --------------------------------------------------------------------------- #
# D-179 / AC-034 / FR-022 — THE NUMBER ON SCREEN IS THE NUMBER THE RESULT STATES
#
# `format_result_blocks` puts a rendered box and the whole result dict on ONE
# response, so every integer the box draws sits beside the field it claims to
# report. Where the display derives its own, the two are free to disagree — and
# they did: driven at the real MCP surface on this run's live archive, one
# Foundry-Next response carried `spend.unreported_count 19` in its JSON and
# rendered "Unreported: 58" in the box above it, then spelled each entry
# "agent@phase" so `prove@F2` printed ten times under a header that counted
# per-cycle ROWS. `_fmt_foundry_record_spend` held the same expression on the
# same two keys.
#
# D-162 fixed this one rung lower — `_overlay_unreported` had the same two
# axes and the same wrong pick — and the class survived because the renderer
# kept a derivation of its own and the D-162 guard asserted only over the
# `_spend_summary` dict, never over a rendered line. So these drive the WIRE,
# and the last one fails when a new labelled count line is added without being
# tied to the field its result states.
# --------------------------------------------------------------------------- #


def _drive_mcp_text(name: str, arguments: dict) -> str:
    """Call a tool through the MCP REQUEST HANDLER, not through `call_tool`.

    The transport a client actually uses, and the same helper shape
    `test_inspect_mode.py`'s D-173 pin uses: this defect is about what a lead
    READS beside what a stream PARSES, and both only exist on the far side of
    the boundary.
    """
    import asyncio

    from mcp import types

    import foundry_mcp.server as srv

    handler = srv.server.request_handlers[types.CallToolRequest]
    request = types.CallToolRequest(
        method="tools/call",
        params=types.CallToolRequestParams(name=name, arguments=arguments),
    )
    return asyncio.run(handler(request)).root.content[0].text


def _plain(text: str) -> str:
    import re

    return re.sub(r"\x1b\[[0-9;]*m", "", text)


def _halves(text: str) -> tuple[str, dict]:
    """(the display the lead reads, the result dict the stream parses)."""
    from foundry_mcp.tools.display import RESULT_JSON_MARKER

    plain = _plain(text)
    assert RESULT_JSON_MARKER in plain, "no machine-readable block in the response"
    head, _, body = plain.partition(RESULT_JSON_MARKER + "\n")
    return head, json.loads(body)


def _rendered_int(rendered: str, pattern: str) -> int:
    """The single integer a rendered line states, by the pattern that names it."""
    import re

    match = re.search(pattern, _plain(rendered))
    assert match, f"no line matching {pattern!r} in:\n{_plain(rendered)}"
    return int(match.group(1))


def _two_axis_run(fdir: Path) -> None:
    """The fixture where the row axis and the pair axis DIFFER.

    One stream agent missed in three cycles plus one teammate: four rows, two
    pairs. D-162 names this as the normal shape of a real run, and it is the
    only shape in which the wrong derivation is visible at all — which is why a
    fixture with one dispatch per agent could never have caught either half.
    """
    _write_state(fdir, phase="F2", cycle=3)
    _write_spawns(fdir, [{"casting_id": 2, "phase": "cast"}])
    (fdir / "stream-rollup.json").write_text(
        json.dumps({"cycles": {
            str(c): {"trace": {"records": [{"cycle": c}]}} for c in (1, 2, 3)
        }}),
        encoding="utf-8",
    )


def test_the_unreported_number_foundry_next_renders_is_the_one_it_states(
    run_env, monkeypatch
):
    """AC-034 verbatim: 'A dispatched agent with no spend record is shown as
    unreported in Foundry-Next and the report, and no gate refuses on it.'

    ONE response, BOTH halves, and they must state one integer. The display
    used to render `len(unreported_dispatches)` while the JSON beside it
    carried `unreported_count`; FR-022 asks for "N agents unreported per
    phase", and the row list is neither agents nor agent-phase pairs.
    """
    import foundry_mcp.server as srv

    project_root, fdir = run_env
    _two_axis_run(fdir)
    monkeypatch.setattr(srv, "_project_root", project_root)

    head, payload = _halves(_drive_mcp_text("Foundry-Next", {}))
    spend = payload["spend"]

    # The two axes really do differ on this fixture, so the assertion below is
    # falsifiable rather than trivially true.
    assert spend["unreported_count"] == 2, spend
    assert spend["unreported_rows"] == 4, spend
    assert len(spend["unreported_dispatches"]) == 4, spend

    assert _rendered_int(head, r"Unreported:\s+(\d+)\s") == spend["unreported_count"]

    # ...and the names beside it are on that same axis: one entry per pair, not
    # one per cycle the stream was missed in.
    line = next(ln for ln in _plain(head).splitlines() if "Unreported:" in ln)
    assert line.count("trace@F2") == 1, line
    assert line.count("casting-2@F1") == 1, line


def test_the_unreported_number_the_spend_door_renders_is_the_one_it_states(
    run_env, monkeypatch
):
    """CT-013 verbatim: 'none; unreported dispatches are listed, never refused.'

    The second surface that held the same expression. `foundry_record_spend`
    published the row LIST and no count at all, so its formatter had nothing to
    read and derived one — the adjacent path that made this a class rather than
    a line. Both keys now come off the one `_spend_summary` call the handler
    already makes.
    """
    import foundry_mcp.server as srv

    project_root, fdir = run_env
    _two_axis_run(fdir)
    monkeypatch.setattr(srv, "_project_root", project_root)

    head, payload = _halves(_drive_mcp_text("Foundry-Spend", {
        "agent": "casting-9", "phase": "F1", "tokens": 1_000, "duration_ms": 60_000,
    }))

    assert payload["ok"] is True, payload
    assert payload["unreported_count"] == 2, payload
    assert payload["unreported_rows"] == 4, payload
    assert len(payload["unreported_dispatches"]) == 4, payload

    assert _rendered_int(head, r"Unreported:\s+(\d+)\s") == payload["unreported_count"]
    assert "Unreported: 4" not in _plain(head), _plain(head)


#: Every labelled Foundry-Next fact line that renders an INTEGER, tied to where
#: the result states it. `stated` is the path to the field the result publishes;
#: `rows` is the list the number is about. Where `stated` is None the result
#: publishes no count, so `len(rows)` is the ONE derivation and there is nothing
#: for it to disagree with — which is the whole rule this table encodes.
_NEXT_COUNT_LINES = (
    ("Unreported", r"Unreported:\s+(\d+)\s",
     ("spend", "unreported_count"), ("spend", "unreported_dispatches")),
    ("Spend", r"over (\d+) reported agent\(s\)",
     ("spend", "total", "agents"), None),
    # D-189: the second integer on the Spend line. Registered here for the same
    # reason every other one is — a count on the screen that no result field
    # states is the D-179 shape, and this line now carries two.
    ("Spend", r",\s+(\d+) matching no dispatch",
     None, ("spend", "unmatched_agents")),
    ("Waiting", r"Waiting:\s+(\d+) agent\(s\)",
     ("waiting_on_agents", "count"), None),
    ("PROVE", r"PROVE:\s+(\d+) row\(s\)",
     None, ("inspect_mode", "prove_sample")),
    ("TRACE", r"TRACE:\s+(\d+) file\(s\)",
     None, ("inspect_mode", "touched_files")),
)

#: The labelled lines that render no integer at all, so the label set below can
#: be asserted whole. A new counted line lands in neither collection and fails
#: `test_every_counted_next_line_renders_the_integer_its_result_states`.
_NEXT_UNCOUNTED_LABELS = frozenset({
    "Inspect", "Roster", "Skipped", "By Phase", "By Cycle", "Server", "HALTED",
})


def _dig(payload: dict, path: tuple[str, ...]):
    value = payload
    for key in path:
        value = value[key]
    return value


def test_every_counted_next_line_renders_the_integer_its_result_states():
    """FR-022 / AC-033 / NFR-005 — the class, not the instance.

    D-179 was one line of `_fmt_foundry_next_lines`; the reason it is a
    STRUCTURAL packet is that nothing stopped the next line from doing the same
    thing. So this drives the renderer with a result whose stated integers all
    DIFFER from the lengths of the lists they describe — five unreported rows
    collapsing to two pairs, three reported agents, four waiting, seven PROVE
    rows, six touched files — and asserts each rendered integer against the
    field the result states rather than against a literal.

    The label assertion is the part that has to survive the next author: every
    labelled line this function emits is either in the table above (and is
    pinned to a stated field) or in the uncounted set. A new counted line is in
    neither, and this fails until it is tied to the field its result states.
    """
    import re

    from foundry_mcp.tools.display import _fmt_foundry_next_lines

    payload = {
        "phase": "HALTED",
        "details": {"halted_reason": "--max-cycles 2 reached"},
        "inspect_mode": {
            "mode": "DELTA", "rule": "delta", "decided_by": "inspect_start",
            "required_streams": ["trace", "prove", "test"],
            "stream_scope": {
                "trace": {"scope": "delta", "detail": "symbols in 6 file(s)"},
                "test01": {"scope": "skipped", "detail": "untouched"},
            },
            "prove_sample": [f"FR-{n:03d}" for n in range(1, 8)],
            "touched_files": [f"src/mod_{i}.py" for i in range(6)],
        },
        "spend": {
            "total": {"tokens": 12_345, "duration_ms": 120_000, "agents": 3},
            "by_phase": {"F2": {"tokens": 12_345, "duration_ms": 120_000}},
            "by_cycle": {"3": {"tokens": 12_345, "duration_ms": 120_000}},
            "unreported_dispatches": [
                {"agent": "trace", "phase": "F2", "cycle": c} for c in (1, 2, 3)
            ] + [
                {"agent": "casting-2", "phase": "F1"},
                {"agent": "casting-2", "phase": "F1"},
            ],
            "unreported_count": 2,
            # D-189: two agents the dispatch record could not match, a length
            # that differs from every other integer in this payload so the
            # renderer cannot pass by reading the wrong one.
            "unmatched_agents": ["casting-l", "who-is-this"],
        },
        "executing_server": {
            "server_version": "4.10.0", "plugin_version": "4.10.0",
            "server_root": "/repo/plugins/foundry", "server_commit": "3f9c1a284d6b",
        },
        "waiting_on_agents": {"waiting": True, "count": 4,
                              "detail": "oldest progress 4m 0s ago"},
    }

    rendered = "\n".join(_fmt_foundry_next_lines(payload))

    for label, pattern, stated, rows in _NEXT_COUNT_LINES:
        expected = (
            _dig(payload, stated) if stated is not None
            else len(_dig(payload, rows))
        )
        assert _rendered_int(rendered, pattern) == expected, (
            f"{label}: the screen and the result disagree"
        )

    # The row axis never reaches the screen as a count.
    assert len(payload["spend"]["unreported_dispatches"]) == 5
    assert "Unreported: 5" not in _plain(rendered), _plain(rendered)

    labels = {
        m.group(1)
        for m in re.finditer(
            r"^\s+([A-Za-z][A-Za-z ]*):", _plain(rendered), flags=re.MULTILINE
        )
    }
    assert labels == {row[0] for row in _NEXT_COUNT_LINES} | _NEXT_UNCOUNTED_LABELS, (
        "a labelled Foundry-Next line is in neither collection — if it renders a "
        "count, add it to _NEXT_COUNT_LINES with the field the result states; if "
        "it renders none, add it to _NEXT_UNCOUNTED_LABELS"
    )


def test_the_spend_cycle_axis_renders_in_counter_order_not_lexicographic():
    """FR-037 verbatim: 'roll-ups are keyed by the server cycle counter'.
    AC-033 verbatim: 'Foundry-Next shows tokens and minutes per phase and per
    cycle and the run total'.

    D-220. `_fmt_foundry_next_lines` walked `by_phase` and `by_cycle` through
    ONE `sorted(buckets.items())`, which orders the STRINGIFIED key. The phase
    keys happen to sort correctly under that rule and the cycle keys — `str(cycle)`
    per C-4 — do not, so the live run rendered "0: ... 1: ... 10: ... 11: ...
    2: ... 20: ... 23: 1,430,191tok/145m  3: ..." on the one line that exists to
    show what each cycle cost.

    The keys below are chosen so lexicographic and numeric order DISAGREE at
    three places (10 before 2, 11 before 2, 20 and 21 before 3), so a renderer
    that reverts to `sorted()` cannot pass by accident. The expected order is
    derived from `foundry_report._cycle_sort_key` — the function this surface
    now consults — rather than typed out, because a literal list here would be
    a fourth derivation of the same fact.
    """
    import re

    from foundry_mcp.tools.display import _fmt_foundry_next_lines
    from foundry_mcp.tools.foundry_report import _cycle_sort_key

    cycles = ["0", "1", "2", "3", "9", "10", "11", "20", "21"]
    payload = {
        "spend": {
            "total": {"tokens": 9_000, "duration_ms": 60_000, "agents": 9},
            "by_phase": {"F1": {"tokens": 1, "duration_ms": 0},
                         "F2": {"tokens": 2, "duration_ms": 0},
                         "F3": {"tokens": 3, "duration_ms": 0},
                         "F5.5": {"tokens": 4, "duration_ms": 0}},
            "by_cycle": {c: {"tokens": int(c), "duration_ms": 0} for c in cycles},
        },
    }

    rendered = _plain("\n".join(_fmt_foundry_next_lines(payload)))

    def _axis(label: str) -> list[str]:
        match = re.search(rf"^\s*{label}:\s+(.*)$", rendered, re.MULTILINE)
        assert match, f"no {label} line in:\n{rendered}"
        return re.findall(r"([^\s]+): [\d,]+tok/\d+m", match.group(1))

    assert _axis("By Cycle") == sorted(cycles, key=_cycle_sort_key), rendered
    # The defect's own signature, stated as the thing that must NOT be true:
    # 10 and 11 no longer precede 2, and 20 and 21 no longer precede 3.
    assert _axis("By Cycle") != sorted(cycles), rendered


def test_the_spend_phase_axis_is_unmoved_by_the_cycle_key_function():
    """FR-021 / AC-033 — THE ADJACENT AXIS THROUGH THE SAME LOOP.

    `_fmt_foundry_next_lines` walks `("by phase", "by_phase")` and
    `("by cycle", "by_cycle")` through ONE shared `sorted(...)`, so the key
    function D-220 installed for the cycle keys is applied to the phase keys
    too. This drives THAT transition rather than the one the defect was found
    on: phase tokens are non-numeric, `_cycle_sort_key` maps every one of them
    to `(1, raw)`, and they must therefore land in exactly the lexicographic
    order the bare `sorted()` gave them — "F5.5" after "F5" and before "F6",
    and no phase displaced by a numeric key that does not exist on this axis.

    Written because the fix's whole risk lives here: an ordering change made
    for one axis of a shared walk that silently reorders the other is a worse
    defect than the one being fixed, and nothing else in this file drives the
    phase axis with enough keys to see it.
    """
    import re

    from foundry_mcp.tools.display import _fmt_foundry_next_lines

    phases = ["F0", "F1", "F2", "F3", "F4", "F5", "F5.5", "F6"]
    payload = {
        "spend": {
            "total": {"tokens": 8, "duration_ms": 0, "agents": 8},
            "by_phase": {p: {"tokens": 1, "duration_ms": 0} for p in phases},
            "by_cycle": {"1": {"tokens": 1, "duration_ms": 0}},
        },
    }

    rendered = _plain("\n".join(_fmt_foundry_next_lines(payload)))
    match = re.search(r"^\s*By Phase:\s+(.*)$", rendered, re.MULTILINE)
    assert match, f"no By Phase line in:\n{rendered}"
    shown = re.findall(r"([^\s]+): [\d,]+tok/\d+m", match.group(1))

    assert shown == sorted(phases), rendered
    assert shown == phases, rendered


def test_the_renderer_consults_the_cycle_key_function_rather_than_copying_it():
    """FR-021 / AC-033 — the CLASS behind D-220, which is
    'two-derivations-of-one-fact-disagree'.

    `_cycle_sort_key` already existed twice before this fix — in
    `foundry_report` and in `scripts/measure-run.py` — and the Foundry-Next
    display consulted neither, which is how one document acquired two orderings
    depending on which surface printed it. A third copy inside `display.py`
    would fix the instance and widen the class, so this asserts the shape of
    the fix and not only its effect: the renderer imports the spelling, and
    defines none of its own.

    fallout GI-024 / OT-016 — AND THE SPELLING MOVED TO THE LEAF.
    This pinned the literal `from foundry_mcp.tools.foundry_report import
    _cycle_sort_key`. Casting 10 consolidated the two copies into
    `foundry_state.cycle_sort_key` (Holmes `share-2`), so `foundry_report` now
    holds an alias rather than the definition and the renderer reaches the leaf
    directly. The claim is unchanged — one definition, everyone else imports it
    — so the assertion is written against the module that DEFINES the symbol,
    resolved from the function object rather than from a typed path.
    """
    from pathlib import Path

    import foundry_mcp.tools.display as display_mod
    from foundry_mcp.tools.foundry_state import cycle_sort_key

    owner = cycle_sort_key.__module__
    assert owner == "foundry_mcp.tools.foundry_state", owner

    source = Path(display_mod.__file__).read_text(encoding="utf-8")
    assert "def _cycle_sort_key" not in source, (
        "display.py defines its own cycle-ordering key — import the one "
        "`foundry_state` already owns instead of adding a third copy"
    )
    assert "def cycle_sort_key" not in source, (
        "display.py defines the key under the leaf's own spelling — the "
        "second copy the underscore spelling above was renamed away from"
    )
    assert f"from {owner} import cycle_sort_key" in source, (
        "the spend renderer must consult the existing cycle key function, "
        f"imported from {owner} — the module that defines it"
    )


# --------------------------------------------------------------------------- #
# CT-013 / FR-022 / FR-037 / AC-033 — D-229: a cycle the run never measured
# gets no row, because an absent row is honest where a zero row is a claim
# --------------------------------------------------------------------------- #


def _rollup_with_stream_in_cycles(fdir: Path, stream: str, cycles: tuple[int, ...]) -> None:
    """The only record that an F2 stream agent was dispatched at all (C-5)."""
    (fdir / "stream-rollup.json").write_text(
        json.dumps({"cycles": {
            str(c): {stream: {"records": [{"cycle": c}]}} for c in cycles
        }}),
        encoding="utf-8",
    )


def test_a_cycle_the_run_never_measured_gets_no_row_at_all(run_env):
    """FR-037 / AC-033 / CT-013 — D-229, driven at the doors that publish it.

    THE DEFECT. `_overlay_unreported` did
    `spend["by_cycle"].setdefault(str(cycle), _empty_spend_bucket())` for every
    cycle the unreported summary named, and `foundry_record_spend` persisted the
    result into state.json. When the pairs later cleared, the loop only reset
    `bucket["unreported"] = 0` on the buckets already there and nothing removed
    a bucket now holding 0/0/0/0. Compounding it, the summary's cycle axis is
    keyed on the (agent, phase) PAIR, so the moment a stream reported spend at
    F2 EVERY cycle stamp that stream carried cleared at once. Driven on the real
    run: `Foundry-Next` returned 23 all-zero `by_cycle` buckets and rendered
    "By Cycle: 0: 0tok/0m  1: 0tok/0m ... 22: 0tok/0m", and the F6 report wrote
    23 rows reading `| cycle | 5 | 0 | 0.0 | 0 | 0 | 0 |` while
    stream-rollup.json records the full stream roster running in cycle 5. Both
    surfaces stated that 23 of the run's 27 cycles cost zero tokens, took zero
    minutes, ran zero agents and had ZERO unreported dispatches — every one of
    which is false.

    THE SEQUENCE IS THE WHOLE TRIGGER and it is driven here end to end: a spend
    recorded while a stream is still unreported SEEDS the stream's cycle
    buckets, and the stream's own later spend record CLEARS them without
    removing them.
    """
    project_root, fdir = run_env
    _write_state(fdir, phase="F2", cycle=5)
    _write_spawns(fdir, [{"casting_id": 2, "phase": "cast"}])
    _rollup_with_stream_in_cycles(fdir, "trace", (1, 2, 3))

    # 1. The seed. `trace` is dispatched in cycles 1-3 and has reported nothing,
    #    so the overlay writes a bucket for each of those cycles — which is
    #    right, and is the case FR-022 exists for.
    first = foundry_record_spend(
        agent="casting-2", phase="F1", tokens=10, duration_ms=60_000,
        project_root=project_root,
    )
    assert first["ok"] is True, first
    seeded = _read_state(fdir)["spend"]["by_cycle"]
    assert {"1", "2", "3"} <= set(seeded), seeded
    assert [seeded[str(c)]["unreported"] for c in (1, 2, 3)] == [1, 1, 1], seeded

    # 2. The clear. One spend record for the pair, and every cycle stamp it
    #    carried stops being unreported at once.
    second = foundry_record_spend(
        agent="trace", phase="F2", tokens=20, duration_ms=60_000,
        project_root=project_root,
    )
    assert second["ok"] is True, second

    # 3. The buckets left behind measured NOTHING, so there are no buckets.
    persisted = _read_state(fdir)["spend"]["by_cycle"]
    assert set(persisted) == {"5"}, persisted
    assert persisted["5"]["tokens"] == 30, persisted

    # The read path says the same, because it runs the same overlay.
    summary = _spend._spend_summary(fdir)
    assert set(summary["by_cycle"]) == {"5"}, summary["by_cycle"]

    # ...and the roll-up that survived is the one that carries a measurement.
    for key, bucket in summary["by_cycle"].items():
        assert any(
            bucket.get(field) for field in
            ("tokens", "duration_ms", "agents", "unreported")
        ), (key, bucket)


def test_neither_foundry_next_nor_the_report_renders_an_unmeasured_cycle(run_env):
    """D-229 — the two surfaces the zero rows reached, driven at both.

    AC-033 makes the Foundry-Next line the one a lead reads tokens and minutes
    per cycle on, and AC-036 puts the same axis in the F6 report. Both read the
    persisted `by_cycle`, so both published the unmeasured zeros; this asserts
    neither does, on one fixture, so a fix that repaired only the display would
    fail here.
    """
    from foundry_mcp.tools.display import _fmt_foundry_next_lines
    from foundry_mcp.tools.foundry_report import generate_report

    project_root, fdir = run_env
    _write_state(fdir, phase="F2", cycle=5)
    (fdir / "spec.md").write_text("# Spec\n- FR-1: a requirement\n", encoding="utf-8")
    _write_spawns(fdir, [{"casting_id": 2, "phase": "cast"}])
    _rollup_with_stream_in_cycles(fdir, "trace", (1, 2, 3))

    foundry_record_spend(
        agent="casting-2", phase="F1", tokens=10, duration_ms=60_000,
        project_root=project_root,
    )
    foundry_record_spend(
        agent="trace", phase="F2", tokens=20, duration_ms=60_000,
        project_root=project_root,
    )

    rendered = "\n".join(_fmt_foundry_next_lines(foundry_next_action(project_root)))
    for cycle in (1, 2, 3):
        assert f"{cycle}: 0tok/0m" not in rendered, rendered
    assert "5: 30tok/2m" in rendered, rendered

    report = generate_report(Path(project_root), fdir)
    assert report.get("ok") is True, report
    markdown = (fdir / "REPORT.md").read_text(encoding="utf-8")
    for cycle in (1, 2, 3):
        assert f"| cycle | {cycle} |" not in markdown, markdown
    assert "| cycle | 5 |" in markdown, markdown


def test_the_overlay_keeps_every_bucket_that_measured_something():
    """D-229's other half, and the one that stops the fix over-reaching.

    The prune is "nothing measured", not "no spend recorded". A cycle with an
    unreported dispatch and no spend at all is the run where the lead forgot
    every `Foundry-Spend` call — the case FR-022 exists to show — and its row
    must stay. Asserted on the helper because the persisted document of a real
    run reaches this function directly (that is how the live run's 23 stale
    buckets are repaired in place, on the next spend call, rather than by hand).
    """
    zero = {"tokens": 0, "duration_ms": 0, "agents": 0, "unreported": 0}
    measured = {"tokens": 1_430_191, "duration_ms": 8_726_752, "agents": 8,
                "unreported": 0}

    cleared = foundry_state.overlay_unreported(
        {"by_phase": {}, "by_cycle": {"5": dict(zero), "23": dict(measured)},
         "total": dict(zero)},
        {"by_phase": {}, "by_cycle": {}, "count": 0},
    )
    assert set(cleared["by_cycle"]) == {"23"}, cleared["by_cycle"]

    still_unreported = foundry_state.overlay_unreported(
        {"by_phase": {}, "by_cycle": {"5": dict(zero), "23": dict(measured)},
         "total": dict(zero)},
        {"by_phase": {"F2": ["trace"]}, "by_cycle": {"5": ["trace"]}, "count": 1},
    )
    assert set(still_unreported["by_cycle"]) == {"5", "23"}, (
        still_unreported["by_cycle"]
    )
    assert still_unreported["by_cycle"]["5"]["unreported"] == 1

    # The PHASE axis is untouched by the prune, and cannot reach all-zero
    # anyway: the spend call that clears a phase's unreported writes that same
    # phase bucket an `agents` count of at least 1. A phase seeded by the
    # overlay alone still carries its unreported and is not a candidate.
    assert set(still_unreported["by_phase"]) == {"F2"}
    assert still_unreported["by_phase"]["F2"]["unreported"] == 1
