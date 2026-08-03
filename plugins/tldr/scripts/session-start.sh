#!/usr/bin/env bash
# tldr — SessionStart hook
# Loads the full response-shaping ruleset once per session, so every new
# session starts in TLDR mode without anyone having to remember to ask.
#
# State file: ~/.claude/.tldr-state
#   absent / "on" → load the ruleset (default)
#   "off"         → load nothing (until /tldr:on)
#   "verbose"     → load the ruleset anyway; scripts/inject.sh exempts the
#                   single next turn and clears the flag
#
# Never blocks session start: any failure exits 0 without output.

set -uo pipefail

STATE_FILE="${HOME}/.claude/.tldr-state"

state="on"
if [[ -f "${STATE_FILE}" ]]; then
  state="$(tr -d '[:space:]' < "${STATE_FILE}" 2>/dev/null || echo on)"
fi

# Session-level opt-out. Emit nothing — inject.sh puts the per-turn OFF notice
# in front of the model instead.
[[ "${state}" == "off" ]] && exit 0

# CLAUDE_PLUGIN_ROOT is set by Claude Code; $0 is the absolute script path, so
# resolving from it keeps the hook working when the env var is missing.
plugin_root="${CLAUDE_PLUGIN_ROOT:-$(cd -- "$(dirname -- "$0")/.." && pwd)}"
ruleset="${plugin_root}/rules/ruleset.md"

[[ -f "${ruleset}" ]] || exit 0

printf '[tldr — response-shaping active for this session. /tldr:verbose exempts one turn, /tldr:off disables until /tldr:on]\n\n'
cat "${ruleset}"
