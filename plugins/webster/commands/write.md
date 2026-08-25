---
description: Write the planned pages, extracting reference material and anchoring every claim
allowed-tools: Bash, Read, Write, Edit, Glob, Grep, Task
---

Load `house-rules`, `structure`, `content-types`, `extraction` and `no-slop`. Read `docs/docs-plan.md`. If it does not exist, run
`/webster:plan` first rather than guessing the shape.

## Step 1, extract before writing

Re-run the survey so the surface is current:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/survey.py . > /tmp/webster-survey.json
```

Run every extractor the plan named. Reference pages are built from that output. Per the
`extraction` skill, a reference page a model composed from memory is a page that drifts.

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

## Step 4, check each page against its type

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/doctype.py check docs
```

Defects fail: a page doing another type's job, an image with no alt text, a skipped heading
level. Fix them. Advisories are reported and judged: a tutorial with no stated outcome, steps
with no prerequisites, a reference page with no table, prose above the reading grade ceiling.

Set `WEBSTER_READING_GRADE=10` when the reader is the non-technical person `house-rules`
section 6 describes.

## Step 5, check for slop before recording anything

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/slop.py docs
```

Fix every high severity finding. Judge the medium and low ones rather than clearing them
mechanically: the detector cannot tell a legitimate short index from a template, which is why it
only fires on repetition. Diagram rules run inside `mermaid`, `d2` and `dot` fences, so a diagram
drawn in the default palette or with nodes named `[Service]` is caught here.

## Step 6, validate the layout and record

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

`drift.py record` stores the current HEAD, a hash of the docs tree, and every anchor the pages
cite. That is what makes the next `/webster:audit` able to tell you which pages a diff
invalidated instead of re-reading everything.

## Step 7, finish

Run `/webster:audit` before reporting done, and report the gate results honestly, including
any that came back `not_checked`.
