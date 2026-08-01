"""RED stubs for measure-run.py — Phase 9 / Plan 09-01 territory.

14 RED test stubs covering measure-run.py's full surface (per-run extractor +
matrix aggregator + closed-vocabulary frozensets + anti-drift cross-grep).

Plan 09-02 implements ``plugins/foundry/scripts/measure-run.py`` and turns
these GREEN; until then the entire module SKIPs at module-top because the
script does not yet exist on disk. Mirrors the Phase 8 / Plan 08-01
``allow_module_level=True`` discipline.

Test surface (per 09-VALIDATION.md RUN-01 verification rows):

  Per-run extractor (Tests 1-6):
   1. test_per_run_json_shape
   2. test_unknown_stream_rejected
   3. test_unknown_cohort_id_rejected
   4. test_missing_handoffs_jsonl_rejected
   5. test_missing_cycle_field_rejected
   6. test_strict_flag_rejects_missing_context

  Matrix aggregator (Tests 7-8):
   7. test_matrix_csv_shape
   8. test_matrix_markdown_table_shape

  Closed-vocabulary frozensets + anti-drift (Tests 9-11):
   9. test_known_phase9_stream_ids_matches_authoritative_sources
  10. test_known_phase9_cohort_ids_matches_runs_dir
  11. test_known_phase9_failure_tokens_present

  Quantitative gates + saturation logic (Tests 12-14):
  12. test_wall_clock_regression_pct_arithmetic
  13. test_run_01_quantitative_gates
  14. test_saturation_threshold_dual_criterion

RED-or-SKIP discipline:

- Module-top guard: ``pytest.skip(allow_module_level=True)`` when
  ``measure-run.py`` is missing on disk. Plan 09-01 ships ZERO production
  code (RED baseline); all 14 stubs SKIP until Plan 09-02 ships the script.
- Plan 09-02 ships measure-run.py + frozensets -> module collects;
  per-test bodies turn RED-or-GREEN based on script behavior.

Phase 1+2+3+4+5+6+7+8 byte-equivalence is preserved by living in a NEW
module — no edits to existing test_*.py modules.
"""

from __future__ import annotations

import csv
import io
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable

import pytest

# tests/test_measure_run.py -> parents:
#   [0]=tests, [1]=mcp-server, [2]=foundry, [3]=plugins, [4]=repo-root.
# Mirrors test_intent_coverage.py / test_spec_test_deriver.py precedent.
REPO_ROOT = Path(__file__).resolve().parents[4]
SCRIPT = REPO_ROOT / "plugins" / "foundry" / "scripts" / "measure-run.py"
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "measure_run"

# Phase 9 / Plan 09-01: cohort manifest stubs live under .planning/phases/...
RUNS_DIR = (
    REPO_ROOT
    / ".planning"
    / "phases"
    / "09-milestone-real-run-consolidation"
    / "runs"
)

# Authoritative-source paths for the anti-drift cross-grep (Test 9). Plan
# 09-02 will encode these literal paths inside measure-run.py; the test
# re-derives the expected stream-id set from disk and compares to the
# script's KNOWN_PHASE9_STREAM_IDS frozenset.
START_MD = REPO_ROOT / "plugins" / "foundry" / "commands" / "start.md"
INTENT_CARRIER = REPO_ROOT / "plugins" / "foundry" / "agents" / "intent-carrier.md"
SPEC_TEST_DERIVER = REPO_ROOT / "plugins" / "foundry" / "agents" / "spec-test-deriver.md"
SPEC_REVIEWER = REPO_ROOT / "plugins" / "forge" / "agents" / "spec-reviewer.md"
EVIDENCE_PY = (
    REPO_ROOT / "plugins" / "foundry" / "mcp-server" / "src" / "foundry_mcp"
    / "tools" / "evidence.py"
)


# Closed-vocabulary frozensets — locked per CONTEXT.md + 09-RESEARCH.md. The
# script's KNOWN_PHASE9_* frozensets MUST equal these literal values
# byte-for-byte; Plan 09-02 cannot drift without breaking these tests.
EXPECTED_KNOWN_PHASE9_STREAM_IDS = frozenset({
    "TRACE", "FLOW_TRACE", "PROVE", "RESEARCH_AUDIT", "COVERAGE_DIFF",
    "TEST-01", "SIGHT", "TEST",
    "EVID-01", "EVID-02",
    "INTV-01", "TYPE-01", "TYPE-02",
    "PROBE-01", "INTENT-01",
})

EXPECTED_KNOWN_PHASE9_FAILURE_TOKENS = frozenset({
    "PHASE9_UNKNOWN_STREAM",
    "PHASE9_UNKNOWN_COHORT",
    "PHASE9_RUN_DIR_INVALID",
    "PHASE9_CONTEXT_FILE_MISSING",
    "PHASE9_WALL_CLOCK_UNAVAILABLE",
    "PHASE9_CYCLE_COUNT_INVALID",
    "PHASE9_SCHEMA_INVALID",
    "PHASE9_DEFECTS_FILE_MALFORMED",
})

EXPECTED_KNOWN_PHASE9_COHORT_IDS = frozenset({
    "v4_2_0_baseline", "all_enabled_baseline",
    "no_INTV_01", "no_TYPE_01", "no_TYPE_02",
    "no_EVID_01", "no_EVID_02",
    "no_PROBE_01", "no_TEST_01", "no_INTENT_01",
})


# Module-top guard — every test in this module SKIPs cleanly when
# measure-run.py is missing on disk (Plan 09-01 RED baseline). Plan 09-02
# ships the script and lifts the SKIP automatically.
#
# Uses ``pytestmark = pytest.mark.skipif(...)`` rather than
# ``pytest.skip(allow_module_level=True)`` so pytest STILL COLLECTS all 14
# stubs (per the plan's verification grep requiring 14 named tests in
# ``--collect-only`` output) — collected but skipped is the RED baseline
# shape, indistinguishable from "all 14 tests pending Plan 09-02 ship".
pytestmark = pytest.mark.skipif(
    not SCRIPT.exists(),
    reason=(
        "measure-run.py not yet implemented — "
        "Plan 09-02 territory; RED until then."
    ),
)


# ---------------------------------------------------------------------------
# Helper fixture — make_run_dir.
#
# Builds a sample foundry-archive run directory with selected fixture
# overlays. Each test that exercises the per-run extractor uses this to
# synthesize a deterministic run dir under tmp_path.
#
# Lives in this module (NOT in conftest.py) so Phase 9 stays scoped to
# this file — mirrors Phase 6 Plan 06-01's local-_run_validator helper
# discipline (no conftest edits eliminate cross-phase regression risk).
# ---------------------------------------------------------------------------


@pytest.fixture
def make_run_dir(tmp_path: Path) -> Callable[..., Path]:
    """Build a sample run dir with selected fixture overlays.

    Default overlays:
      - handoffs.jsonl  := handoffs_minimal.jsonl
      - manifest.json   := manifest_v2_1.json
      - defects.json    := defects_per_stream.json
      - state.json      := state_cycle_3.json
      - context-at-f2.txt := "42.7" (or omitted when context_pct=None)
      - cohort.json     := synthesized using cohort_id (default
                           "all_enabled_baseline") with disable_lever_mechanism
                           "none" and PASS-PASS-PASS-PASS expected verdicts.

    Returns the run-dir path so the test can subprocess-invoke
    ``measure-run.py {run_dir}`` against it.
    """

    def _make(
        handoffs: str = "handoffs_minimal.jsonl",
        manifest: str = "manifest_v2_1.json",
        defects: str = "defects_per_stream.json",
        state: str = "state_cycle_3.json",
        context_pct: str | None = "42.7",
        cohort_id: str = "all_enabled_baseline",
        omit_handoffs: bool = False,
        omit_state: bool = False,
        cohort_json_override: dict[str, Any] | None = None,
    ) -> Path:
        run_dir = tmp_path / cohort_id
        run_dir.mkdir()
        if not omit_handoffs:
            (run_dir / "handoffs.jsonl").write_text(
                (FIXTURES / handoffs).read_text(encoding="utf-8"),
                encoding="utf-8",
            )
        (run_dir / "manifest.json").write_text(
            (FIXTURES / manifest).read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        (run_dir / "defects.json").write_text(
            (FIXTURES / defects).read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        if not omit_state:
            (run_dir / "state.json").write_text(
                (FIXTURES / state).read_text(encoding="utf-8"),
                encoding="utf-8",
            )
        if context_pct is not None:
            (run_dir / "context-at-f2.txt").write_text(
                context_pct, encoding="utf-8"
            )
        cohort_json: dict[str, Any] = cohort_json_override or {
            "cohort_id": cohort_id,
            "disable_lever_mechanism": "none",
            "disable_lever_description": "test fixture",
            "expected_gate_verdicts": {
                "cycles": "PASS",
                "defect_yield_per_stream": "PASS",
                "f2_context_pct": "PASS",
                "wall_clock_regression_pct": "PASS",
            },
            "expected_intervention_contribution": None,
            "archive_subdir": str(run_dir),
            "pre_phase_1_sha": None,
            "spec_path": "forge-specs/phase9-sloppy/spec.md",
            "spec_format_version": "v2.1",
        }
        (run_dir / "cohort.json").write_text(
            json.dumps(cohort_json), encoding="utf-8"
        )
        return run_dir

    return _make


def _invoke_measure_run(
    *args: str,
    cwd: Path | None = None,
) -> tuple[int, str, str]:
    """Subprocess-invoke ``measure-run.py {args}`` and return (exit, stdout, stderr).

    Plan 09-02 ships measure-run.py as an executable Python script. Tests
    invoke it via ``sys.executable`` so the running interpreter (and pytest's
    ``uvx`` venv) provide the runtime.
    """
    cmd = [sys.executable, str(SCRIPT), *args]
    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        cwd=str(cwd) if cwd is not None else None,
    )
    return proc.returncode, proc.stdout, proc.stderr


# ---------------------------------------------------------------------------
# Per-run extractor tests (Tests 1-6).
# ---------------------------------------------------------------------------


def test_per_run_json_shape(make_run_dir: Callable[..., Path]) -> None:
    """Test 1 — measure-run.py emits per-run JSON with all required fields.

    Required fields per 09-RESEARCH.md Example 1: cohort_id, cycles,
    per_stream_defects, f2_context_pct, wall_clock_seconds, gate_verdicts,
    failure_tokens.
    """
    run_dir = make_run_dir()
    exit_code, stdout, stderr = _invoke_measure_run(str(run_dir))
    assert exit_code == 0, (stdout, stderr)
    payload = json.loads(stdout)
    required = {
        "cohort_id",
        "cycles",
        "per_stream_defects",
        "f2_context_pct",
        "wall_clock_seconds",
        "gate_verdicts",
        "failure_tokens",
    }
    assert required.issubset(payload.keys()), (
        f"missing fields: {required - payload.keys()}"
    )
    assert payload["cohort_id"] == "all_enabled_baseline"
    assert isinstance(payload["per_stream_defects"], dict)
    assert isinstance(payload["failure_tokens"], list)


def test_unknown_stream_rejected(make_run_dir: Callable[..., Path]) -> None:
    """Test 2 — defects.json with stream not in KNOWN_PHASE9_STREAM_IDS fires
    PHASE9_UNKNOWN_STREAM.
    """
    run_dir = make_run_dir(defects="defects_unknown_stream.json")
    exit_code, stdout, stderr = _invoke_measure_run(str(run_dir))
    assert exit_code != 0
    payload = json.loads(stdout) if stdout.strip().startswith("{") else {}
    failure_tokens = payload.get("failure_tokens", []) if payload else []
    combined = stdout + stderr
    assert (
        "PHASE9_UNKNOWN_STREAM" in failure_tokens
        or "PHASE9_UNKNOWN_STREAM" in combined
    ), combined


def test_unknown_cohort_id_rejected(
    make_run_dir: Callable[..., Path],
) -> None:
    """Test 3 — cohort.json with cohort_id not in KNOWN_PHASE9_COHORT_IDS
    fires PHASE9_UNKNOWN_COHORT.
    """
    run_dir = make_run_dir(cohort_id="all_enabled_baseline")
    # Override to a cohort_id that is NOT in the locked frozenset.
    bogus_cohort = {
        "cohort_id": "foo_bar",
        "disable_lever_mechanism": "none",
        "disable_lever_description": "bogus",
        "expected_gate_verdicts": {
            "cycles": "PASS",
            "defect_yield_per_stream": "PASS",
            "f2_context_pct": "PASS",
            "wall_clock_regression_pct": "PASS",
        },
        "expected_intervention_contribution": None,
        "archive_subdir": str(run_dir),
        "pre_phase_1_sha": None,
        "spec_path": "forge-specs/phase9-sloppy/spec.md",
        "spec_format_version": "v2.1",
    }
    (run_dir / "cohort.json").write_text(
        json.dumps(bogus_cohort), encoding="utf-8"
    )
    exit_code, stdout, stderr = _invoke_measure_run(str(run_dir))
    assert exit_code != 0
    combined = stdout + stderr
    assert "PHASE9_UNKNOWN_COHORT" in combined, combined


def test_missing_handoffs_jsonl_rejected(
    make_run_dir: Callable[..., Path],
) -> None:
    """Test 4 — empty run dir (no handoffs.jsonl) fires
    PHASE9_WALL_CLOCK_UNAVAILABLE.
    """
    run_dir = make_run_dir(omit_handoffs=True)
    exit_code, stdout, stderr = _invoke_measure_run(str(run_dir))
    assert exit_code != 0
    combined = stdout + stderr
    assert "PHASE9_WALL_CLOCK_UNAVAILABLE" in combined, combined


def test_missing_cycle_field_rejected(
    make_run_dir: Callable[..., Path],
) -> None:
    """Test 5 — state.json missing the cycle field fires
    PHASE9_CYCLE_COUNT_INVALID.
    """
    run_dir = make_run_dir(state="state_cycle_invalid.json")
    exit_code, stdout, stderr = _invoke_measure_run(str(run_dir))
    assert exit_code != 0
    combined = stdout + stderr
    assert "PHASE9_CYCLE_COUNT_INVALID" in combined, combined


def test_strict_flag_rejects_missing_context(
    make_run_dir: Callable[..., Path],
) -> None:
    """Test 6 — ``--strict`` with missing context-at-f2.txt fires
    PHASE9_CONTEXT_FILE_MISSING; without ``--strict`` returns
    ``context_pct: None`` and no failure token.
    """
    # Strict mode + missing context file -> failure token.
    run_dir_strict = make_run_dir(context_pct=None)
    exit_code, stdout, stderr = _invoke_measure_run(
        "--strict", str(run_dir_strict)
    )
    assert exit_code != 0
    combined = stdout + stderr
    assert "PHASE9_CONTEXT_FILE_MISSING" in combined, combined

    # Non-strict mode + missing context file -> exit 0, context_pct None.
    run_dir_loose = make_run_dir(
        context_pct=None, cohort_id="no_INTV_01"
    )
    exit_code, stdout, stderr = _invoke_measure_run(str(run_dir_loose))
    assert exit_code == 0, (stdout, stderr)
    payload = json.loads(stdout)
    assert payload.get("f2_context_pct") is None
    assert "PHASE9_CONTEXT_FILE_MISSING" not in payload.get(
        "failure_tokens", []
    )


# ---------------------------------------------------------------------------
# Matrix aggregator tests (Tests 7-8).
# ---------------------------------------------------------------------------


def _populate_runs_dir(tmp_path: Path) -> Path:
    """Synthesize 10 cohort run dirs under tmp_path/runs/ for matrix tests."""
    runs = tmp_path / "runs"
    runs.mkdir()
    minimal_handoffs = (FIXTURES / "handoffs_minimal.jsonl").read_text(
        encoding="utf-8"
    )
    manifest = (FIXTURES / "manifest_v2_1.json").read_text(encoding="utf-8")
    defects = (FIXTURES / "defects_per_stream.json").read_text(
        encoding="utf-8"
    )
    state = (FIXTURES / "state_cycle_3.json").read_text(encoding="utf-8")
    for cohort_id in sorted(EXPECTED_KNOWN_PHASE9_COHORT_IDS):
        run = runs / cohort_id
        run.mkdir()
        (run / "handoffs.jsonl").write_text(
            minimal_handoffs, encoding="utf-8"
        )
        (run / "manifest.json").write_text(manifest, encoding="utf-8")
        (run / "defects.json").write_text(defects, encoding="utf-8")
        (run / "state.json").write_text(state, encoding="utf-8")
        (run / "context-at-f2.txt").write_text("42.7", encoding="utf-8")
        cohort_json = {
            "cohort_id": cohort_id,
            "disable_lever_mechanism": "none",
            "disable_lever_description": "matrix test fixture",
            "expected_gate_verdicts": {
                "cycles": "PASS",
                "defect_yield_per_stream": "PASS",
                "f2_context_pct": "PASS",
                "wall_clock_regression_pct": "PASS",
            },
            "expected_intervention_contribution": None,
            "archive_subdir": str(run),
            "pre_phase_1_sha": (
                "2171f1f" if cohort_id == "v4_2_0_baseline" else None
            ),
            "spec_path": "forge-specs/phase9-sloppy/spec.md",
            "spec_format_version": "v2.1",
        }
        (run / "cohort.json").write_text(
            json.dumps(cohort_json), encoding="utf-8"
        )
    return runs


def test_matrix_csv_shape(tmp_path: Path) -> None:
    """Test 7 — ``--matrix runs_dir --format csv`` emits CSV with 10 data
    rows (one per cohort) + 1 header row; columns match the cohort matrix
    table shape.
    """
    runs = _populate_runs_dir(tmp_path)
    exit_code, stdout, stderr = _invoke_measure_run(
        "--matrix", str(runs), "--format", "csv"
    )
    assert exit_code == 0, (stdout, stderr)
    reader = csv.reader(io.StringIO(stdout))
    rows = list(reader)
    assert len(rows) == 11, f"expected 1 header + 10 data rows, got {len(rows)}"
    header = rows[0]
    cohort_col = rows[1:]
    cohort_ids = {r[0] for r in cohort_col}
    assert cohort_ids == EXPECTED_KNOWN_PHASE9_COHORT_IDS
    # Required columns per 09-RESEARCH.md Example 4.
    for col in ("cohort_id", "cycles", "f2_context_pct", "wall_clock_seconds"):
        assert col in header, header


def test_matrix_markdown_table_shape(tmp_path: Path) -> None:
    """Test 8 — ``--matrix runs_dir --format markdown`` emits a table whose
    row/column count matches the CSV format and whose data values are
    byte-equivalent.
    """
    runs = _populate_runs_dir(tmp_path)
    exit_code_csv, stdout_csv, _ = _invoke_measure_run(
        "--matrix", str(runs), "--format", "csv"
    )
    exit_code_md, stdout_md, _ = _invoke_measure_run(
        "--matrix", str(runs), "--format", "markdown"
    )
    assert exit_code_csv == 0
    assert exit_code_md == 0
    csv_rows = list(csv.reader(io.StringIO(stdout_csv)))
    # Markdown table: count pipe-rows; subtract header + separator (-/--/---).
    md_lines = [
        ln for ln in stdout_md.splitlines() if ln.strip().startswith("|")
    ]
    # Markdown table = header + separator + 10 data rows = 12 pipe-lines.
    assert len(md_lines) == 12, (
        f"expected 12 markdown pipe-lines, got {len(md_lines)}"
    )
    # Cross-check: each cohort_id present in both formats.
    csv_cohorts = {r[0] for r in csv_rows[1:]}
    md_cohorts = {
        ln.split("|")[1].strip()
        for ln in md_lines[2:]  # skip header + separator
    }
    assert csv_cohorts == md_cohorts == EXPECTED_KNOWN_PHASE9_COHORT_IDS


# ---------------------------------------------------------------------------
# Closed-vocabulary frozenset + anti-drift tests (Tests 9-11).
# ---------------------------------------------------------------------------


def test_known_phase9_stream_ids_matches_authoritative_sources() -> None:
    """Test 9 — anti-drift cross-grep against four authoritative sources:

    1. start.md F0.5 step 2b roster (agent paths -> stream IDs via id frontmatter)
    2. start.md F2 INSPECT block
    3. agent files' id: frontmatter
    4. evidence.py constant MIN_SPEC_FORMAT_VERSION_FOR_EVID_01

    Plan 09-02 will encode KNOWN_PHASE9_STREAM_IDS in measure-run.py; this
    test re-derives the expected set from disk and compares.

    Per 09-RESEARCH.md Example 2: cross-grep covers (a) start.md roster +
    INSPECT block, (b) agent file frontmatter, (c) evidence.py constant.
    """
    # Source 3: agent file id frontmatter — at minimum these three IDs.
    agent_ids = set()
    for agent in (INTENT_CARRIER, SPEC_TEST_DERIVER, SPEC_REVIEWER):
        text = agent.read_text(encoding="utf-8")
        # Frontmatter id: line.
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("id:"):
                agent_ids.add(stripped.split(":", 1)[1].strip())
                break
    expected_in_agents = {"INTENT-01", "TEST-01", "PROBE-01"}
    assert expected_in_agents.issubset(agent_ids), (
        f"agent frontmatter missing IDs: "
        f"{expected_in_agents - agent_ids}"
    )
    # Sub-check: every agent-derived stream id is in the locked frozenset.
    assert agent_ids.issubset(EXPECTED_KNOWN_PHASE9_STREAM_IDS)

    # Source 4: evidence.py constant — EVID-01 stream and v2.1 minimum.
    ev_text = EVIDENCE_PY.read_text(encoding="utf-8")
    assert "MIN_SPEC_FORMAT_VERSION_FOR_EVID_01" in ev_text, (
        "evidence.py missing MIN_SPEC_FORMAT_VERSION_FOR_EVID_01 constant"
    )
    assert "EVID-01" in EXPECTED_KNOWN_PHASE9_STREAM_IDS

    # Source 1+2: start.md mentions all four stream IDs in either F0.5 step
    # 2b or F2 INSPECT.
    start_text = START_MD.read_text(encoding="utf-8")
    for sid in ("INTENT-01", "TEST-01", "PROBE-01", "EVID-01"):
        assert sid in start_text, f"start.md missing {sid}"

    # Plan 09-02 territory: import KNOWN_PHASE9_STREAM_IDS from measure-run.py
    # and assert byte-equivalence with EXPECTED_KNOWN_PHASE9_STREAM_IDS.
    # Until then we exercise the disk-side cross-grep above; the script-side
    # check fires once measure-run.py ships.
    script_text = SCRIPT.read_text(encoding="utf-8")
    assert "KNOWN_PHASE9_STREAM_IDS" in script_text, (
        "measure-run.py must export KNOWN_PHASE9_STREAM_IDS frozenset"
    )
    for sid in EXPECTED_KNOWN_PHASE9_STREAM_IDS:
        assert sid in script_text, (
            f"measure-run.py KNOWN_PHASE9_STREAM_IDS missing {sid}"
        )


def test_known_phase9_cohort_ids_matches_runs_dir() -> None:
    """Test 10 — set of subdirectory names under .planning/phases/09-.../runs/
    matches KNOWN_PHASE9_COHORT_IDS exactly.

    Skipped when .planning/ is absent. Those cohort stubs are local planning
    artifacts that are not committed, so on a clean clone this asserted against
    a directory that cannot exist and failed for every contributor — which
    trains people to ignore a red suite. Skip is the honest signal: the check is
    real where the data is real, and silent where it is not.
    """
    if not RUNS_DIR.exists():
        pytest.skip(f"planning cohort stubs not present in this checkout: {RUNS_DIR}")
    on_disk = {p.name for p in RUNS_DIR.iterdir() if p.is_dir()}
    assert on_disk == EXPECTED_KNOWN_PHASE9_COHORT_IDS, (
        f"on-disk cohorts vs locked frozenset diff: "
        f"on-disk-only={on_disk - EXPECTED_KNOWN_PHASE9_COHORT_IDS}, "
        f"locked-only={EXPECTED_KNOWN_PHASE9_COHORT_IDS - on_disk}"
    )
    # And every subdir contains a valid cohort.json with matching cohort_id.
    for cohort_dir in RUNS_DIR.iterdir():
        if not cohort_dir.is_dir():
            continue
        cohort_json = cohort_dir / "cohort.json"
        assert cohort_json.exists(), f"missing cohort.json: {cohort_dir}"
        data = json.loads(cohort_json.read_text(encoding="utf-8"))
        assert data["cohort_id"] == cohort_dir.name


def test_known_phase9_failure_tokens_present() -> None:
    """Test 11 — KNOWN_PHASE9_FAILURE_TOKENS contains all 8 named tokens.

    Plan 09-02 will encode this frozenset in measure-run.py; the test
    asserts the script's literal source contains each token name.
    """
    script_text = SCRIPT.read_text(encoding="utf-8")
    for token in EXPECTED_KNOWN_PHASE9_FAILURE_TOKENS:
        assert token in script_text, (
            f"measure-run.py missing failure token: {token}"
        )
    # And the frozenset name itself is exposed.
    assert "KNOWN_PHASE9_FAILURE_TOKENS" in script_text


# ---------------------------------------------------------------------------
# Quantitative gate + saturation tests (Tests 12-14).
# ---------------------------------------------------------------------------


def test_wall_clock_regression_pct_arithmetic(tmp_path: Path) -> None:
    """Test 12 — ``(cohort_seconds / v4_2_0_seconds - 1) * 100`` computed
    correctly for sample inputs (v4_2_0=100s, cohort=140s -> 40.0;
    v4_2_0=100s, cohort=160s -> 60.0).

    The matrix aggregator computes this column for every non-baseline cohort.
    The test invokes a hidden ``--compute-regression`` helper subcommand
    (Plan 09-02 territory) or, fallback, the matrix command with synthesized
    handoffs that span the configured wall-clock windows.
    """
    # Simple shape: invoke a calculator subcommand if Plan 09-02 ships one.
    exit_code, stdout, stderr = _invoke_measure_run(
        "--compute-regression",
        "--baseline-seconds", "100",
        "--cohort-seconds", "140",
    )
    assert exit_code == 0, (stdout, stderr)
    payload = json.loads(stdout)
    assert payload["wall_clock_regression_pct"] == pytest.approx(40.0)

    exit_code2, stdout2, _ = _invoke_measure_run(
        "--compute-regression",
        "--baseline-seconds", "100",
        "--cohort-seconds", "160",
    )
    assert exit_code2 == 0, stdout2
    payload2 = json.loads(stdout2)
    assert payload2["wall_clock_regression_pct"] == pytest.approx(60.0)


def test_run_01_quantitative_gates() -> None:
    """Test 13 — 4 RUN-01 gates per cohort:
       cycles ≤ 8 -> PASS; yield 5-50% -> PASS;
       context < 50% -> PASS; wall-clock regression < 50% -> PASS;
       out-of-band -> FAIL.

    Exercises the gate-evaluation function directly via the
    ``--evaluate-gates`` helper (Plan 09-02 territory). Table-driven across
    boundary cases.
    """
    cases = [
        # (cycles, yield_pct, context_pct, regression_pct, expected_verdict)
        (8, 25.0, 42.0, 30.0, "PASS"),    # all in-band
        (9, 25.0, 42.0, 30.0, "FAIL"),    # cycles over
        (8, 4.9, 42.0, 30.0, "FAIL"),     # yield under
        (8, 50.1, 42.0, 30.0, "FAIL"),    # yield over
        (8, 25.0, 49.9, 30.0, "PASS"),    # context just under cap
        (8, 25.0, 50.0, 30.0, "FAIL"),    # context at cap (cap is < 50)
        (8, 25.0, 42.0, 49.9, "PASS"),    # regression just under cap
        (8, 25.0, 42.0, 50.0, "FAIL"),    # regression at cap
    ]
    for cycles, yld, ctx, reg, expected in cases:
        exit_code, stdout, stderr = _invoke_measure_run(
            "--evaluate-gates",
            "--cycles", str(cycles),
            "--yield-pct", str(yld),
            "--context-pct", str(ctx),
            "--regression-pct", str(reg),
        )
        assert exit_code == 0, stderr
        payload = json.loads(stdout)
        assert payload["overall_verdict"] == expected, (
            f"case={cycles, yld, ctx, reg}: "
            f"expected {expected}, got {payload['overall_verdict']}"
        )


def test_saturation_threshold_dual_criterion() -> None:
    """Test 14 — dual-criterion saturation logic:
       baseline_count ≤ 5 -> ±1 count floor branch;
       baseline_count > 5 -> ±10% yield branch.

    Both branches verified with table-driven test cases via
    ``--evaluate-saturation`` helper (Plan 09-02 territory).
    """
    cases = [
        # (baseline_count, cohort_count, baseline_yield_pct, cohort_yield_pct, expected_saturated)
        # Branch A: baseline_count ≤ 5 — floor of ±1 count diff.
        (4, 4, 20.0, 20.0, True),   # diff 0 -> saturated
        (4, 5, 20.0, 25.0, True),   # diff 1 -> saturated (within floor)
        (4, 6, 20.0, 30.0, False),  # diff 2 -> NOT saturated
        (3, 2, 15.0, 10.0, True),   # diff -1 -> saturated (abs ≤ 1)
        # Branch B: baseline_count > 5 — primary ±10% yield-percentage diff.
        (10, 10, 25.0, 25.0, True),   # 0% diff -> saturated
        (10, 11, 25.0, 27.5, True),   # 10% diff -> saturated (at threshold)
        (10, 12, 25.0, 30.0, False),  # 20% diff -> NOT saturated
        (8, 6, 20.0, 15.0, False),    # 25% diff -> NOT saturated
    ]
    for bl_count, co_count, bl_yld, co_yld, expected in cases:
        exit_code, stdout, stderr = _invoke_measure_run(
            "--evaluate-saturation",
            "--baseline-count", str(bl_count),
            "--cohort-count", str(co_count),
            "--baseline-yield-pct", str(bl_yld),
            "--cohort-yield-pct", str(co_yld),
        )
        assert exit_code == 0, stderr
        payload = json.loads(stdout)
        assert payload["saturated"] is expected, (
            f"case=(bl={bl_count}, co={co_count}, "
            f"bl_yld={bl_yld}, co_yld={co_yld}): "
            f"expected {expected}, got {payload['saturated']}"
        )
