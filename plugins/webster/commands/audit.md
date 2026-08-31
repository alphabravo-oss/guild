---
description: Run the seven gates over existing documentation and return a prioritised punch list
allowed-tools: Bash, Read, Glob, Grep, Task
---

Load the `house-rules`, `structure`, `content-types`, `reader-lens`, `pedagogy` and `no-slop` skills. Works on documentation this plugin wrote and documentation it has
never seen. Read-only. Do not edit files unless the user asks.

## Step 1, drift, which is deterministic and comes first

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/drift.py check <docs-dir>
```

**Pass the docs directory.** It defaults to `docs`, and a repo whose documentation lives
somewhere else gets `no_manifest` for a tree that has one, which is a wrong answer that looks
like a right one.

Read the JSON `status`, not just the exit code. Exit 0 is `clean` (nothing changed) or
`unrelated_changes` (code changed that no page cites — not a finding). Exit 1 is `drift`. Exit 2 is
one of six not-checked statuses: `no_docs`, `no_manifest`, `no_anchors`, `no_git`, `head_missing`
(the recorded commit is no longer in the repo) and `hashes_partial` (some anchors were recorded
without a line digest). `hashes` reports `checked`, `partial` or `not_recorded`.

`no_anchors` is its own status and its own P0. A set where no page cites a source resolves every
anchor it has, which is not the same as having been checked. **Sourced cannot report a pass on a
`no_anchors` set.** The report also gives `pages_with_no_anchor`, which is the number to quote.

- `broken_anchors` are claims citing a file or line that no longer exists. **Every one is a P0.**
  The page states something it cannot support.
- `suspect_pages` are pages whose cited code changed since the manifest was recorded. Re-read
  those pages against the current code before anything else. This is where drift actually lives.
- `docs_edited_since_record` true means someone edited pages outside this plugin.

On exit 2, report the `status` by name and run the rest of the audit. `no_manifest` means drift
cannot be measured until `/webster:write` records one. `no_git` and `head_missing` mean the code
half could not be compared; the anchors half still ran, so `broken_anchors` may be non-empty
under any of `no_manifest`, `no_git` or `head_missing`, and every one of those is still a P0.
`hashes_partial` means re-record and re-run. Never summarise exit 2 as "no manifest".

**Do not re-read every page when the drift check is clean.** That is the point of the manifest.

## Step 2, the surface, to catch what is missing rather than wrong

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/survey.py . > /tmp/webster-survey.json
```

Save it: step 4 reads the same file through `WEBSTER_SURVEY`, so that the labels, screen names
and commands the product itself prints are not reported as leaked internals on user pages.

The survey returns two surfaces and they answer different questions. **Read `user_surface`
first.**

**`user_surface` is the coverage question.** A screen, a subcommand or an error message a reader
will meet with no page behind it is a real gap, and that is a P1. This is what "undocumented"
means for a product with users.

**`surface` is the truth question, not the coverage question.** Routes, exports and environment
variables are how a claim gets checked. They are not a table of contents.

- An endpoint the docs describe that the survey does not find is a **P0**, because it is fiction.
- An endpoint with no page is only a finding **when a reader would ever meet it**. On a product
  with a public API documented for developers, that is a P1. On a product whose users click
  buttons, it is not a finding at all, and treating it as one is how a set of documentation ends
  up describing the machinery. The old rule made it a P1 unconditionally, which pushed coverage
  toward the code and away from the reader.
- An environment variable with no page is a P1 **on a product an operator installs**, and
  nothing on a hosted product nobody self-hosts.

## Step 3, layout

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/scaffold.py check
```

Every violation is a P1, except a missing `index.md` or a subject with no overview page, which
are P0 because navigation is broken without them. A page still carrying
`<!-- webster: not written yet -->` is a stub and counts as an undocumented surface item.

## Step 4, content types

```bash
WEBSTER_SURVEY=/tmp/webster-survey.json python3 ${CLAUDE_PLUGIN_ROOT}/scripts/doctype.py check docs
```

The header line says whether the allowlist loaded (`lens allowlist: N terms from WEBSTER_SURVEY`)
or not (`no survey allowlist`). If it did not, the `wrong-lens` count is inflated by the product's
own vocabulary; fix the survey path before judging it.

Every defect is a P1, with three exceptions that are P0:

- **Type mixing on a page a reader lands on first**, because it sends them to the wrong kind of
  page for their question.
- **`no-audience`**, because every rule that depends on the declaration is inert without it, and
  a docs set with none of them is one where the lens has never run. Fix these before judging any
  `wrong-lens` count: a `developer/` page that declared nothing is read against `user` and
  reports every symbol it names.
- **`wrong-lens` on a page whose audience is correctly declared.** The page is written for
  somebody other than the person it says it is for. The fix is usually to move it, not to edit
  the frontmatter until the checker goes quiet.

`glossary-trusted` is an advisory and it is the one to read out loud. It lists terms cleared only
because the glossary defines them, and nothing anywhere reads a definition for correctness. On a
set nobody proofreads before it reaches a customer, that is the list of claims the documentation
made and the tooling took on faith.

Read the `doctype.py` header line before any of the counts. It says which oracle the run used,
and a run with no survey has judged the documentation using the documentation.

`undefined-jargon` and `explains-mechanism` are P1. Both are the page assuming knowledge of a
product the reader came to learn.

Accessibility and reading grade are checked on every page, including ones with no `doc_type`, so
this works on documentation the plugin never wrote. A page with no `doc_type` cannot be checked
against a type, and that is worth reporting rather than passing over.

## Step 4.5, prose shape

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/prose.py check <docs-dir>
```

Step 4 asks whether the page is about the right thing for the right reader. This asks whether a
reader can get through it, which the reading grade in step 4 does not answer and in two measured
cases answered backwards.

- `long-sentence` and `dense-section` are defects. A sentence past the audience's ceiling is one
  a reader has to hold open, and a run of text that long with no heading is a wall. Both are P1,
  and P0 on a page a reader lands on first.
- `long-paragraph`, `passive-voice`, `nominalisation` and `fragmented` are advisories. Judge
  them: a reference page is legitimately dense, and a status table is legitimately passive.

`prose.py limits` prints the thresholds. They scale with the declared audience, so run this
after the audiences in step 4 are right, not before.

## Step 5, README

Load the `readme-rubric` skill and walk its nine criteria, scoring each with cited line numbers.
Establish the repo category first, because the rubric weighting depends on it.

## Step 6, the seven gates

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
authorship the page cannot support.

A published docs site is also a UI, and prose rules cannot judge one. If a UI review tool is
available, run it over the built site; if not, mark that finding `not_checked` rather than
eyeballing a design and calling it a pass.

## Step 8, comprehension

Reading your own documentation cannot find comprehension defects, because you already know the
answer. Readable is therefore two halves, and it used to be one.

**The mechanical half always runs**, in step 4: unexpanded acronyms, a tutorial with no stated
outcome, steps with no prerequisites, prose above the reader's own grade. Report what it found.

**The human half needs somebody who does not already know.** If `courseware:learner` is
installed, dispatch its redline pass over the pages.

Report the gate as `pass` only when both halves ran clean, `partial` when only the mechanical
half ran, and name which half. It was previously delegated whole to a plugin most repos do not
have, so it reported `not_checked` every time and the only gate that catches hard-to-read prose
never ran. `partial` is the honest verdict and it is not a pass.

## Step 7.5, build the site, then read what it produced

```bash
grep -n onBrokenLinks website/docusaurus.config.js
cd website && npm install && npm run build
cd .. && python3 ${CLAUDE_PLUGIN_ROOT}/scripts/rendered.py check website/build
```

**Check `onBrokenLinks` before trusting the build.** Anything other than `'throw'` means a dead
link prints a warning, exits 0, prints `[SUCCESS]` and ships to the reader. Sites scaffolded
before this was fixed carry `'warn'`, so a green build on one of them is evidence of nothing.
Fix the config and rebuild rather than reading the warnings by hand.

Every check above reads markdown as text. None compiles it, so a documentation set can pass all
of them and still fail to build. Invalid YAML in frontmatter, a stray tag, and a link that
resolves to nothing have each done exactly that. A broken build is a P0: the reader gets nothing
at all.

`rendered.py` then reads the pages a browser would. An anchor lives in an HTML comment precisely
so the reader never sees it, and until this existed nothing checked whether that held. Anything
it finds is a **P0**: it is on the page, in front of the reader, now. A build that succeeds with
a visible `file:line` in it is exactly the case the markdown checks cannot see.

If there is no site to build, report both gates `not_checked` with that reason rather than a pass.

## Step 9, punch list

- **P0**: broken anchors, documented surface that does not exist, unsourced claims, examples that
  do not run, missing mandatory pages, a broken build, pages with no declared reader.
- **P1**: a screen, command or error message with no page, shape problems, an unfixed reader,
  a page written for somebody other than its declared reader, undefined jargon, comprehension
  stalls.
- **P2**: polish.

Each item: one line on what is wrong with the line number, one line on why it matters for this
repo's reader, one line of concrete replacement text.

End with the gate table, including every `not_checked` and its reason.
