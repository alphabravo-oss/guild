"""Foundry-Tasks: the co-dispatch set, the alignment block and the concern door.

Carved from `tests/test_orchestrator_gates.py` (fallout FR-005 / GI-026 /
AC-014 / OT-016): one test module per shipped orchestration module, landed in
the same casting as the source move so no pin is ever left pointing at a module
that no longer exists.
"""
from __future__ import annotations

import json

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
    # fallout FR-009 (D-170) — the ownership resolver, called directly: a drive
    # through Foundry-Tasks would assert the FIELD and leave the two-pass
    # exact-beats-prefix ordering unexercised.
    _owning_casting,
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


# --------------------------------------------------------------------------- #
# fallout FR-012 / GI-023 / ST-003 / AC-004 (D-068) — THE REFUSAL'S NAMED EXIT,
# REACHABLE FROM THE STATE THE REFUSAL FIRES IN.
#
# The class this closes is `refusal-hint-names-an-exit-the-check-never-reads`,
# filed in three consecutive cycles. So the pin below is not "the concern gets
# dispatched": it is that the exit `_inspect_start_preconditions` NAMES can be
# taken by a lead standing in the state that refusal describes — a GRIND that
# fixed everything it was handed, which is the only state a lead calls
# `inspect_start` from.
# --------------------------------------------------------------------------- #


def _clean_grind_with_one_open_concern(project_root, fdir):
    """A GRIND that fixed every defect it was handed, one concern still open."""
    from foundry_mcp.tools.concerns import foundry_concern

    _manifest_with_requirement_ids(fdir, {
        2: (["FR-007"], ["src/two.py"]),
        5: (["FR-008"], ["src/five.py"]),
    })
    _write_state(fdir, phase="F3", cycle=2)
    # FIXED, not open. This is what a clean GRIND's ledger looks like.
    _defect_ledger(fdir, [
        dict(_tiered("D-900", "LIVE"), file="src/two.py", spec_ref="FR-007",
             status="fixed"),
    ])
    opened = foundry_concern(
        casting_id=2, cycle=2, target="5",
        text="casting 5's file states the same ruling and was not updated",
        project_root=project_root,
    )
    assert opened.get("error") is None, opened
    return opened["concern"]["id"]


def test_a_clean_grind_can_still_dispatch_an_open_cross_casting_concern(run_env):
    """fallout FR-012 / GI-023 / ST-003 / AC-004 (D-068).

    `foundry_defects_to_tasks` returned early on `not open_defects`, ABOVE the
    concern read, `_annotate_co_dispatch` and `_dispatch_open_concerns`. A GRIND
    that fixed every defect it was handed leaves zero open defects, which is
    exactly the state a lead calls `inspect_start` from — so the exit named in
    that refusal's own hint was unreachable in the only state it fires in, and
    the run could cross only by closing the concern by hand.
    """
    from foundry_mcp.tools.concerns import open_concerns_for_other_castings

    project_root, fdir = run_env
    concern_id = _clean_grind_with_one_open_concern(project_root, fdir)
    assert len(open_concerns_for_other_castings(fdir)) == 1

    result = foundry_defects_to_tasks(project_root)

    assert result["ok"] is True, result
    assert result["concerns_dispatched"] == [concern_id], result
    assert open_concerns_for_other_castings(fdir) == [], "the concern stayed open"


def test_the_clean_grind_dispatch_is_a_packet_and_not_a_cleared_flag(run_env):
    """fallout GI-023 / ST-003 (D-068) — "Foundry-Tasks includes the named
    casting", so there is something to dispatch TO it.

    Marking the concern dispatched with `tasks: []` would clear the refusal and
    send nobody to casting 5, which is the half of fallout FR-012 the refusal
    exists to enforce. The concern becomes the packet: the target's own files as the work,
    the concern text as the description, and a block naming both.
    """
    project_root, fdir = run_env
    concern_id = _clean_grind_with_one_open_concern(project_root, fdir)

    result = foundry_defects_to_tasks(project_root)

    assert result["count"] == 1, result
    task = result["tasks"][0]
    assert task["concern_only"] is True, task
    assert task["concern_id"] == concern_id, task
    assert task["defect_ids"] == [], task
    assert task["co_dispatch"] == [5], task
    assert task["files"] == ["src/five.py"], task
    assert "same ruling" in task["description"], task["description"]

    block = task["alignment_block"]
    # The header does NOT claim a fix whose owner could not be resolved.
    assert "Owning casting: not resolvable" not in block, block
    assert "carries a cross-casting concern and no defect fix" in block, block
    assert "Raised by casting 2." in block, block
    assert "Cross-casting concern(s) carried by this dispatch" in block, block
    assert concern_id in block and "src/five.py" in block, block


def test_the_nothing_to_do_result_carries_the_shape_every_other_call_does(run_env):
    """fallout CT-008 / AC-006 (D-068) — the early return dropped four fields.

    `co_dispatch_computable`, `concerns_dispatched`, `structural_tasks` and
    `alignment_instructions` were absent from the nothing-to-do response
    entirely, so a lead reading it was told nothing about either the co-dispatch
    join or the concern ledger — on the one call where "nothing to do" is the
    answer it most needs to be able to trust.
    """
    project_root, fdir = run_env
    _manifest_with_requirement_ids(fdir, {2: (["FR-007"], ["src/two.py"])})
    _write_state(fdir, phase="F3", cycle=2)
    _defect_ledger(fdir, [])

    result = foundry_defects_to_tasks(project_root)

    assert result["ok"] is True and result["count"] == 0, result
    assert result["tasks"] == [], result
    for field in (
        "co_dispatch_computable", "concerns_dispatched", "structural_tasks",
        "alignment_instructions", "escalated_classes",
    ):
        assert field in result, (field, sorted(result))
    assert result["concerns_dispatched"] == [], result


def test_a_wave_with_defect_tasks_emits_no_duplicate_concern_packet(run_env):
    """fallout D-068 — the packet is for a concern nothing else can carry.

    `_concern_carriers` falls back to every task with a computed set, so on a
    wave WITH defect tasks each open concern already rides one. A second,
    concern-only packet would dispatch the same casting twice for one concern.
    """
    from foundry_mcp.tools.concerns import foundry_concern

    project_root, fdir = run_env
    _manifest_with_requirement_ids(fdir, {
        1: (["FR-007"], ["src/one.py"]),
        3: (["FR-008"], ["src/three.py"]),
    })
    _write_state(fdir, phase="F3", cycle=1)
    _defect_ledger(fdir, [
        dict(_tiered("D-900", "LIVE"), file="src/one.py", spec_ref="FR-007"),
    ])
    foundry_concern(
        casting_id=1, cycle=1, target="3",
        text="casting 3 states the same rule", project_root=project_root,
    )

    result = foundry_defects_to_tasks(project_root)
    assert [t.get("concern_only") for t in result["tasks"]] == [None], result["tasks"]
    assert result["count"] == 1, result
    assert result["tasks"][0]["co_dispatch"] == [3], result["tasks"][0]


def test_the_inspect_start_refusal_and_the_tasks_exit_are_driven_as_a_pair(run_env):
    """fallout FR-012 / GI-023 / ST-005 / AC-004 (D-068) — THE MECHANISM, not
    the instance.

    The class is "the refusal hint names an exit the check never reads", so what
    is asserted is the round trip: the refusal fires, its hint names
    Foundry-Tasks, that call is made in the state the refusal left the run in,
    and the same call then succeeds. Anything less pins one half of a contract
    whose halves were shipped apart three cycles running.
    """
    from foundry_mcp.tools.orchestration.transitions import (
        _inspect_start_preconditions,
    )

    project_root, fdir = run_env
    concern_id = _clean_grind_with_one_open_concern(project_root, fdir)

    refused = _inspect_start_preconditions(fdir, project_root)
    assert refused["passed"] is False, refused
    concern_rungs = [
        r for r in refused["refusals"] if concern_id in r.get("reason", "")
    ]
    assert concern_rungs, refused["refusals"]
    # The hint names Foundry-Tasks as an exit...
    hint = " ".join(r.get("hint", "") for r in concern_rungs)
    assert "Foundry-Tasks" in hint, hint

    # ...and taking it, from this exact state, clears this exact rung.
    assert foundry_defects_to_tasks(project_root)["concerns_dispatched"] == [
        concern_id
    ]

    after = _inspect_start_preconditions(fdir, project_root)
    assert not [
        r for r in after["refusals"] if concern_id in r.get("reason", "")
    ], after["refusals"]


# --------------------------------------------------------------------------- #
# fallout CT-008 / AC-025 / FR-053 (D-069) — THE OTHER HALF OF THE REPORT'S
# CO-DISPATCH SECTION.
# --------------------------------------------------------------------------- #


def _grind_dispatch_records(fdir):
    from foundry_mcp.tools.foundry_state import read_jsonl

    records, problem = read_jsonl(fdir / "handoffs.jsonl")
    assert problem is None, problem
    return [r for r in records if r.get("event") == "grind_dispatched"]


def test_the_dispatch_record_carries_the_co_dispatch_set_the_report_reads(run_env):
    """fallout CT-008 / AC-025 / FR-053 (D-069).

    `foundry_report.py#_halt_and_co_dispatch_section` reads `co_dispatch` off
    each handoff record and SKIPS every record without the key; its docstring
    said the section stays empty "until casting 2 lands the writer". The writer
    landed carrying `{handoff_id, timestamp, event, defect_id, file, cycle,
    casting}` and none of the four keys the reader wants, so the section could
    not populate on any run — this run's own ledger holds 70 such records and
    zero with the key, across two GRIND cycles that both dispatched computed
    sets.
    """
    project_root, fdir = run_env
    _manifest_with_requirement_ids(fdir, {
        3: (["FR-007"], ["src/three.py"]),
        5: (["FR-007"], ["src/five.py"]),
    })
    _write_state(fdir, phase="F3", cycle=1)
    _defect_ledger(fdir, [
        dict(_tiered("D-900", "LIVE"), file="src/three.py", spec_ref="FR-007"),
    ])

    result = foundry_defects_to_tasks(project_root)
    task = next(t for t in result["tasks"] if "D-900" in t["defect_ids"])
    assert task["co_dispatch"] == [5], task

    records = _grind_dispatch_records(fdir)
    assert len(records) == 1, records
    record = records[0]
    # The COMPUTED set, not a recomputation: the same list the task carries.
    assert record["co_dispatch"] == [5], record
    assert record["defect_ids"] == ["D-900"], record
    assert record["requirement_ids"] == ["FR-007"], record
    assert record["phase"] == "F3", record
    # ...and the fields Team-Down reads are untouched.
    assert record["defect_id"] == "D-900" and record["file"] == "src/three.py"
    assert record["cycle"] == 1 and record["casting"] == 3, record


def test_the_report_section_populates_from_a_real_dispatch(run_env):
    """fallout CT-008 (D-069) — the two sides, driven end to end.

    `tests/test_report.py` covers this section by hand-appending a synthetic
    record in a shape no shipped writer produced, which is how a one-sided
    contract passes its own test. This drives the real writer and then the real
    reader over the ledger it wrote.
    """
    from foundry_mcp.tools.foundry_report import _halt_and_co_dispatch_section

    project_root, fdir = run_env
    _manifest_with_requirement_ids(fdir, {
        3: (["FR-007"], ["src/three.py"]),
        5: (["FR-007"], ["src/five.py"]),
    })
    _write_state(fdir, phase="F3", cycle=1)
    _defect_ledger(fdir, [
        dict(_tiered("D-900", "LIVE"), file="src/three.py", spec_ref="FR-007"),
    ])
    foundry_defects_to_tasks(project_root)

    section, problem = _halt_and_co_dispatch_section(fdir, {"phase": "F3"})
    assert problem is None, problem
    assert section["co_dispatch_count"] == 1, section
    row = section["co_dispatch"][0]
    assert row["co_dispatch"] == [5], row
    assert row["defect_ids"] == ["D-900"], row
    assert row["requirement_ids"] == ["FR-007"], row
    assert row["cycle"] == 1 and row["phase"] == "F3", row




def test_a_concern_record_with_no_id_does_not_raise_out_of_the_door(run_env):
    """fallout GI-004 (D-151) — a reachable raise is a blocking defect.

    `foundry_defects_to_tasks`' dispatch loop indexed `c["id"]` on every concern
    record while the module's two sibling concern walks both read
    `concern.get("id", "?")` over the same records — so a `concerns.json`
    holding one id-less record raised `KeyError: 'id'` across the MCP boundary
    as call_tool's unhandled-error banner rather than the house refusal.

    fallout GI-004 carries A-000's sentence without qualification: "A reachable raise
    ... REMAINS A BLOCKING DEFECT AT FULL WEIGHT." No writer in the plugin emits
    this shape, so reaching it needs a hand-edited, migrated or
    partially-written ledger — which is the population `migrate-archive.py` and
    the resume path operate on.

    THE MALFORMED RECORD IS SKIPPED, NOT DEFAULTED TO "?" as the render sites
    do: this loop MARKS rather than prints, and marking "?" dispatched would ask
    the ledger to close a concern by an id no record carries. So the well-formed
    concern beside it must still be dispatched, which is the half that says the
    guard tolerates rather than bails.
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
        text="the well-formed concern beside the malformed one",
        project_root=project_root,
    )
    concern_id = opened["concern"]["id"]

    # The shape no writer emits: a record with no `id` key at all, alongside a
    # real one. Written directly, because the door refuses to create it.
    ledger = json.loads((fdir / "concerns.json").read_text(encoding="utf-8"))
    ledger["concerns"].insert(0, {
        "cycle": 1, "source_casting": 1, "target": "src/three.py",
        "target_casting_id": 3, "text": "hand-edited: no id", "status": "open",
    })
    (fdir / "concerns.json").write_text(json.dumps(ledger), encoding="utf-8")

    result = foundry_defects_to_tasks(project_root)

    # No raise, and the house shape rather than an error banner.
    assert result["ok"] is True, result
    # The well-formed concern is still dispatched: the malformed record is
    # skipped, not treated as a bail-out.
    assert result["concerns_dispatched"] == [concern_id], result


# --------------------------------------------------------------------------- #
# fallout FR-009 (D-170) — A DIRECTORY key_file OWNS WHAT IS INSIDE IT.
# --------------------------------------------------------------------------- #


def _directory_manifest(fdir: Path) -> None:
    """This run's own shape: two castings, one of them holding directories."""
    (fdir / "castings").mkdir(parents=True, exist_ok=True)
    (fdir / "castings" / "manifest.json").write_text(json.dumps({"castings": [
        {"id": 2, "requirement_ids": ["FR-009"], "key_files": [
            "plugins/foundry/mcp-server/src/foundry_mcp/tools/orchestration/",
            "plugins/foundry/mcp-server/tests/orchestration/",
            "plugins/foundry/mcp-server/src/foundry_mcp/server.py",
        ]},
        {"id": 7, "requirement_ids": ["FR-009"], "key_files": [
            "plugins/foundry/mcp-server/src/foundry_mcp/tools/artifacts.py",
        ]},
    ]}), encoding="utf-8")


def test_a_file_inside_a_directory_key_file_resolves_to_its_casting(run_env):
    """fallout FR-009 (D-170) — the resolver matched by set membership, so a
    directory entry matched nothing beneath it, ever.

    `_owning_casting` read `if any(f in key_files for f in files)` — a
    membership test over literal strings — while `key_files` entries are either
    a file path OR a directory spelled with a trailing slash. DRIVEN:
    `Foundry-Tasks` on cycle 5 of this run generated 15 tasks and returned
    `owning_casting: None` for 13 of them; resolving the same files by directory
    prefix against castings/manifest.json showed SEVEN belonged to casting 2,
    whose key_files are exactly the two directory entries below.

    The failure is silent in the worst direction: a lead dispatching per casting
    from this field leaves those defects with no owner, no refusal and no
    warning, and the next INSPECT re-files them looking like fixes that did not
    take. Directory entries are deliberate — F0.9 recorded them as the way to
    fit a new package under the cast gate's 8-key_files cap, and F0.9 VALIDATE
    accepted the manifest — so the two halves of the system disagreed about what
    a key_file may be.

    The self-application is the last row: `directives.py` is itself inside
    `tools/orchestration/`, so the resolver returned None for the record naming
    its own fault.
    """
    project_root, fdir = run_env
    _directory_manifest(fdir)

    for path, owner in (
        ("plugins/foundry/mcp-server/src/foundry_mcp/tools/orchestration/streams.py", 2),
        ("plugins/foundry/mcp-server/tests/orchestration/test_transitions.py", 2),
        # The exact entry beside the directories still resolves, unchanged.
        ("plugins/foundry/mcp-server/src/foundry_mcp/server.py", 2),
        ("plugins/foundry/mcp-server/src/foundry_mcp/tools/artifacts.py", 7),
        # A file no casting declares is still unowned — the fix widens the
        # match, it does not make every path resolve to somebody.
        ("plugins/foundry/mcp-server/src/foundry_mcp/tools/test_deriver.py", None),
        # ...and the prefix is a SEGMENT boundary, not a string prefix: a
        # sibling directory whose name merely starts the same way is not owned.
        ("plugins/foundry/mcp-server/src/foundry_mcp/tools/orchestrationXX/a.py", None),
        ("plugins/foundry/mcp-server/src/foundry_mcp/tools/orchestration/directives.py", 2),
    ):
        assert _owning_casting(fdir, [path]) == owner, (path, _owning_casting(fdir, [path]))


def test_the_narrower_claim_wins_when_both_a_directory_and_its_file_are_declared(run_env):
    """fallout FR-009 (D-170) — exact beats prefix, in manifest order or not.

    A directory entry and a file inside it may both be declared, by one casting
    or by two, and "whichever casting the manifest lists first" is an arbitrary
    tiebreak for a field a lead DISPATCHES from. The narrower claim wins: a
    casting naming the file owns it more specifically than one naming its
    directory.

    Driven in the order that would get it wrong — the directory owner is listed
    FIRST, so a single-pass resolver returns it.
    """
    project_root, fdir = run_env
    (fdir / "castings" / "manifest.json").write_text(json.dumps({"castings": [
        {"id": 2, "requirement_ids": ["FR-009"], "key_files": ["src/pkg/"]},
        {"id": 9, "requirement_ids": ["FR-009"], "key_files": ["src/pkg/one.py"]},
    ]}), encoding="utf-8")

    assert _owning_casting(fdir, ["src/pkg/one.py"]) == 9
    assert _owning_casting(fdir, ["src/pkg/two.py"]) == 2
