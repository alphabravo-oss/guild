---
description: Get the long form for the next single turn (auto-reverts after one prompt)
---

The user wants the next single response unshaped — full reasoning, complete detail, as much structure as the answer needs. The hook auto-reverts to ON after that one turn.

Run exactly this Bash command:

```
mkdir -p ~/.claude && echo verbose > ~/.claude/.tldr-state && echo "tldr: VERBOSE — the next turn gets the long form, then auto-reverts to ON."
```

After it succeeds, reply with one short line confirming the next turn is the long form and that tldr resumes automatically afterward. Do not add anything else.
