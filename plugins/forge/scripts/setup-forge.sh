#!/bin/bash

# Forge Setup Script
# Creates state file and initializes the research + interview session
# Forge researches. Forge interviews. Foundry builds.

set -euo pipefail

# Parse arguments
FEATURE_NAME=""
CONTEXT_FILE=""
OUTPUT_DIR="forge-specs"
MAX_QUESTIONS=0  # Unlimited by default
NO_SURVEY=false
FIRST_PRINCIPLES=false
FOCUS_DIRS=""
USER_PROMPT=""

while [[ $# -gt 0 ]]; do
  case $1 in
    -h|--help)
      cat << 'HELP_EOF'
Forge - Codebase-Aware Specification Engine

Forge researches. Forge interviews. Foundry builds.

USAGE:
  /forge:plan <FEATURE_NAME> [OPTIONS]

ARGUMENTS:
  FEATURE_NAME    Name of the feature to spec out (required)

OPTIONS:
  --prompt <text>       Tell forge what you want (e.g., "refine this spec deeper", "add error handling")
  --context <file>      Initial context file (PRD, requirements, spec to refine, etc.)
  --output-dir <dir>    Output directory for specs (default: forge-specs)
  --max-questions <n>   Maximum question rounds (default: unlimited)
  --no-survey           Skip codebase survey (for greenfield/empty projects)
  --first-principles    Challenge assumptions before detailed spec gathering
  --focus <dirs>        Comma-separated directories to focus survey on (e.g., src/auth,src/api)
  -h, --help            Show this help

DESCRIPTION:
  Forge runs parallel codebase research, then conducts a grounded interview,
  then writes a foundry-ready spec. It studies your codebase FIRST, then asks
  smart questions grounded in what it found.

  Phase R0:   SURVEY     - Parallel agents explore architecture, data, surface, infra
  Phase R1:   SYNTHESIZE  - Merge findings into codebase reality document
  Phase R1.5: RESEARCH    - Targeted online research: stale-knowledge + ecosystem orientation
  Phase R2:   INTERVIEW   - Multi-round adaptive interview (grounded in R0/R1/R1.5)
  Phase R3:   SPEC        - Generate foundry-ready spec (US/FR/NFR/AC/OT IDs)
  Phase R4:   VALIDATE    - Self-check all file refs, patterns, coverage

  The interview continues until you say "done" or "finalize".

EXAMPLES:
  /forge:plan "user authentication"
  /forge:plan "payment processing" --context docs/PRD.md
  /forge:plan "search feature" --focus src/search,src/api
  /forge:plan airgap-e2e --context docs/specs/airgap.md --prompt "refine this spec deeper"
  /forge:plan auth-system --context docs/PRD.md --prompt "focus on error handling and edge cases"
  /forge:plan "new dashboard" --first-principles
  /forge:plan "greenfield api" --no-survey

OUTPUT:
  Final spec:     {output-dir}/{feature-slug}/spec.md
  Structured JSON: {output-dir}/{feature-slug}/spec.json
  Survey data:    {output-dir}/{feature-slug}/survey/
  Progress:       {output-dir}/{feature-slug}/progress.txt
  Draft:          .claude/forge-draft.md

WORKFLOW:
  1. Forge researches + interviews: /forge:plan "my feature"
  2. Foundry builds + verifies:     /foundry --spec forge-specs/my-feature/spec.md

  Forge plans. Foundry builds. Ship with confidence.
HELP_EOF
      exit 0
      ;;
    --context)
      CONTEXT_FILE="$2"
      shift 2
      ;;
    --output-dir)
      OUTPUT_DIR="$2"
      shift 2
      ;;
    --max-questions)
      MAX_QUESTIONS="$2"
      shift 2
      ;;
    --no-survey)
      NO_SURVEY=true
      shift
      ;;
    --first-principles)
      FIRST_PRINCIPLES=true
      shift
      ;;
    --prompt)
      USER_PROMPT="$2"
      shift 2
      ;;
    --focus)
      FOCUS_DIRS="$2"
      shift 2
      ;;
    *)
      if [[ -z "$FEATURE_NAME" ]]; then
        FEATURE_NAME="$1"
      else
        FEATURE_NAME="$FEATURE_NAME $1"
      fi
      shift
      ;;
  esac
done

# Validate feature name
if [[ -z "$FEATURE_NAME" ]]; then
  echo "Error: Feature name is required" >&2
  echo "" >&2
  echo "   Example: /forge:plan \"user authentication\"" >&2
  exit 1
fi

# Create output directories
mkdir -p "$OUTPUT_DIR"

# Generate slug from feature name (strip path components, max 60 chars)
FEATURE_SLUG=$(basename "$FEATURE_NAME" .md | tr '[:upper:]' '[:lower:]' | tr ' ' '-' | tr -cd '[:alnum:]-' | cut -c1-60)
SPEC_PATH="$OUTPUT_DIR/$FEATURE_SLUG/spec.md"
JSON_PATH="$OUTPUT_DIR/$FEATURE_SLUG/spec.json"
PROGRESS_PATH="$OUTPUT_DIR/$FEATURE_SLUG/progress.txt"
DRAFT_PATH="$OUTPUT_DIR/$FEATURE_SLUG/draft.md"
STATE_PATH="$OUTPUT_DIR/$FEATURE_SLUG/state.md"
TRANSCRIPT_PATH="$OUTPUT_DIR/$FEATURE_SLUG/transcript.md"
SURVEY_DIR="$OUTPUT_DIR/$FEATURE_SLUG/survey"
REALITY_PATH="$OUTPUT_DIR/$FEATURE_SLUG/reality.md"
TIMESTAMP=$(date +%Y-%m-%d)

# Create survey directory
mkdir -p "$SURVEY_DIR"

# Read context file if provided
CONTEXT_CONTENT=""
if [[ -n "$CONTEXT_FILE" ]] && [[ -f "$CONTEXT_FILE" ]]; then
  CONTEXT_CONTENT=$(cat "$CONTEXT_FILE")
fi

# Detect project info for survey guidance
PROJECT_LANG=""
if [[ -f "go.mod" ]]; then PROJECT_LANG="go"
elif [[ -f "package.json" ]]; then PROJECT_LANG="javascript/typescript"
elif [[ -f "pyproject.toml" ]] || [[ -f "setup.py" ]]; then PROJECT_LANG="python"
elif [[ -f "Cargo.toml" ]]; then PROJECT_LANG="rust"
elif [[ -f "Package.swift" ]]; then PROJECT_LANG="swift"
fi

# Count source files for survey sizing
SRC_COUNT=$(find . -maxdepth 5 -type f \( -name "*.go" -o -name "*.ts" -o -name "*.tsx" -o -name "*.js" -o -name "*.jsx" -o -name "*.py" -o -name "*.rs" -o -name "*.swift" \) ! -path "*/node_modules/*" ! -path "*/.git/*" ! -path "*/vendor/*" 2>/dev/null | wc -l | tr -d ' ')

# Build the interview prompt
PROMPT_FILE=$(mktemp)

# =========================================================================
# PHASE R0: SURVEY — Parallel codebase research
# =========================================================================

if [[ "$NO_SURVEY" == "false" ]]; then

# Build context-aware scope guidance for survey agents
SCOPE_GUIDANCE=""
if [[ -n "$CONTEXT_CONTENT" ]]; then
  # Extract a summary of the context for agents (first 200 lines max to avoid bloat)
  CONTEXT_SUMMARY=$(echo "$CONTEXT_CONTENT" | head -200)
  SCOPE_GUIDANCE="
SCOPE GUIDANCE — A context file was provided. Focus your exploration on the areas
of the codebase RELEVANT to this context. Do not map the entire repo — focus on
what matters for this feature/spec:

--- CONTEXT SUMMARY ---
$CONTEXT_SUMMARY
--- END CONTEXT SUMMARY ---

Explore code related to the above. Skip unrelated packages/modules."
fi

if [[ -n "$FOCUS_DIRS" ]]; then
  SCOPE_GUIDANCE="$SCOPE_GUIDANCE

FOCUS DIRECTORIES: $FOCUS_DIRS
Prioritize these directories. You may look outside them for dependencies and patterns,
but spend most of your time within these paths."
fi

cat > "$PROMPT_FILE" << SURVEY_PROMPT_EOF
# Forge Specification Engine

You are conducting a codebase-aware specification interview. Unlike a standard interview, you RESEARCH THE CODEBASE FIRST, then ask smart questions grounded in what you found.

## PHASE R0: SURVEY — Codebase Research

Before asking the user a single question, spawn 4 parallel Explore agents to survey the existing repo. **All 4 agents are spawned in a SINGLE message.** Use the Agent tool with \`subagent_type: "Explore"\` for each. Wait for all 4 to complete before proceeding to R1.

Ecosystem orientation (common shapes, gotchas for the feature category) happens later in R1.5 RESEARCH, grounded in what the survey actually found.
$SCOPE_GUIDANCE

### Agent 1: ARCHITECT
\`\`\`
Explore the architecture of this codebase$(if [[ -n "$SCOPE_GUIDANCE" ]]; then echo " relevant to the feature described in the SCOPE GUIDANCE below"; fi). Map:
- Package/module structure and layer boundaries
- Design patterns in use (MVC, hexagonal, microservices, etc.)
- How components communicate (imports, events, APIs, queues)
- Entry points (main files, handler registrations, route definitions)
- Configuration management (how config reaches code)

FOUNDATION HEALTH — for each major component you survey, assess:
- Does the logic actually work? Read function bodies, not just signatures
- Are there stub functions (correct signature, empty/hardcoded body)?
- Are there handlers that return success but don't do real work?
- Are there broken patterns (e.g., middleware registered but never applied)?
Flag anything that looks structurally complete but is logically hollow.
Write a "## Foundation Issues" section at the end with anything you found.
$SCOPE_GUIDANCE

Write your findings as structured markdown to: $SURVEY_DIR/architecture.md

Format: Use headers for each area. Include specific file paths. Note patterns with examples.
\`\`\`

### Agent 2: DATA
\`\`\`
Explore data models, storage, and data flow in this codebase$(if [[ -n "$SCOPE_GUIDANCE" ]]; then echo " relevant to the feature described in the SCOPE GUIDANCE below"; fi). Map:
- Database models/schemas (ORMs, migrations, raw SQL)
- Data structures and types (structs, interfaces, type definitions)
- Data access patterns (repositories, DAOs, direct queries)
- Data flow: input → validation → processing → storage → response
- External data sources (APIs, files, caches, queues)

FOUNDATION HEALTH — for each data path you trace, assess:
- Does the data actually flow end-to-end? (input → DB → response)
- Are there models defined but never written to or read from?
- Are there repo methods that exist but are never called by services?
- Does validation actually run, or is it defined but bypassed?
- Are there fields in the schema that nothing populates?
Flag broken data flows. Write a "## Foundation Issues" section at the end.
$SCOPE_GUIDANCE

Write your findings as structured markdown to: $SURVEY_DIR/data.md

Format: Use headers for each area. Include specific file paths and type names.
\`\`\`

### Agent 3: SURFACE
\`\`\`
Explore the public surface area of this codebase$(if [[ -n "$SCOPE_GUIDANCE" ]]; then echo " relevant to the feature described in the SCOPE GUIDANCE below"; fi). Map:
- API endpoints/routes (HTTP methods, paths, handlers)
- UI components/pages (if frontend exists)
- CLI commands/flags (if CLI exists)
- Exported functions and public interfaces
- Extension points (where new features plug in)
- Authentication/authorization patterns

FOUNDATION HEALTH — for each endpoint/page/component you survey, assess:
- Does the handler do real work, or return hardcoded/empty responses?
- Do UI components actually call APIs and render data, or are they shells?
- Are there registered routes whose handlers are stubs or TODOs?
- Do forms submit data that the backend actually processes?
- Are there components imported but never rendered in any page?
Flag anything that presents a working surface but has hollow logic underneath.
Write a "## Foundation Issues" section at the end.
$SCOPE_GUIDANCE

Write your findings as structured markdown to: $SURVEY_DIR/surface.md

Format: Use headers for each area. Include specific file paths and function names.
\`\`\`

### Agent 4: INFRA
\`\`\`
Explore the infrastructure, testing, and tooling in this codebase$(if [[ -n "$SCOPE_GUIDANCE" ]]; then echo " relevant to the feature described in the SCOPE GUIDANCE below"; fi). Map:
- Test patterns (unit, integration, e2e — frameworks, fixtures, helpers)
- CI/CD configuration (pipelines, quality gates)
- Build system and dependencies (package manager, build tools)
- Environment configuration (env vars, config files, secrets management)
- Linting/formatting tools and conventions
- Deployment patterns (Docker, K8s, serverless, etc.)
$SCOPE_GUIDANCE

Write your findings as structured markdown to: $SURVEY_DIR/infra.md

Format: Use headers for each area. Include specific file paths and tool names.
\`\`\`

**After all 4 agents complete**, read all survey files (architecture.md, data.md, surface.md, infra.md) and proceed to PHASE R1.

SURVEY_PROMPT_EOF

else
  # No survey mode - skip directly to interview
cat > "$PROMPT_FILE" << 'NOSURVEY_PROMPT_EOF'
# Forge Specification Engine

You are conducting a specification interview. The --no-survey flag was set, so codebase research is skipped (greenfield or empty project).

Proceed directly to PHASE R2 (INTERVIEW) below.

NOSURVEY_PROMPT_EOF
fi

# =========================================================================
# PHASE R1: SYNTHESIZE — Merge research into reality document
# =========================================================================

if [[ "$NO_SURVEY" == "false" ]]; then

# Build context block for R1 synthesis
SYNTH_CONTEXT=""
if [[ -n "$CONTEXT_CONTENT" ]]; then
  SYNTH_CONTEXT="
### Feature Context

The user provided a context file describing what they want to build. Use this to PRIORITIZE
your synthesis — highlight the parts of the codebase most relevant to this feature and
deprioritize areas that don't apply.

--- FEATURE CONTEXT ---
$(echo "$CONTEXT_CONTENT" | head -300)
--- END FEATURE CONTEXT ---
"
fi

cat >> "$PROMPT_FILE" << SYNTH_PROMPT_EOF

## PHASE R1: SYNTHESIZE — Build Codebase Reality Document

Read ALL 4 survey files (architecture.md, data.md, surface.md, infra.md) and synthesize them into a single **reality document**. Write this to the reality path specified in SESSION INFORMATION below. The reality document captures inside-in codebase knowledge. Ecosystem orientation is added later in R1.5 RESEARCH.
$SYNTH_CONTEXT
**Key instruction:** Do NOT just concatenate the survey files. Synthesize them — cross-reference findings, resolve contradictions, and prioritize information relevant to the feature being built ("$FEATURE_NAME").

Structure the reality document as:

\`\`\`markdown
# Codebase Reality: $FEATURE_NAME

## Architecture Summary
- How the app is organized (layers, packages, communication patterns)
- Key design patterns with specific examples

## Data Landscape
- Existing models and their relationships
- Data access patterns (with specific file/function references)
- Storage technologies in use

## Public Surface
- Existing endpoints/pages/commands relevant to this feature
- Extension points where new code plugs in
- Auth/authz model (if relevant)

## Conventions to Follow
- Naming patterns (with examples from codebase)
- Error handling style (with examples)
- Test patterns (with examples from test files)
- Logging approach

## Integration Points
- Where new feature code should live (specific packages/directories)
- What existing code to extend vs. create new
- Dependencies to be aware of

## Foundation Health
- Issues found by survey agents in their "Foundation Issues" sections
- Existing code that is logically broken (stubs, hardcoded returns, hollow handlers)
- Data flows that don't complete (form submits but backend ignores fields)
- Components that look complete but don't do real work
- Code the new feature will BUILD ON that needs fixing first
- **For each issue: what it is, where it is (file:line), and whether the new feature depends on it**

If foundation issues exist that the new feature depends on, these MUST become
requirements in the spec (fix X before building Y). Do not assume existing code works.

## Risks & Constraints
- Tight coupling areas
- Missing test coverage
- Tech debt that affects this feature area
- Performance considerations
\`\`\`

This document is the foundation for every interview question. Every question you ask in R2 should reference specific findings from this document. If foundation health issues were found, ask the user whether to fix them as part of this feature or treat them as separate work.

**After writing the reality document, proceed to PHASE R1.5 (RESEARCH).**

SYNTH_PROMPT_EOF
fi

# =========================================================================
# PHASE R1.5: RESEARCH — Targeted online research to kill stale assumptions
# =========================================================================

if [[ "$NO_SURVEY" != "true" ]]; then
  cat >> "$PROMPT_FILE" << RESEARCH_PROMPT_EOF

## PHASE R1.5: RESEARCH — Targeted Online Research

Before interviewing, verify your technical assumptions against the current ecosystem AND gather ecosystem orientation for the feature category. Your training data is 6-18 months stale — library versions, API surfaces, and deprecated patterns may have shifted. Ecosystem orientation (common shapes, known failure modes, gotchas) helps the R2 interviewer ask domain-aware questions instead of treating every feature as novel.

### Purpose

R1.5 covers two complementary jobs in one pass:
- **(a) Stale-knowledge invalidation** — verify specific technical claims in reality.md (library versions, API surfaces, deprecated patterns).
- **(b) Ecosystem orientation** — for the feature category, surface the common shape, 3-5 gotchas, and questions the user probably hasn't thought about.

Examples:
- Reality.md says "codebase uses htmx 1.9" → research: is 1.9 still current? Any breaking changes in 2.x relevant to this feature? What gotchas do people hit with htmx SSE?
- Reality.md says "uses client-go v0.29 for Kubernetes API" → research: current stable version? Deprecated APIs since 0.29? What shape does a listing page for Deployments typically take?
- Feature requires a library not yet in the codebase → research: what's the current recommended option, and what are the pitfalls?

**R1.5 always runs** when survey ran (i.e., when --no-survey was NOT passed). Even if reality.md has no verifiable library claims, spawn at least 1 researcher covering ecosystem orientation for the feature category — the R2 interviewer needs outside-in context. The only skip path is the --no-survey branch below.

### Procedure

1. Read reality.md in full
2. Identify 1-4 research domains:
   - **At minimum**: 1 ecosystem-orientation domain for the feature category (e.g., "workloads-listing-page-patterns", "file-upload-ux-patterns")
   - **If claims exist**: up to 3 additional stale-knowledge domains (library versions, API surfaces, deprecated patterns) — total capped at 4
3. For each domain, spawn a \`researcher\` agent via the Agent tool. Use \`subagent_type: "Agent"\` and pass the full content of \`\${CLAUDE_PLUGIN_ROOT}/agents/researcher.md\` as the prompt. Include in the prompt:
   - The specific claim to verify OR the "ecosystem orientation for <feature category>" framing
   - The output path: \`$SURVEY_DIR/research-{domain-slug}.md\`
   - Any locked decisions from user context (if passed)
4. Spawn ALL researchers in a SINGLE message for parallelism
5. Wait for all researchers to return
6. Append a \`## Research Findings\` section to reality.md:
   \`\`\`markdown
   ## Research Findings

   ### {domain-slug-1}
   **Confidence:** HIGH / MEDIUM / LOW
   **Verdict:** [one-line summary of what the researcher found]
   **Actionable:** [what to tell the interviewer or flag in the spec]

   ### {domain-slug-2}
   ...
   \`\`\`

### Rules

- **Never** research generic topics ("what is htmx?"). Always verify a specific claim OR orient against a specific feature category.
- **Never** spawn more than 4 researchers per session. If there are more than 4 candidate domains, pick the highest-risk ones + keep the ecosystem-orientation slot.
- **Never** skip R1.5 when survey ran. The minimum is 1 ecosystem-orientation researcher. Only --no-survey skips R1.5.
- **Carry confidence forward.** If a researcher returns LOW confidence, the interviewer must ask the user to decide rather than assuming.
- **Conflicts go to the user.** If research contradicts something the user said in the initial prompt, surface it in R2 INTERVIEW as an explicit question: "You mentioned X but research shows Y — which way?"

**After writing research findings to reality.md, proceed to PHASE R2.**

RESEARCH_PROMPT_EOF
else
  cat >> "$PROMPT_FILE" << 'RESEARCH_SKIP_EOF'

## PHASE R1.5: RESEARCH — Skipped (--no-survey mode)

R1.5 is skipped because --no-survey was passed. No codebase reality doc = no targeted research possible. Proceed directly to PHASE R2.

RESEARCH_SKIP_EOF
fi

# =========================================================================
# PHASE R1.75: IMPLICIT-FACT EXTRACTION — gap-list scout-then-ask
# =========================================================================

if [[ "$NO_SURVEY" == "false" ]]; then
  cat >> "$PROMPT_FILE" << 'IMPLICIT_FACT_PROMPT_EOF'

## PHASE R1.75: IMPLICIT-FACT EXTRACTION — Scout-then-Ask Gap-List

After R1.5 RESEARCH appends `## Research Findings` to reality.md, walk the
closed vocabulary and emit a gap-list BEFORE opening R2 free-form interview.

### Closed Vocabulary

Six core categories + OTHER escape hatch:
  - DEPLOYMENT — deployment target (AWS/GCP/Azure/on-prem/k8s/serverless/edge)
  - SCALE — request rate, data size, user count, throughput
  - RUNTIME — language + version (Go 1.21, Node 20, Python 3.12)
  - FRAMEWORK_VERSION — major dependency versions (htmx 1.9, client-go 0.29)
  - SECURITY — compliance regime (SOC2, HIPAA, public/internal/regulated)
  - NETWORK — connectivity model (online, offline-first, air-gapped, multi-region)
  - OTHER — escape hatch; name actual category in entry body

### Procedure (scout-then-ask, mirroring GSD discuss-phase)

1. Read reality.md in full (architecture, data, surface, infra summaries +
   research findings).
2. For each of the six categories, scan reality.md and survey/*.md for
   keywords/findings that determine the value:
   - DEPLOYMENT: docker-compose.yml, k8s manifests, terraform/, .github/workflows
   - SCALE: any explicit perf metric, RPS reference, table-size mention
   - RUNTIME: go.mod / package.json / pyproject.toml version
   - FRAMEWORK_VERSION: research findings naming specific library versions
   - SECURITY: auth middleware, encryption setup, compliance docs
   - NETWORK: CDN refs, edge deployment, offline-storage patterns
3. For each AUTO-DISCOVERED category, emit a synthetic transcript entry:
   ```
   ## A-AUTO-NNN [IMPLICIT_FACT:CATEGORY] (auto-discovered short label)
   <verbatim or near-verbatim restatement> [from <source-file>]
   ```
4. For each GAP (category not auto-discoverable), formulate a domain-specific,
   code-context-annotated AskUserQuestion. Phrasing rules:
   - Cite the specific file/finding that prompted the question
   - Closed-form options where possible (don't ask "describe X" — ask
     "we see X in <file> — confirm or upgrade?")
   - Spec-domain-rephrased ("for adding a workloads page, ask about k8s
     API version specifically, not generic 'deployment target'")
5. Ask the gap-fill questions via AskUserQuestion, ONE per gap, sequentially.
   Append each Q+A verbatim to transcript.md immediately, tagging the A-NNN
   with the relevant [IMPLICIT_FACT:CATEGORY].
6. If user answers "none/N-A/declines", STILL append a tagged A-NNN with
   body "User declined to specify [IMPLICIT_FACT:CATEGORY]" — explicit
   declines are load-bearing for INTENT-01.

### Validator Contract

`validate-spec.py` enforces at SPEC FORGED:
  - Every [IMPLICIT_FACT:X] uses a known category
  - Every A-AUTO-NNN entry has a tag AND a [from <source>] citation
  - Transcript with any A-NNN must contain ≥1 IMPLICIT_FACT-tagged entry
    (auto-discovered or user-answered) — otherwise R1.75 didn't run

After emitting auto-facts and asking gap-fills, proceed to PHASE R2 INTERVIEW.

IMPLICIT_FACT_PROMPT_EOF

else
  cat >> "$PROMPT_FILE" << 'IMPLICIT_FACT_NOSURVEY_PROMPT_EOF'

## PHASE R1.75: IMPLICIT-FACT EXTRACTION — Full Closed-Vocab Batch (--no-survey fallback)

R0/R1/R1.5 were skipped (--no-survey), so there is no reality.md to scout.
Without a codebase reality doc, every closed-vocabulary category is a gap —
the gap-list IS the full closed vocabulary. Ask the full closed vocabulary
as one batched AskUserQuestion at the start of R2.

### Procedure

1. Compose a single AskUserQuestion with six sub-questions (one per category):
   DEPLOYMENT, SCALE, RUNTIME, FRAMEWORK_VERSION, SECURITY, NETWORK.
2. Phrase each as a brief plain-language prompt. The user can answer "skip"
   or "N-A" for any.
3. After the user responds, append SIX A-NNN entries to transcript.md, each
   tagged with the relevant [IMPLICIT_FACT:CATEGORY]. Bodies are the user's
   verbatim answer (or "User declined to specify [IMPLICIT_FACT:CATEGORY]"
   for skipped categories).
4. Then proceed to PHASE R2 INTERVIEW with normal free-form questions.

### Validator Contract (same as scout-then-ask path)

Same enforcement — at least one [IMPLICIT_FACT:CATEGORY] tag must appear in
the transcript before SPEC FORGED.

IMPLICIT_FACT_NOSURVEY_PROMPT_EOF
fi

# =========================================================================
# PHASE R2: INTERVIEW — Codebase-grounded adaptive interview
# =========================================================================

cat >> "$PROMPT_FILE" << 'INTERVIEW_PROMPT_EOF'

## PHASE R2: INTERVIEW — Codebase-Grounded Adaptive Interview

You are now conducting a comprehensive specification interview. Multi-round, adaptive, progressive — and every question is grounded in your codebase research.

### SPEC TYPE CLASSIFICATION (mandatory first step)

Before diving into requirements, classify this feature into one of four types. This classification drives downstream Foundry behavior:

| Type | Meaning |
|---|---|
| `GREENFIELD` | Building something new that doesn't exist yet |
| `MIGRATION` | Converting/porting/replacing existing artifacts into a new form (tests, protocols, libraries, ports) |
| `BUG_FIX` | Fixing specific broken behavior |
| `REFACTOR` | Restructuring code without changing external behavior |

**Detection triggers** in the user's prompt or early answers:
- MIGRATION: "convert", "migrate", "port", "replace existing X with", "rewrite Z in Y", "move from A to B"
- BUG_FIX: "fix", "broken", "race", "regression", "leak", audit finding refs (C-N, H-N)
- REFACTOR: "extract", "split", "consolidate", "restructure", "clean up"
- GREENFIELD: default

Use AskUserQuestion early in the interview to confirm the type if ambiguous.

Write the classification to `state.md` as `spec_type: MIGRATION` (or whichever type). **This field is mandatory** in the final spec.json output.

### IF spec_type IS MIGRATION — additional mandatory duties

If the feature is a MIGRATION, you MUST enumerate the full source inventory before finalizing the spec. Wiggle-word language like "equivalent coverage" or "same as legacy" is NOT a complete spec — refuse to proceed to R3 until the enumeration is done.

**Procedure:**

1. **Generate a candidate source inventory via grep.** Example for test migrations:
   ```bash
   grep -rHn "^func Test" legacy/tests/ | sed 's/:.*func \(Test[A-Za-z0-9_]*\).*/:\1/' | sort -u
   ```
   For library ports, grep exported symbols. For protocol migrations, grep endpoint handlers.

2. **Present the candidate list to the user** via AskUserQuestion. Ask: "I found N source items that would need to be ported. Are any of these out-of-scope? Should any be consolidated?" Let the user prune/confirm.

3. **Ask about the destination naming rule.** How does source map to destination? Common patterns:
   - Suffix `_v2` on the same filename, same symbol names
   - New directory with identical structure
   - New file, renamed symbols
   - Consolidated into a single file
   Write this as `destination_naming_rule` in the spec.

4. **Write the inventory to state.md** under a `## source_inventory` section:
   ```
   ## source_inventory
   - legacy/tests/auth_test.go:TestLogin
   - legacy/tests/auth_test.go:TestLogout
   - ...
   ## destination_naming_rule
   Suffix `_v2` on filename: `auth_test.go` -> `auth_v2_test.go`, same symbol names.
   ```

5. **The inventory becomes Foundry's source of truth.** F0.5 DECOMPOSE will read it and assign every entry to exactly one casting's `coverage_list`. F2 INSPECT will grep for each destination. Any miss is a defect. The user cannot silently lose coverage.

6. **Never accept "equivalence will be validated manually later" as a complete spec.** If the user says this, push back: "Equivalence can only be validated if it already exists. The point of Foundry is to make equivalence checkable. Help me enumerate the specific source items now, then the teammate ports them 1:1, then we verify 1:1 automatically."

### CRITICAL RULES

### CRITICAL RULES

#### 1. USE AskUserQuestion FOR ALL QUESTIONS
You MUST use the AskUserQuestion tool for every question you ask. Plain text questions will NOT work — the user cannot respond to them. Every question must go through AskUserQuestion with 2-4 concrete options.

#### 2. GROUND EVERY QUESTION IN CODEBASE REALITY
If you performed the survey, reference specific findings:
- BAD: "How should authentication work?"
- GOOD: "I see your auth middleware in `middleware/auth.go` uses JWT with RBAC. Should the new feature extend this existing RBAC model, or does it need a separate permission system?"
- BAD: "What's the data model?"
- GOOD: "Your User struct in `models/user.go` has 8 fields. The new feature needs permissions — should we add a `permissions []string` field to User, or create a separate Permission model with a foreign key?"
- BAD: "How should errors be handled?"
- GOOD: "I see you wrap errors with `fmt.Errorf('...: %w', err)` in `services/` and use sentinel errors in `pkg/errors/`. Should the new feature follow this same pattern?"

#### 3. ASK NON-OBVIOUS QUESTIONS
DO NOT ask basic questions the codebase already answers. Probe decisions, trade-offs, and intent:
- "I found 3 similar features using pattern X — should this one follow suit or is there a reason to diverge?"
- "Your test coverage for auth is integration-heavy but the API layer is mostly unit tests — which approach for the new feature?"
- "The existing API uses REST but I see a GraphQL schema file — is this feature REST or are you migrating?"

#### 4. BE DELIBERATE, NOT FAST
This is NOT a speed run. You are building a comprehensive specification that will drive weeks of
implementation. Take time to:
- Explore each domain thoroughly before moving to the next
- Ask follow-up questions when answers are vague ("what specifically happens when X?")
- Circle back to earlier topics when new information changes the picture
- Validate your understanding by restating what you heard before moving on

Do NOT try to cover everything in 3-5 questions. A good forge interview is 10-20+ questions across
multiple rounds. The spec quality directly correlates with interview depth.

#### 5. CONTINUE UNTIL USER SAYS STOP
The interview continues until the user explicitly says "done", "finalize", "finished", or similar. Do NOT stop after one round. After each answer, immediately ask the next question using AskUserQuestion.

#### 6. MAINTAIN RUNNING DRAFT
After every 2-3 questions, update the draft spec file with accumulated information using the Write tool. This ensures nothing is lost if the session is interrupted.

#### 7. BE ADAPTIVE
Base your next question on previous answers. If the user mentions something interesting, probe deeper. Do not follow a rigid script. Build on what you learn.

#### 8. VERBATIM TRANSCRIPT — THIS IS THE SOURCE OF TRUTH

**Every question you ask and every answer the user gives MUST be appended verbatim to `transcript.md` with stable IDs. This is non-negotiable. The transcript IS the spec — the structured spec.md is an index over it.**

**Procedure for every Q/A exchange:**

1. Before calling AskUserQuestion, decide the next Q-ID (Q-001, Q-002, ...). IDs are monotonic across the whole interview.
2. Call AskUserQuestion.
3. When the user answers, immediately Write/Edit `transcript.md` to append:
   ```
   ## Q-NNN
   **Question:** {exact text of the question you asked, verbatim, including any options you presented}
   **Options presented:** {the options array you passed to AskUserQuestion, one per line}

   ## A-NNN
   {the user's answer VERBATIM — if they selected an option, paste the full option text; if they typed freeform, paste their exact words without rewording, rephrasing, or "cleaning up"}
   ```
4. Only then ask the next question.

**ID FORMAT — STRICT:**

- **One A-NNN per block. Always.** Never collapse multiple answers under a batched heading like `## A-005..A-008` or `## A-005, A-006`. The validator's parser cannot disambiguate which body belongs to which ID, so batched headings are silently dropped from the answer index — every spec citation pointing at a batched ID becomes a `DANGLING_CITATION` failure. If the user gave four related answers in one turn, write four separate `## A-NNN` blocks, each with its own body (which can repeat the user's words if the answers are genuinely identical).
- **Optional tag**: `## A-NNN [TAG, TAG]` — used for `[ARCH_INVARIANT]` and any future markers. Tags go in square brackets immediately after the ID.
- **Optional label**: `## A-NNN (short descriptor)` — a parenthetical hint at what the answer is about, useful for navigation in long interviews. Example: `## A-016 (readiness path)`. Labels are descriptive only; the parser ignores them for matching.
- **Combined**: `## A-NNN [TAG] (label)` is fine in that order.

**What NOT to do:**
- **NEVER paraphrase the user's answer** in the transcript. If they said "operator stays generic, agent handles per-node stuff like IDM does," the transcript contains those exact words. Not "user wants operator to remain generic" — their literal sentence.
- **NEVER summarize.** Not "user confirmed X." The literal utterance.
- **NEVER omit freeform answers** because they seem obvious or redundant.
- **NEVER batch transcript writes.** Write after every answer, not every 5 answers. If the session dies mid-interview, the transcript must be accurate up to the last question answered.

**Why:** Downstream, Foundry CAST teammates will read the spec and implement from it. If the spec paraphrases the user's words, the teammate is implementing from Claude's gloss of what the user said — two layers of drift before a single line of code is written. Verbatim capture + citation in the spec keeps the teammates grounded in the user's literal intent. The architectural-placement failures we've seen (code in the wrong layer because "Informational context" lost the user's exact constraint) trace directly to paraphrased interviews.

#### 9. ARCHITECTURAL PLACEMENT DETECTION

As the user answers, watch for **architectural placement language** — statements about *where code should live*, *which layer owns what*, or *what some component should NOT know about*:

- "The operator stays generic — per-node logic happens in the agent"
- "X should never know about Y"
- "This has to go through {module}, not {other module}"
- "Reuse the existing {seam/RPC/interface} — don't add a parallel one"
- "{Component A} owns {concern}; {Component B} just calls it"
- "Treat X as a library — it can't depend on Y"

When you detect any of these, tag the relevant A-NNN in the transcript with a marker:

```
## A-NNN  [ARCH_INVARIANT]
{verbatim user answer}
```

These tagged answers become the `## Global Invariants` section of the final spec (see R3). They are the highest-priority constraints — they determine not just what code does but where it lives. Missing these at interview time means Foundry teammates will put code in the wrong architectural layer and require a full revert cycle to fix.

If the user's initial prompt already contains placement language, you MUST either confirm it with an explicit question (AskUserQuestion: "You said '{quote}' — is this a hard architectural constraint, or are you open to alternatives?") or carry it into the transcript as a Q-000/A-000 bootstrap entry quoting the prompt verbatim.

#### 10. IMPLICIT-FACT TAGGING — environmental constraints become first-class transcript entries

Environmental facts the user assumes but never states (deployment target, scale,
runtime version, framework versions, security regime, network model) MUST appear
as A-NNN or A-AUTO-NNN transcript entries with `[IMPLICIT_FACT:CATEGORY]` tags.
Without this, INTENT-01 (Foundry Phase 8) is structurally blind to constraints
the user takes for granted.

The R1.75 sub-step (run before R2 free-form interview opens) walks the closed
vocabulary and emits the gap-list. By the time R2 opens, every implicit fact
is either:
  - **A-AUTO-NNN entry** — auto-discovered from reality.md, tagged with
    [IMPLICIT_FACT:CATEGORY] and a [from <source>] citation, OR
  - **A-NNN entry** — user-answered gap-fill question, tagged with
    [IMPLICIT_FACT:CATEGORY] in the heading.

If during R2 the user mentions a NEW environmental fact that wasn't in the
gap-list (e.g., user volunteers "we run this on edge POPs in 14 regions"),
tag the relevant A-NNN with [IMPLICIT_FACT:NETWORK] or appropriate category
in the transcript heading immediately after AskUserQuestion returns.

**Format (strict — validator enforces):**
  - `[IMPLICIT_FACT:CATEGORY]` with no whitespace inside brackets
  - Category MUST be one of: DEPLOYMENT, SCALE, RUNTIME, FRAMEWORK_VERSION,
    SECURITY, NETWORK, OTHER
  - Use OTHER as escape hatch; name the actual category in the entry body
    (e.g., "data residency: EU-only")
  - Multiple tags allowed: `## A-008 [ARCH_INVARIANT, IMPLICIT_FACT:SECURITY]`

### DOMAIN DETECTION

Analyze the feature request and classify which domains apply. This determines your question focus:

| Domain | Signals | Question Focus |
|--------|---------|----------------|
| Auth | login, permission, role, token, session | Token strategy, RBAC model, session management |
| API | endpoint, route, handler, REST, GraphQL | HTTP methods, payloads, validation, versioning |
| Data | model, schema, database, query, migration | Schema design, access patterns, indexes, caching |
| Frontend | page, component, form, UI, UX | Component hierarchy, state management, responsive |
| Infra | deploy, CI, Docker, K8s, config | Deployment strategy, environment config, scaling |
| Security | encrypt, PII, compliance, audit | Threat model, data classification, audit logging |
| Testing | test, coverage, fixture, mock | Test strategy, coverage requirements, test data |
| Integration | webhook, event, queue, external API | Contract design, retry logic, circuit breakers |
| Performance | latency, throughput, cache, optimize | Budgets, caching strategy, profiling approach |

Apply 1-3 primary domains. Ask domain-specific questions for each.

### RED FLAG DETECTION

Watch for these red flags in the user's description and probe deeper:

| Red Flag | Risk | Mandatory Follow-up |
|----------|------|---------------------|
| "simple" or "just" | Scope underestimation | "What makes this simpler than [similar feature in codebase]?" |
| No error handling mentioned | Incomplete thinking | "What happens when [operation] fails?" |
| "like X but for Y" | Hidden complexity in delta | "What specifically differs from X?" |
| "secure" without specifics | Security theater | "What threat model? What data classification?" |
| "ASAP" or "quick" | Shortcut pressure | "What can we defer to Phase 2 vs. must-have in Phase 1?" |
| Vague acceptance criteria | Unverifiable requirements | "How would you TEST that this works?" |
| No mention of existing code | Greenfield assumption on brownfield | "How does this interact with [existing feature I found]?" |

### QUESTION PROGRESSION

**Round 1 — Universal (3-5 questions)**
Grounded versions of these core questions:
- What does "done" look like? (verifiable acceptance criteria)
- What is explicitly OUT of scope?
- What related code already exists? (Reference what you found in survey)
- What happens when things go wrong?
- Who/what depends on this?

**Round 2+ — Domain-Specific (adaptive)**
Based on detected domains, ask targeted questions. Examples:

**Auth domain:**
- "I see [current auth pattern]. Should new feature extend it or diverge?"
- "Token lifetime? Refresh strategy? What happens on expiry?"
- "Role hierarchy — flat list or nested permissions?"

**API domain:**
- "I see your handlers follow [pattern from surface.md]. Same pattern?"
- "Pagination strategy? Cursor-based like [existing endpoint] or offset?"
- "Rate limiting? Your current setup uses [X] — same for new endpoints?"

**Data domain:**
- "I found [existing models]. New feature adds [what] to the data model?"
- "Migration strategy — additive only or breaking changes?"
- "Caching layer? I see [cache pattern] in [file] — reuse it?"

**Frontend domain:**
- "Component library — I see [existing components]. Extend or new?"
- "State management — you use [X pattern]. Same for new feature?"
- "Responsive requirements? I see [current breakpoint strategy]."

**Round 3+ — Cross-Cutting Concerns (2-3 questions)**
Pick the most relevant:
- Blast radius: "What could break if this feature fails?"
- Rollback: "How do we undo this if it goes wrong?"
- Monitoring: "What metrics/alerts should we add?"
- Performance: "Latency/throughput budgets?"
- Documentation: "API docs, user docs, or internal docs needed?"

### INTERVIEW WORKFLOW

1. Read the reality document (if survey was performed)
2. Read any provided context
3. Detect domains and red flags from the feature name + context
4. Ask first grounded question using AskUserQuestion
5. After user responds, update draft if enough for a section
6. Ask next question immediately using AskUserQuestion
7. Repeat until user says "done" or "finalize"
8. When done, proceed to PHASE R3

INTERVIEW_PROMPT_EOF

# =========================================================================
# First Principles mode (optional, inserted before interview)
# =========================================================================

if [[ "$FIRST_PRINCIPLES" == "true" ]]; then
  cat >> "$PROMPT_FILE" << 'FP_EOF'

### FIRST PRINCIPLES MODE — ACTIVE

Before detailed spec gathering, challenge the user's assumptions (3-5 questions):

1. "What specific problem led to this idea?"
2. "What happens if we don't build this? Cost of inaction?"
3. "What's the simplest thing that might solve this?"
4. "What would make this the WRONG approach?"
5. "Is there an existing solution (internal, external, off-the-shelf)?"

If the approach seems valid, say: "The approach is sound. Let's move to detailed specification."
If flawed, help discover a better alternative before proceeding.

FP_EOF
fi

# =========================================================================
# Context injection (if provided)
# =========================================================================

if [[ -n "$CONTEXT_CONTENT" ]]; then
  cat >> "$PROMPT_FILE" << CONTEXT_EOF

## PROVIDED CONTEXT

\`\`\`
$CONTEXT_CONTENT
\`\`\`
CONTEXT_EOF
fi

# Detect prior research output in context (e.g., from GSD or another planning tool)
if [[ -n "$CONTEXT_CONTENT" ]] && echo "$CONTEXT_CONTENT" | grep -q "^## .* Research Context"; then
  cat >> "$PROMPT_FILE" << 'PRIOR_RESEARCH_EOF'

## PRIOR RESEARCH DETECTED — INTERVIEW ADAPTATION

The provided context already contains research output (from GSD or another planning
tool). Your survey agents may find overlapping information. Adapt:

### SKIP (prior research already covered):
- Generic tech stack questions
- Generic architecture questions
- Generic feature discovery

### PROBE INSTEAD (prior research is usually broad but shallow):
- **Acceptance criteria**: "research identified [feature] — what proves it works?"
- **Edge cases**: "what happens when [feature] hits [failure mode]?"
- **Constraints**: "research recommends [tech] — version/deployment constraints?"
- **Pitfall handling**: "research flagged [pitfall] — user-facing behavior?"
- **Verification**: "what command proves [requirement] works?"

### MERGE with survey findings:
Cross-reference prior research with what your survey agents found. Where they agree,
skip. Where they conflict, ask the user. Where the prior research has gaps, your
survey fills them.

PRIOR_RESEARCH_EOF
fi

# =========================================================================
# PHASE R3: SPEC — Generate foundry-ready specification
# =========================================================================

cat >> "$PROMPT_FILE" << 'SPEC_PROMPT_EOF'

## PHASE R3: SPEC — Generate Foundry-Ready Specification

When the user says "done", "finalize", "finished", or similar, generate the specification.

### SPEC FORMAT (Foundry-Compatible)

The spec MUST use these ID schemes for foundry traceability:
- **US-NNN**: User Stories
- **FR-NNN**: Functional Requirements (lower-level than US)
- **NFR-NNN**: Non-Functional Requirements
- **AC-NNN**: Acceptance Criteria (nested under US/FR)
- **OT-NNN**: Observable Truths (per domain, min 5 each — foundry verification targets)

### SPEC PHILOSOPHY

You know this codebase. The spec should reflect that. Do NOT generate a generic spec that could
apply to any project. Every section should reference specific files, functions, patterns, and
conventions discovered during the survey. A developer reading this spec should be able to start
coding immediately without exploring the codebase themselves.

Take your time. A thorough spec prevents weeks of back-and-forth during implementation.

### SPEC TEMPLATE

```markdown
---
spec_format_version: v2.1
---

# Specification: {FEATURE_NAME}

> Generated by Forge | Survey: {N} agents | Interview: {N} rounds
> Date: {TIMESTAMP}

## Problem Statement

> **Every sentence in this section MUST end with a `[from A-NNN]` or `[derived from A-NNN, A-NNN]` citation.** No unsourced prose.

[1-3 sentences synthesized from interview, each followed by its transcript citation.] [derived from A-NNN, A-NNN]

## Global Invariants

> **Cross-cutting rules that apply to every casting.** Each row is a direct quote from a transcript answer tagged `[ARCH_INVARIANT]` during R2, with a Locked-only `[from A-NNN]` citation. No paraphrase. Foundry decompose copies this section verbatim into every casting's `<global_invariants>` block; PROVE and TRACE enforce architectural placement against it.

Each row in this table is an architectural-placement or cross-cutting invariant the user explicitly stated during the interview. Every row cites the `[ARCH_INVARIANT]`-tagged A-NNN it came from. The `applies-to` column tells downstream agents (PROBE-01, INTENT-01) which files/layers/components the invariant constrains; the `violation` column gives a concrete example of the prohibited shape so PROVE/TRACE can grep for it.

| ID     | statement                                                          | applies-to                | violation                                            | citation     |
|--------|--------------------------------------------------------------------|---------------------------|------------------------------------------------------|--------------|
| GI-001 | "exact quoted user text describing a placement rule"               | {which files/layers}      | {concrete example of what NOT to do}                 | [from A-NNN] |
| GI-002 | "next invariant — verbatim quote of the user's words"              | {which files/layers}      | {violation example}                                  | [from A-NNN] |

**Sentinel example** (use this exact shape if the user gave no `[ARCH_INVARIANT]`-tagged answers in R2 — exactly one row, replacing `A-003` with the framing answer that justified the conclusion):

| —      | None — the user gave no explicit placement constraints.            | —                         | —                                                    | [from A-003 reasoning — user described feature as a stateless CSS color change] |

*(Do NOT leave the table empty; do NOT skip the table; do NOT invent invariants. The sentinel row is the explicit-acknowledgement signal — PROBE-01 R3.5 reviewer can cite it back at the user if the transcript actually does suggest a placement rule.)*

---

## State Transitions

> **State-machine rules the user described during the interview.** Every row cites a Locked-only `[from A-NNN]` transcript answer whose body contained the transition language. Foundry decompose propagates this section verbatim into every casting's `<state_transitions>` block; downstream Phase 7 TEST-01 derives negative-assertion targets from the `guard` column.

Each row in this table is a state transition the user described ("when X happens, Y becomes Z" / "after step N, transition to step M"). `from-state` and `to-state` are state-name strings; `trigger` is the event/method/input that fires the transition; `guard` is the precondition that must hold for the transition to fire (TEST-01 will use guards as negative-assertion targets). Empty `from-state` indicates the initial state; empty `to-state` indicates a terminal state.

| ID     | from-state | to-state  | trigger                       | guard                  | citation     |
|--------|------------|-----------|-------------------------------|------------------------|--------------|
| ST-001 | RUNNING    | COMPLETED | casting reaches DONE          | ASSAY signs off        | [from A-NNN] |
| ST-002 |            | RUNNING   | F0.5 emits casting prompt     | F0.9 VALIDATE passes   | [from A-NNN] |

**Sentinel example** (use this exact shape if the transcript has no state-machine language — exactly one row, replacing `A-005` with the framing answer that justified the conclusion):

| —      | —          | —         | None — this feature has no state transitions | —      | [from A-005 reasoning — user described feature as a stateless CSS color change] |

*(Sentinel row is REQUIRED if there are no transitions — the heading without a table or with an empty table is rejected by validate-spec.py at SPEC FORGED. Do NOT generate plausible-sounding transitions from spec prose; the validator's content-difference rule (Jaccard ≥0.7) will reject paraphrased rows with `TYPED_ROW_PARAPHRASE`.)*

---

## Contracts

> **Observable contracts the user described during the interview.** Every row cites a Locked-only `[from A-NNN]` transcript answer whose body defined the surface. Foundry decompose propagates this section verbatim into every casting's `<contracts>` block; downstream Phase 7 TEST-01 derives `hypothesis-jsonschema` strategies from the `input`/`output` cells, and the `errors` column gives TEST-01 the negative-assertion mandate surface.

Each row in this table is an observable contract the user described — endpoint, function, handler, CLI command, or other observable surface. `input` and `output` describe types or shapes (e.g., `User → {token: string, expires_at: ISO8601}`); `errors` lists error codes / conditions / HTTP statuses.

Note: this is the **user-stated observable contract layer**. The implementation-stated technical design (data models, internal helpers, error envelope) lives one level lower in `## Technical Design`. The two layers may overlap in subject matter; the `## Contracts` table is what the user described, the `## Technical Design` block is what the implementation will build.

| ID     | surface                  | input                             | output                                            | errors                                       | citation     |
|--------|--------------------------|-----------------------------------|---------------------------------------------------|----------------------------------------------|--------------|
| CT-001 | POST /api/login          | {email: string, password: string} | {token: string, expires_at: ISO8601}              | 401 invalid_credentials, 429 rate_limited    | [from A-NNN] |
| CT-002 | Foundry-Accept-Casting   | casting_id (string)               | {accepted: bool, provenance: {sha256, mtime}}     | INVALID_CASTING_ID, EVIDENCE_MISMATCH        | [from A-NNN] |

**Sentinel example** (use this exact shape if the transcript has no observable contracts — exactly one row, replacing `A-012` with the framing answer that justified the conclusion):

| —      | None — no observable contracts beyond internal helper signatures | —                                 | —                                                 | —                                            | [from A-012 reasoning — user said this is a build-script edit] |

*(Sentinel row is REQUIRED if there are no contracts. Same hallucination ban as `## State Transitions` — the validator will reject paraphrased rows with `TYPED_ROW_PARAPHRASE`.)*

---

## Scope

> **Every bullet MUST carry a citation.** If you can't cite a transcript answer that supports a scope item, the item doesn't belong in the spec.

### In Scope
- [Scope item] [from A-NNN]
- [Scope item] [derived from A-NNN, A-NNN]

### Out of Scope
- [Exclusion] [from A-NNN — user explicitly said "not this"]
- [Exclusion] [derived from A-NNN — user's priorities implied this was deferred]

---

## User Stories

> **Every US and every AC carries citations.** The `As a / I want / so that` narrative is Claude's synthesis of the user's answers; the citation proves the synthesis is grounded. If no transcript answer supports a User Story, the story is hallucination — delete it and ask the user.

### US-001: [Story Title] [derived from A-NNN, A-NNN]
**As a** [user type], **I want** [action], **so that** [benefit].

**Source answers (not authoritative — navigate to the Appendix Transcript for full context):**
- A-NNN: "short quoted fragment showing the user's relevant words"
- A-NNN: "..."

**Acceptance Criteria:**
- **AC-001** [from A-NNN]: "verbatim user words if Locked" OR [derived from A-NNN]: Claude-worded testable criterion
- **AC-002** [from A-NNN]: "..." (error case — must be testable AND cited)
- **AC-003** [derived from A-NNN]: ... (edge case)

**Codebase Integration** (derived from survey, not from interview — cite the survey file):
- Extends: `services/auth.go:AuthService.CreateUser` (line ~45) — add permission check [from survey/architecture.md]
- Follows pattern: `handlers/users.go:HandleCreateUser` — validate → service → respond [from survey/surface.md]
- New files: `services/permissions.go` (in `services/` alongside existing service files) [derived from survey/architecture.md + A-NNN]
- Modifies: `models/user.go:User` struct — add `Permissions []string` field [derived from A-NNN]

### US-002: ... [derived from A-NNN]

---

## Functional Requirements

> **Locked requirements MUST quote the user verbatim with a transcript citation. Flexible requirements cite the source answer but may be Claude-worded. Informational items are background context only.**

### Locked (implement exactly as specified — direct quotes from transcript)

- **FR-001** [from A-NNN]: "exact quoted user text — the user's literal words, not a paraphrase"
  - Maps to: US-001
  - Claude's gloss (for reading convenience only, not authoritative): [optional short restatement]
- **FR-002** [from A-NNN]: "..."
  - Maps to: US-001, US-002

### Flexible (Claude's discretion on approach)

- **FR-NNN** [derived from A-NNN]: [Claude-worded requirement — user described the WHAT but not the HOW]
  - Maps to: US-NNN
  - Source answer: "relevant quoted fragment from A-NNN for traceability"

### Informational (context, not requirements)

- [Background fact from user] [from A-NNN]
- [Research finding from R1.5] [from reality.md Research Findings]

## Non-Functional Requirements

Same Locked/Flexible/Informational structure as Functional Requirements. Every Locked NFR must quote the user.

- **NFR-001** [from A-NNN]: "exact quoted measurable metric from user"

---

## Technical Design

> **Every design decision in this section must cite EITHER a transcript answer (if the user made the decision) OR a survey file (if the decision is inherited from codebase reality).** Claude's aesthetic preferences are not a valid source. If a decision has no transcript or survey backing, it does not go in the spec — it's Claude filling silence.

### Data Model Changes

**Current state** (from survey):
[Existing model/schema that will be modified, with file path] [from survey/data.md]

**Proposed changes:**
- [Change 1] [from A-NNN — user specified] OR [derived from A-NNN — user described the behavior and this is the minimal schema]
- [Change 2] [from A-NNN]
- Migration strategy: [additive | breaking | rolling] [from A-NNN]

### API Design

**New endpoints:**
| Method | Path | Handler | Request Body | Response | Auth | Source |
|--------|------|---------|-------------|----------|------|--------|
| POST   | /api/... | `handlers/...` | `{...}` | 201: `{...}` | Required | [from A-NNN] |

**Modified endpoints:**
- [Endpoint + what changes] [from A-NNN] [from survey/surface.md]

**Pattern to follow:**
- [Reference a specific existing endpoint as the template] [from survey/surface.md + A-NNN if user chose to match]

### Architecture

**Component diagram:**
[How new components fit into existing architecture — reference actual packages] [from survey/architecture.md, decisions from A-NNN]

**Dependency flow:**
[What depends on new code, what new code depends on — reference specific imports] [from survey/architecture.md + A-NNN]

### Error Handling

**Pattern to follow** (from survey):
[Reference the actual error handling pattern in the codebase] [from survey/architecture.md]

**Error cases for this feature:**
| Scenario | HTTP Status | Error Code | Message | Source |
|----------|-------------|------------|---------|--------|
| ... | 400 | VALIDATION_ERROR | "..." | [from A-NNN] |
| ... | 409 | CONFLICT | "..." | [from A-NNN] |

---

## File Change Map

Exactly which files are touched and what happens in each. **Every row cites the transcript answer or survey file that justifies the change.**

### Modified Files
| File | What Changes | Lines/Functions Affected | Source |
|------|-------------|------------------------|--------|
| `models/user.go` | Add Permissions field to User struct | `User` struct (~L15) | [from A-NNN — user said "users need roles"] [from survey/data.md] |
| `services/auth.go` | Add permission check to CreateUser | `CreateUser()` (~L45) | [derived from A-NNN — user specified RBAC] |

### New Files
| File | Purpose | Pattern Source | Source |
|------|---------|---------------|--------|
| `services/permissions.go` | Permission CRUD service | Follows `services/users.go` pattern | [derived from A-NNN + survey/architecture.md] |
| `handlers/permissions.go` | Permission API handlers | Follows `handlers/users.go` pattern | [derived from A-NNN + survey/surface.md] |

---

## Observable Truths

These are verification targets for foundry's INSPECT/ASSAY phases. Each must be (a) independently verifiable by reading code or running a test AND (b) traceable to a transcript answer that justifies why it's a truth worth observing.

> **Every OT must cite a transcript answer.** Observable Truths that have no transcript source are Claude-generated assertions — they're testable in isolation but unanchored to anything the user actually wanted, so they measure the wrong thing. If you can't cite a source, ask the user a follow-up question instead of inventing an OT.

### Domain: [domain-name]
- **OT-001** [from A-NNN]: [user-perspective verifiable statement — e.g., "A user with 'admin' role can access GET /api/admin"]
- **OT-002** [from A-NNN]: [error case]
- **OT-003** [derived from A-NNN]: [edge case the user's answer implied]
- **OT-004** [derived from A-NNN, A-NNN]: [integration truth]
- **OT-005** [from A-NNN]: [data truth]

### Domain: [domain-name-2]
- **OT-006** [from A-NNN]: ...

---

## Implementation Phases

> **Every phase task carries a citation to either the requirement it implements or the transcript answer that motivates it.** Phases are Claude's sequencing — but the *content* of each task must trace to a requirement or a user answer.

### Phase 1: Foundation
- [ ] [Specific task] — implements [FR-NNN | US-NNN | GI-NNN]
- [ ] [Specific task] — implements [FR-NNN]
- **Verification:** `go build ./...` and `go test ./models/... ./services/...`
- **Depends on:** nothing (foundation)

### Phase 2: Core
- [ ] [Specific task] — implements [US-NNN, FR-NNN]
- [ ] [Specific task] — implements [FR-NNN]
- **Verification:** `go test ./handlers/...`
- **Depends on:** Phase 1

### Phase 3: Integration & Polish
- [ ] [Specific task] — implements [US-NNN]
- [ ] [Specific task] — implements [OT-NNN verification]
- **Verification:** `go test ./... -count=1` (full suite)
- **Depends on:** Phase 2

---

## Test Strategy

**Existing test patterns** (from survey):
- Unit tests: [e.g., "Table-driven tests in services/users_test.go — follow this pattern"]
- Integration tests: [e.g., "Uses testcontainers for Postgres in tests/integration/"]
- Fixtures: [e.g., "Test data in testdata/ directory, loaded via helpers.LoadFixture()"]

**Tests to write for this feature:**
| Test File | Tests | Type |
|-----------|-------|------|
| `services/permissions_test.go` | CRUD operations, validation, edge cases | Unit |
| `handlers/permissions_test.go` | HTTP status codes, auth, validation | Unit |
| `tests/integration/permissions_test.go` | Full flow with real DB | Integration |

**Coverage target:** [Based on existing project standards from survey]

---

## Codebase References

Key files, functions, and patterns from the survey that inform this spec:

| Reference | Why It Matters | Source |
|-----------|---------------|--------|
| `models/user.go:User` | Struct being extended with permissions | [from survey/data.md] |
| `handlers/users.go:HandleCreateUser` | Pattern template for new handlers | [from survey/surface.md] |
| `services/users.go:UserService` | Pattern template for new service | [from survey/architecture.md] |
| `middleware/auth.go:RequireAuth` | Where permission checks plug in | [from survey/architecture.md] |
| `tests/integration/users_test.go` | Pattern template for integration tests | [from survey/architecture.md] |

---

## Appendix: Interview Transcript (EMBEDDED VERBATIM)

> **This is the source of truth.** Every citation marker (`[from A-NNN]`, `[derived from A-NNN]`) in the spec above resolves to an answer block below. Foundry teammates, PROVE, TRACE, and human reviewers all read the spec and the transcript as one document — no separate file lookups, no broken references. If a cite in the body refers to an A-NNN that does not appear below, that is hallucination and R4 validation fails.

*At R3 finalization, read `transcript.md` and paste its full body verbatim below this paragraph. Do not summarize, rephrase, or truncate. If the transcript is long, it is still embedded in full — downstream agents skim for the cited A-NNN they care about.*

[Transcript content pasted here at finalization time]
```

### SPEC WRITING RULES

1. Every US MUST have 2+ testable ACs including at least one error/edge case
2. Every AC must be verifiable (not "works correctly" — specific HTTP status, specific behavior)
3. Every "Codebase Integration" section must reference REAL files with line numbers from the survey
4. The File Change Map must list EVERY file that will be modified or created
5. Observable Truths must be user-perspective, verifiable, and include error cases
6. Implementation phases must reference specific files/functions with verification commands
7. Test strategy must reference actual test patterns and name specific test files to create
8. Technical Design must show current state AND proposed changes (not just proposed)
9. Error handling must follow the project's existing pattern (reference specific examples)
10. Do NOT use placeholder text — every example in the spec should be real and specific

### VERBATIM-FIDELITY RULES (HARD CONSTRAINTS)

These rules are non-negotiable. If any of them fails, the spec cannot be finalized — go back to the interview or fix the spec.

**Core principle:** the transcript is the source of truth. The spec is an index over the transcript organized into structured sections foundry can consume. **Every bullet, every table row, every requirement, every Observable Truth, every file change, every phase task in the spec MUST be traceable to a transcript answer OR to a survey file.** Unsourced prose is forbidden.

**Citation syntax:**
- `[from A-NNN]` — direct; the item quotes or restates a single transcript answer
- `[from A-NNN, A-NNN]` — direct synthesis of multiple answers
- `[derived from A-NNN]` — Claude-worded item grounded in the user's answer (used for Flexible requirements, synthesized User Stories, etc.)
- `[from survey/{file}.md]` — the item comes from codebase survey reality, not the interview
- `[derived from A-NNN + survey/{file}.md]` — user decision combined with codebase reality
- `[from reality.md Research Findings]` — item comes from R1.5 online research

**Rules:**

11. **Every Locked FR, NFR, AC, and GI MUST contain a double-quoted string that is a byte-identical substring of some A-NNN answer in `transcript.md`.** Not a paraphrase, not a close approximation, not "in other words" — the user's literal words inside quotes. If you cannot find a quotable answer for a requirement you believe is Locked, either (a) re-classify it as Flexible, or (b) ask the user the missing question before finalizing. Never invent the quote.

12. **Every item in the spec MUST carry a citation marker** using the syntax above. This applies to:
    - Problem Statement sentences
    - Scope bullets (In Scope, Out of Scope)
    - User Stories (each US gets a citation; each AC gets a citation)
    - Codebase Integration lines
    - Functional Requirements (Locked, Flexible, Informational)
    - Non-Functional Requirements
    - Observable Truths
    - Technical Design decisions (Data Model, API Design, Architecture, Error Handling rows)
    - File Change Map rows
    - Implementation Phase tasks (cite the requirement they implement, which in turn cites the transcript)
    - Codebase References
    - Global Invariants (GI-NNN)
    Items without citations are hallucination and MUST be removed or backed by a follow-up question. **Unsourced = doesn't exist.**

13. **The `## Global Invariants` section MUST be populated from transcript answers tagged `[ARCH_INVARIANT]` during R2.** If no answers were tagged, write "None — the user gave no explicit placement constraints" explicitly. Do NOT generate plausible-sounding invariants; that is hallucination, and foundry decompose will propagate hallucinated constraints into every casting.

14. **Claude's gloss is NEVER authoritative.** If you include a gloss after a quoted Locked item for reading convenience, it MUST be labeled "Claude's gloss (not authoritative)". The quote is the requirement; the gloss is navigation.

15. **Survey citations are allowed for Technical Design and Codebase Integration only.** These sections describe codebase reality (the survey found it, not the user). Every other section must have a transcript citation — if a User Story or Observable Truth is citing only a survey file with no user input backing it, that's Claude deciding what the user wants based on what the codebase looks like. That's exactly the drift this rule exists to prevent. Reality tells you what's there; the user tells you what to build. Conflate them and you build things nobody asked for.

16. **Survey-only citations on requirements are a red flag.** If a Locked or Flexible requirement cites `[from survey/...]` with no transcript answer, that's Claude inferring a requirement from the codebase rather than from the user. Treat as hallucination: either find a user answer or delete the requirement.

17. **If the transcript is empty or has fewer than 3 Q/A pairs, the spec cannot be finalized.** An interview with zero captured answers cannot produce a Locked requirement or a properly-cited spec. Go back to R2.

18. **The full `transcript.md` body MUST be embedded in the final spec as `## Appendix: Interview Transcript`**, pasted verbatim at finalization time. This makes the spec self-contained — downstream readers (foundry teammates, PROVE, TRACE, human reviewers) resolve every citation without opening another file. The appendix is not a summary; it is a byte-for-byte paste of the transcript file. If transcript.md is 10,000 lines, the appendix is 10,000 lines.

19. **Synthesize the `## State Transitions` table from transcript at R3.** Walk the transcript for state-machine language ("when X happens, Y becomes Z"; "after step N, transition to step M"; "transitions from STATE_A to STATE_B"). One row per identified transition; ID format `ST-NNN`; `from-state` / `to-state` are state-name strings; `trigger` is the event/method/input that fires the transition; `guard` is the precondition; `citation` is `[from A-NNN]` (the A-NNN whose body contains the transition language). If no transcript-grounded transitions exist, write the documented sentinel row. Do NOT generate plausible-sounding transitions from spec prose; the validator's content-difference rule (Jaccard ≥0.7) will reject paraphrased rows with `TYPED_ROW_PARAPHRASE`. Phase 6 PROBE-01, Phase 7 TEST-01, and Phase 8 INTENT-01 grep this table as their citation surface — REQUIRED.

20. **Synthesize the `## Contracts` table from transcript at R3.** Walk the transcript for surface-defining language (function names, endpoints, handlers, CLI commands, observable signatures). One row per identified surface; ID format `CT-NNN`; `surface` is the endpoint/function/handler name; `input` / `output` describe shapes or types; `errors` lists error codes / conditions / HTTP statuses; `citation` is `[from A-NNN]`. If no transcript-grounded contracts exist, write the documented sentinel row. Same hallucination ban as rule 19 — the user-stated observable contract layer is distinct from the implementation-stated `## Technical Design` block; do NOT collapse one into the other. REQUIRED.

21. **Citation form is Locked-only in typed-section rows.** Every row in `## Global Invariants`, `## State Transitions`, and `## Contracts` MUST cite `[from A-NNN]` where A-NNN is a Locked transcript answer (not survey, not R1.5 research, not A-AUTO-NNN). NO `[derived from A-NNN]` in typed rows. Survey-derived facts go in `## Technical Design` prose, never in typed tables. Sentinel rows are the one exception — their citation may be `[from A-NNN reasoning]` or `[from survey reasoning]` because they document the absence of the row, not the row itself. The validator enforces this via `TYPED_ROW_BAD_CITATION`.

22. **The `## Global Invariants` section is now a 5-column table.** GI-NNN row IDs are preserved from prior versions (a v4.2.0 spec's `**GI-001** [from A-NNN]: "..."` bullet graduates to a `| GI-001 | ... | [from A-NNN] |` row — same identity). The `### Architectural Placement` and `### Cross-Cutting Technical Rules` subheadings are dropped; the `applies-to` column carries that information at row granularity. The "None — the user gave no explicit placement constraints" sentinel graduates from a bullet/paragraph to a sentinel ROW (rule 13's "write 'None — ...' explicitly" still applies, just in row form). Phase 3 (TYPE-02) future work adds `spec_format_version` frontmatter for versioned enforcement; severity-agnostic prose ("validator enforces", "REQUIRED") locks here so Phase 3's warn->fail upgrade is a single-line edit at validate-spec.py, not a re-author of setup-forge.sh.

SPEC_PROMPT_EOF

# =========================================================================
# PHASE R3.5: SPEC REVIEW — Adversarial ambiguity reviewer (PROBE-01)
# =========================================================================

cat >> "$PROMPT_FILE" << 'SPEC_REVIEW_PROMPT_EOF'

## PHASE R3.5: SPEC REVIEW — Adversarial Ambiguity Review (PROBE-01)

This phase runs AFTER you have written the draft spec body (step 4 of FINALIZATION
SEQUENCE) and BEFORE the deterministic R4 Verbatim-Fidelity Gate (step 5). The
reviewer is code-path enforced: do NOT output `<promise>SPEC FORGED</promise>`
until R3.5 passes (verdict: "pass").

R3.5 only activates for `spec_format_version: v2.1`+ specs. v2.0 specs skip R3.5
(F0.5 step 2b stream-skip roster covers PROBE-01 for legacy v2.0 specs — no
behavior change for v4.2.0-era dependent projects).

Spawn the spec-reviewer agent (`plugins/forge/agents/spec-reviewer.md`,
`id: PROBE-01`, `model: sonnet`) via the Agent tool. Its tools are
`Read, Write, Grep, Glob` — read-and-emit only, no `Edit`/`Bash`/`Task`.

### MANDATORY TOOL-CALL ORDER (enforced)

The reviewer MUST read in this order:

1. Read `transcript.md` FIRST (full file — do not truncate, do not skim).
2. Then read the draft `spec.md`.

If the reviewer reads spec.md before transcript.md (the easier read order — the
spec is the more interesting document, the transcript is repetitive), the spec
biases the review. To prevent this self-anchoring, the reviewer MUST emit the
following structural error and STOP:

```json
{"review_version":"v1.0","verdict":"block","flag_count":0,"flags":[],"reviewer_order_violation":true}
```

The validator detects `reviewer_order_violation: true` and forces the reviewer
to be re-spawned with a fresh context. Do NOT label legitimate flags with this
token — it is the structural error emitted only when read order was violated.

### REVIEWER RUBRIC — what to flag (A-NNN-cited only)

The reviewer MAY flag ONLY contradictions or ambiguities directly grounded in
an existing A-NNN transcript answer. Every flag MUST cite a specific A-NNN.

Flag when:

- A typed-table row (`## Global Invariants` / `## State Transitions` / `## Contracts`)
  cites A-NNN but the row's content cells (statement, applies-to, from-state,
  to-state, trigger, surface, input, output, errors) misrepresent what A-NNN's
  body actually says.
- Two typed-table rows cite the same A-NNN and make contradictory claims about
  it (e.g., CT-001 and GI-002 both cite A-005 but disagree on scope or shape).
- A Locked FR/NFR/AC quotes A-NNN verbatim, but the quote, in context of the
  full A-NNN body, admits multiple contradictory valid implementations
  (e.g., A-007 says "we use bcrypt or argon2 — both fine"; FR-003 cites A-007
  with the Locked quote "we use bcrypt" without resolving the OR).

Do NOT flag:

- Missing detail the user never mentioned (no A-NNN = no flag).
- Items `validate-spec.py` already enforces (citation syntax, Jaccard paraphrase,
  verbatim-quote correctness, dangling A-NNN references, survey-only requirements).
- Style or completeness preferences not grounded in a transcript answer.
- Architectural advice or refactoring suggestions.

### 5-FLAG BUDGET CEILING

Emit AT MOST 5 flags. The output validator (`validate_spec_review.py`) rejects
any output with `len(flags) > 5`. If the reviewer genuinely identifies more than
5 contradictions, emit the 5 highest-severity ones — flags whose ambiguity has
the most concrete, transcript-grounded support. Do not pad to reach 5;
under-shooting is acceptable. >5 flags means >5 user clarifications which is
more attention than most users sustain in a single R2 INTERVIEW round.

### OUTPUT: spec-review.json (closed schema)

Write to: `{SESSION_DIR}/spec-review.json`

Format (closed vocabulary — unknown top-level or per-flag keys are rejected):

```json
{
  "review_version": "v1.0",
  "verdict": "pass" | "block",
  "flag_count": 0,
  "flags": [
    {
      "id": "FLAG-NNN",
      "citation": "A-NNN",
      "typed_row": "GI-NNN | ST-NNN | CT-NNN | null",
      "ambiguity": "one-sentence description of the contradiction"
    }
  ],
  "reviewer_order_violation": false
}
```

**Closed-schema rules** (validator enforces every one of these):

- Top-level keys allowed: `review_version`, `verdict`, `flag_count`, `flags`, `reviewer_order_violation`. Forbidden: `suggested_fix`, `recommendation`, `warnings`, `severity`, `notes`, `summary`, `metadata`, `confidence`, or any other top-level key.
- Per-flag keys allowed: `id`, `citation`, `typed_row`, `ambiguity`. Forbidden: `suggested_fix`, `recommendation`, `severity`, `confidence`, `priority`, `category`, `reasoning`, or any other per-flag key.
- `verdict` MUST be `"block"` whenever `len(flags) > 0`. `verdict` MUST be `"pass"` whenever `len(flags) == 0` AND `reviewer_order_violation == false`. Advisory shape (`verdict: "pass"` with non-empty flags) is rejected.
- `len(flags) <= 5`. Validator rejects `len(flags) > 5`.
- Every flag's `citation` MUST be a non-empty string AND MUST resolve to a real `A-NNN` (or `A-AUTO-NNN`) in `transcript.md`.

### Validator invocation

After the reviewer agent emits `spec-review.json`, run:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/validate_spec_review.py" \
  <SESSION_DIR>/spec-review.json <TRANSCRIPT_PATH>
```

Inspect the exit code:

- **Exit 0 AND `verdict == "pass"`** → R3.5 passes. Proceed to step 5 of FINALIZATION SEQUENCE (run `validate-spec.py`).
- **Exit 0 AND `verdict == "block"`** → R3.5 blocks. The schema is well-formed, the citations resolve, the budget is respected, but the reviewer found ≥1 transcript-grounded ambiguity. Follow the ON BLOCK procedure below.
- **Exit 1** → validator rejected the spec-review.json (schema violation, dangling citation, advisory mode, budget exceeded, order violation, etc.). Read the printed `FAIL:` lines, fix the spec-review.json (or re-spawn the reviewer agent if it produced malformed output), re-invoke. Do NOT proceed to step 5 until exit 0.
- **Exit 2** → usage error (wrong arguments). Fix the invocation.

### ON PASS (verdict: "pass")

Proceed to step 5 of FINALIZATION SEQUENCE (run `validate-spec.py` for the R4
Verbatim-Fidelity Gate). The reviewer's clean verdict does NOT imply the R4 gate
will also pass — they are independent gates. R3.5 catches transcript-grounded
ambiguities; R4 catches verbatim-fidelity violations.

### ON BLOCK (verdict: "block")

The reviewer does NOT resolve any flag. Resolution is the user's job, executed
via R2 INTERVIEW.

1. Print each flag's `ambiguity` text to the session (so the user sees the
   issues the reviewer raised).
2. Return to **R2 INTERVIEW** — ask the user to clarify each flag via
   `AskUserQuestion`. The user's answers become new `A-NNN` entries in
   `transcript.md` (same verbatim transcript discipline as R2: stable IDs,
   no paraphrase, append-only).
3. Re-run **R3 SPEC** (FINALIZATION SEQUENCE step 4) — regenerate the spec
   body re-reading the full augmented transcript, so the new A-NNN answers
   are reflected in the typed tables and Locked requirements.
4. Re-run **R3.5** (this phase). Loop until `verdict == "pass"`.

Do NOT auto-resolve any ambiguity. The reviewer surfaces; the user resolves.
`<promise>SPEC FORGED</promise>` is structurally unreachable until R3.5 passes
(SPEC FORGED is emitted in step 10 of FINALIZATION SEQUENCE, which follows
step 5 — `validate-spec.py` exits 0 — which itself follows step 4.5 — R3.5
verdict == "pass"). Both R3.5 and R4 must clear.

SPEC_REVIEW_PROMPT_EOF

# =========================================================================
# PHASE R4: VALIDATE — Self-check
# =========================================================================

cat >> "$PROMPT_FILE" << 'VALIDATE_PROMPT_EOF'

## PHASE R4: VALIDATE — Self-Check Before Declaring Done

After writing the spec, perform these validation checks. **Any failure in the Verbatim-Fidelity Gate is a hard stop — you cannot finalize until it passes.**

### File Reference Check
For every file path mentioned in the spec:
- Use Glob or Read to verify it exists
- If it doesn't exist, mark it as "[NEW]" in the spec (proposed new file)
- If it's wrong, fix the reference

### Pattern Reference Check
For key function/type references:
- Use Grep to verify they exist in the codebase
- Fix any incorrect references

### Coverage Check
- Does every US have acceptance criteria?
- Does every domain have 5+ Observable Truths?
- Are there obvious feature gaps? (e.g., mentioned auth but no logout story)
- Are error cases covered for every happy path?

### Verbatim-Fidelity Gate (HARD STOP, DETERMINISTIC)

**This gate is enforced by `validate-spec.py`, a deterministic Python script, not by your self-check. The script is authoritative — its exit code IS the gate. You do not get to decide "close enough."**

**Procedure (authoritative):**

1. Write the full spec (body + embedded appendix) to a draft path.
2. Run:
   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/validate-spec.py" <spec_path> <transcript_path>
   ```
3. Inspect the exit code:
   - **Exit 0** → gate passed. Proceed to finalization.
   - **Exit 1** → gate failed. The script printed a numbered list of failures with specific item IDs and reasons. Fix every failure, re-run the script, repeat until exit 0. Do NOT output `<promise>SPEC FORGED</promise>`, do NOT write the final spec file under its canonical name until exit 0.
   - **Exit 2** → usage error (wrong arguments). Fix the invocation.
4. If the script reports `UNCITED_ANSWERS` (coverage failure), every listed A-NNN must either be cited in the spec body (in any section — Informational is a fine home for context answers) or the answer must be removed from `transcript.md` because the user retracted it. You cannot silently drop interview content.
5. If the script reports `NOT_VERBATIM` on a Locked item, the quote is wrong. Either fix the quote to byte-match the cited answer, or re-classify the item as Flexible (which removes the verbatim requirement).
6. If the script reports `DANGLING_CITATION`, the cited A-NNN does not exist. This is hallucination — you invented a reference. Find the real answer or remove the item.
7. If the script reports `SURVEY_ONLY_REQUIREMENT`, you wrote a requirement that the user never mentioned, backed only by what the codebase looks like. Find a user answer that supports it or delete it.

**What the script checks (for your reference — the script's output is the final word, not this list):**

- Transcript has ≥3 A-NNN answers (interview depth sanity)
- Spec has `## Global Invariants` section
- Spec has `## State Transitions` section (Phase 2 / TYPE-01 typed table — REQUIRED, validator enforces)
- Spec has `## Contracts` section (Phase 2 / TYPE-01 typed table — REQUIRED, validator enforces)
- Spec has `## Appendix: Interview Transcript` section embedding the full transcript
- Every Locked FR/NFR/AC/GI (under `### Locked` subsections or inside Global Invariants) has: a double-quoted substring, a `[from A-NNN]` citation, the citation resolves, the quote is byte-identical to the cited answer
- Every line that has BOTH a quote AND a `[from A-NNN]` marker (anywhere in the spec) passes the verbatim check — catches AC bullets nested inside User Stories
- Every bullet and non-header table row in these sections has a citation or requirement-ID marker: Problem Statement, Scope, User Stories, Functional Requirements, Non-Functional Requirements, Global Invariants, State Transitions — every row, Contracts — every row, Technical Design, File Change Map, Observable Truths, Codebase References (Implementation Phases is exempt — it traces via `implements [FR-NNN]`)
- Every A-NNN reference in the spec body resolves to a real answer in the transcript
- No `[from Q-NNN]` (citations point at answers, not questions)
- If the transcript has `[ARCH_INVARIANT]`-tagged answers, the Global Invariants section has ≥1 GI-NNN entry
- No Locked/Flexible FR or NFR cites only a survey file with no user answer backing (survey-only = codebase-inferred = hallucination)
- Every A-NNN in the transcript is cited at least once in the spec body (coverage — no interview content silently dropped)
- Typed-section rows (`## Global Invariants`, `## State Transitions`, `## Contracts`) cite Locked-only `[from A-NNN]` (no `[derived from A-NNN]`, no survey citations, no `A-AUTO-NNN` references in typed rows — sentinel rows excepted; validator enforces via `TYPED_ROW_BAD_CITATION`)
- Typed-section rows are content-different from adjacent prose (Jaccard ≥0.7 fails as `TYPED_ROW_PARAPHRASE` — validator enforces)

**Legacy self-check (still run, but non-authoritative):**

Before calling the script, you may run through the manual checks below as a sanity pass. The script will catch anything you miss. **Do not skip the script even if your self-check passed.**

1. **Read `transcript.md`.** Build an in-memory index of every A-NNN block and its verbatim text. Also collect the set of valid A-NNN IDs present in the file.

2. **For every Locked requirement in the spec** (GI-NNN under Global Invariants, FR-NNN under Functional Requirements › Locked, NFR-NNN under Non-Functional Requirements › Locked, AC-NNN marked Locked):
   - **Check L1 — has a quoted string:** The item MUST contain a double-quoted substring (matching `"..."`). If missing → FAIL: "Locked item {ID} has no verbatim quote. Either find the user's actual words in transcript.md and quote them, or re-classify as Flexible."
   - **Check L2 — has a citation:** The item MUST contain a `[from A-NNN]` or `[from A-NNN, A-NNN]` marker. If missing → FAIL: "Locked item {ID} has no transcript citation."
   - **Check L3 — citation resolves:** The cited A-NNN(s) MUST exist in transcript.md. If the citation points to an A-NNN that does not exist → FAIL: "Locked item {ID} cites A-NNN which is not in transcript.md. This is hallucination."
   - **Check L4 — quote is verbatim:** The quoted substring MUST be a byte-identical substring of the cited A-NNN's body. Perform a string-contains check. If the quote is a paraphrase of the cited answer rather than a verbatim substring → FAIL: "Locked item {ID} quotes text that does not appear verbatim in A-NNN. Either fix the quote to match the transcript, or re-classify as Flexible."

3. **UNIVERSAL CITATION CHECK — every bullet, row, and sentence in the spec body MUST have a citation marker.** This is what makes the spec an index over the transcript instead of a gloss.

   Walk the spec body (everything above `## Appendix: Interview Transcript`) and scan section-by-section. For each of the sections below, the rule is: every list item (lines starting with `-` or `- [ ]`), every table row (excluding headers/separators), and every non-heading paragraph line MUST contain at least one of these markers:
   - `[from A-NNN]` or `[from A-NNN, A-NNN, ...]`
   - `[derived from A-NNN]` or `[derived from A-NNN, A-NNN, ...]`
   - `[from survey/{file}.md]` — only valid for Technical Design, Codebase Integration, Codebase References, File Change Map
   - `[derived from A-NNN + survey/{file}.md]`
   - `[from reality.md ...]` — only valid for Informational items

   **Sections subject to this check:**
   - `## Problem Statement` — every sentence
   - `## Scope` (In Scope, Out of Scope) — every bullet
   - `## User Stories` — every US header line, every AC bullet, every Codebase Integration bullet
   - `## Functional Requirements` — every FR bullet (Locked, Flexible, Informational)
   - `## Non-Functional Requirements` — every NFR bullet
   - `## Global Invariants` — every row (Locked-only `[from A-NNN]` citation in the citation cell; OR a sentinel row in the documented form)
   - `## State Transitions` — every row (Locked-only `[from A-NNN]` citation; OR a sentinel row in the documented form — REQUIRED, validator enforces)
   - `## Contracts` — every row (Locked-only `[from A-NNN]` citation; OR a sentinel row in the documented form — REQUIRED, validator enforces)
   - `## Technical Design` — every bullet under Data Model Changes, API Design, Architecture, Error Handling; every table row in New endpoints / Modified endpoints / Error cases
   - `## File Change Map` — every table row
   - `## Observable Truths` — every OT bullet
   - `## Implementation Phases` — every phase task bullet (cites a requirement ID, which in turn cites the transcript)
   - `## Codebase References` — every table row (survey citations allowed here)

   **For each offending line** (a line that should have a citation but doesn't) → FAIL: "Unsourced bullet: '{line text}' in section '{section}'. Every item in the spec must cite its transcript or survey source. Unsourced prose = hallucination. Either add a citation, delete the line, or ask the user a follow-up question to ground it."

   **Exceptions** (these do NOT need citations):
   - Markdown headings (`#`, `##`, `###`)
   - Table header rows and separator rows (`|---|---|`)
   - Blockquote guidance lines the template itself ships (`> **Every bullet...`)
   - Horizontal rules (`---`)
   - Blank lines
   - Phase verification commands (`- **Verification:** ...`) and phase dependency lines (`- **Depends on:** ...`) — these are scaffolding, not claims about user intent
   - The literal sentinel "None — the user gave no explicit placement constraints." in Global Invariants (or its sentinel-row form `| — | None — the user gave no explicit placement constraints. | — | — | [from A-NNN reasoning] |`)
   - Sentinel rows in `## State Transitions` (`| — | — | — | None — this feature has no state transitions | — | [from A-NNN reasoning] |`) and `## Contracts` (`| — | None — no observable contracts beyond internal helper signatures | — | — | — | [from A-NNN reasoning] |`) — these document the absence of typed rows, not the rows themselves

4. **CITATION RESOLUTION CHECK — every A-NNN marker anywhere in the spec body MUST resolve to a real transcript answer.**

   Scan the spec body for every `A-NNN` reference (inside any `[from ...]` or `[derived from ...]` marker). For each reference:
   - If A-NNN does not exist in transcript.md → FAIL: "Dangling citation: spec references A-NNN at '{location}' but transcript.md has no such answer. This is hallucination."
   - Exception: Q-NNN references (questions) are allowed in the transcript but NOT in spec citations — the spec cites answers, not questions. If you find a `[from Q-NNN]` marker → FAIL: "Spec cites a question (Q-NNN) instead of an answer (A-NNN). Change the citation to point at the answer."

5. **SURVEY CITATIONS ON REQUIREMENTS — red-flag check.**

   Walk the Locked and Flexible subsections of Functional Requirements and Non-Functional Requirements. If any FR or NFR has ONLY a `[from survey/...]` citation with no `[from A-NNN]` or `[derived from A-NNN]` companion → FAIL: "Requirement {ID} is sourced from the codebase survey but has no transcript answer backing it. This means Claude inferred a requirement from the codebase rather than from the user. Either find a transcript answer to cite, or delete the requirement. Reality describes what's there; the user says what to build."

6. **For every Informational item:** Check it has a `[from A-NNN]` or `[from reality.md …]` citation. If missing → FAIL: "Informational item has no source citation."

7. **Typed-section existence (Global Invariants / State Transitions / Contracts):**
   - Section header `## Global Invariants` MUST exist in the spec. If missing → FAIL: "Global Invariants section missing — foundry decompose will have nothing to propagate to castings, and architectural-placement violations will slip through."
   - Section content MUST be either (a) one or more GI-NNN rows with verbatim quotes + Locked-only `[from A-NNN]` citations, OR (b) a sentinel row in the documented form (`| — | None — the user gave no explicit placement constraints. | — | — | [from A-NNN reasoning] |`). If the section is empty, contains placeholder text, or contains invented (uncited) invariants → FAIL.
   - If the transcript contains any answer tagged `[ARCH_INVARIANT]`, the Global Invariants section MUST contain at least one GI-NNN row citing that answer. Empty invariants when the transcript has tagged answers → FAIL: "Transcript has ARCH_INVARIANT-tagged answers but Global Invariants section is empty. Extract them."
   - Section header `## State Transitions` MUST exist in the spec. If missing → FAIL: "## State Transitions section missing — Phase 6 PROBE-01, Phase 7 TEST-01, Phase 8 INTENT-01 require typed sections as their citation surface; missing this section is a TYPE-01 violation."
   - Section content MUST be either (a) one or more ST-NNN rows in the documented 6-column form (`| ID | from-state | to-state | trigger | guard | citation |`) with Locked-only `[from A-NNN]` citations, OR (b) a sentinel row in the documented form (`| — | — | — | None — this feature has no state transitions | — | [from A-NNN reasoning] |`). The heading without a table or with an empty table → FAIL.
   - Section header `## Contracts` MUST exist in the spec. If missing → FAIL: "## Contracts section missing — Phase 6 PROBE-01, Phase 7 TEST-01, Phase 8 INTENT-01 require typed sections as their citation surface; missing this section is a TYPE-01 violation."
   - Section content MUST be either (a) one or more CT-NNN rows in the documented 6-column form (`| ID | surface | input | output | errors | citation |`) with Locked-only `[from A-NNN]` citations, OR (b) a sentinel row in the documented form (`| — | None — no observable contracts beyond internal helper signatures | — | — | — | [from A-NNN reasoning] |`). The heading without a table or with an empty table → FAIL.

8. **Transcript sanity:**
   - `transcript.md` MUST exist and MUST contain at least 3 A-NNN blocks. If fewer → FAIL: "Interview too shallow to produce a properly-cited spec. Return to R2 and ask more questions."
   - The `## Appendix: Interview Transcript` section MUST be present in the spec and MUST contain the full byte content of transcript.md (excluding the transcript file's own frontmatter, which is redundant with the section header). After writing, verify: `grep -c "^## A-" spec.md` ≥ the count in transcript.md. If the appendix is truncated or missing → FAIL: "Transcript appendix missing or incomplete. The spec must be self-contained."

**On any FAIL:** print a report listing every failed check with the specific item ID and reason, then:
- If the fix is "re-classify Locked → Flexible", make the change inline in the draft and re-run the gate.
- If the fix requires new user input (the user never answered the question that would produce a Locked quote), return to R2 INTERVIEW and ask the missing question using AskUserQuestion. Append the new Q/A to transcript.md, then re-run the gate.
- If the fix is "remove hallucinated invariant", delete the uncited entry and re-run the gate.
- Do NOT finalize the spec with any Verbatim-Fidelity failures. Do NOT output `<promise>SPEC FORGED</promise>`.

**Why these checks are HARD:** Soft validation ("try to quote when possible") will not hold against the pressure to finalize. Making these blocking is the only way to prevent the drift we've seen in prior runs, where an interview answer like "operator stays generic, agent handles per-node like IDM" became an Informational line, got dropped by foundry decompose's empty invariants, and produced a multi-cycle revert. The verbatim contract is the mechanism that keeps user intent load-bearing through every downstream agent.

### Report Issues
If any non-verbatim check (file refs, pattern refs, coverage) finds issues, fix them in the spec before finalizing. Non-verbatim issues are soft — use judgment. Verbatim issues are hard — block finalization.

VALIDATE_PROMPT_EOF

# =========================================================================
# FINALIZATION CONSTRAINTS
# =========================================================================

cat >> "$PROMPT_FILE" << 'FINAL_PROMPT_EOF'

## FINALIZATION CONSTRAINTS — CRITICAL

When the user says "done", "finalize", "finished", or similar:

### SEQUENCE:
1. **Read `transcript.md` in full.** You will need both its byte content (to paste into the Appendix) and its A-NNN index (to validate citations in step 3).
2. Generate the full spec body (PHASE R3 template above) with complete citations on every bullet.
3. **Append the Appendix.** After the `## Codebase References` section, append:
   ```
   ---

   ## Appendix: Interview Transcript (EMBEDDED VERBATIM)

   > This is the source of truth. Every citation marker in the spec above resolves to an answer block below.

   ```
   Then paste the full byte content of `transcript.md` — every Q-NNN and A-NNN block, the file header, everything. No truncation, no summary, no "[transcript continues]" ellipses. A byte-for-byte copy.
4. Write the complete draft spec (body + appendix) to the spec path in a single Write call. (Use the canonical SPEC_PATH — the script needs a real file to validate.)
4.5. **Run the R3.5 spec-review gate (PROBE-01 — `spec_format_version: v2.1`+ only).** Spawn the spec-reviewer agent (`plugins/forge/agents/spec-reviewer.md`, `id: PROBE-01`, `model: sonnet`). The agent reads `transcript.md` FIRST then the draft `spec.md`, and writes `{SESSION_DIR}/spec-review.json` with up to 5 A-NNN-cited ambiguity flags and a binary block/pass verdict. Then run:
   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/validate_spec_review.py" <SESSION_DIR>/spec-review.json <TRANSCRIPT_PATH>
   ```
   - Exit 0 AND `verdict == "pass"`: proceed to step 5.
   - Exit 0 AND `verdict == "block"`: print each flag's `ambiguity` text, return to R2 INTERVIEW for resolution (the user's answers append to `transcript.md` as new A-NNN entries, same verbatim discipline as R2), re-run step 4 (regenerate the spec body re-reading the augmented transcript), re-run step 4.5. Loop until `verdict == "pass"`.
   - Exit 1: validator rejected the spec-review.json (schema violation, dangling citation, advisory mode, budget exceeded, order violation, etc.). Read the printed FAIL: lines, fix the spec-review.json (or re-spawn the reviewer agent if it produced malformed output), re-invoke. Do NOT proceed to step 5 until exit 0 + `verdict == "pass"`.
   - Exit 2: usage error. Fix the invocation.

   `<promise>SPEC FORGED</promise>` is structurally unreachable until R3.5 passes. Phase 6 PROBE-01 only activates for `spec_format_version: v2.1`+ specs; v2.0 specs skip step 4.5 (the F0.5 step 2b stream-skip roster covers PROBE-01 for legacy v2.0 specs — no behavior change for v4.2.0-era dependent projects).
5. **Run the deterministic R4 Verbatim-Fidelity Gate:**
   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/validate-spec.py" <SPEC_PATH> <TRANSCRIPT_PATH>
   ```
   - Exit 0: proceed to step 6.
   - Exit 1: read the printed failures, fix the spec (use Edit/Write on SPEC_PATH), re-run the script. Loop until exit 0. Do NOT proceed until the script exits 0. Do NOT output `<promise>SPEC FORGED</promise>` on exit 1.
   - Exit 2: fix the invocation arguments.
6. Write JSON spec to the JSON path.
7. Write progress file with all phases marked [PENDING].
8. Delete the state file using Write with empty content.
9. **Do NOT delete `transcript.md`.** The embedded appendix is a copy; the standalone transcript.md remains as a working artifact. Downstream debugging and future re-spawns of forge (`/forge:resume`) depend on its continued existence.
10. Output `<promise>SPEC FORGED</promise>`.
11. STOP IMMEDIATELY.

### ALLOWED ACTIONS:
- Read any files needed for validation
- Write the final spec, JSON, and progress files
- Glob/Grep for validation checks
- Delete state file

### FORBIDDEN ACTIONS:
- NO implementation of any kind
- NO code changes
- NO Task/Agent tool calls during finalization
- NO offering to implement — the spec is the deliverable

### JSON SPEC FORMAT:
Write the JSON spec with this structure:
```json
{
  "feature": "FEATURE_NAME",
  "slug": "FEATURE_SLUG",
  "version": "1.0.0",
  "generated_by": "forge",
  "timestamp": "TIMESTAMP",
  "survey": { "performed": true/false, "agents": 4, "files": ["architecture.md", "data.md", "surface.md", "infra.md"] },
  "user_stories": [{ "id": "US-001", "title": "...", "acceptance_criteria": ["AC-001: ...", "AC-002: ..."] }],
  "functional_requirements": [{ "id": "FR-001", "description": "...", "maps_to": ["US-001"] }],
  "nonfunctional_requirements": [{ "id": "NFR-001", "description": "..." }],
  "observable_truths": [{ "id": "OT-001", "domain": "...", "statement": "..." }],
  "implementation_phases": [{ "phase": 1, "name": "...", "tasks": ["..."], "verification": "..." }],
  "codebase_references": ["file1.go", "file2.ts"]
}
```

### CRITICAL: SPEC FORGED MEANS STOP
After outputting `<promise>SPEC FORGED</promise>`, you MUST stop. Do not:
- Offer to implement the feature
- Suggest next steps beyond "use /foundry --spec"
- Make any code changes
- Run any commands

The spec is the deliverable. Foundry builds it.

FINAL_PROMPT_EOF

# =========================================================================
# Session information
# =========================================================================

cat >> "$PROMPT_FILE" << SESSION_EOF

## SESSION INFORMATION

- **Feature:** $FEATURE_NAME
- **Feature Slug:** $FEATURE_SLUG
- **Draft File:** $DRAFT_PATH (update this every 2-3 questions)
- **Transcript File:** $TRANSCRIPT_PATH (append every Q/A verbatim, immediately after each answer — see R2 rule #8)
- **Final Spec:** $SPEC_PATH (write here when user says done)
- **JSON Spec:** $JSON_PATH (write here when user says done)
- **Progress:** $PROGRESS_PATH (write here when user says done)
- **Survey Directory:** $SURVEY_DIR (agents write here)
- **Reality Document:** $REALITY_PATH (synthesized survey)
- **State File:** $STATE_PATH (delete when done)
- **Started:** $TIMESTAMP
- **Project Language:** ${PROJECT_LANG:-"unknown (survey will detect)"}
- **Source File Count:** $SRC_COUNT
- **Survey Mode:** $(if [[ "$NO_SURVEY" == "true" ]]; then echo "SKIPPED"; else echo "ACTIVE"; fi)
- **Focus Directories:** ${FOCUS_DIRS:-"entire project"}
$(if [[ -n "$USER_PROMPT" ]]; then echo "- **User Intent:** $USER_PROMPT"; fi)

---

$(if [[ -n "$USER_PROMPT" ]]; then
cat << INTENT_EOF
## USER INTENT

The user told you what they want: **"$USER_PROMPT"**

This is your primary directive. Everything — the survey focus, the interview questions, the spec output —
should serve this intent. For example:
- "refine this spec deeper" → read the context file as an existing spec, probe its gaps, produce a more detailed version
- "focus on error handling" → survey for error patterns, ask about failure modes, spec every error case
- "add observability" → survey for logging/metrics, ask about SLOs, spec monitoring requirements
- "break this into smaller pieces" → analyze the context for decomposition boundaries

Adapt your approach to match what the user asked for.

INTENT_EOF
fi)

## BEGIN NOW

$(if [[ "$NO_SURVEY" == "false" ]]; then
  echo "Start by spawning the 4 R0 Explore agents in parallel in a SINGLE message (architecture, data, surface, infra)."
  echo ""
  echo "Replace {SURVEY_DIR} in the agent prompts with: $SURVEY_DIR"
  if [[ -n "$FOCUS_DIRS" ]]; then
    echo ""
    echo "Focus codebase survey agents on these directories: $FOCUS_DIRS"
  fi
else
  echo "Survey is skipped (--no-survey passed). Begin the interview immediately by asking your first question about \"$FEATURE_NAME\" using AskUserQuestion."
fi)

SESSION_EOF

# Read the complete prompt
INTERVIEW_PROMPT=$(cat "$PROMPT_FILE")
rm "$PROMPT_FILE"

# Write state file
cat > "$STATE_PATH" << STATE_EOF
---
active: true
engine: forge
version: "2.0.0"
phase: "R0_SURVEY"
iteration: 1
max_iterations: $MAX_QUESTIONS
started_at: "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
spec_type: ""  # Set in R2 INTERVIEW: GREENFIELD, MIGRATION, BUG_FIX, REFACTOR
entrypoint_node_id: ""  # Set in R2 INTERVIEW (brownfield only): flow-graph node ID where new work attaches, user-confirmed in plan.md §R2 step 3
entrypoint_anchor: ""  # Set in R2 INTERVIEW (brownfield only): file:line of the confirmed entrypoint node's anchor
feature_name: "$FEATURE_NAME"
feature_slug: "$FEATURE_SLUG"
output_dir: "$OUTPUT_DIR"
spec_path: "$SPEC_PATH"
json_path: "$JSON_PATH"
progress_path: "$PROGRESS_PATH"
draft_path: "$DRAFT_PATH"
transcript_path: "$TRANSCRIPT_PATH"
state_path: "$STATE_PATH"
survey_dir: "$SURVEY_DIR"
reality_path: "$REALITY_PATH"
context_file: "$CONTEXT_FILE"
no_survey: $NO_SURVEY
first_principles: $FIRST_PRINCIPLES
focus_dirs: "$FOCUS_DIRS"
user_prompt: "$USER_PROMPT"
---

$INTERVIEW_PROMPT
STATE_EOF

# Initialize draft spec
cat > "$DRAFT_PATH" << DRAFT_EOF
# Specification Draft: $FEATURE_NAME

*Forge interview in progress - Started: $TIMESTAMP*

## Survey Status
$(if [[ "$NO_SURVEY" == "true" ]]; then echo "Skipped (--no-survey)"; else echo "- [ ] Architecture agent"; echo "- [ ] Data agent"; echo "- [ ] Surface agent"; echo "- [ ] Infra agent"; echo "- [ ] Reality document synthesized"; fi)

## Overview
[To be filled during interview]

## Problem Statement
[To be filled during interview]

## Scope

### In Scope
- [To be filled during interview]

### Out of Scope
- [To be filled during interview]

## User Stories

<!--
Format each story with VERIFIABLE acceptance criteria and CODEBASE INTEGRATION:

### US-001: [Story Title]
**As a** [user], **I want** [action], **so that** [benefit].

**Acceptance Criteria:**
- AC-001: [Specific, testable — e.g., "API returns 200 for valid input"]
- AC-002: [Another — e.g., "Error message shown for invalid email"]

**Codebase Integration:**
- Extends: [file:function from survey]
- Pattern: [existing pattern to follow]
- New: [proposed file locations]
-->

[To be filled during interview]

## Functional Requirements
<!-- FR-001: [Description] — Maps to: US-NNN -->
[To be filled during interview]

## Non-Functional Requirements
<!-- NFR-001: [Description with specific metric] -->
[To be filled during interview]

## Technical Design

### Data Model
[To be filled — reference existing models from survey]

### API Design
[To be filled — follow existing endpoint patterns from survey]

### Architecture
[To be filled — align with existing architecture from survey]

## Observable Truths
<!--
Per domain, min 5 each. User-perspective, verifiable.
- OT-001: [Statement a user or test can verify]
-->
[To be filled during interview]

## Implementation Phases

### Phase 1: Foundation
- [ ] [Task with specific file references]
- **Verification:** \`[command]\`

### Phase 2: Core
- [ ] [Task with specific file references]
- **Verification:** \`[command]\`

### Phase 3: Integration
- [ ] [Task with specific file references]
- **Verification:** \`[command]\`

## Test Strategy
[Reference actual test patterns from survey]

## Codebase References
[Key files and patterns from survey]

## Definition of Done
- [ ] All acceptance criteria pass
- [ ] All Observable Truths verified
- [ ] Tests pass: \`[command]\`
- [ ] Lint/typecheck: \`[command]\`
- [ ] Build succeeds: \`[command]\`

## Next Steps

\`\`\`
/foundry --spec $SPEC_PATH
\`\`\`

Forge plans. Foundry builds.

## Open Questions
[To be filled during interview]

---
*Interview notes accumulated below*
---

DRAFT_EOF

# Initialize transcript (verbatim Q/A record, source of truth for R3 SPEC)
cat > "$TRANSCRIPT_PATH" << TRANSCRIPT_EOF
# Interview Transcript: $FEATURE_NAME

*Verbatim Q/A record. Append every question + answer immediately after AskUserQuestion returns. See R2 rule #8.*
*Started: $TIMESTAMP*

---

TRANSCRIPT_EOF

# Output setup message
echo "Forge - Codebase-Aware Specification Engine"
echo ""
echo "Feature: $FEATURE_NAME"
echo "State: $STATE_PATH"
echo "Draft: $DRAFT_PATH"
echo "Transcript: $TRANSCRIPT_PATH"
echo "Output: $SPEC_PATH"
echo "JSON: $JSON_PATH"
echo "Survey: $SURVEY_DIR"
echo "Reality: $REALITY_PATH"
if [[ -n "$CONTEXT_FILE" ]]; then
  echo "Context: $CONTEXT_FILE"
fi
if [[ $MAX_QUESTIONS -gt 0 ]]; then
  echo "Max Questions: $MAX_QUESTIONS"
else
  echo "Max Questions: unlimited"
fi
if [[ "$NO_SURVEY" == "true" ]]; then
  echo "Survey: SKIPPED (--no-survey)"
else
  echo "Survey: ACTIVE (4 parallel agents)"
  if [[ -n "$FOCUS_DIRS" ]]; then
    echo "Focus: $FOCUS_DIRS"
  fi
fi
if [[ "$FIRST_PRINCIPLES" == "true" ]]; then
  echo "Mode: First Principles (challenges assumptions first)"
fi
if [[ -n "$USER_PROMPT" ]]; then
  echo "Intent: $USER_PROMPT"
fi
echo ""
echo "Forge researches first, then interviews. Say \"done\" when finished."
echo ""
echo "$INTERVIEW_PROMPT"
