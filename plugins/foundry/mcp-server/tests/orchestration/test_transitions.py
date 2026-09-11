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
import shutil
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
from foundry_mcp.tools.orchestration import transitions as _transitions

# fallout AC-014 — THE TWO SIBLING SUITES THE CARVE MUST KEEP REACHING.
#
# `test_observations` owns the never-demote corpus and `test_spawn_progress`
# owns the shipped-source-tree derivation. Both are imported rather than copied,
# for the reason the monolith imported them: a parity test that owned its own
# copy of the corpus would keep passing while the two corpora drifted, and two
# scans over "the shipped source" must not be able to disagree about what that
# is. If either renames a symbol the ImportError says so by name, which is the
# loud failure rather than the silent one.

# fallout FR-004 / AC-014 (D-183) — THE ROSTER AND ITS HELPERS COME FROM
# `tests/orchestration/_env.py`, WHICH IS THE ONE PLACE THEY ARE STATED.
#
# This module carried its own byte-identical copy of a hand-typed thirteen-tuple
# and of `orchestration_source`, `owning_module`, `patch_everywhere` and
# `orchestration_has`. Fourteen copies of one roster is fourteen places to
# forget a module, and `keyfiles.py` — shipped in cycle 5 — was forgotten in
# every one of them: `owning_module` answered the IMPORTING module for
# `covers_path` and raised for `owning_entries`, and `patch_everywhere` could
# not reach a binding inside it. The roster is derived from the package
# directory now, so there is one of it and it cannot go stale.
from tests.orchestration._env import orchestration_has, owning_module, patch_everywhere  # noqa: F401

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

from foundry_mcp.tools.orchestration.report_seal import (  # noqa: F401
    _generate_report,
)

from foundry_mcp.tools.orchestration.gates import (  # noqa: F401
    _done_preconditions,
    foundry_gate,
)

from foundry_mcp.tools.orchestration.guidance import (  # noqa: F401
    _compute_next_action,
    foundry_next_action,
)

from foundry_mcp.tools.orchestration.gates import (  # noqa: F401
    _halted_refusal,
)

from foundry_mcp.tools.orchestration.streams import (  # noqa: F401
    VALID_STREAMS,
)

from foundry_mcp.tools.orchestration.transitions import (  # noqa: F401
    PHASE_TOKENS,
    # fallout ST-005 (D-158) — the routine, called directly, because the two
    # fail-open routes are properties of the ROUTINE both doors share and a
    # drive through one door would leave the other unasserted.
    _inspect_start_preconditions,
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

    The plant aliases `ledger_transaction` on import, so the binding the
    resolver sees is a member; the bare-name fallback stays for a module this
    resolver cannot import, which is what the synthetic `c.py` plant exercises.
    (Named by what it aliases rather than by the alias: an import inside a
    planted string is not a binding the tree ships, so the prose pin in
    `test_module_boundaries.py` cannot resolve the local spelling — fallout
    D-062, casting 10's concern C-049.)
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

    # fallout AC-062 (D-088) — `_phase_transition` READS IT THROUGH A ROUTINE
    # NOW, WHICH IS THE ONE READER ONE LAYER IN.
    #
    # It called `_halted_refusal` above its branch chain, and that guard was
    # what made `_halt_preconditions`' `not_already_halted` rung unreachable
    # through the door. The rung is `_halted_outcome`, the first thing every
    # `_<token>_preconditions` asks, so this function reads the halt through its
    # routine exactly as it reads every other precondition — which is what
    # GI-011 asks for and is a stronger statement than the one this test made.
    for fn in (foundry_gate, _done_preconditions, foundry_mark_phase_complete):
        source = _inspect.getsource(fn)
        assert "RUN_PHASE_HALTED" not in source, fn.__name__
        assert (
            "_halted_refusal(" in source
            or "_halted_state(" in source
            or "_halted_outcome(" in source
        ), fn.__name__

    transition_source = _inspect.getsource(_phase_transition)
    assert "RUN_PHASE_HALTED" not in transition_source
    assert "_halted_refusal(" not in transition_source, (
        "the transition composes a halted refusal of its own again; the rung "
        "belongs to the token's routine (D-088)"
    )

    # ...and the ONE reader is still one: `_halted_outcome` is the only builder
    # of a halted rung, and every routine reaches it.
    # Every token's routine asks the rung, either itself or through the
    # routine it DELEGATES to — `assay_fail` IS `grind_start`'s evaluation and
    # `nyquist_done` IS `done`'s, and a delegation is one evaluation with two
    # names, not a second one that could answer differently.
    _DELEGATES = {"_grind_start_preconditions", "_done_preconditions"}
    for token in PHASE_TOKENS:
        fn = getattr(_transitions, f"_{token}_preconditions", None)
        assert fn is not None, token
        body = _inspect.getsource(fn)
        asks = "_halted_outcome(" in body or any(d + "(" in body for d in _DELEGATES)
        assert asks, (
            f"_{token}_preconditions neither makes the halted rung nor "
            "delegates to a routine that does; a token whose routine skips it "
            "is a token that can transition out of HALTED (D-082/D-088)."
        )





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
    from foundry_mcp.tools.artifacts import report_document_status as report_status

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
    from foundry_mcp.tools.artifacts import report_document_status as report_status

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




#: fallout AC-008 / FR-041 (D-133 / D-134) — THE EMPTINESS GUARD ON THE
#: CROSS-PRODUCT, WHICH IS THE AXIS THAT HAD NONE.
#:
#: AC-008 asks that the invariant "PROVOKES EVERY RUNG FOR EVERY TOKEN", and the
#: parametrization derives its cases by intersecting two axes: tokens from
#: `PHASE_TOKENS`, rungs from the `_GATE_RANK_*` constants. BOTH AXES CARRY
#: EMPTINESS GUARDS —
#: `test_every_transition_token_has_exactly_one_preconditions_routine` and
#: `test_every_gate_rank_constant_has_a_way_to_provoke_it`. The INTERSECTION
#: carried none, and the intersection is what is actually walked.
#:
#: So a rung REMOVED from a routine silently deleted the case that would have
#: walked it. DRIVEN: the `_teams_rung` call was deleted from
#: `transitions.py#_cast_preconditions`; collection dropped 59 -> 58 and this
#: invariant stayed GREEN — the deletion was caught only by an unrelated
#: dedicated test, not by the pin AC-008 and FR-041 name as the mechanism. A
#: derived pin that shrinks when the code shrinks is the worst failure a derived
#: pin can have, and it is the same failure
#: `test_every_defect_reading_gate_names_a_real_predicate` was written to
#: prevent one axis over.
#:
#: A FLOOR, NEVER THE ROSTER. This suite's own rule, learned on C-062 and
#: written down at the width pin and again at `_LIFECYCLE_FLOOR`: "named modules
#: rather than a count alone, because a count passes on any roster of the right
#: size". Each entry names the rungs a token's routine MUST be able to emit; a
#: rung ADDED to a routine is walked the day it is written and needs no entry
#: here, and a rung REMOVED fails by name. The cases are still DERIVED — this
#: judges the derivation rather than replacing it.
_TOKEN_RUNG_FLOOR: dict[str, frozenset[str]] = {
    "assay_fail": frozenset({"DEFECTS", "HALTED", "MARKER", "TEAMS"}),
    "cast": frozenset({"CONFIG", "EVIDENCE", "HALTED", "SOURCE", "TEAMS"}),
    "done": frozenset({
        "CONFIG", "DEFECTS", "ESCALATION", "EVIDENCE", "HALTED", "REPORT",
        "SOURCE", "TEAMS", "VERDICTS",
    }),
    "grind_start": frozenset({"DEFECTS", "HALTED", "MARKER", "TEAMS"}),
    "halt": frozenset({"CONFIG", "HALTED", "TEAMS"}),
    "inspect_clean": frozenset({"DEFECTS", "HALTED", "STREAMS", "TEAMS", "WIDTH"}),
    "inspect_start": frozenset({
        "DEFECTS", "EVIDENCE", "HALTED", "MARKER", "SOURCE", "WIDTH",
    }),
    "nyquist": frozenset({
        "CONFIG", "DEFECTS", "EVIDENCE", "HALTED", "SOURCE", "VERDICTS",
    }),
    "nyquist_done": frozenset({
        "CONFIG", "DEFECTS", "ESCALATION", "EVIDENCE", "HALTED", "REPORT",
        "SOURCE", "TEAMS", "VERDICTS",
    }),
    "start_cast": frozenset({"CONFIG", "CONFLICT", "HALTED"}),
    # `CONFIG` is the temper opt-in rung fallout FR-016 / FR-060 added
    # (D-143 / D-144); it is in the floor so the rung cannot be removed and
    # take its own invariant case with it, which is the shape D-133 is about.
    "temper": frozenset({
        "CONFIG", "DEFECTS", "EVIDENCE", "HALTED", "SOURCE", "VERDICTS",
    }),
}


_TOKEN_RUNGS = sorted(
    (token, rank)
    for token in PHASE_TOKENS
    for rank in sorted(_ranks_a_routine_can_emit(token))
)


def test_the_walked_cross_product_covers_every_token_and_its_known_rungs():
    """fallout AC-008 / FR-041 (D-133 / D-134) — the pin's stated WIDTH.

    AC-008 and FR-041 both say the invariant "provokes every rung for every
    token". What was derived was correct and what was MISSING was any assertion
    that the derivation had not shrunk, so a rung deleted from a routine deleted
    its own coverage and the suite stayed green.

    Three claims, none of them a bare count:
      1. every `PHASE_TOKENS` member contributes at least one case — a token
         whose routine emits nothing would otherwise vanish from the walk;
      2. every token's floor rungs are all present, by NAME, so a removal is
         reported as the rung it was;
      3. the floor names every token, so a token added to `PHASE_TOKENS` without
         a floor entry fails here rather than being walked by whatever its
         routine happens to mention.
    """
    walked: dict[str, set[str]] = {}
    for token, rank in _TOKEN_RUNGS:
        walked.setdefault(token, set()).add(rank.replace("_GATE_RANK_", ""))

    assert set(_TOKEN_RUNG_FLOOR) == set(PHASE_TOKENS), {
        "token_without_a_floor": sorted(set(PHASE_TOKENS) - set(_TOKEN_RUNG_FLOOR)),
        "floor_without_a_token": sorted(set(_TOKEN_RUNG_FLOOR) - set(PHASE_TOKENS)),
    }
    missing = {
        token: sorted(floor - walked.get(token, set()))
        for token, floor in _TOKEN_RUNG_FLOOR.items()
        if not floor <= walked.get(token, set())
    }
    assert missing == {}, (
        f"rung(s) a token's routine no longer emits: {missing}. Each one took "
        "its own invariant case with it when it went, so the gate/transition "
        "parity for that check is no longer walked at all. Either restore the "
        "rung, or — if the check genuinely moved — take it out of "
        "_TOKEN_RUNG_FLOOR in the same commit and say where it went."
    )
    # ...and the floor has not outlived the derivation: an entry naming a rung
    # nothing emits would be a floor that judges nothing.
    assert set(walked) == set(PHASE_TOKENS), sorted(
        set(PHASE_TOKENS) - set(walked)
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
    # should-not-stop — after start_cast the halt door takes only a user_stop
    # carrying human-origin proof, so the passing baseline each rung breaks in
    # exactly one place is a user_stop beside an unused /foundry:stop token.
    reason_kw = {"reason": "user_stop", "text": "stopped by hand"} if token == "halt" else {}
    _arrange_passing(project_root, fdir, token)
    if token == "halt":
        _write_stop_token(fdir)
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

    # fallout AC-062 / OT-045 (D-129 / D-130) — THE LADDER IS PUBLISHED, AND
    # THAT IS AN ASSERTION RATHER THAN A CASE.
    #
    # What stood here was `if trans_reasons or gate_reasons: assert equal` with
    # an `else:` that strip-compared the two rendered sentences after regexing
    # the door name away. That `else:` existed for the HALTED rung, which used
    # to short-circuit ABOVE the branch chain at both doors and publish no
    # `refusals` key at all. D-088's fix moved the rung INTO every routine, so
    # the branch became unreachable on a clean tree — and was not deleted, which
    # turned it into the swallower of the exact defect it was written for.
    #
    # DRIVEN: the D-088 shape was re-injected verbatim at BOTH doors in a scratch
    # copy — `foundry_gate` and `foundry_mark_phase_complete` short-circuiting
    # above the branch chain on `_halted_outcome`, returning a door-prefixed
    # sentence with NO `refusals` key — and this pin reported 59 passed. Both
    # doors fell into the `else:`, `strip()` normalised the door prefix away, and
    # the pin was green on a tree where the halt token's third refusal did not
    # come from `_halt_preconditions` at all. AC-062 names this test as the
    # mechanism, and a mechanism that cannot fail on its own regression is not
    # one.
    #
    # So an empty ladder at either door is now a FAILURE. Every routine emits the
    # HALTED rung, so a refusing door that publishes nothing has composed its
    # refusal somewhere other than its preconditions function — which is the
    # thing GI-011, GI-029 and AC-009 exist to forbid.
    assert gate_reasons, (
        f"{token}/{rank}: the gate refused and published NO refusals ladder. A "
        "refusal composed above the branch chain is the D-088 shape, and it is "
        "what this pin exists to catch: the named check has to come from the "
        f"token's own preconditions routine. gate={gate}"
    )
    assert trans_reasons, (
        f"{token}/{rank}: the transition refused and published NO refusals "
        f"ladder. Same rule, other door. transition={transition}"
    )
    assert gate_reasons == trans_reasons, (token, rank, gate, transition)

    # ...and the ONE RENDERED LINE each door speaks is the same judgement, with
    # each door free to frame it. The transition prefixes its own crossing
    # ("Cannot enter CAST — ..."); the gate reports the rung bare. Containment
    # is therefore the honest relation and equality is not — which is why the
    # deleted `else:` could only ever have held for the HALTED short-circuit,
    # where both doors emitted one `_halted_refusal` sentence and neither
    # published a ladder at all.
    assert gate["reason"] in transition["error"], (token, rank, gate, transition)




#: fallout AC-062 / CT-013 / OT-007 (D-188 / D-189 / D-190) — WHAT THE HALT
#: TOKEN'S TWO ARGUMENTS MAY CARRY ON THE WIRE, AT EITHER DOOR.
#:
#: The invariant above drives both doors IN PROCESS, and that is exactly the
#: window this defect lived in. `server.py#call_tool` validates arguments
#: against the ADVERTISED schema before dispatch, so a keyword on one door's
#: property answers a value the shared routine never sees — and a pin that calls
#: the handlers directly walks straight past the layer that answered.
#: `Foundry-Phase`'s `reason` carried `"enum": sorted(HALT_REASONS)` and
#: `Foundry-Gate`'s carried none, so the same non-member reason got the
#: routine's named check at one door and "unusable argument(s): reason" at the
#: other, with no checklist and no refusals ladder on the second.
#:
#: `type` says what shape the transport will carry and `description` constrains
#: nothing, so those two are the whole of what an advertised halt-argument
#: property may be. EVERY OTHER KEYWORD IS A JUDGEMENT — `enum`, `const`,
#: `pattern`, `minLength` — and a judgement here is a refusal composed somewhere
#: other than `_halt_preconditions`, which AC-062 forbids outright ("refuses on
#: that function ALONE") and CT-021 forbids a second way at the gate: a door
#: required to REPORT the membership check as data cannot also refuse on it.
#:
#: EXACT EQUALITY, NOT A DENYLIST of the keywords anyone thought to name. The
#: enum that produced D-188 is caught by either shape; only this one catches the
#: `pattern` nobody has written yet, and only this one fails when a keyword is
#: added to BOTH doors at once — which would keep the pair agreeing while making
#: them agree on a check neither is allowed to make.
_HALT_ARGUMENT_SCHEMA_KEYS = frozenset({"type", "description"})

#: The two arguments the halt token carries at both doors. Named rather than
#: derived from the schema, because the failure being pinned is a property
#: DIFFERING between the doors and a set read off one of them could not see an
#: argument the other lacks.
_HALT_ARGUMENTS = ("reason", "text")


def _machine_readable(rendered: str) -> dict:
    """The JSON half of a rendered tool result.

    Split on `display.RESULT_JSON_MARKER` rather than on a re-typed copy of the
    sentence: the marker is the display layer's own constant and a hand copy
    here would fail the day it is reworded, over a change that broke nothing.
    """
    from foundry_mcp.tools.display import RESULT_JSON_MARKER

    return json.loads(rendered.split(RESULT_JSON_MARKER, 1)[1])


def _judgement_half(prop: dict) -> dict:
    """``prop`` minus its prose — the half a validator actually enforces."""
    return {key: value for key, value in prop.items() if key != "description"}


def _halt_argument_divergence(gate_props: dict, phase_props: dict) -> list[str]:
    """Every way the two doors' halt arguments judge, or judge differently.

    Two findings, and the pin needs both. A keyword outside
    `_HALT_ARGUMENT_SCHEMA_KEYS` is a judgement the transport makes instead of
    the routine (AC-062); a judgement half that DIFFERS between the doors is the
    pair naming two checks for one value (CT-013 / OT-007). The first catches a
    keyword added to both doors, which the second would call agreement.
    """
    found: list[str] = []
    for argument in _HALT_ARGUMENTS:
        for door, properties in (
            ("Foundry-Gate", gate_props), ("Foundry-Phase", phase_props)
        ):
            prop = properties.get(argument)
            if not isinstance(prop, dict):
                found.append(f"{door}.{argument} is not advertised at all")
                continue
            judging = sorted(set(prop) - _HALT_ARGUMENT_SCHEMA_KEYS)
            if judging:
                found.append(f"{door}.{argument} carries {judging}")
        gate_half = _judgement_half(gate_props.get(argument) or {})
        phase_half = _judgement_half(phase_props.get(argument) or {})
        if gate_half != phase_half:
            found.append(
                f"{argument}: Foundry-Gate advertises {gate_half} and "
                f"Foundry-Phase advertises {phase_half}"
            )
    return found


def test_neither_halt_door_advertises_a_judgement_on_its_halt_arguments():
    """fallout AC-062 / CT-013 / OT-007 (D-188 / D-189) — the schema half.

    No test in this suite compared the two tools' `reason` properties, which is
    how one door came to carry a closed set the other did not. Compared here, on
    the schemas `list_tools` really publishes rather than on the source, so a
    second spelling of either entry is judged the same way.
    """
    _, gate_schema = _tool_schema("Foundry-Gate")
    _, phase_schema = _tool_schema("Foundry-Phase")
    gate_props = gate_schema["properties"]
    phase_props = phase_schema["properties"]

    # The emptiness guard: a schema that stopped advertising the arguments would
    # make the comparison below vacuous rather than clean.
    for argument in _HALT_ARGUMENTS:
        assert argument in gate_props, (argument, sorted(gate_props))
        assert argument in phase_props, (argument, sorted(phase_props))

    assert _halt_argument_divergence(gate_props, phase_props) == [], (
        f"{_halt_argument_divergence(gate_props, phase_props)}. The halt token's "
        "arguments are judged by `_halt_preconditions` and by nothing else: "
        "AC-062 says the transition refuses on that function ALONE and CT-021 "
        "says the gate REPORTS its three checks as data, so a schema keyword "
        "that answers first falsifies the first and a matching keyword on the "
        "gate falsifies the second. Advertise the vocabulary in the "
        "`description` — derived from `halt_reason_phrase()`, never re-typed — "
        "and let the door name the check."
    )

    # ...and the vocabulary is still ON THE WIRE, which is the constraint the
    # fix had to respect: `list_tools` is where a client learns the legal
    # values, and trading a nameless refusal for an undocumented argument would
    # be the same defect pointing the other way.
    for door, properties in (
        ("Foundry-Gate", gate_props), ("Foundry-Phase", phase_props)
    ):
        described = properties["reason"]["description"]
        for member in sorted(vocab.HALT_REASONS):
            assert member in described, (door, member, described)


def test_the_halt_argument_recogniser_sees_the_enum_that_produced_the_defect():
    """The anchor: the comparison above finds a divergence when there IS one.

    A comparison over two matching schemas is green whether it works or not, so
    the recogniser is driven over the shape the tree actually shipped — one door
    carrying `enum` and the other bare — and over the shape that would replace
    it if someone answered the parity complaint by giving BOTH doors the
    keyword.
    """
    bare = {
        "reason": {"type": "string", "description": "why the run ended"},
        "text": {"type": "string", "description": "the lead's own words"},
    }
    judged = {
        "reason": {
            "type": "string",
            "enum": sorted(vocab.HALT_REASONS),
            "description": "why the run ended",
        },
        "text": {"type": "string", "description": "the lead's own words"},
    }

    # The shipped shape: the enum on Foundry-Phase alone. Both findings fire —
    # the keyword, and the disagreement it creates.
    one_sided = _halt_argument_divergence(bare, judged)
    assert any("Foundry-Phase.reason carries ['enum']" in row for row in one_sided), one_sided
    assert any(row.startswith("reason: Foundry-Gate advertises") for row in one_sided), one_sided

    # The shape that would "fix" the parity by making the gate refuse too: the
    # doors now agree, and the keyword finding is what still catches it.
    both_sided = _halt_argument_divergence(judged, judged)
    assert both_sided, "a keyword on BOTH doors is agreement on a check neither may make"
    assert not any(row.startswith("reason: Foundry-Gate advertises") for row in both_sided), both_sided

    # ...and a missing property is a finding rather than a silent pass.
    assert _halt_argument_divergence(bare, {"reason": bare["reason"]}) == [
        "Foundry-Phase.text is not advertised at all",
        "text: Foundry-Gate advertises {'type': 'string'} and Foundry-Phase advertises {}",
    ]


def test_both_halt_doors_name_the_same_check_over_mcp(run_env, monkeypatch):
    """fallout AC-062 / CT-013 / OT-007 (D-188 / D-189 / D-190) — the wire half.

    The invariant above is the same statement driven in process, and it reported
    green on the tree that shipped this defect: it calls `foundry_gate` and
    `foundry_mark_phase_complete` directly, so the pre-dispatch validation in
    `server.py#call_tool` never ran. Driven here through
    `request_handlers[CallToolRequest]`, which is the transport a client uses
    and the only place the divergence existed.

    D-189 is precise about what has to match: CT-013 is a statement about the
    NAMED CHECK, and the machine-readable `refusals` ladder is where both doors
    publish it. One door answering with no `refusals` key at all is the failure,
    whatever its sentence says.
    """
    import foundry_mcp.server as srv

    project_root, fdir = run_env
    _write_state(fdir, phase="F3", cycle=1)
    monkeypatch.setattr(srv, "_project_root", project_root)

    arguments = {"phase": "halt", "reason": "because", "text": "x"}
    _arm_ordering_token(fdir)
    gate = _machine_readable(_drive_mcp("Foundry-Gate", arguments))
    _arm_ordering_token(fdir)
    transition = _machine_readable(_drive_mcp("Foundry-Phase", arguments))

    named = "halt reason 'because' is not a member of the halt vocabulary"
    assert gate["passed"] is False, gate
    assert transition.get("ok") is not True, transition
    assert gate["reason"] == named, gate
    assert named in transition["error"], transition

    # The transport's own refusal shape, which is what answered before the fix.
    # Named rather than merely absent-by-implication: "unusable argument(s)" is
    # `_argument_refusal`'s sentence and nothing else in this server emits it.
    assert "unusable argument(s)" not in json.dumps(transition), transition
    assert "unusable argument(s)" not in json.dumps(gate), gate

    # The same ladder and the same checklist row, from the same routine.
    assert {r["reason"] for r in gate["refusals"]} == {
        r["reason"] for r in transition["refusals"]
    }, (gate, transition)
    membership = "halt_reason_is_a_member (reason=because)"
    for door in (gate, transition):
        row = next(r for r in door["checklist"] if r["check"] == membership)
        assert row["ok"] is False, door
        assert row["accepted"] == sorted(vocab.HALT_REASONS), door

    # ...and the refused transition mutated nothing.
    after = json.loads((fdir / "state.json").read_text(encoding="utf-8"))["phase"]
    assert after == "F3", after




def _transition_branches() -> dict[str, str]:
    """`{token: branch source}` for every `phase == "<literal>"` arm.

    fallout AC-009 / FR-041 (D-086) — THE WINDOW IS THE BRANCH, NOT THE
    FUNCTION.

    The pin below used to assert `f"_{token}_preconditions(" in source` over
    the WHOLE of `_phase_transition`, which is satisfied by any ONE branch
    naming any ONE routine: ten branches could share one call and the assertion
    would still pass ten times. Judged per branch, a branch that skipped its
    routine is named.
    """
    tree = ast.parse(textwrap.dedent(inspect.getsource(_phase_transition)))
    fn = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef))
    out: dict[str, str] = {}

    def _token_of(test) -> str | None:
        if not isinstance(test, ast.Compare):
            return None
        if not (isinstance(test.left, ast.Name) and test.left.id == "phase"):
            return None
        for op, comparator in zip(test.ops, test.comparators):
            if isinstance(op, ast.Eq) and isinstance(comparator, ast.Constant):
                if isinstance(comparator.value, str):
                    return comparator.value
        return None

    node = None
    for statement in fn.body:
        if isinstance(statement, ast.If) and _token_of(statement.test):
            node = statement
            break
    while isinstance(node, ast.If):
        token = _token_of(node.test)
        if token:
            out[token] = "\n".join(ast.unparse(s) for s in node.body)
        node = node.orelse[0] if len(node.orelse) == 1 else None
    return out


#: fallout AC-009 / FR-041 / GI-011 (D-218) — THE CALL THAT ENDS A BRANCH'S
#: PRECONDITION WINDOW, SPELLED ONCE.
#:
#: `_split_at_the_refusal` finds the window by this text and the pin below
#: excuses this NAME, because a branch naming its own refusal renderer is the
#: window's boundary rather than a read inside it. Two hand-typed copies of one
#: call name is how a marker and its exemption drift into disagreeing about
#: where the window ends, so the marker is DERIVED from the name.
_BRANCH_REFUSAL_CALL = "_transition_refusal"
_BRANCH_REFUSAL_MARKER = f"return {_BRANCH_REFUSAL_CALL}("


def _split_at_the_refusal(branch: str) -> tuple[str, str]:
    """A branch's PRECONDITION segment and its EFFECT segment.

    fallout GI-011 / AC-009 / FR-058 (D-086 / D-087) — POSITION IS THE RULE,
    AND IT REPLACES AN ALLOWLIST.
    ------------------------------------------------------------------------
    GI-011's violation column is "a transition branch that reads a ledger or
    marker its preconditions function does not", and the pin enforcing it
    hand-exempted `_escalated_classes` in `DOOR_PROTOCOL_READS` — a live
    instance of the invariant this release exists to establish, excused by name
    in the very guard written to catch it (D-087).

    The exemption was standing in for a REAL distinction the pin could not
    express: `inspect_start` calls `_escalated_classes` to RECORD proposals at
    the boundary that closed a cycle, AFTER the routine passed and after the
    state transaction. Nothing refuses on its answer. That is an EFFECT, and
    every branch is full of them — clearing markers, recording widths,
    transacting, stamping SHAs.

    So the distinction is made structurally instead of by name: everything above
    the branch's `return _transition_refusal(...)` can influence a refusal and
    is judged; everything below it runs only on a passing routine and is not.
    A read moved ABOVE that return is judged the day it moves, whatever it is
    called, and no name needs excusing.
    """
    index = branch.find(_BRANCH_REFUSAL_MARKER)
    if index == -1:
        return branch, ""
    tail = branch.index("\n", index) if "\n" in branch[index:] else len(branch)
    return branch[:tail], branch[tail:]


def _reads_in(segment: str, readers: set[str]) -> set[str]:
    """Every ledger/marker read `segment` makes, by call name and by path.

    fallout AC-009 (D-086) — `_load_json` IS A LEDGER READ HERE.

    `_SHARED_PRIMITIVES` excuses it, which is right for a routine (every rung
    loads a document) and wrong for a BRANCH: an inline
    `_load_json(fdir / "defects.json")` above the refusal is exactly the check
    re-inlined at the door that this pin exists to catch, and it was invisible.
    Driven: injecting that call into the `start_cast` branch leaked nothing.

    So the branch window judges the primitives too, plus the marker-existence
    shape a read can also take (`(fdir / "...").exists()`).
    """
    tree = ast.parse(textwrap.dedent(segment))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            name = (
                func.id if isinstance(func, ast.Name)
                else func.attr if isinstance(func, ast.Attribute)
                else ""
            )
            if name in readers or name in _LEDGER_PRIMITIVES:
                found.add(name)
            if name == "exists":
                found.add("<marker>.exists")
    return found


#: The document primitives a BRANCH may not use above its refusal, even though
#: a routine may. Named separately from `_SHARED_PRIMITIVES` because the two
#: windows ask different questions: a rung loading a document is the rung doing
#: its job, and a branch loading one is the branch deciding something its
#: routine did not.
#:
#: fallout AC-009 / FR-041 (D-161) — AND THE STDLIB SPELLINGS, WHICH ARE THE
#: TWO MOST ORDINARY WAYS TO READ A LEDGER.
#:
#: The set held the project's own helpers and nothing else, so the recogniser
#: this pin depends on did not know what a ledger read LOOKS LIKE when it is
#: written the way Python writes one. DRIVEN over `_reads_in` at the branch
#: window: a planted `extra = json.loads((fdir / 'defects.json').read_text(
#: encoding='utf-8'))` above a refusal returned the EMPTY SET, and so did
#: `json.load(open(fdir / 'defects.json'))`. This is the same scan-window class
#: D-086 closed one generation of — the docstring below already says "a read
#: need not be a `_load_json`" — and no shipped branch uses either spelling
#: today, so the guard was green while the rule it encodes was narrower than
#: AC-009 states.
#:
#: `open` is here as a READ even though it also writes: a branch that OPENS a
#: file above its refusal is either reading a ledger the routine did not or
#: mutating before a refusal, and the second is worse than the first. Matched by
#: call NAME (`ast.Name.id`) and by attribute (`ast.Attribute.attr`), so
#: `json.loads`, `p.read_text()` and a bare `open(...)` are all reached.
_LEDGER_PRIMITIVES = frozenset({
    "_load_json", "read_document", "read_jsonl", "read_text_file", "_read_text",
    "read_text", "read_bytes", "open", "load", "loads",
})


#: fallout AC-009 / FR-041 (D-177) — WHAT AN ENTRY POINT IS ALLOWED TO NAME
#: BESIDES A READ: its own dispatch, and the shared rung every routine makes.
#:
#: `_token_preconditions` is the gate's dispatch and `_phase_transition` is the
#: transition door's; `_halted_outcome` is the rung EVERY routine asks, so a
#: door asking it is not asking a check the routine does not make — it is how a
#: door decides whether its own protocol applies (the ordering token is not
#: demanded of a run that has stopped). `_GateLadder` is a constructor.
#:
#: DELIBERATELY NOT `_SHARED_PRIMITIVES`: that set excuses `_load_json`, which
#: is right for a rung loading a document and wrong for a DOOR loading one, for
#: exactly the reason the branch window states one line up.
_DOOR_DISPATCH = frozenset(
    {"_token_preconditions", "_phase_transition", "_halted_outcome", "_GateLadder"}
    | {f"_{token}_preconditions" for token in PHASE_TOKENS}
)

#: fallout AC-009 / CT-013 / FR-046 (D-177) — THE TWO READS BOTH DOORS MAKE
#: OUTSIDE ANY ROUTINE, AND THE WHOLE OF WHAT IS TOLERATED THERE.
#:
#: `_artifact_guard` is the total parse check and `<marker>.exists` is the
#: `.next-action-called` ordering token. Both are scoped `not halt_scoped` at
#: BOTH doors — FR-046 says the halt token refuses on `_halt_preconditions` and
#: nothing else, and CT-013 says the pair refuses the identical set, so a check
#: one door makes and the other does not would break the pair.
#:
#: NAMED AS WELL AS COMPARED. Equality between the two doors alone would license
#: a read added to both at once; naming them means a third entry is an edit
#: somebody has to write an argument for, in a file whose subject is that
#: neither door composes anything.
_DOOR_PROTOCOL_READS = frozenset({"_artifact_guard", "<marker>.exists"})


def _door_reads(source: str) -> set[str]:
    """Every ledger or marker read an ENTRY POINT makes, minus its dispatch.

    fallout AC-009 / FR-041 (D-177) — THE DOOR WINDOW, WHICH IS WIDER THAN THE
    BRANCH WINDOW ON PURPOSE.

    A branch is judged against `refusal_readers`, the set derived from what the
    preconditions routines reach. That is right for a branch and blind at a
    DOOR: `_artifact_guard` is a total read of every run artifact that REFUSES,
    no routine calls it, so it can never enter a set derived from the routines —
    and it sat at both doors invisible to the pin whose subject it is. Here the
    subject is "does this door touch an artifact at all", so every private call
    it makes counts, plus the stdlib spellings and the marker shape `_reads_in`
    already knows.
    """
    found = _reads_in(source, set())
    for node in ast.walk(ast.parse(textwrap.dedent(source))):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = (
            func.id if isinstance(func, ast.Name)
            else func.attr if isinstance(func, ast.Attribute)
            else ""
        )
        if name.startswith("_"):
            found.add(name)
    return found - _DOOR_DISPATCH


def _marker_receivers(source: str) -> list[str]:
    """The expression each `.exists()` in `source` is asked of.

    fallout AC-009 (D-177) — BECAUSE `<marker>.exists` IS ONE ENTRY HOWEVER MANY
    MARKERS A DOOR READS. `_door_reads` collapses every marker read onto that
    one name, so a SECOND marker read is invisible to it by name and a plant
    beside the real one would pass. The receiver expression is what tells them
    apart: both doors ask `fdir` (is there a run at all) and `nac` (the ordering
    token), and a plant asks something else.
    """
    return sorted(
        ast.unparse(node.func.value)
        for node in ast.walk(ast.parse(textwrap.dedent(source)))
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "exists"
    )


def test_neither_door_reads_a_ledger_outside_its_preconditions_routine():
    """fallout AC-009 / FR-041 / GI-011 / GI-029 — the AST pin.

    `foundry_gate` composes nothing: it looks the token up, calls the routine
    and absorbs what comes back. `_phase_transition`'s branches call their own
    routine and, ABOVE THE REFUSAL, no other reader at all. Both are asserted on
    the SOURCE, because a check re-inlined at either door is exactly how the
    disagreement returns — and it returns silently, which is why this is a
    structural assertion and not a behavioural one.

    THREE MEASURED HOLES CLOSED (D-086 / D-087):

      * `_SHARED_PRIMITIVES` contains `_load_json`, so an inline ledger read in
        a branch was invisible. Driven: `_load_json(fdir / "defects.json")`
        injected into the `start_cast` branch leaked nothing. The branch window
        judges the primitives; the routine window still excuses them, because a
        rung loading a document is a rung doing its job.
      * The reader set was consulted at FUNCTION scope, so the recording calls
        `inspect_start` makes after its transaction had to be excused BY NAME —
        `_escalated_classes` sat in `DOOR_PROTOCOL_READS`, which is a live
        instance of GI-011's violation clause excused inside the guard written
        to catch it. Position replaces the name: above the refusal is judged,
        below it is effect.
      * "calls its preconditions function" was asserted over the whole source,
        which one branch naming one routine satisfies for all ten. Per branch
        now.
    """
    refusal_readers = _refusal_readers()
    # The emptiness guard the derivation needs: a walk that silently returned
    # nothing would make every assertion below vacuous, which is the worst
    # failure a derived pin can have.
    assert len(refusal_readers) > 20, sorted(refusal_readers)
    for expected in (
        # fallout GI-033 / AC-061 (D-021 / D-035): the team and SIGHT reads now
        # answer from the leaf, so the verifier layer's own compositions are
        # what a routine reaches — `gates._active_teams` and
        # `width._sight_required`. Same two questions, same two rungs, named by
        # the module that may ask them.
        "_blocking_defects", "_active_teams", "_sight_required",
        "_terminal_evidence_state", "_sweep_evidence_at_boundary",
        "_unrecorded_width_problem",
    ):
        assert expected in refusal_readers, (expected, sorted(refusal_readers))

    #: fallout GI-011 (D-087) — THE TABLE IS EMPTY, AND THAT IS THE FIX.
    #:
    #: It held `_halted_refusal` (the door-level guard, now a rung inside every
    #: routine — D-088) and `_escalated_classes` (a post-transaction recording
    #: call, now excused by POSITION rather than by name — D-087). Neither is a
    #: name this pin needs to know. A row added here is an argument someone has
    #: to write down, and the argument now has to explain why a read that can
    #: influence a refusal is not a precondition.
    DOOR_PROTOCOL_READS: set[str] = set()
    allowed = (
        {f"_{t}_preconditions" for t in PHASE_TOKENS}
        # ...the mapping from a token to its routine, and the SHARED RUNG every
        # routine makes. `_halted_outcome` is in this set for the same reason
        # `_token_preconditions` is, and not as an exemption: a door asking the
        # rung every routine asks is not asking a check the routine does not
        # make, which is the whole of what this pin measures. It is what lets a
        # door decide whether its own PROTOCOL applies — the ordering token is
        # not demanded of a run that has stopped — without reading a ledger the
        # routine does not.
        | {"_token_preconditions", "_halted_outcome"}
        | DOOR_PROTOCOL_READS
    )

    # fallout AC-009 / FR-041 (D-177) — THE GATE HALF, WHICH USED TO ASSERT
    # NEITHER OF AC-009's TWO CLAUSES.
    # ----------------------------------------------------------------------
    # AC-009 is "each gate branch AND each transition branch for a token calls
    # its preconditions function and performs no other ledger or marker read".
    # The transition half below does exactly that. The gate half was one line —
    # `(_called(foundry_gate) & refusal_readers) - allowed` — and it asserted
    # neither clause:
    #
    #   * NO PRESENCE ASSERTION AT ALL. Driven at HEAD by replacing
    #     `_token_preconditions(` with `dict(` in a copy of the gate's source,
    #     so the door called no routine whatsoever: the intersection stayed
    #     empty and the pin stayed green.
    #   * THE READER SET COULD ONLY EVER CONTAIN A READ SOME ROUTINE ALREADY
    #     MAKES. `refusal_readers` is derived from what the routines reach, so
    #     an inline read the routines do NOT make — which is precisely the read
    #     AC-009 forbids — is outside it by construction. Driven with the two
    #     plants D-161 taught this file to recognise at the branch window:
    #     `json.loads((fdir / 'defects.json').read_text(...))` and a
    #     `(fdir / '.planted-marker').exists()` beside a refusal. Both leaked
    #     NOTHING through the gate half while the branch half names either one.
    #
    # AND THE TWO READS THE SHIPPED GATE ACTUALLY MAKES WERE INVISIBLE TO IT.
    # `_artifact_guard(fdir)` — a total read of every run artifact, which
    # REFUSES — and `nac.exists()` are both outside `refusal_readers` (58
    # entries; neither name is in it). They are not a parity break today because
    # `foundry_mark_phase_complete` makes the identical two, under the identical
    # `halt_scoped` scoping. But "identical at both doors" was a fact nothing
    # asserted, and it is the whole of CT-013 — so it is asserted here, from the
    # SIBLING DOOR rather than from a list: the gate's reads outside its routine
    # must be exactly the transition door's, and the pair must be exactly the
    # two named below.
    #
    # THIS IS NOT `DOOR_PROTOCOL_READS` COMING BACK. That table excused a name
    # inside a BRANCH, where the routine is the only thing entitled to read; the
    # window here is the ENTRY POINT, whose preamble the transition half already
    # does not judge (it judges branches). What is new is that the preamble is
    # judged at all, that the two doors are held equal, and that the residue is
    # two named entries rather than an open list.
    gate_src = textwrap.dedent(inspect.getsource(foundry_gate))
    door_src = textwrap.dedent(inspect.getsource(foundry_mark_phase_complete))

    # (1) The gate NAMES its routine. One line, and the one the plant defeated.
    assert "_token_preconditions(" in gate_src, (
        "foundry_gate does not call _token_preconditions. A door that composes "
        "its own checks is the disagreement CT-013 forbids, and it is the shape "
        "this pin exists to make impossible (AC-009)."
    )

    # (2) Its reads outside that routine are the SIBLING DOOR's, exactly.
    gate_protocol = _door_reads(gate_src)
    door_protocol = _door_reads(door_src)
    assert gate_protocol == door_protocol, {
        "gate_only": sorted(gate_protocol - door_protocol),
        "transition_only": sorted(door_protocol - gate_protocol),
    }
    assert gate_protocol == set(_DOOR_PROTOCOL_READS), sorted(gate_protocol)
    # `fdir` is the run directory itself — "there is no run to gate at all",
    # which precedes every question either door could ask — and `nac` is the
    # ordering token. A third receiver is a marker one door consults and the
    # other does not, which is the shape this whole pin measures.
    assert (
        _marker_receivers(gate_src) == _marker_receivers(door_src) == ["fdir", "nac"]
    ), (_marker_receivers(gate_src), _marker_receivers(door_src))
    # ...and `nac` is the ordering token at both, not some other marker wearing
    # the same local name.
    for name, src in (("foundry_gate", gate_src),
                      ("foundry_mark_phase_complete", door_src)):
        assert "nac = fdir / NEXT_ACTION_CALLED_MARKER" in src, name

    # The TRANSITION is judged per branch, above the refusal.
    branches = _transition_branches()
    assert set(branches) == set(PHASE_TOKENS), sorted(set(PHASE_TOKENS) ^ set(branches))
    for token, branch in sorted(branches.items()):
        preconditions, _effect = _split_at_the_refusal(branch)
        # ...each branch names ITS OWN routine, in its own window.
        assert f"_{token}_preconditions(" in preconditions, (
            f"the {token} branch does not call _{token}_preconditions before "
            "its refusal; a branch that shares another's call is a branch whose "
            "checks nobody stated (GI-011)."
        )
        # fallout AC-009 / GI-011 / FR-041 (D-218) — JUDGED BY `_door_reads`'
        # RULE, WHICH IS THE SIBLING WINDOW'S AND ALREADY SHIPS.
        # ------------------------------------------------------------------
        # This read `_reads_in(preconditions, refusal_readers)`, and
        # `refusal_readers` is DERIVED from the names the preconditions
        # routines already reach — so a read the routines do NOT make is
        # outside the set by construction, which is precisely the read GI-011's
        # violation column names ("a transition branch that reads a ledger or
        # marker its preconditions function does not"). DRIVEN in a detached
        # worktree at ac89f59: a module-level
        # `def _peek_defect_ledger(fdir): return _load_json(fdir / 'defects.json')`
        # called as `_probe_unused = _peek_defect_ledger(fdir)` at the top of
        # the `temper` branch left this suite at 484 passed, 2 skipped —
        # byte-identical to the clean baseline — while the same branch's inline
        # `_load_json` (the D-086 plant) still went red.
        #
        # `_door_reads` is the same measurement one window over: "does this
        # window touch an artifact AT ALL", every private call counted rather
        # than only the names some routine happens to share. D-177 gave it to
        # the GATE window for exactly this reason and the sibling walk simply
        # had not adopted it; adopting it here is that one site, not a new
        # recogniser.
        #
        # THE RESIDUE IS ONE NAME, AND IT IS THE WINDOW'S OWN BOUNDARY.
        # `_split_at_the_refusal` keeps the `return _transition_refusal(...)`
        # line inside the precondition segment, so every branch names its
        # refusal renderer there by construction. Measured over all eleven
        # shipped branches, that is the WHOLE residue: `{_transition_refusal}`
        # each, and nothing else.
        reads = _door_reads(preconditions) - {_BRANCH_REFUSAL_CALL}
        assert reads == set(), (token, sorted(reads))


def test_the_branch_window_catches_an_inline_ledger_read(monkeypatch):
    """The anchor for D-086's first measured hole.

    A scan over clean source is green whether it works or not, so the recogniser
    is driven over the exact plant the defect used: an inline `_load_json` of a
    ledger inside a branch, above its refusal. `_SHARED_PRIMITIVES` excuses that
    name, which is why the branch window has its own primitive set.
    """
    planted = (
        "outcome = _start_cast_preconditions(fdir, project_root)\n"
        "extra = _load_json(fdir / 'defects.json')\n"
        "if not outcome['passed']:\n"
        "    return _transition_refusal(outcome, 'Cannot enter CAST')\n"
        "_update_phase(fdir, 'F1')\n"
    )
    preconditions, effect = _split_at_the_refusal(planted)
    assert "_load_json" in preconditions and "_update_phase" in effect
    assert "_load_json" in _reads_in(preconditions, set())

    # ...and the SAME read below the refusal is an effect, which is what lets
    # `_escalated_classes` stop being an allowlist row.
    below = (
        "outcome = _inspect_start_preconditions(fdir, project_root)\n"
        "if not outcome['passed']:\n"
        "    return _transition_refusal(outcome, 'Cannot start an INSPECT')\n"
        "_record_escalation_proposals(fdir, _escalated_classes(fdir, project_root))\n"
    )
    pre_below, eff_below = _split_at_the_refusal(below)
    assert "_escalated_classes" not in pre_below, pre_below
    assert "_escalated_classes" in eff_below, eff_below


def test_the_branch_window_catches_a_marker_read_too():
    """The second half of D-086's first hole: a read need not be a `_load_json`.

    The drive that found this injected a `.exists()` marker read beside an
    inline refusal, and neither pin saw it.
    """
    planted = (
        "outcome = _cast_preconditions(fdir, project_root)\n"
        "if (fdir / '.some-marker').exists():\n"
        "    return _transition_refusal(outcome, 'Cannot mark CAST complete')\n"
        "if not outcome['passed']:\n"
        "    return _transition_refusal(outcome, 'Cannot mark CAST complete')\n"
    )
    preconditions, _effect = _split_at_the_refusal(planted)
    assert "<marker>.exists" in _reads_in(preconditions, set()), preconditions


def test_the_door_window_catches_what_the_gate_half_used_to_miss():
    """fallout AC-009 / FR-041 (D-177) — THE ANCHOR FOR THE GATE HALF.

    A scan over clean source is green whether it works or not, and the gate half
    of the pin above was green for three plants at once. Each is driven here
    over a COPY of `foundry_gate`'s real source, so the recogniser is measured
    on the exact shapes rather than asserted about.

    The three, as D-177 drove them at HEAD against the old one-line gate half:

      1. an inline ledger read spelled the way Python spells one;
      2. a second marker-existence read beside the real one;
      3. `_token_preconditions(` replaced outright, so the door called no
         routine at all.

    Every one of them left `leaked == []`. All three are named now, and the
    third is named by the presence assertion rather than by a reader set, which
    is why it needed a separate clause instead of a wider one.
    """
    real = textwrap.dedent(inspect.getsource(foundry_gate))

    # (1) The stdlib spelling of a ledger read. Outside `refusal_readers` by
    # construction — no routine makes it — so only the door window sees it.
    ledger = real.replace(
        "    if phase not in GATE_TO_TRANSITION:",
        "    extra = json.loads((fdir / 'defects.json').read_text(encoding='utf-8'))\n"
        "    if extra or phase not in GATE_TO_TRANSITION:",
        1,
    )
    assert ledger != real, "the plant did not apply; the anchor is measuring nothing"
    assert {"loads", "read_text"} <= _door_reads(ledger), sorted(_door_reads(ledger))
    assert _door_reads(ledger) != set(_DOOR_PROTOCOL_READS)

    # (2) A SECOND marker read. `_door_reads` collapses it onto the one
    # `<marker>.exists` entry the real gate already earns, which is exactly why
    # the receivers are compared as well.
    marker = real.replace(
        "    if phase not in GATE_TO_TRANSITION:",
        "    if (fdir / '.planted-marker').exists():\n"
        "        return {'phase': phase, 'passed': False, 'reason': 'planted'}\n"
        "    if phase not in GATE_TO_TRANSITION:",
        1,
    )
    assert marker != real, "the plant did not apply"
    assert _door_reads(marker) == set(_DOOR_PROTOCOL_READS), (
        "the name-level window cannot tell one marker read from two — which is "
        "the whole reason `_marker_receivers` exists"
    )
    assert "fdir / '.planted-marker'" in _marker_receivers(marker), (
        _marker_receivers(marker)
    )
    assert _marker_receivers(marker) != _marker_receivers(real)

    # (3) The dispatch removed. No reader set can see this: the door simply
    # stops calling anything, and an intersection with an empty side is empty.
    dropped = real.replace("_token_preconditions(", "dict(")
    assert dropped != real, "the plant did not apply"
    assert "_token_preconditions(" not in dropped
    assert _door_reads(dropped) == set(_DOOR_PROTOCOL_READS), (
        "the reader set is unchanged by removing the dispatch, which is why the "
        "presence assertion is a separate clause"
    )


def test_the_branch_window_catches_the_stdlib_spellings_of_a_ledger_read():
    """fallout AC-009 / FR-041 (D-161) — a read need not be one of OUR helpers.

    AC-009 requires an AST pin asserting that each gate branch and each
    transition branch "performs no other ledger or marker read". The recogniser
    knew `_load_json`, `read_document`, `read_jsonl`, `read_text_file`,
    `_read_text` and `<marker>.exists` — every reader this project wrote, and
    none of the two ways Python itself spells the same act.

    DRIVEN before the fix, both spellings returned the EMPTY SET from
    `_reads_in` over the branch window. No shipped branch uses either today,
    which is exactly why this is planted rather than scanned: a guard that is
    green over clean source is green whether it works or not.
    """
    readers = _refusal_readers()

    dunder_read = (
        "outcome = _start_cast_preconditions(fdir, project_root)\n"
        "extra = json.loads((fdir / 'defects.json').read_text(encoding='utf-8'))\n"
        "if extra:\n"
        "    return _transition_refusal(outcome, 'Cannot enter CAST')\n"
        "_update_phase(fdir, 'F1')\n"
    )
    pre, effect = _split_at_the_refusal(dunder_read)
    found = _reads_in(pre, readers)
    assert "loads" in found and "read_text" in found, (found, pre)
    # ...and the split still works, so the plant is judged in the right window.
    assert "_update_phase" in effect, effect

    open_read = (
        "outcome = _cast_preconditions(fdir, project_root)\n"
        "extra = json.load(open(fdir / 'defects.json'))\n"
        "if extra:\n"
        "    return _transition_refusal(outcome, 'Cannot mark CAST complete')\n"
    )
    pre_open, _ = _split_at_the_refusal(open_read)
    found_open = _reads_in(pre_open, readers)
    assert "load" in found_open and "open" in found_open, (found_open, pre_open)

    # The recogniser stays a NAME test, not a heuristic over argument shapes:
    # a call that is not a read leaks nothing.
    innocent = (
        "outcome = _done_preconditions(fdir, project_root)\n"
        "if not outcome['passed']:\n"
        "    return _transition_refusal(outcome, 'Cannot mark run DONE')\n"
    )
    pre_innocent, _ = _split_at_the_refusal(innocent)
    assert _reads_in(pre_innocent, readers) - {"_done_preconditions"} == set()


def test_the_branch_window_catches_a_read_no_routine_makes(monkeypatch):
    """fallout AC-009 / FR-041 / GI-011 (D-218) — THE ANCHOR FOR THE WIDER RULE.

    The branch window was judged against `_refusal_readers()`, a set DERIVED
    from the names the preconditions routines already reach. A read the
    routines do NOT make is therefore outside it BY CONSTRUCTION — and that is
    the exact read GI-011's violation column forbids, so the guard was green
    while the rule it encodes was narrower than AC-009 states. Same shape as
    the gate half D-177 closed with `_door_reads`, one window over.

    DRIVEN at ac89f59 with a positive control. An inline `_load_json` in the
    `temper` branch (the D-086 plant) went red as it should; a module-level
    private helper wrapping the identical read and called from the same place
    left the whole suite byte-identical to the clean baseline.

    Planted here rather than asserted about, because a scan over clean source
    is green whether it works or not — and this one was.
    """
    readers = _refusal_readers()

    # The plant, in the shape the drive used: a private helper the routines
    # never call, wrapping the read the branch is not allowed to make.
    planted = (
        "outcome = _temper_preconditions(fdir, project_root)\n"
        "_probe_unused = _peek_defect_ledger(fdir)\n"
        "if not outcome['passed']:\n"
        "    return _transition_refusal(outcome, 'Cannot enter TEMPER')\n"
        "_update_phase(fdir, 'F5')\n"
    )
    preconditions, effect = _split_at_the_refusal(planted)
    assert "_peek_defect_ledger" in preconditions and "_update_phase" in effect

    # (1) THE OLD RULE SEES NOTHING. Stated as the delta rather than described,
    # so the anchor measures the widening instead of asserting it happened.
    assert (
        _reads_in(preconditions, readers) - {"_temper_preconditions"} == set()
    ), sorted(_reads_in(preconditions, readers))

    # (2) THE RULE THE PIN NOW USES NAMES IT.
    assert _door_reads(preconditions) - {_BRANCH_REFUSAL_CALL} == {
        "_peek_defect_ledger"
    }, sorted(_door_reads(preconditions))

    # ...and a clean branch still leaks nothing, so the widening did not simply
    # make every window fail. The residue is the refusal renderer alone, which
    # is the window's own boundary and the reason it is excused by NAME.
    clean = (
        "outcome = _cast_preconditions(fdir, project_root)\n"
        "if not outcome['passed']:\n"
        "    return _transition_refusal(outcome, 'Cannot mark CAST complete')\n"
        "_update_phase(fdir, 'F2')\n"
    )
    pre_clean, _ = _split_at_the_refusal(clean)
    assert _door_reads(pre_clean) == {_BRANCH_REFUSAL_CALL}, sorted(
        _door_reads(pre_clean)
    )

    # The marker and the excused name are ONE spelling, so the window's end and
    # its exemption cannot drift into two answers.
    assert _BRANCH_REFUSAL_MARKER == f"return {_BRANCH_REFUSAL_CALL}("
    assert _BRANCH_REFUSAL_MARKER in clean


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
    # fallout AC-062 (D-088) — ONE COMPOSER NOW, not two. The `_halted_refusal`
    # guard above the chain WAS the second, and it was what made
    # `_halt_preconditions`' own halted rung unreachable through this door. The
    # rung is the routine's, so every refusal leaving here is the routine's
    # outcome rendered by the one shape.
    assert composed == {"_transition_refusal"}, sorted(composed)

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




def test_the_temper_door_refuses_a_run_that_did_not_ask_for_temper(run_env):
    """fallout FR-016 / FR-060 / GI-015 / GI-030 (D-143 / D-144) — the opt-in
    rung, at the DOOR.

    FR-016 is "Same split, but TEMPER STAYS OPT-IN"; FR-060 restates it and adds
    the PROVE arm, which was already implemented. Only the routing half of the
    opt-in existed: `_compute_next_action` sends F4 to F6 without the flag, so
    the rule held for a lead that followed the guidance and for nobody else.

    DRIVEN before the fix on a run with `state.json.temper` false:
    `Foundry-Gate('temper')` returned passed=True with NO `temper_enabled` row,
    and `Foundry-Phase('temper')` moved the run to F5.

    THE CONTROL is its sibling optional phase on the same run — `nyquist` has
    carried this rung all along — because "temper has no such rung" and "this
    fixture cannot provoke a config rung" look identical from a single failure.
    """
    project_root, fdir = run_env
    _arrange_passing(project_root, fdir, "temper")
    _write_state(fdir, phase="F4", cycle=1, temper=False, nyquist=False)
    _record_full_inspect_mode(fdir, cycle=1)

    _arm_ordering_token(fdir)
    gate = foundry_gate("temper", project_root)
    assert gate["passed"] is False, gate
    rows = {c["check"]: c for c in gate["checklist"]}
    assert rows["temper_enabled"]["ok"] is False, rows
    assert "opt-in" in gate["reason"], gate

    # The control: the sibling optional phase behaves the same way, which is
    # what makes this a rung and not a fixture artefact.
    _arm_ordering_token(fdir)
    control = foundry_gate("nyquist", project_root)
    assert control["passed"] is False, control
    assert {c["check"] for c in control["checklist"]} >= {"nyquist_enabled"}, control

    # And the TRANSITION refuses on the same named check, leaving F4 intact.
    _arm_ordering_token(fdir)
    result = foundry_mark_phase_complete("temper", project_root)
    assert result.get("ok") is not True, result
    assert "opt-in" in result["error"], result
    assert json.loads((fdir / "state.json").read_text(encoding="utf-8"))["phase"] == "F4"


def test_a_temper_off_run_that_entered_f5_could_never_reach_f6(run_env):
    """fallout FR-016 (D-143) — WHY the opt-in is a refusal and not a tidiness.

    `PHASE_TOKENS['done']`'s accepted_from is `('F5.5',)` if nyquist else
    `('F5',)` if temper else `('F4',)`. On a temper-off run that is `('F4',)`,
    so a run that crossed into F5 through the unguarded door was STRANDED:
    `Foundry-Gate('done')` refuses from F5 and F6 is unreachable, while
    `Foundry-Next` at F5 keeps returning `run_temper`.

    The trap is driven directly by putting a temper-off run at F5 — the state
    the old door produced — rather than by re-opening the door the fix closed.
    """
    project_root, fdir = run_env
    _arrange_passing(project_root, fdir, "done")
    _write_state(fdir, phase="F5", cycle=1, temper=False, nyquist=False)
    _record_full_inspect_mode(fdir, cycle=1)

    _arm_ordering_token(fdir)
    done = foundry_gate("done", project_root)
    assert done["passed"] is False, done
    # ...and the same run WITH the flag leaves through F5 cleanly, so the trap
    # is the missing opt-in and not something else about F5.
    _write_state(fdir, phase="F5", cycle=1, temper=True, nyquist=False)
    _record_full_inspect_mode(fdir, cycle=1)
    _arm_ordering_token(fdir)
    assert foundry_gate("done", project_root)["passed"] is True


def test_the_halt_door_reports_an_empty_text_without_refusing_it(run_env):
    """fallout FR-046 / CT-021 / AC-062 / OT-045 (D-121) — prose and behaviour.

    The door's refusal hint said `text` was required and the door accepted no
    text at all: driven with `reason='lead_ruling'` and no `text`, the
    transition returned ok=True and wrote `halted_reason {"reason":
    "lead_ruling", "text": ""}` — permanently, since nothing leaves HALTED.

    THE BEHAVIOUR IS SPECIFIED, so the prose is what moved. FR-046 gives this
    token three refusals and CT-021 / AC-062 / OT-045 each name three checks, so
    the emptiness is published as a NON-REFUSING fact — the shape GI-032 uses
    for `would_halt` — and the hint says what omitting `text` costs instead of
    claiming an enforcement this door does not make.
    """
    project_root, fdir = run_env
    _arrange_passing(project_root, fdir, "halt")
    # After start_cast the reason a lead may seal with is user_stop, on the
    # human's /foundry:stop token (the should-not-stop halt rule).
    _write_stop_token(fdir)

    # (1) The fact is reported, ok=False, and the gate still PASSES: three
    #     refusing checks, and this is not one of them.
    _arm_ordering_token(fdir)
    gate = foundry_gate("halt", project_root, reason="user_stop", text="")
    assert gate["passed"] is True, gate
    row = next(c for c in gate["checklist"] if c["check"].startswith("halt_text_present"))
    assert row["ok"] is False, row
    assert row["refuses"] is False, row
    assert "halt_text_present (chars=0)" == row["check"], row

    # (2) With text, the same row reports the length and passes.
    _arm_ordering_token(fdir)
    with_text = foundry_gate("halt", project_root, reason="user_stop", text="enough")
    row = next(c for c in with_text["checklist"] if c["check"].startswith("halt_text_present"))
    assert row["ok"] is True and "chars=6" in row["check"], row

    # (3) The hint no longer claims a requirement the door declines to make, and
    #     says what the omission costs instead.
    _arm_ordering_token(fdir)
    refused = foundry_gate("halt", project_root, reason="", text="")
    assert refused["passed"] is False, refused
    hint = refused["hint"]
    assert "recorded, not required" in hint, hint
    assert "nothing leaves HALTED" in hint, hint

    # (4) FR-046's three refusals are still exactly three: the new row refuses
    #     nothing, so an empty text still seals.
    _arm_ordering_token(fdir)
    sealed = foundry_mark_phase_complete("halt", project_root, reason="user_stop", text="")
    assert sealed.get("ok") is True, sealed
    state = json.loads((fdir / "state.json").read_text(encoding="utf-8"))
    assert state["phase"] == vocab.RUN_PHASE_HALTED, state
    assert state["halted_reason"] == {"reason": "user_stop", "text": ""}, state


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
    _write_stop_token(fdir)
    _teams_active(True)
    _arm_ordering_token(fdir)
    result = foundry_mark_phase_complete("halt", project_root, reason="user_stop", text="x")
    assert result.get("ok") is not True, result
    assert "Active teammates" in result["error"], result




def test_a_user_stop_seals_halted_with_the_member_the_text_and_the_history_row(run_env):
    """fallout FR-018 / FR-046 / CT-004 / ST-001 / AC-025 / AC-029 / OT-023.

    The whole contract in one drive: HALTED written through `_update_phase` so
    `phase_history` gains the row and `phase_times` closes the phase that was
    open, the reason stored as `{member, text}`, and the report regenerated with
    the lead's own prose carried.

    Rewritten for the should-not-stop halt rule: this drive used to seal a
    `lead_ruling` from F3, which that rule refuses. After start_cast the member
    a lead can seal with is `user_stop`, on the human's /foundry:stop token, so
    that is the seal driven; everything the contract says about the seal
    itself is asserted unchanged.
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
    _write_stop_token(fdir)
    _arm_ordering_token(fdir)

    result = foundry_mark_phase_complete(
        "halt", project_root, reason="user_stop", text="the spec is wrong"
    )
    assert result.get("ok") is True, result
    assert result["halted"] is True and result["phase"] == vocab.RUN_PHASE_HALTED, result

    state = json.loads((fdir / "state.json").read_text(encoding="utf-8"))
    assert state["phase"] == vocab.RUN_PHASE_HALTED, state
    assert state["halted_reason"] == {"reason": "user_stop", "text": "the spec is wrong"}
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
    _write_stop_token(fdir)
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




# --------------------------------------------------------------------------- #
# should-not-stop AC-011 / AC-012 / AC-013 / AC-015 / AC-016 / AC-027 / AC-035 /
# AC-036 / CT-006 / CT-007 / CT-010 / ST-005 / ST-006 / ST-007 / ST-015
# (A-005, A-011, A-023, A-029) — the post-CAST halt door: from start_cast to
# NYQUIST only the human ends a run. Qualified in the form casting 2 uses in
# this waived module; the lead's ruling on concern C-003 keeps these ids out of
# modules the citation convention scans.
# --------------------------------------------------------------------------- #

#: The human's stop command, whose own shell step writes the token this door
#: takes as proof.
_STOP_MD = Path(__file__).resolve().parents[3] / "commands" / "stop.md"


def _write_stop_token(fdir: Path, **overrides) -> dict:
    """An unused /foundry:stop token for ``fdir``, in the shape stop.md's shell
    step writes — `test_the_stop_commands_own_shell_step_writes_the_token_the_
    door_consumes` runs that step and holds the two shapes equal."""
    token = {
        vocab.STOP_TOKEN_FIELD_RUN: fdir.name,
        vocab.STOP_TOKEN_FIELD_CREATED_AT: now_iso(),
        vocab.STOP_TOKEN_FIELD_NONCE: "5eed" * 8,
        vocab.STOP_TOKEN_FIELD_CONSUMED_AT: None,
    }
    token.update(overrides)
    (fdir / vocab.STOP_TOKEN_FILENAME).write_text(json.dumps(token), encoding="utf-8")
    return token


def _set_phase(fdir: Path, phase: str) -> None:
    state = json.loads((fdir / "state.json").read_text(encoding="utf-8"))
    state["phase"] = phase
    (fdir / "state.json").write_text(json.dumps(state), encoding="utf-8")


def _run_phase(fdir: Path) -> str:
    return json.loads((fdir / "state.json").read_text(encoding="utf-8"))["phase"]


def _halt_at_both_doors(project_root, fdir: Path, *, reason: str, text: str = "the human's words"):
    _arm_ordering_token(fdir)
    gate = foundry_gate("halt", project_root, reason=reason, text=text)
    _arm_ordering_token(fdir)
    transition = foundry_mark_phase_complete("halt", project_root, reason=reason, text=text)
    return gate, transition


@pytest.mark.parametrize("phase", sorted(vocab.POST_CAST_RUN_PHASES))
@pytest.mark.parametrize("reason", ["lead_ruling", "spec_change_required", "cap_reached"])
def test_after_start_cast_the_lead_cannot_halt_on_its_own_ruling(run_env, phase, reason):
    """Refused at both doors, naming the same check, with the phase unchanged.

    A valid /foundry:stop token beside the call changes nothing, because what is
    refused is the lead choosing the reason, not a missing proof — and the
    token is not spent on a halt that did not happen. `cap_reached` is refused
    here too: it is written by the GRIND-opening doors, never supplied.
    """
    project_root, fdir = run_env
    _arrange_passing(project_root, fdir, "halt")
    _set_phase(fdir, phase)
    token = _write_stop_token(fdir)

    gate, transition = _halt_at_both_doors(project_root, fdir, reason=reason)

    assert gate["passed"] is False, gate
    assert transition.get("ok") is not True, transition
    assert _run_phase(fdir) == phase
    assert {r["reason"] for r in gate["refusals"]} == {
        r["reason"] for r in transition["refusals"]
    }, (gate["refusals"], transition["refusals"])
    assert f"halt reason {reason!r} is refused in {phase}" in transition["error"], transition
    assert "only the human can end a run" in transition["error"], transition
    assert "Foundry-Park" in transition["hint"], transition["hint"]
    row = next(
        c for c in gate["checklist"]
        if c["check"].startswith("halt_reason_accepted_after_start_cast")
    )
    assert row["ok"] is False and row["accepted"] == ["user_stop"], row
    assert json.loads(
        (fdir / vocab.STOP_TOKEN_FILENAME).read_text(encoding="utf-8")
    ) == token


def test_a_user_stop_with_no_human_origin_proof_is_refused_and_the_phase_stays(run_env):
    """The lead's word alone is not a user stop: no token, no halt answer, no halt."""
    project_root, fdir = run_env
    _arrange_passing(project_root, fdir, "halt")

    gate, transition = _halt_at_both_doors(project_root, fdir, reason="user_stop")

    assert gate["passed"] is False, gate
    assert transition.get("ok") is not True, transition
    assert _run_phase(fdir) == "F3"
    error = transition["error"]
    assert "needs proof the human asked for it" in error, error
    assert "no /foundry:stop token" in error, error
    assert "no parked question has been answered with halt" in error, error
    assert "/foundry:stop" in transition["hint"], transition["hint"]
    assert "Foundry-Park" in transition["hint"], transition["hint"]
    row = next(c for c in gate["checklist"] if c["check"].startswith("human_origin_proof"))
    assert row["ok"] is False and "proof=none" in row["check"], row


#: Every token that proves nothing, and the words the refusal names it by.
_UNUSABLE_TOKENS = {
    "missing": (None, "is absent"),
    "reused": ({"consumed_at": "2026-09-11T00:00:00+00:00"}, "already used"),
    "another_run": ({"run": "some-other-run"}, "names run 'some-other-run'"),
    "stale": ("stale", "is stale"),
    "future": ("future", "dated in the future"),
    "naive_stamp": ({"created_at": "2026-09-11T00:00:00"}, "no readable UTC created_at"),
    "unreadable": ("unreadable", "cannot be read"),
}


@pytest.mark.parametrize("case", sorted(_UNUSABLE_TOKENS))
def test_a_token_that_proves_nothing_is_refused_naming_why(run_env, case):
    from datetime import datetime, timedelta, timezone

    project_root, fdir = run_env
    _arrange_passing(project_root, fdir, "halt")
    arrangement, named = _UNUSABLE_TOKENS[case]
    now = datetime.now(tz=timezone.utc)
    token_path = fdir / vocab.STOP_TOKEN_FILENAME
    if arrangement == "stale":
        _write_stop_token(fdir, created_at=(
            now - timedelta(seconds=vocab.STOP_TOKEN_MAX_AGE_SECONDS + 120)
        ).isoformat())
    elif arrangement == "future":
        _write_stop_token(fdir, created_at=(now + timedelta(hours=2)).isoformat())
    elif arrangement == "unreadable":
        token_path.write_text("{ not json", encoding="utf-8")
    elif arrangement is not None:
        _write_stop_token(fdir, **arrangement)
    before = token_path.read_bytes() if token_path.exists() else None

    gate, transition = _halt_at_both_doors(project_root, fdir, reason="user_stop")

    assert gate["passed"] is False, gate
    assert transition.get("ok") is not True, transition
    assert named in transition["error"], (case, transition["error"])
    assert _run_phase(fdir) == "F3"
    assert (token_path.read_bytes() if token_path.exists() else None) == before


def test_a_stop_token_proves_one_halt_and_that_halt_consumes_it(run_env):
    """The gate reports the token and spends nothing; the transition seals
    HALTED with user_stop and the human's words, regenerates the report, and
    stamps the token consumed — so a second halt presenting it is refused, and
    refused as REUSED rather than as missing."""
    project_root, fdir = run_env
    _arrange_passing(project_root, fdir, "halt")
    token = _write_stop_token(fdir)
    token_path = fdir / vocab.STOP_TOKEN_FILENAME
    words = "Stopping for the night; the spec needs a rewrite."

    _arm_ordering_token(fdir)
    gate = foundry_gate("halt", project_root, reason="user_stop", text=words)
    assert gate["passed"] is True, gate
    assert gate["halt_proof"]["kind"] == vocab.HALT_PROOF_STOP_TOKEN, gate
    assert json.loads(token_path.read_text(encoding="utf-8")) == token

    _arm_ordering_token(fdir)
    sealed = foundry_mark_phase_complete("halt", project_root, reason="user_stop", text=words)

    assert sealed.get("ok") is True, sealed
    assert sealed["phase"] == vocab.RUN_PHASE_HALTED, sealed
    state = json.loads((fdir / "state.json").read_text(encoding="utf-8"))
    assert state["phase"] == vocab.RUN_PHASE_HALTED, state
    assert state["halted_reason"] == {"reason": "user_stop", "text": words}, state
    assert sealed["report_generated"] is True and (fdir / "REPORT.md").exists(), sealed
    assert sealed["halt_proof"]["kind"] == vocab.HALT_PROOF_STOP_TOKEN, sealed
    assert sealed["halt_proof"]["consumed"] is True, sealed
    assert "human-origin proof" in sealed["message"], sealed["message"]
    spent = json.loads(token_path.read_text(encoding="utf-8"))
    assert spent["nonce"] == token["nonce"] and spent["consumed_at"], spent

    _arm_ordering_token(fdir)
    again = foundry_mark_phase_complete("halt", project_root, reason="user_stop", text=words)
    assert again.get("ok") is not True and "HALTED" in again["error"], again

    # The same token on a run that is live again proves nothing either.
    _set_phase(fdir, "F3")
    _arm_ordering_token(fdir)
    reused = foundry_mark_phase_complete("halt", project_root, reason="user_stop", text=words)
    assert reused.get("ok") is not True, reused
    assert "already used" in reused["error"], reused


@pytest.mark.parametrize("phase", ["F0", "F0.5", "F0.9"])
@pytest.mark.parametrize("reason", sorted(vocab.HALT_REASONS))
def test_before_cast_every_halt_is_exactly_what_it_was(run_env, phase, reason):
    """Pre-CAST the door makes its three checks and nothing else: every member
    is accepted without a token, no checklist row is added, the gate result
    carries exactly the keys it always did, and the seal names no proof."""
    project_root, fdir = run_env
    _arrange_passing(project_root, fdir, "halt")
    _set_phase(fdir, phase)

    _arm_ordering_token(fdir)
    gate = foundry_gate("halt", project_root, reason=reason, text="before the build")
    assert gate["passed"] is True, gate
    assert set(gate) == {"halt_reason", "halt_text", "phase", "passed", "checklist"}, sorted(gate)
    assert [c["check"].split(" (")[0] for c in gate["checklist"]] == [
        "halt_reason_is_a_member", "halt_text_present", "no_active_teams",
        "not_already_halted",
    ], gate["checklist"]

    _arm_ordering_token(fdir)
    sealed = foundry_mark_phase_complete(
        "halt", project_root, reason=reason, text="before the build"
    )
    assert sealed.get("ok") is True, sealed
    assert "halt_proof" not in sealed, sealed
    assert json.loads((fdir / "state.json").read_text(encoding="utf-8"))[
        "halted_reason"
    ] == {"reason": reason, "text": "before the build"}


@pytest.mark.parametrize("token", ["grind_start", "assay_fail"])
def test_the_launch_cap_still_seals_cap_reached_with_no_human_proof(run_env, token):
    """The cap reaches HALTED through the GRIND-opening doors, never through the
    halt door's reason rule, so it needs no token and names no proof."""
    project_root, fdir = run_env
    _arrange_passing(project_root, fdir, token)
    _write_state(fdir, phase="F2" if token == "grind_start" else "F4",
                 cycle=2, max_cycles=2)
    _record_full_inspect_mode(fdir, cycle=2)
    assert not (fdir / vocab.STOP_TOKEN_FILENAME).exists()
    _arm_ordering_token(fdir)

    result = foundry_mark_phase_complete(token, project_root)

    assert result.get("ok") is True and result["halted"] is True, result
    assert result["halted_reason"] == vocab.HALT_REASON_CAP_REACHED, result
    assert "halt_proof" not in result, result
    state = json.loads((fdir / "state.json").read_text(encoding="utf-8"))
    assert state["phase"] == vocab.RUN_PHASE_HALTED, state
    assert state["halted_reason"]["reason"] == vocab.HALT_REASON_CAP_REACHED, state


def _stop_md_shell_step() -> str:
    """The one fenced shell step in stop.md — exactly the block Claude Code runs
    when the human invokes the command."""
    text = _STOP_MD.read_text(encoding="utf-8")
    blocks = re.findall(r"```!\s*\n?([\s\S]*?)\n?```", text)
    assert len(blocks) == 1, blocks
    return blocks[0]


def _run_stop_step(project_root) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", "-c", _stop_md_shell_step()], cwd=project_root,
        capture_output=True, text=True, timeout=60,
    )


_NO_SHELL = shutil.which("bash") is None or shutil.which("python3") is None


@pytest.mark.skipif(_NO_SHELL, reason="the stop step needs bash and python3 on PATH")
def test_the_stop_commands_own_shell_step_writes_the_token_the_door_consumes(run_env):
    """stop.md's shell step, run as the human's invocation runs it, writes the
    token for the active run — the live one whose state.json changed last —
    in the vocabulary's shape, and the halt door takes it as proof."""
    import os
    from datetime import datetime

    project_root, fdir = run_env
    _arrange_passing(project_root, fdir, "halt")
    archive = fdir.parent
    for name, phase in (
        ("older-live", "F2"), ("newer-precast", "F0.5"),
        ("newer-halted", vocab.RUN_PHASE_HALTED),
    ):
        (archive / name).mkdir()
        (archive / name / "state.json").write_text(
            json.dumps({"phase": phase}), encoding="utf-8",
        )
    older = (fdir / "state.json").stat().st_mtime - 3600
    os.utime(archive / "older-live" / "state.json", (older, older))

    ran = _run_stop_step(project_root)

    assert ran.returncode == 0, ran.stderr
    assert fdir.name in ran.stdout, ran.stdout
    written = json.loads((fdir / vocab.STOP_TOKEN_FILENAME).read_text(encoding="utf-8"))
    assert list(written) == list(vocab.STOP_TOKEN_FIELDS), written
    assert written["run"] == fdir.name and written["consumed_at"] is None, written
    assert datetime.fromisoformat(written["created_at"]).tzinfo is not None, written
    for other in ("older-live", "newer-precast", "newer-halted"):
        assert not (archive / other / vocab.STOP_TOKEN_FILENAME).exists(), other

    # The step spells, in shell, the vocabulary it cannot import.
    step = _stop_md_shell_step()
    live = ast.literal_eval(re.search(r"LIVE_PHASES = (\([^)]*\))", step).group(1))
    assert set(live) == set(vocab.POST_CAST_RUN_PHASES), live
    assert re.search(r'TOKEN_FILENAME = "([^"]+)"', step).group(1) == vocab.STOP_TOKEN_FILENAME

    _arm_ordering_token(fdir)
    sealed = foundry_mark_phase_complete(
        "halt", project_root, reason="user_stop", text="the human's stop",
    )
    assert sealed.get("ok") is True, sealed
    assert sealed["halt_proof"]["consumed"] is True, sealed


@pytest.mark.skipif(_NO_SHELL, reason="the stop step needs bash and python3 on PATH")
def test_the_stop_step_writes_no_token_when_no_run_is_in_the_build(run_env):
    project_root, fdir = run_env
    _write_state(fdir, phase="F0.5", cycle=0)

    ran = _run_stop_step(project_root)

    assert ran.returncode == 0, ran.stderr
    assert "no token was written" in ran.stdout, ran.stdout
    assert not (fdir / vocab.STOP_TOKEN_FILENAME).exists()


def test_the_stop_command_stops_agents_then_seals_user_stop_and_names_no_removed_team_tool():
    text = _STOP_MD.read_text(encoding="utf-8")

    assert 'disable-model-invocation: "true"' in text
    assert '"Bash(python3:*)"' in text, "the shell step needs its own grant"
    assert "Foundry-Phase(phase='halt', reason='user_stop'" in text
    for step in ("SendMessage", "TaskStop", "Foundry-Team-Down"):
        assert step in text, step
    assert text.index("STOP EVERY IN-FLIGHT AGENT") < text.index("SEAL THE RUN")
    assert "already HALTED" in text
    # The active-run rule, in the words the Stop hook matches.
    assert (
        "whose `state.json` phase is F1..F5.5 and not HALTED or DONE" in text
    )
    for removed in ("TeamCreate", "TeamDelete"):
        assert removed not in text, removed


def test_no_tool_this_server_advertises_names_a_removed_team_tool():
    from foundry_mcp import server as foundry_server

    for tool in asyncio.run(foundry_server.list_tools()):
        advertised = (tool.description or "") + json.dumps(tool.inputSchema)
        for removed in ("TeamCreate", "TeamDelete"):
            assert removed not in advertised, (tool.name, removed)




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




@pytest.mark.parametrize("counter", ["four", None, -3, 2.5, [5], {"n": 5}])
def test_an_unusable_cycle_counter_does_not_open_the_concern_door(run_env, counter):
    """fallout ST-005 / GI-023 / AC-004 / OT-004 (D-180, concern C-082) — THE
    SECOND ROUTE TO D-158's FAIL-OPEN.

    D-158 closed the route through an unreadable LEDGER and named this one in a
    comment, deferring it to C-082 because the distinguisher belonged to another
    casting's file. C-082 is closed and the fail-open was still live, so it is
    closed here from the leaf reader that already answers the question.

    THE ROUTE. The rung scopes on exact cycle equality, and `current_cycle`
    answers 0 for a missing, absent OR MALFORMED counter — deliberately, so
    every reader gets a usable integer. A `state.json` carrying `"cycle":
    "four"` therefore made the rung look for CYCLE-0 concerns only, and the
    concern filed at cycle 5 was invisible: `no_open_cross_casting_concerns (0)`
    ok True, the routine passed, and the real `Foundry-Phase('inspect_start')`
    SUCCEEDED — F3 to F2 with the cross-casting concern still open, which is
    GI-023's named violation reached through the counter instead of the ledger.

    Driven over every shape `as_count` folds onto 0 — a string, null, a
    negative, a float, a list and a mapping — because "malformed" is a set, not
    an example, and a fix keyed to one spelling would leave the other five.
    """
    from foundry_mcp.tools.concerns import foundry_concern

    project_root, fdir = run_env
    _manifest_with_requirement_ids(fdir, {
        1: (["FR-007"], ["src/one.py"]),
        2: (["FR-007"], ["src/two.py"]),
    })
    _write_state(fdir, phase="F3", cycle=5)
    opened = foundry_concern(
        casting_id=1, cycle=5, target="src/two.py",
        text="the fix reaches casting 2's own spelling of this rule",
        project_root=project_root,
    )
    assert opened.get("error") is None, opened
    concern_id = opened["concern"]["id"]

    # The control, on the untouched counter: the rung refuses by id.
    _arm_ordering_token(fdir)
    control = foundry_mark_phase_complete("inspect_start", project_root)
    assert control.get("ok") is not True and concern_id in control["error"], control

    # Now break the counter and nothing else.
    state = json.loads((fdir / "state.json").read_text(encoding="utf-8"))
    state["cycle"] = counter
    (fdir / "state.json").write_text(json.dumps(state), encoding="utf-8")
    assert current_cycle(fdir) == 0, (counter, current_cycle(fdir))

    # The GATE reports it as a failing rung...
    reported = foundry_gate("inspect_start", project_root)
    rung = [c for c in reported.get("checklist", [])
            if c["check"].startswith("no_open_cross_casting_concerns")]
    assert rung and rung[0]["ok"] is False, (counter, reported)

    # ...and the TRANSITION refuses on it, naming the concern, with the run's
    # recorded phase unchanged. This is the assertion that was False for all
    # six shapes: the call SUCCEEDED and the phase advanced to F2.
    _arm_ordering_token(fdir)
    refused = foundry_mark_phase_complete("inspect_start", project_root)
    assert refused.get("ok") is not True, (counter, refused)
    assert concern_id in refused["error"], (counter, refused)
    assert json.loads(
        (fdir / "state.json").read_text(encoding="utf-8")
    )["phase"] == "F3", counter




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




def test_a_wrong_shaped_concern_ledger_refuses_instead_of_answering_none(run_env):
    """fallout ST-005 / GI-023 / AC-004 (D-158) — the rung failed OPEN on a
    concerns.json that is valid JSON and the wrong shape.

    ST-005's guard is "`_inspect_start_preconditions` finds a concern from the
    closing GRIND still open" and GI-023's violation column is "opening INSPECT
    with an open cross-casting concern from the closing GRIND". The rung built
    its list from `foundry_state.open_cross_casting_concerns`, which reads the
    document and DISCARDS the problem — so every `concerns` cell that is not a
    list of mappings answered as the empty list and the door opened.

    DRIVEN before the fix at F3 cycle 4 with a manifest of two castings: a filed
    C-001 refused by id, and rewriting concerns.json to {"concerns": "C-001
    open"} or {"concerns": ["C-001"]} made the SAME call pass. Unparseable JSON
    was caught by `_artifact_guard`, so the hole was exactly the
    valid-JSON-wrong-shape route. Each shape below is one of the three ways that
    route is reachable.
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
    concern_id = opened["concern"]["id"]

    # The control: an intact ledger refuses by id, as it always has.
    assert concern_id in _inspect_start_preconditions(fdir, project_root)["reason"]

    for body in (
        '{"concerns": "C-001 open"}',      # the cell is a string
        '{"concerns": ["C-001"]}',         # a list of non-records
        '{"filed": []}',                   # no cell at all
    ):
        (fdir / "concerns.json").write_text(body, encoding="utf-8")
        outcome = _inspect_start_preconditions(fdir, project_root)
        assert outcome["passed"] is False, (body, outcome)
        assert "concern ledger cannot be read" in outcome["reason"], (body, outcome)
        # ...and the refusal names the FILE, so the operator repairs the
        # artifact rather than hunting for a concern that cannot be read.
        assert "concerns.json" in outcome["reason"] + outcome["hint"], outcome

    # ABSENT IS STILL ABSENT: a run that filed no concern has no ledger, and
    # every run starts that way. If this rung fired on absence it would block
    # the first INSPECT of every run, which is the opposite failure.
    (fdir / "concerns.json").unlink()
    assert _inspect_start_preconditions(fdir, project_root)["passed"] is True


def test_the_concern_scope_still_narrows_to_the_counter_the_leaf_reports(run_env):
    """fallout ST-005 / GI-023 / AC-004 (D-158, D-180, concern C-082) — THE
    INVERSION THIS TEST WAS WRITTEN TO WAIT FOR, AND THE HALF IT MUST KEEP.

    It used to assert the FAIL-OPEN: with a malformed counter the rung reported
    `no_open_cross_casting_concerns` ok True and a concern filed at cycle 4 went
    unseen. Its own docstring said it "INVERTS the day the leaf reader lands —
    which is what makes it the anchor for that change rather than an excuse for
    the gap". The reader landed (`derive_cycle_count`'s `sources.state_cycle`,
    which is the (value, problem) distinction C-082 asked for, already in the
    leaf), so this is the inverted half; the malformed shapes themselves are
    driven one file down in
    `test_an_unusable_cycle_counter_does_not_open_the_concern_door`.

    WHAT THIS ASSERTS NOW IS THE OTHER DIRECTION, which the widening must not
    have destroyed: on a HEALTHY counter the scope still NARROWS. ST-005 is
    about "a concern from the closing GRIND", so a concern stamped with a
    different cycle does not hold this door — and a fix that answered "every
    open concern, always" would have closed the fail-open by blocking crossings
    ST-005 never asked to block.
    """
    from foundry_mcp.tools.concerns import foundry_concern

    project_root, fdir = run_env
    _manifest_with_requirement_ids(fdir, {
        1: (["FR-007"], ["src/one.py"]),
        2: (["FR-007"], ["src/two.py"]),
    })
    _write_state(fdir, phase="F3", cycle=4)
    opened = foundry_concern(
        casting_id=1, cycle=4, target="src/two.py",
        text="the fix reaches casting 2's own spelling of this rule",
        project_root=project_root,
    )
    concern_id = opened["concern"]["id"]
    # The control: with the counter intact and the stamps agreeing, it refuses.
    assert concern_id in _inspect_start_preconditions(fdir, project_root)["reason"]

    # The counter moves on, honestly. The concern is now from an EARLIER GRIND,
    # and this door is about the one that just closed.
    _write_state(fdir, phase="F3", cycle=5)
    assert current_cycle(fdir) == 5
    outcome = _inspect_start_preconditions(fdir, project_root)
    named = [c for c in outcome["checklist"]
             if c["check"].startswith("no_open_cross_casting_concerns")]
    assert named and named[0]["ok"] is True, outcome["checklist"]


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
