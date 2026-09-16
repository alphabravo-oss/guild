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
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from foundry_mcp.tools import artifacts as _artifacts
from foundry_mcp.tools import evidence as _evidence
from foundry_mcp.tools import foundry_state as _foundry_state
from foundry_mcp.tools.artifacts import _hash_file, foundry_spec_hash
from foundry_mcp.tools.evidence import foundry_accept_casting
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


def _worked_ledger(fdir: Path, cid: str, *, done: bool) -> None:
    """A casting's progress ledger: still writing, or its terminal done line."""
    now = _foundry_state.now_iso()
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
    and send it to re-dispatch work that passed.
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
        f"a refused casting back to re-dispatch (lead-stalls ST-004, D-020)."
    )


@pytest.mark.parametrize("team_registered", [True, False], ids=["team_up", "team_down"])
@pytest.mark.parametrize("refusal", sorted(_REFUSALS))
def test_following_next_call_off_a_refusal_returns_to_that_casting(
    tmp_path, monkeypatch, refusal, team_registered
):
    """lead-stalls ST-004 / US-003 (D-020) — the refusal routes back, not on.

    ST-004 is "casting rejected -> lead re-accepting", triggered by "lead
    follows the `next_call` on the reject payload". Driven at c5045c2 the
    `Foundry-Next` that key names answered `cleanup_teams` with the team up —
    shut the teammate down and delete its team — and, with the team down, the
    wave-2 dispatch over a casting nothing had accepted. Neither is the move a
    refusal owes. The router has to name casting 1 and the door that settles
    it.
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


@pytest.mark.parametrize("team_registered", [True, False], ids=["team_up", "team_down"])
def test_an_accepted_wave_still_moves_on(tmp_path, monkeypatch, team_registered):
    """The adjacent path the reader must not block: casting 1 ACCEPTED.

    Wave 1 is done and accepted and wave 2 is untouched, so the router owes the
    wave boundary: with the team up, tear it down; with it down, dispatch wave
    2. A reader that treated every recorded acceptance as outstanding would
    park the run at exactly the boundary US-001 is about.
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
    """True when ``text`` hands the lead a teardown of its team."""
    return any(call in text for call in _TEARDOWN_CALLS) or "stop working" in text


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
            lines.append(f"    tears the team down         {_tears_down(drive['header'])}")
            lines.append(f"    dispatches wave 2           {_NEXT_WAVE_CALL in drive['header']}")
            lines.append(f"    returns to casting 1        {_returns_to_casting_one(drive['header'])}")

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
    assert joined.count("    returns to casting 1        True") == 6, joined
    assert joined.count("    tears the team down         True") == 1, joined
    assert joined.count("    any teardown in the payload False") == 2, joined
    assert "  dispatches wave 2             True" in joined, joined
