---
description: Survey the stack and the real surface, then agree the shape of the documentation
argument-hint: "[path to repo, defaults to cwd]"
allowed-tools: Bash, Read, Write, Glob, Grep, Task
---

Load the `house-rules` and `reader-lens` skills first. Write no page content in this command.

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

## Step 1.5, the surface a person sees, before the one a developer sees

**Do this before reading a single handler.** Documentation written after a morning in the source
describes the source, because that is what was read. It is the largest single reason a set of
documentation reads as though its reader already knows the product, and no amount of editing
afterwards undoes it.

The survey returns both surfaces. The one above is routes, exports and variables. This one is
what a person can see and do:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/survey.py . | python3 -c "
import json,sys
u = json.load(sys.stdin)['user_surface']
for k in ('screens','commands','labels','messages'):
    print(f'--- {k} ({len(u[k])})')
    for i in u[k][:40]: print('   ', i)
"
```

- `screens` are the pages somebody can be on
- `labels` are the words printed on the buttons, headings, tabs and fields
- `messages` are what the product says when something goes wrong
- `commands` are the subcommands somebody types

Then **run the product**. Open it, click through it, and write down what a person can actually
accomplish, in the order they would meet it. Where there is no interface, run the CLI and read
its help. Where neither is possible, say so in the plan and mark every user page as written
without having seen the product, because that is a real limitation and it belongs on the record.

Three things come out of this and go straight into the plan:

1. **What the product lets a person do**, in their words. This is what the subject list is
   derived from, not the route list.
2. **The vocabulary.** The words on the buttons are the words the documentation uses. Where the
   docs and the interface disagree about a name, find out which one is wrong before writing
   forty pages on top of it.
3. **The error strings**, verbatim, because a reader searching for one is searching for the exact
   wording.

## Step 2, read what the survey pointed at

Read each anchored handler, the config loader, and the tests. The test count matters: it decides
which claims are allowed to be `[SPEC]` and which start as `[?]`. A surface item with no test
behind it is not a verified capability.

**This step is for evidence, not for subject matter.** The code settles whether a sentence is
true. What the documentation is about was settled in step 1.5, and reading the source does not
get to revise it. A route with no user-visible consequence is not a page.

If `gsd:map-codebase` is installed and the repo is unfamiliar, run it rather than re-deriving
the same map by hand.

## Step 3, fix the readers

**There is more than one, and naming only the obvious one is the mistake this step exists to
prevent.** Work out which of `user`, `operator` and `developer` this product actually has, per
house-rules section 6. Most have at least two: the person who uses the thing and the person who
runs it.

The audience is a lens, not a reading grade, and it decides what each page is allowed to be
about. Get it wrong here and every page inherits it. A product that is itself infrastructure has
operators where another product has users, and `--subject-audience` exists for exactly that. A
declaration that does not match the reader is worse than none, because it silences the check.

For each, describe the actual person rather than the label. Not "developers" but "the person who
built this with an AI coding tool, technical enough to deploy and not technical enough to read a
bundle". What they know, what they do not know, and why they are here.

If a reader genuinely does not exist for this product, say so and leave that section of the tree
empty rather than writing pages nobody will read. If the repo's own memory file states its
audience, use that and say so.

## Step 4, choose the subjects

Load the `structure` skill. **The layout is fixed**, so this step is not about inventing a shape.
It is about naming the subjects that go in it.

A subject is a thing in the product a user would say out loud. Derive them from the surface the
survey found and from the product's own vocabulary. Three to twelve. Not `utils`, not `core`,
not `guides`, and never a code module name.

Load the `content-types` skill. Every page declares what it is, and the declaration is what
makes it checkable later. Record the type per page in the plan: `tutorial`, `how-to`,
`reference`, `explanation`, `quickstart`, `api-reference`, `glossary` or `troubleshooting`.

Tutorial and how-to are the pair to get right. A tutorial teaches someone who does not know the
product; a how-to answers someone who does. Choosing wrong is the most common failure there is.

Content type never governs where a page lives. The directory is the subject, the type is how the
page is written.

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

- every reader, named concretely, and which sections of the tree each one reads
- the stack and the extractors that will be used, with their commands
- the subject list, with why each one is a subject
- one row per page: path inside the tree, `doc_type`, `audience`, which agent writes it, what evidence it needs
- the user surface from step 1.5: the screens, the vocabulary taken off the interface, and the
  error strings verbatim
- whether the product was actually run, and if not, why not
- the surface table straight from the survey, anchors included
- claims already known to be unverifiable, so they start life tagged
- what is deliberately not being documented, and why

Show the plan and stop. Do not write pages without the user's say-so.
