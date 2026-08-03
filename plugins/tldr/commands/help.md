---
description: Show the tldr plugin commands and how the response-shaping hooks work
---

The user wants help with the tldr plugin. Reply with exactly this message — verbatim, no additions, no embellishment:

```
tldr — always-on response shaping for Claude Code (v0.1.0)

WHAT IT DOES
  Two hooks, one ruleset.

  SessionStart hook (scripts/session-start.sh) loads rules/ruleset.md into
  every new session — startup, resume, clear, and compact. Nothing to invoke,
  nothing to remember. A fresh session is already shaped.

  UserPromptSubmit hook (scripts/inject.sh) re-reads the state file on every
  turn and emits one line: a reminder when on, an explicit override when off,
  a one-turn exemption when verbose. This is what makes /tldr:off take effect
  immediately — a session that already loaded the ruleset needs something in
  front of the model telling it to stop, not a flag that only matters next
  session.

THE 10 RULES
  1.  First line is the action — command, path, value, or verdict. Not framing.
  2.  Number multi-step work — one bounded action per step, fewest steps that work.
  3.  Close with exactly one next action — under two minutes, unambiguous.
  4.  Kill tangents — finish one issue; a second gets one sentence at the end.
  5.  Restate position every turn — "step 3 of 5 done", not "ready for the next bit?"
  6.  Estimate in real units — "~20 minutes if tests cover it", not "some work".
  7.  Make the win concrete — what works now and how to see it.
  8.  Flat tone on failure — cause and fix, no "uh oh".
  9.  Five items maximum — split past that into do-now vs later.
  10. No preamble, no recap, no sign-off — start at the answer, stop when done.

BREAK-GLASS EXCEPTIONS (built into the ruleset)
  1. User asked for depth ("explain", "walk me through") — run long, still no
     preamble or closer, add headers for skimming
  2. Destructive action ahead — confirm first; safety outranks brevity
  3. Debug spiral (3 turns of "still broken") — stop editing, name the suspect
     assumption, ask one diagnostic question
  4. Real ambiguity — one short clarifying question beats guessing
  5. A rule would delete the answer — "what are my options" gets the options
  6. Harness outranks — system prompt and CLAUDE.md win

COMMANDS
  /tldr:status     Show the current state
  /tldr:on         Re-enable shaping (default)
  /tldr:off        Suspend shaping for the rest of this session
  /tldr:verbose    Long form for the NEXT turn only — auto-reverts
  /tldr:help       This message

STATE FILE
  ~/.claude/.tldr-state
    absent / "on"  ruleset loaded each session, enforced each turn (default)
    "off"          suspended for the session until /tldr:on
    "verbose"      next turn exempt, then auto-reverts to on

WHEN TO RELEASE-VALVE
  /tldr:verbose  One answer genuinely needs the long form — an architecture
                 walkthrough, a design doc, a full comparison. Cheaper than
                 toggling off and forgetting to toggle back.
  /tldr:off      A long session where you want conversational back-and-forth,
                 or you are writing prose with Claude rather than shipping code.

DESIGN NOTES
  - Always-on by default is the whole point. A style you have to remember to
    invoke is a style you get on the turns you were already being careful.
  - The ruleset loads once per session, not once per turn — the per-turn hook
    is one line. Full text every turn would cost more than it shapes.
  - Composes with adhoc: adhoc governs how Claude THINKS before answering
    (read floor, alternatives, verified citations); tldr governs the SHAPE of
    what comes out. Run both — they touch different parts of the turn.
  - Rules are prose in rules/ruleset.md, not scattered through shell. Edit that
    one file to tune your own house style; both hooks pick it up.
  - Prior art: ayghri/i-have-adhd (MIT). Same core insight, rewritten for
    Guild, always-on by default, with a mid-session mechanical off-switch.
```

Do not add anything before or after. Do not summarize. Do not paraphrase.
