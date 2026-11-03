// Runs plugins/trowel/workflows/build.js against a scripted stand-in for the
// Workflow runtime, its agents, and the `trowel` commands the clerk runs, so
// the script's routing is tested without a session. Adapted from joiner's
// harness (itself adapted from mason's). The trowel commands themselves are
// covered by the Python tests; here each one answers from the scenario.
//
// usage: node workflow_harness.mjs <path to build.js> <scenario>
// prints one JSON object: {meta, result, phases, labels, prompts, options, commands, schemaProblems, syntaxFile}

import { readFileSync, writeFileSync, mkdtempSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

const [, , scriptPath, scenarioName] = process.argv
const source = readFileSync(scriptPath, 'utf8')

// The runtime executes the body inside an async function; check it the same way.
const metaMatch = source.match(/^export const meta = (\{[\s\S]*?\n\})\n/m)
if (!metaMatch) throw new Error('no `export const meta = {...}` literal at the top of the script')
const meta = Function(`return (${metaMatch[1]})`)()
const body = source.replace(/^export const meta = /m, 'const meta = ')
const syntaxFile = join(mkdtempSync(join(tmpdir(), 'trowel-wf-')), 'wrapped.mjs')
writeFileSync(syntaxFile, `export default async function workflow(agent, parallel, pipeline, phase, log, args, budget, workflow) {\n${body}\n}\n`)
const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor
const fn = new AsyncFunction('agent', 'parallel', 'pipeline', 'phase', 'log', 'args', 'budget', 'workflow', body)

const schemaProblems = []
const checkSchema = (s, where) => {
  if (!s || typeof s !== 'object') return
  if (s.type === 'object') {
    const props = Object.keys(s.properties || {})
    for (const r of s.required || []) if (!props.includes(r)) schemaProblems.push(`${where}: required "${r}" not in properties`)
    for (const [k, v] of Object.entries(s.properties || {})) checkSchema(v, `${where}.${k}`)
  }
  if (s.enum && !s.enum.length) schemaProblems.push(`${where}: empty enum`)
  if (s.items) checkSchema(s.items, `${where}[]`)
}

const SINGLE = { mode: 'single', waves: [['all']], slices: [{ id: 'all', title: 'the whole spec', requirement_ids: ['FR-1', 'FR-2'], files: [], depends_on: [], brief: '' }] }
const SPLIT = { mode: 'split', waves: [['core'], ['api', 'cli']], slices: [
  { id: 'core', title: 'core', requirement_ids: ['FR-1'], files: ['core/'], depends_on: [], brief: '/b/core.md' },
  { id: 'api', title: 'api', requirement_ids: ['FR-2'], files: ['api/'], depends_on: ['core'], brief: '/b/api.md' },
  { id: 'cli', title: 'cli', requirement_ids: ['FR-2', 'FR-3'], files: ['cli/main.go'], depends_on: ['core'], brief: '/b/cli.md' }] }

// Two chains in two waves: api needs only core, late needs only slow; slow is the slowest builder.
const CHAINS = { mode: 'split', waves: [['core', 'slow'], ['api', 'late']], slices: [
  { id: 'core', title: 'core', requirement_ids: ['FR-1'], files: ['core/'], depends_on: [], brief: '/b/core.md' },
  { id: 'slow', title: 'slow', requirement_ids: ['FR-2'], files: ['slow/'], depends_on: [], brief: '/b/slow.md' },
  { id: 'api', title: 'api', requirement_ids: ['FR-3'], files: ['api/'], depends_on: ['core'], brief: '/b/api.md' },
  { id: 'late', title: 'late', requirement_ids: ['FR-4'], files: ['late/'], depends_on: ['slow'], brief: '/b/late.md' }] }

const fail = (id, extra) => ({ id, kind: 'checks', title: `failure ${id}`, names: [], file: '', ...extra })
const gap = (id, extra) => ({ id, file: 'a.go', requirement_ids: ['FR-1'], ...extra })

const SCENARIOS = {
  // One builder, nothing found: init, build, verify beside the spec check, report.
  single: {},
  // The spec over budget: the planner splits it; three slices in two waves.
  split: { plan: SPLIT },
  // The planner's first plan breaks a rule; the revision passes.
  'plan-revised': { plan: SPLIT, planErrors: [['core: writes behaviour but owns no requirement (S2)']] },
  // The plan never passes: the run stops before any builder.
  'plan-stuck': { plan: SPLIT, planErrors: [['s1 and s2 both own api/'], ['s1 and s2 both own api/']] },
  // The integration check cannot start here: stop before building.
  'preflight-stop': { initBlocked: 'The build cannot start here. a running Docker daemon: `docker info` exited 1:\nCannot connect to the Docker daemon' },
  // Detection found no command: one cheap finder names it, and preflight passes.
  finder: { finder: true },
  // The finder's command cannot start here.
  'finder-blocked': { finder: true, finderBlocked: 'The build cannot start here. mage on PATH: `command -v mage` exited 1' },
  // init refuses (dirty tree).
  'init-stop': { initStop: 'the working tree has uncommitted changes; commit or stash them first' },
  // Round 1 finds a failure and a gap; one fixer gets both; round 2 is clean.
  'find-fix': { rounds: [{ failures: [fail('V1-1')], open: [gap('G-001')] }, { failures: [], open: [] }], integration: true },
  // Only failures: round 2 runs the code check without asking the spec-checker again.
  'failures-only': { rounds: [{ failures: [fail('V1-1')], open: [] }, { failures: [], open: [] }] },
  // Nothing ever goes away: two fix rounds, a third check, then the report.
  'round-cap': { rounds: [{ failures: [fail('V1-1')], open: [gap('G-001')] }, { failures: [fail('V2-1')], open: [gap('G-001')] },
    { failures: [fail('V3-1')], open: [gap('G-001')] }] },
  // --max-rounds 0: what round 1 finds is reported, never fixed.
  'no-rounds': { rounds: [{ failures: [], open: [gap('G-001')] }], maxRounds: 0 },
  // A split's failures and gaps go to the slices that own them.
  'split-fix': { plan: SPLIT, rounds: [{ failures: [fail('V1-1', { file: 'cli/main.go' }), fail('V1-2', { names: ['github.com/x/core'] }), fail('V1-3')],
    open: [gap('G-001', { file: 'api/h.go' }), gap('G-002', { file: '', requirement_ids: ['FR-3'] })] }, { failures: [], open: [] }] },
  // Round 2's re-check names a gap without its file; it still goes to the slice round 1's file named.
  'split-recheck': { plan: { ...SPLIT, slices: SPLIT.slices.map(s => s.id === 'core' ? { ...s, files: ['core/x.go'] } : s) },
    rounds: [{ failures: [], open: [gap('G-001', { file: 'api/h.go', requirement_ids: [] }), gap('G-002', { file: 'core/x_test.go', requirement_ids: [] })] },
      { failures: [], open: [{ id: 'G-001', file: '', requirement_ids: [] }] }, { failures: [], open: [] }] },
  // The agent cap stops the run before the fixer; the report still runs.
  capped: { rounds: [{ failures: [fail('V1-1')], open: [] }], maxAgents: 5 },
  // Resumed after round 1 was checked: straight to the fixer.
  'resume-fix': { resume: { steps_done: ['init', 'preflight', 'plan', 'verify:1'], built: ['all'],
    rounds: { 1: { verify: [fail('V1-1')], spec: [gap('G-001')], fixed: false } } }, rounds: [null, { failures: [], open: [] }] },
  // Resumed with wave 1 of 2 built: only wave 2 builds.
  // A slice starts when its own dependencies finish, not when its whole wave before it has.
  chains: { plan: CHAINS, delays: { 'build:slow': 50 } },
  'resume-build': { plan: SPLIT, resume: { steps_done: ['init', 'preflight', 'plan'], built: ['core'] } },
  // A large spec: round 1 is judged in two shares side by side, joined in code, then re-checked by one checker.
  shares: { shares: [['FR-1'], ['FR-2']], rounds: [{ failures: [], open: [gap('G-001')] }, { failures: [], open: [] }] },
  // One share's checker produces nothing: its requirements are reported unchecked.
  'shares-missing': { shares: [['FR-1'], ['FR-2']], mergeMissing: [2] },
  // Resumed after the build with core's slice check already written: only api and cli are checked.
  'resume-checked': { plan: SPLIT, resume: { steps_done: ['init', 'preflight', 'plan'], built: ['core', 'api', 'cli'], slice_checks: [1] } },
  // Resumed after a preflight stop: the check is re-run before anything else.
  'resume-preflight': { resume: { steps_done: ['init', 'plan'], built: [] } },
}
const sc = SCENARIOS[scenarioName]
if (!sc) throw new Error(`unknown scenario ${scenarioName}`)
const plan = { ...(sc.plan || SINGLE), ...(sc.shares ? { shares: sc.shares } : {}) }
const integ = sc.integration ? { command: 'make e2e', cwd: '', exists: true } : null

const events = [], labels = [], phases = [], prompts = {}, options = {}, commands = []
let planner = 0

const cmdOf = prompt => {
  const line = prompt.split('\n').find(l => l.startsWith('    ')) || ''
  const m = line.match(/--root '[^']*'(?: --calls '((?:[^']|'\\'')*)')?(?: --job (\S+))? ([\w-]+)(.*)$/)
  return m ? { calls: m[1] ? JSON.parse(m[1].replace(/'\\''/g, "'")) : null, job: m[2] || '', name: m[3], rest: m[4] } : { name: '?', rest: '' }
}
const flag = (rest, f) => { const m = rest.match(new RegExp(`--${f} (\\S+)`)); return m ? m[1].replace(/'/g, '') : '' }
const round = r => (sc.rounds || [])[r - 1] || { failures: [], open: [] }

const trowel = ({ name, rest }) => {
  switch (name) {
    case 'init':
      return sc.initStop ? { ok: false, stop: sc.initStop } : { ok: true, stop: '', run: 'r', run_dir: '/p/trowel-archive/r', base_commit: 'base0000000', epoch: 0,
        ids: 2, commands: { build: [{ cmd: 'go build ./...', cwd: '' }], lint: [], test: [{ cmd: 'go test ./...', cwd: '' }] }, warnings: [],
        measure: { entries: 12, read: 3000, new: 50, fits: !sc.plan }, mode: sc.plan ? 'plan' : 'single', plan: sc.plan ? null : plan,
        finder: !!sc.finder, blocked: sc.initBlocked || '', integration: sc.finder ? undefined : integ }
    case 'resume':
      return { ok: true, stop: '', run: 'r', run_dir: '/p/trowel-archive/r', base_commit: 'base0000000', epoch: 1, max_rounds: sc.maxRounds ?? 2,
        max_agents: sc.maxAgents ?? 40, commands: {}, integration: integ, finder: false, plan, built: [], rounds: {}, ...sc.resume }
    case 'preflight':
      return { ok: true, blocked: '', integration: { command: 'make test', cwd: '' }, ran: [], warnings: [] }
    case 'verify': {
      const r = Number(flag(rest, 'round'))
      return { ok: true, round: r, head: `head-r${r}`, failures: round(r).failures, checks: [], integration: null }
    }
    case 'merge-spec':
      return { ok: true, missing: sc.mergeMissing || [], open: round(1).open }
    case 'report':
      return { ok: true, report: '/p/trowel-archive/r/report.md', counts: { MET: 2, 'NOT MET': 0, 'NOT CHECKED': 0 }, fixed: 1, open_gaps: 0, failures: 0, total: '31m00s' }
    default:
      throw new Error(`harness: no answer for trowel ${name}`)
  }
}

const agent = async (prompt, opts = {}) => {
  const label = opts.label || '(unlabelled)'
  labels.push(label)
  prompts[label] = prompt
  options[label] = { agentType: opts.agentType || '', model: opts.model || '', effort: opts.effort || '', phase: opts.phase || '' }
  if (opts.schema) checkSchema(opts.schema, label)
  if (label.startsWith('clerk:')) {
    const c = cmdOf(prompt)
    commands.push({ cmd: `${c.job ? `[${c.job}] ` : ''}${c.name}${c.rest}`.trim(), calls: c.calls })
    return { output: JSON.stringify(trowel(c)) }
  }
  if (label === 'finder') return { output: JSON.stringify(sc.finderBlocked ? { ok: true, blocked: sc.finderBlocked, integration: null }
    : { ok: true, blocked: '', integration: { command: 'mage metrics', cwd: '' }, warnings: [] }) }
  if (label.startsWith('plan')) {
    const errs = (sc.planErrors || [])[planner++]
    return { output: JSON.stringify(errs ? { ok: true, passed: false, errors: errs } : { ok: true, passed: true, errors: [], warnings: [], notes: [], plan }) }
  }
  if (label.startsWith('build:')) {
    events.push(`start:${label}`)
    await new Promise(r => setTimeout(r, (sc.delays || {})[label] || 0))
    events.push(`end:${label}`)
    return { status: 'complete', commits: ['abc'], tests_added: [], needs_files: [], integration: 'make e2e: 0', decisions: [], notes: '' }
  }
  if (label.startsWith('spec:')) return { file: 's.json', open: round(Number(label.split(':r')[1])).open }
  if (label.startsWith('fix:')) return { file: 'f.json', fixed: [], disputed: [], blocked: [] }
  return { output: '{}' }
}
const parallel = async thunks => Promise.all(thunks.map(t => t().catch(() => null)))
const pipeline = async () => { throw new Error('pipeline is not used by this script') }
const phase = t => phases.push(t)
const log = () => {}

const result = await fn(agent, parallel, pipeline, phase, log, {
  plugin_root: '/plugin', project_root: '/p', spec: 'docs/spec.md', run: 'r',
  ...(sc.maxRounds !== undefined ? { max_rounds: sc.maxRounds } : {}),
  ...(sc.maxAgents !== undefined ? { max_agents: sc.maxAgents } : {}),
  resume: sc.resume ? 'r' : '',
}, { total: null, spent: () => 0, remaining: () => Infinity }, async () => { throw new Error('nested workflows unused') })

process.stdout.write(JSON.stringify({ meta, result, phases, events, labels, prompts, options, commands, schemaProblems, syntaxFile }))
