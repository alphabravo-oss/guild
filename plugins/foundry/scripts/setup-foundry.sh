#!/bin/bash

# Foundry Setup Script
# Parses arguments and initializes the foundry run state

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Parse arguments
SCOPE=""
SPEC_PATH=""
URL=""
TEMPER=false
MAX_CYCLES=0
NO_UI=false
OUTPUT_DIR=""
TICKET=""
DESCRIPTION=""
SKIP_START_BACKEND=false
# Serena preflight verdict. UNKNOWN until a real doctor exit code overwrites it,
# never the other way round — an unprobed daemon is never reported healthy.
SERENA_HEALTH="UNKNOWN"

# Handle subcommands first
case "${1:-}" in
  resume|status|stop)
    echo "FOUNDRY_SUBCOMMAND=$1"
    exit 0
    ;;
esac

while [[ $# -gt 0 ]]; do
  case $1 in
    -h|--help)
      cat << 'HELP_EOF'
Foundry — Build-Verify-Fix Loop

Forge plans. Foundry builds.

USAGE:
  /foundry:start <SCOPE> [OPTIONS]
  /foundry:resume
  /foundry:status
  /foundry:stop

ARGUMENTS:
  SCOPE             Description of what to build (required)

OPTIONS:
  --spec <path>            Spec file for spec-aware decomposition
  --url <url>              Browser audit URL for SIGHT verification
  --output-dir <dir>       Output directory (default: auto-generated)
  --temper                 Enable micro-domain stress testing (F5)
  --max-cycles <n>         Cap verify-fix cycles (default: unlimited)
  --no-ui                  Skip browser audit (SIGHT)
  --ticket <id>            Ticket ID for commit messages
  --desc <text>            Run description
  --skip-start-backend     Don't auto-start dev servers

PHASES:
  F0: DECOMPOSE  — Break spec into castings with observable truths
  F1: CAST       — Build castings with parallel teams
  F2: INSPECT    — 4-stream verification (TRACE + PROVE + SIGHT + TEST)
  F3: GRIND      — Fix defects, loop back to INSPECT
  F4: ASSAY      — Final spec-before-code verification (4 parallel agents)
  F5: TEMPER     — Micro-domain stress testing (optional)
  F6: DONE       — Report and archive

EXAMPLES:
  /foundry:start "user authentication" --spec docs/specs/auth.md
  /foundry:start "dashboard redesign" --spec docs/specs/dashboard.md --url http://localhost:3000
  /foundry:start "api improvements" --spec docs/specs/api.md --temper
  /foundry:start "quick fix" --no-ui --max-cycles 2

WORKFLOW:
  1. Forge plans:    /forge:plan "my feature"
  2. Foundry builds: /foundry:start "my feature" --spec docs/specs/my-feature.md

  Forge plans. Foundry builds.
HELP_EOF
      exit 0
      ;;
    --spec)
      SPEC_PATH="$2"
      shift 2
      ;;
    --url)
      URL="$2"
      shift 2
      ;;
    --output-dir)
      OUTPUT_DIR="$2"
      shift 2
      ;;
    --temper)
      TEMPER=true
      shift
      ;;
    --max-cycles)
      MAX_CYCLES="$2"
      shift 2
      ;;
    --no-ui|--headless)
      NO_UI=true
      shift
      ;;
    --ticket)
      TICKET="$2"
      shift 2
      ;;
    --desc)
      DESCRIPTION="$2"
      shift 2
      ;;
    --skip-start-backend)
      SKIP_START_BACKEND=true
      shift
      ;;
    *)
      if [[ -z "$SCOPE" ]]; then
        SCOPE="$1"
      else
        SCOPE="$SCOPE $1"
      fi
      shift
      ;;
  esac
done

# Validate
if [[ -z "$SCOPE" ]]; then
  echo "Error: Scope description is required" >&2
  echo "" >&2
  echo "   Example: /foundry:start \"user authentication\" --spec docs/specs/auth.md" >&2
  exit 1
fi

# ── Serena preflight ──────────────────────────────────────────────────────────
# Record whether Serena was available when this run started, then ALWAYS proceed.
# The verdict is consumed downstream from the run's handoff log; it never gates
# anything here.
#
# Placed after the scope-validation gate and before the report below, so the
# `resume|status|stop` fast path and `--help` (both of which exit earlier) pay no
# probe cost at all.
#
# Three rules are load-bearing:
#   1. NEVER BLOCKS.  A dead Serena must not stop a foundry run, so an unhealthy
#      verdict is the EXPECTED case, on both axes it could arrive by. On exit
#      status, every `doctor` result is captured through an explicit guard
#      (`wait ... || rc=$?`) so a non-zero code cannot trip `set -e`. On
#      latency, the probe is bounded by the watchdog below so a hung one cannot
#      stall the run either. Every path here reaches the report and exits 0.
#   2. EXIT CODE ONLY. doctor's machine-readable contract is its exit code, not
#      its printed report — that text is for humans and may be reworded at any
#      time. Both streams are discarded, which also keeps the report from
#      interleaving into the FOUNDRY_* block below.
#   3. NEVER REPAIRS. `doctor` is read-only by contract and is the only
#      subcommand invoked here. Repair is the SessionStart hook's job; foundry
#      does not mutate service state during a run.
#
# Health probing itself lives entirely in serena-daemon.sh — no retry, port
# check, or handshake is reimplemented here (GI-001). The watchdog below is not
# an exception to that: it bounds an existing invocation from the OUTSIDE and
# interprets nothing. It opens no port, speaks no MCP protocol and reads no
# service state; it knows only "still running" and "no longer running", so it
# adds no liveness logic of its own.

# Wall-clock bound on the probe, in seconds. Rule 1 above holds on the exit-code
# axis for free; without this it does NOT hold on the latency axis. doctor's own
# curl handshake is capped at 3s (serena-daemon.sh:248), but the lsof/ps,
# launchctl/systemctl and drift-comparison steps around it carry no bound of
# their own — so a wedged launchctl, a stuck systemctl or a hung NFS mount under
# $HOME would stall the START of a foundry run indefinitely. That is the
# blocking GI-003 forbids, arriving by latency instead of by exit status.
#
# 5s is exactly the per-invocation bound the SessionStart hook applies to this
# same subcommand on this same script (session-start-serena.sh:47), and it is
# deliberately kept equal: two different bounds on one call would be a
# maintenance trap. Only the budgets AROUND the call differ, and neither differs
# in a direction that argues for a bigger number here. The hook may spend
# 4*5+4=24s because it can issue up to four subcommands around a SETTLE_SECONDS
# wait, and hooks.json's "timeout": 30 stands behind it as a second, outer
# backstop. This preflight issues exactly ONE subcommand and has no outer
# backstop at all, which makes this value the only bound that exists on this
# path — a reason to keep it tight, not to relax it. Change it only in step with
# session-start-serena.sh:47.
SERENA_PROBE_TIMEOUT=5

# Poll granularity while waiting on the probe, in seconds. Sets how long the
# common case over-waits after doctor has already answered (it returns in well
# under a second on a healthy machine), against how many `sleep` forks the worst
# case costs — at most SERENA_PROBE_TIMEOUT/SERENA_POLL_INTERVAL of them.
SERENA_POLL_INTERVAL=0.2

# Run `serena-daemon.sh doctor` under that bound and echo its exit status,
# reporting an expiry as 124 the way timeout(1) would.
#
# No external binary is involved, and that is the entire point. timeout(1) is
# absent from stock macOS — it arrives only with coreutils, as gtimeout or as a
# PATH-shadowing gnubin — so detecting it and degrading to an unbounded call
# when it is missing leaves no bound at all on the priority platform (A-007),
# which is precisely the machine population this plugin reaches through a public
# marketplace in an unknown install state (A-AUTO-007). A bound that is absent
# where it is most needed is not a bound. Everything below is bash builtins plus
# ps/kill/sleep, so the bound holds everywhere the script itself runs.
#
# MUST be called inside a command substitution: the subshell is load-bearing
# twice over. It confines job control to the probe, and it is where bash writes
# the "Terminated" job notice for a killed probe — so the redirection at the
# call site discards that notice. Redirecting the `wait` builtin alone does not.
serena_probe_rc() {
  local probe_pid deadline expired rc pgid target

  # Job control, just long enough to fork the probe, so it becomes the leader of
  # its OWN process group. Killing the leader alone is not enough: doctor's
  # latency is in what it shells out to (curl, ps, launchctl/systemctl), bash
  # does not forward signals to those, and a wedged launchctl would outlive the
  # probe as an orphan still holding the very resource that timed us out.
  # Signalling the group takes the whole tree.
  #
  # stdin comes from /dev/null because under job control a background job that
  # reads the terminal is STOPPED by SIGTTIN rather than killed, and a stopped
  # job would sit in the wait below neither finishing nor dying.
  set -m
  "$SCRIPT_DIR/serena-daemon.sh" doctor >/dev/null 2>&1 </dev/null &
  probe_pid=$!
  set +m

  # Poll, rather than race a background killer against the probe. Escalation
  # then runs on this thread, so there is no second process to cancel and no
  # window where cancelling it skips the follow-up KILL and strands a child that
  # ignored TERM.
  #
  # The deadline is wall-clock via $SECONDS rather than a count of iterations,
  # so the bound stays honest even where `sleep` rejects a fractional argument
  # (some busybox builds). There the guarded sleep fails, this loop spins, and
  # it still ends on the same second — degraded to a busy wait, never to an
  # unbounded one and never to a wrong verdict.
  #
  # $SECONDS advances on whole-second boundaries of the shell's own clock, which
  # this script may have started part-way through, so the kill lands anywhere in
  # (SERENA_PROBE_TIMEOUT-1, SERENA_PROBE_TIMEOUT] — 4s to 5s at the current
  # value, not 5s on the nose. Erring early is the direction GI-003 favours, and
  # even the 4s floor stays clear of doctor's real worst case: a curl handshake
  # capped at 3s plus a handful of fast local queries.
  deadline=$(( SECONDS + SERENA_PROBE_TIMEOUT ))
  expired=false
  while kill -0 "$probe_pid" 2>/dev/null; do
    if [ "$SECONDS" -ge "$deadline" ]; then
      expired=true
      break
    fi
    sleep "$SERENA_POLL_INTERVAL" || true
  done

  if [ "$expired" = true ]; then
    # Never signal a process group we do not own. `kill -TERM -$pid` on a pid
    # that is NOT a group leader lands on THIS script's group — the caller's
    # whole session. So the group id is read back and the negative form is used
    # only where it provably equals the pid; anything else, including a ps that
    # answers nothing, falls back to signalling the single process. Read here
    # rather than at fork time because the probe is known to be alive at this
    # instant, whereas a probe that had already exited would answer nothing.
    pgid="$(ps -o pgid= -p "$probe_pid" 2>/dev/null | tr -d ' ' || true)"
    if [ "$pgid" = "$probe_pid" ]; then
      target="-$probe_pid"
    else
      target="$probe_pid"
    fi
    kill -TERM "$target" 2>/dev/null || true
    sleep 0.5 || true
    kill -KILL "$target" 2>/dev/null || true
  fi

  rc=0
  wait "$probe_pid" || rc=$?
  # A signalled probe reports 143 or 137. Normalise to 124 so "the bound
  # expired" carries one code on every path; both would reach the same UNKNOWN
  # arm below regardless, but only one of them says why.
  if [ "$expired" = true ]; then rc=124; fi
  echo "$rc"
}

if [[ -x "$SCRIPT_DIR/serena-daemon.sh" ]]; then
  rc="$(serena_probe_rc 2>/dev/null)"
  # Mapping is doctor's published exit-code contract; codes are not ours to
  # renumber. Anything outside the table falls through to UNKNOWN rather than
  # being guessed at — 127 (command not found) and 124 (bound expired) both land
  # there, so a hung probe degrades to UNKNOWN and needs no seventh token.
  case "$rc" in
    0) SERENA_HEALTH="HEALTHY" ;;
    1) SERENA_HEALTH="NOT_INSTALLED" ;;
    2) SERENA_HEALTH="INSTALLED_BUT_STOPPED" ;;
    3) SERENA_HEALTH="RUNNING_BUT_UNHEALTHY" ;;
    4) SERENA_HEALTH="DRIFTED" ;;
    *) SERENA_HEALTH="UNKNOWN" ;;
  esac
fi

# Output parsed state for the plan command to use
echo "Foundry — Build-Verify-Fix Loop"
echo ""
echo "Scope: $SCOPE"
if [[ -n "$SPEC_PATH" ]]; then echo "Spec: $SPEC_PATH"; fi
if [[ -n "$URL" ]]; then echo "URL: $URL"; fi
if [[ -n "$OUTPUT_DIR" ]]; then echo "Output: $OUTPUT_DIR"; fi
if [[ "$TEMPER" == "true" ]]; then echo "Temper: enabled"; fi
if [[ "$MAX_CYCLES" -gt 0 ]]; then echo "Max Cycles: $MAX_CYCLES"; fi
if [[ "$NO_UI" == "true" ]]; then echo "UI: disabled"; fi
if [[ -n "$TICKET" ]]; then echo "Ticket: $TICKET"; fi
if [[ -n "$DESCRIPTION" ]]; then echo "Description: $DESCRIPTION"; fi
if [[ "$SERENA_HEALTH" != "HEALTHY" ]]; then echo "Serena: $SERENA_HEALTH (run does NOT block; see serena-daemon.sh doctor)"; fi
echo ""
echo "FOUNDRY_SCOPE=$SCOPE"
echo "FOUNDRY_SPEC=$SPEC_PATH"
echo "FOUNDRY_URL=$URL"
echo "FOUNDRY_OUTPUT=$OUTPUT_DIR"
echo "FOUNDRY_TEMPER=$TEMPER"
echo "FOUNDRY_MAX_CYCLES=$MAX_CYCLES"
echo "FOUNDRY_NO_UI=$NO_UI"
echo "FOUNDRY_TICKET=$TICKET"
echo "FOUNDRY_DESC=$DESCRIPTION"
echo "FOUNDRY_SKIP_BACKEND=$SKIP_START_BACKEND"
echo "FOUNDRY_SERENA_HEALTH=$SERENA_HEALTH"
echo ""
echo "Use MCP tool Foundry-Init to create the run, then follow the phase guide."
echo "Call Foundry-Next at every step to get specific instructions."
echo ""
echo "Forge plans. Foundry builds."
