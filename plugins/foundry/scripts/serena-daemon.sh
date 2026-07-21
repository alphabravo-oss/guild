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
# Subcommands: start stop status restart install-service uninstall-service
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

# ── start ─────────────────────────────────────────────────────────────────────
# Idempotent. Never spawns a second process if one is already running.
cmd_start() {
  # 1. PID file liveness check.
  if [ -f "$PID_FILE" ]; then
    local pid
    pid="$(cat "$PID_FILE" 2>/dev/null || true)"
    if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
      ok "Serena daemon already running (PID $pid)"
      exit 0
    fi
    # Stale PID file — remove and continue.
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
  # 1. Trust the PID file when it points at a live process.
  if [ -f "$PID_FILE" ]; then
    local pid
    pid="$(cat "$PID_FILE" 2>/dev/null || true)"
    if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
      ok "Serena daemon: running (PID $pid)"
      exit 0
    fi
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
      cat > "$PLIST_FILE" << EOF
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
      # Route ExecStart through this script's `start` subcommand so the PID file
      # is written on every launch (including boot). Type=forking + PIDFile lets
      # systemd track the real daemon; Environment=PATH ensures `uvx` resolves
      # under the service's restricted PATH. WorkingDirectory pins the project
      # tree so Serena indexes the correct repo after a reboot.
      local uvx_dir
      uvx_dir="$(dirname "$uvx_path")"
      cat > "$SYSTEMD_FILE" << EOF
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

# ── usage ─────────────────────────────────────────────────────────────────────
usage() {
  cat << EOF
Usage: serena-daemon.sh <subcommand>

Subcommands:
  start              Start the Serena MCP HTTP daemon (idempotent)
  stop               Stop the Serena MCP HTTP daemon
  status             Report daemon status and PID
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
  restart)           "$0" stop; "$0" start ;;
  install-service)   cmd_install_service ;;
  uninstall-service) cmd_uninstall_service ;;
  *)                 usage; exit 1 ;;
esac
