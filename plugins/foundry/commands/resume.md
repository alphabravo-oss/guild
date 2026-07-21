---
description: "Resume an interrupted foundry run"
allowed-tools: ["Bash(ls:*)", "Bash(cat:*)", "Bash(jq:*)", "AskUserQuestion", "Read", "Write", "Glob", "Grep", "Agent", "TaskCreate", "TaskUpdate", "TaskList", "TaskGet", "TeamCreate", "TeamDelete", "SendMessage", "Edit", "Bash(${CLAUDE_PLUGIN_ROOT}/scripts/foundry.sh:*)", "Bash(git:*)", "Bash(go:*)", "Bash(npm:*)", "Bash(npx:*)", "Bash(pnpm:*)", "Bash(make:*)", "Bash(curl:*)"]
hide-from-slash-command-tool: "true"
---

# Foundry Resume Command

Resume an interrupted foundry run.

## STEP 1: FIND EXISTING RUNS

Scan for foundry run directories:

```bash
ls -d foundry-archive/*/ 2>/dev/null || echo "NO_RUNS"
```

## STEP 2: HANDLE RESULTS

### If NO runs exist:

Tell the user:

> No foundry runs found.
>
> To start a new run:
> ```
> /foundry:start "scope" --spec path/to/spec.md
> ```

Then STOP.

### If runs exist:

For each run directory, read `state.json` to extract:
- Run name
- Current phase
- Cycle number
- Spec path
- Created timestamp

Present the list using AskUserQuestion:
- "bold-falcon (phase: INSPECT, cycle: 2, started: 2026-03-20)"
- "swift-anvil (phase: CAST, cycle: 0, started: 2026-03-22)"

## STEP 3: RESUME SELECTED RUN

1. Call `Foundry-Init` with `resume: "<run-name>"` to reload state
2. Call `Foundry-Context` to get full state
3. Call `Foundry-Next` to get the next action
4. Continue the foundry loop from the current phase

Follow the same rules as `/foundry:start` — you are the Lead, never edit code, delegate everything.
