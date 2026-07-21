"""Phase 2 RED stubs for plugins/forge/scripts/validate-spec.py check_typed_sections.

Wave 0 (Plan 02-01) baseline: every test in this file MUST be RED initially.
The validator changes from Plan 02-03 turn them green. The setup-forge.sh /
plan.md changes from Plan 02-02 (column layout) provide the SPEC TEMPLATE
this validator inspects.

Tests use the ``run_typed_validator_subprocess`` fixture (CLI-level subprocess)
to isolate from validator internals so Plan 02-03 can choose internal function
names freely.

Phase 2 vs Phase 1 isolation: Phase 1 tests live in test_validate_spec.py.
Phase 2 tests live HERE. DO NOT extend test_validate_spec.py — keeping Phase 2
tests in their own file makes Phase 3's `warn`→`fail` upgrade tests easier to
add cleanly (CONTEXT.md "Test infrastructure" section).

No tests use ``@pytest.mark.skip`` or ``xfail`` (the optional
``pytest.importorskip`` for the Jaccard tokenizer unit test is the SOLE
permitted exception, per Plan 02-01 action narrative). Failures here are the
red baseline by design.
"""

from __future__ import annotations


# -----------------------------------------------------------------------------
# Test 1: complete typed-spec — all three tables populated, all rules pass
# -----------------------------------------------------------------------------
def test_complete_typed_spec_passes(run_typed_validator_subprocess):
    """TYPE-01 #1/#2/#3 happy path — three populated tables, all rows verbatim.

    Until Plan 02-03 ships ``check_typed_sections`` this test may PASS
    incidentally (validator is silent on typed sections). The assertion that
    none of the typed-section diagnostic tokens appear is the RED hook —
    when 02-03 starts checking and the synthesis is correct, the assertion
    continues to hold; if synthesis is broken, this surfaces it.
    """
    result = run_typed_validator_subprocess("transcript_typed_complete")

    assert result.returncode == 0, (
        f"Expected exit 0 on complete typed spec; got {result.returncode}\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    combined = result.stdout + result.stderr
    for token in (
        "MISSING_TYPED_SECTION",
        "TYPED_SECTION_MALFORMED",
        "TYPED_ROW_BAD_CITATION",
        "TYPED_ROW_DANGLING",
        "TYPED_ROW_NOT_VERBATIM",
        "TYPED_ROW_PARAPHRASE",
        "TYPE_TABLES_MISSING",
    ):
        assert token not in combined, (
            f"Did not expect typed-section diagnostic '{token}' on complete "
            f"fixture; got:\n{combined}"
        )


# -----------------------------------------------------------------------------
# Test 2: missing typed sections WARN (not fail) on legacy v4.2.0 spec
# -----------------------------------------------------------------------------
def test_missing_typed_section_warns(run_typed_validator_subprocess):
    """TYPE-01 #4 — pre-Phase-2 specs (no typed tables) WARN with TYPE_TABLES_MISSING
    but do NOT fail (backwards-compat per CONTEXT.md "Validator enforcement").
    Phase 3 (TYPE-02) upgrades this to a hard fail when spec_format_version >= v2.1.
    """
    result = run_typed_validator_subprocess(
        "transcript_typed_legacy_v420", with_typed_tables=False
    )

    assert result.returncode == 0, (
        f"Expected exit 0 on legacy spec (warning, not failure); "
        f"got {result.returncode}\nstdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )
    combined = result.stdout + result.stderr
    assert "TYPE_TABLES_MISSING" in combined, (
        "Expected TYPE_TABLES_MISSING warning text in stdout/stderr "
        "(check_typed_sections rule 1 should warn on missing typed tables); "
        f"combined output:\n{combined}"
    )


# -----------------------------------------------------------------------------
# Test 3: warning text mentions Phase 3 / spec_format_version / TYPE-02 coordination
# -----------------------------------------------------------------------------
def test_typed_warning_text_mentions_phase3_coordination(
    run_typed_validator_subprocess,
):
    """RESEARCH.md Pitfall 3 — Phase 2 ↔ Phase 3 hand-off discipline.

    The warning text MUST contain all three coordination tokens so Phase 3's
    grep finds the upgrade site without ambiguity. Mirrors Phase 1's
    IMPLICIT_FACT_SKIPPED template.
    """
    result = run_typed_validator_subprocess(
        "transcript_typed_legacy_v420", with_typed_tables=False
    )

    combined = result.stdout + result.stderr
    assert "TYPE_TABLES_MISSING" in combined, (
        f"Expected TYPE_TABLES_MISSING token; got:\n{combined}"
    )
    assert "spec_format_version" in combined, (
        "Phase 3 hand-off requires the warning text to mention "
        "'spec_format_version' (the version frontmatter Phase 3 introduces); "
        f"got:\n{combined}"
    )
    assert "TYPE-02" in combined, (
        "Phase 3 hand-off requires the warning text to mention 'TYPE-02' "
        "(Phase 3 requirement ID); "
        f"got:\n{combined}"
    )


# -----------------------------------------------------------------------------
# Test 4: dangling typed citation (row cites A-999) fails with TYPED_ROW_DANGLING
# -----------------------------------------------------------------------------
def test_dangling_typed_citation_fails(run_typed_validator_subprocess):
    """TYPE-01 #2 (citation integrity) — every row's [from A-NNN] must resolve.

    Phase 1's ``check_dangling_refs`` already catches generic A-999
    references with ``DANGLING_CITATION``; Phase 2's
    ``check_typed_sections`` rule 2 must additionally surface
    ``TYPED_ROW_DANGLING`` (row-scoped, includes the table where the
    dangling reference lives) so PROBE-01 / INTENT-01 can grep it
    independently from the generic dangling check.
    """
    result = run_typed_validator_subprocess(
        "transcript_typed_dangling_citation", inject_dangling_citation=True
    )

    assert result.returncode == 1, (
        f"Expected exit 1 on dangling typed citation; "
        f"got {result.returncode}\nstdout:\n{result.stdout}"
    )
    assert "TYPED_ROW_DANGLING" in result.stdout, (
        "Expected TYPED_ROW_DANGLING token in stdout (Phase 2 rule 2); "
        f"got:\n{result.stdout}"
    )
    assert "A-999" in result.stdout, (
        "Expected the dangling A-NNN id 'A-999' to surface in error output; "
        f"got:\n{result.stdout}"
    )


# -----------------------------------------------------------------------------
# Test 5: row whose tokens overlap section prose at Jaccard >=0.7 fails
# -----------------------------------------------------------------------------
def test_typed_row_paraphrase_fails(run_typed_validator_subprocess):
    """TYPE-01 #2 (rule 3 — content-difference / Jaccard).

    Fixture transcript_typed_paraphrase_violation pairs with a synthesized
    spec emitting a prose paragraph adjacent to the invariants table whose
    tokens overlap the row's content cells at Jaccard 8/11 = 0.727 ≥ 0.7
    (manual computation in the fixture header). Validator must FAIL with
    TYPED_ROW_PARAPHRASE.
    """
    result = run_typed_validator_subprocess(
        "transcript_typed_paraphrase_violation", inject_paraphrase=True
    )

    assert result.returncode == 1, (
        f"Expected exit 1 on paraphrased typed row (Jaccard ≥0.7); "
        f"got {result.returncode}\nstdout:\n{result.stdout}"
    )
    assert "TYPED_ROW_PARAPHRASE" in result.stdout, (
        "Expected TYPED_ROW_PARAPHRASE token in stdout (Phase 2 rule 3); "
        f"got:\n{result.stdout}"
    )


# -----------------------------------------------------------------------------
# Test 6: sentinel rows are EXEMPT from rule-3 Jaccard check
# -----------------------------------------------------------------------------
def test_sentinel_row_exempt_from_jaccard(run_typed_validator_subprocess):
    """CONTEXT.md Pitfall 2 — sentinel rows skip rule-3 Jaccard.

    Fixture transcript_typed_state_empty pairs with a synthesized spec where
    State Transitions has the documented sentinel row. The sentinel reasoning
    often paraphrases adjacent prose; rule 3 must explicitly exempt sentinel
    rows so they do not spuriously trip TYPED_ROW_PARAPHRASE.
    """
    result = run_typed_validator_subprocess(
        "transcript_typed_state_empty", state_transitions_sentinel=True
    )

    assert result.returncode == 0, (
        f"Expected exit 0 on state-transitions-sentinel fixture (rule 3 "
        f"must exempt sentinel rows); got {result.returncode}\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert "TYPED_ROW_PARAPHRASE" not in result.stdout, (
        "Sentinel rows must be exempt from rule 3 (Jaccard); "
        f"got TYPED_ROW_PARAPHRASE in:\n{result.stdout}"
    )


# -----------------------------------------------------------------------------
# Test 7: legacy v4.2.0 transcript validates (no-regression contract)
# -----------------------------------------------------------------------------
def test_legacy_v420_warns_not_fails(run_typed_validator_subprocess):
    """TYPE-01 #4 (backwards-compat / no-regression).

    Duplicates test 2's exit-code assertion intentionally so the "fails at
    SPEC FORGED" success-criterion check stays independently visible. A
    Phase 2 spec with no typed tables MUST still validate as long as it
    declares spec_format_version <= v2.0; Phase 3 upgrades to fail when
    spec_format_version >= v2.1.
    """
    result = run_typed_validator_subprocess(
        "transcript_typed_legacy_v420", with_typed_tables=False
    )

    assert result.returncode == 0, (
        f"TYPE-01 #4 regression: legacy v4.2.0 spec must still validate "
        f"in Phase 2; got exit {result.returncode}\nstdout:\n{result.stdout}"
    )
    combined = result.stdout + result.stderr
    # WARNING(s) must be present (legacy spec triggers TYPE_TABLES_MISSING and
    # IMPLICIT_FACT_SKIPPED at minimum). Do not assert specific text here —
    # that is test 3's job.
    assert "WARNING" in combined or "warn" in combined.lower(), (
        "Legacy fixture should emit at least one warning; "
        f"got:\n{combined}"
    )


# -----------------------------------------------------------------------------
# Test 8: composite — dangling typed citation makes validator exit non-zero
# -----------------------------------------------------------------------------
def test_spec_with_dangling_typed_citation_exits_nonzero(
    run_typed_validator_subprocess,
):
    """TYPE-01 #4 success-criterion check (composite).

    Duplicates test 4's exit-code assertion intentionally so the "fails at
    SPEC FORGED" criterion is independently visible from the diagnostic-token
    assertion. The criterion is "validator FAILS at SPEC FORGED on a spec
    with a dangling typed citation"; this test asserts only the exit code,
    leaving token assertions to test 4.
    """
    result = run_typed_validator_subprocess(
        "transcript_typed_dangling_citation", inject_dangling_citation=True
    )

    assert result.returncode != 0, (
        "TYPE-01 #4: a spec with a dangling typed-row citation must fail "
        f"validate-spec.py at SPEC FORGED; got exit {result.returncode}\n"
        f"stdout:\n{result.stdout}"
    )


# -----------------------------------------------------------------------------
# Test 9 (optional, supplementary): Jaccard tokenizer consistency unit test
# -----------------------------------------------------------------------------
def test_jaccard_tokenizer_consistency():
    """Pure-Python unit test against the EXTRACTED Jaccard helper Plan 02-03
    will create.

    This is the SOLE test in the Phase 2 suite that uses
    ``pytest.importorskip``. The rationale (per Plan 02-01 narrative): the
    tokenizer helper does not yet exist. Once Plan 02-03 ships
    ``check_typed_sections`` plus the ``_tokenize`` and ``_jaccard`` helpers,
    this test exercises them directly with hand-crafted strings whose Jaccard
    score is known by manual computation.

    Manual Jaccard: "the quick brown fox" vs "the quick brown wolf"
      Stop-words removed: {quick, brown, fox} vs {quick, brown, wolf}
      Intersection: {quick, brown} = 2
      Union:        {quick, brown, fox, wolf} = 4
      Jaccard = 2/4 = 0.5

    validate-spec.py is dash-named (not import-able) — Plan 02-03 must
    expose the helpers in an import-able shape (e.g., extract to a sibling
    ``validate_typed_sections.py`` module) for this test to run. Until that
    shape exists, ``pytest.importorskip`` SKIPs this test cleanly.
    """
    import pytest

    typed_helpers = pytest.importorskip(
        "validate_typed_sections",
        reason=(
            "Plan 02-03 has not yet extracted check_typed_sections into an "
            "import-able module. This test is the SOLE Plan 02-01 stub "
            "permitted to use importorskip — see Plan 02-01 action narrative."
        ),
    )

    tokenize = typed_helpers._tokenize
    jaccard = typed_helpers._jaccard

    a = tokenize("the quick brown fox")
    b = tokenize("the quick brown wolf")

    # Stop-word removal should drop "the" from both.
    assert "the" not in a
    assert "the" not in b
    assert {"quick", "brown", "fox"}.issubset(a)
    assert {"quick", "brown", "wolf"}.issubset(b)

    # Jaccard = 2 / 4 = 0.5
    score = jaccard(a, b)
    assert abs(score - 0.5) < 1e-9, (
        f"Expected Jaccard 0.5 for the canonical fox/wolf pair; got {score}"
    )
