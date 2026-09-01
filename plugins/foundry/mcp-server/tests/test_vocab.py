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
import os
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
