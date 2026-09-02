#!/usr/bin/env bash
# install-commit-guard.sh — Install Foundry's index-judging pre-commit guard
# into a target repository (GI-002 / FR-012).
#
# Usage: bash scripts/install-commit-guard.sh [--project /path/to/project]
#                                             [--no-clobber]
#
# Installs:
#   - hooks/pre-commit-guard.sh, as the target repo's `pre-commit` hook
#
# This is the install step commands/start.md invokes at the top of a run, so
# that every target repository a run touches gets the correct staged-only
# behaviour rather than each repo carrying its own hand-rolled guard. The path
# of this script and the path of the asset it installs are both contracts:
# start.md hardcodes them.
#
# IDEMPOTENT. Running it twice leaves exactly one working, executable guard and
# says so. Running it again after the shipped asset changes UPDATES the
# installed copy — the guard is versioned by its content, not by a stamp.
#
# NEVER DESTROYS AN UNRELATED HOOK SILENTLY. A pre-commit hook that is not a
# copy of this guard is preserved to a timestamped backup and the replacement is
# reported loudly, mirroring how setup-prereqs.sh:258-262 handles an existing
# Serena config. --no-clobber turns that into a refusal instead.
#
# AND NEVER THROUGH A SYMLINK. A repo whose `pre-commit` is a symlink into the
# work tree (`.git/hooks/pre-commit -> ../../scripts/my-hook.sh`, the
# husky/lefthook way of version-controlling hooks) is the case where "silently"
# is easiest to violate by accident: cp, chmod and every -f/-x test FOLLOW the
# link, so writing "the hook" rewrites a TRACKED file, leaves the link itself
# untouched, and then reports success because the verification followed the
# link too. The hook is therefore written by rename, and a symlink is treated
# as a foreign hook by construction — this script only ever copies, so a link
# is always someone else's deliberate layout, whatever bytes are at its far end.
#
# EXIT CONTRACT:
#   0 — the guard is installed, executable, and current (whether this run
#       installed it, updated it, or found it already correct).
#   1 — could not install, with the cause named on stderr: the target is not a
#       git repository, the hook directory is not writable, the shipped asset
#       is missing, or --no-clobber refused an existing foreign hook (a plain
#       file or a symlink).

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
NO_CLOBBER=0
while [[ $# -gt 0 ]]; do
  case $1 in
    --project)     PROJECT_DIR="$2"; shift 2 ;;
    --no-clobber)  NO_CLOBBER=1; shift ;;
    *) shift ;;
  esac
done

# Find plugin root (where this script lives)
PLUGIN_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

# The shipped asset. It is COPIED into the target repo, never symlinked, and
# never referenced by an absolute path from the installed hook.
#
# This is the versioned-cache caveat recorded at setup-prereqs.sh:45-50, applied
# to a hook instead of an MCP entry. The plugin cache is version-namespaced
# (~/.claude/plugins/cache/guild/foundry/<version>/...) and old version
# directories are retained after an update, so a symlink or a path baked into
# the installed hook would keep resolving into whatever version directory was
# current at install time — silently running the OLD guard for the rest of the
# repo's life, or breaking outright once that directory is pruned. A copy has no
# such link to go stale; re-running this script is what refreshes it.
GUARD_SRC="$PLUGIN_ROOT/hooks/pre-commit-guard.sh"

# The marker line the shipped asset carries on line 2. Presence of this string
# is how an installed `pre-commit` is recognised as ours rather than a repo's
# own hook. Content equality then decides current-versus-stale, so the marker
# never needs a version number of its own to be bumped by hand.
GUARD_MARKER="foundry-commit-guard"

# ── Locate the target repository ─────────────────────────────────────────────
if [[ -n "$PROJECT_DIR" ]]; then
  PROJECT_ROOT="$PROJECT_DIR"
else
  PROJECT_ROOT="$PWD"
fi

if [[ ! -d "$PROJECT_ROOT" ]]; then
  fail "Target directory does not exist: $PROJECT_ROOT"
  exit 1
fi
PROJECT_ROOT="$(cd "$PROJECT_ROOT" && pwd)"

info "Plugin root:  $PLUGIN_ROOT"
info "Project root: $PROJECT_ROOT"

if [[ ! -f "$GUARD_SRC" ]]; then
  fail "Shipped guard asset not found at $GUARD_SRC"
  fail "The plugin install is incomplete — reinstall with 'claude plugin update foundry@guild'."
  exit 1
fi

if ! command -v git &>/dev/null; then
  fail "git not found on PATH. Cannot install a git hook."
  exit 1
fi

if ! git -C "$PROJECT_ROOT" rev-parse --is-inside-work-tree &>/dev/null; then
  fail "Not a git repository (or has no work tree): $PROJECT_ROOT"
  fail "A pre-commit hook has nothing to attach to here."
  exit 1
fi

# ── Resolve the hook directory ───────────────────────────────────────────────
# `rev-parse --git-path hooks` is the ONLY correct way to ask this question. It
# honours core.hooksPath, which a repo may point anywhere — including outside
# the repository. Assuming `.git/hooks` would install a hook that git never
# runs, and the repo would look guarded while being completely unguarded, which
# is worse than not installing at all.
#
# The answer is relative to the directory git ran in, so it is resolved against
# PROJECT_ROOT unless core.hooksPath already made it absolute.
HOOKS_DIR="$(git -C "$PROJECT_ROOT" rev-parse --git-path hooks)"
if [[ "$HOOKS_DIR" != /* ]]; then
  HOOKS_DIR="$PROJECT_ROOT/$HOOKS_DIR"
fi

CUSTOM_HOOKS_PATH="$(git -C "$PROJECT_ROOT" config --get core.hooksPath || true)"
if [[ -n "$CUSTOM_HOOKS_PATH" ]]; then
  info "Repo sets core.hooksPath=$CUSTOM_HOOKS_PATH — honouring it."
fi

# The directory can legitimately be absent when core.hooksPath names a path that
# has not been created yet.
if [[ ! -d "$HOOKS_DIR" ]]; then
  if ! mkdir -p "$HOOKS_DIR" 2>/dev/null; then
    fail "Hook directory does not exist and could not be created: $HOOKS_DIR"
    exit 1
  fi
  info "Created hook directory: $HOOKS_DIR"
fi

if [[ ! -w "$HOOKS_DIR" ]]; then
  fail "Hook directory is not writable: $HOOKS_DIR"
  exit 1
fi

HOOK_DEST="$HOOKS_DIR/pre-commit"
info "Hook target:  $HOOK_DEST"

# ── Writing the hook ─────────────────────────────────────────────────────────
# The guard is NEVER written with `cp "$GUARD_SRC" "$HOOK_DEST"`. When
# HOOK_DEST is a symlink, cp opens and truncates the file the link POINTS AT
# and leaves the link in place: the target repo gets a tracked file silently
# rewritten, the hook is not replaced at all, and `chmod`/`-x`/`grep` on
# HOOK_DEST then all resolve through the same link and agree that everything
# worked. start.md and resume.md run this against every target repo at the top
# of every run, so that is a mutation the next teammate's pathspec commit can
# capture.
#
# Writing a temp file in HOOKS_DIR and renaming it into place fixes both ends:
# rename(2) replaces the LINK ITSELF rather than following it, and the mode is
# set on the real file before it ever becomes the hook. The temp lives in
# HOOKS_DIR so the rename is same-filesystem and therefore atomic — git can
# never observe a half-written hook, not even if this script is killed midway.
INSTALL_TMP=""
cleanup_install_tmp() {
  [[ -n "$INSTALL_TMP" && -e "$INSTALL_TMP" ]] && rm -f "$INSTALL_TMP"
  return 0
}
trap cleanup_install_tmp EXIT

write_guard() {
  INSTALL_TMP="$(mktemp "$HOOKS_DIR/.pre-commit.foundry-install.XXXXXX")"
  cp "$GUARD_SRC" "$INSTALL_TMP"
  chmod 0755 "$INSTALL_TMP"
  mv -f "$INSTALL_TMP" "$HOOK_DEST"
  INSTALL_TMP=""
}

# ── Decide what to do about anything already there ───────────────────────────
ACTION="install"
LINK_TARGET=""
LINK_RESOLVED=""

# `-L` is tested BEFORE `-e`, and the order is load-bearing. `-e` follows the
# link, so a symlink pointing at a path that does not exist reads as "nothing
# is here" and would take the plain `install` branch — where a bare cp would
# CREATE the missing file at wherever the link happens to point, outside the
# hooks directory entirely. `-L` is true for a live and a dangling link alike.
if [[ -L "$HOOK_DEST" ]]; then
  # Foreign by construction: this installer only ever copies, so it cannot have
  # produced a link. Content is not consulted, because a symlink whose target
  # happens to carry the marker is still somebody's deliberate layout decision
  # and still must not be written through.
  LINK_TARGET="$(readlink "$HOOK_DEST")"
  if [[ -e "$HOOK_DEST" ]]; then
    # Resolved by hand rather than with `readlink -f`, which BSD/macOS did not
    # carry until recently. A relative target resolves against the directory
    # holding the LINK, which is HOOKS_DIR; an absolute one ignores that cd.
    LINK_RESOLVED="$(cd "$HOOKS_DIR" && cd "$(dirname "$LINK_TARGET")" && pwd)/$(basename "$LINK_TARGET")"
  fi

  if [[ $NO_CLOBBER -eq 1 ]]; then
    fail "The pre-commit hook is a SYMLINK, so it is not the foundry guard:"
    fail "  $HOOK_DEST -> $LINK_TARGET"
    if [[ -n "$LINK_RESOLVED" ]]; then
      fail "  which resolves to: $LINK_RESOLVED"
    fi
    fail "Refusing to replace it because --no-clobber was given."
    fail "Nothing was written; the link and the file it points at are untouched."
    exit 1
  fi
  ACTION="replace-symlink"

elif [[ -e "$HOOK_DEST" ]]; then
  if grep -q "$GUARD_MARKER" "$HOOK_DEST" 2>/dev/null; then
    # Ours. Content equality decides whether there is anything to do.
    if cmp -s "$GUARD_SRC" "$HOOK_DEST"; then
      ACTION="already-current"
    else
      ACTION="update"
    fi
  else
    # Somebody else's hook. Never overwritten without a word.
    if [[ $NO_CLOBBER -eq 1 ]]; then
      fail "A pre-commit hook already exists and is NOT the foundry guard:"
      fail "  $HOOK_DEST"
      fail "Refusing to replace it because --no-clobber was given."
      fail "Move or merge it by hand, then re-run without --no-clobber."
      exit 1
    fi
    ACTION="replace-foreign"
  fi
fi

# ── Act ──────────────────────────────────────────────────────────────────────
case "$ACTION" in
  already-current)
    # Reached only for a regular file — a link took the branch above — so this
    # chmod cannot land on somebody else's file. Still assert the exec bit: a
    # hook that is present but not executable is silently skipped by git, which
    # looks identical to being installed.
    chmod 0755 "$HOOK_DEST"
    ok "Foundry commit guard already installed and current — nothing to do."
    ;;

  update)
    write_guard
    ok "Foundry commit guard updated to the shipped version."
    ;;

  replace-foreign)
    BACKUP="$HOOK_DEST.foundry-backup.$(date +%Y%m%d-%H%M%S)"
    # Under `set -e` a failed cp aborts here, BEFORE the overwrite below, so the
    # existing hook is never destroyed without a backup landing first.
    cp "$HOOK_DEST" "$BACKUP"
    warn "An existing, non-foundry pre-commit hook was found and REPLACED."
    warn "  Backed up to: $BACKUP"
    warn "  Restore with: cp '$BACKUP' '$HOOK_DEST'"
    warn "  If that hook is still wanted, merge its checks into the backup copy"
    warn "  and re-install, or keep it and run this script with --no-clobber."
    write_guard
    ok "Foundry commit guard installed."
    ;;

  replace-symlink)
    BACKUP="$HOOK_DEST.foundry-backup.$(date +%Y%m%d-%H%M%S)"
    # -P backs up the LINK rather than what it points at, because the link is
    # the only thing being destroyed here: the file at the far end is never
    # written to, so there is nothing of it to preserve.
    cp -P "$HOOK_DEST" "$BACKUP"
    warn "The existing pre-commit hook was a SYMLINK and was REPLACED."
    warn "    $HOOK_DEST"
    warn "      -> $LINK_TARGET"
    if [[ -n "$LINK_RESOLVED" ]]; then
      warn "    resolving to: $LINK_RESOLVED"
      warn "  That file was NOT written to and keeps its exact current bytes."
      warn "  The link itself is what got replaced."
    else
      warn "    which does not exist — the link was already dangling."
    fi
    warn "  Link backed up to: $BACKUP"
    warn "  Restore with: rm -f '$HOOK_DEST' && cp -P '$BACKUP' '$HOOK_DEST'"
    warn "  A symlinked hook is usually a deliberate, version-controlled hook"
    warn "  layout (husky/lefthook-style). If it is still wanted, merge its"
    warn "  checks into the guard, or re-run with --no-clobber to keep it."
    write_guard
    ok "Foundry commit guard installed."
    ;;

  install)
    write_guard
    ok "Foundry commit guard installed."
    ;;
esac

# ── Verify, rather than assume ───────────────────────────────────────────────
# Report what actually happened. An installer that prints success over a failed
# step is worse than one that fails, because its output is the only evidence
# anyone has that the repo is guarded.
if [[ -L "$HOOK_DEST" ]]; then
  fail "Post-install check FAILED: $HOOK_DEST is still a symlink."
  fail "Every check below would follow it and report on some other file, so"
  fail "this repo cannot be called guarded. Nothing further was written."
  exit 1
fi

if [[ ! -x "$HOOK_DEST" ]]; then
  fail "Post-install check FAILED: $HOOK_DEST is not executable."
  fail "git silently skips a non-executable hook — this repo is NOT guarded."
  exit 1
fi

if ! grep -q "$GUARD_MARKER" "$HOOK_DEST" 2>/dev/null; then
  fail "Post-install check FAILED: $HOOK_DEST does not carry the guard marker."
  exit 1
fi

echo ""
printf "${BOLD}${GREEN}Commit guard active in $PROJECT_ROOT${RESET}\n"
echo ""
echo "The guard judges STAGED content only (git diff --cached):"
echo "  - a peer's unstaged work in a shared tree can never fire it"
echo "  - it blocks a commit whose staged content fails a check"
echo ""
echo "Pair it with pathspec-scoped commits so a commit records only its own files:"
echo "  git commit -m \"...\" -- path/one path/two"
echo ""
