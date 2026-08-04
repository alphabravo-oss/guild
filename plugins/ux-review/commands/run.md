---
description: Drive the running app as a real user and produce a ranked, evidence-backed UX review
---

The user wants an experiential UX review of a running application. This is a USE-the-app review, not a source read. `$ARGUMENTS` may contain a target (URL, or a dev/preview command to start), a focus area (e.g. "the checkout flow", "mobile", "onboarding"), and a depth flag (`--quick` or `--exhaustive`, default balanced).

## Preflight

1. **Load the methodology.** Read `${CLAUDE_PLUGIN_ROOT}/skills/review/SKILL.md` (or invoke the `ux-review:review` skill). It is the authority for how to review — the phases, the five lenses, the coverage grid, the completeness loop, and the finding format. Follow it.
2. **Get the app running in a real browser.** Determine the URL:
   - If `$ARGUMENTS` gives a URL, use it.
   - Else look for a dev/preview/start script (e.g. package.json scripts, a Makefile target, a Procfile) and start it in the background, then wait for it to be reachable.
   - If nothing is runnable, **stop** — report "could not launch the app" as finding #1 with what you tried.
3. **Confirm browser control.** Ensure a Playwright MCP browser is available (navigate/click/type/snapshot/screenshot/console). If not, tell the user which MCP to enable and stop.

## Scope the run

- **Depth.** `--quick` → one primary persona, the core flow, the obvious states, one desktop + one mobile viewport. Default → 2–3 personas, main flows, the full state list, both viewports, the sweeps. `--exhaustive` → every persona × every surface, the complete grid, adversarial states, all cross-cutting sweeps, and loop-until-dry.
- **Fan out for breadth (default & exhaustive).** Browser snapshots are large and clutter context. Spawn `ux-review:persona-driver` subagents — one per (persona × surface) slice — to drive the live app in parallel and return structured findings. Run them concurrently. Then dedupe and synthesize their findings yourself. For `--quick`, drive it inline in the main thread.

## Execute

Work the SKILL phases in order: **0 self-configure → 1 enumerate the grid → 2 do the jobs (five lenses + heuristics) → 3 adversarial states → 4 cross-cutting sweeps → 5 completeness critic + loop until dry → 6 report.** Keep the console and network panel observed throughout — silent failures are findings. Screenshot every defect at the moment it goes wrong.

## Deliver

Produce the ranked report exactly as the SKILL specifies: findings ordered by **user harm** (blocker → polish), each as *finding → job/persona/state/viewport → expected vs actual (+ evidence) → why it hurts → severity/confidence → fix direction*. Close with the **coverage report** (grid marked done/skipped) so the thoroughness is auditable. State plainly where you found nothing — that is a result.

Do not modify the application's code as part of the review. If the user wants fixes, offer to do them as a separate step after they've seen the findings.
