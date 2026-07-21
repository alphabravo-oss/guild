---
description: Show the ux-review plugin — what it does, commands, and how it works
---

The user wants help with the ux-review plugin. Reply with exactly this message — verbatim, no additions:

```
ux-review — experiential UX review that drives the RUNNING app as a real user

WHAT IT DOES
  Reviews the product the way a person actually experiences it, not the way the
  code reads. Reading source is a code review — it can't find a mode that doesn't
  do what its label claims, an overlay anchored to the wrong thing, a feature that
  silently breaks in one state, or a table-stakes affordance that's simply missing.
  This plugin launches the app in a real browser, discovers what it is / who it's
  for / what it promises, then drives real jobs-to-be-done across a full coverage
  grid and reports ranked, evidence-backed findings.

COMMANDS
  /ux-review:run [target] [focus] [--quick|--exhaustive]
                             Drive the app and produce a ranked UX review.
                             target = URL or a start command; focus = an area to
                             concentrate on. Default depth is balanced.
  /ux-review:help            This message

HOW IT WORKS
  Phase 0  Self-configure — discover the product, its personas, its promises, and
           peer-category conventions (this is what keeps it generic).
  Phase 1  Coverage grid — every screen × state × variant × viewport × input.
  Phase 2  Do the jobs through five lenses: promise-vs-delivery, default-vs-buried,
           anchor/subject, every-state-not-the-happy-one, what's-missing — plus the
           classic usability heuristics framed experientially.
  Phase 3  Adversarial states — empty / error / offline / slow / denied / first-run.
  Phase 4  Sweeps — keyboard, screen reader, mobile, latency, silent-failure watch.
  Phase 5  Completeness critic — loop until a full pass finds nothing new.
  Phase 6  Report — findings ranked by user harm + an auditable coverage report.

  Drives real browsers via Playwright MCP. The persona-driver subagent fans out one
  driver per persona × surface for exhaustive parallel coverage.

REQUIRES
  A running app it can reach, and a Playwright MCP browser to drive it.

THE IDEA IN ONE LINE
  Discover the product, then use it like the person it's for — across every state —
  and treat every "huh?" as a bug.
```
