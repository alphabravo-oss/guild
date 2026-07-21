---
description: "Print a copy-pasteable anti-AI-slop design ruleset (tailored to the detected stack) for you to adjust and drop into CLAUDE.md or memory."
argument-hint: "[optional: extra context about the product — audience, brand, vibe]"
allowed-tools: ["Bash(test:*)", "Bash(ls:*)", "Bash(cat:*)", "Bash(find:*)", "Bash(grep:*)", "Read", "Glob", "Grep"]
---

# damu — prevent (emit the anti-slop ruleset)

The user wants a ruleset they can **adjust and paste** into their project `CLAUDE.md`, global
instructions, or a memory file — so Claude stops generating AI-slop UI in the first place. You do not
write any file. You **print one clean, copy-pasteable block**, lightly tailored to the stack you detect.

The catalog of tells and their fixes lives in `${CLAUDE_PLUGIN_ROOT}/skills/slop-catalog/SKILL.md`.
Read it — the ruleset is its `Fix` lines turned into imperatives. Do not invent rules beyond it.

## Extra context

```
$ARGUMENTS
```

If the user gave audience/brand/vibe context (e.g. "kids' app," "fintech dashboard," "neon music
brand"), weave it into the caveats so the rules don't forbid something the product legitimately wants
(a kids' app *should* be rounded and emoji-rich; a dashboard *is* card-based; a music brand *may* earn
neon). If empty, keep the caveats generic.

## PHASE 0 — detect the stack (cheap, best-effort)

Glance at the project to tailor the ruleset. Don't over-invest — a few greps:

- `package.json` for `tailwindcss`, `shadcn`/`@radix-ui`, a component lib, the CSS approach.
- Presence of `components.json` (shadcn), `tailwind.config.*`, a global CSS with `--background`/
  `--foreground` tokens.
- Any obvious framework (Next, Vite, etc.).

This only decides which **stack-specific rules** to foreground (e.g. surface the shadcn-theming and
Tailwind-default rules prominently if those are present). If you can't tell, print the full generic set.

## PHASE 1 — print the ruleset

Print exactly this structure as a fenced ` ```markdown ` block so the user can copy it whole. Fill the
rules from the catalog's `Fix` lines. Keep it tight — imperatives, not essays. Foreground the
stack-specific section only if you detected that stack; otherwise omit it.

````markdown
## Design rules — no AI slop

**Governing rule:** every item below is *sometimes* the right call. The slop is the default applied
without a reason to everything. Have a reason, then it's allowed. The goal is evidence a human made a
choice here — one deliberate accent, type with a job, whitespace that breathes — not "more design."

**Type**
- One or two typefaces, max. A type system: size/weight/spacing for hierarchy, not more fonts.
- Don't reach for the high-contrast AI-startup display serif by default; pick type for *this* product.

**Color**
- Don't default to purple/indigo accents on near-black or deep-blue. If you land on purple, earn it.
- No forced monochrome — restraint still needs one deliberate accent that carries meaning.
- Choose a palette with a reason (brand, content, mood), not the median tech wash.

**Surfaces, shape, depth**
- Not everything is a card. Vary containers; let content breathe with no box; use whitespace and
  alignment over universal `p-6`/24px cards.
- A deliberate, mostly-modest radius scale. Don't round everything heavily.
- Subtle, purposeful shadows only where hierarchy needs them. No giant blur under everything.

**Gradients & borders**
- Prefer solid color. A gradient must earn its place: subtle, same-family. No muddy purple→blue default.
- No neon gradients. No neon gradient borders — the glowing card ring is the AI signature; use a plain
  border or none.

**Motion**
- No parallax unless it tells a story the content needs.
- One restrained hover convention (~120–160ms, subtle). No broad scale/tilt/glow on every element.

**Icons & emoji**
- Icons must depict the actual thing and earn their place — not garnish on every heading and bullet.
- Never an icon/emoji that doesn't match its label. If none fits, use none.
- Don't recycle the same rocket/bolt/shield across every feature.

**Copy**
- Say the specific true thing — concrete nouns, real numbers, the actual benefit. Cut any sentence
  that survives being deleted. No "seamless, cutting-edge, empower" filler.

<!-- stack-specific — include only when detected -->
**Tailwind / shadcn**
- Default Tailwind is the tell. Tune the tokens — bespoke spacing, radius, type, and color scale.
- shadcn is a base, not a finish. Theme it: change the radius and color tokens, type, and density so
  components are yours, not the default scaffold.
````

After the block, add one short plain-text line (outside the code fence): where to put it and that it's
theirs to edit —

> Adjust the caveats to your product, then paste this into your project `CLAUDE.md`, your global
> `~/.claude/CLAUDE.md`, or a memory file. Run `/damu:remediate` to scan a UI you've already built.

## NON-NEGOTIABLE RULES

1. **Print only — write no file.** The user adjusts and places it themselves.
2. **Rules come from the catalog.** Don't freehand new ones; render its `Fix` lines.
3. **Keep the governing caveat on top.** Without it the ruleset becomes a new cage that forbids
   legitimate choices.
4. **One pasteable block.** Don't fragment it across prose.
