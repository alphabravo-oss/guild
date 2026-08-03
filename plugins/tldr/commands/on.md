---
description: Re-enable tldr response shaping
---

The user wants to re-enable the tldr ruleset.

Run exactly this Bash command:

```
rm -f ~/.claude/.tldr-state && echo "tldr: ON. Every response is shaped again — and every new session starts this way."
```

After it succeeds, reply with one short line confirming tldr is on. If the ruleset was loaded at session start it applies again from this turn; if this session started with tldr off, follow the shape anyway — action first, numbered steps, one concrete next action, no preamble or sign-off. Do not add anything else.
