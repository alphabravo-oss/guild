#!/usr/bin/env python3
"""Phase 7 / TEST-01 — deterministic output validator for test_observations channel.

Mirrors plugins/forge/scripts/validate_spec_review.py shape beat-for-beat
(Phase 6 / PROBE-01 reference, 276 LOC). Closed-vocabulary discipline:

* KNOWN_TEST_OBSERVATION_KEYS — top-level closed schema
* KNOWN_OBSERVATION_KEYS — per-observation closed schema
* KNOWN_OBSERVATION_STATUSES — status enum allowlist
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
    }
)

# CLOSED VOCABULARY — observation.status enum.
# Pitfall: free-form status strings smuggle in advisory tiers. Validator
# enforces FAIL/ERROR/SKIP/PASS only.
KNOWN_OBSERVATION_STATUSES = frozenset({"FAIL", "ERROR", "SKIP", "PASS"})

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
        keys, extra per-observation keys, malformed observations list.
      * TEST_OBSERVATION_UNKNOWN_STATUS — status not in
        KNOWN_OBSERVATION_STATUSES.
      * TEST_HEADER_MISSING — observation.tests_spec is empty (channel-side
        token; co-fires with WRONG_TEST_HEADER_MISSING for diagnostic
        precision per CONTEXT.md "diagnostic-precision-over-composite").
      * TEST_HEADER_DANGLING_REQ — when --spec provided, FR/US IDs in
        tests_spec that don't appear in spec's <spec_requirements> block.
      * WRONG_TEST_NO_NEGATIVE_ASSERTION — observation.negative_assertion_present
        is false (only fires on FAIL/ERROR/SKIP — Pitfall B).
      * WRONG_TEST_VALUE_NOT_SHAPE — observation.shape_not_value_check ==
        "failed" (only fires on FAIL/ERROR/SKIP — Pitfall B).
      * WRONG_TEST_SOURCE_LEAK — observation.test_path or .captured_output
        contains anchored substring match against any FORBIDDEN_SOURCE_ROOTS
        entry (Pitfall D).
      * WRONG_TEST_HEADER_MISSING — observation.tests_spec is empty
        (wrong-test stub-pattern token; co-fires with TEST_HEADER_MISSING).
      * TEST_DERIVER_READ_SOURCE — when --tool-call-log provided, any
        Read/Grep/Glob call targeting a FORBIDDEN_SOURCE_ROOTS path that
        is NOT under an ALLOWED_READ_PREFIXES path.

    Pitfall avoidance:

      * Pitfall A (closed-vocab smuggling): top-level + per-observation
        BOTH closed (mirror Phase 6 two-layer KNOWN_REVIEW_KEYS +
        KNOWN_FLAG_KEYS).
      * Pitfall B (wrong-test pattern catches PASS): patterns 7a-d
        (NO_NEGATIVE_ASSERTION / VALUE_NOT_SHAPE / SOURCE_LEAK /
        HEADER_MISSING) only run on observations with status != "PASS".
        PASS observations are informational and are not routed to ASSAY.
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
    # spec-requirement-ID set is available for every dangling-req check.
    spec_requirement_ids: set[str] = set()
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

        # Step 4: status enum.
        status = obs.get("status")
        if status not in KNOWN_OBSERVATION_STATUSES:
            failures.append(
                f"TEST_OBSERVATION_UNKNOWN_STATUS: {obs_id} status="
                f"{status!r}; only "
                f"{sorted(KNOWN_OBSERVATION_STATUSES)!r} allowed"
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

        # Step 7: wrong-test stub patterns — fire regardless of status.
        # The "wrong-test" concept is about tests whose STATUS doesn't
        # faithfully reflect spec compliance: a happy-path PASS without
        # a negative branch is the canonical wrong-test (test passes but
        # the absence is the bug). FAIL/ERROR/SKIP wrong-tests catch the
        # rest of the surface (literal-value asserts that happen to
        # diverge, source-leak imports, missing headers). Gating these
        # checks on status != PASS would silence the most important
        # signal — a passing test that shouldn't have been written that
        # way. (Plan 07-02's Pitfall B prose suggested status-gating but
        # the fixtures shipped in Plan 07-01 show status=PASS for the
        # NO_NEGATIVE_ASSERTION wrong-test, so the fixture contract wins.)
        # Rule 1 deviation from plan prose; documented in 07-02-SUMMARY.md.

        # 7a: negative-assertion mandate.
        if obs.get("negative_assertion_present") is False:
            failures.append(
                f"WRONG_TEST_NO_NEGATIVE_ASSERTION: {obs_id} "
                "negative_assertion_present=false"
            )
        # 7b: shape-not-value rule.
        if obs.get("shape_not_value_check") == "failed":
            failures.append(
                f"WRONG_TEST_VALUE_NOT_SHAPE: {obs_id} "
                "shape_not_value_check=failed"
            )
        # 7c: source-leak detection in test_path + captured_output.
        # Anchored boundary match (Pitfall D): ^src/ or /src/ but NOT
        # library/ or my-src/. We also accept Python-import boundaries
        # so `from src.handlers import x` and `import src.handlers`
        # match via the "src." form (replace trailing slash with dot
        # for import-style references).
        target_text = (
            str(obs.get("test_path", ""))
            + "\n"
            + str(obs.get("captured_output", ""))
        )
        for root in FORBIDDEN_SOURCE_ROOTS:
            # root is e.g. "src/" or "plugins/foundry/agents/".
            # Build TWO patterns per root:
            #   1. literal "src/" with leading boundary (slash,
            #      whitespace, or start-of-line) — catches "from
            #      src/handlers" prose, file-path references like
            #      "src/handlers/login.py", and bare-leading "src/".
            #   2. Python-import dotted form "src." — replace trailing
            #      slash with dot. Catches "from src.handlers" and
            #      "import src.handlers". Boundary: same as (1).
            root_dotted = root.rstrip("/").replace("/", ".") + "."
            slashed = re.escape(root)
            dotted = re.escape(root_dotted)
            pattern = (
                r"(?:^|[\s/(])(?:" + slashed + r"|" + dotted + r")"
            )
            if re.search(pattern, target_text, re.MULTILINE):
                # Skip when the only match is the test_path itself
                # (which lives under foundry-archive/ and is allowed).
                # The captured_output is where source-leak imports show
                # up; test_path under foundry-archive/ never triggers a
                # forbidden-root match by construction (allowed-prefix
                # test_path values don't contain "src/" etc. as anchored
                # tokens). Continue to emit the failure.
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
