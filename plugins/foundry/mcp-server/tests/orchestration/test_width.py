"""The INSPECT width decision and its readers.

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
    _write_manifest_with_castings,
    _write_state,
    run_env,
)

from foundry_mcp.tools.orchestration.width import (  # noqa: F401
    _maybe_skip_trace,
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
    }])
    _write_manifest_with_castings(fdir, ["src/api/a.py"], no_ui=True)

    decision = _maybe_skip_trace(fdir, project_root)

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
    }])
    _write_manifest_with_castings(fdir, ["src/api/a.py"], no_ui=True)

    decision = _maybe_skip_trace(fdir, project_root)

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
