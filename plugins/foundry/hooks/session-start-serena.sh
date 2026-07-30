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

# Per-invocation wall-clock bound, in seconds. At most three subcommands run
# (reconcile, doctor, start), so this caps the hook at 24s. The hook entry in
# hooks.json carries "timeout": 30 as a separate backstop, and the 6s gap
# between the two is deliberate: if every subcommand hung, the per-invocation
# bounds must expire FIRST so this script survives to report the hang through
# additionalContext. Were the two equal, the framework could kill the hook at
# the same moment and the failure report — the entire point of the hook — would
# be lost on exactly the machine that needed it. Raise both together or neither.
#
# Generous against real worst cases: doctor's own handshake is capped at 3s, and
# start blocks only on a 2s liveness sleep because it launches detached.
DAEMON_TIMEOUT=8

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
