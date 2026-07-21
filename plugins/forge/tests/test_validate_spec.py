"""Unit tests for plugins/forge/scripts/validate-spec.py implicit-fact checks.

Wave 0 (Plan 01-01) baseline: every test in this file MUST be RED initially.
The validator changes from Plan 03 turn them green.

Tests use the ``run_validator_subprocess`` fixture (CLI-level) where possible
to isolate from validator internals so Plan 03 can choose function names freely.
Two pure-regex tests (test_combined_tags_parse, test_a_auto_id_pattern) operate
on fixture text directly — no subprocess.

No tests use ``@pytest.mark.skip`` or ``xfail``. Failures here are the red
baseline by design.
"""

from __future__ import annotations

import re

# Mirror the existing ANSWER_BLOCK_RE from validate-spec.py:60-67.
# Inlining the regex keeps these tests independent of validate_spec import paths
# while Plan 03 work is in flight.
ANSWER_BLOCK_RE = re.compile(
    r"^##\s+(A-\d+)"
    r"(?:\s*\[([^\]]*)\])?"
    r"(?:\s*\(([^)]*)\))?"
    r"\s*\n(.*?)"
    r"(?=^##\s+[AQ]-\d+|^##\s+[A-Z]|\Z)",
    re.MULTILINE | re.DOTALL,
)


# -----------------------------------------------------------------------------
# Test 1: valid implicit-fact transcript exits 0 and emits no failures
# -----------------------------------------------------------------------------
def test_valid_implicit_fact_tag_passes(
    run_validator_subprocess, fixtures_dir, tmp_path
):
    spec_path = fixtures_dir / "spec-minimal.md"
    transcript_path = fixtures_dir / "transcript-valid-implicit.md"

    result = run_validator_subprocess(spec_path, transcript_path)

    assert result.returncode == 0, (
        f"Expected exit 0 on well-formed transcript; got {result.returncode}\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert "IMPLICIT_FACT_SKIPPED" not in result.stdout
    assert "UNKNOWN_IMPLICIT_FACT_CATEGORY" not in result.stdout
    assert "A_AUTO_MISSING_TAG" not in result.stdout
    assert "A_AUTO_MISSING_CITATION" not in result.stdout


# -----------------------------------------------------------------------------
# Test 2: legacy transcript (no IMPLICIT_FACT tags) emits warning, exits 0
# -----------------------------------------------------------------------------
def test_implicit_fact_skipped_warns(run_validator_subprocess, fixtures_dir):
    spec_path = fixtures_dir / "spec-minimal.md"
    transcript_path = fixtures_dir / "transcript-legacy-v2.0.md"

    result = run_validator_subprocess(spec_path, transcript_path)

    # Phase 1 contract: legacy transcripts WARN but do NOT fail (backwards-compat
    # per RESEARCH.md §Pitfall 5). Phase 3 will upgrade to hard-fail when
    # spec_format_version >= v2.1.
    assert result.returncode == 0, (
        f"Expected exit 0 (warning only) on legacy transcript; "
        f"got {result.returncode}\nstdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )
    combined = result.stdout + result.stderr
    assert "IMPLICIT_FACT_SKIPPED" in combined, (
        "Expected IMPLICIT_FACT_SKIPPED warning text in stdout/stderr; "
        f"combined output:\n{combined}"
    )


# -----------------------------------------------------------------------------
# Test 3: legacy transcript still validates (no-regression contract — INTV-01 #4)
# -----------------------------------------------------------------------------
def test_legacy_transcript_does_not_break(run_validator_subprocess, fixtures_dir):
    spec_path = fixtures_dir / "spec-minimal.md"
    transcript_path = fixtures_dir / "transcript-legacy-v2.0.md"

    result = run_validator_subprocess(spec_path, transcript_path)

    # Duplicates the exit-code assertion of test 2 by intent — keeps the
    # backwards-compat contract independently visible in the pytest report.
    assert result.returncode == 0, (
        f"INTV-01 #4 regression: legacy transcript must still validate; "
        f"got exit {result.returncode}\nstdout:\n{result.stdout}"
    )


# -----------------------------------------------------------------------------
# Test 4: unknown IMPLICIT_FACT category fails with exit 1
# -----------------------------------------------------------------------------
def test_unknown_category_fails(run_validator_subprocess, fixtures_dir):
    spec_path = fixtures_dir / "spec-minimal.md"
    transcript_path = fixtures_dir / "transcript-unknown-category.md"

    result = run_validator_subprocess(spec_path, transcript_path)

    assert result.returncode == 1, (
        f"Expected exit 1 on unknown category; got {result.returncode}\n"
        f"stdout:\n{result.stdout}"
    )
    assert "UNKNOWN_IMPLICIT_FACT_CATEGORY" in result.stdout, (
        f"Expected UNKNOWN_IMPLICIT_FACT_CATEGORY in stdout; got:\n{result.stdout}"
    )
    assert "GARBAGE" in result.stdout, (
        "Expected the offending category name 'GARBAGE' to surface in error "
        f"output; got:\n{result.stdout}"
    )


# -----------------------------------------------------------------------------
# Test 5: A-AUTO-NNN entry without [IMPLICIT_FACT:CATEGORY] tag fails
# -----------------------------------------------------------------------------
def test_a_auto_missing_tag_fails(run_validator_subprocess, fixtures_dir):
    spec_path = fixtures_dir / "spec-minimal.md"
    transcript_path = fixtures_dir / "transcript-a-auto-missing-tag.md"

    result = run_validator_subprocess(spec_path, transcript_path)

    assert result.returncode == 1, (
        f"Expected exit 1 on A-AUTO without tag; got {result.returncode}\n"
        f"stdout:\n{result.stdout}"
    )
    assert "A_AUTO_MISSING_TAG" in result.stdout, (
        f"Expected A_AUTO_MISSING_TAG in stdout; got:\n{result.stdout}"
    )
    assert "A-AUTO-001" in result.stdout, (
        f"Expected the offending ID 'A-AUTO-001' in stdout; got:\n{result.stdout}"
    )


# -----------------------------------------------------------------------------
# Test 6: A-AUTO-NNN entry without [from <source>] citation fails
# -----------------------------------------------------------------------------
def test_a_auto_missing_citation_fails(run_validator_subprocess, fixtures_dir):
    spec_path = fixtures_dir / "spec-minimal.md"
    transcript_path = fixtures_dir / "transcript-a-auto-missing-citation.md"

    result = run_validator_subprocess(spec_path, transcript_path)

    assert result.returncode == 1, (
        f"Expected exit 1 on A-AUTO without citation; got {result.returncode}\n"
        f"stdout:\n{result.stdout}"
    )
    assert "A_AUTO_MISSING_CITATION" in result.stdout, (
        f"Expected A_AUTO_MISSING_CITATION in stdout; got:\n{result.stdout}"
    )


# -----------------------------------------------------------------------------
# Test 7: A-AUTO-NNN entries are exempt from coverage check (UNCITED_ANSWERS)
# -----------------------------------------------------------------------------
def test_a_auto_exempt_from_coverage(run_validator_subprocess, fixtures_dir):
    spec_path = fixtures_dir / "spec-minimal.md"
    transcript_path = fixtures_dir / "transcript-valid-implicit.md"

    result = run_validator_subprocess(spec_path, transcript_path)

    # spec-minimal.md cites only A-001; A-AUTO-001 and A-AUTO-002 are NOT cited
    # in the spec body (auto-discovered context, not user requirements).
    # check_coverage must skip A-AUTO-NNN — RESEARCH.md §Open Questions #2.
    assert result.returncode == 0, (
        f"Expected exit 0 (A-AUTO exempt from coverage); "
        f"got {result.returncode}\nstdout:\n{result.stdout}"
    )
    assert "UNCITED_ANSWERS" not in result.stdout, (
        "A-AUTO-NNN must be exempt from UNCITED_ANSWERS check; "
        f"saw UNCITED_ANSWERS in output:\n{result.stdout}"
    )


# -----------------------------------------------------------------------------
# Test 8: combined-tags fixture parses both ARCH_INVARIANT and IMPLICIT_FACT:SECURITY
# -----------------------------------------------------------------------------
def test_combined_tags_parse(load_fixture):
    content = load_fixture("transcript-combined-tags.md")
    matches = list(ANSWER_BLOCK_RE.finditer(content))

    assert len(matches) >= 1, (
        f"Expected ≥1 A-NNN match in combined-tags fixture; got {len(matches)}"
    )

    # Find the A-001 match and inspect its tag string (group 2).
    a_001 = next((m for m in matches if m.group(1) == "A-001"), None)
    assert a_001 is not None, "Expected A-001 to be parsed"

    tag_blob = a_001.group(2) or ""
    tags = [t.strip() for t in tag_blob.split(",")]

    assert "ARCH_INVARIANT" in tags, (
        f"Expected 'ARCH_INVARIANT' in parsed tags; got {tags}"
    )
    assert "IMPLICIT_FACT:SECURITY" in tags, (
        f"Expected 'IMPLICIT_FACT:SECURITY' in parsed tags; got {tags}"
    )


# -----------------------------------------------------------------------------
# Test 9: A-AUTO-NNN heading pattern is parseable by Plan 03's regex
# -----------------------------------------------------------------------------
def test_a_auto_id_pattern(load_fixture):
    content = load_fixture("transcript-a-auto-missing-tag.md")

    # Plan 03 will introduce a parallel A-AUTO-NNN regex; this test sanity-
    # checks that the fixture format is what that regex needs to match.
    a_auto_re = re.compile(r"^##\s+(A-AUTO-\d+)", re.MULTILINE)
    matches = list(a_auto_re.finditer(content))

    assert len(matches) >= 1, (
        f"Expected ≥1 A-AUTO-NNN match in fixture; got {len(matches)}\n"
        f"Plan 03's parallel regex will rely on this heading shape."
    )


# -----------------------------------------------------------------------------
# Test 10: warning text mentions Phase 3 / spec_format_version / TYPE-02 coordination
# -----------------------------------------------------------------------------
def test_validator_emits_specific_warning_text_for_skip(
    run_validator_subprocess, fixtures_dir
):
    spec_path = fixtures_dir / "spec-minimal.md"
    transcript_path = fixtures_dir / "transcript-legacy-v2.0.md"

    result = run_validator_subprocess(spec_path, transcript_path)

    # RESEARCH.md §Pitfall 5: the warning message should tell the operator
    # that this check becomes a failure under spec_format_version v2.1 / Phase 3.
    combined = result.stdout + result.stderr
    has_coordination_text = (
        "spec_format_version" in combined
        or "Phase 3" in combined
        or "TYPE-02" in combined
    )
    assert has_coordination_text, (
        "Expected warning text to mention 'spec_format_version' OR 'Phase 3' "
        f"OR 'TYPE-02' (Phase 1 ↔ Phase 3 coordination signal); "
        f"got:\n{combined}"
    )
