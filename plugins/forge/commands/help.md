---
description: "Explain the Forge workflow and available commands"
---

# Forge Help

Please explain the following to the user:

## What is Forge?

**Forge plans. Foundry builds.**

Forge is a codebase-aware specification engine for Claude Code. It runs parallel codebase research before asking a single question, then walks you through a grounded interview, then writes a foundry-ready spec with classified, citation-backed requirements.

Unlike traditional spec interviews that ask generic questions, Forge:

- **Surveys your codebase first** with 4 parallel Explore agents.
- **Researches the ecosystem** for stale-knowledge invalidation and feature-shape orientation.
- **Detects build mode** — `brownfield`, `greenfield`, or `cosmetic` — and routes accordingly.
- **Grounds every question** in real code and verified library facts.
- **Produces foundry-native specs** with tagged requirement IDs, verbatim transcript appendix, and (for brownfield) a flow delta against the existing system.

## How it works

```
R-pre: MODE DETECT  -> brownfield / greenfield / cosmetic
R0:    SURVEY       -> 4 parallel Explore agents map the codebase
R1:    SYNTHESIZE   -> Merge findings into reality.md
R1.5:  RESEARCH     -> Targeted online research grounded in survey findings
R2:    INTERVIEW    -> Multi-round adaptive interview
                       (brownfield mode: flow-mapper + node-by-node hop confirmation)
R3:    SPEC         -> Generate foundry-ready spec.md (and flow-delta.json if brownfield)
R4:    VALIDATE     -> Verify references, citations, coverage, verbatim fidelity
```

## Modes

| Mode | When | Output |
|---|---|---|
| **brownfield** | Existing codebase, flow-shaped request | `spec.md` + `flow-delta.json` (grounded hops against the existing system) |
| **greenfield** | Empty / near-empty target | `spec.md` (end-state) |
| **cosmetic** | Non-flow-shaped (styling, copy, deps, docs, minor refactor) | `spec.md` (no flow mapping) |

Forge auto-detects based on file count and request shape, then confirms with you. Override explicitly with `--brownfield` / `--greenfield` / `--cosmetic`.

## Commands

### `/forge:plan <FEATURE_NAME> [OPTIONS]`

Start a new specification interview.

```
/forge:plan "user authentication"
/forge:plan "payment processing" --context docs/PRD.md
/forge:plan "search feature" --focus src/search,src/api
/forge:plan "new dashboard" --first-principles
/forge:plan "fresh project" --no-survey --greenfield
```

**Options:**
- `--context <file>` — initial context file (PRD, prior research, requirements doc)
- `--output-dir <dir>` — where the final spec goes (default: `docs/specs`)
- `--focus <dirs>` — comma-separated directories to focus the survey on
- `--first-principles` — challenge assumptions before detailed gathering
- `--no-survey` — skip codebase survey (greenfield / empty projects)
- `--brownfield` — force brownfield mode
- `--greenfield` — force greenfield mode
- `--cosmetic` — force cosmetic mode

### `/forge:resume`

Resume an interrupted interview. Lists in-progress sessions, lets you select one, picks up at the exact phase where it left off.

### `/forge:cleanup`

Clean up Forge state files. Asks whether to remove state only or state + survey data. **Does not** delete completed specs in `docs/specs/`.

### `/forge:help`

Show this help.

## Survey agents

During R0, Forge spawns 4 parallel Explore agents:

| Agent | Explores | Discovers |
|---|---|---|
| **Architect** | Package structure, layers, patterns | How the app is organized |
| **Data** | Models, schemas, data flow | What data structures exist |
| **Surface** | APIs, routes, UI, exports | What's exposed and extensible |
| **Infra** | Tests, CI, deps, config | Tooling and operational patterns |

Survey output lives at `docs/recon/{feature-slug}/survey/`. R1 merges it into `reality.md`.

## Output artifacts

**During the run:**
- `forge-specs/{slug}/state.md` — phase + progress
- `forge-specs/{slug}/transcript.md` — verbatim Q/A record (source of truth for R3)
- `docs/recon/{slug}/survey/` — raw survey findings
- `docs/recon/{slug}/reality.md` — synthesized codebase reality
- `docs/recon/{slug}/research/` — R1.5 research findings
- `docs/recon/{slug}/flow-graph.json` — brownfield only

**Final:**
- `docs/specs/{slug}/spec.md` — the foundry-ready spec
- `docs/specs/{slug}/flow-delta.json` — brownfield only

## Spec format

Forge specs use foundry-native ID schemes:

- **US-NNN** — User Stories
- **FR-NNN** — Functional Requirements
- **NFR-NNN** — Non-Functional Requirements
- **AC-NNN** — Acceptance Criteria
- **VC-NNN** — Verification Criteria
- **GI-NNN** — Global Invariants (architectural placement, cross-cutting rules)
- **OT-NNN** — Observable Truths

Every requirement is classified `Locked` (implement exactly), `Flexible` (teammate discretion), or `Informational` (context only). Locked requirements quote the user verbatim with a transcript citation. The full transcript is embedded as an appendix.

## Verbatim-fidelity gate

R4 runs a deterministic Python validator that refuses to finalize a spec until:

- Every Locked requirement is a byte-identical substring of its cited transcript answer.
- Every bullet, table row, and sentence in tracked sections has a citation marker.
- Every A-NNN in the transcript is cited somewhere in the spec body (no silent drops).
- The spec has a populated `## Global Invariants` section and embeds the transcript verbatim.

If any check fails, R3 has to fix it before R4 will pass.

## Complete workflow

```
1. Forge plans:    /forge:plan "my feature"
2. Foundry builds: /foundry:start "my feature" --spec docs/specs/my-feature.md
```

Forge plans. Foundry builds. You ship.
