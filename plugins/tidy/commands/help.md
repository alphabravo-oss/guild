---
description: "Explain the tidy plugin"
argument-hint: ""
allowed-tools: []
---

# Tidy Help

Render this help text to the user verbatim.

---

# Tidy — careful low-risk codebase cleanup

Seven focused tracks. Each inspects, ranks proposals by confidence, applies ONLY HIGH-confidence LOW-risk changes, and runs all checks after each batch. Atomic commits per batch — any failing check is one `git revert` away.

## Commands

```
/tidy:run                     # dry-run: inspect all 7 tracks, present findings, change nothing
/tidy:run --apply             # inspect + apply HIGH-confidence + run checks (default checkpoint before applying)
/tidy:run --apply --auto      # same, no apply checkpoint (still HIGH-only)
/tidy:run --tracks=dedup,dead-code     # subset of tracks
/tidy:apply <run-id>          # apply HIGH-confidence from a prior dry-run
/tidy:status [<run-id>]       # show recent runs or a specific run's state
/tidy:help                    # this text
```

## The seven tracks

### 1. `dedup` — Deduplication
Finds repeated logic, copy-pasted functions, redundant abstractions. Consolidates where it genuinely reduces complexity. **Never** merges code that just looks similar but serves different purposes. Refuses to use comments as the source of truth.

### 2. `type-consolidate` — Type consolidation
Finds type definitions scattered across files. Surfaces drift (same name, different shape — usually a bug). Merges only what's truly identical and serves the same purpose.

### 3. `dead-code` — Dead code removal
Runs language-appropriate static analysis (knip, ts-prune, golangci-lint unused, vulture, etc.). Then **manually verifies** each candidate against dynamic imports, config references, framework conventions, code generation, and external consumers. Only removes what is CONFIRMED dead.

### 4. `circular-deps` — Circular dependency untangling
Maps the dependency graph (madge, go vet, pycycle). Prioritizes cycles by impact: maintainability, testability, correctness. Untangles by extracting shared logic to **neutral modules**. Never introduces new abstractions just to break a cycle.

### 5. `type-strengthen` — Type strengthening
Finds `any`, `unknown`, `interface{}`, weak generics. Researches the real type from call sites + runtime usage. Replaces with strong types — but **preserves legitimate boundary types** (JSON parsing, external APIs, FFI, dynamic dispatch). Type check runs after every batch.

### 6. `error-cleanup` — Error-handling cleanup
Finds try/catch and equivalent defense patterns. Removes silent swallows, default-on-error fallbacks, generic re-throws that lose context. **Keeps** real boundaries, real recovery, real logging, real cleanup, and user-facing error reporting.

### 7. `deprecated-slop` — Deprecated code + AI slop
Removes legacy/deprecated code only when CLEARLY obsolete (no callers, no live state, no public consumers). Removes AI artifacts: edit-narration comments, AI voice, restating-code comments, empty TODOs, unimplemented stubs. Rewrites useful comments so a NEW engineer understands WHY the code exists.

## Confidence ranking

Every track classifies each proposed change:

- **HIGH** — clearly safe, no edge cases, checks will pass → auto-apply on `--apply`
- **MEDIUM** — probably safe but has dependencies or trade-offs → surfaced for your decision
- **LOW** — risky, ambiguous, or actively a bad idea → flagged, NOT changed
- **UNCERTAIN** — couldn't determine → flagged for review

Only HIGH auto-applies. Everything else is shown to you.

## Safety mechanisms

- **Refuses to run on a dirty working tree.** Stash or branch first.
- **Atomic commits per batch.** Each track makes its own commits; reverting any one is `git revert <sha>`.
- **Checks after every batch.** Type check, tests, lint, build (auto-detected). Any failure → that batch is reverted, candidates marked UNCERTAIN.
- **Track order in apply phase:** `deprecated-slop → dead-code → dedup → type-consolidate → type-strengthen → error-cleanup → circular-deps`. Designed to minimize cross-track conflict.
- **Mandatory reviewer pass** after apply: full check matrix re-run, cross-track conflict scan, consolidated debrief.

## Flow

```
/tidy:run
   │
   ├─ Preflight (verify git, clean tree, detect tooling)
   │
   ├─ PHASE 2: Inspection (all 7 tracks in PARALLEL, read-only)
   │
   ├─ PHASE 3: Consolidated assessment rendered in chat
   │
   ├─ [Checkpoint — apply HIGH only / review MEDIUM first / cancel]
   │
   ├─ PHASE 4: Apply (sequential per track order, atomic commits + checks)
   │
   ├─ PHASE 5: Reviewer (final check matrix + cross-track conflict scan)
   │
   └─ PHASE 6: Debrief in chat
```

## What tidy WON'T do

- Auto-apply MEDIUM/LOW changes (ever)
- Run on a dirty working tree
- Use comments as the source of truth for anything
- Introduce new abstractions just to break cycles
- Remove dynamically-imported code, config-referenced code, framework-convention code, or generated code
- Strengthen `unknown` at legitimate boundaries
- Silence real error handlers
- Delete deprecated code that still has callers
- Fabricate WHY in rewritten comments

## Where state lives

`.tidy/runs/<run-id>/`:
- `state.json` — phase, status, configuration
- `tooling.json` — detected language tools
- `tracks/<name>/assessment.md` — per-track findings
- `tracks/<name>/applied.md` — what actually got applied per track
- `reviewer-debrief.md` — final cross-track summary
- `reviewer-checks.md` — final check matrix output

You don't need to read any of these. Assessments and the debrief render in chat. Files exist for resume, audit, and revert.

## To start

```
/tidy:run
```

That's it. Defaults to dry-run, so nothing changes until you opt in.
