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
# This applies to git sources only. uvx also caches *directory* sources, and far
# more stubbornly: --refresh, --refresh-package and --reinstall all keep serving
# the stale build, and only `uv cache prune` clears it. So local entries do not
# use uvx at all — setup registers them as `uv run --directory`, which reads the
# tree on every launch. There is no cache to go stale and nothing to refresh,
# which is why --local exits early below after a single run check.
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
    # Local entries launch via `uv run --directory`, which reads the tree on
    # every start. There is no cached build to invalidate, so there is nothing
    # for this script to do beyond confirming the tree runs.
    MCP_SERVER_SRC="$PLUGIN_ROOT/mcp-server"
    info "Source: local working tree (--local)"
    info "  $MCP_SERVER_SRC"
    if VERSION="$(uv run --directory "$MCP_SERVER_SRC" foundry-mcp --version 2>&1)"; then
        echo ""
        ok "Local tree runs: $VERSION"
        ok "Nothing to refresh — local entries read the tree on every launch."
    else
        echo ""
        fail "Local tree does not run:"
        printf "%s\n" "$VERSION" >&2
        exit 1
    fi
    exit 0
else
    REGISTERED_SRC="$(resolve_registered_src)"
    if [[ -n "$REGISTERED_SRC" ]]; then
        MCP_SERVER_SRC="$REGISTERED_SRC"
        info "Source: registered foundry entry"
        warn "This is a legacy standalone registration — foundry >= 4.7.0 bundles its own"
        warn "server in the plugin manifest, and a standalone entry runs a SECOND server"
        warn "alongside it (one without FOUNDRY_MODEL, defeating the model option)."
        warn "After this refresh, remove it: re-run /foundry:setup or 'claude mcp remove foundry'."
    else
        # No standalone entry is the healthy state since 4.7.0: the plugin
        # manifest declares the server, launched via `uv run --project` from
        # the installed plugin tree on every start (cwd stays the user's
        # project; --project-root gets ${CLAUDE_PROJECT_DIR}) — no uvx cache
        # to refresh.
        info "No standalone foundry MCP entry registered — none is needed."
        info "The plugin bundles its own server (plugin.json mcpServers); it is"
        info "launched fresh from the installed plugin tree on every session."
        if VERSION="$(uv run --directory "$PLUGIN_ROOT/mcp-server" foundry-mcp --version 2>&1)"; then
            echo ""
            ok "Bundled server runs: $VERSION"
            ok "To upgrade it: claude plugin update foundry@guild, then restart Claude Code."
        else
            echo ""
            fail "Bundled server does not run:"
            printf "%s\n" "$VERSION" >&2
            exit 1
        fi
        exit 0
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
else
    printf "${BOLD}${GREEN}Updated:${RESET} %s → %s\n" "$BEFORE" "$AFTER"
fi
echo ""
printf "${BOLD}Restart Claude Code to load the updated server.${RESET}\n"
