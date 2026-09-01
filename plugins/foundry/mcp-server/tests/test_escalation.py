"""Server-side cycle counter and recurring-class escalation.

FR-005 / FR-006 / FR-007 / FR-008 / FR-024, ST-001 / ST-002 / ST-003,
AC-008 / AC-009 / AC-010 / AC-011, OT-003.

NFR-001 makes the synthetic three-cycle escalation fixture part of what this
casting delivers rather than an afterthought: "the spec's acceptance is
structural (each P-item's own acceptance test passes, e.g. ... escalation fires
on a synthetic 3-cycle fixture)".

The baseline being fixed: grand-vulture ran FALSE_DOCUMENTED_CONTRACT for eight
consecutive cycles (9-16), 42 defects, because ``foundry_defects_to_tasks``
groups by LOCATION rather than by cause — one systemic class spread over 11
files became 11 unrelated packets, each fixed per-instance, the class itself
never addressed. The three-cycle rule fires at cycle 11 on that history.

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

from foundry_mcp.tools import foundry_orchestrator as fo
from foundry_mcp.tools import foundry_state
from foundry_mcp.tools.foundry_orchestrator import (
    ESCALATION_CYCLES,
    _current_cycle,
    _defect_class,
    _escalated_classes,
    foundry_defects_to_tasks,
    foundry_gate,
    foundry_inject_directive,
    foundry_mark_phase_complete,
)


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

    monkeypatch.setattr(
        fo,
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


def _arm(fdir: Path) -> None:
    """Foundry-Next's ordering token, which gate/phase calls consume."""
    (fdir / ".next-action-called").write_text(f"{fo._now()}\n", encoding="utf-8")


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


def _recurring(cycles: list[int], klass: str = "FALSE_DOCUMENTED_CONTRACT") -> list[dict]:
    """One new defect of the same declared class in each named cycle."""
    return [
        _defect(f"D-{i:03d}", cycle, **{"class": klass})
        for i, cycle in enumerate(cycles, start=1)
    ]


# --------------------------------------------------------------------------- #
# AC-008 / ST-001 / FR-005 — the server-owned cycle counter
# --------------------------------------------------------------------------- #


def test_grind_to_inspect_increments_the_counter(run_env):
    """AC-008 verbatim: 'After a GRIND->INSPECT transition, the server-side
    cycle counter has incremented without any caller-supplied value.'"""
    project_root, fdir = run_env
    _write_state(fdir, phase="F3", cycle=0)
    _arm(fdir)

    result = foundry_mark_phase_complete("inspect_start", project_root)

    assert result["ok"] is True, result
    assert result["phase"] == "F2"
    assert result["cycle"] == 1
    assert _current_cycle(fdir) == 1


def test_the_boundary_handler_takes_no_cycle_argument(run_env):
    """ST-001: 'caller-supplied cycle is not trusted where the server knows
    better'. The handler's signature is the proof — there is no cycle argument
    to supply, so the increment cannot be steered from outside."""
    import inspect

    params = inspect.signature(foundry_mark_phase_complete).parameters
    assert set(params) == {"phase", "project_root"}

    project_root, fdir = run_env
    _write_state(fdir, phase="F3", cycle=7)
    _arm(fdir)

    assert foundry_mark_phase_complete("inspect_start", project_root)["cycle"] == 8


def test_repeated_grind_inspect_loops_advance_one_cycle_each(run_env):
    """The counter tracks GRIND cycles, one per loop — which is what makes
    'three consecutive cycles' a real measurement."""
    project_root, fdir = run_env
    _write_state(fdir, phase="F2", cycle=0)

    for expected in (1, 2, 3):
        _arm(fdir)
        foundry_mark_phase_complete("grind_start", project_root)
        _arm(fdir)
        result = foundry_mark_phase_complete("inspect_start", project_root)
        assert result["cycle"] == expected, result


def test_entering_inspect_from_cast_does_not_advance_the_cycle(run_env):
    """Only F3 -> F2 advances. The run's FIRST INSPECT arrives from F1 CAST and
    is cycle 0, not cycle 1 — counting it would put every run one ahead and
    make the first GRIND loop look like the second."""
    project_root, fdir = run_env
    _write_state(fdir, phase="F1", cycle=0)
    _arm(fdir)

    result = foundry_mark_phase_complete("inspect_start", project_root)

    assert result["phase"] == "F2"
    assert result["cycle"] == 0
    assert _current_cycle(fdir) == 0


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
# AC-009 / ST-002 / FR-006 — three consecutive cycles fire, two do not
# --------------------------------------------------------------------------- #


def test_escalation_fires_on_the_third_consecutive_cycle(run_env):
    """AC-009 / OT-003: the synthetic 3-cycle fixture. The same class filed in
    three consecutive server-counted cycles escalates."""
    project_root, fdir = run_env
    _write_state(fdir, phase="F2", cycle=2)
    _write_defects(fdir, _recurring([0, 1, 2]))

    escalated = _escalated_classes(fdir, project_root)

    assert list(escalated) == ["FALSE_DOCUMENTED_CONTRACT"]
    assert escalated["FALSE_DOCUMENTED_CONTRACT"]["consecutive_cycles"] == 3
    assert escalated["FALSE_DOCUMENTED_CONTRACT"]["escalated_at_cycle"] == 2


def test_two_consecutive_cycles_do_not_fire_escalation(run_env):
    """AC-009's negative half — the one that keeps N=3 meaningful."""
    project_root, fdir = run_env
    _write_state(fdir, phase="F2", cycle=1)
    _write_defects(fdir, _recurring([0, 1]))

    assert _escalated_classes(fdir, project_root) == {}


def test_non_consecutive_cycles_do_not_fire_escalation(run_env):
    """FR-006 says CONSECUTIVE. A class that appears in cycles 0, 1 and 3 broke
    its run — cycle 2 is evidence a fix held, however briefly."""
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
    """ST-003 CLEARED: once every defect of the class closes, the class stops
    producing a structural packet. Escalation describes work outstanding."""
    project_root, fdir = run_env
    _write_state(fdir, phase="F2", cycle=2)
    defects = _recurring([0, 1, 2])
    for d in defects:
        d["status"] = "fixed"
        d["fixed_in_cycle"] = 2
    _write_defects(fdir, defects)

    assert _escalated_classes(fdir, project_root) == {}


# --------------------------------------------------------------------------- #
# FR-007 / FR-024 / ST-002 (A-033) — class identity
# --------------------------------------------------------------------------- #


def test_a_stream_declared_class_is_the_class(run_env):
    """FR-007: the optional stream-declared ``class`` field wins outright — it
    is the assayer's systemic_patterns[] output finally being consumed."""
    assert _defect_class({"class": "FALSE_DOCUMENTED_CONTRACT", "type": "WRONG",
                          "file": "a/b/c.py"}) == "FALSE_DOCUMENTED_CONTRACT"
    # Whitespace-only is not a declaration.
    assert _defect_class({"class": "   ", "type": "WRONG", "file": "a/b/c.py"}) != "   "


def test_fallback_clusters_on_type_plus_file_cluster(run_env):
    """FR-024 (implementer-tunable, chosen rule documented at the constant):
    the fallback is the canonical defect type joined with the first
    FALLBACK_CLUSTER_DEPTH path segments. Same type + same subsystem is one
    class; a different subsystem is a different class."""
    assert fo.FALLBACK_CLUSTER_DEPTH == 2

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
    """ST-002 / A-033: 'Escalation keys on stream-declared class when present,
    and on the fallback clustering when absent — either can accumulate.'"""
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
# AC-010 / FR-008 / ST-003 / OT-003 — one structural packet per escalated class
# --------------------------------------------------------------------------- #


def test_escalated_class_produces_exactly_one_structural_task(run_env):
    """AC-010 / OT-003: 'Foundry-Tasks emits exactly one structural-fix task
    for that class'. These three defects live in three different files, so the
    old location grouping produced three unrelated packets."""
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
    """AC-010: 'packets for all other defects are unaffected'. Escalation
    changes the shape of ONE class's work and nothing else."""
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
    """FR-008 / ST-003: 'one structural packet with a recorded proposal'. It
    states the evidence that made this systemic and that closure is not
    waived."""
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
    """ST-003 says the proposal is RECORDED on the class's packet. Holding it
    only in the returned dict would lose it the moment the lead moved on."""
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
# AC-010 — the explicit directive override
# --------------------------------------------------------------------------- #


def test_a_scoped_directive_override_restores_per_instance_packets(run_env):
    """AC-010: 'an explicit directive override restores per-instance
    packets'."""
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
# AC-011 — escalation never waives closure
# --------------------------------------------------------------------------- #


def test_done_gate_refuses_while_an_escalated_class_has_open_defects(run_env):
    """AC-011 verbatim: 'the run cannot reach DONE while any escalated-class
    defect remains open'. Stated as its own named check so the guarantee is
    visible rather than merely implied by the open-defect count."""
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
    escalation_check = next(k for k in checks if k.startswith("escalated_classes_closed"))
    assert checks[escalation_check]["ok"] is False
    assert checks[escalation_check]["classes"] == ["FALSE_DOCUMENTED_CONTRACT"]


def test_done_gate_escalation_check_passes_once_the_class_closes(run_env):
    """ST-003 CLEARED: the structural fix still has to close every instance.
    When it does, the check clears — closure is the only exit."""
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
    escalation_check = next(k for k in checks if k.startswith("escalated_classes_closed"))
    assert checks[escalation_check]["ok"] is True
    assert checks[escalation_check]["classes"] == []


def test_the_done_transition_itself_refuses_an_open_escalated_class(run_env):
    """D-037 — AC-011 constrains the RUN, so the TRANSITION must enforce it.

    ``foundry_mark_phase_complete("done")`` was an unconditional
    ``_update_phase(F6)`` + ``clear_active_run()``: it read no verdicts, no open
    defects and no escalated classes. Every check above lived in
    ``foundry_gate("done")``, which is advisory — a lead that simply did not
    call it archived the run. Driven before the fix: DONE reached with six open
    escalated-class defects and zero verdicts, which is precisely what AC-011
    says cannot happen.
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
    escalation_check = next(k for k in checks if k.startswith("escalated_classes_closed"))
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

    # Every defect of the escalated class closed (ST-003 CLEARED)...
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

    _arm(fdir)
    gate = foundry_gate("done", project_root)
    assert gate["passed"] is True, gate

    _arm(fdir)
    result = foundry_mark_phase_complete("done", project_root)

    assert result["ok"] is True, result
    assert result["phase"] == "F6"
    assert json.loads((fdir / "state.json").read_text(encoding="utf-8"))["phase"] == "F6"


# --------------------------------------------------------------------------- #
# AC-011 — F6 has TWO doors and both are locked (D-043 / D-044)
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
# AC-011's words are "the RUN cannot reach DONE while any escalated-class
# defect remains open", with no exception for how F6 is entered.
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
    escalation_check = next(k for k in checks if k.startswith("escalated_classes_closed"))
    assert checks[escalation_check]["ok"] is False
    assert checks[escalation_check]["classes"] == ["FALSE_DOCUMENTED_CONTRACT"]


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
    assert nyquist_done["checklist"] == done["checklist"]
    shared_reason = (
        "1 escalated defect class(es) still have open instances: "
        "FALSE_DOCUMENTED_CONTRACT"
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
    assert "3 open defect(s) remain" in result["reason"]


# --------------------------------------------------------------------------- #
# The lead is told before it dispatches the wave
# --------------------------------------------------------------------------- #


def test_next_action_surfaces_the_escalation_at_f2(run_env):
    """The lead has to know a class escalated BEFORE it dispatches GRIND,
    because the packet shape it is about to hand out changed."""
    project_root, fdir = run_env
    _write_state(fdir, phase="F2", cycle=2)
    _write_defects(fdir, _recurring([0, 1, 2]))
    for s in ("trace", "prove", "test"):
        (fdir / f".{s}-complete").write_text("items_checked=1\nfindings=0\n", encoding="utf-8")

    action = fo._compute_next_action(project_root)

    assert action["action"] == "transition_to_grind"
    assert "ESCALATED" in action["instructions"]
    assert "FALSE_DOCUMENTED_CONTRACT" in action["instructions"]
    assert "FALSE_DOCUMENTED_CONTRACT" in action["details"]["escalation"]


def test_next_action_reads_normally_when_nothing_is_escalated(run_env):
    """The notice is empty on an ordinary cycle — no noise added to the
    common path."""
    project_root, fdir = run_env
    _write_state(fdir, phase="F2", cycle=1)
    _write_defects(fdir, [_defect("D-001", 1)])
    for s in ("trace", "prove", "test"):
        (fdir / f".{s}-complete").write_text("items_checked=1\nfindings=0\n", encoding="utf-8")

    action = fo._compute_next_action(project_root)

    assert "ESCALATED" not in action["instructions"]
    assert action["details"]["escalation"] == {}


def test_escalation_threshold_constant_is_three(run_env):
    """FR-006 / A-012 fixes N at 3. Pinned so a later edit to the constant has
    to be a deliberate spec change, not a silent retune."""
    assert ESCALATION_CYCLES == 3


# --------------------------------------------------------------------------- #
# D-101 — the escalation override is a marker grammar, not a substring
#
# TV-B-03: `if ESCALATION_OVERRIDE_TOKEN not in text.lower()` followed by
# `return scoped or {"*"}` meant ANY mention of the token de-escalated EVERY
# class. A directive that FORBADE the override therefore disabled escalation
# wholesale — semantics exactly inverted from operator intent, with no signal.
# ST-003 makes the override an EXPLICIT directive action, and a substring match
# is not explicit.
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

    assert fo._escalation_overrides(project_root) == set()


@pytest.mark.parametrize("directive", _OVERRIDE_REQUESTS_ALL)
def test_the_bare_marker_grammar_overrides_every_class(run_env, directive):
    project_root, fdir = run_env
    foundry_inject_directive(directive, "normal", project_root)

    assert fo._escalation_overrides(project_root) == {"*"}


def test_the_scoped_marker_grammar_overrides_exactly_that_class(run_env):
    project_root, fdir = run_env
    foundry_inject_directive("escalation-override: FALSE_DOCUMENTED_CONTRACT", "normal", project_root)

    assert fo._escalation_overrides(project_root) == {"FALSE_DOCUMENTED_CONTRACT"}


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
    remove the capability AC-010 requires."""
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
# escalation in silence — ST-002 says EITHER path accumulates the count, and a
# mixed cluster accumulated in neither.
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
    # it (AC-011), so it must appear on the structural packet.
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

    resolved = fo._resolve_defect_classes(
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
    """FR-024 / ST-002: the fallback path is untouched by the absorption rule."""
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
# counter. foundry.py#_server_cycle returned None and _stamp_cycle then fell
# back to the CALLER's cycle; foundry_orchestrator.py#_current_cycle returned 0
# on the same input. _stamp_cycle's docstring claims "Every writer goes through
# this, so 'which cycle was this?' has one answer per run" — it had two.
#
# The harm is not cosmetic. Identical run, identical findings, identical class,
# caller cycles 1/2/3, only the DOOR differs: Foundry-Defect stamped [1,2,3]
# and escalated and DONE refused, while Foundry-Sync stamped [0] and neither
# fired. Mixed filing persisted [1,0,3] — longest consecutive run 2 — so a
# genuine systemic class evaded escalation and the AC-011 guard never fired.
#
# LEAD INTERFACE RULING (cross-casting, casting 3 owns the foundry.py half):
# on a malformed/unusable counter BOTH doors resolve the stamped cycle to 0,
# matching _current_cycle's documented "every reader gets a usable integer"
# and ST-001's "the caller's cycle is never trusted for stamping"; AND both
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
    fo.foundry_sync_defects(
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
    from foundry_mcp.tools.foundry import foundry_add_defect

    foundry_add_defect(
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


# The parity assertions below are a JOINT pin: the batch door is casting 2's
# and the single door is casting 3's (foundry.py#_stamp_cycle / #_server_cycle,
# being brought to this same contract in this cycle). Red here means one half
# has not landed yet, and the messages name which — never misread these as a
# regression in the orchestrator.
_OWNER = (
    "casting 3 owns foundry.py#_stamp_cycle: on a malformed counter it must "
    "resolve to 0 (not fall back to the caller's value) and persist "
    "declared_cycle on the record"
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
    """ST-001 on the path that is entirely this casting's: the server counter
    wins, and the caller's 7 survives only as the audit field."""
    project_root, fdir = run_env
    (fdir / "state.json").write_text(json.dumps({"phase": "F3", "cycle": 4}), encoding="utf-8")

    record = _stamped_via_sync(project_root, CALLER_CYCLE)
    assert record["cycle"] == 4
    assert record["declared_cycle"] == CALLER_CYCLE


def test_a_healthy_counter_is_stamped_by_both_doors_unchanged(run_env):
    """NFR-002: the ruling changes the MALFORMED path only. A real counter is
    still the authority at both doors, and the caller's 7 is still ignored."""
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
    class evaded escalation while the AC-011 DONE guard passed."""
    project_root, fdir = run_env

    for cycle, door in ((1, _stamped_via_add), (2, _stamped_via_sync), (3, _stamped_via_add)):
        (fdir / "state.json").write_text(
            json.dumps({"phase": "F3", "cycle": cycle}), encoding="utf-8"
        )
        door(project_root, cycle)

    # A malformed counter must not let one door drift off the others.
    stamps = [d["cycle"] for d in json.loads((fdir / "defects.json").read_text())["defects"]]
    assert stamps == [1, 2, 3], stamps

    escalated = _escalated_classes(fdir, project_root)
    assert set(escalated) == {"UNWIRED@src/auth"}, escalated
    assert escalated["UNWIRED@src/auth"]["consecutive_cycles"] == 3


def _overrides_prefix(text: str) -> set[str]:
    """The PRE-fix override reader, verbatim: a substring test then a fallback.

    Kept so the evidence log can show the same phrasings judged by both rules
    side by side, rather than asserting the new behaviour against nothing.
    """
    if fo.ESCALATION_OVERRIDE_TOKEN not in text.lower():
        return set()
    scoped = {
        m.group(1).strip(" .,;:'\"`")
        for m in re.finditer(
            rf"{fo.ESCALATION_OVERRIDE_TOKEN}\s*[:=]\s*(\S+)", text, re.IGNORECASE
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
            return fo._escalation_overrides(str(root))
        finally:
            foundry_state.clear_active_run()

    def fmt(value: set[str]) -> str:
        if value == {"*"}:
            return "ALL"
        return ",".join(sorted(value)) if value else "none"

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
