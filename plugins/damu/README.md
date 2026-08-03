<p align="center">
  <b>damu</b> — De-AI My UI — strips the tells that make an interface read as AI-made.<br/>
  <i>~19 slop signatures, one catalog, two modes.</i>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/damu-0.2.0-00897B?style=flat-square" alt="damu 0.2.0"/>
  <img src="https://img.shields.io/badge/guild-review%20only-00897B?style=flat-square" alt="guild reviewer"/>
  <img src="https://img.shields.io/badge/Claude%20Code-plugin-8E44AD?style=flat-square" alt="Claude Code plugin"/>
  <img src="https://img.shields.io/badge/license-MIT-2E7D32?style=flat-square" alt="MIT license"/>
</p>

<p align="center">
  <a href="../../README.md">← back to the Guild marketplace</a>
</p>

---

AI loves the same UI flavor, every time: 4–5 competing fonts, purple-on-near-black, a parallax hero
that means nothing, neon gradient borders, the same rocket/bolt/shield icons recycled like Pokémon
evolutions, every section a 24px card with zero soul, filler copy that says nothing, out-of-box shadcn.
It smells like "made in Tailwind with love" before the DOM even loads.

**damu** strips those tells. One catalog of ~19 slop signatures, two modes.

## Install

In the `guild` marketplace. Add the marketplace, install `damu`. `remediate` needs the Playwright
MCP — if you don't have it, run `/e2e:init` (or add a `playwright` server to `.mcp.json`) and restart.

## Modes

### `/damu:prevent` — stop slop before it's generated

Prints a copy-pasteable anti-slop **design ruleset** — the catalog's fixes as imperatives, lightly
tailored to your stack (Tailwind/shadcn rules foregrounded when detected). You adjust it and drop it
into `CLAUDE.md`, your global `~/.claude/CLAUDE.md`, or a memory file. It writes nothing — it's yours
to place.

```
/damu:prevent kids' learning app, playful, should be colorful and rounded
```

The context keeps the rules from forbidding what your product legitimately wants.

### `/damu:remediate` — fix a UI you already built

Drives Playwright to capture per-route **screenshots + extracted CSS facts** (distinct font count,
color clusters, border-radius / shadow distributions, gradient and parallax signals, shadcn/Tailwind-
default signatures), then runs a Workflow engine that judges them and renders a ranked, source-anchored
change list.

```
/damu:remediate http://localhost:3000
/damu:remediate http://localhost:3000 --apply           # commit the safe fixes
/damu:remediate --routes=/,/pricing,/app --headed
```

`--apply` (clean git tree required) commits only **HIGH-confidence, low-risk** fixes — one atomic
commit each — and leaves every subjective call to you. Copy is never auto-rewritten.

## The one rule that governs everything

Every tell is *sometimes* correct. A dashboard **is** card-based; a kids' app **should** be rounded and
emoji-rich; a music brand **may** earn neon. The slop isn't the pattern — it's the pattern applied by
default, uniformly, with no reason. So damu refutes every flag as possibly-intentional before keeping
it, and defaults to keeping a choice when genuinely unsure. A false flag is worse than a miss.

## How remediate works

Over captured artifacts (never the live browser, so analysis fans out cleanly):

1. **Context** — what is this product, who's it for, what's legitimately fine here (the lenses treat
   those as hard exclusions).
2. **9 blind lenses** — typography · color · gradients & borders · shape & depth · layout soul ·
   motion · iconography · copy · readability — each grounded in a fact signal *and* the screenshot, and
   each the sole judge of its tells: it self-rates confidence (HIGH only when a choice is clearly
   unmotivated *and* uniform), so nothing gets silently dropped — a weak finding just ranks low.
3. **Completeness critic** — uncovered tells, glanced-over pages, whole-page soullessness.
4. **Synthesis** — per-page deliberate / mixed / slop verdict + ranked fixes tagged by confidence,
   risk, and whether they're safe to auto-apply.

Report + artifacts land under `.damu/runs/<timestamp>/`.

## The catalog

The ~19 tells (`SLOP-01`…`SLOP-19`) live in `skills/slop-catalog/SKILL.md` — each with what to look
for, why it reads AI, the fix, the fact-signal that detects it, and the "legit when" exceptions. It's
the single source of truth both modes read from, so prevent ("don't") and remediate ("scan for") never
drift apart.

## Boundaries

- `prevent` writes no file. `remediate` reports first; only `--apply` edits, only HIGH + low-risk,
  only on a clean tree, one atomic commit each, never rewriting copy.
- Not a linter or an a11y/perf tool — it judges *taste*: does this read as AI-generated.
- An empty result is a valid, honest outcome. A UI made with intent should produce few findings.
