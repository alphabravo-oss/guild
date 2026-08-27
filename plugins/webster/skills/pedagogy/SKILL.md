---
name: pedagogy
description: How to write a page for somebody who has not used the product before: stated outcome, prerequisites up front, a concept introduced before it is used, and the error anticipated. Applies to every page written for a user, not only to tutorials. Load before writing any page below developer audience.
---

# Pedagogy

The best writing instructions in this plugin were vendored with the tutorial agent, and then
fenced off where almost nothing could reach them. In the last full run, 3 pages of 67 were
`doc_type: tutorial`. The other 64 got none of it.

**That was the wrong boundary.** Teaching is not a genre of page. It is what any page owes a
reader who has not used the product before, and that reader is on the how-to and the
troubleshooting page too.

So these rules apply to **every page whose audience is `user`**, whatever its `doc_type`, and to
`operator` pages wherever the operator is new to the product rather than new to terminals.

---

## The four that matter

### 1. Say what the reader will end up with, first

Not what the page covers. What they will have when they close it.

- Weak: "This page covers the deployment wizard."
- Real: "By the end of this you will have one cluster running, and you will know which screen
  tells you it is ready."

For a tutorial `doctype.py` checks the literal "By the end of this tutorial you will be able to"
because Good Docs is emphatic about it. On other types the wording is free and the obligation is
not: the first paragraph tells the reader whether this page is for them.

### 2. Everything they need, before step one

A reader who reaches step 4 and finds they needed credentials has been failed, and they have been
failed in a specific way: they now distrust the rest of the page.

State the lot up front: access, accounts, a thing installed, a decision already made. If nothing
is needed, say that too. "You do not need an account for this" is a real sentence that saves a
real reader a real detour.

### 3. A concept is introduced before it is used

The single most common way documentation assumes knowledge of the product it is documenting. A
term appears on page one as though the reader has already met it, because the writer met it in the
source an hour earlier.

- Expand an acronym the first time. Once. `doctype.py` checks this part.
- Introduce a product word the first time it carries weight, in one clause, not a paragraph.
- A term needing more than a clause belongs in the glossary, and the page links it.
- A whole concept needing to be settled before the task makes sense belongs on an explanation
  page, and the task page links that rather than teaching it inline.

The order to write in is the order the reader meets things, which is rarely the order the code is
organised in.

### 4. Anticipate the error, on the page where it happens

Not in a troubleshooting appendix nobody reaches. Beside the step that fails.

`survey.py` returns the product's own error strings under `user_surface.messages`. Use the exact
wording, because the reader is searching for the exact wording:

> If it says **Credentials could not be verified**, the key was pasted with a trailing newline.

## Shape, which is most of readability

The complaint that documentation is hard to read is usually not about vocabulary. It is about
having to hold four things in your head to get through a paragraph.

- **One idea per section.** If a section needs "and also", it is two sections.
- **The action first.** A step opens with what to do, then what happens, then why. A reader
  scanning for the next thing to type should find it at the start of the line.
- **Short paragraphs.** Three or four lines. A screen of unbroken text is skipped, and then the
  sentence you needed them to read is the one they skipped.
- **Bold the thing they look for on screen**, so the page and the interface can be scanned
  together. **Deployments**, then **New**.
- **A list when it is a list, prose when it is an argument.** Bulleting an argument removes the
  connective tissue that made it an argument.
- **Stop when the reader can do the thing.** Length is not thoroughness. Long documentation
  invents more than short documentation does.

## What not to do, which the vendored agent got wrong

Kept from the agent, because it is good: progressive disclosure, prerequisites stated up front,
concepts before use, errors anticipated, examples that run.

Dropped, because it is not:

| The agent says | Why not |
| --- | --- |
| Close with a Summary | It restates what the reader just read. `slop.py` flags it |
| Include intentional errors to teach debugging | Only if labelled as deliberately broken. An unlabelled wrong example is a wrong example |
| Show both correct and incorrect approaches | Same rule. Run the correct one, label the other |
| Link to working code repositories | Only if the link resolves today |
| "Understanding how developers learn" | Most readers of a user page are not developers. That framing is how a user page ends up written for a developer |

## The check

Reading your own page cannot find a comprehension defect, because you already know the answer.
Two things stand in for that:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/doctype.py check docs
```

catches the mechanical half: an unexpanded acronym, a tutorial with no stated outcome, steps with
no prerequisites, prose above the reader's grade.

The other half needs somebody who does not already know. If `courseware:learner` is installed,
dispatch its redline pass. If it is not, the mechanical half still ran and is reported as such:
the Readable gate is `partial`, not `not_checked` and not a pass.
