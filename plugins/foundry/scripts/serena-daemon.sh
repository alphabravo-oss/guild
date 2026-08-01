#!/usr/bin/env bash
# serena-daemon.sh — Manage the shared Serena MCP HTTP daemon.
#
# All Claude Code sessions connect to one long-lived Serena MCP server over
# streamable-HTTP at localhost:9121 instead of each session forking its own
# stdio process. This script starts/stops/inspects that daemon and can install
# an OS service so it survives reboots.
#
# Usage: serena-daemon.sh <subcommand>
#
# Subcommands: start stop status doctor reconcile restart install-service
#              uninstall-service
#
# Streamable-HTTP transport only: it is what lets ONE long-lived server serve
# many concurrent Claude Code sessions, which is the entire point of a shared
# daemon (stdio would be a separate process per client).

set -euo pipefail

# ── Colors ────────────────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
RESET='\033[0m'

info()  { printf "${CYAN}[serena]${RESET} %s\n" "$*"; }
ok()    { printf "${GREEN}[serena]${RESET} %s\n" "$*"; }
warn()  { printf "${YELLOW}[serena]${RESET} %s\n" "$*"; }
fail()  { printf "${RED}[serena]${RESET} %s\n" "$*" >&2; }

# ── Constants ─────────────────────────────────────────────────────────────────
PORT=9121
# Serena ships on PyPI as the `serena-agent` project, whose console script is
# named `serena` — the two names differ, so every launch site (manual start,
# launchd plist) must go through `uvx --from "$SERENA_PKG" serena` rather than
# naming either one alone.
#
# Pinned with `==`, and sourced from PyPI rather than a git URL, because uvx
# resolves this specifier on EVERY daemon start — including the unattended
# RunAtLoad start at login. A git URL made each of those starts require working
# network AND git, and floating HEAD silently rode unreleased upstream changes
# (e.g. the in-flight languages -> language_servers rename). An exact pin names
# one immutable artifact that uv serves from its cache, so starts are
# reproducible. Bump deliberately, and keep both launch sites in agreement.
SERENA_PKG="serena-agent==1.6.1"
PID_FILE="$HOME/.serena-daemon.pid"
LOG_FILE="$HOME/.serena-daemon.log"
PLIST_FILE="$HOME/Library/LaunchAgents/com.guild.serena.plist"
# Pre-rename label. Installs that predate the codsworth -> guild rename still have
# this agent loaded and holding $PORT, so install/uninstall must clear it too.
LEGACY_PLIST_FILE="$HOME/Library/LaunchAgents/com.codsworth.serena.plist"
SYSTEMD_FILE="$HOME/.config/systemd/user/serena-daemon.service"

# PATH handed to the daemon by BOTH service definitions, and the search list used
# to resolve the daemon's own launcher. One list serves both jobs, so the
# launcher can never be resolved out of a directory the daemon is not then given.
#
# FIXED, never read from the environment. The rendered definition is the drift
# oracle: doctor byte-compares it against the installed artifact and reconcile
# rewrites and RELOADS the service on any difference. A list built from the
# invoking shell's PATH answered differently for every caller — a login shell
# carrying a node version-manager shim, the SessionStart hook without it — and
# each disagreement read as drift, so the daemon was killed and the LSP cold-
# reindexed on every single session, announcing itself as a repair each time.
# Determinism here is what makes that byte comparison mean anything (GI-004),
# and what lets a converged session stay silent (GI-008).
#
# WHAT IT HAS TO COVER. Serena's LSP manager spawns its language servers
# (bash-language-server, typescript-language-server) by BARE NAME. A daemon
# without node's directory on this PATH loads, answers an MCP handshake, and then
# fails every symbol lookup with "node is not installed" — healthy on every axis
# but the one that matters. So the user-level install prefixes lead (uv, pipx,
# and both Homebrew architectures — one machine's prefix is the other's dead
# entry), and the system directories trail, including launchd's default in full:
# widening a service PATH must never narrow it.
#
# The same string is rendered on both platforms, deliberately. A directory that
# does not exist on a given machine is skipped at lookup time and costs nothing,
# whereas a per-platform list is exactly how the two arms diverged before — one
# carried a node directory and the other silently did not.
#
# A node installed ONLY under a version manager that mutates PATH rather than
# keeping a fixed shim directory is not found here. That is the deliberate trade:
# locating it means reading the caller's environment, which is precisely the
# instability this constant exists to remove.
SERVICE_PATH_DIRS="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"

# Absolute path to this script (used by the systemd unit's ExecStart so the
# PID file is written on every launch — including service-managed boots).
SCRIPT_PATH="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd -P)/$(basename -- "${BASH_SOURCE[0]}")"

# Post-load health-probe budgets, in seconds. See await_daemon_healthy() for why
# a budget is polled rather than slept, and why either number is spent only when
# something is actually wrong.
#
# install-service is a MANUAL, interactive command with no external watchdog, so
# it can afford to wait out a cold uv cache — and one is guaranteed on every
# machine's first install after SERENA_PKG is repinned, because uvx must fetch
# and unpack the pinned artifact before the server binds anything.
INSTALL_PROBE_SECONDS=15
# reconcile runs unattended from the SessionStart hook on every single session
# (hooks/session-start-serena.sh), so its budget is sized to that caller rather
# than to a cold cache. That hook bounds its subcommands UNEQUALLY, and on
# purpose: reconcile is guarded by its own RECONCILE_TIMEOUT, deliberately
# larger than the DAEMON_TIMEOUT covering doctor and start, because reconcile's
# repair path is the longest one the hook issues. So this number is NOT squeezed
# under a bound shared with the other subcommands — the dependency runs the
# other way. That hook derives its bound partly from THIS constant, counting the
# post-load probe as this budget plus one more curl --max-time. Raise this and
# RECONCILE_TIMEOUT must be re-derived; check it by name over there.
#
# 4s is also the window that hook's SETTLE_SECONDS uses for this exact wait, off
# the same measurement: `launchctl load` returns in 0.020s and the daemon it
# spawns binds 1.473s later. Because the poll is adaptive, a healthy reload
# returns at about that 1.5s mark, not at 4s.
RECONCILE_PROBE_SECONDS=4

# Wall-clock bounds, in seconds, on ONE service-manager call issued by
# cmd_reconcile's repair path. Enforced by run_bounded(). Only the repair path
# pays them, so a converged session start neither waits on nor prints anything
# because of them (GI-008).
#
# WHY THE BOUND LIVES HERE and not only in the caller. The SessionStart hook
# already runs this subcommand under its own RECONCILE_TIMEOUT watchdog, but
# that bounds the caller's PATIENCE, not this process's progress. `launchctl
# unload` of a job that ignores SIGTERM blocks for launchd's own ExitTimeOut,
# and `systemctl restart` blocks on unit start for systemd's TimeoutStartSec;
# both defaults exceed anything a session start tolerates. The watchdog's only
# recourse against either is to kill this process mid-repair, which can leave
# the definition written and the service NOT reloaded, and makes exit 4 and
# exit 5 unreachable on exactly the legacy-agent (A-003) and wedged-daemon
# branches this file exists for. A bound INSIDE the call site is what lets the
# repair either finish or report honestly.
#
# The two numbers are UNEQUAL, deliberately, because the calls are not alike.
#
# A supervisor-only call — both launchctl unloads, launchctl load, systemctl
# daemon-reload, systemctl enable — talks to the service manager and returns.
# It does NOT wait on the daemon: `launchctl load` returns in 0.020s while the
# daemon it just spawned takes 1.473s more to bind the port, which is the same
# measurement RECONCILE_PROBE_SECONDS above is drawn from. 2s is 100x that, so
# no working machine reaches it.
RECONCILE_SERVICE_CALL_SECONDS=2

# `systemctl --user restart`, by contrast, BLOCKS on the unit starting, and this
# unit is Type=forking with ExecStart routed through this script's own `start` —
# which takes its 2s liveness sleep before returning. A real Linux restart is
# therefore seconds, not milliseconds, and bounding it at the figure above would
# manufacture a false exit 4 on every healthy repair while systemd carried the
# job on regardless (killing the systemctl client does not cancel a queued job).
# 8s clears the observed path with headroom while staying an order of magnitude
# under systemd's own default.
RECONCILE_UNIT_START_SECONDS=8

# Poll granularity while waiting on a bounded call, in seconds. Sets how long
# the common case over-waits after the call has already answered against how
# many `sleep` forks the worst case costs.
SERVICE_CALL_POLL_INTERVAL=0.2

# RESIDUAL, stated rather than papered over. These bounds make the repair path
# finite where it was previously open-ended, and on the path that ENDS in an
# expiry they keep it well inside the hook's patience — which is what makes
# exit 4 reachable again. They do not make every path fit: a run where the
# best-effort calls burn their full bounds AND the status-bearing call then
# succeeds AND the post-load probe runs to RECONCILE_PROBE_SECONDS can still
# outlast the hook's watchdog. That compound is improbable — a wedged unload
# normally makes the following load fail, which takes the expiry path — and it
# truncates only the exit-5 REPORT, after every mutation has already happened.
# Closing it needs the hook's own bound re-derived against these; that
# arithmetic is maintained beside RECONCILE_TIMEOUT in the hook, which is the
# only place it can stay true. Check it by name over there if either moves.

# ── Helpers ───────────────────────────────────────────────────────────────────
# Echo the PID of EVERY process currently holding $PORT — Serena or not, one per
# line. This is the ONLY port-resolver chain in this script; every other function
# that needs to know who holds the port comes through here.
#
# Three resolvers are tried in order because no single one is universally
# present: lsof ships on macOS but is absent from Debian-slim, Alpine and most
# container images, where iproute2's `ss` or `fuser` generally is. lsof stays
# first so behaviour on a host that has it is unchanged.
#
# Deliberately UNFILTERED. Callers asking "is a Serena server on the port?" want
# serena_port_pids() below, which filters this by cmdline. Callers asking "is
# ANYONE squatting the port?" — cmd_start, deciding between adopting a daemon,
# refusing a contended port, and launching — need the raw census, because the
# holders the filter discards are exactly the foreign processes that make a
# launch fail. Deriving both readings from one chain is the point: a private
# inline copy of it in cmd_start is what let that step keep an lsof-only view
# after this chain grew its ss and fuser fallbacks.
#
# ALWAYS returns 0, reporting only through stdout — same contract, and for the
# same reason, as serena_port_pids(). An empty result is ambiguous by
# construction ("port free" and "nothing could look" are the same empty string);
# serena_port_owners_known() is what tells those apart.
serena_port_holder_pids() {
  local pids=""
  if command -v lsof >/dev/null 2>&1; then
    pids="$(lsof -i :"$PORT" -t 2>/dev/null || true)"
  elif command -v ss >/dev/null 2>&1; then
    # -p names the owner as `users:(("serena",pid=1234,fd=7))`, and needs no
    # root for our own processes. -l keeps this to LISTEN sockets, so connected
    # clients never enter the set. A busybox ss that rejects the filter
    # expression or omits owner info simply yields nothing here, which degrades
    # into the unknown verdict rather than into a false "not running".
    pids="$(ss -tlnp "sport = :$PORT" 2>/dev/null \
      | grep -oE 'pid=[0-9]+' | cut -d= -f2 || true)"
  elif command -v fuser >/dev/null 2>&1; then
    # Writes the "9121/tcp:" label to stderr and the bare PIDs to stdout.
    pids="$(fuser "$PORT"/tcp 2>/dev/null || true)"
  fi
  printf '%s' "$pids"
}

# Echo the PID(s) of any Serena MCP server process currently bound to $PORT.
#
# uvx (uv tool run) does NOT serve under the PID captured by `$!` at launch — it
# spawns/execs the real server under a different PID. So the recorded PID alone
# cannot be trusted to find, monitor, or terminate the daemon. Resolving by port
# is the only reliable way to locate the actual long-lived process, regardless of
# how it was started (manual `start` or an OS service manager).
#
# This is serena_port_holder_pids() narrowed to Serena: whichever resolver
# answered up there, every candidate PID is confirmed to be a Serena server by
# cmdline — never skipped for a "trusted" tool, because cmd_stop SIGKILLs
# everything this function emits and a misattributed PID would terminate an
# unrelated user process.
#
# ALWAYS returns 0, reporting only through stdout: cmd_stop consumes this as a
# bare `targets="$targets $(serena_port_pids)"`, whose exit status IS the
# substitution's, so a non-zero return here would abort cmd_stop under set -e.
# An empty result is therefore ambiguous by construction — "no Serena on the
# port" and "nothing could look" are the same empty string. Any caller that
# must tell those apart asks serena_port_owners_known().
serena_port_pids() {
  local pids="" p cmdline out=""
  pids="$(serena_port_holder_pids || true)"
  for p in $pids; do
    cmdline="$(ps -p "$p" -o command= 2>/dev/null || true)"
    # Match the server invocation, not a package name: uvx execs the `serena`
    # console script out of $SERENA_PKG, so the real process runs as
    # `.../bin/serena start-mcp-server ...` no matter which package version is
    # pinned — which is what keeps this match surviving a bump of that pin.
    if printf '%s' "$cmdline" | grep -q "start-mcp-server"; then
      out="$out $p"
    fi
  done
  printf '%s' "${out# }"
}

# Return 0 when the set of processes holding $PORT was authoritatively
# enumerated — a resolver ran to completion and either found the port free or
# named every holder. Return 1 when it was not, because nothing could look or a
# holder could not be attributed to a PID.
#
# This exists solely to qualify an EMPTY serena_port_pids(). Empty there means
# "no Serena server was identified on $PORT", which is a real "not running"
# only if something actually managed to look. Reading it that way when nothing
# could look is what reported a live, answering daemon as stopped on hosts
# where lsof is not installed.
#
# Note this deliberately answers "was the holder set enumerated?", NOT "is the
# port occupied?". The two differ on the case that matters: when a resolver
# names a holder that is not Serena — a foreign process, or the pre-rename
# com.codsworth agent contending for this same port (A-003) — the holder set IS
# enumerated, so "Serena is not running" is earned and code 1 is correct.
#
# Consulted only on the cold path of an already-empty PID list, so a healthy
# daemon never pays for it.
serena_port_owners_known() {
  local ss_out="" listen=""

  if command -v lsof >/dev/null 2>&1; then
    # `lsof -i :PORT -t` prints a PID for every holder and nothing at all when
    # the port is free, so a run enumerates the holder set either way. This is
    # the resolver serena_port_pids already prefers, so trusting it here keeps
    # behaviour on lsof-bearing hosts exactly as it was.
    return 0
  fi

  if command -v ss >/dev/null 2>&1; then
    # Non-zero means the flags or filter expression were rejected and nothing
    # was learned — busybox ss and macOS's iproute2mac ss both exit non-zero
    # with empty stdout, and reading that silence as "port free" would recreate
    # this very defect on those hosts.
    if ! ss_out="$(ss -tlnp "sport = :$PORT" 2>/dev/null)"; then
      return 1
    fi
    listen="$(printf '%s\n' "$ss_out" | grep -E "[:.]$PORT([^0-9]|$)" || true)"
    if [ -z "$listen" ]; then
      return 0
    fi
    # A listener exists, so the answer is authoritative only if -p actually
    # attributed it: ss can report occupancy without naming an owner, which is
    # the case cmd_start's ss branch (:205) already notes it cannot resolve.
    if printf '%s' "$listen" | grep -q 'pid='; then
      return 0
    fi
    return 1
  fi

  if command -v fuser >/dev/null 2>&1; then
    # Trusted for a positive answer only. macOS ships a BSD fuser that takes
    # file paths and no port spec: it answers `fuser 9121/tcp` with exit 1 and
    # empty stdout, byte for byte what Linux fuser reports for a genuinely free
    # port. Indistinguishable from here, so silence is never read as "free".
    if [ -n "$(fuser "$PORT"/tcp 2>/dev/null | tr -d '[:space:]' || true)" ]; then
      return 0
    fi
    return 1
  fi

  # No resolver at all.
  return 1
}

# Return 0 iff Serena is genuinely serving MCP on $PORT.
#
# A bound port is NOT evidence of health: a wedged Serena keeps its listener
# open while answering nothing (oraios/serena#1367), so an lsof-only check
# reports a hung daemon as healthy. This probe is layered — the cheap port
# check gates a real MCP `initialize` handshake, and only a response actually
# carrying protocolVersion + serverInfo earns a healthy verdict.
#
# Exit codes (stable contract — callers branch on these and MUST NOT parse
# output; this function prints nothing):
#   0  healthy      — handshake returned a valid protocolVersion/serverInfo
#   1  not running  — a port resolver looked and $PORT is free, so nothing is
#                     serving. Reserved for a verdict the cheap check EARNED;
#                     never inferred from its inability to look.
#   2  unresponsive — a Serena process was identified on $PORT but no valid
#                     handshake came back; the wedged-daemon case a restart
#                     is expected to clear
#   3  unverifiable — MCP-level health could not be established either way:
#                     curl is absent, or nothing could confirm whether the
#                     port is even held (no lsof/ss/fuser, or a holder whose
#                     owner cannot be named) and the handshake did not answer.
#                     Deliberately not 0 — an unprobed daemon is never
#                     "verified healthy". Equally deliberately not 1: a failed
#                     *look* is not a failed *probe*, and reporting one as the
#                     other is what made a live daemon read as dead. Callers
#                     should report uncertainty here, not force a restart.
serena_mcp_healthy() {
  local pids="" body="" req="" re_proto="" re_info=""

  # LAYER 1 — cheap, local, no network: is a Serena server even on the port?
  # Reuses the resolver above rather than re-parsing lsof.
  pids="$(serena_port_pids || true)"
  if [ -z "$pids" ]; then
    # Empty is ambiguous on its own: it means "no Serena server identified",
    # which is a real "not running" only when a resolver actually enumerated
    # the port's holders. Answering the other cases the same way is what
    # reported a healthy, answering daemon as stopped wherever lsof is not
    # installed (Debian-slim, Alpine, most container images).
    if serena_port_owners_known; then
      # Earned: something looked and found no Serena server, so nothing can be
      # serving. Return without paying for an HTTP request that cannot succeed.
      return 1
    fi
    # Nothing could look, or a holder could not be attributed — and that holder
    # may well be Serena. The handshake below is the only remaining witness and
    # is already bounded, so let it decide rather than guessing from absent
    # port evidence.
  fi

  # curl is a new dependency that setup-prereqs.sh does not check for (it does
  # not even hard-fail for uvx), so follow the optional-lsof precedent above
  # and degrade quietly instead of erroring or hanging.
  if ! command -v curl >/dev/null 2>&1; then
    return 3
  fi

  # LAYER 2 — MCP `initialize` handshake against the streamable-HTTP endpoint.
  # The Accept header must offer BOTH types: the transport answers a request
  # POST with either application/json or an SSE frame, and omitting either
  # makes the server reject the request outright. The endpoint is loopback
  # with no authentication, so no credential is sent. Both timeouts are
  # explicit and finite because this runs from the SessionStart hook and must
  # never stall a session — --max-time bounds the whole call, not just connect.
  req='{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"serena-daemon-healthcheck","version":"1.0"}}}'
  # `|| true`: refused / timed-out / non-2xx are EXPECTED outcomes of an
  # unhealthy probe, and set -e must not turn them into a caller abort.
  # --fail makes curl treat a 4xx/5xx as failure and emit no body, so an error
  # page can never be mistaken for a handshake.
  body="$(curl -s --fail \
    --connect-timeout 2 --max-time 3 \
    -X POST "http://localhost:$PORT/mcp" \
    -H 'Content-Type: application/json' \
    -H 'Accept: application/json, text/event-stream' \
    --data "$req" 2>/dev/null || true)"

  # A response only proves health if it carries the handshake fields, so an
  # empty, truncated, or malformed body falls through to the unhealthy return.
  # Matched with bash's own =~ rather than `printf | grep` so that pipefail
  # cannot misreport a SIGPIPE'd printf as a failed match. Raw JSON and an SSE
  # `data:` frame both carry these fields on one line, so this is agnostic to
  # which framing the transport chose.
  re_proto='"protocolVersion"[[:space:]]*:[[:space:]]*"[^"]+"'
  re_info='"serverInfo"[[:space:]]*:[[:space:]]*[{]'
  if [[ "$body" =~ $re_proto ]] && [[ "$body" =~ $re_info ]]; then
    # Authoritative on its own: something answered a real MCP handshake on
    # $PORT. This is why a host with no port resolver at all still gets a
    # correct healthy verdict, and why no /proc/net/tcp walker is needed.
    return 0
  fi

  # No valid handshake. What that proves depends on what Layer 1 could see:
  #   a Serena PID was identified -> the server is there and is not answering,
  #                                  which is the wedged daemon (#1367).
  #   Layer 1 was ambiguous       -> nothing confirmed a Serena process and
  #                                  nothing proved the port free, so neither
  #                                  "wedged" nor "stopped" is honest here.
  if [ -n "$pids" ]; then
    return 2
  fi
  return 3
}

# Poll serena_mcp_healthy until it answers healthy or <budget> seconds elapse.
# Prints nothing — the caller owns the reporting, because what a failure MEANS
# differs between a manual install and an unattended reconcile.
#
# WHY THIS EXISTS. `launchctl load` and `systemctl restart` report that the
# SUPERVISOR ACCEPTED THE JOB, not that the job can run: launchd loads a plist
# whose command can never be satisfied without complaint. Reporting "service
# installed and loaded" on the strength of that acceptance alone is exactly the
# gap the 29-day silent failure went through. So nothing downstream of a load
# may claim success until something has actually answered on $PORT. The probe
# itself is serena_mcp_healthy and is never reimplemented here (GI-001).
#
# WHY A POLL AND NOT A FIXED SLEEP. A reload is asynchronous — `launchctl load`
# returns in 0.020s while the daemon it spawns needs another 1.473s to bind
# (measured; the SessionStart hook sizes its SETTLE_SECONDS off the same two
# figures). A fixed sleep would charge that wait to every SUCCESS. Polling
# charges it only until the daemon answers, so the budget below is spent in full
# only when something is genuinely wrong — which is the case that can afford to
# wait.
#
# BOUNDED BY CONSTRUCTION, never by hope, because reconcile calls this from the
# SessionStart hook and a foundry run must not block on a dead Serena (GI-003).
# Each probe is independently capped by curl's --connect-timeout 2 / --max-time 3
# inside serena_mcp_healthy, and the loop carries TWO independent bounds because
# either one alone has a failure mode:
#
#   Wall clock is the TIGHT bound. It is the only one that also covers a probe
#   that is itself slow — a wedged daemon makes curl burn its full 3s every
#   time, which a probe count cannot bound to anything useful.
#
#   Probe count is the FAILSAFE. A deadline computed from `date` loops FOREVER
#   on a host where that call fails: an empty `now` makes the `-ge` test error,
#   and an errored test inside `if` is merely false, never a break. Counting
#   needs nothing that can fail. Belt and braces is warranted here and nowhere
#   else in this file, because this is the one loop that runs unattended on
#   every session start on machines nobody debugging it can see (A-AUTO-007).
#
# Worst case is therefore <budget> + 3s in wall-clock terms, and at most
# <budget>+1 probes if the clock cannot be read at all.
#
# Return codes:
#   0  healthy      — a valid MCP handshake came back inside the budget
#   1  unhealthy    — the budget expired and the probe could say so on evidence
#   2  unverifiable — MCP health could not be established either way for the
#                     whole budget (curl absent, or nothing could read the port
#                     and the handshake never answered). Deliberately NOT 1:
#                     failing an install because an OPTIONAL tool is missing is
#                     a false negative, and this file already degrades quietly
#                     rather than erroring when curl or lsof is absent.
await_daemon_healthy() {
  local budget="${1:-0}" deadline="" now="" rc=0 last=3 probes=0

  # `|| true`: a host that cannot read its own clock still gets the failsafe
  # bound below, so an unreadable clock degrades the tightness of the bound
  # rather than removing it.
  now="$(date +%s 2>/dev/null || true)"
  if [ -n "$now" ]; then
    deadline=$(( now + budget ))
  fi

  while :; do
    # `|| rc=$?` (never bare): every non-zero verdict here is an EXPECTED
    # outcome of probing a daemon that may still be starting, and set -e must
    # not turn one into a caller abort.
    rc=0
    serena_mcp_healthy || rc=$?
    if [ "$rc" -eq 0 ]; then
      return 0
    fi
    last="$rc"

    # Failsafe bound — depends on nothing external.
    probes=$(( probes + 1 ))
    if [ "$probes" -gt "$budget" ]; then
      break
    fi

    # Tight bound — skipped entirely when the clock could not be read.
    if [ -n "$deadline" ]; then
      now="$(date +%s 2>/dev/null || true)"
      if [ -z "$now" ] || [ "$now" -ge "$deadline" ]; then
        break
      fi
    fi

    sleep 1
  done

  # Distinguish "it is not coming up" from "nothing could tell". Only the
  # probe's own unverifiable verdict (3) becomes 2 here; a port resolver that
  # earned "not running" (1) or found a wedged server (2) is real evidence.
  if [ "$last" -eq 3 ]; then
    return 2
  fi
  return 1
}

# ── start ─────────────────────────────────────────────────────────────────────
# Idempotent. Never spawns a second process if one is already running.
cmd_start() {
  # 1. PID file liveness check.
  if [ -f "$PID_FILE" ]; then
    local pid pid_cmdline
    pid="$(cat "$PID_FILE" 2>/dev/null || true)"
    if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
      # `kill -0` proves only that SOME process holds this PID, not that it is
      # ours: PIDs are recycled, so after a reboot an unrelated process can
      # inherit the recorded number and make `start` report "already running"
      # while no daemon exists. Confirm it really is a Serena server using the
      # same ps/start-mcp-server match serena_port_pids() applies.
      pid_cmdline="$(ps -p "$pid" -o command= 2>/dev/null || true)"
      if printf '%s' "$pid_cmdline" | grep -q "start-mcp-server"; then
        ok "Serena daemon already running (PID $pid)"
        exit 0
      fi
    fi
    # Stale PID file — the process is gone, or the PID was recycled by an
    # unrelated process. Either way the record is worthless: remove it and fall
    # through to the port check, which resolves the daemon reliably.
    rm -f "$PID_FILE"
  fi

  # 2. Port in-use check, delegated entirely to the shared resolvers.
  #
  # This step used to carry its own inline lsof -> ss chain, and that private
  # copy went stale the moment serena_port_pids() grew ss and fuser fallbacks:
  # the adopt-a-running-daemon path lived only in the lsof arm, so an ss-only
  # host (Debian-slim, Alpine) reported a perfectly healthy daemon as "port in
  # use by another process", and a fuser-only host ran no port check whatsoever
  # — the elif chain simply ended — and launched a duplicate against a live
  # server. Reading both questions off the one chain in the Helpers section is
  # what keeps every resolver on equal footing.
  local serena_pids="" port_holders=""
  # `|| true` is load-bearing here and below: these are plain assignments, not
  # `local` declarations, so under set -e the command substitution's status is
  # the statement's.
  serena_pids="$(serena_port_pids || true)"
  if [ -n "$serena_pids" ]; then
    # Serena already on the port but no valid PID file — adopt it. First PID
    # only, matching how step 6 below resolves real_pid.
    local found_serena="${serena_pids%% *}"
    echo "$found_serena" > "$PID_FILE"
    ok "Serena daemon already running (PID $found_serena)"
    exit 0
  fi

  port_holders="$(serena_port_holder_pids || true)"
  if [ -n "$port_holders" ]; then
    # Something holds the port and the cmdline filter cleared none of them as a
    # Serena server, so this is genuine contention. Refuse rather than spawn a
    # process that cannot bind.
    fail "port $PORT in use by another process"
    exit 1
  fi

  # Nothing was found holding the port — which is a real "free" only if a
  # resolver actually managed to look. serena_port_owners_known() separates the
  # two, and they differ only in whether the uncertainty is said out loud: both
  # go on to launch.
  #
  # Launching is the right default even when the port could not be read. This
  # path is now automatic — the SessionStart hook runs `start` whenever doctor
  # reports nothing serving — so refusing on a host with no usable resolver
  # would leave that host permanently without a daemon, strictly worse than the
  # inline check this replaces, which in that situation fell through and
  # launched silently. The launch is also self-limiting: if something really
  # does hold the port, the bind fails and step 6 reports it with the same
  # exit 1. What changes is that "I could not tell" is no longer silently
  # indistinguishable from "the port is free".
  if ! serena_port_owners_known; then
    warn "could not determine whether port $PORT is free; starting anyway"
  fi

  # 3. uvx availability.
  if ! command -v uvx >/dev/null 2>&1; then
    fail "uvx not found. Install uv: curl -LsSf https://astral.sh/uv/install.sh | sh"
    exit 1
  fi

  # 4. Launch detached.
  #
  # The daemon is a machine-level service: validate TLS against the SYSTEM
  # trust store, not whatever project-scoped SSL_CERT_FILE the launching shell
  # happens to carry (a dev-CA bundle with no public roots makes every uv
  # PyPI fetch fail with "invalid peer certificate: UnknownIssuer").
  # Corporate/proxy CAs belong in the system store (update-ca-certificates).
  if [ -f /etc/ssl/certs/ca-certificates.crt ]; then
    export SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt
  fi
  # Launch site 1 of 2. The plist ProgramArguments array in
  # render_service_definition() is the other, and the two carry an IDENTICAL
  # flag set — a divergence between them is what produced the original incident.
  nohup uvx --from "$SERENA_PKG" serena start-mcp-server \
    --context claude-code \
    --transport streamable-http \
    --port "$PORT" \
    >> "$LOG_FILE" 2>&1 &
  local daemon_pid=$!

  # 5. Record PID.
  echo "$daemon_pid" > "$PID_FILE"

  # 6. Verify startup.
  sleep 2
  if kill -0 "$daemon_pid" 2>/dev/null; then
    # Resolve the real listening PID: uvx serves under a PID that differs from
    # $!. Record the actual port owner so `status`/`stop` and the systemd
    # PIDFile track the long-lived daemon rather than the transient launcher.
    local real_pid
    real_pid="$(serena_port_pids | awk '{print $1}')"
    if [ -n "$real_pid" ]; then
      daemon_pid="$real_pid"
      echo "$daemon_pid" > "$PID_FILE"
    fi
    ok "Serena daemon started (PID $daemon_pid). Log: $LOG_FILE"
    exit 0
  else
    rm -f "$PID_FILE"
    fail "Serena daemon failed to start. Check log: $LOG_FILE"
    exit 1
  fi
}

# ── stop ──────────────────────────────────────────────────────────────────────
# Terminates the real Serena server process and removes the PID file.
#
# Targets BOTH the recorded PID and any Serena server bound to the port.
# The recorded PID alone is insufficient: uvx serves the daemon under a PID that
# differs from the launcher's $!, so killing only the recorded PID would leave
# the actual server running (the failure this guards against).
cmd_stop() {
  local pid=""
  if [ -f "$PID_FILE" ]; then
    pid="$(cat "$PID_FILE" 2>/dev/null || true)"
  fi

  # Build the target set: recorded PID (if alive and genuinely ours) plus every
  # Serena server on $PORT. Duplicates are harmless — a second kill on the same
  # PID is a no-op. A WRONG pid is not: every member of this set is about to be
  # sent SIGTERM and, ten seconds later, SIGKILL.
  local targets="" pid_cmdline
  if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
    # `kill -0` proves only that SOME process holds this PID, not that it is
    # ours: PIDs are recycled, so a stale PID file whose number has been reused
    # would make `stop` terminate an arbitrary unrelated user process — and do
    # it silently, because the port-derived half below still stops the real
    # daemon and the command reports success. Confirm it really is a Serena
    # server using the same ps/start-mcp-server match serena_port_pids()
    # applies. Validating a PID away never weakens `stop`: every genuine Serena
    # process bound to the port is contributed by serena_port_pids() regardless.
    pid_cmdline="$(ps -p "$pid" -o command= 2>/dev/null || true)"
    if printf '%s' "$pid_cmdline" | grep -q "start-mcp-server"; then
      targets="$pid"
    fi
  fi
  targets="$targets $(serena_port_pids)"

  if [ -z "$(printf '%s' "$targets" | tr -d '[:space:]')" ]; then
    warn "Serena daemon is not running"
    rm -f "$PID_FILE"
    exit 0
  fi

  # Graceful SIGTERM to all targets.
  local t
  for t in $targets; do
    kill -TERM "$t" 2>/dev/null || true
  done

  # Wait up to 10s for every target to exit.
  local i alive
  for i in $(seq 1 10); do
    alive=""
    for t in $targets; do
      if kill -0 "$t" 2>/dev/null; then
        alive="yes"
      fi
    done
    if [ -z "$alive" ]; then
      break
    fi
    sleep 1
  done

  # SIGKILL fallback for any survivor.
  for t in $targets; do
    if kill -0 "$t" 2>/dev/null; then
      kill -KILL "$t" 2>/dev/null || true
    fi
  done

  rm -f "$PID_FILE"
  ok "Serena daemon stopped."
  exit 0
}

# ── status ────────────────────────────────────────────────────────────────────
cmd_status() {
  # 1. Trust the PID file only when it points at a live process that is
  #    genuinely ours.
  if [ -f "$PID_FILE" ]; then
    local pid pid_cmdline
    pid="$(cat "$PID_FILE" 2>/dev/null || true)"
    if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
      # `kill -0` proves only that SOME process holds this PID. PIDs are
      # recycled, so after a reboot an unrelated process can inherit the
      # recorded number and this fast path would report a healthy daemon that
      # is not running — short-circuiting the reliable port-based check below.
      # Validate with the same ps/start-mcp-server match serena_port_pids()
      # applies before trusting the record.
      pid_cmdline="$(ps -p "$pid" -o command= 2>/dev/null || true)"
      if printf '%s' "$pid_cmdline" | grep -q "start-mcp-server"; then
        ok "Serena daemon: running (PID $pid)"
        exit 0
      fi
    fi
    # Dead PID, or a live one belonging to an unrelated process — fall through
    # to port detection rather than reporting a false "running".
  fi

  # 2. No usable PID file — fall back to port detection. This covers daemons
  #    started by the OS service manager (launchd/systemd) at boot, which may
  #    not have written this script's PID file. Adopt the discovered PID so
  #    subsequent stop/status calls have a record to work from.
  local port_pid
  port_pid="$(serena_port_pids | awk '{print $1}')"
  if [ -n "$port_pid" ]; then
    echo "$port_pid" > "$PID_FILE"
    ok "Serena daemon: running (PID $port_pid)"
    exit 0
  fi

  # 3. Not running anywhere — clear any stale PID file and report stopped.
  rm -f "$PID_FILE"
  info "Serena daemon: stopped"
  exit 1
}

# ── resolve-service-tool ──────────────────────────────────────────────────────
# Print the absolute path of <name> as found within SERVICE_PATH_DIRS, or print
# nothing and return non-zero. Pure: it stats the filesystem and writes one line.
#
# Deliberately NOT `command -v`, which is the very thing that made the render
# unstable. `command -v` answers from the caller's PATH, and from this shell's
# hash table on top of that — two pieces of caller state, neither of which the
# installed artifact should depend on. Searching the rendered directories by hand
# gives the same answer to every caller on a given machine, and guarantees the
# tool it finds is one the daemon can find too.
resolve_service_tool() {
  local name="${1:-}" dir
  [ -n "$name" ] || return 1
  local dirs=()
  # read -ra splits on IFS without glob-expanding, so a directory containing a
  # shell metacharacter cannot turn into a pattern match.
  IFS=: read -ra dirs <<< "$SERVICE_PATH_DIRS"
  for dir in "${dirs[@]}"; do
    if [ -x "$dir/$name" ]; then
      printf '%s\n' "$dir/$name"
      return 0
    fi
  done
  return 1
}

# ── render-service-definition ─────────────────────────────────────────────────
# Emit the complete text of the OS service definition for <platform> on stdout.
#
#   render_service_definition Darwin   -> launchd plist body
#   render_service_definition Linux    -> systemd user unit body
#
# PURE by contract: the ONLY effect of this function is bytes on stdout. It
# writes no file, creates no directory, and calls neither launchctl nor
# systemctl — installing what it renders is the caller's job. It also never
# prints via info/ok/warn, because those write to stdout and would corrupt the
# artifact mid-render; fail() is safe here only because it writes to stderr.
#
# Splitting generation from installation is the point: `install-service` and the
# drift check render from this one template instead of keeping two copies that
# can silently disagree. Generation lives here and nowhere else.
#
# Deliberately carries NO embedded version marker, no generator key, and no
# timestamp — nothing recording when or by what the artifact was made. Drift is
# determined by byte-comparing this output against the installed file, so the
# rendered bytes must stay reproducible — and launchd's tolerance of unknown
# top-level plist keys is unverified, which this approach never has to test.
#
# Exit codes (stable contract — callers branch on these; this function returns
# and never exits, so it is safe to call more than once in a single process):
#   0  rendered     — the definition was written to stdout
#   1  unsupported  — platform argument is neither Darwin nor Linux
#   2  unrenderable — uvx not found, so the launch path cannot be resolved
#
# Note: the render arms intentionally end on `cat` and do NOT force `return 0`.
# A caller testing this in a condition (`if render_service_definition ... ; then`)
# suspends set -e inside the function, so propagating cat's status is what lets a
# failed write on the caller's redirection surface instead of silently passing.
render_service_definition() {
  local platform="${1:-}"

  # Platform first, so an unsupported argument is reported as such even on a
  # machine with no launcher to resolve — the unsupported code outranks the
  # unrenderable one.
  case "$platform" in
    Darwin|Linux) ;;
    *)
      fail "unsupported OS: $platform"
      return 1
      ;;
  esac

  # Resolved ONCE for both platforms rather than per-arm. The arms differ in
  # syntax, not in which launcher they start or which tools that launcher needs
  # to reach — and a per-arm copy of this is how one arm ended up with a PATH the
  # other lacked.
  local uvx_path uvx_dir svc_path
  uvx_path="$(resolve_service_tool uvx || true)"
  if [ -z "$uvx_path" ]; then
    # The one input this render still takes from the caller, and a bounded one: a
    # uvx installed outside every directory in SERVICE_PATH_DIRS — Nix, Linuxbrew,
    # a version-manager shim, a project venv — is reachable only through the
    # invoking shell. Refusing to render for those machines would break a working
    # install to buy a determinism they cannot have anyway, and the standard
    # layouts never reach this line. Its directory is prepended below so the
    # daemon can still find the launcher.
    uvx_path="$(command -v uvx || true)"
  fi
  if [ -z "$uvx_path" ]; then
    fail "uvx not found; cannot render the $platform service definition"
    return 2
  fi
  uvx_dir="$(dirname "$uvx_path")"
  svc_path="$SERVICE_PATH_DIRS"
  case ":$svc_path:" in
    *":$uvx_dir:"*) ;;
    *) svc_path="$uvx_dir:$svc_path" ;;
  esac

  # Platform already validated above, so this branch needs no default arm.
  case "$platform" in
    Darwin)
      # Launch site 2 of 2. cmd_start()'s `nohup uvx` invocation is the other,
      # and the two carry an IDENTICAL flag set — a divergence between them is
      # what produced the original incident. launchd gives every token its own
      # <string> element, so an option and its value are two adjacent elements.
      cat << EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
    "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>com.guild.serena</string>
  <key>ProgramArguments</key>
  <array>
    <string>$uvx_path</string>
    <string>--from</string>
    <string>$SERENA_PKG</string>
    <string>serena</string>
    <string>start-mcp-server</string>
    <string>--context</string>
    <string>claude-code</string>
    <string>--transport</string>
    <string>streamable-http</string>
    <string>--port</string>
    <string>$PORT</string>
  </array>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key>
    <string>$svc_path</string>
  </dict>
  <key>WorkingDirectory</key>
  <string>$HOME</string>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>StandardOutPath</key>
  <string>$HOME/.serena-daemon.log</string>
  <key>StandardErrorPath</key>
  <string>$HOME/.serena-daemon.log</string>
</dict>
</plist>
EOF
      ;;
    Linux)
      # Route ExecStart through this script's `start` subcommand so the PID file
      # is written on every launch (including boot). Type=forking + PIDFile lets
      # systemd track the real daemon; Environment=PATH gives that launch the
      # same tool directories the plist gives the launchd job — the launcher that
      # starts the server, and node for the language servers the server spawns
      # behind it. See SERVICE_PATH_DIRS for why it is one fixed list.
      #
      # Both artifacts pin WorkingDirectory to $HOME, and to $HOME rather than to
      # a repository. This daemon is user-scoped and serves every project the
      # user opens: its launch arguments name no project, and the client activates
      # one per session, so there is no single project tree to pin and pinning one
      # would be wrong. What $HOME buys is a working directory that always exists
      # and is writable by the user the service runs as — a checkout can be
      # renamed, deleted, or sit on a volume that is not mounted at login, and a
      # service whose working directory has vanished does not start.
      #
      # The two start-limit directives below are set EXPLICITLY, and belong in
      # [Unit] rather than [Service]. Omitting them does not mean "unlimited" —
      # it means inheriting the manager-wide DefaultStartLimit* values, which
      # vary by distro, so the circuit breaker would be whatever the local
      # systemd build happens to default to. Pinning them is what guarantees a
      # command that can never succeed actually stops being restarted: with
      # RestartSec=5 a hard-failing start cycles about every 5s, so the 5-start
      # budget is spent in ~25s — far inside the 300s window — and systemd
      # drives the unit into `failed` state instead of retrying forever. The
      # window is deliberately far wider than the budget needs, so that a daemon
      # which runs for hours and dies once never accumulates toward the limit,
      # and so an occasional reconcile-driven restart cannot spend the budget
      # and mask real crash-loop signal.
      #
      # This is the Linux half of crash-loop handling and has no macOS
      # counterpart by design: launchd has no terminal give-up state at all, so
      # KeepAlive stays true in the plist above and macOS relies on
      # detect_crash_loop() reporting the loop instead.
      cat << EOF
[Unit]
Description=Serena MCP HTTP Daemon (guild)
After=network.target
StartLimitIntervalSec=300
StartLimitBurst=5

[Service]
Type=forking
PIDFile=%h/.serena-daemon.pid
Environment=PATH=$svc_path
ExecStart=$SCRIPT_PATH start
WorkingDirectory=$HOME
Restart=on-failure
RestartSec=5
StandardOutput=append:%h/.serena-daemon.log
StandardError=append:%h/.serena-daemon.log

[Install]
WantedBy=default.target
EOF
      ;;
  esac
}

# ── install-service ───────────────────────────────────────────────────────────
# Exit codes: 0 success, non-zero failure. That two-state contract is UNCHANGED
# by the post-load probe added below — a probe that cannot confirm the daemon
# reports the same `exit 1` this command already used for "uvx not found" and
# "unsupported OS", so no caller's branching is disturbed and no code is
# renumbered. (There are no machine callers of this subcommand in-repo; it is
# invoked by hand, and by doctor's advice line telling a human to run it.)
#
# What DOES change is what `exit 0` asserts. It used to mean "launchd/systemd
# accepted the definition"; it now means "...and something answered a real MCP
# handshake on $PORT". Those differ on precisely the machine that produced the
# 29-day incident: a plist whose command can never be satisfied loads perfectly.
cmd_install_service() {
  local os probe_rc=0
  os="$(uname)"

  local uvx_path
  uvx_path="$(command -v uvx || true)"
  if [ -z "$uvx_path" ]; then
    fail "uvx not found"
    exit 1
  fi

  case "$os" in
    Darwin)
      mkdir -p "$HOME/Library/LaunchAgents"
      render_service_definition Darwin > "$PLIST_FILE"
      if [[ -f "$LEGACY_PLIST_FILE" ]]; then
        info "Removing legacy launchd agent (com.codsworth.serena)..."
        launchctl unload "$LEGACY_PLIST_FILE" 2>/dev/null || true
        rm -f "$LEGACY_PLIST_FILE"
      fi
      launchctl unload "$PLIST_FILE" 2>/dev/null || true
      launchctl load "$PLIST_FILE"
      # launchd has now ACCEPTED the job. That is not evidence the job runs, so
      # do not report success until the daemon answers. macOS is the platform
      # the incident actually happened on and the priority platform (A-007).
      probe_rc=0
      await_daemon_healthy "$INSTALL_PROBE_SECONDS" || probe_rc=$?
      if [ "$probe_rc" -eq 1 ]; then
        fail "launchd accepted the job, but Serena did not answer an MCP handshake on port $PORT within ${INSTALL_PROBE_SECONDS}s."
        fail "The plist IS installed — what could not be confirmed is that the command inside it runs. Diagnose: serena-daemon.sh doctor    (log: $LOG_FILE)"
        exit 1
      fi
      if [ "$probe_rc" -eq 2 ]; then
        # An optional tool is missing, not a broken install. Say so plainly
        # rather than failing an install that may be perfectly fine.
        warn "Serena service installed and loaded (macOS launchd), but its health could NOT be verified (curl unavailable, or the port could not be read). Confirm with: serena-daemon.sh doctor"
        exit 0
      fi
      ok "Serena service installed, loaded, and answering MCP on port $PORT (macOS launchd). Daemon will start at login."
      exit 0
      ;;
    Linux)
      mkdir -p "$HOME/.config/systemd/user"
      render_service_definition Linux > "$SYSTEMD_FILE"
      systemctl --user daemon-reload 2>/dev/null || true
      systemctl --user enable --now serena-daemon.service
      # `--now` does block on unit start and does fail loudly under set -e, so
      # this platform was already partly guarded — but only against a unit that
      # cannot START. The unit is Type=forking with ExecStart routed through this
      # script's own `start`, which exits 0 after a liveness check on the
      # launcher PID and never attempts a handshake. So systemd's "started" is
      # not "serving" either, and a wedged daemon (oraios/serena#1367) still
      # reports a clean install. The probe is a platform-independent HTTP
      # handshake, so applying it here closes that hole without contorting the
      # two reload paths toward each other — those legitimately differ (A-007).
      probe_rc=0
      await_daemon_healthy "$INSTALL_PROBE_SECONDS" || probe_rc=$?
      if [ "$probe_rc" -eq 1 ]; then
        fail "systemd started the unit, but Serena did not answer an MCP handshake on port $PORT within ${INSTALL_PROBE_SECONDS}s."
        fail "The unit IS installed — what could not be confirmed is that the command inside it serves. Diagnose: serena-daemon.sh doctor    (log: $LOG_FILE)"
        exit 1
      fi
      if [ "$probe_rc" -eq 2 ]; then
        warn "Serena service installed and enabled (Linux systemd), but its health could NOT be verified (curl unavailable, or the port could not be read). Confirm with: serena-daemon.sh doctor"
        exit 0
      fi
      ok "Serena service installed, enabled, and answering MCP on port $PORT (Linux systemd). Daemon will start at login."
      exit 0
      ;;
    *)
      fail "unsupported OS: $os"
      exit 1
      ;;
  esac
}

# ── uninstall-service ─────────────────────────────────────────────────────────
cmd_uninstall_service() {
  local os
  os="$(uname)"

  case "$os" in
    Darwin)
      launchctl unload "$PLIST_FILE" 2>/dev/null || true
      rm -f "$PLIST_FILE"
      launchctl unload "$LEGACY_PLIST_FILE" 2>/dev/null || true
      rm -f "$LEGACY_PLIST_FILE"
      ok "Serena service uninstalled (macOS launchd)."
      exit 0
      ;;
    Linux)
      systemctl --user disable --now serena-daemon.service 2>/dev/null || true
      rm -f "$SYSTEMD_FILE"
      ok "Serena service uninstalled (Linux systemd)."
      exit 0
      ;;
    *)
      fail "unsupported OS: $os"
      exit 1
      ;;
  esac
}

# ── crash-loop detection ──────────────────────────────────────────────────────
# Decide whether the service is stuck being restarted forever without ever
# succeeding, and print that finding. A HELPER, not a subcommand: `doctor` calls
# it to surface the finding and nothing else does.
#
# The tool has to make this call itself because neither supervisor hands over a
# ready-made answer. systemd only lands a unit in `failed` state once its start
# limit is spent — and that limit is pinned by this script's own renderer, not
# left to any distro default worth trusting. launchd has no terminal give-up
# state at all: an unsatisfiable KeepAlive job retries forever, which is exactly
# how the original incident stayed invisible for 29 days. So the evidence is
# assembled here, from supervisor counters plus the daemon's own log.
#
# EVIDENCE, and why no single signal is trusted on its own:
#   supervisor  Darwin: `successive crashes`, `state`, `last exit code`
#               Linux:  `is-failed` (burst exhausted) plus `NRestarts`
#   log         an identical error line repeated in a bounded tail of $LOG_FILE
#
# A repeating log line is NEVER sufficient by itself — a perfectly healthy
# daemon can log the same error on every poll — so it only ever corroborates
# supervisor evidence that restarts are actually happening. Supervisor evidence
# strong enough on its own (launchd's crash counter, systemd's failed state or a
# high NRestarts) stands without it.
#
# RETURN CODES. This function never calls `exit`; it must not be able to end
# `doctor`, whose own exit codes are a frozen contract:
#   0  no crash loop detected — INCLUDING the indeterminate and not-loaded cases
#   1  crash loop detected
#
# Indeterminate deliberately returns 0. Per concerns.md C-003 the output of
# `launchctl print` is not a stable interface, so any field this function cannot
# find, or cannot read as a number, is reported as unknown and never as a crash
# loop — a false alarm would send a caller restarting a daemon that is fine.
# Field names verified empirically on macOS 15 (Darwin 24.6.0): `successive
# crashes` is ABSENT from the output when it is zero, and `last exit code` is
# not always numeric (it reads `(never exited)` for a job that never ran), so
# absence and non-numeric values are both normal inputs here, not failures.
# Every external command is guarded, so a missing binary — launchctl on Linux,
# systemctl on macOS — degrades a line instead of aborting under set -e.
detect_crash_loop() {
  local os="" artifact="" label="" unit=""
  local lc_print="" lc_out="" lc_fields=""
  local crashes="" job_state="" last_code="" last_code_raw=""
  local failed="no" sc_restarts=""
  local loaded="yes" sup_known="no" sup_hard="no" sup_soft="no" detail=""
  local log_repeat=0 log_line="" top=""

  os="$(uname)"

  # ── Log evidence. Gathered first because both platforms corroborate with it.
  #    The daemon appends for its whole lifetime and this file reaches tens of
  #    megabytes, so only a bounded tail is read. The threshold is 3, not 2:
  #    this function CLASSIFIES a loop, where doctor's own log line merely
  #    REPORTS a repeat, so a single stray pair must not reach this verdict.
  #    awk does the counting (rather than `sort | uniq -c | head`) so no stage
  #    can exit early and trip pipefail.
  #
  #    WHAT COUNTS AS AN ERROR LINE. Matching severity words ANYWHERE in a line
  #    both over- and under-reports. Both directions were verified against a real
  #    ~/.serena-daemon.log:
  #      over  — Serena echoes its own MCP instruction banner once per start, and
  #              that banner's first line opens `CRITICAL: Before starting to work
  #              on a coding task...`. A few restarts inside the window and a
  #              perfectly healthy daemon reports a repeating "error" that is a
  #              banner, which is noise a reader has to spend time dismissing.
  #      under — uv's resolver failure, the exact shape of the 29-day incident
  #              this whole subcommand exists to catch, carries no severity word
  #              at all: `  × No solution found when resolving tool dependencies:`.
  #              A word-matching scan finds NOTHING in a log that is nothing but
  #              crash, which is the far more expensive of the two mistakes.
  #    So a line qualifies on its STRUCTURE, as one of the shapes this daemon's
  #    own writers actually emit:
  #      ERROR|CRITICAL|FATAL + ISO date   python logging record
  #      ERROR:|FATAL: + space             uvicorn level prefix
  #      error: + space                    uv / cargo style diagnostic
  #      × or ╰─▶                          uv error and cause-chain glyphs
  #      Caused by:                        uv cause chain
  #      Traceback                         python traceback header
  #    CRITICAL is deliberately absent from the bare-colon shape and honoured
  #    only in the timestamped one: it is the single severity word that also
  #    opens the banner above, and uvicorn does not emit it in practice.
  #    Two portability notes, both load-bearing: the glyphs are alternated as
  #    whole literals (never a bracket class, which byte-wise awks would split
  #    mid-character), and the year is spelled [0-9][0-9][0-9][0-9] because
  #    interval expressions are not supported by every awk this ships to.
  #    Ties break lexicographically so the reported line is stable run to run —
  #    uv prints × and ╰─▶ once each per attempt, so their counts tie routinely.
  if [ -f "$LOG_FILE" ]; then
    top="$(tail -n 400 "$LOG_FILE" 2>/dev/null \
      | awk '/^[[:space:]]*(ERROR|CRITICAL|FATAL)[[:space:]]+[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]/ ||
             /^[[:space:]]*(ERROR|FATAL):[[:space:]]/ ||
             /^[[:space:]]*error:[[:space:]]/ ||
             /^[[:space:]]*(×|╰─▶)/ ||
             /^[[:space:]]*Caused by:[[:space:]]/ ||
             /^[[:space:]]*Traceback/ { c[$0]++ }
             END { m = 0; l = ""
                   for (k in c) { if (c[k] > m || (c[k] == m && k < l)) { m = c[k]; l = k } }
                   if (m >= 3) printf "%d\t%s", m, l }' || true)"
    if [ -n "$top" ]; then
      log_repeat="${top%%$'\t'*}"
      log_line="$(printf '%s' "${top#*$'\t'}" | cut -c1-120 || true)"
    fi
  fi

  # ── Supervisor evidence, branching on platform the way install-service does.
  case "$os" in
    Darwin)
      artifact="$PLIST_FILE"
      label="$(basename "$artifact" .plist)"
      if [ ! -f "$artifact" ] || ! command -v launchctl >/dev/null 2>&1; then
        loaded="no"
      else
        # `print` carries the crash counter and scheduling state; `list` is the
        # coarse fallback that still reports an exit status when `print` is
        # unavailable or its layout has shifted under us.
        lc_print="$(launchctl print "gui/$(id -u)/$label" 2>/dev/null || true)"
        lc_out="$(launchctl list "$label" 2>/dev/null || true)"
        if [ -z "$lc_print" ] && [ -z "$lc_out" ]; then
          loaded="no"
        else
          # One pass, keys matched EXACTLY after trimming, so a renamed or
          # reordered field yields "" rather than a wrong read. Exact matching
          # is also what keeps the `state` key from swallowing the separate
          # `job state` line that appears later in the same output.
          lc_fields="$(printf '%s\n' "$lc_print" | awk -F' = ' '
            { k = $1; sub(/^[ \t]+/, "", k); sub(/[ \t]+$/, "", k)
              if (NF < 2) next
              v = $2; sub(/;$/, "", v); sub(/[ \t]+$/, "", v)
              if      (k == "successive crashes" && c == "") c = v
              else if (k == "state"              && s == "") s = v
              else if (k == "last exit code"     && e == "") e = v }
            END { printf "%s\t%s\t%s", c, s, e }' || true)"
          crashes="$(printf '%s' "$lc_fields" | cut -f1 || true)"
          job_state="$(printf '%s' "$lc_fields" | cut -f2 || true)"
          last_code="$(printf '%s' "$lc_fields" | cut -f3 || true)"

          # Fall back to `launchctl list`, whose LastExitStatus key has been
          # stable far longer than anything in `print`.
          if [ -z "$last_code" ] && [ -n "$lc_out" ]; then
            last_code="$(printf '%s\n' "$lc_out" \
              | awk -F'=' '/"LastExitStatus"/ { gsub(/[^0-9-]/, "", $2); print $2; exit }' || true)"
          fi

          # Validate before ANY numeric use: `(never exited)` and an empty field
          # both have to survive as "unknown", not error out under set -e.
          # `last exit code` gains a symbolic suffix when the status maps to a
          # sysexits.h name — `78: EX_CONFIG`, which is the exact shape of a
          # real crash-loop report — so match a LEADING integer rather than
          # demanding the whole field be numeric, or that signal is thrown away.
          # The raw text is kept for the human line: "78: EX_CONFIG" names the
          # cause, where a bare "78" sends the reader off to look it up.
          last_code_raw="$last_code"
          if [[ "$last_code" =~ ^(-?[0-9]+) ]]; then
            last_code="${BASH_REMATCH[1]}"
          else
            last_code=""
          fi
          if ! [[ "$crashes" =~ ^[0-9]+$ ]]; then crashes=""; fi

          if [ -n "$crashes" ] || [ -n "$job_state" ] || [ -n "$last_code" ]; then
            sup_known="yes"
          fi

          if [ -n "$crashes" ] && [ "$crashes" -ge 3 ]; then
            sup_hard="yes"
            detail="launchd reports $crashes successive crashes"
          elif [ "$job_state" = "spawn scheduled" ] && [ -n "$last_code" ] && [ "$last_code" -ne 0 ]; then
            # Respawn pending after a failed run: launchd's crash-loop shape.
            sup_hard="yes"
            detail="launchd state '$job_state' with last exit code $last_code_raw"
          elif [ -n "$last_code" ] && [ "$last_code" -ne 0 ]; then
            sup_soft="yes"
            detail="launchd last exit code $last_code_raw"
          fi
        fi
      fi
      ;;
    Linux)
      artifact="$SYSTEMD_FILE"
      unit="$(basename "$artifact")"
      if [ ! -f "$artifact" ] || ! command -v systemctl >/dev/null 2>&1; then
        loaded="no"
      else
        # is-failed exits 0 exactly when the unit sits in `failed` state. Once
        # the start limit is spent that state means "systemd gave up restarting
        # this" — the definitive loop, and the bounded outcome the start limits
        # in render_service_definition exist to produce.
        if systemctl --user is-failed "$unit" >/dev/null 2>&1; then
          failed="yes"
          sup_known="yes"
        fi
        sc_restarts="$(systemctl --user show "$unit" --property=NRestarts 2>/dev/null \
          | awk -F'=' '$1 == "NRestarts" { gsub(/[^0-9]/, "", $2); print $2; exit }' || true)"
        if ! [[ "$sc_restarts" =~ ^[0-9]+$ ]]; then sc_restarts=""; fi
        if [ -n "$sc_restarts" ]; then
          sup_known="yes"
        fi

        if [ "$failed" = "yes" ]; then
          sup_hard="yes"
          detail="systemd unit $unit is in failed state"
        elif [ -n "$sc_restarts" ] && [ "$sc_restarts" -ge 3 ]; then
          sup_hard="yes"
          detail="systemd reports NRestarts=$sc_restarts"
        elif [ -n "$sc_restarts" ] && [ "$sc_restarts" -ge 1 ]; then
          sup_soft="yes"
          detail="systemd reports NRestarts=$sc_restarts"
        fi
      fi
      ;;
    *)
      # No supported service manager, so there is no supervisor to loop and
      # nothing to report. Never an error.
      return 0
      ;;
  esac

  # ── Verdict. Uniform across platforms; only the evidence feeding it differs.
  if [ "$loaded" = "no" ]; then
    info "crash loop ........... none (service not loaded)"
    return 0
  fi

  if [ "$sup_hard" = "yes" ]; then
    warn "crash loop ........... DETECTED — $detail"
  elif [ "$sup_soft" = "yes" ] && [ "$log_repeat" -ge 3 ]; then
    warn "crash loop ........... DETECTED — $detail, and the same error repeats ${log_repeat}x in the log"
  elif [ "$sup_known" = "no" ]; then
    # Loaded, but nothing readable came back. Reported honestly and NOT as a
    # crash loop: an unparseable supervisor is not evidence of one.
    warn "crash loop ........... indeterminate (supervisor state could not be read)"
    return 0
  else
    ok "crash loop ........... none"
    return 0
  fi

  # Detected. Explain what it means on THIS platform, because the two differ in
  # whether anything will ever stop the restarts.
  if [ -n "$log_line" ]; then
    warn "  ^ repeating error (${log_repeat}x in last 400 lines): $log_line"
  fi
  case "$os" in
    Darwin)
      warn "  ^ launchd KeepAlive has no give-up state and will retry forever — fix the cause or run: serena-daemon.sh uninstall-service"
      ;;
    Linux)
      if [ "$failed" = "yes" ]; then
        warn "  ^ systemd hit the start limit and STOPPED restarting it; after fixing the cause run: systemctl --user reset-failed $unit"
      else
        warn "  ^ restarts are bounded by the unit's start limit and will stop once it is spent; inspect $LOG_FILE"
      fi
      ;;
  esac
  return 1
}

# ── doctor ────────────────────────────────────────────────────────────────────
# One-shot diagnostic sweep. Reports seven independent dimensions, one line
# each, plus a derived crash-loop finding, then summarises them in a single exit
# code. The crash-loop line is a FINDING, not an eighth dimension and not a new
# exit state: it classifies evidence the dimensions already report, and the exit
# code contract below is unchanged by it.
#
# EXIT CODE CONTRACT — stable and machine-consumed. Callers branch on the exit
# code and MUST NOT parse stdout text: the wording below is for humans and may
# be reworded at any time, the codes may not.
#
#   0  healthy               service installed, definition current, and Serena
#                            answering a valid MCP handshake on $PORT
#   1  not-installed         no OS service definition on disk (also the verdict
#                            on a platform with no supported service manager,
#                            where one cannot exist)
#   2  installed-but-stopped service installed, nothing serving on $PORT
#   3  running-but-unhealthy something holds $PORT but no valid MCP handshake
#                            came back — the wedged-daemon case — OR health
#                            could not be established at all. An unprobed
#                            daemon is never reported healthy, so the probe's
#                            "unverifiable" verdict lands here and not on 0.
#   4  drifted               running and healthy, but the installed service
#                            definition no longer matches what the current
#                            source renders
#
# Precedence when several apply: 1 > 2 > 3 > 4 > 0. Each code names the single
# most actionable finding, and drift is only meaningful once the service exists,
# so not-installed outranks it. The report always prints all seven dimensions
# regardless of which code wins — nothing is hidden from a human by the summary.
#
# THE PRECEDENCE IS DELIBERATE, AND IT MASKS DRIFT FROM THE EXIT CODE. Because 4
# sits below all three health codes, exit 4 is reachable ONLY from an otherwise
# healthy state: installed, and answering a valid MCP handshake. A host that is
# BOTH drifted AND stopped exits 2, not 4. That ordering is the intended one — a
# daemon that is not serving is the more urgent finding, and a stale definition
# on disk changes nothing until something actually starts from it.
#
# The consequence binds every caller: 1, 2 and 3 carry NO information about
# drift, and MUST NOT be read as "no drift". Only 4 asserts drift, and only 0
# asserts its absence. Drift is still always EVALUATED whenever the service is
# installed — the report's `definition` line states the true answer independently
# of health, and the verdict names a drift that a health code outranked — but
# that is human-readable output, which per this contract callers do not parse. A
# caller that must act on drift specifically runs `reconcile`, which is
# idempotent, silent when already converged, and never consults this exit code.
#
# Drift that cannot be determined (the renderer could not run) is reported as
# such and does NOT become code 4: claiming drift would send a caller into a
# reconcile that fails for the very same reason. This is a third state the exit
# code does not distinguish, for the same reason as above — 0 and 4 are the only
# codes that say anything about drift, and "undetermined" is neither.
#
# READ-ONLY by contract. Unlike `status`, doctor never writes or clears the PID
# file, never installs, and never restarts. Diagnosis and repair are kept apart
# so a caller can ask "what is wrong?" without changing anything.
cmd_doctor() {
  local os port_pids_raw="" port_count=0 p
  local port_state="unknown" health_rc=0 serena_pids=""
  local pidfile_state="absent" pidfile_pid="" pid_cmdline=""
  local platform_ok="yes" artifact="" installed="no"
  local drift="n/a" rendered=""
  local last_exit="unknown"
  local dup_count=0 dup_line="" log_state="absent" top=""

  os="$(uname)"

  # ── Gather (no output; every optional tool is guarded so a missing binary or
  #    an expected failure degrades a line instead of aborting under set -e) ──

  # Shared port resolution, consumed by dimensions (a) and (c) alike. Those two
  # are separate READINGS of one body of evidence, so they must not consult
  # separate resolvers: deriving (a) from a private inline `lsof` call is what
  # let this report print "port unknown (lsof not available)" on the line
  # directly above "Serena process alive (PID 1234)" on every host without lsof
  # — two lines of one report contradicting each other. serena_port_pids()
  # already tries lsof -> ss -> fuser, so it is the single source of truth here.
  serena_pids="$(serena_port_pids || true)"

  # (a) Raw port occupancy. When lsof is present it stays the resolver for this
  #     line because it is the RICHEST reading available: `lsof -i` lists every
  #     process holding a socket on the port — the listening server AND every
  #     connected client — so the count is an occupancy signal, never a count of
  #     Serena processes. Without lsof there is no holder census to be had, so
  #     the shared resolver answers the weaker but still honest question: a named
  #     Serena listener proves the port IS bound, and an authoritative empty
  #     enumeration (serena_port_owners_known) proves it is not. Only when
  #     nothing could look at all does this degrade to "unknown" — which is now
  #     the same condition under which dimension (c) reports nothing running, so
  #     the two lines can no longer disagree. Dimension (c) remains the
  #     authoritative "is the server alive" answer.
  if command -v lsof >/dev/null 2>&1; then
    port_pids_raw="$(lsof -i :"$PORT" -t 2>/dev/null || true)"
    for p in $port_pids_raw; do
      port_count=$((port_count + 1))
    done
    if [ "$port_count" -gt 0 ]; then
      port_state="counted"
    else
      port_state="free"
    fi
  elif [ -n "$serena_pids" ]; then
    port_state="held"
  elif serena_port_owners_known; then
    # Called as an `if` condition (never bare) so its earned "not running"
    # return of 1 cannot abort the sweep under set -e.
    port_state="free"
  fi

  # (b) MCP handshake, delegated to the probe: 0 healthy, 1 not running,
  #     2 unresponsive, 3 unverifiable. It prints nothing, and `|| health_rc=$?`
  #     keeps an expected unhealthy verdict from killing this function.
  serena_mcp_healthy || health_rc=$?

  # (c) The Serena server process — resolved above by port + cmdline rather than
  #     by a bare PID-file read — plus whether the recorded PID can be trusted.
  if [ -f "$PID_FILE" ]; then
    pidfile_pid="$(cat "$PID_FILE" 2>/dev/null || true)"
    if [ -z "$pidfile_pid" ]; then
      pidfile_state="empty"
    elif ! kill -0 "$pidfile_pid" 2>/dev/null; then
      pidfile_state="dead"
    else
      # Live PID is not enough — PIDs are recycled. Same ps/start-mcp-server
      # match used by serena_port_pids() and by the start/status fast paths.
      pid_cmdline="$(ps -p "$pidfile_pid" -o command= 2>/dev/null || true)"
      if printf '%s' "$pid_cmdline" | grep -q "start-mcp-server"; then
        pidfile_state="valid"
      else
        pidfile_state="foreign"
      fi
    fi
  fi

  # (d) Service installed, branching on platform the way install-service does.
  case "$os" in
    Darwin) artifact="$PLIST_FILE" ;;
    Linux)  artifact="$SYSTEMD_FILE" ;;
    *)      platform_ok="no" ;;
  esac
  if [ "$platform_ok" = "yes" ] && [ -f "$artifact" ]; then
    installed="yes"
  fi

  # (e) Drift. Byte comparison against what the current source renders is the
  #     whole test — no version marker exists in either artifact to consult.
  #     The renderer returns (never exits) non-zero on an unsupported platform
  #     or a missing uvx, so it is called as an `if` condition to keep set -e
  #     from aborting the sweep. Generation lives in render_service_definition
  #     and nowhere else; doctor only compares.
  if [ "$installed" = "yes" ]; then
    if rendered="$(render_service_definition "$os")"; then
      # $( ) strips trailing newlines, so the single trailing newline the
      # renderer guarantees is restored before the comparison — that keeps this
      # a true byte comparison rather than a content-only one.
      if printf '%s\n' "$rendered" | cmp -s - "$artifact"; then
        drift="current"
      else
        drift="drifted"
      fi
    else
      drift="unknown"
    fi
  fi

  # (f) Last exit status from the service manager.
  case "$os" in
    Darwin)
      if [ "$installed" = "yes" ] && command -v launchctl >/dev/null 2>&1; then
        local lc_label="" lc_out="" lc_status=""
        # Derive the label from the artifact path rather than repeating the
        # string the renderer already owns.
        lc_label="$(basename "$artifact" .plist)"
        lc_out="$(launchctl list "$lc_label" 2>/dev/null || true)"
        if [ -n "$lc_out" ]; then
          lc_status="$(printf '%s\n' "$lc_out" \
            | awk -F'=' '/LastExitStatus/ { gsub(/[^0-9-]/, "", $2); print $2; exit }' || true)"
          if [ -n "$lc_status" ]; then
            last_exit="$lc_status"
          else
            last_exit="loaded (no LastExitStatus reported)"
          fi
        else
          last_exit="not loaded"
        fi
      fi
      ;;
    Linux)
      if [ "$installed" = "yes" ] && command -v systemctl >/dev/null 2>&1; then
        local sc_unit="" sc_out=""
        sc_unit="$(basename "$artifact")"
        sc_out="$(systemctl --user show "$sc_unit" \
          --property=Result --property=ExecMainStatus --property=NRestarts \
          2>/dev/null || true)"
        if [ -n "$sc_out" ]; then
          last_exit="$(printf '%s' "$sc_out" | tr '\n' ' ' || true)"
        fi
      fi
      ;;
  esac

  # (g) Repeating identical error line in the log. The daemon appends for its
  #     whole lifetime and this file reaches tens of megabytes, so only a
  #     bounded tail is scanned. awk (not `sort | uniq -c | head`) does the
  #     counting so no stage can exit early and trip pipefail. Reporting the
  #     observation is the whole job here — classifying a crash loop is not.
  #
  #     This dimension and detect_crash_loop() are two READINGS of one body of
  #     evidence — this one shallower (200 lines) and more eager (a plain pair
  #     counts), that one deeper and stricter because it feeds a verdict. They
  #     may differ in depth and threshold; they must NOT differ in what counts
  #     as an error line, or this report contradicts the finding printed a few
  #     lines below it. The structural shapes below are therefore kept identical
  #     to the ones detect_crash_loop() matches, and the reasoning for each —
  #     including why a bare `CRITICAL:` is excluded and why the uv glyphs are
  #     matched — is documented once, in that function's log-evidence block.
  #     Change one and you must change the other.
  if [ -f "$LOG_FILE" ]; then
    log_state="scanned"
    top="$(tail -n 200 "$LOG_FILE" 2>/dev/null \
      | awk '/^[[:space:]]*(ERROR|CRITICAL|FATAL)[[:space:]]+[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]/ ||
             /^[[:space:]]*(ERROR|FATAL):[[:space:]]/ ||
             /^[[:space:]]*error:[[:space:]]/ ||
             /^[[:space:]]*(×|╰─▶)/ ||
             /^[[:space:]]*Caused by:[[:space:]]/ ||
             /^[[:space:]]*Traceback/ { c[$0]++ }
             END { m = 0; l = ""
                   for (k in c) { if (c[k] > m || (c[k] == m && k < l)) { m = c[k]; l = k } }
                   if (m > 1) printf "%d\t%s", m, l }' || true)"
    if [ -n "$top" ]; then
      dup_count="${top%%$'\t'*}"
      dup_line="$(printf '%s' "${top#*$'\t'}" | cut -c1-120 || true)"
    fi
  fi

  # ── Report: one line per dimension, always all seven ──
  info "Serena doctor — port $PORT"

  # (a) port bound?
  case "$port_state" in
    counted) ok   "port $PORT ............ bound ($port_count socket holder(s), clients included)" ;;
    held)    ok   "port $PORT ............ bound (Serena listener named on the port)" ;;
    free)    warn "port $PORT ............ not bound" ;;
    *)       warn "port $PORT ............ unknown (no usable port resolver: lsof, ss or fuser)" ;;
  esac

  # (b) MCP handshake OK?
  case "$health_rc" in
    0) ok   "MCP handshake ........ ok" ;;
    1) warn "MCP handshake ........ not attempted (nothing serving on the port)" ;;
    2) warn "MCP handshake ........ FAILED (port held but no valid response — wedged)" ;;
    *) warn "MCP handshake ........ unverifiable (curl not available)" ;;
  esac

  # (c) process alive?
  if [ -n "$serena_pids" ]; then
    ok "Serena process ....... alive (PID(s) $serena_pids); PID file: $pidfile_state"
  else
    warn "Serena process ....... none running; PID file: $pidfile_state"
  fi
  if [ "$pidfile_state" = "foreign" ]; then
    warn "  ^ PID file names $pidfile_pid, which is live but is NOT a Serena process"
  fi

  # (d) service installed?
  if [ "$platform_ok" != "yes" ]; then
    warn "service installed .... no (unsupported OS: $os)"
  elif [ "$installed" = "yes" ]; then
    ok "service installed .... yes ($artifact)"
  else
    warn "service installed .... no (expected at $artifact)"
  fi

  # (e) installed definition matches current source?
  case "$drift" in
    current)  ok   "definition ........... matches current source" ;;
    drifted)  warn "definition ........... DRIFTED from current source" ;;
    unknown)  warn "definition ........... could not be determined (renderer unavailable)" ;;
    *)        info "definition ........... n/a (nothing installed to compare)" ;;
  esac

  # (f) last exit status?
  info "last exit status ..... $last_exit"

  # (g) log showing a repeating error?
  if [ "$log_state" = "absent" ]; then
    info "repeating log error .. none (no log file at $LOG_FILE)"
  elif [ -n "$dup_line" ]; then
    warn "repeating log error .. ${dup_count}x in last 200 lines: $dup_line"
  else
    ok "repeating log error .. none in last 200 lines"
  fi

  # (h) Derived finding: is this thing being restarted forever without ever
  #     succeeding? Dimensions (f) and (g) report raw observations; classifying
  #     them is detect_crash_loop's job, and it reads signals doctor does not —
  #     launchd's crash counter, systemd's failed state.
  #
  #     `|| true` is deliberate and load-bearing twice over. It keeps a detected
  #     crash loop (return 1) from aborting the sweep under set -e, and it
  #     discards the return value on purpose: the finding is a named line in
  #     this report, NOT a new exit state. doctor's exit codes are a contract
  #     consumed by the SessionStart hook and the foundry preflight, so no
  #     finding here may add to them or renumber them.
  detect_crash_loop || true

  # ── Verdict: see EXIT CODE CONTRACT above. Every branch exits explicitly;
  #    nothing falls off the end of this function. ──

  # Drift never outranks a health finding, so a drifted-AND-unhealthy host is
  # about to return 2 or 3 and exit 4 will never be seen. Name the masked drift
  # here rather than letting the one-line verdict silently overrule the
  # `definition` line printed above it: without this, a human reads "installed
  # but stopped", fixes exactly that, and never learns a reconcile was also
  # owed. `drifted` implies the service is installed, so no extra guard on
  # $installed is needed. This changes no exit code — the precedence below is
  # frozen and consumed by the SessionStart hook and the foundry preflight.
  if [ "$drift" = "drifted" ] && [ "$health_rc" -ne 0 ]; then
    warn "note: the definition is ALSO drifted; the verdict below outranks it —"
    warn "      run: serena-daemon.sh reconcile"
  fi

  if [ "$installed" != "yes" ]; then
    warn "verdict: not installed — run: serena-daemon.sh install-service"
    exit 1
  fi
  if [ "$health_rc" -eq 1 ]; then
    warn "verdict: installed but stopped — run: serena-daemon.sh start"
    exit 2
  fi
  if [ "$health_rc" -ne 0 ]; then
    warn "verdict: running but unhealthy — run: serena-daemon.sh restart"
    exit 3
  fi
  if [ "$drift" = "drifted" ]; then
    warn "verdict: drifted — installed definition is out of date"
    exit 4
  fi
  ok "verdict: healthy"
  exit 0
}

# ── project-config repair ─────────────────────────────────────────────────────
# Repair a .serena/project.yml whose `languages:` list was written as a list of
# MAPPINGS instead of a list of plain strings. Prints nothing when there is
# nothing to repair.
#
# THE DEFECT THIS CLEARS. Setup used to emit
#
#     languages:
#       - name: go
#
# where Serena's ProjectConfig._from_dict() calls .lower() on each element
# directly, so a mapping element raises `AttributeError: 'CommentedMap' object
# has no attribute 'lower'` and activate_project aborts. Setup now emits a plain
# string list, but that generator only runs on a manual /foundry:setup — so every
# project configured before the fix stays broken until somebody re-runs setup by
# hand. "Nothing re-runs setup" is precisely the silence GI-002 exists to end,
# which is why the repair hangs off reconcile: the SessionStart hook runs
# reconcile unconditionally on every session, and reconcile is this script's
# designated idempotent, silent-when-converged repair surface (GI-008). `doctor`
# could not host it — that command is READ-ONLY by contract.
#
# WHICH PROJECT, and why this is not a machine-wide sweep. Serena's own registry
# (~/.serena/serena_config.yml `projects:`) does list every project on the box,
# and sweeping it would make this cwd-independent. It is deliberately NOT used:
# that would write into repositories the user has not opened, and this plugin
# ships through a public marketplace to machines in unknown state (A-AUTO-007).
# A broken config harms nothing until its project is activated, so the repair is
# lazy — it fixes the ONE project this session is in, which is exactly the config
# about to be loaded. Every project that matters is therefore repaired the first
# time it is opened, and the blast radius of any single run is one directory the
# user is demonstrably working in. $CLAUDE_PROJECT_DIR is preferred over $PWD
# only as a robustness belt: the hook already runs with cwd at the project root.
#
# Return codes (the caller owns the exit-code policy):
#   0  nothing to do — no config at this root, or its languages list is valid
#   1  repaired      — the file was backed up and rewritten
#   2  found broken but NOT repaired — the malformation is a shape this cannot
#                      rewrite safely, or the backup/write failed. The file is
#                      left exactly as it was found in every case.
repair_project_config() {
  local root="${1:-}" cfg tmp backup rc=0
  [ -n "$root" ] || return 0
  cfg="$root/.serena/project.yml"
  # No Serena project here. The ordinary outcome for most directories, and the
  # reason a manual `reconcile` run from anywhere else stays silent.
  [ -f "$cfg" ] || return 0

  tmp="$cfg.reconcile.$$.tmp"

  # One awk pass both DETECTS and REWRITES, so the two can never disagree about
  # what counts as broken. Its exit status is the verdict: 0 unchanged,
  # 1 rewritten, 2 an unrecognised mapping shape was seen.
  #
  # Only items inside the top-level `languages:` block are considered — the
  # block ends at the next top-level key — so an identically-shaped `- name:`
  # entry under `ignored_paths:` is left untouched.
  #
  # Only `- name: <value>` is rewritten, because that is the exact shape the old
  # generator produced. Any OTHER mapping item is reported and NOT guessed at:
  # inventing a rewrite for a shape this script never wrote is how a repair
  # destroys a hand-edited config. Note the END block checks `unknown` first, so
  # a file mixing both shapes is refused rather than half-repaired.
  #
  # `|| rc=$?` because a non-zero awk status is the expected signal here, not a
  # failure set -e should abort on.
  awk '
    BEGIN { inb = 0; changed = 0; unknown = 0 }
    {
      if (inb) {
        if ($0 ~ /^[^ \t#-]/) {
          inb = 0
        } else if ($0 ~ /^[ \t]*-/) {
          if ($0 ~ /^[ \t]*-[ \t]*[A-Za-z_][A-Za-z0-9_]*[ \t]*:/) {
            if (match($0, /^[ \t]*-[ \t]*name[ \t]*:[ \t]*/)) {
              val = substr($0, RSTART + RLENGTH)
              sub(/[ \t]+$/, "", val)
              ind = $0
              sub(/-.*$/, "", ind)
              if (val != "" && val !~ /[:#]/) {
                print ind "- " val
                changed = 1
                next
              }
            }
            unknown = 1
          }
        }
      }
      if (inb == 0 && $0 ~ /^languages:[ \t]*$/) { inb = 1 }
      print
    }
    END { if (unknown) exit 2; if (changed) exit 1; exit 0 }
  ' "$cfg" > "$tmp" 2>/dev/null || rc=$?

  if [ "$rc" -eq 0 ]; then
    # Already valid. Silent and no write — this runs on every session start.
    rm -f "$tmp"
    return 0
  fi

  if [ "$rc" -ne 1 ]; then
    rm -f "$tmp"
    warn "Serena project config has a languages list Serena cannot load, in a shape this repair does not recognise — left unchanged: $cfg"
    warn "  fix it by hand (languages must be a flat list of plain strings), or re-run: /foundry:setup"
    return 2
  fi

  # Backup FIRST, matching setup-prereqs.sh's convention byte for byte
  # (timestamped .bak beside the original) so there is one backup scheme for
  # this file and not two. Taking it before any write is what makes the
  # non-atomic rewrite below safe: a partial write is always recoverable.
  backup="$cfg.$(date +%Y%m%d-%H%M%S).bak"
  if ! cp "$cfg" "$backup"; then
    rm -f "$tmp"
    fail "cannot back up $cfg — leaving it unchanged"
    return 2
  fi

  # `cat >` rather than `mv`, deliberately: it rewrites in place and so
  # preserves the file's existing mode and ownership. A rename would silently
  # replace them with whatever the current umask produces.
  if ! cat "$tmp" > "$cfg"; then
    rm -f "$tmp"
    fail "cannot write $cfg — restore from $backup"
    return 2
  fi
  rm -f "$tmp"

  # Reported, never silent: this modified a file in the user's repository.
  warn "Serena project config had an unloadable languages list (a list of mappings, which aborts activate_project)."
  ok   "Repaired $cfg — languages rewritten as a plain string list. Backup: $backup"
  return 1
}

# ── bounded command execution ─────────────────────────────────────────────────
# Run a command under a wall-clock bound and RETURN its exit status, reporting
# an expiry as 124 the way timeout(1) would.
#
# DELIBERATE DUPLICATION — this is a third copy of a mechanism that already
# exists twice, as serena_probe_rc() in scripts/setup-foundry.sh and daemon_rc()
# in hooks/session-start-serena.sh. A shared sourced helper was considered again
# here and rejected again, for a reason that is STRONGER in this file than in
# either of those: both of them are CALLERS of this script, whereas this is the
# script itself, so a new sourced file would put `start`, `stop`, `status`,
# `doctor` and `reconcile` alike behind a load path that can be missing on a
# partially-synced plugin directory (A-AUTO-007). The two existing copies also
# ECHO their status and hard-code the command they bound; this one RETURNS the
# status and bounds any command, so sharing would mean rewriting all three, not
# reusing one. Fix bugs in ALL THREE.
#
# No external binary is involved, and that is the entire point. timeout(1) is
# absent from stock macOS — it arrives only with coreutils, as gtimeout or as a
# PATH-shadowing gnubin — so detecting it and degrading to an unbounded call
# when it is missing leaves no bound at all on the priority platform (A-007),
# which is precisely the machine population this plugin reaches through a public
# marketplace in an unknown install state (A-AUTO-007). A bound that is absent
# where it is most needed is not a bound. Everything below is bash builtins plus
# ps/kill/sleep, so the bound holds everywhere this script itself runs.
#
# The subshell is load-bearing, and it lives INSIDE this function rather than at
# the call site: it confines job control to the bounded command, and it is where
# bash writes the "Terminated" job notice for a killed one. Its stderr therefore
# goes to /dev/null to discard that notice, and the command's OWN stderr is
# routed around that to fd 3 — so a genuine launchctl or systemctl diagnosis
# still reaches the user, and a caller that wants the command silenced composes
# exactly as it did on a bare call by writing `run_bounded ... 2>/dev/null`.
#
# Callers must guard the return under set -e (`if !` or `|| true`).
run_bounded() {
  local budget="$1"; shift
  (
    # fd 3 = the real stderr, captured BEFORE fd 2 is silenced. Redirections in
    # one exec are processed left to right, so this order is what makes fd 3 the
    # caller's stderr rather than a second handle on /dev/null.
    exec 3>&2 2>/dev/null

    local job_pid deadline expired rc pgid target

    # Job control, just long enough to fork the command, so it becomes the
    # leader of its OWN process group. Killing the leader alone is not enough:
    # bash does not forward signals to whatever the command shells out to, and a
    # wedged service-manager call would outlive it as an orphan still holding
    # the very resource that timed us out. Signalling the group takes the tree.
    #
    # stdin comes from /dev/null because under job control a background job that
    # reads the terminal is STOPPED by SIGTTIN rather than killed, and a stopped
    # job would sit in the wait below neither finishing nor dying.
    set -m
    "$@" 2>&3 </dev/null &
    job_pid=$!
    set +m

    # Poll, rather than race a background killer against the command. Escalation
    # then runs on this thread, so there is no second process to cancel and no
    # window where cancelling it skips the follow-up KILL and strands a child
    # that ignored TERM.
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
      sleep "$SERVICE_CALL_POLL_INTERVAL" || true
    done

    if [ "$expired" = true ]; then
      # Never signal a process group we do not own. `kill -TERM -$pid` on a pid
      # that is NOT a group leader lands on THIS script's group — and this
      # script runs inside the user's session, so that is the user's whole
      # session. So the group id is read back and the negative form is used only
      # where it provably equals the pid; anything else, including a ps that
      # answers nothing, falls back to signalling the single process. Read here
      # rather than at fork time because the command is known to be alive at
      # this instant, whereas one that had already exited would answer nothing.
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
    # A signalled command reports 143 or 137. Normalise to 124 so "the bound
    # expired" carries one code on every path.
    if [ "$expired" = true ]; then rc=124; fi
    exit "$rc"
  )
}

# ── reconcile ─────────────────────────────────────────────────────────────────
# Converge the installed OS service definition onto whatever the current source
# renders, repairing drift automatically and without being asked. This is
# invoked on every session start, so the converged path must cost nothing and
# say nothing.
#
# EXIT CODES — one code per terminal state, so a failed repair says WHICH stage
# failed rather than just "non-zero". Read WHAT IS ACTUALLY FROZEN HERE below
# before treating any of them as a contract: unlike doctor's, these are a
# diagnosis for whoever is reading a broken machine, not a value a caller reads.
#
#   0  converged | repaired  nothing needed, or drift was found and fully
#                            repaired. Both are success — a caller cannot and
#                            need not tell them apart from the code alone.
#   1  unsupported           OS is neither Darwin nor Linux; nothing was touched
#   2  render-failed         the definition could not be rendered, so drift
#                            could not even be determined. Nothing is written
#                            and nothing is unloaded: a machine that cannot be
#                            evaluated is left exactly as it was found.
#   3  write-failed          drift was found but the new definition could not be
#                            written to disk
#   4  reload-failed         the new definition was written, but the service
#                            manager would not reload it — either it refused
#                            outright, or its call did not return inside the
#                            bound run_bounded holds it to. Those are one state
#                            deliberately: a supervisor that will not answer in
#                            bounded time has not reloaded anything, and an
#                            expiry earns no code of its own
#   5  unconfirmed           the definition was written AND the service manager
#                            reloaded it, but no MCP handshake came back inside
#                            the probe budget. The supervisor accepting a job is
#                            not evidence the job runs — this is the state the
#                            29-day incident sat in while reporting success.
#   6  config-unrepaired     the OS service converged, but this project's
#                            .serena/project.yml carries a languages list Serena
#                            cannot load and the repair declined to guess at it
#
# Precedence when more than one applies: the SERVICE-DEFINITION verdicts (2, 3,
# 4, 5) outrank 6, because a project config matters only once something is
# actually serving. 6 is never silent — it warns first — so nothing is lost when
# a service verdict outranks it.
#
# WHAT IS ACTUALLY FROZEN HERE — established by reading the call sites, not by
# assuming. The distinction is between two DIFFERENT tables, and confusing them
# is the trap this paragraph exists to close.
#
#   NOTHING BRANCHES ON THE CODES ABOVE. The SessionStart hook is reconcile's
#   only caller: it runs the subcommand through its own daemon_rc watchdog under
#   RECONCILE_TIMEOUT and DISCARDS this status by explicit design, then asks
#   `doctor` what the model should be told. The foundry preflight never invokes
#   reconcile at all — commands/start.md forbids foundry from running any repair
#   subcommand, and the preflight bounds `doctor` alone. So no caller can be
#   broken by a change to the codes above. They are documented, and kept stable,
#   because a human reading a failed repair needs to know what the number meant
#   — not because a script reads it.
#
#   `doctor`'s SEPARATE 0-4 table is the frozen one. BOTH the hook and the
#   foundry preflight branch on it, which is exactly why GI-009 forbids parsing
#   its stdout: the integer is doctor's contract. A renumbering that is free
#   here is breaking there. Do not carry a conclusion from one table to the
#   other in either direction.
#
# An earlier revision of this paragraph claimed the codes were "safe to add or
# reorder below 2". Nothing exists below 2 except 0, so that described an empty
# set and gave a future editor no usable rule.
#
# BOUNDED SERVICE-MANAGER CALLS — every launchctl and systemctl call on the
# repair path runs under run_bounded, and an expiry is MAPPED onto the table
# above rather than given a code of its own:
#
#   * A STATUS-BEARING call — `launchctl load`, `systemctl restart` — that
#     expires is exit 4, for the reason recorded in that entry.
#   * A BEST-EFFORT call — both unloads, `daemon-reload`, `enable` — that
#     expires is absorbed exactly as its non-zero status already was, and the
#     post-load health probe adjudicates the consequence instead of this code
#     guessing at it. A legacy agent that survived its unload still holds $PORT,
#     so the handshake does not come back and the run ends at exit 5. That is
#     the honest verdict rather than a lost one, which is the whole point of
#     bounding these calls from inside this function.
#
# WHAT COUNTS AS DRIFT — two independent conditions, either of which triggers a
# repair:
#
#   (1) The installed artifact's bytes differ from what render_service_definition
#       emits. An absent artifact counts, so a machine that never ran
#       install-service is repaired into an installed one — which is the point:
#       recovery must not depend on the owner knowing install-service exists.
#       Byte comparison is the whole test. No version stamp is read, written or
#       relied on, because none exists in either artifact to consult (A-013).
#
#   (2) macOS only — a pre-rename com.codsworth.serena agent is still on disk.
#       That agent is supervised, binds this same $PORT, and carries a command
#       that can no longer be satisfied, so two agents contend for one port
#       (A-003). Its presence is a divergence from what current source describes
#       even when com.guild.serena.plist is itself byte-perfect, and A-014 has
#       reconcile clear it unconditionally — so it cannot be nested inside (1),
#       which may be false forever on exactly such a machine. Condition (1)
#       remains a pure byte comparison; this is a second trigger beside it, not
#       an extra input to it.
#
# Both conditions are self-clearing, which is what makes this idempotent: after
# one repair the artifact matches and the legacy file is gone, so every later
# run takes the silent path.
#
# WRITES TO MACHINE-LEVEL SERVICE STATE AUTOMATICALLY, by design (A-014). There
# is deliberately no confirmation prompt, no dry-run default and no --force
# opt-in gate.
#
# Deliberate consequence of the silent-when-converged rule: if a repair writes
# the definition but the reload fails (exit 4), later runs see a matching
# artifact and stay silent rather than retrying the reload. Reporting that state
# is `doctor`'s job (installed-but-stopped); repair is not re-attempted for a
# condition that already looks converged on disk.
cmd_reconcile() {
  local os rendered="" cfg_rc=0 pc_rc=0 probe_rc=0
  os="$(uname)"

  # ── Pass 1 of 2: this project's Serena config ──
  # Platform-independent, and run before the service branch so it still happens
  # on a host whose OS has no supported service manager. Silent unless it
  # actually repairs (or refuses to repair) something, which is what keeps the
  # converged session start silent overall.
  repair_project_config "${CLAUDE_PROJECT_DIR:-$PWD}" || pc_rc=$?
  if [ "$pc_rc" -eq 2 ]; then
    # Already warned by the helper. Carried as a sticky code rather than an
    # immediate exit: the OS service is the primary job and still has to run.
    cfg_rc=6
  fi

  # ── Pass 2 of 2: the OS service definition ──
  case "$os" in
    Darwin)
      local legacy="no" drifted="no"
      if [[ -f "$LEGACY_PLIST_FILE" ]]; then
        legacy="yes"
      fi

      # Render first. The renderer returns (never exits) non-zero, so it is
      # called as an `if` condition to keep set -e from aborting here. It prints
      # its own diagnosis to stderr on failure.
      if ! rendered="$(render_service_definition Darwin)"; then
        fail "cannot render launchd plist; leaving service state untouched"
        exit 2
      fi

      # $( ) strips trailing newlines, so the single trailing newline the
      # renderer guarantees is restored before comparing — that keeps this a
      # true byte comparison rather than a content-only one. A missing artifact
      # makes cmp fail, which is exactly the "absent counts as drift" verdict.
      if ! printf '%s\n' "$rendered" | cmp -s - "$PLIST_FILE"; then
        drifted="yes"
      fi

      # Converged on both conditions: write nothing, reload nothing, print
      # nothing. This is the path every ordinary session start takes — and with
      # a valid project config $cfg_rc is 0, so it stays a silent success.
      if [ "$drifted" = "no" ] && [ "$legacy" = "no" ]; then
        exit "$cfg_rc"
      fi

      if [ "$drifted" = "yes" ]; then
        info "Serena service definition is out of date — regenerating..."
        if ! mkdir -p "$HOME/Library/LaunchAgents"; then
          fail "cannot create $HOME/Library/LaunchAgents"
          exit 3
        fi
        # Write the very bytes that were just compared, rather than rendering a
        # second time: that is what guarantees the next run finds a match and
        # stays silent instead of re-repairing on every session start.
        if ! printf '%s\n' "$rendered" > "$PLIST_FILE"; then
          fail "cannot write $PLIST_FILE"
          exit 3
        fi
      fi

      if [ "$legacy" = "yes" ]; then
        info "Removing legacy launchd agent (com.codsworth.serena)..."
        # Best-effort and bounded: this is the agent most likely to be wedged,
        # since its command can no longer be satisfied. An expiry is absorbed
        # here and adjudicated by the post-load probe — see BOUNDED
        # SERVICE-MANAGER CALLS in the header above. The file is removed either
        # way, so the legacy trigger still self-clears and the next run is
        # silent. Kept 2>/dev/null: unloading an agent that is present on disk
        # but not loaded is a normal outcome here, not something to report.
        run_bounded "$RECONCILE_SERVICE_CALL_SECONDS" \
          launchctl unload "$LEGACY_PLIST_FILE" 2>/dev/null || true
        rm -f "$LEGACY_PLIST_FILE"
      fi

      # Reload decision.
      #
      # A rewritten definition always needs one — launchd keeps serving the old
      # one until the label is unloaded and loaded again.
      #
      # A legacy-only repair does not. The current agent may already be serving
      # perfectly well, and bouncing a healthy daemon on every session start
      # until the stale file happened to get removed would be gratuitous. So
      # health decides that case: serena_mcp_healthy is consulted only here, on
      # a path that has already committed to repairing something. That keeps it
      # off the converged path entirely — no cost, no output — and it cannot
      # stall a session, because the probe carries explicit connect and max
      # timeouts of its own.
      local reload="no" health_rc=0
      if [ "$drifted" = "yes" ]; then
        reload="yes"
      else
        serena_mcp_healthy || health_rc=$?
        if [ "$health_rc" -ne 0 ]; then
          reload="yes"
        fi
      fi

      if [ "$reload" = "yes" ]; then
        # Both bounded. The unload is best-effort — 2>/dev/null because "not
        # currently loaded" is the ordinary case on a machine being repaired for
        # the first time — and an expiry there is absorbed; the load below is
        # status-bearing, so its expiry is a verdict. launchctl's own stderr is
        # deliberately NOT silenced on the load: it carries launchd's reason,
        # which is the only diagnosis a user gets on a machine nobody debugging
        # this can see (A-AUTO-007).
        run_bounded "$RECONCILE_SERVICE_CALL_SECONDS" \
          launchctl unload "$PLIST_FILE" 2>/dev/null || true
        if ! run_bounded "$RECONCILE_SERVICE_CALL_SECONDS" \
             launchctl load "$PLIST_FILE"; then
          fail "definition written but launchctl could not load $PLIST_FILE (it refused, or did not return within ${RECONCILE_SERVICE_CALL_SECONDS}s)"
          exit 4
        fi

        # launchd accepted the job. Confirm the job actually SERVES before
        # claiming this reconcile worked — a plist whose command can never be
        # satisfied loads without complaint, and announcing "reconciled" on the
        # strength of that acceptance would reproduce the 29-day silent failure,
        # now automatically on every session start.
        #
        # Reached only on a repair path, so the converged run pays nothing for
        # it and stays silent (GI-008). Bounded by construction, so it cannot
        # stall a session (GI-003).
        probe_rc=0
        await_daemon_healthy "$RECONCILE_PROBE_SECONDS" || probe_rc=$?
        if [ "$probe_rc" -eq 1 ]; then
          warn "Serena service definition repaired and reloaded, but the daemon did not answer an MCP handshake on port $PORT within ${RECONCILE_PROBE_SECONDS}s — it may still be starting, or its command may be unsatisfiable. Diagnose with: serena-daemon.sh doctor"
          exit 5
        fi
        if [ "$probe_rc" -eq 2 ]; then
          warn "Serena service definition repaired and reloaded, but its health could NOT be verified (curl unavailable, or the port could not be read). Confirm with: serena-daemon.sh doctor"
          exit "$cfg_rc"
        fi
      fi

      ok "Serena service reconciled (macOS launchd)."
      exit "$cfg_rc"
      ;;
    Linux)
      # No legacy-agent condition here: com.codsworth.serena was a launchd
      # agent, so the pre-rename artifact only ever existed on macOS. Drift is
      # therefore the single trigger on this platform.
      local drifted="no"
      if ! rendered="$(render_service_definition Linux)"; then
        fail "cannot render systemd unit; leaving service state untouched"
        exit 2
      fi
      if ! printf '%s\n' "$rendered" | cmp -s - "$SYSTEMD_FILE"; then
        drifted="yes"
      fi
      if [ "$drifted" = "no" ]; then
        exit "$cfg_rc"
      fi

      info "Serena service definition is out of date — regenerating..."
      if ! mkdir -p "$HOME/.config/systemd/user"; then
        fail "cannot create $HOME/.config/systemd/user"
        exit 3
      fi
      if ! printf '%s\n' "$rendered" > "$SYSTEMD_FILE"; then
        fail "cannot write $SYSTEMD_FILE"
        exit 3
      fi

      # The two platforms' reload paths legitimately differ in shape (A-007
      # does not require behavioural parity), and they do: launchd needs the
      # label unloaded and loaded again, systemd needs daemon-reload to re-read
      # the unit file followed by a restart. `enable --now` — what
      # install-service uses on a fresh install — is NOT sufficient here,
      # because it does not disturb a unit that is already running and would
      # leave the daemon executing the stale ExecStart until the next boot.
      # `restart` starts a stopped unit and bounces a running one, so it is
      # unconditionally correct after drift and leaves the health probe nothing
      # to decide on this platform.
      if ! command -v systemctl >/dev/null 2>&1; then
        fail "definition written but systemctl is unavailable; unit not reloaded"
        exit 4
      fi
      # All three are bounded, and the restart is bounded DIFFERENTLY on
      # purpose. daemon-reload and enable are supervisor-only bookkeeping and
      # return immediately; restart blocks on the unit actually starting, and
      # this unit's ExecStart routes through this script's own `start`, so a
      # perfectly healthy restart legitimately takes seconds. Holding it to the
      # short bound would manufacture a false exit 4 on every good repair while
      # systemd carried the job on regardless — killing the systemctl client
      # does not cancel a queued job. See the two constants for the derivation.
      run_bounded "$RECONCILE_SERVICE_CALL_SECONDS" \
        systemctl --user daemon-reload 2>/dev/null || true
      run_bounded "$RECONCILE_SERVICE_CALL_SECONDS" \
        systemctl --user enable serena-daemon.service 2>/dev/null || true
      if ! run_bounded "$RECONCILE_UNIT_START_SECONDS" \
           systemctl --user restart serena-daemon.service; then
        fail "definition written but systemctl could not restart serena-daemon.service (it refused, or the unit did not start within ${RECONCILE_UNIT_START_SECONDS}s)"
        exit 4
      fi

      # `restart` blocks on the unit starting, so an ExecStart that cannot run
      # at all is already caught above. It is NOT caught that the unit started
      # and then serves nothing: Type=forking judges the launcher's exit, and
      # this script's `start` never attempts a handshake. Same probe as the
      # Darwin arm — it is an HTTP handshake and so platform-independent, which
      # is why sharing it does not force the two RELOAD paths (legitimately
      # different under A-007) any closer together.
      probe_rc=0
      await_daemon_healthy "$RECONCILE_PROBE_SECONDS" || probe_rc=$?
      if [ "$probe_rc" -eq 1 ]; then
        warn "Serena unit definition repaired and restarted, but the daemon did not answer an MCP handshake on port $PORT within ${RECONCILE_PROBE_SECONDS}s — it may still be starting, or its command may be unsatisfiable. Diagnose with: serena-daemon.sh doctor"
        exit 5
      fi
      if [ "$probe_rc" -eq 2 ]; then
        warn "Serena unit definition repaired and restarted, but its health could NOT be verified (curl unavailable, or the port could not be read). Confirm with: serena-daemon.sh doctor"
        exit "$cfg_rc"
      fi

      ok "Serena service reconciled (Linux systemd)."
      exit "$cfg_rc"
      ;;
    *)
      fail "unsupported OS: $os"
      exit 1
      ;;
  esac
}

# ── usage ─────────────────────────────────────────────────────────────────────
usage() {
  cat << EOF
Usage: serena-daemon.sh <subcommand>

Subcommands:
  start              Start the Serena MCP HTTP daemon (idempotent)
  stop               Stop the Serena MCP HTTP daemon
  status             Report daemon status and PID
  doctor             Full health/drift diagnosis (distinct exit code per state)
  reconcile          Repair drifted service definition + project config (silent when converged)
  restart            Stop then start
  install-service    Install OS service (launchd on macOS, systemd on Linux)
  uninstall-service  Remove OS service

Daemon command: uvx --from $SERENA_PKG serena start-mcp-server --context claude-code --transport streamable-http --port $PORT
PID file:       ~/.serena-daemon.pid
Log file:       ~/.serena-daemon.log
Port:           9121
EOF
}

# ── Dispatch ──────────────────────────────────────────────────────────────────
case "${1:-}" in
  start)             cmd_start ;;
  stop)              cmd_stop ;;
  status)            cmd_status ;;
  doctor)            cmd_doctor ;;
  reconcile)         cmd_reconcile ;;
  restart)           "$0" stop; "$0" start ;;
  install-service)   cmd_install_service ;;
  uninstall-service) cmd_uninstall_service ;;
  *)                 usage; exit 1 ;;
esac
