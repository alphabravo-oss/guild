---
name: slop-catalog
description: The canonical catalog of AI-generated-UI tells — the visual signatures that make a UI read as "made by an AI in Tailwind." Each tell carries what to look for, why it reads as AI, the fix, the fact-signal that detects it, and the "legit when…" exceptions that keep it honest. The single source of truth shared by /damu:prevent (turns fixes into rules) and /damu:remediate (turns tells into a scan). Used by the damu remediate Workflow lenses and the prevent ruleset renderer.
user_invocable: false
---

# damu — the AI-slop catalog

This is the reference both damu modes read from. **Prevent** renders the `Fix` lines into a
prohibition ruleset. **Remediate** runs each entry as a detection lens, using `Detect` for the
fact-signal and `Legit when` as the hard exclusions a lens must not flag.

## The one rule that governs all the others

**Every tell below is sometimes correct.** A fintech dashboard *is* mostly cards. A children's app
*should* be rounded and emoji-rich. A brand site *may* earn a parallax hero. The slop isn't the
pattern — it's the pattern applied **by default, without a reason, to everything**. A finding is only
real when the choice looks unmotivated and uniform. When in doubt, rate it LOW confidence rather than
HIGH — surface it, but don't treat a plausibly-intentional choice as a confident finding. Flagging a
justified choice as if it were obvious slop is itself a kind of slop.

A second governing read: **soul**. Slop is what you get when every decision defaulted. The opposite
isn't "more design" — it's *evidence that a human made a choice here and not somewhere else*: one
deliberate accent, asymmetry that means something, type that has a job, whitespace that breathes
unevenly. Absence of any such choice across a whole page is the strongest tell of all.

---

## SLOP-01 — Font chaos
- **The tell:** 4–5 competing typefaces, often a display serif + a geometric sans + a mono + whatever
  the component library shipped, with no hierarchy logic. Looks amateur because nothing is in charge.
- **Why it reads AI:** models stitch type from many sources and never cull. A human picks one or two
  and commits.
- **Fix:** one or two families, max. A type *system* (one display + one text, or a single superfamily
  with weights). Hierarchy by size/weight/spacing, not by swapping fonts.
- **Detect:** count distinct rendered `font-family` stacks across text nodes. >3 non-icon families ⇒ flag.
- **Legit when:** an intentional editorial pairing (display + body + a deliberate mono for code/data) —
  3 families with a clear job each is a system, not chaos.

## SLOP-02 — The AI palette (purple/indigo on near-black/blue)
- **The tell:** violet/indigo/blue accents on near-black or deep-blue grounds. The default "AI startup"
  wash.
- **Why it reads AI:** it's the median of training data — the safest, most-seen tech palette, so models
  regress to it.
- **Fix:** choose a palette with a reason (brand, content, mood). If you land on purple, *earn* it —
  pair it with an unexpected neutral, vary saturation, avoid the indigo-on-black cliché.
- **Detect:** cluster used colors by hue; flag a dominant 250–280° (violet/indigo) accent over a
  <12% lightness ground, especially with a blue secondary.
- **Legit when:** the brand genuinely is purple, or the palette is sampled from real content/photography
  rather than defaulted.

## SLOP-03 — Gratuitous parallax
- **The tell:** a background that scroll-shifts independently for no narrative reason. Decoration, not
  meaning.
- **Why it reads AI:** it's a "looks fancy" tic models reach for to signal effort.
- **Fix:** remove it unless the parallax *tells a story* (a product reveal, a depth metaphor the content
  needs). Motion should explain, not decorate.
- **Detect:** `background-attachment: fixed`, or scroll-linked `transform: translateY()` on a
  background/hero layer.
- **Legit when:** a deliberate scrollytelling piece, a product narrative, or a hero where depth is the point.

## SLOP-04 — Weird hover states
- **The tell:** hovers that lift, glow, scale, tilt, or color-shift in ways that don't match the
  element's importance — every card doing a 3D tilt, buttons that balloon.
- **Why it reads AI:** models add "interactivity" per-element without a motion language.
- **Fix:** one restrained hover convention (subtle bg/border shift, ~120–160ms). Reserve big motion for
  the rare element that deserves emphasis.
- **Detect:** hover transforms with scale >1.03, rotate, or large shadow jumps applied broadly across
  unlike elements.
- **Legit when:** a single deliberate interactive showcase element, or a playful brand where motion is
  the identity.

## SLOP-05 — Ugly / default gradients
- **The tell:** muddy two-stop linear gradients on buttons, headings, and backgrounds — purple→blue
  being the house special.
- **Why it reads AI:** gradient = "premium" shortcut in training data.
- **Fix:** prefer solid color. If a gradient earns its place, make it subtle and same-family, or a
  considered duotone — not a rainbow ramp.
- **Detect:** `linear-gradient`/`radial-gradient` count; flag high-saturation multi-hue gradients used as
  the default surface/text fill.
- **Legit when:** a single brand gradient used sparingly and well; data-viz ramps; a mesh used as one
  deliberate hero.

## SLOP-06 — Recycled stock icons
- **The tell:** the same 3 generic icons (rocket, lightning bolt, shield/checkmark) repeated as if they
  evolve — Pokémon-style — across every feature.
- **Why it reads AI:** default icon-set top hits, reused without thought.
- **Fix:** icons that depict the *actual* thing. Fewer, specific, consistent stroke weight. If no icon
  fits, use none.
- **Detect:** repeated identical icon glyphs across distinct features; default lucide/heroicons rocket/
  zap/shield clusters.
- **Legit when:** a consistent, purpose-built icon set where repetition is a system, not a crutch.

## SLOP-07 — Everything-is-a-card, 24px padding, zero soul
- **The tell:** every section is a rounded card with a border, a shadow, and uniform `p-6`/24px padding.
  A grid of identical boxes.
- **Why it reads AI:** the card is the safest container, so models wrap everything in one.
- **Fix:** vary containers. Let some content breathe on the page with no box. Use whitespace, rules,
  and alignment instead of universal cards. Break the grid intentionally.
- **Detect:** high proportion of sibling containers sharing identical radius+border+shadow+padding
  (esp. 24px / p-6); uniform card grid as the whole page.
- **Legit when:** dashboards, kanban, product grids, settings panels — where discrete cards *are* the
  information model.

## SLOP-08 — Filler text that says nothing
- **The tell:** "Empower your workflow with seamless, cutting-edge solutions." Headlines and body that
  are grammatically fine and semantically empty.
- **Why it reads AI:** LLM prose defaults to confident vagueness.
- **Fix:** say the specific true thing. Concrete nouns, real numbers, the actual benefit. Cut any
  sentence that survives being deleted.
- **Detect:** marketing-adjective density (seamless, cutting-edge, empower, revolutionize, elevate,
  unlock) with no concrete noun/number nearby.
- **Legit when:** never, really — but copy is out of scope for auto-fix; surface, don't rewrite silently.

## SLOP-09 — "Made in Tailwind with love" smell
- **The tell:** the DOM screams default utility scale before it loads — `gap-4 p-6 rounded-lg shadow-md
  bg-gray-900 text-gray-100`, untouched spacing scale, default container widths.
- **Why it reads AI:** models emit Tailwind defaults verbatim and never tune the scale.
- **Fix:** customize the design tokens — bespoke spacing/radii/type scale, a real color config. Tailwind
  is fine; *default* Tailwind is the tell.
- **Detect:** overwhelming presence of unmodified default Tailwind classes; default theme values in the
  computed styles (e.g. exact default gray ramp, default shadow tokens).
- **Legit when:** an early prototype that's honest about being one; internal tools where speed > polish.

## SLOP-10 — Icon & emoji overuse
- **The tell:** an icon or emoji on every list item, heading, button, and bullet. Visual noise.
- **Why it reads AI:** models decorate to fill, treating icons as garnish.
- **Fix:** icons earn their place — use them where they aid scanning (nav, status), not as universal
  bullets. Most text needs none.
- **Detect:** emoji-per-text-node ratio and icon-per-interactive-element ratio above a sane threshold.
- **Legit when:** playful consumer brands, kids' products, chat/social UIs where emoji are the voice.

## SLOP-11 — Icons/emoji that don't match their meaning
- **The tell:** a 🚀 next to "Billing," a gear icon for "Blog," a 🔥 for a neutral feature. Decoration
  detached from meaning.
- **Why it reads AI:** icons chosen for vibe, not denotation.
- **Fix:** every icon must *denote* its label. If you can't find one that does, drop it.
- **Detect:** semantic mismatch between an icon/emoji and its adjacent label (requires vision judgement).
- **Legit when:** deliberately playful/ironic brand voice where the mismatch is the joke — rare, and it
  must read as intentional.

## SLOP-12 — Over-rounded corners everywhere
- **The tell:** large border-radii on everything — cards, buttons, inputs, images, avatars, the whole
  page feeling like a pile of lozenges.
- **Why it reads AI:** "friendly = rounder" default, applied without restraint.
- **Fix:** a deliberate radius scale, mostly modest. Reserve big rounding for things that earn it
  (pills, avatars). Sharp can be elegant.
- **Detect:** distribution of `border-radius`; flag large radii (e.g. ≥16px) applied near-universally
  across unlike elements.
- **Legit when:** a soft, playful, or consumer brand where roundness is a deliberate identity; pills/
  avatars.

## SLOP-13 — Huge drop-shadow radii
- **The tell:** giant soft shadows with big blur/spread under everything, floating the whole UI off the
  page.
- **Why it reads AI:** "depth = premium" shortcut, overdone.
- **Fix:** subtle, purposeful elevation. Small blur, low opacity, and only where hierarchy needs it.
  Many surfaces need no shadow at all.
- **Detect:** `box-shadow` blur radius distribution; flag large-blur/high-spread shadows applied broadly.
- **Legit when:** a single deliberately elevated layer (a modal, a key CTA) — not the whole page.

## SLOP-14 — Neon gradients
- **The tell:** electric, oversaturated gradient fills (cyan→magenta, lime→violet) glowing off the screen.
- **Why it reads AI:** "futuristic" cliché from generative-art training data.
- **Fix:** dial saturation way down or go solid. If you want energy, one accent, used once.
- **Detect:** gradients with very high chroma across clashing hues.
- **Legit when:** music/gaming/crypto/event brands where neon is genuinely the aesthetic — and even then,
  sparingly.

## SLOP-15 — Neon gradient borders
- **The tell:** the glowing 1–2px conic/linear gradient border around cards and buttons — the "AI app"
  signature ring.
- **Why it reads AI:** a single viral pattern models now reproduce constantly.
- **Fix:** a plain border, or none. If you want emphasis, a solid accent border on the one element that
  matters.
- **Detect:** gradient applied via `border-image` or the mask/`padding-box`+`border-box` gradient-border
  trick, especially high-chroma and repeated.
- **Legit when:** one hero element as a deliberate focal point — never as the default card frame.

## SLOP-16 — That one AI-startup serif
- **The tell:** the specific high-contrast display serif every AI launch page now uses for its hero,
  signalling "tasteful" by imitation.
- **Why it reads AI:** it became the default "we have taste" move, so it now signals the opposite.
- **Fix:** pick type with intent for *this* product. If a serif fits, choose one that isn't the cliché,
  and pair it deliberately.
- **Detect:** the well-known display serif as the hero face, paired with a default geometric sans body.
- **Legit when:** an editorial/literary product where that serif genuinely serves the content and the
  pairing is considered.

## SLOP-17 — shadcn out of the box
- **The tell:** unmodified shadcn/ui — default radius, default neutral palette, default component shapes,
  the `--background`/`--foreground` token names untouched. Recognizable on sight.
- **Why it reads AI:** the fastest scaffold, shipped without theming.
- **Fix:** shadcn is a great base — *theme* it. Change the radius token, the color tokens, the type, the
  density. Make components yours.
- **Detect:** signature shadcn CSS variable names and default token values; default component class
  structures with no theme overrides.
- **Legit when:** an internal tool or prototype where the default is an acceptable, honest baseline.

## SLOP-18 — Forced monochrome
- **The tell:** a single hue (or pure grayscale) across the entire UI with no accent, mistaking
  restraint for design. Lifeless rather than minimal.
- **Why it reads AI:** "minimal = one color," over-applied.
- **Fix:** restraint with *one* deliberate accent that carries meaning (action, focus, brand). Minimal
  needs a point of contrast to have a voice.
- **Detect:** near-zero chroma across all surfaces and text, with no functional accent color present.
- **Legit when:** a genuinely monochrome brand system, an e-ink/print aesthetic, or a content surface
  where color would distract — done on purpose.

## SLOP-19 — Tiny / low-contrast text
- **The tell:** body and caption text set too small (sub-14px body, 10–12px captions) and/or too low
  in contrast (light gray on white, mid-gray on dark) — "elegant and subtle" at the cost of being
  readable.
- **Why it reads AI:** models copy the muted-gray, small-caption look from design-system marketing
  demos without ever checking real legibility; "subtle" is a safe-looking default.
- **Fix:** body text ~16px (≥14px even in dense UI), captions still readable; meet WCAG AA contrast
  (4.5:1 for normal text, 3:1 for large). Muted is fine; unreadable is not.
- **Detect:** `readability.smallTextShare` / `minFontSize` (share of own-text nodes under 14px) and
  `readability.lowContrastShare` / `minContrast` (WCAG ratio of text vs its effective background).
- **Legit when:** dense data tables or code where a smaller still-legible size is a deliberate
  tradeoff; decorative/watermark text that isn't real content; text that's already comfortably large.

---

## How remediate uses this

Each lens owns a group of tells and judges **screenshots + extracted CSS facts**, never the live
browser. The context pass first lists the patterns that are **legitimate for this specific product**
(a dashboard's cards, a kids' app's rounding); each lens treats those as hard exclusions and **does not
flag them**. For everything it does report, the lens cites `SLOP-NN`, self-rates confidence (HIGH only
when the choice is clearly unmotivated *and* uniform) and risk, and notes any way it could still be
intentional as a `caveat`. Nothing is dropped after the fact — findings are surfaced and ranked, with
low-confidence ones simply ranking lower and never auto-applied. Copy findings (SLOP-08, -11) are
surfaced, never auto-rewritten.

## How prevent uses this

The ruleset is the `Fix` lines, phrased as imperatives, grouped, with the governing "every tell is
sometimes correct — have a reason" caveat on top so the rules don't turn into a new cage.
