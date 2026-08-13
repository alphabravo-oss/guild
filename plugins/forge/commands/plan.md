---
description: "Start a codebase-aware specification interview for a feature"
argument-hint: "FEATURE_NAME [--context FILE] [--output-dir DIR] [--no-survey] [--focus DIRS] [--first-principles] [--brownfield] [--greenfield] [--cosmetic]"
allowed-tools: ["Bash(${CLAUDE_PLUGIN_ROOT}/scripts/setup-forge.sh:*)", "Bash(python3:*)", "Bash(find:*)", "Bash(wc:*)", "AskUserQuestion", "Read", "Write", "Edit", "Glob", "Grep", "Agent"]
disable-model-invocation: "true"
---

# Forge Plan Command

Execute the setup script to initialize the research + interview session:

```!
"${CLAUDE_PLUGIN_ROOT}/scripts/setup-forge.sh" $ARGUMENTS
```

You are now conducting a codebase-aware specification interview. Follow the instructions provided by the setup script exactly.

## MODE DETECTION (R-pre)

Before R0, detect which pipeline this run uses. Three modes:

| Mode | When | Pipeline |
|---|---|---|
| **`brownfield`** | existing codebase, flow-shaped request | V3: flow-mapper → flow-interviewer → flow-delta.json (plus spec.md for compatibility) |
| **`greenfield`** | empty or near-empty target, flow-shaped request | V2 pipeline unchanged (end-state-first is correct when there is no upstream to honor) |
| **`cosmetic`** | non-flow-shaped request (styling, copy, deps, docs, minor refactor) in any codebase | V2 pipeline, no flow mapping |

**Detection procedure:**

1. If `--brownfield`, `--greenfield`, or `--cosmetic` flag was passed → use that mode verbatim. Skip auto-detection.
2. Otherwise, auto-detect:
   - Count relevant-language source files under target paths (Go `*.go`, TS/JS `*.ts|*.tsx|*.js|*.jsx`, Python `*.py`, Rust `*.rs`, excluding tests, vendored code, `node_modules/`).
     ```bash
     find "${PROJECT_ROOT}" -type f \( -name "*.go" -o -name "*.ts" -o -name "*.tsx" -o -name "*.js" -o -name "*.jsx" -o -name "*.py" -o -name "*.rs" \) \
       -not -path "*/vendor/*" -not -path "*/node_modules/*" -not -path "*/target/*" -not -path "*/__pycache__/*" \
       -not -name "*_test.go" -not -name "*.test.ts" -not -name "*.test.tsx" -not -name "*.spec.ts" \
       | wc -l
     ```
   - If count ≤ 20 → default `greenfield`.
   - Else → read the user's feature-name / initial prompt. If the request is flow-shaped (adds a new feature, new endpoint, new page, new data flow, new module) → default `brownfield`. If it is cosmetic (styling, copy edit, README update, dependency bump, minor refactor with no behavioral change) → default `cosmetic`.
3. **Confirm with the user via AskUserQuestion** — always. Show the detected mode, the file count, and a one-line rationale. Options: `confirm` | `force-greenfield` | `force-brownfield` | `force-cosmetic` | `abort`.
4. Record the chosen mode to `state.md` as `mode: brownfield|greenfield|cosmetic` before proceeding.

**If mode is `greenfield` or `cosmetic`:** follow the V2 pipeline documented below (R0 → R4) unchanged. Stop reading this section.

**If mode is `brownfield`:** follow the V3 overrides in §V3 BROWNFIELD OVERRIDES below. The V2 phase sections remain as reference for the parts V3 does not override (R1 SYNTHESIZE, R1.5 RESEARCH, R4 VALIDATE are all shared).

## PHASE EXECUTION ORDER

1. **R0: SURVEY** — 4 Explore agents research the codebase (architecture, data, surface, infra) in parallel in a single message (unless --no-survey)
2. **R1: SYNTHESIZE** — Read all survey files, write the reality document
3. **R1.5: RESEARCH** — Targeted online research grounded in what the survey found. Covers both (a) stale-knowledge invalidation for library versions/APIs claimed in reality.md AND (b) ecosystem orientation — common shapes and gotchas for the feature category the interviewer should know about.
4. **R1.75: IMPLICIT-FACT EXTRACTION** — Walk the closed vocabulary (DEPLOYMENT, SCALE, RUNTIME, FRAMEWORK_VERSION, SECURITY, NETWORK, OTHER) and emit a gap-list. Auto-discover environmental facts from reality.md as `## A-AUTO-NNN [IMPLICIT_FACT:CATEGORY]` entries with `[from <source>]` citations; ask the user only the gaps via AskUserQuestion. Under `--no-survey`, ask the full closed vocabulary as a single batched AskUserQuestion at the start of R2. Every implicit fact lands in transcript.md before R2 free-form opens, so downstream Foundry agents (especially INTENT-01 in Phase 8) have citation anchors for constraints the user assumed but never explicitly stated.
5. **R2: INTERVIEW** — Multi-round adaptive interview grounded in codebase + research findings, with **spec_type detection and migration source enumeration**. R2 rule #10 requires that any environmental fact volunteered during free-form is also tagged `[IMPLICIT_FACT:CATEGORY]` in the transcript heading immediately after AskUserQuestion returns.
6. **R3: SPEC** — Generate foundry-ready specification when user says "done". R3 now emits typed tables: populate `## Global Invariants` (5-column table with GI-NNN rows from `[ARCH_INVARIANT]`-tagged answers), `## State Transitions` (6-column ST-NNN table or sentinel row), and `## Contracts` (6-column CT-NNN table or sentinel row). See FINALIZATION SEQUENCE step 2.5 for typed-table synthesis discipline. Phase 6 PROBE-01, Phase 7 TEST-01, and Phase 8 INTENT-01 grep these typed tables as their citation surface — Locked-only `[from A-NNN]` citations preserve the structural anchor.
7. **R3.5: SPEC REVIEW (PROBE-01)** — Adversarial spec reviewer (`plugins/forge/agents/spec-reviewer.md`; the model it runs on is resolved at spawn time — see FINALIZATION SEQUENCE step 4.5) reads `transcript.md` FIRST then the draft spec, emits `spec-review.json` with up to 5 A-NNN-cited ambiguity flags. Validator (`plugins/forge/scripts/validate_spec_review.py`) enforces budget ceiling (5 flags max), citation presence (every flag MUST cite an A-NNN), citation resolves to existing transcript A-NNN, closed schema (no `suggested_fix` / `recommendation` / `warnings` keys at top-level OR per-flag), binary block/pass verdict (no advisory mode), and transcript-first read order via `REVIEWER_ORDER_VIOLATION` token. On block, returns to R2 INTERVIEW for spec revision; loop until pass. SPEC FORGED unreachable until R3.5 clears. Phase 6 PROBE-01 only activates for `spec_format_version: v2.1`+ specs; v2.0 specs skip R3.5 (F0.5 step 2b stream-skip roster covers PROBE-01 for legacy v2.0 specs — no behavior change for v4.2.0-era dependent projects).
8. **R4: VALIDATE** — Verify all file references, pattern references, coverage. validate-spec.py enforces the IMPLICIT_FACT contract: closed vocabulary, A-AUTO-NNN well-formedness ([IMPLICIT_FACT:CATEGORY] tag + [from <source>] citation required), and IMPLICIT_FACT_SKIPPED (warning in Phase 1, will become a hard failure in Phase 3 / TYPE-02 when `spec_format_version >= v2.1`). Phase 2 / TYPE-01 typed tables are also enforced here: presence of `## State Transitions` and `## Contracts` headings (`TYPE_TABLES_MISSING` warns in Phase 2, will become a hard failure in Phase 3 when `spec_format_version >= v2.1`); citation integrity (`TYPED_ROW_BAD_CITATION`, `TYPED_ROW_DANGLING`, `TYPED_ROW_NOT_VERBATIM`); content-difference (`TYPED_ROW_PARAPHRASE`, Jaccard ≥0.7 against same-section prose).

**R0 vs R1.5 vs R1.75:**
- **R0 SURVEY** answers "what does THIS codebase look like?" (inside-in)
- **R1.5 RESEARCH** answers "are the libraries/APIs current, and what does this feature category typically look like in the ecosystem?" (outside-in, grounded in what the survey found)
- **R1.75 IMPLICIT-FACT EXTRACTION** answers "what environmental constraints (deployment, scale, runtime, framework version, security regime, network model) is the user assuming but never stating?" (gap-list scout-then-ask, mirroring GSD's `/gsd:discuss-phase` Step 4 + Step 7 pattern)

All three feed the R2 interviewer. Different jobs, different timing, different depth. R0 is structural (codebase shape), R1.5 is ecosystem (library/feature-category currency), R1.75 is environmental (the deployment/scale/security/runtime context the user takes for granted).

## SPEC TYPE DETECTION (R2) — MANDATORY

During R2 INTERVIEW, you MUST classify this feature as one of four types and record the type in `state.md` and in the final `spec.md` frontmatter:

| Type | When | Examples |
|---|---|---|
| `GREENFIELD` | Building something new that doesn't exist yet | "add a workloads page", "new auth endpoint", "new dashboard widget" |
| `MIGRATION` | Converting/porting/replacing an existing artifact into a new form | "convert legacy tests to ginkgo v2", "migrate from REST to gRPC", "port the go bindings to python", "rewrite the parser in Rust" |
| `BUG_FIX` | Fixing specific broken behavior | "certificate rotation loses old cert on failure", "race in the cache invalidation" |
| `REFACTOR` | Restructuring code without changing external behavior | "extract auth middleware into its own package", "split the god-struct into services" |

**Detection trigger phrases** in the user's initial prompt or interview answers:
- MIGRATION: "convert", "migrate", "port", "replace existing X with", "rewrite Z into Y format", "move from A to B"
- BUG_FIX: "fix", "broken", "doesn't work", "race", "regression", "leak", audit finding references (C-N, H-N, M-N)
- REFACTOR: "extract", "split", "consolidate", "restructure", "reorganize", "clean up"
- GREENFIELD: default if none of the above apply

Ask an explicit classification question using AskUserQuestion if the type isn't obvious from the initial prompt.

## MIGRATION MODE ENFORCEMENT (R2) — IF spec_type is MIGRATION

If you classified the feature as MIGRATION, you have additional mandatory duties in R2:

1. **Enumerate the source inventory.** The user MUST provide (or you MUST generate via grep and ask the user to confirm) a complete list of every source artifact that must be ported. For a test migration: every Test* function in every legacy test file. For a library port: every public symbol. For a protocol migration: every endpoint.

2. **Use grep to generate the candidate inventory.** Example:
   ```bash
   grep -rn "^func Test" legacy/tests/ > /tmp/source-inventory.txt
   ```
   Then present the list to the user via AskUserQuestion and ask them to confirm/prune.

3. **Write the inventory to state.md.** Format:
   ```
   ## source_inventory
   - legacy/tests/auth_test.go:TestLogin
   - legacy/tests/auth_test.go:TestLogout
   - legacy/tests/cache_test.go:TestInvalidate
   ...
   ```

4. **Declare the destination naming rule.** How does source map to destination? Suffix `_v2`? New directory? New file with renamed symbols? The rule must be deterministic so the coverage-diff stream in F2 INSPECT can check it mechanically. Ask the user explicitly.

5. **NEVER accept wiggle-word language as a complete spec.** If the user says "equivalent coverage," "same semantics," "similar to legacy" without an enumerated source list, the spec is NOT finalizable. Refuse to proceed to R3 SPEC until the enumeration is done. This is the hard fix for the D4 failure mode.

6. **In R3 SPEC output**, include the full `source_inventory` and `destination_naming_rule` as top-level fields in both the markdown spec and the JSON spec. Foundry's decompose will read these to populate each casting's `coverage_list`.

## SURVEY RULES (R0)
- Spawn the 4 Explore agents in a SINGLE message (parallel execution)
- Use `subagent_type: "Explore"` for each
- Each agent writes to the survey directory specified in SESSION INFORMATION
- Wait for all 4 to complete before proceeding to R1

**If `--no-survey` was passed:** skip R0. Proceed directly to R2.

## RESEARCH RULES (R1.5)

After writing `reality.md`, identify 2-4 **targeted** research domains grounded in what the survey actually found. Research is NOT generic — it verifies the current state of things the codebase already depends on, or things the feature request implies. Each researcher should also include ecosystem orientation for its domain (common shapes, known failure modes, gotchas worth flagging to the interviewer) as part of its RESEARCH.md output — their existing scope already covers this.

**Good research targets** (the survey found specific things):
- "Codebase uses htmx 1.9 + EventSource for SSE — is 1.9 still current? Any breaking changes in 2.x? Does the SSE extension pattern still work? What are the common gotchas people hit with htmx SSE?"
- "Codebase uses client-go v0.29 for k8s API — what's the current stable version? Any deprecated APIs since? What shape does a listing page for Deployments typically take?"
- "User mentioned 'embedded dashboard' and repo has html/template + embed.FS — is this still the idiomatic Go pattern, and what are the common pitfalls?"

**Bad research targets** (too generic — skip these):
- "How do you build a web UI?"
- "What is Kubernetes?"

**Procedure:**
1. Read `reality.md` that R1 just produced
2. Identify specific technical claims that would be wrong if your training data is stale (library versions, API surface, deprecated patterns, ecosystem shifts)
3. Pick 1-4 narrow domains. **R1.5 always runs** when survey ran — at minimum, spawn 1 researcher covering ecosystem orientation for the feature category (common shapes, known failure modes, gotchas worth flagging to the R2 interviewer), even if no library versions need verifying. If technical claims exist, add up to 3 more researchers to cover them — total 2-4. The only reason to run 0 researchers is the `--no-survey` skip below.
4. Spawn all chosen `researcher` agents in parallel (single message). Use `subagent_type: "forge:researcher"`, or `subagent_type: "general-purpose"` with the full content of `${CLAUDE_PLUGIN_ROOT}/agents/researcher.md` as the prompt
5. Pass to each researcher: domain name, either the specific claim from reality.md to verify OR the "ecosystem orientation for <feature category>" framing, and the output path `{survey_dir}/research-{domain-slug}.md`
6. Each researcher uses WebSearch + WebFetch (Context7 if available in this project's .mcp.json)
7. Wait for all researchers to complete
8. Append a `## Research Findings` section to reality.md summarizing each domain's HIGH/MEDIUM/LOW confidence verdict and the top actionable insight

**Skip condition — only one:** If `--no-survey` was passed, skip R1.5 too. No codebase context means no survey to ground research in. Otherwise R1.5 runs.

**Context budget**: each researcher burns 20-40k tokens in its own context. The interviewer (R2) only reads the research findings summary in reality.md, not raw investigation.

## INTERVIEW RULES (R2)
1. EVERY question must use AskUserQuestion — plain text questions won't work
2. Ground every question in codebase findings (from R0/R1) AND research findings (from R1.5, if applicable)
3. Ask NON-OBVIOUS questions (not "what should it do?" but "I see X pattern in Y file — should we follow it or diverge?")
4. **Use research findings to kill stale-knowledge questions.** If R1.5 verified "htmx 2.x is out and SSE extension is now a separate package", don't ask the user "should we use the built-in htmx SSE?" — you already know. Instead, ask "R1.5 research found htmx 2.x moved SSE to a separate package — do you want to migrate to 2.x as part of this feature, or stay on 1.9?"
5. **Surface research conflicts.** If research found something that contradicts what the user seems to assume, tell them explicitly: "You mentioned X, but my research of current {library} docs shows Y. Which way do you want to go?"
6. Continue until user says "done" or "finalize"
7. Update the draft spec file regularly using the Write tool
8. **VERBATIM TRANSCRIPT:** Append every question AND user answer verbatim to `transcript.md` immediately after each AskUserQuestion returns. Use stable IDs (Q-001, A-001, Q-002, A-002, ...). Never paraphrase; never batch. The transcript is the source of truth — the structured spec.md is an index over it. See R2 rule #8 in setup-forge.sh for the full procedure.
9. **ARCHITECTURAL PLACEMENT DETECTION:** When the user describes *where code lives* ("operator stays generic", "X must not know about Y", "agent handles Z, not the operator", "reuse existing RPC", "treat X as a library"), tag that A-NNN in the transcript with `[ARCH_INVARIANT]`. These answers become the `## Global Invariants` section of the final spec and are propagated verbatim into every foundry casting's `<global_invariants>` block. Missing these at interview time means downstream teammates will put code in the wrong architectural layer.

## FINALIZATION CONSTRAINTS — CRITICAL

When the user says "done", "finalize", "finished", or similar:

### ALLOWED ACTIONS:
- Read any files needed to compile and validate the final spec
- Write the final spec, JSON spec, and progress file
- Use Glob/Grep to validate file references in the spec
- Delete the state file

### FORBIDDEN ACTIONS:
- NO Bash tool calls — do not run any commands
- NO Edit tool calls — do not modify existing code
- NO implementation of any kind — you are ONLY writing spec documents

### FINALIZATION SEQUENCE:
1. **Read `transcript.md` in full** — its bytes for the Appendix, its A-NNN index for citation validation.
2. Generate the spec body (PHASE R3 template) — every Locked item quoted + cited; every other bullet cited via `[from A-NNN]` / `[derived from A-NNN]` / `[from survey/...]`.
2.5. **Synthesize the three typed tables from transcript** before writing the spec body:
   - **Invariants table (## Global Invariants):** walk transcript answers tagged `[ARCH_INVARIANT]`. One row per tagged answer; `statement` = verbatim quote from the answer body; `applies-to` and `violation` derived from the answer body or surrounding answers; `citation` = `[from A-NNN]`. Row ID format: `GI-NNN` (preserve identity from prior versions — a v4.2.0 spec's `**GI-001** [from A-NNN]: "..."` bullet graduates to a `| GI-001 | ... | [from A-NNN] |` row, same ID). If no `[ARCH_INVARIANT]`-tagged answers exist, write a sentinel row: `| — | None — the user gave no explicit placement constraints. | — | — | [from A-NNN reasoning] |`.
   - **State-transitions table (## State Transitions):** walk transcript for state-machine language ("when X happens, Y becomes Z"; "after step N, transition to step M"; "transitions from STATE_A to STATE_B"). One row per identified transition; `from-state`/`to-state` are state-name strings; `trigger` is the event/method/input that fires the transition; `guard` is the precondition. Row ID format: `ST-NNN`. If no transcript-grounded transitions exist, write the documented sentinel row: `| — | — | — | None — this feature has no state transitions | — | [from A-NNN reasoning] |`.
   - **Contracts table (## Contracts):** walk transcript for surface-defining language (function names, endpoints, command lines, observable signatures). One row per identified surface; `surface`/`input`/`output`/`errors` derived from transcript answer body. Row ID format: `CT-NNN`. If no transcript-grounded contracts exist, write the documented sentinel row: `| — | None — no observable contracts beyond internal helper signatures | — | — | — | [from A-NNN reasoning] |`.
   - **Citation form is Locked-only.** Every typed-table row MUST cite `[from A-NNN]` where A-NNN is a Locked transcript answer. NO `[derived from A-NNN]`. NO survey citations. NO `A-AUTO-NNN` citations in typed rows. (Sentinel rows are the one exception — their citation may be `[from A-NNN reasoning]` or `[from survey reasoning]`.)
   - **The 70% Jaccard rule provides backstop discipline against fabrication:** any row whose content-cell tokens overlap the same `## ` section's prose at Jaccard ≥0.7 is rejected by `validate-spec.py` as `TYPED_ROW_PARAPHRASE`. If you find yourself paraphrasing spec prose to fill a row, the row should not exist — go back to the transcript. Phase 6 PROBE-01, Phase 7 TEST-01, and Phase 8 INTENT-01 grep these typed tables as their citation surface; the structural anchor is what makes their work tractable.
3. **Append `## Appendix: Interview Transcript`** with the full byte content of transcript.md pasted verbatim. No truncation.
4. Write the draft spec (body + appendix) to the canonical spec path in one Write call.
4.5. **Run the R3.5 spec-review gate (PROBE-01 — `spec_format_version: v2.1`+ only).** Spawn the spec-reviewer agent (`plugins/forge/agents/spec-reviewer.md`, `id: PROBE-01`). The agent reads `transcript.md` FIRST then the draft `spec.md`, and writes `{SESSION_DIR}/spec-review.json` with up to 5 A-NNN-cited ambiguity flags and a binary block/pass verdict.

   **Model for this spawn — forge's `model` user config option.** The configured value is substituted between the arrows on the line below. Read what is actually sitting between them, then follow whichever of the three cases matches. The arrows are delimiters only and are never part of the value.

   → ${user_config.model} ←

   - **Nothing between the arrows** — or an unsubstituted `${...}` placeholder still sitting between them, meaning the option is not declared in this installed build: the option is unconfigured. Spawn `spec-reviewer` with **no `model` parameter at all**. Never pass `model=""`; an unset option substitutes as an empty string, and an empty string at a spawn is a malformed spawn rather than a no-op. The agent's own frontmatter pin governs, and behavior is byte-identical to a build that never had this option.
   - **Exactly one of `opus`, `sonnet`, `haiku`, `fable`, `inherit`:** pass that value as the `model` parameter on this Agent spawn. This spawn and the `foundry:flow-mapper` spawn at V3 R0 (§V3 BROWNFIELD OVERRIDES → R0 step 1) are the only two spawns in this command that take their model from the option. No other spawn does — `forge:researcher` (R1.5) keeps its own frontmatter pin, and R0's four `Explore` surveyors and this main thread keep inheriting the session model.
   - **Anything else:** refuse. Do not spawn, and do not guess at a nearest match. Report to the user:

     `forge model option: "<value>" is not an accepted value. Accepted values are: opus, sonnet, haiku, fable, inherit.`

     R3.5 does not run until the option is corrected or cleared, so `<promise>SPEC FORGED</promise>` stays unreachable.

   Naming a model the consumer cannot reach does not break the spawn: Claude Code runs the subagent on the newest permitted version of that family, or on the inherited model when the allowlist permits none, and warns — the gate still runs either way.

   **Record the spawn.** Before spawning, state the resolved outcome in your response — `spawning spec-reviewer with model=<value>` when you are passing one, or `spawning spec-reviewer with no model parameter (option unset)` when you are not. Forge ships no MCP server and so writes no spawn log; this line is the record of which model the agent was asked to run on.

   Then run:
   ```bash
   python3 ${CLAUDE_PLUGIN_ROOT}/scripts/validate_spec_review.py <SESSION_DIR>/spec-review.json <TRANSCRIPT_PATH>
   ```
   - **Exit 0 AND `verdict == "pass"`:** proceed to step 5.
   - **Exit 0 AND `verdict == "block"`:** print each flag's `ambiguity` text, return to R2 INTERVIEW (the user's answers append to `transcript.md` as new A-NNN entries, same verbatim discipline as R2), re-run step 4 (regenerate the spec body re-reading the augmented transcript), re-run step 4.5. Loop until `verdict == "pass"`.
   - **Exit 1:** validator rejected `spec-review.json` (schema violation, dangling citation, advisory mode, budget exceeded, order violation, etc.). Read the printed FAIL: lines, fix the spec-review.json (or re-spawn the reviewer agent if it produced malformed output), re-invoke. Do NOT proceed to step 5 until exit 0 + `verdict == "pass"`.

   `<promise>SPEC FORGED</promise>` is structurally unreachable until R3.5 passes. Phase 6 PROBE-01 only activates for `spec_format_version: v2.1`+ specs; v2.0 specs skip step 4.5 (the F0.5 step 2b stream-skip roster covers PROBE-01 for legacy v2.0 specs — no behavior change for v4.2.0-era dependent projects).
5. **Run the deterministic gate:** `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/validate-spec.py <spec.md> <transcript.md>`
   - **Exit 0:** proceed to step 6.
   - **Exit 1:** read the numbered failures, fix the spec via Edit/Write on the canonical spec path, re-run the script. Loop until exit 0. This is a HARD STOP — the script is authoritative, your self-check is not.
6. Write the JSON spec.
7. Write the progress file with all phases marked [PENDING].
8. Delete the state file.
9. Do NOT delete transcript.md — it remains as the working artifact.
10. Output `<promise>SPEC FORGED</promise>`.
11. STOP IMMEDIATELY — do not continue with any other actions.

## REQUIREMENT CLASSIFICATION (verbatim-fidelity enforced)

During finalization (R3: SPEC), classify every requirement into one of three categories. **Locked requirements MUST quote the user verbatim with a transcript citation — see R3/R4 in setup-forge.sh for the hard rules. This section is the conceptual overview; the hard rules override if they conflict.**

### Locked (implement exactly as specified — direct quote from transcript)
Requirements where the user gave specific, concrete instructions. The spec contains the user's literal words in quotes with a `[from A-NNN]` citation pointing at the transcript.md answer it came from. No paraphrase, no interpretation, no "in other words."

Examples (note the quote+cite format):
- **FR-001** [from A-012]: "passwords must be hashed with bcrypt, cost factor 12"
- **FR-002** [from A-015]: "the API must return 429 after 100 requests per minute per user"
- **GI-001** [from A-020]: "operator stays generic — per-node rendering happens in the agent, not the operator" (architectural placement → Global Invariants section)

**If you can't find a verbatim quote in the transcript to support a Locked item, the item is not Locked — it's Flexible, or it needs another interview round. Never invent a quote.**

### Flexible (Claude's discretion on approach)
Requirements where the user described the WHAT but not the HOW. The implementing agent has discretion on approach.

Examples:
- "User sees a loading state while data fetches" (implementation approach flexible)
- "Error messages should be user-friendly" (exact wording flexible)
- UI layout and interaction patterns not explicitly constrained

### Informational (context, not requirements)
Background information the user shared that provides context but is NOT a requirement to implement. **Also: research findings from R1.5 that aren't strict requirements but downstream teammates should know about.**

Examples:
- "The team currently uses Tailwind CSS" (user-provided)
- "Previous auth system used JWT with 15-min expiry" (user-provided)
- "We have 10,000 daily active users" (user-provided)
- "htmx 2.x SSE extension is a separate package — this codebase is on 1.9, not migrating in this feature" (from R1.5 research)
- "client-go v0.30 is current stable; codebase uses v0.29 — no breaking changes relevant to Deployments API" (from R1.5 research)

Auto-populate Informational from `reality.md` `## Research Findings` section during R3 finalization. Every research finding that isn't locked or flexible becomes an Informational item so Foundry teammates downstream see the ecosystem context when they build.

### How to classify
During the interview, track which category each piece of information falls into:
- User says "must", "exactly", "require" → **Locked**
- User describes desired behavior without constraining approach → **Flexible**
- User provides background/context → **Informational**

### Spec output format
In the final spec, group requirements under these headings:

```
## Requirements

### Locked (implement exactly as specified)
- **US-1**: User can log in with email and password
- **FR-3**: Passwords must be hashed with bcrypt (cost factor 12)

### Flexible (Claude's discretion on approach)
- **US-5**: User sees a loading state while data fetches
- **FR-8**: Error messages should be user-friendly

### Informational (context, not requirements)
- The team currently uses Tailwind CSS
- Previous auth system used JWT with 15-min expiry
```

Foundry's CAST teammates use this classification: **Locked** = implement exactly, **Flexible** = best judgment, **Informational** = context only.

### CRITICAL: SPEC FORGED MEANS STOP
After outputting `<promise>SPEC FORGED</promise>`, you MUST stop. Do not:
- Offer to implement the feature
- Suggest next steps for implementation
- Make any code changes
- Run any commands

The spec is the deliverable. Foundry builds it.

---

## V3 BROWNFIELD OVERRIDES

Applies only when R-pre MODE DETECTION set `mode: brownfield`. These overrides replace specific V2 phases with V3 equivalents. Phases not listed here (R1 SYNTHESIZE, R1.5 RESEARCH, R4 VALIDATE) run as documented above.

**Why V3 exists, in one line:** end-state-first specs cause downstream teammates to fabricate plausible-sounding middle plumbing backward from the final feature. V3 replaces the end-state spec with a grounded flow graph plus a node-by-node confirmed delta — the attention anchor is the real system, not the imagined endpoint.

### R0 — V3 override: FLOW-MAP (replaces R0 SURVEY)

In brownfield mode, R0 produces a grounded flow graph instead of the four-agent codebase survey.

**Procedure:**

1. Spawn ONE `flow-mapper` agent (full content of `${CLAUDE_PLUGIN_ROOT}/../foundry/agents/flow-mapper.md` as prompt, or `subagent_type: "foundry:flow-mapper"` if registered).

   **Model for this spawn — forge's `model` user config option.** `flow-mapper` ships in foundry, but forge is what spawns it, so the value that reaches it is **forge's** `model` option — the one set under `forge@guild`, not the identically-named option under `foundry@guild`. The configured value is substituted between the arrows on the line below. This is a second, independent substitution of that option, so resolve it here on its own rather than carrying a decision over from FINALIZATION SEQUENCE step 4.5. Read what is actually sitting between the arrows, then follow whichever of the three cases matches. The arrows are delimiters only and are never part of the value.

   → ${user_config.model} ←

   - **Nothing between the arrows** — or an unsubstituted `${...}` placeholder still sitting between them, meaning the option is not declared in this installed build: the option is unconfigured. Spawn `flow-mapper` with **no `model` parameter at all**. Never pass `model=""`; an unset option substitutes as an empty string, and an empty string at a spawn is a malformed spawn rather than a no-op. The agent's own frontmatter pin governs — or, on the inline-content fallback above, the inherited session model — and behavior is byte-identical to a build that never had this option.
   - **Exactly one of `opus`, `sonnet`, `haiku`, `fable`, `inherit`:** pass that value as the `model` parameter on this Agent spawn. This spawn and the `forge:spec-reviewer` spawn at R3.5 (FINALIZATION SEQUENCE step 4.5) are the only two spawns in this command that take their model from the option. No other spawn does — `forge:researcher` (R1.5) keeps its own frontmatter pin, and this main thread keeps inheriting the session model. V3 brownfield does not run the four V2 `Explore` surveyors at all, and in V2 they keep inheriting the session model too.
   - **Anything else:** refuse. Do not spawn, and do not guess at a nearest match. Report to the user:

     `forge model option: "<value>" is not an accepted value. Accepted values are: opus, sonnet, haiku, fable, inherit.`

     R0 does not run until the option is corrected or cleared. Without a flow graph there is nothing for R2 FLOW-INTERVIEW to confirm hops against, so the V3 pipeline cannot start and `<promise>SPEC FORGED</promise>` stays unreachable.

   Naming a model the consumer cannot reach does not break the spawn: Claude Code runs the subagent on the newest permitted version of that family, or on the inherited model when the allowlist permits none, and warns — R0 still runs either way.

   **Record the spawn.** Before spawning, state the resolved outcome in your response — `spawning flow-mapper with model=<value>` when you are passing one, or `spawning flow-mapper with no model parameter (option unset)` when you are not. Forge ships no MCP server and so writes no spawn log; this line is the record of which model the agent was asked to run on.

2. Input to flow-mapper:
   - `project_root`: the target codebase.
   - `scope_hint`: natural-language description of the subsystem the user's feature will touch. Derive from the feature name + any `--focus` dirs. If you cannot derive a tight scope, ask the user via AskUserQuestion before spawning.
   - `run_dir`: the Forge session's survey directory.
   - `depth_cap: 6`, `size_cap: 120` unless user-overridden.
3. Wait for flow-mapper to complete.
4. Flow-mapper writes `flow-graph.json` to the survey directory. Validate it opens and the `validation: "passed"` summary was returned. If `scope_exceeded: true`, ask the user to narrow the scope and re-run — do not proceed with an incomplete graph.

**Important:** the four V2 Explore agents (architecture, data, surface, infra) do NOT run in V3 brownfield. Their output (codebase reality) is captured structurally by the flow graph.

### R1 — shared with V2

R1 SYNTHESIZE runs unchanged. It reads `flow-graph.json` (instead of the four survey files) and writes `reality.md`. The reality doc summarizes the flow graph's observations (node count, entry points, concerns logged by flow-mapper). Ecosystem orientation comes later in R1.5 RESEARCH.

### R1.5 — shared with V2

R1.5 RESEARCH runs unchanged. Same targeted stale-knowledge invalidation.

### R2 — V3 override: FLOW-INTERVIEW (replaces V2 free-form interview)

In brownfield mode, R2 is conducted **in the main Claude thread** (this session) — NOT by a spawned subagent. Subagents cannot call `AskUserQuestion`, which makes interactive node-by-node confirmation impossible from a subagent runtime. You (the main thread) have `AskUserQuestion` and will use it.

The methodology is documented in `${CLAUDE_PLUGIN_ROOT}/agents/flow-interviewer.md` as a reference. Read it once if you need the full procedure. The executable steps are below.

**Procedure (main thread executes, not a subagent):**

1. **Load the flow graph into context.** Use the `Read` tool to read `{survey_dir}/flow-graph.json` in full. Note the node IDs, kinds, anchors, consumes/produces fields. This becomes your working vocabulary for the interview.

2. **Capture or confirm the user's request.** If the plan command's FEATURE_NAME is a rich description (e.g., "Add a Services page..."), proceed. If it's a thin slug (e.g., "add-services-page"), ask the user via `AskUserQuestion` for a 1–2 sentence description of what they want to build. **This description is the end state.** Forge does not re-ask for the end state — the user has already told you what they want built. Your job from here is to locate the entrypoint and sketch the hops between the two.

3. **Confirm the entrypoint via `AskUserQuestion`** (mandatory). Before sketching any hops, the user must lock the **entrypoint**: the existing graph node where the new chain attaches. This is the single most error-prone decision if Claude guesses it, so it is asked explicitly, not inferred.

   **Procedure:**

   a. Rank nodes from `flow-graph.json` by relevance to the user's end-state description. Consider:
      - Nodes whose `description` mentions terms from the feature request.
      - Nodes that are plausible entry points (handlers, route registrations, entry functions) given the request shape.
      - Nodes near files the user named explicitly (via `--focus` or in the request itself).

   b. Pick the top 3-5 candidates. For each, record the node ID, file:line from its `anchor`, and a one-line description from the graph.

   c. Call `AskUserQuestion` with this exact shape:
      ```
      Question: Where does this new work attach to the existing flow?
      Your end state: "<user's feature description, one sentence>"
      Pick the entrypoint — the existing code location where your new chain begins.

      Options (ranked by relevance from flow-graph.json):
        - <node_id_1>: <description> (<file>:<line>)
        - <node_id_2>: <description> (<file>:<line>)
        - <node_id_3>: <description> (<file>:<line>)
        - other: I know where it attaches but it's not listed
        - unclear: show me the graph, I need to see my options
        - abort
      ```

   d. Handle response:

      - **User picks a ranked candidate** → record the chosen node ID to `state.md` as `entrypoint_node_id: <id>`. Proceed to step 4.

      - **`other`** → follow-up `AskUserQuestion`: *"Tell me the file or symbol where the new work attaches. Example: `handlers/deployment.go:HandleList`."* Verify the answer matches a node's `anchor` field in `flow-graph.json` (grep the anchor field for the user-named symbol). If match → record and proceed. If no match → another follow-up with options: `re-run flow-mapper with wider scope` | `pick from listed candidates anyway` | `abort`. NEVER silently accept an unmatched anchor — that is exactly the forced-decision failure V3 is engineered to prevent.

      - **`unclear`** → respond with TWO pieces of information, then re-ask:
        1. **Graph summary.** Output a compact human-readable summary of `flow-graph.json` — list the top 10-15 nodes by their `description`, grouped by `kind` (handlers, entry functions, routes, etc.), with `anchor` (file:line) for each. Let the user see the shape of the codebase without dumping raw JSON.
        2. **Narrowing question.** In the same response, ask: *"What will a user *first interact with* in your new feature? Clicking a button? Hitting a URL? A background job firing? A new API endpoint being called? Tell me that and we'll work backward from there to find the graph node."* Get a free-text answer via another `AskUserQuestion`.
        Then re-present the entrypoint options, this time ranked with the narrowing info in mind (e.g., if the user said "clicking a button on the settings page," promote sidebar/settings-related nodes to the top).

      - **`abort`** → stop R2, delete no state, let the user `/forge:resume` later.

   e. Append the Q and A verbatim to `transcript.md` using the A-NNN / Q-NNN convention.

   f. Record the confirmed entrypoint to `state.md`:
      ```yaml
      entrypoint_node_id: "<chosen node_id>"
      entrypoint_anchor: "<file:line from the node's anchor field>"
      ```

4. **Sketch the proposed hop chain internally.** Given the flow graph, the user's end-state description, AND the user-confirmed entrypoint from step 3, sketch the hops needed to go from the entrypoint to the end state. You now have TWO locked anchors — the entrypoint (just confirmed) and the end state (from the feature/context) — so the sketch is a constrained problem, not a two-endpoint guess.

   Identify: (a) which new nodes need to be added in flow order between the entrypoint and the end state; (b) the terminal — what user-visible thing closes the chain. Do NOT emit anything to disk yet; this is an internal working copy.

5. **Confirm overall shape via `AskUserQuestion`.** Use exactly this structure:
   ```
   Your request translates to <N> new hops attached to <entrypoint_node_id> (confirmed in previous question).
   Proposed chain: <H1 title> → <H2 title> → ... → <terminal>.
   I'll walk you through each hop individually.
   ```
   Options: `ready` | `adjust shape` | `wider scope` | `abort`.
   - `adjust shape` → take free-form feedback via a follow-up question, re-sketch, re-confirm. The entrypoint stays locked; only the hops between entrypoint and end state are adjusted.
   - `wider scope` → the flow graph is too narrow. Log a concern to `concerns.md` requesting flow-mapper re-run with expanded scope, then STOP R2 until scope is resolved.
   - `abort` → stop the run.

6. **Node-by-node confirmation loop** — one `AskUserQuestion` per proposed new hop, in `flow_position` order:
   ```
   Hop {N} of {total}:
     Title: {short description}
     File: {target file path, relative to project_root}
     Change kind: {new-type|new-method|new-file|new-field|new-route|new-line|modify-method}
     Upstream: {existing node_id from flow graph, OR previous hop ID, OR external:<description>}
       {one-line prose of what upstream produces}
     This hop's produces: {new node_id(s) this hop will create}
     Downstream (if any): {next hop's ID, or "user-visible end state"}
     Pattern to mirror (if applicable): {existing node_id with the same kind in the graph}
       {quote the description field of that node verbatim — teammate will need it later}
   ```
   Options: `y` | `adjust` | `reject` | `why?`.
   - `y` → PIN the hop. Append to your in-memory delta working copy. Move to next hop.
   - `adjust` → take free-form feedback via a follow-up question. Rework the hop (upstream, fields, file, pattern). Re-propose. Loop until user says `y`.
   - `reject` → drop the hop. Later hops that depended on it need re-sketching. Tell the user which downstream hops are affected and re-sketch those branches starting from the nearest surviving upstream.
   - `why?` → answer with the reasoning: what upstream produces, what downstream needs, why this middle node is necessary. Then re-ask.

7. **Transcript discipline.** After each `AskUserQuestion` returns, append the Q + answer verbatim to `{session_dir}/transcript.md` using the existing Forge A-NNN / Q-NNN convention. Never paraphrase; never batch.

8. **Pattern-description honesty.** When a hop proposal says "Pattern to mirror: <node>", `Read` the sibling node's anchor file region BEFORE proposing, and include the real code excerpt — not a paraphrase of the flow graph's description. If the graph's description disagrees with what you see in code, trust the code, update the proposal, and flag a graph-quality concern in `concerns.md` for later flow-mapper improvement.

9. **Validate the delta before emitting.** After the last hop is pinned, verify the V3 well-formedness rules:
   1. Every `consumes.ref` of kind `existing` resolves to a node in the flow graph.
   2. Every `consumes.ref` of kind `packet` references a previously-pinned hop.
   3. `depends_on` is a DAG with no cycles.
   4. No packet `produces` a node_id colliding with an existing graph node.
   5. Every packet has at least one `consumes`.
   6. At least one packet has `flow_position == 1`.
   7. The packet at `flow_position == 1` has a `consumes.ref` matching `entrypoint_node_id` from `state.md` (kind `existing`), OR has an `external` consumes (e.g., k8s API, third-party webhook). An entrypoint confirmed in step 3 that is not consumed anywhere in the delta means the chain does not actually attach where the user said it would — re-interview step 3 or step 4.
   If any check fails, identify the broken hop and re-interview just that hop with the user. Do NOT emit a malformed delta.

10. **Emit `flow-delta.json`.** Use the `Write` tool to create `{survey_dir}/flow-delta.json`. Include `user_intent_summary`, `packets[]` (with `consumes`, `produces`, `depends_on`, `terminal_slice`), `schema_version: "v3.0"`, `flow_graph_ref: "flow-graph.json"`, `generated_at`, and `entrypoint_node_id` (copied from `state.md` — this records the user-confirmed attachment point for downstream traceability).

**CRITICAL: no subagent for R2.** If you catch yourself about to `Agent(subagent_type="forge:flow-interviewer", ...)` or similar, STOP. That is the failure mode we are specifically avoiding. Subagents have no `AskUserQuestion`. The `flow-interviewer.md` file is a methodology reference; you execute the methodology yourself.

**If the user asks to fast-forward (batch-confirm remaining hops):** offer a single `AskUserQuestion` showing all remaining proposed hops as one block, with options `confirm all` / `pick specific hops to review`. Log the override in `concerns.md` so we know they skipped node-by-node.

**If you cannot reach the user** (e.g., non-interactive runtime — `claude --print` with no stdin, CI pipeline, stdin redirected from nowhere): abort R2 with an explicit error rather than making forced decisions. V3 brownfield requires interactivity. The error message should tell the user to re-run in an interactive session OR to use `--greenfield` / `--cosmetic` mode which don't require R2 interaction.

**V2-specific R2 rules that still apply in brownfield:**
- VERBATIM TRANSCRIPT: every question and answer goes to `transcript.md`. Format continues A-NNN / Q-NNN.
- ARCHITECTURAL PLACEMENT DETECTION: when the user's answer describes where code lives (not just what it does), tag the A-NNN with `[ARCH_INVARIANT]`. These become `## Global Invariants` entries in the compatibility spec.md emitted in R3. (Flow-delta's grounding makes placement rules LESS critical than in V2 — since every new node has a declared file — but they still help.)
- spec_type classification: still record as GREENFIELD | MIGRATION | BUG_FIX | REFACTOR in `state.md`. Brownfield-mode is orthogonal to spec_type — a brownfield run can still be a MIGRATION.
- MIGRATION MODE ENFORCEMENT: source inventory and destination naming rule still required for MIGRATION spec_type. Flow-delta carries coverage_list on each packet for the same purpose.

**V2-specific R2 rules that DO NOT apply:**
- Multi-round adaptive interview: the flow-interviewer's node-by-node loop replaces this.
- Spec drafting via incremental Write calls: flow-interviewer writes a delta, not a spec body.
- REQUIREMENT CLASSIFICATION (Locked/Flexible/Informational): in brownfield, the packet's file + consumes + produces + pattern-to-mirror carry the locked constraints structurally. R3 below still emits a compatibility spec.md with classification for Foundry V2 compatibility, but it is derived from the delta, not driven by it.

### R3 — V3 override: DELTA + COMPATIBILITY SPEC

When the user says "done" / "finalize" in brownfield:

1. Confirm `flow-delta.json` exists and passes validation (schema + well-formedness, per the rules in step 9 above).
2. **Emit compatibility `spec.md`** — generated deterministically from the flow-delta:
   - `Problem Statement`: the user_intent_summary from the delta.
   - `Scope → In Scope`: one bullet per packet's terminal_slice.
   - `Requirements → Locked`: one LR-NNN per packet, quoting the packet's title, with `[from P<id>]` citation.
   - `File Change Map`: one row per packet (file + change_kind + consumes/produces summary).
   - `Observable Truths`: derived from the delta's terminal_slices (for Foundry's assayer, which still reads spec.md).
   - `## Flow Delta Reference`: path to flow-delta.json. This is the signal to Foundry's decompose that V3 mode applies.
   - `## Global Invariants`: any `[ARCH_INVARIANT]`-tagged transcript answers (same as V2).
   - `## Appendix: Interview Transcript`: verbatim transcript.md (same as V2).
3. Write both `spec.md` and `flow-delta.json` to the session output directory.
4. Write the JSON spec with a new top-level field `flow_delta_path` pointing to `flow-delta.json`. Foundry will use this to detect V3 mode.
5. Run the deterministic gate: `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/validate-spec.py <spec.md> <transcript.md>` — if the script reports failures specific to V3 idioms it doesn't understand yet, log them to `concerns.md` and proceed (V3-aware validator is future work).
6. Delete `state.md`.
7. Preserve `transcript.md` and `flow-delta.json` — both are authoritative.
8. Output `<promise>SPEC FORGED</promise>`.

### R4 — shared with V2

R4 VALIDATE runs unchanged. In brownfield it additionally checks:
- `flow-delta.json` exists and passes schema.
- Every file referenced in the delta's packets exists in `project_root` (for `modify-*` change_kinds) OR is a valid new path in the same directory tree as existing files (for `new-*` change_kinds).
- Every packet's `consumes.ref` of kind `existing` resolves to a node in `flow-graph.json`.

### Brownfield failure modes and recovery

**Hard rule: V3 NEVER makes forced decisions on the user's behalf.** When any step cannot complete as designed, you either ask the user what to do next (`AskUserQuestion`) or abort the run with an explicit error. You never silently degrade, never guess, never "proceed with a safe default" when the user could have told you their actual preference. Forced decisions are themselves a form of backward fabrication — the agent inventing intent the user did not express — and the whole point of V3 is to eliminate that class of failure.

**What to do when things go wrong:**

- **Flow-mapper fails to produce a graph** (returns error, empty, or `scope_exceeded: true`): STOP. Do NOT silently fall back to V2. Ask the user via `AskUserQuestion`:
  - "Flow-mapper failed / exceeded scope. Options: (a) re-run flow-mapper with a narrower scope_hint, (b) re-run with a wider scope_hint, (c) switch this run to `--greenfield` mode (V2 pipeline), (d) abort."
  - Execute whichever the user picks. Log the reason in `concerns.md`.

- **Flow graph is incomplete for the user's request** (interview step 3 reveals the request touches subsystems not in the graph): STOP R2. Ask the user via `AskUserQuestion`:
  - "Your request touches subsystems the flow graph doesn't cover (list them). Options: (a) re-run flow-mapper with a wider scope_hint to include those subsystems, (b) narrow the feature request to what's in the graph, (c) abort."

- **User appears ambivalent or tired mid-interview** (short answers, "whatever you think", "just pick one"): STOP. Do NOT interpret ambivalence as consent. Tell the user: "Node-by-node confirmation requires your input — I cannot make this call for you. Options: (a) take a break and resume later with `/forge:resume`, (b) explicitly pick one of the options I offered, (c) switch to `--greenfield` mode where free-form spec is acceptable, (d) abort." The A-NNN transcript entry for that Q stays blank until the user actually picks.

- **Non-interactive runtime** (no stdin, `claude --print`, CI pipeline, subagent caller): abort R2 immediately with an explicit error. The error message lists the same options above. Brownfield V3 is interactive-only by design.

- **User wants to fast-forward** (volunteers "just confirm them all"): this is a user choice, not a forced decision — the user explicitly asked. Offer a single `AskUserQuestion` showing every remaining proposed hop as one block, options `confirm all` / `pick specific hops to review` / `abort`. Log the fast-forward in `concerns.md` so the session transcript records that node-by-node was skipped by user request.

**There is no failure mode in which R2 proceeds without the user's explicit input on each hop.** If the runtime cannot support that, the run aborts. The cost of aborting is one re-run; the cost of proceeding with forced decisions is a spec that describes work the user did not agree to, which corrupts every downstream phase.
