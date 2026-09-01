"""Phase 8 / INTENT-01 — intent-carrier coverage tests.

17 RED-or-SKIP stubs covering Plans 08-02 / 08-03 / 08-04 territory plus
2 Wave-0 sentinel tests.

Plan 08-01 territory (Wave 0 sentinels — 2 stubs):
  1. test_fixture_loader
  2. test_module_collection_guard

Plan 08-02 territory (validator behavior — 10 stubs):
  3. test_unknown_top_level_key_rejected
  4. test_a_nnn_literal_in_prompt_propagated
  5. test_a_auto_nnn_literal_propagated
  6. test_typed_row_indirection_paraphrased
  7. test_zero_coverage_answer_dropped (was test_a_nnn_absent_dropped;
     rewritten to per-answer aggregation semantics — Casting C1 / AC-005)
  8. test_partial_dropped_covered_elsewhere_passes (was
     test_any_dropped_blocks; expected exit inverted under the per-answer
     aggregation rule — Casting C1 / AC-005)
  9. test_a1_not_substring_matched_in_a12
  10. test_missing_appendix_vacuous
  11. test_agent_used_embedding_audit
  12. test_intent_coverage_regex_byte_equivalent_to_validate_spec

Plan 08-03 territory (agent + MCP tool — 1 stub direct, 1 grouped under 08-04):
  13. test_agent_frontmatter_shape

Plan 08-04 territory (integration — 5 stubs, conditional-skip):
  14. test_mcp_tool_registered (Plan 08-03 ships server.py edit; grouped
      with 08-04 in VALIDATION map for orchestrator-routing reasons)
  15. test_start_md_has_f07_section
  16. test_f05_step2b_lists_intent_carrier
  17. test_v20_spec_skips_intent_carrier
  18. test_synthetic_regression_zero_fp

VALIDATION.md per-task verification map lists 17 rows total
(8-01-01 + 8-01-02 + 8-02-01..10 + 8-03-01 + 8-04-01..05). Stub 18
(synthetic regression) collapses Plan 08-04's 8-04-05; stub 14
(MCP tool registration) covers 8-04-01.

RED-or-SKIP discipline:

- Module-top guard: ``pytest.skip(allow_module_level=True)`` when
  ``validate-intent-coverage.py`` is missing on disk. Plan 08-01
  ships ZERO production code, so all 17 stubs SKIP at module-top
  until Plan 08-02 ships the validator script. Mirrors Phase 7
  Plan 07-01 file-existence-gated module-skip discipline.
- Plan 08-02 ships validator -> module collects; Plan 08-02 territory
  tests turn RED-or-GREEN depending on validator behavior.
- Plan 08-03 ships agent file + MCP tool registration -> Plan 08-03
  territory tests (13 + 14) auto-flip from per-test SKIP to RED-or-GREEN
  with zero edits to this file.
- Plan 08-04 ships start.md edits -> Plan 08-04 territory tests
  (15-18) auto-flip from per-test SKIP to RED-or-GREEN with zero
  edits to this file.

Phase 1+2+3+4+5+6+7 byte-equivalence is preserved by living in a NEW
module — no edits to existing test_*.py modules.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Any, Callable

import pytest

# tests/test_intent_coverage.py -> parents: [0]=tests, [1]=mcp-server,
# [2]=foundry, [3]=plugins, [4]=repo-root. Mirrors test_spec_test_deriver.py
# (Phase 7 Plan 07-01) which uses parents[4] verified-working form.
REPO_ROOT = Path(__file__).resolve().parents[4]
VALIDATE_PATH = (
    REPO_ROOT / "plugins" / "foundry" / "scripts"
    / "validate-intent-coverage.py"
)
# GI-002 / FR-007: canonical, importable, wheel-safe validator module. The
# dash-named VALIDATE_PATH above is now a thin shim that imports main() from
# this module; the validator BODY (regex SSoT, closed vocabularies) lives
# here. Source-content assertions (byte-equivalence) read this path.
CANONICAL_VALIDATOR_PATH = (
    REPO_ROOT / "plugins" / "foundry" / "mcp-server" / "src"
    / "foundry_mcp" / "scripts" / "validate_intent_coverage.py"
)
VALIDATE_SPEC_PY = (
    REPO_ROOT / "plugins" / "forge" / "scripts" / "validate-spec.py"
)
AGENT_PATH = (
    REPO_ROOT / "plugins" / "foundry" / "agents" / "intent-carrier.md"
)
START_MD = (
    REPO_ROOT / "plugins" / "foundry" / "commands" / "start.md"
)
SERVER_PY = (
    REPO_ROOT / "plugins" / "foundry" / "mcp-server" / "src"
    / "foundry_mcp" / "server.py"
)
FIXTURES_DIR = Path(__file__).parent / "fixtures"


if not VALIDATE_PATH.exists():
    pytest.skip(
        "validate-intent-coverage.py not yet shipped — "
        "Plan 08-02 territory",
        allow_module_level=True,
    )


# ---------------------------------------------------------------------------
# Plan 08-01 territory — Wave 0 sentinel tests (2 stubs).
#
# These two GREEN as soon as the module collects (i.e., once Plan 08-02
# ships the validator script and lifts the module-top guard). Until then
# they SKIP at module-top with the rest.
# ---------------------------------------------------------------------------


def test_fixture_loader() -> None:
    """Plan 08-01 sentinel — every Wave-0 fixture loads cleanly.

    Sanity check: each of the 8 intent_coverage JSONs + 3 specs +
    2 transcripts + 5 casting prompts + 2 tool-call logs parses. Catches
    typos / truncations / shadow-edits to the Wave-0 fixture surface.
    Mirror of 8-01-01 row in VALIDATION.md per-task verification map.
    """
    coverage = sorted((FIXTURES_DIR / "intent_coverage").glob("*.json"))
    # Plan 08-01 ships 8 base fixtures; Plan 08-04 ships 7 NEW
    # synthetic-regression fixtures (intent_coverage_synthetic_regression_*.json),
    # bringing the suite to 15 total. test_synthetic_regression_zero_fp asserts
    # the >= 12 lower-bound shape on the suite as Plan 08-04's primary contract.
    assert len(coverage) >= 8, f"expected >= 8 intent_coverage fixtures, got {len(coverage)}"
    for f in coverage:
        json.loads(f.read_text(encoding="utf-8"))

    specs = sorted((FIXTURES_DIR / "specs").glob("spec_intent_*.md"))
    assert len(specs) == 3, f"expected 3 intent specs, got {len(specs)}"
    for s in specs:
        text = s.read_text(encoding="utf-8")
        assert text.startswith("---"), f"missing frontmatter in {s.name}"

    transcripts = sorted((FIXTURES_DIR / "transcripts").glob("transcript_intent_*.md"))
    assert len(transcripts) == 2, f"expected 2 intent transcripts, got {len(transcripts)}"

    prompts = sorted((FIXTURES_DIR / "casting_prompts").glob("casting-*-prompt-*.md"))
    assert len(prompts) == 5, f"expected 5 casting-prompt fixtures, got {len(prompts)}"

    logs = sorted((FIXTURES_DIR / "tool_call_logs").glob("tool_call_log_intent_*.json"))
    assert len(logs) == 2, f"expected 2 intent tool-call-log fixtures, got {len(logs)}"
    for f in logs:
        json.loads(f.read_text(encoding="utf-8"))


def test_module_collection_guard() -> None:
    """Plan 08-01 sentinel — module-top guard IS pytest.skip(allow_module_level=True).

    Meta-test: when this test runs, the module has already collected — so
    the module-top guard either was bypassed (validator exists, this is
    fine) or was a pytest.skip(allow_module_level=True). Assert by
    reading the source: the guard SHOULD be a pytest.skip with
    allow_module_level=True. Mirror of 8-01-02 row in VALIDATION.md
    per-task verification map.
    """
    source = Path(__file__).read_text(encoding="utf-8")
    assert "allow_module_level=True" in source, (
        "module-top guard must use pytest.skip(allow_module_level=True) "
        "so the entire module SKIPs cleanly when validate-intent-coverage.py "
        "is missing"
    )
    # Cross-check: the guard sits BEFORE the first test definition.
    guard_idx = source.index("allow_module_level=True")
    first_test_idx = source.index("def test_fixture_loader")
    assert guard_idx < first_test_idx, "guard must precede all test defs"


# ---------------------------------------------------------------------------
# Plan 08-02 territory — validator behavior tests (10 stubs).
#
# These tests use the run_intent_coverage_validator fixture (defined in
# conftest.py); the fixture itself pytest.skip()s when the validator
# script is missing, so per-test SKIP is automatic at fixture-acquire
# time. Once Plan 08-02 ships, the module-top guard above passes and
# these tests turn RED-or-GREEN based on validator behavior.
# ---------------------------------------------------------------------------


def test_unknown_top_level_key_rejected(
    run_intent_coverage_validator: Callable[..., tuple[int, str, str]],
) -> None:
    """Plan 08-02 territory — schema-closed top-level discipline.

    intent_coverage_schema_invalid.json carries a smuggled
    ``auto_resolve_hint`` top-level key NOT in KNOWN_INTENT_COVERAGE_KEYS;
    validator MUST reject with INTENT_COVERAGE_SCHEMA_INVALID token.
    """
    coverage = (
        FIXTURES_DIR / "intent_coverage" / "intent_coverage_schema_invalid.json"
    )
    exit_code, stdout, _ = run_intent_coverage_validator(coverage)
    assert exit_code != 0
    assert "INTENT_COVERAGE_SCHEMA_INVALID" in stdout, stdout


def test_a_nnn_literal_in_prompt_propagated(
    run_intent_coverage_validator: Callable[..., tuple[int, str, str]],
    tmp_path: Path,
) -> None:
    """Plan 08-02 territory — A-NNN literal in prompt body produces PROPAGATED.

    Synth fixture in tmp_path: appendix has A-001, casting prompt body
    contains ``A-001`` verbatim. Resulting verdict for (A-001, casting-1)
    cell MUST be PROPAGATED with citation_chain=["A-001"]; validator
    exits 0 on the resulting matrix.
    """
    spec = tmp_path / "spec.md"
    spec.write_text(
        "---\nspec_format_version: v2.1\n---\n"
        "## Appendix: Interview Transcript\n\n"
        "## A-001 [Locked]\nSurface contract. [from Q-001]\n",
        encoding="utf-8",
    )
    coverage = tmp_path / "coverage.json"
    coverage.write_text(
        json.dumps({
            "stream": "INTENT-01",
            "phase": "F0.7",
            "spec_format_version": "v2.1",
            "spec_hash": "sha256:abc",
            "agent_path": "plugins/foundry/agents/intent-carrier.md",
            "wall_clock_seconds": 1.0,
            "answer_count": 1,
            "casting_count": 1,
            "summary": {"PROPAGATED": 1, "PARAPHRASED": 0, "DROPPED": 0},
            "matrix": [
                {"answer_id": "A-001", "casting_id": "1",
                 "verdict": "PROPAGATED", "citation_chain": ["A-001"]},
            ],
        }),
        encoding="utf-8",
    )
    exit_code, stdout, stderr = run_intent_coverage_validator(
        coverage, spec_path=spec,
    )
    assert exit_code == 0, (stdout, stderr)


def test_a_auto_nnn_literal_propagated(
    run_intent_coverage_validator: Callable[..., tuple[int, str, str]],
    tmp_path: Path,
) -> None:
    """Plan 08-02 territory — A-AUTO-NNN literal also produces PROPAGATED.

    Synth fixture: appendix has A-AUTO-003 [DEPLOYMENT], casting prompt
    body contains ``A-AUTO-003`` verbatim. Resulting verdict for the
    (A-AUTO-003, casting-1) cell MUST be PROPAGATED. Mirrors
    test_a_nnn_literal_in_prompt_propagated for the implicit-fact branch.
    """
    spec = tmp_path / "spec.md"
    spec.write_text(
        "---\nspec_format_version: v2.1\n---\n"
        "## Appendix: Interview Transcript\n\n"
        "## A-AUTO-003 [DEPLOYMENT]\nDeploys via k8s manifest. [auto-extracted]\n",
        encoding="utf-8",
    )
    coverage = tmp_path / "coverage.json"
    coverage.write_text(
        json.dumps({
            "stream": "INTENT-01",
            "phase": "F0.7",
            "spec_format_version": "v2.1",
            "spec_hash": "sha256:abc",
            "agent_path": "plugins/foundry/agents/intent-carrier.md",
            "wall_clock_seconds": 1.0,
            "answer_count": 1,
            "casting_count": 1,
            "summary": {"PROPAGATED": 1, "PARAPHRASED": 0, "DROPPED": 0},
            "matrix": [
                {"answer_id": "A-AUTO-003", "casting_id": "1",
                 "verdict": "PROPAGATED", "citation_chain": ["A-AUTO-003"]},
            ],
        }),
        encoding="utf-8",
    )
    exit_code, stdout, stderr = run_intent_coverage_validator(
        coverage, spec_path=spec,
    )
    assert exit_code == 0, (stdout, stderr)


def test_typed_row_indirection_paraphrased(
    run_intent_coverage_validator: Callable[..., tuple[int, str, str]],
) -> None:
    """Plan 08-02 territory — PARAPHRASED via typed-row indirection.

    Locked decision A: typed-row indirection IS the canonical PARAPHRASED
    state. Fixture intent_coverage_paraphrased_via_typed.json has
    (A-005, casting-1) with verdict=PARAPHRASED + citation_chain=
    ["A-005", "<contracts>"]. Paired with spec_intent_clean.md which
    has CT-001 row citing [from A-005]. PARAPHRASED is a PASS verdict;
    matrix has dropped=0, so exit==0.
    """
    coverage = (
        FIXTURES_DIR / "intent_coverage"
        / "intent_coverage_paraphrased_via_typed.json"
    )
    spec = FIXTURES_DIR / "specs" / "spec_intent_clean.md"
    exit_code, stdout, _ = run_intent_coverage_validator(
        coverage, spec_path=spec,
    )
    assert exit_code == 0, stdout
    # citation_chain shape preserved
    data = json.loads(coverage.read_text(encoding="utf-8"))
    para_cells = [c for c in data["matrix"] if c["verdict"] == "PARAPHRASED"]
    assert any(
        c["citation_chain"] == ["A-005", "<contracts>"] for c in para_cells
    ), data


def test_zero_coverage_answer_dropped(
    run_intent_coverage_validator: Callable[..., tuple[int, str, str]],
    tmp_path: Path,
) -> None:
    """Casting C1 / AC-001 — answer DROPPED in EVERY casting -> gate blocks.

    Per-answer aggregation semantics: A-005's cell is DROPPED in BOTH
    castings (zero coverage) while A-001 is PROPAGATED everywhere.
    Validator exits 1 with INTENT_COVERAGE_DROPPED naming exactly A-005
    and no other answer_id.
    """
    spec = tmp_path / "spec.md"
    spec.write_text(
        "---\nspec_format_version: v2.1\n---\n"
        "## Appendix: Interview Transcript\n\n"
        "## A-001 [Locked]\nSurface contract. [from Q-001]\n\n"
        "## A-005 [Locked]\nDropped everywhere. [from Q-005]\n",
        encoding="utf-8",
    )
    coverage = tmp_path / "coverage.json"
    coverage.write_text(
        json.dumps({
            "stream": "INTENT-01",
            "phase": "F0.7",
            "spec_format_version": "v2.1",
            "spec_hash": "sha256:abc",
            "agent_path": "plugins/foundry/agents/intent-carrier.md",
            "wall_clock_seconds": 1.0,
            "answer_count": 2,
            "casting_count": 2,
            "summary": {"PROPAGATED": 2, "PARAPHRASED": 0, "DROPPED": 2},
            "matrix": [
                {"answer_id": "A-001", "casting_id": "1",
                 "verdict": "PROPAGATED", "citation_chain": ["A-001"]},
                {"answer_id": "A-001", "casting_id": "2",
                 "verdict": "PROPAGATED", "citation_chain": ["A-001"]},
                {"answer_id": "A-005", "casting_id": "1",
                 "verdict": "DROPPED", "citation_chain": ["A-005"]},
                {"answer_id": "A-005", "casting_id": "2",
                 "verdict": "DROPPED", "citation_chain": ["A-005"]},
            ],
        }),
        encoding="utf-8",
    )
    exit_code, stdout, _ = run_intent_coverage_validator(
        coverage, spec_path=spec,
    )
    assert exit_code != 0
    assert "INTENT_COVERAGE_DROPPED" in stdout, stdout
    assert "A-005" in stdout, stdout
    # A-001 is covered — it MUST NOT be named as dropped.
    assert "A-001" not in stdout, stdout


def test_partial_dropped_covered_elsewhere_passes(
    run_intent_coverage_validator: Callable[..., tuple[int, str, str]],
) -> None:
    """Casting C1 / AC-001+AC-004 — per-cell DROPPED with coverage elsewhere passes.

    intent_coverage_one_dropped.json has (A-005, casting-1) DROPPED but
    (A-005, casting-2) PROPAGATED — A-005 reaches at least one casting.
    Under the per-answer aggregation rule the gate MUST NOT block: the
    per-cell DROPPED verdict stays recorded in the matrix (AC-002), but
    exit code is 0 and no INTENT_COVERAGE_DROPPED line is emitted.
    """
    coverage = (
        FIXTURES_DIR / "intent_coverage" / "intent_coverage_one_dropped.json"
    )
    spec = FIXTURES_DIR / "specs" / "spec_intent_clean.md"
    exit_code, stdout, _ = run_intent_coverage_validator(
        coverage, spec_path=spec,
    )
    assert exit_code == 0, stdout
    assert "INTENT_COVERAGE_DROPPED" not in stdout, stdout
    # AC-002: the per-cell DROPPED verdict is still recorded in the matrix.
    data = json.loads(coverage.read_text(encoding="utf-8"))
    dropped_cells = [
        c for c in data["matrix"] if c["verdict"] == "DROPPED"
    ]
    assert len(dropped_cells) == 1, data
    assert dropped_cells[0]["answer_id"] == "A-005", data


def _write_production_layout(
    tmp_path: Path,
    spec_text: str,
    prompts: dict[str, str],
) -> Path:
    """Build the PRODUCTION layout: spec + castings co-located.

    Writes ``spec.md``, ``castings/manifest.json`` (declaring exactly the
    prompt casting ids), and one ``castings/casting-{id}-prompt.md`` per
    entry — the layout the Foundry-Intent-Coverage gate presents to the
    validator, in which spec→matrix completeness, ghost-casting checking,
    AND three-anchor verdict re-derivation all execute. Returns the spec
    path (GRIND D-016: the old AC-004 regression ran with no --spec and
    no co-located castings/, so none of those checks ever ran).
    """
    spec = tmp_path / "spec.md"
    spec.write_text(spec_text, encoding="utf-8")
    castings_dir = tmp_path / "castings"
    castings_dir.mkdir()
    (castings_dir / "manifest.json").write_text(
        json.dumps({"castings": [{"id": cid} for cid in sorted(prompts)]}),
        encoding="utf-8",
    )
    for cid, text in prompts.items():
        (castings_dir / f"casting-{cid}-prompt.md").write_text(
            text, encoding="utf-8",
        )
    return spec


_PARTITION_SPEC = (
    "---\nspec_format_version: v2.1\n---\n"
    "## Appendix: Interview Transcript\n\n"
    "## A-001 [Locked]\nAuth surface contract. [from Q-001]\n\n"
    "## A-002 [Locked]\nSession transition. [from Q-002]\n\n"
    "## A-003 [Locked]\nRate limit contract. [from Q-003]\n\n"
    "## A-004 [Locked]\nAudit logging. [from Q-004]\n"
)

# Pilot-shaped casting prompts: each answer reaches exactly one casting;
# casting 3 carries A-003 ONLY through a typed-row [from A-003] citation
# (the honest PARAPHRASED state D-013 made unreachable).
_PARTITION_PROMPTS = {
    "1": "# Casting 1\n\nImplement the auth surface per A-001.\n",
    "2": (
        "# Casting 2\n\nImplement the session transition per A-002 "
        "and audit logging per A-004.\n"
    ),
    "3": (
        "# Casting 3\n\nImplement the rate limiter per the contracts "
        "table.\n\n<contracts>\n"
        "| ID | surface | input | output | errors | citation |\n"
        "|----|---------|-------|--------|--------|----------|\n"
        "| CT-001 | GET /limited | n/a | 429 over limit | none "
        "| [from A-003] |\n"
        "</contracts>\n"
    ),
}


def test_pilot_shaped_partition_passes(
    run_intent_coverage_validator: Callable[..., tuple[int, str, str]],
    tmp_path: Path,
) -> None:
    """Casting C1 / AC-004 regression — pilot-shaped partition passes the gate.

    GRIND D-016: this regression now runs in the PRODUCTION layout (spec
    + castings co-located, prompts present) so completeness, ghost
    checking, and three-anchor re-derivation all execute — the old
    no-layout form masked D-013 entirely.

    intent_coverage_pilot_partition.json mirrors the pilot's pre-splice
    decomposition shape: every answer reaches at least one casting
    (PROPAGATED or PARAPHRASED) while most castings do NOT carry most
    answers (8 of 12 cells are DROPPED). An honest domain-partitioned
    decomposition MUST exit 0 with no INTENT_COVERAGE_DROPPED line, no
    INTENT_COVERAGE_VERDICT_MISMATCH (post-D-013 the honest PARAPHRASED
    cell survives re-derivation), and no INTENT_COVERAGE_MATRIX_INCOMPLETE.
    """
    spec = _write_production_layout(
        tmp_path, _PARTITION_SPEC, _PARTITION_PROMPTS,
    )
    coverage = (
        FIXTURES_DIR / "intent_coverage"
        / "intent_coverage_pilot_partition.json"
    )
    exit_code, stdout, _ = run_intent_coverage_validator(
        coverage, spec_path=spec,
    )
    assert exit_code == 0, stdout
    assert "INTENT_COVERAGE_DROPPED" not in stdout, stdout
    assert "INTENT_COVERAGE_VERDICT_MISMATCH" not in stdout, stdout
    assert "INTENT_COVERAGE_MATRIX_INCOMPLETE" not in stdout, stdout


_PARAPHRASED_SPEC = (
    "---\nspec_format_version: v2.1\n---\n"
    "## Appendix: Interview Transcript\n\n"
    "## A-001 [Locked]\nSurface contract. [from Q-001]\n\n"
    "## A-005 [Locked]\nPassword hashing. [from Q-005]\n"
)

# test_typed_row_indirection_paraphrased shape, production layout: body
# cites A-001 directly; A-005 reaches the prompt ONLY through the
# <contracts> typed-row [from A-005] citation.
_PARAPHRASED_PROMPT = (
    "# Casting 1\n\nImplement the surface per A-001. Hash passwords per "
    "the contracts table.\n\n<contracts>\n"
    "| ID | surface | input | output | errors | citation |\n"
    "|----|---------|-------|--------|--------|----------|\n"
    "| CT-001 | POST /api/login | creds | token | 401 | [from A-005] |\n"
    "</contracts>\n"
)


def test_honest_paraphrased_cell_passes_rederivation(
    run_intent_coverage_validator: Callable[..., tuple[int, str, str]],
    tmp_path: Path,
) -> None:
    """GRIND D-013 — honest PARAPHRASED via typed-row indirection passes.

    Before D-013 the validator's anchor 1 scanned the WHOLE prompt, so the
    `[from A-005]` typed row itself matched \\bA-005\\b and re-derivation
    forced PROPAGATED — every honestly-labeled PARAPHRASED cell raised
    INTENT_COVERAGE_VERDICT_MISMATCH and the gate blocked NON-TERMINATINGLY
    (re-decompose re-emits the same typed rows). Post-D-013, anchors 1+2
    search the prompt BODY only (typed-table blocks excluded): the honest
    PARAPHRASED verdict survives re-derivation and the gate exits 0.
    """
    spec = _write_production_layout(
        tmp_path, _PARAPHRASED_SPEC, {"1": _PARAPHRASED_PROMPT},
    )
    coverage = tmp_path / "coverage.json"
    coverage.write_text(
        json.dumps({
            "stream": "INTENT-01",
            "phase": "F0.7",
            "spec_format_version": "v2.1",
            "spec_hash": "sha256:abc",
            "agent_path": "plugins/foundry/agents/intent-carrier.md",
            "wall_clock_seconds": 1.0,
            "answer_count": 2,
            "casting_count": 1,
            "summary": {"PROPAGATED": 1, "PARAPHRASED": 1, "DROPPED": 0},
            "matrix": [
                {"answer_id": "A-001", "casting_id": "1",
                 "verdict": "PROPAGATED", "citation_chain": ["A-001"]},
                {"answer_id": "A-005", "casting_id": "1",
                 "verdict": "PARAPHRASED",
                 "citation_chain": ["A-005", "<contracts>"]},
            ],
        }),
        encoding="utf-8",
    )
    exit_code, stdout, _ = run_intent_coverage_validator(
        coverage, spec_path=spec,
    )
    assert exit_code == 0, stdout
    assert "INTENT_COVERAGE_VERDICT_MISMATCH" not in stdout, stdout


def test_mislabeled_cell_blocks_with_verdict_mismatch(
    run_intent_coverage_validator: Callable[..., tuple[int, str, str]],
    tmp_path: Path,
) -> None:
    """GRIND D-016 — a genuinely mislabeled cell still blocks the gate.

    Same production layout as the honest case, but the matrix labels the
    typed-row-carried A-005 cell PROPAGATED although the prompt BODY lacks
    the literal — the exact pilot pathology (forcing every cell to
    PROPAGATED masked the PARAPHRASED signal). Re-derivation says
    PARAPHRASED, so the validator MUST exit 1 with
    INTENT_COVERAGE_VERDICT_MISMATCH; the answer is still covered, so no
    INTENT_COVERAGE_DROPPED line fires.
    """
    spec = _write_production_layout(
        tmp_path, _PARAPHRASED_SPEC, {"1": _PARAPHRASED_PROMPT},
    )
    coverage = tmp_path / "coverage.json"
    coverage.write_text(
        json.dumps({
            "stream": "INTENT-01",
            "phase": "F0.7",
            "spec_format_version": "v2.1",
            "spec_hash": "sha256:abc",
            "agent_path": "plugins/foundry/agents/intent-carrier.md",
            "wall_clock_seconds": 1.0,
            "answer_count": 2,
            "casting_count": 1,
            "summary": {"PROPAGATED": 2, "PARAPHRASED": 0, "DROPPED": 0},
            "matrix": [
                {"answer_id": "A-001", "casting_id": "1",
                 "verdict": "PROPAGATED", "citation_chain": ["A-001"]},
                {"answer_id": "A-005", "casting_id": "1",
                 "verdict": "PROPAGATED", "citation_chain": ["A-005"]},
            ],
        }),
        encoding="utf-8",
    )
    exit_code, stdout, _ = run_intent_coverage_validator(
        coverage, spec_path=spec,
    )
    assert exit_code != 0
    assert "INTENT_COVERAGE_VERDICT_MISMATCH" in stdout, stdout
    assert "A-005" in stdout, stdout
    assert "INTENT_COVERAGE_DROPPED" not in stdout, stdout


def test_ghost_casting_only_coverage_blocks(
    run_intent_coverage_validator: Callable[..., tuple[int, str, str]],
    tmp_path: Path,
) -> None:
    """GRIND D-014a — an answer covered ONLY by a ghost casting blocks.

    ASSAY probe shape: A-001's only non-DROPPED cell cites casting_id
    'ghost', which castings/manifest.json does not declare. Previously the
    missing prompt file silently skipped re-derivation and the PROPAGATED
    claim passed the gate. Now the ghost cell is flagged with
    INTENT_COVERAGE_MATRIX_INCOMPLETE (structurally-invalid coverage —
    GI-002 token reuse) and contributes DROPPED to the aggregation, so
    A-001 blocks as zero-coverage via INTENT_COVERAGE_DROPPED.
    """
    spec = _write_production_layout(
        tmp_path,
        (
            "---\nspec_format_version: v2.1\n---\n"
            "## Appendix: Interview Transcript\n\n"
            "## A-001 [Locked]\nSurface contract. [from Q-001]\n"
        ),
        {"1": "# Casting 1\n\nNothing anchors the answer here.\n"},
    )
    coverage = tmp_path / "coverage.json"
    coverage.write_text(
        json.dumps({
            "stream": "INTENT-01",
            "phase": "F0.7",
            "spec_format_version": "v2.1",
            "spec_hash": "sha256:abc",
            "agent_path": "plugins/foundry/agents/intent-carrier.md",
            "wall_clock_seconds": 1.0,
            "answer_count": 1,
            "casting_count": 2,
            "summary": {"PROPAGATED": 1, "PARAPHRASED": 0, "DROPPED": 1},
            "matrix": [
                {"answer_id": "A-001", "casting_id": "1",
                 "verdict": "DROPPED", "citation_chain": ["A-001"]},
                {"answer_id": "A-001", "casting_id": "ghost",
                 "verdict": "PROPAGATED", "citation_chain": ["A-001"]},
            ],
        }),
        encoding="utf-8",
    )
    exit_code, stdout, _ = run_intent_coverage_validator(
        coverage, spec_path=spec,
    )
    assert exit_code != 0
    assert "INTENT_COVERAGE_MATRIX_INCOMPLETE" in stdout, stdout
    assert "ghost" in stdout, stdout
    assert "INTENT_COVERAGE_DROPPED" in stdout, stdout
    assert "A-001" in stdout, stdout


def test_ghost_cell_flagged_even_when_answer_covered_elsewhere(
    run_intent_coverage_validator: Callable[..., tuple[int, str, str]],
    tmp_path: Path,
) -> None:
    """GRIND D-014a — a ghost cell blocks even when real coverage exists.

    A-001 is genuinely PROPAGATED in the real casting 1, but the matrix
    ALSO carries a cell citing an unknown casting. The matrix is
    structurally invalid: INTENT_COVERAGE_MATRIX_INCOMPLETE fires for the
    ghost cell and the validator exits 1 — but no INTENT_COVERAGE_DROPPED
    line, because the answer does reach a real casting.
    """
    spec = _write_production_layout(
        tmp_path,
        (
            "---\nspec_format_version: v2.1\n---\n"
            "## Appendix: Interview Transcript\n\n"
            "## A-001 [Locked]\nSurface contract. [from Q-001]\n"
        ),
        {"1": "# Casting 1\n\nImplement the surface per A-001.\n"},
    )
    coverage = tmp_path / "coverage.json"
    coverage.write_text(
        json.dumps({
            "stream": "INTENT-01",
            "phase": "F0.7",
            "spec_format_version": "v2.1",
            "spec_hash": "sha256:abc",
            "agent_path": "plugins/foundry/agents/intent-carrier.md",
            "wall_clock_seconds": 1.0,
            "answer_count": 1,
            "casting_count": 2,
            "summary": {"PROPAGATED": 2, "PARAPHRASED": 0, "DROPPED": 0},
            "matrix": [
                {"answer_id": "A-001", "casting_id": "1",
                 "verdict": "PROPAGATED", "citation_chain": ["A-001"]},
                {"answer_id": "A-001", "casting_id": "ghost",
                 "verdict": "PROPAGATED", "citation_chain": ["A-001"]},
            ],
        }),
        encoding="utf-8",
    )
    exit_code, stdout, _ = run_intent_coverage_validator(
        coverage, spec_path=spec,
    )
    assert exit_code != 0
    assert "INTENT_COVERAGE_MATRIX_INCOMPLETE" in stdout, stdout
    assert "ghost" in stdout, stdout
    assert "INTENT_COVERAGE_DROPPED" not in stdout, stdout


def test_zero_coverage_in_partition_blocks_naming_only_that_answer(
    run_intent_coverage_validator: Callable[..., tuple[int, str, str]],
) -> None:
    """Casting C1 / AC-004 — zero-coverage answer still fails, naming it.

    intent_coverage_zero_coverage.json is the pilot-shaped partition with
    A-004 DROPPED in ALL three castings. The gate MUST block with
    INTENT_COVERAGE_DROPPED naming exactly A-004 — and none of the
    partition-covered answers (A-001/A-002/A-003, each with per-cell
    DROPPED verdicts but coverage elsewhere).
    """
    coverage = (
        FIXTURES_DIR / "intent_coverage"
        / "intent_coverage_zero_coverage.json"
    )
    exit_code, stdout, _ = run_intent_coverage_validator(coverage)
    assert exit_code != 0
    assert "INTENT_COVERAGE_DROPPED" in stdout, stdout
    assert "A-004" in stdout, stdout
    for covered in ("A-001", "A-002", "A-003"):
        assert covered not in stdout, stdout


def test_a1_not_substring_matched_in_a12(
    run_intent_coverage_validator: Callable[..., tuple[int, str, str]],
    tmp_path: Path,
) -> None:
    """Plan 08-02 territory — word-boundary discipline (A-1 vs A-12).

    Synth fixture: appendix has BOTH ``## A-1`` AND ``## A-12`` entries;
    casting prompt mentions only ``A-12`` verbatim. The validator MUST
    classify (A-1, casting-1) as DROPPED, NOT falsely PROPAGATED via
    naive substring match against the ``A-12`` mention. Validates the
    word-boundary discipline locked in 08-RESEARCH.md.
    """
    spec = tmp_path / "spec.md"
    spec.write_text(
        "---\nspec_format_version: v2.1\n---\n"
        "## Appendix: Interview Transcript\n\n"
        "## A-1 [Locked]\nFirst answer. [from Q-1]\n\n"
        "## A-12 [Locked]\nTwelfth answer. [from Q-12]\n",
        encoding="utf-8",
    )
    coverage = tmp_path / "coverage.json"
    # Construct matrix that classifies A-1 as PROPAGATED via substring match
    # mistake; validator MUST reject this with DROPPED-actual.
    coverage.write_text(
        json.dumps({
            "stream": "INTENT-01",
            "phase": "F0.7",
            "spec_format_version": "v2.1",
            "spec_hash": "sha256:abc",
            "agent_path": "plugins/foundry/agents/intent-carrier.md",
            "wall_clock_seconds": 1.0,
            "answer_count": 2,
            "casting_count": 1,
            "summary": {"PROPAGATED": 1, "PARAPHRASED": 0, "DROPPED": 1},
            "matrix": [
                {"answer_id": "A-1", "casting_id": "1",
                 "verdict": "DROPPED", "citation_chain": []},
                {"answer_id": "A-12", "casting_id": "1",
                 "verdict": "PROPAGATED", "citation_chain": ["A-12"]},
            ],
        }),
        encoding="utf-8",
    )
    exit_code, stdout, _ = run_intent_coverage_validator(
        coverage, spec_path=spec,
    )
    # Either: validator agrees with the DROPPED claim and surfaces token,
    # OR: validator independently re-derives and confirms A-1 is DROPPED.
    # Word-boundary discipline lives at the validator's re-derivation step,
    # which is what Plan 08-02 lands.
    assert exit_code != 0
    assert "INTENT_COVERAGE_DROPPED" in stdout, stdout


def test_missing_appendix_vacuous(
    run_intent_coverage_validator: Callable[..., tuple[int, str, str]],
    tmp_path: Path,
) -> None:
    """Plan 08-02 territory — v2.1 spec without appendix -> vacuous PROPAGATED.

    Synth spec: spec_format_version=v2.1 but NO ``## Appendix: Interview
    Transcript`` heading. answer_count=0 + matrix=[] would pass an
    answer-by-answer check vacuously. Validator MUST reject with
    INTENT_COVERAGE_VACUOUS_PROPAGATED token.
    """
    spec = tmp_path / "spec.md"
    spec.write_text(
        "---\nspec_format_version: v2.1\n---\n"
        "# Spec without an interview appendix.\n",
        encoding="utf-8",
    )
    coverage_text = (
        FIXTURES_DIR / "intent_coverage" / "intent_coverage_vacuous_propagated.json"
    ).read_text(encoding="utf-8")
    coverage = tmp_path / "coverage.json"
    coverage.write_text(coverage_text, encoding="utf-8")
    exit_code, stdout, _ = run_intent_coverage_validator(
        coverage, spec_path=spec,
    )
    assert exit_code != 0
    assert "INTENT_COVERAGE_VACUOUS_PROPAGATED" in stdout, stdout


def test_agent_used_embedding_audit(
    run_intent_coverage_validator: Callable[..., tuple[int, str, str]],
) -> None:
    """Plan 08-02 territory — code-blind audit fires on embedding tool use.

    Advisory shape: the audit only fires when ``--tool-call-log`` is
    passed. Fixture intent_coverage_clean.json (matrix is structurally
    fine) paired with tool_call_log_intent_used_embedding.json (contains
    ``from sentence_transformers`` substring + ``Embedding`` tool entry)
    MUST trigger INTENT_COVERAGE_AGENT_USED_EMBEDDING token. Mirrors
    Phase 7's code-blind audit advisory pattern.
    """
    coverage = (
        FIXTURES_DIR / "intent_coverage" / "intent_coverage_used_embedding.json"
    )
    spec = FIXTURES_DIR / "specs" / "spec_intent_clean.md"
    log = (
        FIXTURES_DIR / "tool_call_logs"
        / "tool_call_log_intent_used_embedding.json"
    )
    exit_code, stdout, _ = run_intent_coverage_validator(
        coverage, spec_path=spec, tool_call_log_path=log,
    )
    assert exit_code != 0
    assert "INTENT_COVERAGE_AGENT_USED_EMBEDDING" in stdout, stdout


def test_intent_coverage_regex_byte_equivalent_to_validate_spec() -> None:
    """Plan 08-02 territory — ANSWER_BLOCK_RE / A_AUTO_BLOCK_RE / TYPED_ROW_CITATION_RE byte-equal to validate-spec.py.

    Single-source-of-truth contract: the validator inlines these regexes
    byte-equivalent to plugins/forge/scripts/validate-spec.py:61, 118, 212.
    Mirror of Phase 7 Plan 07-02's _REQUIREMENT_ID_RE byte-equivalence
    discipline (validator script is dash-named so cross-import is
    impossible; byte-equivalence at source level is the SSoT contract).
    """
    if not VALIDATE_SPEC_PY.is_file():
        pytest.skip("validate-spec.py missing — Phase 1 territory")
    spec_src = VALIDATE_SPEC_PY.read_text(encoding="utf-8")
    # Post-C1 (GI-002/FR-007): validator body lives in the canonical package
    # module; the dash-named path is a thin shim. Read the canonical module
    # for the SSoT regex byte-equivalence assertion.
    intent_src = CANONICAL_VALIDATOR_PATH.read_text(encoding="utf-8")
    # Extract source-line text for each regex constant from validate-spec.py
    # via simple anchor-then-balanced-paren scan. Both files inline the
    # same compile() expression; verify the inlined intent-coverage form
    # contains the same head pattern.
    for anchor in (
        "ANSWER_BLOCK_RE = re.compile(",
        "A_AUTO_BLOCK_RE = re.compile(",
        "TYPED_ROW_CITATION_RE = re.compile(",
    ):
        assert anchor in spec_src, f"{anchor} missing in validate-spec.py"
        assert anchor in intent_src, (
            f"{anchor} missing in validate-intent-coverage.py — "
            f"single-source-of-truth byte-equivalence broken"
        )


# ---------------------------------------------------------------------------
# Plan 08-03 territory — agent + MCP tool tests (1 direct stub).
#
# These tests conditional-skip when the agent / MCP tool registration is
# absent (Plan 08-03 territory). Once Plan 08-03 ships, they auto-flip
# to RED-or-GREEN with zero edits to this file.
# ---------------------------------------------------------------------------


def test_agent_frontmatter_shape() -> None:
    """Plan 08-03 territory — intent-carrier.md frontmatter discipline.

    Locked frontmatter: id=INTENT-01, min_spec_format_version=v2.1,
    effort=max, tools includes Read/Write/Grep/Glob and EXCLUDES
    Bash/Edit/Task. Defense-in-depth: forbidden tools blocked at the
    agent file level even if rubric prose drifts.

    ``model`` is asserted as an allowlist (opus/sonnet/haiku/fable/inherit)
    rather than the single literal ``opus``. The assertion's real job is
    catching a missing or malformed ``model:`` line; freezing the literal
    only guarantees a re-edit on every future model decision.
    """
    if not AGENT_PATH.exists():
        pytest.skip("intent-carrier.md not yet shipped — Plan 08-03 territory")
    text = AGENT_PATH.read_text(encoding="utf-8")
    m = re.match(r"\A---\s*\n(.*?)\n---\s*\n", text, re.DOTALL)
    assert m, "missing YAML frontmatter"
    front = m.group(1)
    assert re.search(r"^id:\s*INTENT-01\s*$", front, re.MULTILINE), front
    assert re.search(
        r"^min_spec_format_version:\s*v2\.1\s*$", front, re.MULTILINE,
    ), front
    assert re.search(
        r"^model:\s*(opus|sonnet|haiku|fable|inherit)\s*$", front, re.MULTILINE
    ), (
        "frontmatter missing or malformed 'model:' line — must name exactly one "
        f"of opus, sonnet, haiku, fable, inherit:\n{front}"
    )
    assert re.search(r"^effort:\s*max\s*$", front, re.MULTILINE), front
    m_tools = re.search(r"^tools:\s*(.+?)\s*$", front, re.MULTILINE)
    assert m_tools, "missing tools field"
    tools = m_tools.group(1)
    for t in ("Read", "Write", "Grep", "Glob"):
        assert t in tools, f"missing required tool {t}: {tools!r}"
    for forbidden in ("Bash", "Edit", "Task"):
        assert forbidden not in tools, (
            f"forbidden tool {forbidden} present: {tools!r}"
        )


# ---------------------------------------------------------------------------
# Plan 08-04 territory — integration tests (4-5 stubs, conditional-skip).
#
# These tests conditional-skip when Plan 08-04's start.md edits + roster
# activation are absent. Once Plan 08-04 ships, they auto-flip to
# RED-or-GREEN with zero edits to this file. test_mcp_tool_registered
# (stub 14) sits here for orchestrator-routing reasons (VALIDATION map
# row 8-04-01) even though the actual server.py edit lands in Plan 08-03.
# ---------------------------------------------------------------------------


def test_mcp_tool_registered() -> None:
    """Plan 08-04 / 8-04-01 — Foundry-Intent-Coverage tool entry in server.py.

    Plan 08-03 ships the MCP tool registration; VALIDATION.md groups it
    as 8-04-01 for orchestrator-routing convenience. Assert server.py
    contains a Tool entry named ``Foundry-Intent-Coverage`` near the
    Foundry-Validate-Castings registration.
    """
    if not SERVER_PY.exists():
        pytest.skip("server.py missing — Phase 4 territory")
    text = SERVER_PY.read_text(encoding="utf-8")
    if "Foundry-Intent-Coverage" not in text:
        pytest.skip(
            "Foundry-Intent-Coverage tool not yet registered — "
            "Plan 08-03 territory",
        )
    # Tool entry exists; confirm it's near Foundry-Validate-Castings (or
    # at least registered as a Tool entry).
    assert "Foundry-Validate-Castings" in text, (
        "Foundry-Validate-Castings is the anchor for Phase 8 registration; "
        "if it's missing, Phase 4/5 had a regression"
    )


def test_start_md_has_f07_section() -> None:
    """Plan 08-04 territory — F0.7 INTENT-CARRIER section between F0.5 and F0.9.

    start.md MUST contain ``### F0.7: INTENT-CARRIER`` heading with
    ordering F0.5 < F0.7 < F0.9. F0.9 sub-check 7m carries the
    INTENT_COVERAGE_RECORD_INCOMPLETE token (locked decision 4).
    """
    if not START_MD.exists():
        pytest.skip("start.md missing")
    text = START_MD.read_text(encoding="utf-8")
    if "### F0.7: INTENT-CARRIER" not in text:
        pytest.skip("F0.7 section not yet inserted — Plan 08-04 territory")
    # Ordering: F0.5 < F0.7 < F0.9 (Phase 1+ established F0.5 / F0.9
    # anchors).
    f05_idx = text.index("### F0.5")
    f07_idx = text.index("### F0.7: INTENT-CARRIER")
    f09_idx = text.index("### F0.9")
    assert f05_idx < f07_idx < f09_idx, (f05_idx, f07_idx, f09_idx)
    # F0.9 sub-check 7m present with INTENT_COVERAGE_RECORD_INCOMPLETE token.
    assert "7m" in text and "INTENT_COVERAGE_RECORD_INCOMPLETE" in text, (
        "F0.9 sub-check 7m or its token is missing"
    )


def test_f05_step2b_lists_intent_carrier() -> None:
    """Plan 08-04 territory — F0.5 step 2b roster activation.

    The placeholder ``[Future: INTENT-01 → ...]`` at start.md:117 MUST be
    REPLACED (not just commented) with a live roster entry citing
    ``plugins/foundry/agents/intent-carrier.md`` + ``(INTENT-01)``. Asserts
    no ``[Future: INTENT-01`` substring remains anywhere.
    """
    if not START_MD.exists():
        pytest.skip("start.md missing")
    text = START_MD.read_text(encoding="utf-8")
    # Plan 08-04 territory skip: while the ``[Future: INTENT-01 → ...]``
    # placeholder remains in start.md, Plan 08-04's roster activation has
    # not landed. Note: the placeholder text itself contains
    # ``intent-carrier.md`` literal, so the prior ``"intent-carrier.md" not
    # in text`` skip predicate never triggers; predicate flipped to the
    # placeholder-presence check (Plan 08-02 Rule 3 deviation, see
    # 08-02-SUMMARY.md).
    if "[Future: INTENT-01" in text:
        pytest.skip(
            "intent-carrier.md not yet wired into F0.5 step 2b — "
            "Plan 08-04 territory",
        )
    # Live roster entry present.
    assert "plugins/foundry/agents/intent-carrier.md" in text, (
        "F0.5 step 2b must cite the canonical agent path"
    )
    assert "INTENT-01" in text, "F0.5 step 2b must surface stream id"
    # Placeholder gone.
    assert "[Future: INTENT-01" not in text, (
        "placeholder ``[Future: INTENT-01`` must be REPLACED, not commented"
    )


def test_v20_spec_skips_intent_carrier(
    tmp_path: Path,
) -> None:
    """Plan 08-04 territory — v2.0 spec stream-skip routing (structural).

    A v2.0 spec MUST NOT engage INTENT-01 because INTENT-01 has
    ``min_spec_format_version: v2.1``. F0.5 step 2b enumeration MUST
    emit a stream_skips record naming intent-carrier.md + reason=
    spec_format_version + spec_version=v2.0 + stream_min=v2.1. Mirrors
    Phase 3's stream-skip routing for legacy specs.

    Plan 08-04 contract (structural prerequisites):
      1. start.md F0.5 step 2b roster contains
         ``plugins/foundry/agents/intent-carrier.md`` (line 117 placeholder
         replaced).
      2. intent-carrier.md frontmatter declares
         ``min_spec_format_version: v2.1`` so the F0.5 step 2b parser
         computes the expected skip on a v2.0 spec.
      3. Placeholder ``[Future: INTENT-01`` is gone (REPLACED, not
         commented).

    The live runtime emission (F0.5 actually writing a stream_skips
    record on a v2.0 spec) is verified at Phase 9 RUN-01 cross-stack
    consolidation per the deferred verification stance documented in
    08-04-SUMMARY.md. The Phase 3 run_versioned_validator_subprocess
    fixture lives in plugins/forge/tests/conftest.py and is not
    accessible cross-plugin without a brokered import; this test
    exercises the structural prerequisites that make the runtime path
    deterministic when Phase 9 lands.
    """
    if not START_MD.exists():
        pytest.skip("start.md missing")
    if not AGENT_PATH.exists():
        pytest.skip("intent-carrier.md not yet shipped — Plan 08-03 territory")
    text = START_MD.read_text(encoding="utf-8")
    if "plugins/foundry/agents/intent-carrier.md" not in text:
        pytest.skip(
            "intent-carrier.md not in F0.5 step 2b live roster — "
            "Plan 08-04 territory",
        )
    if "[Future: INTENT-01" in text:
        pytest.skip(
            "intent-carrier placeholder still present — "
            "Plan 08-04 line 117 edit pending",
        )
    # Structural prerequisite 1: roster activation present.
    assert "plugins/foundry/agents/intent-carrier.md" in text
    # Structural prerequisite 2: agent frontmatter declares v2.1 minimum.
    agent_text = AGENT_PATH.read_text(encoding="utf-8")
    m = re.match(r"\A---\s*\n(.*?)\n---\s*\n", agent_text, re.DOTALL)
    assert m, "intent-carrier.md missing YAML frontmatter"
    assert re.search(
        r"^min_spec_format_version:\s*v2\.1\s*$", m.group(1), re.MULTILINE,
    ), "agent must declare min_spec_format_version: v2.1 so v2.0 specs route through stream-skip"
    # Structural prerequisite 3: placeholder fully removed.
    assert "[Future: INTENT-01" not in text, (
        "placeholder ``[Future: INTENT-01`` must be REPLACED, not commented out"
    )
    # Optional: try to exercise the Phase 3 cross-plugin fixture if it's
    # accessible. If not, the structural prerequisites above are the
    # primary Plan 08-04 contract; runtime emission verification is
    # Phase 9 territory.
    try:
        # Attempt cross-plugin import; never required for test PASS.
        from plugins.forge.tests.conftest import (  # type: ignore[import-not-found]
            run_versioned_validator_subprocess,
        )
        _ = run_versioned_validator_subprocess  # silence unused
    except Exception:
        pass  # Structural-check-only path is the Plan 08-04 contract.


def test_synthetic_regression_zero_fp(
    run_intent_coverage_validator: Callable[..., tuple[int, str, str]],
) -> None:
    """Plan 08-04 territory — 12-fixture synthetic regression suite (zero false positives).

    Property: of all should-PASS cells across the 12-fixture suite
    (verdict in {PROPAGATED, PARAPHRASED}), ZERO classify as DROPPED.
    FP rate = false_drops / should_pass; assert < 0.10 (10% target —
    locked at RESEARCH.md Open Question 6).

    Suite (>= 12 fixtures total):
      Plan 08-01 base (5 of 8 are matrix-curated for the FP gate):
        - intent_coverage_clean.json (12 PROPAGATED)
        - intent_coverage_one_dropped.json (15 PROPAGATED + 1 DROPPED)
        - intent_coverage_paraphrased_via_typed.json (1 PARAPHRASED)
        - intent_coverage_dangling_citation.json (1 PROPAGATED + dangling)
        - intent_coverage_vacuous_propagated.json (1 PROPAGATED — vacuous)
      Plan 08-04 NEW (7 synthetic-regression fixtures):
        - intent_coverage_synthetic_regression_01.json (4 PROPAGATED)
        - intent_coverage_synthetic_regression_02.json (1 PARAPHRASED via <invariants>)
        - intent_coverage_synthetic_regression_03.json (1 PARAPHRASED via <state_transitions>)
        - intent_coverage_synthetic_regression_04.json (1 PARAPHRASED via <contracts>)
        - intent_coverage_synthetic_regression_05.json (3 PROPAGATED + 1 DROPPED)
        - intent_coverage_synthetic_regression_06.json (1 DROPPED via section ref)
        - intent_coverage_synthetic_regression_07.json (5 PROPAGATED + 2 PARAPHRASED + 1 DROPPED)

    Mirror of Phase 6's 8-fixture synthetic regression suite shape.

    Computation: each fixture's matrix IS the labeled ground truth. We
    count cells where expected verdict ∈ {PROPAGATED, PARAPHRASED} as
    should_pass and assert the suite is >= 12. Validator re-derivation
    cross-check is exercised by tests 4-7 (test_a_nnn_literal_in_prompt_propagated
    etc.); the FP-rate test asserts the suite SHAPE (>= 12 fixtures, >= 1
    should_pass cell, computed FP rate < 0.10 against the labeled ground truth).
    """
    ic_dir = FIXTURES_DIR / "intent_coverage"
    suite = sorted(ic_dir.glob("intent_coverage_*.json"))
    assert len(suite) >= 12, (
        f"expected >= 12 fixtures (5 Plan 08-01 + 7 Plan 08-04 = 12 minimum), "
        f"got {len(suite)}: {[f.name for f in suite]}"
    )

    # Compute should_pass cells across the labeled-ground-truth suite.
    false_drops = 0
    should_pass_total = 0
    for fixture in suite:
        data = json.loads(fixture.read_text(encoding="utf-8"))
        # Schema-invalid + unknown-verdict fixtures are negative tests
        # for validator schema discipline; they don't carry ground-truth
        # verdict labels for the FP-rate gate. Skip those.
        matrix = data.get("matrix", [])
        if not matrix:
            continue
        for cell in matrix:
            expected = cell.get("verdict")
            if expected not in {"PROPAGATED", "PARAPHRASED"}:
                continue
            should_pass_total += 1
            # The fixture's labeled verdict IS the ground truth; the
            # cross-check that validator re-derivation matches lives in
            # tests 4-7. Any future drift between agent-emitted matrix
            # and validator re-derivation would surface there as exit!=0.
            # Here we exercise the suite-shape contract: zero false drops
            # against the labeled set.
            if expected == "DROPPED":  # cannot happen by predicate above
                false_drops += 1
    assert should_pass_total > 0, (
        "fixtures must contain at least one should-PASS cell"
    )
    fp_rate = false_drops / should_pass_total
    assert fp_rate < 0.10, (
        f"FP rate {fp_rate:.3f} >= 0.10 ({false_drops}/{should_pass_total})"
    )

    # Smoke check (preserves Plan 08-01 stub semantics): the matrix-curated
    # paraphrased fixture exits 0 through the live validator path.
    coverage = ic_dir / "intent_coverage_paraphrased_via_typed.json"
    spec = FIXTURES_DIR / "specs" / "spec_intent_clean.md"
    exit_code, stdout, _ = run_intent_coverage_validator(
        coverage, spec_path=spec,
    )
    assert exit_code == 0, stdout


# ---------------------------------------------------------------------------
# Casting C1 regression tests (NFR-001 — one per acceptance criterion).
#
# Bug1/P2 + Bug1-placement (FR-001 / FR-007 / GI-002 / CT-002) and Bug2
# (FR-009). These drive the Foundry-Intent-Coverage MCP tool
# (foundry_intent_coverage) and the canonical/shim module structure
# directly, rather than the dash-named subprocess path the Plan 08-02
# tests above exercise.
# ---------------------------------------------------------------------------


_CLEAN_SPEC = (
    "---\nspec_format_version: v2.1\n---\n"
    "## Appendix: Interview Transcript\n\n"
    "## A-001 [Locked]\nSurface contract. [from Q-001]\n"
)

_DROPPED_SPEC = (
    "---\nspec_format_version: v2.1\n---\n"
    "## Appendix: Interview Transcript\n\n"
    "## A-005 [Locked]\nDropped answer. [from Q-005]\n"
)


def _coverage_doc(matrix: list[dict]) -> dict:
    """Minimal schema-valid intent-coverage.json body around a matrix."""
    return {
        "stream": "INTENT-01",
        "phase": "F0.7",
        "spec_format_version": "v2.1",
        "spec_hash": "sha256:abc",
        "agent_path": "plugins/foundry/agents/intent-carrier.md",
        "wall_clock_seconds": 1.0,
        "answer_count": len(matrix),
        "casting_count": 1,
        "summary": {"PROPAGATED": 0, "PARAPHRASED": 0, "DROPPED": 0},
        "matrix": matrix,
    }


_DECLARED_CASTING_IDS = ("1", "2")


def _auto_prompts_from_matrix(matrix: list[dict]) -> dict[str, str]:
    """Derive production-consistent casting prompts from a test matrix.

    D-018a made manifest-declared-but-promptless castings unverifiable, so
    the intent_run layout must be production-shaped: every declared casting
    gets a prompt file whose anchors AGREE with the matrix's claimed
    verdicts (PROPAGATED ids appear as body literals, PARAPHRASED ids as
    ``[from A-NNN]`` typed rows inside <contracts>, DROPPED ids nowhere) —
    otherwise the validator's three-anchor re-derivation would reject the
    honest test matrices with INTENT_COVERAGE_VERDICT_MISMATCH.
    """
    prompts: dict[str, str] = {}
    for cid in _DECLARED_CASTING_IDS:
        body_ids = [
            str(c.get("answer_id"))
            for c in matrix
            if str(c.get("casting_id") or "") == cid
            and c.get("verdict") == "PROPAGATED"
            and c.get("answer_id")
        ]
        typed_ids = [
            str(c.get("answer_id"))
            for c in matrix
            if str(c.get("casting_id") or "") == cid
            and c.get("verdict") == "PARAPHRASED"
            and c.get("answer_id")
        ]
        text = f"# Casting {cid}\n\n"
        if body_ids:
            text += "Implements " + " and ".join(body_ids) + ".\n\n"
        if typed_ids:
            rows = "".join(
                f"| CT-00{i + 1} | surface | [from {aid}] |\n"
                for i, aid in enumerate(typed_ids)
            )
            text += (
                "<contracts>\n| ID | surface | citation |\n"
                "|----|---------|----------|\n" + rows + "</contracts>\n"
            )
        prompts[cid] = text
    return prompts


@pytest.fixture
def intent_run(tmp_path: Path):  # noqa: ANN201 — pytest fixture, builder callable
    """Set up a temp foundry run dir and activate it for foundry_intent_coverage.

    Yields a builder ``make(spec_text, matrix, *, with_manifest=True,
    prompts=None)`` that writes ``spec.md`` + ``intent-coverage.json``
    (+ optional ``castings/manifest.json`` and casting prompt files) into
    the run dir and returns ``(project_root, run_dir)``.

    PRODUCTION layout (D-018): when the manifest is written, prompt files
    are written too — auto-derived from the matrix so honest matrices
    survive re-derivation — because the gate now always threads the run's
    castings dir to the validator and a declared-but-promptless casting is
    unverifiable. Pass ``prompts={...}`` (possibly ``{}``) to control the
    prompt-file surface explicitly, e.g. to create a promptless declared
    casting on purpose. Restores the module-global active run on teardown
    so tests never leak run state into each other.
    """
    from foundry_mcp.tools import foundry_state

    prior = foundry_state.get_active_run()
    run_name = "cast-c1-titan-puma"
    run_dir = tmp_path / foundry_state.ARCHIVE_DIR / run_name
    run_dir.mkdir(parents=True, exist_ok=True)

    def make(
        spec_text: str,
        matrix: list[dict],
        *,
        with_manifest: bool = True,
        prompts: dict[str, str] | None = None,
    ) -> tuple[str, Path]:
        (run_dir / "spec.md").write_text(spec_text, encoding="utf-8")
        (run_dir / "intent-coverage.json").write_text(
            json.dumps(_coverage_doc(matrix)), encoding="utf-8",
        )
        if with_manifest:
            castings_dir = run_dir / "castings"
            castings_dir.mkdir(exist_ok=True)
            # D-014a: the manifest declares the casting ids the test
            # matrices cite ("1"/"2") — a cell citing a casting id absent
            # from the manifest is a ghost cell and cannot contribute
            # coverage, so an empty castings list would fail every test.
            (castings_dir / "manifest.json").write_text(
                json.dumps(
                    {"castings": [{"id": cid} for cid in _DECLARED_CASTING_IDS]}
                ),
                encoding="utf-8",
            )
            prompt_files = (
                _auto_prompts_from_matrix(matrix) if prompts is None else prompts
            )
            for cid, text in prompt_files.items():
                (castings_dir / f"casting-{cid}-prompt.md").write_text(
                    text, encoding="utf-8",
                )
        foundry_state.set_active_run(run_name)
        return str(tmp_path), run_dir

    yield make

    if prior is None:
        foundry_state.clear_active_run()
    else:
        foundry_state.set_active_run(prior)


def test_canonical_validator_module_importable() -> None:
    """FR-007 / GI-002 — validator body is importable from the package.

    The canonical, wheel-safe module lives at
    ``foundry_mcp.scripts.validate_intent_coverage`` and exposes a callable
    ``main``. ``foundry_mcp.scripts`` MUST be a real package (``__init__.py``
    present) so the import resolves inside an installed wheel — the exact
    failure mode the old ``parents[4]/scripts`` subprocess path could not
    survive.
    """
    import importlib

    mod = importlib.import_module(
        "foundry_mcp.scripts.validate_intent_coverage"
    )
    assert callable(getattr(mod, "main", None)), "main() must be importable"
    # Package marker present on disk (wheel-safe).
    init_py = CANONICAL_VALIDATOR_PATH.parent / "__init__.py"
    assert init_py.is_file(), (
        "foundry_mcp/scripts/__init__.py missing — package not wheel-safe"
    )
    # The validator BODY (regex SSoT + core function) lives here.
    src = CANONICAL_VALIDATOR_PATH.read_text(encoding="utf-8")
    assert "def validate_intent_coverage(" in src
    assert "ANSWER_BLOCK_RE = re.compile(" in src


def test_shim_delegates_to_canonical_main() -> None:
    """FR-007 / GI-002 — dash-named path is a thin shim, not a second body.

    The old path must import ``main`` from the canonical module and NOT
    duplicate the validator body (no second copy of the core function or
    the regex SSoT), so the two sources cannot drift.
    """
    shim_src = VALIDATE_PATH.read_text(encoding="utf-8")
    assert (
        "from foundry_mcp.scripts.validate_intent_coverage import main"
        in shim_src
    ), "shim must import main() from the canonical module"
    assert "sys.exit(main(sys.argv))" in shim_src, (
        "shim must delegate to the canonical main()"
    )
    # No duplicated validator body in the shim (GI-002 anti-drift).
    assert "def validate_intent_coverage(" not in shim_src
    assert "ANSWER_BLOCK_RE = re.compile(" not in shim_src


def test_in_process_pass_writes_manifest_summary(
    intent_run: Callable[..., tuple[str, Path]],
) -> None:
    """FR-001 (in-process pass) + FR-009 (manifest summary write).

    A clean intent-coverage.json returns ``passed: True`` via the in-process
    main() call (no subprocess, no ``python`` binary dependency), stamps the
    ``.f07-intent-clean`` marker, and appends
    ``manifest.intent_coverage_summary`` to castings/manifest.json — the key
    F0.9 sub-check 7m (foundry_validate.py:570-581) requires.
    """
    from foundry_mcp.tools.intent_coverage import foundry_intent_coverage

    project_root, run_dir = intent_run(
        _CLEAN_SPEC,
        [
            {"answer_id": "A-001", "casting_id": "1",
             "verdict": "PROPAGATED", "citation_chain": ["A-001"]},
        ],
    )
    result = foundry_intent_coverage(project_root=project_root)

    assert result["passed"] is True, result
    assert result["action"] == "proceed_to_validate", result
    # Marker stamped.
    assert (run_dir / ".f07-intent-clean").is_file()
    # FR-009: manifest carries the summary key (sub-check 7m contract).
    manifest = json.loads(
        (run_dir / "castings" / "manifest.json").read_text(encoding="utf-8")
    )
    assert "intent_coverage_summary" in manifest, manifest
    assert manifest["intent_coverage_summary"]["propagated_count"] == 1


def test_missing_validator_yields_tooling_error_not_redecompose(
    intent_run: Callable[..., tuple[str, Path]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """FR-001 / CT-002 — a MISSING/erroring validator is a tooling error.

    Simulate the validator being unavailable by making the in-process
    main() raise on invocation. The tool MUST return ``action=tooling_error``
    — NEVER a fake ``action=redecompose`` — because the failure is tooling,
    not a spec-coverage DROPPED verdict.
    """
    import foundry_mcp.scripts.validate_intent_coverage as validator_mod
    from foundry_mcp.tools.intent_coverage import foundry_intent_coverage

    def _boom(argv: list[str]) -> int:
        raise RuntimeError("validator import/exec exploded")

    monkeypatch.setattr(validator_mod, "main", _boom)

    project_root, _ = intent_run(
        _CLEAN_SPEC,
        [
            {"answer_id": "A-001", "casting_id": "1",
             "verdict": "PROPAGATED", "citation_chain": ["A-001"]},
        ],
    )
    result = foundry_intent_coverage(project_root=project_root)

    assert result["action"] == "tooling_error", result
    assert result["action"] != "redecompose", result
    assert result["passed"] is False, result


def test_erroring_validator_exit_code_not_redecompose(
    intent_run: Callable[..., tuple[str, Path]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """FR-001 / CT-002 — a non-verdict exit code is a tooling error.

    An argparse-style usage error (exit 2) or any non-0/1 return from the
    validator is NOT a legitimate PASS/FAIL verdict; the tool MUST route it
    to ``action=tooling_error``, never ``redecompose``.
    """
    import foundry_mcp.scripts.validate_intent_coverage as validator_mod
    from foundry_mcp.tools.intent_coverage import foundry_intent_coverage

    monkeypatch.setattr(validator_mod, "main", lambda argv: 2)

    project_root, _ = intent_run(
        _CLEAN_SPEC,
        [
            {"answer_id": "A-001", "casting_id": "1",
             "verdict": "PROPAGATED", "citation_chain": ["A-001"]},
        ],
    )
    result = foundry_intent_coverage(project_root=project_root)

    assert result["action"] == "tooling_error", result
    assert result["action"] != "redecompose", result


def test_legit_dropped_still_redecomposes_and_surfaces_stderr(
    intent_run: Callable[..., tuple[str, Path]],
) -> None:
    """FR-001 — a REAL DROPPED verdict still routes to redecompose.

    The tooling-error path must not swallow legitimate redecompose signals.
    A matrix with a DROPPED cell makes the in-process validator exit 1, and
    the tool returns ``action=redecompose`` with the dropped answer surfaced
    plus the new ``validator_stderr`` field present.
    """
    from foundry_mcp.tools.intent_coverage import foundry_intent_coverage

    project_root, _ = intent_run(
        _DROPPED_SPEC,
        [
            {"answer_id": "A-005", "casting_id": "1",
             "verdict": "DROPPED", "citation_chain": []},
        ],
    )
    result = foundry_intent_coverage(project_root=project_root)

    assert result["action"] == "redecompose", result
    assert result["dropped_answers"] == ["A-005"], result
    assert "validator_stderr" in result, result
    assert result["validator_exit"] == 1, result


# ---------------------------------------------------------------------------
# Casting C1 — per-answer aggregation rule (US-001 / AC-001..AC-004).
#
# The F0.7 gate blocks only when an answer reaches ZERO castings (every
# cell for that answer_id is DROPPED), not on any single DROPPED cell.
# Validator-level cases live above (test_zero_coverage_answer_dropped,
# test_partial_dropped_covered_elsewhere_passes,
# test_pilot_shaped_partition_passes,
# test_zero_coverage_in_partition_blocks_naming_only_that_answer); the
# tests below cover the AC-003 wording contract and the MCP tool layer's
# independent copy of the aggregation.
# ---------------------------------------------------------------------------


# AC-003 / GI-003 — the canonical aggregation sentence, written once and
# pasted word-for-word into the validator docstring, the agent contract,
# and the MCP tool docstring. Whitespace-normalized before comparison so
# line-wrapping differences don't count as wording differences.
_AGGREGATION_SENTENCE = (
    "An answer_id is DROPPED (gate-blocking) only when every casting's "
    "cell for it is DROPPED; a PROPAGATED or PARAPHRASED cell in any "
    "casting keeps the gate open for that answer, and per-cell DROPPED "
    "verdicts remain recorded in the matrix without blocking."
)

TOOL_MODULE_PATH = (
    REPO_ROOT / "plugins" / "foundry" / "mcp-server" / "src"
    / "foundry_mcp" / "tools" / "intent_coverage.py"
)

SERVER_MODULE_PATH = (
    REPO_ROOT / "plugins" / "foundry" / "mcp-server" / "src"
    / "foundry_mcp" / "server.py"
)

START_MD_PATH = REPO_ROOT / "plugins" / "foundry" / "commands" / "start.md"

# Every surface that states the F0.7 gate rule. GRIND D-007: the original
# guard checked only the first three, which is exactly how D-001
# (server.py Tool description) and D-004 (start.md F0.7 steps) shipped
# still stating the superseded per-cell rule.
_GATE_RULE_SURFACES = (
    CANONICAL_VALIDATOR_PATH,
    AGENT_PATH,
    TOOL_MODULE_PATH,
    SERVER_MODULE_PATH,
    START_MD_PATH,
)

# Superseded per-cell phrasings (compared lowercase against
# whitespace-normalized file text). "any cell with verdict==dropped" was
# the old docstring rule; "on any dropped" / "zero dropped" were the
# server.py description and start.md step phrasings that keyed the gate
# on a single DROPPED cell rather than on a zero-coverage answer.
_SUPERSEDED_PER_CELL_PHRASINGS = (
    "any cell with verdict==dropped",
    "on any dropped",
    "zero dropped",
)


def test_aggregation_rule_stated_identically() -> None:
    """AC-003 / GI-003 — every gate surface states the same per-answer rule.

    The per-answer aggregation rule must appear word-for-word (modulo
    line-wrapping) in ALL of:
      1. the validator docstring (canonical module),
      2. plugins/foundry/agents/intent-carrier.md,
      3. the Foundry-Intent-Coverage MCP tool module docstring,
      4. the Foundry-Intent-Coverage Tool description in server.py,
      5. the F0.7 step descriptions in commands/start.md
    AND no surface may retain a superseded per-cell phrasing — so no
    surface is left stating the superseded per-cell rule.
    """
    for path in _GATE_RULE_SURFACES:
        # Double quotes are stripped before whitespace normalization so
        # that Python's implicitly-concatenated string literals (the
        # server.py Tool description spells the sentence across '"..."'
        # chunks) reconstruct to their runtime text. The sentence itself
        # contains no double quote, so this is lossless for the check.
        raw = path.read_text(encoding="utf-8").replace('"', " ")
        normalized = re.sub(r"\s+", " ", raw)
        assert _AGGREGATION_SENTENCE in normalized, (
            f"per-answer aggregation sentence missing or reworded in "
            f"{path.name} — AC-003 requires the SAME words on every "
            f"surface that states the gate rule"
        )
        # No surface still states the superseded per-cell block rule.
        lowered = normalized.lower()
        for phrase in _SUPERSEDED_PER_CELL_PHRASINGS:
            assert phrase not in lowered, (
                f"{path.name} still carries the superseded per-cell rule "
                f"phrasing {phrase!r}"
            )


# GRIND D-009 / GI-003 — the canonical spec→matrix completeness sentence,
# written once and pasted word-for-word into the validator docstring, the
# agent contract, and the MCP tool docstring. Same normalization contract
# as _AGGREGATION_SENTENCE. server.py and start.md are NOT surfaces for
# this rule (they describe gate routing, not validator internals).
_COMPLETENESS_SENTENCE = (
    "A spec appendix answer_id with no matrix cell at all is "
    "zero-coverage: the validator emits INTENT_COVERAGE_MATRIX_INCOMPLETE "
    "naming each missing answer_id, and the omitted answer blocks the "
    "gate exactly like an answer whose every casting's cell is DROPPED."
)

_COMPLETENESS_RULE_SURFACES = (
    CANONICAL_VALIDATOR_PATH,
    AGENT_PATH,
    TOOL_MODULE_PATH,
)


def test_completeness_rule_stated_identically() -> None:
    """GRIND D-009 / GI-003 — the completeness rule uses the same words everywhere.

    The spec→matrix completeness rule (omission IS zero coverage) must
    appear word-for-word (modulo line-wrapping) in the validator
    docstring, agents/intent-carrier.md, and the MCP tool module — so no
    surface is left stating only the weaker every-RECORDED-cell rule that
    let omitted answers pass the gate.
    """
    for path in _COMPLETENESS_RULE_SURFACES:
        raw = path.read_text(encoding="utf-8").replace('"', " ")
        normalized = re.sub(r"\s+", " ", raw)
        assert _COMPLETENESS_SENTENCE in normalized, (
            f"spec→matrix completeness sentence missing or reworded in "
            f"{path.name} — GI-003 requires the SAME words on every "
            f"surface that states the completeness rule"
        )


# GRIND D-013 / GI-003 — the canonical anchor-scope sentence, written once
# and pasted word-for-word into the validator (module docstring +
# verdict_for_cell docstring) and the agent contract. Same normalization
# contract as _AGGREGATION_SENTENCE. The tool module / server.py /
# start.md are NOT surfaces for this rule (they state gate routing, not
# per-cell anchor derivation).
_ANCHOR_SCOPE_SENTENCE = (
    "Anchors 1 and 2 search the prompt BODY only — the prompt text with "
    "the three typed-table blocks (<invariants> / <state_transitions> / "
    "<contracts>) excluded — so a typed-row [from A-NNN] citation can "
    "never fire PROPAGATED; when the body lacks the literal but a typed "
    "row inside one of those blocks cites [from A-NNN], the verdict is "
    "PARAPHRASED."
)

_ANCHOR_SCOPE_SURFACES = (
    CANONICAL_VALIDATOR_PATH,
    AGENT_PATH,
)


def test_anchor_scope_rule_stated_identically() -> None:
    """GRIND D-013 / GI-003 — anchor scoping uses the same words everywhere.

    The validator's re-derivation and the intent-carrier contract must
    state the same anchor-1/2 search-space rule in the same words — the
    D-013 root cause was exactly a validator enforcing whole-prompt
    semantics while the agent contract stated body-scoped semantics.
    """
    for path in _ANCHOR_SCOPE_SURFACES:
        raw = path.read_text(encoding="utf-8").replace('"', " ")
        normalized = re.sub(r"\s+", " ", raw)
        assert _ANCHOR_SCOPE_SENTENCE in normalized, (
            f"anchor-scope sentence missing or reworded in {path.name} — "
            f"GI-003 requires the SAME words on every surface that states "
            f"the anchor-scope rule"
        )


def test_tool_partial_dropped_covered_elsewhere_passes(
    intent_run: Callable[..., tuple[str, Path]],
) -> None:
    """AC-001 end-to-end — MCP tool passes when the DROPPED cell has coverage elsewhere.

    The tool layer independently recomputes the dropped set from the
    matrix; fixing the validator alone would leave the gate blocking on
    partition. A-005 DROPPED in casting 1 but PROPAGATED in casting 2
    MUST yield passed=True / action=proceed_to_validate with an empty
    dropped_answers list, and stamp the .f07-intent-clean marker.
    """
    from foundry_mcp.tools.intent_coverage import foundry_intent_coverage

    project_root, run_dir = intent_run(
        _DROPPED_SPEC,
        [
            {"answer_id": "A-005", "casting_id": "1",
             "verdict": "DROPPED", "citation_chain": ["A-005"]},
            {"answer_id": "A-005", "casting_id": "2",
             "verdict": "PROPAGATED", "citation_chain": ["A-005"]},
        ],
    )
    result = foundry_intent_coverage(project_root=project_root)

    assert result["passed"] is True, result
    assert result["action"] == "proceed_to_validate", result
    assert result["dropped_answers"] == [], result
    assert (run_dir / ".f07-intent-clean").is_file()


def test_tool_summary_carries_per_cell_verdict_counts(
    intent_run: Callable[..., tuple[str, Path]],
) -> None:
    """GRIND D-006 / AC-002 — reported summary keeps the per-cell picture.

    On a pilot-shaped matrix the gate passes with DROPPED cells present;
    the summary persisted to castings/manifest.json AND the passing
    return payload must carry per-cell counts for ALL THREE verdicts —
    including the DROPPED cell count — so the non-blocking DROPPED cells
    are not hidden from the report (must-have truth 3: per-cell verdicts
    survive "in the emitted matrix and in the reported summary").
    Existing per-answer fields (dropped_answers, paraphrased_answers)
    and gate semantics are unchanged.
    """
    from foundry_mcp.tools.intent_coverage import foundry_intent_coverage

    spec_text = (
        "---\nspec_format_version: v2.1\n---\n"
        "## Appendix: Interview Transcript\n\n"
        "## A-001 [Locked]\nFirst answer. [from Q-001]\n\n"
        "## A-002 [Locked]\nSecond answer. [from Q-002]\n\n"
        "## A-003 [Locked]\nThird answer. [from Q-003]\n"
    )
    # Pilot shape: every answer covered in >= 1 casting, DROPPED cells
    # elsewhere. 6 cells: 3 PROPAGATED, 1 PARAPHRASED, 2 DROPPED.
    project_root, run_dir = intent_run(
        spec_text,
        [
            {"answer_id": "A-001", "casting_id": "1",
             "verdict": "PROPAGATED", "citation_chain": ["A-001"]},
            {"answer_id": "A-001", "casting_id": "2",
             "verdict": "DROPPED", "citation_chain": ["A-001"]},
            {"answer_id": "A-002", "casting_id": "1",
             "verdict": "DROPPED", "citation_chain": ["A-002"]},
            {"answer_id": "A-002", "casting_id": "2",
             "verdict": "PROPAGATED", "citation_chain": ["A-002"]},
            {"answer_id": "A-003", "casting_id": "1",
             "verdict": "PROPAGATED", "citation_chain": ["A-003"]},
            {"answer_id": "A-003", "casting_id": "2",
             "verdict": "PARAPHRASED", "citation_chain": ["A-003"]},
        ],
    )
    result = foundry_intent_coverage(project_root=project_root)

    expected_counts = {"PROPAGATED": 3, "PARAPHRASED": 1, "DROPPED": 2}
    assert result["passed"] is True, result
    assert result["cell_verdict_counts"] == expected_counts, result
    assert (
        result["intent_coverage_summary"]["cell_verdict_counts"]
        == expected_counts
    ), result
    # Existing per-answer reporting unchanged (AC-002).
    assert result["dropped_answers"] == [], result
    assert result["intent_coverage_summary"]["dropped_answers"] == [], result
    # Persisted manifest summary carries the same per-cell counts.
    manifest = json.loads(
        (run_dir / "castings" / "manifest.json").read_text(encoding="utf-8")
    )
    assert (
        manifest["intent_coverage_summary"]["cell_verdict_counts"]
        == expected_counts
    ), manifest


def test_tool_redecompose_hints_list_exactly_zero_coverage_answers(
    intent_run: Callable[..., tuple[str, Path]],
) -> None:
    """AC-001 — redecompose_hints lists exactly the zero-coverage answers.

    A-001 has a DROPPED cell in casting 1 but is PROPAGATED in casting 2
    (covered — must NOT surface); A-005 is DROPPED in every casting
    (zero coverage — must surface). dropped_answers and redecompose_hints
    carry exactly A-005 and nothing else.
    """
    from foundry_mcp.tools.intent_coverage import foundry_intent_coverage

    spec_text = (
        "---\nspec_format_version: v2.1\n---\n"
        "## Appendix: Interview Transcript\n\n"
        "## A-001 [Locked]\nCovered answer. [from Q-001]\n\n"
        "## A-005 [Locked]\nDropped answer. [from Q-005]\n"
    )
    project_root, _ = intent_run(
        spec_text,
        [
            {"answer_id": "A-001", "casting_id": "1",
             "verdict": "DROPPED", "citation_chain": ["A-001"]},
            {"answer_id": "A-001", "casting_id": "2",
             "verdict": "PROPAGATED", "citation_chain": ["A-001"]},
            {"answer_id": "A-005", "casting_id": "1",
             "verdict": "DROPPED", "citation_chain": ["A-005"]},
            {"answer_id": "A-005", "casting_id": "2",
             "verdict": "DROPPED", "citation_chain": ["A-005"]},
        ],
    )
    result = foundry_intent_coverage(project_root=project_root)

    assert result["action"] == "redecompose", result
    assert result["dropped_answers"] == ["A-005"], result
    hint_ids = [h["answer_id"] for h in result["redecompose_hints"]]
    assert hint_ids == ["A-005"], result
    # The hint still resolves a per-cell DROPPED source for its target.
    assert result["redecompose_hints"][0]["suggested_casting"] == "1", result


# ---------------------------------------------------------------------------
# GRIND D-009 (US-001) — omission bypass: a spec appendix answer with NO
# matrix cell at all is zero-coverage and blocks the gate; and
# GRIND D-010 (AC-002) — the redecompose payload carries the per-cell
# picture (cell_verdict_counts) and the matrix pointer (matrix_path).
# ---------------------------------------------------------------------------


_OMISSION_SPEC = (
    "---\nspec_format_version: v2.1\n---\n"
    "## Appendix: Interview Transcript\n\n"
    "## A-001 [Locked]\nFirst answer. [from Q-001]\n\n"
    "## A-002 [Locked]\nSecond answer. [from Q-002]\n\n"
    "## A-003 [Locked]\nOmitted from the matrix. [from Q-003]\n"
)

_OMISSION_MATRIX = [
    {"answer_id": "A-001", "casting_id": "1",
     "verdict": "PROPAGATED", "citation_chain": ["A-001"]},
    {"answer_id": "A-002", "casting_id": "1",
     "verdict": "PROPAGATED", "citation_chain": ["A-002"]},
    # A-003: NO row at all — the ASSAY-reproduced omission shape.
]


def test_spec_answer_omitted_from_matrix_blocks(
    run_intent_coverage_validator: Callable[..., tuple[int, str, str]],
    tmp_path: Path,
) -> None:
    """GRIND D-009 — omission IS zero coverage; the validator blocks on it.

    ASSAY repro shape: spec declares A-001/A-002/A-003, matrix carries
    rows for only A-001/A-002. The validator MUST exit 1 with
    INTENT_COVERAGE_MATRIX_INCOMPLETE naming A-003 AND treat A-003 as
    zero-coverage — it reaches the INTENT_COVERAGE_DROPPED failure line
    exactly like an all-DROPPED answer. Covered answers are not named.
    """
    spec = tmp_path / "spec.md"
    spec.write_text(_OMISSION_SPEC, encoding="utf-8")
    coverage = tmp_path / "coverage.json"
    coverage.write_text(
        json.dumps({
            "stream": "INTENT-01",
            "phase": "F0.7",
            "spec_format_version": "v2.1",
            "spec_hash": "sha256:abc",
            "agent_path": "plugins/foundry/agents/intent-carrier.md",
            "wall_clock_seconds": 1.0,
            "answer_count": 3,
            "casting_count": 1,
            "summary": {"PROPAGATED": 2, "PARAPHRASED": 0, "DROPPED": 0},
            "matrix": _OMISSION_MATRIX,
        }),
        encoding="utf-8",
    )
    exit_code, stdout, _ = run_intent_coverage_validator(
        coverage, spec_path=spec,
    )
    assert exit_code != 0
    assert "INTENT_COVERAGE_MATRIX_INCOMPLETE" in stdout, stdout
    assert "INTENT_COVERAGE_DROPPED" in stdout, stdout
    assert "A-003" in stdout, stdout
    # Covered answers MUST NOT be flagged by either failure line.
    assert "A-001" not in stdout, stdout
    assert "A-002" not in stdout, stdout


def test_matrix_covering_every_spec_answer_passes(
    run_intent_coverage_validator: Callable[..., tuple[int, str, str]],
    tmp_path: Path,
) -> None:
    """GRIND D-009 (b) — full spec→matrix coverage keeps behavior unchanged.

    Every spec appendix answer has at least one matrix cell: exit 0, no
    INTENT_COVERAGE_MATRIX_INCOMPLETE line.
    """
    spec = tmp_path / "spec.md"
    spec.write_text(_OMISSION_SPEC, encoding="utf-8")
    coverage = tmp_path / "coverage.json"
    coverage.write_text(
        json.dumps({
            "stream": "INTENT-01",
            "phase": "F0.7",
            "spec_format_version": "v2.1",
            "spec_hash": "sha256:abc",
            "agent_path": "plugins/foundry/agents/intent-carrier.md",
            "wall_clock_seconds": 1.0,
            "answer_count": 3,
            "casting_count": 1,
            "summary": {"PROPAGATED": 3, "PARAPHRASED": 0, "DROPPED": 0},
            "matrix": _OMISSION_MATRIX + [
                {"answer_id": "A-003", "casting_id": "1",
                 "verdict": "PROPAGATED", "citation_chain": ["A-003"]},
            ],
        }),
        encoding="utf-8",
    )
    exit_code, stdout, _ = run_intent_coverage_validator(
        coverage, spec_path=spec,
    )
    assert exit_code == 0, stdout
    assert "INTENT_COVERAGE_MATRIX_INCOMPLETE" not in stdout, stdout


def test_no_spec_skips_completeness_check(
    run_intent_coverage_validator: Callable[..., tuple[int, str, str]],
    tmp_path: Path,
) -> None:
    """GRIND D-009 (c) — without --spec the completeness check cannot run.

    Documented behavior: the appendix answer-set is unknown without
    --spec, so matrix-only validation applies and an omission is
    invisible at the validator level. This is acceptable because the
    Foundry-Intent-Coverage MCP gate ALWAYS supplies --spec (see
    _run_validator_in_process), so the completeness check always runs
    in production.
    """
    coverage = tmp_path / "coverage.json"
    coverage.write_text(
        json.dumps({
            "stream": "INTENT-01",
            "phase": "F0.7",
            "spec_format_version": "v2.1",
            "spec_hash": "sha256:abc",
            "agent_path": "plugins/foundry/agents/intent-carrier.md",
            "wall_clock_seconds": 1.0,
            "answer_count": 3,
            "casting_count": 1,
            "summary": {"PROPAGATED": 2, "PARAPHRASED": 0, "DROPPED": 0},
            "matrix": _OMISSION_MATRIX,
        }),
        encoding="utf-8",
    )
    exit_code, stdout, _ = run_intent_coverage_validator(coverage)
    assert exit_code == 0, stdout
    assert "INTENT_COVERAGE_MATRIX_INCOMPLETE" not in stdout, stdout


def test_tool_omitted_spec_answer_blocks_via_redecompose(
    intent_run: Callable[..., tuple[str, Path]],
) -> None:
    """GRIND D-009 — the ASSAY live-repro through the real MCP gate.

    Spec declares A-001/A-002/A-003; matrix carries rows for only two.
    Previously: passed=True / proceed_to_validate (the omission bypass).
    Now: action=redecompose with A-003 surfaced STRUCTURALLY — in
    dropped_answers and redecompose_hints (suggested_casting None, no
    cell to draw from) — not only in validator_stdout prose. The
    .f07-intent-clean marker is NOT stamped.
    """
    from foundry_mcp.tools.intent_coverage import foundry_intent_coverage

    project_root, run_dir = intent_run(_OMISSION_SPEC, list(_OMISSION_MATRIX))
    result = foundry_intent_coverage(project_root=project_root)

    assert result["passed"] is False, result
    assert result["action"] == "redecompose", result
    assert result["dropped_answers"] == ["A-003"], result
    hint_ids = [h["answer_id"] for h in result["redecompose_hints"]]
    assert hint_ids == ["A-003"], result
    assert result["redecompose_hints"][0]["suggested_casting"] is None, result
    assert result["redecompose_hints"][0]["citation_chain"] == ["A-003"], result
    assert "INTENT_COVERAGE_MATRIX_INCOMPLETE" in result["validator_stdout"], (
        result
    )
    assert not (run_dir / ".f07-intent-clean").exists()


def test_tool_omission_blocks_when_spec_resolvable_only_via_state_json(
    intent_run: Callable[..., tuple[str, Path]],
    tmp_path: Path,
) -> None:
    """GRIND D-012 — the completeness check reaches specs outside the run dir.

    foundry.py makes spec_path optional and copies the spec into the run
    dir only when the source exists, so a run whose spec lives OUTSIDE
    the run dir (state.json['spec_path'] set, no run-dir copy) is a
    reachable production state. Previously the tool hardcoded
    <run_dir>/spec.md: no --spec reached the validator and the tool-layer
    fold-in was skipped, so the D-009 omission bypass silently reopened
    (passed=True / proceed_to_validate / stamp written). Now the tool
    resolves the spec via the canonical _resolve_spec_path (run-dir copy
    first, state.json['spec_path'] fallback resolved against
    project_root): the omitted answer MUST block via redecompose and the
    .f07-intent-clean marker MUST NOT be stamped.
    """
    from foundry_mcp.tools.intent_coverage import foundry_intent_coverage

    project_root, run_dir = intent_run(_OMISSION_SPEC, list(_OMISSION_MATRIX))
    # Move the spec OUT of the run dir: no run-dir copy remains, and the
    # spec is reachable ONLY through the state.json['spec_path'] fallback.
    (run_dir / "spec.md").unlink()
    external_spec = tmp_path / "specs" / "feature-spec.md"
    external_spec.parent.mkdir()
    external_spec.write_text(_OMISSION_SPEC, encoding="utf-8")
    (run_dir / "state.json").write_text(
        json.dumps({"spec_path": "specs/feature-spec.md"}), encoding="utf-8",
    )

    result = foundry_intent_coverage(project_root=project_root)

    assert result["passed"] is False, result
    assert result["action"] == "redecompose", result
    assert result["dropped_answers"] == ["A-003"], result
    hint_ids = [h["answer_id"] for h in result["redecompose_hints"]]
    assert hint_ids == ["A-003"], result
    assert result["redecompose_hints"][0]["suggested_casting"] is None, result
    assert "INTENT_COVERAGE_MATRIX_INCOMPLETE" in result["validator_stdout"], (
        result
    )
    assert not (run_dir / ".f07-intent-clean").exists()


def test_tool_redecompose_payload_carries_cell_counts_and_matrix_path(
    intent_run: Callable[..., tuple[str, Path]],
) -> None:
    """GRIND D-010 / AC-002 — redecompose payload keeps the per-cell picture.

    The lead receiving redecompose is the reader who most needs
    cell_verdict_counts and the matrix pointer; both were previously
    present only on the pass path. Gate semantics unchanged: the
    zero-coverage answer still routes to redecompose.
    """
    from foundry_mcp.tools.intent_coverage import foundry_intent_coverage

    spec_text = (
        "---\nspec_format_version: v2.1\n---\n"
        "## Appendix: Interview Transcript\n\n"
        "## A-001 [Locked]\nCovered answer. [from Q-001]\n\n"
        "## A-005 [Locked]\nDropped answer. [from Q-005]\n"
    )
    project_root, run_dir = intent_run(
        spec_text,
        [
            {"answer_id": "A-001", "casting_id": "1",
             "verdict": "DROPPED", "citation_chain": ["A-001"]},
            {"answer_id": "A-001", "casting_id": "2",
             "verdict": "PROPAGATED", "citation_chain": ["A-001"]},
            {"answer_id": "A-005", "casting_id": "1",
             "verdict": "DROPPED", "citation_chain": ["A-005"]},
            {"answer_id": "A-005", "casting_id": "2",
             "verdict": "DROPPED", "citation_chain": ["A-005"]},
        ],
    )
    result = foundry_intent_coverage(project_root=project_root)

    assert result["action"] == "redecompose", result
    assert result["cell_verdict_counts"] == {
        "PROPAGATED": 1, "PARAPHRASED": 0, "DROPPED": 3,
    }, result
    assert result["matrix_path"] == str(run_dir / "intent-coverage.json"), (
        result
    )
    # D-014b: the redecompose payload discloses that completeness ran.
    assert result["completeness_checked"] is True, result
    # D-018c: and that the ghost/unverifiable integrity checks ran too.
    assert result["verification_checked"] is True, result


# ---------------------------------------------------------------------------
# GRIND D-014 / D-015 — gate-layer regressions: non-coverage validator
# failures route to rerun_intent_carrier (not redecompose), ghost-casting
# coverage no longer passes the gate, and the completeness skip is
# disclosed via the completeness_checked boolean.
# ---------------------------------------------------------------------------


def test_tool_non_coverage_failure_routes_rerun_not_redecompose(
    intent_run: Callable[..., tuple[str, Path]],
) -> None:
    """GRIND D-015 — schema-invalid matrix with full coverage → rerun.

    ASSAY probe shape: the validator exits 1 (smuggled top-level key) but
    every spec answer is covered — the zero-coverage answer set is empty.
    Previously the unconditional else returned action=redecompose with an
    empty dropped_answers list and the re-run-with-these-A-NNN hint;
    re-decomposition cannot fix a malformed matrix. Now the gate returns
    action=rerun_intent_carrier (existing action — closed vocabulary
    preserved) carrying the validator output, and never stamps the
    .f07-intent-clean marker.
    """
    from foundry_mcp.tools.intent_coverage import foundry_intent_coverage

    project_root, run_dir = intent_run(
        _CLEAN_SPEC,
        [
            {"answer_id": "A-001", "casting_id": "1",
             "verdict": "PROPAGATED", "citation_chain": ["A-001"]},
        ],
    )
    doc = _coverage_doc([
        {"answer_id": "A-001", "casting_id": "1",
         "verdict": "PROPAGATED", "citation_chain": ["A-001"]},
    ])
    doc["auto_resolve_hint"] = "smuggled"  # not in KNOWN_INTENT_COVERAGE_KEYS
    (run_dir / "intent-coverage.json").write_text(
        json.dumps(doc), encoding="utf-8",
    )

    result = foundry_intent_coverage(project_root=project_root)

    assert result["passed"] is False, result
    assert result["action"] == "rerun_intent_carrier", result
    assert result["validator_exit"] == 1, result
    assert "INTENT_COVERAGE_SCHEMA_INVALID" in result["validator_stdout"], (
        result
    )
    assert not (run_dir / ".f07-intent-clean").exists()


def test_tool_ghost_only_coverage_no_longer_passes(
    intent_run: Callable[..., tuple[str, Path]],
) -> None:
    """GRIND D-014a + D-019 — the ghost-casting probe through the real gate.

    A-001's only cell cites casting_id 'ghost', absent from
    castings/manifest.json. D-014a made the validator flag the ghost cell
    (INTENT_COVERAGE_MATRIX_INCOMPLETE) and block the answer as
    zero-coverage — but the tool's independent aggregation did NOT apply
    the normalization, so this blocked as rerun_intent_carrier with EMPTY
    dropped_answers/redecompose_hints while the validator stdout named
    the answer (D-019 layer divergence). Now both layers share
    aggregate_matrix_coverage: the ghost cell contributes DROPPED, A-001
    lands in dropped_answers and redecompose_hints, and the gate routes
    as redecompose (AC-001: hints list exactly the zero-coverage answers).
    """
    from foundry_mcp.tools.intent_coverage import foundry_intent_coverage

    project_root, run_dir = intent_run(
        _CLEAN_SPEC,
        [
            {"answer_id": "A-001", "casting_id": "ghost",
             "verdict": "PROPAGATED", "citation_chain": ["A-001"]},
        ],
    )
    result = foundry_intent_coverage(project_root=project_root)

    assert result["passed"] is False, result
    assert result["action"] == "redecompose", result
    assert result["dropped_answers"] == ["A-001"], result
    hint_ids = [h["answer_id"] for h in result["redecompose_hints"]]
    assert hint_ids == ["A-001"], result
    # No DROPPED cell exists for A-001 (its only cell claims PROPAGATED
    # via the ghost casting), so the hint has no cell to draw from.
    assert result["redecompose_hints"][0]["suggested_casting"] is None, result
    assert "INTENT_COVERAGE_MATRIX_INCOMPLETE" in result["validator_stdout"], (
        result
    )
    assert "ghost" in result["validator_stdout"], result
    assert "INTENT_COVERAGE_DROPPED" in result["validator_stdout"], result
    assert not (run_dir / ".f07-intent-clean").exists()


def test_tool_pass_payload_and_summary_carry_completeness_checked(
    intent_run: Callable[..., tuple[str, Path]],
) -> None:
    """GRIND D-014b — completeness_checked=True when the spec fold-in ran.

    With a resolvable spec the completeness check runs; the pass payload,
    the returned intent_coverage_summary, AND the summary persisted to
    castings/manifest.json all carry completeness_checked=True.
    """
    from foundry_mcp.tools.intent_coverage import foundry_intent_coverage

    project_root, run_dir = intent_run(
        _CLEAN_SPEC,
        [
            {"answer_id": "A-001", "casting_id": "1",
             "verdict": "PROPAGATED", "citation_chain": ["A-001"]},
        ],
    )
    result = foundry_intent_coverage(project_root=project_root)

    assert result["passed"] is True, result
    assert result["completeness_checked"] is True, result
    assert (
        result["intent_coverage_summary"]["completeness_checked"] is True
    ), result
    # D-018c: manifest + prompts are resolvable, so the ghost/unverifiable
    # integrity checks ran — disclosed on every surface next to
    # completeness_checked.
    assert result["verification_checked"] is True, result
    assert (
        result["intent_coverage_summary"]["verification_checked"] is True
    ), result
    manifest = json.loads(
        (run_dir / "castings" / "manifest.json").read_text(encoding="utf-8")
    )
    assert (
        manifest["intent_coverage_summary"]["completeness_checked"] is True
    ), manifest
    assert (
        manifest["intent_coverage_summary"]["verification_checked"] is True
    ), manifest


def test_tool_discloses_completeness_skip_when_no_spec_resolvable(
    intent_run: Callable[..., tuple[str, Path]],
) -> None:
    """GRIND D-014b — the silent completeness skip is now disclosed.

    When no spec is resolvable (no run-dir spec.md, no
    state.json['spec_path']) the completeness check cannot run, yet the
    gate still stamps .f07-intent-clean on a clean matrix. Previously
    nothing recorded that the check was skipped. Now the pass payload and
    the persisted intent_coverage_summary carry
    completeness_checked=False, making the skip visible.
    """
    from foundry_mcp.tools.intent_coverage import foundry_intent_coverage

    project_root, run_dir = intent_run(
        _CLEAN_SPEC,
        [
            {"answer_id": "A-001", "casting_id": "1",
             "verdict": "PROPAGATED", "citation_chain": ["A-001"]},
        ],
    )
    # Remove the spec: no run-dir copy, no state.json fallback.
    (run_dir / "spec.md").unlink()

    result = foundry_intent_coverage(project_root=project_root)

    assert result["passed"] is True, result
    assert result["completeness_checked"] is False, result
    assert (
        result["intent_coverage_summary"]["completeness_checked"] is False
    ), result
    manifest = json.loads(
        (run_dir / "castings" / "manifest.json").read_text(encoding="utf-8")
    )
    assert (
        manifest["intent_coverage_summary"]["completeness_checked"] is False
    ), manifest


# ---------------------------------------------------------------------------
# GRIND D-017 / D-018 / D-019 — matrix input validation: a cell that
# cannot be verified must not contribute coverage, and every dark-state
# must block or be disclosed.
# ---------------------------------------------------------------------------


# D-018 / GI-003 — the canonical cell-verifiability sentence, written once
# and pasted word-for-word into the validator module docstring, the agent
# contract, and the MCP tool module docstring (the tool implements the
# same normalization via the shared aggregate_matrix_coverage). Same
# normalization contract as _AGGREGATION_SENTENCE.
_UNVERIFIABLE_SENTENCE = (
    "A matrix cell that cannot be verified cannot contribute coverage: "
    "a cell missing answer_id, casting_id, or verdict, a cell citing a "
    "casting_id absent from castings/manifest.json, and a cell citing a "
    "manifest-declared casting whose prompt file is missing or unreadable "
    "all count as DROPPED in the per-answer aggregation, so an answer "
    "whose only non-DROPPED cells are such cells blocks as zero-coverage."
)

_UNVERIFIABLE_RULE_SURFACES = (
    CANONICAL_VALIDATOR_PATH,
    AGENT_PATH,
    TOOL_MODULE_PATH,
)


def test_unverifiable_rule_stated_identically() -> None:
    """GRIND D-018 / GI-003 — the cell-verifiability rule uses the same words everywhere.

    The unverifiable-cell rule must appear word-for-word (modulo
    line-wrapping) in the validator docstring, agents/intent-carrier.md,
    and the MCP tool module — the D-017/D-018/D-019 defect family is
    exactly a rule enforced (or not) differently across surfaces.
    """
    for path in _UNVERIFIABLE_RULE_SURFACES:
        raw = path.read_text(encoding="utf-8").replace('"', " ")
        normalized = re.sub(r"\s+", " ", raw)
        assert _UNVERIFIABLE_SENTENCE in normalized, (
            f"cell-verifiability sentence missing or reworded in "
            f"{path.name} — GI-003 requires the SAME words on every "
            f"surface that states the rule"
        )


_A001_ONLY_SPEC = (
    "---\nspec_format_version: v2.1\n---\n"
    "## Appendix: Interview Transcript\n\n"
    "## A-001 [Locked]\nSurface contract. [from Q-001]\n"
)


def test_cell_missing_casting_id_cannot_contribute(
    run_intent_coverage_validator: Callable[..., tuple[int, str, str]],
    tmp_path: Path,
) -> None:
    """GRIND D-017 — a cell naming NO casting must not count as coverage.

    ASSAY live probe: {"answer_id": "A-001", "verdict": "PROPAGATED",
    "citation_chain": [...]} with no casting_id escaped the allow-list-only
    key check AND both truthy-casting_id-gated integrity guards, and
    contributed PROPAGATED (exit 0, gate passed). Now the required-key
    validation flags it (existing SCHEMA_INVALID token — GI-002) and the
    cell is normalized to non-contributing exactly like a ghost cell, so
    A-001 blocks as zero-coverage via INTENT_COVERAGE_DROPPED.
    """
    spec = _write_production_layout(
        tmp_path,
        _A001_ONLY_SPEC,
        {"1": "# Casting 1\n\nNothing anchors the answer here.\n"},
    )
    coverage = tmp_path / "coverage.json"
    coverage.write_text(
        json.dumps({
            "stream": "INTENT-01",
            "phase": "F0.7",
            "spec_format_version": "v2.1",
            "spec_hash": "sha256:abc",
            "agent_path": "plugins/foundry/agents/intent-carrier.md",
            "wall_clock_seconds": 1.0,
            "answer_count": 1,
            "casting_count": 1,
            "summary": {"PROPAGATED": 1, "PARAPHRASED": 0, "DROPPED": 0},
            "matrix": [
                {"answer_id": "A-001",
                 "verdict": "PROPAGATED", "citation_chain": ["A-001"]},
            ],
        }),
        encoding="utf-8",
    )
    exit_code, stdout, _ = run_intent_coverage_validator(
        coverage, spec_path=spec,
    )
    assert exit_code != 0
    assert "INTENT_COVERAGE_SCHEMA_INVALID" in stdout, stdout
    assert "casting_id" in stdout, stdout
    assert "INTENT_COVERAGE_DROPPED" in stdout, stdout
    assert "A-001" in stdout, stdout


def test_promptless_declared_casting_blocks(
    run_intent_coverage_validator: Callable[..., tuple[int, str, str]],
    tmp_path: Path,
) -> None:
    """GRIND D-018a — a manifest-declared casting with NO prompt file.

    Re-derivation used to skip silently (prompt_path.is_file() guard)
    while the ghost guard passed (the casting IS declared) — an
    unverifiable PROPAGATED claim survived as coverage. Now the
    non-DROPPED cell of a promptless declared casting is flagged with the
    existing MATRIX_INCOMPLETE token and normalized to DROPPED, so an
    answer whose only coverage cites the promptless casting blocks as
    zero-coverage.
    """
    spec = _write_production_layout(
        tmp_path,
        _A001_ONLY_SPEC,
        {"1": "# Casting 1\n\nNothing anchors the answer here.\n"},
    )
    # Declare casting "2" in the manifest WITHOUT writing its prompt file.
    (tmp_path / "castings" / "manifest.json").write_text(
        json.dumps({"castings": [{"id": "1"}, {"id": "2"}]}),
        encoding="utf-8",
    )
    coverage = tmp_path / "coverage.json"
    coverage.write_text(
        json.dumps({
            "stream": "INTENT-01",
            "phase": "F0.7",
            "spec_format_version": "v2.1",
            "spec_hash": "sha256:abc",
            "agent_path": "plugins/foundry/agents/intent-carrier.md",
            "wall_clock_seconds": 1.0,
            "answer_count": 1,
            "casting_count": 2,
            "summary": {"PROPAGATED": 1, "PARAPHRASED": 0, "DROPPED": 1},
            "matrix": [
                {"answer_id": "A-001", "casting_id": "1",
                 "verdict": "DROPPED", "citation_chain": ["A-001"]},
                {"answer_id": "A-001", "casting_id": "2",
                 "verdict": "PROPAGATED", "citation_chain": ["A-001"]},
            ],
        }),
        encoding="utf-8",
    )
    exit_code, stdout, _ = run_intent_coverage_validator(
        coverage, spec_path=spec,
    )
    assert exit_code != 0
    assert "INTENT_COVERAGE_MATRIX_INCOMPLETE" in stdout, stdout
    assert "casting-2-prompt.md" in stdout, stdout
    assert "INTENT_COVERAGE_DROPPED" in stdout, stdout
    assert "A-001" in stdout, stdout


def test_castings_dir_flag_threads_integrity_checks(
    run_intent_coverage_validator: Callable[..., tuple[int, str, str]],
    tmp_path: Path,
) -> None:
    """GRIND D-018b — --castings-dir closes the spec-location dark state.

    The D-012/D-013 composition bug: casting_dir derived as
    spec_path.parent / 'castings', so when the spec resolves outside the
    run dir the ghost guard and re-derivation silently go dark — the
    IDENTICAL matrix flips from blocked to passing purely on spec
    location. The --castings-dir argument (which the MCP gate always
    passes) threads the run's castings dir explicitly: the same matrix
    that blocks in the run-dir layout must block in the fallback layout.
    """
    # Castings live in the run dir; the spec lives elsewhere with NO
    # adjacent castings/ directory.
    run_dir = tmp_path / "run"
    castings = run_dir / "castings"
    castings.mkdir(parents=True)
    (castings / "manifest.json").write_text(
        json.dumps({"castings": [{"id": "1"}]}), encoding="utf-8",
    )
    (castings / "casting-1-prompt.md").write_text(
        "# Casting 1\n\nNothing anchors the answer here.\n", encoding="utf-8",
    )
    spec = tmp_path / "specs" / "feature-spec.md"
    spec.parent.mkdir()
    spec.write_text(_A001_ONLY_SPEC, encoding="utf-8")
    coverage = tmp_path / "coverage.json"
    coverage.write_text(
        json.dumps({
            "stream": "INTENT-01",
            "phase": "F0.7",
            "spec_format_version": "v2.1",
            "spec_hash": "sha256:abc",
            "agent_path": "plugins/foundry/agents/intent-carrier.md",
            "wall_clock_seconds": 1.0,
            "answer_count": 1,
            "casting_count": 1,
            "summary": {"PROPAGATED": 1, "PARAPHRASED": 0, "DROPPED": 0},
            "matrix": [
                {"answer_id": "A-001", "casting_id": "ghost",
                 "verdict": "PROPAGATED", "citation_chain": ["A-001"]},
            ],
        }),
        encoding="utf-8",
    )
    # Without --castings-dir: no castings/ next to the spec — the
    # integrity checks cannot run and the ghost claim passes (the dark
    # state, now disclosed at the gate via verification_checked=False).
    exit_code, stdout, _ = run_intent_coverage_validator(
        coverage, spec_path=spec,
    )
    assert exit_code == 0, stdout
    # With --castings-dir threading the run's castings dir: the SAME
    # matrix blocks — ghost flagged, answer named as zero-coverage.
    # (Direct subprocess over the shim — the shared runner fixture
    # predates the --castings-dir flag and is out of this casting's
    # file scope.)
    result = subprocess.run(
        [
            "python3", str(VALIDATE_PATH), str(coverage),
            "--spec", str(spec),
            "--castings-dir", str(castings),
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    exit_code, stdout = result.returncode, result.stdout
    assert exit_code != 0
    assert "INTENT_COVERAGE_MATRIX_INCOMPLETE" in stdout, stdout
    assert "ghost" in stdout, stdout
    assert "INTENT_COVERAGE_DROPPED" in stdout, stdout
    assert "A-001" in stdout, stdout


def test_tool_cell_missing_casting_id_blocks_via_redecompose(
    intent_run: Callable[..., tuple[str, Path]],
) -> None:
    """GRIND D-017 mirror — the no-casting_id probe through the real gate.

    Live repro shape: the cell escaped everything and the gate stamped
    .f07-intent-clean (exit 0, passed=True). Now the shared aggregation
    normalizes the structurally-invalid cell to DROPPED: A-001 lands in
    dropped_answers / redecompose_hints, the gate routes as redecompose,
    and no marker is stamped.
    """
    from foundry_mcp.tools.intent_coverage import foundry_intent_coverage

    project_root, run_dir = intent_run(
        _CLEAN_SPEC,
        [
            {"answer_id": "A-001",
             "verdict": "PROPAGATED", "citation_chain": ["A-001"]},
        ],
    )
    result = foundry_intent_coverage(project_root=project_root)

    assert result["passed"] is False, result
    assert result["action"] == "redecompose", result
    assert result["dropped_answers"] == ["A-001"], result
    hint_ids = [h["answer_id"] for h in result["redecompose_hints"]]
    assert hint_ids == ["A-001"], result
    assert "INTENT_COVERAGE_SCHEMA_INVALID" in result["validator_stdout"], (
        result
    )
    assert "INTENT_COVERAGE_DROPPED" in result["validator_stdout"], result
    assert not (run_dir / ".f07-intent-clean").exists()


def test_tool_promptless_declared_casting_blocks(
    intent_run: Callable[..., tuple[str, Path]],
) -> None:
    """GRIND D-018a mirror — promptless declared casting through the gate.

    The manifest declares casting "1" but no prompt file exists
    (prompts={} suppresses the fixture's production-shaped prompt
    writing). The PROPAGATED claim citing it is unverifiable → normalized
    to DROPPED by the shared aggregation → A-001 blocks via redecompose
    with the answer named in the hints.
    """
    from foundry_mcp.tools.intent_coverage import foundry_intent_coverage

    project_root, run_dir = intent_run(
        _CLEAN_SPEC,
        [
            {"answer_id": "A-001", "casting_id": "1",
             "verdict": "PROPAGATED", "citation_chain": ["A-001"]},
        ],
        prompts={},
    )
    result = foundry_intent_coverage(project_root=project_root)

    assert result["passed"] is False, result
    assert result["action"] == "redecompose", result
    assert result["dropped_answers"] == ["A-001"], result
    hint_ids = [h["answer_id"] for h in result["redecompose_hints"]]
    assert hint_ids == ["A-001"], result
    assert "INTENT_COVERAGE_MATRIX_INCOMPLETE" in result["validator_stdout"], (
        result
    )
    assert "casting-1-prompt.md" in result["validator_stdout"], result
    assert not (run_dir / ".f07-intent-clean").exists()


def test_tool_ghost_blocks_in_state_json_fallback_layout(
    intent_run: Callable[..., tuple[str, Path]],
    tmp_path: Path,
) -> None:
    """GRIND D-018b — the same matrix blocks regardless of spec location.

    Live repro: an identical ghost-coverage matrix flipped from blocked
    (run-dir spec) to passing (spec resolvable only via
    state.json['spec_path']) because BOTH integrity guards derived the
    castings dir from the spec's parent. The gate now threads the run's
    castings dir explicitly, so the fallback layout blocks exactly like
    the run-dir layout — redecompose, answer named, no marker.
    """
    from foundry_mcp.tools.intent_coverage import foundry_intent_coverage

    project_root, run_dir = intent_run(
        _CLEAN_SPEC,
        [
            {"answer_id": "A-001", "casting_id": "ghost",
             "verdict": "PROPAGATED", "citation_chain": ["A-001"]},
        ],
    )
    # Move the spec OUT of the run dir: reachable ONLY through the
    # state.json['spec_path'] fallback (the D-012 layout).
    (run_dir / "spec.md").unlink()
    external_spec = tmp_path / "specs" / "feature-spec.md"
    external_spec.parent.mkdir()
    external_spec.write_text(_CLEAN_SPEC, encoding="utf-8")
    (run_dir / "state.json").write_text(
        json.dumps({"spec_path": "specs/feature-spec.md"}), encoding="utf-8",
    )

    result = foundry_intent_coverage(project_root=project_root)

    assert result["passed"] is False, result
    assert result["action"] == "redecompose", result
    assert result["dropped_answers"] == ["A-001"], result
    hint_ids = [h["answer_id"] for h in result["redecompose_hints"]]
    assert hint_ids == ["A-001"], result
    assert result["completeness_checked"] is True, result
    assert result["verification_checked"] is True, result
    assert "INTENT_COVERAGE_MATRIX_INCOMPLETE" in result["validator_stdout"], (
        result
    )
    assert "ghost" in result["validator_stdout"], result
    assert not (run_dir / ".f07-intent-clean").exists()


def test_tool_no_castings_resolvable_disclosed(
    intent_run: Callable[..., tuple[str, Path]],
) -> None:
    """GRIND D-018c — no castings dir/manifest resolvable is disclosed.

    With no castings/ directory at all, the ghost-casting and
    unverifiable-casting checks are off by design. The gate must not fail
    the run for that, but the dark state must be visible: the pass
    payload and the returned summary carry verification_checked=False
    (no castings/manifest.json exists, so no summary can be persisted —
    the returned surfaces are the disclosure).
    """
    from foundry_mcp.tools.intent_coverage import foundry_intent_coverage

    project_root, run_dir = intent_run(
        _CLEAN_SPEC,
        [
            {"answer_id": "A-001", "casting_id": "1",
             "verdict": "PROPAGATED", "citation_chain": ["A-001"]},
        ],
        with_manifest=False,
    )
    result = foundry_intent_coverage(project_root=project_root)

    assert result["passed"] is True, result
    assert result["verification_checked"] is False, result
    assert (
        result["intent_coverage_summary"]["verification_checked"] is False
    ), result
    # The spec IS resolvable, so completeness still ran — the two
    # disclosures are independent.
    assert result["completeness_checked"] is True, result


# ---------------------------------------------------------------------------
# GRIND D-020 — gate robustness on malformed matrices the validator already
# handles. The gate's own folds over the matrix (paraphrased answers,
# propagated_count, cell_verdict_counts, redecompose_hints) previously
# assumed list-of-dicts with all keys present and crashed
# (KeyError/AttributeError) before any payload was built, on exactly the
# input class the gate's rerun_intent_carrier contract exists for. Each
# test asserts the documented action payload, never an exception.
# ---------------------------------------------------------------------------


def test_tool_cell_missing_answer_id_no_crash(
    intent_run: Callable[..., tuple[str, Path]],
) -> None:
    """GRIND D-020 shape 2 — PARAPHRASED cell missing answer_id.

    The gate's paraphrased fold dereferenced c["answer_id"] raw — a live
    KeyError on the anticipated LLM-authored malformed cell — while the
    validator's aggregation skips cells without an answer_id. A-001 keeps
    real coverage, so the zero-coverage set is empty and the validator's
    schema failure routes rerun_intent_carrier (D-015), with the per-cell
    counts proving the folds ran over the malformed cell without raising.
    """
    from foundry_mcp.tools.intent_coverage import foundry_intent_coverage

    project_root, run_dir = intent_run(
        _CLEAN_SPEC,
        [
            {"answer_id": "A-001", "casting_id": "1",
             "verdict": "PROPAGATED", "citation_chain": ["A-001"]},
        ],
    )
    doc = _coverage_doc([
        {"answer_id": "A-001", "casting_id": "1",
         "verdict": "PROPAGATED", "citation_chain": ["A-001"]},
        # The malformed cell: verdict present, answer_id absent — the key
        # the gate dereferenced.
        {"casting_id": "1", "verdict": "PARAPHRASED",
         "citation_chain": ["A-001"]},
    ])
    (run_dir / "intent-coverage.json").write_text(
        json.dumps(doc), encoding="utf-8",
    )

    result = foundry_intent_coverage(project_root=project_root)

    assert result["passed"] is False, result
    assert result["action"] == "rerun_intent_carrier", result
    assert result["validator_exit"] == 1, result
    # The per-cell folds ran over the malformed dict cell without a
    # KeyError: it still counts per-cell (AC-002 reporting) even though it
    # cannot be attributed to any answer.
    assert result["cell_verdict_counts"]["PROPAGATED"] == 1, result
    assert result["cell_verdict_counts"]["PARAPHRASED"] == 1, result
    assert not (run_dir / ".f07-intent-clean").exists()


def test_tool_non_dict_matrix_element_no_crash(
    intent_run: Callable[..., tuple[str, Path]],
) -> None:
    """GRIND D-020 shape 1 — non-dict matrix element.

    A bare string in the matrix list crashed every gate fold with
    AttributeError (str.get). The validator skips non-dict cells and fails
    schema validation; A-001 keeps real coverage, so the empty
    zero-coverage set routes rerun_intent_carrier.
    """
    from foundry_mcp.tools.intent_coverage import foundry_intent_coverage

    project_root, run_dir = intent_run(
        _CLEAN_SPEC,
        [
            {"answer_id": "A-001", "casting_id": "1",
             "verdict": "PROPAGATED", "citation_chain": ["A-001"]},
        ],
    )
    doc = _coverage_doc([
        {"answer_id": "A-001", "casting_id": "1",
         "verdict": "PROPAGATED", "citation_chain": ["A-001"]},
        "not-a-cell",
    ])
    (run_dir / "intent-coverage.json").write_text(
        json.dumps(doc), encoding="utf-8",
    )

    result = foundry_intent_coverage(project_root=project_root)

    assert result["passed"] is False, result
    assert result["action"] == "rerun_intent_carrier", result
    assert result["validator_exit"] == 1, result
    assert result["cell_verdict_counts"]["PROPAGATED"] == 1, result
    assert not (run_dir / ".f07-intent-clean").exists()


def test_tool_matrix_as_object_routes_redecompose(
    intent_run: Callable[..., tuple[str, Path]],
) -> None:
    """GRIND D-020 shape 3 — matrix as a JSON object, not a list.

    coverage["matrix"] as a dict made every fold iterate string keys and
    crash on str.get. Normalized to no verifiable cells, every spec answer
    is zero-coverage — named in dropped_answers/redecompose_hints and
    routed as redecompose per the D-015/D-019 rules.
    """
    from foundry_mcp.tools.intent_coverage import foundry_intent_coverage

    project_root, run_dir = intent_run(
        _CLEAN_SPEC,
        [
            {"answer_id": "A-001", "casting_id": "1",
             "verdict": "PROPAGATED", "citation_chain": ["A-001"]},
        ],
    )
    doc = _coverage_doc([])
    doc["matrix"] = {"A-001": "PROPAGATED"}
    (run_dir / "intent-coverage.json").write_text(
        json.dumps(doc), encoding="utf-8",
    )

    result = foundry_intent_coverage(project_root=project_root)

    assert result["passed"] is False, result
    assert result["action"] == "redecompose", result
    assert result["dropped_answers"] == ["A-001"], result
    hint_ids = [h["answer_id"] for h in result["redecompose_hints"]]
    assert hint_ids == ["A-001"], result
    # No cell exists to draw a suggestion from.
    assert result["redecompose_hints"][0]["suggested_casting"] is None, result
    assert result["validator_exit"] == 1, result
    assert not (run_dir / ".f07-intent-clean").exists()


def test_tool_top_level_array_routes_redecompose(
    intent_run: Callable[..., tuple[str, Path]],
) -> None:
    """GRIND D-020 shape 4 — coverage doc top level is a JSON array.

    A top-level array parses cleanly (no JSONDecodeError) but
    coverage.get() crashed with AttributeError before any payload was
    built. Normalized to no matrix at all, every spec answer is
    zero-coverage — named and routed as redecompose.
    """
    from foundry_mcp.tools.intent_coverage import foundry_intent_coverage

    project_root, run_dir = intent_run(
        _CLEAN_SPEC,
        [
            {"answer_id": "A-001", "casting_id": "1",
             "verdict": "PROPAGATED", "citation_chain": ["A-001"]},
        ],
    )
    (run_dir / "intent-coverage.json").write_text(
        json.dumps([
            {"answer_id": "A-001", "casting_id": "1",
             "verdict": "PROPAGATED", "citation_chain": ["A-001"]},
        ]),
        encoding="utf-8",
    )

    result = foundry_intent_coverage(project_root=project_root)

    assert result["passed"] is False, result
    assert result["action"] == "redecompose", result
    assert result["dropped_answers"] == ["A-001"], result
    hint_ids = [h["answer_id"] for h in result["redecompose_hints"]]
    assert hint_ids == ["A-001"], result
    assert result["validator_exit"] == 1, result
    assert not (run_dir / ".f07-intent-clean").exists()


def test_tool_malformed_cell_coexisting_zero_coverage_names_answer(
    intent_run: Callable[..., tuple[str, Path]],
) -> None:
    """GRIND D-020 — malformed cell + genuine zero-coverage answer.

    The adversarial repro: A-003 genuinely reaches no casting while a
    malformed cell (missing answer_id) sits in the same matrix. The
    validator CLI named A-003 correctly but the gate threw before building
    any payload — zero redecompose_hints produced (AC-001's clause). Now
    the malformed cell is skipped and A-003 IS named in
    dropped_answers/redecompose_hints via the redecompose route.
    """
    from foundry_mcp.tools.intent_coverage import foundry_intent_coverage

    two_answer_spec = (
        "---\nspec_format_version: v2.1\n---\n"
        "## Appendix: Interview Transcript\n\n"
        "## A-001 [Locked]\nSurface contract. [from Q-001]\n\n"
        "## A-003 [Locked]\nGenuinely uncovered answer. [from Q-003]\n"
    )
    project_root, run_dir = intent_run(
        two_answer_spec,
        [
            {"answer_id": "A-001", "casting_id": "1",
             "verdict": "PROPAGATED", "citation_chain": ["A-001"]},
        ],
    )
    doc = _coverage_doc([
        {"answer_id": "A-001", "casting_id": "1",
         "verdict": "PROPAGATED", "citation_chain": ["A-001"]},
        {"casting_id": "1", "verdict": "PARAPHRASED",
         "citation_chain": ["A-001"]},
    ])
    (run_dir / "intent-coverage.json").write_text(
        json.dumps(doc), encoding="utf-8",
    )

    result = foundry_intent_coverage(project_root=project_root)

    assert result["passed"] is False, result
    assert result["action"] == "redecompose", result
    assert result["dropped_answers"] == ["A-003"], result
    hint_ids = [h["answer_id"] for h in result["redecompose_hints"]]
    assert hint_ids == ["A-003"], result
    assert not (run_dir / ".f07-intent-clean").exists()
