"""Phase 3 RED stubs for plugins/forge/scripts/validate-spec.py — TYPE-02 versioned spec format.

Wave 0 (Plan 03-01) baseline: every test in this file is RED-or-SKIP initially.

Test ownership map (per 03-VALIDATION.md Per-Task Verification Map):

  Plan 03-02 turns the parser/template tests green:
    - test_no_frontmatter_defaults_v20         (validator parses missing frontmatter as v2.0)
    - test_explicit_v20_warns_only             (explicit v2.0 ≡ missing frontmatter — both warn-only)

  Plan 03-03 turns the warn→fail upgrade tests green:
    - test_v21_complete_passes                 (v2.1 happy path — no diagnostics fire)
    - test_v21_missing_typed_hard_fails        (v2.1 + missing typed tables → FAIL)
    - test_v21_missing_implicit_hard_fails     (v2.1 + missing IMPLICIT_FACT tags → FAIL)
    - test_unknown_version_hard_fails          (v9.0 not in allowlist → FAIL)

  Plan 03-04 turns the F0.5 / F0.9 tests green (currently SKIP via fixture):
    - test_f05_emits_stream_skip_for_legacy_spec
    - test_f05_emits_empty_stream_skips_for_modern
    - test_stream_skip_record_schema
    - test_f09_subcheck_7k_catches_missing
    - test_f09_subcheck_7k_catches_unexpected

Phase 1 vs Phase 2 vs Phase 3 isolation: each phase keeps its own test
module so Phase 3's warn→fail upgrade is a single-file change at validate-
spec.py and a single-file test surface here. Phase 1 lives in
test_validate_spec.py; Phase 2 lives in test_typed_sections.py. Do NOT
extend either of those — append to this module instead.

Success-criteria (SC) mapping:
  - SC#1 (versioned validation lands cleanly): tests 1-6
  - SC#2 (backwards-compat for v2.0 path): tests 1, 2
  - SC#3 (v2.1 spec validates end-to-end): test 3
  - SC#4 (stream-skip plumbing for F0.5 / F0.9): tests 7-11

Plan 03-01 leaves tests 1-6 RED (assertions fail until production code lands)
and tests 7-11 SKIP (run_f05_decompose_with_test_roster raises pytest.skip).
This is the Wave 0 baseline.
"""

from __future__ import annotations

import re
from pathlib import Path


# -----------------------------------------------------------------------------
# Plan 03-02 ownership: parser + template tests
# -----------------------------------------------------------------------------


def test_no_frontmatter_defaults_v20(run_versioned_validator_subprocess):
    """SC#2 — a transcript with no frontmatter defaults to implicit v2.0.

    The legacy fixture has no `<!-- spec_format_version: ... -->` comment, so
    the conftest fixture (called with `spec_format_version=None`) writes a
    synthesized spec with NO frontmatter block at all. Plan 03-02's
    frontmatter parser must default missing frontmatter to v2.0; under v2.0,
    Phase 1's IMPLICIT_FACT_SKIPPED stays warn-not-fail (returncode 0) and
    Phase 2's TYPE_TABLES_MISSING stays warn-not-fail too — the legacy
    backwards-compat contract is preserved.
    """
    result = run_versioned_validator_subprocess(
        "transcript_versioned_legacy",
        spec_format_version=None,
        with_typed_tables=False,
    )

    assert result.returncode == 0, (
        f"Implicit-v2.0 path must remain warn-only (no fail); "
        f"got returncode {result.returncode}\nstdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )
    combined = result.stdout + result.stderr
    assert "IMPLICIT_FACT_SKIPPED" in combined, (
        "Phase 1's IMPLICIT_FACT_SKIPPED warning must still fire under "
        "implicit v2.0 (legacy fixture has no [IMPLICIT_FACT:*] tags); "
        f"combined output:\n{combined}"
    )
    # The warning must be a WARNING — not a fail token.
    assert "WARNING" in combined or "warn" in combined.lower(), (
        "IMPLICIT_FACT_SKIPPED should remain a warning under implicit v2.0; "
        f"combined output:\n{combined}"
    )


def test_explicit_v20_warns_only(run_versioned_validator_subprocess):
    """SC#2 — explicit `spec_format_version: v2.0` is equivalent to missing
    frontmatter (both default to v2.0 semantics).

    Same fixture and expectations as test_no_frontmatter_defaults_v20, but
    with `spec_format_version="v2.0"` passed explicitly. Validates that
    Plan 03-02's parser treats explicit and implicit v2.0 identically.
    """
    result = run_versioned_validator_subprocess(
        "transcript_versioned_legacy",
        spec_format_version="v2.0",
        with_typed_tables=False,
    )

    assert result.returncode == 0, (
        f"Explicit v2.0 must remain warn-only (no fail); "
        f"got returncode {result.returncode}\nstdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )
    combined = result.stdout + result.stderr
    assert "IMPLICIT_FACT_SKIPPED" in combined, (
        "Explicit v2.0 must preserve Phase 1 warning semantics; "
        f"combined output:\n{combined}"
    )


# -----------------------------------------------------------------------------
# Plan 03-03 ownership: warn→fail upgrade + unknown-version reject
# -----------------------------------------------------------------------------


def test_v21_complete_passes(run_versioned_validator_subprocess):
    """SC#1 + SC#3 — v2.1 happy path.

    The modern fixture declares v2.1 and has typed-table-grade content with
    [IMPLICIT_FACT:*] tags. The synthesizer emits typed tables (default
    `with_typed_tables=True`). Under v2.1: rule 1 (typed-table presence)
    passes, rule 2 (citation integrity) passes, rule 3 (Jaccard) passes,
    Phase 1's IMPLICIT_FACT check passes. No Phase 3 warn→fail upgrades
    fire. Returncode == 0; none of the diagnostic tokens appear.
    """
    result = run_versioned_validator_subprocess(
        "transcript_versioned_modern",
        spec_format_version="v2.1",
    )

    assert result.returncode == 0, (
        f"v2.1 happy path must validate cleanly; "
        f"got returncode {result.returncode}\nstdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )
    combined = result.stdout + result.stderr
    for token in (
        "TYPE_TABLES_MISSING",
        "IMPLICIT_FACT_SKIPPED",
        "SPEC_FORMAT_VERSION_UNKNOWN",
    ):
        assert token not in combined, (
            f"v2.1 happy path must not surface '{token}'; got:\n{combined}"
        )


def test_v21_missing_typed_hard_fails(run_versioned_validator_subprocess):
    """SC#1 + SC#4 — v2.1 + missing typed tables = HARD FAIL.

    Plan 03-03 wires `TYPE_TABLES_MISSING` to `report.fail()` when the spec
    declares `spec_format_version >= v2.1`. Under v2.0 this token remains
    a warning (Phase 2 backwards-compat contract).

    The fixture declares v2.1 and has [IMPLICIT_FACT:*] tags so Phase 1's
    check passes. Synthesizer is called with `with_typed_tables=False` so
    the typed tables are absent. Expected: FAIL + non-zero exit.
    """
    result = run_versioned_validator_subprocess(
        "transcript_versioned_v21_missing_typed",
        spec_format_version="v2.1",
        with_typed_tables=False,
    )

    assert result.returncode != 0, (
        f"v2.1 + missing typed tables must FAIL; "
        f"got returncode {result.returncode}\nstdout:\n{result.stdout}"
    )
    combined = result.stdout + result.stderr
    assert "TYPE_TABLES_MISSING" in combined, (
        "Phase 3 warn→fail upgrade must surface TYPE_TABLES_MISSING in "
        f"failure output; got:\n{combined}"
    )
    # FAIL indicator: validate-spec.py prints `FAIL: ` or similar; accept
    # any of the common shapes used elsewhere in the suite.
    assert (
        "FAIL" in combined
        or "ERROR" in combined
        or "failed" in combined.lower()
    ), (
        "Expected a FAIL/ERROR indicator in non-zero exit output; "
        f"got:\n{combined}"
    )


def test_v21_missing_implicit_hard_fails(run_versioned_validator_subprocess):
    """SC#1 — v2.1 + missing IMPLICIT_FACT tags = HARD FAIL.

    Plan 03-03 wires `IMPLICIT_FACT_SKIPPED` to `report.fail()` when the
    spec declares `spec_format_version >= v2.1`. Under v2.0 this token
    remains a warning (Phase 1 backwards-compat contract).

    The fixture has typed-table-grade content and [IMPLICIT_FACT:*] tags;
    the conftest fixture's `with_implicit_fact_tags=False` kwarg strips
    those tags before synthesis, isolating the negative signal to
    IMPLICIT_FACT_SKIPPED. Expected: FAIL + non-zero exit.
    """
    result = run_versioned_validator_subprocess(
        "transcript_versioned_v21_missing_implicit",
        spec_format_version="v2.1",
        with_implicit_fact_tags=False,
    )

    assert result.returncode != 0, (
        f"v2.1 + missing IMPLICIT_FACT tags must FAIL; "
        f"got returncode {result.returncode}\nstdout:\n{result.stdout}"
    )
    combined = result.stdout + result.stderr
    assert "IMPLICIT_FACT_SKIPPED" in combined, (
        "Phase 3 warn→fail upgrade must surface IMPLICIT_FACT_SKIPPED in "
        f"failure output; got:\n{combined}"
    )
    assert (
        "FAIL" in combined
        or "ERROR" in combined
        or "failed" in combined.lower()
    ), (
        "Expected a FAIL/ERROR indicator in non-zero exit output; "
        f"got:\n{combined}"
    )


def test_unknown_version_hard_fails(run_versioned_validator_subprocess):
    """SC#1 — declared version not in `KNOWN_SPEC_FORMAT_VERSIONS` = HARD FAIL.

    Plan 03-02 lands `KNOWN_SPEC_FORMAT_VERSIONS = ("v2.0", "v2.1")`.
    Plan 03-03 emits `SPEC_FORMAT_VERSION_UNKNOWN` as `report.fail()` for
    any declared version outside that allowlist.

    The fixture declares v9.0. Other downstream tokens may or may not also
    fire (depends on whether 03-03 short-circuits or continues). The test
    asserts only the unknown-version token + non-zero exit so either policy
    is acceptable.
    """
    result = run_versioned_validator_subprocess(
        "transcript_versioned_unknown",
        spec_format_version="v9.0",
        with_typed_tables=False,
    )

    assert result.returncode != 0, (
        f"v9.0 (unknown) must FAIL the validator; "
        f"got returncode {result.returncode}\nstdout:\n{result.stdout}"
    )
    combined = result.stdout + result.stderr
    assert "SPEC_FORMAT_VERSION_UNKNOWN" in combined, (
        "Plan 03-03 must surface SPEC_FORMAT_VERSION_UNKNOWN for any "
        f"declared version not in the allowlist; got:\n{combined}"
    )


# -----------------------------------------------------------------------------
# Plan 03-04 ownership: F0.5 stream_skips + F0.9 sub-check 7k
#
# These tests SKIP in Plan 03-01 because run_f05_decompose_with_test_roster
# raises pytest.skip(...). Plan 03-04 replaces the fixture body with the
# real harness; at that point these tests turn RED-then-GREEN as Plan 03-04
# wires up the F0.5 roster-enumeration logic + F0.9 sub-check 7k.
# -----------------------------------------------------------------------------


def test_f05_emits_stream_skip_for_legacy_spec(
    run_f05_decompose_with_test_roster, fixtures_dir
):
    """SC#3 + SC#4 — F0.5 emits a stream_skips record when a stream-agent's
    `min_spec_format_version` exceeds the spec's declared version.

    Synthetic agent fixture declares `min_spec_format_version: v2.1`. Spec
    declares v2.0. Expected emission:

        stream_skips:
          - stream_id: PHASE3-TEST-STREAM
            reason: spec_format_version
            spec_version: v2.0
            stream_min: v2.1
            agent_path: ...

    Plan 03-01: run_f05_decompose_with_test_roster raises pytest.skip(...) —
    test SKIPs cleanly. Plan 03-04: harness lands; test goes RED (manifest
    missing or wrong shape) until Plan 03-04 wires the emission logic.
    """
    result = run_f05_decompose_with_test_roster(
        spec_format_version="v2.0",
        extra_agent_paths=[
            fixtures_dir / "agents" / "agent_phase3_test_stream.md"
        ],
    )

    skips = result.get("stream_skips", [])
    assert len(skips) == 1, (
        f"Expected exactly one stream_skips record for the synthetic "
        f"agent under v2.0 spec; got {len(skips)} records:\n{skips}"
    )
    record = skips[0]
    assert record["stream_id"] == "PHASE3-TEST-STREAM", (
        f"Expected stream_id 'PHASE3-TEST-STREAM'; got '{record.get('stream_id')}'"
    )
    assert record["reason"] == "spec_format_version", (
        f"Expected reason 'spec_format_version'; got '{record.get('reason')}'"
    )


def test_f05_emits_empty_stream_skips_for_modern(
    run_f05_decompose_with_test_roster, fixtures_dir
):
    """SC#4 — `stream_skips` is present-but-empty when no agent skips.

    Same synthetic agent (min v2.1), but spec declares v2.1 — the agent's
    minimum is satisfied, so F0.5 emits NO stream_skips records. The
    manifest field must still be present (empty array), not absent — Plan
    03-04 makes `manifest.stream_skips` a structurally required key so F0.9
    sub-check 7k has an unambiguous emit-vs-omit signal.
    """
    result = run_f05_decompose_with_test_roster(
        spec_format_version="v2.1",
        extra_agent_paths=[
            fixtures_dir / "agents" / "agent_phase3_test_stream.md"
        ],
    )

    assert "stream_skips" in result, (
        "manifest must include 'stream_skips' key even when empty "
        "(present-but-empty array — F0.9 7k requires the key for "
        f"unambiguous emit-vs-omit detection); got: {list(result.keys())}"
    )
    assert result["stream_skips"] == [], (
        f"v2.1 spec + v2.1 agent must yield empty stream_skips; "
        f"got: {result['stream_skips']}"
    )


def test_stream_skip_record_schema(
    run_f05_decompose_with_test_roster, fixtures_dir
):
    """SC#4 — every stream_skips record has all five required keys.

    Schema (Plan 03-04 / 03-CONTEXT.md):
      - stream_id        (string)   — agent's `id` field
      - reason           (string)   — "spec_format_version" (only reason in v1)
      - spec_version     (string)   — declared spec version
      - stream_min       (string)   — agent's min_spec_format_version
      - agent_path       (string)   — relative path to agent file

    Plan 03-04: F0.9 sub-check 7k validates every emitted record against
    this schema; absent keys = STREAM_SKIP_INCOMPLETE.
    """
    result = run_f05_decompose_with_test_roster(
        spec_format_version="v2.0",
        extra_agent_paths=[
            fixtures_dir / "agents" / "agent_phase3_test_stream.md"
        ],
    )

    skips = result.get("stream_skips", [])
    assert skips, (
        f"Expected at least one stream_skips record under v2.0 + v2.1 "
        f"agent; got: {skips}"
    )
    required_keys = {
        "stream_id",
        "reason",
        "spec_version",
        "stream_min",
        "agent_path",
    }
    for record in skips:
        missing = required_keys - set(record.keys())
        assert not missing, (
            f"stream_skips record missing required keys {missing}; "
            f"got: {record}"
        )


def test_f09_subcheck_7k_catches_missing(
    run_f05_decompose_with_test_roster, fixtures_dir
):
    """SC#4 — F0.9 sub-check 7k surfaces STREAM_SKIP_INCOMPLETE when an
    expected stream_skips record is omitted.

    Plan 03-04: harness allows injecting an artificially incomplete
    `manifest.stream_skips` (omit a record that should be present). F0.9
    sub-check 7k cross-references the casting roster against the spec's
    declared version and emits STREAM_SKIP_INCOMPLETE for any agent whose
    `min_spec_format_version > spec_version` that lacks a skip record.

    The test injects a v2.0 spec + v2.1 agent but a manifest with EMPTY
    stream_skips (the expected record is missing). Sub-check 7k must
    surface STREAM_SKIP_INCOMPLETE.
    """
    # Plan 03-04: harness exposes ``omit_required_record=True`` to force
    # an empty ``manifest.stream_skips`` array even when a record SHOULD
    # have been emitted. Sub-check 7k's re-derivation catches the omission
    # and emits STREAM_SKIP_INCOMPLETE.
    result = run_f05_decompose_with_test_roster(
        spec_format_version="v2.0",
        extra_agent_paths=[
            fixtures_dir / "agents" / "agent_phase3_test_stream.md"
        ],
        omit_required_record=True,
    )

    f09_diagnostics = result.get("f09_diagnostics", "")
    assert "STREAM_SKIP_INCOMPLETE" in f09_diagnostics, (
        "Plan 03-04 F0.9 sub-check 7k must surface STREAM_SKIP_INCOMPLETE "
        "when an expected stream_skips record is missing; "
        f"got f09_diagnostics:\n{f09_diagnostics}"
    )


def test_f09_subcheck_7k_catches_unexpected(
    run_f05_decompose_with_test_roster, fixtures_dir
):
    """SC#4 — F0.9 sub-check 7k surfaces STREAM_SKIP_UNEXPECTED when a
    stream_skips record is emitted for an agent whose min ≤ spec version.

    Plan 03-04: harness allows injecting an artificially over-eager
    `manifest.stream_skips` (a record for an agent that should NOT have
    been skipped). Sub-check 7k must detect this and emit
    STREAM_SKIP_UNEXPECTED.

    The test injects a v2.1 spec + v2.1 agent but a manifest with a
    spurious stream_skips record. Sub-check 7k must surface
    STREAM_SKIP_UNEXPECTED.
    """
    # Plan 03-04: harness exposes ``inject_unexpected_record=True`` to
    # append a record for a rostered agent whose min ≤ spec version
    # (false positive). Sub-check 7k catches this and emits
    # STREAM_SKIP_UNEXPECTED.
    result = run_f05_decompose_with_test_roster(
        spec_format_version="v2.1",
        extra_agent_paths=[
            fixtures_dir / "agents" / "agent_phase3_test_stream.md"
        ],
        inject_unexpected_record=True,
    )

    f09_diagnostics = result.get("f09_diagnostics", "")
    assert "STREAM_SKIP_UNEXPECTED" in f09_diagnostics, (
        "Plan 03-04 F0.9 sub-check 7k must surface STREAM_SKIP_UNEXPECTED "
        "when a stream_skips record is emitted for an agent whose min ≤ "
        f"spec version; got f09_diagnostics:\n{f09_diagnostics}"
    )


# -----------------------------------------------------------------------------
# Plan 03-04 anti-drift guards (RESEARCH.md Pitfall 3 + Pitfall 7)
#
# These two tests defend the cross-prose alignment between F0.5 V2 step 2b
# and F0.9 sub-check 7k in plugins/foundry/commands/start.md, plus the
# casting-prompt-cleanliness contract for the F0.5 stdout summary line.
# -----------------------------------------------------------------------------


def test_probe01_inline_min_version_matches_forge_frontmatter():
    """PROBE-01's min version is declared inline in F0.5 step 2b, and must
    still match spec-reviewer.md's real frontmatter.

    Every other readable roster entry is Foundry-side, so F0.5 parses its
    ``min_spec_format_version`` straight from the agent file. PROBE-01
    cannot be: it lives in the Forge plugin, and ``${CLAUDE_PLUGIN_ROOT}``
    names Foundry's own directory. Once installed, each plugin sits under
    its own *version* directory (``.../cache/<marketplace>/forge/4.3.1/``)
    whose name Foundry cannot know, so no relative walk reaches it. The
    roster therefore carries PROBE-01's gate inline.

    That trades an unresolvable read for a drift risk — bump the version in
    spec-reviewer.md's frontmatter and the roster keeps gating on the stale
    one, silently running or skipping PROBE-01 against the wrong spec
    versions. This test closes that gap. It can only run here, in the source
    repo, because this is the one layout where both plugins are present.
    """
    repo_root = Path(__file__).resolve().parent.parent.parent.parent

    reviewer = (
        repo_root / "plugins" / "forge" / "agents" / "spec-reviewer.md"
    ).read_text(encoding="utf-8")
    fm_match = re.search(
        r"^min_spec_format_version:\s*(\S+)\s*$", reviewer, re.MULTILINE
    )
    assert fm_match, (
        "spec-reviewer.md has no min_spec_format_version in its frontmatter "
        "— F0.5 step 2b's inline PROBE-01 gate has nothing to align against."
    )
    frontmatter_version = fm_match.group(1).strip("\"'")

    start_md = (
        repo_root / "plugins" / "foundry" / "commands" / "start.md"
    ).read_text(encoding="utf-8")
    probe_match = re.search(
        r"\*\*PROBE-01\*\*.*?`min_spec_format_version:\s*(\S+?)`",
        start_md,
        re.DOTALL,
    )
    assert probe_match, (
        "F0.5 step 2b has no inline `min_spec_format_version: ...` on its "
        "PROBE-01 roster entry. If PROBE-01 became a readable path again, "
        "delete this test; otherwise restore the inline declaration."
    )
    roster_version = probe_match.group(1)

    assert roster_version == frontmatter_version, (
        "PROBE-01 gate drift: F0.5 step 2b's roster declares "
        f"{roster_version!r} but plugins/forge/agents/spec-reviewer.md "
        f"declares {frontmatter_version!r}. Update the roster entry in "
        "plugins/foundry/commands/start.md to match."
    )


def test_f05_step_2b_and_f09_7k_reference_same_roster():
    """RESEARCH.md Pitfall 7 — F0.5 step 2b and F0.9 sub-check 7k reference
    the same hardcoded agent roster.

    If the two prose blocks drift (one lists ``tracer.md``, the other
    forgets to add it when Phases 6/7/8 update the roster), 7k either
    false-positives or false-negatives in lock-step with F0.5's emission
    bug. The roster appears in two places by design (defense-in-depth via
    re-derivation); this regression test makes the alignment grep-checkable
    on every CI run.

    Acceptance: either both blocks list the same agent-path set, OR
    sub-check 7k uses the explicit by-reference phrase ``same hardcoded
    list as F0.5 step 2b`` (re-derivation by reference is acceptable —
    the roster is still single-sourced inside F0.5 step 2b).
    """
    start_md_path = (
        Path(__file__).resolve().parent.parent.parent.parent
        / "plugins" / "foundry" / "commands" / "start.md"
    )
    text = start_md_path.read_text(encoding="utf-8")

    # Find the F0.5 V2 step 2b block. Anchored on the "2b. " literal at
    # column 0; consumes lines until the next "2c. " sibling header (or
    # the F0.5 V3 / F0.9 boundary).
    f05_match = re.search(
        r"^2b\.\s.*?(?=^2c\.\s|^### |\Z)",
        text,
        re.DOTALL | re.MULTILINE,
    )
    assert f05_match, (
        "F0.5 V2 step 2b block not found in start.md — Plan 03-04's prose "
        "must define a `2b.` sibling step inside F0.5 V2 procedure."
    )
    f05_block = f05_match.group(0)

    # Find the F0.9 sub-check 7k block. Anchored on the "**7k." literal;
    # consumes lines until the next dimension-7 sibling letter (none after
    # 7k currently) or the dimension 8 boundary.
    f09_match = re.search(
        r"\*\*7k\..*?(?=\n\s*-\s*\*\*7[a-z]\.|\n\d+\.\s+\*\*[A-Z]|\Z)",
        text,
        re.DOTALL,
    )
    assert f09_match, (
        "F0.9 sub-check 7k block not found in start.md — Plan 03-04's "
        "prose must define a `7k.` sibling sub-check inside F0.9 "
        "dimension 7."
    )
    f09_block = f09_match.group(0)

    # Acceptance branch 1: by-reference phrase ("same hardcoded ... as
    # F0.5 step 2b"). Re-derivation by reference is acceptable because
    # the roster is still single-sourced inside F0.5 step 2b.
    by_reference_re = re.compile(
        r"same hardcoded (?:list|roster|path list) as F0\.5 step 2b",
        re.IGNORECASE,
    )
    if by_reference_re.search(f09_block):
        return  # Acceptable — re-derivation by reference.

    # Acceptance branch 2: explicit path enumeration must match.
    agent_path_re = re.compile(r"plugins/(?:forge|foundry)/agents/\S+\.md")
    f05_paths = set(agent_path_re.findall(f05_block))
    f09_paths = set(agent_path_re.findall(f09_block))
    assert f05_paths == f09_paths, (
        "Drift detected between F0.5 step 2b roster and F0.9 sub-check 7k "
        "roster. RESEARCH.md Pitfall 7 — these must list the same agent "
        "paths OR sub-check 7k must use the by-reference phrase "
        "'same hardcoded list as F0.5 step 2b'.\n"
        f"F0.5 step 2b paths: {sorted(f05_paths)}\n"
        f"F0.9 sub-check 7k paths: {sorted(f09_paths)}"
    )


def test_f05_stdout_summary_not_in_casting_prompt(
    run_typed_validator_subprocess,
):
    """RESEARCH.md Pitfall 3 — the F0.5 stdout summary substring
    ``F0.5 stream-skipped:`` MUST NOT appear inside any casting prompt.

    Casting prompts must be byte-for-byte stable across runs for wave-
    level prompt-cache locality. The F0.5 step 2c summary line is a
    HUMAN/CI signal emitted at F0.5 entry BEFORE any casting prompt is
    written; if a background agent ever echoed the summary into a casting
    prompt, the cache-hit ratio for that wave would collapse.

    Phase 3 ships the substring contract; later phases enforce on real
    casting-prompt outputs. Phase 3's surface is the validator subprocess
    stdout, which is the structurally-similar stream the prompt-template
    propagation tests (Phase 2) already exercise.
    """
    result = run_typed_validator_subprocess("transcript_typed_complete")
    combined = result.stdout + result.stderr
    assert "F0.5 stream-skipped:" not in combined, (
        "RESEARCH.md Pitfall 3: 'F0.5 stream-skipped:' substring leaked "
        "into validator output — must not appear in any casting-prompt-"
        f"shaped output. Got:\n{combined}"
    )
