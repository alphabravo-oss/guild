"""Server-side cycle counter, recurring-class escalation, and how it STOPS.

EVERY REQUIREMENT ID BELOW NAMES ITS SPEC, BECAUSE TWO SPECS BUILT THIS FILE.
----------------------------------------------------------------------------
Their id spaces collide completely: every id this module cites also resolves in
the other spec, with different text. So the convention here is the one D-178
established one module over, where an unqualified tag sent a stream to the
wrong assertion because it resolved in both specs and named neither:

  * a BARE id cites ``forge-specs/foundry-run-convergence/spec.md`` — the
    effort that gave escalation an EXIT, which is the newer half of this file;
  * a historical tag is written ``process-fixes AC-008``, naming
    ``forge-specs/foundry-run-process-fixes/spec.md`` — the effort that built
    the server-side cycle counter and the three-cycle escalation heuristic,
    which is the older half this file still drives.

That convention is now ENFORCED, not merely stated here. D-181: this header was
corrected in cycle 11 and the per-test docstrings below were left unqualified,
so the module documented a rule it broke 156 times — and a documented rule
nothing checks is how D-178 came back one file over. The pin at the bottom of
this module, under the ``D-178`` sentinel, runs casting 2's scan over this
file's own prose; ``_CONVERGENCE_IDS`` there is the declared list of ids this
module legitimately cites bare, and the block above it states what the pin
cannot see.

The header of this module used to open with a bare
``FR-005 / FR-006 / … / AC-011, OT-003`` list and name no spec at all. Every
one of those ids is a process-fixes id and every one of them ALSO resolves in
the convergence spec: bare ``ST-001`` here reads "the server owns the cycle
number" in one and "class ESCALATED → CLEARED after two clean cycles" in the
other, and both are behaviours this module drives.

WHAT THIS MODULE DRIVES, IN CONVERGENCE IDS
-------------------------------------------
US-001 — an escalated class exits by a rule the server evaluates, "so that a
prover cannot keep the run open by moving the boundary one level finer each
cycle". ST-001 / AC-001 / FR-003 are the clean-cycle arm: two consecutive
server-counted INSPECT cycles in which the class draws zero LIVE instances.
ST-002 / AC-002 / FR-001 / FR-002 are the budget arm: the second structural
packet closing, one structural pass plus one retry. AC-004 / FR-028 are the
persisted exit record — status, exit reason, cleared_at_cycle and the packets
consumed. AC-003 is the half neither arm waives: an open LIVE instance of a
CLEARED class still blocks DONE and falls into an ordinary per-instance packet.
FR-051 is the untiered pre-change record blocking like LIVE beside it, and
ST-010 / GI-006 / CT-014 are the F6 doors this file drives to prove all of it —
DONE requires every escalated class CLEARED and a generated report.

AND IN PROCESS-FIXES IDS, THE HALF THAT CAME FIRST
--------------------------------------------------
process-fixes FR-005 / FR-006 / FR-007 / FR-008 / FR-024 — the counter's
increment point, the N=3 consecutive-cycle rule, the stream-declared ``class``
field, the one structural packet with a recorded proposal, and the fallback
clustering heuristic. process-fixes ST-001 / ST-002 / ST-003 — the GRIND→
INSPECT boundary that owns the count, and the class's NORMAL → ESCALATED →
CLEARED states as that spec defined them. process-fixes AC-008 / AC-009 /
AC-010 / AC-011 — the counter incrementing without a caller-supplied value, the
synthetic 3-cycle fixture, exactly one structural task while a class is
escalated, and escalation never waiving closure. process-fixes OT-003 — one
class recurring across three consecutive server-counted cycles.

process-fixes NFR-001 makes that synthetic three-cycle escalation fixture part
of what its casting delivered rather than an afterthought: "the spec's
acceptance is structural (each P-item's own acceptance test passes, e.g. ...
escalation fires on a synthetic 3-cycle fixture)".

The baseline the earlier effort was fixing: grand-vulture ran
FALSE_DOCUMENTED_CONTRACT for eight consecutive cycles (9-16), 42 defects,
because ``foundry_defects_to_tasks`` groups by LOCATION rather than by cause —
one systemic class spread over 11 files became 11 unrelated packets, each fixed
per-instance, the class itself never addressed. The three-cycle rule fires at
cycle 11 on that history. The convergence effort is the other end of the same
problem: escalation with no exit rule ruled one class NOT CLEARED at cycles 19,
20 and 21, each time at a finer boundary and with no live instance driven,
which is what US-001 above ends.

The counter is a prerequisite, not a separate feature: a per-class
"consecutive cycles" count is meaningless against numbers the caller asserts,
and ``state.json["cycle"]`` was written once as 0 by foundry_init and never
incremented by any code path.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from foundry_mcp.tools import foundry_state
from foundry_mcp.schemas.vocab import (
    ESCALATION_EXIT_REASONS,
    ESCALATION_STATUSES,
    STRUCTURAL_PASS_BUDGET,
)

# fallout FR-005 / AC-014 / GI-010 / GI-026 — ESCALATION HAS A MODULE, AND
# THE FIVE CONCERNS THIS FILE DRIVES HAVE FIVE.
#
# The `fo` alias reached all of them through one name. The single orchestrator
# module it named is gone and GI-010 forbids a re-export shim standing in for
# it, so each reach names the module that DEFINES the symbol:
# the ledger, its readers and the exit arms are `orchestration/escalation.py`;
# the co-dispatch and directive doors are `orchestration/directives.py`; the
# gate ladder and `_done_preconditions` are `orchestration/gates.py`; the phase
# tokens are `orchestration/transitions.py`; the next-action guidance is
# `orchestration/guidance.py`.
#
# `_current_cycle` is NOT one of them. Casting 10 consolidated the two
# byte-identical copies into `foundry_state.current_cycle` (GI-024, Holmes
# `share-2`), and reaching a split module for that leaf fact would recreate the
# second copy this run just removed.
from foundry_mcp.tools.foundry_state import current_cycle as _current_cycle
from foundry_mcp.tools.orchestration import directives as _directives
from foundry_mcp.tools.orchestration import escalation as _escalation
from foundry_mcp.tools.orchestration import fix_gate as _fix_gate
from foundry_mcp.tools.orchestration import gates as _gates
from foundry_mcp.tools.orchestration import guidance as _guidance
from foundry_mcp.tools.orchestration.directives import (
    foundry_defects_to_tasks,
    foundry_inject_directive,
)
from foundry_mcp.tools.orchestration.escalation import (
    ESCALATION_CYCLES,
    _defect_class,
    _escalated_classes,
)
from foundry_mcp.tools.orchestration.gates import foundry_gate
from foundry_mcp.tools.orchestration.transitions import foundry_mark_phase_complete

# `_check_active_teams` is bound by name in five orchestration modules and
# `STRUCTURAL_PASS_BUDGET` in two, so patching the one that DEFINES either
# leaves every importer resolving the real one.
from tests.orchestration._env import patch_everywhere


# --------------------------------------------------------------------------- #
# Fixtures & helpers
# --------------------------------------------------------------------------- #


@pytest.fixture
def run_env(tmp_path, monkeypatch):
    """Activate a foundry run under tmp_path; yield (project_root, fdir)."""
    project_root = tmp_path
    run_name = "escalation-run"
    fdir = project_root / "foundry-archive" / run_name
    (fdir / "castings").mkdir(parents=True, exist_ok=True)

    patch_everywhere(
        monkeypatch,
        "_check_active_teams",
        lambda _pr: {"active": False, "teams": [], "live_panes": []},
    )

    foundry_state.set_active_run(run_name)
    try:
        yield str(project_root), fdir
    finally:
        foundry_state.clear_active_run()


def _write_state(fdir: Path, phase: str, cycle: int = 0, **extra) -> None:
    state = {"phase": phase, "cycle": cycle}
    state.update(extra)
    (fdir / "state.json").write_text(json.dumps(state), encoding="utf-8")


def _generate_report(project_root: str, fdir: Path) -> dict:
    """GI-006 / CT-014: DONE now requires the SERVER-GENERATED report.

    Generated by calling the real `generate_report`, never by writing a
    `report.json` the test made up. The point of the precondition is that the
    report is produced from the run's own ledgers, and a fabricated one would
    let this fixture pass a gate that a real run in the same state could not.
    """
    from foundry_mcp.tools.foundry_report import generate_report

    result = generate_report(Path(project_root), fdir)
    assert result.get("ok") is True, result
    return result


def _arm(fdir: Path) -> None:
    """Foundry-Next's ordering token, which gate/phase calls consume."""
    (fdir / ".next-action-called").write_text(f"{foundry_state.now_iso()}\n", encoding="utf-8")


def _record_inspect_mode(fdir: Path, *, cycle: int, mode: str = "FULL",
                         rule: str = "final_gate",
                         required: tuple[str, ...] = ("trace", "prove", "test")) -> None:
    """Append the `state.json.inspect_modes` entry a real crossing records.

    D-117: an INSPECT with no recorded width is refused at every door rather
    than admitted as full width, so a fixture that puts a run at F2 has to say
    what width that INSPECT was opened at — which every real F2 has, because
    only a recording transition can reach one.
    """
    state_path = fdir / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    modes = state.get("inspect_modes")
    state["inspect_modes"] = (modes if isinstance(modes, list) else []) + [{
        "cycle": cycle,
        "phase": "F2",
        "mode": mode,
        "rule": rule,
        "rule_detail": "fixture",
        "decided_by": "inspect_start",
        "decided_at": foundry_state.now_iso(),
        "required_streams": list(required),
        "stream_scope": {
            wire: {"scope": "full", "detail": "every item in scope"}
            for wire in required
        },
        "touched_files": [],
        "prove_sample": [],
        "diff_base": "",
    }]
    state_path.write_text(json.dumps(state), encoding="utf-8")


def _defect(did: str, cycle: int, **extra) -> dict:
    d = {
        "id": did,
        "cycle": cycle,
        "source": "trace",
        "type": "UNWIRED",
        "description": f"{did} description",
        "spec_ref": "",
        "symbol": "",
        "file": "src/api/handler.py",
        "status": "open",
        "fixed_in_cycle": None,
    }
    d.update(extra)
    return d


def _write_defects(fdir: Path, defects: list[dict]) -> None:
    (fdir / "defects.json").write_text(
        json.dumps({"defects": defects}, indent=2), encoding="utf-8"
    )


# --------------------------------------------------------------------------- #
# CT-001 / CT-002 — both filing doors now REQUIRE an evidence tier and a
# root-cause class, and refuse without them.
#
# Every parity test below is about something else entirely — which cycle a
# record is stamped with, whether a junk record aborts the transaction, whether
# the two doors agree on an open count. Threading two more fields through each
# call by hand would have edited those assertions to test a requirement they are
# not about, so the fields are supplied once, here, and every call reads as it
# did. `setdefault` on both, so a test that IS about the tier or the class
# overrides them and its override wins.
#
# The default tier is LIVE because these fixtures are all reproduced findings —
# a record filed with `status: open` that a gate is then expected to block on.
# --------------------------------------------------------------------------- #

FIXTURE_TIER = "LIVE"
FIXTURE_CLASS = "FALSE_DOCUMENTED_CONTRACT"


def _sync_defects(cycle: int, findings: list[dict], project_root: str) -> dict:
    """`foundry_sync_defects` with the tier and class every finding now needs."""
    supplied = []
    for finding in findings:
        f = dict(finding)
        f.setdefault("tier", FIXTURE_TIER)
        f.setdefault("class", FIXTURE_CLASS)
        supplied.append(f)
    return _fix_gate.foundry_sync_defects(
        cycle=cycle, findings=supplied, project_root=project_root
    )


def _add_defect(**kwargs) -> dict:
    """`foundry_add_defect` with the same two fields supplied the same way."""
    from foundry_mcp.tools.foundry import foundry_add_defect

    kwargs.setdefault("tier", FIXTURE_TIER)
    kwargs.setdefault("defect_class", FIXTURE_CLASS)
    return foundry_add_defect(**kwargs)


def _recurring(cycles: list[int], klass: str = "FALSE_DOCUMENTED_CONTRACT") -> list[dict]:
    """One new defect of the same declared class in each named cycle."""
    return [
        _defect(f"D-{i:03d}", cycle, **{"class": klass})
        for i, cycle in enumerate(cycles, start=1)
    ]


# --------------------------------------------------------------------------- #
# process-fixes AC-008 / ST-001 / FR-005 — the server-owned cycle counter
# --------------------------------------------------------------------------- #


def test_grind_to_inspect_increments_the_counter(run_env):
    """process-fixes AC-008 verbatim: 'After a GRIND->INSPECT transition,
    the server-side cycle counter has incremented without any
    caller-supplied value.'"""
    project_root, fdir = run_env
    _write_state(fdir, phase="F3", cycle=0)
    _arm(fdir)

    result = foundry_mark_phase_complete("inspect_start", project_root)

    assert result["ok"] is True, result
    assert result["phase"] == "F2"
    assert result["cycle"] == 1
    assert _current_cycle(fdir) == 1


def test_the_boundary_handler_takes_no_cycle_argument(run_env):
    """process-fixes ST-001: 'caller-supplied cycle is not trusted where the
    server knows better'. The handler's signature is the proof — there is no
    cycle argument to supply, so the increment cannot be steered from
    outside.

    fallout CT-004 / ST-001 — THE CLAIM IS THE ABSENCE, NOT THE ROSTER.
    This pinned the parameter set as exactly `{phase, project_root}`, which is
    a stronger statement than the requirement makes and it went red the moment
    the halt token arrived: `Foundry-Phase(phase=halt, reason, text)` carries
    the halt reason and its free text, and neither is a cycle. So the claim is
    asserted directly — no argument names the cycle — and the roster is pinned
    beside it, so a THIRD member still has to be looked at rather than sliding
    in under a set comparison nobody reads.
    """
    import inspect

    params = inspect.signature(foundry_mark_phase_complete).parameters
    assert "cycle" not in params, sorted(params)
    assert not [p for p in params if "cycle" in p], sorted(params)
    assert set(params) == {"phase", "project_root", "reason", "text"}, sorted(params)

    project_root, fdir = run_env
    _write_state(fdir, phase="F3", cycle=7)
    _arm(fdir)

    assert foundry_mark_phase_complete("inspect_start", project_root)["cycle"] == 8


def test_repeated_grind_inspect_loops_advance_one_cycle_each(run_env):
    """The counter tracks GRIND cycles, one per loop — which is what makes
    'three consecutive cycles' a real measurement.

    fallout ST-015 / GI-011 / GI-032 — THE GRIND DOOR HAS PRECONDITIONS NOW.
    This drove `grind_start` on an empty run and read the counter off the reply,
    which the door refuses: `_grind_start_preconditions` requires open defects
    and a `.tasks-generated` marker, and only `foundry_defects_to_tasks` writes
    the second. So the loop runs the tool a real cycle runs, which is exactly
    what `_grind_cycle` above documents — "a test that skips it is driving the
    exit against a record no run produces". The measurement is unchanged: one
    crossing, one cycle.
    """
    project_root, fdir = run_env
    _write_state(fdir, phase="F2", cycle=0)
    _write_defects(fdir, [_defect("D-001", 0, **{
        "class": FIXTURE_CLASS, "tier": FIXTURE_TIER,
    })])

    for expected in (1, 2, 3):
        foundry_defects_to_tasks(project_root)
        _arm(fdir)
        opened = foundry_mark_phase_complete("grind_start", project_root)
        assert opened.get("ok") is True, opened
        _arm(fdir)
        result = foundry_mark_phase_complete("inspect_start", project_root)
        assert result.get("ok") is True, result
        assert result["cycle"] == expected, result


def test_inspect_start_from_cast_is_refused_and_names_the_transition_that_works(run_env):
    """D-116: `inspect_start` from F1 used to return ok, move the run to F2 and
    leave the counter alone — skipping CAST entirely, with `.cast-complete`
    absent and no baseline SHA stamped.

    The run's FIRST INSPECT is opened by the `cast` transition, which is what
    records FULL / first_of_phase and sweeps the corpus (GI-009 / AC-016 /
    OT-012). `inspect_start` is the GRIND->INSPECT crossing and nothing else, so
    from F1 it is refused naming the phase and the call that applies.
    """
    project_root, fdir = run_env
    _write_state(fdir, phase="F1", cycle=0)
    _arm(fdir)

    result = foundry_mark_phase_complete("inspect_start", project_root)

    assert result.get("ok") is not True, result
    assert "F1" in result["error"], result
    assert "inspect_start" in result["error"], result
    assert "Foundry-Phase(phase='cast')" in result["hint"], result
    # fallout ST-012 / GI-011 / GI-029 — the accepted set is a CHECKLIST ROW.
    # It was a top-level key of the refusal. One preconditions function per
    # token now answers as a checklist, and the transition adds no refusal of
    # its own, so the fact lives on the rung that computed it. Read off the row
    # by name rather than by index, because the ladder's order is the gate's
    # business and not this test's subject.
    entry_rung = [
        row for row in result["checklist"]
        if row["check"].startswith("entered_from_accepted_phase")
    ]
    assert len(entry_rung) == 1, result["checklist"]
    assert entry_rung[0]["ok"] is False, entry_rung
    assert entry_rung[0]["accepted_from"] == ["F3", "F2"], entry_rung
    # Neither the phase nor the counter moved, and CAST was not skipped.
    state = json.loads((fdir / "state.json").read_text(encoding="utf-8"))
    assert state["phase"] == "F1"
    assert _current_cycle(fdir) == 0
    assert not (fdir / ".cast-complete").exists()


@pytest.mark.parametrize("phase", ["F0", "F1", "F4", "F5", "F5.5", "F6"])
def test_inspect_start_is_refused_from_every_phase_but_f3_and_f2(run_env, phase):
    """D-113 / D-114 / D-116 — the LEAD RULING as one property over every phase.

    The source phases are DERIVED from `_compute_next_action`'s own
    `phase == "<literal>"` comparisons rather than listed beside this test, so a
    phase added to the guidance engine cannot quietly acquire an unguarded
    `inspect_start` — the same discipline `_handler_phase_tokens` applies to the
    token set one axis over.

    Refused from all of them; accepted from F3 (the crossing ST-005 names) and
    from F2 (the widening re-open), both of which ADVANCE the counter. That is
    what makes "the escalation exit arms evaluate only on a transition that
    advanced the counter" true by construction rather than by convention.
    """
    assert phase in _guidance_phases(), (
        f"{phase} is no longer a phase the guidance engine branches on; "
        "the derived set moved and this roster did not"
    )
    project_root, fdir = run_env
    _write_state(fdir, phase=phase, cycle=3)
    _arm(fdir)

    result = foundry_mark_phase_complete("inspect_start", project_root)

    assert result.get("ok") is not True, (phase, result)
    assert phase in result["error"], (phase, result)
    assert result["hint"], (phase, result)
    assert json.loads(
        (fdir / "state.json").read_text(encoding="utf-8")
    )["phase"] == phase, phase
    assert _current_cycle(fdir) == 3, phase


def _guidance_phases() -> set[str]:
    """Every run phase `_compute_next_action` branches on, from its own AST.

    Read out of the function rather than maintained beside it, for the reason
    `_handler_phase_tokens` in `test_orchestrator_gates.py` is: a guard that can
    be satisfied by updating a copy is a guard against nothing.
    """
    import ast
    import inspect
    import textwrap

    tree = ast.parse(textwrap.dedent(inspect.getsource(_guidance._compute_next_action)))
    phases: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Compare):
            continue
        if not (isinstance(node.left, ast.Name) and node.left.id == "phase"):
            continue
        for op, comparator in zip(node.ops, node.comparators):
            if isinstance(op, ast.Eq) and isinstance(comparator, ast.Constant):
                if isinstance(comparator.value, str):
                    phases.add(comparator.value)
    return phases


def test_every_guidance_phase_but_the_two_crossings_refuses_inspect_start(run_env):
    """The other direction of the roster above, so neither can drift alone.

    Every phase the guidance engine knows about is either one of the two
    accepted sources or is covered by the parametrised refusal test. A new phase
    reaches this assertion before it reaches production.
    """
    project_root, fdir = run_env
    covered = {"F0", "F1", "F4", "F5", "F5.5", "F6"} | {"F2", "F3"}
    assert _guidance_phases() <= covered, sorted(_guidance_phases() - covered)

    # And F3 really is accepted, so the roster is not vacuously satisfied by a
    # guard that refuses everything.
    _write_state(fdir, phase="F3", cycle=3)
    _arm(fdir)
    result = foundry_mark_phase_complete("inspect_start", project_root)
    assert result.get("ok") is True, result
    assert result["cycle"] == 4


def test_a_malformed_counter_reads_as_zero_rather_than_raising(run_env):
    """Every reader gets a usable integer: an archive carrying a string or a
    negative must not take the guidance engine down."""
    _project_root, fdir = run_env
    for bad in ("seven", -3, None, True):
        _write_state(fdir, phase="F2")
        state = json.loads((fdir / "state.json").read_text(encoding="utf-8"))
        state["cycle"] = bad
        (fdir / "state.json").write_text(json.dumps(state), encoding="utf-8")
        assert _current_cycle(fdir) == 0


def test_inspect_start_is_a_recognized_phase_token(run_env):
    """The token has to exist to be callable: before this the guidance engine
    told the lead to 'update state to F2' with no tool that does it, which is
    why grand-vulture's state.json read cycle 0 across 18 cycles."""
    project_root, fdir = run_env
    _write_state(fdir, phase="F3")
    _arm(fdir)

    assert "error" not in foundry_mark_phase_complete("inspect_start", project_root)

    _arm(fdir)
    err = foundry_mark_phase_complete("not_a_phase", project_root)
    assert "inspect_start" in err["error"]


# --------------------------------------------------------------------------- #
# process-fixes AC-009 / ST-002 / FR-006 — three consecutive cycles fire,
# two do not
# --------------------------------------------------------------------------- #


def test_escalation_fires_on_the_third_consecutive_cycle(run_env):
    """process-fixes AC-009 / OT-003: the synthetic 3-cycle fixture. The same
    class filed in three consecutive server-counted cycles escalates."""
    project_root, fdir = run_env
    _write_state(fdir, phase="F2", cycle=2)
    _write_defects(fdir, _recurring([0, 1, 2]))

    escalated = _escalated_classes(fdir, project_root)

    assert list(escalated) == ["FALSE_DOCUMENTED_CONTRACT"]
    assert escalated["FALSE_DOCUMENTED_CONTRACT"]["consecutive_cycles"] == 3
    assert escalated["FALSE_DOCUMENTED_CONTRACT"]["escalated_at_cycle"] == 2


def test_two_consecutive_cycles_do_not_fire_escalation(run_env):
    """process-fixes AC-009's negative half — the one that keeps N=3
    meaningful."""
    project_root, fdir = run_env
    _write_state(fdir, phase="F2", cycle=1)
    _write_defects(fdir, _recurring([0, 1]))

    assert _escalated_classes(fdir, project_root) == {}


def test_non_consecutive_cycles_do_not_fire_escalation(run_env):
    """process-fixes FR-006 says CONSECUTIVE. A class that appears in cycles
    0, 1 and 3 broke its run — cycle 2 is evidence a fix held, however
    briefly."""
    project_root, fdir = run_env
    _write_state(fdir, phase="F2", cycle=3)
    _write_defects(fdir, _recurring([0, 1, 3]))

    assert _escalated_classes(fdir, project_root) == {}


def test_a_run_of_three_inside_a_longer_gappy_history_still_fires(run_env):
    """Cycles 0, 2, 3, 4: the 2-3-4 run qualifies even though cycle 1 is a gap.
    grand-vulture's eight-cycle run is this shape."""
    project_root, fdir = run_env
    _write_state(fdir, phase="F2", cycle=4)
    _write_defects(fdir, _recurring([0, 2, 3, 4]))

    escalated = _escalated_classes(fdir, project_root)
    assert escalated["FALSE_DOCUMENTED_CONTRACT"]["consecutive_cycles"] == 3
    assert escalated["FALSE_DOCUMENTED_CONTRACT"]["escalated_at_cycle"] == 4


def test_a_regression_reopen_counts_as_the_class_recurring(run_env):
    """A class reopening IS the class recurring, which is exactly what
    escalation exists to catch — so reopened_in_cycle accumulates too."""
    project_root, fdir = run_env
    _write_state(fdir, phase="F2", cycle=2)
    _write_defects(fdir, [
        _defect("D-001", 0, **{"class": "K"}),
        _defect("D-002", 1, **{"class": "K"}),
        _defect("D-003", 0, reopened_in_cycle=2, regression=True, **{"class": "K"}),
    ])

    assert "K" in _escalated_classes(fdir, project_root)


def test_a_class_with_no_open_defects_is_not_escalated(run_env):
    """process-fixes ST-003 CLEARED: once every defect of the class closes,
    the class stops producing a structural packet. Escalation describes work
    outstanding."""
    project_root, fdir = run_env
    _write_state(fdir, phase="F2", cycle=2)
    defects = _recurring([0, 1, 2])
    for d in defects:
        d["status"] = "fixed"
        d["fixed_in_cycle"] = 2
    _write_defects(fdir, defects)

    assert _escalated_classes(fdir, project_root) == {}


# --------------------------------------------------------------------------- #
# process-fixes FR-007 / FR-024 / ST-002 (A-033) — class identity
# --------------------------------------------------------------------------- #


def test_a_stream_declared_class_is_the_class(run_env):
    """process-fixes FR-007: the optional stream-declared ``class`` field wins
    outright — it is the assayer's systemic_patterns[] output finally being
    consumed."""
    assert _defect_class({"class": "FALSE_DOCUMENTED_CONTRACT", "type": "WRONG",
                          "file": "a/b/c.py"}) == "FALSE_DOCUMENTED_CONTRACT"
    # Whitespace-only is not a declaration.
    assert _defect_class({"class": "   ", "type": "WRONG", "file": "a/b/c.py"}) != "   "


def test_fallback_clusters_on_type_plus_file_cluster(run_env):
    """process-fixes FR-024 (implementer-tunable, chosen rule documented at
    the constant): the fallback is the canonical defect type joined with the
    first FALLBACK_CLUSTER_DEPTH path segments. Same type + same subsystem is
    one class; a different subsystem is a different class."""
    assert _escalation.FALLBACK_CLUSTER_DEPTH == 2

    same_a = _defect_class({"type": "UNWIRED", "file": "src/api/login.py"})
    same_b = _defect_class({"type": "UNWIRED", "file": "src/api/session.py"})
    deeper = _defect_class({"type": "UNWIRED", "file": "src/api/auth/token.py"})
    other_subsystem = _defect_class({"type": "UNWIRED", "file": "src/ui/Login.tsx"})
    other_type = _defect_class({"type": "MISSING", "file": "src/api/login.py"})

    assert same_a == same_b == deeper == "UNWIRED@src/api"
    assert other_subsystem != same_a
    assert other_type != same_a


def test_fallback_folds_defect_type_aliases_together(run_env):
    """MISPLACED and ARCHITECTURAL_PLACEMENT are the same type under two live
    spellings; clustering them apart would halve every count."""
    a = _defect_class({"type": "MISPLACED", "file": "src/api/x.py"})
    b = _defect_class({"type": "ARCHITECTURAL_PLACEMENT", "file": "src/api/x.py"})
    assert a == b == "ARCHITECTURAL_PLACEMENT@src/api"


def test_fallback_handles_a_defect_with_no_file(run_env):
    """No file attribution still yields a stable key rather than raising."""
    assert _defect_class({"type": "THIN"}) == "THIN@-"
    assert _defect_class({"type": "THIN", "file": "toplevel.py"}) == "THIN@-"


def test_the_fallback_can_accumulate_the_three_cycle_count(run_env):
    """process-fixes ST-002 / A-033: 'Escalation keys on stream-declared class
    when present, and on the fallback clustering when absent — either can
    accumulate.'"""
    project_root, fdir = run_env
    _write_state(fdir, phase="F2", cycle=2)
    _write_defects(fdir, [
        _defect("D-001", 0, type="UNWIRED", file="src/api/login.py"),
        _defect("D-002", 1, type="UNWIRED", file="src/api/session.py"),
        _defect("D-003", 2, type="UNWIRED", file="src/api/refresh.py"),
    ])

    escalated = _escalated_classes(fdir, project_root)

    assert list(escalated) == ["UNWIRED@src/api"]
    assert escalated["UNWIRED@src/api"]["declared"] is False
    assert escalated["UNWIRED@src/api"]["files"] == [
        "src/api/login.py", "src/api/refresh.py", "src/api/session.py"
    ]


# --------------------------------------------------------------------------- #
# process-fixes AC-010 / FR-008 / ST-003 / OT-003 — one structural packet
# per escalated class
# --------------------------------------------------------------------------- #


def test_escalated_class_produces_exactly_one_structural_task(run_env):
    """process-fixes AC-010 / OT-003: 'Foundry-Tasks emits exactly one
    structural-fix task for that class'. These three defects live in three
    different files, so the old location grouping produced three unrelated
    packets."""
    project_root, fdir = run_env
    _write_state(fdir, phase="F2", cycle=2)
    _write_defects(fdir, [
        _defect("D-001", 0, file="src/api/a.py", **{"class": "K"}),
        _defect("D-002", 1, file="src/api/b.py", **{"class": "K"}),
        _defect("D-003", 2, file="src/api/c.py", **{"class": "K"}),
    ])

    result = foundry_defects_to_tasks(project_root)

    structural = [t for t in result["tasks"] if t["structural"]]
    assert len(structural) == 1
    assert result["structural_tasks"] == 1
    assert structural[0]["defect_class"] == "K"
    assert sorted(structural[0]["defect_ids"]) == ["D-001", "D-002", "D-003"]
    assert len(structural[0]["instances"]) == 3
    # No per-instance packet duplicates the escalated defects.
    assert len(result["tasks"]) == 1


def test_packets_for_every_other_defect_are_unaffected(run_env):
    """process-fixes AC-010: 'packets for all other defects are unaffected'.
    Escalation changes the shape of ONE class's work and nothing else."""
    project_root, fdir = run_env
    _write_state(fdir, phase="F2", cycle=2)
    _write_defects(fdir, [
        _defect("D-001", 0, file="src/api/a.py", **{"class": "K"}),
        _defect("D-002", 1, file="src/api/b.py", **{"class": "K"}),
        _defect("D-003", 2, file="src/api/c.py", **{"class": "K"}),
        _defect("D-010", 2, type="MISSING", file="src/ui/Widget.tsx"),
        _defect("D-011", 2, type="MISSING", file="src/ui/Panel.tsx"),
    ])

    result = foundry_defects_to_tasks(project_root)

    normal = [t for t in result["tasks"] if not t["structural"]]
    ids = sorted(i for t in normal for i in t["defect_ids"])
    assert ids == ["D-010", "D-011"]
    # Still grouped by location, still chunked, exactly as before.
    assert len(normal) == 2


def test_the_structural_packet_carries_a_recorded_proposal(run_env):
    """process-fixes FR-008 / ST-003: 'one structural packet with a recorded
    proposal'. It states the evidence that made this systemic and that closure
    is not waived."""
    project_root, fdir = run_env
    _write_state(fdir, phase="F2", cycle=2)
    _write_defects(fdir, _recurring([0, 1, 2]))

    task = [t for t in foundry_defects_to_tasks(project_root)["tasks"] if t["structural"]][0]

    assert task["proposal"]
    assert "STRUCTURAL FIX REQUIRED" in task["proposal"]
    assert "FALSE_DOCUMENTED_CONTRACT" in task["proposal"]
    assert "3 consecutive cycles" in task["proposal"]
    assert "not waived" in task["proposal"].lower()
    assert "escalation-override" in task["proposal"]


def test_the_proposal_is_persisted_not_only_returned(run_env):
    """process-fixes ST-003 says the proposal is RECORDED on the class's
    packet. Holding it only in the returned dict would lose it the moment the
    lead moved on."""
    project_root, fdir = run_env
    _write_state(fdir, phase="F2", cycle=2)
    _write_defects(fdir, _recurring([0, 1, 2]))

    foundry_defects_to_tasks(project_root)

    stored = json.loads((fdir / "escalation.json").read_text(encoding="utf-8"))
    entry = stored["classes"]["FALSE_DOCUMENTED_CONTRACT"]
    assert "STRUCTURAL FIX REQUIRED" in entry["proposal"]
    assert entry["consecutive_cycles"] == 3
    assert sorted(entry["defect_ids"]) == ["D-001", "D-002", "D-003"]


def test_a_non_escalated_run_produces_the_same_tasks_as_before(run_env):
    """The no-escalation path is untouched: location grouping, MAX_PER_GROUP
    chunking, and no structural packets."""
    project_root, fdir = run_env
    _write_state(fdir, phase="F2", cycle=1)
    _write_defects(fdir, [
        _defect(f"D-{i:03d}", 1, file="src/api/a.py") for i in range(1, 5)
    ])

    result = foundry_defects_to_tasks(project_root)

    assert result["structural_tasks"] == 0
    assert result["escalated_classes"] == []
    assert len(result["tasks"]) == 2      # 4 defects, MAX_PER_GROUP = 3
    assert [len(t["defect_ids"]) for t in result["tasks"]] == [3, 1]


# --------------------------------------------------------------------------- #
# process-fixes AC-010 — the explicit directive override
# --------------------------------------------------------------------------- #


def test_a_scoped_directive_override_restores_per_instance_packets(run_env):
    """process-fixes AC-010: 'an explicit directive override restores
    per-instance packets'."""
    project_root, fdir = run_env
    _write_state(fdir, phase="F2", cycle=2)
    _write_defects(fdir, [
        _defect("D-001", 0, file="src/api/a.py", **{"class": "K"}),
        _defect("D-002", 1, file="src/api/b.py", **{"class": "K"}),
        _defect("D-003", 2, file="src/api/c.py", **{"class": "K"}),
    ])
    assert foundry_defects_to_tasks(project_root)["structural_tasks"] == 1

    foundry_inject_directive("escalation-override: K", project_root=project_root)

    result = foundry_defects_to_tasks(project_root)
    assert result["structural_tasks"] == 0
    assert result["escalated_classes"] == []
    # Back to one packet per location, exactly as an unescalated class.
    assert sorted(i for t in result["tasks"] for i in t["defect_ids"]) == [
        "D-001", "D-002", "D-003"
    ]
    assert len(result["tasks"]) == 3


def test_a_bare_override_directive_de_escalates_every_class(run_env):
    """The blanket form, for a lead that judges the whole heuristic wrong on
    this run."""
    project_root, fdir = run_env
    _write_state(fdir, phase="F2", cycle=2)
    _write_defects(
        fdir,
        _recurring([0, 1, 2], klass="K1")
        + [_defect(f"D-1{i:02d}", c, **{"class": "K2"}) for i, c in enumerate([0, 1, 2])],
    )
    assert len(_escalated_classes(fdir, project_root)) == 2

    foundry_inject_directive("escalation-override", project_root=project_root)

    assert _escalated_classes(fdir, project_root) == {}


def test_a_scoped_override_leaves_other_classes_escalated(run_env):
    """Scoping means scoping: overriding one class does not disarm the rest."""
    project_root, fdir = run_env
    _write_state(fdir, phase="F2", cycle=2)
    _write_defects(
        fdir,
        _recurring([0, 1, 2], klass="K1")
        + [_defect(f"D-1{i:02d}", c, **{"class": "K2"}) for i, c in enumerate([0, 1, 2])],
    )

    foundry_inject_directive("escalation-override: K1", project_root=project_root)

    assert list(_escalated_classes(fdir, project_root)) == ["K2"]


def test_an_unrelated_directive_does_not_de_escalate(run_env):
    """The override must be explicit — ordinary human steering must not
    accidentally disarm escalation."""
    project_root, fdir = run_env
    _write_state(fdir, phase="F2", cycle=2)
    _write_defects(fdir, _recurring([0, 1, 2]))

    foundry_inject_directive(
        "Focus on the auth domain first, please.", project_root=project_root
    )

    assert list(_escalated_classes(fdir, project_root)) == ["FALSE_DOCUMENTED_CONTRACT"]


# --------------------------------------------------------------------------- #
# process-fixes AC-011 — escalation never waives closure
# --------------------------------------------------------------------------- #


def test_done_gate_refuses_while_an_escalated_class_has_open_defects(run_env):
    """process-fixes AC-011 verbatim: 'the run cannot reach DONE while any
    escalated-class defect remains open'. Stated as its own named check so the
    guarantee is visible rather than merely implied by the open-defect
    count."""
    project_root, fdir = run_env
    _write_state(fdir, phase="F4", cycle=2)
    _write_defects(fdir, _recurring([0, 1, 2]))
    (fdir / "verdicts.json").write_text(
        json.dumps({"requirements": []}), encoding="utf-8"
    )
    _arm(fdir)

    result = foundry_gate("done", project_root)

    assert result["passed"] is False
    checks = {c["check"]: c for c in result["checklist"]}
    escalation_check = next(
        k for k in checks if k.startswith("escalated_classes_cleared")
    )
    assert checks[escalation_check]["ok"] is False
    assert checks[escalation_check]["classes"] == ["FALSE_DOCUMENTED_CONTRACT"]


def test_done_gate_escalation_check_passes_once_the_class_closes(run_env):
    """process-fixes ST-003 CLEARED: the structural fix still has to close
    every instance. When it does, the check clears — closure is the only
    exit."""
    project_root, fdir = run_env
    _write_state(fdir, phase="F4", cycle=2)
    defects = _recurring([0, 1, 2])
    for d in defects:
        d["status"] = "fixed"
        d["fixed_in_cycle"] = 3
    _write_defects(fdir, defects)
    (fdir / "verdicts.json").write_text(
        json.dumps({"requirements": []}), encoding="utf-8"
    )
    _arm(fdir)

    result = foundry_gate("done", project_root)

    checks = {c["check"]: c for c in result["checklist"]}
    escalation_check = next(
        k for k in checks if k.startswith("escalated_classes_cleared")
    )
    assert checks[escalation_check]["ok"] is True
    assert checks[escalation_check]["classes"] == []


def test_the_done_transition_itself_refuses_an_open_escalated_class(run_env):
    """D-037 — process-fixes AC-011 constrains the RUN, so the TRANSITION
    must enforce it.

    ``foundry_mark_phase_complete("done")`` was an unconditional
    ``_update_phase(F6)`` + ``clear_active_run()``: it read no verdicts, no open
    defects and no escalated classes. Every check above lived in
    ``foundry_gate("done")``, which is advisory — a lead that simply did not
    call it archived the run. Driven before the fix: DONE reached with six open
    escalated-class defects and zero verdicts, which is precisely what
    process-fixes AC-011 says cannot happen.
    """
    project_root, fdir = run_env
    _write_state(fdir, phase="F4", cycle=2)
    _write_defects(fdir, _recurring([0, 1, 2]))
    (fdir / "verdicts.json").write_text(
        json.dumps({"requirements": []}), encoding="utf-8"
    )
    _arm(fdir)

    result = foundry_mark_phase_complete("done", project_root)

    assert result.get("ok") is not True, result
    assert "error" in result
    # The named-refusal shape: what is wrong, and what to do about it.
    assert "DONE" in result["error"]
    assert result["hint"]
    # The run did NOT advance.
    assert json.loads((fdir / "state.json").read_text(encoding="utf-8"))["phase"] == "F4"

    # The escalated-class check is the one that is visible in the checklist,
    # named, alongside the others the gate enforces.
    checks = {c["check"]: c for c in result["checklist"]}
    escalation_check = next(
        k for k in checks if k.startswith("escalated_classes_cleared")
    )
    assert checks[escalation_check]["ok"] is False
    assert checks[escalation_check]["classes"] == ["FALSE_DOCUMENTED_CONTRACT"]


def test_the_done_transition_enforces_exactly_the_gates_checks(run_env):
    """The other half of D-037, and the trap beside it: the transition must not
    refuse on conditions the gate does not enforce.

    The two consult ONE evaluation (``_done_preconditions``), so this asserts
    the property directly rather than by re-listing the checks: for the same
    run state, the gate's verdict and the transition's agree. A transition
    stricter than its own gate is unsatisfiable — the lead is told the run is
    ready and then refused.
    """
    project_root, fdir = run_env
    _write_state(fdir, phase="F4", cycle=2)
    _write_defects(fdir, _recurring([0, 1, 2]))
    (fdir / "verdicts.json").write_text(
        json.dumps({"requirements": []}), encoding="utf-8"
    )

    _arm(fdir)
    gate = foundry_gate("done", project_root)
    _arm(fdir)
    transition = foundry_mark_phase_complete("done", project_root)

    assert gate["passed"] is False
    assert transition.get("ok") is not True
    # Same reason, same checklist — one evaluation, two callers.
    assert gate["reason"] in transition["error"]
    assert transition["checklist"] == gate["checklist"]


def test_the_done_transition_advances_once_the_gate_would_pass(run_env, monkeypatch):
    """Closure is the exit, not a waiver. When the preconditions the gate names
    are actually met, the transition archives the run as it always did — the
    refusal is a precondition, not a new phase the lead cannot leave."""
    project_root, fdir = run_env
    _write_state(fdir, phase="F4", cycle=3)

    # Every defect of the escalated class closed (process-fixes ST-003
    # CLEARED)...
    defects = _recurring([0, 1, 2])
    for d in defects:
        d["status"] = "fixed"
        d["fixed_in_cycle"] = 3
    _write_defects(fdir, defects)
    # ...and the spec's requirements all carry VERIFIED verdicts.
    (fdir / "spec.md").write_text(
        "- FR-001: the thing works\n- FR-002: the other thing works\n",
        encoding="utf-8",
    )
    (fdir / "verdicts.json").write_text(
        json.dumps({"requirements": [
            {"requirement_id": "FR-001", "verdict": "VERIFIED"},
            {"requirement_id": "FR-002", "verdict": "VERIFIED"},
        ]}),
        encoding="utf-8",
    )
    # ...and the report GI-006 requires exists (CT-014 / ST-010).
    _generate_report(project_root, fdir)

    _arm(fdir)
    gate = foundry_gate("done", project_root)
    assert gate["passed"] is True, gate

    _arm(fdir)
    result = foundry_mark_phase_complete("done", project_root)

    assert result["ok"] is True, result
    assert result["phase"] == "F6"
    assert json.loads((fdir / "state.json").read_text(encoding="utf-8"))["phase"] == "F6"


# --------------------------------------------------------------------------- #
# process-fixes AC-011 — F6 has TWO doors and both are locked (D-043 / D-044)
#
# D-037 bound _done_preconditions to foundry_mark_phase_complete's `done`
# branch and left the sibling terminal branch, `nyquist_done`, unbound: an
# unconditional _update_phase("F6") + clear_active_run() under a comment
# asserting "The DONE gate still runs first". Nothing in code enforced that.
# commands/start.md:578 routes a --nyquist run through
# Foundry-Gate("done") -> Foundry-Phase("nyquist_done"), so on the runs that
# opt into F5.5 it was the door the run would actually use — and
# foundry_gate had no `nyquist_done` case at all, so there was no server-side
# gate a caller could invoke for the token either.
#
# process-fixes AC-011's words are "the RUN cannot reach DONE while any
# escalated-class defect remains open", with no exception for how F6 is
# entered.
# --------------------------------------------------------------------------- #


def test_the_nyquist_done_transition_refuses_an_open_escalated_class(run_env):
    """D-043 / D-044 stated as the state that was driven through it: six open
    escalated-class defects and no verdicts at all reached F6 in one call."""
    project_root, fdir = run_env
    _write_state(fdir, phase="F5.5", cycle=2, nyquist=True)
    _write_defects(fdir, _recurring([0, 1, 2]))
    (fdir / "verdicts.json").write_text(
        json.dumps({"requirements": []}), encoding="utf-8"
    )
    _arm(fdir)

    result = foundry_mark_phase_complete("nyquist_done", project_root)

    assert result.get("ok") is not True, result
    assert "error" in result
    assert result["hint"]
    # The run did NOT advance, and the active run was NOT cleared.
    assert json.loads((fdir / "state.json").read_text(encoding="utf-8"))["phase"] == "F5.5"
    assert foundry_state.get_active_run() is not None

    checks = {c["check"]: c for c in result["checklist"]}
    escalation_check = next(
        k for k in checks if k.startswith("escalated_classes_cleared")
    )
    assert checks[escalation_check]["ok"] is False
    assert checks[escalation_check]["classes"] == ["FALSE_DOCUMENTED_CONTRACT"]


# --------------------------------------------------------------------------- #
# D-002 — THE ESCALATED-CLASS CHECK IS TIER-AWARE, LIKE ITS SIBLINGS.
#
# `_done_preconditions` counted an escalated class's open instances regardless
# of tier while every sibling branch in the same function reads
# `_blocking_defects`. Driven: three open LATENT instances of one class across
# cycles 1-3 gave blocking={live:[], unknown:[], latent:[D-001,D-002,D-003]} and
# done_preconditions passed=False, "1 escalated defect class(es) still have open
# instances" — against FR-006 verbatim for the DEFECT axis (D-034's lead ruling
# later restored the CLASS axis: a still-ESCALATED class blocks DONE on its own
# terms, whatever the tier of its open instances). And `foundry_gate("nyquist")` consults
# `_escalated_classes` not at all, so the two F6 doors disagreed about identical
# run state.
# --------------------------------------------------------------------------- #


def _latent_recurring(cycles: list[int], klass: str = "FALSE_DOCUMENTED_CONTRACT") -> list[dict]:
    """`_recurring`, one tier along: nothing anyone ever reproduced."""
    return [
        _defect(f"D-{i:03d}", cycle, **{
            "class": klass,
            "tier": "LATENT",
            "reproduction_attempted": "drove every call site; no instance reachable",
        })
        for i, cycle in enumerate(cycles, start=1)
    ]


def _ready_for_f6(fdir: Path, defects: list[dict]) -> None:
    """A run whose only remaining question is the defects under test."""
    _write_state(fdir, phase="F5.5", cycle=2, nyquist=True)
    _write_defects(fdir, defects)
    (fdir / "verdicts.json").write_text(
        json.dumps({"requirements": []}), encoding="utf-8"
    )


def test_a_latent_only_escalated_class_still_blocks_done_until_an_arm_fires(run_env):
    """D-034 — LEAD RULING on the ST-010 / FR-006 tension: BOTH guards apply.

    ST-010's guard is 'every escalated class CLEARED'. This branch used to
    relax it to 'no open LIVE instance in an escalated class', reasoning from
    FR-006 that a LATENT-only backlog leaves no work of either shape. But
    FR-006 is about the DEFECTS and ST-010 is about the CLASS, and the relaxed
    reading dropped the second axis entirely: a class could sit at ESCALATED
    forever, having had neither a structural pass nor two clean cycles, and
    DONE would pass it because its open instances all happened to be LATENT.

    The ruling: a LATENT-only backlog does not by itself clear a class. An arm
    does — ST-001's two clean cycles or ST-002's structural budget — and both
    are mechanical, so this cannot hold a run open indefinitely.
    """
    project_root, fdir = run_env
    _ready_for_f6(fdir, _latent_recurring([0, 1, 2]))

    outcome = _gates._done_preconditions(fdir, project_root)

    assert outcome["passed"] is False
    assert "still ESCALATED" in outcome["reason"]
    assert "FALSE_DOCUMENTED_CONTRACT" in outcome["reason"]
    checks = {c["check"]: c for c in outcome["checklist"]}
    escalation_check = next(
        k for k in checks if k.startswith("escalated_classes_cleared")
    )
    assert checks[escalation_check]["ok"] is False
    assert checks[escalation_check]["classes"] == ["FALSE_DOCUMENTED_CONTRACT"]
    # Still named on its own axis, because this class clears by a DIFFERENT
    # route from one with LIVE work open: it waits for an arm rather than for
    # defects to be fixed, and the hint has to say so.
    assert checks[escalation_check]["latent_only_classes"] == [
        "FALSE_DOCUMENTED_CONTRACT"
    ]
    # D-111: the hint names the ARM and the DISTANCE, per class. The sentence it
    # replaced ("cross the GRIND->INSPECT boundary so the clean-cycle arm
    # counts, or spend the structural budget") named two routes that were both
    # no-ops for a class with no persisted record — a lead followed it six times
    # and moved nothing.
    assert "Distance to each exit" in outcome["hint"], outcome["hint"]
    assert "FALSE_DOCUMENTED_CONTRACT: 3 more INSPECT crossing(s)" in outcome["hint"]
    assert "2 more structural packet(s)" in outcome["hint"]
    assert "CONSECUTIVE" in outcome["hint"]


def test_the_cleared_class_that_carries_a_latent_backlog_passes_done(run_env):
    """The ruling's exit, and the proof it terminates: once an arm has fired
    and `escalation.json` records CLEARED, the LATENT backlog is carried to the
    F6 report exactly as FR-001 and ST-002 say, and DONE passes.

    This is the half that makes the guard a precondition rather than a trap.
    """
    project_root, fdir = run_env
    _ready_for_f6(fdir, _latent_recurring([0, 1, 2]))
    (fdir / "escalation.json").write_text(json.dumps({"classes": {
        "FALSE_DOCUMENTED_CONTRACT": {
            "status": "CLEARED",
            "exit_reason": "budget",
            "cleared_at_cycle": 2,
            "structural_packets_dispatched": 2,
            "open_latent_defect_ids": ["D-001", "D-002", "D-003"],
        }
    }}), encoding="utf-8")

    outcome = _gates._done_preconditions(fdir, project_root)

    checks = {c["check"]: c for c in outcome["checklist"]}
    escalation_check = next(
        k for k in checks if k.startswith("escalated_classes_cleared")
    )
    assert checks[escalation_check]["ok"] is True, outcome["reason"]
    assert "ESCALATED" not in outcome["reason"]


def test_a_persisted_escalated_class_blocks_done_even_with_nothing_open(run_env):
    """ST-010 verbatim: 'every escalated class CLEARED'. D-059.

    The guard measured "still escalated" solely from `_escalated_classes`, whose
    first two lines are `if not bucket["open"]: continue` and `if run_len <
    ESCALATION_CYCLES: continue` — so a class with zero open instances was
    invisible to it WHATEVER escalation.json said. Only the boundary arms write
    CLEARED, and they need two crossings, while ONE clean crossing is enough to
    pass ASSAY/TEMPER/NYQUIST and reach DONE. Driven: all instances fixed,
    escalation.json still `status: ESCALATED` -> Foundry-Gate('done') PASSED
    with checklist "escalated_classes_cleared (still escalated=0)" while the
    run's own report said by_status {CLEARED: 0, ESCALATED: 1}. Two artifacts of
    one run contradicting each other about whether it had finished.
    """
    project_root, fdir = run_env
    defects = _latent_recurring([0, 1, 2])
    for d in defects:
        d["status"] = "fixed"
        d["fixed_in_cycle"] = 2
    _ready_for_f6(fdir, defects)
    (fdir / "escalation.json").write_text(json.dumps({"classes": {
        "FALSE_DOCUMENTED_CONTRACT": {"status": "ESCALATED", "exit_reason": None}
    }}), encoding="utf-8")

    outcome = _gates._done_preconditions(fdir, project_root)

    checks = {c["check"]: c for c in outcome["checklist"]}
    escalation_check = next(
        k for k in checks if k.startswith("escalated_classes_cleared")
    )
    assert checks[escalation_check]["ok"] is False, outcome["reason"]
    assert checks[escalation_check]["persisted_escalated_classes"] == [
        "FALSE_DOCUMENTED_CONTRACT"
    ]
    # The ledger recurrence half sees nothing — which is exactly the blindness.
    assert checks[escalation_check]["recurring_classes"] == []
    assert "FALSE_DOCUMENTED_CONTRACT" in outcome["reason"]


def test_the_persisted_guard_terminates_through_the_clean_arm(run_env):
    """...and it is not the deadlock the D-034 ruling forbids.

    A class whose instances have all closed draws zero LIVE instances by
    construction, so the clean arm counts every crossing and CLEARS it within
    LIVE_CLEAN_CYCLES_TO_CLEAR boundaries. The guard is therefore a wait, not a
    wall: the run finishes by doing the thing ST-001 describes.
    """
    project_root, fdir = run_env
    _escalate(fdir, project_root)
    defects = json.loads((fdir / "defects.json").read_text(encoding="utf-8"))["defects"]
    for d in defects:
        d["status"] = "fixed"
        d["fixed_in_cycle"] = 3
    _write_defects(fdir, defects)

    assert _gates._done_preconditions(fdir, project_root)["passed"] is False

    for _ in range(3):
        _cross_boundary(fdir, project_root)

    assert _escalation_entry(fdir)["status"] == "CLEARED"
    outcome = _gates._done_preconditions(fdir, project_root)
    checks = {c["check"]: c for c in outcome["checklist"]}
    escalation_check = next(
        k for k in checks if k.startswith("escalated_classes_cleared")
    )
    assert checks[escalation_check]["ok"] is True, outcome["reason"]


def test_an_operator_override_still_clears_the_persisted_guard(run_env):
    """The half of the D-034 ruling that keeps the guard where it is.

    Escalation overrides are filtered inside `_escalated_classes`, and
    `_persisted_escalations` applies exactly the same filter — so a DONE guard
    reading the union still does not refuse a run the operator explicitly
    de-escalated. A guard that read escalation.json raw would.
    """
    project_root, fdir = run_env
    defects = _latent_recurring([0, 1, 2])
    for d in defects:
        d["status"] = "fixed"
        d["fixed_in_cycle"] = 2
    _ready_for_f6(fdir, defects)
    (fdir / "escalation.json").write_text(json.dumps({"classes": {
        "FALSE_DOCUMENTED_CONTRACT": {"status": "ESCALATED", "exit_reason": None}
    }}), encoding="utf-8")
    foundry_inject_directive(
        _escalation._override_instruction("FALSE_DOCUMENTED_CONTRACT"),
        project_root=project_root,
    )

    outcome = _gates._done_preconditions(fdir, project_root)

    checks = {c["check"]: c for c in outcome["checklist"]}
    escalation_check = next(
        k for k in checks if k.startswith("escalated_classes_cleared")
    )
    assert checks[escalation_check]["ok"] is True, outcome["reason"]


# --------------------------------------------------------------------------- #
# D-210 — THE CLOSED VOCABULARY, ON THE READS THAT DECIDE (ST-010)
#
# `ESCALATION_STATUSES` was consulted by `foundry_report.py` and
# `scripts/measure-run.py` — two REPORT BUILDERS — and by neither deciding
# read. So a persisted status that was neither member was not ESCALATED at one
# door and not CLEARED at the other: every door agreed the class was neither,
# and DONE passed. The absent-status default was always right; it was the
# PRESENT-but-unknown value that failed open.
# --------------------------------------------------------------------------- #


def _gate_over_the_wire(project_root: str, fdir: Path, gate: str) -> dict:
    """`Foundry-Gate` through `server.py`'s own dispatcher.

    The same door D-210 was driven at. The dispatcher is what a lead's call
    actually reaches, and it is where a checklist entry becomes the answer the
    run acts on. The ordering token is armed here because every gate consumes
    one and a refusal for the want of it would answer a different question.
    """
    from foundry_mcp import server as foundry_server

    _arm(fdir)
    previous_root = foundry_server._project_root
    try:
        foundry_server._project_root = project_root
        return foundry_server._DISPATCH["Foundry-Gate"]({"phase": gate})
    finally:
        foundry_server._project_root = previous_root


def _escalation_check(outcome: dict) -> dict:
    """The `escalated_classes_cleared` checklist entry, whichever counts it
    happens to carry in its label."""
    return next(
        c for c in outcome["checklist"]
        if c["check"].startswith("escalated_classes_cleared")
    )


def _fixed_class_with_status(fdir: Path, status) -> None:
    """A run whose class has every instance FIXED, and one persisted status.

    Everything open is closed, so `_escalated_classes` returns {} and the ONLY
    thing that can hold DONE is the persisted status — which is what makes this
    fixture measure the deciding read rather than the ledger recurrence beside
    it.
    """
    defects = _latent_recurring([0, 1, 2])
    for d in defects:
        d["status"] = "fixed"
        d["fixed_in_cycle"] = 2
    _ready_for_f6(fdir, defects)
    entry: dict = {"exit_reason": None}
    if status is not _ABSENT:
        entry["status"] = status
    (fdir / "escalation.json").write_text(
        json.dumps({"classes": {"FALSE_DOCUMENTED_CONTRACT": entry}}),
        encoding="utf-8",
    )


_ABSENT = object()   #: no `status` key at all — a pre-change archive's shape.


@pytest.mark.parametrize("status", [
    "ESCALATED",     # the vocabulary member that blocks
    _ABSENT,         # absent — always blocked, and still must
    None,            # null — always blocked, and still must
    "",              # present, empty, not a member
    "cleared",       # present, right word, wrong case: not a member
    "BOGUS",         # present, not a member at all
    "CLEARED ",      # present, trailing space: not a member
])
def test_a_status_outside_the_closed_vocabulary_blocks_done(run_env, status):
    """ST-010 verbatim: 'the generated report exists with every required
    section; no LIVE defect open; every escalated class CLEARED'.

    D-210, driven at `server.call_tool`'s `Foundry-Gate('done')` on a run whose
    class has every instance fixed. `_persisted_escalations` read
    `(entry.get("status") or "ESCALATED") == "ESCALATED"`, so ESCALATED, `null`
    and `""` each rendered `escalated_classes_cleared ok:false classes:[K]`
    while `"cleared"` and `"BOGUS"` each rendered `ok:true classes:[]` —
    BYTE-IDENTICAL to a genuine CLEARED — and the report showed the class in
    neither bucket with count 1. CLEARED is terminal and retires a class from
    structural work for the rest of the run, and no value nothing in this
    system writes may buy that.

    Parametrised over the whole shape of the field rather than over the one
    reported spelling: the property is "not a member of `ESCALATION_STATUSES`",
    and a fix keyed on `"BOGUS"` would pass a test that named only `"BOGUS"`.
    """
    project_root, fdir = run_env
    _fixed_class_with_status(fdir, status)

    check = _escalation_check(_gate_over_the_wire(project_root, fdir, "done"))
    assert check["ok"] is False, check
    assert check["classes"] == ["FALSE_DOCUMENTED_CONTRACT"], check
    assert check["persisted_escalated_classes"] == [
        "FALSE_DOCUMENTED_CONTRACT"
    ], check
    # The ledger-recurrence half sees nothing — every instance is fixed — so
    # the persisted read is unambiguously what answered.
    assert check["recurring_classes"] == [], check


def test_the_one_vocabulary_member_that_clears_still_clears(run_env):
    """The control D-210's fix must not move.

    Without this half the parametrised test above would pass on a read that had
    simply started blocking everything, which would deadlock every run that
    legitimately cleared a class — the D-034 ruling's explicit prohibition.
    """
    project_root, fdir = run_env
    _fixed_class_with_status(fdir, "CLEARED")

    check = _escalation_check(_gate_over_the_wire(project_root, fdir, "done"))
    assert check["ok"] is True, check
    assert check["classes"] == [], check
    assert check["persisted_escalated_classes"] == [], check


def test_the_status_resolver_is_the_one_path_both_deciding_reads_take(run_env):
    """THE PROPERTY, derived from the source rather than from the symptom.

    D-210's cause is that each deciding read carried its own opinion of one
    field: `_persisted_escalations` asked "is it ESCALATED" and
    `_escalated_classes` asked "is it CLEARED", and a value that was neither
    answered no to both. `_done_preconditions` then assembled its union from two
    different readings of the same byte string.

    Both now resolve through `_escalation_status`, and that is asserted from the
    source — a future edit that inlines a literal comparison back into either
    read re-opens the divergence whatever the parametrised drive above happens
    to cover. Mirrors `test_the_exit_arms_read_the_escalation_ledger_not_the_
    open_work`'s AST shape.
    """
    import ast
    import inspect
    import textwrap

    def _calls(fn) -> set[str]:
        tree = ast.parse(textwrap.dedent(inspect.getsource(fn)))
        return {
            node.func.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }

    for reader in (_escalation._persisted_escalations, _escalation._escalated_classes):
        assert "_escalation_status" in _calls(reader), (
            f"{reader.__name__} no longer resolves the persisted status through "
            "the closed vocabulary, so a value outside it can read as neither "
            "ESCALATED nor CLEARED again — D-210."
        )

    # And the resolver answers ONLY in the vocabulary, whatever it is handed.
    for handed in ("BOGUS", "cleared", "", None, 7, ["CLEARED"]):
        assert _escalation._escalation_status({"status": handed}) in ESCALATION_STATUSES
    assert _escalation._escalation_status({}) in ESCALATION_STATUSES
    assert _escalation._escalation_status(None) in ESCALATION_STATUSES
    assert _escalation._escalation_status("not a mapping") in ESCALATION_STATUSES


def test_the_two_status_names_are_exactly_the_closed_vocabulary(run_env):
    """The comparands are pinned to casting 1's frozenset (D-210, axis 1).

    `ESCALATION_STATUSES` is the vocabulary and membership in it is what
    decides; these two names are the only place this module says WHICH member
    each read asks about. Pinning the pair here means a member renamed or
    dropped in casting 1's `vocab.py` fails a test in this module, rather than
    quietly leaving a read matching nothing.
    """
    assert {
        _escalation.ESCALATION_STATUS_ESCALATED,
        _escalation.ESCALATION_STATUS_CLEARED,
    } == set(ESCALATION_STATUSES)


def test_the_report_and_the_gate_agree_about_an_unknown_status(run_env):
    """The other half of D-210's observable: what the run's own artifact says.

    The pre-fix drive's worst property was not that DONE passed — it was that
    DONE passed while `report.json` counted the class in NEITHER bucket with
    `count: 1`, so the two artifacts of one run disagreed and neither said the
    class was still escalated. The gate now blocks, and the report's CLEARED
    bucket does not claim it: nothing anywhere calls this class retired.

    `by_status` is `foundry_report.py`'s (casting 5's) roll-up and still counts
    an out-of-vocabulary status in neither bucket, so `sum(by_status.values())`
    is short of `count`. That is recorded as a concern for its owner; what is
    asserted here is the part this casting owns — the gate blocks, and no
    artifact reads the class as CLEARED.
    """
    from foundry_mcp.tools.foundry_report import generate_report

    project_root, fdir = run_env
    _fixed_class_with_status(fdir, "BOGUS")

    assert _escalation_check(_gate_over_the_wire(project_root, fdir, "done"))["ok"] is False

    assert generate_report(Path(project_root), fdir)["ok"] is True
    section = json.loads(
        (fdir / "report.json").read_text(encoding="utf-8")
    )["escalated_classes"]
    assert section["count"] == 1, section
    assert section["by_status"]["CLEARED"] == 0, section
    row = section["classes"][0]
    assert row["class"] == "FALSE_DOCUMENTED_CONTRACT", row
    assert row["status"] != "CLEARED", row
    assert row["exit_reason"] is None, row


# --------------------------------------------------------------------------- #
# D-212 — AND NOTHING PRE-FILTERS THE SHAPE AHEAD OF THE RESOLVER (ST-010)
#
# D-210 routed both deciding reads through `_escalation_status`, whose third
# rung reads "present and NOT a member, OR AN ENTRY THAT IS NOT A MAPPING AT
# ALL -> ESCALATED". `_persisted_escalations` never reached that rung: its
# comprehension tested `isinstance(entry, dict)` one line ABOVE the resolver,
# so a class whose entry is not a mapping was DROPPED from the ESCALATED list
# rather than resolved into it — and the identical `continue` in
# `foundry_report.py`'s reader dropped it from the report. The class
# disappeared from BOTH terminal artifacts of the run at once.
#
# Two axes, and the second is the one D-210 left open. WHAT decides: the
# vocabulary, by membership (D-210). WHERE the shape test lives: inside the
# resolver, so no caller can reach the field ahead of it (D-212).
# --------------------------------------------------------------------------- #


def _fixed_class_with_entry(fdir: Path, entry: object) -> None:
    """`_fixed_class_with_status`, one layer out: the whole ENTRY is the input.

    The shape under test is the entry itself rather than the `status` field
    inside it, so the fixture has to be able to write a value that has no
    fields at all. Everything else is `_fixed_class_with_status`'s run — every
    instance fixed, so `_escalated_classes` returns {} and the persisted read
    is unambiguously what answers.
    """
    defects = _latent_recurring([0, 1, 2])
    for d in defects:
        d["status"] = "fixed"
        d["fixed_in_cycle"] = 2
    _ready_for_f6(fdir, defects)
    (fdir / "escalation.json").write_text(
        json.dumps({"classes": {"FALSE_DOCUMENTED_CONTRACT": entry}}),
        encoding="utf-8",
    )


#: Entries that are not mappings at all. `_escalation_status`'s third rung has
#: always promised to resolve every one of them to ESCALATED.
_NON_MAPPING_ENTRIES = ["just a string", ["ESCALATED"], None, 7, True, []]


@pytest.mark.parametrize("entry", _NON_MAPPING_ENTRIES)
def test_an_entry_that_is_not_a_mapping_blocks_done(run_env, entry):
    """ST-010 verbatim: 'the generated report exists with every required
    section; no LIVE defect open; every escalated class CLEARED'.

    Driven at cdb9322 through `server.call_tool`'s `Foundry-Gate('done')` on a
    run whose class K has every instance fixed and verdicts complete: entry
    `{"status": "BOGUS"}` blocked naming K (correct, post-D-210), while entry
    `"just a string"`, entry `["ESCALATED"]` and entry `null` each rendered
    `escalated_classes_cleared` ABSENT from the failing checks, so the guard
    passed and DONE proceeded. An entry that is not a mapping carries no
    CLEARED, and ST-010's strict reading requires every class recorded in
    escalation.json to carry CLEARED before DONE.

    Parametrised over the SHAPE rather than over the one reported spelling: the
    property is "not a mapping", and a fix keyed on `str` would pass a test
    that named only a string.
    """
    project_root, fdir = run_env
    _fixed_class_with_entry(fdir, entry)

    check = _escalation_check(_gate_over_the_wire(project_root, fdir, "done"))
    assert check["ok"] is False, check
    assert check["classes"] == ["FALSE_DOCUMENTED_CONTRACT"], check
    assert check["persisted_escalated_classes"] == [
        "FALSE_DOCUMENTED_CONTRACT"
    ], check
    # The ledger-recurrence half sees nothing — every instance is fixed — so
    # the persisted read is unambiguously what answered.
    assert check["recurring_classes"] == [], check


@pytest.mark.parametrize("entry", _NON_MAPPING_ENTRIES)
def test_a_non_mapping_entry_is_treated_exactly_as_an_unknown_status(
    run_env, entry
):
    """The pin the fix is measured on: the same treatment, not merely SOME
    refusal.

    `{"status": "BOGUS"}` is the shape D-210 fixed and the reference this one
    is held to — a present value the vocabulary does not spell. An entry that
    is not a mapping is the same fact with one more layer removed, so the
    checklist entry the gate renders for it must be the entry it renders for
    BOGUS, field for field. Asserting merely `ok is False` would pass on a fix
    that blocked for some unrelated reason and named a different class.
    """
    project_root, fdir = run_env

    _fixed_class_with_entry(fdir, {"status": "BOGUS"})
    reference = _escalation_check(_gate_over_the_wire(project_root, fdir, "done"))

    _fixed_class_with_entry(fdir, entry)
    observed = _escalation_check(_gate_over_the_wire(project_root, fdir, "done"))

    assert observed == reference, (entry, observed, reference)


def test_the_shape_test_lives_inside_the_resolver_and_nowhere_above_it(run_env):
    """THE PROPERTY, derived from the source rather than from the symptom.

    D-212's cause is not that `_escalation_status` answered wrongly — it
    answers correctly for every shape and its docstring says so. The cause is
    that `_persisted_escalations` tested the shape BEFORE calling it, so the
    resolver's third rung was unreachable through that door however right it
    was. A future edit that reintroduces any shape test in the comprehension
    re-opens exactly that, whatever the parametrised drives above cover.

    Companion to `test_the_status_resolver_is_the_one_path_both_deciding_reads_
    take`, which pins that the resolver IS called; this pins that nothing is
    consulted ahead of it.
    """
    import ast
    import inspect
    import textwrap

    tree = ast.parse(textwrap.dedent(inspect.getsource(_escalation._persisted_escalations)))
    pre_filters = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "isinstance"
    ]
    assert pre_filters == [], (
        "_persisted_escalations tests an entry's shape itself again; the shape "
        "test belongs inside `_escalation_status`, whose third rung already "
        "resolves a non-mapping entry to ESCALATED — D-212."
    )

    # And the resolver still answers in the vocabulary for every one of them.
    for handed in _NON_MAPPING_ENTRIES:
        assert _escalation._escalation_status(handed) == _escalation.ESCALATION_STATUS_ESCALATED


@pytest.mark.parametrize("entry", _NON_MAPPING_ENTRIES)
def test_the_report_names_a_non_mapping_entry_as_escalated(run_env, entry):
    """AC-004 verbatim: 'escalation.json records for the class a status, the
    exit reason (clean-cycles or budget), the cycle it cleared and the
    structural packets it consumed.'

    The other terminal artifact. `foundry_report.py`'s reader carried the same
    `if not isinstance(entry, dict): continue`, so `generate_report` returned
    `escalated_classes` count 0 with NO row for the class — it vanished from
    the report exactly as it vanished from the gate. A class with no status is
    reported in the state it was written in, ESCALATED, never retired by
    omission.
    """
    from foundry_mcp.tools.foundry_report import generate_report

    project_root, fdir = run_env
    _fixed_class_with_entry(fdir, entry)

    assert generate_report(Path(project_root), fdir)["ok"] is True
    section = json.loads(
        (fdir / "report.json").read_text(encoding="utf-8")
    )["escalated_classes"]
    assert section["count"] == 1, section
    assert section["by_status"]["ESCALATED"] == 1, section
    assert section["by_status"]["CLEARED"] == 0, section
    row = section["classes"][0]
    assert row["class"] == "FALSE_DOCUMENTED_CONTRACT", row
    assert row["status"] == "ESCALATED", row
    assert row["exit_reason"] is None, row


def test_the_genuine_cleared_entry_still_clears(run_env):
    """The control D-212's fix must not move.

    Without it the drives above would pass on a read that had simply started
    blocking everything, which deadlocks every run that legitimately cleared a
    class — the D-034 ruling's explicit prohibition. The one shape that retires
    a class is a mapping whose status the vocabulary spells.
    """
    project_root, fdir = run_env
    _fixed_class_with_entry(fdir, {"status": "CLEARED", "exit_reason": "clean_cycles"})

    check = _escalation_check(_gate_over_the_wire(project_root, fdir, "done"))
    assert check["ok"] is True, check
    assert check["classes"] == [], check
    assert check["persisted_escalated_classes"] == [], check


# --------------------------------------------------------------------------- #
# D-212, the adjacent paths: the OTHER callers of `_persisted_escalations`.
#
# The defect was driven at `Foundry-Gate('done')`. Three other paths reach the
# same list — the `inspect_start` boundary arms (`_advance_escalation_exits`),
# the guidance notice (`_still_escalated_classes`), and `_escalated_classes`'s
# own `_class_info` read of the recorded entry. A class that starts appearing
# in that list appears in all four.
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("entry", _NON_MAPPING_ENTRIES)
def test_the_boundary_arms_normalise_a_non_mapping_entry_instead_of_raising(
    run_env, entry
):
    """ADJACENT PATH — `Foundry-Phase('inspect_start')`, not the DONE gate.

    `_advance_escalation_exits` walks the roster `_persisted_escalations`
    returns and calls `_escalation_entry_defaults` on each entry, which is
    `setdefault` on a mapping. A class that is now IN that roster and is not a
    mapping would raise `AttributeError` several frames below the entry point —
    the D-127 shape the house rule exists to prevent, in the very transition
    that owns the run's cycle counter.

    Normalised exactly as `_record_escalation_proposals` and
    `_spend_structural_budget` already normalise the same document, so the
    crossing succeeds, the entry becomes a C-3 record, and the class keeps
    blocking rather than either crashing the boundary or vanishing again.
    """
    project_root, fdir = run_env
    _escalate(fdir, project_root)
    doc = json.loads((fdir / _escalation.ESCALATION_FILENAME).read_text(encoding="utf-8"))
    doc["classes"]["FALSE_DOCUMENTED_CONTRACT"] = entry
    (fdir / _escalation.ESCALATION_FILENAME).write_text(json.dumps(doc), encoding="utf-8")

    crossing = _cross_boundary(fdir, project_root)

    assert crossing.get("ok") is True, crossing
    recorded = _escalation_entry(fdir)
    assert isinstance(recorded, dict), recorded
    assert recorded["status"] == _escalation.ESCALATION_STATUS_ESCALATED, recorded
    assert recorded["live_clean_cycles"] == 0, recorded
    assert recorded["structural_packets_dispatched"] == 0, recorded
    # ADJACENT PATH 2 — the guidance notice reads the same union the gate
    # refuses on, so the lead is told about the class the gate will stop on.
    assert _guidance._still_escalated_classes(fdir, project_root) == [
        "FALSE_DOCUMENTED_CONTRACT"
    ]


def test_a_normalised_entry_still_reaches_a_bounded_exit(run_env):
    """D-034's prohibition: the block this fix creates must be BOUNDED.

    A class that blocks DONE forever is not an improvement on one that vanished
    from the artifacts. `escalated_at_cycle` is not among
    `_escalation_entry_defaults`' keys, so a normalised entry cannot advance
    the clean arm until `Foundry-Tasks` re-latches it from the ledger — which
    every GRIND cycle calls, and which `foundry_gate`'s grind branch refuses
    without. Two crossings after that, the class CLEARS on `clean_cycles` like
    any other quiet class.
    """
    project_root, fdir = run_env
    _escalate(fdir, project_root)
    doc = json.loads((fdir / _escalation.ESCALATION_FILENAME).read_text(encoding="utf-8"))
    doc["classes"]["FALSE_DOCUMENTED_CONTRACT"] = "just a string"
    (fdir / _escalation.ESCALATION_FILENAME).write_text(json.dumps(doc), encoding="utf-8")

    _cross_boundary(fdir, project_root)
    _fix_every_instance(fdir, _current_cycle(fdir))
    foundry_defects_to_tasks(project_root)
    assert isinstance(_escalation_entry(fdir)["escalated_at_cycle"], int)

    for _ in range(4):
        _cross_boundary(fdir, project_root)
        if _escalation_entry(fdir)["status"] == _escalation.ESCALATION_STATUS_CLEARED:
            break

    entry = _escalation_entry(fdir)
    assert entry["status"] == _escalation.ESCALATION_STATUS_CLEARED, entry
    assert entry["exit_reason"] == "clean_cycles", entry


@pytest.mark.parametrize("entry", _NON_MAPPING_ENTRIES)
def test_the_recurrence_read_survives_a_non_mapping_entry(run_env, entry):
    """ADJACENT PATH — `_escalated_classes`, the OTHER half of the union.

    That read resolves the status correctly (D-210) and then hands the whole
    `recorded` mapping to `_class_info`, which read
    `(recorded.get(key) or {}).get("proposal", "")`. `or {}` covers `None` and
    covers nothing else: a string or a list falls through it and raises
    `AttributeError` on `.get`. Unreachable while the class had no open work —
    the D-212 drive fixed every instance — and reached the moment the same
    class has open recurring instances, which is escalation's ordinary state.
    """
    project_root, fdir = run_env
    _write_defects(fdir, _recurring([1, 2, 3]))
    _write_state(fdir, phase="F2", cycle=3)
    (fdir / _escalation.ESCALATION_FILENAME).write_text(
        json.dumps({"classes": {"FALSE_DOCUMENTED_CONTRACT": entry}}),
        encoding="utf-8",
    )

    escalated = _escalated_classes(fdir, project_root)

    assert list(escalated) == ["FALSE_DOCUMENTED_CONTRACT"], escalated
    assert escalated["FALSE_DOCUMENTED_CONTRACT"]["proposal"] == ""


@pytest.mark.parametrize("classes", ["not a mapping", ["FDC"], 7, None])
def test_the_recurrence_read_survives_a_classes_container_that_is_not_a_mapping(
    run_env, classes
):
    """The container, one level up from the entry.

    `_done_preconditions`, `_still_escalated_classes` and
    `_advance_escalation_exits` each guard `isinstance(..., dict)` on
    `classes`; `_escalated_classes` — a deciding read — did not, so a document
    whose `classes` is a list reached `recorded.get` and raised across the MCP
    boundary instead of returning the house refusal shape.
    """
    project_root, fdir = run_env
    _write_defects(fdir, _recurring([1, 2, 3]))
    _write_state(fdir, phase="F2", cycle=3)
    (fdir / _escalation.ESCALATION_FILENAME).write_text(
        json.dumps({"classes": classes}), encoding="utf-8"
    )

    assert list(_escalated_classes(fdir, project_root)) == [
        "FALSE_DOCUMENTED_CONTRACT"
    ]


def test_a_second_inspect_start_in_one_cycle_cannot_count_a_clean_cycle_twice(run_env):
    """D-057: the clean arm counted inspect_start CALLS, not server cycles.

    `foundry_mark_phase_complete` increments `state['cycle']` only when the
    previous phase was F3, but called the clean arm unconditionally, and the arm
    did `live_clean_cycles += 1` with no record of which cycles it had counted.
    Driven through real doors: escalate at cycle 3, one honest crossing, then two
    further Foundry-Phase(inspect_start) calls — phase already F2, so the counter
    does not move — and `live_clean_cycles` reached 2, CLEARING the class after
    ONE real cycle had ended.

    Both guards are asserted here, because either alone leaves the count
    steerable: the F2->F2 call is now REFUSED on a FULL cycle, and
    `live_clean_cycles_counted` records the closed cycles already evaluated so a
    call that did land could not count one twice.
    """
    project_root, fdir = run_env
    _escalate(fdir, project_root)
    _cross_boundary(fdir, project_root)  # closes cycle 3; nothing counted yet

    entry = _escalation_entry(fdir)
    assert entry["live_clean_cycles"] == 0
    assert entry["live_clean_cycles_counted"] == []

    _cross_boundary(fdir, project_root)  # closes cycle 4 — one clean
    entry = _escalation_entry(fdir)
    assert entry["live_clean_cycles"] == 1
    assert entry["live_clean_cycles_counted"] == [4]

    # Now the call D-057 drove: inspect_start again, WITHOUT returning to F3.
    for _ in range(2):
        _arm(fdir)
        again = foundry_mark_phase_complete("inspect_start", project_root)
        assert "error" in again, again
        assert "nothing to widen" in again["error"]

    entry = _escalation_entry(fdir)
    assert entry["live_clean_cycles"] == 1, "one real cycle has ended, not two"
    assert entry["status"] == "ESCALATED"
    assert _current_cycle(fdir) == 5, "a refused transition moves no counter"


def test_the_clean_arm_records_every_closed_cycle_it_evaluated(run_env):
    """D-057's guard, stated as the property rather than the symptom.

    A cycle is EVALUATED AT MOST ONCE, whichever way it goes — a cycle that drew
    a LIVE instance is recorded as counted too, because re-evaluating it on a
    later call would be the same defect with the sign flipped. This is the
    budget arm's `structural_packet_cycles` guard one field over.
    """
    project_root, fdir = run_env
    _escalate(fdir, project_root)
    _cross_boundary(fdir, project_root)  # closes 3 (the escalation cycle)
    _cross_boundary(fdir, project_root)  # closes 4 — clean

    defects = json.loads((fdir / "defects.json").read_text(encoding="utf-8"))["defects"]
    defects.append(_defect("D-050", 5, **{
        "class": "FALSE_DOCUMENTED_CONTRACT", "tier": "LIVE",
    }))
    _write_defects(fdir, defects)
    _cross_boundary(fdir, project_root)  # closes 5 — a LIVE instance, reset

    entry = _escalation_entry(fdir)
    assert entry["live_clean_cycles"] == 0
    # D-057's guarantee, restated under D-112: the resetting cycle IS recorded as
    # evaluated, so a second call cannot re-evaluate it. What changed is that the
    # list is now the CURRENT STREAK rather than an audit log — a LIVE draw ends
    # the streak, so the cycles before the break leave it. That keeps
    # `live_clean_cycles == len(live_clean_cycles_counted)` true, which is what
    # makes the count checkable against its own evidence.
    assert entry["live_clean_cycles_counted"] == [5], (
        "the resetting cycle is recorded as evaluated too, or a second call "
        "could re-evaluate it"
    )


def test_a_reproduced_instance_of_an_escalated_class_still_blocks_done(run_env):
    """The other side, unchanged: process-fixes AC-011's 'escalation NEVER
    waives closure'.

    One LIVE instance among the LATENT ones and the class blocks again, named,
    with the blocking instance named in the hint — the tier is what decides, not
    the escalation.
    """
    project_root, fdir = run_env
    defects = _latent_recurring([0, 1, 2])
    defects[1]["tier"] = "LIVE"
    defects[1]["reproduction_attempted"] = None
    _ready_for_f6(fdir, defects)

    outcome = _gates._done_preconditions(fdir, project_root)

    assert outcome["passed"] is False
    assert "1 defect class(es) are still ESCALATED" in outcome["reason"]
    assert "FALSE_DOCUMENTED_CONTRACT" in outcome["reason"]
    assert "D-002" in outcome["hint"]
    checks = {c["check"]: c for c in outcome["checklist"]}
    escalation_check = next(
        k for k in checks if k.startswith("escalated_classes_cleared")
    )
    assert checks[escalation_check]["classes"] == ["FALSE_DOCUMENTED_CONTRACT"]


def test_an_untiered_instance_of_an_escalated_class_blocks_like_a_live_one(run_env):
    """FR-051 verbatim: 'Blocks like LIVE until a stream re-files it with a
    tier.' The escalated branch must read the tier the same way every other
    branch does, or an archive written before tiers existed passes here and is
    refused one branch above."""
    project_root, fdir = run_env
    _ready_for_f6(fdir, _recurring([0, 1, 2]))   # `_recurring` writes no tier

    outcome = _gates._done_preconditions(fdir, project_root)

    assert outcome["passed"] is False
    assert "1 defect class(es) are still ESCALATED" in outcome["reason"]


def test_the_f6_doors_and_the_nyquist_gate_agree_about_a_latent_only_class(run_env):
    """THE ADJACENT PATH, and the reason this is a defect rather than a
    preference: `foundry_gate("nyquist")` and `foundry_gate("temper")` read
    `_blocking_defects` and never `_escalated_classes`, so before this fix the
    same run state passed NYQUIST and was refused at DONE. Four doors, one
    answer — asserted across all four rather than at the one that was wrong.
    """
    def _defect_verdict(gate: str) -> str:
        """What this door says about THE DEFECTS, ignoring paperwork it also
        checks (a report, a parseable spec) that has nothing to do with tier."""
        _arm(fdir)
        result = foundry_gate(gate, project_root)
        return result.get("reason", "") + " " + result.get("hint", "")

    doors = ("temper", "nyquist", "done", "nyquist_done")

    project_root, fdir = run_env
    _ready_for_f6(fdir, _latent_recurring([0, 1, 2]))

    for gate in doors:
        said = _defect_verdict(gate)
        assert "escalated defect class" not in said, (gate, said)
        assert "D-001" not in said, (gate, said)

    # And they agree in the other direction too: one reproduced instance and
    # every door names it.
    defects = _latent_recurring([0, 1, 2])
    defects[0]["tier"] = "LIVE"
    defects[0]["reproduction_attempted"] = None
    _write_defects(fdir, defects)
    for gate in doors:
        said = _defect_verdict(gate)
        assert "D-001" in said, (gate, said)


def test_both_doors_into_f6_enforce_the_same_preconditions(run_env):
    """The property, rather than a re-listing of the checks: for one run state,
    ``done`` and ``nyquist_done`` agree. Two terminal transitions with two
    definitions of "finished" is the drift that produced D-043 — one branch was
    fixed and its sibling was not, and the sibling was the live one."""
    project_root, fdir = run_env
    _write_state(fdir, phase="F5.5", cycle=2, nyquist=True)
    _write_defects(fdir, _recurring([0, 1, 2]))
    (fdir / "verdicts.json").write_text(
        json.dumps({"requirements": []}), encoding="utf-8"
    )

    _arm(fdir)
    done = foundry_mark_phase_complete("done", project_root)
    _arm(fdir)
    nyquist_done = foundry_mark_phase_complete("nyquist_done", project_root)

    assert done.get("ok") is not True
    assert nyquist_done.get("ok") is not True
    # Same evaluation, so the same checklist and the same underlying reason —
    # only the prefix naming which door was tried differs.
    #
    # fallout CT-013 / GI-011 / GI-031 — AND THE TOKEN IS NOW ON THE ROW.
    # One preconditions function per TRANSITION token, so the entry rung labels
    # itself `entered_from_accepted_phase (token=done)` /`(token=nyquist_done)`.
    # That IS "the prefix naming which door was tried", moved from the message
    # onto the rung that computed it. So the token is normalised out and the
    # rows compared — and the difference is asserted to be EXACTLY that, rather
    # than merely tolerated, because a comparison that ignores a field cannot
    # tell "only the token differs" from "the token and something else do".
    def _without_token(checklist: list[dict], token: str) -> list[dict]:
        return [
            {**row, "check": row["check"].replace(f"(token={token})", "(token=…)")}
            for row in checklist
        ]

    assert _without_token(nyquist_done["checklist"], "nyquist_done") == _without_token(
        done["checklist"], "done"
    )
    # The normalisation actually fired, or the comparison above proves nothing.
    assert any("(token=…)" in row["check"] for row in
               _without_token(done["checklist"], "done")), done["checklist"]
    assert nyquist_done["checklist"] != done["checklist"], (
        "the two doors' rungs are byte-identical, so nothing names which door "
        "was tried — the normalisation above is guarding a difference that is "
        "no longer there"
    )
    shared_reason = (
        "1 defect class(es) are still ESCALATED: FALSE_DOCUMENTED_CONTRACT"
    )
    assert shared_reason in done["error"]
    assert shared_reason in nyquist_done["error"]


def test_the_nyquist_done_gate_is_a_real_branch_agreeing_with_its_transition(run_env):
    """D-043's second half: ``foundry_gate`` had no ``nyquist_done`` case, so a
    lead about to call that token could only gate a DIFFERENT one. The gate now
    answers for the token being called, and answers the same as the call."""
    project_root, fdir = run_env
    _write_state(fdir, phase="F5.5", cycle=2, nyquist=True)
    _write_defects(fdir, _recurring([0, 1, 2]))
    (fdir / "verdicts.json").write_text(
        json.dumps({"requirements": []}), encoding="utf-8"
    )

    _arm(fdir)
    gate = foundry_gate("nyquist_done", project_root)
    _arm(fdir)
    transition = foundry_mark_phase_complete("nyquist_done", project_root)

    assert "Unknown phase" not in gate.get("reason", "")
    assert gate["passed"] is False
    assert gate["reason"] in transition["error"]
    assert transition["checklist"] == gate["checklist"]


def test_the_advertised_gate_phases_all_resolve_to_a_real_branch(run_env):
    """The recurrence guard for this defect class: an advertised token with no
    branch behind it. It was the original ``nyquist`` bug and it was still true
    of ``nyquist_done`` at the gate. Every value the Foundry-Gate schema offers
    a lead must reach a branch, not the Unknown-phase fallback."""
    import asyncio

    from foundry_mcp import server as foundry_server

    project_root, fdir = run_env
    _write_state(fdir, phase="F0", cycle=0)

    tools = asyncio.run(foundry_server.list_tools())
    gate_tool = next(t for t in tools if t.name == "Foundry-Gate")

    for phase in gate_tool.inputSchema["properties"]["phase"]["enum"]:
        _arm(fdir)
        result = foundry_gate(phase, project_root)
        assert "Unknown phase" not in result.get("reason", ""), phase


def test_the_nyquist_done_transition_advances_once_the_gate_would_pass(run_env):
    """Closure is the exit, not a waiver: the guard is a precondition, not a
    phase the lead cannot leave. Every defect of the class closed and every
    requirement VERIFIED, so the F5.5 exit archives the run as it always did."""
    project_root, fdir = run_env
    _write_state(fdir, phase="F5.5", cycle=3, nyquist=True)

    defects = _recurring([0, 1, 2])
    for d in defects:
        d["status"] = "fixed"
        d["fixed_in_cycle"] = 3
    _write_defects(fdir, defects)
    (fdir / "spec.md").write_text("- FR-001: the thing works\n", encoding="utf-8")
    (fdir / "verdicts.json").write_text(
        json.dumps({"requirements": [
            {"requirement_id": "FR-001", "verdict": "VERIFIED"},
        ]}),
        encoding="utf-8",
    )

    _generate_report(project_root, fdir)

    _arm(fdir)
    gate = foundry_gate("nyquist_done", project_root)
    assert gate["passed"] is True, gate

    _arm(fdir)
    result = foundry_mark_phase_complete("nyquist_done", project_root)

    assert result["ok"] is True, result
    assert result["phase"] == "F6"
    assert json.loads((fdir / "state.json").read_text(encoding="utf-8"))["phase"] == "F6"


def test_an_override_does_not_waive_closure_either(run_env):
    """De-escalating a class restores per-instance packets. It does not close
    anything: the DONE gate still refuses on the open defects themselves."""
    project_root, fdir = run_env
    _write_state(fdir, phase="F4", cycle=2)
    _write_defects(fdir, _recurring([0, 1, 2]))
    (fdir / "verdicts.json").write_text(
        json.dumps({"requirements": []}), encoding="utf-8"
    )
    foundry_inject_directive("escalation-override", project_root=project_root)
    _arm(fdir)

    result = foundry_gate("done", project_root)

    assert result["passed"] is False
    # CT-008: the refusal names the blocking defects rather than counting them,
    # and it distinguishes LIVE from unknown-tier. These three carry no tier —
    # they are `_defect` fixtures, which is what a pre-change record looks like
    # — so they block like LIVE and are reported as untiered (FR-051).
    assert "D-001" in result["reason"] and "D-003" in result["reason"], result
    assert "no tier" in result["reason"], result


# --------------------------------------------------------------------------- #
# The lead is told before it dispatches the wave
# --------------------------------------------------------------------------- #


def test_next_action_surfaces_the_escalation_at_f2(run_env):
    """The lead has to know a class escalated BEFORE it dispatches GRIND,
    because the packet shape it is about to hand out changed."""
    project_root, fdir = run_env
    _write_state(fdir, phase="F2", cycle=2)
    _record_inspect_mode(fdir, cycle=2)
    _write_defects(fdir, _recurring([0, 1, 2]))
    for s in ("trace", "prove", "test"):
        (fdir / f".{s}-complete").write_text("items_checked=1\nfindings=0\n", encoding="utf-8")

    action = _guidance._compute_next_action(project_root)

    assert action["action"] == "transition_to_grind"
    assert "ESCALATED" in action["instructions"]
    assert "FALSE_DOCUMENTED_CONTRACT" in action["instructions"]
    assert "FALSE_DOCUMENTED_CONTRACT" in action["details"]["escalation"]


def test_next_action_reads_normally_when_nothing_is_escalated(run_env):
    """The notice is empty on an ordinary cycle — no noise added to the
    common path."""
    project_root, fdir = run_env
    _write_state(fdir, phase="F2", cycle=1)
    _record_inspect_mode(fdir, cycle=1)
    _write_defects(fdir, [_defect("D-001", 1)])
    for s in ("trace", "prove", "test"):
        (fdir / f".{s}-complete").write_text("items_checked=1\nfindings=0\n", encoding="utf-8")

    action = _guidance._compute_next_action(project_root)

    assert "ESCALATED" not in action["instructions"]
    assert action["details"]["escalation"] == {}


def test_escalation_threshold_constant_is_three(run_env):
    """process-fixes FR-006 / A-012 fixes N at 3. Pinned so a later edit to
    the constant has to be a deliberate spec change, not a silent retune."""
    assert ESCALATION_CYCLES == 3


# --------------------------------------------------------------------------- #
# D-101 — the escalation override is a marker grammar, not a substring
#
# TV-B-03: `if ESCALATION_OVERRIDE_TOKEN not in text.lower()` followed by
# `return scoped or {"*"}` meant ANY mention of the token de-escalated EVERY
# class. A directive that FORBADE the override therefore disabled escalation
# wholesale — semantics exactly inverted from operator intent, with no signal.
# process-fixes ST-003 makes the override an EXPLICIT directive action, and a
# substring match is not explicit.
# --------------------------------------------------------------------------- #


# Every one of these MENTIONS the token and none of them ASKS for an override.
# The first is the verbatim repro from the defect report.
_OVERRIDE_NON_REQUESTS = [
    "Never apply an escalation-override. I want real structural fixes.",
    "the escalation-overrides list is empty",
    "DO NOT USE ESCALATION-OVERRIDE ON THIS RUN.",
    "Ask me before you file any escalation-override for a security class.",
    "Document why escalation-override exists in the protocol prose.",
    "escalation-override is banned for the rest of this run",
    "I removed the escalation-override: AUTH line from the earlier directive.",
]

# Each of these IS the marker grammar, on its own line.
_OVERRIDE_REQUESTS_ALL = [
    "escalation-override",
    "escalation-override: *",
    "escalation-override: all",
    "- escalation-override",
    "Reasoning above.\nescalation-override: *",
]


@pytest.mark.parametrize("directive", _OVERRIDE_NON_REQUESTS)
def test_mentioning_the_override_token_in_prose_does_not_override(run_env, directive):
    """D-101: a mention is not a request. Forbidding phrasings especially."""
    project_root, fdir = run_env
    foundry_inject_directive(directive, "normal", project_root)

    assert _escalation._escalation_overrides(project_root) == set()


@pytest.mark.parametrize("directive", _OVERRIDE_REQUESTS_ALL)
def test_the_bare_marker_grammar_overrides_every_class(run_env, directive):
    project_root, fdir = run_env
    foundry_inject_directive(directive, "normal", project_root)

    assert _escalation._escalation_overrides(project_root) == {"*"}


def test_the_scoped_marker_grammar_overrides_exactly_that_class(run_env):
    project_root, fdir = run_env
    foundry_inject_directive("escalation-override: FALSE_DOCUMENTED_CONTRACT", "normal", project_root)

    assert _escalation._escalation_overrides(project_root) == {"FALSE_DOCUMENTED_CONTRACT"}


def test_a_directive_forbidding_the_override_leaves_escalation_armed(run_env):
    """The full repro, end to end: the class stays escalated.

    Verified in the defect report against a class open across cycles 1/2/3 —
    `_escalation_overrides() == {"*"}` collapsed `_escalated_classes()` to {}.
    """
    project_root, fdir = run_env
    _write_defects(fdir, _recurring([1, 2, 3]))
    _write_state(fdir, phase="F3", cycle=3)

    assert set(_escalated_classes(fdir, project_root)) == {"FALSE_DOCUMENTED_CONTRACT"}

    foundry_inject_directive(
        "Never apply an escalation-override. I want real structural fixes.",
        "normal",
        project_root,
    )

    assert set(_escalated_classes(fdir, project_root)) == {"FALSE_DOCUMENTED_CONTRACT"}


def test_the_marker_grammar_still_de_escalates_the_named_class(run_env):
    """The override must keep WORKING — D-101 narrows the trigger, it does not
    remove the capability process-fixes AC-010 requires."""
    project_root, fdir = run_env
    _write_defects(fdir, _recurring([1, 2, 3]))
    _write_state(fdir, phase="F3", cycle=3)

    foundry_inject_directive(
        "escalation-override: FALSE_DOCUMENTED_CONTRACT", "normal", project_root
    )

    assert _escalated_classes(fdir, project_root) == {}


# --------------------------------------------------------------------------- #
# D-102 — a mixed declared/undeclared cluster still accumulates
#
# TV-B-04: `class` is OPTIONAL, so one stream omitting it split a real cluster
# into SHARED{1,3} and MISSING@src{2}. Neither reached three consecutive
# cycles, so a class that genuinely recurred three straight cycles escaped
# escalation in silence — process-fixes ST-002 says EITHER path accumulates
# the count, and a mixed cluster accumulated in neither.
# --------------------------------------------------------------------------- #


def _mixed_cluster(klass: str = "SHARED", file_path: str = "src/a.py") -> list[dict]:
    """The defect report's repro: same file, same type, cycles 1/2/3, and only
    TWO of the three carry the declared class."""
    return [
        _defect("D-001", 1, type="MISSING", file=file_path, **{"class": klass}),
        _defect("D-002", 2, type="MISSING", file=file_path),  # no class declared
        _defect("D-003", 3, type="MISSING", file=file_path, **{"class": klass}),
    ]


def test_a_mixed_declared_cluster_escalates_on_the_third_cycle(run_env):
    """D-102, the exact repro. Before the fix this returned {}."""
    project_root, fdir = run_env
    _write_defects(fdir, _mixed_cluster())
    _write_state(fdir, phase="F3", cycle=3)

    escalated = _escalated_classes(fdir, project_root)

    assert set(escalated) == {"SHARED"}, escalated
    assert escalated["SHARED"]["consecutive_cycles"] == 3
    assert escalated["SHARED"]["cycles"] == [1, 2, 3]
    # The undeclared record is CARRIED, not merely counted: closure still binds
    # it (process-fixes AC-011), so it must appear on the structural packet.
    assert set(escalated["SHARED"]["defect_ids"]) == {"D-001", "D-002", "D-003"}


def test_the_undeclared_record_is_absorbed_not_duplicated(run_env):
    """The buckets stay a PARTITION. Counting the undeclared record in both its
    fallback cluster AND the declared class would double-escalate: two
    structural packets covering an overlapping defect set."""
    project_root, fdir = run_env
    _write_defects(fdir, _mixed_cluster())
    _write_state(fdir, phase="F3", cycle=3)

    escalated = _escalated_classes(fdir, project_root)

    assert len(escalated) == 1
    all_ids = [did for info in escalated.values() for did in info["defect_ids"]]
    assert len(all_ids) == len(set(all_ids))


def test_two_declared_classes_over_one_cluster_do_not_absorb(run_env):
    """The ambiguity guard. When rival declared classes own the same fallback
    cluster there is no non-arbitrary owner, so the undeclared record stays in
    its own bucket rather than being assigned to whichever sorted first."""
    project_root, fdir = run_env
    _write_defects(fdir, [
        _defect("D-001", 1, type="MISSING", file="src/a.py", **{"class": "ALPHA"}),
        _defect("D-002", 2, type="MISSING", file="src/a.py", **{"class": "BETA"}),
        _defect("D-003", 3, type="MISSING", file="src/a.py"),
    ])
    _write_state(fdir, phase="F3", cycle=3)

    resolved = _escalation._resolve_defect_classes(
        json.loads((fdir / "defects.json").read_text())["defects"]
    )
    assert sorted(resolved.values()) == ["ALPHA", "BETA", "MISSING@src"]
    assert _escalated_classes(fdir, project_root) == {}


def test_a_declared_class_is_never_merged_into_another_declared_class(run_env):
    """Rule 1: a stream that named a class meant it. Two declared classes on
    one file stay two classes even though their fallback cluster is shared."""
    project_root, fdir = run_env
    _write_defects(fdir, [
        _defect(f"D-{i:03d}", c, type="MISSING", file="src/a.py", **{"class": k})
        for i, (c, k) in enumerate(
            [(1, "ALPHA"), (2, "ALPHA"), (3, "ALPHA"), (1, "BETA"), (2, "BETA"), (3, "BETA")],
            start=1,
        )
    ])
    _write_state(fdir, phase="F3", cycle=3)

    assert set(_escalated_classes(fdir, project_root)) == {"ALPHA", "BETA"}


def test_a_wholly_undeclared_cluster_still_uses_the_fallback(run_env):
    """process-fixes FR-024 / ST-002: the fallback path is untouched by the
    absorption rule."""
    project_root, fdir = run_env
    _write_defects(fdir, [
        _defect("D-001", 1, type="MISSING", file="src/a.py"),
        _defect("D-002", 2, type="MISSING", file="src/b.py"),
        _defect("D-003", 3, type="MISSING", file="src/c.py"),
    ])
    _write_state(fdir, phase="F3", cycle=3)

    assert set(_escalated_classes(fdir, project_root)) == {"MISSING@src"}


def test_two_cycles_of_a_mixed_cluster_still_do_not_fire(run_env):
    """D-102 widens what COUNTS as one class; it must not weaken N=3."""
    project_root, fdir = run_env
    _write_defects(fdir, _mixed_cluster()[:2])
    _write_state(fdir, phase="F3", cycle=2)

    assert _escalated_classes(fdir, project_root) == {}


# --------------------------------------------------------------------------- #
# D-119 — the two filing doors agree on WHICH cycle a record belongs to
#
# TV-E-01: two independent derivations of one rule diverged on a malformed
# counter. `foundry.py`'s own reader returned None, and the caller-side wrapper
# around it — named _stamp_cycle at the time, deleted in 6453159 which folded
# it back into that reader — took that as licence to fall back to the CALLER's
# cycle; while the orchestrator's second reader returned 0 on the same input.
# That wrapper's docstring claimed "Every writer goes through this, so 'which
# cycle was this?' has one answer per run" — it had two.
#
# Neither of those two symbols survives, and this comment names neither as a
# live reader: the `foundry.py` one was `foundry.py#_server_cycle`, deleted by
# casting 4's f2bbce0 as a byte-equivalent second copy — cited in the
# path#Symbol form the `_OWNER` block below uses for the same symbol, because a
# deleted symbol still has to say which file it was deleted FROM — and the
# orchestrator's was `_current_cycle`, consolidated by casting 10 (GI-024).
# Both are now
# plugins/foundry/mcp-server/src/foundry_mcp/tools/foundry_state.py#current_cycle,
# which is the live symbol, the only derivation left, and where any fix to
# either half goes. One reader is what makes the divergence below
# unrepeatable rather than merely fixed — and it is why the cites here name
# the leaf and the history names the deleted symbols as history.
#
# The harm is not cosmetic. Identical run, identical findings, identical class,
# caller cycles 1/2/3, only the DOOR differs: Foundry-Defect stamped [1,2,3]
# and escalated and DONE refused, while Foundry-Sync stamped [0] and neither
# fired. Mixed filing persisted [1,0,3] — longest consecutive run 2 — so a
# genuine systemic class evaded escalation and the process-fixes AC-011 guard
# never fired.
#
# LEAD INTERFACE RULING (cross-casting, casting 3 owns the foundry.py half):
# on a malformed/unusable counter BOTH doors resolve the stamped cycle to 0,
# matching _current_cycle's documented "every reader gets a usable integer"
# and process-fixes ST-001's "the caller's cycle is never trusted for
# stamping"; AND both
# writers persist the caller-supplied value as `declared_cycle` for
# auditability, so the divergence is visible rather than silent.
#
# 5964be0 made the doors agree on WHAT a defect is. This makes them agree on
# WHEN.
# --------------------------------------------------------------------------- #


# no-key / null / '3' / -3 / 2.5 / true — the defect report's matrix verbatim.
_MALFORMED_COUNTERS = [
    pytest.param({}, id="no-key"),
    pytest.param({"cycle": None}, id="null"),
    pytest.param({"cycle": "3"}, id="str"),
    pytest.param({"cycle": -3}, id="negative"),
    pytest.param({"cycle": 2.5}, id="float"),
    pytest.param({"cycle": True}, id="bool"),
]

CALLER_CYCLE = 7


@pytest.mark.parametrize("state", _MALFORMED_COUNTERS)
def test_current_cycle_reads_every_malformed_counter_as_zero(run_env, state):
    """My half of the ruling: _current_cycle already conforms — pinned so it
    keeps conforming, over the whole matrix rather than one example."""
    _project_root, fdir = run_env
    (fdir / "state.json").write_text(json.dumps({"phase": "F3", **state}), encoding="utf-8")

    assert _current_cycle(fdir) == 0


def test_current_cycle_reads_a_malformed_container_as_zero(run_env):
    """The rung D-098 added underneath: the CONTAINER, not just the value.
    `[1,2,3]` used to raise AttributeError out of every caller."""
    _project_root, fdir = run_env
    (fdir / "state.json").write_text("[1, 2, 3]", encoding="utf-8")

    assert _current_cycle(fdir) == 0


def _stamped_via_sync(project_root: str, cycle: int) -> dict:
    """File one finding through the BATCH door; return the persisted record."""
    _sync_defects(
        cycle=cycle,
        findings=[{
            "source": "trace",
            "type": "UNWIRED",
            "description": "the session refresh never calls the token store",
            "symbol": "refresh_session",
            "file": "src/auth/session.py",
        }],
        project_root=project_root,
    )
    fdir = Path(project_root) / "foundry-archive" / "escalation-run"
    return json.loads((fdir / "defects.json").read_text())["defects"][-1]


def _stamped_via_add(project_root: str, cycle: int) -> dict:
    """File the same finding through the SINGLE door; return the record.

    Importing casting 3's door to drive the parity comparison is READING it,
    not editing it — the foundry.py half of this contract is theirs.
    """
    _add_defect(
        cycle=cycle,
        source="trace",
        defect_type="UNWIRED",
        description="the session refresh never calls the token store",
        symbol="refresh_session",
        file_path="src/auth/session.py",
        project_root=project_root,
    )
    fdir = Path(project_root) / "foundry-archive" / "escalation-run"
    return json.loads((fdir / "defects.json").read_text())["defects"][-1]


# The parity assertions below are a JOINT pin: the batch door and the single
# door in `plugins/foundry/mcp-server/src/foundry_mcp/tools/foundry.py`, which
# stamp through `foundry_state.py#current_cycle`. Both halves have landed, so
# red here is a REGRESSION in one of them, and the messages name which — never
# misread these as a regression in the orchestrator.
#
# The cite named `foundry.py#_server_cycle` until casting 4's f2bbce0 deleted
# that symbol as a byte-equivalent second copy of the leaf reader (C-008 /
# C-015, GI-024) and pointed its six call sites at `current_cycle`. The
# symbol is authoritative, so the cite moves with it: a message naming a
# deleted symbol sends a reader looking for a file that answers nothing, which
# is the whole of what a stale cite costs.
_OWNER = (
    "the filing doors in "
    "plugins/foundry/mcp-server/src/foundry_mcp/tools/foundry.py stamp through "
    "plugins/foundry/mcp-server/src/foundry_mcp/tools/foundry_state.py#current_cycle: "
    "on a malformed counter it must resolve to 0 (not fall back to the "
    "caller's value) and the door must persist declared_cycle on the record"
)


@pytest.mark.parametrize("state", _MALFORMED_COUNTERS)
def test_both_filing_doors_stamp_the_same_cycle(run_env, state):
    """The parity pin. Identical finding, identical caller cycle, one door each
    — the stamps must be IDENTICAL, and per the ruling both must be 0."""
    project_root, fdir = run_env
    (fdir / "state.json").write_text(json.dumps({"phase": "F3", **state}), encoding="utf-8")

    single = _stamped_via_add(project_root, CALLER_CYCLE)
    batch = _stamped_via_sync(project_root, CALLER_CYCLE)

    assert single["cycle"] == batch["cycle"], (
        f"doors disagree on a malformed counter: single(foundry.py)="
        f"{single['cycle']} batch(orchestrator)={batch['cycle']} "
        f"(caller said {CALLER_CYCLE}). {_OWNER}"
    )
    assert single["cycle"] == 0, _OWNER


@pytest.mark.parametrize("state", _MALFORMED_COUNTERS)
def test_both_filing_doors_persist_the_callers_declared_cycle(run_env, state):
    """Auditability half of the ruling: what the caller CLAIMED survives on the
    record, so a divergence is visible and migrate/escalation tooling can
    reconcile it later. Mirrors stream-rollup's existing declared_cycle field.
    """
    project_root, fdir = run_env
    (fdir / "state.json").write_text(json.dumps({"phase": "F3", **state}), encoding="utf-8")

    single = _stamped_via_add(project_root, CALLER_CYCLE)
    batch = _stamped_via_sync(project_root, CALLER_CYCLE)

    assert single.get("declared_cycle") == CALLER_CYCLE, _OWNER
    assert batch["declared_cycle"] == CALLER_CYCLE


# --------------------------------------------------------------------------- #
# The BATCH door alone — casting 2's half of the ruling, verifiable on its own
# so this casting's completeness does not depend on the sibling's commit order.
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("state", _MALFORMED_COUNTERS)
def test_the_batch_door_stamps_zero_on_a_malformed_counter(run_env, state):
    project_root, fdir = run_env
    (fdir / "state.json").write_text(json.dumps({"phase": "F3", **state}), encoding="utf-8")

    assert _stamped_via_sync(project_root, CALLER_CYCLE)["cycle"] == 0


@pytest.mark.parametrize("state", _MALFORMED_COUNTERS)
def test_the_batch_door_persists_the_callers_declared_cycle(run_env, state):
    project_root, fdir = run_env
    (fdir / "state.json").write_text(json.dumps({"phase": "F3", **state}), encoding="utf-8")

    assert _stamped_via_sync(project_root, CALLER_CYCLE)["declared_cycle"] == CALLER_CYCLE


def test_the_batch_door_stamps_a_healthy_counter_over_the_callers_claim(run_env):
    """process-fixes ST-001 on the path that is entirely this casting's: the
    server counter wins, and the caller's 7 survives only as the audit
    field."""
    project_root, fdir = run_env
    (fdir / "state.json").write_text(json.dumps({"phase": "F3", "cycle": 4}), encoding="utf-8")

    record = _stamped_via_sync(project_root, CALLER_CYCLE)
    assert record["cycle"] == 4
    assert record["declared_cycle"] == CALLER_CYCLE


def test_a_healthy_counter_is_stamped_by_both_doors_unchanged(run_env):
    """process-fixes NFR-002: the ruling changes the MALFORMED path only. A
    real counter is still the authority at both doors, and the caller's 7 is
    still ignored."""
    project_root, fdir = run_env
    (fdir / "state.json").write_text(json.dumps({"phase": "F3", "cycle": 4}), encoding="utf-8")

    single = _stamped_via_add(project_root, CALLER_CYCLE)
    batch = _stamped_via_sync(project_root, CALLER_CYCLE)

    assert single["cycle"] == batch["cycle"] == 4
    assert batch["declared_cycle"] == CALLER_CYCLE
    assert single.get("declared_cycle") == CALLER_CYCLE, _OWNER


def test_mixed_door_filing_across_three_cycles_still_escalates(run_env):
    """The harm, end to end. Same class, three consecutive cycles, alternating
    doors, on an archive whose counter is malformed. Before the ruling the
    stamps were [1, 0, 3] — longest consecutive run 2 — and a genuine systemic
    class evaded escalation while the process-fixes AC-011 DONE guard
    passed."""
    project_root, fdir = run_env

    for cycle, door in ((1, _stamped_via_add), (2, _stamped_via_sync), (3, _stamped_via_add)):
        (fdir / "state.json").write_text(
            json.dumps({"phase": "F3", "cycle": cycle}), encoding="utf-8"
        )
        door(project_root, cycle)

    # A malformed counter must not let one door drift off the others.
    stamps = [d["cycle"] for d in json.loads((fdir / "defects.json").read_text())["defects"]]
    assert stamps == [1, 2, 3], stamps

    # The class key is the DECLARED one now, not the `type@file-cluster`
    # fallback: CT-002 makes `class` required at both doors, so a record filed
    # through either can no longer arrive without one. The fallback survives for
    # READING pre-change archives and is exercised by the `_defect` fixtures
    # elsewhere in this file; what this test is about is the CYCLE stamps, and
    # they are asserted above.
    escalated = _escalated_classes(fdir, project_root)
    assert set(escalated) == {FIXTURE_CLASS}, escalated
    assert escalated[FIXTURE_CLASS]["consecutive_cycles"] == 3


def _overrides_prefix(text: str) -> set[str]:
    """The PRE-fix override reader, verbatim: a substring test then a fallback.

    Kept so the evidence log can show the same phrasings judged by both rules
    side by side, rather than asserting the new behaviour against nothing.
    """
    if _escalation.ESCALATION_OVERRIDE_TOKEN not in text.lower():
        return set()
    scoped = {
        m.group(1).strip(" .,;:'\"`")
        for m in re.finditer(
            rf"{_escalation.ESCALATION_OVERRIDE_TOKEN}\s*[:=]\s*(\S+)", text, re.IGNORECASE
        )
    }
    return {s for s in scoped if s} or {"*"}


def render_override_table() -> str:
    """D-101 pre/post, over the phrasings the pins use. Used by the evidence log."""
    import tempfile

    rows = ["   %-9s %-9s %s" % ("PRE-fix", "post-fix", "directive text"),
            "   %-9s %-9s %s" % ("-------", "--------", "--------------")]

    def post(text: str) -> set[str]:
        root = Path(tempfile.mkdtemp())
        fdir = root / "foundry-archive" / "ov"
        (fdir / "castings").mkdir(parents=True)
        foundry_state.set_active_run("ov")
        try:
            foundry_inject_directive(text, "normal", str(root))
            return _escalation._escalation_overrides(str(root))
        finally:
            foundry_state.clear_active_run()

    def fmt(value: set[str]) -> str:
        if value == {"*"}:
            return "ALL"
        if not value:
            return "none"
        rendered = ",".join(sorted(value))
        return rendered if len(rendered) <= 24 else rendered[:21] + "..."

    out = ["== D-101: a MENTION of the token is not a REQUEST for an override =="] + rows
    for text in _OVERRIDE_NON_REQUESTS:
        out.append("   %-9s %-9s %s" % (fmt(_overrides_prefix(text)), fmt(post(text)),
                                        text.replace("\n", " / ")[:64]))
    out.append("")
    out.append("== the marker grammar, which must keep working ==")
    out.extend(rows)
    for text in _OVERRIDE_REQUESTS_ALL + ["escalation-override: FALSE_DOCUMENTED_CONTRACT"]:
        out.append("   %-9s %-9s %s" % (fmt(_overrides_prefix(text)), fmt(post(text)),
                                        text.replace("\n", " / ")[:64]))
    return "\n".join(out)


def test_the_override_table_shows_the_inversion_and_the_fix():
    """The pre/post table is asserted, not merely rendered: every forbidding
    phrasing overrode EVERY class before and overrides none now, and every
    marker-grammar line still lands."""
    # Every forbidding / discussing phrasing DID trigger an override before —
    # that is the inversion — and triggers none now.
    for text in _OVERRIDE_NON_REQUESTS:
        assert _overrides_prefix(text) != set(), f"pre-fix arm is wrong for {text!r}"

    # ...and most of them de-escalated EVERY class, not merely one.
    wholesale = [t for t in _OVERRIDE_NON_REQUESTS if _overrides_prefix(t) == {"*"}]
    assert len(wholesale) >= 6, wholesale

    table = render_override_table()
    assert "== D-101" in table
    assert table.count("ALL       none") == len(wholesale)


# --------------------------------------------------------------------------- #
# D-128 (process-fixes FR-020 / AC-025 / NFR-002) — the malformed-record row
# of the cross-door parity matrix.
#
# D-097 added `_dict_records` to foundry.py and applied it to the SINGLE door.
# The BATCH door kept `[d for d in records if d.get("status") == "fixed"]` over
# every historical record. Driven at ab5a430 with
# {"defects": [{"id":"D-001","status":"open"}, "not-a-dict"]}:
#
#     Foundry-Defect  -> D-002 persisted, malformed record preserved   (correct)
#     Foundry-Defects -> clean                                          (correct)
#     Foundry-Sync    -> AttributeError 'str' object has no attribute 'get'
#
# and the raise lands AFTER the in-transaction list is mutated, so nothing is
# written, the filing the stream just made is GONE, and the escaped text names
# no file. That is the one row breaking D-095/D-098's stated bar (21/24 named
# refusals, 2 tolerate, shape-8 x Sync raises AND loses).
#
# Asserted as PARITY across both doors rather than as a Sync outcome, for the
# same reason the cycle-stamp rows above are: two filing doors that disagree
# about what a defect ledger may contain is the defect, not the symptom.
# --------------------------------------------------------------------------- #

# Every non-dict JSON value a hand-edited or half-migrated ledger can hold.
_MALFORMED_RECORDS = ["not-a-dict", None, 42, True, ["nested"], 3.5]


@pytest.mark.parametrize("junk", _MALFORMED_RECORDS)
def test_neither_filing_door_raises_on_a_malformed_historical_record(run_env, junk):
    """process-fixes NFR-002 / the house rule: never raise across MCP, refuse
    by name."""
    project_root, fdir = run_env
    _write_state(fdir, phase="F2", cycle=1)
    _write_defects(fdir, [_defect("D-001", 1), junk])

    single = _stamped_via_add(project_root, 1)
    batch = _stamped_via_sync(project_root, 1)

    assert single["id"] and batch["id"]


@pytest.mark.parametrize("junk", _MALFORMED_RECORDS)
def test_neither_filing_door_loses_the_filing_it_was_handed(run_env, junk):
    """The harm, not the traceback. The raise aborted the transaction AFTER the
    append, so the good filing was discarded and the caller was told nothing
    that named a file. Both doors must PERSIST what they were handed."""
    project_root, fdir = run_env
    _write_state(fdir, phase="F2", cycle=1)
    _write_defects(fdir, [_defect("D-001", 1), junk])

    _sync_defects(
        cycle=1,
        findings=[{
            "source": "trace",
            "type": "UNWIRED",
            "description": "the session refresh never calls the token store",
            "symbol": "refresh_session",
            "file": "src/auth/session.py",
        }],
        project_root=project_root,
    )

    persisted = json.loads((fdir / "defects.json").read_text())["defects"]
    ids = [d.get("id") for d in persisted if isinstance(d, dict)]
    assert "D-002" in ids, persisted


@pytest.mark.parametrize("junk", _MALFORMED_RECORDS)
def test_neither_filing_door_discards_the_malformed_record(run_env, junk):
    """process-fixes NFR-002's no-narrowing half. Tolerating a junk record
    must not mean DELETING it -- that would be a quieter D-096, refusing to
    lose records to a
    bad container while losing them to a bad record. The single door already
    preserved it; the batch door must agree."""
    project_root, fdir = run_env
    _write_state(fdir, phase="F2", cycle=1)
    _write_defects(fdir, [_defect("D-001", 1), junk])

    _stamped_via_sync(project_root, 1)

    persisted = json.loads((fdir / "defects.json").read_text())["defects"]
    assert junk in persisted, persisted


@pytest.mark.parametrize("junk", _MALFORMED_RECORDS)
def test_both_doors_agree_on_the_open_count_over_a_junk_ledger(run_env, junk):
    """The scan D-128 named, read back through both doors. A count that
    disagrees is the two halves of the same scan diverging again."""
    project_root, fdir = run_env
    _write_state(fdir, phase="F2", cycle=1)
    _write_defects(fdir, [_defect("D-001", 1), junk])

    single = _add_defect(
        cycle=1, source="trace", defect_type="UNWIRED",
        description="a first finding", symbol="a", file_path="src/api/a.py",
        project_root=project_root,
    )
    batch = _sync_defects(
        cycle=1,
        findings=[{
            "source": "trace", "type": "UNWIRED",
            "description": "a second finding", "symbol": "b",
            "file": "src/api/b.py",
        }],
        project_root=project_root,
    )

    # The two doors spell the same number differently (`open_defects` vs
    # `total_open`); what must agree is the COUNT the scan produced.
    assert single["open_defects"] == 2, single
    assert batch["total_open"] == 3, batch


def test_a_regression_reopen_survives_a_junk_ledger(run_env):
    """The other raw scan in the same block: the reopen pass matched on
    `d["id"]`, which raises on a junk record exactly as `d.get` did."""
    project_root, fdir = run_env
    _write_state(fdir, phase="F2", cycle=1)
    fixed = _defect("D-001", 1, status="fixed", fixed_in_cycle=1,
                    symbol="refresh_session", file="src/auth/session.py",
                    description="the session refresh never calls the token store")
    _write_defects(fdir, ["not-a-dict", fixed])

    result = _sync_defects(
        cycle=1,
        findings=[{
            "source": "trace", "type": "UNWIRED",
            "description": "the session refresh never calls the token store",
            "symbol": "refresh_session", "file": "src/auth/session.py",
        }],
        project_root=project_root,
    )

    assert result["reopened"] == 1, result
    assert result["regressions"] == ["D-001"], result


# --------------------------------------------------------------------------- #
# D-133 (process-fixes AC-010 / ST-003 / FR-008) — the override grammar's two
# residual holes, and the common root under them.
#
# D-101's fix is correct and closed: a MENTION of the token is no longer a
# REQUEST. What survived it, both "invisible rather than refused":
#
#   (1) `_OVERRIDE_SCOPED_RE`'s value group was `(\S+)`, so a class key
#       containing a SPACE could never be overridden -- and process-fixes
#       FR-007 makes `class` free text a stream writes. Driven: "SHARED
#       RESOURCE LEAK"
#       escalates; Foundry-Directive("escalation-override: SHARED RESOURCE
#       LEAK") returns ok:true "injected", `_escalation_overrides()` is set(),
#       and the class stays escalated. `_structural_proposal` interpolates that
#       key into the instruction it hands the lead, so the tool tells the
#       operator to send a string it cannot read back.
#   (2) `_OVERRIDE_LINE_PREFIX` admitted the blockquote char, so QUOTING an
#       escalation packet into a directive silently de-escalated the class.
#   (3) COMMON ROOT: nothing reported an override in either direction.
# --------------------------------------------------------------------------- #

# Class keys a stream can legitimately declare under process-fixes FR-007's
# free text. The first is the one from the defect report.
_AWKWARD_CLASS_KEYS = [
    "SHARED RESOURCE LEAK",
    "a hardening mechanism bound by hand to the site where a defect was reported",
    "auth: token refresh",
    "UNWIRED@src/api",
    "FALSE_DOCUMENTED_CONTRACT",
]


@pytest.mark.parametrize("klass", _AWKWARD_CLASS_KEYS)
def test_a_class_key_with_a_space_can_be_overridden(run_env, klass):
    """(1) process-fixes FR-007 makes the class key free text; the grammar
    must be able to name any key a stream can declare."""
    project_root, fdir = run_env
    _write_defects(fdir, _recurring([1, 2, 3], klass=klass))
    _write_state(fdir, phase="F3", cycle=3)

    assert set(_escalated_classes(fdir, project_root)) == {klass}

    foundry_inject_directive(f"escalation-override: {klass}", "normal", project_root)

    assert _escalated_classes(fdir, project_root) == {}


@pytest.mark.parametrize("klass", _AWKWARD_CLASS_KEYS)
def test_the_restore_instruction_the_tool_prints_actually_works(run_env, klass):
    """The property behind (1), which the space bug was only one instance of:
    the marker text this server HANDS the operator must be one this server can
    READ BACK. Driven by feeding the printed instruction straight back in."""
    project_root, fdir = run_env
    _write_defects(fdir, _recurring([1, 2, 3], klass=klass))
    _write_state(fdir, phase="F3", cycle=3)

    escalated = _escalated_classes(fdir, project_root)
    proposal = _escalation._structural_proposal(escalated[klass] | {"proposal": ""})

    # Lift the directive text out of the printed Foundry-Directive('...') call.
    marker = re.search(r"Foundry-Directive\('(.+?)'\)\.", proposal)
    assert marker, proposal
    foundry_inject_directive(marker.group(1), "normal", project_root)

    assert _escalated_classes(fdir, project_root) == {}, (
        f"the proposal told the operator to send {marker.group(1)!r} and the "
        f"reader did not honour it"
    )


_QUOTED_MARKERS = [
    "> escalation-override: FALSE_DOCUMENTED_CONTRACT",
    ">escalation-override: *",
    "> escalation-override",
    "> - escalation-override: FALSE_DOCUMENTED_CONTRACT",
    ">> escalation-override: *",
    # The report's exact shape: buried at line 8 of a 9-line note.
    "\n".join([f"line {i}" for i in range(1, 8)]
              + ["> escalation-override: FALSE_DOCUMENTED_CONTRACT", "line 9"]),
]


@pytest.mark.parametrize("directive", _QUOTED_MARKERS)
def test_a_blockquoted_marker_is_not_honoured(run_env, directive):
    """(2) Quoting an escalation packet back into a directive is a REPORT of
    what the packet said, never a request to act on it."""
    project_root, fdir = run_env
    _write_defects(fdir, _recurring([1, 2, 3]))
    _write_state(fdir, phase="F3", cycle=3)

    foundry_inject_directive(directive, "normal", project_root)

    assert _escalation._escalation_overrides(project_root) == set()
    assert set(_escalated_classes(fdir, project_root)) == {"FALSE_DOCUMENTED_CONTRACT"}


@pytest.mark.parametrize("directive", _QUOTED_MARKERS)
def test_a_blockquoted_marker_is_reported_as_ignored_not_dropped(run_env, directive):
    """(3) Ignoring it silently is the same failure one step later: the
    operator must be told the marker was seen and why it did nothing."""
    project_root, fdir = run_env
    _write_defects(fdir, _recurring([1, 2, 3]))
    _write_state(fdir, phase="F3", cycle=3)

    result = foundry_inject_directive(directive, "normal", project_root)

    assert result["ok"] is True
    report = result.get("escalation_override")
    assert report, result
    assert report["quoted_ignored"], report
    assert any("IGNORED" in d for d in report["decisions"]), report


def test_a_recognised_override_is_reported_by_the_injecting_call(run_env):
    """(3) The working case must be reported too, or the operator still cannot
    tell a live override from a dead one."""
    project_root, fdir = run_env
    _write_defects(fdir, _recurring([1, 2, 3]))
    _write_state(fdir, phase="F3", cycle=3)

    result = foundry_inject_directive(
        "escalation-override: FALSE_DOCUMENTED_CONTRACT", "normal", project_root
    )

    report = result["escalation_override"]
    assert report["de_escalated"] == ["FALSE_DOCUMENTED_CONTRACT"], report
    assert "de-escalated" in " ".join(report["decisions"])
    assert "FALSE_DOCUMENTED_CONTRACT" in result["message"]


def test_an_override_naming_no_escalated_class_says_so(run_env):
    """(3) The mistyped-key case -- byte-identical to a working override before
    the fix. It must name what IS escalated so the typo is visible."""
    project_root, fdir = run_env
    _write_defects(fdir, _recurring([1, 2, 3]))
    _write_state(fdir, phase="F3", cycle=3)

    result = foundry_inject_directive(
        "escalation-override: FALSE_DOCUMENTED_CONTRAC", "normal", project_root
    )

    report = result["escalation_override"]
    assert report["unmatched"] == ["FALSE_DOCUMENTED_CONTRAC"], report
    assert report["de_escalated"] == []
    decisions = " ".join(report["decisions"])
    assert "matched NO escalated class" in decisions
    assert "FALSE_DOCUMENTED_CONTRACT" in decisions  # the real key, for comparison
    # ...and the class really is still escalated.
    assert set(_escalated_classes(fdir, project_root)) == {"FALSE_DOCUMENTED_CONTRACT"}


def test_foundry_next_reports_the_override_decision_too(run_env):
    """(3) A directive is STANDING text: the lead that reads it may be a
    different session from the one that sent it, so the decision has to be in
    the lead's own output and not only in the injecting call's return."""
    project_root, fdir = run_env
    _write_defects(fdir, _recurring([1, 2, 3]))
    _write_state(fdir, phase="F3", cycle=3)
    foundry_inject_directive(
        "escalation-override: FALSE_DOCUMENTED_CONTRAC", "normal", project_root
    )

    action = _guidance.foundry_next_action(project_root)

    assert action["escalation_overrides"]["unmatched"] == ["FALSE_DOCUMENTED_CONTRAC"]
    assert "ESCALATION-OVERRIDE DECISIONS" in action["instructions"]
    assert "matched NO escalated class" in action["instructions"]


def test_a_quoted_marker_beside_a_real_one_honours_only_the_real_one(run_env):
    """The two halves must not interfere: quoting the packet while ALSO asking
    for the override is a real request plus a quotation."""
    project_root, fdir = run_env
    _write_defects(fdir, _recurring([1, 2, 3]))
    _write_state(fdir, phase="F3", cycle=3)

    foundry_inject_directive(
        "As the packet says:\n"
        "> escalation-override: SOMETHING ELSE\n"
        "and I do want it here:\n"
        "escalation-override: FALSE_DOCUMENTED_CONTRACT",
        "normal",
        project_root,
    )

    assert _escalation._escalation_overrides(project_root) == {"FALSE_DOCUMENTED_CONTRACT"}
    assert _escalated_classes(fdir, project_root) == {}


@pytest.mark.parametrize("directive", _OVERRIDE_NON_REQUESTS)
def test_widening_the_value_group_did_not_reopen_d_101(run_env, directive):
    """process-fixes NFR-002 for the grammar itself. The value group went
    from `(\\S+)` to end-of-line, which is exactly the direction that could
    resurrect D-101's
    inversion. Every phrasing D-101 closed is re-driven against the new
    grammar."""
    project_root, fdir = run_env
    foundry_inject_directive(directive, "normal", project_root)

    assert _escalation._escalation_overrides(project_root) == set()


@pytest.mark.parametrize("directive", _OVERRIDE_REQUESTS_ALL)
def test_widening_the_value_group_kept_the_bare_marker_working(run_env, directive):
    """...and the capability process-fixes AC-010 requires still works."""
    project_root, fdir = run_env
    foundry_inject_directive(directive, "normal", project_root)

    assert _escalation._escalation_overrides(project_root) == {"*"}


# --------------------------------------------------------------------------- #
# Pre/post tables. Rendered for the evidence logs and ASSERTED below, so the
# comparison is a claim this suite holds rather than a picture beside it.
# --------------------------------------------------------------------------- #


def _prefix_scan(records: list) -> str:
    """The PRE-fix batch-door scan, verbatim, over ``records``.

    `foundry_sync_defects` line 3631 as it stood at ab5a430. Kept so the
    evidence log can show the same ledger judged by both rules side by side.
    """
    try:
        [d for d in records if d.get("status") == "fixed"]
        return "ok"
    except AttributeError as exc:
        return f"{type(exc).__name__}: {exc}"


def render_ledger_parity_table() -> str:
    """D-128 pre/post over every non-dict record shape a ledger can hold."""
    from foundry_mcp.tools.foundry import _dict_records

    out = [
        "== D-128: the batch door's scan, over a ledger holding one junk record ==",
        "   %-12s %-9s %s" % ("shape", "PRE-fix", "what the door got"),
        "   %-12s %-9s %s" % ("-" * 12, "-------", "-" * 17),
    ]
    for junk in _MALFORMED_RECORDS:
        records = [{"id": "D-001", "status": "open"}, junk]
        result = _prefix_scan(records)
        out.append("   %-12s %-9s %s" % (
            json.dumps(junk)[:12], "RAISES" if result != "ok" else "ok", result[:56]
        ))
    out.append("")
    out.append("== the same shapes through _dict_records, the one place the tolerance lives ==")
    for junk in _MALFORMED_RECORDS:
        records = [{"id": "D-001", "status": "open"}, junk]
        kept = _dict_records(records)
        out.append("   %-12s post-fix  scanned %d of %d records, no raise" % (
            json.dumps(junk)[:12], len(kept), len(records)
        ))
    return "\n".join(out)


def test_the_ledger_parity_table_shows_the_raise_and_the_fix():
    """Asserted, not merely rendered: every shape DID raise on the pre-fix
    scan -- that is the defect -- and every shape is scanned now."""
    from foundry_mcp.tools.foundry import _dict_records

    for junk in _MALFORMED_RECORDS:
        records = [{"id": "D-001", "status": "open"}, junk]
        assert _prefix_scan(records).startswith("AttributeError"), junk
        assert len(_dict_records(records)) == 1

    table = render_ledger_parity_table()
    assert table.count("RAISES") == len(_MALFORMED_RECORDS)
    assert "no raise" in table


def _prefix_override_prefix() -> re.Pattern:
    """The PRE-fix line prefix, verbatim: `[\\s>]*` admitted the blockquote."""
    return re.compile(
        rf"^[\s>]*(?:[-*+]\s*)?{_escalation.ESCALATION_OVERRIDE_TOKEN}\s*[:=]\s*(\S+)\s*$",
        re.IGNORECASE | re.MULTILINE,
    )


def _overrides_d133_prefix(text: str) -> set[str]:
    """D-101's grammar as it stood at ab5a430: `(\\S+)` value, `>` allowed.

    Reproduced whole, including the `_OVERRIDE_ALL_VALUES` mapping, so the
    PRE-fix column is what that code really returned and not a simplification
    of it.
    """
    scoped: set[str] = set()
    override_all = False
    for m in _prefix_override_prefix().finditer(text):
        value = m.group(1).strip(" .,;:'\"`")
        if not value:
            continue
        if value.lower() in _escalation._OVERRIDE_ALL_VALUES:
            override_all = True
        else:
            scoped.add(value)
    if re.search(
        rf"^[\s>]*(?:[-*+]\s*)?{_escalation.ESCALATION_OVERRIDE_TOKEN}\s*[:=]?\s*$",
        text, re.IGNORECASE | re.MULTILINE,
    ):
        override_all = True
    return {"*"} if override_all else scoped


def render_override_decision_table() -> str:
    """D-133 pre/post over the two holes, plus the reporting that was absent."""

    def fmt(value: set[str]) -> str:
        if value == {"*"}:
            return "ALL"
        if not value:
            return "none"
        rendered = ",".join(sorted(value))
        return rendered if len(rendered) <= 24 else rendered[:21] + "..."

    def post(text: str) -> set[str]:
        return _escalation._override_markers(text)["overrides"]

    out = [
        "== D-133 (1) THE VALUE: a class key with a space could never be named ==",
        "   %-24s %-24s %s" % ("PRE-fix", "post-fix", "directive text"),
        "   %-24s %-24s %s" % ("-" * 7, "-" * 8, "-" * 14),
    ]
    for key in _AWKWARD_CLASS_KEYS:
        text = f"{_escalation.ESCALATION_OVERRIDE_TOKEN}: {key}"
        out.append("   %-24s %-24s %s" % (
            fmt(_overrides_d133_prefix(text)), fmt(post(text)), text[:56]
        ))
    out.append("")
    out.append("== D-133 (2) THE PREFIX: quoting an escalation packet de-escalated it ==")
    out.extend(out[1:3])
    for text in _QUOTED_MARKERS:
        out.append("   %-24s %-24s %s" % (
            fmt(_overrides_d133_prefix(text)), fmt(post(text)),
            text.replace("\n", " / ")[:56]
        ))
    out.append("")
    out.append("== D-101's closures, re-driven against the widened value group ==")
    out.extend(out[1:3])
    for text in _OVERRIDE_NON_REQUESTS:
        out.append("   %-24s %-24s %s" % (
            fmt(_overrides_d133_prefix(text)), fmt(post(text)),
            text.replace("\n", " / ")[:56]
        ))
    out.append("")
    out.append("== the marker grammar AC-010 requires, still working ==")
    out.extend(out[1:3])
    for text in _OVERRIDE_REQUESTS_ALL:
        out.append("   %-24s %-24s %s" % (
            fmt(_overrides_d133_prefix(text)), fmt(post(text)),
            text.replace("\n", " / ")[:56]
        ))
    return "\n".join(out)


def test_the_override_decision_table_shows_both_holes_and_the_fix():
    """Asserted arm by arm.

    (1) every space-carrying key WAS unnameable and is nameable now;
    (2) every quoted marker DID override and overrides nothing now;
    and neither change reopened D-101 or broke process-fixes AC-010's
    capability.
    """
    spaced = [k for k in _AWKWARD_CLASS_KEYS if " " in k]
    assert len(spaced) >= 3
    for key in spaced:
        text = f"{_escalation.ESCALATION_OVERRIDE_TOKEN}: {key}"
        # The pre-fix reader could only ever see the LAST whitespace-free run,
        # never the key itself -- most often nothing at all.
        assert _overrides_d133_prefix(text) != {key}, key
        assert _escalation._override_markers(text)["overrides"] == {key}, key

    for text in _QUOTED_MARKERS:
        assert _overrides_d133_prefix(text) != set(), f"pre-fix arm wrong for {text!r}"
        assert _escalation._override_markers(text)["overrides"] == set(), text
        assert _escalation._override_markers(text)["quoted"], text

    for text in _OVERRIDE_NON_REQUESTS:
        assert _escalation._override_markers(text)["overrides"] == set(), text
    for text in _OVERRIDE_REQUESTS_ALL:
        assert _escalation._override_markers(text)["overrides"] == {"*"}, text

    table = render_override_decision_table()
    assert "D-133 (1) THE VALUE" in table
    assert "D-133 (2) THE PREFIX" in table


def _isolated_run(defects: list[dict]) -> tuple[str, Path]:
    """A throwaway activated run seeded with ``defects``. Caller clears."""
    import tempfile

    root = Path(tempfile.mkdtemp())
    fdir = root / "foundry-archive" / "ov"
    (fdir / "castings").mkdir(parents=True)
    (fdir / "state.json").write_text(json.dumps({"phase": "F3", "cycle": 3}))
    _write_defects(fdir, defects)
    foundry_state.set_active_run("ov")
    return str(root), fdir


# The five outcomes an operator has to be able to tell apart. Before the fix
# every one of them produced byte-identical output.
_OVERRIDE_OUTCOMES = [
    ("(no directive)", None),
    ("recognised", "escalation-override: FALSE_DOCUMENTED_CONTRACT"),
    ("mistyped key", "escalation-override: FALSE_DOCUMENTED_CONTRAC"),
    ("key with a space", "escalation-override: SHARED RESOURCE LEAK"),
    ("quoted out of a packet", "> escalation-override: FALSE_DOCUMENTED_CONTRACT"),
]


def _override_outcome_reports() -> list[tuple[str, bool, str]]:
    """Drive each outcome; return (label, still_escalated, what_was_reported)."""
    rows = []
    for label, text in _OVERRIDE_OUTCOMES:
        project_root, fdir = _isolated_run(_recurring([1, 2, 3]))
        try:
            result = (
                foundry_inject_directive(text, "normal", project_root) if text else {}
            )
            still = bool(_escalated_classes(fdir, project_root))
            report = result.get("escalation_override") or {}
            said = "; ".join(report.get("decisions", [])) or "(nothing)"
            rows.append((label, still, said))
        finally:
            foundry_state.clear_active_run()
    return rows


def render_override_root_table() -> str:
    """D-133 (3): the four decisions, each now distinguishable."""
    rows = _override_outcome_reports()
    out = [
        "== D-133 (3) THE ROOT: every outcome used to produce identical output ==",
        "   %-24s %-10s %s" % ("directive", "escalated", "what the tool reported"),
        "   %-24s %-10s %s" % ("-" * 9, "-" * 9, "-" * 22),
    ]
    for label, still, said in rows:
        out.append("   %-24s %-10s %s" % (label, "yes" if still else "NO", said[:92]))
    out.append("")
    out.append(
        "   distinct reports across the five outcomes: %d of %d   (PRE-fix: 1 of %d)"
        % (len({r[2] for r in rows}), len(rows), len(rows))
    )
    return "\n".join(out)


def test_every_override_outcome_is_distinguishable(run_env):
    """D-133 (3) asserted: the five outcomes must produce five DIFFERENT
    reports. One shared report is the defect -- an operator could not tell a
    live override from a dead one without watching the next Foundry-Tasks."""
    rows = _override_outcome_reports()
    said = [r[2] for r in rows]
    assert len(set(said)) == len(rows), rows
    by_label = {r[0]: r for r in rows}
    assert by_label["recognised"][1] is False
    for label in ("mistyped key", "key with a space", "quoted out of a packet"):
        assert by_label[label][1] is True, by_label[label]
    assert "(nothing)" == by_label["(no directive)"][2]


def _door_outcomes() -> list[tuple]:
    """Drive both filing doors over each junk shape; return the matrix rows."""
    from foundry_mcp.tools.foundry import foundry_add_defect

    def drive(fn):
        try:
            fn()
            return "ok"
        except Exception as exc:  # noqa: BLE001 - the point is what escaped
            return type(exc).__name__

    rows = []
    for junk in _MALFORMED_RECORDS:
        project_root, fdir = _isolated_run([_defect("D-001", 1), junk])
        try:
            single = drive(lambda: _add_defect(
                cycle=1, source="trace", defect_type="UNWIRED", description="a",
                symbol="a", file_path="src/a.py", project_root=project_root))
            batch = drive(lambda: _sync_defects(
                cycle=1, findings=[{"source": "trace", "type": "UNWIRED",
                                    "description": "b", "symbol": "b",
                                    "file": "src/b.py"}],
                project_root=project_root))
            records = json.loads((fdir / "defects.json").read_text())["defects"]
            ids = [d.get("id") for d in records if isinstance(d, dict)]
            rows.append((junk, single, batch, "D-003" in ids, junk in records))
        finally:
            foundry_state.clear_active_run()
    return rows


def render_ledger_door_table() -> str:
    """D-128: both doors, every junk shape, all three properties."""
    out = [
        "== both doors DRIVEN: does either raise, lose the filing, or drop the junk? ==",
        "   %-12s %-10s %-10s %-12s %s" % (
            "junk record", "Defect", "Sync", "filing kept", "junk kept"),
        "   %-12s %-10s %-10s %-12s %s" % (
            "-" * 11, "-" * 6, "-" * 4, "-" * 11, "-" * 9),
    ]
    for junk, single, batch, kept, preserved in _door_outcomes():
        out.append("   %-12s %-10s %-10s %-12s %s" % (
            json.dumps(junk)[:12], single, batch,
            "yes" if kept else "NO", "yes" if preserved else "NO"))
    out.append("")
    out.append(
        "   PRE-fix every row read: Defect=ok  Sync=AttributeError  "
        "filing kept=NO  junk kept=yes"
    )
    return "\n".join(out)


def test_the_ledger_door_table_shows_both_doors_agreeing(run_env):
    """D-128 asserted over the whole matrix: no raise, no lost filing, no
    dropped junk record, at either door, for any shape."""
    rows = _door_outcomes()
    assert len(rows) == len(_MALFORMED_RECORDS)
    for junk, single, batch, kept, preserved in rows:
        assert single == "ok", (junk, single)
        assert batch == "ok", (junk, batch)
        assert kept, f"the filing handed to the doors was lost for {junk!r}"
        assert preserved, f"the malformed record was DROPPED for {junk!r}"


# --------------------------------------------------------------------------- #
# ST-001 / ST-002 / FR-001 / FR-002 / FR-003 / FR-028 — THE MECHANICAL EXIT
#
# thunder-viper is the reason this section exists. An adversarial prover with no
# convergence criterion re-filed one class at a finer boundary in cycles 19, 20
# and 21 — never driving a live instance — so the class never emptied, never
# stopped drawing a structural packet, and the run ended only when a human told
# the lead to fix the last defects directly. Escalation had exactly one exit:
# every instance closing.
#
# Two exits now sit beside closure, and CLEARED is the persisted answer to both.
# Neither waives anything: an open LIVE instance of a CLEARED class is still a
# blocking defect, fixed one at a time. What ends is only the SHAPE of the work.
# --------------------------------------------------------------------------- #


def _escalate(fdir: Path, project_root: str, klass: str = "FALSE_DOCUMENTED_CONTRACT",
              cycles: tuple[int, ...] = (1, 2, 3), tier: str = "LIVE") -> None:
    """Put `klass` into ESCALATED, through the door that really escalates it.

    Files one open instance per cycle and then calls Foundry-Tasks, which is what
    records the class in escalation.json — recorded state is a precondition of
    both exits, and a fixture that hand-wrote it would be testing the exit
    against a record no code path produces.
    """
    _write_defects(fdir, [
        _defect(f"D-{i:03d}", cycle, **{"class": klass, "tier": tier})
        for i, cycle in enumerate(cycles, start=1)
    ])
    _write_state(fdir, phase="F2", cycle=cycles[-1])
    foundry_defects_to_tasks(project_root)


def _escalation_entry(fdir: Path, klass: str = "FALSE_DOCUMENTED_CONTRACT") -> dict:
    return json.loads(
        (fdir / _escalation.ESCALATION_FILENAME).read_text(encoding="utf-8")
    )["classes"][klass]


def _cross_boundary(fdir: Path, project_root: str) -> dict:
    """One GRIND -> INSPECT crossing, which is where the clean count advances."""
    _arm(fdir)
    state = json.loads((fdir / "state.json").read_text(encoding="utf-8"))
    state["phase"] = "F3"
    (fdir / "state.json").write_text(json.dumps(state), encoding="utf-8")
    _arm(fdir)
    return foundry_mark_phase_complete("inspect_start", project_root)


def test_two_clean_cycles_clear_the_class_and_stop_the_structural_packet(run_env):
    """AC-001 verbatim: 'On a synthetic fixture where a class is escalated and
    the next two server-counted INSPECT cycles file zero LIVE instances of it,
    the class is CLEARED and Foundry-Tasks emits no structural packet for it.'

    FR-003's arm. Counted on the SERVER cycle stamp (ST-001), at the INSPECT
    boundary — the only event that knows a cycle has ended. `_escalated_classes`
    is re-derived from the ledger on every call and holds no memory, and the
    ledger cannot distinguish "cycle N ran and this class drew nothing" from
    "cycle N never ran", so the count is persisted rather than inferred.
    """
    project_root, fdir = run_env
    _escalate(fdir, project_root)
    assert _escalation_entry(fdir)["status"] == "ESCALATED"

    # The first crossing closes cycle 3 — the cycle the class escalated ON, which
    # ST-001 excludes — and opens cycle 4.
    first = _cross_boundary(fdir, project_root)
    assert first["cycle"] == 4
    assert _escalation_entry(fdir)["live_clean_cycles"] == 0

    # The second closes cycle 4, which drew nothing of the class. One clean.
    second = _cross_boundary(fdir, project_root)
    assert second["cycle"] == 5
    assert _escalation_entry(fdir)["live_clean_cycles"] == 1
    assert _escalation_entry(fdir)["status"] == "ESCALATED", (
        "one clean cycle is not two"
    )

    # The third closes cycle 5. Two consecutive clean cycles, and the class is out.
    third = _cross_boundary(fdir, project_root)
    assert third["cycle"] == 6

    entry = _escalation_entry(fdir)
    assert entry["status"] == "CLEARED"
    assert entry["exit_reason"] == "clean_cycles"
    # D-057's ruling: `cleared_at_cycle` is the counter AFTER the boundary that
    # applied the exit. The exit is applied BY this crossing, so dating it to
    # the cycle that just ended would put it inside the cycle whose work earned
    # it — and the same rule holds for both arms, so a report reading the field
    # never has to ask which one fired to know what the number means.
    assert entry["cleared_at_cycle"] == 6
    assert third["cycle"] == 6
    assert third["escalation_cleared"][0]["class"] == "FALSE_DOCUMENTED_CONTRACT"

    # And the packet stops. This is the half AC-001 is actually about: a CLEARED
    # class draws no more structural work.
    tasks = foundry_defects_to_tasks(project_root)
    assert tasks["escalated_classes"] == []
    assert tasks["structural_tasks"] == 0


def test_latent_instances_do_not_reset_the_clean_count(run_env):
    """ST-001 verbatim: 'LATENT instances do not reset the count.'

    THE MECHANISM, not a detail. A prover re-filing the same class at a finer
    boundary every cycle, never driving a live instance, is exactly thunder-viper
    — and if a LATENT filing reset the counter the class could be held open
    forever by findings nobody reproduced. That is the loop the exit exists to
    terminate.
    """
    project_root, fdir = run_env
    _escalate(fdir, project_root)

    # Cycle 3 closes first (the escalation cycle, excluded), then cycles 4 and 5
    # each draw one LATENT instance at a finer boundary and nothing reproduced.
    _cross_boundary(fdir, project_root)
    for cycle, did in ((4, "D-101"), (5, "D-102")):
        defects = json.loads((fdir / "defects.json").read_text(encoding="utf-8"))["defects"]
        defects.append(_defect(did, cycle, **{
            "class": "FALSE_DOCUMENTED_CONTRACT", "tier": "LATENT",
            "reproduction_attempted": "AST sweep of every call site finds 0 sites",
        }))
        _write_defects(fdir, defects)
        _cross_boundary(fdir, project_root)

    entry = _escalation_entry(fdir)
    assert entry["live_clean_cycles"] == 2
    assert entry["status"] == "CLEARED"
    assert entry["exit_reason"] == "clean_cycles"


# --------------------------------------------------------------------------- #
# D-043 — a class whose instances were all FIXED must still leave escalation
# --------------------------------------------------------------------------- #


def _fix_every_instance(fdir: Path, cycle: int) -> None:
    """Close every open defect, the way a GRIND cycle that succeeded would."""
    defects = json.loads((fdir / "defects.json").read_text(encoding="utf-8"))["defects"]
    for d in defects:
        d["status"] = "fixed"
        d["fixed_in_cycle"] = cycle
    _write_defects(fdir, defects)


def test_a_class_whose_instances_were_all_fixed_clears_on_the_clean_arm(run_env):
    """AC-004 verbatim: 'escalation.json records for the class a status, the
    exit reason (clean-cycles or budget), the cycle it cleared and the
    structural packets it consumed.'

    D-043: it recorded none of them for the commonest ending of all. Both exit
    arms iterated `_escalated_classes`, which opens with
    `if not bucket["open"]: continue`, so the moment a structural fix actually
    WORKED and every instance closed, the class became invisible to both arms.
    Driven over four real GRIND->INSPECT crossings: `live_clean_cycles` 0, 0, 0,
    0; `status` ESCALATED; `exit_reason` null; `cleared_at_cycle` null — and the
    generated report's escalated-classes row carried a blank exit reason while
    report.json carried `by_status {"CLEARED": 0, "ESCALATED": 1}`, calling
    finished work unresolved in the section AC-004 and FR-023 name.

    A class with nothing open draws zero LIVE instances by construction, which
    is exactly the condition ST-001 counts, so `clean_cycles` is the arm that
    fires and no third exit reason is needed (`ESCALATION_EXIT_REASONS` is
    frozen at {clean_cycles, budget}).
    """
    project_root, fdir = run_env
    _escalate(fdir, project_root)
    assert _escalation_entry(fdir)["status"] == "ESCALATED"

    _fix_every_instance(fdir, cycle=3)
    # `_escalated_classes` drops the class from here on — that is the blindness
    # the arms used to inherit.
    assert _escalated_classes(fdir, project_root) == {}

    _cross_boundary(fdir, project_root)          # closes cycle 3, the excluded one
    assert _escalation_entry(fdir)["live_clean_cycles"] == 0

    _cross_boundary(fdir, project_root)          # closes cycle 4 — one clean
    assert _escalation_entry(fdir)["live_clean_cycles"] == 1
    assert _escalation_entry(fdir)["status"] == "ESCALATED"

    third = _cross_boundary(fdir, project_root)  # closes cycle 5 — two clean

    entry = _escalation_entry(fdir)
    assert entry["status"] == "CLEARED"
    assert entry["exit_reason"] == "clean_cycles"
    assert entry["exit_reason"] in ESCALATION_EXIT_REASONS
    assert entry["cleared_at_cycle"] == 6  # the counter AFTER the boundary
    assert third["cycle"] == 6
    assert isinstance(entry["structural_packets_dispatched"], int)
    assert third["escalation_cleared"][0]["class"] == "FALSE_DOCUMENTED_CONTRACT"


def test_the_exit_arms_read_the_escalation_ledger_not_the_open_work(run_env):
    """THE PROPERTY, derived from the source rather than from the one symptom.

    D-043's cause is a roster, not a count: the arms asked `_escalated_classes`
    — a function whose contract is "classes with open work that are still
    escalating" — for the answer to "which classes has the run recorded as
    ESCALATED". Those are different questions, and a future edit that puts an
    arm back on the first one re-opens every symptom at once.

    D-058 moved BOTH arms into `_advance_escalation_exits`, so that is the one
    function this property is about now. `_spend_structural_budget` is asserted
    separately, and in the opposite direction: it dispatches, and it must key on
    what the CALLER escalated, never on the persisted roster — counting a packet
    against a class that drew none is the mirror-image defect.
    """
    import ast
    import inspect
    import textwrap

    def _calls(fn) -> set[str]:
        tree = ast.parse(textwrap.dedent(inspect.getsource(fn)))
        return {
            node.func.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }

    exits = _calls(_escalation._advance_escalation_exits)
    assert "_escalated_classes" not in exits, (
        "_advance_escalation_exits iterates _escalated_classes again, which "
        "omits a class with no open instances — D-043's exact blindness."
    )
    assert "_persisted_escalations" in exits, (
        "_advance_escalation_exits no longer reads the persisted ESCALATED "
        "roster, so a class the ledger records as escalated can be unreachable "
        "again."
    )

    spend = _calls(_escalation._spend_structural_budget)
    assert "_escalated_classes" not in spend
    assert "_persisted_escalations" not in spend, (
        "the dispatcher must count only against the classes this call actually "
        "packeted (D-043); reading the persisted roster here would increment a "
        "budget for a class that got no packet."
    )


def test_the_dispatcher_holds_no_exit_arm(run_env):
    """D-058, as the property rather than the one call sequence.

    ST-002's trigger is the second structural packet CLOSING, and
    `foundry_defects_to_tasks` cannot observe a close — it is the call that
    OPENS the work. So no writer of `status`, `exit_reason` or
    `cleared_at_cycle` may live on the dispatch path, and both arms live at the
    boundary that knows a cycle ended.
    """
    import ast
    import inspect
    import textwrap

    exit_fields = {"status", "exit_reason", "cleared_at_cycle"}
    for fn in (_escalation._spend_structural_budget, _directives.foundry_defects_to_tasks):
        tree = ast.parse(textwrap.dedent(inspect.getsource(fn)))
        written = {
            target.slice.value
            for node in ast.walk(tree)
            if isinstance(node, ast.Assign)
            for target in node.targets
            if isinstance(target, ast.Subscript)
            and isinstance(target.slice, ast.Constant)
        }
        assert not (written & exit_fields), (
            f"{fn.__name__} writes {sorted(written & exit_fields)} — an exit "
            "applied on the dispatch path retracts the packet it just emitted."
        )


def test_an_overridden_class_is_not_cleared_by_an_arm(run_env):
    """The adjacent path the ledger read opens, closed deliberately.

    `_escalated_classes` filters classes the operator de-escalated with a
    directive; a roster read straight off `escalation.json` would not, and an
    arm would then stamp CLEARED with an exit reason no rule earned — recording
    the operator's decision as the machine's, irreversibly, because CLEARED is
    terminal and withdrawing the directive could never bring the class back.
    """
    project_root, fdir = run_env
    _escalate(fdir, project_root)
    _fix_every_instance(fdir, cycle=3)
    foundry_inject_directive(
        "escalation-override: FALSE_DOCUMENTED_CONTRACT", project_root=project_root
    )

    for _ in range(4):
        _cross_boundary(fdir, project_root)

    entry = _escalation_entry(fdir)
    assert entry["status"] == "ESCALATED", (
        "an override de-escalates a class; it must not be laundered into a "
        "machine-earned CLEARED"
    )
    assert entry["exit_reason"] is None


def test_the_recorded_proposal_never_asserts_a_fixed_instance_is_open(run_env):
    """AC-004's record is read by the F6 report, so a stale field is a false
    statement in the run's own final artifact.

    D-043: `foundry_defects_to_tasks` wrote
    `info.get("proposal") or _structural_proposal(info)`, so the FIRST proposal
    a class drew was the one every later report carried — open counts and
    defect ids frozen at that moment. This run's own report.json asserted a
    class 'still has 3 open instance(s)' and that all three 'must reach fixed'
    when all three were fixed.
    """
    project_root, fdir = run_env
    _escalate(fdir, project_root)
    assert "3 open instance(s)" in _escalation_entry(fdir)["proposal"]

    _fix_every_instance(fdir, cycle=3)
    foundry_defects_to_tasks(project_root)

    proposal = _escalation_entry(fdir)["proposal"]
    assert "3 open instance(s)" not in proposal, proposal
    assert "must reach fixed" not in proposal, proposal
    assert "every instance of it is now closed" in proposal, proposal


def _grind_cycle(fdir: Path, project_root: str) -> dict:
    """ONE REAL GRIND CYCLE, not just the boundary crossing.

    D-001: `_cross_boundary` calls `inspect_start` alone, which is a path a real
    cycle cannot take — `foundry_gate`'s grind branch refuses without
    `.tasks-generated`, and only `foundry_defects_to_tasks` writes it. So every
    real cycle also runs the tool that records escalation state, and a test that
    skips it is driving the exit against a record no run produces.
    """
    foundry_defects_to_tasks(project_root)
    return _cross_boundary(fdir, project_root)


def _file_finer_latent(fdir: Path, did: str, cycle: int) -> None:
    """One more never-reproduced instance of the class, at a finer boundary."""
    defects = json.loads((fdir / "defects.json").read_text(encoding="utf-8"))["defects"]
    defects.append(_defect(did, cycle, **{
        "class": "FALSE_DOCUMENTED_CONTRACT", "tier": "LATENT",
        "reproduction_attempted": "AST sweep of every call site finds 0 sites",
    }))
    _write_defects(fdir, defects)


def test_a_later_filing_does_not_re_date_the_escalation_in_a_real_cycle(run_env):
    """D-001, in the default configuration and with nothing patched.

    `_record_escalation_proposals` bare-assigned `escalated_at_cycle` from
    `_escalated_classes`'s CURRENT `_consecutive_run` end, which is recomputed
    from the ledger on every call. `foundry_defects_to_tasks` runs that recorder
    once per GRIND and cannot be skipped — `foundry_gate`'s grind branch refuses
    without `.tasks-generated`, which only that tool writes — so one new filing
    at a finer boundary re-dated the escalation to the newest cycle.
    """
    project_root, fdir = run_env
    _escalate(fdir, project_root)
    assert _escalation_entry(fdir)["escalated_at_cycle"] == 3

    _grind_cycle(fdir, project_root)          # closes cycle 3, the escalation cycle
    _file_finer_latent(fdir, "D-101", 4)
    _grind_cycle(fdir, project_root)

    assert _escalation_entry(fdir)["escalated_at_cycle"] == 3, (
        "the escalation date is a latch: it records WHEN the class escalated, "
        "not when it was last filed against"
    )


def test_the_clean_arm_fires_across_real_grind_cycles_not_just_boundaries(
    run_env, monkeypatch
):
    """ST-001 verbatim: 'the second consecutive INSPECT cycle in which the class
    draws zero LIVE instances ... LATENT instances do not reset the count.'

    D-001: the arm could not fire in a REAL cycle. The re-dated latch made
    ST-001's own guard — `completed_cycle <= escalated_at`, applied by
    `_clean_arm_step` for `_advance_escalation_exits` — skip the count on every
    crossing, so
    `live_clean_cycles` sat at 0 forever and only the budget arm could ever
    terminate a class. That is the exact finer-boundary loop this effort exists
    to end, converging on the arm the effort added.

    The structural budget is raised for the length of this test so the OTHER
    exit cannot pre-empt the one under test — ST-002's arm fires on the second
    packet, which in a real run arrives before two clean cycles can. Every code
    path driven here is the production one; only which arm gets to win is
    pinned.
    """
    project_root, fdir = run_env
    patch_everywhere(monkeypatch, "STRUCTURAL_PASS_BUDGET", 99)
    _escalate(fdir, project_root)

    _grind_cycle(fdir, project_root)          # closes cycle 3, the escalation cycle
    for cycle, did in ((4, "D-101"), (5, "D-102")):
        _file_finer_latent(fdir, did, cycle)
        _grind_cycle(fdir, project_root)

    entry = _escalation_entry(fdir)
    assert entry["escalated_at_cycle"] == 3
    assert entry["live_clean_cycles"] == 2
    assert entry["status"] == "CLEARED"
    assert entry["exit_reason"] == "clean_cycles"
    assert entry["cleared_at_cycle"] == 6  # the counter AFTER the boundary


def test_every_writer_of_the_escalation_date_treats_it_as_a_latch(run_env):
    """DERIVED FROM THE SOURCE, because D-001 was one writer disagreeing with
    two. Three functions write `escalated_at_cycle`; a bare assignment in ANY of
    them re-dates the escalation and silently disables ST-001's clean arm, so
    the property is asserted over the module's own AST rather than over the one
    writer that happened to be wrong.
    """
    import ast
    import inspect
    import textwrap

    writers = (
        _escalation._record_escalation_proposals,
        _escalation._spend_structural_budget,
        _escalation._advance_escalation_exits,
    )
    bare: list[str] = []
    for fn in writers:
        tree = ast.parse(textwrap.dedent(inspect.getsource(fn)))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Assign):
                continue
            for target in node.targets:
                if (
                    isinstance(target, ast.Subscript)
                    and isinstance(target.slice, ast.Constant)
                    and target.slice.value == "escalated_at_cycle"
                ):
                    bare.append(f"{fn.__name__}:{node.lineno}")

    assert bare == [], (
        f"{bare} assign escalated_at_cycle directly. It is a latch — write it "
        "with setdefault, or the clean-cycle exit stops firing."
    )


def test_one_live_instance_in_a_cycle_resets_the_count(run_env):
    """The other side of the same rule, and the reason the exit is safe: a class
    that is still drawing REPRODUCED instances has not converged, and the clean
    count starts again from zero rather than accumulating across the recurrence.
    """
    project_root, fdir = run_env
    _escalate(fdir, project_root)

    _cross_boundary(fdir, project_root)   # closes cycle 3, the escalation cycle
    _cross_boundary(fdir, project_root)   # closes cycle 4, clean
    assert _escalation_entry(fdir)["live_clean_cycles"] == 1

    defects = json.loads((fdir / "defects.json").read_text(encoding="utf-8"))["defects"]
    defects.append(_defect("D-101", 5, **{
        "class": "FALSE_DOCUMENTED_CONTRACT", "tier": "LIVE",
    }))
    _write_defects(fdir, defects)
    _cross_boundary(fdir, project_root)

    entry = _escalation_entry(fdir)
    assert entry["live_clean_cycles"] == 0
    assert entry["status"] == "ESCALATED"


def test_the_escalation_cycle_itself_is_never_counted_as_clean(run_env):
    """ST-001's guard: 'the class must have been escalated before the two cycles
    began.'

    The cycle a class escalates ON is the cycle whose third consecutive filing
    escalated it, so it is by construction not a clean one. Counting cycles at or
    before `escalated_at_cycle` would let a class clear on history that predates
    the escalation entirely — an exit granted for cycles that happened before
    anyone was watching.
    """
    project_root, fdir = run_env
    _escalate(fdir, project_root, cycles=(1, 2, 3))
    entry = _escalation_entry(fdir)
    assert entry["escalated_at_cycle"] == 3

    # Rewind the counter so the crossing evaluates cycle 3 — the escalation
    # cycle itself, which drew a LIVE instance and is not eligible anyway.
    _write_state(fdir, phase="F3", cycle=2)
    _cross_boundary(fdir, project_root)

    assert _escalation_entry(fdir)["live_clean_cycles"] == 0
    assert _escalation_entry(fdir)["status"] == "ESCALATED"


def test_the_second_structural_packet_clears_the_class_when_it_closes(run_env):
    """ST-002 verbatim: 'the structural-pass budget for the class is exhausted
    (second structural packet CLOSED)'. AC-002: 'CLEARED after the second
    structural packet CLOSES.'

    A packet CLOSES when the GRIND cycle it was dispatched in ends, and the
    event that knows a cycle ended is the `inspect_start` boundary — so the
    class is still ESCALATED for the whole of the cycle its last packet is
    being worked in, and CLEARS on the crossing out of it. Two passes is not a
    judgement that the class is fixed; it is the statement that structural work
    has had its turn.
    """
    project_root, fdir = run_env
    _escalate(fdir, project_root)

    entry = _escalation_entry(fdir)
    assert entry["structural_packets_dispatched"] == 1
    assert entry["status"] == "ESCALATED"

    _write_state(fdir, phase="F2", cycle=4)
    second = foundry_defects_to_tasks(project_root)

    assert second["structural_tasks"] == 1, "the retry is dispatched, not skipped"
    assert second["structural_packets_counted"] == ["FALSE_DOCUMENTED_CONTRACT"]
    entry = _escalation_entry(fdir)
    assert entry["structural_packets_dispatched"] == STRUCTURAL_PASS_BUDGET
    assert entry["structural_packet_cycles"] == [3, 4]
    assert entry["status"] == "ESCALATED", (
        "the packet has been dispatched, not closed — the class is still "
        "escalated while cycle 4's GRIND works it"
    )

    crossing = _cross_boundary(fdir, project_root)  # closes cycle 4

    entry = _escalation_entry(fdir)
    assert entry["status"] == "CLEARED"
    assert entry["exit_reason"] == "budget"
    assert entry["cleared_at_cycle"] == crossing["cycle"] == 5
    assert crossing["escalation_cleared"][0]["exit_reason"] == "budget"

    third = foundry_defects_to_tasks(project_root)
    assert third["structural_tasks"] == 0
    assert third["escalated_classes"] == []


def test_the_budget_arm_does_not_retract_the_packet_it_just_dispatched(run_env):
    """D-058: `Foundry-Tasks` DISPATCHES and COUNTS; it never clears.

    The budget arm ran inside `foundry_defects_to_tasks`, BEFORE the packets
    were built, so it flipped the class to CLEARED in the same call that emitted
    packet 2 — and `_escalated_classes` skips a CLEARED class. Driven: escalate
    at cycle 3, advance to cycle 4, call Foundry-Tasks (structural_tasks 1,
    status CLEARED), then call it AGAIN in the SAME cycle -> structural_tasks 0
    and escalated_classes [], while the packet just dispatched was still being
    worked. Foundry-Tasks is explicitly a tool a lead may call twice in one
    cycle, which is the whole reason `structural_packet_cycles` exists.
    """
    project_root, fdir = run_env
    _escalate(fdir, project_root)
    _write_state(fdir, phase="F2", cycle=4)

    second = foundry_defects_to_tasks(project_root)
    assert second["structural_tasks"] == 1

    again = foundry_defects_to_tasks(project_root)

    assert again["structural_tasks"] == 1, (
        "the class is still being worked structurally in this cycle; re-reading "
        "the task list must show the packet that was dispatched"
    )
    assert again["escalated_classes"] == ["FALSE_DOCUMENTED_CONTRACT"]
    # ...and the second read spent nothing: one packet per class per cycle.
    assert "structural_packets_counted" not in again
    entry = _escalation_entry(fdir)
    assert entry["structural_packets_dispatched"] == STRUCTURAL_PASS_BUDGET
    assert entry["structural_packet_cycles"] == [3, 4]


def test_calling_tasks_twice_in_one_cycle_spends_one_pass(run_env):
    """A lead re-reading the task list is not a second structural pass.

    `Foundry-Tasks` is an ordinary tool a lead may call twice in a cycle, and a
    budget a double-click could exhaust would end escalation after ONE real
    attempt. The recorded cycle list is the guard, which also makes the record
    legible: it reads as "the cycles this class was worked structurally in".
    """
    project_root, fdir = run_env
    _escalate(fdir, project_root)

    foundry_defects_to_tasks(project_root)
    foundry_defects_to_tasks(project_root)

    entry = _escalation_entry(fdir)
    assert entry["structural_packets_dispatched"] == 1
    assert entry["structural_packet_cycles"] == [3]
    assert entry["status"] == "ESCALATED"


def test_the_finer_boundary_scenario_is_driven_and_clears_on_budget(run_env):
    """AC-002 verbatim: 'On a synthetic fixture where each cycle's PROVE files
    one LATENT instance of the escalated class at a finer boundary, the class is
    CLEARED after the second structural packet closes, the run reaches NYQUIST,
    and the report names the LATENT instances left.'

    D-115 — THE FIXTURE IS A STARTING STATE, NEVER THE EXPECTED OUTPUT.
    ------------------------------------------------------------------
    This test used to copy eight fixture files into the run directory and then
    assert `entry["status"] == "CLEARED"`, `entry["exit_reason"] == "budget"` and
    `entry["structural_packets_dispatched"] == STRUCTURAL_PASS_BUDGET`, with NO
    production call between the copy and the reads. Every one of those
    assertions was a read-back of the test's own input: the commit that
    introduced it said its test "drives those transitions instead of asserting
    typed JSON", and for this half that was not so. The budget arm is genuinely
    driven elsewhere, so no coverage was missing — what was missing was the
    driving this test claimed.

    So the fixture supplies the SCENARIO — casting 5's realised finer-boundary
    run, whose defect rows carry the real files, symbols and ever-finer
    descriptions — and the run is walked through the REAL doors: file the three
    LIVE instances that escalate the class, dispatch a structural packet, cross
    the boundary, file the LATENT instance at a finer boundary, dispatch the
    second packet, cross again. `escalation.json` is then whatever PRODUCTION
    wrote, and nothing in this test put it there.
    """
    project_root, fdir = run_env
    fixture = Path(__file__).parent / "fixtures" / "escalation" / "finer_boundary_run"
    scenario = json.loads(
        (fixture / "defects.json").read_text(encoding="utf-8")
    )["defects"]
    klass = "FALSE_DOCUMENTED_CONTRACT"
    by_id = {d["id"]: d for d in scenario}

    def _row(did: str, **over) -> dict:
        row = dict(by_id[did])
        row.update(over)
        return row

    # --- cycles 1-3: three LIVE instances of one class, which is what escalates
    #     it. Filed OPEN, because a class with nothing open is not escalating.
    ledger = [_row(d, status="open", fixed_in_cycle=None) for d in
              ("D-001", "D-002", "D-003")]
    ledger.append(_row("D-006", status="open", fixed_in_cycle=None))
    _write_defects(fdir, ledger)
    _write_state(fdir, phase="F2", cycle=3, nyquist=True)

    assert klass in _escalated_classes(fdir, project_root), (
        "the scenario must actually escalate the class through the ledger rule"
    )

    # --- packet 1, dispatched in cycle 3 by the door that dispatches packets.
    tasks = foundry_defects_to_tasks(project_root)
    assert tasks["structural_tasks"] == 1, tasks
    assert _escalation_entry(fdir)["structural_packets_dispatched"] == 1

    _cross_boundary(fdir, project_root)          # closes 3, opens 4
    assert _current_cycle(fdir) == 4

    # --- cycle 4: the structural pass closed the LIVE instances, and PROVE files
    #     the same class one boundary finer — LATENT, because nothing reproduced.
    ledger = [_row(d, status="fixed", fixed_in_cycle=4) for d in
              ("D-001", "D-002", "D-003", "D-006")]
    ledger.append(_row("D-004", status="open", fixed_in_cycle=None))
    _write_defects(fdir, ledger)

    tasks = foundry_defects_to_tasks(project_root)
    assert tasks["structural_tasks"] == 1, tasks
    entry = _escalation_entry(fdir)
    assert entry["structural_packets_dispatched"] == STRUCTURAL_PASS_BUDGET
    assert entry["structural_packet_cycles"] == [3, 4], entry
    assert entry["status"] == "ESCALATED", (
        "packet 2 has been dispatched but cycle 4 has not closed (D-058/D-114)"
    )

    # --- the boundary that CLOSES cycle 4 is what spends the budget (ST-002).
    _cross_boundary(fdir, project_root)          # closes 4, opens 5

    # --- cycle 5: one more LATENT instance, finer again. It changes nothing:
    #     CLEARED is terminal and a LATENT backlog blocks no gate.
    ledger.append(_row("D-005", status="open", fixed_in_cycle=None))
    _write_defects(fdir, ledger)

    entry = _escalation_entry(fdir)
    assert entry["status"] == "CLEARED", entry
    assert entry["exit_reason"] == "budget", entry
    assert entry["cleared_at_cycle"] == 5, entry
    assert entry["structural_packets_dispatched"] == STRUCTURAL_PASS_BUDGET, entry
    assert entry["exit_reason"] in ESCALATION_EXIT_REASONS
    assert entry["status"] in ESCALATION_STATUSES

    # A CLEARED class draws no structural packet, whatever its cycle history.
    assert _escalated_classes(fdir, project_root) == {}
    assert foundry_defects_to_tasks(project_root)["structural_tasks"] == 0

    # --- the run reaches NYQUIST: the only open instances are LATENT, and a
    #     never-reproduced backlog blocks nothing (FR-006).
    #
    # fallout ST-012 / GI-011 / GI-031 — AND THE DOOR NOW ASKS WHERE FROM.
    # `nyquist` carries a `_PHASE_ENTRY_SOURCES` row: F4 on a TEMPER-off run,
    # F5 with `--temper`. The crossings above leave the run at F2 mid-INSPECT,
    # which is a phase no real run gates NYQUIST from, so the state is moved to
    # the accepted one first. The subject here is the LATENT backlog and not
    # the entry rung, and driving the gate from a phase the door refuses would
    # have asserted `passed is True` about a refusal it never reached.
    (fdir / "verdicts.json").write_text(
        json.dumps({"requirements": []}), encoding="utf-8"
    )
    state = json.loads((fdir / "state.json").read_text(encoding="utf-8"))
    assert not state.get("temper"), state
    state["phase"] = "F4"
    (fdir / "state.json").write_text(json.dumps(state), encoding="utf-8")

    _arm(fdir)
    gate = foundry_gate("nyquist", project_root)
    assert gate["passed"] is True, gate
    entry_rung = next(
        c for c in gate["checklist"]
        if c["check"].startswith("entered_from_accepted_phase")
    )
    assert entry_rung["ok"] is True, entry_rung
    blocking = next(
        c for c in gate["checklist"] if c["check"].startswith("zero_blocking_defects")
    )
    assert blocking["ok"] is True
    assert sorted(blocking["latent_backlog"]) == ["D-004", "D-005"], blocking

    # --- ...and the report names what is being carried.
    from foundry_mcp.tools.foundry_report import generate_report

    assert generate_report(Path(project_root), fdir)["ok"] is True
    report = json.loads((fdir / "report.json").read_text(encoding="utf-8"))
    named = json.dumps(report["latent_backlog"]) + json.dumps(report["escalated_classes"])
    assert "D-004" in named and "D-005" in named
    assert "budget" in json.dumps(report["escalated_classes"])


def test_the_finer_boundary_fixture_is_the_scenario_and_not_the_answer(run_env):
    """D-115 stated as a guard, so the read-back cannot come back.

    The fixture's own `escalation.json` is never copied into the run directory by
    the test above — production writes that file, from the transitions the test
    drives. This asserts the two AGREE about the exit, which is the claim the
    fixture is entitled to make, without either standing in for the other.
    """
    fixture = Path(__file__).parent / "fixtures" / "escalation" / "finer_boundary_run"
    recorded = json.loads(
        (fixture / "escalation.json").read_text(encoding="utf-8")
    )["classes"]["FALSE_DOCUMENTED_CONTRACT"]

    assert recorded["exit_reason"] == "budget"
    assert recorded["structural_packet_cycles"] == [3, 4]
    assert recorded["cleared_at_cycle"] == 5
    assert recorded["open_latent_defect_ids"] == ["D-004", "D-005"]


def test_an_open_live_instance_of_a_cleared_class_still_blocks_and_is_packeted(run_env):
    """AC-003 verbatim: 'With one LIVE instance of a CLEARED class still open,
    Foundry-Gate done and nyquist refuse naming that defect, and Foundry-Tasks
    groups it into an ordinary per-instance packet.'

    THE EXIT IS NOT A WAIVER. Escalation only ever changed the SHAPE of the work
    — one structural packet instead of N per-instance ones — and clearing it puts
    the shape back. Every instance must still close.
    """
    project_root, fdir = run_env
    _escalate(fdir, project_root)
    for _ in range(3):
        _cross_boundary(fdir, project_root)
    assert _escalation_entry(fdir)["status"] == "CLEARED"

    (fdir / "verdicts.json").write_text(
        json.dumps({"requirements": []}), encoding="utf-8"
    )
    # nyquist additionally requires the run to have been started with --nyquist,
    # which is a different precondition and would claim `reason` first.
    state = json.loads((fdir / "state.json").read_text(encoding="utf-8"))
    state["nyquist"] = True
    (fdir / "state.json").write_text(json.dumps(state), encoding="utf-8")

    for phase in ("done", "nyquist"):
        _arm(fdir)
        gate = foundry_gate(phase, project_root)
        assert gate["passed"] is False, (phase, gate)
        blocking = next(
            c for c in gate["checklist"]
            if c["check"].startswith("zero_blocking_defects")
        )
        assert blocking["ok"] is False, (phase, gate)
        assert "D-001" in blocking["unknown_tier"] + blocking["live"], (phase, gate)

    tasks = foundry_defects_to_tasks(project_root)
    assert tasks["structural_tasks"] == 0
    assert all(not t["structural"] for t in tasks["tasks"])
    packeted = {d for t in tasks["tasks"] for d in t["defect_ids"]}
    assert {"D-001", "D-002", "D-003"} <= packeted


def test_the_escalation_record_carries_every_field_the_report_reads(run_env):
    """AC-004 verbatim: 'escalation.json records for the class a status, the exit
    reason (clean-cycles or budget), the cycle it cleared and the structural
    packets it consumed.'

    FR-028 leaves the shape to the implementer provided the exit reason is
    MACHINE-READABLE and appears in the report. So the reason is a member of a
    closed vocabulary rather than prose, and every field the F6 report reads is
    present on the record before the report is generated.
    """
    project_root, fdir = run_env
    _escalate(fdir, project_root)
    for _ in range(3):
        _cross_boundary(fdir, project_root)

    entry = _escalation_entry(fdir)
    assert entry["status"] in ESCALATION_STATUSES
    assert entry["exit_reason"] in ESCALATION_EXIT_REASONS
    assert isinstance(entry["cleared_at_cycle"], int)
    assert isinstance(entry["structural_packets_dispatched"], int)
    assert isinstance(entry["structural_packet_cycles"], list)
    assert isinstance(entry["live_clean_cycles"], int)
    assert isinstance(entry["open_latent_defect_ids"], list)
    # The pre-change fields survive: the record was extended, not replaced.
    assert entry["proposal"]
    assert entry["escalated_at_cycle"] == 3
    assert entry["defect_ids"]


def test_a_pre_change_escalation_record_reads_as_escalated_with_nothing_counted(run_env):
    """Archive compatibility, in the only direction that is safe.

    A record written before this landed has none of the new fields. Every default
    is chosen so it reads as "escalated, nothing has happened yet" — reading a
    missing `live_clean_cycles` as anything but zero would clear classes in an
    old archive that nothing ever measured.
    """
    project_root, fdir = run_env
    _write_defects(fdir, _recurring([1, 2, 3]))
    _write_state(fdir, phase="F2", cycle=3)
    (fdir / _escalation.ESCALATION_FILENAME).write_text(
        json.dumps({"classes": {"FALSE_DOCUMENTED_CONTRACT": {
            "proposal": "an older run recorded only this",
            "escalated_at_cycle": 3,
        }}}),
        encoding="utf-8",
    )

    assert set(_escalated_classes(fdir, project_root)) == {"FALSE_DOCUMENTED_CONTRACT"}

    foundry_defects_to_tasks(project_root)
    entry = _escalation_entry(fdir)
    assert entry["status"] == "ESCALATED"
    assert entry["live_clean_cycles"] == 0
    assert entry["exit_reason"] is None


def test_the_open_latent_backlog_is_refreshed_not_frozen(run_env):
    """FR-001: 'open LATENT instances go to the F6 named backlog.'

    Refreshed on every recording, because the backlog the report names has to be
    the CURRENT set of never-reproduced instances — not the set as of whenever
    the class first escalated.
    """
    project_root, fdir = run_env
    _escalate(fdir, project_root)
    assert _escalation_entry(fdir)["open_latent_defect_ids"] == []

    defects = json.loads((fdir / "defects.json").read_text(encoding="utf-8"))["defects"]
    defects.append(_defect("D-101", 4, **{
        "class": "FALSE_DOCUMENTED_CONTRACT", "tier": "LATENT",
        "reproduction_attempted": "drove every caller; none reaches the branch",
    }))
    _write_defects(fdir, defects)
    _write_state(fdir, phase="F2", cycle=4)
    foundry_defects_to_tasks(project_root)

    assert _escalation_entry(fdir)["open_latent_defect_ids"] == ["D-101"]


# --------------------------------------------------------------------------- #
# D-111 — an escalated class whose backlog is entirely LATENT must be able to
# LEAVE escalation. Both exit arms return immediately unless `escalation.json`
# holds a record for the class, and the only writer of that file was
# `_record_escalation_proposals`, reached only from `foundry_defects_to_tasks`.
# Nothing routes a LATENT-only class there: the DONE guard blocks on
# `_escalated_classes` (ledger recurrence, not tier-aware) while Foundry-Next's
# `transition_to_grind` branch — the only caller of `_escalation_notice` and the
# only branch naming Foundry-Tasks — is guarded by `open_count > 0`, which counts
# LIVE and untiered only. So the class escalated, blocked DONE, and had no
# reachable exit at all.
# --------------------------------------------------------------------------- #


def test_a_latent_only_class_acquires_its_record_at_the_inspect_boundary(run_env):
    """D-111: escalation.json is written at the boundary, not only by Foundry-Tasks.

    Driven exactly as filed: three LATENT instances of one class at server cycles
    1, 2 and 3 — `_escalated_classes` returns it, and `escalation.json` was
    ABSENT. The DONE gate refused "1 defect class(es) are still ESCALATED", and
    following that refusal's own hint six times walked the counter from 4 to 9
    with the file still absent and the class still escalated.
    """
    project_root, fdir = run_env
    _write_defects(fdir, _latent_recurring([1, 2, 3]))
    _write_state(fdir, phase="F3", cycle=3)

    assert "FALSE_DOCUMENTED_CONTRACT" in _escalated_classes(fdir, project_root)
    assert not (fdir / _escalation.ESCALATION_FILENAME).exists(), (
        "the fixture must start from the state D-111 reports: escalated, unrecorded"
    )

    _arm(fdir)
    result = foundry_mark_phase_complete("inspect_start", project_root)

    assert result["ok"] is True, result
    entry = _escalation_entry(fdir)
    assert entry["status"] == "ESCALATED"
    assert entry["escalated_at_cycle"] == 3
    assert entry["live_clean_cycles"] == 0
    assert entry["open_latent_defect_ids"] == ["D-001", "D-002", "D-003"]


def test_a_latent_only_class_clears_on_the_clean_arm_within_the_promised_bound(run_env):
    """D-111's real complaint: the block was UNBOUNDED.

    The D-034 lead ruling promises the escalation block is bounded at "at most
    two more INSPECT cycles or one structural packet". With no persisted record
    neither arm could fire, so it was bounded by nothing. With the record written
    at the boundary the promise holds: two further crossings and the class is
    CLEARED with a machine-readable exit reason (AC-004 / FR-028), and DONE stops
    naming it.
    """
    project_root, fdir = run_env
    _write_defects(fdir, _latent_recurring([1, 2, 3]))
    _write_state(fdir, phase="F3", cycle=3)

    _cross_boundary(fdir, project_root)   # closes 3, the escalation cycle
    _cross_boundary(fdir, project_root)   # closes 4 — clean
    assert _escalation_entry(fdir)["live_clean_cycles"] == 1
    _cross_boundary(fdir, project_root)   # closes 5 — two consecutive, out

    entry = _escalation_entry(fdir)
    assert entry["status"] == "CLEARED", entry
    assert entry["exit_reason"] == "clean_cycles", entry
    assert entry["cleared_at_cycle"] == 6, entry
    assert _escalated_classes(fdir, project_root) == {}

    # ...and the F6 door stops naming it, which is the outcome the six wasted
    # crossings in the filing were trying and failing to reach.
    outcome = _gates._done_preconditions(fdir, project_root)
    assert "FALSE_DOCUMENTED_CONTRACT" not in outcome["reason"], outcome["reason"]


def test_the_done_refusal_names_the_arm_and_the_cycles_remaining(run_env):
    """D-111's hint half: the refusal named a call that was a no-op in that state.

    The hint offered two routes — "cross the GRIND->INSPECT boundary so the
    clean-cycle arm counts, or spend the structural budget" — and for a class
    with no record BOTH were no-ops, while `escalation-override` (which does
    clear it, and leaves no record) was never mentioned. It now states the
    distance to each arm, per class, from the record the boundary writes.
    """
    project_root, fdir = run_env
    _write_defects(fdir, _latent_recurring([1, 2, 3]))
    _write_state(fdir, phase="F3", cycle=3)
    _cross_boundary(fdir, project_root)   # closes 3, records the class
    _cross_boundary(fdir, project_root)   # closes 4 — one clean cycle banked

    outcome = _gates._done_preconditions(fdir, project_root)

    assert outcome["passed"] is False
    hint = outcome["hint"]
    assert "FALSE_DOCUMENTED_CONTRACT: 1 more INSPECT crossing(s)" in hint, hint
    assert "2 more structural packet(s)" in hint, hint
    assert "inspect_start" in hint, hint


def _crossings_the_hint_promises(hint: str, klass: str) -> int:
    """The integer the DONE hint states for `klass`'s clean arm."""
    match = re.search(rf"{klass}: (\d+) more INSPECT crossing\(s\)", hint)
    assert match is not None, hint
    return int(match.group(1))


def test_the_promised_crossings_are_the_crossings_the_arm_actually_takes(run_env):
    """D-157 — the DONE hint's number, WALKED rather than re-asserted.

    ST-001 / FR-003 verbatim: 'Two consecutive INSPECT cycles with zero LIVE
    instances of the class', with 'the class must have been escalated before the
    two cycles began'. The distance the refusal prints and the distance the arm
    honours were derived two different ways, and in AC-002's own state they
    disagreed: escalated at cycle 5, counter at 5, every instance LATENT so
    `_class_drew_live_in_cycle(defects, key, 5)` is False — and the hint said
    two crossings while the arm needed three, because the crossing closing cycle
    5 is discarded by the escalated-before guard and banks nothing.

    The guard this replaces asserted the sentence's own arithmetic and never
    crossed a boundary, so a number the arm would not honour read as correct.
    This one reads N out of the sentence, crosses N-1 boundaries and requires
    the class STILL escalated, then crosses the Nth and requires it CLEARED —
    which fails on any disagreement in either direction.
    """
    project_root, fdir = run_env
    _write_defects(fdir, _latent_recurring([3, 4, 5]))
    _write_state(fdir, phase="F3", cycle=5)

    hint = _gates._done_preconditions(fdir, project_root)["hint"]
    promised = _crossings_the_hint_promises(hint, "FALSE_DOCUMENTED_CONTRACT")
    assert promised == 3, hint
    # The sentence says WHICH crossing banks nothing, so the count and the
    # guard cannot read as contradicting each other.
    assert "at or before the cycle this class escalated on (5)" in hint, hint

    for _ in range(promised - 1):
        _cross_boundary(fdir, project_root)
    assert _escalation_entry(fdir)["status"] == "ESCALATED", (
        "cleared EARLY: the arm banked a crossing the hint did not promise"
    )

    _cross_boundary(fdir, project_root)
    entry = _escalation_entry(fdir)
    assert entry["status"] == "CLEARED", (
        "still escalated after every promised crossing: the hint named a "
        "distance the arm does not honour"
    )
    assert entry["exit_reason"] == "clean_cycles", entry
    assert entry["live_clean_cycles_counted"] == [6, 7], entry


# --------------------------------------------------------------------------- #
# D-112 — "two CONSECUTIVE INSPECT cycles" (FR-003, Locked) is a test the clean
# arm did not make.
# --------------------------------------------------------------------------- #


def test_a_live_draw_between_two_clean_cycles_does_not_clear_the_class(run_env):
    """FR-003 verbatim: 'Two consecutive INSPECT cycles with zero LIVE instances
    of the class'. ST-001 repeats 'the second consecutive INSPECT cycle'.

    The no-regression half of D-112, stated on the path that already held: a
    LIVE draw the arm ACTUALLY EVALUATES has always reset the accumulator. What
    D-112 reports is the path where the arm is never reached at all, which
    `test_cycles_skipped_under_an_override_break_the_streak_too` drives — so
    this test exists to keep the working half working while that one closes the
    hole, and it must not start passing for the adjacency test's reason instead
    of its own.
    """
    project_root, fdir = run_env
    _escalate(fdir, project_root)          # LIVE at 1, 2, 3 -> escalated at 3
    _cross_boundary(fdir, project_root)    # closes 3
    _cross_boundary(fdir, project_root)    # closes 4 — clean, count 1

    defects = json.loads((fdir / "defects.json").read_text(encoding="utf-8"))["defects"]
    defects.append(_defect("D-050", 5, **{
        "class": "FALSE_DOCUMENTED_CONTRACT", "tier": "LIVE",
    }))
    _write_defects(fdir, defects)
    _cross_boundary(fdir, project_root)    # closes 5 — LIVE draw, streak ends
    assert _escalation_entry(fdir)["live_clean_cycles"] == 0

    _cross_boundary(fdir, project_root)    # closes 6 — clean, count 1 again
    entry = _escalation_entry(fdir)
    assert entry["status"] == "ESCALATED", (
        "one clean cycle either side of a LIVE draw is not two consecutive ones"
    )
    assert entry["live_clean_cycles"] == 1, entry


def test_cycles_skipped_under_an_override_break_the_streak_too(run_env):
    """D-112 as driven: the LIVE draws were never even EVALUATED.

    `_persisted_escalations` filters out an operator-overridden class, so every
    boundary crossed while an `escalation-override` directive is active is
    skipped entirely — the arm is never reached, so no LIVE-draw reset could
    fire and the accumulator survived untouched. Driven: escalated at 3, clean at
    4, then the override (the marker the server's OWN structural proposal prints)
    held across 5, 6 and 7, each drawing a LIVE instance. After
    Foundry-Clear-Directives the next crossing recorded CLEARED / clean_cycles /
    cleared_at 9 with live_clean_cycles_counted [4, 8] — a streak claimed across
    three cycles nothing judged, while six LIVE instances stood open.

    Adjacency is tested against `live_clean_cycles_counted`, which already
    records which cycles were evaluated.
    """
    project_root, fdir = run_env
    _escalate(fdir, project_root)
    _cross_boundary(fdir, project_root)    # closes 3
    _cross_boundary(fdir, project_root)    # closes 4 — clean, count 1
    assert _escalation_entry(fdir)["live_clean_cycles"] == 1

    foundry_inject_directive(
        _escalation._override_instruction("FALSE_DOCUMENTED_CONTRACT"),
        project_root=project_root,
    )
    defects = json.loads((fdir / "defects.json").read_text(encoding="utf-8"))["defects"]
    for i, cycle in enumerate((5, 6, 7), start=4):
        defects.append(_defect(f"D-{i:03d}", cycle, **{
            "class": "FALSE_DOCUMENTED_CONTRACT", "tier": "LIVE",
        }))
        _write_defects(fdir, defects)
        _cross_boundary(fdir, project_root)   # closes 5, 6, 7 — all skipped
    assert _escalation_entry(fdir)["live_clean_cycles"] == 1, (
        "an overridden class must not advance the count either"
    )

    _directives.foundry_clear_directives(project_root=project_root)
    _cross_boundary(fdir, project_root)    # closes 8 — clean, but NOT adjacent

    entry = _escalation_entry(fdir)
    assert entry["status"] == "ESCALATED", (
        "cycles 5-7 drew LIVE instances and were never evaluated; a streak "
        "cannot be claimed across them"
    )
    assert entry["live_clean_cycles"] == 1, entry
    assert entry["live_clean_cycles_counted"] == [8], entry


def test_the_counted_cycles_are_the_current_streak_and_match_its_length(run_env):
    """D-112's record invariant, which is what makes the count checkable.

    `live_clean_cycles_counted` is the streak's own evidence, so it holds the
    cycles of the CURRENT streak and nothing else — `live_clean_cycles ==
    len(live_clean_cycles_counted)` at every boundary. Under the old bare
    accumulator the list was a mixed audit log and the number did not match it,
    which is exactly why a non-consecutive pair could read as a streak of two.
    """
    project_root, fdir = run_env
    _escalate(fdir, project_root)
    for _ in range(2):
        _cross_boundary(fdir, project_root)
        entry = _escalation_entry(fdir)
        assert entry["live_clean_cycles"] == len(entry["live_clean_cycles_counted"]), entry


# --------------------------------------------------------------------------- #
# D-113 / D-114 — both exit arms evaluate ONLY on a transition that advanced the
# cycle counter, because both are stated in terms of a cycle having ENDED.
# --------------------------------------------------------------------------- #


def test_the_clean_arm_never_evaluates_a_cycle_still_in_flight(run_env):
    """D-113: `inspect_start` from F5.5 evaluated the OPEN cycle.

    The branch had no source-phase precondition, so entered from F4, F5 or F5.5
    it set phase F2, did NOT advance the counter, and called the exit arms with
    `completed_cycle` equal to the cycle still in flight. Driven: escalated at 3
    with live_clean_cycles 1 at F5.5 and cycle 6 open, `inspect_start` returned
    ok with the counter still 6 and stamped CLEARED / clean_cycles /
    cleared_at 6, counted [4, 5, 6]. TEMPER then filed a LIVE instance of the
    class INSIDE cycle 6, and because CLEARED is terminal the boundary that
    really closed cycle 6 could not undo it.

    This is precisely the call the DONE and nyquist_done refusal hints instruct
    the lead to make, so it was on the guided path.
    """
    project_root, fdir = run_env
    _escalate(fdir, project_root)
    _cross_boundary(fdir, project_root)   # closes 3
    _cross_boundary(fdir, project_root)   # closes 4 — one clean cycle banked
    assert _escalation_entry(fdir)["live_clean_cycles"] == 1

    state = json.loads((fdir / "state.json").read_text(encoding="utf-8"))
    state["phase"] = "F5.5"
    state["cycle"] = 6
    (fdir / "state.json").write_text(json.dumps(state), encoding="utf-8")
    _arm(fdir)

    result = foundry_mark_phase_complete("inspect_start", project_root)

    assert result.get("ok") is not True, result
    assert "F5.5" in result["error"], result
    entry = _escalation_entry(fdir)
    assert entry["status"] == "ESCALATED", entry
    assert entry["cleared_at_cycle"] is None, entry
    assert 6 not in entry["live_clean_cycles_counted"], entry
    assert _current_cycle(fdir) == 6


def test_the_budget_arm_never_retracts_a_packet_inside_its_own_cycle(run_env):
    """D-114, a REGRESSION of D-058 through the same unguarded door.

    The ST-002 lead ruling requires CLEARED-via-budget to be applied when the
    boundary CLOSES the cycle the budget-th packet was dispatched in. The guard
    is `completed_cycle >= packet_cycles[BUDGET - 1]`, satisfied by EQUALITY —
    and equality is exactly what every `inspect_start` that closes no cycle
    produces. Driven through the real doors: packet 1 at cycle 3, run walked to
    F5.5 at cycle 4, Foundry-Tasks at F5.5 emitted packet 2 in cycle 4, then
    `inspect_start` from F5.5 returned ok with the counter still 4 and stamped
    CLEARED / budget / cleared_at 4 — the packet retracted inside the cycle that
    emitted it, with no GRIND ever working it.
    """
    project_root, fdir = run_env
    _escalate(fdir, project_root)
    foundry_defects_to_tasks(project_root)          # packet 1, cycle 3
    _cross_boundary(fdir, project_root)             # closes 3, opens 4

    state = json.loads((fdir / "state.json").read_text(encoding="utf-8"))
    state["phase"] = "F5.5"
    (fdir / "state.json").write_text(json.dumps(state), encoding="utf-8")
    foundry_defects_to_tasks(project_root)          # packet 2, cycle 4
    entry = _escalation_entry(fdir)
    assert entry["structural_packets_dispatched"] == STRUCTURAL_PASS_BUDGET
    assert entry["structural_packet_cycles"] == [3, 4]

    _arm(fdir)
    result = foundry_mark_phase_complete("inspect_start", project_root)

    assert result.get("ok") is not True, result
    entry = _escalation_entry(fdir)
    assert entry["status"] == "ESCALATED", (
        "packet 2 was dispatched in cycle 4 and cycle 4 has not closed"
    )
    assert entry["cleared_at_cycle"] is None, entry
    assert _current_cycle(fdir) == 4

    # The HONEST crossing — which closes cycle 4 — is what spends the budget.
    _cross_boundary(fdir, project_root)
    entry = _escalation_entry(fdir)
    assert entry["status"] == "CLEARED", entry
    assert entry["exit_reason"] == "budget", entry
    assert entry["cleared_at_cycle"] == 5, entry


# --------------------------------------------------------------------------- #
# D-178 — THE TWO-SPEC ID CONVENTION IS PINNED, NOT MERELY DOCUMENTED.
#
# D-181, filed one cycle after D-178 closed in test_observations.py, is the same
# defect in this module. Cycle 11 corrected THIS file's header to state the
# convention — a bare id cites forge-specs/foundry-run-convergence/spec.md, a
# historical one is written `process-fixes AC-008` — and left the per-test
# docstrings unqualified, so the header documented a rule the file itself broke
# 156 times. Confirmed by citation resolution:
# `test_grind_to_inspect_increments_the_counter` quoted "AC-008 verbatim: 'After
# a GRIND->INSPECT transition, the server-side cycle counter has incremented
# without any caller-supplied value.'" — process-fixes AC-008 — while
# convergence AC-008 reads "With only LATENT defects open, Foundry-Gate assay,
# temper, nyquist and done pass...". A stream resolving that bare tag against
# the spec the header names judges the test against text it does not prove.
#
# So the convention is now MACHINE-CHECKED here, and the check is casting 2's,
# IMPORTED rather than copied. Two copies of one scan drift, and the drift is
# invisible until a stream trips over the half that was not updated — which is
# the shape of D-178 itself, one rung up. `_prose_blocks` keys its own exemption
# off the sentinel line above, so it exempts THIS block and this module's
# docstring for the same reason it exempts test_observations': both are ABOUT
# the collision and must be free to name both sides of it.
#
# WHAT THIS PIN CANNOT SEE, STATED SO NOBODY DISCOVERS IT THE HARD WAY.
# --------------------------------------------------------------------------
# `_CONVERGENCE_IDS` is an ID-level allow-list, so declaring an id there
# silences the scan for that id ACROSS THE WHOLE FILE. Three ids here are cited
# both ways — FR-006, ST-001 and ST-002 each appear as convergence citations in
# the escalation-exit sections and as process-fixes citations in the
# cycle-counter and class-identity sections — so their process-fixes uses are
# qualified by hand and CANNOT be held by this test. A scan that could hold them
# would have to demand a qualification on EVERY id, convergence ones included,
# which is a convention change across every module that cites one, not a change
# this file may make alone.
# --------------------------------------------------------------------------- #

#: The convergence-spec ids this module cites BARE, per its own docstring's
#: "a BARE id cites forge-specs/foundry-run-convergence/spec.md" rule. Every one
#: of them also resolves in the process-fixes spec with different text, so
#: membership is DECLARED here and never inferred. Kept honest from the other
#: side by `test_every_declared_convergence_id_is_actually_cited_here`: an entry
#: naming an id this file does not cite is an allow-list entry that waives a
#: check nothing needed, and it fails.
_CONVERGENCE_IDS = frozenset({
    "AC-001", "AC-002", "AC-003", "AC-004", "AC-016",
    "CT-001", "CT-002", "CT-008", "CT-014",
    "FR-001", "FR-002", "FR-003", "FR-006", "FR-023", "FR-028", "FR-051",
    "OT-012",
    "ST-001", "ST-002", "ST-005", "ST-010",
})  # 21 items


def _own_source() -> str:
    return Path(__file__).read_text(encoding="utf-8")


def _shared_prose_scan(monkeypatch):
    """casting 2's scan, imported — `(prose_blocks, unqualified_ids)`.

    Imported LAZILY, inside the function that needs it, exactly as this server's
    modules import their cross-casting seams: a module-top import of a sibling
    test module makes one unfinished file a collection error for both.

    `_CONVERGENCE_IDS` is the only file-specific datum the scan takes, and it is
    a module global there, so it is patched rather than passed. That is the
    whole of what "share the machinery, declare the data" means here: the
    qualification grammar — the `process-fixes ` prefix, the `A / B / C` chain
    whose head governs the tail, the wrap-collapse — has ONE implementation, and
    each module states which bare ids it owns.
    """
    from tests import test_observations as shared

    monkeypatch.setattr(shared, "_CONVERGENCE_IDS", _CONVERGENCE_IDS)
    return shared._prose_blocks, shared._unqualified_ids


def test_every_requirement_id_in_this_module_names_its_spec(monkeypatch) -> None:
    """D-181's root cause, refused structurally rather than re-tagged by hand.

    Every requirement id in this module's docstrings and comments is a
    `process-fixes` citation or a declared convergence id. Anything else is the
    unqualified tag that sent a stream to a requirement this file does not
    drive, and this fails naming it.
    """
    prose_blocks, unqualified_ids = _shared_prose_scan(monkeypatch)

    offenders: list[str] = []
    for lineno, text in prose_blocks(_own_source()):
        for offence in unqualified_ids(text):
            offenders.append(f"line {lineno}: {offence}")

    assert not offenders, (
        "unqualified requirement id(s) — D-178/D-181 again. Write "
        "'process-fixes AC-008' for the earlier spec, or add the id to "
        "_CONVERGENCE_IDS if it cites forge-specs/foundry-run-convergence:\n  "
        + "\n  ".join(offenders)
    )


def test_the_pin_reports_the_bare_tag_it_was_written_for(monkeypatch) -> None:
    """The pin's own fail-safe: a guard that cannot fail guards nothing.

    Driven over the exact prose D-181 was filed against, which must be reported,
    and over each legal form, which must not be. Without this the pin would go
    green on a file whose ids were all declared away, and nobody would know.
    """
    _, unqualified_ids = _shared_prose_scan(monkeypatch)

    # The docstring D-181 named, verbatim from this module before the fix.
    assert unqualified_ids(
        "AC-008 verbatim: 'After a GRIND->INSPECT transition, the server-side "
        "cycle counter has incremented without any caller-supplied value.'"
    )
    # The other tags the census found, one per carrier and per grammar shape.
    assert unqualified_ids("ST-003 CLEARED: once every defect of the class closes")
    assert unqualified_ids("AC-010 / FR-008 / ST-003 / OT-003 — one structural packet")
    assert unqualified_ids("NFR-002 / the house rule: never raise across MCP")

    # ...and the qualified forms stay silent, including across a `/` chain whose
    # head governs its tail and across a source line wrap.
    assert not unqualified_ids(
        "process-fixes AC-008 verbatim: 'After a GRIND->INSPECT transition, "
        "the server-side cycle counter has incremented without any "
        "caller-supplied value.'"
    )
    assert not unqualified_ids(
        "process-fixes AC-010 / FR-008 / ST-003 / OT-003 — one structural packet"
    )
    # A declared convergence id needs no prefix — that is the convention.
    assert not unqualified_ids("AC-004 verbatim: 'escalation.json records'")


def test_a_declared_convergence_id_is_unpinned_across_this_whole_file(
    monkeypatch,
) -> None:
    """The limitation, asserted rather than left to be discovered.

    `_CONVERGENCE_IDS` is keyed on the id alone, so declaring one waives the
    check for every occurrence of it in this module — including the
    process-fixes uses of the three dual-cited ids, which are therefore held by
    hand and by the census in the defect report, not by this test. Stated as a
    test so the next author reads it as a known boundary of the guard rather
    than as coverage they can lean on.
    """
    _, unqualified_ids = _shared_prose_scan(monkeypatch)

    for dual in ("FR-006", "ST-001", "ST-002"):
        assert dual in _CONVERGENCE_IDS, dual
        # Its process-fixes sense reads exactly as silent as its convergence
        # sense, which is the hole: neither is reported.
        assert not unqualified_ids(f"{dual} says CONSECUTIVE")
        assert not unqualified_ids(f"process-fixes {dual} says CONSECUTIVE")

    # A NON-dual process-fixes id is still reported, so the hole is bounded to
    # what is declared and has not swallowed the guard whole.
    assert unqualified_ids("ST-003 says CONSECUTIVE")


def test_every_declared_convergence_id_is_actually_cited_here() -> None:
    """The allow-list cannot grow into a blanket waiver.

    An entry for an id this module does not cite waives a check nothing needed,
    and the cheapest way to make the pin above go green is to keep adding
    entries. So every declared id must appear in this file's prose, and the
    check is the same regex the scan uses, read off the shared module rather
    than re-typed.
    """
    from tests.test_observations import _TWO_SPEC_ID_RE

    cited = set(_TWO_SPEC_ID_RE.findall(_own_source()))
    unused = sorted(_CONVERGENCE_IDS - cited)
    assert not unused, (
        f"_CONVERGENCE_IDS declares {unused}, which this module does not cite. "
        "Remove the entry rather than leaving a standing waiver."
    )


# --------------------------------------------------------------------------- #
# D-237 — an ESCALATED entry with no cycle stamp still reaches an exit
# --------------------------------------------------------------------------- #


def _seed_recorded_escalation(fdir: Path, entry: object, klass: str = "K") -> None:
    """Write escalation.json directly, with NO stamp, and an empty ledger.

    Hand-written on purpose, and it is the one fixture in this file that is: the
    state under test is a RECORD the current writers can no longer produce but
    that a resumed archive carries — a pre-change entry, an entry an operator
    repaired by hand, or one this run's own `_record_escalation_proposals`
    latched `escalated_at_cycle: null` into before D-237 was closed. The ledger
    is EMPTY because that is the state the defect was driven in: every instance
    of the class has been fixed, so nothing re-derives a stamp for it.
    """
    (fdir / "defects.json").write_text(
        json.dumps({"defects": []}, indent=2), encoding="utf-8"
    )
    (fdir / _escalation.ESCALATION_FILENAME).write_text(
        json.dumps({"classes": {klass: entry}}), encoding="utf-8"
    )


@pytest.mark.parametrize(
    "entry",
    [
        {"status": "ESCALATED"},
        {"status": "ESCALATED", "escalated_at_cycle": None},
        # D-212's shape: a non-mapping entry reads as ESCALATED rather than
        # vanishing, so it must reach an exit on the same terms.
        None,
    ],
)
def test_an_escalated_class_with_no_stamp_is_stamped_and_then_clears(run_env, entry):
    """ST-001 verbatim: 'the second consecutive INSPECT cycle in which the class
    draws zero LIVE instances ... COUNTED ON THE SERVER CYCLE STAMP; LATENT
    instances do not reset the count; THE CLASS MUST HAVE BEEN ESCALATED BEFORE
    THE TWO CYCLES BEGAN.' ST-002 / FR-001 / AC-004.

    D-237. `_escalation_entry_defaults` filled every C-3 key EXCEPT
    `escalated_at_cycle`, and BOTH exit arms read it. `_clean_arm_step` opens
    `if not isinstance(escalated_at, int) ... return False`, so an unstamped
    entry can never bank a clean cycle; the budget arm needs
    `structural_packet_cycles`, written only by `_spend_structural_budget`,
    which is fed `_escalated_classes` — and that opens `if not bucket["open"]:
    continue`, so a class with no open instances can never consume a packet
    either. Both arms sat still forever.

    Driven at HEAD across four full crossings, on each seeding below: still
    ESCALATED, `escalated_at_cycle` None, `live_clean_cycles` 0, and
    `Foundry-Gate('done')` refusing "1 defect class(es) are still ESCALATED: K"
    with a remedy sentence — "The next crossing records it" — that no crossing
    ever performed.

    THE STAMP IS CONSERVATIVE, and this asserts the arithmetic rather than only
    the outcome. The crossing stamps the cycle it has just CLOSED, and
    `_clean_arm_step` counts only cycles strictly after the stamp, so that
    crossing banks nothing: the next banks one and the one after clears. That is
    ST-001's "escalated before the two cycles began" honoured for a class whose
    real escalation cycle is unknowable, and it is exactly what the refusal's
    own remedy already promised.
    """
    project_root, fdir = run_env
    _write_state(fdir, phase="F2", cycle=4)
    _seed_recorded_escalation(fdir, entry)

    # Crossing one closes cycle 4 and STAMPS it. Nothing is banked.
    _cross_boundary(fdir, project_root)
    stamped = _escalation_entry(fdir, "K")
    assert stamped["escalated_at_cycle"] == 4, stamped
    assert stamped["status"] == "ESCALATED", stamped
    assert stamped["live_clean_cycles"] == 0, stamped

    # Crossing two closes cycle 5: the first cycle the guard admits.
    _cross_boundary(fdir, project_root)
    banked = _escalation_entry(fdir, "K")
    assert banked["live_clean_cycles"] == 1, banked
    assert banked["status"] == "ESCALATED", banked

    # Crossing three closes cycle 6 and CLEARS.
    _cross_boundary(fdir, project_root)
    cleared = _escalation_entry(fdir, "K")
    assert cleared["status"] == "CLEARED", cleared
    assert cleared["exit_reason"] == "clean_cycles", cleared
    assert isinstance(cleared["cleared_at_cycle"], int), cleared
    # AC-004: the record says which arm fired and on which cycle.
    assert cleared["escalated_at_cycle"] == 4, cleared


def test_the_done_refusal_stops_naming_a_class_that_can_now_exit(run_env):
    """AC-003 / ST-010 / FR-001. D-237's operator-facing half, and D-111's rule.

    "A refusal that names no reachable call is not a remedy." The DONE gate
    refused with "1 defect class(es) are still ESCALATED: K" and offered "The
    next crossing records it, and counting starts from the crossing after that"
    — a sentence describing a repair no crossing performed, so a lead following
    it moved nothing however many times they followed it. The refusal text is
    unchanged; what changed is that it is now TRUE.

    Asserted end to end at the real doors: refused before the crossings, and
    passing after exactly the number of crossings the sentence promises.
    """
    project_root, fdir = run_env
    _write_state(fdir, phase="F5.5", cycle=4, nyquist=True)
    _seed_recorded_escalation(fdir, {"status": "ESCALATED"})
    (fdir / "verdicts.json").write_text(
        json.dumps({"requirements": []}), encoding="utf-8"
    )

    def _escalation_check(result: dict) -> dict:
        """The gate's OWN named entry for the escalated-class guarantee.

        Read rather than `passed`, because this fixture is deliberately thin —
        no spec, no generated report — so the DONE gate has other reasons to
        refuse and asserting on the verdict would test those instead. The
        subject here is one check: does the escalated class still block, and
        does the refusal still name it.
        """
        return next(
            row for row in result["checklist"]
            if row["check"].startswith("escalated_classes_cleared")
        )

    _arm(fdir)
    before = foundry_gate("done", project_root)
    assert before["passed"] is False, before
    assert "K" in before["reason"], before
    assert "ESCALATED" in before["reason"], before
    assert _escalation_check(before)["ok"] is False, before
    # D-111's rule, and the sentence that was false: the remedy names the
    # crossing that records the stamp.
    assert "The next crossing records it" in (
        before["reason"] + before.get("hint", "")
    ), before

    for _ in range(3):
        _write_state(fdir, phase="F5.5", cycle=_current_cycle(fdir), nyquist=True)
        _cross_boundary(fdir, project_root)

    _write_state(fdir, phase="F5.5", cycle=_current_cycle(fdir), nyquist=True)
    _arm(fdir)
    after = foundry_gate("done", project_root)

    assert _escalation_check(after)["ok"] is True, after
    assert _escalation_check(after)["classes"] == [], after
    assert "still ESCALATED" not in after["reason"], after
    assert _escalation_entry(fdir, "K")["exit_reason"] == "clean_cycles"


def test_a_real_escalation_cycle_is_never_overwritten_by_the_repair(run_env):
    """D-001's latch, preserved. `escalated_at_cycle` is a LATCH: once an int is
    recorded it is never moved, because `_consecutive_run` recomputes the
    current run end from the ledger on every call and a bare assignment would
    re-date the escalation to the newest filing — which is what let a class
    escalated at cycle 3 walk its marker to 4, 5, 6 while `live_clean_cycles`
    stayed 0 and the finer-boundary loop could not converge.

    D-237 moved that latch INTO `_escalation_entry_defaults`, so this is the
    property that must survive the move: the repair writes only over a value
    that is not an int, and a class the ledger can date keeps its own date
    through every writer that touches the record.
    """
    project_root, fdir = run_env
    _escalate(fdir, project_root)
    original = _escalation_entry(fdir)["escalated_at_cycle"]
    assert isinstance(original, int), original

    for _ in range(2):
        _cross_boundary(fdir, project_root)
        assert _escalation_entry(fdir)["escalated_at_cycle"] == original
    # ...and through Foundry-Tasks, the other writer of the key.
    foundry_defects_to_tasks(project_root)
    assert _escalation_entry(fdir)["escalated_at_cycle"] == original


@pytest.mark.parametrize("offered", [None, "7", True, 2.5])
def test_only_a_real_integer_stamps_an_unstamped_entry(offered):
    """The normaliser in isolation. C-3 types `escalated_at_cycle` as int|null,
    and a bool is not an int here for the same reason it is not one anywhere
    else in this module — `isinstance(True, int)` is True in Python, and a
    stamp of `True` would compare as cycle 1 and silently admit history.

    An entry with nothing to stamp it still comes back carrying the KEY, so a
    reader sees "not yet known" rather than "not yet written"; every arm already
    treats `None` as unstamped.
    """
    entry = _escalation._escalation_entry_defaults(
        {"status": "ESCALATED"}, escalated_at_cycle=offered
    )
    assert "escalated_at_cycle" in entry
    assert entry["escalated_at_cycle"] is None, entry

    # A real int stamps, and then a second offer never moves it.
    stamped = _escalation._escalation_entry_defaults(
        {"status": "ESCALATED"}, escalated_at_cycle=9
    )
    assert stamped["escalated_at_cycle"] == 9
    assert _escalation._escalation_entry_defaults(
        stamped, escalated_at_cycle=11
    )["escalated_at_cycle"] == 9
