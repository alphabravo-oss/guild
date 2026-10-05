---
description: Disable the tldr Stop-hook length gate, keeping the ruleset itself on
---

The user wants to stop the length gate blocking over-long responses. The
ruleset stays loaded and still shapes answers — only the enforcement stops.

Run exactly this Bash command:

```
mkdir -p ~/.claude && echo off > ~/.claude/.tldr-gate && echo "tldr length gate: OFF. Shaping still applies; nothing checks it. /tldr:gate-on re-enables."
```

After it succeeds, reply with one short line confirming the gate is off and that
the ruleset still applies. Do not add anything else.
