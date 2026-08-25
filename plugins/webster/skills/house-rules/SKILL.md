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

## 6. Who the reader is

**A set of documentation serves more than one reader, and a page serves exactly one.** Getting
this wrong is the failure that produces an installation page pitched at somebody who has never
opened a terminal, and a getting-started page that assumes they have.

Every page declares its reader in frontmatter, the same way it declares its type:

```yaml
doc_type: how-to
audience: operator
```

### The three readers

| Audience | Who they are | Assumes | Reading grade |
| --- | --- | --- | --- |
| `user` | Uses the product. No development, sysadmin or DevOps background | General computer literacy and a real reason to be here | 10 |
| `operator` | Installs, configures and runs it | A terminal, a package manager, environment variables, a hosting dashboard | 13 |
| `developer` | Builds against it or contributes to it | The language, the toolchain, and how to read source | 15 |

`user` is the default, and it is the right default: most people reading documentation are trying
to use the thing rather than run or extend it. A page that does not declare an audience is read
against `user`, which will report a page written for the other two as too dense. That is the
correct failure, because it forces the declaration rather than silently lowering the bar.

### Which sections serve which reader

The layout in `structure` already splits them, so the mapping is fixed rather than decided per
page:

| Section | Reader |
| --- | --- |
| `index.md`, `faq.md`, `getting-started/` | `user` |
| `<subject>/` | `user` unless the product is itself a tool for operators or developers |
| `troubleshooting/` | `user`, because that is who hits an error first |
| `install/`, `advanced/` | `operator` |
| `api/`, `developer/` | `developer` |

`scaffold.py check` reports a page whose declared audience disagrees with its section.

### Naming the reader concretely

The three audiences set the reading grade. They do not excuse you from describing the actual
person in the plan, and the plan is where that happens: not "developers" but "the person who
built this with an AI coding tool, technical enough to deploy and not technical enough to read a
bundle". Vagueness about the reader produces documentation that serves nobody.

### The rule that governs all of it

Domain vocabulary is not jargon; **undefined** domain vocabulary is. A page about HTTP has to say
"header". Judge the introduction of a term, not its existence. What changes with the audience is
how much gets introduced: a `user` page defines "environment variable", a `developer` page does
not.

## 7. Precedence over the vendored agents

The five agents were vendored from `documentation-generation` and their pedagogy is worth
keeping: progressive disclosure, prerequisites stated up front, concepts introduced before they
are used, common errors anticipated. Follow all of that.

Their instructions also predate these rules and contradict them in six specific places. **These
rules win. Every time.** Precedence is not left to judgement, because a model reading two
documents that disagree will follow whichever it read last.

| The agent says | Do this instead |
| --- | --- |
| `architect`: comprehensive documents, 10 to 100+ pages | Write what is true and stop. Long documentation invents more than short documentation does |
| `architect`: open with an executive summary | Open with what the thing is, in one sentence. A summary of a page nobody has read yet is padding |
| `tutorial`: close with a Summary section | Cut it. It restates what the reader just read, and `slop.py` flags it |
| `tutorial`: fail forward, include intentional errors | Allowed, but the broken example is labelled as deliberately broken. An unlabelled wrong example is a wrong example |
| `tutorial`: show both correct and incorrect approaches | Same rule. Run the correct one, label the incorrect one |
| `tutorial`: link to working code repositories | Only if the link resolves today. A dead pointer makes a page look explained when it is not |
| all three: "Remember: your goal is to create documentation that serves as..." | Ignore the closing exhortation. It is press-release cadence and `slop.py` flags `copula-avoidance` on it |

Their own prose is title case throughout, which these rules forbid. That is their prose, not
yours. Write headings in sentence case regardless of what the agent file looks like.

## 8. The gates

A page is not finished until all six pass. Report the ones that did not, with the reason.

| Gate | Passes when |
| --- | --- |
| Sourced | Every behavioural claim has a resolving anchor, in a comment or in frontmatter |
| Runnable | Every non-illustrative example has been executed |
| Shaped | `scaffold.py check` and `doctype.py check` both pass: the tree is right and no page is doing another type's job |
| Readable | A reader matching §6 could follow it without stopping |
| Honest | The two §4 pages exist and the page claims nothing from §3 |

**A gate that could not be run reports `not_checked` with a reason. It never reports a pass.**
A false pass is the worst outcome this plugin can produce, because the reader trusts the page
precisely to the extent that they believe the gates ran.
