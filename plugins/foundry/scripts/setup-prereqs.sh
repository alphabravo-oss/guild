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

# curl carries the MCP `initialize` handshake in serena-daemon.sh's health probe.
# Advisory, not fatal: the probe already degrades to a port-only check without it
# and the daemon itself runs fine, so this warns at the same level as uvx — a
# strictly harder dependency this script also declines to hard-fail on. Naming
# the later symptom here is the point: it is otherwise invisible why a healthy
# daemon reports as unverifiable.
if ! command -v curl &>/dev/null; then
    warn "curl not found. Serena MCP health probing degrades to a port-only check."
    warn "Expect 'MCP handshake ... unverifiable' from serena-daemon.sh doctor and the SessionStart hook."
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

# Directories excluded from BOTH the language census below and the generated
# ignored_paths block. One array feeds both, so the two can never drift apart:
# a directory the census counted but Serena then ignored would put a language
# server in the config with no files to serve.
SERENA_IGNORED_PATHS=(
    node_modules vendor .git dist build __pycache__ .venv
    forge-specs foundry-archive .serena
)

# Used only when the census finds nothing. It must stay non-empty: an empty
# languages list disables Serena's LSP backend outright, which is the same
# silent degradation this generator exists to prevent. Held to interpreters
# whose language servers are cheap to install, since by definition nothing was
# found here to justify more.
SERENA_FALLBACK_LANGUAGES=(python bash)

# Census the tree instead of assuming a language set. setup-prereqs.sh ships to
# other people's repositories, so any fixed list is wrong for most of them — and
# a list that names languages the project does not contain, while omitting the
# ones it does, leaves the LSP backend with nothing to parse.
#
# Extensions resolve through a whitelist because Serena matches each entry
# against its Language enum: an identifier it does not know raises at config
# load, the same class of failure as the mapping-shaped list this generator used
# to write. Markup and data formats are deliberately absent — they match in
# nearly every repository and would start language servers TRACE has no use for,
# ranked ahead of the code the run is actually verifying.
#
# Ordered by file count, descending. Serena treats the first entry as the
# default language server, so the repository's dominant language leads.
detect_serena_languages() {
    local root="$1" dir
    local prune=()
    for dir in "${SERENA_IGNORED_PATHS[@]}"; do
        [ "${#prune[@]}" -eq 0 ] || prune+=( -o )
        prune+=( -name "$dir" )
    done

    find "$root" \( "${prune[@]}" \) -prune -o -type f -print 2>/dev/null |
    awk '
        BEGIN {
            map["sh"]="bash";        map["bash"]="bash"
            map["py"]="python"
            map["go"]="go"
            map["ts"]="typescript";  map["tsx"]="typescript"
            map["js"]="typescript";  map["jsx"]="typescript"
            map["mjs"]="typescript"; map["cjs"]="typescript"
            map["rs"]="rust"
            map["java"]="java"
            map["rb"]="ruby"
            map["php"]="php"
            map["cs"]="csharp"
            map["kt"]="kotlin";      map["kts"]="kotlin"
            map["swift"]="swift"
            map["c"]="cpp";          map["h"]="cpp"
            map["cpp"]="cpp";        map["cc"]="cpp"
            map["cxx"]="cpp";        map["hpp"]="cpp"
            map["lua"]="lua"
            map["ex"]="elixir";      map["exs"]="elixir"
            map["dart"]="dart"
            map["scala"]="scala"
            map["zig"]="zig"
            map["tf"]="terraform"
            map["nix"]="nix"
            map["clj"]="clojure";    map["cljs"]="clojure"
            map["pl"]="perl";        map["pm"]="perl"
            map["ps1"]="powershell"
            map["sol"]="solidity"
            map["vue"]="vue"
            map["svelte"]="svelte"
            map["hs"]="haskell"
            map["ml"]="ocaml"
            map["erl"]="erlang"
        }
        {
            base = $0
            sub(/^.*\//, "", base)
            if (base !~ /\./) next
            ext = base
            sub(/^.*\./, "", ext)
            lang = map[tolower(ext)]
            if (lang != "") count[lang]++
        }
        END { for (l in count) printf "%d\t%s\n", count[l], l }
    ' | sort -k1,1nr -k2,2 | cut -f2
}

# `|| true` because a census that finds nothing is an ordinary outcome handled
# by SERENA_FALLBACK_LANGUAGES below, not a failure set -e should abort on.
SERENA_LANGUAGES=()
while IFS= read -r serena_lang; do
    [ -n "$serena_lang" ] || continue
    SERENA_LANGUAGES+=("$serena_lang")
done < <(detect_serena_languages "$PROJECT_ROOT" || true)

if [ "${#SERENA_LANGUAGES[@]}" -eq 0 ]; then
    SERENA_LANGUAGES=("${SERENA_FALLBACK_LANGUAGES[@]}")
    warn "No recognised source files under $PROJECT_ROOT."
    warn "Serena languages set to: ${SERENA_LANGUAGES[*]} — edit $SERENA_CONFIG if that is wrong."
else
    info "Detected Serena languages: ${SERENA_LANGUAGES[*]}"
fi

# One redirection for the whole file: a single truncation and a single write
# path, so a failure part-way cannot leave a half-written config that the backup
# taken above is the only record of.
#
# Every element of `languages` is emitted as a plain string. Serena's
# ProjectConfig._from_dict() calls .lower() on each element directly, so a
# mapping element (a list item carrying a `name:` key) raises AttributeError
# when the config loads.
{
    printf '%s\n' '# Serena LSP configuration for Foundry TRACE verification'
    printf 'languages:\n'
    printf '  - %s\n' "${SERENA_LANGUAGES[@]}"
    printf '\n'
    printf 'ignored_paths:\n'
    printf '  - %s\n' "${SERENA_IGNORED_PATHS[@]}"
} > "$SERENA_CONFIG"
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
