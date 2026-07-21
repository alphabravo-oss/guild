---
description: Disable the adhoc Stop hook strict gates (grounding audit + critic-agent gate)
---

The user wants to disable the adhoc strict gates. While off, the zero-tool-call grounding audit and the pre-Stop critic-agent gate short-circuit. The uncertainty scanner and citation verifier remain on.

Run exactly this Bash command:

```
mkdir -p ~/.claude && echo off > ~/.claude/.adhoc-strict-mode && echo "adhoc strict-mode: OFF. Codebase grounding + pre-Stop critic gates disabled. Run /adhoc:strict-on to re-enable."
```

After it succeeds, reply to the user with one short line confirming strict mode is off. Do not add anything else.
