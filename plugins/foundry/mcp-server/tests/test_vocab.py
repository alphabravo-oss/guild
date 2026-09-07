"""FR-013 — the canonical vocabulary module.

Pins the export surface of ``foundry_mcp.schemas.vocab``. Later castings wire
the six re-typed vocabulary copies (server.py's two schema enums, the
orchestrator's VALID_STREAMS and its sync coercion set, the marker-clear
lists, and measure-run.py's roster) to the names asserted here, so a rename
or a narrowing breaks this module first.

Two guarantees carry the most weight:

  * NFR-002 (no narrowing) — every value the live surfaces accept today must
    still be a member. The baselines are IMPORTED from the real modules, not
    re-typed here, so the assertions track the surfaces rather than a copy.
  * AC-002 (never weaken) — the denylist outranks the observation classes.
    A finding matching both is a DEFECT.

GI-010 / GI-026 — THE WAVE-2 REPOINT, NOW LANDED
------------------------------------------------
This module reached the monolith lazily, inside test bodies and one fixture.
GI-010 is "No facade: rewrite every import" and GI-026 puts the repoint "in the
same casting as the source move" — but the source move was casting 2's, in wave
2, so the imports were written here against the destinations the module
contract named and twelve registers stood RED until that casting landed:

    orchestration/streams.py     VALID_STREAMS
    orchestration/gates.py       BLOCKING_TIERS
    orchestration/teams.py       _check_active_teams  (the ``run_env`` fixture)
    orchestration/escalation.py  _persisted_escalations, and the shipped-reader
                                 roster row naming the module that reads
                                 ``escalation.json``

All four destinations now exist and all twelve are green, so the list is kept
as the record of what the repoint cost rather than as a live exception. Nothing
in this module is expected to fail; a red register here is a defect, not a wait.

``test_the_monolith_earns_delta_until_the_split_lands`` was deleted when the
split landed, on its own instruction — it skipped with "the monolith is gone,
so the wave-1/wave-2 window has closed and this row has nothing left to
describe — delete it", and a row that describes nothing is worse than absent
because it reads as coverage.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import os
import re
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

from foundry_mcp.schemas import vocab

# tests/test_vocab.py -> [0]=tests, [1]=mcp-server, [2]=foundry, [3]=plugins,
# [4]=repo-root. Mirrors test_measure_run.py's precedent.
REPO_ROOT = Path(__file__).resolve().parents[4]
SRC = REPO_ROOT / "plugins" / "foundry" / "mcp-server" / "src"
BASH_TWIN = REPO_ROOT / "plugins" / "foundry" / "scripts" / "foundry.sh"

# The canonical 15-id roster, spelled out once so a silent edit to vocab.py
# fails here rather than propagating to the instrumentation.
EXPECTED_CANONICAL_STREAM_IDS = frozenset({
    "TRACE", "FLOW_TRACE", "PROVE", "RESEARCH_AUDIT", "COVERAGE_DIFF",
    "TEST-01", "SIGHT", "TEST",
    "EVID-01", "EVID-02",
    "INTV-01", "TYPE-01", "TYPE-02",
    "PROBE-01", "INTENT-01",
})


def _foundry_defect_schema() -> dict:
    """The LIVE Foundry-Defect input schema, read off the running server.

    Mirrors test_orchestrator_gates.test_tool_schema_enum_matches_runtime_valid_set:
    reading the real tool registration is what makes the no-narrowing
    assertions track server.py instead of a transcription of it.
    """
    from foundry_mcp import server as foundry_server

    tools = asyncio.run(foundry_server.list_tools())
    tool = next(t for t in tools if t.name == "Foundry-Defect")
    return tool.inputSchema["properties"]


# ---------------------------------------------------------------------------
# Stream vocabularies.
# ---------------------------------------------------------------------------


def test_canonical_stream_ids_is_the_15_id_roster() -> None:
    assert vocab.CANONICAL_STREAM_IDS == EXPECTED_CANONICAL_STREAM_IDS
    assert len(vocab.CANONICAL_STREAM_IDS) == 15
    assert isinstance(vocab.CANONICAL_STREAM_IDS, frozenset)


def test_stream_wire_ids_never_narrows_valid_streams() -> None:
    """NFR-002 — every stream Foundry-Stream accepts today keeps working."""
    # GI-010 / GI-026 — the wave-2 destination. Casting 2's split defines
    # this symbol in the module named below and its completion report's
    # `## Symbol map` is the authority; where the map and this differ, the
    # map wins and this is the edit.
    from foundry_mcp.tools.orchestration.streams import VALID_STREAMS

    assert VALID_STREAMS <= vocab.STREAM_WIRE_IDS, (
        f"narrowed: {VALID_STREAMS - vocab.STREAM_WIRE_IDS}"
    )


def test_stream_wire_ids_adds_the_fr_013_values() -> None:
    """FR-013 / AC-018 — flow_trace and test01 are members."""
    assert "test01" in vocab.STREAM_WIRE_IDS
    assert "flow_trace" in vocab.STREAM_WIRE_IDS


def test_wire_to_canonical_is_total_and_lands_in_canonical() -> None:
    assert set(vocab.WIRE_TO_CANONICAL) == set(vocab.STREAM_WIRE_IDS), (
        "WIRE_TO_CANONICAL must be total over STREAM_WIRE_IDS"
    )
    assert set(vocab.WIRE_TO_CANONICAL.values()) <= vocab.CANONICAL_STREAM_IDS


@pytest.mark.parametrize(
    "wire,canonical",
    [
        ("trace", "TRACE"),
        ("prove", "PROVE"),
        ("test01", "TEST-01"),
        ("probe", "PROBE-01"),
        ("flow_trace", "FLOW_TRACE"),
        ("coverage_diff", "COVERAGE_DIFF"),
    ],
)
def test_canonical_stream_id_maps_wire_to_canonical(wire: str, canonical: str) -> None:
    assert vocab.canonical_stream_id(wire) == canonical


def test_canonical_stream_id_is_identity_on_canonical_ids() -> None:
    for sid in vocab.CANONICAL_STREAM_IDS:
        assert vocab.canonical_stream_id(sid) == sid


def test_canonical_stream_id_refuses_unknown_without_coercing() -> None:
    """CT-002 — no silent coercion of an unknown source onto a known stream."""
    assert vocab.canonical_stream_id("BOGUS_STREAM") is None
    assert vocab.canonical_stream_id("") is None
    # Never raises, even on a non-str the JSON layer could hand it.
    assert vocab.canonical_stream_id(None) is None  # type: ignore[arg-type]
    assert vocab.canonical_stream_id(17) is None  # type: ignore[arg-type]


def test_defect_source_ids_never_narrows_the_live_schema() -> None:
    """NFR-002 — assay and temper still pass, alongside every wire id."""
    live = set(_foundry_defect_schema()["source"]["enum"])
    assert live <= vocab.DEFECT_SOURCE_IDS, (
        f"narrowed: {live - vocab.DEFECT_SOURCE_IDS}"
    )
    assert {"assay", "temper"} <= vocab.DEFECT_SOURCE_IDS
    assert vocab.STREAM_WIRE_IDS <= vocab.DEFECT_SOURCE_IDS


# ---------------------------------------------------------------------------
# Defect vocabularies.
# ---------------------------------------------------------------------------


def test_defect_types_never_narrows_the_live_schema() -> None:
    live = set(_foundry_defect_schema()["defect_type"]["enum"])
    assert live <= vocab.DEFECT_TYPES, f"narrowed: {live - vocab.DEFECT_TYPES}"


def test_defect_types_adds_the_fr_013_values() -> None:
    """FR-013 — PARTIAL plus the MISPLACED/ARCHITECTURAL_PLACEMENT pair.

    Both spellings are members: agents/tracer.md and agents/assayer.md
    instruct streams to persist type "ARCHITECTURAL_PLACEMENT" while using
    MISPLACED as the verdict word, and neither contract may break.
    """
    assert {"PARTIAL", "ARCHITECTURAL_PLACEMENT", "MISPLACED"} <= vocab.DEFECT_TYPES


def test_canonical_defect_type_folds_the_alias() -> None:
    assert vocab.canonical_defect_type("MISPLACED") == "ARCHITECTURAL_PLACEMENT"
    assert (
        vocab.canonical_defect_type("ARCHITECTURAL_PLACEMENT")
        == "ARCHITECTURAL_PLACEMENT"
    )


def test_canonical_defect_type_is_identity_on_every_other_member() -> None:
    for value in vocab.DEFECT_TYPES - {"MISPLACED"}:
        assert vocab.canonical_defect_type(value) == value


def test_canonical_defect_type_refuses_unknown_without_coercing() -> None:
    """A non-member yields None so the caller builds a named refusal."""
    assert vocab.canonical_defect_type("FALSE_DOCUMENTED_CONTRACT") is None
    assert vocab.canonical_defect_type("misplaced") is None  # case-sensitive
    assert vocab.canonical_defect_type(None) is None  # type: ignore[arg-type]


def test_finding_classes_is_the_classification_axis() -> None:
    assert vocab.FINDING_CLASSES == frozenset({"DEFECT", "OBSERVATION"})


# ---------------------------------------------------------------------------
# Finding-record vocabularies (D-071).
#
# The export-surface half only. What these vocabularies must AGREE WITH — the
# skills' documented blocks and the schemas served by Validate-Report — is
# pinned in test_findings_schemas.py, which routes every assertion through the
# document rather than through this module.
# ---------------------------------------------------------------------------


def test_finding_id_prefixes_is_the_union_over_the_three_skills() -> None:
    assert vocab.FINDING_ID_PREFIXES == frozenset(
        {"L", "THIN", "SA", "DEV", "PL", "CR", "SP", "DX", "T"}
    )
    assert len(vocab.FINDING_ID_PREFIXES) == 9
    assert isinstance(vocab.FINDING_ID_PREFIXES, frozenset)


def test_finding_id_pattern_is_derived_from_the_prefix_set() -> None:
    """One edit, not two — the pattern is built from the frozenset.

    Asserted by construction rather than by string equality so adding a
    prefix does not need an edit here as well.
    """
    compiled = re.compile(vocab.FINDING_ID_PATTERN)
    for prefix in vocab.FINDING_ID_PREFIXES:
        assert compiled.match(f"{prefix}-1"), f"{prefix}-1 rejected"
        assert compiled.match(f"{prefix}-4217"), "multi-digit ordinals are legal"
    # THIN vs T: an anchored match must backtrack out of the shorter branch
    # rather than committing to it, whichever order the alternation lands in.
    assert compiled.match("THIN-9")
    assert not compiled.match("TH-9")


def test_temper_domain_statuses_carry_the_five_the_skill_reports() -> None:
    assert vocab.TEMPER_DOMAIN_STATUSES == frozenset(
        {"SOLID", "CRACKED", "HOLLOW", "MISSING", "STUCK"}
    )
    assert "UNTESTED" not in vocab.TEMPER_DOMAIN_STATUSES, (
        "UNTESTED was the pre-D-071 schema's invention; temper never "
        "reports it."
    )


# ---------------------------------------------------------------------------
# Observation-class predicates (AC-001).
# ---------------------------------------------------------------------------

# One positive case per class. Each is written to exercise ONLY its own
# class's cue vocabulary, so the exclusivity assertion below is meaningful.
OBSERVATION_CASES = {
    vocab.LINE_DRIFT_CITE: (
        "The cite foundry.py#_save_json points at line 71 but the helper "
        "now starts at line 88 - the line hint is stale."
    ),
    vocab.PROSE_COUNT: (
        "The trailing count comment claims 15 members but the frozenset "
        "now holds 16."
    ),
    vocab.DIRECTION_WORD: (
        "The comment says the helper is defined below, but it now sits "
        "above its caller."
    ),
    vocab.ENUMERATION: (
        "The provenance comment enumerates the consumer sites but the list "
        "omits the marker-clear site."
    ),
}

OBSERVATION_PREDICATES = {
    vocab.LINE_DRIFT_CITE: vocab.is_line_drift_cite,
    vocab.PROSE_COUNT: vocab.is_prose_count,
    vocab.DIRECTION_WORD: vocab.is_direction_word,
    vocab.ENUMERATION: vocab.is_enumeration,
}


def test_observation_classes_names_the_four_ac_001_classes() -> None:
    """The four comment-prose classes each still have their own predicate.

    `TEMPER_CANDIDATE` is deliberately absent from `OBSERVATION_PREDICATES`:
    the four above are DETECTED from a finding's prose, and a candidate is
    RECORDED by the stream that thought of the probe. A prose predicate for it
    would be a regex guessing whether a sentence is a good idea.
    """
    assert frozenset(OBSERVATION_PREDICATES) < vocab.OBSERVATION_CLASSES
    assert len(OBSERVATION_PREDICATES) == 4


def test_observation_classes_admits_the_temper_candidate(  # FR-017 / GI-027
) -> None:
    """CT-017 — the fifth member, and the one that is not comment prose.

    GI-027: "TEMPER's roster = open candidates + its own micro-domains; each is
    closed as DRIVEN (filed or clean)". A candidate is neither a defect nor a
    HARDENING record — HARDENING is a probe that was DRIVEN and failed, and a
    candidate is a probe nobody has driven at all.
    """
    assert vocab.TEMPER_CANDIDATE == "TEMPER_CANDIDATE"
    assert vocab.TEMPER_CANDIDATE in vocab.OBSERVATION_CLASSES
    assert vocab.OBSERVATION_CLASSES == frozenset(
        {vocab.LINE_DRIFT_CITE, vocab.PROSE_COUNT, vocab.DIRECTION_WORD,
         vocab.ENUMERATION, vocab.TEMPER_CANDIDATE}
    )
    assert len(vocab.OBSERVATION_CLASSES) == 5


def test_the_denylist_still_outranks_the_new_observation_class() -> None:
    """AC-002 / GI-004 — the never-weaken guarantee is untouched by the member.

    A tier or a class is never a route around the denylist. A probe idea whose
    description makes a security-property claim is a DEFECT, and the audit
    tripwire names the entry that matched — the same answer the four
    comment-prose classes get.
    """
    assert vocab.TEMPER_CANDIDATE not in vocab.NEVER_DEMOTE_CLASSES
    assert len(vocab.NEVER_DEMOTE_CLASSES) == 4
    assert not (vocab.OBSERVATION_CLASSES & vocab.NEVER_DEMOTE_CLASSES)

    claim = {
        "description": "probe idea: the auth check can be bypassed by a "
                       "crafted header, worth driving",
        "target_kind": "code",
    }
    assert vocab.never_demote_class(claim) is not None, (
        "a probe idea that makes a security-property claim is a DEFECT; the "
        "denylist outranks every observation class, this one included"
    )


@pytest.mark.parametrize("class_name", sorted(OBSERVATION_CASES))
def test_each_observation_class_matches_only_its_own_case(class_name: str) -> None:
    """Each class matches its own positive case and no other class's."""
    finding = {"description": OBSERVATION_CASES[class_name]}
    matched = [
        name for name, pred in OBSERVATION_PREDICATES.items() if pred(finding)
    ]
    assert matched == [class_name], (
        f"{class_name} case matched {matched}"
    )
    assert vocab.observation_class(finding) == class_name


def test_observation_class_returns_none_for_an_unremarkable_finding() -> None:
    assert vocab.observation_class({"description": "the handler returns 500"}) is None
    assert vocab.observation_class({}) is None


def test_line_drift_accepts_the_structured_signal() -> None:
    """A caller that already compared the line hint can say so directly."""
    assert vocab.is_line_drift_cite({"line_hint_stale": True})
    assert not vocab.is_line_drift_cite({"line_hint_stale": False})


# ---------------------------------------------------------------------------
# Never-demote denylist predicates (AC-002).
# ---------------------------------------------------------------------------

DENYLIST_CASES = {
    vocab.SECURITY_PROPERTY_CLAIM: {
        "description": (
            "The endpoint accepts the session token without verifying the "
            "signature."
        )
    },
    vocab.SPEC_REQUIRED_BEHAVIOUR_CLAIM: {
        "description": "AC-024 requires real per-stream counts; the roll-up is empty."
    },
    vocab.UNRESOLVABLE_CITE: {
        "description": "The cite names a symbol that does not resolve anywhere in src/."
    },
    vocab.NON_COMMENT: {
        "description": "the counts are wrong",
        "target_kind": "code",
    },
}

DENYLIST_PREDICATES = {
    vocab.SECURITY_PROPERTY_CLAIM: vocab.is_security_property_claim,
    vocab.SPEC_REQUIRED_BEHAVIOUR_CLAIM: vocab.is_spec_required_behaviour_claim,
    vocab.UNRESOLVABLE_CITE: vocab.is_unresolvable_cite,
    vocab.NON_COMMENT: vocab.is_non_comment,
}


def test_never_demote_classes_names_the_four_ac_002_classes() -> None:
    assert vocab.NEVER_DEMOTE_CLASSES == frozenset(DENYLIST_PREDICATES)
    assert len(vocab.NEVER_DEMOTE_CLASSES) == 4


@pytest.mark.parametrize("class_name", sorted(DENYLIST_CASES))
def test_each_denylist_class_matches_only_its_own_case(class_name: str) -> None:
    finding = DENYLIST_CASES[class_name]
    matched = [name for name, pred in DENYLIST_PREDICATES.items() if pred(finding)]
    assert matched == [class_name], f"{class_name} case matched {matched}"
    assert vocab.never_demote_class(finding) == class_name


def test_denylist_accepts_the_structured_signals() -> None:
    """Caller-supplied resolution and target facts drive two of the four."""
    assert vocab.is_unresolvable_cite({"symbol_resolved": False})
    assert not vocab.is_unresolvable_cite({"symbol_resolved": True})
    assert vocab.is_non_comment({"target_kind": "function"})
    assert not vocab.is_non_comment({"target_kind": "comment"})
    # Absent keys mean "this class does not match" — never a raise.
    assert not vocab.is_non_comment({})
    assert not vocab.is_unresolvable_cite({})


def test_a_spec_ref_alone_is_a_spec_required_behaviour_claim() -> None:
    assert vocab.is_spec_required_behaviour_claim({"spec_ref": "AC-002"})
    assert not vocab.is_spec_required_behaviour_claim({"spec_ref": "  "})


def test_no_ui_meaning_is_the_one_sentence_the_flag_means() -> None:
    """fallout GI-033 / fallout D-080 (concern C-059 row 8) — one meaning.

    `survey/surface.md` FI-2 found `--no-ui` meaning three different things at
    once, and the three disagreed about the DIRECTION of the effect: skip the
    SIGHT audit, suppress banners, or hard-block. It lives here because
    `orchestration/width.py` and `orchestration/teams.py` both reach for it and
    they are the verifier layer, so the sentence cannot live in the largest
    lifecycle module in the tree.

    Pinned on what it SAYS, not merely that it exists: the clause that makes it
    a declaration about the RUN and the clause that denies it is about output
    are the two FI-2 turned on.
    """
    assert isinstance(vocab.NO_UI_MEANING, str) and vocab.NO_UI_MEANING.strip()
    assert "no browsable UI" in vocab.NO_UI_MEANING, (
        "the flag declares a fact about the RUN — the meaning its name carries"
    )
    assert "SIGHT" in vocab.NO_UI_MEANING, "and the consequence that follows"
    assert "banner" not in vocab.NO_UI_MEANING.lower(), (
        "it says nothing about banners: the display is not a UI the run audits, "
        "and a flag that suppressed output would need its own name"
    )


def test_the_section_titles_cannot_be_written_through_any_name() -> None:
    """fallout GI-033 — read-only by CONSTRUCTION, not by nobody having tried.

    Three modules in two layers bind this one object, and casting 7's leaf read
    pins `artifacts.REPORT_SECTION_TITLES is vocab.REPORT_SECTION_TITLES`
    rather than comparing equal — a copy that agreed today was the defect that
    hoist was refused over.

    But identity proves the reference is SHARED, not that nobody writes through
    it. Two independent checks agreed the tree mutates nothing here — a grep
    over the name and an AST scan for subscript assignment, `del`, rebinding
    and the dict mutators — and BOTH miss the same shape:

        t = REPORT_SECTION_TITLES
        t["verdict_matrix"] = "..."

    an alias neither the scan nor the identity pin inspects. A scan and a pin
    that miss the same shape is where construction beats inspection, so the
    ALIAS case below is the one this test exists for; the direct case is the
    easy half.
    """
    from types import MappingProxyType

    assert isinstance(vocab.REPORT_SECTION_TITLES, MappingProxyType)

    with pytest.raises(TypeError):
        vocab.REPORT_SECTION_TITLES["verdict_matrix"] = "hijacked"

    # THE CASE THE SCAN CANNOT SEE: reached through a local name.
    alias = vocab.REPORT_SECTION_TITLES
    with pytest.raises(TypeError):
        alias["verdict_matrix"] = "hijacked"
    with pytest.raises(TypeError):
        del alias["verdict_matrix"]
    with pytest.raises(AttributeError):
        alias.update({"verdict_matrix": "hijacked"})

    assert vocab.REPORT_SECTION_TITLES["verdict_matrix"] == "Verdict matrix", (
        "and the value every reader sees is the one the seal writes"
    )


def test_every_report_section_has_a_title_and_the_two_are_paired_here() -> None:
    """fallout GI-033 (concern C-060 row 2) — the headings the gate checks.

    The report SEAL writes these headings and the DONE gate CHECKS them, and
    those two sit in different layers, so the table cannot live in the
    presentation module that renders it — `artifacts.py` reads it back and
    would have had to import `foundry_report.py` to do so.

    Pairing it with the tuple HERE is what makes the pairing enforceable at
    vocabulary import: a section added to one and not the other fails at the
    vocabulary rather than at whichever reader happened to run first. The
    module-level assertion beside the table is that check; this pins that the
    check has something to bite on and that no title is blank.
    """
    assert set(vocab.REPORT_SECTION_TITLES) == set(vocab.REPORT_REQUIRED_SECTIONS)
    assert len(vocab.REPORT_SECTION_TITLES) == len(vocab.REPORT_REQUIRED_SECTIONS), (
        "one title per section, and the tuple has no duplicate members"
    )
    assert all(
        isinstance(t, str) and t.strip() for t in vocab.REPORT_SECTION_TITLES.values()
    ), "a blank title renders a blank `## ` heading the DONE gate cannot match"

    # A prettifier over the key names is why this is a dict: it would render
    # `unknown_tier_defects` as "Unknown Tier Defects", which reads as a tier
    # called "Unknown Tier".
    assert vocab.REPORT_SECTION_TITLES["unknown_tier_defects"] == "Unknown-tier defects"


def test_the_claim_half_of_the_denylist_is_derived_not_re_listed() -> None:
    """fallout GI-004 / AC-023 / OT-019 (D-078) — the split has one home.

    Three entries read what a finding CLAIMS and one reads its SUBJECT. Two
    doors need the claim half alone and each used to spell the exclusion for
    itself, which is one ruling in two voices.

    DERIVED, so a fifth CLAIM entry joins every rung that reads it by
    construction. That is asserted here by subtraction rather than by naming
    the three: an assertion listing them would go stale in exactly the case
    the derivation exists for.
    """
    assert vocab.NEVER_DEMOTE_SUBJECT_CLASSES == frozenset({vocab.NON_COMMENT})
    assert vocab.NEVER_DEMOTE_CLAIM_CLASSES == (
        vocab.NEVER_DEMOTE_CLASSES - vocab.NEVER_DEMOTE_SUBJECT_CLASSES
    )
    assert vocab.NEVER_DEMOTE_CLAIM_CLASSES <= vocab.NEVER_DEMOTE_CLASSES
    assert vocab.NON_COMMENT not in vocab.NEVER_DEMOTE_CLAIM_CLASSES
    assert len(vocab.NEVER_DEMOTE_CLAIM_CLASSES) == 3


@pytest.mark.parametrize("class_name", sorted(DENYLIST_CASES))
def test_never_demote_claim_class_answers_every_claim_and_drops_the_subject(
    class_name: str,
) -> None:
    """fallout D-078 — the dispatcher both doors read instead of re-typing it.

    Every CLAIM entry answers itself; the SUBJECT entry answers None. Driven
    over the same `DENYLIST_CASES` the full dispatcher is driven over, so a
    new entry joins this pin with its case rather than needing one written.
    """
    finding = DENYLIST_CASES[class_name]
    claim = vocab.never_demote_claim_class(finding)

    if class_name in vocab.NEVER_DEMOTE_SUBJECT_CLASSES:
        assert claim is None, (
            f"{class_name} reads the finding's SUBJECT, not its claim: the "
            f"HARDENING rung must not refuse a defect for being about code"
        )
    else:
        assert claim == class_name
        assert claim == vocab.never_demote_class(finding), (
            "a claim entry answers identically through both dispatchers"
        )


def test_the_claim_dispatcher_depends_on_the_catch_all_being_ordered_last() -> None:
    """fallout D-078 / D-083 — why dropping NON_COMMENT does not drop a claim.

    `never_demote_claim_class` is safe ONLY because `never_demote_class`
    returns the first match and the generic subject catch-all is evaluated
    after every claim entry, so an answer of NON_COMMENT already means no
    claim matched. Move the catch-all earlier and the narrowed dispatcher
    starts answering None for findings that DO make a security claim — the
    exact signal D-083 was filed over — so the ordering is pinned here, beside
    the function that rests on it.
    """
    order = [name for name, _ in vocab._NEVER_DEMOTE_PREDICATES]
    assert order[-1] == vocab.NON_COMMENT, (
        f"{order} — the subject catch-all must be evaluated LAST"
    )
    assert set(order[:-1]) == vocab.NEVER_DEMOTE_CLAIM_CLASSES

    # The case that proves it: a security claim whose SUBJECT is code matches
    # both entries, and the claim must survive the narrowing.
    both = {
        "description": DENYLIST_CASES[vocab.SECURITY_PROPERTY_CLAIM]["description"],
        "target_kind": "code",
    }
    assert vocab.is_non_comment(both), "the subject entry really does match too"
    assert vocab.never_demote_claim_class(both) == vocab.SECURITY_PROPERTY_CLAIM


def test_never_demote_claim_class_is_total_over_junk() -> None:
    """The house rule: a vocabulary predicate never raises across the boundary."""
    assert vocab.never_demote_claim_class({}) is None
    assert vocab.never_demote_claim_class({"description": None}) is None
    assert vocab.never_demote_claim_class({"target_kind": ["code"]}) is None


def test_never_demote_class_returns_none_for_a_plain_comment_finding() -> None:
    finding = {
        "description": OBSERVATION_CASES[vocab.PROSE_COUNT],
        "target_kind": "comment",
    }
    assert vocab.never_demote_class(finding) is None


# ---------------------------------------------------------------------------
# Precedence — the AC-002 never-weaken guarantee.
# ---------------------------------------------------------------------------


def test_denylist_outranks_observation_class() -> None:
    """AC-002 — a finding matching BOTH is a DEFECT.

    This case is genuinely ambiguous: it is phrased as a stale prose count
    (an observation class) but claims user input is unsanitized (a security
    property). The denylist wins, and it names the entry that fired so the
    caller can raise the audit tripwire against it.
    """
    finding = {
        "description": (
            "The count comment claims 3 sanitizers but only 2 exist, so user "
            "input is unsanitized."
        ),
        "target_kind": "comment",
    }
    # It really does match an observation class — otherwise this proves nothing.
    assert vocab.observation_class(finding) == vocab.PROSE_COUNT
    # And the denylist overrides it.
    assert vocab.never_demote_class(finding) == vocab.SECURITY_PROPERTY_CLAIM


def test_every_predicate_is_total_over_a_malformed_record() -> None:
    """Predicates never raise on a partial or wrong-typed record."""
    malformed = [
        {},
        {"description": None},
        {"description": 17, "spec_ref": [], "target_kind": {}},
        {"symbol_resolved": "no", "line_hint_stale": "yes"},
    ]
    for finding in malformed:
        assert vocab.observation_class(finding) is None  # type: ignore[arg-type]
        assert vocab.never_demote_class(finding) is None  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Purity — the module sits at the bottom of the import graph.
# ---------------------------------------------------------------------------


def test_vocab_imports_without_pulling_in_tools_or_the_mcp_sdk() -> None:
    """Purity rule — importing vocab must not drag in foundry_mcp.tools or
    the MCP SDK, so the stdlib-only CLIs under plugins/foundry/scripts/ can
    import it through their sys.path shim.

    Checked in a SUBPROCESS: this test module has already imported the
    orchestrator and the server, so an in-process sys.modules check would
    pass no matter what vocab does.
    """
    code = (
        "import sys\n"
        "import foundry_mcp.schemas.vocab as v\n"
        "leaked = sorted(\n"
        "    m for m in sys.modules\n"
        "    if m.startswith('foundry_mcp.tools') or m == 'mcp' "
        "or m.startswith('mcp.')\n"
        ")\n"
        "assert not leaked, leaked\n"
        "assert len(v.CANONICAL_STREAM_IDS) == 15\n"
        "print('CLEAN')\n"
    )
    env = dict(os.environ)
    env["PYTHONPATH"] = str(SRC) + os.pathsep + env.get("PYTHONPATH", "")
    proc = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        env=env,
    )
    assert proc.returncode == 0, (proc.stdout, proc.stderr)
    assert "CLEAN" in proc.stdout


# ---------------------------------------------------------------------------
# GI-003 / AC-022 — the bash twin is retired.
# ---------------------------------------------------------------------------


def test_bash_twin_deleted() -> None:
    """AC-022 (file half) — scripts/foundry.sh is gone.

    FR-013 asks that "all six copies (schema, handlers, display, markers,
    bash twin if kept) read one source of truth". That is satisfiable only
    because the twin is NOT kept: the MCP server is the single
    implementation (GI-003). The allow-list half of AC-022 is pinned by the
    commands/ casting.
    """
    assert not BASH_TWIN.exists(), (
        f"{BASH_TWIN} must be deleted — the MCP server is the single "
        "implementation (GI-003)"
    )


# ---------------------------------------------------------------------------
# D-090 / D-093 — the denylist matches the vocabulary real findings use.
#
# The pre-fix predicates enumerated specific security nouns and omitted the
# word "security" itself. A ten-case battery of textbook security-property
# claims found EIGHT demotable with the tripwire silent — the module's own
# stated-unacceptable failure mode. The suite did not catch it because
# test_observations.py exercises exactly ONE security string, hand-written to
# contain "CSRF", a literal the regex already held. These cases are therefore
# written as PROSE A STREAM WOULD ACTUALLY FILE, not as keyword probes.
# ---------------------------------------------------------------------------

# (label, description) — the OT-002 battery, verbatim from the D-093 drive.
SECURITY_CLAIM_BATTERY = [
    (
        "hmac-signature",
        "The comment above validate_token() claims the token signature is "
        "verified, but the function never checks the HMAC.",
    ),
    (
        "constant-time",
        "The comment below compare_digest says the comparison is "
        "constant-time, but it uses == so it is not.",
    ),
    (
        "rate-limit",
        "The docstring states the endpoint is rate-limited; no rate limiting "
        "exists in the handler.",
    ),
    (
        "input-validation",
        "The comment claims the payload is validated before use, but no "
        "validation runs — untrusted input reaches the shell.",
    ),
    (
        "hashed-plaintext",
        "The comment says the value is hashed before storage; it is stored "
        "in plaintext.",
    ),
    (
        "cors",
        "The comment says CORS is locked to the allowlist, but the header is "
        "set to *.",
    ),
    (
        "nonce-replay",
        "The comment promises replay protection via a nonce, but the nonce is "
        "never checked.",
    ),
    (
        "bounds-overread",
        "The comment claims the index is bounds-checked, but there is no "
        "bounds check — a buffer overread.",
    ),
    (
        "authentication",
        "The comment above the handler claims authentication is enforced, but "
        "no auth check runs.",
    ),
    (
        "password",
        "The comment says the password is salted, but it is not.",
    ),
    # D-090's own phrasings: the bare category word, and timing.
    (
        "bare-security-word",
        "The comment asserts a security property the code does not implement.",
    ),
    (
        "timing-attack",
        "The comment says the compare is safe, but it opens a timing attack.",
    ),
]


@pytest.mark.parametrize(
    "label,description", SECURITY_CLAIM_BATTERY, ids=[c[0] for c in SECURITY_CLAIM_BATTERY]
)
def test_every_realistic_security_claim_is_a_denylist_match(
    label: str, description: str
) -> None:
    """AC-002 / OT-002 — a security-property claim can NEVER be demoted.

    Asserted through ``never_demote_class`` and not only the predicate: the
    demotion decision a caller makes is the dispatcher's answer, and a claim
    that matches the predicate but loses the dispatch would still be demoted.
    """
    finding = {"description": description, "spec_ref": "", "target_kind": "comment"}
    assert vocab.is_security_property_claim(finding), (
        f"{label}: is_security_property_claim missed a textbook security claim "
        f"— {description!r}"
    )
    assert vocab.never_demote_class(finding) == vocab.SECURITY_PROPERTY_CLAIM, (
        f"{label}: the denylist did not fire, so this claim is demotable to an "
        f"observation with the audit tripwire silent (AC-002)"
    )


UNRESOLVABLE_CITE_PHRASINGS = [
    "The cite resolves to no symbol anywhere in the tree.",
    "There is no symbol named _save_json in that module.",
    "The cite names a symbol that does not exist.",
    "The symbol is missing from the file the cite names.",
    "The cited function no longer exists.",
    # Pre-existing phrasings — pinned so the widening cannot drop one.
    "The cite does not resolve.",
    "The symbol resolves to nothing.",
    "A dangling cite.",
]


@pytest.mark.parametrize("description", UNRESOLVABLE_CITE_PHRASINGS)
def test_every_unresolvable_cite_phrasing_is_a_denylist_match(
    description: str,
) -> None:
    """AC-002 / AC-006 — a cite naming nothing is a defect, however phrased.

    The pre-fix pattern covered "resolves to nothing" but none of the three
    spellings a stream actually writes, so an unresolvable cite was demotable.
    """
    finding = {"description": description, "spec_ref": "", "target_kind": "comment"}
    assert vocab.is_unresolvable_cite(finding), (
        f"is_unresolvable_cite missed {description!r}"
    )
    assert vocab.never_demote_class(finding) == vocab.UNRESOLVABLE_CITE


# Ordinary engineering prose carrying NO security or cite-resolution claim.
# Over-matching is tolerated by design (it costs one observation that stays a
# defect), but a denylist that matched everything would abolish the
# observations ledger entirely, so the widening owes a floor as well as a
# ceiling. Every string below is one a real run would file.
NON_SECURITY_PROSE = [
    "The handler returns 500 when the upstream times out.",
    "The retry loop sleeps for a fixed interval instead of backing off.",
    "This helper rebuilds the lookup dict on every call.",
    "The docstring says 8 items but there are 9 now.",
    "The comment says the helper is defined above, but it moved.",
    "The comment's list of streams omits flow_trace.",
    "The cite points at line 71 but the helper now starts at line 88.",
    "The summary prints the cycle count before the phase name.",
]


@pytest.mark.parametrize("description", NON_SECURITY_PROSE)
def test_ordinary_prose_is_not_a_security_or_cite_claim(description: str) -> None:
    """The widening has a floor: it did not degenerate into matching all prose."""
    finding = {"description": description}
    assert not vocab.is_security_property_claim(finding), (
        f"the security denylist now matches ordinary prose: {description!r}"
    )
    assert not vocab.is_unresolvable_cite(finding), (
        f"the unresolvable-cite denylist now matches ordinary prose: {description!r}"
    )


def test_the_four_observation_cases_are_still_demotable() -> None:
    """The widening must not have swept the observation ledger's own inputs.

    These are the exact strings the split exists to route to observations. If
    a widened denylist claimed one, comment-prose findings would go back to
    being defects and the whole FR-023 separation would be inert.
    """
    for class_name, description in OBSERVATION_CASES.items():
        finding = {"description": description, "target_kind": "comment"}
        assert vocab.never_demote_class(finding) is None, (
            f"the {class_name} case is no longer demotable: {description!r}"
        )
        assert vocab.observation_class(finding) == class_name


def test_a_security_term_inside_an_identifier_does_not_match() -> None:
    """The `\\b(?:...)\\b` bound is what keeps the widening honest.

    A term appearing inside a symbol name is a CITE, not a claim: "the comment
    above validate_report is stale" is line-drift prose about a function that
    happens to be named for validation, and demoting it is correct.
    """
    finding = {
        "description": (
            "The comment above validate_report cites line 40 but the writer "
            "now sits at line 52."
        ),
        "target_kind": "comment",
    }
    assert not vocab.is_security_property_claim(finding)
    assert vocab.never_demote_class(finding) is None
    assert vocab.observation_class(finding) == vocab.LINE_DRIFT_CITE


# ---------------------------------------------------------------------------
# fallout D-056 — the bare `validat*` alternation, and the floor it broke.
#
# CT-017 admits exactly ONE error on Foundry-Observation with classification
# TEMPER_CANDIDATE: "classification not in OBSERVATION_CLASSES". Two filings
# carrying a VALID classification were refused naming SECURITY_PROPERTY_CLAIM
# and the audit tripwire fired for both, because `_SECURITY_RE` carried
# `validat (?:e|es|ed|ing|ion|or)` BARE — matching the ordinary English words
# wherever they appeared, with no phrase binding, against the pattern block's
# own stated discipline.
#
# The impact is not one refused filing. AC-018 / FR-017 make TEMPER_CANDIDATE
# the ONLY channel a TEMPER-on PROVE has for an off-row probe idea, and
# GI-027 makes those candidates TEMPER's roster; this package's whole subject
# is doors that validate filings, so a probe idea about a validator could not
# be written in English without tripping the predicate.
#
# The two strings below are VERBATIM from the D-056 ledger record — the prose
# a stream actually filed — for the reason the D-090/D-093 battery gives about
# itself: a keyword probe would have been written around the pattern.
# ---------------------------------------------------------------------------

TEMPER_CANDIDATE_PROBE_PROSE = [
    (
        "names-a-validator",
        "Probe idea: the F0.9 corrupt-artifact guard judges some manifest "
        "member shapes and not others. Driven incidentally while building a "
        "harness — a manifest whose casting carries `must_haves` as a LIST "
        "rather than a mapping raises an uncaught AttributeError ('list' "
        "object has no attribute 'get') out of foundry_validate_castings, "
        "while the same call refuses cleanly with a named corrupt_artifacts "
        "entry when `waves[0]` is a list instead of an object. Worth a "
        "systematic pass: enumerate every manifest member the validator "
        "dereferences by key and check which ones the shape guard actually "
        "covers, since the guard's whole promise is that a document it had "
        "to guess at is refused rather than acted on.",
    ),
    (
        "says-the-fields-are-validated",
        "Probe idea: the two provenance fields on a defect record are "
        "validated asymmetrically, deliberately and with a documented "
        "rationale. One is refused at both doors when it names an id the "
        "ledger does not carry; the sibling field is accepted and persisted "
        "verbatim in the same case, and also when it names a record of the "
        "wrong tier, with the door reporting that nothing was closed. "
        "Driven: both doors accepted the dangling value and stored it on the "
        "record. The abstention is defensible against the contract's errors "
        "column, so this is not filed as a verdict — the probe worth driving "
        "is downstream: sweep every reader of that field in the report "
        "generator and in the measurement script and check whether any of "
        "them counts a stored value as evidence a promotion happened, rather "
        "than reading the closed record's own status.",
    ),
]


@pytest.mark.parametrize(
    "label,description",
    TEMPER_CANDIDATE_PROBE_PROSE,
    ids=[c[0] for c in TEMPER_CANDIDATE_PROBE_PROSE],
)
def test_a_probe_idea_about_a_validator_is_not_a_security_claim(
    label: str, description: str
) -> None:
    """fallout D-056 — the bare `validat*` alternation is gone.

    Both strings were refused by `is_security_property_claim` before the
    narrowing: `_SECURITY_RE.search` returned 'validator' at offset 509 for
    the first and 'validated' at offset 61 for the second, and each filing
    came back naming SECURITY_PROPERTY_CLAIM with the tripwire fired. Neither
    asserts a security property at all; each names a validator the way any
    engineering prose in this package names one.

    Asserted through the SECURITY predicate specifically, and not through
    `never_demote_class` for both, because the first string also carries the
    literal `must_haves` — a deliberate member of `_SPEC_CLAIM_RE`, since a
    finding that a casting's must_haves are unmet IS a spec-required-behaviour
    claim. That is a different predicate with a different rationale, filed
    separately in concerns.md and NOT narrowed here; this test is about the
    security entry and says so rather than asserting a floor it does not own.
    """
    finding = {"description": description}
    assert not vocab.is_security_property_claim(finding), (
        f"{label}: the security denylist still matches a probe idea that "
        f"asserts no security property — {description[:80]!r}..."
    )
    assert not vocab.is_security_property_text(description), label
    assert vocab.never_demote_class(finding) != vocab.SECURITY_PROPERTY_CLAIM, (
        f"{label}: the dispatcher still answers SECURITY_PROPERTY_CLAIM, so "
        f"the refusal and the audit tripwire both still name it (AC-007)"
    )


def test_the_probe_naming_only_a_validator_is_demotable_end_to_end() -> None:
    """fallout D-056 / CT-017 — the whole point: the candidate is ACCEPTED.

    The second string carries no spec vocabulary of any kind, so once the
    security entry stops matching, `never_demote_class` returns None and the
    observation door has nothing left to refuse on — which is what AC-018
    needs, TEMPER_CANDIDATE being the only channel an off-row probe idea has.
    """
    description = TEMPER_CANDIDATE_PROBE_PROSE[1][1]
    assert vocab.never_demote_class({"description": description}) is None


GENUINE_VALIDATION_CLAIMS = [
    (
        "input-validation-phrase",
        "The handler's input validation is claimed in the docstring and "
        "absent from the code.",
    ),
    (
        "unvalidated",
        "The unvalidated path segment reaches open() directly.",
    ),
    (
        "certificate-verb-form",
        "The comment says the certificate is validated, but the chain is "
        "never checked.",
    ),
    (
        "jwt-verb-form",
        "The docstring claims the JWT is validated before use; nothing "
        "validates it.",
    ),
]


@pytest.mark.parametrize(
    "label,description",
    GENUINE_VALIDATION_CLAIMS,
    ids=[c[0] for c in GENUINE_VALIDATION_CLAIMS],
)
def test_a_genuine_validation_security_claim_is_still_refused(
    label: str, description: str
) -> None:
    """fallout D-056's ceiling — GI-004 is not traded for the floor.

    Narrowing a never-demote pattern is the direction the module calls its own
    unacceptable failure mode, so the removal owes this list as much as it
    owes the two probe strings. Each case is a claim that a validation
    property is broken, and each reaches the denylist through a PHRASE:
    `input validation` and `unvalidated` were already members on the line
    above the bare form, and the credential-document verb form
    (`certificate`/`JWT` ... `validated`) is the one sense the bare
    alternation carried that no other member did, kept as a bounded phrase.
    """
    finding = {"description": description}
    assert vocab.is_security_property_claim(finding), (
        f"{label}: the narrowing dropped a genuine security claim — "
        f"{description!r}"
    )
    assert vocab.never_demote_class(finding) == vocab.SECURITY_PROPERTY_CLAIM, label


# ---------------------------------------------------------------------------
# D-083 — the audit record names the class the refusal names (AC-007 / OT-005
# / CT-003).
#
# `record_denylist_tripwire` does not receive the refusal's class; it RE-DERIVES
# one from `never_demote_class`. So the two artifacts one filing event writes —
# the refusal handed back to the stream and the tripwire persisted in
# observations.json — agree only if the dispatcher returns the same entry the
# LATENT denylist rung refused on. With NON_COMMENT leading the tuple they did
# not: `target_kind` "code" and "test" recorded NON_COMMENT under a refusal
# naming SECURITY_PROPERTY_CLAIM, and those two are the DEFAULT shape of a
# production-code filing, so the security signal was lost for exactly the
# filings AC-007 is about.
#
# Driven here across all four subject declarations, on BOTH shipped doors,
# because a fix that held on one door and not the other is the same defect one
# door along.
# ---------------------------------------------------------------------------

#: One description asserting an authentication property. Identical across every
#: case below — only the SUBJECT declaration moves, which is what makes the
#: subject the sole variable under test.
_AUTH_CLAIM = (
    "The session endpoint accepts the bearer token without verifying its "
    "signature, so authentication is never enforced on the route."
)

#: Every subject a stream declares. `None` means the field is not passed at
#: all, which is what a caller does by default — the field is optional in the
#: advertised schema.
_SUBJECT_DECLARATIONS = ("code", "test", "comment", None)
_SUBJECT_IDS = ("code", "test", "comment", "absent")


@pytest.fixture
def run_env(tmp_path, monkeypatch):
    """Activate a foundry run under tmp_path; yield (project_root, fdir).

    The synthetic-run-directory shape tests/test_escalation.py established and
    every door-level test in this suite is built on. Only what the two filing
    doors read is written: a state.json for the server-side cycle, and the
    castings/ directory the run layout expects.
    """
    # GI-010 / GI-026 — the wave-2 destination. Casting 2's split defines
    # this symbol in the module named below and its completion report's
    # `## Symbol map` is the authority; where the map and this differ, the
    # map wins and this is the edit.
    from foundry_mcp.tools import foundry_state
    from foundry_mcp.tools.orchestration import teams as _teams

    run_name = "vocab-denylist-run"
    fdir = tmp_path / "foundry-archive" / run_name
    (fdir / "castings").mkdir(parents=True, exist_ok=True)
    (fdir / "state.json").write_text(
        json.dumps({"phase": "F2", "cycle": 3}), encoding="utf-8"
    )

    monkeypatch.setattr(
        _teams,
        "_check_active_teams",
        lambda _pr: {"active": False, "teams": [], "live_panes": []},
    )

    foundry_state.set_active_run(run_name)
    try:
        yield str(tmp_path), fdir
    finally:
        foundry_state.clear_active_run()


def _fired_classes(fdir: Path) -> list[str]:
    """The denylist_class of every tripwire record persisted so far."""
    ledger = json.loads((fdir / "observations.json").read_text(encoding="utf-8"))
    return [t["denylist_class"] for t in ledger["tripwire"]]


def test_the_generic_catch_all_is_evaluated_last() -> None:
    """D-083 — the declaration ORDER is the audit answer, so it is pinned.

    `never_demote_class` returns the first match. Which entry that is decides
    what `observations.json.tripwire[].denylist_class` says, so the order is a
    contract, not a reading convenience.
    """
    order = [name for name, _ in vocab._NEVER_DEMOTE_PREDICATES]

    assert set(order) == set(vocab.NEVER_DEMOTE_CLASSES), (
        f"the dispatcher and the frozenset disagree about the roster: "
        f"{sorted(order)} vs {sorted(vocab.NEVER_DEMOTE_CLASSES)}"
    )
    assert order[0] == vocab.SECURITY_PROPERTY_CLAIM, (
        f"{order[0]} is evaluated before {vocab.SECURITY_PROPERTY_CLAIM}. The "
        f"security entry is the ONE a refusal also names — the LATENT rung in "
        f"validate_defect_filing — and the tripwire may not disagree with the "
        f"refusal it was fired for."
    )
    assert order[-1] == vocab.NON_COMMENT, (
        f"{order[-1]} is evaluated after {vocab.NON_COMMENT}. is_non_comment "
        f"matches ANY declared non-comment target_kind, so anything behind it "
        f"is unreachable for a filing that named its subject — which is every "
        f"production-code filing."
    )


@pytest.mark.parametrize(
    "target_kind", _SUBJECT_DECLARATIONS, ids=_SUBJECT_IDS
)
def test_a_security_claim_outranks_the_non_comment_catch_all(
    target_kind: str | None,
) -> None:
    """The dispatcher, isolated from the doors: the subject never masks the
    claim. All four declarations answer SECURITY_PROPERTY_CLAIM."""
    finding: dict[str, object] = {"description": _AUTH_CLAIM}
    if target_kind is not None:
        finding["target_kind"] = target_kind

    assert vocab.never_demote_class(finding) == vocab.SECURITY_PROPERTY_CLAIM, (
        f"target_kind={target_kind!r} masked the security entry, so the audit "
        f"record for this filing names the subject instead of the claim"
    )


@pytest.mark.parametrize(
    "target_kind", _SUBJECT_DECLARATIONS, ids=_SUBJECT_IDS
)
def test_the_defect_door_audits_under_the_class_it_refuses(
    run_env, target_kind: str | None
) -> None:
    """Foundry-Defect: the refusal's class and the persisted tripwire's class
    are ONE value, whatever subject the filer declared."""
    from foundry_mcp import server as foundry_server

    project_root, fdir = run_env
    args: dict[str, object] = {
        "cycle": 3,
        "source": "prove",
        "defect_type": "WRONG",
        "description": _AUTH_CLAIM,
        "defect_class": "AUTH_NOT_ENFORCED",
        "tier": "LATENT",
        "reproduction_attempted": "AST sweep of both roots finds 0 sites",
    }
    if target_kind is not None:
        args["target_kind"] = target_kind

    previous_root = foundry_server._project_root
    try:
        foundry_server._project_root = project_root
        result = foundry_server._DISPATCH["Foundry-Defect"](args)
    finally:
        foundry_server._project_root = previous_root

    assert result["denylist_class"] == vocab.SECURITY_PROPERTY_CLAIM, result
    assert _fired_classes(fdir) == [result["denylist_class"]], (
        f"one filing event wrote two artifacts that disagree: the refusal says "
        f"{result['denylist_class']} and the tripwire says "
        f"{_fired_classes(fdir)}. An auditor querying observations.json for "
        f"{vocab.SECURITY_PROPERTY_CLAIM} finds nothing for this filing."
    )


@pytest.mark.parametrize(
    "target_kind", _SUBJECT_DECLARATIONS, ids=_SUBJECT_IDS
)
def test_the_sync_door_audits_under_the_class_it_refuses(
    run_env, target_kind: str | None
) -> None:
    """Foundry-Sync, the door a whole INSPECT stream files through. Same
    invariant, same four declarations — the two doors cannot be trusted to
    remember an audit class each."""
    from foundry_mcp import server as foundry_server

    project_root, fdir = run_env
    finding: dict[str, object] = {
        "source": "prove",
        "type": "WRONG",
        "description": _AUTH_CLAIM,
        "class": "AUTH_NOT_ENFORCED",
        "tier": "LATENT",
        "reproduction_attempted": "AST sweep of both roots finds 0 sites",
    }
    if target_kind is not None:
        finding["target_kind"] = target_kind

    previous_root = foundry_server._project_root
    try:
        foundry_server._project_root = project_root
        result = foundry_server._DISPATCH["Foundry-Sync"](
            {"cycle": 3, "findings": [finding]}
        )
    finally:
        foundry_server._project_root = previous_root

    refused = result["refusals"][0]
    assert refused["denylist_class"] == vocab.SECURITY_PROPERTY_CLAIM, result
    assert _fired_classes(fdir) == [refused["denylist_class"]], (
        f"the batch door's refusal says {refused['denylist_class']} and its "
        f"tripwire says {_fired_classes(fdir)}"
    )


# ---------------------------------------------------------------------------
# D-091 — a defect `source` is resolved against the SOURCE vocabulary.
# ---------------------------------------------------------------------------


def test_canonical_defect_source_is_total_over_the_defect_source_vocabulary() -> None:
    """Every value Foundry-Defect accepts as a `source` must resolve.

    ``canonical_stream_id`` is the wrong resolver for this field: it knows
    only the nine stream wire ids, so `assay` and `temper` — both members of
    DEFECT_SOURCE_IDS and both carried by server.py's live `source` enum —
    resolved to None. measure-run.py then discarded those records and reported
    them as PHASE9_UNKNOWN_STREAM.
    """
    for source in vocab.DEFECT_SOURCE_IDS:
        assert vocab.canonical_defect_source(source) is not None, (
            f"{source!r} is a legal defect source that does not resolve"
        )
    assert vocab.canonical_defect_source("assay") == "ASSAY"
    assert vocab.canonical_defect_source("temper") == "TEMPER"


def test_the_two_non_stream_filers_are_not_stream_ids() -> None:
    """ASSAY and TEMPER file defects but never file stream coverage.

    Their absence from the 15-id roster is the correct modelling, which is why
    the repair is a sibling table rather than an extension of the stream one —
    widening CANONICAL_STREAM_IDS would have made the roll-up artifact accept
    coverage records from two things that never produce them.
    """
    assert vocab.NON_STREAM_DEFECT_SOURCES == frozenset({"assay", "temper"})
    assert not vocab.NON_STREAM_DEFECT_SOURCES & vocab.STREAM_WIRE_IDS
    for wire in vocab.NON_STREAM_DEFECT_SOURCES:
        assert vocab.canonical_stream_id(wire) is None, (
            f"{wire!r} must not resolve as a stream — it files no coverage"
        )
        assert vocab.canonical_defect_source(wire) not in vocab.CANONICAL_STREAM_IDS


def test_canonical_defect_source_narrows_nothing_canonical_stream_id_accepts() -> None:
    """NFR-002 — the new resolver is a strict superset of the old one.

    Only nine canonical stream ids have a wire spelling, so deriving the
    identity set from the mapping's values would have REJECTED the six that do
    not (EVID-01, EVID-02, INTV-01, TYPE-01, TYPE-02, INTENT-01) even though a
    real archive records them verbatim. Caught by the suite when it did.
    """
    for value in vocab.CANONICAL_STREAM_IDS | vocab.STREAM_WIRE_IDS:
        assert (
            vocab.canonical_defect_source(value) == vocab.canonical_stream_id(value)
        ), f"{value!r} resolves differently through the two resolvers"


def test_canonical_defect_source_never_coerces_an_unknown_value() -> None:
    for unknown in ("bogus", "TRACE-99", "", "Assay", "sight ", 17, None):
        assert vocab.canonical_defect_source(unknown) is None, unknown  # type: ignore[arg-type]


def test_defect_source_mapping_is_total_and_lands_in_known_names() -> None:
    """The mapping's contract, mirroring WIRE_TO_CANONICAL's own pin."""
    assert set(vocab.DEFECT_SOURCE_TO_CANONICAL) == set(vocab.DEFECT_SOURCE_IDS)
    for wire, canonical in vocab.DEFECT_SOURCE_TO_CANONICAL.items():
        if wire in vocab.STREAM_WIRE_IDS:
            assert canonical in vocab.CANONICAL_STREAM_IDS
            assert canonical == vocab.WIRE_TO_CANONICAL[wire]
        else:
            assert canonical == wire.upper()


# ---------------------------------------------------------------------------
# The daring-orca Shared Design Contract, section C-1.
#
# Castings 2, 3, 4, 5, 6 and 7 build in parallel against these names and import
# them from here. A name renamed in vocab.py is a name a parallel wave cannot
# find, and it fails at IMPORT time in another agent's worktree — the one place
# the failure is hardest to attribute. So the roster is spelled out ONCE, here,
# with the kind each name must have, and every member is asserted.
#
# This is deliberately a re-typed copy rather than a derivation: deriving it
# from `dir(vocab)` would pass no matter what the module exports, which is the
# same vacuity `assert shipped.enum == vocab.ENUM` has in
# tests/test_findings_schemas.py. The transcription IS the pin.
# ---------------------------------------------------------------------------

#: (name, kind) for every export the C-1 contract names. `kind` is what the
#: contract spells: a frozenset stays a frozenset, a tuple stays ordered, and
#: a dict stays JSON-serializable (the report generator json.dumps it).
C1_CONTRACT_EXPORTS = (
    ("DEFECT_TIERS", frozenset),
    ("TIER_UNKNOWN", str),
    ("DEFECT_TIER_OR_UNKNOWN", frozenset),
    ("ESCALATION_STATUSES", frozenset),
    ("ESCALATION_EXIT_REASONS", frozenset),
    ("STRUCTURAL_PASS_BUDGET", int),
    ("LIVE_CLEAN_CYCLES_TO_CLEAR", int),
    ("INSPECT_MODES", frozenset),
    ("INSPECT_FULL_RULES", frozenset),
    ("INSPECT_DELTA_RULE", str),
    ("FULL_ROSTER_STREAMS", tuple),
    ("DELTA_CONDITIONAL_STREAMS", frozenset),
    ("PROVE_DELTA_SAMPLE_SIZE", int),
    ("VERIFIER_PATH_PATTERNS", tuple),
    ("is_verifier_path", "callable"),
    ("LEAD_LANE_MAX_FILES", int),
    ("LEAD_LANE_MAX_LINES", int),
    ("FIX_AUTHORS", frozenset),
    ("is_test_file", "callable"),
    ("HANDOFF_EVENT_LEAD_FIX", str),
    ("PHASE_LADDER", tuple),
    ("PHASE_NAMES", dict),
    ("RUN_PHASE_HALTED", str),
    ("SPEND_LEDGER_FILENAME", str),
    ("REPORT_MD_FILENAME", str),
    ("REPORT_JSON_FILENAME", str),
    ("REPORT_REQUIRED_SECTIONS", tuple),
    ("THUNDER_VIPER_BASELINE", dict),
    ("CONVERGENCE_TARGET", dict),
    ("is_security_property_text", "callable"),
    ("reproduction_attempted_problem", "callable"),
)


@pytest.mark.parametrize(
    "name,kind", C1_CONTRACT_EXPORTS, ids=[n for n, _ in C1_CONTRACT_EXPORTS]
)
def test_the_c1_contract_name_exists_with_its_contracted_kind(name, kind) -> None:
    """One assertion per C-1 export — the cross-casting import surface."""
    assert hasattr(vocab, name), (
        f"schemas/vocab.py no longer exports {name!r}. Castings 2-7 import it "
        f"by that spelling; renaming it breaks their imports, not this test's "
        f"assertion. Restore the name (an alias is fine) rather than editing "
        f"this roster."
    )
    value = getattr(vocab, name)
    if kind == "callable":
        assert callable(value), f"{name} must be callable"
    else:
        assert isinstance(value, kind), (
            f"{name} is {type(value).__name__}, but the C-1 contract spells it "
            f"{kind.__name__}"
        )


def test_the_two_comparison_dicts_are_json_serializable() -> None:
    """C-10 — the report generator json.dumps `baseline_comparison` wholesale.

    Spelled as plain dicts rather than MappingProxyType, against this module's
    usual immutability habit, because a mapping proxy raises TypeError inside
    json.dumps and casting 5's report.json carries both of these verbatim. The
    contents pin below is what replaces the immutability.
    """
    import json

    payload = json.dumps(
        {"baseline": vocab.THUNDER_VIPER_BASELINE, "target": vocab.CONVERGENCE_TARGET}
    )
    assert json.loads(payload)["baseline"]["grind_cycles"] == 22


# ---------------------------------------------------------------------------
# GI-001 / CT-001 / FR-004 — the evidence tier.
# ---------------------------------------------------------------------------


def test_defect_tiers_is_the_three_member_closed_vocabulary() -> None:
    """FR-014 / GI-014 / AC-022 — HARDENING is the third member."""
    assert vocab.DEFECT_TIERS == frozenset({"LIVE", "LATENT", "HARDENING"})
    assert isinstance(vocab.DEFECT_TIERS, frozenset)
    assert vocab.TIER_HARDENING == "HARDENING"
    assert vocab.TIER_HARDENING in vocab.DEFECT_TIERS


def test_hardening_is_a_defect_tier_and_is_not_in_the_blocking_set() -> None:
    """AC-022 — the whole of HARDENING's gate semantics, stated as an assertion.

    `BLOCKING_TIERS` is a literal tuple in the gate module, so adding
    the member changes nothing there and the CORRECT outcome is that nothing
    changed. That is exactly what makes this pin worth having: the day someone
    derives `BLOCKING_TIERS` from `DEFECT_TIERS` — which reads like a tidy-up
    and is the obvious next refactor — every gate starts blocking on the
    non-blocking tier and this fails first.

    AC-022 also names the four doors that must pass with open HARDENING
    defects; those are driven at the doors themselves (casting 4's file). What
    is asserted here is the vocabulary fact the doors read.

    fallout GI-033 / D-021 / D-035 (concern C-027) — READ FROM `vocab` NOW.
    This used to import the tuple from `orchestration/gates.py`, which is where
    it happened to be declared. GI-014's applies-to column had always named it
    beside DEFECT_TIERS here, and the layering guard forced the move: gates is
    a VERIFIER module and the status display needed the same membership from
    the LIFECYCLE layer, so a symbol both layers read could live in neither.
    """
    BLOCKING_TIERS = vocab.BLOCKING_TIERS

    assert vocab.TIER_HARDENING not in BLOCKING_TIERS, (
        "HARDENING is a driven failure that no requirement asks about "
        "(GI-014): it is REPORTED in its own backlog and blocks no gate. A "
        "blocking HARDENING tier holds a run shut over behaviour nobody "
        "specified, which is how a run learns to stop driving probes."
    )
    assert set(BLOCKING_TIERS) == {"LIVE", vocab.TIER_UNKNOWN}, (
        f"{BLOCKING_TIERS} — LIVE is a reachable failure and unknown is a "
        f"record nobody classified. Neither LATENT nor HARDENING joins them."
    )


def test_blocking_tiers_is_an_ordered_pair_derived_from_the_sentinel() -> None:
    """fallout GI-033 / CT-008 (concern C-027) — the tuple's own shape.

    Hand-built, and deliberately NOT compared against the copy in
    `orchestration/gates.py`: casting 2 deletes that copy and repoints in this
    same wave, so a parity assertion would be comparing an expression to itself
    and then, a commit later, to nothing.

    A TUPLE, and ordered. A refusal reads the members out in this order ("LIVE,
    then unknown"), so the container is not incidental. Membership is the only
    test any consumer applies, which is why nothing else rests on it.

    `TIER_UNKNOWN` is the second member BY DERIVATION, not by a second spelling
    of "unknown" — the sentinel has exactly one spelling and this is a reader
    of it, so renaming the sentinel cannot leave this tuple pointing at a value
    no reader resolves to.
    """
    assert vocab.BLOCKING_TIERS == ("LIVE", "unknown")
    assert isinstance(vocab.BLOCKING_TIERS, tuple)
    assert vocab.BLOCKING_TIERS[1] is vocab.TIER_UNKNOWN
    assert len(vocab.BLOCKING_TIERS) == 2

    # The two members that are DEFECT_TIERS members, and the two that are not.
    assert set(vocab.BLOCKING_TIERS) & vocab.DEFECT_TIERS == {"LIVE"}
    assert set(vocab.DEFECT_TIERS) - set(vocab.BLOCKING_TIERS) == {
        "LATENT", "HARDENING",
    }
    # Every member is something a reader can actually see (FR-051).
    assert set(vocab.BLOCKING_TIERS) <= vocab.DEFECT_TIER_OR_UNKNOWN


def test_defect_tier_reads_a_hardening_record_as_hardening() -> None:
    """The read side of the same member: no coercion, in either direction."""
    assert vocab.defect_tier({"tier": "HARDENING"}) == "HARDENING"
    assert vocab.defect_tier({"tier": "hardening"}) == vocab.TIER_UNKNOWN
    assert vocab.TIER_HARDENING in vocab.DEFECT_TIER_OR_UNKNOWN


def test_the_wire_enums_widen_by_derivation_and_never_by_a_second_list() -> None:
    """GI-014 — `sorted(DEFECT_TIERS)` is what the three wire surfaces spell.

    `server.py`'s two Foundry-Defect / Foundry-Sync input schemas and
    `schemas/findings.py` all derive their `tier` enum from the constant, so
    the member added above reaches the wire with no edit of theirs. A surface
    that re-typed the two old names would have silently refused every HARDENING
    filing while the vocabulary declared it legal — the exact seventh-copy
    shape D-071 filed.
    """
    from foundry_mcp.schemas import findings

    schema_tier = _foundry_defect_schema()["tier"]
    assert schema_tier["enum"] == sorted(vocab.DEFECT_TIERS)
    assert vocab.TIER_HARDENING in schema_tier["enum"]

    finding_tier = findings._FINDING_ITEM["properties"]["tier"]
    assert finding_tier["enum"] == sorted(vocab.DEFECT_TIERS)
    assert vocab.TIER_HARDENING in finding_tier["enum"]


def test_tier_unknown_is_a_read_sentinel_and_not_a_writable_tier() -> None:
    """FR-051 — a door may never WRITE unknown; a reader must always see it.

    Enrolling the sentinel in DEFECT_TIERS would make it a value a filing
    stream could legally set, which is precisely the "I did not classify this"
    escape the tier exists to close.
    """
    assert vocab.TIER_UNKNOWN == "unknown"
    assert vocab.TIER_UNKNOWN not in vocab.DEFECT_TIERS
    assert vocab.DEFECT_TIER_OR_UNKNOWN == vocab.DEFECT_TIERS | {vocab.TIER_UNKNOWN}
    assert len(vocab.DEFECT_TIER_OR_UNKNOWN) == 4


@pytest.mark.parametrize(
    "record,expected",
    [
        ({}, "unknown"),
        ({"tier": None}, "unknown"),
        ({"tier": ""}, "unknown"),
        ({"tier": "latent"}, "unknown"),      # case matters; not coerced
        ({"tier": "MINOR"}, "unknown"),       # a grade is not a tier
        ({"tier": 1}, "unknown"),
        ({"tier": "LIVE"}, "LIVE"),
        ({"tier": "LATENT"}, "LATENT"),
    ],
)
def test_defect_tier_reads_anything_unclassified_as_unknown(record, expected) -> None:
    """FR-051 — never LATENT by default, and never coerced onto a member.

    The LATENT half is the load-bearing one: LATENT blocks NO gate — FR-006
    passes INSPECT-clean, ASSAY, TEMPER, NYQUIST and DONE alike on a
    LATENT-only backlog — so a record that defaults to LATENT silently clears
    every one of them on a defect nobody ever classified.
    """
    assert vocab.defect_tier(record) == expected
    assert vocab.defect_tier(record) in vocab.DEFECT_TIER_OR_UNKNOWN


# ---------------------------------------------------------------------------
# D-148 — the tier's GATE RULE, pinned where the tier is defined.
#
# The header above `DEFECT_TIERS` stated a rule the requirements forbid and the
# shipped gates never implemented: that the axis chose WHICH door a still-open
# instance blocks, ASSAY blocking on either tier while TEMPER, NYQUIST and DONE
# blocked on LIVE alone. Driven through `foundry_gate(phase="assay")` on a run
# whose only open defect was LATENT: passed=True, reason=None. FR-006 is one
# sentence with no exception in it — "INSPECT-clean, ASSAY, TEMPER, NYQUIST and
# DONE all pass when the only open defects are LATENT" — and CT-008 says the
# same, so the header was inventing a fifth requirement in the one file a
# maintainer reads to learn what the tier means.
#
# WHY A PROSE PIN AND NOT ONLY A BEHAVIOUR TEST. The gates were already correct
# and already tested; what failed was the DESCRIPTION of them, which no
# behaviour test can reach. This class — stale prose surviving beside new prose
# — has now been filed for five consecutive cycles, and the instance fix is
# what kept failing. So the rule is asserted as text: the header must state the
# requirements' rule, and no casting-1 surface may restate it with a door
# missing.
# ---------------------------------------------------------------------------

#: The five doors FR-006 names. A statement of the tier's gate rule that omits
#: any one of them is the D-148 undercount, whatever else it gets right.
_TIER_AWARE_DOORS = ("INSPECT-clean", "ASSAY", "TEMPER", "NYQUIST", "DONE")

#: The retired spellings, verbatim as they shipped. Each asserted ABSENT below;
#: naming a door subset is what every one of them has in common.
_RETIRED_TIER_GATE_SPELLINGS = (
    "ASSAY blocks on either",   # D-148, vocab.py's own header
    "block on LIVE alone",      # D-148, the same sentence's second half
    "stops blocking at TEMPER",  # D-148, the four sibling restatements
    "clears three gates",       # D-148, the undercount: FR-006 names five
    "clear three gates",        # D-148, same undercount, other conjugation
    "three gates pass",         # D-148, same undercount, measure-run's phrasing
)

#: Every casting-1 key_file whose prose could restate the rule, EXCEPT this
#: module — the roster above spells all six retired sentences on purpose, so a
#: scan including this file would report itself. That is the only exclusion,
#: and it is one file wide.
_OWNED_PROSE_SURFACES = (
    "plugins/foundry/mcp-server/src/foundry_mcp/schemas/vocab.py",
    "plugins/foundry/mcp-server/src/foundry_mcp/schemas/findings.py",
    "plugins/foundry/mcp-server/tests/test_findings_schemas.py",
    "plugins/foundry/scripts/migrate-archive.py",
    "plugins/foundry/scripts/measure-run.py",
    "plugins/foundry/mcp-server/tests/test_migrate_archive.py",
    "plugins/foundry/mcp-server/tests/test_measure_run.py",
)


def _tier_header() -> str:
    """The comment block above `DEFECT_TIERS` in schemas/vocab.py.

    Sliced from the source text rather than from a docstring because the block
    IS a `#` comment — the thing D-148 was filed against — and a docstring
    rewrite would leave the filed surface unread by this assertion.
    """
    text = Path(vocab.__file__).read_text(encoding="utf-8")
    start = text.index("# The evidence tier (GI-001")
    end = text.index("DEFECT_TIERS = frozenset", start)
    return text[start:end]


def test_the_tier_header_states_the_gate_rule_the_requirements_give() -> None:
    """FR-006 / CT-008 verbatim: 'INSPECT-clean, ASSAY, TEMPER, NYQUIST and
    DONE all pass when the only open defects are LATENT.'

    The header must say that, name every door FR-006 names, and say that the
    unknown sentinel blocks — the half FR-051 adds. A header that describes the
    axis without describing what it does to a run is what let the wrong rule
    sit there unchallenged.
    """
    header = _tier_header()
    for door in _TIER_AWARE_DOORS:
        assert door in header, (
            f"the DEFECT_TIERS header does not name {door}, which FR-006 lists "
            f"among the doors a LATENT-only backlog passes. Naming a subset is "
            f"exactly the D-148 defect."
        )
    assert "only open defects are LATENT" in header, (
        "the header no longer quotes FR-006's own rule. State it in the "
        "requirement's words; a paraphrase is what drifted last time."
    )
    assert "unknown-tier" in header, (
        "FR-051's half is missing: an untiered record blocks like LIVE, so a "
        "header promising that only LIVE blocks is wrong in the other "
        "direction."
    )


@pytest.mark.parametrize("relpath", _OWNED_PROSE_SURFACES)
def test_no_casting_1_surface_names_the_retired_per_gate_tier_rule(
    relpath: str,
) -> None:
    """No owned file restates the tier's gate rule with a door missing.

    Six surfaces carried the retired rule (D-148): `vocab.py`'s header, and
    five restatements that named three of the five doors and called them
    "three gates". Only the first was filed; the other five are the same
    sentence, and leaving them is how this class came back four times.
    """
    text = (REPO_ROOT / relpath).read_text(encoding="utf-8")
    hits = [s for s in _RETIRED_TIER_GATE_SPELLINGS if s in text]
    assert not hits, (
        f"{relpath} names the retired per-gate tier rule: {hits}. FR-006 gives "
        f"one rule for all five doors — INSPECT-clean, ASSAY, TEMPER, NYQUIST "
        f"and DONE all pass when the only open defects are LATENT. Restate it "
        f"in full or do not restate it; a subset reads as a live exception."
    )


def test_the_retired_tier_gate_pin_actually_fires() -> None:
    """A pin that cannot fail is a comment with an assert in it.

    Each retired spelling is driven through the same containment check the scan
    above uses, in a sentence shaped like the ones that shipped, and must be
    caught alone — so a later edit that narrows a pattern into uselessness, or
    lets two patterns shadow each other, fails here rather than going quiet.
    """
    for spelling in _RETIRED_TIER_GATE_SPELLINGS:
        sample = f"the tier decides which gate blocks: LATENT {spelling} today"
        hits = [s for s in _RETIRED_TIER_GATE_SPELLINGS if s in sample]
        assert hits == [spelling], (
            f"{spelling!r} was not caught alone in {sample!r}; got {hits}"
        )


# ---------------------------------------------------------------------------
# fallout AC-022 / NFR-011 — EVERY count comment in this module is DERIVED,
# not remembered.
#
# The scan above catches one retired SENTENCE. It could not catch what the
# fallout cycle-5 filings were actually made of: `commands/start.md`'s tier
# briefing (D-162), `skills/prove/SKILL.md` and `skills/trace/SKILL.md`'s
# Structured Output Format paragraphs (D-163, D-164) each described
# `DEFECT_TIERS` as TWO members and closed "Both tiers are defects and both
# get fixed", months after HARDENING joined the frozenset. Three surfaces, one
# failure: prose that COUNTS a vocabulary, written once and never re-derived.
#
# `vocab.py` is the constant those surfaces drifted from, and it states the
# same kind of count about itself thirty-three times — the `# N items` comment
# closing each declaration, which the module's own `PROSE_COUNT` observation
# class exists to name ("The trailing count comment claims 15 members but the
# frozenset now holds 16"). Declaring that class and then leaving every
# instance of it in the declaring file unpinned is the gap this closes.
#
# THE PIN IS DERIVED, WHICH IS THE POINT (NFR-011, top convention 2). It
# resolves each comment to the constant it annotates and asks `len()`. There is
# no roster of expected counts here to fall behind the module a second time —
# adding a member to any vocabulary and forgetting its comment fails HERE, at
# the source of truth, rather than at whichever consumer's prose is read next.
# ---------------------------------------------------------------------------

#: A declaration's closing count comment: `)  # 16 sections`, `= frozenset(
#: {...})  # 3 items`. The noun is free text because this module already uses
#: eight of them (items, members, sections, patterns, phases, families, glob,
#: path) and which noun fits a vocabulary is an authoring choice, not a rule.
_COUNT_COMMENT = re.compile(r"#\s*(\d+)\s+([a-z][a-z-]*(?:\s+[a-z][a-z-]*)*)\s*$")

#: A top-level constant binding, public or module-private. Matched at column
#: zero so an annotated assignment inside a function body cannot claim a count
#: comment that belongs to the declaration around it.
_TOP_LEVEL_BINDING = re.compile(r"^(_?[A-Z][A-Z0-9_]*)\s*(?::[^=]+)?=")

#: The floor. A pin over "every match" passes vacuously the moment the matches
#: stop being found — a regex narrowed by a later edit, a reformat that moves
#: every comment onto its own line — and a green vacuous pin is worse than no
#: pin, because it reads as coverage. Thirty-three resolve today; the floor is
#: set below that so adding or removing one declaration is not a failure, while
#: losing the whole scan is.
_MIN_COUNT_COMMENTS = 25


def _count_comments() -> list[tuple[int, str, int, str]]:
    """Every `# N <noun>` count comment in vocab.py, with what it annotates.

    Returns ``[(lineno, constant_name, claimed_count, noun)]`` for each trailing
    count comment that resolves to a top-level binding of this module carrying a
    length. A comment on a line the walk-back cannot resolve to such a binding is
    returned with an empty name, so the caller REPORTS it rather than skipping
    it quietly — an unresolvable comment is the one shape this pin must not
    treat as a pass.

    Read from the source TEXT, like `_tier_header` above and for the same
    reason: the thing under test is a `#` comment, which no import can reach.
    """
    lines = Path(vocab.__file__).read_text(encoding="utf-8").splitlines()
    out: list[tuple[int, str, int, str]] = []
    for index, line in enumerate(lines):
        # A whole-line comment is prose, not a declaration's closing count —
        # including this module's own explanatory blocks, which spell numbers
        # in sentences all the time.
        if line.lstrip().startswith("#"):
            continue
        match = _COUNT_COMMENT.search(line)
        if match is None:
            continue
        name = ""
        for back in range(index, max(-1, index - 80), -1):
            binding = _TOP_LEVEL_BINDING.match(lines[back])
            if binding is not None:
                name = binding.group(1)
                break
        out.append((index + 1, name, int(match.group(1)), match.group(2)))
    return out


def test_every_count_comment_in_vocab_matches_its_constant() -> None:
    """fallout AC-022 / NFR-011 — the module never miscounts itself.

    `DEFECT_TIERS` is the member this was written for: it reads
    `frozenset({"LIVE", "LATENT", "HARDENING"})  # 3 items`, and three surfaces
    outside this module were caught in cycle 5 still describing it as two
    (D-162, D-163, D-164). Their fix is prose on their own files; this is the
    half that belongs to the constant — every vocabulary here states its own
    size, and every one of those statements is checked against `len()` rather
    than trusted.
    """
    comments = _count_comments()
    assert len(comments) >= _MIN_COUNT_COMMENTS, (
        f"only {len(comments)} count comments resolved, below the floor of "
        f"{_MIN_COUNT_COMMENTS}. The scan has gone blind rather than the "
        f"module having shrunk; a pin that finds nothing passes for the wrong "
        f"reason."
    )

    unresolved = [(line, "") for line, name, _, _ in comments if not name]
    assert not unresolved, (
        f"count comments at lines {[line for line, _ in unresolved]} annotate "
        f"no top-level binding this module exports. Either the comment is on "
        f"the wrong line or the constant moved; both are the drift this pin is "
        f"for, so neither is skipped."
    )

    wrong: list[str] = []
    for line, name, claimed, noun in comments:
        value = getattr(vocab, name, None)
        if value is None or not hasattr(value, "__len__"):
            wrong.append(
                f"{name} (line {line}) carries a count comment but has no "
                f"length — a count comment on a scalar is a claim about "
                f"nothing"
            )
            continue
        if len(value) != claimed:
            wrong.append(
                f"{name} (line {line}) says '# {claimed} {noun}' and holds "
                f"{len(value)}"
            )
    assert not wrong, (
        "a vocabulary and its own count comment disagree, which is the "
        f"`PROSE_COUNT` class this module declares: {wrong}"
    )


def test_the_count_comment_pin_reads_the_tier_vocabulary_it_was_written_for(
) -> None:
    """The pin above actually reaches `DEFECT_TIERS`, and reads three.

    The scan is generic, so this is what stops it from being generically empty:
    the one declaration the cycle-5 filings were about must be among the
    comments it resolved, with the count the frozenset actually holds.
    """
    by_name = {name: claimed for _, name, claimed, _ in _count_comments()}

    assert by_name.get("DEFECT_TIERS") == len(vocab.DEFECT_TIERS) == 3, (
        "the tier vocabulary's own count comment is not in the scan's reach, "
        "which is the one comment it exists to hold"
    )
    assert by_name.get("DEFECT_TIER_OR_UNKNOWN") == len(
        vocab.DEFECT_TIER_OR_UNKNOWN
    ) == 4
    assert by_name.get("BLOCKING_TIERS") == len(vocab.BLOCKING_TIERS) == 2, (
        "BLOCKING_TIERS is the count that must NOT move when a tier is added "
        "(GI-014), and the pin has to be watching it to say so"
    )


def test_the_count_comment_pin_actually_fires() -> None:
    """A pin that cannot fail is a comment with an assert in it.

    Driven against a stand-in module text rather than by mutating `vocab.py`:
    the parse is the part that can rot, so the parse is what is exercised. A
    correct comment resolves and agrees; a stale one resolves and disagrees;
    a whole-line comment is not mistaken for a declaration's own count.
    """
    sample = "\n".join([
        "# CLOSED VOCABULARY — 99 items, in a sentence, not a declaration.",
        'STALE_SET = frozenset({"A", "B", "C"})  # 2 items',
        'GOOD_SET = frozenset({"A", "B"})  # 2 items',
    ]).splitlines()

    found = []
    for index, line in enumerate(sample):
        if line.lstrip().startswith("#"):
            continue
        match = _COUNT_COMMENT.search(line)
        if match is None:
            continue
        name = ""
        for back in range(index, -1, -1):
            binding = _TOP_LEVEL_BINDING.match(sample[back])
            if binding is not None:
                name = binding.group(1)
                break
        found.append((name, int(match.group(1))))

    assert found == [("STALE_SET", 2), ("GOOD_SET", 2)], (
        "the prose line was counted, or a declaration was missed; both are "
        "how this pin would go quiet"
    )
    assert found[0][1] != 3, (
        "STALE_SET holds three and claims two — the disagreement the real "
        "assertion reports"
    )


def test_reproduction_attempted_accepts_a_real_negative_result() -> None:
    """CT-001's example, verbatim from the spec's own FR-004 text."""
    assert vocab.reproduction_attempted_problem(
        "AST sweep of both roots finds 0 sites"
    ) is None


@pytest.mark.parametrize(
    "statement",
    [None, 17, [], "", "   ", "n/a", "N/A", "none", "TBD", "todo", "-", "unknown",
     "not attempted", "too short", "looked, nothing"],
)
def test_reproduction_attempted_refuses_a_statement_naming_no_evidence(
    statement,
) -> None:
    """FR-004 — the server refuses a LATENT filing without a real statement.

    Every case here is something a stream might actually type to get past the
    field. The refusal must NAME why, because a door that says only "invalid"
    teaches the stream to try another placeholder.
    """
    problem = vocab.reproduction_attempted_problem(statement)
    assert problem is not None, f"{statement!r} was accepted as evidence"
    assert "reproduction_attempted" in problem


def test_reproduction_placeholders_are_all_refused_by_their_own_rule() -> None:
    """The closed vocabulary and the predicate cannot drift apart."""
    for placeholder in vocab.REPRODUCTION_PLACEHOLDERS:
        assert vocab.reproduction_attempted_problem(placeholder) is not None
        assert vocab.reproduction_attempted_problem(f"  {placeholder.upper()}  ") is not None


def test_the_shared_reproduction_refusal_names_no_tier() -> None:
    """fallout D-115 / CT-012 — one rung, two tiers, and the sentence must fit both.

    `foundry.py` splits the HINT in two on the filing's own tier, because "a
    single hint would tell half its readers to write the wrong thing": a LATENT
    filing owes a negative result, a HARDENING filing owes the probe it drove
    and the wrong result it saw. The ERROR beside that hint came from here and
    was fixed LATENT text — "A LATENT filing must say what was driven and what
    it found (e.g. 'AST sweep of both roots finds 0 sites')" — so a HARDENING
    filing missing its reproduction was refused with the correct HARDENING hint
    and, one field over, an instruction to satisfy it with a negative-result AST
    sweep. That is the one kind of evidence a HARDENING record must NOT carry:
    the refusal told the stream to write the thing that would make the filing
    wrong.

    Neutral, not silent: the obligation both tiers share is still stated, and
    the sentence points at the hint for which evidence THIS tier owes.
    """
    for statement in ("", "none", "n/a", "short"):
        problem = vocab.reproduction_attempted_problem(statement)
        assert problem is not None, statement
        assert "LATENT" not in problem, (
            f"the shared refusal names LATENT to a HARDENING filer: {problem}"
        )
        assert "AST sweep" not in problem, (
            f"the shared refusal offers a NEGATIVE-RESULT example, which is "
            f"exactly what a HARDENING filing may not carry: {problem}"
        )
        assert "DRIVEN" in problem or "driven" in problem, (
            f"neutral is not empty — the shared obligation must still be "
            f"stated: {problem}"
        )


def test_the_two_tier_hints_beside_that_refusal_do_name_their_tier() -> None:
    """fallout D-115 — the tier-specific half lives at the door, and still does.

    Neutralising the shared sentence is only correct because the doors already
    carry the tier-correct instruction. If those two hints ever merged, this
    module's refusal would be the only text a filer got and it would name
    neither tier — so the split is asserted here, beside the neutrality it
    depends on.
    """
    from foundry_mcp.tools import foundry

    latent = foundry._LATENT_REPRODUCTION_HINT
    hardening = foundry._HARDENING_REPRODUCTION_HINT
    assert latent != hardening, "one hint for two tiers is what D-115 undid"
    assert "LATENT" in latent and "negative result" in latent
    assert "HARDENING" in hardening and "DROVE" in hardening
    assert "wrong result" in hardening, (
        "the HARDENING hint must ask for the wrong result the probe produced, "
        "which is the evidence the neutral refusal deliberately does not name"
    )


def test_is_security_property_text_is_the_same_predicate_as_the_denylist() -> None:
    """CT-003 — the LATENT denylist reuses the existing security predicate.

    D-090 and D-093 each widened `_SECURITY_RE` after real claims slipped
    past it. A second search site would have had to be found and widened
    twice, so this asserts the two agree on the whole D-093 battery rather
    than on one hand-picked string.
    """
    for label, description in SECURITY_CLAIM_BATTERY:
        assert vocab.is_security_property_text(description) is True, label
        assert vocab.is_security_property_text(description) == (
            vocab.is_security_property_claim({"description": description})
        ), label
    for description in NON_SECURITY_PROSE:
        assert vocab.is_security_property_text(description) is False, description


# ---------------------------------------------------------------------------
# FR-028 / ST-001 / ST-002 — escalation lifecycle vocabularies.
# ---------------------------------------------------------------------------


def test_escalation_vocabularies_name_both_exit_doors() -> None:
    """FR-028 — the exit reason must be machine-readable and appear in F6.

    Two doors, both recorded: ST-001 (clean cycles) and ST-002 (budget). A
    status flag alone cannot answer "why did this class stop being escalated",
    and an unanswerable exit is how a class quietly stops receiving structural
    packets while its instances keep arriving.
    """
    assert vocab.ESCALATION_STATUSES == frozenset({"ESCALATED", "CLEARED"})
    assert vocab.ESCALATION_EXIT_REASONS == frozenset({"clean_cycles", "budget"})
    assert vocab.STRUCTURAL_PASS_BUDGET == 2
    assert vocab.LIVE_CLEAN_CYCLES_TO_CLEAR == 2


# ---------------------------------------------------------------------------
# D-214 / D-215 — THE CLOSED VOCABULARY, ENFORCED ON EVERY READ OF THE FIELD.
#
# `escalation.json` has ONE writer and THREE readers: the orchestrator's two
# deciding reads behind `Foundry-Gate('done')`,
# `foundry_report.py#_read_escalated_classes` (report.json / REPORT.md), and
# `scripts/measure-run.py#_read_escalation` (the CLI census). The resolver was
# PRIVATE to the first of them, so the other two each carried their own opinion
# of one field and each was wrong in its own way — the report rendered any
# string verbatim and counted it in neither bucket (`count` 3, `by_status`
# summing to 2, a row reading "BOGUS"), and the CLI dropped every non-mapping
# entry one line above its own `unknown_status` counter (three of four classes
# off the census while the gate blocked on all four).
#
# The class `closed-vocabulary-not-enforced-on-the-deciding-read` recurred for
# three consecutive cycles (20, 21, 22) because each cycle fixed a copy. These
# pins are on the STRUCTURE, not on the copies: the resolver is public in
# vocab.py, the readers are DISCOVERED by AST over the shipped tree rather than
# listed by hand, and a fourth reader that spells the vocabulary itself fails
# here.
# ---------------------------------------------------------------------------

#: The one entry shape that retires a class. CLEARED is terminal — it ends
#: structural work on the class for the rest of the run — so it is spelled
#: exactly or it is not spelled.
_STATUS_SHAPES_RESOLVING_TO_CLEARED = ({"status": "CLEARED"},)

#: Every other shape, and the reason each is in the roster. The first spells
#: ESCALATED; the rest are the ways an entry can carry no status the vocabulary
#: knows — a value outside it (D-210), a near-miss spelling, a non-string, an
#: absent key, or an entry that is not a mapping at all (D-212). One roster, so
#: the resolver pins, the `is_unknown` pins and the cross-reader pin below all
#: walk the same shapes: a shape covered by one and not the others is exactly
#: how D-212 survived D-210's fix.
_STATUS_SHAPES_RESOLVING_TO_ESCALATED = (
    {"status": "ESCALATED"},
    {"status": "BOGUS"},
    {"status": "cleared"},
    {"status": "Cleared"},
    {"status": "CLEARED "},
    {"status": ""},
    {"status": None},
    {"status": 7},
    {"status": ["CLEARED"]},
    {},
    "just a string",
    ["ESCALATED"],
    None,
    7,
    True,
    [],
)

#: The shapes the resolver had to DEFAULT — everything above except the one
#: that actually spells a member. DERIVED from the roster, so a shape added to
#: it cannot leave this pin behind.
_STATUS_SHAPES_DEFAULTED = tuple(
    shape
    for shape in _STATUS_SHAPES_RESOLVING_TO_ESCALATED
    if shape != {"status": "ESCALATED"}
)


def test_the_two_status_names_are_exactly_the_closed_vocabulary() -> None:
    """The comparands live beside the frozenset, and are pinned to equal it.

    Moved here from `tests/test_escalation.py`, which pinned the pair while it
    was still `foundry_orchestrator`'s private copy. The names are vocab.py's
    now, so a member renamed or dropped must fail HERE first; that module's
    pin still passes because the orchestrator imports both names rather than
    re-typing them, which is the property this move exists to create.
    """
    assert {
        vocab.ESCALATION_STATUS_ESCALATED,
        vocab.ESCALATION_STATUS_CLEARED,
    } == set(vocab.ESCALATION_STATUSES)
    assert vocab.ESCALATION_STATUS_ESCALATED != vocab.ESCALATION_STATUS_CLEARED


@pytest.mark.parametrize("entry", _STATUS_SHAPES_RESOLVING_TO_ESCALATED)
def test_every_shape_but_an_exact_cleared_resolves_to_escalated(entry) -> None:
    """Out of vocabulary FAILS CLOSED, and shapelessness is out of vocabulary.

    True positives this keeps: `{"status": "BOGUS"}` (D-210 — a present value
    the vocabulary does not spell, which used to read as neither ESCALATED nor
    CLEARED at the two deciding reads), and every non-mapping entry (D-212 —
    which used to be dropped from the ESCALATED list, and from the report,
    ahead of the resolver rather than resolved by it).
    """
    assert vocab.escalation_status(entry) == vocab.ESCALATION_STATUS_ESCALATED


@pytest.mark.parametrize("entry", _STATUS_SHAPES_RESOLVING_TO_CLEARED)
def test_only_an_exact_cleared_resolves_to_cleared(entry) -> None:
    assert vocab.escalation_status(entry) == vocab.ESCALATION_STATUS_CLEARED


def test_the_resolver_is_total_over_the_closed_vocabulary() -> None:
    """Whatever it is handed, the answer is a member and nothing else.

    This is what lets every caller drop its own membership guard — the report's
    `by_status[status] += 1` has no `if status in by_status` above it any more,
    which is why `count == sum(by_status.values())` cannot drift (D-214).
    """
    for entry in (
        _STATUS_SHAPES_RESOLVING_TO_CLEARED + _STATUS_SHAPES_RESOLVING_TO_ESCALATED
    ):
        assert vocab.escalation_status(entry) in vocab.ESCALATION_STATUSES


@pytest.mark.parametrize("entry", _STATUS_SHAPES_DEFAULTED)
def test_a_defaulted_entry_is_reported_as_an_unknown_status(entry) -> None:
    """`unknown_status` counts the entries the resolver had to default.

    D-215's census printed `unknown_status: 0` while three of four classes were
    missing from it entirely, because the shape test ran above the counter.
    """
    assert vocab.escalation_status_is_unknown(entry) is True


def test_a_declared_member_is_never_an_unknown_status() -> None:
    """And it is NOT the same question as "resolves to ESCALATED".

    A class that genuinely records ESCALATED answers True to that and False to
    this. Conflating them would report every escalated class as undeclared.
    """
    for entry in ({"status": "ESCALATED"}, {"status": "CLEARED"}):
        assert vocab.escalation_status_is_unknown(entry) is False
    assert (
        vocab.escalation_status({"status": "ESCALATED"})
        == vocab.escalation_status({"status": "BOGUS"})
    )
    assert vocab.escalation_status_is_unknown(
        {"status": "ESCALATED"}
    ) != vocab.escalation_status_is_unknown({"status": "BOGUS"})


def _shipped_readers_of_escalation_json() -> dict[str, "object"]:
    """Every shipped module that NAMES `escalation.json` — literal or import.

    DISCOVERED, never listed. A hand list is what the three per-instance fixes
    of this class each amounted to: the fix landed on the readers somebody
    remembered. The AST walk finds a FOURTH reader the day it is written.

    TWO SPELLINGS, AND THE SECOND IS WHY (Holmes `vocab-3`, concern C-014).
    ----------------------------------------------------------------------
    Discovery keyed on the string CONSTANT alone, and Holmes named the trap
    that made: "the ONLY `escalation.json` Constant node in the orchestrator is
    the declaration ... if both imported the name from a shared home,
    `_shipped_readers_of_escalation_json` would find NEITHER. A structural pin
    that fails on centralisation is a ratchet holding the split in place, not a
    coincidence." It fired exactly as predicted the moment `foundry_report.py`
    stopped re-typing the literal and imported `ESCALATION_FILENAME` from the
    module that writes the file — the pin reported that it was "vouching for a
    reader it never looked at", which was true and was the RIGHT alarm.

    The answer is not to keep the literal. It is that a module which imports
    the NAME is a reader too, so discovery matches either spelling. The pin's
    own property is untouched — a reader nobody has written yet is still found
    the day it appears — and the ratchet is gone: centralising the filename now
    makes discovery follow it instead of losing it.

    Tests are excluded — a test builds fixture documents and asserts on the
    literals by design. `vocab.py` is not excluded by name and does not need to
    be: it holds the vocabulary, not the filename.
    """
    import ast

    root = REPO_ROOT / "plugins" / "foundry"
    found: dict[str, object] = {}
    for path in sorted(root.rglob("*.py")):
        rel = path.relative_to(REPO_ROOT).as_posix()
        if "/tests/" in rel or "/.venv/" in rel or "/site-packages/" in rel:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        names_the_file = any(
            isinstance(node, ast.Constant) and node.value == "escalation.json"
            for node in ast.walk(tree)
        ) or any(
            alias.name == "ESCALATION_FILENAME"
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
            for alias in node.names
        )
        if names_the_file:
            found[rel] = tree
    return found


#: The readers that existed when this pin was written. Asserted as a SUBSET of
#: what discovery finds, never as an equality: a new reader must be checked by
#: the assertions below, not merely noticed here. Naming them is what stops a
#: silently-collapsed discovery from passing forever (the D-202 lesson).
_KNOWN_ESCALATION_READERS = frozenset({
    # GI-010 / GI-026 — the escalation reader is `orchestration/escalation.py`
    # after casting 2's split. Repointed here rather than left naming a module
    # that will not exist: this roster is asserted as a SUBSET of what the walk
    # discovers, so a stale row makes the pin red the moment the monolith goes
    # and a row that never existed makes it red until the split lands. The
    # second is the honest RED — it is named in casting 10's completion report
    # and closed by casting 2, where the first would be a surprise nobody wrote
    # down.
    "plugins/foundry/mcp-server/src/foundry_mcp/tools/orchestration/escalation.py",
    "plugins/foundry/mcp-server/src/foundry_mcp/tools/foundry_report.py",
    "plugins/foundry/scripts/measure-run.py",
})


def test_every_shipped_reader_of_escalation_json_resolves_status_in_vocab() -> None:
    """THE STRUCTURAL PIN. Not "these three readers" — every reader there is.

    Two assertions per discovered module, and they are the two halves of the
    class:

      * it IMPORTS `escalation_status` from `schemas.vocab`, so the deciding
        read goes through the closed vocabulary rather than around it;
      * it holds NO bare `"ESCALATED"` / `"CLEARED"` string constant of its
        own, so it cannot grow a private opinion of the field beside the
        import. The roster of forbidden literals is `ESCALATION_STATUSES`
        itself — derived, so renaming a member cannot leave this behind.

    TRUE POSITIVES THIS KEEPS. `foundry_report.py`'s
    `status if isinstance(status, str) else "ESCALATED"` (D-214) fails the
    second assertion. `measure-run.py`'s `if not isinstance(status, str):
    status = "ESCALATED"` (D-215) fails the second. A fourth reader that
    reimplements either fails both.
    """
    import ast

    readers = _shipped_readers_of_escalation_json()
    missing = sorted(_KNOWN_ESCALATION_READERS - set(readers))
    assert not missing, (
        f"discovery no longer finds {missing} — it reads `escalation.json` by "
        f"some spelling this AST walk cannot see, so this pin is vouching for "
        f"a reader it never looked at"
    )

    for rel, tree in sorted(readers.items()):
        imported = [
            alias.asname or alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
            and node.module
            and node.module.endswith("schemas.vocab")
            for alias in node.names
            if alias.name == "escalation_status"
        ]
        # THE SECOND ARM: a module that resolves NOTHING is not required to
        # import the resolver. `orchestration/gates.py` names the file and
        # takes `_escalated_classes` / `_persisted_escalations` from
        # `orchestration/escalation.py` — the module that DOES import
        # `escalation_status` — so it holds no opinion of the field to drift.
        # Demanding the vocab import there would push a reader into importing
        # a resolver it never calls, which is noise, not a guard.
        #
        # Widening this arm costs nothing the pin was built for: the literal
        # ban below applies to EVERY discovered module unconditionally, and it
        # is the half that catches both named true positives. The delegation
        # arm cannot swallow the pin either — the assertion after the loop
        # requires that at least one discovered module really does import the
        # resolver.
        delegates = [
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
            and node.module
            and node.module.endswith("orchestration.escalation")
        ]
        assert imported or delegates, (
            f"{rel} reads escalation.json but neither imports "
            f"`escalation_status` from schemas.vocab nor takes its escalation "
            f"derivations from orchestration/escalation.py. Every reader of "
            f"that file resolves the status through the ONE resolver, or "
            f"through a module that does — a reader with its own is how D-214 "
            f"and D-215 happened, three cycles apart, in two different files."
        )
        literals = sorted({
            node.value
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and node.value in vocab.ESCALATION_STATUSES
        })
        assert literals == [], (
            f"{rel} holds the bare status literal(s) {literals}. The two "
            f"members are named ONCE, in schemas/vocab.py, as "
            f"ESCALATION_STATUS_ESCALATED and ESCALATION_STATUS_CLEARED; a "
            f"literal here is a second opinion of the field waiting to drift "
            f"from the resolver beside it (D-214 / D-215)."
        )

    # THE DELEGATION ARM IS NOT A WAY OUT OF THE PIN. At least one discovered
    # module must import the resolver itself, or a tree where every reader
    # delegated to a module that had quietly stopped importing it would pass
    # here while nothing resolved the status through the vocabulary at all.
    resolvers = [
        rel for rel, tree in readers.items()
        if any(
            alias.name == "escalation_status"
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
            and node.module
            and node.module.endswith("schemas.vocab")
            for alias in node.names
        )
    ]
    assert resolvers, (
        "no discovered reader of escalation.json imports `escalation_status` "
        "from schemas.vocab. Every one of them is delegating to somebody, and "
        "nobody is resolving."
    )
    assert len(readers) >= 3, sorted(readers)


#: The document the class was found on, carrying one of each shape: a class
#: that cleared, one that is escalated, one whose status is outside the
#: vocabulary (D-210 / D-214) and one that is not a mapping at all (D-212 /
#: D-215). Three of the four must read as still-escalated everywhere.
_FOUR_SHAPE_ESCALATION_DOCUMENT = {
    "classes": {
        "clean-class": {"status": "CLEARED", "exit_reason": "clean_cycles"},
        "live-class": {"status": "ESCALATED"},
        "bogus-class": {"status": "BOGUS"},
        "shapeless-class": "just a string",
    }
}


def _load_measure_run_module():
    """`scripts/measure-run.py` imported by path — its name has a hyphen.

    Registered in `sys.modules` BEFORE execution: the script defines
    dataclasses, and `dataclasses` resolves a field's string annotation through
    `sys.modules[cls.__module__]`, which raises AttributeError for a module
    that is not there yet.
    """
    import importlib.util

    path = REPO_ROOT / "plugins" / "foundry" / "scripts" / "measure-run.py"
    spec = importlib.util.spec_from_file_location("_measure_run_under_pin", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(spec.name, None)
        raise
    return module


def test_all_three_readers_account_for_every_class_on_one_document(
    tmp_path,
) -> None:
    """THE OBSERVABLE the structural fix is measured on (AC-004 / ST-010).

    One document, three readers, and every class accounted for in each. Before
    the fix the same four classes read as four in the gate, three in the report
    (with a row saying "BOGUS" and buckets summing to 2) and one in the CLI
    census — three artifacts of one run disagreeing about how many classes the
    run had, which is the shape an operator cannot detect by reading any one of
    them.

    Driving all three against ONE fixture is the point: a per-reader pin is
    what the last three cycles shipped, and each passed while its neighbours
    were wrong.
    """
    # GI-010 / GI-026 — the wave-2 destination. Casting 2's split defines
    # this symbol in the module named below and its completion report's
    # `## Symbol map` is the authority; where the map and this differ, the
    # map wins and this is the edit.
    from foundry_mcp.tools.foundry_report import _read_escalated_classes
    from foundry_mcp.tools.orchestration.escalation import _persisted_escalations

    run_dir = tmp_path / "foundry-archive" / "pin-run"
    run_dir.mkdir(parents=True)
    (run_dir / "escalation.json").write_text(
        json.dumps(_FOUR_SHAPE_ESCALATION_DOCUMENT), encoding="utf-8"
    )
    still_escalated = ["bogus-class", "live-class", "shapeless-class"]

    # Reader 1 — the deciding read behind Foundry-Gate('done').
    persisted = _persisted_escalations(
        run_dir, str(tmp_path), _FOUR_SHAPE_ESCALATION_DOCUMENT["classes"]
    )
    assert persisted == still_escalated, persisted

    # Reader 2 — report.json's `escalated_classes` section (D-214).
    section, problem = _read_escalated_classes(run_dir)
    assert problem is None, problem
    assert section["count"] == 4, section
    assert sum(section["by_status"].values()) == section["count"], section
    assert section["by_status"] == {"CLEARED": 1, "ESCALATED": 3}, section
    assert {row["class"]: row["status"] for row in section["classes"]} == {
        "clean-class": "CLEARED",
        "live-class": "ESCALATED",
        "bogus-class": "ESCALATED",
        "shapeless-class": "ESCALATED",
    }, section["classes"]

    # Reader 3 — measure-run.py's census (D-215).
    census = _load_measure_run_module()._read_escalation(run_dir)
    assert census["classes"] == 4, census
    assert sum(census["by_status"].values()) == census["classes"], census
    assert census["by_status"] == {"CLEARED": 1, "ESCALATED": 3}, census
    # `bogus-class` and `shapeless-class` declared no status the vocabulary
    # spells; the two that did are not counted here.
    assert census["unknown_status"] == 2, census

    # AND THE THREE AGREE — the property no per-reader pin can state.
    assert section["by_status"] == census["by_status"]
    assert sorted(
        row["class"] for row in section["classes"] if row["status"] != "CLEARED"
    ) == persisted


# ---------------------------------------------------------------------------
# ST-006 / ST-007 / GI-009 / FR-032 — INSPECT width.
# ---------------------------------------------------------------------------


def test_inspect_mode_vocabularies() -> None:
    assert vocab.INSPECT_MODES == frozenset({"FULL", "DELTA"})
    assert vocab.INSPECT_FULL_RULES == frozenset(
        {"first_of_phase", "final_gate", "verifier_touched"}
    )


def test_the_delta_rule_is_not_a_full_rule() -> None:
    """"Nothing forced FULL" is the ABSENCE of a rule, not a member of the set.

    Enrolling it would make `rule in INSPECT_FULL_RULES` read true for the one
    case that means the opposite.
    """
    assert vocab.INSPECT_DELTA_RULE == "delta"
    assert vocab.INSPECT_DELTA_RULE not in vocab.INSPECT_FULL_RULES


def test_the_full_roster_is_ordered_and_every_member_is_a_real_stream() -> None:
    """A tuple, not a frozenset — the roster is displayed in dispatch order."""
    assert isinstance(vocab.FULL_ROSTER_STREAMS, tuple)
    assert vocab.FULL_ROSTER_STREAMS == (
        "trace", "prove", "test", "research_audit", "test01",
    )
    unknown = set(vocab.FULL_ROSTER_STREAMS) - vocab.STREAM_WIRE_IDS
    assert not unknown, (
        f"the FULL roster names {sorted(unknown)}, which Foundry-Stream would "
        f"reject — a roster entry no stream can report against is a "
        f"streams-complete check that can never pass"
    )
    assert vocab.DELTA_CONDITIONAL_STREAMS < set(vocab.FULL_ROSTER_STREAMS), (
        "ST-007's conditional streams must be a PROPER subset of the FULL "
        "roster; a conditional stream outside it is required by nothing"
    )
    assert vocab.PROVE_DELTA_SAMPLE_SIZE == 10


#: FR-003 / AC-012 — the post-split verifier set, spelled as REAL repo paths.
#:
#: A-005 verbatim: "the gate/transition module(s), the width decision,
#: `schemas/`, `vocab.py`, the evidence sweep, and the loaded prose of the
#: VERIFYING agents (assayer, tracer, research-auditor, spec-test-deriver,
#: skills prove/trace/sight/temper)". A pattern that matches a synthetic path
#: and not the one git actually prints in a diff is a `verifier_touched` rule
#: that never fires, which is why these are the paths and not fixtures.
VERIFIER_SET = (
    "plugins/foundry/mcp-server/src/foundry_mcp/schemas/vocab.py",
    "plugins/foundry/mcp-server/src/foundry_mcp/schemas/findings.py",
    "plugins/foundry/mcp-server/src/foundry_mcp/tools/evidence.py",
    "plugins/foundry/agents/assayer.md",
    "plugins/foundry/agents/tracer.md",
    "plugins/foundry/agents/flow-tracer.md",
    "plugins/foundry/agents/research-auditor.md",
    "plugins/foundry/agents/coverage-diff.md",
    "plugins/foundry/agents/spec-test-deriver.md",
    "plugins/foundry/skills/prove/SKILL.md",
    "plugins/foundry/skills/trace/SKILL.md",
    "plugins/foundry/skills/sight/SKILL.md",
    "plugins/foundry/skills/temper/SKILL.md",
    "plugins/foundry/scripts/validate-test-observations.py",
    "plugins/foundry/scripts/validate-intent-coverage.py",
    # fallout D-125 (of D-080) — THE THREE LEAVES THE GI-033 LAYERING HOISTED
    # THE GATE AND WIDTH PREDICATES INTO. `unrecorded_width_problem`,
    # `inspect_mode_gap` and `WIDTH_RECORDING_TRANSITIONS` — the width refusal,
    # moved wholesale out of `width.py` — plus `check_streams_complete`,
    # `persisted_max_cycles`, `open_cross_casting_concerns`, `active_teams`,
    # `halted_state`, `current_inspect_mode`, `git_changed_paths` and
    # `boundary_base_sha` are defined in `foundry_state.py`;
    # `report_document_status`, `count_spec_requirements` and
    # `_spec_requirement_ids` in `artifacts.py`; the escalation rung readers in
    # `orchestration/escalation.py`. Each is called from a live refusal path in
    # `gates.py` or `transitions.py`, so a GRIND diff confined to one of these
    # three changed the machinery that JUDGES and recorded rule `delta`.
    "plugins/foundry/mcp-server/src/foundry_mcp/tools/foundry_state.py",
    "plugins/foundry/mcp-server/src/foundry_mcp/tools/artifacts.py",
    "plugins/foundry/mcp-server/src/foundry_mcp/tools/orchestration/escalation.py",
)

#: fallout D-113 — the sibling plugins in this repo. Every rule in
#: `VERIFIER_PATH_PATTERNS` is segment-anchored so it survives being installed
#: at a different root, and in the Guild source tree that anchor also matched
#: another plugin's identically-named file. No foundry stream loads crucible's
#: assayer or shells out to forge's spec validators, so each of these bought a
#: five-stream FULL INSPECT for a diff that touched no foundry surface at all.
SIBLING_PLUGIN_LOOKALIKES = (
    "plugins/crucible/agents/assayer.md",
    "plugins/forge/scripts/validate-spec.py",
    "plugins/forge/scripts/validate_spec_review.py",
)

#: The four modules casting 2 carves out of the monolith in WAVE 2. They are
#: members of the verifier set TODAY — a regex matches a string, and the rule is
#: correct the moment it is written — but they do not exist on disk yet, so the
#: existence half of the pin below cannot be asserted for them and must not be
#: faked.
#:
#: A SELF-CLOSING TRANSITIONAL PIN, NOT A FAIL-OPEN ONE. The existence
#: assertion is skipped only while `foundry_mcp/tools/orchestration/` itself is
#: absent; the moment casting 2 lands the package, every row here becomes a real
#: existence assertion with no edit. A row that never existed and a row whose
#: package arrived and whose module did not are then different outcomes, which
#: is the property a bare `pytest.skip` on the whole group would have thrown
#: away.
POST_SPLIT_VERIFIER_MODULES = (
    "plugins/foundry/mcp-server/src/foundry_mcp/tools/orchestration/gates.py",
    "plugins/foundry/mcp-server/src/foundry_mcp/tools/orchestration/transitions.py",
    "plugins/foundry/mcp-server/src/foundry_mcp/tools/orchestration/width.py",
    "plugins/foundry/mcp-server/src/foundry_mcp/tools/orchestration/evidence_boundary.py",
)

ORCHESTRATION_PACKAGE = (
    "plugins/foundry/mcp-server/src/foundry_mcp/tools/orchestration"
)

#: GI-009's violation column, as paths. "a display, report-seal, spend, halt,
#: directives or teams module (or `commands/*.md`, `teammate.md`,
#: `references/`) still matching a verifier pattern after the split". Each one
#: acts on a verdict or describes it; none of them moving can make a verdict
#: already reached wrong, which is the only thing `verifier_touched` is for.
POST_SPLIT_DELTA_MODULES = (
    "plugins/foundry/mcp-server/src/foundry_mcp/tools/orchestration/display.py",
    "plugins/foundry/mcp-server/src/foundry_mcp/tools/orchestration/report_seal.py",
    "plugins/foundry/mcp-server/src/foundry_mcp/tools/orchestration/spend.py",
    "plugins/foundry/mcp-server/src/foundry_mcp/tools/orchestration/halt.py",
    "plugins/foundry/mcp-server/src/foundry_mcp/tools/orchestration/directives.py",
    "plugins/foundry/mcp-server/src/foundry_mcp/tools/orchestration/teams.py",
    "plugins/foundry/mcp-server/src/foundry_mcp/tools/orchestration/guidance.py",
)


@pytest.mark.parametrize("path", VERIFIER_SET)
def test_every_path_fr_032_names_is_a_verifier_path(path: str) -> None:
    """FR-032 / AC-012's positive half, path by path.

    These are REAL repo paths, not synthetic ones: a pattern that matches
    `schemas/vocab.py` but not the path git actually prints in a diff is a
    verifier-touched rule that never fires.
    """
    assert vocab.is_verifier_path(path), (
        f"{path} is verifier machinery, so a GRIND diff touching it must force "
        f"the next INSPECT to FULL (ST-006, rule verifier_touched)"
    )
    assert (REPO_ROOT / path).exists(), (
        f"{path} no longer exists, so this row proves nothing about a real "
        f"diff. Point it at the file that replaced it."
    )


@pytest.mark.parametrize("path", POST_SPLIT_VERIFIER_MODULES)
def test_every_post_split_gate_module_is_a_verifier_path(path: str) -> None:
    """AC-012's four deciders, whose files casting 2 creates in wave 2.

    THE RULE IS ASSERTABLE TODAY AND THE EXISTENCE IS NOT, so the two halves
    are asserted separately rather than the whole row being skipped. A regex
    matches a string: `is_verifier_path` answers True for these paths the
    moment the pattern names them, and that is the property AC-012 states.

    The existence half ARMS ITSELF. While `foundry_mcp/tools/orchestration/`
    does not exist the assertion is skipped and says so; the moment casting 2
    lands the package, this becomes a real existence assertion with no edit
    here — so a module the split renamed fails this pin rather than passing it
    quietly, which is exactly what a fail-open transitional pin would not do.
    """
    assert vocab.is_verifier_path(path), (
        f"{path} is a gate, a transition, the width decision or the evidence "
        f"boundary — the four modules that DECIDE. A GRIND diff moving one can "
        f"make a verdict already reached wrong (ST-006)."
    )
    if not (REPO_ROOT / ORCHESTRATION_PACKAGE).is_dir():
        pytest.skip(
            f"{ORCHESTRATION_PACKAGE} does not exist yet — casting 2 creates "
            f"it in wave 2. The RULE is asserted above; the existence "
            f"assertion arms itself the moment the package appears."
        )
    assert (REPO_ROOT / path).exists(), (
        f"{ORCHESTRATION_PACKAGE} exists but {path} does not. The split landed "
        f"and this module was renamed or never created, so the narrowed "
        f"verifier rule names a path no diff can ever carry — point this row "
        f"at the file that replaced it, and fix VERIFIER_PATH_PATTERNS with it."
    )


@pytest.mark.parametrize("path", POST_SPLIT_DELTA_MODULES)
def test_every_post_split_lifecycle_module_earns_delta(path: str) -> None:
    """GI-009's violation column, asserted before the modules exist.

    "a display, report-seal, spend, halt, directives or teams module ... still
    matching a verifier pattern after the split". Each acts on a verdict or
    renders one; none of them moving can make a verdict already reached wrong.

    Asserted on the PATH rather than on the file, and deliberately so: this is
    the half of the narrowing that is easiest to undo by accident. A future
    edit that reaches for a `orchestration/` segment rule — which reads like
    the tidy generalisation of the four-module alternation — turns every row
    here red at once, which is the whole reason they are spelled out.
    """
    assert not vocab.is_verifier_path(path), (
        f"{path} is a lifecycle or presentation module: it ACTS on verdicts or "
        f"renders them, and moving it leaves every verdict already reached as "
        f"sound as it was. A GRIND diff confined to it earns rule `delta` "
        f"(AC-012 / OT-013). Widening to a `orchestration/` segment rule is the "
        f"regression this row exists to catch."
    )


@pytest.mark.parametrize(
    "path",
    [
        # Offline CLIs. They read a finished archive and print; no gate
        # consults either, so a diff touching one moves no judgement.
        "plugins/foundry/scripts/measure-run.py",
        "plugins/foundry/scripts/migrate-archive.py",
        # D-118's other side. The `scripts/validate[-_]…` rule that swept in
        # the two validators must NOT have swept in the rest of `scripts/`:
        # these install and update the plugin, and the commit guard constrains
        # what may be COMMITTED rather than judging whether the build is right.
        # None of them moving can make an earlier cycle's verdict wrong.
        "plugins/foundry/scripts/setup-foundry.sh",
        "plugins/foundry/scripts/update-mcp.sh",
        "plugins/foundry/hooks/pre-commit-guard.sh",
        # The server's OWN tests. They are the pins, not the judgement, and the
        # TEST stream re-runs them at every width. They are also the file a
        # self-targeting GRIND cycle touches most, so keeping them out is what
        # leaves that run a real DELTA case.
        "plugins/foundry/mcp-server/tests/test_vocab.py",
        "plugins/foundry/mcp-server/tests/conftest.py",
        "README.md",
        "plugins/foundry/README.md",
        "src/myschemas.py",          # `schemas` inside a name is not a segment
        "docs/agents.md",            # `agents` as a FILE is not the directory
        "skills/README.md",          # a skill dir's non-SKILL file
        "src/myfoundry_mcp/tools/x.py",  # `foundry_mcp` inside a name, again
        "",
        # ------------------------------------------------------------------
        # AC-012 / GI-009 — THE NARROWING'S OWN NEGATIVES.
        #
        # A-005 verbatim: "Display, report seal, spend, halt, directives,
        # teams, `commands/*.md`, `teammate.md` and `references/` earn DELTA."
        # Every row below was a verifier path before this change, and each one
        # is a GRIND cycle that used to pay for a five-stream INSPECT.
        # ------------------------------------------------------------------
        # The whole-package rule's cost, module by module. These are renderers,
        # ledgers and readers: they act on verdicts, and moving one leaves
        # every verdict already reached as sound as it was.
        # `foundry_state.py` LEFT THIS LIST at D-125 and did not leave it
        # quietly: the GI-033 layering hoisted the width refusal and nine other
        # gate predicates into it, so it stopped being a reader of verdicts and
        # became one of the modules that reach them. It is in `VERIFIER_SET`
        # above now. The four that remain are renderers and ledgers still.
        "plugins/foundry/mcp-server/src/foundry_mcp/tools/display.py",
        "plugins/foundry/mcp-server/src/foundry_mcp/tools/foundry_report.py",
        "plugins/foundry/mcp-server/src/foundry_mcp/tools/foundry_spawn.py",
        "plugins/foundry/mcp-server/src/foundry_mcp/tools/foundry_handoff.py",
        "plugins/foundry/mcp-server/src/foundry_mcp/tools/citation.py",
        "plugins/foundry/mcp-server/src/foundry_mcp/server.py",
        # The lead's protocol and the teammate contract. `commands/*.md` tells
        # the LEAD what to do next and `teammate.md` tells a BUILDER how to
        # build; neither is a stream contract, and D-118's widening swept both
        # in as collateral of the `agents|commands|references` rule.
        "plugins/foundry/commands/start.md",
        "plugins/foundry/commands/resume.md",
        "plugins/foundry/agents/teammate.md",
        # The shared references. `verification-patterns.md` IS loaded by
        # `agents/tracer.md` and `agents/assayer.md` as a binding contract —
        # that is D-118's own instance — and A-005, AC-012 and OT-013 each put
        # `references/` on the delta side anyway. The mitigation now covers the
        # agent and skill files where a stream's contract is STATED, and not
        # the documents they cite. The anti-staleness pin below is re-scoped to
        # the loaders to match (AC-017).
        "plugins/foundry/references/verification-patterns.md",
        "plugins/foundry/references/lead-discipline.md",
        # The non-verifying agents. A codebase mapper, a pattern mapper, a
        # researcher and a nyquist auditor produce INPUTS to the build or act
        # after it; none of them judges it, so none of them moving can make a
        # verdict already reached wrong.
        "plugins/foundry/agents/codebase-mapper.md",
        "plugins/foundry/agents/pattern-mapper.md",
        "plugins/foundry/agents/researcher.md",
        "plugins/foundry/agents/nyquist-auditor.md",
    ],
)
def test_an_ordinary_path_is_not_a_verifier_path(path: str) -> None:
    """The rule has to be able to say no, or every GRIND cycle runs FULL.

    DELTA exists to cut the ~1M-token cost of a five-stream INSPECT; a
    predicate that over-matches quietly deletes that saving while still
    reporting DELTA as available. AC-046 puts a number on it — FULL cycles
    under half of all INSPECT cycles — and on a self-targeting run the
    whole-package rule made that ceiling unreachable.
    """
    assert not vocab.is_verifier_path(path)


def test_the_verifier_rule_answers_in_both_directions() -> None:
    """AC-012 — the narrowing, asserted as the two-way statement it is.

    WHAT THIS PIN USED TO SAY, AND WHY IT COULD NOT SURVIVE THE SPLIT
    -----------------------------------------------------------------
    It was `test_the_whole_server_package_is_verifier_machinery`, and it walked
    every `.py` under `foundry_mcp/` asserting each answered True. That was the
    right assertion for a tree where one 15,000-line module held every gate,
    every transition, the width decision, the spend roll-up, the report seal and
    the halt door: "everything in the package is a gate or something a gate
    imports" was simply TRUE, and D-033's basename roster had gone stale for
    exactly that reason.

    The split makes it false. A-005 narrows the rule to the four deciders, the
    evidence sweep, `schemas/`, `vocab.py` and the verifying streams' contract
    prose, and puts display, report seal, spend, halt, directives, teams,
    `commands/*.md`, `teammate.md` and `references/` on the delta side. A
    one-directional pin cannot state that: it can only say what must answer
    True, and the whole content of the narrowing is what must now answer False.

    So the pin is REWRITTEN, not deleted — the D-033 property it protects (an
    enumeration going stale unnoticed) is real, and it is protected here by
    asserting the set in BOTH directions and by keeping the empty-set guard
    below, so a derived roster that stops finding anything fails loudly instead
    of going quietly green.
    """
    assert VERIFIER_SET, "an empty positive set proves nothing"
    assert POST_SPLIT_DELTA_MODULES, "an empty negative set proves nothing"

    missed = [p for p in VERIFIER_SET if not vocab.is_verifier_path(p)]
    assert not missed, (
        f"{missed} are the machinery that JUDGES the build and answer False, "
        f"so a GRIND diff moving one would be judged by a DELTA roster "
        f"(ST-006). Widen VERIFIER_PATH_PATTERNS; do not add rows here."
    )

    swept = [p for p in POST_SPLIT_DELTA_MODULES if vocab.is_verifier_path(p)]
    assert not swept, (
        f"{swept} act on verdicts or render them and still match a verifier "
        f"pattern (GI-009's violation column). Every GRIND cycle that touches "
        f"one then pays for a five-stream INSPECT, which is the cost AC-046 "
        f"puts a ceiling on."
    )


def test_the_server_package_is_no_longer_matched_whole() -> None:
    """AC-017's first clause: "no longer contains the whole-package rule".

    Driven as a PROPERTY of the predicate rather than as a scan of the constant,
    because a whole-package rule can be spelled several ways and a substring
    check for the old regex would miss every one of them but the retired
    spelling. The test is a module inside `foundry_mcp/` that is not verifier
    machinery: under the package rule such a path could not exist, and under
    the narrowed rule it must.
    """
    # fallout D-125 — `foundry_state.py` is off this list because it now DEFINES
    # the width refusal and nine other gate predicates (GI-033's hoist), not
    # because the package rule came back. The four below still carry the
    # property: real modules under `foundry_mcp/` that answer False.
    lifecycle = (
        "plugins/foundry/mcp-server/src/foundry_mcp/tools/display.py",
        "plugins/foundry/mcp-server/src/foundry_mcp/tools/foundry_report.py",
        "plugins/foundry/mcp-server/src/foundry_mcp/tools/foundry_spawn.py",
        "plugins/foundry/mcp-server/src/foundry_mcp/tools/citation.py",
    )
    for path in lifecycle:
        assert (REPO_ROOT / path).exists(), f"{path} is gone; re-point this row"
        assert not vocab.is_verifier_path(path), (
            f"{path} sits inside `foundry_mcp/` and answers True, so the "
            f"whole-package rule (or something equivalent to it) is back. "
            f"AC-017's first clause is that it is not."
        )

    # And `schemas/` is still matched WHOLE, which is the one package-shaped
    # rule A-005 keeps: every finding and report shape a stream validates on
    # lives there, so any module in it can change what a stream will accept.
    assert vocab.is_verifier_path(
        "plugins/foundry/mcp-server/src/foundry_mcp/schemas/findings.py"
    )


#: The shipped prose corpus: every document a stream agent or the lead is
#: handed. Walked rather than listed, so a new agent or skill joins the harvest
#: below without an edit here.
_SHIPPED_PROSE_GLOBS = ("agents/*.md", "commands/*.md", "skills/*/SKILL.md", "references/*.md")

#: A load instruction as the prose actually spells one. `commands/start.md`
#: explains why this is the only portable spelling: the `plugins/foundry/`
#: prefix exists in the Guild source repo alone, and an installed plugin has
#: its `agents/`, `commands/` and `references/` at the top level, so a document
#: meant to be READ is always addressed through the plugin root. Anything else
#: in the prose is a citation, not a load.
_PLUGIN_ROOT_MD_LOAD = re.compile(r"CLAUDE_PLUGIN_ROOT\}/([A-Za-z0-9_./-]+\.md)")

#: AC-017's re-scoping, as a PREDICATE over the corpus rather than a roster.
#:
#: A-005 names the verifying streams: "the assayer, tracer, research-auditor,
#: spec-test-deriver ... and the prove/trace/sight/temper skills", plus the two
#: FLOW/COVERAGE agents the same sentence's "loaded prose of the VERIFYING
#: agents" reaches. `is_verifier_path` is the shipped statement of that set, so
#: the corpus is DERIVED from it: a seventh verifying agent added to
#: `VERIFIER_PATH_PATTERNS` joins this harvest with no edit here, and a
#: document that leaves the set drops out of it the same way.
#:
#: Deriving it from the predicate under test is deliberate and is not circular,
#: because the harvest asserts something the predicate does not: that every
#: document these contracts LOAD is itself in the set. The predicate cannot
#: make that true by construction — it knows nothing about load instructions —
#: and the guards below fail the pin if the derivation ever selects nothing.
def _verifying_stream_contracts() -> list["Path"]:
    # The walk starts from the WHOLE shipped prose corpus and narrows by the
    # predicate, rather than pre-restricting to the two globs that happen to
    # survive it today. That is the difference between deriving the set and
    # asserting a hunch about it: `commands/*.md` and `references/*.md` drop
    # out because `is_verifier_path` says so, and the day a verifying stream's
    # contract lands in a fifth directory the corpus grows with no edit here.
    return sorted(
        doc
        for glob in _SHIPPED_PROSE_GLOBS
        for doc in (REPO_ROOT / "plugins" / "foundry").glob(glob)
        if vocab.is_verifier_path(str(doc.relative_to(REPO_ROOT)))
    )


def test_every_prose_file_the_shipped_agents_load_is_a_verifier_path() -> None:
    """AC-017 — "the anti-staleness pin still passes for every loaded contract
    of a verifying stream".

    WHAT D-118 FILED, AND WHY THE PIN SURVIVES THE NARROWING
    --------------------------------------------------------
    `VERIFIER_PATH_PATTERNS` named `agents/`, `skills/` and `commands/` one
    directory at a time, so `references/verification-patterns.md` — which
    `agents/tracer.md` and `agents/assayer.md` load as a binding contract —
    answered False, and a GRIND whose only touched file was the tracer's own
    verification contract recorded mode DELTA. Adding two rows to a
    parametrized list would have fixed those two files and kept the class, so
    this pin does not name files: it reads what the prose TELLS an agent to
    load and asserts every target answers True.

    THE RE-SCOPING, WHICH IS THE WHOLE OF AC-017'S SECOND CLAUSE
    ------------------------------------------------------------
    The harvest used to run over EVERY shipped prose document, including
    `commands/start.md`. A-005 puts `commands/*.md` and `references/` on the
    delta side, so the old harvest would now assert that
    `references/lead-discipline.md` — loaded by the LEAD's protocol, not by any
    stream — is a verifier path, which the requirements say it is not.

    So the corpus is the VERIFYING STREAMS' own contract prose, derived from
    `is_verifier_path` rather than listed. `references/verification-patterns.md`
    is the one target that leaves the assertion with it, and that is a decision
    the requirements make three times over (A-005, AC-012, OT-013): the
    mitigation covers the documents where a stream's contract is STATED, and
    not the shared documents those contracts cite. The pin's own property is
    untouched — a contract file added to a directory nobody has invented yet
    still fails here the moment a VERIFYING stream names it — and that is the
    property, not the particular targets it happened to catch in 2026.
    """
    plugin_root = REPO_ROOT / "plugins" / "foundry"
    assert plugin_root.is_dir(), f"{plugin_root} is gone; point this pin at the plugin"

    corpus = _verifying_stream_contracts()
    assert len(corpus) >= 4, (
        f"only {[str(p.relative_to(plugin_root)) for p in corpus]} derived as "
        f"verifying-stream contracts; A-005 names ten (six agents and four "
        f"skills), so `is_verifier_path` has stopped matching the prose half "
        f"and this pin would pass while proving nothing"
    )
    assert any("skills/" in str(p) for p in corpus), (
        "no skill file derived; the four verification skills are half the "
        "corpus and a harvest without them cannot witness a skill's contract "
        "changing"
    )

    # {loaded repo-relative path: the verifying contracts that load it}
    loaded: dict[str, set[str]] = {}
    for doc in corpus:
        for rel in _PLUGIN_ROOT_MD_LOAD.findall(doc.read_text(encoding="utf-8")):
            target = plugin_root / rel
            if not target.is_file():
                continue  # a renamed or illustrative path proves nothing
            loaded.setdefault(
                str(target.resolve().relative_to(REPO_ROOT)), set()
            ).add(str(doc.relative_to(plugin_root)))

    # THE "NO TARGET HARVESTED" GUARD, WHICH THE RE-SCOPING MAKES SHARPER
    # RATHER THAN WEAKER. The old harvest ran over every shipped document and
    # required four targets; this one runs over ten and requires that the
    # regex still matches the spelling the prose uses at all. A harvest that
    # found nothing would make the assertion below vacuously true, which is the
    # one way a re-scoped pin can go quietly green.
    assert loaded, (
        f"no `${{CLAUDE_PLUGIN_ROOT}}/….md` load target harvested from the "
        f"{len(corpus)} verifying-stream contracts. Either the prose stopped "
        f"loading documents that way, or the regex stopped matching the "
        f"spelling it uses — and either way this pin is now asserting nothing."
    )

    missed = {
        p: sorted(by) for p, by in sorted(loaded.items())
        if not vocab.is_verifier_path(p) and not p.startswith(
            "plugins/foundry/references/"
        )
    }
    assert not missed, (
        f"{missed} are loaded as binding contracts by the VERIFYING streams "
        f"listed beside them, yet answer False — so a GRIND diff moving one "
        f"would be judged by a DELTA roster (ST-006). Widen "
        f"VERIFIER_PATH_PATTERNS. The one exemption is `references/`, which "
        f"A-005, AC-012 and OT-013 each place on the delta side by name; "
        f"anything else here is a real gap."
    )


def test_the_references_exemption_is_the_only_one_and_it_is_real() -> None:
    """AC-012 / AC-017 — the exemption above, driven rather than asserted.

    The pin's exemption clause is the one place the re-scoping could hide a
    regression, so it is checked from the other side: `references/` must
    actually be a live load target of a verifying stream (or the exemption is
    dead prose covering nothing), and it must actually answer False (or the
    exemption is silently unnecessary and the narrowing did not land).

    Both halves matter. An exemption that covers nothing is a comment; an
    exemption whose subject already passes is a hole waiting for the day it
    does not.
    """
    plugin_root = REPO_ROOT / "plugins" / "foundry"
    referenced: dict[str, set[str]] = {}
    for doc in _verifying_stream_contracts():
        for rel in _PLUGIN_ROOT_MD_LOAD.findall(doc.read_text(encoding="utf-8")):
            if rel.startswith("references/") and (plugin_root / rel).is_file():
                referenced.setdefault(rel, set()).add(
                    str(doc.relative_to(plugin_root))
                )

    assert referenced, (
        "no verifying stream loads a `references/` document any more, so the "
        "exemption in the pin above covers nothing — delete it, and the "
        "harvest becomes unconditional again"
    )
    for rel, loaders in sorted(referenced.items()):
        assert not vocab.is_verifier_path(f"plugins/foundry/{rel}"), (
            f"plugins/foundry/{rel} answers True while A-005, AC-012 and "
            f"OT-013 each put `references/` on the delta side. It is loaded by "
            f"{sorted(loaders)}, so if the requirements have changed, change "
            f"the pin above with them — do not leave an exemption that no "
            f"longer describes the rule."
        )


def test_every_validator_a_stream_shells_out_to_is_a_verifier_path() -> None:
    """D-118 — the executable half, walked rather than named.

    `plugins/foundry/scripts/validate-test-observations.py` is what the
    TEST-01 adjudicator runs as its Layer 1, halting the stream on a non-zero
    exit; `validate-intent-coverage.py` is the standalone twin of the module
    `Foundry-Intent-Coverage` calls. Both answered False, so an edit to the
    thing that decides whether a stream's output is admissible was judged by a
    DELTA roster.

    The walk is what makes the fix survive a third validator being added: the
    rule is `scripts/validate…`, and this asserts the rule over the directory
    as it actually stands rather than over the two names the defect happened
    to catch.
    """
    scripts = REPO_ROOT / "plugins" / "foundry" / "scripts"
    assert scripts.is_dir(), f"{scripts} is gone; point this pin at the scripts"

    validators = sorted(scripts.glob("validate*.py"))
    assert len(validators) >= 2, (
        f"only {[p.name for p in validators]} found under {scripts}; the walk "
        f"is not reaching the validators, so this pin would prove nothing"
    )

    missed = [
        str(p.relative_to(REPO_ROOT))
        for p in validators
        if not vocab.is_verifier_path(str(p.relative_to(REPO_ROOT)))
    ]
    assert not missed, (
        f"{missed} are validators a verification stream executes, so a GRIND "
        f"diff moving one must force FULL (ST-006, rule verifier_touched)"
    )

    # The same directory's other half, asserted here rather than only in the
    # parametrized list, so the boundary is visible at the point the rule is
    # widened: these read a finished archive or install the plugin, and no
    # gate consults either.
    for offline in ("measure-run.py", "migrate-archive.py", "setup-foundry.sh"):
        rel = f"plugins/foundry/scripts/{offline}"
        assert (REPO_ROOT / rel).is_file(), f"{rel} is gone; re-point this row"
        assert not vocab.is_verifier_path(rel), (
            f"{rel} moves no judgement, so sweeping it in deletes a real DELTA "
            f"case and every GRIND cycle pays for a five-stream INSPECT"
        )


@pytest.mark.parametrize("path", SIBLING_PLUGIN_LOOKALIKES)
def test_another_plugins_lookalike_is_not_this_verifier(path: str) -> None:
    """fallout D-113 — a segment anchor cannot tell two plugins apart on its own.

    `(?:^|/)agents/(?:assayer|…)\\.md$` and `(?:^|/)scripts/validate[-_]…\\.py$`
    are written with a segment anchor so they still match when the plugin is
    installed at a different root, and in THIS repo that anchor also matched
    `plugins/crucible/agents/assayer.md` and forge's two spec validators. No
    foundry stream loads crucible's assayer or shells out to forge's
    validators, so each of these charged a five-stream FULL INSPECT against the
    AC-046 ceiling for a diff that touched no foundry surface.

    Driven as REAL repo paths, like the positive rows: a rule that excludes a
    path git never prints is a rule that never fires.
    """
    assert (REPO_ROOT / path).exists(), (
        f"{path} is gone; re-point this row at a sibling plugin's lookalike or "
        f"drop it — an exclusion asserted over a path that does not exist "
        f"proves nothing about a real diff"
    )
    assert not vocab.is_verifier_path(path), (
        f"{path} belongs to another plugin in this repo and answers True, so a "
        f"forge-only or crucible-only GRIND diff records rule verifier_touched "
        f"and pays for a full five-stream INSPECT (AC-012 / AC-046)"
    )


def test_the_foundry_twin_of_every_lookalike_still_answers_true() -> None:
    """fallout D-113 — the exclusion must not take the real file with it.

    The whole risk of a negative rule is that it is too wide. Each row above has
    a foundry twin that is genuinely stream-contract surface, and asserting the
    exclusion without asserting the twin would let a rule that matched
    `agents/assayer.md` everywhere pass this file.
    """
    for path in (
        "plugins/foundry/agents/assayer.md",
        "plugins/foundry/scripts/validate-test-observations.py",
        "plugins/foundry/scripts/validate-intent-coverage.py",
    ):
        assert vocab.is_verifier_path(path), (
            f"{path} is foundry's own and stopped matching; the D-113 "
            f"exclusion is too wide and has deleted the rule it was narrowing"
        )

    # And the PLUGIN-RELATIVE spellings still match, which is what
    # `tests/test_inspect_mode.py` drives the width decision with and the reason
    # the fix is an exclusion rather than a `foundry/` prefix on each rule.
    assert vocab.is_verifier_path("agents/assayer.md")
    assert vocab.is_verifier_path("skills/prove/SKILL.md")


def test_the_exclusion_gates_the_patterns_and_not_the_runs_own_spec() -> None:
    """fallout D-113 — FR-032's spec arm is not a name match and is not gated.

    A run whose spec lives under another plugin is still that run's own spec,
    and `verifier_touched` on the spec is the whole of FR-032. The exclusion
    exists to stop a rule matching by NAME; it has no business answering for a
    path the caller has named as this run's spec.
    """
    spec = "plugins/crucible/specs/mini-foundry/spec.md"
    assert not vocab.is_verifier_path(spec), "no pattern should claim it"
    assert vocab.is_verifier_path(spec, spec_path=spec), (
        "the spec arm was gated by the sibling-plugin exclusion, so a run "
        "building another plugin would judge its own spec's edits with a DELTA "
        "roster"
    )


def test_the_exclusion_set_is_a_constant_the_predicate_reads() -> None:
    """fallout D-113 — same rule as the pattern tuple: one constant, compiled once."""
    assert isinstance(vocab.VERIFIER_PATH_EXCLUSIONS, tuple)
    assert vocab.VERIFIER_PATH_EXCLUSIONS, "an empty exclusion set excludes nothing"
    for pattern in vocab.VERIFIER_PATH_EXCLUSIONS:
        re.compile(pattern)
    assert len(vocab._VERIFIER_PATH_EXCLUSION_RES) == len(
        vocab.VERIFIER_PATH_EXCLUSIONS
    ), "the compiled set is derived from the constant, never typed twice"


@pytest.mark.parametrize(
    "path",
    [
        "plugins/foundry/mcp-server/src/foundry_mcp/tools/foundry_state.py",
        "plugins/foundry/mcp-server/src/foundry_mcp/tools/artifacts.py",
        "plugins/foundry/mcp-server/src/foundry_mcp/tools/orchestration/escalation.py",
    ],
)
def test_the_hoisted_gate_predicates_host_module_forces_full(path: str) -> None:
    """fallout D-125 (of D-080) — the verifier set follows the machinery.

    FR-043: "the verifier set is exactly the gates/transitions module(s), THE
    WIDTH DECISION, the evidence sweep, schemas/, vocab.py, and the loaded
    prose". The GI-033 layering moved sixteen gate and width predicates OUT of
    the four decider modules — a symbol both the verifier layer and the
    lifecycle layer read can live in neither, so it went to a leaf — and this
    tuple did not follow them.

    The consequence is the self-hosting hazard A-032 names, at its sharpest: a
    GRIND diff touching only `unrecorded_width_problem`, the predicate that
    decides whether a cycle's verification was COMPLETE, recorded rule `delta`,
    so the next INSPECT ran narrow having just changed the thing that judges
    completeness. `verifier_touched` forcing FULL is the only accepted
    mitigation for exactly that.
    """
    assert (REPO_ROOT / path).exists(), f"{path} is gone; re-point this row"
    assert vocab.is_verifier_path(path), (
        f"{path} defines predicates called from a live refusal path in "
        f"gates.py or transitions.py and answers False, so a GRIND diff moving "
        f"one would be judged by a DELTA roster (FR-043 / AC-012 / ST-006)"
    )


def test_each_hoisted_predicate_named_by_d125_is_defined_where_the_rule_says() -> None:
    """fallout D-125 — the rows above are widened for a REASON, and it is checkable.

    A path added to `VERIFIER_SET` on a hunch is the D-033 staleness this file
    exists to catch, one direction over. These are the symbols the filing named
    — the width refusal, the streams-complete check, the cap behind `would_halt`,
    the CONCERN_OPEN rung, the halt rungs, the recorded-width reader, the DONE
    gate's report read — so if a later casting moves them somewhere else, the
    row above stops being justified HERE rather than going quietly stale.
    """
    from foundry_mcp.tools import artifacts, foundry_state
    from foundry_mcp.tools.orchestration import escalation

    for module, symbols in (
        (foundry_state, (
            "unrecorded_width_problem", "inspect_mode_gap",
            "WIDTH_RECORDING_TRANSITIONS", "current_inspect_mode",
            "check_streams_complete", "persisted_max_cycles",
            "open_cross_casting_concerns", "active_teams", "halted_state",
            "git_changed_paths", "boundary_base_sha", "sight_required",
        )),
        (artifacts, (
            "report_document_status", "count_spec_requirements",
        )),
        (escalation, ("_persisted_escalations",)),
    ):
        missing = [name for name in symbols if not hasattr(module, name)]
        assert not missing, (
            f"{module.__name__} no longer defines {missing}. D-125 widened "
            f"VERIFIER_PATH_PATTERNS to this module BECAUSE it defines them; "
            f"if they moved, move the pattern with them rather than leaving a "
            f"rule that names a file for a reason that is no longer true."
        )


def test_the_spec_is_matched_by_the_argument_not_by_a_pattern() -> None:
    """FR-032 — a run's spec lives wherever state.json.spec_path says.

    A static `spec\\.md$` pattern would sweep in every unrelated spec in the
    tree (this repo carries five under forge-specs/), so the run's own spec is
    passed at call time and compared after normalisation.
    """
    spec = "forge-specs/foundry-run-convergence/spec.md"
    assert not vocab.is_verifier_path(spec)
    assert vocab.is_verifier_path(spec, spec_path=spec)
    assert vocab.is_verifier_path("./" + spec, spec_path=spec), "normalised both sides"
    assert not vocab.is_verifier_path("forge-specs/other/spec.md", spec_path=spec)
    assert vocab.is_verifier_path(
        "foundry-archive/daring-orca/spec.md",
        spec_path="foundry-archive/daring-orca/spec.md",
    )


def test_verifier_patterns_are_one_constant_the_predicate_reads() -> None:
    """FR-032: "derived from one constant rather than typed in several places"."""
    assert isinstance(vocab.VERIFIER_PATH_PATTERNS, tuple)
    assert vocab.VERIFIER_PATH_PATTERNS, "an empty pattern set matches nothing"
    for pattern in vocab.VERIFIER_PATH_PATTERNS:
        re.compile(pattern)  # every member must be a valid regex
    assert len(vocab._VERIFIER_PATH_RES) == len(vocab.VERIFIER_PATH_PATTERNS), (
        "the compiled set is derived from the constant; a hand-maintained "
        "second list is the drift FR-032 forbids"
    )


# ---------------------------------------------------------------------------
# FR-034 / CT-006 — the lead fix lane.
# ---------------------------------------------------------------------------


def test_fix_authors_and_lane_limits() -> None:
    assert vocab.FIX_AUTHORS == frozenset({"lead", "teammate"})
    assert vocab.LEAD_LANE_MAX_FILES == 1
    assert vocab.LEAD_LANE_MAX_LINES == 20
    assert vocab.HANDOFF_EVENT_LEAD_FIX == "lead_fix"


# ---------------------------------------------------------------------------
# D-194 — the lane's MEASUREMENT rule, pinned where the lane is defined.
#
# The block above `LEAD_LANE_MAX_FILES` claimed a LATENT lead fix's fix_commit
# "is recorded and left alone", i.e. that git never reads it. Driven: a lead
# fix on a LATENT defect, over two non-test files and 401 added-plus-deleted
# lines, was accepted through `Foundry-Fix` and the server's own `lead_fix`
# record carries line_count 401 with a full per-file `files` array. The GRIND
# cycle 13 ruling on FR-046 / FR-053 / CT-006 / GI-003 / AC-022 settled it: the
# numstat MEASUREMENT runs on both tiers so every record carries a line count,
# and the LIMIT is what refuses only on LIVE.
#
# WHY A PROSE PIN AND NOT ONLY A BEHAVIOUR TEST. Same reason as D-148 above.
# The doors were already correct and already tested on both lanes
# (`test_fix_gate.py`, `test_handoff_records.py`); what was wrong was the
# DESCRIPTION of them in the module that DEFINES the lane, which no behaviour
# test can reach. `foundry_handoff.record_lead_fix_handoff` had already been
# corrected — this is the same class as D-148, stale prose surviving beside
# new prose, so the corrected sentence is asserted as text rather than trusted
# to stay.
# ---------------------------------------------------------------------------

#: The two halves of the ruling. Each is a phrase the corrected block must
#: carry, because stating one without the other is how the block was wrong in
#: the first place: it named the LIVE-only fact and let a reader infer that
#: LATENT is therefore untouched by git.
_LEAD_LANE_RULING_HALVES = (
    ("the measurement runs on both tiers", "runs on both tiers"),
    ("the limit refuses only on LIVE", "only when the defect is LIVE"),
)

#: The retired spellings, verbatim as they shipped in `vocab.py`. Asserted
#: ABSENT from every owned surface below. Both say the same false thing — that
#: the LATENT lane skips git entirely — and both are narrow enough that the
#: corrected block, which describes the same history in its own words, does not
#: match either.
_RETIRED_LEAD_LANE_SPELLINGS = (
    "A LATENT lead fix is NOT measured",   # D-194, vocab.py's lane block
    "recorded and left alone",             # D-194, the same sentence's tail
)


def _lead_lane_header() -> str:
    """The comment block above `LEAD_LANE_MAX_FILES` in schemas/vocab.py.

    Sliced from the source text, not from a docstring, for the D-148 reason:
    the block IS a `#` comment — the surface D-194 was filed against — so an
    assertion that read a docstring would leave the filed surface unread.
    """
    text = Path(vocab.__file__).read_text(encoding="utf-8")
    marker = "# ST-004 / CT-006 \u2014 the lane"
    assert marker in text, (
        f"schemas/vocab.py no longer opens the lead-lane block with {marker!r}, "
        f"so this pin reads nothing. Restore the marker or re-slice here; a pin "
        f"that silently reads an empty string is the D-194 defect with a green "
        f"suite over it."
    )
    start = text.index(marker)
    end = text.index("LEAD_LANE_MAX_FILES = ", start)
    return text[start:end]


def test_the_lead_lane_header_states_both_halves_of_the_measurement_ruling() -> None:
    """GI-003 / AC-022 / CT-006, per the GRIND cycle 13 ruling: the numstat
    measurement runs on both tiers so every lead_fix record carries a line
    count; the LIMIT is evaluated, and can refuse, only when the defect is
    LIVE.

    The block must say BOTH. It said only the second, phrased as though the
    first did not happen, which is the falsehood D-194 filed.
    """
    header = _lead_lane_header()
    for name, phrase in _LEAD_LANE_RULING_HALVES:
        assert phrase in header, (
            f"the lead-lane block does not state {name} ({phrase!r} is absent). "
            f"Both halves or neither: naming the LIVE-only limit alone is what "
            f"let a reader conclude git never reads a LATENT fix_commit, which "
            f"it does — the lead_fix record D-194 drove carries line_count 401."
        )
    assert "line count" in header, (
        "the block no longer says the record carries a line count on both "
        "tiers. That is the half GI-003 and AC-022 require and the half the "
        "retired sentence denied."
    )


@pytest.mark.parametrize("relpath", _OWNED_PROSE_SURFACES)
def test_no_casting_1_surface_says_a_latent_lead_fix_goes_unmeasured(
    relpath: str,
) -> None:
    """No owned file restates the retired lane rule.

    Scoped the same way as the D-148 scan and over the same roster, because
    the failure mode is the same one: the instance was fixed in `vocab.py`
    while a restatement elsewhere went on asserting it. `test_vocab.py` is the
    one exclusion — it spells both retired sentences above on purpose.
    """
    text = (REPO_ROOT / relpath).read_text(encoding="utf-8")
    hits = [s for s in _RETIRED_LEAD_LANE_SPELLINGS if s in text]
    assert not hits, (
        f"{relpath} says a LATENT lead fix goes unmeasured: {hits}. The numstat "
        f"measurement runs on BOTH tiers so every lead_fix record carries a "
        f"file list and a line count; only the LIMIT is LIVE-only. Say that, or "
        f"say nothing about the LATENT lane here."
    )


def test_the_retired_lead_lane_pin_actually_fires() -> None:
    """A pin that cannot fail is a comment with an assert in it.

    Each retired spelling is driven through the same containment check the
    scan above uses, in a sentence shaped like the one that shipped, and must
    be caught alone — so a later edit that narrows a pattern into uselessness,
    or lets the two shadow each other, fails here rather than going quiet.
    """
    for spelling in _RETIRED_LEAD_LANE_SPELLINGS:
        sample = f"on the lane: {spelling} (CT-006), so the commit is filed"
        hits = [s for s in _RETIRED_LEAD_LANE_SPELLINGS if s in sample]
        assert hits == [spelling], (
            f"{spelling!r} was not caught alone in {sample!r}; got {hits}"
        )

    # And the corrected block passes the scan it is pinned by — the positive
    # arm, so a roster so broad it condemns the fix fails here too.
    header = _lead_lane_header()
    assert not [s for s in _RETIRED_LEAD_LANE_SPELLINGS if s in header], (
        "the corrected lead-lane block matches its own retired roster; narrow "
        "the spellings rather than reword the ruling"
    )


@pytest.mark.parametrize(
    "path,expected",
    [
        # A `PYTEST_PYTHON_FILES` basename, in ANY directory (cycle-27
        # ruling): under a testpath, at the repo root, and nested deep
        # somewhere that is not a test tree at all.
        ("plugins/foundry/mcp-server/tests/test_vocab.py", True),
        ("tests/test_real.py", True),
        ("tests/test_dir_case.py", True),
        ("packages/api/tests/test_sweeper.py", True),
        ("test_root_case.py", True),
        ("verifier/deep/test_nested_case.py", True),
        # pytest's own fixed filename, loaded from the rootdir down.
        ("tests/conftest.py", True),
        ("conftest.py", True),
        ("verifier/deep/conftest.py", True),
        # D-222, escape 1: no pyproject.toml in this repo names `*_test.py`,
        # and the driven commit shipped 400 lines of it beside a 5-line source
        # file through a lane bounded at one file and twenty lines.
        ("pkg/thing_test.py", False),
        ("src/helpers_test.py", False),
        ("root_case_test.py", False),
        # D-222, escape 2: a `tests` segment is not a licence over every file
        # beneath it. `python_files` still has to match the basename.
        ("src/tests/production_helper.py", False),
        ("src/tests/__init__.py", False),
        ("mypkg/tests/big_module.py", False),
        ("tests/fixtures/measure_run/state_cycle_3.json", False),
        # D-231, the over-correction the cycle-27 ruling reverses: this row
        # read False, and with it the lane refused an in-lane LIVE lead fix
        # whose regression test sat outside `tests/`. `testpaths` SEEDS
        # argument-less collection; it does not stop a `test_*.py` elsewhere
        # from being a test, and the lane measures a commit in the TARGET
        # repo, which owes this repo no directory layout.
        ("test_sample.py", True),
        ("plugins/foundry/mcp-server/src/foundry_mcp/schemas/vocab.py", False),
        ("src/latest/handler.py", False),   # `latest` is not `tests`
        ("src/tests_helper.py", False),     # nor is `tests_helper`
        ("", False),
    ],
)
def test_is_test_file_matches_pytest_discovery(path: str, expected: bool) -> None:
    """FR-034 — test files are excluded from the lane's one-file count.

    The lane admits ONE non-test file, so a misclassification in either
    direction is a real refusal: calling a source file a test lets a lead ship
    two source files in one lane, and calling a test file source spends the
    whole budget on the regression test the fix is required to carry.

    D-223: this table used to PIN the first of those harms as correct
    behaviour — `("pkg/thing_test.py", True)` and
    `("tests/fixtures/measure_run/state_cycle_3.json", True)` asserted that
    two files no declared configuration collects were tests, so the sentence
    above named a harm its own rows caused. The rows now answer the way
    `pytest --collect-only` does, and
    `test_is_test_file_agrees_with_real_pytest_in_both_directions` drives that
    agreement against real pytest rather than against this table.

    D-231 — AND THE NARROWING OVER-CORRECTED, SO THE OTHER HARM LANDED.
    ------------------------------------------------------------------
    That fix ALSO required a `PYTEST_TESTPATHS` run in the directory part,
    which made `test_root_case.py` and `verifier/deep/test_nested_case.py`
    SOURCE files. Driven at the real Foundry-Fix door: an in-lane LIVE lead
    fix — one non-test file, 20 lines, plus its regression test — was refused
    with "changes 2 non-test file(s) (lanemod0691.py, test_lane0692.py) — the
    lane requires exactly 1", which is the SECOND harm the paragraph above
    names, spending the whole budget on the regression test. The cycle-27
    lead ruling settles it: the BASENAME is the whole test, in any directory,
    because `testpaths` only seeds argument-less collection and the lane
    measures a commit in the TARGET repo. `*_test.py` stays source — no
    `pyproject.toml` in this repo declares it — so D-222 does not reopen.
    """
    assert vocab.is_test_file(path) is expected


#: The pytest configuration sources the FR-034 pins read. Every
#: `pyproject.toml` under the repo that declares `[tool.pytest.ini_options]` is
#: a source; the interpreter's own virtualenvs and vendored packages are not
#: this repo's configuration and are excluded by segment.
_NON_REPO_SEGMENTS = frozenset({".venv", "venv", "node_modules", "site-packages", ".git"})


def _declared_pytest_discovery() -> dict[str, dict[str, tuple[str, ...]]]:
    """`{relpath: {"python_files": (...), "testpaths": (...)}}`, parsed.

    Read with `tomllib` rather than grepped: the previous guard matched
    `python_files\\s*=\\s*\\[([^\\]]*)\\]` with a regex, which sees one legal
    spelling of the setting and silently reads nothing from the others. The
    configured source is TOML, so it is parsed as TOML.
    """
    found: dict[str, dict[str, tuple[str, ...]]] = {}
    for path in sorted(REPO_ROOT.rglob("pyproject.toml")):
        rel = path.relative_to(REPO_ROOT)
        if _NON_REPO_SEGMENTS.intersection(rel.parts):
            continue
        section = (
            tomllib.loads(path.read_text(encoding="utf-8"))
            .get("tool", {})
            .get("pytest", {})
            .get("ini_options")
        )
        if not isinstance(section, dict):
            continue
        found[rel.as_posix()] = {
            "python_files": tuple(section.get("python_files", ())),
            "testpaths": tuple(section.get("testpaths", ())),
        }
    return found


def test_vocab_mirrors_every_declared_pytest_discovery_setting() -> None:
    """FR-034 verbatim: "provided it matches the repo's pytest discovery
    patterns" — EQUALITY against the configured source, not coverage of it.

    D-222 / D-223. The guard this replaces read `python_files` out of ONE
    pyproject.toml and asserted only that every glob pytest collects answers
    `is_test_file` True. That is the safe direction alone, so it passed
    unchanged however broad the recogniser became: the re-typed basename
    regexes it read from also matched `*_test.py`, and `TEST_DIRECTORY_SEGMENT`
    matched any path segment
    spelled `tests` whatever the file inside it was, and neither surplus could
    fail here. Both surpluses are SUBTRACTED from the lead lane's file and line
    counts, which is how a `fix_commit` over `src/tiny.py` plus a 400-line
    `src/helpers_test.py` cleared a lane bounded at one non-test file and 20
    lines.

    So the pin is equality, in both directions, against every declared source:
    a glob the repo adds that vocab does not mirror fails here, and a glob
    vocab carries that no `pyproject.toml` asks for fails here too.
    """
    declared = _declared_pytest_discovery()
    assert declared, (
        "no pyproject.toml in this repo declares [tool.pytest.ini_options] — "
        "the pin has no source, and a pin with no source proves nothing"
    )
    for relpath, config in sorted(declared.items()):
        assert set(config["python_files"]) == set(vocab.PYTEST_PYTHON_FILES), (
            f"{relpath} declares python_files={list(config['python_files'])} but "
            f"vocab.PYTEST_PYTHON_FILES is {list(vocab.PYTEST_PYTHON_FILES)}. The "
            f"lane's recogniser MIRRORS the configured source; update the "
            f"constant to match the config rather than widening it past one."
        )
        assert set(config["testpaths"]) == set(vocab.PYTEST_TESTPATHS), (
            f"{relpath} declares testpaths={list(config['testpaths'])} but "
            f"vocab.PYTEST_TESTPATHS is {list(vocab.PYTEST_TESTPATHS)}. A "
            f"testpath vocab does not know about is a test tree the lane counts "
            f"as source; one it invents is a source tree the lane waves through."
        )


#: The shapes D-222 was driven on, the three D-231 was driven on, and the
#: files the driven `pytest --collect-only` actually collects. Materialised
#: into a throwaway repo below and judged against real pytest, so the corpus
#: is not a restatement of the predicate under test.
_FR034_CANDIDATE_FILES = (
    "tests/test_real.py",
    "tests/__init__.py",
    "tests/conftest.py",
    "tests/helpers_test.py",
    "tests/fixtures/state_cycle_3.json",
    "src/tiny.py",
    "src/helpers_test.py",
    "src/tests/__init__.py",
    "src/tests/production_helper.py",
    "src/tests/big_module.py",
    # D-231 — a `test_*.py` outside every declared testpath, at the root and
    # nested, plus the `*_test.py` spelling that stays SOURCE beside them.
    "test_root_case.py",
    "verifier/deep/test_nested_case.py",
    "root_case_test.py",
)

#: The candidates that carry a real `def test_...`, so `--collect-only` has an
#: item to report for them. Enumerated rather than derived from
#: `PYTEST_PYTHON_FILES`, because a corpus built by the predicate under test
#: proves nothing about it. Two members are DELIBERATELY uncollectable:
#: pytest skips `root_case_test.py` and `tests/helpers_test.py` on the
#: basename alone, however many tests they define — which is the D-222
#: direction re-driven, now that the D-231 rows have widened the other one.
_FR034_FILES_WITH_A_TEST = frozenset(
    {
        "tests/test_real.py",
        "tests/helpers_test.py",
        "test_root_case.py",
        "verifier/deep/test_nested_case.py",
        "root_case_test.py",
    }
)


def _pytest_ini_options_block() -> str:
    """mcp-server's `[tool.pytest.ini_options]` table, re-emitted as TOML.

    Round-tripped through `tomllib` + `json.dumps` so the throwaway repo below
    is configured by THIS repo's own settings rather than by a second copy of
    them typed into a test.
    """
    parsed = tomllib.loads(
        (REPO_ROOT / "plugins" / "foundry" / "mcp-server" / "pyproject.toml")
        .read_text(encoding="utf-8")
    )
    options = parsed["tool"]["pytest"]["ini_options"]
    lines = ["[tool.pytest.ini_options]"]
    for key, value in options.items():
        lines.append(f"{key} = {json.dumps(value)}")
    return "\n".join(lines) + "\n"


def test_is_test_file_agrees_with_real_pytest_in_both_directions(tmp_path) -> None:
    """FR-034 — the converse assertion D-223 says was missing.

    The predicate is judged against `pytest --collect-only` itself, run in a
    throwaway repo configured by mcp-server's own
    `[tool.pytest.ini_options]` table, over a candidate set that spans every
    shape D-222 was driven on. Both directions are asserted: a file pytest
    collects must answer True, and — the direction no previous guard had — a
    file pytest does NOT collect must answer False.

    That second direction is the whole defect. `pytest --collect-only` against
    this configuration collects ONLY `tests/test_real.py`, while the shipped
    recogniser answered True for `src/helpers_test.py`,
    `src/tests/production_helper.py`, `src/tests/__init__.py` and
    `tests/fixtures/state_cycle_3.json` — four files whose lines the lead lane
    then subtracted from its own bound.

    `conftest.py` is the one deliberate exception and it is asserted as one:
    pytest hardcodes the name and loads it from the rootdir down, so it never
    appears as a collected node id even though it is unambiguously pytest's.

    D-231 — AND THE COLLECTION IS DRIVEN WITH AN EXPLICIT PATH ARGUMENT.
    -------------------------------------------------------------------
    This ran `--collect-only` with no path. Argument-less collection seeds
    itself from `testpaths`, so it reports "not collected" for every
    `test_*.py` outside `tests/` whatever pytest would say about one — and
    the guard therefore PINNED the over-correction D-231 names instead of
    catching it. With `.` passed, pytest answers for the whole throwaway
    repo: `test_root_case.py` and `verifier/deep/test_nested_case.py` come
    back collected, while `root_case_test.py` and `tests/helpers_test.py` do
    not, though all four define a test function. That is the cycle-27 ruling,
    driven rather than restated.
    """
    (tmp_path / "pyproject.toml").write_text(
        _pytest_ini_options_block(), encoding="utf-8"
    )
    for rel in _FR034_CANDIDATE_FILES:
        target = tmp_path / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        if rel in _FR034_FILES_WITH_A_TEST:
            target.write_text("def test_case():\n    assert True\n", encoding="utf-8")
        elif rel.endswith(".json"):
            target.write_text("{}\n", encoding="utf-8")
        else:
            target.write_text("VALUE = 1\n", encoding="utf-8")

    # The "." is load-bearing (D-231). Argument-less collection SEEDS itself
    # from `testpaths`, so it can only ever answer for files under `tests/`
    # and would pin the very over-correction this test exists to catch. An
    # explicit path argument is what makes pytest answer for the whole repo,
    # which is the question the lane actually asks of a TARGET commit.
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
         "--collect-only", "."],
        cwd=str(tmp_path), capture_output=True, text=True, timeout=300,
    )
    collected = {
        line.strip().split("::", 1)[0]
        for line in proc.stdout.splitlines()
        if "::" in line and not line.startswith(" ")
    }
    assert collected == {
        "tests/test_real.py",
        "test_root_case.py",
        "verifier/deep/test_nested_case.py",
    }, (
        "the throwaway repo did not collect what this repo's configuration "
        f"says it should (rc={proc.returncode}); collected={sorted(collected)}\n"
        f"{proc.stdout[-2000:]}\n{proc.stderr[-2000:]}"
    )

    disagreements = []
    for rel in _FR034_CANDIDATE_FILES:
        expected = rel in collected or rel.rsplit("/", 1)[-1] == "conftest.py"
        if vocab.is_test_file(rel) is not expected:
            verdict = "collects" if expected else "does not collect"
            disagreements.append(
                f"{rel}: pytest {verdict} it, is_test_file says "
                f"{vocab.is_test_file(rel)}"
            )
    assert not disagreements, (
        "is_test_file disagrees with the pytest configuration it mirrors:\n  "
        + "\n  ".join(disagreements)
        + "\nEvery disagreement in the False->True direction is a source file "
          "the lead lane subtracts from its own one-file, twenty-line bound."
    )


# ---------------------------------------------------------------------------
# GI-006 / CT-014 / ST-008 / NFR-001 — report, terminal state, baseline.
# ---------------------------------------------------------------------------


def test_report_required_sections_is_the_fifteen_gi_006_names_in_order() -> None:
    """A TUPLE — GI-006 fixes the ORDER as well as the membership.

    report.json's top-level keys are these plus generated_at and run, and
    REPORT.md carries one `## ` heading per member in this sequence, so a
    frozenset here would leave the order undefined at both surfaces.

    AC-047 / FR-027 grow it by FOUR, and each one is placed beside the section
    it is read WITH rather than appended at the end: the HARDENING backlog
    beside the LATENT backlog (AC-024 asks for that by name), fallout after the
    defect sections it counts, stream coverage beside the INSPECT widths it
    measures, and the halt after the dispatch sections whose sets it lists.
    """
    assert vocab.REPORT_REQUIRED_SECTIONS == (
        "verdict_matrix",
        "requirement_span",
        "defects_by_tier_and_status",
        "latent_backlog",
        "hardening_backlog",
        "unknown_tier_defects",
        "fallout_per_cycle",
        "escalated_classes",
        "lead_fix_records",
        "inspect_modes_per_cycle",
        "stream_coverage_per_cycle",
        "spend_per_phase_and_cycle",
        "unreported_dispatches",
        "halt_and_co_dispatch",
        "executing_versions",
        "baseline_comparison",
    )
    assert len(set(vocab.REPORT_REQUIRED_SECTIONS)) == 16, "no duplicate section"


def test_the_hardening_backlog_sits_beside_the_latent_backlog() -> None:
    """AC-024 names the placement, not just the membership.

    "`report.json` and `REPORT.md` carry a HARDENING backlog section BESIDE the
    LATENT backlog". The tuple is what `_render_markdown` walks, so a member's
    index here IS where the section sits in the document a lead reads — and a
    reader comparing the two backlogs should not have to scroll past nine
    sections to do it.
    """
    order = list(vocab.REPORT_REQUIRED_SECTIONS)
    assert order.index("hardening_backlog") == order.index("latent_backlog") + 1
    assert order.index("stream_coverage_per_cycle") == (
        order.index("inspect_modes_per_cycle") + 1
    ), "both are per-cycle facts about the same INSPECTs"


# ---------------------------------------------------------------------------
# fallout D-015 — the run-phase ladder, declared once.
# ---------------------------------------------------------------------------


def test_the_phase_ladder_is_the_loop_gi_001_names_in_order() -> None:
    """fallout GI-001's loop, as the ids a run passes through.

    ORDER IS THE ASSERTION, not membership: a renderer walks this tuple to draw
    the ladder, so a phase inserted in the wrong place is drawn in the wrong
    place. HALTED is deliberately not a row — it is where a run stops instead
    of continuing along the ladder, and the status renderer draws it as its own
    line (D-137).
    """
    assert vocab.PHASE_LADDER == (
        ("F0", "RESEARCH"),
        ("F0.5", "DECOMPOSE"),
        ("F0.9", "VALIDATE"),
        ("F1", "CAST"),
        ("F2", "INSPECT"),
        ("F3", "GRIND"),
        ("F4", "ASSAY"),
        ("F5", "TEMPER"),
        ("F5.5", "NYQUIST"),
        ("F6", "DONE"),
    )
    assert len(vocab.PHASE_LADDER) == 10
    assert vocab.RUN_PHASE_HALTED not in dict(vocab.PHASE_LADDER)


def test_the_phase_name_lookup_is_derived_from_the_ladder() -> None:
    """One declaration, two shapes — a renderer holding an id gets a name.

    DERIVED, so adding a phase is one edit. The two hand-typed copies D-015
    filed against were a mapping in `display.py` and a list of pairs in
    `orchestration/guidance.py`, each knowing the same ten rows with nothing
    comparing them; deriving one from the other is what makes that
    unrepresentable rather than merely fixed.
    """
    assert vocab.PHASE_NAMES == dict(vocab.PHASE_LADDER)
    assert vocab.PHASE_NAMES["F2"] == "INSPECT"
    assert vocab.PHASE_NAMES.get("HALTED") is None


def test_display_reads_the_phase_vocabulary_and_declares_none_of_its_own() -> None:
    """fallout D-015 — the rendering module IMPORTS the ladder.

    The defect is a second DECLARATION, so the pin is on the declaration and
    not on the values: no top-level container in `display.py` may spell a run
    phase id. Before this change it held a ten-row phase-name table of its
    own, re-typed beside the renderers that used it, and this failed naming it.

    Scoped to the RUN ladder's ids on purpose. `_FORGE_PHASE_ICONS` is Forge's
    spec phases (S0..S3, READY) — a different vocabulary for a different
    machine, sharing only the word "phase", and folding it in here would make
    the pin fire on a table that duplicates nothing.
    """
    import ast
    from foundry_mcp.tools import display

    ladder_ids = {pid for pid, _ in vocab.PHASE_LADDER}
    source = Path(display.__file__).read_text(encoding="utf-8")
    offenders = []
    for node in ast.parse(source).body:
        if isinstance(node, ast.Assign):
            names = [t.id for t in node.targets if isinstance(t, ast.Name)]
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names = [node.target.id]
        else:
            continue
        spelled = {
            n.value for n in ast.walk(node.value)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)
        }
        if spelled & ladder_ids:
            offenders.extend(names)

    assert not offenders, (
        f"display.py declares a run-phase table of its own again ({offenders}); "
        f"the ladder is `vocab.PHASE_LADDER` and the lookup "
        f"`vocab.PHASE_NAMES` (D-015)."
    )
    assert display.PHASE_NAMES is vocab.PHASE_NAMES


def test_the_palette_display_publishes_is_the_palette_it_renders_with() -> None:
    """fallout D-014 — the cross-module palette is a PUBLIC contract.

    `orchestration/guidance.py#_format_status_display` renders the status
    banner and the ladder with this palette. It reached the underscore
    spellings across a module boundary, which is a published contract written
    as if it were internal. The public names are the definitions now and the
    private ones are aliases, so the two cannot drift while that module
    repoints.
    """
    from foundry_mcp.tools import display

    for public, private in (
        ("RESET", "_RESET"), ("DIM", "_DIM"), ("GREEN", "_GREEN"),
        ("BRED", "_BRED"), ("BGREEN", "_BGREEN"), ("BYELLOW", "_BYELLOW"),
        ("BCYAN", "_BCYAN"), ("BWHITE", "_BWHITE"),
    ):
        assert getattr(display, public) == getattr(display, private), public
        assert getattr(display, public).startswith("\033["), public


def test_the_span_section_sits_beside_the_verdict_matrix() -> None:
    """fallout AC-044 / D-039 — the placement, and the count it grew to.

    The two are the same table read along its two axes: the matrix says whether
    each requirement came out VERIFIED, the span says how many castings had to
    build it. A reader asking why a requirement came out thin reads them
    together, and the tuple's order is where the document puts them.
    """
    order = list(vocab.REPORT_REQUIRED_SECTIONS)
    assert order.index("requirement_span") == order.index("verdict_matrix") + 1
    assert len(vocab.REPORT_REQUIRED_SECTIONS) == 16


def test_run_artifact_filenames_and_the_halted_state() -> None:
    assert vocab.SPEND_LEDGER_FILENAME == "spend.jsonl"
    assert vocab.REPORT_MD_FILENAME == "REPORT.md"
    assert vocab.REPORT_JSON_FILENAME == "report.json"
    # ST-008 — a named terminal state reached by a SUCCESSFUL transition, and
    # deliberately not "DONE".
    assert vocab.RUN_PHASE_HALTED == "HALTED"
    assert vocab.RUN_PHASE_HALTED != "DONE"


# ---------------------------------------------------------------------------
# FR-019 / CT-005 / ST-001 — the halt reason vocabulary.
# ---------------------------------------------------------------------------


def test_halt_reasons_is_the_four_member_closed_vocabulary() -> None:
    """FR-019 verbatim: "{cap_reached, lead_ruling, spec_change_required,
    user_stop}"."""
    assert vocab.HALT_REASONS == frozenset(
        {"cap_reached", "lead_ruling", "spec_change_required", "user_stop"}
    )
    assert isinstance(vocab.HALT_REASONS, frozenset)
    assert len(vocab.HALT_REASONS) == 4
    assert vocab.HALT_REASON_CAP_REACHED == "cap_reached"
    assert vocab.HALT_REASON_CAP_REACHED in vocab.HALT_REASONS


@pytest.mark.parametrize("member", sorted(
    {"cap_reached", "lead_ruling", "spec_change_required", "user_stop"}
))
def test_halt_reason_resolves_every_member_by_identity(member: str) -> None:
    assert vocab.halt_reason(member) == member


@pytest.mark.parametrize(
    "value",
    [None, "", "CAP_REACHED", "cap reached", "budget", 1, True, [], {},
     "--max-cycles 2 reached: opening GRIND cycle 3 would exceed it"],
    ids=repr,
)
def test_halt_reason_never_guesses_a_member_out_of_a_sentence(value) -> None:
    """CT-005 — None is a REAL answer and not an error.

    `halted_reason` was a bare f-string before FR-019, and every archive
    written then carries one. Coercing that sentence onto `cap_reached`
    because it contains "max-cycles" would reclassify a run's ending by
    reading its prose, which is the guess `defect_tier` refuses one axis over.
    A reader prints the text; a grouper skips the row.
    """
    assert vocab.halt_reason(value) is None


def test_halt_reason_phrase_is_derived_from_the_constant() -> None:
    """The `_PYTEST_DISCOVERY_PHRASE` shape: a refusal that names a set reads it.

    The door that refuses an unknown reason has to print the set it accepts,
    and a hand-typed copy of a closed vocabulary in a refusal message is the
    drift this whole module exists to end. Sorted, so the sentence is stable.
    """
    phrase = vocab.halt_reason_phrase()
    assert phrase == ", ".join(sorted(vocab.HALT_REASONS))
    for member in vocab.HALT_REASONS:
        assert member in phrase
    assert phrase == "cap_reached, lead_ruling, spec_change_required, user_stop"


def test_the_baseline_and_target_carry_nfr_001s_four_numbers() -> None:
    """NFR-001 / AC-039 / OT-030 — 22 / 8 baseline, 12 / 3 target.

    thunder-viper's archive cannot supply either baseline number: it executed
    on the 4.7.3 cache, so its state.json cycle counter stayed at 0 for all 22
    cycles and it wrote no inspect_modes. Recording them here is what lets
    measure-run.py and the F6 report print the same two numbers.
    """
    assert vocab.THUNDER_VIPER_BASELINE == {
        "run": "thunder-viper",
        "grind_cycles": 22,
        "post_verification_cycles": 8,
    }
    assert vocab.CONVERGENCE_TARGET == {
        "grind_cycles": 12,
        "post_verification_cycles": 3,
    }
    # The target must actually be an improvement, or "meets_target" is noise.
    for key in ("grind_cycles", "post_verification_cycles"):
        assert vocab.CONVERGENCE_TARGET[key] < vocab.THUNDER_VIPER_BASELINE[key]


def test_the_prefix_partition_covers_this_runs_own_spec() -> None:
    """Every `XX-NNN` namespace in a shipped spec is on exactly one side.

    `tests/test_evidence.py::test_every_id_prefix_in_a_real_spec_is_classified`
    sweeps the real specs and reports a prefix belonging to NEITHER set. It
    named `OBS` and `RA` in `forge-specs/foundry-run-fallout/spec.md`, which is
    the partition doing its job: a new family falls into the gap and the sweep
    says so rather than letting it through.

    Both are NON-requirements, and the direction matters in one direction only.
    Adding them to `REQUIREMENT_ID_PREFIXES` would make an observation and a
    research-audit roster row into things the DONE gate counts and the verdict
    synthesis writes a row for — a run could then never reach DONE, because
    `OBS-026` is a finding and not a promise. Adding them here costs nothing:
    the two sets are disjoint by construction, asserted below.
    """
    assert {"OBS", "RA"} <= vocab.NON_REQUIREMENT_ID_PREFIXES
    assert not (vocab.NON_REQUIREMENT_ID_PREFIXES
                & vocab.REQUIREMENT_ID_PREFIXES), (
        "a prefix on both sides makes the partition meaningless — the sweep "
        "would report it as classified while two readers disagree about "
        "whether it is a requirement"
    )
    assert not vocab.REQUIREMENT_ID_RE.findall("OBS-026 and RA-1"), (
        "the requirement grammar is built FROM the requirement set, so a "
        "non-requirement prefix must not match it"
    )
    assert vocab.REQUIREMENT_ID_RE.findall("FR-014 and AC-024") == [
        "FR-014", "AC-024",
    ]


# ---------------------------------------------------------------------------
# fallout GI-033 / D-021 / D-035 (concern C-033) — the concern ledger's
# lifecycle vocabulary.
# ---------------------------------------------------------------------------


def test_concern_statuses_is_the_three_member_lifecycle_in_order() -> None:
    """CT-001 / GI-013 — hand-built, and NOT compared against `tools/concerns.py`.

    Casting 1 repoints that module to import from here in this same wave, so a
    parity assertion would be comparing the declaration to itself and then, a
    commit later, to nothing. The values below are typed out because the whole
    point of the move is that this file is now where they are decided.

    ORDER IS THE LIFECYCLE, which is why it is a tuple: a concern opens, is
    dispatched to the casting its target names (ST-003), and is closed with a
    reason (ST-004). Refusal hints read out in that order.
    """
    assert vocab.CONCERN_STATUSES == ("open", "dispatched", "closed")
    assert isinstance(vocab.CONCERN_STATUSES, tuple)
    assert len(vocab.CONCERN_STATUSES) == 3
    assert len(set(vocab.CONCERN_STATUSES)) == 3


def test_each_concern_status_constant_is_its_own_member_of_the_set() -> None:
    """The three names, unpacked from the declaration rather than re-typed.

    A constant spelled beside the set instead of taken out of it is how a
    rename leaves a name pointing at a value the set no longer holds — and a
    door comparing against that name then refuses on a status nothing writes.
    """
    assert vocab.CONCERN_STATUS_OPEN == "open"
    assert vocab.CONCERN_STATUS_DISPATCHED == "dispatched"
    assert vocab.CONCERN_STATUS_CLOSED == "closed"
    assert (
        vocab.CONCERN_STATUS_OPEN,
        vocab.CONCERN_STATUS_DISPATCHED,
        vocab.CONCERN_STATUS_CLOSED,
    ) == vocab.CONCERN_STATUSES


def test_dispatched_is_a_status_of_its_own_and_not_a_kind_of_open(tmp_path) -> None:
    """GI-023 / FR-039 / ST-005 — the member most easily got wrong.

    The INSPECT door refuses while a cross-casting concern from the closing
    GRIND is unaddressed, and `dispatched` is the mark the co-dispatch set
    leaves when the concern reached the casting that owns it — so it is
    addressed by definition. A rung that treated it as still open would hold
    the phase shut over work already handed to its owner, which is the
    direction that stalls a run rather than the one that lets a defect through.
    """
    assert vocab.CONCERN_STATUS_DISPATCHED != vocab.CONCERN_STATUS_OPEN
    assert vocab.CONCERN_STATUS_DISPATCHED in vocab.CONCERN_STATUSES

    # Driven through the leaf reader the door consults, so this is the
    # PROPERTY and not merely two strings being different. One record per
    # member, all three cross-casting; only the open one is unaddressed.
    from foundry_mcp.tools import foundry_state as fs

    run_dir = tmp_path
    (run_dir / "concerns.json").write_text(json.dumps({"concerns": [
        {"id": f"C-{n}", "status": status, "source_casting": 10,
         "target_casting_id": 4, "cycle": 2}
        for n, status in enumerate(vocab.CONCERN_STATUSES, start=1)
    ]}), encoding="utf-8")

    unaddressed = fs.open_cross_casting_concerns(
        run_dir, status_open=vocab.CONCERN_STATUS_OPEN
    )
    assert [c["id"] for c in unaddressed] == ["C-1"]


def test_the_leaf_reader_still_takes_the_member_rather_than_defaulting_to_it() -> None:
    """fallout GI-033 (concern C-033) — and the reason is not a preference.

    Now that the member is a leaf, a default on
    `foundry_state.open_cross_casting_concerns` looks like the tidy next step.
    It is not available: `foundry_state` may import NOTHING from its own
    package at any depth — that is the whole of the leaf contract, pinned by
    `test_report.py::test_foundry_state_still_imports_nothing_from_its_own_package`,
    and it is what lets `scripts/measure-run.py` read that module with no
    package installed. So the member is passed IN, exactly as `modes`,
    `shape_problem` and `no_ui_meaning` are, and this pins that it stays that
    way rather than acquiring a re-typed literal as a default.
    """
    from foundry_mcp.tools import foundry_state as fs

    signature = inspect.signature(fs.open_cross_casting_concerns)
    status_open = signature.parameters["status_open"]
    assert status_open.kind is inspect.Parameter.KEYWORD_ONLY
    assert status_open.default is inspect.Parameter.empty, (
        "a default here would have to be a literal re-typed in the leaf, "
        "because the leaf may not import the module that declares it"
    )
