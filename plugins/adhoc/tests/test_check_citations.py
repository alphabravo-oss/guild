#!/usr/bin/env python3
"""Offline tests for check-citations.py.

Builds synthetic transcript JSONL files matching the Stop hook input format,
runs the hook against each, and asserts the decision (block / pass) and the
specific unverified citations the hook reports.

Test cases:
  1. SHIRO   — assistant cites file:line that wasn't Read; expect BLOCK
  2. CLEAN   — every cited file was Read in the turn; expect PASS
  3. FENCED  — citation appears only inside a ``` block; expect PASS (skipped)
  4. SUBAGENT — only a Task/Agent call touched the file; expect BLOCK
  5. DIFF    — citation-shaped text in diff lines (---/+++); expect PASS
  6. PROSE   — file path mentioned without :line; expect PASS (tier A only)
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
HOOK = PLUGIN_ROOT / "scripts" / "check-citations.py"

assert HOOK.exists(), f"hook not found at {HOOK}"


# --- transcript builder ---------------------------------------------------

def msg(role: str, content) -> dict:
    return {"role": role, "content": content}


def text_block(text: str) -> dict:
    return {"type": "text", "text": text}


def tool_use_block(name: str, params: dict) -> dict:
    return {"type": "tool_use", "name": name, "input": params, "id": "tu_1"}


def write_transcript(messages: list[dict]) -> str:
    """Write messages as JSONL, return path."""
    fd, path = tempfile.mkstemp(suffix=".jsonl")
    with os.fdopen(fd, "w") as fh:
        for m in messages:
            fh.write(json.dumps(m) + "\n")
    return path


def run_hook(transcript_path: str, *, mode: str | None = "default") -> dict:
    """Invoke check-citations.py with a Stop event for the given transcript.

    Returns a dict: {"exit": int, "stdout": str, "stderr": str, "decision": parsed}
    """
    # Set up isolated HOME so we don't pollute the user's real config
    home = tempfile.mkdtemp()
    claude_dir = Path(home) / ".claude"
    claude_dir.mkdir(parents=True)
    if mode is not None:
        (claude_dir / ".adhoc-citations-mode").write_text(mode)

    event = {
        "session_id": "test-session",
        "transcript_path": transcript_path,
        "stop_hook_active": False,
        "hook_event_name": "Stop",
    }

    proc = subprocess.run(
        [sys.executable, str(HOOK)],
        input=json.dumps(event),
        capture_output=True,
        text=True,
        env={**os.environ, "HOME": home},
        timeout=10,
    )

    decision = None
    if proc.stdout.strip():
        try:
            decision = json.loads(proc.stdout)
        except json.JSONDecodeError:
            pass

    return {
        "exit": proc.returncode,
        "stdout": proc.stdout,
        "stderr": proc.stderr,
        "decision": decision,
        "log_path": str(claude_dir / ".adhoc-citations-log.jsonl"),
    }


# --- test cases -----------------------------------------------------------

def test_shiro_failure_blocks():
    """Reproduces the Shiro failure: assistant cites a file:line it never Read."""
    t = write_transcript([
        msg("user", "How can we wire Shiro scan results into the deployment report?"),
        msg("assistant", [
            tool_use_block("Read", {"file_path": "/repo/internal/api/report/handler.go"}),
            text_block(
                "The handler is at internal/api/report/handler.go:73 and the proto "
                "definition lives at proto/pioneer/v1/report.proto:11 where "
                "REPORT_TYPE_COMPLIANCE is reserved."
            ),
        ]),
    ])
    result = run_hook(t)
    assert result["exit"] == 0, result
    assert result["decision"] is not None, "expected a decision payload"
    assert result["decision"]["decision"] == "block", result
    reason = result["decision"]["reason"]
    # handler.go was Read → should be considered verified
    assert "handler.go:73" not in reason, f"handler.go was Read, should not be flagged: {reason}"
    # report.proto was NOT Read → must be flagged
    assert "report.proto:11" in reason, f"report.proto was not Read, should be flagged: {reason}"
    print("  ✓ shiro_failure_blocks")


def test_clean_response_passes():
    """All cited files were Read in this turn — should pass."""
    t = write_transcript([
        msg("user", "What's at handler.go:73?"),
        msg("assistant", [
            tool_use_block("Read", {"file_path": "/repo/internal/api/handler.go"}),
            text_block("The function lives at internal/api/handler.go:73."),
        ]),
    ])
    result = run_hook(t)
    assert result["exit"] == 0, result
    assert result["decision"] is None, f"expected no block, got: {result['decision']}"
    print("  ✓ clean_response_passes")


def test_fenced_block_skipped():
    """Citations only appearing in ``` code fences should NOT trigger block."""
    t = write_transcript([
        msg("user", "Show me an example."),
        msg("assistant", [
            text_block(
                "Here's a hypothetical:\n\n"
                "```go\n"
                "// example/path/foo.go:42\n"
                "func Foo() {}\n"
                "```\n\n"
                "That's the shape."
            ),
        ]),
    ])
    result = run_hook(t)
    assert result["decision"] is None, (
        f"citation in fenced block should be skipped, got: {result['decision']}"
    )
    print("  ✓ fenced_block_skipped")


def test_subagent_read_does_not_count():
    """Only a Task (subagent) call touched the file; no direct Read.

    This is the exact failure mode from the Shiro turn — Explore subagent
    Read the files, returned a summary, Claude cited from the summary.
    The hook MUST treat subagent invocations as non-verification.
    """
    t = write_transcript([
        msg("user", "Where is X defined?"),
        msg("assistant", [
            tool_use_block("Task", {
                "description": "Find X",
                "subagent_type": "Explore",
                "prompt": "Find X",
            }),
            text_block("X is defined at internal/foo/bar.go:99."),
        ]),
    ])
    result = run_hook(t)
    assert result["decision"] is not None and result["decision"]["decision"] == "block", (
        f"subagent calls must not count as verification, got: {result['decision']}"
    )
    assert "bar.go:99" in result["decision"]["reason"]
    print("  ✓ subagent_read_does_not_count")


def test_diff_lines_skipped():
    """`+++ b/foo.go` and similar diff markers should not be treated as citations."""
    t = write_transcript([
        msg("user", "Show me a diff."),
        msg("assistant", [
            text_block(
                "The change:\n"
                "--- a/internal/foo.go\n"
                "+++ b/internal/foo.go\n"
                "@@ -10,5 +10,7 @@\n"
                " unchanged line\n"
                "+new line\n"
            ),
        ]),
    ])
    result = run_hook(t)
    assert result["decision"] is None, (
        f"diff markers should be skipped, got: {result['decision']}"
    )
    print("  ✓ diff_lines_skipped")


def test_bare_path_without_line_skipped():
    """Tier A only fires on path:line. A bare path mention is not a citation."""
    t = write_transcript([
        msg("user", "What does the codebase look like?"),
        msg("assistant", [
            text_block(
                "The main entry is internal/api/handler.go and the proto lives "
                "in proto/pioneer/v1/report.proto. Lots of code."
            ),
        ]),
    ])
    result = run_hook(t)
    assert result["decision"] is None, (
        f"bare paths (no :line) should not trigger tier A, got: {result['decision']}"
    )
    print("  ✓ bare_path_without_line_skipped")


def test_off_mode_short_circuits():
    """When mode is 'off', the hook must do nothing — no decision, no block."""
    t = write_transcript([
        msg("user", "Where is X?"),
        msg("assistant", [
            text_block("X is at totally/made/up/file.go:999."),
        ]),
    ])
    result = run_hook(t, mode="off")
    assert result["decision"] is None, (
        f"off mode must short-circuit, got: {result['decision']}"
    )
    print("  ✓ off_mode_short_circuits")


def test_stop_hook_active_short_circuits():
    """When stop_hook_active=true, the hook must not re-block (avoids infinite loop)."""
    t = write_transcript([
        msg("user", "Where is X?"),
        msg("assistant", [
            text_block("X is at made-up/path.go:42."),
        ]),
    ])
    # Build event manually with stop_hook_active=True
    home = tempfile.mkdtemp()
    (Path(home) / ".claude").mkdir()
    event = {
        "session_id": "s",
        "transcript_path": t,
        "stop_hook_active": True,
        "hook_event_name": "Stop",
    }
    proc = subprocess.run(
        [sys.executable, str(HOOK)],
        input=json.dumps(event),
        capture_output=True,
        text=True,
        env={**os.environ, "HOME": home},
        timeout=10,
    )
    assert proc.returncode == 0
    assert proc.stdout.strip() == "", (
        f"stop_hook_active should suppress decision output, got: {proc.stdout!r}"
    )
    print("  ✓ stop_hook_active_short_circuits")


def test_grep_counts_as_verification():
    """A Grep with path argument should verify that path."""
    t = write_transcript([
        msg("user", "Where is X?"),
        msg("assistant", [
            tool_use_block("Grep", {"path": "/repo/internal/foo.go", "pattern": "X"}),
            text_block("Found X at internal/foo.go:55."),
        ]),
    ])
    result = run_hook(t)
    assert result["decision"] is None, (
        f"Grep on the cited path should count as verification, got: {result['decision']}"
    )
    print("  ✓ grep_counts_as_verification")


def test_log_written_on_block():
    """Hook should append a structured log entry for every check.

    The log entry nests per-check results under `checks.<name>`, and the
    citations check's `details` is the unverified list as (path, line) pairs
    — which survive the JSON round-trip as two-element lists.
    """
    t = write_transcript([
        msg("user", "Where is X?"),
        msg("assistant", [
            text_block("X is at totally/made/up.go:999."),
        ]),
    ])
    result = run_hook(t)
    assert result["decision"]["decision"] == "block"
    log_path = result["log_path"]
    assert os.path.exists(log_path), f"log not written at {log_path}"
    log_lines = Path(log_path).read_text().strip().splitlines()
    assert len(log_lines) >= 1
    entry = json.loads(log_lines[-1])
    assert entry["decision"] == "block"
    citations = entry["checks"]["citations"]
    assert citations["blocked"] is True
    assert any(
        path.endswith("up.go") for path, _line in citations["details"]
    ), f"unverified citation not recorded in log: {citations['details']}"
    print("  ✓ log_written_on_block")


# --- runner ---------------------------------------------------------------

def main() -> int:
    tests = [
        test_shiro_failure_blocks,
        test_clean_response_passes,
        test_fenced_block_skipped,
        test_subagent_read_does_not_count,
        test_diff_lines_skipped,
        test_bare_path_without_line_skipped,
        test_off_mode_short_circuits,
        test_stop_hook_active_short_circuits,
        test_grep_counts_as_verification,
        test_log_written_on_block,
    ]
    print(f"Running {len(tests)} citation-hook tests:\n")
    failed = 0
    for t in tests:
        try:
            t()
        except AssertionError as e:
            print(f"  ✗ {t.__name__}: {e}")
            failed += 1
        except Exception as e:
            print(f"  ✗ {t.__name__}: unexpected error: {e}")
            failed += 1
    print()
    if failed:
        print(f"FAIL — {failed} of {len(tests)} tests failed")
        return 1
    print(f"PASS — all {len(tests)} tests passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
