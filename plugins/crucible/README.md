<p align="center">
  <b>crucible</b> — a mini-foundry — the build engine without the MCP server or the interview.<br/>
  <i>You bring the spec. Crucible builds it and refuses to let it drift.</i>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/crucible-0.1.0-F57C00?style=flat-square" alt="crucible 0.1.0"/>
  <img src="https://img.shields.io/badge/guild-pipeline-1E88E5?style=flat-square" alt="guild pipeline"/>
  <img src="https://img.shields.io/badge/Claude%20Code-plugin-8E44AD?style=flat-square" alt="Claude Code plugin"/>
  <img src="https://img.shields.io/badge/license-MIT-2E7D32?style=flat-square" alt="MIT license"/>
</p>

<p align="center">
  <a href="../../README.md">← back to the Guild marketplace</a>
</p>

---

`foundry` is a full autonomous build engine: an MCP server, phase gates, spec/prompt hashing, isolated-
worktree evidence, an interactive planning interview. **crucible** is its build-verify-fix heart
distilled into a single Workflow you can point at any spec — no MCP server, no interview.

You bring a structured spec. crucible builds it and refuses to let it drift.

## Install

In the `guild` marketplace. Add the marketplace, install `crucible`.

## Use

```
/crucible:build path/to/spec.md
/crucible:build path/to/spec.md --max-grind=5
```

The spec must have **stable requirement IDs** (`FR-1`, `US-2`, `AC-3`, …) — crucible verifies code
against them. No spec? Make one with `/forge:plan`, or hand-write one with IDs.

## The flow

```
decompose (you approve once)
   → CAST     parallel builders, verbatim unit prompts, re-executable evidence
   → INSPECT  wiring · spec-before-code assay · evidence re-execution   (blind, orthogonal)
   → GRIND    fix → full re-inspect, loop until zero defects or cap
   → ASSAY    fresh-eyes, spec-first, confirm every requirement
```

The decomposition splits the spec into **units** with **disjoint file ownership** — that's what makes
the parallel build safe and the verification meaningful. You approve the split once; then the engine
runs autonomously.

## Why the streams

Build-green is not done — **a stub compiles.** So verification is three blind, orthogonal agents, each
catching a different failure mode:

- **wiring** — every required symbol EXISTS, is SUBSTANTIVE (not a stub), is WIRED (actually called).
- **spec-before-code assay** — read the spec requirement, write the observable truths, *then* read the
  code; assume broken until proven.
- **evidence re-execution** — re-run every evidence command the builder produced and diff the output.
  "It compiles" never counts; the behavior has to reproduce.

Every non-passing verdict is a **defect**. Grind fixes and fully re-inspects until zero — or reports
what's left. It never says "done" over a HOLLOW requirement.

## Keeps / drops vs. foundry

**Keeps** the load-bearing principles — verbatim unit prompts (no interpretation layer), disjoint file
ownership, multi-stream blind verification, re-executable evidence bound to requirement IDs, defect-to-
zero grind, spec-before-code fresh-eyes assay, and an engine that **routes and verifies but never
re-narrates the spec or judges the code itself.**

**Drops** what needs the heavy machinery — the MCP gate server + spec/prompt hashing, isolated-worktree
server-side evidence (crucible re-runs commands in the shared tree — weaker isolation), wave-level
prompt caching, and the brownfield flow-graph node-by-node interview. v1 targets building a feature in
the current repo against a BYO spec.

## Safety

- Refuses a dirty git tree (atomic per-unit commits are the revert net).
- Edits the shared working tree under disjoint ownership.
- Commits only on your say-so, **per-unit** — any unit is one `git revert` away.
- Report + artifacts under `.crucible/runs/<timestamp>/`.

## Lineage

`forge` plans → `foundry` builds. crucible is the foundry build engine, mini.
