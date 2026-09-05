"""Foundry-Phase: the per-token invariant, and the doors every branch shares.

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
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from foundry_mcp.schemas import vocab
from foundry_mcp.schemas.vocab import (  # noqa: F401
    RUN_PHASE_HALTED,
    STREAM_WIRE_IDS,
)
from foundry_mcp.tools import artifacts, foundry_state
from foundry_mcp.tools.foundry_state import (  # noqa: F401
    current_cycle,
    now_iso,
)
from foundry_mcp.tools.display import format_result

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
    _arm_ordering_token,
    _arrange_passing,
    _at_the_phase_for,
    _d190_state,
    _defect_ledger,
    _gate_for,
    _gate_rank_names,
    _generate_report,
    _manifest_with_requirement_ids,
    _old_marker_body,
    _preconditions_name,
    _ranks_a_routine_can_emit,
    _ready_for_the_end_gates,
    _refusal_readers,
    _record_full_inspect_mode,
    _teams_active,
    _tiered,
    _write_manifest_with_castings,
    _write_spec,
    _write_state,
    _write_verdicts,
    run_env,
)

from foundry_mcp.tools.orchestration.directives import (  # noqa: F401
    foundry_defects_to_tasks,
)

from foundry_mcp.tools.orchestration.gates import (  # noqa: F401
    _done_preconditions,
    _generate_report,
    foundry_gate,
)

from foundry_mcp.tools.orchestration.guidance import (  # noqa: F401
    _compute_next_action,
    foundry_next_action,
)

from foundry_mcp.tools.orchestration.halt import (  # noqa: F401
    _halted_refusal,
)

from foundry_mcp.tools.orchestration.streams import (  # noqa: F401
    VALID_STREAMS,
)

from foundry_mcp.tools.orchestration.transitions import (  # noqa: F401
    PHASE_TOKENS,
    _phase_transition,
    _token_preconditions,
    _update_phase,
    foundry_mark_phase_complete,
)

from tests.orchestration._env import (  # noqa: F401
    _ABSENCE_ASSERTION,
    _BAD_UTF8_DOCUMENT,
    _BAD_UTF8_SPEC,
    _CORRUPTIBLE_ARTIFACTS,
    _D137_BAD_MANIFEST,
    _D137_GOOD_MANIFEST,
    _DECODE_RULE_AXES,
    _FORGE_DOORS,
    _LEAD_HEADING,
    _LEAD_SENTINEL,
    _MALFORMED_BODIES,
    _ONE_FINDING,
    _OPERATOR_STATE,
    _PLANTED_ALIASED_LEDGER_SCAN,
    _PLANTED_ALIASED_LOADER,
    _PLANTED_ALIASED_RENAME,
    _PLANTED_COVERED_LOADER,
    _PLANTED_COVERED_SPLIT_LOADER,
    _PLANTED_DECODE_ONLY_LOADER,
    _PLANTED_FROM_IMPORT_LOADER,
    _PLANTED_LEDGER_SCAN,
    _PLANTED_LOADER,
    _PLANTED_OSERROR_DECODE_LOADER,
    _PLANTED_RERAISING_LOADER,
    _PLANTED_SPLIT_LOADER,
    _PLANTED_WRITER,
    _REGISTRY_BEGIN,
    _REGISTRY_END,
    _RETIRED_MECHANISMS,
    _RETIREMENT_MARKERS,
    _RUNG_ARRANGEMENTS,
    _RUN_DOCUMENT_POSITIONS,
    _RUN_MARKER_POSITIONS,
    _SECURITY_CLAIM,
    _STALE_PROSE_SURFACES,
    _TERMINAL_DOORS,
    _TERMINAL_EVIDENCE_CROSSINGS,
    _UNUSABLE_MANIFEST_RECORDS,
    _append_lead_prose,
    _arrange_terminal_door,
    _assert_lead_prose_survived,
    _clean_f2,
    _corrupt,
    _escalated_fixture,
    _init_schema,
    _latent,
    _plain,
    _traceability_fixture,
    _tripwire_classes,
)

from tests.orchestration.test_module_boundaries import (  # noqa: F401
    _corrupt_external_input_door_drive,
    _drive_mcp,
    _external_spec_run,
    _load_cli_module,
    _markdown_units,
    _plant,
    _plugin_root,
    _python_units,
    _raw_ledger_iterations,
    _setup_worktree_root_dirnames,
    _shipped_cli,
    _tool_schema,
    _unguarded_document_loads,
    _unlocked_artifact_renames,
    render_derived_guard_table,
    render_external_input_guard_table,
    render_load_spelling_table,
    render_shipped_tree_decode_table,
)




def test_phase_advance_clears_gate_passed_marker(run_env):
    """AC ST-002: a real phase advance (_update_phase) clears the gate-passed
    marker so guidance does not get stuck on a stale 'already passed' note."""
    project_root, fdir = run_env
    _write_state(fdir, phase="F4")
    (fdir / ".gate-passed").write_text(
        json.dumps({"phase": "done", "at": now_iso()}), encoding="utf-8"
    )

    _update_phase(fdir, "F6")

    assert not (fdir / ".gate-passed").exists()




def test_grind_start_clears_every_stream_marker(run_env):
    """Honest completion state across GRIND cycles: grind_start clears every
    recordable stream's marker — including all three new names — so no stale
    'complete' survives into the next INSPECT. Required set is untouched."""
    project_root, fdir = run_env
    _write_state(fdir, phase="F3")
    for stream in sorted(VALID_STREAMS):
        (fdir / f".{stream}-complete").write_text(
            _old_marker_body(), encoding="utf-8"
        )
    # fallout AC-056: `grind_start` shares the `grind` GATE's evaluation now, so
    # the fixture owes what that gate has always required — something to grind
    # and a Foundry-Tasks that packeted it. The token used to have no
    # preconditions at all, which is the divergence this release closes.
    _defect_ledger(fdir, [_tiered("D-500", "LIVE")])
    (fdir / ".tasks-generated").write_text("x\n", encoding="utf-8")

    _arm_ordering_token(fdir)
    result = foundry_mark_phase_complete("grind_start", project_root)

    assert result.get("ok") is True, result
    for stream in VALID_STREAMS:
        assert not (fdir / f".{stream}-complete").exists(), stream




def test_no_advertised_phase_token_is_refused_by_the_handler(run_env):
    """The other direction, driven rather than compared: every advertised token
    resolves to a branch instead of the else-error."""
    from foundry_mcp import server as foundry_server

    project_root, fdir = run_env

    tools = asyncio.run(foundry_server.list_tools())
    phase_tool = next(t for t in tools if t.name == "Foundry-Phase")

    for token in phase_tool.inputSchema["properties"]["phase"]["enum"]:
        _write_state(fdir, phase="F2", cycle=0)
        _arm_ordering_token(fdir)
        foundry_state.set_active_run("c3-test-run")
        result = foundry_mark_phase_complete(token, project_root)
        assert "Invalid phase" not in str(result.get("error", "")), (token, result)




def test_a_blind_state_cycle_recogniser_fails_the_rule_by_name(monkeypatch):
    """D-142's regression: the anchor must FAIL when the derivation sees nothing.

    The probe that filed D-142 replaced each per-module scan with one that
    returns nothing and watched every rule stay green. This drives that probe
    as a test: taint the recogniser so it can no longer see the package's
    `state.json` spelling, and the rule above must fail on its `seen` anchor
    rather than passing for the wrong reason.
    """
    import tests.orchestration.test_module_boundaries as gates

    monkeypatch.setattr(gates, "_mentions_state_json", lambda node: False)
    with pytest.raises(AssertionError, match="derivation has gone blind"):
        gates.test_every_state_cycle_read_goes_through_a_guarded_reader()




@pytest.mark.parametrize("artifact", _CORRUPTIBLE_ARTIFACTS)
@pytest.mark.parametrize("body", _MALFORMED_BODIES)
def test_a_malformed_artifact_does_not_pass_a_gate(run_env, artifact, body):
    """Refusing loudly is only half of it — an unreadable run must not be
    allowed to ADVANCE. A guard that named the file but let the transition
    through would be worse than the traceback."""
    project_root, fdir = run_env
    _corrupt(fdir, artifact, body)

    assert foundry_gate("done", project_root=project_root)["passed"] is False
    assert "ok" not in foundry_mark_phase_complete("inspect_start", project_root)




@pytest.mark.parametrize("body", _MALFORMED_BODIES)
def test_load_json_is_total(run_env, body):
    """The tolerance that binds every reader with no per-site edit: no shape
    reaches a caller as an exception."""
    _project_root, fdir = run_env
    path = fdir / "anything.json"
    path.write_text(body, encoding="utf-8")

    assert artifacts._load_json(path) == {}




def test_load_json_tolerates_non_utf8_bytes(run_env):
    _project_root, fdir = run_env
    path = fdir / "anything.json"
    path.write_bytes(b'{"phase": "\xff\xfe"}')

    assert artifacts._load_json(path) == {}




@pytest.mark.parametrize(
    "tool, arguments, field, bad",
    [
        (
            "Foundry-Defect",
            {"cycle": 1, "source": "trace", "defect_type": "UNWIRED",
             "description": "x", "defect_class": "K", "tier": "MAJOR"},
            "tier", "MAJOR",
        ),
        (
            "Foundry-Defect",
            {"cycle": 1, "source": "nobody", "defect_type": "UNWIRED",
             "description": "x", "defect_class": "K", "tier": "LIVE"},
            "source", "nobody",
        ),
        (
            "Foundry-Fix",
            {"defect_id": "D-1", "cycle": 1, "authored_by": "robot"},
            "authored_by", "robot",
        ),
        (
            "Foundry-Observation",
            {"cycle": 1, "source": "trace", "classification": "NOT_A_CLASS",
             "description": "x"},
            "classification", "NOT_A_CLASS",
        ),
        (
            "Foundry-Phase",
            {"phase": "teleport"},
            "phase", "teleport",
        ),
    ],
)
def test_an_out_of_vocabulary_enum_is_refused_over_mcp_naming_the_field(
    tool, arguments, field, bad
):
    """CT-001's errors column: 'refusal naming the missing tier.' AC-006: a tier
    outside {LIVE, LATENT} is 'refused naming the field'.

    D-042 — THE SDK ANSWERED FIRST, AND ITS ANSWER NAMES NOTHING. Driven over
    the real MCP transport at the time of filing, `Foundry-Defect(tier='MAJOR')`
    returned the entire response text

        Input validation error: 'MAJOR' is not one of ['LATENT', 'LIVE']

    — byte-identical in shape to the same validator's output for `source`,
    `defect_type`, `target_kind` or `authored_by`, because the offending
    PROPERTY appears nowhere in it. The handler's own field-naming refusal never
    ran: validation happens before dispatch.

    PARAMETRISED OVER FOUR TOOLS AND FOUR ENUMS, not over `tier` alone. The
    filing is one property short in one tool; the CLASS is every enum-valued
    argument of every tool, and a fix that named only `tier` would leave the
    other three answering exactly as before.
    """
    rendered = _drive_mcp(tool, arguments)

    assert field in rendered, rendered
    assert bad in rendered, rendered
    assert "Input validation error" not in rendered, rendered




def test_an_absent_required_argument_is_refused_over_mcp_naming_every_one():
    """The house rule the boundary now obeys: 'where several fields failed at
    once, name every one of them in a single refusal'.

    A caller that omits five required properties should learn all five, not
    discover them one round trip at a time — which is what the SDK's
    single-message refusal produced.
    """
    rendered = _drive_mcp("Foundry-Defect", {"cycle": 1})

    for field in ("source", "defect_type", "description", "tier", "defect_class"):
        assert field in rendered, rendered




def test_a_valid_call_still_reaches_its_handler_over_mcp():
    """The check must not become a second gate. Every constraint the SDK
    enforced is still enforced — `required`, `type` and every `enum`, against
    the same advertised schema with the same draft semantics — so a call that
    was legal before is legal now and reaches the handler that owns it."""
    rendered = _drive_mcp("Foundry-Defect", {
        "cycle": 1, "source": "trace", "defect_type": "UNWIRED",
        "description": "x", "defect_class": "K", "tier": "LIVE",
    })

    # No active run in this process, so the HANDLER's own refusal is what comes
    # back — which is the proof that dispatch happened.
    assert "unusable argument(s)" not in rendered, rendered
    assert "No active foundry run" in rendered or "foundry" in rendered.lower()




def _forge_door(name: str, project_root: str):
    from foundry_mcp.tools import forge_spec as fsp

    return {
        "check": lambda r: fsp.forge_spec_check("auth rework", "codebase", r),
        "start": lambda r: fsp.forge_spec_start("auth rework", r),
        "status": lambda r: fsp.forge_spec_status("auth rework", r),
    }[name](project_root)




def _seed_planning_state(tmp_path: Path, body, name: str) -> Path:
    root = tmp_path / name
    proj = root / "foundry-planning" / "auth-rework"
    proj.mkdir(parents=True)
    state = proj / "state.json"
    if isinstance(body, dict):
        state.write_text(json.dumps(body), encoding="utf-8")
    else:
        body(state)
    return root




@pytest.mark.parametrize("door", _FORGE_DOORS)
def test_a_wrong_typed_inner_collection_is_refused_not_repaired(tmp_path, door):
    """D-140 — the repair destroyed data and reported success.

    Forge-Spec-Check returned a clean success dict with NO "error" key and
    rewrote state.json so "phases" became four fresh {"status": "pending"}
    defaults — the record of which planning phases completed GONE — while
    "foundry_ready": true survived beside them, an internally inconsistent
    state Forge-Spec-Status then reported as normal.
    """
    root = _seed_planning_state(tmp_path, _OPERATOR_STATE, f"wrong-{door}")
    state = root / "foundry-planning" / "auth-rework" / "state.json"
    before = state.read_bytes()

    result = _forge_door(door, str(root))

    assert "error" in result, result
    assert "phases" in result["error"], result["error"]
    assert result.get("corrupt_artifacts"), result
    # Refuses AND destroys nothing: the operator's record is byte-identical.
    assert state.read_bytes() == before




def test_all_three_forge_doors_refuse_a_wrong_typed_state_identically(tmp_path):
    """One file, one story. D-140's other half was that the doors DISAGREED:
    Check raised IsADirectoryError while Start and Status returned ok over a
    fabricated all-default state."""
    seen = set()
    for door in _FORGE_DOORS:
        root = _seed_planning_state(tmp_path, _OPERATOR_STATE, f"same-{door}")
        result = _forge_door(door, str(root))
        seen.add((result["error"], result["hint"]))
    assert len(seen) == 1, seen




@pytest.mark.parametrize("door", _FORGE_DOORS)
def test_a_state_json_directory_is_refused_at_every_door(tmp_path, door):
    """D-140's third residual. A DIRECTORY named state.json passed
    `_planning_guard`'s `if c.is_file()` filter, so Check raised
    IsADirectoryError while Start and Status returned ok with a fabricated
    all-default state."""
    root = _seed_planning_state(tmp_path, lambda p: p.mkdir(), f"dir-{door}")

    result = _forge_door(door, str(root))

    assert "error" in result, result
    assert "state.json" in result["error"], result["error"]




def render_forge_state_table(tmp_path: Path) -> str:
    """D-140 pre/post: what each door does with an operator's real state.

    Rows are the corruption shapes, columns the three doors, so a door that
    starts disagreeing with its peers shows up as a row that is not uniform.
    """
    shapes = (
        ("phases: a bare string (shape 8)", dict(_OPERATOR_STATE)),
        ("phases[S0]: a bare string (9)",
         {**_OPERATOR_STATE, "phases": {"S0_understand": "complete"}}),
        ("state.json is a DIRECTORY", (lambda p: p.mkdir())),
        ("partial state (absent keys)", {"phase": "S1"}),
    )
    out = [
        "== an operator's state.json at each door: refuse, or repair in silence? ==",
        "   %-32s %-22s %-22s %s" % ("state.json holds", "Check", "Start", "Status"),
        "   %-32s %-22s %-22s %s" % ("-" * 16, "-" * 5, "-" * 5, "-" * 6),
    ]
    for label, body in shapes:
        cells = []
        for door in _FORGE_DOORS:
            root = _seed_planning_state(tmp_path, body, f"tbl-{door}-{len(out)}")
            state = root / "foundry-planning" / "auth-rework" / "state.json"
            before = state.read_bytes() if state.is_file() else None
            result = _forge_door(door, str(root))
            if "error" in result:
                intact = before is None or state.read_bytes() == before
                cells.append("REFUSES" + (", intact" if intact else ", DESTROYED"))
            else:
                changed = before is not None and state.read_bytes() != before
                cells.append("ok" + (", REWROTE FILE" if changed else ""))
        out.append("   %-32s %-22s %-22s %s" % (label, *cells))
    return "\n".join(out)




def test_the_forge_state_table_is_uniform_across_the_doors(tmp_path):
    """Asserted: every corrupt shape refuses at ALL THREE doors with the
    operator's file intact, and the partial state still resumes everywhere."""
    table = render_forge_state_table(tmp_path)
    rows = [
        [c.strip() for c in re.split(r"\s{2,}", r.strip()) if c.strip()]
        for r in table.split("\n")[3:]
    ]
    assert "DESTROYED" not in table, table
    for label, *cells in rows[:3]:
        assert cells == ["REFUSES, intact"] * 3, (label, cells)
    # The partial state resumes at every door. Check REWRITES it, and that is
    # the correct half of the old behaviour kept: Check is the mutating door,
    # and filling in keys NOBODY WROTE destroys nothing. Start and Status only
    # read, so they leave the file alone. Refusing this would have traded a
    # silent destruction for a pipeline that cannot start.
    assert rows[3][1:] == ["ok, REWROTE FILE", "ok", "ok"], rows[3]




def test_an_absent_key_still_takes_its_default(tmp_path):
    """The control, and the distinction the whole fix rests on.

    ABSENT is not WRONG. A partial state must still resume — that is what the
    completion step is for — or the refusal would have traded a silent
    destruction for a planning pipeline that cannot start.
    """
    root = _seed_planning_state(tmp_path, {"phase": "S1"}, "partial")

    result = _forge_door("status", str(root))

    assert "error" not in result, result
    assert result["phase"] == "S1"
    assert [c["status"] for c in result["checklist"]] == ["pending"] * 4




def test_every_ledger_key_container_shape_is_covered_by_the_parity_drive():
    """The drive above must cover the collection keys the module declares.

    ``_LEDGER_KEYS`` is where "which container holds this ledger's records" is
    declared once. Asserting the drive against it means a ledger added there
    surfaces here rather than being silently untested — the failure mode of
    every hand-kept fixture list.
    """
    from foundry_mcp.tools.foundry import _LEDGER_KEYS

    assert _LEDGER_KEYS["defects.json"] == ("defects",), _LEDGER_KEYS
    # Every declared ledger is a real artifact name, and every one with a
    # collection key is reachable from some dispatched door.
    for name, keys in _LEDGER_KEYS.items():
        assert name.endswith(".json"), name
        assert isinstance(keys, tuple), (name, keys)




@pytest.mark.parametrize(
    "axis,taints,phrase", _DECODE_RULE_AXES, ids=[a[0] for a in _DECODE_RULE_AXES]
)
def test_a_blind_read_recogniser_fails_the_decode_rule_by_name(
    monkeypatch, axis, taints, phrase
):
    """D-142's regression for the decode rule, driven as the probe drove it.

    With a recogniser returning nothing -- exactly what a refactor that hoists
    the read behind a new spelling, or a package that drifts to a loader this
    resolver cannot import, does -- the rule must fail on that axis's member
    anchor, not pass on an empty offender list.

    D-153 ADDED THE LOAD ROW, AND IT IS THE ROW THAT WAS MISSING. Re-driven at
    1e07a4c, this test blinded the READ axis only. Blind the LOAD axis instead
    and the rule stayed GREEN: `seen` was keyed on reads, so the member anchor
    held while `offenders` emptied for the worst possible reason -- no site in
    the package was classified as a document load at all. Both axes are driven
    now, and each must fail naming ITS OWN recogniser, because "the rule went
    blind" and "which half of it went blind" are different repairs.
    """
    import tests.orchestration.test_module_boundaries as gates

    for name, blinded in taints.items():
        monkeypatch.setattr(gates, name, blinded)
    with pytest.raises(AssertionError, match=phrase):
        gates.test_no_document_load_can_raise_across_the_mcp_boundary()




@pytest.mark.parametrize(
    "rule,blinded,test_name",
    [
        (
            "_unlocked_artifact_renames",
            (lambda path: ([], [])),
            "test_no_module_renames_onto_a_run_artifact_without_a_lock",
        ),
        (
            "_raw_ledger_iterations",
            (lambda path: ([], [])),
            "test_no_ledger_scan_bypasses_the_malformed_record_filter",
        ),
    ],
    ids=["renames", "ledger"],
)
def test_a_blind_scan_fails_its_own_rule_by_name(monkeypatch, rule, blinded, test_name):
    """D-142's probe, run as a test against the two remaining package rules.

    The probe that filed D-142 replaced each per-module scan with
    ``lambda p: []`` and watched all four rules stay green. Replaced now with
    ``lambda p: ([], [])`` -- a scan that recognises nothing -- each rule must
    fail on its own member anchor and name the derivation, not the corpus.
    """
    import tests.orchestration.test_module_boundaries as gates

    monkeypatch.setattr(gates, rule, blinded)
    with pytest.raises(AssertionError, match="derivation has gone blind"):
        getattr(gates, test_name)()




def test_the_manifest_record_guard_is_the_shared_validator_not_a_local_copy():
    """The binding rule: one validator, not a per-module isinstance chain.

    A private `isinstance` check in each reader would pass the drive above and
    still be the escalated class — five copies of one rule, free to disagree
    about what "unusable" means. The readers here reach D-132's validator, so
    a manifest this module refuses is one foundry_spawn refuses too.
    """
    from foundry_mcp.tools.foundry_spawn import _manifest_shape_problem

    for body in _UNUSABLE_MANIFEST_RECORDS:
        assert _manifest_shape_problem(body) is not None, body
    # ...and the validator is narrow: a healthy manifest is not refused.
    assert _manifest_shape_problem(
        {"castings": [{"id": 1, "key_files": ["a.py"]}], "waves": [{"wave": 1, "casting_ids": [1]}]}
    ) is None




def test_a_directory_at_every_run_document_position_is_named(run_env):
    """D-140 verbatim, held at every artifact type: a path OCCUPYING an
    artifact's name is the guard's business whatever kind of thing it is.
    D-197.

    The true positives the predicate must still catch, pinned one per artifact
    family. Measured against the pre-fix commit: `_run_artifact_problems` at
    4d705a1 named directives.md, forge-log.md, handoffs.jsonl, notes.txt,
    spawns.log and spec.md on this tree; at d872362 it named none of them and
    returned only state.json.
    """
    project_root, fdir = run_env
    _write_state(fdir, phase="F2", cycle=1)
    for position in _RUN_DOCUMENT_POSITIONS:
        (fdir / position).parent.mkdir(parents=True, exist_ok=True)
        target = fdir / position
        if target.exists():
            target.unlink()
        target.mkdir()
    # ...and D-195's false positive in the SAME tree, so neither side of the
    # predicate can be satisfied by sacrificing the other.
    (fdir / "test_observations" / "generated" / ".hypothesis"
     / "unicode_data" / "14.0.0").mkdir(parents=True)

    problems = artifacts._run_artifact_problems(fdir)
    named = {p.split(" could")[0] for p in problems}

    unguarded = sorted(
        position for position in _RUN_DOCUMENT_POSITIONS
        if Path(position).name not in named
    )
    assert unguarded == [], (
        f"document position(s) a directory occupies and the guard walked past: "
        f"{unguarded}. _load_json returns {{}} for each, and the doors then act "
        f"on a fabricated all-default document and report success (D-140, "
        f"D-197)."
    )
    assert not any("14.0.0" in p for p in problems), (
        f"D-195's false positive is back: a version-number directory no reader "
        f"opens was named, and _artifact_guard runs at every MCP entry point, "
        f"so that refuses every door at once. {problems}"
    )




def test_a_directory_at_every_run_marker_position_is_named(run_env):
    """D-140 verbatim, held at the suffix-less half: a path OCCUPYING an
    artifact's name is the guard's business whatever kind of thing it is.
    CT-012, CT-007, AC-013. D-201.

    D-197 widened the SUFFIX TABLE and left the QUESTION keyed on the suffix.
    `Path(".last-next-at").suffix` is `""`, `""` is in no table, so every
    sentinel above was walked past and its door died with `call_tool`'s
    unhandled-error banner instead of the guard's named refusal — measured at
    f5b487b, where `_run_artifact_problems` named NONE of these fifteen and
    named all eight suffixed positions in the same tree.

    The D-195 control sits in the same tree for the same reason it does in the
    suffixed pin: a predicate can always satisfy one side by sacrificing the
    other, and a version-number scratch directory named here refuses every MCP
    door at once.
    """
    project_root, fdir = run_env
    _write_state(fdir, phase="F2", cycle=1)
    for position in _RUN_MARKER_POSITIONS:
        target = fdir / position
        if target.exists():
            target.unlink()
        target.mkdir()
    (fdir / "test_observations" / "generated" / ".hypothesis"
     / "unicode_data" / "14.0.0").mkdir(parents=True)

    problems = artifacts._run_artifact_problems(fdir)
    named = {p.split(" could")[0] for p in problems}

    unguarded = sorted(p for p in _RUN_MARKER_POSITIONS if p not in named)
    assert unguarded == [], (
        f"marker position(s) a directory occupies and the guard walked past: "
        f"{unguarded}. Each is a name this package writes and READS BACK, so "
        f"the read raises out of call_tool as an unhandled error instead of "
        f"refusing by name — CT-012's Foundry-Next, whose errors cell reads "
        f"'none; never blocks', among them (D-140, D-197, D-201)."
    )
    assert not any("14.0.0" in p for p in problems), (
        f"D-195's false positive is back: a version-number directory no reader "
        f"opens was named, and _artifact_guard runs at every MCP entry point, "
        f"so that refuses every door at once. {problems}"
    )




def test_every_stream_completion_marker_is_a_document_position():
    """D-201: the completion sentinel family is DERIVED, not enumerated.

    `.trace-complete` and `.prove-complete` are two members of
    `f".{wire_id}-complete"` over the closed stream vocabulary. Listing the
    members in `_RUN_MARKER_NAMES` would leave the next wire id unguarded on
    the day `STREAM_WIRE_IDS` gains it — the hand-kept-list defect D-129 and
    D-138 were both filed on — so the guard derives the family and this asserts
    the derivation reaches every member.
    """
    from foundry_mcp.schemas.vocab import STREAM_WIRE_IDS

    assert STREAM_WIRE_IDS, "empty vocabulary would make this pass vacuously"
    unguarded = sorted(
        marker for wire_id in STREAM_WIRE_IDS
        if not artifacts._is_document_position(Path("run") / (marker := f".{wire_id}-complete"))
    )
    assert unguarded == [], (
        f"stream completion marker(s) the guard walks past: {unguarded}. "
        f"_check_streams_complete opens each of these, so a directory on one "
        f"raises out of the door instead of refusing by name (D-201)."
    )




def _seed_dispatch_ledger(fdir: Path) -> None:
    """`spawns.log` naming two dispatched castings, and no spend record."""
    (fdir / "spawns.log").write_text(
        '{"timestamp": "2026-09-03T00:55:14+00:00", "casting_id": 1, '
        '"phase": "cast"}\n'
        '{"timestamp": "2026-09-03T00:55:15+00:00", "casting_id": 2, '
        '"phase": "cast"}\n',
        encoding="utf-8",
    )




def test_foundry_next_lists_unreported_dispatches_and_refuses_on_an_occupied_spawns_log(
    run_env, monkeypatch
):
    """AC-034 verbatim: 'A dispatched agent with no spend record is shown as
    unreported in Foundry-Next and the report, and no gate refuses on it.'
    FR-022 / CT-013. D-197.

    The harm, at the transport a client uses. With a readable `spawns.log` the
    two dispatched castings are named; with a DIRECTORY on that name the same
    call returned ok with `unreported_count 0`, no error and no warning — the
    two agents AC-034 requires to be shown were invisible, while Foundry-Report
    on the same run refused naming the file. One artifact, two doors, two
    stories, which is D-140's shape exactly.
    """
    import foundry_mcp.server as srv

    project_root, fdir = run_env
    _write_state(fdir, phase="F1", cycle=0)
    _seed_dispatch_ledger(fdir)
    monkeypatch.setattr(srv, "_project_root", project_root)

    reported = _drive_mcp("Foundry-Next", {})
    assert "casting-1" in reported, reported[:600]
    assert "casting-2" in reported, reported[:600]
    assert "Run artifacts cannot be read" not in reported, reported[:600]

    (fdir / "spawns.log").unlink()
    (fdir / "spawns.log").mkdir()

    occupied = _drive_mcp("Foundry-Next", {})
    assert "Run artifacts cannot be read" in occupied, occupied[:600]
    assert "spawns.log" in occupied, occupied[:600]




def test_a_directory_on_the_stall_clock_refuses_foundry_next_by_name(
    run_env, monkeypatch
):
    """CT-012's errors cell, verbatim: 'none; never blocks'. D-201.

    THE SENTINEL DOOR, DRIVEN. `.last-next-at` is the stall clock CT-012 reads
    at every Foundry-Next, and at f5b487b a DIRECTORY on it returned
    "Foundry-Next failed: IsADirectoryError: [Errno 21] Is a directory: ..."
    with `corrupt_artifacts` ABSENT. That banner is `call_tool`'s outermost net
    catching an unhandled exception several frames below the entry point — the
    very thing this module's house rule forbids ("a tool never raises across
    the MCP boundary, it returns {error, hint}") — and it is not the guard's
    refusal, so the operator is handed a traceback rather than the name of the
    file to repair. The suffixed positions in the same tree were all refused by
    name, which is what makes the missing axis the finding.
    """
    import foundry_mcp.server as srv

    project_root, fdir = run_env
    _write_state(fdir, phase="F2", cycle=1)
    monkeypatch.setattr(srv, "_project_root", project_root)

    healthy = _drive_mcp("Foundry-Next", {})
    assert "Run artifacts cannot be read" not in healthy, healthy[:600]

    marker = fdir / ".last-next-at"   # spelled out: a test that read the
    #                                   constant would follow a rename in
    #                                   silence, and it is the NAME on disk
    #                                   that a door opens.
    if marker.exists():
        marker.unlink()
    marker.mkdir()

    occupied = _drive_mcp("Foundry-Next", {})
    assert "Run artifacts cannot be read" in occupied, occupied[:600]
    assert ".last-next-at" in occupied, occupied[:600]
    assert "Foundry-Next failed" not in occupied, (
        f"the outer unhandled-error banner is back, so the read raised across "
        f"the MCP boundary instead of the guard refusing by name: {occupied[:600]}"
    )




def test_a_directory_on_the_inspect_boundary_sha_refuses_the_transition_by_name(
    run_env, monkeypatch
):
    """CT-007 / AC-013 / ST-005: the inspect_start transition re-executes the
    in-scope evidence logs and 'refuses the transition naming any log whose
    output mismatches'. D-201.

    THE BOUNDARY SWEEP CANNOT PROVE ANYTHING ON A DOCUMENT IT HAD TO GUESS AT.
    `.inspect-boundary-sha` records the HEAD the next crossing measures "since
    the last boundary" from, and at f5b487b a DIRECTORY on it was walked past
    by `_is_document_position`: the transition proceeded on a fabricated empty
    marker rather than refusing, so the GI-002/ST-005 sweep that exists to
    prove the committed evidence still reproduces neither swept nor refused by
    name. The guard now names it before the transition opens.
    """
    import foundry_mcp.server as srv

    project_root, fdir = run_env
    _write_state(fdir, phase="F3", cycle=1)
    monkeypatch.setattr(srv, "_project_root", project_root)

    marker = fdir / ".inspect-boundary-sha"   # the NAME on disk, not the
    #                                           constant that spells it
    if marker.exists():
        marker.unlink()
    marker.mkdir()
    _arm_ordering_token(fdir)

    occupied = _drive_mcp("Foundry-Phase", {"phase": "inspect_start"})
    assert "Run artifacts cannot be read" in occupied, occupied[:600]
    assert ".inspect-boundary-sha" in occupied, occupied[:600]
    # The counter is the proof the transition did not happen: a refusal that
    # advanced it would leave the run one cycle ahead of the sweep that never
    # ran (CT-007: 'the cycle counter does not advance on refusal').
    assert current_cycle(fdir) == 1, current_cycle(fdir)




def test_the_sidecars_the_writers_create_are_the_ones_the_scan_excludes(run_env):
    """DERIVED, not typed twice: the exclusion is asserted against the sidecars
    the REAL write primitives produce, so a writer that changes its naming fails
    here rather than silently re-opening the race.
    """
    _project_root, fdir = run_env
    seen: list[Path] = []
    real_write_text = Path.write_text

    def spy(self, *args, **kwargs):
        seen.append(self)
        return real_write_text(self, *args, **kwargs)

    Path.write_text = spy
    try:
        artifacts._save_json(fdir / "state.json", {"phase": "F2"})
        with artifacts._document_transaction(fdir / "defects.json") as doc:
            doc["defects"] = []
    finally:
        Path.write_text = real_write_text

    tmp_sidecars = [p for p in seen if p.name.endswith(artifacts._TX_TMP_SUFFIX)]
    assert tmp_sidecars, "no _save_json sidecar observed — the spy missed the write"
    for sidecar in tmp_sidecars:
        assert artifacts._is_write_sidecar(sidecar), sidecar

    lock = fdir / ("defects.json" + artifacts._TX_LOCK_SUFFIX)
    assert lock.exists(), "the transaction's lock sidecar was not created"
    assert artifacts._is_write_sidecar(lock)
    # And the lock sitting in the run dir is not reported as an artifact.
    assert not any(lock.name in p for p in artifacts._run_artifact_problems(fdir))




def test_a_real_artifact_that_vanishes_mid_scan_is_absent_not_corrupt(run_env):
    """`read_text_file`'s own rule — "an ABSENT file is not a problem" — decided
    from an `exists()` taken BEFORE the read, so a file removed between the two
    landed in its OSError arm and was named unreadable. The scan now holds the
    same rule for a file that became absent DURING it, which is the only way the
    two answers could ever disagree.
    """
    _project_root, fdir = run_env
    ghost = fdir / "vanished.json"

    assert artifacts._unless_it_vanished(ghost, "vanished.json could not be read (x)") is None
    # A file that IS there keeps its named problem, so the guard has not gone soft.
    ghost.write_bytes(b"\xff\xfe not utf-8")
    assert artifacts._unless_it_vanished(ghost, "vanished.json could not be read (x)") == (
        "vanished.json could not be read (x)"
    )




def test_the_derived_guard_table_catches_a_fresh_copy_anywhere(tmp_path):
    """The structural claim, asserted rather than described.

    Each planted copy is a verbatim reproduction of the shape one of the prior
    fixes closed, written in a module none of them touched. All of them are
    named. This is what "unrepresentable" means here: not that today's copies
    are gone, but that tomorrow's cannot arrive unannounced.
    """
    table = render_derived_guard_table(tmp_path)
    assert "MISSED" not in table, table
    # The shipped source itself is clean, or the planted-copy claim proves
    # nothing...
    assert table.count("none") == 3, table
    # ...and each rule SAW its anchor while reporting none, or "clean" and
    # "blind" would read identically here (D-142).
    assert table.count("sees ") == 3, table
    assert ": False)" not in table, table




def _d137_run(tmp_root: Path, raw: bytes) -> str:
    """A minimal run dir whose manifest carries ``raw`` verbatim."""
    from foundry_mcp.tools import foundry_state as fst

    fdir = tmp_root / "foundry-archive" / "d137"
    (fdir / "castings").mkdir(parents=True, exist_ok=True)
    (fdir / "castings" / "manifest.json").write_bytes(raw)
    (fdir / "castings" / "casting-1-prompt.md").write_text("# casting 1\n", encoding="utf-8")
    (fdir / "state.json").write_text(json.dumps({"phase": "F1", "cycle": 0}), encoding="utf-8")
    fst.set_active_run("d137")
    return str(tmp_root)




def render_guarded_read_table(tmp_path: Path) -> str:
    """D-137 pre/post: one non-UTF-8 byte at the two spawn doors.

    The PRE arm is the shape all fourteen sites held, reproduced verbatim
    rather than described, so the log shows the raise instead of asserting it
    happened once.
    """
    from foundry_mcp.tools import foundry_spawn as fs

    def old_read(p: Path):
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}

    out = [
        "== D-137: one non-UTF-8 byte in castings/manifest.json ==",
        "",
        "-- why `except json.JSONDecodeError` never covered this --",
        f"   issubclass(UnicodeDecodeError, ValueError)           = "
        f"{issubclass(UnicodeDecodeError, ValueError)}",
        f"   issubclass(UnicodeDecodeError, json.JSONDecodeError) = "
        f"{issubclass(UnicodeDecodeError, json.JSONDecodeError)}",
    ]
    root = _d137_run(tmp_path / "pre", _D137_BAD_MANIFEST)
    manifest = Path(root) / "foundry-archive" / "d137" / "castings" / "manifest.json"
    try:
        old_read(manifest)
        out.append("   the 14-site shape  : returned (no raise)")
    except Exception as exc:
        out.append(f"   the 14-site shape  : RAISES {type(exc).__name__}")

    doors = (
        ("Foundry-Spawn-Teammate", lambda r: fs.foundry_spawn_teammate(1, "cast", r)),
        ("Foundry-Cast-Wave", lambda r: fs.foundry_cast_wave(1, "cast", r)),
    )
    out += ["", "-- the same byte through the shipped doors, post-fix --"]
    refusals = []
    for i, (label, call) in enumerate(doors):
        root = _d137_run(tmp_path / f"bad{i}", _D137_BAD_MANIFEST)
        try:
            res = call(root)
        except Exception as exc:
            out.append(f"   {label:24s} RAISED {type(exc).__name__} across MCP")
            continue
        refusals.append((res.get("error", "").split(": /")[0], res.get("hint", "")))
        out.append(
            f"   {label:24s} ok={res.get('ok')}  names the file="
            f"{'manifest.json' in res.get('error', '')}"
        )
        out.append(f"      {refusals[-1][0]}")
    out.append(
        f"   both doors tell ONE story about the file: "
        f"{len(set(refusals)) == 1}"
    )

    out += ["", "-- control: the refusal is NARROW (same document, valid UTF-8) --"]
    for i, (label, call) in enumerate(doors):
        root = _d137_run(tmp_path / f"good{i}", _D137_GOOD_MANIFEST)
        res = call(root)
        out.append(
            f"   {label:24s} ok={res.get('ok')}  read-fault reported="
            f"{'could not be read' in res.get('error', '')}"
        )
    return "\n".join(out)




def test_the_guarded_read_table_shows_the_raise_and_the_refusal(tmp_path):
    """D-137's drive, ASSERTED so the log is a claim and not a picture."""
    table = render_guarded_read_table(tmp_path)

    # The pre arm really does raise, and for the reason the fix is built on.
    assert "the 14-site shape  : RAISES UnicodeDecodeError" in table, table
    assert "issubclass(UnicodeDecodeError, json.JSONDecodeError) = False" in table
    # Neither door raises now, both name the file, and they agree on the text.
    assert "RAISED" not in table, table
    assert table.count("names the file=True") == 2, table
    assert "both doors tell ONE story about the file: True" in table, table
    # ...and the refusal did not swallow the working case.
    assert table.count("ok=True  read-fault reported=False") == 2, table




def test_the_planted_copies_are_the_shapes_the_prior_fixes_closed(tmp_path):
    """Guards the guard: a planted copy that no rule fires on would make the
    table above vacuous, so each shape is checked against its own rule."""
    loader = _plant(tmp_path, "a.py", _PLANTED_LOADER)
    writer = _plant(tmp_path, "b.py", _PLANTED_WRITER)
    scan = _plant(tmp_path, "c.py", _PLANTED_LEDGER_SCAN)

    assert _unguarded_document_loads(loader)[1] == [
        "a.py::_load_json:7 leaks OSError+UnicodeDecodeError+JSONDecodeError"
    ]
    assert _unlocked_artifact_renames(writer)[1] == ["b.py::_save_json:6"]
    assert _raw_ledger_iterations(scan)[1] == ["c.py::sync:3"]
    # ...and each rule is silent on the other two shapes: three rules, three
    # distinct classes, no rule standing in for another.
    assert _unguarded_document_loads(writer)[1] == []
    assert _unlocked_artifact_renames(loader)[1] == []
    assert _raw_ledger_iterations(loader)[1] == []
    # Each rule SAW its own plant, which is what makes "silent on the other
    # two" a statement about the rules and not about an empty scan (D-142).
    assert _unguarded_document_loads(loader)[0] == ["a.py#_load_json"]
    assert _unlocked_artifact_renames(writer)[0] == ["b.py#_save_json"]
    assert _raw_ledger_iterations(scan)[0] == ["c.py#sync"]




def test_a_jsondecodeerror_only_handler_over_a_read_is_an_offender(tmp_path):
    """D-137's structural half, asserted directly.

    The old rule matched handler NAMES against a frozenset holding
    "JSONDecodeError", so both spellings below were counted as covered while
    one non-UTF-8 byte raised straight across the MCP boundary from the two
    spawn doors. The rule now asks the exception hierarchy instead, so what it
    reports is what Python actually does:

        issubclass(UnicodeDecodeError, ValueError)           -> True
        issubclass(UnicodeDecodeError, json.JSONDecodeError) -> False

    The third arm is the control that keeps the rule narrow. Without it the
    rule would flag every document load, the canonical primitive included, and
    the only way out would be the name allow-list this replaced.
    """
    # The hierarchy claim the rule rests on, asserted rather than assumed.
    assert issubclass(UnicodeDecodeError, ValueError)
    assert not issubclass(UnicodeDecodeError, json.JSONDecodeError)

    decode_only = _plant(tmp_path, "d.py", _PLANTED_DECODE_ONLY_LOADER)
    with_oserror = _plant(tmp_path, "e.py", _PLANTED_OSERROR_DECODE_LOADER)
    covered = _plant(tmp_path, "f.py", _PLANTED_COVERED_LOADER)

    assert _unguarded_document_loads(decode_only)[1] == [
        "d.py::_load_manifest:5 leaks OSError+UnicodeDecodeError"
    ]
    # Adding OSError closes the open() failure and leaves the bytes uncovered —
    # which is the majority spelling among the fourteen sites, and exactly the
    # residual a name-matching rule cannot see.
    assert _unguarded_document_loads(with_oserror)[1] == [
        "e.py::_load_manifest:5 leaks UnicodeDecodeError"
    ]
    # ValueError IS UnicodeDecodeError's parent, so this one genuinely holds —
    # and the rule still SAW the read, which is what separates "covered" from
    # "not recognised".
    seen, offenders = _unguarded_document_loads(covered)
    assert offenders == []
    assert seen == ["f.py#_load_manifest"]




def test_the_decode_rule_resolves_the_load_spelling_rather_than_matching_it(tmp_path):
    """D-147, asserted directly: the expression axis is derived too.

    The handler axis resolved a name to a real exception class and asked
    ``issubclass``; the expression axis asked whether the callee was SPELLED
    ``json.something``. So three writings of one operation — an aliased
    import, a from-import, and a read split from its decode — were reported
    CLEAN with no handler at all, while the plain spelling was reported. A
    module that aliased its import escaped the rule entirely.

    Every arm below carries NO adequate handler, so a rule that sees the
    operation must report it; one that matches the string cannot.
    """
    aliased = _plant(tmp_path, "h.py", _PLANTED_ALIASED_LOADER)
    from_import = _plant(tmp_path, "i.py", _PLANTED_FROM_IMPORT_LOADER)
    split = _plant(tmp_path, "j.py", _PLANTED_SPLIT_LOADER)
    covered_split = _plant(tmp_path, "k.py", _PLANTED_COVERED_SPLIT_LOADER)
    reraising = _plant(tmp_path, "l.py", _PLANTED_RERAISING_LOADER)

    leaks = "leaks OSError+UnicodeDecodeError+JSONDecodeError"
    # `import json as j` — the live spelling foundry_orchestrator.py already
    # carries, on the day someone writes a load under it.
    assert _unguarded_document_loads(aliased)[1] == [f"h.py::_load_manifest:4 {leaks}"]
    # `from json import loads` — no dotted owner to match a string against.
    assert _unguarded_document_loads(from_import)[1] == [f"i.py::_load_manifest:4 {leaks}"]
    # The read and the decode two statements apart, each under a handler that
    # answers the OTHER rung: `except FileNotFoundError` leaves the read's
    # OSError and UnicodeDecodeError open even though a JSONDecodeError
    # handler sits below it. Judged per rung, not by the union.
    assert _unguarded_document_loads(split)[1] == [
        "j.py::_load_manifest:10 leaks OSError+UnicodeDecodeError"
    ]
    # ...and the control that keeps the split rule narrow: each rung answered
    # where it sits is genuinely covered.
    assert _unguarded_document_loads(covered_split)[1] == []
    # D-147's second gap: catching everything and raising a NEW type is not
    # covering — the operation still crosses the boundary, as something the
    # caller has no handler for. The converting handler names itself.
    reraise_offenders = _unguarded_document_loads(reraising)[1]
    assert len(reraise_offenders) == 1, reraise_offenders
    assert leaks in reraise_offenders[0], reraise_offenders
    assert "re-raises: Exception" in reraise_offenders[0], reraise_offenders




def test_an_unresolvable_load_spelling_is_reported_not_skipped(tmp_path):
    """The load axis fails toward REPORTING, exactly as the handler axis does.

    A callee this resolver cannot see, whose last segment is nonetheless a
    loader's name, is treated as a member and carries its spelling into the
    message. Silent acceptance is the failure mode the whole D-137/D-147 line
    exists to remove, and it must not be reintroduced one axis over.
    """
    mystery = _plant(
        tmp_path,
        "m.py",
        "from some_vendor_lib import codec\n"
        "\n"
        "def _load(path):\n"
        "    return codec.loads(path.read_text(encoding='utf-8'))\n",
    )
    offenders = _unguarded_document_loads(mystery)[1]
    assert len(offenders) == 1, offenders
    assert "unresolved load spelling: codec.loads" in offenders[0], offenders




def test_the_decode_rule_resolves_handler_names_rather_than_matching_them(tmp_path):
    """The axis that was hand-kept, asserted as derived.

    A handler naming an exception this resolver cannot see must cover NOTHING
    (the conservative direction — it can only over-report) and must SAY so, so
    an unrecognised member announces itself instead of silently widening the
    gap. That is the difference between this and the frozenset it replaces:
    the old table's failure mode was silent acceptance.
    """
    exotic = _plant(
        tmp_path,
        "g.py",
        "import json\n"
        "\n"
        "def _load(path):\n"
        "    try:\n"
        "        return json.loads(path.read_text(encoding='utf-8'))\n"
        "    except SomeVendorError:\n"
        "        return {}\n",
    )
    offenders = _unguarded_document_loads(exotic)[1]
    assert len(offenders) == 1, offenders
    assert "leaks OSError+UnicodeDecodeError+JSONDecodeError" in offenders[0]
    assert "unresolved handler names: ['SomeVendorError']" in offenders[0], offenders




def test_the_external_input_membership_is_derived_over_the_state_document(tmp_path):
    """The axis, asserted: a NEW declared key is a member the day it is written.

    This is the difference between the fix and the instance. `spec_path` is
    nowhere in `_declared_external_inputs`; what the derivation knows is that a
    string leaf of state.json which resolves to an existing FILE outside the
    run dir is an input this run declared. Plant the corrupt file under a key
    nobody has ever heard of and the guard must still name it — that is the
    "NEW unbound member" test for this mechanism.
    """
    root, fdir = _external_spec_run(
        tmp_path, "inputs/context.txt", _BAD_UTF8_SPEC, state_key="operator_context_path"
    )
    try:
        declared = artifacts._declared_external_inputs(fdir)
        assert [p.name for p in declared] == ["context.txt"], declared
        problems = artifacts._run_artifact_problems(fdir)
        assert any("context.txt" in p for p in problems), problems
    finally:
        foundry_state.clear_active_run()




def test_declared_leaves_that_name_no_file_are_not_members(tmp_path):
    """...and the derivation stays narrow, or it would report the whole run.

    state.json's ordinary leaves are phase tokens, run names and spec TYPES,
    none of which name a file; a leaf that names a DIRECTORY is a container,
    judged by what a reader opens inside it. Neither may become a problem, or
    the guard cries wolf at every door and gets disabled.
    """
    root = tmp_path / "proj"
    fdir = root / "foundry-archive" / "d145b"
    fdir.mkdir(parents=True)
    (root / "forge-specs").mkdir()
    (fdir / "state.json").write_text(
        json.dumps({
            "phase": "F2",
            "run": "d145b",
            "spec_type": "GREENFIELD",
            "cycle": 3,
            "no_ui": True,
            "some_dir": "forge-specs",
            "nested": {"deep": ["also-not-a-file"]},
        }),
        encoding="utf-8",
    )
    foundry_state.set_active_run("d145b")
    try:
        assert artifacts._declared_external_inputs(fdir) == []
        assert artifacts._run_artifact_problems(fdir) == []
    finally:
        foundry_state.clear_active_run()




def test_the_worktrees_exclusion_names_what_setup_worktree_roots_under():
    """D-206's HOW-MEMBERSHIP axis: the exclusion and the creation cannot drift.

    `worktree_helpers._setup_worktree` is the ONE writer that decides where a
    worktree is nested, and it spells that directory as a literal inside its own
    body. `RUN_WORKTREES_DIRNAME` is a second spelling of the same fact in
    another module, which is exactly the hand-kept-list shape D-129 and D-138
    were filed on — so the fact is derived back out of the writer's own source
    and compared, and this fails the day either side moves.

    The one-declaration shape (exporting the name from `worktree_helpers` and
    importing it) belongs to that module's owning casting and is recorded in
    `foundry-archive/daring-orca/concerns.md`; this pin is what stands in for it
    until then.
    """
    roots = _setup_worktree_root_dirnames()

    assert roots == [artifacts.RUN_WORKTREES_DIRNAME], (
        f"_setup_worktree nests worktrees under {roots}, the guard walks past "
        f"{artifacts.RUN_WORKTREES_DIRNAME!r}; one of the two has moved"
    )




def test_no_formatter_can_drop_a_named_refusal():
    """ST-003's class assertion for D-157, over the WHOLE formatter table.

    The reported path is Foundry-Next. Five of the twenty-two formatters drop a
    named refusal on their own -- Foundry-Next, Foundry-Context, Foundry-Init,
    Validate-Report, Verify-Citations -- so a fix bound to Foundry-Next would
    have left four live and formatter twenty-three free to decide it again.
    Membership is every entry of `_FORMATTERS`, derived, so a formatter added
    tomorrow is a member the day it is registered.
    """
    from foundry_mcp.tools import display

    refusal = {
        "error": (
            "Run artifacts cannot be read: spec.md could not be read "
            "(UnicodeDecodeError: invalid continuation byte)."
        ),
        "hint": "Repair or delete the named file(s) in the run directory.",
        "corrupt_artifacts": ["spec.md could not be read (UnicodeDecodeError: ...)"],
    }
    assert display._FORMATTERS, "the formatter table is empty -- this rule is vacuous"

    dropped = [
        tool for tool in sorted(display._FORMATTERS)
        if refusal["error"] not in display.format_result(tool, dict(refusal))
    ]
    assert dropped == [], (
        f"{dropped} render a result that NAMES a refusal without carrying the "
        f"refusal, so the handler declined and the operator was not told. The "
        f"guarantee belongs to `format_result`, not to each formatter -- do not "
        f"fix this by adding an `if r.get('error')` branch to the named tools."
    )
    # ...and the anchor: the rule must still SEE the members it polices, so a
    # `format_result` that started returning the refusal for everything (or a
    # table that emptied) cannot make the assertion above pass vacuously.
    per_formatter = [
        tool for tool, fmt in sorted(display._FORMATTERS.items())
        if refusal["error"] not in fmt(dict(refusal))
    ]
    assert per_formatter, (
        "no formatter in the table drops a refusal on its own any more, so this "
        "rule is no longer exercising the router's guarantee. If every formatter "
        "now renders its own refusal, delete this anchor deliberately -- do not "
        "let it pass by accident."
    )




def test_a_new_formatter_that_drops_a_refusal_is_still_rendered(monkeypatch):
    """The NEW unbound member. A formatter nobody has written yet.

    This is the difference between the fix and the instance: register a
    formatter that ignores `error` entirely -- the shape all five offenders had
    -- and the refusal must still reach the screen, because the guarantee is the
    router's and not the formatter's.
    """
    from foundry_mcp.tools import display

    monkeypatch.setitem(
        display._FORMATTERS, "Foundry-Brand-New-Tool", lambda r: "nothing to see here"
    )
    rendered = display.format_result(
        "Foundry-Brand-New-Tool",
        {"error": "state.json could not be read (UnicodeDecodeError: x)",
         "hint": "Repair it.", "corrupt_artifacts": ["state.json"]},
    )
    assert "state.json could not be read" in rendered, rendered
    assert "Repair it." in rendered, rendered
    assert "Foundry-Brand-New-Tool refused" in rendered, rendered




def test_a_result_that_names_no_refusal_is_left_exactly_as_the_formatter_rendered_it():
    """...and the guarantee stays NARROW, or it is an outage.

    Every healthy result must render byte-identically to what its own formatter
    produced. A post-condition that fired on success would replace the whole
    display layer with an error box.
    """
    from foundry_mcp.tools import display

    healthy = {
        "Foundry-Next": {"display": "BANNER", "instructions": "do the thing"},
        "Foundry-Phase": {"phase": "F2", "message": "moved"},
        "Foundry-Gate": {"phase": "F2", "passed": True, "checklist": []},
        "Foundry-Init": {"run_name": "r", "spec_path": "s", "castings": 1},
    }
    for tool, result in healthy.items():
        assert display.format_result(tool, dict(result)) == display._FORMATTERS[tool](
            dict(result)
        ), tool




def test_foundry_gate_and_foundry_context_tell_the_same_story_on_the_same_fixture(tmp_path):
    """D-157 adjacent-path test (AC-013).

    The path the defect was reported on is Foundry-Next's guidance render. The
    ADJACENT paths this drives are the two OTHER consumers of the same declared
    external input named in the defect: Foundry-Gate, which renames the guard's
    `error` into its own `reason` key, and Foundry-Context, which was the second
    formatter dropping the refusal outright. Different doors, different result
    shapes, one story -- an operator must not be able to tell which door noticed.
    """
    stories = {}
    for tool in ("Foundry-Gate", "Foundry-Context", "Foundry-Next"):
        result, rendered = _corrupt_external_input_door_drive(tmp_path, tool)
        assert any("spec.md" in str(p) for p in result.get("corrupt_artifacts") or []), (
            tool, result,
        )
        assert "spec.md" in rendered, (tool, rendered)
        stories[tool] = tuple(sorted(result.get("corrupt_artifacts") or []))
    assert len(set(stories.values())) == 1, stories




@pytest.mark.parametrize("script", ["measure-run.py", "migrate-archive.py"])
def test_the_shipped_cli_loaders_do_not_raise_on_a_non_utf8_document(tmp_path, script):
    """D-141's two driven sites, as a regression rather than a transcript.

    Both `_load_json` copies are byte-identical and both raised. Their contract
    was always "None on missing/malformed"; a non-UTF-8 byte is malformed, and
    it is now answered as such instead of leaving the function by exception.
    """
    document = tmp_path / "defects.json"
    document.write_bytes(_BAD_UTF8_DOCUMENT)
    module = _load_cli_module(script)

    assert module._load_json(document) is None
    # ...and the narrow control: a healthy document still parses.
    document.write_bytes(b'{"a": "cafe"}')
    assert module._load_json(document) == {"a": "cafe"}
    # ...and an absent one is still "absent", never confused with "corrupt".
    assert module._load_json(tmp_path / "nope.json") is None




def test_the_shipped_clis_all_read_through_the_one_primitive():
    """The KEY LINK, asserted: no CLI re-decides the raise set for itself.

    Three scripts each carried their own tolerant loader and each named its own
    handler set; that is the copy-per-site the whole D-137 line is made of. The
    assertion is on the import, because a script that imports the primitive and
    then writes a fresh `except` beside it is caught by the decode scan, while a
    script that never imports it is the one that can drift.
    """
    for name in ("measure-run.py", "migrate-archive.py", "validate-test-observations.py"):
        source = _shipped_cli(name).read_text(encoding="utf-8")
        assert "from foundry_mcp.tools.foundry_state import" in source, name




def test_the_shipped_clis_refuse_a_non_utf8_input_without_a_traceback(tmp_path):
    """The operator-visible half, driven through the real process boundary.

    A CLI that raises prints a traceback naming no file and exits 1 by
    accident; a CLI that refuses names the input. Each script is invoked the
    way its own tests invoke it — `sys.executable script args` — so the
    sys.path bootstrap is exercised too, not assumed.
    """
    import subprocess

    fdir = tmp_path / "foundry-archive" / "d141"
    fdir.mkdir(parents=True)
    (fdir / "defects.json").write_bytes(_BAD_UTF8_DOCUMENT)
    (fdir / "state.json").write_text(json.dumps({"cycle": 2}), encoding="utf-8")
    observation = tmp_path / "observation.json"
    observation.write_bytes(_BAD_UTF8_DOCUMENT)

    drives = [
        ("measure-run.py", [str(fdir)]),
        ("migrate-archive.py", [str(fdir)]),
        ("validate-test-observations.py", [str(observation)]),
    ]
    for name, args in drives:
        proc = subprocess.run(
            [sys.executable, str(_shipped_cli(name)), *args],
            capture_output=True, text=True, timeout=60,
        )
        combined = proc.stdout + proc.stderr
        assert "Traceback" not in combined, (name, combined)
        assert "UnicodeDecodeError" not in proc.stderr, (name, proc.stderr)
        assert proc.returncode in (0, 1), (name, proc.returncode, combined)




def test_the_rename_rule_resolves_the_move_primitive_rather_than_matching_it(tmp_path):
    """D-147 adjacent-path test: the same axis, one rule over.

    The path the defect was reported on is `_is_document_load`'s
    `func.value.id == "json"`. The ADJACENT path this drives is the rename
    rule's `func.value.id == "os"` — a different rule, a different module
    literal, the same silent hole: alias the import and the site disappears.
    `os.replace` is now compared as an object.
    """
    aliased = _plant(tmp_path, "n.py", _PLANTED_ALIASED_RENAME)
    seen, offenders = _unlocked_artifact_renames(aliased)
    assert offenders == ["n.py::_save_json:6"], offenders
    assert seen == ["n.py#_save_json"], seen




def test_the_ledger_rule_resolves_the_primitive_rather_than_matching_it(tmp_path):
    """...and the third axis, on a module that really imports the primitive.

    `_tx` is `ledger_transaction`, so the binding is a member; the bare-name
    fallback stays for a module this resolver cannot import, which is what the
    synthetic `c.py` plant exercises.
    """
    aliased = _plant(tmp_path, "o.py", _PLANTED_ALIASED_LEDGER_SCAN)
    seen, offenders = _raw_ledger_iterations(aliased)
    assert offenders == ["o.py::sync:5"], offenders
    assert seen == ["o.py#sync"], seen




def test_the_shipped_tree_decode_table_shows_the_raise_and_the_refusal(tmp_path):
    """D-141's drive, ASSERTED so the log is a claim and not a picture."""
    table = render_shipped_tree_decode_table(tmp_path)
    assert "scripts                  is_dir=True" in table, table
    assert "the 3-script shape : RAISES UnicodeDecodeError" in table, table
    assert table.count("_load_json -> None") == 2, table
    assert "traceback=True" not in table, table
    assert table.count("names-a-token=True") == 3, table




def test_the_external_input_guard_table_shows_the_raise_and_the_refusal(tmp_path):
    """D-145's drive, ASSERTED so the log is a claim and not a picture."""
    table = render_external_input_guard_table(tmp_path)
    assert "...which resolves OUTSIDE     : True" in table, table
    assert "spec_path.read_text : RAISES UnicodeDecodeError" in table, table
    assert "RAISED" not in table.split("-- control:")[0].replace(
        "spec_path.read_text : RAISES UnicodeDecodeError", ""), table
    assert "names the file=True" in table, table
    assert "guard problems = []" in table, table
    assert "read-fault reported=False" in table, table
    assert "['context.txt'] reported=['context.txt']" in table, table




def test_the_load_spelling_table_shows_the_miss_and_the_fix(tmp_path):
    """D-147's drive, ASSERTED so the log is a claim and not a picture."""
    table = render_load_spelling_table(tmp_path)
    # The control the old rule DID see, so the misses below are about the
    # spelling and not about the rule being off.
    control = next(r for r in table.split("\n") if "json.loads(read_text)" in r)
    assert control.count("REPORTED") == 2, control
    # Three spellings the string match called clean, every one now reported.
    for label in ("import json as j", "from json import loads",
                  "read and decode split", "except Exception: raise Boom()"):
        row = next(r for r in table.split("\n") if label in r)
        assert "clean" in row and "REPORTED" in row, row
    # ...and the narrow control: both rungs answered is clean under both rules.
    both = next(r for r in table.split("\n") if "both rungs answered" in r)
    assert both.count("clean") == 2, both
    assert "carries `import json as _json`: True" in table, table
    assert "unresolved load spelling: codec.loads" in table, table




def test_the_traceability_matrix_carries_an_observable_truth(tmp_path):
    """D-150 end to end, through the reader furthest from the change.

    `parsers/spec.py` held the last two copies of the seven-family literal, and
    `citation.verify_citations` builds its whole traceability matrix out of
    `extract_requirements`. So an OT- requirement was not merely uncovered in
    that matrix — it had no ROW: the spec was read, the verdict citing it was
    parsed, and the requirement simply did not exist as far as the parser was
    concerned. Nothing reported a gap, because a gap needs two sides.

    NFR-002 is asserted alongside it: the US- row the old literal always
    produced is still produced, still covered, and still carries its text.
    """
    from foundry_mcp.tools.citation import verify_citations

    spec, report = _traceability_fixture(tmp_path)
    result = verify_citations(
        spec_path=spec, report_path=report, project_root=str(tmp_path)
    )
    rows = {r["requirement_id"]: r for r in result["traceability_matrix"]}

    assert "US-1" in rows, f"NFR-002 narrowing — the old family lost its row: {rows}"
    assert "OT-011" in rows, (
        f"the traceability matrix still has no row for an observable truth, so "
        f"`extract_requirements` is not reading the declared families: {rows}"
    )
    assert rows["OT-011"]["status"] == "covered", rows["OT-011"]
    assert rows["OT-011"]["requirement_text"].startswith("the observable truth")




def test_the_spec_parser_keeps_its_own_anchoring(tmp_path):
    """The grant was two ID sub-patterns, not the parser's shape.

    `extract_requirements` decides how a requirement is ANCHORED in a line
    (leading text, the id, the separator run) and how the TEXT after it is
    captured, including the line number. Swapping the id sub-pattern must not
    move any of that, so it is pinned here rather than assumed: the em-dash
    separator, the bold markers, a dotted id, and the 1-based line number.
    """
    from foundry_mcp.parsers.spec import extract_requirements, extract_requirement_ids

    text = (
        "# Spec\n"
        "- **US-1**: a user story\n"
        "- **OT-011** — an observable truth\n"
        "prose mentioning AC-3.2: nested id\n"
    )
    reqs = extract_requirements(text)
    assert {k: v.text for k, v in reqs.items()} == {
        "US-1": "a user story",
        "OT-011": "an observable truth",
        "AC-3.2": "nested id",
    }
    assert {k: v.line for k, v in reqs.items()} == {"US-1": 2, "OT-011": 3, "AC-3.2": 4}
    assert extract_requirement_ids(text) == ["AC-3.2", "OT-011", "US-1"]




def test_inspect_clean_refuses_a_live_defect_and_names_it(run_env):
    """The transition half of AC-008, which the gate does not cover.

    `Foundry-Phase('inspect_clean')` is what actually writes `.inspect-clean` and
    moves the run to F4. A tier read wired into the gate but not the transition
    would let a lead mark a cycle clean over a reachable failure by simply not
    calling the gate — the D-037 shape, one requirement along.
    """
    project_root, fdir = run_env
    _ready_for_the_end_gates(project_root, fdir)
    _defect_ledger(fdir, [_tiered("D-002", "LIVE")])
    (fdir / ".inspect-clean").unlink()

    _arm_ordering_token(fdir)
    result = foundry_mark_phase_complete("inspect_clean", project_root)

    assert result.get("ok") is not True
    assert "D-002" in result["error"]
    assert not (fdir / ".inspect-clean").exists()




# --------------------------------------------------------------------------- #
# ST-011 / FR-044 / AC-035 / OT-028 — the two ride-along ordering fixes
# --------------------------------------------------------------------------- #


def test_gate_followed_directly_by_phase_is_accepted(run_env):
    """OT-028 verbatim (first half): 'Foundry-Gate followed immediately by
    Foundry-Phase is accepted.'

    `foundry_gate` used to unlink `.next-action-called` before any branch ran, so
    the documented sequence Gate -> Phase could not be executed: the gate
    destroyed the marker the transition demands, and the lead's next call was
    refused for having done exactly what start.md told it to. The workaround it
    forced — a Foundry-Next between every gate and its transition — is what made
    Foundry-Next look mandatory there, and FR-044 says it is optional.
    """
    project_root, fdir = run_env
    _write_state(fdir, phase="F2", cycle=1)
    _defect_ledger(fdir, [_tiered("D-001", "LIVE")])
    (fdir / ".tasks-generated").write_text("x\n", encoding="utf-8")

    _arm_ordering_token(fdir)
    gate = foundry_gate("grind", project_root)
    assert gate["passed"] is True, gate
    assert (fdir / ".next-action-called").exists(), (
        "the gate must not consume the ordering token"
    )

    # No Foundry-Next in between. This is the sequence the documentation states.
    transition = foundry_mark_phase_complete("grind_start", project_root)
    assert transition["ok"] is True, transition
    assert transition["phase"] == "F3"




def test_the_phase_transition_is_still_the_one_consumer_of_the_token(run_env):
    """The other half: the handshake is not abolished, it is scoped.

    The token means "a Foundry-Next preceded this transition", and a gate check
    is not a transition — it writes no phase, advances no counter, and on the
    passing path only stamps `.gate-passed`. So the transition still consumes it,
    and a second transition without a fresh Foundry-Next is still refused.
    """
    project_root, fdir = run_env
    _write_state(fdir, phase="F2", cycle=1)
    _defect_ledger(fdir, [_tiered("D-001", "LIVE")])
    (fdir / ".tasks-generated").write_text("x\n", encoding="utf-8")

    _arm_ordering_token(fdir)
    foundry_gate("grind", project_root)
    foundry_mark_phase_complete("grind_start", project_root)

    assert not (fdir / ".next-action-called").exists()
    second = foundry_mark_phase_complete("grind_start", project_root)
    assert second.get("ok") is not True
    assert "Foundry-Next" in second["error"]




# --------------------------------------------------------------------------- #
# GI-006 / CT-014 / AC-036 / ST-010 / OT-025 — DONE requires the report
# --------------------------------------------------------------------------- #


def test_done_without_a_generated_report_is_refused_naming_it(run_env):
    """OT-025 verbatim (first half): 'Foundry-Phase done without a generated
    report is refused naming the report.'"""
    project_root, fdir = run_env
    _write_spec(fdir, ["FR-1"])
    _write_state(fdir, phase="F4", cycle=1)
    _write_verdicts(fdir, [{"requirement_id": "FR-1", "verdict": "VERIFIED"}])
    _defect_ledger(fdir, [])

    _arm_ordering_token(fdir)
    result = foundry_mark_phase_complete("done", project_root)

    assert result.get("ok") is not True
    assert "REPORT.md" in result["error"] and "report.json" in result["error"]
    assert "Foundry-Report" in result["hint"]
    assert json.loads((fdir / "state.json").read_text())["phase"] == "F4"




# --------------------------------------------------------------------------- #
# ST-008 / CT-016 / FR-024 / FR-045 / FR-052 / AC-037 / OT-026 — HALTED
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("token", ["grind_start", "assay_fail"])
def test_the_call_that_would_exceed_max_cycles_halts_rather_than_refusing(
    run_env, token
):
    """AC-037 verbatim: 'With max_cycles persisted as 2, the Foundry-Phase call
    that would start GRIND cycle 3 succeeds and sets state.json to HALTED, the
    report is generated naming every open LIVE and LATENT defect, and the next
    Foundry-Next reports the run halted and issues no dispatch.'

    IT IS A TRANSITION, NOT A REFUSAL, and that is the whole of FR-045. A refusal
    would leave the run sitting where it was with the lead free to call the same
    token again, having produced nothing — a cap that only annoys. Instead the
    run reaches a named terminal state with its open work written down.

    Parametrized over BOTH doors that open a GRIND. They clear the same markers
    and call the same `_update_phase(fdir, "F3")`; a cap wired to one would let a
    run looping back through ASSAY failure run forever while a run looping
    through GRIND halts — and the ASSAY loop is exactly the one --max-cycles
    exists to bound.
    """
    project_root, fdir = run_env
    _write_spec(fdir, ["FR-1"])
    _write_state(fdir, phase="F2", cycle=2, max_cycles=2)
    _write_verdicts(fdir, [{"requirement_id": "FR-1", "verdict": "VERIFIED"}])
    _defect_ledger(fdir, [
        _tiered("D-001", "LIVE"),
        _tiered("D-002", "LATENT", reproduction_attempted="AST sweep finds 0 sites"),
        _tiered("D-003", None),
    ])
    (fdir / ".tasks-generated").write_text("x\n", encoding="utf-8")

    _arm_ordering_token(fdir)
    result = foundry_mark_phase_complete(token, project_root)

    assert result["ok"] is True, result
    assert result["halted"] is True
    assert result["phase"] == RUN_PHASE_HALTED
    assert json.loads((fdir / "state.json").read_text())["phase"] == RUN_PHASE_HALTED

    # FR-045: 'the report is written naming every open LIVE and LATENT defect.'
    assert (fdir / "report.json").exists()
    named = (fdir / "report.json").read_text(encoding="utf-8")
    for did in ("D-001", "D-002", "D-003"):
        assert did in named, did
    assert result["open_live_defects"] == ["D-001"]
    assert result["open_latent_defects"] == ["D-002"]
    assert result["open_unknown_tier_defects"] == ["D-003"]




def test_a_halt_whose_report_failed_says_so_and_names_the_call_that_writes_it(
    run_env, monkeypatch
):
    """FR-045 verbatim: 'state.json phase becomes HALTED, the report is written
    naming every open LIVE and LATENT defect'. D-165.

    `_halt_if_capped` called `_generate_report` and never read its `ok`, so the
    halt ASSERTED a report it had not written and then parked the run on that
    assertion. Driven at the real door: max_cycles 2 at cycle 2, one open LIVE
    and one open LATENT defect, and a deliberately corrupt verdicts.json —
    `ok True`, phase HALTED, message "The report has been generated naming 1
    open LIVE, 0 untiered and 1 open LATENT defect(s)", REPORT.md absent from
    disk, and the nested report result carrying `ok False` with "verdicts.json
    is not valid JSON". Every later call compounded it: `_halted_refusal`
    returned "the report says what", the hint "Read REPORT.md ... and stop",
    and a `report` field holding the absolute path of a file that does not
    exist. HALTED has no exit by design, so the operator was told to read a
    document that was never written and given no reason to regenerate it.

    CT-014 SPECIFIES this failure branch (the unreadable-ledger refusal), so it
    is designed and reachable. The transition still happens — FR-045 makes the
    cap 'not a refusal' — and what changes is that it stops lying about the
    artifact and names the one call that can still write it.
    """
    project_root, fdir = run_env
    _write_spec(fdir, ["FR-1"])
    _write_state(fdir, phase="F2", cycle=2, max_cycles=2)
    _write_verdicts(fdir, [{"requirement_id": "FR-1", "verdict": "VERIFIED"}])
    _defect_ledger(fdir, [
        _tiered("D-001", "LIVE"),
        _tiered("D-002", "LATENT", reproduction_attempted="AST sweep finds 0 sites"),
    ])
    (fdir / ".tasks-generated").write_text("x\n", encoding="utf-8")

    # THE WINDOW THAT REALLY EXISTS. `_artifact_guard` runs at the top of the
    # entry point and the report is generated several frames later, and this
    # tree is SHARED — five castings commit into it at once. So the artifact is
    # corrupted between the guard and the generation, and the refusal asserted
    # below is the REAL generator's (CT-014's unreadable-ledger refusal), not a
    # stub's.
    real_generate = _generate_report

    def _corrupted_mid_transition(pr, run_dir):
        (fdir / "verdicts.json").write_text("{not json", encoding="utf-8")
        return real_generate(pr, run_dir)

    patch_everywhere(monkeypatch, "_generate_report", _corrupted_mid_transition)

    _arm_ordering_token(fdir)
    result = foundry_mark_phase_complete("grind_start", project_root)
    patch_everywhere(monkeypatch, "_generate_report", real_generate)

    # The transition HAPPENED: a run that ran out of cycles has run out of
    # cycles whether or not its ledgers can be rendered.
    assert result["ok"] is True, result
    assert result["halted"] is True
    assert json.loads((fdir / "state.json").read_text())["phase"] == RUN_PHASE_HALTED

    # ...and it does not claim the document it failed to write.
    assert not (fdir / "REPORT.md").exists()
    assert result["report_generated"] is False, result
    assert result["report"]["ok"] is False, result["report"]
    assert "could NOT be generated" in result["message"], result["message"]
    assert "has been generated" not in result["message"], result["message"]
    assert "Foundry-Report" in result["message"], result["message"]
    # The counts are still stated, from the ledger that IS intact.
    assert "1 open LIVE" in result["message"], result["message"]
    assert "1 open LATENT" in result["message"], result["message"]

    # The operator repairs what the error named — the only move the hint asks
    # for — and the refusal every later door returns then names the missing
    # report and the exit that exists, instead of pointing at a file that is
    # not there.
    _write_verdicts(fdir, [{"requirement_id": "FR-1", "verdict": "VERIFIED"}])
    _arm_ordering_token(fdir)
    later = foundry_mark_phase_complete("done", project_root)
    assert later.get("ok") is not True, later
    assert "was NOT written" in later["error"], later["error"]
    assert "the report says what" not in later["error"], later["error"]
    assert "Foundry-Report" in later["hint"], later["hint"]
    assert "defects.json" in later["hint"], later["hint"]
    assert later["report"] is None, later
    assert later["report_generated"] is False, later

    # And that exit really runs on a halted run: Foundry-Report is not a phase
    # transition, so it is reachable from HALTED — which is what makes the hint
    # actionable rather than a second dead end.
    from foundry_mcp import server as foundry_server

    previous = foundry_server._project_root
    try:
        foundry_server._project_root = project_root
        repaired = foundry_server._DISPATCH["Foundry-Report"]({})
    finally:
        foundry_server._project_root = previous
    assert repaired["ok"] is True, repaired
    assert (fdir / "REPORT.md").exists()

    # ...and once it is written the refusal names it again. The FILE is the
    # ground truth; a recorded failure that outlived the repair would be this
    # same defect with the sign flipped.
    with_report = _halted_refusal(fdir, "Foundry-Phase(phase='done')")
    assert with_report["report_generated"] is True, with_report
    assert "the report says what" in with_report["error"], with_report["error"]




def test_a_halt_whose_report_succeeded_still_names_it(run_env):
    """The other side of D-165, so the fix is a discrimination and not a
    deletion: on the ordinary halt the report IS written, and both the
    transition message and every later refusal say so.
    """
    project_root, fdir = run_env
    _write_spec(fdir, ["FR-1"])
    _write_state(fdir, phase="F2", cycle=2, max_cycles=2)
    _write_verdicts(fdir, [{"requirement_id": "FR-1", "verdict": "VERIFIED"}])
    _defect_ledger(fdir, [_tiered("D-001", "LIVE")])
    (fdir / ".tasks-generated").write_text("x\n", encoding="utf-8")

    _arm_ordering_token(fdir)
    result = foundry_mark_phase_complete("grind_start", project_root)

    assert result["ok"] is True, result
    assert result["report_generated"] is True, result
    assert "The report has been generated naming" in result["message"], result
    assert (fdir / "REPORT.md").exists()

    refusal = _halted_refusal(fdir, "Foundry-Phase(phase='done')")
    assert refusal["report_generated"] is True, refusal
    assert "the report says what" in refusal["error"], refusal["error"]
    assert refusal["report"] == str(fdir / "REPORT.md"), refusal




def test_the_cycle_below_the_cap_is_untouched(run_env):
    """The cap bounds the run; it does not shorten it.

    With max_cycles 2 the second GRIND-opening call must still open a GRIND —
    the halt is on the call that would open number THREE. An off-by-one here
    would silently cost every capped run its last cycle.
    """
    project_root, fdir = run_env
    _write_state(fdir, phase="F2", cycle=1, max_cycles=2)
    _defect_ledger(fdir, [_tiered("D-001", "LIVE")])
    (fdir / ".tasks-generated").write_text("x\n", encoding="utf-8")

    _arm_ordering_token(fdir)
    result = foundry_mark_phase_complete("grind_start", project_root)

    assert result["ok"] is True, result
    assert result.get("halted") is not True
    assert result["phase"] == "F3"




def test_max_cycles_zero_is_unbounded(run_env):
    """CT-016 / FR-024: 'Default 0 = unbounded.'

    A run that never passed the flag behaves exactly as every run did before,
    which is what makes the cap safe to ship on by default.
    """
    project_root, fdir = run_env
    _write_state(fdir, phase="F2", cycle=99, max_cycles=0)
    _defect_ledger(fdir, [_tiered("D-001", "LIVE")])
    (fdir / ".tasks-generated").write_text("x\n", encoding="utf-8")

    _arm_ordering_token(fdir)
    result = foundry_mark_phase_complete("grind_start", project_root)

    assert result["ok"] is True, result
    assert result.get("halted") is not True
    assert result["phase"] == "F3"




def test_halted_is_a_named_state_and_not_done(run_env):
    """ST-008: 'HALTED is not DONE.'

    They are both terminal and they mean opposite things — DONE is "every
    requirement verified and every LIVE defect closed", HALTED is "we ran out of
    cycles with work still open". Collapsing them would let a capped run be
    reported as a successful one.
    """
    project_root, fdir = run_env
    _write_spec(fdir, ["FR-1"])
    _write_state(fdir, phase="F2", cycle=2, max_cycles=2)
    _write_verdicts(fdir, [{"requirement_id": "FR-1", "verdict": "VERIFIED"}])
    _defect_ledger(fdir, [_tiered("D-001", "LIVE")])
    (fdir / ".tasks-generated").write_text("x\n", encoding="utf-8")

    _arm_ordering_token(fdir)
    result = foundry_mark_phase_complete("grind_start", project_root)

    assert result["phase"] == RUN_PHASE_HALTED
    assert result["phase"] != "F6"
    state = json.loads((fdir / "state.json").read_text())
    assert state["phase"] == RUN_PHASE_HALTED
    assert state["halted_at_cycle"] == 2
    # fallout CT-004: the member is what a grouper keys on and the text is why
    # THIS run ended. Neither substitutes for the other, so both are asserted.
    assert state["halted_reason"]["reason"] == vocab.HALT_REASON_CAP_REACHED
    assert "max-cycles" in state["halted_reason"]["text"]




# --------------------------------------------------------------------------- #
# D-067 — the ordering token is consumed by a transition that HAPPENED
# --------------------------------------------------------------------------- #


def test_a_refused_transition_leaves_the_ordering_token_in_place(run_env):
    """AC-013 / CT-007 / ST-005. D-067.

    `.next-action-called` was unlinked before ANY branch ran, so a REFUSED
    transition burned it and every refusal whose hint says "fix this and re-call
    Foundry-Phase" named a call that was then refused for a second, different
    reason. Driven on the sweep refusal: repair the log, do exactly what the
    hint says, get "Must call Foundry-Next before phase transitions".

    Driven here on a refusal with no filesystem apparatus — an unknown phase
    token — because the property is about the TOKEN, not about which branch
    refused.
    """
    project_root, fdir = run_env
    _write_state(fdir, phase="F3", cycle=1)
    _arm_ordering_token(fdir)

    refused = foundry_mark_phase_complete("not_a_real_token", project_root)

    assert refused.get("ok") is not True
    assert (fdir / ".next-action-called").exists(), (
        "the token means 'a Foundry-Next preceded this transition', and no "
        "transition happened — the remedy the refusal names has to work"
    )




def test_a_successful_transition_still_consumes_the_token(run_env):
    """The half that must not be lost: the token is a HANDSHAKE, so a
    transition that happened consumes it and the next one needs a fresh
    Foundry-Next."""
    project_root, fdir = run_env
    _write_state(fdir, phase="F0", cycle=0)
    # fallout AC-056: `start_cast` shares the `cast` GATE's evaluation now — the
    # manifest has to exist, parse and carry a casting. It used to be
    # `_update_phase(fdir, "F1")` under the halted guard alone, so a CAST wave
    # could be opened over a manifest the gate would have refused.
    _write_manifest_with_castings(fdir, ["src/handler.py"], no_ui=True)
    _arm_ordering_token(fdir)

    assert foundry_mark_phase_complete("start_cast", project_root)["ok"] is True
    assert not (fdir / ".next-action-called").exists()

    again = foundry_mark_phase_complete("start_cast", project_root)
    assert again.get("ok") is not True
    assert "Foundry-Next" in again["error"]




def test_the_halt_is_read_through_one_reader(run_env):
    """One rule, one implementation. `_halted_state` is the ONLY comparison of
    `state.json`'s phase against RUN_PHASE_HALTED in the transition, gate and
    done-precondition paths — a terminal state each door decides for itself is a
    terminal state each door can decide differently, which is exactly how
    `_halt_if_capped` came to be wired into two doors of ten.
    """
    import inspect as _inspect

    for fn in (_phase_transition, foundry_gate, _done_preconditions,
               foundry_mark_phase_complete):
        source = _inspect.getsource(fn)
        assert "RUN_PHASE_HALTED" not in source, fn.__name__
        assert ("_halted_refusal(" in source) or ("_halted_state(" in source), fn.__name__




def test_the_single_filing_door_no_longer_advertises_a_required_location():
    """D-101: `Foundry-Defect` stops asserting the withdrawn D-089 obligation.

    The location is still EXPECTED and the reason is still stated — the F6
    LATENT backlog is read by a lead with no defects.json to join against
    (D-029) — because guidance the door does not enforce is still worth giving.
    What may not survive is the word REQUIRED, which was a contract.
    """
    description, schema = _tool_schema("Foundry-Defect")

    assert "must carry file_path" not in description
    file_path = schema["properties"]["file_path"]
    assert "REQUIRED" not in file_path["description"], file_path
    assert "Expected on every filing" in file_path["description"]
    assert "D-029" in file_path["description"]
    # The location is still a PAIR, and the symbol half is what survives line
    # drift — unchanged by the withdrawal.
    assert "symbol" in file_path["description"]
    assert "symbol" in schema["properties"]["symbol"]["description"]




def test_the_batch_filing_door_withdraws_it_on_the_same_terms():
    """The SAME withdrawal on the door a whole INSPECT stream files through.
    Two filing doors that disagree about what a defect must carry would be a
    worse bug than any either could have alone, and this is the door where a
    wrongly-advertised requirement costs a whole batch.
    """
    description, schema = _tool_schema("Foundry-Sync")

    assert "must carry `file`" not in description
    item = schema["properties"]["findings"]["items"]["properties"]
    assert "REQUIRED" not in item["file"]["description"], item["file"]
    assert "Expected on every finding" in item["file"]["description"]
    assert "D-029" in item["file"]["description"]




def test_the_defect_door_renders_a_retier_differently_from_a_fresh_append():
    """D-100: the two outcomes rendered BYTE-IDENTICALLY.

    Driven with ANSI stripped: `foundry_add_defect` against a ledger holding an
    open untiered D-001 returned retiered=1, retiered_ids=['D-001'] and rendered
    "F O U N D R Y  Defect: D-001 / Total: 1  Open: 1"; the identical call
    against an EMPTY ledger returned retiered=0, retiered_ids=[] and rendered
    the same two lines. The lead could not tell which had happened.
    """
    retiered = _plain(format_result("Foundry-Defect", {
        "defect_id": "D-001", "cycle": 2, "type": "UNWIRED",
        "total_defects": 1, "open_defects": 1,
        "retiered": 1, "retiered_ids": ["D-001"], "tier": "LATENT",
    }))
    appended = _plain(format_result("Foundry-Defect", {
        "defect_id": "D-001", "cycle": 2, "type": "UNWIRED",
        "total_defects": 1, "open_defects": 1,
        "retiered": 0, "retiered_ids": [],
    }))

    assert retiered != appended, "a re-tier and a fresh append still read alike"
    assert "re-tiered" in retiered
    assert "D-001" in retiered
    assert "LATENT" in retiered
    assert "re-tiered" not in appended




def test_the_batch_door_renders_a_retier_beside_added_and_reopened():
    """D-100's other half: a batch that reclassified an open blocking record in
    place rendered "Added: +0 / Reopened: 0 / Total open: 1", which reads as
    "the batch recorded nothing"."""
    rendered = _plain(format_result("Foundry-Sync", {
        "ok": True, "cycle": 2, "added": 0, "reopened": 0, "total_open": 1,
        "regressions": [], "retiered": 1, "retiered_ids": ["D-001"],
    }))
    quiet = _plain(format_result("Foundry-Sync", {
        "ok": True, "cycle": 2, "added": 0, "reopened": 0, "total_open": 1,
        "regressions": [], "retiered": 0, "retiered_ids": [],
    }))

    assert "Re-tiered:  1" in rendered, rendered
    assert "re-tiered" in rendered and "D-001" in rendered
    assert rendered != quiet
    assert "Re-tiered:  0" in quiet, quiet




def _prose_units(path: Path, text: str) -> list[tuple[int, str]]:
    """The units a retired-mechanism claim is judged in, per file type."""
    if path.suffix == ".py":
        return _python_units(text)
    return _markdown_units(text)




def _excluded_line_spans(text: str) -> list[range]:
    """Line ranges a file has explicitly marked as registry fixtures."""
    spans: list[range] = []
    open_at: int | None = None
    for row, line in enumerate(text.splitlines(), start=1):
        if _REGISTRY_BEGIN in line:
            open_at = row
        elif _REGISTRY_END in line and open_at is not None:
            spans.append(range(open_at, row + 1))
            open_at = None
    if open_at is not None:
        spans.append(range(open_at, len(text.splitlines()) + 2))
    return spans




def test_no_filed_surface_names_a_retired_mechanism_as_though_it_were_live():
    """The escalated class `stale-prose-survives-beside-new-prose`, as a rule.

    Four consecutive cycles, seven filings, four castings' files. Every one is
    the same shape: a sentence describing a mechanism the run replaced, sitting
    in the present tense beside the mechanism that replaced it, where the next
    reader takes it for documentation.

    The rule this pins is that a retired mechanism may be named only as
    HISTORY. Concretely: if a PROSE UNIT — a whole docstring, or a whole
    contiguous run of `#` comment lines — contains one of the retired spellings
    in `_RETIRED_MECHANISMS`, that same unit must carry a retirement marker: a
    `D-NNN` citation, or a past-tense phrase such as "used to", "no longer",
    "D-098 deleted". Every historical mention in the tree today already
    satisfies this, because the house style ("Every non-obvious decision
    carries its failure history in a comment") was already asking for it. Only
    the live assertions do not, and those were the seven filings.

    WHY A WHOLE COMMENT AND NOT A PARAGRAPH. The house style puts the defect id
    on a comment's heading line and the retired behaviour several paragraphs
    below it inside the same docstring; a paragraph-sized unit reports a
    correctly-written failure-history comment as a violation. Driven while
    writing this: the paragraph unit flagged
    `test_sync_denylist_hit_fires_the_tripwire_end_to_end`, whose docstring
    opens "D-036 / AC-002 / FR-002" and describes the retired branch three
    lines later.

    WHY A LINE ASSERTING ABSENCE IS EXEMPT. `assert "never from REPORT.md" not
    in source` is this class's own pin, one defect earlier (D-050). A rule that
    cannot tell "the code claims X" from "the suite checks X is gone" would
    make every pin against stale prose itself a violation.

    WHY THE SEVEN SURFACES AND NOT ONE CASTING'S FILES. The class is
    cross-casting — the seven filings land in four castings — so a pin scoped
    to the files one casting owns would have caught one of them and let the
    class recur through the other three. This test READS the other castings'
    files and edits none of them.

    WHEN THIS FAILS, the fix is not to add a marker word. It is to read the
    named block and decide which is true: the mechanism is live, and the row
    in `_RETIRED_MECHANISMS` is wrong, or the mechanism is gone and the prose
    must say so.
    """
    root = _plugin_root()
    violations: list[str] = []

    for rel, filed_as in sorted(_STALE_PROSE_SURFACES.items()):
        path = root / rel
        assert path.exists(), f"filed surface missing from the tree: {rel}"
        text = path.read_text(encoding="utf-8")
        excluded = _excluded_line_spans(text)
        for start, block in _prose_units(path, text):
            if any(start in span for span in excluded):
                continue
            if _RETIREMENT_MARKERS.search(block):
                continue
            if _ABSENCE_ASSERTION.search(block):
                continue
            for name, pattern, why in _RETIRED_MECHANISMS:
                match = pattern.search(block)
                if match is None:
                    continue
                line = start + block[: match.start()].count("\n")
                violations.append(
                    f"{rel}:{line} names {name!r} with nothing marking it as "
                    f"history — {why} (this surface was filed as {filed_as})\n"
                    f"    {match.group(0)!r}"
                )

    assert not violations, (
        "a retired mechanism is described as though it were live:\n"
        + "\n".join(violations)
    )




def test_the_retired_mechanism_pin_actually_fires():
    """The pin's own falsifiability.

    A scanner whose patterns never match anything passes forever and proves
    nothing — and this class has already survived two structural packets, so a
    pin that cannot be shown to fire is not a mechanism, it is a comment. Each
    retired spelling is driven through the block scan in both directions: bare,
    it is a violation; carrying a retirement marker, it is not.
    """
    for name, pattern, _why in _RETIRED_MECHANISMS:
        # retired-mechanism-registry: BEGIN (this span is excluded from its own scan)
        sample = {
            "the pre-width required-stream roster":
                "All streams (trace, prove, sight, test) must complete.",
            "the cycle-count context estimate":
                "If Foundry-Next shows estimated_usage: high, save state.",
            "the foundry_mark_inspect_clean call":
                "Call foundry_mark_inspect_clean when clean.",
            "the JSON-only report_status read":
                "report_status reads the JSON precisely so prose is safe.",
            "the blocking-count final_gate proxy":
                "One OPEN LIVE defect, enough to keep final_gate from firing.",
            "the sync auto-demotion branch":
                "foundry_sync_defects's auto-demotion branch faces the mirror.",
            "the per-door gate exception on the tier axis":
                "the axis decides only which GATE a still-open instance "
                "blocks (ASSAY blocks on either; TEMPER, NYQUIST and DONE "
                "block on LIVE alone).",
            "the two-ending run":
                "Zero approval gates. The foundry runs until F6 DONE or an "
                "error stops it.",
        }[name]
        # retired-mechanism-registry: END

        assert pattern.search(sample), f"{name}: the pattern matches nothing"
        assert not _RETIREMENT_MARKERS.search(sample), (
            f"{name}: the bare sample must read as a live assertion"
        )
        marked = f"D-999 — this is what it used to say: {sample}"
        assert _RETIREMENT_MARKERS.search(marked), (
            f"{name}: a marked block must be exempt"
        )




def test_the_registry_exclusion_is_the_only_one():
    """The escape hatch, pinned shut.

    `_excluded_line_spans` lets a file mark a span as registry fixtures, and
    the retired-mechanism scan skips it. That is necessary — the registry and
    its falsifiability samples name every retired spelling on purpose — and it
    is exactly the shape that would let a live assertion hide behind a comment
    somebody pasted from here.

    So the hatch is enumerable and enumerated: the ONLY surface allowed to open
    one is this test file, which owns the registry. Any other filed surface
    declaring a span fails here, by name, whatever the scan says about it.
    """
    root = _plugin_root()
    owner = "mcp-server/tests/test_orchestrator_gates.py"

    for rel in sorted(_STALE_PROSE_SURFACES):
        spans = _excluded_line_spans((root / rel).read_text(encoding="utf-8"))
        if rel == owner:
            assert spans, "the registry span sentinel has gone missing"
            continue
        assert not spans, (
            f"{rel} declares a retired-mechanism exclusion span. Only the file "
            "that owns the registry may; a span anywhere else hides a live "
            "assertion from the scan."
        )




def test_the_terminal_crossing_table_names_every_branch_that_sweeps(run_env):
    """D-159's floor: the guard above is only as good as its roster.

    The retired guard drove two doors and read as exhaustive. So the roster is
    checked against the source: every branch of `_phase_transition` that calls
    the terminal evidence rung must appear in `_TERMINAL_EVIDENCE_CROSSINGS`,
    and a fourth crossing added later fails HERE rather than passing silently.
    """
    import ast
    import inspect
    import textwrap

    tree = ast.parse(textwrap.dedent(inspect.getsource(_phase_transition)))
    sweeping: set[str] = set()
    for branch in ast.walk(tree):
        if not isinstance(branch, ast.If):
            continue
        tokens = {
            node.comparators[0].value
            for node in ast.walk(branch.test)
            if isinstance(node, ast.Compare)
            and isinstance(node.left, ast.Name)
            and node.left.id == "phase"
            and isinstance(node.comparators[0], ast.Constant)
            and isinstance(node.comparators[0].value, str)
        }
        if not tokens:
            continue
        calls = {
            node.func.id
            for node in ast.walk(ast.Module(body=branch.body, type_ignores=[]))
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        # fallout AC-056 — THE RUNG MOVED INTO THE SHARED ROUTINES, SO THE SCAN
        # FOLLOWS THE CALL ONE HOP.
        #
        # A branch no longer sweeps inline: it calls its
        # `_<token>_preconditions`, and the rung lives there — which is what
        # gives `Foundry-Gate('nyquist')` the sweep it never had. Reading only
        # the branch's own body would report one crossing of three and call the
        # roster wrong, when what actually happened is that all three now take
        # the rung through one function each.
        for callee in sorted(calls):
            if callee.endswith("_preconditions") and orchestration_has(callee):
                calls |= {
                    node.func.id
                    for node in ast.walk(
                        ast.parse(textwrap.dedent(inspect.getsource(getattr(owning_module(callee), callee))))
                    )
                    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                }
        if {"_terminal_evidence_state", "_done_preconditions"} & calls:
            sweeping |= tokens

    named = {token for token, _phase, _door in _TERMINAL_EVIDENCE_CROSSINGS}
    assert sweeping == named, (
        f"terminal crossings that take the evidence rung: {sorted(sweeping)}; "
        f"crossings the guard drives: {sorted(named)}"
    )




@pytest.mark.parametrize(
    "tool, arguments, bad_field",
    [
        (
            "Foundry-Defect",
            {"cycle": 1, "source": "bogus", "defect_type": "UNWIRED",
             "tier": "LATENT", "defect_class": "K",
             "reproduction_attempted": "drove the endpoint and saw no check",
             "description": _SECURITY_CLAIM},
            "source",
        ),
        (
            "Foundry-Defect",
            {"cycle": 1, "source": "prove", "defect_type": "NOTATYPE",
             "tier": "LATENT", "defect_class": "K",
             "reproduction_attempted": "drove the endpoint and saw no check",
             "description": _SECURITY_CLAIM},
            "defect_type",
        ),
        (
            "Foundry-Sync",
            {"cycle": 1, "findings": [
                {"description": _SECURITY_CLAIM, "source": "bogus",
                 "tier": "LATENT", "class": "K",
                 "reproduction_attempted": "drove the endpoint and saw no check"},
            ]},
            "source",
        ),
    ],
)
def test_a_schema_refused_filing_still_fires_the_security_tripwire(
    run_env, tool, arguments, bad_field
):
    """AC-007 verbatim: a LATENT filing matching the security-property
    predicate 'is refused ... naming the denylist class
    SECURITY_PROPERTY_CLAIM', and CT-003's audit record captures the attempt.

    The filing is malformed in an UNRELATED field, so the schema refuses it
    before dispatch. The refusal is still the schema's — the caller is told
    which field it got wrong, which is the answer it needs — and the audit
    record is written anyway, because the attempt is what the record is for.
    """
    project_root, fdir = run_env
    _write_state(fdir, phase="F2", cycle=1)
    _defect_ledger(fdir, [])

    import foundry_mcp.server as srv

    previous = srv._project_root
    try:
        srv._project_root = project_root
        before = len(_tripwire_classes(fdir))
        out = _drive_mcp(tool, arguments)
    finally:
        srv._project_root = previous

    # The caller still gets the schema refusal, naming the field it got wrong.
    assert bad_field in out, out
    # ...and the audit record exists, under the class the denylist refuses on.
    classes = _tripwire_classes(fdir)
    assert len(classes) == before + 1, (classes, out)
    assert classes[-1] == "SECURITY_PROPERTY_CLAIM", (classes, out)




def test_an_ordinary_schema_refusal_writes_no_tripwire(run_env):
    """The other side: the audit ledger is not a log of every bad argument.

    A filing that is malformed and carries NO security-property claim writes
    nothing. An audit control that fires on everything is one nobody reads,
    and `observations.json.tripwire` is queried by class.
    """
    project_root, fdir = run_env
    _write_state(fdir, phase="F2", cycle=1)
    _defect_ledger(fdir, [])

    import foundry_mcp.server as srv

    previous = srv._project_root
    try:
        srv._project_root = project_root
        _drive_mcp("Foundry-Defect", {
            "cycle": 1, "source": "bogus", "defect_type": "UNWIRED",
            "tier": "LATENT", "defect_class": "K",
            "reproduction_attempted": "read the renderer end to end",
            "description": "the backlog heading says every row carries a file",
        })
    finally:
        srv._project_root = previous

    assert _tripwire_classes(fdir) == []




def _single_door_arguments(declared_kind: str | None) -> dict:
    """`_ONE_FINDING` under Foundry-Defect's argument names."""
    args = {
        "cycle": 1,
        "source": _ONE_FINDING["source"],
        "defect_type": _ONE_FINDING["type"],
        "description": _ONE_FINDING["description"],
        "spec_ref": _ONE_FINDING["spec_ref"],
        "symbol": _ONE_FINDING["symbol"],
        "file_path": _ONE_FINDING["file"],
        "tier": _ONE_FINDING["tier"],
        "defect_class": _ONE_FINDING["class"],
        "reproduction_attempted": _ONE_FINDING["reproduction_attempted"],
    }
    if declared_kind is not None:
        args["target_kind"] = declared_kind
    return args




def _batch_door_arguments(declared_kind: str | None) -> dict:
    """The same finding under Foundry-Sync's `findings` item names."""
    finding = dict(_ONE_FINDING)
    if declared_kind is not None:
        finding["target_kind"] = declared_kind
    return {"cycle": 1, "findings": [finding]}




@pytest.mark.parametrize("declared_kind", ["code", None])
def test_both_filing_doors_persist_one_record_shape_over_the_wire(
    run_env, declared_kind
):
    """CT-001 and CT-002 name ONE surface: 'Foundry-Defect and Foundry-Sync'.
    US-002 verbatim: 'As a verification stream, I want to file each defect as
    LIVE or LATENT from the evidence I actually drove, so that a reachable
    failure blocks the run exactly as today while a scan-derivation gap is
    tracked without re-litigation.'

    D-192 — TWO LITERALS, ONE CLAIM THAT THEY AGREE, AND THEY DID NOT.
    ------------------------------------------------------------------
    The batch door's record literal carried a comment asserting "the batch door
    writes the SAME record shape as the single door, field for field". Driven
    at HEAD 3584f55 with one finding carrying `target_kind='code'` through both
    doors: Foundry-Defect persisted D-001 WITH `target_kind='code'` and
    Foundry-Sync persisted D-002 with no `target_kind` key at all. Enforcement
    was identical at both doors and that is not what was lost — the durable
    RECORD was: a Sync-filed defect carried no evidence the declaration had
    ever been made, so it could not be audited for it while the same finding
    filed one door over could.

    The fix is `foundry_orchestrator.new_defect_record`, one shape definition
    the batch door builds from. THIS TEST is what makes it stay one: a comment
    asserting two literals agree fails silently, and the assertion below fails
    loudly, the first time either door persists a field the other does not.

    OVER THE WIRE, not through the Python functions. The MCP SDK validates
    arguments against the advertised `inputSchema` before dispatch, and a
    schema that never advertised `target_kind` on one of the two doors would
    lose the field just as thoroughly as a record literal that dropped it — so
    the transport a stream actually files through is the one that has to agree.

    Parametrised over a DECLARED kind and an omitted one, because absence is a
    load-bearing value here: `vocab.is_non_comment` reads a present-and-not-
    "comment" `target_kind`, so a door that helpfully wrote `""` where the
    caller declared nothing would be inventing a declaration, and every
    pre-change archive read would change shape under it.
    """
    project_root, fdir = run_env
    _write_state(fdir, phase="F2", cycle=1)
    _defect_ledger(fdir, [])

    import foundry_mcp.server as srv

    previous = srv._project_root
    try:
        srv._project_root = project_root
        single_out = _drive_mcp("Foundry-Defect", _single_door_arguments(declared_kind))
        batch_out = _drive_mcp("Foundry-Sync", _batch_door_arguments(declared_kind))
    finally:
        srv._project_root = previous

    stored = json.loads((fdir / "defects.json").read_text(encoding="utf-8"))["defects"]
    assert len(stored) == 2, (stored, single_out, batch_out)
    single, batch = stored
    assert single["id"] == "D-001" and batch["id"] == "D-002", stored

    # The keys themselves, first: a field one door writes and the other omits is
    # exactly D-192, and it reads as an absent key rather than a wrong value.
    assert set(single) == set(batch), {
        "only_single_door": sorted(set(single) - set(batch)),
        "only_batch_door": sorted(set(batch) - set(single)),
    }

    # Then the values. `id` is minted per record and `created_at` is stamped at
    # write time, so those two are the only legitimate difference between one
    # finding filed twice.
    differing = {k for k in single if single[k] != batch[k]}
    assert differing <= {"id", "created_at"}, {
        k: (single[k], batch[k]) for k in sorted(differing - {"id", "created_at"})
    }

    # And the field D-192 was actually about, named rather than merely covered
    # by the set comparison above.
    if declared_kind is None:
        assert "target_kind" not in batch, batch
        assert "target_kind" not in single, single
    else:
        assert batch["target_kind"] == declared_kind, batch
        assert single["target_kind"] == declared_kind, single




def test_the_argument_refusal_block_argues_from_a_record_that_supports_it():
    """D-156, the fifth filing of `stale-prose-survives-beside-new-prose`.

    The house style's whole return on a failure-history comment is that the
    next reader can FOLLOW the cite. `server.py`'s argument-refusal header
    argued its design point — one boundary check rather than thirty handlers
    each remembering to re-check their own enums — from D-127, which is the
    waiting-notice and liveness defect (`_waiting_on_agents` gating on the team
    scan rather than on the liveness roster). A reader who followed it reached
    a record that does not hold up the claim beside it, which costs the comment
    exactly the thing it is written for.

    The duplication shape IS on record, twice: D-098 (`two-surfaces-of-one-rule-
    disagree` — the comment-prose rung sat at a different point in each filing
    door's pipeline, so the two doors disagreed about what a defect is) and
    D-077 (FR-051's untiered exit implemented at ONE of the two doors). The
    block now names those, and says why the cite moved.

    Asserted as a property of the FILE rather than as a comment about it, so a
    future edit that restores the old attribution fails here.
    """
    block = (
        _plugin_root() / "mcp-server/src/foundry_mcp/server.py"
    ).read_text(encoding="utf-8")
    header = block[block.index("# D-042"):block.index("_SCHEMAS: dict[str, dict]")]

    assert "D-098" in header and "D-077" in header, header[-1200:]
    assert "D-127 already cost this server once" not in header, header[-1200:]
    # ...and the correction itself is history, not a silent rewrite: the block
    # says which cite was there and why it did not hold.
    assert "D-156" in header, header[-1200:]




def test_the_clean_f2_arm_names_the_crossing_the_server_accepts_from_f2(run_env):
    """US-001 / ST-001 / ST-010: the exit is mechanical, so the prose that
    sends a lead to make it has to name a call the server takes.

    The recorded width here is FULL / final_gate, which is what a clean cycle
    that reached ASSAY looks like. Driven at cycle 8:
    `Foundry-Phase('inspect_start')` from that F2 is refused and the counter
    does not move; the sequence that closes one clean cycle is `grind_start`
    then `inspect_start`. Both halves are asserted — the arm names the working
    sequence, and the call it used to name is shown to be the refusal it is.
    """
    project_root, fdir = run_env
    _escalated_fixture(fdir, open_instances=False)
    _clean_f2(project_root, fdir)

    nxt = foundry_next_action(project_root)
    instructions = nxt["instructions"]

    assert nxt["action"] == "transition_to_assay", nxt
    assert "grind_start" in instructions, instructions
    assert "REFUSED" in instructions, instructions

    # ...and the call the arm used to name really is refused from here.
    _arm_ordering_token(fdir)
    refused = foundry_mark_phase_complete("inspect_start", project_root)
    assert refused.get("ok") is not True, refused
    assert "nothing to widen" in refused["error"], refused




def test_an_ordinary_halt_names_the_report_it_wrote(run_env):
    """The unremarkable path, unchanged: the report generates, and the notice
    says so and hands over the path."""
    project_root, fdir = run_env
    _write_spec(fdir, ["FR-1"])
    _write_state(fdir, phase="F2", cycle=2, max_cycles=2)
    _write_verdicts(fdir, [{"requirement_id": "FR-1", "verdict": "VERIFIED"}])
    _defect_ledger(fdir, [_tiered("D-001", "LATENT",
                                  reproduction_attempted="AST sweep finds 0 sites")])
    (fdir / ".tasks-generated").write_text("x\n", encoding="utf-8")

    _arm_ordering_token(fdir)
    assert foundry_mark_phase_complete("grind_start", project_root)["halted"] is True
    assert (fdir / "REPORT.md").exists()

    action = _compute_next_action(project_root)

    assert action["action"] == "halted"
    assert "The report has been generated at REPORT.md" in action["instructions"]
    assert action["details"]["report"] == str(fdir / "REPORT.md")
    assert action["details"]["report_generated"] is True
    assert action["details"]["report_error"] == ""




# --------------------------------------------------------------------------- #
# D-176 — A BATCH REFUSAL NAMES THE OFFENDING FINDING AND THE ONE FIELD IT LACKS
#
# OT-029 verbatim: "Foundry-Sync with one finding lacking class refuses the
# whole batch naming the finding."
#
# `_argument_refusal`'s `required` branch read `for prop in err.validator_value:
# if prop not in (arguments or {})`. On a batch door that is the wrong object on
# both sides — `validator_value` is the ITEM schema's whole required list and
# `arguments` is the TOP-LEVEL dict, which holds only `cycle` and `findings` —
# so every member came back absent. Driven at the real door with a two-item
# batch whose second finding carried description, source, tier, file, symbol and
# type and omitted only `class`: "unusable argument(s): description — required,
# and absent; source — required, and absent; tier — required, and absent; class
# — required, and absent." Three of those four were supplied, and the message
# was byte-identical whether the offender was the only finding, the second of
# two or the third of three.
#
# Both facts were already on the error and were discarded by the `continue`:
# `err.absolute_path` had resolved to ['findings', 1] and `err.message` read
# "'class' is a required property". The batch IS refused whole and nothing is
# persisted — only the diagnostic was wrong, and it sent a stream to re-add
# fields it had already sent.
# --------------------------------------------------------------------------- #


def _sync_finding(**overrides) -> dict:
    finding = {
        "description": "the handler never calls the store it documents",
        "source": "prove",
        "tier": "LIVE",
        "file": "src/api/handler.py",
        "symbol": "handle",
        "type": "UNWIRED",
        "class": "UNWIRED_SURFACE",
    }
    finding.update(overrides)
    return finding




@pytest.mark.parametrize("offender", [0, 1, 2])
def test_a_batch_refusal_names_the_offending_finding_by_index(offender):
    """OT-029 verbatim: 'Foundry-Sync with one finding lacking class refuses the
    whole batch naming the finding.' CT-002 / AC-010 / FR-007.

    Parametrised over the position because the old message was byte-identical
    at every one of them: naming the index is the whole of "naming the
    finding", and a message that cannot vary with the offender's position
    cannot be naming it.
    """
    import asyncio

    from foundry_mcp import server as srv

    batch = [_sync_finding(symbol=f"h{i}") for i in range(3)]
    bad = dict(batch[offender])
    bad.pop("class")
    batch[offender] = bad

    schema = asyncio.run(srv._tool_schema("Foundry-Sync"))
    refusal = srv._argument_refusal(
        "Foundry-Sync", schema, {"cycle": 1, "findings": batch}
    )

    assert refusal is not None
    assert refusal["missing_fields"] == [f"findings[{offender}].class"], refusal
    assert f"findings[{offender}].class" in refusal["error"], refusal["error"]
    # The fields that WERE supplied are not named. This is the half that sent a
    # stream to re-send what it had already sent.
    for supplied in ("description", "source", "tier", "file", "symbol", "type"):
        assert f"{supplied} — required" not in refusal["error"], (
            supplied, refusal["error"]
        )




def test_the_batch_refusal_index_is_the_spelling_the_handler_uses():
    """One address, one spelling. `foundry_sync_defects` names an offending
    batch member `findings[N]` and `schemas/findings.py` renders its own errors
    the same way — `test_a_non_dict_finding_refuses_the_batch_naming_the_index`
    pins that substring against the handler. This rung refuses the SAME batch
    about the SAME member one frame earlier, so it says the same thing; the
    previous `".".join(...)` rendered `findings.1.class`, which reads as a
    nested key rather than an index and is a second spelling of one address.
    """
    from foundry_mcp import server as srv

    assert srv._instance_label(["findings", 1]) == "findings[1]"
    assert srv._instance_label(["findings", 1, "tier"]) == "findings[1].tier"
    assert srv._instance_label([]) == ""
    assert srv._instance_label(["tier"]) == "tier"




def test_a_top_level_required_failure_is_named_exactly_as_before():
    """The single-door spelling is untouched. At the top level `absolute_path`
    is empty and `err.instance` IS `arguments`, so a bare missing field stays a
    bare name — no index, no prefix, and every genuinely absent one still named
    in the one refusal.
    """
    import asyncio

    from foundry_mcp import server as srv

    schema = asyncio.run(srv._tool_schema("Foundry-Defect"))
    refusal = srv._argument_refusal(
        "Foundry-Defect", schema, {"cycle": 1, "source": "prove"}
    )

    assert refusal is not None
    assert set(refusal["missing_fields"]) == {
        "defect_type", "description", "tier", "defect_class",
    }, refusal["missing_fields"]
    assert not any("[" in field for field in refusal["missing_fields"]), refusal




def test_a_nested_finding_missing_two_fields_names_both_on_that_finding():
    """The house rule at this rung: where several fields failed at once, name
    every one of them in a single refusal. jsonschema yields one `required`
    error per missing property, so membership is tested against the instance
    the rule was applied to and both come back — on the right finding.
    """
    import asyncio

    from foundry_mcp import server as srv

    bad = _sync_finding()
    bad.pop("class")
    bad.pop("tier")

    schema = asyncio.run(srv._tool_schema("Foundry-Sync"))
    refusal = srv._argument_refusal(
        "Foundry-Sync", schema, {"cycle": 1, "findings": [_sync_finding(), bad]}
    )

    assert set(refusal["missing_fields"]) == {
        "findings[1].class", "findings[1].tier",
    }, refusal["missing_fields"]




def test_both_f6_transitions_publish_the_ranked_refusals(run_env):
    """`_done_preconditions` is ONE evaluation with four callers (D-037), so
    what the two GATES publish and what the two TRANSITIONS publish is the same
    list. A refusal a lead can read at the gate and not at the call is the
    drift that helper exists to prevent."""
    project_root, fdir = run_env
    _d190_state(project_root, fdir)

    _arm_ordering_token(fdir)
    gate = foundry_gate("done", project_root)

    # Both tokens are made from F5.5 on a --nyquist run: `done`'s accepted
    # source is the LAST phase the run's own flags make terminal (D-164).
    for token in ("done", "nyquist_done"):
        _at_the_phase_for(fdir, token)
        _arm_ordering_token(fdir)
        result = foundry_mark_phase_complete(token, project_root)
        assert result.get("ok") is not True, (token, result)
        assert result["refusals"] == gate["refusals"], (token, result)
        assert "D-900" in result["error"], (token, result)




@pytest.mark.parametrize(
    "token,phase,flags",
    [
        ("done", "F4", {}),
        ("nyquist_done", "F5.5", {"temper": True, "nyquist": True}),
    ],
)
def test_the_f6_seal_carries_the_prose_the_lead_appended(run_env, token, phase, flags):
    """GI-006 verbatim: 'The lead may append prose but cannot omit a section;
    `Foundry-Phase('done')` refuses if the report is absent.'

    D-224. That is TWO clauses, and the D-218 seal enforced the second by
    destroying the first. Driven at the real door: the report was generated, a
    `## Lead notes` section carrying a sentinel was appended below the generated
    sections, `report_status` still read present True, and
    `Foundry-Phase('done')` returned ok True and phase F6 with the sentinel and
    its heading gone from REPORT.md. `clear_active_run()` runs immediately
    after, so the run was archived with the prose gone and nothing in the
    response saying it had been discarded.

    BOTH CLAUSES ARE ASSERTED HERE, because a fix that traded the other way
    would pass an assertion on either one alone: the appended prose is still on
    disk, AND the document still carries every generated section and the
    LATENT backlog the regeneration exists to refresh (D-218 / FR-001).

    Parametrized over BOTH F6 doors for D-043/D-044's reason — they are two
    transitions into the same state and they drifted the first time.
    """
    from foundry_mcp.tools.foundry_report import report_status

    project_root, fdir = run_env
    _write_spec(fdir, ["FR-1"])
    _write_state(fdir, phase=phase, cycle=2, **flags)
    _write_verdicts(fdir, [{"requirement_id": "FR-1", "verdict": "VERIFIED"}])
    _defect_ledger(fdir, [])
    _generate_report(project_root, fdir)

    _append_lead_prose(fdir)
    # The prose does not make a section look missing — the property
    # test_report.py already pins, restated here as the precondition this test
    # depends on rather than assumed.
    assert report_status(fdir)["present"] is True

    # ...and the ledger moves under the document, which is why the transition
    # regenerates at all.
    _defect_ledger(fdir, [_latent("D-224")])

    _arm_ordering_token(fdir)
    result = foundry_mark_phase_complete(token, project_root)

    assert result["ok"] is True, result
    assert result["phase"] == "F6"
    assert result["report_generated"] is True, result

    text = _assert_lead_prose_survived(fdir)
    # GI-006's second clause, unweakened: every section is still findable and
    # the regeneration really happened.
    status = report_status(fdir)
    assert status["present"] is True, status
    assert status["missing_sections"] == [], status
    assert "D-224" in text, text[-2000:]
    assert json.loads(
        (fdir / "report.json").read_text(encoding="utf-8")
    )["latent_backlog"]["open_count"] == 1

    # NFR-005: the transition SAYS what it moved. The failure was silent on both
    # halves — the prose went and the message did not mention it.
    assert result["lead_prose_lines"] > 0, result
    assert not result["lead_prose_error"], result
    assert "GI-006" in result["message"], result["message"]




def test_a_section_the_lead_deleted_never_reaches_the_seal_at_all(run_env):
    """GI-006's other clause: 'cannot omit a section'.

    The two clauses are enforced in different places and this pins which. The
    preservation added for D-224 does NOT soften the omission rule, because the
    omission rule fires first: `_done_preconditions` reads `report_status` and
    refuses the whole transition, so a lead who deleted a heading never reaches
    the regeneration and their remaining prose is not touched either. A fix that
    had instead taught the seal to quietly restore the heading would have turned
    a named refusal into a silent repair.
    """
    from foundry_mcp.tools.foundry_report import report_status

    project_root, fdir = run_env
    _write_spec(fdir, ["FR-1"])
    _write_state(fdir, phase="F4", cycle=2)
    _write_verdicts(fdir, [{"requirement_id": "FR-1", "verdict": "VERIFIED"}])
    _defect_ledger(fdir, [])
    _generate_report(project_root, fdir)
    _append_lead_prose(fdir)

    path = fdir / "REPORT.md"
    lines = path.read_text(encoding="utf-8").splitlines()
    headings = [i for i, line in enumerate(lines) if line.strip().startswith("## ")]
    generated = [i for i in headings if lines[i].strip() != _LEAD_HEADING]
    del lines[generated[-1]]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    missing = report_status(fdir)["missing_sections"]
    assert missing != []

    before = path.read_text(encoding="utf-8")
    _arm_ordering_token(fdir)
    result = foundry_mark_phase_complete("done", project_root)

    assert "ok" not in result, result
    assert missing[0] in result["error"] or missing[0] in result["hint"], result
    assert json.loads((fdir / "state.json").read_text())["phase"] == "F4"
    # Refused BEFORE the regeneration, so the document is exactly as the lead
    # left it — prose included.
    assert path.read_text(encoding="utf-8") == before
    assert _LEAD_SENTINEL in before




def test_a_zero_fraction_cap_halts_the_run_the_door_promised_to_bound(run_env):
    """CT-016 verbatim: 'max_cycles from the --max-cycles flag, default 0 |
    persisted in state.json; the Foundry-Phase call that would exceed it
    succeeds, sets phase HALTED and generates the report as part of that
    transition'.

    D-225. The door and the deciding read disagreed about what an integer is.
    `Foundry-Init` advertises `{"type": "integer"}` and `_argument_refusal`
    validates with Draft202012Validator, in which a zero-fraction float IS an
    integer — so `2.0` was ACCEPTED and persisted, while `_halt_if_capped`
    guarded with `isinstance(max_cycles, int)`, which is False for `2.0`.
    Driven: cap 2.0 persisted, counter at 99, `Foundry-Phase('grind_start')`
    returned ok True and phase F3 — an operator who asked for a cap of 2 opened
    GRIND cycle 100 and was told nothing.

    Both halves are asserted, because a fix to either alone leaves the two
    surfaces disagreeing: the door still accepts the value, and the halt now
    acts on it.
    """
    from foundry_mcp import server as srv

    assert srv._argument_refusal(
        "Foundry-Init", _init_schema(), {"max_cycles": 2.0}
    ) is None

    project_root, fdir = run_env
    _write_spec(fdir, ["FR-1"])
    _write_state(fdir, phase="F2", cycle=2, max_cycles=2.0)
    _write_verdicts(fdir, [{"requirement_id": "FR-1", "verdict": "VERIFIED"}])
    _defect_ledger(fdir, [_tiered("D-001", "LIVE")])
    (fdir / ".tasks-generated").write_text("x\n", encoding="utf-8")

    _arm_ordering_token(fdir)
    result = foundry_mark_phase_complete("grind_start", project_root)

    assert result["ok"] is True, result
    assert result["halted"] is True, result
    assert result["phase"] == RUN_PHASE_HALTED, result
    assert result["max_cycles"] == 2, result
    assert json.loads((fdir / "state.json").read_text())["phase"] == RUN_PHASE_HALTED




def test_the_assay_failure_door_honours_the_same_cap(run_env):
    """THE ADJACENT PATH: the other transition that opens a GRIND.

    `_halt_if_capped` is reached from `grind_start` — the token D-225 was driven
    on — and from `assay_fail`, which clears the same markers and calls the same
    `_update_phase(fdir, "F3")`. The ASSAY loop is exactly the one --max-cycles
    exists to bound, so a cap honoured on one token and discarded on the other
    would leave the expensive loop unbounded.
    """
    project_root, fdir = run_env
    _write_spec(fdir, ["FR-1"])
    _write_state(fdir, phase="F4", cycle=2, max_cycles=2.0)
    _write_verdicts(fdir, [{"requirement_id": "FR-1", "verdict": "VERIFIED"}])
    _defect_ledger(fdir, [_tiered("D-001", "LIVE")])
    (fdir / ".tasks-generated").write_text("x\n", encoding="utf-8")

    _arm_ordering_token(fdir)
    result = foundry_mark_phase_complete("assay_fail", project_root)

    assert result["ok"] is True, result
    assert result["halted"] is True, result
    assert result["phase"] == RUN_PHASE_HALTED, result




def test_the_init_door_refuses_a_cap_below_zero(run_env):
    """CT-016's other boundary. D-225.

    The schema carried no `minimum`, so `-1` was ACCEPTED at the door and read
    as "no cap" by the halt's `<= 0` arm: an operator who typed a negative cap
    ran unbounded with no notice. A cap below zero is not a cap, and the door is
    where the operator can still act on being told so.
    """
    from foundry_mcp import server as srv

    schema = _init_schema()
    assert schema["properties"]["max_cycles"]["minimum"] == 0
    refusal = srv._argument_refusal("Foundry-Init", schema, {"max_cycles": -1})
    assert refusal is not None
    assert "max_cycles" in refusal["error"], refusal
    # 0 is the documented spelling of unbounded and stays legal.
    assert srv._argument_refusal("Foundry-Init", schema, {"max_cycles": 0}) is None




# --------------------------------------------------------------------------- #
# D-233 — the cap door says what the seal did to the lead's prose
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("door", _TERMINAL_DOORS)
def test_a_seal_that_could_not_carry_the_prose_says_so_at_every_door(
    run_env, door, monkeypatch
):
    """GI-006 verbatim: 'The lead may append prose but cannot omit a section;
    `Foundry-Phase('done')` refuses if the report is absent.' FR-045 / AC-037.

    D-233. `_halt_if_capped` runs the SAME
    `_regenerate_report_preserving_lead_prose` the two F6 doors run and puts the
    same `lead_prose_lines` / `lead_prose_error` in its result, but it built its
    operator-facing `message` independently of `_sealed_report_sentence` — which
    has exactly two call sites, both F6 doors. So at the cap a FAILED write-back
    produced no "WARNING:" anywhere the operator reads, and `display.py` renders
    a phase result's `message` and no other field. That is the silence D-224 was
    filed for, arriving at the third terminal door, and it is the shape D-043
    and D-044 named: a rule enforced at one terminal transition and not the
    other is a rule the run walks around by ending the other way.

    THE FAILURE IS INDUCED AT THE WRITE-BACK, not simulated by patching the
    message. `_regenerate_report_preserving_lead_prose` generates the document
    first and carries the lead's block onto it second, so failing the SECOND
    write to REPORT.md is exactly the state the requirement is about: the report
    exists and is fresh, and the prose that was on it is gone. Anything the
    operator is not told here, they will never learn — HALTED and F6 both
    archive the run immediately after.
    """
    project_root, fdir = run_env
    _arrange_terminal_door(fdir, door)
    _generate_report(project_root, fdir)
    _append_lead_prose(fdir)

    real_write = Path.write_text
    seen = {"report_writes": 0}

    def _failing_write(self, data, *args, **kwargs):
        if self.name == vocab.REPORT_MD_FILENAME:
            seen["report_writes"] += 1
            # The first write is the generator's. The second is the seal
            # carrying the lead's block back on, and that is the one that fails.
            if seen["report_writes"] >= 2:
                raise OSError(13, "Permission denied")
        return real_write(self, data, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", _failing_write)

    _arm_ordering_token(fdir)
    result = foundry_mark_phase_complete(door, project_root)

    monkeypatch.undo()

    # The transition still HAPPENS — a failure to preserve never costs the
    # report and never refuses a crossing whose preconditions passed.
    assert result["ok"] is True, result
    assert result["report_generated"] is True, result
    assert seen["report_writes"] >= 2, seen
    assert result["lead_prose_error"], result
    assert result["lead_prose_lines"] == 0, result
    # ...and the operator is TOLD, in the one field the display renders.
    assert "WARNING:" in result["message"], (door, result["message"])
    assert result["lead_prose_error"] in result["message"], (door, result["message"])
    # The warning displaces the carried-prose sentence rather than joining it:
    # nothing was carried, so claiming it was is the D-228 defect with the sign
    # flipped.
    assert "prose you appended" not in result["message"], (door, result["message"])




def test_every_transition_token_has_exactly_one_preconditions_routine():
    """fallout AC-007 / GI-011 / GI-031 — one routine per TRANSITION token.

    Derived from `PHASE_TOKENS`, so a token added without a routine fails here
    rather than reaching a door that composes its checks inline again.
    """
    assert PHASE_TOKENS, "the token roster is empty; the derivation is blind"
    missing = [
        t for t in PHASE_TOKENS
        if not callable(getattr(_transitions, _preconditions_name(t), None))
    ]
    assert missing == [], missing
    # ...and the one place that says which routine belongs to which token knows
    # every one of them. Read off the chain's own source rather than driven,
    # because driving it would run eleven real evaluations against the
    # filesystem to learn something the source states outright.
    chain = inspect.getsource(_token_preconditions)
    for token in PHASE_TOKENS:
        assert f'token == "{token}"' in chain, token
        assert f"{_preconditions_name(token)}(" in chain, token




def test_every_gate_rank_constant_has_a_way_to_provoke_it():
    """The emptiness guard on the RUNG axis.

    `_RUNG_ARRANGEMENTS` is matched to the module's `_GATE_RANK_*` constants by
    NAME. A rank added without an arranger would simply never be walked, and the
    parametrization below would shrink silently — the worst failure a derived pin
    can have, and the one `test_every_defect_reading_gate_names_a_real_predicate`
    was written to prevent one axis over.
    """
    declared = set(_gate_rank_names())
    assert declared, "no _GATE_RANK_* constants found; the derivation is blind"
    assert declared == set(_RUNG_ARRANGEMENTS), {
        "declared_but_unarrangeable": sorted(declared - set(_RUNG_ARRANGEMENTS)),
        "arranged_but_undeclared": sorted(set(_RUNG_ARRANGEMENTS) - declared),
    }




_TOKEN_RUNGS = sorted(
    (token, rank)
    for token in PHASE_TOKENS
    for rank in sorted(_ranks_a_routine_can_emit(token))
)




@pytest.mark.parametrize("token,rank", _TOKEN_RUNGS)
def test_a_gate_and_its_transition_refuse_the_same_check(run_env, monkeypatch, token, rank):
    """fallout AC-008 / OT-007 / CT-013 — the whole invariant, every token, every rung.

    For each `PHASE_TOKENS` member and each `_GATE_RANK_*` rung its routine can
    emit: arrange the run so that rung fires, then drive BOTH doors and assert
    they refuse naming the same check, with `state.json.phase` unchanged by the
    refused transition.

    This is the pin D-240..D-243 would have failed. Each of those four was a
    check `foundry_gate` made and `_phase_transition` did not: the verdict read
    at temper, the second copy at nyquist, `state.nyquist`, and the sight url at
    cast. Under one routine per token they cannot recur, and this walks every
    token and rung to say so rather than the four the previous pin arranged.
    """
    project_root, fdir = run_env
    reason_kw = {"reason": "lead_ruling", "text": "stopped by hand"} if token == "halt" else {}
    _arrange_passing(project_root, fdir, token)
    if not _RUNG_ARRANGEMENTS[rank](project_root, fdir, token, monkeypatch):
        pytest.skip(f"{rank} is not reachable for {token} on a real arrangement")
    if token == "halt" and rank == "_GATE_RANK_CONFIG":
        reason_kw = {"reason": "not-a-member", "text": ""}

    before = json.loads((fdir / "state.json").read_text(encoding="utf-8")).get("phase")

    _arm_ordering_token(fdir)
    gate = foundry_gate(_gate_for(token), project_root, **reason_kw)
    _arm_ordering_token(fdir)
    transition = foundry_mark_phase_complete(token, project_root, **reason_kw)

    assert gate["passed"] is False, (token, rank, gate)
    assert transition.get("ok") is not True, (token, rank, transition)

    after = json.loads((fdir / "state.json").read_text(encoding="utf-8")).get("phase")
    assert after == before, (token, rank, before, after)

    # The SAME named check. `refusals` is the machine-readable list both doors
    # publish; comparing it rather than the one rendered sentence is what makes
    # this an assertion about the evaluation instead of about a prefix.
    gate_reasons = {r["reason"] for r in gate.get("refusals", [])}
    trans_reasons = {r["reason"] for r in transition.get("refusals", [])}
    if trans_reasons or gate_reasons:
        assert gate_reasons == trans_reasons, (token, rank, gate, transition)
    else:
        # The HALTED guard short-circuits above the branch chain at both doors,
        # so neither publishes a ladder — but both say the same sentence, with
        # each naming ITS OWN surface, which is the whole of the difference.
        # `_halted_refusal` takes that surface as an argument for exactly this
        # reason: one judgement, two callers, and the caller's name in the
        # sentence so the operator knows which call was refused.
        strip = lambda text: re.sub(r"Foundry-(Gate|Phase)\(phase='[^']+'\)", "<door>", text)
        assert strip(gate["reason"]) == strip(transition["error"]), (token, rank, gate, transition)




def test_neither_door_reads_a_ledger_outside_its_preconditions_routine():
    """fallout AC-009 — the AST pin.

    `foundry_gate` composes nothing: it looks the token up, calls the routine
    and absorbs what comes back. `_phase_transition`'s branches call their own
    routine and no OTHER refusal-producing reader. Both are asserted on the
    SOURCE, because a check re-inlined at either door is exactly how the
    disagreement returns — and it returns silently, which is why this is a
    structural assertion and not a behavioural one.

    The named set is the refusal-producing readers, not every function: the
    branches still clear markers, record widths and transact, and all of that
    is EFFECT, which runs only after the shared routine passed.

    THE READER SET IS DERIVED, AND BOTH CALL SHAPES ARE WALKED (fallout AC-009
    / FR-041). This pin was twelve hand-typed names matched against `ast.Name`
    calls only, and it reported green over three transition branches that swept
    the evidence corpus themselves and returned `_sweep_refusal(...)`:
    `_sweep_evidence_at_boundary`, `_sweep_refusal`, `_escalated_classes`,
    `_load_json` and `current_cycle` were all outside the twelve, and any
    `module.fn()` spelling was outside the walk. The set now comes from the
    routines' own ASTs — every helper any `_<token>_preconditions` reaches,
    transitively, minus the primitives a branch may also use — so a rung added
    tomorrow is a reader this pin knows about tomorrow.
    """
    refusal_readers = _refusal_readers()
    # The emptiness guard the derivation needs: a walk that silently returned
    # nothing would make every assertion below vacuous, which is the worst
    # failure a derived pin can have.
    assert len(refusal_readers) > 20, sorted(refusal_readers)
    for expected in (
        "_blocking_defects", "_check_active_teams", "_check_sight_required",
        "_terminal_evidence_state", "_sweep_evidence_at_boundary",
        "_unrecorded_width_problem",
    ):
        assert expected in refusal_readers, (expected, sorted(refusal_readers))

    #: The TWO reads a door may make outside its routine, each with its reason.
    #: Both are protocol rather than precondition — Holmes `flow-1` names the
    #: first explicitly — and naming them here, with the reason, is what makes
    #: adding a third an argument someone has to write down.
    DOOR_PROTOCOL_READS = {
        # D-082: the HALTED guard, stated ONCE above each door's branch chain
        # rather than as an arm inside every branch. It is not a precondition of
        # any token; it is the statement that a stopped run has no next door.
        "_halted_refusal",
        # `inspect_start` calls this to RECORD escalation proposals at the
        # boundary that closed a cycle — an effect, taken after the routine
        # passed, and nothing refuses on its answer. A reader set that judged by
        # name rather than by role would push a recording call into a
        # preconditions routine to satisfy a pin, which is how a gate acquires a
        # side effect.
        "_escalated_classes",
    }
    allowed = (
        {f"_{t}_preconditions" for t in PHASE_TOKENS}
        | {"_token_preconditions"}
        | DOOR_PROTOCOL_READS
    )

    def _called(fn) -> set[str]:
        tree = ast.parse(textwrap.dedent(inspect.getsource(fn)))
        names: set[str] = set()
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if isinstance(node.func, ast.Name):
                names.add(node.func.id)
            elif isinstance(node.func, ast.Attribute):
                names.add(node.func.attr)
        return names

    for fn in (foundry_gate, _phase_transition):
        leaked = sorted((_called(fn) & refusal_readers) - allowed)
        assert leaked == [], (fn.__name__, leaked)

    # ...and the transition's own branches each name their token's routine.
    source = inspect.getsource(_phase_transition)
    for token in PHASE_TOKENS:
        assert f"_{token}_preconditions(" in source, token




def test_the_transition_adds_no_refusal_of_its_own():
    """fallout FR-041 / FR-058 / GI-029 / ST-012 / AC-009 — the OTHER half.

    "The transition adds no refusal of its own" was in FR-041 and asserted
    nowhere. Three branches returned `_sweep_refusal(...)` directly, which is a
    refusal the mapped gate could not make and — because it was built outside
    `_GateLadder` — one that reached the caller with `refusals` absent, so the
    ranked list both doors publish did not contain the check that stopped the
    crossing.

    Structural, because the harm is structural: the check is that every refusal
    leaving this function was composed by the shared shape. Two composers are
    legal — `_transition_refusal`, which renders the routine's outcome, and the
    `_halted_refusal` guard stated once above the chain — and a third is the
    defect returning.
    """
    tree = ast.parse(textwrap.dedent(inspect.getsource(_phase_transition)))

    composed = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and (node.func.id.endswith("_refusal") or node.func.id.endswith("_problem"))
    }
    assert composed == {"_transition_refusal", "_halted_refusal"}, sorted(composed)

    # ...and no branch hand-builds one either. A returned dict literal carrying
    # an `error` key IS a refusal, whatever composed it.
    #
    # ONE is legal and it is not a refusal of a crossing: the else-branch answer
    # to a token that is not a token at all, which names `PHASE_TOKENS` as the
    # legal set exactly as `foundry_gate`'s unknown-phase answer names
    # `GATE_TO_TRANSITION`. It is identified by that derivation rather than by
    # position, so a second inline refusal cannot hide behind "there was already
    # one".
    inline = [
        ast.unparse(node.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Return) and isinstance(node.value, ast.Dict)
        and any(
            isinstance(key, ast.Constant) and key.value == "error"
            for key in node.value.keys
        )
    ]
    unaccounted = [text for text in inline if "PHASE_TOKENS" not in text]
    assert unaccounted == [], unaccounted
    assert len(inline) == 1, inline




def test_the_boundary_sweep_is_a_rung_of_the_routine_not_an_arm_in_the_branch():
    """fallout FR-058 / GI-029 / AC-056 — D-032, structurally.

    GI-002 puts an evidence sweep at every boundary that opens an INSPECT, and
    `cast`, `inspect_start` and `temper` are those three doors. The sweep and
    the width decision that scopes it stood inside `_phase_transition`'s
    branches, so `Foundry-Gate('inspect')`, `('inspect_start')` and
    `('temper')` answered `passed: True` over a corpus that no longer
    reproduced while the matching `Foundry-Phase` calls refused. Same shape as
    D-240..D-243, one boundary over.

    Asserted on the routines rather than driven, because "which door owns the
    read" is a structural fact and driving it proves only that today's
    arrangement happens to agree.
    """
    for token in ("cast", "inspect_start", "temper"):
        routine = getattr(_transitions, f"_{token}_preconditions")
        tree = ast.parse(textwrap.dedent(inspect.getsource(routine)))
        calls = {
            node.func.id for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        assert "_decide_inspect_mode" in calls, (token, sorted(calls))
        assert "_boundary_evidence_rung" in calls, (token, sorted(calls))

    # ...and the branches take neither read back. They consume what the routine
    # produced — `inspect_entry` and `evidence_sweep` are non-refusing facts on
    # the outcome, exactly as `_nyquist_preconditions` hands its record forward.
    branch_source = inspect.getsource(_phase_transition)
    assert "_decide_inspect_mode(" not in branch_source, branch_source
    assert "_sweep_evidence_at_boundary(" not in branch_source, branch_source
    assert branch_source.count('outcome["inspect_entry"]') == 3, branch_source




def test_the_four_open_live_defects_are_closed_as_one_class(run_env):
    """fallout AC-010 / OT-008 — D-240, D-241, D-242 and D-243.

    Driven at the TRANSITION, which is the door each of the four was open on.
    Not four separate arrangements of four separate rungs: one run, four
    crossings, each refused by the check its gate had always made and its
    transition never had.
    """
    project_root, fdir = run_env
    _arrange_passing(project_root, fdir, "temper")

    # D-240 — the verdict read at temper.
    _write_verdicts(fdir, [{"requirement_id": "FR-1", "verdict": "THIN"}])
    _arm_ordering_token(fdir)
    result = foundry_mark_phase_complete("temper", project_root)
    assert result.get("ok") is not True, result
    assert "not verified" in result["error"], result

    # D-241 — the same read at nyquist.
    _write_state(fdir, phase="F4", cycle=1, nyquist=True)
    _record_full_inspect_mode(fdir, cycle=1)
    _arm_ordering_token(fdir)
    result = foundry_mark_phase_complete("nyquist", project_root)
    assert result.get("ok") is not True, result
    assert "not verified" in result["error"], result

    # D-242 — `state.nyquist` false.
    _write_verdicts(fdir, [{"requirement_id": "FR-1", "verdict": "VERIFIED"}])
    _write_state(fdir, phase="F4", cycle=1, nyquist=False)
    _record_full_inspect_mode(fdir, cycle=1)
    _arm_ordering_token(fdir)
    result = foundry_mark_phase_complete("nyquist", project_root)
    assert result.get("ok") is not True, result
    assert "opt-in" in result["error"], result

    # ...and from the wrong source phase, which it also never checked.
    _write_state(fdir, phase="F2", cycle=1, nyquist=True)
    _record_full_inspect_mode(fdir, cycle=1)
    _arm_ordering_token(fdir)
    result = foundry_mark_phase_complete("nyquist", project_root)
    assert result.get("ok") is not True, result
    assert "F5.5" in result["error"], result

    # D-243 — the sight url at cast.
    _write_state(fdir, phase="F1", cycle=1)
    _write_manifest_with_castings(fdir, ["src/App.tsx"], no_ui=False)
    _arm_ordering_token(fdir)
    result = foundry_mark_phase_complete("cast", project_root)
    assert result.get("ok") is not True, result
    assert "frontend files in scope" in result["error"], result




def test_a_gated_transition_still_crosses_on_a_clean_ledger(run_env):
    """The passing half, driven all the way through the two cheapest doors.

    A parity pin that only ever observes refusals would be satisfied by a
    transition that refuses everything, which is the opposite failure and just
    as bad for a run.
    """
    project_root, fdir = run_env
    _arrange_passing(project_root, fdir, "grind_start")
    _arm_ordering_token(fdir)
    assert foundry_gate("grind", project_root)["passed"] is True
    _arm_ordering_token(fdir)
    result = foundry_mark_phase_complete("grind_start", project_root)
    assert result.get("ok") is True, result
    assert result["phase"] == "F3", result




@pytest.mark.parametrize("token", ["grind_start", "assay_fail"])
def test_both_grind_doors_seal_halted_at_the_cap(run_env, token):
    """fallout ST-015 / AC-060 — and the seal writes the vocabulary MEMBER."""
    project_root, fdir = run_env
    _arrange_passing(project_root, fdir, token)
    _write_state(fdir, phase="F2" if token == "grind_start" else "F4",
                 cycle=2, max_cycles=2)
    _record_full_inspect_mode(fdir, cycle=2)
    _arm_ordering_token(fdir)

    result = foundry_mark_phase_complete(token, project_root)
    assert result.get("ok") is True, result
    assert result["halted"] is True, result
    assert result["halted_reason"] == vocab.HALT_REASON_CAP_REACHED, result
    state = json.loads((fdir / "state.json").read_text(encoding="utf-8"))
    assert state["phase"] == vocab.RUN_PHASE_HALTED, state
    assert state["halted_reason"]["reason"] == "cap_reached", state
    assert "--max-cycles 2" in state["halted_reason"]["text"], state




def test_a_reason_outside_the_vocabulary_is_refused_naming_the_set(run_env):
    """fallout AC-026 / CT-005 / OT-024 — and the sentence is DERIVED."""
    project_root, fdir = run_env
    _arrange_passing(project_root, fdir, "halt")
    _arm_ordering_token(fdir)
    result = foundry_mark_phase_complete("halt", project_root, reason="because", text="x")
    assert result.get("ok") is not True, result
    for member in vocab.HALT_REASONS:
        assert member in result["hint"], (member, result)
    assert json.loads((fdir / "state.json").read_text(encoding="utf-8"))["phase"] == "F3"




def test_the_halt_door_refuses_while_a_team_is_registered(run_env):
    """fallout AC-025 / OT-023 — a halt with the teammates still up is a halt
    that leaves agents writing into a run nothing will read again."""
    project_root, fdir = run_env
    _arrange_passing(project_root, fdir, "halt")
    _teams_active(True)
    _arm_ordering_token(fdir)
    result = foundry_mark_phase_complete("halt", project_root, reason="lead_ruling", text="x")
    assert result.get("ok") is not True, result
    assert "Active teammates" in result["error"], result




def test_a_lead_ruling_seals_halted_with_the_member_the_text_and_the_history_row(run_env):
    """fallout FR-018 / FR-046 / CT-004 / ST-001 / AC-025 / AC-029 / OT-023.

    The whole contract in one drive: HALTED written through `_update_phase` so
    `phase_history` gains the row and `phase_times` closes the phase that was
    open, the reason stored as `{member, text}`, and the report regenerated with
    the lead's own prose carried.
    """
    project_root, fdir = run_env
    _arrange_passing(project_root, fdir, "halt")
    # A real run reaches this door through transitions, so `phase_times` carries
    # an OPEN entry for the phase it is in. AC-029's second half is that the
    # halt closes it, which a fixture that wrote state.json by hand cannot show.
    _update_phase(fdir, "F3")
    report = fdir / "REPORT.md"
    report.write_text(
        report.read_text(encoding="utf-8") + "\n## My own notes\n\nkeep this line\n",
        encoding="utf-8",
    )
    _arm_ordering_token(fdir)

    result = foundry_mark_phase_complete(
        "halt", project_root, reason="lead_ruling", text="the spec is wrong"
    )
    assert result.get("ok") is True, result
    assert result["halted"] is True and result["phase"] == vocab.RUN_PHASE_HALTED, result

    state = json.loads((fdir / "state.json").read_text(encoding="utf-8"))
    assert state["phase"] == vocab.RUN_PHASE_HALTED, state
    assert state["halted_reason"] == {"reason": "lead_ruling", "text": "the spec is wrong"}
    assert state["halted_at_cycle"] == 1, state
    # AC-029: the history ends on HALTED and the phase that was open is closed.
    assert state["phase_history"][-1]["phase"] == vocab.RUN_PHASE_HALTED, state
    assert "ended_at" in state["phase_times"]["F3"], state["phase_times"]
    # FR-046: the same regeneration the cap uses, so the lead's prose survives.
    assert "keep this line" in report.read_text(encoding="utf-8")




def test_a_halted_run_refuses_every_door_including_a_second_halt(run_env):
    """fallout ST-001 — HALTED is terminal, and `halt` is not its own exit."""
    project_root, fdir = run_env
    _arrange_passing(project_root, fdir, "halt")
    _arm_ordering_token(fdir)
    assert foundry_mark_phase_complete(
        "halt", project_root, reason="user_stop", text="stop"
    )["ok"] is True

    for token in PHASE_TOKENS:
        _arm_ordering_token(fdir)
        result = foundry_mark_phase_complete(
            token, project_root, reason="user_stop", text="again"
        )
        assert result.get("ok") is not True, (token, result)
        assert "HALTED" in str(result.get("error", "")), (token, result)




def test_a_sub_agent_read_arms_neither_the_token_nor_the_stall_clock(run_env):
    """fallout FR-034 / FR-055 / AC-053.

    Both halves fail in opposite directions. A sub-agent that armed the ordering
    token would satisfy, on the lead's behalf, the handshake that proves the
    LEAD consulted guidance before a transition. One that reset the stall clock
    would restart the watchdog every time any agent oriented itself — a
    watchdog any agent can silence is not a watchdog.
    """
    project_root, fdir = run_env
    _write_state(fdir, phase="F2", cycle=1)

    foundry_next_action(project_root)
    assert (fdir / artifacts.NEXT_ACTION_CALLED_MARKER).exists()
    assert (fdir / artifacts.LAST_NEXT_AT_MARKER).exists()

    (fdir / artifacts.NEXT_ACTION_CALLED_MARKER).unlink()
    (fdir / artifacts.LAST_NEXT_AT_MARKER).unlink()

    subagent = foundry_next_action(project_root, caller="subagent")
    assert not (fdir / artifacts.NEXT_ACTION_CALLED_MARKER).exists()
    assert not (fdir / artifacts.LAST_NEXT_AT_MARKER).exists()
    # It still ANSWERS — the read is not refused, it is just not the lead's.
    assert subagent["action"], subagent
    assert subagent["heading_for"] in ("DONE", vocab.RUN_PHASE_HALTED), subagent

    # ...and the transition still owes the lead's own handshake.
    refused = foundry_mark_phase_complete("grind_start", project_root)
    assert refused.get("ok") is not True
    assert "Foundry-Next" in refused["error"], refused




def test_inspect_start_is_refused_while_a_cross_casting_concern_is_open(run_env):
    """fallout FR-012 / GI-023 / ST-005 / AC-004 / OT-004.

    Both exits, driven: the concern is dispatched by Foundry-Tasks, or closed
    with a reason. Neither is "fix everything"; both leave a record.
    """
    from foundry_mcp.tools.concerns import foundry_concern

    project_root, fdir = run_env
    _manifest_with_requirement_ids(fdir, {
        1: (["FR-007"], ["src/one.py"]),
        2: (["FR-007"], ["src/two.py"]),
    })
    _write_state(fdir, phase="F3", cycle=1)
    opened = foundry_concern(
        casting_id=1, cycle=1, target="src/two.py",
        text="the fix reaches casting 2's own spelling of this rule",
        project_root=project_root,
    )
    assert opened.get("error") is None, opened
    concern_id = opened["concern"]["id"]

    _arm_ordering_token(fdir)
    refused = foundry_mark_phase_complete("inspect_start", project_root)
    assert refused.get("ok") is not True, refused
    assert concern_id in refused["error"], refused
    assert "Foundry-Tasks" in refused["hint"], refused
    assert "Foundry-Concern(close=" in refused["hint"], refused

    # EXIT ONE: the co-dispatch set carries it, and Foundry-Tasks says so.
    _defect_ledger(fdir, [
        dict(_tiered("D-900", "LIVE"), file="src/one.py", spec_ref="FR-007"),
    ])
    tasks = foundry_defects_to_tasks(project_root)
    assert concern_id in tasks["concerns_dispatched"], tasks

    _arm_ordering_token(fdir)
    passed = foundry_mark_phase_complete("inspect_start", project_root)
    assert "cross-casting concern" not in str(passed.get("error", "")), passed




def test_a_concern_closed_with_a_reason_also_clears_the_door(run_env):
    """fallout ST-004 / AC-004 — the OTHER exit, which costs a decision."""
    from foundry_mcp.tools.concerns import foundry_concern

    project_root, fdir = run_env
    _manifest_with_requirement_ids(fdir, {
        1: (["FR-007"], ["src/one.py"]),
        2: (["FR-007"], ["src/two.py"]),
    })
    _write_state(fdir, phase="F3", cycle=1)
    opened = foundry_concern(
        casting_id=1, cycle=1, target="src/two.py",
        text="the fix reaches casting 2's own spelling of this rule",
        project_root=project_root,
    )
    concern_id = opened["concern"]["id"]

    closed = foundry_concern(
        close=concern_id, reason="casting 2 already carries the new spelling",
        project_root=project_root,
    )
    assert closed.get("error") is None, closed

    _arm_ordering_token(fdir)
    result = foundry_mark_phase_complete("inspect_start", project_root)
    assert "cross-casting concern" not in str(result.get("error", "")), result




def test_a_crossing_already_refused_does_not_re_execute_the_corpus(run_env, monkeypatch):
    """fallout FR-058 / AC-056 — the rung is last, so it must not be blind.

    `_boundary_evidence_rung` is the final rung of the three INSPECT-opening
    routines, and a whole-corpus re-execution is minutes. `Foundry-Gate('inspect')`
    called from F3 is the case that makes this matter: it guards the `cast`
    transition, whose FIRST rung refuses from F3 — so sweeping there measures a
    corpus for a crossing that cannot happen, and the verdict could not change
    the answer either way.

    BOTH DOORS TAKE THE SAME BRANCH, which is what keeps this an optimisation
    and not a divergence: the skip lives inside the one routine both doors call,
    so the refusal set is still identical and only an already-decided checklist
    row goes unmeasured. The row says so rather than going missing.
    """
    project_root, fdir = run_env
    _write_state(fdir, phase="F3", cycle=1)
    _write_manifest_with_castings(fdir, ["src/api/a.py"], no_ui=True)

    swept = {"n": 0}

    def _counting(_fdir, _pr, _entry, *, full):
        swept["n"] += 1
        return {"ok": True, "record": {"scope": "full", "corpus_size": 0,
                                       "logs_reexecuted": [], "mismatches": [],
                                       "per_log": [], "elapsed_seconds": 0.0,
                                       "pool_size": 0},
                "mismatches": [], "error": ""}

    patch_everywhere(monkeypatch, "_sweep_evidence_at_boundary", _counting)

    _arm_ordering_token(fdir)
    gate = foundry_gate("inspect", project_root)
    assert gate["passed"] is False, gate
    assert swept["n"] == 0, "the corpus was re-executed for a refused crossing"

    # The row is REPORTED, not dropped: a checklist that silently loses a rung
    # is how a lead stops being able to tell "passed" from "not asked".
    rows = [c for c in gate["checklist"] if "evidence_reproduces_at_head" in c["check"]]
    assert len(rows) == 1, gate["checklist"]
    assert "not taken" in rows[0]["check"], rows[0]
    assert rows[0]["refuses"] is False, rows[0]

    # ...and from the phase the crossing IS legal from, it sweeps.
    _write_state(fdir, phase="F1", cycle=1)
    (fdir / artifacts.CAST_COMPLETE_MARKER).write_text("x\n", encoding="utf-8")
    _arm_ordering_token(fdir)
    foundry_gate("inspect", project_root)
    assert swept["n"] == 1, "the corpus was not re-executed on a reachable crossing"
