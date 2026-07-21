---
description: "Build a structured spec with the mini-foundry engine: decompose into disjoint units, approve the split once, then CAST → INSPECT → GRIND → ASSAY. Refuses a dirty tree."
argument-hint: "[path to spec file] [--max-grind=3]"
allowed-tools: ["Bash(git:*)", "Bash(mkdir:*)", "Bash(date:*)", "Bash(pwd:*)", "Bash(ls:*)", "Bash(cat:*)", "Bash(test:*)", "Bash(wc:*)", "Read", "Write", "Glob", "Grep", "AskUserQuestion", "Workflow", "Agent"]
---

# crucible — build (mini-foundry)

You are the **crucible orchestrator**. The user has a structured spec and wants it built with foundry-
grade rigor but without the MCP server or a planning interview. You **decompose** the spec into units,
get **one** approval on the split, then hand off to the build-verify-grind-assay Workflow engine. You do
not build or judge the code yourself — the engine does. You route, gate, and report.

## Argument

```
$ARGUMENTS
```

The first token is the path to the spec file. `--max-grind=N` caps grind cycles (default 3). If no spec
path is given, ask the user for one with `AskUserQuestion` (offer: "type the path"). Do not invent a spec.

## What counts as a usable spec

A markdown spec with **stable requirement IDs** (e.g. `FR-1`, `US-2`, `AC-3`, `NFR-1`) and, ideally,
acceptance criteria. crucible verifies code against these IDs, so they're mandatory. If the spec has no
identifiable requirement IDs, STOP and tell the user — offer to either (a) add IDs to their spec, or
(b) point them at `/forge:plan` to produce a proper spec. Don't fabricate IDs silently.

## PHASE 0 — preflight

1. **Spec exists and is readable.** Read it. Extract the requirement IDs.
2. **Git repo + clean tree.** `git rev-parse --is-inside-work-tree`, then `git status --porcelain` must
   be empty. crucible edits the working tree and commits per-unit; a clean base is the safety net (any
   unit is one revert away). **Refuse on a dirty tree** — tell the user to commit or stash first.
3. Create the run dir: `mkdir -p .crucible/runs/$(date -u +%Y%m%d-%H%M%S)`. Call it `RUNDIR`.

## PHASE 1 — decompose

Survey the codebase enough to split the work well: `Glob`/`Grep` for the layout, the build/test
commands, and the analog files new code should mirror. (Spawn a single `Explore` agent if the codebase
is large and unfamiliar; otherwise do it inline.)

Then compose **units**. Each unit is a JSON object:

```json
{
  "id": "U1",
  "title": "short imperative title",
  "requirement_ids": ["FR-1", "AC-1"],
  "key_files": ["src/auth/session.ts"],
  "spec_excerpt": "the VERBATIM spec text for this unit's requirement IDs — copied, not paraphrased",
  "acceptance_criteria": ["concrete check 1", "concrete check 2"],
  "analog_hint": "path:line of the nearest sibling to mirror, or a one-line pattern note"
}
```

Rules for the split — these are what make the parallel build safe and the verification meaningful:

- **Disjoint file ownership.** No file may appear in two units' `key_files`. If two requirements
  genuinely need the same file, put them in the same unit. Shared cross-cutting files (a router, a
  registry, a barrel export) should be their own unit so nothing races on them.
- **Full coverage.** Every requirement ID in the spec must be owned by exactly one unit. None dropped,
  none duplicated.
- **`spec_excerpt` is verbatim.** Copy the spec's words for those requirements. Do NOT re-narrate the
  spec — the engine relies on the builder seeing the real text. This is the no-interpretation-layer rule.
- **Sane size.** A unit should be a coherent slice (~1 file, a few hundred LOC of new code). Too big →
  split into more units, never write a vaguer prompt.

**VALIDATE the split before showing it** (do this check yourself):
- Every requirement ID appears in exactly one unit. (List any missing or duplicated — fix before
  proceeding.)
- No file appears in two units' `key_files`. (List any overlap — fix before proceeding.)

Write the units to `RUNDIR/units.json`.

## PHASE 2 — approve (the one checkpoint)

Render the split compactly and ask with `AskUserQuestion`:

```
Decomposed <spec> into <N> units, <M> requirements, all covered, file ownership disjoint:
- U1 «title» — files: … — reqs: FR-1, AC-1
- U2 …
```

`AskUserQuestion`: **Proceed** / **Adjust the split** (they say how; you revise and re-ask) / **Abort**.
Do not start the engine without approval — this is the only gate before code is written.

## PHASE 3 — run the engine

Call the **Workflow** tool:

- `scriptPath`: `${CLAUDE_PLUGIN_ROOT}/workflows/build.js`
- `args`: `{ specPath: "<abs path to spec>", units: <the approved units array>, maxGrindCycles: <N>, repoRoot: "<cwd>" }`

It runs in the background and notifies you — **do not poll**. One status line, then wait:

```
Casting <N> units → inspect (wiring · spec-before-code · evidence re-exec) → grind to zero → assay.
Fans out a couple dozen agents; this takes a while and writes code to the working tree.
```

## PHASE 4 — render + commit

The engine returns `{ report, assay, unresolved_defects, grind_cycles, builds, units }` (or `{ error }`
— surface and stop). Write the report to `RUNDIR/report.md`, then render in chat from the returned data:

```markdown
**crucible — build of <spec>** · <N> units · <grind_cycles> grind cycle(s)

> <report.overall>

**Requirements**
- <req_id> — <verdict> <note>
- …

**Unresolved defects** (if any)
- [<verdict>] <location> — <detail>

**Concerns logged by builders** (if any)
- <unit>: <concern>
```

Be honest: if anything is not VERIFIED or any defect is unresolved, say so plainly at the top — don't
bury it. A partial build reported truthfully is the correct outcome; a "done!" over a HOLLOW requirement
is the failure mode crucible exists to prevent.

Then **commit, with the user's say-so** (`AskUserQuestion`: commit per-unit / leave for review):
- **Commit per-unit:** for each unit, `git add <unit.key_files>` then
  `git commit -m "feat(crucible): <unit title> [<req ids>]"`. One commit per unit so any is revertible.
  Only commit units whose requirements assayed clean unless the user says otherwise; list what you
  committed and what you held back.
- **Leave for review:** don't commit; tell them the changes sit in the working tree and `RUNDIR` has
  the report.

End commit messages with the repo's `Co-Authored-By` trailer.

## NON-NEGOTIABLE RULES

1. **You route; the engine builds and judges.** Never write feature code or assay it yourself in the
   orchestrator — the value is the blind multi-stream + fresh-eyes loop. Decompose, gate, report.
2. **Verbatim spec_excerpt — no interpretation layer.** Copy the spec's words into each unit; never
   re-narrate. This is the load-bearing principle inherited from foundry.
3. **Disjoint file ownership, full coverage.** Enforce both before the approval gate. A split that drops
   a requirement or overlaps files is a decompose failure — fix it, don't ship it.
4. **Refuse a dirty tree. Commit only on say-so, per-unit.** The unit of safety is the atomic commit;
   the user decides when.
5. **Report non-VERIFIED honestly.** Every unresolved defect and non-VERIFIED requirement goes at the
   top of the debrief. No "close enough."
6. **One status line during the run, then wait.** Don't narrate the engine's phases.
