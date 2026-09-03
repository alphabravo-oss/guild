"""Casting 2 — every defect carries an evidence tier (US-002).

One regression test per acceptance criterion, each docstring quoting the
requirement it proves. Built on the synthetic-run-directory shape
``tests/test_escalation.py`` establishes: a run activated under ``tmp_path``,
the real filing door driven, the persisted ledger read back.

  AC-006 / CT-001 / FR-004 / OT-004   tier is required and closed; a LATENT
                                      filing without a reproduction_attempted
                                      statement is refused naming the field.
  AC-007 / CT-003 / FR-005 / OT-005   a LATENT security-property claim is
                                      refused naming SECURITY_PROPERTY_CLAIM
                                      and fires the audit tripwire; a LATENT
                                      filing carrying only a spec_ref and a
                                      scan-gap description is ACCEPTED.
  AC-010 / CT-002 / FR-007 / OT-029   class is required at both doors, and the
                                      batch door refuses the whole batch.
  FR-029                              the content check on the negative-result
                                      statement refuses a placeholder by name.
  C-2                                 the persisted record's shape.

``validate_defect_filing`` is tested DIRECTLY as well as through
``foundry_add_defect``, because the batch door ``foundry_sync_defects`` is
casting 3's file and lands in wave 3: the helper is the contract those two
doors share, so it is the thing that must be pinned now. The finding-dict shape
used in those tests is the shape that door passes through.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from foundry_mcp.schemas.vocab import DEFECT_TIERS, SECURITY_PROPERTY_CLAIM
from foundry_mcp.tools.foundry import (
    foundry_add_defect,
    foundry_init,
    validate_defect_filing,
)
from foundry_mcp.tools.foundry_state import clear_active_run


@pytest.fixture
def run_env(tmp_path):
    """Activate a foundry run under tmp_path; yield (project_root, fdir)."""
    result = foundry_init(project_root=str(tmp_path))
    try:
        yield str(tmp_path), Path(result["foundry_dir"])
    finally:
        clear_active_run()


def _defects(fdir: Path) -> list[dict]:
    return json.loads((fdir / "defects.json").read_text(encoding="utf-8"))["defects"]


def _tripwire(fdir: Path) -> list[dict]:
    return json.loads((fdir / "observations.json").read_text(encoding="utf-8"))["tripwire"]


def _finding(**overrides) -> dict:
    """The finding-dict shape ``foundry_sync_defects`` passes to the validator."""
    finding = {
        "description": "the handler is registered but never called",
        "spec_ref": "",
        "target_kind": "",
        "symbol": "",
        "file": "src/api/handler.py",
        "tier": "LIVE",
        "class": "UNWIRED_HANDLER",
        "reproduction_attempted": "",
    }
    finding.update(overrides)
    return finding


# --- AC-006 / CT-001 / FR-004: the tier is required and closed ---------------
def test_a_filing_without_a_tier_is_refused_naming_the_field(run_env):
    """AC-006 verbatim: 'A Foundry-Defect or Foundry-Sync call without tier, or
    with a tier outside {LIVE, LATENT}, is refused naming the field.'"""
    project_root, fdir = run_env

    result = foundry_add_defect(
        cycle=0,
        source="trace",
        defect_type="UNWIRED",
        description="the handler is registered but never called",
        defect_class="UNWIRED_HANDLER",
        project_root=project_root,
    )

    assert result["ok"] is False, result
    assert result["field"] == "tier"
    assert "tier" in result["error"]
    assert result["hint"]
    assert _defects(fdir) == [], "a refused filing must persist nothing"


def test_a_tier_outside_the_closed_vocabulary_is_refused(run_env):
    """AC-006 — 'or with a tier outside {LIVE, LATENT}'. MINOR is the shape the
    abolished severity grade would return in: a value that means 'less
    important than LIVE'. It is not coerced onto LATENT, it is refused."""
    project_root, fdir = run_env

    result = foundry_add_defect(
        cycle=0,
        source="trace",
        defect_type="UNWIRED",
        description="the handler is registered but never called",
        defect_class="UNWIRED_HANDLER",
        tier="MINOR",
        project_root=project_root,
    )

    assert result["ok"] is False
    assert result["field"] == "tier"
    assert "MINOR" in result["error"]
    assert _defects(fdir) == []


def test_the_refusal_lists_the_accepted_tiers_from_the_vocabulary(run_env):
    """FR-013's rule applied to this refusal: the accepted values are READ from
    ``DEFECT_TIERS``, never hand-typed. A refusal naming a pair the runtime no
    longer accepts is the drift the vocabulary module exists to stop."""
    project_root, _ = run_env

    result = foundry_add_defect(
        cycle=0,
        source="trace",
        defect_type="UNWIRED",
        description="the handler is registered but never called",
        defect_class="UNWIRED_HANDLER",
        tier="",
        project_root=project_root,
    )

    for tier in DEFECT_TIERS:
        assert tier in result["error"], (
            f"the refusal never names the accepted tier {tier!r}"
        )


# --- AC-006 / FR-029 / OT-004: the LATENT negative-result statement ----------
def test_a_latent_filing_without_a_statement_is_refused_naming_it(run_env):
    """OT-004 verbatim: 'A Foundry-Defect call with tier LATENT and no
    reproduction_attempted is refused naming the field; the same call with the
    statement succeeds and stores both.' This is the first half."""
    project_root, fdir = run_env

    result = foundry_add_defect(
        cycle=0,
        source="prove",
        defect_type="MISSING",
        description="the sweep covers one root, so a second-root site is unseen",
        defect_class="SCAN_COVERAGE_GAP",
        tier="LATENT",
        project_root=project_root,
    )

    assert result["ok"] is False
    assert result["field"] == "reproduction_attempted"
    assert "reproduction_attempted" in result["error"]
    assert _defects(fdir) == []


@pytest.mark.parametrize("placeholder", ["n/a", "none", "TBD", "  todo  ", ""])
def test_a_placeholder_statement_is_refused_by_name(run_env, placeholder):
    """FR-029 verbatim: 'a placeholder or empty statement is refused by name'.

    The placeholders are the spellings of 'I did not': a LATENT filing's whole
    evidence IS the negative result, so a token in that field is a filing with
    no evidence at all wearing the shape of one that has some."""
    project_root, fdir = run_env

    result = foundry_add_defect(
        cycle=0,
        source="prove",
        defect_type="MISSING",
        description="the sweep covers one root, so a second-root site is unseen",
        defect_class="SCAN_COVERAGE_GAP",
        tier="LATENT",
        reproduction_attempted=placeholder,
        project_root=project_root,
    )

    assert result["ok"] is False, f"{placeholder!r} was accepted as evidence"
    assert result["field"] == "reproduction_attempted"
    assert _defects(fdir) == []


def test_a_too_short_statement_is_refused(run_env):
    """FR-029 — the content check. A statement shorter than the floor cannot
    carry both a subject and a negative result, so it is a token by another
    spelling and is refused for the same reason a placeholder is."""
    project_root, _ = run_env

    result = foundry_add_defect(
        cycle=0,
        source="prove",
        defect_type="MISSING",
        description="the sweep covers one root, so a second-root site is unseen",
        defect_class="SCAN_COVERAGE_GAP",
        tier="LATENT",
        reproduction_attempted="looked, nothing",
        project_root=project_root,
    )

    assert result["ok"] is False
    assert result["field"] == "reproduction_attempted"


def test_a_latent_filing_with_a_real_statement_stores_both_fields(run_env):
    """OT-004, second half: 'the same call with the statement succeeds and
    stores both.'"""
    project_root, fdir = run_env
    statement = "AST sweep of both roots finds 0 sites"

    result = foundry_add_defect(
        cycle=0,
        source="prove",
        defect_type="MISSING",
        description="the sweep covers one root, so a second-root site is unseen",
        defect_class="SCAN_COVERAGE_GAP",
        tier="LATENT",
        reproduction_attempted=statement,
        project_root=project_root,
    )

    assert result["defect_id"] == "D-001", result
    record = _defects(fdir)[0]
    assert record["tier"] == "LATENT"
    assert record["reproduction_attempted"] == statement


# --- AC-007 / CT-003 / FR-005 / OT-005: the LATENT security denylist ---------
def test_a_latent_security_property_claim_is_refused_and_fires_the_tripwire(run_env):
    """OT-005 verbatim: 'A LATENT filing whose description asserts an
    authentication property is refused naming SECURITY_PROPERTY_CLAIM and a
    tripwire record appears.'

    A security-property claim is never a gap reasoned about — the standard may
    not be weakened, and 'I did not find it' is not an answer to 'is this
    secure'."""
    project_root, fdir = run_env

    result = foundry_add_defect(
        cycle=0,
        source="prove",
        defect_type="MISSING",
        description="the endpoint does not verify the auth token before writing",
        defect_class="MISSING_AUTH_GUARD",
        tier="LATENT",
        reproduction_attempted="AST sweep of both roots finds 0 sites",
        project_root=project_root,
    )

    assert result["ok"] is False
    assert SECURITY_PROPERTY_CLAIM in result["error"]
    assert result["denylist_class"] == SECURITY_PROPERTY_CLAIM
    assert _defects(fdir) == [], "the filing must not be persisted"

    fired = _tripwire(fdir)
    assert len(fired) == 1, fired
    assert fired[0]["denylist_class"] == SECURITY_PROPERTY_CLAIM
    assert fired[0]["source"] == "prove"
    assert "auth token" in fired[0]["description"]


def test_the_same_security_claim_filed_as_live_is_accepted(run_env):
    """The denylist is about the TIER, not the subject. The refusal above must
    not become a way to lose a security finding: the identical description
    filed as LIVE — the tier that carries a reproduction — is a defect and is
    persisted."""
    project_root, fdir = run_env

    result = foundry_add_defect(
        cycle=0,
        source="prove",
        defect_type="MISSING",
        description="the endpoint does not verify the auth token before writing",
        defect_class="MISSING_AUTH_GUARD",
        tier="LIVE",
        project_root=project_root,
    )

    assert result["defect_id"] == "D-001", result
    assert _defects(fdir)[0]["tier"] == "LIVE"


def test_a_spec_ref_alone_never_refuses_a_latent_filing(run_env):
    """CT-003 verbatim: 'spec_ref alone never refuses a LATENT filing', and
    OT-005: 'a LATENT filing citing NFR-002 with a scan-gap description is
    accepted.'

    ``never_demote_class`` returns SPEC_REQUIRED_BEHAVIOUR_CLAIM for ANY
    finding carrying a non-empty spec_ref, so routing the LATENT gate through
    it would refuse the majority of LATENT filings — every one that cites the
    requirement it is about. The gate consults the security predicate ONLY, and
    this is the test that fails if it is ever re-routed."""
    project_root, fdir = run_env

    result = foundry_add_defect(
        cycle=0,
        source="prove",
        defect_type="MISSING",
        description="the sweep reads one root, so a second-root site is unseen",
        spec_ref="NFR-002",
        defect_class="SCAN_COVERAGE_GAP",
        tier="LATENT",
        reproduction_attempted="AST sweep of both roots finds 0 sites",
        project_root=project_root,
    )

    assert result["defect_id"] == "D-001", result
    record = _defects(fdir)[0]
    assert record["spec_ref"] == "NFR-002"
    assert record["tier"] == "LATENT"
    assert _tripwire(fdir) == [], "a spec_ref must not fire the denylist tripwire"


# --- AC-010 / CT-002 / FR-007: class is required -----------------------------
@pytest.mark.parametrize("defect_class", ["", "   "])
def test_a_filing_without_a_class_is_refused_naming_it(run_env, defect_class):
    """AC-010 verbatim: 'A filing without a non-empty class is refused at both
    doors'. Escalation keys on the declared class, so a filing without one
    cannot recur as anything — it is invisible to ST-002 no matter how many
    times its root cause comes back."""
    project_root, fdir = run_env

    result = foundry_add_defect(
        cycle=0,
        source="trace",
        defect_type="UNWIRED",
        description="the handler is registered but never called",
        defect_class=defect_class,
        tier="LIVE",
        project_root=project_root,
    )

    assert result["ok"] is False
    assert result["field"] == "class"
    assert "class" in result["error"]
    assert _defects(fdir) == []


# --- C-2: the persisted record's shape ---------------------------------------
def test_the_persisted_record_carries_every_c2_field(run_env):
    """C-2 — the record shape casting 3's fix door and casting 5's report read
    back. ``regression_test``, ``authored_by`` and ``fix_commit`` are seeded
    null at FILING so an open defect answers 'who fixed this and with what
    test' with None rather than a KeyError."""
    project_root, fdir = run_env

    foundry_add_defect(
        cycle=4,
        source="trace",
        defect_type="UNWIRED",
        description="the handler is registered but never called",
        spec_ref="FR-007",
        symbol="handle_request",
        file_path="src/api/handler.py",
        defect_class="UNWIRED_HANDLER",
        tier="LIVE",
        project_root=project_root,
    )

    record = _defects(fdir)[0]
    assert record["tier"] == "LIVE"
    assert record["class"] == "UNWIRED_HANDLER"
    assert record["reproduction_attempted"] is None, (
        "a LIVE record's reproduction lives in the description; the field is "
        "null rather than empty, because absent evidence and empty evidence "
        "are different claims"
    )
    assert record["regression_test"] is None
    assert record["authored_by"] is None
    assert record["fix_commit"] is None
    # ST-001 — the server's counter is still the authority and the caller's
    # claim is still persisted beside it. The new fields did not displace it.
    assert record["cycle"] == 0
    assert record["declared_cycle"] == 4


def test_the_class_key_is_written_unconditionally(run_env):
    """C-2 / FR-007 — ``class`` moved out of the trailing ``if defect_class:``
    block and into the record literal. The validator has already refused an
    absent one, so a keyless record can no longer be produced here, and
    escalation no longer has to handle a shape it can never be handed."""
    project_root, fdir = run_env

    foundry_add_defect(
        cycle=0,
        source="trace",
        defect_type="UNWIRED",
        description="the handler is registered but never called",
        defect_class="UNWIRED_HANDLER",
        tier="LIVE",
        project_root=project_root,
    )

    assert "class" in _defects(fdir)[0]


# --- the shared validator, driven directly (the batch door's contract) -------
def test_the_validator_accepts_a_well_formed_live_finding():
    """CT-001 / CT-002 — None means 'may be persisted'. Driven on the
    finding-dict shape ``foundry_sync_defects`` passes through, because that
    door is casting 3's and lands in wave 3: the helper is what the two doors
    share, so it is what must be pinned before the second one arrives."""
    assert validate_defect_filing(_finding()) is None


def test_the_validator_accepts_a_well_formed_latent_finding():
    """CT-001 — the LATENT lane, with the negative result named."""
    assert (
        validate_defect_filing(
            _finding(
                tier="LATENT",
                reproduction_attempted="AST sweep of both roots finds 0 sites",
            )
        )
        is None
    )


@pytest.mark.parametrize(
    "overrides,field",
    [
        ({"tier": None}, "tier"),
        ({"tier": "MINOR"}, "tier"),
        ({"tier": 1}, "tier"),
        ({"class": ""}, "class"),
        ({"class": None}, "class"),
        ({"tier": "LATENT"}, "reproduction_attempted"),
        (
            {"tier": "LATENT", "reproduction_attempted": "n/a"},
            "reproduction_attempted",
        ),
        (
            {
                "tier": "LATENT",
                "reproduction_attempted": "AST sweep of both roots finds 0 sites",
                "description": "the endpoint does not verify the auth token",
            },
            "description",
        ),
    ],
)
def test_the_validator_names_the_offending_field(overrides, field):
    """OT-029's mechanism: the batch door refuses the whole batch 'naming the
    finding', which it can only do because the validator hands it back the
    field that failed. Every refusal is the house shape — ok/error/hint — and
    carries ``field``."""
    refusal = validate_defect_filing(_finding(**overrides))

    assert refusal is not None, overrides
    assert refusal["ok"] is False
    assert refusal["field"] == field
    assert refusal["error"] and refusal["hint"]


def test_the_validator_check_order_is_locked():
    """The order is LOCKED so both doors name the same field first for the same
    bad filing. A finding wrong in every way must report ``tier`` — if the
    order drifted, the single door and the batch door would send two streams
    looking at two different fields for one finding."""
    everything_wrong = _finding(
        tier="",
        **{"class": ""},
        reproduction_attempted="",
        description="the endpoint does not verify the auth token",
    )

    assert validate_defect_filing(everything_wrong)["field"] == "tier"

    # ...and with the tier supplied, class is next, not the LATENT checks.
    everything_wrong["tier"] = "LATENT"
    assert validate_defect_filing(everything_wrong)["field"] == "class"

    # ...then the statement, and only then the denylist.
    everything_wrong["class"] = "MISSING_AUTH_GUARD"
    assert validate_defect_filing(everything_wrong)["field"] == "reproduction_attempted"

    everything_wrong["reproduction_attempted"] = "AST sweep of both roots finds 0 sites"
    refusal = validate_defect_filing(everything_wrong)
    assert refusal["field"] == "description"
    assert refusal["denylist_class"] == SECURITY_PROPERTY_CLAIM


def test_the_validator_reads_the_mapping_and_nothing_else(tmp_path):
    """Its locked contract: 'Reads the mapping and nothing else — no ledger
    read, no run-dir resolution, no write — so the batch door can call it once
    per finding BEFORE it opens its transaction, and so a refusal costs
    nothing.'

    Driven with NO active run: a validator that touched the run dir would fail
    here, and the batch door would then be unable to validate before locking.
    """
    clear_active_run()
    assert validate_defect_filing(_finding()) is None
    assert validate_defect_filing(_finding(tier=""))["field"] == "tier"
    assert list(tmp_path.iterdir()) == []


def test_the_validator_never_raises_on_a_hostile_mapping():
    """NFR-005 — the JSON layer can hand a filing door anything, and a tool
    never raises across the MCP boundary. Every value below is a shape the
    schema would reject but a direct caller can produce."""
    for hostile in (
        {},
        {"tier": ["LIVE"], "class": "X"},
        {"tier": "LATENT", "class": "X", "reproduction_attempted": 17},
        {"tier": "LATENT", "class": "X", "reproduction_attempted": None},
        {"tier": "LIVE", "class": {"a": 1}},
    ):
        refusal = validate_defect_filing(hostile)
        assert refusal is not None and refusal["ok"] is False, hostile
