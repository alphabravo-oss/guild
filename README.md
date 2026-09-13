<div align="center">

<img src=".github/assets/the-guild.png" alt="The Guild — smiths and scribes at work: one hammers at the anvil, one drafts at the bench, one carries the finished piece away" width="760"/>

<img src=".github/assets/tagline.svg" alt="Forge plans. Foundry builds. adhoc keeps Claude honest. tldr says it in one line. You ship." width="760"/>

<br/>

**13 plugins for Claude Code.** A spec engine, a build engine, two always-on rule layers, and a bench of specialists.

<br/>

<a href="#-quick-start"><img src="https://img.shields.io/badge/QUICK_START-1E88E5?style=for-the-badge&logoColor=white" alt="Quick start"/></a>
<a href="#-the-guild"><img src="https://img.shields.io/badge/13_PLUGINS-8E44AD?style=for-the-badge" alt="13 plugins"/></a>
<a href="#-deeper"><img src="https://img.shields.io/badge/DOCS-F57C00?style=for-the-badge" alt="Docs"/></a>
<a href="./LICENSE"><img src="https://img.shields.io/badge/MIT-2E7D32?style=for-the-badge" alt="MIT"/></a>

<a href="https://github.com/alphabravo-oss/guild/stargazers"><img src="https://img.shields.io/github/stars/alphabravo-oss/guild?style=flat-square&color=FFC107" alt="Stars"/></a>
<a href="https://github.com/alphabravo-oss/guild/issues"><img src="https://img.shields.io/github/issues/alphabravo-oss/guild?style=flat-square&color=607D8B" alt="Issues"/></a>
<img src="https://img.shields.io/github/last-commit/alphabravo-oss/guild?style=flat-square&color=607D8B" alt="Last commit"/>
<img src="https://img.shields.io/badge/agents-44-00897B?style=flat-square" alt="44 agents"/>
<img src="https://img.shields.io/badge/commands-60-6D4C41?style=flat-square" alt="60 commands"/>

<br/>

<a href="#-the-guild">Plugins</a> · <a href="#-quick-start">Quick start</a> · <a href="#-how-it-works">How it works</a> · <a href="#-why-guild">Why</a> · <a href="#-deeper">Deeper</a>

</div>

---

<div align="center">

## 🧩 The Guild

<table>
<tr>
<td width="33%" align="center" valign="top">
<h2>📐</h2>
<b><a href="plugins/forge">forge</a></b><br/>
<sub>Interviews you.<br/>Emits a locked spec.</sub><br/><br/>
<code>/forge:plan</code>
</td>
<td width="33%" align="center" valign="top">
<h2>🏭</h2>
<b><a href="plugins/foundry">foundry</a></b><br/>
<sub>Builds the spec.<br/>Fully autonomous.</sub><br/><br/>
<code>/foundry:start</code>
</td>
<td width="33%" align="center" valign="top">
<h2>⚗️</h2>
<b><a href="plugins/crucible">crucible</a></b><br/>
<sub>Foundry, mini.<br/>No MCP, no interview.</sub><br/><br/>
<code>/crucible:build</code>
</td>
</tr>
<tr>
<td width="33%" align="center" valign="top">
<h2>🤖</h2>
<b><a href="plugins/crew">crew</a></b><br/>
<sub>Owns the outcome.<br/>Five agents, one job.</sub><br/><br/>
<code>/crew:do</code>
</td>
<td width="33%" align="center" valign="top">
<h2>🧭</h2>
<b><a href="plugins/adhoc">adhoc</a></b><br/>
<sub>Blocks citations<br/>it never verified.</sub><br/><br/>
<code>always on</code>
</td>
<td width="33%" align="center" valign="top">
<h2>⚡</h2>
<b><a href="plugins/tldr">tldr</a></b><br/>
<sub>Action first.<br/>No preamble.</sub><br/><br/>
<code>always on</code>
</td>
</tr>
<tr>
<td width="33%" align="center" valign="top">
<h2>🔍</h2>
<b><a href="plugins/holmes">holmes</a></b><br/>
<sub>Shaped right,<br/>or accreted?</sub><br/><br/>
<code>/holmes:review</code>
</td>
<td width="33%" align="center" valign="top">
<h2>👁️</h2>
<b><a href="plugins/ux-review">ux-review</a></b><br/>
<sub>Drives the app.<br/>Doesn't read code.</sub><br/><br/>
<code>/ux-review:run</code>
</td>
<td width="33%" align="center" valign="top">
<h2>🎨</h2>
<b><a href="plugins/damu">damu</a></b><br/>
<sub>De-AI my UI.<br/>19 slop signatures.</sub><br/><br/>
<code>/damu:remediate</code>
</td>
</tr>
<tr>
<td width="33%" align="center" valign="top">
<h2>🧹</h2>
<b><a href="plugins/tidy">tidy</a></b><br/>
<sub>7-track cleanup.<br/>HIGH-confidence only.</sub><br/><br/>
<code>/tidy:run</code>
</td>
<td width="33%" align="center" valign="top">
<h2>🎭</h2>
<b><a href="plugins/e2e">e2e</a></b><br/>
<sub>Describe the flow.<br/>Get a passing spec.</sub><br/><br/>
<code>/e2e:write</code>
</td>
<td width="33%" align="center" valign="top">
<h2>🕸️</h2>
<b><a href="plugins/weave">weave</a></b><br/>
<sub>Authors Workflow<br/>scripts on demand.</sub><br/><br/>
<code>/weave:make</code>
</td>
</tr>
<tr>
<td width="33%" align="center" valign="top">
<h2>📖</h2>
<b><a href="plugins/webster">webster</a></b><br/>
<sub>Harvester-shaped docs<br/>that cite their sources.</sub><br/><br/>
<code>/webster:plan</code>
</td>
<td colspan="2"></td>
</tr>
</table>


<sub><img src="https://img.shields.io/badge/forge-4.4.1-1E88E5?style=flat-square" alt="forge 4.4.1"/> <img src="https://img.shields.io/badge/foundry-4.11.0-F57C00?style=flat-square" alt="foundry 4.11.0"/> <img src="https://img.shields.io/badge/foundry--mcp-1.10.0-F57C00?style=flat-square" alt="foundry-mcp 1.10.0"/> <img src="https://img.shields.io/badge/crucible-0.1.0-F57C00?style=flat-square" alt="crucible 0.1.0"/> <img src="https://img.shields.io/badge/crew-0.2.0-6D4C41?style=flat-square" alt="crew 0.2.0"/> <img src="https://img.shields.io/badge/adhoc-0.3.0-43A047?style=flat-square" alt="adhoc 0.3.0"/> <img src="https://img.shields.io/badge/tldr-0.1.0-43A047?style=flat-square" alt="tldr 0.1.0"/> <img src="https://img.shields.io/badge/holmes-0.1.0-00897B?style=flat-square" alt="holmes 0.1.0"/> <img src="https://img.shields.io/badge/ux--review-0.1.0-00897B?style=flat-square" alt="ux-review 0.1.0"/> <img src="https://img.shields.io/badge/damu-0.2.0-00897B?style=flat-square" alt="damu 0.2.0"/> <img src="https://img.shields.io/badge/tidy-0.1.0-6D4C41?style=flat-square" alt="tidy 0.1.0"/> <img src="https://img.shields.io/badge/e2e-0.1.0-6D4C41?style=flat-square" alt="e2e 0.1.0"/> <img src="https://img.shields.io/badge/weave-0.1.0-6D4C41?style=flat-square" alt="weave 0.1.0"/> <img src="https://img.shields.io/badge/webster-0.11.0-6D4C41?style=flat-square" alt="webster 0.11.0"/></sub>


</div>

---

## ⚡ Quick start

```bash
claude plugin marketplace add alphabravo-oss/guild
for p in forge foundry adhoc tldr; do claude plugin install "$p@guild"; done
```

> [!WARNING]
> `claude plugin install` takes **one** plugin. Extra arguments are discarded silently: no error,
> no warning, exit 0. Install in a loop, or one command per plugin.

```bash
/forge:plan "workloads page listing running pods with status and logs"   # → spec.md
/foundry:start pioneer --spec docs/specs/workloads-page.md               # → shipped
```

> [!NOTE]
> `adhoc` and `tldr` need no setup — they are live on your next session. `foundry` bundles its own MCP server since 4.7.0 — installing the plugin is all the wiring there is. If you ever registered the old standalone entry, remove it (`claude mcp remove foundry`): it starts a second server that silently defeats the `model` option.

> [!TIP]
> Forge and foundry each declare a `model` option that moves their config-steerable agents onto a different model — per session:
>
> ```bash
> claude --settings '{"pluginConfigs": {"foundry@guild": {"options": {"model": "fable"}}, "forge@guild": {"options": {"model": "fable"}}}}'
> ```
>
> or persistently via the same `pluginConfigs` block in `~/.claude/settings.json`. Which agents each option reaches is in the plugin READMEs under **Model selection**.

<details>
<summary><b>Install the other nine</b></summary>

```bash
for p in crucible holmes ux-review damu crew tidy e2e weave webster; do
  claude plugin install "$p@guild"
done
```

`e2e`, `ux-review`, and `damu:remediate` drive a real browser and want the Playwright MCP server.

</details>

---

## 🔄 How it works

```mermaid
flowchart LR
    idea([💡 Your idea]) --> F
    F["📐 forge<br/>survey · research<br/>interview · spec<br/>adversarial review"] --> S[("spec.md")]
    S --> Y
    Y["🏭 foundry<br/>decompose · validate<br/>cast · inspect<br/>grind · assay"] --> D([🚀 Shipped])
    Y -.->|defects| Y

    style idea fill:#43A047,stroke:#1B5E20,color:#fff
    style F fill:#1E88E5,stroke:#0D47A1,color:#fff
    style S fill:#37474F,stroke:#263238,color:#fff
    style Y fill:#F57C00,stroke:#E65100,color:#fff
    style D fill:#8E44AD,stroke:#4A148C,color:#fff
```

**Forge does not build. Foundry does not interview.** They talk through one artifact — the spec — and every mechanism in the repo exists to keep it intact across that handoff.

---

## 💡 Why Guild

<table>
<tr><th align="left" width="50%">Most AI coding tools</th><th align="left" width="50%">Guild</th></tr>
<tr><td>Ask and build in one breath</td><td>Interview → spec → autonomous build</td></tr>
<tr><td>Planner rewrites the prompt for the executor</td><td><b>Plans are prompts</b> — authored once, verbatim everywhere</td></tr>
<tr><td>Drift prevention is prose discipline</td><td>Drift prevention is <b>mechanical</b> — byte-identical propagation, verified at a gate</td></tr>
<tr><td>"Looks done" = tests pass</td><td>11 validation dimensions + 8 inspect streams + fresh-eyes assay</td></tr>
<tr><td>User approves every phase</td><td>Autonomous from <code>/foundry:start</code> to done</td></tr>
<tr><td>Claims a file says X</td><td>Hook <b>blocks the response</b> if Claude never Read it</td></tr>
<tr><td>Subagent summary counts as "verified"</td><td>Only direct Read/Grep this turn counts</td></tr>
<tr><td>A UI review reads the source</td><td>ux-review drives the running app; damu measures rendered CSS</td></tr>
<tr><td>"Be concise" lives in CLAUDE.md and decays</td><td>tldr loads the ruleset at the runtime, every session</td></tr>
</table>

---

## 📚 Deeper

<details>
<summary><b>📐 forge — the spec engine</b></summary>

<br/>

4 parallel Explore agents survey your codebase before a single question is asked. Then targeted ecosystem research, then an `AskUserQuestion`-driven interview that captures facts you state in passing (*"we're on Postgres 14"*) as tagged `A-AUTO-NNN` entries.

Output is a spec with typed `## Global Invariants` / `## State Transitions` / `## Contracts` tables — every row citing `[from A-NNN]` — that propagate byte-identical into every Foundry casting.

| Phase | Does |
|---|---|
| `R-pre` | Classify brownfield / greenfield / cosmetic |
| `R0` | 4 Explore agents map architecture, data, surface, infra |
| `R1` → `R1.5` | Synthesize `reality.md`, then ecosystem research |
| `R2` | Interview + implicit-fact extraction (brownfield adds node-by-node flow confirmation) |
| `R3` → `R3.5` | Write spec, then an adversarial reviewer hunts ambiguity |
| `R4` | `validate-spec.py` — citations, coverage, verbatim fidelity |

**Verbatim-fidelity gate:** Locked requirements must quote you word-for-word with a transcript citation, or the spec refuses to finalize.

→ [Full docs](plugins/forge)

</details>

<details>
<summary><b>🏭 foundry — the build engine</b></summary>

<br/>

Decompose authors every teammate prompt **once**, freezes it, and validates it against the spec. The lead is a router, not an interpreter — it never re-drafts or paraphrases.

| Phase | Does |
|---|---|
| `F0` → `F0.6` | Research, codebase map, pattern map (finds real analogs to mirror) |
| `F0.5` → `F0.7` | Decompose into castings, verify no user answer got dropped |
| `F0.9` | **11-dimension gate — before any code is written** |
| `F1` | Parallel wave build; cited evidence **re-run server-side** |
| `F2` | Up to **8 inspect streams** in parallel |
| `F3` | Grind every defect to zero, re-inspect; a class that recurs three cycles running **escalates to one structural packet** |
| `F4` | Fresh-eyes assay with stub detection |
| `F5` | `--temper` — micro-domain stress testing, same grind loop |
| `F5.5` | `--nyquist` — regression tests for every verified requirement that lacks one |

**The 8 streams:** `TRACE` (LSP upstream wiring) · `FLOW_TRACE` (downstream) · `PROVE` (spec-to-code) · `RESEARCH_AUDIT` · `COVERAGE_DIFF` · `SIGHT` (browser) · `TEST/PROBE` · `TEST_OBSERVATIONS` (derives property tests from the contracts table, runs them **code-blind**)

> [!IMPORTANT]
> Hand-fabricated evidence cannot pass. When a teammate cites `$ pytest tests/foo.py`, Foundry re-runs it server-side in a clean worktree and stamps provenance — each artifact binds to a specific requirement ID, and a "volatile" pattern that would erase the verdict is refused rather than redacted.

**Findings have two channels.** Behaviour and security findings are defects. Comment prose — a stale line hint, a count, a direction word — goes to a typed observations ledger instead, and the server refuses to file it the other way. A security-property claim can never be demoted; trying trips a persisted audit signal.

**`--max-cycles N` caps the loop, and the cap is not a refusal.** The default `0` is unbounded. The transition that would open a GRIND cycle past the cap succeeds into a named `HALTED` state, and the report is generated as part of that same transition, naming every open defect at every tier: every open `LIVE` one, and every open `LATENT` and `HARDENING` one in its own `latent_backlog` and `hardening_backlog` section. `HALTED` is not `DONE` — it is a run that stopped with open work, and it says so.

**Building foundry with foundry — launch with `--plugin-dir`.** A run whose target is the foundry plugin is started as `claude --plugin-dir <project_root>/plugins/foundry`, so the executing MCP server is the working tree and the fixes the run ships reach that same run. F0 refuses a self-targeting run whose server does not match the tree, naming the launch command; a run targeting anything else compares nothing. No run ever switches servers mid-flight.

→ [Full docs](plugins/foundry)

</details>

<details>
<summary><b>🧭 adhoc + ⚡ tldr — the always-on layer</b></summary>

<br/>

Both fire on every conversation by default. A rule you have to remember to turn on is a rule you get on the turns you were already being careful.

```mermaid
flowchart LR
    S([New session]) --> T["⚡ tldr SessionStart<br/>loads the 10-rule ruleset"]
    T --> U([Prompt])
    U --> A["🧭 adhoc preamble<br/>read floor · alternatives · blast radius"]
    A --> C[Claude drafts]
    C --> H{"🧭 Stop hook"}
    H -->|"cited a file<br/>it never Read"| B["🚫 BLOCKED"]
    B --> C
    H -->|verified| O([✅ Delivered])

    style T fill:#43A047,stroke:#1B5E20,color:#fff
    style A fill:#43A047,stroke:#1B5E20,color:#fff
    style B fill:#C62828,stroke:#8E0000,color:#fff
    style O fill:#2E7D32,stroke:#1B5E20,color:#fff
```

| | 🧭 adhoc | ⚡ tldr |
|---|---|---|
| Governs | How Claude *thinks* | The *shape* of the output |
| Prevents | A confident, wrong answer | A right answer you must mine for |
| Off | `/adhoc:off` `/adhoc:casual` | `/tldr:off` `/tldr:verbose` |

**adhoc** treats *probably / likely / typically* as tripwires for unverified inference, refuses comments as evidence, and runs an iterative critic gate (Haiku rounds 1–2, Opus 3–5) that checks whether the last round's flags were actually fixed or just rephrased.

**tldr** keeps all 10 rules as prose in one file — [`rules/ruleset.md`](plugins/tldr/rules/ruleset.md) — so tuning your house style is a markdown edit, not a code change.

→ [adhoc docs](plugins/adhoc) · [tldr docs](plugins/tldr)

</details>

<details>
<summary><b>🔍 The reviewers — report only, never edit</b></summary>

<br/>

**🔍 holmes** runs 7 blind design lenses (package proliferation, missed sharing, helper sprawl, cohesion, accretion markers…) as separate agents, then a **skeptic tries to refute each finding as intentional** so plausible-but-wrong smells get dropped. An empty result is a valid honest outcome.

**👁️ ux-review** launches the app in a real browser and uses it. A code read structurally cannot find an overlay anchored to the viewport center instead of the point you clicked, or a feature that works on the default screen and silently breaks in another state. Sweeps adversarial states — empty, error, offline, slow, denied, first-run — plus keyboard, screen reader, and mobile.

**🎨 damu** catalogs ~19 AI-UI tells — font chaos, purple-on-near-black, neon gradient borders, recycled rocket icons, everything-is-a-24px-card. `prevent` gives you an anti-slop ruleset for CLAUDE.md; `remediate` measures the rendered CSS and reports per-page verdicts. Governing rule: **every tell is sometimes correct**, so HIGH confidence requires the choice to look unmotivated *and* uniform.

→ [holmes](plugins/holmes) · [ux-review](plugins/ux-review) · [damu](plugins/damu)

</details>

<details>
<summary><b>🛠️ The workers — hand them a job</b></summary>

<br/>

**🤖 crew** — five agents investigate, plan, execute, verify. SSH into boxes, run terraform, debug deploys. The worker self-switches modes instead of being five personas; critic and fresh-eyes must produce **evidence ledgers of commands they actually ran**. `/crew:goal` loops unattended until the goal is met.

**🧹 tidy** — 7 parallel read-only tracks, changes ranked HIGH/MEDIUM/LOW, **auto-applies HIGH-confidence only**, atomic commits, refuses a dirty tree. Never merges code that merely *looks* similar.

**🎭 e2e** — `init` scaffolds Playwright, `write` drives the browser and emits a green spec, `crawl` covers every route, `matrix` does routes × roles, `audit` reads `trace.zip` and triages by root cause.

**🕸️ weave** — classifies your task, picks the fan-out topology (pipeline by default, barrier only when a stage truly needs all prior results), and validates the script with `node --check` plus a 12-point audit before saving.

**📖 webster** — writes the docs you would publish, in a fixed Harvester-shaped tree, then proves them. Every page declares its content type, with sections from The Good Docs Project and quality checks drawn from the ISO/IEC/IEEE 26514 characteristics. A stack detector enumerates the real public surface with a `file:line` anchor on every entry (Next.js App Router and Pages API, Express, Fastify, Hono, FastAPI, Flask, Go mux, plus middleware, CLI binaries, package exports, and env vars read from code rather than only from `.env.example`). Reference material is extracted with the right tool instead of composed in prose, because a reference written from a code read is correct on the day and wrong by the next commit. A drift manifest records the git HEAD and every cited anchor, so a later audit says which pages your diff invalidated instead of re-reading all of them. Six gates decide done, and a gate that could not run reports `not_checked` rather than a pass.

→ [crew](plugins/crew) · [tidy](plugins/tidy) · [e2e](plugins/e2e) · [weave](plugins/weave) · [webster](plugins/webster)

</details>

<details>
<summary><b>🗄️ Where state lives</b></summary>

<br/>

| Plugin | State |
|---|---|
| foundry | `foundry-archive/{run}/` — every prompt, acceptance check, handoff, the defect ledger, the observations ledger, per-agent progress ledgers, and per-cycle stream roll-ups. Full audit trail; `migrate-archive.py` upgrades older archives in place. |
| forge | `docs/specs/{slug}/spec.md` + `flow-delta.json` on brownfield runs |
| adhoc / tldr | dotfiles in `~/.claude/` — survive `/clear` and compaction. adhoc logs every gate decision to `.adhoc-citations-log.jsonl` |
| crew | `.crew/runs/` for resume |
| tidy / damu | atomic commits — any applied change is one revert away |

</details>

<details>
<summary><b>🆕 What's new</b></summary>

<br/>

### foundry 4.11.0 — the loop stops making work for itself

The successor to 4.10.0's own build. `daring-orca` shipped 4.10.0 in 29 GRIND cycles and sealed `HALTED` with four defects still open — and much of what those cycles found was fallout the loop had produced itself: a fix in one casting quietly breaking a sibling nobody had dispatched, one finding re-filed as three because a probe that was never a spec requirement had no channel of its own, and an orchestrator that had been shaped cycle by cycle by the defect loop instead of by a design. Every item below narrows one of those. The run now measures its own fallout rather than absorbing it.

| Adds | Where |
|---|---|
| **`Foundry-Concern` — the cross-casting concern ledger** — a teammate whose fix reaches another casting's files records it against a target the manifest can resolve, and the lead closes it with a reason. `concerns.md` stays prose; the ledger is what the server reads. An open concern from the closing GRIND refuses the next INSPECT by id | `Foundry-Concern` · `concerns.json` · `_inspect_start_preconditions` |
| **Co-dispatch instead of a lone fix** — `Foundry-Tasks` emits, per task, the set of castings whose `requirement_ids` intersect the fix, under a **server-generated** alignment block naming the originating defects and each sibling's files. `Foundry-Directive` gets the same set from the ids the requirement-ID regex finds in its text | `Foundry-Tasks` · `Foundry-Directive` · `castings/manifest.json` |
| **One preconditions routine per transition token** — every `PHASE_TOKENS` member has exactly one `_<token>_preconditions`, and the transition makes no other read and adds no refusal of its own. `Foundry-Gate` reports that same checklist through `GATE_TO_TRANSITION`, so a gate and the transition it guards can no longer disagree. The cap arrives as a non-refusing `would_halt` fact the transition acts on | `transitions.py` · `gates.py` |
| **The orchestrator split — no facade** — the 1.9.0 monolith is deleted rather than shimmed, and every importer rewritten: `tools/orchestration/` is fourteen modules with their own test package, and the verifier set narrows to the gates, transitions, width and sweep modules. A stdlib-only pytest guard holds the import graph acyclic, keeps each symbol defined once, and keeps verifier modules out of the presentation layer | `tools/orchestration/` · `tests/orchestration/test_module_boundaries.py` |
| **`HARDENING` tier** — a third channel for a probe the stream drove *itself* and saw fail, with no spec row behind it. It does not block a gate, it gets its own report backlog, and it is never re-tiered in place: a promotion is a new filing that cites it through `supersedes`. A `HARDENING` filing carrying any `spec_ref` is refused at both doors | `vocab.DEFECT_TIERS` · both filing doors · `Foundry-Report` |
| **The lead halt door** — `Foundry-Phase(phase='halt')` ends a run deliberately on one of four reasons (`cap_reached`, `lead_ruling`, `spec_change_required`, `user_stop`) plus the lead's own text. `halt` is a full token with its own preconditions function and its own gate | `Foundry-Phase` · `Foundry-Gate` · `vocab.HALT_REASONS` |
| **Stream records replace; rosters persist** — a second `Foundry-Stream` for one `(stream, cycle)` REPLACES the first and names what it replaced, history kept, so totals can never exceed 100%. The verifying **agent** records; the lead confirms the record exists. `Foundry-Roster` fixes a stream's item list at first derivation and later cycles read it | `Foundry-Stream` · `Foundry-Roster` · `rosters/` |
| **Evidence commands linted at both doors** — an `# evidence-cmd:` that will not parse under `/bin/sh -n` is `BLOCKED` at the commit guard naming the log and the shell's own message, and refused *before execution* at every sweep crossing with `EVIDENCE_COMMAND_SYNTAX`. Linting at one door only is what let a broken command reach the corpus | `hooks/pre-commit-guard.sh` · `evidence.py` |
| **The run measures its own fallout** — `measure-run.py` reports `fallout_per_cycle` (findings that are fallout of an earlier fix) and `full_cycle_ratio` (FULL over total INSPECT cycles), each with a pass/fail verdict; defect records gained `fallout_of` and `supersedes` to feed it | `scripts/measure-run.py` · `Foundry-Report` |
| **Ownership is declared, not inferred** — each casting persists its `requirement_ids` at F0.5 instead of having ownership re-read out of prose at dispatch time, and F0.9 refuses a requirement spanning more than two castings without a recorded `split_reason` | `castings/manifest.json` · `Foundry-Validate-Castings` |
| **`Foundry-Team-Down` refuses a live hand-off** — tearing a GRIND team down while a defect dispatched this cycle is still open, with its file among the commits since the cycle baseline, is refused by id | `Foundry-Team-Down` · `handoffs.jsonl` |
| **`TEMPER_CANDIDATE` observations** — PROVE records a probe idea instead of filing it as a defect; TEMPER's roster is the open candidates plus its own micro-domains, and each is closed as driven — filed or clean | `Foundry-Observation` · `skills/prove` · `skills/temper` |
| **`/foundry:resume --max-cycles N`** — the resume path rewrites the persisted cap in the same locked write as the refreshed provenance, lowering a ceiling onto a run already moving. A negative cap is refused at the door rather than silently read as unbounded | `Foundry-Init` · `commands/resume.md` |
| **Archives keep reading** — `migrate-archive.py` takes a schema-3 archive to schema 4 idempotently: rollup totals rewritten to the LAST record with history kept, and defaults filled for `requirement_ids`, `split_reason`, `fallout_of`, `supersedes`, `concerns.json` and `rosters/` | `scripts/migrate-archive.py` |

### foundry 4.10.0 — the run knows when to stop

The successor to 4.9.0's own retrospective. `thunder-viper` shipped 4.9.0 in 22 GRIND cycles, eight of them after verification was already clean, and TEMPER never converged on its own — it had no stated end, so it ended when a human said so. Every item below exists to make a run terminate on evidence rather than on patience: findings now carry whether they were *observed* or merely *derived*, gates count only the observed ones, escalated defect families exit by a rule instead of a judgement call, and the run's own report is generated from its ledgers rather than written by the lead who is tired of it.

| Adds | Where |
|---|---|
| **A tier on every finding** — `LIVE` means the stream drove the door and saw the wrong result; `LATENT` means it derived the finding with no reachable instance and must say what it drove. A security-property claim can never be `LATENT`. 4.11.0 widened the set with `HARDENING` (see its row in the 4.11.0 table); `vocab.DEFECT_TIERS` holds the members | `Foundry-Defect` · `Foundry-Sync` · all four stream agents · temper |
| **Tier-aware gates** — `LIVE` and unknown-tier defects block; a `LATENT`-only backlog passes every gate and stays open, tracked, and named in the report | `inspect_clean` · ASSAY · TEMPER · NYQUIST · DONE |
| **Escalation exits mechanically** — two consecutive cycles drawing zero `LIVE` instances, or an exhausted two-pass structural budget; `CLEARED` persists its exit reason. Clearing ends escalation, never a defect | `Foundry-Tasks` · `escalation.json` |
| **`LATENT` fix lane** — a `LATENT` defect closes on a named regression test, without the adjacent-path declaration a `LIVE` fix still requires | `Foundry-Fix` |
| **Bounded lead-fix lane** — the lead may fix `LATENT` at any size and `LIVE` within one non-test file and 20 lines; the **server** measures it with `git show --numstat` and writes the `lead_fix` handoff | `Foundry-Fix` · `foundry_handoff.py` |
| **Server-side evidence sweep at the GRIND boundary** — every evidence log re-executes byte-identical at HEAD in a detached worktree, delta by default and whole-corpus before ASSAY / NYQUIST / DONE; a mismatch refuses the transition naming the log | `Foundry-Phase(inspect_start)` · `evidence.py` |
| **FULL vs DELTA INSPECT** — the transition that OPENS an INSPECT decides its width and records the rule that fired; `Foundry-Next` only reports it | `Foundry-Phase` · `state.json` `inspect_modes` |
| **Self-target preflight** — a run building foundry is launched with `claude --plugin-dir`, and F0 refuses when the executing server is not the working tree, naming the launch command | `Foundry-Init` |
| **Pointer dispatch** — spawn tools return a path and a sha256 instead of prompt text; the agent reads the file and states the hash, and acceptance refuses on mismatch | `Foundry-Spawn-Teammate` · `Foundry-Cast-Wave` |
| **Liveness-aware stall detector** — a waiting-on-N-agents notice while agents are running; a stall warning only when none are | `Foundry-Next` · `Foundry-Liveness` |
| **`Foundry-Spend`** — per-agent tokens and duration, rolled up per phase and per cycle. The lead pastes the numbers; **the server never parses a transcript**. A forgotten record is reported, never blocking | `Foundry-Spend` |
| **`Foundry-Report`** — `REPORT.md` and `report.json` generated from the run's ledgers across eleven required sections. The lead may append prose under a heading of their own, which the F6 seal carries verbatim into a trailing `Lead notes (carried by the seal)` section, but can never omit a generated section; `Foundry-Phase('done')` refuses a missing section | `Foundry-Report` |
| **`--max-cycles N`** — caps the verify-fix cycles. Reaching the cap **succeeds** into a named `HALTED` state, generating the report; `HALTED` is not `DONE` | `setup-foundry.sh` · `Foundry-Init` · `Foundry-Phase` |

### foundry 4.9.0 — the run stops manufacturing its own work

Built from two real runs' archives (`deployment-interconnect`, `interconnect-lifecycle-followups`) where ~70 of 137 defects were comment prose and the same defect class was re-found one axis at a time for five cycles. Every item is additive; nothing the loop catches got weaker.

| Adds | Where |
|---|---|
| Observation/defect split — comment prose has its own typed ledger; both filing doors refuse it as a defect; security and spec-behaviour claims can **never** be demoted (denylist + audit tripwire) | `Foundry-Defect` · `Foundry-Observation` · `Foundry-Sync` · all four stream agents · F0 seeded directive |
| `path#Symbol` cites — a resolvable symbol passes despite a stale `:line`; an unresolvable one is a defect; line drift is not a finding class | `Foundry-Accept-Casting` |
| Server-side cycle counter; escalation after three consecutive cycles of one class → **one structural packet**, with a directive override that reports its decision | `Foundry-Fix` · `Foundry-Tasks` · `Foundry-Next` |
| `Foundry-Fix` refuses without an adjacent-path statement and an adjacent-path test | `Foundry-Fix` · teammate GRIND protocol |
| Index-judging pre-commit guard shipped as an asset and installed into every target repo; pathspec-scoped commits; no-stash rule | `install-commit-guard.sh` · `Foundry-Init` |
| One vocabulary module — stream ids, defect types (incl. `PARTIAL`), sources, and requirement-ID families (`GI`/`OT`/`CT`/`ST`/`LR` were invisible before) | `schemas/vocab.py`, read by every door |
| Per-cycle stream roll-up (partial PROVE tranches accumulate; ≥95% judged once at streams-complete); `Foundry-Liveness` tells a slow agent from a dead one | `Foundry-Stream` · `Foundry-Liveness` |
| Evidence gate reachable over MCP (`casting_commit`), resolving the run's real spec; redactions may not erase a disagreement; volatile fields are a corpus-derived allowlist | `Foundry-Accept-Casting` · `evidence.py` |
| Every run artefact read through one guarded primitive; malformed ledgers, manifests, directives and planning state **refuse by name** at every door instead of raising or silently repairing — enforced by package-wide AST scans with plant and blind tests | MCP server, both shipped trees |
| `scripts/foundry.sh` retired; `migrate-archive.py` upgrades old archives idempotently; `measure-run.py` reports real cycle/defect-yield metrics | scripts |

Shipped by run `thunder-viper` (162 defects, 22 cycles, 1,917 tests). Its own retrospective — TEMPER did not converge on its own — is the next work request.

### Since v4.2.0

> [!NOTE]
> All eight are **verified by synthetic-fixture suite, not by ablation cohort.** Milestone-level proof of combined defect-rate drop is tracked as Phase 9 / RUN-01.

| ID | Adds | Where |
|---|---|---|
| `INTV-01` | Implicit environmental facts captured as `A-AUTO-NNN` | forge R2 |
| `TYPE-01` | Typed invariants / transitions / contracts tables, propagated byte-identical | forge R3 → foundry F0.5 |
| `TYPE-02` | `spec_format_version` frontmatter; legacy specs build unchanged | forge R4 |
| `EVID-01` | Cited evidence re-run server-side with provenance | foundry F1 |
| `EVID-02` | Each artifact binds to a requirement ID | foundry F1 |
| `PROBE-01` | Adversarial spec review before SPEC FORGED | forge R3.5 |
| `TEST-01` | 8th stream derives property tests from contracts, runs code-blind | foundry F2 |
| `INTENT-01` | Verifies no user answer got dropped in decomposition | foundry F0.7 |

Together they take the F0.9 gate from 9 dimensions to 11.

</details>

---

<div align="center">

**Update** — `claude plugin marketplace update guild`, then `claude plugin update <name>@guild`

**Contributing** — issues and PRs welcome. Adding a validation dimension, drift-prevention mechanism, or inspect stream? Open a discussion first — *plans are prompts* is load-bearing.

**License** — [MIT](./LICENSE)

<img src=".github/assets/wave.svg" width="100%" alt=""/>

<i>Forge plans. Foundry builds. adhoc keeps Claude honest. tldr says it in one line.<br/><b>You ship.</b></i>

</div>
