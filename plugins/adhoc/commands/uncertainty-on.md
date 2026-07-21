---
description: Enable the adhoc Stop hook uncertainty-tell scanner (default mode)
---

The user wants to enable the uncertainty-tell scanner Stop hook. This hook scans every substantive response for phrases like "not verified", "haven't checked", "I assumed", "still need to verify", and blocks the Stop until Claude either verifies the flagged items or stops to ASK the user.

Run exactly this Bash command:

```
mkdir -p ~/.claude && echo default > ~/.claude/.adhoc-uncertainty-mode && echo "adhoc uncertainty-tell scanner: ON. Responses containing self-flagged uncertainty phrases will be blocked until verified or replaced with a clarifying question."
```

After it succeeds, reply to the user with one short line confirming the uncertainty scanner is on. Do not add anything else.
