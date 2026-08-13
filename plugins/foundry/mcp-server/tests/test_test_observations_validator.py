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

The validator is a dash-named script invoked via subprocess (not an
importable module); the ``run_test_observations_validator`` conftest
fixture provides the runner and SKIPs cleanly when the script is
missing. Prose-contract tests (9-13) are the mechanical GI-003 guard:
agent .md and validator must state the same rule in the same words.
"""

from __future__ import annotations

import json
import re
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


def _load_clean_channel(fixtures_dir: Path) -> dict[str, Any]:
    return json.loads(
        (
            fixtures_dir / "test_observations"
            / "test-deriver-cycle-clean.json"
        ).read_text(encoding="utf-8")
    )


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
