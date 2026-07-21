---
description: "Apply HIGH-confidence changes from a previous tidy dry-run"
argument-hint: "<run-id> [--tracks=...]"
allowed-tools: ["Bash(mkdir:*)", "Bash(cat:*)", "Bash(ls:*)", "Bash(date:*)", "Bash(test:*)", "Bash(echo:*)", "Bash(printf:*)", "Bash(git:*)", "Bash(jq:*)", "Read", "Write", "Edit", "Glob", "Grep", "Agent", "AskUserQuestion"]
---

# Tidy Apply

Apply the HIGH-confidence changes from a previous `--dry-run` invocation.

```
RUN_ID="$ARGUMENTS"
```

If `$ARGUMENTS` is empty, list recent runs with status `inspection_complete` and ask which to apply.

## Steps

1. Verify `.tidy/runs/$RUN_ID/state.json` exists. If not, fail with helpful message.

2. Read state.json. Check `status` — must be `inspection_complete` or `cancelled_at_review`. If anything else, tell user the run is in an unexpected state (point them at the run dir).

3. Verify the working tree is still clean:

```bash
git status --porcelain
```

If dirty: refuse, instruct user to stash/commit/branch first.

4. Verify the pre_tidy_sha is still reachable (in case they merged or rebased):

```bash
git rev-parse "$PRE_TIDY_SHA" >/dev/null 2>&1
```

If not, the historical baseline is gone — tell the user the run cannot be applied because the baseline was rewritten. Suggest re-running `/tidy:run --apply` fresh.

5. Read each track's `assessment.md`. Present a brief summary to the user:

```
Tidy run <RUN_ID> inspection found:
- HIGH-confidence (will apply): <X> total across N tracks
- MEDIUM-confidence (will skip — needs review): <Y>
- LOW (will skip — flagged): <Z>

Last inspected: <created_at>
Tracks: <list>

Proceed with apply?
```

6. Call `AskUserQuestion`:
   - **Apply** — proceed
   - **Review HIGH list first** — render the HIGH-confidence items from each assessment, then re-ask
   - **Cancel** — stop, no changes made

7. On Apply: invoke the apply phase from `commands/run.md` PHASE 4 onward — same logic, same track-application order, same reviewer pass. The orchestrator code in run.md is the canonical implementation; this command is a re-entry point.

8. Update state.json: `mode: "apply"`, `applied_at: "<ISO>"`.

## Rules

- **Same refuse-on-dirty-tree rule** as `run.md` PHASE 0.2.
- **Same track-application order** (deprecated-slop → dead-code → dedup → type-consolidate → type-strengthen → error-cleanup → circular-deps).
- **Reviewer pass is mandatory.**
- **If the user runs `apply` on a completed run, refuse.** They should run `/tidy:run` again for a fresh pass.
