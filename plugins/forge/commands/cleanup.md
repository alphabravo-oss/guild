---
description: "Clean up all Forge interview state files"
allowed-tools: ["Bash(rm:*)", "Bash(ls:*)", "AskUserQuestion"]
hide-from-slash-command-tool: "true"
---

# Forge Cleanup Command

Remove all Forge interview state files to reset the plugin state.

## STEP 1: CHECK FOR STATE FILES

First, check what state files exist:

```bash
ls -la forge-specs/*/state.md 2>/dev/null || echo "NO_STATE_FILES"
```

Also check for survey data:

```bash
ls -d forge-specs/*/survey/ 2>/dev/null || echo "NO_SURVEY_DATA"
```

## STEP 2: HANDLE RESULTS

### If NO state files exist:

Tell the user:

> No Forge state files found. Nothing to clean up.

Then STOP.

### If state files exist:

List the files that will be deleted, then ask the user for confirmation using AskUserQuestion:

> "Found N state files and M survey directories. What should I clean up?"
> - "State files only" — Remove state.md and draft.md (keep survey data for reference)
> - "Everything" — Remove entire forge-specs/ directory
> - "Cancel" — Don't delete anything

Then execute based on their choice:

**State files only:**
```bash
rm -f forge-specs/*/state.md forge-specs/*/draft.md
```

**Everything:**
```bash
rm -rf forge-specs/
```

Then confirm to the user:

> Forge cleanup complete.
>
> To start a new interview, use:
> ```
> /forge:plan "your feature name"
> ```

## IMPORTANT NOTES

- This command only deletes state and survey files
- It does NOT delete completed specs (the .md and .json files in forge-specs/)
- Use this when you want to abandon all in-progress interviews
