---
description: Show the adhoc plugin commands and how the methodical-mode hook works
---

The user wants help with the adhoc plugin. Reply with exactly this message — verbatim, no additions, no embellishment:

```
adhoc — methodical-mode for ad-hoc Claude work (v0.3.0)

WHAT IT DOES
  Always-on UserPromptSubmit hook prepends a methodical pre-response checklist
  to every user turn: restate the task, mark assumptions VERIFIED/UNVERIFIED,
  Read Floor (read code before editing), Approach Deliberation (>=2 candidates),
  Blast Radius (grep callers), Stall Check (anti-spin), Competing Hypotheses
  (for bugs), Goal-Driven Execution (verifiable success criteria), Restraint
  Check (no over-engineering). Stops the race-to-an-answer pattern.

  Always-on Stop hook enforces four gates after the response is drafted:
    (a) Citations  — file:line citations must be backed by Read/Grep this turn
    (b) Uncertainty — blocks "not verified" / "haven't checked" / "I assumed"
    (c) Grounding  — codebase questions require a Read/Grep/Glob this turn
                     (no general-knowledge fallback)
    (d) Iterative Critic — substantive responses must pass a multi-round
                           critic dialog (NEW in v0.3.0):
        - Rounds 1-2: adhoc:fast-critic (Haiku, cheap, 10-item rule audit)
        - Rounds 3-5: adhoc:pre-stop-critic (opus escalation, same audit
                      with deeper file-reading verification)
        - Each critic sees prior-round feedback and checks if Claude
          ADDRESSED, STILL FAILING, or REPHRASED-NOT-FIXED each prior flag
        - At round 5 without CONCUR: HARD block — Claude cannot Stop until
          /adhoc:trust-me bypass is invoked by the user

  Gates (a)/(b)/(c) honor stop_hook_active (one block per turn each).
  Gate (d) uses a transcript-based round counter (up to 5 block-rounds).

COMMANDS — methodical-mode (UserPromptSubmit hook)
  /adhoc:status            Show current methodical-mode state
  /adhoc:on                Re-enable methodical-mode for this session
  /adhoc:off               Disable methodical-mode for the rest of this session
  /adhoc:casual            Skip methodical-mode for the NEXT turn only — auto-reverts
  /adhoc:deep              Heavier methodical analysis pass (skill, read-only)
  /adhoc:help              This message

COMMANDS — Stop-hook gates
  /adhoc:citations-on      Enable file:line citation verifier (default)
  /adhoc:citations-off     Disable citation verifier
  /adhoc:uncertainty-on    Enable uncertainty-tell scanner (default)
  /adhoc:uncertainty-off   Disable uncertainty-tell scanner
  /adhoc:strict-on         Enable grounding audit + iterative critic gate (default)
  /adhoc:strict-off        Disable grounding audit + iterative critic gate
  /adhoc:trust-me          One-shot bypass of grounding + critic gates for the
                           NEXT turn only — auto-consumed. Uncertainty + citation
                           gates stay on. Use this to escape a HARD-blocked
                           round-5 deadlock when the critic is misbehaving.

SUBAGENTS
  adhoc:second-opinion     Independent fresh-context critique of a plan / approach.
                           User-invoked. Heavier review for major decisions.
                           Not part of the Stop-hook gate dialog.

  adhoc:fast-critic        Hook-invoked Haiku critic — runs rounds 1-2 of the
                           gate dialog. Cheap 10-item rule audit per round.
                           Emits sentinel [adhoc:fast-critic-output] so the Stop
                           hook skips itself on critic output (recursion guard).

  adhoc:pre-stop-critic    Hook-invoked opus critic — runs rounds 3-5 of the
                           gate dialog (escalation tier). Same 10-item audit
                           with deeper file-reading verification and
                           prior-round continuity check. Emits sentinel
                           [adhoc:pre-stop-critic-output] for recursion guard.

ITERATIVE GATE DIALOG (gate d — the new v0.3.0 layer)
  Round 1: Claude drafts -> Stop hook spawns fast-critic
           - CONCUR -> response lands
           - Non-CONCUR -> Claude revises based on specific feedback
  Round 2: Claude retries Stop -> hook spawns fast-critic again with
           prior-round feedback included
           - Critic checks each prior flag: ADDRESSED | STILL FAILING |
             REPHRASED-NOT-FIXED
           - CONCUR -> response lands
  Round 3: Escalates to pre-stop-critic (opus). Same loop with deeper review.
  Round 4: Pre-stop-critic continues iteration.
  Round 5: Pre-stop-critic last chance. If still non-CONCUR -> HARD block.
           User must invoke /adhoc:trust-me to bypass.

  Cost profile:
    Typical turn (CONCUR round 1): 1 Haiku call (~$0.001)
    Stuck turn worst case: 2 Haiku + 3 opus calls (~$0.10-0.15)

  Deadlock risk: if a critic misbehaves (returns false PUSH BACK loops),
  the session locks up at round 5 until /adhoc:trust-me is invoked.
  This is intentional per the strict-on configuration.

STATE FILES (all under ~/.claude/)
  .adhoc-state                  methodical-mode injector: absent/"on" / "off" / "casual"
  .adhoc-citations-mode         gate (a): absent/"default" / "off"
  .adhoc-uncertainty-mode       gate (b): absent/"default" / "off"
  .adhoc-strict-mode            gates (c) and (d): absent/"default" / "off"
  .adhoc-trust-me               one-shot bypass file for (c)+(d) — consumed on read
  .adhoc-citations-log.jsonl    per-turn gate decisions (audit log, includes
                                rounds_completed and prior_verdicts for gate d)

WHEN TO RELEASE-VALVE
  /adhoc:casual    For trivial chitchat where the methodical preamble is pure
                   noise. Skips the UserPromptSubmit injection for one turn.
  /adhoc:trust-me  For genuine general-knowledge questions OR when the critic
                   gate gets stuck in a false-PUSH-BACK loop. Skips grounding
                   gate (c) + iterative critic gate (d) for one turn. Citation
                   + uncertainty gates still run.
  /adhoc:off       For long pairing sessions where you've calibrated and the
                   preamble repeats itself. Silences the UserPromptSubmit
                   injection for the rest of the session. Stop gates keep firing.
  /adhoc:strict-off + /adhoc:uncertainty-off + /adhoc:citations-off
                   Nuclear: disables ALL Stop gates for the session.

DESIGN NOTES
  - Default is always-on enforcement. If you remembered to invoke it, you
    wouldn't need it — the point is that it fires when you forget.
  - Citation, uncertainty, and grounding gates use deterministic regex.
    Critic gate (d) uses LLM judgment (Haiku then opus) because language
    variation is too rich for regex (e.g., "haven't checked" vs "re-checking"
    vs "let me look into it" — all the same dodge, different vocabulary).
  - Recursion guard: critic responses begin with their sentinel marker.
    Stop hook detects EITHER fast-critic or pre-stop-critic sentinel and
    skips ALL gates on those responses.
  - All gate decisions are logged to .adhoc-citations-log.jsonl for
    post-hoc auditing of false positives / negatives.
  - The 10-item compliance audit each critic runs:
      1. Rules consulted (CLAUDE.md/AGENTS.md/memory)
      2. Read Floor satisfied
      3. Comments-not-code (no docstring citations passed off as evidence)
      4. Approach Deliberation written
      5. Blast Radius written
      6. Competing Hypotheses written (for bugs)
      7. Restraint check (no scope creep)
      8. Self-critique
      9. Promise-without-action
      10. Hedge-laundering
```

Do not add anything before or after. Do not summarize. Do not paraphrase.
