#!/usr/bin/env python3
"""Phase 8 / INTENT-01 — deterministic citation-only validator for intent-coverage.json.

Mirrors plugins/foundry/scripts/validate-test-observations.py shape
beat-for-beat (Phase 7 / TEST-01 reference, ~524 LOC) and
plugins/forge/scripts/validate_spec_review.py (Phase 6 / PROBE-01,
276 LOC). Closed-vocabulary discipline:

* KNOWN_INTENT_COVERAGE_KEYS — top-level closed schema (10 keys)
* KNOWN_CELL_KEYS — per-cell closed schema (4 keys)
* KNOWN_INTENT_COVERAGE_VERDICTS — verdict enum (3 values)
* KNOWN_INTENT_COVERAGE_FAILURE_TOKENS — 9-token failure vocabulary
* FORBIDDEN_AGENT_TOOLS / FORBIDDEN_BASH_PATTERNS — code-blind /
  embedding-blind tool-call audit denylist (advisory shape)

Three-anchor citation graph (RESEARCH.md Pattern 1):
1. Direct A-NNN literal in casting prompt body → PROPAGATED
2. Direct A-AUTO-NNN literal in prompt body → PROPAGATED
3. Typed-row [from A-NNN] inside <invariants>/<state_transitions>/<contracts>
   (Phase 2 / TYPE-01 indirection) → PARAPHRASED
4. Otherwise → DROPPED (per-cell verdict; gate aggregation is per-answer,
   see below)

Anchor-scope rule (stated word-for-word in
plugins/foundry/agents/intent-carrier.md — GI-003 mirroring): Anchors 1
and 2 search the prompt BODY only — the prompt text with the three
typed-table blocks (<invariants> / <state_transitions> / <contracts>)
excluded — so a typed-row [from A-NNN] citation can never fire
PROPAGATED; when the body lacks the literal but a typed row inside one
of those blocks cites [from A-NNN], the verdict is PARAPHRASED.

Gate aggregation rule (stated word-for-word in
plugins/foundry/agents/intent-carrier.md — GI-003 mirroring): An answer_id
is DROPPED (gate-blocking) only when
every casting's cell for it is DROPPED; a PROPAGATED or PARAPHRASED cell
in any casting keeps the gate open for that answer, and per-cell DROPPED
verdicts remain recorded in the matrix without blocking.

Spec→matrix completeness rule (stated word-for-word in
plugins/foundry/agents/intent-carrier.md — GI-003 mirroring): A spec
appendix answer_id with no matrix cell at all is zero-coverage: the
validator emits INTENT_COVERAGE_MATRIX_INCOMPLETE naming each missing
answer_id, and the omitted answer blocks the gate exactly like an answer
whose every casting's cell is DROPPED. Without ``--spec`` the appendix
answer-set is unknown and the completeness check cannot run (matrix-only
validation applies); the Foundry-Intent-Coverage MCP gate resolves the
run's spec canonically (run-dir spec.md, falling back to
state.json['spec_path']) and supplies ``--spec`` whenever the spec is
resolvable — the completeness check runs whenever the spec is resolvable
and cannot run only when no spec is resolvable at all.

Cell-verifiability rule (stated word-for-word in
plugins/foundry/agents/intent-carrier.md — GI-003 mirroring): A matrix
cell that cannot be verified cannot contribute coverage: a cell missing
answer_id, casting_id, or verdict, a cell citing a casting_id absent from
castings/manifest.json, and a cell citing a manifest-declared casting
whose prompt file is missing or unreadable all count as DROPPED in the
per-answer aggregation, so an answer whose only non-DROPPED cells are
such cells blocks as zero-coverage. The manifest and prompt files are
located via ``--castings-dir`` (the Foundry-Intent-Coverage MCP gate
always passes the run's castings dir — D-018b), falling back to the
spec-adjacent ``castings/`` directory for CLI compatibility; when
neither resolves, these integrity checks cannot run and the gate
discloses the skip via ``verification_checked=False``.

Citation-only — never embeddings, never Jaccard, never fuzzy text-overlap.

Exits 0 on pass, 1 on any failure, 2 on usage error.

Usage:
    validate-intent-coverage.py <intent-coverage.json> \\
        [--spec <spec.md>] [--tool-call-log <log.json>] \\
        [--castings-dir <castings/>]

This script is the authoritative INTENT-01 F0.7 gate. The
intent-carrier agent's prompt rubric is advisory; this script is
load-bearing. If the script fails, the F0.7 stream's intent-coverage
output must not be considered eligible for downstream consumption.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

from foundry_mcp.tools.foundry_state import read_document


# ---------------------------------------------------------------------------
# Constants — closed vocabularies
# ---------------------------------------------------------------------------

# CLOSED VOCABULARY — top-level keys allowed in intent-coverage.json.
# Pitfall A (closed-vocab smuggling): any extra top-level field is rejected
# with INTENT_COVERAGE_SCHEMA_INVALID. Mirrors Phase 6/7 discipline.
# Extend only via phase-level RFC.
KNOWN_INTENT_COVERAGE_KEYS = frozenset(
    {
        "stream",
        "phase",
        "spec_format_version",
        "spec_hash",
        "agent_path",
        "wall_clock_seconds",
        "answer_count",
        "casting_count",
        "summary",
        "matrix",
    }
)

# CLOSED VOCABULARY — keys allowed inside each per-cell entry.
# Mirrors KNOWN_INTENT_COVERAGE_KEYS discipline at the cell level (parallel
# to Phase 6 KNOWN_FLAG_KEYS / Phase 7 KNOWN_OBSERVATION_KEYS).
KNOWN_CELL_KEYS = frozenset(
    {
        "answer_id",
        "casting_id",
        "verdict",
        "citation_chain",
    }
)

# CLOSED VOCABULARY — cell.verdict enum.
# Pitfall: free-form verdict strings smuggle in advisory tiers. Validator
# enforces PROPAGATED/PARAPHRASED/DROPPED only.
KNOWN_INTENT_COVERAGE_VERDICTS = frozenset(
    {"PROPAGATED", "PARAPHRASED", "DROPPED"}
)

# CLOSED VOCABULARY — failure tokens emitted by this validator.
# 9 tokens locked; mirrors Phase 4's 8-token / Phase 7's 9-token closed
# vocabularies. Sub-pattern detail surfaces in trailing prose AFTER the
# token, not as a parallel token.
KNOWN_INTENT_COVERAGE_FAILURE_TOKENS = frozenset(
    {
        "INTENT_COVERAGE_DROPPED",
        "INTENT_COVERAGE_DANGLING_CITATION",
        "INTENT_COVERAGE_SCHEMA_INVALID",
        "INTENT_COVERAGE_UNKNOWN_VERDICT",
        "INTENT_COVERAGE_MATRIX_INCOMPLETE",
        "INTENT_COVERAGE_VACUOUS_PROPAGATED",
        "INTENT_COVERAGE_AGENT_USED_EMBEDDING",
        "INTENT_COVERAGE_AGENT_USED_FUZZY_OVERLAP",
        "INTENT_COVERAGE_VERDICT_MISMATCH",
    }
)

# Code-blind / embedding-blind discipline denylists. The intent-carrier
# agent must perform citation-only mapping — any embedding-API call or
# fuzzy-overlap library import is a discipline violation.
FORBIDDEN_AGENT_TOOLS = frozenset(
    {
        "Embedding",
        "VectorSearch",
        "SemanticSimilarity",
    }
)

FORBIDDEN_BASH_PATTERNS = frozenset(
    {
        "openai.embeddings.create",
        "anthropic.embeddings.create",
        "from sentence_transformers",
        "import faiss",
        "import chromadb",
        "scipy.spatial.distance",
        "sklearn.metrics.pairwise",
    }
)

# Patterns that surface as INTENT_COVERAGE_AGENT_USED_EMBEDDING (vs
# INTENT_COVERAGE_AGENT_USED_FUZZY_OVERLAP). Embedding-API patterns get the
# embedding token; general fuzzy-overlap libs get the fuzzy-overlap token.
_EMBEDDING_PATTERN_MARKERS = (
    "embeddings",
    "sentence_transformers",
    "faiss",
    "chromadb",
)


# ---------------------------------------------------------------------------
# Regexes — single-source-of-truth byte-equivalent to validate-spec.py
# ---------------------------------------------------------------------------
#
# Inline regex copies — byte-equivalent to plugins/forge/scripts/validate-spec.py:
#   APPENDIX_HEADING_RE   (validate-spec.py:43)
#   ANSWER_BLOCK_RE       (validate-spec.py:61)
#   ANSWER_REF_RE         (validate-spec.py:85)
#   A_AUTO_BLOCK_RE       (validate-spec.py:118)
#   TYPED_ROW_CITATION_RE (validate-spec.py:212)
#
# Inlined rather than imported because this script is invoked standalone via
# subprocess (subprocess discipline mirroring validate-test-observations.py:161).
# The cross-script alignment test in plugins/foundry/mcp-server/tests/
# test_intent_coverage.py::test_intent_coverage_regex_byte_equivalent_to_validate_spec
# asserts byte-equivalence — any drift here surfaces at test time.

APPENDIX_HEADING_RE = re.compile(
    r"^##\s+Appendix:\s*Interview\s+Transcript\b",
    re.MULTILINE | re.IGNORECASE,
)

ANSWER_BLOCK_RE = re.compile(
    r"^##\s+(A-\d+)"
    r"(?:\s*\[([^\]]*)\])?"
    r"(?:\s*\(([^)]*)\))?"
    r"\s*\n(.*?)"
    r"(?=^##\s+[AQ]-\d+|^##\s+[A-Z]|\Z)",
    re.MULTILINE | re.DOTALL,
)

ANSWER_REF_RE = re.compile(r"\bA-\d+\b")

A_AUTO_BLOCK_RE = re.compile(
    r"^##\s+(A-AUTO-\d+)"
    r"(?:\s*\[([^\]]*)\])?"
    r"(?:\s*\(([^)]*)\))?"
    r"\s*\n(.*?)"
    r"(?=^##\s+[AQ]-\d+|^##\s+A-AUTO-\d+|^##\s+[A-Z]|\Z)",
    re.MULTILINE | re.DOTALL,
)

A_AUTO_REF_RE = re.compile(r"\bA-AUTO-\d+\b")

TYPED_ROW_CITATION_RE = re.compile(r"^\s*\[from\s+(A-\d+)\s*\]\s*$")

# Phase 2 / TYPE-01 typed-block tag set. Typed-row indirection inside any
# of these tags counts as PARAPHRASED per Locked Decision A.
TYPED_BLOCK_TAGS: tuple[str, ...] = (
    "invariants",
    "state_transitions",
    "contracts",
)

# Phase 3 / TYPE-02 minimum spec_format_version that mandates a populated
# Appendix: Interview Transcript block. v2.0 specs route through the
# stream-skip path; v2.0 with empty appendix is NOT a defect.
_VACUOUS_PROPAGATED_MIN_VERSION = "v2.1"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _extract_appendix_body(spec_text: str) -> str:
    """Return text AFTER the ``## Appendix: Interview Transcript`` heading.

    Pitfall 3 (RESEARCH.md): scope ANSWER_BLOCK_RE / A_AUTO_BLOCK_RE search
    to text AFTER the appendix heading — never grep whole-spec; spec body
    bullets that mention A-NNN are NOT canonical answer-set entries.
    """
    m = APPENDIX_HEADING_RE.search(spec_text)
    if m is None:
        return ""
    return spec_text[m.end():]


def extract_answer_ids_from_spec(spec_text: str) -> set[str]:
    """RESEARCH.md Code Example 1 — appendix-scoped A-NNN ∪ A-AUTO-NNN extraction.

    Single-source-of-truth: ANSWER_BLOCK_RE + A_AUTO_BLOCK_RE byte-equivalent
    to validate-spec.py. Returns the union; matrix rows for both forms.
    """
    body = _extract_appendix_body(spec_text)
    if not body:
        return set()
    ids: set[str] = set()
    ids.update(am.group(1) for am in ANSWER_BLOCK_RE.finditer(body))
    ids.update(am.group(1) for am in A_AUTO_BLOCK_RE.finditer(body))
    return ids


def _prompt_body_excluding_typed_blocks(prompt_text: str) -> str:
    """Return the prompt BODY — prompt text with typed-table blocks removed.

    D-013 anchor-scope discipline: the body/typed-block partition is
    complementary — every region this function removes is exactly the
    region the anchor-3 typed-row search inspects (same tag regex shape),
    so no text is invisible to both anchors.
    """
    body = prompt_text
    for tag in TYPED_BLOCK_TAGS:
        body = re.sub(rf"<{tag}>.*?</{tag}>", "", body, flags=re.DOTALL)
    return body


def verdict_for_cell(
    answer_id: str, prompt_text: str
) -> tuple[str, list[str]]:
    """Closed-vocabulary verdict + citation_chain for a single cell.

    Three-anchor algorithm (RESEARCH.md Pattern 1):

    1. Direct ``A-NNN`` / ``A-AUTO-NNN`` literal in prompt body
       (word-boundary anchored — Pitfall 2 word-boundary discipline:
       ``A-1`` MUST NOT substring-match ``A-12``) → PROPAGATED.
    2. Typed-row ``[from A-NNN]`` indirection inside <invariants> /
       <state_transitions> / <contracts> tag blocks (Locked Decision A:
       typed-row indirection IS the canonical PARAPHRASED state) →
       PARAPHRASED.
    3. Otherwise → DROPPED.

    Anchor-scope rule (stated word-for-word in
    plugins/foundry/agents/intent-carrier.md — GI-003 mirroring): Anchors
    1 and 2 search the prompt BODY only — the prompt text with the three
    typed-table blocks (<invariants> / <state_transitions> / <contracts>)
    excluded — so a typed-row [from A-NNN] citation can never fire
    PROPAGATED; when the body lacks the literal but a typed row inside
    one of those blocks cites [from A-NNN], the verdict is PARAPHRASED.

    Citation-only — no embeddings, no Jaccard, no fuzzy text-overlap.
    """
    if not answer_id:
        return "DROPPED", []

    # Anchor 1+2: direct literal (boundary-anchored) in the prompt BODY
    # (D-013: typed-table blocks excluded, so `[from A-NNN]` typed rows
    # cannot mask the PARAPHRASED signal).
    # re.escape to handle the dash inside "A-NNN" / "A-AUTO-NNN" literals.
    body_text = _prompt_body_excluding_typed_blocks(prompt_text)
    if re.search(rf"\b{re.escape(answer_id)}\b", body_text):
        return "PROPAGATED", [answer_id]

    # Anchor 3: typed-row indirection — search ONLY inside typed-table
    # blocks (Pitfall 3 mirror at the casting-prompt scope). finditer
    # over every same-tag block so the body-exclusion above and this
    # search cover complementary regions with no gap.
    for tag in TYPED_BLOCK_TAGS:
        for m in re.finditer(
            rf"<{tag}>(.*?)</{tag}>", prompt_text, re.DOTALL
        ):
            for cite in re.finditer(
                r"\[\s*from\s+(A-\d+)\s*\]", m.group(1)
            ):
                if cite.group(1) == answer_id:
                    return "PARAPHRASED", [answer_id, f"<{tag}>"]

    return "DROPPED", [answer_id]


# D-017: keys every matrix cell must carry. A cell missing (or carrying an
# empty) answer_id, casting_id, or verdict is structurally invalid and
# cannot contribute coverage. Subset of KNOWN_CELL_KEYS (citation_chain
# stays optional); no existing frozenset grows (GI-002).
REQUIRED_CELL_KEYS: tuple[str, ...] = ("answer_id", "casting_id", "verdict")


def load_manifest_casting_ids(castings_dir: Path | None) -> set[str] | None:
    """Return manifest.castings[].id (str-normalized) or None when unavailable.

    D-014a manifest read, extracted so the validator body AND the
    Foundry-Intent-Coverage gate tool resolve the same id-set through one
    code path. ``None`` means "manifest unavailable — the ghost-casting
    and unverifiable-casting checks cannot run" (matrix-only validation),
    mirroring the --spec-absent completeness stance; the gate discloses
    that state as ``verification_checked=False`` (D-018c).
    """
    if castings_dir is None:
        return None
    manifest_path = castings_dir / "manifest.json"
    if not manifest_path.is_file():
        return None
    manifest_data, manifest_problem = read_document(manifest_path)
    if manifest_problem is not None:
        return None
    if isinstance(manifest_data, dict) and isinstance(
        manifest_data.get("castings"), list
    ):
        return {
            str(c.get("id"))
            for c in manifest_data["castings"]
            if isinstance(c, dict) and c.get("id") is not None
        }
    return None


def _read_casting_prompt(
    castings_dir: Path,
    casting_id: Any,
    cache: dict[str, str | None],
) -> str | None:
    """Read (and cache) a casting prompt's text; None when missing/unreadable.

    ``None`` is the D-018a unverifiable state — no prompt text exists to
    re-derive against. An empty-but-readable prompt file returns ``""``
    and re-derivation runs against it (deriving DROPPED for any answer).
    """
    prompt_path = castings_dir / f"casting-{casting_id}-prompt.md"
    cache_key = str(prompt_path)
    if cache_key not in cache:
        try:
            cache[cache_key] = prompt_path.read_text(encoding="utf-8")
        except OSError:
            cache[cache_key] = None
    return cache[cache_key]


def cell_cannot_contribute_reason(
    cell: dict,
    *,
    manifest_casting_ids: set[str] | None,
    castings_dir: Path | None,
    prompt_cache: dict[str, str | None] | None = None,
) -> str | None:
    """Classify why a cell's verdict cannot contribute coverage, or None.

    The single classification behind the cell-verifiability rule (module
    docstring — D-017/D-018/D-019). Returns one of:

      * ``"missing_required_keys"`` — a REQUIRED_CELL_KEYS entry is
        absent or empty (D-017);
      * ``"ghost_casting"`` — manifest ids are known and the cell cites a
        casting_id the manifest does not declare (D-014a);
      * ``"unverifiable_casting"`` — the manifest declares the casting
        but its prompt file is missing or unreadable (D-018a);
      * ``None`` — the cell is verifiable and contributes its verdict.

    Used by the validator's per-cell failure lines AND by
    ``aggregate_matrix_coverage`` (which both layers call for the gate
    decision), so the classification cannot diverge between layers.
    """
    if prompt_cache is None:
        prompt_cache = {}
    if any(not cell.get(k) for k in REQUIRED_CELL_KEYS):
        return "missing_required_keys"
    if manifest_casting_ids is None:
        return None
    casting_id = cell["casting_id"]
    if str(casting_id) not in manifest_casting_ids:
        return "ghost_casting"
    if (
        castings_dir is not None
        and _read_casting_prompt(castings_dir, casting_id, prompt_cache)
        is None
    ):
        return "unverifiable_casting"
    return None


def aggregate_matrix_coverage(
    matrix: Any,
    *,
    castings_dir: Path | None,
    prompt_cache: dict[str, str | None] | None = None,
) -> tuple[dict[str, list[str]], bool]:
    """Per-answer contributed verdicts with cannot-contribute normalization.

    THE shared aggregation (D-019: one rule, no layer divergence): the
    validator's blocking decision and the Foundry-Intent-Coverage gate
    tool's ``dropped_answers`` / ``redecompose_hints`` fold both flow
    through this function. Cells whose ``cell_cannot_contribute_reason``
    is non-None contribute DROPPED regardless of their claimed verdict;
    cells without an answer_id cannot be attributed to any answer and are
    excluded (their missing-key state still fails validation).

    Returns ``(cell_verdicts_by_answer, verification_checked)`` where
    ``verification_checked`` is True only when castings/manifest.json was
    loadable — i.e. the ghost-casting and unverifiable-casting integrity
    checks actually ran (D-018c disclosure).
    """
    manifest_casting_ids = load_manifest_casting_ids(castings_dir)
    verification_checked = manifest_casting_ids is not None
    if prompt_cache is None:
        prompt_cache = {}
    cell_verdicts_by_answer: dict[str, list[str]] = {}
    for cell in matrix if isinstance(matrix, list) else []:
        if not isinstance(cell, dict):
            continue
        answer_id = cell.get("answer_id")
        if not answer_id:
            continue
        reason = cell_cannot_contribute_reason(
            cell,
            manifest_casting_ids=manifest_casting_ids,
            castings_dir=castings_dir,
            prompt_cache=prompt_cache,
        )
        cell_verdicts_by_answer.setdefault(answer_id, []).append(
            "DROPPED" if reason is not None else cell.get("verdict")
        )
    return cell_verdicts_by_answer, verification_checked


def _spec_format_version_meets_v21(version: str | None) -> bool:
    """True iff version is v2.1 or higher.

    Allowlist-narrow form per plan note: only fires for v2.1+ since Phase 3
    only ships v2.0 / v2.1. v2.0 routes through stream-skip and never hits
    this path; unknown values fail closed (return False) to avoid false-
    positive vacuous-PROPAGATED rejection on non-recognized versions.
    """
    if not version:
        return False
    if version == _VACUOUS_PROPAGATED_MIN_VERSION:
        return True
    # Permissive future-version compare: vN.M tuple lex order.
    m = re.match(r"^v(\d+)\.(\d+)$", version)
    if m is None:
        return False
    return (int(m.group(1)), int(m.group(2))) >= (2, 1)


def _classify_bash_pattern(pattern_text: str, forbid: str) -> str:
    """Map a forbidden Bash pattern to its closed-vocab failure token.

    Embedding-API markers (embeddings, sentence_transformers, faiss,
    chromadb) → INTENT_COVERAGE_AGENT_USED_EMBEDDING.
    Everything else (scipy.spatial.distance, sklearn.metrics.pairwise) →
    INTENT_COVERAGE_AGENT_USED_FUZZY_OVERLAP.
    """
    for marker in _EMBEDDING_PATTERN_MARKERS:
        if marker in forbid:
            return "INTENT_COVERAGE_AGENT_USED_EMBEDDING"
    return "INTENT_COVERAGE_AGENT_USED_FUZZY_OVERLAP"


# ---------------------------------------------------------------------------
# Core validator
# ---------------------------------------------------------------------------


def validate_intent_coverage(
    coverage_path: Path,
    *,
    spec_path: Path | None = None,
    tool_call_log_path: Path | None = None,
    castings_dir: Path | None = None,
) -> int:
    """Validate intent-coverage.json against the closed-vocab schema.

    Returns exit code: 0 on pass, 1 on any failure.

    Failure modes (each appends a token-prefixed line to stdout before the
    function returns 1):

      * INTENT_COVERAGE_SCHEMA_INVALID — malformed JSON, extra top-level
        keys, extra per-cell keys, malformed matrix list.
      * INTENT_COVERAGE_UNKNOWN_VERDICT — verdict not in
        KNOWN_INTENT_COVERAGE_VERDICTS.
      * INTENT_COVERAGE_VACUOUS_PROPAGATED — empty answer-set on v2.1+
        spec (mirror of Phase 1 IMPLICIT_FACT_SKIPPED severity-agnostic
        discipline).
      * INTENT_COVERAGE_DANGLING_CITATION — cell.answer_id not present
        in spec's appendix answer-set.
      * INTENT_COVERAGE_MATRIX_INCOMPLETE — spec→matrix completeness
        (only when --spec provided and the appendix answer-set is
        non-empty). A spec
        appendix answer_id with no matrix cell at all is zero-coverage: the
        validator emits INTENT_COVERAGE_MATRIX_INCOMPLETE naming each missing
        answer_id, and the omitted answer blocks the gate exactly like an answer
        whose every casting's cell is DROPPED. Without --spec this check
        cannot run; the MCP gate supplies --spec whenever the run's spec
        is resolvable (run-dir spec.md, falling back to
        state.json['spec_path']). The same token also fires per ghost
        cell (D-014a): when castings/manifest.json is loadable next to
        the casting prompts, a cell citing a casting_id absent from
        manifest.castings[].id is structurally-invalid coverage — the
        matrix cites a casting the manifest does not declare, the dual
        of a spec answer with no cell — and cannot contribute coverage:
        it counts as DROPPED in the per-answer aggregation, so an answer
        whose only non-DROPPED cells are ghosts blocks as zero-coverage.
        (Token reuse per GI-002; INTENT_COVERAGE_VERDICT_MISMATCH stays
        reserved for re-derivation disagreement over an EXISTING casting
        prompt — a remediation, re-labeling, that cannot apply to a
        nonexistent casting.) The same token also fires per
        unverifiable cell (D-018a): a non-DROPPED cell citing a
        manifest-declared casting whose prompt file is missing or
        unreadable cannot be re-derived against anything, so it cannot
        contribute coverage and is normalized to DROPPED in the
        aggregation exactly like a ghost cell (cell-verifiability rule,
        module docstring).
      * INTENT_COVERAGE_VERDICT_MISMATCH — when casting-prompt locatable,
        validator's three-anchor re-derivation disagrees with agent's
        cell.verdict.
      * INTENT_COVERAGE_DROPPED — F0.7 gate's primary block condition.
        An answer_id is DROPPED (gate-blocking) only when
        every casting's cell for it is DROPPED; a PROPAGATED or PARAPHRASED cell
        in any casting keeps the gate open for that answer, and per-cell DROPPED
        verdicts remain recorded in the matrix without blocking.
      * INTENT_COVERAGE_AGENT_USED_EMBEDDING — when --tool-call-log
        provided, any FORBIDDEN_AGENT_TOOLS match or any Bash pattern
        containing an embedding-API marker.
      * INTENT_COVERAGE_AGENT_USED_FUZZY_OVERLAP — when --tool-call-log
        provided, any Bash pattern containing a non-embedding fuzzy-
        overlap marker (scipy.spatial.distance / sklearn.metrics.pairwise).

    Pitfall avoidance:

      * Pitfall 2 (substring matching): re.search(rf"\\b{re.escape(answer_id)}\\b", ...)
        — word-boundary anchored so A-1 does NOT substring-match inside A-12.
      * Pitfall 3 (spec body vs appendix): scope ANSWER_BLOCK_RE /
        A_AUTO_BLOCK_RE search to text AFTER APPENDIX_HEADING_RE match.
      * Pitfall 4 (vacuous PROPAGATED): empty answer-set on v2.1 spec is
        a defect; rejection token fires.
      * Pitfall 5 (section-ref-only): section-number citations like
        ``[per spec §3.2]`` are NOT a citation surface; only A-NNN literals
        or typed-row ``[from A-NNN]`` count. Verdict goes to DROPPED for
        that cell — gate working as designed.
      * Pitfall 6 (PARAPHRASED severity creep): PARAPHRASED is a first-
        class PASS verdict; validator never block-routes on PARAPHRASED.
      * Pitfall 7 (A-AUTO-NNN forgotten): answer-set is union(ANSWER_BLOCK_RE,
        A_AUTO_BLOCK_RE); the matrix has rows for both. No special-casing.
      * Pitfall 9 (regex byte-equivalence): inline copies match validate-
        spec.py byte-for-byte; cross-script test catches drift.
    """
    failures: list[str] = []

    # ----- Step 1: JSON parse -----
    if not coverage_path.exists():
        print(
            f"INTENT_COVERAGE_SCHEMA_INVALID: coverage file missing: "
            f"{coverage_path}"
        )
        return 1
    coverage, coverage_problem = read_document(coverage_path)
    if coverage_problem is not None:
        print(f"INTENT_COVERAGE_SCHEMA_INVALID: malformed JSON: {coverage_problem}")
        return 1
    if not isinstance(coverage, dict):
        print(
            "INTENT_COVERAGE_SCHEMA_INVALID: top-level must be a JSON object, "
            f"got {type(coverage).__name__}"
        )
        return 1

    # ----- Step 2: Top-level schema closed (Pitfall A, layer 1) -----
    extra_top = set(coverage.keys()) - KNOWN_INTENT_COVERAGE_KEYS
    if extra_top:
        failures.append(
            f"INTENT_COVERAGE_SCHEMA_INVALID: extra top-level keys "
            f"{sorted(extra_top)!r}; only "
            f"{sorted(KNOWN_INTENT_COVERAGE_KEYS)!r} allowed"
        )

    matrix = coverage.get("matrix", [])
    if not isinstance(matrix, list):
        failures.append(
            "INTENT_COVERAGE_SCHEMA_INVALID: matrix field must be a JSON "
            f"array, got {type(matrix).__name__}"
        )
        matrix = []

    spec_format_version = coverage.get("spec_format_version")

    # Optional spec parse (one-shot, before per-cell loop).
    spec_text: str = ""
    spec_answer_ids: set[str] = set()
    if spec_path is not None:
        try:
            spec_text = spec_path.read_text(encoding="utf-8")
        except FileNotFoundError:
            failures.append(
                f"INTENT_COVERAGE_SCHEMA_INVALID: --spec path missing: "
                f"{spec_path}"
            )
            spec_text = ""
        if spec_text:
            spec_answer_ids = extract_answer_ids_from_spec(spec_text)

    # ----- Step 5: Vacuous-PROPAGATED check (only on v2.1+ specs) -----
    # Pitfall 4: empty answer-set on v2.1 spec is a defect (Phase 1
    # INTV-01 mandates ≥1 entry per v2.1 spec). v2.0 specs may legitimately
    # have empty appendices — they route through stream-skip elsewhere and
    # never hit this validator anyway.
    if (
        spec_path is not None
        and spec_text
        and not spec_answer_ids
        and _spec_format_version_meets_v21(spec_format_version)
    ):
        failures.append(
            "INTENT_COVERAGE_VACUOUS_PROPAGATED: zero A-NNN/A-AUTO-NNN "
            "entries found in spec's <Appendix: Interview Transcript> block; "
            f"v{_VACUOUS_PROPAGATED_MIN_VERSION[1:]} spec must have ≥1 entry "
            "per Phase 1 INTV-01"
        )

    # ----- Step 3+4+6+7+8: per-cell -----
    # Casting-prompt cache (str path -> prompt text, None = missing or
    # unreadable). Castings-dir resolution (D-018b): an explicit
    # --castings-dir wins (the Foundry-Intent-Coverage MCP gate always
    # passes the run's castings dir, so the checks no longer go dark when
    # the spec resolves outside the run dir via the state.json fallback);
    # the spec-adjacent derivation remains as the CLI-compat default.
    casting_prompt_cache: dict[str, str | None] = {}
    casting_dir: Path | None = None
    if castings_dir is not None:
        if castings_dir.is_dir():
            casting_dir = castings_dir
    elif spec_path is not None:
        candidate_castings_dir = spec_path.parent / "castings"
        if candidate_castings_dir.is_dir():
            casting_dir = candidate_castings_dir

    # D-014a: ghost-casting guard. When castings/manifest.json is loadable
    # next to the casting prompts, matrix casting_ids are validated against
    # manifest.castings[].id (str-normalized — foundry_spawn.py compares
    # str(c.get("id")) == str(casting_id), ids may be ints). None means
    # "manifest unavailable — check cannot run" (matrix-only validation),
    # mirroring the --spec-absent completeness stance.
    manifest_casting_ids = load_manifest_casting_ids(casting_dir)

    # The loop below emits per-cell failure lines only. The gate-blocking
    # per-answer aggregation runs AFTER the loop through
    # aggregate_matrix_coverage — the SAME function the
    # Foundry-Intent-Coverage gate tool calls (D-019: one rule, no layer
    # divergence). Spec-declared answers with NO recorded cell at all are
    # zero-coverage too — the spec→matrix completeness check below the
    # loop catches those (omission IS zero coverage).
    for idx, cell in enumerate(matrix):
        if not isinstance(cell, dict):
            failures.append(
                f"INTENT_COVERAGE_SCHEMA_INVALID: matrix[{idx}] is not a "
                f"JSON object, got {type(cell).__name__}"
            )
            continue

        # Step 3: Per-cell schema closed (Pitfall A, layer 2).
        extra_cell = set(cell.keys()) - KNOWN_CELL_KEYS
        if extra_cell:
            failures.append(
                f"INTENT_COVERAGE_SCHEMA_INVALID: matrix[{idx}] extra keys "
                f"{sorted(extra_cell)!r}; only "
                f"{sorted(KNOWN_CELL_KEYS)!r} allowed"
            )

        answer_id = cell.get("answer_id", "")
        casting_id = cell.get("casting_id", "")
        verdict = cell.get("verdict")

        # Step 3b (D-017) + 6b (D-014a) + 6c (D-018a): one classification —
        # cell_cannot_contribute_reason — drives the failure lines AND
        # (via aggregate_matrix_coverage below the loop) the gate
        # decision, so the two can never disagree (D-019).
        reason = cell_cannot_contribute_reason(
            cell,
            manifest_casting_ids=manifest_casting_ids,
            castings_dir=casting_dir,
            prompt_cache=casting_prompt_cache,
        )

        # Step 3b (D-017): required-key validation — the inverse of the
        # allow-list check above. Every matrix cell must carry answer_id,
        # casting_id, and verdict; a cell missing any of them is
        # structurally invalid (existing SCHEMA_INVALID token — GI-002)
        # and its verdict cannot contribute coverage.
        if reason == "missing_required_keys":
            missing_keys = sorted(
                k for k in REQUIRED_CELL_KEYS if not cell.get(k)
            )
            failures.append(
                f"INTENT_COVERAGE_SCHEMA_INVALID: matrix[{idx}] missing "
                f"required cell key(s) {missing_keys!r}; every matrix cell "
                f"must carry answer_id, casting_id, and verdict — a "
                f"structurally-invalid cell cannot contribute coverage"
            )

        # Step 4: verdict enum (a MISSING verdict is already flagged as a
        # missing required key above; this fires for present-but-unknown
        # values only).
        if verdict and verdict not in KNOWN_INTENT_COVERAGE_VERDICTS:
            failures.append(
                f"INTENT_COVERAGE_UNKNOWN_VERDICT: matrix[{idx}] verdict="
                f"{verdict!r}; only "
                f"{sorted(KNOWN_INTENT_COVERAGE_VERDICTS)!r} allowed"
            )

        # Step 6: dangling citation (only when --spec provided AND spec's
        # answer-set is non-empty).
        if (
            spec_path is not None
            and spec_answer_ids
            and answer_id
            and answer_id not in spec_answer_ids
        ):
            failures.append(
                f"INTENT_COVERAGE_DANGLING_CITATION: matrix[{idx}] "
                f"answer_id={answer_id!r} not present in spec appendix "
                f"answer-set ({sorted(spec_answer_ids)!r})"
            )

        # Step 6b (D-014a): ghost-casting cell. A cell citing a casting_id
        # the manifest does not declare is structurally-invalid coverage:
        # it is flagged with the MATRIX_INCOMPLETE token (GI-002 reuse —
        # justification in the docstring above) and counts as DROPPED in
        # the per-answer aggregation below, so an answer whose only
        # non-DROPPED cells are ghosts blocks as zero-coverage.
        if reason == "ghost_casting":
            failures.append(
                f"INTENT_COVERAGE_MATRIX_INCOMPLETE: matrix[{idx}] "
                f"casting_id={casting_id!r} not present in "
                f"castings/manifest.json casting ids "
                f"({sorted(manifest_casting_ids or set())!r}); a cell "
                f"citing an unknown casting cannot contribute coverage"
            )

        # Step 6c (D-018a): unverifiable-casting cell. The manifest
        # declares the casting but no readable prompt file exists, so
        # re-derivation has nothing to check the claimed verdict against.
        # Its non-DROPPED cells are flagged (same MATRIX_INCOMPLETE token
        # as ghosts — GI-002 reuse) and normalized to DROPPED in the
        # aggregation; a claimed-DROPPED cell contributes nothing either
        # way and is not flagged.
        if reason == "unverifiable_casting" and verdict != "DROPPED":
            failures.append(
                f"INTENT_COVERAGE_MATRIX_INCOMPLETE: matrix[{idx}] "
                f"casting_id={casting_id!r} is declared in "
                f"castings/manifest.json but casting-{casting_id}-prompt.md "
                f"is missing or unreadable; an unverifiable casting cannot "
                f"contribute coverage"
            )

        # Step 7: three-anchor verdict re-derivation — only for cells
        # whose classification is clean (all required keys present,
        # casting declared, prompt readable). For fixtures that don't
        # ship full casting-prompt trees (no castings dir resolvable), we
        # skip the re-derivation silently — the matrix's own DROPPED
        # markers are still sufficient to block the gate, and the MCP
        # gate discloses the skip via verification_checked=False.
        if (
            reason is None
            and casting_dir is not None
            and verdict in KNOWN_INTENT_COVERAGE_VERDICTS
        ):
            prompt_text = _read_casting_prompt(
                casting_dir, casting_id, casting_prompt_cache,
            )
            if prompt_text is not None:
                expected_verdict, _expected_chain = verdict_for_cell(
                    answer_id, prompt_text,
                )
                if expected_verdict != verdict:
                    failures.append(
                        f"INTENT_COVERAGE_VERDICT_MISMATCH: matrix[{idx}] "
                        f"answer_id={answer_id!r} casting_id="
                        f"{casting_id!r} agent_verdict={verdict!r} "
                        f"validator_re-derived={expected_verdict!r}"
                    )

    # Step 8: per-answer aggregation through THE shared function (D-019 —
    # the Foundry-Intent-Coverage gate tool calls the same one, so the
    # two layers cannot diverge). Cells that cannot be verified (missing
    # required keys, ghost casting, unverifiable casting) contribute
    # DROPPED regardless of their claimed verdict; cells without an
    # answer_id cannot be attributed to any answer and are excluded.
    cell_verdicts_by_answer, _verification_checked = aggregate_matrix_coverage(
        matrix,
        castings_dir=casting_dir,
        prompt_cache=casting_prompt_cache,
    )

    # Spec→matrix completeness (only when --spec provided and the appendix
    # answer-set is non-empty — same predicate as the dangling-citation
    # direction above, run spec→matrix instead of matrix→spec). Omission
    # IS zero coverage: an answer the intent-carrier never recorded a cell
    # for must not slip past the gate. Without --spec the appendix
    # answer-set is unknown and this check cannot run.
    missing_from_matrix: list[str] = []
    if spec_path is not None and spec_answer_ids:
        missing_from_matrix = sorted(
            spec_answer_ids - set(cell_verdicts_by_answer)
        )
        if missing_from_matrix:
            failures.append(
                f"INTENT_COVERAGE_MATRIX_INCOMPLETE: "
                f"{len(missing_from_matrix)} spec appendix answer_id(s) "
                f"have no matrix cell in any casting: "
                f"{missing_from_matrix!r}; omission IS zero coverage — "
                "these answers block the gate exactly like all-DROPPED "
                "answers"
            )

    # Per-answer aggregation: an answer blocks when every one of its
    # recorded cells is DROPPED (zero coverage across all castings) OR
    # when the spec declares it and the matrix carries no cell for it.
    zero_coverage_answers = sorted(
        set(missing_from_matrix)
        | {
            ans
            for ans, cell_verdicts in cell_verdicts_by_answer.items()
            if all(v == "DROPPED" for v in cell_verdicts)
        }
    )
    if zero_coverage_answers:
        failures.append(
            f"INTENT_COVERAGE_DROPPED: {len(zero_coverage_answers)} "
            f"answer_id(s) with zero coverage — no casting's cell is "
            f"PROPAGATED or PARAPHRASED: {zero_coverage_answers!r}; "
            "F0.7 gate blocks; route to F0.5 re-decompose with these IDs "
            "as guidance"
        )

    # ----- Step 9: code-blind / embedding-blind tool-call audit -----
    # Advisory shape: only fires when --tool-call-log passed (mirror of
    # Phase 7 advisory pattern).
    if tool_call_log_path is not None:
        try:
            calls_text = tool_call_log_path.read_text(encoding="utf-8")
        except FileNotFoundError as e:
            failures.append(
                f"INTENT_COVERAGE_SCHEMA_INVALID: --tool-call-log path "
                f"missing: {e}"
            )
            calls: Any = []
        else:
            try:
                calls = json.loads(calls_text)
            except json.JSONDecodeError as e:
                failures.append(
                    "INTENT_COVERAGE_SCHEMA_INVALID: --tool-call-log "
                    f"unreadable JSON: {e}"
                )
                calls = []
        if not isinstance(calls, list):
            failures.append(
                "INTENT_COVERAGE_SCHEMA_INVALID: --tool-call-log must be a "
                f"JSON array, got {type(calls).__name__}"
            )
            calls = []
        for call in calls:
            if not isinstance(call, dict):
                continue
            tool = call.get("tool", "")
            # Layer 2a: forbidden tool names.
            if tool in FORBIDDEN_AGENT_TOOLS:
                failures.append(
                    f"INTENT_COVERAGE_AGENT_USED_EMBEDDING: forbidden tool "
                    f"{tool!r} invoked"
                )
                continue
            # Layer 2b: forbidden Bash patterns (substring match against
            # call.pattern or call.command).
            if tool == "Bash":
                pattern_text = (
                    call.get("pattern", "")
                    or call.get("command", "")
                    or ""
                )
                pattern_text = str(pattern_text)
                for forbid in FORBIDDEN_BASH_PATTERNS:
                    if forbid in pattern_text:
                        token = _classify_bash_pattern(pattern_text, forbid)
                        failures.append(
                            f"{token}: Bash pattern {forbid!r} matched in "
                            f"{pattern_text!r}"
                        )
                        break  # one token per Bash call

    # ----- Emit + return -----
    for f in failures:
        print(f)
    return 1 if failures else 0


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description="Phase 8 / INTENT-01 intent-coverage.json validator"
    )
    parser.add_argument(
        "coverage_path",
        type=Path,
        help="Path to intent-coverage.json",
    )
    parser.add_argument(
        "--spec",
        dest="spec_path",
        type=Path,
        default=None,
        help=(
            "Optional path to spec.md; when provided, dangling-citation, "
            "vacuous-PROPAGATED, spec-to-matrix completeness "
            "(INTENT_COVERAGE_MATRIX_INCOMPLETE), and three-anchor verdict "
            "re-derivation checks run. Without it the completeness check "
            "cannot run; the MCP gate supplies it whenever the run's "
            "spec is resolvable."
        ),
    )
    parser.add_argument(
        "--castings-dir",
        dest="castings_dir",
        type=Path,
        default=None,
        help=(
            "Optional path to the run's castings/ directory (manifest.json "
            "+ casting-{id}-prompt.md files). When provided it wins over "
            "the spec-adjacent derivation, so the ghost-casting, "
            "unverifiable-casting, and verdict re-derivation checks run "
            "even when the spec resolves outside the run dir (D-018b). "
            "The Foundry-Intent-Coverage MCP gate always passes it. "
            "Without it, the directory is derived next to --spec for CLI "
            "compatibility; when neither resolves, those checks cannot "
            "run."
        ),
    )
    parser.add_argument(
        "--tool-call-log",
        dest="tool_call_log_path",
        type=Path,
        default=None,
        help=(
            "Optional path to a JSON array of {tool, target_path|pattern|"
            "command} records; code-blind / embedding-blind audit "
            "(INTENT_COVERAGE_AGENT_USED_EMBEDDING / "
            "INTENT_COVERAGE_AGENT_USED_FUZZY_OVERLAP) runs when provided."
        ),
    )
    args = parser.parse_args(argv[1:])
    return validate_intent_coverage(
        args.coverage_path,
        spec_path=args.spec_path,
        tool_call_log_path=args.tool_call_log_path,
        castings_dir=args.castings_dir,
    )


if __name__ == "__main__":
    sys.exit(main(sys.argv))
