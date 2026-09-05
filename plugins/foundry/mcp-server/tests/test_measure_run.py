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
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable

import pytest

# AC-024 / GI-014 — the tier ROSTER, never a hand-typed list. `_tiers` below
# builds every expected `defects_by_tier` shape from it, for the same reason
# `measure-run.py` seeds its counts from it: the day HARDENING joined the
# vocabulary, a test carrying its own three names would have gone red without
# telling anyone WHICH list was wrong.
from foundry_mcp.schemas.vocab import DEFECT_TIER_OR_UNKNOWN

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
    PHASE9_WALL_CLOCK_UNAVAILABLE, reports a NULL wall clock, and exits 0.

    D-087 / D-086 re-point the two halves of this test.

    The wall clock is `None`, never 0.0. `foundry_report._wall_clock_minutes`
    has always published null here and its docstring forbids the other
    spelling by name — "a run that took no measurable time and a run nobody
    measured are different facts, and NFR-001's comparison is unreadable if
    they print the same" — and this command publishes the OTHER column of that
    same comparison, so it cannot fabricate what the report refuses to.

    The status is 0 because "nobody measured the wall clock" is not an
    unreadable artifact. D-105 named this exact asymmetry — the tool "failed
    loud on 'could not measure the wall clock' and stayed silent on 'the
    convergence gate FAILED'" — and closed it by making the quiet half loud;
    NFR-001 ("numbers are the target, not a gate") requires the other
    direction. The TOKEN still fires, which is what an operator and a cohort
    matrix actually read.
    """
    run_dir = make_run_dir(omit_handoffs=True)
    exit_code, stdout, stderr = _invoke_measure_run(str(run_dir))
    combined = stdout + stderr
    assert "PHASE9_WALL_CLOCK_UNAVAILABLE" in combined, combined
    payload = json.loads(stdout)
    assert payload["wall_clock_seconds"] is None, (
        "0.0 would be a fabricated measurement; the report module prints null "
        "for this same run (D-087)"
    )
    assert exit_code == 0, (
        "an unmeasured wall clock is not an unreadable artifact (D-086)"
    )


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

    D-086 re-points the STATUS half only. ``--strict`` still decides whether
    the token FIRES — that is the whole of what the flag is for, and the
    cohort-study workflow reads the token — but a missing measurement is not
    an unreadable artifact, so it does not reach the exit code. NFR-001:
    "Numbers are the target, not a gate."
    """
    # Strict mode + missing context file -> failure token.
    run_dir_strict = make_run_dir(context_pct=None)
    exit_code, stdout, stderr = _invoke_measure_run(
        "--strict", str(run_dir_strict)
    )
    combined = stdout + stderr
    assert "PHASE9_CONTEXT_FILE_MISSING" in combined, combined
    assert "PHASE9_CONTEXT_FILE_MISSING" in json.loads(stdout)["failure_tokens"]
    assert exit_code == 0, (
        "--strict names the gap in the payload; it does not gate the status"
    )

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
    """Test 13 — 4 RUN-01 advisory verdicts per cohort:
       cycles ≤ CONVERGENCE_TARGET["grind_cycles"] -> PASS; yield 5-50% ->
       PASS; context < 50% -> PASS; wall-clock regression < 50% -> PASS;
       out-of-band -> FAIL.

    Exercises the verdict-evaluation function directly via the
    ``--evaluate-gates`` helper (Plan 09-02 territory). Table-driven across
    boundary cases.

    D-086 RE-POINTS BOTH HALVES OF THIS TEST.

    The cycles band is `vocab.CONVERGENCE_TARGET["grind_cycles"]` and no
    longer a second constant of 8 kept in this script. Two thresholds for one
    number lived in one payload: a 10-cycle run had
    ``baseline_comparison.meets_target.grind_cycles`` true (10 <= 12) and
    ``gate_verdicts.cycles`` FAIL (10 > 8), side by side. The boundary cases
    below are therefore READ from the constant rather than written as
    literals, which is what stops this table from becoming the third copy.

    And the status is 0 on every row, FAIL included. NFR-001 verbatim:
    "Numbers are the target, not a gate." An operator may gate on the verdict
    this command prints; the command does not gate on it and exit nonzero, and
    that is the whole of D-086 — the failing verdict was the one driving the
    exit status, on runs that MET the effort's stated target.
    """
    from foundry_mcp.schemas.vocab import CONVERGENCE_TARGET

    at = CONVERGENCE_TARGET["grind_cycles"]
    over = at + 1
    cases = [
        # (cycles, yield_pct, context_pct, regression_pct, expected_verdict)
        (at, 25.0, 42.0, 30.0, "PASS"),    # all in-band
        (over, 25.0, 42.0, 30.0, "FAIL"),  # cycles over
        (at, 4.9, 42.0, 30.0, "FAIL"),     # yield under
        (at, 50.1, 42.0, 30.0, "FAIL"),    # yield over
        (at, 25.0, 49.9, 30.0, "PASS"),    # context just under cap
        (at, 25.0, 50.0, 30.0, "FAIL"),    # context at cap (cap is < 50)
        (at, 25.0, 42.0, 49.9, "PASS"),    # regression just under cap
        (at, 25.0, 42.0, 50.0, "FAIL"),    # regression at cap
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
        assert exit_code == 0, (
            f"case={cycles, yld, ctx, reg}: verdict {expected} reached the "
            f"process status, which NFR-001 forbids{stderr}"
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
    lowercase (tools/foundry.py, orchestration/fix_gate.py). measure-run
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
    one-record ledger puts 100% of the yield on a single stream, so the yield
    verdict FAILs; that says nothing about the legacy key.

    D-086 re-points the status. The verdict still FAILs and is still printed;
    the process still exits 0, because NFR-001 makes these numbers a report
    and not a gate. The sharper assertion — no token — is unchanged and is
    what the test is actually for.
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
    # The yield band fails on a single-record ledger and every other verdict
    # is clean — reported, and reaching nothing (D-086).
    assert payload["gate_verdicts"]["defect_yield_per_stream"] == "FAIL"
    assert payload["gate_verdicts"]["cycles"] == "PASS"
    assert exit_code == 0, (stdout, stderr)


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


# ---------------------------------------------------------------------------
# D-182 — the C-6 cycle-level facts, and the ONE rule that recognises them.
#
# `stream-rollup.json`'s cycle bucket has two writers and therefore two kinds
# of key: the stream tranches `_record_stream_rollup` accumulates, and the
# cycle-level facts `_record_cycle_rollup` writes beside them. No fixture in
# this file carried the second kind, which is why the roll-up reader could
# treat every key as a stream id for a whole release without a test noticing.
# ---------------------------------------------------------------------------

#: Every non-stream key `_record_cycle_rollup` writes into a cycle bucket, in
#: the shape the live archive holds them (two strings, two mappings, plus the
#: nested `temper_entry` sub-bucket the F5 entry writes under). Spelled from
#: `foundry-archive/daring-orca/stream-rollup.json` cycle 10 rather than
#: invented, so the fixture is the document the server actually produces.
_CYCLE_LEVEL_FACTS: dict[str, Any] = {
    "inspect_mode": "FULL",
    "inspect_rule": "verifier_touched",
    "stream_scope": {
        "prove": {"scope": "full", "detail": "every item in scope"},
        "trace": {"scope": "delta", "detail": "3 touched files"},
    },
    "evidence_sweep": {
        "scope": "full",
        "corpus_size": 60,
        "logs_reexecuted": ["evidence/casting-5-sweep-engine.log"],
        "mismatches": [],
        "elapsed_seconds": 41.2,
        "pool_size": 8,
        "swept_at": "2026-09-03T22:10:00+00:00",
    },
    "temper_entry": {"inspect_mode": "FULL", "inspect_rule": "first_of_phase"},
}


def test_cycle_level_facts_are_not_read_as_streams(
    make_run_dir: Callable[..., Path],
) -> None:
    """D-182 — the instrument must not call the documented shape a fault.

    The spec's Data Model widened this document: "stream-rollup.json gains the
    inspect mode, the rule that fired, and per-stream scope for each cycle,
    plus the evidence sweep result". The roll-up reader was never taught those
    keys, so it read `inspect_mode` (a string) as a malformed stream entry and
    `stream_scope` (a mapping) as a stream whose name no roster knows.

    Driven at 056f51a as a real process, this run's own archive produced
    PHASE9_SCHEMA_INVALID x4 and PHASE9_UNKNOWN_STREAM x2 and exit 1, while
    thunder-viper — the archive that PREDATES these keys — exited 0. NFR-001
    says "Numbers are the target, not a gate"; the numbers were right and the
    verdict on the artifact was not.
    """
    run_dir = make_run_dir(
        rollup=_rollup_doc(
            {"3": {"prove": _entry(80, 80, 1), **_CYCLE_LEVEL_FACTS}}
        )
    )
    exit_code, stdout, stderr = _invoke_measure_run(str(run_dir))
    assert exit_code == 0, (stdout, stderr)
    payload = json.loads(stdout)
    assert payload["failure_tokens"] == [], (
        "a cycle bucket carrying its own C-6 facts is the documented shape, "
        "not a broken artifact"
    )
    # The coverage the bucket really does carry still lands, and the five
    # cycle-level keys land nowhere: they are facts about the cycle, not
    # streams reporting what they checked.
    assert payload["per_cycle_coverage"] == {
        "3": {"PROVE": {"items_checked": 80, "items_total": 80, "findings": 1}}
    }


def test_a_roster_key_whose_value_is_not_a_tranche_is_still_a_fault(
    make_run_dir: Callable[..., Path],
) -> None:
    """D-182 — filtering by value alone would trade one silence for another.

    `prove` holding a string is a BROKEN stream record, not a cycle-level
    fact, and the branch that named it PHASE9_SCHEMA_INVALID before this fix
    must keep naming it. What tells the two cases apart is the KEY: the roster
    knows `prove` and does not know `inspect_mode`, so the key is resolved
    before the value test decides.
    """
    run_dir = make_run_dir(rollup=_rollup_doc({"3": {"prove": "FULL"}}))
    exit_code, stdout, _ = _invoke_measure_run(str(run_dir))
    assert exit_code != 0
    assert "PHASE9_SCHEMA_INVALID" in json.loads(stdout)["failure_tokens"]


def test_both_rollup_readers_classify_one_bucket_identically(
    tmp_path: Path,
) -> None:
    """D-182 — the two walkers of one bucket, fed the same bucket.

    `orchestration.spend._stream_dispatch_cycles` and `measure-run.py`
    `_read_stream_rollup` both answer "which keys here are streams", and the
    whole filing is that they answered differently. The rule now has ONE
    definition in `foundry_state.is_stream_record`; this drives BOTH readers
    over a single bucket and asserts they accept and reject exactly the same
    keys, so the day a third C-6 field is added, whichever reader is not
    taught it fails here rather than in a live run.
    """
    from foundry_mcp.tools.orchestration import spend

    bucket = {
        "prove": _entry(80, 80, 1),
        "trace": _entry(12, 12, 0),
        **_CYCLE_LEVEL_FACTS,
    }
    (tmp_path / "stream-rollup.json").write_text(
        json.dumps(_rollup_doc({"3": bucket})), encoding="utf-8"
    )

    orchestrator_streams = set(spend._stream_dispatch_cycles(tmp_path))

    module = _load_measure_run_module()
    coverage, _highest, tokens = module._read_stream_rollup(tmp_path)
    # The roll-up reader re-keys onto the canonical UPPERCASE spelling, so the
    # comparison is made on the wire ids both readers were handed.
    measure_streams = {
        wire
        for wire in bucket
        if module.canonical_stream_id(wire) in coverage.get("3", {})
    }

    assert orchestrator_streams == {"prove", "trace"}
    assert measure_streams == orchestrator_streams, (
        "the two readers of one cycle bucket disagree about which keys are "
        "streams — D-182 is back"
    )
    assert set(_CYCLE_LEVEL_FACTS) & orchestrator_streams == set()
    assert tokens == [], (
        "no cycle-level fact may produce a failure token in either reader"
    )


def test_the_stream_record_rule_has_one_definition() -> None:
    """D-182 — the rule is READ by the readers I own, never re-typed.

    Behaviour alone would let the two copies drift back apart and stay green
    until they disagreed on a key neither test covered, which is exactly how
    this got here: two modules spelled `"records" in entry` inline and a third
    never learned it. Both readers in this package's own tree must CALL the
    shared predicate.

    THE SECOND OWNER MOVED, AND THE PIN FOLLOWED THE SYMBOL (GI-026). The
    dispatch-summary walk of the cycle bucket used to sit inside
    `foundry_report._read_dispatch_summary`; the GI-024 consolidation moved it
    into `foundry_state.unreported_dispatch_inputs`, which assembles the roster
    and the cycle map off ONE walk for both surfaces. A pin left on the old
    host reads as "the rule was re-typed" when what actually happened is that
    the rule was consolidated further — the exact false alarm a pin aimed at a
    module rather than at the symbol produces. It is aimed at the walker now,
    wherever the walker lives.
    """
    import inspect

    from foundry_mcp.tools import foundry_state

    assert callable(foundry_state.is_stream_record)
    assert foundry_state.is_stream_record({"records": []}) is True
    assert foundry_state.is_stream_record({"scope": "full"}) is False
    assert foundry_state.is_stream_record("FULL") is False
    assert foundry_state.is_stream_record(None) is False

    for owner in (
        inspect.getsource(_load_measure_run_module()._read_stream_rollup),
        inspect.getsource(foundry_state.unreported_dispatch_inputs),
        inspect.getsource(foundry_state.stream_rollup_rows),
    ):
        assert "is_stream_record(" in owner, (
            "a walker of the cycle bucket re-typed the stream-record rule "
            "instead of reading the one definition (D-182)"
        )


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
    assert payload["gate_verdicts"]["f2_context_pct"] == "PASS"
    assert payload["gate_verdicts"]["wall_clock_regression_pct"] == "PASS"
    # THE CLAIM IS ABOUT THE TWO GATES THE OPERATOR CAN SUPPLY, and it was
    # written as "no MISSING anywhere" because at the time those were the only
    # two that could be. FR-025 and FR-026 added two more acceptance figures,
    # and this fixture's archive records neither an `inspect_modes` list nor a
    # `fallout_of` field — so both are honestly MISSING here, and a blanket
    # sweep would read that honesty as the regression it is the opposite of.
    # The four gates a measurement exists for are named.
    for gate in ("cycles", "defect_yield_per_stream", "f2_context_pct",
                 "wall_clock_regression_pct"):
        assert payload["gate_verdicts"][gate] != "MISSING", (
            gate, payload["gate_verdicts"]
        )


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
    """The gate half of D-063, re-pointed by D-086.

    A run at index N executed N+1 cycles, and the verdict must be evaluated
    against the COUNT. The threshold is `vocab.CONVERGENCE_TARGET`, which is
    now the only one: `MAX_CYCLES_FOR_CONVERGENCE` was a SECOND constant for
    the same number, eight against the target's twelve, and both were
    evaluated in one payload — a 10-cycle run read `meets_target: true` and
    `gate_verdicts.cycles: FAIL` in the same document, with the FAIL driving
    the exit status.

    So the boundary is read from the constant, the two spellings of the one
    comparison are asserted to AGREE, and nothing here reaches the status.
    """
    from foundry_mcp.schemas.vocab import CONVERGENCE_TARGET

    module = _load_measure_run_module()
    assert not hasattr(module, "MAX_CYCLES_FOR_CONVERGENCE"), (
        "the second cycle threshold is back; there is one, and it is "
        "vocab.CONVERGENCE_TARGET (D-086)"
    )
    at_count = CONVERGENCE_TARGET["grind_cycles"]

    over = make_run_dir(
        rollup=_rollup_doc({str(at_count): {"prove": _entry(10, 10, 0)}}),
        cohort_id="no_TYPE_01",
    )
    payload = json.loads(_invoke_measure_run(str(over))[1])
    assert payload["cycles"] == at_count + 1, "index N is the (N+1)th cycle"
    assert payload["gate_verdicts"]["cycles"] == "FAIL"
    # The verdict and `meets_target` are ONE comparison, and this is the pin
    # that they cannot part again.
    assert payload["baseline_comparison"]["meets_target"]["grind_cycles"] is False

    at_threshold = make_run_dir(
        rollup=_rollup_doc({str(at_count - 1): {"prove": _entry(10, 10, 0)}}),
        cohort_id="no_TYPE_02",
    )
    payload = json.loads(_invoke_measure_run(str(at_threshold))[1])
    assert payload["cycles"] == at_count
    assert payload["gate_verdicts"]["cycles"] == "PASS"
    assert payload["baseline_comparison"]["meets_target"]["grind_cycles"] is True

    # The operator-supplied path already spoke in COUNTS; both paths read the
    # same constant.
    exit_code, stdout, _ = _invoke_measure_run(
        "--evaluate-gates", "--cycles", str(at_count + 1),
        "--yield-pct", "25.0", "--context-pct", "42.0", "--regression-pct", "30.0",
    )
    assert json.loads(stdout)["gate_verdicts"]["cycles"] == "FAIL"
    assert exit_code == 0, "NFR-001: the numbers are the target, not a gate"


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

    # The verdict half. Those same two numbers are BOTH out of band: 18 cycles
    # against a convergence target of 12, and a defect yield concentrated past
    # the 50% ceiling. That is the whole premise of NFR-001, which names
    # grand-vulture as the baseline this effort must improve ON, so the
    # instrument has to say so out loud — in the payload, which is where it
    # says everything else.
    assert payload["gate_verdicts"]["cycles"] == "FAIL"
    assert payload["gate_verdicts"]["defect_yield_per_stream"] == "FAIL"
    # D-086 re-points the STATUS. Saying it out loud is not the same as
    # refusing: NFR-001 is "numbers are the target, not a gate", and a healthy,
    # fully readable archive that merely missed the target must not exit
    # nonzero. Nothing about grand-vulture is malformed — asserted above —
    # so there is nothing left to make the status anything but 0.
    assert exit_code == 0, (
        "a readable archive that missed the target is not a failed read: "
        f"{payload['gate_verdicts']} / {stderr}"
    )

    # And the real archive was never touched.
    assert json.loads((GRAND_VULTURE / "state.json").read_text())["cycle"] == 0


# ---------------------------------------------------------------------------
# D-086 — NO VERDICT REACHES THE EXIT STATUS.
#
# D-105 found a real ASYMMETRY here: a purely informational token
# (PHASE9_WALL_CLOCK_UNAVAILABLE, pinned by Test 4) exited 1 with every gate
# PASSing, while a blown convergence gate exited 0. It closed the asymmetry by
# making the quiet half loud — every verdict routed to the status.
#
# NFR-001 requires the other direction, verbatim: "Numbers are the target, not
# a gate." Driven on a synthetic 10-cycle run, `meets_target.grind_cycles` was
# true (10 <= the CONVERGENCE_TARGET of 12) while `gate_verdicts.cycles` was
# FAIL (10 > the second, local constant of 8) and the FAILING verdict drove
# exit 1 — an operator was told a converging run had failed by the effort's own
# acceptance instrument.
#
# So the asymmetry stays closed and the calibration flips: the status answers
# ONLY "could this command read what it was pointed at". The seven
# UNREADABLE_ARTIFACT_TOKENS exit 1; verdicts and the two could-not-measure
# tokens are reported in the payload and reach nothing.
# ---------------------------------------------------------------------------


def test_a_blown_gate_alone_is_not_a_nonzero_status(
    make_run_dir: Callable[..., Path],
) -> None:
    """The per-run path, with a verdict FAIL as the ONLY thing wrong.

    ``--context-pct 50.0`` is the cleanest isolation available: it blows one
    band (the cap is a strict ``<``) while leaving failure_tokens empty, so the
    status can come from nowhere else — which makes it the sharpest possible
    pin that a verdict alone moves nothing. 49.9 is the control, one tenth of a
    point away. Together they still pin the band boundary itself, which D-105
    verified as consistent and must not move.
    """
    run_dir = make_run_dir()

    exit_code, stdout, stderr = _invoke_measure_run(
        str(run_dir), "--context-pct", "50.0"
    )
    payload = json.loads(stdout)
    assert payload["failure_tokens"] == [], (
        "isolation broken — the status could come from a token, not the verdict"
    )
    assert payload["gate_verdicts"]["f2_context_pct"] == "FAIL"
    assert exit_code == 0, (
        f"a verdict reached the exit status, which NFR-001 forbids: {stderr}"
    )

    exit_code, stdout, stderr = _invoke_measure_run(
        str(run_dir), "--context-pct", "49.9"
    )
    payload = json.loads(stdout)
    assert payload["gate_verdicts"]["f2_context_pct"] == "PASS"
    assert exit_code == 0, (stdout, stderr)


def test_an_unreadable_artifact_is_still_a_nonzero_status(
    make_run_dir: Callable[..., Path],
) -> None:
    """The half D-086 keeps: a BROKEN input is a fault, not a measurement.

    "Gates on nothing" is about the NUMBERS. An archive whose defects.json will
    not parse produced no numbers at all, and reporting success for it would
    make every downstream reading of the payload a guess. This is the isolation
    from the other side of `test_a_blown_gate_alone_is_not_a_nonzero_status`:
    every verdict is clean or MISSING and the status is 1 anyway, so the two
    tests together show exactly which input moves it.
    """
    run_dir = make_run_dir()
    (run_dir / "defects.json").write_text("{not json", encoding="utf-8")

    exit_code, stdout, stderr = _invoke_measure_run(str(run_dir))
    payload = json.loads(stdout)
    assert "PHASE9_DEFECTS_FILE_MALFORMED" in payload["failure_tokens"]
    assert "FAIL" not in payload["gate_verdicts"].values(), (
        "isolation broken — a verdict could be supplying the status"
    )
    assert exit_code == 1, (stdout, stderr)


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


def test_matrix_gate_fail_is_rendered_and_does_not_move_the_exit_status(
    tmp_path: Path,
) -> None:
    """The --matrix path, on the exact row D-105 describes, re-pointed by D-086.

    A row rendering ``gate_verdict_overall=FAIL`` beside an EMPTY
    ``failure_tokens_csv`` was the visible shape of D-105's bug. What that row
    proves under NFR-001 is the opposite: the verdict must be RENDERED — a
    cohort matrix is unreadable if the column is blank — and must not move the
    status, because nothing about this arm is unreadable. One cohort is given a
    single-record ledger, which puts 100% of the yield on one stream and blows
    the band with no token attached, so the isolation is exact.
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
    assert exit_code == 0, (
        f"a FAIL row in the matrix moved the exit status: {stderr}"
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


def _tiers(**counts: int) -> dict[str, int]:
    """The expected ``defects_by_tier`` shape, DERIVED from the vocabulary.

    AC-024 / GI-014 — HARDENING joined ``vocab.DEFECT_TIERS`` and every
    assertion in this module that hand-listed three tier names went red at once.
    That is the right failure and the wrong place for it: the reader seeds its
    counts from ``DEFECT_TIER_OR_UNKNOWN``, so a test that re-types the roster is
    asserting against a second hand list — the exact drift the vocabulary module
    exists to prevent, on the surface that is supposed to be checking for it.

    So the SHAPE comes from the roster and only the NUMBERS come from the test.
    The day a fifth tier is added, every assertion below still describes what it
    means to describe, and the one that genuinely cares — the positive HARDENING
    count immediately after this — is the one that has to be looked at.

    A named tier that is not a roster member is a typo in the test, and it
    raises here rather than passing an assertion that compares two wrong dicts.
    """
    expected = dict.fromkeys(sorted(DEFECT_TIER_OR_UNKNOWN), 0)
    unknown_names = set(counts) - set(expected)
    assert not unknown_names, (
        f"{sorted(unknown_names)} is not in vocab.DEFECT_TIER_OR_UNKNOWN "
        f"({sorted(DEFECT_TIER_OR_UNKNOWN)})"
    )
    expected.update(counts)
    return expected


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
    assert payload["defects_by_tier"] == _tiers(LIVE=2, LATENT=1, unknown=3)
    # The two ledger columns read the same file and must agree on the total.
    assert sum(payload["defects_by_tier"].values()) == sum(
        payload["per_stream_defects"].values()
    )


def test_an_untiered_record_reads_as_unknown_and_never_as_latent(
    make_run_dir: Callable[..., Path],
) -> None:
    """FR-051, stated as the negative that matters.

    LATENT blocks no gate: FR-006 passes INSPECT-clean, ASSAY, TEMPER, NYQUIST
    and DONE alike on a LATENT-only backlog. A reader that resolved a missing
    tier to LATENT would report every pre-change archive as fully triaged and
    let all five pass on records no stream ever looked at.
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

    D-090 — `agents` COUNTS AGENTS AND `records` COUNTS ROWS. They were one
    key here, meaning ROWS, while `orchestration.spend._spend_summary`
    published the same key over the same ledger meaning DISTINCT agents — one
    field name, two meanings, across three surfaces of one run, parting the
    moment any agent reported twice. The comment below this assertion already
    said "two dispatches but one agent" beneath an assertion of 4; the
    assertion now says what the comment always meant.
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
        "tokens": 1500, "duration_ms": 90_000, "minutes": 1.5,
        "records": 2, "agents": None, "unreported": 0,
    }
    assert spend["by_phase"]["F2"]["tokens"] == 2000
    assert spend["by_cycle"]["1"] == {
        "tokens": 2250, "duration_ms": 135_000, "minutes": 2.25,
        "records": 2, "agents": None, "unreported": 0,
    }
    assert spend["total"]["tokens"] == 3750
    assert spend["total"]["minutes"] == 3.75
    # Two records from teammate-1 are two dispatches but one agent.
    assert spend["total"]["records"] == 4
    assert "distinct_agents" not in spend["total"], (
        "`distinct_agents` existed only because `agents` had been taken by the "
        "row count; two spellings of one number in one bucket is the same "
        "defect one shape smaller (D-090)"
    )

    # GI-024 — `agents` IS THE ROLL-UP'S NUMBER AND ONLY THE ROLL-UP'S, so it
    # is None on a fixture whose state.json carries no `spend` roll-up. This
    # assertion used to read 3, counted off the ledger's own distinct names —
    # which is a DIFFERENT derivation of the same field name, and the pair
    # parting is D-090 one layer up. `foundry_state.spend_rollup` keeps the
    # ledger's count as a CHECK (it appears in `disagreements` when the two
    # differ) and publishes the roll-up's, or None when there is none: "nobody
    # recorded how many agents ran" is not "no agents ran".
    assert spend["total"]["agents"] is None, (
        "this fixture writes no state.json spend roll-up, and the roll-up is "
        "the only source of `agents`"
    )
    assert spend["disagreements"] == [], "nothing to disagree with"
    # `unreported` is DERIVED from the dispatch record on every run (D-163),
    # so 0 here is a measurement and not a seed: this fixture has no spawns.log
    # and therefore no dispatch that could have gone unreported.
    assert spend["total"]["unreported"] == 0


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
    # GI-024 / D-119 — ONE ROW PER DECISION, not one per cycle. This asserted a
    # single collapsed mapping because this file used to build one, keeping the
    # LAST entry per cycle; `foundry_report._archive_metrics` names that copy by
    # name as the surface that could differ from the census and says which
    # direction is right. The census is `foundry_state.inspect_mode_rows` now,
    # and its `per_cycle` is a LIST — so a cycle whose F5 INSPECT is followed by
    # an F2 one keeps both, which is what a census means.
    assert modes["per_cycle"]["3"] == [
        {"cycle": 3, "phase": "F2", "mode": "FULL", "rule": "verifier_touched",
         "decided_by": None, "required_streams": None},
    ]
    assert modes["by_mode"] == {"DELTA": 2, "FULL": 3}
    assert modes["count"] == 5, "five decisions"
    assert modes["cycle_count"] == 5, "across five cycles"
    assert list(modes["per_cycle"]) == ["1", "2", "3", "4", "5"], "numeric order"
    # `post_verification_cycles` IS NOT A COLUMN OF THIS TABLE ANY MORE, and it
    # is not lost: it was this file's own third derivation of a number
    # `_archive_metrics` already derives, and it is published one key over in
    # the comparison where the thunder-viper baseline's 8 sits beside it. That
    # is the only place the number means anything, and it is where it is read.
    assert "post_verification_cycles" not in modes, (
        "removing the second derivation is the GI-024 fix; moving it would be "
        "the same defect in a new column"
    )
    assert payload["baseline_comparison"]["current"][
        "post_verification_cycles"
    ] == 2
    assert "entries" not in modes, (
        "the census's raw input is not a measurement; a payload that repeats "
        "its own source makes an operator choose which copy to believe"
    )


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
    # LEGACY_CLASS declares no status, so the resolver DEFAULTED it — it is
    # counted as ESCALATED above (the state it was written in) and reported
    # here as undeclared. This assertion read 0 before D-215: the counter then
    # meant "a status present, a string, and outside the vocabulary", a
    # narrower question than the one the column's name asks, and the entries it
    # most needed to count were the ones a `continue` had already dropped.
    assert escalation["unknown_status"] == 1
    assert sum(escalation["by_status"].values()) == escalation["classes"]


def test_every_class_is_counted_whatever_shape_its_entry_has(
    make_run_dir: Callable[..., Path],
) -> None:
    """D-215 / AC-004 — the census and the gate agree about how many classes.

    THE REPORTED FIXTURE, over the wire. `_read_escalation` opened
    `if not isinstance(entry, dict): continue`, one line above its own
    `unknown_status` counter, so on exactly these four classes the CLI printed
    `{"classes": 4, "by_status": {"CLEARED": 0, "ESCALATED": 1},
    "unknown_status": 0}` — three of four classes gone from the buckets while
    `Foundry-Gate('done')` at the same commit blocked on all four. An operator
    reading the metric saw one escalated class and a run that would not close.

    `classes == sum(by_status.values())` is the property, and it is asserted
    rather than the three numbers alone: a fix that dropped the classes into
    some other bucket would satisfy a per-count assertion and still lose them.
    """
    run_dir = make_run_dir()
    (run_dir / "escalation.json").write_text(
        json.dumps(
            {
                "classes": {
                    "K1": {"status": "ESCALATED"},
                    "K2": "just a string",
                    "K3": ["ESCALATED"],
                    "K4": None,
                }
            }
        ),
        encoding="utf-8",
    )

    exit_code, stdout, stderr = _invoke_measure_run(str(run_dir))
    assert exit_code == 0, stderr
    escalation = json.loads(stdout)["escalation"]

    assert escalation["classes"] == 4, escalation
    assert sum(escalation["by_status"].values()) == 4, escalation
    assert escalation["by_status"] == {"CLEARED": 0, "ESCALATED": 4}, escalation
    # K1 declared a member; the other three declared nothing the vocabulary
    # spells and are reported in the state they were written in.
    assert escalation["unknown_status"] == 3, escalation
    assert escalation["by_exit_reason"] == {"budget": 0, "clean_cycles": 0}


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
    assert payload["defects_by_tier"] == _tiers(unknown=162)
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


@pytest.mark.skipif(
    not THUNDER_VIPER.exists(),
    reason=(
        f"thunder-viper archive not present in this checkout: {THUNDER_VIPER} "
        "(foundry-archive/ is git-ignored)"
    ),
)
def test_migrating_the_baseline_archive_still_derives_its_22_cycles(
    tmp_path: Path,
) -> None:
    """D-084 — migrate-archive.py turned the 22-cycle baseline into 23.

    `_observed_max_cycle` maxed `cycle`, `fixed_in_cycle` and
    `reopened_in_cycle` into one number and wrote it to `state.json["cycle"]`,
    which `foundry_state.derive_cycle_count` then reads as a 0-based INDEX and
    publishes as index + 1. thunder-viper is the archive where those diverge:
    max `cycle` 21, max `fixed_in_cycle` 22. Migrating it moved the counter
    from 0 to 22 and this command then printed 23, permanently — re-running the
    migration is a no-op, so nothing ever healed it. OT-030 requires 22.

    No test migrated this archive before: test_migrate_archive.py uses
    synthetic fixtures and grand-vulture, whose `cycle` and `fixed_in_cycle`
    both max at 17, so the one archive with the shape was the one nobody drove.

    Driven on a COPY, twice, with the real archive's digest asserted unchanged
    on the way out — the baseline is only meaningful while it stays as found.
    """
    before = _tree_digest(THUNDER_VIPER)
    dest = tmp_path / "thunder-viper"
    shutil.copytree(THUNDER_VIPER, dest)

    recorded = json.loads(_invoke_measure_run(str(dest))[1])["cycles"]
    assert recorded == 22, "premise: the pristine archive derives 22 (OT-030)"

    for attempt in (1, 2):
        migrate = subprocess.run(
            [sys.executable, str(MIGRATE_SCRIPT), str(dest)],
            capture_output=True, text=True,
        )
        assert migrate.returncode == 0, migrate.stderr
        payload = json.loads(_invoke_measure_run(str(dest))[1])
        assert payload["cycles"] == 22, (
            f"migration {attempt} moved the baseline off OT-030's 22: "
            f"{payload['cycles']}"
        )

    # The written value is the INDEX, which is what `derive_cycle_count` reads
    # it as. index + 1 == count is the whole of the contract the two share.
    state_cycle = json.loads((dest / "state.json").read_text())["cycle"]
    assert state_cycle == 21, state_cycle
    assert state_cycle + 1 == 22

    assert _tree_digest(THUNDER_VIPER) == before, "the real archive was touched"


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


def test_all_four_nfr_001_columns_are_beside_the_baseline_not_only_cycles(
    make_run_dir: Callable[..., Path],
) -> None:
    """AC-039 verbatim: 'cycles, defects by tier, tokens and wall clock for a
    run beside the thunder-viper baseline.'

    D-088 — only CYCLES were beside it. `_baseline_comparison`'s baseline and
    current objects carried `grind_cycles` and `post_verification_cycles` and
    nothing else, while `defects_by_tier`, `spend` and `wall_clock_seconds`
    were top-level current-run-only keys with no baseline cell at all. The F6
    report did the side-by-side that the CLI named in the requirement did not,
    because `foundry_report._archive_metrics` derives all five for BOTH columns
    from whichever archive it is handed.

    The whole section is that function's output now, so this asserts the four
    columns are present on both sides AND that the baseline archive planted
    beside the run is actually read for the three it can supply.
    """
    from foundry_mcp.schemas.vocab import THUNDER_VIPER_BASELINE

    run_dir = make_run_dir()
    baseline_dir = run_dir.parent / THUNDER_VIPER_BASELINE["run"]
    baseline_dir.mkdir()
    (baseline_dir / "state.json").write_text(
        json.dumps({"phase": "F6", "cycle": 21}), encoding="utf-8"
    )
    (baseline_dir / "defects.json").write_text(
        json.dumps({"defects": [{"id": "D-001", "cycle": 21, "tier": "LIVE"}]}),
        encoding="utf-8",
    )
    (baseline_dir / "handoffs.jsonl").write_text(
        '{"timestamp": "2026-08-01T00:00:00+00:00"}\n'
        '{"timestamp": "2026-08-02T00:30:00+00:00"}\n',
        encoding="utf-8",
    )

    comparison = json.loads(_invoke_measure_run(str(run_dir))[1])["baseline_comparison"]

    for column in ("grind_cycles", "post_verification_cycles", "defects_by_tier",
                   "tokens", "wall_clock_minutes"):
        assert column in comparison["current"], column
        assert column in comparison["baseline_metrics"], column

    # The two recorded numbers are the constant, never the derivation (D-085).
    assert comparison["baseline_metrics"]["grind_cycles"] == (
        THUNDER_VIPER_BASELINE["grind_cycles"]
    )
    # The three with no recorded constant come off the archive itself.
    assert comparison["baseline_metrics"]["defects_by_tier"] == _tiers(LIVE=1)
    assert comparison["baseline_metrics"]["wall_clock_minutes"] == 1470.0
    assert comparison["baseline_metrics"]["tokens"] is None, "no spend ledger"


def test_the_cli_and_the_report_publish_one_baseline_comparison(
    make_run_dir: Callable[..., Path],
) -> None:
    """D-088's structural half — the two surfaces are ONE object.

    NFR-001's comparison is printed by this CLI and by the F6 report, and a
    side-by-side table is exactly the surface where a half-unit of drift is
    invisible and decisive: D-036 (the cycle count) and D-085 (the baseline
    floor) were both that shape. Assembling the section twice is what let them
    diverge, so `_baseline_comparison` delegates to
    `foundry_report._baseline_comparison_section` and this drives both over one
    archive to pin that the payloads are equal, key for key.
    """
    from foundry_mcp.tools.foundry_report import generate_report

    run_dir = make_run_dir(rollup=_rollup_doc({"4": {"prove": _entry(9, 9, 1)}}))
    cli = json.loads(_invoke_measure_run(str(run_dir))[1])["baseline_comparison"]

    result = generate_report(run_dir.parent, run_dir)
    assert result["ok"] is True, result
    report = json.loads((run_dir / "report.json").read_text(encoding="utf-8"))

    # `metrics` is a tuple in the module and a list once round-tripped through
    # JSON on both sides, so the documents compare directly.
    assert cli == report["baseline_comparison"]


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

    assert payload["defects_by_tier"] == _tiers(unknown=6)
    for column in ("spend", "inspect_modes", "escalation"):
        assert payload[column] is None, column
    assert payload["failure_tokens"] == []


# ---------------------------------------------------------------------------
# D-182 demo — the filing driven as a real process, and its own control.
# ---------------------------------------------------------------------------


def test_demo_grind_cycle_12_the_widened_rollup_at_the_real_door(
    make_run_dir: Callable[..., Path], capsys
) -> None:
    """D-182, driven as the real process the filing drove, plus its control.

    AC-039 / NFR-001 / OT-030. The filing's control was "strip only those keys
    from a copy of the archive and nothing else changes"; that control is run
    here as an assertion rather than quoted, so the claim that the C-6 facts
    changed the VERDICT and not one NUMBER is checked every time this runs.
    """
    with capsys.disabled():
        cycle_bucket = {
            "prove": _entry(172, 172, 2),
            "trace": _entry(92, 92, 1),
            "research_audit": _entry(4, 4, 0),
        }
        widened = _rollup_doc({"3": {**cycle_bucket, **_CYCLE_LEVEL_FACTS}})
        stripped = _rollup_doc({"3": dict(cycle_bucket)})

        print("\n=== D-182 — a cycle bucket carrying its own C-6 facts ===")
        print("  the five keys `_record_cycle_facts` writes beside the stream")
        print("  tranches, in the shape the live archive holds them:")
        for key, value in _CYCLE_LEVEL_FACTS.items():
            kind = type(value).__name__
            print(f"    {key:<15} ({kind})")

        run_dir = make_run_dir(rollup=widened)
        exit_code, stdout, stderr = _invoke_measure_run(str(run_dir))
        assert exit_code == 0, (stdout, stderr)
        widened_payload = json.loads(stdout)

        print("\n  measure-run.py over that archive, as a real process:")
        print(f"    exit code:      {exit_code}")
        print(f"    failure_tokens: {widened_payload['failure_tokens']}")
        print(f"    cycles:         {widened_payload['cycles']}")
        print(
            "    coverage read:  "
            f"{sorted(widened_payload['per_cycle_coverage']['3'])}"
        )
        print(
            "  before this fix the same document produced PHASE9_SCHEMA_INVALID"
        )
        print(
            "  for the two string keys, PHASE9_UNKNOWN_STREAM for the three"
        )
        print("  mapping keys, and exit 1 — a schema fault asserted about the")
        print("  shape the spec's own Data Model widened the document to.")

        # THE FILING'S CONTROL, EXACTLY AS IT WAS DRIVEN: a COPY of the very
        # archive above with only those keys stripped out of its roll-up. A
        # second archive built from the fixtures would differ in its cohort id
        # and leave a real difference to explain away; a copy differs in
        # nothing, so an empty diff below is the whole claim.
        # Copied under a SIBLING PARENT rather than a sibling name, so the
        # copy keeps the archive's own directory name: `baseline_comparison`
        # publishes the run name, and renaming the copy would put a real
        # difference in the diff that has nothing to do with the roll-up.
        control_dir = run_dir.parent / "control" / run_dir.name
        shutil.copytree(run_dir, control_dir)
        (control_dir / "stream-rollup.json").write_text(
            json.dumps(stripped), encoding="utf-8"
        )
        control_code, control_stdout, control_stderr = _invoke_measure_run(
            str(control_dir)
        )
        assert control_code == 0, (control_stdout, control_stderr)
        control_payload = json.loads(control_stdout)

        print("\n=== the filing's own control — the same archive with only")
        print("=== those keys stripped, and nothing else changed ===")
        print(f"    exit code:      {control_code}")
        print(f"    failure_tokens: {control_payload['failure_tokens']}")
        print(f"    cycles:         {control_payload['cycles']}")

        differing = sorted(
            key
            for key in widened_payload
            if widened_payload[key] != control_payload[key]
        )
        assert differing == [], differing
        print(f"\n  keys differing between the two payloads: {differing}")
        print("  every published value identical — the five keys carry no")
        print("  measurement this command publishes. The numbers were never the")
        print("  defect; the VERDICT on a well-formed artifact was. NFR-001:")
        print("  'Numbers are the target, not a gate'.")

        print("\n=== one definition, both readers ===")
        from foundry_mcp.tools.orchestration import spend
        from foundry_mcp.tools.foundry_state import is_stream_record

        (control_dir / "stream-rollup.json").write_text(
            json.dumps(widened), encoding="utf-8"
        )
        orchestrator_view = sorted(spend._stream_dispatch_cycles(control_dir))
        predicate_view = sorted(
            key
            for key, value in {**cycle_bucket, **_CYCLE_LEVEL_FACTS}.items()
            if is_stream_record(value)
        )
        print(f"  orchestration.spend._stream_dispatch_cycles: {orchestrator_view}")
        print(f"  foundry_state.is_stream_record:               {predicate_view}")
        assert orchestrator_view == predicate_view
        print("  the same three keys, from the rule stated once rather than")
        print("  hand-typed a third time — which is how the third walker of")
        print("  this bucket came to be missing it.")


# ---------------------------------------------------------------------------
# FR-025 / FR-026 / AC-024 / AC-045 / AC-046 — THE TWO ACCEPTANCE FIGURES.
#
# NFR-006 ("zero filings across its last two INSPECT cycles") and NFR-008
# ("FULL cycles / total INSPECT cycles below 50%") are the numbers this release
# is measured by, and before this casting neither existed anywhere in the tree —
# `survey/infra.md` §9 records it flatly: "There is no 'fallout' column, metric,
# or concept anywhere in the codebase."
#
# Every test below drives the REAL CLI over a fixture archive and reads the
# emitted payload, because that is the surface an operator has. The arithmetic
# itself is `foundry_state`'s (casting 10) and is tested there; what is tested
# here is that this command publishes THAT number — the whole point of hosting
# the derivation in the leaf is that the F6 report and this CLI cannot print two
# different answers to one acceptance question.
#
# THE FAILURE MODE THESE GUARD IS A FALSE PASS. Both figures pass at LOW values,
# so an archive that never measured them and reported 0 would show the strongest
# possible result on no evidence. Every "no data" case below therefore asserts
# the MISSING verdict explicitly, and never merely that the number is absent.
# ---------------------------------------------------------------------------


def _with_inspect_modes(run_dir: Path, *decisions: dict[str, Any]) -> None:
    """Plant an `inspect_modes` list on the fixture's state.json."""
    state = json.loads((run_dir / "state.json").read_text())
    state["inspect_modes"] = list(decisions)
    (run_dir / "state.json").write_text(json.dumps(state), encoding="utf-8")


def _decision(cycle: int, mode: str, phase: str = "F2") -> dict[str, Any]:
    return {"cycle": cycle, "phase": phase, "mode": mode, "rule": "first_of_phase"}


def test_hardening_is_counted_beside_live_latent_and_unknown(
    make_run_dir: Callable[..., Path],
) -> None:
    """AC-024 / GI-014 — the non-blocking backlog is visible without the ledger.

    HARDENING is a probe that was DRIVEN and failed. It blocks no gate, which is
    exactly why it needs a column: a tier nothing refuses on is a tier nobody
    would otherwise count, and "how much hardening work is outstanding" then
    becomes a question only a hand-read of defects.json can answer.

    The column exists here because HARDENING is a member of
    `vocab.DEFECT_TIERS`, not because this file or the script names it — that is
    the key link, and `_tiers` above asserts the shape from the same roster the
    reader seeds from.
    """
    run_dir = make_run_dir()
    (run_dir / "defects.json").write_text(
        _defects(
            _defect("D-001", tier="LIVE"),
            _defect("D-002", tier="HARDENING",
                    reproduction_attempted="drove the probe; the raise is reachable"),
            _defect("D-003", tier="HARDENING",
                    reproduction_attempted="drove the second probe"),
            _defect("D-004", tier="LATENT",
                    reproduction_attempted="AST sweep finds 0 sites"),
            _defect("D-005"),
        ),
        encoding="utf-8",
    )
    payload = json.loads(_invoke_measure_run(str(run_dir))[1])
    assert payload["defects_by_tier"] == _tiers(
        LIVE=1, HARDENING=2, LATENT=1, unknown=1
    )
    assert "HARDENING" in DEFECT_TIER_OR_UNKNOWN, (
        "the column is derived from the roster; if HARDENING ever leaves it, "
        "this test should fail here and not on the count above"
    )


def test_the_tier_roster_is_never_hand_listed_in_the_script() -> None:
    """AC-024's structural half — the column comes from the vocabulary.

    Behaviour alone would stay green on a script that hand-listed four tier
    names, and would then go silently wrong the day a fifth was added: an
    archive full of the new tier would report it nowhere and the totals would
    simply not add up. The reader must SEED from the roster.
    """
    import inspect as _inspect

    source = _inspect.getsource(_load_measure_run_module()._read_defects_by_tier)
    assert "DEFECT_TIER_OR_UNKNOWN" in source, (
        "the tier column re-typed the roster instead of deriving it (GI-014)"
    )
    for tier in sorted(DEFECT_TIER_OR_UNKNOWN):
        assert f'"{tier}"' not in source, (
            f"{tier} is spelled literally in the reader; the roster is the "
            f"one source of the tier names"
        )


def test_full_cycle_ratio_is_published_with_its_verdict_below_the_threshold(
    make_run_dir: Callable[..., Path],
) -> None:
    """AC-046 / NFR-008 — below 50% is the acceptance figure, and it passes.

    Two of five INSPECT cycles ran FULL: 0.4, under the 50% NFR-008 names. The
    threshold travels WITH the number, from `foundry_state.full_cycle_ratio`, so
    this command cannot compare one ratio against a second constant.
    """
    run_dir = make_run_dir()
    _with_inspect_modes(
        run_dir,
        _decision(1, "FULL"), _decision(2, "DELTA"), _decision(3, "DELTA"),
        _decision(4, "FULL"), _decision(5, "DELTA"),
    )
    payload = json.loads(_invoke_measure_run(str(run_dir))[1])
    assert payload["full_cycle_ratio"] == {
        "full_cycles": 2, "total_cycles": 5, "ratio": 0.4,
        "threshold": 0.5, "passes": True,
    }
    assert payload["gate_verdicts"]["full_cycle_ratio"] == "PASS"


def test_full_cycle_ratio_fails_at_the_threshold_and_above_it(
    make_run_dir: Callable[..., Path],
) -> None:
    """AC-046 — "below 50%" excludes 50% itself, and the boundary is asserted.

    A-037's figure is *below* half, so a run that ran exactly half its INSPECTs
    at FULL width has not met it. Half of a boundary condition is the half that
    gets written as `<=` by accident, so both sides of it are driven: 2/4 fails
    and 3/4 fails.
    """
    run_dir = make_run_dir()
    _with_inspect_modes(
        run_dir,
        _decision(1, "FULL"), _decision(2, "FULL"),
        _decision(3, "DELTA"), _decision(4, "DELTA"),
    )
    payload = json.loads(_invoke_measure_run(str(run_dir))[1])
    assert payload["full_cycle_ratio"]["ratio"] == 0.5
    assert payload["full_cycle_ratio"]["passes"] is False, "50% is not below 50%"
    assert payload["gate_verdicts"]["full_cycle_ratio"] == "FAIL"

    _with_inspect_modes(
        run_dir,
        _decision(1, "FULL"), _decision(2, "FULL"), _decision(3, "FULL"),
        _decision(4, "DELTA"),
    )
    payload = json.loads(_invoke_measure_run(str(run_dir))[1])
    assert payload["full_cycle_ratio"]["ratio"] == 0.75
    assert payload["gate_verdicts"]["full_cycle_ratio"] == "FAIL"


def test_a_cycle_reopened_at_full_counts_once_on_the_ratio_axis(
    make_run_dir: Callable[..., Path],
) -> None:
    """AC-046 — the axis is CYCLES, not decisions (D-119's shape).

    One cycle can carry two decisions: the F2 INSPECT and the F5 one TEMPER
    opens without advancing the counter. Counting decisions would make a run
    that reopened ONE cycle at FULL look wider than a run that ran TWO cycles at
    FULL, so a run could fail this acceptance figure by doing less work.
    """
    run_dir = make_run_dir()
    _with_inspect_modes(
        run_dir,
        _decision(1, "FULL"), _decision(1, "FULL", phase="F5"),
        _decision(2, "DELTA"), _decision(3, "DELTA"), _decision(4, "DELTA"),
    )
    payload = json.loads(_invoke_measure_run(str(run_dir))[1])
    assert payload["inspect_modes"]["count"] == 5, "five decisions"
    assert payload["full_cycle_ratio"]["total_cycles"] == 4, "four cycles"
    assert payload["full_cycle_ratio"]["full_cycles"] == 1
    assert payload["full_cycle_ratio"]["ratio"] == 0.25


def test_an_archive_with_no_recorded_widths_has_no_ratio_and_does_not_pass(
    make_run_dir: Callable[..., Path],
) -> None:
    """AC-046's negative — a missing measurement is not a passing one.

    Two ways to have no ratio, and both must read MISSING rather than 0.0:
    an archive that never wrote an `inspect_modes` list (every pre-release one,
    thunder-viper included), and one that wrote the list empty. 0.0 is *below*
    50%, so a fabricated zero here is a PASS on the acceptance figure — the one
    direction in which the None-not-zero rule is not merely tidy.
    """
    run_dir = make_run_dir()                    # fixture state.json has no list
    payload = json.loads(_invoke_measure_run(str(run_dir))[1])
    assert payload["inspect_modes"] is None
    assert payload["full_cycle_ratio"] is None
    assert payload["gate_verdicts"]["full_cycle_ratio"] == "MISSING"

    _with_inspect_modes(run_dir)                # the list, written empty
    payload = json.loads(_invoke_measure_run(str(run_dir))[1])
    assert payload["full_cycle_ratio"]["ratio"] is None
    assert payload["full_cycle_ratio"]["passes"] is None, (
        "'no INSPECT recorded a width' and 'more than half were FULL' are "
        "different answers and must not print the same"
    )
    assert payload["gate_verdicts"]["full_cycle_ratio"] == "MISSING"


def test_fallout_is_counted_per_cycle_from_the_defect_ledger(
    make_run_dir: Callable[..., Path],
) -> None:
    """FR-025 / AC-045 / OT-039 — "measure-run counts it per cycle".

    THE COUNTING HALF IS THIS COMMAND'S AND THE VALIDATING HALF IS NOT. The
    filing doors accept `fallout_of` and refuse an unknown id (casting 4); this
    command reports what the ledger holds and judges no filing — which is why
    the fixture below is built by hand rather than through a door, and why a
    record naming a parent that is not in this ledger is still counted here.

    A cycle with records but no fallout reads 0, because the records CARRY the
    key: that is a measurement. A cycle whose records have no key at all is a
    different fact and is asserted in the test below this one.
    """
    run_dir = make_run_dir()
    (run_dir / "defects.json").write_text(
        _defects(
            _defect("D-001", cycle=1, fallout_of=None),
            _defect("D-002", cycle=1, fallout_of="D-001"),
            _defect("D-003", cycle=2, fallout_of="D-001"),
            _defect("D-004", cycle=2, fallout_of="D-002"),
            _defect("D-005", cycle=3, fallout_of=None),
        ),
        encoding="utf-8",
    )
    payload = json.loads(_invoke_measure_run(str(run_dir))[1])
    fallout = payload["fallout_per_cycle"]

    assert fallout["total"] == 3
    assert {c: b["fallout"] for c, b in fallout["per_cycle"].items()} == {
        "1": 1, "2": 2, "3": 0,
    }
    assert fallout["per_cycle"]["2"]["ids"] == ["D-003", "D-004"]
    assert fallout["measured_records"] == 5
    assert fallout["unmeasured_records"] == 0
    assert list(fallout["per_cycle"]) == ["1", "2", "3"], "numeric cycle order"


def test_a_ledger_that_predates_the_field_is_unmeasured_not_zero(
    make_run_dir: Callable[..., Path],
) -> None:
    """FR-054 / AC-045 — an absent key is not a measured zero.

    Every record written before `fallout_of` existed carries no such key, and
    counting those cycles as "zero fallout" would certify NFR-006 on an archive
    that never measured it — the strongest possible acceptance result from the
    least possible evidence. The verdict names the cycles and says what to run.
    """
    run_dir = make_run_dir()
    (run_dir / "defects.json").write_text(
        _defects(
            _defect("D-001", cycle=1),
            _defect("D-002", cycle=2),
            _defect("D-003", cycle=3),
        ),
        encoding="utf-8",
    )
    payload = json.loads(_invoke_measure_run(str(run_dir))[1])
    fallout = payload["fallout_per_cycle"]

    assert fallout["total"] == 0, "no record names a parent"
    assert fallout["measured_records"] == 0, "and none carries the key either"
    assert fallout["unmeasured_records"] == 3
    assert fallout["verdict"] == "not_measurable"
    assert "migrate-archive.py" in fallout["verdict_reason"], (
        "the operator is told how to make the pair measurable"
    )
    assert payload["gate_verdicts"]["fallout"] == "MISSING", (
        "a total of 0 over an unmeasured ledger must not read as a pass"
    )


def test_the_fallout_verdict_passes_only_on_a_clean_closing_pair(
    make_run_dir: Callable[..., Path],
) -> None:
    """NFR-006 / NFR-007 — zero across the LAST TWO INSPECT cycles.

    Three drives against one axis: a clean pair passes, a single filing in
    either cycle of the pair fails, and an earlier cycle's filing does not —
    the criterion is about where the run FINISHED, which is the whole point of
    a convergence figure.
    """
    run_dir = make_run_dir()

    def _drive(*records: dict[str, Any]) -> dict[str, Any]:
        (run_dir / "defects.json").write_text(_defects(*records), encoding="utf-8")
        return json.loads(_invoke_measure_run(str(run_dir))[1])

    # Fallout early, none in the closing pair (cycles 2 and 3) — a converging run.
    payload = _drive(
        _defect("D-001", cycle=1, fallout_of="D-000"),
        _defect("D-002", cycle=2, fallout_of=None),
        _defect("D-003", cycle=3, fallout_of=None),
    )
    assert payload["fallout_per_cycle"]["last_two_cycles"] == [2, 3]
    assert payload["fallout_per_cycle"]["verdict"] == "pass"
    assert payload["gate_verdicts"]["fallout"] == "PASS"

    # One filing in the closing pair is enough to fail it.
    payload = _drive(
        _defect("D-001", cycle=1, fallout_of=None),
        _defect("D-002", cycle=2, fallout_of="D-001"),
        _defect("D-003", cycle=3, fallout_of=None),
    )
    assert payload["fallout_per_cycle"]["verdict"] == "fail"
    assert "[2]" in payload["fallout_per_cycle"]["verdict_reason"]
    assert payload["gate_verdicts"]["fallout"] == "FAIL"

    # And so is one in the last cycle alone.
    payload = _drive(
        _defect("D-001", cycle=2, fallout_of=None),
        _defect("D-002", cycle=3, fallout_of="D-001"),
    )
    assert payload["fallout_per_cycle"]["verdict"] == "fail"
    assert payload["gate_verdicts"]["fallout"] == "FAIL"


def test_a_run_with_fewer_than_two_inspect_cycles_cannot_pass_the_figure(
    make_run_dir: Callable[..., Path],
) -> None:
    """NFR-006's negative — the criterion is defined over a PAIR.

    A one-cycle run has no pair. Reporting `pass` because nothing contradicted
    the criterion would make the acceptance figure easiest to satisfy on the
    runs that did the least work, which is the opposite of what a convergence
    figure is for.
    """
    run_dir = make_run_dir()
    (run_dir / "state.json").write_text(
        json.dumps({"phase": "F2", "cycle": 0}), encoding="utf-8"
    )
    (run_dir / "defects.json").write_text(
        _defects(_defect("D-001", cycle=0, fallout_of=None)), encoding="utf-8"
    )
    payload = json.loads(_invoke_measure_run(str(run_dir))[1])
    fallout = payload["fallout_per_cycle"]

    assert fallout["total"] == 0
    assert fallout["last_two_cycles"] == []
    assert fallout["verdict"] == "not_measurable"
    assert "fewer than two" in fallout["verdict_reason"]
    assert payload["gate_verdicts"]["fallout"] == "MISSING"


def test_a_missing_defect_ledger_has_no_fallout_census_at_all(
    make_run_dir: Callable[..., Path],
) -> None:
    """The structurally-missing spelling, on the fallout column too."""
    run_dir = make_run_dir()
    (run_dir / "defects.json").unlink()
    payload = json.loads(_invoke_measure_run(str(run_dir))[1])
    assert payload["fallout_per_cycle"] is None
    assert payload["gate_verdicts"]["fallout"] == "MISSING"


def test_neither_acceptance_figure_reaches_the_process_status(
    make_run_dir: Callable[..., Path],
) -> None:
    """CT-016 / NFR-001 — "the numbers are the target, not a gate".

    Both new verdicts FAIL here and the command still exits 0, because
    `_exit_status` answers one question — could this command READ what it was
    pointed at — and neither figure is an answer to it. `survey/infra.md` §9
    records the exit contract (0 OK, 1 unreadable artefact, 2 usage) and
    CT-016's error column says it does not change; D-086 is what it cost the
    last time a published number reached the status.
    """
    run_dir = make_run_dir()
    _with_inspect_modes(run_dir, _decision(1, "FULL"), _decision(2, "FULL"))
    (run_dir / "defects.json").write_text(
        _defects(
            _defect("D-001", cycle=1, fallout_of=None),
            _defect("D-002", cycle=2, fallout_of="D-001"),
        ),
        encoding="utf-8",
    )
    exit_code, stdout, stderr = _invoke_measure_run(str(run_dir))
    payload = json.loads(stdout)

    assert payload["gate_verdicts"]["full_cycle_ratio"] == "FAIL"
    assert payload["gate_verdicts"]["fallout"] == "FAIL"
    assert payload["failure_tokens"] == [], (stdout, stderr)
    assert exit_code == 0, "a failing acceptance figure is reported, never gated"


def test_the_matrix_carries_both_acceptance_figures_and_leaves_them_empty(
    tmp_path: Path,
) -> None:
    """CT-016 — the two figures are readable off the cohort matrix too.

    A cohort study compares arms on the numbers this release is measured by, so
    a figure that exists only in the per-run JSON is a figure the comparison
    cannot see. Both are EMPTY here rather than 0 — the synthesized cohort arms
    record no `inspect_modes` and no `fallout_of` — which is the same D-087
    spelling `wall_clock_seconds` uses two columns to the left, and matters more
    for these two because 0 is their PASSING value.
    """
    runs = _populate_runs_dir(tmp_path)
    exit_code, stdout, stderr = _invoke_measure_run(
        "--matrix", str(runs), "--format", "csv"
    )
    assert exit_code == 0, (stdout, stderr)
    rows = list(csv.reader(io.StringIO(stdout)))
    header, data = rows[0], rows[1:]

    for col in ("full_cycle_ratio", "fallout_per_cycle_json"):
        assert col in header, header
    ratio_col = header.index("full_cycle_ratio")
    fallout_col = header.index("fallout_per_cycle_json")
    for row in data:
        assert row[ratio_col] == "", (
            "an arm that recorded no INSPECT width has no ratio, and 0.0000 "
            "would be a PASS on the acceptance figure"
        )
        assert row[fallout_col] == "", "and no fallout census either"


def test_the_script_reads_the_consolidated_derivations_and_defines_none(
) -> None:
    """GI-024 — the anti-drift check: one derivation, read not re-typed.

    `survey/architecture.md` §3.2 inventories what this file used to carry its
    own copy of, by line: the spend bucket shape (`:626`), the spend roll-up
    (`_read_spend:557`), `_cycle_sort_key:652` and `_as_count:645`. Its verdict
    on the spend aggregation alone is "three implementations, one question", and
    the four defects that pair cost are recorded in `foundry_report._read_spend`'s
    own docstring (D-038, D-090, D-162, D-163).

    BEHAVIOUR ALONE CANNOT HOLD THIS DOWN. Two implementations of one rule agree
    on the day they are written and part on the first input neither test covers
    — which is exactly how the three spend readers came to publish `agents`
    meaning two different things. So the check is structural: the consolidated
    objects must be the SAME objects, by identity, and each reader must call the
    leaf rather than re-implement it.

    A NEW derivation added here in future fails this test at the `is` assertion
    or the `getsource` one, whichever it takes — and the correct response is a
    `Foundry-Concern` naming casting 10, which owns `foundry_state.py`, never a
    fifth copy written here.
    """
    import inspect as _inspect

    from foundry_mcp.tools import foundry_state

    module = _load_measure_run_module()

    # The two shared primitives are BOUND, not wrapped: a wrapper is a fourth
    # definition with an extra frame, which is the shape the inventory lists.
    assert module._as_count is foundry_state.as_count
    assert module._cycle_sort_key is foundry_state.cycle_sort_key

    # And the four consolidated tables are CALLED.
    for reader, leaf in (
        (module._read_spend, "spend_rollup("),
        (module._read_inspect_modes, "inspect_mode_rows("),
        (module._read_inspect_modes, "full_cycle_ratio("),
        (module._read_escalation, "escalated_class_rows("),
        (module._read_fallout, "fallout_rows("),
        (module._read_dispatch_summary, "unreported_dispatch_summary("),
    ):
        source = _inspect.getsource(reader)
        assert leaf in source, (
            f"{reader.__name__} does not call {leaf} — a table this script is "
            f"supposed to READ has been derived here a second time (GI-024)"
        )

    # The four copies the survey names by line are gone from the whole module.
    whole = Path(SCRIPT).read_text(encoding="utf-8")
    for gone in (
        "def _new_spend_bucket", "def _as_count(", "def _cycle_sort_key(",
    ):
        assert gone not in whole, f"{gone} is the copy §3.2 inventories"


def test_the_rollup_reader_takes_the_last_record_as_the_total_on_schema_4(
    make_run_dir: Callable[..., Path],
) -> None:
    """FR-054 / AC-034 — both ledger shapes read, and schema 4 reads the LAST.

    THE TWO SHAPES DIFFER IN THE TOTALS AND NOWHERE ELSE. Both carry `records`;
    what changed is what the top-level `items_checked` / `items_total` mean. The
    old additive writer ACCUMULATED them across every record, so a stream that
    recorded twice reads above 100% — nine daring-orca rows do, cycle 29's trace
    at 1084/542 over two records of 542/542 being the row AC-034 and OT-032
    name. Under schema 4 those same top-level keys are the LAST record's values,
    rewritten there by `migrate-archive.py` step 8 and written there from the
    start by `Foundry-Stream`'s replace semantics, with every earlier record
    retained under `records[]`.

    THE READER NEEDS NO SCHEMA TEST FOR THIS, and that is the assertion. It has
    always taken the top-level totals, so the migration moves the NUMBER and
    this reader keeps its one rule; the same fixture read before and after gives
    1084/542 and then 542/542 with nothing here branching on a version. A
    version branch would be a second place for the two shapes to be told apart,
    and they do not need telling apart — which is why AC-034 is a migration
    assertion and not a reader one.

    The over-100% row is NOT normalised on the way through either. A coverage
    figure quietly clamped to its total is a measurement replaced by an
    assertion, and an operator measuring an un-migrated archive needs to see
    that the run recorded 1084 checks against 542 items.
    """
    records = [
        {"items_checked": 542, "items_total": 542, "findings": 4,
         "recorded_at": "2026-09-05T01:03:47.428012+00:00"},
        {"items_checked": 542, "items_total": 542, "findings": 4,
         "recorded_at": "2026-09-05T01:04:52.960145+00:00"},
    ]

    # Schema 3 — the additive writer's totals, above 100%.
    run_dir = make_run_dir(rollup=_rollup_doc({
        "3": {"trace": {"items_checked": 1084, "items_total": 542,
                        "findings": 8, "records": records}},
    }))
    payload = json.loads(_invoke_measure_run(str(run_dir))[1])
    assert payload["per_cycle_coverage"]["3"]["TRACE"] == {
        "items_checked": 1084, "items_total": 542, "findings": 8,
    }
    assert payload["failure_tokens"] == [], (
        "an over-100% row is the shape the additive writer left, not a "
        "malformed document"
    )

    # Schema 4 — the same records, totals rewritten to the LAST of them.
    run_dir = make_run_dir(cohort_id="no_TEST_01", rollup=_rollup_doc({
        "3": {"trace": {"items_checked": 542, "items_total": 542,
                        "findings": 4, "records": records}},
    }))
    payload = json.loads(_invoke_measure_run(str(run_dir))[1])
    assert payload["per_cycle_coverage"]["3"]["TRACE"] == {
        "items_checked": 542, "items_total": 542, "findings": 4,
    }
    assert payload["failure_tokens"] == []


def test_demo_the_two_acceptance_figures_on_one_archive(
    make_run_dir: Callable[..., Path],
) -> None:
    """FR-026 / FR-025 / NFR-008 / NFR-006 / OT-040 / OT-039 — both figures.

    A demonstration, printed, because these two numbers are what the release is
    measured BY: NFR-007 asks for "a terminal state in materially fewer
    FULL-width cycles" and NFR-006 for "zero filings across its last two INSPECT
    cycles", and neither existed anywhere in the tree before this casting —
    `survey/infra.md` §9: "There is no 'fallout' column, metric, or concept
    anywhere in the codebase."

    Driven through the real CLI on one archive, twice: a run that fails both,
    and then the same archive rewritten to a converging shape. Everything
    printed is read out of the emitted payload; the assertions are the ones the
    non-demo tests above make.
    """
    run_dir = make_run_dir()

    def _drive() -> dict[str, Any]:
        exit_code, stdout, stderr = _invoke_measure_run(str(run_dir))
        assert exit_code == 0, (stdout, stderr)
        return json.loads(stdout)

    def _report(payload: dict[str, Any], heading: str) -> None:
        ratio = payload["full_cycle_ratio"]
        fallout = payload["fallout_per_cycle"]
        print(f"\n=== {heading} ===")
        print(f"  full_cycle_ratio: {ratio['full_cycles']}/{ratio['total_cycles']}"
              f" = {ratio['ratio']}  (threshold {ratio['threshold']}, "
              f"passes={ratio['passes']})")
        print(f"    verdict: {payload['gate_verdicts']['full_cycle_ratio']}")
        print(f"  fallout per cycle: "
              f"{ {c: b['fallout'] for c, b in fallout['per_cycle'].items()} }")
        print(f"    closing pair {fallout['last_two_cycles']}, "
              f"total {fallout['total']}, verdict {fallout['verdict']}")
        print(f"    reason: {fallout['verdict_reason']}")
        print(f"    verdict: {payload['gate_verdicts']['fallout']}")
        print(f"  process exit: 0 — NFR-001, the numbers are the target and "
              f"not a gate")

    # A run that met neither: every INSPECT ran FULL, and the closing cycle
    # filed a defect that is fallout of an earlier one.
    _with_inspect_modes(
        run_dir,
        _decision(1, "FULL"), _decision(2, "FULL"), _decision(3, "FULL"),
    )
    (run_dir / "defects.json").write_text(
        _defects(
            _defect("D-001", cycle=1, fallout_of=None),
            _defect("D-002", cycle=2, fallout_of=None),
            _defect("D-003", cycle=3, fallout_of="D-001"),
        ),
        encoding="utf-8",
    )
    failing = _drive()
    _report(failing, "a run that met neither figure")
    assert failing["gate_verdicts"]["full_cycle_ratio"] == "FAIL"
    assert failing["gate_verdicts"]["fallout"] == "FAIL"

    # The converging shape: most INSPECTs narrowed to DELTA, and the last two
    # cycles filed nothing that is fallout of anything.
    _with_inspect_modes(
        run_dir,
        _decision(1, "FULL"), _decision(2, "DELTA"), _decision(3, "DELTA"),
    )
    (run_dir / "defects.json").write_text(
        _defects(
            _defect("D-001", cycle=1, fallout_of="D-000"),
            _defect("D-002", cycle=2, fallout_of=None),
            _defect("D-003", cycle=3, fallout_of=None),
        ),
        encoding="utf-8",
    )
    passing = _drive()
    _report(passing, "the same archive, converged")
    assert passing["gate_verdicts"]["full_cycle_ratio"] == "PASS"
    assert passing["gate_verdicts"]["fallout"] == "PASS"

    print("\n  both figures are read from `foundry_state` — `full_cycle_ratio`")
    print("  and `fallout_rows` — so the F6 report publishes the SAME two")
    print("  numbers rather than a second derivation of an acceptance")
    print("  criterion, which is the one place a disagreement is unarguable.")
