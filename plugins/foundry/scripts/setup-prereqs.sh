#!/usr/bin/env bash
# setup-prereqs.sh — Install everything needed for Foundry.
#
# Usage: bash scripts/setup-prereqs.sh [--project /path/to/project]
#
# Installs:
#   - Playwright MCP (browser automation for SIGHT)
#   - Serena MCP (LSP wiring for TRACE) — only if its daemon starts
#   - Configures .mcp.json in the target project
#
# The Foundry MCP server itself is NOT registered here: since 4.7.0 the plugin
# manifest declares it (plugin.json mcpServers), launched from the installed
# plugin tree. This script only verifies it runs and migrates away any legacy
# project-scope entry.

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
info "MCP server:   bundled with the plugin (plugin.json mcpServers)"
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
# Bring the daemon up now so a fresh install is usable in this session rather
# than the next one. Whether it comes up does NOT gate the serena MCP entry:
# the SessionStart hook runs reconcile -> doctor -> start on every session, so a
# daemon that is down right now is repaired automatically later. Deleting the
# entry here would make that repair unreachable — the hook would start a daemon
# with nothing configured to connect to it. So a failure below is reported, not
# acted on.
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
# QUOTED heredoc, values passed through the environment. This is deliberate and
# load-bearing: with an UNQUOTED heredoc the shell expands the block before
# python sees it, so any backtick or $(...) in a comment here is EXECUTED. That
# is not hypothetical — three such comments below once ran `uv run --directory`
# and `uv cache prune`, the latter blocking forever on the long-lived uvx
# processes setup itself had just started. Quoting also removes a quoting bug:
# a path containing a double quote used to produce broken python.
MCP_FILE="$MCP_FILE" \
SERENA_UP="$SERENA_UP" \
python3 << 'PYEOF'
import json, os

mcp_file = os.environ["MCP_FILE"]
serena_up = os.environ["SERENA_UP"] == "1"

with open(mcp_file) as f:
    cfg = json.load(f)

servers = cfg.setdefault("mcpServers", {})
configured = []

# Foundry MCP (the core state engine) is NOT written here anymore. Since 4.7.0
# the plugin manifest declares the server itself (plugin.json mcpServers) so it
# can receive `${user_config.model}` as FOUNDRY_MODEL — user-config substitution
# only works in a plugin-scope declaration. A project-scope entry would not
# shadow the plugin one: BOTH would start (verified on Claude Code 2.1.229),
# doubling the server and exposing a second tool surface whose env lacks
# FOUNDRY_MODEL, silently defeating the model option. So setup now MIGRATES:
# any `foundry` entry a previous setup wrote is removed.
if "foundry" in servers:
    del servers["foundry"]
    print("Migrated: removed the project-scope 'foundry' MCP entry — "
          "foundry >= 4.7.0 bundles its own server in the plugin manifest.")

# Playwright MCP (browser automation for SIGHT)
if "playwright" not in servers:
    servers["playwright"] = {
        "command": "npx",
        "args": ["@playwright/mcp@latest", "--caps", "vision,devtools", "--output-dir", ".playwright-mcp"]
    }
configured.append("playwright")

# Serena MCP — shared HTTP daemon (all sessions connect; none fork).
# Registered unconditionally. The entry is a pointer, not a promise: the
# SessionStart hook reconciles and starts the daemon on every session, so an
# entry written while the daemon is down is repaired before anything reads it.
# Removing it instead would strand that repair — the hook would bring a daemon
# up with nothing configured to reach it.
#
# The hazard that once justified removal was TRACE silently degrading to grep
# behind a dead port. It cannot: agents/tracer.md emits NOT_VERIFIED with cause
# SERENA_UNAVAILABLE per symbol, and grep evidence can never yield WIRED, so a
# dead port produces a visibly degraded report rather than a false clean one.
servers["serena"] = {"type": "http", "url": "http://localhost:9121/mcp"}
configured.append("serena")

with open(mcp_file, "w") as f:
    json.dump(cfg, f, indent=2)
    f.write("\n")

print(f"Configured: {', '.join(configured)} in {mcp_file}")
if not serena_up:
    print("Note: serena registered, but its daemon is not up yet — "
          "the SessionStart hook will start it on the next session.")
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
  echo "  MCP Servers: foundry (bundled with the plugin)"
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
echo "  Config:      $MCP_FILE, $SERENA_DIR/project.yml"
echo ""
echo "The foundry server ships inside the plugin — upgrade it with"
echo "'claude plugin update foundry@guild' (or /foundry:update), then restart."
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
