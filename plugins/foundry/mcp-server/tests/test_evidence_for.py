"""Phase 5 / EVID-02 RED scaffolding.

Mirrors plugins/foundry/mcp-server/tests/test_evidence.py shape (Phase 4 /
EVID-01) verbatim. All stubs SKIP or fail until Plans 05-02 (parser) and
05-03 (coverage diff + foundry_accept_casting integration) land bodies.

Plan 05-04 lands teammate.md Step 11 + start.md F0.5 informational note;
no test surface for those (they are doc edits — verified via grep).

Stub allocation:

Plan 05-02 territory (parser + token allowlist) — 4 stubs that fail
meaningfully via assert against the expected post-05-02 behavior:
  - test_known_header_directives_includes_for
  - test_evidence_for_clean_parsed_to_list
  - test_evidence_for_malformed_raises_token
  - test_failure_tokens_includes_unbound_and_malformed

Plan 05-03 territory (coverage diff + foundry_accept_casting integration +
provenance schema extension) — 5 stubs that pytest.skip until Plan 05-03
wires the gate; flip to live assertions when 05-03 lands:
  - test_provenance_record_has_evidence_for_field
  - test_unbound_requirements_rejects_with_named_ids
  - test_many_to_many_overlap_accepts
  - test_omit_evidence_for_header_rejects
  - test_v20_legacy_bypasses_evidence_for_check

Pitfall 5 guard — 1 stub that asserts inline-`#` capture behavior:
  - test_evidence_for_with_inline_comment_captures_all_ids

RED-or-SKIP discipline mirrors Phase 4 Plan 04-01:

- Module-top ``importorskip("foundry_mcp.tools.evidence")`` allows the module
  to SKIP cleanly even if the production module breaks temporarily during
  Plan 05-02's parser edit.
- Plan 05-02 territory stubs raise AssertionError live (the parser /
  allowlist edits are local; tests fire as-soon-as Plan 05-02 ships).
- Plan 05-03 territory stubs use ``pytest.skip("Plan 05-03 territory")``
  because the integration path (verify_evidence + foundry_accept_casting +
  manifest synthesis) is multi-step and lands as one wave.
"""

from __future__ import annotations

import pytest

# Module-top importorskip mirrors Phase 4 discipline. The skipped state lets
# ``pytest --collect-only`` succeed even pre-05-02 if the module happens to
# break temporarily.
evidence = pytest.importorskip("foundry_mcp.tools.evidence")


# ---------------------------------------------------------------------------
# Plan 05-02 territory — parser + token allowlist (4 stubs)
# ---------------------------------------------------------------------------


def test_known_header_directives_includes_for():
    """Phase 5 grep contract: ``_KNOWN_HEADER_DIRECTIVES`` extends with
    ``"for"``. Plan 05-02 owns the one-line frozenset extension.

    RED until Plan 05-02 ships.
    """
    assert "for" in evidence._KNOWN_HEADER_DIRECTIVES, (
        "Plan 05-02 must extend _KNOWN_HEADER_DIRECTIVES with 'for'"
    )


def test_evidence_for_clean_parsed_to_list(load_fixture):
    """Well-formed ``# evidence-for: US-1, FR-2`` parses to ordered list.

    Plan 05-02 lands the directive-dispatch branch in
    ``_parse_evidence_header``. Declared order preserved (mirrors
    ``# evidence-volatile:`` ordering discipline from Phase 4 Pitfall 5).

    RED until Plan 05-02 ships.
    """
    text = load_fixture("evidence/evidence_log_for_clean.log")
    parsed = evidence._parse_evidence_header(text)
    assert parsed.get("evidence_for") == ["US-1", "FR-2"], (
        f"Plan 05-02 parser must return evidence_for=['US-1', 'FR-2']; "
        f"got {parsed.get('evidence_for')}"
    )


def test_evidence_for_malformed_raises_token(load_fixture):
    """``# evidence-for: bogus, not-an-id`` (zero valid IDs) raises
    ``ValueError`` with token prefix ``EVIDENCE_FOR_MALFORMED``.

    Mirrors Phase 4's ``EVIDENCE_VOLATILE_MALFORMED`` raise-path. Plan
    05-02 lands the malformed-detection at parser-time.

    RED until Plan 05-02 ships.
    """
    text = load_fixture("evidence/evidence_log_for_malformed.log")
    with pytest.raises(ValueError, match="EVIDENCE_FOR_MALFORMED"):
        evidence._parse_evidence_header(text)


def test_failure_tokens_includes_unbound_and_malformed():
    """``KNOWN_EVIDENCE_FAILURE_TOKENS`` extends from 8 tokens (Phase 4) to
    10 (Phase 5) to 11 (fallout / US-008). The Phase 5 pair is
    EVIDENCE_REQUIREMENT_UNBOUND + EVIDENCE_FOR_MALFORMED; the eleventh is
    EVIDENCE_COMMAND_SYNTAX, the parse-before-execute refusal CT-015 / FR-051
    add to the sweep.

    Closed-vocabulary discipline mirrors Phase 4's
    ``test_failure_tokens_are_in_allowlist``: any TWELFTH token = code-edit
    forced through this test.

    D-002/D-018/D-044: the pin, not the code, was the obsolete side when
    EVIDENCE_COMMAND_SYNTAX landed. The count is raised rather than deleted —
    deleting it is what an accreting vocabulary wants, and the whole value of
    this assertion is that a new member cannot arrive quietly. Each token is
    named here so the next raise has to say which member it is admitting.
    """
    actual = frozenset(evidence.KNOWN_EVIDENCE_FAILURE_TOKENS)
    assert "EVIDENCE_REQUIREMENT_UNBOUND" in actual, (
        "Plan 05-02 must add EVIDENCE_REQUIREMENT_UNBOUND to "
        "KNOWN_EVIDENCE_FAILURE_TOKENS"
    )
    assert "EVIDENCE_FOR_MALFORMED" in actual, (
        "Plan 05-02 must add EVIDENCE_FOR_MALFORMED to "
        "KNOWN_EVIDENCE_FAILURE_TOKENS"
    )
    assert "EVIDENCE_COMMAND_SYNTAX" in actual, (
        "US-008 / FR-051 must add EVIDENCE_COMMAND_SYNTAX to "
        "KNOWN_EVIDENCE_FAILURE_TOKENS — the sweep's parse-before-execute "
        "refusal names it, and a token outside this closed allowlist reaches "
        "the operator as an unexplained failure"
    )
    assert len(evidence.KNOWN_EVIDENCE_FAILURE_TOKENS) == 11, (
        f"the closed allowlist stands at exactly 11 tokens (Phase 4's 8, "
        f"Phase 5's EVIDENCE_REQUIREMENT_UNBOUND + EVIDENCE_FOR_MALFORMED, "
        f"and US-008's EVIDENCE_COMMAND_SYNTAX); got "
        f"{len(evidence.KNOWN_EVIDENCE_FAILURE_TOKENS)}. A new member is a "
        f"code edit HERE as well as in evidence.py — name it above and raise "
        f"this count, never delete the count"
    )


def test_a_declared_volatile_admits_a_keyed_interpreter_path_and_refuses_a_bare_one():
    """The HEADER-side door onto the environmental-field allowlist.

    ``tests/test_evidence.py`` walks the REGISTRY: which grammars are declared,
    which the corpus witnesses, which admit their own witness pair. This drives
    the other direction, the one an evidence author actually takes — a
    ``# evidence-volatile:`` line parsed out of a real header, handed to
    ``_compare_byte_match``, deciding whether the byte-match that redaction
    bought erased a FIELD or a CLAIM. Same registry, a different caller: the
    parse-then-compare path rather than the registry sweep.

    D-003/D-019: the shape pinned here is the one ``pytest_platform_interpreter``
    exists for and the one ``evidence/casting-5-platform-witness.log`` carries.
    A sweep re-executes in a detached worktree with no project ``.venv``, where
    uv builds a fresh ``builds-v0/.tmpXXXXXX`` env every run, so a ``pytest -v``
    log's interpreter disagrees on every honest verification.

    The KEY is what makes that admissible, and the second half is why the entry
    is not simply "paths are volatile": the same two paths with the
    ``platform … --`` text no longer beside them are a bare relocation, which
    says nothing about whether it is where the run happened or what the run
    reported, and the guard refuses it.
    """
    interpreter = "/Users/x/.cache/uv/builds-v0/{build}/bin/python"
    keyed = (
        "============================= test session starts ==============\n"
        "platform darwin -- Python 3.14.6, pytest-9.1.1, pluggy-1.6.0 -- "
        + interpreter
        + "\nconfigfile: pyproject.toml\n"
        "tests/test_x.py::test_y PASSED\n"
        "============================== 1 passed =======================\n"
    )
    matched, _, _, _ = evidence._compare_byte_match(
        keyed.format(build=".tmphgnUSu"),
        keyed.format(build=".tmpvRAwWQ"),
        [r"(?:^|\s)platform \S+ -- Python \S+.*"],
    )
    assert matched, (
        "the interpreter pytest -v reports, under the platform key that "
        "identifies it, is the field pytest_platform_interpreter admits — a "
        "log that declares the whole `platform … --` line must survive its "
        "own re-execution in a worktree"
    )

    unkeyed = (
        "the suite ran and reported this build directory as its result: "
        + interpreter
        + "\nconfigfile: pyproject.toml\n"
        "tests/test_x.py::test_y PASSED\n"
    )
    with pytest.raises(ValueError, match="EVIDENCE_VOLATILE_MALFORMED"):
        evidence._compare_byte_match(
            unkeyed.format(build=".tmphgnUSu"),
            unkeyed.format(build=".tmpvRAwWQ"),
            [r"/\S*"],
        )


# ---------------------------------------------------------------------------
# Plan 05-03 territory — verify_evidence + foundry_accept_casting integration
# (5 stubs, pytest.skip until Plan 05-03 wires the gate)
# ---------------------------------------------------------------------------


def test_provenance_record_has_evidence_for_field(run_accept_casting_with_evidence):
    """SC#1: every provenance record carries an ``evidence_for`` key with
    the parsed list of requirement IDs.

    Plan 05-03 extends the 13-field provenance schema (Phase 4) to 14
    fields; ``evidence_for`` is the new field. Closed-schema discipline
    enforced via ``test_provenance_record_has_required_fields`` analog
    landing in Plan 05-03.
    """
    result = run_accept_casting_with_evidence(
        "evidence/evidence_log_for_clean.log",
        spec_format_version="v2.1",
    )
    assert result["verdict"] == "accepted"
    prov = result["provenance"]
    assert prov is not None
    assert "evidence_for" in prov, (
        "Plan 05-03 must add evidence_for key to provenance record"
    )
    assert prov["evidence_for"] == ["US-1", "FR-2"], (
        f"provenance.evidence_for must equal ['US-1', 'FR-2']; "
        f"got {prov.get('evidence_for')}"
    )


def test_unbound_requirements_rejects_with_named_ids(
    run_accept_casting_with_evidence,
):
    """SC#2 + SC#4: casting cites [FR-1, FR-2, FR-3] but evidence binds
    only FR-1 → EVIDENCE_REQUIREMENT_UNBOUND with named IDs in the failure
    payload.

    Strict-not-permissive discipline: any uncovered ID rejects the casting.
    The failure payload names exactly the unbound IDs ([FR-2, FR-3]) so
    the lead can trace which artifact is missing.
    """
    result = run_accept_casting_with_evidence(
        "evidence/evidence_log_for_partial.log",
        spec_format_version="v2.1",
        casting_req_ids_override=["FR-1", "FR-2", "FR-3"],
    )
    assert result["verdict"] == "rejected"
    assert result["failure_token"] == "EVIDENCE_REQUIREMENT_UNBOUND"
    assert result.get("unbound_requirements") == ["FR-2", "FR-3"], (
        f"unbound_requirements must list missing IDs in sorted order; "
        f"got {result.get('unbound_requirements')}"
    )


def test_many_to_many_overlap_accepts(run_accept_casting_with_evidence):
    """SC#3: multi-artifact-same-requirement coverage works.

    Primary: evidence_log_for_overlap_a.log (binds FR-1, FR-2).
    Extra: evidence_log_for_overlap_b.log (binds FR-2, FR-3).
    Combined coverage: FR-1 (from a), FR-2 (from both), FR-3 (from b).
    Casting cites [FR-1, FR-2, FR-3] → fully covered → accepted.
    """
    result = run_accept_casting_with_evidence(
        "evidence/evidence_log_for_overlap_a.log",
        spec_format_version="v2.1",
        extra_evidence_fixtures=("evidence/evidence_log_for_overlap_b.log",),
        casting_req_ids_override=["FR-1", "FR-2", "FR-3"],
    )
    assert result["verdict"] == "accepted", (
        f"many-to-many coverage must accept; got verdict={result['verdict']}, "
        f"failure_token={result.get('failure_token')}"
    )


def test_omit_evidence_for_header_rejects(run_accept_casting_with_evidence):
    """SC#2 strict-not-permissive: an evidence file with NO ``# evidence-for:``
    header binds zero IDs. If the casting cites any IDs, the gate rejects.

    This locks the strict default: missing for-line = unbound, NOT
    "covers everything implicitly". Permissive interpretation would make
    the gate trivially defeatable by deleting the header.
    """
    result = run_accept_casting_with_evidence(
        "evidence/evidence_log_for_clean.log",
        spec_format_version="v2.1",
        omit_evidence_for_header=True,
        casting_req_ids_override=["US-1", "FR-2"],
    )
    assert result["verdict"] == "rejected"
    assert result["failure_token"] == "EVIDENCE_REQUIREMENT_UNBOUND"
    assert result.get("unbound_requirements") == ["FR-2", "US-1"], (
        f"missing-for-line rejection must name all casting IDs as unbound; "
        f"got {result.get('unbound_requirements')}"
    )


def test_v20_legacy_bypasses_evidence_for_check(run_accept_casting_with_evidence):
    """SC#3 boundary (Pitfall 8): v2.0 specs route through Phase 4's
    stream-skip path BEFORE the Phase 5 coverage gate fires.

    A v2.0 spec with a casting that cites three IDs but evidence that
    binds only two MUST still report ``verdict=skipped`` + ``ok=True``
    (legacy spec, no Phase 5 enforcement).
    """
    result = run_accept_casting_with_evidence(
        "evidence/evidence_log_for_clean.log",
        spec_format_version="v2.0",
        casting_req_ids_override=["FR-1", "FR-2", "FR-3"],
    )
    # Phase 4's v2.0 path returns verdict=skipped; Phase 5 coverage gate
    # MUST NOT fire on legacy specs.
    assert result["verdict"] == "skipped", (
        f"v2.0 spec must route through stream-skip; got verdict="
        f"{result['verdict']}, failure_token={result.get('failure_token')}"
    )


# ---------------------------------------------------------------------------
# Pitfall 5 guard — locks inline-`#` parser behavior (1 stub)
# ---------------------------------------------------------------------------


def test_evidence_for_with_inline_comment_captures_all_ids(load_fixture):
    """Pitfall 5 lock: ``# evidence-for: US-1, # FR-2 deferred`` captures
    BOTH ``US-1`` AND ``FR-2``. The for-line is one parser-line; the
    inline ``#`` is just text — NOT a comment-suppression mechanism.

    A teammate's intent of "comment out FR-2" is NOT honored by the
    parser. This test docstring exists explicitly so future readers don't
    mistake the captured-anyway behavior for a bug. If a comment-
    suppression mechanism is desired, it requires an explicit feature
    edit + new test — not a regex tweak.

    RED until Plan 05-02 ships the directive-dispatch branch.
    """
    text = load_fixture("evidence/evidence_log_for_with_comment.log")
    parsed = evidence._parse_evidence_header(text)
    # The req_id_pattern from foundry_handoff.py:324 captures ID tokens
    # regardless of surrounding non-ID text. Both US-1 and FR-2 surface.
    assert parsed.get("evidence_for") == ["US-1", "FR-2"], (
        f"Pitfall 5 lock — inline `#` is NOT comment suppression; "
        f"both US-1 and FR-2 must be captured. Got: "
        f"{parsed.get('evidence_for')}"
    )
