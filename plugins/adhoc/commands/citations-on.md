---
description: Enable the adhoc Stop hook citation verifier (default mode)
---

The user wants to enable the citation-verifier Stop hook. This hook scans every response for file:line citations and blocks any response that cites a file Claude did not Read or Grep in the same turn.

Run exactly this Bash command:

```
mkdir -p ~/.claude && echo default > ~/.claude/.adhoc-citations-mode && echo "adhoc citation-verifier: ON. Responses with unverified file:line citations will be blocked until you Read the cited files."
```

After it succeeds, reply to the user with one short line confirming the citation hook is on. Do not add anything else.
