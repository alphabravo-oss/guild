---
description: Skip adhoc methodical-mode for the next single turn (auto-reverts after one prompt)
---

The user wants to bypass the adhoc methodical-mode injection for the next single user turn only. The hook will auto-revert to ON after that turn.

Run exactly this Bash command:

```
mkdir -p ~/.claude && echo casual > ~/.claude/.adhoc-state && echo "adhoc methodical-mode: CASUAL — next turn skips the methodical preamble, then auto-reverts to ON."
```

After it succeeds, reply to the user with one short line confirming the next turn is casual and that methodical-mode resumes automatically afterward. Do not add anything else.
