---
description: "Gracefully stop the current foundry run"
allowed-tools: ["Bash(ls:*)", "Bash(cat:*)", "Bash(jq:*)", "Bash(tmux:*)", "Bash(kill:*)", "Read", "Write", "AskUserQuestion", "TaskUpdate", "TaskList", "TeamDelete", "SendMessage"]
hide-from-slash-command-tool: "true"
---

# Foundry Stop Command

Gracefully stop the active foundry run.

## STEP 1: CHECK FOR ACTIVE RUN

Call `Foundry-Context` to find the active run.

If no active run:
> No active foundry run. Nothing to stop.
Then STOP.

## STEP 2: CONFIRM

Use AskUserQuestion:
> "Stop foundry run '{run-name}' at phase {phase}, cycle {cycle}?"
> - "Stop after current task" — Let running teammates finish, then stop
> - "Stop immediately" — Kill all teammates now
> - "Cancel" — Don't stop

## STEP 3: EXECUTE STOP

### Stop after current task:
1. Send "All work complete, stop working." to each teammate in ONE parallel SendMessage batch (no broadcast — structured messages reject `to='*'`)
2. `TeamDelete` + `Foundry-Team-Down` **immediately** — do NOT wait for shutdown_response/ack or idle confirmations. Idle/terminated panes are the signal; `TeamDelete` cleans zombies.
3. Save state (run can be resumed later)

### Stop immediately:
1. `TeamDelete` + `Foundry-Team-Down` (kills tmux panes)
2. Cancel all pending tasks
3. Save state

Tell the user:
> Foundry run '{name}' stopped at phase {phase}, cycle {cycle}.
> Resume with: `/foundry:resume`
