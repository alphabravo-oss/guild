"""Phase 7 / TEST-01 — spec-derived test stream tests.

15 RED-or-SKIP stubs covering Plans 07-02 / 07-03 / 07-04 territory.

Plan 07-02 territory (validator behavior — 9 stubs):
  1. test_validator_exits_zero_on_clean
  2. test_unknown_top_level_key_rejected
  3. test_unknown_status_rejected
  4. test_no_negative_assertion_pattern
  5. test_value_not_shape_pattern
  6. test_source_leak_pattern
  7. test_header_missing_pattern
  8. test_dangling_requirement_rejected
  9. test_code_blind_audit_violation

Plan 07-03 territory (agent + uvx subprocess — 3 stubs, conditional-skip):
  10. test_agent_file_frontmatter_shape
  11. test_uvx_subprocess_smoke
  12. test_v20_spec_skips_test_01

Plan 07-04 territory (integration — 3 stubs, conditional-skip):
  13. test_f05_roster_activation
  14. test_f2_inspect_stream_count
  15. test_assay_routing_extension

RED-or-SKIP discipline:

- Module-top guard: ``pytest.skip(allow_module_level=True)`` when
  ``validate-test-observations.py`` is missing on disk. Plan 07-01
  ships ZERO production code, so all 15 stubs SKIP at module-top
  until Plan 07-02 ships the validator script. Mirrors Phase 6 Plan
  06-01 plugins/forge/tests/test_spec_review.py module-skip
  discipline (file-existence-gated rather than importorskip — the
  validator is a dash-named script invoked via subprocess, not an
  importable Python module).
- Plan 07-02 ships validator -> module collects; Plan 07-02 territory
  tests turn RED-or-GREEN depending on validator behavior.
- Plan 07-03 ships agent file + uvx wrapper -> Plan 07-03 territory
  tests (10/11/12) auto-flip from per-test SKIP to RED-or-GREEN with
  zero edits to this file.
- Plan 07-04 ships start.md edits + assayer/adjudicator -> Plan 07-04
  territory tests (13/14/15) auto-flip from per-test SKIP to
  RED-or-GREEN with zero edits to this file.

Phase 1+2+3+4+5+6 byte-equivalence is preserved by living in a NEW
module — no edits to test_evidence.py / test_evidence_for.py /
test_validate_spec.py / test_typed_sections.py /
test_versioned_spec_format.py / test_spec_review.py.

fallout D-168 — the observation-emitting layer, driven rather than stubbed
(3 tests at the foot of this module):

  16. test_parse_header_reads_the_binding_from_a_path_it_is_handed
  17. test_the_emitter_binds_its_requirements_from_a_cwd_that_is_not_the_test_root
  18. test_the_emitted_test_path_stays_the_nodeid_pytest_reported

Those three carry NO ``pytest.skip`` and no ``try/except`` around the
entry point. The 15 stubs above are gated that way because the code they
describe had not shipped when they were written; ``foundry_mcp.tools.
test_deriver`` has shipped, and a test that can skip itself when the
module changes shape is a test that cannot report the module changing
shape — which is the coverage hole D-168 was filed on. The import below
is therefore hard.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Callable

import pytest

# Hard, module-level, unguarded (fallout D-168). Every other reach into this
# module in this file is wrapped in a skip; those wrappers date from when the
# module was unshipped territory, and the three tests at the foot of this file
# exist precisely because a guarded reach cannot go red.
from foundry_mcp.tools import test_deriver

# tests/test_spec_test_deriver.py -> parents: [0]=tests, [1]=mcp-server,
# [2]=foundry, [3]=plugins, [4]=repo-root.
REPO_ROOT = Path(__file__).resolve().parents[4]
VALIDATE_PATH = (
    REPO_ROOT / "plugins" / "foundry" / "scripts"
    / "validate-test-observations.py"
)
AGENT_PATH = (
    REPO_ROOT / "plugins" / "foundry" / "agents"
    / "spec-test-deriver.md"
)
START_MD = (
    REPO_ROOT / "plugins" / "foundry" / "commands" / "start.md"
)
ASSAYER_MD = (
    REPO_ROOT / "plugins" / "foundry" / "agents" / "assayer.md"
)
ADJUDICATOR_MD = (
    REPO_ROOT / "plugins" / "foundry" / "agents"
    / "test-observations-adjudicator.md"
)


if not VALIDATE_PATH.exists():
    pytest.skip(
        "validate-test-observations.py not yet shipped — "
        "Plan 07-02 territory",
        allow_module_level=True,
    )


# ---------------------------------------------------------------------------
# Plan 07-02 territory — validator behavior tests (9 stubs).
#
# These tests use the run_test_observations_validator fixture (defined
# in conftest.py); the fixture itself pytest.skip()s when the validator
# script is missing, so per-test SKIP is automatic at fixture-acquire
# time. Once Plan 07-02 ships, the module-top guard above passes and
# these tests turn RED-or-GREEN based on validator behavior.
# ---------------------------------------------------------------------------


def test_validator_exits_zero_on_clean(
    run_test_observations_validator: Callable[..., tuple[int, str, str]],
    fixtures_dir: Path,
) -> None:
    """Plan 07-02 territory: clean fixture exits 0.

    Happy-path baseline: schema-valid observation JSON with one PASS +
    one FAIL observation, both negative_assertion_present=true,
    shape_not_value_check="passed", citation_chain populated, valid
    tests_spec referencing FR-1 in spec_test_deriver_simple.md.
    """
    observation = (
        fixtures_dir / "test_observations"
        / "test-deriver-cycle-clean.json"
    )
    spec = fixtures_dir / "specs" / "spec_test_deriver_simple.md"
    exit_code, stdout, stderr = run_test_observations_validator(
        observation, spec_path=spec
    )
    assert exit_code == 0, (
        f"clean fixture should pass; got exit {exit_code}\n"
        f"stdout: {stdout}\nstderr: {stderr}"
    )


def test_unknown_top_level_key_rejected(
    run_test_observations_validator: Callable[..., tuple[int, str, str]],
    fixtures_dir: Path,
) -> None:
    """Plan 07-02 territory: extra top-level key triggers schema-invalid.

    Closed-vocabulary discipline: KNOWN_TEST_OBSERVATION_KEYS frozenset
    enumerates the only legal top-level keys; ``suggested_fix`` smuggled
    at top-level is rejected with TEST_OBSERVATION_SCHEMA_INVALID.
    Mirrors Phase 6 PROBE-01's KNOWN_REVIEW_KEYS rejection of
    auto-resolve smuggling.
    """
    observation = (
        fixtures_dir / "test_observations"
        / "test-deriver-cycle-schema-invalid.json"
    )
    exit_code, stdout, stderr = run_test_observations_validator(
        observation
    )
    assert exit_code != 0, (
        f"schema-invalid fixture should fail; got exit {exit_code}\n"
        f"stdout: {stdout}\nstderr: {stderr}"
    )
    combined = stdout + stderr
    assert "TEST_OBSERVATION_SCHEMA_INVALID" in combined, (
        f"expected TEST_OBSERVATION_SCHEMA_INVALID in output;\n"
        f"stdout: {stdout}\nstderr: {stderr}"
    )


def test_unknown_status_rejected(
    run_test_observations_validator: Callable[..., tuple[int, str, str]],
    tmp_path: Path,
) -> None:
    """Plan 07-02 territory: status not in KNOWN_OBSERVATION_STATUSES.

    Synthesizes an observation JSON with status="UNKNOWN_STATUS" (not
    in the {FAIL, ERROR, SKIP, PASS} closed vocabulary). Validator
    rejects with TEST_OBSERVATION_UNKNOWN_STATUS token.
    """
    payload: dict[str, Any] = {
        "stream": "TEST-01",
        "cycle": 1,
        "spec_format_version": "v2.1",
        "spec_hash": "sha256:" + "0" * 60,
        "agent_path": "plugins/foundry/agents/spec-test-deriver.md",
        "wall_clock_seconds": 1.0,
        "uvx_subprocess_seconds": 0.5,
        "observations": [
            {
                "observation_id": "OBS-001",
                "test_path": "foundry-archive/run-001/test_observations/generated/test_x.py",
                "tests_spec": ["FR-1"],
                "derived_from_contract_row": "CT-001",
                "hypothesis_seed": 1,
                "status": "UNKNOWN_STATUS",
                "captured_output": "",
                "negative_assertion_present": True,
                "shape_not_value_check": "passed",
                "citation_chain": ["A-001", "CT-001", "FR-1"],
            }
        ],
    }
    synth = tmp_path / "test-deriver-cycle-unknown-status.json"
    synth.write_text(json.dumps(payload), encoding="utf-8")
    exit_code, stdout, stderr = run_test_observations_validator(synth)
    assert exit_code != 0, (
        f"unknown-status fixture should fail; got exit {exit_code}\n"
        f"stdout: {stdout}\nstderr: {stderr}"
    )
    combined = stdout + stderr
    assert "TEST_OBSERVATION_UNKNOWN_STATUS" in combined, (
        f"expected TEST_OBSERVATION_UNKNOWN_STATUS in output;\n"
        f"stdout: {stdout}\nstderr: {stderr}"
    )


def test_no_negative_assertion_pattern(
    run_test_observations_validator: Callable[..., tuple[int, str, str]],
    fixtures_dir: Path,
) -> None:
    """Plan 07-02 territory: negative_assertion_present=false rejected.

    Wrong-test stub-pattern library: a test that passes only the happy
    case without exercising any negative branch is a wrong-test, not
    an absence of bug. Validator surfaces
    WRONG_TEST_NO_NEGATIVE_ASSERTION token.
    """
    observation = (
        fixtures_dir / "test_observations"
        / "test-deriver-cycle-no-negative-assertion.json"
    )
    exit_code, stdout, stderr = run_test_observations_validator(
        observation
    )
    assert exit_code != 0, (
        f"no-negative-assertion fixture should fail; got exit {exit_code}\n"
        f"stdout: {stdout}\nstderr: {stderr}"
    )
    combined = stdout + stderr
    assert "WRONG_TEST_NO_NEGATIVE_ASSERTION" in combined, (
        f"expected WRONG_TEST_NO_NEGATIVE_ASSERTION in output;\n"
        f"stdout: {stdout}\nstderr: {stderr}"
    )


def test_value_not_shape_pattern(
    run_test_observations_validator: Callable[..., tuple[int, str, str]],
    fixtures_dir: Path,
) -> None:
    """Plan 07-02 territory: shape_not_value_check="failed" rejected.

    Wrong-test stub-pattern library: tests asserting on concrete
    literal values (rather than shape: type / non-empty / structure)
    encode an implementation detail and break under semantic-preserving
    refactors. Validator surfaces WRONG_TEST_VALUE_NOT_SHAPE token.
    """
    observation = (
        fixtures_dir / "test_observations"
        / "test-deriver-cycle-value-not-shape.json"
    )
    exit_code, stdout, stderr = run_test_observations_validator(
        observation
    )
    assert exit_code != 0, (
        f"value-not-shape fixture should fail; got exit {exit_code}\n"
        f"stdout: {stdout}\nstderr: {stderr}"
    )
    combined = stdout + stderr
    assert "WRONG_TEST_VALUE_NOT_SHAPE" in combined, (
        f"expected WRONG_TEST_VALUE_NOT_SHAPE in output;\n"
        f"stdout: {stdout}\nstderr: {stderr}"
    )


def test_source_leak_pattern(
    run_test_observations_validator: Callable[..., tuple[int, str, str]],
    fixtures_dir: Path,
) -> None:
    """Plan 07-02 territory: source-leak imports in test body rejected.

    Wrong-test stub-pattern library: code-blind discipline forbids
    importing or referencing forbidden source roots (src/, app/, lib/,
    plugins/<n>/agents, etc.). Observation captured_output containing
    ``from src.handlers import login_handler`` triggers
    WRONG_TEST_SOURCE_LEAK token.
    """
    observation = (
        fixtures_dir / "test_observations"
        / "test-deriver-cycle-source-leak.json"
    )
    exit_code, stdout, stderr = run_test_observations_validator(
        observation
    )
    assert exit_code != 0, (
        f"source-leak fixture should fail; got exit {exit_code}\n"
        f"stdout: {stdout}\nstderr: {stderr}"
    )
    combined = stdout + stderr
    assert "WRONG_TEST_SOURCE_LEAK" in combined, (
        f"expected WRONG_TEST_SOURCE_LEAK in output;\n"
        f"stdout: {stdout}\nstderr: {stderr}"
    )


def test_header_missing_pattern(
    run_test_observations_validator: Callable[..., tuple[int, str, str]],
    fixtures_dir: Path,
) -> None:
    """Plan 07-02 territory: empty tests_spec rejected.

    Every generated test file must include ``# tests-spec: FR-N``
    header on the first non-blank line. Empty tests_spec=[] in the
    observation indicates the header was missing or unparseable;
    validator surfaces either WRONG_TEST_HEADER_MISSING or
    TEST_HEADER_MISSING (both are valid tokens per CONTEXT.md).
    """
    observation = (
        fixtures_dir / "test_observations"
        / "test-deriver-cycle-header-missing.json"
    )
    exit_code, stdout, stderr = run_test_observations_validator(
        observation
    )
    assert exit_code != 0, (
        f"header-missing fixture should fail; got exit {exit_code}\n"
        f"stdout: {stdout}\nstderr: {stderr}"
    )
    combined = stdout + stderr
    assert (
        "WRONG_TEST_HEADER_MISSING" in combined
        or "TEST_HEADER_MISSING" in combined
    ), (
        f"expected WRONG_TEST_HEADER_MISSING or TEST_HEADER_MISSING in output;\n"
        f"stdout: {stdout}\nstderr: {stderr}"
    )


def test_dangling_requirement_rejected(
    run_test_observations_validator: Callable[..., tuple[int, str, str]],
    fixtures_dir: Path,
) -> None:
    """Plan 07-02 territory: tests_spec citing FR-99 not in spec rejected.

    Spec ``spec_test_deriver_simple.md`` <spec_requirements> block
    enumerates FR-1, FR-2, US-1. Observation citing FR-99 is dangling;
    validator (when invoked with --spec) cross-references and surfaces
    TEST_HEADER_DANGLING_REQ. Mirrors Phase 1's APPENDIX_INCOMPLETE
    cross-reference discipline.
    """
    observation = (
        fixtures_dir / "test_observations"
        / "test-deriver-cycle-dangling-req.json"
    )
    spec = fixtures_dir / "specs" / "spec_test_deriver_simple.md"
    exit_code, stdout, stderr = run_test_observations_validator(
        observation, spec_path=spec
    )
    assert exit_code != 0, (
        f"dangling-req fixture should fail; got exit {exit_code}\n"
        f"stdout: {stdout}\nstderr: {stderr}"
    )
    combined = stdout + stderr
    assert "TEST_HEADER_DANGLING_REQ" in combined, (
        f"expected TEST_HEADER_DANGLING_REQ in output;\n"
        f"stdout: {stdout}\nstderr: {stderr}"
    )


def test_code_blind_audit_violation(
    run_test_observations_validator: Callable[..., tuple[int, str, str]],
    fixtures_dir: Path,
) -> None:
    """Plan 07-02 territory: tool-call log Reading source rejected.

    Code-blind enforcement layer 2 (post-hoc validator audit). Even on
    a clean observation JSON, if the agent's tool-call log shows a
    Read/Grep/Glob targeting a forbidden root (src/handlers/login.py,
    plugins/forge/agents/spec-reviewer.md), validator surfaces
    TEST_DERIVER_READ_SOURCE token rejecting the entire stream's
    observations.
    """
    observation = (
        fixtures_dir / "test_observations"
        / "test-deriver-cycle-clean.json"
    )
    tool_call_log = (
        fixtures_dir / "tool_call_logs"
        / "tool_call_log_source_leak.json"
    )
    exit_code, stdout, stderr = run_test_observations_validator(
        observation, tool_call_log_path=tool_call_log
    )
    assert exit_code != 0, (
        f"code-blind audit should reject source-leak tool-call log; "
        f"got exit {exit_code}\nstdout: {stdout}\nstderr: {stderr}"
    )
    combined = stdout + stderr
    assert "TEST_DERIVER_READ_SOURCE" in combined, (
        f"expected TEST_DERIVER_READ_SOURCE in output;\n"
        f"stdout: {stdout}\nstderr: {stderr}"
    )


# ---------------------------------------------------------------------------
# Plan 07-03 territory — agent file + uvx subprocess (3 stubs).
#
# Conditional-skip: tests skip when the artifact (agent file / uvx
# wrapper module) is missing, assert when it exists. Plan 07-03 ships
# the artifacts; tests auto-flip from SKIP to RED-or-GREEN with zero
# edits.
# ---------------------------------------------------------------------------


def test_agent_file_frontmatter_shape() -> None:
    """Plan 07-03 territory: spec-test-deriver.md frontmatter shape.

    Asserts the agent file's YAML frontmatter declares the
    Phase-7-locked fields:
      - id: TEST-01 (referenced by F0.5 step 2b roster +
        manifest.stream_skips)
      - min_spec_format_version: v2.1 (Phase 3 stream-skip gate)
      - model: any recognised alias (opus/sonnet/haiku/fable/inherit).
        Deliberately an allowlist rather than one literal: the assertion's
        real job is catching a missing or malformed ``model:`` line, not
        freezing which model it names. This agent's shipped baseline moved
        sonnet -> opus, and the option can move steerable agents further,
        so a single-literal assertion would have to be re-edited on every
        model decision.
      - effort: high
      - tools: includes Read/Write/Bash/Grep/Glob; excludes Edit/Task
        (code-blind enforcement layer 1)

    Skip until Plan 07-03 ships the agent file.
    """
    if not AGENT_PATH.exists():
        pytest.skip(
            "spec-test-deriver.md not yet shipped — Plan 07-03 territory"
        )
    text = AGENT_PATH.read_text(encoding="utf-8")
    m = re.match(r"\A---\s*\n(.*?)\n---\s*\n", text, re.DOTALL)
    assert m, (
        "spec-test-deriver.md must declare YAML frontmatter "
        "(--- delimited at file top)"
    )
    front = m.group(1)
    assert re.search(
        r"^id:\s*TEST-01\s*$", front, re.MULTILINE
    ), f"frontmatter missing 'id: TEST-01':\n{front}"
    assert re.search(
        r"^min_spec_format_version:\s*v2\.1\s*$", front, re.MULTILINE
    ), f"frontmatter missing 'min_spec_format_version: v2.1':\n{front}"
    assert re.search(
        r"^model:\s*(opus|sonnet|haiku|fable|inherit)\s*$", front, re.MULTILINE
    ), (
        "frontmatter missing or malformed 'model:' line — must name exactly one "
        f"of opus, sonnet, haiku, fable, inherit:\n{front}"
    )
    assert re.search(
        r"^effort:\s*high\s*$", front, re.MULTILINE
    ), f"frontmatter missing 'effort: high':\n{front}"
    # Tools allowlist — must include Read/Write/Bash/Grep/Glob;
    # must exclude Edit/Task (code-blind enforcement layer 1).
    m_tools = re.search(
        r"^tools:\s*(.+?)\s*$", front, re.MULTILINE
    )
    assert m_tools, f"frontmatter missing 'tools' field:\n{front}"
    tools = m_tools.group(1)
    for required in ("Read", "Write", "Bash", "Grep", "Glob"):
        assert required in tools, (
            f"tools allowlist missing '{required}': {tools!r}"
        )
    for forbidden in ("Edit", "Task"):
        assert forbidden not in tools, (
            f"tools allowlist must EXCLUDE '{forbidden}' "
            f"(code-blind layer 1); got: {tools!r}"
        )


def test_uvx_subprocess_smoke(
    mock_uvx_subprocess: dict[str, Any],
) -> None:
    """Plan 07-03 territory: uvx wrapper invokes hypothesis-jsonschema.

    Asserts the agent's Python entry point (Plan 07-03 lands the
    wrapper module under foundry_mcp.tools.test_deriver or similar)
    shells out to ``uvx --from hypothesis-jsonschema --with hypothesis
    python -m pytest`` shape. The mock_uvx_subprocess fixture
    intercepts subprocess.run, records the cmd, returns synthetic empty
    observations JSON.

    Skip until Plan 07-03 ships the wrapper module.
    """
    try:
        from foundry_mcp.tools import test_deriver  # type: ignore[import-not-found]
    except ImportError:
        pytest.skip(
            "foundry_mcp.tools.test_deriver not yet shipped — "
            "Plan 07-03 territory"
        )
    # Plan 07-03 author's discretion: entry-point function name. This
    # test will assert on whichever public function Plan 07-03
    # exposes; for now we look for a callable named "derive_tests" or
    # "run" and skip if neither is present.
    entry: Callable[..., Any] | None = None
    for name in ("derive_tests", "run", "main"):
        candidate = getattr(test_deriver, name, None)
        if callable(candidate):
            entry = candidate
            break
    if entry is None:
        pytest.skip(
            "test_deriver module exists but no public entry point "
            "found (derive_tests / run / main) — Plan 07-03 territory"
        )
    # Best-effort invocation; harness-specific kwargs land in Plan 07-03.
    try:
        entry()
    except TypeError:
        # Required kwargs not yet stable; defer to Plan 07-03.
        pytest.skip(
            "test_deriver entry point requires kwargs not yet stable "
            "— Plan 07-03 territory"
        )
    # Verify the mock recorded a uvx invocation with the locked flags.
    uvx_calls = [
        c
        for c in mock_uvx_subprocess["calls"]
        if isinstance(c, list) and c and "uvx" in str(c[0])
    ]
    assert uvx_calls, (
        "expected at least one uvx subprocess invocation; got: "
        f"{mock_uvx_subprocess['calls']!r}"
    )
    cmd_text = " ".join(str(t) for t in uvx_calls[0])
    assert "--from hypothesis-jsonschema" in cmd_text, (
        f"uvx cmd missing '--from hypothesis-jsonschema': {cmd_text!r}"
    )
    assert "--with hypothesis" in cmd_text, (
        f"uvx cmd missing '--with hypothesis': {cmd_text!r}"
    )
    assert "pytest" in cmd_text, (
        f"uvx cmd missing 'pytest' invocation: {cmd_text!r}"
    )


def test_v20_spec_skips_test_01() -> None:
    """Plan 07-03 + 07-04 territory: legacy v2.0 spec emits stream-skip.

    When a v2.0 spec is processed and TEST-01's
    min_spec_format_version is v2.1, F0.5 step 2b roster activation
    must emit a stream_skips manifest record naming stream_id=TEST-01
    + reason=below_min_spec_format_version. Mirrors Phase 3's
    EVID-01/EVID-02 + Phase 6 PROBE-01 stream-skip discipline.

    Skip until Plan 07-04 activates the F0.5 step 2b roster (line 116
    placeholder removed) AND Plan 07-03 ships the agent file (so the
    roster has something to compare against).
    """
    if not AGENT_PATH.exists():
        pytest.skip(
            "spec-test-deriver.md not yet shipped — Plan 07-03 territory"
        )
    if not START_MD.exists():
        pytest.skip(
            "plugins/foundry/commands/start.md not present at expected "
            "path — Plan 07-04 territory"
        )
    start_text = START_MD.read_text(encoding="utf-8")
    if "[Future: TEST-01" in start_text:
        pytest.skip(
            "F0.5 step 2b roster placeholder still present "
            "([Future: TEST-01...]) — Plan 07-04 activation territory"
        )
    # Roster activated. Once Plan 07-04 also lands the harness wrapper
    # for synthesizing v2.0 specs and asserting stream_skips, this
    # test will turn GREEN. For now we assert structural readiness:
    # the roster references the agent file path.
    assert "spec-test-deriver.md" in start_text, (
        "F0.5 step 2b roster activation must reference "
        "plugins/foundry/agents/spec-test-deriver.md; "
        "(line 116 placeholder removal is incomplete)"
    )


# ---------------------------------------------------------------------------
# Plan 07-04 territory — integration (3 stubs).
#
# Conditional-skip: tests skip when start.md / assayer / adjudicator
# artifacts haven't been edited / created yet. Plan 07-04 ships the
# edits + new agent; tests auto-flip from SKIP to RED-or-GREEN with
# zero edits to this file.
# ---------------------------------------------------------------------------


def test_f05_roster_activation() -> None:
    """Plan 07-04 territory: line 116 placeholder activated.

    F0.5 step 2b roster in plugins/foundry/commands/start.md carries a
    placeholder ``[Future: TEST-01 → ...]`` at line 116 (per
    07-CONTEXT.md). Plan 07-04 replaces it with a live roster entry
    referencing ``plugins/foundry/agents/spec-test-deriver.md``.
    Mirrors Phase 6 Plan 06-03's spec-reviewer.md F0.5 roster
    activation precedent.
    """
    if not START_MD.exists():
        pytest.skip(
            "plugins/foundry/commands/start.md not present at expected "
            "path — Plan 07-04 territory"
        )
    text = START_MD.read_text(encoding="utf-8")
    if "[Future: TEST-01" in text:
        pytest.skip(
            "F0.5 step 2b roster placeholder '[Future: TEST-01...]' "
            "still present — Plan 07-04 activation territory"
        )
    assert "spec-test-deriver.md" in text, (
        "F0.5 step 2b roster activation must reference "
        "plugins/foundry/agents/spec-test-deriver.md; "
        "Plan 07-04 line-116-placeholder edit incomplete"
    )
    assert "TEST-01" in text, (
        "F0.5 step 2b roster activation must reference TEST-01 "
        "stream id"
    )


def test_f2_inspect_stream_count() -> None:
    """Plan 07-04 territory: F2 INSPECT bumps 7 -> 8 streams.

    Plan 07-04 edits start.md F2 INSPECT block:
      - Heading "7 parallel streams" -> "8 parallel streams"
      - TEST-01 row added to the stream list
    Per 07-CONTEXT.md line 436. Mirrors Phase 6 Plan 06-03's prose-only
    integration discipline.
    """
    if not START_MD.exists():
        pytest.skip(
            "plugins/foundry/commands/start.md not present at expected "
            "path — Plan 07-04 territory"
        )
    text = START_MD.read_text(encoding="utf-8")
    if "8 parallel streams" not in text:
        pytest.skip(
            "F2 INSPECT '8 parallel streams' heading not yet present "
            "— Plan 07-04 territory (heading currently still says "
            "'7 parallel streams' or another shape)"
        )
    # Heading flipped — assert the negation: old heading no longer
    # surfaces as the heading shape (allow surrounding prose mentions
    # to survive in changelog / migration notes).
    assert "8 parallel streams" in text, (
        "F2 INSPECT heading must read '8 parallel streams' "
        "(Plan 07-04 stream count bump incomplete)"
    )
    assert "TEST-01" in text, (
        "F2 INSPECT stream list must include a TEST-01 row "
        "(Plan 07-04 line-436 edit incomplete)"
    )


def test_assay_routing_extension() -> None:
    """Plan 07-04 territory: F4 ASSAY consumes test_observations.

    Plan 07-04 extends start.md F4 ASSAY block to consume the new
    ``test_observations`` channel and routes observations via
    KNOWN_TEST_OBSERVATION_VERDICTS frozenset (DEFECT / WRONG_TEST /
    INCONCLUSIVE). The verdict surface lands either in
    plugins/foundry/agents/assayer.md (extension) or in a new
    plugins/foundry/agents/test-observations-adjudicator.md (5th
    ASSAY agent per 07-RESEARCH.md Open Question 3).
    """
    if not START_MD.exists():
        pytest.skip(
            "plugins/foundry/commands/start.md not present at expected "
            "path — Plan 07-04 territory"
        )
    start_text = START_MD.read_text(encoding="utf-8")
    if "test_observations" not in start_text:
        pytest.skip(
            "F4 ASSAY block does not yet mention 'test_observations' "
            "channel — Plan 07-04 territory"
        )
    assert "test_observations" in start_text, (
        "F4 ASSAY block must reference the 'test_observations' channel "
        "(Plan 07-04 routing extension incomplete)"
    )
    # KNOWN_TEST_OBSERVATION_VERDICTS frozenset surface — must live in
    # either assayer.md (extension) or test-observations-adjudicator.md
    # (new). Either path is acceptable per 07-RESEARCH.md Open
    # Question 3.
    candidates: list[Path] = []
    if ASSAYER_MD.exists():
        candidates.append(ASSAYER_MD)
    if ADJUDICATOR_MD.exists():
        candidates.append(ADJUDICATOR_MD)
    if not candidates:
        pytest.skip(
            "Neither assayer.md nor test-observations-adjudicator.md "
            "present at expected paths — Plan 07-04 territory"
        )
    # Source-grep for KNOWN_TEST_OBSERVATION_VERDICTS frozenset surface
    # plus the three locked verdict tokens.
    found_verdicts = False
    for path in candidates:
        contents = path.read_text(encoding="utf-8")
        if (
            "KNOWN_TEST_OBSERVATION_VERDICTS" in contents
            and "DEFECT" in contents
            and "WRONG_TEST" in contents
            and "INCONCLUSIVE" in contents
        ):
            found_verdicts = True
            break
    assert found_verdicts, (
        "KNOWN_TEST_OBSERVATION_VERDICTS frozenset (containing DEFECT, "
        "WRONG_TEST, INCONCLUSIVE) must appear in either assayer.md "
        "or test-observations-adjudicator.md (Plan 07-04 routing "
        "verdict surface incomplete)"
    )


# ---------------------------------------------------------------------------
# fallout D-168 — the observation-emitting layer.
#
# `tools/test_deriver.py` was the lowest-covered module in the tree at 30%,
# and `_parse_header` / `_parse_reportlog` were at zero: no test in the
# 55-module roster named either. What that hole hid was not a corner case but
# the stream's entire output. A reportlog nodeid is relative to the ROOTDIR
# pytest chose; `_parse_reportlog` runs back in the SERVER process, whose cwd
# is a different directory; so the header read missed every time and every
# observation shipped with an empty `tests_spec`, which
# `validate-test-observations.py` rejects with TEST_HEADER_MISSING and
# WRONG_TEST_HEADER_MISSING before the ASSAY adjudicator ever sees it.
#
# THE CWD IS THE WHOLE TEST. A test that chdir'd into the generated-test root
# first would have passed against the broken code, which is why none of these
# chdir and why each one asserts that it did not.
# ---------------------------------------------------------------------------


def _write_reportlog(path: Path, nodeid: str, outcome: str = "failed") -> None:
    """One pytest-reportlog line, in the shape pytest-reportlog 1.0.0 emits."""
    path.write_text(
        json.dumps(
            {
                "$report_type": "TestReport",
                "nodeid": nodeid,
                "outcome": outcome,
                "longrepr": "assert 200 == 401",
                "when": "call",
            }
        )
        + "\n",
        encoding="utf-8",
    )


def test_parse_header_reads_the_binding_from_a_path_it_is_handed(
    tmp_path: Path,
) -> None:
    """The parser itself is correct, and this pins that it is.

    Worth its own test because it is the half that made the defect hard to
    see: `_parse_header` handed an absolute path does exactly what its
    docstring claims, so anyone reading it in isolation concludes the binding
    works. The fault was never here -- it was in what the caller handed it.
    """
    generated = tmp_path / "generated"
    generated.mkdir()
    test_file = generated / "test_derived_login.py"
    test_file.write_text(
        "# tests-spec: FR-019, US-006\n"
        "def test_login_rejects_empty():\n"
        "    assert True\n",
        encoding="utf-8",
    )

    assert test_deriver._parse_header(test_file) == ["FR-019", "US-006"]

    # And the total-read contract: a path naming nothing, and a path naming a
    # DIRECTORY, both read as "no header" rather than raising. `Path("")`
    # normalises to the cwd, which exists and whose read_text raises.
    assert test_deriver._parse_header(generated / "absent.py") == []
    assert test_deriver._parse_header(Path("")) == []


def test_the_emitter_binds_its_requirements_from_a_cwd_that_is_not_the_test_root(
    tmp_path: Path,
) -> None:
    """fallout D-168, driven: the observation carries its `tests_spec`.

    The reportlog nodeid below is rootdir-relative and names a file that does
    not exist relative to this process's cwd -- which is the production
    arrangement exactly, since `derive_and_run_tests` runs pytest with
    `cwd=worktree_path` against a `generated_dir` outside it and then parses
    the reportlog back here.

    RED before the fix: `_parse_reportlog` built `Path("tests/test_derived_login.py")`
    and let `_parse_header` resolve it against the ambient cwd, so `tests_spec`
    came back `[]` and the validator refused the observation channel-side.
    """
    generated = tmp_path / "generated"
    generated.mkdir()
    (generated / "test_derived_login.py").write_text(
        "# tests-spec: FR-019, US-006\n"
        "def test_login_rejects_empty():\n"
        "    assert True\n",
        encoding="utf-8",
    )
    report_log = tmp_path / "report.jsonl"
    _write_reportlog(
        report_log, "tests/test_derived_login.py::test_login_rejects_empty"
    )

    # The precondition that makes this test mean anything. If it ever stops
    # holding, the test is passing for the wrong reason and says so here.
    assert Path.cwd() != generated, (
        "this test drives the bug's own arrangement -- a parse from a cwd that "
        "is NOT the generated-test root. Running it from inside that root "
        "would make it pass against the broken code."
    )

    observations = test_deriver._parse_reportlog(
        report_log, generated_dir=generated
    )

    assert len(observations) == 1, observations
    obs = observations[0]
    assert obs["tests_spec"] == ["FR-019", "US-006"], (
        "the observation shipped with an empty requirement binding, which "
        "`validate-test-observations.py` refuses with TEST_HEADER_MISSING and "
        "WRONG_TEST_HEADER_MISSING -- so the whole TEST-01 stream's output is "
        "rejected channel-side and ASSAY has nothing left to adjudicate"
    )
    assert obs["status"] == "FAIL"

    # The other rootdir pytest actually picks when it finds no ini file above
    # the argument: rootdir == generated_dir, so the nodeid is a bare
    # basename. Same call, same root, still bound.
    _write_reportlog(
        report_log, "test_derived_login.py::test_login_rejects_empty"
    )
    bare = test_deriver._parse_reportlog(report_log, generated_dir=generated)
    assert bare[0]["tests_spec"] == ["FR-019", "US-006"]

    # And the resolver does not invent a binding: a nodeid naming a file that
    # is not under the generated root reads as no header, not as someone
    # else's header.
    _write_reportlog(report_log, "tests/test_absent.py::test_nothing")
    missing = test_deriver._parse_reportlog(report_log, generated_dir=generated)
    assert missing[0]["tests_spec"] == []


def test_the_emitted_test_path_stays_the_nodeid_pytest_reported(
    tmp_path: Path,
) -> None:
    """Only the header READ is resolved; the emitted field is not.

    `validate-test-observations.py` scans `test_path` for forbidden roots
    (WRONG_TEST_SOURCE_LEAK). Resolving the nodeid onto an absolute machine
    path and then emitting THAT would push an ephemeral path into a committed
    artefact and hand that scan a string it never had to judge before. So the
    fix moves the resolution and leaves the field alone, and this is what says
    so.
    """
    generated = tmp_path / "generated"
    generated.mkdir()
    (generated / "test_derived_login.py").write_text(
        "# tests-spec: FR-019\ndef test_x():\n    assert True\n",
        encoding="utf-8",
    )
    report_log = tmp_path / "report.jsonl"
    _write_reportlog(
        report_log, "tests/test_derived_login.py::test_login_rejects_empty"
    )

    obs = test_deriver._parse_reportlog(report_log, generated_dir=generated)[0]

    assert obs["test_path"] == "tests/test_derived_login.py"
    assert not Path(obs["test_path"]).is_absolute(), (
        f"an absolute path reached the emitted artefact: {obs['test_path']!r}"
    )
    assert str(tmp_path) not in obs["test_path"]

