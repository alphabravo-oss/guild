#!/usr/bin/env python3
"""
validate-spec.py — deterministic fidelity/traceability/coverage gate for
forge-generated specifications.

Enforces the Verbatim-Fidelity contract:

  * Fidelity     — every Locked requirement quotes the user's literal words,
                   and the quote is a byte-identical substring of the cited
                   transcript answer.
  * Traceability — every bullet / table row in listed sections carries a
                   citation marker that resolves to a real transcript answer
                   (or a survey file / reality.md for permitted sections).
  * Coverage     — every A-NNN in the transcript is cited somewhere in the
                   spec body, so no interview content is silently dropped.
  * Structure    — the spec has a populated `## Global Invariants` section
                   and embeds the transcript verbatim as an appendix.

Exits 0 on pass, 1 on any failure, 2 on usage error.
Usage: validate-spec.py <spec.md> <transcript.md>

This script is the authoritative R4 gate. The model's self-check prose in
setup-forge.sh is advisory; this script is load-bearing. If the script fails,
finalization must not proceed.
"""

from __future__ import annotations

import re
import string
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MIN_TRANSCRIPT_ANSWERS = 3

APPENDIX_HEADING_RE = re.compile(
    r"^##\s+Appendix:\s*Interview\s+Transcript\b",
    re.MULTILINE | re.IGNORECASE,
)

GLOBAL_INVARIANTS_HEADING_RE = re.compile(
    r"^##\s+Global\s+Invariants\b",
    re.MULTILINE | re.IGNORECASE,
)

# Accept:
#   ## A-001
#   ## A-001 [TAG, TAG]
#   ## A-001 (short label)
#   ## A-001 [TAG] (short label)
# Reject (caught separately with a clearer error):
#   ## A-005..A-008            (range form — one block per answer)
#   ## A-005, A-006            (comma-batched)
ANSWER_BLOCK_RE = re.compile(
    r"^##\s+(A-\d+)"
    r"(?:\s*\[([^\]]*)\])?"
    r"(?:\s*\(([^)]*)\))?"
    r"\s*\n(.*?)"
    r"(?=^##\s+[AQ]-\d+|^##\s+[A-Z]|\Z)",
    re.MULTILINE | re.DOTALL,
)

BATCHED_ANSWER_HEADING_RE = re.compile(
    r"^##\s+A-\d+(?:\.\.A-\d+|\s*,\s*A-\d+)",
    re.MULTILINE,
)

QUESTION_BLOCK_RE = re.compile(
    r"^##\s+Q-\d+\b",
    re.MULTILINE,
)

CITATION_RE = re.compile(
    r"\[(?:from|derived from)\s+[^\]]+\]",
    re.IGNORECASE,
)

ANSWER_REF_RE = re.compile(r"\bA-\d+\b")
QUESTION_CITE_RE = re.compile(r"\[\s*from\s+(Q-\d+)\s*\]", re.IGNORECASE)

# IMPLICIT_FACT tag validation (Phase 1 / INTV-01).
# These constants support the check_implicit_facts() validator and the
# A-AUTO-NNN parsing extension below. Closed vocabulary mirrors the
# user's locked decision in 01-CONTEXT.md (DEPLOYMENT, SCALE, RUNTIME,
# FRAMEWORK_VERSION, SECURITY, NETWORK, OTHER).
#
# Two regex variants:
#   IMPLICIT_FACT_TAG_RE         — matches the bracketed form '[IMPLICIT_FACT:X]'
#                                  in raw transcript text (closed-vocab scan).
#   IMPLICIT_FACT_TAG_INNER_RE   — matches the un-bracketed form 'IMPLICIT_FACT:X'
#                                  inside tag-list strings after parse_transcript
#                                  has stripped the surrounding brackets and
#                                  comma-split the tag list (validate-spec.py:211).
IMPLICIT_FACT_TAG_RE = re.compile(r"\[IMPLICIT_FACT:([A-Z_]+)\]")
IMPLICIT_FACT_TAG_INNER_RE = re.compile(r"^IMPLICIT_FACT:([A-Z_]+)$")
VALID_IMPLICIT_FACT_CATEGORIES = frozenset({
    "DEPLOYMENT",
    "SCALE",
    "RUNTIME",
    "FRAMEWORK_VERSION",
    "SECURITY",
    "NETWORK",
    "OTHER",
})
A_AUTO_ID_RE = re.compile(r"^A-AUTO-\d+$")
# Parallel regex to ANSWER_BLOCK_RE for A-AUTO-NNN entries (auto-discovered
# implicit facts). The lookahead terminates on Q-NNN, A-NNN, A-AUTO-NNN,
# or any other top-level capitalized heading. ANSWER_BLOCK_RE intentionally
# does NOT match A-AUTO-NNN (its first group is A-\d+ only); this pattern
# fills that gap.
A_AUTO_BLOCK_RE = re.compile(
    r"^##\s+(A-AUTO-\d+)"
    r"(?:\s*\[([^\]]*)\])?"
    r"(?:\s*\(([^)]*)\))?"
    r"\s*\n(.*?)"
    r"(?=^##\s+[AQ]-\d+|^##\s+A-AUTO-\d+|^##\s+[A-Z]|\Z)",
    re.MULTILINE | re.DOTALL,
)

# Typed-section validation (Phase 2 / TYPE-01).
# These constants support the check_typed_sections() validator which enforces
# the three TYPE-01 rules on the typed tables emitted at R3 SPEC FORGED:
#   1. Presence — `## Global Invariants` / `## State Transitions` /
#      `## Contracts` headings each carry a markdown table with the documented
#      column count + at least one data row or a sentinel row. Phase 2 ships
#      this rule as report.warn() (TYPE_TABLES_MISSING) for backwards-compat;
#      Phase 3 (TYPE-02) upgrades to report.fail() when
#      spec_format_version >= v2.1.
#   2. Citation integrity — every row's citation cell parses as
#      `[from A-NNN]` (Locked form ONLY); cited A-NNN exists in the
#      transcript; row's `statement` quotes verbatim from the cited body.
#   3. Content-difference — each non-sentinel row's content tokens have
#      Jaccard < 0.7 vs. the same `## ` section's prose tokens (rejects
#      paraphrase-from-prose fabrication).
TYPED_SECTION_HEADINGS = ("Global Invariants", "State Transitions", "Contracts")

# Phase 3 / TYPE-02: spec_format_version frontmatter constants.
# Strict allowlist (closed-vocabulary discipline parallel to Phase 1's
# VALID_IMPLICIT_FACT_CATEGORIES and Phase 2's TYPED_SECTION_HEADINGS).
# Bumping a version requires (a) editing this allowlist, (b) editing
# setup-forge.sh's SPEC TEMPLATE literal, (c) re-running the
# cross-script alignment test in test_versioned_alignment.py.
KNOWN_SPEC_FORMAT_VERSIONS: tuple[str, ...] = ("v2.0", "v2.1")
IMPLICIT_DEFAULT_SPEC_FORMAT_VERSION = "v2.0"
LATEST_SPEC_FORMAT_VERSION = "v2.1"

_VERSION_TUPLE_RE = re.compile(r"^v(\d+)\.(\d+)$")
FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.DOTALL)
_FRONTMATTER_SPEC_VERSION_RE = re.compile(
    r"^\s*spec_format_version\s*:\s*"
    r"(?:\"([^\"\n]+)\"|'([^'\n]+)'|(\S+))"
    r"\s*(?:#.*)?\s*$",
    re.MULTILINE,
)


def _parse_version_tuple(literal: str) -> tuple[int, int]:
    """Parse 'v2.1' to (2, 1). Raises ValueError on shape mismatch."""
    m = _VERSION_TUPLE_RE.match(literal.strip())
    if not m:
        raise ValueError(f"Malformed spec_format_version literal: {literal!r}")
    return (int(m.group(1)), int(m.group(2)))


_KNOWN_VERSION_TUPLES: dict[str, tuple[int, int]] = {
    v: _parse_version_tuple(v) for v in KNOWN_SPEC_FORMAT_VERSIONS
}

INVARIANTS_TABLE_COLUMNS = (
    "ID",
    "statement",
    "applies-to",
    "violation",
    "citation",
)
STATE_TRANSITIONS_TABLE_COLUMNS = (
    "ID",
    "from-state",
    "to-state",
    "trigger",
    "guard",
    "citation",
)
CONTRACTS_TABLE_COLUMNS = (
    "ID",
    "surface",
    "input",
    "output",
    "errors",
    "citation",
)

# Sentinel detection — match "None — ..." or "None - ..." in any non-ID,
# non-citation content cell. Permissive cell-level detection because the
# sentinel may appear in different cells across the three tables (e.g.,
# contracts sentinel lands in `surface` column; state-transitions sentinel
# lands in `trigger` column).
TYPED_SENTINEL_RE = re.compile(r"^\s*[Nn]one\s*[—\-]\s+.+")

# Locked-only citation form (Phase 2 rule 2). Matches the strict
# `[from A-NNN]` shape. The existing CITATION_RE is too permissive (it
# accepts `[derived from ...]`) so this regex is independently maintained.
# Rejects: `[derived from A-NNN]`, `[from survey/...]`, `[from R1.5 research]`,
# `[from A-AUTO-NNN]`.
TYPED_ROW_CITATION_RE = re.compile(r"^\s*\[from\s+(A-\d+)\s*\]\s*$")

# Jaccard content-difference threshold (rule 3). Hard-coded per CONTEXT.md;
# configurable threshold deferred to v2+.
JACCARD_REJECTION_THRESHOLD = 0.7

# Stop-word list for Jaccard tokenization — small fixed English list per
# CONTEXT.md "Content-difference scope". Domain-specific stop-words deferred.
_TYPED_STOP_WORDS = frozenset(
    {
        "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
        "of", "in", "on", "at", "to", "for", "with", "by", "from", "as",
        "and", "or", "but", "if", "then", "else", "this", "that", "these",
        "those",
    }
)

QUOTED_STRING_RE = re.compile(r'"([^"\n]{3,})"')

LOCKED_ITEM_ID_RE = re.compile(
    r"^\s*[-*]\s+\*\*(FR-\d+|NFR-\d+|AC-\d+|GI-\d+|US-\d+|OT-\d+)\*\*",
    re.MULTILINE,
)

SECTION_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*$", re.MULTILINE)

# Sections whose bullets / table rows MUST carry a citation marker.
# Matched case-insensitively on the heading text (trimmed).
# Implementation Phases is EXCLUDED — its bullets trace via "implements [FR-NNN]"
# requirement references, not direct transcript citations, and the requirements
# themselves already carry the fidelity burden.
REQUIRED_CITATION_SECTIONS = {
    "problem statement",
    "scope",
    "user stories",
    "functional requirements",
    "non-functional requirements",
    "global invariants",
    "technical design",
    "file change map",
    "observable truths",
    "codebase references",
    # Phase 2 / TYPE-01: typed-section tables also enforce per-row citation
    # discipline via the existing _line_has_traceable_marker logic. The
    # row-scoped TYPED_ROW_BAD_CITATION check in check_typed_sections is
    # stricter (Locked-only `[from A-NNN]` form), but inheriting the generic
    # citation discipline catches accidentally-uncited rows uniformly.
    "state transitions",
    "contracts",
}

# Sentinels and scaffolding that do NOT need citations.
SENTINEL_LINES = {
    "none — the user gave no explicit placement constraints.",
    "none - the user gave no explicit placement constraints.",
}

# Sub-field prefixes. Lines starting with any of these (after stripping the
# bullet marker) are treated as sub-fields of their parent bullet and inherit
# the parent's citation — no independent citation required.
SUBFIELD_PREFIXES = (
    "**verification:**",
    "**depends on:**",
    "**source answers",
    "**acceptance criteria:**",
    "**codebase integration",
    "**current state**",
    "**proposed changes:**",
    "**new endpoints:**",
    "**modified endpoints:**",
    "**pattern to follow**",
    "**component diagram:**",
    "**dependency flow:**",
    "**error cases for this feature:**",
    "**claude's gloss",
    "maps to:",
    "maps to ",
    "applies to:",
    "applies to ",
    "violation looks like:",
    "violation looks like ",
    "extends:",
    "extends ",
    "follows pattern:",
    "follows pattern ",
    "new files:",
    "new files ",
    "modifies:",
    "modifies ",
    "migration strategy:",
    "migration strategy ",
)

# A line that starts with `**As a**` is the User Story narrative line —
# its parent US heading already carries the citation.
US_NARRATIVE_PREFIX = "**as a**"


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class Answer:
    id: str
    body: str
    tags: list[str] = field(default_factory=list)
    label: str = ""
    normalized_body: str = ""


@dataclass
class Report:
    failures: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    # Track which item IDs have already been flagged NOT_VERBATIM so the
    # Locked check and the opportunistic check don't both fire on the
    # same bullet.
    verbatim_violated: set[str] = field(default_factory=set)

    def fail(self, msg: str) -> None:
        self.failures.append(msg)

    def warn(self, msg: str) -> None:
        self.warnings.append(msg)


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def parse_transcript(text: str) -> dict[str, Answer]:
    """Return {A-NNN: Answer(id, body, tags, label, normalized_body)}.

    Heading forms accepted (groups: id, tags, label, body):
        ## A-001
        ## A-001 [ARCH_INVARIANT]
        ## A-001 (readiness path)
        ## A-001 [ARCH_INVARIANT] (readiness path)
    """
    answers: dict[str, Answer] = {}
    for match in ANSWER_BLOCK_RE.finditer(text):
        aid = match.group(1)
        tag_blob = match.group(2) or ""
        label = (match.group(3) or "").strip()
        body = match.group(4).strip()
        tags = [t.strip() for t in tag_blob.split(",") if t.strip()]
        answers[aid] = Answer(
            id=aid,
            body=body,
            tags=tags,
            label=label,
            normalized_body=normalize_for_compare(body),
        )
    # A-AUTO-NNN entries (auto-discovered implicit facts) — Phase 1 / INTV-01.
    # Stored alongside A-NNN entries so check_implicit_facts can validate
    # well-formedness; check_coverage exempts them from UNCITED_ANSWERS.
    for match in A_AUTO_BLOCK_RE.finditer(text):
        auto_id = match.group(1)
        tag_blob = match.group(2) or ""
        label = (match.group(3) or "").strip()
        body = match.group(4).strip()
        tags = [t.strip() for t in tag_blob.split(",") if t.strip()]
        answers[auto_id] = Answer(
            id=auto_id,
            body=body,
            tags=tags,
            label=label,
            normalized_body=normalize_for_compare(body),
        )
    return answers


# ---------------------------------------------------------------------------
# Verbatim normalization
# ---------------------------------------------------------------------------

# Map of "fuzzy-equivalent" character pairs. The verbatim check normalizes
# both the spec quote and the transcript answer through this table before
# substring comparison so cosmetic differences (smart quotes from copy-paste,
# em-dash vs hyphen, NBSP) don't fail an otherwise-faithful quote.
_UNICODE_NORMALIZE = {
    "\u2018": "'",  # left single quote
    "\u2019": "'",  # right single quote / apostrophe
    "\u201c": '"',  # left double quote
    "\u201d": '"',  # right double quote
    "\u2013": "-",  # en dash
    "\u2014": "-",  # em dash
    "\u2212": "-",  # minus sign
    "\u00a0": " ",  # non-breaking space
    "\u202f": " ",  # narrow no-break space
    "\u2009": " ",  # thin space
    "\u200b": "",   # zero-width space
}

_WHITESPACE_RUN_RE = re.compile(r"\s+")


def normalize_for_compare(text: str) -> str:
    """Lowercase-preserving normalization for verbatim quote comparison.
    Collapses whitespace runs, normalizes unicode punctuation, strips ends.
    Preserves case (the verbatim contract is byte-faithful, but cosmetic
    differences like smart quotes shouldn't break it).
    """
    out = text
    for src, dst in _UNICODE_NORMALIZE.items():
        out = out.replace(src, dst)
    out = _WHITESPACE_RUN_RE.sub(" ", out)
    return out.strip()


def _translate_unicode_chars(text: str) -> str:
    """Apply the _UNICODE_NORMALIZE table without whitespace collapse.

    Unlike normalize_for_compare (which collapses whitespace runs to a
    single space), this helper preserves line structure — required for
    YAML frontmatter parsing where newlines are load-bearing
    (RESEARCH.md Pitfall 1).
    """
    out = text
    for src, dst in _UNICODE_NORMALIZE.items():
        out = out.replace(src, dst)
    return out


def split_spec_body_appendix(text: str) -> tuple[str, str | None]:
    """Return (body, appendix) — appendix is None if no appendix section."""
    match = APPENDIX_HEADING_RE.search(text)
    if not match:
        return text, None
    return text[: match.start()], text[match.start() :]


def iter_sections(text: str) -> Iterable[tuple[str, int, int]]:
    """Yield (heading_text, start_of_body, end_of_section) for every `## ` section.

    `## ` headings only (not `###` subsections). Body starts after the heading
    line and extends until the next `## ` or end-of-text.
    """
    matches = list(re.finditer(r"^##\s+(.+?)\s*$", text, re.MULTILINE))
    for i, m in enumerate(matches):
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        yield m.group(1).strip(), start, end


def extract_section(text: str, heading_name: str) -> str | None:
    """Extract the body of a `## {heading_name}` section (case-insensitive)."""
    target = heading_name.strip().lower()
    for name, start, end in iter_sections(text):
        if name.lower().startswith(target):
            return text[start:end]
    return None


def extract_frontmatter(text: str) -> dict[str, str]:
    """Extract YAML-style frontmatter block from top of text.

    Returns {} if absent. Tolerates UTF-8 BOM (RESEARCH.md Pitfall 2),
    smart quotes / NBSP / em-dash via _translate_unicode_chars
    (RESEARCH.md Pitfall 1), trailing whitespace on `---` delimiters,
    single/double quoted scalars, trailing `# comment` after value.

    Phase 3 reads exactly one key (spec_format_version). Permissive on
    unknown keys — forge may add other frontmatter fields in future.
    """
    if text.startswith("﻿"):
        text = text[1:]
    m = FRONTMATTER_RE.match(text)
    if not m:
        return {}
    block_body = _translate_unicode_chars(m.group(1))
    out: dict[str, str] = {}
    kv = _FRONTMATTER_SPEC_VERSION_RE.search(block_body)
    if kv:
        value = kv.group(1) or kv.group(2) or kv.group(3)
        out["spec_format_version"] = value.strip()
    return out


# ---------------------------------------------------------------------------
# Typed-section helpers (Phase 2 / TYPE-01)
# ---------------------------------------------------------------------------

# Punctuation-strip translation table for _tokenize. Built once at module
# import; reused for every row-cell and prose tokenization in
# check_typed_sections.
_TYPED_PUNCT_TABLE = str.maketrans("", "", string.punctuation)


def _iter_table_rows(section_body: str) -> Iterable[list[str]]:
    """Yield each markdown table data row as list[cell_text].

    Skips the header row (first row containing only column names) and the
    separator row (`|---|---|`). Strips leading/trailing pipes and whitespace
    from each cell. Returns an empty iterator if no markdown table is present.

    Used by check_typed_sections to walk each typed section's table without
    pulling in an external markdown parser. Phase 1 / RESEARCH.md "Don't
    Hand-Roll" notes the existing iter_sections + regex approach handles the
    in-repo `## Technical Design` tables correctly; this helper reuses the
    same shape at row granularity.
    """
    in_table = False
    for line in section_body.splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            in_table = False
            continue
        # Separator row: "|---|---|" (any combination of -, :, |, whitespace)
        if re.match(r"^\|[\s|:\-]+\|$", stripped):
            in_table = True
            continue
        if not in_table:
            # Header row before the separator — skip silently.
            continue
        cells = [c.strip() for c in stripped.strip("|").split("|")]
        yield cells


def _tokenize(text: str) -> frozenset[str]:
    """Lowercase + punctuation-strip + split + stop-word remove + dedupe.

    Used by Jaccard rule 3 — both row content cells and section prose are
    tokenized through this helper to ensure consistent comparison
    (RESEARCH.md Pitfall 6 — tokenizer inconsistency across cells/prose).
    """
    cleaned = text.translate(_TYPED_PUNCT_TABLE).lower()
    return frozenset(
        w for w in cleaned.split() if w and w not in _TYPED_STOP_WORDS
    )


def _jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    """Jaccard similarity — empty union is vacuously 0 (rule 3 satisfied)."""
    if not a and not b:
        return 0.0
    union = a | b
    if not union:
        return 0.0
    return len(a & b) / len(union)


def _strip_table_lines(section_body: str) -> str:
    """Return section_body with markdown table lines, headings, blockquotes,
    and bullet items removed — used to compute the prose token-set for rule 3.

    Bullets (e.g., Locked `**GI-NNN**` items) are themselves transcript-cited
    structural data, not free-form prose. Including them in the "adjacent
    prose" token-set would dilute the Jaccard union and mask real paraphrase
    violations from genuine free-form prose paragraphs the rule is designed
    to catch.
    """
    lines = []
    for line in section_body.splitlines():
        stripped = line.strip()
        if stripped.startswith("|"):
            continue  # table row (header, separator, or data)
        if stripped.startswith("#"):
            continue  # heading
        if stripped.startswith(">"):
            continue  # blockquote
        if stripped.startswith(("-", "*")):
            continue  # bullet item — Locked structural data, not prose
        lines.append(line)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Checks — each returns nothing and appends to Report
# ---------------------------------------------------------------------------


def check_transcript_sanity(
    answers: dict[str, Answer], report: Report
) -> None:
    if len(answers) < MIN_TRANSCRIPT_ANSWERS:
        report.fail(
            f"TRANSCRIPT_TOO_SHALLOW: transcript has {len(answers)} "
            f"A-NNN answers, need ≥{MIN_TRANSCRIPT_ANSWERS}"
        )


def check_batched_headings(
    transcript_text: str, report: Report
) -> None:
    """Forbid `## A-005..A-008` and `## A-005, A-006` style batched headings.
    Each answer must have its own block — the parser cannot disambiguate
    which body belongs to which ID, and the spec citations expect single IDs.
    """
    for match in BATCHED_ANSWER_HEADING_RE.finditer(transcript_text):
        line = match.group(0).rstrip()
        report.fail(
            f"BATCHED_ANSWER_HEADING: transcript heading '{line}' batches "
            f"multiple A-NNN IDs into one block. Split into separate "
            f"`## A-NNN` headings — one per answer — and re-append the "
            f"transcript appendix in the spec. The parser cannot tell which "
            f"answer body belongs to which ID when they're batched."
        )


def check_structure(
    spec_text: str,
    body: str,
    appendix: str | None,
    transcript_answers: dict[str, Answer],
    report: Report,
) -> None:
    if not GLOBAL_INVARIANTS_HEADING_RE.search(spec_text):
        report.fail(
            "MISSING_SECTION: '## Global Invariants' not found in spec. "
            "Foundry decompose needs this to propagate architectural "
            "constraints into every casting."
        )

    if appendix is None:
        report.fail(
            "MISSING_APPENDIX: '## Appendix: Interview Transcript' not found "
            "in spec. Embed transcript.md verbatim at finalization."
        )
        return

    # Count A-NNN blocks inside the appendix (not body) and compare to
    # transcript.md. If the appendix is truncated, fail. A-AUTO-NNN entries
    # (Phase 1 / INTV-01) are counted via the parallel A_AUTO_BLOCK_RE — they
    # live in transcript_answers alongside A-NNN, so the appendix must
    # contain them too.
    appendix_answer_ids = set(
        m.group(1) for m in ANSWER_BLOCK_RE.finditer(appendix)
    )
    appendix_answer_ids.update(
        m.group(1) for m in A_AUTO_BLOCK_RE.finditer(appendix)
    )
    transcript_ids = set(transcript_answers.keys())
    missing_from_appendix = transcript_ids - appendix_answer_ids
    if missing_from_appendix:
        report.fail(
            f"APPENDIX_INCOMPLETE: transcript has "
            f"{len(transcript_ids)} answers but appendix contains "
            f"only {len(appendix_answer_ids)}. "
            f"Missing: {sorted(missing_from_appendix)[:10]}"
            + (" ..." if len(missing_from_appendix) > 10 else "")
        )


def check_locked_fidelity(
    body: str, transcript_answers: dict[str, Answer], report: Report
) -> None:
    """Locked items must have a quoted substring that is byte-identical to
    a range inside the cited transcript answer.
    """
    # A Locked item lives inside `### Locked (...)` or inside Global Invariants
    # (every GI is implicitly Locked). We scan the body for bullets whose ID
    # prefix is FR/NFR/AC/GI AND which sit under a Locked heading OR inside
    # the Global Invariants section. For simplicity we treat EVERY bullet with
    # `**FR-N**` / `**NFR-N**` / `**AC-N**` / `**GI-N**` under a "Locked"
    # subheading, AND every `**GI-N**` anywhere, as Locked.
    locked_bullets = _collect_locked_bullets(body)
    for bullet in locked_bullets:
        _check_single_locked(bullet, transcript_answers, report)


def _collect_locked_bullets(body: str) -> list[tuple[str, str, str]]:
    """Return list of (id, full_bullet_text, section_name) for Locked items."""
    bullets: list[tuple[str, str, str]] = []
    # Walk ## sections, then within each find ### Locked subsections,
    # then extract bullets. Also pull every GI-NNN bullet regardless of
    # Locked framing (GIs are always Locked).
    for section_name, start, end in iter_sections(body):
        section = body[start:end]

        # Direct GI bullets (Global Invariants = implicitly Locked)
        if section_name.lower().startswith("global invariants"):
            for b in _iter_bullets(section):
                if re.search(r"\*\*GI-\d+\*\*", b):
                    bullets.append(("GI", b, section_name))

        # ### Locked subsections
        sub_matches = list(
            re.finditer(r"^###\s+Locked\b.*?$", section, re.MULTILINE)
        )
        for i, sm in enumerate(sub_matches):
            sub_start = sm.end()
            # end of this subsection: next ### or end of parent
            next_sub = re.search(r"^###\s+", section[sub_start:], re.MULTILINE)
            sub_end = (
                sub_start + next_sub.start()
                if next_sub is not None
                else len(section)
            )
            sub_body = section[sub_start:sub_end]
            for b in _iter_bullets(sub_body):
                id_match = re.search(r"\*\*((?:FR|NFR|AC|GI)-\d+)\*\*", b)
                if id_match:
                    bullets.append((id_match.group(1), b, section_name))
    return bullets


def _iter_bullets(text: str) -> Iterable[str]:
    """Yield full bullet strings (including multi-line continuations)."""
    lines = text.splitlines()
    i = 0
    current: list[str] = []
    bullet_indent: int | None = None
    while i < len(lines):
        line = lines[i]
        # Start of a bullet
        m = re.match(r"^(\s*)([-*])\s+", line)
        if m:
            if current:
                yield "\n".join(current)
                current = []
            bullet_indent = len(m.group(1))
            current.append(line)
        elif current is not None and line.strip() == "":
            # Blank line — keep accumulating if the next non-blank is still
            # indented under this bullet; for simplicity we end bullets on
            # blank lines unless followed by an indented continuation.
            # Conservative: break here.
            yield "\n".join(current)
            current = []
            bullet_indent = None
        elif current:
            # Continuation if more indented than the bullet marker
            stripped = line.lstrip()
            indent = len(line) - len(stripped)
            if bullet_indent is not None and indent > bullet_indent:
                current.append(line)
            else:
                yield "\n".join(current)
                current = []
                bullet_indent = None
                # Re-process this line as potential new bullet
                continue
        i += 1
    if current:
        yield "\n".join(current)


def _check_single_locked(
    bullet: tuple[str, str, str],
    transcript_answers: dict[str, Answer],
    report: Report,
) -> None:
    item_id, text, section = bullet
    # L1: has quoted string
    quotes = QUOTED_STRING_RE.findall(text)
    if not quotes:
        report.fail(
            f"LOCKED_NO_QUOTE: {item_id} in '{section}' has no double-quoted "
            f"user-verbatim substring. Either add the user's literal words "
            f"inside quotes or re-classify as Flexible."
        )
        return
    # L2: has [from A-NNN] marker
    citation_match = re.search(
        r"\[from\s+((?:A-\d+)(?:\s*,\s*A-\d+)*)\s*\]", text, re.IGNORECASE
    )
    if not citation_match:
        report.fail(
            f"LOCKED_NO_CITATION: {item_id} in '{section}' has no "
            f"[from A-NNN] marker."
        )
        return
    cited_ids = re.findall(r"A-\d+", citation_match.group(1))
    # L3: citation resolves
    unresolved = [cid for cid in cited_ids if cid not in transcript_answers]
    if unresolved:
        report.fail(
            f"DANGLING_CITATION: {item_id} cites {unresolved} but "
            f"transcript has no such answer(s). This is hallucination."
        )
        return
    # L4: at least one quoted string is a substring of some cited answer's
    # body, after whitespace + unicode normalization. Cosmetic differences
    # (smart quotes, em dash, line wrap) don't fail; semantic paraphrase does.
    matched_any = False
    for quote in quotes:
        nq = normalize_for_compare(quote)
        for cid in cited_ids:
            if nq in transcript_answers[cid].normalized_body:
                matched_any = True
                break
        if matched_any:
            break
    if not matched_any:
        preview = quotes[0][:120].replace("\n", " ")
        cited_previews = "\n".join(
            f"      {cid}: {transcript_answers[cid].body[:200]!r}"
            + ("..." if len(transcript_answers[cid].body) > 200 else "")
            for cid in cited_ids
        )
        report.fail(
            f"NOT_VERBATIM: {item_id} quotes '{preview}'\n"
            f"    not found in cited answer(s):\n{cited_previews}\n"
            f"    Fix: copy the user's actual words from the transcript "
            f"into the quote (whitespace and smart-quote differences are "
            f"already normalized — this is a real semantic mismatch). "
            f"Or re-classify the item as Flexible."
        )
        report.verbatim_violated.add(item_id)


def check_opportunistic_fidelity(
    body: str, transcript_answers: dict[str, Answer], report: Report
) -> None:
    """Any line in the spec body containing BOTH a double-quoted substring
    AND a `[from A-NNN]` marker is subject to verbatim check, even if the
    line isn't in a `### Locked` subsection. This catches AC-NNN bullets
    nested inside User Stories, ad-hoc quoted claims in Technical Design,
    etc. Lines with `[derived from A-NNN]` and a quote are flexible —
    the quote is illustrative, not verbatim — so this check only applies
    to `[from A-NNN]` lines.
    """
    from_cite_pattern = re.compile(
        r"\[from\s+((?:A-\d+)(?:\s*,\s*A-\d+)*)\s*\]", re.IGNORECASE
    )
    for line in body.splitlines():
        from_match = from_cite_pattern.search(line)
        if not from_match:
            continue
        quotes = QUOTED_STRING_RE.findall(line)
        if not quotes:
            continue
        cited_ids = re.findall(r"A-\d+", from_match.group(1))
        unresolved = [c for c in cited_ids if c not in transcript_answers]
        if unresolved:
            # Already reported by check_dangling_refs; skip here.
            continue
        matched = False
        for quote in quotes:
            nq = normalize_for_compare(quote)
            for cid in cited_ids:
                if nq in transcript_answers[cid].normalized_body:
                    matched = True
                    break
            if matched:
                break
        if not matched:
            id_match = re.search(
                r"\*\*((?:FR|NFR|AC|GI|US|OT)-\d+)\*\*", line
            )
            label = id_match.group(1) if id_match else "line"
            # Skip if the Locked check already flagged this item
            if label in report.verbatim_violated:
                continue
            preview = quotes[0][:120].replace("\n", " ")
            cited_previews = "\n".join(
                f"      {cid}: {transcript_answers[cid].body[:200]!r}"
                + ("..." if len(transcript_answers[cid].body) > 200 else "")
                for cid in cited_ids
            )
            report.fail(
                f"NOT_VERBATIM: {label} quotes '{preview}'\n"
                f"    not found in cited answer(s):\n{cited_previews}\n"
                f"    Fix: copy the user's actual words from the transcript "
                f"into the quote. Whitespace and smart-quote differences are "
                f"already normalized — this is a real semantic mismatch."
            )
            report.verbatim_violated.add(label)


def check_universal_citations(body: str, report: Report) -> None:
    """Every bullet / table row in REQUIRED_CITATION_SECTIONS must contain a
    traceable marker (or be an allowed sentinel / scaffolding line).
    """
    for section_name, start, end in iter_sections(body):
        name_norm = section_name.strip().lower()
        if name_norm not in REQUIRED_CITATION_SECTIONS:
            continue
        section = body[start:end]
        for line_num, line in enumerate(section.splitlines(), 1):
            if not _line_requires_citation(line):
                continue
            if not _line_has_traceable_marker(line):
                preview = line.strip()[:100]
                report.fail(
                    f"UNSOURCED_BULLET: section '{section_name}' line "
                    f"{line_num}: {preview}"
                )


def _line_requires_citation(line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return False
    # Headings
    if stripped.startswith("#"):
        return False
    # Blockquotes (guidance lines the template itself emits)
    if stripped.startswith(">"):
        return False
    # Horizontal rules
    if stripped.startswith("---"):
        return False
    # Sentinels
    if stripped.lower() in SENTINEL_LINES:
        return False
    # Table rows
    if stripped.startswith("|"):
        # Separator rows
        if set(stripped.replace("|", "").strip()) <= set("-: "):
            return False
        # Header row detection is heuristic: treat rows whose cells are all
        # short labels (≤3 words, no digits) as headers.
        cells = [c.strip() for c in stripped.strip("|").split("|")]
        if cells and all(
            len(c.split()) <= 3 and not any(ch.isdigit() for ch in c)
            for c in cells
        ):
            return False
        return True
    # Strip bullet marker before scaffolding/sub-field checks
    bullet_match = re.match(r"^[-*]\s+\[?\s*[ x]?\s*\]?\s*", stripped)
    content = stripped[bullet_match.end() :] if bullet_match else stripped
    content_low = content.lower()
    # Sub-field prefixes — these are elaborations of the parent bullet
    if any(content_low.startswith(p) for p in SUBFIELD_PREFIXES):
        return False
    # User Story narrative line
    if content_low.startswith(US_NARRATIVE_PREFIX):
        return False
    # List items (after sub-field exclusion) → citation required
    if bullet_match:
        return True
    # Plain paragraph lines inside a required section
    return True


def _line_has_traceable_marker(line: str) -> bool:
    """A line is traceable if it has EITHER a [from/derived from] citation
    OR a bare A-NNN reference OR a bracketed requirement ID reference
    ([FR-NNN], [GI-NNN], [US-NNN], [OT-NNN], [NFR-NNN], [AC-NNN]).
    """
    if CITATION_RE.search(line):
        return True
    if ANSWER_REF_RE.search(line):
        return True
    if re.search(r"\[(?:FR|NFR|AC|GI|US|OT)-\d+", line):
        return True
    return False


def check_dangling_refs(
    body: str, transcript_answers: dict[str, Answer], report: Report
) -> None:
    """Every A-NNN referenced inside a citation marker in the body must exist
    in the transcript.
    """
    seen_bad: set[tuple[str, str]] = set()
    for cite_match in CITATION_RE.finditer(body):
        cite_text = cite_match.group(0)
        for aid_match in ANSWER_REF_RE.finditer(cite_text):
            aid = aid_match.group(0)
            if aid not in transcript_answers:
                key = (aid, cite_text)
                if key in seen_bad:
                    continue
                seen_bad.add(key)
                report.fail(
                    f"DANGLING_CITATION: spec body cites {aid} in "
                    f"'{cite_text}' but transcript has no such answer."
                )


def check_no_question_citations(body: str, report: Report) -> None:
    for m in QUESTION_CITE_RE.finditer(body):
        report.fail(
            f"CITES_QUESTION: spec contains '[from {m.group(1)}]'. "
            f"Citations must point at answers (A-NNN), not questions."
        )


def check_arch_invariants_populated(
    body: str, transcript_answers: dict[str, Answer], report: Report
) -> None:
    arch_tagged = [
        aid
        for aid, ans in transcript_answers.items()
        if any("ARCH_INVARIANT" in t for t in ans.tags)
    ]
    if not arch_tagged:
        return
    gi_section = extract_section(body, "Global Invariants")
    if gi_section is None:
        # Already reported by check_structure; don't double-fail here.
        return
    gi_entries = re.findall(
        r"^\s*[-*]\s+\*\*GI-\d+\*\*", gi_section, re.MULTILINE
    )
    if not gi_entries:
        report.fail(
            f"MISSING_GI_ENTRIES: transcript has "
            f"{len(arch_tagged)} ARCH_INVARIANT-tagged answer(s) "
            f"({arch_tagged[:5]}) but Global Invariants section has no "
            f"GI-NNN entries. Extract the placement rules from those answers."
        )


def check_coverage(
    body: str, transcript_answers: dict[str, Answer], report: Report
) -> None:
    """Every A-NNN in the transcript must be cited somewhere in the spec
    body (not counting the embedded appendix). Uncited answers mean the
    model dropped interview content on the floor.

    A-AUTO-NNN entries are auto-discovered context (sourced from reality.md
    or survey files), not user-answered requirements; they are NOT required
    to be cited in the spec body. INTENT-01 (Phase 8) reads them directly
    from the transcript. (Phase 1 / INTV-01 — see RESEARCH.md §Open Questions #2.)
    """
    cited: set[str] = set()
    for cite_match in CITATION_RE.finditer(body):
        for aid_match in ANSWER_REF_RE.finditer(cite_match.group(0)):
            cited.add(aid_match.group(0))
    # Filter out A-AUTO-NNN before computing coverage diff — auto-facts are
    # exempt from the UNCITED_ANSWERS check.
    user_answered_keys = {
        aid for aid in transcript_answers if not A_AUTO_ID_RE.match(aid)
    }
    uncited = sorted(user_answered_keys - cited)
    if uncited:
        report.fail(
            f"UNCITED_ANSWERS: {len(uncited)} transcript answer(s) are never "
            f"cited in the spec body: {uncited[:10]}"
            + (" ..." if len(uncited) > 10 else "")
            + ". Either cite them in a relevant section, add to Informational "
            f"with a note, or remove them from the transcript if the user "
            f"retracted them."
        )


def check_spec_format_version(body: str, report: Report) -> tuple[int, int]:
    """Parse spec_format_version from body's frontmatter (Phase 3 / TYPE-02).

    Returns the parsed (major, minor) tuple, defaulting to
    IMPLICIT_DEFAULT_SPEC_FORMAT_VERSION's tuple when frontmatter
    is absent or the field is not declared. Hard-fails via report
    with SPEC_FORMAT_VERSION_UNKNOWN if the declared version is
    outside KNOWN_SPEC_FORMAT_VERSIONS.

    Plan 03-02 lands the parser, default behavior, and the
    SPEC_FORMAT_VERSION_UNKNOWN allowlist hard-fail emission.
    Plan 03-03 wires the warn→fail predicate at the IMPLICIT_FACT_SKIPPED
    and TYPE_TABLES_MISSING sites using the version tuple this function
    returns.
    """
    fm = extract_frontmatter(body)
    declared = fm.get("spec_format_version")
    if declared is None:
        return _KNOWN_VERSION_TUPLES[IMPLICIT_DEFAULT_SPEC_FORMAT_VERSION]
    if declared not in _KNOWN_VERSION_TUPLES:
        report.fail(
            f"SPEC_FORMAT_VERSION_UNKNOWN: spec declared "
            f"spec_format_version={declared!r} which is not in the "
            f"known allowlist {sorted(KNOWN_SPEC_FORMAT_VERSIONS)}. "
            f"Phase 3 / TYPE-02 enforces a strict allowlist; bumping "
            f"a version requires editing KNOWN_SPEC_FORMAT_VERSIONS "
            f"in validate-spec.py AND the SPEC TEMPLATE literal in "
            f"setup-forge.sh together (cross-script alignment is "
            f"covered by test_versioned_alignment.py)."
        )
        # Return implicit default so downstream checks still run with
        # warn-level semantics (defensive — main() still returns
        # non-zero because report.fail was called).
        return _KNOWN_VERSION_TUPLES[IMPLICIT_DEFAULT_SPEC_FORMAT_VERSION]
    return _KNOWN_VERSION_TUPLES[declared]


def check_implicit_facts(
    transcript_text: str,
    transcript_answers: dict[str, Answer],
    report: Report,
    *,
    spec_version_tuple: tuple[int, int] = (2, 0),  # Plan 03-02 added; Plan 03-03 uses
) -> None:
    """IMPLICIT_FACT tag validation (Phase 1 / INTV-01).

    Three rules enforce the implicit-fact extraction contract:

      1. Closed vocabulary — every [IMPLICIT_FACT:CATEGORY] tag in the
         transcript text uses a known category from
         VALID_IMPLICIT_FACT_CATEGORIES. Unknown categories fail with
         UNKNOWN_IMPLICIT_FACT_CATEGORY.

      2. A-AUTO-NNN well-formedness — every A-AUTO-NNN entry must have an
         [IMPLICIT_FACT:CATEGORY] tag in its heading AND a [from <source>]
         citation in its body. Missing tag fails with A_AUTO_MISSING_TAG;
         missing citation fails with A_AUTO_MISSING_CITATION.

      3. Non-skippable extraction — if the transcript has any answers, at
         least one entry (A-NNN or A-AUTO-NNN) must carry an
         [IMPLICIT_FACT:CATEGORY] tag, otherwise the R1.75 sub-step did
         not attempt elicitation.

    Phase 1 ↔ Phase 3 coordination: rule 3 ships as report.warn() (NOT
    report.fail()) to preserve v4.2.0 backwards-compatibility — legacy
    transcripts predating this phase have no IMPLICIT_FACT tags and must
    continue to validate (exit 0). Phase 3 (TYPE-02) introduces
    spec_format_version frontmatter and upgrades this warning to a hard
    failure when spec_format_version >= v2.1. To find the upgrade site,
    grep for IMPLICIT_FACT_SKIPPED in this file.
    """
    # Rule 1: Closed-vocabulary check — scan whole transcript text.
    seen_unknown: set[str] = set()
    for match in IMPLICIT_FACT_TAG_RE.finditer(transcript_text):
        category = match.group(1)
        if category not in VALID_IMPLICIT_FACT_CATEGORIES:
            if category in seen_unknown:
                continue
            seen_unknown.add(category)
            report.fail(
                f"UNKNOWN_IMPLICIT_FACT_CATEGORY: '[IMPLICIT_FACT:{category}]' "
                f"uses unknown category. Valid categories: "
                f"{sorted(VALID_IMPLICIT_FACT_CATEGORIES)}. "
                f"Use OTHER as escape hatch and name the actual category in "
                f"the entry body (e.g., 'data residency: EU-only')."
            )

    # Rule 2: A-AUTO-NNN well-formedness.
    # Note: ans.tags contains un-bracketed strings (parse_transcript strips the
    # surrounding [] and comma-splits). Use IMPLICIT_FACT_TAG_INNER_RE to match
    # the un-bracketed 'IMPLICIT_FACT:X' form, NOT IMPLICIT_FACT_TAG_RE which
    # expects the bracketed form found in raw transcript text.
    auto_ids = [aid for aid in transcript_answers if A_AUTO_ID_RE.match(aid)]
    for aid in auto_ids:
        ans = transcript_answers[aid]
        has_implicit_tag = any(
            IMPLICIT_FACT_TAG_INNER_RE.match(t) for t in ans.tags
        )
        if not has_implicit_tag:
            report.fail(
                f"A_AUTO_MISSING_TAG: {aid} is auto-discovered but has no "
                f"[IMPLICIT_FACT:CATEGORY] tag in its heading. Auto-facts "
                f"must declare a category from "
                f"{sorted(VALID_IMPLICIT_FACT_CATEGORIES)}."
            )
        has_citation = bool(CITATION_RE.search(ans.body))
        if not has_citation:
            report.fail(
                f"A_AUTO_MISSING_CITATION: {aid} has no [from <source>] "
                f"citation in body. Auto-discovered facts must cite the "
                f"source file or section that determined them (e.g., "
                f"[from survey/architecture.md] or [from R1.5 research])."
            )

    # Rule 3: Non-skippable extraction (WARN in Phase 1, FAIL in Phase 3).
    # Only fires when R2 actually ran (transcript has answers). This is a
    # WARNING not a FAILURE so existing v4.2.0 transcripts (which lack
    # IMPLICIT_FACT tags entirely) continue to validate. Phase 3's TYPE-02
    # plan upgrades this to report.fail() when spec_format_version >= v2.1.
    if transcript_answers:
        any_tagged = any(
            any(IMPLICIT_FACT_TAG_INNER_RE.match(t) for t in ans.tags)
            for ans in transcript_answers.values()
        )
        if not any_tagged:
            # Phase 3 / TYPE-02: severity is gated on spec_format_version.
            # Specs declaring v2.1+ hard-fail; v2.0 / missing-frontmatter
            # specs still warn-only (backwards-compat for legacy v4.2.0
            # specs in dependent projects). The message text is identical
            # between branches so the Phase 3 coordination tokens
            # (spec_format_version, Phase 3, TYPE-02) remain grep-stable.
            _msg = (
                "IMPLICIT_FACT_SKIPPED: transcript has answers but no "
                "[IMPLICIT_FACT:CATEGORY]-tagged entries. The R1.75 "
                "implicit-fact extraction sub-step did not run or did not "
                "emit any auto-facts/user-answered facts. Re-run with R1.75 "
                "active, or add at least one A-AUTO-NNN entry citing "
                "reality.md. NOTE: this is a WARNING in Phase 1 to preserve "
                "v4.2.0 backwards-compatibility; Phase 3 (TYPE-02) upgrades "
                "this to a hard FAILURE for specs declaring "
                "spec_format_version >= v2.1."
            )
            if spec_version_tuple >= (2, 1):
                report.fail(_msg)
            else:
                report.warn(_msg)


def check_typed_sections(
    body: str,
    transcript_answers: dict[str, Answer],
    report: Report,
    *,
    spec_version_tuple: tuple[int, int] = (2, 0),  # Plan 03-02 added; Plan 03-03 uses
) -> None:
    """Typed-section validation (Phase 2 / TYPE-01).

    Three rules enforce the typed-table contract on the three typed sections
    emitted at R3 SPEC FORGED:

      1. Presence — each of `## Global Invariants` / `## State Transitions` /
         `## Contracts` must contain a markdown table with the documented
         column count AND either >=1 data row or exactly 1 sentinel row.
         WARNING in Phase 2 for backwards-compat (TYPE_TABLES_MISSING);
         Phase 3 (TYPE-02) upgrades to FAIL when spec_format_version >= v2.1.

      2. Citation integrity — every row's citation cell parses as
         `[from A-NNN]` (Locked form only); cited A-NNN exists in
         transcript; row's `statement` cell quotes verbatim from the
         cited A-NNN body. Failures: TYPED_ROW_BAD_CITATION /
         TYPED_ROW_DANGLING / TYPED_ROW_NOT_VERBATIM (hard fail).

      3. Content-difference — for each data row (sentinel rows exempt):
         tokenize the row's content cells (excluding ID + citation);
         collect token-set of all non-table prose paragraphs in the same
         `## ` section; reject if Jaccard >= JACCARD_REJECTION_THRESHOLD
         (0.7) with TYPED_ROW_PARAPHRASE (hard fail).

    Phase 2 ↔ Phase 3 coordination: rule 1 ships as report.warn() with
    TYPE_TABLES_MISSING / spec_format_version / Phase 3 / TYPE-02 tokens
    in the warning text. Phase 3 reads spec_format_version frontmatter
    and upgrades to report.fail() when >= v2.1. Search TYPE_TABLES_MISSING
    in this file for the upgrade site (one-line edit, no re-author).

    Rules 2 and 3 ship as hard fails immediately — they only fire when
    typed sections exist and contain rows, so legacy specs (no tables, no
    rows) trip rule 1 only. V3 specs (which use flow-delta as their
    structural anchor and don't carry typed tables in spec.md
    compatibility layer) trip rule 1 as a warning, never a hard fail
    in Phase 2 — Phase 3's spec_format_version frontmatter is the
    actual V2/V3 mode switch.
    """
    # Per-section column expectation map for rule 1 / rule 2 row parsing.
    section_columns = {
        "global invariants": INVARIANTS_TABLE_COLUMNS,
        "state transitions": STATE_TRANSITIONS_TABLE_COLUMNS,
        "contracts": CONTRACTS_TABLE_COLUMNS,
    }

    # ---- Rule 1: Presence ----
    # Each of the three required headings must have a table with the
    # documented column count AND >=1 data row OR exactly 1 sentinel row.
    # Phase 2 ships as report.warn() for backwards-compat.
    section_bodies: dict[str, str] = {}
    for heading_lower, expected_cols in section_columns.items():
        section_body = extract_section(body, heading_lower) or ""
        section_bodies[heading_lower] = section_body
        rows = list(_iter_table_rows(section_body))
        if not section_body.strip() or not rows:
            # Phase 3 / TYPE-02: severity is gated on spec_format_version.
            # Specs declaring v2.1+ hard-fail; v2.0 / missing-frontmatter
            # specs still warn-only. Message text identical across
            # branches for grep-stability of TYPE_TABLES_MISSING /
            # spec_format_version / Phase 3 / TYPE-02 tokens.
            _msg = (
                f"TYPE_TABLES_MISSING: spec is missing the "
                f"'## {heading_lower.title()}' typed section or its markdown "
                f"table. Phase 6 PROBE-01, Phase 7 TEST-01, and Phase 8 "
                f"INTENT-01 require these typed tables as their citation "
                f"surface. NOTE: this is a WARNING in Phase 2 to preserve "
                f"v4.2.0 backwards-compatibility; Phase 3 (TYPE-02) upgrades "
                f"this to a hard FAILURE for specs declaring "
                f"spec_format_version >= v2.1."
            )
            if spec_version_tuple >= (2, 1):
                report.fail(_msg)
            else:
                report.warn(_msg)
            continue
        # Column-count check on the first data row (or sentinel row).
        first_row = rows[0]
        if len(first_row) != len(expected_cols):
            report.fail(
                f"TYPED_SECTION_MALFORMED: '## {heading_lower.title()}' "
                f"table has {len(first_row)} columns; expected "
                f"{len(expected_cols)} ({' | '.join(expected_cols)}). "
                f"Re-emit the table with the documented column shape per "
                f"setup-forge.sh SPEC TEMPLATE block."
            )

    # ---- Rule 2 + Rule 3 iterate per row of each present section ----
    for heading_lower, expected_cols in section_columns.items():
        section_body = section_bodies.get(heading_lower, "")
        if not section_body:
            continue  # rule 1 already warned
        rows = list(_iter_table_rows(section_body))
        if not rows:
            continue

        # Tokenize section prose ONCE for rule 3 (reused per row).
        # Strip citation markers (`[from A-NNN]`, `[derived from ...]`) from
        # the prose before tokenizing — citations are provenance metadata,
        # not content, and should not count toward content-overlap (this
        # mirrors how _iter_table_rows treats the citation cell as separate
        # from row content).
        prose_text = _strip_table_lines(section_body)
        prose_text = CITATION_RE.sub("", prose_text)
        prose_tokens = _tokenize(prose_text)

        # Verbatim check (rule 2) only applies when column index 1 is the
        # literal "statement" column — i.e., the Invariants table. State
        # Transitions has `from-state` at index 1 (canonical state names,
        # not transcript quotes); Contracts has `surface` at index 1
        # (canonical surface/endpoint names). Per CONTEXT.md decisions,
        # only `statement` is required to be verbatim from the cited body.
        check_verbatim = (
            len(expected_cols) >= 2 and expected_cols[1] == "statement"
        )

        for row_idx, cells in enumerate(rows):
            if len(cells) != len(expected_cols):
                # Already flagged in rule 1 (column-count mismatch).
                continue

            # Detect sentinel row — exempt from rule 2 verbatim and rule 3.
            # Permissive: match the sentinel pattern in any non-ID,
            # non-citation cell (sentinel may land in `surface` for contracts,
            # `trigger` for state-transitions, etc.) — see CONTEXT.md
            # "Empty-table / non-applicable policy" + RESEARCH.md Open
            # Question 2.
            is_sentinel = any(
                TYPED_SENTINEL_RE.match(cell) for cell in cells[1:-1]
            )

            citation_cell = cells[-1]

            # ---- Rule 2: Citation integrity ----
            if is_sentinel:
                # Sentinel: extract any A-NNN reference; if present, must
                # exist in transcript. Sentinels may use looser citation
                # forms (e.g., `[from A-NNN reasoning]` or
                # `[from survey reasoning]`) per CONTEXT.md.
                aid_matches = ANSWER_REF_RE.findall(citation_cell)
                for aid in aid_matches:
                    if aid not in transcript_answers:
                        report.fail(
                            f"TYPED_ROW_DANGLING: '## "
                            f"{heading_lower.title()}' sentinel row cites "
                            f"{aid} but transcript has no such answer. The "
                            f"sentinel's citation must point at the "
                            f"transcript answer that justified the absence "
                            f"(e.g., user said the feature has no state "
                            f"transitions)."
                        )
            else:
                # Data row: strict Locked-only citation form.
                cite_match = TYPED_ROW_CITATION_RE.match(citation_cell)
                if not cite_match:
                    report.fail(
                        f"TYPED_ROW_BAD_CITATION: '## "
                        f"{heading_lower.title()}' row {row_idx + 1} "
                        f"citation cell {citation_cell!r} does not match "
                        f"the required Locked-only form '[from A-NNN]'. "
                        f"Survey-derived facts go in '## Technical Design' "
                        f"prose, never in typed tables. Reject reasons: "
                        f"'[derived from ...]', '[from survey/...]', "
                        f"'[from R1.5 research]', '[from A-AUTO-NNN]' are "
                        f"all forbidden in typed-row citations."
                    )
                    continue
                cited_aid = cite_match.group(1)
                if cited_aid not in transcript_answers:
                    report.fail(
                        f"TYPED_ROW_DANGLING: '## "
                        f"{heading_lower.title()}' row {row_idx + 1} cites "
                        f"{cited_aid} but transcript has no such answer."
                    )
                    continue
                # Verbatim check: row's `statement` cell (cell index 1 —
                # second column, always after ID) tokens must appear in
                # the cited A-NNN body. Only applies to tables where index
                # 1 is the literal "statement" column (Invariants); for
                # State Transitions and Contracts, index 1 is `from-state`
                # / `surface` which carry canonical names not transcript
                # quotes (per CONTEXT.md table column schemas).
                if check_verbatim:
                    statement_cell = cells[1] if len(cells) > 1 else ""
                    if statement_cell:
                        normalized_stmt = normalize_for_compare(statement_cell)
                        normalized_body = normalize_for_compare(
                            transcript_answers[cited_aid].body
                        )
                        if (
                            normalized_stmt
                            and normalized_stmt not in normalized_body
                        ):
                            report.fail(
                                f"TYPED_ROW_NOT_VERBATIM: '## "
                                f"{heading_lower.title()}' row "
                                f"{row_idx + 1} statement "
                                f"{statement_cell!r} does not appear "
                                f"verbatim in cited answer {cited_aid}'s "
                                f"body. Typed-row statements must quote "
                                f"verbatim from the Locked transcript "
                                f"answer; paraphrase is rejected (the 70%% "
                                f"Jaccard rule is the prose-side backstop)."
                            )

            # ---- Rule 3: Content-difference (sentinel rows exempt) ----
            if is_sentinel:
                continue
            # Tokenize row content cells (skip ID at index 0 and citation
            # at index -1).
            row_content_text = " ".join(cells[1:-1])
            row_tokens = _tokenize(row_content_text)
            similarity = _jaccard(row_tokens, prose_tokens)
            if similarity >= JACCARD_REJECTION_THRESHOLD:
                report.fail(
                    f"TYPED_ROW_PARAPHRASE: '## {heading_lower.title()}' "
                    f"row {row_idx + 1} content overlaps section prose at "
                    f"Jaccard={similarity:.2f} (threshold "
                    f"{JACCARD_REJECTION_THRESHOLD}). Typed-row content "
                    f"must come from the transcript, not be paraphrased "
                    f"from spec prose. If the transcript does not support "
                    f"the row, the row should not exist — go back to the "
                    f"transcript or write a sentinel row."
                )


def check_survey_only_requirements(body: str, report: Report) -> None:
    """FR/NFR items under Locked/Flexible whose only citation is
    [from survey/...] — these imply a requirement inferred from the codebase,
    not from the user.
    """
    for section_name, start, end in iter_sections(body):
        name_low = section_name.strip().lower()
        if name_low not in (
            "functional requirements",
            "non-functional requirements",
        ):
            continue
        section = body[start:end]
        # Only inspect bullets under ### Locked / ### Flexible
        sub_matches = list(
            re.finditer(
                r"^###\s+(Locked|Flexible)\b", section, re.MULTILINE
            )
        )
        for i, sm in enumerate(sub_matches):
            sub_start = sm.end()
            next_sub = re.search(
                r"^###\s+", section[sub_start:], re.MULTILINE
            )
            sub_end = (
                sub_start + next_sub.start()
                if next_sub is not None
                else len(section)
            )
            sub_body = section[sub_start:sub_end]
            for bullet in _iter_bullets(sub_body):
                id_match = re.search(
                    r"\*\*((?:FR|NFR)-\d+)\*\*", bullet
                )
                if not id_match:
                    continue
                has_answer_cite = bool(ANSWER_REF_RE.search(bullet))
                has_survey_cite = "survey/" in bullet.lower()
                if has_survey_cite and not has_answer_cite:
                    report.fail(
                        f"SURVEY_ONLY_REQUIREMENT: {id_match.group(1)} in "
                        f"'{section_name}' cites only a survey file with no "
                        f"[from A-NNN] backing. This is a requirement "
                        f"inferred from the codebase, not from the user."
                    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(
            "usage: validate-spec.py <spec.md> <transcript.md>",
            file=sys.stderr,
        )
        return 2

    spec_path = Path(argv[1])
    transcript_path = Path(argv[2])

    if not spec_path.exists():
        print(f"FAIL: spec file not found: {spec_path}", file=sys.stderr)
        return 1
    if not transcript_path.exists():
        print(
            f"FAIL: transcript file not found: {transcript_path}",
            file=sys.stderr,
        )
        return 1

    spec_text = spec_path.read_text(encoding="utf-8")
    transcript_text = transcript_path.read_text(encoding="utf-8")

    transcript_answers = parse_transcript(transcript_text)
    body, appendix = split_spec_body_appendix(spec_text)

    report = Report()

    check_batched_headings(transcript_text, report)
    check_transcript_sanity(transcript_answers, report)
    check_structure(spec_text, body, appendix, transcript_answers, report)
    check_locked_fidelity(body, transcript_answers, report)
    check_opportunistic_fidelity(body, transcript_answers, report)
    check_universal_citations(body, report)
    check_dangling_refs(body, transcript_answers, report)
    check_no_question_citations(body, report)
    check_arch_invariants_populated(body, transcript_answers, report)
    check_survey_only_requirements(body, report)
    check_coverage(body, transcript_answers, report)
    # Phase 3 / TYPE-02: parse spec_format_version BEFORE any other
    # check that depends on it. The parsed tuple threads into
    # check_implicit_facts and check_typed_sections so their warn sites
    # (IMPLICIT_FACT_SKIPPED, TYPE_TABLES_MISSING) can promote to fail
    # when version >= v2.1 (Plan 03-03). SPEC_FORMAT_VERSION_UNKNOWN is
    # emitted directly inside check_spec_format_version on allowlist miss.
    spec_version_tuple = check_spec_format_version(body, report)

    check_implicit_facts(
        transcript_text,
        transcript_answers,
        report,
        spec_version_tuple=spec_version_tuple,  # Phase 3 / TYPE-02
    )  # Phase 1 / INTV-01
    check_typed_sections(
        body,
        transcript_answers,
        report,
        spec_version_tuple=spec_version_tuple,  # Phase 3 / TYPE-02
    )  # Phase 2 / TYPE-01

    # Dedupe failures (opportunistic + locked checks can fire on the same line)
    seen: set[str] = set()
    deduped: list[str] = []
    for f in report.failures:
        if f in seen:
            continue
        seen.add(f)
        deduped.append(f)
    report.failures = deduped

    # Print report
    print(f"=== Forge Spec Validation ===")
    print(f"spec:       {spec_path}")
    print(f"transcript: {transcript_path} ({len(transcript_answers)} answers)")
    print()

    if report.warnings:
        print(f"⚠ {len(report.warnings)} WARNING(S):")
        for w in report.warnings:
            print(f"  - {w}")
        print()

    if report.failures:
        print(f"✗ {len(report.failures)} FAILURE(S):")
        for i, f in enumerate(report.failures, 1):
            print(f"  {i}. {f}")
        print()
        print(
            "FAIL: spec does not satisfy the Verbatim-Fidelity Gate. "
            "Fix and re-run."
        )
        return 1

    print("✓ PASS: fidelity, traceability, and coverage checks all passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
