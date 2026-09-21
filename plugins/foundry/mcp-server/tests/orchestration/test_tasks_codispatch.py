"""Foundry-Tasks: the co-dispatch set, the alignment block and the concern door.

Carved from `tests/test_orchestrator_gates.py` (fallout FR-005 / GI-026 /
AC-014 / OT-016): one test module per shipped orchestration module, landed in
the same casting as the source move so no pin is ever left pointing at a module
that no longer exists.
"""
from __future__ import annotations

import json

from pathlib import Path



# fallout FR-004 / AC-013 — THE MODULE OBJECTS, UNDER UNDERSCORE ALIASES.
#
# `streams`, `spend`, `width`, `gates`, `directives` and `teams` are all LOCAL
# variable names somewhere in this suite, and a local rebinding shadows a
# module for the rest of its function. The aliases are what `ORCHESTRATION`,
# `owning_module` and every `monkeypatch.setattr` resolve through; individual
# SYMBOLS are imported by name below, which is how the carved modules read.

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
    # D-008 — the two manifest maps, read directly: a drive through
    # Foundry-Tasks asserts the FIELDS and would pass over a map that dropped
    # a casting silently, since a dropped casting and an unowned file are the
    # same empty answer one layer up.
    _casting_files,
    _casting_requirement_ids,
    # D-075 — the requirement join, called directly beside the drive through
    # Foundry-Tasks: the payload asserts the ORDER the join produced, and this
    # asserts that the join is what can produce it at all on a mixed manifest.
    _co_dispatch_for,
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


def test_the_block_names_the_sibling_files_that_cite_the_ids(run_env):
    """fallout FR-038 / CT-008 — "THAT CITE THOSE IDS" IS THE HALF THAT WAS
    PROSE (D-217).

    fallout FR-038 ends "the sibling files this casting owns THAT CITE THOSE
    IDS", fallout CT-008 says the same, and `guidance.py`'s imperative repeats
    it to the lead verbatim — three surfaces promising a narrowing the renderer
    never performed: it named `_casting_files(fdir)[cid]`, the whole `key_files`
    list, which the manifest records with no per-file requirement mapping at
    all.

    DRIVEN at ac89f59 with exactly this manifest: the block rendered
    `- casting 1: src/cites.py, src/also_cites.py, src/unrelated.py,
    docs/unrelated.md` — both unrelated files named. The block is pasted
    VERBATIM into a dispatch prompt, so over-naming is a teammate sent to files
    the rule does not live in with nothing else to go on.

    THE CITATION IS THE SOURCE OF TRUTH because it is the one that exists: this
    package's own convention, pinned by `tests/test_spec_id_convention.py`, is
    that a rule carries its ids in the file that implements it.
    """
    project_root, fdir = run_env
    root = Path(project_root)
    (root / "src").mkdir(parents=True, exist_ok=True)
    (root / "docs").mkdir(parents=True, exist_ok=True)
    (root / "src" / "cites.py").write_text(
        "# fallout FR-007 — the rule lives here\n", encoding="utf-8"
    )
    (root / "src" / "also_cites.py").write_text(
        '"""FR-007 is carried here too."""\n', encoding="utf-8"
    )
    (root / "src" / "unrelated.py").write_text(
        "# " + "N" + "FR-007 and FR-0071 are different ids and must not match\n",
        encoding="utf-8",
    )
    (root / "docs" / "unrelated.md").write_text("nothing at all\n", encoding="utf-8")
    (root / "src" / "three.py").write_text("# fallout FR-007\n", encoding="utf-8")

    _manifest_with_requirement_ids(fdir, {
        3: (["FR-007"], ["src/three.py"]),
        1: (["FR-007"], [
            "src/cites.py", "src/also_cites.py", "src/unrelated.py",
            "docs/unrelated.md",
        ]),
    })
    _write_state(fdir, phase="F3", cycle=1)
    _defect_ledger(fdir, [
        dict(_tiered("D-001", "LIVE"), file="src/three.py", spec_ref="FR-007"),
    ])

    result = foundry_defects_to_tasks(project_root)
    task = next(t for t in result["tasks"] if "D-001" in t["defect_ids"])
    block = task["alignment_block"]
    assert task["co_dispatch"] == [1], task

    # The two that carry the rule are named...
    assert "src/cites.py" in block, block
    assert "src/also_cites.py" in block, block
    # ...and the two that do not are not. The unrelated file's own text carries
    # the two near-misses the whole-token match must refuse — a longer prefix
    # ending in the same two letters, and a longer number — so a substring
    # reading would drag it back in and a boundary-free one would too.
    assert "src/unrelated.py" not in block, block
    assert "docs/unrelated.md" not in block, block


def test_a_directory_key_file_is_expanded_to_the_files_that_cite(run_env):
    """fallout FR-038 / CT-008 / FR-009 (D-217) — the SELF-APPLICATION.

    This run's own manifest names `tools/orchestration/` and
    `tests/orchestration/` as single `key_files` entries, because the cast gate
    caps a casting at eight and a casting carving a whole package fits under it
    by naming the package once. Answering with the ENTRY would name a
    thirteen-module package for a rule that lives in one module — the same
    over-naming one level up, and the shape that made D-170 answer
    `owning_casting: None` for thirteen of fifteen tasks.
    """
    project_root, fdir = run_env
    root = Path(project_root)
    pkg = root / "pkg" / "orchestration"
    pkg.mkdir(parents=True, exist_ok=True)
    (pkg / "carries.py").write_text("# fallout FR-050 — here\n", encoding="utf-8")
    (pkg / "silent.py").write_text("# nothing to declare\n", encoding="utf-8")
    (root / "src").mkdir(parents=True, exist_ok=True)
    (root / "src" / "owner.py").write_text("# fallout FR-050\n", encoding="utf-8")

    _manifest_with_requirement_ids(fdir, {
        2: (["FR-050"], ["src/owner.py"]),
        6: (["FR-050"], ["pkg/orchestration/"]),
    })
    _write_state(fdir, phase="F3", cycle=1)
    _defect_ledger(fdir, [
        dict(_tiered("D-002", "LIVE"), file="src/owner.py", spec_ref="FR-050"),
    ])

    result = foundry_defects_to_tasks(project_root)
    task = next(t for t in result["tasks"] if "D-002" in t["defect_ids"])
    block = task["alignment_block"]
    assert task["co_dispatch"] == [6], task
    assert "pkg/orchestration/carries.py" in block, block
    assert "silent.py" not in block, block


def test_a_casting_that_cites_no_id_is_named_as_such_and_not_dropped(run_env):
    """fallout FR-038 / CT-008 (D-217) — the fallback, and why it is NAMED.

    Under-naming is the worse of the two failures. The manifest records this
    casting as an OWNER of the requirement, so a block that listed nothing for
    it would tell the lead the co-dispatch was empty when what is actually true
    is that the rule is carried without a written cite. The whole `key_files`
    list follows, with the line saying which of the two answers it is — so a
    teammate reading the block verbatim can tell "these files carry the rule"
    from "nobody wrote the id down; find the surface".
    """
    project_root, fdir = run_env
    root = Path(project_root)
    (root / "src").mkdir(parents=True, exist_ok=True)
    (root / "src" / "owner.py").write_text("# fallout FR-061\n", encoding="utf-8")
    (root / "src" / "quiet_a.py").write_text("# no id here\n", encoding="utf-8")
    (root / "src" / "quiet_b.py").write_text("# nor here\n", encoding="utf-8")

    _manifest_with_requirement_ids(fdir, {
        4: (["FR-061"], ["src/owner.py"]),
        9: (["FR-061"], ["src/quiet_a.py", "src/quiet_b.py"]),
    })
    _write_state(fdir, phase="F3", cycle=1)
    _defect_ledger(fdir, [
        dict(_tiered("D-003", "LIVE"), file="src/owner.py", spec_ref="FR-061"),
    ])

    result = foundry_defects_to_tasks(project_root)
    task = next(t for t in result["tasks"] if "D-003" in t["defect_ids"])
    block = task["alignment_block"]
    assert task["co_dispatch"] == [9], task
    assert "src/quiet_a.py" in block and "src/quiet_b.py" in block, block
    assert "no file in this casting cites FR-061 by id" in block, block






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


def test_a_compound_spec_ref_co_dispatches_every_casting_owning_any_of_its_ids(run_env):
    """fallout OT-002 / AC-002 / CT-008 / FR-038 (D-231).

    Both filing doors accept a compound `spec_ref`, and real ledgers are full
    of them. The join read each ref as ONE id, so a ref joining
    fallout FR-007 and fallout CT-004 with a slash intersected no casting
    and the set came back `[]`. Driven here through Foundry-Tasks, over
    PROVE's own manifest: casting 3 owns fallout FR-007 and the fix's file,
    casting 4 owns fallout FR-007, casting 5 owns fallout CT-004. Expected
    `[4, 5]`, with the parsed ids on the dispatch record the F6 report reads.
    """
    project_root, fdir = run_env
    _manifest_with_requirement_ids(fdir, {
        3: (["FR-007"], ["src/three.py"]),
        4: (["FR-007"], ["src/four.py"]),
        5: (["CT-004"], ["src/five.py"]),
    })
    _write_state(fdir, phase="F3", cycle=1)

    for spelling in ("FR-007 / CT-004", "FR-007, CT-004"):
        (fdir / "handoffs.jsonl").unlink(missing_ok=True)
        _defect_ledger(fdir, [
            dict(_tiered("D-900", "LIVE"), file="src/three.py", spec_ref=spelling),
        ])
        result = foundry_defects_to_tasks(project_root)
        task = next(t for t in result["tasks"] if "D-900" in t["defect_ids"])
        assert task["co_dispatch"] == [4, 5], (spelling, task)
        assert task["owning_casting"] == 3, (spelling, task)
        block = task["alignment_block"]
        for fragment in ("CT-004", "FR-007", "casting 4", "casting 5"):
            assert fragment in block, (spelling, fragment, block)

        records = _grind_dispatch_records(fdir)
        assert [r["requirement_ids"] for r in records] == [["CT-004", "FR-007"]], (
            spelling, records,
        )

    # A ref holding no requirement id at all is carried verbatim, as it was: it
    # still names what the defect cited, and still co-dispatches nobody.
    (fdir / "handoffs.jsonl").unlink(missing_ok=True)
    anchor = "research/holmes-orchestrator.md#share-11"
    _defect_ledger(fdir, [
        dict(_tiered("D-901", "LIVE"), file="src/three.py", spec_ref=anchor),
    ])
    task = next(
        t for t in foundry_defects_to_tasks(project_root)["tasks"]
        if "D-901" in t["defect_ids"]
    )
    assert task["co_dispatch"] == [], task
    assert anchor in task["alignment_block"], task["alignment_block"]
    assert [r["requirement_ids"] for r in _grind_dispatch_records(fdir)] == [[anchor]]


def test_a_dispatch_the_set_cannot_key_is_still_recorded_for_team_down(run_env):
    """fallout GI-017 / FR-022 / FR-048 / AC-039 (D-264).

    On a manifest without `requirement_ids` the co-dispatch set is "not
    computable" (fallout AC-006), and Foundry-Tasks skipped the `grind_dispatched`
    record for exactly that task — so the defect was dispatched with no record,
    and Team-Down, which reads nothing else, passed a fix committed with its
    ledger row still open. Driven end to end: a legacy manifest, Foundry-Tasks,
    a commit touching the dispatched file, then the Team-Down predicate. The
    record must exist with a NULL set, the door must refuse, and the F6 report
    must still read that record as no co-dispatch row.
    """
    from foundry_mcp.tools import artifacts
    from foundry_mcp.tools.foundry_report import _halt_and_co_dispatch_section
    from foundry_mcp.tools.orchestration.teams import _unrecorded_fix_problem
    from tests.orchestration._env import _repo_with_commit

    project_root, fdir = run_env
    base = _repo_with_commit(project_root, "a.txt", "before\n")
    (fdir / artifacts.INSPECT_BOUNDARY_SHA_MARKER).write_text(f"{base}\n", encoding="utf-8")
    _write_manifest_with_castings(fdir, ["a.txt"], no_ui=True)
    _write_state(fdir, phase="F3", cycle=4)
    _defect_ledger(fdir, [dict(_tiered("D-001", "LIVE"), file="a.txt")])

    result = foundry_defects_to_tasks(project_root)
    assert result["co_dispatch_computable"] is False, result
    task = next(t for t in result["tasks"] if "D-001" in t["defect_ids"])
    assert task["co_dispatch"] is None, task

    records = _grind_dispatch_records(fdir)
    assert [(r["defect_id"], r["file"]) for r in records] == [("D-001", "a.txt")], records
    assert records[0]["co_dispatch"] is None, records[0]

    fix = _repo_with_commit(project_root, "a.txt", "after\n")
    refusal = _unrecorded_fix_problem(fdir, project_root)
    assert refusal is not None, "a committed fix with an open row passed Team-Down"
    assert [d["id"] for d in refusal["defects"]] == ["D-001"], refusal
    assert fix.startswith(refusal["defects"][0]["commit"]), refusal

    # The other reader of this record. A NULL set is neither a dispatch row nor
    # "the casting owned its requirements alone" — the reader's case 2, which
    # an empty list written here would have claimed.
    section, problem = _halt_and_co_dispatch_section(fdir, {"phase": "F3"})
    assert problem is None, problem
    assert section["co_dispatch_count"] == 0, section
    assert section["co_dispatch_owned_alone_count"] == 0, section


def test_a_decorated_file_still_resolves_the_casting_that_owns_the_fix(run_env):
    """fallout AC-002 / OT-002 / CT-008 / FR-038 (D-270).

    Fallout of D-265, whose record named this exact adjacent failure. Both
    filing doors store `file` verbatim and four stream contracts invite a
    `#Symbol` beside the path, but the owner lookup matched the RAW field, so
    `src/three.py#handler` and `src/three.py:12` were owned by nobody. The
    owning casting then appeared INSIDE `co_dispatch`, told to make the same
    change as a sibling, the block read "Owning casting: not resolvable from
    the manifest", and the dispatch record Team-Down reads carried no casting.
    The absolute spelling rides the same fold (D-269).

    Driven through Foundry-Tasks over PROVE's own manifest, with the bare
    spelling first as the control every decorated one must match.
    """
    project_root, fdir = run_env
    _manifest_with_requirement_ids(fdir, {
        3: (["FR-007"], ["src/three.py"]),
        4: (["FR-007"], ["src/four.py"]),
    })
    _write_state(fdir, phase="F3", cycle=1)

    for spelling in (
        "src/three.py",
        "src/three.py#handler",
        "src/three.py:12",
        f"{project_root}/src/three.py",
    ):
        (fdir / "handoffs.jsonl").unlink(missing_ok=True)
        _defect_ledger(fdir, [
            dict(_tiered("D-900", "LIVE"), file=spelling, spec_ref="FR-007"),
        ])
        task = next(
            t for t in foundry_defects_to_tasks(project_root)["tasks"]
            if "D-900" in t["defect_ids"]
        )
        assert task["owning_casting"] == 3, (spelling, task)
        assert task["co_dispatch"] == [4], (spelling, task)
        block = task["alignment_block"]
        assert "Fixed in casting 3" in block, (spelling, block)
        assert "not resolvable from the manifest" not in block, (spelling, block)
        assert "- casting 3:" not in block, (spelling, block)
        assert [r["casting"] for r in _grind_dispatch_records(fdir)] == [3], spelling




# --------------------------------------------------------------------------- #
# D-008 — A CASTING ID IS WHATEVER THE MANIFEST SPELLS IT.
#
# `directives.py` built both of its manifest maps with `int(casting.get("id"))`
# and skipped every casting the coercion refused, so a manifest whose ids are
# words produced two EMPTY maps and no refusal anywhere. The three tests below
# drive the two entry points that rejoin those maps — the task-owner resolution
# a lead dispatches from, and the cross-casting concern join — over a manifest
# shaped like this run's own.
# --------------------------------------------------------------------------- #


def _word_id_manifest(fdir: Path) -> None:
    """This run's own manifest shape: three castings whose ids are words."""
    (fdir / "castings").mkdir(parents=True, exist_ok=True)
    (fdir / "castings" / "manifest.json").write_text(json.dumps({"castings": [
        {"id": "imperatives", "requirement_ids": ["FR-001"], "key_files": [
            "src/foundry_mcp/tools/orchestration/",
            "tests/orchestration/test_guidance_imperatives.py",
        ]},
        {"id": "payloads", "requirement_ids": ["FR-001"], "key_files": [
            "src/foundry_mcp/tools/evidence.py",
        ]},
        {"id": "release", "requirement_ids": ["FR-009"], "key_files": [
            "pyproject.toml",
        ]},
    ]}), encoding="utf-8")


def test_a_casting_id_the_manifest_spells_as_a_word_still_resolves_its_owner(run_env):
    """D-008 — one door accepted the id another silently dropped.

    Both manifest maps in `directives.py` coerced the id with `int()` and
    `continue`d on failure, so every casting of a word-id manifest fell out of
    both of them: `_owning_casting` answered None for every file it was asked
    about, and `_co_dispatch_for` intersected an empty table while reporting
    ids_declared True beside it — "no other casting owns this" asserted where
    the truth was "nothing was read".

    DRIVEN on this run before the fix: Foundry-Tasks returned a null owner for
    all five generated tasks, three of them naming a file that IS a declared
    key_file, and every alignment block read "Owning casting: not resolvable
    from the manifest". Nothing refused the manifest on the way in — Init took
    it, Validate-Castings passed every dimension on it, Cast-Wave dispatched by
    word id and Accept-Casting accepted by word id — so exactly one consumer
    required an integer while the schema of Spawn-Teammate publishes the field
    as integer-or-string.

    This is fallout FR-009 (D-170)'s symptom through a second cause one rung
    up: that fix taught the resolver what a directory entry is, this one
    teaches the map what an id is. Both fail in the direction that resolver's
    docstring calls the worst one, because the lead dispatches from the field
    and a None leaves the defects with no owner, no refusal and no warning.
    """
    project_root, fdir = run_env
    _word_id_manifest(fdir)

    for path, owner in (
        # Inside the directory entry, which is the shape this run declares.
        ("src/foundry_mcp/tools/orchestration/guidance.py", "imperatives"),
        # ...and the exact entries beside it.
        ("tests/orchestration/test_guidance_imperatives.py", "imperatives"),
        ("src/foundry_mcp/tools/evidence.py", "payloads"),
        ("pyproject.toml", "release"),
        # A file no casting declares is still unowned: the fold widens what an
        # id may be, it does not make every path resolve to somebody.
        ("src/foundry_mcp/server.py", None),
    ):
        assert _owning_casting(fdir, [path]) == owner, (path, _owning_casting(fdir, [path]))

    # The maps themselves, because a resolver that answers correctly off a
    # half-built table would still be dropping castings silently.
    assert sorted(_casting_files(fdir)) == ["imperatives", "payloads", "release"]
    owned, declared = _casting_requirement_ids(fdir)
    assert declared is True, owned
    assert owned == {
        "imperatives": {"FR-001"}, "payloads": {"FR-001"}, "release": {"FR-009"},
    }


def test_an_integer_casting_id_is_still_published_as_an_integer(run_env):
    """D-008 — the fold preserves the type it was already returning.

    `owning_casting` is a PUBLISHED field a lead dispatches from and a handoff
    record carries, so a fold that keyed every id as a string would change what
    every integer-id run emits — `9` becoming `"9"` in a payload two other
    doors already read. An int keys as itself; a digit string that round-trips
    is the same casting as the integer and folds to it, which is what the
    ledger reader in `foundry_state.py` already assumes when it compares both
    sides with str().
    """
    project_root, fdir = run_env
    (fdir / "castings").mkdir(parents=True, exist_ok=True)
    (fdir / "castings" / "manifest.json").write_text(json.dumps({"castings": [
        {"id": 2, "requirement_ids": ["FR-007"], "key_files": ["src/pkg/"]},
        {"id": "9", "requirement_ids": ["FR-007"], "key_files": ["src/pkg/one.py"]},
    ]}), encoding="utf-8")

    owner = _owning_casting(fdir, ["src/pkg/two.py"])
    assert owner == 2 and isinstance(owner, int), repr(owner)
    # The digit string names the same casting the integer would, and the
    # narrower claim still wins over the directory entry.
    digits = _owning_casting(fdir, ["src/pkg/one.py"])
    assert digits == 9 and isinstance(digits, int), repr(digits)


def test_foundry_tasks_resolves_owner_and_co_dispatch_on_a_word_id_manifest(run_env):
    """D-008 — the field the lead actually dispatched from, driven end to end.

    The symbol-level test above proves the map; this proves what reaches the
    lead. Before the fix every one of these was the empty answer: a null owner,
    an empty set, and a block whose standing header told the lead the owner was
    not resolvable from a manifest that names it on its first line.
    """
    project_root, fdir = run_env
    _word_id_manifest(fdir)
    _write_state(fdir, phase="F3", cycle=1)
    _defect_ledger(fdir, [
        dict(
            _tiered("D-900", "LIVE"),
            file="src/foundry_mcp/tools/orchestration/guidance.py",
            spec_ref="FR-001",
        ),
    ])

    result = foundry_defects_to_tasks(project_root)
    assert result["ok"] is True, result
    task = next(t for t in result["tasks"] if "D-900" in t["defect_ids"])

    assert task["owning_casting"] == "imperatives", task
    # `release` owns a different id and stays out; the owner is excluded from
    # its own set, which is only possible once both maps key the same way.
    assert task["co_dispatch"] == ["payloads"], task
    block = task["alignment_block"]
    assert "Fixed in casting imperatives" in block, block
    assert "not resolvable from the manifest" not in block, block
    assert "- casting payloads: src/foundry_mcp/tools/evidence.py" in block, block
    # ...and the dispatch record Team-Down and the F6 report read carries it.
    record = _grind_dispatch_records(fdir)[0]
    assert record["casting"] == "imperatives", record
    assert record["co_dispatch"] == ["payloads"], record


def test_a_cross_casting_concern_targeting_a_word_id_is_carried_and_marked(run_env):
    """D-008 — the OTHER transition into the same map.

    A concern record stores the casting id the manifest spells, verbatim, and
    `directives.py` read it back through `int()` at four sites. DRIVEN before
    the fix, this test failed with `co_dispatch == []`: the two guarded sites
    dropped the word target as unresolvable, so the concern was never carried,
    never marked, and stayed open with the Tasks-driven exit unreachable and
    `inspect_start` refusing on a concern nothing could dispatch. The two
    UNGUARDED sites, both inside the alignment-block render, never raised only
    because that upstream skip is what keeps a target they cannot read from
    ever reaching them — a raise held off by the same bug, which is why this
    test pins the whole path rather than the coercion.

    The concern targets a file belonging to a casting the REQUIREMENT join does
    not reach, so it is carried for the one reason the ownership join cannot
    supply — the case fallout ST-003 (D-054) exists for, one spelling over.
    """
    from foundry_mcp.tools.concerns import (
        foundry_concern,
        open_concerns_for_other_castings,
    )

    project_root, fdir = run_env
    _word_id_manifest(fdir)
    _write_state(fdir, phase="F3", cycle=1)
    _defect_ledger(fdir, [
        dict(
            _tiered("D-901", "LIVE"),
            file="src/foundry_mcp/tools/orchestration/guidance.py",
            spec_ref="FR-001",
        ),
    ])
    opened = foundry_concern(
        casting_id="imperatives", cycle=1, target="pyproject.toml",
        text="the version floor I relied on is stated in the release casting too",
        project_root=project_root,
    )
    assert opened.get("error") is None, opened
    concern_id = opened["concern"]["id"]
    assert opened["concern"]["target_casting_id"] == "release", opened

    result = foundry_defects_to_tasks(project_root)
    assert result["ok"] is True, result
    task = next(t for t in result["tasks"] if "D-901" in t["defect_ids"])

    # `payloads` owns the requirement; `release` is here because the concern
    # names it. Both spellings sort, which a set holding two types cannot.
    assert task["co_dispatch"] == ["payloads", "release"], task
    assert task["concerns_co_dispatched"] == [concern_id], task
    assert result["concerns_dispatched"] == [concern_id], result
    block = task["alignment_block"]
    assert concern_id in block, block
    assert "-> casting release: pyproject.toml" in block, block
    # ...and the door the concern was holding shut now opens.
    assert open_concerns_for_other_castings(fdir) == [], "the concern is still open"


# --------------------------------------------------------------------------- #
# lead-stalls D-075 / D-076 — THE D-008 REMEDY, ON THE ONE MANIFEST SHAPE IT
# IS OBSERVABLE ON.
#
# Three joins carry that fix and no test reached any of them. `_co_dispatch_for`
# and `_annotate_co_dispatch`'s concern join both pass `key=_casting_order` to
# `sorted`, and `_concern_only_tasks` reads its target through `_casting_key`.
# Each of the three was reverted ALONE at 7c2f0a0 and judged by the WHOLE suite:
# 5756 passed / 113 skipped / 0 failed every time, zero red beyond the control.
#
# THE REVERTS ARE NOT NO-OPS, which is what makes this a missing test rather
# than a dead argument. `sorted({1, "payloads"})` raises TypeError, and
# `int("imperatives")` raises ValueError onto a `continue` that drops the
# concern — the exact D-008 symptom, restored, with nothing noticing. What hid
# it is the fixtures: every manifest in the suite keys its castings all-int or
# all-word, and over a homogeneous set `_casting_order` and `sorted`'s default
# agree. The remedy's only observable behaviour is on a MIXED manifest, and
# until these three tests no fixture built one.
# --------------------------------------------------------------------------- #


def _mixed_id_manifest(fdir: Path, castings: list[tuple]) -> None:
    """`[(casting id, requirement_ids, key_files)]` as the F0.5 manifest, in
    the order given, over ids that need NOT be all one type.

    `_manifest_with_requirement_ids` cannot express this and never will:
    it walks `sorted(spec.items())`, which raises the very TypeError
    `_casting_order` exists to prevent. That helper builds most of the suite's
    requirement-bearing manifests, which is part of why the mixed shape had no
    fixture — the builder the suite reaches for first could not hold one.
    """
    (fdir / "castings").mkdir(parents=True, exist_ok=True)
    (fdir / "castings" / "manifest.json").write_text(
        json.dumps({
            "no_ui": True,
            "castings": [
                {"id": cid, "title": f"casting {cid}",
                 "requirement_ids": ids, "key_files": files}
                for cid, ids, files in castings
            ],
        }),
        encoding="utf-8",
    )


def test_a_manifest_mixing_an_int_id_and_a_word_id_still_orders_the_join(run_env):
    """D-075 — `key=_casting_order` on `_co_dispatch_for`'s `sorted`, pinned.

    `_co_dispatch_for`'s own docstring says the key is there because plain
    `sorted` "raises TypeError the moment a manifest carries an int id and a
    word id together". Removing it left the whole suite green: the set the join
    builds came from a homogeneous map in every fixture, and over one of those
    the two orders agree. So the argument the docstring justifies was, until
    this test, unjudged — and a later tidy reading the suite as the authority
    on what is load-bearing would have been told it was free to drop.

    The manifest below is this run's own spelling beside an integer one, which
    is a shape F0.5 has no rule against: `_casting_key` accepts both, four
    doors take either, and `Foundry-Spawn-Teammate` publishes the field as
    integer-or-string. Mixed is therefore not a synthetic case but an
    unrefused one, and it is the only case in which the key is visible.
    """
    project_root, fdir = run_env
    _mixed_id_manifest(fdir, [
        ("imperatives", ["FR-002"], ["src/imp.py"]),
        (3, ["FR-001"], ["src/three.py"]),
        ("payloads", ["FR-001"], ["src/payloads.py"]),
    ])
    _write_state(fdir, phase="F3", cycle=1)
    _defect_ledger(fdir, [
        dict(_tiered("D-900", "LIVE"), file="src/imp.py", spec_ref="FR-001"),
    ])

    # The join itself, over the map this manifest produces: the set is
    # {3, "payloads"} and an unkeyed `sorted` over it raises.
    owned, declared = _casting_requirement_ids(fdir)
    assert declared is True, owned
    assert _co_dispatch_for(owned, {"FR-001"}) == [3, "payloads"], owned
    # Ints keep their numeric order and word ids follow them, which is the
    # order `_casting_order` declares; `exclude` still drops the owner.
    assert _co_dispatch_for(owned, {"FR-001"}, exclude=3) == ["payloads"], owned

    # ...and the field the lead dispatches from, end to end.
    result = foundry_defects_to_tasks(project_root)
    assert result["ok"] is True, result
    task = next(t for t in result["tasks"] if "D-900" in t["defect_ids"])
    assert task["owning_casting"] == "imperatives", task
    assert task["co_dispatch"] == [3, "payloads"], task
    block = task["alignment_block"]
    assert "- casting 3: src/three.py" in block, block
    assert "- casting payloads: src/payloads.py" in block, block


def test_a_word_id_concern_target_joins_an_int_id_co_dispatch_set(run_env):
    """D-075 — the SAME key on the OTHER join, `_annotate_co_dispatch`'s.

    The concern join unions the target into the set the requirement join
    computed, and the two sides come from different places: the set from the
    manifest's `requirement_ids`, the target from a concern record. So a run
    whose requirement join is homogeneous can still be handed a target of the
    other spelling, and that union is where the second `sorted` stands.

    Driven with the requirement join answering `[3]` — all-int, so it survives
    its own revert untouched — and a concern naming `release`. Without the key
    on THIS `sorted` the union of an int set and a word target raises, and the
    two halves of one remedy are then judged separately rather than together,
    which is what "red beyond the control: 0" on hunk 13 alone already showed.
    """
    from foundry_mcp.tools.concerns import (
        foundry_concern,
        open_concerns_for_other_castings,
    )

    project_root, fdir = run_env
    _mixed_id_manifest(fdir, [
        (1, ["FR-001"], ["src/one.py"]),
        (3, ["FR-001"], ["src/three.py"]),
        ("release", ["FR-009"], ["pyproject.toml"]),
    ])
    _write_state(fdir, phase="F3", cycle=1)
    _defect_ledger(fdir, [
        dict(_tiered("D-900", "LIVE"), file="src/one.py", spec_ref="FR-001"),
    ])
    opened = foundry_concern(
        casting_id=1, cycle=1, target="pyproject.toml",
        text="the version floor I relied on is stated in the release casting too",
        project_root=project_root,
    )
    assert opened.get("error") is None, opened
    concern_id = opened["concern"]["id"]
    # The record stores the id the MANIFEST spells, which is the word here.
    assert opened["concern"]["target_casting_id"] == "release", opened

    result = foundry_defects_to_tasks(project_root)
    assert result["ok"] is True, result
    task = next(t for t in result["tasks"] if "D-900" in t["defect_ids"])

    # `3` is here because it owns the requirement; `release` because the
    # concern names it. One int, one word, in one sorted set.
    assert task["co_dispatch"] == [3, "release"], task
    assert task["concerns_co_dispatched"] == [concern_id], task
    assert result["concerns_dispatched"] == [concern_id], result
    block = task["alignment_block"]
    assert "- casting 3: src/three.py" in block, block
    assert "-> casting release: pyproject.toml" in block, block
    assert open_concerns_for_other_castings(fdir) == [], "the concern is still open"


def test_a_clean_grind_dispatches_a_concern_whose_target_is_a_word_id(run_env):
    """D-076 — `_casting_key` in `_concern_only_tasks`, pinned.

    This is the half of D-008 the user ruled had to be fixed in this run, and
    it is the half no test reached. The walk read its target with
    `int(concern["target_casting_id"])` and `continue`d on ValueError, so on a
    word-id manifest every concern was dropped FOR ITS SPELLING: the concern
    stayed open, `inspect_start` refused on it, and the exit that refusal names
    — "call Foundry-Tasks, whose co-dispatch set carries the concern to the
    casting it names" — was unreachable. The sibling walk in
    `_annotate_co_dispatch` is pinned by
    `test_a_cross_casting_concern_targeting_a_word_id_is_carried_and_marked`
    above, and its comment says "as in the sibling walk above", so the gap was
    one walk rather than the mechanism — which is exactly the shape a whole
    green suite hides.

    A CLEAN GRIND is the state that reaches this walk. With every defect fixed
    there are no tasks for a concern to ride, so `_concern_only_tasks` makes
    the concern the packet itself — and it is the only place the word target is
    read without the sibling's cover.
    """
    from foundry_mcp.tools.concerns import (
        foundry_concern,
        open_concerns_for_other_castings,
    )

    project_root, fdir = run_env
    _word_id_manifest(fdir)
    _write_state(fdir, phase="F3", cycle=2)
    # FIXED, not open: a GRIND that closed everything it was handed, which is
    # the only state a lead calls `inspect_start` from.
    _defect_ledger(fdir, [
        dict(
            _tiered("D-900", "LIVE"),
            file="src/foundry_mcp/tools/orchestration/guidance.py",
            spec_ref="FR-001", status="fixed",
        ),
    ])
    opened = foundry_concern(
        casting_id="imperatives", cycle=2, target="pyproject.toml",
        text="the release casting's own floor states the ruling I applied",
        project_root=project_root,
    )
    assert opened.get("error") is None, opened
    concern_id = opened["concern"]["id"]
    assert opened["concern"]["target_casting_id"] == "release", opened
    assert len(open_concerns_for_other_castings(fdir)) == 1

    result = foundry_defects_to_tasks(project_root)
    assert result["ok"] is True, result
    packets = [t for t in result["tasks"] if t.get("concern_only")]
    assert len(packets) == 1, result["tasks"]
    task = packets[0]
    assert task["concern_id"] == concern_id, task
    # The target, in the spelling the manifest uses, with that casting's own
    # key_files as the work — a packet, not a cleared flag.
    assert task["co_dispatch"] == ["release"], task
    assert task["files"] == ["pyproject.toml"], task
    assert task["owning_casting"] is None, task
    assert result["concerns_dispatched"] == [concern_id], result
    # ...and the door the concern was holding shut opens.
    assert open_concerns_for_other_castings(fdir) == [], "the concern stayed open"


# --------------------------------------------------------------------------- #
# lead-stalls D-075 / D-076 — THE THREE JOINS, EACH REVERTED ALONE.
#
# The three tests above are the pins; this is the drive that shows each one
# bites, and the one `evidence/casting-imperatives-codispatch-order-revert.log`
# re-executes. TEST filed D-075 and D-076 by doing exactly this at 7c2f0a0 and
# getting zero red on all three, so a fix whose only claim is "I wrote a test"
# is a claim about the same thing measured the same way — with the answer now
# expected to be one red per join.
#
# `_run_with` is IMPORTED from the sibling module rather than copied, for the
# reason this module's header gives for the two suites it already imports: a
# second implementation of "copy the tree, mutate one file, run the suite
# there" is free to drift from the one the other evidence logs are drawn
# through, and two drives over one tree must not be able to disagree about what
# a revert did. The judge is THIS module, because these three pins live here.
# --------------------------------------------------------------------------- #

_DIRECTIVES_REL = "src/foundry_mcp/tools/orchestration/directives.py"
_THIS_MODULE = "tests/orchestration/test_tasks_codispatch.py"

#: One row per join the D-008 remedy stands on: the name the log prints, the
#: file, the text as it stands, and the text TEST reverted it to. Kept as the
#: reverts were FILED, so the log answers the filing rather than a paraphrase
#: of it.
_CODISPATCH_REVERTS = (
    (
        "requirement-join-order", _DIRECTIVES_REL,
        "\n".join([
            "    return sorted(",
            "        (",
            "            cid for cid, ids in owned.items()",
            "            if cid != exclude and ids & requirement_ids",
            "        ),",
            "        key=_casting_order,",
            "    )",
        ]),
        "\n".join([
            "    return sorted(",
            "        cid for cid, ids in owned.items()",
            "        if cid != exclude and ids & requirement_ids",
            "    )",
        ]),
    ),
    (
        "concern-join-order", _DIRECTIVES_REL,
        "\n".join([
            '                task["co_dispatch"] = sorted(',
            '                    set(task["co_dispatch"]) | {target}, key=_casting_order',
            "                )",
        ]),
        "\n".join([
            '                task["co_dispatch"] = sorted(',
            '                    set(task["co_dispatch"]) | {target}',
            "                )",
        ]),
    ),
    (
        "concern-only-target", _DIRECTIVES_REL,
        "\n".join([
            '        target = _casting_key(concern.get("target_casting_id"))',
            "        if target is None:",
            "            # Unresolvable targets are refused at `Foundry-Concern`'s own door",
            "            # (CT-001), so one here is a hand-edited ledger. It stays open and",
            "            # `inspect_start` keeps naming it, which is the honest end for a",
            "            # record nothing can resolve.",
            "            continue",
        ]),
        "\n".join([
            "        try:",
            '            target = int(concern["target_casting_id"])',
            "        except (KeyError, TypeError, ValueError):",
            "            continue",
        ]),
    ),
)


def test_every_codispatch_revert_names_text_that_is_in_the_tree_exactly_once():
    """The floor under `codispatch_revert_report`, and why it is a TEST.

    A row whose text has moved cannot be applied, so the join it stands for is
    silently unjudged: the log still re-executes, still reports its zero, over
    a population one smaller than it claims. That is the green-over-nothing
    failure D-075 and D-076 ARE — three joins nothing judged, under a suite
    reporting 5756 passed — so the sweep that closes them must not be able to
    fail the same way one layer up.

    `concern-only-target` is the row this pin earns twice over: the text it
    reverts is the second of two walks that read the same field the same way,
    and the only thing distinguishing it from its sibling is the comment
    underneath. A row quoting the coercion alone would match twice and apply
    to neither.
    """
    project = Path(__file__).resolve().parents[2]
    counts = {
        name: (project / rel).read_text(encoding="utf-8").count(old)
        for name, rel, old, _new in _CODISPATCH_REVERTS
    }
    assert {n: c for n, c in counts.items() if c != 1} == {}, counts
    names = [name for name, *_rest in _CODISPATCH_REVERTS]
    assert len(set(names)) == len(names), names
    assert set(names) == {
        "requirement-join-order", "concern-join-order", "concern-only-target",
    }, sorted(names)


def codispatch_revert_report(workers: int = 3) -> list[str]:
    """`evidence/casting-imperatives-codispatch-order-revert.log`: each join of
    the D-008 remedy reverted alone, and the tests that go red for it.

    The CONTROL is the unmutated tree, run first and subtracted: a suite red
    before anything is reverted says nothing about any join, and charging its
    failures to a mutation is how a vacuous drive reads as a thorough one.
    `test_guidance_imperatives.py`'s two reports state the same rule for their
    own populations.

    Deterministic in order and in content — the rows are walked in table order
    and `_run_with` sorts its failures — so the log re-executes
    byte-identically. ``workers`` only changes how long that takes.
    """
    from concurrent.futures import ThreadPoolExecutor

    from tests.orchestration.test_guidance_imperatives import _run_with

    project = Path(__file__).resolve().parents[2]
    source = (project / _DIRECTIVES_REL).read_text(encoding="utf-8")
    baseline = [
        line for line in _run_with(
            project, _DIRECTIVES_REL, source, (_THIS_MODULE,)
        )
        if line.startswith("  FAILED")
    ]

    def judged(row: tuple[str, str, str, str]) -> list[str]:
        name, rel, old, new = row
        if source.count(old) != 1:
            # NOT an exception: a row whose text has moved is a finding about
            # the table, and a report that raised here would take the whole
            # log down with it and say nothing about the other two.
            return [
                f"  the text this reverts appears {source.count(old)} times in "
                f"{Path(rel).name} — the join moved and this row did not"
            ]
        result = _run_with(
            project, rel, source.replace(old, new), (_THIS_MODULE,)
        )
        red = [
            line for line in result
            if line.startswith("  FAILED") and line not in baseline
        ]
        return red + [f"{result[-1]}, red beyond the control: {len(red)}"]

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        results = list(pool.map(judged, _CODISPATCH_REVERTS))

    lines = [
        f"joins reverted one at a time: {len(_CODISPATCH_REVERTS)}",
        f"judged by: {_THIS_MODULE}",
        "files mutated: " + ", ".join(sorted(
            Path(rel).name for rel in
            {rel for _n, rel, _o, _x in _CODISPATCH_REVERTS}
        )),
        f"red in the control with nothing reverted: {len(baseline)}",
        *baseline,
        "",
    ]
    for (name, rel, _old, _new), result in zip(_CODISPATCH_REVERTS, results):
        lines.append(f"== revert: {name} ({Path(rel).name})")
        lines.extend(result)
    green = [
        name for (name, _rel, _old, _new), result in
        zip(_CODISPATCH_REVERTS, results)
        if not any(line.startswith("  FAILED") for line in result)
    ]
    lines += [
        "",
        f"joins with no test red when reverted: {', '.join(green) or 'none'}",
    ]
    return lines
