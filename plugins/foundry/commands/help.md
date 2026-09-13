---
description: "Explain the Foundry workflow and available commands"
---

# Foundry Help

Please explain the following to the user:

## What is Foundry?

**Forge plans. Foundry builds.**

Foundry is an autonomous build-verify-fix loop for Claude Code. Give it a Forge spec; it decomposes the spec into castings with pre-authored teammate prompts, builds in parallel waves, runs multi-stream verification, grinds defects to zero, then assays the result with fresh eyes — all without approval gates.

## How it works

```
F0     RESEARCH   →  F0.5  DECOMPOSE  →  F0.9  VALIDATE  →  F1  CAST
                                                              ↓
                                                            F2  INSPECT  ←──┐
                                                              ↓             │
                                                       defects → F3 GRIND ──┘
                                                              ↓
                                                       clean → F4 ASSAY
                                                              ↓
                                                  F5 TEMPER (optional, --temper)
                                                              ↓
                                                  F5.5 NYQUIST (optional, --nyquist)
                                                              ↓
                                                            F6  DONE
```

### Phases

| Phase | What happens |
|---|---|
| **F0 RESEARCH** | Per-domain researcher agents investigate how to build. Optional `codebase-mapper` extracts conventions + mandatory rules. |
| **F0.5 DECOMPOSE** | Authors the casting manifest AND the complete teammate prompt for each casting, from the spec as source of truth. |
| **F0.9 VALIDATE** | 9-dimension mechanical gate: requirement coverage, completeness, dependency correctness, key links, scope sanity, research integration, prompt fidelity (with `<global_invariants>` and `<mandatory_rules>` propagation), migration coverage, spec structure. |
| **F1 CAST** | Parallel wave-based building. Lead is a router — never re-drafts teammate prompts. |
| **F2 INSPECT** | Up to 7 parallel verification streams (see below). |
| **F3 GRIND** | Every defect becomes a casting-scoped task. Teammates fix, Foundry re-verifies. No deferrals. |
| **F4 ASSAY** | 4 fresh-eyes agents read spec FIRST, form expectations, THEN read code. Catches stubs and hollow handlers. |
| **F5 TEMPER** | Optional (`--temper`). Micro-domain stress testing per filesystem domain. |
| **F5.5 NYQUIST** | Optional (`--nyquist`). Generates regression tests for VERIFIED requirements that lack coverage. |
| **F6 DONE** | Shutdown, report, commit. |

### Verification streams (F2 INSPECT)

| Stream | Method | Checks |
|---|---|---|
| **TRACE** | Serena LSP | Upstream wiring: EXISTS → SUBSTANTIVE → WIRED → PLACED |
| **FLOW_TRACE** | Serena LSP | Brownfield only. Downstream wiring: PRODUCED → CONSUMES_UPSTREAM → SUBSTANTIVE → CHAIN_INTACT |
| **PROVE** | Spec-before-code | Every requirement has cited code evidence; stub detection; architectural placement check |
| **RESEARCH_AUDIT** | Research compliance | Code honors every recommendation captured during F0 research |
| **COVERAGE_DIFF** | 1:1 source/dest diff | MIGRATION specs only — every source symbol has a destination |
| **SIGHT** | Playwright | UI renders, buttons work, no console errors |
| **TEST / PROBE** | Test suite + smoke | All tests pass; APIs respond; smoke flows complete end-to-end |

## Drift prevention

Three frozen, byte-identical blocks ride in every casting prompt:

- `<mandatory_rules>` — full CLAUDE.md / AGENTS.md / .cursorrules imperatives
- `<global_invariants>` — cross-cutting spec rules (auth, validation, security, architectural placement)
- `<spec_requirements>` — the casting's specific spec slice (V2) OR `<upstream_anchor>`/`<this_hop>`/`<downstream_contract>` (V3 brownfield)

F0.9 mechanically verifies byte-identical propagation across every casting. The lead at F1/F3 calls `Foundry-Spawn-Teammate` or `Foundry-Cast-Wave` and gets back a **dispatch pointer** — the prompt file's path and its sha256, never its text — which it passes to the Agent tool verbatim; the teammate reads the frozen file itself and states the hash back, and acceptance refuses on a mismatch. No re-drafting, no paraphrasing, and nothing for the lead to re-type.

## Commands

### `/foundry:start <SCOPE> [OPTIONS]`

Start a new build run.

```
/foundry:start "user auth"  --spec docs/specs/auth.md
/foundry:start "dashboard"  --spec docs/specs/dashboard.md --url http://localhost:3000
/foundry:start "api"        --spec docs/specs/api.md --temper --nyquist
```

**Options:**
- `--spec <path>` — Forge spec to build from (strongly recommended)
- `--url <url>` — base URL for SIGHT (Playwright UI audit)
- `--temper` — enable F5 micro-domain stress testing
- `--nyquist` — enable F5.5 regression test generation
- `--max-cycles <n>` — cap on F2/F3 verify-fix loops (default `0`, unbounded)
- `--no-ui` — skip SIGHT

**`--max-cycles N` and the HALTED state.** `N` caps the verify-fix cycles; the default `0` is
unbounded. The phase transition that would open a GRIND cycle beyond the cap **succeeds** — it
is a successful transition, not a refusal. The run's phase becomes `HALTED`, the report is
generated as part of that transition naming every open defect at every tier — the blocking
`LIVE` ones, and the `LATENT` and `HARDENING` ones the run carried rather than blocked on, each
in its own `latent_backlog` and `hardening_backlog` section — and the next guidance call reports
the run halted and issues no further dispatch. **`HALTED` is a named
terminal state distinct from `DONE`:** a halted run stopped with open work, and calling it
"finished" is the one reading the state exists to prevent.

**Building foundry itself — start the session with `--plugin-dir`.** A run whose TARGET is the
foundry plugin must be launched as:

```bash
claude --plugin-dir <project_root>/plugins/foundry
```

That makes the executing MCP server the working tree, so process fixes the run ships are
available to that same run. A self-targeting run is refused at F0 with a named reason and the
exact launch command when either of two comparisons disagrees: the version in the working
tree's `plugin.json` against the version in the executing server's OWN `plugin.json`, and the
HEAD commit of the project root against the HEAD commit of the directory the server was
imported from. It is plugin manifest against plugin manifest — the MCP server's `__version__`
is recorded and displayed but never compared — and an unreadable commit is never a match. A
run that does not target foundry compares nothing and is never warned. **A mid-run server switch is never
attempted** — no step calls `/reload-plugins`, rewrites `.mcp.json`, or installs a plugin
mid-run. Prose and code a run ships take effect only in a server started after they land, never in
the one that wrote them: when a crossing depends on changed server code or agent or skill prose —
and before DONE, whenever either changed — the run parks for you to relaunch rather than running
on the old code.

### `/foundry:resume`

Resume an interrupted run. Lists runs by phase + cycle; pick one to continue.

### `/foundry:status`

Show current run status — phase, cycle, defects, stream coverage.

### `/foundry:stop`

Stop the active run. It ends `HALTED` with reason `user_stop` and your words as its text, and the
report is regenerated and sealed in the same step. After CAST this is how a run ends early — the
lead cannot end one on its own ruling. `HALTED` is terminal: carry the remaining work into a NEW
run.

### `/foundry:setup`

Install prerequisites: Foundry MCP server, Playwright MCP, Serena MCP.

### `/foundry:update`

Update the foundry MCP server to the latest version. Nothing else — no config
changes, no reinstalls. Run this after a new foundry release.

### `/foundry:help`

Show this help.

## Prerequisites

Run `/foundry:setup` once per machine to install:

- **Foundry MCP server** — phase gates, defect tracking, orchestration state.
  The only hard requirement; without it no foundry command works.
- **Playwright MCP** — browser automation. Needed only for the SIGHT stream.
- **Serena MCP** — LSP symbol resolution for TRACE / FLOW_TRACE. Optional: it
  needs a shared daemon on `localhost:9121`, and those streams fall back to grep
  (and say so in their reports) when it is absent.

Setup registers the MCP server against a git URL that carries no version, so
your `.mcp.json` never goes stale and never needs hand-editing. To upgrade the
server later run `/foundry:update` — uvx caches the build by resolved commit and
will otherwise serve the same one indefinitely.

## Key properties

- **One command, zero approval gates** — fully autonomous from `/foundry:start` to F6 DONE
- **Lead never edits code** — delegates everything to teammates (SIGHT/Playwright is the one exception)
- **Plans are prompts** — decompose authors once at F0.5; teammates are dispatched a pointer and read that frozen prompt verbatim from disk
- **Every non-passing verdict is a defect** — no deferrals, no "close enough"
- **Full re-verify after every fix** — no spot-checking
- **Methodical teammate** — tuned for correctness over wall-clock speed (read floor, approach deliberation, blast radius, competing hypotheses)
- **Stop hook** — while a build is live (F1..F5.5), the Stop hook blocks the lead's turn from ending and sends it back to `Foundry-Next`; the turn ends only at a sanctioned stop — every remaining item parked and put to you, a `HALTED` seal, or F6 DONE. The Stop hook is what forces re-engagement; a stall warning only makes a quiet run visible
- **MCP-guided** — `Foundry-Next` returns a literal "YOUR NEXT CALL" imperative every step
- **Full audit trail** — every casting prompt, every acceptance, every handoff written to `foundry-archive/{run}/`

## Complete workflow

```
1. Forge plans:    /forge:plan "my feature"
2. Foundry builds: /foundry:start "my feature" --spec docs/specs/my-feature.md
```

Forge plans. Foundry builds. You ship.
