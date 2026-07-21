---
description: Disable adhoc methodical-mode injection for the rest of this session
---

The user wants to silence the adhoc UserPromptSubmit hook for the remainder of this session.

Run exactly this Bash command:

```
mkdir -p ~/.claude && echo off > ~/.claude/.adhoc-state && echo "adhoc methodical-mode: OFF for this session. Run /adhoc:on to re-enable."
```

After it succeeds, reply to the user with one short line confirming methodical-mode is off and noting that `/adhoc:on` re-enables it. Do not add anything else.
