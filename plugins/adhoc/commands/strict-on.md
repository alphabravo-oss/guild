---
description: Enable the adhoc Stop hook strict gates (grounding audit + critic-agent gate)
---

The user wants to enable the adhoc strict gates: the zero-tool-call grounding audit (codebase-shaped questions require a Read/Grep/Glob call this turn — no general-knowledge fallback) and the pre-Stop critic-agent gate (substantive responses must pass an `adhoc:pre-stop-critic` subagent verdict before landing).

Run exactly this Bash command:

```
mkdir -p ~/.claude && echo default > ~/.claude/.adhoc-strict-mode && echo "adhoc strict-mode: ON. Codebase questions require Read/Grep this turn; substantive responses must pass the pre-Stop critic. /adhoc:trust-me for a one-turn bypass."
```

After it succeeds, reply to the user with one short line confirming strict mode is on. Do not add anything else.
