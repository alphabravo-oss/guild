---
name: trace
description: "Trace every function, every data flow, and simulate user workflows against the spec. Identifies gaps — missing API calls, disconnected data, unwired functions — that tests and builds don't catch."
user_invocable: true
model: opus
effort: high
allowed-tools: Read, Grep, Glob, Bash
context: fork
---

> **Foundry integration:** This skill's Q1–Q5 methodology is used in Foundry's F2 TRACE stream, enhanced with Serena LSP for deterministic symbol resolution. The `tracer` agent (`agents/tracer.md`) is the agent-runtime wrapper around this skill.

# /foundry:trace — Spec-Anchored Code Completeness Audit

Trace every function and data flow against the original spec. Verify end-to-end
wiring — not just that code exists, but that it's connected and does real work.

**Tests and builds do NOT count as quality gates.** Code that compiles and passes
tests can still have disconnected functions, missing API calls, and incomplete
data flows. The Logical Audit catches what tests miss.

## Mindset: Assume the Code is Broken

Your job is to FIND what's wrong, not confirm the code works. Assume every
function has a bug until you prove otherwise by reading the body. If you finish
with zero findings, you failed — go back and look harder.

## Task

### Step 0: ANCHOR — Load the Spec and Observable Truths

1. **Load the spec** — file path or inline text
2. **Extract all user stories** (US-N) with acceptance criteria
3. **Extract all functional requirements** (FR-N) and technical design
4. **Build a checklist** of every verifiable requirement
5. **Load observable truths** — if casting files or spec sections have
   `--- OBSERVABLE TRUTHS ---` blocks, read them. Each OT-N becomes a verification
   target. Observable truths are often MORE specific than spec requirements and catch
   THIN implementations that technically satisfy the spec but miss the intent.
6. Store the spec reference for re-reading in subsequent verification iterations

### Step 1: STATIC ANALYSIS — Map the Wiring

Read the codebase to build a mental model of what exists and how it's connected.

#### 1a: Inventory

Scan and catalog:

**Backend:** route/endpoint definitions, service/business-logic functions,
repository/data-access functions, data models/types/schemas, middleware,
external integrations (API clients, queues, caches).

**Frontend (if present):** pages/routes, components with API calls, state
management (stores, contexts, reducers), form handlers, navigation flows.

#### 1b: Call Graph

For each entry point (route handler, page component, event handler):
- What functions does it call? What do those call?
- Trace 3-4 levels deep: handler → service → repository → database
- Note dead ends — functions that exist but are never called

#### 1c: Data Flow Map

For each data model/type: where is it created, stored, read, and displayed?
Are there orphan fields (defined but never written or read)?

#### 1d: Wiring Check

Cross-reference inventory against the spec:
- Every API endpoint in spec → handler exists? Registered in router?
- Every data model in spec → type exists? Used in handlers AND repo?
- Every frontend page in spec → route exists? Calls the right APIs?
- Every integration in spec → client wired? Actually called?

**Optional: Serena MCP** — If available, use `find_symbol` and
`find_referencing_symbols` for deterministic wiring verification. Supplements
(does not replace) manual code reading.

Serena is inherited from the session's MCP servers — there is no per-spawn
scoping, so a TRACE agent has it only if the session has it. It requires the
shared HTTP daemon on `localhost:9121`; run `scripts/serena-daemon.sh status` to
check and `scripts/serena-daemon.sh start` to bring it up. When the daemon is
down the Serena tools are simply absent and this step falls back to grep and
Read — record that fallback in the report rather than presenting grep results as
LSP-verified.

### Step 2: FUNCTION-LEVEL DEEP DIVE — The Core of the Audit

**DO NOT skim the call chain.** The #1 failure mode is tracing
`handler → service → repo` and marking each "✓ WIRED" without reading what
the functions DO. "Wired" means nothing if the function body is empty.

For each user story, map the full chain, then **STOP at each function** and
answer ALL five questions. You cannot mark a function ✓ without answering every
question. Write the answers in the report.

**Q1: What does this function actually DO?** (1-2 sentences describing the
logic, not the name. "Accepts a CreateRequest, validates name is non-empty,
calls service.Create with name and type, returns the created ID" — NOT
"creates a foo")

**Q2: Trace concrete data through it.** Pick a realistic input like
`{name: "prod-db", type: "postgres"}` and trace line by line. What happens
to each field? Validated? Stored? Transformed? Ignored? Write the trace:
```
Line 42: req.Name → validated non-empty ✓
Line 43: req.Type → validated against enum ✓
Line 45: calls service.Create(req.Name, req.Type)
Line 46: returns created.ID in response
```

**Q3: What happens with BAD input?** Trace `{name: "", type: "invalid"}`.
Does it return a validation error? Silently accept garbage? Crash? Return
500? Return 200 with no data?

**Q4: Bug Hunter's Checklist.** See `rules/audit-reference.md` for the full
checklist. Run it on every function body.

**Q5: Does this function do ENOUGH?** The spec says "manage credentials" —
does this function only handle CREATE? Where's UPDATE? DELETE? If this
handler covers 1 of 5 CRUD operations, it's THIN.

**Verdict per function:** See `rules/audit-reference.md` for verdict
definitions (SOLID, WIRED but THIN, GAP, BROKEN).

**You MUST write Q1+Q2 answers in the report for every function in the chain.**
This is what makes the audit deep instead of shallow. If you can't describe
what a function does with a specific input, you didn't read it.

#### Error Paths — Trace Them Like Happy Paths

For each story, trace at least 3 error scenarios through the SAME chain with
the SAME Q1-Q5 questions:

- **Validation failure**: `{name: ""}` → what happens at each function?
- **Auth failure**: no token → which middleware catches it? What response?
- **Not found**: ID that doesn't exist → 404 or 500?
- **Server error**: database down → timeout? Retry? Crash?
- **Conflict**: duplicate name → 409 or silent overwrite?

For each error path, trace the FULL chain — don't just say "there's error
handling." Show which line catches the error, what it does, what the user sees.

#### Scenario Completeness — Count What's Implemented vs Expected

For each user story, enumerate EVERY reasonable scenario:

Example — spec says "users can manage credentials":
| # | Scenario | Status | Evidence |
|---|----------|--------|----------|
| 1 | Create a credential | ✓ | handler.go#CreateCredential |
| 2 | View list of credentials | ✓ | handler.go#ListCredentials |
| 3 | View single credential details | ✗ GAP | No GET /:id handler |
| 4 | Edit a credential | ✗ GAP | No PUT handler |
| 5 | Delete a credential | ✓ | handler.go#DeleteCredential |
| 6 | Search/filter credentials | ✗ GAP | No query params |
| 7 | Validate input on create | ✓ THIN | Only checks name |
| 8 | Handle duplicate names | ✗ GAP | No unique constraint |
| 9 | Empty state (no credentials) | ✗ GAP | Returns `null` not `[]` |

**Result: 3/9 = 33% → THIN**

**Minimum 5 scenarios per story.** Every feature has: create, read (list),
read (detail), update, delete, search, filter, validate, error handling,
empty state, permissions. If observable truths are unsatisfied, flag as `THIN-{N}`.

#### Cross-Story Flows

Check interactions: compatible data formats between stories? Cascade/guard on
deletion? Concurrency handling when multiple stories modify the same resource?

#### End-to-End User Workflow Simulation (Plumber Check)

Don't just verify individual function wiring — simulate what a REAL USER does.
For each major feature, walk through the complete workflow as a user would:

1. **Map the workflow** — what sequence of actions does a user perform?
   Example: "Create credential" = navigate to page → click "New" → fill form →
   submit → see success → verify it appears in list → click it → see details

2. **Trace the FULL stack for each step:**
   - Frontend: route → component → API call → state update → render
   - Backend: handler → validation → service → repo → DB → response
   - Return: response → frontend state → re-render → what user sees

3. **Identify breaks in the chain** — where does the flow disconnect?
   - Button exists but onClick is empty
   - API call fires but response isn't used
   - Data saved but list page doesn't refresh
   - Success toast shows but data didn't actually persist
   - Form submits but no loading state during the request

4. **Test the RETURN TRIP** — the most commonly broken flow:
   - Create something → does it show in the list?
   - Edit something → does the edit persist after navigation?
   - Delete something → does it disappear from all views?
   - If any return trip fails, it's a **BREAK** finding (worse than a GAP)

5. **Report as PL-N findings:**
   - PL-1: "Create credential flow breaks at step 4 — form submits (POST /api/credentials
     returns 201) but list page doesn't re-fetch, so new credential is invisible until refresh"
   - PL-2: "Edit flow — EditCredential component reads credential by ID but DetailPage
     doesn't pass the ID prop, so edit form loads empty"

**PL-N findings are high severity** — they represent flows that a user will hit on
their first interaction. A feature with working functions but broken workflows is
worse than a missing feature (users expect it to work and get confused when it doesn't).

In foundry TRACE mode, PL-N findings become defects alongside L-N and THIN-N findings.

### Step 3: CLASSIFY — Number the Findings

- **L-1, L-2...** — Gaps. Description, story ref, `path#Symbol`, what's missing.
- **THIN-1, THIN-2...** — Anemic features with unsatisfied observable truths. Story
  ref, which OTs are YES vs NO, what's missing. **THIN findings are just as important
  as GAP findings.**
- **SA-1, SA-2...** — Spec ambiguities. The ambiguous text, what code does,
  inferred interpretation.
- **DEV-1, DEV-2...** — Deviations. What spec says vs code does, impact.
  Flagged for review, do not block.

### Step 4: REPORT

When run standalone, write to `trace-reports/trace-{timestamp}.md`. When run from foundry, defects are recorded via the `Foundry-Defect` MCP tool. Must include:
- Story traces with function-level Q1+Q2 for every function in each chain
- Scenario completeness tables per story
- Error path traces (3+ per story)
- All findings (L-N, THIN-N, SA-N, DEV-N) with `path#Symbol` references
- Cross-story analysis

### Step 5: DECIDE

**Standalone (`/foundry:trace`):** Present the report. Done.

**Foundry F2 TRACE stream (READ-ONLY):** DO NOT fix findings. DO NOT spawn
agents. Only collect and document. Record findings via the `Foundry-Defect`
MCP tool, then mark the stream complete via `Foundry-Mark-Stream-Complete`
with `items_checked`, `items_total`, and `findings_count`. F3 GRIND converts
findings into fix items.

## MCP Validation (optional)

After generating the report, if the `foundry` MCP server is available, run
`Validate-Report` with `schema_name: "trace"` on the report file to validate
the appended JSON block against the built-in schema. Advisory — warn on failures but
do not block the report.

## Effort Level

**Recommended effort: high.** Deep flow tracing and function-level verification require
sustained reasoning. When building API requests for this skill, use `effort: "high"`.

## Context Efficiency

Write findings incrementally to the report file — do not accumulate everything in
context before writing. When spawned as a foundry TRACE agent with `context: fork`,
the audit runs in an isolated context that does not pollute the foundry lead's
window. State files under `foundry-archive/{run}/` persist findings across context
compactions — the lead re-reads them each iteration via `Foundry-Context`.

## Structured Output Format

When outputting findings (especially in foundry TRACE mode or CI), use this JSON schema
for machine-parseable results. The markdown report remains the primary output; append a
JSON block at the end for tooling consumption.

```json
{
  "type": "object",
  "properties": {
    "findings": {
      "type": "array",
      "items": {
        "type": "object",
        "properties": {
          "id": {"type": "string", "description": "Finding ID (L-N, THIN-N, SA-N, DEV-N)"},
          "severity": {"type": "string", "enum": ["critical", "high", "medium", "low"]},
          "category": {"type": "string", "description": "gap|thin|ambiguity|deviation"},
          "file": {"type": "string", "description": "Primary file path"},
          "line": {"type": "integer", "description": "Line number (if applicable)"},
          "description": {"type": "string", "description": "What's wrong and why"},
          "spec_reference": {"type": "string", "description": "Spec section/requirement ID"},
          "suggested_fix": {"type": "string", "description": "Concrete fix direction"}
        },
        "required": ["id", "severity", "category", "file", "description"]
      }
    },
    "summary": {
      "type": "object",
      "properties": {
        "total": {"type": "integer"},
        "by_severity": {
          "type": "object",
          "properties": {
            "critical": {"type": "integer"},
            "high": {"type": "integer"},
            "medium": {"type": "integer"},
            "low": {"type": "integer"}
          }
        },
        "verdict": {"type": "string", "enum": ["PASS", "WARN", "FAIL"]},
        "items_checked": {"type": "integer", "description": "Number of spec items verified"},
        "items_total": {"type": "integer", "description": "Total spec items in scope"},
        "findings_count": {"type": "integer", "description": "Number of non-passing findings"}
      },
      "required": ["total", "verdict", "items_checked", "items_total", "findings_count"]
    }
  }
}
```

**Verdict rules:**
- **FAIL**: any critical or high finding
- **WARN**: only medium/low findings
- **PASS**: zero findings (rare — verify you didn't miss anything)

This JSON format can be passed directly to the foundry defect sync tools.

## Key Constraints

- **Read-only** — never modify code, only read and report
- **Spec-anchored** — every finding references a spec requirement
- **The symbol is authoritative** — cite `path#Symbol`, never `path:line`. A cite whose
  symbol resolves is valid however stale any line hint beside it has become. No finding
  ever turns on the line component, a moved line alone produces no finding of any kind,
  and cite-refresh sweeps happen only under an explicit directive. Durable cites are
  symbol-only, optionally with a quoted snippet; a line hint belongs only in a
  commit-pinned run artifact, where it is frozen against the one commit it was written at.
- **No AST tooling** — read code directly for semantic understanding
- **Language-agnostic** — works with any language Claude can read
- Do NOT trust tests as evidence — tests can pass with stubs
- Do NOT mark a function ✓ without Q1+Q2 in the report
- Do NOT finish with fewer than 3 findings — real codebases always have gaps
- Do NOT recommend removing code — fix direction is always "fill out the body"
- Do NOT flag cosmetic/style issues — only structural completeness gaps
