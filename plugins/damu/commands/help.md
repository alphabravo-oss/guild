---
description: "Explain the damu (De-AI My UI) plugin"
allowed-tools: ["Read"]
---

# damu — help

**damu** ("De-AI My UI") strips the tells that make an interface read as *made by an AI in Tailwind* —
font chaos, the purple-on-black palette, gratuitous parallax, neon gradient borders, everything-is-a-
24px-card, recycled rocket/bolt/shield icons, the AI-startup serif, out-of-box shadcn, filler copy, and
the rest. One shared catalog of ~19 tells drives two modes.

## Commands

- `/damu:prevent [context]` — **stops slop before it's generated.** Prints a copy-pasteable anti-slop
  design ruleset (the catalog's fixes as imperatives), lightly tailored to your detected stack
  (Tailwind/shadcn rules foregrounded when present). You adjust it and drop it into `CLAUDE.md`, your
  global instructions, or a memory file. Writes no file — it's yours to place. Give it context
  ("kids' app", "fintech dashboard") so the rules don't forbid what your product legitimately wants.

- `/damu:remediate [url] [--apply] [--headed] [--routes=/,/pricing]` — **scans a UI you already built.**
  Drives Playwright to capture per-route screenshots + extracted CSS facts, runs a Workflow engine that
  judges them, and renders a ranked, source-anchored change list. `--apply` (clean git tree required)
  commits only the HIGH-confidence, low-risk fixes, one atomic commit each, and leaves the subjective
  calls to you. Needs the Playwright MCP — run `/e2e:init` first if you don't have it.

- `/damu:help` — this.

## The governing idea

Every tell is *sometimes* the right call — a dashboard *is* card-based, a kids' app *should* be rounded
and emoji-rich. The slop isn't the pattern; it's the pattern applied **by default, uniformly, with no
reason**. So damu doesn't blindly flag purple.

## How remediate earns trust

Not "ask Claude if the UI looks AI." A Workflow engine with the same trust mechanisms as holmes, over
captured artifacts (screenshots + objective CSS facts), never the live browser:

1. **Context pass** — reads the UI to establish what the product is, who it's for, and which slop-
   adjacent patterns are *legitimate here*, which the lenses then treat as hard exclusions.
2. **9 blind lenses** — typography, color, gradients & borders, shape & depth, layout soul, motion,
   iconography, copy, readability — each hunting one group of tells, grounded in a fact signal *and* the
   image. Each lens is the sole judge of its tells and self-rates confidence (HIGH only when a choice is
   clearly unmotivated *and* uniform), so nothing is silently dropped — a weak finding just ranks low.
3. **Completeness critic** — re-examines uncovered tells, glanced-over pages, and whole-page
   soullessness no single catalog row captures.

## Output

`prevent`: one pasteable markdown block in chat. `remediate`: a debrief in chat + a saved report and
all artifacts under `.damu/runs/<timestamp>/`. Every finding carries the fact signal, what the
screenshot shows, the concrete fix, its confidence, and a "why it might be fine" caveat. An empty result
is honest — a UI made with intent should produce few findings.
