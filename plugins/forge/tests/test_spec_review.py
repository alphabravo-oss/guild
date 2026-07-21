"""Phase 6 / PROBE-01 — adversarial spec reviewer validator tests.

Mirrors Phase 4 plugins/foundry/mcp-server/tests/test_evidence.py +
Phase 2 plugins/forge/tests/test_typed_sections.py + Phase 3
test_versioned_spec_format.py module-isolation discipline. RED until
Plan 06-02 ships ``validate_spec_review.py``; agent-existence +
setup-forge R3.5 + plan.md PHASE EXECUTION ORDER tests SKIP until
Plan 06-03 lands those artifacts.

Module-level ``pytest.skip(allow_module_level=True)`` short-circuits
the entire suite when ``validate_spec_review.py`` is absent — keeps
Plan 06-01's RED baseline noise-free at collection time. Once Plan
06-02 ships the script, the seven validator-behavior tests turn
RED-or-GREEN immediately. Once Plan 06-03 ships the agent file +
setup-forge R3.5 heredoc + plan.md PHASE EXECUTION ORDER entry,
the three conditional-skip stubs auto-flip to GREEN with no edit
to this file.

Phase 1+2+3+4+5 byte-equivalence is preserved by living in a NEW
module — no edits to test_validate_spec.py / test_typed_sections.py /
test_versioned_spec_format.py / test_setup_forge_smoke.py /
test_foundry_decompose_propagation.py / test_versioned_alignment.py.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

# Resolve script path. ``validate_spec_review.py`` lives at
# plugins/forge/scripts/validate_spec_review.py and is invoked via
# subprocess (Phase 1 Plan 01-03 precedent: dash-named filenames are
# not valid Python identifiers; this script uses underscore form but we
# still invoke it via subprocess for parity with validate-spec.py's
# subprocess invocation pattern in conftest.run_validator_subprocess).
SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "validate_spec_review.py"
)

if not SCRIPT.exists():
    pytest.skip(
        "validate_spec_review.py not yet shipped (Plan 06-02 territory)",
        allow_module_level=True,
    )


def _run_validator(
    review_path: Path, transcript_path: Path
) -> subprocess.CompletedProcess:
    """Invoke validate_spec_review.py via subprocess.

    Mirrors conftest.run_validator_subprocess shape — Phase 6's harness
    is local to this module because the validator surface is
    self-contained (no synthesized-spec rebuild needed; the review JSON
    + transcript are passed verbatim).
    """
    return subprocess.run(
        ["python3", str(SCRIPT), str(review_path), str(transcript_path)],
        capture_output=True,
        text=True,
        timeout=30,
    )


# ---------------------------------------------------------------------------
# Plan 06-02 territory — validator behavior tests (RED until 06-02 ships)
# ---------------------------------------------------------------------------


def test_validate_spec_review_pass_returns_zero(fixtures_dir):
    """Happy path — pass-shape fixture exits 0."""
    result = _run_validator(
        fixtures_dir / "spec_review_pass.json",
        fixtures_dir / "transcript_probe_sloppy.md",
    )
    assert result.returncode == 0, (
        f"expected exit 0 on pass fixture; got {result.returncode}\n"
        f"stdout: {result.stdout}\n"
        f"stderr: {result.stderr}"
    )


def test_advisory_mode_rejected(fixtures_dir):
    """Pitfall 3 — verdict=pass with non-empty flags is forbidden.

    Validator must treat any flag as a blocker (binary block/pass
    discipline). Advisory-mode review JSON ships verdict=pass while
    carrying a flag; validator rejects.
    """
    result = _run_validator(
        fixtures_dir / "spec_review_advisory_mode.json",
        fixtures_dir / "transcript_probe_sloppy.md",
    )
    assert result.returncode == 1, (
        f"expected exit 1 on advisory-mode fixture; got {result.returncode}\n"
        f"stdout: {result.stdout}"
    )
    assert "verdict must be 'block'" in result.stdout, (
        f"expected 'verdict must be \\'block\\'' substring in stdout; "
        f"got: {result.stdout}"
    )


def test_uncited_flag_rejected(fixtures_dir):
    """Pitfall 1 — flag with empty citation field is auto-rejected."""
    result = _run_validator(
        fixtures_dir / "spec_review_uncited.json",
        fixtures_dir / "transcript_probe_sloppy.md",
    )
    assert result.returncode == 1, (
        f"expected exit 1 on uncited-flag fixture; got {result.returncode}\n"
        f"stdout: {result.stdout}"
    )
    assert "no citation" in result.stdout, (
        f"expected 'no citation' substring in stdout; got: {result.stdout}"
    )


def test_dangling_citation_rejected(fixtures_dir):
    """Pitfall 1 — flag citing an A-NNN absent from the transcript fails.

    A-999 is guaranteed absent from transcript_probe_sloppy.md (which
    carries A-001..A-008). Validator's citation-resolves-against-
    transcript check rejects.
    """
    result = _run_validator(
        fixtures_dir / "spec_review_dangling_citation.json",
        fixtures_dir / "transcript_probe_sloppy.md",
    )
    assert result.returncode == 1, (
        f"expected exit 1 on dangling-citation fixture; "
        f"got {result.returncode}\n"
        f"stdout: {result.stdout}"
    )
    assert "not in transcript" in result.stdout, (
        f"expected 'not in transcript' substring in stdout; "
        f"got: {result.stdout}"
    )


def test_flag_budget_ceiling(fixtures_dir):
    """Pitfall 4 — flag_count > MAX_FLAGS=5 rejected at the budget gate."""
    result = _run_validator(
        fixtures_dir / "spec_review_over_budget.json",
        fixtures_dir / "transcript_probe_sloppy.md",
    )
    assert result.returncode == 1, (
        f"expected exit 1 on over-budget fixture; got {result.returncode}\n"
        f"stdout: {result.stdout}"
    )
    assert "Flag budget exceeded" in result.stdout, (
        f"expected 'Flag budget exceeded' substring in stdout; "
        f"got: {result.stdout}"
    )


def test_order_violation_blocks(fixtures_dir):
    """Pitfall 5 — reviewer_order_violation=true forces immediate block.

    Independent of flags array (zero flags + order violation still
    blocks).
    """
    result = _run_validator(
        fixtures_dir / "spec_review_order_violation.json",
        fixtures_dir / "transcript_probe_sloppy.md",
    )
    assert result.returncode == 1, (
        f"expected exit 1 on order-violation fixture; "
        f"got {result.returncode}\n"
        f"stdout: {result.stdout}"
    )
    assert "REVIEWER_ORDER_VIOLATION" in result.stdout, (
        f"expected 'REVIEWER_ORDER_VIOLATION' substring in stdout; "
        f"got: {result.stdout}"
    )


def test_unknown_keys_rejected(fixtures_dir):
    """Pitfall 2 — extra top-level key (e.g. ``suggested_fix``) is rejected.

    Closed-vocabulary schema discipline: KNOWN_REVIEW_KEYS frozenset
    enumerates the only legal keys; anything else is a hard reject so
    reviewers cannot smuggle auto-resolve fields into the JSON.
    """
    result = _run_validator(
        fixtures_dir / "spec_review_unknown_keys.json",
        fixtures_dir / "transcript_probe_sloppy.md",
    )
    assert result.returncode == 1, (
        f"expected exit 1 on unknown-keys fixture; got {result.returncode}\n"
        f"stdout: {result.stdout}"
    )
    assert "Unknown keys" in result.stdout, (
        f"expected 'Unknown keys' substring in stdout; got: {result.stdout}"
    )


def test_known_review_keys_is_closed_vocabulary():
    """KNOWN_REVIEW_KEYS frozenset enumerates exactly 5 review keys.

    Source-grep test: imports the script's text and asserts the five
    canonical review keys appear inside a KNOWN_REVIEW_KEYS-shaped
    block. Plan 06-02 ships the actual frozenset; this test is the
    grep-discoverable contract that locks it.

    Per-flag keys (id, citation, typed_row, ambiguity) live in a
    separate KNOWN_FLAG_KEYS frozenset — not asserted here so Plan
    06-02 owns the per-flag schema independently.
    """
    src = SCRIPT.read_text()
    assert "KNOWN_REVIEW_KEYS" in src, (
        "validate_spec_review.py must declare a KNOWN_REVIEW_KEYS "
        "frozenset (closed-vocabulary discipline)"
    )
    for key in (
        "review_version",
        "verdict",
        "flag_count",
        "flags",
        "reviewer_order_violation",
    ):
        assert key in src, (
            f"KNOWN_REVIEW_KEYS must enumerate '{key}'; "
            f"missing from validate_spec_review.py"
        )


# ---------------------------------------------------------------------------
# Plan 06-03 territory — agent file / setup-forge / plan.md (SKIP until 06-03)
# ---------------------------------------------------------------------------
#
# Conditional-skip pattern: tests skip when the artifact is missing,
# assert when it exists. Plan 06-03 ships the agent file + setup-forge
# R3.5 heredoc + plan.md PHASE EXECUTION ORDER entry; tests auto-flip
# from SKIP to GREEN with zero edits to this module.


def test_spec_reviewer_agent_exists():
    """Plan 06-03 ships plugins/forge/agents/spec-reviewer.md.

    Frontmatter must declare ``id: PROBE-01`` and
    ``min_spec_format_version: v2.1`` so Plan 03-04's F0.5 V2 step 2b
    roster automatically routes the agent on legacy v2.0 specs.
    """
    agent = (
        Path(__file__).resolve().parents[1]
        / "agents"
        / "spec-reviewer.md"
    )
    if not agent.exists():
        pytest.skip(
            "Plan 06-03 territory: spec-reviewer.md not yet shipped"
        )
    text = agent.read_text()
    assert "id: PROBE-01" in text, (
        "spec-reviewer.md frontmatter must declare 'id: PROBE-01'"
    )
    assert "min_spec_format_version: v2.1" in text, (
        "spec-reviewer.md frontmatter must declare "
        "'min_spec_format_version: v2.1' (so v2.0 specs auto-skip)"
    )


def test_setup_forge_emits_r35_phase(run_setup_forge):
    """Plan 06-03 inserts R3.5 PROBE / spec-review.json into setup-forge.sh.

    The assembled R0..R4 prompt must reference the new R3.5 phase
    inline so Foundry's R-phase orchestration knows about the
    adversarial reviewer step. Conditional-skip until Plan 06-03 lands
    the heredoc edit.
    """
    result = run_setup_forge("phase6-r35-probe", "--no-survey")
    if result.process.returncode != 0:
        pytest.skip(
            "Plan 06-03 territory: setup-forge.sh exited non-zero "
            "(likely because R3.5 heredoc not yet inserted)"
        )
    if "PHASE R3.5" not in result.prompt_text:
        pytest.skip(
            "Plan 06-03 territory: 'PHASE R3.5' token not yet "
            "in setup-forge.sh assembled prompt"
        )
    assert "spec-review.json" in result.prompt_text, (
        "setup-forge.sh R3.5 block must reference 'spec-review.json' "
        "(the artifact name validate_spec_review.py consumes)"
    )


def test_plan_md_has_r35_entry():
    """Plan 06-03 adds R3.5 / PROBE-01 to plan.md PHASE EXECUTION ORDER.

    Mirrors Phase 1 Plan 01-04's R1.75 IMPLICIT-FACT EXTRACTION
    insertion and Phase 2 Plan 02-02's step 2.5 FINALIZATION SEQUENCE
    insertion: stable-identity discipline preserved by inserting at
    a sub-numbered slot rather than renumbering.
    """
    plan_md = (
        Path(__file__).resolve().parents[1].parent
        / "forge"
        / "commands"
        / "plan.md"
    )
    if not plan_md.exists():
        pytest.skip(
            "Plan 06-03 territory: plugins/forge/commands/plan.md "
            "not present at expected path"
        )
    text = plan_md.read_text()
    if "R3.5" not in text:
        pytest.skip(
            "Plan 06-03 territory: 'R3.5' not yet added to plan.md "
            "PHASE EXECUTION ORDER section"
        )
    assert "PROBE-01" in text, (
        "plan.md PHASE EXECUTION ORDER R3.5 row must reference "
        "'PROBE-01' (the requirement ID)"
    )
