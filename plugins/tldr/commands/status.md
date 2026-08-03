---
description: Show the current tldr response-shaping state
---

The user wants to see the current tldr state.

Run exactly this Bash command:

```
state="on"; [ -f ~/.claude/.tldr-state ] && state=$(cat ~/.claude/.tldr-state | tr -d '[:space:]'); echo "tldr: ${state}"
```

After it succeeds, reply with one short line explaining the value:

- `on` — ruleset loaded every new session and enforced every turn (default)
- `off` — suspended for this session; `/tldr:on` re-enables
- `verbose` — next turn gets the long form, then auto-reverts to on

Do not add anything else.
