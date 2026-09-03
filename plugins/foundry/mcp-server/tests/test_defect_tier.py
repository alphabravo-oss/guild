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
import re
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
        file_path="src/foundry_mcp/tools/foundry.py",
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
        file_path="src/foundry_mcp/tools/foundry.py",
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
    bad filing — if it drifted, the single door and the batch door would send
    two streams looking at two different fields for one finding.

    D-061 put the security denylist FIRST. A LATENT filing whose description
    asserts a security property is refused for THAT, whatever else is also
    wrong with it, because the tripwire the doors fire keys on the returned
    refusal and an audit control a filer can switch off by also omitting a
    field is not a control (AC-007 / OT-005 / CT-003)."""
    everything_wrong = _finding(
        tier="LATENT",
        **{"class": ""},
        reproduction_attempted="",
        description="the endpoint does not verify the auth token",
    )

    refusal = validate_defect_filing(everything_wrong)
    assert refusal["field"] == "description"
    assert refusal["denylist_class"] == SECURITY_PROPERTY_CLAIM

    # ...and with the security claim out of the description, tier is next.
    everything_wrong["description"] = "the handler is registered but never called"
    everything_wrong["tier"] = ""
    assert validate_defect_filing(everything_wrong)["field"] == "tier"

    # ...then class, not the LATENT checks.
    everything_wrong["tier"] = "LATENT"
    assert validate_defect_filing(everything_wrong)["field"] == "class"

    # ...and last the negative-result statement.
    everything_wrong["class"] = "UNWIRED_HANDLER"
    assert validate_defect_filing(everything_wrong)["field"] == "reproduction_attempted"

    everything_wrong["reproduction_attempted"] = "AST sweep of both roots finds 0 sites"
    assert validate_defect_filing(everything_wrong) is None


def test_the_denylist_outranks_a_tier_outside_the_vocabulary():
    """CT-003 scopes the denylist to LATENT, and the first rung keys on the tier
    the caller DECLARED. A filing with no tier is not a LATENT filing: it is
    refused naming ``tier`` exactly as it always was, so the new rung cannot
    swallow the tier refusal for a finding that never claimed the lane."""
    for tier in ("", None, "MINOR", "LIVE"):
        refusal = validate_defect_filing(
            _finding(
                tier=tier,
                description="the endpoint does not verify the auth token",
            )
        )
        if tier == "LIVE":
            assert refusal is None, (
                "a LIVE security-property filing is exactly what the denylist "
                "wants — driven and observed — and is never refused"
            )
        else:
            assert refusal["field"] == "tier", tier
            assert "denylist_class" not in refusal


@pytest.mark.parametrize(
    "earlier_rung",
    [
        {"reproduction_attempted": ""},
        {"reproduction_attempted": "n/a"},
        {"defect_class": ""},
    ],
    ids=["no-statement", "placeholder-statement", "no-class"],
)
def test_the_security_tripwire_fires_even_when_an_earlier_rung_fails(
    run_env, earlier_rung
):
    """D-061 / AC-007 / OT-005 — 'a LATENT filing whose description matches the
    security-property predicate is refused naming SECURITY_PROPERTY_CLAIM and a
    tripwire record is written'. Unconditional on the description matching: the
    doors fire ``record_denylist_tripwire`` only when the refusal they got back
    carries ``denylist_class``, so with the denylist behind the tier, class and
    reproduction_attempted rungs a hand-waved security claim with no
    negative-result evidence — the exact shape a stream files LATENT — left no
    audit trace at all."""
    project_root, fdir = run_env

    args = {
        "cycle": 0,
        "source": "prove",
        "defect_type": "PARTIAL",
        "description": (
            "the login endpoint does not verify the authentication token "
            "signature"
        ),
        "defect_class": "MISSING_AUTH_GUARD",
        "tier": "LATENT",
        "reproduction_attempted": "AST sweep of both roots finds 0 sites",
        "project_root": project_root,
    }
    args.update(earlier_rung)

    result = foundry_add_defect(**args)

    assert result["ok"] is False, result
    assert result["field"] == "description", result
    assert result["denylist_class"] == SECURITY_PROPERTY_CLAIM
    assert SECURITY_PROPERTY_CLAIM in result["error"]
    assert len(_tripwire(fdir)) == 1, "the attempt is what the audit record is for"
    assert _defects(fdir) == [], "a refused filing must persist nothing"


@pytest.mark.parametrize(
    "earlier_rung",
    [
        {"reproduction_attempted": ""},
        {"reproduction_attempted": "n/a"},
        {"class": ""},
    ],
    ids=["no-statement", "placeholder-statement", "no-class"],
)
def test_the_batch_door_fires_the_tripwire_on_the_same_filing(run_env, earlier_rung):
    """The adjacent path: ``foundry_sync_defects`` is the OTHER caller of
    ``validate_defect_filing``, and it is the door a whole INSPECT stream files
    through — so a hole there is the common path, not the rare one. Both doors
    fire the audit record off the same returned ``denylist_class``, which is
    why the reorder had to happen inside the shared validator rather than at
    either call site (D-061)."""
    from foundry_mcp.tools.foundry_orchestrator import foundry_sync_defects

    project_root, fdir = run_env

    finding = {
        "source": "prove",
        "type": "PARTIAL",
        "description": (
            "the login endpoint does not verify the authentication token "
            "signature"
        ),
        "spec_ref": "",
        "symbol": "",
        "file": "src/api/login.py",
        "class": "MISSING_AUTH_GUARD",
        "tier": "LATENT",
        "reproduction_attempted": "AST sweep of both roots finds 0 sites",
    }
    finding.update(earlier_rung)

    result = foundry_sync_defects(
        cycle=0, findings=[finding], project_root=project_root
    )

    assert "refusals" in result, result
    assert result["refusals"][0]["field"] == "description"
    assert result["refusals"][0]["denylist_class"] == SECURITY_PROPERTY_CLAIM
    assert len(_tripwire(fdir)) == 1
    assert _defects(fdir) == [], "the whole batch is refused, nothing recorded"


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


# --- D-101: a LATENT filing owes NO location; the cycle-5 rung is reversed ---
#
# These five pinned the `file_path` rung GRIND cycle 5 added under D-089. The
# lead REVERSED that ruling in cycle 6 (state.json spec_ambiguities entry 6)
# because the rung contradicts FR-005's "refuses LATENT only when the
# description matches the security-property regex", CT-001's two-refusal errors
# cell and CT-003's "spec_ref alone never refuses a LATENT filing". They are
# re-pointed rather than deleted: a rung that shipped once and was reversed
# needs a test asserting the reversal, or the next author reads D-089's comment
# and puts it back.
def test_a_latent_filing_without_a_file_path_is_accepted(run_env):
    """FR-005 verbatim: 'Server refuses LATENT only when the description
    matches the security-property regex ... naming the denylist class.'

    `only` is the whole word. A LATENT filing that named its tier, its class
    and its negative result has satisfied every refusal the contract admits,
    and a location it did not have is not one of them."""
    project_root, fdir = run_env

    result = foundry_add_defect(
        cycle=0,
        source="prove",
        defect_type="MISSING",
        description="the sweep covers one root, so a second-root site is unseen",
        symbol="sweep_roots",
        defect_class="SCAN_COVERAGE_GAP",
        tier="LATENT",
        reproduction_attempted="AST sweep of both roots finds 0 sites",
        project_root=project_root,
    )

    assert result["defect_id"] == "D-001", result
    record = _defects(fdir)[0]
    assert record["tier"] == "LATENT"
    assert record["file"] == "", "the absent location persists as absent"
    assert record["symbol"] == "sweep_roots"


@pytest.mark.parametrize("blank", ["", "   "])
def test_a_blank_file_path_is_accepted_like_an_absent_one(run_env, blank):
    """Whitespace is not a location either — and neither is refused. The two
    spellings of "no path" must reach the same verdict, whichever way the
    reversal went, or the door refuses on invisible characters."""
    project_root, fdir = run_env

    result = foundry_add_defect(
        cycle=0,
        source="prove",
        defect_type="MISSING",
        description="the sweep covers one root, so a second-root site is unseen",
        file_path=blank,
        defect_class="SCAN_COVERAGE_GAP",
        tier="LATENT",
        reproduction_attempted="AST sweep of both roots finds 0 sites",
        project_root=project_root,
    )

    assert result["defect_id"] == "D-001", blank
    assert _defects(fdir)[0]["file"] == blank


def test_a_live_filing_without_a_file_path_is_still_accepted(run_env):
    """Unchanged by the reversal, and kept for exactly that reason: LIVE was
    never gated on a location, so this is the test that says the reversal made
    the two tiers agree rather than swapping which one was wrong."""
    project_root, fdir = run_env

    result = foundry_add_defect(
        cycle=0,
        source="trace",
        defect_type="UNWIRED",
        description="the handler is registered but never called",
        defect_class="UNWIRED_HANDLER",
        tier="LIVE",
        project_root=project_root,
    )

    assert result["defect_id"] == "D-001", result
    assert _defects(fdir)[0]["file"] == ""


@pytest.mark.parametrize(
    "overrides",
    [{"file": ""}, {"file": "   "}, {"file": None}, {"file": 3}],
    ids=["empty", "whitespace", "null", "non-string"],
)
def test_the_validator_accepts_a_locationless_batch_door_shape(overrides):
    """The reversal reaches BOTH doors, and both doors are this one function.

    Driven on the finding-dict shape ``foundry_sync_defects`` passes straight
    through, whose location key is spelled ``file``. Every spelling of "no
    location" — absent, blank, null, not even a string — is accepted, because
    the validator no longer reads the key at all."""
    accepted = validate_defect_filing(
        _finding(
            tier="LATENT",
            reproduction_attempted="AST sweep of both roots finds 0 sites",
            **overrides,
        )
    )

    assert accepted is None, overrides


def test_reproduction_attempted_is_the_last_latent_rung():
    """With the cycle-5 rung gone, the negative-result statement is the final
    thing a LATENT filing owes. A filing that clears it clears the validator,
    with or without a location — which is the property the removed rung broke.
    """
    filing = _finding(tier="LATENT", reproduction_attempted="", file="")
    assert validate_defect_filing(filing)["field"] == "reproduction_attempted"

    filing["reproduction_attempted"] = "AST sweep of both roots finds 0 sites"
    assert validate_defect_filing(filing) is None, "no rung may follow this one"

    filing["file"] = "src/api/handler.py"
    assert validate_defect_filing(filing) is None


# --- D-101: the doors accept their own documented examples -------------------
#
# The rung was not caught by any test in this file for a whole cycle because
# every fixture here is hand-built, and a hand-built fixture is written to
# satisfy whatever the door currently demands. What it broke was the EXAMPLES —
# the JSON blocks a stream copies out of its own agent or skill file — and
# nothing swept those against the validator.
#
# The corpus is imported rather than re-derived: `DEFECT_FILING_SURFACES` is
# `test_protocol_prose`'s derivation over both directories a filing surface can
# live in (`agents/*.md`, `skills/*/SKILL.md`, filtered to those naming a
# filing door). A second hand-rolled corpus here would drift from it, and the
# drift would be invisible in exactly the way this pin exists to prevent.
def _documented_latent_examples() -> list[tuple[str, dict]]:
    """Every `tier: LATENT` object in a filing surface's normative examples."""
    from tests.test_protocol_prose import DEFECT_FILING_SURFACES

    found: list[tuple[str, dict]] = []
    for path in DEFECT_FILING_SURFACES:
        text = path.read_text(encoding="utf-8")
        for block in re.findall(r"^```json\n(.*?)^```$", text, re.S | re.M):
            try:
                parsed = json.loads(block)
            except json.JSONDecodeError:
                continue
            stack: list[object] = [parsed]
            while stack:
                node = stack.pop()
                if isinstance(node, dict):
                    if node.get("tier") == "LATENT":
                        found.append((path.name, node))
                    stack.extend(node.values())
                elif isinstance(node, list):
                    stack.extend(node)
    return found


def test_the_documented_latent_examples_corpus_is_not_empty():
    """Floor check: the pin below is vacuous if the parse stops finding them.

    A surface that renames its fence, or a corpus that stops deriving, would
    turn the sweep green by sweeping nothing — which is the shape of failure
    the rung it guards against had for a cycle."""
    examples = _documented_latent_examples()

    assert len(examples) >= 5, (
        f"only {len(examples)} documented LATENT examples found across the "
        f"filing surfaces. Either the surfaces stopped publishing one, or the "
        f"fence parse stopped matching. Fix whichever it is rather than "
        f"lowering this floor."
    )
    assert len({name for name, _ in examples}) >= 3, (
        "the documented LATENT examples now come from fewer than three files, "
        "so this sweep no longer spans the surfaces it is about."
    )


def test_every_documented_latent_example_passes_the_filing_door():
    """D-101 — a door that refuses its own documented example is the defect.

    Driven at the reversal, against the pre-edit validator: two of the six
    `tier: LATENT` examples published on the filing surfaces were refused by
    the cycle-5 `file_path` rung — `agents/coverage-diff.md`, whose finding is
    a casting that declares no coverage_list and is located by `casting_id`,
    and `skills/sight/SKILL.md`, whose finding is an empty-state gap located by
    `page` and `element`. Neither forgot a path; neither HAS one. A stream that
    copies the shape its own prose publishes and is refused for it learns that
    the prose is wrong, and its next move is to invent a location — which the
    backlog then reads as evidence."""
    refused = [
        (name, example.get("description", "")[:60], refusal["field"])
        for name, example in _documented_latent_examples()
        if (refusal := validate_defect_filing(example)) is not None
    ]

    assert refused == [], (
        f"the filing door refuses {len(refused)} of its own documented LATENT "
        f"examples: {refused}. Either the door gained a rung the surfaces do "
        f"not teach, or a surface publishes an example that was never valid. "
        f"Fix whichever one is wrong — never both, and never neither."
    )


# --- FR-051 / D-077: the untiered exit, at the single door too ---------------
def _seed_untiered(fdir: Path, cycle: int = 3, **overrides) -> dict:
    """One open PRE-CHANGE record: no ``tier`` key at all, which is the point.

    The shape ``tests/test_orchestrator_gates.py`` seeds for the batch door's
    half of this exit, so the two doors are driven against the same record.
    """
    record = {
        "id": "D-001", "cycle": 0, "source": "trace", "type": "UNWIRED",
        "description": "filed before the tier axis existed",
        "spec_ref": "CT-013", "symbol": "foundry_next",
        "file": "src/api/a.py", "status": "open", "fixed_in_cycle": None,
        "class": "UNWIRED_SURFACE",
    }
    record.update(overrides)
    (fdir / "defects.json").write_text(
        json.dumps({"defects": [record]}), encoding="utf-8"
    )
    state = json.loads((fdir / "state.json").read_text(encoding="utf-8"))
    state["cycle"] = cycle
    (fdir / "state.json").write_text(json.dumps(state), encoding="utf-8")
    return record


def _refile(project_root: str, **overrides) -> dict:
    """The same finding the seeded record names, re-filed WITH a tier."""
    args = {
        "cycle": 3,
        "source": "trace",
        "defect_type": "UNWIRED",
        "description": "re-filed with the tier the record never carried",
        "symbol": "foundry_next",
        "file_path": "src/api/a.py",
        "defect_class": "UNWIRED_SURFACE",
        "tier": "LATENT",
        "reproduction_attempted": (
            "drove every caller of the display path; none reaches the branch, "
            "so nothing reproduced"
        ),
        "project_root": project_root,
    }
    args.update(overrides)
    return foundry_add_defect(**args)


def test_re_filing_an_untiered_record_at_the_single_door_classifies_it(run_env):
    """FR-051 verbatim: 'blocks like LIVE UNTIL A STREAM RE-FILES IT WITH A
    TIER.' AC-008. D-077.

    D-062 implemented that exit at ONE of the two doors. Driven before this
    fix, on this ledger: Foundry-Sync returned {'retiered': 1, 'retiered_ids':
    ['D-001'], 'added': 0, 'total_open': 1} and D-001 carried LATENT, while
    Foundry-Defect — the door every stream's prose instructs — returned
    {'defect_id': 'D-002'}, left D-001 open and untiered, and appended a
    duplicate beside it."""
    project_root, fdir = run_env
    _seed_untiered(fdir)

    result = _refile(project_root)

    assert result["defect_id"] == "D-001", result
    assert result["retiered"] == 1, "a re-filing classifies; it does not duplicate"
    assert result["retiered_ids"] == ["D-001"]
    assert result["total_defects"] == 1, "no duplicate was appended"
    assert result["open_defects"] == 1

    records = _defects(fdir)
    assert len(records) == 1, records
    assert records[0]["id"] == "D-001", "the record KEEPS its id, so cites stay valid"
    assert records[0]["tier"] == "LATENT"
    assert records[0]["reproduction_attempted"].startswith("drove every caller")
    assert records[0]["retiered_in_cycle"] == 3, "the server's cycle, not the caller's"


def test_the_single_door_retier_clears_the_blocking_bucket(run_env):
    """THE ADJACENT PATH: the gate reader, one module over.

    FR-051's exit is only worth having if it MOVES something. ``_blocking_defects``
    in ``foundry_orchestrator.py`` buckets by ``vocab.defect_tier`` and treats
    TIER_UNKNOWN exactly like LIVE, and D-077's whole complaint is that through
    this door the blocking count stayed at 1. This drives that reader before
    and after."""
    from foundry_mcp.tools import foundry_orchestrator as fo

    project_root, fdir = run_env
    _seed_untiered(fdir)
    assert fo._blocking_defects(fdir)["unknown"] == ["D-001"]
    assert fo._blocking_defects(fdir)["blocking"] == 1

    _refile(project_root)

    after = fo._blocking_defects(fdir)
    assert after["unknown"] == [], after
    assert after["blocking"] == 0, "the LATENT classification must unblock the gate"


def test_the_batch_door_still_classifies_the_same_untiered_record(run_env):
    """THE OTHER ADJACENT PATH: ``foundry_sync_defects``, the second caller of
    the rule this fix moved into a shared helper.

    Lifting a rule out of one door and into a helper both call is only safe if
    the door it came FROM still behaves identically. Same seeded record, same
    finding, driven through the batch door."""
    from foundry_mcp.tools.foundry_orchestrator import foundry_sync_defects

    project_root, fdir = run_env
    _seed_untiered(fdir)

    result = foundry_sync_defects(
        cycle=3,
        findings=[{
            "source": "trace", "type": "UNWIRED", "symbol": "foundry_next",
            "file": "src/api/a.py", "class": "UNWIRED_SURFACE",
            "tier": "LATENT",
            "reproduction_attempted": (
                "drove every caller of the display path; none reaches the "
                "branch, so nothing reproduced"
            ),
            "description": "re-filed with the tier the record never carried",
        }],
        project_root=project_root,
    )

    assert result["ok"] is True, result
    assert result["added"] == 0
    assert result["retiered"] == 1
    assert result["retiered_ids"] == ["D-001"]
    assert _defects(fdir)[0]["tier"] == "LATENT"


def test_both_doors_report_the_retier_under_the_same_keys(run_env):
    """The two doors return ``retiered`` as a count and ``retiered_ids`` as a
    list, so a lead or a report reading either result handles ONE shape. A bool
    on the single door would have been the natural spelling and is exactly the
    per-door divergence that produced D-119 and then D-077."""
    project_root, fdir = run_env
    _seed_untiered(fdir)

    result = _refile(project_root)
    assert isinstance(result["retiered"], int)
    assert not isinstance(result["retiered"], bool)
    assert isinstance(result["retiered_ids"], list)

    # ...and the same two keys are present, zeroed, on an ordinary filing.
    plain = _refile(project_root, symbol="somewhere_else")
    assert plain["retiered"] == 0
    assert plain["retiered_ids"] == []
    assert plain["defect_id"] == "D-002"


def test_a_record_a_stream_already_tiered_is_never_rewritten(run_env):
    """Only an UNTIERED record takes this path. A record some stream already
    classified is answerable for its evidence, and a re-filing must not
    silently move a classification mid-run."""
    project_root, fdir = run_env
    _seed_untiered(fdir, tier="LIVE")

    result = _refile(project_root)

    assert result["retiered"] == 0
    assert result["defect_id"] == "D-002", "it files as a new record instead"
    records = _defects(fdir)
    assert records[0]["tier"] == "LIVE", "the earlier classification stands"


def test_a_fixed_untiered_record_is_not_retiered(run_env):
    """The match is scoped to OPEN records. A closed one is the regression
    path's business (``_is_regression_of`` reopens it), and re-tiering it here
    would resurrect a fixed defect without reopening it."""
    project_root, fdir = run_env
    _seed_untiered(fdir, status="fixed", fixed_in_cycle=1)

    result = _refile(project_root)

    assert result["retiered"] == 0
    assert result["defect_id"] == "D-002"
    assert "tier" not in _defects(fdir)[0]


@pytest.mark.parametrize(
    "differs",
    [
        {"source": "prove"},
        {"defect_type": "MISSING"},
        {"file_path": "src/api/b.py"},
        {"symbol": "foundry_gate"},
    ],
    ids=["source", "type", "file", "symbol"],
)
def test_identity_is_source_type_file_and_symbol(run_env, differs):
    """The four fields that say WHICH finding this is. A re-filing that differs
    on any of them is a DIFFERENT finding and files a new record — otherwise
    one untiered record would swallow every later filing that happened to
    arrive while it was open."""
    project_root, fdir = run_env
    _seed_untiered(fdir)

    result = _refile(project_root, **differs)

    assert result["retiered"] == 0, differs
    assert result["defect_id"] == "D-002"
    assert len(_defects(fdir)) == 2
    assert "tier" not in _defects(fdir)[0]


def test_the_description_is_excluded_from_identity(run_env):
    """Excluded on purpose: a re-filing stream rewrites its prose, and
    requiring the wording to match would make the exit unreachable for exactly
    the reason the server's hint was."""
    project_root, fdir = run_env
    _seed_untiered(fdir)

    result = _refile(
        project_root,
        description="entirely different words for the same unwired surface",
    )

    assert result["retiered"] == 1, result
    assert _defects(fdir)[0]["tier"] == "LATENT"


def test_the_retier_matches_on_the_canonical_type_spelling(run_env):
    """MISPLACED and ARCHITECTURAL_PLACEMENT are ONE type under two live
    spellings (D-018) and both doors PERSIST the canonical one. Matching the
    caller's raw alias would miss the record it is meant to classify — the
    duplicate D-077 reports, arriving by a different route."""
    project_root, fdir = run_env
    _seed_untiered(fdir, type="ARCHITECTURAL_PLACEMENT")

    result = _refile(project_root, defect_type="MISPLACED")

    assert result["retiered"] == 1, result
    assert _defects(fdir)[0]["tier"] == "LATENT"


def test_a_live_refiling_stores_a_null_reproduction_statement(run_env):
    """C-2 — ``reproduction_attempted`` is the LATENT lane's evidence and is
    explicitly ``None`` on a LIVE record, whose reproduction lives in the
    description. The re-tier writes the record the same way the append does."""
    project_root, fdir = run_env
    _seed_untiered(fdir)

    result = _refile(project_root, tier="LIVE", reproduction_attempted="")

    assert result["retiered"] == 1, result
    record = _defects(fdir)[0]
    assert record["tier"] == "LIVE"
    assert record["reproduction_attempted"] is None


def test_the_retier_never_moves_a_class_the_record_already_declared(run_env):
    """A class the earlier filing declared is what escalation has been keying
    on, so overwriting it here would move a class mid-run — every ST-002 count
    against the old name would silently stop accruing."""
    project_root, fdir = run_env
    _seed_untiered(fdir)

    result = _refile(project_root, defect_class="SOMETHING_ELSE")

    assert result["retiered"] == 1, "the re-tier must actually have run"
    assert _defects(fdir)[0]["class"] == "UNWIRED_SURFACE", "declared class stands"
    assert _defects(fdir)[0]["tier"] == "LATENT", "the TIER is what a re-filing moves"


def test_the_retier_fills_a_class_the_record_never_had(run_env):
    """The other half: an ABSENT class is filled, so a re-tiered pre-change
    record is not left invisible to ST-002. `class` became required only with
    the tier axis, so a genuinely pre-change record can carry none."""
    project_root, fdir = run_env
    _seed_untiered(fdir)
    ledger = json.loads((fdir / "defects.json").read_text(encoding="utf-8"))
    del ledger["defects"][0]["class"]
    (fdir / "defects.json").write_text(json.dumps(ledger), encoding="utf-8")

    _refile(project_root, defect_class="SOMETHING_ELSE")

    assert _defects(fdir)[0]["class"] == "SOMETHING_ELSE", "an absent class is filled"


def test_the_helper_skips_a_record_it_could_not_name(run_env):
    """Driven directly, on the list shape ``ledger_transaction`` yields.

    A record with no usable id can be MUTATED but not REPORTED, and a caller
    told ``None`` files the duplicate anyway — the very outcome D-077 reports.
    So the match must be one the helper can both classify and name."""
    from foundry_mcp.tools.foundry import retier_matching_untiered

    records = [
        {"id": "", "status": "open", "source": "trace", "type": "UNWIRED",
         "file": "src/api/a.py", "symbol": "foundry_next"},
        {"id": "D-002", "status": "open", "source": "trace", "type": "UNWIRED",
         "file": "src/api/a.py", "symbol": "foundry_next"},
    ]

    got = retier_matching_untiered(
        records,
        source="trace",
        type="UNWIRED",
        file="src/api/a.py",
        symbol="foundry_next",
        tier="LIVE",
        reproduction_attempted="",
        defect_class="UNWIRED_SURFACE",
        cycle=4,
    )

    assert got == "D-002", "the id-less record is skipped, not half-handled"
    assert "tier" not in records[0], "and it is left untouched for the migration"
    assert records[1]["tier"] == "LIVE"


def test_the_helper_tolerates_a_non_dict_historical_record():
    """NFR-005 — it iterates through ``_dict_records`` like every other scan of
    this ledger. A raw loop raises ``AttributeError`` on a malformed historical
    record, and the raise would land INSIDE the transaction, aborting it and
    silently discarding the filing the stream just made."""
    from foundry_mcp.tools.foundry import retier_matching_untiered

    assert retier_matching_untiered(
        ["not-a-dict", None, 7],
        source="trace",
        type="UNWIRED",
        file="src/api/a.py",
        symbol="foundry_next",
        tier="LIVE",
        reproduction_attempted="",
        defect_class="UNWIRED_SURFACE",
        cycle=4,
    ) is None
