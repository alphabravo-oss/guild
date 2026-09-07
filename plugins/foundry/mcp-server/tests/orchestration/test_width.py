"""The INSPECT width decision and its readers.

Carved from `tests/test_orchestrator_gates.py` (fallout FR-005 / GI-026 /
AC-014 / OT-016): one test module per shipped orchestration module, landed in
the same casting as the source move so no pin is ever left pointing at a module
that no longer exists.
"""
from __future__ import annotations

import json



from foundry_mcp.tools import artifacts

# fallout FR-004 / AC-013 — THE MODULE OBJECTS, UNDER UNDERSCORE ALIASES.
#
# `streams`, `spend`, `width`, `gates`, `directives` and `teams` are all LOCAL
# variable names somewhere in this suite, and a local rebinding shadows a
# module for the rest of its function. The aliases are what `ORCHESTRATION`,
# `owning_module` and every `monkeypatch.setattr` resolve through; individual
# SYMBOLS are imported by name below, which is how the carved modules read.
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
    _write_manifest_with_castings,
    _write_state,
    run_env,
)

from foundry_mcp.tools.orchestration.width import (  # noqa: F401
    _trace_skip_from_width,
)

# fallout GI-008 / GI-009 / GI-033 (D-021 / D-035, ruling item 5) — the fence
# split in two. `width._trace_skip_from_width` DECIDES at the transition and the
# answer is recorded in the `inspect_modes` entry; `guidance._stamp_trace_skip`
# READS the recorded answer and performs the stamp. Both halves are driven
# below, and the entries the fixtures write now carry the field a real decision
# would have written.
from foundry_mcp.tools.orchestration.guidance import (  # noqa: F401
    _stamp_trace_skip,
)








def test_the_trace_skip_fires_on_a_delta_cycle_with_an_empty_diff(run_env):
    """...and is not removed, which would be GI-007 from the other side.

    In DELTA mode TRACE's scope is "the symbols the GRIND commits touched"
    (AC-019). An empty diff has no symbols to walk, which is the one case where
    skipping and running are the same answer.
    """
    project_root, fdir = run_env
    _write_state(fdir, phase="F2", cycle=2, inspect_modes=[{
        "cycle": 2, "phase": "F2", "mode": "DELTA", "rule": "delta",
        "decided_by": "inspect_start",
        "required_streams": ["trace", "prove", "test"],
        "stream_scope": {}, "prove_sample": [], "touched_files": [],
        "trace_skip": {"skip": True, "reason": "DELTA width and the GRIND diff is empty — there are no touched symbols for TRACE to walk"},
    }])
    _write_manifest_with_castings(fdir, ["src/api/a.py"], no_ui=True)

    # The DECISION, at the transition that records the width...
    assert _trace_skip_from_width(False, "delta", [], "DELTA")["skip"] is True
    # ...and the EFFECT, at the surface that reads what was recorded.
    decision = _stamp_trace_skip(fdir)

    assert decision["skip"] is True, decision
    assert (fdir / ".trace-complete").exists()




def test_the_trace_skip_does_not_fire_on_a_delta_cycle_with_a_diff(run_env):
    """The other DELTA arm: files were touched, so there are symbols to walk."""
    project_root, fdir = run_env
    _write_state(fdir, phase="F2", cycle=2, inspect_modes=[{
        "cycle": 2, "phase": "F2", "mode": "DELTA", "rule": "delta",
        "decided_by": "inspect_start",
        "required_streams": ["trace", "prove", "test"],
        "stream_scope": {}, "prove_sample": [],
        "touched_files": ["src/api/a.py"],
        "trace_skip": {"skip": False, "reason": "DELTA width over 1 touched file(s) — TRACE runs over the symbols the GRIND commits touched"},
    }])
    _write_manifest_with_castings(fdir, ["src/api/a.py"], no_ui=True)

    assert _trace_skip_from_width(False, "delta", ["src/api/a.py"], "DELTA")["skip"] is False
    decision = _stamp_trace_skip(fdir)

    assert decision["skip"] is False, decision
    assert not (fdir / ".trace-complete").exists()


# --------------------------------------------------------------------------- #
# fallout OT-013 / OT-014 / FR-043 — ONLY THE VERIFIER FORCES FULL WIDTH.
# --------------------------------------------------------------------------- #

#: The surfaces a GRIND may touch and still earn a DELTA INSPECT. Every one of
#: them is a module this casting created, and the point of creating them was
#: that the monolith made this list impossible: one file held the gate ladder
#: AND the report seal AND the spend ledger, so a diff touching the spend door
#: forced a FULL cycle over everything.
_DELTA_SURFACES = (
    "plugins/foundry/mcp-server/src/foundry_mcp/tools/display.py",
    "plugins/foundry/mcp-server/src/foundry_mcp/tools/orchestration/report_seal.py",
    "plugins/foundry/mcp-server/src/foundry_mcp/tools/orchestration/spend.py",
    "plugins/foundry/mcp-server/src/foundry_mcp/tools/orchestration/halt.py",
    "plugins/foundry/mcp-server/src/foundry_mcp/tools/orchestration/directives.py",
    "plugins/foundry/mcp-server/src/foundry_mcp/tools/orchestration/teams.py",
    "plugins/foundry/commands/start.md",
    "plugins/foundry/agents/teammate.md",
    "plugins/foundry/references/lead-discipline.md",
)

#: The surfaces whose diff makes a verdict already reached UNTRUSTWORTHY, which
#: is the only thing `verifier_touched` is for.
_VERIFIER_SURFACES = (
    "plugins/foundry/mcp-server/src/foundry_mcp/tools/orchestration/gates.py",
    "plugins/foundry/mcp-server/src/foundry_mcp/tools/orchestration/transitions.py",
    "plugins/foundry/mcp-server/src/foundry_mcp/tools/orchestration/width.py",
    "plugins/foundry/mcp-server/src/foundry_mcp/tools/orchestration/evidence_boundary.py",
    "plugins/foundry/mcp-server/src/foundry_mcp/tools/evidence.py",
    "plugins/foundry/mcp-server/src/foundry_mcp/schemas/vocab.py",
    "plugins/foundry/mcp-server/src/foundry_mcp/schemas/findings.py",
)


def test_the_split_surfaces_that_may_earn_a_delta_inspect():
    """fallout OT-013 — a diff touching only these records rule `delta`.

    This is what the whole carve BUYS. Before it, one file held the gate ladder,
    the report seal, the spend ledger, the halt door, the directives and the team
    lifecycle, so a diff touching any of them matched the verifier pattern and
    every GRIND ran at FULL width. Casting 10 narrowed `VERIFIER_PATH_PATTERNS`
    to four module paths; this asserts the paths it names are the ones that now
    exist, which is the half a narrowing cannot check for itself.
    """
    from foundry_mcp.schemas.vocab import is_verifier_path

    forced = [p for p in _DELTA_SURFACES if is_verifier_path(p, None)]
    assert forced == [], (
        f"{forced} still match the verifier pattern, so a GRIND touching only "
        "the presentation and lifecycle surfaces would run at FULL width — the "
        "cost the split exists to remove."
    )


def test_every_verifier_surface_still_forces_full_after_the_split():
    """fallout OT-014 / FR-043 — and the narrowing did not narrow too far.

    A pattern set that matched NOTHING would satisfy the test above and be
    catastrophic: `verifier_touched` would never fire and a run that rewrote its
    own gate would re-use verdicts the rewrite invalidated. Both halves, on the
    same list, in the same commit.
    """
    from foundry_mcp.schemas.vocab import is_verifier_path

    missed = [p for p in _VERIFIER_SURFACES if not is_verifier_path(p, None)]
    assert missed == [], (
        f"{missed} no longer match the verifier pattern. A diff moving any of "
        "them can make a verdict already reached wrong, which is the whole of "
        "what `verifier_touched` is for."
    )


def test_the_two_sets_are_disjoint_and_neither_is_empty():
    """The emptiness guard on both axes at once."""
    assert set(_DELTA_SURFACES).isdisjoint(_VERIFIER_SURFACES)
    assert len(_DELTA_SURFACES) >= 6 and len(_VERIFIER_SURFACES) >= 6




# --------------------------------------------------------------------------- #
# fallout research/holmes-orchestrator.md#acc-3 (D-097) — the research-skip
# record's accepted spellings are declared once and honoured as declared.
# --------------------------------------------------------------------------- #


def test_the_research_skip_read_honours_exactly_its_declared_spellings(run_env):
    """fallout research/holmes-orchestrator.md#acc-3 / RA-6 (D-097).

    RA-6 names `_research_skipped` reading three uncoordinated storage locations
    as a cohesion defect. The accepted set is now DECLARED — the marker plus
    `_RESEARCH_SKIPPED_KEY` in each of `_RESEARCH_SKIPPED_DOCUMENTS` — instead of
    being spelled inline in a loop, so the set a reader must satisfy is stated in
    one place rather than discovered by reading the reader.

    THIS IS THE PIN ON THE DECLARATION. It drives every declared spelling in
    isolation and asserts each one alone is enough, and it drives an UNdeclared
    document to assert the read is not simply scanning everything. A spelling
    silently dropped from the declaration fails here, and a fourth location
    added to the reader without joining the declaration fails here too.

    WHAT IS NOT CLOSED, recorded so nobody reads this pin as saying it is: no
    shipped writer writes ANY of the three (swept across `plugins/foundry/**`
    for both spellings — the only writes are test fixtures), so the single WRITE
    point RA-6 also asks for belongs at `foundry_init` in casting 4's
    `tools/foundry.py` and is raised as a cross-casting concern.
    """
    _project_root, fdir = run_env

    documents = _width._RESEARCH_SKIPPED_DOCUMENTS
    key = _width._RESEARCH_SKIPPED_KEY
    assert documents, "the accepted-document set is empty; the read is blind"
    assert key == "research_skipped", key

    def _clear() -> None:
        (fdir / artifacts.RESEARCH_SKIPPED_MARKER).unlink(missing_ok=True)
        for document in documents:
            path = fdir / document
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({}), encoding="utf-8")

    # Nothing recorded anywhere: the read says no.
    _clear()
    assert _width._research_skipped(fdir) is False

    # The marker alone.
    _clear()
    (fdir / artifacts.RESEARCH_SKIPPED_MARKER).write_text("x\n", encoding="utf-8")
    assert _width._research_skipped(fdir) is True, "the marker spelling stopped counting"

    # Each declared document alone — driven per member, so a spelling dropped
    # from the tuple is named by the parametrised failure rather than hidden by
    # another member still answering yes.
    for document in documents:
        _clear()
        (fdir / document).write_text(json.dumps({key: True}), encoding="utf-8")
        assert _width._research_skipped(fdir) is True, document

    # ...and an UNdeclared document does not count, which is what makes the
    # declaration a set rather than a description of a scan.
    _clear()
    undeclared = fdir / "verdicts.json"
    undeclared.write_text(json.dumps({key: True}), encoding="utf-8")
    assert undeclared.name not in documents, undeclared.name
    assert _width._research_skipped(fdir) is False, (
        "the read counted a document the declaration does not name"
    )
