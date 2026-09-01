---
name: house-rules
description: The bar every page written by this plugin is held to, evidence rules, the four uncertainty tags, the voice rules, and the gates a page must clear before it ships. Load before writing or auditing any documentation page.
---

# House rules

The four plugins this replaces could all write documentation. None of them could tell you
which sentences were true. These are the rules that close that gap.

---

## 1. The governing rule

**A sentence about behaviour needs a source, or it needs a tag.** There is no third option
and no exemption for sentences that are obviously true.

A source is one of:

- a `file:line` anchor that resolves in the current tree
- a command plus the output it actually produced
- a named test that covers the behaviour
- **the screen, for a claim about what a reader sees.** "The Deployments page has a New button"
  is checked by looking at it, and the anchor is the component that renders it or a screenshot
  in the page. This class exists because without it every sentence on a user page has to be
  justified against source code, and a writer who spends the day in source writes about source.
  `survey.py` returns the product's own on-screen wording under `user_surface`

Write the anchor down as you go. Reconstructing sources afterwards does not work, because by
then you no longer remember which sentences you checked and which ones you assumed.

## 2. Working notation, and where it stops

While drafting, mark what you have not verified. Four tags, borrowed from HADS:

```
[SPEC]   Authoritative. Sourced. Terse.
[NOTE]   Human context, history, reasoning. Not a behavioural claim.
[BUG]    A verified failure. Requires symptom, cause, fix.
[?]      Inferred, not verified. Says what would settle it.
```

**None of it ships.** These tags are for the draft and for the plan. A reader who opens a
product's documentation and finds `[?]` in the middle of a sentence is being shown the tooling
that produced the page, and no product does that.

The honesty the tag carries is not optional, only the notation is. Before publishing, every `[?]`
resolves one of three ways:

1. **Go and check it.** Then it is a plain sentence.
2. **Say it in ordinary words.** "This has not been tested against a live server" is honest,
   readable, and belongs on the page.
3. **Cut the sentence.** Silence is better than a claim nobody stands behind.

Never promote a `[?]` to fact by rewriting it to sound more confident. That is not checking.

Same rule for the anchors. A claim about behaviour is checked against a `file:line`, and that
anchor lives in an HTML comment beside the claim or in a frontmatter `sources:` list, where
`drift.py` reads it:

```markdown
The scan stops after 40 requests. <!-- src/lib/net.ts:9 -->
```

The reader sees the sentence. The anchor exists so the sentence can be re-checked a year later,
which is a job for the tooling and not for them.

## 3. What a page may not claim

- **Never say a thing works if no one ran it.** "The live adapter is written against the
  documented request shapes and has never held a real token" is a real sentence and a useful one.
- **Never describe a capability the tests do not cover** without marking it `[?]`.
- **Never document an aspiration in the present tense.** Planned is not shipped.
- **Never let an example go in that has not been run.** Every fenced block is either executed
  or labelled as illustrative.

## 4. The mandatory pages

Two pages are not optional, whatever the repo is.

1. **What this is, in one sentence**, written for someone who has never heard of it, at the top,
   above any badge or diagram.
2. **What this deliberately does not do.** The scope you refused, and why. This is the page that
   makes the rest of the documentation believable, and it is the one people skip.

## 5. Voice

- Sentence case for headings and buttons. Not title case.
- No em dashes. Restructure the sentence.
- Banned: unlock, elevate, leverage, seamless, robust, cutting-edge, revolutionize, empower,
  supercharge, game-changing, effortless, next-level, "in today's fast-paced world",
  "in an era where".
- Banned constructions: "it's not just X, it's Y", "Whether you're a ... or a ...", generic CTAs.
- Cut any clause that can be cut without losing meaning.
- Write as one specific person explaining a thing to one specific reader. Not marketing aimed at
  everyone, not a committee.

## 6. Who the reader is, and what that lets the page be about

**A set of documentation serves more than one reader, and a page serves exactly one.**

Every page declares its reader in frontmatter, the same way it declares its type. A page that
declares no reader is a defect, not a note. That was the wrong way round until it was measured:
in the last full run of this plugin, none of the 67 pages declared an audience, so every rule
that depended on one was inert.

```yaml
doc_type: how-to
audience: operator
```

### The audience is a lens before it is a reading grade

This is the part that used to be missing, and it is the reason a page could pass every gate and
still be written for the wrong person. `audience` used to buy one thing, a Flesch-Kincaid
ceiling. Short sentences about the wrong subject still cleared it.

**A page may name only the things its reader can touch.**

| Audience | Who they are | May name | May not name | Grade |
| --- | --- | --- | --- | --- |
| `user` | Uses the product. No development, sysadmin or DevOps background | Screens, buttons, fields, menus, their own files, what the product gives back and what it says when something fails | Symbols, routes, environment variables, architecture | 10 |
| `operator` | Installs, configures and runs it | The above, plus commands, flags, config files, variables, ports, logs | Symbols, architecture | 13 |
| `developer` | Builds against it or contributes to it | Anything in the system | nothing | 15 |

`doctype.py check` enforces both halves. The `reader-lens` skill carries the full rule, how to
write from the product's own on-screen vocabulary, and where internals go instead.

`user` is the default, and it is the right default: most people reading documentation are trying
to use the thing rather than run or extend it.

### Which sections serve which reader

| Section | Reader |
| --- | --- |
| `index.md`, `faq.md`, `getting-started/` | `user` |
| `<subject>/` | `user` unless the product is itself a tool for operators or developers |
| `troubleshooting/` | `user`, because that is who hits an error first |
| `install/`, `advanced/` | `operator` |
| `api/`, `developer/` | `developer` |

`scaffold.py check` reports a page whose declared audience disagrees with its section. When a page
turns out to be about something its reader cannot touch, **the fix is to move the page**. Editing
the `audience:` field until the checker goes quiet is the way this rule is defeated, and it is
worth naming because it is the cheapest thing to do and it is always wrong.

### Naming the reader concretely

The three audiences set the lens and the grade. They do not excuse you from describing the actual
person in the plan: not "developers" but "the person who built this with an AI coding tool,
technical enough to deploy and not technical enough to read a bundle".

### The rule that governs all of it

Domain vocabulary is not jargon; **undefined** domain vocabulary is. A page about HTTP has to say
"header". Judge the introduction of a term, not its existence. What changes with the audience is
how much gets introduced: a `user` page expands an acronym on first use, a `developer` page does
not. `doctype.py` checks the acronyms.

## 7. Precedence over the vendored agents

The five agents were vendored from `documentation-generation`. Their pedagogy is the best writing
instruction in this plugin and it now lives in the `pedagogy` skill, which **every page below
`developer` audience loads**, not only tutorials. Confining it to `doc_type: tutorial` meant 3
pages of 67 got it and the rest got nothing.

What survives from the agent files, and is now in `pedagogy`: stated outcome first, prerequisites
before step one, a concept introduced before it is used, the error anticipated beside the step
that fails.

What does not, because it predates these rules:

| The agent says | Do this instead |
| --- | --- |
| `architect`: comprehensive documents, 10 to 100+ pages | Write what is true and stop. Long documentation invents more than short documentation does |
| `architect`: open with an executive summary | Open with what the thing is, in one sentence |
| `tutorial`: close with a Summary section | Cut it. `slop.py` flags it |
| `tutorial`: intentional errors, and both correct and incorrect approaches | Allowed, labelled as deliberately broken. An unlabelled wrong example is a wrong example |
| `tutorial`: link to working code repositories | Only if the link resolves today |
| `tutorial`: "how developers learn" | Most readers of a `user` page are not developers, and that framing is how a user page ends up written for one |
| all three: "Remember: your goal is to create documentation that serves as..." | Ignore the closing exhortation. `slop.py` flags `copula-avoidance` on it |

Their own prose is title case throughout, which these rules forbid. Write headings in sentence
case regardless of what the agent file looks like.

## 8. The gates

**The bar is documentation somebody can hand to a customer without reading it first.** That is
what every rule here is for, and it changes what a soft verdict means. `not_checked` and
`partial` are honest reports, and honesty only buys something when a person reads the report. If
nobody does, an unchecked gate is an unchecked page in front of a customer. So a soft verdict is
not a smaller pass. It is the list of what shipped on trust, and it is the first thing to say.

A page is not finished until all seven pass. Report the ones that did not, with the reason.

| Gate | Passes when |
| --- | --- |
| Sourced | Every behavioural claim has a resolving anchor, in a comment or in frontmatter |
| Runnable | Every non-illustrative example has been executed |
| Shaped | `scaffold.py check` and `doctype.py check` both pass: the tree is right and no page is doing another type's job |
| Lens | Every page declares a reader, and names nothing that reader cannot touch |
| Builds | The site compiles with `onBrokenLinks: 'throw'`, and `rendered.py` finds nothing internal on the rendered pages |
| Readable | `prose.py check` and the mechanical half of `doctype.py check` are clean, and somebody who did not already know the answer got through it |
| Honest | The two §4 pages exist and the page claims nothing from §3 |

**Builds is the gate the other six cannot stand in for.** They all read markdown as text and
none of them compiles it, so a documentation set can pass every checker and still deliver a
reader nothing. Invalid YAML in a frontmatter `description`, a stray closing tag at the end of a
page, and a link written as an absolute path have each taken a whole site down while the
checkers stayed green. Where there is no site to build, report it `not_checked`.

**A build that warns is not a build that passes.** The scaffold set `onBrokenLinks: 'warn'`,
so a dead internal link printed a warning, exited 0, printed `[SUCCESS]`, and shipped the 404.
This table said the gate passes when the site "reports no broken links", and it reported them
and passed anyway. It is `throw` now, which is Docusaurus's own default. Never lower it: the
reader is the one who finds a dead link, and on a set nobody proofreads they find it first.

**Compiling is half of it. Reading the result is the other half.** `rendered.py` scans the built
HTML for anything internal that reached the reader: a visible `file:line`, a source path, a
working-note tag, a frontmatter key printed as text. This matters because the anchor rule in §1
depends on a comment rendering to nothing, and that was assumed rather than checked. It holds on
Docusaurus 3, verified on a real build. `rendered.py` is what keeps it true.

**Sourced cannot pass on a set with no anchors.** `drift.py check` returns `no_anchors` and exits
2 when a recorded set has pages but cites no sources. It used to return `clean`, because every
anchor it had resolved, and a set with none has none to break. That was a false pass on the gate
that matters most.

**Grade is not readability, and it was pointing the wrong way.** The Flesch-Kincaid ceiling in
`doctype.py` is a statistic over word and sentence length, so it rewards monosyllables and
charges for naming the product. Measured on two pages held to the same ceiling of 10: an
unreadable one built out of "It is set by it. The setting of it is done by the system." scored
**-0.2** and passed, and "Open the Deployments page and choose New deployment, then pick the
cloud provider you already have a credential for" scored **10.2** and was flagged. The formula
was pushing away from the writing `reader-lens` asks for.

`prose.py` measures shape instead, which is what decides whether a page can be got through: a
sentence the reader has to hold open, a paragraph with nowhere to land, a run of text with no
heading to break it, and the two constructions that hide who does the thing. Thresholds scale
with the audience on the same grounds the grade ceiling does. Grade stays as a weak floor and is
no longer the thing called readable.

**Readable no longer has a single point of failure.** It used to be delegated whole to
`courseware:learner`, which is not installed in most repos, so it reported `not_checked`
permanently and the one gate that would catch a page a person cannot follow never ran once. It
is now two halves, and both belong to this plugin.

The mechanical half is `prose.py` and the readable rules in `doctype.py`: sentence and section
length against the audience, unexpanded acronyms, a tutorial with no stated outcome, steps with
no prerequisites.

The other half is `webster-reader`, one agent per page. It is given the page and the declared
audience and nothing else, and it reports where the page stopped it, in a closed vocabulary.
**Its value is entirely in what it does not know.** You cannot run this half yourself and you
cannot brief it: you have the source and the plan, so you fill every gap on the page from your
own head before you see that it is a gap. That is what knowing the answer does to a reviewer,
and it is why a set of documentation can pass every other gate and still fail the first person
who opens it.

Report `partial` when only the mechanical half ran or only some pages were read, and say which.

**A gate that could not be run reports `not_checked` with a reason. It never reports a pass.**
A false pass is the worst outcome this plugin can produce, because the reader trusts the page
precisely to the extent that they believe the gates ran.
