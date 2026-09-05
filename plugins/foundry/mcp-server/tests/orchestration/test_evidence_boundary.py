"""The evidence sweep at the INSPECT and terminal boundaries.

Carved from `tests/test_orchestrator_gates.py` (fallout FR-005 / GI-026 /
AC-014 / OT-016): one test module per shipped orchestration module, landed in
the same casting as the source move so no pin is ever left pointing at a module
that no longer exists.
"""
from __future__ import annotations

import subprocess
from pathlib import Path


from foundry_mcp.tools import artifacts, foundry_state
from foundry_mcp.tools.foundry_state import current_cycle

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
    _arm_ordering_token,
    _write_manifest_with_castings,
    _write_state,
    run_env,
)

from foundry_mcp.tools.orchestration.evidence_boundary import (  # noqa: F401
    _sweep_evidence_at_boundary,
    _terminal_evidence_sweep,
)

from foundry_mcp.tools.orchestration.transitions import (  # noqa: F401
    foundry_mark_phase_complete,
)

from tests.orchestration._env import (  # noqa: F401
    _router_ledger,
)




def test_the_sweep_refusals_prescribed_remedy_works(run_env):
    """D-067's driven case, end to end: the refusal names a retry, and the retry
    is accepted without a second Foundry-Next.

    `_sweep_refusal` carries a `token` parameter for no purpose other than
    naming the right transition to retry, so the remedy was engineered to be
    directly actionable and the token consumption made it not.
    """
    project_root, fdir = run_env
    _write_state(fdir, phase="F3", cycle=1)
    _write_manifest_with_castings(fdir, ["src/api/a.py"], no_ui=True)
    _router_ledger(fdir, [])
    _arm_ordering_token(fdir)

    calls = {"n": 0}

    def _sweep(fdir_, project_root_, entry, *, full):
        calls["n"] += 1
        if calls["n"] == 1:
            return {
                "ok": False, "record": {"scope": "full", "logs_reexecuted": [],
                                        "mismatches": [], "elapsed_seconds": 0.0,
                                        "pool_size": 0, "per_log": []},
                "mismatches": [{"log": "evidence/casting-3-gates.log",
                                "reason": "output mismatch"}],
                "error": "",
            }
        return {
            "ok": True, "record": {"scope": "full", "logs_reexecuted": [],
                                   "mismatches": [], "elapsed_seconds": 0.0,
                                   "pool_size": 0, "per_log": []},
            "mismatches": [], "error": "",
        }

    # fallout FR-004: rebound on every module that carries the name, because
    # after the carve `transitions.py` imported it and a local rebinding here
    # reaches none of them. Restored in the `finally` from the originals rather
    # than by reloading, which cannot put back what other modules imported.
    _real = {
        m: vars(m)["_sweep_evidence_at_boundary"] for m in ORCHESTRATION
        if "_sweep_evidence_at_boundary" in vars(m)
    }
    assert _real, "nothing carries the sweep; this drive would assert nothing"
    for _module in _real:
        setattr(_module, "_sweep_evidence_at_boundary", _sweep)
    try:
        refused = foundry_mark_phase_complete("inspect_start", project_root)
        assert refused.get("ok") is not True
        assert "casting-3-gates.log" in refused["error"]
        assert "inspect_start" in refused["hint"]
        assert current_cycle(fdir) == 1, "the counter did not move"

        # The log is repaired. Do EXACTLY what the hint said — no Foundry-Next.
        retried = foundry_mark_phase_complete("inspect_start", project_root)
    finally:
        for _module, _original in _real.items():
            setattr(_module, "_sweep_evidence_at_boundary", _original)

    assert retried["ok"] is True, retried
    assert retried["cycle"] == 2




def test_the_terminal_sweep_is_taken_once_per_head(run_env, monkeypatch):
    """D-133's cost half: the gate and the transition ask the same question.

    `_done_preconditions` is ONE evaluation with two callers by design (D-037),
    so the evidence rung has to be in it — a rung present in the transition and
    absent from the gate is the drift that helper exists to prevent. A
    whole-corpus re-execution is minutes, and Foundry-Gate('done') followed by
    Foundry-Phase('done') would pay it twice. The result is memoised on HEAD,
    so the claim stays exactly as strong ("the corpus was re-executed at THIS
    tree") while being made once.
    """
    import subprocess

    project_root, fdir = run_env
    subprocess.run(["git", "init", "-q", project_root], check=True)
    subprocess.run(["git", "-C", project_root, "config", "user.email", "t@t"],
                   check=True)
    subprocess.run(["git", "-C", project_root, "config", "user.name", "t"],
                   check=True)
    (Path(project_root) / "seed.txt").write_text("x", encoding="utf-8")
    subprocess.run(["git", "-C", project_root, "add", "seed.txt"], check=True)
    subprocess.run(["git", "-C", project_root, "commit", "-qm", "seed"], check=True)

    calls = []

    def _counting(_fdir, _pr, _entry, *, full):
        calls.append(full)
        return {"ok": True, "record": {"scope": "full", "logs_reexecuted": []},
                "mismatches": [], "error": ""}

    patch_everywhere(monkeypatch, "_sweep_evidence_at_boundary", _counting)

    first = _terminal_evidence_sweep(fdir, project_root)
    second = _terminal_evidence_sweep(fdir, project_root)

    assert first["ok"] and second["ok"]
    assert first["cached"] is False and second["cached"] is True
    assert len(calls) == 1, "the second ask re-executed the corpus"

    # ...and a commit — which is what a fix landing in F5 or F5.5 IS —
    # invalidates it, which is the case D-133 was filed on.
    (Path(project_root) / "seed.txt").write_text("y", encoding="utf-8")
    subprocess.run(["git", "-C", project_root, "add", "seed.txt"], check=True)
    subprocess.run(["git", "-C", project_root, "commit", "-qm", "fix"], check=True)

    third = _terminal_evidence_sweep(fdir, project_root)
    assert third["cached"] is False
    assert len(calls) == 2, "HEAD moved and the memo was still trusted"
