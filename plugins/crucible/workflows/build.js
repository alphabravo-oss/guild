export const meta = {
  name: 'crucible-build',
  description: 'Mini-foundry build-verify-fix engine. Takes a structured spec and a pre-approved decomposition into units with disjoint file ownership, then: CAST (parallel deliberate-engineering builders, each handed its unit prompt verbatim, each emitting re-executable evidence bound to requirement IDs) -> INSPECT (three blind orthogonal streams — wiring exists/substantive/wired, spec-before-code fresh-eyes assay, and re-execution of every evidence command) -> GRIND (every non-passing verdict is a defect; fix the affected units and full-re-inspect, loop until zero defects or a cycle cap) -> ASSAY (fresh-eyes agents read the spec first, form expectations, then confirm each requirement). The engine routes and verifies; it never re-narrates the spec or judges the code itself. Edits the shared working tree under disjoint ownership; does not commit (the command does, per-unit, on user say-so).',
  whenToUse: 'Driven by /crucible:build after the command decomposes the spec into units and the user approves the split. Not run directly.',
  phases: [
    { title: 'Cast' },
    { title: 'Inspect' },
    { title: 'Grind' },
    { title: 'Assay' },
  ],
}

// ---------------------------------------------------------------------------
// Inputs
// ---------------------------------------------------------------------------
const specPath = args?.specPath
const units = Array.isArray(args?.units) ? args.units : null
const maxGrind = Number.isInteger(args?.maxGrindCycles) ? args.maxGrindCycles : 3
if (!specPath || !units || !units.length) {
  return { error: 'crucible-build requires args.specPath and a non-empty args.units array' }
}

const GROUND = `
SPEC: read it in full yourself at ${specPath} — do not trust any summary of it.
The spec is the source of truth. Requirements are identified by stable IDs (e.g. FR-1, US-2, AC-3).
Every unit owns a DISJOINT set of files; never touch a file outside the unit you are given.`

const chunk = (arr, n) => {
  const out = []
  for (let i = 0; i < arr.length; i += n) out.push(arr.slice(i, i + n))
  return out
}
const allReqIds = Array.from(new Set(units.flatMap(u => u.requirement_ids || []))).sort()

// ---------------------------------------------------------------------------
// Schemas
// ---------------------------------------------------------------------------
const CAST_SCHEMA = {
  type: 'object',
  properties: {
    unit_id: { type: 'string' },
    files_written: { type: 'array', items: { type: 'string' } },
    evidence: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          cmd: { type: 'string', description: 'A real, deterministic shell command that demonstrates the behavior end-to-end.' },
          for_ids: { type: 'array', items: { type: 'string' }, description: 'Requirement IDs this command proves.' },
          output: { type: 'string', description: 'The actual stdout/stderr from running cmd.' },
        },
        required: ['cmd', 'for_ids', 'output'],
      },
    },
    concerns: { type: 'array', items: { type: 'string' }, description: 'Architectural issues found but out of this unit\'s scope.' },
  },
  required: ['unit_id', 'files_written', 'evidence'],
}

const DEFECTS_SCHEMA = {
  type: 'object',
  properties: {
    defects: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          requirement_id: { type: 'string', description: 'The spec requirement this defect blocks, or "" if structural.' },
          unit_id: { type: 'string', description: 'The unit that owns the file(s) at fault, best guess.' },
          verdict: { type: 'string', enum: ['HOLLOW', 'THIN', 'PARTIAL', 'MISSING', 'WRONG', 'MISPLACED', 'UNWIRED', 'EVIDENCE_MISMATCH'] },
          location: { type: 'string', description: 'file:line of the fault.' },
          detail: { type: 'string', description: 'One line: the gap between what the spec required and what the code does.' },
        },
        required: ['verdict', 'location', 'detail'],
      },
    },
  },
  required: ['defects'],
}

const ASSAY_SCHEMA = {
  type: 'object',
  properties: {
    results: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          requirement_id: { type: 'string' },
          verdict: { type: 'string', enum: ['VERIFIED', 'HOLLOW', 'THIN', 'PARTIAL', 'MISSING', 'WRONG', 'MISPLACED'] },
          evidence: { type: 'string', description: 'Spec expectation + file:line + what the code actually does.' },
        },
        required: ['requirement_id', 'verdict', 'evidence'],
      },
    },
  },
  required: ['results'],
}

const REPORT_SCHEMA = {
  type: 'object',
  properties: {
    overall: { type: 'string', description: 'One paragraph: did the build deliver the spec, and what (if anything) is unresolved.' },
    requirements_matrix: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          requirement_id: { type: 'string' },
          verdict: { type: 'string', enum: ['VERIFIED', 'HOLLOW', 'THIN', 'PARTIAL', 'MISSING', 'WRONG', 'MISPLACED'] },
          note: { type: 'string' },
        },
        required: ['requirement_id', 'verdict'],
      },
    },
    unresolved_defects: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          requirement_id: { type: 'string' },
          verdict: { type: 'string' },
          location: { type: 'string' },
          detail: { type: 'string' },
        },
        required: ['verdict', 'location', 'detail'],
      },
    },
  },
  required: ['overall', 'requirements_matrix'],
}

// ---------------------------------------------------------------------------
// Prompts
// ---------------------------------------------------------------------------
const unitBlock = u => `
UNIT ${u.id}: ${u.title}
Requirement IDs you own: ${(u.requirement_ids || []).join(', ')}
Files you may write (and ONLY these): ${(u.key_files || []).join(', ')}

Spec text for your requirements (verbatim — implement exactly this):
${u.spec_excerpt || '(see the spec file for the listed requirement IDs)'}

Acceptance criteria:
${(u.acceptance_criteria || []).map(a => `- ${a}`).join('\n') || '(derive from the requirements above)'}

Pattern to mirror: ${u.analog_hint || '(find the nearest sibling in the codebase yourself)'}`

const castPrompt = u => `${GROUND}\n\nYou are building one unit. ${unitBlock(u)}\n\nFollow your deliberate-engineering procedure (Read Floor, Approach Deliberation, Blast Radius). Build\nreal code, not stubs. Produce re-executable evidence for every requirement ID, bound via for_ids. Write\nONLY your key_files. Return the structured result.`

const grindPrompt = (u, ds) => `${GROUND}\n\nYou are FIXING defects in unit ${u.id}. ${unitBlock(u)}\n\n## Defects to fix this cycle (from blind verification):\n${ds.map(d => `- [${d.verdict}] ${d.location} — ${d.detail}${d.requirement_id ? ` (req ${d.requirement_id})` : ''}`).join('\n')}\n\nFix the root cause, not the symptom. Re-run your self-check and refresh the evidence commands. Write\nONLY your key_files. Return the structured result.`

// ---------------------------------------------------------------------------
// Inspect — three blind orthogonal streams, run in parallel. Returns deduped defects.
// (A barrier is correct here: inspect needs ALL units built; grind needs ALL defects.)
// ---------------------------------------------------------------------------
const runInspect = async (builds, cycleLabel) => {
  const evidence = builds.flatMap(b => (b.evidence || []).map(e => ({ ...e, unit_id: b.unit_id })))
  const reqGroups = chunk(allReqIds, 5)

  const streams = await parallel([
    // Stream 1 — wiring: every declared symbol EXISTS, is SUBSTANTIVE (not a stub), is WIRED (called).
    () => agent(
      `${GROUND}\n\nYou are the WIRING stream (read-only; do not edit). For every symbol the spec requires\nacross these units (${units.map(u => u.id).join(', ')}), verify in the actual code: (1) it EXISTS, (2) it is\nSUBSTANTIVE — a real body, not an empty/stub/hardcoded-return, (3) it is WIRED — actually called from\nits expected entry point (grep for callers). Report only failures as defects (verdict UNWIRED for not-\ncalled, MISSING for absent, HOLLOW for stub). Ground every defect in file:line.`,
      { agentType: 'Explore', schema: DEFECTS_SCHEMA, label: `inspect:wiring:${cycleLabel}`, phase: 'Inspect' }
    ).then(r => (r && r.defects) || []),

    // Stream 2 — spec-before-code fresh-eyes assay, split across requirement groups.
    ...reqGroups.map((g, i) => () => agent(
      `${GROUND}\n\nVerify ONLY these requirement IDs: ${g.join(', ')}.\nFollow your spec-before-code procedure: read the spec for each ID and write the observable truths FIRST,\nthen read the code and judge it. Assume broken until proven. Report every non-VERIFIED as a defect with\nfile:line and the spec-vs-code gap. Map each to the unit that owns the file when you can.`,
      { agentType: 'crucible:assayer', schema: DEFECTS_SCHEMA, label: `inspect:assay:${cycleLabel}:${i}`, phase: 'Inspect' }
    ).then(r => (r && r.defects) || [])),

    // Stream 3 — evidence re-execution: re-run every evidence command and diff its output.
    () => agent(
      `${GROUND}\n\nYou are the EVIDENCE stream (read-only on source; you may RUN commands). Re-execute each evidence\ncommand below in the repo and compare its fresh output to the claimed output. Ignore volatile fields\n(timestamps, ports, uuids, durations). If a command fails, errors, or its output materially contradicts\nthe claim, emit a defect (verdict EVIDENCE_MISMATCH) citing the command and what differed. "Compiles" is\nnot proof — a passing build with a failing evidence command is still a defect.\n\nEVIDENCE:\n${JSON.stringify(evidence, null, 2)}`,
      { agentType: 'Explore', schema: DEFECTS_SCHEMA, label: `inspect:evidence:${cycleLabel}`, phase: 'Inspect' }
    ).then(r => (r && r.defects) || []),
  ])

  // Flatten + dedupe by location+verdict so the same fault from two streams counts once.
  const seen = new Set()
  const defects = []
  for (const d of streams.filter(Boolean).flat()) {
    const key = `${d.verdict}@${d.location}`
    if (seen.has(key)) continue
    seen.add(key)
    defects.push(d)
  }
  return defects
}

const unitById = id => units.find(u => u.id === id)
const ownerOf = d => {
  if (d.unit_id && unitById(d.unit_id)) return d.unit_id
  // best-effort: match the defect location's file to a unit's key_files
  const loc = (d.location || '').split(':')[0]
  const owner = units.find(u => (u.key_files || []).some(f => loc && (f === loc || loc.endsWith(f) || f.endsWith(loc))))
  return owner ? owner.id : units[0].id
}

// ---------------------------------------------------------------------------
// Phase: Cast
// ---------------------------------------------------------------------------
phase('Cast')
const builds = (await parallel(
  units.map(u => () => agent(castPrompt(u), { agentType: 'crucible:teammate', schema: CAST_SCHEMA, label: `cast:${u.id}`, phase: 'Cast' }))
)).filter(Boolean)

// ---------------------------------------------------------------------------
// Phase: Inspect + Grind (loop until zero defects or cycle cap)
// ---------------------------------------------------------------------------
phase('Inspect')
let defects = await runInspect(builds, 'c0')
let cycle = 0
while (defects.length && cycle < maxGrind) {
  cycle++
  phase('Grind')
  log(`Grind cycle ${cycle}/${maxGrind}: ${defects.length} defect(s) to fix`)
  // group defects by owning unit, fix affected units in parallel (disjoint files → safe)
  const byUnit = {}
  for (const d of defects) (byUnit[ownerOf(d)] ||= []).push(d)
  await parallel(Object.entries(byUnit).map(([uid, ds]) => () =>
    agent(grindPrompt(unitById(uid), ds), { agentType: 'crucible:teammate', schema: CAST_SCHEMA, label: `grind:${cycle}:${uid}`, phase: 'Grind' })
  ))
  phase('Inspect')
  defects = await runInspect(builds, `c${cycle}`)
}
if (defects.length) log(`Grind cap reached with ${defects.length} defect(s) unresolved — reporting them, not hiding them.`)

// ---------------------------------------------------------------------------
// Phase: Assay (fresh-eyes confirmation of every requirement)
// ---------------------------------------------------------------------------
phase('Assay')
const assayGroups = chunk(allReqIds, 5)
const assay = (await parallel(assayGroups.map((g, i) => () =>
  agent(
    `${GROUND}\n\nFINAL ASSAY. Confirm ONLY these requirement IDs: ${g.join(', ')}.\nRead the spec for each FIRST and form observable truths, THEN read the code. Assume broken until proven.\nReturn a verdict per requirement with file:line evidence. VERIFIED must be earned.`,
    { agentType: 'crucible:assayer', schema: ASSAY_SCHEMA, label: `assay:${i}`, phase: 'Assay' }
  ).then(r => (r && r.results) || [])
))).filter(Boolean).flat()

// ---------------------------------------------------------------------------
// Synthesize
// ---------------------------------------------------------------------------
const report = await agent(
  `${GROUND}\n\nYou are the SYNTHESIS pass. Produce the final build report from these inputs — do not re-judge the\ncode yourself, just consolidate.\n\nASSAY RESULTS (fresh-eyes per requirement):\n${JSON.stringify(assay, null, 2)}\n\nUNRESOLVED DEFECTS after grind (${defects.length}):\n${JSON.stringify(defects, null, 2)}\n\nBuilds: ${JSON.stringify(builds.map(b => ({ unit_id: b.unit_id, files: b.files_written, concerns: b.concerns || [] })), null, 2)}\n\nBuild the requirements_matrix from the assay results (one row per requirement ID: ${allReqIds.join(', ')}),\ncarry the unresolved defects through verbatim, and write a one-paragraph overall verdict that is honest\nabout anything not VERIFIED. Do not mark anything VERIFIED that the assay did not.`,
  { label: 'synthesize', schema: REPORT_SCHEMA }
)

return {
  specPath,
  units: units.map(u => ({ id: u.id, title: u.title, key_files: u.key_files, requirement_ids: u.requirement_ids })),
  builds,
  grind_cycles: cycle,
  unresolved_defects: defects,
  assay,
  report: report || { overall: 'Synthesis failed; raw assay + defects attached.', requirements_matrix: [], unresolved_defects: defects },
}
