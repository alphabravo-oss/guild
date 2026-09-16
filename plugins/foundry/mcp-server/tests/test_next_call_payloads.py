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


# --------------------------------------------------------------------------- #
# lead-stalls GI-005 / CT-005 / OT-006 (D-002) — the response, not the function.
# --------------------------------------------------------------------------- #


def _call_over_the_dispatcher(tool: str, arguments: dict) -> dict:
    """One `Foundry-*` call through `server.py#call_tool`, as a dict.

    The MCP boundary, not the handler. That distinction IS the defect below:
    everything the AST walk above proves is a property of
    `foundry_accept_casting`, and the payload a schema-refused caller receives
    is built before that function is entered.
    """
    import asyncio
    import json

    from foundry_mcp import server as _server
    from foundry_mcp.tools.display import RESULT_JSON_MARKER

    blocks = asyncio.run(_server.call_tool(tool, arguments))
    text = blocks[0].text
    # `format_result_blocks` emits the display half, then `RESULT_JSON_MARKER`,
    # then the whole result as JSON — but ONLY for a tool that has a formatter
    # (fallout D-173). An unformatted tool's text is the JSON and nothing else,
    # so the marker is read when it is there and the whole text parsed when it
    # is not, rather than assuming either shape.
    if RESULT_JSON_MARKER in text:
        text = text.split(RESULT_JSON_MARKER, 1)[1]
    return json.loads(text)


def test_a_schema_refused_accept_casting_still_names_the_next_call():
    """lead-stalls GI-005 / CT-005 / OT-006 — the pre-dispatch refusal path.

    THE DEFECT (D-002). `foundry_accept_casting` carries `LEAD_NEXT_CALL` on
    every one of its own returns and the AST walk above proves it. That proof
    is sound over the FUNCTION and says nothing about this payload, because
    this payload is built in `server.py` by `_argument_refusal` and returned
    BEFORE the handler runs — the refusal's own hint says so: "the server
    rejects anything outside it before the handler runs". Driven live, an
    `Foundry-Accept-Casting` call carrying `casting_id`, `spec_hash`,
    `prompt_hash` and `completion_report` but omitting the required
    `casting_commit` came back as exactly `{error, missing_fields,
    invalid_fields, hint}`: the lead at the end of a unit of work, handed a
    refusal plus a judgment task and no imperative, which is lead-stalls
    ST-003's shape exactly. A refusal that parks the run is a worse outcome
    than the refusal was meant to produce, which is why lead-stalls GI-005 says
    every path and not every SUCCESSFUL path.

    Driven over the dispatcher rather than asserted about `_argument_refusal`,
    because the claim is about what a CALLER receives. A test that read the
    helper directly would pass just as happily with the key added in the wrong
    place.
    """
    payload = _call_over_the_dispatcher(
        "Foundry-Accept-Casting",
        {
            "casting_id": "0",
            "spec_hash": "x",
            "prompt_hash": "x",
            "completion_report": "x",
        },
    )

    assert payload.get("missing_fields") == ["casting_commit"], payload
    assert payload.get("next_call") == _artifacts.LEAD_NEXT_CALL, payload


def test_the_shared_refusal_builder_is_left_byte_identical_for_other_tools():
    """lead-stalls FR-004 / NFR-002 — the blast radius the call-site fix avoids.

    `_argument_refusal` serves EVERY registered tool. The key could have been
    added inside it in one line, and that one line would have put `next_call`
    on ~40 other tools' refusal envelopes — payloads this spec does not name,
    which is the non-additive change lead-stalls FR-004 / NFR-002 forbids. It
    would also have changed the shape two other suites read from the helper
    directly (`tests/test_foundry_init.py` against Foundry-Init,
    `tests/test_defect_tier.py` against Foundry-Sync), which is how a fix
    scoped to one door turns up as a diff in somebody else's.

    So the negative half is pinned beside the positive one: a peer tool refused
    on the SAME rung, by the SAME builder, for the SAME reason, must come back
    without the key. `Foundry-Team-Down` is the peer chosen deliberately —
    lead-stalls GI-003 / CT-004 scope ITS `next_call` to the success payload,
    so a refusal carrying one would be this run's other door over-reaching.
    """
    from foundry_mcp import server as _server

    assert "Foundry-Team-Down" not in _server._NEXT_CALL_TOOLS, (
        _server._NEXT_CALL_TOOLS
    )

    payload = _call_over_the_dispatcher("Foundry-Team-Down", {})

    assert payload.get("missing_fields"), payload
    assert "next_call" not in payload, payload


def test_the_unhandled_error_banner_names_the_next_call_too(monkeypatch):
    """lead-stalls GI-005 / OT-006 — the refusal path's twin, one branch over.

    `call_tool` wraps the handler in a net that exists precisely for the case
    where the handler's own return never happened, so the banner it composes is
    the SECOND payload a caller receives from `Foundry-Accept-Casting` that the
    function's own returns cannot speak for. Covering the schema refusal and
    leaving this one would have shipped the same defect on the same rung
    against the same tool — and a lead reading an unhandled-error banner is, if
    anything, more in need of somewhere to go than one reading a refusal.

    The handler is replaced rather than provoked: what is being pinned is the
    boundary's behaviour when a handler raises, not any particular way of
    making one raise.
    """
    from foundry_mcp import server as _server

    def _boom(_args):
        raise RuntimeError("synthetic")

    monkeypatch.setitem(_server._DISPATCH, "Foundry-Accept-Casting", _boom)

    payload = _call_over_the_dispatcher(
        "Foundry-Accept-Casting",
        {
            "casting_id": "0",
            "spec_hash": "x",
            "prompt_hash": "x",
            "completion_report": "x",
            "casting_commit": "0" * 40,
        },
    )

    assert "RuntimeError: synthetic" in payload.get("error", ""), payload
    assert payload.get("next_call") == _artifacts.LEAD_NEXT_CALL, payload


def dispatch_refusal_report() -> list[str]:
    """The lines `evidence/casting-payloads-dispatch-refusal.log` carries.

    Here rather than inlined into a `# evidence-cmd:` one-liner so the evidence
    and the tests read the SAME code: a report spelled out in a shell string is
    a second implementation of the claim, free to drift from the one the suite
    drives. `tests/orchestration/test_guidance_imperatives.py#audit_evidence`
    is the same arrangement in the imperatives casting, and
    `test_the_evidence_report_agrees_with_the_tests_beside_it` below keeps this
    one from becoming dead code.

    Every value is derived at call time and none of it is environmental — no
    path, no duration, no count that depends on where the command ran — so the
    log re-executes byte-identically in the detached worktree the acceptance
    gate builds.
    """
    from foundry_mcp import server as _server

    lines: list[str] = []
    entered: list[int] = []

    args = {
        "casting_id": "0",
        "spec_hash": "x",
        "prompt_hash": "x",
        "completion_report": "x",
    }

    real = _server._DISPATCH["Foundry-Accept-Casting"]
    _server._DISPATCH["Foundry-Accept-Casting"] = (
        lambda a: entered.append(1) or real(a)
    )
    try:
        refusal = _call_over_the_dispatcher("Foundry-Accept-Casting", args)
    finally:
        _server._DISPATCH["Foundry-Accept-Casting"] = real

    def _boom(_a):
        raise RuntimeError("synthetic")

    _server._DISPATCH["Foundry-Accept-Casting"] = _boom
    try:
        banner = _call_over_the_dispatcher(
            "Foundry-Accept-Casting", dict(args, casting_commit="0" * 40)
        )
    finally:
        _server._DISPATCH["Foundry-Accept-Casting"] = real

    import asyncio

    schema = asyncio.run(_server._tool_schema("Foundry-Accept-Casting"))
    helper = _server._argument_refusal("Foundry-Accept-Casting", schema, args)

    lines.append("the pre-dispatch schema refusal of Foundry-Accept-Casting")
    lines.append("  missing_fields                       "
                 + str(refusal.get("missing_fields")))
    lines.append("  handler was entered                  " + str(bool(entered)))
    lines.append("  next_call on the response            "
                 + repr(refusal.get("next_call")))
    lines.append("  it is the leaf artifacts.py constant "
                 + str(refusal.get("next_call") == _artifacts.LEAD_NEXT_CALL))
    lines.append("")
    lines.append("the unhandled-error banner of the same tool")
    lines.append("  error names the raise                "
                 + str("RuntimeError: synthetic" in banner.get("error", "")))
    lines.append("  next_call on the response            "
                 + repr(banner.get("next_call")))
    lines.append("")
    lines.append("blast radius: the SAME rung, the other tools it serves")
    lines.append("  _argument_refusal itself carries it  "
                 + str("next_call" in helper))
    for peer in ("Foundry-Team-Down", "Foundry-Init", "Foundry-Spec-Hash"):
        payload = _call_over_the_dispatcher(peer, {})
        lines.append("  " + peer.ljust(36) + str("next_call" in payload))
    lines.append("  tools this boundary adds it for      "
                 + str(sorted(_server._NEXT_CALL_TOOLS)))
    return lines


def test_the_evidence_report_agrees_with_the_tests_beside_it():
    """The committed log's claims, re-derived here so neither can drift alone.

    `dispatch_refusal_report` exists to be printed into an evidence log that
    the acceptance gate re-executes. A report nothing drives is a second
    implementation of the claims above, and the failure mode is quiet: the log
    goes on reproducing byte-identically off code the suite never runs.
    """
    lines = dispatch_refusal_report()
    joined = "\n".join(lines)

    assert "  handler was entered                  False" in joined, joined
    assert joined.count(f"  next_call on the response            "
                        f"{_artifacts.LEAD_NEXT_CALL!r}") == 2, joined
    assert "  _argument_refusal itself carries it  False" in joined, joined
    for peer in ("Foundry-Team-Down", "Foundry-Init", "Foundry-Spec-Hash"):
        assert f"  {peer.ljust(36)}False" in joined, joined
