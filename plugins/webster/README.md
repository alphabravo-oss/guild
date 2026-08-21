# webster

**Documentation for an app you built, where every claim carries its source and the ones that
could not be checked say so.**

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
| `redpen` | The copy and residue rule corpus, retargeted from rendered HTML to markdown and diagrams |

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
/webster:plan     survey the stack and the real surface, agree the shape
/webster:write    extract reference material, write the pages, record the manifest
/webster:audit    drift check, then five gates, prioritised punch list
```

## The scripts

The mechanical half is three scripts rather than prompts, so it is deterministic and runs in CI
without an agent.

| Script | What it does |
| --- | --- |
| `scripts/survey.py` | Detects the stack, then enumerates the real public surface with a `file:line` anchor on every entry: HTTP routes from Next.js App Router and Pages API, Express, Fastify, Hono, FastAPI, Flask and Go mux, plus CLI binaries, package exports, env vars and OpenAPI specs. Names the extractors that fit the stack |
| `scripts/drift.py` | `record` stores HEAD, a hash of the docs tree and every anchor the pages cite. `check` reports anchors that no longer resolve and pages whose cited code changed. Exit 1 on drift |
| `scripts/slop.py` | The redpen copy and residue corpus retargeted to markdown, plus tells specific to docs and to generated diagrams. Prose rules skip code fences, diagram rules run only inside `mermaid`, `d2` and `dot` fences. Exit 1 on any high severity finding |
| `scripts/llmstxt.py` | Builds an llms.txt to the llmstxt.org format from pages that exist on disk |

Reference pages are extracted, not composed. A model writing an API reference from a code read
produces a table that is correct on the day and wrong by the next commit. See `skills/extraction`.

## The six gates

A page is not finished until all six pass, and a gate that could not be run reports
`not_checked` with a reason rather than a pass.

| Gate | Passes when |
| --- | --- |
| Sourced | Every behavioural claim has a resolving anchor or a `[?]` tag |
| Runnable | Every non-illustrative example has been executed |
| Shaped | The page is tutorial, how-to, reference or explanation, and does not drift |
| Readable | A reader with no development background could follow it |
| Honest | "What this is" and "what this deliberately does not do" both exist |
| Unslopped | No high severity slop finding, and diagrams name real things rather than categories |

## Optional companions

webster works alone. Where these are installed it uses them instead of reimplementing them,
and where they are absent it marks the affected gate `not_checked` rather than passing it:

- `courseware:learner`: a naive reader's redline, for the Readable gate
- `plumb` or `hollis:checker`: anchor resolution, for the Sourced gate
- `gsd:map-codebase`: the repo survey in `/webster:plan`
- `repo-visuals`: hero images for a published docs site
- `redpen`: judges a published docs site as a UI, against 151 rules in a real browser

## Status

Version 0.2.0.

Exercised: all three scripts run. `survey.py` was run against a Next.js App Router repo and
returned both route handlers with anchors that resolve to the correct lines. `drift.py` was
tested on a git fixture across three cases, clean, a broken anchor, and code changed under a
page, and returned the right verdict and exit code for each.

Not yet exercised: the three commands end to end, which means every claim about what
`/webster:plan`, `/webster:write` and `/webster:audit` produce is still untested.

The name has had no trademark or product search. Do that before anything goes on it.
