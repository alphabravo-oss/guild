---
name: temper
description: "Post-ASSAY micro-domain deep audit. After F4 ASSAY can't find anything else, temper zooms into tiny areas — individual functions, single pages, specific flows — and asks extremely specific questions about whether they actually work. Finds issues that broad audits miss."
user_invocable: false
model: opus
effort: max
---

> **Foundry integration:** This skill's methodology is used as F5 TEMPER in Foundry runs invoked with `--temper`. When invoked by Foundry, temper domains and probe results are written to `foundry-archive/{run}/temper/`.

# Temper — Micro-Domain Deep Audit

Temper activates after F4 ASSAY terminates. What terminates ASSAY is the tier-aware
gates, not a count of every finding ever filed: ASSAY passes when no `LIVE` defect and no
unknown-tier defect is open, and an open `LATENT` backlog does not hold it shut — the same
rule that ends temper itself, stated once more under **When the sweep ends** below. Broad
audits miss things because they look at too much at once. Temper zooms into the smallest units of
functionality and proves they work — or proves they don't.

## Mindset: Bug Hunter, Not Reviewer

Your job is to FIND what's broken. Assume every function has a bug until you prove
otherwise by reading the body line by line. If you finish with zero findings, you
failed — re-read the 5 most complex functions and run the checklist again.

---

## Phase C0: ROSTER — read the recorded candidates BEFORE anything else

**Your first tool call is `Foundry-Observations(classification="TEMPER_CANDIDATE")`.** Those
records are the probe ideas the PROVE stream had at INSPECT and was forbidden to file: on a
run with `--temper` set, an INSPECT defect must cite the matrix row whose stated behaviour
failed, and every off-row hunch is recorded as a candidate instead of spent as a GRIND cycle.
TEMPER is the phase those hunches were being saved FOR. Read them before you build anything,
because a candidate you never read is a probe the run paid to think of and then threw away.

**Your roster is the open candidates UNIONED with your own micro-domains, never one or the
other.** Phase C1 below discovers domains from the filesystem and stays spec-blind; this read
does not break that, and the reason is mechanical rather than a promise: a `TEMPER_CANDIDATE`
observation carries no `spec_ref` and no requirement id at all — `Foundry-Observation` refuses
one that does, reading any `spec_ref` as a spec-required-behaviour claim by construction — so
a candidate tells you a place to look and nothing about what the spec says should be there.
Walk the filesystem in C1 without narrowing it to the candidate list, then add every open
candidate to the domains that walk produced.

**Every candidate is closed as DRIVEN — filed or clean, both are closures.** A candidate you
probed and filed a finding against is driven. A candidate you probed and found sound is ALSO
driven: a probe driven and found clean is a result, not a blank, and it is the only result
that ever retires a question. Closure is recorded on the candidate's own observation record,
which the F6 report reads at
`plugins/foundry/mcp-server/src/foundry_mcp/tools/foundry_report.py#_read_undriven_temper_candidates`
— a record carrying `driven` or `status: "DRIVEN"` counts as driven, and a record carrying
neither is UNDRIVEN and is listed by id, cycle and description in the report's non-blocking
backlog beside the `HARDENING` rows. That listing is the whole cost of skipping one, and it
is visible to everyone who reads the run: leaving a candidate alone is a decision the report
prints, not a decision that disappears. No exceptions, no deferrals, no "the domain looked
fine from outside."

**You record your own stream; the lead only confirms the record exists.** Call
`Foundry-Stream` yourself once a pass is done, with `stream`, `cycle`, `items_checked`,
`items_total` and `findings_count` — and `stream` is the WIRE ID OF THE STREAM YOU RAN AS,
a member of the closed vocabulary at
`plugins/foundry/mcp-server/src/foundry_mcp/schemas/vocab.py#STREAM_WIRE_IDS`; read the
members there and never re-type the set here. **`temper` is not one of them, and that is
deliberate rather than an omission.** `temper` is a member of
`plugins/foundry/mcp-server/src/foundry_mcp/schemas/vocab.py#DEFECT_SOURCE_IDS` — it is the
identity you FILE under, which is a different axis from the stream you record coverage for —
so a `Foundry-Stream` call passing `stream: "temper"` is rejected at the MCP boundary and the
pass records no coverage at all. Take `cycle` from `Foundry-Next`. A second call for the same
stream and cycle REPLACES the first, names in `replaced` what it replaced, and keeps every
record under `records[]`, so a re-run is a correction rather than a doubling; a stream that
never records contributes nothing to the cycle's coverage roll-up, where its absence reads as
no coverage rather than as a broken call.

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
   - **YES** — cite `path#Symbol`, quote relevant code
   - **NO** — cite `path#Symbol` or "not found", explain what's missing
   - **PARTIAL** — explain what works and what doesn't
   - **HOLLOW** — code exists but body doesn't do anything useful
4. **Mental execution** — trace concrete data through the chain before rendering a verdict.
5. **Render verdict** — SOLID, THIN, CRACKED, HOLLOW, or MISSING. SOLID means "I would bet money this works."
6. **Generate findings** — for each NO/PARTIAL/HOLLOW probe:
   - ID: `T-{domain}-{probe_number}` (e.g., `T-login-3`)
   - Description, `path#Symbol`, fix direction

### Phase C3: SUGGESTIONS

While probing, look for improvement opportunities: missing loading/error/empty states,
missing validation, missing confirmations for destructive actions, accessibility gaps,
missing retry/timeout handling, UX improvements.

For each suggestion:
1. Check feasibility (does the backend support it?)
2. Route it by WHERE the work lands, never by how much of it there is: a frontend-only
   change is implemented in the run, and one that needs backend work or a refactor goes to
   `foundry-archive/{run}/temper/suggestion-backlog.md`. Nothing here routes on how large a
   fix looks, and no line count appears in the rule — a size threshold is the work-effort
   grade wearing a number, and it reinstates on the routing axis exactly what the next
   sentence abolishes on the naming one. **Never grade a suggestion by effort** — no
   `minor`, no `major`, no `critical`, no `severity`, no `priority`, no `impact`, and no
   fresh spelling for the same axis next cycle. That is the same six names `## Key
   Constraints` bans on a defect, banned here on a suggestion, so the two rules cannot
   drift into disagreeing about which words are the axis. The work-effort grade is
   abolished across this release and a suggestion is not where it comes back. `tier` grades
   a FINDING by whether you drove it or derived it, which is evidence rather than effort; a
   suggestion carries no grade at all, only a destination.

### Phase C4: REPORT

Create `foundry-archive/{run}/temper/temper-{timestamp}.md`. Required sections:

- **Summary** — counts: domains probed, SOLID/CRACKED/HOLLOW/MISSING/STUCK, findings, suggestions
- **Domain results** — per-domain probe table with columns: #, Probe, Result, Evidence
- **Findings** — each with `T-N` ID, description, `path#Symbol`, fix direction
- **Suggestions** — the ones implemented in the run, and the ones routed to the backlog
- **STUCK domains** — what couldn't be fixed and why

Sync findings to the foundry defect tracker via the `Foundry-Defect` MCP tool with
`source: "temper"`, each carrying `tier`, `defect_class`, `target_kind` and — on a `LATENT`
filing — `reproduction_attempted`, per the filing rules in `## Key Constraints`. The lead
routes them through F3 GRIND.

---

## Continuous Temper Loop

Temper is NOT a single pass. It cycles: probe → fix → re-probe → SOLID or STUCK.

**Mechanics:**

1. Pick the next unprobed domain (most complex/risky first)
2. Probe it (C2). If SOLID, move on.
3. If findings exist:
   a. Sync findings to the foundry defect tracker via `Foundry-Defect` with `source: "temper"`,
      `T-` IDs, each carrying `tier`, `defect_class`, `target_kind` and — on a `LATENT` filing —
      `reproduction_attempted`, per the filing rules in `## Key Constraints`
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

After each sweep pass, sync findings via `Foundry-Defect` with `source: "temper"`
and `TS-` IDs, each carrying `tier`, `defect_class`, `target_kind` and — on a `LATENT`
filing — `reproduction_attempted`, per the filing rules in `## Key Constraints`. F3 GRIND
fixes them. Then start the next sweep pass focusing on changed files.

### When the sweep ends

There is no iteration cap on sweep mode, and a pass that found nothing is not a reason
to stop. After a pass with zero new issues: read more carefully, try different
scenarios, check for regressions from previous fixes, read files not yet covered. A
quiet pass is evidence about the pass, not about the code.

**But temper does have an end, and it is mechanical.** Temper is over when NO `LIVE`
defect is open and EVERY escalated class is `CLEARED`. Both halves are read from the
run's own ledgers, so neither is a judgement call: `LIVE` defects are counted by the
same tier-aware gates that count them everywhere else, and a class clears by drawing
zero `LIVE` instances for two consecutive INSPECT cycles or by exhausting its
structural-pass budget. An open `LATENT` backlog does NOT hold temper open — those
defects stay tracked and land in the F6 report's named backlog. Until both conditions
hold, keep sweeping; once they do, temper is finished and says so.

---

## Key Constraints

- **Read-only during probe** — temper NEVER modifies code during C1–C4; fixes go through F3 GRIND
- **Evidence-based** — every probe answer cites `path#Symbol` with actual code behavior
- **The symbol is authoritative** — a cite whose symbol resolves is valid however stale any
  line hint beside it has become. No probe answer ever turns on the line component, a moved
  line alone produces no finding of any kind, and cite-refresh sweeps happen only under an
  explicit directive. Durable cites are symbol-only, optionally with a quoted snippet; a
  line hint belongs only in a commit-pinned run artifact, where it is frozen against the one
  commit it was written at.
- **Every temper finding carries a `tier`, like any other stream's.** Probing IS driving the
  domain, so most temper findings are `LIVE`. Temper gets no separate tier vocabulary and no
  discretion the other streams lack: escalation exits by the same rule everywhere, and both
  tiers are defects that get fixed. The five rules below are the ones every defect-filing
  stream carries, word-identically, and they are reproduced here rather than summarised:
  temper files into the same ledger through the same doors, so a micro-domain probe is a
  different lens on the code, not a different contract with the defect ledger. A rule restated
  in temper's own words would be one more spelling of a refusal the doors report in one.
- **Name the class when instances share a root cause.** Three HOLLOW verdicts behind one missing middleware are one class, not three unrelated defects — put the shared root cause in each record's `class` field, spelled identically across every instance (`Foundry-Defect` takes it as `defect_class`; `Foundry-Sync` reads it as `class`). A class that draws new defects for three consecutive cycles escalates to a single structural-fix packet, and that only fires if you named it — `systemic_patterns` is your prose summary and nothing downstream consumes it. Name a class on EVERY defect, including one that genuinely stands alone — a single-instance class is still a class, and `Foundry-Defect` and `Foundry-Sync` refuse a filing whose `class` is empty. Never invent a class to bundle findings that do not share a cause.
- **No severity classification.** **No severity tiers.** The work-effort grade is banned by name — no `minor`, no `major`, no `critical`, no `severity`, no `priority`, no `impact`, and no fresh spelling invented next cycle — because every defect gets fixed and a grade for how much a fix is worth has nothing left to decide. Grade a finding by whether you actually drove it or only derived it from a scan, and never by how much work it would take to fix: the first is the `tier` axis the next rule makes required, the second stays abolished. `tier` is evidence, not effort, and it displaces nothing below it — `classification` still decides the channel a finding goes down and `target_kind` still rides on every filing. No exceptions, no deferrals, no "this one is only cosmetic."
- **Set `tier` on every filing; the stream that files the defect is the one that sets it.** `tier` is a closed two-member vocabulary declared once at `plugins/foundry/mcp-server/src/foundry_mcp/schemas/vocab.py#DEFECT_TIERS` — read the members there and never re-type them anywhere else. `LIVE` means you drove the door and observed the wrong result, and the description names both the door and the result. `LATENT` means you derived the finding and found no reachable instance, and that filing MUST carry a `reproduction_attempted` statement naming what you drove and what it found ("AST sweep of both roots finds 0 sites"); `Foundry-Defect` and `Foundry-Sync` refuse a `LATENT` filing without one. A security-property claim can NEVER be `LATENT` — that filing is refused naming the denylist class `SECURITY_PROPERTY_CLAIM` and writes a tripwire record, so a claim that a security property is broken is one you drive and file `LIVE`, or one you do not file at all. Both tiers are defects, both get fixed, and `tier` buys you no discretion over anything else. No exceptions, no deferrals, no "I could not reproduce it, so it is probably fine."
- **Comment-prose findings are observations, not defects.** A drifted line number in a cite, a count stated in prose, a direction word ("above", "below", "the following"), an enumeration that no longer matches the thing it enumerates — that class is comment prose. Record it in the run's `observations.json` ledger, never in the `defects` array; `Foundry-Defect` and `Foundry-Sync` refuse it as a defect server-side. This is a channel, not a severity tier, and it buys you no discretion over anything else.
- **Declare `target_kind` on every filing.** That refusal is not automatic — it fires only when your call DECLARES what the finding is about: `target_kind: "comment"` when the verdict concerns a code comment, otherwise what the subject really is (`code`, `test`, `config`, `doc`). Omit the field and the server has nothing to judge, so a line-drift finding is accepted into `defects.json` and the split above did nothing. Every `Foundry-Defect` and `Foundry-Sync` call carries it, on every verdict, including the ones you are certain about.
- **Exhaustive** — probe every domain, sweep every file; don't skip what "looks fine"
- **No assumptions** — "the function exists" is not evidence; "`path#Symbol` does X" is
- **Fix direction must be specific** — "fix saveCredentials" is useless; include what the function MUST do

## Effort Level

**Recommended effort: max.** Micro-domain probing requires maximum reasoning
depth to catch subtle bugs in individual function bodies. When building API requests, use
`effort: "max"`. If the running model's effort ladder does not reach `max`, request the
highest level it does offer. Effort is calibrated per model, so a given level on one
model is not equivalent to the same level on another — read any cross-model comparison
with that in mind.

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
   Every non-SOLID domain must get 3 fix attempts before being marked STUCK. What
   actually holds the run is the tier-aware gates: they refuse while any `LIVE` or
   unknown-tier defect is open, and the done gate refuses while an escalated class is
   not `CLEARED`. Nothing anywhere inspects domain status, so a CRACKED domain whose
   findings were never filed is invisible to every gate — which is why anti-pattern 6
   above is the one that matters. File the findings and the gates do the rest.

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
