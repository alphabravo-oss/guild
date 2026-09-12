"""The active foundry run, as the two foundry hooks read it from disk.

SHARED by `stop-continue.py` (the Stop hook) and `session-start-run.py` (the
SessionStart compact/resume hook), so the two hooks can never disagree about
which run is live: there is one reader here, not two copies of it.

FILES ONLY. A hook never calls the MCP server and never imports the server
package, so this module is plain standard library. It reads `state.json` files
under `foundry-archive/`, writes nothing, and its public functions never raise:
an unreadable file is an answer ("not a qualifying run"), not an exception, and
a `cwd` no filesystem call can encode is a root dropped, not a crash. That
contract is load-bearing rather than tidy. The Stop hook's caller has no
handler: an exception raised here reaches the interpreter, and the script exits
having printed NEITHER a block nor an allow, which the platform reads as
"stop" — so a single throw silently removes the backstop from a live build
(CT-011, AC-004, FR-041, GI-002).

THE ACTIVE-RUN RULE — the lead's ruling, verbatim, shared with casting 1's
/foundry:stop shell step (`commands/stop.md` STEP 0), which applies the same
rule to decide which run its one-time stop token names:

    The active run is the run under `<cwd>/foundry-archive/` whose state.json
    phase is F1..F5.5 and not HALTED or DONE. If several qualify, take the one
    whose state.json was modified most recently.

How this module applies it, point by point:

  * F1..F5.5 is `LIVE_PHASES` below: the server's
    `schemas/vocab.py#POST_CAST_RUN_PHASES`, re-typed because a hook may not
    import the server. HALTED and F6 (DONE) are not members, and neither are
    the pre-CAST phases F0, F0.5 and F0.9. `tests/test_stop_hook.py` holds this
    tuple, vocab's set and stop.md's own `LIVE_PHASES` equal, so a phase added
    to one and not the others turns a test red instead of letting two readers
    quietly disagree.
  * "Modified most recently" is the state.json file's mtime. A tie breaks on
    the run directory path, exactly as stop.md's `max(candidates)` over
    `(mtime, run_dir)` pairs breaks it, so the scripts agree even then.
  * A candidate whose state.json cannot be read — missing, unreadable, not
    JSON, not a JSON object — is not a qualifying run. It never hides a
    readable live run beside it, and when no readable live run exists the
    answer is "no active run".
  * `<cwd>` is the hook event's `cwd` when stdin parses; otherwise (or
    additionally) CLAUDE_PROJECT_DIR, then the process working directory — the
    lead's order. The run is taken from the FIRST of those roots that holds a
    qualifying run. That differs from "the first root, full stop" in exactly
    one case: the session's `cwd` has drifted below the project — a lead that
    ran `cd plugins/foundry/mcp-server` to run the suite — so
    `<cwd>/foundry-archive/` does not exist. The server writes its archive
    under CLAUDE_PROJECT_DIR (plugin.json launches it with
    `--project-root ${CLAUDE_PROJECT_DIR}`), so falling through to that root
    finds the run the server is actually driving instead of silently opening
    the Stop hook. When `cwd` holds the live run, the result is the ruling's.

THE PARKED STATE this module reads is the server-owned top-level `parked` key
that `orchestration/park.py` writes, spelled once in `schemas/vocab.py`:

    "parked": {"items": [...],
               "awaiting_human": null | {"set_at", "cycle", "item_ids"}}

Only `parked.awaiting_human` matters to a hook, and only whether it is an ask
of the shape the server writes: an object naming at least one parked item. The
router writes it when every remaining unit of work is parked and it has put the
questions to the human; that ask is the ONE thing that lets a mid-build turn
end. A `parked` that is absent or not an object, an `awaiting_human` that is
null or not an object, an object naming no item, and an `items` list however
long all read as "no ask outstanding". The hook never writes the key.

THE READER IS NO LAXER THAN THE WRITER. `park.py#set_awaiting_human` returns
None rather than writing a marker when none of the ids handed to it is an open
parked item, so an ask naming nothing is a thing the server never writes. What
the writer will not write, this reader will not honour: see `_awaiting_human`.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import NamedTuple

#: The archive directory under a project root, and the one file read per run.
ARCHIVE_DIRNAME = "foundry-archive"
STATE_FILENAME = "state.json"

#: F1..F5.5 — start_cast (CAST) through NYQUIST: the phases in which the
#: operator is away and the build must keep moving. Mirrors
#: `schemas/vocab.py#POST_CAST_RUN_PHASES` and stop.md's `LIVE_PHASES`.
LIVE_PHASES = ("F1", "F2", "F3", "F4", "F5", "F5.5")

#: `schemas/vocab.py#PARKED_STATE_KEY`, `#PARKED_AWAITING_HUMAN_KEY` and
#: `#AWAITING_FIELD_ITEM_IDS`, spelled as literals because a hook may not
#: import the server package. `tests/test_stop_hook.py` holds the three copies
#: equal to the server's own, so a rename there turns a test red rather than
#: quietly making this reader look at a key nothing writes.
PARKED_KEY = "parked"
AWAITING_HUMAN_KEY = "awaiting_human"
AWAITING_ITEM_IDS_KEY = "item_ids"


class ActiveRun(NamedTuple):
    """The run the hooks act on, and the one fact about it the Stop hook needs."""

    name: str
    phase: str
    state_path: Path
    awaiting_human: bool


def read_event(stream) -> dict | None:
    """The hook event from ``stream`` (stdin), or None when missing or malformed.

    Never raises. A closed or absent stream, an interactive terminal (nothing
    was piped), bytes that are not UTF-8, text that is not JSON and JSON that
    is not an object all read as None: what a caller does with "no event" is
    that caller's rule, not this reader's.
    """
    try:
        if stream is None or stream.isatty():
            return None
        event = json.loads(stream.read())
    except (OSError, ValueError, RecursionError):
        return None
    return event if isinstance(event, dict) else None


def project_roots(event: dict | None) -> list[Path]:
    """The roots to look for `foundry-archive/` under, in the ruling's order.

    The event's `cwd` (when the event parsed and carries one), then
    CLAUDE_PROJECT_DIR, then the process working directory. A root that names
    the same directory as an earlier one is dropped, and so is a root that
    cannot be resolved to a name at all.

    NEVER RAISES, because the event's `cwd` is a string this hook did not
    author: it arrives on stdin, and JSON carries an embedded NUL or a lone
    surrogate as happily as it carries a path. Resolving one raises — a
    ValueError for the NUL, a UnicodeEncodeError (which subclasses it) for the
    surrogate — and the resolution here exists only to tell two roots naming
    one directory apart, so a root it cannot name is simply not a root. It
    names no directory a `state.json` could live under, so dropping it loses
    nothing real and the next root decides: CLAUDE_PROJECT_DIR, which is the
    root the server writes the archive under. Dropping it is NOT a licence to
    block — with no live run under any surviving root there is still no run,
    and both hooks stay the silent no-ops they are in every session that is not
    a foundry build.
    """
    raw_roots: list[str] = []
    if event is not None:
        cwd = event.get("cwd")
        if isinstance(cwd, str) and cwd:
            raw_roots.append(cwd)
    project_dir = os.environ.get("CLAUDE_PROJECT_DIR")
    if project_dir:
        raw_roots.append(project_dir)
    try:
        raw_roots.append(os.getcwd())
    except OSError:
        pass
    roots: list[Path] = []
    seen: set[str] = set()
    for raw in raw_roots:
        try:
            key = os.path.realpath(raw)
        except (OSError, ValueError):
            continue  # a root that cannot be named is no root; the next decides
        if key not in seen:
            seen.add(key)
            roots.append(Path(raw))
    return roots


def find_active_run(roots) -> ActiveRun | None:
    """The active run under the first root in ``roots`` that holds one, or None."""
    for root in roots:
        run = _active_run_under(Path(root))
        if run is not None:
            return run
    return None


def _active_run_under(root: Path) -> ActiveRun | None:
    """The active run under ``root/foundry-archive/`` by the shared rule, or None."""
    archive = root / ARCHIVE_DIRNAME
    try:
        state_paths = sorted(archive.glob(f"*/{STATE_FILENAME}")) if archive.is_dir() else []
    except OSError:
        return None
    candidates = []
    for state_path in state_paths:
        state = _read_state(state_path)
        if state is None:
            continue
        phase = state.get("phase")
        if phase not in LIVE_PHASES:
            continue
        try:
            mtime = state_path.stat().st_mtime
        except OSError:
            continue
        candidates.append((mtime, state_path.parent, phase, state))
    if not candidates:
        return None
    _mtime, run_dir, phase, state = max(candidates, key=lambda c: (c[0], c[1]))
    return ActiveRun(
        name=run_dir.name,
        phase=phase,
        state_path=run_dir / STATE_FILENAME,
        awaiting_human=_awaiting_human(state),
    )


def _read_state(path: Path) -> dict | None:
    """``path`` parsed as a JSON object, or None when it cannot be read as one."""
    try:
        with path.open(encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError, RecursionError):
        return None
    return data if isinstance(data, dict) else None


def _awaiting_human(state: dict) -> bool:
    """True only for an ask of the shape the server writes: an object at
    ``state["parked"]["awaiting_human"]`` naming at least one parked item.

    MIRRORS THE WRITER'S FLOOR, which is `park.py#set_awaiting_human`'s own
    refusal to write an ask that names nothing. Keying on "is an object" alone
    was laxer than the writer: an `awaiting_human` of `{}` — reachable from a
    corrupted or hand-edited `state.json` — opened this hook's ONE allow path
    for a live build, ending the turn on an ask with nothing to wait for. An
    object the writer would not have written is no ask, and the run keeps the
    turn (GI-011, AC-034, FR-036).

    The floor is the writer's and no stricter: one named id is enough, and
    `set_at` and `cycle` are not required here. Reading more of the marker than
    the decision needs would fail CLOSED on a writer that legitimately changed
    a field this hook never uses.
    """
    parked = state.get(PARKED_KEY)
    if not isinstance(parked, dict):
        return False
    marker = parked.get(AWAITING_HUMAN_KEY)
    if not isinstance(marker, dict):
        return False
    item_ids = marker.get(AWAITING_ITEM_IDS_KEY)
    if not isinstance(item_ids, list):
        return False
    return any(isinstance(ident, str) and ident for ident in item_ids)
