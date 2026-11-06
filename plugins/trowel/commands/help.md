---
description: "Explain how trowel builds a spec and what its flags do"
hide-from-slash-command-tool: "true"
---

# trowel help

Explain the following to the user, compactly and in your own words.

## What trowel is

A small spec-to-code build workflow: `/trowel:build <spec>`. It aims for about an hour per feature and old foundry's
level of correctness, with a handful of agent calls. It is one Workflow script plus small Python helpers, with three
agents and no MCP server.

## The flow

1. **Preflight**, in code: the spec's seal if it carries one, a clean working tree, the project's build, lint and test commands, the
   spec's integration or end-to-end check, and proof it can start here (its runner, a Docker daemon if it uses one, a
   dry start, the build at the base commit). If code cannot name the check, one cheap agent does. A check that cannot
   start stops the run with the failing command and its output.
2. **Plan**, in code first: if the spec fits one builder's budget, one builder. Only an over-budget spec gets a
   planner, which splits it by ten written rules (S1-S10), checked in code.
3. **Build**: one Opus builder per slice (one for most specs), test-first, from the spec text verbatim. It must run the
   integration check and read its result before it reports.
4. **Verify**, side by side: code runs build, lint, the full unit suite and the integration check against the base
   commit; one spec-checker (old foundry's PROVE method) reads the spec, then the code, and reports every gap.
5. **Fix**: one fixer per slice gets every failure and gap at once; a behavioural fix needs a test that fails before
   and passes after. Code re-checks everything; the spec-checker re-checks only the gaps it reported. At most
   `--max-rounds` rounds.
6. **Report**: `trowel-archive/<run>/report.md`, a verdict per requirement with evidence, what remains, timings and
   the number of agent calls.

## Flags

- `--max-rounds N`: fix rounds before what remains goes to the report (default 2; 0 reports without fixing).
- `--max-agents N`: a hard cap on agent calls for the run (default 60); the report still runs when it is reached.
- `--resume RUN`: continue a stopped run from its last completed step (on its `trowel/<run>` branch).

The reasons for each step are in the plugin's README.
