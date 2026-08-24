---
description: Run the five gates over existing documentation and return a prioritised punch list
allowed-tools: Bash, Read, Glob, Grep, Task
---

Load the `house-rules`, `structure`, `content-types` and `no-slop` skills. Works on documentation this plugin wrote and documentation it has
never seen. Read-only. Do not edit files unless the user asks.

## Step 1, drift, which is deterministic and comes first

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/drift.py check
```

Exit 0 means clean, 1 means drift, 2 means there is no manifest yet.

- `broken_anchors` are claims citing a file or line that no longer exists. **Every one is a P0.**
  The page states something it cannot support.
- `suspect_pages` are pages whose cited code changed since the manifest was recorded. Re-read
  those pages against the current code before anything else. This is where drift actually lives.
- `docs_edited_since_record` true means someone edited pages outside this plugin.

On exit 2, say the repo has no manifest, run the rest of the audit, and note that drift cannot be
measured until `/webster:write` records one.

**Do not re-read every page when the drift check is clean.** That is the point of the manifest.

## Step 2, the surface, to catch what is missing rather than wrong

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/survey.py .
```

Compare the surface against what the docs cover. An endpoint, CLI command, package export or
environment variable with no page is an undocumented surface item, and that is a P1. An endpoint
the docs describe that the survey does not find is a P0, because it is fiction.

## Step 3, layout

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/scaffold.py check
```

Every violation is a P1, except a missing `index.md` or a subject with no overview page, which
are P0 because navigation is broken without them. A page still carrying
`<!-- webster: not written yet -->` is a stub and counts as an undocumented surface item.

## Step 4, content types

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/doctype.py check docs
```

Every defect is a P1, except type mixing on a page a reader is likely to land on first, which is
a P0 because it sends them to the wrong kind of page for their question.

Accessibility and reading grade are checked on every page, including ones with no `doc_type`, so
this works on documentation the plugin never wrote. A page with no `doc_type` cannot be checked
against a type, and that is worth reporting rather than passing over.

## Step 5, README

Load the `readme-rubric` skill and walk its nine criteria, scoring each with cited line numbers.
Establish the repo category first, because the rubric weighting depends on it.

## Step 6, the six gates

Run house-rules section 8 over every page. For each failure record the exact line, what is wrong,
and the replacement text spelled out. Not "this section is too technical". Instead: "line 41 uses
`--severity` as a known concept, but severity is not introduced until page 3."

`Sourced` is settled by step 1 for anchors that exist. What step 1 cannot settle is a behavioural
claim carrying **no** anchor at all. Extract those by hand and check each one.

## Step 7, slop

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/slop.py docs
```

Every high severity finding is a P1, and `agent-attribution` is a P0 because it is a claim about
authorship the page cannot support. If the docs are published as a site, run `redpen scan` over
the site as well, and mark it `not_checked` if redpen is not installed.

## Step 8, comprehension

Reading your own documentation cannot find comprehension defects, because you already know the
answer. If `courseware:learner` is installed, dispatch its redline pass over the pages. If it is
not installed, say so and mark the Readable gate `not_checked`. Do not substitute your own read
for it and call it a pass.

## Step 9, punch list

- **P0**: broken anchors, documented surface that does not exist, unsourced claims, examples that
  do not run, missing mandatory pages.
- **P1**: undocumented surface, shape problems, an unfixed reader, comprehension stalls.
- **P2**: polish.

Each item: one line on what is wrong with the line number, one line on why it matters for this
repo's reader, one line of concrete replacement text.

End with the gate table, including every `not_checked` and its reason.
