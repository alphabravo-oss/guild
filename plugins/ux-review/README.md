<p align="center">
  <b>ux-review</b> — experiential UX review that drives the running app as a real user.<br/>
  <i>A code read cannot find an overlay anchored to the wrong thing.</i>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/ux--review-0.1.0-00897B?style=flat-square" alt="ux-review 0.1.0"/>
  <img src="https://img.shields.io/badge/guild-review%20only-00897B?style=flat-square" alt="guild reviewer"/>
  <img src="https://img.shields.io/badge/Claude%20Code-plugin-8E44AD?style=flat-square" alt="Claude Code plugin"/>
  <img src="https://img.shields.io/badge/license-MIT-2E7D32?style=flat-square" alt="MIT license"/>
</p>

<p align="center">
  <a href="../../README.md">← back to the Guild marketplace</a>
</p>

---

**Experiential UX review that drives the running app as a real user — not a code read.**

Reading source is a *code* review: it verifies correctness. It structurally cannot find the defects that only appear when a person *uses* the product:

- a mode or label that doesn't do what it claims,
- an overlay/result anchored to the wrong thing (the viewport center instead of the point you chose),
- a feature that works on the default screen and silently breaks in another state,
- a table-stakes affordance that is simply **absent**.

`ux-review` launches the app in a real browser, discovers what it is, and uses it the way its actual users would — then reports ranked, evidence-backed findings.

## Commands

| Command | What it does |
|---|---|
| `/ux-review:run [target] [focus] [--quick\|--exhaustive]` | Drive the app and produce a ranked UX review. `target` = URL or a start command; `focus` = an area to concentrate on. |
| `/ux-review:help` | Show the plugin overview. |

## How it works

1. **Self-configure** — discover the product, its personas, its promises, and peer-category conventions. *(This is what keeps the review generic — it's told nothing; it learns the app.)*
2. **Coverage grid** — enumerate every screen × state × variant × viewport × input modality; commit to ticking each cell.
3. **Do the jobs** through five sharp lenses — **promise vs delivery, default vs buried, anchor/subject, every-state-not-the-happy-one, what's-missing** — plus the classic usability heuristics framed experientially.
4. **Adversarial states** — empty / error / offline / slow / permission-denied / first-run / heavy-returning.
5. **Cross-cutting sweeps** — keyboard-only, screen reader, mobile/touch, latency, and a continuous silent-failure watch on the console and network.
6. **Completeness critic** — loop until a full pass surfaces nothing new; name anything deliberately skipped.
7. **Report** — findings ranked by *user harm* (blocker → polish), each as `job/persona/state → expected vs actual (+screenshot) → why it hurts → severity/confidence`, closed by an auditable coverage report.

## Requirements

- A running application the plugin can reach (it will try to start a dev/preview server if one is defined).
- A **Playwright MCP** browser to drive it (navigate, click, type, drag, resize, snapshot, screenshot, read console/network).

## Depth

- `--quick` — one persona, the core flow, the obvious states, desktop + mobile.
- *default* — 2–3 personas, main flows, the full state list, both viewports, the sweeps.
- `--exhaustive` — every persona × surface, the complete grid, adversarial states, all sweeps, and loop-until-dry. Broad runs fan out the `persona-driver` subagent (one per persona × surface) so coverage doesn't blow the main context.

## The idea in one line

> Discover the product, then use it like the person it's for — across every state — and treat every "huh?" as a bug.

## License

See the repository `LICENSE`.
