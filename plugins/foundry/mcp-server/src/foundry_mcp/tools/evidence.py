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
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from foundry_mcp.schemas.vocab import REQUIREMENT_ID_RE
# D-184 / D-187: the THIRD reader of "which requirement IDs does this casting
# own" reads the one derivation too, rather than keeping the full-text scan
# D-180 replaced in the other two. The edge is not new — `_hash_str` has come
# from this module all along — and it stays acyclic: `foundry_handoff` reaches
# BACK into this module only through a lazy in-function import inside
# `foundry_accept_casting`, never at module top.
from foundry_mcp.tools.foundry_handoff import _hash_str, declared_requirement_ids
from foundry_mcp.tools.foundry_spawn import _manifest_shape_problem
from foundry_mcp.tools.foundry_state import get_run_dir, read_document
from foundry_mcp.tools.worktree_helpers import (
    _PRUNE_DONE_FOR,
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
    "EVIDENCE_COMMAND_SYNTAX",           # fallout / US-008 — CT-015 parse-before-execute
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

# The second-level placeholder of the redaction ladder (see
# ``_apply_volatile_redaction``). Named here rather than spelled inline at each
# use so the residue guard and the substituter cannot disagree about which
# tokens are placeholders and which are content.
TIMING_PLACEHOLDER: str = "<TIMING>"

# D-126 — the composed-redaction residue floor.
#
# A declared volatile pattern is supposed to remove a FIELD: a duration, a pid,
# a temp path. When the whole declared set, applied in its declared order to a
# REAL log, leaves this share or less of that log's non-whitespace characters
# standing, what it removed was not a varying field, it was the evidence.
#
# The value is measured, not guessed. Over the 47-log committed corpus the
# lowest surviving share is 0.633 (casting-3-agent-prose.log, 31 of 49
# non-whitespace characters); the most nearly-benign of the five bypasses PROVE
# drove leaves 0.058, and the other four leave 0.000-0.020. 0.25 sits 2.5x below
# every real log and 4.3x above the closest bypass.
EVIDENCE_MIN_RESIDUE_RATIO: float = 0.25

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

# Phase 5 / EVID-02 — the requirement-ID grammar, READ from the vocabulary
# module rather than re-typed here.
#
# D-150: this was a hand-copied literal whose comment claimed to be a
# "single-source-of-truth ... re-used from foundry_handoff.py" while being the
# second of seven copies. All seven knew the same seven families and none knew
# OT- or GI-, so `# evidence-for: OT-011` parsed to the EMPTY LIST and was
# dropped without a word — an evidence file could never bind to an observable
# truth, and EVID-02's requirement-binding check could never see one. The
# canonical pattern is a strict superset of what this copy matched, so no
# `# evidence-for:` header that bound before binds less now (NFR-002).
_REQUIREMENT_ID_RE: re.Pattern[str] = REQUIREMENT_ID_RE


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
            TIMING_PLACEHOLDER if VOLATILE_PLACEHOLDER in pat
            else VOLATILE_PLACEHOLDER
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
    """True when ``pattern`` erases ONE FIXED CANARY, not a varying field.

    A FAST PRE-CHECK, NOT THE GATE (D-126). The gate is
    ``_composed_redaction_problem``, applied by ``_compare_byte_match`` to the
    composed residue of the real texts. This probe survives because it is cheap
    and it names the offending pattern precisely — a caller who writes
    ``[\\s\\S]*`` gets told which header line is wrong rather than being told
    that the redaction as a whole annihilated the log. It must never again be
    relied on as the only check.

    WHAT IT CANNOT SEE, and why D-109 stayed open behind it. This asks a
    question about the PATTERN, against text the pattern has never met. Its own
    docstring used to argue that was the design; it was the hole. Two whole
    families escape it:

      - A pattern anchored on a token that is present in a real log and absent
        from the canaries. ``== test session starts ==[\\s\\S]*`` erases no
        canary and erases every pytest log. So does ``__init__\\.py[\\s\\S]*``,
        and so does ``(?![\\s\\S]*4f2a)[\\s\\S]*``, which fingerprints the
        canary's own literal to match everything EXCEPT a canary.
      - A pattern that is harmless alone and annihilating in company. This runs
        per pattern, inside ``_apply_volatile_redaction``'s loop, so the PAIR
        ``\\A[^\\n]*`` + ``(?s)(?<=\\n)[\\s\\S]*`` — first line, then everything
        after the first newline — passes twice and composes to nothing.

    Both families produced ``verdict='accepted'`` end to end with
    ``log_sha256 != captured_sha256``, which is the entire EVID-01 mechanism
    defeated by one header line. Only a check on the composed output of the
    real text separates them, because only the real text knows which tokens it
    contains and only the composition knows what the patterns do together.

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
        if not _content_residue(probed):
            return True
    return False


#: Whitespace is not evidence. A redaction that leaves only line breaks behind
#: has left nothing to compare, so the residue measure counts non-whitespace
#: characters and nothing else.
_WHITESPACE_RE: re.Pattern[str] = re.compile(r"\s+")


def _content_residue(text: str) -> str:
    """``text`` reduced to the characters that could still discriminate.

    Redaction placeholders are removed because they are what the redaction PUT
    THERE — counting them as surviving content would let a pattern that erased
    a whole log report a full-length residue of ``<VOLATILE>``. Whitespace is
    removed because it survives every substitution and carries no signal.

    Applied to BOTH the pre- and post-redaction text wherever the two are
    compared, so a log that literally contains the placeholder token is
    measured consistently on both sides rather than being scored as if the
    redaction had eaten it.
    """
    without_placeholders = text.replace(VOLATILE_PLACEHOLDER, "").replace(
        TIMING_PLACEHOLDER, ""
    )
    return _WHITESPACE_RE.sub("", without_placeholders)


def _composed_redaction_problem(
    label: str, original: str, redacted: str
) -> str | None:
    """Named reason the COMPOSED redaction of ``original`` proves nothing.

    THE GATE D-109 NEEDED AND DID NOT GET (D-126). ``_compare_byte_match``
    applies the same declared patterns to both the committed log and the
    re-execution capture, so a redaction that annihilates them collapses both
    to one identical string and the byte-match returns matched=True for ANY
    output. The per-pattern canary probe cannot see that happen: it never meets
    the real text, and it runs before the patterns have been composed.

    This one measures the OUTCOME, on the real text, after the whole declared
    set has been applied in order. That is what makes it total over the bypass
    families rather than over a list of remembered bypasses — a pattern
    anchored on any token, known or unknown to this module, is caught by what
    it did, and a pair of patterns that only annihilate together is caught
    because composition is the thing being measured.

    Returns None when the redaction left enough of ``original`` standing to
    still discriminate one command's output from another's, else a sentence
    naming which side collapsed, by how much, and what to do — the house
    refusal shape, raised by the caller under
    ``EVIDENCE_VOLATILE_MALFORMED``.

    A body with no non-whitespace content of its own passes: there was nothing
    for the redaction to erase, so the emptiness is the command's, not the
    declaration's, and blaming the volatile lines for it would refuse a
    correctly-silent command.
    """
    before = _content_residue(original)
    if not before:
        return None
    after = _content_residue(redacted)
    if not after:
        return (
            f"the declared volatile patterns erase the ENTIRE {label} "
            f"({len(before)} characters of content in, none out). Applied to "
            f"both the committed log and the re-execution capture they collapse "
            f"the two to the same string, so any command's output would "
            f"byte-match any log and the evidence gate would prove nothing. "
            f"Narrow each pattern to the field that actually varies between "
            f"runs — a duration, a pid, a temp path — and re-run the sweep."
        )
    ratio = len(after) / len(before)
    if ratio <= EVIDENCE_MIN_RESIDUE_RATIO:
        return (
            f"the declared volatile patterns leave only {ratio:.1%} of the "
            f"{label} standing ({len(after)} of {len(before)} non-whitespace "
            f"characters), at or below the {EVIDENCE_MIN_RESIDUE_RATIO:.0%} "
            f"floor. A volatile pattern removes a varying FIELD; one that "
            f"consumes this much of a real log is redacting the evidence, and "
            f"what survives can no longer tell one command's output from "
            f"another's. Narrow the patterns — or, if the output genuinely is "
            f"almost entirely volatile, cite a command whose output is not."
        )
    return None


# ---------------------------------------------------------------------------
# D-135 — the residue floor measures VOLUME; this measures DISCRIMINATION.
#
# D-126 asked "how much of the log survived the redaction?" and set the floor
# at 25%. That closed the annihilation family and nothing else, because the
# forgery that matters does not annihilate: it redacts ONE LINE. A committed
# log claiming ``== 1650 passed, 4 skipped in 75.00s ==`` against a command
# that actually prints ``== 1631 passed, 19 failed, 4 skipped in 78.11s ==``,
# declared volatile as ``== \d+ passed.*?==`` — "the summary line has a
# duration in it, so it is volatile", the single most ordinary declaration a
# reviewer would wave through — leaves 99.4% of the log standing, sails over
# the floor, and byte-matches. A 25% volume floor cannot see a 0.6%-by-volume
# redaction that removes 100% of the discriminating content.
#
# WHAT THE COMPARATOR CAN ACTUALLY SEE. The redaction is applied identically to
# both sides, so any span it erases cancels out of the comparison. When the two
# redacted texts match but the RAW texts do not, the redaction reconciled a
# disagreement — and the only question worth asking is whether what it
# reconciled was a FIELD or a CLAIM.
#
# It cannot be answered by volume: the corpus's ``rootdir: .*`` swallows an
# entire line to hide a path, exactly as the forgery swallows an entire line to
# hide a verdict. It cannot be answered by similarity: two rootdirs share less
# text than the two summary lines do. It cannot be answered by the pattern's
# shape: both anchor a literal and wildcard to end-of-line. Measured on the
# real corpus, every one of those measures ranks the forgery as MORE innocent
# than the logs it must not refuse.
#
# WHAT DOES ANSWER IT is the shape of the disagreeing VALUE. Cold-driven over
# all 56 committed logs at d3820c5, 30 of them have raw texts that differ, and
# every single differing span is a duration (``4.86s`` / ``4.57s``), a path
# (``rootdir:`` lines, ``.planning`` roots, uv's ``.tmphgnUSu`` build dirs) or
# a size (``12ms``). Not one differs in a bare word or a bare integer. That is
# not a coincidence about this corpus, it is what a volatile field IS: an
# environment or timing artifact whose value carries STRUCTURE — a decimal
# point, a path separator, a unit suffix, a mixed case run. A bare word and a
# bare integer carry no structure of their own; they are whatever the command
# SAID. ``passed`` becoming ``failed`` and ``1650`` becoming ``1631`` are the
# two forgeries PROVE drove, and they are precisely the two shapes a field
# never takes.
#
# So the rule, derived over every member rather than remembered per bypass:
# where the redaction erased a span in which the two real texts disagree, the
# spans must align token-for-token and every disagreeing token must be
# structured. The axes are total by construction — every declared pattern (the
# whole list, in the composed order the substituter uses), every match
# (``finditer``, mirroring ``sub``), every whitespace token, and a character
# partition with no third bucket. Where an axis cannot be walked — a pattern
# matching a different NUMBER of times on the two sides, or a side roster that
# is not a pair — the guard REPORTS rather than silently continuing, because an
# unalignable member is exactly where a bypass would hide.
#
# D-143 — WHY THE CHARACTER-CLASS TEST WAS THE WRONG DERIVATION, AND WHAT
# REPLACED IT.
#
# The first cut of this rung asked ``token.isalpha() or token.isdigit()`` and
# refused when either side was true — a DENYLIST of two shapes, with accept as
# the default. PROVE drove 13 forgery shapes through the shipped
# ``_verify_one_evidence_file`` on a real git repo at a real casting commit and
# 8 were ACCEPTED end to end, because one non-alphanumeric character anywhere
# in the disagreeing token made a verdict read as a "field":
#
#     failures: 0/1650  vs  19/1650     exit=0  vs  exit=1
#     coverage: 95.2%   vs  41.0%       status: all-passed vs some-failed
#     result 1650/1650  vs  1631/1650   run=passed-1650 vs failed-1631
#     out/1650/summary.json vs out/1631/…   rootdir line carrying passed=1650
#
# A log claiming zero failures against a command printing 19 is A-AUTO-005's
# one binding constraint — "the verification loop's standard must not weaken" —
# defeated by a slash. The previous cycle recorded this as a documented limit
# scoped to percentages; the shipped scope was every token carrying one
# non-alphanumeric character, which includes the process exit code and the
# failure count. A limit that names one member of a class and ships a rule
# admitting eight has documented an instance, not a limit.
#
# THE POLARITY IS THE FIX. "What is a volatile field" is now an ALLOWLIST with
# refusal as the default, so a shape nobody anticipated is REPORTED instead of
# waved through. Membership is derived from the corpus rather than remembered
# per bypass: cold-driven over all 65 committed logs at d8215c5 from a clean
# worktree, 37 differ raw and all 37 reconcile, and every differing token pair
# in the entire corpus is one of exactly two shapes — a duration (``4.86s`` /
# ``4.91s``, ``0.07s`` / ``0.18s``, ``54.80s`` / ``55.09s``; 37 pairs) or an
# absolute filesystem path (rootdir and cachedir roots, uv's
# ``.tmphgnUSu`` / ``.tmpLS4FNM`` build dirs, the ``.planning`` root; 13
# pairs). Not one bare word, bare integer, ratio, percentage, ``=``-assignment
# or ``symbol:line``.
#
# WHERE A GRAMMAR'S VARIATION LIVES is the one axis each entry declares, and it
# is what stops the allowlist from re-opening the hole inside a grammar. An
# absolute path is environmental because the run RELOCATED; a duration, a pid
# and a timestamp are environmental because the NUMBER moved. So:
#
#   varies_in="digits"  the disagreement must be confined to the digits
#                       — strip them and the two sides must be IDENTICAL.
#   varies_in="text"    the disagreement must NOT be confined to the digits
#                       — strip them and the two sides must still DIFFER.
#
# One predicate, negated; a total partition with no third bucket, and an
# unrecognised ``varies_in`` is reported rather than silently admitted. That
# second rung is what refuses ``/tmp/out/1650/summary.json`` against
# ``/tmp/out/1631/summary.json`` — both absolute paths, both matching the
# grammar, digit-stripped to the same string, so the only thing that moved was
# a count. It refuses ``/tmp/run-1650/x`` against ``/tmp/run-1631/x`` too,
# where the count hides inside a word-shaped segment that no segment-level
# check would catch. And it costs the corpus nothing: all 13 real path pairs
# still differ after digit-stripping, because a relocated path changes its
# words, not just its numbers.
#
# WHAT IDENTIFIES THE FIELD is the second axis, and it is what D-156 was filed
# on. `varies_in` says WHICH HALF of a token the environment owns; it does not
# say HOW MUCH may vary, and a grammar whose membership test is "the token
# starts with a slash" hands the environment the entire non-digit half of an
# arbitrary string. The registry stated the governing principle itself, on
# ``process_id``: "the key is part of the grammar. `exit=0` against `exit=1` is
# an assignment too and is REFUSED — what makes a pid environmental is that it
# is a pid, not that it has an `=` in it." By that principle what makes a path
# environmental is that it is a ROOTDIR or an INTERPRETER PATH, not that it has
# a `/` in it.
#
# So every grammar now declares a KEY — the text that identifies the token as
# an instance of this field — and where that key lives:
#
#   key=None   the token identifies ITSELF. A duration wears its `s`, a pid
#              wears its `pid=`, a timestamp wears the ISO shape. `token`
#              fullmatching both sides IS the membership test.
#   key=<re>   the token is shapeless on its own, so the identifier sits in
#              the span BESIDE it and must match on BOTH sides. This is the
#              path family: nothing about `/x/y/z` says whether it is where
#              the run happened or what the command reported.
#
# DERIVED, NOT REMEMBERED. Cold-driven at 1e07a4c over all 77 committed logs
# from a clean detached worktree, the corpus produces 16 path disagreements
# across 10 logs, and EVERY ONE of them is keyed:
#
#     rootdir: <path>                              9 disagreements, 9 logs
#     platform … -- Python … -- <path>             6 disagreements, 6 logs
#     <path containing /.planning/>                1 disagreement,  1 log
#
# Not one is a bare path. `absolute_path`'s `token=/\S*` was therefore strictly
# WIDER than every witness it cited — the derivation gap D-156 names — and the
# three forgeries PROVE drove through it (`/var/run/failed/report.txt` against
# `…/passed/…`, `/FAILED` against `/PASSED`, `/deadbeefcafe1234` against
# `/0badc0de99887766`) are all UNKEYED. Splitting the one wide entry into the
# three keyed entries the corpus actually witnesses refuses all three by name
# and keeps all 16 real disagreements green.
#
# WHY NOT A SEGMENT-POSITION RULE. The rule proposed with the defect — admit
# only a head-prefix (root) substitution, require the tail after the last
# common segment to be byte-identical — was measured against the same cold
# corpus before being rejected: 7 of the 9 distinct real pairs are INTERIOR
# single-segment substitutions (`…/builds-v0/.tmphgnUSu/bin/python` against
# `…/.tmpvRAwWQ/…`, six logs; `…/scratchpad/wt-head/plugins/…` against
# `…/wt-base/…`, six logs), so that rule refuses ten currently-green logs. It
# also cannot refuse forgeries 2 and 3, which have no common tail at all. The
# interior swap `wt-head`→`wt-base` and the forgery `failed`→`passed` are the
# same shape; no rule over segment counts, positions or character classes
# separates them. The KEY separates them, which is why the key is the bound.
#
# EVERY GRAMMAR CARRIES A LIVE WITNESS, or the registry sweep names it. Two
# witness kinds, both real artifacts in the tree rather than a maintainer's
# memory: ``corpus``, meaning some committed log's own declared pattern erases
# a token of this shape — UNDER THIS GRAMMAR'S KEY — from its own body, and
# ``protocol``, meaning ``plugins/foundry/agents/teammate.md`` ships this
# pattern as an example it tells evidence authors to declare.
# ``test_every_declared_grammar_has_a_live_witness`` walks the registry itself
# — not a hand-copied list — and reports by name any grammar whose witness has
# gone dead, so a grammar cannot outlive the reason it was admitted.
#
# AND NO GRAMMAR IS WIDER THAN ITS WITNESS. The sweep used to check only that
# a witness EXISTS, which is how a `/\S*` token kept a witness that was really
# a `rootdir:` path. Each entry therefore carries two driven pairs, both walked
# by the registry sweep rather than by a hand-written test per grammar:
#
#   witness_pair  a REAL disagreement from the cold corpus run, which the
#                 grammar must ADMIT — so no grammar is narrower than its
#                 witness and a rewrite that broke the corpus fails by name.
#   falsifier     the SAME disagreement with the grammar's identifier removed
#                 (the key stripped, or the unit stripped off the token),
#                 which the whole registry must REFUSE — so no grammar is
#                 wider than its identifier.
#
# STATED RESIDUAL, scoped to what actually ships rather than to the one
# instance that was easiest to describe. D-143's ruling was that a limit is a
# limit only when its DECLARED scope is its REAL scope, so this names the whole
# of it, including the part that is uncomfortable.
#
# A disagreement is admitted when it wears a declared field's identifier and
# varies in that field's own direction. Concretely, and these are the real
# attacks, not a euphemism for them:
#
#   1. A fabricated duration against a real one — `4.86s` against `9.91s`, and
#      equally `4.86s` against `99999999.0s`. The key bounds WHICH field may
#      vary; within a field the magnitude is the environment's and no shape
#      rule bounds it without a remembered constant. Refusing this would refuse
#      44 of the corpus's 60 differing token pairs. The same holds for `pid=`
#      and for the ISO date.
#   2. A path that wears a real key. A committed log citing
#      `rootdir: /logs/passed/run` against a command printing
#      `rootdir: /logs/failed/run` is admitted, because `wt-head` against
#      `wt-base` under that same key is a real corpus pair of exactly that
#      shape. The bound is the key, not the value: an attacker needs the REAL
#      command to print a verdict word inside its rootdir or its interpreter
#      path. The same holds for a token carrying the literal `/.planning/`,
#      whose anchor is in the token rather than beside it.
#
# What is NOT in the residual, and was in the last one: an UNKEYED path. All
# three of PROVE's forgeries, every verdict, count, ratio, percentage, exit
# code, assignment and hyphenated word standing on its own, and every count
# embedded in a path — `/tmp/out/1650/x` against `/tmp/out/1631/x` is refused,
# and so is `/tmp/run-1650/x`, key or no key.
# ---------------------------------------------------------------------------
_DIGIT_RUN_RE: re.Pattern[str] = re.compile(r"\d+")

#: The two directions a grammar's environmental variation can run. Closed, and
#: total over the registry: ``test_every_grammar_declares_a_known_variation_site``
#: walks ``_ENVIRONMENTAL_GRAMMARS`` and fails on any other value, and
#: ``_environmental_field`` REPORTS an unrecognised one rather than admitting
#: the token. There is deliberately no third bucket — a shape whose
#: disagreement is neither confined to digits nor confined to non-digits is
#: not a field, it is two different strings.
_KNOWN_VARIATION_SITES: frozenset[str] = frozenset({"digits", "text"})  # 2 sites


@dataclass(frozen=True)
class _EnvironmentalGrammar:
    """One shape the environment is allowed to vary, and where it varies.

    ``token`` must FULLMATCH both sides of a disagreement — a partial match
    would let an arbitrary prefix ride along beside a legal field.

    ``varies_in`` names the half of the token the environment owns; the other
    half must be byte-identical across the two sides. See the block comment
    above for why the two halves are not symmetric.

    ``key`` is what IDENTIFIES a token as an instance of this field, and it is
    the bound on how much may vary (D-156). ``None`` means the token is
    self-identifying — a duration wears its ``s``, a pid its ``pid=`` — so
    ``token`` fullmatching both sides is the whole membership test. A compiled
    pattern means the identifier sits in the span BESIDE the token, and it must
    match the text preceding the token on BOTH sides. Nothing about ``/x/y/z``
    says whether it is where the run happened or what the command reported, so
    every path grammar is keyed.

    ``witness_kind`` / ``witness`` record what keeps this entry alive:
    ``corpus`` names a committed evidence log whose own declared pattern erases
    a token of this shape, under this key, from its own body; ``protocol``
    names the literal ``# evidence-volatile:`` example ``agents/teammate.md``
    ships.

    ``witness_pair`` is ``(key_context, side_a, side_b)`` — a REAL disagreement
    this grammar must ADMIT, taken from the cold corpus run rather than
    invented. ``falsifier`` is the same triple with the grammar's identifier
    removed, which the whole registry must REFUSE. The registry sweep drives
    both, so an entry cannot be narrower OR wider than what witnesses it.
    """

    token: re.Pattern[str]
    varies_in: str
    key: re.Pattern[str] | None
    witness_kind: str
    witness: str
    witness_pair: tuple[str, str, str]
    falsifier: tuple[str, str, str]
    note: str

    @property
    def sample(self) -> str:
        """One real token of this shape — the ``a`` side of the witness pair."""
        return self.witness_pair[1]


#: The closed allowlist of environmental shapes. Refusal is the default: a
#: disagreeing token pair that matches no entry here is evidence, not a field.
#:
#: Provenance is per entry and checked, not asserted. Anything the corpus does
#: not exercise and the protocol does not document is ABSENT on purpose —
#: notably a byte-size and a content hash, which the lead's brief anticipated
#: but which no committed log varies and no teammate.md example declares.
#: Adding one means adding its witness, which is the point.
_ENVIRONMENTAL_GRAMMARS: dict[str, _EnvironmentalGrammar] = {
    "duration_seconds": _EnvironmentalGrammar(
        token=re.compile(r"\d+\.\d+s"),
        varies_in="digits",
        key=None,  # the `s` unit is in the token
        witness_kind="corpus",
        witness="casting-1-pytest.log",
        witness_pair=("", "0.47s", "0.76s"),
        falsifier=("", "0.47", "0.76"),  # strip the unit: a bare ratio-less number
        note=(
            "wall-clock seconds. 44 of the corpus's 60 differing token pairs, "
            "and the shape 52 of the 77 logs declare volatile. The decimal "
            "point is required because every second-valued duration in the "
            "corpus carries one; a bare `\\d+s` has no witness. Magnitude is "
            "unbounded WITHIN the unit and that is residual 1 above."
        ),
    ),
    "duration_millis": _EnvironmentalGrammar(
        token=re.compile(r"\d+(?:\.\d+)?(?:ms|us|µs|ns)"),
        varies_in="digits",
        key=None,  # the unit suffix is in the token
        witness_kind="protocol",
        witness=r"# evidence-volatile: \b\d+ms\b",
        witness_pair=("", "12ms", "15ms"),
        falsifier=("", "12", "15"),
        note=(
            "sub-second latencies. 40 logs DECLARE an `Installed \\d+ packages "
            "in \\d+ms` shape, but the witness sweep showed no committed body "
            "actually carries one — uv prints that line only on a cold cache, "
            "so the declaration is precautionary and the corpus has never "
            "varied a millisecond value. The live witness is therefore the "
            "protocol, not the corpus. Kept because teammate.md tells authors "
            "to declare this shape and the narrowness controls exercise it. "
            "Note the unit is part of the identity: `12ms` against `12ns` has "
            "different digit skeletons and is REFUSED."
        ),
    ),
    "pytest_rootdir": _EnvironmentalGrammar(
        token=re.compile(r"/\S*"),
        varies_in="text",
        key=re.compile(r"(?:^|\s)rootdir:$"),
        witness_kind="corpus",
        witness="casting-3-observations.log",
        witness_pair=(
            "rootdir:",
            "/Users/rayjanoka/ab/code/guild/plugins/foundry/mcp-server",
            "/tmp/wt/casting-3/plugins/foundry/mcp-server",
        ),
        falsifier=(
            "",  # the SAME relocation with no key beside it
            "/Users/rayjanoka/ab/code/guild/plugins/foundry/mcp-server",
            "/tmp/wt/casting-3/plugins/foundry/mcp-server",
        ),
        note=(
            "where pytest thinks the project is. 9 of the corpus's 16 path "
            "disagreements, under the `rootdir: .*` (6 logs) and "
            "`rootdir: \\S+` (4 logs) declarations. The gate ALWAYS re-executes "
            "inside a worktree it creates, so this line differs on every "
            "honest verification and the whole corpus depends on it."
        ),
    ),
    "pytest_platform_interpreter": _EnvironmentalGrammar(
        token=re.compile(r"/\S*"),
        varies_in="text",
        key=re.compile(r"(?:^|\s)platform \S+ -- Python \S+.* --$"),
        witness_kind="corpus",
        witness="casting-5-protocol-prose.log",
        witness_pair=(
            "platform darwin -- Python 3.14.6, pytest-9.1.1, pluggy-1.6.0 --",
            "/Users/rayjanoka/.cache/uv/builds-v0/.tmphgnUSu/bin/python",
            "/Users/rayjanoka/.cache/uv/builds-v0/.tmpvRAwWQ/bin/python",
        ),
        falsifier=(
            "",
            "/Users/rayjanoka/.cache/uv/builds-v0/.tmphgnUSu/bin/python",
            "/Users/rayjanoka/.cache/uv/builds-v0/.tmpvRAwWQ/bin/python",
        ),
        note=(
            "the interpreter pytest -v reports, which under uv is a per-build "
            "temp directory: 6 of the 16 path disagreements, one per log that "
            "declares the whole `platform … --` line. The varying segment is "
            "INTERIOR (`.tmphgnUSu` against `.tmpvRAwWQ`), which is why the "
            "bound is this key and not a rule about where in the path the "
            "disagreement sits — see the block comment."
        ),
    ),
    "planning_root": _EnvironmentalGrammar(
        token=re.compile(r"/\S*/\.planning/\S*"),
        varies_in="text",
        key=None,  # the `/.planning/` anchor is in the token
        witness_kind="corpus",
        witness="casting-1-pytest.log",
        witness_pair=(
            "",
            "/private/var/folders/kq/T/tmp.X6ktF5/wt/.planning/phases/09",
            "/private/tmp/scratch/clone/.planning/phases/09",
        ),
        falsifier=(
            "",  # the same relocation with the anchor removed
            "/private/var/folders/kq/T/tmp.X6ktF5/wt/phases/09",
            "/private/tmp/scratch/clone/phases/09",
        ),
        note=(
            "the milestone planning corpus root, printed by a skip message in "
            "test_measure_run. 1 of the 16 path disagreements, under the one "
            "`/[^ ]*/\\.planning/[^ ]*` declaration. Self-keyed: the pattern "
            "erases the bare token with nothing beside it, so the anchor has "
            "to be IN the token or this field has no identifier at all."
        ),
    ),
    "archive_root": _EnvironmentalGrammar(
        token=re.compile(r"/\S*/foundry-archive/\S*"),
        varies_in="text",
        key=None,  # the `/foundry-archive/` anchor is in the token
        witness_kind="corpus",
        witness="casting-8-suite.log",
        witness_pair=(
            "",
            "/private/tmp/c3wt/foundry-archive/thunder-viper",
            "/private/var/folders/kq/T/tmp.X6ktF5/wt/foundry-archive/thunder-viper",
        ),
        falsifier=(
            "",  # the same relocation with the anchor removed
            "/private/tmp/c3wt/thunder-viper",
            "/private/var/folders/kq/T/tmp.X6ktF5/wt/thunder-viper",
        ),
        note=(
            "the run-archive root, printed by the `<name> archive not present "
            "in this checkout: <path>` skip in test_measure_run and "
            "test_migrate_archive. `foundry-archive/` is git-ignored, so those "
            "tests skip in EVERY detached worktree and name that worktree in "
            "the message -- which is to say the line appears exactly when a "
            "sweep re-executes the log and never when it was captured in the "
            "main tree, so without this entry a suite log can be re-captured "
            "any number of times and still never re-execute. Sibling of "
            "`planning_root` in every respect: same shape, same reason, same "
            "self-keying. Self-keyed because the declaration erases the bare "
            "token with nothing beside it, so the anchor has to be IN the "
            "token or the field has no identifier at all; `varies_in='text'` "
            "because what moved is the checkout, not a number. The CLAIM -- "
            "that the archive is absent -- is byte-identical on both sides "
            "and stays visible."
        ),
    ),
    "process_id": _EnvironmentalGrammar(
        token=re.compile(r"pid=\d+", re.IGNORECASE),
        varies_in="digits",
        key=None,  # `pid=` is in the token
        witness_kind="protocol",
        witness=r"# evidence-volatile: pid=\d+",
        witness_pair=("", "pid=1234", "pid=5678"),
        falsifier=("", "1234", "5678"),
        note=(
            "the key is part of the grammar. `exit=0` against `exit=1` is an "
            "assignment too and is REFUSED — what makes a pid environmental "
            "is that it is a pid, not that it has an `=` in it. This entry is "
            "the principle the path family was rebuilt on (D-156)."
        ),
    ),
    "iso_timestamp": _EnvironmentalGrammar(
        token=re.compile(
            r"20\d{2}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?"
            r"(?:Z|[+-]\d{2}:?\d{2})?)?"
        ),
        varies_in="digits",
        key=None,  # the ISO shape is in the token
        witness_kind="protocol",
        witness=r"# evidence-volatile: 20\d{2}-\d{2}-\d{2}T",
        witness_pair=("", "2026-08-14T10:23:45", "2026-09-01T04:11:02"),
        falsifier=("", "20260814", "20260901"),  # the separators are the identity
        note=(
            "wall-clock date, with or without a time. The date alone is "
            "admitted because the corpus's own decoy shape is `20\\d{2}-"
            "\\d{2}-\\d{2}`. A date against a date-and-time has different "
            "digit skeletons and is REFUSED — the shape is the identity."
        ),
    ),
}  # 8 grammars


def _digit_skeleton(token: str) -> str:
    """``token`` with every run of digits removed.

    The one primitive both directions of ``varies_in`` are measured through, so
    the two halves of the partition cannot drift apart: a digits-varying field
    must leave this UNCHANGED across the two sides, a text-varying field must
    leave it CHANGED.
    """
    return _DIGIT_RUN_RE.sub("", token)


def _environmental_field(
    token_a: str,
    token_b: str,
    key_context_a: str = "",
    key_context_b: str = "",
) -> tuple[str | None, str]:
    """Classify a disagreeing token pair against the grammar allowlist.

    Returns ``(grammar_name, note)``. A non-None name means the environment is
    allowed to have varied this token and the redaction may erase it. A None
    name means REFUSE, and the note says what could not be classified — the
    whole registry is walked before giving up, so the note reports the most
    specific reason found rather than the first grammar tried.

    Both sides must match the SAME grammar. A token pair that changes shape —
    a duration on one side, a path on the other — is not one field varying.

    ``key_context_a`` / ``key_context_b`` are the text of the erased span
    PRECEDING this token on each side, whitespace-normalised. A grammar whose
    ``key`` is not None is only reachable when that key matches BOTH contexts:
    a path is environmental because it is a rootdir or an interpreter path, not
    because it has a `/` in it (D-156). Defaulting both to the empty string is
    deliberate — a caller that has no context to offer gets the UNKEYED
    registry, which is the refusing direction.
    """
    closest = ""
    for name, grammar in _ENVIRONMENTAL_GRAMMARS.items():
        if not (
            grammar.token.fullmatch(token_a) and grammar.token.fullmatch(token_b)
        ):
            continue
        if grammar.key is not None and not (
            grammar.key.search(key_context_a) and grammar.key.search(key_context_b)
        ):
            # Right shape, wrong field — or no field at all. Reported rather
            # than skipped so a keyed grammar that ALMOST matched says why.
            closest = (
                f"both sides are {name!r}-shaped, but this field is identified "
                f"by {grammar.key.pattern!r} beside the token and the erased "
                f"span reads {key_context_a!r} / {key_context_b!r} there. What "
                f"makes a path environmental is that it is a rootdir or an "
                f"interpreter path, not that it starts with a slash"
            )
            continue
        if grammar.varies_in not in _KNOWN_VARIATION_SITES:
            # An unrecognised member of the one axis this rule turns on. Report
            # it; never fall through to admitting the token.
            closest = (
                f"both sides match the {name!r} grammar, but it declares "
                f"varies_in={grammar.varies_in!r}, which is not one of "
                f"{', '.join(sorted(_KNOWN_VARIATION_SITES))}. A grammar whose "
                f"direction of variation cannot be read cannot license an "
                f"erasure"
            )
            continue
        confined_to_digits = _digit_skeleton(token_a) == _digit_skeleton(token_b)
        if confined_to_digits == (grammar.varies_in == "digits"):
            return name, ""
        if grammar.varies_in == "digits":
            closest = (
                f"both sides are {name!r}-shaped, but they differ outside "
                f"their digits ({_digit_skeleton(token_a)!r} against "
                f"{_digit_skeleton(token_b)!r}) — the environment varies the "
                f"NUMBER in this field, not the text around it"
            )
        else:
            closest = (
                f"both sides are {name!r}-shaped, but strip the digits and "
                f"they are the same string ({_digit_skeleton(token_a)!r}) — "
                f"the only thing that moved is a COUNT, and a count is what "
                f"the command reported, not where it ran"
            )
    return None, closest


def _field_disagreement_problem(
    pattern: str, label_a: str, span_a: str, label_b: str, span_b: str
) -> str | None:
    """Named reason this erased span hid a CLAIM rather than a FIELD.

    ``span_a`` and ``span_b`` are the same pattern's match on the two real
    texts, and they differ — so the redaction is about to make two texts that
    disagree here compare as equal. Returns None when EVERY disagreeing token
    is a member of the environmental allowlist, else the house refusal sentence
    naming the pattern, both sides' text, and what to do about it.

    Refusal is the default (D-143). Every differing token is walked, not just
    the first, so the refusal reports the whole disagreement rather than the
    one token that happened to be leftmost.

    Each token is classified WITH the text that precedes it in its own span,
    which is where a keyed field's identifier lives (D-156): the span
    ``rootdir: /x/y`` offers ``rootdir:`` as the context for its second token,
    and a span that is a bare path offers nothing, which is the refusing
    direction. The contexts are rebuilt from the same ``split()`` the token
    walk uses, so the text a grammar keys on is exactly the text that was
    erased beside the token.
    """
    tokens_a, tokens_b = span_a.split(), span_b.split()
    verdict: str | None = None
    if len(tokens_a) != len(tokens_b):
        verdict = (
            f"the {label_a} has {len(tokens_a)} words there and the "
            f"{label_b} has {len(tokens_b)}, so the redaction is not hiding a "
            f"field whose value moved — it is hiding text that one side "
            f"reported and the other did not"
        )
    else:
        unclassified: list[str] = []
        for index, (token_a, token_b) in enumerate(zip(tokens_a, tokens_b)):
            if token_a == token_b:
                continue
            grammar, note = _environmental_field(
                token_a,
                token_b,
                " ".join(tokens_a[:index]),
                " ".join(tokens_b[:index]),
            )
            if grammar is not None:
                continue
            unclassified.append(
                f"{token_a!r} in the {label_a} is {token_b!r} in the "
                f"{label_b}"
                + (f" — {note}" if note else "")
            )
        if unclassified:
            verdict = (
                "; ".join(unclassified)
                + ". A volatile field is one of "
                + ", ".join(sorted(_ENVIRONMENTAL_GRAMMARS))
                + "; anything else that differs is what the command REPORTED"
            )
    if verdict is None:
        return None
    return (
        f"the declared volatile pattern {pattern!r} erased a span where the "
        f"{label_a} and the {label_b} DISAGREE, and the disagreement is not "
        f"field-shaped: {verdict}. The {label_a} says {span_a!r}; the "
        f"{label_b} says {span_b!r}. Applied to both sides that span cancels "
        f"out of the comparison, so the gate would report a byte-match "
        f"between two texts that plainly say different things. A volatile "
        f"pattern removes a value the ENVIRONMENT varies — a duration whose "
        f"digits moved, a path that relocated, a pid, a timestamp. Narrow the "
        f"pattern to that value, and the comparison will surface the "
        f"disagreement instead of swallowing it."
    )


def _erased_disagreement_problem(
    sides: dict[str, str], volatile_patterns: list[str]
) -> str | None:
    """Named reason the composed redaction reconciled a disagreement (D-135).

    Called only once the redacted texts have been found EQUAL, which is the
    only state in which a forgery is being accepted. Walks the declared
    patterns in the composed order ``_apply_volatile_redaction`` applies them,
    enumerating each pattern's matches on the running text of every side, so
    the spans it inspects are byte-for-byte the spans the substituter removed
    — including at the second rung of the placeholder ladder, where a level-1
    pattern matches text an earlier pattern created.

    ``sides`` is the SAME mapping ``_compare_byte_match`` redacts and compares,
    not a re-derivation of it: a side cannot be compared without also being
    walked here, because there is no second list of sides to forget to extend.
    A roster that is not a pair is reported rather than silently truncated to
    its first two members.
    """
    if len(sides) != 2:
        return (
            f"the comparison carries {len(sides)} sides "
            f"({', '.join(sorted(sides))}), and this guard aligns a PAIR. "
            f"A side that is redacted and compared without being checked for "
            f"erased disagreements is the hole D-135 was filed on. Extend the "
            f"alignment before adding the side."
        )
    (label_a, text_a), (label_b, text_b) = sides.items()
    if text_a == text_b:
        return None  # nothing was reconciled; the redaction changed no verdict
    running_a, running_b = text_a, text_b
    for pattern in volatile_patterns:
        replacement = (
            TIMING_PLACEHOLDER if VOLATILE_PLACEHOLDER in pattern
            else VOLATILE_PLACEHOLDER
        )
        try:
            spans_a = [m.group(0) for m in re.finditer(pattern, running_a)]
            spans_b = [m.group(0) for m in re.finditer(pattern, running_b)]
        except re.error:
            # Compilation is _apply_volatile_redaction's error to report, with
            # its own message and its own offending pattern.
            return None
        if len(spans_a) != len(spans_b):
            return (
                f"the declared volatile pattern {pattern!r} matches "
                f"{len(spans_a)} time(s) in the {label_a} and {len(spans_b)} "
                f"time(s) in the {label_b}, so its removed spans cannot be "
                f"aligned and what it erased cannot be shown to be a varying "
                f"field. A pattern that fires a different number of times on "
                f"the two texts is describing their difference, not a field "
                f"they share. Narrow it until it matches the same field on "
                f"both sides."
            )
        for span_a, span_b in zip(spans_a, spans_b):
            if span_a == span_b:
                continue  # erased the same text on both sides; nothing hidden
            problem = _field_disagreement_problem(
                pattern, label_a, span_a, label_b, span_b
            )
            if problem is not None:
                return problem
        running_a = re.sub(pattern, replacement, running_a)
        running_b = re.sub(pattern, replacement, running_b)
    return None


# ---------------------------------------------------------------------------
# Worktree + subprocess primitives are factored to ``worktree_helpers.py``
# (Phase 7 / Plan 07-03 — RESEARCH.md Open Question 1 recommendation).
#
# ``_run_command_with_timeout``, ``_setup_worktree``, ``_teardown_worktree``,
# ``_prune_orphaned_worktrees`` and ``_PRUNE_DONE_FOR`` are imported at
# module-top so identity is preserved across the import boundary — Phase 4/5
# callers and Phase 7 callers share the same once-per-session prune guard.
#
# ``_WORKTREE_LOCK`` is NOT imported here (casting 3's unused-import pin, under
# the D-203 structural packet). It is taken inside ``_setup_worktree``, which
# is where the `.git/config.lock` race it exists for lives; nothing in this
# module ever took it, so the name only ever LOOKED like this module shared
# the lock. It does share it — through the helper that holds it — and the
# import was the misleading half.
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Byte-match comparator with declared-volatile redaction + capped diff
# (Plan 04-03).
#
# Closed escape-hatch: ONLY declared volatility tolerated. The redaction
# runs on BOTH committed log and re-execution capture in the same declared
# order, then byte-compares. Any divergence is a failure.
#
# What CLOSES the hatch is a LADDER of two gates (D-126, then D-135), not the
# per-pattern canary probe that preceded them. Symmetric redaction is the
# mechanism's strength and its one weakness: patterns applied identically to
# both sides cancel out of the comparison, so a declaration broad enough to
# erase both sides makes every log match every capture.
#
#   1. The residue floor (D-126) measures what the whole declared set left of
#      each real side, so a declaration cannot erase a log wholesale.
#   2. The disagreement guard (D-135) measures whether what survived still
#      DISCRIMINATES, so a declaration cannot erase the ONE LINE the two texts
#      disagree on and buy a byte-match with the other 99.4% intact.
#
# Volume was never the property that mattered; it was only the property the
# first fix could see. Together they are what stops "declared volatility" from
# quietly widening into "declared evidence".
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
        ValueError prefixed ``EVIDENCE_VOLATILE_MALFORMED`` when a pattern
        fails to compile (propagated from ``_apply_volatile_redaction``), when
        the composed redaction annihilates either side
        (``_composed_redaction_problem`` — D-126), or when the redaction bought
        its byte-match by erasing a span in which the two real texts disagree
        about something no field varies (``_erased_disagreement_problem`` —
        D-135).

    On mismatch the diff is unified-format via ``difflib.unified_diff``,
    capped at ``_DIFF_CAP_LINES`` lines; if truncated, a "... (N more
    diff lines truncated)" sentinel is appended.
    """
    # D-126. The two sides of the comparison are enumerated ONCE, here, and
    # both loops below walk that one mapping. That is what makes the residue
    # guard total rather than remembered: a side cannot be redacted and
    # compared without also being guarded, because there is no second list of
    # sides to forget to extend. The guard runs BEFORE the equality test, so a
    # redaction that collapsed both sides to the same erased string is refused
    # rather than reported as a match.
    sides: dict[str, str] = {
        "committed log": committed,
        "re-execution capture": captured,
    }
    redacted: dict[str, str] = {
        label: _apply_volatile_redaction(text, volatile_patterns)
        for label, text in sides.items()
    }
    if volatile_patterns:
        # No declared patterns means the redaction is the identity function,
        # and an empty body is then the command's own doing. Guarding it would
        # refuse a correctly-silent command for a declaration it never made.
        for label, text in sides.items():
            problem = _composed_redaction_problem(label, text, redacted[label])
            if problem is not None:
                raise ValueError(f"EVIDENCE_VOLATILE_MALFORMED: {problem}")

    rc = redacted["committed log"]
    rcc = redacted["re-execution capture"]
    if rc == rcc:
        # D-135. Equality is the only state in which a forgery gets accepted,
        # so this is where the second rung goes. The residue floor above has
        # already established that ENOUGH of each side survived; this asks
        # whether what did survive still DISCRIMINATES — i.e. whether the
        # redaction bought this equality by erasing a span in which the two
        # real texts disagree about something a field never varies. It walks
        # the same `sides` mapping the redaction above walked, so the guard
        # cannot be extended to a new side by accident.
        if volatile_patterns:
            problem = _erased_disagreement_problem(sides, volatile_patterns)
            if problem is not None:
                raise ValueError(f"EVIDENCE_VOLATILE_MALFORMED: {problem}")
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
    """Drop the maximal leading run of ``#`` and blank lines, keeping the rest.

    Unlike ``_strip_header_and_blank_lines`` this preserves the body verbatim
    — interior blank lines and all — because the byte comparator's whole job
    is an exact match on what the command emitted.

    THIS IS A RUN FINDER, NOT A HEADER FINDER (D-200). The run it drops is a
    SUPERSET of the header: ``_EVIDENCE_HEADER_BLOCK_RE`` alternates ``#``
    lines and blank lines in one pass, so it does not stop at the header/body
    blank separator and keeps eating any further leading ``#`` or blank lines
    past it. That is harmless on a committed log whose body starts with real
    output, and it is why this remains the right helper for the test harnesses
    that build a replay file from a synthetic log. It is NOT sound to apply
    independently to the two sides of a comparison — see
    ``_split_committed_header`` for what the comparator uses and why.
    """
    return _EVIDENCE_HEADER_BLOCK_RE.sub("", text, count=1)


# D-205 — THE HEADER GRAMMAR, DEFINED ONCE, AND THE ONLY TEXT EVER DISCARDED.
#
# THE STRUCTURAL PRINCIPLE (D-197, D-201, D-205 are one class).
# ------------------------------------------------------------
# Three cycles running, a guard here asked a NARROWER question than the harm
# it names, and the gap between the two questions was the whole defect:
# D-197/D-201 asked "is this suffix in a table" where the harm was "does a
# reader open this name"; D-200 asked "is the strip symmetric" where the harm
# was "did the command emit this"; D-205 asked "is the capture's run a suffix"
# where the harm is "is every line I am about to discard actually header".
# The principle all three violate, and the one pinned here:
#
#     A GUARD THAT DISCARDS, SKIPS OR EXEMPTS INPUT MUST PROVE EACH DISCARDED
#     UNIT AGAINST THE RULE THAT NAMES THE EXEMPTION. ANYTHING NOT PROVEN IS
#     SUBJECT TO THE CHECK.
#
# Concretely, for the byte comparator: the accept branch must prove every
# discarded LINE against the directive grammar, and anything not proven is
# COMPARED. That is why there is no longer a helper in this module that
# returns the wide leading ``#``/blank run for the comparator to reason about
# — a superset computed first and narrowed by a second rule is exactly the
# shape that failed three times, because the next author reaches for the
# superset and forgets the narrowing.
#
# WHAT THE WRITER'S GRAMMAR ACCOUNTS FOR (the lead's binding ruling, D-205).
# -------------------------------------------------------------------------
# The committed leading ``#`` run is INSIDE the byte-identical guarantee. The
# only text outside it is the header the writers emit BY GRAMMAR: a contiguous
# leading run of ``# evidence-<directive>:`` lines whose directive is one the
# parser actually honours, plus the ONE blank separator that follows them.
# Every other line, ``#``-prefixed or not, is body and must match the capture
# byte for byte — so a claim the command never emitted can never be made
# reproducible by prefixing it with ``#``.
#
# Driven at the wire at cb77e83, before this change, through
# ``server.request_handlers[CallToolRequest]`` — three runs identical but for
# the committed log's body: an honest log accepted; the SAME log with
# ``# FABRICATED: all 47 assertions passed on a clean tree`` inserted between
# the directives and the body ALSO accepted, ``mismatches: []``, cycle counter
# advanced; the identical claim without the leading ``#`` correctly REFUSED.
# B and C differed by one character, because the accept branch returned the
# whole committed ``#``/blank run as "header" and never tested a line of it
# against the writer's grammar — the one ``_provable_header_lines`` applies,
# over the closed directive set ``_KNOWN_HEADER_DIRECTIVES``.
#
# WHY THE KNOWN-DIRECTIVE SET AND NOT ANY ``# evidence-*:`` SHAPE.
# ---------------------------------------------------------------
# ``_parse_evidence_header`` silently IGNORES a directive it does not know, so
# an unknown ``# evidence-<word>:`` line is not something a writer emits by
# grammar — it is unread text, and a fabricator who spells the claim
# ``# evidence-summary: all 47 assertions passed`` would get it discarded
# unread all over again, one notch narrower. So the grammar is the CLOSED set
# the parser honours (``_KNOWN_HEADER_DIRECTIVES``). This fails CLOSED: a
# future phase that starts writing a new directive into logs before adding it
# to that set gets a loud refusal naming the log, not a silent discard. That
# coupling is the point — it is what keeps "text this module does not read is
# body" true by construction.
_EVIDENCE_DIRECTIVE_LINE_RE = re.compile(
    r"^[ \t]*#[ \t]*evidence-([a-z][a-z0-9-]*)[ \t]*:"
)


def _is_directive_line(line: str) -> bool:
    """True when the WRITER'S grammar accounts for this line as a directive."""
    match = _EVIDENCE_DIRECTIVE_LINE_RE.match(line)
    return match is not None and match.group(1) in _KNOWN_HEADER_DIRECTIVES


def _provable_header_lines(text: str) -> list[str]:
    """The header, and nothing else: known directives + one blank separator.

    Lines keep their newlines, so a caller can ``"".join`` them back into the
    exact prefix they occupy. The blank separator is consumed only when at
    least one directive preceded it — which is also what makes the suffix test
    in ``_split_committed_header`` line-aligned by construction, since a bare
    ``"\\n"`` can never be a provable header on its own and so can never
    "match" the tail of a directive line's newline.
    """
    lines = text.splitlines(keepends=True)
    i = 0
    while i < len(lines) and _is_directive_line(lines[i]):
        i += 1
    if i and i < len(lines) and not lines[i].strip():
        i += 1  # the single blank separator every writer emits
    return lines[:i]


def _split_committed_header(log_text: str, captured: str) -> str:
    """Return the committed log's header — the provable prefix the command did NOT emit.

    WHY THE TWO SIDES ARE DECIDED TOGETHER (FR-010 / D-200 / D-205)
    ---------------------------------------------------------------
    Both comparator call sites used to run ``_strip_leading_header_block``
    independently on each side and hand the two results to
    ``_compare_byte_match``. The comment above them asserted the regex "only
    matches a LEADING run of ``#`` comment and blank lines, so it cannot eat
    content" — false the moment the CONTENT starts with ``#``, which is
    exactly the shape D-198 had just established is legitimate. Driven live at
    f5b487b: a committed log of ``<directives> + "# FABRICATED..." + blank +
    "REAL_TAIL"`` and a capture of ``"# a completely different comment" +
    blank + "REAL_TAIL"`` both reduced to ``"REAL_TAIL\\n"`` and the comparator
    returned matched=True. Driven one door further, ``Foundry-Phase
    (inspect_start)`` on a committed log whose three captured ``#`` lines
    disagree with HEAD returned no error, no mismatches, and advanced the
    cycle counter — so a fabricated leading comment block passed both
    ``Foundry-Accept-Casting`` (FR-010) and the boundary sweep (GI-002 /
    ST-005). D-198 was the false-REFUSAL half of the same helper family; this
    is the false-ACCEPT half.

    THE STRIP IS ONE DECISION OVER BOTH SIDES, WHICH IS WHY IT LIVES HERE.
    The sound definition of the header is not a shape at all: it is the part
    of the committed file the re-execution did not produce. So the captured
    side is NEVER stripped, and the committed side loses exactly the prefix
    the capture did not emit.

    D-205 NARROWS WHAT "THE PREFIX" MAY EVEN CONTAIN. D-200 fixed the SYMMETRY
    of that decision and left its GRAMMAR wide: the run it discarded was the
    whole leading ``#``/blank run, never tested line by line against the
    directive grammar, so a committed-only ``# FABRICATED: ...`` line inside
    that run was discarded unread and the forgery ACCEPTED at both doors (see
    the module comment above ``_provable_header_lines`` for the wire runs).
    The candidate set is now the PROVABLE HEADER — known directives plus the
    one blank separator — so every line this function returns has been proven
    header, and every line it does not return is compared. The four cases:

      * capture emits no provable header (every log whose command output does
        not begin with a ``# evidence-<known>:`` line — the whole shipped
        corpus): the header is the committed provable header. Writer prose in
        the committed leading run is NOT in it, so it is compared, which is
        the D-205 refusal.
      * capture's provable header EQUALS the committed one (the
        ``use_cat_replay`` harness, whose replay file holds the FULL rewritten
        evidence so both sides carry the directives as output): the header is
        empty and the two full texts are compared. Still a match, for the
        right reason.
      * capture's provable header is a proper suffix of the committed one (a
        replay that emits from the second directive on): the header is the
        committed one minus those lines, so the directives the capture really
        emitted are compared on both sides instead of vanishing from both.
      * capture's provable header is NOT a suffix: the two sides disagree
        about their directives. Discard the committed provable header — the
        most that could ever be header — and let ``_compare_byte_match``
        refuse with a diff naming the first differing line.

    A committed body that legitimately BEGINS with ``#`` lines — D-198's own
    subject, a command that shows the source it changed — is untouched by all
    of this: those lines are not directives, so they are never candidates for
    the strip, and they are compared on both sides and agree.

    Line alignment is load-bearing: a raw ``str.endswith`` would accept a
    capture whose run is ``"\\n"`` against a committed run of
    ``"# evidence-cmd: X\\n"``, splitting mid-line and silently donating the
    directive's own newline to the body. The comparison is over lines, so the
    suffix test is over lines — and ``_provable_header_lines`` cannot return a
    lone blank line, so the mid-line split has no shape left to take.
    """
    committed_header = _provable_header_lines(log_text)
    captured_header = _provable_header_lines(captured)
    n = len(captured_header)
    if n and n <= len(committed_header) and committed_header[len(committed_header) - n :] == captured_header:
        return "".join(committed_header[: len(committed_header) - n])
    return "".join(committed_header)


def _header_stripped_pair(log_text: str, captured: str) -> tuple[str, str]:
    """``(committed_body, captured_body)`` for the byte comparator.

    The single entry point both comparator call sites use, so the two halves
    of the strip cannot drift apart again (D-200), and the ONE place the
    header grammar is consulted, so the accept branch cannot go wide again
    (D-205). The captured side is returned verbatim: a re-execution capture is
    all output.
    """
    header = _split_committed_header(log_text, captured)
    return log_text[len(header) :], captured


def _strip_header_and_blank_lines(text: str) -> list[str]:
    """Return body lines: the leading header block and blanks dropped.

    "Header block" is the CONTIGUOUS leading run of ``#`` lines — the
    ``# evidence-*:`` directives plus whatever prose the writer wrote under
    them — terminated by the first line that is not one, which is the blank
    separator every writer in this plugin emits between header and body. A
    ``#`` line BELOW that separator is captured output and survives.

    WHY THE HEADER IS A LEADING RUN AND NOT "EVERY # LINE" (FR-010 / D-198)
    ----------------------------------------------------------------------
    This dropped every line whose lstrip started with ``#``, anywhere in the
    file, and its two consumers then judged a body that was missing the
    captured lines. Driven end to end at the MCP door on two runs identical
    but for three characters: a teammate's log whose command was
    ``sed -n '1,3p' src/guard.py && printf '<three stamps>'``, where those
    three source lines are comments in this codebase's own house style, is a
    six-line body of which three are timestamps (50%) — but read as a
    three-line body of which three are timestamps (100%), because the strip
    deleted the denominator. ``Foundry-Accept-Casting`` refused it as
    EVIDENCE_STUB_TIMESTAMP_CLUSTER; the same capture with the leading ``# ``
    removed was accepted. The bare-ack arm failed the same way: nine comment
    lines then ``PASS`` fullmatched ``_STUB_BARE_ACK_RE`` on a body that had
    been reduced to the word ``PASS``.

    So the discriminator was punctuation, not fabrication, and FR-010's
    converse went false: evidence that ran, reproduces byte-identically and
    is honest was refused. Of the 70 logs this run has committed, 15 already
    have a majority-``#`` body under the old strip. Whole-file ``#`` deletion
    was rejected for exactly that reason; the leading run is what the header
    grammar (``_EVIDENCE_HEADER_BLOCK_RE``, ``_parse_evidence_header``) has
    always meant by "header block".

    Narrowing costs the detector nothing it was catching: a genuine bare-ack
    stub (header, then ``PASS``) and a genuine timestamp cluster (header,
    then only timestamps) have no ``#`` line below the separator to restore,
    so both still fire. ``tests/test_evidence.py`` pins both true positives
    beside the two false negatives this closes.
    """
    lines = text.splitlines()
    start = 0
    while start < len(lines) and lines[start].lstrip().startswith("#"):
        start += 1
    return [ln for ln in lines[start:] if ln.strip()]


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

    D-198: "non-header" means what ``_strip_header_and_blank_lines`` now
    implements — the CONTIGUOUS leading ``#`` block — and no longer every
    ``#`` line in the file. This sentence was already written this way while
    the call under it deleted comment lines out of the captured body, which
    is what inflated the ratio: deleting a line that is not a timestamp
    shrinks the denominator and can only push the fraction up, never down.
    A capture that is half comments and half timestamps read as 100%.

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
    # D-200 CORRECTS THIS COMMENT. It used to say the strip was applied
    # SYMMETRICALLY and that "the regex only matches a LEADING run of `#`
    # comment and blank lines, so it cannot eat content" — false whenever the
    # CONTENT starts with `#`, and a symmetric strip is precisely what let two
    # differing leading comment blocks reduce to the same bytes and ACCEPT.
    # `_header_stripped_pair` decides the strip once, over both sides: the
    # capture is never stripped, and the committed side loses exactly the
    # prefix the capture did not emit. Both conventions still work — a real
    # evidence file (committed header+body vs captured body) matches on the
    # body, and the `use_cat_replay` harness (whose replay file deliberately
    # holds the full rewritten evidence, so BOTH sides carry the header) lands
    # on the equal-runs case and compares the two full texts.
    #
    # D-205 NARROWS WHAT MAY BE STRIPPED. D-200 left the accept branch's
    # GRAMMAR wide — it discarded the whole leading `#`/blank run without
    # testing a line of it — so a committed-only `# FABRICATED: ...` line was
    # discarded unread and this door returned `accepted`. The candidate set is
    # now the provable header alone (known `# evidence-<directive>:` lines plus
    # one blank separator); anything else in that run is body and is compared.
    committed_body, captured_body = _header_stripped_pair(log_text, captured)
    try:
        matched, diff, redacted_log, redacted_captured = _compare_byte_match(
            committed=committed_body,
            captured=captured_body,
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
    manifest, problem = read_document(manifest_path)
    if problem is not None:
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
    manifest, problem = read_document(manifest_path)
    if problem is not None:
        return
    # D-134. ``read_document`` establishes that the manifest is a MAPPING; it
    # says nothing about what is inside ``castings``. A manifest whose
    # ``castings`` is ``"nope"`` or ``[1, 2, 3]`` or ``[None]`` reads back
    # cleanly and then meets ``c.get("id")`` below, which is an AttributeError
    # raised out of the evidence gate — a traceback naming no file, from a path
    # whose whole error contract is a named refusal (NFR-002).
    #
    # The shape is established through the SHARED validator rather than a
    # private isinstance chain, because the point of D-134 is that all six
    # readers of this document decide on one policy: a rung declared in
    # ``_MANIFEST_SHAPE`` is covered here the day it is declared, where a
    # hand-written check at the rung a defect was reported on covers exactly
    # that rung forever. ``_manifest_shape_problem`` is the TOLERANT half of
    # the pair, which is the right half here: this is housekeeping on the way
    # out, and its failure must no more decide the verdict than the orphan
    # prune's does, so an unusable manifest is a silent no-op — the same
    # discipline ``_append_to_manifest_stream_skips`` already documents for a
    # missing file.
    if _manifest_shape_problem(manifest) is not None:
        return
    castings = manifest.setdefault("castings", [])
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


# ---------------------------------------------------------------------------
# GI-002 / ST-005 / CT-007 — the GRIND-boundary evidence sweep.
#
# WHY THIS IS A NEW CALLER AND NOT A NEW ENGINE
# ---------------------------------------------
# `verify_evidence` above already knows how to re-execute a committed log in an
# isolated checkout and decide whether the bytes still match: the redaction
# ladder, the D-126 residue floor and the D-135 disagreement guard are all
# reached through `_compare_byte_match`, and every one of them exists because a
# specific forgery got past the shape that preceded it. A sweep that re-decided
# any of that would be a SECOND opinion about what a byte-match is, and the two
# would drift the first time one of them was hardened — which is the exact
# failure class `schemas/vocab.py` exists to make unrepresentable one layer up.
#
# So the sweep reuses the ladder verbatim and owns only what is genuinely new:
#
#   * WHICH logs run (`select_sweep_scope` — delta by default, whole corpus
#     when the FULL rule fired or the run is about to reach ASSAY, NYQUIST or
#     DONE), and
#   * WHERE they run (ONE detached worktree at HEAD of the shared tree, rather
#     than per-casting worktrees at each casting's own commit).
#
# The second difference is the load-bearing one. Acceptance asks "did casting N
# tell the truth at its own commit"; the sweep asks "does the whole committed
# corpus still reproduce on the tree the run is about to INSPECT". A GRIND cycle
# that quietly broke casting 3's evidence while fixing casting 7's defect is
# invisible to the first question and is precisely what the second one catches.
#
# WHY THE SWEEP DOES NOT RE-RUN THE STUB LIBRARY
# ----------------------------------------------
# `_check_stub_patterns` is acceptance's step 6 and is deliberately NOT reached
# here. It judges whether a COMMITTED LOG is a fabrication — too small, replayed
# by a vacuous command, a bare `PASS`, a timestamp cluster — and that judgment
# is a property of the log's own bytes, which have not changed since the
# casting commit where acceptance already made it and passed it. Re-making it
# at every GRIND boundary could only ever produce the same answer, and the one
# case where it would NOT is the case where it is wrong: a log whose acceptance
# passed being refused at cycle 9 for a reason that was true at cycle 1.
#
# What the sweep adds over acceptance is the TREE, not the log. Acceptance
# asked whether the bytes reproduced at the casting's commit; the sweep asks
# whether they still reproduce on the tree the next INSPECT will read.
#
# Neither function writes anything: no manifest append, no provenance record,
# no state mutation. The refusal, the cycle counter and the `evidence_sweep`
# roll-up all belong to the `inspect_start` transition that calls these.
# ---------------------------------------------------------------------------

#: The worktree directory prefix for a sweep, passed to `_setup_worktree`'s
#: `dir_prefix`. Distinct from the Phase 4 `casting-` default so a sweep in
#: flight and an acceptance in flight for casting N can never derive the same
#: path — the D-111 claim would step one of them to a suffix, which is safe but
#: costs a directory; a distinct prefix means the collision never arises.
SWEEP_WORKTREE_PREFIX: str = "sweep-"

# FR-031 / NFR-004 — the parallel pool ceiling, DERIVED, not decreed.
#
# Measured from the corpus this sweep actually runs. Every `# evidence-cmd:`
# committed to `evidence/` in this repo is one of: a `uv run ... pytest`
# invocation, a `python3 scripts/*.py` run, or a `grep`/`awk` pipeline. The
# first kind dominates (9 of the 13 logs standing when this was written) and
# each is a SINGLE CPU-bound interpreter process, so useful parallelism is
# bounded by cores, not by I/O — past that point the workers only contend.
#
# The ceiling is 8 rather than "all cores" for two corpus-derived reasons.
# First, each `uv run --with pytest` materialises its own environment and reads
# the shared uv cache, so the peak is eight simultaneous interpreters plus
# their imports, not eight bare `grep`s. Second, the sweep runs INSIDE the
# `inspect_start` transition on the lead's own machine while nothing else is
# scheduled to run, and leaving half a typical 16-core box free is what keeps
# NFR-004 ("well inside the wall time of the INSPECT it precedes") true without
# making the lead's session unresponsive for the duration.
#
# At the observed run scale (A-AUTO-002: ~85 agent spawns, so a corpus around
# 40-50 logs by DONE) a full sweep is therefore ~6 waves of 8. A DELTA sweep is
# usually one wave or none at all, which is the point of DELTA.
SWEEP_POOL_CEILING: int = 8


def _sweep_relative_log_name(log: Path, project_root: Path) -> str:
    """The repo-relative spelling of a log, for the result and the refusal.

    A mismatch record names the log the lead has to go and look at, so it is
    reported the way the lead types it: `evidence/casting-3-login.log`, never
    an absolute path through someone's tmpdir and never a bare basename that
    two directories could both claim. Falls back to the absolute path when the
    log genuinely sits outside the tree, which is a caller error worth seeing
    rather than hiding behind a prettier string.
    """
    try:
        return str(log.resolve().relative_to(project_root.resolve()))
    except (ValueError, OSError):
        return str(log)


def _sweep_requirement_to_castings(manifest: dict) -> dict[str, set[str]]:
    """Map each requirement ID to the casting ids that DECLARE it.

    This is the SECOND source for keying a log to a casting, and it exists
    because the first one is a filename convention. `# evidence-for:` names
    REQUIREMENTS, not castings, so resolving it needs the manifest: a casting's
    `spec_text` is the verbatim `<spec_requirements>` block its prompt carried,
    and the IDs it DECLARES there are the IDs that casting is answerable for.

    Both sources are used, unioned, because either alone loses logs. A log
    named off-convention has no filename key; a log with no `# evidence-for:`
    header has no requirement key. A log with neither is not silently dropped —
    it simply falls through to the command-reference test in
    `select_sweep_scope`, and is back in scope the moment `full=True`.

    DECLARED, NOT MENTIONED (D-184 / D-187)
    ---------------------------------------
    The sentence above used to end "and the IDs IN it are exactly the IDs that
    casting is answerable for", and this loop was a bare
    `_REQUIREMENT_ID_RE.findall(spec_text)` over the whole block. That claim is
    false and D-180 disproved it at two other doors — the acceptance gate and
    the F0.9 validator — by replacing the same `findall` with
    `declared_requirement_ids`, which judges POSITION: an ID is declared when it
    is the subject of its own line, and merely quoted when it appears inside
    another requirement's prose. This reader was the third and was not migrated
    with them, so one rule kept two owners and a survivor.

    Driven on this run's manifest at HEAD 0e09b40: `['NFR-002']` resolved to
    castings `{'5', '2'}` — casting 5 declares it, casting 2 merely quotes
    'NFR-002' once inside OT-005's statement text — so casting 5's own evidence
    became keyed to casting 2 and was re-executed on any DELTA cycle whose diff
    touched casting 2. Nine IDs were mis-attributed that way (GI-003, NFR-002,
    US-001..US-005, US-007, US-008; US-008 alone widened from {3,5} to
    {1,2,3,5,7,8}), and on the one-file diff PROVE drove the boundary selected
    44 of 62 logs where FR-009's rule selects 28. FR-009 defines the delta set
    as "casting key_files intersect the GRIND diff, plus any log whose command
    references a touched file" — a log keyed through a QUOTATION satisfies
    neither arm, so every one of those extra 16 was outside the rule.

    Widening is the cheap direction of that error and is why it survived: it
    costs seconds per cycle and refuses nothing. The expensive direction is the
    same mapping used in a boundary refusal, which would name a log the diff
    never touched (FR-042 / OT-008).

    The requirement GRAMMAR still comes from `_REQUIREMENT_ID_RE` — via
    `declared_requirement_ids`, which builds its position rule from
    `REQUIREMENT_ID_RE.pattern` rather than re-typing it (D-150). The header
    scan at the top of this module keeps the bare `findall`, and correctly: a
    `# evidence-for:` line is a comma-separated LIST of ids, not prose, so
    there is no subject position for the rule to judge.
    """
    mapping: dict[str, set[str]] = {}
    castings = manifest.get("castings")
    if not isinstance(castings, list):
        return mapping
    for entry in castings:
        if not isinstance(entry, dict):
            continue
        casting_id = entry.get("id")
        if casting_id is None:
            continue
        spec_text = entry.get("spec_text")
        if not isinstance(spec_text, str):
            continue
        for req_id in declared_requirement_ids(spec_text):
            mapping.setdefault(req_id, set()).add(str(casting_id))
    return mapping


def _sweep_touched_castings(manifest: dict, touched: list[str]) -> set[str]:
    """The casting ids whose `key_files` intersect the GRIND diff (GI-002).

    A `key_files` entry is either a file path or a DIRECTORY, spelled with a
    trailing slash — casting 5's own manifest entry carries
    `tests/fixtures/escalation/finer_boundary_run/`, and a diff touching a file
    inside it must count as touching that casting. Comparing the two as bare
    strings would miss every directory entry, so a trailing-slash entry is
    matched as a path PREFIX and everything else exactly.

    Paths are compared with forward slashes and no leading `./`, which is how
    both `git diff --name-only` and the manifest spell them.
    """
    def _norm(raw: object) -> str:
        text = str(raw).replace("\\", "/").strip()
        while text.startswith("./"):
            text = text[2:]
        return text

    touched_norm = {_norm(t) for t in touched if str(t).strip()}
    hits: set[str] = set()
    castings = manifest.get("castings")
    if not isinstance(castings, list):
        return hits
    for entry in castings:
        if not isinstance(entry, dict):
            continue
        casting_id = entry.get("id")
        key_files = entry.get("key_files")
        if casting_id is None or not isinstance(key_files, list):
            continue
        for key_file in key_files:
            key = _norm(key_file)
            if not key:
                continue
            if key.endswith("/"):
                if any(t.startswith(key) for t in touched_norm):
                    hits.add(str(casting_id))
                    break
            elif key in touched_norm:
                hits.add(str(casting_id))
                break
    return hits


def _sweep_command_references(cmd: str, touched: list[str]) -> bool:
    """Does this `# evidence-cmd:` reference a file the GRIND diff touched?

    GI-002's second delta arm. The test errs deliberately toward INCLUSION,
    and the asymmetry is the whole point: a log swept that need not have been
    costs seconds, while a log NOT swept that should have been is a broken
    evidence artifact carried silently past the boundary that exists to catch
    it. When the two errors are that unequal, the loose test is the correct
    one.

    Matching is on any trailing path-suffix of the touched file, down to the
    bare basename, because commands do not spell paths the way the diff does:
    the committed corpus is full of `cd plugins/foundry/mcp-server && ... pytest
    tests/test_vocab.py`, where the diff says
    `plugins/foundry/mcp-server/tests/test_vocab.py` and the command says
    `tests/test_vocab.py`. Requiring the full repo-relative spelling would
    match none of them.

    A suffix must begin at a path boundary in the command text — start of
    string, or a character that is not a path character — so `vocab.py` does
    not match `myvocab.py` and `evidence.py` does not match `test_evidence.py`.

    A LITERAL SUFFIX IS NOT THE ONLY WAY A COMMAND REACHES A FILE (D-030)
    ---------------------------------------------------------------------
    The literal test alone answered False for `pytest tests/`, for a bare
    `pytest`, and for `grep -r foo src/` — every one of which genuinely
    re-executes the changed surface. The delta arm therefore under-selected in
    exactly the shape the corpus is full of, and under-selection is the error
    this test is built to avoid: a log NOT swept that should have been is a
    broken artifact carried silently past the boundary.

    So a second arm reads the command's WALK ROOTS (`_sweep_walk_roots`): a
    recursive program's directory operand, or its working directory when it was
    given no operand at all. A touched file beneath a walk root is referenced.

    The two arms are deliberately different tests, and the difference is what
    keeps the boundary rule intact. `cd plugins/foundry/mcp-server && pytest
    tests/test_evidence.py` names its target exactly, so it does NOT walk
    `plugins/foundry/mcp-server` and does not reference every file under it —
    a `cd` sets the cwd, it does not make the shell read the tree. Only a
    walking program with no path operand promotes its cwd to a walk root.

    A SHELL GLOB IS A PATH OPERAND AND NEITHER ARM EXPANDED ONE (D-045)
    ------------------------------------------------------------------
    D-030 named "glob forms" among the shapes that "all resolve to nothing"
    and closed the directory-operand, bare-command and `grep -r` halves. The
    glob half stayed open, and it fails in BOTH arms at once: `cat src/*.py`
    reaches no walker so only the literal arm runs, and it searches the command
    text for `src/mod.py`, which a glob never spells; `ruff check src/*.py`
    DOES reach a walker, and the operand `src/*.py` is then compared as a
    literal path segment, so `src/mod.py` neither equals it nor starts with
    `src/*.py/`. Driven at the door: `cat src/*.py`, `pytest tests/*.py`,
    `grep foo src/**/*.py`, `wc -l evidence/*.log`, `uv run pytest
    tests/test_*.py` and `ruff check src/*.py` every one answered False.

    So a glob-shaped operand gets its own arm (`_sweep_glob_operands`), and it
    is the LITERAL ARM GENERALISED, not a new kind of test: where that arm asks
    "is this exact suffix present at a path boundary", this one asks "does this
    glob match this suffix". Both quantify over the same suffix ladder for the
    same reason — commands spell `tests/*.py` where the diff says
    `plugins/foundry/mcp-server/tests/test_vocab.py` — which is also why the
    glob arm needs NO cwd tracking: matching every suffix already subsumes what
    joining a `cd`-established cwd onto the pattern would buy, so re-deriving
    the cwd here would be a second derivation of a fact this arm never reads.

    A glob-shaped WALK ROOT is handled separately and more loosely, because a
    walker handed `tests/*` reads the SUBTREES the glob names: such a root
    matches the touched path or any directory above it. `cat *` therefore
    selects every touched file, which is what `cat *` honestly does; a quoted
    `grep '*' f.txt` over-selects and costs the seconds the asymmetry above
    says are the right ones to spend.
    """
    if not cmd:
        return False
    walk_roots = _sweep_walk_roots(cmd)
    literal_roots = [root for root in walk_roots if not _sweep_is_glob(root)]
    root_globs = _sweep_glob_matchers(
        [root for root in walk_roots if _sweep_is_glob(root)]
    )
    operand_globs = _sweep_glob_matchers(_sweep_glob_operands(cmd))
    for raw in touched:
        path = str(raw).replace("\\", "/").strip()
        while path.startswith("./"):
            path = path[2:]
        if not path:
            continue
        segments = [seg for seg in path.split("/") if seg]
        # Longest suffix first is only an ordering nicety — any hit is a hit.
        for start in range(len(segments)):
            suffix = "/".join(segments[start:])
            for index in _iter_substring_starts(cmd, suffix):
                before = cmd[index - 1] if index else ""
                if before not in _SWEEP_PATH_CHARS:
                    return True
            # The walk-root arm: is this touched path (or a suffix of it)
            # inside a directory the command walks? "" is the whole cwd, which
            # a walker with no operand reads in full.
            for root in literal_roots:
                if root == "" or suffix == root or suffix.startswith(root + "/"):
                    return True
            # The glob-operand arm: the literal arm generalised (D-045).
            for matcher in operand_globs:
                if matcher.fullmatch(suffix):
                    return True
            # A glob WALK ROOT names directories, so everything beneath a
            # matched one is walked: test the suffix and every path above it.
            for matcher in root_globs:
                if matcher.fullmatch(suffix):
                    return True
                for end in range(start + 1, len(segments)):
                    if matcher.fullmatch("/".join(segments[start:end])):
                        return True
    return False


#: The three shell-glob metacharacters. `{a,b}` brace expansion is deliberately
#: absent: it is a bash-ism rather than a glob, `shlex` does not treat it as one
#: either, and every form D-045 names is built from these three.
_SWEEP_GLOB_CHARS: frozenset[str] = frozenset("*?[")  # 3 metacharacters


def _sweep_is_glob(token: str) -> bool:
    """Does ``token`` carry a shell-glob metacharacter?"""
    return any(ch in _SWEEP_GLOB_CHARS for ch in token)


def _sweep_glob_to_regex(pattern: str) -> str:
    """Translate a shell glob into a regex source string. Never raises.

    NOT ``fnmatch.translate`` (D-045). ``fnmatch`` is a FILENAME matcher: its
    ``*`` crosses ``/`` freely, so ``src/*.py`` would match ``src/a/b/c.py``
    and, worse, ``tests/*`` would match every file at any depth — turning every
    DELTA scope containing one glob into a FULL one. The separator rules here
    are the shell's own:

      ``**/``  zero or more leading directories  -> ``(?:.*/)?``
      ``**``   crosses separators                -> ``.*``
      ``*``    within one segment                -> ``[^/]*``
      ``?``    one character, not a separator    -> ``[^/]``
      ``[..]`` a character class, ``!`` negating -> ``[..]`` / ``[^..]``

    An unterminated ``[`` is emitted as a literal bracket rather than treated
    as a class, which is what the shell does with it too, and means a command
    carrying a stray bracket degrades to a narrower match instead of an
    exception. Everything else is ``re.escape``d, so a `.` in `*.py` is a dot.
    """
    parts: list[str] = []
    index = 0
    length = len(pattern)
    while index < length:
        char = pattern[index]
        if char == "*":
            if pattern.startswith("**", index):
                index += 2
                if index < length and pattern[index] == "/":
                    index += 1
                    parts.append("(?:.*/)?")
                else:
                    parts.append(".*")
                continue
            parts.append("[^/]*")
            index += 1
            continue
        if char == "?":
            parts.append("[^/]")
            index += 1
            continue
        if char == "[":
            close = index + 1
            if close < length and pattern[close] in "!^":
                close += 1
            if close < length and pattern[close] == "]":
                close += 1
            while close < length and pattern[close] != "]":
                close += 1
            if close >= length:
                parts.append(re.escape("["))
                index += 1
                continue
            body = pattern[index + 1:close].replace("\\", "\\\\")
            if body[:1] in ("!", "^"):
                body = "^" + body[1:]
            parts.append(f"[{body}]")
            index = close + 1
            continue
        parts.append(re.escape(char))
        index += 1
    return "".join(parts)


def _sweep_glob_matchers(patterns: list[str]) -> list[re.Pattern[str]]:
    """Compile each glob ONCE per call, dropping any that will not compile.

    Compiled up front rather than inside the touched-file loop because that
    loop runs once per suffix of every path in the GRIND diff, and at the
    observed run scale (A-AUTO-002) a diff of fifty files is ordinary.

    A pattern that will not compile is DROPPED rather than raised on: this
    predicate's contract is a bool, `select_sweep_scope`'s other arms still
    run, and a FULL sweep re-executes the log regardless. Refusing here would
    turn a stray metacharacter in one command into a refused transition.
    """
    matchers: list[re.Pattern[str]] = []
    for pattern in patterns:
        if not pattern:
            continue
        try:
            matchers.append(re.compile(_sweep_glob_to_regex(pattern)))
        except re.error:
            continue
    return matchers


def _sweep_glob_operands(cmd: str) -> list[str]:
    """Every glob-shaped path operand in ``cmd``. ``[]`` when it cannot be lexed.

    D-045. The operand rules are `_sweep_walk_roots`' rules, held to
    deliberately: a token is not an operand when it is a separator, when it is
    a flag (`-l`, `--quiet`), or when it is the VALUE of a flag that takes one
    (`-k 'test_*'` is a pytest SELECTOR, not a path, and reading it as one
    would select every log whose command filters by name).

    Unlike `_sweep_walk_roots` this does NOT track a `cd`-established cwd, and
    the omission is the point rather than an oversight: the caller matches each
    pattern against every trailing suffix of the touched path, so the bare
    `tests/*.py` already answers everything the cwd-joined
    `plugins/foundry/mcp-server/tests/*.py` would. Tracking the cwd here would
    be a second derivation of a fact this arm never consults — the house
    anti-pattern the module comment on `_dispatched_agents` names.

    Returns `[]` on an unlexable command, which is the same fail-OPEN rule
    `_sweep_walk_roots` holds: the literal arm still runs, and the log is
    re-executed by any FULL sweep.
    """
    tokens = _shell_tokens(cmd)
    if tokens is None:
        return []
    consumed = {
        index + 1
        for index, token in enumerate(tokens)
        if token in _SWEEP_OPERAND_FLAG_VALUES and index + 1 < len(tokens)
    }
    operands: list[str] = []
    for index, token in enumerate(tokens):
        if index in consumed or token in _STUB_CMD_SEPARATORS:
            continue
        if token.startswith("-") or not _sweep_is_glob(token):
            continue
        text = token.replace("\\", "/").strip().rstrip("/")
        while text.startswith("./"):
            text = text[2:]
        if text:
            operands.append(text)
    return operands


#: Programs that read a directory tree rather than the operands they are
#: handed. DERIVED from the committed corpus's own `# evidence-cmd:` headers —
#: every command in `evidence/` is a pytest invocation, a `python3 scripts/*.py`
#: run, or a grep/awk pipeline — plus the linters and type checkers a foundry
#: casting's verification command routinely adds. `grep` is listed
#: unconditionally rather than only under `-r`: including a log that did not
#: need sweeping costs seconds, and the asymmetry stated above says which way
#: to err.
_SWEEP_WALKER_PROGRAMS: frozenset[str] = frozenset(
    {
        "pytest", "py.test", "unittest", "nosetests", "tox",
        "grep", "egrep", "fgrep", "rg", "ag", "ack", "find",
        "ruff", "mypy", "pyright", "flake8", "pylint", "black", "isort",
    }
)  # 20 programs


#: Tokens that are never a path operand: a flag, a separator, or a `-k`-style
#: value. Anything else in operand position is treated as a path, because a
#: false path costs an unnecessary sweep and a missed one costs a defect.
_SWEEP_OPERAND_FLAG_VALUES: frozenset[str] = frozenset(
    {"-k", "-m", "-e", "--deselect", "-p", "--ignore", "-n", "--with", "-c"}
)


def _sweep_walk_roots(cmd: str) -> list[str]:
    """Directories ``cmd`` reads recursively. ``""`` means the whole cwd.

    D-030. Walks the token stream tracking two things: the current working
    directory (set by a `cd` at any command position, since the corpus spells
    almost every command `cd plugins/foundry/mcp-server && …`) and, at each
    command position, whether the program is one that walks a tree.

    A walker with directory or path operands contributes each of them, joined
    onto the current cwd. A walker with NO path operand — a bare `pytest`, a
    `ruff check` — walks its cwd, so the cwd itself becomes the root; when no
    `cd` preceded it that is `""`, the whole tree, which is the honest answer.

    Returns ``[]`` when the command cannot be lexed. That is the same
    fail-OPEN rule `_shell_tokens`'s callers hold: an unparseable command is
    never judged on this rule's word, and the literal-suffix arm still runs.
    """
    tokens = _shell_tokens(cmd)
    if tokens is None:
        return []

    def _norm(raw: str) -> str:
        text = raw.replace("\\", "/").strip().rstrip("/")
        while text.startswith("./"):
            text = text[2:]
        return text

    def _join(base: str, path: str) -> str:
        if not base or path.startswith("/"):
            return path
        return f"{base}/{path}"

    # A flag's VALUE is not an operand and is not a program. `-p
    # no:cacheprovider` and `--deselect tests/x.py::y` both appear verbatim in
    # the committed corpus, and reading either as a path would invent a root.
    consumed = {
        index + 1
        for index, token in enumerate(tokens)
        if token in _SWEEP_OPERAND_FLAG_VALUES and index + 1 < len(tokens)
    }

    def _operands(start: int) -> list[str]:
        out: list[str] = []
        for index in range(start, len(tokens)):
            if tokens[index] in _STUB_CMD_SEPARATORS:
                break
            if index in consumed or tokens[index].startswith("-"):
                continue
            out.append(tokens[index])
        return out

    # A walker is looked for at EVERY token position, not only at a command
    # position, because the corpus almost never invokes one directly: the
    # standing spelling is `uv run --quiet --with pytest pytest …`, where
    # `uv` holds the command position and the program that actually walks the
    # tree is an operand of it. Restricting the search to command position
    # found a walker in none of those, which is most of the corpus.
    roots: list[str] = []
    cwd = ""
    at_command_position = True
    for index, token in enumerate(tokens):
        if token in _STUB_CMD_SEPARATORS:
            at_command_position = True
            continue
        if index in consumed:
            at_command_position = False
            continue
        if at_command_position and re.fullmatch(
            r"[A-Za-z_][A-Za-z0-9_]*=.*", token, re.DOTALL
        ):
            continue
        name = token.rsplit("/", 1)[-1]
        if at_command_position and name == "cd":
            targets = _operands(index + 1)
            if targets:
                cwd = _join(cwd, _norm(targets[0]))
            at_command_position = False
            continue
        at_command_position = False
        if name not in _SWEEP_WALKER_PROGRAMS:
            continue
        # A walker's sub-command (`ruff check`) is an operand too. A bare word
        # naming no real path simply never matches a touched file, so keeping
        # it costs nothing and dropping it would need a per-program operand
        # grammar this does not have.
        paths = [_norm(o) for o in _operands(index + 1) if _norm(o)]
        if paths:
            for path in paths:
                roots.append(_join(cwd, path))
                roots.append(path)       # the cwd-relative spelling too
        else:
            # No path operand: the walker reads its working directory. With no
            # preceding `cd` that is `""` — the whole tree, which is what a
            # bare `pytest` at the repo root honestly does.
            roots.append(cwd)
    return roots


#: Characters that may appear immediately before a path without ending it. A
#: suffix preceded by one of these is the tail of a LONGER path, not a
#: reference to this file, which is what keeps `test_evidence.py` from
#: matching a diff that touched `evidence.py`.
_SWEEP_PATH_CHARS: frozenset[str] = frozenset("abcdefghijklmnopqrstuvwxyz"
                                              "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
                                              "0123456789_-.")


def _iter_substring_starts(haystack: str, needle: str):
    """Yield every start index of ``needle`` in ``haystack``. Never raises."""
    if not needle:
        return
    start = haystack.find(needle)
    while start != -1:
        yield start
        start = haystack.find(needle, start + 1)


def _sweep_git(args: list[str], *, stdin: bytes | None = None) -> bytes | None:
    """Run a read-only git command. Returns stdout, or None on ANY failure.

    Never raises and never reports a reason: every caller's fallback is the
    working tree, and a sweep that could not consult git degrades to the
    behaviour it had before rather than refusing. The refusal that matters is
    the sweep's own, and it is raised where HEAD is resolved.
    """
    try:
        proc = subprocess.run(
            ["git", *args],
            input=stdin,
            capture_output=True,
            timeout=30,
        )
    except (subprocess.SubprocessError, OSError):
        return None
    return proc.stdout if proc.returncode == 0 else None


def _sweep_repo_root(evidence_dir: Path) -> Path | None:
    """The work-tree root containing ``evidence_dir``, or None outside git."""
    probe = evidence_dir
    while not probe.is_dir():
        parent = probe.parent
        if parent == probe:
            return None
        probe = parent
    out = _sweep_git(["-C", str(probe), "rev-parse", "--show-toplevel"])
    if not out:
        return None
    root = out.decode("utf-8", errors="replace").strip()
    return Path(root) if root else None


def _sweep_head_blobs(root: Path, rel_names: list[str]) -> dict[str, str]:
    """Read every named path's content AT HEAD in ONE `git cat-file --batch`.

    One subprocess for the whole corpus, not one per log: at the observed run
    scale (A-AUTO-002 — around forty to fifty logs by DONE) a `git show` per
    log is fifty process spawns inside a transition NFR-004 asks to finish
    well inside the INSPECT it precedes.

    `--batch` answers each `HEAD:<path>` request with ``<oid> SP <type> SP
    <size> LF``, then exactly ``<size>`` bytes, then a trailing LF; a request
    it cannot resolve comes back as ``<request> SP missing LF`` and is simply
    absent from the result. Parsed on BYTES because the size is a byte count —
    decoding first would desynchronise the cursor on any log holding a
    multi-byte character, and the corpus is full of pytest output that does.
    """
    if not rel_names:
        return {}
    stdin = "".join(f"HEAD:{name}\n" for name in rel_names).encode("utf-8")
    out = _sweep_git(["-C", str(root), "cat-file", "--batch"], stdin=stdin)
    if out is None:
        return {}

    blobs: dict[str, str] = {}
    cursor = 0
    for name in rel_names:
        newline = out.find(b"\n", cursor)
        if newline == -1:
            break
        header = out[cursor:newline].decode("utf-8", errors="replace").split()
        cursor = newline + 1
        if len(header) < 3 or header[1] != "blob":
            # "missing" / "ambiguous": no content follows, so the cursor is
            # already at the next header.
            continue
        try:
            size = int(header[2])
        except ValueError:
            break
        blobs[name] = out[cursor:cursor + size].decode("utf-8", errors="replace")
        cursor += size + 1  # the trailing LF git writes after the content
    return blobs


def _sweep_corpus(evidence_dir: Path) -> tuple[list[Path], dict[Path, str]]:
    """The committed corpus AT HEAD, plus each log's text at HEAD (D-016).

    Returns ``(sorted absolute log paths, {path: text at HEAD})``. The paths
    are spelled in the WORKING tree — `_sweep_one_log` maps them through
    `relative_to(project_root)` into the sweep worktree, so a log that exists
    at HEAD and not in the working tree resolves correctly and is re-executed.

    WHY HEAD AND NOT THE WORKING TREE
    ---------------------------------
    This globbed `evidence_dir` in the live tree while `sweep_evidence_at_head`
    compared inside a detached worktree at HEAD, so the two halves of one
    boundary disagreed about what the corpus IS. Driven: a log committed at
    HEAD but removed from the working tree produced a FULL scope of ZERO, and
    the boundary that exists to prove the committed evidence still reproduces
    returned ``ok: True`` having checked none of it. ST-005 says "byte-identical
    at HEAD"; the enumeration has to be at HEAD too or the sweep is measuring a
    corpus nobody committed.

    `ls-tree -r` is also RECURSIVE, which closes the second half of the same
    defect: the old `glob('*.log')` was flat, so a log under `evidence/<subdir>/`
    was in no scope, ever — not even a FULL one.

    Outside a git work tree (a synthetic fixture directory, a tarball) there is
    no HEAD to read, so this falls back to a RECURSIVE filesystem walk. That is
    a degradation, never a refusal: `select_sweep_scope` returns a scope and the
    sweep's own HEAD resolution is where an un-sweepable tree is named.
    """
    root = _sweep_repo_root(evidence_dir)
    if root is not None:
        try:
            rel_dir = evidence_dir.resolve().relative_to(root.resolve())
        except (ValueError, OSError):
            rel_dir = None
        if rel_dir is not None:
            out = _sweep_git(
                ["-C", str(root), "ls-tree", "-r", "-z", "--name-only", "HEAD",
                 "--", str(rel_dir)]
            )
            if out is not None:
                names = sorted(
                    name
                    for name in out.decode("utf-8", errors="replace").split("\0")
                    if name.endswith(".log")
                )
                blobs = _sweep_head_blobs(root, names)
                logs = [root / name for name in names]
                return logs, {
                    root / name: text for name, text in blobs.items()
                }

    try:
        logs = sorted(p for p in evidence_dir.rglob("*.log") if p.is_file())
    except OSError:
        # An unreadable evidence directory is not this function's refusal to
        # make: it returns nothing, and the caller's own sweep reports a corpus
        # of zero rather than a traceback across the MCP boundary.
        return [], {}
    texts: dict[Path, str] = {}
    for log in logs:
        try:
            texts[log] = log.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
    return logs, texts


def select_sweep_scope(
    *,
    manifest: dict,
    evidence_dir: Path,
    touched_files: list[str],
    full: bool,
) -> list[Path]:
    """The evidence logs this boundary must re-execute (GI-002 / AC-014).

    Args:
        manifest: the run's `castings/manifest.json` as a dict. Read for each
            casting's `key_files` (the delta intersection) and `spec_text`
            (the requirement-ID -> casting resolution). An empty or malformed
            manifest degrades to "no casting can be keyed", never raises.
        evidence_dir: the directory holding the committed corpus — repo-root
            `evidence/`, the same directory `verify_evidence` globs inside the
            casting's worktree.
        touched_files: repo-relative paths from the GRIND diff since the last
            sweep. Ignored entirely when `full` is True.
        full: the FULL-rule outcome the calling transition already computed.
            GI-009 is emphatic that the decision lives at the transition and
            nowhere else, so this function is told the answer and never
            re-derives it.

    Returns:
        A sorted list of absolute log paths. Sorted because a sweep result is
        read by a human comparing two cycles, and a set's iteration order would
        make two identical sweeps look different.

    `full=True` returns EVERY committed log. `full=False` returns every log
    whose casting's `key_files` intersect `touched_files`, plus every log whose
    `# evidence-cmd:` references a touched file — and an empty list when
    neither holds, which is AC-014's "re-executes zero logs" and is a correct
    answer, not a degenerate one.

    A log is keyed to its casting by BOTH the `casting-{id}-*.log` filename
    convention and the casting its `# evidence-for:` header resolves to; see
    `_sweep_requirement_to_castings` for why one source is not enough. A log
    that cannot be keyed by either is not dropped silently — its only delta
    test is the command-reference arm, and `full=True` sweeps it regardless.
    """
    candidates, texts = _sweep_corpus(evidence_dir)
    if full:
        return candidates

    touched = [str(t) for t in (touched_files or []) if str(t).strip()]
    if not touched:
        return []

    touched_castings = _sweep_touched_castings(manifest, touched)
    req_to_castings = _sweep_requirement_to_castings(manifest)

    selected: list[Path] = []
    for log in candidates:
        keyed: set[str] = set()
        name_match = re.match(r"^casting-([^-]+)-", log.name)
        if name_match:
            keyed.add(name_match.group(1))
        header: dict[str, Any] = {"cmd": None, "evidence_for": []}
        try:
            header = _parse_evidence_header(texts.get(log, ""))
        except ValueError:
            # A log whose header will not parse is still a log. It cannot be
            # keyed by requirement and its command cannot be read, so it falls
            # out of the DELTA scope — and a FULL sweep will re-execute it and
            # surface the malformed header as the mismatch it is.
            pass
        for req_id in header.get("evidence_for") or []:
            keyed |= req_to_castings.get(req_id, set())
        if keyed & touched_castings:
            selected.append(log)
            continue
        if _sweep_command_references(header.get("cmd") or "", touched):
            selected.append(log)
    return selected


def _derive_sweep_pool_size(logs: list[Path], override: int | None) -> int:
    """FR-031 — the worker count, derived from the corpus about to be swept.

    Never more workers than there is work (`len(logs)`), never more than the
    machine can actually run in parallel (`os.cpu_count()`), and never past
    `SWEEP_POOL_CEILING`, whose derivation from the committed corpus is stated
    at its definition. An explicit `override` from the caller wins, clamped to
    at least one, because a lead debugging a flaky log wants to force
    serialisation and a pool of zero would simply hang.
    """
    if override is not None:
        return max(1, int(override))
    if not logs:
        return 0
    return max(1, min(len(logs), os.cpu_count() or 1, SWEEP_POOL_CEILING))


def _sweep_log_timeout(header: dict[str, Any], override: float | None) -> int:
    """FR-031 — the per-log timeout, taken from the log's own measurement.

    A `# evidence-timeout:` header is the artifact author's own statement about
    how long their command needs, already validated by `_parse_evidence_header`
    against `EVIDENCE_TIMEOUT_CEILING_SECONDS`. Honouring it is what keeps the
    sweep and acceptance agreeing about the same log: `_verify_one_evidence_file`
    reads exactly this value, and a sweep that imposed its own would kill a
    300-second integration log that acceptance had already passed.

    Undeclared falls back to `EVIDENCE_TIMEOUT_DEFAULT_SECONDS`, the same
    default acceptance uses. A caller-supplied `override` wins for every log,
    which is the knob a lead uses to bound a whole sweep.
    """
    if override is not None:
        return max(1, int(override))
    declared = header.get("timeout")
    if isinstance(declared, int) and declared > 0:
        return declared
    return EVIDENCE_TIMEOUT_DEFAULT_SECONDS


def _sweep_mismatch(
    *,
    log_name: str,
    reason: str,
    failure_token: str,
    redacted_log: str | None = None,
    redacted_captured: str | None = None,
    exit_code: int | None = None,
    elapsed_seconds: float = 0.0,
) -> dict[str, Any]:
    """One mismatch record, carrying BOTH vocabularies for the same two hashes.

    C-7 names the fields `expected_sha256` / `actual_sha256`; the provenance
    records `_make_provenance_record` writes name the same two values
    `redacted_log_sha256` / `redacted_captured_sha256`. A reader that arrives
    from either direction — a lead reading a sweep refusal, or a tool
    correlating that refusal against the casting's accepted provenance — must
    find the value under the spelling it knows, so both are carried and the
    hash is computed ONCE per side and assigned to both names.

    The four hash fields are `None`, not the hash of the empty string, on every
    path where redaction never ran (a timeout, a non-zero exit, a header that
    would not parse). `_hash_str("")` is a real, stable, meaningless value, and
    publishing it would let a reader compare two logs that were never compared
    and find them equal.
    """
    expected = None if redacted_log is None else _hash_str(redacted_log)
    actual = None if redacted_captured is None else _hash_str(redacted_captured)
    return {
        "log": log_name,
        "reason": reason,
        "failure_token": failure_token,
        "expected_sha256": expected,
        "actual_sha256": actual,
        "redacted_log_sha256": expected,
        "redacted_captured_sha256": actual,
        "exit_code": exit_code,
        "elapsed_seconds": round(elapsed_seconds, 3),
    }


def _sweep_head_log_path(log: Path, project_root: Path, worktree_path: Path) -> Path:
    """Where ``log`` lives inside the sweep worktree, best effort."""
    try:
        return worktree_path / log.resolve().relative_to(project_root.resolve())
    except (ValueError, OSError):
        return worktree_path / log.name


def _sweep_submission_order(
    logs: list[Path], *, project_root: Path, worktree_path: Path
) -> list[Path]:
    """NFR-004 — dispatch order chosen for WALL TIME, not for reading.

    "pool size and log ordering are tuned to that", where "that" is finishing
    well inside the INSPECT the sweep precedes. The order was a plain
    `sorted()`, which is tuned for a reader diffing two sweeps — a real goal,
    but a different one, and the requirement names wall time.

    Longest Processing Time first: with a fixed pool, dispatching the longest
    jobs first is the classic makespan heuristic, and it bounds the finish at
    (4/3 - 1/(3m)) times optimal. The failure it removes is concrete — the
    corpus holds one 300-second integration log among a dozen 5-second greps,
    and a `sorted()` order that happens to dispatch it LAST leaves seven idle
    workers waiting five minutes on one straggler.

    The estimate is the log's own `# evidence-timeout:` — the author's stated
    upper bound, already validated at acceptance — falling back to the shared
    default, with byte size breaking ties because a longer capture came from a
    longer command (a pytest suite prints more than a grep). Read from the
    WORKTREE, so a log absent from the working tree is still estimated.

    Reporting order is unaffected: the caller re-keys results into the input
    order, so two identical sweeps still print identically (see
    `sweep_evidence_at_head`).
    """
    def _weight(log: Path) -> tuple[int, int, str]:
        head_log = _sweep_head_log_path(log, project_root, worktree_path)
        declared = EVIDENCE_TIMEOUT_DEFAULT_SECONDS
        size = 0
        try:
            text = head_log.read_text(encoding="utf-8", errors="replace")
            size = len(text)
            header = _parse_evidence_header(text)
            value = header.get("timeout")
            if isinstance(value, int) and value > 0:
                declared = value
        except (OSError, ValueError):
            pass
        # Negated so `sorted` ascending puts the heaviest first; the name is
        # the final tie-break so the order is deterministic for a given corpus.
        return (-declared, -size, log.name)

    return sorted(logs, key=_weight)


#: The shell every `# evidence-cmd:` is parsed with AND executed by, named once.
#
# `worktree_helpers._run_command_with_timeout` launches the command through
# `Popen(shell=True)` with no `executable=` argument — there is none anywhere in
# the plugin — which on POSIX is `['/bin/sh', '-c', cmd]`. The lint below has to
# parse with the shell that will run the command or it would be judging a
# dialect nobody executes, so the path is one constant both halves read rather
# than a literal spelled twice that can drift.
_EVIDENCE_SHELL: str = "/bin/sh"


def _shell_parse_problem(cmd: str) -> str | None:
    """The shell's own complaint when ``cmd`` will not parse; None when it will.

    ``-n`` READS AND PARSES WITHOUT EXECUTING. Nothing in ``cmd`` runs here, at
    any size, under any content — that is the whole of the check, and it is the
    only reason handing an unreviewed command to a shell is safe at all.

    NOT A CONTRADICTION OF ``_shell_tokens``, which returns None on an unlexable
    command so that the log still RUNS. The two answer different questions and
    fail in opposite directions on purpose. ``_shell_tokens`` decides whether a
    GRIND diff INVALIDATES a log — a question about relevance, where rejecting a
    command nobody can lex would discard evidence for a reason unrelated to
    whether it still reproduces, so it fails OPEN. This decides whether the
    command CAN RUN AT ALL, where "I cannot parse this" is the same sentence the
    shell about to execute it is going to say, so it fails CLOSED. Neither
    rationale is weakened by the other; changing one does not license changing
    the other.

    The command is an ARGUMENT to ``-c``, never written to the child's stdin:
    ``sh -n`` abandons a broken script without draining its input, so a pipe
    would race the writer against a reader that has already gone.

    Never raises. A shell that cannot be spawned at all is reported AS a problem
    rather than swallowed, because a sweep that cannot answer this question must
    not answer it with silence — the caller's whole contract is that a command
    reaching the runner has been parsed.
    """
    try:
        proc = subprocess.run(
            [_EVIDENCE_SHELL, "-n", "-c", cmd],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
    except (subprocess.SubprocessError, OSError) as exc:
        return f"{_EVIDENCE_SHELL} -n could not run: {type(exc).__name__}: {exc}"
    if proc.returncode == 0:
        return None
    return (proc.stderr or proc.stdout or "").strip() or (
        f"{_EVIDENCE_SHELL} -n exited {proc.returncode} without a message"
    )


def _sweep_one_log(
    log: Path,
    *,
    project_root: Path,
    worktree_path: Path,
    timeout_seconds: float | None,
) -> tuple[str, dict[str, Any] | None]:
    """Re-execute ONE log at HEAD. Returns ``(log_name, mismatch_or_None)``.

    Runs on a pool worker, so it NEVER raises: an exception here would surface
    at `future.result()` in the collector and take the whole sweep — and with
    it the `inspect_start` transition — down with a traceback naming no log.
    Every failure becomes a named mismatch instead, which is the same rule
    `_verify_one_evidence_file` holds one layer down.

    The committed bytes are read from INSIDE the worktree, not from the working
    tree. That is ST-005's "byte-identical at HEAD" taken literally: a log the
    lead edited but did not commit is not the corpus, and comparing a working-
    tree log against a HEAD re-execution would report a mismatch that says
    nothing about whether the committed evidence still reproduces.
    """
    log_name = _sweep_relative_log_name(log, project_root)
    try:
        try:
            relative = log.resolve().relative_to(project_root.resolve())
        except (ValueError, OSError):
            return log_name, _sweep_mismatch(
                log_name=log_name,
                reason=(
                    f"{log_name} is not inside the swept tree, so it has no "
                    f"counterpart at HEAD"
                ),
                failure_token="EVIDENCE_COMMAND_MISSING",
            )
        head_log = worktree_path / relative
        if not head_log.is_file():
            return log_name, _sweep_mismatch(
                log_name=log_name,
                reason=(
                    f"{log_name} is not committed at HEAD; the sweep compares "
                    f"the COMMITTED corpus, so an uncommitted log has nothing "
                    f"to re-execute against"
                ),
                failure_token="EVIDENCE_COMMAND_MISSING",
            )

        log_text = head_log.read_text(encoding="utf-8", errors="replace")
        try:
            header = _parse_evidence_header(log_text)
        except ValueError as exc:
            msg = str(exc)
            token = (
                "EVIDENCE_FOR_MALFORMED"
                if msg.startswith("EVIDENCE_FOR_MALFORMED")
                else "EVIDENCE_VOLATILE_MALFORMED"
            )
            return log_name, _sweep_mismatch(
                log_name=log_name, reason=msg, failure_token=token
            )

        cmd = header.get("cmd")
        if cmd is None:
            return log_name, _sweep_mismatch(
                log_name=log_name,
                reason=f"no `# evidence-cmd:` header in {log.name}",
                failure_token="EVIDENCE_COMMAND_MISSING",
            )

        # CT-015 / FR-002 — PARSED BEFORE IT IS EXECUTED, and refused here if it
        # will not parse. Until this rung a command with a syntax error was
        # discovered by RUNNING it: the shell exited non-zero, the sweep called
        # that EVIDENCE_EXIT_NONZERO, and the operator read "your command
        # failed" for what was a typo the shell had already diagnosed in full.
        # Worse, everything before the syntax error in a partially-valid script
        # ran first — `rm -rf x && (` executes the `rm` — so "it only failed to
        # parse" was never the same as "it had no effect".
        #
        # The refusal is a per-log mismatch like every other, so the boundary and
        # terminal crossings that read this result name the log and the token
        # without a new path: nothing downstream of here is re-decided.
        parse_problem = _shell_parse_problem(cmd)
        if parse_problem is not None:
            return log_name, _sweep_mismatch(
                log_name=log_name,
                reason=(
                    f"`# evidence-cmd:` in {log.name} does not parse under "
                    f"`{_EVIDENCE_SHELL} -n`, so it was NOT executed: "
                    f"{parse_problem}"
                ),
                failure_token="EVIDENCE_COMMAND_SYNTAX",
            )

        timeout = _sweep_log_timeout(header, timeout_seconds)
        exit_code, captured, elapsed = _run_command_with_timeout(
            cmd=cmd, cwd=worktree_path, timeout=timeout
        )
        if exit_code == -1:
            return log_name, _sweep_mismatch(
                log_name=log_name,
                reason=f"command exceeded {timeout}s; killed via SIGTERM/SIGKILL",
                failure_token="EVIDENCE_TIMEOUT",
                exit_code=exit_code,
                elapsed_seconds=elapsed,
            )
        if exit_code != 0:
            return log_name, _sweep_mismatch(
                log_name=log_name,
                reason=f"command exited with code {exit_code}",
                failure_token="EVIDENCE_EXIT_NONZERO",
                exit_code=exit_code,
                elapsed_seconds=elapsed,
            )

        # D-200: the same one-decision strip the acceptance door uses. The
        # boundary sweep took the same false ACCEPT — driven at f5b487b,
        # `Foundry-Phase(inspect_start)` on a committed log whose three
        # captured `#` lines disagreed with HEAD reported no mismatch and
        # advanced the cycle counter — so both callers route through
        # `_header_stripped_pair` rather than stripping each side alone.
        #
        # D-205: and the same NARROWED grammar. Driven at cb77e83, this
        # boundary advanced the counter with `mismatches: []` on a committed
        # log carrying `# FABRICATED: all 47 assertions passed on a clean
        # tree` above its body, because the discarded run was never tested
        # against the grammar. One entry point, one grammar, both doors.
        committed_body, captured_body = _header_stripped_pair(log_text, captured)
        try:
            matched, diff, redacted_log, redacted_captured = _compare_byte_match(
                committed=committed_body,
                captured=captured_body,
                volatile_patterns=header.get("volatile", []),
            )
        except ValueError as exc:
            return log_name, _sweep_mismatch(
                log_name=log_name,
                reason=str(exc),
                failure_token="EVIDENCE_VOLATILE_MALFORMED",
                exit_code=exit_code,
                elapsed_seconds=elapsed,
            )
        if not matched:
            return log_name, _sweep_mismatch(
                log_name=log_name,
                reason=diff or "re-execution output differs from the committed log",
                failure_token="EVIDENCE_OUTPUT_MISMATCH",
                redacted_log=redacted_log,
                redacted_captured=redacted_captured,
                exit_code=exit_code,
                elapsed_seconds=elapsed,
            )
        return log_name, None
    except BaseException as exc:  # noqa: BLE001 — see the docstring
        return log_name, _sweep_mismatch(
            log_name=log_name,
            reason=f"sweep worker failed: {type(exc).__name__}: {exc}",
            failure_token="EVIDENCE_COMMAND_MISSING",
        )


def _sweep_run_one(
    log: Path,
    *,
    project_root: Path,
    worktree_path: Path,
    timeout_seconds: float | None,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """`_sweep_one_log` plus its own wall time. Returns ``(record, mismatch)``.

    CT-007 asks for the sweep result "recorded per log with scope (delta or
    full) and elapsed seconds", and only MISMATCHES carried a time — so the
    column the contract names was absent for exactly the logs that passed, and
    a lead tuning NFR-004's pool could not see which log was the straggler.

    The measurement is the WHOLE per-log operation: reading the committed bytes
    out of the worktree, running the command, and comparing. A mismatch record
    keeps its own narrower `elapsed_seconds` — the command's run alone — beside
    this one, because that is the number a lead debugging a timeout wants and
    it is not the same number.
    """
    started = time.monotonic()
    log_name, mismatch = _sweep_one_log(
        log,
        project_root=project_root,
        worktree_path=worktree_path,
        timeout_seconds=timeout_seconds,
    )
    record = {
        "log": log_name,
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "matched": mismatch is None,
        # A matched log reached the comparison, which `_sweep_one_log` only
        # does after the command exited 0.
        "exit_code": 0 if mismatch is None else mismatch.get("exit_code"),
        "failure_token": None if mismatch is None else mismatch.get("failure_token"),
    }
    return record, mismatch


def sweep_evidence_at_head(
    *,
    project_root: Path,
    run_dir: Path,
    logs: list[Path],
    pool_size: int | None = None,
    timeout_seconds: float | None = None,
) -> dict[str, Any]:
    """Re-execute the in-scope corpus at HEAD (GI-002 / ST-005 / CT-007).

    ONE detached worktree at HEAD of ``project_root`` for the whole sweep, torn
    down on the success path and on every failure path. Each log's
    ``# evidence-cmd:`` is PARSED with `_shell_parse_problem` before it is run —
    a command that will not parse is refused as EVIDENCE_COMMAND_SYNTAX and
    never reaches the runner (CT-015) — then runs inside the worktree in a
    bounded thread pool, and each capture goes through the SAME comparison
    `verify_evidence` uses —
    `_compare_byte_match`, with the same declared-volatile redaction, the same
    D-126 residue floor and the same D-135 disagreement guard. None of those
    rules is re-decided here.

    Args:
        project_root: the shared tree the run is building in. HEAD of THIS repo
            is what the sweep checks out — not any casting's own commit, which
            is acceptance's question and a different one.
        run_dir: the run directory; the worktree is created beneath it, exactly
            as acceptance's is.
        logs: what `select_sweep_scope` returned. An empty list is a complete
            answer: zero logs re-executed, no worktree created, no subprocess
            spawned (AC-014).
        pool_size: optional worker-count override; otherwise derived from the
            corpus by `_derive_sweep_pool_size`.
        timeout_seconds: optional per-log timeout override; otherwise each log's
            own `# evidence-timeout:` is honoured, falling back to
            `EVIDENCE_TIMEOUT_DEFAULT_SECONDS`.

    Returns:
        ``{'ok': bool, 'scope_count': int, 'logs_reexecuted': [str],
        'per_log': [{'log', 'elapsed_seconds', 'matched', 'exit_code',
        'failure_token'}],
        'mismatches': [{'log', 'reason', 'expected_sha256', 'actual_sha256',
        'redacted_log_sha256', 'redacted_captured_sha256', 'failure_token',
        'exit_code', 'elapsed_seconds'}], 'elapsed_seconds': float,
        'pool_size': int, 'head_commit': str | None, 'error': str | None}``.

    ``per_log`` is CT-007's "recorded per log ... and elapsed seconds", and it
    carries a row for EVERY log in scope, matched or not — the timing column
    used to exist only on mismatches, so the logs that passed had none.

    ``ok`` is False when any log mismatched OR when the sweep could not run at
    all (no HEAD to resolve, no worktree to create). ``error`` is non-None only
    in that second case, and the caller must name it in the refusal — a sweep
    that could not run is emphatically not a sweep that passed, and returning
    ``ok: True`` with an empty mismatch list is the shape that would let a
    broken sweep quietly clear the boundary it exists to hold.

    Never raises. The `inspect_start` transition owns the refusal, the cycle
    counter and the `evidence_sweep` roll-up record; this function writes
    nothing and decides nothing about the run.
    """
    started = time.monotonic()
    scope_count = len(logs)
    result: dict[str, Any] = {
        "ok": True,
        "scope_count": scope_count,
        "logs_reexecuted": [],
        "per_log": [],
        "mismatches": [],
        "elapsed_seconds": 0.0,
        "pool_size": _derive_sweep_pool_size(logs, pool_size),
        "head_commit": None,
        "error": None,
    }
    if not logs:
        # AC-014's zero-log case, and the reason it is handled BEFORE anything
        # else: a DELTA sweep whose GRIND touched nothing in scope must cost no
        # worktree, no `.git/config.lock` contention and no subprocess at all.
        # Creating a worktree and immediately tearing it down would be the same
        # answer at a cost NFR-004 exists to avoid.
        result["elapsed_seconds"] = round(time.monotonic() - started, 3)
        return result

    try:
        head = subprocess.run(
            ["git", "-C", str(project_root), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
    except (subprocess.SubprocessError, OSError) as exc:
        result["ok"] = False
        result["error"] = (
            f"could not resolve HEAD of {project_root}: {type(exc).__name__}: {exc}"
        )
        result["elapsed_seconds"] = round(time.monotonic() - started, 3)
        return result
    if head.returncode != 0 or not head.stdout.strip():
        result["ok"] = False
        result["error"] = (
            f"could not resolve HEAD of {project_root}: "
            f"{head.stderr.strip() or 'git rev-parse HEAD produced no output'}"
        )
        result["elapsed_seconds"] = round(time.monotonic() - started, 3)
        return result
    head_commit = head.stdout.strip()
    result["head_commit"] = head_commit

    try:
        _prune_orphaned_worktrees(project_root)
    except (subprocess.SubprocessError, OSError):
        # D-116, same reasoning as the acceptance path: housekeeping must never
        # decide a verdict. If it matters, `_setup_worktree` fails next.
        pass

    worktree_path: Path | None = None
    try:
        try:
            worktree_path = _setup_worktree(
                project_root,
                "evidence",
                head_commit,
                run_dir,
                dir_prefix=SWEEP_WORKTREE_PREFIX,
            )
        except (RuntimeError, subprocess.SubprocessError, OSError) as exc:
            result["ok"] = False
            result["error"] = (
                f"could not create the sweep worktree at HEAD "
                f"{head_commit[:12]}: {type(exc).__name__}: {exc}"
            )
            result["elapsed_seconds"] = round(time.monotonic() - started, 3)
            return result

        pool = result["pool_size"]
        # NFR-004 — DISPATCH order is longest-first (see
        # `_sweep_submission_order`); REPORTING order is the caller's, restored
        # below. The two goals are different and were previously served by one
        # `sorted()` that met neither: a lead diffing cycle N against N-1 needs
        # a stable list, and the pool needs the straggler started first.
        order = _sweep_submission_order(
            logs, project_root=project_root, worktree_path=worktree_path
        )
        outcomes: dict[Path, tuple[dict[str, Any], dict[str, Any] | None]] = {}
        with ThreadPoolExecutor(max_workers=pool) as executor:
            futures = {
                executor.submit(
                    _sweep_run_one,
                    log,
                    project_root=project_root,
                    worktree_path=worktree_path,
                    timeout_seconds=timeout_seconds,
                ): log
                for log in order
            }
            for future, log in futures.items():
                outcomes[log] = future.result()

        executed: list[str] = []
        per_log: list[dict[str, Any]] = []
        mismatches: list[dict[str, Any]] = []
        for log in logs:
            record, mismatch = outcomes[log]
            executed.append(record["log"])
            per_log.append(record)
            if mismatch is not None:
                mismatches.append(mismatch)
        result["logs_reexecuted"] = executed
        # CT-007's per-log column. `logs_reexecuted` stays a list of relative
        # paths because that is what C-6 writes into `stream-rollup.json` and
        # casting 3 reads; the timing rides beside it rather than changing the
        # element type of a field another casting already consumes.
        result["per_log"] = per_log
        result["mismatches"] = mismatches
        result["ok"] = not mismatches
    finally:
        if worktree_path is not None and worktree_path.exists():
            try:
                _teardown_worktree(project_root, worktree_path)
            except (subprocess.SubprocessError, OSError):
                # D-116: an exception in a `finally` REPLACES the result the
                # body computed. A slow git during cleanup must not discard a
                # sweep that already ran; the worst case is a stale directory
                # the next prune removes.
                pass

    result["elapsed_seconds"] = round(time.monotonic() - started, 3)
    return result
