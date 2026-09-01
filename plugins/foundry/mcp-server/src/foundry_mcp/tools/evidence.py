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
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from foundry_mcp.tools.foundry_handoff import _hash_str
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
            redacted = re.sub(pat, replacement, redacted)
        except re.error as exc:
            raise ValueError(
                f"EVIDENCE_VOLATILE_MALFORMED: invalid regex {pat!r}: {exc}"
            ) from exc
    return redacted


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
#   2. NO_CMD_IN_HEADER — first 3 body lines (header comments + blanks
#      stripped) lack the cmd-first-token substring
#   3. BARE_PASS — log body is a single ``PASS`` (or PASS|OK|✓|SUCCESS for
#      _check_stub_patterns; ``_is_stub_pattern_bare_pass`` is PASS-only
#      per its test contract)
#   4. TIMESTAMP_CLUSTER — log body is predominantly timestamp-only lines
#      (fabricated bulk pattern)
#
# Sub-tokens emitted via ``_check_stub_patterns`` failure_detail; the public
# closed-vocabulary token remains ``EVIDENCE_STUB_DETECTED`` (8-token
# allowlist preserved).
# ---------------------------------------------------------------------------
EVIDENCE_STUB_TOO_SMALL = "EVIDENCE_STUB_TOO_SMALL"
EVIDENCE_STUB_NO_CMD_IN_HEADER = "EVIDENCE_STUB_NO_CMD_IN_HEADER"
EVIDENCE_STUB_BARE_PASS = "EVIDENCE_STUB_BARE_PASS"
EVIDENCE_STUB_TIMESTAMP_CLUSTER = "EVIDENCE_STUB_TIMESTAMP_CLUSTER"

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


def _is_stub_pattern_no_cmd_in_header(
    text: str,
    cmd: str,
    check_lines: int = 3,
) -> bool:
    """Return True if the first ``check_lines`` body lines lack the
    cmd-first-token substring.

    "Body" = output minus leading ``# evidence-*:`` header comment block
    and blank lines. The cmd-first-token is the first whitespace-separated
    token of ``cmd`` (e.g. ``pytest`` for ``pytest -k login``). Used as a
    smoke signal that the captured output starts with execution of the
    declared command rather than fabricated boilerplate.

    Empty cmd ⇒ rule vacuously satisfied (returns False).
    """
    if not cmd:
        return False
    cmd_first_token = cmd.split(maxsplit=1)[0]
    body = _strip_header_and_blank_lines(text)
    first_n = body[:check_lines]
    if not first_n:
        # Empty body — TOO_SMALL territory, not NO_CMD_IN_HEADER.
        return False
    return not any(cmd_first_token in ln for ln in first_n)


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

    Order: TOO_SMALL → NO_CMD_IN_HEADER → BARE_PASS → TIMESTAMP_CLUSTER.
    First hit wins (CONTEXT.md "Stub-pattern library — first hit wins").

    Stub patterns fire ON TOP of byte-match (CONTEXT.md): even when
    ``_compare_byte_match`` succeeds, a stub-pattern hit rejects the log.
    """
    # Pattern 1: TOO_SMALL
    if _is_stub_pattern_too_small(log_text, EVIDENCE_STUB_MIN_BYTES):
        return EVIDENCE_STUB_TOO_SMALL

    # Pattern 2: NO_CMD_IN_HEADER (skip when no cmd to anchor on)
    if evidence_cmd and _is_stub_pattern_no_cmd_in_header(
        log_text, evidence_cmd, check_lines=3
    ):
        return EVIDENCE_STUB_NO_CMD_IN_HEADER

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


def _read_spec_format_version(spec_path: Path) -> tuple[int, int]:
    """Return parsed ``(major, minor)`` tuple, defaulting to ``(2, 0)`` on
    absence or any parse failure.

    Mirrors Phase 3's ``extract_frontmatter`` shape; duplicated here because
    hyphen-named ``validate-spec.py`` cannot be imported. Permissive defaults
    — ``validate-spec.py`` is the script that hard-fails on unknown versions
    at SPEC FORGED time. Plan 04-04 just needs a routing decision: v2.0 →
    stream-skip; v2.1+ → engage re-execution.
    """
    if not spec_path.exists():
        return (2, 0)
    try:
        text = spec_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return (2, 0)
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return (2, 0)
    block = m.group(1)
    kv = _SPEC_VERSION_RE.search(block)
    if not kv:
        return (2, 0)
    val = (kv.group(1) or kv.group(2) or kv.group(3) or "").strip()
    vm = re.match(r"^v(\d+)\.(\d+)$", val)
    if not vm:
        return (2, 0)
    return (int(vm.group(1)), int(vm.group(2)))


def _append_to_manifest_stream_skips(
    project_root: Path,
    skip_record: dict[str, Any],
) -> None:
    """Append ``skip_record`` to ``manifest.stream_skips`` (Phase 3 schema).

    Initializes the array if absent; preserves existing entries. Silently
    no-ops when ``castings/manifest.json`` is absent (test harnesses that
    don't synthesize a manifest still function — provenance lives only in
    the returned dict).
    """
    manifest_path = project_root / "castings" / "manifest.json"
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
    project_root: Path,
    casting_id: int | str,
    record: dict[str, Any],
) -> None:
    """Append ``record`` to ``manifest.castings[N].evidence_provenance``.

    Locates the casting by string-equal id match against ``castings[*].id``;
    synthesizes a minimal entry if absent (Plan 04-04 author's discretion;
    upgrade to error if abuse surfaces). Silently no-ops when manifest is
    missing — same discipline as ``_append_to_manifest_stream_skips``.
    """
    manifest_path = project_root / "castings" / "manifest.json"
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
            engagement. Missing/unparseable → v2.0 (permissive default;
            validate-spec.py is the hard-fail authority).
        run_dir: parent directory under which the worktree is created at
            ``run_dir / 'worktrees' / 'casting-{id}'``. REQUIRED on the
            v2.1+ engagement path; not consumed on the v2.0 skip path.

    Returns:
        ``{
            'verdict': 'accepted' | 'rejected' | 'skipped',
            'failure_token': str | None,
            'failure_detail': str | None,
            'provenance_records': list[dict],
            'manifest_updates': dict,
            'spec_path': str,
            'spec_format_version': 'vN.N',
        }``

    ``spec_path`` / ``spec_format_version`` report the spec this call actually
    read and the version it parsed out of it, on BOTH branches — the audit
    trail for the routing decision.

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
    spec_version = _read_spec_format_version(effective_spec_path)
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
        _append_to_manifest_stream_skips(project_root, skip_record)
        return {
            "verdict": "skipped",
            "failure_token": None,
            "failure_detail": None,
            "provenance_records": [],
            "manifest_updates": {"stream_skips": [skip_record]},
            "spec_path": str(effective_spec_path),
            "spec_format_version": f"v{spec_version[0]}.{spec_version[1]}",
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
        _append_to_manifest_evidence_provenance(project_root, casting_id, record)
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
    _prune_orphaned_worktrees(project_root)

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
        if worktree_path is not None and worktree_path.exists():
            _teardown_worktree(project_root, worktree_path)

    return {
        "verdict": overall_verdict,
        "failure_token": overall_token,
        "failure_detail": overall_detail,
        "provenance_records": provenance_records,
        "manifest_updates": {},
    }
