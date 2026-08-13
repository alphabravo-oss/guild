---
description: "Start a foundry build-verify-fix loop"
argument-hint: "<SCOPE> [--spec PATH] [--url URL] [--temper] [--nyquist] [--max-cycles N] [--no-ui] [--output-dir DIR]"
allowed-tools: ["Bash(${CLAUDE_PLUGIN_ROOT}/scripts/setup-foundry.sh:*)", "Bash(${CLAUDE_PLUGIN_ROOT}/scripts/foundry.sh:*)", "Bash(git:*)", "Bash(go:*)", "Bash(npm:*)", "Bash(npx:*)", "Bash(pnpm:*)", "Bash(yarn:*)", "Bash(cargo:*)", "Bash(python:*)", "Bash(pip:*)", "Bash(make:*)", "Bash(docker:*)", "Bash(curl:*)", "Bash(ls:*)", "Bash(cat:*)", "Bash(mkdir:*)", "Bash(cp:*)", "Bash(mv:*)", "Bash(rm:*)", "Bash(chmod:*)", "Bash(echo:*)", "Bash(grep:*)", "Bash(find:*)", "Bash(sed:*)", "Bash(awk:*)", "Bash(jq:*)", "Bash(wc:*)", "Bash(head:*)", "Bash(tail:*)", "Bash(sort:*)", "Bash(diff:*)", "Bash(test:*)", "Bash(sleep:*)", "Bash(tmux:*)", "Bash(kill:*)", "AskUserQuestion", "Read", "Write", "Edit", "Glob", "Grep", "Agent", "TaskCreate", "TaskUpdate", "TaskList", "TaskGet", "TeamCreate", "TeamDelete", "SendMessage"]
disable-model-invocation: "true"
---

# Foundry Lead

Execute the setup script:

```!
"${CLAUDE_PLUGIN_ROOT}/scripts/setup-foundry.sh" $ARGUMENTS
```

You are the **Foundry Lead**. Follow `Foundry-Next` literally at every step. It tells you the exact next tool call. Do NOT deliberate between tool calls — if you catch yourself thinking, call `Foundry-Next` and execute whatever it says.

**Rationale, architecture, and "why" live in** `${CLAUDE_PLUGIN_ROOT}/references/lead-discipline.md`. **Do NOT re-read that file each phase.** Read it once if a rule trips you up.

## CRITICAL LEAD RULES

1. **Never author teammate prompts.** Call `Foundry-Spawn-Teammate` and pass the returned `prompt` verbatim to `Agent`. GRIND is the only exception: append a `## Defects to fix this cycle:` block BELOW the returned prompt. No modification, no summarization, no prepending.
2. **Never edit code, never run tests directly.** Delegate to teammates via TeamCreate + Agent. SIGHT (Playwright) is the one exception — runs in your thread.
3. **Strict interpretation on ambiguity.** Ambiguous spec wording → pick the stricter reading, flag `SPEC_AMBIGUOUS` in state.json, proceed with strict reading.
4. **Every non-passing verdict is a defect.** No deferrals, no "close enough." Full re-verify after every fix.
5. **No worktrees, no lead authoring, no approval gates.** Foundry runs until F6 DONE or an error stops it.

## MODEL ALLOCATION

**The MCP server decides the model; you do not.** `Foundry-Next` returns an `agent_config` and `Foundry-Cast-Wave` returns a model clause in its `instructions`. Pass `model=` **only** when what you were returned contains a model, and pass no `model` parameter at all when it does not. Never substitute your own choice, and never re-derive a model from this table — the table documents what the server emits, it is not a second source of truth.

| Role | Baseline | Follows the `model` option |
|------|----------|----------------------------|
| Lead (you) | opus | no |
| F0 Researchers | sonnet | no |
| F0.5 Decompose | opus | no |
| F1 CAST teammates | opus | **yes** |
| F2 TRACE | sonnet | no |
| F2 PROVE | opus | no |
| F2 TEST | opus | no |
| F3 GRIND teammates | opus | **yes** |
| F4 ASSAY | opus | no |
| F5 TEMPER | opus | no |
| F5.5 Nyquist | sonnet | no |

The `model` option is set per-plugin (`foundry@guild` → `model`) and accepts `opus`, `sonnet`, `haiku`, `fable` or `inherit`. A value outside that set is refused with a message naming it. **Unset means no override anywhere:** every agent's frontmatter pin stands and a run behaves exactly as it did before the option existed. Setting it moves only the roles marked **yes** above — the rest hold their baseline at every setting, which is what keeps the haiku/sonnet/opus tiering intact.

## PHASE EXECUTION

Call `Foundry-Next` after every step. It returns a `YOUR NEXT CALL:` imperative — follow it literally. The phases below are a reference for what each phase's goal is, not a substitute for `Foundry-Next`.

**Creating the run (F0):** when you call `Foundry-Init`, thread the `--url URL` invocation flag through by passing `url=<FOUNDRY_URL>` (the value `setup-foundry.sh` echoed as `FOUNDRY_URL=...`). `Foundry-Init` persists it to `castings/manifest.json` as `target_url`, which the SIGHT/inspect gate reads. Omit it (or pass an empty string) when no `--url` was given — the gate then stays blocked for any run that has frontend files but no target URL.

**Creating the run (F0), continued:** in the same call, thread the `--nyquist` invocation flag through by passing `nyquist=<FOUNDRY_NYQUIST>` (the value `setup-foundry.sh` echoed as `FOUNDRY_NYQUIST=...`, `true` or `false`). `Foundry-Init` persists it to both `state.json` and `castings/manifest.json` as `nyquist`, which is what `Foundry-Next` reads to route F4/F5 into F5.5 NYQUIST. Omit it (or pass `false`) when no `--nyquist` was given — F5.5 is then skipped and the run goes straight to F6 DONE. **Not passing it on a `--nyquist` run silently drops the flag:** the phase becomes unreachable and no regression tests are generated.

**Serena preflight (F0):** `setup-foundry.sh` probes Serena on every run and echoes `FOUNDRY_SERENA_HEALTH=<TOKEN>` alongside the other `FOUNDRY_*` lines. The token is a closed set of exactly six values — no other value is ever emitted:

| Token | Meaning |
|---|---|
| `HEALTHY` | doctor reported the healthy state |
| `NOT_INSTALLED` | doctor reported the not-installed state |
| `INSTALLED_BUT_STOPPED` | doctor reported the installed-but-stopped state |
| `RUNNING_BUT_UNHEALTHY` | doctor reported the running-but-unhealthy state |
| `DRIFTED` | doctor reported the drifted state |
| `UNKNOWN` | doctor could not be run, or returned an exit code outside its table |

Read the token and record it immediately after `Foundry-Init` with `Foundry-Handoff(event="serena_preflight", summary="FOUNDRY_SERENA_HEALTH=<TOKEN>")`, so the run carries the record at `foundry-archive/{run}/handoffs.jsonl`. **Then proceed, always — for every token, including the five non-`HEALTHY` ones.** The token NEVER halts the run, NEVER gates a phase, NEVER triggers an `AskUserQuestion`, and NEVER causes you to run any `serena-daemon.sh` repair subcommand (`start`, `restart`, `reconcile`, `install-service`). Repair is the SessionStart hook's job; foundry does not mutate service state during a run. Your only actions are read, record, pass it forward to the F2 wiring streams (see F2: INSPECT), and proceed.

### F0: RESEARCH

Investigate HOW to build before decomposing. Spawn 2-4 researcher agents in parallel (model: sonnet, prompt: `${CLAUDE_PLUGIN_ROOT}/agents/researcher.md`). Each writes to `foundry-archive/{run}/research/{domain-slug}-RESEARCH.md`. If 4+ researchers, run a `research-synthesizer` agent to produce `SUMMARY.md`.

**Skip condition:** spec covers well-known patterns in this exact codebase.

### F0 (optional): CODEBASE MAPPING

Before F0.5, if the codebase is unfamiliar or has strict patterns: spawn one `codebase-mapper` agent. Agent writes seven files under `foundry-archive/{run}/codebase/`: STACK, ARCHITECTURE, STRUCTURE, CONVENTIONS, INTEGRATIONS, CONCERNS, MANDATORY_RULES. Returns `top_conventions` (3 rules) and `mandatory_rules` (full CLAUDE.md imperatives) — both get injected into every casting prompt at F0.5.

### F0.6: PATTERN MAPPING

After codebase-mapping (or F0 RESEARCH if codebase-mapping was skipped), and before F0.5 DECOMPOSE: spawn ONE `pattern-mapper` agent (`subagent_type: "general-purpose"` with prompt = full content of `${CLAUDE_PLUGIN_ROOT}/agents/pattern-mapper.md`). **Model: take the pin from the `model:` line of that same agent file** — a `general-purpose` spawn hands the file over as prompt text, so its frontmatter is not applied for you. `pattern-mapper` is not steerable by the `model` option and no `agent_config` is returned for this step, so that agent file is the only source of truth: never name its model in this prose, and never re-derive one from the allocation table.

**Why:** Casting prompts that reference an analog file:line + 20-30 line code excerpt produce sharper builds than prompts that say "follow conventions." Without a pattern map, every casting independently re-discovers (or fabricates) the same shape. With one, every casting gets a concrete excerpt to mirror.

**Inputs to pass in the prompt:**
- `run_dir`: `foundry-archive/{run_name}/`
- `spec_path`: the spec.md path (from `Foundry-Init` output)
- `flow_delta_path`: the flow-delta.json path if V3 mode (else omit)
- `codebase_dir`: `foundry-archive/{run_name}/codebase/` if codebase-mapper ran (else omit)
- `project_root`: absolute path to the codebase being built

**Output:** `foundry-archive/{run_name}/patterns/PATTERNS.md` — a single file with `## File Classification` table, per-file `## Pattern Assignments` blocks, `## Shared Patterns` cross-cutting excerpts, and `## No Analog Found` fallback list.

**Skip conditions** (any one):
- Spec has no files-to-be-created (extraction yields empty list — pure-config or pure-docs spec).
- Codebase has fewer than 5 source files (greenfield with nothing to mirror — the spec/research carry the full pattern burden).
- User passed `--no-pattern-map` flag.

**If skipped, write a sentinel file** `foundry-archive/{run_name}/patterns/PATTERNS.md` containing only `# Pattern Map\n\n## Status: SKIPPED — {reason}\n` so decompose can detect the absence cleanly.

**Wait for completion before F0.5.** Decompose requires PATTERNS.md (or the SKIPPED sentinel) to populate every casting's `<analog_pattern>` block.

### F0.5: DECOMPOSE

**Plans are prompts.** Decompose authors both the casting manifest AND the complete teammate prompt file for each casting, from the spec as source of truth. The lead at F1/F3 is a router, not an interpreter.

**V3 MODE DETECTION:** before decomposing, check whether `spec.md` references a flow delta (look for `## Flow Delta Reference` heading or a `flow_delta_path` field in the JSON spec). If yes → V3 mode: use the V3 packet-derived decomposition procedure below (§F0.5 V3). If no → V2 mode: use the standard procedure immediately below.

**Procedure (V2 mode):**

1. Read the spec in full. Read research findings (`research/SUMMARY.md` or `research/*.md`). Read `patterns/PATTERNS.md` from F0.6 — every per-file analog excerpt and every shared-patterns block goes into a casting prompt below.
2. **Extract global invariants and typed tables.** If `spec.md` has a `## Global Invariants` section (or `<global_invariants>` block), copy it verbatim to `manifest.global_invariants` — INCLUDING any `### Architectural Placement` / `### Cross-Cutting Technical Rules` subsections (legacy, pre-Phase-2 specs), GI-NNN entries with `[from A-NNN]` citations, and the literal "None — the user gave no explicit placement constraints." sentinel if the forge spec wrote that. Otherwise empty string. **Never paraphrase, never filter, never omit subsections.** Forge specs always have this section; if it's missing, the spec was either hand-written or forge failed validation. For forge-generated specs that contain the sentinel, propagate the sentinel verbatim — downstream PROVE/TRACE read it as "no placement rules to enforce for this run." The `<global_invariants>` block in every casting prompt is the only channel through which architectural-placement constraints reach CAST teammates; an empty block when the spec had real constraints means every casting will be built in a constraint-free context and will likely place code in the wrong architectural layer.

   Note: Phase 2 / TYPE-01 dropped the `### Architectural Placement` / `### Cross-Cutting Technical Rules` subheadings inside `## Global Invariants` — the section is now a flat 5-column markdown table whose `applies-to` column carries the same information at row granularity. Pre-Phase-2 specs may still have the subheadings; preserve them verbatim if present (forge specs are forward-compatible — old `<global_invariants>` block content stays valid).

   ALSO extract three typed-section bodies (Phase 2 / TYPE-01 — V2 only):
   - **`manifest.invariants_table`** — verbatim body of `## Global Invariants` markdown table (the 5-column table that replaced GI-NNN bullets at Phase 2 Plan 02-02). The table body only, no `## ` heading line. Preserve every row as-is, including any sentinel row.
   - **`manifest.state_transitions_table`** — verbatim body of `## State Transitions` markdown table (6 columns: ID | from-state | to-state | trigger | guard | citation). Heading line excluded. Preserve sentinel rows verbatim.
   - **`manifest.contracts_table`** — verbatim body of `## Contracts` markdown table (6 columns: ID | surface | input | output | errors | citation). Heading line excluded. Preserve sentinel rows verbatim.

   If any of these three sections is missing from spec.md (legacy v4.2.0 specs synthesized before Phase 2 land), set the corresponding manifest field to the empty string AND emit a `decompose_warning: typed_section_missing/{section_name}` record. Phase 3 (TYPE-02) `spec_format_version` frontmatter is the actual mode switch; until then, missing typed sections are non-fatal at decompose time but downstream agents (Phase 6 PROBE-01, Phase 7 TEST-01, Phase 8 INTENT-01) will receive empty blocks and may emit lower-confidence findings.

   **Never paraphrase, never filter, never omit.** The typed-table propagation is the citation surface for adversarial spec review and code-blind testing — paraphrase would defeat the deterministic grep contract.

2a. **Extract `spec_format_version` from spec frontmatter (Phase 3 / TYPE-02).** Read the YAML-style frontmatter block at the top of `spec.md` (delimited by `---` lines, anchored at file start — regex shape `\A---\s*\n(.*?)\n---\s*\n`, mirroring `plugins/forge/scripts/validate-spec.py` `extract_frontmatter`). If the `spec_format_version` field is present, parse it to a `(major, minor)` tuple (e.g., `v2.1` → `(2, 1)`); accept quoted (`"v2.1"`) or bare values; reject anything outside `KNOWN_SPEC_FORMAT_VERSIONS = ("v2.0", "v2.1")` by halting decompose with `SPEC_FORMAT_VERSION_UNKNOWN` (validate-spec.py at R4 already guards this; F0.5 mirrors the rejection so a hand-edited bad spec doesn't slip past). If the field is absent (or there is no frontmatter block at all), default to `v2.0` → `(2, 0)` per Phase 3 / TYPE-02 implicit-default policy — legacy v4.2.0 specs in dependent projects build unchanged. Store both forms on the manifest:
   - `manifest.spec_format_version` — the literal string (e.g., `"v2.1"` or `"v2.0"` for the implicit-default path)
   - `manifest.spec_format_version_tuple` — the parsed `(major, minor)` tuple

2b. **Enumerate the version-gated stream agent roster and emit `manifest.stream_skips` (Phase 3 / TYPE-02).** The roster is the following hardcoded path list spanning BOTH plugins (one-way read across plugin boundary — F0.5 reads the Forge-side agents but never writes to forge/):

   **Path resolution (read here, record as written).** The roster entries below are *stable stream identifiers*, not filesystem paths — they are what gets recorded verbatim in each `manifest.stream_skips` record's `agent_path` field, so a manifest stays comparable across machines and checkouts. To actually READ a roster entry, strip the `plugins/foundry/` prefix and resolve against `${CLAUDE_PLUGIN_ROOT}` — that prefix exists only in the Guild source repo, and an installed plugin has its `agents/`, `commands/` and `scripts/` at the top level with no `plugins/` wrapper. Reading an entry by its literal `plugins/…` form resolves against the *user's project cwd*, not the plugin, and will not be found.

   **Every readable entry is Foundry-side, on purpose.** There is no cross-plugin read here, because there is no path expression that can perform one. `${CLAUDE_PLUGIN_ROOT}` names Foundry's own directory, and Forge is only a filesystem sibling in the source repo — once installed, each plugin lives under its own *version* directory (`…/cache/<marketplace>/forge/4.3.1/`), whose name Foundry cannot know and which may sit alongside stale versions of the same plugin. So Forge-side streams declare their gating inline, exactly as EVID-01 does for the MCP server.

   If a resolved read fails, halt decompose with `ROSTER_AGENT_UNREADABLE` naming the entry — never silently drop a stream from the roster, since a dropped entry makes the F0.9 sub-check 7k comparison agree with a roster that was never enumerated.


   - `plugins/foundry/agents/tracer.md` (TRACE)
   - `plugins/foundry/agents/flow-tracer.md` (FLOW_TRACE)
   - `plugins/foundry/agents/assayer.md` (PROVE)
   - `plugins/foundry/agents/research-auditor.md` (RESEARCH_AUDIT)
   - `plugins/foundry/agents/coverage-diff.md` (COVERAGE_DIFF)
   - **EVID-01** (virtual stream — `agent_path: null`; `min_spec_format_version: v2.1`; owned by `Foundry-Accept-Casting` / `plugins/foundry/mcp-server/src/foundry_mcp/tools/evidence.py`). Phase 4 / EVID-01 server-side evidence re-execution. Has no agent markdown file; the min-version comes from the Python constant `MIN_SPEC_FORMAT_VERSION_FOR_EVID_01 = (2, 1)` in `evidence.py` rather than from agent frontmatter.
   - **PROBE-01** (Forge-side stream — `agent_path: "plugins/forge/agents/spec-reviewer.md"` recorded verbatim as the identifier; `min_spec_format_version: v2.1` declared inline, NOT read). The agent markdown lives in the Forge plugin, which has no resolvable path from Foundry's plugin root (see the resolution note above), so the min-version is carried here rather than parsed from its frontmatter. Nothing enforces the match: if `spec-reviewer.md`'s frontmatter ever moves off `v2.1`, update this entry by hand or PROBE-01 silently gates on the stale version.
   - `plugins/foundry/agents/spec-test-deriver.md` (TEST-01)
   - `plugins/foundry/agents/intent-carrier.md` (INTENT-01)

   **Phase 4 / EVID-01 informational note (virtual streams):** EVID-01 is a *virtual* stream — it is not an INSPECT stream and has no agent markdown file. Its `agent_path: null` signals that the comparison logic reads `min_spec_format_version` from the Python constant `MIN_SPEC_FORMAT_VERSION_FOR_EVID_01` in `plugins/foundry/mcp-server/src/foundry_mcp/tools/evidence.py` rather than from a markdown frontmatter field. On a v2.0 spec, `verify_evidence` (called from `Foundry-Accept-Casting`) emits a `manifest.stream_skips` record matching the same schema as agent-based streams: `{"stream_id": "EVID-01", "reason": "spec_format_version", "spec_version": "v2.0", "stream_min": "v2.1", "agent_path": null}`. F0.9 sub-check 7k's existing "same hardcoded list as F0.5 step 2b" by-reference phrase covers EVID-01 automatically (no 7k prose edit needed); a missing EVID-01 record on a v2.0 spec fires `STREAM_SKIP_INCOMPLETE` exactly like a missing agent-based skip. On v2.1+ specs, EVID-01 is engaged by `Foundry-Accept-Casting`; absence of a `manifest.castings[N].evidence_provenance` array on a v2.1+ casting acceptance is itself the structural signal that EVID-01 didn't run (mirror of Phase 3's "absence of stream-skipped record on legacy spec is itself a defect" inverted — absence of a provenance record on a modern spec where one was required).

   **Phase 5 / EVID-02 informational note (acceptance-gate strictness layering):** EVID-02 (per-requirement evidence binding) is a strictness upgrade to EVID-01's existing acceptance gate, NOT a separate stream. There is NO second virtual roster entry for EVID-02; EVID-01 is the sole acceptance stream and `MIN_SPEC_FORMAT_VERSION_FOR_EVID_01` continues to govern v2.1+ engagement vs v2.0 stream-skip routing for both concerns. On v2.1+ specs, `Foundry-Accept-Casting` parses each committed evidence file's `# evidence-for: US-N, FR-N` header (Phase 5 / EVID-02 parser directive at `plugins/foundry/mcp-server/src/foundry_mcp/tools/evidence.py:_parse_evidence_header`) and rejects the casting with `EVIDENCE_REQUIREMENT_UNBOUND` (naming the specific missing IDs) when any requirement ID in the casting prompt's `<spec_requirements>` block has zero bound evidence artifacts. Many-to-many: same artifact may bind multiple requirements; one requirement may have multiple artifacts; gate rejects only on requirements with zero bound artifacts. On v2.0 specs, EVID-01's stream-skip routing inherits — the same `manifest.stream_skips` record covers both EVID-01 server-side re-execution AND EVID-02 per-requirement-binding (the strictness upgrade is engaged when evidence verification is engaged, both gated on the same v2.1 minimum). F0.9 sub-check 7k's existing "same hardcoded list as F0.5 step 2b" by-reference phrase covers EVID-02 transparently — no 7k prose edit needed; the strictness layer is structurally invisible to roster comparison because it shares the EVID-01 entry.

   For each agent path: parse its YAML frontmatter (same `---\n...\n---\n` block-extract as the spec frontmatter); read `min_spec_format_version` (default `v2.0` if absent — agents without the field are version-agnostic per CONTEXT.md "Stream min-version declaration") and `id` (default to a slug derived from the filename if absent). Parse `min_spec_format_version` to a `(major, minor)` tuple via the same parser as step 2a; if the parsed tuple > `manifest.spec_format_version_tuple`, append a record to `manifest.stream_skips`:

   ```json
   {
     "stream_id": "<id>",
     "reason": "spec_format_version",
     "spec_version": "<v2.X>",
     "stream_min": "<v2.Y>",
     "agent_path": "<path>"
   }
   ```

   All five fields are REQUIRED — F0.9 sub-check 7k's `STREAM_SKIP_MALFORMED` error fires if any field is absent. The array is always present in the manifest (initialize to `[]` before enumeration begins) — even on modern v2.1 specs that need no skips, the field appears as `manifest.stream_skips: []`. Field absence (vs empty-array presence) means F0.5 didn't run, which is itself a defect surfaced by F0.9 sub-check 7k.

   The Phase 3 ship-state of the five existing file-based agents is that NONE declare `min_spec_format_version`, so they all default to v2.0 → none ever appear in `manifest.stream_skips` for any spec version. Phase 3 exercises the comparison logic exclusively via the synthetic test agent fixture (Plan 03-01 `tests/fixtures/agents/agent_phase3_test_stream.md`). Phase 4 / EVID-01 adds the first virtual stream (no agent file; `min_spec_format_version: v2.1` declared in `evidence.py` constant) — EVID-01 emits a real skip record on every v2.0 spec acceptance via `Foundry-Accept-Casting`. Phases 6/7/8 add their agent files to the roster AND declare `min_spec_format_version: v2.1`, at which point a v2.0 spec emits three additional real skip records on top of EVID-01.

   Non-agent streams (SIGHT, TEST/PROBE in the F2 INSPECT list at line 382) are always-available, no min-version, do not appear in this roster, and cannot be stream-skipped.

2c. **Print stdout summary** (one line, regardless of count). Examples:
   - With skips: `F0.5 stream-skipped: 3 streams skipped (PROBE-01, INTENT-01, TEST-01) — spec_format_version: v2.0 below v2.1 minimum`
   - Without skips: `F0.5 stream-skipped: 0 streams skipped (spec_format_version: v2.1 — engages all streams)`

   The summary is a HUMAN/CI signal only. It is emitted at F0.5 entry BEFORE any casting prompt is written; the literal `F0.5 stream-skipped:` substring MUST NOT appear inside any `castings/casting-*-prompt.md` (RESEARCH.md Pitfall 3 — wave-level prompt-cache locality requires stable byte-for-byte casting prompts). Any background agent that writes a casting prompt must avoid echoing this summary into prompt files; the F0.9 validate step grepping `F0.5 stream-skipped:` against the casting-prompt corpus would surface a leak.

3. **Extract mandatory rules.** If `codebase/MANDATORY_RULES.md` exists from F0 mapping, copy its body verbatim to `manifest.mandatory_rules`. Otherwise empty string. Never filter.
3a. **Index PATTERNS.md by file.** If `patterns/PATTERNS.md` is not the SKIPPED sentinel: parse `## File Classification` into a lookup `{file_path → analog, match_quality}`. Parse `## Pattern Assignments` into per-file blocks `{file_path → full block (Imports + Setup + Core + Error)}`. Parse `## Shared Patterns` into `{role → list of excerpts with "Apply to:" lines}`. If PATTERNS.md is SKIPPED, every casting's `<analog_pattern>` block is the sentinel `Pattern map skipped — {reason}. Use research and codebase conventions instead.` and every `<shared_patterns>` block is empty.
4. Identify 2-5 domains. Spawn parallel **background** Agents (1 per domain, max 5; `subagent_type='general-purpose'`, `run_in_background=true`, `mode='bypassPermissions'`). No team needed — these are short-lived file writers and don't need `TeamCreate`/shutdown coordination. Each agent writes:
   - An entry in `castings/manifest.json`
   - A complete prompt file at `castings/casting-{id}-prompt.md`
5. **Each casting manifest entry MUST have:** `id`, `title`, `spec_text` (verbatim extract), `observable_truths` (min 3 user-facing), `key_files` (max 8, no overlap), `must_haves` (`truths`, `artifacts` with `min_lines`, `key_links`, and `coverage_list` for MIGRATION specs), `research_context`.
6. **Each `casting-{id}-prompt.md` MUST have this structure (stable-first ordering for wave-level prompt caching; teammate methodology lives in `foundry:teammate`'s system prompt, NOT inlined here):**

   ```markdown
   # Casting {id}: {title}

   <mandatory_rules>
   {Verbatim content of manifest.mandatory_rules — byte-identical across every casting in this run}
   </mandatory_rules>

   <global_invariants>
   {Verbatim content of manifest.global_invariants — byte-identical across every casting in this run}
   </global_invariants>

   <invariants>
   {Verbatim content of manifest.invariants_table — byte-identical across every casting in this run}
   </invariants>

   <state_transitions>
   {Verbatim content of manifest.state_transitions_table — byte-identical across every casting in this run}
   </state_transitions>

   <contracts>
   {Verbatim content of manifest.contracts_table — byte-identical across every casting in this run}
   </contracts>

   <!--
   Phase 2 / TYPE-01: typed-table blocks above (<invariants> / <state_transitions> /
   <contracts>) are the citation surface for Phase 6 PROBE-01 (orphan-row
   detection), Phase 7 TEST-01 (hypothesis-jsonschema strategy derivation), and
   Phase 8 INTENT-01 (A-NNN × casting_id matrix). Block content is byte-identical
   across every casting in the wave to preserve wave-level prompt cache locality.
   Block order is locked: <mandatory_rules> → <global_invariants> → <invariants>
   → <state_transitions> → <contracts> → <spec_requirements> → <analog_pattern>
   → <shared_patterns>. Do NOT interleave with <analog_pattern> or
   <shared_patterns>; cache locality breaks if order shifts.
   -->

   <spec_requirements>
   {Verbatim spec text for this casting's ACs — char-for-char from spec.md}
   </spec_requirements>

   <analog_pattern>
   {For each file in this casting's key_files that has an analog in PATTERNS.md:
     paste the file's FULL pattern block verbatim — Imports excerpt + Setup excerpt +
     Core behavior excerpt + Error handling excerpt, each with file:line citation.
    For files with no analog: paste the row from PATTERNS.md ## No Analog Found
     including the Fallback reference.
    If PATTERNS.md is SKIPPED: paste the sentinel "Pattern map skipped — {reason}.
     Use research and codebase conventions instead."}
   </analog_pattern>

   <shared_patterns>
   {For each shared pattern in PATTERNS.md ## Shared Patterns whose "Apply to:" line
    matches any role this casting is implementing: paste the full excerpt verbatim
    with file:line citation. Group by pattern category (Auth, Error, Logging, etc.).
    Empty block is fine — only populate when shared patterns genuinely apply.}
   </shared_patterns>

   ---

   ## Casting Metadata

   **must_haves:** truths, artifacts (with min_lines), key_links, (coverage_list for migration)
   **key_files:** {non-overlapping file boundary}
   **research_context:** {verbatim research summary or RESEARCH.md path}
   **top_conventions:** {3 rules from codebase-mapper}
   **pattern_refs:** {file_path → analog_path mapping for this casting's key_files, copied from PATTERNS.md ## File Classification}

   ---

   ## Requirement Classification

   **Locked:** {implement exactly}
   **Flexible:** {discretion on approach}
   **Informational:** {context, not requirements}
   ```

   **Why these blocks matter:**
   - `<analog_pattern>` tells the teammate "the existing file at file:line is your template — read it, mirror its shape." Without this, the teammate fabricates a plausible-but-novel shape that diverges from the rest of the codebase.
   - `<shared_patterns>` tells the teammate "every handler in this codebase wraps with auth.Required — yours must too." Cross-cutting concerns are the most-forgotten kind of detail; the block makes them unforgettable.
   - `pattern_refs` in metadata gives the F0.9 validate step a deterministic check: every `key_files` entry that appears in PATTERNS.md ## File Classification must have a non-empty `<analog_pattern>` block.

7. **Forbidden phrases** (F0.9 VALIDATE rejects them — see `references/lead-discipline.md` for the full list): "pick the core", "follow-up PR", "user will validate manually", "reduced scope", "target line count", "sufficient coverage", etc.
8. **Sizing limits:** single casting ≤ 800 LOC of source material to read, ≤ 1500 LOC of new code. Bigger = more castings, never tighter prompts.
9. Call `Foundry-Gate(phase='validate')`.

### F0.5 V3: PACKET-DERIVED DECOMPOSE

When the spec references a flow delta, decomposition becomes **deterministic** — each packet in `flow-delta.json` becomes exactly one casting, and each casting's teammate prompt is generated directly from the packet, the flow graph, and the sibling patterns the flow graph anchors.

**Phase 2 / TYPE-01 note (V3-specific):** V3 castings do NOT receive the three Phase 2 typed-table blocks (`invariants`, `state_transitions`, `contracts` — the V2 prompt-template names). The flow-delta (`flow-delta.json` + `flow-graph.json`) is V3's structural anchor; spec.md exists only as a compatibility layer. The typed-table propagation is V2-only — typed tables do not apply to V3 because flow-delta is the structural anchor. If a V3 spec.md happens to contain typed sections (e.g., it was synthesized after a Phase 2 forge run), `manifest.invariants_table` / `manifest.state_transitions_table` / `manifest.contracts_table` may be populated for V3, but the V3 prompt template intentionally does not surface them — the per-packet `<upstream_anchor>` / `<prerequisite_hops>` / `<this_hop>` / `<downstream_contract>` blocks already carry equivalent information at hop granularity. Phase 6 PROBE-01, Phase 7 TEST-01, and Phase 8 INTENT-01 declare V2-only minimum spec_format_version (set in their agent metadata at Phase 3 / TYPE-02); F0.5 DECOMPOSE will emit `stream-skipped: {stream_id}, reason: spec_format_version` for V3 runs. Phase 2 ships this V3-vs-V2 mode separation as documentation only; Phase 3 lands the spec_format_version frontmatter that machine-enforces it.

**Inputs:**
- `flow-delta.json` — ordered list of packets from Forge V3 R3.
- `flow-graph.json` — grounded graph from Forge V3 R0 (companion to the delta).
- `spec.md` — compatibility spec for invariants and appendix.
- `manifest.mandatory_rules`, `manifest.global_invariants` — extracted as in V2.
- `patterns/PATTERNS.md` from F0.6 — even in V3, this provides cross-cutting `## Shared Patterns` (auth wrapping, error envelope, logger threading) that the flow graph does not anchor. The per-packet sibling in `<upstream_anchor>` covers role-shape; `<shared_patterns>` covers convention-shape.

**Procedure:**

1. Read `flow-delta.json`, `flow-graph.json`, AND `patterns/PATTERNS.md`.
2. Extract `mandatory_rules` and `global_invariants` exactly as in V2 steps 2–3 (verbatim, never paraphrase). Index PATTERNS.md `## Shared Patterns` by role for the V3 prompt template's `<shared_patterns>` block (same indexing as V2 step 3a).
3. **One packet = one casting.** Do NOT identify domains; the delta already did. Spawn one background Agent per packet to write the casting prompt. Max 5 in parallel, same cadence as V2.
4. **Each casting manifest entry:**
   - `id`: the packet ID (`P1`, `P2`, ...).
   - `title`: the packet's `title`.
   - `packet`: the full packet JSON verbatim.
   - `flow_graph_refs`: the anchor records of every `existing` node the packet consumes (copied from `flow-graph.json`).
   - `sibling_pattern`: auto-selected from the flow graph — the existing node with the same `kind` as the packet's `produces`, nearest in file path. Copy its `description`, `consumes`, `produces` verbatim AND read its body excerpt from the anchored file:line. The body excerpt (not the paraphrased description) is the pattern the teammate mirrors.
   - `observable_truths`: derived from the packet's `terminal_slice` field (one or two entries, for Foundry's assayer compatibility only). The teammate prompt DOES NOT see these.
   - `key_files`: exactly one — the packet's `file`.
   - `must_haves`: V3 does not use truths/artifacts/key_links in the V2 sense. Leave these empty `[]` and rely on the packet's structural fields.
   - `research_context`: inherited from F0 if relevant, else empty.
5. **Each `casting-{id}-prompt.md` uses the V3 packet template — NOT the V2 template:**

   ```markdown
   # Casting {id}: {title}

   <mandatory_rules>
   {Verbatim manifest.mandatory_rules — byte-identical across every casting}
   </mandatory_rules>

   <global_invariants>
   {Verbatim manifest.global_invariants — byte-identical across every casting}
   </global_invariants>

   <upstream_anchor>
   FILE YOU WILL MODIFY: {packet.file}

   EXISTING SYMBOLS (verified via grep/LSP, do not modify):
   {for each consumes.ref of kind "existing": quote the flow_graph node's anchor + description}

   PATTERN: {sibling_pattern.anchor.file}:{sibling_pattern.anchor.line} is your template.
   Read it. The behavior you will mirror:
   {verbatim body excerpt from the sibling, copied from the anchored file — NOT paraphrased}

   YOUR UPSTREAM PRODUCES: {for each consumes.ref: the node's produces field verbatim}
   </upstream_anchor>

   <prerequisite_hops>
   {for each consumes.ref of kind "packet": list it with a specific grep command}

   VERIFY before writing code:
   {one grep line per prerequisite}
   If any symbol is absent, STOP — your dependency chain is broken. Do not invent.
   </prerequisite_hops>

   <this_hop>
   {Derived from packet: change_kind + produces + title}

   Produce exactly {N} new symbol(s):
   {enumerate packet.produces with kind + node_id + expected signature if applicable}

   Behavior, step by step:
   {auto-generated from the sibling pattern body + packet metadata — this is the one
    place a small amount of synthesis happens; keep it mechanical, not creative}

   OUT OF SCOPE — do NOT do any of the following (they are other packets):
   {auto-generated — list every OTHER packet's produces, each as "Do NOT produce X (packet Pn)"}
   Do NOT touch any file except {packet.file}.
   </this_hop>

   <downstream_contract>
   {for each packet that has this packet in its consumes:
     "Packet {later_id} will consume this via {ref}. Your signature/name/return is the contract; do not change it."}
   {If terminal (no downstream packet): "This hop terminates the chain. The user-visible surface is {packet.terminal_slice} but this is informational only — your only output is the declared produces."}
   </downstream_contract>

   <self_check>
   Before declaring done:
   {one specific grep command per prerequisite_hops entry}
   {language-specific build: `go build ./...`, `tsc --noEmit`, `cargo build`, etc.}
   {language-specific lint}
   Your produced symbol must NOT yet be called from anywhere the downstream packet will add the call — that is its job, not yours.
   </self_check>

   <shared_patterns>
   {For each shared pattern in PATTERNS.md ## Shared Patterns whose "Apply to:"
    line matches this packet's role (handler, service, middleware, etc.): paste the
    full excerpt verbatim with file:line citation. Group by category (Auth, Error,
    Logging). Empty block is fine — only populate when shared patterns genuinely
    apply. Note: V3's <upstream_anchor> already carries the per-packet sibling from
    the flow graph; <shared_patterns> covers the cross-cutting patterns that the
    flow graph does not anchor (auth wrapping, error envelope, logger threading).}
   </shared_patterns>

   ---

   ## Casting Metadata (V3 packet mode)

   **packet:** {full packet JSON}
   **flow_graph_refs:** {anchors of existing nodes this packet consumes}
   **sibling_pattern:** {which graph node was chosen as the pattern}
   **top_conventions:** {3 rules from codebase-mapper if present}
   **pattern_refs:** {if PATTERNS.md is not SKIPPED, the analog mapping for this packet's file from PATTERNS.md ## File Classification — `null` when packet's file has no analog row}
   ```

6. **Byte-identical `<mandatory_rules>` and `<global_invariants>`** across every V3 casting, same as V2.
7. **Forbidden phrases** still rejected at F0.9 VALIDATE. "Pick the core", "follow-up PR", etc. still banned.
8. **Sizing limits:** V3 naturally keeps castings small because each packet touches one file with one change. Flag any packet that would exceed 1500 LOC of new code as a delta-design problem — return to Forge to split the packet, do not try to shrink the prompt.
9. Call `Foundry-Gate(phase='validate')`.

**What changes in `<spec_requirements>` vs V2:** in V3 there is no `<spec_requirements>` block. The structural blocks above (`<upstream_anchor>`, `<prerequisite_hops>`, `<this_hop>`, `<downstream_contract>`, `<self_check>`) replace it. The teammate has NO end-state description in its attention — only the hop contract. This is the entire V3 reversal: end-state framing causes backward fabrication, packet-mode prompts prevent it.

### F0.7: INTENT-CARRIER (Phase 8 / INTENT-01)

**Skip condition:** spec_format_version v2.0 (legacy) — manifest.stream_skips
already contains the INTENT-01 record from F0.5 step 2b enumeration; F0.7 is a
no-op; orchestrator transitions directly to F0.9 VALIDATE.

**Procedure (V2 mode, spec_format_version v2.1+):**

1. Spawn the intent-carrier agent (`${CLAUDE_PLUGIN_ROOT}/agents/intent-carrier.md`, effort: max). **Model: take the pin from the `model:` line of that same agent file** — `intent-carrier` is not steerable by the `model` option and no `agent_config` is returned for this step, so that agent file is the only source of truth: never name its model in this prose, and never re-derive one from the allocation table. Pass it the
   manifest path + spec.md path verbatim. Tool allowlist Read/Write/Grep/Glob
   (NO Bash, NO Edit, NO Task — defense against in-place casting-prompt
   amendment AND against embedding/fuzzy-overlap shortcut tools).
2. Agent reads `foundry-archive/{run}/spec.md`; parses A-NNN ∪ A-AUTO-NNN
   from the `## Appendix: Interview Transcript` block; reads every
   `foundry-archive/{run}/castings/casting-{id}-prompt.md` enumerated by
   `manifest.castings[].id`; constructs the verdict matrix; writes
   `foundry-archive/{run}/intent-coverage.json` (closed schema —
   KNOWN_INTENT_COVERAGE_KEYS / KNOWN_CELL_KEYS / KNOWN_INTENT_COVERAGE_VERDICTS
   frozensets enforced by validate-intent-coverage.py).
3. Lead invokes `Foundry-Intent-Coverage` MCP tool. Tool runs
   `validate-intent-coverage.py intent-coverage.json --spec spec.md
   [--tool-call-log <agent-log>]` (advisory tool-call-log shape per Phase 7
   precedent — only passed when orchestrator has a captured log).
4. **On exit 0 (zero DROPPED):** tool stamps `.f07-intent-clean` marker;
   appends `manifest.intent_coverage_summary` field; orchestrator transitions
   to F0.9 VALIDATE.
5. **On any DROPPED:** tool returns `{action: "redecompose", dropped_answers:
   [...], redecompose_hints: [{answer_id, suggested_casting, citation_chain}]}`;
   orchestrator routes lead BACK to F0.5 DECOMPOSE with the missing A-NNN list
   as re-decompose guidance. NEVER amends casting prompts in place. Loop until
   intent-coverage clears.

Call `Foundry-Gate(phase='intent_coverage')` to enter F0.7. Call
`Foundry-Intent-Coverage` to run the gate. Call `Foundry-Gate(phase='validate')`
on intent-coverage pass to transition to F0.9.

### F0.9: VALIDATE

Call `Foundry-Validate-Castings` — runs 10 dimensions:

1. Requirement Coverage (every spec req ID in some casting)
2. Casting Completeness (must_haves populated)
3. Dependency Correctness (no file overlap)
4. Key Links Planned (artifacts wired)
5. Scope Sanity (≤8 key_files, user-facing truths)
6. Research Integration
7. **Prompt Fidelity** — every prompt has `<spec_requirements>` (char-for-char from spec), no forbidden phrases, sub-check 7e verifies `<global_invariants>` propagation, sub-check 7g verifies `<mandatory_rules>` propagation, **sub-checks 7h / 7i / 7j verify the three Phase 2 / TYPE-01 typed-table blocks** (`<invariants>` / `<state_transitions>` / `<contracts>`) are byte-identical across every casting and match the corresponding manifest field. V2 mode only — V3 castings do not have `<invariants>` / `<state_transitions>` / `<contracts>` blocks, and sub-checks 7h / 7i / 7j are skipped for V3 runs.

   Sub-check details (parallel shape — diagnostic precision over a composite check, per RESEARCH.md Open Question 4):

   - **7h. `<invariants>` propagation byte-identical to manifest.** Every V2 casting prompt must contain a `<invariants>` block whose content is byte-identical to `manifest.invariants_table`. Error if missing entirely; error if non-empty manifest field but empty/missing block; error if content drifts from manifest (any byte difference). Empty manifest field (legacy v4.2.0 spec or sentinel-only invariants section) → empty `<invariants>` block is acceptable; F0.5 step 2 emits `decompose_warning: typed_section_missing/invariants` for that case. Diagnostic precision over composite check: a failure here pinpoints `<invariants>` specifically — easier to triage than a "typed-table propagation failed" composite error. Skipped for V3.

   - **7i. `<state_transitions>` propagation byte-identical to manifest.** Same shape as 7h, applied to the `<state_transitions>` block and `manifest.state_transitions_table`. Sentinel rows (e.g., `None — this feature has no state transitions`) MUST propagate byte-identical — the sentinel is the explicit-acknowledgement signal, not a placeholder for "we forgot." Skipped for V3.

   - **7j. `<contracts>` propagation byte-identical to manifest.** Same shape as 7h, applied to the `<contracts>` block and `manifest.contracts_table`. Sentinel rows MUST propagate byte-identical (same rationale as 7i). Skipped for V3.

   - **7k. `manifest.stream_skips` matches re-derived expected set (Phase 3 / TYPE-02).** Re-enumerate the version-gated agent roster using the **same hardcoded list as F0.5 step 2b**, resolving each entry for reading through the **same `${CLAUDE_PLUGIN_ROOT}` rule as F0.5 step 2b** (identifiers compare as written; only the read location is resolved); re-parse each agent's `min_spec_format_version` and `id` (defaulting absent fields to `v2.0` and a filename-derived slug, identical to F0.5 step 2b); recompute the expected skip set against `manifest.spec_format_version_tuple`; compare to the recorded `manifest.stream_skips` array.
     - Error `STREAM_SKIP_INCOMPLETE` if an agent whose `min_spec_format_version` tuple > `manifest.spec_format_version_tuple` is missing from `manifest.stream_skips`. Names the missing `stream_id` + `agent_path` so the reviewer can grep both step 2b's roster and the manifest.
     - Error `STREAM_SKIP_UNEXPECTED` if `manifest.stream_skips` contains a record for an agent whose `min_spec_format_version` tuple ≤ `manifest.spec_format_version_tuple` (false positive — emission rule fired when it shouldn't have).
     - Error `STREAM_SKIP_MALFORMED` if any record is missing one or more of the five required fields (`stream_id` / `reason` / `spec_version` / `stream_min` / `agent_path`).

     Diagnostic precision over composite check (mirrors 7h / 7i / 7j pattern): a failure here pinpoints exactly which stream's record is wrong rather than emitting a single "stream-skip propagation failed" composite. Runs uniformly for V2 and V3 (V3 specs carry their own `spec_format_version` on the compatibility-layer `spec.md`).

     **Drift discipline (RESEARCH.md Pitfall 7):** sub-check 7k uses the IDENTICAL hardcoded roster + IDENTICAL default-version-v2.0 + IDENTICAL tuple-compare semantics as F0.5 step 2b. If those drift, 7k either false-positives or false-negatives in lock-step with F0.5's emission bug. The roster appears in two places by design (defense-in-depth via re-derivation); a regression test in `plugins/forge/tests/test_versioned_spec_format.py` (`test_f05_step_2b_and_f09_7k_reference_same_roster`) asserts both prose blocks list the same agent-path set OR sub-check 7k uses an explicit "same hardcoded list as F0.5 step 2b" by-reference phrase.

   - **7m. `intent-coverage.json` present when INTENT-01 not stream-skipped (Phase 8 / INTENT-01).**
     Re-derive the version-gated agent roster using the **same hardcoded list as F0.5 step 2b**
     (cross-plugin); recompute the expected skip set against `manifest.spec_format_version_tuple`;
     if INTENT-01 is NOT in the recomputed skip set, assert `foundry-archive/{run}/intent-coverage.json`
     exists AND `manifest.intent_coverage_summary` is populated. Absence on a v2.1+ spec is itself
     a defect — fires `INTENT_COVERAGE_RECORD_INCOMPLETE`. By-reference to 7k's roster derivation
     (defense-in-depth via re-derivation, not roster duplication).

8. **Migration Coverage** — MIGRATION specs only; 1:1 coverage_list
9. **Spec Structure** — spec has tagged req IDs (error); spec has `## Global Invariants` section (warning)
10. **File Change Map ↔ key_files cross-check** — every file in spec's `## File Change Map` must appear in exactly one casting's key_files (error if orphaned — the change is unimplementable). Files in key_files but not in the map are flagged as scope creep (warning). Skipped if the spec has no File Change Map section.

**Dimension 11 — Pattern Compliance (lead-side manual check, runs after `Foundry-Validate-Castings`):**

For each casting, read its prompt file and verify:

a. **`<analog_pattern>` block exists and is non-empty** — every casting prompt must contain this block. An empty block means decompose failed to inject. Error if missing entirely; error if empty when PATTERNS.md has analog assignments for any of the casting's `key_files`; PASS with warning if PATTERNS.md is SKIPPED and the block contains the SKIPPED sentinel.

b. **Every `key_files` entry that appears in PATTERNS.md `## File Classification` has its full pattern block injected.** Cross-check by grepping the casting prompt for the analog file's name. If `key_files` includes `internal/handlers/auth.go` and PATTERNS.md says its analog is `internal/handlers/users.go`, the casting prompt must contain `internal/handlers/users.go` somewhere inside `<analog_pattern>`. Error if a mapped analog is missing.

c. **`<shared_patterns>` block matches PATTERNS.md `## Shared Patterns` for this casting's role(s).** If PATTERNS.md says "Apply to: every handler" and this casting builds a handler, that shared pattern's excerpt must appear inside `<shared_patterns>`. Warning (not error) if a shared pattern is missing — teammates can sometimes derive cross-cutting patterns from research, but the build is sharper when the excerpt is present.

d. **No paraphrased excerpts.** `<analog_pattern>` and `<shared_patterns>` excerpts must be byte-for-byte from PATTERNS.md (which itself is byte-for-byte from the analog file). Spot-check by reading 1-2 random excerpts from a casting prompt and grepping the cited file:line in the actual codebase. Mismatch = error.

**Severity rules for Dimension 11:**
- 11a missing block: error (re-run decompose for that casting).
- 11b mapped analog missing from prompt: error.
- 11c shared pattern missing: warning.
- 11d paraphrased excerpt: error (decompose violated verbatim rule — re-run).

Skip Dimension 11 entirely if `patterns/PATTERNS.md` does not exist (F0.6 was not run — pre-pattern-mapper run, or pattern-mapper crashed). In that case, log a single warning "Dimension 11 skipped — no PATTERNS.md" and proceed.

**Revision loop:** auto-revise on failures (max 3 iterations), then proceed with warnings.

Call `Foundry-Gate(phase='cast')`.

### F1: CAST

**Router, not interpreter.** Decompose already wrote every teammate prompt. Your job is scheduling + team lifecycle.

1. Determine wave from `manifest.json` dependency graph. Max 5 teammates per wave.
2. `TeamCreate("cast-{run}-wave-N")` → `Foundry-Team-Up` (substitute `{run}` with the active run slug from `Foundry-Next`)
3. `Foundry-Cast-Wave(wave=N, phase="cast")` — single bulk call returns prompts for every casting in the wave. Then, in **ONE message**, spawn parallel Agent tool calls (one per returned casting) with `subagent_type=foundry:teammate`, `mode=bypassPermissions`, `prompt=<that casting's prompt VERBATIM>`. No modification. **Model: obey the returned `instructions` clause verbatim** — when the `model` option is configured it names the model to pass on every teammate Agent call; when it is not, it tells you to pass no `model` parameter and foundry:teammate's frontmatter pin (`model=opus + effort=xhigh`) governs. Do not decide this yourself. Do NOT serialize into separate messages — that's what the bulk tool + parallel tool use exists to avoid.
   - GRIND phase or single re-dispatch: fall back to per-casting `Foundry-Spawn-Teammate(casting_id=N, phase="cast"|"grind")`.
4. Wait for teammates to finish their **work** (report "complete" or task list empty). Then send shutdown in ONE parallel SendMessage batch and **immediately** `TeamDelete` + `Foundry-Team-Down` — do NOT wait for shutdown_response/ack/idle confirmations. Idle panes are the signal; `TeamDelete` kills zombies.
5. Build + test → commit → advance to next wave
6. After all waves: review `concerns.md`. Any concern that relaxes the spec is a decompose failure — re-run F0.5.
7. Call `Foundry-Gate(phase='inspect')`.

**Acceptance check per casting:**

1. `Foundry-Spec-Hash` → fresh hash (forces spec re-read)
2. `Foundry-Spawn-Teammate(casting_id=N)` → fresh prompt hash + text
3. `Foundry-Accept-Casting(casting_id=N, spec_hash=..., prompt_hash=..., completion_report=...)` — returns `acceptance_criteria`, `requirement_ids`, `missing_citations`, `warning`. Non-null `warning` = reject + re-dispatch.
4. Even on `ok: true`, YOU must verify each AC has a corresponding artifact in the completion report.
5. `Foundry-Handoff(event="teammate_to_accepted", ...)` to record acceptance.

### F2: INSPECT (up to 8 parallel streams)

- **TRACE** — agent with `agents/tracer.md` (sonnet). Upstream wiring: EXISTS → SUBSTANTIVE → WIRED → PLACED.
- **FLOW_TRACE** — V3 only, when `flow-delta.json` exists. Agent with `agents/flow-tracer.md` (sonnet). Downstream wiring: PRODUCED → CONSUMES_UPSTREAM → SUBSTANTIVE → CHAIN_INTACT. Pairs with TRACE to cover both directions. Primary catcher of "endpoint exists but is disconnected from its declared upstream" — the exact failure V3 is engineered to prevent.
- **PROVE** — agent with `agents/assayer.md` (opus). Spec-before-code + stub detection + research compliance.
- **RESEARCH_AUDIT** — agent with `agents/research-auditor.md` (haiku). Verifies code honors research. Skip if no research + no Informational items.
- **COVERAGE_DIFF** — MIGRATION only. Agent with `agents/coverage-diff.md` (haiku). 1:1 source → destination check.
- **TEST-01** — agent with `agents/spec-test-deriver.md` (opus, code-blind). Reads spec only; derives hypothesis-jsonschema strategies from TYPE-01 contracts table; runs generated tests in ephemeral worktree; emits findings to `test_observations/test-deriver-cycle-{N}.json`. ASSAY (F4) routes via 5th parallel agent (`agents/test-observations-adjudicator.md`).
- **SIGHT** — lead runs Playwright directly (only exception to "lead never does work").
- **TEST / PROBE** — inline test suite / API smoke.

**Pass the Serena token to TRACE and FLOW_TRACE.** Both wiring streams run on the Serena LSP tools, and both fail open to `NOT_VERIFIED` when those tools are unavailable. When you spawn them, include the `FOUNDRY_SERENA_HEALTH` token you recorded at F0 (see **Serena preflight (F0)** above) in the agent prompt, under the name each agent declares: `FOUNDRY_SERENA_HEALTH` for TRACE (`agents/tracer.md`), `serena_health` for FLOW_TRACE (`agents/flow-tracer.md`). Passing it under the other name delivers nothing — the agent reads only its own field.

The token is diagnostic, never a gate. It lets a `NOT_VERIFIED` record name *which* state the daemon was in (`NOT_INSTALLED`, `INSTALLED_BUT_STOPPED`, `RUNNING_BUT_UNHEALTHY`, `DRIFTED`, `UNKNOWN`) instead of reporting an unattributed failure. **It NEVER gates the spawn, NEVER skips a stream, and NEVER halts the run.** Spawn both streams normally for every token, including `HEALTHY`, and let each agent's own Step 0 gate decide its verdicts — an unhealthy daemon changes what the streams can *verify*, never whether they *run*. If no token was recorded, spawn anyway and omit the field.

*Streams whose agent declares `min_spec_format_version` exceeding `manifest.spec_format_version_tuple` are predictively skipped at F0.5 (see F0.5 V2 step 2b) and recorded in `manifest.stream_skips`. F2 invokes only the streams not in the skip list; F0.9 sub-check 7k re-derives the expected skip set and compares to the recorded array. Phase 3 / TYPE-02.*

Sync all findings: `Foundry-Sync`. Don't trust build-green alone — stubs compile.

Zero defects → `Foundry-Phase("inspect_clean")` → F4. Defects → `Foundry-Phase("grind_start")` → F3.

### F3: GRIND

Same router principle as F1. Lead does NOT draft GRIND prompts.

1. `Foundry-Tasks` — convert defects to per-casting task groups.
2. `TeamCreate("grind-{run}-cycle-N")` → `Foundry-Team-Up` (substitute `{run}` with the active run slug)
3. Per casting with open defects: `Foundry-Spawn-Teammate(casting_id=N, phase="grind")` → spawn Agent with the returned prompt verbatim, APPEND a separate `## Defects to fix this cycle:` block below (the ONLY thing lead may append). **Model: obey the returned `instructions` clause verbatim** — when the `model` option is configured the response also carries a `model` field and the clause names the model to pass on that Agent call; when it does not, pass no `model` parameter and foundry:teammate's frontmatter pin (`model=opus + effort=xhigh`) governs. Do not decide this yourself.
4. Max 3 teammates per GRIND cycle.
5. Shut down → build + test → commit → back to F2 INSPECT.

If a teammate says "this defect requires a spec change": halt, log `SPEC_CHANGE_REQUIRED` to concerns.md, return to F0.5 DECOMPOSE for the affected castings.

### F4: ASSAY

Split requirements into 4 groups → spawn 4 parallel `foundry:assayer` agents using the `agent_config` `Foundry-Next` returns. That config carries no `model` key — assayer holds a fixed opus baseline the `model` option cannot steer — so pass no `model` parameter and let its frontmatter (`model=opus + effort=max`) govern. Each reads spec FIRST, forms expectations, THEN reads code.

**If `test_observations/test-deriver-cycle-{N}.json` exists for the current cycle (Phase 7 / TEST-01)**: spawn a 5th parallel agent — `agents/test-observations-adjudicator.md` (opus + effort=max). It runs `validate-test-observations.py` against the channel file (rejects schema/header/source-leak/wrong-test-pattern violations), then for each pattern-clean FAIL observation classifies a verdict from the closed vocabulary `KNOWN_TEST_OBSERVATION_VERDICTS = {DEFECT, WRONG_TEST, INCONCLUSIVE}`. Routing rule: `status: FAIL` + wrong-test patterns clean → `DEFECT` (route to GRIND with `# defect-source: TEST-01 OBS-NNN` annotation); any wrong-test pattern hit → `WRONG_TEST` (logged for next-cycle drop, NOT routed); `status: ERROR` or `SKIP` → `WRONG_TEST`; `status: PASS` → not routed. Adjudicator appends an `assay_verdict` field per observation to the source JSON. Backwards compat: if the channel file does NOT exist for the current cycle (v2.0 spec stream-skip case, or TEST-01 disabled), the 5th parallel agent is NOT spawned — only the 4 default assayer agents run; Phase 4/5/6 byte-equivalent semantics preserved.

Merge all verdicts via `Foundry-Verdict`. All VERIFIED + zero TEST-01 DEFECT routings → F5/F5.5/F6. Any non-VERIFIED OR any TEST-01 DEFECT → F3 → F2 → F4.

### F5: TEMPER (--temper only)

Micro-domain stress testing. Walk filesystem, classify domains, probe each with Serena. Fix loop per domain (max 3 cycles).

### F5.5: NYQUIST (--nyquist only)

Reached only when the run was created with `nyquist=true` (see **Creating the run (F0), continued**). Enter with `Foundry-Gate(phase="nyquist")` → `Foundry-Phase(phase="nyquist")`.

Generate regression tests for VERIFIED requirements. Batch by 5 → spawn `foundry:nyquist-auditor` agents using the `agent_config` `Foundry-Next` returns. That config carries no `model` key — nyquist-auditor holds a fixed sonnet baseline the `model` option cannot steer — so pass no `model` parameter and let its frontmatter govern. Each classifies COVERED / UNTESTED / UNDERTESTED, generates minimal behavioral tests, runs them, commits passing ones. Any `ESCALATE_IMPL_BUG` result → new GRIND cycle. Never mark untested requirements as passing.

Exit with `Foundry-Gate(phase="done")` → `Foundry-Phase(phase="nyquist_done")` → F6.

### F6: DONE

Shut down all teammates → generate report → `Foundry-Phase("done")`.

## CONTEXT MANAGEMENT

Multi-cycle runs accumulate context. After cycle 2+, if `Foundry-Next` shows `estimated_usage: "high"`: save state via `Foundry-Context`, suggest `/foundry:resume` (fresh context). Do NOT continue in degraded context — it causes more GRIND cycles than it saves.

## MCP TOOLS REFERENCE

| Tool | When |
|------|------|
| `Foundry-Init` | F0: create run — pass `url=<FOUNDRY_URL from setup-foundry.sh>` so the `--url URL` invocation flag is persisted to `castings/manifest.json` `target_url` for the SIGHT/inspect gate, and `nyquist=<FOUNDRY_NYQUIST from setup-foundry.sh>` so the `--nyquist` flag is persisted to `state.json` and reaches the F5.5 routing |
| `Foundry-Next` | Every step: what to do next (returns `YOUR NEXT CALL:` imperative) |
| `Foundry-Gate` | Before phase transitions |
| `Foundry-Phase` | Mark phase transitions |
| `Foundry-Validate-Castings` | F0.9: 9-dimension validate |
| `Foundry-Spawn-Teammate` | F1/F3: read pre-authored teammate prompt |
| `Foundry-Spec-Hash` | Before acceptance: fresh spec hash |
| `Foundry-Handoff` | At every phase/artifact transition; also F0 right after `Foundry-Init` to record the Serena preflight verdict as `event="serena_preflight"`, `summary="FOUNDRY_SERENA_HEALTH=<TOKEN>"` |
| `Foundry-Accept-Casting` | Before marking casting complete |
| `Foundry-Team-Up` | After TeamCreate |
| `Foundry-Team-Down` | After TeamDelete |
| `Foundry-Defect` | Log findings |
| `Foundry-Sync` | Merge findings |
| `Foundry-Tasks` | Convert defects to tasks |
| `Foundry-Fix` | Mark defect fixed |
| `Foundry-Verdict` | Record assay verdicts |
| `Foundry-Coverage` | Traceability matrix |
| `Foundry-Stream` | Mark verification stream complete |
| `Foundry-Context` | Reload state after compaction |

## AGENT PROMPTS

- `agents/tracer.md` — TRACE (sonnet, three-level EXISTS→SUBSTANTIVE→WIRED)
- `agents/assayer.md` — PROVE / ASSAY (opus, spec-before-code + stub detection)
- `agents/codebase-mapper.md` — F0 mapping (haiku, extracts mandatory_rules)
- `agents/researcher.md` — F0 research (sonnet)
- `agents/research-synthesizer.md` — F0 synthesis (haiku)
- `agents/research-auditor.md` — F2 research compliance (haiku)
- `agents/coverage-diff.md` — F2 MIGRATION 1:1 check (haiku)
- `agents/nyquist-auditor.md` — F5.5 test generation (sonnet)
