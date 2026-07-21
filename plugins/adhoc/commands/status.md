---
description: Show the current adhoc methodical-mode and citation-verifier state
---

The user wants to see both adhoc state values: methodical-mode (the UserPromptSubmit preamble injection) and citation-verifier (the Stop hook).

Run exactly this Bash command:

```
methodical="on"; [ -f ~/.claude/.adhoc-state ] && methodical=$(cat ~/.claude/.adhoc-state | tr -d '[:space:]'); citations="default"; [ -f ~/.claude/.adhoc-citations-mode ] && citations=$(cat ~/.claude/.adhoc-citations-mode | tr -d '[:space:]'); echo "adhoc methodical-mode: ${methodical}"; echo "adhoc citation-verifier: ${citations}"
```

After it succeeds, reply to the user with two short lines:

- methodical-mode: explain the value
  - `on` — methodical preamble fires every turn (default)
  - `off` — silenced for the session; `/adhoc:on` re-enables
  - `casual` — next turn skips, then auto-reverts to on
- citation-verifier: explain the value
  - `default` — Stop hook blocks responses with unverified file:line citations (default)
  - `off` — hook disabled; `/adhoc:citations-on` re-enables

Keep it short — one line per state. Do not add anything else.
