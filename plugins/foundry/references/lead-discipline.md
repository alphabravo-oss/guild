# Foundry Lead Discipline — Rationale

**Who reads this:** Anyone debugging Foundry behavior, or the Lead ONCE when a rule violation is detected. The Lead does NOT re-read this every phase. This file is rationale — the mechanical rules live in `commands/start.md`.

## Why the "router not interpreter" architecture

**The failure mode.** When the lead drafts teammate prompts from the casting manifest, the lead becomes an *interpreter* between the spec and the teammate. That interpretation layer is where spec fidelity silently erodes: scope cuts, hedge language, "pick the core coverage" permissions. Each translation is lossy; multiplicative decay across layers turns a 90%-faithful spec into a 59%-faithful build.

**The fix.** Eliminate the interpretation layer. Decompose authors the complete teammate prompt ONCE, from the spec as source of truth, at F0.5. The prompt is saved to disk, validated at F0.9, frozen. The lead at F1/F3 calls `Foundry-Spawn-Teammate` or `Foundry-Cast-Wave`, which return a `dispatch` block naming that frozen file's path and its sha256 — never its text. The lead passes the `dispatch` block to the Agent tool verbatim and the teammate reads the file itself, so the prompt reaches the builder without ever passing through the lead. Plans are prompts.

## Why CORRECTNESS BEATS CONTEXT BUDGET

If a casting is "too large for one teammate's context," that is a DECOMPOSITION failure, not a license to cut scope. Split the casting into smaller ones, run more waves, or split work across more teammates with non-overlapping file boundaries. NEVER instruct a teammate to skip subtests, drop edge cases, defer coverage, cut to "core cases," or let the user validate the rest manually. Those are forbidden and F0.9 VALIDATE will reject any casting prompt that contains them.

## Why strict interpretation is the default

When the spec contains ambiguous wording ("equivalent coverage", "similar to legacy", "roughly like X", "core cases", "mostly"), always pick the STRICTER interpretation. "User will validate equivalence manually" means "equivalence must already be there for the user to validate," NOT "partial is fine for now." If you cannot resolve an ambiguity with the strict reading, flag it in state.json as `SPEC_AMBIGUOUS` and proceed with strict reading. Autonomous runs never downgrade strictness as a convenience.

## Why verbatim prompts, no lead authoring

When spawning a teammate, pass the `dispatch` block from `Foundry-Spawn-Teammate` or `Foundry-Cast-Wave` verbatim to the Agent tool, together with the `progress_protocol` block that comes back beside it. Never prompt text: the `prompt` field comes back `null` unless you pass `full_prompt=true`, which exists for debugging and nothing else, so a lead who reaches for that field hands the agent an empty prompt. You MAY NOT modify, summarize, paraphrase, prepend, append, substitute, or wrap either block, and you MAY NOT re-type or reconstruct the prompt the file holds. GRIND is the only exception: you may append a clearly-delimited `## Defects to fix this cycle:` block after the dispatch, never inside it.

Any lead-authored text in the teammate prompt is a vector for spec drift. By mechanically forbidding any lead authoring at F1, we eliminate the drift surface entirely. If something is missing from the prompt, the fix is to re-run F0.5 DECOMPOSE with a correction, not to inject text here.

If you find yourself wanting to "just add a note" or "clarify scope" in a teammate prompt — STOP. That instinct is the exact failure mode this architecture prevents. The correct response is to re-run F0.5 DECOMPOSE with the clarification as an update to the spec or the casting's `<spec_requirements>` block.

## Why dispatch is a pointer

**The failure mode.** Handing the teammate its prompt as TEXT makes every dispatch a copy, and a copy is a thing that can be edited. The verbatim rule above is the only thing standing between the returned prompt and a lead who trims it, and that rule is enforced by nothing but the lead's own discipline — there is no artifact afterwards that shows what was actually delivered. A prompt paraphrased in the dispatch is indistinguishable, at acceptance time, from one delivered whole. Worse, the same text is then carried twice through the run's context for no gain: once out of the tool, once into the agent.

**The fix.** Dispatch a pointer instead. `Foundry-Spawn-Teammate` and `Foundry-Cast-Wave` return the prompt's path and its sha256; the agent reads the file itself and states the hash it read in its completion report; `Foundry-Accept-Casting` and `Foundry-Fix` refuse when that hash differs from the file's. Only an agent that actually read the file can produce the right answer, so "the prompt arrived intact" stops being a discipline and becomes a checked fact. `full_prompt=true` still returns the text for debugging, which is the only thing it is for.

## Why the lead-fix lane is bounded

**The failure mode.** A run whose remaining defects are all one-line changes still pays a full teammate spawn per defect — team creation, a dispatch, a read of the whole casting prompt, a build, a commit, a shutdown — to change one line. That cost is what makes a lead start fixing things directly near the end of a long run, and the previous run ended exactly that way: the user told the lead to fix the last defects itself. Nothing recorded which defects those were, who authored them, how large the changes were, or whether any test covered them. An unbounded, unaudited lane is not a shortcut around the delegation rule; it is the delegation rule quietly ceasing to exist at the moment the run is most tired.

**The fix.** Give the lane an explicit boundary and let the SERVER hold it. `Foundry-Fix` requires `authored_by` on every fix and `fix_commit` on every lead fix, runs `git show --numstat` on that commit to measure a `LIVE` lead fix against the limits, and refuses naming the count that overran. The server — not the lead — then writes the `lead_fix` handoff record, and the F6 report lists every one. The lane exists so the cheap fixes stay cheap; the measurement exists so the lead's own fixes are exactly as auditable as a teammate's, which is the property that was actually missing.

This file is rationale. The mechanical rule — which tier, how many files, how many lines, which arguments every lead fix carries — is lead rule 2 in `commands/start.md`, and the numbers there are the numbers the server measures against.

## Why a named backlog is a successful end

**The failure mode.** A lead that believes `DONE` is the only acceptable ending will grind cycles against a target it has already met. The previous run is the shape of it: TEMPER did not converge, and the response was more cycles rather than a ruling, because nothing on the surface the lead read before every call said what the run was heading for or what the backlog already was. The same belief bends the streams. If a clean PROVE is the goal, then a PROVE that keeps finding things is a problem to be managed, and the cheapest way to manage it is to narrow what PROVE looks at until it stops finding them — at which point the run has bought its ending by deleting the verification that was earning it.

**The fix.** Say what the run is heading for, on the surface the lead already reads. Every `Foundry-Next` response carries `heading_for`, `open_by_tier` and `cycles_to_cap`, so the backlog and the distance to the cap are visible before every call rather than in a report read once at the end; `Foundry-Phase(phase='halt', reason, text)` makes ending on a ruling an ordinary successful transition; and the F6 report has named sections — `latent_backlog`, `hardening_backlog`, `unknown_tier_defects` — whose whole job is to carry open work forward under its own name. **A run that reaches `HALTED` with every open finding written down and tiered has succeeded.** It stopped with work outstanding, which is a different thing from stopping without knowing what was outstanding. And an empty PROVE is not the goal: adversarial verification is mandatory on every run, so a stream that finds nothing because it was narrowed has not verified anything — it has only stopped reporting.

This file is rationale. The mechanical rule — the halt door, its four-member reason vocabulary, and the three fields every guidance response carries — is in `commands/start.md`, and the section names the done gate refuses by are the rows it lists.

## Why a self-hosting run carries a residual risk

**The failure mode.** On a run whose target is the verifier itself, a GRIND fix changes a gate — and every verdict an earlier cycle recorded THROUGH that gate was recorded by a gate that no longer exists. Those verdicts are not re-derived and nothing marks them stale, so the run carries forward conclusions produced by code it has since replaced. It is not a hypothetical: this run's own castings move the gates, the vocabularies and the stream contracts, and each of them invalidates some earlier cycle's evidence about itself.

**The fix.** There is not one, and recording that is the deliverable. The only thing standing against it is width: a GRIND whose diff touched `vocab.py`, anything under `schemas/`, gate or orchestrator code, agent or skill prose or the spec closes into a `FULL` INSPECT under the `verifier_touched` rule, so the NEXT cycle re-runs every stream over the whole corpus instead of a delta. That re-verifies FORWARD. It does not re-verify the cycles already banked, and nothing does. **Nothing mitigates this beyond `FULL` width on `verifier_touched`, and that is an accepted residual risk of self-hosting rather than a gap someone is going to close later.** Read it as a bound on what a self-targeting run's early verdicts are worth, not as a task.

This file is rationale. The mechanical rule — which GRIND diffs force `FULL` width, and which rule label the transition records beside the mode — is the INSPECT-width paragraph in `commands/start.md`.

## Why no worktrees

Teammates work in the main directory, no `isolation: "worktree"` when spawning agents. Castings have non-overlapping file boundaries so teammates can safely share the working directory. Worktree lifecycle + merge-back adds complexity with no benefit when file ownership is already disjoint.

## Why commits are pathspec-scoped

**The gap in "file ownership is already disjoint."** Disjoint ownership makes the *working tree* safe to share — two teammates never edit the same file. It does not make the *index* safe to share, and the index is shared too. A `git commit` with no pathspec commits the entire index by git's documented default. So a teammate who stages three files of their own and then runs a bare `git commit` publishes every path any peer has staged at that moment, under their own message. Disjoint file ownership does not prevent this; it is orthogonal to it. The commit boundary and the ownership boundary are different boundaries, and only one of them was ever being enforced.

**The fix.** `agents/teammate.md`'s COMMIT PROTOCOL mandates `git commit -m '...' -- <key_files>`. Naming the paths makes the commit boundary equal to the ownership boundary, which is what everyone already assumed it was. Paths not named stay staged and stay their owner's to commit.

**Why not stash.** The obvious alternative — stash the peers' work, commit clean, restore — is worse than the problem. Stash operates on the whole working tree, so it takes peers' uncommitted work with it, and the `--keep-index` variant silently drops the unstaged half of a partially-staged file on restore. The no-stash rule is codified in the teammate protocol for that reason.

**Why the guard judges the index.** The shipped pre-commit guard inspects `git diff --cached`, never the working tree. A working-tree guard (`git diff HEAD`) would fire on a peer's unstaged edits — work the committing teammate cannot fix and did not cause — and the only way past it would be a bypass flag, which would then be used routinely and the guard would stop meaning anything. Judging the index keeps the guard's blame surface identical to the committer's responsibility surface, so no protocol path ever needs a bypass.

This file is rationale. The mechanical steps — staging, the pathspec form, deletes and renames, the no-stash rule — live in `agents/teammate.md`.

## Why teams are ephemeral

Teams are created per phase, destroyed after. One team at a time — register/unregister via foundry MCP tools. Ephemeral teams prevent stale teammate context from bleeding across phases.

## Forbidden phrases in casting prompts

These phrases silently authorize scope cuts and will fail F0.9 VALIDATE:
- "pick the core", "pick the most important"
- "don't port every X verbatim", "do not port every"
- "skip the edge cases", "skip the [N] subtests"
- "core coverage", "main cases", "the important ones"
- "follow-up PR", "user will validate manually", "user will confirm later", "validate equivalence manually"
- "intentionally out-of-scope", "reduced scope"
- "target line count", "aim for ~", "keep it under"
- "sufficient coverage", "prove the framework is sufficient"

If the spec demands full coverage, the prompt must say "full coverage" — not hedge around it.

## Casting sizing limits

A single casting may not reference more than 800 LOC of source material a teammate must read OR expect to produce more than 1500 LOC of new code. If the work is bigger than that, split into more castings. The correct response to "this is a lot of work" is more castings, never tighter prompts.

## Requirement classification (from Forge)

- **Locked** → casting MUST implement exactly as specified. Copy the Locked items verbatim into `<spec_requirements>`.
- **Flexible** → teammate has discretion on approach. Include in the block but mark as Flexible.
- **Informational** → provide as context, not as requirements. Include in the `## Requirement Classification` section under Informational.

## The three source-of-truth blocks

Every casting prompt contains three frozen blocks, in priority order:

1. `<mandatory_rules>` — project CLAUDE.md / AGENTS.md / .cursorrules imperatives (highest priority — these are codebase-wide constraints)
2. `<global_invariants>` — cross-cutting spec rules (auth, validation, naming, error handling, security)
3. `<spec_requirements>` — the casting's specific acceptance criteria

If two blocks conflict, the higher-priority block wins. F0.9 Dimensions 7e and 7g verify byte-identical propagation across every casting.

## Why the concern ledger exists

**The failure mode.** A teammate that hits an architectural wall has one good option and two bad ones: record it and keep building, or stop and wait, or quietly bend the scope until the wall is gone. Recording only beats the other two if the record REACHES someone, and for four releases the record was a paragraph appended to `foundry-archive/{run}/concerns.md` — prose with no ids, no status, no schema, and no reader but a lead who remembered to open the file after CAST. The case that breaks is a concern whose fix lands on a SIBLING casting: one casting changes a rule, another states that same rule in its own file, and nothing joins the two. INSPECT then opens over a tree where half a rule moved, the stream that finds the mismatch files it as a fresh defect, and a cycle is spent re-discovering something a teammate had already written down.

**The fix.** Make the ledger structured and let the SERVER act on it. `Foundry-Concern` writes `concerns.json` — `{id, cycle, source_casting, target, text, status}` — and refuses a target nothing resolves, or an empty text, at the door; `concerns.md` stays the prose RENDERING of that ledger, which is what a human reads and never what the server parses. A concern naming another casting then becomes a fact the run can be gated on. It has exactly two exits, both leaving a record: `Foundry-Tasks` carries it to the casting it names in that task's co-dispatch set and marks it dispatched, or `Foundry-Concern(close=<id>, reason=…)` closes it on your ruling and is refused without a reason. And leaving it open is not deferring it — `Foundry-Phase(phase='inspect_start')` REFUSES by id while a cross-casting concern from the closing GRIND is still open, so an unaddressed concern is not a note somebody may read later but a phase the lead cannot open.

This file is rationale. The mechanical rule — which arguments the tool takes, which rung refuses, and what the teammate is told to file — is `Foundry-Concern`'s row in the tools table in `commands/start.md`, and the concern paragraph in `agents/teammate.md` is the half the teammate reads.

## Spec change during GRIND

If a GRIND teammate says "this defect requires a spec change" — that is a ruling to END the run on, not a grind fix and not a quiet return to F0.5. `spec_change_required` is a member of the halt vocabulary `schemas/vocab.py` holds in `HALT_REASONS`, so the exit has a door of its own: `Foundry-Phase(phase='halt', reason='spec_change_required', text=…)`, a SUCCESSFUL transition into `HALTED` that seals a report naming every open finding. File the concern through `Foundry-Concern` first so the ledger records which casting it landed on, then halt, then update the spec and start a NEW run — `HALTED` is terminal and there is no second halt. Never let a GRIND teammate modify scope.
