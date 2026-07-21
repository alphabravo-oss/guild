#!/usr/bin/env python3
"""
validate_spec_review.py — deterministic output validator for the Phase 6 /
PROBE-01 adversarial spec reviewer.

Enforces the spec-review.json contract:

  * Closed schema  — only KNOWN_REVIEW_KEYS allowed at top level (Pitfall 2:
                     no suggested_fix / recommendation auto-resolve fields)
  * Closed flags   — only KNOWN_FLAG_KEYS allowed inside each flag entry
  * Order          — reviewer_order_violation=true is an immediate block
                     (Pitfall 5: transcript-first read enforcement)
  * Budget         — at most MAX_FLAGS=5 flags (Pitfall 4: rubric ceiling)
  * Citation       — every flag.citation MUST resolve to an A-NNN that exists
                     in transcript.md (Pitfall 1: no missing-detail flags)
  * Binary verdict — verdict must be 'block' iff flags is non-empty,
                     'pass' iff flags is empty (Pitfall 3: no advisory mode)

Exits 0 on pass, 1 on any failure, 2 on usage error.
Usage: validate_spec_review.py <spec-review.json> <transcript.md>

This script is the authoritative R3.5 gate. The reviewer agent's prompt
rubric is advisory; this script is load-bearing. If the script fails,
the R3.5 PROBE phase must not be considered passed.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path


# ---------------------------------------------------------------------------
# Constants — closed vocabularies
# ---------------------------------------------------------------------------

# CLOSED VOCABULARY — top-level keys allowed in spec-review.json.
# Pitfall 2: no suggested_fix / recommendation auto-resolve fields. The
# reviewer's job is to flag, not to fix; smuggling auto-resolve fields
# defeats the adversarial discipline. Extend only via phase-level RFC.
KNOWN_REVIEW_KEYS = frozenset(
    {
        "review_version",
        "verdict",
        "flag_count",
        "flags",
        "reviewer_order_violation",
    }
)

# CLOSED VOCABULARY — keys allowed inside each flag entry.
# Mirrors KNOWN_REVIEW_KEYS discipline: only the four documented fields.
KNOWN_FLAG_KEYS = frozenset(
    {
        "id",
        "citation",
        "typed_row",
        "ambiguity",
    }
)

# Pitfall 4 ceiling — synced with reviewer rubric prose in
# plugins/forge/scripts/setup-forge.sh R3.5 heredoc (Plan 06-03).
# 5 flags is the budget; over-budget reviews are rejected so the
# reviewer cannot "spam" the spec with a long flag list and effectively
# turn the gate into noise.
MAX_FLAGS = 5

# Inline ANSWER_BLOCK_RE — byte-equivalent to validate-spec.py:61-68.
# Phase 1 Plan 01-03 precedent (inline regex in test files because
# dash-named filename validate-spec.py is not a valid Python identifier).
# validate_spec_review.py IS a valid identifier; we inline anyway to keep
# this script single-purpose and self-contained — it has no library role.
ANSWER_BLOCK_RE = re.compile(
    r"^##\s+(A-\d+)"
    r"(?:\s*\[([^\]]*)\])?"
    r"(?:\s*\(([^)]*)\))?"
    r"\s*\n(.*?)"
    r"(?=^##\s+[AQ]-\d+|^##\s+[A-Z]|\Z)",
    re.MULTILINE | re.DOTALL,
)

# Phase 3 / TYPE-02 also recognizes A-AUTO-NNN entries in transcripts.
# PROBE-01 reads transcripts that may contain auto-discovered implicit-fact
# answers; we accept A-AUTO-NNN citations as valid by including them in the
# answer_ids set. Mirror validate-spec.py:118-124 A_AUTO_BLOCK_RE.
A_AUTO_BLOCK_RE = re.compile(
    r"^##\s+(A-AUTO-\d+)"
    r"(?:\s*\[([^\]]*)\])?"
    r"(?:\s*\(([^)]*)\))?"
    r"\s*\n(.*?)"
    r"(?=^##\s+[AQ]-\d+|^##\s+A-AUTO-\d+|^##\s+[A-Z]|\Z)",
    re.MULTILINE | re.DOTALL,
)


# ---------------------------------------------------------------------------
# Core validator
# ---------------------------------------------------------------------------


def validate_spec_review(review_path: Path, transcript_path: Path) -> int:
    """Validate spec-review.json against transcript.md.

    Returns exit code: 0 on pass, 1 on any failure.

    Failure modes (each appends a FAIL line to stdout before returning 1):
      * Closed-schema violation — unknown top-level or per-flag keys
      * Order violation — reviewer_order_violation=true
      * Budget violation — len(flags) > MAX_FLAGS
      * Citation violation — flag.citation empty or not in transcript A-IDs
      * Verdict violation — non-binary verdict mapping vs. flag list shape
      * Internal inconsistency — flag_count disagrees with len(flags)
    """
    try:
        review_text = review_path.read_text()
    except FileNotFoundError:
        print(
            f"FAIL: spec-review.json not found at {review_path}",
            file=sys.stderr,
        )
        return 1
    try:
        review = json.loads(review_text)
    except json.JSONDecodeError as exc:
        print(
            f"FAIL: spec-review.json is not valid JSON: {exc}",
            file=sys.stderr,
        )
        return 1
    if not isinstance(review, dict):
        print(
            f"FAIL: spec-review.json must be a JSON object at top level, "
            f"got {type(review).__name__}",
            file=sys.stderr,
        )
        return 1

    try:
        transcript = transcript_path.read_text()
    except FileNotFoundError:
        print(
            f"FAIL: transcript.md not found at {transcript_path}",
            file=sys.stderr,
        )
        return 1

    # Build the answer_ids set: A-NNN union A-AUTO-NNN entries in transcript.md
    answer_ids: set[str] = set()
    answer_ids.update(m.group(1) for m in ANSWER_BLOCK_RE.finditer(transcript))
    answer_ids.update(m.group(1) for m in A_AUTO_BLOCK_RE.finditer(transcript))

    failures: list[str] = []

    # Check 1: Closed top-level schema (Pitfall 2)
    extra = set(review.keys()) - KNOWN_REVIEW_KEYS
    if extra:
        failures.append(
            f"Unknown keys in spec-review.json: {sorted(extra)!r} - "
            f"closed-vocabulary schema, only "
            f"{sorted(KNOWN_REVIEW_KEYS)!r} allowed"
        )

    # Check 2: Order violation (Pitfall 5) - immediate block regardless of
    # flags. Run BEFORE the verdict-consistency check so the legitimate
    # order-violation shape (flags=[] + verdict=block + order_violation=true)
    # is reported with the explicit ORDER_VIOLATION token rather than a
    # confusing "verdict must be 'pass'" double-fault.
    order_violation = bool(review.get("reviewer_order_violation"))
    if order_violation:
        failures.append(
            "REVIEWER_ORDER_VIOLATION: reviewer read spec before transcript "
            "(reviewer_order_violation=true in output)"
        )

    # Check 3: Flag-list shape
    flags = review.get("flags", [])
    if not isinstance(flags, list):
        failures.append(
            f"flags field must be a JSON array, got {type(flags).__name__}"
        )
        flags = []  # short-circuit further per-flag checks

    # Check 4: Flag budget ceiling (Pitfall 4)
    if len(flags) > MAX_FLAGS:
        failures.append(
            f"Flag budget exceeded: {len(flags)} flags, max {MAX_FLAGS}"
        )

    # Check 5: Per-flag citation (Pitfall 1) and per-flag closed schema
    for idx, flag in enumerate(flags):
        if not isinstance(flag, dict):
            failures.append(
                f"Flag flags[{idx}] must be a JSON object, "
                f"got {type(flag).__name__}"
            )
            continue
        flag_id = flag.get("id", f"flags[{idx}]")
        # Closed per-flag schema
        extra_flag_keys = set(flag.keys()) - KNOWN_FLAG_KEYS
        if extra_flag_keys:
            failures.append(
                f"Flag {flag_id} has unknown keys "
                f"{sorted(extra_flag_keys)!r} - closed-vocabulary, only "
                f"{sorted(KNOWN_FLAG_KEYS)!r} allowed"
            )
        cite = flag.get("citation", "")
        if not cite:
            failures.append(
                f"Flag {flag_id} has no citation field (or empty)"
            )
        elif cite not in answer_ids:
            sample_ids = sorted(answer_ids)[:5]
            ellipsis = "..." if len(answer_ids) > 5 else ""
            failures.append(
                f"Flag {flag_id} cites {cite!r} which is not in transcript "
                f"(answer_ids: {sample_ids}{ellipsis})"
            )

    # Check 6: Binary verdict (Pitfall 3) - no advisory mode.
    # Order-violation path is excluded from the "verdict must be 'pass' when
    # no flags" check because the legitimate order-violation shape is
    # flags=[] + verdict=block + reviewer_order_violation=true.
    verdict = review.get("verdict")
    if len(flags) > 0 and verdict != "block":
        failures.append(
            f"verdict must be 'block' when flags are present (got "
            f"{verdict!r}) - binary block/pass discipline; no advisory tier"
        )
    if len(flags) == 0 and verdict != "pass" and not order_violation:
        failures.append(
            f"verdict must be 'pass' when no flags are present "
            f"(got {verdict!r})"
        )

    # Check 7: flag_count internal-consistency (informational; do not split-
    # brain the truth from the flags list - flags is authoritative).
    declared_count = review.get("flag_count")
    if (
        declared_count is not None
        and isinstance(declared_count, int)
        and declared_count != len(flags)
    ):
        failures.append(
            f"flag_count={declared_count} disagrees with "
            f"len(flags)={len(flags)}"
        )

    if failures:
        for f in failures:
            print(f"FAIL: {f}")
        return 1
    return 0


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(
            f"Usage: {argv[0]} <spec-review.json> <transcript.md>",
            file=sys.stderr,
        )
        return 2
    review_path = Path(argv[1])
    transcript_path = Path(argv[2])
    return validate_spec_review(review_path, transcript_path)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
