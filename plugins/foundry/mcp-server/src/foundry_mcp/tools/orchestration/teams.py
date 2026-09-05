"""Team lifecycle, the model policy, and the SIGHT requirement.

Survey blocks A, Y and Z. The teardown door reads the fix ledger before it
reads tmux, because after teardown the teammate who knows cannot be asked.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

from foundry_mcp.tools.artifacts import (
    _artifact_guard,
    _document_transaction,
    _load_json,
)
from foundry_mcp.tools.foundry_state import (
    current_cycle,
    get_run_dir,
    registered_team_dirs,
)
from pathlib import Path





# --------------------------------------------------------------------------- #
# Model selection policy — the MCP server owns it (GI-003 / FR-009 / A-012).
#
# Delivery: foundry's plugin manifest declares a ``model`` userConfig option and
# this MCP server's declaration substitutes ``${user_config.model}`` into its
# ``env`` as FOUNDRY_MODEL. That is the ONLY path that works — ``${user_config
# .KEY}`` never interpolates into agent frontmatter (an agent pinned
# ``model: ${user_config.model}`` dies at spawn with "There's an issue with the
# selected model"), so every agent keeps a literal frontmatter pin as its floor
# and any override is applied at spawn time on top of it.
#
# The one dangerous detail: an UNSET option substitutes as the EMPTY STRING,
# not as an absent variable. Absence and "" must therefore resolve identically,
# and ``"model": ""`` must never reach an agent config — that is a malformed
# spawn, not a no-op (FR-003 "Absence = no override", FR-004 "Emit no model key
# at all", CT-002, CT-003, OT-001, OT-004).
# --------------------------------------------------------------------------- #

MODEL_ENV_VAR = "FOUNDRY_MODEL"



# CT-001 / FR-002 "Aliases + inherit". ``inherit`` is a real, forwardable value
# — the sentinel that makes an agent follow the session model. It is NOT a
# synonym for unset, and must not be collapsed into the empty-string path.
ACCEPTED_MODELS = ("opus", "sonnet", "haiku", "fable", "inherit")



# FR-005 "The good fits only" (AC-001). EXACTLY these foundry agents follow the
# option. Everything else this server configures keeps its own baseline at every
# setting (AC-004): ``foundry:assayer`` and ``foundry:tracer`` hold their
# frontmatter pins, and the ``general-purpose`` decompose / test / temper agents
# hold the explicit opus baseline they have always carried.
#
# ``foundry:flow-mapper`` has no spawn site in this server — forge's plan.md
# spawns it during V3 R0 — but it belongs in the set so the policy states the
# full foundry membership in one place rather than implying it.
STEERABLE_SUBAGENT_TYPES = ("foundry:teammate", "foundry:flow-mapper")




def configured_model() -> str:
    """Return the validated configured model, or ``""`` when unconfigured.

    ``""`` means "the user configured nothing" — both an absent FOUNDRY_MODEL
    and the empty string the harness substitutes for an unset option.

    Raises:
        ValueError: the value is outside ACCEPTED_MODELS. The message names the
            accepted set (CT-001, OT-003, FR-020). Refusal is loud rather than
            silently degrading, so a typo surfaces at the first tool call
            instead of as a confusing mid-run API error.
    """
    raw = os.environ.get(MODEL_ENV_VAR, "")
    value = raw.strip()
    if not value:
        return ""
    if value not in ACCEPTED_MODELS:
        raise ValueError(
            f"{MODEL_ENV_VAR}={raw!r} is not an accepted model. "
            f"Accepted values: {', '.join(ACCEPTED_MODELS)}."
        )
    return value




def agent_model(subagent_type: str, baseline: str = "") -> dict:
    """Return the ``{"model": ...}`` fragment to splat into one agent config.

    Args:
        subagent_type: the agent this config spawns. Only members of
            STEERABLE_SUBAGENT_TYPES follow the configured value.
        baseline: the model this site emitted before the option existed, or
            ``""`` when the site emitted no model key and the agent's own
            frontmatter pin governs.

    Returns:
        ``{"model": <value>}``, or ``{}`` when nothing resolves. An empty
        fragment emits NO key at all, so an unset option is indistinguishable
        from a build where this feature was never implemented (A-023, AC-003,
        CT-003, OT-004).
    """
    configured = configured_model()
    if configured and subagent_type in STEERABLE_SUBAGENT_TYPES:
        resolved = configured
    else:
        resolved = baseline
    return {"model": resolved} if resolved else {}




# --- Team lifecycle ---


def _scan_tmux_panes() -> dict:
    """Scan all tmux panes and classify them.

    Claude Code spawns teammates as PANES within the lead's tmux session
    (via split-window). Pane titles are set to the agent name (e.g., "@cast-c1").

    IMPORTANT: pane_current_command for a live teammate is the Claude Code
    VERSION NUMBER (e.g., "2.1.80"), NOT "claude" or "node" or "bash".
    A zombie pane shows "bash"/"zsh" because the agent exited and the shell
    is all that's left. But a live teammate's bash shell has the agent as a
    child process, so pane_current_command reflects the agent binary.

    We use pane title + child process check for definitive classification:
    - LEAD: the active pane
    - LIVE: teammate pane whose bash PID has child processes (agent running)
    - ZOMBIE: teammate pane that is dead OR whose bash PID has NO children
    - USER: non-lead pane that doesn't look like a teammate (left alone)

    Teammate detection: Claude Code sets pane titles via `select-pane -T`.
    Teammate panes have titles starting with "@" or matching agent naming
    patterns (cast-, grind-, etc.). User's personal panes are never touched.

    Returns {
        "available": bool,
        "live": [(id, title, cmd)],
        "zombie": [(id, title, cmd)],
        "user": [(id, title, cmd)],   # user's panes — never touched
        "lead": (id, title) | None,
    }
    """
    import subprocess
    import re

    empty: dict = {"available": False, "live": [], "zombie": [], "user": [], "lead": None}
    try:
        check = subprocess.run(["tmux", "list-sessions"], capture_output=True, timeout=5)
        if check.returncode != 0:
            return empty
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return empty

    # Patterns that identify a pane as a Claude Code teammate
    _TEAMMATE_RE = re.compile(
        r"^@|"                                 # Claude Code prefixes teammate titles with @
        r"cast[-_]|grind[-_]|inspect[-_]|"     # foundry phase agents
        r"assay[-_]|temper[-_]|decompose[-_]|" # foundry phase agents
        r"trace[-_]|prove[-_]|sight[-_]|"      # verification stream agents
        r"test[-_]|probe[-_]|"                 # verification stream agents
        r"^teammate-|^agent-",                 # generic teammate patterns
        re.IGNORECASE,
    )

    try:
        result = subprocess.run(
            ["tmux", "list-panes", "-a", "-F",
             "#{session_name}:#{window_index}.#{pane_index}\t"
             "#{pane_title}\t#{pane_dead}\t#{pane_current_command}\t"
             "#{pane_active}\t#{pane_pid}"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode != 0:
            return empty
    except (subprocess.TimeoutExpired, OSError):
        return empty

    live = []
    zombie = []
    user = []
    lead = None

    for line in result.stdout.strip().split("\n"):
        if not line.strip():
            continue
        parts = line.split("\t", 5)
        if len(parts) < 6:
            continue
        pane_id, title, dead, cmd, active, pid = parts

        if active == "1":
            lead = (pane_id, title)
            continue

        # Only touch panes that look like teammates
        if not _TEAMMATE_RE.search(title):
            user.append((pane_id, title, cmd))
            continue

        # Dead panes are always zombies
        if dead == "1":
            zombie.append((pane_id, title, cmd))
            continue

        # Check if the pane's process has children (= agent still running)
        has_children = _pid_has_children(pid)
        if has_children:
            live.append((pane_id, title, cmd))
        else:
            zombie.append((pane_id, title, cmd))

    return {"available": True, "live": live, "zombie": zombie, "user": user, "lead": lead}




def _pid_has_children(pid: str) -> bool:
    """Check if a PID has child processes (i.e., agent is still running)."""
    import subprocess

    if not pid or not pid.strip().isdigit():
        return False
    try:
        # pgrep -P returns 0 if children exist, 1 if none
        result = subprocess.run(
            ["pgrep", "-P", pid],
            capture_output=True, timeout=3,
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return False




def _kill_panes(panes: list[tuple[str, str, str]]) -> int:
    """Kill a list of (pane_id, title, cmd) tuples.

    Kills in REVERSE order to avoid index shifting — tmux reindexes
    panes when siblings are killed, so killing from highest index
    first prevents targeting the wrong pane.

    Returns count killed.
    """
    import subprocess

    # Sort by pane index descending so kills don't shift targets
    sorted_panes = sorted(panes, key=lambda p: p[0], reverse=True)
    killed = 0
    for pane_id, _title, _cmd in sorted_panes:
        try:
            subprocess.run(["tmux", "kill-pane", "-t", pane_id],
                           capture_output=True, timeout=5)
            killed += 1
        except (subprocess.TimeoutExpired, OSError):
            pass
    return killed




def _check_active_teams(project_root: str) -> dict:
    """Check if any registered teams still have directories OR live tmux panes.

    Two-layer check:
    1. Team directory exists in ~/.claude/teams/ (TeamDelete wasn't called)
    2. Live teammate tmux panes exist (teammates haven't exited yet)

    BOTH must be clear for the gate to pass. This prevents the lead from
    progressing to the next phase while teammates are still running.
    """
    fdir = get_run_dir(project_root)
    if not fdir:
        return {"active": False, "teams": [], "live_panes": []}
    # fallout GI-033 / AC-061 (concern C-017) — THE ARTIFACT HALF IS THE LEAF'S.
    #
    # This check has two halves and only one of them reads a run artifact:
    # `state.json.active_teams` crossed against the team directories on disk.
    # That half is `foundry_state.registered_team_dirs` now, which is what lets
    # `gates.py`, `transitions.py` and `width.py` — all three VERIFIER modules —
    # ask the question without importing this lifecycle module. The tmux pane
    # scan and the shutdown hint below stay here: they read no artifact, and a
    # leaf that shelled out to tmux would have stopped being one.
    #
    # `Path.home()` is supplied rather than known there for the same reason
    # every other closed-set value is: the leaf's contract is json and pathlib
    # over the run directory, and where a machine keeps its team dirs is not a
    # fact about the run.
    active = registered_team_dirs(fdir, teams_dir=Path.home() / ".claude" / "teams")

    # Also check for live teammate tmux panes — even if TeamDelete was called,
    # the claude processes might still be running
    live_panes = []
    scan = _scan_tmux_panes()
    if scan["available"] and scan["live"]:
        live_panes = [title for _, title, _ in scan["live"]]

    is_active = len(active) > 0 or len(live_panes) > 0

    result: dict = {"active": is_active, "teams": active, "live_panes": live_panes}
    if live_panes and not active:
        result["hint"] = (
            f"{len(live_panes)} teammate pane(s) still running: {', '.join(live_panes)}. "
            "Send 'All work complete, stop working.' to each teammate in a parallel SendMessage batch, "
            "then TeamDelete immediately \u2014 do NOT wait for acks. "
            "If panes won't terminate, run: tmux kill-pane -t <pane_id>"
        )
    return result




def foundry_register_team(
    team_name: str,
    project_root: str = ".",
) -> dict:
    """Register a team for lifecycle tracking."""
    fdir = get_run_dir(project_root)
    if not fdir:
        return {"error": "No active foundry run. Call Foundry-Init first."}
    if (corrupt := _artifact_guard(fdir)):
        return corrupt
    state_path = fdir / "state.json"
    # D-103: the roster check and the roster write are one critical section \u2014
    # two concurrent registrations both passed the "no active teams" check
    # against the same snapshot and the second write dropped the first.
    refusal: dict | None = None
    total_teams = 0
    with _document_transaction(state_path) as state:
        teams = state.get("active_teams", [])
        if not isinstance(teams, list):
            teams = []

        teams_dir = Path.home() / ".claude" / "teams"
        still_active = [t for t in teams if t != team_name and (teams_dir / t).is_dir()]
        if still_active:
            refusal = {
                "error": f"Cannot register '{team_name}' \u2014 active teams exist: {', '.join(still_active)}",
                "hint": "Shut down existing teammates (SendMessage + TeamDelete) and Foundry-Team-Down before creating a new team. One team at a time.",
                "active_teams": still_active,
            }
        else:
            if team_name not in teams:
                teams.append(team_name)
            state["active_teams"] = teams
            total_teams = len(teams)

    if refusal:
        return refusal
    return {"ok": True, "registered": team_name, "total_teams": total_teams}




def _concerns_excusing(
    fdir: Path, cycle: int, defect_ids: list[str]
) -> dict[str, str]:
    """`{defect id: concern id}` for ids an OPEN concern of this cycle names.

    fallout AC-041 (D-050) — the read that makes `_unrecorded_fix_problem`'s
    third exit exist.

    READ THROUGH THE CONCERN MODULE'S OWN READER, never a walk of the document
    here: `read_concerns` is where "what is a concern record" is decided, and a
    second walk is the shape this run has already paid for twice. LAZY, in the
    seam style this module's other cross-module reaches use.

    NOT `open_cross_casting_concerns`, deliberately. That reader drops a concern
    whose target casting IS its source, which is right for the INSPECT rung it
    serves (GI-023 is about a fix reaching a SIBLING) and wrong here: a casting
    saying "my own fix is deliberately partial" is self-targeting by nature, and
    C-022 — the concern this defect was driven on — is exactly that shape.

    THE CYCLE COMPARISON IS `>=`, AND THAT IS THE ARITHMETIC, NOT LOOSENESS.
    A `grind_dispatched` record carries the SERVER counter; `Foundry-Concern`
    stores the cycle its CALLER declared. The counter advances at
    `inspect_start`, so during GRIND N the counter reads N-1 and the lead
    declares N — C-022 carries cycle 1 while this run's counter reads 0. An
    equality test would therefore exclude every concern a lead ever files about
    the GRIND it is standing in, which is this defect one layer over: a check
    that silently never fires. Anything from an EARLIER cycle is still excluded,
    which is what the scoping is for.

    THE MATCH IS BOUNDED. `D-021` must not be found inside `D-0210`, and
    `CD-021` is not a mention of `D-021`, so the id is matched with neither an
    identifier character before it nor after it.
    """
    import re

    from foundry_mcp.tools.concerns import CONCERN_STATUS_OPEN, read_concerns

    records, problem = read_concerns(fdir)
    if problem is not None:
        # A ledger that will not read excuses nothing, which is the direction
        # every advisory reader in this package takes: the door still refuses,
        # and the operator is not told an id passed on evidence nobody has.
        return {}
    out: dict[str, str] = {}
    for record in records:
        if record.get("status") != CONCERN_STATUS_OPEN:
            continue
        record_cycle = record.get("cycle")
        if not isinstance(record_cycle, int) or record_cycle < cycle:
            continue
        text = str(record.get("text") or "")
        concern_id = str(record.get("id") or "")
        if not concern_id:
            continue
        for defect_id in defect_ids:
            if defect_id in out:
                continue
            pattern = rf"(?<![A-Za-z0-9_]){re.escape(defect_id)}(?![A-Za-z0-9_])"
            if re.search(pattern, text):
                out[defect_id] = concern_id
    return out


def _unrecorded_fix_problem(fdir: Path, project_root: str) -> dict | None:
    """The Team-Down refusal a stale fix ledger owes, or None.

    fallout FR-022 / FR-048 / GI-017 / CT-010 / ST-011 / AC-039 / AC-041.

    For every defect `Foundry-Tasks` dispatched into THIS cycle that is still
    open, ask whether a commit since the cycle's baseline touched its file. If
    one did, the fix is on the branch and the ledger row is not -- which is the
    one state where tearing the team down loses the only person who could close
    it.

    THREE WAYS THIS ANSWERS NOTHING, and all three PASS rather than refuse,
    because an advisory join that blocks on its own blindness is worse than one
    that does not fire:

      * no dispatch records -- a CAST team, or a GRIND that never ran
        Foundry-Tasks. There is nothing dispatched to be stale about.
      * no baseline SHA -- a run whose first INSPECT has not happened. "Since
        when" has no answer, and a diff measured from nothing is not a diff.
      * the diff cannot be computed -- no git, a detached tree, a timeout.
        `git_changed_paths` reports that distinctly from an empty diff, and this
        keeps the two apart for the reason every caller of it must.
    """
    # fallout FR-004 / GI-033 -- LAZY SEAM, written once per symbol.
    # `directives, width` import(s) this module, so a module-top import here would
    # close a cycle that takes every tool in this server down at once.
    # Unguarded, so a wiring break fails loudly at the one call site that
    # needs the symbol rather than hiding behind a silent fallback.
    from foundry_mcp.tools.orchestration.directives import DISPATCHED_DEFECT_UNRECORDED, _grind_dispatches
    from foundry_mcp.tools.orchestration.width import (
        _boundary_base_sha,
        git_changed_paths,
        git_touching_commit,
    )
    cycle = current_cycle(fdir)
    dispatched = _grind_dispatches(fdir, cycle)
    if not dispatched:
        return None
    open_ids = {
        d["id"] for d in _load_json(fdir / "defects.json").get("defects", [])
        if isinstance(d, dict) and d.get("status") == "open" and d.get("id")
    }
    still_open = [
        r for r in dispatched if r.get("defect_id") in open_ids and r.get("file")
    ]
    if not still_open:
        return None

    base, base_source = _boundary_base_sha(fdir)
    if not base:
        return None
    diff = git_changed_paths(project_root, base)
    if not diff["ok"]:
        return None
    touched = set(diff["files"])

    unrecorded = [r for r in still_open if str(r["file"]) in touched]

    # fallout AC-041 (D-050) — THE THIRD EXIT THE HINT NAMES, MADE REAL.
    #
    # The hint below offers three ways past this refusal and the third is "or
    # leave it open and say so in the cycle's concerns". This function read the
    # dispatch rows, the defect ledger, the baseline SHA and the diff, and never
    # read `concerns.json` — so the exit did not exist. Driven at this run's own
    # cycle-1 door: D-021 and D-035 were deliberately left open as a partial fix
    # of a ruled structural packet, C-022 was filed naming both ids and the
    # reason, and Foundry-Team-Down returned the identical refusal with the
    # identical two ids. A run with an honestly partial fix could only get its
    # team down by falsifying a `Foundry-Fix` record or by deleting a sentence
    # from the hint, which are the two things this door exists to prevent.
    #
    # An OPEN concern from this cycle that NAMES the id is now what the hint
    # says it is, and the reason names the concern so the operator can see why
    # an id passed rather than wondering whether the check ran.
    excused = _concerns_excusing(fdir, cycle, [str(r["defect_id"]) for r in unrecorded])
    unrecorded = [r for r in unrecorded if str(r["defect_id"]) not in excused]
    if not unrecorded:
        return None

    # fallout AC-039 — THE COMMIT THAT MADE THE CHANGE, RESOLVED PER FILE.
    #
    # "refuses naming the id AND the commit", and this named the BASELINE: the
    # revision the diff is measured FROM, which is the one commit that provably
    # did not make the change. `git_changed_paths` prints paths and no
    # revisions, so the touching commit was never resolved at all — the
    # operator got a SHA that could not be the answer and had to run the log
    # themselves. `git_touching_commit` answers it per file, and answers "" for
    # every way it cannot; an unresolved commit is an absent field here, never a
    # refusal withheld, because the id and the file are still the finding.
    for row in unrecorded:
        row["_commit"] = git_touching_commit(project_root, base, str(row["file"]))
    named = ", ".join(
        f"{r['defect_id']} ({r['file']}"
        + (f" @ {r['_commit']}" if r.get("_commit") else "")
        + ")"
        for r in unrecorded
    )
    commits = sorted({r["_commit"] for r in unrecorded if r.get("_commit")})
    return {
        "error": DISPATCHED_DEFECT_UNRECORDED,
        "reason": (
            f"{len(unrecorded)} defect(s) dispatched this cycle are still OPEN "
            f"while a commit since {base} ({base_source}) touched the file each "
            f"names: {named}"
            + (
                # AC-041 / D-050: an id that PASSED is named beside the concern
                # that excused it, so the operator reads why the count is what
                # it is rather than inferring that the check did not run.
                "; excused by an open concern of this cycle: "
                + ", ".join(f"{d} ({c})" for d, c in sorted(excused.items()))
                if excused else ""
            )
        ),
        "hint": (
            "The fix is on the branch and the ledger row is not. Close each id "
            "with Foundry-Fix before the team goes down -- after teardown the "
            "teammate who made the change cannot be asked, and the next INSPECT "
            "re-verifies work that is already done and files it again. If the "
            "commit is unrelated to the defect, close the id against the fix "
            "commit it really belongs to, or leave it open and say so in the "
            "cycle's concerns."
        ),
        "phase": "dispatched_defect_unrecorded",
        "defects": [
            {
                "id": r["defect_id"],
                "file": r["file"],
                "casting": r.get("casting"),
                "commit": r.get("_commit", ""),
            }
            for r in unrecorded
        ],
        "commits": commits,
        "baseline_sha": base,
        "baseline_source": base_source,
        "excused": excused,
    }




def foundry_unregister_team(
    team_name: str,
    project_root: str = ".",
) -> dict:
    """Unregister a team with verified teardown.

    Three-phase verification:
    1. CHECK: team directory gone (TeamDelete was called)
    2. CHECK: no live claude processes in non-lead panes
    3. CLEAN: kill zombie panes (dead + idle shells)
    4. UNREGISTER: remove from foundry state

    Blocks if steps 1 or 2 fail — forces proper shutdown ordering.
    """
    import time

    fdir = get_run_dir(project_root)
    if not fdir:
        return {"error": "No active foundry run."}
    if (corrupt := _artifact_guard(fdir)):
        return corrupt

    # -- Phase 0: the FIX LEDGER, before the tmux scan ------------------------
    #
    # fallout FR-022 / FR-048 / GI-017 / CT-010 / ST-011 / AC-039 / AC-041 /
    # OT-036 -- A DISPATCHED DEFECT WHOSE FIX IS ON THE BRANCH AND WHOSE LEDGER
    # ROW IS STILL OPEN.
    #
    # The shape this refuses is the one that costs a whole cycle: a teammate
    # made the fix, committed it, and did not close the defect -- so the ledger
    # says open, the tree says fixed, and the next INSPECT re-verifies work that
    # is already done and files it again. It is invisible at every other door,
    # because every other door reads the LEDGER and the ledger is wrong.
    #
    # The two facts are joined HERE because this is the last door before the
    # team is gone: after teardown the teammate who knows cannot be asked.
    # AC-041 is the boundary, and it is the whole reason the commit half exists:
    # a dispatched id that is open with NO commit touching its file did not get
    # fixed, which is a GRIND that ran out of time and not a ledger that went
    # stale. That case passes.
    #
    # Stated FIRST, before the tmux scan, for the ordering reason every door in
    # this module holds: a refusal that costs nothing to compute goes above one
    # that shells out.
    if (unrecorded := _unrecorded_fix_problem(fdir, project_root)) is not None:
        return unrecorded


    # ── Phase 1: Verify TeamDelete was called ────────────────────────
    teams_dir = Path.home() / ".claude" / "teams"
    if (teams_dir / team_name).is_dir():
        return {
            "error": f"Team directory still exists: ~/.claude/teams/{team_name}/",
            "hint": (
                "TeamDelete must be called BEFORE Foundry-Team-Down. "
                "Proper order: SendMessage(shutdown) to each teammate in ONE parallel batch -> "
                "TeamDelete immediately (do NOT wait for shutdown acks \u2014 idle panes ARE the signal) "
                "-> Foundry-Team-Down."
            ),
            "phase": "team_dir_exists",
        }

    # ── Phase 2: Verify no live teammate processes ───────────────────
    scan = _scan_tmux_panes()
    if scan["available"] and scan["live"]:
        live_titles = [title for _, title, _cmd in scan["live"]]
        return {
            "error": f"{len(scan['live'])} teammate pane(s) still running: {', '.join(live_titles)}",
            "hint": (
                "Teammates are still alive \u2014 they have active claude processes. "
                "Send 'All work complete, stop working.' to each teammate (parallel SendMessage), "
                "then TeamDelete immediately (do NOT wait for acks). Re-run Foundry-Team-Down after."
            ),
            "phase": "live_teammates",
            "live_panes": live_titles,
        }

    # ── Phase 3: Kill zombie panes ───────────────────────────────────
    killed = 0
    if scan["available"] and scan["zombie"]:
        killed = _kill_panes(scan["zombie"])
        # Brief wait + re-scan to verify
        time.sleep(1)
        rescan = _scan_tmux_panes()
        remaining_zombie = len(rescan.get("zombie", []))
        remaining_live = len(rescan.get("live", []))
        if remaining_zombie > 0 or remaining_live > 0:
            # Retry once
            if rescan.get("zombie"):
                killed += _kill_panes(rescan["zombie"])
            time.sleep(1)
            rescan = _scan_tmux_panes()
            remaining_zombie = len(rescan.get("zombie", []))
            remaining_live = len(rescan.get("live", []))
            if remaining_zombie > 0 or remaining_live > 0:
                return {
                    "error": (
                        f"Panes still alive after cleanup: "
                        f"{remaining_live} live, {remaining_zombie} zombie. "
                        "Kill manually: tmux kill-server"
                    ),
                    "phase": "cleanup_failed",
                    "killed": killed,
                }

    # ── Phase 4: Unregister from foundry state ───────────────────────
    state_path = fdir / "state.json"
    with _document_transaction(state_path) as state:
        teams = state.get("active_teams", [])
        if not isinstance(teams, list):
            teams = []
        teams = [t for t in teams if t != team_name]
        state["active_teams"] = teams

    return {
        "ok": True,
        "unregistered": team_name,
        "remaining_teams": len(teams),
        "tmux_panes_killed": killed,
        "verified_clean": True,
    }




# --- SIGHT enforcement ---


def _check_sight_required(project_root: str) -> dict:
    """Check if SIGHT audit is required based on frontend files in castings.

    fallout AC-052 / FR-055 — `--no-ui` IS A DECLARATION AND THIS GATE HONOURS
    IT.
    -----------------------------------------------------------------------
    AC-052 requires one documented meaning across README, setup script and
    gate, and `NO_UI_MEANING` is that sentence: "`--no-ui` declares that this
    run has no browsable UI, so the SIGHT browser audit is not part of it."
    Two of the three surfaces said exactly that; this one implemented its
    opposite. With the flag set and any UI extension in scope it answered
    `required: True, blocked: True` with a reason reading "--no-ui set but N
    frontend files in scope", `_check_streams_complete` then appended `sight`
    to the required roster, and `_cast_preconditions` failed at the config
    rung — so declaring a run had no browsable UI was the one way to make the
    browser audit mandatory AND unsatisfiable.

    It survived because no fixture reached the arm: every `no_ui` arrangement
    under `tests/orchestration/` used `.py` key files, where the extension scan
    returns first. `test_sight_is_not_required_when_the_run_declares_no_ui`
    drives it with a `.tsx` key file, which is the shape that was missing.

    The extension scan stays for runs that did NOT declare the flag: absence of
    `--no-ui` is not a claim either way, so the file extensions are the only
    evidence there is.
    """
    fdir = get_run_dir(project_root)
    if not fdir:
        return {"required": False}
    manifest = fdir / "castings" / "manifest.json"

    if not manifest.exists():
        return {"required": False}

    from foundry_mcp.tools.foundry_spawn import _manifest_shape_problem

    data = _load_json(manifest)
    # D-134: the records, not just the container. `castings: "nope"` used to
    # meet `.get()` two lines down and raise AttributeError out of Foundry-Next.
    # The guard at every MCP door names the file; this keeps the reader itself
    # total for the paths that reach it without one.
    if _manifest_shape_problem(data) is not None:
        return {"required": False, "reason": "castings/manifest.json records are unreadable"}
    ui_exts = {".tsx", ".jsx", ".vue", ".svelte", ".css", ".scss", ".html", ".astro"}

    ui_files = []
    for casting in data.get("castings", []):
        for f in casting.get("key_files", []):
            if any(f.endswith(ext) for ext in ui_exts):
                ui_files.append(f)

    # Read BEFORE the extension scan's own answer, because the flag is the
    # operator's statement about the run and the extensions are an inference
    # about it. `ui_files` is still reported so the operator can see the
    # tension between what they declared and what is in scope; it is a fact on
    # the answer, never a reason to overrule the declaration.
    #
    # LAZY, in the shape the cross-module seam pattern shows: `tools/foundry.py`
    # reaches into this package, so a module-top import here would close a cycle
    # the boundary guard refuses. Unguarded, so a wiring break fails loudly at
    # the one call site that needs the sentence.
    if data.get("no_ui", False):
        from foundry_mcp.tools.foundry import NO_UI_MEANING

        return {
            "required": False,
            "blocked": False,
            "no_ui": True,
            "ui_files": len(ui_files),
            "reason": NO_UI_MEANING,
        }

    if not ui_files:
        return {"required": False, "reason": "No frontend files in castings"}

    url = data.get("target_url", "")

    if not url:
        return {"required": True, "blocked": True, "ui_files": len(ui_files),
                "reason": f"No --url provided but {len(ui_files)} frontend files in scope"}

    return {"required": True, "blocked": False, "url": url, "ui_files": len(ui_files)}
