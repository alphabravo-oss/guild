# Foundry Lead Discipline — Rationale

**Who reads this:** Anyone debugging Foundry behavior, or the Lead ONCE when a rule violation is detected. The Lead does NOT re-read this every phase. This file is rationale — the mechanical rules live in `commands/start.md`.

## Why the "router not interpreter" architecture

**The failure mode.** When the lead drafts teammate prompts from the casting manifest, the lead becomes an *interpreter* between the spec and the teammate. That interpretation layer is where spec fidelity silently erodes: scope cuts, hedge language, "pick the core coverage" permissions. Each translation is lossy; multiplicative decay across layers turns a 90%-faithful spec into a 59%-faithful build.

**The fix.** Eliminate the interpretation layer. Decompose authors the complete teammate prompt ONCE, from the spec as source of truth, at F0.5. The prompt is saved to disk, validated at F0.9, frozen. The lead at F1/F3 calls `Foundry-Spawn-Teammate` which reads the file and returns the text. The lead passes it to the Agent tool verbatim. Plans are prompts.

## Why CORRECTNESS BEATS CONTEXT BUDGET

If a casting is "too large for one teammate's context," that is a DECOMPOSITION failure, not a license to cut scope. Split the casting into smaller ones, run more waves, or split work across more teammates with non-overlapping file boundaries. NEVER instruct a teammate to skip subtests, drop edge cases, defer coverage, cut to "core cases," or let the user validate the rest manually. Those are forbidden and F0.9 VALIDATE will reject any casting prompt that contains them.

## Why strict interpretation is the default

When the spec contains ambiguous wording ("equivalent coverage", "similar to legacy", "roughly like X", "core cases", "mostly"), always pick the STRICTER interpretation. "User will validate equivalence manually" means "equivalence must already be there for the user to validate," NOT "partial is fine for now." If you cannot resolve an ambiguity with the strict reading, flag it in state.json as `SPEC_AMBIGUOUS` and proceed with strict reading. Autonomous runs never downgrade strictness as a convenience.

## Why verbatim prompts, no lead authoring

When spawning a teammate, pass the `prompt` field from `Foundry-Spawn-Teammate` verbatim to the Agent tool. You MAY NOT modify, summarize, paraphrase, prepend, append, substitute, or wrap the prompt. GRIND is the only exception: you may append a clearly-delimited `## Defects to fix this cycle:` block after the returned prompt, never inside it.

Any lead-authored text in the teammate prompt is a vector for spec drift. By mechanically forbidding any lead authoring at F1, we eliminate the drift surface entirely. If something is missing from the prompt, the fix is to re-run F0.5 DECOMPOSE with a correction, not to inject text here.

If you find yourself wanting to "just add a note" or "clarify scope" in a teammate prompt — STOP. That instinct is the exact failure mode this architecture prevents. The correct response is to re-run F0.5 DECOMPOSE with the clarification as an update to the spec or the casting's `<spec_requirements>` block.

## Why no worktrees

Teammates work in the main directory, no `isolation: "worktree"` when spawning agents. Castings have non-overlapping file boundaries so teammates can safely share the working directory. Worktree lifecycle + merge-back adds complexity with no benefit when file ownership is already disjoint.

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

## Why concerns.md exists

Teammates log architectural concerns to `foundry-archive/{run}/concerns.md` instead of stopping. The Lead reviews these after CAST completes. Any concern that relaxes the spec is a decompose failure — re-run F0.5, not a patch at F1.

## Spec change during GRIND

If a GRIND teammate says "this defect requires a spec change" — that is a halt condition, not a grind fix. Log it to concerns.md as `SPEC_CHANGE_REQUIRED`, surface to the lead, and return to F0.5 DECOMPOSE for the affected castings after the spec is updated. Never let a GRIND teammate modify scope.
