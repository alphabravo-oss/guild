"""US-002 / US-003 — adjudicated-channel validity + contract-surface tests.

Casting C2 coverage:

AC-010 territory (assay_verdict optional known key, closed verdict vocab):
  1. test_round_trip_clean_adjudicate_revalidate
  2. test_each_verdict_value_accepted (x3, parametrized)
  3. test_info_verdict_rejected
  4. test_bogus_verdict_rejected
  5. test_partial_adjudication_valid

AC-011 territory (pre-adjudication files validate exactly as today):
  6. test_pre_adjudication_fixture_exit_codes (x9, parametrized over the
     eight shipped fixtures — dangling-req twice, bare + --spec)
  7. test_other_extra_observation_key_still_rejected

AC-012 territory (single validator copy):
  8. test_validator_ships_in_one_copy

AC-006/AC-007/AC-009 + GI-003 territory (contract <-> validator mirroring):
  9. test_shared_source_leak_sentence_mirrored
  10. test_verdict_frozenset_matches_adjudicator_contract
  11. test_adjudicator_info_option_dropped
  12. test_deriver_sanctions_contract_surface_execution

AC-008 territory (SKIP-on-no-surface deriver contract):
  13. test_deriver_skip_on_no_surface_rule

GRIND cycle 2 — D-002 (AC-007 contract-surface exemption IMPLEMENTED,
not just narrated):
  14. test_contract_surface_references_exempt_with_spec
  15. test_contract_surface_exemption_requires_spec
  16. test_undeclared_reference_still_leaks_with_spec
  17. test_mixed_declared_and_undeclared_still_leaks
  18. test_surface_directory_prefix_is_not_the_surface
  19. test_contract_surface_exemption_statement_mirrored

GRIND cycle 2 — D-003 (AC-008 truthful SKIP validates):
  20. test_truthful_skip_observation_validates (x2, bare + --spec)
  21. test_fail_without_negative_assertion_still_fires
  22. test_skip_exempt_from_shape_not_value_rule
  23. test_skip_with_source_leak_still_fires
  24. test_truthful_skip_statement_mirrored

F5 TEMPER T-D1 (AC-006/AC-007 — mask boundary is a superset of the
7c scan boundary; `./` normalization symmetric):
  25. test_real_failing_test_traceback_against_declared_surface_is_clean
  26. test_undeclared_absolute_path_still_leaks_with_spec
  27. test_scan_boundary_positions_are_subset_of_mask_boundary
  28. test_dot_slash_normalization_is_symmetric
  29. test_undeclared_references_still_leak_after_boundary_widening (x4)

F5 TEMPER T-D2 (AC-006 — exemption blackout made loud; tolerant
Contracts parse):
  30. test_contracts_heading_suffix_tolerated
  31. test_surface_prefixed_column_header_tolerated (x2)
  32. test_surface_column_blackout_prints_diagnostic_and_keeps_exit_semantics
  33. test_no_diagnostic_when_surface_column_identified
  34. test_contracts_parsing_rule_statement_mirrored

F5 TEMPER T-D3 (AC-010/GI-003 — closed-vocab membership tests total
over every JSON value class):
  35. test_non_string_status_rejected_by_token (x3)
  36. test_non_string_assay_verdict_rejected_by_token (x3)

The validator is a dash-named script invoked via subprocess (not an
importable module); the ``run_test_observations_validator`` conftest
fixture provides the runner and SKIPs cleanly when the script is
missing. Prose-contract tests (9-13, 19, 24) are the mechanical
GI-003 guard: agent .md and validator must state the same rule in the
same words.
"""

from __future__ import annotations

import json
import re
import string
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable

import pytest

# tests/test_test_observations_validator.py -> parents: [0]=tests,
# [1]=mcp-server, [2]=foundry, [3]=plugins, [4]=repo-root.
REPO_ROOT = Path(__file__).resolve().parents[4]
VALIDATE_PATH = (
    REPO_ROOT / "plugins" / "foundry" / "scripts"
    / "validate-test-observations.py"
)
DERIVER_MD = (
    REPO_ROOT / "plugins" / "foundry" / "agents" / "spec-test-deriver.md"
)
ADJUDICATOR_MD = (
    REPO_ROOT / "plugins" / "foundry" / "agents"
    / "test-observations-adjudicator.md"
)
MCP_SCRIPTS_PKG = (
    REPO_ROOT / "plugins" / "foundry" / "mcp-server" / "src"
    / "foundry_mcp" / "scripts"
)

KNOWN_TEST_OBSERVATION_VERDICTS = frozenset(
    {"DEFECT", "WRONG_TEST", "INCONCLUSIVE"}
)

# The GI-003 shared sentence — written once in the spec, pasted into
# spec-test-deriver.md (wrong-test pattern 3 + Contract-Surface
# Execution), test-observations-adjudicator.md (validation step), and
# validate-test-observations.py (7c source-leak check comment).
SHARED_SOURCE_LEAK_SENTENCE = (
    "Referencing or executing a surface named in the spec's "
    "`## Contracts` table is never a source leak; referencing "
    "symbols absent from both the spec and the contracts table "
    "still is."
)

# GRIND D-002 shared statement — the MECHANICAL half of AC-007. Pasted
# into spec-test-deriver.md (wrong-test pattern 3) and
# validate-test-observations.py (7c comment). Names the no-spec
# behavior explicitly per the defect's requirement.
SHARED_CONTRACT_SURFACE_EXEMPTION_STATEMENT = (
    "Contract-surface exemption (mechanical rule): when `--spec` "
    "is provided, the validator parses the spec's `## Contracts` "
    "table surface column and masks verbatim references to its "
    "declared path or module tokens before the forbidden-root "
    "scan; every forbidden-root reference that survives the "
    "masking still leaks. When no `--spec` is passed there is no "
    "contracts table to consult, so no exemption applies and "
    "every forbidden-root reference leaks — the adjudicator "
    "always passes `--spec`, so production adjudication always "
    "honors the exemption."
)

# F5 TEMPER T-D2 shared statement — the mechanical Contracts parsing
# rule (tolerant heading/header matching + loud exemption blackout).
# Pasted into spec-test-deriver.md (wrong-test pattern 3) and
# validate-test-observations.py (_contract_surface_leak_tokens
# docstring).
SHARED_CONTRACTS_PARSING_RULE_STATEMENT = (
    "Contracts parsing rule (mechanical): the Contracts section "
    "opens at any markdown heading of level 2-6 whose heading text "
    "begins with `Contracts` (case-insensitive; suffixed headings "
    "like `Contracts (TYPE-01)` are tolerated, mirroring forge "
    "validate-spec.py's startswith section matching), and the "
    "surface column is the first header-row cell whose lowercased "
    "text begins with `surface` (so `surface (CLI)` and "
    "`Surface / entrypoint` qualify). When a Contracts section has "
    "table rows but no such header cell, the validator prints a "
    "non-fatal `note:` diagnostic to stderr — no failure token, "
    "exit code unchanged — so an exemption blackout is visible "
    "instead of silent."
)

# The validator's full 9-token closed failure vocabulary (GI-002) —
# used to assert the T-D2 diagnostic line smuggles no token.
KNOWN_TEST_DERIVER_FAILURE_TOKENS = frozenset(
    {
        "TEST_DERIVER_READ_SOURCE",
        "TEST_HEADER_MISSING",
        "TEST_HEADER_DANGLING_REQ",
        "WRONG_TEST_NO_NEGATIVE_ASSERTION",
        "WRONG_TEST_VALUE_NOT_SHAPE",
        "WRONG_TEST_SOURCE_LEAK",
        "WRONG_TEST_HEADER_MISSING",
        "TEST_OBSERVATION_SCHEMA_INVALID",
        "TEST_OBSERVATION_UNKNOWN_STATUS",
    }
)

# GRIND D-003 shared statement — the accepted truthful SKIP shape.
# Pasted into spec-test-deriver.md (§ Test Derivation Procedure) and
# validate-test-observations.py (Step 7 comment).
SHARED_TRUTHFUL_SKIP_STATEMENT = (
    "Truthful SKIP shape: a SKIP-on-no-surface observation "
    "carries `status: SKIP`, a `captured_output` reason naming "
    "the missing surface, `negative_assertion_present: false` "
    "(no test body exists to assert anything), and "
    "`shape_not_value_check: passed` (vacuous — no assertions "
    "were written). Wrong-test rules 7a (negative-assertion "
    "mandate) and 7b (shape-not-value rule) presume an executed "
    "test body and never fire on `status: SKIP`; the source-leak "
    "scan (7c) and the header rules still apply to SKIP "
    "observations."
)


if not VALIDATE_PATH.exists():
    pytest.skip(
        "validate-test-observations.py missing on disk",
        allow_module_level=True,
    )


def _normalized(path: Path) -> str:
    """File text with comment prefixes stripped and whitespace collapsed.

    Lets the byte-mirrored sentence be matched across a Python comment
    block (``# ``-prefixed, re-wrapped) and markdown prose (indented
    list continuation) without either file carrying literal-identical
    line breaks.
    """
    lines = [
        re.sub(r"^\s*#*\s*", "", line)
        for line in path.read_text(encoding="utf-8").splitlines()
    ]
    return re.sub(r"\s+", " ", " ".join(lines)).strip()


def _load_channel(fixtures_dir: Path, name: str) -> dict[str, Any]:
    return json.loads(
        (fixtures_dir / "test_observations" / name).read_text(
            encoding="utf-8"
        )
    )


def _load_clean_channel(fixtures_dir: Path) -> dict[str, Any]:
    return _load_channel(fixtures_dir, "test-deriver-cycle-clean.json")


def _write_channel(tmp_path: Path, payload: dict[str, Any]) -> Path:
    out = tmp_path / "test-deriver-cycle-adjudicated.json"
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return out


# ---------------------------------------------------------------------------
# AC-010 — assay_verdict optional known key, closed-vocab value
# ---------------------------------------------------------------------------


def test_round_trip_clean_adjudicate_revalidate(
    run_test_observations_validator: Callable[..., tuple[int, str, str]],
    fixtures_dir: Path,
    tmp_path: Path,
) -> None:
    """US-003 round-trip: validate clean -> adjudicate -> validate again.

    Mirrors the adjudicator's append-only write contract: FAIL
    observation gets ``assay_verdict = "DEFECT"``; PASS observation
    gets NO verdict (field omitted); no other field touched. Both the
    pre- and post-adjudication files must exit 0.
    """
    spec = fixtures_dir / "specs" / "spec_test_deriver_simple.md"
    pristine = (
        fixtures_dir / "test_observations"
        / "test-deriver-cycle-clean.json"
    )
    exit_code, stdout, stderr = run_test_observations_validator(
        pristine, spec_path=spec
    )
    assert exit_code == 0, (
        f"pre-adjudication clean fixture must pass; got {exit_code}\n"
        f"stdout: {stdout}\nstderr: {stderr}"
    )

    channel = _load_clean_channel(fixtures_dir)
    for obs in channel["observations"]:
        if obs["status"] == "FAIL":
            obs["assay_verdict"] = "DEFECT"
        # PASS observations: omit the field (adjudicator contract).
    adjudicated = _write_channel(tmp_path, channel)
    exit_code, stdout, stderr = run_test_observations_validator(
        adjudicated, spec_path=spec
    )
    assert exit_code == 0, (
        f"post-adjudication file must still pass; got {exit_code}\n"
        f"stdout: {stdout}\nstderr: {stderr}"
    )


@pytest.mark.parametrize(
    "verdict", sorted(KNOWN_TEST_OBSERVATION_VERDICTS)
)
def test_each_verdict_value_accepted(
    run_test_observations_validator: Callable[..., tuple[int, str, str]],
    fixtures_dir: Path,
    tmp_path: Path,
    verdict: str,
) -> None:
    """Every member of the closed verdict vocabulary validates."""
    channel = _load_clean_channel(fixtures_dir)
    channel["observations"][1]["assay_verdict"] = verdict
    adjudicated = _write_channel(tmp_path, channel)
    exit_code, stdout, stderr = run_test_observations_validator(
        adjudicated
    )
    assert exit_code == 0, (
        f"assay_verdict={verdict!r} must be accepted; got {exit_code}\n"
        f"stdout: {stdout}\nstderr: {stderr}"
    )


def test_info_verdict_rejected(
    run_test_observations_validator: Callable[..., tuple[int, str, str]],
    fixtures_dir: Path,
    tmp_path: Path,
) -> None:
    """The adjudicator's former "INFO" convention fails the value check.

    must_haves truth 2: a verdict outside the closed vocabulary makes
    the validator exit non-zero with a token-prefixed failure naming
    the observation and the allowed set.
    """
    channel = _load_clean_channel(fixtures_dir)
    channel["observations"][0]["assay_verdict"] = "INFO"
    adjudicated = _write_channel(tmp_path, channel)
    exit_code, stdout, stderr = run_test_observations_validator(
        adjudicated
    )
    assert exit_code != 0, (
        f"assay_verdict='INFO' must be rejected; got {exit_code}\n"
        f"stdout: {stdout}\nstderr: {stderr}"
    )
    combined = stdout + stderr
    assert "TEST_OBSERVATION_SCHEMA_INVALID" in combined
    assert "OBS-001" in combined, (
        f"failure must name the observation; output: {combined}"
    )
    for member in KNOWN_TEST_OBSERVATION_VERDICTS:
        assert member in combined, (
            f"failure must name the allowed set; missing {member!r} in: "
            f"{combined}"
        )


def test_bogus_verdict_rejected(
    run_test_observations_validator: Callable[..., tuple[int, str, str]],
    fixtures_dir: Path,
    tmp_path: Path,
) -> None:
    """Arbitrary free-text verdicts are rejected (GI-002)."""
    channel = _load_clean_channel(fixtures_dir)
    channel["observations"][1]["assay_verdict"] = "MAYBE_DEFECT"
    adjudicated = _write_channel(tmp_path, channel)
    exit_code, stdout, stderr = run_test_observations_validator(
        adjudicated
    )
    assert exit_code != 0
    assert "TEST_OBSERVATION_SCHEMA_INVALID" in stdout + stderr


def test_partial_adjudication_valid(
    run_test_observations_validator: Callable[..., tuple[int, str, str]],
    fixtures_dir: Path,
    tmp_path: Path,
) -> None:
    """Absent assay_verdict means not-yet-adjudicated — never a failure.

    A channel where only some observations carry the field (the
    adjudicator omits it on PASS) must validate.
    """
    channel = _load_clean_channel(fixtures_dir)
    assert len(channel["observations"]) >= 2
    channel["observations"][1]["assay_verdict"] = "WRONG_TEST"
    # observations[0] (PASS) deliberately carries no assay_verdict.
    adjudicated = _write_channel(tmp_path, channel)
    exit_code, stdout, stderr = run_test_observations_validator(
        adjudicated
    )
    assert exit_code == 0, (
        f"partially adjudicated channel must pass; got {exit_code}\n"
        f"stdout: {stdout}\nstderr: {stderr}"
    )


# ---------------------------------------------------------------------------
# AC-011 — pre-adjudication behavior unchanged
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("fixture_name", "with_spec", "expect_pass"),
    [
        ("test-deriver-cycle-clean.json", False, True),
        ("test-deriver-cycle-clean.json", True, True),
        ("test-deriver-cycle-defect.json", False, True),
        ("test-deriver-cycle-dangling-req.json", False, True),
        ("test-deriver-cycle-dangling-req.json", True, False),
        ("test-deriver-cycle-header-missing.json", False, False),
        ("test-deriver-cycle-no-negative-assertion.json", False, False),
        ("test-deriver-cycle-schema-invalid.json", False, False),
        ("test-deriver-cycle-source-leak.json", False, False),
        ("test-deriver-cycle-value-not-shape.json", False, False),
    ],
)
def test_pre_adjudication_fixture_exit_codes(
    run_test_observations_validator: Callable[..., tuple[int, str, str]],
    fixtures_dir: Path,
    fixture_name: str,
    with_spec: bool,
    expect_pass: bool,
) -> None:
    """AC-011: the shipped fixtures keep their pre-change exit codes."""
    observation = fixtures_dir / "test_observations" / fixture_name
    spec = (
        fixtures_dir / "specs" / "spec_test_deriver_simple.md"
        if with_spec
        else None
    )
    exit_code, stdout, stderr = run_test_observations_validator(
        observation, spec_path=spec
    )
    if expect_pass:
        assert exit_code == 0, (
            f"{fixture_name} (spec={with_spec}) regressed to failing;\n"
            f"stdout: {stdout}\nstderr: {stderr}"
        )
    else:
        assert exit_code != 0, (
            f"{fixture_name} (spec={with_spec}) regressed to passing;\n"
            f"stdout: {stdout}\nstderr: {stderr}"
        )


def test_other_extra_observation_key_still_rejected(
    run_test_observations_validator: Callable[..., tuple[int, str, str]],
    fixtures_dir: Path,
    tmp_path: Path,
) -> None:
    """AC-011: assay_verdict is the ONLY new key; others still fail."""
    channel = _load_clean_channel(fixtures_dir)
    channel["observations"][0]["severity"] = "high"
    adjudicated = _write_channel(tmp_path, channel)
    exit_code, stdout, stderr = run_test_observations_validator(
        adjudicated
    )
    assert exit_code != 0, (
        f"extra key 'severity' must still be rejected; got {exit_code}\n"
        f"stdout: {stdout}\nstderr: {stderr}"
    )
    combined = stdout + stderr
    assert "TEST_OBSERVATION_SCHEMA_INVALID" in combined
    assert "severity" in combined


# ---------------------------------------------------------------------------
# AC-012 — single validator copy
# ---------------------------------------------------------------------------


def test_validator_ships_in_one_copy() -> None:
    """AC-012: no second copy under foundry_mcp/scripts to drift from.

    If the body is ever promoted into the package, the dash-named
    script must become a thin shim of it (the
    validate-intent-coverage.py pattern) and this guard updated in the
    same change — never two independent bodies.
    """
    assert VALIDATE_PATH.exists()
    assert not (MCP_SCRIPTS_PKG / "validate_test_observations.py").exists(), (
        "a second validator copy appeared without shim-ification; "
        "AC-012 forbids two independent bodies"
    )


# ---------------------------------------------------------------------------
# GI-003 — validator <-> agent-contract mirroring
# ---------------------------------------------------------------------------


def test_shared_source_leak_sentence_mirrored() -> None:
    """AC-007/GI-003: the shared sentence appears in all three files."""
    for path in (DERIVER_MD, ADJUDICATOR_MD, VALIDATE_PATH):
        assert SHARED_SOURCE_LEAK_SENTENCE in _normalized(path), (
            f"{path.name} must carry the shared source-leak sentence "
            "(whitespace-normalized) verbatim"
        )


def test_verdict_frozenset_matches_adjudicator_contract() -> None:
    """AC-009/AC-010: validator frozenset == adjudicator vocabulary.

    Parses KNOWN_TEST_OBSERVATION_VERDICTS out of the validator source
    and asserts exactly the three closed members — no fourth verdict,
    no free-text widening (GI-002).
    """
    source = VALIDATE_PATH.read_text(encoding="utf-8")
    match = re.search(
        r"KNOWN_TEST_OBSERVATION_VERDICTS\s*=\s*frozenset\(\s*"
        r"\{([^}]*)\}",
        source,
    )
    assert match, (
        "validate-test-observations.py must define "
        "KNOWN_TEST_OBSERVATION_VERDICTS"
    )
    members = set(re.findall(r'"([A-Z_]+)"', match.group(1)))
    assert members == set(KNOWN_TEST_OBSERVATION_VERDICTS)

    adjudicator_text = ADJUDICATOR_MD.read_text(encoding="utf-8")
    assert (
        "KNOWN_TEST_OBSERVATION_VERDICTS = {DEFECT, WRONG_TEST, "
        "INCONCLUSIVE}" in adjudicator_text
    ), "adjudicator contract must name the same three-member vocabulary"


def test_adjudicator_info_option_dropped() -> None:
    """must_haves truth 6: the `"INFO"` verdict option is reconciled.

    The adjudicator's write contract must never offer a value outside
    the closed vocabulary; PASS observations omit the field instead.
    """
    text = ADJUDICATOR_MD.read_text(encoding="utf-8")
    assert '"INFO"' not in text, (
        "adjudicator contract still offers the 'INFO' verdict — its "
        "own writes would fail the validator's closed-vocab check"
    )
    assert "OMIT the `assay_verdict` field" in text


def test_deriver_sanctions_contract_surface_execution() -> None:
    """AC-006: execution sanctioned, reads still forbidden."""
    text = DERIVER_MD.read_text(encoding="utf-8")
    assert "## Contract-Surface Execution (sanctioned)" in text
    assert "execution permission does NOT widen" in text
    assert "`ALLOWED_READ_PREFIXES`" in text
    # The read discipline survives untouched.
    assert "TEST_DERIVER_READ_SOURCE" in text
    assert "Forbidden source roots" in text


# ---------------------------------------------------------------------------
# AC-008 — SKIP-on-no-surface deriver contract
# ---------------------------------------------------------------------------


def test_deriver_skip_on_no_surface_rule() -> None:
    """AC-008: no-surface rows SKIP with a reason; tautology-PASS banned."""
    text = DERIVER_MD.read_text(encoding="utf-8")
    assert "SKIP-on-no-surface rule" in text
    assert "`status: SKIP`" in text
    assert "names the missing surface" in text
    assert "tautology" in text


# ---------------------------------------------------------------------------
# GRIND D-002 — AC-007 contract-surface exemption is IMPLEMENTED
# ---------------------------------------------------------------------------


def test_contract_surface_references_exempt_with_spec(
    run_test_observations_validator: Callable[..., tuple[int, str, str]],
    fixtures_dir: Path,
) -> None:
    """The four PROVE-disproven shapes validate once the spec declares them.

    The contract-surface fixture's captured_output invokes
    ``python plugins/foundry/scripts/validate-test-observations.py``,
    ``go run ./cmd/mytool``, ``python src/cli.py``, and
    ``node lib/bin/mytool.js`` — every one under a forbidden root, every
    one declared in spec_contract_surfaces.md's ``## Contracts`` surface
    column. With --spec, none is a leak (AC-006/AC-007).
    """
    observation = (
        fixtures_dir / "test_observations"
        / "test-deriver-cycle-contract-surface.json"
    )
    spec = fixtures_dir / "specs" / "spec_contract_surfaces.md"
    exit_code, stdout, stderr = run_test_observations_validator(
        observation, spec_path=spec
    )
    assert exit_code == 0, (
        f"declared-surface references must not leak; got {exit_code}\n"
        f"stdout: {stdout}\nstderr: {stderr}"
    )


def test_contract_surface_exemption_requires_spec(
    run_test_observations_validator: Callable[..., tuple[int, str, str]],
    fixtures_dir: Path,
) -> None:
    """No --spec means no contracts table to consult — no exemption.

    The shared statement's stated no-spec behavior: every
    forbidden-root reference leaks. All four observations trip.
    """
    observation = (
        fixtures_dir / "test_observations"
        / "test-deriver-cycle-contract-surface.json"
    )
    exit_code, stdout, stderr = run_test_observations_validator(
        observation
    )
    assert exit_code != 0, (
        f"without --spec the same channel must leak; got {exit_code}\n"
        f"stdout: {stdout}\nstderr: {stderr}"
    )
    combined = stdout + stderr
    assert "WRONG_TEST_SOURCE_LEAK" in combined
    for obs_id in ("OBS-090", "OBS-091", "OBS-092", "OBS-093"):
        assert obs_id in combined, (
            f"every declared-surface observation must leak without "
            f"--spec; missing {obs_id} in: {combined}"
        )


def test_undeclared_reference_still_leaks_with_spec(
    run_test_observations_validator: Callable[..., tuple[int, str, str]],
    fixtures_dir: Path,
    tmp_path: Path,
) -> None:
    """Symbols absent from both spec and contracts table still leak."""
    channel = _load_channel(
        fixtures_dir, "test-deriver-cycle-contract-surface.json"
    )
    channel["observations"][0]["captured_output"] = (
        "import src.secret_impl  # module never declared anywhere"
    )
    mutated = _write_channel(tmp_path, channel)
    spec = fixtures_dir / "specs" / "spec_contract_surfaces.md"
    exit_code, stdout, stderr = run_test_observations_validator(
        mutated, spec_path=spec
    )
    assert exit_code != 0, (
        f"undeclared src.secret_impl import must leak; got {exit_code}\n"
        f"stdout: {stdout}\nstderr: {stderr}"
    )
    combined = stdout + stderr
    assert "WRONG_TEST_SOURCE_LEAK" in combined
    assert "OBS-090" in combined


def test_mixed_declared_and_undeclared_still_leaks(
    run_test_observations_validator: Callable[..., tuple[int, str, str]],
    fixtures_dir: Path,
    tmp_path: Path,
) -> None:
    """A declared surface does not launder an undeclared path beside it."""
    channel = _load_channel(
        fixtures_dir, "test-deriver-cycle-contract-surface.json"
    )
    channel["observations"][2]["captured_output"] = (
        "$ python src/cli.py --help\n"
        "cat src/impl/core.py  # peeking at internals is still a leak"
    )
    mutated = _write_channel(tmp_path, channel)
    spec = fixtures_dir / "specs" / "spec_contract_surfaces.md"
    exit_code, stdout, stderr = run_test_observations_validator(
        mutated, spec_path=spec
    )
    assert exit_code != 0, (
        f"undeclared src/impl/core.py must leak even next to a "
        f"declared surface; got {exit_code}\n"
        f"stdout: {stdout}\nstderr: {stderr}"
    )
    assert "WRONG_TEST_SOURCE_LEAK" in stdout + stderr


def test_surface_directory_prefix_is_not_the_surface(
    run_test_observations_validator: Callable[..., tuple[int, str, str]],
    fixtures_dir: Path,
    tmp_path: Path,
) -> None:
    """Referencing the declared surface's parent DIRECTORY still leaks.

    The exemption masks verbatim declared tokens only —
    ``plugins/foundry/scripts/`` bare is not the declared
    ``plugins/foundry/scripts/validate-test-observations.py`` surface.
    """
    channel = _load_channel(
        fixtures_dir, "test-deriver-cycle-contract-surface.json"
    )
    channel["observations"][0]["captured_output"] = (
        "ls plugins/foundry/scripts/ shows sibling validators"
    )
    mutated = _write_channel(tmp_path, channel)
    spec = fixtures_dir / "specs" / "spec_contract_surfaces.md"
    exit_code, stdout, stderr = run_test_observations_validator(
        mutated, spec_path=spec
    )
    assert exit_code != 0, (
        f"directory-prefix reference must still leak; got {exit_code}\n"
        f"stdout: {stdout}\nstderr: {stderr}"
    )
    assert "WRONG_TEST_SOURCE_LEAK" in stdout + stderr


def test_contract_surface_exemption_statement_mirrored() -> None:
    """D-002/GI-003: mechanical rule stated in the same words twice.

    Deriver contract (wrong-test pattern 3) and validator (7c comment)
    must carry the exemption statement — including the no-spec
    behavior — whitespace-normalized identical.
    """
    for path in (DERIVER_MD, VALIDATE_PATH):
        assert (
            SHARED_CONTRACT_SURFACE_EXEMPTION_STATEMENT
            in _normalized(path)
        ), (
            f"{path.name} must carry the contract-surface exemption "
            "statement (whitespace-normalized) verbatim"
        )


# ---------------------------------------------------------------------------
# GRIND D-003 — AC-008 truthful SKIP validates
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("with_spec", [False, True])
def test_truthful_skip_observation_validates(
    run_test_observations_validator: Callable[..., tuple[int, str, str]],
    fixtures_dir: Path,
    with_spec: bool,
) -> None:
    """A truthful SKIP-on-no-surface channel exits 0.

    The fixture carries the exact accepted shape from the shared
    statement: status SKIP, reason naming the missing surface,
    negative_assertion_present false, shape_not_value_check passed.
    Pre-fix this exited 1 (WRONG_TEST_NO_NEGATIVE_ASSERTION) and
    halted the adjudicator into a TEST-01 re-run loop.
    """
    observation = (
        fixtures_dir / "test_observations"
        / "test-deriver-cycle-skip-no-surface.json"
    )
    spec = (
        fixtures_dir / "specs" / "spec_test_deriver_simple.md"
        if with_spec
        else None
    )
    exit_code, stdout, stderr = run_test_observations_validator(
        observation, spec_path=spec
    )
    assert exit_code == 0, (
        f"truthful SKIP must validate (spec={with_spec}); "
        f"got {exit_code}\nstdout: {stdout}\nstderr: {stderr}"
    )


def test_fail_without_negative_assertion_still_fires(
    run_test_observations_validator: Callable[..., tuple[int, str, str]],
    fixtures_dir: Path,
    tmp_path: Path,
) -> None:
    """The 7a exemption is SKIP-only: FAIL + false still trips.

    (PASS + false is covered by the shipped no-negative-assertion
    fixture in the AC-011 table.)
    """
    channel = _load_channel(
        fixtures_dir, "test-deriver-cycle-skip-no-surface.json"
    )
    channel["observations"][0]["status"] = "FAIL"
    channel["observations"][0]["captured_output"] = (
        "AssertionError: happy path only; no negative branch"
    )
    mutated = _write_channel(tmp_path, channel)
    exit_code, stdout, stderr = run_test_observations_validator(mutated)
    assert exit_code != 0, (
        f"FAIL without negative assertion must still trip 7a; "
        f"got {exit_code}\nstdout: {stdout}\nstderr: {stderr}"
    )
    assert "WRONG_TEST_NO_NEGATIVE_ASSERTION" in stdout + stderr


def test_skip_exempt_from_shape_not_value_rule(
    run_test_observations_validator: Callable[..., tuple[int, str, str]],
    fixtures_dir: Path,
    tmp_path: Path,
) -> None:
    """7b also presumes an executed test body — SKIP is exempt.

    The truthful shape sets shape_not_value_check to "passed"
    (vacuous), but the validator's exemption is status-keyed: even a
    contradictory "failed" on a SKIP is not judged, because there was
    no test body whose assertions could be shape-checked.
    """
    channel = _load_channel(
        fixtures_dir, "test-deriver-cycle-skip-no-surface.json"
    )
    channel["observations"][0]["shape_not_value_check"] = "failed"
    mutated = _write_channel(tmp_path, channel)
    exit_code, stdout, stderr = run_test_observations_validator(mutated)
    assert exit_code == 0, (
        f"7b must not fire on SKIP; got {exit_code}\n"
        f"stdout: {stdout}\nstderr: {stderr}"
    )


def test_skip_with_source_leak_still_fires(
    run_test_observations_validator: Callable[..., tuple[int, str, str]],
    fixtures_dir: Path,
    tmp_path: Path,
) -> None:
    """The SKIP exemption covers 7a/7b only — 7c still scans SKIPs."""
    channel = _load_channel(
        fixtures_dir, "test-deriver-cycle-skip-no-surface.json"
    )
    channel["observations"][0]["captured_output"] = (
        "SKIP: consulted src/handlers/login.py to decide the surface"
    )
    mutated = _write_channel(tmp_path, channel)
    exit_code, stdout, stderr = run_test_observations_validator(mutated)
    assert exit_code != 0, (
        f"a SKIP reason referencing a forbidden root must leak; "
        f"got {exit_code}\nstdout: {stdout}\nstderr: {stderr}"
    )
    assert "WRONG_TEST_SOURCE_LEAK" in stdout + stderr


def test_truthful_skip_statement_mirrored() -> None:
    """D-003/GI-003: accepted SKIP shape stated in the same words twice."""
    for path in (DERIVER_MD, VALIDATE_PATH):
        assert SHARED_TRUTHFUL_SKIP_STATEMENT in _normalized(path), (
            f"{path.name} must carry the truthful SKIP shape statement "
            "(whitespace-normalized) verbatim"
        )


# ---------------------------------------------------------------------------
# F5 TEMPER T-D1 — mask boundary ⊇ scan boundary (AC-006/AC-007)
# ---------------------------------------------------------------------------


def _cli_observation_channel(
    fixtures_dir: Path, tmp_path: Path, captured_output: str
) -> Path:
    """Channel with only OBS-092 (the declared `src/cli.py` surface).

    Derived from the shipped contract-surface fixture so top-level
    keys, citation chains, and tests_spec stay canonical; only the
    captured_output under test varies.
    """
    channel = _load_channel(
        fixtures_dir, "test-deriver-cycle-contract-surface.json"
    )
    obs = channel["observations"][2]
    assert obs["observation_id"] == "OBS-092"
    obs["captured_output"] = captured_output
    channel["observations"] = [obs]
    return _write_channel(tmp_path, channel)


def test_real_failing_test_traceback_against_declared_surface_is_clean(
    run_test_observations_validator: Callable[..., tuple[int, str, str]],
    fixtures_dir: Path,
    tmp_path: Path,
) -> None:
    """T-D1 decisive case: a REAL failing test's traceback validates clean.

    The deriver's mandated shape is ``subprocess.run`` of the
    contract-row CLI entrypoint (spec-test-deriver.md § Build the test
    body). Python tracebacks print the ``__main__`` file by ABSOLUTE
    path regardless of how the script was invoked, so the captured
    output of a genuinely failing test against declared ``src/cli.py``
    contains ``File "/abs/.../src/cli.py"`` — a `/`-preceded reference
    the pre-fix mask could never exempt. A genuine DEFECT must not be
    branded WRONG_TEST_SOURCE_LEAK (AC-006/AC-007).
    """
    proj = tmp_path / "proj"
    (proj / "src").mkdir(parents=True)
    (proj / "src" / "cli.py").write_text(
        "import sys\n"
        "\n"
        "\n"
        "def main() -> None:\n"
        '    if "--bad" in sys.argv:\n'
        '        raise RuntimeError("boom: bad flag accepted")\n'
        "\n"
        "\n"
        "main()\n",
        encoding="utf-8",
    )
    proc = subprocess.run(
        [sys.executable, "src/cli.py", "--bad"],
        cwd=proj,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode != 0, "the probe script must genuinely fail"
    traceback_text = proc.stderr
    assert re.search(r'File "/.*/src/cli\.py"', traceback_text), (
        "precondition: the real traceback must reference the declared "
        f"surface by ABSOLUTE path; got:\n{traceback_text}"
    )

    channel_path = _cli_observation_channel(
        fixtures_dir,
        tmp_path,
        "$ python src/cli.py --bad\n" + traceback_text,
    )
    spec = fixtures_dir / "specs" / "spec_contract_surfaces.md"
    exit_code, stdout, stderr = run_test_observations_validator(
        channel_path, spec_path=spec
    )
    assert exit_code == 0, (
        "a failing test's absolute-path traceback referencing the "
        f"declared surface must not leak; got {exit_code}\n"
        f"stdout: {stdout}\nstderr: {stderr}"
    )


def test_undeclared_absolute_path_still_leaks_with_spec(
    run_test_observations_validator: Callable[..., tuple[int, str, str]],
    fixtures_dir: Path,
    tmp_path: Path,
) -> None:
    """The widened mask exempts DECLARED surfaces only, at any position."""
    channel_path = _cli_observation_channel(
        fixtures_dir,
        tmp_path,
        "Traceback (most recent call last):\n"
        '  File "/home/u/proj/src/secret_impl.py", line 3, in <module>\n'
        "RuntimeError: boom\n",
    )
    spec = fixtures_dir / "specs" / "spec_contract_surfaces.md"
    exit_code, stdout, stderr = run_test_observations_validator(
        channel_path, spec_path=spec
    )
    assert exit_code != 0, (
        f"undeclared absolute path must still leak; got {exit_code}\n"
        f"stdout: {stdout}\nstderr: {stderr}"
    )
    combined = stdout + stderr
    assert "WRONG_TEST_SOURCE_LEAK" in combined
    assert "OBS-092" in combined


def test_scan_boundary_positions_are_subset_of_mask_boundary(
    run_test_observations_validator: Callable[..., tuple[int, str, str]],
    fixtures_dir: Path,
    tmp_path: Path,
) -> None:
    """Mechanical regression for the T-D1 root cause.

    Every position where the 7c scan can detect a forbidden root must
    be a position the contract-surface mask can exempt. One
    observation per printable prefix character around the declared
    ``src/cli.py`` surface: if ANY prefix is scan-detectable but not
    maskable (the pre-fix `/` asymmetry), that observation false-leaks
    and the channel exits 1.
    """
    channel = _load_channel(
        fixtures_dir, "test-deriver-cycle-contract-surface.json"
    )
    template = channel["observations"][2]
    channel["observations"] = [
        {
            **template,
            "observation_id": f"OBS-B{i:03d}",
            "captured_output": f"probe {ch}src/cli.py probe",
        }
        for i, ch in enumerate(sorted(set(string.printable)))
    ]
    channel_path = _write_channel(tmp_path, channel)
    spec = fixtures_dir / "specs" / "spec_contract_surfaces.md"
    exit_code, stdout, stderr = run_test_observations_validator(
        channel_path, spec_path=spec
    )
    assert exit_code == 0, (
        "a declared-surface reference false-leaked at some boundary "
        f"position the mask cannot cover; got {exit_code}\n"
        f"stdout: {stdout}\nstderr: {stderr}"
    )
    assert "WRONG_TEST_SOURCE_LEAK" not in stdout + stderr


def test_dot_slash_normalization_is_symmetric(
    run_test_observations_validator: Callable[..., tuple[int, str, str]],
    fixtures_dir: Path,
    tmp_path: Path,
) -> None:
    """Declared ``X`` covers ``./X`` and declared ``./X`` covers ``X``."""
    spec = fixtures_dir / "specs" / "spec_contract_surfaces.md"

    # Direction 1 (the T-D1 gap): spec declares bare `src/cli.py`
    # (CT-003); a `./`-prefixed reference must not leak.
    channel_path = _cli_observation_channel(
        fixtures_dir,
        tmp_path,
        "$ python ./src/cli.py --help\nusage: cli [-h]",
    )
    exit_code, stdout, stderr = run_test_observations_validator(
        channel_path, spec_path=spec
    )
    assert exit_code == 0, (
        f"./-prefixed reference to declared bare surface leaked; "
        f"got {exit_code}\nstdout: {stdout}\nstderr: {stderr}"
    )

    # Direction 2 (pre-existing): spec declares `./cmd/mytool`
    # (CT-002); a bare reference must not leak.
    channel = _load_channel(
        fixtures_dir, "test-deriver-cycle-contract-surface.json"
    )
    assert channel["observations"][1]["observation_id"] == "OBS-091"
    channel["observations"][1]["captured_output"] = (
        "$ go run cmd/mytool --version\nmytool 1.2.3"
    )
    channel_path = _write_channel(tmp_path, channel)
    exit_code, stdout, stderr = run_test_observations_validator(
        channel_path, spec_path=spec
    )
    assert exit_code == 0, (
        f"bare reference to declared ./-prefixed surface leaked; "
        f"got {exit_code}\nstdout: {stdout}\nstderr: {stderr}"
    )


@pytest.mark.parametrize(
    "leak_output",
    [
        "cat /abs/path/src/cli.pyc",
        "cat ./src/cli.py.bak",
        "$ python src/cli2.py --help",
        "ls /abs/src/ shows internals",
    ],
    ids=["abs-tail-neighbour", "dot-tail-neighbour", "sibling", "bare-root"],
)
def test_undeclared_references_still_leak_after_boundary_widening(
    run_test_observations_validator: Callable[..., tuple[int, str, str]],
    fixtures_dir: Path,
    tmp_path: Path,
    leak_output: str,
) -> None:
    """Widening the mask's leading boundary must not widen its SCOPE.

    Tail-neighbours, truncated prefixes, siblings, and bare roots stay
    leaks — the trailing boundary and verbatim-token discipline are
    untouched (temper Q1/Q2/Q3/Q4 re-verification).
    """
    channel_path = _cli_observation_channel(
        fixtures_dir, tmp_path, leak_output
    )
    spec = fixtures_dir / "specs" / "spec_contract_surfaces.md"
    exit_code, stdout, stderr = run_test_observations_validator(
        channel_path, spec_path=spec
    )
    assert exit_code != 0, (
        f"undeclared reference {leak_output!r} must still leak; "
        f"got {exit_code}\nstdout: {stdout}\nstderr: {stderr}"
    )
    assert "WRONG_TEST_SOURCE_LEAK" in stdout + stderr


# ---------------------------------------------------------------------------
# F5 TEMPER T-D2 — tolerant Contracts parse + loud exemption blackout
# ---------------------------------------------------------------------------


def _write_surface_spec(
    tmp_path: Path,
    *,
    heading: str = "## Contracts",
    surface_header: str = "surface",
) -> Path:
    """Minimal v2.1 spec declaring `src/cli.py` under a variable shape."""
    spec = tmp_path / "spec_temper_probe.md"
    spec.write_text(
        "---\n"
        "spec_format_version: v2.1\n"
        "---\n"
        "\n"
        "# Spec: temper-probe\n"
        "\n"
        f"{heading}\n"
        "\n"
        f"| ID | {surface_header} | input | output | errors | citation |\n"
        "|----|---------|-------|--------|--------|----------|\n"
        "| CT-003 | `python src/cli.py --help` | `{}` | `{usage: str}` |"
        " `exit 2 on bad flag` | [from A-001] |\n"
        "\n"
        "<spec_requirements>\n"
        "- FR-1: Declared surfaces are executable as documented."
        " [from A-001]\n"
        "- FR-2: Declared surfaces fail loudly on out-of-contract input."
        " [from A-001]\n"
        "</spec_requirements>\n",
        encoding="utf-8",
    )
    return spec


def test_contracts_heading_suffix_tolerated(
    run_test_observations_validator: Callable[..., tuple[int, str, str]],
    fixtures_dir: Path,
    tmp_path: Path,
) -> None:
    """`## Contracts (TYPE-01)` is forge-admissible and must not blackout.

    forge validate-spec.py's extract_section matches headings by
    startswith, so this spec clears the upstream gate; the exemption
    parser must recognize the same section.
    """
    spec = _write_surface_spec(tmp_path, heading="## Contracts (TYPE-01)")
    channel_path = _cli_observation_channel(
        fixtures_dir,
        tmp_path,
        "$ python src/cli.py --help\nusage: cli [-h]",
    )
    exit_code, stdout, stderr = run_test_observations_validator(
        channel_path, spec_path=spec
    )
    assert exit_code == 0, (
        f"suffixed Contracts heading caused an exemption blackout; "
        f"got {exit_code}\nstdout: {stdout}\nstderr: {stderr}"
    )


@pytest.mark.parametrize(
    "surface_header", ["surface (CLI)", "Surface / entrypoint"]
)
def test_surface_prefixed_column_header_tolerated(
    run_test_observations_validator: Callable[..., tuple[int, str, str]],
    fixtures_dir: Path,
    tmp_path: Path,
    surface_header: str,
) -> None:
    """A `surface`-prefixed header cell identifies the surface column."""
    spec = _write_surface_spec(tmp_path, surface_header=surface_header)
    channel_path = _cli_observation_channel(
        fixtures_dir,
        tmp_path,
        "$ python src/cli.py --help\nusage: cli [-h]",
    )
    exit_code, stdout, stderr = run_test_observations_validator(
        channel_path, spec_path=spec
    )
    assert exit_code == 0, (
        f"surface-prefixed header {surface_header!r} caused an "
        f"exemption blackout; got {exit_code}\n"
        f"stdout: {stdout}\nstderr: {stderr}"
    )


def test_surface_column_blackout_prints_diagnostic_and_keeps_exit_semantics(
    run_test_observations_validator: Callable[..., tuple[int, str, str]],
    fixtures_dir: Path,
    tmp_path: Path,
) -> None:
    """Genuine parse failure is loud but non-fatal (no token, exit unchanged).

    A Contracts section with table rows but no identifiable surface
    column prints a `note:` diagnostic to stderr in BOTH the leaking
    and the clean case; the exit code is still determined solely by
    the failure list, and the diagnostic smuggles no failure token
    (GI-002).
    """
    spec = _write_surface_spec(tmp_path, surface_header="entrypoint")

    # Leaking channel: exemption is inactive, so the declared-in-spirit
    # reference leaks — and the blackout is visible on stderr.
    channel_path = _cli_observation_channel(
        fixtures_dir, tmp_path, "$ python src/cli.py --help\n"
    )
    exit_code, stdout, stderr = run_test_observations_validator(
        channel_path, spec_path=spec
    )
    assert exit_code != 0
    assert "WRONG_TEST_SOURCE_LEAK" in stdout
    assert "note:" in stderr, (
        f"exemption blackout must be loud on stderr; stderr: {stderr!r}"
    )
    assert "surface" in stderr
    for token in KNOWN_TEST_DERIVER_FAILURE_TOKENS:
        assert token not in stderr, (
            f"diagnostic must not smuggle failure token {token}; "
            f"stderr: {stderr!r}"
        )

    # Clean channel: diagnostic still visible, exit code still 0.
    channel_path = _cli_observation_channel(
        fixtures_dir, tmp_path, "all shape assertions passed"
    )
    exit_code, stdout, stderr = run_test_observations_validator(
        channel_path, spec_path=spec
    )
    assert exit_code == 0, (
        f"the diagnostic must not change the exit code; got {exit_code}\n"
        f"stdout: {stdout}\nstderr: {stderr}"
    )
    assert "note:" in stderr


def test_no_diagnostic_when_surface_column_identified(
    run_test_observations_validator: Callable[..., tuple[int, str, str]],
    fixtures_dir: Path,
) -> None:
    """State 1 (legitimately parsed table) stays silent.

    Both a benign-surface spec (nothing under a forbidden root) and a
    forbidden-root-surface spec parse their surface columns — neither
    is a blackout, so neither emits the note.
    """
    cases = [
        (
            fixtures_dir / "test_observations"
            / "test-deriver-cycle-clean.json",
            fixtures_dir / "specs" / "spec_test_deriver_simple.md",
        ),
        (
            fixtures_dir / "test_observations"
            / "test-deriver-cycle-contract-surface.json",
            fixtures_dir / "specs" / "spec_contract_surfaces.md",
        ),
    ]
    for observation, spec in cases:
        exit_code, stdout, stderr = run_test_observations_validator(
            observation, spec_path=spec
        )
        assert exit_code == 0, (
            f"{observation.name} + {spec.name} regressed; "
            f"stdout: {stdout}\nstderr: {stderr}"
        )
        assert "note:" not in stderr, (
            f"no blackout occurred, so no diagnostic is due; "
            f"stderr: {stderr!r}"
        )


def test_contracts_parsing_rule_statement_mirrored() -> None:
    """T-D2/GI-003: the parsing rule stated in the same words twice."""
    for path in (DERIVER_MD, VALIDATE_PATH):
        assert (
            SHARED_CONTRACTS_PARSING_RULE_STATEMENT in _normalized(path)
        ), (
            f"{path.name} must carry the Contracts parsing rule "
            "statement (whitespace-normalized) verbatim"
        )


# ---------------------------------------------------------------------------
# F5 TEMPER T-D3 — closed-vocab membership tests are total
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad_status",
    [["SKIP"], {"SKIP": 1}, 7],
    ids=["list", "dict", "int"],
)
def test_non_string_status_rejected_by_token(
    run_test_observations_validator: Callable[..., tuple[int, str, str]],
    fixtures_dir: Path,
    tmp_path: Path,
    bad_status: Any,
) -> None:
    """Every JSON value class rejects by token, never by traceback.

    Pre-fix, unhashable values (arrays/objects) raised TypeError inside
    the frozenset membership test — exit 1 with empty stdout and no
    token, breaking the docstring's and the adjudicator contract's
    closed-vocab promise (GI-003).
    """
    channel = _load_clean_channel(fixtures_dir)
    channel["observations"][0]["status"] = bad_status
    channel_path = _write_channel(tmp_path, channel)
    exit_code, stdout, stderr = run_test_observations_validator(
        channel_path
    )
    assert exit_code != 0
    assert "TEST_OBSERVATION_UNKNOWN_STATUS" in stdout, (
        f"non-string status must reject with the closed-vocab token; "
        f"stdout: {stdout!r}\nstderr: {stderr!r}"
    )
    assert "OBS-001" in stdout, (
        f"rejection must name the observation; stdout: {stdout!r}"
    )
    assert "Traceback" not in stderr and "TypeError" not in stderr, (
        f"validator must not crash on unhashable status; "
        f"stderr: {stderr!r}"
    )


@pytest.mark.parametrize(
    "bad_verdict",
    [["DEFECT"], {"v": "DEFECT"}, 7],
    ids=["list", "dict", "int"],
)
def test_non_string_assay_verdict_rejected_by_token(
    run_test_observations_validator: Callable[..., tuple[int, str, str]],
    fixtures_dir: Path,
    tmp_path: Path,
    bad_verdict: Any,
) -> None:
    """AC-010 totality: assay_verdict value check covers every value class."""
    channel = _load_clean_channel(fixtures_dir)
    channel["observations"][1]["assay_verdict"] = bad_verdict
    channel_path = _write_channel(tmp_path, channel)
    exit_code, stdout, stderr = run_test_observations_validator(
        channel_path
    )
    assert exit_code != 0
    assert "TEST_OBSERVATION_SCHEMA_INVALID" in stdout, (
        f"non-string assay_verdict must reject with the closed-vocab "
        f"token; stdout: {stdout!r}\nstderr: {stderr!r}"
    )
    assert "OBS-002" in stdout, (
        f"rejection must name the observation; stdout: {stdout!r}"
    )
    assert "Traceback" not in stderr and "TypeError" not in stderr, (
        f"validator must not crash on unhashable assay_verdict; "
        f"stderr: {stderr!r}"
    )
