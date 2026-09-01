#!/usr/bin/env python3
"""Phase 7 / TEST-01 — deterministic output validator for test_observations channel.

Mirrors plugins/forge/scripts/validate_spec_review.py shape beat-for-beat
(Phase 6 / PROBE-01 reference, 276 LOC). Closed-vocabulary discipline:

* KNOWN_TEST_OBSERVATION_KEYS — top-level closed schema
* KNOWN_OBSERVATION_KEYS — per-observation closed schema
* KNOWN_OBSERVATION_STATUSES — status enum allowlist
* KNOWN_TEST_OBSERVATION_VERDICTS — assay_verdict enum allowlist
  (OPTIONAL adjudicator-appended key; absent = not yet adjudicated)
* KNOWN_TEST_DERIVER_FAILURE_TOKENS — 9-token failure vocabulary
* FORBIDDEN_SOURCE_ROOTS — code-blind audit denylist
* ALLOWED_READ_PREFIXES — code-blind audit allowlist (by exception)

The validator runs at F2 INSPECT stream completion before observations
land in the test_observations channel.

Discipline mirrors:

  * Phase 6 / PROBE-01 — KNOWN_REVIEW_KEYS / KNOWN_FLAG_KEYS two-layer
    closed-vocabulary auto-resolve smuggling defense (Pitfall A).
  * Phase 5 / EVID-02 — # evidence-for: header parser regex byte-equivalence
    via shared single-source-of-truth _REQUIREMENT_ID_RE.
  * Phase 4 / EVID-01 — closed-vocabulary failure tokens collapsed under
    a small frozenset surface; sub-pattern detail surfaces in
    failure_detail (not as separate top-level tokens).

Exits 0 on pass, 1 on any failure, 2 on usage error.

Usage:
    validate-test-observations.py <observation.json> \\
        [--spec <spec.md>] [--tool-call-log <log.json>]

This script is the authoritative TEST-01 INSPECT-stream gate. The
spec-test-deriver agent's prompt rubric is advisory; this script is
load-bearing. If the script fails, the F2 INSPECT TEST-01 stream's
observations must not be considered eligible for ASSAY routing.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Constants — closed vocabularies
# ---------------------------------------------------------------------------

# CLOSED VOCABULARY — top-level keys allowed in
# test_observations/test-deriver-cycle-{N}.json.
# Pitfall A (closed-vocab smuggling): any extra top-level field is rejected
# with TEST_OBSERVATION_SCHEMA_INVALID. Mirrors Phase 6 KNOWN_REVIEW_KEYS
# rejection of suggested_fix / recommendation auto-resolve smuggling.
# Extend only via phase-level RFC.
KNOWN_TEST_OBSERVATION_KEYS = frozenset(
    {
        "stream",
        "cycle",
        "spec_format_version",
        "spec_hash",
        "agent_path",
        "wall_clock_seconds",
        "uvx_subprocess_seconds",
        "observations",
    }
)

# CLOSED VOCABULARY — keys allowed inside each per-observation entry.
# Mirrors KNOWN_TEST_OBSERVATION_KEYS discipline at the observation level
# (parallel to Phase 6 KNOWN_FLAG_KEYS per-flag closed schema).
# `assay_verdict` is OPTIONAL and adjudicator-appended: the
# test-observations-adjudicator appends it per observation at ASSAY, so
# a channel file must validate before AND after adjudication. The
# spec-test-deriver never emits it. Absent = not yet adjudicated —
# never itself a failure. When present, its value is enforced against
# KNOWN_TEST_OBSERVATION_VERDICTS below.
KNOWN_OBSERVATION_KEYS = frozenset(
    {
        "observation_id",
        "test_path",
        "tests_spec",
        "derived_from_contract_row",
        "hypothesis_seed",
        "status",
        "captured_output",
        "negative_assertion_present",
        "shape_not_value_check",
        "citation_chain",
        "assay_verdict",
    }
)

# CLOSED VOCABULARY — observation.status enum.
# Pitfall: free-form status strings smuggle in advisory tiers. Validator
# enforces FAIL/ERROR/SKIP/PASS only.
KNOWN_OBSERVATION_STATUSES = frozenset({"FAIL", "ERROR", "SKIP", "PASS"})

# CLOSED VOCABULARY — observation.assay_verdict enum (OPTIONAL key,
# adjudicator-appended). Mirrors the test-observations-adjudicator
# agent contract's KNOWN_TEST_OBSERVATION_VERDICTS byte-for-byte:
# three members, no more. Pitfall: free-form verdict strings (e.g. an
# informational "INFO" tier) smuggle in an open vocabulary; PASS
# observations carry NO verdict — the adjudicator omits the field.
# Extend only via phase-level RFC.
KNOWN_TEST_OBSERVATION_VERDICTS = frozenset(
    {"DEFECT", "WRONG_TEST", "INCONCLUSIVE"}
)

# CLOSED VOCABULARY — failure tokens emitted by this validator.
# 9 tokens locked; mirrors Phase 4's 8-token KNOWN_EVIDENCE_FAILURE_TOKENS
# closed-vocabulary discipline. Sub-pattern detail (e.g., specific forbidden
# root that triggered WRONG_TEST_SOURCE_LEAK) surfaces in failure_detail
# string AFTER the token, not as a parallel token.
KNOWN_TEST_DERIVER_FAILURE_TOKENS = frozenset(
    {
        "TEST_DERIVER_READ_SOURCE",
        "TEST_HEADER_MISSING",
        "TEST_HEADER_DANGLING_REQ",
        "WRONG_TEST_NO_NEGATIVE_ASSERTION",
        "WRONG_TEST_VALUE_NOT_SHAPE",
        "WRONG_TEST_SOURCE_LEAK",
        "WRONG_TEST_HEADER_MISSING",
        "TEST_OBSERVATION_SCHEMA_INVALID",
        "TEST_OBSERVATION_UNKNOWN_STATUS",
    }
)

# Code-blind discipline denylist. Any Read/Grep/Glob targeting a path that
# matches one of these prefixes is a TEST_DERIVER_READ_SOURCE violation
# (unless the path matches an ALLOWED_READ_PREFIXES entry, which wins).
# Pitfall D (forbidden-root substring matching): comparison uses anchored
# substring match (^|/) so "lib/" does NOT match inside "library/".
FORBIDDEN_SOURCE_ROOTS = frozenset(
    {
        "src/",
        "app/",
        "lib/",
        "internal/",
        "pkg/",
        "cmd/",
        "plugins/foundry/agents/",
        "plugins/foundry/scripts/",
        "plugins/forge/agents/",
        "plugins/forge/scripts/",
        "plugins/foundry/mcp-server/src/",
    }
)

# Code-blind discipline allowlist. The spec-test-deriver agent reads ONLY
# from foundry-archive/{run}/ — spec.md, transcript.md, and the
# test_observations/ subdir it writes its own outputs to.
ALLOWED_READ_PREFIXES: tuple[str, ...] = ("foundry-archive/",)

# Path-or-module token shape shared by the 7c source-leak scan's
# contract-surface exemption and the `## Contracts` surface-column
# parser: a maximal run of word chars, dots, slashes, and hyphens —
# the shape of file paths, CLI entrypoints, and dotted module refs.
_PATH_TOKEN_RE = re.compile(r"[\w./-]+")


def _build_forbidden_root_patterns() -> list[tuple[str, "re.Pattern[str]"]]:
    """Precompile the anchored forbidden-root patterns used by check 7c.

    Byte-identical semantics to the historical inline construction:
    per root, TWO alternatives —

      1. literal "src/" with leading boundary (slash, whitespace,
         paren, or start-of-line) — catches "from src/handlers" prose,
         file-path references like "src/handlers/login.py", and
         bare-leading "src/".
      2. Python-import dotted form "src." — trailing slash replaced
         with a dot. Catches "from src.handlers" and
         "import src.handlers". Same boundary as (1).

    Anchored boundary match (Pitfall D): ^src/ or /src/ but NOT
    library/ or my-src/. Iteration order is sorted for deterministic
    failure messages when multiple roots match.
    """
    patterns: list[tuple[str, "re.Pattern[str]"]] = []
    for root in sorted(FORBIDDEN_SOURCE_ROOTS):
        root_dotted = root.rstrip("/").replace("/", ".") + "."
        slashed = re.escape(root)
        dotted = re.escape(root_dotted)
        patterns.append(
            (
                root,
                re.compile(
                    r"(?:^|[\s/(])(?:" + slashed + r"|" + dotted + r")",
                    re.MULTILINE,
                ),
            )
        )
    return patterns


_FORBIDDEN_ROOT_PATTERNS = _build_forbidden_root_patterns()

# Future enhancement (Pitfall C) — Jaccard prose-overlap heuristic for
# "literal == comparison whose RHS overlaps spec prose at >= 0.7" detection
# is documented in 07-CONTEXT.md but NOT enforced in v1. v1 trusts the
# agent's self-reported shape_not_value_check field; the threshold is
# defined here for symmetry with Phase 2 typed-table Jaccard but
# unreferenced until v2.
VALUE_NOT_SHAPE_JACCARD_THRESHOLD = 0.7


# ---------------------------------------------------------------------------
# Regexes — single-source-of-truth from evidence.py byte-equivalent
# ---------------------------------------------------------------------------

# Reuse evidence.py's regex byte-equivalent — single source of truth across
# Phases 4/5/7. Inlined rather than imported because this script is invoked
# standalone via subprocess; the MCP server is not necessarily installed in
# the validator's environment. The Phase 8 INTENT-01 grep contract relies
# on this regex matching the same FR-N/US-N IDs as Phase 5's
# # evidence-for: parser does.
# Byte-equivalent to:
#   plugins/foundry/mcp-server/src/foundry_mcp/tools/evidence.py:_REQUIREMENT_ID_RE
_REQUIREMENT_ID_RE = re.compile(r"\b(?:US|FR)-\d+\b")

# Header parser for `# tests-spec: FR-N, US-M` lines on first non-blank
# line of generated test files. Byte-equivalent shape to evidence.py's
# `# evidence-for:` parser (Phase 5 EVID-02). Phase 8 INTENT-01 grep
# contract: this regex must match the exact same FR-N/US-N IDs as
# `# evidence-for:` headers do.
_TESTS_SPEC_HEADER_RE = re.compile(
    r"^#\s*tests-spec:\s*((?:US-\d+|FR-\d+)(?:\s*,\s*(?:US-\d+|FR-\d+))*)\s*$"
)


# ---------------------------------------------------------------------------
# Contract-surface exemption (AC-006/AC-007) — `## Contracts` parsing
# ---------------------------------------------------------------------------


def _contract_surface_leak_tokens(
    spec_text: str,
) -> tuple[frozenset[str], list[str]]:
    """Path/module tokens declared in the spec's ``## Contracts`` surface
    column that would otherwise trip the forbidden-root scan.

    Shared GI-003 sentence (byte-mirrored, modulo comment prefix and
    wrapping, in spec-test-deriver.md wrong-test pattern 3):
    Referencing or executing a surface named in the spec's
    `## Contracts` table is never a source leak; referencing
    symbols absent from both the spec and the contracts table
    still is.

    Shared GI-003 statement (byte-mirrored, modulo comment prefix and
    wrapping, in spec-test-deriver.md wrong-test pattern 3):
    Contracts parsing rule (mechanical): the Contracts section
    opens at any markdown heading of level 2-6 whose heading text
    begins with `Contracts` (case-insensitive; suffixed headings
    like `Contracts (TYPE-01)` are tolerated, mirroring forge
    validate-spec.py's startswith section matching), and the
    surface column is the first header-row cell whose lowercased
    text begins with `surface` (so `surface (CLI)` and
    `Surface / entrypoint` qualify). When a Contracts section has
    table rows but no such header cell, the validator prints a
    non-fatal `note:` diagnostic to stderr — no failure token,
    exit code unchanged — so an exemption blackout is visible
    instead of silent.

    Mechanics: locate the Contracts section per the parsing rule
    above, find the surface column, then for each data row extract
    every path token (backticks stripped) that trips a
    forbidden-root pattern when standing alone. Those tokens are the
    declared surfaces the 7c scan exempts (via
    :func:`_mask_contract_surface_tokens`). The ``./`` normalization
    is symmetric: a ``./``-prefixed token also declares its
    unprefixed form AND an unprefixed token also declares its
    ``./``-prefixed form, so ``./cmd/mytool`` and ``cmd/mytool``
    reference the same surface in either direction. Prose,
    input/output/errors cells, and every other spec section
    contribute NO exemptions — a spec's file-changes table naming
    ``src/`` paths does not sanction referencing them.

    Returns ``(tokens, notes)`` — the declared-surface token set plus
    zero or more non-fatal diagnostic lines the caller surfaces on
    stderr. "Contracts table legitimately names no forbidden-root
    surface" yields ``(frozenset(), [])`` (benign, silent);
    "Contracts section has table rows but no identifiable surface
    column" yields a diagnostic so the two states are
    distinguishable.
    """
    tokens: set[str] = set()
    notes: list[str] = []
    in_contracts = False
    surface_idx: int | None = None
    saw_contracts_rows = False
    found_surface_column = False
    for line in spec_text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            heading = re.match(r"#{2,6}\s*(.*)", stripped)
            in_contracts = bool(
                heading
                and heading.group(1).strip().lower().startswith(
                    "contracts"
                )
            )
            surface_idx = None
            continue
        if not in_contracts or not stripped.startswith("|"):
            continue
        saw_contracts_rows = True
        cells = [c.strip() for c in stripped.strip("|").split("|")]
        lowered = [c.lower() for c in cells]
        if surface_idx is None:
            for i, cell in enumerate(lowered):
                if cell.startswith("surface"):
                    surface_idx = i
                    found_surface_column = True
                    break
            continue
        if surface_idx >= len(cells):
            continue
        cell_text = cells[surface_idx].replace("`", " ")
        for token in _PATH_TOKEN_RE.findall(cell_text):
            if any(p.search(token) for _, p in _FORBIDDEN_ROOT_PATTERNS):
                tokens.add(token)
                if token.startswith("./"):
                    tokens.add(token[2:])
                else:
                    tokens.add("./" + token)
    if saw_contracts_rows and not found_surface_column:
        notes.append(
            "note: --spec Contracts section has table rows but no "
            "header cell beginning with 'surface'; contract-surface "
            "exemption inactive (zero declared surfaces)"
        )
    return frozenset(tokens), notes


def _mask_contract_surface_tokens(
    text: str, tokens: frozenset[str]
) -> str:
    """Replace verbatim declared-surface tokens with an inert marker.

    The leading boundary class is a SUPERSET of the 7c scan's leading
    boundary class (``(?:^|[\\s/(])``): every position where the scan
    can detect a forbidden root — start-of-line, whitespace, ``/``,
    ``(`` — is a position the mask can exempt. In particular ``/`` is
    a valid mask boundary, so a declared surface referenced by
    absolute path (``File "/home/u/proj/src/cli.py"`` — the shape a
    real failing test's traceback prints), by ``./`` prefix, or by a
    repo-relative parent path is masked, never false-leaked
    (AC-006/AC-007). The trailing boundary stays strict so
    ``src/cli.py`` masks inside ``python src/cli.py --help`` but NOT
    inside ``src/cli.pyc`` (a different, undeclared path), and a
    declared bare root like ``src/`` never blanket-exempts paths
    beneath it. Longest token first so a long declared path is masked
    before any shorter declared token that happens to nest inside it.
    With an empty token set (no --spec, or a contracts table naming
    no forbidden-root surfaces) this is the identity function — the
    7c scan then behaves exactly as it always has.
    """
    for token in sorted(tokens, key=len, reverse=True):
        text = re.sub(
            r"(?<![\w.-])" + re.escape(token) + r"(?![\w./-])",
            "CONTRACT-SURFACE",
            text,
        )
    return text


# ---------------------------------------------------------------------------
# Core validator
# ---------------------------------------------------------------------------


def validate_test_observations(
    observation_path: Path,
    *,
    spec_path: Path | None = None,
    tool_call_log_path: Path | None = None,
) -> int:
    """Validate test-deriver-cycle-{N}.json against the closed-vocab schema.

    Returns exit code: 0 on pass, 1 on any failure.

    Failure modes (each appends a token-prefixed line to stdout before the
    function returns 1):

      * TEST_OBSERVATION_SCHEMA_INVALID — malformed JSON, extra top-level
        keys, extra per-observation keys, malformed observations list, or
        an assay_verdict value outside KNOWN_TEST_OBSERVATION_VERDICTS
        (the verdict-value detail surfaces after the token, not as a
        parallel token — the 9-token roster stays closed; the value
        check is total: non-string values, including unhashable JSON
        arrays/objects, reject with this token, never a traceback).
      * TEST_OBSERVATION_UNKNOWN_STATUS — status not in
        KNOWN_OBSERVATION_STATUSES (total over every JSON value
        class: non-string status values reject with this same token).
      * TEST_HEADER_MISSING — observation.tests_spec is empty (channel-side
        token; co-fires with WRONG_TEST_HEADER_MISSING for diagnostic
        precision per CONTEXT.md "diagnostic-precision-over-composite").
      * TEST_HEADER_DANGLING_REQ — when --spec provided, FR/US IDs in
        tests_spec that don't appear in spec's <spec_requirements> block.
      * WRONG_TEST_NO_NEGATIVE_ASSERTION — observation.negative_assertion_present
        is false (fires on PASS/FAIL/ERROR; never on status SKIP — a
        SKIP-on-no-surface observation has no test body to assert).
      * WRONG_TEST_VALUE_NOT_SHAPE — observation.shape_not_value_check ==
        "failed" (fires on PASS/FAIL/ERROR; never on status SKIP — same
        no-test-body exemption as 7a).
      * WRONG_TEST_SOURCE_LEAK — observation.test_path or .captured_output
        contains anchored substring match against any FORBIDDEN_SOURCE_ROOTS
        entry (Pitfall D), after verbatim references to surfaces declared
        in the --spec's `## Contracts` table are masked (AC-006/AC-007;
        no --spec means no masking).
      * WRONG_TEST_HEADER_MISSING — observation.tests_spec is empty
        (wrong-test stub-pattern token; co-fires with TEST_HEADER_MISSING).
      * TEST_DERIVER_READ_SOURCE — when --tool-call-log provided, any
        Read/Grep/Glob call targeting a FORBIDDEN_SOURCE_ROOTS path that
        is NOT under an ALLOWED_READ_PREFIXES path.

    Pitfall avoidance:

      * Pitfall A (closed-vocab smuggling): top-level + per-observation
        BOTH closed (mirror Phase 6 two-layer KNOWN_REVIEW_KEYS +
        KNOWN_FLAG_KEYS).
      * Pitfall B (status-gating the wrong-test patterns): 7a/7b run on
        every status EXCEPT "SKIP" (a happy-path PASS without a negative
        branch is the canonical wrong-test, so PASS is NOT exempt; a
        SKIP-on-no-surface observation has no test body, so SKIP is).
        7c (SOURCE_LEAK) and the header rules run on ALL statuses.
      * Pitfall C (Jaccard prose-overlap): not in v1; v1 trusts the
        agent's self-reported shape_not_value_check field. Threshold
        constant defined for symmetry but unused.
      * Pitfall D (forbidden-root substring matching): re.search with
        anchored boundaries (^|/) avoids "lib/" matching inside "library/".
    """
    failures: list[str] = []

    # ----- Step 1: JSON parse -----
    try:
        observation = json.loads(observation_path.read_text())
    except FileNotFoundError as e:
        print(
            f"TEST_OBSERVATION_SCHEMA_INVALID: observation file missing: {e}"
        )
        return 1
    except json.JSONDecodeError as e:
        print(f"TEST_OBSERVATION_SCHEMA_INVALID: malformed JSON: {e}")
        return 1
    if not isinstance(observation, dict):
        print(
            "TEST_OBSERVATION_SCHEMA_INVALID: top-level must be a JSON object, "
            f"got {type(observation).__name__}"
        )
        return 1

    # ----- Step 2: Top-level schema closed (Pitfall A, layer 1) -----
    extra_top = set(observation.keys()) - KNOWN_TEST_OBSERVATION_KEYS
    if extra_top:
        failures.append(
            f"TEST_OBSERVATION_SCHEMA_INVALID: extra top-level keys "
            f"{sorted(extra_top)!r}; only "
            f"{sorted(KNOWN_TEST_OBSERVATION_KEYS)!r} allowed"
        )

    observations = observation.get("observations", [])
    if not isinstance(observations, list):
        failures.append(
            "TEST_OBSERVATION_SCHEMA_INVALID: observations field must be a "
            f"JSON array, got {type(observations).__name__}"
        )
        observations = []

    # Optional spec parse — done once before the per-observation loop so the
    # spec-requirement-ID set is available for every dangling-req check and
    # the contract-surface exemption tokens are available for every 7c scan.
    spec_requirement_ids: set[str] = set()
    contract_surface_tokens: frozenset[str] = frozenset()
    if spec_path is not None:
        try:
            spec_text = spec_path.read_text()
        except FileNotFoundError:
            failures.append(
                f"TEST_OBSERVATION_SCHEMA_INVALID: --spec path missing: "
                f"{spec_path}"
            )
            spec_text = ""
        if spec_text:
            # Extract <spec_requirements> block first; fall back to whole-spec
            # grep when the block is absent (legacy v2.0 specs may not carry
            # the block but still have FR/US ID mentions in prose).
            m = re.search(
                r"<spec_requirements>(.*?)</spec_requirements>",
                spec_text,
                re.DOTALL,
            )
            if m:
                spec_requirement_ids = set(
                    _REQUIREMENT_ID_RE.findall(m.group(1))
                )
            if not spec_requirement_ids:
                spec_requirement_ids = set(
                    _REQUIREMENT_ID_RE.findall(spec_text)
                )
            contract_surface_tokens, surface_notes = (
                _contract_surface_leak_tokens(spec_text)
            )
            # Non-fatal diagnostics (e.g. exemption blackout: Contracts
            # rows present but no surface column identified). Stderr,
            # not stdout — never a failure token, never an exit-code
            # change (GI-002 keeps the 9-token roster closed).
            for note in surface_notes:
                print(note, file=sys.stderr)

    # ----- Step 3+4+5+6+7: per-observation -----
    for idx, obs in enumerate(observations):
        if not isinstance(obs, dict):
            failures.append(
                f"TEST_OBSERVATION_SCHEMA_INVALID: observations[{idx}] is "
                f"not a JSON object, got {type(obs).__name__}"
            )
            continue

        obs_id = obs.get("observation_id", f"OBS-?{idx}")

        # Step 3: Per-observation schema closed (Pitfall A, layer 2).
        extra_obs = set(obs.keys()) - KNOWN_OBSERVATION_KEYS
        if extra_obs:
            failures.append(
                f"TEST_OBSERVATION_SCHEMA_INVALID: {obs_id} extra keys "
                f"{sorted(extra_obs)!r}; only "
                f"{sorted(KNOWN_OBSERVATION_KEYS)!r} allowed"
            )

        # Step 4: status enum. Type-guarded so the membership test is
        # total over every JSON value class: non-string values
        # (including unhashable arrays/objects, which a bare frozenset
        # membership test would crash on) reject with the same token,
        # never with a traceback.
        status = obs.get("status")
        if (
            not isinstance(status, str)
            or status not in KNOWN_OBSERVATION_STATUSES
        ):
            failures.append(
                f"TEST_OBSERVATION_UNKNOWN_STATUS: {obs_id} status="
                f"{status!r}; only "
                f"{sorted(KNOWN_OBSERVATION_STATUSES)!r} allowed"
            )

        # Step 4b: assay_verdict enum — OPTIONAL adjudicator-appended
        # key. Absent means the observation is not yet adjudicated and
        # is never itself a failure; the check runs only when the key
        # is present. Value outside KNOWN_TEST_OBSERVATION_VERDICTS
        # (e.g. a free-form "INFO" tier) is rejected under the
        # existing TEST_OBSERVATION_SCHEMA_INVALID token so the
        # 9-token failure roster stays closed. Type-guarded like the
        # status enum: non-string values (including unhashable
        # arrays/objects) reject with the token, never a traceback.
        if "assay_verdict" in obs and (
            not isinstance(obs["assay_verdict"], str)
            or obs["assay_verdict"] not in KNOWN_TEST_OBSERVATION_VERDICTS
        ):
            failures.append(
                f"TEST_OBSERVATION_SCHEMA_INVALID: {obs_id} "
                f"assay_verdict={obs['assay_verdict']!r}; only "
                f"{sorted(KNOWN_TEST_OBSERVATION_VERDICTS)!r} allowed"
            )

        # Step 5: tests_spec header check. Both tokens fire when empty —
        # diagnostic precision per CONTEXT.md "diagnostic-precision-over-
        # composite" (TEST_HEADER_MISSING is the channel-side missing-
        # header token; WRONG_TEST_HEADER_MISSING is the wrong-test
        # stub-pattern token).
        tests_spec = obs.get("tests_spec", []) or []
        if not tests_spec:
            failures.append(
                f"TEST_HEADER_MISSING: {obs_id} tests_spec is empty"
            )
            failures.append(
                f"WRONG_TEST_HEADER_MISSING: {obs_id} tests_spec is empty "
                "(wrong-test stub pattern; cross-reference of "
                "TEST_HEADER_MISSING)"
            )
        else:
            # Step 6: dangling FR check — only when --spec provided AND
            # the spec's requirement-ID set is non-empty (else there's
            # nothing to compare against).
            if spec_path is not None and spec_requirement_ids:
                cited = set(tests_spec)
                dangling = cited - spec_requirement_ids
                if dangling:
                    failures.append(
                        f"TEST_HEADER_DANGLING_REQ: {obs_id} cited "
                        f"{sorted(dangling)!r} not in spec "
                        "<spec_requirements> "
                        f"({sorted(spec_requirement_ids)!r})"
                    )

        # Step 7: wrong-test stub patterns.
        # The "wrong-test" concept is about tests whose STATUS doesn't
        # faithfully reflect spec compliance: a happy-path PASS without
        # a negative branch is the canonical wrong-test (test passes but
        # the absence is the bug). FAIL/ERROR wrong-tests catch the
        # rest of the surface (literal-value asserts that happen to
        # diverge, source-leak imports, missing headers). Gating 7a/7b
        # on status != PASS would silence the most important signal — a
        # passing test that shouldn't have been written that way. The
        # ONE status 7a/7b never fire on is "SKIP" (AC-008): a
        # SKIP-on-no-surface observation reports that no test was
        # written, so shape rules that presume an executed test body
        # have nothing to judge.
        #
        # Shared GI-003 statement (byte-mirrored, modulo comment prefix
        # and wrapping, in spec-test-deriver.md § Test Derivation
        # Procedure):
        # Truthful SKIP shape: a SKIP-on-no-surface observation
        # carries `status: SKIP`, a `captured_output` reason naming
        # the missing surface, `negative_assertion_present: false`
        # (no test body exists to assert anything), and
        # `shape_not_value_check: passed` (vacuous — no assertions
        # were written). Wrong-test rules 7a (negative-assertion
        # mandate) and 7b (shape-not-value rule) presume an executed
        # test body and never fire on `status: SKIP`; the source-leak
        # scan (7c) and the header rules still apply to SKIP
        # observations.

        # 7a: negative-assertion mandate (never fires on SKIP).
        if (
            status != "SKIP"
            and obs.get("negative_assertion_present") is False
        ):
            failures.append(
                f"WRONG_TEST_NO_NEGATIVE_ASSERTION: {obs_id} "
                "negative_assertion_present=false"
            )
        # 7b: shape-not-value rule (never fires on SKIP).
        if (
            status != "SKIP"
            and obs.get("shape_not_value_check") == "failed"
        ):
            failures.append(
                f"WRONG_TEST_VALUE_NOT_SHAPE: {obs_id} "
                "shape_not_value_check=failed"
            )
        # 7c: source-leak detection in test_path + captured_output.
        # Shared GI-003 sentence (byte-mirrored, modulo comment prefix
        # and wrapping, in spec-test-deriver.md wrong-test pattern 3):
        # Referencing or executing a surface named in the spec's
        # `## Contracts` table is never a source leak; referencing
        # symbols absent from both the spec and the contracts table
        # still is.
        #
        # Shared GI-003 statement (byte-mirrored likewise, in
        # spec-test-deriver.md wrong-test pattern 3):
        # Contract-surface exemption (mechanical rule): when `--spec`
        # is provided, the validator parses the spec's `## Contracts`
        # table surface column and masks verbatim references to its
        # declared path or module tokens before the forbidden-root
        # scan; every forbidden-root reference that survives the
        # masking still leaks. When no `--spec` is passed there is no
        # contracts table to consult, so no exemption applies and
        # every forbidden-root reference leaks — the adjudicator
        # always passes `--spec`, so production adjudication always
        # honors the exemption.
        target_text = (
            str(obs.get("test_path", ""))
            + "\n"
            + str(obs.get("captured_output", ""))
        )
        scan_text = _mask_contract_surface_tokens(
            target_text, contract_surface_tokens
        )
        for root, pattern in _FORBIDDEN_ROOT_PATTERNS:
            if pattern.search(scan_text):
                failures.append(
                    f"WRONG_TEST_SOURCE_LEAK: {obs_id} references "
                    f"forbidden root {root!r} in test_path or "
                    "captured_output"
                )
                break  # one source-leak token per observation
        # 7d: tests_spec empty — already covered in step 5 (both
        # TEST_HEADER_MISSING and WRONG_TEST_HEADER_MISSING fire there
        # for diagnostic precision); no second emission here.

    # ----- Step 8: code-blind tool-call audit -----
    if tool_call_log_path is not None:
        try:
            calls_text = tool_call_log_path.read_text()
        except FileNotFoundError as e:
            failures.append(
                f"TEST_OBSERVATION_SCHEMA_INVALID: --tool-call-log path "
                f"missing: {e}"
            )
            calls = []
        else:
            try:
                calls = json.loads(calls_text)
            except json.JSONDecodeError as e:
                failures.append(
                    "TEST_OBSERVATION_SCHEMA_INVALID: --tool-call-log "
                    f"unreadable JSON: {e}"
                )
                calls = []
        if not isinstance(calls, list):
            failures.append(
                "TEST_OBSERVATION_SCHEMA_INVALID: --tool-call-log must be a "
                f"JSON array, got {type(calls).__name__}"
            )
            calls = []
        for call in calls:
            if not isinstance(call, dict):
                continue
            tool = call.get("tool", "")
            if tool not in {"Read", "Grep", "Glob"}:
                continue
            target = call.get("target_path") or call.get("pattern") or ""
            target_str = str(target)
            # Allowed prefix wins (early-out). Only foundry-archive/* reads
            # are unconditionally permitted; any other path goes through
            # the FORBIDDEN_SOURCE_ROOTS scan.
            if any(
                target_str.startswith(p) for p in ALLOWED_READ_PREFIXES
            ):
                continue
            for root in FORBIDDEN_SOURCE_ROOTS:
                pattern = r"(?:^|/)" + re.escape(root)
                if re.search(pattern, target_str):
                    failures.append(
                        f"TEST_DERIVER_READ_SOURCE: tool {tool!r} read "
                        f"{target_str!r} (forbidden root {root!r})"
                    )
                    break

    # ----- Emit + return -----
    for f in failures:
        print(f)
    return 1 if failures else 0


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description="Phase 7 / TEST-01 test_observations validator"
    )
    parser.add_argument(
        "observation_path",
        type=Path,
        help="Path to test-deriver-cycle-{N}.json",
    )
    parser.add_argument(
        "--spec",
        dest="spec_path",
        type=Path,
        default=None,
        help=(
            "Optional path to spec.md; when provided, dangling-FR/US "
            "checks are run against the <spec_requirements> block."
        ),
    )
    parser.add_argument(
        "--tool-call-log",
        dest="tool_call_log_path",
        type=Path,
        default=None,
        help=(
            "Optional path to a JSON array of {tool, target_path|pattern} "
            "records; code-blind audit (TEST_DERIVER_READ_SOURCE) runs "
            "when provided."
        ),
    )
    args = parser.parse_args(argv[1:])
    return validate_test_observations(
        args.observation_path,
        spec_path=args.spec_path,
        tool_call_log_path=args.tool_call_log_path,
    )


if __name__ == "__main__":
    sys.exit(main(sys.argv))
