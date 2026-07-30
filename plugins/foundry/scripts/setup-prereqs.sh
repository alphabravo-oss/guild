#!/usr/bin/env bash
# setup-prereqs.sh — Install everything needed for Foundry.
#
# Usage: bash scripts/setup-prereqs.sh [--project /path/to/project]
#
# Installs:
#   - Foundry MCP server (via uvx from this plugin)
#   - Playwright MCP (browser automation for SIGHT)
#   - Serena MCP (LSP wiring for TRACE)
#   - ralph-loop plugin (teammate execution engine)
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
while [[ $# -gt 0 ]]; do
  case $1 in
    --project) PROJECT_DIR="$2"; shift 2 ;;
    *) shift ;;
  esac
done

# Find plugin root (where this script lives)
PLUGIN_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
MCP_SERVER_DIR="$PLUGIN_ROOT/mcp-server"

# Find project root
if [[ -n "$PROJECT_DIR" ]]; then
  PROJECT_ROOT="$PROJECT_DIR"
elif [[ -f "$PWD/.mcp.json" ]] || [[ -f "$PWD/package.json" ]] || [[ -f "$PWD/go.mod" ]]; then
  PROJECT_ROOT="$PWD"
else
  PROJECT_ROOT="$PWD"
fi

info "Plugin root:  $PLUGIN_ROOT"
info "MCP server:   $MCP_SERVER_DIR"
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

# ── Install Plugins ──────────────────────────────────────────────────────────
info "Installing required plugins..."

# ralph-loop (teammate execution)
info "  Installing ralph-loop..."
if claude plugin marketplace add anthropics/claude-plugins-official 2>/dev/null; then
    claude plugin install ralph-loop 2>/dev/null && ok "  ralph-loop installed" || warn "  ralph-loop may already be installed"
else
    warn "  claude-plugins-official marketplace may already be added"
    claude plugin install ralph-loop 2>/dev/null && ok "  ralph-loop installed" || warn "  ralph-loop may already be installed"
fi

# hookify (optional but useful)
info "  Installing hookify..."
claude plugin install hookify 2>/dev/null && ok "  hookify installed" || warn "  hookify may already be installed"

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
import json, os

mcp_file = "$MCP_FILE"
mcp_server_dir = "$MCP_SERVER_DIR"

with open(mcp_file) as f:
    cfg = json.load(f)

servers = cfg.setdefault("mcpServers", {})

# Foundry MCP (the core state engine)
servers["foundry"] = {
    "command": "uvx",
    "args": ["--from", mcp_server_dir, "foundry-mcp", "--project-root", "."]
}

# Playwright MCP (browser automation for SIGHT)
if "playwright" not in servers:
    servers["playwright"] = {
        "command": "npx",
        "args": ["@playwright/mcp@latest", "--caps", "vision,devtools", "--output-dir", ".playwright-mcp"]
    }

# Serena MCP — shared HTTP daemon (all sessions connect; none fork)
servers["serena"] = {"type": "http", "url": "http://localhost:9121/mcp"}

with open(mcp_file, "w") as f:
    json.dump(cfg, f, indent=2)
    f.write("\n")

print(f"Configured: foundry, playwright, serena (HTTP) in {mcp_file}")
PYEOF

ok "MCP servers configured in $MCP_FILE"

# ── Serena project config ────────────────────────────────────────────────────
SERENA_DIR="$PROJECT_ROOT/.serena"
SERENA_CONFIG="$SERENA_DIR/project.yml"
info "Writing Serena project config..."
mkdir -p "$SERENA_DIR"

# Check the destination before writing, the same discipline the .mcp.json block
# above uses. An existing config is preserved to a timestamped backup and then
# rewritten — never skipped: configs written by earlier versions carry a
# list-of-mappings languages block that Serena cannot load, and only a rewrite
# repairs them. Under `set -e` a failed cp aborts before the overwrite, so the
# file is never destroyed without a backup landing first.
if [ -f "$SERENA_CONFIG" ]; then
    SERENA_BACKUP="$SERENA_CONFIG.$(date +%Y%m%d-%H%M%S).bak"
    cp "$SERENA_CONFIG" "$SERENA_BACKUP"
    warn "Existing Serena config backed up to $SERENA_BACKUP"
fi

# `languages` must be a flat list of plain strings. Serena's
# ProjectConfig._from_dict() calls .lower() on each element directly, so a
# mapping element (a list item carrying a `name:` key) raises AttributeError
# when the config loads.
cat > "$SERENA_CONFIG" << 'SERENA_EOF'
# Serena LSP configuration for Foundry TRACE verification
languages:
  - go
  - typescript
  - python

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
ok "Serena config written at $SERENA_CONFIG"

# ── Summary ──────────────────────────────────────────────────────────────────
echo ""
printf "${BOLD}${GREEN}Foundry setup complete.${RESET}\n"
echo ""
echo "Installed:"
echo "  Plugins:     ralph-loop, hookify"
echo "  MCP Servers: foundry (local), playwright (npx), serena (HTTP)"
echo "  Config:      $MCP_FILE, $SERENA_DIR/project.yml"
echo ""
echo "Commands available:"
echo "  /foundry:start \"scope\" --spec path/to/spec.md    Start building"
echo "  /foundry:resume                                  Resume interrupted run"
echo "  /foundry:status                                  Show run status"
echo "  /foundry:stop                                    Graceful stop"
echo ""
printf "${BOLD}Restart Claude Code to activate MCP servers.${RESET}\n"
echo ""
echo "Forge plans. Foundry builds."
