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
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
import sys
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
    from foundry_mcp.tools import foundry_orchestrator as fo

    assert fo.VALID_STREAMS <= vocab.STREAM_WIRE_IDS, (
        f"narrowed: {fo.VALID_STREAMS - vocab.STREAM_WIRE_IDS}"
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
    assert vocab.OBSERVATION_CLASSES == frozenset(OBSERVATION_PREDICATES)
    assert len(vocab.OBSERVATION_CLASSES) == 4


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
    from foundry_mcp.tools import foundry_orchestrator as fo
    from foundry_mcp.tools import foundry_state

    run_name = "vocab-denylist-run"
    fdir = tmp_path / "foundry-archive" / run_name
    (fdir / "castings").mkdir(parents=True, exist_ok=True)
    (fdir / "state.json").write_text(
        json.dumps({"phase": "F2", "cycle": 3}), encoding="utf-8"
    )

    monkeypatch.setattr(
        fo,
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


def test_defect_tiers_is_the_two_member_closed_vocabulary() -> None:
    assert vocab.DEFECT_TIERS == frozenset({"LIVE", "LATENT"})
    assert isinstance(vocab.DEFECT_TIERS, frozenset)


def test_tier_unknown_is_a_read_sentinel_and_not_a_writable_tier() -> None:
    """FR-051 — a door may never WRITE unknown; a reader must always see it.

    Enrolling the sentinel in DEFECT_TIERS would make it a value a filing
    stream could legally set, which is precisely the "I did not classify this"
    escape the tier exists to close.
    """
    assert vocab.TIER_UNKNOWN == "unknown"
    assert vocab.TIER_UNKNOWN not in vocab.DEFECT_TIERS
    assert vocab.DEFECT_TIER_OR_UNKNOWN == vocab.DEFECT_TIERS | {vocab.TIER_UNKNOWN}
    assert len(vocab.DEFECT_TIER_OR_UNKNOWN) == 3


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

    The LATENT half is the load-bearing one: LATENT stops blocking at TEMPER,
    NYQUIST and DONE, so a record that defaults to LATENT silently clears three
    gates on a defect nobody ever classified.
    """
    assert vocab.defect_tier(record) == expected
    assert vocab.defect_tier(record) in vocab.DEFECT_TIER_OR_UNKNOWN


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


@pytest.mark.parametrize(
    "path",
    [
        "plugins/foundry/mcp-server/src/foundry_mcp/schemas/vocab.py",
        "plugins/foundry/mcp-server/src/foundry_mcp/schemas/findings.py",
        "plugins/foundry/mcp-server/src/foundry_mcp/tools/foundry_orchestrator.py",
        "plugins/foundry/mcp-server/src/foundry_mcp/tools/foundry.py",
        "plugins/foundry/mcp-server/src/foundry_mcp/tools/foundry_handoff.py",
        "plugins/foundry/mcp-server/src/foundry_mcp/tools/evidence.py",
        "plugins/foundry/mcp-server/src/foundry_mcp/tools/foundry_validate.py",
        "plugins/foundry/mcp-server/src/foundry_mcp/server.py",
        # D-033 — the five gate-path modules the old basename alternation
        # missed. `report_status` in foundry_report.py IS the DONE
        # precondition and `_manifest_shape_problem` in foundry_spawn.py is
        # called by `foundry_gate`, so a diff moving either was being judged
        # by a DELTA roster.
        "plugins/foundry/mcp-server/src/foundry_mcp/tools/foundry_report.py",
        "plugins/foundry/mcp-server/src/foundry_mcp/tools/foundry_spawn.py",
        "plugins/foundry/mcp-server/src/foundry_mcp/tools/foundry_state.py",
        "plugins/foundry/mcp-server/src/foundry_mcp/tools/display.py",
        "plugins/foundry/mcp-server/src/foundry_mcp/tools/citation.py",
        # And the modules no report has named yet, which is the point of
        # matching the package rather than a roster of basenames.
        "plugins/foundry/mcp-server/src/foundry_mcp/tools/validation.py",
        "plugins/foundry/mcp-server/src/foundry_mcp/tools/intent_coverage.py",
        "plugins/foundry/mcp-server/src/foundry_mcp/tools/worktree_helpers.py",
        "plugins/foundry/mcp-server/src/foundry_mcp/parsers/spec.py",
        "plugins/foundry/agents/assayer.md",
        "plugins/foundry/agents/tracer.md",
        "plugins/foundry/skills/prove/SKILL.md",
        "plugins/foundry/skills/trace/SKILL.md",
        "plugins/foundry/commands/start.md",
        # D-118 — the prose the streams LOAD, which the directory-by-directory
        # roster missed for the same reason D-033's basename roster missed the
        # gate modules. `verification-patterns.md` is pulled in as a binding
        # contract by `agents/tracer.md` and `agents/assayer.md`;
        # `lead-discipline.md` by `commands/start.md`. A GRIND whose only
        # touched file was the tracer's own verification contract was
        # recording DELTA.
        "plugins/foundry/references/verification-patterns.md",
        "plugins/foundry/references/lead-discipline.md",
        # D-118 — the validators a stream shells out to. The TEST-01
        # adjudicator runs the first as its Layer 1 and halts on a non-zero
        # exit; `Foundry-Intent-Coverage` runs the second's in-package twin.
        "plugins/foundry/scripts/validate-test-observations.py",
        "plugins/foundry/scripts/validate-intent-coverage.py",
    ],
)
def test_every_path_fr_032_names_is_a_verifier_path(path: str) -> None:
    """FR-032's minimum match set, path by path.

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
        # The server's OWN tests. D-033 widened the rule to the whole
        # `foundry_mcp/` package and stopped there on purpose: these are the
        # pins, not the judgement, and the TEST stream re-runs them at every
        # width. They are also the file a self-targeting GRIND cycle touches
        # most, so keeping them out is what leaves that run a real DELTA case.
        "plugins/foundry/mcp-server/tests/test_vocab.py",
        "plugins/foundry/mcp-server/tests/conftest.py",
        "README.md",
        "plugins/foundry/README.md",
        "src/myschemas.py",          # `schemas` inside a name is not a segment
        "docs/agents.md",            # `agents` as a FILE is not the directory
        "skills/README.md",          # a skill dir's non-SKILL file
        "src/myfoundry_mcp/tools/x.py",  # `foundry_mcp` inside a name, again
        "",
    ],
)
def test_an_ordinary_path_is_not_a_verifier_path(path: str) -> None:
    """The rule has to be able to say no, or every GRIND cycle runs FULL.

    DELTA exists to cut the ~1M-token cost of a five-stream INSPECT; a
    predicate that over-matches quietly deletes that saving while still
    reporting DELTA as available.
    """
    assert not vocab.is_verifier_path(path)


def test_the_whole_server_package_is_verifier_machinery() -> None:
    """D-033, driven over the package as it stands rather than over a roster.

    The reported defect was an ENUMERATION going stale: five `tools/`
    basenames, so `foundry_report.py` — whose `report_status` is the DONE
    precondition — answered False. A parametrized list of the five that were
    missing would pass while the sixth module added next week misses again, so
    the pin walks the real package and asserts every module in it answers True.
    """
    package = (
        REPO_ROOT / "plugins" / "foundry" / "mcp-server" / "src" / "foundry_mcp"
    )
    assert package.is_dir(), f"{package} is gone; point this pin at the server"

    modules = sorted(
        p for p in package.rglob("*.py") if "__pycache__" not in p.parts
    )
    assert len(modules) >= 20, (
        f"only {len(modules)} modules found under {package}; the walk is not "
        f"reaching the package, so this pin would prove nothing"
    )

    missed = [
        str(p.relative_to(REPO_ROOT))
        for p in modules
        if not vocab.is_verifier_path(str(p.relative_to(REPO_ROOT)))
    ]
    assert not missed, (
        f"{missed} sit inside the server package and answer False, so a GRIND "
        f"diff moving one would be judged by a DELTA roster (ST-006). That is "
        f"D-033: widen VERIFIER_PATH_PATTERNS, do not add rows here."
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


def test_every_prose_file_the_shipped_agents_load_is_a_verifier_path() -> None:
    """D-118, derived from the load instructions rather than from a roster.

    The reported defect was the PROSE half of D-033's enumeration problem.
    `VERIFIER_PATH_PATTERNS` named `agents/`, `skills/` and `commands/` one
    directory at a time, so `references/verification-patterns.md` — which
    `agents/tracer.md` and `agents/assayer.md` load as a binding contract —
    and `references/lead-discipline.md` — which `commands/start.md` loads —
    both answered False. Driven: a GRIND whose only touched file was the
    tracer's own verification contract recorded mode DELTA, rule delta, and a
    roster of trace/prove/test. The machinery that judges the build had moved
    and a narrow INSPECT judged the move.

    Adding two rows to the parametrized list above would fix those two files
    and keep the class, so this pin does not name files at all: it reads what
    the shipped prose TELLS an agent to load and asserts every target answers
    True. A contract file added to `references/` — or to a directory nobody has
    invented yet — fails here the moment an agent names it, which is the
    property the constant itself cannot have while the PURITY RULE forbids it
    the filesystem.
    """
    plugin_root = REPO_ROOT / "plugins" / "foundry"
    assert plugin_root.is_dir(), f"{plugin_root} is gone; point this pin at the plugin"

    corpus = sorted(
        p for glob in _SHIPPED_PROSE_GLOBS for p in plugin_root.glob(glob)
    )
    assert len(corpus) >= 20, (
        f"only {len(corpus)} prose files found under {plugin_root}; the walk is "
        f"not reaching the corpus, so this pin would prove nothing"
    )

    # {loaded repo-relative path: the documents that load it}
    loaded: dict[str, set[str]] = {}
    for doc in corpus:
        for rel in _PLUGIN_ROOT_MD_LOAD.findall(doc.read_text(encoding="utf-8")):
            target = plugin_root / rel
            if not target.is_file():
                continue  # a renamed or illustrative path proves nothing
            loaded.setdefault(
                str(target.resolve().relative_to(REPO_ROOT)), set()
            ).add(str(doc.relative_to(plugin_root)))

    assert len(loaded) >= 4, (
        f"only {sorted(loaded)} harvested; the shipped prose loads more than "
        f"that, so the regex has stopped matching the spelling the prose uses "
        f"and this pin would pass while proving nothing"
    )
    assert any(p.startswith("plugins/foundry/references/") for p in loaded), (
        f"no references/ target harvested from {sorted(loaded)}; those two "
        f"files are the D-118 instance, so a harvest without them cannot "
        f"witness the regression"
    )

    missed = {p: sorted(by) for p, by in sorted(loaded.items()) if not vocab.is_verifier_path(p)}
    assert not missed, (
        f"{missed} are loaded as binding contracts by the documents listed "
        f"beside them, yet answer False — so a GRIND diff moving one would be "
        f"judged by a DELTA roster (ST-006). Widen VERIFIER_PATH_PATTERNS. If "
        f"a target here is genuinely NOT a contract (a README an agent merely "
        f"cites), say so in the D-118 note in vocab.py and narrow this harvest "
        f"deliberately — do not silence it by dropping the row."
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


@pytest.mark.parametrize(
    "path,expected",
    [
        ("plugins/foundry/mcp-server/tests/test_vocab.py", True),
        ("tests/conftest.py", True),
        ("conftest.py", True),
        ("pkg/thing_test.py", True),
        ("tests/fixtures/measure_run/state_cycle_3.json", True),
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
    """
    assert vocab.is_test_file(path) is expected


def test_the_repos_own_pytest_discovery_setting_is_covered() -> None:
    """FR-034 says "the repo's pytest discovery patterns" — read the setting.

    mcp-server/pyproject.toml declares `python_files`; every glob it names must
    be recognised here, or the lane counts a file pytest collects as source.
    """
    text = (REPO_ROOT / "plugins" / "foundry" / "mcp-server" / "pyproject.toml").read_text(
        encoding="utf-8"
    )
    match = re.search(r"python_files\s*=\s*\[([^\]]*)\]", text)
    assert match is not None, "pyproject.toml no longer declares python_files"
    for glob in re.findall(r'"([^"]+)"', match.group(1)):
        sample = glob.replace("*", "sample")
        assert vocab.is_test_file(sample), (
            f"pytest collects {glob} (sample: {sample}) but is_test_file calls "
            f"it a source file, so it would consume the lead lane's one-file "
            f"budget"
        )


# ---------------------------------------------------------------------------
# GI-006 / CT-014 / ST-008 / NFR-001 — report, terminal state, baseline.
# ---------------------------------------------------------------------------


def test_report_required_sections_is_the_eleven_gi_006_names_in_order() -> None:
    """A TUPLE — GI-006 fixes the ORDER as well as the membership.

    report.json's top-level keys are these plus generated_at and run, and
    REPORT.md carries one `## ` heading per member in this sequence, so a
    frozenset here would leave the order undefined at both surfaces.
    """
    assert vocab.REPORT_REQUIRED_SECTIONS == (
        "verdict_matrix",
        "defects_by_tier_and_status",
        "latent_backlog",
        "unknown_tier_defects",
        "escalated_classes",
        "lead_fix_records",
        "inspect_modes_per_cycle",
        "spend_per_phase_and_cycle",
        "unreported_dispatches",
        "executing_versions",
        "baseline_comparison",
    )
    assert len(set(vocab.REPORT_REQUIRED_SECTIONS)) == 11, "no duplicate section"


def test_run_artifact_filenames_and_the_halted_state() -> None:
    assert vocab.SPEND_LEDGER_FILENAME == "spend.jsonl"
    assert vocab.REPORT_MD_FILENAME == "REPORT.md"
    assert vocab.REPORT_JSON_FILENAME == "report.json"
    # ST-008 — a named terminal state reached by a SUCCESSFUL transition, and
    # deliberately not "DONE".
    assert vocab.RUN_PHASE_HALTED == "HALTED"
    assert vocab.RUN_PHASE_HALTED != "DONE"


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
