---
description: "Stop the active foundry run: seal it HALTED with your reason"
allowed-tools: ["Bash(python3:*)", "Bash(rm:*)", "Bash(ls:*)", "Bash(cat:*)", "Read", "AskUserQuestion", "TaskList", "TaskStop", "TaskUpdate", "SendMessage"]
disable-model-invocation: "true"
---

# Foundry Stop Command

Stop the active foundry run. This is the human's own stop: the run ends **HALTED**, sealed with reason `user_stop` and the human's words, and `REPORT.md` is regenerated and sealed inside that same transition. HALTED is terminal — the remaining work goes to a NEW run.

## STEP 0: THE STOP TOKEN — ALREADY WRITTEN

After `start_cast` the halt door accepts `user_stop` from the lead only on proof the human asked for it. The shell step below writes that proof: a one-time token in the active run's archive, `foundry-archive/<run>/.stop-token.json`. Claude Code runs it when the human invokes `/foundry:stop`, before the model reads this file, and `disable-model-invocation` keeps the model from invoking this command at all. **The lead never writes, copies or edits this token** — a token the lead made is the lead's word, and the halt door exists to refuse that.

**The active run** is the run under `foundry-archive/` whose `state.json` phase is F1..F5.5 and not HALTED or DONE. If several qualify, it is the one whose `state.json` was modified most recently. This is the same rule the Stop hook applies, and it is applied under the same roots in the same order: this session's working directory first, then `CLAUDE_PROJECT_DIR`, with the first root that holds a qualifying run winning. (The hook tries its event `cwd` ahead of both; for a shell step that is the working directory, so the two orders are one order.)

**The fallthrough to `CLAUDE_PROJECT_DIR` is what makes the two agree when the session's directory has drifted below the project** — a lead that ran `cd plugins/foundry/mcp-server` to run the suite, which is routine on a self-targeting run. The server writes the archive under that root, because `plugin.json` launches it with `--project-root ${CLAUDE_PROJECT_DIR}`. Without the fallthrough the step resolves `foundry-archive/` against the working directory alone, finds nothing and writes no token, while the hook finds the run and holds the lead's turn open — and the cost lands on you: the halt door then refuses your own stop for want of proof that nothing was able to write.

The token names its run, carries its creation time, and proves a halt for 60 minutes. The halt door refuses a token that is missing, names another run, is older than that, or has already been consumed. The halt it proves consumes it, and a second halt presenting it is refused. Before CAST (F0.x) no token is written and none is needed.

```!
python3 - <<'FOUNDRY_STOP_TOKEN'
import json, os, secrets
from datetime import datetime, timezone
from pathlib import Path

LIVE_PHASES = ("F1", "F2", "F3", "F4", "F5", "F5.5")  # start_cast to NYQUIST
TOKEN_FILENAME = ".stop-token.json"

# THE ROOTS, IN THE ORDER THE STOP HOOK TRIES THEM: this session's working
# directory, then CLAUDE_PROJECT_DIR — the root the server writes the archive
# under, because plugin.json launches it with `--project-root
# ${CLAUDE_PROJECT_DIR}`. The FIRST root holding a qualifying run wins.
# hooks/foundry_active_run.py#project_roots tries a hook event's cwd ahead of
# both; for a shell step that IS the working directory, so the two orders are
# one order. A root naming a directory an earlier one already named is dropped,
# so one archive is never scanned twice.
try:
    cwd = os.getcwd()
except OSError:
    cwd = ""
roots, seen = [], set()
for raw in (cwd, os.environ.get("CLAUDE_PROJECT_DIR")):
    if raw:
        key = os.path.realpath(raw)
        if key not in seen:
            seen.add(key)
            roots.append(Path(raw))

run_dir = None
for root in roots:
    archive = root / "foundry-archive"
    try:
        state_paths = sorted(archive.glob("*/state.json")) if archive.is_dir() else []
    except OSError:
        continue
    candidates = []
    for state_path in state_paths:
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
            mtime = state_path.stat().st_mtime
        except (OSError, ValueError):
            continue
        if isinstance(state, dict) and state.get("phase") in LIVE_PHASES:
            candidates.append((mtime, state_path.parent))
    if candidates:
        run_dir = max(candidates)[1]
        break

if run_dir is None:
    print("foundry:stop token: no run under foundry-archive/ is in F1..F5.5, so no token was written (a run before start_cast needs none).")
else:
    token = {
        "run": run_dir.name,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "nonce": secrets.token_hex(16),
        "consumed_at": None,
    }
    staged = run_dir / (TOKEN_FILENAME + ".tmp")
    staged.write_text(json.dumps(token, indent=2) + "\n", encoding="utf-8")
    os.replace(staged, run_dir / TOKEN_FILENAME)
    print(f"foundry:stop token: written for run {run_dir.name} at {token['created_at']} ({run_dir / TOKEN_FILENAME}).")
FOUNDRY_STOP_TOKEN
```

**A token this command does not spend is deleted before the command ends.** Every branch below that stops without sealing — no active run, a run already HALTED or DONE, Cancel — runs `rm -f` on the token path the step above printed. Nothing else ever touches the file.

## STEP 1: CHECK THE RUN

Call `Foundry-Context`.

- **No active run:** delete the token, then tell the user:
  > No active foundry run in this session. Nothing to stop. (If a run is still live on disk, `/foundry:resume` it first, then `/foundry:stop` again.)

  Then STOP.
- **Already HALTED:** delete the token, then tell the user:
  > Foundry run '{name}' is already HALTED at cycle {cycle} — {halted_reason}. Nothing to stop: HALTED is terminal. The report is `foundry-archive/{name}/REPORT.md`.

  Then STOP. Never call the halt door on a HALTED run: nothing leaves HALTED, and there is no second halt.
- **DONE (F6):** delete the token, tell the user the run already finished, and STOP.
- **Otherwise:** continue. If the step above wrote its token for a different run than Foundry-Context names, say so. The halt door will refuse that token, because it names another run.

## STEP 2: CONFIRM, AND TAKE THE REASON IN THE HUMAN'S WORDS

Use AskUserQuestion:
> "Stop foundry run '{run-name}' at phase {phase}, cycle {cycle}? It ends HALTED — terminal, with the report regenerated and sealed."
> - "Stop after the current step" — teammates commit what they are doing, then stop
> - "Stop now" — stop every agent immediately
> - "Cancel" — don't stop

**Cancel:** delete the token, tell the user nothing was stopped, and STOP.

Otherwise ask, with AskUserQuestion, why the run is stopping. Whatever the human answers is the halt's `text`, recorded **verbatim** — it is the one line in the sealed archive that says why THIS run ended.

## STEP 3: STOP EVERY IN-FLIGHT AGENT

The halt comes after the agents are stopped, never before:

1. **Named teammates:** send "All work complete, stop working." to each in ONE parallel SendMessage batch — no broadcast, because structured messages reject `to='*'`. For **Stop after the current step**, give them a bounded wait to commit what they are on: poll `TaskList`, or use Monitor, for at most 20 minutes in total. The token expires after 60, so the wait must stay well inside that. Never end the turn to wait for a notification.
2. **Everything still running:** `TaskStop` each task `TaskList` still shows running, including background agents.
3. **Teams:** call `Foundry-Team-Down(team_name=...)` for every team still registered in the run ledger. The halt door refuses while a team is registered. Teammates are named Agent spawns, and the run ledger is the only team record.

## STEP 4: SEAL THE RUN

Call `Foundry-Phase(phase='halt', reason='user_stop', text=<the human's reason, verbatim>)`.

- **Success:** the phase is HALTED, `halted_reason` is `{"reason": "user_stop", "text": <their words>}`, `REPORT.md` is regenerated and sealed inside the same transition, and the result's `halt_proof` names the token it consumed. If `report_generated` is false, repair what `report_error` names and call `Foundry-Report`. Foundry-Report is not a phase transition, so it still runs on a halted run.
- **Refused naming the token** (missing, stale, reused, or another run's): tell the human exactly what the refusal says. Invoking `/foundry:stop` again writes a fresh token. Do not work around it, and never write a token yourself.
- **Refused naming a registered team:** bring that team down with `Foundry-Team-Down`, then call again.
- **Before CAST (F0.x):** the same call. The door takes `user_stop` there without a token.

## STEP 5: TELL THE USER

> Foundry run '{name}' is HALTED at phase {phase}, cycle {cycle} — user_stop: {their reason}.
> The report is `foundry-archive/{name}/REPORT.md`; it names every open defect by tier.
> HALTED is terminal: to carry the remaining work forward, start a NEW run.
