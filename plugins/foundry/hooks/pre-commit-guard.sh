#!/usr/bin/env bash
# foundry-commit-guard v1
# pre-commit-guard.sh — Foundry's index-judging pre-commit hook (GI-002).
#
# Installed into a target repository as its `pre-commit` hook by
# scripts/install-commit-guard.sh. It is a TEMPLATE: it hardcodes no repo, no
# language, and no path outside the repository it is installed in.
#
# JUDGES THE INDEX AND ONLY THE INDEX. Every question this script asks is asked
# of `git diff --cached` and of blobs read out of the index by their `:0:<path>`
# name. It MUST NEVER run `git diff HEAD`, `git diff` with no `--cached`,
# `git status` against the working tree, or any other query that can observe
# unstaged content. Foundry teammates share ONE working tree, so a peer's
# unstaged work-in-progress is always present and is never part of this commit.
# A guard that sees it fires on the wrong agent and teaches everyone to reach
# for --no-verify, which is precisely the failure this asset exists to end.
# The anti-pattern to never reintroduce, by name: `git diff --name-only HEAD`.
#
# MUST NEVER RUN `git stash`, IN ANY FORM, --keep-index INCLUDED. Stash-based
# "check only what is staged" hooks silently discard partially-staged hunks —
# a well-documented footgun in the pre-commit ecosystem. Staged CONTENT is read
# straight out of the index instead; the working tree is never mutated.
#
# MUST NEVER PIPE STAGED CONTENT INTO A MATCHER THAT CAN EXIT EARLY. This is a
# correctness rule, not a style preference, and it is written here because the
# obvious spelling of this hook is silently, catastrophically wrong:
#
#     git cat-file blob ":0:$path" | grep -q PATTERN     # ← FAILS OPEN
#
# `grep -q` exits 0 the instant it matches and closes the pipe. On any blob big
# enough to outlast the pipe buffer, `git cat-file` is still writing, takes
# SIGPIPE, and dies 141. `set -o pipefail` then reports the PIPELINE as 141, and
# an `if` condition reads that nonzero status as "no match" — so a MATCH becomes
# a PASS, and the larger the violation the more reliably it is waved through.
# Measured fail-open band: roughly 100 KB up to the size limit; a 4.2 MB file
# with a conflict marker on line 1 committed cleanly through this hook.
#
# Staged content is therefore read ONCE, IN FULL, into a private scratch file
# outside the repository, and every content check runs against that file. The
# one pipeline that remains (`tr | wc`, for the binary probe) is safe for a
# stated reason and not by luck: `wc` is the reader, and `wc` counts to EOF by
# definition — it has no early exit to take. Any future check that pipes blob
# bytes into something that CAN stop early belongs here, against the file.
#
# THIS SCRIPT EXISTS TO BLOCK. That is the inverse of the plugin's only other
# shipped hook (hooks/session-start-serena.sh), which promises that every path
# exits 0, so the contract here is stated just as loudly:
#
#     exit 0  — the staged change passes; git proceeds with the commit.
#     exit 1  — the staged change fails a check; git REFUSES the commit.
#               Every violation is named on stderr before exiting.
#     exit 1  — an internal error (a git command failed unexpectedly). The
#               guard fails CLOSED, and the ERR trap below says where, because
#               a blocked commit with no explanation is the worst outcome
#               available to this script.
#
# WHY REPO ROOT IS NOT RESOLVED FROM BASH_SOURCE, departing from the shipped-hook
# convention deliberately: this file is a COPY living inside the target repo's
# hook directory. Its own location identifies the git dir at best, and under
# `core.hooksPath` it identifies nothing at all — the hook may sit entirely
# outside the repository. git itself is the only authority on where the
# repository is, so the root comes from `git rev-parse --show-toplevel`.
#
# ON GIT_INDEX_FILE — load-bearing, do not "simplify" it away. For a PATHSPEC
# commit (`git commit -m ... -- a.txt b.txt`, which is Foundry's commit
# protocol) git builds a TEMPORARY index containing only the named paths and
# exports GIT_INDEX_FILE pointing at it before running this hook. Every git
# command below inherits that variable, so `git diff --cached` here reports
# exactly the paths this commit will record — not a peer's staged files, and
# not a peer's unstaged edits. Never unset or override GIT_INDEX_FILE.

set -euo pipefail

# Fail closed, but never silently. Without this an unexpected git failure would
# abort under `set -e` with a nonzero status and no output, blocking the commit
# for no visible reason.
trap 'printf "[foundry-guard] INTERNAL ERROR at line %s — commit blocked.\n" "$LINENO" >&2' ERR

# ── Scratch file for staged blobs ────────────────────────────────────────────
# Staged content is copied here, whole, one path at a time — see "MUST NEVER
# PIPE STAGED CONTENT" above. It lives in TMPDIR, never in the repository, so
# nothing this guard does is visible to `git status` or to a peer's editor, and
# the no-stash rule is honoured: the working tree is read-only to this script.
# Bounded by the size check below, which runs BEFORE the read, so an oversize
# blob is rejected without ever being written to disk.
SCRATCH="$(mktemp "${TMPDIR:-/tmp}/foundry-guard.XXXXXX")"
trap 'rm -f "$SCRATCH"' EXIT

# ── Tunables ─────────────────────────────────────────────────────────────────
# Largest staged blob, in bytes, that may be committed. Default 5 MiB. Named to
# match the check, and overridable per-invocation so a repo that legitimately
# commits large assets can raise it without editing the installed hook:
#     FOUNDRY_GUARD_MAX_FILE_SIZE=52428800 git commit -m "..."
MAX_FILE_SIZE="${FOUNDRY_GUARD_MAX_FILE_SIZE:-5242880}"

# ── Reporting ────────────────────────────────────────────────────────────────
# Everything goes to stderr: stdout of a pre-commit hook is not a reporting
# channel a user reliably sees, and mixing the two makes the block reason hard
# to find among git's own output.
say() { printf "[foundry-guard] %s\n" "$*" >&2; }

VIOLATIONS=0
violation() { say "BLOCKED: $*"; VIOLATIONS=$((VIOLATIONS + 1)); }

# ── Conflict-marker patterns, built character by character ───────────────────
# Deliberate, and NOT an over-complication. If this file spelled the 7-character
# markers literally at the start of a line, the guard would flag its own source
# the moment anyone committed it — and it would flag its own test suite too.
# Assembling them at runtime means the literal run never appears in the file.
repeat_char() {
  local ch="$1" count="$2" out="" i
  for ((i = 0; i < count; i++)); do out+="$ch"; done
  printf '%s' "$out"
}

CONFLICT_OURS="$(repeat_char '<' 7)"      # <<<<<<< ours
CONFLICT_BASE="$(repeat_char '\|' 7)"     # ||||||| base (diff3), escaped for ERE
CONFLICT_THEIRS="$(repeat_char '>' 7)"    # >>>>>>> theirs

# A bare 7-equals line is NOT matched, on purpose: it is the setext underline in
# ordinary Markdown and a common ASCII divider, so matching it would block
# perfectly good documentation commits in every repo this template reaches.
# The three markers above always carry a trailing label (or end the line), which
# is what the `( |$)` tail requires.
CONFLICT_RE="^(${CONFLICT_OURS}|${CONFLICT_BASE}|${CONFLICT_THEIRS})( |$)"

# ── Locate the repository ────────────────────────────────────────────────────
REPO_ROOT="$(git rev-parse --show-toplevel)"
cd "$REPO_ROOT"

# What the index is compared against. On an unborn branch there is no HEAD, and
# a bare `git diff --cached` would abort; the empty tree is git's own idiom for
# "everything staged is new", so the very first commit is guarded like any other.
if git rev-parse --verify --quiet HEAD >/dev/null 2>&1; then
  AGAINST="HEAD"
else
  AGAINST="$(git hash-object -t tree /dev/null)"
fi

# ── Collect the staged paths ─────────────────────────────────────────────────
# --cached : index versus $AGAINST. The ONLY query shape this guard may use.
# --diff-filter=d : skip deletions — a deleted path has no staged blob to read.
# -z : NUL-delimited, so paths with spaces, quotes or newlines survive intact.
STAGED=()
while IFS= read -r -d '' path; do
  STAGED+=("$path")
done < <(git diff --cached --name-only --diff-filter=d -z "$AGAINST")

if [ "${#STAGED[@]}" -eq 0 ]; then
  # Nothing staged to judge. An empty commit, or an --allow-empty. Not this
  # guard's business either way.
  exit 0
fi

# ── Check every staged blob ──────────────────────────────────────────────────
for path in "${STAGED[@]}"; do
  # `:0:<path>` names the STAGE-0 index entry — the staged blob — and never the
  # working-tree file of the same name. Stage 0 is spelled explicitly because a
  # bare `:<path>` is ambiguous while a merge is unresolved. An unmerged path
  # has no stage 0, so cat-file fails and `continue` skips it; git refuses to
  # commit an unresolved merge on its own anyway.
  size="$(git cat-file -s ":0:${path}" 2>/dev/null)" || continue

  # ── Check 1: staged blob size ──────────────────────────────────────────────
  # Catches the build artifact, the vendored dependency tree and the stray
  # binary that a broad `git add` swept in — the accidents that are painful to
  # remove from history once they land.
  if [ "$size" -gt "$MAX_FILE_SIZE" ]; then
    violation "${path} — staged blob is ${size} bytes, over the ${MAX_FILE_SIZE}-byte limit."
    say "         Raise it for one commit with: FOUNDRY_GUARD_MAX_FILE_SIZE=<bytes> git commit ..."
    continue
  fi

  # ── Read the staged blob, ONCE, IN FULL ────────────────────────────────────
  # Redirection to a file, not a pipe into a matcher: `git cat-file` runs to
  # completion every time, so there is no SIGPIPE for `pipefail` to turn into a
  # bogus "no match". This single line is what makes the checks below fail
  # CLOSED on a large blob. See the header for the failure it replaces.
  git cat-file blob ":0:${path}" >"$SCRATCH" 2>/dev/null || continue

  # ── Check 2: is this text at all? ──────────────────────────────────────────
  # A NUL byte anywhere is git's own test for binary content, so a blob whose
  # NUL-stripped length differs from its real length is binary. Binary blobs
  # are exempt from the text checks below — an image whose bytes happen to
  # spell a marker at a line boundary is not a merge conflict.
  #
  # `tr | wc` is a pipeline, and it is deliberately allowed: `wc` is the reader
  # and it counts to EOF by definition, so neither command can exit early and
  # neither can raise SIGPIPE on the other.
  nul_free="$(tr -d '\0' <"$SCRATCH" | wc -c | tr -d ' ')"
  if [ "$nul_free" != "$size" ]; then
    continue
  fi

  # ── Check 3: unresolved conflict markers in staged content ─────────────────
  # grep reads the scratch FILE, so `-q` is free to stop at the first match:
  # there is no upstream process left to kill, the exit status is grep's own,
  # and an `if` condition exempts it from `set -e`. Match = violation, always,
  # at any blob size.
  if LC_ALL=C grep -Eq -- "$CONFLICT_RE" "$SCRATCH"; then
    violation "${path} — staged content contains unresolved merge-conflict markers."
  fi
done

if [ "$VIOLATIONS" -gt 0 ]; then
  say ""
  say "${VIOLATIONS} violation(s) in the STAGED content of this commit."
  say "Only what you staged was examined — a peer's unstaged work is never read."
  exit 1
fi

exit 0
