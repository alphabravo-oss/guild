---
name: content-types
description: What each kind of page is for, what belongs in it, what must never go in it, and the quality characteristics a page is judged against. Sections from The Good Docs Project, quality characteristics from ISO/IEC/IEEE 26514. Load before writing or auditing any page.
---

# Content types

Every page declares what it is and who it is for, in frontmatter:

```yaml
doc_type: how-to
audience: operator
```

Those two declarations are what make a page checkable. `doc_type` decides which sections belong
and what must never appear; `audience` decides **what the page may be about** and, second, the
reading grade it is held to. The `reader-lens` skill carries that rule; `house-rules` section 6
carries the table. A page that declares no audience is a defect, because a rule nothing declares
is a rule that never fires. `scripts/doctype.py check` validates each page
against its declared type, and reports two severities that must not be confused.

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/doctype.py types           # what each type is for
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/doctype.py template how-to # the starting skeleton
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/doctype.py check docs      # exit 1 on a defect
```

## A skeleton is not a rule

The templates carry the sections The Good Docs Project recommends: Overview, Before you start,
Steps, See also. They are a starting point for a blank page.

**They are not validated, and requiring them would be wrong.** Only 3 of Harvester's 128 pages
carry a literal `Overview` heading. Real documentation names its sections after its subject,
`Hardware Requirements` rather than `Overview`, and a checker that demanded the template's words
would report 125 false findings against the best documentation in the field.

So the skeleton guides the writing, and the checker tests only what is actually a defect.

## The types

| Type | For | Never contains |
| --- | --- | --- |
| `tutorial` | Learning. Hands on, teaches a skill, 15 to 60 minutes | Exhaustive option tables |
| `how-to` | One task, for someone who already knows the basics | Teaching the concept |
| `reference` | Structured entries, looked up rather than read | Procedures |
| `explanation` | Why it is built this way, what was refused | Procedures, endpoint schemas |
| `explanation` + `audience: user` | The choice the reader is making, and how to decide | Any mechanism they cannot act on |
| `quickstart` | The primary feature end to end, under two hours | Error cases, secondary features |
| `api-reference` | Every endpoint, parameter and response | Tutorials, concepts |
| `glossary` | Terms a reader will not know | Procedures |
| `troubleshooting` | A symptom, its cause, its fix | Concepts |

**`explanation` is the type that absorbs internals**, because "why it is built this way" reads
as permission to describe how it is built. In the last full run of this plugin, 18 of 67 pages
were explanation and the worst of them explained a state machine to a reader who cannot see one.

An explanation page for a `user` earns its place by settling a choice they actually face: which
option to pick, why their number came out different, whether an estimate is safe to act on. If it
explains something the reader cannot act on, it is a `developer` page in the wrong directory, and
the fix is to move it. `doctype.py` tests this by how often the page addresses the reader at all,
which is crude and catches the real cases.

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/doctype.py template explanation user
```

**Tutorial and how-to are the pair people confuse.** A tutorial is learning oriented and a how-to
is problem oriented. A tutorial teaches someone who does not know the product yet; a how-to
answers someone who does. Writing one as the other is the most common single failure in
documentation, which is why type mixing is a defect rather than a note.

## Defects, which fail the gate

- **Type mixing.** A reference or explanation page containing step by step instructions, or a
  tutorial containing an exhaustive option table. Detected by shape: a numbered list whose items
  start with an imperative verb is a procedure, while `1. A free automated scan` is a list.
  Move the content to its own page of the right type and link to it.
- **An image with empty alt text.** ISO 26514 names accessibility as a quality characteristic and
  a screen reader has nothing to say about that image. Harvester has 365 of these.
- **A skipped heading level.** `h2` followed by `h4` reports a gap in the outline to a screen
  reader.
- **Internals on a published page.** A `file:line` reference, a source path, a working-note tag
  like `[?]`, or an internal instruction file such as `CLAUDE.md`. A reader came to use the
  product and is being shown the machinery that produced the page instead. Anchors belong in an
  HTML comment beside the claim, where `drift.py` reads them.
- **A page with no declared reader.** Until this was a defect it was a note nobody acted on, and
  every page of the last full run was in that state.
- **Subject matter the declared reader cannot touch.** A symbol name in backticks, a request
  route, a part of the architecture, or on a `user` page an environment variable. This is the
  rule that stops a page passing every other check while being written for the wrong person. See
  `reader-lens`.
- **An acronym the documentation never expands.** On a `user` page. Elsewhere it is an advisory.
  Expanding it once, or adding it to the glossary, clears it.
- **A `user` explanation page that never addresses the reader.** It is explaining a mechanism
  rather than settling a choice.

## Advisories, which are reported and never fail the gate

Real guidance that good documentation does not follow universally, so it informs rather than
blocks.

- **A tutorial with no stated outcome.** Good Docs is emphatic: open with "By the end of this
  tutorial you will be able to ...". It is what tells a reader whether the page is for them.
- **Steps with no prerequisites.** A reader who reaches step 4 and finds they needed credentials
  has been failed. Only 19 of Harvester's 94 pages with steps state prerequisites, so this is
  advice rather than law.
- **A reference page with no table.** Reference reads best as structured entries.
- **Reading grade above the ceiling.** Flesch-Kincaid, measured against the page's own
  `audience`: 10 for `user`, 13 for `operator`, 15 for `developer`. A page with no declared
  audience is read against `user`, so an undeclared operator page reports as too dense, which is
  the correct nudge. `WEBSTER_READING_GRADE` overrides every page at once and should be rare.
  The grade is driven by sentence length more than by vocabulary.

## What each type is judged on

From Good Docs: a type is not judged on criteria it does not serve. Reference must be
comprehensive and may be dry. A tutorial must be well written and must not try to be complete.
Spending effort on the wrong criterion is how documentation gets long without getting better.

| Type | Comprehensive | Writing quality | Current | Specific reader |
| --- | --- | --- | --- | --- |
| tutorial | should not | must | should | must |
| how-to | should not | should | must | must |
| reference | must | may | must | may |
| explanation | may | must | may | should |
| quickstart | should not | must | must | must |
| api-reference | must | may | must | may |

## ISO/IEC/IEEE 26514

The standard for designing and developing information for users names eight quality
characteristics. Four are measured here, one is measured elsewhere, three need a person.

| Characteristic | How it is judged |
| --- | --- |
| Accessibility | Measured: alt text, heading levels |
| Understandability | Measured: reading grade against the page's own declared audience |
| Conciseness | Measured: sentence length drives the grade |
| Consistency | Measured in part: one term for one thing |
| Subject fit | Measured: whether the page is about something its declared reader can act on |
| Correctness | Measured by `drift.py`, which resolves every cited anchor |
| Usability | A person judges whether a reader can find and apply it |
| Clarity | Measured in part: undefined acronyms, stated outcome, prerequisites, sentence and section length against the reader. The rest is `webster-reader`, which reads the page knowing nothing about the product and reports where it was stopped |
| Minimalism | A person judges whether anything here is unnecessary |

Do not claim conformance to 26514. It is a purchased standard aimed at organisations with a
documentation function, and what is implemented here is its quality model rather than its
process requirements. Saying "checked against the 26514 quality characteristics" is true.
Saying "26514 conformant" is not.
