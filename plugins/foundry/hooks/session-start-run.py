#!/usr/bin/env python3
"""foundry — SessionStart hook (compact, resume): put a live build back in front of the lead.

A foundry run outlives one context window. The lead never deliberately ends the
session for context; when the platform compacts it, or the session is resumed,
the conversation that knew which run was live and what came next is gone. This
hook re-injects that one fact, so the lead re-orients through the server and
carries on instead of waiting for someone to type /foundry:resume.

REGISTERED for the `compact` and `resume` matchers only, as its own
SessionStart entry beside the Serena entry (`.*`), which it does not touch.

WHICH RUN. The same rule as the Stop hook, implemented once in
`foundry_active_run.py` beside this file and shared with casting 1's
/foundry:stop shell step. In the lead's ruling's own words: the active run is
the run under `<cwd>/foundry-archive/` whose state.json phase is F1..F5.5 and
not HALTED or DONE; if several qualify, take the one whose state.json was
modified most recently. Pre-CAST runs are excluded on purpose: one rule keeps
the two hooks from disagreeing about which run is live, and a compaction in an
unrelated session is never steered into an abandoned planning run.

OUTPUT. With an active run: one additionalContext envelope naming the run and
its phase and telling the lead to call Foundry-Context, then Foundry-Next. A
resumed session starts a fresh MCP server whose active run is held only in
memory, so the context also says what to do when Foundry-Context reports no
run: call Foundry-Init(resume='<run>') first, then the same two steps. The
envelope is built with a real JSON encoder because it carries a run name this
hook did not author.

NEVER BLOCKS AND NEVER FAILS THE SESSION. No active run gives no output. Any
error — malformed stdin, an unreadable archive, anything unexpected — gives no
output. Every path exits 0.

FILES ONLY. Reads its stdin and files under `foundry-archive/`; never calls the
MCP server, never imports the server package, writes nothing.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

#: How the context describes what just happened to the session, by the event's
#: `source`. Anything else reads as a plain restart.
_SOURCE_PHRASES = {"compact": "compacted", "resume": "resumed"}


def _context(run, source: object) -> str:
    happened = _SOURCE_PHRASES.get(source, "restarted") if isinstance(source, str) else "restarted"
    return (
        f"[foundry] Foundry run '{run.name}' is live in phase {run.phase} "
        f"(foundry-archive/{run.name}/state.json). This session was just "
        f"{happened}, and the build carries on in this session: do not hand "
        "over, end the turn, or wait for /foundry:resume. Re-orient before "
        "anything else: (1) call Foundry-Context; (2) call Foundry-Next and "
        "follow the step it gives. A resumed session starts a fresh MCP server "
        "with no run loaded: if Foundry-Context reports no active run, call "
        f"Foundry-Init(resume='{run.name}') first, then Foundry-Context, then "
        "Foundry-Next."
    )


def main() -> int:
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from foundry_active_run import find_active_run, project_roots, read_event

        event = read_event(sys.stdin)
        if event is None:
            return 0  # an error gives no output
        run = find_active_run(project_roots(event))
        if run is None:
            return 0  # no active run: the common case, and silent
        envelope = {
            "hookSpecificOutput": {
                "hookEventName": "SessionStart",
                "additionalContext": _context(run, event.get("source")),
            }
        }
        sys.stdout.write(json.dumps(envelope) + "\n")
    except Exception:
        # A SessionStart hook must never degrade the session it starts: any
        # failure here means no re-orientation line, never an error.
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
