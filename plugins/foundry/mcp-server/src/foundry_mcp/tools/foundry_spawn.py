"""Foundry teammate spawn — reads the pre-authored casting prompt file.

Architecture principle: **plans are prompts.** Teammate prompts are
authored ONCE by decompose at F0.5, written to disk as
`foundry-archive/{run}/castings/casting-{id}-prompt.md`, validated at F0.9,
and frozen. The lead never drafts or modifies teammate prompts — it calls
this tool with a casting_id and passes the returned text directly to the
Agent tool.

This eliminates the "lead drafts prompt from casting" step where spec
fidelity used to silently erode via paraphrasing, scope cuts, or hedge
language. The lead is a router, not an interpreter.

PROGRESS LEDGER (FR-015)
------------------------
This module also owns both halves of the per-agent progress ledger:

  write side  the spawn prompt text returned to the lead carries a
              ``progress_protocol`` block telling the spawned agent to append
              periodic phase/step/timestamp lines to its own file under
              ``foundry-archive/{run}/progress/``;
  read side   ``foundry_liveness`` globs that directory and reports each
              agent's last-progress age against a stall threshold.

The two halves are one closed loop and must stay in sync: the path and line
shape named in ``_progress_protocol_block`` are exactly what
``_read_progress_ledger`` parses back. Change one and you must change both.

The ledger copies ``spawns.log``'s shape (JSONL, one object per line, ISO-8601
UTC ``timestamp``, append-only, failures swallowed) because that is this run
directory's established audit-ledger idiom — with one addition that carries
the whole point: every line names the ``step`` the agent has reached. A bare
timestamp ping would prove the process is alive without proving it is getting
anywhere, and "alive but not advancing" is the failure mode the lead actually
needs to see (A-025: "Distinguishes no-heartbeat from heartbeat-but-no-progress").

An agent's LAST line carries ``"done": true``. Without a terminal line an agent
does not stop being watched when it finishes — it stops writing, crosses the
stall threshold, and reports ``stalled`` for the remainder of the run, so every
completed casting silts up ``needs_attention`` until the lead stops reading it.
A watchlist that is mostly finished work is a watchlist nobody watches.

WHO GETS A LEDGER
-----------------
Both spawn tools here serve TEAMMATES, but they are not the only agents a run
spawns. The four INSPECT streams that file into the defect ledger — TRACE,
FLOW_TRACE, PROVE, RESEARCH_AUDIT — are spawned by the lead from the F2 roster
in ``commands/start.md`` against the ``agent_configs`` ``Foundry-Next``
returns, never through this module, so nothing here can hand them the protocol
at spawn time. ``foundry_liveness`` closes that half from the read side: while
the run is in INSPECT it reports each expected stream agent that has written no
ledger under the ``no_ledger`` status, carrying that agent's own
``progress_protocol`` block for the lead to append. The durable placement for
that text is each stream's own agent file (GI-001's shape); until it lands
there, this is the channel that makes the four streams visible at all rather
than silently absent from the roster.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from foundry_mcp.schemas import vocab
from foundry_mcp.tools.foundry_orchestrator import agent_model
from foundry_mcp.tools.foundry_state import get_run_dir


# Both spawn paths in this module dispatch the same agent, so the model the
# lead must pass is resolved in one place. ``""`` means "pass no model
# parameter" — teammate's frontmatter pin (model=opus + effort=xhigh) governs
# and behavior is identical to a build without the option (FR-003, OT-001).
TEAMMATE_SUBAGENT_TYPE = "foundry:teammate"

# Per-agent progress ledgers live one directory below the run root, one
# ``{agent_id}.jsonl`` per agent. Created lazily by whichever agent writes
# first — the server never creates it, so an empty roster and an unstarted run
# are the same observable state.
PROGRESS_DIR_NAME = "progress"

# ---------------------------------------------------------------------------
# Cadence and stall threshold (FR-025).
#
# FR-025 makes these the implementer's choice but requires the choice be
# "derived from real spawn timings rather than a generic constant". The
# derivation, from `foundry-archive/grand-vulture/spawns.log` (83 spawn
# records over 28.5h, grouped into 24 batches on a >60s gap, where a batch's
# working duration is the gap from its last spawn to the next batch's first):
#
#     within-batch gap        median 0.70s   (a bulk wave writes all its
#                                             spawn records in under a second)
#     batch working duration  min 1.1 min / median 56.0 min / p90 90.7 min
#                             outliers 253.5 and 384.3 min — the LEAD was
#                             away overnight, not the agent, so these must
#                             not be allowed to set the threshold.
#
# CADENCE 300s (5 min). A real teammate works for tens of minutes per batch,
# so a line every 5 minutes is ~11 lines across a median batch — enough
# resolution to locate where an agent died, few enough that the ledger stays
# a handful of KB over a 28h run.
#
# THRESHOLD 900s (15 min) = 3x the cadence. Three consecutive missed lines is
# a real signal rather than one slow file read. Checked against the measured
# distribution: 15 min is 13.6x the SHORTEST genuine working batch (1.1 min),
# so a short-but-real batch can never trip it, and 0.27x the median batch
# (56 min), so a dead agent is caught roughly four times over within a normal
# batch instead of at the end of one.
#
# NOT copied: the lead stall watchdog's 180s (`foundry_orchestrator.py`
# `.last-next-at`). That number is tuned to a LEAD's tool-call cadence, where
# three quiet minutes is genuinely anomalous. A teammate that reads ten files
# before writing a line would trip it constantly. The marker-plus-threshold
# SHAPE is the prior art worth copying; the constant is not.
#
# Both are overridable per call via ``foundry_liveness(stall_seconds=...)``.
PROGRESS_CADENCE_SECONDS = 300
STALL_THRESHOLD_SECONDS = 900

# CLOSED VOCABULARY — the liveness status of one agent. Owned here rather than
# in `schemas/vocab.py`: these values are this tool's report vocabulary, never
# go on the Foundry-Stream/Foundry-Defect wire, and are not persisted in any
# run artifact. Extend only via phase-level RFC.
#
# The three live values are a two-axis answer to "slow or dead", which is why
# a bare heartbeat cannot produce them:
#   PROGRESSING  a line arrived inside the threshold AND it named a step the
#                agent had not already been sitting on — genuinely advancing.
#   NO_PROGRESS  lines keep arriving, but (phase, step) has not changed for
#                longer than the threshold. Alive, not advancing. This is the
#                "heartbeat-but-no-progress" case A-025 names, and it is
#                invisible to any liveness scheme that only timestamps a ping.
#   STALLED      no line at all inside the threshold. The no-heartbeat case.
#   UNKNOWN      a ledger file exists but holds no parseable line, so the
#                honest answer is that we cannot tell. Reported rather than
#                folded into STALLED, because STALLED asserts a last-progress
#                time and here there is none to assert.
#
# Two more that are about the ENDS of an agent's life rather than its middle:
#   DONE         the last line carried `"done": true`. Terminal, and it
#                outranks every age check — a finished agent is finished
#                however long ago it finished. Without this status a completed
#                casting simply stops writing, crosses the threshold, and
#                reports STALLED forever, so `needs_attention` fills with
#                finished work and stops being a watchlist.
#   NO_LEDGER    the run expects this agent to be writing and no ledger file
#                exists. Distinct from STALLED (which dates a real last line)
#                and from UNKNOWN (which has a file to point at). This is the
#                only status not derived from a ledger, and it is what makes
#                an agent that never wrote one visible at all.
STATUS_PROGRESSING = "progressing"
STATUS_NO_PROGRESS = "no_progress"
STATUS_STALLED = "stalled"
STATUS_UNKNOWN = "unknown"
STATUS_DONE = "done"
STATUS_NO_LEDGER = "no_ledger"

PROGRESS_STATUSES = frozenset(
    {
        STATUS_PROGRESSING,
        STATUS_NO_PROGRESS,
        STATUS_STALLED,
        STATUS_UNKNOWN,
        STATUS_DONE,
        STATUS_NO_LEDGER,
    }
)  # 6 items

# Every status the lead should look at. PROGRESSING is working as intended and
# DONE is finished work — neither is a call to action, and putting DONE here
# would rebuild the exact silting-up this status exists to stop.
NEEDS_ATTENTION_STATUSES = frozenset(
    {STATUS_NO_PROGRESS, STATUS_STALLED, STATUS_UNKNOWN, STATUS_NO_LEDGER}
)  # 4 items

# The ledger field an agent sets on its final line to declare itself finished.
# A dedicated boolean rather than a reserved `step` spelling: `step` is free
# prose an agent writes in its own words, so any sentinel value there would
# collide with a real step sooner or later.
TERMINAL_FIELD = "done"

# The four INSPECT stream agents that file into the defect ledger. Not a new
# enum — these are the lowercase wire spellings `schemas/vocab.py` already
# owns (the same ids `Foundry-Stream` takes and a defect's `source` persists),
# so one agent carries one id across the whole protocol and its ledger
# filename is that id. The import-time check below is what keeps that true.
#
# Why these four and not all nine wire ids: SIGHT and TEST/PROBE are the lead's
# own work rather than spawned agents ("lead runs Playwright directly", "inline
# test suite" — commands/start.md F2 roster), and COVERAGE_DIFF and TEST-01 are
# conditional streams. These four are the roster GI-001 names, and they are the
# four D-021 found invisible. Extend only via phase-level RFC.
INSPECT_STREAM_AGENT_IDS = ("trace", "flow_trace", "prove", "research_audit")  # 4 items

_unknown_stream_ids = set(INSPECT_STREAM_AGENT_IDS) - set(vocab.STREAM_WIRE_IDS)
if _unknown_stream_ids:  # pragma: no cover - import-time contract guard
    raise ImportError(
        f"INSPECT_STREAM_AGENT_IDS drifted from vocab.STREAM_WIRE_IDS: "
        f"{sorted(_unknown_stream_ids)} are not canonical wire ids. The stream "
        f"roster is vocab.py's to own; fix the spelling here rather than "
        f"re-declaring the vocabulary."
    )
del _unknown_stream_ids

# The state.json phase during which the INSPECT streams are running and are
# therefore expected to be writing ledgers. Outside it they are not late, they
# are simply not spawned, and reporting them would be the false alarm that
# teaches a lead to ignore the roster.
INSPECT_PHASE = "F2"


def _teammate_model() -> str:
    """Return the model to spawn ``foundry:teammate`` with, or ``""``."""
    return agent_model(TEAMMATE_SUBAGENT_TYPE).get("model", "")


def _progress_dir(fdir: Path) -> Path:
    """Return the run's per-agent progress-ledger directory (may not exist)."""
    return fdir / PROGRESS_DIR_NAME


def _agent_id_for_casting(casting_id: int | str) -> str:
    """Return the ledger agent id for a casting's teammate.

    One id per casting, not per spawn: a casting's GRIND re-dispatches are the
    same worker resuming, so their lines belong in the same ledger and the
    lead reads one continuous history per casting.
    """
    return f"casting-{casting_id}"


# The two agent-shaped halves of the protocol block. Everything else in it —
# the path, the field names, the cadence, the threshold, the terminal line — is
# identical for every agent, because it is the half `foundry_liveness` parses.
# Only the examples differ, and they have to: a teammate handed a block that
# names INSPECT steps, or a stream agent told its phase is `cast`, would write
# a truthful-looking ledger describing work it is not doing.
TEAMMATE_PHASE_HINT = "the run phase you were spawned for (`cast` or `grind`)"
TEAMMATE_STEP_EXAMPLES = (
    "Read Floor done, Approach Deliberation written, each file edited, "
    "self-check run, commit made"
)

STREAM_PHASE_HINT = "the run phase you were spawned for (`inspect`)"
STREAM_STEP_EXAMPLES = (
    "scope read, each casting or requirement swept, findings assembled, "
    "Foundry-Sync called"
)


def _progress_protocol_block(
    run_name: str,
    agent_id: str,
    phase_hint: str = TEAMMATE_PHASE_HINT,
    step_examples: str = TEAMMATE_STEP_EXAMPLES,
) -> str:
    """Return the progress-ledger protocol block for one spawned agent (FR-015).

    The lead appends this BELOW the agent's prompt, the same placement rule
    ``grind_cycle_context`` already uses — the prompt itself stays verbatim.

    The path and the field names below are the write half of the loop
    ``foundry_liveness`` reads back; the cadence and threshold are
    interpolated from the module constants so the prose an agent obeys cannot
    drift from the numbers the tool judges it against.

    ``phase_hint`` and ``step_examples`` are the only agent-shaped parts. They
    default to the teammate wording, so the two spawn tools in this module call
    this with an id and nothing else; ``STREAM_PHASE_HINT`` /
    ``STREAM_STEP_EXAMPLES`` produce the INSPECT-stream variant.
    """
    ledger = f"foundry-archive/{run_name}/{PROGRESS_DIR_NAME}/{agent_id}.jsonl"
    cadence_min = PROGRESS_CADENCE_SECONDS // 60
    stall_min = STALL_THRESHOLD_SECONDS // 60
    return "\n".join(
        [
            "## Progress ledger protocol (APPEND A LINE AS YOU WORK)",
            "",
            f"Your progress ledger is `{ledger}`.",
            "",
            "Append one JSON object per line to it, carrying exactly these three fields:",
            "",
            "```",
            '{"timestamp": "2026-08-31T19:04:22+00:00", "phase": "cast", "step": "read floor complete"}',
            "```",
            "",
            "- `timestamp` — ISO-8601 **UTC**, with the offset. A bare local time is a guess.",
            f"- `phase` — {phase_hint}.",
            "- `step` — where you have actually got to, in a few words.",
            "",
            "Write a line when you start, and again every time you reach a NEW step: "
            f"{step_examples}. Never let more than {cadence_min} minutes of work pass "
            "without a line.",
            "",
            f"`step` is the load-bearing field. The lead's `Foundry-Liveness` query reads "
            f"this file and reports you as `{STATUS_STALLED}` if no line arrives for "
            f"{stall_min} minutes, and as `{STATUS_NO_PROGRESS}` if lines keep arriving "
            f"while `step` stays identical for {stall_min} minutes — alive but not "
            "advancing, which is just as alarming to the lead as silence. So change "
            "`step` when the work moves, and do not pad the ledger with repeats to look busy.",
            "",
            "### Your LAST line declares you finished",
            "",
            f"When your work is done, append one final line carrying `\"{TERMINAL_FIELD}\": true` "
            "alongside the usual three fields:",
            "",
            "```",
            '{"timestamp": "2026-08-31T20:11:07+00:00", "phase": "cast", '
            f'"step": "committed 9f21ac3", "{TERMINAL_FIELD}": true}}',
            "```",
            "",
            "This is not bookkeeping. Finishing does not take you off the lead's watchlist "
            "— it just stops your ledger, so without a terminal line you cross the "
            f"{stall_min}-minute threshold and are reported as `{STATUS_STALLED}` for the "
            f"rest of the run. Write it and you are reported as `{STATUS_DONE}` and drop "
            "out of `needs_attention`, which is what keeps that list worth reading: a "
            "roster where every finished agent looks stalled teaches the lead to ignore "
            "the one that really is.",
            "",
            f"Create the `{PROGRESS_DIR_NAME}/` directory if it does not exist, and open the "
            "file in append mode — never rewrite it. A failed append must NEVER block your "
            "work: swallow the error and carry on.",
            "",
        ]
    )


def _parse_progress_timestamp(value: object) -> datetime | None:
    """Parse a ledger line's ``timestamp`` into an aware UTC datetime, or None.

    Total and never-raising: a missing, non-string, or unparseable value means
    "this line carries no usable time", never an exception across the MCP
    boundary. A naive timestamp is read as UTC rather than discarded — an
    agent that wrote ``datetime.now().isoformat()`` still gets counted, and
    the protocol block above asks for the offset precisely so that guess is
    rarely needed.
    """
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip())
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _read_progress_ledger(path: Path) -> list[dict]:
    """Return the parseable progress lines in one ledger, oldest first.

    Malformed lines are skipped rather than fatal: the ledger is written by a
    working agent with a best-effort append that may be interrupted mid-line,
    and one torn write must not blind the lead to every good line around it.
    Lines are returned in file order — an append-only ledger is already
    chronological, and re-sorting would hide a clock anomaly worth seeing.
    """
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return []

    lines: list[dict] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(record, dict):
            continue
        moment = _parse_progress_timestamp(record.get("timestamp"))
        if moment is None:
            continue
        lines.append({"record": record, "moment": moment})
    return lines


def _is_terminal(record: dict) -> bool:
    """True when a ledger line declares the agent finished.

    Strictly ``True``, never merely truthy: a ledger is hand-written prose from
    a working agent, and ``"done": "not yet"`` must not retire it.
    """
    return record.get(TERMINAL_FIELD) is True


def _step_key(record: dict) -> tuple[str, str]:
    """Return the (phase, step) pair identifying where an agent has got to."""
    phase = record.get("phase")
    step = record.get("step")
    return (
        phase.strip() if isinstance(phase, str) else "",
        step.strip() if isinstance(step, str) else "",
    )


def _agent_liveness_record(
    path: Path,
    now: datetime,
    threshold: float,
    run_rel: str,
) -> dict:
    """Report one agent's liveness from its ledger file (CT-004, OT-010).

    Two ages, because one number cannot answer "slow or dead":

      ``last_line_age_seconds``      when the agent last wrote ANYTHING — the
                                     heartbeat age.
      ``last_progress_age_seconds``  when the agent last reached a NEW
                                     (phase, step) — the progress age, and the
                                     number AC-021 asks for. Computed by
                                     walking back over the trailing run of
                                     lines that all repeat the final step, so
                                     it dates the moment that step was first
                                     reached rather than the last time it was
                                     restated.

    Both ages are equal while an agent is advancing normally; they separate
    exactly when it starts pinging without progressing.
    """
    agent_id = path.stem
    ledger_path = f"{run_rel}/{PROGRESS_DIR_NAME}/{path.name}"
    lines = _read_progress_ledger(path)

    if not lines:
        return {
            "agent": agent_id,
            "status": STATUS_UNKNOWN,
            "last_progress_age_seconds": None,
            "last_line_age_seconds": None,
            "phase": None,
            "step": None,
            "last_timestamp": None,
            "progress_since": None,
            "lines": 0,
            "ledger": ledger_path,
            "detail": (
                "Ledger file exists but holds no parseable progress line "
                "(expected one JSON object per line with a timestamp field)."
            ),
        }

    last = lines[-1]
    current_step = _step_key(last["record"])

    # Walk back over the trailing repeats of the current step to find when the
    # agent first reached it.
    progress_moment = last["moment"]
    for entry in reversed(lines[:-1]):
        if _step_key(entry["record"]) != current_step:
            break
        progress_moment = entry["moment"]

    last_line_age = (now - last["moment"]).total_seconds()
    last_progress_age = (now - progress_moment).total_seconds()

    # Terminal outranks every age check: a finished agent is finished however
    # long ago it finished, and ageing it into STALLED is precisely the bug
    # this branch exists to prevent.
    if _is_terminal(last["record"]):
        status = STATUS_DONE
    elif last_line_age >= threshold:
        status = STATUS_STALLED
    elif last_progress_age >= threshold:
        status = STATUS_NO_PROGRESS
    else:
        status = STATUS_PROGRESSING

    phase, step = current_step
    return {
        "agent": agent_id,
        "status": status,
        "last_progress_age_seconds": int(last_progress_age),
        "last_line_age_seconds": int(last_line_age),
        "phase": phase or None,
        "step": step or None,
        "last_timestamp": last["moment"].isoformat(),
        "progress_since": progress_moment.isoformat(),
        "lines": len(lines),
        "ledger": ledger_path,
    }


def _load_run_state(fdir: Path) -> dict:
    """Return the run's ``state.json`` as a dict, or ``{}`` if unreadable.

    Read-only and never-raising. Liveness is a diagnostic: a missing or torn
    state file must degrade it to "I cannot tell which phase this is", never
    fail the call.
    """
    try:
        data = json.loads((fdir / "state.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _skipped_stream_ids(fdir: Path) -> set[str]:
    """Return the wire ids of streams this run declared it would not spawn.

    ``manifest.stream_skips`` is F0.5's predictive skip list (entries carry a
    ``stream_id`` in the canonical UPPERCASE spelling plus a ``reason``); a
    bare string entry is accepted too, so an older manifest still reads. The
    canonical spelling is mapped back to its wire id through
    ``vocab.WIRE_TO_CANONICAL`` rather than by lowercasing, because the two
    spellings are not related by case alone (``TEST-01`` / ``test01``).
    """
    try:
        manifest = json.loads(
            (fdir / "castings" / "manifest.json").read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError):
        return set()
    if not isinstance(manifest, dict):
        return set()

    canonical_to_wire = {
        canonical: wire for wire, canonical in vocab.WIRE_TO_CANONICAL.items()
    }
    skipped: set[str] = set()
    for entry in manifest.get("stream_skips") or []:
        if isinstance(entry, dict):
            raw = entry.get("stream_id") or entry.get("stream") or entry.get("id")
        else:
            raw = entry
        if not isinstance(raw, str) or not raw.strip():
            continue
        token = raw.strip()
        if token.lower() in vocab.STREAM_WIRE_IDS:
            skipped.add(token.lower())
        elif token.upper() in canonical_to_wire:
            skipped.add(canonical_to_wire[token.upper()])
    return skipped


def _expected_inspect_stream_agents(fdir: Path) -> list[str]:
    """Return the stream agents this run actually spawns at INSPECT (D-021).

    Conditional by necessity. Reporting a stream the run was never going to
    run is a permanent false alarm, and a ``needs_attention`` list that is
    always wrong about the same agent is the same disease D-022 names: the
    lead learns to skip it. So each of the three conditions below is the
    stream's own documented spawn condition from the F2 roster:

      * anything in ``manifest.stream_skips`` was predictively skipped at F0.5;
      * FLOW_TRACE is "V3 only, when ``flow-delta.json`` exists";
      * RESEARCH_AUDIT is skipped when the run gathered no research.
    """
    skipped = _skipped_stream_ids(fdir)
    expected: list[str] = []
    for wire_id in INSPECT_STREAM_AGENT_IDS:
        if wire_id in skipped:
            continue
        if wire_id == "flow_trace" and not (fdir / "flow-delta.json").exists():
            continue
        if wire_id == "research_audit" and not (fdir / "research").is_dir():
            continue
        expected.append(wire_id)
    return expected


def _missing_stream_records(
    fdir: Path,
    now: datetime,
    threshold: float,
    run_rel: str,
) -> list[dict]:
    """Report expected INSPECT stream agents that have written no ledger (D-021).

    The four defect-filing streams are spawned from the F2 roster, not from
    either tool in this module, so nothing hands them the progress protocol at
    spawn time and a glob over ``progress/`` cannot see them at all — the
    roster simply has no row where they should be. These records are that row,
    and each one carries the agent's own ``progress_protocol`` block so the
    answer to "why is this stream invisible?" arrives with the thing that
    fixes it.

    Two gates keep this from crying wolf. The run must be IN ``INSPECT_PHASE``
    — outside it the streams are not late, they are not spawned. And the phase
    must have been running for longer than the stall threshold, so a stream
    spawned twenty seconds ago is not reported for not having written yet;
    below that bar the honest answer is "too early to say", which is silence.
    """
    state = _load_run_state(fdir)
    if state.get("phase") != INSPECT_PHASE:
        return []

    phase_times = state.get("phase_times")
    entered = None
    if isinstance(phase_times, dict):
        entry = phase_times.get(INSPECT_PHASE)
        if isinstance(entry, dict):
            entered = _parse_progress_timestamp(entry.get("started_at"))
    if entered is None:
        return []

    expected_age = (now - entered).total_seconds()
    if expected_age < threshold:
        return []

    run_name = fdir.name
    return [
        {
            "agent": wire_id,
            "status": STATUS_NO_LEDGER,
            "last_progress_age_seconds": None,
            "last_line_age_seconds": None,
            "phase": "inspect",
            "step": None,
            "last_timestamp": None,
            "progress_since": None,
            "lines": 0,
            "ledger": f"{run_rel}/{PROGRESS_DIR_NAME}/{wire_id}.jsonl",
            "expected_since_seconds": int(expected_age),
            "detail": (
                f"No progress ledger exists for the {wire_id} stream agent, which this "
                f"run has been expecting since it entered {INSPECT_PHASE}. Stream agents "
                "are spawned from the F2 roster rather than from Foundry-Spawn-Teammate, "
                "so nothing hands them the protocol automatically — append the "
                "`progress_protocol` block on this record to the agent's prompt."
            ),
            "progress_protocol": _progress_protocol_block(
                run_name, wire_id, STREAM_PHASE_HINT, STREAM_STEP_EXAMPLES
            ),
        }
        for wire_id in _expected_inspect_stream_agents(fdir)
    ]


def foundry_liveness(
    agent: str | None = None,
    stall_seconds: int | float | None = None,
    project_root: str = ".",
) -> dict:
    """Report per-agent last-progress age against the stall threshold (CT-004).

    Registered as the MCP tool ``Foundry-Liveness``. Answers the lead's "slow
    or dead" question from the progress ledgers the spawn prompts told agents
    to write.

    Args:
        agent: A single agent id — a ledger filename stem (``"casting-4"``) or
            an expected INSPECT stream agent (``"trace"``, ``"prove"``).
            Omitted / ``None`` reports every agent in the run.
        stall_seconds: Per-call override of ``STALL_THRESHOLD_SECONDS``.
        project_root: Repo root.

    Returns:
        On success:
            {
                "ok": True,
                "run": "{run}",
                "stall_threshold_seconds": 900,
                "progress_cadence_seconds": 300,
                "checked_at": "<ISO-8601 UTC>",
                "agents": [ {"agent": ..., "status": ..., ...}, ... ],
                "needs_attention": ["casting-7", ...],
            }
        On failure:
            {"ok": False, "error": "...", "hint": "..."}

    Roster policy: every ledger file in the run, plus — while the run is in
    INSPECT — the stream agents it expects that have written no ledger (D-021).
    A run where nothing has written a ledger and no stream is overdue reports
    an empty roster with ``ok: True``: an unstarted ledger is a normal
    early-run state, not an error.

    Nothing is enumerated from ``spawns.log``. It would surface the deadest
    teammate case of all — spawned, never wrote a line — but the log records a
    spawn REQUEST rather than a live worker, so a run's finished waves would
    accumulate in the roster forever with no signal that says otherwise. The
    INSPECT streams are added despite writing no spawn record because their
    expectation window is bounded by a phase that ends, which is exactly the
    property ``spawns.log`` lacks. A teammate that dies before its first line
    is still invisible here; see ``concerns.md`` for that gap.

    An explicitly-named agent with neither a ledger nor an expectation IS a
    named refusal: the caller asked about a specific worker and the honest
    answer is that no such agent is known to this run, with the ones that are
    listed in the hint.
    """
    fdir = get_run_dir(project_root)
    if not fdir:
        return {"ok": False, "error": "No active foundry run", "hint": "Call Foundry-Init first"}
    if not fdir.exists():
        return {"ok": False, "error": "Foundry run directory not found", "hint": f"Expected {fdir}"}

    threshold: float = STALL_THRESHOLD_SECONDS
    if stall_seconds is not None:
        if isinstance(stall_seconds, bool) or not isinstance(stall_seconds, (int, float)):
            return {
                "ok": False,
                "error": f"Invalid stall_seconds: {stall_seconds!r}. Must be a positive number of seconds.",
                "hint": f"Omit it to use the derived default of {STALL_THRESHOLD_SECONDS}s.",
            }
        if stall_seconds <= 0:
            return {
                "ok": False,
                "error": f"Invalid stall_seconds: {stall_seconds}. Must be greater than 0.",
                "hint": f"Omit it to use the derived default of {STALL_THRESHOLD_SECONDS}s.",
            }
        threshold = float(stall_seconds)

    run_rel = f"foundry-archive/{fdir.name}"
    pdir = _progress_dir(fdir)
    ledgers = sorted(pdir.glob("*.jsonl")) if pdir.is_dir() else []
    now = datetime.now(timezone.utc)

    records = [_agent_liveness_record(p, now, threshold, run_rel) for p in ledgers]

    # An expected stream agent that HAS written a ledger is reported from that
    # ledger like anybody else; only the ones with nothing to read get a
    # synthesized row, so a stream that is obeying the protocol never appears
    # twice and never appears as no_ledger.
    have_ledgers = {record["agent"] for record in records}
    records.extend(
        record
        for record in _missing_stream_records(fdir, now, threshold, run_rel)
        if record["agent"] not in have_ledgers
    )
    records.sort(key=lambda record: record["agent"])
    known = [record["agent"] for record in records]

    if agent is not None:
        wanted = agent.strip()
        match = next((record for record in records if record["agent"] == wanted), None)
        if match is None:
            return {
                "ok": False,
                "error": f"No agent '{wanted}' is known to this run",
                "hint": (
                    f"Agents this run knows about: {known}"
                    if known
                    else (
                        f"No agent in this run has written a progress ledger yet. Agents are "
                        f"told where to write by the `progress_protocol` block returned with "
                        f"their spawn prompt; expected {run_rel}/{PROGRESS_DIR_NAME}/{wanted}.jsonl"
                    )
                ),
            }
        records = [match]

    result: dict = {
        "ok": True,
        "run": fdir.name,
        "stall_threshold_seconds": int(threshold),
        "progress_cadence_seconds": PROGRESS_CADENCE_SECONDS,
        "checked_at": now.isoformat(),
        "agents": records,
        "needs_attention": [
            r["agent"] for r in records if r["status"] in NEEDS_ATTENTION_STATUSES
        ],
    }

    missing = [r["agent"] for r in records if r["status"] == STATUS_NO_LEDGER]
    if missing:
        result["instructions"] = (
            f"{len(missing)} INSPECT stream agent(s) have written no progress ledger and "
            f"are therefore invisible to this tool: {', '.join(missing)}. The four "
            "defect-filing stream agents are spawned from the F2 roster, not from "
            "Foundry-Spawn-Teammate, so nothing hands them the progress protocol the way "
            "a teammate spawn does. APPEND each one's `progress_protocol` block (carried "
            "on its record above) BELOW that stream agent's prompt when you spawn it, "
            "exactly as you already do for a teammate. Until then a stream that dies "
            "early is indistinguishable from one still reading the spec."
        )

    if not records and agent is None:
        result["note"] = (
            "No agent in this run has written a progress ledger yet. Agents are told "
            "where to write by the `progress_protocol` block returned with their spawn "
            f"prompt; ledgers appear under {run_rel}/{PROGRESS_DIR_NAME}/."
        )

    return result


def foundry_spawn_teammate(
    casting_id: int | str,
    phase: str = "cast",
    project_root: str = ".",
) -> dict:
    """Read and return the pre-authored prompt for a casting.

    Args:
        casting_id: The id of the casting whose teammate prompt to read.
        phase: "cast" (F1) or "grind" (F3). Affects which prompt variant to
            return if both exist; otherwise identical.
        project_root: Repo root.

    Returns:
        On success:
            {
                "ok": True,
                "casting_id": N,
                "phase": "cast" | "grind",
                "prompt_path": "foundry-archive/{run}/castings/casting-N-prompt.md",
                "prompt_hash": "sha256:...",
                "prompt": "<full text of the pre-authored prompt>",
                "instructions": "Pass the `prompt` field verbatim to the Agent tool. Do NOT modify it. Do NOT prepend, append, or substitute text. Only the `prompt` content is authorized teammate context."
            }
        On failure:
            {"ok": False, "error": "...", "hint": "..."}
    """
    fdir = get_run_dir(project_root)
    if not fdir:
        return {"ok": False, "error": "No active foundry run", "hint": "Call Foundry-Init first"}
    if not fdir.exists():
        return {"ok": False, "error": "Foundry run directory not found", "hint": f"Expected {fdir}"}

    manifest_path = fdir / "castings" / "manifest.json"
    if not manifest_path.exists():
        return {"ok": False, "error": "No manifest.json", "hint": "Run F0.5 DECOMPOSE first"}

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        return {"ok": False, "error": f"manifest.json parse error: {e}"}

    castings = manifest.get("castings", [])
    casting = None
    for c in castings:
        if str(c.get("id")) == str(casting_id):
            casting = c
            break

    if not casting:
        available = [c.get("id") for c in castings]
        return {
            "ok": False,
            "error": f"casting_id {casting_id} not found in manifest",
            "hint": f"Available casting ids: {available}",
        }

    # Locate the pre-authored prompt file.
    prompt_path = fdir / "castings" / f"casting-{casting_id}-prompt.md"
    if not prompt_path.exists():
        return {
            "ok": False,
            "error": f"casting-{casting_id}-prompt.md does not exist",
            "hint": (
                "Decompose must write a pre-authored teammate prompt file for every casting. "
                "Re-run F0.5 DECOMPOSE or check that the decompose step wrote the prompt files."
            ),
        }

    prompt_text = prompt_path.read_text(encoding="utf-8")

    if not prompt_text.strip():
        return {
            "ok": False,
            "error": f"casting-{casting_id}-prompt.md is empty",
            "hint": "Re-run F0.5 DECOMPOSE to regenerate the prompt file.",
        }

    # Hash the prompt for audit tracking.
    prompt_hash = "sha256:" + hashlib.sha256(prompt_text.encode("utf-8")).hexdigest()[:16]

    model = _teammate_model()

    # Log the spawn for the audit trail. When a model is configured the record
    # names it, so which model each steerable agent was asked to run on is
    # observable in the run's spawn log rather than inferred (OT-011, NFR-001).
    # With nothing configured the entry is byte-identical to before.
    spawn_log = fdir / "spawns.log"
    try:
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "casting_id": casting_id,
            "phase": phase,
            "prompt_hash": prompt_hash,
            "prompt_path": str(prompt_path.relative_to(Path(project_root)) if prompt_path.is_absolute() else prompt_path),
        }
        if model:
            entry["model"] = model
        with spawn_log.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception:
        # Logging failures must not block the spawn.
        pass

    result: dict = {
        "ok": True,
        "casting_id": casting_id,
        "phase": phase,
        "prompt_path": str(prompt_path.relative_to(Path(project_root)) if prompt_path.is_absolute() else prompt_path),
        "prompt_hash": prompt_hash,
        "prompt": prompt_text,
        "instructions": (
            "Pass the `prompt` field VERBATIM to the Agent tool as the teammate's prompt. "
            "Do NOT modify, summarize, paraphrase, or augment the text. Do NOT add your own context, "
            "hedges, or scope notes. The prompt was authored at F0.5 DECOMPOSE with the master spec "
            "as source of truth and was validated at F0.9. Modifying it reintroduces the exact drift "
            "failure mode this architecture was built to prevent."
        ),
    }

    # Model steering. Present ONLY when the option is configured — an absent
    # key means "pass no model parameter", indistinguishable from a build where
    # this was never implemented (FR-004, CT-003, OT-004).
    if model:
        result["model"] = model
        result["instructions"] += (
            f" Model: pass model='{model}' on the Agent call — the foundry `model` "
            f"option is configured and {TEAMMATE_SUBAGENT_TYPE} follows it. Effort "
            "stays at the agent's frontmatter value."
        )

    # GRIND cycle context. When the teammate respawns to fix defects, the code
    # has already moved past CAST — earlier GRIND cycles may have
    # modified files this teammate owns or depends on. Without this block the
    # teammate re-explores from scratch and may re-do work already done.
    # The lead is expected to append this verbatim BEFORE the defect list
    # (if non-empty), so the teammate reads current state before acting.
    if phase == "grind":
        context = _build_grind_cycle_context(fdir, casting_id, project_root)
        if context:
            result["grind_cycle_context"] = context
            result["instructions"] += (
                " GRIND addendum: when appending the defect block BELOW this prompt, "
                "prepend the `grind_cycle_context` block FIRST so the teammate reads current "
                "file state before acting on defects."
            )

    # Progress ledger protocol (FR-015). Appended LAST, after any
    # grind_cycle_context and defect blocks, so the established (a) context
    # (b) defects order the orchestrator's own guidance names is undisturbed.
    result["progress_protocol"] = _progress_protocol_block(
        fdir.name, _agent_id_for_casting(casting_id)
    )
    result["instructions"] += (
        " Progress ledger: APPEND the `progress_protocol` block BELOW the prompt, LAST — "
        "after any grind_cycle_context and defect blocks. It tells the teammate where to "
        "write its progress lines; without it Foundry-Liveness has nothing to read back "
        "and a dead teammate is indistinguishable from a thinking one."
    )

    return result


def _build_grind_cycle_context(fdir, casting_id, project_root: str) -> str:
    """Return a '## Prior-cycle file changes' block for a GRIND teammate.

    Diffs HEAD against .cast-baseline-sha (stamped at CAST→INSPECT transition).
    Empty list = cycle 1 pre-edit, so we return empty string (nothing to append).
    Filters to the casting's declared key_files when available, falling back
    to the full diff when key_files aren't declared.
    """
    baseline_file = fdir / ".cast-baseline-sha"
    if not baseline_file.exists():
        return ""
    try:
        baseline_sha = baseline_file.read_text(encoding="utf-8").strip()
    except OSError:
        return ""
    if not baseline_sha:
        return ""

    import subprocess
    try:
        diff = subprocess.run(
            ["git", "-C", project_root, "diff", "--name-only", baseline_sha, "HEAD"],
            capture_output=True, text=True, timeout=10,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return ""
    if diff.returncode != 0:
        return ""

    changed = [line.strip() for line in diff.stdout.splitlines() if line.strip()]
    if not changed:
        return ""

    # Locate this casting's key_files for scoped context
    manifest_path = fdir / "castings" / "manifest.json"
    casting_keyfiles: set[str] = set()
    if manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            for c in manifest.get("castings", []):
                if str(c.get("id")) == str(casting_id):
                    for f in (c.get("key_files") or []):
                        if isinstance(f, str) and f.strip():
                            casting_keyfiles.add(f.strip())
                    break
        except json.JSONDecodeError:
            pass

    relevant = [f for f in changed if f in casting_keyfiles] if casting_keyfiles else changed
    other = [f for f in changed if f not in casting_keyfiles] if casting_keyfiles else []

    lines = [
        "## Prior-cycle file changes (READ BEFORE ACTING ON DEFECTS)",
        "",
        "Earlier CAST or GRIND cycles modified the files listed below. Before assuming "
        "anything about current code state, **read these files first**. Memory is a hint, "
        "not ground truth — verify against the actual files. Skip redundant exploration: "
        "if a defect mentions a symbol in one of these files, read the current version "
        "before re-implementing.",
        "",
    ]
    if casting_keyfiles and relevant:
        lines.append("### Your casting's key_files that changed:")
        for f in relevant:
            lines.append(f"- `{f}`")
        lines.append("")
    if other:
        label = "### Other files changed (may be upstream dependencies):" if casting_keyfiles else "### Files changed since CAST:"
        lines.append(label)
        for f in other[:40]:  # cap at 40 to avoid prompt bloat
            lines.append(f"- `{f}`")
        if len(other) > 40:
            lines.append(f"- ... ({len(other) - 40} more)")
        lines.append("")

    return "\n".join(lines)


def foundry_cast_wave(
    wave: int,
    phase: str = "cast",
    project_root: str = ".",
) -> dict:
    """Read and return prompts for every casting in the specified wave.

    Optimization: replaces N sequential `Foundry-Spawn-Teammate` calls
    (each ~1s of lead deliberation + 1 MCP roundtrip) with a single bulk
    fetch. The lead then spawns all N Agent calls in a single parallel
    tool-use message. Preserves the verbatim-prompt contract and audit
    trail (each casting still logged to spawns.log).

    Args:
        wave: 1-indexed wave number from manifest.waves.
        phase: "cast" or "grind".
        project_root: Repo root.

    Returns on success:
        {
            "ok": True,
            "wave": N,
            "phase": "cast",
            "team_name_suggestion": "cast-{run}-wave-N  (or grind-{run}-cycle-N for phase='grind')",
            "castings": [
                {"casting_id": 1, "prompt": "...", "prompt_hash": "sha256:..."},
                ...
            ],
            "instructions": "Spawn every casting as a SEPARATE Agent tool call in ONE message..."
        }
    """
    fdir = get_run_dir(project_root)
    if not fdir:
        return {"ok": False, "error": "No active foundry run", "hint": "Call Foundry-Init first"}
    if not fdir.exists():
        return {"ok": False, "error": "Foundry run directory not found", "hint": f"Expected {fdir}"}

    manifest_path = fdir / "castings" / "manifest.json"
    if not manifest_path.exists():
        return {"ok": False, "error": "No manifest.json", "hint": "Run F0.5 DECOMPOSE first"}

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        return {"ok": False, "error": f"manifest.json parse error: {e}"}

    waves = manifest.get("waves") or []
    wave_entry = None
    for w in waves:
        if w.get("wave") == wave:
            wave_entry = w
            break
    if not wave_entry:
        return {
            "ok": False,
            "error": f"wave {wave} not found in manifest",
            "hint": f"Available waves: {[w.get('wave') for w in waves]}",
        }

    casting_ids = wave_entry.get("casting_ids") or []
    if not casting_ids:
        return {
            "ok": False,
            "error": f"wave {wave} has no casting_ids in manifest",
            "hint": "Re-run F0.5 DECOMPOSE to rebuild wave groupings.",
        }

    spawn_log = fdir / "spawns.log"
    results = []
    model = _teammate_model()

    for cid in casting_ids:
        prompt_path = fdir / "castings" / f"casting-{cid}-prompt.md"
        if not prompt_path.exists():
            return {
                "ok": False,
                "error": f"casting-{cid}-prompt.md does not exist (wave {wave})",
                "hint": "Re-run F0.5 DECOMPOSE — every casting must have a pre-authored prompt.",
            }
        prompt_text = prompt_path.read_text(encoding="utf-8")
        if not prompt_text.strip():
            return {
                "ok": False,
                "error": f"casting-{cid}-prompt.md is empty (wave {wave})",
                "hint": "Re-run F0.5 DECOMPOSE to regenerate the prompt file.",
            }
        prompt_hash = "sha256:" + hashlib.sha256(prompt_text.encode("utf-8")).hexdigest()[:16]
        entry_out: dict = {
            "casting_id": cid,
            "prompt": prompt_text,
            "prompt_hash": prompt_hash,
            "prompt_path": str(prompt_path.relative_to(Path(project_root)) if prompt_path.is_absolute() else prompt_path),
            # FR-015. Per casting, because the ledger id is per casting.
            "progress_protocol": _progress_protocol_block(
                fdir.name, _agent_id_for_casting(cid)
            ),
        }

        # FR-020 / AC-025 — the bulk-GRIND grind_cycle_context hole. The
        # single-spawn path has built this since it was added; this path never
        # did, so a bulk GRIND wave (the normal way GRIND is dispatched) sent
        # every teammate in blind while the instructions below told the lead to
        # append a block that was never produced. Per casting rather than once
        # for the wave: _build_grind_cycle_context filters the diff to THIS
        # casting's key_files, so one shared block would mislabel every
        # teammate's files as somebody else's. Reuses the existing builder
        # rather than reimplementing the diff.
        if phase == "grind":
            context = _build_grind_cycle_context(fdir, cid, project_root)
            if context:
                entry_out["grind_cycle_context"] = context

        results.append(entry_out)

        try:
            entry = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "casting_id": cid,
                "phase": phase,
                "wave": wave,
                "prompt_hash": prompt_hash,
                "bulk": True,
            }
            if model:
                entry["model"] = model
            with spawn_log.open("a", encoding="utf-8") as f:
                f.write(json.dumps(entry) + "\n")
        except Exception:
            pass

    run_name = fdir.name
    # Phase-first naming: {phase}-{run}-{suffix}. CAST uses `wave-N`
    # from the dependency graph; GRIND uses `cycle-N` from the defect cycle
    # counter. The same bulk tool services both phases; `wave` is the arg
    # name in both but semantically distinct across phases.
    phase_prefix = "cast" if phase == "cast" else "grind"
    suffix_word = "wave" if phase == "cast" else "cycle"
    team_suggestion = f"{phase_prefix}-{run_name}-{suffix_word}-{wave}"

    # The model clause states what to do rather than asserting a static
    # frontmatter pin: this server owns the decision, and the lead's job is to
    # pass exactly what it is told (GI-003). Both branches are explicit so the
    # "pass nothing" case is an instruction, not an omission the lead has to
    # infer (FR-003, OT-001).
    if model:
        model_clause = (
            f"Also pass model='{model}' \u2014 the foundry `model` option is configured "
            "and foundry:teammate follows it (effort stays at its frontmatter xhigh). "
        )
    else:
        model_clause = (
            "Pass NO model parameter \u2014 the foundry `model` option is not configured, "
            "so foundry:teammate's frontmatter pin governs (model=opus + effort=xhigh). "
        )

    result: dict = {
        "ok": True,
        "wave": wave,
        "phase": phase,
        "team_name_suggestion": team_suggestion,
        "castings": results,
        "instructions": (
            f"Spawn {len(results)} Agent tool calls in a SINGLE MESSAGE (parallel tool use). "
            "Each Agent call gets its corresponding casting's prompt VERBATIM \u2014 no modification. "
            "Required per-Agent params: subagent_type='foundry:teammate', "
            f"mode='bypassPermissions'. {model_clause}"
            "NEVER run_in_background=true (foreground, TeamCreate-managed). "
            "Before spawning: TeamCreate(team_name_suggestion) + Foundry-Team-Up(team_name_suggestion). "
            "GRIND phase only: append that casting's own `grind_cycle_context` block (present on "
            "the casting entry whenever it is non-empty) then a "
            "'## Defects to fix this cycle:' block BELOW each prompt, never inside it. "
            "EVERY phase: append that casting's `progress_protocol` block BELOW its prompt, LAST "
            "— after any grind_cycle_context and defect blocks. It tells the teammate where to "
            "write its progress lines; without it Foundry-Liveness has nothing to read back and a "
            "dead teammate is indistinguishable from a thinking one."
        ),
    }
    if model:
        result["model"] = model
    return result
