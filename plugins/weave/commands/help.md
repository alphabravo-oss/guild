---
description: "Explain the weave plugin and how to use it"
argument-hint: ""
allowed-tools: []
---

# Weave Help

Render this help text to the user verbatim.

---

# weave — author really good dynamic Workflow scripts

Claude's **Workflow tool** fans out subagents deterministically — `agent()`, `pipeline()`,
`parallel()`, budget-driven loops. Writing a *good* one is mostly knowing the rules: pipeline
by default, barrier only when a stage truly needs all prior results, schema'd returns instead
of parsing JSON-as-text, adversarial verification for findings, loop-until-dry for unknown-size
work. Weave bakes those rules into a generator so you don't freehand the orchestration.

## How to use it

```
/weave:make <what you want the workflow to do>
```

The task is free-form. Weave classifies it into an orchestration archetype, picks the fan-out
topology, authors a self-contained script, **validates it** (`node --check` + a 12-point
self-audit), and saves it to `.claude/workflows/<name>.js`.

Examples:

```
/weave:make review the current diff for bugs and prove each finding is real

/weave:make research how teams do blue-green deploys on k8s and write a cited report

/weave:make migrate every call site from the old logger API to the new one --run

/weave:make map this unfamiliar codebase's subsystems into one architecture overview
```

Flags:

- `--run` — after authoring + validating, actually run the workflow (explicit opt-in).
- `--save=<name>` — name the saved file (otherwise derived from the task).
- `--args=<json>` — JSON passed verbatim as the workflow's `args` input.

## What you get

A validated, named Workflow script in `.claude/workflows/`, parameterized over `args` so it
is reusable. Run it now with `--run`, run it later by name, or edit and re-run. To resume a
paused run, re-invoke the Workflow tool with `{scriptPath, resumeFromRunId}`.

## The archetypes it knows

| archetype | shape |
|---|---|
| **understand** | parallel readers per subsystem → one synthesis (barrier justified) |
| **design** | N approaches from different angles → judge panel → synthesize winner |
| **review** | dimensions in a pipeline, each finding adversarially verified as it lands |
| **research** | multi-modal sweep → deep-read → verify claims → cited synthesis |
| **migrate** | discover sites → transform each in a worktree → verify |
| **generic** | loop-until-dry / loop-until-budget for unknown-size work |

The full generation procedure lives in `references/rulebook.md` — hard rules, archetype
skeletons, schema authoring, and the 12-point pre-flight audit.

## Note

Running a workflow can spawn many agents and use a lot of tokens — that's why `/weave:make`
authors and saves by default and only *runs* when you pass `--run` or say so.
