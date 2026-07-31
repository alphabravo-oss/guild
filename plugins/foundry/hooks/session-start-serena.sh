#!/usr/bin/env bash
# foundry — SessionStart hook wrapper
#
# The first automatic caller of serena-daemon.sh. Until this existed the script
# had no inbound edge at all: nothing re-ran setup, so a machine holding a stale
# pre-rename com.codsworth.serena agent (A-003) kept contending for the port
# indefinitely and no one was told. This runs on every session start, which is
# what actually closes that silent-failure class.
#
# THIN DISPATCHER ONLY. Every probe, repair, and lifecycle decision lives in
# serena-daemon.sh (A-008). This file contains subcommand invocations, a branch
# on their integer exit codes, and JSON emission — no port check, no handshake,
# no service-definition comparison, no PID handling. No HTTP client, no socket
# lister, no service-manager client and no process launcher may ever be called
# from this file; if one is needed, shell out to a subcommand instead.
#
# NEVER BLOCKS AND NEVER FAILS THE SESSION. Every path exits 0. A dead, wedged,
# missing or hung Serena is reported to the model through additionalContext, not
# through a nonzero exit and not through a hang.

set -euo pipefail

HOOK_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
DAEMON="$HOOK_DIR/../scripts/serena-daemon.sh"

# Per-invocation wall-clock bounds, in seconds, enforced by daemon_rc() below.
#
# They are UNEQUAL, and that is deliberate. One shared bound must be sized to
# the slowest subcommand, which inflates the worst case of every other call for
# no reason; sized to the fastest, it truncates reconcile mid-repair. Each bound
# is instead derived from the real worst case of the subcommand it guards.
#
# DAEMON_TIMEOUT covers doctor, the settle re-probe, and start. doctor's
# handshake is capped by curl's own --max-time 3 and the rest of it is fast
# local queries; start runs no handshake at all, only port resolvers plus the 2s
# liveness sleep it takes after launching detached (~3s worst). 5s clears both
# with headroom. It is also, deliberately, the SAME number the foundry preflight
# bounds its own doctor probe with (setup-foundry.sh, SERENA_PROBE_TIMEOUT): two
# callers of one subcommand must not disagree about how long it may take, and
# that file's comment asks for the two to be changed only in step. Honour that.
DAEMON_TIMEOUT=5

# RECONCILE_TIMEOUT covers reconcile alone — the one subcommand the preflight
# never calls, which is why it can diverge without breaking the equality above.
# reconcile's CONVERGED path is 0.025s, but its REPAIR path now ends in a
# post-load health probe (serena-daemon.sh, RECONCILE_PROBE_SECONDS=4) whose
# budget is only checked AFTER each attempt, so a final attempt can begin just
# under budget and then spend curl's full 3s cap: 4 + 3 = 7s worst case. Under
# the old shared 5s bound that repair was killed mid-probe — recorded as concern
# C-019 while the missing timeout(1) on stock macOS still masked it. 8s lets the
# repair finish and report instead of being truncated.
RECONCILE_TIMEOUT=8

# Settle window, in seconds, between a doctor verdict of "installed but stopped"
# and acting on it. See the long comment at the settle block below for why this
# exists. Sized from measurement, not guesswork: `launchctl load` returns in
# 0.020s while the daemon it just spawned takes 1.473s more to bind the port
# (measured on a warm uv cache), so 4s is ~2.7x the observed window.
SETTLE_SECONDS=4

# Poll granularity while waiting on a bounded subcommand, in seconds. Sets how
# long the common case over-waits after the subcommand has already answered
# (doctor returns in well under a second on a healthy machine) against how many
# `sleep` forks the worst case costs.
DAEMON_POLL_INTERVAL=0.2

# ── Worst-case budget ─────────────────────────────────────────────────────────
# The longest path this hook can take is the stopped-daemon repair path, which
# issues four subcommands around one settle wait:
#
#     reconcile          RECONCILE_TIMEOUT     8
#     doctor             DAEMON_TIMEOUT        5
#     settle sleep       SETTLE_SECONDS        4
#     doctor re-probe    DAEMON_TIMEOUT        5
#     start              DAEMON_TIMEOUT        5
#                                             --
#                                             27
#
# plus the watchdog's own escalation: an expiry costs a TERM, a 0.5s grace and a
# KILL, so four expiries add 2.0s. Worst case 29s — an upper bound rather than
# an estimate, because the watchdog can only fire EARLY: $SECONDS ticks on whole
# second boundaries of a clock this script may have started part-way through, so
# a bound of N expires somewhere in (N-1, N] and never past N.
#
# hooks.json carries "timeout": 36 as an outer, framework-enforced backstop, and
# the 7s gap between the two is deliberate: if every subcommand hung, the
# per-invocation bounds must expire FIRST so this script survives to report the
# hang through additionalContext. Were the two equal the framework could kill
# the hook mid-flight and the failure report — the entire point of the hook —
# would be lost on exactly the machine that needed it. Raise both together or
# neither.

# Run a serena-daemon.sh subcommand under a wall-clock bound and ECHO its exit
# status, reporting an expiry as 124 the way timeout(1) would.
#
# DELIBERATE DUPLICATION: this is the same bash-native watchdog as
# serena_probe_rc() in scripts/setup-foundry.sh, which bounds the foundry
# preflight's doctor probe. Extracting a shared helper was considered and
# rejected — a new sourced file would be a third component with its own load
# path, and neither caller may fail if it is missing. Fix bugs in BOTH copies.
#
# No external binary is involved, and that is the entire point. timeout(1) is
# absent from stock macOS — it arrives only with coreutils, as gtimeout or as a
# PATH-shadowing gnubin — so detecting it and degrading to an unbounded call
# when it is missing leaves no per-invocation bound at all on the priority
# platform (A-007), which is precisely the machine population this plugin
# reaches through a public marketplace in an unknown install state
# (A-AUTO-007). There the hooks.json key was the only remaining bound, and it
# kills the hook MID-FLIGHT — losing the additionalContext warning that is this
# hook's whole purpose. A bound that is absent where it is most needed is not a
# bound. Everything below is bash builtins plus ps/kill/sleep, so it holds
# everywhere the script itself runs.
#
# All subcommand output is discarded: they print colored [serena] status lines
# for humans, and this script's stdout is reserved for the JSON envelope below —
# interleaving the two would corrupt it. The status is the ONLY thing read back;
# doctor's stdout text is explicitly not a contract (see the EXIT CODE CONTRACT
# header above cmd_doctor in serena-daemon.sh) and is never parsed here.
#
# A missing or non-executable script surfaces as 127, an expiry as 124 — both
# well outside doctor's 0-4 range, so both land in the default branch below.
#
# MUST be called inside a command substitution: the subshell is load-bearing
# twice over. It confines job control to the subcommand, and it is where bash
# writes the "Terminated" job notice for a killed one — so the 2>/dev/null at
# the call site discards that notice. Redirecting the `wait` builtin alone does
# not, and an undiscarded notice on this script's stderr would be the only
# output it produces that is not the JSON envelope.
daemon_rc() {
  local budget="$1"; shift
  local job_pid deadline expired rc pgid target

  # Job control, just long enough to fork the subcommand, so it becomes the
  # leader of its OWN process group. Killing the leader alone is not enough:
  # the latency in these subcommands is in what THEY shell out to, bash does not
  # forward signals to those, and a wedged service-manager or port query would
  # outlive its parent as an orphan still holding the very resource that timed
  # us out. Signalling the group takes the whole tree.
  #
  # stdin comes from /dev/null because under job control a background job that
  # reads the terminal is STOPPED by SIGTTIN rather than killed, and a stopped
  # job would sit in the wait below neither finishing nor dying. This hook is
  # handed the SessionStart event JSON on its own stdin, so there is genuinely
  # something there to be read.
  set -m
  "$DAEMON" "$@" >/dev/null 2>&1 </dev/null &
  job_pid=$!
  set +m

  # Poll, rather than race a background killer against the subcommand.
  # Escalation then runs on this thread, so there is no second process to cancel
  # and no window where cancelling it skips the follow-up KILL and strands a
  # child that ignored TERM.
  #
  # The deadline is wall-clock via $SECONDS rather than a count of iterations,
  # so the bound stays honest even where `sleep` rejects a fractional argument
  # (some busybox builds). There the guarded sleep fails, this loop spins, and
  # it still ends on the same second — degraded to a busy wait, never to an
  # unbounded one and never to a wrong verdict.
  deadline=$(( SECONDS + budget ))
  expired=false
  while kill -0 "$job_pid" 2>/dev/null; do
    if [ "$SECONDS" -ge "$deadline" ]; then
      expired=true
      break
    fi
    sleep "$DAEMON_POLL_INTERVAL" || true
  done

  if [ "$expired" = true ]; then
    # Never signal a process group we do not own. `kill -TERM -$pid` on a pid
    # that is NOT a group leader lands on THIS script's group — and this script
    # runs inside the user's session, so that is the user's whole session. So
    # the group id is read back and the negative form is used only where it
    # provably equals the pid; anything else, including a ps that answers
    # nothing, falls back to signalling the single process. Read here rather
    # than at fork time because the subcommand is known to be alive at this
    # instant, whereas one that had already exited would answer nothing.
    pgid="$(ps -o pgid= -p "$job_pid" 2>/dev/null | tr -d ' ' || true)"
    if [ "$pgid" = "$job_pid" ]; then
      target="-$job_pid"
    else
      target="$job_pid"
    fi
    kill -TERM "$target" 2>/dev/null || true
    sleep 0.5 || true
    kill -KILL "$target" 2>/dev/null || true
  fi

  rc=0
  wait "$job_pid" || rc=$?
  # A signalled subcommand reports 143 or 137. Normalise to 124 so "the bound
  # expired" carries one code on every path; all three would reach the same
  # default arm below regardless, but only one of them says why.
  if [ "$expired" = true ]; then rc=124; fi
  echo "$rc"
}

# Emit the SessionStart context envelope. Callers pass plain ASCII with no
# double quotes, backslashes or newlines, so the string needs no escaping.
emit_context() {
  printf '{"hookSpecificOutput":{"hookEventName":"SessionStart","additionalContext":"%s"}}\n' "$1"
}

# ── 1. Converge service state ────────────────────────────────────────────────
# Unconditional by design. reconcile is idempotent and silent once converged
# (A-014), so there is nothing to gate this behind — and this hook has no drift
# check of its own to gate it with. On a machine still holding the pre-rename
# agent, this is the call that finally clears it.
#
# Its status is deliberately DISCARDED — hence the `:` — rather than merely
# ignored: doctor runs immediately after and is the authority on what the model
# gets told. A reconcile that failed to converge shows up as doctor's 1 or 4
# verdict, and those messages say so. The command substitution is not optional
# even here; see the note on daemon_rc above.
: "$(daemon_rc "$RECONCILE_TIMEOUT" reconcile 2>/dev/null)"

# ── 2. Diagnose ──────────────────────────────────────────────────────────────
# Branch on the integer exit code only. Contract, verbatim from
# the EXIT CODE CONTRACT header above cmd_doctor in serena-daemon.sh:
#   0 healthy   1 not-installed   2 installed-but-stopped
#   3 running-but-unhealthy   4 drifted
# Precedence there is 1 > 2 > 3 > 4 > 0, and "installed" is tested first, so
# code 1 tells us nothing about health — the wording below reflects that.
#
# Every test on this value below is a STRING comparison, never `-eq`. A subshell
# that somehow died before echoing would leave it empty, and `[ "" -eq 2 ]` is a
# fatal error under set -e — which would abort the hook on precisely the path
# where it most needs to survive and report. Empty simply falls to the default
# arm, which already means "unrecognised status".
doctor_rc="$(daemon_rc "$DAEMON_TIMEOUT" doctor 2>/dev/null)"

# ── 2a. Settle before believing "stopped" ────────────────────────────────────
# The reconcile above may have just reloaded the service, and a reload is
# ASYNCHRONOUS: `launchctl load` returns as soon as launchd accepts the job,
# which is 0.020s, while the daemon it spawns under RunAtLoad needs another
# 1.473s to bind the port (both measured on a warm uv cache; a cold one is far
# slower, and repinning SERENA_PKG guarantees a cold cache on every machine's
# first session start after an upgrade).
#
# Inside that window nothing is on the port, so serena_mcp_healthy takes its
# cheap "nothing is listening" path — no handshake, no curl — and doctor
# summarises that as code 2. Code 2 is indistinguishable from a genuinely dead
# daemon from out here, and acting on it directly would call `start`, whose
# adoption guard cannot help: it adopts an already-bound port, and the whole
# premise of this window is that the port is NOT bound yet. There is nothing to
# adopt, so start would launch a SECOND, unsupervised instance racing launchd's
# own. Whichever binds first wins and the other dies on the address conflict.
# The two losses are not symmetric: if the unsupervised one loses it simply
# exits, but if the SUPERVISED one loses, KeepAlive restarts it straight back
# into the same conflict, and launchd has no give-up state to end that.
#
# So wait once, bounded, and ask again. Re-probing means re-running `doctor`,
# never inspecting the port from here: which processes hold it, and what that
# implies, stays entirely inside serena-daemon.sh.
#
# Only the stopped verdict pays for this. An ordinary healthy session start
# never reaches it and costs exactly what it did before.
if [ "$doctor_rc" = "2" ]; then
  # `|| true` for the same reason the watchdog's own sleeps carry it: a host
  # without a usable `sleep` must lose the settle window, not the whole hook.
  sleep "$SETTLE_SECONDS" || true
  # Re-probe replaces the verdict outright rather than being handled inline, so
  # a daemon that finished coming up during the wait flows into the branch that
  # actually matches its new state — including the healthy branch, which stays
  # silent. Still 2 afterwards means the wait was long enough and nothing came
  # up, so it really is down and the start branch below is correct.
  doctor_rc="$(daemon_rc "$DAEMON_TIMEOUT" doctor 2>/dev/null)"
fi

case "$doctor_rc" in
  0)
    # Healthy. Emit nothing: this hook fires on every single session start, so
    # an injection here would be noise in every session on every machine.
    :
    ;;

  1)
    # Not installed, and the reconcile above did not install it. Health is
    # unknown rather than bad — precedence hides it — so this stops short of
    # asserting Serena is dead.
    emit_context "Serena MCP service is NOT installed on this machine, and the automatic reconcile at session start did not install it. Serena/LSP symbol tooling may be unavailable this session: prefer Read/Grep/Glob over Serena symbol tools, and do not report any symbol as LSP-verified unless a Serena call actually succeeded. Diagnose with: serena-daemon.sh doctor"
    ;;

  2)
    # Installed but nothing serving the port. This is the one state the hook
    # repairs directly.
    start_rc="$(daemon_rc "$DAEMON_TIMEOUT" start 2>/dev/null)"
    if [ "$start_rc" = "0" ]; then
      # cmd_start confirms the process is alive but does NOT verify an MCP
      # handshake, so "started" is not yet "ready" — say exactly that rather
      # than staying silent and letting the first Serena call fail unexplained.
      emit_context "Serena MCP daemon was stopped and this session start has just launched it. It may need a few seconds before it accepts MCP connections, so an early Serena tool call can still fail: retry once, or fall back to Read/Grep/Glob."
    else
      emit_context "Serena MCP daemon is installed but stopped, and the automatic start attempt at session start FAILED. Serena/LSP symbol tooling is unavailable this session: use Read/Grep/Glob instead of Serena symbol tools, and do not report any symbol as LSP-verified. Check the daemon log with: serena-daemon.sh status"
    fi
    ;;

  3)
    # Something holds the port but no valid handshake came back, or health could
    # not be established at all. Both land here by contract.
    emit_context "Serena MCP daemon is running but did NOT answer a valid MCP handshake - it is wedged, or its health could not be verified. Serena/LSP symbol tooling is unavailable or unreliable this session: use Read/Grep/Glob instead of Serena symbol tools, and do not report any symbol as LSP-verified. Repair with: serena-daemon.sh restart"
    ;;

  4)
    # Drifted. Per contract this state is running AND healthy, so telling the
    # model that Serena is unavailable would be false. It is still worth one
    # line: reconcile ran seconds ago and did not converge, and an unconverged
    # drift that nobody reports is the exact silence this hook exists to end.
    emit_context "Serena MCP is running and healthy, so Serena tooling IS usable this session. However the installed OS service definition is still out of date after the automatic reconcile ran, which means reconcile could not converge it. This is a machine-state issue, not a tooling outage. Inspect with: serena-daemon.sh doctor"
    ;;

  *)
    # Outside doctor's 0-4 contract: timed out and killed (124), script missing
    # or not executable (127), or a code this wrapper predates.
    emit_context "Serena health check could not be completed at session start - the serena-daemon.sh doctor probe timed out, could not be run, or returned an unrecognized status. Treat Serena/LSP symbol tooling as possibly unavailable this session: prefer Read/Grep/Glob, and do not report any symbol as LSP-verified unless a Serena call actually succeeded. Diagnose with: serena-daemon.sh doctor"
    ;;
esac

# Unconditional. A failed reconcile, a failed start and a hung probe have all
# already been reported through additionalContext above; none of them may
# degrade the session by failing the hook.
exit 0
