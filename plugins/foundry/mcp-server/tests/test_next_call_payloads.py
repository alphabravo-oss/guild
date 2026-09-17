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
import contextlib
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from foundry_mcp.tools import artifacts as _artifacts
from foundry_mcp.tools import evidence as _evidence
from foundry_mcp.tools import foundry_state as _foundry_state
from foundry_mcp.tools.artifacts import _hash_file, foundry_spec_hash
from foundry_mcp.tools.evidence import foundry_accept_casting
from foundry_mcp.tools.foundry_spawn import foundry_spawn_teammate
from foundry_mcp.tools.orchestration import teams as _teams
from foundry_mcp.tools.orchestration.guidance import foundry_next_action
from foundry_mcp.tools.orchestration.teams import (
    foundry_register_team,
    foundry_unregister_team,
)

from tests.orchestration._env import (  # noqa: F401
    _write_state,
    patch_everywhere,
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


# --------------------------------------------------------------------------- #
# lead-stalls ST-004 / US-003 / GI-008 (D-018, D-019, D-020) — WHERE THE KEY
# LEADS, NOT ONLY THAT IT IS THERE.
#
# Everything above proves `next_call` is on the payload. D-020 drove the next
# hop: a refused acceptance, then the `Foundry-Next` its key names, and the
# lead was told to tear the wave down (team registered) or to dispatch the
# NEXT wave over the refused casting (team torn down). A-014's premise was
# "Foundry-Next already knows whether the casting was accepted", and nothing
# in the server read acceptance: the one record this door wrote said
# `casting-{id}-accepted` even on a warned `ok: False`.
#
# So these drive the real handlers in sequence — `foundry_register_team`,
# `foundry_accept_casting`, `foundry_unregister_team`, `foundry_next_action` —
# against a scratch HOME holding the team directory `TeamCreate` makes. Only
# the tmux pane scan is stubbed. `run_env`'s team-scan fake is deliberately NOT
# used: it answers the router's team question without the registration, which
# is how every earlier suite stayed blind to the registered-team arm (D-015).
# --------------------------------------------------------------------------- #

_RUN = "payloads-route"
_WAVE_ONE_TEAM = f"cast-{_RUN}-wave-1"
_ROUTE_PROMPT = (
    "# casting\n\n<spec_requirements>\n- build the thing\n</spec_requirements>\n"
)
_NEXT_ACTION_MARKER = "═══ YOUR NEXT ACTION ═══"

#: What a lead is told when the router sends it to TEAR THE WAVE DOWN, and
#: what it is told when the router sends it to DISPATCH THE NEXT WAVE. A
#: refused or unaccepted casting owes neither: commands/start.md answers a
#: refusal with "reject + re-dispatch".
_TEARDOWN_CALLS = ("TeamDelete", "Foundry-Team-Down")
_NEXT_WAVE_CALL = "Foundry-Cast-Wave(wave=2"


@contextlib.contextmanager
def _scratch_run(base: Path, monkeypatch):
    """A live run under ``base`` whose team question is answered for real.

    HOME is ``base/home``, so `Path.home() / ".claude" / "teams"` — where both
    `foundry_register_team` and `foundry_state.active_teams` look — is a
    directory this drive owns. Yields ``(project_root, fdir, teams_dir)``.
    """
    home = base / "home"
    teams_dir = home / ".claude" / "teams"
    teams_dir.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    patch_everywhere(monkeypatch, "live_teammate_panes", _no_panes)
    root = base / "proj"
    fdir = root / "foundry-archive" / _RUN
    (fdir / "castings").mkdir(parents=True)
    _foundry_state.set_active_run(_RUN)
    try:
        yield str(root), fdir, teams_dir
    finally:
        _foundry_state.clear_active_run()


def _arrange_waves(fdir: Path, waves: dict[int, list[str]]) -> None:
    """F1, a v2.0 spec, a waved manifest, and one prompt per casting.

    v2.0 because that is the evidence rung's stream-skip branch: it answers
    without a git repository, so the acceptance verdict these tests are about
    is reached through the real handler and nothing else is synthesized.
    """
    _write_state(fdir, phase="F1", cycle=0)
    (fdir / "spec.md").write_text("# Spec\n\n- build the thing\n", encoding="utf-8")
    (fdir / "castings" / "manifest.json").write_text(
        json.dumps({
            "castings": [
                {"id": cid, "title": cid, "wave": number}
                for number, ids in sorted(waves.items()) for cid in ids
            ],
            "waves": [
                {"wave": number, "casting_ids": list(ids)}
                for number, ids in sorted(waves.items())
            ],
        }),
        encoding="utf-8",
    )
    for ids in waves.values():
        for cid in ids:
            (fdir / "castings" / f"casting-{cid}-prompt.md").write_text(
                _ROUTE_PROMPT, encoding="utf-8"
            )


def _worked_ledger(fdir: Path, cid: str, *, done: bool, stamp: str | None = None) -> None:
    """A casting's progress ledger: still writing, or its terminal done line."""
    now = stamp or _foundry_state.now_iso()
    lines = [{"timestamp": now, "phase": "cast", "step": "writing the handler"}]
    if done:
        lines.append(
            {"timestamp": now, "phase": "cast", "step": "committed", "done": True}
        )
    (fdir / "progress").mkdir(parents=True, exist_ok=True)
    (fdir / "progress" / f"casting-{cid}.jsonl").write_text(
        "".join(json.dumps(line) + "\n" for line in lines), encoding="utf-8"
    )


def _accept(root: str, fdir: Path, cid: str, **overrides) -> dict:
    """`foundry_accept_casting` with every argument honest unless overridden."""
    args = {
        "casting_id": cid,
        "spec_hash": foundry_spec_hash(project_root=root)["spec_hash"],
        "prompt_hash": _hash_file(fdir / "castings" / f"casting-{cid}-prompt.md"),
        "completion_report": "built the thing",
        "project_root": root,
        "casting_commit": "0" * 40,
    }
    args.update(overrides)
    return foundry_accept_casting(**args)


def _acceptance_destinations(fdir: Path) -> list[str]:
    """Every `acceptance` record's destination, in the order it was written."""
    path = fdir / "handoffs.jsonl"
    if not path.exists():
        return []
    return [
        record["destination"]
        for record in (
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        )
        if record.get("event") == "acceptance"
    ]


def _header(nxt: dict) -> str:
    """The imperative the lead is handed: after the marker, before CONTEXT."""
    text = nxt.get("instructions", "")
    tail = text.split(_NEXT_ACTION_MARKER, 1)[1] if _NEXT_ACTION_MARKER in text else text
    return tail.split("\nCONTEXT:", 1)[0]


#: The acceptance outcomes these drives produce, as the overrides that produce
#: them. `stale_spec_hash` is D-020's own drive: a refused CALL, which records
#: nothing, so the casting reads as done and never accepted. The other two are
#: verdicts on the CASTING and record `-refused`.
_REFUSALS = {
    "stale_spec_hash": {"spec_hash": "0" * 64},
    "stale_prompt_hash": {"prompt_hash": "sha256:0000000000000000"},
    "warned": {"completion_report": "built the thing; the tests are deferred"},
}

#: The first call the router names for each refusal, by whether casting 1's
#: team is still registered. A refused CALL leaves the casting done and
#: unjudged, which owes acceptance made correctly — a fresh spec hash first,
#: whatever the team state. A refused CASTING goes back to a teammate by
#: message, so the CAST dispatch block is never augmented (commands/start.md
#: rule 1): to the teammate that built it while its team stands, and to a
#: fresh one, dispatched first, once the team is gone (lead-stalls D-022).
def _owed_first_call(refusal: str, team_registered: bool) -> str:
    if refusal == "stale_spec_hash":
        return "Foundry-Spec-Hash"
    return "SendMessage" if team_registered else "Foundry-Spawn-Teammate"


#: The stable opening of the send-back branch's step (1).
_REFUSAL_TO_TEAMMATE = "(1) SendMessage(to=<the teammate you spawned for casting 1>"


def _refused_to_casting(header: str, cid: str) -> bool:
    """True when ``header`` is a refused branch, forwarding casting ``cid``'s refusal."""
    return f"the refusal Foundry-Accept-Casting returned for casting {cid}" in header


#: The two refused branches the team state selects (lead-stalls D-022), each
#: as the calls it opens with and the reading it states. Each branch carries
#: its own and none of the other's.
_REFUSED_BRANCH = {
    True: (_REFUSAL_TO_TEAMMATE, "own wave team is still registered"),
    False: (
        "(1) Foundry-Spawn-Teammate(casting_id=1, phase='cast')",
        "(3) SendMessage(to=<the teammate step (2) spawned>",
        "own wave team is not registered",
    ),
}

#: What D-022's single refused branch said: a step taken only on the answer an
#: earlier call returned, and a sentence predicting that answer. Under
#: lead-stalls GI-008 / CT-008 the lead receives one unconditional imperative,
#: so no header either branch emits may carry any of them.
_CONDITIONED_STEP = (
    "Only a send",
    "cannot be reached",
    "is expected to reach",
    "may answer",
    " unless ",
    "whether",
    "otherwise",
    "depending",
)


def _refuse_then_follow(
    base: Path, monkeypatch, *, refusal: str | None, team_registered: bool
) -> dict:
    """Wave 1 = casting 1, worked and done; wave 2 = casting 2, not started.

    Casting 1's acceptance is refused as ``refusal`` names (``None`` accepts
    it), the team is registered or already torn down, and then the call the
    payload's `next_call` names is made. Returns what each hop answered.
    """
    with _scratch_run(base, monkeypatch) as (root, fdir, teams_dir):
        _arrange_waves(fdir, {1: ["1"], 2: ["2"]})
        if team_registered:
            (teams_dir / _WAVE_ONE_TEAM).mkdir()
            up = foundry_register_team(_WAVE_ONE_TEAM, project_root=root)
            assert up.get("ok") is True, up
        _worked_ledger(fdir, "1", done=True)
        payload = _accept(root, fdir, "1", **_REFUSALS.get(refusal, {}))
        nxt = foundry_next_action(root)
        return {
            "payload": payload,
            "destinations": _acceptance_destinations(fdir),
            "action": nxt.get("action"),
            "header": _header(nxt),
        }


@pytest.mark.parametrize(
    "refusal, recorded",
    [
        ("stale_prompt_hash", ["casting-1-refused"]),
        ("warned", ["casting-1-refused"]),
        (None, ["casting-1-accepted"]),
    ],
    ids=["stale_prompt_hash", "warned", "accepted"],
)
def test_the_acceptance_verdict_is_recorded_as_the_payload_states_it(
    tmp_path, monkeypatch, refusal, recorded
):
    """lead-stalls ST-004 / US-003 (D-020) — the WRITER half.

    The warned row is the one that was wrong on disk: `ok: False`, "Do NOT
    accept this casting", recorded as `casting-1-accepted`. The prompt-hash
    row wrote nothing at all. A router cannot send a lead back to a refusal it
    has no record of, whatever it is taught to read.
    """
    drive = _refuse_then_follow(
        tmp_path, monkeypatch, refusal=refusal, team_registered=False
    )

    assert drive["payload"]["ok"] is (refusal is None), drive["payload"]
    assert drive["payload"]["next_call"] == _artifacts.LEAD_NEXT_CALL, drive
    assert drive["destinations"] == recorded, drive


def test_a_refused_call_leaves_the_casting_verdict_standing(tmp_path, monkeypatch):
    """lead-stalls ST-004 — a malformed CALL is not a verdict on the casting.

    The rungs above the prompt load judge the call's own arguments. Recording
    them would let a lead's stale re-call flip an accepted casting to refused
    and send its teammate back to rework a casting that passed.
    """
    with _scratch_run(tmp_path, monkeypatch) as (root, fdir, _teams_dir):
        _arrange_waves(fdir, {1: ["1"]})
        assert _accept(root, fdir, "1")["ok"] is True
        for overrides in (
            {"spec_hash": "0" * 64},
            {"casting_commit": None},
        ):
            refused = _accept(root, fdir, "1", **overrides)
            assert refused["ok"] is False, refused
            assert refused["next_call"] == _artifacts.LEAD_NEXT_CALL, refused

        assert _acceptance_destinations(fdir) == ["casting-1-accepted"]


def _judged_return_lines() -> tuple[list[int], list[int]]:
    """``(judged, unrecorded)`` return lines of the SHIPPED `foundry_accept_casting`.

    A return is JUDGED when it sits below the stale-spec-hash rung — the last
    rung that refuses the CALL rather than the casting — and UNRECORDED when
    the statement before it in its own block is not a
    `_record_acceptance_verdict(...)` call.
    """
    source = Path(_evidence.__file__).read_text(encoding="utf-8")
    fn = next(
        node for node in ast.parse(source).body
        if isinstance(node, ast.FunctionDef) and node.name == "foundry_accept_casting"
    )
    call_rung = max(
        node.lineno for node in ast.walk(fn)
        if isinstance(node, ast.Constant) and node.value == "stale_spec_hash"
    )

    def _records(stmt: ast.stmt) -> bool:
        return (
            isinstance(stmt, ast.Expr)
            and isinstance(stmt.value, ast.Call)
            and isinstance(stmt.value.func, ast.Name)
            and stmt.value.func.id == "_record_acceptance_verdict"
        )

    judged: list[int] = []
    unrecorded: list[int] = []
    for node in ast.walk(fn):
        for field in ("body", "orelse", "finalbody"):
            block = getattr(node, field, None)
            if not isinstance(block, list):
                continue
            for index, stmt in enumerate(block):
                if not isinstance(stmt, ast.Return) or stmt.lineno < call_rung:
                    continue
                judged.append(stmt.lineno)
                if index == 0 or not _records(block[index - 1]):
                    unrecorded.append(stmt.lineno)
    return sorted(judged), sorted(unrecorded)


def test_every_judged_return_path_records_its_verdict_first():
    """lead-stalls ST-004 / GI-005 — "every path" for the RECORD, as for the key.

    The same argument as the `next_call` walk at the top of this module: most
    of these paths cost a worktree and a re-executed command to reach, so a
    suite that drove them all would still only prove the ones that existed on
    the day it was written. Every judged `return` must be preceded, in its own
    block, by a `_record_acceptance_verdict(...)` statement.
    """
    judged, unrecorded = _judged_return_lines()

    assert len(judged) >= 8, judged
    assert not unrecorded, (
        f"return(s) at line(s) {unrecorded} in foundry_accept_casting judge the "
        f"casting and record no verdict — the router reads that record to send "
        f"a refused casting back to its teammate (lead-stalls ST-004, D-020)."
    )


@pytest.mark.parametrize("team_registered", [True, False], ids=["team_up", "team_down"])
@pytest.mark.parametrize("refusal", sorted(_REFUSALS))
def test_following_next_call_off_a_refusal_returns_to_that_casting(
    tmp_path, monkeypatch, refusal, team_registered
):
    """lead-stalls ST-004 / US-003 (D-020, D-022) — the refusal routes back, not on.

    lead-stalls ST-004 is "casting rejected -> lead re-accepting", triggered by "lead
    follows the `next_call` on the reject payload". Driven at c5045c2 the
    `Foundry-Next` that key names answered `cleanup_teams` with the team up —
    shut the teammate down and delete its team — and, with the team down, the
    wave-2 dispatch over a casting nothing had accepted. Neither is the move a
    refusal owes. The router has to name casting 1 and the door that settles
    it — and, for a refused casting, name ONE branch: D-022 found the send-back
    and the re-dispatch in one header, the second taken "only" on the first's
    answer, which is a conditional lead-stalls GI-008 / CT-008 forbid.
    """
    drive = _refuse_then_follow(
        tmp_path, monkeypatch, refusal=refusal, team_registered=team_registered
    )

    assert drive["payload"]["next_call"] == _artifacts.LEAD_NEXT_CALL, drive
    assert drive["action"] != "cleanup_teams", drive
    assert not _tears_down(drive["header"]), drive
    assert _NEXT_WAVE_CALL not in drive["header"], drive
    assert "Foundry-Gate(phase='inspect')" not in drive["header"], drive
    assert _returns_to_casting_one(drive["header"]), drive
    header = drive["header"]
    first = _owed_first_call(refusal, team_registered)
    assert _first_call(header) == first, drive
    assert not _conditioned(header), drive
    if first == "Foundry-Spec-Hash":
        assert "Foundry-Accept-Casting(casting_id=1, " in header, drive
        return
    # lead-stalls D-022 — exactly ONE refused branch, the one this team state
    # selects, and no step of it waits on the answer to an earlier one.
    assert _refused_to_casting(header, "1"), drive
    for marker in _REFUSED_BRANCH[team_registered]:
        assert marker in header, (marker, drive)
    for marker in _REFUSED_BRANCH[not team_registered]:
        assert marker not in header, (marker, drive)
    assert "END YOUR TURN" in header, drive
    if team_registered:
        assert "Foundry-Spawn-Teammate" not in header, drive
        assert "(2) " + "Waiting on a running agent" in header, drive
        assert "(3) " not in header, drive
    else:
        assert "casting 1>" not in header, drive
        # lead-stalls D-030 — the re-spawned teammate is handed its ledger.
        assert "nothing appended" not in _redispatch_step(header), drive
        assert "`progress_protocol` block" in _redispatch_step(header), drive
        assert (
            header.index("(1) Foundry-Spawn-Teammate")
            < header.index("(2) One foreground Agent(")
            < header.index("(3) SendMessage(")
            < header.index("(4) Waiting on a running agent")
        ), drive


def _refuse_then_rework_then_follow(base: Path, monkeypatch) -> dict:
    """Casting 1 refused, then its teammate answers and writes done AGAIN.

    The order is the real one, in real time: the refusal's record is written
    by the door, and the done line after it by the ledger writer — nothing is
    back-dated. Team torn down, so only the acceptance branches can answer.
    """
    with _scratch_run(base, monkeypatch) as (root, fdir, _teams_dir):
        _arrange_waves(fdir, {1: ["1"], 2: ["2"]})
        _worked_ledger(fdir, "1", done=True)
        refused = _accept(root, fdir, "1", **_REFUSALS["warned"])
        _worked_ledger(fdir, "1", done=True)
        nxt = foundry_next_action(root)
    return {
        "payload": refused,
        "destinations": _acceptance_destinations(fdir),
        "action": nxt.get("action"),
        "header": _header(nxt),
    }


def test_a_refusal_the_teammate_has_answered_is_owed_acceptance_again(
    tmp_path, monkeypatch
):
    """lead-stalls ST-004 — "casting rejected -> lead RE-ACCEPTING".

    A `-refused` verdict OLDER than the casting's latest done line has been
    answered: the teammate the refusal was sent to reworked the casting and
    declared itself done again. Sending it the same refusal a second time would
    throw that work away, so the router owes the acceptance call — the other
    half of the refused/send-back pair the route test above pins.
    """
    drive = _refuse_then_rework_then_follow(tmp_path, monkeypatch)

    assert drive["payload"]["ok"] is False, drive
    assert drive["destinations"] == ["casting-1-refused"], drive
    assert _first_call(drive["header"]) == "Foundry-Spec-Hash", drive
    assert "Foundry-Accept-Casting(casting_id=1, " in drive["header"], drive
    assert _REFUSAL_TO_TEAMMATE not in drive["header"], drive
    assert "Foundry-Spawn-Teammate" not in drive["header"], drive
    assert not _tears_down(drive["header"]), drive
    assert _NEXT_WAVE_CALL not in drive["header"], drive


class _SameSecond(datetime):
    """`datetime` whose `now` is one whole second, for `artifacts`' handoff writer.

    `record_handoff_event` stamps `datetime.now(timezone.utc).isoformat()`, so
    frozen at `microsecond=0` it writes the same whole-second string a done
    line carries — the tie `_owed_acceptances` documents and D-025 found
    unpinned.
    """

    frozen: datetime = datetime(2026, 9, 16, 12, 0, 0, tzinfo=timezone.utc)

    @classmethod
    def now(cls, tz=None):
        return cls.frozen if tz is None else cls.frozen.astimezone(tz)


def _owed_then_follow(
    base: Path,
    monkeypatch,
    *,
    waves: dict[int, list[str]],
    refused: list[str],
    team_registered: bool,
    same_second: bool = False,
    team_wave: int = 1,
) -> dict:
    """Every casting in waves 1..``team_wave`` worked and done; ``refused`` refused
    through the real door, in the order listed; every other one never
    accepted. Then Foundry-Next.

    ``same_second`` stamps each done line and each refusal record with the one
    whole second both writers would print inside a single second — the done
    line written first, as a teammate's is. ``team_wave`` is the wave whose
    CAST team ``team_registered`` registers; every wave up to it is worked.
    """
    with _scratch_run(base, monkeypatch) as (root, fdir, teams_dir):
        _arrange_waves(fdir, waves)
        if team_registered:
            team = f"cast-{_RUN}-wave-{team_wave}"
            (teams_dir / team).mkdir()
            assert foundry_register_team(team, project_root=root)["ok"]
        stamp = None
        if same_second:
            monkeypatch.setattr(_artifacts, "datetime", _SameSecond)
            stamp = _SameSecond.frozen.isoformat()
        for number in range(1, team_wave + 1):
            for cid in waves[number]:
                _worked_ledger(fdir, cid, done=True, stamp=stamp)
        payloads = {
            cid: _accept(root, fdir, cid, **_REFUSALS["warned"]) for cid in refused
        }
        nxt = foundry_next_action(root)
        records = [
            json.loads(line)
            for line in (fdir / "handoffs.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    return {
        "payloads": payloads,
        "destinations": _acceptance_destinations(fdir),
        "stamps": [r.get("timestamp") for r in records if r.get("event") == "acceptance"],
        "done_stamp": stamp,
        "action": nxt.get("action"),
        "liveness": nxt.get("agent_liveness") or {},
        "header": _header(nxt),
    }


def _names_only_casting(header: str, cid: str, others: list[str]) -> bool:
    """True when every casting ``header`` names is ``cid``.

    Both spellings the branches use — `casting_id=N` in a call and `casting N`
    in prose — so a header that names the right casting in its call and the
    wrong one in the refusal it forwards is still caught.
    """
    def _named(some: str) -> bool:
        return bool(
            re.search(rf"casting_id={re.escape(some)}\b", header)
            or re.search(rf"casting {re.escape(some)}\b", header)
        )

    return _named(cid) and not any(_named(other) for other in others)


@pytest.mark.parametrize("team_registered", [True, False], ids=["team_up", "team_down"])
def test_a_refusal_stamped_in_the_done_lines_second_is_still_refused(
    tmp_path, monkeypatch, team_registered
):
    """lead-stalls ST-004 / US-003 (D-025) — the tie goes to the teammate.

    The done line is written BEFORE the call that refuses it and may carry
    only whole seconds, so a refusal inside that second prints the same
    timestamp. That refusal has not been answered: the router must send it
    back to the teammate, not owe a re-acceptance of a casting nobody changed.
    `refused_at > finished_at` reads the tie the other way.
    """
    drive = _owed_then_follow(
        tmp_path, monkeypatch, waves={1: ["1"], 2: ["2"]}, refused=["1"],
        team_registered=team_registered, same_second=True,
    )

    assert drive["destinations"] == ["casting-1-refused"], drive
    assert drive["stamps"] == [drive["done_stamp"]], drive
    assert drive["payloads"]["1"]["next_call"] == _artifacts.LEAD_NEXT_CALL, drive
    assert _first_call(drive["header"]) != "Foundry-Spec-Hash", drive
    assert "Foundry-Accept-Casting(casting_id=1, " not in drive["header"], drive
    assert _refused_to_casting(drive["header"], "1"), drive
    assert not _tears_down(drive["header"]), drive
    assert _NEXT_WAVE_CALL not in drive["header"], drive


@pytest.mark.parametrize("team_registered", [True, False], ids=["team_up", "team_down"])
def test_two_refused_castings_are_settled_in_manifest_order(
    tmp_path, monkeypatch, team_registered
):
    """lead-stalls ST-004 / US-003 (D-026) — `{casting}` is the FIRST owed.

    Both wave-1 castings are refused; casting 2 is refused first, so neither
    "the latest refusal" nor "the earliest" would agree with manifest order by
    accident. The branch names casting 1, and only casting 1, everywhere.
    """
    drive = _owed_then_follow(
        tmp_path, monkeypatch, waves={1: ["1", "2"], 2: ["3"]}, refused=["2", "1"],
        team_registered=team_registered,
    )

    assert drive["destinations"] == ["casting-2-refused", "casting-1-refused"], drive
    assert _refused_to_casting(drive["header"], "1"), drive
    assert _names_only_casting(drive["header"], "1", ["2", "3"]), drive
    assert not _tears_down(drive["header"]), drive


@pytest.mark.parametrize("team_registered", [True, False], ids=["team_up", "team_down"])
def test_a_refusal_goes_to_its_own_casting_beside_an_unaccepted_one(
    tmp_path, monkeypatch, team_registered
):
    """lead-stalls ST-004 / US-003 (D-027) — `{casting}` belongs to the branch.

    Casting 1 is done and never accepted; casting 2 is done and refused;
    nobody is running. The refused branch is chosen, so the casting it names
    and whose refusal it forwards is casting 2 — casting 1 comes FIRST in the
    manifest, which is exactly the id a slot reading the unaccepted list first
    would send casting 2's refusal to.
    """
    drive = _owed_then_follow(
        tmp_path, monkeypatch, waves={1: ["1", "2"], 2: ["3"]}, refused=["2"],
        team_registered=team_registered,
    )

    assert drive["destinations"] == ["casting-2-refused"], drive
    assert _refused_to_casting(drive["header"], "2"), drive
    assert _names_only_casting(drive["header"], "2", ["1", "3"]), drive
    assert not _tears_down(drive["header"]), drive


def test_a_refusal_is_sent_back_by_its_own_waves_team_not_a_later_one(
    tmp_path, monkeypatch
):
    """lead-stalls FR-015 / ST-004 / US-003 (D-036) — the FIRST refused id's wave.

    Casting 1 (wave 1) and casting 2 (wave 2) are both done and refused, and
    only `cast-{run}-wave-2` is registered: wave 1's team came down before
    wave 2 went up. `{casting}` is casting 1, so the send-back choice has to
    read casting 1's wave team. Reading the LAST refused id's wave finds wave
    2's team standing, and the lead messages a casting-1 teammate whose team
    is gone and ends its turn with nothing running (lead-stalls ST-003).
    """
    drive = _owed_then_follow(
        tmp_path, monkeypatch, waves={1: ["1"], 2: ["2"]}, refused=["2", "1"],
        team_registered=True, team_wave=2,
    )

    assert drive["destinations"] == ["casting-2-refused", "casting-1-refused"], drive
    assert drive["liveness"].get("teams_registered") == [f"cast-{_RUN}-wave-2"], drive
    assert drive["liveness"].get("cast_refused_team") == _WAVE_ONE_TEAM, drive
    assert (drive["action"], _first_call(drive["header"])) == (
        "build_castings", "Foundry-Spawn-Teammate"
    ), drive
    assert _refused_to_casting(drive["header"], "1"), drive
    assert _REFUSAL_TO_TEAMMATE not in drive["header"], drive
    assert "own wave team is not registered" in drive["header"], drive
    assert not _tears_down(drive["header"]), drive


@pytest.mark.parametrize("team_registered", [True, False], ids=["team_up", "team_down"])
def test_an_accepted_wave_still_moves_on(tmp_path, monkeypatch, team_registered):
    """The adjacent path the reader must not block: casting 1 ACCEPTED.

    Wave 1 is done and accepted and wave 2 is untouched, so the router owes the
    wave boundary: with the team up, tear it down; with it down, dispatch wave
    2. A reader that treated every recorded acceptance as outstanding would
    park the run at exactly the boundary lead-stalls US-001 is about.
    """
    drive = _refuse_then_follow(
        tmp_path, monkeypatch, refusal=None, team_registered=team_registered
    )

    assert drive["destinations"] == ["casting-1-accepted"], drive
    if team_registered:
        assert _tears_down(drive["header"]), drive
    else:
        assert drive["action"] == "build_castings", drive
        assert _NEXT_WAVE_CALL in drive["header"], drive


#: Where a block handed to a teammate tells it to write its progress lines.
_LEDGER_PATH = re.compile(r"`(foundry-archive/[^`]+/progress/[^`]+\.jsonl)`")


def _redispatch_step(header: str) -> str:
    """The redispatch branch's step (2): what the lead passes the Agent tool."""
    return header.split("\n  (2) ", 1)[1].split("\n  (3) ", 1)[0]


def _handed_to_teammate(root: str, header: str, spawned: dict) -> str:
    """Everything a teammate spawned by step (2), made AS WRITTEN, reads.

    The `dispatch` block, the prompt file it names, and the `progress_protocol`
    block only when step (2) tells the lead to append it. A step that says
    "with nothing appended" is taken at its word, which is what a lead obeying
    lead-stalls GI-008's one unconditional imperative does.
    """
    step = _redispatch_step(header)
    handed = [spawned["dispatch"], (Path(root) / spawned["prompt_path"]).read_text(
        encoding="utf-8"
    )]
    if "progress_protocol" in step and "nothing appended" not in step:
        handed.append(spawned["progress_protocol"])
    return "\n\n".join(handed)


def _teammate_does_what_it_was_told(root: str, handed: str) -> bool:
    """Rework, then write the ledger lines ``handed`` asks for — and no others.

    A teammate told of no ledger writes none: the server's own `dispatched`
    seed line is then the last line its casting has.
    """
    match = _LEDGER_PATH.search(handed)
    if match is None or '"done": true' not in handed:
        return False
    now = _foundry_state.now_iso()
    with (Path(root) / match.group(1)).open("a", encoding="utf-8") as ledger:
        ledger.write(json.dumps({"timestamp": now, "phase": "cast", "step": "fixed the refusal"}) + "\n")
        ledger.write(
            json.dumps({"timestamp": now, "phase": "cast", "step": "committed", "done": True})
            + "\n"
        )
    return True


def _refuse_then_respawn_then_follow(base: Path, monkeypatch) -> dict:
    """lead-stalls D-030's drive: the no-team refusal route, every step real.

    Casting 1 declared done half an hour ago and no CAST team is registered.
    Foundry-Accept-Casting refuses it, Foundry-Next answers the redispatch
    branch, the real Foundry-Spawn-Teammate seeds casting 1's ledger, and the
    teammate step (2) spawned does what it was handed. Then Foundry-Next again,
    as the woken lead calls it.
    """
    with _scratch_run(base, monkeypatch) as (root, fdir, _teams_dir):
        _arrange_waves(fdir, {1: ["1"], 2: ["2"]})
        earlier = (datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat()
        _worked_ledger(fdir, "1", done=True, stamp=earlier)
        refused = _accept(root, fdir, "1", **_REFUSALS["warned"])
        routed = foundry_next_action(root)
        header = _header(routed)
        spawned = foundry_spawn_teammate(casting_id="1", phase="cast", project_root=root)
        handed = _handed_to_teammate(root, header, spawned) if spawned.get("ok") else ""
        wrote = _teammate_does_what_it_was_told(root, handed)
        followed = foundry_next_action(root)
    return {
        "payload": refused,
        "routed_action": routed.get("action"),
        "routed_rules": routed.get("instructions", "").split(_NEXT_ACTION_MARKER, 1)[0],
        "routed_header": header,
        "spawned_ok": spawned.get("ok"),
        "wrote_done": wrote,
        "action": followed.get("action"),
        "header": _header(followed),
    }


def test_the_no_team_refusal_route_followed_as_written_reaches_acceptance(
    tmp_path, monkeypatch
):
    """lead-stalls ST-004 / US-003 / ST-003 (D-030) — through the spawn door.

    At fbbda01 step (2) said "VERBATIM with nothing appended", so the teammate
    was never told where its ledger is: it wrote no done line, the server's
    `dispatched` seed was the casting's last line, and the woken lead's
    Foundry-Next answered the `live` park (END YOUR TURN, nothing running) —
    or, 15 minutes on, a re-dispatch of all of wave 1. A route that reaches
    re-acceptance only for a lead that disobeys its imperative is not a route.
    """
    drive = _refuse_then_respawn_then_follow(tmp_path, monkeypatch)

    assert drive["payload"]["ok"] is False, drive
    assert drive["routed_action"] == "build_castings", drive
    assert _first_call(drive["routed_header"]) == "Foundry-Spawn-Teammate", drive
    assert drive["spawned_ok"] is True, drive
    # The route's outcome first, so a red run names what the lead was handed.
    assert (drive["action"], _first_call(drive["header"])) == (
        "build_castings", "Foundry-Spec-Hash"
    ), drive
    assert "Foundry-Accept-Casting(casting_id=1, " in drive["header"], drive
    assert "Foundry-Cast-Wave(wave=1" not in drive["header"], drive
    assert _NEXT_WAVE_CALL not in drive["header"], drive
    assert not _tears_down(drive["header"]), drive
    assert drive["wrote_done"] is True, drive
    assert "nothing appended" not in _redispatch_step(drive["routed_header"]), drive
    assert "progress_protocol" in _redispatch_step(drive["routed_header"]), drive


def _spawn_prompt_rule(rules: str) -> str:
    """The standing rule, above the marker, on passing a Foundry-Spawn-Teammate prompt."""
    return "\n".join(
        line for line in rules.splitlines()
        if "Foundry-Spawn-Teammate" in line and "VERBATIM" in line
    )


def test_the_rules_above_the_redispatch_step_sanction_what_it_appends(
    tmp_path, monkeypatch
):
    """lead-stalls GI-008 / US-003 / ST-004 (D-035) — one payload, one contract.

    Every Foundry-Next payload opens with the standing CRITICAL RULES, and at
    b358445 the one on a Foundry-Spawn-Teammate prompt said "Pass it to Agent
    VERBATIM. GRIND is the only exception" — printed above a CAST redispatch
    step (2) that orders the `progress_protocol` block appended BELOW the
    dispatch. A lead obeying the rule passes the dispatch alone, which is
    D-030's drive: the teammate is never told its ledger and re-acceptance is
    never reached. So the rule has to name the block every spawn passes
    (commands/start.md rule 1), and the check reads the rules as printed on the
    same payload as the step, not the constant they are built from.
    """
    drive = _refuse_then_respawn_then_follow(tmp_path, monkeypatch)

    assert drive["routed_action"] == "build_castings", drive
    assert "progress_protocol" in _redispatch_step(drive["routed_header"]), drive
    rule = _spawn_prompt_rule(drive["routed_rules"])
    assert rule, drive["routed_rules"]
    assert "progress_protocol" in rule, rule


#: A live Claude Code teammate pane in ANOTHER tmux session. `tmux list-panes
#: -a` lists every session on the machine, so `live_teammate_panes` answers
#: this for any other project with a team up (lead-stalls D-031).
_FOREIGN_PANE = {
    "available": True,
    "live": [("other-project:1.1", "@researcher", "2.1.80")],
    "zombie": [],
    "user": [],
    "lead": None,
}


def _refuse_beside_a_foreign_pane(base: Path, monkeypatch) -> dict:
    """No CAST team ever registered; casting 1 refused; a foreign pane live."""
    with _scratch_run(base, monkeypatch) as (root, fdir, _teams_dir):
        patch_everywhere(monkeypatch, "live_teammate_panes", lambda *_a, **_k: _FOREIGN_PANE)
        _arrange_waves(fdir, {1: ["1"], 2: ["2"]})
        earlier = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
        _worked_ledger(fdir, "1", done=True, stamp=earlier)
        refused = _accept(root, fdir, "1", **_REFUSALS["warned"])
        registered = _foundry_state.registered_team_dirs(
            fdir, teams_dir=Path.home() / ".claude" / "teams"
        )
        nxt = foundry_next_action(root)
    return {
        "payload": refused,
        "registered": registered,
        "action": nxt.get("action"),
        "header": _header(nxt),
    }


def test_a_foreign_teammate_pane_does_not_choose_the_send_back(tmp_path, monkeypatch):
    """lead-stalls FR-015 / ST-004 / US-003 (D-031) — the team is the WAVE's own.

    D-022 sends a refusal back by message only while the refused casting's
    team is registered. The choice read `teams_active`, which a live teammate
    pane anywhere on the machine sets, so a team in another project told this
    lead to message a teammate whose team was never created, claim "the CAST
    team is still registered", and end its turn with nothing running.
    """
    drive = _refuse_beside_a_foreign_pane(tmp_path, monkeypatch)

    assert drive["payload"]["ok"] is False, drive
    assert drive["registered"] == [], drive
    assert drive["action"] == "build_castings", drive
    assert _first_call(drive["header"]) == "Foundry-Spawn-Teammate", drive
    assert _refused_to_casting(drive["header"], "1"), drive
    assert _REFUSAL_TO_TEAMMATE not in drive["header"], drive
    assert "still registered" not in drive["header"], drive
    assert not _tears_down(drive["header"]), drive


def _refuse_under_a_split_scan(base: Path, monkeypatch, *, first_scan: str) -> dict:
    """lead-stalls D-028's drive: the reading's team scan and the arm's disagree.

    `cast-{run}-wave-1` is registered through the real Team-Up, casting 1 is
    done and refused, wave 2 is pending. The FIRST `_check_active_teams` call
    of the Foundry-Next — the reading's — raises (``raises``) or reads an
    empty registry (``empty``, the registry changing between the scans); every
    later call answers for real.
    """
    with _scratch_run(base, monkeypatch) as (root, fdir, teams_dir):
        _arrange_waves(fdir, {1: ["1"], 2: ["2"]})
        (teams_dir / _WAVE_ONE_TEAM).mkdir()
        assert foundry_register_team(_WAVE_ONE_TEAM, project_root=root)["ok"]
        _worked_ledger(fdir, "1", done=True)
        refused = _accept(root, fdir, "1", **_REFUSALS["warned"])
        real = _teams._check_active_teams
        calls: list[str] = []

        def _split(project_root: str) -> dict:
            calls.append(project_root)
            if len(calls) > 1:
                return real(project_root)
            if first_scan == "raises":
                raise OSError("team scan failed")
            return {"active": False, "teams": [], "live_panes": []}

        patch_everywhere(monkeypatch, "_check_active_teams", _split)
        nxt = foundry_next_action(root)
    return {
        "payload": refused,
        "scans": len(calls),
        "action": nxt.get("action"),
        "header": _header(nxt),
    }


@pytest.mark.parametrize("first_scan", ["raises", "empty"])
def test_a_refused_casting_keeps_its_team_when_the_scans_disagree(
    tmp_path, monkeypatch, first_scan
):
    """lead-stalls FR-002 / ST-004 / US-003 (D-028) — never `cleanup_teams`.

    The reading reads no team and answers `redispatch`; the cleanup arm's own
    scan reads `cast-{run}-wave-1` registered. Whichever way the two are
    reconciled, the team whose casting was just refused is not the lead's to
    shut down: D-022's `redispatch` is owed work, exactly as `refused` is.
    """
    drive = _refuse_under_a_split_scan(tmp_path, monkeypatch, first_scan=first_scan)

    assert drive["payload"]["ok"] is False, drive
    assert drive["scans"] >= 1, drive
    assert drive["action"] != "cleanup_teams", drive
    assert not _tears_down(drive["header"]), drive
    assert _NEXT_WAVE_CALL not in drive["header"], drive
    assert _refused_to_casting(drive["header"], "1"), drive


def _team_up_then_follow(
    base: Path, monkeypatch, *, stall_clock_seconds: int | None
) -> dict:
    """A real Team-Up, casting 1 done and casting 2 still writing, then Foundry-Next.

    The state the FIRST completion of a two-teammate wave leaves, which is the
    wake `_WAITING_IS_NOT_STOPPING` tells the lead to answer with Foundry-Next.
    """
    with _scratch_run(base, monkeypatch) as (root, fdir, teams_dir):
        _arrange_waves(fdir, {1: ["1", "2"]})
        (teams_dir / _WAVE_ONE_TEAM).mkdir()
        up = foundry_register_team(_WAVE_ONE_TEAM, project_root=root)
        _worked_ledger(fdir, "1", done=True)
        _worked_ledger(fdir, "2", done=False)
        if stall_clock_seconds is not None:
            stamp = datetime.now(timezone.utc) - timedelta(seconds=stall_clock_seconds)
            (fdir / ".last-next-at").write_text(stamp.isoformat() + "\n", encoding="utf-8")
        nxt = foundry_next_action(root)
    return {
        "team_up": up,
        "action": nxt.get("action"),
        "liveness": nxt.get("agent_liveness") or {},
        "instructions": nxt.get("instructions", ""),
        "header": _header(nxt),
    }


def test_team_up_success_names_no_call_at_all(tmp_path, monkeypatch):
    """lead-stalls GI-003 / CT-004 (D-018) — Team-Up is not a door that routes.

    lead-stalls GI-003 scopes the additive key to Team-Down and Accept-Casting.
    Team-Up returns in the middle of the lead's own ordered sequence
    (TeamCreate, Team-Up, Cast-Wave, spawn), so a `next_call` here would send
    the lead to `Foundry-Next` BEFORE its teammates exist — the hop D-015
    drove into a teardown. Its success payload is pinned to the three keys it
    has always had.
    """
    up = _team_up_then_follow(tmp_path, monkeypatch, stall_clock_seconds=None)["team_up"]

    assert set(up) == {"ok", "registered", "total_teams"}, up
    assert up["registered"] == _WAVE_ONE_TEAM, up


@pytest.mark.parametrize("stall_clock_seconds", [None, 600], ids=["fresh", "stale"])
def test_foundry_next_after_team_up_leaves_running_teammates_running(
    tmp_path, monkeypatch, stall_clock_seconds
):
    """lead-stalls ST-002 / GI-008 (D-018, D-019) — through the router, not the table.

    The woken lead's `Foundry-Next` answered `cleanup_teams` — stop the
    teammate that is still building — and, with the stall clock stale, put
    that teardown directly under "END YOUR TURN" in the same payload. One
    payload, one imperative, and never a teardown over a running teammate.
    The whole instruction text is searched, not only the header, because
    D-019's contradiction was between the header and the notice above it.
    """
    drive = _team_up_then_follow(
        tmp_path, monkeypatch, stall_clock_seconds=stall_clock_seconds
    )

    assert drive["action"] != "cleanup_teams", drive["action"]
    assert not _tears_down(drive["instructions"]), drive["instructions"]
    assert "END YOUR TURN" in drive["header"], drive["header"]
    # lead-stalls FR-015 / CT-012 (D-037) — the live reading publishes the
    # registered teams too. `_waiting_on_agents` has two returns and documents
    # `teams_registered` on both; only the no-live-agents one was pinned, so
    # the live one could drop the key with every route still green.
    assert drive["liveness"].get("waiting") is True, drive["liveness"]
    assert drive["liveness"].get("teams_registered") == [_WAVE_ONE_TEAM], (
        drive["liveness"]
    )


def _team_down_then_follow(base: Path, monkeypatch) -> dict:
    """Wave 1 accepted through the real door, TeamDelete done, the real
    Team-Down, then the call its `next_call` names."""
    with _scratch_run(base, monkeypatch) as (root, fdir, teams_dir):
        _arrange_waves(fdir, {1: ["1"], 2: ["2"]})
        (teams_dir / _WAVE_ONE_TEAM).mkdir()
        foundry_register_team(_WAVE_ONE_TEAM, project_root=root)
        _worked_ledger(fdir, "1", done=True)
        accepted = _accept(root, fdir, "1")
        (teams_dir / _WAVE_ONE_TEAM).rmdir()
        down = foundry_unregister_team(_WAVE_ONE_TEAM, root)
        nxt = foundry_next_action(root)
    return {
        "accepted": accepted,
        "team_down": down,
        "action": nxt.get("action"),
        "header": _header(nxt),
    }


def test_team_down_next_call_carries_a_finished_wave_to_the_next(tmp_path, monkeypatch):
    """lead-stalls US-001 / CT-004 — Team-Down's key, followed, dispatches wave 2.

    The adjacent path of the teardown half: the real `Foundry-Team-Down`
    succeeds and names `Foundry-Next`, and that call must hand the lead the
    next wave — not a second teardown of a team that no longer exists.
    """
    drive = _team_down_then_follow(tmp_path, monkeypatch)

    assert drive["accepted"]["ok"] is True, drive
    assert drive["team_down"].get("ok") is True, drive
    assert drive["team_down"]["next_call"] == _artifacts.LEAD_NEXT_CALL, drive
    assert drive["action"] == "build_castings", drive
    assert _NEXT_WAVE_CALL in drive["header"], drive
    assert not _tears_down(drive["header"]), drive


def _tears_down(text: str) -> bool:
    """True when ``text`` hands the lead a teardown of its team.

    Read from the YOUR NEXT ACTION marker onward when ``text`` carries one.
    Every Foundry-Next payload opens with the standing CRITICAL RULES block,
    which has always told the lead to "call TeamDelete immediately" once
    teammates are done — a rule about HOW to tear down, present in every state,
    not an instruction to do it now. The imperative, its CONTEXT and any
    directive overlay all follow the marker, so the scope still covers every
    place a teardown could be handed out.
    """
    if _NEXT_ACTION_MARKER in text:
        text = text.split(_NEXT_ACTION_MARKER, 1)[1]
    return any(call in text for call in _TEARDOWN_CALLS) or "stop working" in text


#: A condition that opens a SENTENCE, wherever the sentence sits on its line:
#: at the start of the text, after a newline, after a numbered or bulleted
#: step's marker, or after the sentence before it on the same line.
#: lead-stalls D-034 found the audit's condition detector anchored to the
#: start of a LINE, so "... two accounts of one run. If an AGENT stream
#: finished and no record exists, ... re-dispatch it, or file it" passed it
#: mid-line (D-033). A phrase list alone is blind the same way — it names the
#: conditions it has already met — so the header is also judged per sentence.
#: "When the notification arrives, call Foundry-Next" is a wake event, not a
#: choice, and `when` is deliberately absent.
_CONDITION_OPENS_A_SENTENCE = re.compile(
    r"(?:\A|\n|(?<=[.!?;:])[ \t]+)[ \t]*(?:(?:[-*•]|\(\d+\))[ \t]+)?"
    r"(?:if|unless|whether|otherwise|in case)\b",
    re.IGNORECASE,
)


def _conditioned(header: str) -> bool:
    """True when ``header`` carries a step that waits on an earlier call's answer,
    or a sentence that opens on a condition the lead must evaluate."""
    return any(phrase in header for phrase in _CONDITIONED_STEP) or bool(
        _CONDITION_OPENS_A_SENTENCE.search(header)
    )


@pytest.mark.parametrize(
    "text, conditioned",
    [
        # lead-stalls D-033's sentence in its emitted position: mid-line.
        (
            "Both are two accounts of one run. If an AGENT stream finished and "
            "no record exists, that is a finding about the stream — re-dispatch "
            "it, or file it — not a gap for you to fill in.",
            True,
        ),
        ("If the teammate answers, call Foundry-Next.", True),
        ("  (2) If step (1) refused, call Foundry-Spawn-Teammate.", True),
        ("Casting 1 was refused.\nUnless it reworks, call Foundry-Next.", True),
        # The wake event and the parked step every refused branch carries.
        ("When the notification arrives, call Foundry-Next and follow what it says then.", False),
        ("  (4) Waiting on a running agent is not stopping. END YOUR TURN.", False),
        # A word that only starts with a condition is not one.
        ("Ifs are not the point. Call Foundry-Next now.", False),
    ],
    ids=["d033_mid_line", "line_start", "numbered_step", "after_newline",
         "wake_event", "parked_step", "prefix_word"],
)
def test_the_route_condition_check_judges_each_sentence(text, conditioned):
    """lead-stalls OT-002 / GI-008 (D-034) — the check the route headers pass.

    Every `a step conditioned  False` line in the route report, and every
    `not _conditioned(header)` above, is only as good as this check, so its
    positive controls quote the shape it was blind to.
    """
    assert _conditioned(text) is conditioned, text


def _first_call(header: str) -> str:
    """The name of the call a header numbers ``(1)``, or ``NONE``."""
    match = re.search(r"\(1\) ([A-Za-z][A-Za-z-]*)", header)
    return match.group(1) if match else "NONE"


def _returns_to_casting_one(text: str) -> bool:
    """True when ``text`` sends the lead back to settle casting 1's acceptance."""
    return "Foundry-Accept-Casting" in text and (
        "casting_id=1" in text or "casting 1" in text
    )


def acceptance_route_report() -> list[str]:
    """The lines `evidence/casting-payloads-acceptance-route.log` carries.

    Beside the tests for the reason `dispatch_refusal_report` is: the log and
    the suite drive the SAME functions, so neither can drift alone —
    `test_the_route_report_agrees_with_the_tests_beside_it` keeps it honest.
    Every drive runs in its own temporary directory under its own
    `MonkeyPatch`, undone before the next, and nothing printed is environmental:
    no path, no timestamp, no duration.
    """
    import tempfile

    def _in_scratch(drive, **kwargs) -> dict:
        patch = pytest.MonkeyPatch()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                return drive(Path(tmp), patch, **kwargs)
        finally:
            patch.undo()

    lines: list[str] = []
    lines.append("Foundry-Accept-Casting, then the Foundry-Next its next_call names")
    lines.append("  wave 1 = casting 1 (done); wave 2 = casting 2 (not started)")
    for refusal in [None, *sorted(_REFUSALS)]:
        for team_registered in (True, False):
            drive = _in_scratch(
                _refuse_then_follow, refusal=refusal, team_registered=team_registered
            )
            lines.append("")
            lines.append(
                f"  {refusal or 'accepted'}, team "
                f"{'registered' if team_registered else 'torn down'}"
            )
            lines.append(f"    payload ok                  {drive['payload']['ok']}")
            lines.append(f"    payload next_call           {drive['payload']['next_call']!r}")
            lines.append(f"    acceptance records          {drive['destinations']}")
            lines.append(f"    Foundry-Next action         {drive['action']}")
            lines.append(f"    first call named            {_first_call(drive['header'])}")
            lines.append(f"    tears the team down         {_tears_down(drive['header'])}")
            lines.append(f"    dispatches wave 2           {_NEXT_WAVE_CALL in drive['header']}")
            lines.append(f"    returns to casting 1        {_returns_to_casting_one(drive['header'])}")
            lines.append(f"    a step conditioned          {_conditioned(drive['header'])}")

    drive = _in_scratch(_refuse_then_rework_then_follow)
    lines.append("")
    lines.append("  warned, then the teammate reworked it and wrote done again")
    lines.append(f"    acceptance records          {drive['destinations']}")
    lines.append(f"    Foundry-Next action         {drive['action']}")
    lines.append(f"    first call named            {_first_call(drive['header'])}")
    lines.append(f"    sends the refusal back      {_REFUSAL_TO_TEAMMATE in drive['header']}")
    lines.append(f"    returns to casting 1        {_returns_to_casting_one(drive['header'])}")

    lines.append("")
    lines.append("more than one wave-1 casting done, then Foundry-Next (D-025..D-027)")
    owed = (
        ("1 refused in the done line's second", {1: ["1"], 2: ["2"]}, ["1"], True, "1"),
        ("2 then 1 refused", {1: ["1", "2"], 2: ["3"]}, ["2", "1"], False, "1"),
        ("2 refused, 1 never accepted", {1: ["1", "2"], 2: ["3"]}, ["2"], False, "2"),
    )
    for label, waves, refused, same_second, owed_id in owed:
        others = [c for ids in waves.values() for c in ids if c != owed_id]
        for team_registered in (True, False):
            drive = _in_scratch(
                _owed_then_follow, waves=waves, refused=refused,
                team_registered=team_registered, same_second=same_second,
            )
            lines.append(
                f"  {label}, team {'registered' if team_registered else 'torn down'}"
            )
            lines.append(f"    acceptance records          {drive['destinations']}")
            lines.append(f"    first call named            {_first_call(drive['header'])}")
            lines.append(f"    forwards casting {owed_id}'s refusal  {_refused_to_casting(drive['header'], owed_id)}")
            lines.append(f"    names no other casting      {_names_only_casting(drive['header'], owed_id, others)}")

    drive = _in_scratch(
        _owed_then_follow, waves={1: ["1"], 2: ["2"]}, refused=["2", "1"],
        team_registered=True, same_second=False, team_wave=2,
    )
    lines.append("  2 then 1 refused across two waves, only wave 2's team registered (D-036)")
    lines.append(f"    teams registered            {drive['liveness'].get('teams_registered')}")
    lines.append(f"    refused casting's team      {drive['liveness'].get('cast_refused_team')}")
    lines.append(f"    first call named            {_first_call(drive['header'])}")
    lines.append(f"    forwards casting 1's refusal  {_refused_to_casting(drive['header'], '1')}")
    lines.append(f"    sends the refusal back      {_REFUSAL_TO_TEAMMATE in drive['header']}")

    drive = _in_scratch(_refuse_then_respawn_then_follow)
    lines.append("")
    lines.append("warned, no team; the real Foundry-Spawn-Teammate, the teammate obeys (D-030)")
    lines.append(f"    routed first call           {_first_call(drive['routed_header'])}")
    lines.append(f"    step (2) appends the ledger {'progress_protocol' in _redispatch_step(drive['routed_header'])}")
    lines.append(f"    rules above sanction it     {'progress_protocol' in _spawn_prompt_rule(drive['routed_rules'])}")
    lines.append(f"    teammate wrote done         {drive['wrote_done']}")
    lines.append(f"    Foundry-Next action         {drive['action']}")
    lines.append(f"    first call named            {_first_call(drive['header'])}")
    lines.append(f"    returns to casting 1        {_returns_to_casting_one(drive['header'])}")

    drive = _in_scratch(_refuse_beside_a_foreign_pane)
    lines.append("")
    lines.append("warned, no team registered, another project's teammate pane live (D-031)")
    lines.append(f"    registered teams            {drive['registered']}")
    lines.append(f"    Foundry-Next action         {drive['action']}")
    lines.append(f"    first call named            {_first_call(drive['header'])}")
    lines.append(f"    claims a registered team    {'still registered' in drive['header']}")

    lines.append("")
    lines.append("warned, wave-1 team registered, the reading's team scan disagrees (D-028)")
    for first_scan in ("raises", "empty"):
        drive = _in_scratch(_refuse_under_a_split_scan, first_scan=first_scan)
        lines.append(f"  first scan {first_scan}")
        lines.append(f"    Foundry-Next action         {drive['action']}")
        lines.append(f"    tears the team down         {_tears_down(drive['header'])}")
        lines.append(f"    sends casting 1 back        {_refused_to_casting(drive['header'], '1')}")

    judged, unrecorded = _judged_return_lines()
    lines.append("")
    lines.append("foundry_accept_casting return paths that judge the casting")
    lines.append(f"  judged                        {len(judged)}")
    lines.append(f"  recording no verdict          {len(unrecorded)}")

    lines.append("")
    lines.append("a real Foundry-Team-Up, casting 1 done, casting 2 still writing")
    for stall in (None, 600):
        drive = _in_scratch(_team_up_then_follow, stall_clock_seconds=stall)
        lines.append(f"  stall clock {'stale' if stall else 'fresh'}")
        lines.append(f"    Team-Up payload keys        {sorted(drive['team_up'])}")
        lines.append(f"    Foundry-Next action         {drive['action']}")
        lines.append(f"    first call named            {_first_call(drive['header'])}")
        lines.append(f"    any teardown in the payload {_tears_down(drive['instructions'])}")
        lines.append(f"    header ends the turn        {'END YOUR TURN' in drive['header']}")

    drive = _in_scratch(_team_down_then_follow)
    lines.append("")
    lines.append("wave 1 accepted, TeamDelete, a real Foundry-Team-Down, then Foundry-Next")
    lines.append(f"  Team-Down next_call           {drive['team_down'].get('next_call')!r}")
    lines.append(f"  Foundry-Next action           {drive['action']}")
    lines.append(f"  dispatches wave 2             {_NEXT_WAVE_CALL in drive['header']}")
    lines.append(f"  tears the team down           {_tears_down(drive['header'])}")
    return lines


def test_the_route_report_agrees_with_the_tests_beside_it():
    """The committed log's claims, re-derived here so neither can drift alone."""
    joined = "\n".join(acceptance_route_report())

    assert "  recording no verdict          0" in joined, joined
    assert joined.count("    returns to casting 1        True") == 8, joined
    assert joined.count("    a step conditioned          False") == 8, joined
    assert joined.count("    first call named            SendMessage") == 5, joined
    assert joined.count("    first call named            Foundry-Spawn-Teammate") == 7, joined
    assert joined.count("    first call named            Foundry-Spec-Hash") == 4, joined
    assert joined.count("'s refusal  True\n    names no other casting      True") == 6, joined
    assert joined.count("    sends the refusal back      False") == 2, joined
    assert f"    refused casting's team      {_WAVE_ONE_TEAM}" in joined, joined
    assert joined.count("    tears the team down         True") == 1, joined
    assert joined.count("    any teardown in the payload False") == 2, joined
    assert "  dispatches wave 2             True" in joined, joined
    assert "    step (2) appends the ledger True" in joined, joined
    assert "    rules above sanction it     True" in joined, joined
    assert "    teammate wrote done         True" in joined, joined
    assert "    claims a registered team    False" in joined, joined
    assert joined.count("    sends casting 1 back        True") == 2, joined
    split = joined.split("the reading's team scan disagrees (D-028)", 1)[1]
    assert "    Foundry-Next action         cleanup_teams" not in split, split
