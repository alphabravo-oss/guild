---
description: "Cancel an active crew run"
argument-hint: "<run-id>"
allowed-tools: ["Bash(cat:*)", "Bash(ls:*)", "Bash(test:*)", "Bash(date:*)", "Read", "Write", "Edit", "AskUserQuestion"]
---

# Crew Cancel

Mark a crew run as cancelled. Does NOT kill running processes (the orchestrator is per-invocation, not a daemon) — it sets the on-disk status so subsequent runs/lists treat it as cancelled.

```
RUN_ID="$ARGUMENTS"
```

If `$ARGUMENTS` is empty, list active runs (status not in {completed, cancelled*}) and ask which to cancel via `AskUserQuestion`.

## Steps

1. Verify the run exists at `.crew/runs/$RUN_ID/state.json`.

2. Read current status. If already `completed` or `cancelled*`, tell the user "nothing to do" and stop.

3. Confirm with user via `AskUserQuestion`:
   - **Cancel** — mark cancelled, leave artifacts for inspection
   - **Cancel and delete** — mark cancelled, remove the run dir (only the run dir, never anything outside)
   - **Never mind** — leave alone

4. On Cancel: update `state.json`:

```json
{
  ...existing fields...,
  "status": "cancelled",
  "cancelled_at": "<ISO timestamp>",
  "cancelled_at_phase": "<previous phase>"
}
```

5. On Cancel and delete: do the state.json update first, then remove the dir:

```bash
rm -rf ".crew/runs/$RUN_ID"
```

(This is the only place in the plugin where the orchestrator itself does a destructive op — and it's gated by an explicit user confirmation.)

6. Confirm to user:

```
Cancelled <RUN_ID>. <Artifacts at .crew/runs/<RUN_ID>/ | Artifacts removed.>
```

## Rules

- Confirm before destructive action. Always.
- Never touch anything outside `.crew/runs/<run-id>/`.
- A cancelled run can be inspected but not resumed (use `/crew:do` to start fresh).
