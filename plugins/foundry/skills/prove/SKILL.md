---
name: prove
description: "Relentless spec-to-code verification with fresh eyes. Reads the spec line by line, reads the actual code, and does not stop until every requirement is provably implemented — not just present, but correct. Catches intent gaps, systemic issues, and spec drift that mechanical audits miss."
user_invocable: true
model: opus
effort: max
allowed-tools: Read, Grep, Glob, Bash
context: fork
---

> **Foundry integration:** This skill encodes the methodology used by Foundry's F2 PROVE stream and F4 ASSAY phase. The `assayer` agent (`agents/assayer.md`) is the agent-runtime wrapper around this skill. When run standalone, it produces a verification report; when run by foundry, defects feed F3 GRIND.

# /foundry:prove — Relentless Spec Verification

Read the spec BEFORE the code. Form expectations of what the implementation SHOULD
look like based on the spec alone. Then read the code and compare. The order is
always: **Spec → Expectations → Code → Verdict.** Never Code → Spec.

**Invocation:** `/foundry:prove <spec_path>` or `/foundry:prove <spec_path> --focus US-1,US-3`

## Mindset

Your default assumption is that the code is broken. You are not here to confirm it
works — you are here to find where it doesn't. If you find zero non-VERIFIED items,
you are almost certainly wrong. Go back and read the hardest functions again.

## Step 0: Decompose the Spec — Build the Verification Checklist

**Read the spec BEFORE reading any code.** This prevents rationalization bias.

1. **Read the spec fresh** — load the file. Do NOT read source code yet.

2. **Extract EVERY verifiable requirement** — not summaries. Every single thing the
   spec says the system should do:
   - User stories: each acceptance criterion is a separate item
   - Functional requirements, data models (fields, relationships, constraints)
   - API endpoints with expected behavior
   - UI pages with expected elements and interactions
   - Business rules, error handling, integrations

3. **Extract implicit requirements** — things the spec clearly implies:
   - Roles mentioned -> must be assignable/manageable
   - Lists mentioned -> pagination or scrolling
   - Resource creation -> validation and error feedback
   - Multi-step flows -> navigation between steps
   - User-facing data -> loading/empty/error states
   - APIs -> input validation, auth, error responses
   - Data storage -> duplicates, missing fields, constraints

4. **Number every item** — `VC-1`, `VC-2`, ... The Critic does not stop until every
   item has a verdict.

5. **Write your EXPECTATION per item** — based on spec text alone, what function/
   endpoint/component should exist? What should it do? What inputs/outputs? Example:
   - VC-3: "Users can search products" -> Expect: search endpoint with query param,
     fuzzy matching on name/description, paginated results.

6. **Derive OBSERVABLE TRUTHS per item** (3-5 each) — goal-backward from a USER's
   perspective. Not "handler exists" but "a user can do X and see Y." Example:
   - VC-3 OT-1: Typing "widget" returns products with "widget" in name or description
   - VC-3 OT-2: No matches shows "No results found"
   - VC-3 OT-3: Partial match "wid" finds "widget"

   Observable truths are harder to rubber-stamp than code existence checks. They
   require reading actual logic, not just seeing an import.

## Step 0.5: Read the Width the Run Recorded

The checklist is what the spec says. What you must CHECK this cycle is what the run recorded — you read the width, you never decide it. Call `Foundry-Next` and read `inspect_mode` out of the RESPONSE, not out of the display:

- `inspect_mode.mode` — `FULL` or `DELTA`, decided by the `Foundry-Phase` transition that opened this INSPECT.
- `inspect_mode.prove_sample` — the PROVE roster on a `DELTA` cycle: the rows tied to the defects the preceding GRIND fixed, plus a deterministic sample of the remainder.
- `inspect_mode.cycle` — the cycle that roster belongs to. A roster stamped with a different cycle is not yours.

**That read carries the caller argument, and so does every other one.** If you are a
SUB-AGENT rather than the lead, pass caller='subagent' on every Foundry-Next call. The
lead's call is a protocol step — it arms the ordering token the next Foundry-Gate requires
and resets the stall clock; yours is a read, and passing the argument keeps it one. That
sentence is
`plugins/foundry/mcp-server/src/foundry_mcp/tools/orchestration/guidance.py#SUBAGENT_CALLER_INSTRUCTION`
quoted rather than re-typed (fallout FR-034 / FR-055 / AC-053).

**On `DELTA`, verify exactly the rows in `inspect_mode.prove_sample`** — every one of them and no fewer — and report `items_checked` and `items_total` against that roster's length rather than against the spec. **On `FULL`, verify the whole matrix** and report `items_total` as every requirement in the spec. **With no recorded `inspect_mode` at all, verify the whole matrix**: a missing width means no narrowing was decided, never that you may narrow it yourself.

**Read the array, never the terminal line.** The `Foundry-Next` display truncates the roster at eight rows; the roster itself is `inspect_mode.prove_sample`, below that display and after the marker line. Copying the eight visible rows checks eight rows and reports a width that was never run.

**Where `inspect_mode` actually is: after the marker line.** A formatted tool's response is the rendered display, then a line reading `── machine-readable result ──`, then the complete result as JSON — every array in full, nothing truncated, no fence to strip and no terminator to find, because the JSON runs to the end of the response. Everything after that marker line IS the JSON: `json.loads` it and read `inspect_mode` off the object it returns. That is what "out of the RESPONSE" means here and it is the whole of it — a tool with no display formatter appends no marker, because its entire response is already that JSON. No exceptions, no deferrals, no "the printed list looked complete."

## Step 1: Verify — Line by Line

For EACH checklist item, in order:

1. **Locate the implementation** — grep, glob, read. If Serena MCP is available,
   use `find_symbol` / `find_referencing_symbols` for deterministic wiring checks.

2. **Read the actual function body** — not the signature, not the file name. THE BODY.

3. **Compare against your expectation** — mismatches are findings even if the code
   "works." Trust pre-code expectations over post-code rationalizations.

4. **Mental execution** — trace concrete inputs through the function line by line.
   Then try a bad input. Bug Hunter's Checklist: see `rules/audit-reference.md`.

5. **Verdict** — one of (defined in `rules/audit-reference.md`):
   - **VERIFIED**: Code does what spec says. You traced the full chain with concrete
     inputs. Cite `path#Symbol`.
   - **THIN**: Technically implemented but minimal. Observable truths from the queue
     item are unsatisfied. Feature "exists" but a real user would be disappointed.
   - **HOLLOW**: Code exists but doesn't do real work — empty body, stub, TODO,
     hardcoded data, delegates to something itself hollow.
   - **PARTIAL**: Some of the requirement is implemented but not all. Specify what's
     done and what's missing.
   - **LETTER-ONLY**: Technically satisfies literal words but misses intent. A handler
     returning 200 OK with empty data "handles the request" but doesn't implement
     the feature.
   - **MISSING**: No implementation found.
   - **WRONG**: Implementation actively contradicts the spec.

6. **Evidence required for each verdict**: spec text (quoted), your pre-code
   expectation, the `path#Symbol` cite, what the code actually does, the gap.

Do not batch-verify. Each requirement gets individual verification with its own
evidence. Do not stop or summarize early — verify EVERY item.

## Step 1.5: Scenario Expansion

For each major feature, enumerate reasonable scenarios a real user would expect:

- How many scenarios does this feature have? (create, view, edit, delete, filter,
  search, sort, export, etc.)
- How many are actually implemented? Count them.
- What would a user try NEXT after the happy path?
- What happens at boundaries? (first item, no items, 1000 items, special characters)

Document as "Scenario Coverage" in the report:
- Per feature: list each observable truth (OT-N), mark YES or NO
- Count: OTs satisfied vs total. If ANY OT is NO, the feature is not done.
- Features with unsatisfied OTs are THIN and must be flagged.

## Step 1.7: Displacement Audit — What Should NOT Exist

After verifying what the spec requires, flip the question: **what code exists that
the spec does NOT justify?** This is the surgeon's eye — ruthless identification
of code that should be cut.

For each file touched by the implementation:

1. **List every function/type/route** in the file
2. **For each one, find its spec justification** — which VC-N item requires it?
3. **No justification = DISPLACED** — it's either:
   - **Dead code**: unreferenced, never called (verify with `find_referencing_symbols`)
   - **Superseded**: replaced by new implementation but not removed
   - **Orphaned**: was part of old approach, new approach doesn't need it
   - **Speculative**: added "just in case" with no spec backing

4. **Report as DX-N findings** alongside CR-N findings:
   - DX-1: `old_auth_handler` in auth.go — superseded by new middleware, 0 references
   - DX-2: `LegacyUserType` in models.go — old type, new `User` type replaces it
   - DX-3: `utils/format_date.go` — entire file unused, no imports

**Verdict additions:**
- **DISPLACED**: Code exists but serves no spec requirement. Should be removed.
- **BLOAT**: Code technically works but duplicates what another function already does.

**In foundry PROVE/ASSAY mode:** DX-N findings become defects with fix direction
"DELETE — no spec justification, N references." GRIND teammates remove the dead code
as part of their fix cycle.

**The surgeon's rule:** If you can't point to a spec requirement that needs this code,
it shouldn't exist. New features should REPLACE old code, not pile on top.

## Step 2: Assess

1. **Tally**: VERIFIED vs each non-verified category. What % is truly implemented?
2. **Displacement tally**: how many functions/files exist without spec justification?
3. **Journey placement**: name the user journey each non-VERIFIED item sits on, so
   the report says what a user actually hits. Description, never triage — an item
   nothing on the happy path reaches is a defect on exactly the same terms as one
   the first click hits, and this tally hands you no discretion to rank them.
4. **Cross-cutting concerns**: auth on all protected endpoints? Errors propagated
   with context or swallowed? Race conditions? Input validation at boundaries?

## Step 3: Patterns

Look across ALL non-VERIFIED items for systemic issues:

- **3+ repeated issue class = systemic**: "5 endpoints missing auth" is a missing
  convention, not 5 bugs. "All errors return 500" is a missing error strategy.
- **Root cause chains**: trace issues to their source. "UI shows stale data" <-
  "API doesn't return updated records" <- "no mutation-then-query convention."
- **Architectural concerns**: wrong abstraction level, data model that doesn't
  support spec requirements, tight coupling.

## Step 4: Cross-Reference with Audits

Read audit reports AFTER completing your own verification (fresh eyes first):

- **Agreement**: validates both. Note overlap.
- **Critic-only**: things you found that audits missed — highest value.
- **Audit-only**: things audits found that you didn't — go back and re-verify.
- **Disagreement**: your assessment contradicts an audit — flag for attention.

## Step 5: Report

When run standalone, write to `prove-reports/prove-{timestamp}.md`. When run from foundry, the assayer agent records verdicts via the `Foundry-Verdict` MCP tool. Required sections:

- **Summary**: total items, count per verdict, systemic pattern count, % truly implemented
- **Verification Checklist**: every VC-N with source, verdict, implementation `path#Symbol`,
  evidence. Do not truncate.
- **Findings** (CR-N): each non-VERIFIED item consolidated — type, related VC items,
  spec text, what code does, user impact, files, fix direction
- **Systemic Patterns** (SP-N): pattern name, instances, root cause (WHY not WHAT),
  systemic fix approach
- **Observable Truths**: per feature — each OT-N with YES/NO verdict
- **Audit Cross-Reference**: table of findings vs logical/UI audit overlap
- **Overall Assessment**: 2-3 sentences naming what you drove and what you only
  read, plus the recommendation (PROCEED/FIX_SYSTEMIC_FIRST/SIGNIFICANT_GAPS). No
  confidence ladder over the report as a whole: `tier` already records the evidence
  behind each finding one finding at a time, and a single rung averaged over all of
  them is that axis in a form nothing downstream can act on.

## Step 6: Decide

**Standalone (`/foundry:prove`):** present report. Done.

**Foundry F2 PROVE stream:** record findings via `Foundry-Sync` (or `Foundry-Defect`,
one call per finding), then mark the stream complete via `Foundry-Stream` with
`stream: "prove"`, `cycle`, `items_checked`, `items_total` and `findings_count`.
`stream`, `cycle` and `items_checked` are all REQUIRED — a call omitting any of them is
rejected at the MCP boundary, and a stream that cannot mark itself complete records no
coverage for the cycle at all. Take `cycle` from `Foundry-Next`, and take `items_checked`
and `items_total` from the width Step 0.5 read — on a `DELTA` cycle they are counted
against `inspect_mode.prove_sample`, not against the spec.

**You record your own stream; the lead only confirms the record exists.** A second call
for the same stream and cycle REPLACES the first, names in `replaced` what it replaced,
and keeps every record under `records[]` — so widening the sweep and recording again is
a correction, not a second stream, and the cycle's totals can never exceed 100%. Nobody
records on your behalf: a pass that ends without the call leaves the cycle showing no
PROVE coverage at all, which reads as a stream that did nothing rather than as one that
finished and forgot.

**Which INSPECT filing arm is live is a READ, not a call you make.** `Foundry-Context`
returns `state.temper` — the run's persisted `--temper` setting, written once at
`Foundry-Init` and false unless the lead passed the flag. Take it in the same breath as
the Step 0.5 width read, because it decides what happens to a finding that Step 1.5 or
Step 1.7 turned up OFF your checklist: something you drove, that came back wrong, and
that no VC-N row in Step 0 ever claimed would come back right.

- **TEMPER on.** A defect filed at INSPECT MUST cite the matrix row whose stated
  behaviour failed — the VC-N row's requirement id in `spec_ref`, and the behaviour that
  row states named in the description beside what the code did instead. Everything else
  is a probe idea, and a probe idea is recorded rather than filed: `Foundry-Observation`
  with `cycle`, `source: "prove"`, `description` and
  `classification: "TEMPER_CANDIDATE"`. That is the one observation class whose subject
  is code rather than comment prose, so declaring it lifts the `target_kind: "comment"`
  rung the other four carry. F5 TEMPER reads the recorded candidates before it builds
  its own roster, so a recorded idea is a probe deferred to the phase built for it — not
  a finding dropped.
- **TEMPER off.** F5 never runs, so this step is the whole of the adversarial half and a
  PROVE pass narrowed to checklist rows leaves the cycle with no novel probe driven
  anywhere. Drive the novel probes at INSPECT. A probe you DROVE that failed on a path
  no requirement states is filed as `HARDENING` — never `LIVE` unless the never-demote
  denylist fires — which holds no gate shut and is carried into the F6 backlog. It owes
  the same evidence a `LIVE` filing owes: a `reproduction_attempted` naming the probe
  you ran and the wrong result you saw, without which `Foundry-Defect` and
  `Foundry-Sync` refuse it. Any `spec_ref` on a `HARDENING` filing is refused
  `TIER_NOT_ALLOWED` — citing a requirement is what makes a failure on-row, and an
  on-row failure is `LIVE`.

Neither arm reaches the never-demote denylist, and neither is allowed to. A
security-property claim and a spec-required-behaviour claim can never be parked in a
non-blocking tier and can never be recorded as an observation: the filing is refused
naming the class that matched, the tripwire fires, and the finding stays a blocking
defect. `HARDENING` is a home for a driven failure on a path the spec never stated,
never a quieter rung for one it did. No exceptions, no deferrals, no "the spec never
mentioned it, so it cannot have mattered."

**Foundry F4 ASSAY:** return report path, finding counts, and verification %
via the `Foundry-Verdict` MCP tool. Four parallel `foundry:assayer` agents each
verify a domain slice with `effort: max`. SP-N patterns become single fix items
(fix root cause, not instances). Fix direction for HOLLOW/PARTIAL must be
"FILL OUT" — stubs exist because something belongs there. Nothing here ranks one
verdict above another: every non-VERIFIED verdict is a defect and every defect
gets fixed, so there is no fix order left for this step to state. No exceptions,
no deferrals, no "this one is only cosmetic."

## MCP Validation (optional)

After generating the report, if the `foundry` MCP server is available:

1. Run `Validate-Report` with `schema_name: "prove"` on the report file to validate
   the appended JSON block against the built-in schema.
2. Run `verify_citations` with the spec path and report path to verify traceability —
   every spec requirement should have a verdict, every non-VERIFIED verdict should cite
   spec text.

These are advisory — warn on failures but do not block the report.

## Foundry ASSAY Integration

Critic runs every INSPECT→GRIND iteration. Each time: re-read the spec fresh from
disk, rebuild the full checklist, re-verify every item (even previously VERIFIED —
regressions happen). THIN counts as non-verified. The foundry loop continues until
everything is VERIFIED or max cycles are reached.

## Effort Level

**Recommended effort: max.** Exhaustive spec-to-code verification demands
maximum reasoning depth. When building API requests, use `effort: "max"`. If the running
model's effort ladder does not reach `max`, request the highest level it does offer.
Effort is calibrated per model, so a given level on one model is not equivalent to the
same level on another — read any cross-model comparison with that in mind.

## Spec as Citable Document

When the spec is loaded as a document source, enable citations so every verdict traces
back to exact spec text:

```python
{"type": "document", "source": {...}, "citations": {"enabled": True}}
```

Every VERIFIED/HOLLOW/PARTIAL/MISSING/WRONG verdict MUST cite the specific spec text it
verifies against. The `cited_text` does not count toward output tokens.

**Important:** Citations cannot be combined with structured JSON output (`json_schema`
format). When citations are enabled, use the markdown report format with the JSON block
appended separately (not as the response format).

**Graceful degradation:** If the API does not support citations (e.g., older model
versions), fall back to manual spec references (section/line numbers). The verdict
quality is the same — citations just make traceability automatic.

## Structured Output Format

When outputting verdicts (especially in foundry ASSAY mode or CI), append a JSON block
at the end of the markdown report for machine-parseable consumption. This JSON feeds
the `foundry_add_verdict` MCP tool for defect tracking.

```json
{
  "type": "object",
  "properties": {
    "findings": {
      "type": "array",
      "items": {
        "type": "object",
        "properties": {
          "id": {"type": "string", "description": "Finding ID (CR-N, SP-N)"},
          "classification": {"type": "string", "enum": ["DEFECT", "OBSERVATION"],
            "description": "Channel, not a tier. Comment-prose findings are OBSERVATION; every other finding is a DEFECT. The never-demote denylist overrides this field."},
          "type": {"type": "string",
            "enum": ["MISSING", "WRONG", "THIN", "HOLLOW", "PARTIAL", "UNWIRED",
                     "BROKEN", "FAIL", "ARCHITECTURAL_PLACEMENT", "RESEARCH_DEVIATION",
                     "COVERAGE_INCOMPLETE", "THIN_MIGRATION"],
            "description": "DEFECT_TYPES member. MISPLACED is accepted as an alias and folds onto ARCHITECTURAL_PLACEMENT."},
          "class": {"type": "string",
            "description": "Required root-cause group, non-empty on every filing and spelled identically on every instance that shares it. Not a tier — it is what lets three cycles of one root cause escalate to a single structural fix. Foundry-Defect and Foundry-Sync refuse a filing without it, and one classless finding refuses the whole Foundry-Sync batch."},
          "tier": {"type": "string", "enum": ["LIVE", "LATENT", "HARDENING"],
            "description": "Evidence axis, never a work-effort grade. LIVE: the stream drove the door and observed the wrong result. LATENT: the stream derived the finding and found no reachable instance. HARDENING: the stream drove a probe that failed on a path no requirement states, and the record blocks no gate. Closed vocabulary, source of truth plugins/foundry/mcp-server/src/foundry_mcp/schemas/vocab.py#DEFECT_TIERS."},
          "reproduction_attempted": {"type": "string",
            "description": "Required on a LATENT finding: what was driven and what it found. The server refuses a LATENT filing without one."},
          "file": {"type": "string", "description": "Bare path. Never carries a line number."},
          "symbol": {"type": "string", "description": "The symbol the finding is about. With `file` this is the `path#Symbol` cite."},
          "description": {"type": "string", "description": "What's wrong, with spec text quoted"},
          "spec_reference": {"type": "string", "description": "VC-N item or spec section cited"},
          "suggested_fix": {"type": "string", "description": "Concrete fix direction"}
        },
        "required": ["id", "classification", "type", "class", "tier", "file", "symbol", "description"]
      }
    },
    "summary": {
      "type": "object",
      "properties": {
        "total": {"type": "integer"},
        "by_classification": {
          "type": "object",
          "properties": {
            "DEFECT": {"type": "integer"},
            "OBSERVATION": {"type": "integer"}
          }
        },
        "verdict": {"type": "string", "enum": ["PASS", "WARN", "FAIL"]}
      }
    }
  }
}
```

**There is no `severity` field, and adding one is a vocabulary violation.** The work-effort grade is banned by name — no `minor`, no `major`, no `critical`, no `severity`, no `priority`, no `impact` — because every defect gets fixed and a grade for how much a fix is worth has nothing left to decide. What decides where a finding *goes* is `classification`, which is a channel: comment prose to the observations ledger, everything else to the defect ledger. What records how much evidence stands behind it is `tier`: grade a finding by whether you actually drove it or only derived it from a scan, and never by how much work it would take to fix. `LIVE` means you drove the door and observed the wrong result; `LATENT` means you derived the finding and found no reachable instance, and a `LATENT` finding MUST carry a `reproduction_attempted` statement naming what you drove and what it found — the server refuses a `LATENT` filing without one, and a security-property claim can NEVER be `LATENT`, because that filing is refused naming the denylist class `SECURITY_PROPERTY_CLAIM` and writes a tripwire record. Both tiers are defects, both get fixed, and `tier` buys the stream no discretion over anything else. `type`, `classification` and `tier` are the closed vocabularies, and their one source of truth is `plugins/foundry/mcp-server/src/foundry_mcp/schemas/vocab.py#DEFECT_TYPES`, `#FINDING_CLASSES` and `#DEFECT_TIERS` — a value outside them is rejected server-side rather than coerced onto something known.

**There is no `line` field either.** A finding cites `path#Symbol` — `file` bare, `symbol` beside it. The symbol is authoritative, and the commit-pinned-run-artifact carve-out that permits a line hint does not reach a findings record: this JSON can be passed straight to the foundry defect sync tools and is then re-read cycle after cycle as the tree moves under it, so a line hint rots into a false finding while a symbol cite keeps resolving.

**Verdict rules:**
- **FAIL**: any finding classified `DEFECT` — there is no off-the-critical-path exemption, because that was the severity axis wearing a different name, and no `LATENT` exemption either, because `tier` records the evidence behind a defect and never whether it is worth fixing
- **WARN**: findings exist but every one is classified `OBSERVATION`
- **PASS**: no findings at all — all items VERIFIED (verify this isn't a false positive)

## Spec Citations

Every verdict MUST cite the exact spec section it verifies against. Use the format
`[SPEC:section_id]` to create traceable links from code back to requirements.

**Format:** `[SPEC:US-1.AC-2]`, `[SPEC:FR-3]`, `[SPEC:Section 4.2]`

**Example:**
```
VC-7: "Users can filter credentials by type" [SPEC:US-3.AC-1]
Expectation: GET /credentials?type=postgres returns filtered list
Code: handler.go#ListCredentials — reads query param, passes to repo filter
Verdict: VERIFIED — filter works for valid types, returns empty array for unknown types
```

Every VC-N item in the verification checklist must have a `[SPEC:...]` reference. If a
requirement cannot be traced to a specific spec section, flag it as `[SPEC:implicit]`
and document the inference.

**When no spec is provided:** Note `[SPEC:none — no spec available for citation]` on
each verdict and base verification on observable behavior and code intent.

**Future API integration:** When building API calls that include spec documents, enable
citations for automatic traceability:
```python
{"type": "document", "source": {"type": "text", "data": spec_text}, "citations": {"enabled": True}}
```
The `cited_text` in responses doesn't count toward output tokens (free), and citations
guarantee valid pointers into the provided document.

## Constraints

- **Read-only** — never modify code, only read and report
- **Grade the evidence, never the effort** — every finding carries `tier`, and the stream
  that files it is the one that sets it. `LIVE` when you drove the door and observed the
  wrong result; `LATENT` when you derived the finding and found no reachable instance, in
  which case `reproduction_attempted` names what you drove and what it found or the server
  refuses the filing. A security-property claim can never be `LATENT`. Both tiers are
  defects and both get fixed — `tier` records evidence, never how much work a fix is
  worth. No exceptions, no deferrals, no "this one is only cosmetic."
- **Set `fallout_of` when the finding is fallout of an earlier fix.** A sibling surface
  left on a contract a previous cycle's fix changed is not a fresh defect — it is the
  half of that fix that did not reach, and the CR-N you are about to write is a second
  description of one incomplete repair rather than a new one. Carry `fallout_of` naming
  the `D-NNN` whose fix moved the contract, in the same call as `spec_ref`, `class` and
  `tier`. An id the ledger does not carry is REFUSED at the door rather than stored, so
  take the parent from `defects.json` and never from a fix's own account of itself.
  Unset is correct when the finding stands alone: `scripts/measure-run.py` counts
  fallout per cycle against a target of zero across the last two, so an unmarked cycle
  reads as a clean one and a decorative `fallout_of` reads as a dirty one. No
  exceptions, no deferrals, no "the earlier fix was probably unrelated."
- **Spec-anchored** — every finding references exact spec text with `[SPEC:...]` citations
- **Fresh eyes** — read spec and code BEFORE any audit reports
- **Exhaustive** — verify every item, no batching or skipping
- **Evidence-based** — every verdict cites `path#Symbol` and describes what the code does
- **The symbol is authoritative** — a cite whose symbol resolves is valid however stale any
  line hint beside it has become. No verdict ever turns on the line component, a moved line
  alone produces no finding of any kind, and cite-refresh sweeps happen only under an
  explicit directive. Durable cites are symbol-only, optionally with a quoted snippet; a
  line hint belongs only in a commit-pinned run artifact, where it is frozen against the
  one commit it was written at.
