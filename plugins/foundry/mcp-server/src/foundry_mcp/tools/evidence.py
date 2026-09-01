"""Phase 4 / EVID-01 — server-side evidence re-execution.

Re-runs each cited evidence command in a ``git worktree``-isolated checkout at
the casting's commit hash, redacts declared volatile fields, and compares
byte-for-byte against the committed log. Mismatches, non-zero exits, timeouts,
missing commands, or stub-pattern hits all reject with closed-vocabulary
failure tokens.

Plan 04-02: skeleton (constants + header parser + verify_evidence stub).
Plan 04-03: worktree/subprocess/redaction/comparator/stub-library bodies.
Plan 04-04: foundry_accept_casting integration + v2.0 stream-skip routing.

CONTEXT.md decisions locked. RESEARCH.md patterns followed beat-for-beat.

Closed vocabulary: every public failure path emits exactly one member of
``KNOWN_EVIDENCE_FAILURE_TOKENS``. Mirrors Phase 1
``VALID_IMPLICIT_FACT_CATEGORIES``, Phase 2 ``TYPED_SECTION_HEADINGS``, Phase 3
``KNOWN_SPEC_FORMAT_VERSIONS``.
"""

from __future__ import annotations

import difflib
import json
import os
import re
import shlex
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from foundry_mcp.tools.foundry_handoff import _hash_str
from foundry_mcp.tools.foundry_state import get_run_dir
from foundry_mcp.tools.worktree_helpers import (
    _PRUNE_DONE_FOR,
    _WORKTREE_LOCK,
    _prune_orphaned_worktrees,
    _run_command_with_timeout,
    _setup_worktree,
    _teardown_worktree,
)

# ---------------------------------------------------------------------------
# Closed-vocabulary failure-token allowlist (Phase 1/2/3 discipline mirror).
#
# Any new token = code-edit forced; ``test_failure_tokens_are_in_allowlist``
# enforces tuple-membership at CI time. Order intentional and documented in
# CONTEXT.md.
# ---------------------------------------------------------------------------
KNOWN_EVIDENCE_FAILURE_TOKENS: tuple[str, ...] = (
    "EVIDENCE_COMMAND_MISSING",          # Phase 4 / EVID-01
    "EVIDENCE_TIMEOUT",                  # Phase 4 / EVID-01
    "EVIDENCE_EXIT_NONZERO",             # Phase 4 / EVID-01
    "EVIDENCE_OUTPUT_MISMATCH",          # Phase 4 / EVID-01
    "EVIDENCE_STUB_DETECTED",            # Phase 4 / EVID-01
    "EVIDENCE_VOLATILE_MALFORMED",       # Phase 4 / EVID-01
    "EVIDENCE_COMMIT_MISSING",           # Phase 4 / EVID-01
    "EVIDENCE_NETWORK_VIOLATION",        # Phase 4 / EVID-01 reserved; never fires; activated by future per-evidence network-deny opt-in
    "EVIDENCE_REQUIREMENT_UNBOUND",      # Phase 5 / EVID-02 addition
    "EVIDENCE_FOR_MALFORMED",            # Phase 5 / EVID-02 addition
)

# Sanity-bounded timeout discipline. Default fires when an evidence file omits
# ``# evidence-timeout:``; ceiling caps deliberately-long sleeps that would
# stall the gate. CONTEXT.md Claude's Discretion #7 → 1800s recommended.
EVIDENCE_TIMEOUT_DEFAULT_SECONDS: int = 120
EVIDENCE_TIMEOUT_CEILING_SECONDS: int = 1800

# Stub-pattern threshold (Plan 04-03 territory; constant declared here so
# Plan 04-02 stubs can reference it deterministically).
EVIDENCE_STUB_MIN_BYTES: int = 128

# v2.0 backwards-compat gate — Plan 04-04 reads spec_format_version from
# spec.md frontmatter and routes <(2,1) specs through manifest.stream_skips.
MIN_SPEC_FORMAT_VERSION_FOR_EVID_01: tuple[int, int] = (2, 1)

# Volatile-redaction placeholder. Public so test code + Plan 04-03 comparator
# share the same literal. NOT one of the failure tokens — substituted into
# captured/log text during the redaction pipeline.
VOLATILE_PLACEHOLDER: str = "<VOLATILE>"

# Phase 5 grep contract: Phase 4 owns these directives only. Phase 5's
# ``# evidence-for:`` joins this set without parser edits — the parser
# silently ignores unknown directives so Phase 5 can introduce its directive
# at activation time (mirrors Phase 1 ``[IMPLICIT_FACT:CATEGORY]`` precedent —
# introduced by the same phase that owns it).
_KNOWN_HEADER_DIRECTIVES: frozenset[str] = frozenset(
    {"cmd", "volatile", "timeout", "for"}  # Phase 5 / EVID-02 — 'for' added
)


# ---------------------------------------------------------------------------
# Header parser (Plan 04-02 territory).
#
# Header block extends from file start through the last consecutive comment-
# or-blank line; first non-comment, non-blank line ends the block. Parser
# accepts ``# evidence-cmd:`` (single, mandatory at caller-translation level —
# parser returns None, caller emits EVIDENCE_COMMAND_MISSING),
# ``# evidence-volatile:`` (zero or more, list-valued in DECLARED ORDER per
# Pitfall 5 from RESEARCH.md), ``# evidence-timeout:`` (optional integer in
# (0, EVIDENCE_TIMEOUT_CEILING_SECONDS]).
#
# Unknown directives (e.g., Phase 5's ``# evidence-for:``) are silently
# ignored so Phase 5's introduction lands without parser edits — Phase 5
# grep contract from CONTEXT.md.
# ---------------------------------------------------------------------------
_EVIDENCE_HEADER_LINE_RE = re.compile(
    r"^\s*#\s*evidence-([a-z][a-z0-9-]*)\s*:\s*(.+?)\s*$",
    re.MULTILINE,
)
_EVIDENCE_HEADER_BLOCK_RE = re.compile(r"\A(?:#[^\n]*\n|[ \t]*\n)+")

# Phase 5 / EVID-02 — single-source-of-truth requirement-ID regex re-used
# from plugins/foundry/mcp-server/src/foundry_mcp/tools/foundry_handoff.py:324
# (`req_id_pattern`). Module-level constant so artifact-side parsing
# (this module) and prompt-side parsing (foundry_handoff.py) agree
# byte-for-byte. Closed vocabulary: US, FR, NFR, AC, VC, IR, TR + numeric
# ID with optional decimal (e.g., FR-2.1).
_REQUIREMENT_ID_RE: re.Pattern[str] = re.compile(
    r"\b(?:US|FR|NFR|AC|VC|IR|TR)-\d+(?:\.\d+)?\b"
)


def _parse_evidence_header(text: str) -> dict[str, Any]:
    """Parse evidence file header (leading comment block).

    Args:
        text: full evidence-file contents (header block + body).

    Returns:
        ``{'cmd': str | None, 'volatile': list[str], 'timeout': int | None}``.

    Raises:
        ValueError prefixed with EVIDENCE_VOLATILE_MALFORMED when:
          - ``# evidence-timeout:`` value is not an integer
          - ``# evidence-timeout:`` integer is <= 0 or
            > ``EVIDENCE_TIMEOUT_CEILING_SECONDS``

    Caller translates ``cmd is None`` → ``EVIDENCE_COMMAND_MISSING``. Volatile
    patterns are returned as raw strings (NOT pre-compiled);
    ``_apply_volatile_redaction`` (Plan 04-03) compiles them at application
    time and raises ``EVIDENCE_VOLATILE_MALFORMED`` on ``re.error``. Plan
    04-02 SUMMARY documents this application-time-validation choice.

    Multiple ``# evidence-cmd:`` lines: first wins; subsequent ignored. Plan
    04-04 may upgrade to a hard-fail if abuse surfaces.

    Phase 5 grep contract: unknown ``# evidence-*:`` directives are silently
    ignored at this parser level. Phase 5 owns the parsing of its own
    directives (e.g. ``# evidence-for:``) at the foundry_accept_casting layer.

    Timeout out-of-range collapses to EVIDENCE_VOLATILE_MALFORMED rather than
    introducing a 9th token: closed-vocabulary discipline preserves the
    8-token allowlist locked in CONTEXT.md (Plan 04-02 SUMMARY decision).
    """
    out: dict[str, Any] = {
        "cmd": None,
        "volatile": [],
        "timeout": None,
        "evidence_for": [],  # Phase 5 / EVID-02 — declared-order list of req IDs
    }
    block_match = _EVIDENCE_HEADER_BLOCK_RE.match(text)
    block = block_match.group(0) if block_match else ""
    for m in _EVIDENCE_HEADER_LINE_RE.finditer(block):
        directive, raw_val = m.group(1), m.group(2).strip()
        if directive not in _KNOWN_HEADER_DIRECTIVES:
            continue  # Phase 5 grep contract — ignore unknown
        if directive == "cmd":
            if out["cmd"] is not None:
                continue  # first wins; subsequent silently ignored
            out["cmd"] = raw_val
        elif directive == "volatile":
            out["volatile"].append(raw_val)
        elif directive == "timeout":
            try:
                parsed = int(raw_val)
            except ValueError as exc:
                raise ValueError(
                    f"EVIDENCE_VOLATILE_MALFORMED: timeout {raw_val!r} "
                    f"is not an integer"
                ) from exc
            if parsed <= 0 or parsed > EVIDENCE_TIMEOUT_CEILING_SECONDS:
                raise ValueError(
                    f"EVIDENCE_VOLATILE_MALFORMED: timeout {parsed} "
                    f"out of range (0, {EVIDENCE_TIMEOUT_CEILING_SECONDS}]"
                )
            out["timeout"] = parsed
        elif directive == "for":
            # Phase 5 / EVID-02: parse comma-separated requirement-ID list.
            # ``re.findall`` extracts every valid ID, tolerating whitespace,
            # commas, semicolons, and embedded comments. Bogus tokens that
            # don't match the regex are silently dropped — caller's set-diff
            # against ``casting_req_ids`` surfaces the unbound requirements
            # (Plan 05-03 territory at foundry_accept_casting).
            #
            # When the value is non-empty but contains zero valid IDs, raise
            # EVIDENCE_FOR_MALFORMED — mirrors Phase 4's
            # EVIDENCE_VOLATILE_MALFORMED raise-path for invalid timeout values.
            #
            # Multiple ``# evidence-for:`` lines accumulate (mirrors
            # ``# evidence-volatile:`` multi-line discipline). De-dup is
            # caller responsibility; declared order preserved.
            ids = _REQUIREMENT_ID_RE.findall(raw_val)
            if raw_val and not ids:
                raise ValueError(
                    f"EVIDENCE_FOR_MALFORMED: no requirement IDs found in {raw_val!r}"
                )
            out["evidence_for"].extend(ids)
    return out


# ---------------------------------------------------------------------------
# Volatile-redaction (Plan 04-03 — body landed).
#
# Pitfall 5 from RESEARCH.md: ordering matters. Each ``re.sub`` is applied to
# the OUTPUT of the previous substitution, so pattern N's substituted text
# can match (or de-match) pattern N+1. Tests lock the non-commutative
# contract via ``test_volatile_order_is_respected``.
# ---------------------------------------------------------------------------
def _apply_volatile_redaction(text: str, volatile_patterns: list[str]) -> str:
    """Apply each volatile pattern as ``re.sub`` in DECLARED ORDER.

    Args:
        text: source string to redact.
        volatile_patterns: ordered list of regex pattern strings. Each is
            applied via ``re.sub(pattern, VOLATILE_PLACEHOLDER, text)`` against
            the running output (NOT the original ``text``).

    Returns:
        The fully-redacted string. Empty list ⇒ ``text`` returned unchanged.

    Raises:
        ValueError prefixed with ``EVIDENCE_VOLATILE_MALFORMED`` when any
        pattern fails to compile (``re.error``). Caller translates to a
        provenance record with ``failure_token=EVIDENCE_VOLATILE_MALFORMED``.

    Pitfall 5 mitigation: iterative ``re.sub`` with declared order honored.
    Reverse-ordered patterns yield different output (test-locked).

    Placeholder-ladder discipline (test-locked in
    ``test_volatile_order_is_respected``): the substitution token used for
    each pattern is selected by inspecting the pattern itself —

      - If the pattern string CONTAINS ``<VOLATILE>`` (a "compound" rule
        that depends on a prior level-0 redaction), matches are substituted
        with ``<TIMING>`` (the next-level placeholder).
      - Otherwise (a "level-0" rule on raw text), matches are substituted
        with ``<VOLATILE>``.

    This lets authors stage redactions in two passes: first collapse raw
    timing fields into ``<VOLATILE>``, then collapse the resulting
    ``"<phrase> <VOLATILE>"`` shape into a higher-level
    ``<TIMING>`` token. Without the ladder, a compound pattern would
    re-substitute with the same ``<VOLATILE>`` and lose the level
    distinction. CONTEXT.md describes the level-0 case (``<VOLATILE>``);
    the ladder generalizes that to multi-level chains.
    """
    redacted = text
    for pat in volatile_patterns:
        # Placeholder ladder: pattern referencing <VOLATILE> is level-1+,
        # substitutes with <TIMING>; otherwise level-0 → <VOLATILE>.
        replacement = (
            "<TIMING>" if VOLATILE_PLACEHOLDER in pat else VOLATILE_PLACEHOLDER
        )
        try:
            substituted = re.sub(pat, replacement, redacted)
        except re.error as exc:
            raise ValueError(
                f"EVIDENCE_VOLATILE_MALFORMED: invalid regex {pat!r}: {exc}"
            ) from exc
        if _pattern_redacts_everything(pat, replacement):
            raise ValueError(
                f"EVIDENCE_VOLATILE_MALFORMED: pattern {pat!r} redacts ALL "
                f"content, not a varying field. Applied to both the committed "
                f"log and the re-execution capture it collapses them to the "
                f"same string, so any command's output would byte-match any "
                f"log and the evidence gate would prove nothing. Narrow it to "
                f"the field that actually varies between runs."
            )
        redacted = substituted
    return redacted


#: Fixed samples used to ask a volatile pattern how much it matches. They share
#: nothing with any real evidence output, so a pattern that erases one is a
#: pattern that erases arbitrary content — precisely the property that makes a
#: bypass work. Each is multi-line so a line-oriented pattern is exercised.
#:
#: The ``<VOLATILE>`` token appears in a DIFFERENT POSITION in each, because a
#: level-1 pattern anchored to the placeholder erases only in the direction it
#: reaches: ``[\s\S]*<VOLATILE>`` empties a log whose placeholder ends it,
#: ``<VOLATILE>[\s\S]*`` one whose placeholder begins it. A single canary with
#: the token in the middle leaves surviving text on whichever side the pattern
#: does not reach, and passes both — which is how ``[\s\S]*<VOLATILE>`` slipped
#: through the first cut of this guard. Three positions leave no direction
#: unprobed.
_REDACTION_CANARIES: tuple[str, ...] = (
    "foundry-evidence-canary alpha 4f2a\n"
    "completed in <VOLATILE> after bravo 91b7\n"
    "charlie delta echo\n",
    # placeholder last
    "foundry-evidence-canary alpha 4f2a\ncharlie delta echo <VOLATILE>\n",
    # placeholder first
    "<VOLATILE> foundry-evidence-canary alpha 4f2a\ncharlie delta echo\n",
)  # 3 probes


def _pattern_redacts_everything(pattern: str, replacement: str) -> bool:
    """True when ``pattern`` erases arbitrary content, not a varying field.

    THE HOLE THIS CLOSES (D-109). ``_compare_byte_match`` applies the SAME
    declared patterns to both the committed log and the re-execution capture,
    so a pattern matching everything collapses both sides to one identical
    string and the byte-match returns matched=True for ANY output. Driven end
    to end, a fabricated log plus a real-but-unrelated command plus one
    ``# evidence-volatile: [\\s\\S]*`` line produced ``ok=True`` and
    ``evidence_verdict='accepted'`` while the two SHA256s plainly differed —
    the whole EVID-01 mechanism defeated by a single header line. It also
    neutralised the stub library, which reads the RAW log and so never saw the
    collapse. The module header calls this a "closed escape-hatch: ONLY
    declared volatility tolerated"; total redaction is where the hatch stops
    being closed, because what is declared is no longer volatility — it is the
    evidence.

    THE TEST IS ON THE PATTERN, NOT ON THE LOG, and that distinction is the
    whole design. Asking "did this substitution empty THIS text?" conflates two
    different things: ``[\\s\\S]*`` empties every text and is a bypass, while
    the legitimate ladder pattern ``completed in <VOLATILE>`` empties only a
    log that happens to consist of nothing but that phrase. Probing a fixed
    canary instead asks the question that actually separates them — does this
    pattern consume content it has never seen? — so an over-broad pattern is
    refused whatever the log contains, and a narrow one is accepted however
    short the log is.

    Both ladder levels are covered because the canaries carry a ``<VOLATILE>``
    token: a level-1 pattern such as ``[\\s\\S]*<VOLATILE>`` erases the canary
    whose placeholder sits last, where a canary of plain text would have let it
    through. Erasing ANY probe is disqualifying — a pattern that empties one
    shape of log and not another is still a pattern that empties a log.
    """
    for canary in _REDACTION_CANARIES:
        try:
            probed = re.sub(pattern, replacement, canary)
        except re.error:
            # Compilation is the caller's error to report, with its own message.
            return False
        residue = probed.replace(VOLATILE_PLACEHOLDER, "").replace("<TIMING>", "")
        if not residue.strip():
            return True
    return False


# ---------------------------------------------------------------------------
# Worktree + subprocess primitives are factored to ``worktree_helpers.py``
# (Phase 7 / Plan 07-03 — RESEARCH.md Open Question 1 recommendation).
#
# ``_run_command_with_timeout``, ``_setup_worktree``, ``_teardown_worktree``,
# ``_prune_orphaned_worktrees``, ``_WORKTREE_LOCK``, ``_PRUNE_DONE_FOR``
# are imported at module-top so identity is preserved across the import
# boundary — Phase 4/5 callers and Phase 7 callers serialize on the same
# lock and share the same once-per-session prune guard.
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Byte-match comparator with declared-volatile redaction + capped diff
# (Plan 04-03).
#
# Closed escape-hatch: ONLY declared volatility tolerated. The redaction
# runs on BOTH committed log and re-execution capture in the same declared
# order, then byte-compares. Any divergence is a failure.
#
# Diff cap: 50 lines via ``_DIFF_CAP_LINES``. Larger diffs append a
# truncation marker so the failure_detail stays scannable.
# ---------------------------------------------------------------------------
_DIFF_CAP_LINES: int = 50


def _compare_byte_match(
    committed: str,
    captured: str,
    volatile_patterns: list[str],
) -> tuple[bool, str | None, str, str]:
    """Apply volatile redaction to both inputs in declared order, byte-compare.

    Args:
        committed: committed log text (the evidence-file body).
        captured: re-execution stdout+stderr capture.
        volatile_patterns: ordered list of redaction regex patterns.

    Returns:
        ``(matched, capped_diff_or_None, redacted_committed, redacted_captured)``
        — the redacted strings are returned so the caller can SHA256-hash
        them for the ``redacted_log_sha256`` / ``redacted_captured_sha256``
        provenance fields without re-invoking the redaction.

    Raises:
        ValueError prefixed ``EVIDENCE_VOLATILE_MALFORMED`` (propagated from
        ``_apply_volatile_redaction``) when a pattern fails to compile.

    On mismatch the diff is unified-format via ``difflib.unified_diff``,
    capped at ``_DIFF_CAP_LINES`` lines; if truncated, a "... (N more
    diff lines truncated)" sentinel is appended.
    """
    rc = _apply_volatile_redaction(committed, volatile_patterns)
    rcc = _apply_volatile_redaction(captured, volatile_patterns)
    if rc == rcc:
        return True, None, rc, rcc
    diff_lines = list(
        difflib.unified_diff(
            rc.splitlines(keepends=True),
            rcc.splitlines(keepends=True),
            fromfile="committed",
            tofile="captured",
            lineterm="",
            n=3,
        )
    )
    capped = diff_lines[:_DIFF_CAP_LINES]
    if len(diff_lines) > _DIFF_CAP_LINES:
        capped.append(
            f"... ({len(diff_lines) - _DIFF_CAP_LINES} more diff lines truncated)"
        )
    return False, "".join(capped), rc, rcc


# ---------------------------------------------------------------------------
# Stub-pattern library (Plan 04-03 — CONTEXT.md "Stub-pattern library").
#
# Four patterns, first-hit-wins ordering inside ``_check_stub_patterns``:
#
#   1. TOO_SMALL — log encoded length < EVIDENCE_STUB_MIN_BYTES (128)
#   2. VACUOUS_CMD — the declared command runs nothing but no-ops and pure
#      output emitters, so its output proves nothing about the tree
#   3. BARE_PASS — log body is a single ``PASS`` (or PASS|OK|✓|SUCCESS for
#      _check_stub_patterns; ``_is_stub_pattern_bare_pass`` is PASS-only
#      per its test contract)
#   4. TIMESTAMP_CLUSTER — log body is predominantly timestamp-only lines
#      (fabricated bulk pattern)
#
# Sub-tokens emitted via ``_check_stub_patterns`` failure_detail; the public
# closed-vocabulary token remains ``EVIDENCE_STUB_DETECTED`` (8-token
# allowlist preserved).
#
# WHY RULE 2 JUDGES THE COMMAND AND NOT THE LOG (D-062)
# -----------------------------------------------------
# It used to judge the log: it required the command's FIRST TOKEN to appear as
# a substring of the first three body lines. Two facts sank that rule.
#
# First, it is measurably wrong about real evidence. The dominant evidence
# command in this repo is `cd plugins/foundry/mcp-server && uv run --with
# pytest pytest ...`, whose first token is `cd` — which no pytest banner ever
# echoes — and `pytest -q`'s output (`.... [100%]` / `40 passed in 0.26s`)
# quotes nothing from its command line at all. Run over the corpus this effort
# committed, the rule called 19 of 25 genuine logs stubs. Widening it to every
# token of the whole command does not save it: the `-q` bodies contain no token
# of their command anywhere, not just in the first three lines.
#
# Second, and decisive: this library runs at step 6, AFTER `_compare_byte_match`
# has already proven the committed bytes ARE the output of re-executing this
# command in a clean worktree at the casting commit. Given that proof, "the
# output does not quote the command" carries no information about fabrication.
# The failure it was groping for — a body of boilerplate unrelated to the
# command — is caught upstream and far more strongly, as
# EVIDENCE_OUTPUT_MISMATCH.
#
# What byte-match CANNOT catch is the vector RESEARCH.md calls Pitfall 4: a
# self-consistent fabricated log replayed by a command that does no work
# (`echo`, `printf`, `true`). Such a command reproduces its own fabricated body
# on every re-execution, so it byte-matches forever. That is what rule 2 now
# judges, from the command text alone — strictly stronger than the rule it
# replaces, which a fabricator defeated by adding one echoed line.
# ---------------------------------------------------------------------------
EVIDENCE_STUB_TOO_SMALL = "EVIDENCE_STUB_TOO_SMALL"
EVIDENCE_STUB_VACUOUS_CMD = "EVIDENCE_STUB_VACUOUS_CMD"
EVIDENCE_STUB_BARE_PASS = "EVIDENCE_STUB_BARE_PASS"
EVIDENCE_STUB_TIMESTAMP_CLUSTER = "EVIDENCE_STUB_TIMESTAMP_CLUSTER"

# Words that separate one command from the next. The token AFTER any of these
# is in command position, which is the only position rule 2 judges.
_STUB_CMD_SEPARATORS = frozenset({
    "&&", "||", "|", "|&", ";", ";;", "&", "(", ")", "{", "}", "!",
    "then", "else", "elif", "do", "done", "fi", "in",
})  # 18 separators

# Words in command position that delegate the real work to a payload. When a
# `-c` argument follows, rule 2 recurses into it rather than stopping here.
_STUB_SHELL_WRAPPERS = frozenset({"sh", "bash", "zsh", "dash"})  # 4 wrappers

# Words in command position that neither do work nor delegate it: shell
# bookkeeping. Skipped — they neither prove work nor prove its absence.
_STUB_SHELL_BOOKKEEPING = frozenset({
    "cd", "test", "[", "[[", "set", "export", "local", "unset", "shift",
    "read", "trap", "wait", "pushd", "popd", "eval",
})  # 15 words

# Words in command position that emit output without reading or running
# anything under test. A command built ONLY from these can reproduce any body
# it likes on every re-execution, which is exactly how a fabricated log
# byte-matches itself. `cat` is deliberately ABSENT: reading a file committed
# at the casting commit is real evidence about the tree, and the test harness's
# replay path (`cat replay.txt`) depends on it staying legitimate.
_STUB_VACUOUS_PROGRAMS = frozenset({
    ":", "true", "false", "echo", "printf", "yes", "exit", "return", "sleep",
})  # 9 programs

# Depth ceiling for `sh -c '<payload>'` recursion. Two levels covers every
# shape in the corpus (`sh -c` wrapping a script that itself calls `sh -c`);
# beyond it the command is treated as doing work (fail open, never reject).
_STUB_CMD_RECURSION_LIMIT = 2

# Bare-pass regex used by _check_stub_patterns — broader than the
# _is_stub_pattern_bare_pass helper (which is PASS-only per its test).
# Multi-line tolerant; fires when the entire body is one acknowledgement.
_STUB_BARE_ACK_RE = re.compile(r"^\s*(PASS|OK|✓|SUCCESS)\s*$")

# Timestamp-only line: HH:MM:SS, ISO 8601 (2026-05-05T10:00:00Z), or syslog
# (Apr  5 10:00:00). Matches a line whose entire content (after strip) is
# a single timestamp token.
_STUB_TIMESTAMP_LINE_RE = re.compile(
    r"^\s*("
    r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z?"  # ISO 8601
    r"|\d{2}:\d{2}:\d{2}(?:[.,]\d{1,9})?"                # HH:MM:SS[.ffffff]
    r"|[A-Z][a-z]{2}\s+\d{1,2}\s+\d{2}:\d{2}:\d{2}"      # syslog: Apr  5 10:00:00
    r")\s*$"
)


def _strip_leading_header_block(text: str) -> str:
    """Drop the leading ``# evidence-*:`` comment block, keeping the body.

    Unlike ``_strip_header_and_blank_lines`` this preserves the body verbatim
    — interior blank lines and all — because the byte comparator's whole job
    is an exact match on what the command emitted.
    """
    return _EVIDENCE_HEADER_BLOCK_RE.sub("", text, count=1)


def _strip_header_and_blank_lines(text: str) -> list[str]:
    """Return body lines (header `# evidence-*:` comments and blanks dropped).

    Helper for stub-pattern checks that operate on "real content lines"
    rather than the literal evidence-file bytes (which include the header
    comment block).
    """
    return [
        ln for ln in text.splitlines()
        if ln.strip() and not ln.lstrip().startswith("#")
    ]


def _is_stub_pattern_too_small(
    text: str,
    threshold: int = EVIDENCE_STUB_MIN_BYTES,
) -> bool:
    """Return True if ``text`` encoded byte-length is below ``threshold``.

    UTF-8 encoded length is the canonical measure (matches what gets
    committed to disk + transmitted in MCP responses). Tests pass
    ``threshold=128`` explicitly; default mirrors ``EVIDENCE_STUB_MIN_BYTES``.
    """
    return len(text.encode("utf-8")) < threshold


def _shell_tokens(cmd: str) -> list[str] | None:
    """Split ``cmd`` the way a shell would, or None when it cannot be parsed.

    ``punctuation_chars=True`` is what makes `;`, `&&`, `|` and the grouping
    characters come back as their OWN tokens — ``shlex.split`` folds them into
    the preceding word (``"echo a; grep b"`` -> ``["echo", "a;", ...]``), which
    would hide every command position after the first. Quoting is honoured, so
    an ``sh -c '<script>'`` payload arrives as one token and can be re-lexed.

    Returns None on unbalanced quotes. Callers treat that as "cannot judge" and
    fail OPEN — an unparseable command is never rejected on this rule's word.
    """
    try:
        lexer = shlex.shlex(cmd, posix=True, punctuation_chars=True)
        lexer.whitespace_split = True
        return list(lexer)
    except ValueError:
        return None


def _command_position_programs(cmd: str, _depth: int = 0) -> list[str] | None:
    """Return the program invoked at each command position in ``cmd``.

    Command position = the first token, or the token after a separator
    (``&&``, ``;``, ``|``, a subshell paren, …). Environment assignments
    (``PYTHONPATH=src pytest``) are stepped over so the program behind them is
    what gets reported. A shell wrapper with a ``-c`` payload
    (``sh -c 'python3 …; grep …'``) is recursed into, because the wrapper name
    says nothing about whether the payload works.

    Returns None when the command cannot be lexed (see ``_shell_tokens``).
    """
    tokens = _shell_tokens(cmd)
    if tokens is None:
        return None

    programs: list[str] = []
    at_command_position = True
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token in _STUB_CMD_SEPARATORS:
            at_command_position = True
            index += 1
            continue
        if not at_command_position:
            index += 1
            continue
        # `VAR=value cmd …` — the assignment is a prefix, not the program.
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", token, re.DOTALL):
            index += 1
            continue
        at_command_position = False
        name = token.rsplit("/", 1)[-1]
        if name in _STUB_SHELL_WRAPPERS and _depth < _STUB_CMD_RECURSION_LIMIT:
            # Recurse into `-c <payload>` when there is one; a wrapper invoked
            # any other way (`sh script.sh`) runs a script, which is work.
            payload_index = next(
                (
                    i + 1
                    for i in range(index + 1, len(tokens) - 1)
                    if tokens[i] == "-c"
                ),
                None,
            )
            if payload_index is not None:
                inner = _command_position_programs(
                    tokens[payload_index], _depth=_depth + 1
                )
                if inner is None:
                    return None
                programs.extend(inner)
                index = payload_index + 1
                continue
        if name in _STUB_SHELL_BOOKKEEPING:
            index += 1
            continue
        programs.append(name)
        index += 1
    return programs


def _is_stub_pattern_vacuous_cmd(cmd: str) -> bool:
    """Return True when ``cmd`` runs nothing but no-ops and output emitters.

    This is the rule that survives byte-match. By the time the stub library
    runs, the committed log has been proven to be this command's own output at
    the casting commit — so the only fabrication left is a command that emits a
    canned body without touching the tree (``echo``/``printf``/``true``), which
    then byte-matches itself on every re-execution forever.

    Judged over the WHOLE command, at every command position, recursing into
    ``sh -c`` payloads: one real program anywhere (``pytest``, ``python3``,
    ``grep``, ``git``, ``cat``) means the command does work, whatever its first
    token is. See the library header for why the previous first-token-in-output
    reading had to go (D-062).

    Fails OPEN in all three ambiguous cases — an empty command, an unlexable
    one, and one with no classifiable command position are never called stubs.
    """
    if not cmd.strip():
        return False
    programs = _command_position_programs(cmd)
    if not programs:
        return False
    return all(name in _STUB_VACUOUS_PROGRAMS for name in programs)


def _is_stub_pattern_bare_pass(text: str) -> bool:
    """Return True iff ``text`` (after strip) is exactly ``PASS`` (PASS-only).

    Test-locked semantics:
      - ``PASS\\n`` → True
      - ``PASS`` → True
      - ``OK\\n`` → False (this helper is PASS-only; the broader bare-ack
        check lives inside ``_check_stub_patterns`` via ``_STUB_BARE_ACK_RE``)
      - ``PASS\\nsomething else here\\n`` → False
    """
    return text.strip() == "PASS"


def _is_stub_pattern_timestamp_cluster(text: str) -> bool:
    """Return True if the body is predominantly timestamp-only lines.

    Heuristic: among non-blank, non-header lines, ≥80% match
    ``_STUB_TIMESTAMP_LINE_RE`` AND there are at least 3 such lines. This
    catches "fabricated bulk" logs that pad out to bypass the TOO_SMALL
    threshold by repeating a timestamp shape.

    CONTEXT.md describes a stricter "<1ms cluster" rule for
    ``_check_stub_patterns``; this helper uses the broader "fabricated-bulk
    timestamp lines" heuristic that the test fixture exercises (5 ISO
    timestamps spaced 1s apart). Real pytest output (mixed test-name +
    elapsed-time lines) does not trip the rule.
    """
    body = _strip_header_and_blank_lines(text)
    if len(body) < 3:
        return False
    timestamp_lines = sum(
        1 for ln in body if _STUB_TIMESTAMP_LINE_RE.match(ln)
    )
    return timestamp_lines >= 3 and timestamp_lines >= int(0.8 * len(body))


def _check_stub_patterns(log_text: str, evidence_cmd: str) -> str | None:
    """Run all four stub-pattern rules first-hit-wins.

    Returns:
        Sub-token name (e.g. ``EVIDENCE_STUB_TOO_SMALL``) on first hit,
        or ``None`` when the log clears all four rules.

    The caller (``verify_evidence`` / ``_verify_one_evidence_file``) wraps
    a hit into the ``EVIDENCE_STUB_DETECTED`` public failure token with the
    sub-token embedded in ``failure_detail`` (preserves the 8-token
    closed vocabulary).

    Order: TOO_SMALL → VACUOUS_CMD → BARE_PASS → TIMESTAMP_CLUSTER.
    First hit wins (CONTEXT.md "Stub-pattern library — first hit wins").

    Stub patterns fire ON TOP of byte-match (CONTEXT.md): even when
    ``_compare_byte_match`` succeeds, a stub-pattern hit rejects the log.
    Rules 1, 3 and 4 judge the LOG for triviality; rule 2 judges the COMMAND
    for vacuity, which is the only fabrication a byte-match cannot see.
    """
    # Pattern 1: TOO_SMALL
    if _is_stub_pattern_too_small(log_text, EVIDENCE_STUB_MIN_BYTES):
        return EVIDENCE_STUB_TOO_SMALL

    # Pattern 2: VACUOUS_CMD (skip when there is no cmd to judge)
    if evidence_cmd and _is_stub_pattern_vacuous_cmd(evidence_cmd):
        return EVIDENCE_STUB_VACUOUS_CMD

    # Pattern 3: BARE_PASS / OK / ✓ / SUCCESS — broader than the
    # _is_stub_pattern_bare_pass helper, which is PASS-only per its test.
    body = _strip_header_and_blank_lines(log_text)
    body_text = "\n".join(body).strip()
    if body_text and _STUB_BARE_ACK_RE.fullmatch(body_text):
        return EVIDENCE_STUB_BARE_PASS

    # Pattern 4: TIMESTAMP_CLUSTER (predominantly timestamp-only lines)
    if _is_stub_pattern_timestamp_cluster(log_text):
        return EVIDENCE_STUB_TIMESTAMP_CLUSTER

    return None


# ---------------------------------------------------------------------------
# Provenance record builder (Plan 04-03 — 13-field schema per CONTEXT.md).
#
# Closed-schema discipline: every provenance record has exactly these 13
# fields. ``test_provenance_record_has_required_fields`` enforces the
# schema via ``frozenset.issubset`` (Plan 04-04 territory but works in
# Plan 04-03 since the record shape lives in ``_make_provenance_record``).
# ---------------------------------------------------------------------------
def _make_provenance_record(
    *,
    evidence_path: Path,
    evidence_cmd: str | None,
    casting_commit: str,
    log_text: str,
    captured_text: str,
    redacted_log: str,
    redacted_captured: str,
    exit_code: int | None,
    elapsed_seconds: float,
    verdict: str,
    failure_token: str | None,
    failure_detail: str | None,
    evidence_for: list[str] | None = None,  # Phase 5 / EVID-02 — defaults []
) -> dict[str, Any]:
    """Build a single 13-field provenance record (CONTEXT.md schema).

    Fields:
        evidence_path, evidence_cmd, casting_commit, log_sha256,
        captured_sha256, redacted_log_sha256, redacted_captured_sha256,
        server_mtime, exit_code, elapsed_seconds, env_keys_present,
        verdict, failure_token. (failure_detail included as 14th
        soft-companion to failure_token; tests only require the 13 above.)

    ``env_keys_present`` carries the SORTED list of env-var NAMES present
    at re-exec time (NEVER values — abuse trail per CONTEXT.md). The
    redacted_* SHA256s let auditors verify the comparator decision after
    the fact without re-deriving regex application.

    Plan 05-03 / EVID-02: ``evidence_for`` field carries the requirement
    IDs declared in the artifact's ``# evidence-for:`` header (parsed
    upstream by ``_parse_evidence_header`` Plan 05-02 dispatch branch).
    Defaults to empty list so backwards-compat callers that haven't
    migrated produce records with ``evidence_for=[]`` rather than
    KeyError on field absence. The Phase 5 coverage check at
    ``foundry_handoff.py::foundry_accept_casting`` is the primary
    consumer at the gate layer.
    """
    rel_path: str
    try:
        # If evidence is under a worktree at run_dir/worktrees/casting-N/
        # evidence/casting-N-name.log, return "evidence/casting-N-name.log".
        rel_path = str(evidence_path.relative_to(evidence_path.parents[1]))
    except (ValueError, IndexError):
        rel_path = str(evidence_path)
    env_keys = sorted(os.environ.keys())
    return {
        "evidence_path": rel_path,
        "evidence_cmd": evidence_cmd,
        "casting_commit": casting_commit,
        "log_sha256": _hash_str(log_text),
        "captured_sha256": _hash_str(captured_text),
        "redacted_log_sha256": _hash_str(redacted_log),
        "redacted_captured_sha256": _hash_str(redacted_captured),
        "server_mtime": datetime.now(tz=timezone.utc).isoformat(timespec="seconds"),
        "exit_code": exit_code,
        "elapsed_seconds": round(elapsed_seconds, 3),
        "env_keys_present": env_keys,
        "verdict": verdict,
        "failure_token": failure_token,
        "failure_detail": failure_detail,
        "evidence_for": list(evidence_for or []),  # Phase 5 / EVID-02
    }


# ---------------------------------------------------------------------------
# Single-evidence-file verifier (Plan 04-03).
#
# Decomposed from ``verify_evidence`` so the iteration loop stays readable.
# Each evidence file goes through:
#
#   parse header → run cmd → compare → check stub patterns → produce record
#
# Failures short-circuit: header parse failure → no re-exec; non-zero exit →
# no comparison (would always mismatch on error output anyway); timeout →
# returns -1 from the executor.
# ---------------------------------------------------------------------------
def _verify_one_evidence_file(
    evidence_path: Path,
    worktree_path: Path,
    casting_commit: str,
) -> dict[str, Any]:
    """Verify a single evidence file. Returns one provenance record."""
    log_text = evidence_path.read_text(encoding="utf-8", errors="replace")

    # Step 1: Parse header.
    #
    # Plan 05-03: catch-block routes EVIDENCE_FOR_MALFORMED separately from
    # EVIDENCE_VOLATILE_MALFORMED so the surfaced failure_token names the
    # actual concern (Phase 5 / EVID-02 closed-vocabulary discipline). The
    # parser raises ValueError with a token-prefixed message for both
    # branches; we sniff the prefix to route. Default fallback preserves
    # Phase 4 behavior (any unrecognized prefix → EVIDENCE_VOLATILE_MALFORMED).
    try:
        header = _parse_evidence_header(log_text)
    except ValueError as exc:
        msg = str(exc)
        if msg.startswith("EVIDENCE_FOR_MALFORMED"):
            token = "EVIDENCE_FOR_MALFORMED"
        else:
            token = "EVIDENCE_VOLATILE_MALFORMED"  # legacy fallback
        return _make_provenance_record(
            evidence_path=evidence_path,
            evidence_cmd=None,
            casting_commit=casting_commit,
            log_text=log_text,
            captured_text="",
            redacted_log="",
            redacted_captured="",
            exit_code=None,
            elapsed_seconds=0.0,
            verdict="rejected",
            failure_token=token,
            failure_detail=msg,
            evidence_for=[],  # parse failed — no IDs available
        )

    # Step 2: Cmd presence is mandatory.
    if header.get("cmd") is None:
        return _make_provenance_record(
            evidence_path=evidence_path,
            evidence_cmd=None,
            casting_commit=casting_commit,
            log_text=log_text,
            captured_text="",
            redacted_log="",
            redacted_captured="",
            exit_code=None,
            elapsed_seconds=0.0,
            verdict="rejected",
            failure_token="EVIDENCE_COMMAND_MISSING",
            failure_detail=f"no `# evidence-cmd:` header in {evidence_path.name}",
            evidence_for=header.get("evidence_for", []),
        )

    timeout = header.get("timeout") or EVIDENCE_TIMEOUT_DEFAULT_SECONDS

    # Step 3: Re-execute.
    exit_code, captured, elapsed = _run_command_with_timeout(
        cmd=header["cmd"], cwd=worktree_path, timeout=timeout,
    )

    # Step 4a: Timeout (-1) → EVIDENCE_TIMEOUT.
    if exit_code == -1:
        return _make_provenance_record(
            evidence_path=evidence_path,
            evidence_cmd=header["cmd"],
            casting_commit=casting_commit,
            log_text=log_text,
            captured_text=captured,
            redacted_log="",
            redacted_captured="",
            exit_code=exit_code,
            elapsed_seconds=elapsed,
            verdict="rejected",
            failure_token="EVIDENCE_TIMEOUT",
            failure_detail=(
                f"command exceeded {timeout}s; killed via SIGTERM/SIGKILL"
            ),
            evidence_for=header.get("evidence_for", []),
        )

    # Step 4b: Non-zero exit → EVIDENCE_EXIT_NONZERO.
    if exit_code != 0:
        return _make_provenance_record(
            evidence_path=evidence_path,
            evidence_cmd=header["cmd"],
            casting_commit=casting_commit,
            log_text=log_text,
            captured_text=captured,
            redacted_log="",
            redacted_captured="",
            exit_code=exit_code,
            elapsed_seconds=elapsed,
            verdict="rejected",
            failure_token="EVIDENCE_EXIT_NONZERO",
            failure_detail=f"command exited with code {exit_code}",
            evidence_for=header.get("evidence_for", []),
        )

    # Step 5: Byte-match comparison (volatile redaction applied to both).
    #
    # `_compare_byte_match` documents its `committed` parameter as "the
    # evidence-file BODY", but was handed the whole file — header block
    # included. Since a re-executed command emits only the body, every
    # correctly-formatted evidence file mismatched on its own `# evidence-*:`
    # header lines. It went unnoticed because `casting_commit` was unreachable
    # over MCP, so this comparison had never run outside the test harness.
    #
    # The strip is applied SYMMETRICALLY, which is what keeps both conventions
    # working: a real evidence file (committed header+body vs captured body)
    # matches on the body, and the `use_cat_replay` harness (whose replay file
    # deliberately holds the full rewritten evidence, so BOTH sides carry the
    # header) still compares body against body. The regex only matches a
    # LEADING run of `#` comment and blank lines, so it cannot eat content.
    try:
        matched, diff, redacted_log, redacted_captured = _compare_byte_match(
            committed=_strip_leading_header_block(log_text),
            captured=_strip_leading_header_block(captured),
            volatile_patterns=header.get("volatile", []),
        )
    except ValueError as exc:
        return _make_provenance_record(
            evidence_path=evidence_path,
            evidence_cmd=header["cmd"],
            casting_commit=casting_commit,
            log_text=log_text,
            captured_text=captured,
            redacted_log="",
            redacted_captured="",
            exit_code=exit_code,
            elapsed_seconds=elapsed,
            verdict="rejected",
            failure_token="EVIDENCE_VOLATILE_MALFORMED",
            failure_detail=str(exc),
            evidence_for=header.get("evidence_for", []),
        )

    if not matched:
        return _make_provenance_record(
            evidence_path=evidence_path,
            evidence_cmd=header["cmd"],
            casting_commit=casting_commit,
            log_text=log_text,
            captured_text=captured,
            redacted_log=redacted_log,
            redacted_captured=redacted_captured,
            exit_code=exit_code,
            elapsed_seconds=elapsed,
            verdict="rejected",
            failure_token="EVIDENCE_OUTPUT_MISMATCH",
            failure_detail=diff,
            evidence_for=header.get("evidence_for", []),
        )

    # Step 6: Stub patterns fire ON TOP of byte-match (CONTEXT.md locked).
    stub_token = _check_stub_patterns(log_text, header["cmd"])
    if stub_token:
        return _make_provenance_record(
            evidence_path=evidence_path,
            evidence_cmd=header["cmd"],
            casting_commit=casting_commit,
            log_text=log_text,
            captured_text=captured,
            redacted_log=redacted_log,
            redacted_captured=redacted_captured,
            exit_code=exit_code,
            elapsed_seconds=elapsed,
            verdict="rejected",
            failure_token="EVIDENCE_STUB_DETECTED",
            failure_detail=f"{stub_token}: stub-pattern hit on committed log",
            evidence_for=header.get("evidence_for", []),
        )

    # Accepted.
    return _make_provenance_record(
        evidence_path=evidence_path,
        evidence_cmd=header["cmd"],
        casting_commit=casting_commit,
        log_text=log_text,
        captured_text=captured,
        redacted_log=redacted_log,
        redacted_captured=redacted_captured,
        exit_code=exit_code,
        elapsed_seconds=elapsed,
        verdict="accepted",
        failure_token=None,
        failure_detail=None,
        evidence_for=header.get("evidence_for", []),
    )


# ---------------------------------------------------------------------------
# v2.0 backwards-compat routing + manifest persistence (Plan 04-04 territory).
#
# spec_format_version frontmatter parsing duplicates the small regex pair from
# plugins/forge/scripts/validate-spec.py (extract_frontmatter shape) — the
# script's hyphen-named filename (validate-spec.py) is not a valid Python
# identifier so cross-import is impossible (RESEARCH.md Anti-Pattern: hyphen-
# named scripts can't be imported). Same regex shape locked to Phase 3 Plan
# 03-02 patterns; permissive defaults — validator-script's job to hard-fail
# on unknown versions at SPEC FORGED time. Plan 04-04 just routes the legacy
# v2.0 path through manifest.stream_skips.
# ---------------------------------------------------------------------------
_FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.DOTALL)
_SPEC_VERSION_RE = re.compile(
    r"^\s*spec_format_version\s*:\s*(?:\"([^\"\n]+)\"|'([^'\n]+)'|(\S+))",
    re.MULTILINE,
)


def _declared_spec_format_version(spec_path: Path) -> str | None:
    """Return the RAW ``spec_format_version`` value declared in frontmatter.

    ``None`` means the spec declares no version at all — an unreadable file,
    no frontmatter block, or no ``spec_format_version`` key. A non-None result
    is whatever the author actually wrote, unparsed, so a caller can name it
    back in an error message.
    """
    if not spec_path.exists():
        return None
    try:
        text = spec_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return None
    kv = _SPEC_VERSION_RE.search(m.group(1))
    if not kv:
        return None
    return (kv.group(1) or kv.group(2) or kv.group(3) or "").strip()


def _read_spec_format_version(spec_path: Path) -> tuple[int, int] | None:
    """Return the parsed ``(major, minor)`` version, or ``None`` if malformed.

    Two failure modes that used to collapse into one are now distinct, because
    they call for opposite handling:

      * **Absent** — no file, no frontmatter, no key. Returns ``(2, 0)``. This
        permissive default is deliberate and unchanged: pre-v2.1 specs simply
        predate the key, and ``validate-spec.py`` is the hard-fail authority at
        SPEC FORGED time.
      * **Declared but unparseable** — the key is present and its value is not
        ``vN.N``. Returns ``None``. Silently reading this as v2.0 downgraded the
        run to the stream-skip branch, so a typo in one frontmatter line bought
        a green ``ok: true`` with zero evidence re-executed. An author who wrote
        the key meant to say something; the only safe reading of an unintelligible
        version is to refuse, not to guess the lowest one.

    Mirrors Phase 3's ``extract_frontmatter`` shape; duplicated here because
    hyphen-named ``validate-spec.py`` cannot be imported.
    """
    raw = _declared_spec_format_version(spec_path)
    if raw is None:
        return (2, 0)
    vm = re.match(r"^v(\d+)\.(\d+)$", raw)
    if not vm:
        return None
    return (int(vm.group(1)), int(vm.group(2)))


def _resolve_manifest_path(project_root: Path, run_dir: Path | None) -> Path:
    """Resolve the castings manifest THIS RUN actually keeps.

    The manifest a run reads and writes is
    ``foundry-archive/{run}/castings/manifest.json`` — ``foundry_init`` creates
    it there and every manifest reader loads it from there. Both writers below
    built ``<project_root>/castings/manifest.json``, a path no real run has, so
    every append silently no-op'd against the "manifest is missing" guard: the
    live run's castings all carried ``evidence_provenance: []`` while evidence
    verification was in fact running and accepting. The tests missed it because
    the harness synthesizes a manifest at exactly the wrong path.

    Resolution mirrors the spec path's rather than inventing a third rule: the
    run dir the caller already holds, then the session's active run, and only
    then the project-root form — which survives for callers that have no active
    run at all (direct ``verify_evidence`` invocations, fixtures).
    """
    for candidate in (run_dir, get_run_dir(str(project_root))):
        if candidate is None:
            continue
        manifest = candidate / "castings" / "manifest.json"
        if manifest.exists():
            return manifest
    return project_root / "castings" / "manifest.json"


def _append_to_manifest_stream_skips(
    manifest_path: Path,
    skip_record: dict[str, Any],
) -> None:
    """Append ``skip_record`` to ``manifest.stream_skips`` (Phase 3 schema).

    Initializes the array if absent; preserves existing entries. Takes the
    resolved manifest path rather than re-deriving one from ``project_root``:
    two independent derivations of the same path is what let this writer and
    its readers disagree in the first place.
    """
    if not manifest_path.exists():
        return
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    skips = manifest.setdefault("stream_skips", [])
    if not isinstance(skips, list):
        skips = []
        manifest["stream_skips"] = skips
    skips.append(skip_record)
    manifest_path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _append_to_manifest_evidence_provenance(
    manifest_path: Path,
    casting_id: int | str,
    record: dict[str, Any],
) -> None:
    """Append ``record`` to ``manifest.castings[N].evidence_provenance``.

    Locates the casting by string-equal id match against ``castings[*].id``;
    synthesizes a minimal entry if absent (Plan 04-04 author's discretion;
    upgrade to error if abuse surfaces). Takes the resolved manifest path and
    silently no-ops when it is missing — same discipline as
    ``_append_to_manifest_stream_skips``.
    """
    if not manifest_path.exists():
        return
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    castings = manifest.setdefault("castings", [])
    if not isinstance(castings, list):
        castings = []
        manifest["castings"] = castings
    casting = next(
        (c for c in castings if str(c.get("id")) == str(casting_id)),
        None,
    )
    if casting is None:
        casting = {"id": str(casting_id), "evidence_provenance": []}
        castings.append(casting)
    arr = casting.setdefault("evidence_provenance", [])
    if not isinstance(arr, list):
        arr = []
        casting["evidence_provenance"] = arr
    arr.append(record)
    manifest_path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# Top-level entry point (Plan 04-03 body refactored into
# _verify_evidence_v21_body; Plan 04-04 wraps with v2.0 stream-skip routing +
# manifest persistence + foundry_accept_casting integration).
# ---------------------------------------------------------------------------
def verify_evidence(
    casting_id: int | str,
    project_root: Path,
    casting_commit: str,
    *,
    spec_path: Path | None = None,
    run_dir: Path | None = None,
) -> dict[str, Any]:
    """Top-level Phase 4 evidence verification entry point.

    Plan 04-04 wraps Plan 04-03's body with:
      - v2.0 backwards-compat routing: if ``spec_format_version`` parsed from
        ``spec_path`` is below ``MIN_SPEC_FORMAT_VERSION_FOR_EVID_01`` (i.e.
        ``v2.0``), record an EVID-01 entry in ``manifest.stream_skips`` and
        return ``verdict='skipped'`` WITHOUT re-execution (worktree never
        created — preserves Phase 1/2/3 v4.2.0 backwards-compat).
      - Manifest persistence: on the v2.1+ path, every provenance record is
        also appended to ``manifest.castings[N].evidence_provenance``.

    Args:
        casting_id: casting identifier (int or str — manifest stores as str).
        project_root: repo root containing ``.git`` and the casting commit.
        casting_commit: full SHA of the casting's commit (rev-parseable).
        spec_path: optional explicit spec.md path. When absent, defaults to
            ``project_root / 'specs' / 'spec.md'``. Read for
            ``spec_format_version`` to decide v2.0 stream-skip vs v2.1+
            engagement. An ABSENT version → v2.0 (permissive default;
            validate-spec.py is the hard-fail authority). A version that is
            DECLARED but unparseable → ``verdict='rejected'``, never a
            silent downgrade.
        run_dir: parent directory under which the worktree is created at
            ``run_dir / 'worktrees' / 'casting-{id}'``. REQUIRED on the
            v2.1+ engagement path; not consumed on the v2.0 skip path. Also
            the FIRST candidate for locating the run's castings manifest.

    Returns:
        ``{
            'verdict': 'accepted' | 'rejected' | 'skipped',
            'failure_token': str | None,
            'failure_detail': str | None,
            'provenance_records': list[dict],
            'manifest_updates': dict,
            'spec_path': str,
            'spec_format_version': 'vN.N' | str | None,
            'manifest_path': str,
        }``

    ``spec_path`` / ``spec_format_version`` report the spec this call actually
    read and the version it parsed out of it, on every branch — the audit
    trail for the routing decision. On the malformed-version branch
    ``spec_format_version`` carries the raw declared string rather than a
    parsed ``vN.N``, which is the point: it names what was unreadable.

    ``manifest_path`` reports the castings manifest this call actually wrote,
    resolved by ``_resolve_manifest_path``. It is the second half of the same
    audit trail — a provenance append that lands nowhere is indistinguishable
    from one that never happened unless the destination is reported.

    On the v2.0 skip path ``manifest_updates['stream_skips']`` carries the
    appended record so callers (e.g. ``foundry_accept_casting``) can audit
    the routing decision without re-reading the manifest.
    """
    # v2.0 backwards-compat gate (Plan 04-04 / Pitfall 6 from RESEARCH.md).
    # Reading spec_format_version BEFORE worktree setup keeps the v2.0 path
    # zero-cost — no .git/config.lock contention, no subprocess spawn.
    effective_spec_path = (
        spec_path if spec_path is not None
        else project_root / "specs" / "spec.md"
    )
    manifest_path = _resolve_manifest_path(project_root, run_dir)
    spec_version = _read_spec_format_version(effective_spec_path)

    # A DECLARED but unparseable version is refused, never downgraded. Reading
    # it as v2.0 is what made a one-character frontmatter typo return
    # `ok: true` with `verdict: "skipped"` and zero evidence re-executed — the
    # loudest possible failure dressed as a pass. Absence still defaults to
    # v2.0 (see `_read_spec_format_version`); only an unintelligible
    # declaration lands here.
    if spec_version is None:
        declared = _declared_spec_format_version(effective_spec_path)
        return {
            "verdict": "rejected",
            # Deliberately no closed-vocabulary token: this is a spec-authoring
            # error, not an evidence failure, and KNOWN_EVIDENCE_FAILURE_TOKENS
            # names only the latter. The named refusal a lead actually sees is
            # raised one rung earlier, on foundry_accept_casting's precondition
            # ladder, which uses the {ok, error, hint} shape instead.
            "failure_token": None,
            "failure_detail": (
                f"{effective_spec_path} declares spec_format_version "
                f"{declared!r}, which is not a vN.N version. Evidence "
                f"verification refuses to guess a version: fix the spec's "
                f"frontmatter."
            ),
            "provenance_records": [],
            "manifest_updates": {},
            "spec_path": str(effective_spec_path),
            "spec_format_version": declared,
        }

    if spec_version < MIN_SPEC_FORMAT_VERSION_FOR_EVID_01:
        skip_record = {
            "stream_id": "EVID-01",
            "reason": "spec_format_version",
            "spec_version": f"v{spec_version[0]}.{spec_version[1]}",
            "stream_min": (
                f"v{MIN_SPEC_FORMAT_VERSION_FOR_EVID_01[0]}."
                f"{MIN_SPEC_FORMAT_VERSION_FOR_EVID_01[1]}"
            ),
            "agent_path": None,  # virtual stream — owned by foundry_accept_casting
        }
        _append_to_manifest_stream_skips(manifest_path, skip_record)
        return {
            "verdict": "skipped",
            "failure_token": None,
            "failure_detail": None,
            "provenance_records": [],
            "manifest_updates": {"stream_skips": [skip_record]},
            "spec_path": str(effective_spec_path),
            "spec_format_version": f"v{spec_version[0]}.{spec_version[1]}",
            "manifest_path": str(manifest_path),
        }

    # v2.1+ engagement path delegates to the Plan 04-03 body, then persists
    # provenance records into manifest.castings[N].evidence_provenance.
    result = _verify_evidence_v21_body(
        casting_id=casting_id,
        project_root=project_root,
        casting_commit=casting_commit,
        run_dir=run_dir,
    )
    for record in result.get("provenance_records", []):
        _append_to_manifest_evidence_provenance(manifest_path, casting_id, record)
    result["manifest_path"] = str(manifest_path)
    # Report WHICH spec drove the routing decision on both branches. A caller
    # that hands over the wrong path gets a silent v2.0 downgrade otherwise —
    # `_read_spec_format_version` defaults to (2, 0) on a missing file — so
    # the effective path is the only evidence distinguishing "this run is
    # legitimately v2.0" from "the caller pointed at a spec that isn't there".
    result["spec_path"] = str(effective_spec_path)
    result["spec_format_version"] = f"v{spec_version[0]}.{spec_version[1]}"
    return result


def _verify_evidence_v21_body(
    casting_id: int | str,
    project_root: Path,
    casting_commit: str,
    *,
    run_dir: Path | None = None,
) -> dict[str, Any]:
    """v2.1+ evidence-verification body (Plan 04-03 logic, byte-equivalent).

    Discovers ``evidence/casting-{id}-*.log`` in the casting commit's
    worktree, parses each, re-executes, redacts, compares, runs stub
    patterns, returns provenance records. ``try/finally`` guarantees
    worktree teardown on success AND failure paths.

    Plan 04-04 lifts the body unchanged from Plan 04-03's ``verify_evidence``
    so the v2.0 routing wrapper can decide before re-execution begins.
    """
    if run_dir is None:
        raise ValueError(
            "run_dir required for v2.1+ engagement path; Plan 04-04 callers "
            "(e.g. foundry_accept_casting) must derive it via "
            "foundry_state.get_run_dir before invoking verify_evidence"
        )

    # Pitfall 1: clean up orphaned worktrees from prior crashes (idempotent
    # via _PRUNE_DONE_FOR module-level guard — once per session).
    #
    # D-116: housekeeping, so its failure must never decide the verdict. The
    # prune shells out to git with a timeout and sits OUTSIDE the try below, so
    # a slow or missing git escaped the gate here before the run had even
    # begun. Swallowed rather than translated, because a worktree that could
    # not be pruned is not evidence of anything about the casting — if it
    # matters, `_setup_worktree` fails next and is translated there.
    try:
        _prune_orphaned_worktrees(project_root)
    except (subprocess.SubprocessError, OSError):
        pass

    provenance_records: list[dict[str, Any]] = []
    overall_verdict = "accepted"
    overall_token: str | None = None
    overall_detail: str | None = None

    worktree_path: Path | None = None
    try:
        try:
            worktree_path = _setup_worktree(
                project_root, casting_id, casting_commit, run_dir
            )
        except RuntimeError as exc:
            return {
                "verdict": "rejected",
                "failure_token": "EVIDENCE_COMMIT_MISSING",
                "failure_detail": str(exc),
                "provenance_records": [],
                "manifest_updates": {},
            }
        except (subprocess.SubprocessError, OSError) as exc:
            # D-116: `_setup_worktree` raises RuntimeError only for a non-zero
            # `git worktree add`. Its `subprocess.run(..., timeout=30)` also
            # raises TimeoutExpired, which is NOT a RuntimeError, and a missing
            # git binary raises FileNotFoundError — neither was caught, so both
            # escaped `verify_evidence` untranslated and the gate returned a
            # traceback instead of a verdict. Driven with a slow git shim, the
            # call escaped as TimeoutExpired after 30s.
            #
            # Translated onto EVIDENCE_COMMIT_MISSING rather than a new token:
            # KNOWN_EVIDENCE_FAILURE_TOKENS is a closed vocabulary whose size is
            # pinned by a test outside this casting, and this IS the existing
            # token's meaning — the casting's commit could not be materialised.
            # The detail names the real cause so the operator is not sent
            # looking for a bad SHA when git is simply absent or hung.
            return {
                "verdict": "rejected",
                "failure_token": "EVIDENCE_COMMIT_MISSING",
                "failure_detail": (
                    f"could not create the worktree for commit "
                    f"{str(casting_commit)[:12]}: "
                    f"{type(exc).__name__}: {exc}"
                ),
                "provenance_records": [],
                "manifest_updates": {},
            }

        # Discover evidence files under the casting commit's worktree.
        evidence_dir = worktree_path / "evidence"
        if evidence_dir.exists():
            evidence_files = sorted(
                evidence_dir.glob(f"casting-{casting_id}-*.log")
            )
        else:
            evidence_files = []

        if not evidence_files:
            # Plan 04-04 wraps this with v2.0 stream-skip routing — on
            # v2.0 specs, empty evidence is acceptable (skipped, not
            # rejected). Plan 04-03 ships the rejection path; Plan 04-04
            # wraps the v2.0 skip via its harness/integration layer.
            return {
                "verdict": "rejected",
                "failure_token": "EVIDENCE_COMMAND_MISSING",
                "failure_detail": (
                    f"casting {casting_id} committed no evidence files "
                    f"(expected evidence/casting-{casting_id}-*.log)"
                ),
                "provenance_records": [],
                "manifest_updates": {},
            }

        # Verify each evidence file in turn.
        for ef_path in evidence_files:
            record = _verify_one_evidence_file(
                evidence_path=ef_path,
                worktree_path=worktree_path,
                casting_commit=casting_commit,
            )
            provenance_records.append(record)
            if (
                record["verdict"] == "rejected"
                and overall_verdict == "accepted"
            ):
                overall_verdict = "rejected"
                overall_token = record["failure_token"]
                overall_detail = record["failure_detail"]

    finally:
        # Pitfall 1: teardown ALWAYS runs — accepted, rejected, or
        # exception (try/finally guarantees the cleanup path).
        #
        # D-116: teardown makes three more timeout-bounded git calls, and it
        # runs in a `finally`. An exception raised there REPLACES whatever the
        # body was returning or raising, so a slow git during cleanup could
        # discard a perfectly good verdict — the one place where a housekeeping
        # failure can destroy a real result. Swallowed for that reason; the
        # worst case is a stale worktree dir, which the next run's prune
        # removes.
        if worktree_path is not None and worktree_path.exists():
            try:
                _teardown_worktree(project_root, worktree_path)
            except (subprocess.SubprocessError, OSError):
                pass

    return {
        "verdict": overall_verdict,
        "failure_token": overall_token,
        "failure_detail": overall_detail,
        "provenance_records": provenance_records,
        "manifest_updates": {},
    }
