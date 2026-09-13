#!/usr/bin/env python3
"""foundry — Stop hook: the backstop that keeps a live build from ending its turn.

The operator walks away after start_cast and expects the build to keep going
for hours with nobody watching. The lead's protocol already says never end the
turn mid-build; this hook is what holds it to that when the protocol slips — a
premature "done", a wait that ends the turn instead of bounding it, a
notification that never arrives. It fires on every turn-end in every session
where the foundry plugin is enabled, so it is a silent no-op unless a foundry
build is genuinely live.

WHICH RUN. The active run is found by the rule shared with the SessionStart
hook and with casting 1's /foundry:stop shell step, implemented once in
`foundry_active_run.py` beside this file. In the lead's ruling's own words:
the active run is the run under `<cwd>/foundry-archive/` whose state.json phase
is F1..F5.5 and not HALTED or DONE; if several qualify, take the one whose
state.json was modified most recently. That module's header says how `<cwd>`
is resolved (event `cwd`, then CLAUDE_PROJECT_DIR, then the process cwd).

THE DECISION, and every allow path there is:

  * ALLOW (no output, exit 0) when no run under the project is in F1..F5.5 —
    no archive at all, a run still before CAST (F0, F0.5, F0.9), a run that is
    DONE (F6) or HALTED — or when the active run's state.json carries the
    server-written ask marker, `parked.awaiting_human`, as a non-null object.
    That marker means every remaining unit of work is parked and Foundry-Next
    has put the questions to the human, so waiting for the human IS the work.
  * BLOCK otherwise. A parked list that is merely non-empty never allows the
    turn to end: one parked item is one item waiting, and every other casting,
    defect and stream keeps moving. `stop_hook_active` never turns a block into
    an allow either; the platform's own cap of 8 consecutive blocks is the
    second safety net, and the only one.

THE REASON names the run, its phase and every in-flight entry of the event's
`background_tasks`, and says what to do instead of stopping: call Foundry-Next
and follow it, or, while agents are running, wait for them with a bounded wait
(a time-limited Bash wait loop, or the Monitor tool).

ERRORS. A malformed or missing stdin is not a reason to allow: the decision is
taken from run state anyway, with the project located through
CLAUDE_PROJECT_DIR and then the process working directory, so a readable
state.json in F1..F5.5 with no ask marker still blocks. The stop is allowed,
with no output and no error text, only when the run state itself cannot be
read, so a broken hook never traps a session. This is the one place the hook
departs from crew's goal-gate.py, whose structure it otherwise follows: that
hook fails open on any malformed input.

FILES ONLY. This hook reads its stdin and files under `foundry-archive/`. It
never calls the MCP server, never imports the server package and never runs
the server's executable; it writes nothing. `.stop-token.json` in a run
directory is the halt door's proof of a human stop and is no signal here.

Stop protocol: print {"decision": "block", "reason": ...} to block; exit 0
with no output to allow.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

# The shared reader lives beside this script. Put this directory on the path
# explicitly rather than relying on the interpreter doing it, which isolated
# mode (-I) does not.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from foundry_active_run import ActiveRun, find_active_run, project_roots, read_event  # noqa: E402

#: `background_tasks` statuses that mean the task is no longer in flight. Any
#: other status, or none, is named as running: naming a finished task costs the
#: lead one bounded wait, while omitting a running one would send it to end the
#: turn with work still in the air.
_FINISHED_STATUSES = frozenset(
    {"completed", "complete", "done", "failed", "error", "killed", "stopped", "cancelled", "canceled"}
)

#: Longest single field quoted from a background task, so one verbose
#: description cannot swamp the instruction the reason exists to carry.
_FIELD_LIMIT = 80


def _allow() -> int:
    """Allow the stop: emit nothing, exit 0."""
    return 0


def _block(reason: str) -> int:
    print(json.dumps({"decision": "block", "reason": reason}))
    return 0


def _field(value: object) -> str:
    """``value`` as one short line of text, or "" when it is not a string."""
    if not isinstance(value, str):
        return ""
    return " ".join(value.split())[:_FIELD_LIMIT]


def _in_flight(event: dict | None) -> list[str]:
    """A label for every in-flight entry of the event's `background_tasks`.

    A missing or malformed array names nothing. That is never a reason to
    allow: it only means the reason cannot say which agents are running.
    """
    tasks = event.get("background_tasks") if event is not None else None
    if not isinstance(tasks, list):
        return []
    labels = []
    for task in tasks:
        if not isinstance(task, dict):
            continue
        status = task.get("status")
        if isinstance(status, str) and status.strip().lower() in _FINISHED_STATUSES:
            continue
        labels.append(_label(task))
    return labels


def _label(task: dict) -> str:
    """`<type> "<name>" (<id>)`, from the fields the platform documents."""
    kind = _field(task.get("type")) or "task"
    ident = _field(task.get("id"))
    name = _field(task.get("name")) or _field(task.get("description")) or ident
    label = f'{kind} "{name}"' if name else kind
    if ident and ident != name:
        label += f" ({ident})"
    return label


def _reason(run: ActiveRun, in_flight: list[str]) -> str:
    lines = [
        f"[foundry] Run '{run.name}' is live in phase {run.phase} and is not "
        "waiting on the human, so this turn does not end here. Only DONE, "
        "HALTED, or an ask Foundry-Next has put to the human because every "
        "remaining item is parked lets the turn end; the human's own stop is "
        "/foundry:stop.",
    ]
    if in_flight:
        lines.append(f"In flight ({len(in_flight)}): " + "; ".join(in_flight) + ".")
        lines.append(
            "Agents are still running: wait for them with a bounded wait (a "
            "time-limited Bash wait loop that checks on them, or the Monitor "
            "tool) instead of ending the turn to wait for a notification, and "
            "call Foundry-Next and follow it whenever it has a step for you."
        )
    else:
        lines.append(
            "Next: call Foundry-Next and follow the step it gives. If every "
            "remaining unit of work is parked, Foundry-Next asks the human and "
            "records the ask, and only then may the turn end."
        )
    return "\n".join(lines)


def main() -> int:
    event = read_event(sys.stdin)
    run = find_active_run(project_roots(event))
    if run is None:
        return _allow()  # no run in F1..F5.5, or its state cannot be read
    if run.awaiting_human:
        return _allow()  # every remaining item is parked and the human is asked
    return _block(_reason(run, _in_flight(event)))


if __name__ == "__main__":
    sys.exit(main())
