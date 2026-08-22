---
description: Write the planned pages, extracting reference material and anchoring every claim
allowed-tools: Bash, Read, Write, Edit, Glob, Grep, Task
---

Load `house-rules`, `extraction` and `no-slop`. Read `docs/docs-plan.md`. If it does not exist, run
`/webster:plan` first rather than guessing the shape.

## Step 1, extract before writing

Re-run the survey so the surface is current:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/survey.py . > /tmp/webster-survey.json
```

Run every extractor the plan named. Reference pages are built from that output. Per the
`extraction` skill, a reference page a model composed from memory is a page that drifts.

## Step 2, dispatch

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

1. Anchor as you write. A claim about behaviour gets its `file:line`, its command and real
   output, or its test name, at the moment the sentence is written. The drift script reads those
   anchors back, so an anchor written carelessly becomes a false pass later.
2. Anything you could not check gets `[?]` and a line saying what would settle it. Do not soften
   the sentence instead.
3. Run every example. An example you did not run is labelled illustrative or it does not go in.
4. The vendored agents contradict these rules in six named places. `house-rules` section 7 lists
   each one and says which wins. Read it before dispatching, because the agent file is longer
   than the rule and a model tends to follow whichever it read last.

## Step 3, check for slop before recording anything

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/slop.py docs
```

Fix every high severity finding. Judge the medium and low ones rather than clearing them
mechanically: the detector cannot tell a legitimate short index from a template, which is why it
only fires on repetition. Diagram rules run inside `mermaid`, `d2` and `dot` fences, so a diagram
drawn in the default palette or with nodes named `[Service]` is caught here.

## Step 4, record and publish the index

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/llmstxt.py > llms.txt
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/drift.py record
```

`llmstxt.py` builds an llms.txt to the llmstxt.org format from pages that exist on disk. Set
`WEBSTER_BASE_URL` when the docs are published at a URL rather than read in the repo.

`drift.py record` stores the current HEAD, a hash of the docs tree, and every anchor the pages
cite. That is what makes the next `/webster:audit` able to tell you which pages a diff
invalidated instead of re-reading everything.

## Step 5, finish

Run `/webster:audit` before reporting done, and report the gate results honestly, including
any that came back `not_checked`.
