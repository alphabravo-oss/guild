#!/usr/bin/env bash
# update-mcp.sh — Upgrade the foundry MCP server to the latest published code.
#
# Usage: update-mcp.sh [--project /path/to/project] [--local]
#
# Why this exists: the MCP entry points uvx at an unpinned git URL. uvx caches
# the git build by resolved commit and does NOT re-resolve HEAD on later runs —
# verified: a second invocation without --refresh reuses the cached environment
# and prints no "Updating <url>" line. So new server code is never picked up on
# its own. `uv cache clean foundry-mcp` is not sufficient either; it removes
# package artifacts but leaves the uvx tool environment intact. Only --refresh
# actually re-resolves and rebuilds.
#
# KNOWN LIMITATION (--local only): --refresh reliably re-resolves *git* sources
# but does NOT pick up edits to a local directory source — verified against
# --refresh, --refresh-package and --reinstall, all of which silently served the
# stale build. Only `uv cache prune` clears the sticky uvx tool environment. The
# shipped path is the git URL and is unaffected; maintainers running --local get
# an explicit warning below when the version does not move.
#
# This script is the whole upgrade path. setup-prereqs.sh calls it too, so there
# is one implementation rather than two that drift.

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

PLUGIN_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PROJECT_ROOT="${PROJECT_DIR:-$PWD}"
FOUNDRY_MCP_GIT="git+https://github.com/alphabravo-oss/guild#subdirectory=plugins/foundry/mcp-server"

if ! command -v uvx &>/dev/null; then
    fail "uvx not found. Install uv: curl -LsSf https://astral.sh/uv/install.sh | sh"
    exit 1
fi

# ── Resolve which source to refresh ──────────────────────────────────────────
# Refresh whatever the user actually registered, not a hardcoded default — a
# maintainer on a --local entry should not be silently switched to the remote.
# Project .mcp.json wins over user scope, mirroring Claude Code's own precedence.
resolve_registered_src() {
    python3 - "$PROJECT_ROOT" <<'PYEOF' 2>/dev/null || true
import json, os, sys

project_root = sys.argv[1]

def from_arg(entry):
    args = entry.get("args") or []
    if "--from" in args:
        i = args.index("--from")
        if i + 1 < len(args):
            return args[i + 1]
    return None

for path in (os.path.join(project_root, ".mcp.json"),
             os.path.expanduser("~/.claude.json")):
    try:
        with open(path) as f:
            cfg = json.load(f)
    except (OSError, ValueError):
        continue

    candidates = [cfg.get("mcpServers") or {}]
    # ~/.claude.json also carries per-project (local-scope) registrations.
    for proj in (cfg.get("projects") or {}).values():
        candidates.append(proj.get("mcpServers") or {})

    for servers in candidates:
        entry = servers.get("foundry")
        if entry:
            src = from_arg(entry)
            if src:
                print(src)
                sys.exit(0)
PYEOF
}

if [[ $USE_LOCAL_SRC -eq 1 ]]; then
    MCP_SERVER_SRC="$PLUGIN_ROOT/mcp-server"
    info "Source: local working tree (--local)"
else
    REGISTERED_SRC="$(resolve_registered_src)"
    if [[ -n "$REGISTERED_SRC" ]]; then
        MCP_SERVER_SRC="$REGISTERED_SRC"
        info "Source: registered foundry entry"
    else
        MCP_SERVER_SRC="$FOUNDRY_MCP_GIT"
        warn "No foundry MCP entry found — falling back to the default git remote"
        warn "  Register it with:"
        warn "  claude mcp add -s user foundry -- uvx --from \"$FOUNDRY_MCP_GIT\" foundry-mcp --project-root ."
    fi
fi
info "  $MCP_SERVER_SRC"

# ── Report the version currently being served ────────────────────────────────
BEFORE="$(uvx --from "$MCP_SERVER_SRC" foundry-mcp --version 2>/dev/null || echo "unknown")"
info "Installed: $BEFORE"

# ── Refresh ──────────────────────────────────────────────────────────────────
info "Refreshing from source..."
if ! AFTER="$(uvx --refresh --from "$MCP_SERVER_SRC" foundry-mcp --version 2>&1)"; then
    fail "Refresh failed:"
    printf "%s\n" "$AFTER" >&2
    echo ""
    fail "The server was rebuilt but cannot start. This usually means a dependency"
    fail "resolved to an incompatible major version. Check the traceback above."
    exit 1
fi

echo ""
if [[ "$BEFORE" == "$AFTER" ]]; then
    ok "Already up to date: $AFTER"
    if [[ $USE_LOCAL_SRC -eq 1 ]]; then
        echo ""
        warn "Running against a local working tree. If you edited the server and the"
        warn "version above did not move, uvx is serving a stale cached build —"
        warn "--refresh does not invalidate it for directory sources. Clear it with:"
        warn "  uv cache prune"
        warn "then re-run this script."
    fi
else
    printf "${BOLD}${GREEN}Updated:${RESET} %s → %s\n" "$BEFORE" "$AFTER"
fi
echo ""
printf "${BOLD}Restart Claude Code to load the updated server.${RESET}\n"
