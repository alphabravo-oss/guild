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
    """Every shipped module naming `escalation.json` as a string constant.

    DISCOVERED, never listed. A hand list is what the three per-instance fixes
    of this class each amounted to: the fix landed on the readers somebody
    remembered. The AST walk finds a FOURTH reader the day it is written.

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
        if any(
            isinstance(node, ast.Constant) and node.value == "escalation.json"
            for node in ast.walk(tree)
        ):
            found[rel] = tree
    return found


#: The readers that existed when this pin was written. Asserted as a SUBSET of
#: what discovery finds, never as an equality: a new reader must be checked by
#: the assertions below, not merely noticed here. Naming them is what stops a
#: silently-collapsed discovery from passing forever (the D-202 lesson).
_KNOWN_ESCALATION_READERS = frozenset({
    "plugins/foundry/mcp-server/src/foundry_mcp/tools/foundry_orchestrator.py",
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
        assert imported, (
            f"{rel} reads escalation.json but does not import "
            f"`escalation_status` from schemas.vocab. Every reader of that "
            f"file resolves the status through the ONE resolver — a reader "
            f"with its own is how D-214 and D-215 happened, three cycles "
            f"apart, in two different files."
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
    from foundry_mcp.tools import foundry_orchestrator as fo
    from foundry_mcp.tools.foundry_report import _read_escalated_classes

    run_dir = tmp_path / "foundry-archive" / "pin-run"
    run_dir.mkdir(parents=True)
    (run_dir / "escalation.json").write_text(
        json.dumps(_FOUR_SHAPE_ESCALATION_DOCUMENT), encoding="utf-8"
    )
    still_escalated = ["bogus-class", "live-class", "shapeless-class"]

    # Reader 1 — the deciding read behind Foundry-Gate('done').
    persisted = fo._persisted_escalations(
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
        # Collected by the declared configuration: `test_*.py` under `tests`.
        ("plugins/foundry/mcp-server/tests/test_vocab.py", True),
        ("tests/test_real.py", True),
        ("packages/api/tests/test_sweeper.py", True),
        # pytest's own fixed filename, loaded from the rootdir down.
        ("tests/conftest.py", True),
        ("conftest.py", True),
        # D-222, escape 1: no pyproject.toml in this repo names `*_test.py`,
        # and the driven commit shipped 400 lines of it beside a 5-line source
        # file through a lane bounded at one file and twenty lines.
        ("pkg/thing_test.py", False),
        ("src/helpers_test.py", False),
        # D-222, escape 2: a `tests` segment is not a licence over every file
        # beneath it. `python_files` still has to match the basename.
        ("src/tests/production_helper.py", False),
        ("src/tests/__init__.py", False),
        ("mypkg/tests/big_module.py", False),
        ("tests/fixtures/measure_run/state_cycle_3.json", False),
        # `test_*.py` OUTSIDE every declared testpath is not collected either.
        ("test_sample.py", False),
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
    unchanged however broad the recogniser became: `_TEST_BASENAME_RES` also
    matched `*_test.py`, and `TEST_DIRECTORY_SEGMENT` matched any path segment
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


#: The shapes D-222 was driven on, plus the one file the driven
#: `pytest --collect-only` actually collected. Materialised into a throwaway
#: repo below and judged against real pytest, so the corpus is not a
#: restatement of the predicate under test.
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
    """
    (tmp_path / "pyproject.toml").write_text(
        _pytest_ini_options_block(), encoding="utf-8"
    )
    for rel in _FR034_CANDIDATE_FILES:
        target = tmp_path / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        if rel == "tests/test_real.py":
            target.write_text("def test_real():\n    assert True\n", encoding="utf-8")
        elif rel.endswith(".json"):
            target.write_text("{}\n", encoding="utf-8")
        else:
            target.write_text("VALUE = 1\n", encoding="utf-8")

    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
         "--collect-only"],
        cwd=str(tmp_path), capture_output=True, text=True, timeout=300,
    )
    collected = {
        line.strip().split("::", 1)[0]
        for line in proc.stdout.splitlines()
        if "::" in line and not line.startswith(" ")
    }
    assert collected == {"tests/test_real.py"}, (
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
