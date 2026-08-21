---
name: no-slop
description: The tells that make documentation, diagrams and a docs site read as machine-made, and what to do about each. Load before writing any page, drawing any diagram, or building a docs site.
---

# No slop

Rules adapted from the redpen corpus (`rules/copy.yaml`, `rules/residue.yaml`), retargeted from
rendered HTML to markdown, plus tells specific to documentation and to generated diagrams.

Run the detector rather than eyeballing it:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/slop.py docs
```

Exit 1 when anything at high severity fires. Prose rules skip fenced code; diagram rules run only
inside `mermaid`, `d2` and `dot` fences.

## Prose

The high severity ones are the ones a reader names out loud.

| Tell | Why it fails |
| --- | --- |
| Marketing buzzwords | A superlative that survives a find and replace of the product name is saying nothing about the product |
| Em dashes | The single most named tell in AI prose. Restructure the sentence |
| "Not just X, but Y" | The shape a model falls into when a sentence needs to sound important |
| "Whether you are a ... or a ..." | An audience defined so widely it names nobody |
| Moreover, Furthermore, In conclusion | Connectives marking a connection the sentences do not have |
| Invented metrics | A number with no source is a liability, not just a tell |
| Hedged claims | "Designed to help improve" tells the reader you are not sure it does |
| Press-release verbs | "Serves as", "boasts", "represents" standing in for "is" |
| Everything in threes | One tricolon is a device. By the fourth the reader hears the rhythm instead of the claim |

## Residue

Worse than slop, because it is a claim the reader cannot check.

- **Agent attribution.** A byline naming the tool that wrote it. It says nothing about the
  content and outlives its accuracy the first time somebody edits underneath it.
- **Conversation artifacts.** "Here's the updated function", "I've added error handling". These
  are replies, written to someone who asked. Committed, they address a reader who was not there.
- **Orchestration markers.** A phase or task id. The next reader has no phase and no way to look
  it up, so the sentence explains the page with a fact they cannot check.
- **Dead scaffold references.** A pointer to a plan or handoff file written for one run and
  deleted after it. The page appears to be explained somewhere and is not.

## Markdown

- **Emoji in a heading** is the loudest single markdown tell. Emoji as bullet icons is the second.
- **Title case headings.** Sentence case, unless there is a specific reason.
- **A Conclusion or Summary section** restates what the reader just read. Cut it.
- **Every bullet opening with a bold label** is a template rather than a list. Fine once, a tic
  by the fifth. The detector only fires at five in one file, because the pattern is legitimate
  for a short index.
- **Tables where every cell is the same shape.** A real table has rows of different lengths and
  at least one that does not fit.

## Diagrams

Diagrams are where slop hides, because nobody proofreads a picture.

- **The default mermaid palette.** `#f9f`, `#bbf` and friends belong to no design system. Use no
  colour at all, or colours that carry meaning and are stated in a key.
- **AI purple**, `#8b5cf6` and its neighbours. The second most named visual tell there is.
- **Nodes named after their category.** `[Data]`, `[Service]`, `[Handler]`, `[Layer]`. A node
  named after its category shows nothing. Name the actual module, endpoint or file.
- **Left to right, every time.** A diagram whose shape is the default of the tool that drew it
  is decoration. If the flow is a cycle, draw a cycle.
- **A diagram that restates the list above it.** Draw the thing prose cannot carry: a shape, a
  cycle, a fan out. If prose said it already, cut the diagram.

Every node in a diagram is a claim, so anchor it. A box labelled `POST /api/scan` should be a
route that `scripts/survey.py` found, not one that seemed likely.

## A docs site

When the docs get published as a site rather than read in the repo, the site is a UI and
`redpen` already judges UIs against 151 rules with a real browser. Run it rather than
reimplementing it here:

```bash
redpen scan ./site --url http://localhost:3000
```

If redpen is not installed, say so and mark the finding `not_checked`. Do not eyeball a design
and call it a pass.

## The rule behind the rules

Every tell above has the same root: a default that costs nothing to produce. The fix is never to
swap one default for another. It is to make a choice the page could not have arrived at by
itself, and to be able to say why.
