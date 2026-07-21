---
description: "Show current foundry run status"
allowed-tools: ["Bash(ls:*)", "Bash(cat:*)", "Bash(jq:*)", "Read", "Glob"]
hide-from-slash-command-tool: "true"
---

# Foundry Status Command

Show the current state of foundry runs.

## STEP 1: CHECK FOR ACTIVE RUN

Call `Foundry-Context` to check for an active run in this session.

If active, display:
- Run name, phase, cycle
- Open defects count
- Verification stream status
- Team status

## STEP 2: LIST ALL RUNS

```bash
ls -d foundry-archive/*/ 2>/dev/null || echo "NO_RUNS"
```

For each run, read `state.json` and display a summary table:

| Run | Phase | Cycle | Defects | Started |
|-----|-------|-------|---------|---------|
| bold-falcon | INSPECT | 2 | 3 open | 2026-03-20 |
| swift-anvil | DONE | 0 | 0 | 2026-03-22 |

## STEP 3: DETAILED VIEW (if user asks)

Read and display:
- `forge-log.md` — execution history
- `defects.json` — open/fixed defects
- `verdicts.json` — requirement verdicts
