<p align="center">
  <b>foundry</b> — the autonomous build-verify-fix loop for Claude Code.<br/>
  <i>Forge plans. Foundry builds.</i>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/foundry-4.11.0-F57C00?style=flat-square" alt="foundry 4.11.0"/>
  <img src="https://img.shields.io/badge/foundry--mcp-1.10.0-F57C00?style=flat-square" alt="foundry-mcp 1.10.0"/>
  <img src="https://img.shields.io/badge/guild-pipeline-1E88E5?style=flat-square" alt="guild pipeline"/>
  <img src="https://img.shields.io/badge/Claude%20Code-plugin-8E44AD?style=flat-square" alt="Claude Code plugin"/>
  <img src="https://img.shields.io/badge/license-MIT-2E7D32?style=flat-square" alt="MIT license"/>
</p>

<p align="center">
  <a href="../../README.md">← back to the Guild marketplace</a>
</p>

---

## What it is

Foundry is a Claude Code plugin that takes a Forge-produced spec and runs an **autonomous build-verify-fix loop** until the feature is shipped or an error stops the run. There are no approval gates. No "is this what you wanted?" checkpoints. The Lead inside Claude Code reads the spec, decomposes it into castings, dispatches a pointer to each frozen teammate prompt, runs up to eight parallel verification streams, grinds defects to zero, then assays the result with fresh eyes against the original spec.

The discipline is the product. Every mechanism in Foundry exists to keep the spec intact across the build.

---

## Mental model

```mermaid
flowchart TB
    spec[(spec.md + flow-delta.json)] --> F0
    F0[F0 RESEARCH + map] --> F06
    F06[F0.6 PATTERN MAPPING] --> F05
    F05[F0.5 DECOMPOSE — V2 end-state OR V3 packet] --> F07
    F07[F0.7 INTENT-CARRIER] --> F09
    F09[F0.9 11-dimension VALIDATE] --> F1
    F1[F1 CAST — parallel waves + evidence re-exec] --> F2
    F2[F2 INSPECT — up to 8 streams]
    F2 -->|defects| F3[F3 GRIND]
    F3 --> F2
    F2 -->|clean| F4[F4 ASSAY — fresh-eyes]
    F4 --> F5{--temper?}
    F5 -->|yes| F5T[F5 TEMPER — micro-domain probe]
    F5T --> F55
    F5 -->|no| F55{--nyquist?}
    F55 -->|yes| F55N[F5.5 NYQUIST — regression tests]
    F55N --> F6
    F55 -->|no| F6
    F6[F6 DONE] --> shipped([Shipped feature])
```

---

## Phases

| Phase | What it does | Key tools |
|---|---|---|
| **F0 RESEARCH** | Per-domain `researcher` agents (sonnet, parallel); optional `codebase-mapper` extracts `MANDATORY_RULES.md` | `Foundry-Init` |
| **F0.6 PATTERN** | `pattern-mapper` finds analog files for every spec target; emits `PATTERNS.md` with `<analog_pattern>` and `<shared_patterns>` excerpts | — |
| **F0.5 DECOMPOSE** | Authors casting manifest + per-casting prompt files; **V2 end-state mode** for `flow-delta.json`-less specs, **V3 packet mode** for brownfield flow-delta runs | `Foundry-Spawn-Teammate` (background) |
| **F0.7 INTENT-CARRIER** | Verifies every transcript `A-NNN` answer survives into a casting prompt; `INTENT_DROPPED` blocks F0.9 (skipped on `< v2.1` specs) | `Foundry-Intent-Coverage` |
| **F0.9 VALIDATE** | 11-dimension mechanical gate; sub-checks 7e/7g/7h/7i/7j/7m verify byte-identical block propagation | `Foundry-Validate-Castings` |
| **F1 CAST** | Parallel wave-based building; `Foundry-Accept-Casting` re-runs cited evidence server-side and binds it to specific requirement IDs | `Foundry-Cast-Wave`, `Foundry-Accept-Casting` |
| **F2 INSPECT** | Up to 8 parallel verification streams (see below) | `Foundry-Sync` |
| **F3 GRIND** | Defects → casting-scoped tasks → fix → re-inspect | `Foundry-Tasks`, `Foundry-Fix` |
| **F4 ASSAY** | Four parallel `assayer` agents (opus); spec-before-code methodology | `Foundry-Verdict` |
| **F5 TEMPER** | Optional (`--temper`) — micro-domain stress testing | `Foundry-Stream` |
| **F5.5 NYQUIST** | Optional (`--nyquist`) — regression test generation for VERIFIED requirements | `Foundry-Stream` |
| **F6 DONE** | Shutdown all teammates, generate report, mark `done` | `Foundry-Phase("done")` |

---

## F2 INSPECT streams

| Stream | What it verifies | When it runs |
|---|---|---|
| **TRACE** | Upstream wiring: EXISTS → SUBSTANTIVE → WIRED → PLACED (LSP-powered, sonnet) | Always |
| **FLOW_TRACE** | Downstream wiring: PRODUCED → CONSUMES_UPSTREAM → SUBSTANTIVE → CHAIN_INTACT | Brownfield (`flow-delta.json` present) |
| **PROVE** | Spec-to-code citation verification with stub detection (opus) | Always |
| **RESEARCH_AUDIT** | Code honours every research recommendation from F0 | When research findings exist or Informational items appear in spec |
| **COVERAGE_DIFF** | 1:1 source → destination symbol check | MIGRATION specs only |
| **SIGHT** | Browser-based UI audit via Playwright (lead runs directly) | When the spec describes UI behavior |
| **TEST / PROBE** | Full test suite + API smoke (inline) | Always |
| **TEST_OBSERVATIONS (TEST-01)** | Spec-only test derivation: reads `## Contracts` table, generates Hypothesis property tests, runs them code-blind, emits `test_observations` report | When spec is `>= v2.1` and `## Contracts` table is non-empty |

Zero defects → F4. Any defect → F3 → F2 → F4 (full re-verify after every fix).

---

## What every casting prompt carries

The drift-prevention sextet — six frozen, byte-identical-across-the-run blocks every teammate sees:

```markdown
<mandatory_rules>           # CLAUDE.md / AGENTS.md / .cursorrules verbatim
<global_invariants>         # cross-cutting spec rules (from spec ## Global Invariants section)
<invariants>                # spec ## Global Invariants table — TYPE-01
<state_transitions>         # spec ## State Transitions table — TYPE-01
<contracts>                 # spec ## Contracts table — TYPE-01
<spec_requirements>         # this casting's spec slice (V2 only)
<analog_pattern>            # F0.6 pattern excerpts (Imports/Setup/Core/Error)
<shared_patterns>           # F0.6 cross-cutting patterns (auth, error, logging)
```

In V3 packet mode, `<spec_requirements>` is replaced by structural blocks: `<upstream_anchor>`, `<prerequisite_hops>`, `<this_hop>`, `<downstream_contract>`, `<self_check>`. The teammate has **no end-state framing** in attention — only the hop contract. This is V3's reversal of the failure mode where backward-fabrication causes endpoint-anchored plumbing hallucination.

---

## Slash commands

| Command | What it does |
|---|---|
| `/foundry:setup` | Install MCP server + verify Python prerequisites |
| `/foundry:start "<scope>" --spec PATH` | Start a build-verify-fix loop |
| `/foundry:status` | Show current foundry run status |
| `/foundry:resume [--max-cycles N]` | Resume an interrupted run. `--max-cycles N` **rewrites** the persisted cap in the same locked write as the refreshed provenance, lowering a ceiling onto a run that is already moving; the next GRIND door halts when `N` is below the cycle it would open. Same semantics as the `/foundry:start` row below |
| `/foundry:stop` | Gracefully stop the current run |
| `/foundry:help` | Show plugin help |

`/foundry:start` flags:

| Flag | Effect |
|---|---|
| `--spec PATH` | Spec file (required) |
| `--url URL` | Run against a URL surface (alternative to filesystem-only) |
| `--temper` | Enable F5 TEMPER stress testing |
| `--nyquist` | Enable F5.5 NYQUIST regression test generation |
| `--max-cycles N` | Cap the verify-fix cycles. Default `0` = unbounded. The phase transition that would open a GRIND cycle past the cap **succeeds** — it is not a refusal: the run's phase becomes `HALTED`, the report is generated as part of that transition naming every open `LIVE` and `LATENT` defect, and the next guidance call reports the halt and dispatches nothing. **`HALTED` is a named terminal state distinct from `DONE`** — a halted run stopped with open work |
| `--no-ui` | `--no-ui` declares that this run has no browsable UI, so the SIGHT browser audit is not part of it. It says nothing about banners — the display is not a UI the run audits |

### Building foundry itself — launch with `--plugin-dir`

A run whose TARGET is the foundry plugin must be started with:

```bash
claude --plugin-dir <project_root>/plugins/foundry
```

This loads the plugin in place for the session and wins over a same-named marketplace install, so the executing MCP server IS the working tree and the process fixes the run ships are available to that same run. `Foundry-Init` detects a self-targeting run by finding a `plugin.json` named `foundry` under the project root and then compares two things: that manifest's version against the version in the executing server's OWN `.claude-plugin/plugin.json`, resolved from the directory the server was imported from, and the project root's HEAD commit against that same directory's HEAD commit. The comparison is plugin manifest against plugin manifest — the MCP server's `__version__` is a third number, recorded as `server_version` and displayed, but never compared — and a commit git could not read is never treated as a match. Either mismatch **refuses at F0 with a named reason and the exact launch command**, before any run directory is written. A run that does not target foundry compares nothing and is never warned — its executing versions are simply recorded and displayed.

**A mid-run server switch is never attempted.** No run step calls `/reload-plugins`, rewrites `.mcp.json`, or installs a plugin mid-run. Prose and code a run ships take effect for the NEXT run, never the one that wrote them.

---

## Skills

The `skills/` directory carries the Lead's verification-stream methodology references:

- `skills/prove/` — spec-before-code citation verification methodology
- `skills/sight/` — Playwright-based UI audit methodology
- `skills/temper/` — micro-domain stress-testing methodology
- `skills/trace/` — LSP-anchored upstream wiring methodology

Foundry-Sight runs these as Claude Code skills directly during the corresponding INSPECT streams.

---

## Agents

| Agent | Phase | Model | Notes |
|---|---|---|---|
| `researcher.md` | F0 | sonnet | Per-domain research; parallel up to 4 |
| `research-synthesizer.md` | F0 | haiku | Merges 4+ research outputs into `SUMMARY.md` |
| `codebase-mapper.md` | F0 (optional) | haiku | Extracts MANDATORY_RULES + STACK + ARCHITECTURE + STRUCTURE + CONVENTIONS + INTEGRATIONS + CONCERNS |
| `flow-mapper.md` | F0 (V3 brownfield) | opus | Grounded `flow-graph.json` from LSP/grep |
| `pattern-mapper.md` | F0.6 PATTERN | opus | Maps every spec target to its closest analog; emits `PATTERNS.md` |
| `tracer.md` | F2 TRACE | sonnet | Three-level EXISTS → SUBSTANTIVE → WIRED |
| `flow-tracer.md` | F2 FLOW_TRACE | sonnet | Mirror of tracer for downstream verification |
| `assayer.md` | F2 PROVE / F4 ASSAY | opus | Spec-before-code + stub detection |
| `research-auditor.md` | F2 RESEARCH_AUDIT | haiku | Verifies code honours research |
| `coverage-diff.md` | F2 COVERAGE_DIFF | haiku | 1:1 MIGRATION check |
| `spec-test-deriver.md` | F2 TEST_OBSERVATIONS | opus | Code-blind Hypothesis test derivation from the `## Contracts` table |
| `test-observations-adjudicator.md` | F4 ASSAY | opus | Adjudicates the `test_observations` channel |
| `intent-carrier.md` | F0.7 INTENT | opus | A-NNN × casting coverage matrix |
| `nyquist-auditor.md` | F5.5 | sonnet | Regression test generation |
| `teammate.md` | F1 CAST / F3 GRIND | opus | Methodical implementation; CAST deliberates, GRIND stays surgical |

The `Model` column mirrors each agent's `model:` frontmatter, which is the single source of truth for which model an agent runs on. Which of these the `model` config option can steer is documented under [Model selection](#model-selection); the allocation of foundry's *orchestration* roles — the Lead itself, F0.5 DECOMPOSE — lives in `commands/start.md`'s MODEL ALLOCATION table.

The Lead never authors teammate prompts — F0.5 DECOMPOSE wrote them once, and F1/F3 dispatch a pointer to the frozen file rather than its text; the teammate reads it and states the sha256 back. The Lead is a router, not an interpreter.

---

## MCP server

The `mcp-server/` directory ships a Python MCP server (`foundry-mcp`) that backs every Lead-side tool call. Without it the slash command loads but the workflow cannot drive — `Foundry-Init`, `Foundry-Next`, `Foundry-Validate-Castings` etc. are all served from there.

**Since 4.7.0 the plugin declares this server itself** (`.claude-plugin/plugin.json` → `mcpServers`), so installing the plugin is all the wiring there is — no `claude mcp add`, no `.mcp.json` entry. The plugin-scope declaration is also what delivers the `model` option: `${user_config.model}` substitutes only there, arriving in the server's environment as `FOUNDRY_MODEL`.

**Upgrading from < 4.7.0 — remove the old project-scope entry.** Earlier versions registered the server in your project's `.mcp.json` (via `setup-prereqs.sh` or a manual `claude mcp add foundry ...`). A project-scope entry does **not** shadow the plugin-declared one — both servers start (verified on Claude Code 2.1.229), doubling the process cost and exposing a second `Foundry-*` tool surface whose environment lacks `FOUNDRY_MODEL`, which silently defeats the model option. Re-running `/foundry:setup` removes the stale entry for you; or remove it by hand with `claude mcp remove foundry` (project scope) or by deleting the `"foundry"` key from `.mcp.json`.

The server stores all run state under `foundry-archive/{run}/` in your project — castings, prompts, defects, handoffs, reports, every acceptance check. A full audit trail per build.

| Tool | When |
|---|---|
| `Foundry-Init` | F0: create the run |
| `Foundry-Next` | Every step: returns `YOUR NEXT CALL:` imperative |
| `Foundry-Gate` | Before phase transitions |
| `Foundry-Phase` | Mark phase transitions — including `phase='halt'`, the lead's deliberate end (see below) |
| `Foundry-Spawn-Teammate` | F0.5 / F1 / F3: dispatch block (path + sha256) for one casting's pre-authored prompt |
| `Foundry-Cast-Wave` | F1: one bulk call returning the dispatch block for every casting in a wave |
| `Foundry-Validate-Castings` | F0.9: 11-dimension validate |
| `Foundry-Intent-Coverage` | F0.7: A-NNN coverage check |
| `Foundry-Spec-Hash` | Before acceptance: fresh hash forces spec re-read |
| `Foundry-Accept-Casting` | F1: re-run cited evidence + bind to requirement IDs |
| `Foundry-Handoff` | Record every phase / artifact transition |
| `Foundry-Defect` / `Foundry-Sync` / `Foundry-Tasks` / `Foundry-Fix` | F2 / F3 defect lifecycle |
| `Foundry-Concern` | F1 / F3: a teammate whose fix reaches ANOTHER casting's files records it here, and the lead closes it with a reason. `concerns.md` keeps the prose; this is the ledger the server acts on — an open concern from the closing GRIND refuses `Foundry-Phase(phase='inspect_start')` by id, and `Foundry-Tasks` marks it dispatched when the co-dispatch set reaches its target |
| `Foundry-Verdict` | F4 ASSAY verdicts |
| `Foundry-Coverage` | Traceability matrix |
| `Foundry-Roster` | F2, first derivation: persists a stream's item list to `rosters/<stream>.json` so later cycles read it instead of re-deriving a different one. A second write is refused unless `revise=true` carries a reason |
| `Foundry-Stream` | F2: the verifying **agent** records its own `(stream, cycle)` counts — never the lead, which would assert numbers it did not measure. A later record for the same pair REPLACES the earlier one and names what it replaced, with the history kept, so a cycle carries one account of one run and a total can never exceed 100% |
| `Foundry-Context` | Reload state after compaction |
| `Foundry-Team-Up` / `Foundry-Team-Down` | Teammate lifecycle around CAST + GRIND waves. Team-Down is **refused** while a defect dispatched this cycle is still open and its file appears in the commits since the cycle baseline, named by id |

### Ending a run on purpose — `Foundry-Phase(phase='halt')`

A run does not only end by finishing. `Foundry-Phase` takes a `halt` token that ends it deliberately, and the reason is a closed vocabulary — `schemas/vocab.py`'s `HALT_REASONS`, four members:

| Reason | When |
|---|---|
| `cap_reached` | the GRIND door found the persisted `max_cycles` below the cycle it was about to open. The only member a transition writes on its own |
| `lead_ruling` | the lead stopped the run deliberately |
| `spec_change_required` | the run cannot converge without a spec change, so continuing would grind against a target that is itself wrong |
| `user_stop` | the user asked for it |

The member is what the report and `measure-run.py` group on; the lead's own free text rides alongside it and says why *this* run ended, which no closed set can carry. Neither substitutes for the other.

**`HALTED` with a named backlog is a successful end, not a failure.** The halt is a transition that succeeds: the phase becomes `HALTED`, the report regenerates with every open `LIVE`, `LATENT` and `HARDENING` defect named in it, and `phase_history` gains a `HALTED` row. It is refused only on the three things `Foundry-Gate(phase='halt')` will report first — a reason outside the four, a team still registered, or a run already halted. **`HALTED` is a named terminal state distinct from `DONE`**: a halted run stopped with open work, and the report says what.

---

## Tests

```bash
cd plugins/foundry/mcp-server && uv run --with pytest pytest
```

`uv run` is required, not `uvx pytest` — the suite imports `foundry_mcp.server`, which imports `mcp`, so it needs the project's declared dependencies on the path. `uvx` builds an isolated pytest environment without them and two tests fail on `ModuleNotFoundError`.

Current baseline: **259 passed + 1 skipped** (synthetic-fixture suite covering every MCP tool's parsers, schemas, and handoff state). The skipped test is the `measure-run` planning-cohort gate, which needs real-run stubs not carried in this checkout. The empirical cross-cohort matrix (`measure-run.py`) ships as a separate consolidation tooling under `plugins/foundry/scripts/measure-run.py` (397 LOC, stdlib-only) for future milestone-level RUN-01 closure.

---

## Model selection

Foundry declares one `userConfig` option, `model`, in `.claude-plugin/plugin.json`. It moves a curated subset of agents onto a different model without editing a single frontmatter pin.

| Property | Value |
|---|---|
| Option key | `model` |
| Accepted values | `opus`, `sonnet`, `haiku`, `fable`, `inherit` |
| Any other value | Refused, with a message naming the accepted set |
| Unset | No model parameter is emitted at any spawn. Every agent's frontmatter pin governs and behaviour is identical to today |

Set it per plugin, under `pluginConfigs[<plugin-id>].options`. For one session, pass it inline:

```bash
claude --settings '{"pluginConfigs": {"foundry@guild": {"options": {"model": "fable"}}}}'
```

To persist it, put the same `pluginConfigs` block in `~/.claude/settings.json`. The sources honored are, in precedence order: managed settings, `--settings`, then user settings — project and local settings are ignored for it. The `${user_config.model}` substitution happens when the plugin's MCP server launches, so a change takes effect on the next session start. (Both forms verified live; the flat shape without the `options` level silently delivers nothing — the server sees an empty `FOUNDRY_MODEL`.)

**The option is declared per plugin.** Foundry and Forge each declare their own identically-named `model` option, and there is no shared cross-plugin store. Set it once under `foundry@guild` and once under `forge@guild` if you want both steered.

### What the option reaches

| Agent | Baseline pin | Steerable by `model`? |
|---|---|---|
| `foundry:teammate` | opus, `effort: xhigh` | Yes |
| `foundry:flow-mapper` † | opus, `effort: high` (was sonnet) | Yes — but by **forge's** option, not this one |
| `forge:spec-reviewer` ‡ | opus, `effort: high` (was sonnet) | Yes — but by **forge's** option, not this one |
| `foundry:assayer` | opus, `effort: max` | No — fixed baseline |
| `foundry:intent-carrier` | opus, `effort: max` | No — fixed baseline |
| `foundry:test-observations-adjudicator` | opus, `effort: max` | No — fixed baseline |
| `foundry:pattern-mapper` | opus (was sonnet) | No — fixed baseline |
| `foundry:spec-test-deriver` | opus, `effort: high` (was sonnet) | No — fixed baseline |

† Not by foundry's own option. `foundry:flow-mapper` ships in foundry, but foundry never spawns it — its only spawn site is `/forge:plan`'s V3 brownfield R0 flow-map step, so the value that steers it comes from **forge's** `model` option, set under `forge@guild`. Foundry's MCP server carries `foundry:flow-mapper` in its steerable set so the policy names foundry's full membership in one place, but it has no spawn site to apply it at. **Set only `foundry@guild` and flow-mapper stays on its frontmatter pin, with no diagnostic** — an option that reaches nothing looks exactly like one that was never set. If you run `/forge:plan` in brownfield mode and want flow-mapper steered, set the option under `forge@guild` too.

‡ Not by foundry's own option either. `forge:spec-reviewer` ships in forge, and its only spawn site is `/forge:plan`'s R3.5 spec-review step, which substitutes **forge's** `model` option into the command body. Foundry's MCP server carries no forge agent in its steerable set at all, so nothing set under `foundry@guild` can reach it. Set the option under `forge@guild`.

**Which option steers which agent.** Each of the three is reached by exactly one plugin's option, because each has exactly one spawn site: `foundry:teammate` spawns only from foundry's MCP server, so **foundry's** option under `foundry@guild` steers it; `foundry:flow-mapper` (V3 brownfield R0) and `forge:spec-reviewer` (R3.5) both spawn from `/forge:plan`, so **forge's** option under `forge@guild` steers both. Setting one plugin's option never moves an agent the other plugin spawns, and the plugin an agent *ships* in does not decide which option reaches it — the plugin that *spawns* it does.

Every other agent keeps the model it ships with and is not reachable by the option at any setting: foundry's `tracer`, `flow-tracer`, `nyquist-auditor` and `researcher` stay sonnet, `forge:researcher` stays sonnet, and foundry's four haiku agents — `codebase-mapper`, `coverage-diff`, `research-auditor`, `research-synthesizer` — stay haiku. **No agent in any plugin other than foundry and forge changes model at any setting of this option.**

### How the value is delivered

Frontmatter pins are always literal. An agent declaring `model: ${user_config.model}` fails at spawn time — the placeholder reaches the model resolver unsubstituted — so the option can never rewrite a pin. Each pin is the floor, and the configured value is applied at spawn time on top of it.

Foundry's manifest passes `${user_config.model}` into the MCP server declaration's environment as `FOUNDRY_MODEL`. The MCP server validates it against the accepted set and emits it in the `agent_config` it already returns to the Lead, **omitting the `model` key entirely when the value is empty** — an unset option substitutes as an empty string rather than as an absent variable, so the omission is what makes "unset" indistinguishable from "never implemented". The MCP server is the single owner of foundry's model policy: the Lead uses the model the MCP tells it to use rather than deciding one itself.

### If you cannot reach the configured model

A blocked model does not fail the spawn. Claude Code checks the value against your organisation's `availableModels` allowlist; for a blocked family alias it runs the subagent on the newest permitted version of that family, and for any other blocked value — or when the allowlist permits no version of the family at all — it runs the subagent on the **inherited model** instead, warning in interactive sessions and naming both models.

`fable` in particular is a Covered Model with mandatory 30-day retention and is **not available under Zero Data Retention**, so ZDR-bound consumers get the inherited model rather than a failed run. Foundry stays fully usable without `fable`.

---

## What's new

### foundry 4.11.0 — the loop stops making work for itself

4.10.0 taught a run when to stop. Building it showed what a run does *until* it stops: `daring-orca` shipped it in 29 GRIND cycles and sealed `HALTED` with four defects still open, and a large share of what those cycles found was the loop's own fallout — a fix in one casting breaking a sibling nobody had dispatched, one finding re-filed as three because no channel existed for a probe that was never a spec requirement, and a 15,000-line orchestrator shaped cycle by cycle by the defect loop rather than by a design. This release is about that: work the machine manufactures for itself. Concerns become a ledger the server can act on instead of prose nobody joins; a fix dispatches to every casting it reaches rather than to the one it was filed against; each transition token gets exactly one routine that decides it; and the monolith is split, with a guard that keeps it split. The sections above document each mechanism in place; the table below maps what shipped to where it lives.

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

This release is about how a run *ends*. `thunder-viper` shipped 4.9.0 in 22 GRIND cycles, eight of them after verification was already clean, and TEMPER had no stated end — it stopped when a human said so. Every addition below moves a stopping decision out of judgement and onto evidence: a finding records whether it was *observed* or merely *derived*, gates count only the observed ones, an escalated defect family exits by a rule instead of a verdict call, and the run's report is generated from its own ledgers rather than written by the lead who is tired of it. The sections above document each mechanism in place; the table below is the map from what shipped to where it lives.

| Adds | Where |
|---|---|
| **`LIVE` / `LATENT` tier on every finding** — `LIVE` means the stream drove the door and saw the wrong result; `LATENT` means it derived the finding with no reachable instance and must say what it drove. A security-property claim can never be `LATENT` | `Foundry-Defect` · `Foundry-Sync` · all four stream agents · temper |
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

---

## Why Foundry does not interview

The split is load-bearing. A builder that also interviews is biased toward designing castings whose shape it knows how to ship. A builder that consumes a frozen spec produced elsewhere just *implements what the spec says*. Foundry is engineered to be a worse interviewer than it is a builder — by construction, it cannot ask questions, so it doesn't try.

What Forge writes is what Foundry reads, byte for byte. Every `[from A-NNN]` citation, every `Locked:` quote, every typed-table row, every implicit-fact tag survives the trip.

Forge plans. Foundry builds.
