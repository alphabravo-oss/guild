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

## Step 4, choose the subjects

Load the `structure` skill. **The layout is fixed**, so this step is not about inventing a shape.
It is about naming the subjects that go in it.

A subject is a thing in the product a user would say out loud. Derive them from the surface the
survey found and from the product's own vocabulary. Three to twelve. Not `utils`, not `core`,
not `guides`, and never a code module name.

Diataxis still governs how each page is written, tutorial or how-to or reference or explanation,
and it never governs where the page lives. Record the mode per page in the plan.

Then scaffold the tree, which is cheap and makes the plan concrete:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/scaffold.py init \
  --title "<name>" --description "<one sentence>" \
  --subject "<key>:<Label>,<key>:<Label>" --site \
  $([ -n "<openapi spec found by the survey>" ] && echo --api) \
  --url <docs url> --org <owner> --project <repo> \
  --edit-url https://github.com/<owner>/<repo>/edit/main/
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/scaffold.py check
```

Pass `--site` unless the user has said they do not want a published site. Pass `--api`
only when the survey found an OpenAPI spec, because that section is generated from one. The stub pages carry
`<!-- webster: not written yet -->` so an unwritten page is visible rather than absent.

## Step 5, emit the plan

Write `docs/docs-plan.md`:

- the reader profile
- the stack and the extractors that will be used, with their commands
- the subject list, with why each one is a subject
- one row per page: path inside the tree, mode, audience, which agent writes it, what evidence it needs
- the surface table straight from the survey, anchors included
- claims already known to be unverifiable, so they start life tagged
- what is deliberately not being documented, and why

Show the plan and stop. Do not write pages without the user's say-so.
