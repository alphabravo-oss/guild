#!/usr/bin/env python3
"""crew goal-gate — Stop-hook enforcement for /crew:goal (crew v0.2.0).

While a crew goal run is active, this Stop hook blocks Claude from ending the
turn until the goal is met (critic AND fresh-eyes both return `pass`), a safety
cap is reached, or the run is no longer active (cancelled / blocked).

This is the ENFORCEMENT layer behind /crew:goal. commands/goal.md drives the
cooperative loop; this hook is the backstop that catches drift — a premature
"done", a mishandled agent return, an early stop.

Contract — written by commands/goal.md, read here:
  <project>/.crew/active-goal.json
      {"run_id": "...", "run_dir": ".crew/runs/<run_id>"}
      An empty object {} means "no active goal" (goal.md empties it on exit).
  <run_dir>/state.json   (goal-mode fields)
      mode                     "goal"
      status                   active | goal_met | capped | stuck |
                               blocked | cancelled
      condition                str   — the completion condition
      cycle                    int   — completed verify cycles
      max_cycles               int
      max_seconds              int
      started_at               ISO-8601 UTC
      last_critic_verdict      pass | conditional-pass | fail | null
      last_fresh_eyes_verdict  pass | partial | fail | null

Fast path: no (or empty) .crew/active-goal.json -> exit 0. This hook fires on
EVERY Stop in EVERY session, so it must be a silent no-op unless a crew goal
run is genuinely in progress.

Fail-open: any malformed/missing input -> exit 0 (allow stop). A Stop hook must
never trap the user because of its own bug.

Stop protocol: print {"decision":"block","reason":...} to block; exit 0 with no
output to allow.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path


def _allow() -> int:
    """Allow the Stop — emit nothing, exit 0."""
    return 0


def _block(reason: str) -> int:
    print(json.dumps({"decision": "block", "reason": reason}))
    return 0


def _load_json(path: Path) -> dict | None:
    try:
        with path.open(encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def _as_int(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _parse_ts(value) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def main() -> int:
    try:
        event = json.load(sys.stdin)
    except (json.JSONDecodeError, OSError):
        return _allow()

    project_dir = Path(event.get("cwd") or os.getcwd())
    marker = project_dir / ".crew" / "active-goal.json"
    if not marker.is_file():
        return _allow()  # no active goal — silent no-op (the common case)

    pointer = _load_json(marker)
    run_dir_raw = pointer.get("run_dir") if pointer else None
    if not run_dir_raw:
        return _allow()  # empty / malformed marker — goal is not armed

    run_dir = Path(run_dir_raw)
    if not run_dir.is_absolute():
        run_dir = project_dir / run_dir

    state = _load_json(run_dir / "state.json")
    if not state or state.get("mode") != "goal":
        return _allow()

    # Only an `active` run is enforced. goal_met / capped / stuck / blocked /
    # cancelled all mean the orchestrator (or /crew:cancel) is done — let it stop.
    if state.get("status") != "active":
        return _allow()

    # --- safety caps: the gate stops enforcing once a cap is reached ----------
    cycle = _as_int(state.get("cycle"))
    max_cycles = _as_int(state.get("max_cycles"))
    max_seconds = _as_int(state.get("max_seconds"))

    if max_cycles > 0 and cycle >= max_cycles:
        return _allow()  # cycle cap — orchestrator debriefs as "capped"

    started = _parse_ts(state.get("started_at"))
    elapsed = None
    if started is not None:
        elapsed = (datetime.now(timezone.utc) - started).total_seconds()
        if max_seconds > 0 and elapsed >= max_seconds:
            return _allow()  # wall-clock cap — backstop against a spin loop

    # --- goal met? critic AND fresh-eyes must BOTH be exactly `pass` ----------
    # critic verdicts: pass | conditional-pass | fail
    # fresh-eyes verdicts: pass | partial | fail
    # Only a clean pass on both counts as met — conditional-pass / partial do not.
    critic = str(state.get("last_critic_verdict") or "").strip().lower()
    fresh = str(state.get("last_fresh_eyes_verdict") or "").strip().lower()
    if critic == "pass" and fresh == "pass":
        return _allow()  # goal met — orchestrator debriefs as "goal_met"

    # --- not met, not capped, still active -> ENFORCE ------------------------
    elapsed_min = int(elapsed // 60) if elapsed is not None else "?"
    max_min = max_seconds // 60 if max_seconds > 0 else "?"
    reason = (
        "[crew:goal] Goal NOT met — the crew goal loop is still active. "
        "Do NOT stop.\n"
        f"  Condition: {state.get('condition') or '(unspecified)'}\n"
        f"  Cycle: {cycle}/{max_cycles or '?'}    "
        f"Elapsed: {elapsed_min}m/{max_min}m\n"
        f"  Last critic verdict: {critic or 'none yet'}    "
        f"Last fresh-eyes verdict: {fresh or 'none yet'}\n"
        "Continue the crew goal loop per commands/goal.md:\n"
        "  - If either verdict is fail / partial / conditional-pass, "
        "re-dispatch the worker with the critic + fresh-eyes findings, then "
        "re-run dual verification.\n"
        "  - The goal is met ONLY when critic AND fresh-eyes BOTH return "
        "'pass'.\n"
        "  - If the worker declared stuck, run multi-agent reflexion — do not "
        "stop.\n"
        "  - Update <run_dir>/state.json (cycle, last_critic_verdict, "
        "last_fresh_eyes_verdict) after every cycle so this gate sees "
        "progress.\n"
        f"  - To end the run early: /crew:cancel {state.get('run_id') or '<run-id>'}"
    )
    return _block(reason)


if __name__ == "__main__":
    sys.exit(main())
