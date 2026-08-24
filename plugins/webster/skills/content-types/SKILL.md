---
name: content-types
description: What each kind of page is for, what belongs in it, what must never go in it, and the quality characteristics a page is judged against. Sections from The Good Docs Project, quality characteristics from ISO/IEC/IEEE 26514. Load before writing or auditing any page.
---

# Content types

Every page declares what it is, in frontmatter:

```yaml
doc_type: how-to
```

That declaration is what makes a page checkable. `scripts/doctype.py check` validates each page
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
| `quickstart` | The primary feature end to end, under two hours | Error cases, secondary features |
| `api-reference` | Every endpoint, parameter and response | Tutorials, concepts |
| `glossary` | Terms a reader will not know | Procedures |
| `troubleshooting` | A symptom, its cause, its fix | Concepts |

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

## Advisories, which are reported and never fail the gate

Real guidance that good documentation does not follow universally, so it informs rather than
blocks.

- **A tutorial with no stated outcome.** Good Docs is emphatic: open with "By the end of this
  tutorial you will be able to ...". It is what tells a reader whether the page is for them.
- **Steps with no prerequisites.** A reader who reaches step 4 and finds they needed credentials
  has been failed. Only 19 of Harvester's 94 pages with steps state prerequisites, so this is
  advice rather than law.
- **A reference page with no table.** Reference reads best as structured entries.
- **Reading grade above the ceiling.** Flesch-Kincaid, default 12, `WEBSTER_READING_GRADE` to
  change it. Set it to 10 when the reader is the non-technical person `house-rules` section 6
  describes. The grade is driven by sentence length more than by vocabulary.

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
| Understandability | Measured: reading grade against the stated reader |
| Conciseness | Measured: sentence length drives the grade |
| Consistency | Measured in part: one term for one thing |
| Correctness | Measured by `drift.py`, which resolves every cited anchor |
| Usability | A person judges whether a reader can find and apply it |
| Clarity | A person judges it. `courseware:learner` is the closest thing to a test |
| Minimalism | A person judges whether anything here is unnecessary |

Do not claim conformance to 26514. It is a purchased standard aimed at organisations with a
documentation function, and what is implemented here is its quality model rather than its
process requirements. Saying "checked against the 26514 quality characteristics" is true.
Saying "26514 conformant" is not.
