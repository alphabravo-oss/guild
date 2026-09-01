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
