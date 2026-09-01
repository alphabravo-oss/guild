---
description: "Resume an interrupted foundry run"
allowed-tools: ["Bash(${CLAUDE_PLUGIN_ROOT}/scripts/install-commit-guard.sh:*)", "Bash(${CLAUDE_PLUGIN_ROOT}/scripts/migrate-archive.py:*)", "Bash(ls:*)", "Bash(cat:*)", "Bash(jq:*)", "AskUserQuestion", "Read", "Write", "Glob", "Grep", "Agent", "TaskCreate", "TaskUpdate", "TaskList", "TaskGet", "TeamCreate", "TeamDelete", "SendMessage", "Edit", "Bash(git:*)", "Bash(go:*)", "Bash(npm:*)", "Bash(npx:*)", "Bash(pnpm:*)", "Bash(make:*)", "Bash(curl:*)"]
disable-model-invocation: "true"
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

## STEP 3: MIGRATE THE ARCHIVE

A run created before 4.9.0 predates the observations ledger, the defect `class` field, the per-cycle stream roll-up and the progress ledgers. Resuming into it without migrating means the tools read structures that are not there.

Run the migration **before** `Foundry-Init`, so state is repaired before it is reloaded:

```bash
"${CLAUDE_PLUGIN_ROOT}/scripts/migrate-archive.py" foundry-archive/<run-name>
```

It is idempotent — safe on an already-migrated or half-migrated archive, and it no-ops on a current one, so run it on every resume rather than trying to judge the archive's age by eye. Check `state.json`'s `archive_schema_version` marker if you want the answer first, and pass `--dry-run` to see what would change without writing. Archived history is preserved verbatim: defect `type` and `source` values outside the current vocabulary are migrated as-is, never normalized away.

If it exits non-zero, stop and report — do NOT resume into an archive that failed to migrate.

## STEP 4: INSTALL THE COMMIT GUARD

A run created before 4.9.0 also predates the pre-commit guard asset entirely, and a run created after it still carries whatever copy was current when the hook was installed — the installer writes a **copy**, never a symlink, so a plugin update does not refresh it. Either way, the repository a resumed run is about to commit into may be unguarded or stale.

Install it **before** STEP 5 hands control back to the loop, so the guard is on the hook path before the first CAST or GRIND teammate commits:

```bash
"${CLAUDE_PLUGIN_ROOT}/scripts/install-commit-guard.sh"
```

It places `${CLAUDE_PLUGIN_ROOT}/hooks/pre-commit-guard.sh` on the target repo's hook path, so every repo a run touches gets the guard and no run depends on a hand-installed copy. It is idempotent on exactly the terms STEP 3 states for the migration — it installs, refreshes a stale copy, or reports the current one and exits 0 — so run it on every resume rather than trying to remember which plugin version last wrote the hook. A `pre-commit` hook that is *not* this guard is never destroyed silently: it is preserved to a timestamped backup and the replacement is reported loudly.

Installing it mid-run is safe precisely because the guard judges the index only — `git diff --cached`. A resumed working tree normally still carries the interrupted run's uncommitted work; a working-tree guard (`git diff HEAD`) would fire on that the moment it landed, and on a peer's unstaged work forever after, while a correctly pathspec-scoped commit (see `agents/teammate.md` COMMIT PROTOCOL) passes it.

## STEP 5: RESUME SELECTED RUN

1. Call `Foundry-Init` with `resume: "<run-name>"` to reload state
2. Call `Foundry-Context` to get full state
3. Call `Foundry-Next` to get the next action
4. Continue the foundry loop from the current phase

Follow the same rules as `/foundry:start` — you are the Lead, never edit code, delegate everything.
