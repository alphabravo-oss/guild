"""Real-script tests for the foundry Stop hook, `plugins/foundry/hooks/stop-continue.py`.

The should-not-stop spec's backstop: while a build is live (F1..F5.5, not HALTED
or DONE) and the human has not been asked anything, the lead's turn does not
end. Every test here runs the SHIPPED script in a subprocess, the way Claude
Code runs it — the event JSON on stdin, the decision on stdout — against a
throwaway `foundry-archive/` under ``tmp_path``. Nothing is mocked: the property
under test is what the real script prints for a real tree.

THE ENVIRONMENT IS SCRUBBED OF CLAUDE_PROJECT_DIR on every run unless a test
sets it. The suite itself often runs inside a Claude Code session whose project
has a live foundry run on disk, and the hook falls back to that variable, so an
unscrubbed "no run" case would find the real run and pass or fail by accident.

The run state is arranged with plain ``json.dumps``, spelled with the server's
own constants from ``schemas/vocab.py``, and in two tests written by the park
module's real marker writer. The hook re-types those names because it may not
import the server; arranging with the constants means a rename on the server
side turns these tests red instead of silently opening the hook.
"""

from __future__ import annotations

import ast
import json
import os
import re
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from foundry_mcp.schemas.vocab import (
    AWAITING_FIELD_CYCLE,
    AWAITING_FIELD_ITEM_IDS,
    AWAITING_FIELD_SET_AT,
    PARK_CATEGORY_SPEC_WRONG,
    PARKED_AWAITING_HUMAN_KEY,
    PARKED_FIELD_ANSWER,
    PARKED_FIELD_ANSWER_IS_HALT,
    PARKED_FIELD_ANSWERED_AT,
    PARKED_FIELD_CATEGORY,
    PARKED_FIELD_CYCLE,
    PARKED_FIELD_ID,
    PARKED_FIELD_ITEM_REF,
    PARKED_FIELD_PARKED_AT,
    PARKED_FIELD_QUESTION,
    PARKED_ITEMS_KEY,
    PARKED_STATE_KEY,
    PHASE_NAMES,
    POST_CAST_RUN_PHASES,
    RUN_PHASE_HALTED,
    STOP_TOKEN_FILENAME,
)
from foundry_mcp.tools.orchestration.park import clear_awaiting_human, set_awaiting_human


# tests/ -> mcp-server/ -> foundry/ -> plugins/ -> repo root.
REPO_ROOT = Path(__file__).resolve().parents[4]
HOOKS_DIR = REPO_ROOT / "plugins" / "foundry" / "hooks"
STOP_HOOK = HOOKS_DIR / "stop-continue.py"
SESSION_HOOK = HOOKS_DIR / "session-start-run.py"
SHARED_READER = HOOKS_DIR / "foundry_active_run.py"
STOP_MD = REPO_ROOT / "plugins" / "foundry" / "commands" / "stop.md"

LIVE_PHASES = sorted(POST_CAST_RUN_PHASES)
#: Every phase a run can hold outside the live window: the ladder's other rows
#: (pre-CAST and DONE) plus HALTED, which is not a ladder row.
QUIET_PHASES = [p for p in PHASE_NAMES if p not in POST_CAST_RUN_PHASES] + [RUN_PHASE_HALTED]

TS = "2026-09-11T00:00:00+00:00"
_ABSENT = object()


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------


def _hook_env(project_dir: Path | None = None) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k != "CLAUDE_PROJECT_DIR"}
    if project_dir is not None:
        env["CLAUDE_PROJECT_DIR"] = str(project_dir)
    return env


def _run_stop(
    stdin: str,
    *,
    cwd: Path,
    project_dir: Path | None = None,
    argv: list[str] | None = None,
) -> subprocess.CompletedProcess:
    """Invoke the real shipped Stop hook with ``stdin`` as its event."""
    return subprocess.run(
        argv or [str(STOP_HOOK)],
        input=stdin,
        cwd=str(cwd),
        env=_hook_env(project_dir),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=False,
    )


def _event(cwd: Path, **extra) -> str:
    event = {
        "session_id": "test-session",
        "cwd": str(cwd),
        "hook_event_name": "Stop",
        "stop_hook_active": False,
        "background_tasks": [],
    }
    event.update(extra)
    return json.dumps(event)


def _item(ident: str = "P-001", ref: str = "casting:3", *, answered: bool = False) -> dict:
    return {
        PARKED_FIELD_ID: ident,
        PARKED_FIELD_ITEM_REF: ref,
        PARKED_FIELD_CATEGORY: PARK_CATEGORY_SPEC_WRONG,
        PARKED_FIELD_QUESTION: "Which of the two contradictory rows is right?",
        PARKED_FIELD_CYCLE: 1,
        PARKED_FIELD_PARKED_AT: TS,
        PARKED_FIELD_ANSWER: "the first" if answered else None,
        PARKED_FIELD_ANSWERED_AT: TS if answered else None,
        PARKED_FIELD_ANSWER_IS_HALT: False,
    }


def _marker(*ids: str) -> dict:
    return {AWAITING_FIELD_SET_AT: TS, AWAITING_FIELD_CYCLE: 1, AWAITING_FIELD_ITEM_IDS: list(ids)}


def _parked(items: list[dict], awaiting: dict | None = None) -> dict:
    return {PARKED_ITEMS_KEY: items, PARKED_AWAITING_HUMAN_KEY: awaiting}


def _write_run(
    project: Path,
    name: str,
    phase: object,
    *,
    parked: object = _ABSENT,
    mtime: float | None = None,
) -> Path:
    run_dir = project / "foundry-archive" / name
    run_dir.mkdir(parents=True, exist_ok=True)
    state: dict = {"phase": phase, "cycle": 1}
    if parked is not _ABSENT:
        state[PARKED_STATE_KEY] = parked
    state_path = run_dir / "state.json"
    state_path.write_text(json.dumps(state), encoding="utf-8")
    if mtime is not None:
        os.utime(state_path, (mtime, mtime))
    return run_dir


def _decision(result: subprocess.CompletedProcess) -> dict | None:
    """The hook's decision, or None when it allowed the stop (printed nothing)."""
    assert result.returncode == 0, f"the hook exited {result.returncode}: {result.stderr}"
    if not result.stdout.strip():
        return None
    return json.loads(result.stdout)


def _blocked(result: subprocess.CompletedProcess) -> str:
    """The block reason, asserting the hook blocked."""
    decision = _decision(result)
    assert decision is not None, "the hook allowed the stop when it should have blocked"
    assert decision["decision"] == "block"
    assert decision["reason"].strip()
    return decision["reason"]


def _silent(result: subprocess.CompletedProcess) -> None:
    """Assert the hook allowed the stop: exit 0, nothing on stdout or stderr."""
    assert result.returncode == 0, result.stderr
    assert result.stdout == "", f"the hook printed when it should have allowed: {result.stdout}"
    assert result.stderr == "", f"the hook wrote error text: {result.stderr}"


@pytest.fixture
def project(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    root.mkdir()
    return root


# ---------------------------------------------------------------------------
# The block / allow matrix over the phases
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("phase", LIVE_PHASES)
def test_a_live_run_with_nothing_parked_blocks_naming_the_run_and_phase(project, phase):
    _write_run(project, "brave-otter", phase)
    reason = _blocked(_run_stop(_event(project), cwd=project))
    assert "'brave-otter'" in reason
    assert f"in phase {phase} and" in reason
    assert "call Foundry-Next" in reason


@pytest.mark.parametrize("phase", QUIET_PHASES)
def test_outside_the_live_phases_the_hook_says_nothing(project, phase):
    _write_run(project, "brave-otter", phase)
    _silent(_run_stop(_event(project), cwd=project))


@pytest.mark.parametrize("phase", [None, "", "f2", "F7", ["F2"], {"phase": "F2"}, 2])
def test_a_phase_that_is_no_live_phase_says_nothing(project, phase):
    _write_run(project, "brave-otter", phase)
    _silent(_run_stop(_event(project), cwd=project))


def test_with_no_archive_at_all_the_hook_says_nothing(project):
    _silent(_run_stop(_event(project), cwd=project))


def test_an_archive_with_no_state_files_says_nothing(project):
    (project / "foundry-archive" / "empty-run").mkdir(parents=True)
    (project / "foundry-archive" / "stray.json").write_text("{}", encoding="utf-8")
    _silent(_run_stop(_event(project), cwd=project))


# ---------------------------------------------------------------------------
# The ask marker versus a parked list
# ---------------------------------------------------------------------------


def test_the_ask_marker_lets_the_turn_end(project):
    _write_run(project, "brave-otter", "F3", parked=_parked([_item()], _marker("P-001")))
    _silent(_run_stop(_event(project), cwd=project))


def test_one_parked_item_without_the_marker_still_blocks(project):
    _write_run(project, "brave-otter", "F3", parked=_parked([_item()], None))
    _blocked(_run_stop(_event(project), cwd=project))


def test_a_parked_section_with_no_marker_key_still_blocks(project):
    _write_run(project, "brave-otter", "F3", parked={PARKED_ITEMS_KEY: [_item()]})
    _blocked(_run_stop(_event(project), cwd=project))


def test_many_parked_items_without_the_marker_still_block(project):
    items = [_item("P-001"), _item("P-002", "defect:D-004"), _item("P-003", "stream:prove", answered=True)]
    _write_run(project, "brave-otter", "F2", parked=_parked(items, None))
    _blocked(_run_stop(_event(project), cwd=project))


@pytest.mark.parametrize(
    "parked",
    [
        [],
        "awaiting",
        True,
        {PARKED_AWAITING_HUMAN_KEY: "yes"},
        {PARKED_AWAITING_HUMAN_KEY: True},
        {PARKED_AWAITING_HUMAN_KEY: ["P-001"]},
        {PARKED_AWAITING_HUMAN_KEY: 1},
        {"awaiting-human": _marker("P-001")},
    ],
    ids=["list", "string", "bool", "marker-string", "marker-bool", "marker-list", "marker-int", "misspelt-key"],
)
def test_a_parked_value_of_the_wrong_shape_reads_as_no_marker(project, parked):
    _write_run(project, "brave-otter", "F4", parked=parked)
    _blocked(_run_stop(_event(project), cwd=project))


def test_the_marker_the_park_module_writes_is_the_marker_the_hook_reads(project):
    """End to end over the server's own writer: set, the hook allows; clear, it blocks."""
    run_dir = _write_run(project, "brave-otter", "F3", parked=_parked([_item()], None))
    _blocked(_run_stop(_event(project), cwd=project))

    written = set_awaiting_human(run_dir, ["P-001"])
    assert written is not None and written[AWAITING_FIELD_ITEM_IDS] == ["P-001"]
    _silent(_run_stop(_event(project), cwd=project))

    assert clear_awaiting_human(run_dir) is True
    _blocked(_run_stop(_event(project), cwd=project))


def test_an_ask_naming_no_open_item_is_never_written_so_the_hook_keeps_blocking(project):
    run_dir = _write_run(project, "brave-otter", "F3", parked=_parked([_item(answered=True)], None))
    assert set_awaiting_human(run_dir, ["P-001", "P-999"]) is None
    _blocked(_run_stop(_event(project), cwd=project))


# ---------------------------------------------------------------------------
# background_tasks: naming the agents in flight
# ---------------------------------------------------------------------------


def test_running_background_agents_are_named_and_the_lead_is_told_to_bounded_wait(project):
    _write_run(project, "brave-otter", "F2")
    tasks = [
        {"type": "subagent", "status": "running", "id": "task_001", "name": "prove-stream"},
        {"type": "teammate", "status": "running", "id": "task_002", "description": "casting-4 builder"},
        {"type": "shell", "status": "completed", "id": "task_003", "name": "finished-shell"},
    ]
    reason = _blocked(_run_stop(_event(project, background_tasks=tasks), cwd=project))
    assert "In flight (2)" in reason
    assert 'subagent "prove-stream" (task_001)' in reason
    assert 'teammate "casting-4 builder" (task_002)' in reason
    assert "finished-shell" not in reason
    assert "bounded wait" in reason
    assert "Monitor" in reason
    assert "instead of ending the turn" in reason
    assert "Foundry-Next" in reason


def test_a_task_with_no_status_is_named_as_in_flight(project):
    _write_run(project, "brave-otter", "F2")
    tasks = [{"type": "monitor", "id": "task_009"}]
    reason = _blocked(_run_stop(_event(project, background_tasks=tasks), cwd=project))
    assert 'monitor "task_009"' in reason
    assert "bounded wait" in reason


def test_with_nothing_in_flight_the_reason_says_call_foundry_next(project):
    _write_run(project, "brave-otter", "F2")
    tasks = [{"type": "subagent", "status": "failed", "id": "t1", "name": "gone"}]
    reason = _blocked(_run_stop(_event(project, background_tasks=tasks), cwd=project))
    assert "In flight" not in reason
    assert "bounded wait" not in reason
    assert "Next: call Foundry-Next and follow the step it gives." in reason


@pytest.mark.parametrize("tasks", ["running", {"type": "subagent"}, [1, "two", None, []], None])
def test_a_malformed_background_tasks_value_names_nothing_and_still_blocks(project, tasks):
    _write_run(project, "brave-otter", "F5")
    reason = _blocked(_run_stop(_event(project, background_tasks=tasks), cwd=project))
    assert "In flight" not in reason


def test_a_verbose_task_field_is_quoted_on_one_short_line(project):
    _write_run(project, "brave-otter", "F2")
    tasks = [{"type": "subagent", "status": "running", "id": "t1", "description": "line one\nline two " + "x" * 400}]
    reason = _blocked(_run_stop(_event(project, background_tasks=tasks), cwd=project))
    in_flight = next(line for line in reason.splitlines() if line.startswith("In flight"))
    assert "line one line two" in in_flight
    assert len(in_flight) < 200


def test_stop_hook_active_never_turns_a_block_into_an_allow(project):
    _write_run(project, "brave-otter", "F3")
    _blocked(_run_stop(_event(project, stop_hook_active=True), cwd=project))


# ---------------------------------------------------------------------------
# Error handling: stdin never allows; only unreadable run state does
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("stdin", ["", "not json", "[1, 2]", '{"cwd": ', "null", "��"])
def test_malformed_stdin_still_blocks_from_the_project_dir(project, tmp_path, stdin):
    _write_run(project, "brave-otter", "F2")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    reason = _blocked(_run_stop(stdin, cwd=elsewhere, project_dir=project))
    assert "'brave-otter'" in reason


def test_malformed_stdin_with_no_project_dir_blocks_from_the_working_directory(project):
    _write_run(project, "brave-otter", "F4")
    _blocked(_run_stop("garbage", cwd=project))


@pytest.mark.parametrize("contents", ["{not json", "[1, 2]", "", '"F2"', "null"])
def test_an_unreadable_state_json_lets_the_turn_end_with_no_output(project, contents):
    run_dir = project / "foundry-archive" / "brave-otter"
    run_dir.mkdir(parents=True)
    (run_dir / "state.json").write_text(contents, encoding="utf-8")
    _silent(_run_stop(_event(project), cwd=project))


def test_undecodable_state_bytes_let_the_turn_end_with_no_output(project):
    run_dir = project / "foundry-archive" / "brave-otter"
    run_dir.mkdir(parents=True)
    (run_dir / "state.json").write_bytes(b'{"phase": "F2\xff\xfe"}')
    _silent(_run_stop(_event(project), cwd=project))


def test_malformed_stdin_and_unreadable_state_together_allow_silently(project):
    run_dir = project / "foundry-archive" / "brave-otter"
    run_dir.mkdir(parents=True)
    (run_dir / "state.json").write_text("{", encoding="utf-8")
    _silent(_run_stop("garbage", cwd=project, project_dir=project))


def test_an_unreadable_run_never_hides_a_readable_live_one(project):
    _write_run(project, "older-live", "F2", mtime=1_000_000)
    broken = project / "foundry-archive" / "newer-broken"
    broken.mkdir(parents=True)
    (broken / "state.json").write_text("{", encoding="utf-8")
    os.utime(broken / "state.json", (2_000_000, 2_000_000))
    reason = _blocked(_run_stop(_event(project), cwd=project))
    assert "'older-live'" in reason


# ---------------------------------------------------------------------------
# The active-run rule: one run, the most recently modified live one
# ---------------------------------------------------------------------------


def test_of_two_live_runs_the_most_recently_modified_state_wins(project):
    _write_run(project, "alpha-run", "F2", mtime=1_000_000)
    _write_run(project, "beta-run", "F3", mtime=2_000_000)
    reason = _blocked(_run_stop(_event(project), cwd=project))
    assert "'beta-run'" in reason and "in phase F3 and" in reason
    assert "alpha-run" not in reason

    os.utime(project / "foundry-archive" / "alpha-run" / "state.json", (3_000_000, 3_000_000))
    reason = _blocked(_run_stop(_event(project), cwd=project))
    assert "'alpha-run'" in reason and "in phase F2 and" in reason


def test_the_active_run_alone_decides_so_its_ask_allows_even_beside_an_older_live_run(project):
    _write_run(project, "older-live", "F2", mtime=1_000_000)
    _write_run(project, "newer-asked", "F3", parked=_parked([_item()], _marker("P-001")), mtime=2_000_000)
    _silent(_run_stop(_event(project), cwd=project))


def test_a_newer_finished_run_does_not_shadow_an_older_live_one(project):
    _write_run(project, "live-run", "F4", mtime=1_000_000)
    _write_run(project, "done-run", "F6", mtime=2_000_000)
    _write_run(project, "halted-run", RUN_PHASE_HALTED, mtime=3_000_000)
    _write_run(project, "planning-run", "F0.5", mtime=4_000_000)
    reason = _blocked(_run_stop(_event(project), cwd=project))
    assert "'live-run'" in reason


def test_a_stop_token_in_the_run_directory_is_no_stop_signal(project):
    run_dir = _write_run(project, "brave-otter", "F3")
    token = {"run": "brave-otter", "created_at": TS, "nonce": "00", "consumed_at": None}
    (run_dir / STOP_TOKEN_FILENAME).write_text(json.dumps(token), encoding="utf-8")
    _blocked(_run_stop(_event(project), cwd=project))


def test_a_cwd_drifted_below_the_project_falls_through_to_the_project_dir(project):
    _write_run(project, "brave-otter", "F3")
    drifted = project / "plugins" / "foundry" / "mcp-server"
    drifted.mkdir(parents=True)
    reason = _blocked(_run_stop(_event(drifted), cwd=drifted, project_dir=project))
    assert "'brave-otter'" in reason


def test_a_drifted_cwd_with_no_project_dir_finds_nothing_to_hold(project):
    """The limit of the fallback, pinned: with no root that holds the archive,
    there is no run to hold the turn for."""
    _write_run(project, "brave-otter", "F3")
    drifted = project / "sub"
    drifted.mkdir()
    _silent(_run_stop(_event(drifted), cwd=drifted))


def test_the_event_cwd_run_is_taken_before_the_project_dir_run(tmp_path, project):
    other = tmp_path / "other-project"
    _write_run(project, "cwd-run", "F2", mtime=1_000_000)
    _write_run(other, "env-run", "F3", mtime=2_000_000)
    reason = _blocked(_run_stop(_event(project), cwd=project, project_dir=other))
    assert "'cwd-run'" in reason


# ---------------------------------------------------------------------------
# Files only: the hook never reaches the server
# ---------------------------------------------------------------------------


def test_the_hook_runs_on_the_standard_library_alone(project):
    """Isolated mode with no site-packages: the server package cannot be
    imported, so a hook that reached for it would crash rather than block."""
    _write_run(project, "brave-otter", "F2")
    argv = [sys.executable, "-I", "-S", str(STOP_HOOK)]
    reason = _blocked(_run_stop(_event(project), cwd=project, argv=argv))
    assert "'brave-otter'" in reason


#: Names a hook source may never carry: the server package, the server
#: executable, and every standard-library way to launch a process — a hook
#: that shelled out could reach the server however it spelt the command.
#: A NAME scan, deliberately: which modules the hooks import is proven by the
#: isolated-mode runs above, where anything outside the standard library and
#: the hooks directory cannot be imported at all.
FORBIDDEN_NAMES = ("foundry_mcp", "foundry-mcp", "subprocess", "os.system", "os.popen", "os.exec", "os.spawn")


@pytest.mark.parametrize("script", [STOP_HOOK, SESSION_HOOK, SHARED_READER], ids=lambda p: p.name)
def test_the_hook_sources_never_name_the_server_or_a_process_launcher(script):
    text = script.read_text(encoding="utf-8")
    named = [name for name in FORBIDDEN_NAMES if name in text]
    assert named == [], f"{script.name} names {named}: a hook reads files and never reaches the server"


def test_the_reader_spells_the_phases_and_keys_the_server_spells():
    """The hook re-types the server's names; this holds the copies equal, and
    holds stop.md's own copy of the live phases equal too, so the Stop hook and
    the /foundry:stop token step can never disagree about which run is live."""
    literals = {}
    for node in ast.parse(SHARED_READER.read_text(encoding="utf-8")).body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            try:
                literals[node.targets[0].id] = ast.literal_eval(node.value)
            except ValueError:
                continue
    assert set(literals["LIVE_PHASES"]) == set(POST_CAST_RUN_PHASES)
    assert literals["PARKED_KEY"] == PARKED_STATE_KEY
    assert literals["AWAITING_HUMAN_KEY"] == PARKED_AWAITING_HUMAN_KEY

    match = re.search(r"^LIVE_PHASES = (\(.*?\))", STOP_MD.read_text(encoding="utf-8"), re.MULTILINE)
    assert match, "commands/stop.md no longer spells LIVE_PHASES in its token step"
    assert set(ast.literal_eval(match.group(1))) == set(literals["LIVE_PHASES"])


def test_the_stop_hook_is_an_executable_python_script():
    assert STOP_HOOK.stat().st_mode & stat.S_IXUSR, "stop-continue.py is not executable"
    assert STOP_HOOK.read_text(encoding="utf-8").startswith("#!/usr/bin/env python3\n")
