"""Casting 2 — every defect carries an evidence tier (convergence US-002).

One regression test per acceptance criterion, each docstring quoting the
requirement it proves. Built on the synthetic-run-directory shape
``tests/test_escalation.py`` establishes: a run activated under ``tmp_path``,
the real filing door driven, the persisted ledger read back.

Every requirement id below is a ``forge-specs/foundry-run-convergence`` id and
says so, because the same numbers exist in
``forge-specs/foundry-run-process-fixes`` naming entirely different
requirements. THREE IDS IN THIS FILE GO THE OTHER WAY: process-fixes FR-013
(one canonical vocabulary module), process-fixes ST-001 (the server owns the
cycle counter) and process-fixes ST-002 (a class escalates on the third
consecutive cycle). tests/test_spec_id_convention.py refuses the bare form in
either direction.

  convergence AC-006 / CT-001 / FR-004 / OT-004
      tier is required and closed; a LATENT filing without a
      reproduction_attempted statement is refused naming the field.
  convergence AC-007 / CT-003 / FR-005 / OT-005
      a LATENT security-property claim is refused naming
      SECURITY_PROPERTY_CLAIM and fires the audit tripwire; a LATENT filing
      carrying only a spec_ref and a scan-gap description is ACCEPTED.
  convergence AC-010 / CT-002 / FR-007 / OT-029
      class is required at both doors, and the batch door refuses the whole
      batch.
  convergence FR-029
      the content check on the negative-result statement refuses a placeholder
      by name.
  C-2
      the persisted record's shape.

Casting 4 of ``forge-specs/foundry-run-fallout`` added the HARDENING rows
below. They sit in this module rather than a new one because the rungs they
prove are rungs of ``validate_defect_filing`` — the same shared validator the
rows above prove — and a second module would have to re-establish the same
synthetic run and then drift from it.

  fallout AC-023 / CT-012 / FR-045 / OT-018
      a HARDENING filing carrying a reproduction is accepted at both doors,
      and the four terminal gates do not refuse on it.
  fallout AC-055 / FR-057 / GI-028 / OT-019
      a HARDENING filing carrying ANY spec_ref is refused naming
      TIER_NOT_ALLOWED, at both doors, on the same field.
  fallout GI-004 / NFR-004
      a HARDENING filing matching the never-demote denylist is refused and the
      tripwire records it, exactly as the LATENT one is.
  fallout FR-025 / CT-019 / AC-045 / OT-039
      fallout_of naming a known id is accepted and persisted; an unknown id is
      refused naming it; and the key is on every record whether it was set or
      not, because an absent key is what the measurement reads as never
      measured.
  fallout ST-006 / GI-022 / OT-020
      a filing citing an open HARDENING id under supersedes closes that record
      as superseded and leaves its tier exactly where the stream put it.
  fallout AC-023 / GI-004 / OT-019 (defect D-078)
      the never-demote denylist is enforced at the filing doors in FULL for
      HARDENING, not only through its security entry: a filing whose PROSE
      claims a stated requirement's behaviour is absent is refused and
      audited, while the LATENT contract (a spec_ref alone never refuses) and
      the tier's ordinary subject (`target_kind="code"`) are untouched.
  fallout GI-004 (lead ruling, GRIND cycle 4)
      the claim-vs-subject split is asked of the vocabulary at BOTH sites that
      turn on it, so a fifth claim entry joins them by construction.
  fallout GI-006 (defect D-079)
      the forge-log mirror carries the reproduction row for every tier the
      record literal writes it on — HARDENING included, whose evidence the
      door had just demanded as a condition of acceptance.
  fallout CT-012 (defect D-093, co-dispatched alignment)
      the single door's OWN advertised prose names every member of
      DEFECT_TIERS and scopes the reproduction to both tiers that owe one —
      the sibling of the wire-schema sentence `server.py#list_tools` carries.
  fallout GI-004 / GI-028 / AC-055 / OT-019 (defect D-157)
      the tier rules are asked of the RECORD an untiered re-tier classifies
      and not only of the filing that classifies it: a record whose own
      spec_ref or own claim prose the declared tier forbids is refused at both
      doors, the tripwire records the attempt, and nothing is appended beside
      the record that was not classified. The scope is NON_BLOCKING_TIERS, so
      a re-tier into LIVE — where such a finding belongs — is untouched.
  fallout ST-006 / GI-022 (defect D-053)
      a filing citing the id it is itself about to be given promotes NOTHING,
      at both doors — the transition is stated over two records, and on an
      empty ledger the mint is deterministic enough that the one-record case
      is reachable by a filer who simply guessed the next id.
  fallout NFR-011 (D-195's class)
      the row this module cites for the LATENT reproduction rung is the
      convergence tier contract, named with its spec and DERIVED from that
      spec's own table — and the same number under THIS run's name, where it
      is `Foundry-Concern`, is pinned as the spelling that must not come back.
      The cite lived in an assertion message, the one prose surface
      `prose_blocks` does not walk, which is why the convention pin above
      passed over it.

``validate_defect_filing`` is tested DIRECTLY as well as through
``foundry_add_defect``, because the batch door ``foundry_sync_defects`` is
casting 3's file and lands in wave 3: the helper is the contract those two
doors share, so it is the thing that must be pinned now. The finding-dict shape
used in those tests is the shape that door passes through.
"""

from __future__ import annotations

import ast
import inspect
import json
import re
import textwrap
from pathlib import Path

import pytest

from foundry_mcp.schemas.vocab import (
    DEFECT_TIERS,
    SECURITY_PROPERTY_CLAIM,
    SPEC_REQUIRED_BEHAVIOUR_CLAIM,
    is_security_property_text,
)
from foundry_mcp.tools.foundry import (
    foundry_add_defect,
    foundry_init,
    record_denylist_tripwire,
    validate_defect_filing,
)
from foundry_mcp.tools.foundry_state import clear_active_run

# fallout NFR-011 (D-195's class, fallout_of D-150) — the citation-resolution
# grammar, IMPORTED. `tests/test_skill_prose.py` owns it: which spellings
# qualify a cite, how a spec's own table is parsed into rows, and how a
# module's assertion MESSAGES are read out of its AST. The section at the tail
# of this module consumes that grammar on this module's one message that names
# the reproduction rung; it does not restate it. A second copy of the spec-row
# parser here would be exactly the duplicate-spelling class this run keeps
# filing, and `tests/test_evidence.py` already establishes that reaching a
# sibling test module's scanner helpers by name is how this suite shares them.
from tests.test_skill_prose import (
    SPEC_ROWS,
    _assertion_messages,
    _contract_rows_naming,
)


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


# --- convergence AC-006 / CT-001 / FR-004: the tier is required and closed ---
def test_a_filing_without_a_tier_is_refused_naming_the_field(run_env):
    """convergence AC-006 verbatim: 'A Foundry-Defect or Foundry-Sync call without tier, or
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
    """convergence AC-006 — 'or with a tier outside {LIVE, LATENT}'. MINOR is the shape the
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
    """process-fixes FR-013's rule applied to this refusal: the accepted values
    are READ from ``DEFECT_TIERS``, never hand-typed. A refusal naming a pair
    the runtime no
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


# --- convergence AC-006 / FR-029 / OT-004: the LATENT negative-result statement ---
def test_a_latent_filing_without_a_statement_is_refused_naming_it(run_env):
    """convergence OT-004 verbatim: 'A Foundry-Defect call with tier LATENT and no
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
    """convergence FR-029 verbatim: 'a placeholder or empty statement is refused by name'.

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
    """convergence FR-029 — the content check. A statement shorter than the floor cannot
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
    """convergence OT-004, second half: 'the same call with the statement succeeds and
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


# --- convergence AC-007 / CT-003 / FR-005 / OT-005: the LATENT security denylist ---
def test_a_latent_security_property_claim_is_refused_and_fires_the_tripwire(run_env):
    """convergence OT-005 verbatim: 'A LATENT filing whose description asserts an
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
    """convergence CT-003 verbatim: 'spec_ref alone never refuses a LATENT filing', and
    convergence OT-005: 'a LATENT filing citing convergence NFR-002 with a
    scan-gap description is accepted.'

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


# --- convergence AC-010 / CT-002 / FR-007: class is required -----------------
@pytest.mark.parametrize("defect_class", ["", "   "])
def test_a_filing_without_a_class_is_refused_naming_it(run_env, defect_class):
    """convergence AC-010 verbatim: 'A filing without a non-empty class is refused at both
    doors'. Escalation keys on the declared class, so a filing without one
    cannot recur as anything — it is invisible to process-fixes ST-002 no
    matter how many times its root cause comes back."""
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
    # process-fixes ST-001 — the server's counter is still the authority and the caller's
    # claim is still persisted beside it. The new fields did not displace it.
    assert record["cycle"] == 0
    assert record["declared_cycle"] == 4


def test_the_class_key_is_written_unconditionally(run_env):
    """C-2 / convergence FR-007 — ``class`` moved out of the trailing ``if defect_class:``
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
    """convergence CT-001 / CT-002 — None means 'may be persisted'. Driven on the
    finding-dict shape ``foundry_sync_defects`` passes through, because that
    door is casting 3's and lands in wave 3: the helper is what the two doors
    share, so it is what must be pinned before the second one arrives."""
    assert validate_defect_filing(_finding()) is None


def test_the_validator_accepts_a_well_formed_latent_finding():
    """convergence CT-001 — the LATENT lane, with the negative result named."""
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
    """convergence OT-029's mechanism: the batch door refuses the whole batch 'naming the
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
    field is not a control (convergence AC-007 / OT-005 / CT-003)."""
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
    """convergence CT-003 scopes the denylist to LATENT, and the first rung
    keys on the tier the caller DECLARED. A filing with no tier is not a LATENT
    filing: it is refused naming ``tier`` exactly as it always was, so the new
    rung cannot
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
    """D-061 / convergence AC-007 / OT-005 — 'a LATENT filing whose description matches the
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
    from foundry_mcp.tools.orchestration.fix_gate import foundry_sync_defects

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


# --- D-128: the tripwire is not rung-dependent at the rungs ABOVE the shared
# validator either. D-061 moved the denylist to the validator's first rung, but
# `foundry_add_defect` returned at its own source/defect_type vocabulary rungs
# BEFORE calling the validator at all, so the ordering property held only for
# filings that got the vocabulary right. The batch door accumulates those same
# two rungs and runs the validator anyway, so the two doors disagreed about
# whether one filing is audited.
_SECURITY_CLAIM = (
    "the login endpoint does not verify the authentication token signature"
)


@pytest.mark.parametrize(
    "vocabulary_rung,named",
    [
        ({"source": "bogus"}, "bogus"),
        ({"defect_type": "COSMETIC"}, "COSMETIC"),
    ],
    ids=["unknown-source", "unknown-defect-type"],
)
def test_the_security_tripwire_fires_when_a_vocabulary_rung_also_fails(
    run_env, vocabulary_rung, named
):
    """convergence AC-007 / OT-005 / CT-003 / FR-005 — 'A LATENT filing whose description
    matches the security-property predicate is refused naming
    SECURITY_PROPERTY_CLAIM and a tripwire record is written.'

    Unconditional on the description matching, which is the whole of D-061's
    ruling: an audit control a filer can switch off by ALSO getting another
    field wrong is not a control. D-061 closed that for the rungs INSIDE
    ``validate_defect_filing``; this door short-circuited one frame further out,
    at its own source and defect_type checks, so the same hand-waved security
    claim filed under a misspelt stream id was refused naming only the stream
    and left ``observations.json`` tripwire delta 0.
    """
    project_root, fdir = run_env

    args = {
        "cycle": 0,
        "source": "prove",
        "defect_type": "PARTIAL",
        "description": _SECURITY_CLAIM,
        "defect_class": "MISSING_AUTH_GUARD",
        "tier": "LATENT",
        "reproduction_attempted": "AST sweep of both roots finds 0 sites",
        "project_root": project_root,
    }
    args.update(vocabulary_rung)

    result = foundry_add_defect(**args)

    assert result["ok"] is False, result
    # Both faults are named in ONE refusal: the filer fixes the filing in one
    # pass rather than learning about the second rung only after fixing the
    # first.
    assert named in result["error"], result
    assert SECURITY_PROPERTY_CLAIM in result["error"], result
    assert result["denylist_class"] == SECURITY_PROPERTY_CLAIM, result
    # The vocabulary rung is still named FIRST, so the two doors name the same
    # field first for the same bad filing (the property D-098 pinned).
    assert result["field"] == next(iter(vocabulary_rung)), result
    assert "description" in result["fields"], result
    assert len(_tripwire(fdir)) == 1, "the attempt is what the audit record is for"
    assert _defects(fdir) == [], "a refused filing must persist nothing"


def test_both_doors_audit_the_same_unknown_source_security_filing(run_env):
    """The D-128 divergence stated directly: ONE filing, both doors, one answer.

    Driven in-process, because no MCP call can reach either handler with an
    unknown source — ``server._argument_refusal`` refuses it pre-dispatch on
    both tools — so the doors could disagree here indefinitely without any
    stream being able to observe it. That is what makes it a LATENT defect and
    not a reason to leave the doors disagreeing.
    """
    from foundry_mcp.tools.orchestration.fix_gate import foundry_sync_defects

    project_root, fdir = run_env
    shared = {
        "description": _SECURITY_CLAIM,
        "tier": "LATENT",
        "reproduction_attempted": "AST sweep of both roots finds 0 sites",
    }

    single = foundry_add_defect(
        cycle=0, source="bogus", defect_type="PARTIAL",
        defect_class="MISSING_AUTH_GUARD", project_root=project_root, **shared,
    )
    single_tripwires = len(_tripwire(fdir))

    batch = foundry_sync_defects(
        cycle=0,
        findings=[{
            "source": "bogus", "type": "PARTIAL", "spec_ref": "", "symbol": "",
            "file": "src/api/login.py", "class": "MISSING_AUTH_GUARD", **shared,
        }],
        project_root=project_root,
    )
    batch_tripwires = len(_tripwire(fdir)) - single_tripwires

    # Each door audits the attempt exactly once.
    assert single_tripwires == 1, single
    assert batch_tripwires == 1, batch
    # And each names BOTH faults, under the same two names.
    for answer in (single["error"], batch["error"]):
        assert "bogus" in answer, answer
        assert SECURITY_PROPERTY_CLAIM in answer, answer
    assert single["field"] == batch["refusals"][0]["field"] == "source"
    assert single["denylist_class"] == SECURITY_PROPERTY_CLAIM
    assert _defects(fdir) == [], "neither door records a refused filing"


def test_one_failing_rung_still_returns_that_rungs_refusal_unchanged(run_env):
    """The merge is reached ONLY by a filing that failed several rungs.

    D-128's fix walks the ladder instead of short-circuiting it, and the cost of
    getting that wrong is silent: every refusal this door already returned would
    gain a `Refused on N rungs` preamble and a `refusals` list that no caller
    asked for. A filing wrong in exactly one way is the overwhelmingly common
    refusal, and it must read exactly as it did before.
    """
    project_root, fdir = run_env

    only_bad_source = foundry_add_defect(
        cycle=0, source="bogus", defect_type="PARTIAL",
        description="the handler is registered but never called",
        defect_class="UNWIRED_HANDLER", tier="LIVE", project_root=project_root,
    )

    assert "bogus" in only_bad_source["error"], only_bad_source
    assert "Refused on" not in only_bad_source["error"], only_bad_source
    assert "refusals" not in only_bad_source, only_bad_source
    assert "fields" not in only_bad_source, only_bad_source
    assert _defects(fdir) == []


def test_the_renderer_shows_every_named_rung_of_a_merged_refusal(run_env):
    """ADJACENT PATH — ``display.py#_fmt_foundry_add_defect``.

    A different caller of this door's result than the MCP dispatch the defect
    was found through: ``server.py`` returns the dict, and this renderer is what
    a lead actually READS. It reads ``r["error"]`` and nothing else — not
    ``refusals``, not ``fields`` — so every rung has to survive in that one
    string or the screen silently loses the ones the structured keys carry, and
    a lead fixes the named fault, re-files, and meets the next one.
    """
    from foundry_mcp.tools.display import format_result

    project_root, fdir = run_env

    merged = foundry_add_defect(
        cycle=0, source="bogus", defect_type="PARTIAL",
        description=_SECURITY_CLAIM, defect_class="MISSING_AUTH_GUARD",
        tier="LATENT", reproduction_attempted="AST sweep finds 0 sites",
        project_root=project_root,
    )
    rendered = re.sub(r"\x1b\[[0-9;]*m", "", format_result("Foundry-Defect", merged))

    assert "bogus" in rendered, rendered
    assert SECURITY_PROPERTY_CLAIM in rendered, rendered


def test_the_retier_transition_still_reaches_the_ledger(run_env):
    """ADJACENT PATH — the re-tier transition through this same door.

    A DIFFERENT transition than the refusal ladder the defect was found on: it
    is reached only by a filing that passes every rung and then matches an open
    untiered record, so it sits past the exact code D-128 restructured. Walking
    the ladder instead of returning from it must leave the accepting path
    untouched — a filing that fails nothing must still classify D-001 in place
    rather than appending beside it (convergence FR-051 / D-077).
    """
    project_root, fdir = run_env
    (fdir / "defects.json").write_text(
        json.dumps({"defects": [{
            "id": "D-001", "cycle": 0, "source": "trace", "type": "UNWIRED",
            "description": "filed before the tier axis existed", "spec_ref": "",
            "symbol": "handle", "file": "src/api/a.py", "status": "open",
            "fixed_in_cycle": None,
        }]}),
        encoding="utf-8",
    )

    result = foundry_add_defect(
        cycle=0, source="trace", defect_type="UNWIRED",
        description="the same finding, re-filed with a tier",
        symbol="handle", file_path="src/api/a.py",
        defect_class="UNWIRED_SURFACE", tier="LATENT",
        reproduction_attempted="drove every caller; none reach it",
        project_root=project_root,
    )

    assert result["retiered_ids"] == ["D-001"], result
    assert [d["id"] for d in _defects(fdir)] == ["D-001"], "classified, not appended"
    assert _defects(fdir)[0]["tier"] == "LATENT"


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
    """convergence NFR-005 — the JSON layer can hand a filing door anything, and a tool
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
# because the rung contradicts convergence FR-005's "refuses LATENT only when the
# description matches the security-property regex", convergence CT-001's two-refusal errors
# cell and convergence CT-003's "spec_ref alone never refuses a LATENT filing". They are
# re-pointed rather than deleted: a rung that shipped once and was reversed
# needs a test asserting the reversal, or the next author reads D-089's comment
# and puts it back.
def test_a_latent_filing_without_a_file_path_is_accepted(run_env):
    """convergence FR-005 verbatim: 'Server refuses LATENT only when the description
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


# --- convergence FR-051 / D-077: the untiered exit, at the single door too ---
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
    """convergence FR-051 verbatim: 'blocks like LIVE UNTIL A STREAM RE-FILES IT WITH A
    TIER.' convergence AC-008. D-077.

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

    convergence FR-051's exit is only worth having if it MOVES something.
    ``orchestration/gates.py#_blocking_defects`` buckets by
    ``vocab.defect_tier`` and treats
    TIER_UNKNOWN exactly like LIVE, and D-077's whole complaint is that through
    this door the blocking count stayed at 1. This drives that reader before
    and after."""
    from foundry_mcp.tools.orchestration.gates import _blocking_defects

    project_root, fdir = run_env
    _seed_untiered(fdir)
    assert _blocking_defects(fdir)["unknown"] == ["D-001"]
    assert _blocking_defects(fdir)["blocking"] == 1

    _refile(project_root)

    after = _blocking_defects(fdir)
    assert after["unknown"] == [], after
    assert after["blocking"] == 0, "the LATENT classification must unblock the gate"


def test_the_batch_door_still_classifies_the_same_untiered_record(run_env):
    """THE OTHER ADJACENT PATH: ``foundry_sync_defects``, the second caller of
    the rule this fix moved into a shared helper.

    Lifting a rule out of one door and into a helper both call is only safe if
    the door it came FROM still behaves identically. Same seeded record, same
    finding, driven through the batch door."""
    from foundry_mcp.tools.orchestration.fix_gate import foundry_sync_defects

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
    on, so overwriting it here would move a class mid-run — every
    process-fixes ST-002 count against the old name would silently stop
    accruing."""
    project_root, fdir = run_env
    _seed_untiered(fdir)

    result = _refile(project_root, defect_class="SOMETHING_ELSE")

    assert result["retiered"] == 1, "the re-tier must actually have run"
    assert _defects(fdir)[0]["class"] == "UNWIRED_SURFACE", "declared class stands"
    assert _defects(fdir)[0]["tier"] == "LATENT", "the TIER is what a re-filing moves"


def test_the_retier_fills_a_class_the_record_never_had(run_env):
    """The other half: an ABSENT class is filled, so a re-tiered pre-change
    record is not left invisible to process-fixes ST-002. `class` became
    required only with the tier axis, so a genuinely pre-change record can
    carry none."""
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
    """convergence NFR-005 — it iterates through ``_dict_records`` like every other scan of
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


# --- D-147: the denylist reads the FILING, not one field of it ---------------
#
# Driven at the moment D-147 was filed, through
# `server.call_tool('Foundry-Sync', ...)` over the real transport, in the
# filing shape `agents/coverage-diff.md` documents: description '', the
# security sentence in a `failure` key, tier LATENT. ACCEPTED — +1 Added,
# persisted with tier LATENT and description '', and `observations.json`
# tripwire delta 0. The gate read `description` and nothing else, so the one
# key the shipped stream prose puts its sentence in was the one key the gate
# did not look at.
#
# Every test below drives a shape a surface documents or a stream can send,
# never a hand-built worst case, because the hole was on the documented path.

#: The claim itself, spelled once. Any sentence asserting an authentication
#: property does; this one is `agents/*.md`'s own register.
_SMUGGLED_CLAIM = "the login endpoint does not verify the authentication token signature"


def _coverage_diff_latent(**overrides) -> dict:
    """`agents/coverage-diff.md`'s documented LATENT defect, in the shape these
    tests are about: its prose is in `failure`, it carries no `description` key
    at all, and it is located by `casting_id` rather than a path. That is the
    shape the driven filing wore.

    The `type` is deliberately NOT the one that surface publishes. It files as
    `MISSING` there — `MISSING_COVERAGE_LIST` is the stream's own flag name for
    what it found and stays in its report register, which is the distinction
    `coverage-diff.md`'s "The flag name is not the filed `type`" paragraph
    draws. A non-member spelling is held here on purpose, so the batch door's
    vocabulary rung fires ALONGSIDE the claim rung and the tests below have to
    name the refusal they are about instead of taking whichever came first.
    """
    finding = {
        "type": "MISSING_COVERAGE_LIST",
        "failure": "casting declares no coverage_list",
        "class": "migration-casting-shipped-without-a-coverage-list",
        "tier": "LATENT",
        "target_kind": "config",
        "reproduction_attempted": (
            "Read casting 7 must_haves in manifest.json and grepped its casting "
            "prompt for coverage_list; the key is absent from both, so there was "
            "no source entry to derive a destination from and no grep to drive"
        ),
        "casting_id": 7,
    }
    finding.update(overrides)
    return finding


def test_a_security_claim_in_a_key_other_than_description_is_refused():
    """convergence AC-007 / CT-003 / FR-005: 'A LATENT filing whose description matches the
    security-property predicate is refused naming SECURITY_PROPERTY_CLAIM.'

    D-147: `description` was read literally, so the predicate answered False
    for a filing whose claim sat one key over — and the batch door hands the
    caller's dict straight through, so every key a stream invents was a
    channel. The rung now reads every prose value the filing carries."""
    from foundry_mcp.schemas.vocab import is_security_property_text
    from foundry_mcp.tools.foundry import security_scan_text

    smuggled = _coverage_diff_latent(failure=_SMUGGLED_CLAIM)

    assert is_security_property_text(str(smuggled.get("description", ""))) is False, (
        "the one-field reading is what D-147 drove past; if this starts "
        "answering True the shape below stopped being the regression"
    )
    assert is_security_property_text(security_scan_text(smuggled)) is True

    refusal = validate_defect_filing(smuggled)
    assert refusal is not None, "the smuggled claim was accepted again"
    assert refusal["denylist_class"] == SECURITY_PROPERTY_CLAIM
    assert refusal["field"] == "description", (
        "the locked return contract admits four field names; the rung reads "
        "more keys than it names, and `description` is where a claim belongs"
    )


def test_the_batch_door_refuses_and_audits_a_claim_in_an_invented_key(run_env):
    """convergence OT-005 / AC-007: refused naming SECURITY_PROPERTY_CLAIM 'and a tripwire
    record appears' — driven end to end through the door that HAS the channel.

    RETIRED SHAPE (D-158). This test used to drive the SINGLE door with the
    claim in `reproduction_attempted`, on the argument that the single door
    builds its finding from named parameters so its only channel besides the
    description is that field. The argument was wrong about the field:
    `reproduction_attempted` reports what a search did NOT find, so treating
    it as a claim channel refused the LATENT filings convergence AC-007 clause 2 and
    convergence OT-005 clause 2 require the door to accept. See the D-158 block below for
    the two filings that drove that.

    The invented-key channel is real and it belongs to the BATCH door, which
    hands the caller's finding dict straight through — so that is the door
    driven here, in `agents/coverage-diff.md`'s documented shape: no
    `description` key at all, the sentence in `failure`. Refused whole,
    nothing persisted, and the audit record written under the class the
    refusal named."""
    from foundry_mcp.tools.orchestration.fix_gate import foundry_sync_defects

    project_root, fdir = run_env

    smuggled = _coverage_diff_latent(failure=_SMUGGLED_CLAIM, source="coverage_diff")

    result = foundry_sync_defects(
        cycle=0, findings=[smuggled], project_root=project_root
    )

    assert "refusals" in result, result
    assert SECURITY_PROPERTY_CLAIM in result["error"]

    # Located in the list rather than at index 0: the helper holds a `type` no
    # closed vocabulary admits (`MISSING_COVERAGE_LIST` is coverage-diff.md's
    # report-register flag name, not what it files), so the batch door's
    # vocabulary rung refuses alongside this one. That the audit record still
    # fires when an earlier rung also fails is D-128's property, pinned above;
    # what this test is about is the invented-key claim channel, so it names
    # its own refusal instead of depending on the order two unrelated rungs
    # report in.
    denylist = [r for r in result["refusals"] if r.get("denylist_class")]
    assert len(denylist) == 1, result["refusals"]
    assert denylist[0]["denylist_class"] == SECURITY_PROPERTY_CLAIM
    assert denylist[0]["field"] == "description"
    assert _defects(fdir) == [], "the filing must not be persisted"

    fired = _tripwire(fdir)
    assert len(fired) == 1, fired
    assert fired[0]["denylist_class"] == SECURITY_PROPERTY_CLAIM
    assert "authentication token" in fired[0]["description"], (
        "the audit record quotes the absent description instead of the prose "
        "that matched, so an auditor reading it learns nothing"
    )


def test_the_tripwire_names_the_class_the_refusal_named():
    """D-083's property, held one field along (D-147).

    `record_denylist_tripwire` re-derives the class through
    `vocab.never_demote_class`, whose security entry reads `description` alone.
    Once the refusal keys on all the prose and the derivation keys on one
    field, one event writes two artifacts that contradict each other — and an
    auditor querying the tripwire ledger for SECURITY_PROPERTY_CLAIM finds
    nothing for exactly the filings convergence AC-007 is about. `tripwire_finding` is what
    closes that, and this is the disagreement it closes."""
    from foundry_mcp.schemas.vocab import never_demote_class
    from foundry_mcp.tools.foundry import tripwire_finding

    smuggled = _coverage_diff_latent(failure=_SMUGGLED_CLAIM)

    assert never_demote_class(smuggled) != SECURITY_PROPERTY_CLAIM, (
        "the raw finding is what the door used to hand over; if the vocab "
        "predicate started reading the whole finding this substitution is "
        "redundant and should be removed rather than left to rot"
    )
    assert never_demote_class(tripwire_finding(smuggled)) == SECURITY_PROPERTY_CLAIM
    assert validate_defect_filing(smuggled)["denylist_class"] == SECURITY_PROPERTY_CLAIM


def test_a_claim_nested_below_the_top_level_is_read():
    """The same rung, on the shapes streams actually send: `skills/sight`
    documents an `evidence` key, and a finding is JSON the caller shaped, so a
    sentence can sit inside a nested object or a list. A scan that read only
    top-level strings would admit either."""
    nested = _coverage_diff_latent(
        failure="casting declares no coverage_list",
        evidence={"console": ["CE-1", _SMUGGLED_CLAIM]},
    )

    assert validate_defect_filing(nested)["denylist_class"] == SECURITY_PROPERTY_CLAIM


def test_locators_and_the_escalation_class_are_not_scanned():
    """convergence CT-003: 'spec_ref alone never refuses a LATENT filing' — and the same
    promise for the two locators and the escalation key beside it.

    `_SECURITY_RE` matches bounded tokens, so `src/auth/login.py` hits and so
    does `agents/assayer.md`'s own documented class,
    `no-auth-guard-on-destructive-endpoints`. Scanning those would refuse a
    shape a surface ships, and its filer could not re-word a path or rename an
    escalation key to recover — D-099/D-101's defect exactly. This is the test
    that fails if the exclusion list is dropped 'for completeness'."""
    located = _coverage_diff_latent(
        **{
            "class": "no-auth-guard-on-destructive-endpoints",
            "file": "src/auth/login.py",
            "symbol": "verify_auth_token",
            "spec_ref": "NFR-002",
            "type": "MISSING_AUTH_GUARD",
        }
    )

    assert validate_defect_filing(located) is None, (
        "a LATENT filing was refused for where it points rather than for what "
        "it claims"
    )


def test_a_live_filing_that_states_nothing_is_refused_naming_the_description():
    """convergence CT-001 input: 'for LIVE the door and observed wrong result in the
    description'. convergence FR-004 verbatim: 'LIVE needs the reproduction (door +
    observed wrong result).'

    D-147's secondary consequence: both doors accepted `description=''` and
    persisted a record that states nothing, so a reader of `defects.json`
    cannot tell what was wrong. A class and a file locate a finding; they do
    not state one."""
    refusal = validate_defect_filing(
        {"tier": "LIVE", "class": "UNWIRED_HANDLER", "file": "src/api/a.py",
         "symbol": "handle", "spec_ref": "US-002", "description": ""}
    )

    assert refusal is not None, "a LIVE record that states nothing was accepted"
    assert refusal["field"] == "description"
    assert refusal["ok"] is False and refusal["hint"]


def test_the_live_prose_floor_accepts_the_documented_shape_that_has_no_description():
    """The floor asks whether the filing says anything, not whether it filled a
    named key — because two of `agents/coverage-diff.md`'s three documented
    defects carry tier LIVE and no `description` key at all: the sentence is in
    `failure`, beside `source_entry` and `expected_destination`. A rung
    demanding the description KEY would refuse a shape that surface ships."""
    documented_live = {
        "type": "COVERAGE_INCOMPLETE",
        "source_entry": "internal/web/workloads_test.go:TestStatusInjection",
        "expected_destination": "internal/web/workloads_v2_test.go:TestStatusInjection",
        "failure": "destination symbol not found",
        "class": "casting-4-v2-port-is-incomplete",
        "tier": "LIVE",
        "target_kind": "test",
        "casting_id": 4,
    }

    assert validate_defect_filing(documented_live) is None


def test_the_live_prose_floor_cannot_refuse_a_latent_filing():
    """convergence FR-005 verbatim: 'Server refuses LATENT only when the description
    matches the security-property regex ... naming the denylist class.' `only`
    is the whole word, and D-101 reversed a LATENT `file_path` rung for
    breaking it.

    The floor is scoped to LIVE by construction rather than by reachability, so
    a LATENT filing with no description — the documented coverage-diff shape —
    is accepted on its `reproduction_attempted` statement alone."""
    assert validate_defect_filing(_coverage_diff_latent()) is None
    assert "description" not in _coverage_diff_latent(), (
        "the shape under test stopped being the one that has no description"
    )


def test_the_scan_text_is_deterministic_for_a_given_filing():
    """`security_scan_text` is quoted back into the tripwire record, and the
    D-083 property it serves is that two artifacts of ONE event agree. A caller
    assembling a finding in Python can hand it a set — never decoded JSON, but
    a test or a helper — and set iteration order is not stable across
    interpreters. Two scans of one filing must be one string."""
    from foundry_mcp.tools.foundry import security_scan_text

    finding = _coverage_diff_latent(
        evidence={"console": {"CE-1", "CE-2", "CE-3", _SMUGGLED_CLAIM}}
    )

    assert security_scan_text(finding) == security_scan_text(dict(finding))
    assert validate_defect_filing(finding)["denylist_class"] == SECURITY_PROPERTY_CLAIM


# --- D-158: the predicate reads what a filing CLAIMS, never what it SEARCHED
# FOR — convergence AC-007 clause 2, convergence OT-005 clause 2,
# convergence CT-003 and convergence FR-005
#
# D-147's fix widened the denylist rung from `description` to every prose value
# a filing carries, and swept in `reproduction_attempted` — the one field whose
# documented job (`skills/prove/SKILL.md`, `skills/trace/SKILL.md`: "Required on
# a LATENT finding: what was driven and what it found") is to say what was
# searched for and NOT found. So the rung began refusing the filings the spec
# requires it to ACCEPT, and the refusal named `description` while the
# description was innocent. Its hint read "Do NOT re-word the filing to get past
# this refusal", leaving a stream that followed the documented LATENT protocol
# with no path to file at all.
#
# The two filings below are the ones driven on both real doors at 148b3ae. Each
# is paired with a control that differs ONLY in the negative-space statement, so
# the pin fails if the door ever again decides a filing on that field.
_NEGATIVE_SPACE_STATEMENTS = (
    (
        "grepped both roots for a security section and found 0 sites",
        "grepped both roots for a minutes column and found 0 sites",
    ),
    (
        "drove the CLI with no credentials configured; 0 prompts appeared",
        "drove the CLI with no config present; 0 prompts appeared",
    ),
)


def _renderer_gap(statement: str) -> dict:
    """A LATENT filing with a spec_ref and a non-security description.

    convergence AC-007 clause 2 and convergence OT-005 clause 2 name exactly
    this shape as one the door must ACCEPT. Only `reproduction_attempted`
    varies across the pairs above.
    """
    return {
        "source": "prove",
        "type": "MISSING",
        "description": "The report renderer omits the per-cycle minutes column.",
        "spec_ref": "NFR-002",
        "symbol": "",
        "file": "src/report/render.py",
        "class": "report-renderer-gap",
        "tier": "LATENT",
        "reproduction_attempted": statement,
    }


@pytest.mark.parametrize(
    "matched,control", _NEGATIVE_SPACE_STATEMENTS, ids=("security", "credentials")
)
def test_a_negative_space_statement_never_decides_a_filing(matched, control):
    """convergence FR-005 verbatim: 'Server refuses LATENT only when the DESCRIPTION
    matches the security-property regex.'

    The statement names a security term because the filer SEARCHED for one and
    found nothing — which is the evidence convergence CT-001 demands of a LATENT filing.
    Asserted as a pair so the property is 'this field is not consulted' rather
    than 'this sentence happens to pass'."""
    assert is_security_property_text(matched) is True, (
        "the statement no longer contains a term the predicate matches, so "
        "this pair has stopped being the regression it was written for"
    )
    assert is_security_property_text(control) is False

    assert validate_defect_filing(_renderer_gap(matched)) is None, (
        "a LATENT filing was refused for what its filer searched for and did "
        "not find. convergence CT-001 asks for that statement; the denylist "
        "may not then refuse the filing on it."
    )
    assert validate_defect_filing(_renderer_gap(control)) is None


@pytest.mark.parametrize(
    "matched,control", _NEGATIVE_SPACE_STATEMENTS, ids=("security", "credentials")
)
def test_both_real_doors_accept_the_negative_space_statement(run_env, matched, control):
    """convergence AC-007 clause 2 verbatim: 'a LATENT filing with a spec_ref and a
    non-security description is accepted.' convergence OT-005 clause 2 says the same.

    Driven through both REAL doors rather than the shared validator, because
    D-158 shipped on both and the batch door is the one a whole INSPECT stream
    files through. Persisted, and the audit ledger untouched — a tripwire for
    an accepted filing would be the refusal's other half surviving alone."""
    from foundry_mcp.tools.orchestration.fix_gate import foundry_sync_defects

    project_root, fdir = run_env

    single = foundry_add_defect(
        cycle=0,
        source="prove",
        defect_type="MISSING",
        description="The report renderer omits the per-cycle minutes column.",
        spec_ref="NFR-002",
        file_path="src/report/render.py",
        defect_class="report-renderer-gap",
        tier="LATENT",
        reproduction_attempted=matched,
        project_root=project_root,
    )
    assert "error" not in single, single
    assert single["defect_id"]

    batch = foundry_sync_defects(
        cycle=0,
        findings=[_renderer_gap(control)],
        project_root=project_root,
    )
    assert "error" not in batch, batch

    persisted = _defects(fdir)
    assert len(persisted) == 2, persisted
    assert {d["reproduction_attempted"] for d in persisted} == {matched, control}
    assert _tripwire(fdir) == [], (
        "an accepted filing fired the audit tripwire, so the refusal was "
        "retired at one rung and not the other"
    )


# ---------------------------------------------------------------------------
# fallout NFR-011 (D-195's class, fallout_of D-150) — THE ROW THIS MODULE CITES
# FOR THE LATENT REPRODUCTION RUNG, DERIVED FROM THE TABLE THAT STATES IT.
#
# WHAT WENT WRONG, one message above this block, and green the whole time. The
# accept-side assertion in
# `test_a_negative_space_statement_never_decides_a_filing` attributed the
# rung's obligation to a contract id typed with no spec in front of it. Three
# specs are installed side by side and more than one of them numbers that row.
# Under the convergence spec it is the tier contract — "tier in {LIVE, LATENT};
# for LATENT a reproduction_attempted statement" — which is exactly the rung
# the test drives. Under the spec THIS run builds against, the same number is
# `Foundry-Concern`, a tool with no tiers, no filing doors and no
# reproduction, so a reader resolving the cite against the current spec landed
# on a concern ledger.
#
# (This block cannot spell that number bare either, and the pin above this one
# is why: a module held to the full convention may not carry an unqualified id
# even to talk ABOUT one. Both spellings are derived below instead, which is
# the better answer anyway — prose that names the number goes stale, and prose
# that reads it off the table cannot.)
#
# WHY NO PIN COULD SEE IT. `tests/test_spec_id_convention.py` holds this module
# to the full convention and passed, because its scan is `prose_blocks` —
# docstrings and comments. An assertion message is neither, and it is the
# sentence a failing engineer actually reads.
#
# THE EDIT WAS `convergence CT-001`, AND THE TEMPTING WRONG EDIT WAS
# `fallout CT-001`. That is the part worth pinning rather than trusting. Faced
# with a bare id in a run whose spec is `fallout`, the reflex is to qualify it
# with THIS run's name — and that spelling RESOLVES, so a check that only asks
# "does the spec the cite names number this row" waves it straight through
# while sending the reader somewhere that says nothing about tiers. Both pins
# below therefore read the number off a spec table rather than trusting a typed
# one: the `_PYTEST_DISCOVERY_PHRASE` shape applied to a citation.
#
# WHAT THIS DELIBERATELY DOES NOT ADD. `tests/test_skill_prose.py` carries a
# module-wide "every id in this module's assertion messages names its spec"
# scan, and casting 2 is landing that same scan for every module held to the
# convention. A third copy here would be the duplicate-spelling class these
# pins exist to catch. This block pins the ONE claim this module got wrong,
# and imports every scanner it needs.
# ---------------------------------------------------------------------------

#: The contract id each spec states the reproduction obligation in, DERIVED
#: from that spec's own table. `convergence` states it for LATENT — the tier
#: this module's negative-space pair drives — and `fallout` states it for
#: HARDENING. Same rung, two rows, two numbers, and typing one for the other is
#: invisible to every check that only asks whether an id is well-formed.
_LATENT_REPRODUCTION_CONTRACT = _contract_rows_naming(
    "convergence ", "reproduction_attempted"
)
_THIS_SPECS_REPRODUCTION_CONTRACT = _contract_rows_naming(
    "fallout ", "reproduction_attempted"
)


def test_the_latent_reproduction_rung_cites_the_spec_that_states_it() -> None:
    """fallout NFR-011 (D-195's class): the cite, derived rather than trusted.

    The accept-side message above names the row that imposes the
    `reproduction_attempted` obligation on a LATENT filing. That row is the
    convergence spec's tier contract, and this asserts the message says so
    with the spec named — read out of the convergence contracts table, so a
    renumbering there turns this red instead of leaving a stale number in a
    sentence nobody re-reads.
    """
    assert len(_LATENT_REPRODUCTION_CONTRACT) == 1, (
        f"the convergence contracts table states `reproduction_attempted` in "
        f"{list(_LATENT_REPRODUCTION_CONTRACT)}. This pin expects exactly one "
        f"row to attribute the LATENT rung to; with none or several, the "
        f"message above needs rewriting against the table rather than this "
        f"assertion relaxing."
    )
    expected = f"convergence {_LATENT_REPRODUCTION_CONTRACT[0]}"
    assert any(expected in text for _, text in _assertion_messages(Path(__file__))), (
        f"no assertion message in this module attributes the LATENT "
        f"reproduction rung to {expected}, the one row the convergence "
        f"contracts table states `reproduction_attempted` in. An engineer "
        f"reading the failure is sent to the wrong contract — which with a "
        f"BARE number is whichever spec they happened to open."
    )


def test_the_latent_rungs_number_never_wears_this_runs_spec_name() -> None:
    """fallout NFR-011 (D-195's class), the absence half: the tempting wrong edit.

    A bare id is fixed by putting a spec in front of it, and the reflex spec to
    reach for is the one the run is building against. Here that reflex is
    wrong and it FAILS SILENTLY: the fallout spec numbers that row too, so the
    cite resolves, reads well, and points at `Foundry-Concern` — a tool with
    no tiers and no filing doors. Only the row is wrong, which no
    well-formedness check and no resolution check can see.

    Both numbers are derived, so this pin cannot drift from the tables it
    guards; it fires only when the two specs genuinely disagree about the rung,
    which is precisely when the substitution is possible.
    """
    borrowed = sorted(
        cited
        for cited in _LATENT_REPRODUCTION_CONTRACT
        if cited not in _THIS_SPECS_REPRODUCTION_CONTRACT
        and any(
            f"fallout {cited}" in text
            for _, text in _assertion_messages(Path(__file__))
        )
    )
    assert not borrowed, (
        f"{borrowed} is the CONVERGENCE spec's contract id for the LATENT "
        f"reproduction rung, cited here as this run's. Under the fallout spec "
        f"that row is "
        f"{SPEC_ROWS['fallout '][borrowed[0]].split('|')[2].strip()!r}, which "
        f"has no tiers and no filing doors. This run's number for the same "
        f"rung is {list(_THIS_SPECS_REPRODUCTION_CONTRACT)} and states it for "
        f"HARDENING; cite the predecessor with its own name — "
        f"`convergence {borrowed[0]}` — when the predecessor is what you mean."
    )


def test_a_claim_in_the_description_is_still_refused_beside_an_innocent_statement(
    run_env,
):
    """The refuse side, held while the accept side lands (convergence AC-007 clause 1).

    D-158 is a narrowing, and the failure mode of a narrowing is taking the
    refusal with it. This is the same filing shape as the pair above with the
    claim moved into the description and the statement left innocent: still
    refused, still audited."""
    project_root, fdir = run_env

    result = foundry_add_defect(
        cycle=0,
        source="prove",
        defect_type="MISSING",
        description=_SMUGGLED_CLAIM,
        spec_ref="NFR-002",
        defect_class="report-renderer-gap",
        tier="LATENT",
        reproduction_attempted="grepped both roots for a minutes column and found 0 sites",
        project_root=project_root,
    )

    assert result["ok"] is False, result
    assert result["denylist_class"] == SECURITY_PROPERTY_CLAIM
    assert _defects(fdir) == []
    assert len(_tripwire(fdir)) == 1


def test_the_claim_scan_is_the_complement_of_one_named_field_set():
    """The mechanism this packet delivers: ONE explicit, documented partition.

    The class this defect belongs to is
    security-denylist-tripwire-is-rung-dependent — D-128, D-146/D-147, D-158 —
    and every instance was a rung reading a different set of fields than the
    rung beside it. So the field set is named once, derived from its four
    partitions rather than re-listed, and this is what fails if a member is
    added to a partition and missed in the union."""
    from foundry_mcp.tools.foundry import (
        _FILING_ESCALATION_KEYS,
        _FILING_LOCATOR_KEYS,
        _FILING_NEGATIVE_SPACE_KEYS,
        _FILING_VOCABULARY_KEYS,
        NON_CLAIM_FILING_KEYS,
        security_scan_text,
    )

    partitions = (
        _FILING_VOCABULARY_KEYS,
        _FILING_LOCATOR_KEYS,
        _FILING_ESCALATION_KEYS,
        _FILING_NEGATIVE_SPACE_KEYS,
    )

    assert NON_CLAIM_FILING_KEYS == set().union(*partitions)
    assert sum(len(p) for p in partitions) == len(NON_CLAIM_FILING_KEYS), (
        "the partitions overlap, so a member's documented reason for being "
        "excluded is no longer the reason it is excluded"
    )
    assert _FILING_NEGATIVE_SPACE_KEYS == {"reproduction_attempted"}, (
        "the negative-space partition is what D-158 added and what its own "
        "block documents; a member joining or leaving it is a contract change"
    )

    scanned = security_scan_text(
        _renderer_gap("grepped both roots for a security section and found 0 sites")
    )
    assert "0 sites" not in scanned, (
        "the negative-space statement is in the text the predicate judges"
    )
    assert "per-cycle minutes column" in scanned, (
        "the description dropped out of the claim scan, which is D-147"
    )


def test_the_pre_dispatch_rung_and_the_handler_build_one_mapping():
    """`server.py`'s pre-dispatch rung must judge the filing the handler would.

    D-146 put a rung ahead of dispatch so a schema-invalid argument set cannot
    switch off the tripwire. That rung re-spelled `foundry_add_defect`'s eight
    argument names as `args.get("...")` literals, so a field added to one
    spelling and missed in the other is two rungs reading different filings —
    the class, exactly. `filing_finding_mapping` is the one spelling both use;
    this pin is what fails if a claim-bearing parameter is added to the door
    and not to it."""
    import inspect

    from foundry_mcp.tools.foundry import (
        _FILING_ARGUMENT_NAMES,
        filing_finding_mapping,
    )

    door_params = set(inspect.signature(foundry_add_defect).parameters)
    assert set(_FILING_ARGUMENT_NAMES) <= door_params, (
        f"{sorted(set(_FILING_ARGUMENT_NAMES) - door_params)} is not a "
        f"parameter of the filing door, so the pre-dispatch rung reads an "
        f"argument the handler never receives"
    )
    unread = door_params - set(_FILING_ARGUMENT_NAMES) - {
        "cycle",  # the server's counter is the authority (process-fixes ST-001)
        "source",  # a closed vocabulary, and passed separately by the rung
        "defect_type",  # a closed vocabulary
        "project_root",  # not part of the filing
        # fallout CT-019 — LOCATORS, not claim prose: each names another record
        # by id and asserts nothing about the code, so a rung blind to them is
        # blind to nothing a security predicate could ever match. They are in
        # `_FILING_LOCATOR_KEYS` for the same reason, which is what keeps the
        # LIVE prose floor from reading a bare `D-NNN` as a statement.
        "fallout_of",
        "supersedes",
    }
    assert unread == set(), (
        f"{sorted(unread)} reached the filing door without reaching "
        f"`filing_finding_mapping`. Either it carries claim prose — in which "
        f"case the pre-dispatch rung is blind to it — or it is a vocabulary "
        f"or plumbing argument and belongs in the exclusion above, named."
    )

    built = filing_finding_mapping(
        {
            "description": "the handler is registered but never called",
            "spec_ref": "US-002",
            "target_kind": None,
            "symbol": "handle",
            "file_path": "src/api/handler.py",
            "tier": "LIVE",
            "defect_class": "UNWIRED_HANDLER",
        }
    )
    assert built == _finding(
        description="the handler is registered but never called",
        spec_ref="US-002",
        symbol="handle",
    )
    assert built["target_kind"] == "" and built["reproduction_attempted"] == "", (
        "an absent argument arrived as something other than the empty string, "
        "so a filing off the wire is judged on a value no filer wrote"
    )


def test_every_documented_latent_example_is_accepted_by_both_real_doors(run_env):
    """The protocol's own examples, driven end to end (D-158's structural pin).

    `test_every_documented_latent_example_passes_the_filing_door` above drives
    this corpus through the shared validator, which is where D-101's rung
    lived. D-158's rung lived there too — but the refusal it produced only
    BECAME a refused filing at the doors, and the tripwire it wrote only
    appeared there. So the same corpus is driven through both real doors here:
    a change to the claim scan that refuses a shape `agents/assayer.md`,
    `agents/tracer.md`, `agents/coverage-diff.md`, `agents/flow-tracer.md`,
    `agents/research-auditor.md` or `skills/sight/SKILL.md` publishes fails
    with that surface named, and so does one that fires an audit record for a
    filing the doors accepted."""
    from foundry_mcp.tools.orchestration.fix_gate import foundry_sync_defects

    project_root, fdir = run_env

    examples = _documented_latent_examples()
    assert len(examples) >= 5, examples

    refused: list[tuple[str, str, object]] = []
    for name, example in examples:
        finding = dict(example)
        finding.setdefault("source", "prove")
        # The `type` is SUPPLIED rather than carried through. The corpus is not
        # uniform on this key — `agents/assayer.md`'s example declares no `type`
        # at all, and the rest publish DEFECT_TYPES members (`MISSING`,
        # `UNWIRED`, `RESEARCH_DEVIATION`) whose own pins live in
        # `tests/test_vocab.py` and `tests/test_protocol_prose.py`. Holding one
        # member constant across every example is what keeps this sweep a pin
        # on the CLAIM decision: a refusal reported below is one the claim scan
        # produced, not one the type vocabulary did, and not one an example
        # that never named a type would have drawn at either door.
        finding["type"] = "MISSING"

        batch = foundry_sync_defects(
            cycle=0, findings=[finding], project_root=project_root
        )
        if "error" in batch:
            refused.append((name, "Foundry-Sync", batch["error"]))

        single = foundry_add_defect(
            cycle=0,
            source="prove",
            defect_type="MISSING",
            description=str(example.get("description") or ""),
            spec_ref=str(example.get("spec_ref") or ""),
            symbol=str(example.get("symbol") or ""),
            file_path=str(example.get("file") or ""),
            target_kind=str(example.get("target_kind") or ""),
            defect_class=str(example.get("class") or ""),
            tier="LATENT",
            reproduction_attempted=str(example.get("reproduction_attempted") or ""),
            project_root=project_root,
        )
        if "error" in single:
            refused.append((name, "Foundry-Defect", single["error"]))

    assert refused == [], (
        f"a real filing door refuses {len(refused)} of the LATENT shapes the "
        f"streams' own instructions publish: {refused}. A stream that copies "
        f"its own example meets a refusal naming a field those instructions "
        f"never taught it. Fix the door or fix the surface — never this "
        f"assertion."
    )
    assert _tripwire(fdir) == [], (
        "a documented LATENT example fired the security audit tripwire. The "
        "filing was accepted, so the record names an attempt nobody made and "
        "an auditor reading the ledger by class finds a false positive."
    )


# ---------------------------------------------------------------------------
# fallout US-005 — the HARDENING tier, at the one shared validator.
# ---------------------------------------------------------------------------


def _hardening_finding(**overrides) -> dict:
    """The batch door's finding shape for a well-formed HARDENING filing."""
    finding = {
        "source": "prove",
        "type": "PARTIAL",
        "description": "drove the retry arm twice and the counter reported 3 for 2 jobs",
        "spec_ref": "",
        "symbol": "",
        "file": "src/queue/retry.py",
        "class": "OFF_SPEC_RETRY_DOUBLE_COUNT",
        "tier": "HARDENING",
        "reproduction_attempted": (
            "drove POST /jobs/retry twice against one queued job; the counter "
            "reported 3"
        ),
    }
    finding.update(overrides)
    return finding


def _hardening_arguments(project_root: str, **overrides) -> dict:
    """The single door's argument set for the same filing."""
    args = {
        "cycle": 1,
        "source": "prove",
        "defect_type": "PARTIAL",
        "description": "drove the retry arm twice and the counter reported 3 for 2 jobs",
        "file_path": "src/queue/retry.py",
        "defect_class": "OFF_SPEC_RETRY_DOUBLE_COUNT",
        "tier": "HARDENING",
        "reproduction_attempted": (
            "drove POST /jobs/retry twice against one queued job; the counter "
            "reported 3"
        ),
        "project_root": project_root,
    }
    args.update(overrides)
    return args


def test_a_hardening_filing_with_a_reproduction_is_accepted_at_both_doors(run_env):
    """fallout AC-023 / CT-012 / OT-018: 'A HARDENING defect is accepted with a
    reproduction and does not block assay, temper, nyquist or done.'

    The acceptance half, at BOTH doors, because fallout CT-012's surface
    column names 'Foundry-Defect / Foundry-Sync' and a tier one door takes and
    the other refuses is the D-119 class with a third field. The gate half is
    the test below.
    """
    from foundry_mcp.tools.orchestration.fix_gate import foundry_sync_defects

    project_root, fdir = run_env

    single = foundry_add_defect(**_hardening_arguments(project_root))
    assert single.get("defect_id"), single

    batch = foundry_sync_defects(
        cycle=1, findings=[_hardening_finding()], project_root=project_root
    )
    assert batch.get("added") == 1, batch

    stored = _defects(fdir)
    assert [d["tier"] for d in stored] == ["HARDENING", "HARDENING"], stored
    assert all(d["status"] == "open" for d in stored), stored

    # The evidence the record RESTS on, persisted rather than dropped. Both this
    # door's literal and the batch door's `new_defect_record` wrote
    # `reproduction_attempted if tier == "LATENT" else None`, which refuses a
    # HARDENING filing for omitting the field and then throws away the field it
    # supplied. Fixed here; the batch door's copy of the expression lives in
    # `orchestration/fix_gate.py#new_defect_record`, which is casting 2's file
    # and is named in this run's concerns.md — so this asserts the door it can
    # reach and does NOT pin the other door's current answer as correct.
    single_record = next(d for d in stored if d["id"] == single["defect_id"])
    assert single_record["reproduction_attempted"], single_record


def test_a_hardening_filing_without_a_reproduction_is_refused_naming_the_field(run_env):
    """fallout FR-045: HARDENING carries 'a reproduction, not a worry' — the
    same evidence standard as LIVE, stated in the field LATENT states its
    negative result in.

    A worry nobody drove is a TEMPER_CANDIDATE observation, and the hint says
    so, because the wrong repair here is to invent a reproduction.
    """
    project_root, _ = run_env

    refusal = foundry_add_defect(
        **_hardening_arguments(project_root, reproduction_attempted="")
    )

    assert refusal.get("field") == "reproduction_attempted", refusal
    assert "TEMPER_CANDIDATE" in refusal["hint"], refusal["hint"]


@pytest.mark.parametrize(
    "spec_ref",
    ["FR-025", "the retry section", "0", " NFR-002 "],
    ids=["well-formed-id", "prose", "zero-string", "padded"],
)
def test_a_hardening_filing_carrying_any_spec_ref_is_refused_at_both_doors(
    run_env, spec_ref
):
    """fallout AC-055 / FR-057 / GI-028 / OT-019 verbatim: 'Refuse HARDENING
    whenever spec_ref is set' — 'both filing doors refuse a HARDENING record
    that carries any spec_ref, naming the tier rule'.

    ANY value, not merely a well-formed requirement id: fallout GI-028's
    violation column is 'a HARDENING record carrying a spec_ref for context',
    and a reference offered as context is exactly the one a well-formedness
    check would wave through.

    Driven at both doors and asserted to name the SAME field, which is the
    property the locked check order exists for — a filing refused on `spec_ref`
    at one door and on something else at the other sends two streams to repair
    two different things about one filing.
    """
    from foundry_mcp.tools.orchestration.fix_gate import foundry_sync_defects
    from foundry_mcp.tools.foundry import TIER_NOT_ALLOWED

    project_root, fdir = run_env

    single = foundry_add_defect(**_hardening_arguments(project_root, spec_ref=spec_ref))
    assert single.get("field") == "spec_ref", single
    assert TIER_NOT_ALLOWED in single["error"], single["error"]

    batch = foundry_sync_defects(
        cycle=1,
        findings=[_hardening_finding(spec_ref=spec_ref)],
        project_root=project_root,
    )
    refused = batch.get("refusals") or []
    assert refused and batch.get("error"), batch
    assert [r["field"] for r in refused] == ["spec_ref"], batch
    assert TIER_NOT_ALLOWED in refused[0]["reason"], batch

    assert _defects(fdir) == [], "a refused filing may not reach the ledger"


def test_a_blank_spec_ref_is_not_a_spec_ref(run_env):
    """The other side of the rung, and the one an over-eager reading breaks.

    `_finding_mapping` writes `spec_ref: ""` for every filing that did not give
    one, and `filing_finding_mapping` coerces an absent argument to `""` off
    the wire — so a rung that treated "the key is present" as "a reference was
    made" would refuse EVERY HARDENING filing, which is the tier being
    unreachable rather than guarded.
    """
    project_root, fdir = run_env

    assert foundry_add_defect(
        **_hardening_arguments(project_root, spec_ref="")
    ).get("defect_id"), "a blank spec_ref is the absence of one"
    assert validate_defect_filing(
        _finding(
            tier="HARDENING",
            spec_ref="   ",
            reproduction_attempted="drove the arm; saw 3 for 2",
        )
    ) is None
    assert len(_defects(fdir)) == 1


def test_a_hardening_security_claim_is_refused_and_the_tripwire_records_it(run_env):
    """fallout GI-004 / AC-023 / NFR-004: 'a never-demote class match is refused
    and the tripwire recorded'.

    vocab's DEFECT_TIERS block states the rule this drives: 'a security-property
    claim filed as HARDENING is refused and the audit tripwire fires, exactly as
    it is when filed as LATENT. A tier is never a route around the never-weaken
    guarantee.' The rung is the FIRST one, so the audit record is written for a
    filing that is also wrong in some other way — which is the shape a stream
    actually files, and D-061 is the record of what a rung-dependent audit
    control costs.
    """
    project_root, fdir = run_env

    refusal = foundry_add_defect(
        **_hardening_arguments(
            project_root,
            description=(
                "the login endpoint does not verify the authentication token "
                "signature"
            ),
            # Also wrong on a later rung, so this proves the ordering and not
            # merely the predicate.
            reproduction_attempted="",
        )
    )

    assert refusal.get("denylist_class") == SECURITY_PROPERTY_CLAIM, refusal
    fired = _tripwire(fdir)
    assert len(fired) == 1, fired
    assert fired[0]["denylist_class"] == SECURITY_PROPERTY_CLAIM, fired
    assert _defects(fdir) == [], "the claim may not land in the backlog tier"


@pytest.mark.parametrize("gate_token", ["assay", "temper", "nyquist", "done"])
def test_the_terminal_gates_do_not_refuse_on_an_open_hardening_defect(
    run_env, gate_token
):
    """fallout OT-018 / AC-022: '... does not block assay, temper, nyquist or
    done.'

    THE REAL GATE IS DRIVEN, not `BLOCKING_TIERS` membership. The constant lives
    in `orchestration/gates.py` and this casting does not touch it — the whole
    of the tier's gate semantics is that it is ABSENT from it, and the thing
    worth pinning is that the ladder every door actually walks agrees.

    Asserted as the absence of the DEFECT rung from the failing checks rather
    than as `passed is True`: a synthetic run is not gate-ready for other
    reasons (no recorded INSPECT width, no generated report), and a test
    demanding a green gate would be pinning those instead. The control below
    shows the rung is reachable and does fire.
    """
    from foundry_mcp.tools.orchestration.gates import (
        NEXT_ACTION_CALLED_MARKER,
        foundry_gate,
    )

    project_root, fdir = run_env
    filed = foundry_add_defect(**_hardening_arguments(project_root))
    (fdir / NEXT_ACTION_CALLED_MARKER).touch()

    gate = foundry_gate(gate_token, project_root)
    failing = [c["check"] for c in gate.get("checklist", []) if not c.get("ok")]

    assert not any(c.startswith("zero_blocking_defects") for c in failing), gate
    assert filed["defect_id"] not in str(gate.get("reason", "")), gate


def test_the_blocking_rung_the_gates_walk_does_fire_on_a_live_defect(run_env):
    """The control for the four above: the rung they are asserted not to trip
    is reachable, and an open LIVE defect trips it by name.

    Without this, the four tests above pass just as well against a gate that
    stopped reading the ledger at all — which is the failure mode a
    non-blocking tier makes easiest to ship.
    """
    from foundry_mcp.tools.orchestration.gates import (
        NEXT_ACTION_CALLED_MARKER,
        foundry_gate,
    )

    project_root, fdir = run_env
    foundry_add_defect(
        **_hardening_arguments(
            project_root, tier="LIVE", reproduction_attempted=""
        )
    )
    (fdir / NEXT_ACTION_CALLED_MARKER).touch()

    gate = foundry_gate("done", project_root)
    failing = [c["check"] for c in gate.get("checklist", []) if not c.get("ok")]

    assert any(c.startswith("zero_blocking_defects") for c in failing), gate
    assert "D-001" in str(gate.get("reason", "")), gate


# ---------------------------------------------------------------------------
# fallout FR-025 / CT-019 / ST-006 — the two provenance fields.
# ---------------------------------------------------------------------------


def _live_arguments(project_root: str, **overrides) -> dict:
    args = {
        "cycle": 1,
        "source": "prove",
        "defect_type": "MISSING",
        "description": "drove POST /login with a valid body and got 500",
        "file_path": "src/api/login.py",
        "defect_class": "UNHANDLED_500",
        "tier": "LIVE",
        "project_root": project_root,
    }
    args.update(overrides)
    return args


def test_fallout_of_naming_a_known_defect_is_accepted_and_persisted(run_env):
    """fallout AC-045 / CT-019 / OT-039 verbatim: 'Optional fallout_of: D-NNN on
    the defect record, set by the filing stream; measure-run counts it per
    cycle.'

    The field is persisted under exactly this name because casting 3's
    `measure-run.py` and casting 10's `foundry_state.fallout_rows` read it by
    name — the shape is a cross-module contract, not this door's private
    spelling.
    """
    project_root, fdir = run_env

    parent = foundry_add_defect(**_live_arguments(project_root))
    child = foundry_add_defect(
        **_live_arguments(
            project_root,
            description="the retry after that 500 double-books the session",
            defect_class="RETRY_DOUBLE_BOOK",
            symbol="retry_login",
            fallout_of=parent["defect_id"],
        )
    )

    assert child.get("defect_id"), child
    stored = {d["id"]: d for d in _defects(fdir)}
    assert stored[child["defect_id"]]["fallout_of"] == parent["defect_id"], stored
    assert stored[parent["defect_id"]]["fallout_of"] is None, stored


def test_fallout_of_naming_an_unknown_id_is_refused_naming_the_id(run_env):
    """fallout CT-019 / OT-039: 'fallout_of naming an unknown id' is the ONE
    error this contract admits, and the refusal names the id so the filer can
    see which citation is wrong rather than which field.

    Refused with NOTHING written: the check runs inside the ledger transaction
    ahead of every mutation, so a bad citation costs the run no record and no
    id.
    """
    project_root, fdir = run_env

    refusal = foundry_add_defect(
        **_live_arguments(project_root, fallout_of="D-404")
    )

    assert refusal.get("field") == "fallout_of", refusal
    assert "D-404" in refusal["error"], refusal
    assert _defects(fdir) == [], "a refused filing may not reach the ledger"


def test_every_record_carries_the_provenance_keys_even_when_nothing_set_them(run_env):
    """fallout FR-025 — an ABSENT key is not a measured zero, so the door writes
    both keys on every record.

    `foundry_state.fallout_rows` (casting 10) reads the KEY's presence as 'this
    record was measured' and its absence as 'this record predates the field',
    and a cycle holding one unmeasured record cannot contribute to fallout
    FR-025's acceptance figure. A door that wrote `fallout_of` only when a
    filer set it would make every cycle of every post-change run read as
    not_measurable forever — the reader certifying nothing while looking like
    it certified something. Driven through the reader itself, not asserted on
    the literal.
    """
    from foundry_mcp.tools.foundry_state import fallout_rows

    project_root, fdir = run_env
    foundry_add_defect(**_live_arguments(project_root))

    record = _defects(fdir)[0]
    assert record["fallout_of"] is None and record["supersedes"] is None, record

    rows = fallout_rows(fdir)
    assert rows["measured_records"] == 1, rows
    assert rows["unmeasured_records"] == 0, rows


def test_supersedes_closes_the_cited_hardening_record(run_env):
    """fallout ST-006 / AC-023 / OT-020 verbatim: 'a later filing carrying
    supersedes closes the HARDENING record as superseded and no door re-tiers a
    record in place'.

    The tier, the description and the reproduction of the earlier record are
    asserted UNCHANGED, because that is the whole of fallout GI-022:
    promotion is a new filing that cites the old one, and re-tiering in place
    would rewrite what a stream said it saw. `status` moves to a third value
    beside open and fixed —
    the closure stops it blocking without claiming anybody repaired it.
    """
    project_root, fdir = run_env

    hardening = foundry_add_defect(**_hardening_arguments(project_root))
    before = next(d for d in _defects(fdir) if d["id"] == hardening["defect_id"])

    promotion = foundry_add_defect(
        **_live_arguments(
            project_root,
            description=(
                "the same retry arm loses a job when the queue is drained "
                "concurrently, which FR-025 requires it not to"
            ),
            spec_ref="FR-025",
            symbol="retry_arm",
            supersedes=hardening["defect_id"],
        )
    )

    assert promotion["superseded"] == hardening["defect_id"], promotion

    stored = {d["id"]: d for d in _defects(fdir)}
    closed = stored[hardening["defect_id"]]
    assert closed["status"] == "superseded", closed
    assert closed["superseded_by"] == promotion["defect_id"], closed
    # NOT RE-TIERED. Every field the stream filed is exactly where it left it.
    assert closed["tier"] == before["tier"] == "HARDENING", closed
    assert closed["description"] == before["description"], closed
    assert closed["reproduction_attempted"] == before["reproduction_attempted"], closed
    # The promotion carries its OWN tier and its own evidence.
    assert stored[promotion["defect_id"]]["tier"] == "LIVE", stored


def test_supersedes_citing_something_that_is_not_an_open_hardening_record(run_env):
    """The other half of fallout ST-006, and the reason it is not a refusal.

    fallout CT-019's errors column admits exactly one error — an unknown
    `fallout_of` — and D-101 is this package's record of what inventing a rung
    a contract does not admit costs: the door refuses its own documented
    example and the stream's next move is to fabricate the field. So a
    `supersedes` naming an unknown id,
    or naming a record that is not an open HARDENING one, closes NOTHING and
    refuses nothing; the result's `superseded` key is null and that is how the
    filer learns the promotion did not land.
    """
    project_root, fdir = run_env

    live = foundry_add_defect(**_live_arguments(project_root))

    unknown = foundry_add_defect(
        **_live_arguments(
            project_root, symbol="a", defect_class="B", supersedes="D-404"
        )
    )
    assert unknown.get("defect_id"), unknown
    assert unknown["superseded"] is None, unknown

    not_hardening = foundry_add_defect(
        **_live_arguments(
            project_root, symbol="c", defect_class="D", supersedes=live["defect_id"]
        )
    )
    assert not_hardening["superseded"] is None, not_hardening
    stored = {d["id"]: d for d in _defects(fdir)}
    assert stored[live["defect_id"]]["status"] == "open", stored


def test_no_door_offers_a_tier_rewrite_of_a_record_already_on_disk(run_env):
    """fallout GI-022 / OT-020: 'no door re-tiers a record in place'.

    The structural half. Two functions in this module mutate a record that is
    already on disk, and neither can move a tier a stream declared:
    `retier_matching_untiered` writes one only into a record that has NONE
    (`defect_tier` reads TIER_UNKNOWN), and `close_superseded_record` writes
    `status` and nothing else. Driven rather than read: an identical re-filing
    of a HARDENING record appends a second record beside it and leaves the
    first one's tier alone.
    """
    project_root, fdir = run_env

    first = foundry_add_defect(**_hardening_arguments(project_root))
    again = foundry_add_defect(
        **_hardening_arguments(project_root, tier="LIVE", reproduction_attempted="")
    )

    assert again["defect_id"] != first["defect_id"], (first, again)
    assert again["retiered"] == 0, again
    stored = {d["id"]: d for d in _defects(fdir)}
    assert stored[first["defect_id"]]["tier"] == "HARDENING", stored


def test_a_filing_cannot_supersede_the_record_it_is_itself_creating(run_env):
    """fallout ST-006 / GI-022, the D-053 half: the promotion is stated over
    TWO records — 'a new filing on the same path that CITES the HARDENING id',
    'promotion only by a NEW filing that cites it' — so a filing citing the id
    it is about to be GIVEN promotes nothing.

    NOT AN EXOTIC INPUT. Both doors close the supersession AFTER the append, so
    the scan sees the record being filed; and on an EMPTY ledger the mint is
    deterministic, so ``supersedes="D-001"`` on a run's first filing names that
    filing. Driven at exactly that point before the fix, the record was BORN
    ``status: "superseded"`` with ``superseded_by`` naming itself — it never
    counted as open, never reached the F6 HARDENING backlog fallout CT-012
    promises it, and the door reported the self-promotion as a success.

    A NO-OP, NOT A REFUSAL, exactly like the unknown / already-closed /
    non-HARDENING citations the test above covers. fallout CT-019's errors
    column admits one error and this is not it (D-101), so the record lands
    OPEN and ``superseded`` is null, which is how the filer learns the
    promotion did not land.
    """
    project_root, fdir = run_env

    assert _defects(fdir) == [], "an empty ledger is what makes the mint known"

    filed = foundry_add_defect(**_hardening_arguments(project_root, supersedes="D-001"))

    assert filed["defect_id"] == "D-001", filed
    assert filed["superseded"] is None, filed
    assert filed["open_defects"] == 1, filed

    stored = _defects(fdir)
    assert len(stored) == 1, stored
    record = stored[0]
    assert record["status"] == "open", record
    assert "superseded_by" not in record, record
    # The CITATION is kept exactly as the filer made it. The record is not
    # rewritten to hide a promotion that did not happen; `superseded` in the
    # result is the only place the outcome is reported.
    assert record["supersedes"] == "D-001", record


def test_the_batch_door_cannot_supersede_the_record_it_is_itself_creating(run_env):
    """THE ADJACENT PATH: ``foundry_sync_defects`` is the OTHER caller of
    ``close_superseded_record``, and it reaches the same shared function from
    ``orchestration/fix_gate.py`` with its OWN mint (``_mint_defect_id``) and
    its own transaction.

    Driven separately rather than assumed from the single door, because that is
    the whole reason the rung lives in the shared function: a door the fix
    reached and a door it did not is the D-119 class, and the batch door is the
    one a whole INSPECT stream files through, so a gap there is the common path
    rather than the rare one. Before the fix this door returned
    ``{added: 1, superseded_ids: ["D-001"], total_open: 0}`` over an empty
    ledger holding one self-superseded record.
    """
    from foundry_mcp.tools.orchestration.fix_gate import foundry_sync_defects

    project_root, fdir = run_env

    assert _defects(fdir) == [], "an empty ledger is what makes the mint known"

    result = foundry_sync_defects(
        cycle=1,
        findings=[_hardening_finding(supersedes="D-001")],
        project_root=project_root,
    )

    assert result.get("added") == 1, result
    assert result.get("superseded_ids") == [], result
    assert result.get("total_open") == 1, result

    stored = _defects(fdir)
    assert len(stored) == 1 and stored[0]["id"] == "D-001", stored
    assert stored[0]["status"] == "open", stored[0]
    assert "superseded_by" not in stored[0], stored[0]


def test_a_real_two_record_promotion_still_closes_after_the_self_citation_rung(run_env):
    """The positive control for the rung above, and it is not optional: a guard
    that refused every citation would pass both tests above and silently delete
    fallout ST-006.

    Two DISTINCT records, which is the shape that transition is stated over — so the
    earlier one closes, the later one stays open, and the tier of neither moves.
    """
    project_root, fdir = run_env

    hardening = foundry_add_defect(**_hardening_arguments(project_root))
    promotion = foundry_add_defect(
        **_hardening_arguments(
            project_root,
            symbol="retry_arm",
            defect_class="OFF_SPEC_RETRY_TRIPLE_COUNT",
            supersedes=hardening["defect_id"],
        )
    )

    assert promotion["defect_id"] != hardening["defect_id"], (hardening, promotion)
    assert promotion["superseded"] == hardening["defect_id"], promotion

    stored = {d["id"]: d for d in _defects(fdir)}
    assert stored[hardening["defect_id"]]["status"] == "superseded", stored
    assert stored[hardening["defect_id"]]["tier"] == "HARDENING", stored
    assert stored[promotion["defect_id"]]["status"] == "open", stored


# --- fallout AC-023 / GI-004 / OT-019 (D-078): the OTHER denylist entries ----
#: The filing D-078 drove at both doors: no spec_ref at all, and prose that
#: asserts a STATED requirement's behaviour is missing. Spelled once because
#: four tests below ask different questions of the same sentence.
_SPEC_CLAIM_PROSE = (
    "AC-022 requires Foundry-Gate('done') to pass with open HARDENING "
    "defects and the gate refuses; the required behaviour is absent"
)


def test_a_hardening_filing_claiming_spec_required_behaviour_is_refused(run_env):
    """fallout AC-023 / GI-004 / OT-019 (D-078): 'a never-demote class match is
    refused and the tripwire recorded', and fallout GI-004's violation
    column is 'filing a security claim OR A SPEC-REQUIRED BEHAVIOUR FAILURE as HARDENING
    or LATENT'.

    The denylist rung consulted `is_security_property_text` alone, so of the
    four never-demote entries only SECURITY_PROPERTY_CLAIM was enforced at
    either filing door: driven with this exact finding, `never_demote_class`
    answered SPEC_REQUIRED_BEHAVIOUR_CLAIM while `foundry_add_defect` returned
    a defect id, `foundry_sync_defects` returned `{'ok': True, 'added': 1}`,
    and the tripwire stayed empty. The tier the release added for OFF-spec
    findings was accepting on-spec ones.

    Driven at BOTH doors, because a rung one door remembers and the other does
    not is the D-119 class and the batch door is the one a whole INSPECT stream
    files through.
    """
    from foundry_mcp.tools.orchestration.fix_gate import foundry_sync_defects

    project_root, fdir = run_env

    single = foundry_add_defect(
        **_hardening_arguments(project_root, description=_SPEC_CLAIM_PROSE)
    )
    assert single.get("denylist_class") == SPEC_REQUIRED_BEHAVIOUR_CLAIM, single
    assert single.get("field") == "description", single

    batch = foundry_sync_defects(
        cycle=1,
        findings=[_hardening_finding(description=_SPEC_CLAIM_PROSE)],
        project_root=project_root,
    )
    refused = batch.get("refusals") or []
    assert refused and batch.get("error"), batch
    assert refused[0]["denylist_class"] == SPEC_REQUIRED_BEHAVIOUR_CLAIM, batch
    assert refused[0]["field"] == "description", batch

    # The audit record, under the class the refusal named (D-083), once per
    # door — a refusal that leaves no trace is what D-061 was filed about.
    fired = _tripwire(fdir)
    assert [f["denylist_class"] for f in fired] == [
        SPEC_REQUIRED_BEHAVIOUR_CLAIM,
        SPEC_REQUIRED_BEHAVIOUR_CLAIM,
    ], fired
    assert _defects(fdir) == [], "an on-spec claim may not land in the backlog tier"


def test_the_spec_claim_rung_is_reached_before_the_class_rung(run_env):
    """The ORDERING half of D-078, which is why the rung is placed beside the
    security one rather than after the `spec_ref` refusal.

    D-061's ruling on the security entry applies unchanged here: 'An audit
    control a filer can switch off by ALSO omitting a field is not a control.'
    This filing is wrong twice over — the denylist claim AND a missing `class`
    — and the shape a stream actually files is the malformed one.
    """
    project_root, fdir = run_env

    refusal = foundry_add_defect(
        **_hardening_arguments(
            project_root, description=_SPEC_CLAIM_PROSE, defect_class=""
        )
    )

    assert refusal.get("denylist_class") == SPEC_REQUIRED_BEHAVIOUR_CLAIM, refusal
    assert len(_tripwire(fdir)) == 1, "the audit may not depend on the other rungs"


def test_a_hardening_filing_whose_only_spec_signal_is_the_locator_names_the_tier_rule(
    run_env,
):
    """The narrowing that keeps fallout AC-055 reachable: the rung reads the
    CLAIM, never the citation.

    `vocab.is_spec_required_behaviour_claim` answers True for ANY non-empty
    `spec_ref`, so a rung asking the dispatcher the raw finding would refuse
    every spec_ref-carrying HARDENING filing HERE and fallout AC-055's own
    refusal ('naming the tier rule') would become unreachable. `spec_ref` is
    neutralised at this rung for exactly that reason, and this is the test that
    fails if it stops being.
    """
    from foundry_mcp.tools.foundry import TIER_NOT_ALLOWED

    project_root, _ = run_env

    refusal = foundry_add_defect(**_hardening_arguments(project_root, spec_ref="FR-025"))

    assert refusal.get("field") == "spec_ref", refusal
    assert TIER_NOT_ALLOWED in refusal["error"], refusal["error"]
    assert "denylist_class" not in refusal, refusal


def test_a_hardening_filing_about_code_is_not_refused_for_its_subject(run_env):
    """The other narrowing: NON_COMMENT reads the finding's SUBJECT, not its
    claim, and is excluded from this rung.

    `foundry_add_defect`'s own contract for `target_kind` is that 'any other
    value, or none, means the finding is not demotable and is filed as a
    defect', so `code` is the DEFAULT shape of every production-code filing. A
    rung that refused it would make fallout OT-018 ('A HARDENING defect is
    accepted with a reproduction') unsatisfiable for the ordinary case, which
    is a stricter denylist buying an unfileable tier.
    """
    project_root, fdir = run_env

    filed = foundry_add_defect(
        **_hardening_arguments(project_root, target_kind="code")
    )

    assert filed.get("defect_id"), filed
    assert _tripwire(fdir) == [], filed
    assert [d["tier"] for d in _defects(fdir)] == ["HARDENING"]


def test_a_latent_filing_citing_a_requirement_in_its_prose_is_still_accepted(run_env):
    """THE ADJACENT PATH: the LATENT transition through the same validator,
    which this rung must not have touched.

    convergence CT-003 / OT-005 — 'a spec_ref alone never refuses a LATENT
    filing', and `validate_defect_filing`'s docstring is explicit that routing
    the LATENT gate through `never_demote_class` 'would refuse every LATENT
    filing that cites a requirement — which is the majority of them'. The rung
    added for D-078 is HARDENING-scoped precisely so that stays true, and the
    filing below is the one that proves it: the SAME prose that is refused one
    tier over, plus the spec_ref, accepted here with its negative result.
    """
    project_root, fdir = run_env

    filed = foundry_add_defect(
        **_hardening_arguments(
            project_root,
            tier="LATENT",
            spec_ref="AC-022",
            description=_SPEC_CLAIM_PROSE,
            reproduction_attempted=(
                "drove Foundry-Gate('done') against a seeded HARDENING record; "
                "the blocking rung did not name it"
            ),
        )
    )

    assert filed.get("defect_id"), filed
    assert _tripwire(fdir) == [], "the LATENT gate is the security predicate only"
    assert [d["tier"] for d in _defects(fdir)] == ["LATENT"]


# --- fallout GI-006 (D-079): the mirror carries what the door demanded ------
def _forge_log(fdir: Path) -> str:
    return (fdir / "forge-log.md").read_text(encoding="utf-8")


def test_the_hardening_mirror_carries_the_reproduction_it_demanded(run_env):
    """fallout GI-006 (D-079): 'Run artefacts stay complete.'

    `mirror_rows` keyed the reproduction row on `tier == "LATENT"` while the
    record literal thirty lines above wrote the field on `tier != "LIVE"` — and
    that literal's own comment forbids exactly this, warning that keying on
    LATENT 'would refuse a HARDENING filing for omitting evidence and then drop
    the evidence it supplied'. The mirror did what the comment forbade: driven,
    forge-log.md carried the row for a LATENT filing and no such row for a
    HARDENING filing whose reproduction the door had just demanded as a
    condition of acceptance.
    """
    project_root, fdir = run_env

    filed = foundry_add_defect(**_hardening_arguments(project_root))

    assert filed.get("defect_id"), filed
    log = _forge_log(fdir)
    assert "- **Tier:** HARDENING" in log, log
    assert (
        "- **Reproduction attempted:** drove POST /jobs/retry twice" in log
    ), log


def test_the_other_two_tiers_mirror_exactly_as_they_did(run_env):
    """THE ADJACENT PATH: the LATENT and LIVE transitions through the same
    `mirror_rows` list, neither of which the D-079 filing walked.

    The row is now keyed the way the record literal is keyed, so LATENT must
    still print its negative result and LIVE must still print no row at all —
    a LIVE record's reproduction lives in its description, and `_ledger_mirror`
    prints a row only for a truthy value.
    """
    project_root, fdir = run_env

    foundry_add_defect(
        **_hardening_arguments(
            project_root,
            tier="LATENT",
            reproduction_attempted="AST sweep of both roots finds 0 sites",
        )
    )
    foundry_add_defect(
        **_hardening_arguments(
            project_root,
            tier="LIVE",
            symbol="retry_arm",
            reproduction_attempted="",
        )
    )

    log = _forge_log(fdir)
    assert "- **Reproduction attempted:** AST sweep of both roots" in log, log
    live = log.split("- **Tier:** LIVE", 1)[1]
    assert "Reproduction attempted" not in live, live


# --- fallout CT-012 (D-093 alignment): the door's own advertised prose -------
def test_the_single_doors_advertised_tier_prose_names_every_member() -> None:
    """fallout CT-012's input column: 'tier HARDENING, reproduction_attempted,
    no spec_ref'.

    D-093 is filed on `server.py#list_tools`, whose wire strings a client
    reads: the enum accepted three tiers while every description beside it
    enumerated two and scoped `reproduction_attempted` to LATENT, so a stream
    reading the published contract learned neither what HARDENING means nor
    that it owes a reproduction, and was then refused for a field the schema
    had told it was LATENT-only. That fix is casting 2's. THIS is the same
    sentence on the surface this casting owns — `foundry_add_defect`'s own Args
    block, which every in-process caller and every reader of the module meets
    instead of the wire schema — and it had drifted identically.

    Derived from `DEFECT_TIERS` rather than asserting three names, so a fourth
    member fails this pin instead of being quietly left out of the prose.
    """
    doc = inspect.getdoc(foundry_add_defect) or ""
    tier_at = doc.index("\n    tier: ")
    repro_at = doc.index("\n    reproduction_attempted:")
    fallout_at = doc.index("\n    fallout_of:")

    tier_prose = doc[tier_at:repro_at]
    missing = sorted(m for m in DEFECT_TIERS if m not in tier_prose)
    assert not missing, (
        f"the tier parameter's prose names no {missing} while the vocabulary "
        f"accepts it:\n{tier_prose}"
    )

    repro_prose = doc[repro_at:fallout_at]
    assert "HARDENING" in repro_prose, (
        "the reproduction is owed by BOTH non-blocking tiers — a paragraph "
        f"that scopes it to LATENT is the contract D-093 reports:\n{repro_prose}"
    )


# --- fallout GI-004 (lead ruling): one voice for the claim-vs-subject split --
@pytest.mark.parametrize(
    "site",
    [validate_defect_filing, record_denylist_tripwire],
    ids=["validate_defect_filing", "record_denylist_tripwire"],
)
def test_both_claim_vs_subject_sites_ask_the_vocabulary(site) -> None:
    """fallout GI-004, and the lead's ruling on the note this casting raised in
    GRIND cycle 4: the split between the denylist entries that read a CLAIM and
    the one that reads a SUBJECT is spelled ONCE, in the vocabulary that owns
    the dispatcher.

    Two sites in `tools/foundry.py` turn on that difference — the D-078 rung in
    `validate_defect_filing`, which must refuse a claim and must NOT refuse a
    `target_kind`, and `record_denylist_tripwire`'s TEMPER_CANDIDATE path,
    which must admit a subject and must NOT admit a claim. Each used to state
    the ruling in its own voice against the FULL dispatcher: a
    `!= NON_COMMENT` guard at one, a `== NON_COMMENT` lift at the other. One
    ruling in two voices is the divergence class `validate_defect_filing`'s own
    docstring is written about ('Every check those two doors were each trusted
    to remember has eventually diverged'), and it is why
    `vocab.NEVER_DEMOTE_CLAIM_CLASSES` is DERIVED by subtraction rather than
    re-listed: a fifth claim entry must join both sites by construction.

    Asked of the AST rather than of the source text, because both functions
    DISCUSS `never_demote_class` at length in prose that must stay readable —
    it is the calls that may not restate the split, not the sentences that
    explain why.
    """
    tree = ast.parse(textwrap.dedent(inspect.getsource(site)))
    called = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }

    assert "never_demote_claim_class" in called, (
        f"{site.__name__} decides the claim question without asking the "
        f"vocabulary; it calls {sorted(called)}"
    )
    assert "never_demote_class" not in called, (
        f"{site.__name__} asks the FULL dispatcher and must therefore carve "
        f"the subject entry back out itself — which is the second voice this "
        f"pin exists to keep out of the module"
    )


# --- fallout D-135 / D-152: the claim scan reads the fields HARDENING states --
#     its claim in (fallout AC-023 / GI-004 / OT-019 / CT-012 / FR-045 / US-005) -
#
# The HARDENING never-demote rung scanned `security_scan_text(finding)`, the
# COMPLEMENT of NON_CLAIM_FILING_KEYS — which excludes `reproduction_attempted`
# and `class`. Both exclusions were keyed by FIELD while their whole
# justification was keyed by TIER (LATENT's negative space, D-158; a documented
# LATENT example's class, D-099). So the one field HARDENING's own wire contract
# points the claim into ("name the probe you ran and the wrong result you
# observed") was the one field the denylist never read, and a security-property
# claim placed there landed in the tier that holds no gate shut with an EMPTY
# tripwire. The controls below are the other half of the fix: the identical
# statements still file at LATENT, so D-158's repair is untouched.

#: The claim D-135 drove, in the shape a HARDENING filing is TOLD to write:
#: a probe and the wrong result it produced.
_HARDENING_SMUGGLED_CLAIM = (
    "drove the cookie issuer 40x: the session cookie is issued with no "
    "signature, so an attacker can forge one"
)

#: The negative-result statement D-158 requires ACCEPTED at LATENT — the same
#: security vocabulary, reporting an ABSENCE rather than asserting a break.
_LATENT_NEGATIVE_RESULT = (
    "grepped both roots for a signature check on the session cookie and found "
    "0 sites"
)


def test_a_hardening_claim_in_the_reproduction_is_refused_at_both_doors(run_env):
    """fallout AC-023 / GI-004 / OT-019: 'a never-demote class match is REFUSED
    and the tripwire recorded'.

    THE DRIVEN CASE, both doors. Description innocent, the security-property
    claim in `reproduction_attempted` — the field
    `_HARDENING_REPRODUCTION_HINT` sends a HARDENING filer to. Before the fix
    this was ACCEPTED as D-001, open and non-blocking, with
    `observations.json.tripwire` EMPTY; the control (the identical claim in
    `description`) was refused. A tier is never a route around the never-weaken
    guarantee, and via this field it was one.
    """
    from foundry_mcp.tools.orchestration.fix_gate import foundry_sync_defects

    project_root, fdir = run_env

    single = foundry_add_defect(
        **_hardening_arguments(
            project_root,
            description="off-spec probe of the cookie issuer",
            reproduction_attempted=_HARDENING_SMUGGLED_CLAIM,
        )
    )
    assert single.get("denylist_class") == SECURITY_PROPERTY_CLAIM, single
    assert _defects(fdir) == [], "the claim landed in the backlog tier"
    assert [t["denylist_class"] for t in _tripwire(fdir)] == [
        SECURITY_PROPERTY_CLAIM
    ], _tripwire(fdir)

    batch = foundry_sync_defects(
        cycle=1,
        findings=[
            _hardening_finding(
                description="off-spec probe of the cookie issuer",
                reproduction_attempted=_HARDENING_SMUGGLED_CLAIM,
            )
        ],
        project_root=project_root,
    )
    assert SECURITY_PROPERTY_CLAIM in batch.get("error", ""), batch
    assert _defects(fdir) == [], "the batch door persisted the refused claim"
    assert len(_tripwire(fdir)) == 2, "the batch door did not audit the attempt"


def test_the_same_statement_still_files_as_latent_at_both_doors(run_env):
    """fallout D-158's repair, held: the scoping is `tier == HARDENING` and
    NOTHING else.

    convergence AC-007 / convergence OT-005, clause 2 of each, require a LATENT filing whose
    `reproduction_attempted` names a security term it searched for and did not
    find to be ACCEPTED. This is the control for the test above: the same
    field, the same vocabulary, the tier the exclusion was written for.
    """
    from foundry_mcp.tools.orchestration.fix_gate import foundry_sync_defects

    project_root, fdir = run_env

    single = foundry_add_defect(
        cycle=1,
        source="prove",
        defect_type="PARTIAL",
        description="The report renderer omits the per-cycle minutes column.",
        file_path="src/report/render.py",
        defect_class="report-renderer-gap",
        tier="LATENT",
        spec_ref="NFR-002",
        reproduction_attempted=_LATENT_NEGATIVE_RESULT,
        project_root=project_root,
    )
    assert "defect_id" in single, single

    batch = foundry_sync_defects(
        cycle=1,
        findings=[
            _finding(
                source="prove",
                type="PARTIAL",
                tier="LATENT",
                spec_ref="NFR-002",
                **{"class": "report-renderer-gap"},
                description="The report renderer omits the per-cycle minutes column.",
                file="src/report/render2.py",
                reproduction_attempted=_LATENT_NEGATIVE_RESULT,
            )
        ],
        project_root=project_root,
    )
    assert batch.get("added") == 1, batch
    assert _tripwire(fdir) == [], "a negative result fired the audit tripwire"


def test_a_hardening_claim_in_the_class_is_refused_at_both_doors(run_env):
    """fallout D-152 window 1 / fallout GI-004: the ESCALATION KEY was unscanned too.

    Driven with `defect_class="the-auth-token-signature-is-never-verified"` and
    innocent prose everywhere else: accepted at both doors, tripwire empty. The
    `class` exclusion was written over `agents/assayer.md`'s documented LATENT
    example, whose LIVE sibling shares the class — a shape the surface ships.
    No surface documents a HARDENING example, and a finding that could
    legitimately share a class with a security finding may never be HARDENING
    at all, so at this tier the exclusion protected nothing.
    """
    from foundry_mcp.tools.orchestration.fix_gate import foundry_sync_defects

    project_root, fdir = run_env
    claim_class = "the-auth-token-signature-is-never-verified"

    single = foundry_add_defect(
        **_hardening_arguments(
            project_root,
            description="drove the token refresh loop 40x and it never settled",
            defect_class=claim_class,
        )
    )
    assert single.get("denylist_class") == SECURITY_PROPERTY_CLAIM, single
    assert _defects(fdir) == [], single

    batch = foundry_sync_defects(
        cycle=1,
        findings=[
            _hardening_finding(
                description="drove the token refresh loop 40x and it never settled",
                **{"class": claim_class},
            )
        ],
        project_root=project_root,
    )
    assert SECURITY_PROPERTY_CLAIM in batch.get("error", ""), batch
    assert len(_tripwire(fdir)) == 2, _tripwire(fdir)


def test_the_documented_latent_class_still_files(run_env):
    """fallout D-099's repair, held: the class scoping is HARDENING-only.

    `agents/assayer.md` ships `class: "no-auth-guard-on-destructive-endpoints"`
    on BOTH a LATENT example and its LIVE sibling, and `_SECURITY_RE` matches
    the bounded token `auth` inside it. A door that refused its own documented
    example teaches the stream that the prose is wrong; that is why the
    exclusion exists at every tier but the one above.
    """
    project_root, fdir = run_env
    documented_class = "no-auth-guard-on-destructive-endpoints"

    latent = foundry_add_defect(
        cycle=1,
        source="assay",
        defect_type="PARTIAL",
        description="the destructive-endpoint sweep found no reachable instance",
        file_path="src/api/delete.py",
        defect_class=documented_class,
        tier="LATENT",
        reproduction_attempted=(
            "drove every destructive route in the router table; all 9 are "
            "behind the guard"
        ),
        project_root=project_root,
    )
    assert "defect_id" in latent, latent

    live = foundry_add_defect(
        cycle=1,
        source="assay",
        defect_type="PARTIAL",
        description="drove DELETE /projects/1 unauthenticated and it returned 204",
        file_path="src/api/delete.py",
        defect_class=documented_class,
        tier="LIVE",
        project_root=project_root,
    )
    assert "defect_id" in live, live
    assert _tripwire(fdir) == [], "a documented class fired the audit tripwire"


def test_a_hardening_spec_claim_in_the_reproduction_is_refused(run_env):
    """fallout OT-019 / FR-045 / CT-012, clause 2 of the first: the never-demote rung reads
    the whole denylist, and now over the whole claim.

    D-078 widened the HARDENING rung from the security predicate to
    `never_demote_claim_class`. It still read a field set that excluded the
    tier's own evidence field, so a spec-required-behaviour claim placed there
    was accepted exactly as the security one was. Both halves are needed: the
    dispatcher AND the fields it is asked about.
    """
    project_root, fdir = run_env

    refusal = foundry_add_defect(
        **_hardening_arguments(
            project_root,
            description="off-spec probe of the terminal gate",
            reproduction_attempted=(
                "AC-022 requires Foundry-Gate('done') to pass with open "
                "HARDENING defects and the gate refuses; the required "
                "behaviour is absent"
            ),
        )
    )
    assert refusal.get("denylist_class") == SPEC_REQUIRED_BEHAVIOUR_CLAIM, refusal
    assert _defects(fdir) == [], refusal
    assert _tripwire(fdir)[0]["denylist_class"] == SPEC_REQUIRED_BEHAVIOUR_CLAIM


def test_the_claim_scan_field_set_is_scoped_by_the_declared_tier():
    """fallout D-135 / D-152: the two tier-dependent partitions, DERIVED.

    `non_claim_filing_keys` is the one derivation every rung consults, and
    `_CLAIM_BEARING_AT_HARDENING` is subtracted from the two partitions rather
    than re-listed — so a member joining either one joins the scoping by
    construction. What fails here is a member added to a partition and missed
    in the scoping, or a scoping that leaked to a tier it was never written
    for.
    """
    from foundry_mcp.tools.foundry import (
        _CLAIM_BEARING_AT_HARDENING,
        _FILING_ESCALATION_KEYS,
        _FILING_NEGATIVE_SPACE_KEYS,
        NON_CLAIM_FILING_KEYS,
        non_claim_filing_keys,
    )

    assert _CLAIM_BEARING_AT_HARDENING == (
        _FILING_ESCALATION_KEYS | _FILING_NEGATIVE_SPACE_KEYS
    ), "the scoping re-lists its members instead of deriving them"

    for tier in sorted(DEFECT_TIERS - {"HARDENING"}):
        assert non_claim_filing_keys({"tier": tier}) == NON_CLAIM_FILING_KEYS, (
            f"the HARDENING scoping leaked to {tier}, which is D-158 returning"
        )
    assert non_claim_filing_keys({}) == NON_CLAIM_FILING_KEYS
    assert non_claim_filing_keys({"tier": "HARDENING"}) == (
        NON_CLAIM_FILING_KEYS - _CLAIM_BEARING_AT_HARDENING
    )
    # An unhashable declared tier reaches this before any rung validates it,
    # and a validator whose contract is "never raises" may not raise on one.
    assert non_claim_filing_keys({"tier": ["HARDENING"]}) == NON_CLAIM_FILING_KEYS


# --- fallout D-119: the tripwire records the tier the filing attempted -------
def test_the_tripwire_record_names_the_tier_the_filing_attempted(run_env):
    """fallout GI-004: the violation is stated OVER the attempted tier —
    'filing a security claim or a spec-required behaviour failure as HARDENING
    or LATENT' — so which one was attempted is part of the fact recorded.

    DRIVEN: the same never-demote description filed twice, once LATENT and once
    HARDENING. Both were correctly refused, and both wrote a record whose field
    set was exactly ['cycle', 'denylist_class', 'description', 'detail', 'file',
    'fired_at', 'source', 'spec_ref', 'symbol'] — no tier key and no field
    anywhere holding either string, so the two records were indistinguishable
    and `observations.json.tripwire` could not answer the question fallout GI-004 is
    written over.
    """
    project_root, fdir = run_env
    claim = "the login endpoint does not verify the authentication token signature"

    foundry_add_defect(
        cycle=1, source="prove", defect_type="PARTIAL", description=claim,
        file_path="src/api/login.py", defect_class="AUTH_GAP", tier="LATENT",
        reproduction_attempted="drove the endpoint; nothing reproduced",
        project_root=project_root,
    )
    foundry_add_defect(
        **_hardening_arguments(project_root, description=claim)
    )

    fired = _tripwire(fdir)
    assert len(fired) == 2, fired
    assert [t["tier"] for t in fired] == ["LATENT", "HARDENING"], fired
    assert fired[0] != fired[1], (
        "two materially different violations are still recorded identically"
    )


def test_an_observation_tripwire_records_no_attempted_tier(run_env):
    """fallout D-119: `""` is honest, not a default this writer invented.

    `foundry_add_observation` is a caller of the same writer and an observation
    attempts no tier at all, so the record says so rather than borrowing one.
    """
    from foundry_mcp.tools.foundry import foundry_add_observation

    project_root, fdir = run_env

    refusal = foundry_add_observation(
        cycle=1,
        source="prove",
        classification="LINE_DRIFT_CITE",
        description=(
            "the login endpoint does not verify the authentication token "
            "signature"
        ),
        target_kind="comment",
        project_root=project_root,
    )
    assert refusal.get("denylist_class") == SECURITY_PROPERTY_CLAIM, refusal
    fired = _tripwire(fdir)
    assert len(fired) == 1 and fired[0]["tier"] == "", fired


# --- fallout D-101: the re-tier exit carries the provenance ------------------
def test_a_retiered_record_carries_the_provenance_the_refiling_declared(run_env):
    """fallout FR-025 / CT-019 / AC-045: `fallout_of` is accepted and
    ledger-validated at both doors, and was then silently discarded whenever
    the filing took the re-tier exit.

    DRIVEN at both doors with `fallout_of="D-001"` against a matching untiered
    record: both reported success, the persisted record carried no `fallout_of`
    key at all, and neither result mentioned the loss.
    """
    project_root, fdir = run_env
    _seed_untiered(fdir)
    parent = foundry_add_defect(
        cycle=3, source="trace", defect_type="PARTIAL",
        description="drove the parent door and it returned the wrong row",
        file_path="src/api/parent.py", defect_class="PARENT", tier="LIVE",
        project_root=project_root,
    )
    assert "defect_id" in parent, parent

    result = _refile(project_root, fallout_of=parent["defect_id"])
    assert result["defect_id"] == "D-001", result

    retiered = next(d for d in _defects(fdir) if d["id"] == "D-001")
    assert retiered["fallout_of"] == parent["defect_id"], retiered
    assert retiered["tier"] == "LATENT", retiered


def test_every_retiered_record_carries_the_provenance_keys(run_env):
    """fallout AC-045: an ABSENT key is not a measured zero.

    `foundry_state.fallout_rows` counts key-PRESENCE as measured and returns
    verdict `not_measurable` for any cycle pair holding a record with no such
    key — so a re-tiering filing was exactly the shape that made every
    post-change cycle read as unmeasured forever, which is fallout FR-025's acceptance
    figure. Both keys land whether the re-filing declared either or not.
    """
    project_root, fdir = run_env
    _seed_untiered(fdir)

    result = _refile(project_root)
    assert result["defect_id"] == "D-001", result

    retiered = next(d for d in _defects(fdir) if d["id"] == "D-001")
    for key in ("fallout_of", "supersedes"):
        assert key in retiered, (
            f"{key} is absent from a re-tiered record, which the measurement "
            f"reads as never measured"
        )
        assert retiered[key] is None


def test_the_batch_door_retier_leaves_the_provenance_keys_measured(run_env):
    """fallout AC-045, at the door a whole INSPECT stream files through.

    The two provenance parameters default so the batch door in
    `orchestration/fix_gate.py` (casting 2's file) keeps compiling while it is
    repointed — and because the KEY's presence is what the measurement reads,
    the measurability half is closed at that door by the defaults alone.
    """
    from foundry_mcp.tools.orchestration.fix_gate import foundry_sync_defects

    project_root, fdir = run_env
    _seed_untiered(fdir)

    result = foundry_sync_defects(
        cycle=3,
        findings=[
            _finding(
                source="trace", type="UNWIRED", tier="LATENT",
                spec_ref="CT-013", symbol="foundry_next", file="src/api/a.py",
                **{"class": "UNWIRED_SURFACE"},
                description="re-filed with the tier the record never carried",
                reproduction_attempted=(
                    "drove every caller of the display path; none reaches the "
                    "branch, so nothing reproduced"
                ),
            )
        ],
        project_root=project_root,
    )
    assert result.get("retiered_ids") == ["D-001"], result

    retiered = next(d for d in _defects(fdir) if d["id"] == "D-001")
    for key in ("fallout_of", "supersedes"):
        assert key in retiered, retiered


def test_the_retier_never_moves_provenance_the_record_already_declared(run_env):
    """fallout D-101: the `class` rule, one field along.

    Provenance an earlier filing declared is what a later reader has been
    citing; overwriting it on a re-tier would move it mid-run. A NULL is not a
    declaration — `scripts/migrate-archive.py` fills `fallout_of: null` as a
    schema-4 default — so that one is filled and a real value is not.
    """
    project_root, fdir = run_env
    # Seeded with ONE of the two keys, so this also proves the other lands.
    _seed_untiered(fdir, fallout_of="D-900")
    # `D-900` is not in the ledger; the rung that would refuse it reads the
    # RE-FILING's value, and this record's own value is not re-validated.
    parent = foundry_add_defect(
        cycle=3, source="trace", defect_type="PARTIAL",
        description="drove the parent door and it returned the wrong row",
        file_path="src/api/parent.py", defect_class="PARENT", tier="LIVE",
        project_root=project_root,
    )

    _refile(project_root, fallout_of=parent["defect_id"])

    retiered = next(d for d in _defects(fdir) if d["id"] == "D-001")
    assert retiered["fallout_of"] == "D-900", (
        "the re-tier moved provenance the record already declared"
    )
    assert "supersedes" in retiered, retiered


# --- fallout D-157: the tier rules are asked of the RECORD a re-tier -------
# --- classifies, not only of the filing that classifies it ------------------
#
# Both doors validate the INCOMING mapping before they open a transaction, and
# `validate_defect_filing` "reads the mapping and nothing else" by contract. So
# every rung that judges CLAIM PROSE or a CITATION was asked about the filing
# and never about the stored record — and a re-tier keeps the record's own
# `description` and `spec_ref` exactly where the earlier filing put them. The
# tier arrived; the prose that tier forbids stayed.
#
# DRIVEN twice before the guard, against fresh run directories: an untiered
# D-001 whose stored record cited fallout FR-014, re-filed as HARDENING with no
# citation of its own, came out `{'id': 'D-001', 'tier': 'HARDENING',
# 'spec_ref': <the record's own cite>, 'status': 'open'}`; an untiered D-001
# whose OWN description asserted an authentication property came out tier
# HARDENING with the claim intact and `observations.json` tripwire length 0.

#: The claim on the SEEDED record, spelled once. It is the register
#: `agents/*.md` use, and it is the RECORD's prose — every re-filing below
#: carries innocent prose of its own, which is the whole point.
_RECORD_CLAIM = (
    "the auth token is never verified, so an attacker reaches the handler"
)

#: A HARDENING reproduction that names a probe and its wrong result without
#: naming a requirement — so the rung under test is the one the test is about
#: and not the never-demote claim rung reading this field (which HARDENING,
#: alone among the tiers, does read).
_PROBE = "drove the off-spec probe 40 times; the row came back empty every time"


def test_the_retier_refuses_a_record_whose_own_spec_ref_the_new_tier_forbids(run_env):
    """fallout AC-055 / GI-028 / FR-057 / OT-019 verbatim: 'both filing doors
    refuse a HARDENING record that carries any `spec_ref`, naming the tier
    rule.'

    fallout GI-028's violation column is 'a HARDENING record carrying a
    `spec_ref` for context' — a RECORD, which is what this exit mints. The
    seeded record carries a citation of its own and the re-filing carries none,
    so the door's own rung has nothing to refuse and the record's citation is
    the only thing standing between the ledger and a HARDENING row carrying a
    spec_ref.
    """
    from foundry_mcp.tools.foundry import TIER_NOT_ALLOWED

    project_root, fdir = run_env
    _seed_untiered(fdir)

    result = _refile(
        project_root, tier="HARDENING", reproduction_attempted=_PROBE
    )

    assert "defect_id" not in result, result
    assert TIER_NOT_ALLOWED in result["error"], result
    assert result["field"] == "spec_ref", result
    assert result["retier_target"] == "D-001", result
    # The refusal names the RECORD, or a filer carrying no spec_ref has nothing
    # to act on.
    assert "D-001" in result["error"], result

    records = _defects(fdir)
    assert len(records) == 1, "nothing was appended beside the refused re-tier"
    assert "tier" not in records[0], "and the record was not classified"
    assert records[0]["spec_ref"] == "CT-013", records[0]


def test_the_batch_door_refuses_the_same_re_tier_on_the_same_field(run_env):
    """fallout AC-055 / GI-028 — 'at BOTH doors, naming the same field first'.

    The batch door consumes the shared helper's result as
    ``if retier_id is not None``, so the refusal cannot be a return value there
    without being counted as a successful re-tier. It travels the seam both
    doors already have instead: the raise aborts the transaction (nothing is
    written, which is what keeps this door's refusal all-or-nothing) and
    ``@ledger_refusals`` turns it into the house refusal.
    """
    from foundry_mcp.tools.foundry import TIER_NOT_ALLOWED
    from foundry_mcp.tools.orchestration.fix_gate import foundry_sync_defects

    project_root, fdir = run_env
    _seed_untiered(fdir)

    result = foundry_sync_defects(
        cycle=3,
        findings=[
            _finding(
                source="trace", type="UNWIRED", tier="HARDENING",
                spec_ref="", symbol="foundry_next", file="src/api/a.py",
                **{"class": "UNWIRED_SURFACE"},
                description="re-filed with the tier the record never carried",
                reproduction_attempted=_PROBE,
            )
        ],
        project_root=project_root,
    )

    assert result.get("retiered_ids") in (None, []), result
    assert TIER_NOT_ALLOWED in result["error"], result
    assert result["field"] == "spec_ref", result
    assert result["retier_target"] == "D-001", result

    records = _defects(fdir)
    assert len(records) == 1 and "tier" not in records[0], records


def test_the_retier_refuses_a_record_whose_own_prose_is_a_security_claim(run_env):
    """fallout GI-004 / NFR-004 / OT-019: 'A HARDENING filing carrying any
    `spec_ref`, or matching a never-demote class, is refused.'

    The claim is on the RECORD and the re-filing's prose is innocent, so the
    door's own denylist rung — which reads the filing — has nothing to match.
    Driven before the guard: retiered=1, tier HARDENING persisted with the
    claim intact, `observations.json` tripwire length 0. A security-property
    claim parked in a tier that holds no gate shut, with the audit control
    silent, is the outcome the denylist is absolute about.
    """
    project_root, fdir = run_env
    _seed_untiered(fdir, description=_RECORD_CLAIM, spec_ref="")

    result = _refile(
        project_root, tier="HARDENING", reproduction_attempted=_PROBE
    )

    assert "defect_id" not in result, result
    assert SECURITY_PROPERTY_CLAIM in result["error"], result
    assert result["denylist_class"] == SECURITY_PROPERTY_CLAIM, result
    assert result["retier_target"] == "D-001", result

    records = _defects(fdir)
    assert len(records) == 1 and "tier" not in records[0], records

    # D-061: the ATTEMPT is what the audit record exists to capture, and it is
    # written even though nothing reached the defect ledger.
    fired = _tripwire(fdir)
    assert len(fired) == 1, fired
    assert fired[0]["denylist_class"] == SECURITY_PROPERTY_CLAIM, fired[0]
    assert fired[0]["tier"] == "HARDENING", fired[0]
    assert _RECORD_CLAIM in fired[0]["description"], fired[0]


def test_the_retier_refuses_a_security_claim_record_at_latent_too(run_env):
    """fallout GI-004's OTHER half: the violation is 'filing a security claim
    ... as HARDENING **or LATENT**', so the guard is scoped to
    NON_BLOCKING_TIERS and not to the new tier alone.

    LATENT is the older of the two non-blocking tiers and the one every
    pre-change stream reached for, so a re-tier into it is the likelier route
    to the same parked claim.
    """
    project_root, fdir = run_env
    _seed_untiered(fdir, description=_RECORD_CLAIM, spec_ref="")

    result = _refile(project_root)  # tier LATENT, by default

    assert "defect_id" not in result, result
    assert SECURITY_PROPERTY_CLAIM in result["error"], result
    assert _defects(fdir)[0].get("tier") is None, _defects(fdir)[0]
    assert len(_tripwire(fdir)) == 1, _tripwire(fdir)


def test_the_batch_door_refuses_the_security_claim_re_tier_as_well(run_env):
    """fallout GI-004 at the door a whole INSPECT stream files through.

    The ENFORCEMENT half needs nothing from the run dir, so it is closed at
    this door by the raise alone; the AUDIT half is asserted at the single door
    above and reaches this one when its call site passes `fdir` (cross-casting
    concern to casting 2, whose file `orchestration/fix_gate.py` is). This test
    deliberately makes no claim about the tripwire here — a pin that asserted
    it is EMPTY would pin the gap as correct.
    """
    from foundry_mcp.tools.orchestration.fix_gate import foundry_sync_defects

    project_root, fdir = run_env
    _seed_untiered(fdir, description=_RECORD_CLAIM, spec_ref="")

    result = foundry_sync_defects(
        cycle=3,
        findings=[
            _finding(
                source="trace", type="UNWIRED", tier="HARDENING",
                spec_ref="", symbol="foundry_next", file="src/api/a.py",
                **{"class": "UNWIRED_SURFACE"},
                description="re-filed with the tier the record never carried",
                reproduction_attempted=_PROBE,
            )
        ],
        project_root=project_root,
    )

    assert SECURITY_PROPERTY_CLAIM in result["error"], result
    records = _defects(fdir)
    assert len(records) == 1 and "tier" not in records[0], records


def test_the_record_tier_guard_leaves_the_live_re_tier_exactly_as_it_was(run_env):
    """fallout GI-004 — the SCOPE, pinned as a rule rather than left to read as
    an omission.

    `NON_BLOCKING_TIERS`' own block states the reason: 'what the denylist
    exists to stop is a claim filed where it holds no gate shut, and LIVE — the
    one tier that does hold one shut — is therefore the exclusion.' A security
    claim's correct home is LIVE, so a guard that refused the re-tier INTO it
    would leave the record permanently untiered and hand the stream no exit at
    all. It also keeps the LIVE prose floor unreachable by construction, so a
    description-less record still classifies exactly as it did.
    """
    project_root, fdir = run_env
    _seed_untiered(fdir, description=_RECORD_CLAIM, spec_ref="")

    result = _refile(project_root, tier="LIVE", reproduction_attempted="")

    assert result["defect_id"] == "D-001", result
    assert result["retiered_ids"] == ["D-001"], result
    assert _defects(fdir)[0]["tier"] == "LIVE", _defects(fdir)[0]
    assert _tripwire(fdir) == [], "a re-tier into the blocking tier demotes nothing"
