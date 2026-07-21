# holmes

A **design-quality / cohesion review** for Claude Code that reads the codebase like a detective — deducing the real design problem from the detail that's out of place. The senior-engineer critique, not bug-hunting or lint:

> Is this shaped right, or thrown together? Why are there N functions doing almost-the-same thing? Why so many packages? Where could this logic have been shared? Does the architecture flow, or fight you? Is it deliberate or accreted?

— and it **lists the proposed solutions**.

```
/holmes:review <target>
```

`<target>` is free-form: a path (`pkg/auth`), a change (`the diff`, `this branch vs main`), a subsystem name, or a description (`the retry logic in the worker`). Omit it and you'll be asked.

It is **report only** — it produces a verdict and a reshaping plan, and never edits code.

## Why you can trust it

A naive "review the architecture" pass produces plausible-sounding opinions you can't act on. Holmes is a deterministic Workflow engine built around four mechanisms:

| Worry | Mechanism |
|---|---|
| It hallucinates / cites things that aren't there | **Grounded map first** — a structural model (modules, boundaries, intended shape) that every finding must anchor to with `file:line` |
| It misses whole areas | **7 blind lenses** — separate agents, each hunting one class of design smell, none seeing the others |
| It reports smells that are actually intentional | **Adversarial verify** — every candidate goes to a skeptic that tries to *refute* it; non-survivors are dropped |
| I have to keep prodding it | **Completeness critic** — a final agent asks "what went unexamined?" and re-lenses the gaps |

And, like its namesake: **everybody lies.** Holmes never trusts a comment to tell him what the code does — he reads the actual code and traces the call sites.

### The seven lenses

1. **Package / module proliferation** — arbitrary boundaries; collapsible modules; a module that's secretly three things.
2. **Missed sharing & reuse** — logic reimplemented instead of shared; a missing seam.
3. **Helper sprawl & abstraction fit** — one-off wrappers that hide nothing; over- or under-abstraction.
4. **Flow & layering** — does control/data flow cleanly, or zigzag? Are dependencies pointed the right way?
5. **Cohesion** — does each module do one thing, or is it a grab-bag?
6. **Naming & structural consistency** — similar things that don't look similar; half-finished migrations.
7. **Accretion markers** — v1/v2 side-by-side, dead flags, vestigial layers — grew without pruning.

## Output

A debrief in chat plus a saved report under `.holmes/reviews/`. Per subsystem: a `deliberate | mixed | accreted` verdict and the design story; per finding: file:line evidence and a concrete proposed solution; then an ordered, highest-leverage-first reshaping plan.

An empty result is honest — a well-shaped target should produce few or no findings.

## How it's built

- `commands/review.md` — thin orchestrator: scopes the target, runs the engine, renders the debrief. Does not review code itself.
- `commands/help.md` — `/holmes:help`.
- `workflows/review.js` — the engine. A Workflow script: `map → lenses (parallel, blind) → verify (per-finding, adversarial) → critic (re-lens gaps) → synthesize`. Pure-literal `meta`, schema-validated agent returns, pipeline (no needless barriers), `.filter(Boolean)` after fan-out — authored to the weave rulebook.

Requires the Workflow tool. The `/holmes:review` command is itself the opt-in.
