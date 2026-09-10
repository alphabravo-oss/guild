"""Team lifecycle, the model policy, and the SIGHT requirement.

Survey blocks A, Y and Z. The teardown door reads the fix ledger before it
reads tmux, because after teardown the teammate who knows cannot be asked.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

from foundry_mcp.schemas.vocab import NO_UI_MEANING, _normalise_path
from foundry_mcp.tools.orchestration.keyfiles import DIRECTORY_ENTRY_SUFFIX
from foundry_mcp.tools.artifacts import (
    CAST_BASELINE_SHA_MARKER,
    INSPECT_BOUNDARY_SHA_MARKER,
    TRACE_CLEAN_AT_MARKER,
    # fallout GI-033 / AC-061 (D-080, concern C-060) — ALIASED, and the alias
    # is load-bearing rather than a leftover: D-134's scan recognises a manifest
    # reader as GUARDED by the NAME it calls, and
    # `tests/test_spawn_progress.py` pins that set to `_manifest_shape_problem`
    # and `_manifest_shape_error`. Calling the leaf's public spelling directly
    # would report every reader here as UNGUARDED, which is worse than an
    # import error because it looks like a finding.
    manifest_shape_problem as _manifest_shape_problem,
    _artifact_guard,
    _document_transaction,
    _load_json,
)
from foundry_mcp.tools.foundry_state import (
    active_teams,
    boundary_base_sha,
    current_cycle,
    get_run_dir,
    git_changed_paths,
    git_touching_commit,
    live_teammate_panes,
    sight_required,
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




def _teammate_shutdown_hint(live_panes: list[str]) -> str:
    """The shutdown sentence, for live panes with no registered team left.

    fallout GI-033 / AC-061 (D-021 / D-035, concern C-027) — PROTOCOL PROSE
    STAYS WITH THE DOORS IT NAMES. `foundry_state.active_teams` composes the
    two halves of "is a team still holding the tree" and takes this shaper as
    `hint_for`, on the same rule every other injected sentence in the leaf
    follows: the leaf may hold the READ and may not hold a sentence naming
    SendMessage, TeamDelete and `tmux kill-pane`, because those are this
    module's doors.
    """
    return (
        f"{len(live_panes)} teammate pane(s) still running: {', '.join(live_panes)}. "
        "Send 'All work complete, stop working.' to each teammate in a parallel SendMessage batch, "
        "then TeamDelete immediately — do NOT wait for acks. "
        "If panes won't terminate, run: tmux kill-pane -t <pane_id>"
    )




def _check_active_teams(project_root: str) -> dict:
    """Is any team still holding the tree? The LIFECYCLE layer's composition.

    fallout GI-033 / AC-061 / FR-063 (D-021 / D-035, concern C-027) — THE
    ANSWER IS THE LEAF'S; THIS IS THE COMPOSITION.
    ----------------------------------------------------------------------
    Both halves — `state.json.active_teams` crossed against the directories
    still on disk, and the tmux pane scan — now live in
    `foundry_state.active_teams`, because the gates and transitions that ask
    the question are VERIFIER modules and this one is lifecycle, so wherever
    the answer sat inside `orchestration/` exactly one of the two layers could
    reach it. The pane scan went with it: it is a READ that lists panes and
    kills nothing, and a readers-only leaf that reads the machine rather than
    the run directory is still a leaf (lead ruling
    `lead_ruling_gi_033_leaf_moves`). `_kill_panes` stayed here with the doors
    that kill.

    WHAT THIS ADDS is the two things the leaf may not know: where this machine
    keeps its team directories, and the shutdown sentence. `gates.py` composes
    the same leaf call for the verifier side and passes no sentence, falling
    back to its own `_TEAMS_DOWN_HINT` — one answer, two callers, and neither
    of them a second judgement about what "active" means.
    """
    fdir = get_run_dir(project_root)
    if not fdir:
        return {"active": False, "teams": [], "live_panes": []}
    return active_teams(
        fdir,
        teams_dir=Path.home() / ".claude" / "teams",
        hint_for=_teammate_shutdown_hint,
    )








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




def _concerns_naming(
    fdir: Path, cycle: int, defect_ids: list[str]
) -> dict[str, str]:
    """`{defect id: concern id}` for ids an OPEN concern of this cycle names.

    fallout AC-039 / AC-041 / GI-017 (D-074, supersedes D-050) — CONTEXT ON THE
    REFUSAL, NOT AN EXIT THROUGH IT.

    THIS FUNCTION EXCUSES NOTHING, and the rename is the fix. It was
    `_concerns_excusing`, and `_unrecorded_fix_problem` DROPPED every id it
    returned — so an open concern of this cycle whose free text mentioned a
    dispatched id unregistered the team in exactly the state five rows require a
    refusal. AC-039 states the rule ("refuses naming the id and the commit") and
    AC-041 states the only exit the spec sanctions ("a dispatched id that is
    open with NO commit touching its file"). No row anywhere sanctions a
    concern-based exit; D-050's fix made the hint's third sentence true by
    WIDENING THE DOOR instead of correcting the sentence, which is GI-008's
    violation column — "treating a non-negotiable as a concern to trade
    against" — executed in shipped code.

    THE MATCH IS UNSTRUCTURED PROSE, which is the second half of why it could
    never have been an exit. A concern reading "D-900 is NOT fixed" excused the
    id exactly as one claiming it was, and the concerns are authored by the same
    teammates the door constrains.

    What survives is REPORTING. An operator reading a refusal is better served
    knowing a teammate wrote something about this id than not, so the ids are
    still resolved and published beside the refusal under `concerns_naming` —
    as context, in a field named for what it is. Nothing is dropped from the
    finding.

    THE CYCLE COMPARISON IS `>=`, AND THAT IS THE ARITHMETIC, NOT LOOSENESS.
    A `grind_dispatched` record carries the SERVER counter; `Foundry-Concern`
    stores the cycle its CALLER declared. The counter advances at
    `inspect_start`, so during GRIND N the counter reads N-1 and the lead
    declares N. An equality test would therefore exclude every concern a lead
    ever files about the GRIND it is standing in. Anything from an EARLIER cycle
    is still excluded, which is what the scoping is for.

    NOT `open_concerns_for_other_castings`, deliberately. That reader drops a
    concern whose target casting IS its source, which is right for the INSPECT
    rung it serves (GI-023 is about a fix reaching a SIBLING) and wrong here: a
    casting saying "my own fix is deliberately partial" is self-targeting by
    nature.

    READ THROUGH THE CONCERN MODULE'S OWN READER, never a walk of the document
    here: `read_concerns` is where "what is a concern record" is decided, and a
    second walk is the shape this run has already paid for twice. LAZY, in the
    seam style this module's other cross-module reaches use.

    THE MATCH IS BOUNDED. `D-021` must not be found inside `D-0210`, and
    `CD-021` is not a mention of `D-021`, so the id is matched with neither an
    identifier character before it nor after it.
    """
    import re

    from foundry_mcp.tools.concerns import CONCERN_STATUS_OPEN, read_concerns

    records, problem = read_concerns(fdir)
    if problem is not None:
        # A ledger that will not read reports nothing, which is the direction
        # every advisory reader in this package takes: the door still refuses
        # on exactly what it refused on, and the operator is not told an id was
        # written about on evidence nobody has.
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

    base, base_source = boundary_base_sha(
        fdir,
        boundary_marker=INSPECT_BOUNDARY_SHA_MARKER,
        trace_marker=TRACE_CLEAN_AT_MARKER,
        cast_marker=CAST_BASELINE_SHA_MARKER,
    )
    if not base:
        return None
    diff = git_changed_paths(project_root, base)
    if not diff["ok"]:
        return None
    touched = set(diff["files"])

    # fallout AC-039 / CT-010 / ST-011 / OT-036 (D-265) — THE RECORDED `file`
    # IS FOLDED TO A BARE REPO-RELATIVE PATH BEFORE THE JOIN.
    #
    # Both filing doors store `file` verbatim, and four stream contracts tell
    # their agents "a `#Symbol` beside it is fine". Compared raw against git's
    # repo-relative names, `a.txt#Sym`, `a.txt:12` and `./a.txt` each matched
    # nothing, and the door passed a committed fix whose row was still open.
    # The fold is the one the fix gate already applies to a reference — its
    # `#Symbol` / `::test` head and its line hint — then vocab's prefix-aware
    # path normaliser, so no third spelling of either rule is written here.
    # LAZY, in this function's seam style: `fix_gate` is a lifecycle sibling.
    from foundry_mcp.tools.orchestration.fix_gate import (
        _ref_file_component,
        _strip_line_hint,
    )

    for row in still_open:
        row["_path"] = _normalise_path(
            _strip_line_hint(_ref_file_component(str(row["file"])))
        )
    unrecorded = [r for r in still_open if r["_path"] in touched]

    if not unrecorded:
        return None

    # fallout AC-039 / AC-041 / GI-017 (D-074, supersedes D-050) — READ AFTER
    # THE FINDING IS FIXED, BECAUSE IT NO LONGER CHANGES IT.
    #
    # D-050 read this ABOVE the return and subtracted its keys from
    # `unrecorded`, making an open concern of this cycle a THIRD exit past the
    # refusal. The spec sanctions exactly one exit — AC-041's "no commit
    # touching its file" — and the match was over a concern's free text, so
    # "D-900 is NOT fixed" cleared the id exactly as a claim that it was. The
    # ids are still resolved and published, as CONTEXT beside a refusal that
    # stands, and the hint below no longer offers the exit that produced them.
    naming = _concerns_naming(fdir, cycle, [str(r["defect_id"]) for r in unrecorded])

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
        row["_commit"] = git_touching_commit(project_root, base, row["_path"])
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
                # fallout AC-039 / D-074: an id an open concern of this cycle
                # WRITES ABOUT is named beside its concern — as context, and
                # explicitly not as a pass. It is still in the count above.
                "; written about in an open concern of this cycle (which is "
                "context, not an exit): "
                + ", ".join(f"{d} ({c})" for d, c in sorted(naming.items()))
                if naming else ""
            )
        ),
        # fallout AC-039 / AC-041 (D-074) — EVERY EXIT THIS HINT NAMES IS ONE
        # THE CHECK ABOVE ACTUALLY READS.
        #
        # It used to end "or leave it open and say so in the cycle's concerns",
        # and D-050 answered that by teaching the check to honour it — widening
        # the door until the sentence was true. AC-039 admits no such exit and
        # AC-041 names the only one there is, so the sentence goes and the door
        # stays the shape five rows describe. The two remaining clauses are both
        # reachable from exactly this state: `Foundry-Fix` closes the id, and an
        # id whose commit belongs to a different defect is closed against that
        # one.
        "hint": (
            "The fix is on the branch and the ledger row is not. Close each id "
            "with Foundry-Fix before the team goes down -- after teardown the "
            "teammate who made the change cannot be asked, and the next INSPECT "
            "re-verifies work that is already done and files it again. If the "
            "commit is unrelated to the defect, close the id against the fix "
            "commit it really belongs to. A concern saying an id is "
            "deliberately open does NOT clear this refusal: the concern is "
            "prose the same teammates author, and AC-039 admits one exit only "
            "-- no commit since the baseline touching the id's file."
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
        # fallout D-074 — RENAMED FOR WHAT IT IS. `excused` said these ids had
        # passed the check; they are in `defects` above and in the count.
        "concerns_naming": naming,
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
    scan = live_teammate_panes()
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
        rescan = live_teammate_panes()
        remaining_zombie = len(rescan.get("zombie", []))
        remaining_live = len(rescan.get("live", []))
        if remaining_zombie > 0 or remaining_live > 0:
            # Retry once
            if rescan.get("zombie"):
                killed += _kill_panes(rescan["zombie"])
            time.sleep(1)
            rescan = live_teammate_panes()
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
    """Whether the SIGHT browser audit is part of this run.

    fallout AC-052 / FR-055, and GI-033 / AC-061 (D-021 / D-035) — ONE RULE,
    READ FROM THE LEAF.
    -------------------------------------------------------------------
    The whole decision — the `--no-ui` declaration read BEFORE the extension
    scan's own answer, the manifest shape guard, the blocked-without-a-url arm —
    is `foundry_state.sight_required`. It went to the leaf because
    `orchestration/width.py` and `orchestration/transitions.py` are VERIFIER
    modules that ask the same question and may not import this lifecycle one;
    keeping a second implementation here would have left the run with two
    answers about whether it has a browsable UI, which is the divergence
    AC-052 exists to end.

    The two closed-set values are supplied because a leaf may not know them,
    and both are LAZY for the reason the seam pattern gives: `foundry_spawn`
    and `tools/foundry.py` both import this package, so a module-top import
    closes an import cycle that takes every tool in the server down at load.
    """
    fdir = get_run_dir(project_root)
    if not fdir:
        return {"required": False}


    # fallout FR-020 / AC-025 (casting 10's concern C-081) — the run root, so a
    # directory `key_files` entry is WALKED rather than read as carrying no
    # frontend file. This shim already takes `project_root` and derived `fdir`
    # from it two lines up, so nothing new is resolved. `directory_suffix` is
    # injected because `foundry_state.py` holds zero package imports — the
    # contract that keeps `scripts/measure-run.py`'s package-free read working —
    # so the caller binds the one spelling of it.
    return sight_required(
        fdir,
        shape_problem=_manifest_shape_problem,
        no_ui_meaning=NO_UI_MEANING,
        project_root=Path(project_root),
        directory_suffix=DIRECTORY_ENTRY_SUFFIX,
    )
