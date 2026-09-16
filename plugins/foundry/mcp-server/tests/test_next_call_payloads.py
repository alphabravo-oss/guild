"""The `next_call` payload contract on Foundry-Team-Down and Foundry-Accept-Casting.

lead-stalls FR-003 / FR-004 / FR-011 / NFR-002 / GI-003 / GI-005 / GI-008 /
CT-004 / CT-005 / ST-004 / OT-005 / OT-006 / US-001 / US-003.

THE DEFECT THESE PIN. The lead accepted the wave's last casting, tore the team
down, and received two payloads that between them described everything that had
just FINISHED and named nothing to CALL — so it reported status and ended its
turn with no agent running and the run neither DONE nor HALTED (lead-stalls
ST-003). Every key in both payloads was true. None of them was an instruction.

WHY AN AST WALK AND NOT ONLY DRIVEN CALLS. lead-stalls GI-005 / OT-006 are a
claim about EVERY return path of `foundry_accept_casting`, and several of those paths cost a
synthesized git repo, a detached worktree and a re-executed evidence command to
reach — so a suite that drove them all would still only prove the twelve that
existed on the day it was written. `test_every_accept_casting_return_path_
carries_next_call` reads the shipped function's own syntax tree instead, which
makes "every path" a property the thirteenth return has to satisfy too. The
driven tests beside it prove the key is really in the dict a caller receives,
which the AST walk alone cannot say.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from foundry_mcp.tools import artifacts as _artifacts
from foundry_mcp.tools import evidence as _evidence
from foundry_mcp.tools.evidence import foundry_accept_casting
from foundry_mcp.tools.orchestration import teams as _teams
from foundry_mcp.tools.orchestration.teams import foundry_unregister_team

from tests.orchestration._env import (  # noqa: F401
    _write_state,
    run_env,
)


# The five keys `Foundry-Team-Down` returned on success BEFORE this change.
# Named here rather than derived, because lead-stalls OT-005's claim is that
# the additive key joins these five — a set computed from the current return would agree
# with itself no matter what the return had been changed to.
TEAM_DOWN_PRE_EXISTING_KEYS = frozenset(
    {"ok", "unregistered", "remaining_teams", "tmux_panes_killed", "verified_clean"}
)


def _accept_casting_returns() -> list[ast.Return]:
    """Every `return` in the SHIPPED `foundry_accept_casting`, as AST nodes.

    Read off `evidence.__file__` rather than a hand-typed path so the walk
    follows the module the tests actually import; a path literal would keep
    passing against a file the package no longer ships.
    """
    source = Path(_evidence.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    fn = next(
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == "foundry_accept_casting"
    )
    return [n for n in ast.walk(fn) if isinstance(n, ast.Return)]


def _carries_next_call(node: ast.Return) -> bool:
    """True when this return's value is a dict literal with a `next_call` key.

    `{**helper(...), "next_call": ...}` satisfies this: a dict display with a
    `**` unpacking carries a `None` in `keys`, and the literal key sits beside
    it. That shape is REQUIRED at the two helper-delegating returns rather than
    incidental — `document_refusal` and `check_reported_prompt_hash` are shared
    with five other doors, so the key has to be added at the call site.
    """
    value = node.value
    if not isinstance(value, ast.Dict):
        return False
    return any(
        isinstance(key, ast.Constant) and key.value == "next_call"
        for key in value.keys
    )


def test_every_accept_casting_return_path_carries_next_call():
    """lead-stalls GI-005 / OT-006 / CT-005 — every path, success and refusal.

    A `return` that is not a dict literal fails here too, and deliberately: it
    is the shape that hid two of the twelve paths from the `return {` sweep
    this change was planned against. `return some_helper(...)` cannot be shown
    to carry the key without reading the helper, and the helper is shared.
    """
    returns = _accept_casting_returns()
    assert returns, "no returns found — the AST walk lost the function"
    missing = sorted(n.lineno for n in returns if not _carries_next_call(n))
    assert not missing, (
        f"{len(missing)} of {len(returns)} return paths in foundry_accept_casting "
        f"omit `next_call`, at line(s) {missing}. lead-stalls GI-005 is every "
        f"path, including "
        f"every refusal. A helper-delegating return takes the key at the CALL "
        f"SITE: `return {{**helper(...), 'next_call': LEAD_NEXT_CALL}}`."
    )


def test_accept_casting_refusal_carries_next_call(run_env):
    """lead-stalls ST-004 / US-003 — a REFUSED acceptance still routes the lead.

    Driven at the `casting_commit` rung — the first refusal on the ladder and
    the one a lead hits before any artifact of the run is even read. A refusal
    that names the remedy and no next call is how a rejected casting parks the
    run.
    """
    project_root, fdir = run_env
    _write_state(fdir, phase="F1", cycle=1)

    refusal = foundry_accept_casting(
        casting_id=1,
        spec_hash="whatever",
        prompt_hash="whatever",
        completion_report="built it",
        project_root=project_root,
        casting_commit=None,
    )

    assert refusal["ok"] is False, refusal
    assert refusal["field"] == "casting_commit", refusal
    assert refusal["next_call"] == _evidence.LEAD_NEXT_CALL, refusal


def test_accept_casting_refusal_keeps_every_key_it_had(run_env):
    """lead-stalls FR-004 / NFR-002 — additive, so nothing downstream breaks.

    The refusal's own four keys are asserted by name. `next_call` is the only
    addition — a refusal that gained a next call and lost `hint` would satisfy
    lead-stalls GI-005 and still cost the lead the remedy.
    """
    project_root, fdir = run_env
    _write_state(fdir, phase="F1", cycle=1)

    refusal = foundry_accept_casting(
        casting_id=1,
        spec_hash="whatever",
        prompt_hash="whatever",
        completion_report="built it",
        project_root=project_root,
        casting_commit=None,
    )

    assert set(refusal) == {
        "ok",
        "error",
        "hint",
        "casting_id",
        "field",
        "next_call",
    }, refusal


def _no_panes(*_args, **_kwargs) -> dict:
    """A tmux scan that answers "nothing running", without asking tmux.

    `run_env` patches `_check_active_teams`, which is the OTHER half of the
    team question; `foundry_unregister_team` reads `live_teammate_panes`
    directly and would otherwise shell out to whatever tmux session happens to
    be running the suite — so the success payload would be reachable on one
    machine and refused on the next.
    """
    return {"available": False, "live": [], "zombie": [], "user": [], "lead": None}


def test_team_down_success_payload_gains_exactly_one_key(run_env, monkeypatch):
    """lead-stalls OT-005 / CT-004 / FR-003 — the five keys, plus `next_call`.

    Asserted as an exact set difference rather than a membership check, because
    lead-stalls GI-003's violation shape is "renaming or removing an existing response key
    instead of adding one" — which a bare `assert "next_call" in result` passes
    happily while `verified_clean` has silently become `clean`.
    """
    project_root, fdir = run_env
    _write_state(fdir, phase="F3", cycle=1)
    monkeypatch.setattr(_teams, "live_teammate_panes", _no_panes)

    result = foundry_unregister_team("a-team-with-no-directory", project_root)

    assert result.get("ok") is True, result
    assert set(result) - TEAM_DOWN_PRE_EXISTING_KEYS == {"next_call"}, result
    assert TEAM_DOWN_PRE_EXISTING_KEYS <= set(result), result
    assert result["next_call"] == _teams.LEAD_NEXT_CALL, result


def test_team_down_refusal_does_not_claim_a_next_call(run_env, monkeypatch):
    """lead-stalls GI-003 / CT-004 scope this door's key to the SUCCESS payload.

    lead-stalls GI-005's every-path rule belongs to Accept-Casting, and the difference is
    not an oversight: Team-Down's refusals mean the teardown did not happen, and
    each already names the ordered remedy in `hint`. Telling the lead to move on
    from a door that just refused to let it is the failure this asserts against.
    """
    project_root, fdir = run_env
    _write_state(fdir, phase="F3", cycle=1)
    monkeypatch.setattr(
        _teams,
        "live_teammate_panes",
        lambda *_a, **_k: {
            "available": True,
            "live": [("%1", "teammate-casting-payloads", "claude")],
            "zombie": [],
            "user": [],
            "lead": None,
        },
    )

    refused = foundry_unregister_team("a-team-with-no-directory", project_root)

    assert refused.get("error"), refused
    assert refused.get("hint"), refused
    assert "next_call" not in refused, refused


def test_both_doors_name_the_same_next_call():
    """lead-stalls FR-011 pins the wording, and both doors are pinned to ONE object.

    `is`, not `==`. `teams.py` is LIFECYCLE and `evidence.py` is a VERIFIER and
    neither may import the other at any depth (fallout AC-061), so a per-module copy
    was the first shape tried here — and
    `test_no_top_level_symbol_is_defined_in_two_shipped_modules` refused it.
    The constant lives in `tools/artifacts.py`, the leaf BOTH doors already
    import from, and an identity check is what says so: two equal strings would
    pass `==` on the day someone re-forks the literal, which is the whole
    failure that guard exists to prevent.
    """
    assert _artifacts.LEAD_NEXT_CALL == "Call Foundry-Next now.", (
        _artifacts.LEAD_NEXT_CALL
    )
    assert _evidence.LEAD_NEXT_CALL is _artifacts.LEAD_NEXT_CALL
    assert _teams.LEAD_NEXT_CALL is _artifacts.LEAD_NEXT_CALL


@pytest.mark.parametrize(
    "value",
    [_evidence.LEAD_NEXT_CALL, _teams.LEAD_NEXT_CALL],
    ids=["accept_casting", "team_down"],
)
def test_next_call_names_a_literal_tool_call_with_no_conditional(value):
    """lead-stalls GI-008 / GI-004: one unconditional imperative, and never a wait loop.

    The defect this run fixes is prose that handed the lead a branch to
    evaluate instead of a call to make, so a `next_call` that reintroduced one
    would be the same defect in a new key. `wait` is matched as a whole word:
    it is the instruction that cost the run its turn, while "waiting" inside a
    larger word is not what lead-stalls GI-004 is about.
    """
    assert "Foundry-Next" in value, value

    lowered = value.lower()
    for conditional in (" if ", "if ", "whether", "unless", "otherwise", "depending"):
        assert not lowered.startswith(conditional), value
        assert conditional not in lowered, value

    for stalling in ("sleep", "poll", "loop", "retry until", "wait"):
        assert stalling not in lowered.split() and stalling not in lowered, value
