---
description: Survey the stack and the real surface, then agree the shape of the documentation
argument-hint: "[path to repo, defaults to cwd]"
allowed-tools: Bash, Read, Write, Glob, Grep, Task
---

Load the `house-rules` skill first. Write no page content in this command.

## Step 1, run the survey before reading anything by hand

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/survey.py .
```

It returns the stack, the frameworks, and the real public surface with a `file:line` anchor on
every entry: HTTP routes from Next.js App Router, Next.js Pages API, Express, Fastify, Hono,
FastAPI, Flask and Go mux, plus CLI binaries, package exports, environment variables and any
OpenAPI spec. It also returns the test count and the extractors that fit this stack.

**Treat its output as the surface.** Do not re-derive the route list by reading directories,
and do not add an endpoint it did not find without an anchor of your own. If it missed something,
that is a bug in the script worth fixing rather than a gap to paper over in prose.

Then check the current drift state, which also tells you whether documentation already exists:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/drift.py check
```

## Step 2, read what the survey pointed at

Read each anchored handler, the config loader, and the tests. The test count matters: it decides
which claims are allowed to be `[SPEC]` and which start as `[?]`. A surface item with no test
behind it is not a verified capability.

If `gsd:map-codebase` is installed and the repo is unfamiliar, run it rather than re-deriving
the same map by hand.

## Step 3, fix the reader

Name them concretely, per house-rules section 6. If the repo's own memory file states its
audience, use that and say so.

## Step 4, choose the shape

Sort the pages into four modes and keep them apart:

- **Tutorial**: learning, start to finish, one path that works.
- **How-to**: a specific goal, for someone who already knows the basics.
- **Reference**: exhaustive, dry, looked up rather than read. Load the `extraction` skill. Most
  of this is generated, not written.
- **Explanation**: why it is built this way, what was refused.

Plus the two mandatory pages from house-rules section 4.

## Step 5, emit the plan

Write `docs/docs-plan.md`:

- the reader profile
- the stack and the extractors that will be used, with their commands
- one row per page: path, mode, audience, which agent writes it, what evidence it needs
- the surface table straight from the survey, anchors included
- claims already known to be unverifiable, so they start life tagged
- what is deliberately not being documented, and why

Show the plan and stop. Do not write pages without the user's say-so.
