---
name: reader-lens
description: What a page is allowed to be ABOUT, which is a different question from how hard its sentences are. The rule that keeps user documentation about the product instead of about the code. Load before writing or auditing any page.
---

# Reader lens

`audience` used to buy one thing: a reading grade. That is not enough, and the gap is visible in
the output. A page can describe a status enum in short words and pass at grade 9:

> A deployment's status is the field everything else in the UI hangs off. This page is about how
> that field moves. It is also about the sub-phase field underneath it.

Nothing there is untrue, nothing is long-winded, and it is not documentation. It is a developer's
model of the system handed to somebody who wanted to know whether their cluster was ready.

**So the audience is a lens before it is a grade.** It decides subject matter first and sentence
length second.

---

## The rule

**A page may name only the things its reader can touch.**

| Audience | May name | May not name |
| --- | --- | --- |
| `user` | Screens, buttons, fields, menus, files they have, what the product gives back, what it says when something fails | Symbols, routes, environment variables, architecture |
| `operator` | All of the above, plus commands, flags, config files, environment variables, ports, logs | Symbols and architecture |
| `developer` | Anything in the system | nothing |

`doctype.py check` enforces this. On a page below `developer` it is a defect to name a symbol in
backticks, a request route, or a part of the architecture, and on a `user` page an environment
variable as well. A page that declares no reader is itself a defect: in the last full run of this
plugin before the rule existed, not one of 67 pages declared an audience, so the lens never fired.

## Writing from the screen

The reliable move is mechanical. **Before writing a user page, get the words the product prints
on itself.** `survey.py` returns them under `user_surface`:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/survey.py . | python3 -c "
import json,sys; u=json.load(sys.stdin)['user_surface']
[print(k, len(v)) for k,v in u.items()]"
```

- `screens`, the pages a person can be on
- `labels`, the text on buttons, headings, tabs and form fields
- `messages`, what the product says when something goes wrong
- `commands`, the subcommands somebody types

Write with those words. A page that says "open Deployments, then New" matches what the reader is
looking at. A page that says "issue a create request" does not, and no amount of shortening the
sentence fixes it.

**The labels are also the vocabulary test.** If the documentation calls something by a name the
interface never uses, one of the two is wrong and it is worth finding out which before writing
another forty pages on top of it.

## Where the internals actually go

They are not banned from the documentation set. They are banned from the wrong page.

- Architecture, request shapes, symbol names → `developer/`, `audience: developer`
- Commands, config, variables, ports → `install/` and `advanced/`, `audience: operator`
- Everything a person does with the product → the subject directories, `audience: user`

Moving a page is the fix. Editing the `audience:` field to make a checker stop complaining is
not, and it is the failure this rule is easiest to defeat with.

## Explanation, which is where this leaks first

`explanation` is the type that quietly absorbs internals, because "why it is built this way"
sounds like permission to describe how it is built. In the last full run, 18 of 67 pages were
explanation and the worst of them explained a state machine to somebody who cannot see it.

**An explanation page for a `user` exists to settle a choice they actually face.** Which
deployment type do I pick. Why did my number come out different from my neighbour's. Whether the
estimate is safe to spend money on.

If the page explains a mechanism the reader cannot act on, it is a developer page in the wrong
directory. `doctype.py` tests this crudely and effectively: a `user` explanation page that barely
addresses the reader is not written for them.

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/doctype.py template explanation user
```

## When a word is domain vocabulary rather than a leak

One product's internals are another product's ordinary vocabulary. A Kubernetes tool's operators
genuinely handle controllers, and reporting that as a leak would be wrong.

```bash
WEBSTER_LENS_ALLOW="controller,controllers,reconciler" \
  python3 ${CLAUDE_PLUGIN_ROOT}/scripts/doctype.py check docs
```

Record the list in the plan with a line on why each term belongs to the reader, and keep it
short. It is a declaration, not a default, because the easy way to defeat this rule is to widen
it until nothing fires. A term goes on the list when the product's own interface uses it in front
of that reader, not when removing it would be inconvenient.

## Jargon, which is the same failure one level down

Naming a symbol assumes the reader knows the code. Using an unexpanded acronym assumes they know
the field. Both are the page skipping an introduction it owes.

Domain vocabulary is not jargon; **undefined** domain vocabulary is. A page about food plots says
"forage". A page about clusters says "node". What changes with the audience is how much gets
introduced.

The checker takes the mechanical part: an acronym expanded nowhere on the page and absent from the
glossary is a defect on a `user` page and an advisory elsewhere. Expanding it once, or adding it
to `getting-started/glossary.md`, clears it.

## Reading the output

`doctype.py check` groups findings by rule with a count, because the first run over a real docs
set produces a lot of them and a flat list is unreadable. `WEBSTER_SHOW_PER_RULE` changes how
many examples of each are printed.

**A large `wrong-lens` count almost always means the audiences are wrong, not the prose.** Fix
the declarations first, then look again. Measured over one real 67-page set: 1,115 defects before
any page declared a reader, and 41 once each page declared the right one. The volume was a
missing declaration, not an unusable rule.

## What this does not cover

The lens catches the page being about the wrong thing. It does not catch the page being about the
right thing badly. That is `pedagogy`, and the two are checked separately because they fail
separately.
