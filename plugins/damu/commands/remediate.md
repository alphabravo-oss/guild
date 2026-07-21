---
description: "Scan a running UI for AI-slop tells: Playwright captures screenshots + CSS facts per route, a Workflow engine judges them with 9 blind self-rating lenses, and renders a ranked change list. --apply commits HIGH-confidence low-risk fixes."
argument-hint: "[url or route list] [--apply] [--headed] [--routes=/,/pricing,/app]"
allowed-tools: ["Bash(git:*)", "Bash(mkdir:*)", "Bash(date:*)", "Bash(pwd:*)", "Bash(ls:*)", "Bash(cat:*)", "Bash(test:*)", "Bash(find:*)", "Bash(grep:*)", "Read", "Write", "Edit", "Glob", "Grep", "AskUserQuestion", "Workflow", "mcp__plugin_playwright_playwright__browser_navigate", "mcp__plugin_playwright_playwright__browser_take_screenshot", "mcp__plugin_playwright_playwright__browser_evaluate", "mcp__plugin_playwright_playwright__browser_resize", "mcp__plugin_playwright_playwright__browser_snapshot", "mcp__plugin_playwright_playwright__browser_wait_for", "mcp__plugin_playwright_playwright__browser_close"]
---

# damu — remediate (scan an existing UI for AI slop)

You are the **damu orchestrator**. The user wants their *already-built* UI scanned for the tells that
make it read as AI-generated, with concrete fixes. The catalog of tells lives at
`${CLAUDE_PLUGIN_ROOT}/skills/slop-catalog/SKILL.md` — read it before you start; it governs everything.

Your job has four phases: **capture** (you drive Playwright), **judge** (a bundled Workflow engine does
the analysis — you do NOT judge the UI yourself), **render**, and — only with `--apply` — a careful,
HIGH-confidence-only fix pass. Playwright is stateful and lives in *your* context, so **you** capture
the artifacts; the Workflow agents work off the saved screenshots + facts, never the live browser.

## Arguments

```
$ARGUMENTS
```

Parse: a URL or comma-separated routes; `--apply` (run the fix pass after the report); `--headed`
(visible browser, else headless is fine); `--routes=...` explicit route list.

## PHASE 0 — preflight

1. **Playwright MCP present?** The `mcp__plugin_playwright_playwright__*` tools must be available. If
   not, tell the user to run `/e2e:init` (or add the `playwright` server to `.mcp.json`) and **restart
   Claude Code**, then stop.
2. **Git repo?** `git rev-parse --is-inside-work-tree`. Needed for `--apply` (atomic commits) and for
   anchoring findings to source files. A scan can run without git, but `--apply` cannot — say so.
3. **For `--apply`: clean tree.** `git status --porcelain` must be empty. If dirty, refuse the apply
   pass (you'll still produce the report) — like tidy, every fix is one atomic commit away from a revert,
   which only works from a clean base.
4. **Target URL.** If none in args, ask with `AskUserQuestion`: the dev server URL (they type it). Do
   not guess a port. If they give a base URL, you'll discover routes in Phase 1.

Create the run dir:

```bash
mkdir -p .damu/runs/$(date -u +%Y%m%d-%H%M%S)/shots
```

Use that path as `RUNDIR` throughout.

## PHASE 1 — capture (you drive Playwright)

Determine the route list: explicit `--routes`/args win; otherwise scan the base URL, read its nav via
`browser_snapshot`, and pick the primary distinct pages (home, pricing, app/dashboard, a content page —
cap at ~6 so the run stays bounded; **`log` which routes you dropped**, never silently truncate).

For **each** route, at a desktop viewport (resize to 1440×900) and then once at 390×844 (mobile) for
the home route only:

1. `browser_navigate` to the route; `browser_wait_for` the page to settle.
2. `browser_take_screenshot` **full-page** → save to `RUNDIR/shots/<slug>-desktop.png` (and
   `<slug>-mobile.png` for home).
3. `browser_evaluate` the **facts extractor** below and append the returned object (keyed by route) to
   `RUNDIR/facts.json`.

### Facts extractor

Run this with `browser_evaluate` and store its result. It gathers the objective signals the lenses
need so findings are grounded in measurements, not just vibes:

```js
() => {
  const els = Array.from(document.querySelectorAll('body *')).slice(0, 4000);
  const cs = el => getComputedStyle(el);
  const fonts = {}, radii = [], shadows = [], gradients = [], colors = {}, bgs = {}, fontSizes = [], contrasts = [];
  let cards = 0, fixedBg = 0, gradientText = 0, textNodes = 0, smallText = 0, lowContrast = 0;
  const norm = s => (s || '').trim();
  // WCAG contrast helpers
  const rgb = c => { const m = (c || '').match(/[\d.]+/g); return m ? m.slice(0, 3).map(Number) : null; };
  const lum = c => { const r = rgb(c); if (!r) return null; const [R, G, B] = r.map(v => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); }); return 0.2126 * R + 0.7152 * G + 0.0722 * B; };
  const ratio = (fg, bg) => { const a = lum(fg), b = lum(bg); if (a == null || b == null) return null; const hi = Math.max(a, b), lo = Math.min(a, b); return (hi + 0.05) / (lo + 0.05); };
  const effBg = el => { let n = el; while (n) { const b = cs(n).backgroundColor; if (b && b !== 'rgba(0, 0, 0, 0)' && b !== 'transparent') return b; n = n.parentElement; } return 'rgb(255,255,255)'; };
  for (const el of els) {
    const s = cs(el);
    const fam = norm(s.fontFamily); if (fam) fonts[fam] = (fonts[fam] || 0) + 1;
    const r = parseFloat(s.borderTopLeftRadius) || 0; if (r) radii.push(r);
    if (s.boxShadow && s.boxShadow !== 'none') {
      const blur = (s.boxShadow.match(/(-?\d+(?:\.\d+)?)px/g) || []).map(parseFloat);
      if (blur[2] != null) shadows.push(blur[2]);
    }
    const bgi = s.backgroundImage || '';
    if (bgi.includes('gradient')) { gradients.push(bgi.slice(0, 120)); if (s.webkitBackgroundClip === 'text' || s.backgroundClip === 'text') gradientText++; }
    if (s.backgroundAttachment === 'fixed') fixedBg++;
    const col = norm(s.color); if (col) colors[col] = (colors[col] || 0) + 1;
    const bg = norm(s.backgroundColor); if (bg && bg !== 'rgba(0, 0, 0, 0)') bgs[bg] = (bgs[bg] || 0) + 1;
    const hasBorder = parseFloat(s.borderTopWidth) > 0;
    const hasShadow = s.boxShadow && s.boxShadow !== 'none';
    const pad = s.padding;
    if (r >= 8 && (hasBorder || hasShadow) && /(^|\s)24px|(^|\s)1\.5rem/.test(pad)) cards++;
    // readability: only elements with their OWN visible text
    const own = Array.from(el.childNodes).some(n => n.nodeType === 3 && n.textContent.trim().length > 1);
    if (own && s.visibility !== 'hidden' && s.display !== 'none') {
      const fs = parseFloat(s.fontSize) || 0;
      const bold = (parseInt(s.fontWeight, 10) || 400) >= 700;
      const large = fs >= 24 || (fs >= 18.66 && bold); // WCAG "large text"
      textNodes++;
      if (fs) { fontSizes.push(fs); if (fs < 14 && !large) smallText++; }
      const cr = ratio(col, effBg(el));
      if (cr != null) { contrasts.push(Math.round(cr * 10) / 10); if (cr < (large ? 3 : 4.5)) lowContrast++; }
    }
  }
  const top = o => Object.entries(o).sort((a,b)=>b[1]-a[1]).slice(0,8);
  const text = document.body.innerText || '';
  const emoji = (text.match(/\p{Extended_Pictographic}/gu) || []).length;
  const filler = (text.match(/\b(seamless|cutting-edge|empower|revolutioniz\w*|elevate|unlock|leverage|next-generation|game-chang\w*|effortless\w*)\b/gi) || []).length;
  const shadcn = !!Array.from(document.styleSheets).some(ss => { try { return Array.from(ss.cssRules||[]).some(r => /--background|--foreground|--muted-foreground|--ring/.test(r.cssText)); } catch { return false; } });
  return {
    distinctFonts: Object.keys(fonts).length, topFonts: top(fonts),
    radii: { count: radii.length, max: Math.max(0, ...radii), largeShare: radii.filter(x=>x>=16).length / Math.max(1, radii.length) },
    shadows: { count: shadows.length, maxBlur: Math.max(0, ...shadows), bigShare: shadows.filter(x=>x>=24).length / Math.max(1, shadows.length) },
    gradients: { count: gradients.length, samples: gradients.slice(0,6), gradientText },
    parallaxFixedBg: fixedBg,
    cardLikeCount: cards,
    topTextColors: top(colors), topBgColors: top(bgs),
    readability: {
      textNodes,
      minFontSize: fontSizes.length ? Math.min(...fontSizes) : null,
      smallTextShare: textNodes ? Math.round(smallText / textNodes * 100) / 100 : 0,
      minContrast: contrasts.length ? Math.min(...contrasts) : null,
      lowContrastShare: textNodes ? Math.round(lowContrast / textNodes * 100) / 100 : 0,
    },
    emojiCount: emoji, fillerHits: filler,
    shadcnTokensPresent: shadcn,
    docHeight: document.body.scrollHeight,
  };
}
```

If a route errors (won't load, auth wall), note it and skip — don't abort the whole run.

When all routes are captured, **`browser_close`** to free the browser, then write a small
`RUNDIR/manifest.json`: `{ routes: [{slug, url, shots:[...]}], baseUrl }`.

### Map the source (for anchoring + apply)

Best-effort, so findings can point at real files: `Glob`/`Grep` the project for the global CSS, the
Tailwind/shadcn config, and the top-level layout/page components. You don't need every file — enough
that the synthesis can suggest *where* a fix lives. Record what you found as `RUNDIR/source-map.json`.

## PHASE 2 — judge (run the Workflow engine)

Call the **Workflow** tool:

- `scriptPath`: `${CLAUDE_PLUGIN_ROOT}/workflows/remediate.js`
- `args`: an object —
  `{ runDir: "<RUNDIR>", catalogPath: "${CLAUDE_PLUGIN_ROOT}/skills/slop-catalog/SKILL.md", appContext: "<anything the user told you about audience/brand, or empty>" }`

The engine reads `facts.json`, the screenshots, and `source-map.json`; runs a context pass that
establishes what's legitimate for this product, 9 blind per-tell lenses that each self-rate confidence
and hard-respect those legitimate patterns (no separate skeptic — nothing gets dropped, weak findings
just rank low), a completeness critic, and synthesis. It runs in the background and notifies you — **do
not poll**. Tell the user once, briefly:

```
Scanning <N> routes for AI-slop tells. Context → 9 blind lenses → critic → synthesis. Fans out a
couple dozen agents; a few minutes.
```

## PHASE 3 — render the report

The workflow returns `{ runDir, findings, report }` (or `{ error }` — surface and stop). Write the
rendered markdown to `RUNDIR/report.md`, then render this in chat, built **only** from what the engine
returned — don't add findings of your own, don't soften the anchors:

```markdown
**damu — slop scan of <baseUrl>** · <N> routes · <M> findings

> <report.overall_verdict>   (per-page: <page: deliberate | mixed | slop>, …)

**Findings — highest leverage first**

- **<finding.title>** · `SLOP-NN` · <severity> · confidence <HIGH|MEDIUM|LOW> · risk <low|med|high>
  - where: <route(s)> — <file anchor if known>
  - evidence: <the fact signal + what the screenshot shows>
  - fix: <concrete change>
  - why it might be fine: <the finding's caveat, if any>
- …

**If you want me to apply the safe ones**
Re-run `/damu:remediate --apply` (clean tree required). I'll commit only the HIGH-confidence,
low-risk fixes (each its own atomic commit) and leave the subjective calls to you.
```

If `findings` is empty, say so plainly — the UI doesn't read as slop, or every candidate was refuted as
intentional. Don't manufacture findings to fill space. Copy findings (`SLOP-08`, `SLOP-11`) are
surfaced for the user to rewrite — never list them as auto-fixable.

## PHASE 4 — apply (only with `--apply`, only if clean tree)

Skip entirely unless `--apply` was passed and Phase 0 confirmed a clean git tree. Then, for each
finding with **confidence HIGH and risk low** only:

1. Read the target file(s) the synthesis named. Make the minimal, mechanical change (e.g. collapse the
   font stack to two, remove the `background-attachment: fixed`, drop the universal large radius to the
   scale, delete the neon gradient border, tune a default Tailwind token). **Never** rewrite copy
   (SLOP-08/-11) — those stay for the human.
2. Run the project's typecheck/lint/build if one is obvious (`package.json` scripts); if it fails,
   revert that change and downgrade the finding to "surfaced, not applied."
3. **One atomic commit per finding** — message: `style(damu): <fix> [SLOP-NN]`. Co-author trailer per
   repo convention.

MEDIUM/LOW/high-risk findings are **never** auto-applied — list them as "left for you" with the file
and the suggested change. After the pass, summarize: applied (with commit shas), reverted, and left.

## NON-NEGOTIABLE RULES

1. **You capture; the engine judges.** Don't freehand a parallel UI opinion in the orchestrator — the
   value is the blind self-rating lenses over grounded facts. Render what it returns.
2. **Every tell is sometimes correct.** Each finding carries its own confidence and a caveat (the case
   it might be intentional) — preserve the caveat in the report so the user sees both sides, and lean on
   confidence/risk, not your own taste, to decide what's worth acting on.
3. **Preserve every anchor and fact signal verbatim.** They're the trust mechanism.
4. **Apply only HIGH + low-risk, only on a clean tree, one atomic commit each.** Refuse a dirty tree.
   Never auto-rewrite copy. UI taste is subjective — when unsure, surface, don't change.
5. **Empty is a valid result.** A UI made with intent should produce few or no findings.
6. **One status line during the run, then wait.** Don't narrate the engine's phases.
