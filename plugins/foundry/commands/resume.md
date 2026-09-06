---
description: "Resume an interrupted foundry run"
argument-hint: "[--max-cycles N]"
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

Install it **before** STEP 6 hands control back to the loop, so the guard is on the hook path before the first CAST or GRIND teammate commits:

```bash
"${CLAUDE_PLUGIN_ROOT}/scripts/install-commit-guard.sh"
```

It places `${CLAUDE_PLUGIN_ROOT}/hooks/pre-commit-guard.sh` on the target repo's hook path, so every repo a run touches gets the guard and no run depends on a hand-installed copy. It is idempotent on exactly the terms STEP 3 states for the migration — it installs, refreshes a stale copy, or reports the current one and exits 0 — so run it on every resume rather than trying to remember which plugin version last wrote the hook. A `pre-commit` hook that is *not* this guard is never destroyed silently: it is preserved to a timestamped backup and the replacement is reported loudly.

Installing it mid-run is safe precisely because the guard judges the index only — `git diff --cached`. A resumed working tree normally still carries the interrupted run's uncommitted work; a working-tree guard (`git diff HEAD`) would fire on that the moment it landed, and on a peer's unstaged work forever after, while a correctly pathspec-scoped commit (see `agents/teammate.md` COMMIT PROTOCOL) passes it.

## STEP 5: THREAD `--max-cycles N` IF IT WAS GIVEN

**Resuming a run, with a new cap:** when `/foundry:resume` was invoked with `--max-cycles N`, thread that flag through by passing `max_cycles=N` on the STEP 6 `Foundry-Init(resume=…)` call. **Take N from the invocation itself.** `setup-foundry.sh` does not parse flags on the `resume` subcommand — it echoes `FOUNDRY_SUBCOMMAND=resume` and exits — and this command never runs it, so there is no `FOUNDRY_MAX_CYCLES=...` line to read here and no echoed value to copy. `Foundry-Init(resume=…, max_cycles=N)` REWRITES `state.json.max_cycles`, in the same locked write as the refreshed provenance: it moves the ceiling on a run that is already moving — lowering it, raising it, or lifting it altogether — which is the whole point of the flag existing on this door.

**This door gives THREE answers, not two, and the omitted flag is one of them.** Omitting `--max-cycles` leaves the run's persisted cap exactly where it was: a bare `/foundry:resume` never moves a ceiling. `--max-cycles 0` REWRITES the cap to unbounded, because `0` is the spelling of unbounded and an operator who types it is lifting a ceiling on purpose. Any positive N rewrites the cap to N. **Never describe an omitted flag as a cap of `0`** — that reading is what made the one value meaning "unbounded" the one value this door would not write, so an operator who typed `--max-cycles 0` to lift a ceiling was told, accurately and uselessly, that the ceiling was still where it had been.

The absence is carried at the parameter rather than guessed at from falsiness: `foundry_init` takes `max_cycles: int | None = None`, `server.py` forwards `args.get("max_cycles")` and its schema declares no default, so an absent key arrives as absence and never as a manufactured `0`. Only a value an operator actually typed reaches the cap check, which is what lets that check's own hint promise an exit the door then honours. A non-integer or a negative value is refused there by name; an omitted flag reaches neither that check nor the write.

**The rewrite does not halt anything by itself, and the door is where the halt happens.** `Foundry-Init` writes the number and returns; the cap is READ once, in the preconditions of the two transitions that open a GRIND cycle — `grind_start` and `assay_fail` — as a non-refusing fact those transitions then act on. So a run sitting in F4 with a cap below its cycle stays in F4, working, until something tries to open a GRIND. `Foundry-Gate(phase='grind')` REPORTS that fact and does not refuse on it, which is the difference between a door telling you where the run is going and a door standing in its way.

**A value BELOW the cycle the run is on halts it at the next GRIND door**, with reason `cap_reached`. That is a SUCCESSFUL `Foundry-Phase` transition and never a refusal: the phase becomes `HALTED`, `halted_at_cycle` and `halted_reason` are written beside it, and the report is generated inside that same transition, naming every open defect. The next `Foundry-Next` reports the run halted and issues no dispatch. **`HALTED` is a named terminal state distinct from `DONE`** — a halted run stopped with open work, and a run that reaches it with every open finding written down and tiered has succeeded rather than failed. Never describe it as a refusal and never describe it as a finished run; `references/lead-discipline.md` carries why a named backlog is a successful end.

## STEP 6: RESUME SELECTED RUN

1. Call `Foundry-Init` with `resume: "<run-name>"` to reload state — plus `max_cycles=N` when STEP 5 applies
2. Call `Foundry-Context` to get full state
3. Call `Foundry-Next` to get the next action — its `heading_for`, `open_by_tier` and `cycles_to_cap` fields say which ending the run you just picked up is coming to
4. Continue the foundry loop from the current phase

**A resume runs the SAME self-target preflight a fresh `Foundry-Init` runs.** On a run whose target is foundry itself, it compares the working tree's plugin manifest against the executing server's own and `git rev-parse HEAD` on both sides, and REFUSES the resume on either mismatch, naming the reason and the launch command. That refusal is the point: the run you are picking up shipped process fixes, and resuming it on a server built before them silently verifies the wrong thing for the rest of the run.

Follow the same rules as `/foundry:start` — you are the Lead, never edit code, delegate everything.
