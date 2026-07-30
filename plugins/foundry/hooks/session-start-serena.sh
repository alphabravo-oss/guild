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

# Per-invocation wall-clock bound, in seconds. At most FOUR subcommands run
# (reconcile, doctor, the post-settle doctor re-probe, start) plus one
# SETTLE_SECONDS sleep, so the worst case is 4*5 + 4 = 24s. The hook entry in
# hooks.json carries "timeout": 30 as a separate backstop, and the 6s gap
# between the two is deliberate: if every subcommand hung, the per-invocation
# bounds must expire FIRST so this script survives to report the hang through
# additionalContext. Were the two equal, the framework could kill the hook at
# the same moment and the failure report — the entire point of the hook — would
# be lost on exactly the machine that needed it. Raise both together or neither.
#
# The settle re-probe below was paid for by tightening this bound from 8s to 5s
# rather than by raising the manifest key: 3*8=24 and 4*5+4=24 are the same
# total, so the worst case and the 6s margin are both exactly what they were.
#
# 5s still clears every real worst case with headroom, all of which are bounded
# by construction rather than by hope: doctor's handshake is capped by curl's
# own --max-time 3, reconcile's heaviest path is that same 3s probe plus a
# launchctl unload/load measured at 0.02s, and start blocks only on a 2s
# liveness sleep because it launches detached. A subcommand that somehow does
# exceed 5s is killed and surfaces as 124, which the default branch reports —
# degraded, but still never blocking.
DAEMON_TIMEOUT=5

# Settle window, in seconds, between a doctor verdict of "installed but stopped"
# and acting on it. See the long comment at the settle block below for why this
# exists. Sized from measurement, not guesswork: `launchctl load` returns in
# 0.020s while the daemon it just spawned takes 1.473s more to bind the port
# (measured on a warm uv cache), so 4s is ~2.7x the observed window.
SETTLE_SECONDS=4

# Stock macOS ships no timeout(1) — it arrives only with coreutils, as gtimeout
# or as a PATH-shadowing gnubin. This plugin ships through a public marketplace
# to machines in an unknown install state (A-AUTO-007), so the binary is
# detected rather than assumed. When it is absent the per-invocation bound
# degrades to nothing and the hooks.json "timeout" key is the sole hard bound;
# that key is why constraint LR-015 holds on every platform, not just this one.
TIMEOUT_BIN=""
if command -v timeout >/dev/null 2>&1; then
  TIMEOUT_BIN="timeout"
elif command -v gtimeout >/dev/null 2>&1; then
  TIMEOUT_BIN="gtimeout"
fi

# Run a serena-daemon.sh subcommand and hand back its exit status.
#
# All output is discarded: the subcommands print colored [serena] status lines
# for humans, and this script's stdout is reserved for the JSON envelope below —
# interleaving the two would corrupt it. The status is the ONLY thing read back;
# doctor's stdout text is explicitly not a contract (see the EXIT CODE CONTRACT
# header above cmd_doctor in serena-daemon.sh) and is never parsed here.
#
# A missing script or a timeout kill surfaces as a status well outside doctor's
# 0-4 range (127 and 124 respectively), which the default branch catches.
daemon_rc() {
  local rc=0
  if [ -n "$TIMEOUT_BIN" ]; then
    "$TIMEOUT_BIN" "$DAEMON_TIMEOUT" "$DAEMON" "$@" >/dev/null 2>&1 || rc=$?
  else
    "$DAEMON" "$@" >/dev/null 2>&1 || rc=$?
  fi
  return "$rc"
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
# Its status is deliberately not captured: doctor runs immediately after and is
# the authority on what the model gets told. A reconcile that failed to converge
# shows up as doctor's 1 or 4 verdict, and those messages say so.
daemon_rc reconcile || true

# ── 2. Diagnose ──────────────────────────────────────────────────────────────
# Branch on the integer exit code only. Contract, verbatim from
# the EXIT CODE CONTRACT header above cmd_doctor in serena-daemon.sh:
#   0 healthy   1 not-installed   2 installed-but-stopped
#   3 running-but-unhealthy   4 drifted
# Precedence there is 1 > 2 > 3 > 4 > 0, and "installed" is tested first, so
# code 1 tells us nothing about health — the wording below reflects that.
doctor_rc=0
daemon_rc doctor || doctor_rc=$?

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
if [ "$doctor_rc" -eq 2 ]; then
  sleep "$SETTLE_SECONDS"
  # Re-probe replaces the verdict outright rather than being handled inline, so
  # a daemon that finished coming up during the wait flows into the branch that
  # actually matches its new state — including the healthy branch, which stays
  # silent. Still 2 afterwards means the wait was long enough and nothing came
  # up, so it really is down and the start branch below is correct.
  doctor_rc=0
  daemon_rc doctor || doctor_rc=$?
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
    start_rc=0
    daemon_rc start || start_rc=$?
    if [ "$start_rc" -eq 0 ]; then
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
