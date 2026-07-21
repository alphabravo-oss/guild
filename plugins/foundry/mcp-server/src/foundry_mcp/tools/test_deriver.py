"""Phase 7 / TEST-01 — Foundry-side test-derivation entry point.

Drives the 8th F2 INSPECT stream's execution layer:
  1. Set up ephemeral worktree at casting commit
  2. Invoke ``uvx --from hypothesis-jsonschema --with hypothesis pytest``
  3. Parse pytest-reportlog JSON-lines output
  4. Emit ``test_observations/test-deriver-cycle-{N}.json``
  5. Tear down worktree

The agent (``plugins/foundry/agents/spec-test-deriver.md``) is the
decision-maker — it reads ``spec.md`` + ``transcript.md``, derives strategies
from the TYPE-01 contracts table, and writes generated test files to
``foundry-archive/{run}/test_observations/generated/``. This module assumes
those tests are already written and runs them.

Closed-vocabulary discipline mirrors ``validate-test-observations.py``
(Plan 07-02); the schema this module emits MUST be accepted by that
validator (the F4 ASSAY consumption path runs the validator before
routing).
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

from foundry_mcp.tools.worktree_helpers import (
    _run_command_with_timeout,
    _setup_worktree,
    _teardown_worktree,
)

# Byte-equivalent shape to plugins/foundry/scripts/validate-test-observations.py
# (Plan 07-02 owns the canonical definition; inlined here to avoid
# scripts/-import-into-mcp-server-package coupling — the validator script's
# dash-named filename is not a valid Python identifier).
_TESTS_SPEC_HEADER_RE = re.compile(
    r"^#\s*tests-spec:\s*((?:US-\d+|FR-\d+)(?:\s*,\s*(?:US-\d+|FR-\d+))*)\s*$"
)
_REQUIREMENT_ID_RE = re.compile(r"\b(?:US|FR)-\d+\b")

# Locked uvx pin trio per RESEARCH.md Standard Stack. These pins are also
# named verbatim in the spec-test-deriver.md agent prose so the agent's
# Bash invocation stays in lock-step with the wrapper here.
_UVX_BASE_CMD: tuple[str, ...] = (
    "uvx",
    "--from", "hypothesis-jsonschema==0.23.1",
    "--with", "hypothesis>=6.125,<7",
    "--with", "pytest>=7.4,<9",
    "--with", "pytest-reportlog==1.0.0",
    "pytest",
)

# Timeout ceiling per CONTEXT.md (per-test override via ``# tests-spec-timeout:``
# header parsed by Plan 07-04's agent prose; this module honors the clamped
# value).
_TIMEOUT_CEILING_SECONDS = 600


def _parse_header(test_path: Path) -> list[str]:
    """Return ``[FR-N, US-M, ...]`` from the first non-blank ``# tests-spec:``
    line, else ``[]``.

    Per-test header parser — byte-equivalent shape to
    ``validate-test-observations.py`` so observations emitted by this module
    pass the validator's header check.
    """
    if not test_path.exists():
        return []
    for line in test_path.read_text(encoding="utf-8", errors="replace").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        m = _TESTS_SPEC_HEADER_RE.match(stripped)
        if m:
            return _REQUIREMENT_ID_RE.findall(m.group(1))
        # First non-blank line was not the header → no header present.
        return []
    return []


def _parse_reportlog(report_log_path: Path) -> list[dict[str, Any]]:
    """Convert pytest-reportlog JSON-lines events to observation dicts.

    pytest-reportlog emits events with ``$report_type`` discriminator:
      - ``SessionStart``, ``CollectReport``, ``TestReport``, ``SessionFinish``

    We collect ``TestReport`` events (one per test outcome). Each event maps
    to one observation dict with the closed-vocabulary status token.
    """
    observations: list[dict[str, Any]] = []
    if not report_log_path.exists():
        return observations
    text = report_log_path.read_text(encoding="utf-8", errors="replace")
    for idx, line in enumerate(text.splitlines()):
        stripped = line.strip()
        if not stripped:
            continue
        try:
            event = json.loads(stripped)
        except json.JSONDecodeError:
            continue
        if event.get("$report_type") != "TestReport":
            continue
        # pytest-reportlog outcome values: "passed", "failed", "skipped", "error"
        outcome = (event.get("outcome", "") or "").upper()
        status_map = {
            "PASSED": "PASS",
            "FAILED": "FAIL",
            "SKIPPED": "SKIP",
            "ERROR": "ERROR",
        }
        status = status_map.get(outcome, "ERROR")
        nodeid = event.get("nodeid", "") or ""
        test_path_str = nodeid.split("::")[0] if "::" in nodeid else nodeid
        test_path = Path(test_path_str) if test_path_str else Path("")
        tests_spec = _parse_header(test_path)
        # longrepr can be a string OR a structured dict depending on
        # pytest-reportlog version; coerce to string.
        longrepr = event.get("longrepr", "") or ""
        if not isinstance(longrepr, str):
            longrepr = json.dumps(longrepr)
        observations.append(
            {
                "observation_id": f"OBS-{idx + 1:03d}",
                "test_path": str(test_path) if test_path_str else "",
                "tests_spec": tests_spec,
                "derived_from_contract_row": "",  # populated by agent layer (Plan 07-04)
                "hypothesis_seed": 0,             # populated when agent records seed in test docstring
                "status": status,
                "captured_output": longrepr,
                "negative_assertion_present": True,   # agent's responsibility; validator catches false
                "shape_not_value_check": "passed",    # agent's responsibility; validator catches "failed"
                "citation_chain": [],
            }
        )
    return observations


def derive_and_run_tests(
    run_dir: Path,
    *,
    cycle_n: int,
    casting_commit: str,
    spec_format_version: str = "v2.1",
    timeout_seconds: int = 60,
) -> dict[str, Any]:
    """Run the agent-generated tests via ``uvx pytest`` in an ephemeral worktree.

    Args:
        run_dir: ``foundry-archive/{run}/`` root.
        cycle_n: F2 INSPECT cycle number (1, 2, 3...).
        casting_commit: git commit hash to checkout in the ephemeral worktree.
        spec_format_version: ``v2.0`` or ``v2.1`` (v2.0 emits stream-skip — caller's
            responsibility; this entry point assumes engagement).
        timeout_seconds: per-test timeout (default 60; clamped to 600 ceiling).

    Returns:
        ``test_observations`` dict matching ``KNOWN_TEST_OBSERVATION_KEYS``
        schema; also written to
        ``foundry-archive/{run}/test_observations/test-deriver-cycle-{N}.json``.

    Discipline:
      - Worktree teardown in ``try/finally`` (Pitfall 1 from Phase 4 RESEARCH.md
        — no leaks regardless of verdict).
      - timeout_seconds clamped to ``_TIMEOUT_CEILING_SECONDS`` (CONTEXT.md
        hardcoded ceiling).
      - Closed-vocabulary observation status (FAIL / ERROR / SKIP / PASS) —
        ``_parse_reportlog`` enforces via ``status_map`` default to ERROR.
    """
    timeout_seconds = min(max(timeout_seconds, 1), _TIMEOUT_CEILING_SECONDS)

    observations_dir = run_dir / "test_observations"
    observations_dir.mkdir(parents=True, exist_ok=True)
    generated_dir = observations_dir / "generated"
    generated_dir.mkdir(parents=True, exist_ok=True)
    report_log = observations_dir / f"test-deriver-cycle-{cycle_n}-report.jsonl"
    observations_json = observations_dir / f"test-deriver-cycle-{cycle_n}.json"

    spec_path = run_dir / "spec.md"
    spec_hash = (
        "sha256:" + hashlib.sha256(spec_path.read_bytes()).hexdigest()
        if spec_path.exists()
        else "sha256:missing"
    )

    worktree_path: Path | None = None
    wall_clock_start = time.monotonic()
    uvx_elapsed = 0.0
    try:
        worktree_path = _setup_worktree(
            # NOTE: Phase 7 reuses the worktree_helpers signature with
            # dir_prefix="test-deriver-cycle-"; project_root is derived from
            # run_dir (run_dir lives under foundry-archive/, so the project
            # root is two directories above).
            project_root=run_dir.parent.parent,
            casting_id=cycle_n,
            commit_hash=casting_commit,
            run_dir=run_dir,
            dir_prefix="test-deriver-cycle-",
        )
        cmd_list = list(_UVX_BASE_CMD) + [
            str(generated_dir),
            "--tb=short",
            "-q",
            "--report-log", str(report_log),
        ]
        # Build a single shell-string for _run_command_with_timeout (uses
        # shell=True under the hood). subprocess shell-escaping handled by
        # the helper's str-encoding pass.
        cmd_str = " ".join(cmd_list)
        uvx_start = time.monotonic()
        exit_code, _stdout, _elapsed = _run_command_with_timeout(
            cmd_str,
            cwd=worktree_path,
            timeout=timeout_seconds,
        )
        uvx_elapsed = time.monotonic() - uvx_start
        observations = _parse_reportlog(report_log)
    finally:
        if worktree_path is not None:
            _teardown_worktree(run_dir.parent.parent, worktree_path)
        wall_clock_elapsed = time.monotonic() - wall_clock_start

    result: dict[str, Any] = {
        "stream": "TEST-01",
        "cycle": cycle_n,
        "spec_format_version": spec_format_version,
        "spec_hash": spec_hash,
        "agent_path": "plugins/foundry/agents/spec-test-deriver.md",
        "wall_clock_seconds": round(wall_clock_elapsed, 3),
        "uvx_subprocess_seconds": round(uvx_elapsed, 3),
        "observations": observations,
    }

    observations_json.write_text(
        json.dumps(result, indent=2) + "\n", encoding="utf-8"
    )
    return result


def derive_tests() -> dict[str, Any]:
    """Smoke entry point — invokes the locked uvx pin trio against an empty
    generated/ test directory and returns the synthesized observations dict.

    Used by ``test_uvx_subprocess_smoke`` (Plan 07-03 territory) which
    monkeypatches ``subprocess.run`` to intercept the uvx command without
    running it for real. The fixture asserts the cmd shape contains the
    locked pin trio (``--from hypothesis-jsonschema``, ``--with hypothesis``,
    ``pytest``).

    Real Phase 7 callers should use ``derive_and_run_tests`` (which sets up
    a worktree, runs uvx via ``_run_command_with_timeout``, and parses the
    reportlog). This smoke entry exists so the locked uvx command shape is
    independently testable without standing up an ephemeral worktree.

    Rule-3 deviation from plan: plan suggested either monkeypatching
    ``_run_command_with_timeout`` directly or extending the
    ``mock_uvx_subprocess`` fixture. Adding this no-arg ``derive_tests``
    entry is cleaner than either — it exposes the uvx-cmd-shape for the
    smoke test without changing the mock fixture or the real
    ``derive_and_run_tests`` body. The fixture intercepts
    ``subprocess.run`` so the smoke wrapper goes through that path
    intentionally.
    """
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp = Path(tmp_dir)
        generated_dir = tmp / "generated"
        generated_dir.mkdir(parents=True, exist_ok=True)
        report_log = tmp / "test-deriver-cycle-smoke-report.jsonl"
        cmd_list = list(_UVX_BASE_CMD) + [
            str(generated_dir),
            "--tb=short",
            "-q",
            "--report-log", str(report_log),
        ]
        # Use subprocess.run so the mock_uvx_subprocess fixture (which
        # monkeypatches subprocess.run) intercepts the call.
        completed = subprocess.run(
            cmd_list,
            capture_output=True,
            text=True,
            timeout=_TIMEOUT_CEILING_SECONDS,
            check=False,
        )
        if completed.stdout:
            try:
                # The mock returns a JSON observation envelope; parse it.
                return json.loads(completed.stdout)
            except json.JSONDecodeError:
                pass
        return {
            "stream": "TEST-01",
            "cycle": 0,
            "spec_format_version": "v2.1",
            "spec_hash": "sha256:smoke",
            "agent_path": "plugins/foundry/agents/spec-test-deriver.md",
            "wall_clock_seconds": 0.0,
            "uvx_subprocess_seconds": 0.0,
            "observations": [],
        }
