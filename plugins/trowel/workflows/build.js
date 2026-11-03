export const meta = {
  name: 'trowel-build',
  description: 'trowel: preflight in code, one builder (slices only when the spec is over budget), then one code check and one spec-to-code check per round, capped fix rounds, report.',
  whenToUse: 'Launched by /trowel:build with the project root, the plugin root, the run name and the parsed flags. Not run directly.',
  phases: [
    { title: 'Preflight', detail: 'seal, clean tree, commands, size, the integration check can start here' },
    { title: 'Plan', detail: 'only when the spec is over one builder\'s budget' },
    { title: 'Build', detail: 'one builder, or one per slice, each starting once its dependencies are built' },
    { title: 'Verify', detail: 'code check and spec-to-code check, side by side' },
    { title: 'Fix', detail: 'one fixer per slice with every failure and gap' },
    { title: 'Report', detail: 'verdict per requirement, timings, agent calls' },
  ],
}

// The script has no filesystem: each code step is a `trowel` command
// (scripts/trowel.py) that a low-effort clerk agent runs and copies back.
const A = args || {}
const PLUGIN = A.plugin_root, ROOT = A.project_root, RUN = A.run, RESUME = !!A.resume
if (!PLUGIN || !ROOT || !RUN) return { status: 'error', message: 'trowel-build needs args.plugin_root, args.project_root and args.run; launch it with /trowel:build' }
let MAX_ROUNDS = Number.isInteger(A.max_rounds) && A.max_rounds >= 0 ? A.max_rounds : 2
let CAP = Number.isInteger(A.max_agents) && A.max_agents >= 4 ? A.max_agents : 40
const q = s => `'${String(s).replace(/'/g, `'\\''`)}'`
const T = `${q(`${PLUGIN}/scripts/trowel`)} --root ${q(ROOT)}`, RR = `--run ${q(RUN)}`

let RDIR = '', EPOCH = 0, BASE = '', COMMANDS = {}, INTEG = null, PLAN = null, SPLIT = false, slice = {}, R = 0
const concerns = [], timeline = [], calls = { total: 0, by: {} }
const note = m => { timeline.push(m); log(m) }, concern = m => { concerns.push(m); note(`concern: ${m}`) }
class Stop { constructor(status, message) { this.status = status; this.message = message } }
const result = (status, message, extra) => ({ status, message, run: RUN, run_dir: RDIR, agent_calls: calls, concerns, timeline, ...(extra || {}) })

// Every agent call is counted here and refused past the cap; the last call is kept for the report.
const call = async (kind, prompt, opts, last) => {
  if (calls.total >= CAP - (last ? 0 : 1)) throw new Stop('capped', `the run reached its cap of ${CAP} agent calls`)
  calls.total++; calls.by[kind] = (calls.by[kind] || 0) + 1
  return agent(prompt, opts)
}
// A builder, checker or fixer that dies does not end the run; the next code check sees what it left.
const soft = async (kind, prompt, opts) => {
  try { return await call(kind, prompt, opts) } catch (e) { if (e instanceof Stop) throw e; concern(`${opts.label} failed: ${e && e.message ? e.message : e}`); return null }
}
// parallel() turns a throw into null; all() rethrows the first Stop after the barrier.
const all = async thunks => {
  const out = await parallel(thunks.map(fn => async () => { try { return { v: await fn() } } catch (e) { return { e } } }))
  const bad = out.find(x => !x || x.e)
  if (bad) throw (bad && bad.e) || new Stop('error', 'a parallel branch returned nothing')
  return out.map(x => x.v)
}

const str = d => ({ type: 'string', description: d }), strs = d => ({ type: 'array', items: { type: 'string' }, description: d })
const obj = props => ({ type: 'object', properties: props, required: Object.keys(props) })
const LINE = obj({ output: str('the final JSON line the command printed, copied exactly') })
const BUILDER = obj({ status: { type: 'string', enum: ['complete', 'blocked', 'spec_problem'] }, commits: strs(), tests_added: strs(),
  needs_files: strs(), integration: str('the command you ran and its final exit code, or why it did not run'), decisions: strs(), notes: str() })
const GAPS = obj({ file: str('the file you wrote'), open: { type: 'array', items: obj({ id: str(), file: str(), requirement_ids: strs() }) } })
const FIXER = obj({ file: str(), fixed: strs('item ids'), disputed: strs(), blocked: strs() })

const parse = (label, r) => {
  if (!r) throw new Stop('error', `${label}: the agent returned nothing`)
  let out
  try { out = JSON.parse(String(r.output).trim()) } catch (e) { throw new Stop('error', `${label}: the command printed no JSON: ${String(r.output).slice(0, 300)}`) }
  if (out.ok === false && out.error) throw new Stop('error', `${label}: ${out.error}`)
  return out
}
const wait = 'It prints one JSON line. If that line contains "pending": true, run the exact same command again, and keep doing so until it prints a line without "pending". Put the final line, exactly as printed, in output.'
// --calls hands code the running count; a run's state exists only after init.
const counted = last => RDIR ? ` --calls ${q(JSON.stringify({ epoch: EPOCH, total: calls.total + 1, by: { ...calls.by, clerk: (calls.by.clerk || 0) + 1 }, ...(last ? { concerns } : {}) }))}` : ''
const clerk = async (label, ph, command, job, last) => parse(label, await call('clerk', `Run this one command with the Bash tool, with the tool's timeout set to 600000, and report what it prints. Do nothing else: do not edit files, fix anything or interpret the result.

    ${T}${counted(last)}${job ? ` --job ${job}-e${EPOCH}` : ''} ${command}

${wait}`, { label: `clerk:${label}`, phase: ph, schema: LINE, effort: 'low' }, last))

const ctx = () => `Project root: ${ROOT}
Run directory: ${RDIR}
Spec: ${RDIR}/spec.md; every requirement ID with its verbatim clause: ${RDIR}/spec-index.json
Base commit of the build: ${BASE}
Project commands: ${['build', 'lint', 'test'].map(k => `${k}: ${(COMMANDS[k] || []).map(c => `\`${c.cmd}\`${c.cwd ? ` in ${c.cwd}/` : ''}`).join(', ') || 'none detected'}`).join('; ')}`
const checksLine = key => `Project checks (build, lint, all tests; one agent at a time, reused when the code has not changed): ${T} --job ${key}-e${EPOCH} checks ${RR}
`
const integLine = key => checksLine(key.replace(/^integ/, 'checks')) + (INTEG ? `Integration check: \`${INTEG.command}\`${INTEG.cwd ? ` in ${INTEG.cwd}/` : ''}${INTEG.exists === false ? ' (it does not exist yet; the spec asks for it to be written)' : ''}. Run it to completion with: ${T} --job ${key}-e${EPOCH} integration ${RR}` : 'The spec names no integration check.')

const finish = async forced => {
  phase('Report')
  const rep = await clerk('report', 'Report', `report ${RR}`, '', true)
  const c = rep.counts
  const status = forced || (c['NOT MET'] || c['NOT CHECKED'] || rep.failures ? 'done_with_gaps' : 'done')
  return result(status, `${c.MET} met, ${c['NOT MET']} not met, ${c['NOT CHECKED']} not checked; ${rep.failures} code-check failures remain; ${rep.fixed} gaps fixed, ${rep.open_gaps} open; ${rep.total}; ${calls.total} agent calls this invocation`,
    { report: rep.report, counts: c, mode: PLAN && PLAN.mode, rounds: R })
}

try {
  // ------------------------------------------------------------------ Preflight
  phase('Preflight')
  const st = RESUME ? await clerk('resume', 'Preflight', `resume ${RR}`)
    : await clerk('init', 'Preflight', `init --spec ${q(A.spec || '')} ${RR} --max-rounds ${MAX_ROUNDS} --max-agents ${CAP}`, 'init')
  if (st.stop) return result('stopped', st.stop)
  RDIR = st.run_dir; EPOCH = st.epoch || 0; BASE = st.base_commit; COMMANDS = st.commands || {}; INTEG = st.integration || null
  if (RESUME) { MAX_ROUNDS = st.max_rounds; CAP = st.max_agents }
  const done = new Set(st.steps_done || ['init'])
  ;(st.warnings || []).forEach(concern)
  let blocked = st.blocked || ''
  if (!done.has('preflight') && (RESUME || st.finder)) {
    const pf = !st.finder ? await clerk('preflight', 'Preflight', `preflight ${RR}`, 'preflight')
      : parse('finder', await call('finder', `The spec at ${RDIR}/spec.md asks for an integration, end-to-end, acceptance or smoke check, and trowel could not match it to a command. Find it, cheaply: read the spec's lines about it, then how such checks run in this repository (${ROOT}): Makefile or magefile targets, package.json scripts, scripts under tests/, CI workflows. Do not run the check and do not change any file. Then run exactly one of these with the Bash tool, with the tool's timeout set to 600000:

    ${T} --job preflight-e${EPOCH} preflight ${RR} --command '<the command>' [--cwd '<directory>'] [--need '<read-only command that exits 0 only when a prerequisite is met, such as docker info>' ...]
    ${T} --job preflight-e${EPOCH} preflight ${RR} --none

Use --none only when the spec asks for no check that can be run. ${wait}`, { label: 'finder', phase: 'Preflight', schema: LINE, effort: 'low' }))
    blocked = pf.blocked; INTEG = pf.integration || null; (pf.warnings || []).forEach(concern)
  }
  if (blocked) return result('blocked', blocked)
  note(`run ${RUN} ${RESUME ? `resumed (epoch ${EPOCH})` : `started at ${BASE.slice(0, 10)}, ${st.ids} requirements`}; ${INTEG ? `integration check \`${INTEG.command}\`` : 'no integration check'}`)

  // ----------------------------------------------------------------------- Plan
  PLAN = st.plan
  if (!PLAN) {
    phase('Plan')
    let gate = null
    for (let attempt = 0; attempt <= 1 && !(gate && gate.passed); attempt++) {
      gate = parse(`plan:${attempt}`, await call('planner', `Read ${PLUGIN}/prompts/planner.md and follow it: it is your instruction. The spec did not fit one builder: ${JSON.stringify(st.measure || {})}.
${ctx()}
Write ${RDIR}/plan.json. Check it with: ${T} plan ${RR}${attempt ? `\n\nYour plan.json failed trowel's check. Fix every one of these:\n${gate.errors.map(e => `- ${e}`).join('\n')}` : ''}
When the check prints "passed": true, or you cannot make it pass, put its last JSON line, exactly as printed, in output.`, { label: `plan${attempt ? ':rev1' : ''}`, phase: 'Plan', schema: LINE }))
    }
    if (!gate.passed) return result('blocked', `the plan still failed trowel's check after one revision:\n${(gate.errors || []).map(e => `- ${e}`).join('\n')}`)
    ;[...(gate.warnings || []), ...(gate.notes || [])].forEach(concern)
    PLAN = gate.plan
  }
  SPLIT = PLAN.mode === 'split'
  slice = Object.fromEntries(PLAN.slices.map(s => [s.id, s]))
  note(SPLIT ? `${PLAN.slices.length} slices in ${PLAN.waves.length} waves` : 'one builder')

  // ------------------------------------------------- Spec checks (defined before Build: a slice is checked the moment it lands)
  // A split build checks each slice as soon as its builder finishes, while the other slices are still building, on the
  // requirements it owns; a single build checks once, in shares when the spec is large. Either way code joins the files.
  const specCheck = (rnd, recheck, k, ids, sid) => soft('spec-checker', `${recheck ? `Re-check only these gaps after fix round ${rnd - 1}: ${recheck.map(g => g.id).join(', ')}. Each is defined in ${RDIR}/checks/spec-r1.json.`
    : sid ? `Judge only the requirements slice ${sid} owns; its builder has just finished and other slices may still be building: ${ids.join(', ')}. You also own the displacement audit for this slice's files: ${slice[sid].files.join(', ')}.`
    : ids ? `Judge only these requirements, share ${k} of ${PLAN.shares.length}; other checkers judge the rest at the same time: ${ids.join(', ')}.${k === 1 ? ' You also own the displacement audit.' : ' Skip the displacement audit; share 1 owns it.'}` : 'Check every requirement in the spec against the code, in one pass.'}
${ctx()}
The code this build added or changed: git diff ${BASE}..HEAD.
Write your result to ${RDIR}/checks/spec-r${rnd}${k ? `-${k}` : ''}.json.`, { label: `spec:r${rnd}${sid ? `:${sid}` : k ? `:${k}` : ''}`, phase: 'Verify', agentType: 'trowel:spec-checker', effort: 'max', schema: GAPS })
  const checked = SPLIT ? PLAN.slices.filter(x => x.requirement_ids.length).map(x => x.id) : [], partOf = id => checked.indexOf(id) + 1
  const landed = {}, sliceCheck = id => (landed[id] = landed[id] || (!checked.includes(id) || (st.slice_checks || []).includes(partOf(id)) ? Promise.resolve(true) : specCheck(1, null, partOf(id), slice[id].requirement_ids, id)))
  const shares = SPLIT ? checked : PLAN.shares || [], firstCheck = async () => { if (shares.length < 2) return specCheck(1)
    const got = await all(shares.map((ids, i) => () => SPLIT ? sliceCheck(ids) : specCheck(1, null, i + 1, ids))), m = await clerk('merge-spec', 'Verify', `merge-spec ${RR} --parts ${shares.length}`)
    if (m.missing.length) concern(`spec check ${SPLIT ? 'of slices' : 'shares'} ${m.missing.map(k => SPLIT ? checked[k - 1] : k).join(', ')} produced nothing; their requirements are reported unchecked`)
    return got.some(Boolean) ? m : null }

  // ---------------------------------------------------------------------- Build
  phase('Build')
  const built = new Set(st.built || [])
  const job = {} // a slice starts once its own dependencies are built, not its whole wave; waves are in dependency order
  for (const id of PLAN.waves.flat()) job[id] = built.has(id) ? Promise.resolve() : Promise.all((slice[id].depends_on || []).map(d => job[d])).then(async () => {
    const r = await soft('builder', `${SPLIT ? `Build slice ${id} (${slice[id].title}). Your brief: ${slice[id].brief}. The whole spec, for context only: ${RDIR}/spec.md.`
      : `Build the whole spec: ${RDIR}/spec.md is your brief, verbatim.${slice[id].brief ? ` The planner's code anchors and patterns are in ${slice[id].brief}.` : ''}`}
${ctx()}
${integLine(`integ-${id}`)}
Commit with: ${T} commit ${RR} --as ${id} -m "<message>" [paths...]${SPLIT ? `
Other slices may be building in this tree at the same time; you own only: ${slice[id].files.join(', ')}.` : ''}`, { label: `build:${id}`, phase: 'Build', agentType: 'trowel:builder', model: 'opus', schema: BUILDER })
    if (r && r.status !== 'complete') concern(`builder of ${id} reported ${r.status}: ${[r.notes, ...(r.needs_files || [])].filter(Boolean).join('; ')}`)
    if (SPLIT) sliceCheck(id) // started, not awaited: slices that depend on this one start now
  })
  const bad = (await Promise.allSettled(Object.values(job))).find(x => x.status === 'rejected'); if (bad) throw bad.reason

  // ------------------------------------------------------------ Verify and fix
  // Code assigns each item to the slice that owns its file, then the unit its test file tests, then its directory, its requirement, its test names.
  const owns = (s, f) => !!f && s.files.some(e => e.endsWith('/') ? f.startsWith(e) : f === e), dir = f => f.replace(/\/?[^/]*$/, '')
  const unit = f => f.replace(/^.*\//, '').split('.')[0].replace(/^test_|(_test|_spec|Tests?|Spec)$/, '').toLowerCase()
  const sameDir = (s, f, u) => !!f && s.files.some(e => !e.endsWith('/') && dir(e) === dir(f) && (!u || unit(e) === unit(f)))
  const assign = items => items.reduce((by, x) => { const s = !SPLIT ? PLAN.slices[0] : PLAN.slices.find(s => owns(s, x.file)) || PLAN.slices.find(s => sameDir(s, x.file, 1)) || PLAN.slices.find(s => sameDir(s, x.file))
    || PLAN.slices.find(s => (x.requirement_ids || []).some(r => s.requirement_ids.includes(r)))
    || PLAN.slices.find(s => (x.names || []).some(n => s.files.some(e => n.includes(e.replace(/\/$/, ''))))) || PLAN.slices[PLAN.slices.length - 1]
    ;(by[s.id] = by[s.id] || []).push(x.id); return by }, {})
  const known = {} // a gap keeps the file and requirements round 1 gave it: a re-check's reply may leave them out
  const rounds = st.rounds || {}
  let open = []
  for (R = 1; ; R++) {
    const rd = rounds[R] || {}
    phase('Verify')
    const [v, s] = await all([
      () => rd.verify ? { failures: rd.verify } : clerk(`verify:r${R}`, 'Verify', `verify ${RR} --round ${R}`, `verify-r${R}`),
      () => rd.spec ? { open: rd.spec } : R > 1 && !open.length ? { open: [] } : R > 1 ? specCheck(R, open) : firstCheck(),
    ])
    const failures = v.failures || []
    if (s) open = (s.open || []).map(g => known[g.id] || (known[g.id] = g))
    else concern(`the spec check of round ${R} produced nothing; ${R === 1 ? 'requirements are reported unchecked' : 'its gaps keep their last status'}`)
    note(`round ${R}: ${failures.length} code-check failures, ${open.length} open gaps`)
    if (!failures.length && !open.length) break
    if (R > MAX_ROUNDS) { concern(`after ${MAX_ROUNDS} fix rounds these remain: ${[...failures, ...open].map(x => x.id).join(', ')}`); break }
    if (rd.fixed) continue
    phase('Fix')
    const by = assign([...failures, ...open]), free = SPLIT ? PLAN.slices.map(x => x.id).filter(id => !by[id]) : []
    await all(Object.entries(by).map(([sid, ids]) => () => soft('fixer', `Fix every one of these${SPLIT ? ` against slice ${sid}` : ''}: ${ids.join(', ')}.
Failures (V${R}-n), with the commands' output: ${RDIR}/checks/verify-r${R}.json. Gaps (G-n): ${RDIR}/checks/spec-r1.json${R > 1 ? `, with this round's re-check evidence in ${RDIR}/checks/spec-r${R}.json` : ''}.${SPLIT ? ` Your slice's brief: ${slice[sid].brief}` : ''}
${ctx()}
${integLine(`integ-fix-${sid}-r${R}`)}
Commit with: ${T} commit ${RR} --as fix:${sid}${free.length ? ` --also ${free.join(',')}` : ''} -m "<message>" [paths...]${free.length ? `
No fixer works on slices ${free.join(', ')} this round, so you may also commit their files.` : ''}
Write ${RDIR}/fixes/r${R}-${sid}.json.`, { label: `fix:${sid}:r${R}`, phase: 'Fix', agentType: 'trowel:fixer', model: 'opus', schema: FIXER })))
  }
  return await finish()
} catch (e) {
  if (e instanceof Stop && e.status === 'capped' && RDIR) { concern(e.message); return await finish('capped') }
  if (e instanceof Stop) return result(e.status, e.message)
  throw e
}
