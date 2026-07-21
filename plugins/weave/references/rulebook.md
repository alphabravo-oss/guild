# Weave Rulebook — how to author a really good dynamic Workflow script

This is the generation procedure `/weave:make` follows. It is distilled from the Workflow
tool contract. When you author a script, every rule here is load-bearing — a script that
violates a HARD rule will either fail to parse, fail to run, or silently waste wall-clock.

---

## 0. What a Workflow script *is*

A Workflow script is plain **JavaScript** (NOT TypeScript) that orchestrates subagents
deterministically. It runs in the background. You author it inline and the runtime drives
it. The script body runs in an async context, so `await` directly.

The runtime gives you these hooks (do not import them — they are globals):

| hook | shape | use |
|---|---|---|
| `agent(prompt, opts?)` | `Promise<string \| object>` | spawn one subagent. With `opts.schema` it returns a validated object; without, its final text as a string. Returns `null` if the user skips it. |
| `pipeline(items, ...stages)` | `Promise<any[]>` | run each item through all stages independently — **no barrier between stages**. THE DEFAULT for multi-stage work. |
| `parallel(thunks)` | `Promise<any[]>` | run thunks concurrently, **await all** (barrier). A throwing thunk resolves to `null`. |
| `log(msg)` | `void` | narrator line shown above the progress tree. |
| `phase(title)` | `void` | start a progress group; later `agent()` calls group under it. |
| `args` | `any` | the value passed as Workflow's `args` input, verbatim. |
| `budget` | `{total, spent(), remaining()}` | the turn's token target. `total` is `null` if unset. |
| `workflow(nameOrRef, args?)` | `Promise<any>` | run another saved workflow inline (one level deep only). |

`agent()` opts: `{label, phase, schema, model, isolation:'worktree', agentType}`.

---

## 1. HARD rules (a violation breaks the script)

1. **`meta` is a PURE LITERAL.** First statement must be
   `export const meta = { name, description, phases }`. No variables, function calls,
   spreads, or template interpolation *inside* the literal. Required: `name`, `description`.
   Optional: `whenToUse`, `phases`, per-phase `model`.
2. **`meta.phases` titles match `phase()` calls exactly** — same strings, or the phase gets
   its own orphan group box.
3. **No `Date.now()`, `Math.random()`, or argless `new Date()`** — they throw (they would
   break resume). Need a timestamp? Pass it via `args` and stamp after the workflow returns.
   Need variation across agents? Vary the prompt/label by index.
4. **Plain JS only.** No type annotations (`: string[]`), interfaces, or generics — they
   fail to parse. No `import`/`require`, no filesystem or Node API.
5. **`.filter(Boolean)` before consuming `parallel()` / `pipeline()` results** — a skipped
   or thrown agent becomes `null` in the array.
6. **Guard budget loops on `budget.total`.** With no target set, `remaining()` is `Infinity`
   and `while (budget.remaining() > X)` runs straight to the 1000-agent cap. Write
   `while (budget.total && budget.remaining() > 50_000)`.

## 2. STRONG rules (a violation wastes tokens or wall-clock, or degrades quality)

7. **`pipeline()` is the default; `parallel()` is a barrier — justify every barrier.**
   A barrier is correct ONLY when stage N needs cross-item context from ALL of stage N-1:
   dedup/merge across the full set, early-exit on total count, or a prompt that references
   "the other findings." It is NOT justified by "I need to flatten/map/filter first" (do
   that inside a pipeline stage) or "the stages are conceptually separate." Smell test:
   `const a = await parallel(...); const b = transform(a); const c = await parallel(b...)`
   where `transform` has no cross-item dependency → rewrite as one `pipeline()`.
8. **Use `schema` for any structured return.** Never have an agent return JSON-as-text and
   then `JSON.parse` it — pass a JSON Schema and the runtime validates + retries for you.
9. **Inside `pipeline()`/`parallel()` stages, set `opts.phase` explicitly** (and usually
   `opts.label`) — the global `phase()` state races across concurrent stages.
10. **`isolation: 'worktree'` ONLY for parallel file mutation.** It costs ~200–500ms + disk
    per agent. Read-only agents and single-writer flows do not need it.
11. **Omit `opts.model` by default** — agents inherit the resolved session model, which is
    almost always right. Only set it when you are highly confident a different tier fits.
12. **No silent caps.** If the script bounds coverage (top-N, sampling, no-retry), `log()`
    what was dropped — silent truncation reads as "covered everything" when it did not.
13. **Subagents return raw data, not prose for a human.** Their final text IS the return
    value. Prompt them accordingly ("Return …", "Emit …").
14. **Scale to the ask.** "find any bugs" → a few finders, single-vote verify. "thoroughly
    audit" / "be comprehensive" → larger finder pool, 3–5 vote adversarial pass, synthesis.

---

## 3. Archetypes — pick the topology that fits the task

Classify the user's task into one of these. Each maps to a skeleton. Compose freely; these
are starting points, not a menu to pick exactly one from.

### UNDERSTAND — map an unfamiliar system
Parallel readers, each owning a subsystem, → one synthesis agent. Barrier is justified:
the synthesizer needs all maps at once.
```js
const SUBSYSTEMS = ['auth', 'billing', 'api', 'jobs']
const maps = (await parallel(SUBSYSTEMS.map(s => () =>
  agent(`Map the ${s} subsystem: entry points, data flow, key types. Return a structured map.`,
        {label: `map:${s}`, phase: 'Map', schema: MAP_SCHEMA})))).filter(Boolean)
const summary = await agent(`Synthesize these subsystem maps into one architecture overview:\n${JSON.stringify(maps)}`,
  {phase: 'Synthesize'})
return { maps, summary }
```

### DESIGN — choose among approaches (judge panel)
Generate N independent attempts from different angles, score with parallel judges,
synthesize from the winner while grafting the best of the runners-up.
```js
const ANGLES = ['MVP-first', 'risk-first', 'user-first']
const attempts = await pipeline(ANGLES,
  a => agent(`Design an approach to "${args.task}" from a ${a} angle. Return the design + tradeoffs.`,
             {label: `design:${a}`, phase: 'Design', schema: DESIGN_SCHEMA}),
  (design, a) => agent(`Score this ${a} design for feasibility, risk, and value (0-10 each). Return scores + rationale.`,
             {label: `judge:${a}`, phase: 'Judge', schema: SCORE_SCHEMA}).then(s => ({a, design, s})))
const winner = attempts.filter(Boolean).sort((x,y) => y.s.total - x.s.total)[0]
return { winner, all: attempts.filter(Boolean) }
```

### REVIEW — find issues, then prove they are real (THE canonical shape)
Dimensions reviewed in a pipeline; each finding adversarially verified the moment its
review lands. No barrier — dimension B verifies while dimension C is still reviewing.
```js
const DIMENSIONS = [{key:'bugs', prompt:'...'}, {key:'perf', prompt:'...'}, {key:'security', prompt:'...'}]
const results = await pipeline(DIMENSIONS,
  d => agent(d.prompt, {label: `review:${d.key}`, phase: 'Review', schema: FINDINGS_SCHEMA}),
  review => parallel((review.findings || []).map(f => () =>
    agent(`Adversarially verify this finding — try to REFUTE it. Default to refuted if uncertain: ${f.title}`,
          {label: `verify:${f.id}`, phase: 'Verify', schema: VERDICT_SCHEMA}).then(v => ({...f, verdict: v})))))
const confirmed = results.flat().filter(Boolean).filter(f => f.verdict && f.verdict.isReal)
return { confirmed }
```

### RESEARCH — multi-modal sweep → deep-read → verify → synthesize
Search several different ways (each blind to the others), deep-read the hits, verify
claims, then synthesize a cited report. Verification per-claim is a pipeline; synthesis
is a final barrier.

### MIGRATE — discover sites → transform each in isolation → verify
Discover the work-list (often inline, before the workflow), then `pipeline` each site
through transform (in a `worktree`) → verify. Worktree isolation is justified here because
transforms mutate files in parallel.
```js
const verified = await pipeline(args.sites,
  site => agent(`Migrate ${site} from old API to new API. Edit in place.`,
                {label: `migrate:${site}`, phase: 'Migrate', isolation: 'worktree', schema: DIFF_SCHEMA}),
  (diff, site) => agent(`Verify the migration of ${site} compiles and preserves behavior. Return pass/fail + evidence.`,
                {label: `verify:${site}`, phase: 'Verify', schema: VERDICT_SCHEMA}).then(v => ({site, diff, v})))
```

### GENERIC / unknown size — loop-until-dry or loop-until-budget
When the work-list size is unknown, keep going until K dry rounds, or until budget runs
low. Dedup against everything seen, NOT against confirmed-only (else rejected items
reappear forever and it never converges).
```js
const seen = new Set(), confirmed = []
let dry = 0
while (dry < 2) {
  const found = (await parallel(FINDERS.map(f => () =>
    agent(f.prompt, {phase: 'Find', schema: ITEMS_SCHEMA})))).filter(Boolean).flatMap(r => r.items || [])
  const fresh = found.filter(x => !seen.has(x.key))
  if (!fresh.length) { dry++; continue }
  dry = 0; fresh.forEach(x => seen.add(x.key))
  // ...verify fresh, push survivors to confirmed...
}
```

---

## 4. Quality patterns (compose as the task warrants)

- **Adversarial verify** — N skeptics per finding, each prompted to REFUTE, default-refuted
  on uncertainty; kill on majority refute. Stops plausible-but-wrong findings.
- **Perspective-diverse verify** — when a finding can fail multiple ways, give each verifier
  a distinct lens (correctness / security / perf / does-it-reproduce) instead of N clones.
- **Loop-until-dry** — for unknown-size discovery, K consecutive empty rounds = done. Beats
  a fixed `while (count < N)` which misses the tail.
- **Multi-modal sweep** — parallel searchers each using a different angle (by-container,
  by-content, by-entity, by-time); each blind to the others.
- **Completeness critic** — a final agent asking "what's missing — modality not run, claim
  unverified, source unread?"; its output seeds the next round.

---

## 5. Schema authoring

Define schemas as plain JS objects (JSON Schema). Keep them tight — the model fills exactly
the shape you ask for. Example:
```js
const FINDINGS_SCHEMA = {
  type: 'object',
  properties: {
    findings: { type: 'array', items: {
      type: 'object',
      properties: {
        id:    { type: 'string' },
        title: { type: 'string' },
        file:  { type: 'string' },
        line:  { type: 'number' },
        severity: { type: 'string', enum: ['low','medium','high','critical'] },
      },
      required: ['id','title','severity'],
    }},
  },
  required: ['findings'],
}
```

---

## 6. The 12-point self-audit (run BEFORE saving or running)

1. First statement is `export const meta = {...}` and the literal is pure (no vars/calls/spreads).
2. Every `phase()` title appears in `meta.phases` (and vice-versa where intended).
3. No `Date.now()` / `Math.random()` / argless `new Date()`.
4. No TS syntax, no `import`/`require`, no fs/Node API.
5. Every `parallel()`/`pipeline()` result is `.filter(Boolean)`-ed before use.
6. Every barrier (`parallel()` between stages) has a real cross-item justification — else it's a `pipeline()`.
7. Every structured return uses `schema`; no `JSON.parse` of agent text.
8. Agents inside concurrent stages set `opts.phase` (and usually `opts.label`).
9. `isolation:'worktree'` appears ONLY where agents mutate files in parallel.
10. `opts.model` is omitted unless there's a clear reason; budget loops guard on `budget.total`.
11. Any coverage cap (top-N, sampling) is `log()`-ged, not silent.
12. `node --check` passes on the generated file.

Only after all 12 pass does the script get saved or run.
