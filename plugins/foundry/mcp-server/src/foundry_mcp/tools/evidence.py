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
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from foundry_mcp.schemas.vocab import REQUIREMENT_ID_RE
from foundry_mcp.tools.foundry_handoff import _hash_str
from foundry_mcp.tools.foundry_spawn import _manifest_shape_problem
from foundry_mcp.tools.foundry_state import get_run_dir, read_document
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
# EVERY GRAMMAR CARRIES A LIVE WITNESS, or the registry sweep names it. Two
# witness kinds, both real artifacts in the tree rather than a maintainer's
# memory: ``corpus``, meaning some committed log's own declared pattern erases
# a token of this shape from its own body, and ``protocol``, meaning
# ``plugins/foundry/agents/teammate.md`` ships this pattern as an example it
# tells evidence authors to declare. ``test_every_declared_grammar_has_a_live_witness``
# walks the registry itself — not a hand-copied list — and reports by name any
# grammar whose witness has gone dead, so a grammar cannot outlive the reason
# it was admitted.
#
# STATED RESIDUAL, scoped this time to what actually ships rather than to the
# one instance that was easiest to describe. D-143's ruling was that a limit is
# a limit only when its DECLARED scope is its REAL scope, so this names the
# whole of it, including the part that is uncomfortable.
#
# A disagreement inside a token that matches a declared grammar IN THAT
# GRAMMAR'S OWN DIRECTION OF VARIATION is admitted. Concretely, and these are
# the real attacks, not a euphemism for them:
#
#   1. A fabricated duration against a real one — `4.86s` against `9.91s`.
#      Refusing this would refuse 37 of the corpus's 50 differing token pairs.
#   2. A VERDICT WORD PLACED IN A PATH SEGMENT — a committed log citing
#      `/logs/passed/run` against a command printing `/logs/failed/run`. Both
#      are absolute paths, both differ outside their digits, so the path
#      grammar reads it as a relocation and lets it through. This is the same
#      family D-143 was filed on, one spelling further out, and it is NOT
#      closed. It cannot be closed by shape: the corpus relocates paths by
#      exactly one alphabetic segment (`.tmphgnUSu` against `.tmpLS4FNM`) and
#      by whole roots (`/Users/rayjanoka/…` against `/private/tmp/…`), so no
#      rule over segment counts, positions or character classes separates a
#      moved directory from a swapped word. Separating them needs a list of
#      verdict words, which is the remembered-membership shape this whole class
#      keeps recurring as. Recorded for the lead rather than hand-listed here.
#
# What is NOT in the residual, and was in the last one: every verdict, count,
# ratio, percentage, exit code, assignment and hyphenated word standing on its
# own, plus every count embedded in a path — `/tmp/out/1650/x` against
# `/tmp/out/1631/x` is refused, and so is `/tmp/run-1650/x`.
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

    ``witness_kind`` / ``witness`` record what keeps this entry alive:
    ``corpus`` names a committed evidence log whose own declared pattern erases
    a token of this shape from its own body, ``protocol`` names the literal
    ``# evidence-volatile:`` example ``agents/teammate.md`` ships. ``sample``
    is a real token of the shape, used by the witness sweep to prove the
    grammar and its witness still describe the same thing.
    """

    token: re.Pattern[str]
    varies_in: str
    witness_kind: str
    witness: str
    sample: str
    note: str


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
        witness_kind="corpus",
        witness="casting-1-pytest.log",
        sample="4.86s",
        note=(
            "wall-clock seconds. 37 of the corpus's 50 differing token pairs, "
            "and the shape 42 of the 65 logs declare volatile. The decimal "
            "point is required because every second-valued duration in the "
            "corpus carries one; a bare `\\d+s` has no witness."
        ),
    ),
    "duration_millis": _EnvironmentalGrammar(
        token=re.compile(r"\d+(?:\.\d+)?(?:ms|us|µs|ns)"),
        varies_in="digits",
        witness_kind="protocol",
        witness=r"# evidence-volatile: \b\d+ms\b",
        sample="12ms",
        note=(
            "sub-second latencies. 31 logs DECLARE `Installed \\d+ packages "
            "in \\d+ms`, but the witness sweep showed no committed body "
            "actually carries one — uv prints that line only on a cold cache, "
            "so the declaration is precautionary and the corpus has never "
            "varied a millisecond value. The live witness is therefore the "
            "protocol, not the corpus. Kept because teammate.md tells authors "
            "to declare this shape and the narrowness controls exercise it."
        ),
    ),
    "absolute_path": _EnvironmentalGrammar(
        token=re.compile(r"/\S*"),
        varies_in="text",
        witness_kind="corpus",
        witness="casting-1-pytest.log",
        sample="/Users/rayjanoka/ab/code/guild/plugins/foundry/mcp-server",
        note=(
            "where the run happened: rootdir and cachedir roots, uv build "
            "dirs, the .planning root. 13 of the corpus's 50 differing token "
            "pairs. Anchored at `/` because every path the corpus varies is "
            "absolute — a RELATIVE path such as `out/1650/summary.json` is "
            "not admitted, and `0/1650` is not a path at all."
        ),
    ),
    "process_id": _EnvironmentalGrammar(
        token=re.compile(r"pid=\d+", re.IGNORECASE),
        varies_in="digits",
        witness_kind="protocol",
        witness=r"# evidence-volatile: pid=\d+",
        sample="pid=1234",
        note=(
            "the key is part of the grammar. `exit=0` against `exit=1` is an "
            "assignment too and is REFUSED — what makes a pid environmental "
            "is that it is a pid, not that it has an `=` in it."
        ),
    ),
    "iso_timestamp": _EnvironmentalGrammar(
        token=re.compile(
            r"20\d{2}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?"
            r"(?:Z|[+-]\d{2}:?\d{2})?)?"
        ),
        varies_in="digits",
        witness_kind="protocol",
        witness=r"# evidence-volatile: 20\d{2}-\d{2}-\d{2}T",
        sample="2026-08-14T10:23:45",
        note=(
            "wall-clock date, with or without a time. The date alone is "
            "admitted because the corpus's own decoy shape is `20\\d{2}-"
            "\\d{2}-\\d{2}`."
        ),
    ),
}  # 5 grammars


def _digit_skeleton(token: str) -> str:
    """``token`` with every run of digits removed.

    The one primitive both directions of ``varies_in`` are measured through, so
    the two halves of the partition cannot drift apart: a digits-varying field
    must leave this UNCHANGED across the two sides, a text-varying field must
    leave it CHANGED.
    """
    return _DIGIT_RUN_RE.sub("", token)


def _environmental_field(token_a: str, token_b: str) -> tuple[str | None, str]:
    """Classify a disagreeing token pair against the grammar allowlist.

    Returns ``(grammar_name, note)``. A non-None name means the environment is
    allowed to have varied this token and the redaction may erase it. A None
    name means REFUSE, and the note says what could not be classified — the
    whole registry is walked before giving up, so the note reports the most
    specific reason found rather than the first grammar tried.

    Both sides must match the SAME grammar. A token pair that changes shape —
    a duration on one side, a path on the other — is not one field varying.
    """
    closest = ""
    for name, grammar in _ENVIRONMENTAL_GRAMMARS.items():
        if not (
            grammar.token.fullmatch(token_a) and grammar.token.fullmatch(token_b)
        ):
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
        for token_a, token_b in zip(tokens_a, tokens_b):
            if token_a == token_b:
                continue
            grammar, note = _environmental_field(token_a, token_b)
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
