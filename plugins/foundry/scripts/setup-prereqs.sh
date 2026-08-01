#!/usr/bin/env bash
# setup-prereqs.sh — Install everything needed for Foundry.
#
# Usage: bash scripts/setup-prereqs.sh [--project /path/to/project]
#
# Installs:
#   - Foundry MCP server (via uvx from this plugin)
#   - Playwright MCP (browser automation for SIGHT)
#   - Serena MCP (LSP wiring for TRACE) — only if its daemon starts
#   - Configures .mcp.json in the target project

set -euo pipefail

# ── Colors ────────────────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
RESET='\033[0m'

info()  { printf "${CYAN}[foundry]${RESET} %s\n" "$*"; }
ok()    { printf "${GREEN}[foundry]${RESET} %s\n" "$*"; }
warn()  { printf "${YELLOW}[foundry]${RESET} %s\n" "$*"; }
fail()  { printf "${RED}[foundry]${RESET} %s\n" "$*" >&2; }

# ── Parse arguments ──────────────────────────────────────────────────────────
PROJECT_DIR=""
USE_LOCAL_SRC=0
while [[ $# -gt 0 ]]; do
  case $1 in
    --project) PROJECT_DIR="$2"; shift 2 ;;
    --local)   USE_LOCAL_SRC=1; shift ;;
    *) shift ;;
  esac
done

# Find plugin root (where this script lives)
PLUGIN_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

# Where uvx pulls the foundry MCP server from.
#
# Default is the git remote, NOT "$PLUGIN_ROOT/mcp-server". The plugin cache is
# version-namespaced (~/.claude/plugins/cache/guild/foundry/4.4.0/...), and old
# version directories are retained after an update. A local path therefore pins
# the MCP entry to whatever version was current at setup time and keeps working
# after `claude plugin update` — silently serving the OLD server to the NEW
# commands. The git URL carries no version, so a user's .mcp.json never goes
# stale and never needs hand-editing.
#
# --local overrides this for maintainers developing against a working tree.
FOUNDRY_MCP_GIT="git+https://github.com/alphabravo-oss/guild#subdirectory=plugins/foundry/mcp-server"
if [[ $USE_LOCAL_SRC -eq 1 ]]; then
  MCP_SERVER_SRC="$PLUGIN_ROOT/mcp-server"
else
  MCP_SERVER_SRC="$FOUNDRY_MCP_GIT"
fi

# Find project root
if [[ -n "$PROJECT_DIR" ]]; then
  PROJECT_ROOT="$PROJECT_DIR"
elif [[ -f "$PWD/.mcp.json" ]] || [[ -f "$PWD/package.json" ]] || [[ -f "$PWD/go.mod" ]]; then
  PROJECT_ROOT="$PWD"
else
  PROJECT_ROOT="$PWD"
fi

info "Plugin root:  $PLUGIN_ROOT"
info "MCP server:   $MCP_SERVER_SRC"
info "Project root: $PROJECT_ROOT"

# ── Check prerequisites ─────────────────────────────────────────────────────
if ! command -v claude &>/dev/null; then
    fail "claude CLI not found. Install Claude Code first."
    exit 1
fi

if ! command -v python3 &>/dev/null; then
    fail "python3 not found. Required for foundry MCP server."
    exit 1
fi

if ! command -v npx &>/dev/null; then
    warn "npx not found. Playwright MCP (SIGHT) won't work."
fi

if ! command -v uvx &>/dev/null; then
    warn "uvx not found. Install uv: curl -LsSf https://astral.sh/uv/install.sh | sh"
    warn "Serena MCP (TRACE) and Foundry MCP require uvx."
fi

# ── Plugin dependencies ──────────────────────────────────────────────────────
# Previously this installed ralph-loop (billed as the "teammate execution
# engine") and hookify, and added the claude-plugins-official marketplace to do
# it. Neither is referenced anywhere in foundry — not in commands, agents,
# skills, scripts, or the MCP server. Teammates are spawned through the Agent /
# TeamCreate / SendMessage tools declared in commands/start.md. Installing
# plugins a user did not ask for, to satisfy a dependency that does not exist,
# is not setup's business.
#
# If teammate execution is ever routed through ralph-loop for real, reinstate
# the install here AND make something actually call it.

# ── Start the Serena daemon ─────────────────────────────────────────────────
# The serena MCP entry is an HTTP pointer at localhost:9121. Writing that entry
# without a daemon behind it produces a dead server that fails to connect on
# every session start, which is how this shipped previously. Bring the daemon up
# first and only register serena if it actually answers.
SERENA_UP=0
SERENA_DAEMON="$PLUGIN_ROOT/scripts/serena-daemon.sh"

if [[ -x "$SERENA_DAEMON" ]] || [[ -f "$SERENA_DAEMON" ]]; then
    info "Starting Serena daemon..."
    if bash "$SERENA_DAEMON" start; then
        if bash "$SERENA_DAEMON" status >/dev/null 2>&1; then
            SERENA_UP=1
            ok "Serena daemon is up on :9121"
        else
            warn "Serena daemon started but is not answering on :9121 — skipping serena MCP entry"
        fi
    else
        warn "Serena daemon failed to start — skipping serena MCP entry"
        warn "  Retry manually: bash $SERENA_DAEMON start"
    fi
else
    warn "serena-daemon.sh not found at $SERENA_DAEMON — skipping serena MCP entry"
fi

# ── Configure .mcp.json ─────────────────────────────────────────────────────
info "Configuring MCP servers..."

MCP_FILE="$PROJECT_ROOT/.mcp.json"

if [ -f "$MCP_FILE" ]; then
    info "Updating existing .mcp.json..."
else
    info "Creating .mcp.json..."
    echo '{"mcpServers": {}}' > "$MCP_FILE"
fi

# Use python3 for safe JSON manipulation
python3 << PYEOF
import json

mcp_file = "$MCP_FILE"
mcp_server_src = "$MCP_SERVER_SRC"
serena_up = "$SERENA_UP" == "1"
use_local = "$USE_LOCAL_SRC" == "1"

with open(mcp_file) as f:
    cfg = json.load(f)

servers = cfg.setdefault("mcpServers", {})
configured = []

# Foundry MCP (the core state engine).
# --project-root is relative and resolved against the server process cwd, which
# Claude Code sets to the session's project directory. That is what makes this
# entry portable enough to live at user scope instead of per-project.
#
# Two different launchers, on purpose:
#   remote (default) — uvx against the git URL. Version-free, so the entry never
#     goes stale, at the cost of needing an explicit refresh to move commits.
#   local (--local)  — `uv run --directory`, NOT uvx. uvx caches a directory
#     source and will not release it: --refresh, --refresh-package and
#     --reinstall all keep serving the stale build, and only `uv cache prune`
#     clears it. `uv run --directory` reads the tree on every launch, so a
#     maintainer's edits take effect immediately with no cache step at all.
if use_local:
    servers["foundry"] = {
        "command": "uv",
        "args": ["run", "--directory", mcp_server_src, "foundry-mcp", "--project-root", "."]
    }
else:
    servers["foundry"] = {
        "command": "uvx",
        "args": ["--from", mcp_server_src, "foundry-mcp", "--project-root", "."]
    }
configured.append("foundry")

# Playwright MCP (browser automation for SIGHT)
if "playwright" not in servers:
    servers["playwright"] = {
        "command": "npx",
        "args": ["@playwright/mcp@latest", "--caps", "vision,devtools", "--output-dir", ".playwright-mcp"]
    }
configured.append("playwright")

# Serena MCP — shared HTTP daemon (all sessions connect; none fork).
# Only registered when the daemon is confirmed up; a stale entry pointing at a
# dead port is worse than no entry, because TRACE silently falls back to grep.
if serena_up:
    servers["serena"] = {"type": "http", "url": "http://localhost:9121/mcp"}
    configured.append("serena")
else:
    servers.pop("serena", None)

with open(mcp_file, "w") as f:
    json.dump(cfg, f, indent=2)
    f.write("\n")

print(f"Configured: {', '.join(configured)} in {mcp_file}")
if not serena_up:
    print("Skipped: serena (daemon not running)")
PYEOF

ok "MCP servers configured in $MCP_FILE"

# ── Install / refresh the foundry MCP server ────────────────────────────────
# Delegated to update-mcp.sh so the refresh logic lives in exactly one place —
# setup and /foundry:update must never drift apart.
UPDATE_SCRIPT="$PLUGIN_ROOT/scripts/update-mcp.sh"
UPDATE_ARGS=(--project "$PROJECT_ROOT")
[[ $USE_LOCAL_SRC -eq 1 ]] && UPDATE_ARGS+=(--local)

MCP_SERVER_OK=0
if [[ -f "$UPDATE_SCRIPT" ]]; then
    if bash "$UPDATE_SCRIPT" "${UPDATE_ARGS[@]}"; then
        MCP_SERVER_OK=1
    else
        warn "MCP server refresh failed — run /foundry:update once the cause is fixed"
    fi
else
    warn "update-mcp.sh not found at $UPDATE_SCRIPT — skipping MCP server refresh"
fi

# ── Serena project config ────────────────────────────────────────────────────
SERENA_DIR="$PROJECT_ROOT/.serena"
info "Writing Serena project config..."
mkdir -p "$SERENA_DIR"
cat > "$SERENA_DIR/project.yml" << 'SERENA_EOF'
# Serena LSP configuration for Foundry TRACE verification
languages:
  - name: go
  - name: typescript
  - name: python

ignored_paths:
  - node_modules
  - vendor
  - .git
  - dist
  - build
  - __pycache__
  - .venv
  - forge-specs
  - foundry-archive
  - .serena
SERENA_EOF
ok "Serena config written at $SERENA_DIR/project.yml"

# ── Summary ──────────────────────────────────────────────────────────────────
# Report what actually happened. A setup script that prints "complete" over a
# failed step is how the serena and ralph-loop problems survived: its output was
# the only evidence anyone had, and it was wrong.
echo ""
if [[ $MCP_SERVER_OK -eq 1 ]]; then
  printf "${BOLD}${GREEN}Foundry setup complete.${RESET}\n"
else
  printf "${BOLD}${YELLOW}Foundry setup finished with errors.${RESET}\n"
  printf "${YELLOW}The MCP server is NOT installed — foundry will not run.${RESET}\n"
fi
echo ""
echo "Installed:"
if [[ $MCP_SERVER_OK -eq 1 ]]; then
  echo "  MCP Servers: foundry (uvx)"
else
  echo "  MCP Servers: foundry FAILED — see the error above, then /foundry:update"
fi
if command -v npx &>/dev/null; then
  echo "               playwright (npx) — SIGHT stream"
else
  echo "               playwright NOT usable — npx not found, SIGHT will not run"
fi
if [[ $SERENA_UP -eq 1 ]]; then
  echo "               serena (HTTP :9121) — TRACE / FLOW_TRACE"
else
  echo "               serena NOT registered — daemon is down"
  echo "                 TRACE falls back to grep and reports it"
  echo "                 Start it with:   bash $SERENA_DAEMON start"
  echo "                 Survive reboots: bash $SERENA_DAEMON install-service"
fi
echo "  MCP source:  $MCP_SERVER_SRC"
echo "  Config:      $MCP_FILE, $SERENA_DIR/project.yml"
echo ""
echo "To upgrade the foundry MCP server later, run /foundry:update."
echo "Your .mcp.json carries no version and never needs hand-editing."
echo ""
echo "Commands available:"
echo "  /foundry:start \"scope\" --spec path/to/spec.md    Start building"
echo "  /foundry:resume                                  Resume interrupted run"
echo "  /foundry:status                                  Show run status"
echo "  /foundry:stop                                    Graceful stop"
echo "  /foundry:update                                  Update the MCP server"
echo ""
printf "${BOLD}Restart Claude Code to activate MCP servers.${RESET}\n"
echo ""
echo "Forge plans. Foundry builds."
