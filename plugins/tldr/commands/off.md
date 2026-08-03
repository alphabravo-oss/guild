---
description: Disable tldr response shaping for the rest of this session
---

The user wants to suspend the tldr ruleset for the remainder of this session.

Run exactly this Bash command:

```
mkdir -p ~/.claude && echo off > ~/.claude/.tldr-state && echo "tldr: OFF for this session. Run /tldr:on to re-enable."
```

After it succeeds, reply with one short line confirming tldr is off and noting that `/tldr:on` re-enables it. Answer in your default style from here on — the ruleset loaded at session start no longer applies. Do not add anything else.
