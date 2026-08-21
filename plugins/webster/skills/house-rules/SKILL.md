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

## 2. The four tags

Borrowed from HADS, because uncertainty has to be structural. Tone will not carry it.

```
[SPEC]   Authoritative. Sourced. Terse.
[NOTE]   Human context, history, reasoning. Not a behavioural claim.
[BUG]    A verified failure. Requires symptom, cause, fix.
[?]      Inferred, not verified. Says what would settle it.
```

A `[?]` block is a success, not a failure. It is the page telling the reader exactly how far
the writer got. A page with three honest `[?]` blocks is worth more than a confident page with
three wrong sentences in it, because the reader can act on the first and is misled by the second.

**Never promote `[?]` to `[SPEC]` without going and checking.** Rewriting the sentence to sound
more certain is not checking.

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

Fix this person before writing a word, and write it down in the plan. Vagueness about the reader
produces documentation that serves nobody.

The default, unless the repo says otherwise: **an average adult of average intelligence with a
real reason to be here.** General computer literacy. No development, sysadmin or DevOps
background. Reads comfortably at about an 8th to 10th grade level.

The corollary cuts both ways. Domain vocabulary is not jargon; **undefined** domain vocabulary is.
A page about HTTP has to say "header". Judge the introduction of a term, not its existence.

## 7. The gates

A page is not finished until all six pass. Report the ones that did not, with the reason.

| Gate | Passes when |
| --- | --- |
| Sourced | Every behavioural claim has a resolving anchor or a `[?]` tag |
| Runnable | Every non-illustrative example has been executed |
| Shaped | The page is one of tutorial, how-to, reference, explanation, and does not drift between them |
| Readable | A reader matching §6 could follow it without stopping |
| Honest | The two §4 pages exist and the page claims nothing from §3 |

**A gate that could not be run reports `not_checked` with a reason. It never reports a pass.**
A false pass is the worst outcome this plugin can produce, because the reader trusts the page
precisely to the extent that they believe the gates ran.
