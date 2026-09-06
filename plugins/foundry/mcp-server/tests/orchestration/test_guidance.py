"""Foundry-Next and Foundry-Context: what the lead is told to do.

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
import tempfile
import textwrap
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from foundry_mcp.schemas import vocab
from foundry_mcp.schemas.vocab import RUN_PHASE_HALTED
from foundry_mcp.tools import artifacts, foundry_state
from foundry_mcp.tools.foundry_state import now_iso

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
    _defect_ledger,
    _halted_run,
    _teams_active,
    _tiered,
    _write_manifest_with_castings,
    _write_prove,
    _write_spec,
    _write_state,
    run_env,
)

from foundry_mcp.tools.orchestration.directives import (  # noqa: F401
    foundry_defects_to_tasks,
)

from foundry_mcp.tools.orchestration.gates import (  # noqa: F401
    foundry_gate,
)

from foundry_mcp.tools.orchestration.guidance import (  # noqa: F401
    _ACTION_IMPERATIVES,
    _waiting_on_agents,
    _GATE_THEN_PHASE_EXCEPTION,
    _GATE_THEN_PHASE_NOTE,
    _STANDING_CRITICAL_RULES,
    _compute_next_action,
    _format_status_display,
    foundry_get_context,
    foundry_next_action,
)

from foundry_mcp.tools.orchestration.streams import (  # noqa: F401
    _check_streams_complete,
    foundry_mark_stream,
)

from foundry_mcp.tools.orchestration.teams import (  # noqa: F401
    _check_active_teams,
)

from foundry_mcp.tools.orchestration.transitions import (  # noqa: F401
    foundry_mark_phase_complete,
)

from tests.orchestration._env import (  # noqa: F401
    _CORRUPTIBLE_ARTIFACTS,
    _MALFORMED_BODIES,
    _corrupt,
    _plain,
    _progressing_ledger,
    _router_defect,
    _router_ledger,
    _stale_stall_clock,
    _stalled_ledger,
    _streams_done,
)




# --------------------------------------------------------------------------- #
# P3 — verdict synthesis on clean PROVE (FR-003 / FR-004 / ST-001)
# --------------------------------------------------------------------------- #


def test_clean_prove_autopass_synthesizes_verified_verdict_per_id(run_env):
    """AC FR-004: clean-PROVE auto-pass writes one VERIFIED row per spec ID."""
    project_root, fdir = run_env
    ids = ["FR-1", "FR-2", "US-3", "AC-4"]
    _write_spec(fdir, ids)
    _write_state(fdir, phase="F4", temper=False)
    _write_prove(fdir, items_checked=len(ids), items_total=len(ids), findings=0)
    # verdicts.json does not exist yet — .prove-complete stores only aggregates.
    assert not (fdir / "verdicts.json").exists()

    result = _compute_next_action(project_root)

    assert result["action"] == "transition_to_done"
    verdicts = json.loads((fdir / "verdicts.json").read_text(encoding="utf-8"))
    got = {r["id"]: r["verdict"] for r in verdicts["requirements"]}
    assert set(got) == set(ids)
    assert all(v == "VERIFIED" for v in got.values())




# --------------------------------------------------------------------------- #
# P4 — ordering-token / stall-clock decouple (FR-005 / FR-008)
# --------------------------------------------------------------------------- #


def test_stall_clock_decoupled_from_ordering_token(run_env):
    """AC FR-005 (decouple): the stall clock reads ``.last-next-at`` — which
    gate/phase never unlink — so a consumed ordering token does NOT blind the
    watchdog. A large gap still warns even when ``.next-action-called`` is
    gone."""
    project_root, fdir = run_env
    _write_state(fdir, phase="F0")

    # Simulate: a prior Foundry-Next stamped .last-next-at 200s ago, and an
    # intervening gate/phase consumed (unlinked) the ordering token.
    old = (datetime.now(timezone.utc) - timedelta(seconds=200)).isoformat()
    (fdir / ".last-next-at").write_text(f"{old}\n", encoding="utf-8")
    assert not (fdir / ".next-action-called").exists()

    result = foundry_next_action(project_root)

    assert result.get("stall_detected_seconds", 0) >= 180
    assert "STALL DETECTED" in result["instructions"]
    # Both markers are (re)written by Foundry-Next.
    assert (fdir / ".next-action-called").exists()
    assert (fdir / ".last-next-at").exists()




def test_real_stall_still_warns(run_env):
    """AC FR-008: a genuine >180s gap between Foundry-Next calls warns."""
    project_root, fdir = run_env
    _write_state(fdir, phase="F0")

    old = (datetime.now(timezone.utc) - timedelta(seconds=240)).isoformat()
    (fdir / ".last-next-at").write_text(f"{old}\n", encoding="utf-8")
    (fdir / ".next-action-called").write_text(f"{old}\n", encoding="utf-8")

    result = foundry_next_action(project_root)

    assert result.get("stall_detected_seconds", 0) >= 180
    assert "STALL DETECTED" in result["instructions"]




def test_no_false_stall_on_recent_activity(run_env):
    """AC FR-008 (no false positive): back-to-back Foundry-Next calls with a
    tiny gap do NOT warn."""
    project_root, fdir = run_env
    _write_state(fdir, phase="F0")

    foundry_next_action(project_root)  # stamps .last-next-at = now
    result = foundry_next_action(project_root)  # gap ~0s

    assert "stall_detected_seconds" not in result
    assert "STALL DETECTED" not in result["instructions"]




@pytest.mark.parametrize(
    "bad_cycle",
    ["seven", None, [], {"a": 1}, -3, 2.5, True],
    ids=["str", "null", "list", "dict", "negative", "float", "bool"],
)
def test_malformed_state_cycle_leaves_next_and_context_answering(run_env, bad_cycle):
    """AC-008 / FR-005 on the defect path and its nearest neighbour.

    Foundry-Next is the mandatory handshake before EVERY phase transition and
    EVERY gate, and commands/start.md makes it the universal loop step, so an
    unhandled raise there wedges the run with no recovery path through the
    protocol. Driven over _DISPATCH because that is the surface the lead
    actually calls, and because jsonschema validates the ARGUMENTS -- nothing
    validates the state file the handler then reads.
    """
    from foundry_mcp import server as foundry_server

    project_root, fdir = run_env
    _write_state(fdir, phase="F2", cycle=bad_cycle)
    _write_manifest_with_castings(fdir, ["src/api/handler.py"], no_ui=True)

    previous_root = foundry_server._project_root
    try:
        foundry_server._project_root = project_root

        nxt = foundry_server._DISPATCH["Foundry-Next"]({})
        ctx = foundry_server._DISPATCH["Foundry-Context"]({})
    finally:
        foundry_server._project_root = previous_root

    # Answers rather than raising...
    #
    # Read through the rendered display rather than the old `context_budget`
    # block, which is gone: it mapped the cycle counter onto the words
    # low/moderate/high/critical and called the result "estimated_usage",
    # reading no tokens and no durations. AC-033 replaced it with the spend the
    # lead actually reported. The claim this test makes was never about that
    # block — it is that a malformed counter still NORMALISES to 0 everywhere
    # it surfaces, and the display is where Foundry-Next surfaces it.
    assert "Cycle: 0" in nxt["display"]
    assert ctx["state"]["cycle"] == 0
    # ...and normalises rather than passing the malformed value through.
    assert isinstance(ctx["state"]["cycle"], int)
    assert not isinstance(ctx["state"]["cycle"], bool)
    # The status display renders the normalised counter, not the raw value.
    assert "Cycle: 0" in _format_status_display(project_root)




def test_valid_state_cycle_still_reaches_next_and_context_unchanged(run_env):
    """NFR-002: the fix normalises malformed values, it does not flatten real
    ones. A run whose counter genuinely reads 4 still reports 4."""
    from foundry_mcp import server as foundry_server

    project_root, fdir = run_env
    _write_state(fdir, phase="F2", cycle=4)
    _write_manifest_with_castings(fdir, ["src/api/handler.py"], no_ui=True)

    previous_root = foundry_server._project_root
    try:
        foundry_server._project_root = project_root
        nxt = foundry_server._DISPATCH["Foundry-Next"]({})
        ctx = foundry_server._DISPATCH["Foundry-Context"]({})
    finally:
        foundry_server._project_root = previous_root

    assert "Cycle: 4" in nxt["display"]
    assert ctx["state"]["cycle"] == 4
    assert "Cycle: 4" in _format_status_display(project_root)




# --- the ADJACENT path: a written record, not a response field -------------- #


@pytest.mark.parametrize("bad_cycle", ["seven", -3, 2.5], ids=["str", "negative", "float"])
def test_malformed_state_cycle_never_reaches_synthesized_verdicts(run_env, bad_cycle):
    """D-059 adjacent-path test (AC-013).

    The defect was found on Foundry-Next's context-budget path, where a bad
    value lands in a response field. This drives a DIFFERENT caller and a
    DIFFERENT transition: _compute_next_action's F4 clean-PROVE auto-pass,
    which passes the same read as ``cycle=`` into
    ``_synthesize_clean_prove_verdicts`` -- and that stamps it onto EVERY
    synthesized row of verdicts.json. Here the consequence is persisted data
    that outlives the call, and 'seven' never raises on this path because
    nothing compares it; it is simply written.
    """
    project_root, fdir = run_env
    ids = ["FR-1", "FR-2", "US-3"]
    _write_spec(fdir, ids)
    _write_state(fdir, phase="F4", temper=False, cycle=bad_cycle)
    _write_prove(fdir, items_checked=len(ids), items_total=len(ids), findings=0)

    result = _compute_next_action(project_root)
    assert result["action"] == "transition_to_done"

    rows = json.loads((fdir / "verdicts.json").read_text(encoding="utf-8"))["requirements"]
    assert {r["id"] for r in rows} == set(ids)
    for row in rows:
        assert row["cycle"] == 0, f"{row['id']} carries the malformed state cycle"
        assert isinstance(row["cycle"], int) and not isinstance(row["cycle"], bool)




def test_valid_state_cycle_is_stamped_on_synthesized_verdicts(run_env):
    """The same adjacent path with a real counter: the value is carried, not
    zeroed. Without this the test above would pass on a hardcoded 0."""
    project_root, fdir = run_env
    ids = ["FR-1", "FR-2"]
    _write_spec(fdir, ids)
    _write_state(fdir, phase="F4", temper=False, cycle=5)
    _write_prove(fdir, items_checked=len(ids), items_total=len(ids), findings=0)

    assert _compute_next_action(project_root)["action"] == "transition_to_done"

    rows = json.loads((fdir / "verdicts.json").read_text(encoding="utf-8"))["requirements"]
    assert [r["cycle"] for r in rows] == [5, 5]




# The orchestrator entry points reachable over MCP that read run artifacts.
def _entry_point_calls(project_root: str) -> dict:
    return {
        "Foundry-Next": lambda: foundry_next_action(project_root=project_root),
        "Foundry-Context": lambda: foundry_get_context(project_root=project_root),
        "Foundry-Phase": lambda: foundry_mark_phase_complete("inspect_start", project_root),
        "Foundry-Gate": lambda: foundry_gate("done", project_root=project_root),
        "Foundry-Stream": lambda: foundry_mark_stream(
            "trace", cycle=0, items_checked=1, items_total=1, project_root=project_root
        ),
        "Foundry-Tasks": lambda: foundry_defects_to_tasks(project_root=project_root),
    }




def _drive_matrix_cell(artifact: str, body: str, guarded: bool) -> str:
    """Drive one (artifact, malformed body) pair through every entry point.

    ``guarded=False`` restores the PRE-fix reader — the raw
    ``json.loads(path.read_text())`` and no ``_artifact_guard`` — which is the
    state group E's matrix was driven against. Shared by the pins below and by
    this casting's evidence command, so the demonstration and the assertion
    cannot drift apart.
    """
    import tempfile

    from foundry_mcp.tools import foundry_state as _fs

    root = Path(tempfile.mkdtemp())
    fdir = root / "foundry-archive" / "matrix"
    (fdir / "castings").mkdir(parents=True)
    (fdir / artifact).write_text(body, encoding="utf-8")
    _fs.set_active_run("matrix")

    # fallout FR-004: the team scan is rebound on the MODULE that defines it and
    # on every module that imported the name, because after the carve there is a
    # binding per importer and a local of the same name reaches none of them.
    def _bind(name, value):
        """Rebind `name` on every module that carries it; return the originals.

        fallout FR-004: after the carve a symbol is imported BY NAME into each
        caller's namespace, so setting it on the module that DEFINES it reaches
        none of the importers. This drive's whole subject is what the doors do
        when the primitive misbehaves, so the primitive has to misbehave for
        all of them or the table below measures nothing.
        """
        originals = {}
        for module in (*ORCHESTRATION, artifacts):
            if name in vars(module):
                originals[module] = vars(module)[name]
                setattr(module, name, value)
        return originals

    real_teams = _bind(
        "_check_active_teams",
        lambda _p: {"active": False, "teams": [], "live_panes": []},
    )
    real_load: dict = {}
    real_guard: dict = {}
    if not guarded:
        real_load = _bind(
            "_load_json",
            lambda p: (json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}),
        )
        real_guard = _bind("_artifact_guard", lambda _f: None)
    try:
        raised, named = 0, 0
        for _tool, call in _entry_point_calls(str(root)).items():
            try:
                if Path(artifact).name in json.dumps(call()):
                    named += 1
            except Exception:
                raised += 1
        if raised:
            return "BRICKS %d/6" % raised
        return "names %d/6" % named if named else "silent    "
    finally:
        for name, originals in (("_load_json", real_load),
                                ("_artifact_guard", real_guard),
                                ("_check_active_teams", real_teams)):
            for module, original in originals.items():
                setattr(module, name, original)
        _fs.clear_active_run()




def render_corruption_matrix(guarded: bool) -> str:
    """The 24-combination matrix as a printable table. Used by the evidence log."""
    bodies = [(p.id, p.values[0]) for p in _MALFORMED_BODIES]
    head = (
        "post-fix: tolerant loader + _artifact_guard at every entry point"
        if guarded
        else "PRE-fix: raw json.loads(read_text()), no shape check, no guard"
    )
    out = ["== %s ==" % head,
           "   %-24s %s" % ("artifact", "  ".join("%-12s" % n for n, _ in bodies))]
    bricked = named = 0
    for artifact in _CORRUPTIBLE_ARTIFACTS:
        cells = []
        for _name, body in bodies:
            verdict = _drive_matrix_cell(artifact, body, guarded)
            bricked += verdict.startswith("BRICKS")
            named += verdict.startswith("names")
            cells.append("%-12s" % verdict)
        out.append("   %-24s %s" % (artifact, "  ".join(cells)))
    out.append(
        "   -> %d of 24 brick at least one tool, %d of 24 name the offending file"
        % (bricked, named)
    )
    return "\n".join(out)




def test_the_matrix_bricks_before_the_fix_and_names_after():
    """The headline numbers, asserted rather than only demonstrated.

    Group E's audit: 24/24 bricked a tool and NOT ONE named the offending
    file. The pre-fix arm reproduces the bricking; the post-fix arm must brick
    nothing and name everything.
    """
    before = render_corruption_matrix(guarded=False)
    bricked_before = int(before.rsplit("-> ", 1)[1].split(" of 24")[0])
    assert bricked_before >= 20, before
    assert "0 of 24 name the offending file" in before

    after = render_corruption_matrix(guarded=True)
    assert "BRICKS" not in after
    assert "0 of 24 brick at least one tool, 24 of 24 name" in after




@pytest.mark.parametrize("artifact", _CORRUPTIBLE_ARTIFACTS)
@pytest.mark.parametrize("body", _MALFORMED_BODIES)
def test_a_malformed_artifact_refuses_by_name_instead_of_raising(run_env, artifact, body):
    """The 24-combination matrix, driven through every affected entry point.

    Two assertions, and the second is the one group E's audit was really
    about: 24/24 bricked a tool and NOT ONE named the offending file.
    """
    project_root, fdir = run_env
    _corrupt(fdir, artifact, body)

    for tool, call in _entry_point_calls(project_root).items():
        result = call()  # must not raise

        assert isinstance(result, dict), f"{tool} returned {type(result).__name__}"
        named = json.dumps(result)
        assert Path(artifact).name in named, (
            f"{tool} did not name {artifact} in its refusal: {named[:300]}"
        )




def test_the_refusal_carries_the_house_error_and_hint_shape(run_env):
    project_root, fdir = run_env
    _corrupt(fdir, "state.json", "[1, 2, 3]")

    result = foundry_next_action(project_root=project_root)

    assert "state.json" in result["error"]
    assert "list" in result["error"]  # names WHAT it found, not just that it failed
    assert result["hint"]
    assert result["corrupt_artifacts"]




def test_every_corrupt_artifact_is_named_not_just_the_first(run_env):
    """A run with three broken files must not send the operator round three
    times. The scan is derived over the run dir, so it reports all of them."""
    project_root, fdir = run_env
    for artifact in ("state.json", "defects.json", "verdicts.json"):
        _corrupt(fdir, artifact, "null")

    result = foundry_next_action(project_root=project_root)

    assert len(result["corrupt_artifacts"]) == 3
    for artifact in ("state.json", "defects.json", "verdicts.json"):
        assert any(artifact in p for p in result["corrupt_artifacts"])




def test_a_new_artifact_is_covered_without_being_enrolled(run_env):
    """Derived membership. The scan globs the run dir rather than consulting a
    hand-kept list, so an artifact nobody remembered to enrol is still caught —
    which is the failure mode the marker lists in this module keep repeating."""
    project_root, fdir = run_env
    _corrupt(fdir, "some-future-artifact.json", "[]")

    result = foundry_next_action(project_root=project_root)

    assert any("some-future-artifact.json" in p for p in result["corrupt_artifacts"])




def test_an_absent_artifact_is_not_a_problem(run_env):
    """A run legitimately has artifacts it has not written yet. Only a file that
    EXISTS and cannot be read is a refusal."""
    project_root, fdir = run_env

    assert artifacts._run_artifact_problems(fdir) == []
    assert artifacts._artifact_guard(fdir) is None
    assert "corrupt_artifacts" not in foundry_next_action(project_root=project_root)




def test_foundry_context_does_not_reset_the_stall_clock(run_env):
    """OT-028's second half: 'Foundry-Context does not change .last-next-at.'

    `foundry_get_context` calls `foundry_next_action` for its `next_action`
    field, so every Foundry-Context call used to reset the stall clock to now.
    The effect is the opposite of the watchdog's purpose: a lead that deliberates
    for twenty minutes, calls Foundry-Context to reorient, and deliberates for
    twenty more is measured from the Context call and never warned. The clock
    measures Foundry-Next to Foundry-Next, and a read-only reorientation call is
    not one of those.
    """
    project_root, fdir = run_env
    _write_state(fdir, phase="F2", cycle=1)
    _write_manifest_with_castings(fdir, ["src/api/a.py"], no_ui=True)

    foundry_next_action(project_root)
    before = (fdir / ".last-next-at").read_text(encoding="utf-8")

    foundry_get_context(project_root)

    assert (fdir / ".last-next-at").read_text(encoding="utf-8") == before

    # ...and a real Foundry-Next still arms it.
    (fdir / ".last-next-at").write_text("2020-01-01T00:00:00+00:00\n", encoding="utf-8")
    foundry_next_action(project_root)
    assert (fdir / ".last-next-at").read_text(encoding="utf-8") != (
        "2020-01-01T00:00:00+00:00\n"
    )




def test_a_halted_run_issues_no_dispatch(run_env):
    """AC-037's last clause: 'the next Foundry-Next reports the run halted and
    issues no dispatch.'

    Checked before every other branch in the guidance engine, including the
    active-teams one: a run that hit its cap is over, and emitting the ordinary
    phase guidance would send the lead round the loop the cap just stopped.
    """
    project_root, fdir = run_env
    _write_state(
        fdir, phase=RUN_PHASE_HALTED, cycle=2, max_cycles=2,
        halted_at_cycle=2, halted_reason="--max-cycles 2 reached",
    )
    _defect_ledger(fdir, [_tiered("D-001", "LIVE")])

    nxt = foundry_next_action(project_root)

    assert nxt["action"] == "halted"
    assert "HALTED" in nxt["instructions"]
    assert "NOT DONE" in nxt["instructions"]
    assert "YOUR NEXT CALL: NONE" in nxt["instructions"]
    assert nxt["details"]["open_live_defects"] == ["D-001"]
    # No agent config anywhere: nothing here tells the lead to spawn anything.
    assert "agent_config" not in nxt["details"]
    assert "agent_configs" not in nxt["details"]




def test_a_long_gap_with_agents_progressing_reports_waiting_not_a_stall(run_env):
    """AC-032 verbatim (first half): 'With an active team and a progressing
    ledger, a Foundry-Next call more than 180 seconds after the previous one
    reports waiting on N agents with the oldest progress age and sets no stall
    warning.'

    OT-022 drives the same claim at ten minutes. The warning used to fire on the
    clock ALONE, so the most common multi-minute gap in a foundry run — the lead
    waiting, correctly, for eight CAST teammates — was reported as "You were
    silently deliberating. Stop deliberating." The lead is trained to obey that
    literally, so the accusation actively pushed it to stop waiting and improvise
    over half-built work. A watchdog whose false positive is the NORMAL case is
    not a watchdog.
    """
    project_root, fdir = run_env
    _write_state(fdir, phase="F1", cycle=0)
    _write_manifest_with_castings(fdir, ["src/api/a.py"], no_ui=True)
    _progressing_ledger(fdir)
    _stale_stall_clock(fdir, 600)
    # AC-032's first half says "With an ACTIVE TEAM and a progressing ledger",
    # and D-076 made both halves load-bearing: the fixture patches the team scan
    # inactive by default, so the active arm has to be asked for explicitly.
    _teams_active(True)

    nxt = foundry_next_action(project_root)

    assert "stall_detected_seconds" not in nxt, nxt.get("instructions", "")[:400]
    assert nxt["waiting_on_agents"]["waiting"] is True
    assert nxt["waiting_on_agents"]["count"] == 1
    assert "oldest progress" in nxt["waiting_on_agents"]["detail"]
    assert "WAITING ON 1 AGENT" in nxt["instructions"]
    # FR-036's proviso: the notice never asserts deliberation while an agent is
    # progressing.
    assert "silently deliberating" not in nxt["instructions"]




def test_a_long_gap_with_nothing_running_still_reports_the_stall(run_env):
    """AC-032's second half: 'with no active teams it reports the stall.'

    The watchdog is scoped, not removed. When nothing is running the silence IS
    the lead's own, and the blunt instruction is the right one — that failure
    mode (a lead deliberating instead of executing) is real and is what the
    warning was written for.
    """
    project_root, fdir = run_env
    _write_state(fdir, phase="F1", cycle=0)
    _write_manifest_with_castings(fdir, ["src/api/a.py"], no_ui=True)
    _stale_stall_clock(fdir, 600)

    nxt = foundry_next_action(project_root)

    assert nxt["stall_detected_seconds"] >= 600
    assert "STALL DETECTED" in nxt["instructions"]
    assert "NO agent is running" in nxt["instructions"]
    assert "waiting_on_agents" not in nxt




def test_a_finished_agent_does_not_hold_the_lead_waiting(run_env):
    """The notice must not become the false positive it replaced.

    An agent whose ledger ends with a terminal line has declared itself finished,
    and reporting the lead as "waiting" on it would teach the lead to ignore the
    notice — the same way a roster where every finished agent looks stalled
    teaches it to ignore `needs_attention`.
    """
    project_root, fdir = run_env
    _write_state(fdir, phase="F1", cycle=0)
    _write_manifest_with_castings(fdir, ["src/api/a.py"], no_ui=True)
    (fdir / "progress").mkdir(parents=True, exist_ok=True)
    (fdir / "progress" / "casting-3.jsonl").write_text(
        json.dumps({
            "timestamp": now_iso(), "phase": "cast", "step": "committed 9f21ac3",
            "done": True, "agent": "casting-3",
        }) + "\n",
        encoding="utf-8",
    )
    _stale_stall_clock(fdir, 600)

    nxt = foundry_next_action(project_root)

    assert "waiting_on_agents" not in nxt
    assert "STALL DETECTED" in nxt["instructions"]




def test_the_waiting_notice_never_blocks(run_env):
    """CT-012: 'none; never blocks.'

    Driven by breaking the liveness read outright. A watchdog that could raise —
    or that could refuse a Foundry-Next because it failed to work out who was
    running — would be strictly worse than the accusation it replaced, and the
    degrade direction is toward WARNING rather than toward silence, so a broken
    reader can never suppress a real stall.
    """
    project_root, fdir = run_env
    _write_state(fdir, phase="F1", cycle=0)
    _write_manifest_with_castings(fdir, ["src/api/a.py"], no_ui=True)
    _stale_stall_clock(fdir, 600)

    from foundry_mcp.tools import foundry_spawn

    def _explode(*_a, **_k):
        raise RuntimeError("liveness is unavailable")

    original = foundry_spawn.foundry_liveness
    foundry_spawn.foundry_liveness = _explode
    try:
        nxt = foundry_next_action(project_root)
    finally:
        foundry_spawn.foundry_liveness = original

    assert nxt["action"], "the call answered rather than raising"
    assert "STALL DETECTED" in nxt["instructions"], (
        "a liveness reader that cannot answer must not suppress a real stall"
    )




def test_a_short_gap_says_nothing_either_way(run_env):
    """The threshold is unchanged (FR-036 leaves it to the implementer, and 180
    seconds was never the defect — the accusation was). A normal cadence must
    produce no notice at all, or the signal is noise."""
    project_root, fdir = run_env
    _write_state(fdir, phase="F1", cycle=0)
    _write_manifest_with_castings(fdir, ["src/api/a.py"], no_ui=True)
    _progressing_ledger(fdir)
    _stale_stall_clock(fdir, 10)

    nxt = foundry_next_action(project_root)

    assert "stall_detected_seconds" not in nxt
    assert "waiting_on_agents" not in nxt
    assert "STALL DETECTED" not in nxt["instructions"]
    assert "WAITING ON" not in nxt["instructions"]




# --------------------------------------------------------------------------- #
# D-012 / D-023 — the lead's imperatives describe the run that exists
# --------------------------------------------------------------------------- #


def test_the_imperatives_do_not_tell_the_lead_to_pass_the_prompt_field(run_env):
    """D-012: 'The lead imperatives instruct the lead to pass prompt text that
    is now always null. _ACTION_IMPERATIVES says "prompt=<returned prompt
    VERBATIM...>" while foundry_spawn.py returns "prompt": prompt_text if
    full_prompt else None.'

    start.md was already correct, so the run shipped four instruction surfaces
    with three wrong — and start.md is the one that tells the lead to follow
    Foundry-Next literally. Pointer dispatch put the text behind a file and a
    hash; an imperative naming the old field sends the lead to paste `None`.
    """
    for action in ("transition_to_cast", "transition_to_grind"):
        text = _ACTION_IMPERATIVES[action]
        assert "`dispatch` field VERBATIM" in text, action
        assert "returned prompt VERBATIM" not in text, action
        assert "prompt text VERBATIM" not in text, action
        # The positive statement, so a lead reading only this line knows why
        # the field it remembers is empty.
        assert "null by default" in text, action




def test_the_imperatives_say_foundry_next_is_optional_between_gate_and_phase(
    run_env
):
    """D-023: 'The imperatives never say Foundry-Next is optional between Gate
    and Phase. Text-verified: the word "optional" appears ZERO times in
    _ACTION_IMPERATIVES, while FR-044 names "the imperatives and start.md" as
    the two surfaces that must carry the rule.'

    FR-044 verbatim: 'Foundry-Gate no longer unlinks the ordering token. The
    imperatives and start.md say Gate then Phase, and note Foundry-Next may be
    called between them (it is where the inspect mode is announced) but is not
    required.' Both surfaces, not either.
    """
    # GATE then PHASE, in that order. `transition_to_assay` names both but the
    # other way round, and a Foundry-Next before a Phase call that no Gate
    # preceded is still REQUIRED — the token has to be armed by something.
    gate_then_phase = [
        action for action, text in _ACTION_IMPERATIVES.items()
        if "Foundry-Gate(" in text and "Foundry-Phase(" in text
        and text.index("Foundry-Gate(") < text.index("Foundry-Phase(")
    ]
    assert gate_then_phase, "no imperative pairs a Gate with a following Phase call"

    for action in gate_then_phase:
        text = _ACTION_IMPERATIVES[action]
        assert "OPTIONAL" in text, action
        assert "Foundry-Next" in text, action




def test_the_optional_rule_has_one_spelling(run_env):
    """FR-044's rule is stated once and appended, not typed into each
    imperative. This file's own history is that a rule stated in N copies
    becomes a rule stated N different ways — which is the
    stale-prose-survives-beside-new-prose class D-012 and D-023 both belong
    to."""
    note = _GATE_THEN_PHASE_NOTE
    assert "OPTIONAL" in note

    carriers = [t for t in _ACTION_IMPERATIVES.values() if note in t]
    assert len(carriers) >= 3
    # No imperative says it in its own words.
    for text in _ACTION_IMPERATIVES.values():
        assert text.count("OPTIONAL") == (1 if note in text else 0)




def test_a_latent_only_backlog_is_not_routed_back_into_grind(run_env):
    """FR-006 verbatim: 'INSPECT-clean, ASSAY, TEMPER, NYQUIST and DONE all pass
    when the only open defects are LATENT.' AC-008. D-055.

    The GATES were made tier-aware and the ROUTER was not: `_compute_next_action`
    counted raw open records and the F2 branch routed on `open_count > 0`, so a
    LATENT-only backlog was sent back into GRIND forever — the exact
    non-termination FR-006 exists to end. commands/start.md orders the lead to
    follow Foundry-Next literally and not deliberate, so the run could not reach
    ASSAY while any LATENT instance stayed open, contradicting AC-002's "the run
    reaches NYQUIST" and NFR-003's "LATENT stays open, tracked, and listed in
    the report".
    """
    project_root, fdir = run_env
    _write_state(fdir, phase="F2", cycle=2, inspect_modes=[{
        "cycle": 2, "phase": "F2", "mode": "FULL", "rule": "final_gate",
        "decided_by": "inspect_start",
        "required_streams": ["trace", "prove", "test"],
        "stream_scope": {}, "prove_sample": [], "touched_files": [],
    }])
    _write_manifest_with_castings(fdir, ["src/api/a.py"], no_ui=True)
    _router_ledger(fdir, [_router_defect("D-001", tier="LATENT",
                                         reproduction_attempted="drove it; nothing")])
    _streams_done(fdir)

    action = _compute_next_action(project_root)

    assert action["action"] == "transition_to_assay", action
    assert action["details"]["latent_backlog"] == ["D-001"]
    assert "D-001" not in action["instructions"] or "block" in action["instructions"]




def test_one_live_defect_still_routes_into_grind(run_env):
    """The other side, unchanged: the tier is an evidence grade, not a waiver.
    A reproduced failure routes to GRIND exactly as every open defect used to."""
    project_root, fdir = run_env
    _write_state(fdir, phase="F2", cycle=2, inspect_modes=[{
        "cycle": 2, "phase": "F2", "mode": "FULL", "rule": "final_gate",
        "decided_by": "inspect_start",
        "required_streams": ["trace", "prove", "test"],
        "stream_scope": {}, "prove_sample": [], "touched_files": [],
    }])
    _write_manifest_with_castings(fdir, ["src/api/a.py"], no_ui=True)
    _router_ledger(fdir, [
        _router_defect("D-001", tier="LATENT",
                       reproduction_attempted="drove it; nothing"),
        _router_defect("D-002", tier="LIVE"),
    ])
    _streams_done(fdir)

    action = _compute_next_action(project_root)

    assert action["action"] == "transition_to_grind"
    assert action["details"]["live_defects"] == ["D-002"]
    assert action["details"]["latent_backlog"] == ["D-001"]




def test_an_untiered_defect_routes_into_grind_like_a_live_one(run_env):
    """FR-051: an open pre-change record with no tier 'blocks like LIVE'. The
    router reads the same `_blocking_defects` the gates do, so the two cannot
    answer differently about one ledger."""
    project_root, fdir = run_env
    _write_state(fdir, phase="F2", cycle=2, inspect_modes=[{
        "cycle": 2, "phase": "F2", "mode": "FULL", "rule": "final_gate",
        "decided_by": "inspect_start",
        "required_streams": ["trace", "prove", "test"],
        "stream_scope": {}, "prove_sample": [], "touched_files": [],
    }])
    _write_manifest_with_castings(fdir, ["src/api/a.py"], no_ui=True)
    untiered = _router_defect("D-009")
    del untiered["tier"]
    _router_ledger(fdir, [untiered])
    _streams_done(fdir)

    action = _compute_next_action(project_root)

    assert action["action"] == "transition_to_grind"
    assert action["details"]["unknown_tier_defects"] == ["D-009"]




def test_the_grind_imperative_names_a_foundry_fix_the_server_accepts(run_env):
    """AC-020 / AC-011 / CT-005. D-056.

    The F3 arm dictated `Foundry-Fix(defect_id, cycle, adjacent_path_statement,
    adjacent_path_test)` and stated beside it that "the two declarations are
    required and the call is refused without them". The shipped schema's
    required list is ['defect_id', 'cycle', 'authored_by'], so that exact
    argument set is refused — a lead following Foundry-Next literally was
    refused on its FIRST fix of every cycle. The sentence was wrong for LATENT
    defects too, where AC-012 forbids demanding the adjacent-path pair.

    Asserted against the ADVERTISED SCHEMA rather than against a remembered
    field list, so the imperative and the tool cannot drift apart again.
    """
    from foundry_mcp import server as foundry_server

    project_root, fdir = run_env
    _write_state(fdir, phase="F3", cycle=2)
    _write_manifest_with_castings(fdir, ["src/api/a.py"], no_ui=True)
    _router_ledger(fdir, [_router_defect("D-001")])

    instructions = _compute_next_action(project_root)["instructions"]

    tools = asyncio.run(foundry_server.list_tools())
    fix = next(t for t in tools if t.name == "Foundry-Fix")
    for field in fix.inputSchema["required"]:
        assert field in instructions, (
            f"{field} is required by the advertised schema and the imperative "
            "does not name it — the lead's first fix of the cycle is refused"
        )
    # ...and both lanes are described, so a LATENT defect is not sent the LIVE
    # ceremony (AC-012).
    assert "regression_test" in instructions
    assert "LATENT" in instructions




def test_the_grind_imperative_names_the_recorded_width_not_full(run_env):
    """FR-011 / GI-009. D-072's first half.

    The F3 arm ended "run full INSPECT again", and that arm is the state DELTA
    is reachable from — so it instructed full width while the very next
    transition would record a DELTA roster. Foundry-Next REPORTS the recorded
    decision and the next crossing DECIDES the next one; neither is a claim this
    arm may make.
    """
    project_root, fdir = run_env
    _write_state(fdir, phase="F3", cycle=2, inspect_modes=[{
        "cycle": 2, "phase": "F2", "mode": "DELTA", "rule": "delta",
        "decided_by": "inspect_start",
        "required_streams": ["trace", "prove", "test"],
        "stream_scope": {}, "prove_sample": [], "touched_files": [],
    }])
    _write_manifest_with_castings(fdir, ["src/api/a.py"], no_ui=True)
    _router_ledger(fdir, [_router_defect("D-001")])

    action = _compute_next_action(project_root)

    assert "run full INSPECT again" not in action["instructions"]
    assert action["details"]["inspect_mode"] == "DELTA"
    assert action["details"]["inspect_rule"] == "delta"
    assert "DELTA" in action["instructions"]




def test_the_f1_imperative_names_the_tool_that_enters_f2(run_env):
    """GI-009 verbatim: 'whichever Foundry-Phase transition opens an INSPECT
    records the mode.' D-072's second half.

    The F1 arm said "then update state to F2", naming no tool. The only thing
    that records the F2 entry's mode is `Foundry-Phase(phase='cast')`, so
    hand-editing state.json produced exactly GI-009's named violation — a first
    INSPECT of a phase with no recorded mode.
    """
    project_root, fdir = run_env
    _write_state(fdir, phase="F1", cycle=0)
    _write_manifest_with_castings(fdir, ["src/api/a.py"], no_ui=True)
    _router_ledger(fdir, [])
    (fdir / ".cast-complete").write_text("x\n", encoding="utf-8")

    action = _compute_next_action(project_root)

    assert action["action"] == "transition_to_inspect"
    assert "Foundry-Phase(phase='cast')" in action["instructions"]
    assert "update state to F2" not in action["instructions"]




def test_the_inspect_action_names_its_own_crossing_from_f1_and_from_f3(run_env):
    """fallout D-058 / AC-059 / GI-001 — driven at BOTH emission sites.

    `transition_to_inspect` is the only action `_compute_next_action` emits from
    two phases, and the two are different crossings. It carried one frozen pair
    of literals — `Foundry-Gate(phase='inspect')` above
    `Foundry-Phase(phase='inspect_start')` — which is refused at both: from F3
    the gate is refused ("Cannot enter F2 from phase F3 ... accepted from F1 and
    from nowhere else"), and from F1 the phase call is ("accepted from F3, and
    from F2"). Both tokens are legal enum members, so nothing rejects either
    before the door and the mistake is invisible until the lead makes the call.

    Driven through `foundry_next_action`, which is the surface the lead reads,
    rather than off the constant: the substitution happens at emission and a
    test that read the table would not exercise it. The stale-literal check is
    the point of the second assertion in each half — a `{gate}` reaching the
    lead is a call it would try to make.
    """
    project_root, fdir = run_env
    _write_manifest_with_castings(fdir, ["src/api/a.py"], no_ui=True)

    # ── F1: CAST complete, and the crossing is `cast` behind the `inspect` gate.
    _write_state(fdir, phase="F1", cycle=0)
    _router_ledger(fdir, [])
    (fdir / ".cast-complete").write_text("x\n", encoding="utf-8")

    f1 = foundry_next_action(project_root)
    assert f1["action"] == "transition_to_inspect", f1
    text = f1["instructions"]
    assert "Foundry-Gate(phase='inspect')" in text, text
    assert "Foundry-Phase(phase='cast')" in text, text
    assert "Foundry-Phase(phase='inspect_start')" not in text.split("CONTEXT")[0], text
    assert "{gate}" not in text and "{token}" not in text, text

    # ── F3: GRIND closed, and the crossing is `inspect_start` behind its OWN
    # gate — the token AC-059 added so this transition would have one at all.
    (fdir / ".cast-complete").unlink()
    _write_state(fdir, phase="F3", cycle=2)
    _router_ledger(fdir, [])

    f3 = foundry_next_action(project_root)
    assert f3["action"] == "transition_to_inspect", f3
    text = f3["instructions"]
    assert "Foundry-Gate(phase='inspect_start')" in text, text
    assert "Foundry-Phase(phase='inspect_start')" in text, text
    assert "{gate}" not in text and "{token}" not in text, text




def test_the_gate_advance_signal_reads_the_gate_this_crossing_actually_needs(run_env):
    """fallout D-058 / AC-059 — the second consumer, and it failed both ways.

    `_expected_gate_for_action` is what `.gate-passed` is compared against. With
    one frozen `inspect` for both phases, a lead at F3 who ran the CORRECT door
    got `gate_advanced` absent and was told to run the refusing one; and a stale
    `inspect` marker left over from the F1 entry produced "Foundry-Gate(phase=
    inspect) ALREADY PASSED — do NOT re-run it" for a gate that never guarded
    this crossing. Wrong in the reassuring direction is the worse of the two.
    """
    project_root, fdir = run_env
    _write_manifest_with_castings(fdir, ["src/api/a.py"], no_ui=True)
    _write_state(fdir, phase="F3", cycle=2)
    _router_ledger(fdir, [])
    marker = fdir / artifacts.GATE_PASSED_MARKER

    # The gate this crossing needs, passed: the advance signal names it.
    marker.write_text(json.dumps({"phase": "inspect_start"}), encoding="utf-8")
    advanced = foundry_next_action(project_root)
    assert advanced.get("gate_advanced", {}).get("passed_gate") == "inspect_start", (
        advanced.get("gate_advanced")
    )

    # The F1 entry's gate, stale on disk: NOT this crossing's, and not vouched
    # for. The lead is left to run the door that guards what it is about to call.
    marker.write_text(json.dumps({"phase": "inspect"}), encoding="utf-8")
    stale = foundry_next_action(project_root)
    assert "gate_advanced" not in stale, stale.get("gate_advanced")




def test_a_clean_delta_cycle_is_told_to_widen_not_to_open_assay(run_env):
    """AC-016 / D-068's ruling, on the router side: the imperative names the
    crossing that actually works. Naming `inspect_clean` here would send the
    lead into the refusal the transition now returns.

    D-169: and the CONDITION it states is the one both ASSAY doors evaluate —
    the recorded mode. It said "ASSAY is only opened by an INSPECT recorded
    with rule final_gate", and neither door reads a rule, so a lead sitting on
    a clean FULL / verifier_touched cycle was told to spend a widening cycle
    the server would refuse as having nothing to widen."""
    project_root, fdir = run_env
    _write_state(fdir, phase="F2", cycle=3, inspect_modes=[{
        "cycle": 3, "phase": "F2", "mode": "DELTA", "rule": "delta",
        "decided_by": "inspect_start",
        "required_streams": ["trace", "prove", "test"],
        "stream_scope": {}, "prove_sample": [], "touched_files": [],
    }])
    _write_manifest_with_castings(fdir, ["src/api/a.py"], no_ui=True)
    _router_ledger(fdir, [])
    _streams_done(fdir)

    action = _compute_next_action(project_root)

    assert action["action"] == "widen_inspect"
    assert "inspect_start" in action["instructions"]
    assert "recorded mode is FULL" in action["instructions"], action["instructions"]
    assert "rule final_gate" not in action["instructions"], (
        "the rule is not the condition either ASSAY door reads"
    )




# --------------------------------------------------------------------------- #
# D-097 — the payload does not argue with itself about Foundry-Next
# --------------------------------------------------------------------------- #


def test_the_rules_block_and_the_gate_note_are_the_same_string(run_env):
    """FR-044 / AC-035 / OT-028 / D-097.

    The CRITICAL RULES block that heads EVERY Foundry-Next payload read 'NEVER
    stop between phases. Call Foundry-Next after each step and follow it.' A
    gate is a step, so the lead met an unconditional instruction at the top of
    the payload and the note that qualifies it at the tail of the imperative —
    on the three imperatives that carry it, hundreds of tokens further down.
    FR-044's own rationale is that 'the word optional appeared ZERO times in the
    imperatives'; adding the note fixed that surface and left the contradicting
    general rule standing above it.

    Pinned as ONE STRING rather than as two texts that happen to agree: a test
    asserting both say 'OPTIONAL' does not stop them saying different things,
    and this file's documented failure mode is that a rule stated in N copies
    becomes a rule stated N different ways.
    """
    project_root, fdir = run_env
    _write_state(fdir, phase="F0")

    instructions = foundry_next_action(project_root)["instructions"]

    assert _GATE_THEN_PHASE_EXCEPTION in instructions
    assert _GATE_THEN_PHASE_EXCEPTION in _GATE_THEN_PHASE_NOTE
    assert _GATE_THEN_PHASE_NOTE == "\n" + _GATE_THEN_PHASE_EXCEPTION

    # The exception is stated inside the rule it qualifies, not somewhere else
    # in the payload: the rule line and the exception are one sentence sequence.
    rule_line = next(
        line for line in instructions.splitlines()
        if line.startswith("- NEVER stop between phases")
    )
    assert "OPTIONAL" in rule_line
    assert "REQUIRED everywhere except exactly one place" in rule_line
    assert not rule_line.endswith("Call Foundry-Next after each step and follow it.")




def test_the_announced_field_has_one_name_across_both_surfaces(run_env):
    """D-097's second half: commands/start.md calls it the INSPECT 'mode' while
    the imperatives called it the 'width and rule', so a lead reading the two
    surfaces had to work out they meant the same field. The one string names it
    both ways."""
    assert "mode" in _GATE_THEN_PHASE_EXCEPTION
    assert "width" in _GATE_THEN_PHASE_EXCEPTION
    assert "rule" in _GATE_THEN_PHASE_EXCEPTION

    start_md = (
        # fallout FR-005: one directory deeper than the module this was
        # carved from, so the index moves with it.
        Path(__file__).resolve().parents[4] / "foundry" / "commands" / "start.md"
    )
    if start_md.exists():
        gate_then_phase = next(
            line for line in start_md.read_text(encoding="utf-8").splitlines()
            if line.startswith("**Gate then Phase.**")
        )
        assert "OPTIONAL" in gate_then_phase
        assert "mode" in gate_then_phase




# --------------------------------------------------------------------------- #
# D-136 / D-137 — a HALTED run is told to stop, once, in words that agree
# --------------------------------------------------------------------------- #


def test_a_halted_run_is_never_told_to_keep_going(run_env):
    """FR-052 / FR-045 / NFR-005: Foundry-Next 'reports halted and stops
    dispatching'. D-136.

    Driven on a halted state, `instructions` was the standing CRITICAL RULES
    block — "NEVER stop between phases. Call Foundry-Next after each step and
    follow it", "If you catch yourself thinking, call Foundry-Next and execute
    whatever it says", "The foundry runs until F6 DONE or an error stops it" —
    followed by the halted imperative "YOUR NEXT CALL: NONE ... do NOT call
    Foundry-Next in a loop ... stop". Dispatch was correctly withheld; the
    lead-facing text told the lead to do the opposite, on the same surface.
    """
    project_root, fdir = run_env
    _write_manifest_with_castings(fdir, ["src/api/a.py"], no_ui=True)
    _write_state(
        fdir, phase=vocab.RUN_PHASE_HALTED, cycle=3,
        halted_at_cycle=3, halted_reason="max_cycles 2 reached", max_cycles=2,
    )
    _defect_ledger(fdir, [])

    nxt = foundry_next_action(project_root)
    text = nxt["instructions"]

    assert nxt["action"] == "halted"
    # Each element below is the RETIRED wording this asserts is absent — test
    # data, not a claim. The markers are inline because the pin scans a bare
    # tuple element as its own code line, where the `assert ... not in` two
    # lines down is not visible to it.
    for contradiction in (
        "NEVER stop between phases",  # retired on a halted run (D-136)
        "call Foundry-Next and execute whatever it says",  # retired (D-136)
        "runs until F6 DONE or an error stops it",  # retired by FR-024 (D-136)
    ):
        assert contradiction not in text, contradiction
    assert "HALTED" in text
    assert "do NOT call Foundry-Next in a loop" in text or "loop" in text




def test_the_standing_rules_name_all_three_endings(run_env):
    """FR-024: HALTED is a third ending, 'not a refusal'. D-136's other half.

    The standing block is read on EVERY non-halted call, and it said the run
    ends two ways. A lead told the halt cannot happen has been mis-briefed
    about the one transition it will not recognise when it arrives.
    """
    assert "runs until F6 DONE or an error stops it" not in (
        _STANDING_CRITICAL_RULES
    )
    assert "HALTED" in _STANDING_CRITICAL_RULES
    assert "F6 DONE" in _STANDING_CRITICAL_RULES

    project_root, fdir = run_env
    _write_manifest_with_castings(fdir, ["src/api/a.py"], no_ui=True)
    _write_state(fdir, phase="F1", cycle=0)
    _defect_ledger(fdir, [])
    assert "HALTED" in foundry_next_action(project_root)["instructions"]




def test_the_status_header_renders_halted_once(run_env):
    """NFR-005 / CT-016: HALTED is a named terminal state Foundry-Next reports.
    D-137.

    Driven on a halted state, the banner read "F O U N D R Y  HALTED HALTED":
    `_format_status_display` renders the phase token followed by
    `phase_names.get(phase, phase)`, and `phase_names` — built from the ten
    ladder rows — has no entry for the halt, so the fallback repeated the
    token. Every new notice has to read correctly in a terminal.
    """
    project_root, fdir = run_env
    _write_manifest_with_castings(fdir, ["src/api/a.py"], no_ui=True)
    _write_state(
        fdir, phase=vocab.RUN_PHASE_HALTED, cycle=3,
        halted_at_cycle=3, halted_reason="max_cycles 2 reached",
    )
    _defect_ledger(fdir, [])

    # The banner sits inside the hammer art block, so the whole render is the
    # unit — reading line 1 alone reads the art.
    rendered = _plain(_format_status_display(project_root))
    banner = next(
        line for line in rendered.splitlines() if "F O U N D R Y" in line
    )

    assert "HALTED HALTED" not in banner
    assert banner.count("HALTED") == 1, banner
    # The ordinary phases still render token AND name, which is what the
    # fallback was there for.
    _write_state(fdir, phase="F2", cycle=1)
    ordinary = _plain(_format_status_display(project_root))
    ordinary_banner = next(
        line for line in ordinary.splitlines() if "F O U N D R Y" in line
    )
    assert "F2 INSPECT" in ordinary_banner




def test_every_next_response_names_the_terminal_state_it_is_heading_for(run_env):
    """fallout FR-021 / CT-007 / AC-028 / OT-026 — three fields, every response.

    A named backlog is a SUCCESSFUL end. A lead that believes DONE is the only
    acceptable ending grinds cycles against a target it has already met, so the
    three facts that decide which ending is coming sit on the surface it reads
    before every call rather than in a report it reads once.
    """
    project_root, fdir = run_env
    _write_state(fdir, phase="F2", cycle=1, max_cycles=3)
    _defect_ledger(fdir, [
        _tiered("D-1", "LIVE"),
        _tiered("D-2", "HARDENING", reproduction_attempted="drove the probe; it failed"),
    ])

    result = foundry_next_action(project_root)
    assert result["heading_for"] == "DONE", result
    assert result["open_by_tier"]["LIVE"] == 1, result
    assert result["open_by_tier"]["HARDENING"] == 1, result
    assert result["cycles_to_cap"] == 2, result

    # At the cap, the run is heading for HALTED BEFORE anything has stopped —
    # which is the whole point of saying it at the door rather than after it.
    _write_state(fdir, phase="F2", cycle=3, max_cycles=3)
    at_cap = foundry_next_action(project_root)
    assert at_cap["heading_for"] == vocab.RUN_PHASE_HALTED, at_cap
    assert at_cap["cycles_to_cap"] == 0, at_cap

    # An unbounded run reports None, not 0: "no cap" and "no cycles left" are
    # opposite facts and a reader that conflates them halts a run that had no
    # cap at all.
    _write_state(fdir, phase="F2", cycle=3)
    assert foundry_next_action(project_root)["cycles_to_cap"] is None

    # And a halted run says so whatever the arithmetic.
    _halted_run(fdir)
    assert foundry_next_action(project_root)["heading_for"] == vocab.RUN_PHASE_HALTED




def test_every_action_the_router_emits_has_an_imperative():
    """fallout FR-035 / AC-054 — the survey counted nine holes; there are none.

    An imperative table with holes is worse than none: the holes are invisible
    and they are exactly where the lead improvises, because the generic fallback
    header says "Execute the first tool call mentioned. Do not deliberate." over
    a body that for several of them mentions no tool call at all.

    Derived from `_compute_next_action`'s own AST, so the tenth is caught the
    day it is written.
    """
    emitted: set[str] = set()
    for name in ("_compute_next_action", "_nyquist_transition"):
        tree = ast.parse(textwrap.dedent(inspect.getsource(getattr(owning_module(name), name))))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Dict):
                continue
            for key, value in zip(node.keys, node.values):
                if (
                    isinstance(key, ast.Constant) and key.value == "action"
                    and isinstance(value, ast.Constant)
                ):
                    emitted.add(value.value)
    assert len(emitted) >= 20, emitted
    assert emitted <= set(_ACTION_IMPERATIVES), sorted(emitted - set(_ACTION_IMPERATIVES))




def test_the_done_imperative_states_the_f6_order_exactly():
    """fallout FR-035 / AC-054 — Report, Gate, strip, Phase.

    It read "YOUR NEXT CALL: Foundry-Phase(phase='done')", which contradicts the
    sequence the doors enforce: the DONE evaluation requires the generated
    report AND re-executes the committed evidence corpus, so stripping
    `evidence/` before the gate refuses it for a corpus that is not there, and
    stripping after the gate passes changes the tree the gate judged.
    """
    imperative = _ACTION_IMPERATIVES["transition_to_done"]
    order = [
        imperative.index("Foundry-Report"),
        imperative.index("Foundry-Gate(phase='done')"),
        imperative.index("git rm"),
        imperative.index("Foundry-Phase(phase='done')"),
    ]
    assert order == sorted(order), (order, imperative)




def test_no_lead_imperative_tells_the_lead_to_record_a_stream():
    """fallout FR-023 / FR-049 / GI-016 / AC-030 — the AGENT records.

    A lead that records on an agent's behalf asserts numbers it did not measure,
    and when the agent then records its own the cycle carries two accounts of
    one run. Swept over every imperative rather than checked on `run_streams`
    alone, because the instruction it was removed from is the one a lead reads
    most and the next copy would land in a sibling.
    """
    # The distinguishing form is the IMPERATIVE one — a sentence telling the
    # lead to make the call. A sentence stating that the agent records is the
    # opposite claim and is what these now carry.
    lead_is_told_to_record = (
        "call foundry-stream",
        "then foundry-stream",
        "then call foundry-stream",
    )
    seen = 0
    for action, imperative in _ACTION_IMPERATIVES.items():
        if "Foundry-Stream" not in imperative:
            continue
        seen += 1
        lowered = " ".join(imperative.lower().split())
        for form in lead_is_told_to_record:
            assert form not in lowered, (action, form, imperative)
        assert "records its own" in lowered or "confirm" in lowered, (action, imperative)
    assert seen >= 2, (
        "no imperative mentions Foundry-Stream at all, so this sweep asserts "
        "nothing — the scan has gone blind"
    )




def test_the_status_banner_declares_no_palette_and_no_phase_ladder_of_its_own():
    """fallout research/holmes-orchestrator.md#coh-8 (D-014) / GI-024 / D-015.

    `_format_status_display` is a RENDERER, and it lived beside its own copy of
    two things another module owns: eight ANSI codes and the ten-row run-phase
    ladder. Two declarations of one palette drift into two colour schemes in one
    terminal; two declarations of one ladder mean a phase added to the
    vocabulary renders as a run with a step missing, and the two agree only by
    inspection until they do not.

    Both are read now — `display`'s public spellings and
    `schemas.vocab.PHASE_LADDER` / `PHASE_NAMES` — and this asserts the reading
    on the SOURCE, because the harm is a second declaration and a behavioural
    drive over agreeing copies proves nothing about which module owns them.
    """
    source = inspect.getsource(_guidance)

    # No escape literal of any kind. The palette is display.py's, whole.
    assert "\\x1b[" not in source and "\\033[" not in source, (
        "guidance.py spells an ANSI escape of its own. The palette is "
        "display.py's; import the public name."
    )

    # ...and no second phase ladder. A tuple pairing a run-phase id with that
    # id's LABEL is the vocabulary's own row, wherever it is typed. A tuple of
    # two phase IDS is a different thing — a source/destination pair — and is
    # left alone, which is why the second element is judged against the labels
    # rather than merely against "is a string".
    tree = ast.parse(source)
    typed_rows = [
        ast.unparse(node)
        for node in ast.walk(tree)
        if isinstance(node, ast.Tuple)
        and len(node.elts) == 2
        and all(
            isinstance(e, ast.Constant) and isinstance(e.value, str)
            for e in node.elts
        )
        and vocab.PHASE_NAMES.get(node.elts[0].value) == node.elts[1].value
    ]
    assert typed_rows == [], (
        f"guidance.py types run-phase ladder row(s) of its own: {typed_rows}. "
        "The ladder is schemas/vocab.py#PHASE_LADDER."
    )

    # The positive half: the renderer reaches both declarations.
    banner = inspect.getsource(_guidance._format_status_display)
    assert "PHASE_LADDER" in banner and "PHASE_NAMES" in banner, banner[:400]




def test_the_subagent_caller_instruction_reaches_a_subagent_mechanically():
    """fallout FR-034 / FR-055 / AC-053 — D-047: a published argument nobody
    is told to pass.

    The guard is correct — `is_lead = caller == LEAD_CALLER` gates both the
    ordering token and the stall clock — and it was unreachable. `caller`
    defaults to the lead value at the server boundary, and a driven grep across
    `agents/`, `skills/` and `commands/` returned zero files naming it, so every
    shipped sub-agent took the default and armed the handshake the LEAD owes.
    Nine stream surfaces are pinned to take the cycle from Foundry-Next with no
    caller argument beside it.

    The two surfaces a sub-agent provably reads are the tool description it
    loads with the tool list and the protocol block the lead appends to its
    prompt verbatim. Both carry the instruction, and both QUOTE the one
    constant rather than re-wording it, which is what keeps a prose sweep in
    one of them from silently diverging from the other.
    """
    from foundry_mcp import server as foundry_server
    from foundry_mcp.tools.foundry_spawn import _progress_protocol_block
    from foundry_mcp.tools.orchestration.guidance import (
        LEAD_CALLER,
        SUBAGENT_CALLER,
        SUBAGENT_CALLER_INSTRUCTION,
    )

    # The sentence names the argument and the value, so an agent reading only
    # this line knows what to type.
    assert f"caller='{SUBAGENT_CALLER}'" in SUBAGENT_CALLER_INSTRUCTION
    assert SUBAGENT_CALLER != LEAD_CALLER

    tools = {t.name: t for t in asyncio.run(foundry_server.list_tools())}
    next_tool = tools["Foundry-Next"]
    assert SUBAGENT_CALLER_INSTRUCTION in next_tool.description, next_tool.description
    caller_property = next_tool.inputSchema["properties"]["caller"]
    assert SUBAGENT_CALLER_INSTRUCTION in caller_property["description"]
    # The wire enum is DERIVED from the two constants, so a third caller kind
    # cannot reach the guard without appearing here.
    assert caller_property["enum"] == [LEAD_CALLER, SUBAGENT_CALLER]

    # ...and every spawn this server makes appends it, which is the half that
    # does not depend on an agent file being rewritten.
    block = _progress_protocol_block("a-run", "casting-1")
    assert SUBAGENT_CALLER_INSTRUCTION in block, block[-600:]


def test_only_the_leads_next_arms_the_ordering_token_and_the_stall_clock(run_env):
    """fallout AC-053 — the guard the instruction above exists to make reachable.

    Driven at the door rather than read: a sub-agent's call must leave the
    ordering token absent and the stall clock untouched, and the lead's call
    must arm both. This is what the argument BUYS, and it is why publishing a
    default that says 'lead' and telling nobody was the whole defect.
    """
    from foundry_mcp.tools.orchestration.guidance import (
        LEAD_CALLER,
        SUBAGENT_CALLER,
    )

    project_root, fdir = run_env
    _write_state(fdir, phase="F2", cycle=1)
    token = fdir / artifacts.NEXT_ACTION_CALLED_MARKER
    token.unlink(missing_ok=True)

    foundry_next_action(project_root, caller=SUBAGENT_CALLER)
    assert not token.exists(), "a sub-agent's read armed the lead's ordering token"

    foundry_next_action(project_root, caller=LEAD_CALLER)
    assert token.exists(), "the lead's own call did not arm the ordering token"


# --------------------------------------------------------------------------- #
# fallout GI-033 / AC-061 (D-021 / D-035) — CARVED OUT OF `test_width.py` WITH
# THEIR SUBJECT.
#
# `_waiting_on_agents` and `STALL_NOTICE_SECONDS` moved from the verifier-set
# `orchestration/width.py` to `orchestration/guidance.py`, whose
# `_compute_next_action` is their only caller. GI-026 says the tests move in the
# same casting as the source, so they move here — where the module that now
# defines them already has its test module.
# --------------------------------------------------------------------------- #



def test_a_mode_less_inspect_routes_to_the_width_and_not_to_assay(run_env):
    """fallout GI-008 / D-117 (concern C-040) — Foundry-Next is a THIRD caller.

    `foundry_state.check_streams_complete` holds the unrecorded-width arm behind
    an INJECTION, and the first version of this cycle's two compositions omitted
    it. The doors stayed safe — `Foundry-Gate('assay')` and `inspect_clean` ask
    `_unrecorded_width_problem` themselves — but Foundry-Next asks nothing of its
    own, so on an INSPECT whose width was never recorded it answered over the
    PRE-WIDTH roster and routed the lead to ASSAY with `research_audit` and
    `test01` silently dropped. GI-008 names that shape in as many words: "a
    streams-complete check that reads a roster nothing recorded".

    DRIVEN THROUGH THE GUIDANCE SURFACE, not through the leaf, because the leaf
    was never wrong: it is the composition that decides whether the arm can
    fire, and only a drive of the reporting caller tells the two apart.
    """
    project_root, fdir = run_env
    _write_manifest_with_castings(fdir, ["src/a.py"], no_ui=True)
    # F2 with NO `inspect_modes` entry at all — the D-117 shape — and the three
    # pre-width markers on disk, which is what made the fallback look complete.
    _write_state(fdir, phase="F2", cycle=2)
    for stream in ("trace", "prove", "test"):
        (fdir / f".{stream}-complete").write_text(
            "2020-01-01T00:00:00+00:00 cycle=2\nitems_checked=1\n"
            "items_total=1\ncoverage=100%\nfindings=0\n",
            encoding="utf-8",
        )

    streams = _check_streams_complete(project_root)
    assert streams["complete"] is False, streams
    assert streams["missing"] == "inspect_mode", streams
    assert streams.get("unrecorded_width") is True, streams

    action = foundry_next_action(project_root)
    assert action["action"] != "transition_to_assay", action
    # ...and it says WHICH thing is missing, rather than routing on a roster
    # nothing recorded.
    assert "width" in action["action"] or "width" in str(action.get("details", {})), action








def test_a_registered_team_with_dead_ledgers_does_not_suppress_the_stall(
    run_env, monkeypatch
):
    """D-021: 'A registered-but-dead team suppresses the stall warning forever.
    The function's own docstring states the intended rule ("a team dir that was
    never cleaned up is the false positive"); the code does the opposite.'

    Driven exactly as filed: a three-hour-old ledger, so `foundry_liveness`
    reports every agent `stalled`, plus a registered team the run never cleaned
    up. The old final arm returned `waiting: True` on that state and
    Foundry-Next rendered "that gap is the agents working, not you
    deliberating" indefinitely — a watchdog a stale directory can switch off.

    `_check_active_teams` is monkeypatched ACTIVE here, against the fixture's
    default. That inversion is the point: every fixture in the suite pins it
    inactive, which is why the arm that only fires when it is active was
    untestable as shipped.
    """
    project_root, fdir = run_env
    patch_everywhere(monkeypatch, "_check_active_teams",
        lambda _pr: {"active": True, "teams": ["cast-run-wave-1"], "live_panes": []},
    )
    _write_state(fdir, phase="F1", cycle=0)
    _write_manifest_with_castings(fdir, ["src/api/a.py"], no_ui=True)
    _stalled_ledger(fdir)
    _stale_stall_clock(fdir, 600)

    assert _waiting_on_agents(project_root)["waiting"] is False

    nxt = foundry_next_action(project_root)

    assert nxt.get("stall_detected_seconds", 0) >= 600
    assert "waiting_on_agents" not in nxt
    assert "WAITING ON" not in nxt["instructions"]
    assert "silently deliberating" in nxt["instructions"]




def test_a_progressing_ledger_with_no_team_registered_reports_waiting(run_env):
    """FR-020 verbatim: 'IF AGENTS ARE RUNNING it reports waiting on N agents
    (oldest progress Xm) instead of a stall'. FR-036, D-127.

    This test asserted the opposite twice, in opposite directions, and the
    second version is the defect. D-076's repair ANDed the team scan with the
    liveness roster, so waiting required a REGISTERED tmux team. The F2 INSPECT
    streams are background Agents and never tmux teammates, so
    `_check_active_teams` cannot see them: driven with no registered team, two
    progress ledgers written seconds earlier and `.last-next-at` 600s old,
    `_waiting_on_agents` returned waiting False, teams_active False,
    progressing_agents 2 — and Foundry-Next emitted stall_detected_seconds 600
    beside "NO agent is running. You were silently deliberating", asserting
    deliberation over two agents the same call had just measured progressing.
    FR-036's proviso is that the notice never does that, and FR-020 is Locked,
    so no GRIND ruling could amend it.

    LEAD RULING, GRIND cycle 7 (superseding the cycle-4 AND where they
    conflict): 'if agents are running' is decided by EVIDENCE OF PROGRESS. A
    progressing roster is SUFFICIENT whether or not a team is registered.
    `teams_active` is still read and still reported, so CT-012's declared input
    set is unchanged — see the sibling test that pins the stale-team direction.
    """
    project_root, fdir = run_env
    _write_state(fdir, phase="F2", cycle=1)
    _write_manifest_with_castings(fdir, ["src/api/a.py"], no_ui=True)
    _progressing_ledger(fdir, agent="prove")
    _stale_stall_clock(fdir, 600)
    _teams_active(False)

    waiting = _waiting_on_agents(project_root)

    assert waiting["waiting"] is True
    assert waiting["count"] == 1
    assert waiting["teams_active"] is False, (
        "reported, not asserted — waiting no longer implies a registered team"
    )
    assert waiting["progressing_agents"] == 1

    nxt = foundry_next_action(project_root)
    assert "stall_detected_seconds" not in nxt, (
        "an agent is progressing; FR-020 makes this the waiting notice"
    )
    assert "silently deliberating" not in nxt["instructions"]
    assert "WAITING ON" in nxt["instructions"]




def test_a_registered_but_dead_team_still_reports_the_stall(run_env):
    """D-021, which the AND must not undo.

    A team dir that was never cleaned up is not an agent that is running. The
    old code returned `waiting: True` on exactly that and Foundry-Next rendered
    "that gap is the agents working, not you deliberating" forever, on a run
    where nothing was working. Either source answering "nothing is running" is
    enough to let the watchdog speak, so the stale directory can no longer
    suppress it.
    """
    project_root, fdir = run_env
    _write_state(fdir, phase="F2", cycle=1)
    _write_manifest_with_castings(fdir, ["src/api/a.py"], no_ui=True)
    _stale_stall_clock(fdir, 600)
    _teams_active(True)

    waiting = _waiting_on_agents(project_root)

    assert waiting["waiting"] is False
    assert waiting["teams_active"] is True
    assert waiting["progressing_agents"] == 0
    assert "stall_detected_seconds" in foundry_next_action(project_root)




def test_the_waiting_check_consults_both_declared_inputs(run_env):
    """CT-012's input list, asserted on the SOURCE — because "which sources it
    asked" is not observable from a return value that agrees on the tested
    cases, and that is exactly how a declared input came to have a test
    guarding its ABSENCE (`test_the_waiting_check_does_not_consult_the_team_scan`
    AST-walked this function and failed if `_check_active_teams` appeared).
    """
    import inspect
    import textwrap

    tree = ast.parse(textwrap.dedent(inspect.getsource(_waiting_on_agents)))
    called = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert "_check_active_teams" in called, (
        "FR-020 verbatim: 'Foundry-Next checks active teams AND "
        "Foundry-Liveness'. Both, or the notice is not the one CT-012 declares."
    )
    assert "foundry_liveness" in called, (
        "a registered team is not evidence that an agent is running; the "
        "progress ledgers are the half that answers that (D-021)."
    )

