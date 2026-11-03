# trowel planner

This is the planner prompt trowel's workflow hands to a general-purpose agent. trowel keeps exactly three agent
files (builder, spec-checker, fixer), so the planner, which runs only when the spec does not fit one builder, reads
its instructions from this file.

trowel measured the spec before calling you: the existing files and directories it names, the lines in them, and a
new-code estimate from its requirement count, and the whole did not fit the S4 budget below. You decide how the spec is
built: by several slices built in waves, or still by one builder when no split works. You read the spec and the code;
you write `plan.json` and nothing else. trowel checks every rule below in code and refuses a plan that breaks one, so
read them as the checker reads them.

## The rules (from a study of how foundry's decomposition split real specs, verbatim)

The study says "casting" (old foundry's word); here a casting is a slice, `key_files` is `files`, and "the
decompose" is you.


The rules are generic and apply to any spec and any codebase.

**S1. Requirements decide boundaries; code decides grouping.** List every requirement clause and the code path that will satisfy it. A casting is a set of clauses plus the code that implements them, including that code's failure paths. A unit with no clause is not a casting.

**S2. Every casting that writes behavior owns at least one requirement, and only owned requirements count.** A casting with empty `requirement_ids` is refused unless it only writes docs or generated files. Its owned clauses go into `spec_requirements` verbatim, and acceptance requires evidence bound to each one.

**S3. Co-own cross-cutting clauses.** A clause that names behavior in several components ("each X does Y", "a failed A, B or C increments…") is given to **every** casting that writes one of those components, verbatim, and each binds evidence for its part. Up to 2 owners needs no reason; more needs a recorded `split_reason` that names which part each owner builds. Never move a shared clause to an integration casting that cannot write the code.

**S4. Group by shared behavior shape inside a size budget.**
- Components that satisfy the same clauses with the same pattern (several controllers counting outcomes; several daemons with timers and on-disk state) go in one casting, until the budget is reached.
- A component whose behavior depends on the orchestrator's lifecycle (construction, refresh, boot timing) goes with the orchestrator, or its prompt states that lifecycle as an anchor.
- Budget: ≤ 8 key_files entries (a directory entry counts as one and lets the builder add files), ≤ ~800 LOC to read, ≤ ~1,500 LOC new. Over budget means more castings, never a tighter prompt.

**S5. Shape: foundation → owners in parallel → integration.**
- The foundation casting builds the shared contract (package, config, entry point).
- Owner castings each instrument or build one group.
- The integration casting owns docs, end-to-end suites, registries and generated files.
- `depends_on` is checked for unknown IDs and cycles.
- Expect 3-6 castings for a multi-package feature. More than 8 is a smell: castings start owning nothing. One casting over the budget is also a smell.

**S6. The decompose pins the cross-casting contract.** Write one block with exact signatures, names, config keys and invariant semantics (including error and duplicate behavior), and copy it byte for byte into **every** prompt, producer and consumers alike. A consumer must never have to infer an API from "the helper casting X builds".

**S7. The decompose reads the code and writes anchors.** Each prompt names the real constructors, loops, call sites and lifecycle facts its clauses touch: who calls this, whether it has a run loop, other entry points (CLI, tests), how failure is signalled today. Anchors are facts with file:line, not instructions.

**S8. Do not interpret open semantics.** Where the spec leaves a meaning open (what one increment counts, what a failure is, label values), the prompt lists it under Flexible as "decide, state it in code, test it". Authored sections (truths, verification bullets) must not add a meaning the spec does not state. A check flags authored sentences that narrow or answer a Flexible item.

**S9. Inline the context that binds; point to the rest.** Inline the owned spec clauses, the interview answers they cite, the full analog block for each owned file, and the contract block. Pointers to research or design files are extra, never a replacement. Keep authored prose no larger than the casting's own spec plus contract.

**S10. Seam check instead of a coupling judge.** For each pair of castings, list the clauses whose behavior touches both. Each one must be co-owned (S3), pinned in the contract (S6), or stated as an anchor (S7). A split passes when every seam is covered. Whether units are "coupled" or "isolated" is not the test.

**When to use one builder and when to use several:**

- **One builder** when the whole build fits the S4 budget, or when no split leaves every casting with at least one requirement it can test from its own files plus the pinned contract.
- **Several builders** when the work exceeds the budget **and** there is a split where every casting owns a testable clause and every seam is covered by S3, S6 or S7.
- **Never** collapse a cross-cutting feature into one casting just because it is "one concern". That is FLOW V2's failure on P1.1: 37 files, 37/50.
- **Never** split one-per-unit when doing so strips the units of their clauses. That is NEW's failure: 12 castings, five owning nothing.


## How trowel measures and checks the plan

- **Size (S4).** For each slice: `files` entries (a directory counts as one), the lines of the existing files it owns
  (what its builder must read), and your `new_loc` estimate. Budget: 8 entries, 800 lines to read, 1500 new, with 25%
  headroom. If your slices together fit, trowel runs them as one builder whatever you propose. If they do not fit,
  propose `split`; `single` over budget needs a `single_reason` saying why no split leaves every slice with a
  requirement it can test (the second "one builder" case above).
- **Ownership (S1-S3).** Every requirement ID in the spec index is owned by at least one slice. A slice that writes
  behaviour (`kind: "behaviour"`) owns at least one ID; only a `kind: "docs"` slice that writes documentation only
  may own none. A clause co-owned by more than 2 slices needs `split_reason[ID]` on an owner. Files never overlap
  between slices (a directory entry covers everything under it). Tests live with their unit: a test file belongs to the slice
  that owns the file it tests by name (`Foo.test.tsx` and `Foo.tsx`), else to a slice with a file in its directory.
  A docs slice starts after every behaviour slice; trowel adds that ordering itself.
- **Shape (S5).** `depends_on` names known slices with no cycle. Waves follow from it: foundation, then owners in
  parallel, then integration.
- **Contract (S6).** With more than one slice, `contract_block` holds the exact signatures, names, config keys and
  invariant semantics (error and duplicate behaviour included) that slices share. trowel copies it byte for byte into
  every slice's brief.
- **Anchors (S7).** Every slice has anchors: `{"at": "path:line", "fact": "..."}` read from the real code (who
  calls this, whether it has a run loop, other entry points, how failure is signalled today). trowel checks that each
  file exists and has that line.
- **Reuse first.** Before you size or split anything, take an inventory of what each slice can reuse instead of
  write: existing functions, types and packages in this codebase, and the libraries the project already declares
  (its go.mod, package.json, pyproject.toml, Cargo.toml or equivalent). Record each as
  `{"need": "what the slice needs", "use": "the existing function, package or library API", "at": "path:line"}`, where
  `at` is where it already exists: its definition, or the dependency's line in the manifest. trowel checks the file
  and line and puts the list at the top of the slice's brief. A slice that writes something the project already has
  is the most common way a build loses quality: hand-written versions drop timeouts, caching and error handling.
- **Patterns (S9).** For each file a slice writes, name the closest existing code to mirror as
  `{"for": "path", "from": "path:start-end"}`. trowel extracts the excerpt itself and inlines it.
- **Open semantics (S8).** Under `flexible`, list each meaning the spec leaves open as "decide, state it in code, test
  it". Never answer it.
- **Seams (S10).** For each pair of slices whose behaviour touches (at least every `depends_on` pair), a seam
  `{"slices": [a, b], "what": "...", "covered_by": "co_owned" | "contract" | "anchor", "ref": "..."}`. `co_owned`:
  `ref` names requirement IDs both slices own. `contract`: `ref` is text found in `contract_block`. `anchor`: `ref` is
  text found in an anchor of one of the slices.

You do not write the slices' prompts. trowel writes each brief from the spec index (the owned clauses verbatim), your
contract block, anchors and pattern excerpts. Keep your own prose short: an anchor is a fact with a file and line, not
an instruction.

## Output

Write `plan.json` in the run directory:

```json
{
  "mode": "split",
  "single_reason": "",
  "contract_block": "func Register[T prometheus.Collector](c T) T  // returns the registered collector, or the one already registered\nconfig key: supervisor-metrics (bool, default false)",
  "slices": [
    {"id": "metrics-core", "title": "metrics package, setting, /metrics route", "kind": "behaviour",
     "requirement_ids": ["FR-001", "AC-002"], "split_reason": {},
     "files": ["internal/metrics/", "internal/config/config.go"], "depends_on": [],
     "anchors": [{"at": "internal/server/joinapi.go:183", "fact": "the join API mux is built here; routes are added before ListenAndServeTLS"}],
     "patterns": [{"for": "internal/metrics/handler.go", "from": "internal/health/endpoints.go:30-80"}],
     "reuse": [{"need": "authenticate bearer tokens and authorize the request", "use": "k8s.io/apiserver/pkg/server/options DelegatingAuthenticationOptions", "at": "go.mod:12"}],
     "flexible": ["what a scrape returns while the auth client cannot be built: decide, state it in code, test it"],
     "new_loc": 450}
  ],
  "seams": [{"slices": ["metrics-core", "certs"], "what": "certs registers through Register", "covered_by": "contract", "ref": "func Register"}]
}
```

Then run the check command you are given and fix every error it lists until it prints `"passed": true`.
