"""The scripted continuity walk: a synthetic run from F1 to F6 in which every
turn-end attempt outside a sanctioned stop is blocked.

"I walk away after start_cast and expect it to keep going for hours with
nobody watching" (A-001), proved the way A-019 asks: "a scripted walk of a
synthetic run from F1 to F6 in which every turn-end attempt outside a
sanctioned stop is blocked."

Citations are interview answer ids from the should-not-stop spec's transcript,
per the lead's ruling on concern C-003: that spec is not one of the three this
directory's citation convention can qualify, so its requirement ids stay out
of this module's prose and the answers they derive from stand in.

  A-001  walk away after start_cast; the run keeps going unwatched
  A-008  park only the blocked item; ask only when nothing else can move
  A-009  the Stop hook blocks a live run's turn-end and says call Foundry-Next
  A-011  after CAST the halt door refuses the lead's own ruling
  A-012  never stop for diminishing returns; the trend is recorded instead
  A-014  TEMPER ends on its mechanical rule; nobody is asked
  A-019  the scripted F1-to-F6 walk
  A-025  the hook reads only files and never calls the server
  A-027  a halt answer seals HALTED with user_stop and the human's text
  A-029  post-CAST user_stop only on human-origin proof
  A-031  the awaiting_human marker, the one thing that lets a turn end

THE ONLY THING THAT MOVES A RUN IS THE LEAD'S NEXT TOOL CALL, so continuity is
proved at the turn boundary. Between door calls the walk attempts a turn-end by
running the SHIPPED Stop hook, `plugins/foundry/hooks/stop-continue.py`, in a
subprocess exactly as Claude Code runs it — the Stop event JSON on stdin, the
decision on stdout — against this walk's own `foundry-archive/` run directory.
Every attempt is blocked (A-009) except at the three sanctioned stops:

  * the ask — Foundry-Next writes the awaiting_human marker because every
    remaining unit of work is parked, and the answer that clears it puts the
    block straight back (A-008, A-031);
  * a HALTED seal with `user_stop` carrying human-origin proof (A-027, A-029);
  * F6, which is DONE.

One parked item with other work still runnable never lets the turn end
(A-031), and neither does the lead's own ruling, which the halt door refuses
after start_cast (A-011).

THE RUN MOVES ONLY THROUGH ITS REAL DOORS. Every phase change is
`foundry_mark_phase_complete`, with the ordering token armed by a real
Foundry-Next call (`foundry_next_action`, the lead's call) whose routing is
asserted at the same step; parked items go through the park door; spend goes
through the spend door; the report through the report door. What the walk
arranges by hand is only what an agent outside the server would have produced
— a stream's completion marker, a defect it filed, ASSAY's verdicts, a
casting's acceptance record — and it never writes `phase`.

The hook reads only files and never imports this package (A-025), so it is
the one participant here that sees the run exactly as a live session would.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from foundry_mcp.schemas import vocab
from foundry_mcp.tools.foundry_report import foundry_report
from foundry_mcp.tools.foundry_state import now_iso
from foundry_mcp.tools.orchestration.guidance import foundry_next_action
from foundry_mcp.tools.orchestration.park import foundry_park
from foundry_mcp.tools.orchestration.spend import foundry_record_spend
from foundry_mcp.tools.orchestration.transitions import foundry_mark_phase_complete

from tests.orchestration._env import (  # noqa: F401
    _router_defect,
    _router_ledger,
    _write_spec,
    _write_state,
    _write_verdicts,
    run_env,
)


#: tests/orchestration -> tests -> mcp-server -> plugins/foundry.
STOP_HOOK = Path(__file__).resolve().parents[3] / "hooks" / "stop-continue.py"


# --------------------------------------------------------------------------- #
# The turn boundary: the shipped Stop hook, run as Claude Code runs it.
# --------------------------------------------------------------------------- #


def _turn_end(project_root: str) -> subprocess.CompletedProcess:
    """One turn-end attempt: the real hook, the Stop event on stdin.

    CLAUDE_PROJECT_DIR is scrubbed, because the suite often runs inside a
    Claude Code session whose own project holds a live run, and the hook falls
    back to that variable — an unscrubbed attempt could be judged against the
    wrong run.
    """
    event = {
        "session_id": "continuity-walk",
        "cwd": project_root,
        "hook_event_name": "Stop",
        "stop_hook_active": False,
        "background_tasks": [],
    }
    return subprocess.run(
        [str(STOP_HOOK)],
        input=json.dumps(event),
        cwd=project_root,
        env={k: v for k, v in os.environ.items() if k != "CLAUDE_PROJECT_DIR"},
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=False,
    )


def _blocked(project_root: str, fdir: Path, phase: str) -> str:
    """Assert the turn-end is blocked in ``phase``; return the reason."""
    result = _turn_end(project_root)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip(), f"the hook let the turn end in {phase}"
    decision = json.loads(result.stdout)
    assert decision["decision"] == "block", decision
    assert f"Run '{fdir.name}' is live in phase {phase}" in decision["reason"], decision
    assert "Foundry-Next" in decision["reason"], decision
    return decision["reason"]


def _allowed(project_root: str) -> None:
    """Assert the turn-end is allowed: exit 0, nothing on stdout or stderr."""
    result = _turn_end(project_root)
    assert result.returncode == 0, result.stderr
    assert result.stdout == "", f"the hook blocked at a sanctioned stop: {result.stdout}"
    assert result.stderr == "", result.stderr


# --------------------------------------------------------------------------- #
# The lead's calls.
# --------------------------------------------------------------------------- #


def _state(fdir: Path) -> dict:
    return json.loads((fdir / "state.json").read_text(encoding="utf-8"))


def _next(project_root: str, fdir: Path, *actions: str) -> dict:
    """The lead's Foundry-Next: routing asserted, ordering token armed."""
    result = foundry_next_action(project_root)
    assert result.get("action") in actions, (actions, result.get("action"), result)
    assert (fdir / ".next-action-called").exists(), "the lead's call arms the token"
    return result


def _cross(project_root: str, fdir: Path, token: str, to_phase: str, **kw) -> dict:
    """One Foundry-Phase door; the phase is the door's to write, never ours."""
    result = foundry_mark_phase_complete(token, project_root, **kw)
    assert result.get("ok") is True, (token, result)
    assert result["phase"] == to_phase, (token, result)
    assert _state(fdir)["phase"] == to_phase
    return result


def _park(project_root: str, ref: str, category: str, question: str) -> str:
    result = foundry_park(
        action="park", item_ref=ref, category=category, question=question,
        project_root=project_root,
    )
    assert result["ok"] is True, result
    return result["parked"]["id"]


def _answer(project_root: str, parked_id: str, answer: str, **extra) -> dict:
    result = foundry_park(
        action="answer", parked_id=parked_id, answer=answer,
        project_root=project_root, **extra,
    )
    assert result["ok"] is True, result
    return result


def _spend(project_root: str, agent: str, phase: str, tokens: int, minutes: int) -> None:
    result = foundry_record_spend(
        agent, phase, tokens, minutes * 60_000, project_root=project_root
    )
    assert result.get("error") is None, result


# --------------------------------------------------------------------------- #
# What agents outside the server would have produced.
# --------------------------------------------------------------------------- #


def _launch(fdir: Path) -> None:
    """A decomposed run at F0, launched with --temper --nyquist so the walk
    crosses every phase from F1 to F5.5."""
    _write_spec(fdir, ["FR-1"])
    (fdir / "castings" / "manifest.json").write_text(json.dumps({
        "target_url": "", "no_ui": True,
        "castings": [
            {"id": cid, "title": f"casting {cid}", "key_files": [f"src/c{cid}.py"],
             "depends_on": []}
            for cid in (1, 2)
        ],
    }), encoding="utf-8")
    _write_state(fdir, phase="F0", cycle=0, temper=True, nyquist=True)
    _router_ledger(fdir, [])


def _accepted(fdir: Path, *casting_ids: int) -> None:
    """The acceptance records Foundry-Accept-Casting appends to the handoffs."""
    with (fdir / "handoffs.jsonl").open("a", encoding="utf-8") as handle:
        for cid in casting_ids:
            handle.write(json.dumps({
                "timestamp": now_iso(), "event": "acceptance",
                "destination": f"casting-{cid}-accepted",
            }) + "\n")


def _streams_ran(fdir: Path) -> None:
    """Every stream on the INSPECT roster the last crossing recorded, complete."""
    entry = _state(fdir)["inspect_modes"][-1]
    for stream in entry["required_streams"]:
        (fdir / f".{stream}-complete").write_text(
            f"{now_iso()} cycle={entry['cycle']}\nitems_checked=1\nitems_total=1\n"
            "coverage=100%\nfindings=0\n",
            encoding="utf-8",
        )


def _file(fdir: Path, *records: dict) -> None:
    """Defects a stream filed, appended to the ledger as the doors append."""
    ledger = json.loads((fdir / "defects.json").read_text(encoding="utf-8"))["defects"]
    _router_ledger(fdir, ledger + list(records))


def _fixed(fdir: Path, defect_id: str, cycle: int) -> None:
    ledger = json.loads((fdir / "defects.json").read_text(encoding="utf-8"))["defects"]
    for record in ledger:
        if record["id"] == defect_id:
            record["status"] = "fixed"
            record["fixed_in_cycle"] = cycle
    _router_ledger(fdir, ledger)


def _stop_token(fdir: Path) -> None:
    """An unused /foundry:stop token, in the shape stop.md's own shell step
    writes it — `test_transitions.py#test_the_stop_commands_own_shell_step_
    writes_the_token_the_door_consumes` runs that step and holds the shapes
    equal."""
    (fdir / vocab.STOP_TOKEN_FILENAME).write_text(json.dumps({
        vocab.STOP_TOKEN_FIELD_RUN: fdir.name,
        vocab.STOP_TOKEN_FIELD_CREATED_AT: now_iso(),
        vocab.STOP_TOKEN_FIELD_NONCE: "c0ffee" * 5,
        vocab.STOP_TOKEN_FIELD_CONSUMED_AT: None,
    }), encoding="utf-8")


def _into_grind(project_root: str, fdir: Path) -> None:
    """F0 -> F1 -> F2 -> F3 through the doors, one LIVE defect open in GRIND."""
    _launch(fdir)
    _next(project_root, fdir, "transition_to_cast")
    _cross(project_root, fdir, "start_cast", "F1")
    _accepted(fdir, 1, 2)
    _next(project_root, fdir, "transition_to_inspect")
    _cross(project_root, fdir, "cast", "F2")
    _file(fdir, _router_defect("D-001", cycle=0, source="trace"))
    _streams_ran(fdir)
    (fdir / ".tasks-generated").write_text("x\n", encoding="utf-8")
    _next(project_root, fdir, "transition_to_grind")
    _cross(project_root, fdir, "grind_start", "F3")


# --------------------------------------------------------------------------- #
# The walk.
# --------------------------------------------------------------------------- #


def test_a_synthetic_run_walks_f1_to_f6_and_only_the_sanctioned_stops_end_a_turn(run_env):
    """A-001 / A-008 / A-009 / A-012 / A-014 / A-019 / A-031.

    F1 -> F2 -> F3 -> F2 -> F4 -> F5 -> F5.5 -> F6, one INSPECT/GRIND loop,
    a parked casting beside a runnable one, an every-item-parked ask at CAST
    and again at GRIND, three STUCK TEMPER domains the run moves past with
    nobody asked — and a turn-end attempted at every step.
    """
    project_root, fdir = run_env
    _launch(fdir)
    _allowed(project_root)  # F0: before CAST the operator is at the keyboard

    # ---- F1 CAST ----------------------------------------------------------
    _next(project_root, fdir, "transition_to_cast")
    _cross(project_root, fdir, "start_cast", "F1")
    _blocked(project_root, fdir, "F1")
    _next(project_root, fdir, "build_castings")
    _blocked(project_root, fdir, "F1")

    # A-031 — one item parked, casting 2 still runnable: the wave goes on.
    first = _park(project_root, "casting:1", vocab.PARK_CATEGORY_SPEC_WRONG,
                  "Rows FR-1 and FR-2 contradict each other; which one stands?")
    wave = _next(project_root, fdir, "build_castings")
    assert wave["details"]["parked"] == ["1"], wave["details"]
    assert _state(fdir)["parked"][vocab.PARKED_AWAITING_HUMAN_KEY] is None
    _blocked(project_root, fdir, "F1")

    # A-008 — every casting parked: ONE ask, the marker, the one allowed stop.
    second = _park(project_root, "casting:2", vocab.PARK_CATEGORY_ENV_BROKEN,
                   "The sandbox refuses network access; may casting 2 retry offline?")
    ask = _next(project_root, fdir, "ask_human")
    assert ask["details"]["awaiting_human"]["item_ids"] == [first, second]
    assert _state(fdir)["phase"] == "F1", "the run waits in place; it is not HALTED"
    _allowed(project_root)

    # A-031 — an answer clears the ask and the block is back at once.
    _answer(project_root, first, "FR-1 stands; build against it")
    _blocked(project_root, fdir, "F1")
    _answer(project_root, second, "Retry offline on the same model")
    _blocked(project_root, fdir, "F1")

    _spend(project_root, "casting-1", "F1", 400_000, 20)
    _spend(project_root, "casting-2", "F1", 300_000, 15)
    _accepted(fdir, 1, 2)
    _next(project_root, fdir, "transition_to_inspect")
    _blocked(project_root, fdir, "F1")

    # ---- F2 INSPECT, cycle 0 -----------------------------------------------
    _cross(project_root, fdir, "cast", "F2")
    _blocked(project_root, fdir, "F2")
    _file(fdir, _router_defect("D-001", cycle=0, source="trace"))
    _spend(project_root, "trace", "F2", 120_000, 6)
    _streams_ran(fdir)
    (fdir / ".tasks-generated").write_text("x\n", encoding="utf-8")
    _next(project_root, fdir, "transition_to_grind")
    _blocked(project_root, fdir, "F2")

    # ---- F3 GRIND, cycle 0 -------------------------------------------------
    _cross(project_root, fdir, "grind_start", "F3")
    _blocked(project_root, fdir, "F3")
    held = _park(project_root, "defect:D-001", vocab.PARK_CATEGORY_UNKNOWN_DEADLOCK,
                 "D-001's fix and its test contradict each other; which wins?")
    grind_ask = _next(project_root, fdir, "ask_human")
    assert grind_ask["details"]["awaiting_human"]["item_ids"] == [held]
    _allowed(project_root)
    _answer(project_root, held, "The test wins; fix the handler")
    _blocked(project_root, fdir, "F3")
    _spend(project_root, "casting-1", "F3", 200_000, 10)
    _fixed(fdir, "D-001", 0)
    _next(project_root, fdir, "transition_to_inspect")
    _blocked(project_root, fdir, "F3")

    # ---- F2 INSPECT, cycle 1: the loop closes ------------------------------
    _cross(project_root, fdir, "inspect_start", "F2")
    assert _state(fdir)["cycle"] == 1, "the GRIND -> INSPECT crossing advances the cycle"
    _blocked(project_root, fdir, "F2")
    _spend(project_root, "prove", "F2", 80_000, 4)
    _streams_ran(fdir)
    _next(project_root, fdir, "transition_to_assay")
    _blocked(project_root, fdir, "F2")

    # ---- F4 ASSAY ----------------------------------------------------------
    _cross(project_root, fdir, "inspect_clean", "F4")
    _blocked(project_root, fdir, "F4")
    _write_verdicts(fdir, [{"requirement_id": "FR-1", "id": "FR-1", "verdict": "VERIFIED"}])
    _next(project_root, fdir, "transition_to_temper")
    _blocked(project_root, fdir, "F4")

    # ---- F5 TEMPER: three STUCK domains, and nobody is asked (A-014) --------
    _cross(project_root, fdir, "temper", "F5")
    _blocked(project_root, fdir, "F5")
    _file(
        fdir,
        _router_defect("D-002", cycle=1, source="temper", tier="LATENT",
                       **{"class": "RETRY_ORDERING"},
                       reproduction_attempted="drove the retry path three ways; no reachable instance"),
        _router_defect("D-003", cycle=1, source="temper", tier="HARDENING",
                       spec_ref=None,
                       reproduction_attempted="drove a 10k-row page; the cursor repeats row 5001"),
        _router_defect("D-004", cycle=1, source="temper", tier="HARDENING",
                       spec_ref=None,
                       reproduction_attempted="drove two writers at once; one save is lost"),
    )
    _spend(project_root, "temper", "F5", 150_000, 9)
    _next(project_root, fdir, "run_temper")
    _blocked(project_root, fdir, "F5")

    # ---- F5.5 NYQUIST ------------------------------------------------------
    _cross(project_root, fdir, "nyquist", "F5.5")
    _blocked(project_root, fdir, "F5.5")
    _next(project_root, fdir, "run_nyquist")
    _blocked(project_root, fdir, "F5.5")

    # ---- F6 DONE -----------------------------------------------------------
    assert foundry_report(project_root)["ok"] is True
    _next(project_root, fdir, "run_nyquist")
    _cross(project_root, fdir, "nyquist_done", "F6")
    _allowed(project_root)

    # What the run kept instead of stopping (A-012, A-014): the report carries it.
    report = json.loads((fdir / vocab.REPORT_JSON_FILENAME).read_text(encoding="utf-8"))
    markdown = (fdir / vocab.REPORT_MD_FILENAME).read_text(encoding="utf-8")

    parked = report["halt_and_co_dispatch"]["parked_items"]
    assert [i["id"] for i in parked["items"]] == [first, second, held]
    assert all(i["status"] == "answered" for i in parked["items"])
    assert not any(i["answer_is_halt"] for i in parked["items"])
    assert "FR-1 stands; build against it" in markdown

    trend = {row["cycle"]: row for row in report["spend_per_phase_and_cycle"]["cycle_trend"]["rows"]}
    assert trend["0"]["tokens"] == 1_020_000 and trend["0"]["defects_filed"] == 1, trend
    assert trend["1"]["tokens"] == 230_000 and trend["1"]["defects_filed"] == 3, trend

    stuck = report["hardening_backlog"]["temper_backlog"]
    assert stuck["open_count"] == 3
    assert stuck["by_tier"]["LATENT"]["ids"] == ["D-002"]
    assert stuck["by_tier"]["HARDENING"]["ids"] == ["D-003", "D-004"]


@pytest.mark.parametrize("proof", ["parked_answer", "stop_token"])
def test_only_a_human_origin_halt_ends_the_turn_mid_build(run_env, proof):
    """A-011 / A-027 / A-029.

    In GRIND the lead's own ruling is refused and the turn still cannot end.
    The human's halt — a parked question answered with halt, or the
    /foundry:stop token — is not itself a stop either: the turn goes on until
    the lead seals `user_stop` through the halt door, and only HALTED lets it
    end.
    """
    project_root, fdir = run_env
    _into_grind(project_root, fdir)
    _blocked(project_root, fdir, "F3")

    ruling = foundry_mark_phase_complete(
        "halt", project_root, reason="lead_ruling", text="diminishing returns"
    )
    assert ruling.get("ok") is not True, ruling
    assert _state(fdir)["phase"] == "F3"
    _blocked(project_root, fdir, "F3")

    if proof == "parked_answer":
        asked = _park(project_root, "defect:D-001", vocab.PARK_CATEGORY_SPEC_WRONG,
                      "D-001 shows the spec is wrong; halt, or rebuild against a new spec?")
        _next(project_root, fdir, "ask_human")
        _allowed(project_root)
        _answer(project_root, asked, "Halt the run; I will rewrite the spec", halt=True)
    else:
        _stop_token(fdir)
    # The human's halt is proof for the door, not a stop for the hook.
    _blocked(project_root, fdir, "F3")

    sealed = foundry_mark_phase_complete(
        "halt", project_root, reason=vocab.HALT_REASON_USER_STOP,
        text="the human stopped the run",
    )
    assert sealed.get("ok") is True and sealed["phase"] == vocab.RUN_PHASE_HALTED, sealed
    _allowed(project_root)

    if proof == "parked_answer":
        items = json.loads(
            (fdir / vocab.REPORT_JSON_FILENAME).read_text(encoding="utf-8")
        )["halt_and_co_dispatch"]["parked_items"]["items"]
        assert items[-1]["answer_is_halt"] is True
        assert items[-1]["answer"] == "Halt the run; I will rewrite the spec"
