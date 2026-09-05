"""Foundry-Tasks: the co-dispatch set, the alignment block and the concern door.

Carved from `tests/test_orchestrator_gates.py` (fallout FR-005 / GI-026 /
AC-014 / OT-016): one test module per shipped orchestration module, landed in
the same casting as the source move so no pin is ever left pointing at a module
that no longer exists.
"""
from __future__ import annotations

from pathlib import Path


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
    _defect_ledger,
    _manifest_with_requirement_ids,
    _tiered,
    _write_manifest_with_castings,
    _write_state,
    run_env,
)

from foundry_mcp.tools.orchestration.directives import (  # noqa: F401
    foundry_defects_to_tasks,
    foundry_inject_directive,
)












def test_foundry_tasks_names_every_casting_that_owns_the_defects_requirement(run_env):
    """fallout FR-011 / FR-038 / GI-021 / CT-008 / AC-002 / OT-002.

    A defect cites a requirement, the requirement is owned by more than one
    casting, and the fix lands in one of them. The others carry the same rule
    spelled the old way until a later cycle files the same finding against them
    — a cycle spent re-discovering something the run already knew.
    """
    project_root, fdir = run_env
    _manifest_with_requirement_ids(fdir, {
        3: (["FR-007", "FR-009"], ["src/three.py"]),
        5: (["FR-007"], ["src/five.py"]),
        7: (["FR-009"], ["src/seven.py"]),
    })
    _write_state(fdir, phase="F3", cycle=1)
    _defect_ledger(fdir, [
        dict(_tiered("D-900", "LIVE"), file="src/three.py",
             spec_ref="FR-007"),
    ])

    result = foundry_defects_to_tasks(project_root)
    assert result["ok"] is True, result
    assert result["co_dispatch_computable"] is True, result
    task = next(t for t in result["tasks"] if "D-900" in t["defect_ids"])
    assert task["co_dispatch"] == [5], task
    block = task["alignment_block"]
    for fragment in ("D-900", "FR-007", "casting 3", "src/three.py",
                     "casting 5", "src/five.py"):
        assert fragment in block, (fragment, block)
    # Casting 7 owns a DIFFERENT requirement and is not dragged in.
    assert "src/seven.py" not in block, block






def test_a_manifest_without_requirement_ids_reports_not_computable(run_env):
    """fallout AC-006 / OT-006 — never an empty set.

    "No casting owns this requirement" and "nobody recorded who owns anything"
    are opposite facts, and a lead reading the first when the second is true
    dispatches one casting for a rule that lives in four.
    """
    project_root, fdir = run_env
    _write_manifest_with_castings(fdir, ["src/three.py"], no_ui=True)
    _write_state(fdir, phase="F3", cycle=1)
    _defect_ledger(fdir, [dict(_tiered("D-900", "LIVE"), file="src/three.py")])

    result = foundry_defects_to_tasks(project_root)
    assert result["co_dispatch_computable"] is False, result
    task = next(t for t in result["tasks"] if "D-900" in t["defect_ids"])
    assert task["co_dispatch"] is None, task
    assert "not computable" in task["co_dispatch_problem"], task






def test_a_directive_naming_a_requirement_prints_the_castings_that_own_it(run_env):
    """fallout FR-011 / GI-021 / CT-009 / AC-003 / OT-003.

    The union of the owners of every id in the text, found with the ONE
    requirement-id grammar and never a second regex here. A directive naming no
    id prints an empty set and is otherwise unchanged — most directives are
    instructions, not citations, and that case must stay free.
    """
    project_root, fdir = run_env
    _manifest_with_requirement_ids(fdir, {
        3: (["FR-007"], ["src/three.py"]),
        5: (["FR-007", "CT-004"], ["src/five.py"]),
        7: (["CT-004"], ["src/seven.py"]),
    })

    both = foundry_inject_directive(
        "Re-read FR-007 and CT-004 before the next wave.",
        project_root=project_root,
    )
    assert both["ok"] is True, both
    assert both["co_dispatch"] == [3, 5, 7], both
    assert both["requirement_ids"] == ["CT-004", "FR-007"] or set(
        both["requirement_ids"]
    ) == {"CT-004", "FR-007"}, both

    plain = foundry_inject_directive("Slow down and read the diff.",
                                        project_root=project_root)
    assert plain["ok"] is True, plain
    assert plain["co_dispatch"] == [], plain
    assert plain["requirement_ids"] == [], plain
