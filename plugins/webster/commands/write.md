---
description: Write the planned pages, extracting reference material and anchoring every claim
allowed-tools: Bash, Read, Write, Edit, Glob, Grep, Task
---

Load `house-rules`, `structure`, `content-types`, `reader-lens`, `pedagogy`, `extraction` and `no-slop`. Read `docs/docs-plan.md`. If it does not exist, run
`/webster:plan` first rather than guessing the shape.

## Step 1, extract before writing

Re-run the survey so the surface is current:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/survey.py . > /tmp/webster-survey.json
```

Keep that file and pass it to every check below as `WEBSTER_SURVEY`. It is the only thing telling
them what the product calls its own screens and which names the code really reads as environment
variables. Without it they infer both from the pages being checked, which is the artifact
deciding how the artifact is judged.

Run every extractor the plan named. Reference pages are built from that output. Per the
`extraction` skill, a reference page a model composed from memory is a page that drifts.

Keep `user_surface` from that survey open while writing. It carries the words the product prints
on its own buttons, headings and error messages, and those are the words a user page uses. A page
that invents its own name for a screen sends the reader looking for something that is not there.

## Step 2, make sure the tree exists

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/scaffold.py check
```

On exit 2 the tree was never scaffolded, so run `/webster:plan` first. On exit 1 fix the layout
before writing prose into it, because moving pages afterwards breaks every anchor that cites them.

Write into the scaffolded paths. Do not invent a page at the docs root, and do not delete a
section because it would be short.

Each scaffolded page already carries its `doc_type`, its `audience` and that type's skeleton.
Keep both declarations, and write to the audience the page names: an `install/` page may assume a
terminal, a `getting-started/` page may not. Changing an audience means moving the page, not
editing the field. Keep the `doc_type`, and treat the skeleton as a starting shape rather than a form to fill in: rename its
sections after the subject where that reads better. `Hardware requirements` beats `Overview`.
What the checker tests is not the section names, it is whether the page does something another
type should be doing.

## Step 3, dispatch

One page at a time, to the agent the plan named:

| Mode | Agent |
| --- | --- |
| Tutorial, onboarding, first run | `webster-tutorial` |
| Reference, parameters, config, env | `webster-reference`, wrapped around extractor output |
| HTTP API, SDK | `webster-api`, wrapped around the survey surface or the OpenAPI spec |
| Explanation, architecture, why | `webster-architect` |
| Any diagram | `webster-diagram` |

**Every page whose audience is `user` carries the `pedagogy` rules, whatever its `doc_type`.**
Teaching is not a genre of page. Confining it to `webster-tutorial` meant 3 pages of 67 got a
stated outcome, prerequisites and a concept introduced before it was used, and the other 64 got
none of it. Pass the `pedagogy` and `reader-lens` rules into the dispatch for every page below
`developer` audience, not only tutorials.

Load `adr`, `changelog` or `openapi` when the plan calls for those artifacts specifically.

## Non-negotiables

1. Anchor as you write, **in an HTML comment or in frontmatter, never in the prose**. A claim
   about behaviour gets its `file:line` at the moment the sentence is written:

   ```markdown
   The scan stops after 40 requests. <!-- src/lib/net.ts:9 -->
   ```

   The reader sees the sentence; `drift.py` reads the comment. A published page that shows a
   source path, a working-note tag or an internal instruction file is showing a reader the
   tooling instead of the product, and no product does that.
2. Anything you could not check gets said in ordinary words: "this has not been tested against a
   live server". Never soften a sentence to hide the gap, and never ship the `[?]` notation
   itself.
3. Run every example. An example you did not run is labelled illustrative or it does not go in.
4. The vendored agents contradict these rules in six named places. `house-rules` section 7 lists
   each one and says which wins. Read it before dispatching, because the agent file is longer
   than the rule and a model tends to follow whichever it read last.
5. **Write the page from what the reader can touch.** A `user` page names screens, buttons,
   fields and what the product gives back. It does not name a symbol, a route, a variable or a
   part of the architecture. Those pages exist, and they live in `developer/` and `advanced/`.
   Moving a page is the fix; editing its `audience:` until the checker goes quiet is not.

## Step 4, check each page against its type

```bash
WEBSTER_SURVEY=/tmp/webster-survey.json python3 ${CLAUDE_PLUGIN_ROOT}/scripts/doctype.py check docs
```

Defects fail: a page with no declared reader, a page naming something that reader cannot touch,
an unexpanded acronym on a user page, a `user` explanation page that never addresses the reader,
a page doing another type's job, an image with no alt text, a skipped heading level.

Findings are grouped by rule with a count, so the shape of the problem is visible before the
list. `WEBSTER_SHOW_PER_RULE` changes how many examples of each are printed.

**A large `wrong-lens` count usually means the audiences are wrong, not the prose.** Check the
declarations first. A `developer/` page that never declared itself is read against `user` and
reports every symbol it names, which is the correct failure and the wrong fix to make by hand.

Advisories are reported and judged: steps with no prerequisites, a reference page with no table,
prose above the reading grade ceiling, and vocabulary that points at the machinery.

## Step 5, check for slop before recording anything

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/slop.py docs
```

Fix every high severity finding. Judge the medium and low ones rather than clearing them
mechanically: the detector cannot tell a legitimate short index from a template, which is why it
only fires on repetition. Diagram rules run inside `mermaid`, `d2` and `dot` fences, so a diagram
drawn in the default palette or with nodes named `[Service]` is caught here.

## Step 6, validate the layout

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/scaffold.py check
```

Every page carries frontmatter with a title, every directory has a `_category_.json` with a
unique position, and no page sits loose at the docs root. Then:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/llmstxt.py > llms.txt
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/drift.py record
```

`llmstxt.py` builds an llms.txt to the llmstxt.org format from pages that exist on disk. Set
`WEBSTER_BASE_URL` when the docs are published at a URL rather than read in the repo.

`drift.py record <docs-dir>` stores the current HEAD, a hash of the docs tree, and every anchor
the pages cite. It reports how many anchors it found: **if that number is 0, no claim in the set
can ever be re-verified**, and the Sourced gate fails rather than passing quietly. That is what makes the next `/webster:audit` able to tell you which pages a diff
invalidated instead of re-reading everything.

## Step 6.5, build the site, because the checkers cannot see this

```bash
cd website && npm install && npm run build
cd .. && python3 ${CLAUDE_PLUGIN_ROOT}/scripts/rendered.py check website/build
```

**A green run of every checker above does not mean the documentation works.** All of them read
markdown as text. None of them compiles it. Three defects have shipped past a fully green gate
set and were only caught by building:

- a colon inside an unquoted frontmatter `description`, which is invalid YAML and refuses the
  whole build
- a stray closing tag left at the end of a page, which fails MDX compilation
- a link written as an absolute path including the docs directory, which resolves to nothing
  because `routeBasePath` strips it

The build is the only check that reads the pages the way a reader's browser will. Run it, fix
what it reports, and run it again until it prints `[SUCCESS]` with no broken links.

Then run `rendered.py`, which reads the built HTML rather than the markdown. It is what verifies
that the anchors stayed invisible. A page can build cleanly with a visible `file:line`, a source
path or a `[?]` in it, and every markdown check will have passed. Anything it reports is on the
page in front of a reader.

Then re-run `llmstxt.py` and `drift.py record`, because a fix here changes the pages they
describe.

If the repo has no site, say the build gate is `not_checked` and why. Do not report it as a pass.

## Step 7, finish

Run `/webster:audit` before reporting done, and report the gate results honestly, including
any that came back `not_checked`.
