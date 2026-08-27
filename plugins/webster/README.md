# webster

**Documentation for the person who has to use the thing, where every claim carries its source
and the ones that could not be checked say so.**

Four documentation plugins were installed to do this job. Two of them are the same plugin: the
`docs-architect` and `tutorial-engineer` agents in `code-documentation` are byte-identical to the
ones in `documentation-generation` apart from the `name:` line, and `/doc-generate` is identical
in both. Between them they could write a great deal of documentation. None of them could tell you
which sentences were true.

webster merges them into one surface and adds the missing half.

## What was merged

| Source | What was taken |
| --- | --- |
| `documentation-generation` | All five agents, and the ADR, changelog and OpenAPI skills unchanged |
| `code-documentation` | Nothing. It is a strict subset of the above |
| `documentation-standards` | The HADS tags: `[SPEC]`, `[NOTE]`, `[BUG]`, `[?]` |
| `repo-doctor` | The nine-criterion README rubric, scored with cited evidence |
| A UI slop-rule corpus | Copy and residue rules, retargeted from rendered HTML to markdown and diagrams |
| The Good Docs Project | The content types, their sections, and what each type may not contain |
| ISO/IEC/IEEE 26514 | The quality characteristics. Four are measured, one is measured by `drift.py`, three need a person |

Dropped on purpose: the generic `/doc-generate` command, which is a large stack-agnostic template
dump written around Python, FastAPI and Sphinx, and produces noise in a repo that is none of those.
`/webster:plan` surveys the actual repo instead.

## Install

```
/plugin marketplace add ~/Projects/webster
/plugin install webster@webster
```

## Use

```
/webster:plan     see the product the way a person does, name the readers and the subjects,
                  scaffold the tree and the site
/webster:write    extract reference material, write the pages, record the manifest
/webster:audit    drift, layout, seven gates, prioritised punch list
```

## Every page declares who it is for

This is the rule the rest of the plugin hangs off, and it is the one that decides whether the
documentation is about the product or about the code.

```yaml
doc_type: how-to
audience: operator
```

`audience` decides **what the page may be about** first, and how hard its sentences may be
second. That order matters, because a page can describe a status enum in short words and still be
useless:

> A deployment's status is the field everything else in the UI hangs off. This page is about how
> that field moves.

Nothing there is untrue, nothing is long-winded, and it is a developer's model of the system
handed to somebody who wanted to know whether their cluster was ready.

| Audience | May name | May not name |
| --- | --- | --- |
| `user` | Screens, buttons, fields, menus, their own files, what the product gives back, what it says when something fails | Symbols, routes, environment variables, architecture |
| `operator` | The above, plus commands, flags, config files, variables, ports, logs | Symbols, architecture |
| `developer` | Anything in the system | nothing |

Those pages all exist. `developer/` and `advanced/` are where the internals go, and they declare
a different reader. Moving a page is the fix; editing `audience:` until the checker goes quiet is
not. `WEBSTER_LENS_ALLOW` widens the vocabulary where a product's readers genuinely use it, and
a Kubernetes tool's operators really do handle controllers.

A page that declares no reader is a defect rather than a note, which is a correction rather than
a preference: in the last full run before the rule existed, none of 67 pages declared an
audience, so every rule that depended on one was inert.

## The product is read before the source is

`/webster:plan` looks at what a person can see and do before it opens a single handler.
`survey.py` returns both surfaces:

- **`user_surface`**, the screens, the words printed on the buttons and headings, the error
  messages, the subcommands. This is what the subject list and the vocabulary come from.
- **`surface`**, routes, exports, environment variables. This is how a sentence gets checked,
  not a table of contents.

The order is the point. Documentation written after a morning in the source describes the source,
because that is what was read, and no amount of editing afterwards undoes it.

## The scripts

The mechanical half is three scripts rather than prompts, so it is deterministic and runs in CI
without an agent.

| Script | What it does |
| --- | --- |
| `scripts/survey.py` | Two surfaces, both anchored to `file:line`. **`user_surface`**: the screens, the text on the buttons, headings and fields, the product's own error strings, the subcommands somebody types. **`surface`**: HTTP routes from Next.js App Router and Pages API, Express, Fastify, Hono, FastAPI, Flask and Go mux, plus CLI binaries, package exports, env vars and OpenAPI specs. Names the extractors that fit the stack |
| `scripts/drift.py` | `record` stores HEAD, a hash of the docs tree and every anchor the pages cite. `check` reports anchors that no longer resolve and pages whose cited code changed. A set that cites no sources returns `no_anchors` rather than `clean`, because resolving every anchor you have is not the same as having been checked. Exit 1 on drift, 2 on nothing to measure |
| `scripts/scaffold.py` | Writes the documentation tree in the layout Harvester uses, subject-first directories with `_category_.json` ordering, plus a Docusaurus site whose sidebar is generated from the filesystem. `check` validates an existing tree and exits 1 on any violation |
| `scripts/doctype.py` | Per-page content type **and reader**. Skeletons from The Good Docs Project, quality checks from the ISO/IEC/IEEE 26514 characteristics. Defects (no declared reader, subject matter that reader cannot act on, an acronym the docs never expand, a user explanation page that never addresses the reader, type mixing, missing alt text, skipped heading levels) exit 1; advisories (no prerequisites, no table, reading grade, vocabulary pointing at the machinery) are reported. Findings are grouped by rule with a count |
| `scripts/slop.py` | A copy and residue rule corpus retargeted to markdown, plus tells specific to docs and to generated diagrams. Prose rules skip code fences, diagram rules run only inside `mermaid`, `d2` and `dot` fences. Exit 1 on any high severity finding |
| `scripts/rendered.py` | Reads the built HTML rather than the markdown, and reports anything internal that reached the reader: a visible `file:line`, a source path, a working-note tag, a frontmatter key printed as text. The only check that sees what a browser sees. Exit 1 on any leak |
| `scripts/llmstxt.py` | Builds an llms.txt to the llmstxt.org format from pages that exist on disk |

## The layout is fixed

Every set of documentation this plugin produces uses the same tree, modelled on
[Harvester](https://github.com/harvester/docs): subject-first directories at the top level, a
Docusaurus site whose navigation is the filesystem, and Diataxis applied one level down to decide
how a page is written rather than where it lives.

```
docs/index.md  faq.md  getting-started/  install/  <subject>/...  api/  advanced/
troubleshooting/  developer/
```

A reader who learns where things live in one product finds them in the same place in the next.
A small product still gets the full tree, because an empty section is an honest visible gap and
the shape can be grown into.

Reference pages are extracted, not composed. A model writing an API reference from a code read
produces a table that is correct on the day and wrong by the next commit. See `skills/extraction`.

## The seven gates

A page is not finished until all seven pass, and a gate that could not be run reports
`not_checked` with a reason rather than a pass.

| Gate | Passes when |
| --- | --- |
| Sourced | Every behavioural claim has a resolving anchor, held in a comment or in frontmatter rather than shown to the reader |
| Runnable | Every non-illustrative example has been executed |
| Shaped | The tree passes `scaffold.py check` and no page is doing another type's job |
| Lens | Every page declares a reader, and names nothing that reader cannot touch |
| Builds | The site compiles, reports no broken links, and nothing internal reached the rendered pages |
| Readable | The mechanical checks are clean, and somebody who did not already know the answer got through it |
| Honest | "What this is" and "what this deliberately does not do" both exist |
| Unslopped | No high severity slop finding, and diagrams name real things rather than categories |

**Readable used to be one gate and is now two halves.** It was delegated whole to
`courseware:learner`, which most repos do not have, so it reported `not_checked` every time and
the only gate that catches hard-to-read prose never ran. The mechanical half now always runs:
unexpanded acronyms, a tutorial with no stated outcome, steps with no prerequisites, prose above
the reader's own grade. When only that half ran, the verdict is `partial`, which is not a pass.

## Optional companions

webster works alone. Where these are installed it uses them instead of reimplementing them,
and where they are absent it marks the affected gate `not_checked` rather than passing it:

- `courseware:learner`: a naive reader's redline, for the human half of the Readable gate. The
  mechanical half runs without it
- `plumb` or `hollis:checker`: anchor resolution, for the Sourced gate
- `gsd:map-codebase`: the repo survey in `/webster:plan`
- `repo-visuals`: hero images for a published docs site

## Status

Version 0.2.0.

Exercised: all three scripts run. `survey.py` was run against a Next.js App Router repo and
returned both route handlers with anchors that resolve to the correct lines. `drift.py` was
tested on a git fixture across three cases, clean, a broken anchor, and code changed under a
page, and returned the right verdict and exit code for each.

Not yet exercised: the three commands end to end, which means every claim about what
`/webster:plan`, `/webster:write` and `/webster:audit` produce is still untested.

The name has had no trademark or product search. Do that before anything goes on it.
