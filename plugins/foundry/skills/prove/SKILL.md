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
     inputs. Cite file:line.
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
   expectation, file:line, what the code actually does, the gap.

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
3. **Critical path gaps**: which non-VERIFIED items are on the core user journey?
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
- **Verification Checklist**: every VC-N with source, verdict, implementation file:line,
  evidence. Do not truncate.
- **Findings** (CR-N): each non-VERIFIED item consolidated — type, related VC items,
  spec text, what code does, user impact, files, fix direction
- **Systemic Patterns** (SP-N): pattern name, instances, root cause (WHY not WHAT),
  systemic fix approach
- **Observable Truths**: per feature — each OT-N with YES/NO verdict
- **Audit Cross-Reference**: table of findings vs logical/UI audit overlap
- **Overall Assessment**: 2-3 sentences, quality confidence (HIGH/MEDIUM/LOW),
  recommendation (PROCEED/FIX_SYSTEMIC_FIRST/SIGNIFICANT_GAPS)

## Step 6: Decide

**Standalone (`/foundry:prove`):** present report. Done.

**Foundry F4 ASSAY:** return report path, finding counts, and verification %
via the `Foundry-Verdict` MCP tool. Four parallel `foundry:assayer` agents each
verify a domain slice with `effort: max`. SP-N patterns become single fix items
(fix root cause, not instances). HOLLOW verdicts are highest priority. Fix
direction for HOLLOW/PARTIAL must be "FILL OUT" — stubs exist because something
belongs there.

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

**Recommended effort: max (Opus only).** Exhaustive spec-to-code verification demands
maximum reasoning depth. When building API requests, use `effort: "max"`. On Sonnet,
fall back to `effort: "high"`.

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
          "severity": {"type": "string", "enum": ["critical", "high", "medium", "low"]},
          "category": {"type": "string", "description": "missing|hollow|partial|thin|letter-only|wrong|systemic"},
          "file": {"type": "string", "description": "Primary file path"},
          "line": {"type": "integer", "description": "Line number (if applicable)"},
          "description": {"type": "string", "description": "What's wrong, with spec text quoted"},
          "spec_reference": {"type": "string", "description": "VC-N item or spec section cited"},
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
        "verdict": {"type": "string", "enum": ["PASS", "WARN", "FAIL"]}
      }
    }
  }
}
```

**Verdict rules:**
- **FAIL**: any non-VERIFIED item on the critical path
- **WARN**: non-VERIFIED items exist but none on the critical path
- **PASS**: all items VERIFIED (verify this isn't a false positive)

## Spec Citations

Every verdict MUST cite the exact spec section it verifies against. Use the format
`[SPEC:section_id]` to create traceable links from code back to requirements.

**Format:** `[SPEC:US-1.AC-2]`, `[SPEC:FR-3]`, `[SPEC:Section 4.2]`

**Example:**
```
VC-7: "Users can filter credentials by type" [SPEC:US-3.AC-1]
Expectation: GET /credentials?type=postgres returns filtered list
Code: handler.go:52 — ListCredentials reads query param, passes to repo filter
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
- **Spec-anchored** — every finding references exact spec text with `[SPEC:...]` citations
- **Fresh eyes** — read spec and code BEFORE any audit reports
- **Exhaustive** — verify every item, no batching or skipping
- **Evidence-based** — every verdict cites file:line and describes what the code does
