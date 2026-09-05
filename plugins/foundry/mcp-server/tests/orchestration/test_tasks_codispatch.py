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
    _annotate_co_dispatch,
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




def test_the_alignment_block_is_named_by_a_surface_that_reaches_the_prompt(run_env):
    """fallout FR-038 / GI-021 / CT-008 / AC-002 — D-040: computed, published,
    consumed by nothing.

    fallout FR-038 ends "the lead pastes it verbatim into the dispatch
    prompt", and a
    grep over src/, tests/, commands/ and agents/ found exactly three
    references to the block: its definition, the one assignment inside
    `foundry_defects_to_tasks`, and one test assertion. No consumer, and no
    instruction anywhere naming it — so a correctly computed block reached a
    dispatch prompt only if the lead had happened to read the JSON and
    remembered which field to copy.

    The two surfaces that hand a lead its GRIND dispatch now name it: the
    `transition_to_grind` imperative, which is the sequence the lead follows
    literally and already the way `grind_cycle_context` and
    `progress_protocol` reach a teammate, and the Foundry-Tasks result the
    block itself arrives on. Both quote one constant, so a reword of either
    cannot leave them saying different things.
    """
    from foundry_mcp.tools.orchestration.directives import (
        ALIGNMENT_APPEND_INSTRUCTION,
    )
    from foundry_mcp.tools.orchestration.guidance import _ACTION_IMPERATIVES

    project_root, fdir = run_env
    _write_state(fdir, phase="F3", cycle=1)
    _manifest_with_requirement_ids(fdir, {
        3: (["FR-007"], ["src/three.py"]),
        5: (["FR-007"], ["src/five.py"]),
    })
    _defect_ledger(fdir, [
        dict(_tiered("D-900", "LIVE"), file="src/three.py", spec_ref="FR-007"),
    ])

    result = foundry_defects_to_tasks(project_root)
    assert result["ok"] is True, result
    # The response that carries the block says what to do with it.
    assert result["alignment_instructions"] == ALIGNMENT_APPEND_INSTRUCTION
    assert "VERBATIM" in ALIGNMENT_APPEND_INSTRUCTION

    task = next(t for t in result["tasks"] if "D-900" in t["defect_ids"])
    # The owning casting is published beside the co-dispatch set, so a consumer
    # can tell whose block this is without parsing the rendered prose.
    assert task["owning_casting"] == 3, task
    assert task["co_dispatch"] == [5], task

    # ...and the sequence the lead follows names the block as its own step.
    grind = _ACTION_IMPERATIVES["transition_to_grind"]
    assert "`alignment_block`" in grind, grind
    assert "VERBATIM" in grind
    # Named in the ORDER, so a lead following the append list reaches it.
    assert "defects → alignment" in grind, grind


def test_asking_for_the_alignment_block_records_no_dispatch(run_env):
    """The adjacent path the extraction exists to keep safe.

    `_annotate_co_dispatch` computes the co-dispatch set, the owning casting
    and the block, and writes NOTHING; `_append_grind_dispatch` stays at the
    door that dispatches. Two writers of one `grind_dispatched` record would
    make `Foundry-Team-Down` refuse over a defect nobody dispatched twice, and
    the annotation is the half a reader wants.
    """
    project_root, fdir = run_env
    _write_state(fdir, phase="F3", cycle=1)
    _manifest_with_requirement_ids(fdir, {3: (["FR-007"], ["src/three.py"])})
    _defect_ledger(fdir, [
        dict(_tiered("D-900", "LIVE"), file="src/three.py", spec_ref="FR-007"),
    ])

    tasks = [{
        "structural": False,
        "defect_ids": ["D-900"],
        "description": "d",
        "files": ["src/three.py"],
        "symbols": [],
        "spec_refs": ["FR-007"],
        "regression": False,
        "source": "prove",
    }]
    assert _annotate_co_dispatch(fdir, tasks) is True
    assert tasks[0]["alignment_block"], tasks[0]
    assert tasks[0]["owning_casting"] == 3, tasks[0]
    assert not (fdir / "handoffs.jsonl").exists(), (
        "annotating a task appended a dispatch handoff record"
    )




def test_a_concern_naming_a_casting_no_requirement_reaches_joins_the_set(run_env):
    """fallout FR-012 / GI-023 / ST-003 / AC-004 (D-054) — the OTHER half.

    fallout FR-012 is two clauses and only the refusal one shipped:
    "Foundry-Tasks includes the named casting" was implemented as "Foundry-Tasks
    NOTICES a concern whose target the requirement-ownership join already
    reached". The difference is invisible whenever the concern happens to name a
    casting that owns one of the defect's requirement ids, and total whenever it
    does not — which is the case fallout ST-003 exists for, a sibling surface no
    requirement id connects.

    DRIVEN AS THE PAIR. The synthetic manifest below gives casting 3 a
    DIFFERENT requirement id from the one the only open defect cites, so the
    ownership join reaches castings 1 and 2 and never 3. The concern names
    casting 3's file. Before this fix the set was `[2]`, `concerns_dispatched`
    was empty and the concern stayed open, so `inspect_start` kept refusing with
    the Tasks-driven exit unreachable and only the lead's hand close left —
    fallout AC-004's OTHER exit standing in for both.
    """
    from foundry_mcp.tools.concerns import foundry_concern

    project_root, fdir = run_env
    _manifest_with_requirement_ids(fdir, {
        1: (["FR-007"], ["src/one.py"]),
        2: (["FR-007"], ["src/two.py"]),
        3: (["FR-008"], ["src/three.py"]),
    })
    _write_state(fdir, phase="F3", cycle=1)
    _defect_ledger(fdir, [
        dict(_tiered("D-900", "LIVE"), file="src/one.py", spec_ref="FR-007"),
    ])
    opened = foundry_concern(
        casting_id=1, cycle=1, target="src/three.py",
        text="the ruling I applied is also stated in casting 3's own prose",
        project_root=project_root,
    )
    assert opened.get("error") is None, opened
    concern_id = opened["concern"]["id"]

    result = foundry_defects_to_tasks(project_root)
    assert result["ok"] is True, result
    task = next(t for t in result["tasks"] if "D-900" in t["defect_ids"])

    # Casting 2 is here because it OWNS the requirement the defect cites;
    # casting 3 is here because the concern NAMES it. Both are dispatched; only
    # one of them was before.
    assert task["co_dispatch"] == [2, 3], task
    assert task["concerns_co_dispatched"] == [concern_id], task
    assert result["concerns_dispatched"] == [concern_id], result

    # The block the lead pastes says WHICH reason each casting is here for. A
    # concern-driven member listed under "the SAME requirement is owned by"
    # would be telling the lead something untrue about why it was dispatched.
    block = task["alignment_block"]
    assert "- casting 2: src/two.py" in block, block
    assert "Cross-casting concern(s) carried by this dispatch" in block, block
    assert concern_id in block, block
    assert "src/three.py" in block, block
    assert "casting 3's own prose" in block, block
    assert block.index("- casting 2:") < block.index(concern_id), block

    # ...and the door the concern was holding shut now opens.
    from foundry_mcp.tools.concerns import open_concerns_for_other_castings

    assert open_concerns_for_other_castings(fdir) == [], "the concern is still open"




def test_a_concern_targeting_the_casting_that_owns_the_fix_is_already_dispatched(run_env):
    """fallout FR-012 / ST-003 (D-054) — the owner counts as reached.

    `co_dispatch` excludes the owning casting by construction, so a join that
    read only `co_dispatch` would leave a concern targeting the very file the
    fix lands in open forever, with nothing left to dispatch that could close
    it. The task IS the dispatch of that casting.
    """
    from foundry_mcp.tools.concerns import foundry_concern

    project_root, fdir = run_env
    _manifest_with_requirement_ids(fdir, {
        1: (["FR-007"], ["src/one.py"]),
        2: (["FR-007"], ["src/two.py"]),
    })
    _write_state(fdir, phase="F3", cycle=1)
    _defect_ledger(fdir, [
        dict(_tiered("D-900", "LIVE"), file="src/one.py", spec_ref="FR-007"),
    ])
    # Named BY CASTING ID, not by file: a file target already matched the
    # task's own `files`, so only an id target drives the reached-set arm this
    # test is for.
    opened = foundry_concern(
        casting_id=2, cycle=1, target="1",
        text="casting 1's file states the same rule",
        project_root=project_root,
    )
    concern_id = opened["concern"]["id"]

    result = foundry_defects_to_tasks(project_root)
    task = next(t for t in result["tasks"] if "D-900" in t["defect_ids"])
    assert task["owning_casting"] == 1, task
    # NOT added to co_dispatch — it is the owner, and a set that named its own
    # owner would have the lead dispatch one casting twice.
    assert 1 not in task["co_dispatch"], task
    assert result["concerns_dispatched"] == [concern_id], result
