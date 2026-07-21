export const meta = {
  name: 'holmes-review',
  description: 'Macro-architecture / design-cohesion review. Builds a grounded structural map of the target, runs 7 blind design lenses (package proliferation, missed sharing, helper sprawl, flow & layering, cohesion, naming consistency, accretion markers), adversarially verifies every finding to drop intentional patterns, runs a completeness critic that re-lenses uncovered areas, then synthesizes a per-subsystem deliberate-vs-accreted verdict with concrete proposed reshaping. Report only — never edits.',
  whenToUse: 'When you want a senior-engineer design critique of a codebase or change: is this shaped right or accreted, why so many packages/helpers, where could logic have been shared, does the architecture flow — with proposed solutions. Driven by /holmes:review.',
  phases: [
    { title: 'Map' },
    { title: 'Lenses' },
    { title: 'Verify' },
    { title: 'Critic' },
    { title: 'Synthesize' },
  ],
}

// ---------------------------------------------------------------------------
// Schemas
// ---------------------------------------------------------------------------

const MAP_SCHEMA = {
  type: 'object',
  properties: {
    scope_manifest: {
      type: 'array',
      description: 'Concrete files and/or directories actually in scope for this review',
      items: { type: 'string' },
    },
    modules: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          name: { type: 'string' },
          path: { type: 'string' },
          responsibility: { type: 'string', description: 'What this module is FOR, in one sentence' },
          depends_on: { type: 'array', items: { type: 'string' } },
        },
        required: ['name', 'path', 'responsibility'],
      },
    },
    intended_architecture: {
      type: 'string',
      description: 'The apparent intended shape — layers, boundaries, the flow the author seems to have meant. State plainly if there is no discernible intended shape.',
    },
    observations: { type: 'string', description: 'Early structural observations to seed the lenses' },
  },
  required: ['scope_manifest', 'modules', 'intended_architecture'],
}

const FINDINGS_SCHEMA = {
  type: 'object',
  properties: {
    findings: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          id: { type: 'string', description: 'Unique within this lens, e.g. pkg-1' },
          title: { type: 'string', description: 'The design problem in one line' },
          locations: {
            type: 'array',
            description: 'Concrete file:line anchors — at least one. e.g. "pkg/auth/token.go:42"',
            items: { type: 'string' },
          },
          evidence: { type: 'string', description: 'What in the code demonstrates this — specific, not "appears to"' },
          why_it_smells: { type: 'string', description: 'Why this is a design problem, not just a preference' },
          proposed_solution: { type: 'string', description: 'Concrete reshaping — what to move/merge/split/rename and roughly how' },
          severity: { type: 'string', enum: ['low', 'medium', 'high'] },
          effort: { type: 'string', enum: ['small', 'medium', 'large'] },
        },
        required: ['id', 'title', 'locations', 'evidence', 'why_it_smells', 'proposed_solution', 'severity'],
      },
    },
  },
  required: ['findings'],
}

const VERDICT_SCHEMA = {
  type: 'object',
  properties: {
    isReal: { type: 'boolean', description: 'true only if the smell survives a genuine attempt to justify the status quo' },
    confidence: { type: 'string', enum: ['low', 'medium', 'high'] },
    refutation: { type: 'string', description: 'The strongest case that the current shape is intentional and correct. If that case wins, isReal is false.' },
    refined_solution: { type: 'string', description: 'A sharpened proposed solution if the finding survives, else empty' },
  },
  required: ['isReal', 'confidence', 'refutation'],
}

const CRITIC_SCHEMA = {
  type: 'object',
  properties: {
    complete: { type: 'boolean' },
    gaps: {
      type: 'array',
      description: 'Files/modules in scope that no surviving finding touched, or design questions not yet asked',
      items: {
        type: 'object',
        properties: {
          area: { type: 'string' },
          path: { type: 'string' },
          question: { type: 'string', description: 'The specific design question to investigate in this area' },
        },
        required: ['area', 'question'],
      },
    },
  },
  required: ['complete', 'gaps'],
}

const REPORT_SCHEMA = {
  type: 'object',
  properties: {
    overall_verdict: { type: 'string', description: 'One paragraph: is the target deliberate, mixed, or accreted overall, and why' },
    themes: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          subsystem: { type: 'string' },
          verdict: { type: 'string', enum: ['deliberate', 'mixed', 'accreted'] },
          narrative: { type: 'string', description: 'The design story for this subsystem grounded in the findings' },
          findings: {
            type: 'array',
            items: {
              type: 'object',
              properties: {
                title: { type: 'string' },
                locations: { type: 'array', items: { type: 'string' } },
                severity: { type: 'string', enum: ['low', 'medium', 'high'] },
                proposed_solution: { type: 'string' },
              },
              required: ['title', 'locations', 'severity', 'proposed_solution'],
            },
          },
        },
        required: ['subsystem', 'verdict', 'narrative', 'findings'],
      },
    },
    reshaping_plan: {
      type: 'array',
      description: 'Ordered, concrete reshaping steps — highest leverage first',
      items: {
        type: 'object',
        properties: {
          step: { type: 'string' },
          rationale: { type: 'string' },
          effort: { type: 'string', enum: ['small', 'medium', 'large'] },
        },
        required: ['step', 'rationale'],
      },
    },
  },
  required: ['overall_verdict', 'themes', 'reshaping_plan'],
}

// ---------------------------------------------------------------------------
// Lens definitions — each is a blind, single-question design critic
// ---------------------------------------------------------------------------

const LENSES = [
  {
    key: 'package-proliferation',
    title: 'Package / module proliferation',
    question: 'Are there more packages/modules/files than the problem warrants? Are boundaries arbitrary, or do they each earn their existence? Could several collapse into one without loss? Conversely, is one module secretly three things that should split?',
  },
  {
    key: 'missed-sharing',
    title: 'Missed sharing & reuse',
    question: 'Where is logic reimplemented that already exists elsewhere in scope (or in an obvious shared util)? Where do two+ call sites do the same work that should have been shared? This is about DESIGN signal — repeated logic as evidence of a missing seam — not mechanical dedup.',
  },
  {
    key: 'helper-sprawl',
    title: 'Helper sprawl & abstraction fit',
    question: 'Are there many small helper functions that did not need to exist (single-use wrappers, indirection that hides nothing)? Or the opposite — copy-paste where a real abstraction was warranted? Is anything over-abstracted (premature generics, config for one caller) or under-abstracted?',
  },
  {
    key: 'flow-layering',
    title: 'Flow & layering',
    question: 'Does control and data flow move cleanly in one direction, or zigzag across boundaries? Are dependencies pointed the right way (does low-level reach up into high-level)? Does the reader have to jump around to follow one operation? Does it flow, or fight you?',
  },
  {
    key: 'cohesion',
    title: 'Cohesion',
    question: 'Does each module/type/function do one coherent thing, or is it a grab-bag of unrelated responsibilities bundled by accident of location? Are related things scattered across distant places that should sit together?',
  },
  {
    key: 'consistency',
    title: 'Naming & structural consistency',
    question: 'Do things that are the same kind LOOK the same (naming, signatures, file layout, error shape, construction patterns)? Where does an inconsistency signal two authors / two eras / a half-finished migration rather than a deliberate distinction?',
  },
  {
    key: 'accretion',
    title: 'Accretion markers',
    question: 'What shows the code grew without pruning? v1 alongside v2, dead feature flags, vestigial layers, commented-out paths kept "just in case", abstractions whose only remaining caller is a test, names that no longer match behavior. Signs of accretion vs deliberate evolution.',
  },
]

// ---------------------------------------------------------------------------
// Prompt builders
// ---------------------------------------------------------------------------

const GROUNDING = `
GROUNDING DISCIPLINE (non-negotiable):
- Every finding MUST carry at least one concrete file:line anchor you actually read. No anchor → no finding.
- Read the code and trace call sites. Never treat a comment as the source of truth — comments lie and go stale.
- Forbidden phrases: "appears to", "looks like", "seems to", "probably". Either you verified it in the code or it is not a finding.
- This is MACRO-DESIGN critique, not bug-hunting and not lint. Do not report missing error handling, style nits, or correctness bugs. Report SHAPE: why it is structured this way, what would make it cohere.
- Every finding needs a concrete proposed solution — what to move/merge/split/rename and roughly how. "Consider refactoring" is not a solution.
- Be willing to find nothing. An empty findings array is a valid, honest result for a well-shaped target.`

function mapPrompt(target) {
  return `You are mapping the structure of a codebase region for a design-cohesion review.

TARGET TO REVIEW (interpret this literally — it may be a path, "the diff"/"the branch", a named subsystem, or a description of some logic):
"""
${target}
"""

Resolve the target into a concrete set of files/directories using the tools available to you:
- For a path/dir: glob it.
- For "the diff" / "the branch" / "uncommitted changes": run the appropriate git command (e.g. git diff --name-only main...HEAD, or git status) to get the file list, then read those files plus enough surrounding context to understand them.
- For a described subsystem or "this logic about X": grep for it and follow the code.

Then build a STRUCTURAL MODEL:
- scope_manifest: the concrete files/dirs you determined are in scope.
- modules: each module/package/significant file with its single-sentence responsibility and what it depends on.
- intended_architecture: the shape the author appears to have intended — layers, boundaries, the flow. If there is no discernible intent, say so plainly (that itself is a signal).
- observations: anything structurally notable to seed deeper lenses.

Read real files. Do not invent paths. Return the structured map.`
}

function lensPrompt(lens, map, target) {
  return `You are a senior engineer doing ONE specific lens of a design-cohesion review. Your lens:

## ${lens.title}
${lens.question}

You are blind to the other lenses — stay strictly on yours.

TARGET: ${target}

STRUCTURAL MAP (already built — use it to navigate, but read the actual code before asserting anything):
${JSON.stringify(map)}

Investigate the in-scope code through your lens ONLY. Read the real files at the paths in the map. Trace relationships. For each genuine design problem your lens surfaces, emit a finding.
${GROUNDING}

Return findings (possibly empty) for YOUR lens only.`
}

function verifyPrompt(finding, map) {
  return `You are an adversarial reviewer. A design-cohesion lens produced the finding below. Your job is to REFUTE it — build the strongest possible case that the current shape is INTENTIONAL and CORRECT, and the proposed change would be neutral or harmful.

FINDING:
${JSON.stringify(finding)}

STRUCTURAL MAP (for context):
${JSON.stringify(map)}

Read the cited code yourself. Then genuinely try to defend the status quo:
- Is this package split / helper / duplication actually a deliberate seam (different layer, different rate of change, different owner, a boundary that will diverge)?
- Would the proposed reshaping introduce coupling, hurt testability, or merge things that only coincidentally look alike?
- Is the "duplicated" logic meaningfully different on close read?
- Are the cited file:line anchors real and do they actually show what the finding claims?

Default to isReal=false if you can construct a reasonable design rationale for the current shape, OR if the anchors don't hold up. Mark isReal=true only when the smell genuinely survives your best defense. If it survives, sharpen the proposed solution.

Return your verdict.`
}

function criticPrompt(map, survived, lensKeys) {
  return `You are the completeness critic for a design-cohesion review. Your job is to find what the review MISSED.

STRUCTURAL MAP:
${JSON.stringify(map)}

LENSES ALREADY RUN: ${lensKeys.join(', ')}

SURVIVING FINDINGS (after adversarial verification):
${JSON.stringify(survived.map(f => ({ title: f.title, locations: f.locations, lens: f.lens })))}

Identify gaps:
- Which files/modules in scope_manifest were NOT touched by any surviving finding? Are they genuinely clean, or unexamined?
- Which design questions does the map's intended_architecture raise that no lens asked?

For each real gap, return an area + path + the specific design question to investigate there. If coverage is genuinely complete, return complete=true with an empty gaps array. Do not invent gaps to look thorough.`
}

function gapLensPrompt(gap, map, target) {
  return `You are a senior engineer investigating a SPECIFIC design question that the first review pass may have under-covered.

AREA: ${gap.area}${gap.path ? ` (${gap.path})` : ''}
QUESTION: ${gap.question}

TARGET: ${target}
STRUCTURAL MAP:
${JSON.stringify(map)}

Read the real code in this area and answer the question with concrete findings.
${GROUNDING}

Return findings (possibly empty).`
}

function synthPrompt(map, survived, target) {
  return `You are the lead reviewer writing the final design-cohesion verdict. The lenses found and the skeptic confirmed the findings below. Your job is to turn them into a coherent design narrative — not a flat list.

TARGET: ${target}

STRUCTURAL MAP:
${JSON.stringify(map)}

CONFIRMED FINDINGS:
${JSON.stringify(survived)}

Produce:
- overall_verdict: is the target deliberate, mixed, or accreted overall, and why — one tight paragraph.
- themes: cluster the findings by subsystem. For each, a deliberate/mixed/accreted verdict and a narrative that tells the design story (why it got this way, what it costs). Carry each finding's locations and proposed_solution VERBATIM — do not soften or drop the file:line anchors.
- reshaping_plan: an ordered list of concrete reshaping steps, highest-leverage first, each with a rationale and effort. This is the "list out all the proposed solutions" the user asked for.

Keep every claim anchored to the findings' file:line. Do not introduce new findings here. Return the structured report.`
}

// ---------------------------------------------------------------------------
// Orchestration
// ---------------------------------------------------------------------------

const target = (typeof args === 'string' && args.trim())
  ? args.trim()
  : (args && args.target) ? args.target : 'the whole repository'

// 1. MAP — grounded structural anchor
phase('Map')
log(`Mapping structure of: ${target}`)
const map = await agent(mapPrompt(target), { schema: MAP_SCHEMA, phase: 'Map', label: 'map' })
if (!map) {
  return { error: 'Map phase produced no structural model; aborting.', target }
}

// 2 + 3. LENSES (blind, parallel) -> VERIFY each finding adversarially (pipeline, no barrier)
const reviewed = await pipeline(
  LENSES,
  (lens) => agent(lensPrompt(lens, map, target), {
    schema: FINDINGS_SCHEMA, phase: 'Lenses', label: `lens:${lens.key}`,
  }),
  (res, lens) => parallel(((res && res.findings) || []).map((f) => () =>
    agent(verifyPrompt({ ...f, lens: lens.key }, map), {
      schema: VERDICT_SCHEMA, phase: 'Verify', label: `verify:${lens.key}:${f.id}`,
    }).then((v) => ({ ...f, lens: lens.key, verdict: v }))
  )),
)

const survived = reviewed.flat().filter(Boolean).filter((f) => f.verdict && f.verdict.isReal)
  .map((f) => ({ ...f, proposed_solution: (f.verdict.refined_solution || f.proposed_solution) }))
log(`Lenses: ${reviewed.flat().filter(Boolean).length} candidates, ${survived.length} survived adversarial verify`)

// 4. CRITIC — one completeness pass that re-lenses uncovered areas (bounded)
phase('Critic')
const critic = await agent(criticPrompt(map, survived, LENSES.map((l) => l.key)), {
  schema: CRITIC_SCHEMA, phase: 'Critic', label: 'critic',
})
const gaps = (critic && critic.gaps) ? critic.gaps.slice(0, 6) : []
if (critic && critic.gaps && critic.gaps.length > 6) {
  log(`Critic flagged ${critic.gaps.length} gaps; investigating the top 6.`)
}
if (gaps.length) {
  const gapReviewed = await pipeline(
    gaps,
    (gap) => agent(gapLensPrompt(gap, map, target), {
      schema: FINDINGS_SCHEMA, phase: 'Lenses', label: `gap:${gap.area}`,
    }),
    (res, gap) => parallel(((res && res.findings) || []).map((f) => () =>
      agent(verifyPrompt({ ...f, lens: `gap:${gap.area}` }, map), {
        schema: VERDICT_SCHEMA, phase: 'Verify', label: `verify:gap:${f.id}`,
      }).then((v) => ({ ...f, lens: `gap:${gap.area}`, verdict: v }))
    )),
  )
  const gapSurvived = gapReviewed.flat().filter(Boolean).filter((f) => f.verdict && f.verdict.isReal)
    .map((f) => ({ ...f, proposed_solution: (f.verdict.refined_solution || f.proposed_solution) }))
  log(`Critic round: +${gapSurvived.length} findings from ${gaps.length} gap areas`)
  survived.push(...gapSurvived)
}

// 5. SYNTHESIZE — design narrative + ordered reshaping plan
phase('Synthesize')
if (!survived.length) {
  return {
    target,
    map,
    findings: [],
    report: {
      overall_verdict: 'No surviving design-cohesion findings. The target appears deliberately shaped, or any candidate smells were refuted as intentional under adversarial review.',
      themes: [],
      reshaping_plan: [],
    },
  }
}
const report = await agent(synthPrompt(map, survived, target), {
  schema: REPORT_SCHEMA, phase: 'Synthesize', label: 'synthesize',
})

return { target, map, findings: survived, report }
