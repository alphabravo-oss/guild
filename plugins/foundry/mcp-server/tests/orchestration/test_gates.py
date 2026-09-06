"""Foundry-Gate: the ranked ladder and what each rung says.

Carved from `tests/test_orchestrator_gates.py` (fallout FR-005 / GI-026 /
AC-014 / OT-016): one test module per shipped orchestration module, landed in
the same casting as the source move so no pin is ever left pointing at a module
that no longer exists.
"""
from __future__ import annotations

import ast
import asyncio
import inspect
import json
import re
import tempfile
import textwrap
from pathlib import Path

import pytest

import foundry_mcp
from foundry_mcp.schemas import vocab
from foundry_mcp.schemas.vocab import (  # noqa: F401
    REPORT_REQUIRED_SECTIONS,
    RUN_PHASE_HALTED,
)
from foundry_mcp.tools import artifacts, foundry_state

# fallout FR-004 / AC-013 — THE MODULE OBJECTS, UNDER UNDERSCORE ALIASES.
#
# `streams`, `spend`, `width`, `gates`, `directives` and `teams` are all LOCAL
# variable names somewhere in this suite, and a local rebinding shadows a
# module for the rest of its function. The aliases are what `ORCHESTRATION`,
# `owning_module` and every `monkeypatch.setattr` resolve through; individual
# SYMBOLS are imported by name below, which is how the carved modules read.
from foundry_mcp.tools.orchestration import directives as _directives
from foundry_mcp.tools.orchestration import escalation as _escalation
from foundry_mcp.tools.orchestration import evidence_boundary as _evidence_boundary
from foundry_mcp.tools.orchestration import fix_gate as _fix_gate
from foundry_mcp.tools.orchestration import gates as _gates
from foundry_mcp.tools.orchestration import guidance as _guidance
from foundry_mcp.tools.orchestration import halt as _halt
from foundry_mcp.tools.orchestration import report_seal as _report_seal
from foundry_mcp.tools.orchestration import spend as _spend
from foundry_mcp.tools.orchestration import streams as _streams
from foundry_mcp.tools.orchestration import teams as _teams
from foundry_mcp.tools.orchestration import transitions as _transitions
from foundry_mcp.tools.orchestration import width as _width

# fallout AC-014 — THE TWO SIBLING SUITES THE CARVE MUST KEEP REACHING.
#
# `test_observations` owns the never-demote corpus and `test_spawn_progress`
# owns the shipped-source-tree derivation. Both are imported rather than copied,
# for the reason the monolith imported them: a parity test that owned its own
# copy of the corpus would keep passing while the two corpora drifted, and two
# scans over "the shipped source" must not be able to disagree about what that
# is. If either renames a symbol the ImportError says so by name, which is the
# loud failure rather than the silent one.

#: fallout FR-004 / AC-014 — WHAT `fo` USED TO MEAN, NOW THAT IT MEANS THIRTEEN
#: THINGS.
#:
#: Every pin that read `Path(fo.__file__).read_text()` was asking about THE
#: ORCHESTRATOR. That is thirteen files now, so the honest translation of the
#: question is all thirteen — and it stays the honest translation when a
#: fourteenth is added, which a hand-listed pair of modules would not.
ORCHESTRATION = (
    _report_seal, _escalation, _streams, _teams, _width, _evidence_boundary,
    _spend, _halt, _gates, _transitions, _fix_gate, _directives, _guidance,
)


def orchestration_source() -> str:
    """The concatenated source of every shipped orchestration module."""
    return chr(10).join(
        Path(m.__file__).read_text(encoding="utf-8") for m in ORCHESTRATION
    )


def owning_module(symbol: str):
    """The orchestration module that DEFINES `symbol`.

    A patch has to reach the module each CALLER resolves the name through, and
    after the carve that is a binding per importer rather than one module
    attribute. Patching only the module that defines a symbol leaves every
    importer on the real one, which is the silent half of a broken pin.
    """
    for module in ORCHESTRATION:
        value = vars(module).get(symbol)
        if value is None:
            continue
        if getattr(value, "__module__", module.__name__) == module.__name__:
            return module
    for module in ORCHESTRATION:
        if symbol in vars(module):
            return module
    raise AssertionError(f"no orchestration module defines {symbol!r}")


def patch_everywhere(monkeypatch, name: str, value) -> None:
    """Patch `name` in EVERY module that carries it.

    fallout FR-004 / AC-014 — WHAT A MODULE-ATTRIBUTE PATCH USED TO MEAN.

    There was one module, so patching it patched the only binding. After the
    carve a symbol is imported BY NAME into each caller's namespace, so patching
    the module that DEFINES it leaves every importer resolving the real one —
    and a patch that reaches some callers and not others is worse than no patch,
    because the drive then exercises a state no run can be in. This patches
    every binding, which is the same fact the single module used to make true by
    construction.
    """
    for module in (*ORCHESTRATION, artifacts, foundry_state):
        if name in vars(module):
            monkeypatch.setattr(module, name, value)


def orchestration_has(symbol: str) -> bool:
    """True when any orchestration module carries `symbol`."""
    return any(symbol in vars(m) for m in ORCHESTRATION)

from tests.orchestration._env import (  # noqa: F401
    _GATE_PHASES_THAT_READ_DEFECTS,
    _arm_ordering_token,
    _arrange_passing,
    _at_the_phase_for,
    _committed_evidence,
    _d190_state,
    _defect_ledger,
    _evidence_repo,
    _gate_for,
    _generate_report,
    _halted_run,
    _ready_for_the_end_gates,
    _record_full_inspect_mode,
    _teams_active,
    _tiered,
    _write_manifest_with_castings,
    _write_prove,
    _write_spec,
    _write_state,
    _write_verdicts,
    run_env,
)

from foundry_mcp.tools.orchestration.evidence_boundary import (  # noqa: F401
    EVIDENCE_STRIPPED_TOKEN,
    TERMINAL_SWEEP_FILENAME,
    _evidence_corpus_existed,
)

from foundry_mcp.tools.orchestration.fix_gate import (  # noqa: F401
    foundry_sync_defects,
)

from foundry_mcp.tools.orchestration.gates import (  # noqa: F401
    GATE_TO_TRANSITION,
    VERDICT_VALUES,
    _GATE_RANK_DEFECTS,
    _GATE_RANK_EVIDENCE,
    _GATE_RANK_HALTED,
    _TEAMS_DOWN_HINT,
    _blocking_defects,
    _count_spec_requirements,
    _done_preconditions,
    _generate_report,
    _open_defects_by_tier,
    _sorted_spec_requirement_ids,
    foundry_gate,
)

from foundry_mcp.tools.orchestration.guidance import (  # noqa: F401
    _ACTION_IMPERATIVES,
    _synthesize_clean_prove_verdicts,
    _ACTION_TO_GATE,
    _compute_next_action,
    _expected_gate_for_action,
    _gate_tokens_named,
    foundry_next_action,
)

from foundry_mcp.tools.orchestration.teams import (  # noqa: F401
    _check_sight_required,
)

from foundry_mcp.tools.orchestration.transitions import (  # noqa: F401
    PHASE_TOKENS,
    foundry_mark_phase_complete,
)

from tests.orchestration._env import (  # noqa: F401
    _BAD_UTF8_SPEC,
    _OLD_ID_FAMILIES,
    _TERMINAL_EVIDENCE_CROSSINGS,
    _UNUSABLE_MANIFEST_RECORDS,
    _append_lead_prose,
    _arrange_terminal_door,
    _assert_lead_prose_survived,
    _head,
    _latent,
    _spec_with_every_family,
    _strip_evidence,
    _traceability_fixture,
    _untiered_open,
    _widened_id_families,
)

from tests.orchestration.test_module_boundaries import (  # noqa: F401
    _drive_mcp,
    _external_spec_run,
)




def test_done_gate_verdict_coverage_passes_after_synthesis(run_env):
    """AC FR-003 (end-to-end): after the auto-pass synthesis, the DONE gate's
    ``verdict_coverage`` check reads N/N and the gate passes."""
    project_root, fdir = run_env
    ids = ["FR-1", "FR-2", "US-3"]
    _write_spec(fdir, ids)
    _write_state(fdir, phase="F4")
    _write_prove(fdir, len(ids), len(ids), 0)

    # Router auto-pass path synthesizes verdicts.
    _compute_next_action(project_root)

    _generate_report(project_root, fdir)
    _arm_ordering_token(fdir)
    gate = foundry_gate("done", project_root)

    assert gate["passed"] is True, gate
    coverage = [c for c in gate["checklist"] if c["check"].startswith("verdict_coverage")]
    assert coverage and coverage[0]["ok"] is True, gate["checklist"]
    non_verified = [c for c in gate["checklist"] if c["check"].startswith("all_verified")]
    assert non_verified and non_verified[0]["ok"] is True




def test_synthesized_rows_match_foundry_verdict_schema_and_preserve_existing(run_env):
    """AC FR-004 (schema + dedup): synthesized rows carry the Foundry-Verdict
    schema keys, existing real verdicts are never overwritten, and no ID is
    duplicated."""
    project_root, fdir = run_env
    ids = ["FR-1", "FR-2"]
    _write_spec(fdir, ids)
    _write_state(fdir, phase="F4")
    _write_prove(fdir, len(ids), len(ids), 0)

    # A real ASSAY verdict already exists for FR-1 with rich evidence.
    real_row = {
        "id": "FR-1",
        "verdict": "VERIFIED",
        "evidence": "Real ASSAY evidence — do not overwrite.",
        "spec_text_cited": "FR-1 spec text",
        "code_location": "src/foo.py:10",
        "cycle": 2,
        "recorded_at": "2020-01-01T00:00:00+00:00",
    }
    _write_verdicts(fdir, [real_row])

    count = _synthesize_clean_prove_verdicts(fdir, project_root, cycle=1)

    assert count == 1  # only FR-2 added; FR-1 skipped
    verdicts = json.loads((fdir / "verdicts.json").read_text(encoding="utf-8"))
    rows = verdicts["requirements"]
    ids_present = [r["id"] for r in rows]
    assert ids_present.count("FR-1") == 1  # no duplicate
    assert set(ids_present) == {"FR-1", "FR-2"}

    fr1 = next(r for r in rows if r["id"] == "FR-1")
    assert fr1 == real_row  # untouched, evidence preserved

    fr2 = next(r for r in rows if r["id"] == "FR-2")
    for key in ("id", "verdict", "evidence", "spec_text_cited", "code_location",
                "cycle", "recorded_at"):
        assert key in fr2, f"synthesized row missing schema key: {key}"
    assert fr2["verdict"] == "VERIFIED"




# --------------------------------------------------------------------------- #
# P4 — passing-gate guidance advance (FR-005 / ST-002)
# --------------------------------------------------------------------------- #


def test_passing_gate_advances_guidance_state(run_env):
    """AC FR-005 / ST-002: a passing Foundry-Gate advances the guidance state
    so the NEXT Foundry-Next surfaces the gate as satisfied (proceed to the
    transition step) instead of re-instructing the now-satisfied gate."""
    project_root, fdir = run_env
    ids = ["FR-1", "FR-2"]
    _write_spec(fdir, ids)
    _write_state(fdir, phase="F4")
    _write_verdicts(
        fdir,
        [
            {"id": rid, "verdict": "VERIFIED", "evidence": "", "spec_text_cited": "",
             "code_location": "", "cycle": 1, "recorded_at": "2020-01-01T00:00:00+00:00"}
            for rid in ids
        ],
    )

    _generate_report(project_root, fdir)

    # Before the gate passes: no gate_advanced signal.
    first = foundry_next_action(project_root)
    assert first["action"] == "transition_to_done"
    assert "gate_advanced" not in first
    assert "ALREADY" not in first["instructions"]

    # Pass the DONE gate (foundry_next_action armed the ordering token).
    gate = foundry_gate("done", project_root)
    assert gate["passed"] is True, gate
    assert (fdir / ".gate-passed").exists()

    # After the gate passes: the next Foundry-Next advances guidance.
    second = foundry_next_action(project_root)
    assert second.get("gate_advanced", {}).get("passed_gate") == "done"
    assert "ALREADY PASSED" in second["instructions"]




# --------------------------------------------------------------------------- #
# NFR-002 — no regression to the count / normal-run behavior
# --------------------------------------------------------------------------- #


def test_count_spec_requirements_dedups_after_refactor(run_env):
    """NFR-002: the refactored counter still returns the count of UNIQUE IDs,
    and the new id-list helper returns the sorted unique set."""
    project_root, fdir = run_env
    # FR-1 appears twice; the set-dedup must collapse it.
    (fdir / "spec.md").write_text(
        "# Spec\n- FR-1 first mention\n- FR-1 again\n- US-2\n- NFR-3\n",
        encoding="utf-8",
    )
    assert _count_spec_requirements(project_root) == 3
    assert _sorted_spec_requirement_ids(project_root) == ["FR-1", "NFR-3", "US-2"]




def test_synthesis_is_noop_when_verdicts_already_complete(run_env):
    """NFR-002: on a normal ASSAY run where verdicts already cover every ID,
    synthesis adds nothing and leaves verdicts.json byte-for-byte unchanged."""
    project_root, fdir = run_env
    ids = ["FR-1", "FR-2"]
    _write_spec(fdir, ids)
    _write_prove(fdir, len(ids), len(ids), 0)
    _write_verdicts(
        fdir,
        [
            {"id": rid, "verdict": "VERIFIED", "evidence": "real", "spec_text_cited": "",
             "code_location": "", "cycle": 1, "recorded_at": "2020-01-01T00:00:00+00:00"}
            for rid in ids
        ],
    )
    before = (fdir / "verdicts.json").read_text(encoding="utf-8")

    count = _synthesize_clean_prove_verdicts(fdir, project_root, cycle=1)

    assert count == 0
    assert (fdir / "verdicts.json").read_text(encoding="utf-8") == before




# --------------------------------------------------------------------------- #
# D-017 — the verdict axis is derived, not hand-typed
# --------------------------------------------------------------------------- #


def test_verdict_enum_is_derived_and_admits_misplaced():
    """D-017 / FR-013 / CT-002: the Foundry-Verdict enum was a hand-typed
    baseline copy that rejected MISPLACED — a verdict agents/assayer.md
    mandates and commands/start.md routes into this very tool, so the protocol
    told an agent to emit a verdict the recording surface could not carry."""
    from foundry_mcp import server as foundry_server

    tools = asyncio.run(foundry_server.list_tools())
    verdict_tool = next(t for t in tools if t.name == "Foundry-Verdict")
    advertised = set(verdict_tool.inputSchema["properties"]["verdict"]["enum"])

    assert advertised == set(VERDICT_VALUES)
    # Derived from the canonical defect vocabulary plus the one verdict that is
    # not a defect, so a defect type added to vocab.py is a verdict for free.
    assert vocab.DEFECT_TYPES <= advertised
    assert "VERIFIED" in advertised
    assert "MISPLACED" in advertised
    # NFR-002: every value the surface accepted before still validates.
    assert {"VERIFIED", "HOLLOW", "THIN", "PARTIAL", "MISSING", "WRONG",
            "COVERAGE_INCOMPLETE"} <= advertised




# --------------------------------------------------------------------------- #
# D-033 — a spec that parses to zero requirements cannot reach DONE
# --------------------------------------------------------------------------- #


def test_done_gate_refuses_a_spec_that_parses_to_zero_requirements(run_env):
    """FR-020 / AC-025: every other DONE check is vacuously satisfied by a spec
    with no requirement IDs — no requirement can be non-VERIFIED, and the
    verdict_coverage check guarded ITSELF with ``spec_count > 0`` and skipped.
    So an unresolvable or untagged spec sailed through DONE having proved
    nothing. The auto-VERIFY hole had moved, not closed."""
    project_root, fdir = run_env
    (fdir / "spec.md").write_text("# Spec\nProse with no tagged IDs.\n", encoding="utf-8")
    _write_state(fdir, phase="F4")
    _write_verdicts(fdir, [])
    _arm_ordering_token(fdir)

    gate = foundry_gate("done", project_root)

    assert gate["passed"] is False
    assert "ZERO requirement IDs" in gate["reason"]
    checks = {c["check"]: c["ok"] for c in gate["checklist"]}
    parsed_check = next(k for k in checks if k.startswith("spec_requirements_parsed"))
    assert checks[parsed_check] is False




def test_done_gate_still_passes_on_a_real_spec(run_env):
    """The guard is inert for every run that has requirements — it must not
    make DONE unreachable, only non-vacuous."""
    project_root, fdir = run_env
    ids = ["FR-1", "AC-2"]
    _write_spec(fdir, ids)
    _write_state(fdir, phase="F4")
    _write_verdicts(fdir, [{"id": r, "verdict": "VERIFIED"} for r in ids])
    _generate_report(project_root, fdir)
    _arm_ordering_token(fdir)

    gate = foundry_gate("done", project_root)

    assert gate["passed"] is True, gate
    checks = {c["check"]: c["ok"] for c in gate["checklist"]}
    parsed_check = next(k for k in checks if k.startswith("spec_requirements_parsed"))
    assert checks[parsed_check] is True




def test_a_more_specific_done_failure_still_names_itself(run_env):
    """The zero-requirement check runs FIRST so a run that is also blocked on
    something concrete reports that instead — the reason a lead reads should be
    the most actionable one, not whichever check happens to run last."""
    project_root, fdir = run_env
    _write_state(fdir, phase="F4")
    (fdir / "defects.json").write_text(
        json.dumps({"defects": [{"id": "D-001", "status": "open"}]}), encoding="utf-8"
    )
    _write_verdicts(fdir, [])
    _arm_ordering_token(fdir)

    gate = foundry_gate("done", project_root)

    assert gate["passed"] is False
    # CT-008 / FR-051: the refusal NAMES the blocking defects and says which
    # axis blocks them. D-001 carries no tier, so it blocks like LIVE and is
    # reported as untiered until a stream re-files it — the reason a lead reads
    # is still the concrete one, not the report or the zero-requirement notice.
    assert "D-001" in gate["reason"], gate
    assert "no tier" in gate["reason"], gate




def test_the_done_precondition_prose_describes_what_report_status_reads():
    """GI-006 / D-050 — stale prose survives beside new prose.

    `_done_preconditions`' report block asserted that `report_status` answers
    'from `report.json` and never from REPORT.md', and argued from that claim
    that appended prose could not make a section look absent. D-015 had already
    made the claim false — `rm REPORT.md` left the DONE gate passing, so the
    read moved onto both documents — and the block kept the case for the
    behaviour that drive removed. A maintainer reading it would conclude
    REPORT.md is unchecked and could revert D-015 as redundant.

    Asserted against the CODE as well as the comment, so the pin cannot be
    satisfied by editing prose to match a behaviour that later moves again.
    """
    import inspect

    from foundry_mcp.tools.foundry_report import report_status

    source = inspect.getsource(_done_preconditions)
    assert "never from REPORT.md" not in source, (
        "the block still claims report_status ignores REPORT.md"
    )
    assert "BOTH documents" in source or "both documents" in source, source

    # And the behaviour the corrected prose describes, driven.
    with tempfile.TemporaryDirectory() as tmp:
        run_dir = Path(tmp)
        (run_dir / "report.json").write_text(
            json.dumps({s: {} for s in REPORT_REQUIRED_SECTIONS}
                       | {"generated_at": "now", "run": "r"}),
            encoding="utf-8",
        )
        status = report_status(run_dir)
        assert status["present"] is False, (
            "REPORT.md is absent, so no section can be shown to a reader"
        )
        assert status["missing_sections"] == list(REPORT_REQUIRED_SECTIONS)




@pytest.mark.parametrize("body", _UNUSABLE_MANIFEST_RECORDS)
def test_no_manifest_reader_in_this_module_raises_on_unusable_records(run_env, body):
    """D-134 — the doors in THIS module, driven against the nested shapes.

    D-132's validator was bound inside foundry_spawn.py: its four readers were
    guarded and its membership was derived over that module's own functions,
    not over every reader of castings/manifest.json in the package. So
    `_check_sight_required`, `foundry_gate` and the trace-skip predicate that
    stood beside them indexed the same records behind a top-rung-only guard, and
    `castings: "nope"` met `.get()` and raised AttributeError. (That predicate
    was deleted for having no caller — fallout D-057 — so two of the three
    readers are driven here; the rule is about every reader in the package, not
    about a fixed count.)

    Foundry-Next is the mandatory handshake before EVERY phase transition, so
    this was reachable on the most-travelled door in the tool surface.
    """
    project_root, fdir = run_env
    _write_state(fdir, phase="F2", cycle=1)
    (fdir / "castings").mkdir(exist_ok=True)
    (fdir / "castings" / "manifest.json").write_text(json.dumps(body), encoding="utf-8")

    # Each reader answers rather than raising...
    assert _check_sight_required(project_root).get("required") is False
    for phase in ("validate", "cast"):
        assert foundry_gate(phase, project_root)["passed"] is False

    # ...and the handshake itself returns the HOUSE refusal, naming the file in
    # `corrupt_artifacts` the way every other artifact refusal on this surface
    # does, rather than a traceback that names nothing.
    result = foundry_next_action(project_root)
    assert "error" in result, result
    assert "manifest.json" in result["error"], result["error"]
    assert any("manifest.json" in p for p in result["corrupt_artifacts"]), result




def test_the_external_input_reader_is_total_on_its_own_merits(tmp_path, monkeypatch):
    """D-145 adjacent-path test (AC-013).

    The path the defect was found on is Foundry-Validate-Castings reaching the
    external spec through `_artifact_guard`. The ADJACENT path this drives is
    the same file through a DIFFERENT caller — the leaf ladder
    `_sorted_spec_requirement_ids` takes its ids off, which
    the DONE gate's requirement count and P3 verdict synthesis both run through,
    and which never calls the artifact guard at all. It read the spec under
    `except OSError`, which does not name UnicodeDecodeError. Both readers must
    hold without the guard above them, because "a guard runs first" is a fact
    about one call site and not a property of the reader.
    """
    root, fdir = _external_spec_run(tmp_path, "forge-specs/probe/spec.md", _BAD_UTF8_SPEC)
    try:
        patch_everywhere(monkeypatch, "_resolve_spec_path", lambda pr: Path(root) / "forge-specs/probe/spec.md")
        assert _sorted_spec_requirement_ids(root) == []
        assert _count_spec_requirements(root) == 0
    finally:
        foundry_state.clear_active_run()




def test_the_orchestrator_counts_every_declared_requirement_family(run_env):
    """D-150 on the reported path: the DONE gate's count sees GI- and OT-.

    NFR-002 is the first assertion, not an afterthought: every family the old
    literal matched must still be counted, or "widened" would be a narrowing
    wearing the word.
    """
    project_root, fdir = run_env
    expected = _spec_with_every_family(fdir)

    found = set(_sorted_spec_requirement_ids(project_root))
    old_half = {i for i in expected if i.split("-")[0] in _OLD_ID_FAMILIES}
    assert old_half <= found, f"NFR-002 narrowing: {sorted(old_half - found)}"
    assert expected <= found, f"still unseen: {sorted(expected - found)}"
    assert _count_spec_requirements(project_root) == len(expected)




def test_verdict_synthesis_covers_the_widened_families(run_env):
    """D-150 adjacent-path test (AC-013).

    The path the defect names is the requirement COUNT. The ADJACENT path this
    drives is P3 verdict synthesis, a different caller of the same id source:
    it writes one row per requirement id, and `verdict_coverage` is measured
    against the count. If the two ever read different families the run reports
    coverage over a denominator that does not match its own rows — which is
    the drift the one leaf ladder exists to prevent, now restated one
    vocabulary wider.
    """
    project_root, fdir = run_env
    expected = _spec_with_every_family(fdir)
    _write_prove(fdir, items_checked=len(expected), items_total=len(expected), findings=0)

    written = _synthesize_clean_prove_verdicts(fdir, project_root, cycle=1)
    rows = json.loads((fdir / "verdicts.json").read_text(encoding="utf-8"))
    synthesized = {r["id"] for r in rows.get("requirements", [])}
    assert expected <= synthesized, (
        f"P3 synthesis wrote rows for {sorted(synthesized)}, missing "
        f"{sorted(expected - synthesized)} — the count and the rows disagree "
        f"about which families exist."
    )
    assert written == len(synthesized) == _count_spec_requirements(project_root)




def render_requirement_family_table(tmp_path: Path) -> str:
    """D-150 pre/post: which families each reader could see.

    The PRE arm is the literal all six copies carried, reproduced verbatim
    rather than described, so the log shows the miss instead of asserting it.
    """
    from foundry_mcp.schemas.vocab import REQUIREMENT_ID_PREFIXES, REQUIREMENT_ID_RE

    project_root = str(tmp_path)
    fdir = tmp_path / "foundry-archive" / "d150"
    fdir.mkdir(parents=True, exist_ok=True)
    _write_state(fdir, phase="F4", cycle=1)
    foundry_state.set_active_run("d150")
    old = re.compile(r"\b(?:US|FR|NFR|AC|VC|IR|TR)-\d+(?:\.\d+)?\b")
    expected = sorted(_spec_with_every_family(fdir))
    spec_text = (fdir / "spec.md").read_text(encoding="utf-8")

    out = [
        "== D-150: which requirement families exist was a literal, typed six times ==",
        "",
        f"   the vocabulary declares      : {sorted(REQUIREMENT_ID_PREFIXES)}",
        f"   the literal every copy held  : ['AC', 'FR', 'IR', 'NFR', 'TR', 'US', 'VC']",
        f"   families the copies could NOT see: {list(_widened_id_families())}",
        "",
        "-- a spec naming one id of every family --",
        f"   ids present                  : {expected}",
        f"   the old literal finds        : {sorted(set(old.findall(spec_text)))}",
        f"   the declared pattern finds   : {sorted(set(REQUIREMENT_ID_RE.findall(spec_text)))}",
        "",
        "-- through the two readers this casting owns --",
    ]
    _write_prove(fdir, items_checked=len(expected), items_total=len(expected), findings=0)
    out.append(f"   DONE gate requirement count  : {_count_spec_requirements(project_root)}")
    written = _synthesize_clean_prove_verdicts(fdir, project_root, cycle=1)
    rows = json.loads((fdir / "verdicts.json").read_text(encoding="utf-8"))
    ids = sorted(r["id"] for r in rows.get("requirements", []))
    out.append(f"   P3 verdict rows synthesized  : {written} -> {ids}")
    out.append(
        f"   count and rows agree         : "
        f"{written == _count_spec_requirements(project_root)}"
    )

    out += ["", "-- and no module in the grant re-types the families any more --"]
    for dotted in (
        "foundry_mcp.tools.orchestration.gates",
        "foundry_mcp.tools.foundry_validate",
        "foundry_mcp.parsers.spec",
    ):
        module = __import__(dotted, fromlist=["x"])
        source = Path(module.__file__).read_text(encoding="utf-8")
        own = any(
            fam + "|" in source for fam in ("US", "FR", "AC")
        ) and "-\\d+(?:\\.\\d+)?" in source
        out.append(
            f"   {Path(module.__file__).name:26s} own copy={own}"
            f"  reads the export={'REQUIREMENT_ID_RE' in source}"
        )

    out += [
        "",
        "-- end to end, through the reader furthest from the change --",
    ]
    from foundry_mcp.tools.citation import verify_citations

    spec, report = _traceability_fixture(tmp_path)
    result = verify_citations(
        spec_path=spec, report_path=report, project_root=str(tmp_path)
    )
    rows = {r["requirement_id"]: r["status"] for r in result["traceability_matrix"]}
    out.append(f"   verify_citations traceability rows : {rows}")
    out.append(
        "   an observable truth has a row      : "
        f"{'OT-011' in rows}   (and US-1 still does: {'US-1' in rows})"
    )
    return "\n".join(out)




def test_the_requirement_family_table_shows_the_miss_and_the_fix(tmp_path):
    """D-150's drive, ASSERTED so the log is a claim and not a picture."""
    try:
        table = render_requirement_family_table(tmp_path)
    finally:
        foundry_state.clear_active_run()
    assert f"families the copies could NOT see: {list(_widened_id_families())}" in table, table
    # NFR-002 first: nothing the old literal found has stopped being found.
    old_line = next(r for r in table.split("\n") if "the old literal finds" in r)
    new_line = next(r for r in table.split("\n") if "the declared pattern finds" in r)
    for family in _OLD_ID_FAMILIES:
        assert f"'{family}-" in old_line and f"'{family}-" in new_line, family
    for family in _widened_id_families():
        assert f"'{family}-" not in old_line and f"'{family}-" in new_line, family
    assert "count and rows agree         : True" in table, table
    assert "own copy=False" in table and "own copy=True" not in table, table
    assert table.count("reads the export=True") == 3, table
    assert "an observable truth has a row      : True   (and US-1 still does: True)" in table, table




def test_a_latent_only_backlog_passes_every_end_gate(run_env):
    """AC-008 verbatim (first clause): 'With only LATENT defects open,
    Foundry-Gate assay, temper, nyquist and done pass and Foundry-Phase
    inspect_clean succeeds.'

    FR-006 says the same thing and adds what happens to them: 'LATENT stays
    open, tracked, and listed in the report.' Nothing is closed or waived — the
    gates simply stop treating "I looked for this and could not make it happen"
    as equivalent to "I made it happen".
    """
    project_root, fdir = run_env
    _ready_for_the_end_gates(project_root, fdir)
    _defect_ledger(fdir, [
        _tiered("D-001", "LATENT", reproduction_attempted="AST sweep finds 0 sites"),
        _tiered("D-002", "LATENT", reproduction_attempted="drove every caller; none reach it"),
    ])
    _generate_report(project_root, fdir)

    for phase in _GATE_PHASES_THAT_READ_DEFECTS:
        _at_the_phase_for(fdir, phase)
        _arm_ordering_token(fdir)
        gate = foundry_gate(phase, project_root)
        assert gate["passed"] is True, (phase, gate)
        blocking = next(
            c for c in gate["checklist"]
            if c["check"].startswith("zero_blocking_defects")
        )
        assert blocking["ok"] is True, (phase, gate)
        assert sorted(blocking["latent_backlog"]) == ["D-001", "D-002"], (phase, gate)

    _arm_ordering_token(fdir)
    transition = foundry_mark_phase_complete("inspect_clean", project_root)
    assert transition["ok"] is True, transition
    assert transition["phase"] == "F4"




@pytest.mark.parametrize("phase", _GATE_PHASES_THAT_READ_DEFECTS)
def test_one_open_live_defect_refuses_every_end_gate_by_name(run_env, phase):
    """AC-008's second clause: 'with one LIVE defect open, Foundry-Gate nyquist
    refuses naming it.'

    Parametrized over all four, because FR-006 is about the SET — "INSPECT-clean,
    ASSAY, TEMPER, NYQUIST and DONE all pass when the only open defects are
    LATENT" — and a tier read wired into three of four gates would leave the
    fourth deciding on a different definition of "open".

    NYQUIST is the one that changes most: it read no defects at all before, so a
    run could enter F5.5 and generate regression tests locking in behaviour a
    stream had already ruled wrong (FR-006: "NYQUIST gains the missing defect
    read so one open LIVE now blocks it").
    """
    project_root, fdir = run_env
    _ready_for_the_end_gates(project_root, fdir)
    _defect_ledger(fdir, [
        _tiered("D-001", "LATENT", reproduction_attempted="AST sweep finds 0 sites"),
        _tiered("D-002", "LIVE"),
    ])
    _generate_report(project_root, fdir)

    _at_the_phase_for(fdir, phase)
    _arm_ordering_token(fdir)
    gate = foundry_gate(phase, project_root)

    assert gate["passed"] is False, gate
    assert "D-002" in gate["reason"], gate
    assert "LIVE" in gate["reason"], gate
    assert "D-001" not in gate["reason"], (
        "a LATENT defect is not the reason anything is blocked"
    )




@pytest.mark.parametrize("phase", _GATE_PHASES_THAT_READ_DEFECTS)
def test_an_untiered_defect_blocks_like_live_and_is_named_separately(run_env, phase):
    """AC-008's last clause: 'on a resumed pre-change run an open defect with no
    tier blocks like LIVE until a stream re-files it with a tier, and the report
    lists it separately.'

    FR-051 is the rule: "Blocks like LIVE until a stream re-files it with a
    tier." Reading it as LATENT would silently clear every gate on records nobody
    ever classified — the one direction of this change that could lose a real
    defect. It is named SEPARATELY because the operator's next move differs: a
    LIVE defect needs fixing, an untiered one needs a stream to look at it, after
    which it may well stop blocking.
    """
    project_root, fdir = run_env
    _ready_for_the_end_gates(project_root, fdir)
    _defect_ledger(fdir, [_tiered("D-007", None)])
    _generate_report(project_root, fdir)

    _at_the_phase_for(fdir, phase)
    _arm_ordering_token(fdir)
    gate = foundry_gate(phase, project_root)

    assert gate["passed"] is False, gate
    assert "D-007" in gate["reason"], gate
    assert "no tier" in gate["reason"], gate
    blocking = next(
        c for c in gate["checklist"] if c["check"].startswith("zero_blocking_defects")
    )
    assert blocking["unknown_tier"] == ["D-007"]
    assert blocking["live"] == []




@pytest.mark.parametrize(
    "tier", ["minor", "major", "MEDIUM", "", None], ids=lambda t: repr(t)
)
def test_a_tier_outside_the_vocabulary_reads_as_unknown_and_blocks(run_env, tier):
    """GI-001's no-severity guarantee, at the read side.

    The abolished axis would come back as a value rather than as a key: a record
    carrying `tier: "minor"` must not be coerced onto LATENT, because that is
    exactly the silent downgrade the evidence axis replaced. Every value outside
    DEFECT_TIERS resolves to unknown and therefore blocks.
    """
    project_root, fdir = run_env
    _ready_for_the_end_gates(project_root, fdir)
    _defect_ledger(fdir, [_tiered("D-009", tier)])
    _generate_report(project_root, fdir)

    _arm_ordering_token(fdir)
    gate = foundry_gate("nyquist", project_root)

    assert gate["passed"] is False, (tier, gate)
    assert "D-009" in gate["reason"], (tier, gate)




def test_a_temper_cycle_filing_only_latent_leaves_temper_and_nyquist_passing(run_env):
    """OT-011 verbatim: 'A TEMPER cycle that files only LATENT defects leaves
    Foundry-Gate temper and nyquist passing.'

    This is the convergence case the whole tier exists for. TEMPER's job is to
    zoom into micro-domains and ask very specific questions, and its answers are
    frequently "I reasoned about this and could not make it fail" — findings
    worth recording and not worth blocking a finished run on.
    """
    project_root, fdir = run_env
    _ready_for_the_end_gates(project_root, fdir)
    _write_state(fdir, phase="F5", cycle=6, nyquist=True, temper=True)
    _defect_ledger(fdir, [
        _tiered(f"D-{n:03d}", "LATENT", cycle=6,
                reproduction_attempted="drove the boundary; no instance reproduced")
        for n in range(1, 5)
    ])
    _generate_report(project_root, fdir)

    for phase in ("temper", "nyquist"):
        _at_the_phase_for(fdir, phase)
        _arm_ordering_token(fdir)
        gate = foundry_gate(phase, project_root)
        assert gate["passed"] is True, (phase, gate)




def test_the_grind_gate_still_counts_every_open_defect(run_env):
    """The boundary of the change, stated so it is not read as a general rule.

    GRIND asks "is there work to do", and a LATENT defect is work: it is a defect
    and it gets fixed. Making the GRIND gate tier-aware would refuse to open a
    cycle whose whole job is closing the LATENT backlog.
    """
    project_root, fdir = run_env
    _write_state(fdir, phase="F2", cycle=1)
    _defect_ledger(fdir, [
        _tiered("D-001", "LATENT", reproduction_attempted="AST sweep finds 0 sites"),
    ])
    (fdir / ".tasks-generated").write_text("x\n", encoding="utf-8")

    _arm_ordering_token(fdir)
    gate = foundry_gate("grind", project_root)

    assert gate["passed"] is True, gate




def test_the_done_preconditions_hint_matches_the_check_that_claimed_the_reason(run_env):
    """FR-026's fourth ride-along: the stale hint in `_done_preconditions`.

    The open-defect branch set `reason` and left `hint` alone, so whatever the
    PREVIOUS check happened to write stayed attached to it. A run with open
    defects AND a non-VERIFIED requirement was refused with reason "N open
    defect(s) remain" beside hint "Fix all non-VERIFIED requirements. Every THIN
    item must be fully implemented." — an instruction for a different check
    entirely, and the lead's only stated next move.
    """
    project_root, fdir = run_env
    _write_spec(fdir, ["FR-1"])
    _write_state(fdir, phase="F4", cycle=1)
    _write_verdicts(fdir, [{"requirement_id": "FR-1", "verdict": "THIN"}])
    _defect_ledger(fdir, [_tiered("D-002", "LIVE")])
    _generate_report(project_root, fdir)

    _arm_ordering_token(fdir)
    gate = foundry_gate("done", project_root)

    assert gate["passed"] is False
    # The open defect is the last check to claim `reason`, so the hint beside it
    # must be about defects — not about THIN requirements.
    assert "D-002" in gate["reason"]
    assert "non-VERIFIED" not in gate["hint"], gate
    assert "THIN" not in gate["hint"], gate
    assert "GRIND" in gate["hint"] or "re-file" in gate["hint"], gate




def test_after_foundry_report_the_done_transition_succeeds(run_env):
    """OT-025's second half: 'after Foundry-Report it succeeds and report.json
    contains every named section.'"""
    project_root, fdir = run_env
    _write_spec(fdir, ["FR-1"])
    _write_state(fdir, phase="F4", cycle=1)
    _write_verdicts(fdir, [{"requirement_id": "FR-1", "verdict": "VERIFIED"}])
    _defect_ledger(fdir, [])
    _generate_report(project_root, fdir)

    report = json.loads((fdir / "report.json").read_text(encoding="utf-8"))
    for section in REPORT_REQUIRED_SECTIONS:
        assert section in report, section

    _arm_ordering_token(fdir)
    result = foundry_mark_phase_complete("done", project_root)

    assert result["ok"] is True, result
    assert result["phase"] == "F6"




def test_a_report_missing_one_section_is_refused_naming_that_section(run_env):
    """GI-006 verbatim: 'The lead may append prose but cannot omit a section.'

    The omission half. A lead editing the generated report is expected — that is
    what "may append prose" means — so the check reads `report.json`'s keys
    rather than the markdown, and names what was removed so the lead knows the
    fix is to regenerate rather than to hunt.
    """
    project_root, fdir = run_env
    _write_spec(fdir, ["FR-1"])
    _write_state(fdir, phase="F4", cycle=1)
    _write_verdicts(fdir, [{"requirement_id": "FR-1", "verdict": "VERIFIED"}])
    _defect_ledger(fdir, [])
    _generate_report(project_root, fdir)

    document = json.loads((fdir / "report.json").read_text(encoding="utf-8"))
    del document["latent_backlog"]
    (fdir / "report.json").write_text(json.dumps(document), encoding="utf-8")

    _arm_ordering_token(fdir)
    result = foundry_mark_phase_complete("done", project_root)

    assert result.get("ok") is not True
    assert "latent_backlog" in result["error"]




def test_appending_prose_to_the_markdown_never_blocks_done(run_env):
    """GI-006 exactly: 'The lead may APPEND prose but cannot OMIT a section.'

    APPENDING is the tolerance, and it is the only one. `_markdown_missing_sections`
    matches a whole trimmed `## <title>` line anywhere in the document, at any
    depth and in any order, so a lead's own headings and paragraphs can sit
    between, above and below the generated ones without hiding any.

    D-142 — WHAT THIS DOCSTRING USED TO CLAIM. It read "`report_status` reads
    the JSON precisely so that appending prose, REWORDING A HEADING or
    REFLOWING A TABLE — all things the lead is allowed to do — cannot make a
    present section look absent", and two of those three were false at HEAD.
    Driven: rewording `## LATENT backlog` to `## Latent backlog (reworded)`
    makes `report_status` return present False, missing ['latent_backlog'],
    because the match is on the whole heading line. The body exercised only
    the appending case, so the test asserted a contract the code did not hold
    AND did not check — a docstring is what the next maintainer reads to learn
    what the guarantee is, and this one licensed an edit that breaks the gate.

    Both directions are now driven, so the sentence cannot drift from the
    behaviour again: appending passes, and rewording a heading is reported as
    the omission it is.
    """
    project_root, fdir = run_env
    _write_spec(fdir, ["FR-1"])
    _write_state(fdir, phase="F4", cycle=1)
    _write_verdicts(fdir, [{"requirement_id": "FR-1", "verdict": "VERIFIED"}])
    _defect_ledger(fdir, [])
    _generate_report(project_root, fdir)
    md = fdir / "REPORT.md"
    generated = md.read_text(encoding="utf-8")

    # The tolerance GI-006 grants: appended prose, including the lead's own
    # headings, above and below the generated ones.
    md.write_text(
        "## Lead's preamble\n\nMine too.\n\n"
        + generated
        + "\n## Lead's postscript\n\nAll mine.\n",
        encoding="utf-8",
    )
    _arm_ordering_token(fdir)
    assert foundry_mark_phase_complete("done", project_root)["ok"] is True

    # And the half the retired sentence got wrong: a REWORDED heading is an
    # omitted section, and the check says so by name.
    from foundry_mcp.tools.foundry_report import report_status

    reworded = generated.replace(
        "## LATENT backlog", "## Latent backlog (reworded)"
    )
    assert reworded != generated, "the fixture must actually reword a heading"
    md.write_text(reworded, encoding="utf-8")
    status = report_status(fdir)
    assert status["present"] is False
    assert "latent_backlog" in status["missing_sections"], status




def test_re_filing_an_untiered_record_classifies_it_in_place(run_env):
    """FR-051 verbatim: 'Blocks like LIVE UNTIL A STREAM RE-FILES IT WITH A
    TIER.' AC-008. D-062.

    The exit FR-051 names was unimplemented, and following the server's own
    hint made the ledger worse. `_blocking_defects` says "have the filing
    stream re-file each untiered defect with tier=LIVE or tier=LATENT"; the
    batch door only reopened records already `fixed` or appended new ones, so
    the identical finding came back as a SECOND open record beside the untiered
    one. Driven: {'added': 1, 'total_open': 2}, D-001 still untiered, blocking
    unchanged at 1. A resumed pre-change run had no cheap exit at all.
    """
    project_root, fdir = run_env
    _untiered_open(fdir)
    assert _blocking_defects(fdir)["unknown"] == ["D-001"]

    result = foundry_sync_defects(
        3,
        [{
            "source": "trace", "type": "UNWIRED", "symbol": "foundry_next",
            "file": "src/api/a.py", "class": "UNWIRED_SURFACE",
            "tier": "LATENT",
            "reproduction_attempted": (
                "drove every caller of the display path; none reaches the "
                "branch, so nothing reproduced"
            ),
            "description": "re-filed with the tier the record never carried",
        }],
        project_root,
    )

    assert result["ok"] is True, result
    assert result["added"] == 0, "a re-filing classifies; it does not duplicate"
    assert result["retiered"] == 1
    assert result["retiered_ids"] == ["D-001"]
    assert result["total_open"] == 1

    ledger = json.loads((fdir / "defects.json").read_text(encoding="utf-8"))
    assert len(ledger["defects"]) == 1
    record = ledger["defects"][0]
    assert record["id"] == "D-001", "the id survives, so every citation does"
    assert record["tier"] == "LATENT"
    assert record["retiered_in_cycle"] == 3
    assert record["reproduction_attempted"]

    # ...and the gate that was blocked now passes, which is the whole exit.
    assert _blocking_defects(fdir)["blocking"] == 0
    assert _blocking_defects(fdir)["latent"] == ["D-001"]




def test_re_filing_an_untiered_record_as_live_leaves_it_blocking(run_env):
    """The other direction: classifying is not clearing. A stream that drives
    the failure re-files it LIVE, and the record blocks exactly as it did —
    with the difference that now somebody has said so."""
    project_root, fdir = run_env
    _untiered_open(fdir)

    result = foundry_sync_defects(
        3,
        [{
            "source": "trace", "type": "UNWIRED", "symbol": "foundry_next",
            "file": "src/api/a.py", "class": "UNWIRED_SURFACE", "tier": "LIVE",
            "description": "drove the door and read the wrong result back",
        }],
        project_root,
    )

    assert result["retiered"] == 1
    blocking = _blocking_defects(fdir)
    assert blocking["live"] == ["D-001"]
    assert blocking["unknown"] == []




def test_an_otherwise_perfect_run_still_cannot_reach_done_from_halted(run_env):
    """D-081 at its strongest: everything else about this run passes.

    Report generated, every requirement VERIFIED, zero open defects, no
    escalated class, no active team — and the run is HALTED, so DONE is refused
    and the halt is the reason given. HALTED and DONE are both terminal and mean
    opposite things: DONE is 'every requirement verified and every LIVE defect
    closed', HALTED is 'we ran out of cycles with work still open'. A run that
    can walk from one to the other collapses the distinction the cap exists to
    draw.
    """
    project_root, fdir = run_env
    _ready_for_the_end_gates(project_root, fdir)
    _defect_ledger(fdir, [])
    _generate_report(project_root, fdir)

    # Prove the fixture is otherwise clean BEFORE the halt is written.
    _at_the_phase_for(fdir, "done")
    _arm_ordering_token(fdir)
    assert foundry_gate("done", project_root)["passed"] is True

    _halted_run(fdir)

    _arm_ordering_token(fdir)
    gate = foundry_gate("done", project_root)
    assert gate["passed"] is False, gate
    assert "HALTED" in gate["reason"]

    for token in ("done", "nyquist_done"):
        _arm_ordering_token(fdir)
        result = foundry_mark_phase_complete(token, project_root)
        assert result.get("ok") is not True, (token, result)
        assert "HALTED" in result["error"], (token, result)

    state = json.loads((fdir / "state.json").read_text(encoding="utf-8"))
    assert state["phase"] == RUN_PHASE_HALTED
    assert state["phase"] != "F6"




def test_done_preconditions_names_the_halt_and_lets_it_claim_the_reason(run_env):
    """D-081's filed symbol: `_done_preconditions` is the SHARED evaluation both
    F6 doors consult, so the halt has to be visible THERE and not only in the
    blanket guards at the gate and the transition.

    It claims `reason` LAST, which in this function's ordering discipline means
    it wins: on a halted run every other failure is a detail of a run that has
    already stopped, and telling a lead to fix three open LIVE defects on a run
    that ended two cycles ago sends them to do work that cannot be gated.
    """
    project_root, fdir = run_env
    _ready_for_the_end_gates(project_root, fdir)
    _defect_ledger(fdir, [_tiered("D-001", "LIVE")])
    _generate_report(project_root, fdir)
    _halted_run(fdir)


    outcome = _done_preconditions(fdir, project_root)

    assert outcome["passed"] is False
    assert "HALTED" in outcome["reason"], outcome
    assert "D-001" not in outcome["reason"], outcome["reason"]
    row = next(c for c in outcome["checklist"] if c["check"].startswith("run_not_halted"))
    assert row["ok"] is False
    assert "max-cycles" in row["halted_reason"]

    # fallout AC-062 (D-088) — AND IT IS THE ONLY RUNG, WHICH IS THE POINT.
    #
    # This used to fail the ladder and CARRY ON, so a halted run paid for the
    # whole evaluation — including the evidence sweep, which re-executes the
    # committed corpus in a detached worktree — before being refused by a rung
    # that defeats every other one by rank. `_GATE_RANK_HALTED` is 0 precisely
    # because "on a run that has already stopped every other remedy is work
    # that cannot be gated", so computing those remedies is spending a worktree
    # to publish advice nobody can act on.
    #
    # The rung SHORT-CIRCUITS now, which is also what makes it reachable
    # through both public doors: it is the same `_halted_outcome` every
    # `_<token>_preconditions` asks first, so the guards those doors used to
    # state above their branch chains are gone and this rung is what speaks.
    assert [r["rank"] for r in outcome["refusals"]] == [_GATE_RANK_HALTED], outcome
    assert [c["check"] for c in outcome["checklist"]] == [
        "run_not_halted (halted_at_cycle=2)"
    ], outcome["checklist"]
    # The halt record rides out as facts, so nothing a caller read off the
    # retired door-level guard is lost.
    assert outcome["halted"] is True and outcome["halted_at_cycle"] == 2, outcome
    assert "report_generated" in outcome and "max_cycles" in outcome, sorted(outcome)




def test_a_running_run_carries_the_passing_halt_row(run_env):
    """The mirror: the named check is present and OK on a run that has not
    halted, so the checklist a lead reads answers the question either way rather
    than only when the answer is bad."""
    project_root, fdir = run_env
    _ready_for_the_end_gates(project_root, fdir)
    _defect_ledger(fdir, [])
    _generate_report(project_root, fdir)

    _at_the_phase_for(fdir, "done")
    outcome = _done_preconditions(fdir, project_root)

    assert outcome["passed"] is True, outcome
    row = next(c for c in outcome["checklist"] if c["check"] == "run_not_halted")
    assert row["ok"] is True




def test_the_untiered_hint_names_both_filing_doors(run_env):
    """FR-051 / D-077: the hint used to say 'have the filing stream re-file it'
    while only one door matched an existing untiered record, so a stream that
    followed it through the other door got a SECOND open record beside the
    untiered one and the blocking count did not move. A hint that is actionable
    only through the door it does not name is not a hint.
    """
    project_root, fdir = run_env
    _defect_ledger(fdir, [_tiered("D-001", None)])

    blocking = _blocking_defects(fdir)

    assert blocking["blocking"] == 1
    assert "Foundry-Defect" in blocking["hint"]
    assert "Foundry-Sync" in blocking["hint"]
    assert "IN PLACE" in blocking["hint"]




def test_the_sync_door_still_re_tiers_in_place(run_env):
    """The behaviour the helper has to preserve: the record KEEPS ITS ID, so
    every citation and every task already naming it stays valid, and the ledger
    does not grow a duplicate."""
    project_root, fdir = run_env
    _write_state(fdir, phase="F2", cycle=1)
    _defect_ledger(fdir, [_tiered("D-001", None)])

    result = foundry_sync_defects(
        1,
        [{
            "description": "the handler never calls the store",
            "source": "trace", "type": "UNWIRED", "file": "src/api/a.py",
            "symbol": "handle", "tier": "LATENT", "class": "UNWIRED_SURFACE",
            "reproduction_attempted": "drove every caller; none reach it",
        }],
        project_root,
    )

    assert result["retiered_ids"] == ["D-001"], result
    assert result["added"] == 0, result
    records = json.loads((fdir / "defects.json").read_text(encoding="utf-8"))["defects"]
    assert [d["id"] for d in records] == ["D-001"]
    assert records[0]["tier"] == "LATENT"
    assert _blocking_defects(fdir)["blocking"] == 0




# --------------------------------------------------------------------------- #
# D-122 / D-123 — the ASSAY gate's two hints describe the run that exists
# --------------------------------------------------------------------------- #


def test_the_streams_hint_names_the_roster_the_transition_recorded(run_env):
    """FR-012 / AC-017: 'Foundry-Next names exactly that required set' — and so
    must the refusal that blocks on it. D-122.

    Driven on a FULL-width cycle whose recorded `required_streams` are trace,
    prove, test, research_audit and test01 with only the last two unmarked. The
    gate refused with reason "Verification streams incomplete: research_audit
    test01" and, beside it, the hint "All streams (trace, prove, sight, test)
    must complete before ASSAY" — the roster from before FR-012 widened it. It
    omits both streams the refusal is about and names `sight`, which this run's
    recorded roster does not require. A lead following the hint runs the wrong
    streams and never learns which two are owed.
    """
    project_root, fdir = run_env
    _write_manifest_with_castings(fdir, ["src/api/a.py"], no_ui=True)
    _write_state(fdir, phase="F2", cycle=1)
    _defect_ledger(fdir, [])
    recorded = _record_full_inspect_mode(
        fdir, cycle=1, required_streams=vocab.FULL_ROSTER_STREAMS
    )
    assert "research_audit" in recorded["required_streams"], recorded
    assert "test01" in recorded["required_streams"], recorded
    for stream in ("trace", "prove", "test"):
        (fdir / f".{stream}-complete").write_text(
            "2020-01-01T00:00:00+00:00 cycle=1\nitems_checked=10\n"
            "items_total=10\ncoverage=100%\nfindings=0\n",
            encoding="utf-8",
        )

    _arm_ordering_token(fdir)
    gate = foundry_gate("assay", project_root)

    assert gate["passed"] is False
    assert "research_audit" in gate["reason"] and "test01" in gate["reason"]
    # The hint names the same set the reason is computed from...
    for stream in recorded["required_streams"]:
        assert stream in gate["hint"], (stream, gate["hint"])
    # ...and not the retired roster.
    assert "trace, prove, sight, test" not in gate["hint"]
    assert "sight" not in gate["hint"], gate["hint"]




def test_the_inspect_clean_hint_names_a_call_the_server_accepts(run_env):
    """FR-006 / AC-008 / FR-044: the door that closes an INSPECT is
    Foundry-Phase(phase='inspect_clean'). D-123.

    Driven on a run with every required stream recorded, one fixed defect and
    no `.inspect-clean` marker: the gate refused with reason "GRIND fixed
    defects but INSPECT has not re-verified" and hint "Run full INSPECT cycle
    after GRIND. Call foundry_mark_inspect_clean when clean."
    `grep -rn foundry_mark_inspect_clean plugins/foundry` found that name in
    the hint string and NOWHERE else — no MCP tool, no Python function, no
    prose surface carries it. The refusal's only stated next move was a call
    the server would reject.
    """
    project_root, fdir = run_env
    _write_manifest_with_castings(fdir, ["src/api/a.py"], no_ui=True)
    _write_state(fdir, phase="F2", cycle=1)
    _defect_ledger(fdir, [{
        "id": "D-001", "cycle": 1, "source": "trace", "type": "UNWIRED",
        "description": "d", "file": "src/api/a.py", "symbol": "h",
        "status": "fixed", "tier": "LIVE", "class": "K", "fixed_in_cycle": 1,
    }])
    recorded = _record_full_inspect_mode(fdir, cycle=1)
    for stream in recorded["required_streams"]:
        (fdir / f".{stream}-complete").write_text(
            "2020-01-01T00:00:00+00:00 cycle=1\nitems_checked=10\n"
            "items_total=10\ncoverage=100%\nfindings=0\n",
            encoding="utf-8",
        )

    # fallout AC-056 — THE ARM THIS PIN WAS WRITTEN AGAINST CANNOT BE A REFUSAL.
    #
    # `.inspect-clean` is written by ONE call: `Foundry-Phase('inspect_clean')`,
    # the transition the `assay` gate guards. Under one shared routine per
    # transition token, a rung on that marker would make the transition refuse
    # itself, and it already made the documented sequence impossible — the gate
    # demanded a marker only the call it precedes writes. It is a NON-REFUSING
    # checklist fact now, and the substance the arm was reaching for — "GRIND
    # fixed defects and this INSPECT has not re-verified" — is carried as a real
    # refusal by `fixes_after_decision`, which measures it against the recorded
    # width decision instead of against a marker.
    #
    # D-123's guarantee is unchanged and is what this still pins: whatever the
    # door says, the call it names is one the server accepts.
    state = json.loads((fdir / "state.json").read_text(encoding="utf-8"))
    state["inspect_modes"][-1]["fixes_after_decision"] = ["D-001"]
    (fdir / "state.json").write_text(json.dumps(state), encoding="utf-8")

    _arm_ordering_token(fdir)
    gate = foundry_gate("assay", project_root)

    assert gate["passed"] is False
    assert "fixed after this INSPECT's width was decided" in gate["reason"], gate
    assert "foundry_mark_inspect_clean" not in gate["hint"]
    assert "Foundry-Phase(phase='inspect_start')" in gate["hint"], gate["hint"]

    # And the named calls are ones the server actually accepts.
    assert "inspect_clean" in PHASE_TOKENS
    assert "inspect_start" in PHASE_TOKENS

    # The marker itself is still REPORTED, so nothing a lead could read is lost.
    fact = next(c for c in gate["checklist"] if c["check"] == "inspect_clean")
    assert fact["ok"] is False and fact["refuses"] is False, fact




# --------------------------------------------------------------------------- #
# D-133 — the corpus re-executes before NYQUIST and before DONE
# --------------------------------------------------------------------------- #


def test_the_done_checklist_carries_an_evidence_rung(run_env):
    """GI-002 verbatim: the whole corpus is swept 'before ASSAY/NYQUIST/DONE'.
    D-133.

    `_done_preconditions`' checklist was report_generated,
    escalated_classes_cleared, run_not_halted, spec_requirements_parsed,
    all_verified, zero_blocking_defects, no_active_teams, verdict_coverage —
    no evidence rung of any kind, and `fixes_after_decision` (the stamp that
    catches the same shape one phase earlier) is read only by `inspect_clean`.
    So a fix landing in F5 or F5.5 reached DONE with the committed corpus never
    re-executed over it.

    Asserted on the CHECKLIST as well as the verdict, because the checklist is
    what a lead reads to learn what DONE requires, and a guarantee absent from
    it is a guarantee nobody can see.
    """
    project_root, fdir = run_env
    _write_spec(fdir, ["FR-1"])
    _write_state(fdir, phase="F4", cycle=1)
    _write_verdicts(fdir, [{"requirement_id": "FR-1", "verdict": "VERIFIED"}])
    _defect_ledger(fdir, [])
    _generate_report(project_root, fdir)

    _at_the_phase_for(fdir, "done")

    outcome = _done_preconditions(fdir, project_root)

    rungs = [c["check"].split(" ")[0] for c in outcome["checklist"]]
    assert "evidence_reproduces_at_head" in rungs, rungs
    assert outcome["passed"] is True, outcome




def test_a_log_that_no_longer_reproduces_refuses_nyquist_and_done(run_env,
                                                                  monkeypatch):
    """GI-002 / CT-007: the sweep 'refuses on mismatch', naming each log.

    Both terminal doors, because D-133's drive reached NYQUIST and DONE through
    two different transitions and neither swept. The sweep engine is casting
    5's; what is pinned here is that this module CALLS it at these boundaries
    and refuses on its answer.
    """
    project_root, fdir = run_env
    _write_spec(fdir, ["FR-1"])
    _write_verdicts(fdir, [{"requirement_id": "FR-1", "verdict": "VERIFIED"}])
    _defect_ledger(fdir, [])
    _generate_report(project_root, fdir)

    def _mismatch(_fdir, _pr, _entry, *, full):
        assert full is True, "a terminal boundary sweeps the WHOLE corpus"
        return {
            "ok": False,
            "record": {"scope": "full", "logs_reexecuted": ["evidence/a.log"]},
            "mismatches": [{"log": "evidence/a.log", "reason": "output differs"}],
            "error": "",
        }

    patch_everywhere(monkeypatch, "_sweep_evidence_at_boundary", _mismatch)

    _write_state(fdir, phase="F5", cycle=1)
    _arm_ordering_token(fdir)
    nyq = foundry_mark_phase_complete("nyquist", project_root)
    assert nyq.get("ok") is not True, nyq
    assert "evidence/a.log" in nyq["error"], nyq
    assert json.loads((fdir / "state.json").read_text())["phase"] == "F5", (
        "a refused crossing leaves the run where it was"
    )

    _write_state(fdir, phase="F4", cycle=1)
    _arm_ordering_token(fdir)
    done = foundry_mark_phase_complete("done", project_root)
    assert done.get("ok") is not True, done
    assert "evidence/a.log" in json.dumps(done), done




def _done_ready(project_root: str, fdir: Path) -> None:
    """Everything DONE needs except the evidence rung's answer."""
    _write_spec(fdir, ["FR-1"])
    _write_state(fdir, phase="F5.5", cycle=1)
    _write_verdicts(fdir, [{"requirement_id": "FR-1", "verdict": "VERIFIED"}])
    _defect_ledger(fdir, [])
    _generate_report(project_root, fdir)




def test_a_stripped_corpus_with_no_recorded_pass_refuses_done_by_name(run_env):
    """GI-002 verbatim: the SERVER sweeps at the boundary 'and refuses on
    mismatch ... the whole corpus ... before ASSAY/NYQUIST/DONE'.

    The strip is a COMMIT, so it moves HEAD and the memo with it, and the
    corpus it deleted re-executes NOTHING — which reports the same zero
    mismatches a clean whole-corpus pass reports. Driven end to end: a log that
    genuinely no longer reproduces refuses DONE; the mandated strip is then run
    verbatim; the SAME door is re-asked and must still refuse, now naming the
    strip rather than the log.
    """
    project_root, fdir = run_env
    _evidence_repo(project_root)
    _committed_evidence(
        project_root, "casting-1-handler.log",
        "echo the-handler-calls-the-store", "the-handler-does-not\n",
    )
    _done_ready(project_root, fdir)

    _at_the_phase_for(fdir, "done")

    before = _done_preconditions(fdir, project_root)
    assert before["passed"] is False, before
    assert "casting-1-handler.log" in json.dumps(before), before

    _strip_evidence(project_root)

    _at_the_phase_for(fdir, "done")

    after = _done_preconditions(fdir, project_root)
    assert after["passed"] is False, after
    assert EVIDENCE_STRIPPED_TOKEN in after["reason"], after
    rung = next(
        c for c in after["checklist"]
        if c["check"].startswith("evidence_reproduces_at_head")
    )
    assert rung["ok"] is False, rung
    assert rung["token"] == EVIDENCE_STRIPPED_TOKEN, rung
    assert rung["logs_reexecuted"] == 0, rung




def test_the_mandated_f6_order_earns_the_pass_the_strip_then_spends(run_env):
    """AC-014 / FR-042: 'at the INSPECT before ASSAY, NYQUIST or DONE the sweep
    covers the whole corpus.'

    The documented F6 order — Foundry-Gate(phase="done") BEFORE the `git rm` —
    is what makes that true across the strip. The gate re-executes the whole
    corpus at the commit that still carries it and records `last_full_pass`
    naming that commit; `Foundry-Phase("done")` after the strip is then
    satisfied by the recorded pass rather than by a tree the corpus is gone
    from. `commands/start.md` states this order and
    `tests/test_lead_prose.py#test_the_f6_sequence_sweeps_before_it_strips`
    fails if the two sentences ever swap.
    """
    project_root, fdir = run_env
    _evidence_repo(project_root)
    _committed_evidence(
        project_root, "casting-1-handler.log", "echo reproduces", "reproduces\n"
    )
    _done_ready(project_root, fdir)

    swept_at = _head(project_root)
    _at_the_phase_for(fdir, "done")
    gate = _done_preconditions(fdir, project_root)
    assert gate["passed"] is True, gate
    gate_rung = next(
        c for c in gate["checklist"]
        if c["check"].startswith("evidence_reproduces_at_head")
    )
    assert gate_rung["logs_reexecuted"] == 1, gate_rung
    assert gate_rung["corpus_size"] == 1, gate_rung

    marker = json.loads(
        (fdir / TERMINAL_SWEEP_FILENAME).read_text(encoding="utf-8")
    )
    assert marker["last_full_pass"]["head"] == swept_at, marker
    assert marker["last_full_pass"]["corpus_size"] == 1, marker

    _strip_evidence(project_root)

    _at_the_phase_for(fdir, "done")

    after = _done_preconditions(fdir, project_root)
    assert after["passed"] is True, after
    rung = next(
        c for c in after["checklist"]
        if c["check"].startswith("evidence_reproduces_at_head")
    )
    assert rung["ok"] is True, rung
    assert rung["logs_reexecuted"] == 0, rung
    # ...and it says WHICH commit the pass it is standing on was taken at, so
    # "logs=0" is never read as "the corpus reproduced".
    assert rung["pre_strip_pass"]["head"] == swept_at, rung
    # The durable half survived the strip, which moved HEAD and rewrote the
    # per-HEAD memo — the one thing it exists to survive.
    marker = json.loads(
        (fdir / TERMINAL_SWEEP_FILENAME).read_text(encoding="utf-8")
    )
    assert marker["last_full_pass"]["head"] == swept_at, marker




def test_a_run_that_committed_no_evidence_at_all_still_passes(run_env):
    """The third state, and the reason the discriminator is not "logs == 0".

    A refactor or docs-only run commits no evidence, so its terminal sweep
    honestly covers nothing and there is no corpus history for a strip to have
    removed. Refusing it would be this defect's own shape inverted — a door
    refusing a run for the absence of something it never owed.
    """
    project_root, fdir = run_env
    _evidence_repo(project_root)
    _done_ready(project_root, fdir)

    _at_the_phase_for(fdir, "done")

    outcome = _done_preconditions(fdir, project_root)

    assert outcome["passed"] is True, outcome
    rung = next(
        c for c in outcome["checklist"]
        if c["check"].startswith("evidence_reproduces_at_head")
    )
    assert rung["ok"] is True and rung["token"] == "", rung
    assert _evidence_corpus_existed(fdir, project_root) is False




def test_every_terminal_crossing_takes_the_same_stripped_corpus_refusal(run_env):
    """GI-002 verbatim: the whole corpus is swept "when the FULL rule fires or
    before ASSAY/NYQUIST/DONE".

    `_done_preconditions` is ONE evaluation with four callers (both gates and
    both transitions), which is the D-037 discipline that keeps the gate and
    the transition from disagreeing about what DONE means. D-159 is that
    discipline's gap: the `nyquist` branch called `_terminal_evidence_sweep`
    ITSELF and refused on `ok` alone, so it never saw the three states D-149
    established. Driven — a stripped corpus refused `nyquist_done` and `done`
    and ADMITTED `nyquist`, phase F5.5, corpus_size 0. A --nyquist run had a
    second route around the rung: enter F5.5 through the door that did not
    apply it.

    So every crossing is driven on the SAME stripped tree, from the phase it is
    actually made from.
    """
    project_root, fdir = run_env
    _evidence_repo(project_root)
    _committed_evidence(
        project_root, "casting-2-beta.log", "echo reproduces", "reproduces\n"
    )
    _done_ready(project_root, fdir)
    _strip_evidence(project_root)

    for token, source_phase, _door in _TERMINAL_EVIDENCE_CROSSINGS:
        _write_state(fdir, phase=source_phase, cycle=1, temper=True, nyquist=True)
        _arm_ordering_token(fdir)
        result = foundry_mark_phase_complete(token, project_root)
        assert result.get("ok") is not True, (token, result)
        assert EVIDENCE_STRIPPED_TOKEN in json.dumps(result), (token, result)
        assert json.loads((fdir / "state.json").read_text(encoding="utf-8"))[
            "phase"
        ] == source_phase, (
            f"{token} moved the run on a refusal; a refused crossing must leave "
            f"no trace it was attempted"
        )

    gate = foundry_gate("done", project_root)
    assert gate["passed"] is False, gate
    assert EVIDENCE_STRIPPED_TOKEN in gate["reason"], gate




# --------------------------------------------------------------------------- #
# D-171 — THE HALTED NEXT-ACTION READS THE REPORT IT NAMES.
#
# D-165 one surface along. `_halt_if_capped` behaves correctly on a failed
# generation — it records `halted_report_error`, says "The report could NOT be
# generated" and names Foundry-Report — and `_halted_refusal` reads that record.
# `_compute_next_action`'s HALTED branch read NEITHER: it asserted, as fact,
# "The report has been generated at REPORT.md and names every open defect by
# tier" and set `details.report` to the path of a file that does not exist.
#
# D-165's fix reasoned that every later REFUSAL names the call that can still
# write the report, and Foundry-Next is not a refusal — so this branch was
# missed. It is also the ONE surface a lead consults next, and HALTED has no
# exit by design, so nothing regenerates the report on its own: the lead's only
# stated next move was to read a document that was never written.
#
# `grep -rn halted_report_error tests/` returned nothing before this block.
# --------------------------------------------------------------------------- #


def _halt_with_a_failed_report(project_root, fdir, monkeypatch) -> dict:
    """Drive the real cap transition with the report generation failing.

    The same window `test_a_halt_whose_report_failed_says_so` drives, and for
    the same reason: `_artifact_guard` runs at the entry point and the report is
    generated several frames later, in a tree five castings commit into at once.
    """
    _write_spec(fdir, ["FR-1"])
    _write_state(fdir, phase="F2", cycle=2, max_cycles=2)
    _write_verdicts(fdir, [{"requirement_id": "FR-1", "verdict": "VERIFIED"}])
    _defect_ledger(fdir, [
        _tiered("D-001", "LIVE"),
        _tiered("D-002", "LATENT", reproduction_attempted="AST sweep finds 0 sites"),
    ])
    (fdir / ".tasks-generated").write_text("x\n", encoding="utf-8")

    real_generate = _generate_report

    def _corrupted_mid_transition(pr, run_dir):
        (fdir / "verdicts.json").write_text("{not json", encoding="utf-8")
        return real_generate(pr, run_dir)

    patch_everywhere(monkeypatch, "_generate_report", _corrupted_mid_transition)
    _arm_ordering_token(fdir)
    halted = foundry_mark_phase_complete("grind_start", project_root)
    patch_everywhere(monkeypatch, "_generate_report", real_generate)
    return halted




def test_the_halted_next_action_does_not_claim_an_unwritten_report(run_env, monkeypatch):
    """FR-045 verbatim: 'state.json phase becomes HALTED, the report is written
    naming every open LIVE and LATENT defect, and Foundry-Next then reports the
    run halted and stops dispatching'.

    The transition and the notice must agree about the artifact. Driven end to
    end: the halt records the failure and says so, and the next action a lead
    reads must say the same thing rather than the opposite.
    """
    project_root, fdir = run_env
    halted = _halt_with_a_failed_report(project_root, fdir, monkeypatch)

    assert halted["ok"] is True, halted
    assert halted["report_generated"] is False, halted
    assert not (fdir / "REPORT.md").exists()
    assert json.loads((fdir / "state.json").read_text())["halted_report_error"]

    action = _compute_next_action(project_root)

    assert action["action"] == "halted"
    assert "was NOT generated" in action["instructions"], action["instructions"]
    assert "has been generated" not in action["instructions"], action["instructions"]
    assert "verdicts.json" in action["instructions"], (
        "the recorded error is what tells the lead WHAT to repair"
    )
    assert "Foundry-Report" in action["instructions"], (
        "HALTED has no exit, so the one call that can still write the report "
        "must be named or the lead has no next move at all"
    )
    # The field is named as what it IS, so a caller cannot read a promise out
    # of its presence — the same shape `_halted_refusal` uses.
    assert action["details"]["report"] is None, action["details"]
    assert action["details"]["report_generated"] is False, action["details"]
    assert action["details"]["report_error"], action["details"]
    # ...and it still stops dispatching, which is the rest of FR-052.
    assert "Do NOT dispatch another wave" in action["instructions"]




def test_the_halted_next_action_names_the_report_once_it_exists(run_env, monkeypatch):
    """The discrimination, not a deletion. Read the other way this would say
    "not written" about a report the lead had just regenerated with
    Foundry-Report — the same defect with the sign flipped — so the FILE is the
    ground truth and the recorded error only supplies the REASON when it is
    absent.
    """
    project_root, fdir = run_env
    _halt_with_a_failed_report(project_root, fdir, monkeypatch)

    # The operator does exactly what the notice asks: repair, then regenerate.
    _write_verdicts(fdir, [{"requirement_id": "FR-1", "verdict": "VERIFIED"}])
    from foundry_mcp import server as foundry_server

    previous = foundry_server._project_root
    try:
        foundry_server._project_root = project_root
        assert foundry_server._DISPATCH["Foundry-Report"]({})["ok"] is True
    finally:
        foundry_server._project_root = previous
    assert (fdir / "REPORT.md").exists()

    action = _compute_next_action(project_root)

    assert "The report has been generated at REPORT.md" in action["instructions"]
    assert "was NOT generated" not in action["instructions"]
    assert action["details"]["report"] == str(fdir / "REPORT.md"), action["details"]
    assert action["details"]["report_generated"] is True




def test_the_refusal_that_speaks_is_the_highest_ranked_one(run_env):
    """FR-011 / AC-016 / CT-008 — the ordering rule, on a run failing four
    checks at once.

    `reason` and `hint` are the pair a terminal prints, and they come from ONE
    check — the highest-ranked failing one, whose remedy no other failing check
    can defeat. Every other failing check's sentence is still published under
    `refusals`, because "the width refusal the arm above computed is discarded"
    is how D-186 names the harm: nothing is discarded now, one thing speaks.
    """
    project_root, fdir = run_env
    _write_manifest_with_castings(fdir, ["src/api/a.py"], no_ui=True)
    _write_state(fdir, phase="F2", cycle=1)
    # Open LIVE defect + no recorded width + no streams + no marker + a team.
    _defect_ledger(fdir, [_tiered("D-001", "LIVE")])
    _teams_active(True)

    _arm_ordering_token(fdir)
    gate = foundry_gate("assay", project_root)

    assert gate["passed"] is False, gate
    ranks = [r["rank"] for r in gate["refusals"]]
    assert ranks == sorted(ranks), gate["refusals"]
    assert len(ranks) >= 4, gate["refusals"]
    assert gate["reason"] == gate["refusals"][0]["reason"], gate
    assert gate["hint"] == gate["refusals"][0]["hint"], gate
    assert all(r["hint"].strip() for r in gate["refusals"]), gate["refusals"]

    # The width is the top rank at this door: `Foundry-Phase(inspect_start)` is
    # the one remedy here that no other failing check refuses.
    #
    # fallout AC-056: the rung states the CHECK, and the door that refused
    # states what it could not do. `Foundry-Phase('inspect_clean')` prefixes
    # "Cannot mark INSPECT clean — "; the gate answers "may I?" and renders the
    # check bare. One evaluation, one sentence, and the prefix is the whole of
    # the difference — so the substance is what this pin reads.
    assert "no recorded width" in gate["reason"], gate["reason"]
    # ...and every check that failed is still named, in rank order.
    published = " ".join(r["reason"] for r in gate["refusals"])
    for fragment in ("Active teammates", "D-001", "streams incomplete"):
        assert fragment in published, (fragment, gate["refusals"])




def test_a_passing_gate_publishes_no_refusals_at_all(run_env):
    """A key that is always present is a key nobody reads — the same rule
    `Foundry-Spend`'s warnings follow. `refusals` accompanies `reason`, and both
    exist only when the gate refused."""
    project_root, fdir = run_env
    _write_spec(fdir, ["FR-1"])
    _ready_for_the_end_gates(project_root, fdir)
    _defect_ledger(fdir, [])
    _generate_report(project_root, fdir)

    _arm_ordering_token(fdir)
    gate = foundry_gate("assay", project_root)

    assert gate["passed"] is True, gate
    assert "refusals" not in gate and "reason" not in gate, gate




def test_the_grind_gate_names_the_ledger_before_the_tasks_marker(run_env):
    """FR-026 / NFR-005 — the same rule applied to the branch beside the one
    D-186 was driven on, and stated because it CHANGES what a lead is told.

    Under the last-writer-wins ladder a GRIND gate with an empty ledger and no
    `.tasks-generated` marker answered "defects-to-tasks has not been run" with
    the remedy "Call Foundry-Tasks before entering GRIND" — a call that has
    nothing to packet on a ledger with nothing open, so following it cannot
    change this gate's answer. The ledger read outranks the marker now.
    """
    project_root, fdir = run_env
    _write_state(fdir, phase="F2", cycle=1)
    _defect_ledger(fdir, [])

    _arm_ordering_token(fdir)
    gate = foundry_gate("grind", project_root)

    assert gate["passed"] is False, gate
    assert gate["reason"] == "No open defects to grind", gate["reason"]
    assert "skip to ASSAY" in gate["hint"], gate["hint"]
    published = [r["reason"] for r in gate["refusals"]]
    assert "defects-to-tasks has not been run" in published, published




def test_the_inspect_gate_names_the_teammates_before_the_sight_url(run_env):
    """FR-026 / NFR-005 — the third branch that scans teams, same rule.

    Under source order the sight-URL check spoke last, so a run with a
    registered team AND no target_url was told to edit `manifest.json` while a
    teammate was still holding the tree. Shutting the team down is refused by
    nothing and everything else waits on it.
    """
    project_root, fdir = run_env
    _write_state(fdir, phase="F1", cycle=0)
    _write_manifest_with_castings(fdir, ["src/ui/App.tsx"], no_ui=False)
    (fdir / ".cast-complete").write_text("x\n", encoding="utf-8")
    _teams_active(True)

    _arm_ordering_token(fdir)
    gate = foundry_gate("inspect", project_root)

    assert gate["passed"] is False, gate
    assert "Active teammates" in gate["reason"], gate["reason"]
    assert gate["hint"] == _TEAMS_DOWN_HINT, gate["hint"]
    # Non-vacuous: the check this arm has to OUTRANK really did fail, and its
    # own sentence is published rather than thrown away.
    sight = next(c for c in gate["checklist"] if c["check"] == "sight_url")
    assert sight["ok"] is False, gate["checklist"]
    assert any("SIGHT" in r["hint"] for r in gate["refusals"]), gate["refusals"]




def test_the_done_door_names_the_open_live_defect_beside_a_stale_evidence_log(
    run_env,
):
    """AC-003 verbatim: 'With one LIVE instance of a CLEARED class still open,
    Foundry-Gate done and nyquist refuse naming that defect.'

    D-190's drive. The evidence rung claimed `reason` AFTER the blocking-defect
    arm, so on this state the done door answered '1 committed evidence log(s)
    no longer reproduce at HEAD: evidence/casting-1-alpha.log' and D-900
    appeared in neither `reason` nor `refusals` — only in the checklist. The
    ranks say why that is wrong rather than merely that it is: re-capturing a
    log is defeated by the open defect, because the corpus moves again when the
    defect is fixed and the log has to be captured a second time.
    """
    project_root, fdir = run_env
    _d190_state(project_root, fdir)

    _arm_ordering_token(fdir)
    gate = foundry_gate("done", project_root)

    assert gate["passed"] is False, gate
    assert "D-900" in gate["reason"], gate["reason"]
    assert "GRIND" in gate["hint"], gate["hint"]
    # The evidence rung really did fail too — without that this fixture proves
    # nothing about ordering — and its sentence is PUBLISHED, not discarded.
    published = {r["reason"] for r in gate["refusals"]}
    assert any("casting-1-alpha.log" in r for r in published), gate["refusals"]
    assert len(gate["refusals"]) == 2, gate["refusals"]
    assert [r["rank"] for r in gate["refusals"]] == [
        _GATE_RANK_DEFECTS, _GATE_RANK_EVIDENCE
    ], gate["refusals"]




def test_the_same_state_with_no_defect_open_answers_differently(run_env):
    """The control D-190 names: 'The SAME call on the SAME state with NO defect
    open returns a byte-identical reason and hint.'

    That identity is the defect stated as an observation — a door whose answer
    does not change when the thing it is refusing on changes is a door that was
    never reading it. Driven as its own case so the fix cannot be satisfied by
    a reason that merely MENTIONS the defect while still being produced by the
    evidence rung.
    """
    project_root, fdir = run_env
    _d190_state(project_root, fdir)

    _arm_ordering_token(fdir)
    with_defect = foundry_gate("done", project_root)

    _defect_ledger(fdir, [])
    _arm_ordering_token(fdir)
    without_defect = foundry_gate("done", project_root)

    assert with_defect["passed"] is False and without_defect["passed"] is False
    assert with_defect["reason"] != without_defect["reason"], (
        "the done door answers the same whether or not a LIVE defect is open"
    )
    assert "casting-1-alpha.log" in without_defect["reason"], without_defect
    assert with_defect["hint"] != without_defect["hint"], with_defect




def test_both_ac_003_doors_name_the_defect_on_the_same_state(run_env):
    """AC-003 names TWO doors — 'Foundry-Gate done and nyquist' — and D-190 is
    that they disagreed: nyquist answered '1 open LIVE defect(s): D-900' while
    done answered about a log. One state, both doors, one answer about the
    defect, driven through the MCP transport a client actually uses.
    """
    project_root, fdir = run_env
    _d190_state(project_root, fdir)

    # Asserted on `reason` — the ONE line a lead is answered with — and not on
    # the rendered blob. The checklist named D-900 the whole time (`live=
    # ['D-900']`), so a whole-output substring check passes on the very state
    # AC-003 was failing, which is why D-190 had to say "appears in neither
    # `reason` nor `refusals`" to describe it at all.
    for door in ("done", "nyquist"):
        _arm_ordering_token(fdir)
        gate = foundry_gate(door, project_root)
        assert gate["passed"] is False, (door, gate)
        assert "D-900" in gate["reason"], (door, gate["reason"])
        assert any("D-900" in r["reason"] for r in gate["refusals"]), (
            door, gate["refusals"]
        )

    # ...and the answer survives to a terminal, through the transport a client
    # actually uses (NFR-005).
    import foundry_mcp.server as foundry_server

    previous_root = foundry_server._project_root
    try:
        foundry_server._project_root = project_root
        for door in ("done", "nyquist"):
            _arm_ordering_token(fdir)
            assert "D-900" in _drive_mcp("Foundry-Gate", {"phase": door}), door
    finally:
        foundry_server._project_root = previous_root




def test_the_done_door_publishes_every_failing_check_and_states_a_live_remedy(
    run_env,
):
    """D-191's drive: four checks fail at once and `refusals` published ONE.

    NFR-005 / FR-026: the pair a terminal prints must come from a check whose
    remedy no other failing check defeats. Here the retired ladder rendered
    'ASSAY must write ALL verdicts to verdicts.json' — and driving it,
    `Foundry-Gate('assay')` is refused at `_GATE_RANK_DEFECTS` by the very
    defect this door declined to mention. CT-008's clause that the refusal
    NAMES the open LIVE defects was satisfied only in the checklist.
    """
    project_root, fdir = run_env
    ids = ["FR-1", "FR-2", "FR-3", "FR-4", "FR-5"]
    _write_spec(fdir, ids)
    _write_state(fdir, phase="F4", cycle=1)
    _write_verdicts(fdir, [
        {"requirement_id": r, "verdict": "VERIFIED"} for r in ids[:2]
    ])
    _defect_ledger(fdir, [_tiered("D-9", "LIVE")])
    _teams_active(True)

    _arm_ordering_token(fdir)
    gate = foundry_gate("done", project_root)

    assert gate["passed"] is False, gate
    # Four failing checks, four published sentences, in rank order.
    ranks = [r["rank"] for r in gate["refusals"]]
    assert len(ranks) == 4, gate["refusals"]
    assert ranks == sorted(ranks), gate["refusals"]
    assert all(r["hint"].strip() for r in gate["refusals"]), gate["refusals"]
    assert gate["reason"] == gate["refusals"][0]["reason"], gate
    assert gate["hint"] == gate["refusals"][0]["hint"], gate

    # The remedy that speaks is the team shutdown: it is refused by nothing on
    # this call, while re-running ASSAY is refused by D-9 and a generated
    # report is invalidated by every other check still failing.
    assert "Active teams" in gate["reason"], gate["reason"]
    assert "ASSAY must write ALL verdicts" not in gate["hint"], gate["hint"]

    # ...and nothing is discarded: each of the other three is readable.
    published = " ".join(f"{r['reason']} {r['hint']}" for r in gate["refusals"])
    for fragment in ("D-9", "5 requirements", "Foundry-Report"):
        assert fragment in published, (fragment, gate["refusals"])




def test_the_verdict_remedy_this_door_used_to_render_is_the_defeated_one(
    run_env,
):
    """D-191's proof that the old rendered remedy was unusable, driven rather
    than reasoned: on the SAME state, the call its hint sends the lead to make
    is itself refused, and refused for the defect the done door did not name.
    """
    project_root, fdir = run_env
    ids = ["FR-1", "FR-2", "FR-3", "FR-4", "FR-5"]
    _write_spec(fdir, ids)
    _write_state(fdir, phase="F4", cycle=1)
    _write_verdicts(fdir, [
        {"requirement_id": r, "verdict": "VERIFIED"} for r in ids[:2]
    ])
    _defect_ledger(fdir, [_tiered("D-9", "LIVE")])

    _arm_ordering_token(fdir)
    assay = foundry_gate("assay", project_root)

    assert assay["passed"] is False, assay
    assert any(
        r["rank"] == _GATE_RANK_DEFECTS and "D-9" in r["reason"]
        for r in assay["refusals"]
    ), assay["refusals"]




def test_the_halt_outranks_the_evidence_rung_it_used_to_follow(run_env):
    """ST-008 / CT-016: 'HALTED is not DONE', and it keeps the last word.

    The retired ladder asserted the halt TWICE — once in its own branch and
    again after the evidence rung — because source position was the only
    ordering the function had, and the rung below would otherwise have taken
    `reason`. `_GATE_RANK_HALTED` is the lowest rank there is, so one `fail`
    says the same thing for a stated reason. Driven on the state that made the
    second assertion necessary: halted AND carrying a log that no longer
    reproduces.
    """
    project_root, fdir = run_env
    _d190_state(project_root, fdir)
    _halted_run(fdir)


    outcome = _done_preconditions(fdir, project_root)

    assert outcome["passed"] is False
    assert "HALTED" in outcome["reason"], outcome["reason"]
    assert "casting-1-alpha.log" not in outcome["reason"], outcome["reason"]
    assert outcome["refusals"][0]["rank"] == _GATE_RANK_HALTED, outcome

    # fallout AC-062 (D-088) — THE RUNG IT OUTRANKS IS NO LONGER RUN AT ALL,
    # AND THAT IS A STRONGER STATEMENT OF THE SAME RULE.
    #
    # Ranking said "the halt defeats every other remedy"; short-circuiting says
    # it and then declines to compute them. The evidence rung is the reason that
    # matters: it re-executes the committed corpus in a detached worktree, so a
    # halted run was paying for a sweep to publish a remedy nobody can act on —
    # the run is over. Nothing is DISCARDED here (D-186's rule), because nothing
    # else was computed to discard; what the operator gets is the one refusal
    # that is true of the run's state, at rank 0.
    halts = [r for r in outcome["refusals"] if r["rank"] == _GATE_RANK_HALTED]
    assert len(halts) == 1, outcome["refusals"]
    assert len(outcome["refusals"]) == 1, outcome["refusals"]

    # ...and on the SAME arrangement without the halt, the evidence rung is
    # still computed and still published — so this is the halt short-circuiting
    # and not the rung having gone missing.
    _write_state(fdir, phase="F4", cycle=2)
    running = _done_preconditions(fdir, project_root)
    assert any(
        "casting-1-alpha.log" in r["reason"] for r in running["refusals"]
    ), running["refusals"]




def test_the_done_transition_regenerates_the_report_it_closes_the_run_with(run_env):
    """FR-001 verbatim: 'open LATENT instances go to the F6 named backlog.'
    AC-036 verbatim: 'Foundry-Report writes REPORT.md and report.json with ...
    the LATENT backlog'.

    D-218, driven end to end. The report was generated while defects.json was
    empty, so its `latent_backlog` read `{"open_count": 0, "defects": []}`; one
    open LATENT defect was then filed, and `Foundry-Phase('done')` returned ok
    True and phase F6 with its own checklist simultaneously reading
    `zero_blocking_defects (live=0 unknown_tier=0 latent_backlog=1)` and
    `report_generated (missing_sections=0)`. The run finished with the one
    artifact FR-001 puts the backlog in saying the backlog was empty.

    The assertion is on the DOCUMENT, not on the checklist: DONE's report is
    what the next run's lead receives, and `_blocking_defects`' own hint
    promises the operator these defects are "named in the F6 backlog".
    """
    project_root, fdir = run_env
    _write_spec(fdir, ["FR-1", "FR-2"])
    _write_state(fdir, phase="F4", cycle=2)
    _write_verdicts(fdir, [
        {"requirement_id": "FR-1", "verdict": "VERIFIED"},
        {"requirement_id": "FR-2", "verdict": "VERIFIED"},
    ])
    _defect_ledger(fdir, [])
    _generate_report(project_root, fdir)

    stale = json.loads((fdir / "report.json").read_text(encoding="utf-8"))
    assert stale["latent_backlog"]["open_count"] == 0, stale["latent_backlog"]

    # ...and then the LATENT defect the report was written before.
    _defect_ledger(fdir, [_latent("D-001")])

    _arm_ordering_token(fdir)
    result = foundry_mark_phase_complete("done", project_root)

    assert result["ok"] is True, result
    assert result["phase"] == "F6"
    assert result["report_generated"] is True, result

    sealed = json.loads((fdir / "report.json").read_text(encoding="utf-8"))
    assert sealed["latent_backlog"]["open_count"] == 1, sealed["latent_backlog"]
    assert [d["id"] for d in sealed["latent_backlog"]["defects"]] == ["D-001"]
    # Generated AFTER the phase write, so the document renders the FINISHED
    # run rather than the one that was about to finish.
    assert sealed["run"]["phase"] == "F6", sealed["run"]
    # NFR-005: the terminal says what it wrote, and names the backlog it named.
    assert "D-001" in result["message"], result["message"]




def test_the_nyquist_door_seals_the_report_on_the_same_terms(run_env):
    """AC-011 / D-043 / D-044 — THE ADJACENT PATH: F6's other door.

    `nyquist_done` is a second transition into F6, not a different kind of
    transition, and every precondition D-037 bound to `done` had to be bound to
    it separately because the two branches drifted the first time. The report
    seal is one more thing both doors must do, so it is driven here through the
    door the defect was NOT found on — a `--nyquist` run routes through this
    token and would otherwise close on the stale document the other door now
    refreshes.
    """
    project_root, fdir = run_env
    _write_spec(fdir, ["FR-1"])
    _write_state(fdir, phase="F5.5", cycle=3, temper=True, nyquist=True)
    _write_verdicts(fdir, [{"requirement_id": "FR-1", "verdict": "VERIFIED"}])
    _defect_ledger(fdir, [])
    _generate_report(project_root, fdir)
    assert json.loads(
        (fdir / "report.json").read_text(encoding="utf-8")
    )["latent_backlog"]["open_count"] == 0

    _defect_ledger(fdir, [_latent("D-007"), _latent("D-008")])

    _arm_ordering_token(fdir)
    result = foundry_mark_phase_complete("nyquist_done", project_root)

    assert result["ok"] is True, result
    assert result["phase"] == "F6"
    assert result["report_generated"] is True, result

    sealed = json.loads((fdir / "report.json").read_text(encoding="utf-8"))
    assert [d["id"] for d in sealed["latent_backlog"]["defects"]] == ["D-007", "D-008"]
    assert sealed["run"]["phase"] == "F6", sealed["run"]




def test_a_report_that_cannot_be_regenerated_at_f6_is_recorded_and_said(
    run_env, monkeypatch
):
    """CT-014 / GI-006 / NFR-005 — the designed failure branch, not a
    theoretical one.

    `generate_report` returns a named refusal rather than raising, and CT-014
    SPECIFIES that branch. At this point in the transition the readable-ledger
    half of it has already been refused upstream — `_artifact_guard` rejects the
    whole `Foundry-Phase` call on a corrupt run artifact — so what remains
    reachable here is the write itself failing, which is why the generator is
    driven to its documented refusal shape rather than through a corrupted
    ledger the guard would catch first.

    What is asserted is that the outcome is not DISCARDED. D-165 is the same
    branch one door along: `_halt_if_capped` threw its `report` away and
    announced a document that did not exist. DONE has no exit, so refusing
    after `_update_phase(fdir, "F6")` would leave the run unable to be
    anything; the transition still happens, the failure is RECORDED in
    state.json and SAID in the message, and the message names the one call that
    still writes the report. GI-006's refusal condition is ABSENCE, and that
    stays enforced in `_done_preconditions` before this runs at all.
    """
    project_root, fdir = run_env
    _write_spec(fdir, ["FR-1"])
    _write_state(fdir, phase="F4", cycle=1)
    _write_verdicts(fdir, [{"requirement_id": "FR-1", "verdict": "VERIFIED"}])
    _defect_ledger(fdir, [])
    _generate_report(project_root, fdir)

    patch_everywhere(monkeypatch, "_generate_report",
        lambda _pr, _fd: {
            "ok": False,
            "error": "could not write REPORT.md: Read-only file system",
            "hint": "Repair or delete the named file, then retry.",
        },
    )

    _arm_ordering_token(fdir)
    result = foundry_mark_phase_complete("done", project_root)

    assert result["ok"] is True, result
    assert result["phase"] == "F6"
    assert result["report_generated"] is False, result
    assert "Read-only file system" in result["report_error"], result
    assert "Foundry-Report" in result["message"], result["message"]

    state = json.loads((fdir / "state.json").read_text(encoding="utf-8"))
    assert state["phase"] == "F6"
    assert "Read-only file system" in state["done_report_error"], state




def test_the_cap_transition_carries_the_prose_the_f6_seal_carries(run_env):
    """THE ADJACENT PATH: the run's OTHER terminal transition.

    ST-008 / FR-045 — `_halt_if_capped` writes the report as part of the HALTED
    transition, and it carried its own copy of the unconditional overwrite. A
    rule enforced at one terminal transition and not the other is one a run
    walks around by ending the other way: this is the door a capped run leaves
    by, and it archives no differently from DONE.

    Driven through `grind_start`, which is not the transition `_seal_run_report`
    is reached from at all.
    """
    project_root, fdir = run_env
    _arrange_terminal_door(fdir, "grind_start")
    _generate_report(project_root, fdir)
    _append_lead_prose(fdir)

    _arm_ordering_token(fdir)
    result = foundry_mark_phase_complete("grind_start", project_root)

    assert result["ok"] is True, result
    assert result["halted"] is True
    assert result["report_generated"] is True, result
    assert result["lead_prose_lines"] > 0, result

    text = _assert_lead_prose_survived(fdir)
    # FR-045: 'the report is written naming every open LIVE and LATENT defect' —
    # still true, on the document the prose came back onto.
    assert "D-001" in text, text[-2000:]




def test_a_purely_generated_report_is_left_exactly_as_generated(run_env):
    """The seal must not INVENT prose either.

    A run whose lead appended nothing has to close with the document
    `generate_report` writes, byte for byte — a merge that re-appended stale
    generated rows on every seal would grow the report a section at a time and
    would be this defect with the sign flipped.
    """
    project_root, fdir = run_env
    _write_spec(fdir, ["FR-1"])
    _write_state(fdir, phase="F4", cycle=2)
    _write_verdicts(fdir, [{"requirement_id": "FR-1", "verdict": "VERIFIED"}])
    _defect_ledger(fdir, [_latent("D-100")])
    _generate_report(project_root, fdir)

    _arm_ordering_token(fdir)
    result = foundry_mark_phase_complete("done", project_root)

    assert result["ok"] is True, result
    assert result["lead_prose_lines"] == 0, result
    sealed = (fdir / "REPORT.md").read_text(encoding="utf-8")
    # Regenerated from the same ledgers, so the only line that may differ is the
    # banner's timestamp.
    #
    # COUNTED AS `markdown_sections` COUNTS THEM — a heading is a WHOLE TRIMMED
    # LINE beginning `"## "` — and not as a substring. A substring count reads the
    # generator's own banner prose, which quotes `## ` inline to tell the lead
    # where their additions may go, and a sentence about headings is not a
    # heading. The subject of this assertion is what the seal did to the
    # document, so it must not move when a sibling module rewords a paragraph.
    headings = [
        line for line in sealed.splitlines() if line.strip().startswith("## ")
    ]
    assert len(headings) == len(vocab.REPORT_REQUIRED_SECTIONS), headings




#: fallout AC-059 — the FOUR rows the spec enumerates as exceptions, quoted:
#: "inspect→cast, grind→grind_start and assay_fail, assay→inspect_clean,
#: validate→start_cast, others same-name including halt". Typed here because
#: the spec sentence is the authority and there is nothing in the package to
#: derive it from; the "others" clause below is walked over the shipped table,
#: so a row added later is judged without anyone editing this tuple.
_AC059_NAMED_EXCEPTIONS = {
    "inspect": ("cast",),
    "grind": ("grind_start", "assay_fail"),
    "assay": ("inspect_clean",),
    "validate": ("start_cast",),
}


def test_the_table_is_the_specs_enumeration_with_no_fifth_exception():
    """fallout AC-059 — every row outside the spec's four maps SAME-NAME.

    `cast` shipped mapping to `start_cast`, a fifth exception the enumeration
    does not name. It read as harmless — `validate` guards `start_cast` too, so
    the rung set was reachable — but it left the `cast` gate answering the
    question one phase early, and the transition `cast` gated by a single token
    while `start_cast` had two. The clause the table has to satisfy is
    positional, not incidental: four named rows, and same-name for the rest.
    """
    for gate, expected in _AC059_NAMED_EXCEPTIONS.items():
        assert GATE_TO_TRANSITION[gate] == expected, (gate, GATE_TO_TRANSITION[gate])
    off_enumeration = {
        gate: targets
        for gate, targets in GATE_TO_TRANSITION.items()
        if gate not in _AC059_NAMED_EXCEPTIONS and targets != (gate,)
    }
    assert off_enumeration == {}, off_enumeration


def test_the_cast_action_names_the_gate_that_guards_the_transition_it_calls():
    """fallout AC-059 — the guidance chain the table's fifth exception hid.

    `_ACTION_TO_GATE` is what `.gate-passed` is compared against, so the gate
    `transition_to_cast` names has to be the one evaluating `start_cast`, the
    transition the same action's imperative then tells the lead to call. While
    `cast` mapped to `start_cast` the chain closed by accident and the table's
    deviation was invisible from here; with the table corrected it closes only
    through `validate`, which is the row the spec assigns `start_cast` to.

    Driven off the shipped imperative rather than a second hand-typed table:
    the `Foundry-Phase(phase='<token>')` the lead is told to call is read out of
    the string, so rewording the step without moving the gate fails here.

    fallout D-058 — AND THE SCOPE NOTE THAT USED TO STAND HERE WAS THE NEXT
    DEFECT, WRITTEN DOWN.

    It read: "Scoped to this one action deliberately ... several of them span
    more than one crossing in one step — `transition_to_inspect` gates `inspect`
    and then calls `inspect_start`". That pair is not a wider chain; it is a
    gate that does not guard the transition beside it, and it was refused at
    both of the phases the action is emitted from. The general form of this
    assertion now lives in
    `test_every_transition_action_names_the_gate_that_guards_the_crossing_it_calls`
    below, over every (phase, action) pair the router can emit; this one stays
    as AC-059's own regression for the `validate`/`start_cast` row.
    """
    gate = _ACTION_TO_GATE["transition_to_cast"]
    assert gate in GATE_TO_TRANSITION, gate
    called = set(
        re.findall(
            r"Foundry-Phase\(phase='([a-z_]+)'\)",
            _ACTION_IMPERATIVES["transition_to_cast"],
        )
    )
    assert called == {"start_cast"}, called
    assert called <= set(GATE_TO_TRANSITION[gate]), {
        "gate": gate,
        "gate_guards": GATE_TO_TRANSITION[gate],
        "imperative_calls": sorted(called),
    }
    # ...and the step itself names that gate, so a lead following the text and a
    # lead following `_ACTION_TO_GATE` make the same call.
    assert (
        f"Foundry-Gate(phase='{gate}')" in _ACTION_IMPERATIVES["transition_to_cast"]
    ), _ACTION_IMPERATIVES["transition_to_cast"]


def _router_phase_action_pairs() -> set[tuple[str | None, str]]:
    """`(phase, action)` for every response literal the router can return.

    Derived from `_compute_next_action`'s and `_nyquist_transition`'s own AST,
    in the shape `test_every_action_the_router_emits_has_an_imperative` already
    uses over the same two functions — a hand list would be a second copy of the
    branch chain, free to drift from it in exactly the direction that hides a
    hole.

    A dict whose `phase` is not a literal (``_nyquist_transition`` passes its
    ``from_phase`` argument through) yields `(None, action)`, which is a REAL
    answer the assertion below acts on: an action reachable from a phase this
    scan cannot name must not have a phase-keyed gate, because nothing could
    resolve it.
    """
    pairs: set[tuple[str | None, str]] = set()
    for name in ("_compute_next_action", "_nyquist_transition"):
        fn = getattr(_guidance, name)
        tree = ast.parse(textwrap.dedent(inspect.getsource(fn)))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Dict):
                continue
            fields: dict[str, ast.expr] = {
                key.value: value
                for key, value in zip(node.keys, node.values)
                if isinstance(key, ast.Constant) and isinstance(key.value, str)
            }
            action_node = fields.get("action")
            if not (
                isinstance(action_node, ast.Constant)
                and isinstance(action_node.value, str)
            ):
                continue
            phase_node = fields.get("phase")
            phase = (
                phase_node.value
                if isinstance(phase_node, ast.Constant)
                and isinstance(phase_node.value, str)
                else None
            )
            pairs.add((phase, action_node.value))
    return pairs




def test_every_transition_action_names_the_gate_that_guards_the_crossing_it_calls():
    """fallout D-058 / AC-059 / GI-001 — the general form, over every emission site.

    GI-001's violation column is "a casting that ... lets a transition skip its
    gate", and naming the WRONG gate is that violation with a step in it: the
    lead makes a call, is refused, and the refusal's hint is the only place the
    right token appears.

    `transition_to_inspect` is the only action the router emits from two phases
    and the two are different crossings — F1 gates `inspect` and calls
    `Foundry-Phase(phase='cast')`; F3 gates `inspect_start` and calls
    `Foundry-Phase(phase='inspect_start')`. One entry held `inspect` for both,
    so from F3 the gate was refused ("accepted from F1 and from nowhere else")
    and from F1 the phase call was ("accepted from F3, and from F2"). Nothing
    pinned either string.

    THREE THINGS ARE ASSERTED PER EMISSION SITE, and the chain only closes if
    all three hold: the gate `_expected_gate_for_action` resolves is a real
    door; the imperative the lead reads names THAT gate; and every
    `Foundry-Phase(phase='X')` the same imperative tells them to call is a
    transition that gate guards. Read off the shipped strings rather than a
    second hand-typed table, so rewording a step without moving its gate fails
    here.
    """
    pairs = _router_phase_action_pairs()
    assert len(pairs) >= 20, sorted(pairs)

    gated = sorted(
        ((phase, action) for phase, action in pairs if action in _ACTION_TO_GATE),
        key=lambda pair: (pair[1], pair[0] or ""),
    )
    assert len(gated) >= len(_ACTION_TO_GATE), {
        "emission_sites_found": gated,
        "actions_in_the_map": sorted(_ACTION_TO_GATE),
    }
    # The action this defect is about is reachable from BOTH phases, and a scan
    # that found only one of them would prove nothing about the other.
    assert {"F1", "F3"} <= {
        phase for phase, action in gated if action == "transition_to_inspect"
    }, gated

    problems: dict[str, dict] = {}
    for phase, action in gated:
        if phase is None:
            # Unresolvable phase: the entry must not be keyed by one.
            if isinstance(_ACTION_TO_GATE[action], dict):
                problems[action] = {
                    "reason": "phase-keyed gate for an action emitted with a "
                              "non-literal phase; nothing can resolve it",
                }
            continue
        gate = _expected_gate_for_action(action, phase)
        site = f"{action}@{phase}"
        if gate is None:
            problems[site] = {"reason": "no gate resolves for this emission site"}
            continue
        if gate not in GATE_TO_TRANSITION:
            problems[site] = {"reason": "gate is not a door", "gate": gate}
            continue
        imperative = _guidance._format_imperative_header(
            action, "", {}, phase=phase
        )
        if f"Foundry-Gate(phase='{gate}')" not in imperative:
            problems[site] = {
                "reason": "the imperative does not name the gate the map does",
                "gate": gate,
                "imperative": imperative,
            }
            continue
        called = set(re.findall(r"Foundry-Phase\(phase='([a-z_]+)'\)", imperative))
        stray = sorted(called - set(GATE_TO_TRANSITION[gate]))
        if stray:
            problems[site] = {
                "reason": "the imperative calls a transition this gate does not guard",
                "gate": gate,
                "gate_guards": list(GATE_TO_TRANSITION[gate]),
                "imperative_calls": sorted(called),
                "unguarded": stray,
            }
    assert problems == {}, problems




def test_every_gate_token_maps_to_a_transition_and_every_transition_has_a_gate():
    """fallout AC-059 / ST-014 / CT-020 — `GATE_TO_TRANSITION` is the whole map.

    Both directions. A gate token missing from the table is a door with no
    evaluation; a `PHASE_TOKENS` member that is no gate's target is a transition
    a lead cannot ask about before calling it, which is the state `inspect_start`
    and `halt` were in before this release.
    """
    assert GATE_TO_TRANSITION, "the mapping is empty; every assertion below is vacuous"
    targets = {t for tokens in GATE_TO_TRANSITION.values() for t in tokens}
    assert targets == set(PHASE_TOKENS), {
        "mapped_but_not_a_token": sorted(targets - set(PHASE_TOKENS)),
        "token_with_no_gate": sorted(set(PHASE_TOKENS) - targets),
    }
    # The transport advertises exactly this set, DERIVED from it in server.py
    # rather than re-typed beside it. Asserted on the source, because the enum
    # is what the SDK validates against BEFORE dispatch: a token missing there
    # is unreachable over MCP however the handler behaves, which is what made
    # `inspect_start` uncallable for an entire release.
    server_src = Path(
        foundry_mcp.__file__
    ).resolve().parent.joinpath("server.py").read_text(encoding="utf-8")
    assert "sorted(GATE_TO_TRANSITION)" in server_src, (
        "server.py's Foundry-Gate enum no longer derives from the table"
    )

    # ...and the guidance engine's own action->gate map names only doors that
    # exist. `_ACTION_TO_GATE` is what `.gate-passed` is compared against, so a
    # value missing from the table is a lead sent to a call the server refuses.
    #
    # fallout D-058 — FLATTENED, because a value is one gate or one gate PER
    # PHASE. `set(_ACTION_TO_GATE.values())` raised on the keyed entry, and the
    # membership question is about the gates a value can RESOLVE to rather than
    # about the container holding them.
    named = sorted({
        gate
        for entry in _ACTION_TO_GATE.values()
        for gate in _gate_tokens_named(entry)
    })
    assert named, "the action->gate map names no gate at all"
    unknown = sorted(set(named) - set(GATE_TO_TRANSITION))
    assert unknown == [], unknown




@pytest.mark.parametrize("token", sorted(PHASE_TOKENS))
def test_a_gate_and_its_transition_agree_on_the_passing_case(run_env, token):
    """The other half: what one door admits, the other admits.

    A pin that only walks refusals is satisfied by a gate that refuses
    everything. Both doors are driven on the arrangement `_arrange_passing`
    builds, and the gate must PASS — the transition is not driven to success
    here because several tokens have irreversible effects (a detached worktree,
    an archived run); `test_a_gated_transition_still_crosses_on_a_clean_ledger`
    below drives the ones that are cheap to complete.
    """
    project_root, fdir = run_env
    reason_kw = {"reason": "lead_ruling", "text": "stopped by hand"} if token == "halt" else {}
    _arrange_passing(project_root, fdir, token)
    _arm_ordering_token(fdir)
    gate = foundry_gate(_gate_for(token), project_root, **reason_kw)
    assert gate["passed"] is True, (token, gate)




# --------------------------------------------------------------------------- #
# fallout FR-062 / GI-032 / ST-015 / AC-060 / OT-044 — the cap is a FACT.
# --------------------------------------------------------------------------- #


def test_the_grind_gate_passes_at_the_cap_and_shows_would_halt(run_env):
    """`Foundry-Gate('grind')` PASSES at the cap and reports `would_halt: true`.

    A gate that REFUSED here would be a gate with no remedy: reaching the cap is
    not something a lead clears at the door. The run stops — successfully, with
    its open work written down — and the gate's job is to say so before the
    lead spends a wave finding out.
    """
    project_root, fdir = run_env
    _arrange_passing(project_root, fdir, "grind_start")
    _write_state(fdir, phase="F2", cycle=2, max_cycles=2)
    _record_full_inspect_mode(fdir, cycle=2)
    _arm_ordering_token(fdir)

    gate = foundry_gate("grind", project_root)
    assert gate["passed"] is True, gate
    assert gate["would_halt"] is True, gate
    cap = next(c for c in gate["checklist"] if c["check"].startswith("within_cycle_cap"))
    assert cap["would_halt"] is True and cap["ok"] is True, cap




# --------------------------------------------------------------------------- #
# fallout FR-018 / FR-064 / GI-034 / CT-004 / CT-005 / CT-021 / ST-001 /
# AC-025 / AC-026 / AC-029 / AC-062 / OT-023 / OT-024 / OT-045 — the halt door.
# --------------------------------------------------------------------------- #


def test_the_halt_gate_reports_the_three_checks_as_data(run_env):
    """fallout CT-021 / AC-062 / OT-045 — reported, not acted on."""
    project_root, fdir = run_env
    _arrange_passing(project_root, fdir, "halt")
    _arm_ordering_token(fdir)
    gate = foundry_gate("halt", project_root, reason="lead_ruling", text="enough")
    assert gate["passed"] is True, gate
    names = [c["check"] for c in gate["checklist"]]
    assert any(n.startswith("halt_reason_is_a_member") for n in names), names
    assert "no_active_teams" in names, names
    assert "not_already_halted" in names, names
    # Reported, not acted on: the run is exactly where it was.
    assert json.loads((fdir / "state.json").read_text(encoding="utf-8"))["phase"] == "F3"




# --------------------------------------------------------------------------- #
# fallout FR-021 / FR-034 / FR-035 / FR-047 / FR-049 / FR-055 / GI-014 /
# GI-016 / CT-007 / AC-021 / AC-028 / AC-053 / AC-054 / OT-026 — the lead-facing
# surface.
# --------------------------------------------------------------------------- #


def test_the_tier_buckets_are_derived_from_the_vocabulary(run_env):
    """fallout GI-014 / AC-011 — a HARDENING record does not take the ledger down.

    `_open_defects_by_tier` seeded three hand-typed keys and `defect_tier` is
    TOTAL over `DEFECT_TIERS`, so the moment `HARDENING` joined that frozenset
    this raised `KeyError: 'HARDENING'` on any ledger carrying one — and
    `_blocking_defects` calls it, and every gate and every transition calls
    that. Driven here on a ledger with one record of every tier, because the
    tier exists precisely so a stream will file into it.
    """
    project_root, fdir = run_env
    _defect_ledger(fdir, [
        _tiered("D-1", "LIVE"),
        _tiered("D-2", "LATENT", reproduction_attempted="drove every caller; none reach it"),
        _tiered("D-3", "HARDENING", reproduction_attempted="drove the probe; it failed"),
        _tiered("D-4", None),
    ])

    buckets = _open_defects_by_tier(fdir)
    assert set(buckets) == set(vocab.DEFECT_TIERS) | {vocab.TIER_UNKNOWN}, buckets
    assert [d["id"] for d in buckets["HARDENING"]] == ["D-3"], buckets

    # ...and HARDENING does not block, which is the whole reason it is a tier
    # rather than a flag on LIVE.
    blocking = _blocking_defects(fdir)
    assert blocking["blocking"] == 2, blocking
    assert sorted(blocking["live"]) == ["D-1"], blocking
    assert sorted(blocking["unknown"]) == ["D-4"], blocking


# --------------------------------------------------------------------------- #
# fallout CT-020 (D-084 / D-085) — WHAT THE DOOR PUBLISHES IS THE DOOR'S, AND
# EACH CHECK SPEAKS ONCE.
# --------------------------------------------------------------------------- #


def test_a_precondition_fact_never_shadows_the_gate_token_it_was_asked_about(run_env):
    """fallout CT-020 (D-084) — `Foundry-Gate('temper')` answered `phase: F0`.

    CT-020's output column is "the mapped transition's preconditions checklist,
    including non-refusing facts such as would_halt", and the facts are SPREAD
    into the result. `_source_phase_rung` published the run's CURRENT phase
    under `phase`, the spread landed after the literal, and the door's own
    identity field became a precondition's answer. Downstream harm is real:
    `display.py#_fmt_foundry_gate` does `PHASE_NAMES.get(phase.upper(), phase)`,
    so a lead who ran the temper gate from F0 was shown "Gate RESEARCH: not
    ready". A REGRESSION — the pre-wave-1 gate spread nothing.
    """
    project_root, fdir = run_env
    # F0 is a wrong source for all three of these, so the source rung fires and
    # its facts are published — which is the only state the collision existed in.
    _write_state(fdir, phase="F0", cycle=0)
    (fdir / artifacts.NEXT_ACTION_CALLED_MARKER).write_text("x", encoding="utf-8")

    for token in ("temper", "cast", "inspect"):
        result = foundry_gate(token, project_root)
        assert result["phase"] == token, (token, result["phase"])
        # ...and the fact is not lost to the fix: it is published under a name
        # that says what it is.
        assert result.get("source_phase") == "F0", (token, result)
        assert result.get("accepted_from"), (token, result)


def test_the_two_f6_doors_publish_the_source_rung_they_compute(run_env):
    """fallout CT-020 (D-084) — the mirror bug, on the same channel.

    `_done_preconditions` assigned `source = _source_phase_rung(...)` and never
    read it, so `done` and `nyquist_done` published no `accepted_from` and no
    source phase at all — while every other token's routine spreads them through
    `_preconditions_outcome`. `_transition_refusal`'s docstring promises a
    caller reading those keys keeps them; at these two doors it did not.
    """
    project_root, fdir = run_env
    _write_state(fdir, phase="F2", cycle=1)
    (fdir / artifacts.NEXT_ACTION_CALLED_MARKER).write_text("x", encoding="utf-8")

    for token in ("done", "nyquist_done"):
        result = foundry_gate(token, project_root)
        assert result["phase"] == token, result
        assert result["passed"] is False, result
        assert result.get("source_phase") == "F2", (token, result)
        assert result.get("accepted_from"), (token, result)


def test_the_gate_identity_keys_are_not_spreadable_over(run_env):
    """fallout CT-020 (D-084) — the GENERATOR, not the one colliding name.

    Renaming `phase` to `source_phase` fixes the instance; a fact named `passed`
    or `checklist` would do the same damage tomorrow. The result literal is
    built with the spread FIRST, so the door's three answers are the door's
    whatever a routine computes. Driven by planting each name as a fact.
    """
    from foundry_mcp.tools.orchestration import transitions as _transitions

    project_root, fdir = run_env
    _write_state(fdir, phase="F2", cycle=1)
    (fdir / artifacts.NEXT_ACTION_CALLED_MARKER).write_text("x", encoding="utf-8")

    real = _transitions._token_preconditions

    def _poisoned(token, fdir_, project_root_, *, reason="", text=""):
        outcome = real(token, fdir_, project_root_, reason=reason, text=text)
        if outcome is not None:
            outcome["phase"] = "F0"
            outcome["passed"] = "not a bool"
            outcome["checklist"] = "not a list"
        return outcome

    _transitions._token_preconditions = _poisoned
    try:
        result = foundry_gate("done", project_root)
    finally:
        _transitions._token_preconditions = real

    assert result["phase"] == "done", result["phase"]
    assert isinstance(result["passed"], bool), result["passed"]
    assert isinstance(result["checklist"], list), result["checklist"]


def test_a_two_token_gate_publishes_each_failing_check_once(run_env):
    """fallout CT-020 (D-085) — one ladder, absorbed twice.

    `grind` is the one gate token mapping to TWO transitions, and
    `_assay_fail_preconditions` IS `_grind_start_preconditions` — the same
    function, returning the same ladder — so `foundry_gate`'s per-token loop
    absorbed it twice and published every refusal twice. The checklist was
    deduped one line down; the refusals were not.

    The invariant test could not see it: it compares `{r['reason'] for r in
    ...}`, a SET, which is precisely what a duplicate survives. So this asserts
    the LIST.
    """
    from foundry_mcp.tools.orchestration.gates import GATE_TO_TRANSITION

    project_root, fdir = run_env
    _write_state(fdir, phase="F3", cycle=1)
    (fdir / artifacts.NEXT_ACTION_CALLED_MARKER).write_text("x", encoding="utf-8")
    # The arrangement: `grind` maps to a pair, and a registered team makes one
    # rung fire for both members of it.
    assert len(GATE_TO_TRANSITION["grind"]) == 2, GATE_TO_TRANSITION["grind"]
    (Path.home() / ".claude" / "teams").mkdir(parents=True, exist_ok=True)

    result = foundry_gate("grind", project_root)
    refusals = result.get("refusals") or []
    # Every published refusal is distinct...
    assert len(refusals) == len({
        (r["rank"], r["reason"], r["hint"]) for r in refusals
    }), refusals
    # ...and the checklist, which was already deduped, still is.
    checklist = result["checklist"]
    assert len(checklist) == len([
        c for i, c in enumerate(checklist) if c not in checklist[:i]
    ]), checklist


def test_the_dedupe_keeps_a_genuinely_second_distinct_refusal(run_env):
    """fallout D-085 — deduping identical entries is not dropping information.

    A rung that two mapped tokens fail DIFFERENTLY still publishes both, because
    the two entries are not equal. Only the byte-identical repeat of one
    evaluation goes.
    """
    from foundry_mcp.tools.orchestration import transitions as _transitions

    project_root, fdir = run_env
    _write_state(fdir, phase="F3", cycle=1)
    (fdir / artifacts.NEXT_ACTION_CALLED_MARKER).write_text("x", encoding="utf-8")

    real = _transitions._token_preconditions

    def _distinct(token, fdir_, project_root_, *, reason="", text=""):
        outcome = real(token, fdir_, project_root_, reason=reason, text=text)
        if outcome is not None:
            outcome["refusals"] = [
                {"rank": 40, "reason": f"a reason only {token} gives",
                 "hint": "a hint"}
            ]
            outcome["passed"] = False
        return outcome

    _transitions._token_preconditions = _distinct
    try:
        result = foundry_gate("grind", project_root)
    finally:
        _transitions._token_preconditions = real

    reasons = [r["reason"] for r in result["refusals"]]
    assert len(reasons) == 2, reasons
    assert len(set(reasons)) == 2, reasons
