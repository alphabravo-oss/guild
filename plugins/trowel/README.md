# trowel

trowel builds a spec into code with as few agents as the job allows. You run `/trowel:build <spec>`. Code checks the
environment and plans the work; builders write it test-first, reusing what the project already has; each slice is
checked against the spec the moment it lands; code runs the project's own build, lint, tests and real end-to-end
check; fixers take every finding in one batch; and the report gives a verdict with evidence for every requirement.

It is a separate plugin from forge and foundry and changes neither. It reads any Markdown spec: a forge spec, or a
hand-written one.

## Use

```
/trowel:build docs/specs/metrics/spec.md
/trowel:build spec.md --max-rounds 1
/trowel:build --resume metrics-20261016-101500
```

| Flag | Default | Meaning |
|---|---|---|
| `--max-rounds N` | 2 | Fix rounds before what remains goes to the report. 0 reports without fixing. |
| `--max-agents N` | 40 | Hard cap on agent calls. The last call is kept for the report, so a capped run still reports. |
| `--resume RUN` | none | Continue a run from its last completed step, on its `trowel/<run>` branch. |

You need a git repository with a clean working tree, `python3`, and the project's own toolchain. trowel works on a new
branch, `trowel/<run>`, commits there, never pushes, and never creates or moves any other branch or tag. Everything
else it writes goes to `trowel-archive/<run>/`, which ignores itself in git. Run one build per working tree; to run
several at once, give each its own clone or worktree.

## What a spec needs

| The spec has | trowel does |
|---|---|
| Requirement IDs (`FR-1`, `AC-014`, any short capital prefix) | Tracks, checks, fixes and reports each one. |
| No IDs, just a list | Numbers the top-level items `SPEC-1`, `SPEC-2`, ... and warns. |
| A seal (`sealed_by: validate-spec.py sha256:...`) | Refuses to build if the spec changed after sealing. |
| No seal | Builds it as given, with a note. |
| A named end-to-end check ("run `make e2e`") | Runs it in code every round. Without one, build, lint and unit tests only. |

## Results

The same feature (abk8s P1.1, Prometheus `/metrics` on the supervisor, 66 requirements) was built from the same spec by
each workflow, then reviewed blind against old foundry by a reviewer who did not know which was which: correctness,
tests, fit with the codebase, simplicity and operational safety, 10 points each, with 12+ mutations applied to both.

| Build | Time | Blind score vs foundry 4.8 (same review) |
|---|---|---|
| foundry 4.8 | 2h42m | (35-41 across reviews) |
| plain Claude Code, one prompt | 30m | 33 vs 39 |
| trowel, first version | 1h52m | 40 vs 38 |
| trowel, + per-slice checks | 2h39m | 38 vs 37 |
| **trowel, + reuse inventory and dependency-failure checks** | **1h35m** | **39 vs 36** |

The current version won on correctness (9 vs 7), tests (9 vs 7) and operational safety (9 vs 7). It still lost on
fit (6 vs 7) and simplicity (6 vs 8), mostly from code the spec did not ask for; the scope rules below were added for
that and have not yet been through a benchmark run. Reviewer variance is about 6 points, so treat single comparisons
with care. On a larger UI spec (37 requirements, seven slices) it finished unattended in 4h18m with 31 of 37 met.

## The flow

| Step | What happens | Done by |
|---|---|---|
| 1. Preflight | Seal check if the spec has one, clean tree, the spec indexed by requirement ID, build/lint/test commands from the project's files. The end-to-end check is named from the spec and the project's targets (Makefile, magefile, package.json, justfile), never the plain unit-test command. Code proves it can start: its runner on PATH, Docker when its definition uses containers, a dry start, and the build at the base commit. | code; a low-effort finder agent only when code cannot name the check |
| 2. Plan | Code measures the spec. If one builder can carry it, there is no planner. Otherwise one planner splits it into slices by ten written rules (S1-S10, `prompts/planner.md`) and takes an inventory, per slice, of what the codebase and its declared libraries already provide. Code checks the plan (every requirement owned, no shared files, no dependency cycles, every reuse item and anchor exists) and writes each slice's brief from the spec verbatim. | code; planner only when needed |
| 3. Build | One Opus builder per slice, test-first, each starting as soon as the slices it depends on are done. Builders use the brief's reuse list, build only what the spec asks for, run the project checks and the end-to-end check once at the end through trowel, and never wait on another agent. | builder |
| 4. Verify | **Each slice is spec-checked as soon as it lands**, while other slices are still building: old foundry's PROVE method, spec before code, plus a reuse audit (new code that re-does what already exists), a scope check (code no requirement needs) and failure states (restarts, partial writes, a remote dependency down or slow). **Code** checks everything is committed, no requirement IDs in production code, slice ownership, build, lint, the full test suite against the base, and the end-to-end check. | spec-checker per slice + code |
| 5. Fix | One fixer per slice gets every failure and gap in one batch and stays inside the gap. A behavioural fix needs a test that fails before and passes after, which code re-runs on the pre-fix commit. Code re-checks everything; the spec-checker re-checks only the gaps it reported. At most `--max-rounds`. | fixer, then code + spec-checker |
| 6. Report | A verdict per requirement with evidence, gaps fixed and open, a "needs your call" list for anything trowel's own rules stopped a fixer from changing, timings and agent calls. | code |

A typical split run makes 20-30 agent calls; a single-builder run with nothing to fix makes 5.

## What is code and what is an agent

The Workflow script (`workflows/build.js`) decides the order of steps, how many builders run, which fixer gets which
finding and when to stop. A Workflow script has no filesystem, so each code step is a `scripts/trowel` command that a
low-effort clerk agent runs and copies back.

| Command | What it decides |
|---|---|
| `args` | the flags and the run name |
| `init` / `resume` | seal, clean tree, run dir, branch, spec index, commands, size, end-to-end detection and preflight / where a run left off |
| `preflight` | the end-to-end check's needs, its dry start, the base build |
| `plan` | the plan obeys the rules; writes the slice briefs with their reuse lists |
| `commit --as <actor>` | how builders and fixers commit; a test belongs to the slice whose file it tests |
| `checks` / `integration` | the project checks and the end-to-end check, one agent at a time, reused for unchanged code |
| `merge-spec` | joins the per-slice spec checks |
| `verify --round N` | the round's code check, and fail-before/pass-after for the previous round's fixes |
| `report` | `report.md` and `report.json` |

| Agent | Role |
|---|---|
| `builder` | builds the spec or a slice, test-first, reusing what exists (Opus) |
| `spec-checker` | spec-to-code, spec before code, assumes the code is broken; old foundry's PROVE method |
| `fixer` | every failure and gap for its slice in one pass, with fail-before/pass-after tests (Opus) |

## Run directory

```
trowel-archive/<run>/
  state.json                steps done with timestamps, plan, commits per actor, agent calls
  spec.md, spec-index.json  the spec as built, and every requirement ID with its verbatim clause
  plan.json, slices/        the plan and the slice briefs (split builds only)
  checks/                   code checks per round, per-slice and merged spec checks, end-to-end records
  fixes/                    each fixer's report per round
  report.md, report.json
```

## Tests

```
cd plugins/trowel && uv run --with pytest pytest -q tests
```

`tests/workflow_harness.mjs` runs `workflows/build.js` with scripted agents and command results through every branch;
the Python tests run the helpers against real git repositories in temporary directories.

## Limits

- **Run time varies by about 40 minutes** from one run of the same spec to the next, so compare versions on more than
  one run.
- **The single-or-split decision is an estimate:** 25 new lines per requirement, and only the paths the spec names.
- **End-to-end detection is heuristic.** Name the check in the spec (`run \`make e2e\``) to be sure.
- **No validator.** A wrong gap goes straight to a fixer, which can dispute it; the report shows the dispute.
- **Slices share one working tree**, so one build per tree.
- **Failure names are parsed** for Go, pytest, Jest/Vitest, cargo, Maven/Gradle, .NET and compiler-style errors;
  other runners are compared by exit code only.
