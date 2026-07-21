---
description: "Resume an interrupted forge specification interview"
allowed-tools: ["AskUserQuestion", "Read", "Write", "Glob", "Grep", "Agent", "Bash(ls:*)", "Bash(cat:*)"]
hide-from-slash-command-tool: "true"
---

# Forge Resume Command

Resume an interrupted specification interview.

## STEP 1: SCAN FOR INTERRUPTED INTERVIEWS

First, scan for all existing interview state files:

```bash
ls -la forge-specs/*/state.md 2>/dev/null || echo "NO_STATE_FILES"
```

## STEP 2: HANDLE RESULTS

### If NO state files exist:

Tell the user:

> No interrupted Forge interviews found.
>
> To start a new interview, use:
> ```
> /forge:plan "your feature name"
> ```

Then STOP — do not continue.

### If state files exist:

For each `forge-specs/*/state.md` file found (exclude `forge-draft.md`), read the YAML frontmatter to extract:
- `feature_name` — The human-readable feature name
- `started_at` — When the interview was started
- `phase` — Which phase was in progress (R0_SURVEY, R1_SYNTHESIZE, R2_INTERVIEW, R3_SPEC, R4_VALIDATE)

Then present the list to the user using AskUserQuestion with options like:
- Option 1: "Feature Name A (started: 2026-01-15, phase: R2_INTERVIEW)"
- Option 2: "Feature Name B (started: 2026-01-17, phase: R0_SURVEY)"

## STEP 3: RESUME SELECTED INTERVIEW

Once the user selects an interview:

1. Read the full state file content (includes the complete prompt)
2. Read the draft spec file at `forge-specs/*/draft.md` if it exists
3. Check the `phase` field to determine where to resume:

### Resume from R0_SURVEY:
- Check which survey files already exist in the survey directory
- Re-spawn any missing survey agents
- Then continue to R1 → R2 → R3 → R4

### Resume from R1_SYNTHESIZE:
- Read existing survey files
- Write the reality document
- Continue to R2 → R3 → R4

### Resume from R2_INTERVIEW:
- Read the reality document (if survey was performed)
- Read the draft spec to understand what's been gathered
- Continue the interview from where it left off
- Tell the user: "Resuming interview for: [FEATURE_NAME]. I'll review the draft and continue."

### Resume from R3_SPEC or R4_VALIDATE:
- Read all gathered information
- Generate/complete the spec

## INTERVIEW RULES (same as /forge:plan)

1. EVERY question must use AskUserQuestion — plain text questions won't work
2. Ground every question in codebase findings
3. Ask NON-OBVIOUS questions
4. Continue until user says "done" or "finalize"
5. Update the draft spec file regularly using the Write tool

## FINALIZATION CONSTRAINTS — CRITICAL

When the user says "done", "finalize", "finished", or similar:

### ALLOWED ACTIONS (Read and Write ONLY):
- Read any files needed to compile the final spec
- Write the final spec to {output-dir}/{slug}.md
- Write the JSON spec to {output-dir}/{slug}.json
- Write the progress file to {output-dir}/{slug}-progress.txt
- Delete the state file at .claude/forge-{slug}.md using Write with empty content

### FORBIDDEN ACTIONS (DO NOT USE):
- NO Bash tool calls — do not run any commands
- NO Edit tool calls — do not modify existing code
- NO Task tool calls — do not launch subagents
- NO implementation of any kind — you are ONLY writing spec documents

### FINALIZATION SEQUENCE:
1. Write the final markdown spec file
2. Write the JSON spec file
3. Write the progress file with all phases marked as [PENDING]
4. Delete the state file (.claude/forge-{slug}.md)
5. Output `<promise>SPEC FORGED</promise>`
6. STOP IMMEDIATELY — do not continue with any other actions

The spec is the deliverable. Foundry builds it.
