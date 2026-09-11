"""Real-script tests for the foundry SessionStart hook, `plugins/foundry/hooks/session-start-run.py`,
and for the hook registry that wires it and the Stop hook in.

When the lead's session is compacted or resumed mid-build, this hook puts the
live run back in front of it: an additionalContext envelope naming the run and
telling it to call Foundry-Context, then Foundry-Next. With no live run, or on
any error, it prints nothing and exits 0 — it must never degrade a session.

Every test runs the SHIPPED script in a subprocess with the SessionStart event
on stdin, against a throwaway `foundry-archive/` under ``tmp_path``, with
CLAUDE_PROJECT_DIR scrubbed from the environment unless a test sets it (the
suite may itself run inside a session whose project has a live run on disk).
"""

from __future__ import annotations

import json
import os
import re
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from foundry_mcp.schemas.vocab import RUN_PHASE_HALTED


# tests/ -> mcp-server/ -> foundry/ -> plugins/ -> repo root.
REPO_ROOT = Path(__file__).resolve().parents[4]
PLUGIN_ROOT = REPO_ROOT / "plugins" / "foundry"
HOOKS_DIR = PLUGIN_ROOT / "hooks"
HOOKS_JSON = HOOKS_DIR / "hooks.json"
SESSION_HOOK = HOOKS_DIR / "session-start-run.py"
STOP_HOOK = HOOKS_DIR / "stop-continue.py"

#: Every SessionStart source the platform documents, as its matchers.
SESSION_SOURCES = ("startup", "resume", "clear", "compact", "fork")

SERENA_ENTRY = {
    "matcher": ".*",
    "hooks": [
        {
            "type": "command",
            "command": "${CLAUDE_PLUGIN_ROOT}/hooks/session-start-serena.sh",
            "timeout": 47,
        }
    ],
}


def _run(
    script: Path,
    stdin: str,
    *,
    cwd: Path,
    project_dir: Path | None = None,
    argv: list[str] | None = None,
) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if k != "CLAUDE_PROJECT_DIR"}
    if project_dir is not None:
        env["CLAUDE_PROJECT_DIR"] = str(project_dir)
    return subprocess.run(
        argv or [str(script)],
        input=stdin,
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=False,
    )


def _event(cwd: Path, source: str = "compact") -> str:
    return json.dumps(
        {"session_id": "test-session", "cwd": str(cwd), "hook_event_name": "SessionStart", "source": source}
    )


def _write_run(project: Path, name: str, phase: str, *, mtime: float | None = None) -> Path:
    run_dir = project / "foundry-archive" / name
    run_dir.mkdir(parents=True, exist_ok=True)
    state_path = run_dir / "state.json"
    state_path.write_text(json.dumps({"phase": phase, "cycle": 2}), encoding="utf-8")
    if mtime is not None:
        os.utime(state_path, (mtime, mtime))
    return run_dir


def _context(result: subprocess.CompletedProcess) -> str:
    assert result.returncode == 0, result.stderr
    envelope = json.loads(result.stdout)
    output = envelope["hookSpecificOutput"]
    assert output["hookEventName"] == "SessionStart"
    return output["additionalContext"]


def _silent(result: subprocess.CompletedProcess) -> None:
    assert result.returncode == 0, result.stderr
    assert result.stdout == "", f"the hook printed when it should have said nothing: {result.stdout}"
    assert result.stderr == "", f"the hook wrote error text: {result.stderr}"


@pytest.fixture
def project(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    root.mkdir()
    return root


# ---------------------------------------------------------------------------
# The re-orientation context
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("source", "happened"), [("compact", "compacted"), ("resume", "resumed")])
def test_compact_and_resume_name_the_run_and_both_steps_in_order(project, source, happened):
    _write_run(project, "brave-otter", "F3")
    context = _context(_run(SESSION_HOOK, _event(project, source), cwd=project))
    assert "'brave-otter'" in context
    assert "phase F3" in context
    assert f"just {happened}" in context
    assert context.index("Foundry-Context") < context.index("Foundry-Next")
    assert "(1) call Foundry-Context; (2) call Foundry-Next" in context
    assert "Foundry-Init(resume='brave-otter')" in context
    assert "do not hand over" in context


def test_with_no_archive_the_hook_says_nothing(project):
    _silent(_run(SESSION_HOOK, _event(project), cwd=project))


@pytest.mark.parametrize("phase", ["F0", "F0.5", "F0.9", "F6", RUN_PHASE_HALTED])
def test_a_run_outside_the_live_phases_is_not_re_injected(project, phase):
    _write_run(project, "brave-otter", phase)
    _silent(_run(SESSION_HOOK, _event(project), cwd=project))


@pytest.mark.parametrize("stdin", ["", "not json", "[1]", "null", '{"source": '])
def test_errors_give_no_output_and_exit_zero(project, stdin):
    _write_run(project, "brave-otter", "F2")
    _silent(_run(SESSION_HOOK, stdin, cwd=project, project_dir=project))


def test_an_unreadable_state_json_gives_no_output(project):
    run_dir = project / "foundry-archive" / "brave-otter"
    run_dir.mkdir(parents=True)
    (run_dir / "state.json").write_text("{not json", encoding="utf-8")
    _silent(_run(SESSION_HOOK, _event(project), cwd=project))


def test_a_run_name_the_hook_did_not_author_still_yields_valid_json(project):
    odd = 'odd "quoted" \\ it\'s run'
    _write_run(project, odd, "F4")
    context = _context(_run(SESSION_HOOK, _event(project), cwd=project))
    assert f"'{odd}'" in context


def test_both_hooks_pick_the_same_run_by_the_shared_rule(project):
    _write_run(project, "alpha-run", "F2", mtime=1_000_000)
    _write_run(project, "beta-run", "F3", mtime=2_000_000)
    _write_run(project, "gamma-done", "F6", mtime=3_000_000)
    context = _context(_run(SESSION_HOOK, _event(project), cwd=project))
    stop = _run(STOP_HOOK, json.dumps({"cwd": str(project)}), cwd=project)
    reason = json.loads(stop.stdout)["reason"]
    assert "'beta-run'" in context and "'beta-run'" in reason
    assert "alpha-run" not in context and "alpha-run" not in reason


def test_the_hook_runs_on_the_standard_library_alone(project):
    _write_run(project, "brave-otter", "F2")
    argv = [sys.executable, "-I", "-S", str(SESSION_HOOK)]
    context = _context(_run(SESSION_HOOK, _event(project), cwd=project, argv=argv))
    assert "'brave-otter'" in context


# ---------------------------------------------------------------------------
# The registry
# ---------------------------------------------------------------------------


def _registry() -> dict:
    return json.loads(HOOKS_JSON.read_text(encoding="utf-8"))["hooks"]


def _entries_running(event: str, script: str) -> list[dict]:
    return [
        entry for entry in _registry().get(event, [])
        if any(hook.get("command") == f"${{CLAUDE_PLUGIN_ROOT}}/hooks/{script}" for hook in entry["hooks"])
    ]


def test_hooks_json_keeps_the_serena_entry_exactly_as_it_was():
    serena = _entries_running("SessionStart", "session-start-serena.sh")
    assert serena == [SERENA_ENTRY]
    assert all(re.fullmatch(serena[0]["matcher"], source) for source in SESSION_SOURCES)


def test_the_run_hook_is_registered_for_compact_and_resume_only():
    entries = _entries_running("SessionStart", "session-start-run.py")
    assert len(entries) == 1
    matcher = entries[0]["matcher"]
    assert matcher != ".*"
    matched = {source for source in SESSION_SOURCES if re.fullmatch(matcher, source)}
    assert matched == {"compact", "resume"}


def test_the_stop_hook_is_registered_on_the_stop_event():
    entries = _entries_running("Stop", "stop-continue.py")
    assert len(entries) == 1
    assert entries[0]["hooks"][0]["type"] == "command"


def test_every_registered_command_is_a_shipped_file_and_the_python_ones_are_executable():
    for event, entries in _registry().items():
        for entry in entries:
            for hook in entry["hooks"]:
                command = hook["command"]
                assert command.startswith("${CLAUDE_PLUGIN_ROOT}/hooks/"), f"{event}: {command}"
                path = PLUGIN_ROOT / command.removeprefix("${CLAUDE_PLUGIN_ROOT}/")
                assert path.is_file(), f"{event} runs {command}, which does not ship"
                assert path.stat().st_mode & stat.S_IXUSR, f"{path.name} is not executable"
                if path.suffix == ".py":
                    assert path.read_text(encoding="utf-8").startswith("#!/usr/bin/env python3\n")
