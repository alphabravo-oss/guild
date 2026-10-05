---
description: Re-enable the tldr Stop-hook length gate
---

The user wants over-long responses blocked again before they are delivered.

Run exactly this Bash command:

```
rm -f ~/.claude/.tldr-gate && echo "tldr length gate: ON. An over-long response is blocked once and rewritten."
```

After it succeeds, reply with one short line confirming the gate is on. Do not
add anything else.
