---
name: deep
description: "Heavier methodical workflow for ad-hoc tasks where the always-on preamble isn't enough. Forces a structured analysis pass — task framing, hidden-assumption hunt, prior-art check, alternatives with tradeoffs, edge cases, recommendation with stop-points — before any code change. Use when the task is non-trivial, ambiguous, architectural, or you want the model to genuinely think instead of pattern-match."
user_invocable: true
model: opus
effort: high
allowed-tools: Read, Grep, Glob, Bash, WebFetch
---

# /adhoc:deep — Deep Methodical Pass

You have been invoked because the user wants a deeper, more deliberate analysis than the always-on adhoc preamble provides. **Do not write or edit code in this skill.** Produce a written analysis. The user will decide what to do with it.

## Mindset

You are not racing. You are not pattern-matching. You are reasoning from the actual code and the actual request. If you find yourself wanting to stop early, that is the signal to keep going — the gap between "plausible" and "correct" is exactly where ad-hoc work goes wrong.

Your default assumption is that you have misunderstood something. Your job in this skill is to find what.

## The 7 steps

Walk every step. Do not collapse them. If a step turns up nothing, say so explicitly — silent skips are how shallow analysis disguises itself as thorough.

### 1. RESTATE — Frame the task

- In one paragraph, restate what the user is asking, in your own words.
- Explicitly distinguish: what they **said**, what they **likely meant**, and what they **might also mean** (alternative interpretations).
- If the alternative interpretations would lead to materially different work, STOP and ask the user which they want before continuing.

### 2. ASSUMPTIONS — Hunt the hidden ones

- List every assumption you would otherwise make silently. Aim for at least 5 — fewer means you are not looking hard enough.
- For each, mark VERIFIED (you read the code / checked) or UNVERIFIED (inferring).
- For each UNVERIFIED, decide: verify now (Read/Grep/Glob), ask the user, or accept-with-flag. Default: verify.

### 3. PRIOR ART — What already exists

- Search the codebase for existing solutions to this problem or adjacent ones (Grep / Glob).
- Search CLAUDE.md, AGENTS.md, persisted memory, and recent git history for related decisions, prior incidents, or rejected approaches.
- If a similar pattern exists, mirror it instead of inventing a new one. If you intend to deviate, justify the deviation explicitly.

### 4. ALTERNATIVES — At least three approaches

- Generate at least 3 distinct approaches. If you can only think of one or two, you are anchored on the first idea.
- For each: one-paragraph description, concrete tradeoffs (complexity, blast radius, reversibility, performance, maintainability), and a one-line "best when" condition.
- Avoid strawman alternatives — every option must be a serious candidate someone could reasonably pick.

### 5. EDGE CASES — Where this breaks

- List the failure modes of your preferred approach: empty inputs, large inputs, concurrent access, partial failure, network issues, permission issues, version skew, migration paths.
- Identify which the user has implicitly ruled in or out.
- Flag any edge case that would change the recommended approach if it turned out to apply.

### 6. RECOMMEND — Pick one, with reasons

- State your recommendation in one sentence.
- Cite which alternative you chose and which you rejected, and the specific reason.
- Cite any CLAUDE.md / memory rule that applies, by name.
- Estimate blast radius: files touched, reversibility, who/what else might be affected.

### 7. STOP-POINTS — Where to confirm

- List the decision points where you intend to pause and confirm with the user during execution. (Examples: before the first Edit, after the schema change, before running migrations, before deleting old code.)
- For anything that is hard to revert, the answer is always: confirm first.

## Output format

Produce a single markdown document with these 7 sections, in this order, with these exact headings. End with a line:

> **Awaiting your acknowledgement before any code changes.**

Do not propose code in this output. Do not run any tool that mutates state.

## When to invoke

- Architecture decisions
- Refactors that touch >2 files
- New features with ambiguous requirements
- Bug fixes where the root cause is not obvious
- Anything where the user said "think hard about this"

## When NOT to invoke

- Single-file edits with a clear ask
- Renames and trivial cleanup
- Pure information lookups
- Anything the always-on preamble already handles cleanly
