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

  FR-013 vocabulary derivation + the FR-018 key/case repair (Tests 15-17).

  D-034 — the gates operating on a REAL archive (Tests 18-28): absent
  cohort.json and context-at-f2.txt are cohort-study inputs no run writes, the
  per-cycle roll-up is read rather than ignored, a roll-up proving a higher
  cycle than state.json names the stale counter, and --context-pct /
  --baseline-seconds turn the two structurally-MISSING gates into real
  verdicts.

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
import shutil
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
# FR-013: the canonical vocabulary module measure-run.py now derives its
# stream roster from. This is where the roster literals live post-FR-013.
VOCAB_PY = (
    REPO_ROOT / "plugins" / "foundry" / "mcp-server" / "src" / "foundry_mcp"
    / "schemas" / "vocab.py"
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
        omit_cohort: bool = False,
        rollup: dict[str, Any] | None = None,
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
        if not omit_cohort:
            (run_dir / "cohort.json").write_text(
                json.dumps(cohort_json), encoding="utf-8"
            )
        if rollup is not None:
            (run_dir / "stream-rollup.json").write_text(
                json.dumps(rollup), encoding="utf-8"
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

    # Script side: measure-run.py still exposes KNOWN_PHASE9_STREAM_IDS, but
    # per FR-013 it DERIVES it from foundry_mcp.schemas.vocab rather than
    # re-typing the roster. The literal cross-grep therefore runs against
    # vocab.py, the roster's single source of truth — same anti-drift check,
    # pointed at the copy that is now authoritative.
    script_text = SCRIPT.read_text(encoding="utf-8")
    assert "KNOWN_PHASE9_STREAM_IDS" in script_text, (
        "measure-run.py must export KNOWN_PHASE9_STREAM_IDS frozenset"
    )
    assert "CANONICAL_STREAM_IDS" in script_text, (
        "measure-run.py must derive its roster from vocab.CANONICAL_STREAM_IDS"
    )
    vocab_text = VOCAB_PY.read_text(encoding="utf-8")
    for sid in EXPECTED_KNOWN_PHASE9_STREAM_IDS:
        assert sid in vocab_text, (
            f"vocab.py CANONICAL_STREAM_IDS missing {sid}"
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
        payload = json.loads(stdout)
        assert payload["overall_verdict"] == expected, (
            f"case={cycles, yld, ctx, reg}: "
            f"expected {expected}, got {payload['overall_verdict']}"
        )
        # D-105: this path printed overall_verdict FAIL and then returned 0
        # unconditionally, and this loop asserted that 0 on all eight cases —
        # five of which it simultaneously asserted were FAIL. The verdict and
        # the status must agree at every boundary, or the band constants below
        # are documentation rather than gates.
        assert exit_code == (1 if expected == "FAIL" else 0), (
            f"case={cycles, yld, ctx, reg}: verdict {expected} "
            f"but exit {exit_code}{stderr}"
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


# ---------------------------------------------------------------------------
# FR-013 / FR-018 — vocabulary derivation + the real-archive key/case repair.
# ---------------------------------------------------------------------------


def _load_measure_run_module():
    """Import the dash-named measure-run.py as a module object.

    Loading the real file (rather than grepping its text) is what proves the
    roster is DERIVED at import time and not merely mentioned in a comment.
    """
    import importlib.util

    spec = importlib.util.spec_from_file_location("_measure_run_under_test", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # measure-run.py defines a @dataclass, whose annotation resolution looks
    # the defining module up in sys.modules — register before exec_module.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_known_phase9_stream_ids_is_derived_from_vocab() -> None:
    """Test 15 — the ``measure-run.py roster <- vocab.py`` key link (FR-013).

    KNOWN_PHASE9_STREAM_IDS must not be a re-typed copy: it must BE
    vocab.CANONICAL_STREAM_IDS. Identity (``is``) is asserted, not just
    equality, so a future hand-typed duplicate that happens to match today
    still fails this test.
    """
    from foundry_mcp.schemas import vocab

    module = _load_measure_run_module()
    assert module.KNOWN_PHASE9_STREAM_IDS is vocab.CANONICAL_STREAM_IDS, (
        "measure-run.py must derive its roster from vocab, not re-type it"
    )
    assert module.KNOWN_PHASE9_STREAM_IDS == EXPECTED_KNOWN_PHASE9_STREAM_IDS
    assert len(module.KNOWN_PHASE9_STREAM_IDS) == 15


def test_lowercase_source_counted_under_canonical_stream_id(
    make_run_dir: Callable[..., Path],
) -> None:
    """Test 16 — FR-018 / AC-024 regression.

    Every writer of defects.json persists the filing stream as ``source``,
    lowercase (tools/foundry.py, tools/foundry_orchestrator.py). measure-run
    read ``d.get("stream")`` against an UPPERCASE roster, so per_stream_defects
    was ALWAYS empty and every record emitted PHASE9_DEFECTS_FILE_MALFORMED.
    This pins both halves of that repair: the key and the case.
    """
    run_dir = make_run_dir()
    (run_dir / "defects.json").write_text(
        json.dumps(
            {
                "defects": [
                    {"id": "D-001", "source": "prove", "type": "THIN"},
                    {"id": "D-002", "source": "prove", "type": "WRONG"},
                    {"id": "D-003", "source": "trace", "type": "MISSING"},
                    {"id": "D-004", "source": "test01", "type": "FAIL"},
                ]
            }
        ),
        encoding="utf-8",
    )
    exit_code, stdout, stderr = _invoke_measure_run(str(run_dir))
    assert exit_code == 0, (stdout, stderr)
    payload = json.loads(stdout)
    assert payload["per_stream_defects"] == {
        "PROVE": 2,
        "TRACE": 1,
        "TEST-01": 1,
    }, payload["per_stream_defects"]
    assert "PHASE9_DEFECTS_FILE_MALFORMED" not in payload["failure_tokens"]


def test_legacy_stream_key_still_counted(
    make_run_dir: Callable[..., Path],
) -> None:
    """Test 17 — an archive written under the legacy ``stream`` key still
    counts, so the key repair loses no pre-existing data.

    The claim under test is "no REJECTION", which is ``failure_tokens == []``
    — a strictly sharper assertion than the exit code this used to read. A
    one-record ledger puts 100% of the yield on a single stream, so post-D-105
    the yield gate FAILs and the status is 1; that says nothing about the
    legacy key, and asserting 0 here would have re-pinned the bug D-105 fixed.
    """
    run_dir = make_run_dir()
    (run_dir / "defects.json").write_text(
        json.dumps({"defects": [{"id": "D-001", "stream": "TRACE"}]}),
        encoding="utf-8",
    )
    exit_code, stdout, stderr = _invoke_measure_run(str(run_dir))
    payload = json.loads(stdout)
    assert payload["per_stream_defects"] == {"TRACE": 1}
    assert payload["failure_tokens"] == [], (
        "the legacy `stream` key is being rejected, not merely gated"
    )
    # The nonzero status is the yield band on a single-record ledger, and
    # nothing else — every other gate is clean.
    assert payload["gate_verdicts"]["defect_yield_per_stream"] == "FAIL"
    assert payload["gate_verdicts"]["cycles"] == "PASS"
    assert exit_code == 1, (stdout, stderr)


def test_assay_and_temper_sourced_defects_are_counted_not_discarded(
    make_run_dir: Callable[..., Path],
) -> None:
    """D-091 / FR-018 — a defect `source` is resolved against the SOURCE
    vocabulary, not the stream roster.

    The key/case half of FR-018 was fixed; a VOCABULARY half survived it. The
    field being read is `source`, whose vocabulary is vocab.DEFECT_SOURCE_IDS
    (11 values, byte-equal to server.py's live Foundry-Defect `source` enum).
    It was resolved through ``canonical_stream_id``, which knows only the nine
    STREAM wire ids — ASSAY and TEMPER are adjudicators, not verification
    streams, so they are correctly absent from the roster. Each such record was
    therefore dropped from the counts AND reported as PHASE9_UNKNOWN_STREAM: a
    failure token naming a value the protocol declares legal.

    Both halves matter to NFR-001. ``_yield_band_verdict`` sums
    per_stream_defects, so the yield gate was evaluated on a truncated total;
    and grand-vulture's baseline holds only prove/test/trace sources, so the
    BASELINE measured clean while the comparison side under-counted — an
    instrument manufacturing the exact "defect finding dropped" signal the
    effort's global bar treats as proof the change is wrong.
    """
    run_dir = make_run_dir()
    (run_dir / "defects.json").write_text(
        json.dumps(
            {
                "defects": [
                    {"id": "D-001", "source": "prove", "type": "THIN"},
                    {"id": "D-002", "source": "trace", "type": "MISSING"},
                    {"id": "D-003", "source": "assay", "type": "WRONG"},
                    {"id": "D-004", "source": "temper", "type": "HOLLOW"},
                ]
            }
        ),
        encoding="utf-8",
    )
    exit_code, stdout, stderr = _invoke_measure_run(str(run_dir))
    assert exit_code == 0, (stdout, stderr)
    payload = json.loads(stdout)
    assert payload["per_stream_defects"] == {
        "PROVE": 1,
        "TRACE": 1,
        "ASSAY": 1,
        "TEMPER": 1,
    }, payload["per_stream_defects"]
    # The ledger's own total is what the yield gate must see.
    assert sum(payload["per_stream_defects"].values()) == 4
    assert payload["failure_tokens"] == [], (
        "a legal defect source is being reported as an unknown stream"
    )


def test_a_source_outside_the_defect_vocabulary_is_still_unknown(
    make_run_dir: Callable[..., Path],
) -> None:
    """PHASE9_UNKNOWN_STREAM keeps meaning what it always claimed to.

    The D-091 repair widens the resolver to the right vocabulary; it must not
    turn the refusal off. A value outside DEFECT_SOURCE_IDS is still named and
    still discarded — never coerced onto a known source.
    """
    run_dir = make_run_dir()
    (run_dir / "defects.json").write_text(
        json.dumps(
            {
                "defects": [
                    {"id": "D-001", "source": "prove", "type": "THIN"},
                    {"id": "D-002", "source": "haruspex", "type": "WRONG"},
                ]
            }
        ),
        encoding="utf-8",
    )
    exit_code, stdout, _ = _invoke_measure_run(str(run_dir))
    # A named token is a rejection — Test 25's contract for the roll-up reader,
    # and the defect reader owes the same.
    assert exit_code != 0
    payload = json.loads(stdout)
    assert payload["per_stream_defects"] == {"PROVE": 1}
    assert payload["failure_tokens"] == ["PHASE9_UNKNOWN_STREAM:haruspex"]


def test_the_defect_reader_uses_the_defect_source_resolver() -> None:
    """The key link, pinned at the source rather than only through behaviour.

    ``_read_defects_per_stream`` must call ``canonical_defect_source``. The
    roll-up reader keeps ``canonical_stream_id``, because a roll-up genuinely
    IS per-stream coverage and ASSAY files none — the two resolvers are
    siblings and each site must read its own vocabulary.
    """
    import inspect

    measure_run_module = _load_measure_run_module()
    source = inspect.getsource(measure_run_module._read_defects_per_stream)
    assert "canonical_defect_source(" in source, (
        "the defect reader no longer resolves `source` against the defect-source "
        "vocabulary; D-091 is back"
    )
    assert "canonical_stream_id(" not in source, (
        "the defect reader resolves a defect `source` through the STREAM "
        "resolver, which drops every assay- and temper-filed defect (D-091)"
    )
    rollup = inspect.getsource(measure_run_module._read_stream_rollup)
    assert "canonical_stream_id(" in rollup, (
        "the roll-up reader must keep the STREAM resolver — its keys are "
        "streams reporting coverage, not defect filers"
    )


# ---------------------------------------------------------------------------
# D-034 — the gates must operate on a REAL run archive (FR-018 / AC-024).
#
# A real foundry archive has no cohort.json and no context-at-f2.txt (both are
# hand-placed cohort-study inputs, written by no run), and — after the release
# that introduces it — a stream-rollup.json that measure-run never opened.
# ---------------------------------------------------------------------------


def _rollup_doc(cycles: dict[str, Any]) -> dict[str, Any]:
    """A stream-rollup.json in the shape the server writes and migration emits."""
    return {"cycles": cycles, "updated_at": "2026-08-30T04:34:50+00:00"}


def _entry(items_checked: int, items_total: int, findings: int) -> dict[str, Any]:
    return {
        "items_checked": items_checked,
        "items_total": items_total,
        "findings": findings,
        "records": [],
    }


def test_absent_cohort_json_is_not_a_schema_violation(
    make_run_dir: Callable[..., Path],
) -> None:
    """Test 18 — D-034's first symptom.

    No foundry run writes cohort.json, so treating its absence as
    PHASE9_SCHEMA_INVALID made EVERY real archive emit a failure token and exit
    1 — the instrumentation rejected real data by construction.
    """
    run_dir = make_run_dir(omit_cohort=True)
    exit_code, stdout, stderr = _invoke_measure_run(str(run_dir))
    assert exit_code == 0, (stdout, stderr)
    payload = json.loads(stdout)
    assert payload["failure_tokens"] == []
    assert payload["cohort_id"] == ""


def test_strict_flag_rejects_missing_cohort_json(
    make_run_dir: Callable[..., Path],
) -> None:
    """Test 19 — absence is strict-gated, exactly as context-at-f2.txt is,
    so the cohort-study workflow keeps its strictness.
    """
    run_dir = make_run_dir(omit_cohort=True, context_pct="42.7")
    exit_code, stdout, stderr = _invoke_measure_run("--strict", str(run_dir))
    assert exit_code != 0
    assert "PHASE9_SCHEMA_INVALID" in json.loads(stdout)["failure_tokens"]


def test_malformed_cohort_json_is_still_a_schema_violation(
    make_run_dir: Callable[..., Path],
) -> None:
    """Test 20 — tolerating ABSENCE must not tolerate a corrupt file."""
    run_dir = make_run_dir()
    (run_dir / "cohort.json").write_text("{not json", encoding="utf-8")
    exit_code, stdout, _ = _invoke_measure_run(str(run_dir))
    assert exit_code != 0
    assert "PHASE9_SCHEMA_INVALID" in json.loads(stdout)["failure_tokens"]


def test_stream_rollup_coverage_is_read_and_reported(
    make_run_dir: Callable[..., Path],
) -> None:
    """Test 21 — D-034's third symptom: the roll-up was never opened.

    defects.json records what each stream FOUND; only stream-rollup.json
    records what it CHECKED, so without it the instrumentation reported defect
    yield with no denominator. Coverage is re-keyed onto the canonical
    UPPERCASE ids so the payload speaks one spelling throughout.
    """
    run_dir = make_run_dir(
        rollup=_rollup_doc(
            {
                "2": {"prove": _entry(80, 80, 4), "trace": _entry(12, 15, 1)},
                "3": {"prove": _entry(165, 165, 0), "test01": _entry(9, 9, 0)},
            }
        )
    )
    exit_code, stdout, stderr = _invoke_measure_run(str(run_dir))
    assert exit_code == 0, (stdout, stderr)
    payload = json.loads(stdout)
    assert payload["per_cycle_coverage"] == {
        "2": {
            "PROVE": {"items_checked": 80, "items_total": 80, "findings": 4},
            "TRACE": {"items_checked": 12, "items_total": 15, "findings": 1},
        },
        "3": {
            "PROVE": {"items_checked": 165, "items_total": 165, "findings": 0},
            "TEST-01": {"items_checked": 9, "items_total": 9, "findings": 0},
        },
    }


def test_absent_stream_rollup_is_not_a_failure(
    make_run_dir: Callable[..., Path],
) -> None:
    """Test 22 — archives written before the roll-up existed are exactly the
    ones this instrumentation has to measure, so absence reports empty.
    """
    run_dir = make_run_dir()
    exit_code, stdout, stderr = _invoke_measure_run(str(run_dir))
    assert exit_code == 0, (stdout, stderr)
    payload = json.loads(stdout)
    assert payload["per_cycle_coverage"] == {}
    assert payload["failure_tokens"] == []


def test_rollup_proving_a_higher_cycle_names_the_stale_counter(
    make_run_dir: Callable[..., Path],
) -> None:
    """Test 23 — survey/data.md FI-1.

    ``state.json["cycle"]`` was written once as 0 and never incremented, so an
    unrepaired archive reports a cycle count the run never had and the
    convergence gate PASSes on fiction. The roll-up is keyed BY the server-side
    cycle counter (FR-005 / ST-001), so its highest key is that counter read
    from the artifact — not a second counter invented here. When it proves
    more, report the proven value and NAME the stale one.
    """
    run_dir = make_run_dir(  # fixture state.json records cycle 3
        rollup=_rollup_doc({"3": {"prove": _entry(80, 80, 1)},
                            "7": {"prove": _entry(80, 80, 0)}})
    )
    exit_code, stdout, _ = _invoke_measure_run(str(run_dir))
    payload = json.loads(stdout)
    # Index 7 is the EIGHTH cycle — the counter is 0-based (see
    # _reconcile_final_cycle_index) and ``cycles`` publishes a COUNT.
    assert payload["cycles"] == 8, "must report the cycles the run reached"
    assert "PHASE9_CYCLE_COUNT_INVALID" in payload["failure_tokens"]
    assert exit_code != 0


def test_rollup_agreeing_with_state_is_silent(
    make_run_dir: Callable[..., Path],
) -> None:
    """Test 24 — a repaired archive reconciles cleanly and stays quiet."""
    run_dir = make_run_dir(
        rollup=_rollup_doc({"3": {"prove": _entry(80, 80, 1)}})
    )
    exit_code, stdout, stderr = _invoke_measure_run(str(run_dir))
    assert exit_code == 0, (stdout, stderr)
    payload = json.loads(stdout)
    assert payload["cycles"] == 4, "index 3 is the fourth cycle"
    assert payload["failure_tokens"] == []


def test_unknown_stream_in_rollup_is_named(
    make_run_dir: Callable[..., Path],
) -> None:
    """Test 25 — a roll-up key outside the roster is NAMED, never coerced."""
    run_dir = make_run_dir(rollup=_rollup_doc({"3": {"bogus": _entry(1, 1, 0)}}))
    exit_code, stdout, _ = _invoke_measure_run(str(run_dir))
    assert exit_code != 0
    assert "PHASE9_UNKNOWN_STREAM:bogus" in json.loads(stdout)["failure_tokens"]


def test_malformed_stream_rollup_is_a_schema_violation(
    make_run_dir: Callable[..., Path],
) -> None:
    """Test 26 — a roll-up that EXISTS but is not the documented shape."""
    run_dir = make_run_dir()
    (run_dir / "stream-rollup.json").write_text("{not json", encoding="utf-8")
    exit_code, stdout, _ = _invoke_measure_run(str(run_dir))
    assert exit_code != 0
    assert "PHASE9_SCHEMA_INVALID" in json.loads(stdout)["failure_tokens"]


def test_operator_inputs_turn_the_two_missing_gates_real(
    make_run_dir: Callable[..., Path],
) -> None:
    """Test 27 — D-034's second symptom: 2 of 4 gates permanently MISSING.

    The F2 context percentage and the wall-clock baseline are measurements no
    archive holds, so without an input path those two gates can NEVER be
    anything but MISSING. ``--context-pct`` and ``--baseline-seconds`` supply
    them; all four gates then return a real verdict.
    """
    run_dir = make_run_dir(omit_cohort=True, context_pct=None)

    # Without the inputs: honest MISSING on exactly those two gates.
    exit_code, stdout, stderr = _invoke_measure_run(str(run_dir))
    assert exit_code == 0, (stdout, stderr)
    before = json.loads(stdout)["gate_verdicts"]
    assert before["f2_context_pct"] == "MISSING"
    assert before["wall_clock_regression_pct"] == "MISSING"

    # With them: four real verdicts, no MISSING left.
    exit_code, stdout, stderr = _invoke_measure_run(
        str(run_dir), "--context-pct", "41.5", "--baseline-seconds", "100.0"
    )
    assert exit_code == 0, (stdout, stderr)
    payload = json.loads(stdout)
    assert payload["f2_context_pct"] == 41.5
    assert "MISSING" not in payload["gate_verdicts"].values(), payload["gate_verdicts"]
    assert payload["gate_verdicts"]["f2_context_pct"] == "PASS"


def test_context_pct_override_wins_over_the_file(
    make_run_dir: Callable[..., Path],
) -> None:
    """Test 28 — an explicit measurement beats a stale on-disk one."""
    run_dir = make_run_dir(context_pct="42.7")
    exit_code, stdout, stderr = _invoke_measure_run(
        str(run_dir), "--context-pct", "13.5"
    )
    assert exit_code == 0, (stdout, stderr)
    assert json.loads(stdout)["f2_context_pct"] == 13.5


# ---------------------------------------------------------------------------
# D-063 — ``cycles`` is a COUNT, and the convergence gate reads it as one.
#
# The server's counter is 0-based (Foundry-Init writes 0; the F1 -> F2 entry
# from CAST is not a new cycle; only the F3 -> F2 boundary increments), so a run
# that executed N cycles ends at index N-1. Publishing the raw index reported
# every run one cycle short and let the convergence gate admit one cycle more
# than MAX_CYCLES_FOR_CONVERGENCE names, at every threshold value.
# ---------------------------------------------------------------------------


MIGRATE_SCRIPT = REPO_ROOT / "plugins" / "foundry" / "scripts" / "migrate-archive.py"
GRAND_VULTURE = REPO_ROOT / "foundry-archive" / "grand-vulture"


def test_convergence_gate_threshold_counts_cycles_not_indices(
    make_run_dir: Callable[..., Path],
) -> None:
    """The gate half of D-063.

    A run at index 8 executed NINE cycles. The gate must FAIL it against a
    threshold of 8, and the boundary case (index 7 = eight cycles) must PASS.
    """
    module = _load_measure_run_module()
    assert module.MAX_CYCLES_FOR_CONVERGENCE == 8

    over = make_run_dir(
        rollup=_rollup_doc({"8": {"prove": _entry(10, 10, 0)}}),
        cohort_id="no_TYPE_01",
    )
    payload = json.loads(_invoke_measure_run(str(over))[1])
    assert payload["cycles"] == 9, "index 8 is the ninth cycle"
    assert payload["gate_verdicts"]["cycles"] == "FAIL"

    at_threshold = make_run_dir(
        rollup=_rollup_doc({"7": {"prove": _entry(10, 10, 0)}}),
        cohort_id="no_TYPE_02",
    )
    payload = json.loads(_invoke_measure_run(str(at_threshold))[1])
    assert payload["cycles"] == 8
    assert payload["gate_verdicts"]["cycles"] == "PASS"

    # The operator-supplied path already spoke in COUNTS; both paths now agree.
    exit_code, stdout, _ = _invoke_measure_run(
        "--evaluate-gates", "--cycles", "9",
        "--yield-pct", "25.0", "--context-pct", "42.0", "--regression-pct", "30.0",
    )
    assert json.loads(stdout)["gate_verdicts"]["cycles"] == "FAIL"
    assert exit_code == 1, "a blown convergence gate is a nonzero status (D-105)"


@pytest.mark.skipif(
    not GRAND_VULTURE.exists(),
    reason=(
        f"grand-vulture archive not present in this checkout: {GRAND_VULTURE} "
        "(foundry-archive/ is git-ignored)"
    ),
)
def test_grand_vulture_reports_nfr_001s_two_baseline_numbers(tmp_path: Path) -> None:
    """NFR-001 writes its baseline as "18 cycles, 168 defects".

    Both numbers are read from the SAME archive by the SAME command, so they
    can never again disagree: the defect half already matched to the unit while
    the cycle half was one short, which is what ruled out coincidence when
    D-063 was filed. The archive is copied first — never opened for writing.
    """
    dest = tmp_path / "grand-vulture"
    shutil.copytree(GRAND_VULTURE, dest)

    migrate = subprocess.run(
        [sys.executable, str(MIGRATE_SCRIPT), str(dest)],
        capture_output=True, text=True,
    )
    assert migrate.returncode == 0, migrate.stderr

    exit_code, stdout, stderr = _invoke_measure_run(str(dest))
    payload = json.loads(stdout)
    assert payload["cycles"] == 18, "NFR-001 baseline: 18 cycles"
    assert sum(payload["per_stream_defects"].values()) == 168, (
        "NFR-001 baseline: 168 defects"
    )
    # D-060's other half — the repaired archive must satisfy its own detector.
    # This is the token half of the exit contract, and it stays clean: nothing
    # about grand-vulture is malformed or unreadable.
    assert payload["failure_tokens"] == [], payload["failure_tokens"]

    # D-105 — the gate half. Those same two numbers are BOTH out of band:
    # 18 cycles against a convergence threshold of 8, and a defect yield
    # concentrated past the 50% ceiling. That is the whole premise of NFR-001,
    # which names grand-vulture as the baseline this effort must improve ON.
    # The instrument therefore has to say so out loud. It previously printed
    # both FAILs and exited 0 — the acceptance instrument certifying the very
    # run whose gates it had just watched blow.
    assert payload["gate_verdicts"]["cycles"] == "FAIL"
    assert payload["gate_verdicts"]["defect_yield_per_stream"] == "FAIL"
    assert exit_code == 1, (
        "measure-run reports success on the baseline archive whose gates it "
        f"just failed: {payload['gate_verdicts']} / {stderr}"
    )

    # And the real archive was never touched.
    assert json.loads((GRAND_VULTURE / "state.json").read_text())["cycle"] == 0


# ---------------------------------------------------------------------------
# D-105 — a gate verdict must reach the exit status.
#
# All three gate-evaluating entry points derived their status from
# failure_tokens alone, so a run could blow a gate, print the FAIL, and exit 0.
# The calibration was exactly backwards: a purely informational token
# (PHASE9_WALL_CLOCK_UNAVAILABLE, pinned by Test 4) exited 1 with every gate
# PASSing, while a blown convergence gate exited 0. NFR-001 makes this the
# effort's acceptance instrument; a gate whose verdict never reaches the exit
# status is not a gate.
# ---------------------------------------------------------------------------


def test_a_blown_gate_alone_is_a_nonzero_status(
    make_run_dir: Callable[..., Path],
) -> None:
    """The per-run path, with a gate FAIL as the ONLY thing wrong.

    ``--context-pct 50.0`` is the cleanest isolation available: it blows one
    gate (the cap is a strict ``<``) while leaving failure_tokens empty, so the
    status can come from nowhere else. 49.9 is the control — one tenth of a
    point away, the same code path, exit 0. Together they also pin the band
    boundary itself, which D-105 verified as consistent and must not move.
    """
    run_dir = make_run_dir()

    exit_code, stdout, stderr = _invoke_measure_run(
        str(run_dir), "--context-pct", "50.0"
    )
    payload = json.loads(stdout)
    assert payload["failure_tokens"] == [], (
        "isolation broken — the status could come from a token, not the gate"
    )
    assert payload["gate_verdicts"]["f2_context_pct"] == "FAIL"
    assert exit_code == 1, (
        f"gate FAIL printed but not routed to the exit status: {stderr}"
    )

    exit_code, stdout, stderr = _invoke_measure_run(
        str(run_dir), "--context-pct", "49.9"
    )
    payload = json.loads(stdout)
    assert payload["gate_verdicts"]["f2_context_pct"] == "PASS"
    assert exit_code == 0, (stdout, stderr)


def test_a_missing_gate_is_honest_not_failed(
    make_run_dir: Callable[..., Path],
) -> None:
    """MISSING maps to 0, and the mapping is deliberate.

    Two of the four gates read measurements no archive holds, so MISSING is the
    ordinary state of a run measured without ``--context-pct`` /
    ``--baseline-seconds``. Folding MISSING into the nonzero status would fail
    nearly every real run — the over-firing calibration D-034 already had to
    undo. Only a gate that was measured AND blown is a 1.
    """
    run_dir = make_run_dir(omit_cohort=True, context_pct=None)
    exit_code, stdout, stderr = _invoke_measure_run(str(run_dir))
    payload = json.loads(stdout)
    assert payload["failure_tokens"] == []
    verdicts = payload["gate_verdicts"]
    assert "MISSING" in verdicts.values(), verdicts
    assert "FAIL" not in verdicts.values(), verdicts
    assert exit_code == 0, (stdout, stderr)


def test_matrix_gate_fail_reaches_the_exit_status(tmp_path: Path) -> None:
    """The --matrix path, reproducing the exact row D-105 describes.

    A row rendering ``gate_verdict_overall=FAIL`` beside an EMPTY
    ``failure_tokens_csv`` was the visible shape of the bug: the aggregator
    read only the tokens column it had just printed blank. One cohort is given
    a single-record ledger, which puts 100% of the yield on one stream and
    blows the band with no token attached.
    """
    runs = _populate_runs_dir(tmp_path)
    (runs / "no_TYPE_01" / "defects.json").write_text(
        json.dumps({"defects": [{"id": "D-001", "source": "prove"}]}),
        encoding="utf-8",
    )
    exit_code, stdout, stderr = _invoke_measure_run(
        "--matrix", str(runs), "--format", "csv"
    )
    rows = {r[0]: r for r in csv.reader(io.StringIO(stdout))}
    header = list(csv.reader(io.StringIO(stdout)))[0]
    row = rows["no_TYPE_01"]
    assert row[header.index("gate_verdict_overall")] == "FAIL", row
    assert row[header.index("failure_tokens_csv")] == "", (
        "isolation broken — this row must fail on its VERDICT, not a token"
    )
    assert exit_code == 1, (
        f"a FAIL row in the matrix left the exit status at 0: {stderr}"
    )


# ---------------------------------------------------------------------------
# D-106 — --matrix over a directory holding no cohort arm.
#
# The roster comprehension dropped every non-cohort subdir with no token, no
# warning and no count, so a directory matching NONE of the ten ids produced a
# header row, zero data rows and exit 0. --matrix <a file> and --matrix
# <nonexistent> were both correctly refused; the ordinary mistake (the archive
# root instead of the runs dir, or arms named otherwise) was the single input
# that reported success.
# ---------------------------------------------------------------------------


def test_matrix_over_a_directory_with_no_cohorts_is_refused(
    tmp_path: Path,
) -> None:
    """The refusal is named, counts what it ignored, and exits nonzero."""
    runs = tmp_path / "archive-root"
    runs.mkdir()
    for name in ("grand-vulture", "thunder-viper", "process-fixes"):
        (runs / name).mkdir()

    exit_code, stdout, stderr = _invoke_measure_run("--matrix", str(runs))
    assert exit_code == 1, (stdout, stderr)
    assert "PHASE9_NO_COHORTS" in stderr, stderr
    # The operator has to be able to see WHICH arms were ignored, or the
    # refusal just relocates the guesswork.
    assert "3 subdirectories ignored" in stderr, stderr
    for name in ("grand-vulture", "thunder-viper", "process-fixes"):
        assert name in stderr, stderr
    # No table is printed for a directory that was never measured.
    assert stdout.strip() == "", stdout

    # --strict is not what makes this an error; it always was one.
    strict_exit, _, strict_err = _invoke_measure_run(
        "--matrix", str(runs), "--strict"
    )
    assert strict_exit == 1
    assert "PHASE9_NO_COHORTS" in strict_err


def test_matrix_over_an_empty_directory_is_refused(tmp_path: Path) -> None:
    """A directory with nothing in it at all takes the same refusal."""
    runs = tmp_path / "empty"
    runs.mkdir()
    exit_code, stdout, stderr = _invoke_measure_run("--matrix", str(runs))
    assert exit_code == 1, (stdout, stderr)
    assert "PHASE9_NO_COHORTS" in stderr, stderr
    assert "0 subdirectories ignored" in stderr, stderr


def test_matrix_reports_the_arms_it_ignored(tmp_path: Path) -> None:
    """Partial recognition is the dangerous middle case.

    Ten arms measured beside four that were dropped still exits on the gates
    alone — ignoring a docs/ directory is ordinary and must not become a
    refusal — but the operator is told what was left out, because "10 rows"
    and "10 of your 14 arms" look identical in the table.
    """
    runs = _populate_runs_dir(tmp_path)
    for name in ("docs", "no_TYPE_03", "v4_1_0_baseline", "scratch"):
        (runs / name).mkdir()
    # A stray file is not an ignored arm and must not be counted as one.
    (runs / "README.md").write_text("notes", encoding="utf-8")

    exit_code, stdout, stderr = _invoke_measure_run(
        "--matrix", str(runs), "--format", "csv"
    )
    assert exit_code == 0, (stdout, stderr)
    assert "4 non-cohort subdirectories ignored" in stderr, stderr
    for name in ("docs", "no_TYPE_03", "v4_1_0_baseline", "scratch"):
        assert name in stderr, stderr
    assert "README.md" not in stderr, stderr
    # The advisory is not a refusal, so it must not wear a token's name.
    assert "PHASE9_" not in stderr, stderr
    # And the ten real arms are still measured.
    rows = list(csv.reader(io.StringIO(stdout)))
    assert len(rows) == 11, f"expected 1 header + 10 data rows, got {len(rows)}"
    assert {r[0] for r in rows[1:]} == EXPECTED_KNOWN_PHASE9_COHORT_IDS


def test_no_cohorts_token_joins_the_closed_vocabulary() -> None:
    """D-106's token is a member of the frozenset, not a loose string.

    The closed-vocabulary discipline is that a refusal is a NAMED member of
    KNOWN_PHASE9_FAILURE_TOKENS — writing the string to stderr without
    enrolling it would make the roster a partial inventory of the refusals the
    script can actually emit, which is what the roster exists to prevent.
    """
    module = _load_measure_run_module()
    assert "PHASE9_NO_COHORTS" in module.KNOWN_PHASE9_FAILURE_TOKENS
    # The 8 locked per CONTEXT.md are still all present — extended, not
    # rewritten.
    assert EXPECTED_KNOWN_PHASE9_FAILURE_TOKENS.issubset(
        module.KNOWN_PHASE9_FAILURE_TOKENS
    )
    assert len(module.KNOWN_PHASE9_FAILURE_TOKENS) == 9


# ---------------------------------------------------------------------------
# NFR-001 / AC-039 / OT-030 / FR-051 — the convergence columns.
#
# thunder-viper is the archive these columns have to survive. It executed on the
# 4.7.3 server cache, so it wrote no escalation.json, no spend.jsonl, no
# stream-rollup.json and no `inspect_modes`, its state.json cycle counter stayed
# at 0 for all 22 cycles, and none of its 162 defect records carries a tier.
# OT-030 nonetheless requires this command to read it and print the baseline
# beside the current run's numbers.
#
# Two failure modes are guarded throughout, and they pull in opposite
# directions: reporting an unmeasured column as 0 (a fabricated number), and
# reporting it as a failure token (a healthy archive exiting nonzero — the
# over-firing calibration D-034 had to undo). The honest answer is null.
# ---------------------------------------------------------------------------

THUNDER_VIPER = REPO_ROOT / "foundry-archive" / "thunder-viper"


def _defects(*records: dict[str, Any]) -> str:
    return json.dumps({"defects": list(records)})


def _defect(did: str, source: str = "prove", **extra: Any) -> dict[str, Any]:
    return {"id": did, "cycle": 0, "source": source, "type": "THIN", **extra}


def test_defects_by_tier_counts_live_latent_and_unknown(
    make_run_dir: Callable[..., Path],
) -> None:
    """AC-039 — "defects by tier" is three columns, and all three are printed.

    A zero is a real measurement here (the ledger was read and held none), so
    every member of the vocabulary is present even at 0. Omitting a zero key
    would make "no LATENT defects" indistinguishable from "LATENT was never
    measured", which is the exact distinction the null columns below carry.
    """
    run_dir = make_run_dir()
    (run_dir / "defects.json").write_text(
        _defects(
            _defect("D-001", tier="LIVE"),
            _defect("D-002", tier="LIVE"),
            _defect("D-003", tier="LATENT",
                    reproduction_attempted="AST sweep finds 0 sites"),
            _defect("D-004"),                 # pre-change: no tier key at all
            _defect("D-005", tier=None),      # written, then nulled
            _defect("D-006", tier="MINOR"),   # a grade is not a tier
        ),
        encoding="utf-8",
    )
    payload = json.loads(_invoke_measure_run(str(run_dir))[1])
    assert payload["defects_by_tier"] == {"LIVE": 2, "LATENT": 1, "unknown": 3}
    # The two ledger columns read the same file and must agree on the total.
    assert sum(payload["defects_by_tier"].values()) == sum(
        payload["per_stream_defects"].values()
    )


def test_an_untiered_record_reads_as_unknown_and_never_as_latent(
    make_run_dir: Callable[..., Path],
) -> None:
    """FR-051, stated as the negative that matters.

    LATENT stops blocking at TEMPER, NYQUIST and DONE. A reader that resolved
    a missing tier to LATENT would report every pre-change archive as fully
    triaged and let three gates pass on records no stream ever looked at.
    """
    run_dir = make_run_dir()
    (run_dir / "defects.json").write_text(
        _defects(_defect("D-001"), _defect("D-002", tier=None)), encoding="utf-8"
    )
    payload = json.loads(_invoke_measure_run(str(run_dir))[1])
    assert payload["defects_by_tier"]["unknown"] == 2
    assert payload["defects_by_tier"]["LATENT"] == 0


def test_a_missing_defect_ledger_reports_null_not_a_row_of_zeros(
    make_run_dir: Callable[..., Path],
) -> None:
    """No ledger is not an empty ledger."""
    run_dir = make_run_dir()
    (run_dir / "defects.json").unlink()
    payload = json.loads(_invoke_measure_run(str(run_dir))[1])
    assert payload["defects_by_tier"] is None


def test_spend_is_rolled_up_per_phase_per_cycle_and_in_total(
    make_run_dir: Callable[..., Path],
) -> None:
    """AC-039 — "the tokens and minutes each phase and cycle spent".

    Minutes are reported beside milliseconds because the question an operator
    asks is "how long did F3 take", and a column that requires dividing by
    60_000 in your head stops being read.
    """
    run_dir = make_run_dir()
    (run_dir / "spend.jsonl").write_text(
        "\n".join(
            json.dumps(entry)
            for entry in [
                {"agent": "teammate-1", "phase": "F1", "cycle": 0,
                 "tokens": 1000, "duration_ms": 60_000},
                {"agent": "teammate-2", "phase": "F1", "cycle": 0,
                 "tokens": 500, "duration_ms": 30_000},
                {"agent": "prover", "phase": "F2", "cycle": 1,
                 "tokens": 2000, "duration_ms": 120_000},
                {"agent": "teammate-1", "phase": "F3", "cycle": 1,
                 "tokens": 250, "duration_ms": 15_000},
            ]
        ) + "\n",
        encoding="utf-8",
    )
    payload = json.loads(_invoke_measure_run(str(run_dir))[1])
    spend = payload["spend"]

    assert spend["by_phase"]["F1"] == {
        "tokens": 1500, "duration_ms": 90_000, "minutes": 1.5, "agents": 2,
    }
    assert spend["by_phase"]["F2"]["tokens"] == 2000
    assert spend["by_cycle"]["1"] == {
        "tokens": 2250, "duration_ms": 135_000, "minutes": 2.25, "agents": 2,
    }
    assert spend["total"]["tokens"] == 3750
    assert spend["total"]["minutes"] == 3.75
    # Two records from teammate-1 are two dispatches but one agent.
    assert spend["total"]["agents"] == 4
    assert spend["total"]["distinct_agents"] == 3


def test_a_torn_spend_line_costs_only_that_line(
    make_run_dir: Callable[..., Path],
) -> None:
    """The ledger is append-only from ~85 concurrent dispatches (A-AUTO-002).

    A half-written final line is an ordinary crash artifact, so it is skipped
    rather than failing the read — losing 84 real records to one torn one would
    be the whole measurement.
    """
    run_dir = make_run_dir()
    (run_dir / "spend.jsonl").write_text(
        json.dumps({"agent": "a", "phase": "F1", "cycle": 0,
                    "tokens": 10, "duration_ms": 1000})
        + "\n\n"
        + '{"agent": "b", "phase": "F1", "cyc',   # torn mid-write
        encoding="utf-8",
    )
    exit_code, stdout, _ = _invoke_measure_run(str(run_dir))
    payload = json.loads(stdout)
    assert payload["spend"]["total"]["tokens"] == 10
    assert payload["failure_tokens"] == [], "a torn line is not a refusal"


def test_inspect_modes_are_reported_per_cycle_with_the_f5_count(
    make_run_dir: Callable[..., Path],
) -> None:
    """AC-039 — "whether each inspect cycle ran at full or reduced width".

    `post_verification_cycles` counts DISTINCT cycles whose INSPECT was opened
    in F5. thunder-viper's REPORT.md is the definition: its build was verified
    at cycle 14 and cycles 15-22 were TEMPER hardening, which is the baseline's
    8. One cycle appearing in both F2 and F5 is counted once.
    """
    run_dir = make_run_dir()
    state = json.loads((run_dir / "state.json").read_text())
    state["inspect_modes"] = [
        {"cycle": 1, "phase": "F2", "mode": "FULL", "rule": "first_of_phase"},
        {"cycle": 2, "phase": "F2", "mode": "DELTA", "rule": "delta"},
        {"cycle": 3, "phase": "F2", "mode": "FULL", "rule": "verifier_touched"},
        {"cycle": 4, "phase": "F5", "mode": "FULL", "rule": "first_of_phase"},
        {"cycle": 5, "phase": "F5", "mode": "DELTA", "rule": "delta"},
    ]
    (run_dir / "state.json").write_text(json.dumps(state), encoding="utf-8")

    payload = json.loads(_invoke_measure_run(str(run_dir))[1])
    modes = payload["inspect_modes"]
    assert modes["per_cycle"]["3"] == {
        "phase": "F2", "mode": "FULL", "rule": "verifier_touched",
    }
    assert modes["by_mode"] == {"DELTA": 2, "FULL": 3}
    assert modes["post_verification_cycles"] == 2
    assert list(modes["per_cycle"]) == ["1", "2", "3", "4", "5"], "numeric order"
    assert payload["baseline_comparison"]["current"][
        "post_verification_cycles"
    ] == 2


def test_escalation_exit_reasons_are_counted(
    make_run_dir: Callable[..., Path],
) -> None:
    """FR-028 — the exit reason must be machine-readable and appear in a report.

    A class that left ESCALATED did so either on clean cycles or on an
    exhausted budget, and "which" is why the reason is recorded at all: a
    status flag alone cannot tell an operator whether the structural work
    landed or simply ran out of attempts.
    """
    run_dir = make_run_dir()
    (run_dir / "escalation.json").write_text(
        json.dumps(
            {
                "classes": {
                    "FALSE_DOCUMENTED_CONTRACT": {
                        "status": "CLEARED", "exit_reason": "clean_cycles",
                    },
                    "UNWIRED_TOOL": {"status": "CLEARED", "exit_reason": "budget"},
                    "THIN_HANDLER": {"status": "ESCALATED", "exit_reason": None},
                    # Written before this release's fields existed.
                    "LEGACY_CLASS": {"proposal": "restructure the dispatcher"},
                }
            }
        ),
        encoding="utf-8",
    )
    payload = json.loads(_invoke_measure_run(str(run_dir))[1])
    escalation = payload["escalation"]
    assert escalation["classes"] == 4
    assert escalation["by_status"] == {"CLEARED": 2, "ESCALATED": 2}
    assert escalation["by_exit_reason"] == {"budget": 1, "clean_cycles": 1}
    assert escalation["unknown_status"] == 0


def test_the_convergence_columns_never_fire_a_token_or_move_a_gate(
    make_run_dir: Callable[..., Path],
) -> None:
    """NFR-001: "Numbers are the target, not a gate."

    The run below misses BOTH convergence targets by a wide margin and has
    none of the three post-4.7.3 artifacts. It must still exit 0 with empty
    failure_tokens and the same four gate verdicts as a run that met them —
    the columns report, they do not judge.
    """
    baseline = make_run_dir(omit_cohort=True, context_pct=None)
    missed = make_run_dir(
        omit_cohort=True, context_pct=None, cohort_id="no_TYPE_01",
        rollup=_rollup_doc({"30": {"prove": _entry(10, 10, 0)}}),
    )
    state = json.loads((missed / "state.json").read_text())
    state["cycle"] = 30
    state["inspect_modes"] = [
        {"cycle": c, "phase": "F5", "mode": "FULL", "rule": "first_of_phase"}
        for c in range(20, 31)
    ]
    (missed / "state.json").write_text(json.dumps(state), encoding="utf-8")

    exit_code, stdout, stderr = _invoke_measure_run(str(missed))
    payload = json.loads(stdout)
    comparison = payload["baseline_comparison"]
    assert comparison["current"]["grind_cycles"] == 31
    assert comparison["current"]["post_verification_cycles"] == 11
    assert comparison["meets_target"] == {
        "grind_cycles": False, "post_verification_cycles": False,
    }
    # Missing both targets is reported and nothing else.
    assert payload["failure_tokens"] == [], payload["failure_tokens"]
    assert "convergence" not in stderr.lower()

    baseline_payload = json.loads(_invoke_measure_run(str(baseline))[1])
    assert set(payload["gate_verdicts"]) == set(baseline_payload["gate_verdicts"])
    assert payload["gate_verdicts"]["cycles"] == "FAIL", (
        "isolation check — the ONLY nonzero-status pressure here is the "
        "pre-existing convergence gate on `cycles`, not anything new"
    )


def test_a_run_missing_every_new_artifact_still_exits_clean(
    make_run_dir: Callable[..., Path],
) -> None:
    """The honesty pin, in the shape thunder-viper actually has.

    Three columns null, no tokens, exit 0. A zero in any of them would be a
    fabricated measurement; a token in any of them would make every archive
    written before this release exit nonzero on first read.
    """
    run_dir = make_run_dir(omit_cohort=True, context_pct=None)
    exit_code, stdout, stderr = _invoke_measure_run(str(run_dir))
    payload = json.loads(stdout)
    for column in ("spend", "inspect_modes", "escalation"):
        assert payload[column] is None, (column, payload[column])
    assert payload["failure_tokens"] == []
    assert exit_code == 0, (stdout, stderr)


def test_the_baseline_and_target_are_read_from_vocab_not_re_typed() -> None:
    """The measure-run -> vocab key link for the four NFR-001 numbers.

    Re-typing 22 and 8 here would make this script a second source of truth for
    a baseline the F6 report also prints, and the two would be free to drift —
    the exact failure FR-013 built vocab.py to end. Checked by IDENTITY of the
    values and by the absence of the literals from the module source.
    """
    from foundry_mcp.schemas import vocab

    module = _load_measure_run_module()
    assert module.THUNDER_VIPER_BASELINE is vocab.THUNDER_VIPER_BASELINE
    assert module.CONVERGENCE_TARGET is vocab.CONVERGENCE_TARGET
    assert module.SPEND_LEDGER_FILENAME is vocab.SPEND_LEDGER_FILENAME

    source = SCRIPT.read_text(encoding="utf-8")
    for literal in ("grind_cycles\": 22", "post_verification_cycles\": 8"):
        assert literal not in source, (
            f"measure-run.py spells {literal!r} itself instead of reading "
            f"vocab.THUNDER_VIPER_BASELINE"
        )


@pytest.mark.skipif(
    not THUNDER_VIPER.exists(),
    reason=(
        f"thunder-viper archive not present in this checkout: {THUNDER_VIPER} "
        "(foundry-archive/ is git-ignored)"
    ),
)
def test_thunder_viper_prints_the_baseline_beside_the_current_run() -> None:
    """OT-030, driven against the real baseline archive, READ-ONLY.

    The archive is opened in place and never copied-then-migrated the way
    grand-vulture is, because NFR-001 and OT-030 are only meaningful while it
    stays byte-identical: it is the number every later run is measured against.
    A tree hash before and after is the proof, and migrate-archive.py is never
    pointed at it.

    Its 162 records carry no tier, its counter stayed at 0, and it has none of
    the three post-4.7.3 artifacts — so this is simultaneously the OT-030
    assertion and the honest-nulls assertion, on real data rather than a
    fixture built to have the shape.
    """
    before = _tree_digest(THUNDER_VIPER)

    exit_code, stdout, stderr = _invoke_measure_run(str(THUNDER_VIPER))
    assert stdout.strip(), f"no payload; stderr was: {stderr}"
    payload = json.loads(stdout)

    comparison = payload["baseline_comparison"]
    assert comparison["baseline"] == {
        "run": "thunder-viper", "grind_cycles": 22, "post_verification_cycles": 8,
    }
    assert comparison["target"] == {
        "grind_cycles": 12, "post_verification_cycles": 3,
    }
    # ...beside the current run's own numbers, read from the archive rather
    # than from the constant. They disagree with the baseline BECAUSE this
    # archive cannot supply them: the counter never incremented, so 1 is what
    # it honestly says, and the 22 comes from the constant that exists for
    # exactly this reason.
    assert comparison["current"]["run"] == "thunder-viper"
    assert comparison["current"]["grind_cycles"] == payload["cycles"]
    assert comparison["current"]["post_verification_cycles"] is None

    # FR-051 on 162 real unclassified records.
    assert payload["defects_by_tier"] == {"LIVE": 0, "LATENT": 0, "unknown": 162}
    assert sum(payload["per_stream_defects"].values()) == 162

    # The three artifacts 4.7.3 never wrote: null, not zero, not a token.
    for column in ("spend", "inspect_modes", "escalation"):
        assert payload[column] is None, (column, payload[column])
    assert payload["failure_tokens"] == [], payload["failure_tokens"]

    # And the baseline archive is byte-identical afterwards.
    assert _tree_digest(THUNDER_VIPER) == before, (
        "measure-run wrote into the baseline archive; NFR-001 and OT-030 are "
        "only meaningful while it stays exactly as found"
    )


@pytest.mark.skipif(
    not THUNDER_VIPER.exists(),
    reason=(
        f"thunder-viper archive not present in this checkout: {THUNDER_VIPER} "
        "(foundry-archive/ is git-ignored)"
    ),
)
def test_the_baseline_archive_never_reports_that_it_met_the_target() -> None:
    """D-022 / AC-039 / OT-030, on the archive that exposed it, READ-ONLY.

    This command printed `meets_target: true` for thunder-viper. Its baseline
    column read 22 from the constant while the archive-derived column for the
    SAME archive said 1 — its counter was written once as 0 and never
    incremented, and it wrote no stream-rollup.json, so both of the sources
    this tool consulted were blind. The acceptance instrument therefore
    certified the very run whose 22 cycles are the entire reason
    CONVERGENCE_TARGET exists.

    The fix is a third source rather than a special case: the defect ledger
    stamps the cycle each filing was made in, so its highest is a floor on the
    cycles a run executed, and on this archive that reproduces the published 22
    exactly. The recorded constant stays as a floor beneath it."""
    # READ from vocab so this assertion cannot drift from the constant the
    # command itself compares against.
    from foundry_mcp.schemas import vocab

    recorded = vocab.THUNDER_VIPER_BASELINE["grind_cycles"]
    before = _tree_digest(THUNDER_VIPER)
    payload = json.loads(_invoke_measure_run(str(THUNDER_VIPER))[1])

    assert payload["cycles"] == recorded, (
        "the 22 cycles are derivable from the archive's own defect ledger"
    )
    comparison = payload["baseline_comparison"]
    assert comparison["current"]["grind_cycles"] == recorded
    assert comparison["meets_target"]["grind_cycles"] is False
    assert payload["gate_verdicts"]["cycles"] == "FAIL"
    # Still not a token: a pre-release archive is healthy, and the defect
    # ledger proving cycles is not evidence that the COUNTER is stale — only
    # the roll-up, which is keyed by that counter, is (D-034's calibration).
    assert payload["failure_tokens"] == [], payload["failure_tokens"]
    assert _tree_digest(THUNDER_VIPER) == before, "the archive must be untouched"


def test_an_archive_with_no_cycle_evidence_reports_missing_not_pass(
    tmp_path: Path,
) -> None:
    """D-022's other half: `meets_target` is never True on a number nobody
    measured.

    The count used to be forced to an int, so an archive no ledger could speak
    for arrived at the gate as 1 and PASSED the convergence gate. "Did not
    meet" and "cannot say" are different answers."""
    run_dir = tmp_path / "no_ledgers"
    run_dir.mkdir()
    (run_dir / "handoffs.jsonl").write_text(
        '{"timestamp": "2026-08-01T00:00:00+00:00"}\n'
        '{"timestamp": "2026-08-01T01:00:00+00:00"}\n',
        encoding="utf-8",
    )
    payload = json.loads(_invoke_measure_run(str(run_dir))[1])

    assert payload["cycles"] is None
    assert payload["gate_verdicts"]["cycles"] == "MISSING"
    assert payload["baseline_comparison"]["meets_target"]["grind_cycles"] is None
    # A state.json that cannot supply a counter is still malformed and named.
    assert "PHASE9_CYCLE_COUNT_INVALID" in payload["failure_tokens"]


def test_measure_run_and_the_report_derive_the_cycle_count_identically(
    make_run_dir: Callable[..., Path],
) -> None:
    """D-036 — two derivations of one fact that could disagree.

    `_extract_per_run` published `final_index + 1` while
    `foundry_report._baseline_comparison_section` published the raw
    `state.json["cycle"]`. They differ by exactly one, which straddles
    `CONVERGENCE_TARGET["grind_cycles"]`: a run at index 12 met the effort's
    own target on one surface and missed it on the other. Both now call
    `foundry_state.derive_cycle_count`, and this drives BOTH over one archive
    and asserts the two answers are the same number."""
    from foundry_mcp.tools.foundry_report import generate_report
    from foundry_mcp.tools.foundry_state import derive_cycle_count

    run_dir = make_run_dir(rollup=_rollup_doc({"3": {"prove": _entry(80, 80, 1)}}))
    payload = json.loads(_invoke_measure_run(str(run_dir))[1])

    result = generate_report(run_dir.parent, run_dir)
    assert result["ok"] is True, result
    report = json.loads((run_dir / "report.json").read_text(encoding="utf-8"))

    shared = derive_cycle_count(run_dir)["count"]
    assert payload["cycles"] == shared
    assert report["baseline_comparison"]["current"]["grind_cycles"] == shared


def _tree_digest(root: Path) -> str:
    """Path-sensitive content hash of a tree. Mirrors test_migrate_archive."""
    import hashlib

    digest = hashlib.sha256()
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        digest.update(str(path.relative_to(root)).encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


# ---------------------------------------------------------------------------
# OT-030's committed stand-in.
#
# WHY A FIXTURE EXISTS BESIDE THE REAL-ARCHIVE TEST ABOVE
# -------------------------------------------------------
# ``foundry-archive/`` is git-ignored, and Foundry-Accept-Casting re-executes
# every committed ``# evidence-cmd:`` inside a ``git worktree add --detach``
# checkout of the casting's commit — which contains TRACKED FILES ONLY. So a
# command that reads foundry-archive/thunder-viper produces one output here and
# a different one at the gate, and no evidence log can bind OT-030 to the real
# archive without failing byte-comparison.
#
# This fixture is thunder-viper's STRUCTURE, not its data: a run that reached
# F6 with the cycle counter still at 0, defect records filed by the same five
# sources with NOT ONE carrying a `tier`, no stream-rollup.json, no
# escalation.json, no spend.jsonl and no `inspect_modes`. That is exactly the
# set of gaps the 4.7.3 cache left behind, so the behaviour OT-030 names is
# committed and re-executable. The real archive keeps its own test above.
# ---------------------------------------------------------------------------

THUNDER_VIPER_SHAPE = FIXTURES / "thunder_viper_shape"


def test_the_fixture_really_has_thunder_vipers_structural_gaps() -> None:
    """The precondition, or the test below proves nothing about that shape."""
    assert json.loads((THUNDER_VIPER_SHAPE / "state.json").read_text())["cycle"] == 0
    state = json.loads((THUNDER_VIPER_SHAPE / "state.json").read_text())
    assert "inspect_modes" not in state
    records = json.loads((THUNDER_VIPER_SHAPE / "defects.json").read_text())["defects"]
    assert records and not any("tier" in r for r in records)
    for absent in ("stream-rollup.json", "escalation.json", "spend.jsonl"):
        assert not (THUNDER_VIPER_SHAPE / absent).exists(), absent


def test_a_thunder_viper_shaped_archive_prints_the_baseline_and_the_target() -> None:
    """OT-030 / AC-039 / NFR-001 / FR-039, on committed re-executable data.

    Three things at once, because they are one behaviour: the baseline's 22
    and 8 print beside the current run's own numbers, the target's 12 and 3
    print with them, and every column the archive cannot supply is null rather
    than a fabricated zero. The command exits 0 — a pre-release archive is not
    a failure.
    """
    exit_code, stdout, stderr = _invoke_measure_run(str(THUNDER_VIPER_SHAPE))
    assert exit_code == 0, (stdout, stderr)
    payload = json.loads(stdout)

    comparison = payload["baseline_comparison"]
    assert comparison["baseline"]["grind_cycles"] == 22
    assert comparison["baseline"]["post_verification_cycles"] == 8
    assert comparison["target"]["grind_cycles"] == 12
    assert comparison["target"]["post_verification_cycles"] == 3
    assert comparison["current"]["grind_cycles"] == payload["cycles"]
    assert comparison["current"]["post_verification_cycles"] is None

    assert payload["defects_by_tier"] == {"LIVE": 0, "LATENT": 0, "unknown": 6}
    for column in ("spend", "inspect_modes", "escalation"):
        assert payload[column] is None, column
    assert payload["failure_tokens"] == []
