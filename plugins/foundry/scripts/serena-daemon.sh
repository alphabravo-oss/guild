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
# Subcommands: start stop status doctor restart install-service uninstall-service
#
# Streamable-HTTP transport only (SSE is broken for Claude Code, issue #196).

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
# Serena is not published to PyPI as "serena-mcp" — it is distributed from the
# oraios/serena git repo and exposes the `serena` console script. Every launch
# site (manual start, launchd plist) must use `uvx --from "$SERENA_PKG" serena`.
SERENA_PKG="git+https://github.com/oraios/serena"
PID_FILE="$HOME/.serena-daemon.pid"
LOG_FILE="$HOME/.serena-daemon.log"
PLIST_FILE="$HOME/Library/LaunchAgents/com.guild.serena.plist"
# Pre-rename label. Installs that predate the codsworth -> guild rename still have
# this agent loaded and holding $PORT, so install/uninstall must clear it too.
LEGACY_PLIST_FILE="$HOME/Library/LaunchAgents/com.codsworth.serena.plist"
SYSTEMD_FILE="$HOME/.config/systemd/user/serena-daemon.service"

# Absolute path to this script (used by the systemd unit's ExecStart so the
# PID file is written on every launch — including service-managed boots).
SCRIPT_PATH="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd -P)/$(basename -- "${BASH_SOURCE[0]}")"

# ── Helpers ───────────────────────────────────────────────────────────────────
# Echo the PID(s) of any Serena MCP server process currently bound to $PORT.
#
# uvx (uv tool run) does NOT serve under the PID captured by `$!` at launch — it
# spawns/execs the real server under a different PID. So the recorded PID alone
# cannot be trusted to find, monitor, or terminate the daemon. Resolving by port
# is the only reliable way to locate the actual long-lived process, regardless of
# how it was started (manual `start` or an OS service manager).
serena_port_pids() {
  local pids="" p cmdline out=""
  if command -v lsof >/dev/null 2>&1; then
    pids="$(lsof -i :"$PORT" -t 2>/dev/null || true)"
  fi
  for p in $pids; do
    cmdline="$(ps -p "$p" -o command= 2>/dev/null || true)"
    # Match the server invocation, not a package name: the real process runs
    # as `.../bin/serena start-mcp-server ...` (via uvx --from git+.../serena).
    if printf '%s' "$cmdline" | grep -q "start-mcp-server"; then
      out="$out $p"
    fi
  done
  printf '%s' "${out# }"
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
#   1  not running  — nothing serving on $PORT (cheap check found no process)
#   2  unresponsive — port is held but no valid handshake came back; this is
#                     the wedged-daemon case a restart is expected to clear
#   3  unverifiable — port is held but curl is absent, so MCP-level health
#                     could not be established either way. Deliberately not 0:
#                     an unprobed daemon is never "verified healthy". Callers
#                     should report uncertainty here, not force a restart.
serena_mcp_healthy() {
  local pids="" body="" req="" re_proto="" re_info=""

  # LAYER 1 — cheap, local, no network: is a Serena server even on the port?
  # Reuses the resolver above rather than re-parsing lsof. Nothing there means
  # unhealthy, and we return without issuing any HTTP request at all.
  pids="$(serena_port_pids || true)"
  if [ -z "$pids" ]; then
    return 1
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
    return 0
  fi

  return 2
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

  # 2. Port in-use check.
  local port_pids=""
  if command -v lsof >/dev/null 2>&1; then
    port_pids="$(lsof -i :"$PORT" -t 2>/dev/null || true)"
    if [ -n "$port_pids" ]; then
      local found_serena=""
      local p cmdline
      for p in $port_pids; do
        cmdline="$(ps -p "$p" -o command= 2>/dev/null || true)"
        if printf '%s' "$cmdline" | grep -q "start-mcp-server"; then
          found_serena="$p"
        fi
      done
      if [ -n "$found_serena" ]; then
        # Serena already on the port but no valid PID file — adopt it.
        echo "$found_serena" > "$PID_FILE"
        ok "Serena daemon already running (PID $found_serena)"
        exit 0
      fi
      fail "port $PORT in use by another process"
      exit 1
    fi
  elif command -v ss >/dev/null 2>&1; then
    # Linux fallback: detect occupancy only (cannot identify the process).
    if ss -tlnp "sport = :$PORT" 2>/dev/null | grep -q "$PORT"; then
      fail "port $PORT in use by another process"
      exit 1
    fi
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
  nohup uvx --from "$SERENA_PKG" serena start-mcp-server \
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

  # Build the target set: recorded PID (if alive) plus every Serena server on $PORT.
  # Duplicates are harmless — a second kill on the same PID is a no-op.
  local targets=""
  if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
    targets="$pid"
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
  case "${1:-}" in
    Darwin)
      local uvx_path
      uvx_path="$(command -v uvx || true)"
      if [ -z "$uvx_path" ]; then
        fail "uvx not found; cannot render launchd plist"
        return 2
      fi
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
    <string>--transport</string>
    <string>streamable-http</string>
    <string>--port</string>
    <string>$PORT</string>
  </array>
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
      local uvx_path
      uvx_path="$(command -v uvx || true)"
      if [ -z "$uvx_path" ]; then
        fail "uvx not found; cannot render systemd unit"
        return 2
      fi
      # Route ExecStart through this script's `start` subcommand so the PID file
      # is written on every launch (including boot). Type=forking + PIDFile lets
      # systemd track the real daemon; Environment=PATH ensures `uvx` resolves
      # under the service's restricted PATH. WorkingDirectory pins the project
      # tree so Serena indexes the correct repo after a reboot.
      local uvx_dir
      uvx_dir="$(dirname "$uvx_path")"
      cat << EOF
[Unit]
Description=Serena MCP HTTP Daemon (guild)
After=network.target

[Service]
Type=forking
PIDFile=%h/.serena-daemon.pid
Environment=PATH=$uvx_dir:/usr/local/bin:/usr/bin:/bin
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
    *)
      fail "unsupported OS: ${1:-}"
      return 1
      ;;
  esac
}

# ── install-service ───────────────────────────────────────────────────────────
cmd_install_service() {
  local os
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
      ok "Serena service installed and loaded (macOS launchd). Daemon will start at login."
      exit 0
      ;;
    Linux)
      mkdir -p "$HOME/.config/systemd/user"
      render_service_definition Linux > "$SYSTEMD_FILE"
      systemctl --user daemon-reload 2>/dev/null || true
      systemctl --user enable --now serena-daemon.service
      ok "Serena service installed and enabled (Linux systemd). Daemon will start at login."
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

# ── doctor ────────────────────────────────────────────────────────────────────
# One-shot diagnostic sweep. Reports seven independent dimensions, one line
# each, then summarises them in a single exit code.
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
# Drift that cannot be determined (the renderer could not run) is reported as
# such and does NOT become code 4: claiming drift would send a caller into a
# reconcile that fails for the very same reason.
#
# READ-ONLY by contract. Unlike `status`, doctor never writes or clears the PID
# file, never installs, and never restarts. Diagnosis and repair are kept apart
# so a caller can ask "what is wrong?" without changing anything.
cmd_doctor() {
  local os port_pids_raw="" port_count=0 p
  local lsof_seen="no" health_rc=0 serena_pids=""
  local pidfile_state="absent" pidfile_pid="" pid_cmdline=""
  local platform_ok="yes" artifact="" installed="no"
  local drift="n/a" rendered=""
  local last_exit="unknown"
  local dup_count=0 dup_line="" log_state="absent" top=""

  os="$(uname)"

  # ── Gather (no output; every optional tool is guarded so a missing binary or
  #    an expected failure degrades a line instead of aborting under set -e) ──

  # (a) Raw port occupancy. `lsof -i` lists every process holding a socket on
  #     the port — the listening server AND every connected client — so this is
  #     an occupancy signal only, never a count of Serena processes. Dimension
  #     (c) is the authoritative "is the server alive" answer.
  if command -v lsof >/dev/null 2>&1; then
    lsof_seen="yes"
    port_pids_raw="$(lsof -i :"$PORT" -t 2>/dev/null || true)"
  fi
  for p in $port_pids_raw; do
    port_count=$((port_count + 1))
  done

  # (b) MCP handshake, delegated to the probe: 0 healthy, 1 not running,
  #     2 unresponsive, 3 unverifiable. It prints nothing, and `|| health_rc=$?`
  #     keeps an expected unhealthy verdict from killing this function.
  serena_mcp_healthy || health_rc=$?

  # (c) The Serena server process, resolved by port + cmdline rather than by a
  #     bare PID-file read, plus whether the recorded PID can still be trusted.
  serena_pids="$(serena_port_pids || true)"
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
  if [ -f "$LOG_FILE" ]; then
    log_state="scanned"
    top="$(tail -n 200 "$LOG_FILE" 2>/dev/null \
      | awk '/[Ee]rror|ERROR|Traceback|Exception|CRITICAL|[Ff]ailed|[Ff]atal/ { c[$0]++ }
             END { m = 0; l = ""
                   for (k in c) { if (c[k] > m) { m = c[k]; l = k } }
                   if (m > 1) printf "%d\t%s", m, l }' || true)"
    if [ -n "$top" ]; then
      dup_count="${top%%$'\t'*}"
      dup_line="$(printf '%s' "${top#*$'\t'}" | cut -c1-120 || true)"
    fi
  fi

  # ── Report: one line per dimension, always all seven ──
  info "Serena doctor — port $PORT"

  # (a) port bound?
  if [ "$lsof_seen" != "yes" ]; then
    warn "port $PORT ............ unknown (lsof not available)"
  elif [ "$port_count" -eq 0 ]; then
    warn "port $PORT ............ not bound"
  else
    ok "port $PORT ............ bound ($port_count socket holder(s), clients included)"
  fi

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

  # ── Verdict: see EXIT CODE CONTRACT above. Every branch exits explicitly;
  #    nothing falls off the end of this function. ──
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

# ── usage ─────────────────────────────────────────────────────────────────────
usage() {
  cat << EOF
Usage: serena-daemon.sh <subcommand>

Subcommands:
  start              Start the Serena MCP HTTP daemon (idempotent)
  stop               Stop the Serena MCP HTTP daemon
  status             Report daemon status and PID
  doctor             Full health/drift diagnosis (distinct exit code per state)
  restart            Stop then start
  install-service    Install OS service (launchd on macOS, systemd on Linux)
  uninstall-service  Remove OS service

Daemon command: uvx --from git+https://github.com/oraios/serena serena start-mcp-server --transport streamable-http --port 9121
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
  restart)           "$0" stop; "$0" start ;;
  install-service)   cmd_install_service ;;
  uninstall-service) cmd_uninstall_service ;;
  *)                 usage; exit 1 ;;
esac
