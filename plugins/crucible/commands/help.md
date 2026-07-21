---
description: "Explain the crucible (mini-foundry) plugin"
allowed-tools: ["Read"]
---

# crucible — help

**crucible** is a mini-foundry: the build-verify-fix engine, without the MCP server or the planning
interview. You bring a structured spec; crucible builds it and refuses to let it drift.

## Command

- `/crucible:build <spec-file> [--max-grind=N]` — decompose the spec into units with disjoint file
  ownership, approve the split once, then run the engine: CAST → INSPECT → GRIND → ASSAY.
- `/crucible:help` — this.

## What it needs from you

A markdown spec with **stable requirement IDs** (`FR-1`, `US-2`, `AC-3`, …) and acceptance criteria.
crucible verifies code against those IDs, so they're required. No spec? Use `/forge:plan` to make one,
or hand-write one with IDs.

## The flow

1. **Decompose** (in the command, with your approval) — the spec is split into units, each owning a
   **disjoint** set of files and a verbatim slice of the spec. You approve the split once before any
   code is written. This is the only checkpoint.
2. **Cast** — parallel deliberate-engineering builders, one per unit, each handed its unit prompt
   *verbatim*. Each produces **re-executable evidence** bound to requirement IDs.
3. **Inspect** — three blind orthogonal streams: **wiring** (every symbol exists, is substantive not a
   stub, is actually called), **spec-before-code assay** (read the spec, form expectations, then judge
   the code), **evidence re-execution** (re-run every evidence command and diff the output). Every
   non-passing verdict is a defect.
4. **Grind** — fix the affected units and **fully re-inspect**, looping until zero defects or the cycle
   cap. Nothing is deferred; unresolved defects are reported, not hidden.
5. **Assay** — fresh-eyes agents read the spec first and confirm every requirement independently.

## What it keeps from foundry — and what it drops

**Keeps** the load-bearing principles: verbatim unit prompts (no interpretation layer), disjoint file
ownership for safe parallelism, multi-stream blind verification (build-green ≠ done), re-executable
evidence bound to requirement IDs, defect-to-zero grind, spec-before-code fresh-eyes assay, and an
engine that **routes and verifies but never re-narrates the spec or judges the code itself.**

**Drops** what needs the heavy machinery: the MCP gate server + spec/prompt hashing, isolated-worktree
server-side evidence, wave-level prompt caching, and the brownfield flow-graph node-by-node interview.
It targets building a feature in the current repo against a spec you provide.

## Safety

Refuses a dirty git tree. Edits the shared working tree under disjoint ownership. Commits only on your
say-so, **per-unit**, so any unit is one `git revert` away. Report + artifacts land under
`.crucible/runs/<timestamp>/`.

## Lineage

`forge` plans, `foundry` builds — the full pipeline with an MCP server and an interactive interview.
crucible is foundry's build engine distilled to a single Workflow you can point at any spec.
