---
name: temper
description: "Post-ASSAY micro-domain deep audit. After F4 ASSAY can't find anything else, temper zooms into tiny areas — individual functions, single pages, specific flows — and asks extremely specific questions about whether they actually work. Finds issues that broad audits miss."
user_invocable: false
model: opus
effort: max
---

> **Foundry integration:** This skill's methodology is used as F5 TEMPER in Foundry runs invoked with `--temper`. When invoked by Foundry, temper domains and probe results are written to `foundry-archive/{run}/temper/`.

# Temper — Micro-Domain Deep Audit

Temper activates after F4 ASSAY terminates with zero OPEN findings. Broad audits miss
things because they look at too much at once. Temper zooms into the smallest units of
functionality and proves they work — or proves they don't.

## Mindset: Bug Hunter, Not Reviewer

Your job is to FIND what's broken. Assume every function has a bug until you prove
otherwise by reading the body line by line. If you finish with zero findings, you
failed — re-read the 5 most complex functions and run the checklist again.

---

## Phase C1: DECOMPOSE — Map the Codebase into Micro-Domains

**THIS PHASE IS SPEC-BLIND.** Do NOT read the spec or prior phase findings yet.
Domain discovery is driven entirely by the filesystem. This prevents anchoring on
spec-related code and ensures the ENTIRE codebase is covered.

A micro-domain is the smallest unit of functionality that can be independently verified.

#### Step 1: Walk the Filesystem

Mechanically enumerate the codebase — exhaustive, not creative:

1. List every directory under source roots (`src/`, `pkg/`, `internal/`, `cmd/`, `lib/`, `app/`, etc.)
2. For each directory, list every file and its exported functions/components
3. Do NOT skip any directory — utilities, helpers, middleware, config, migrations, all of it

#### Step 2: Classify into Domain Categories

Group the inventory into micro-domains. Categories:

- **Feature domains** — single API endpoint, single page flow, single UI interaction
- **Infrastructure domains** — middleware, DB connection/pool, config loading, error handling, logging
- **Shared utility domains** — HTTP client wrappers, validation helpers, formatting/parsing, encryption
- **Data lifecycle domains** — single entity lifecycle, state management, cache management
- **Integration domains** — WebSocket/SSE, third-party API clients, file upload/download

#### Step 3: Generate Probe Questions

For each micro-domain, generate 3-5 extremely specific questions about function bodies.
Include both functional probes (does it do what it should?) and code quality probes
(errors, validation, resources, security).

Each domain entry includes:
- Name (e.g., "login-form-submit")
- Entry file:function
- Expected chain (handler → service → repo, etc.)
- 3-5 specific probe questions

**Example:**

```
Domain: login-form-submit
Entry: src/components/LoginForm.tsx:handleSubmit
Chain: handleSubmit → authApi.login() → POST /api/auth/login → authHandler.Login() → authService.Authenticate() → userRepo.FindByEmail()

Probes:
1. Does handleSubmit() call authApi.login() with email and password from form state?
2. Does authApi.login() make a POST to /api/auth/login with credentials in the body?
3. Does authHandler.Login() return the token in the response (not empty body)?
4. Does authService.Authenticate() hash-compare (not plaintext compare)?
5. After success, does the frontend store the token and redirect?
```

**Coverage requirements:**
- Minimum 15 domains for any non-trivial codebase (typical: 20-50)
- Every source directory must have at least one domain — verify after generating the list

### Phase C1.5: CROSS-REFERENCE — Prior Phase Findings

**Only after domains are finalized**, read prior-phase verdicts from
`foundry-archive/{run}/` (defects.json, verdicts from F4 ASSAY). Tag domains
that overlap with known findings (probe deeper) and domains with no overlap
(extra scrutiny — earlier phases never examined these).

### Phase C2: PROBE — Verify Each Micro-Domain

For each micro-domain:

1. **Read the entry point file** — find the entry function
2. **Run the Bug Hunter's Checklist** (see Verdicts and Bug Hunter's Checklist below) on every function body
3. **Answer each probe question** with evidence:
   - **YES** — cite file:line, quote relevant code
   - **NO** — cite file:line or "not found", explain what's missing
   - **PARTIAL** — explain what works and what doesn't
   - **HOLLOW** — code exists but body doesn't do anything useful
4. **Mental execution** — trace concrete data through the chain before rendering a verdict.
5. **Render verdict** — SOLID, THIN, CRACKED, HOLLOW, or MISSING. SOLID means "I would bet money this works."
6. **Generate findings** — for each NO/PARTIAL/HOLLOW probe:
   - ID: `T-{domain}-{probe_number}` (e.g., `T-login-3`)
   - Description, file:line, fix direction

### Phase C3: SUGGESTIONS

While probing, look for improvement opportunities: missing loading/error/empty states,
missing validation, missing confirmations for destructive actions, accessibility gaps,
missing retry/timeout handling, UX improvements.

For each suggestion:
1. Check feasibility (does the backend support it?)
2. Classify: **Minor** (frontend-only, <50 lines, auto-implement) or **Major** (needs
   backend work or significant refactoring → `foundry-archive/{run}/temper/suggestion-backlog.md`)

### Phase C4: REPORT

Create `foundry-archive/{run}/temper/temper-{timestamp}.md`. Required sections:

- **Summary** — counts: domains probed, SOLID/CRACKED/HOLLOW/MISSING/STUCK, findings, suggestions
- **Domain results** — per-domain probe table with columns: #, Probe, Result, Evidence
- **Findings** — each with `T-N` ID, description, file:line, fix direction
- **Suggestions** — minor (implemented) and major (backlog)
- **STUCK domains** — what couldn't be fixed and why

Sync findings to the foundry defect tracker via the `Foundry-Defect` MCP tool with
`source: "temper"`. The lead routes them through F3 GRIND.

---

## Continuous Temper Loop

Temper is NOT a single pass. It cycles: probe → fix → re-probe → SOLID or STUCK.

**Mechanics:**

1. Pick the next unprobed domain (most complex/risky first)
2. Probe it (C2). If SOLID, move on.
3. If findings exist:
   a. Sync findings to the foundry defect tracker via `Foundry-Defect` with `source: "temper"`, `T-` IDs
   b. Route fixes through F3 GRIND (DO NOT fix directly)
   c. After fixes, re-probe the same domain
   d. If still not SOLID after 3 attempts → mark STUCK, move on

**Limits:**
- Per-domain: 3 fix-reprobe cycles max, then STUCK
- Batch efficiently: collect findings from multiple domains into single fix iterations
- If 3+ domains are STUCK, stop temper — issues need human review

**Domain tracker:** `foundry-archive/{run}/temper/domains.md` — tracks each domain's
status, entry point, probe count, pass count, findings fixed, and suggestions. Updated
after every probe pass.

---

## Phase C5: CONTINUOUS SWEEP — After Domains Are Done

After all domains are SOLID or STUCK, temper transitions to file-by-file sweeping.
This catches bugs BETWEEN domains and in files that didn't fit any domain.

### Sweep A: File-by-File Code Review

Walk every source file (not just domain entry points). For each file:

1. Read the entire file — every function body
2. Run the Bug Hunter's Checklist on every function
3. **Find bugs** — missing loading states, useless error messages, wrong dependency
   arrays, unvalidated request bodies, memory leaks, duplicate logic, catch blocks
   that return success
4. **Find missing implementations** — don't just fix what exists, look for what's
   MISSING. A handlers file with Create and List but no Update/Delete is incomplete.
   A form component with no validation is incomplete. A page with no empty state is
   incomplete. These are findings, not suggestions.
5. **Find THIN features** — for every feature area you encounter, ask: how many
   scenarios does this support? Check observable truths from the spec — are any
   unsatisfied? Create a `TS-THIN-{N}` finding for each unsatisfied OT. These get
   escalated to full implementation items in the fix queue, not patches.
6. Follow the chain — read called functions too, a function can be broken in context
7. Track coverage in `foundry-archive/{run}/temper/sweep-{pass}.md`

File ordering: Pass 1 starts from entry points outward. Pass 2+ prioritizes files
changed by fixes, complex files, dependencies of fixed files, then uncovered files.

### Sweep B: UI Crawl (if URL available)

Runs when `--url <url>` was passed to `/foundry:start`. Exploratory, not spec-based.

1. Discover all routes from router config + navigation
2. Exercise everything: links, buttons, forms (valid/empty/invalid data), dropdowns,
   tables, modals, search/filter, back/forward/reload, responsive (375px, 768px)
3. Every interaction must produce a visible DOM change — silent no-ops are findings
4. Classify: F (functional), V (visual), D (data), E (console error), N (network), U (UX)

### Findings routing

After each sweep pass, sync findings via `Foundry-Defect` with `source: "temper-sweep"`
and `TS-` IDs. F3 GRIND fixes them. Then start the next sweep pass focusing on
changed files.

### Sweep never stops

There is no iteration cap on sweep mode. Temper continues until the run completes or
the lead halts it. After a pass with zero new issues: read more carefully, try
different scenarios, check for regressions from previous fixes, read files not yet
covered. Temper never declares itself done.

---

## Key Constraints

- **Read-only during probe** — temper NEVER modifies code during C1–C4; fixes go through F3 GRIND
- **Evidence-based** — every probe answer cites file:line with actual code behavior
- **Exhaustive** — probe every domain, sweep every file; don't skip what "looks fine"
- **No assumptions** — "the function exists" is not evidence; "the function does X at file:line" is
- **Fix direction must be specific** — "fix saveCredentials" is useless; include what the function MUST do

## Effort Level

**Recommended effort: max (Opus only).** Micro-domain probing requires maximum reasoning
depth to catch subtle bugs in individual function bodies. When building API requests, use
`effort: "max"`. On Sonnet, fall back to `effort: "high"`.

## Anti-Patterns

1. Reading the spec during C1 domain discovery — filesystem first, spec later
2. Only generating domains from recently-touched code — walk EVERY directory
3. Skipping infrastructure/utilities/config — these are domains too
4. Using broad scopes like "auth system" — break into micro-domains
5. Answering probes with "YES" without citing the specific line that makes it true
6. **Documenting findings as "known tradeoffs" or "non-blocking" without syncing to
   the foundry defect tracker.** There is no such thing as a non-blocking temper
   finding. Every CRACKED/HOLLOW domain has findings. Every finding MUST be synced.
   F3 GRIND decides what can be fixed — not the auditor. "Known tradeoff" is a fix
   queue item with context, not a reason to skip.
7. **Marking temper complete with CRACKED domains that never got fix attempts.**
   Every non-SOLID domain must get 3 fix attempts before being marked STUCK. The
   orchestrator rejects completion if CRACKED domains exist without fix attempts
   or STUCK status.

---

## Future: Batch API for Probe Parallelization

**Prerequisites:** API key with batch access, large number of independent micro-domains.

Temper probes are inherently independent — each domain is probed in isolation. When
the Batch API is available, submit all domain probes as a single batch for 50% cost
reduction:

1. Collect all micro-domains from C1 discovery
2. Build one probe request per domain (spec context + domain files + probe checklist)
3. Submit as a batch — results arrive asynchronously (up to 24 hours)
4. Parse results: SOLID domains pass, CRACKED domains enter F3 GRIND

**When to use:** Large codebases with 20+ micro-domains where interactive probing
would take hours. Not suitable when fix-probe turnaround speed matters more than cost.
