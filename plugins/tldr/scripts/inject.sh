#!/usr/bin/env bash
# tldr — UserPromptSubmit hook
# The ruleset itself is loaded once per session by scripts/session-start.sh.
# This hook re-reads the state file on every turn so the toggles take effect
# immediately, mid-session, instead of only on the next session — a session
# already carrying the ruleset needs an explicit override in front of the model
# to stop obeying it.
#
# State file: ~/.claude/.tldr-state
#   absent / "on" → one-line reminder (default)
#   "off"         → explicit override notice; the loaded ruleset is suspended
#   "verbose"     → exempt this one turn, then auto-revert to on

set -uo pipefail

STATE_FILE="${HOME}/.claude/.tldr-state"

state="on"
if [[ -f "${STATE_FILE}" ]]; then
  state="$(tr -d '[:space:]' < "${STATE_FILE}" 2>/dev/null || echo on)"
fi

case "${state}" in
  off)
    cat <<'EOF'
[tldr: OFF for this session — the TLDR response-shaping ruleset does NOT apply to this turn. Disregard it and answer in your default style. /tldr:on re-enables it.]
EOF
    ;;
  verbose)
    # One-shot exemption — clear the flag so the next turn is shaped again.
    rm -f "${STATE_FILE}"
    cat <<'EOF'
[tldr: VERBOSE for this turn only — the TLDR response-shaping ruleset is suspended for this one response. Give the long form: full reasoning, complete detail, as much structure as the answer needs. TLDR mode resumes automatically on the next turn.]
EOF
    ;;
  *)
    cat <<'EOF'
[tldr active — action first, numbered steps, state where we are, one concrete next action, no preamble/recap/sign-off. Full ruleset loaded at session start. /tldr:verbose for the long form this turn, /tldr:off to disable.]
EOF
    ;;
esac
