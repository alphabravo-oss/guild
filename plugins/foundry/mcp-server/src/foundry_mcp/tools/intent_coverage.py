"""Phase 8 / INTENT-01 — Foundry-Intent-Coverage MCP tool.

In-process wrapper around foundry_mcp.scripts.validate_intent_coverage.
Returns structured result with action='proceed_to_validate' on pass,
action='redecompose' on any zero-coverage answer, action=
'rerun_intent_carrier' when intent-coverage.json is missing or
malformed. Action routing rule (D-015): redecompose is reserved for a
non-empty zero-coverage answer set; when the validator fails for
non-coverage reasons (zero-coverage answer set empty, validator exit
nonzero) the gate returns action: rerun_intent_carrier with the
validator output, because re-decomposition cannot fix a malformed
matrix.

Completeness disclosure (D-014b): every verdict-bearing payload and the
persisted intent_coverage_summary carry a ``completeness_checked``
boolean — True only when a spec was resolvable and the spec→matrix
completeness fold-in actually ran, so a silent skip (no resolvable spec)
is visible instead of being masked by a stamped .f07-intent-clean.

F0.7 gate semantics (per-answer aggregation rule — stated word-for-word
in the validator docstring and agents/intent-carrier.md): An answer_id
is DROPPED (gate-blocking) only when
every casting's cell for it is DROPPED; a PROPAGATED or PARAPHRASED cell
in any casting keeps the gate open for that answer, and per-cell DROPPED
verdicts remain recorded in the matrix without blocking.

Spec→matrix completeness rule (same words as the validator docstring and
agents/intent-carrier.md): A spec
appendix answer_id with no matrix cell at all is zero-coverage: the
validator emits INTENT_COVERAGE_MATRIX_INCOMPLETE naming each missing
answer_id, and the omitted answer blocks the gate exactly like an answer
whose every casting's cell is DROPPED. This tool folds those omitted
answers into ``dropped_answers`` / ``redecompose_hints`` so re-decompose
routing sees them structurally, not only in validator_stdout prose.

Cell-verifiability rule (same words as the validator docstring and
agents/intent-carrier.md — D-017/D-018/D-019): A matrix
cell that cannot be verified cannot contribute coverage: a cell missing
answer_id, casting_id, or verdict, a cell citing a casting_id absent from
castings/manifest.json, and a cell citing a manifest-declared casting
whose prompt file is missing or unreadable all count as DROPPED in the
per-answer aggregation, so an answer whose only non-DROPPED cells are
such cells blocks as zero-coverage. The tool's aggregation applies this
normalization through the validator module's ``aggregate_matrix_coverage``
— the SAME function the validator's blocking decision uses — so the two
layers cannot diverge (D-019): zero-coverage-by-ghost answers land in
``dropped_answers`` / ``redecompose_hints`` and route as ``redecompose``,
while ``rerun_intent_carrier`` stays reserved for genuinely non-coverage
failures (malformed JSON/schema with no zero-coverage answers). The gate
always threads the run's castings dir to the validator via
``--castings-dir`` (D-018b), and every verdict-bearing payload plus the
persisted summary disclose ``verification_checked`` — True only when
castings/manifest.json was loadable and the ghost/unverifiable integrity
checks actually ran (D-018c), so a dark state is visible instead of
silently green.

  - PASS (no zero-coverage answer): stamps .f07-intent-clean marker;
    orchestrator transitions to F0.9 VALIDATE.
  - FAIL (any zero-coverage answer): returns redecompose action +
    dropped_answers list + redecompose_hints; orchestrator routes lead
    BACK to F0.5 DECOMPOSE with the missing A-NNN list as guidance.
    NEVER amends casting prompts in place (REQUIREMENTS.md Out of Scope).

Locked decisions (per 08-RESEARCH.md Open Questions 2 + 3):
  - On pass: stamp .f07-intent-clean marker file in run dir.
  - On fail: structured payload with action / dropped_answers /
    redecompose_hints / hint / validator_stdout / validator_exit.
"""
from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path

from foundry_mcp.tools.foundry_orchestrator import _resolve_spec_path
from foundry_mcp.tools.foundry_state import get_run_dir


def _save_json_atomic(path: Path, data: dict) -> None:
    """Atomic JSON write — write to .tmp then rename.

    Mirrors foundry.py:_save_json discipline (write to sibling .tmp then
    os-level rename) so a concurrent reader never observes a truncated
    manifest.json.
    """
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    tmp.rename(path)


def _run_validator_in_process(
    coverage_path: Path,
    spec_path: Path | None,
    castings_dir: Path | None,
) -> tuple[int, str, str] | None:
    """Call the canonical validator's main() in-process.

    Imports foundry_mcp.scripts.validate_intent_coverage.main and invokes
    it with an explicit argv list, capturing stdout/stderr. The subprocess
    is removed entirely (FR-001): no ``"python"``-vs-``sys.executable``
    mismatch and no dependency on plugins/foundry/scripts/ being shipped in
    the wheel (GI-002 / FR-007).

    Returns (exit_code, stdout, stderr) on a clean run, or ``None`` when the
    validator is missing or errors — the caller maps ``None`` to an explicit
    tooling error (CT-002), never a fake redecompose.

    main(argv) slices argv[1:] and returns an int; it only calls sys.exit
    inside its ``__main__`` guard, so the only SystemExit reachable here is
    argparse's own usage-error path, which we treat as a tooling error.
    """
    try:
        from foundry_mcp.scripts.validate_intent_coverage import main
    except Exception:
        return None

    argv: list[str] = ["validate-intent-coverage", str(coverage_path)]
    if spec_path is not None and spec_path.exists():
        argv += ["--spec", str(spec_path)]
    # D-018b: always thread the run's castings dir explicitly so the
    # ghost-casting / unverifiable-casting / re-derivation checks run
    # regardless of where the spec resolved (the spec-adjacent derivation
    # went dark when the spec lived outside the run dir). The validator
    # treats a nonexistent directory as "checks cannot run" — disclosed
    # via verification_checked below.
    if castings_dir is not None:
        argv += ["--castings-dir", str(castings_dir)]
    # tool-call-log is advisory — passed only when the orchestrator has
    # captured an agent tool-call log for this run (08-RESEARCH.md Open
    # Question 5; advisory shape locked per Phase 7 precedent).

    out_buf = io.StringIO()
    err_buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(out_buf), contextlib.redirect_stderr(
            err_buf
        ):
            exit_code = main(argv)
    except SystemExit as exc:  # argparse usage error — non-verdict exit.
        code = exc.code if isinstance(exc.code, int) else 2
        return None if code not in (0, 1) else (
            code,
            out_buf.getvalue(),
            err_buf.getvalue(),
        )
    except Exception:  # noqa: BLE001 — any validator crash is a tooling error.
        return None

    if not isinstance(exit_code, int) or exit_code not in (0, 1):
        # A non-int / non-verdict return is not a legitimate PASS/FAIL —
        # surface as a tooling error rather than mis-routing to redecompose.
        return None
    return exit_code, out_buf.getvalue(), err_buf.getvalue()


def foundry_intent_coverage(project_root: str = ".") -> dict:
    """F0.7 INTENT-CARRIER gate — validate intent-coverage.json.

    Returns one of:
      {passed: True, action: 'proceed_to_validate', propagated_count: int,
       paraphrased_answers: [...], dropped_answers: [],
       completeness_checked: bool, verification_checked: bool,
       matrix_path: str}
      OR
      {passed: False, action: 'redecompose', dropped_answers: [...],
       redecompose_hints: [{answer_id, suggested_casting, citation_chain}],
       cell_verdict_counts: {PROPAGATED, PARAPHRASED, DROPPED: int},
       completeness_checked: bool, verification_checked: bool,
       matrix_path: str, hint: str,
       validator_stdout: str, validator_exit: int}
      OR
      {passed: False, action: 'rerun_intent_carrier', reason: str}
        — when intent-coverage.json is missing or malformed, AND (D-015)
        when the validator fails for non-coverage reasons (zero-coverage
        answer set empty, validator exit nonzero); in the latter case the
        payload also carries cell_verdict_counts / completeness_checked /
        verification_checked / matrix_path / validator output.
    """
    fdir = get_run_dir(project_root)
    if not fdir:
        return {"passed": False, "reason": "No active foundry run"}

    coverage_path = fdir / "intent-coverage.json"
    # GRIND D-012: resolve the spec via the canonical resolver (run-dir
    # spec.md first, falling back to state.json['spec_path']) instead of
    # hardcoding <run_dir>/spec.md. foundry.py copies the spec into the
    # run dir only when the source exists, so a run whose spec lives
    # OUTSIDE the run dir is a reachable production state — the hardcoded
    # join silently dropped --spec and skipped the fold-in there,
    # reopening the D-009 omission bypass. Read-only import of the
    # single resolver from foundry_orchestrator (no duplicated fallback
    # logic); None only when no spec is resolvable at all.
    spec_path = _resolve_spec_path(project_root)
    # D-018b: the gate knows the run dir; thread its castings dir to the
    # validator explicitly instead of relying on the spec-adjacent
    # derivation (which goes dark when the spec resolves via the
    # state.json fallback).
    castings_dir = fdir / "castings"
    if not coverage_path.exists():
        return {
            "passed": False,
            "action": "rerun_intent_carrier",
            "reason": (
                "intent-coverage.json missing — run intent-carrier agent first"
            ),
        }

    # FR-001 / CT-002: run the validator IN-PROCESS. A missing or erroring
    # validator returns None here and is surfaced as an explicit tooling
    # error below — NEVER a fake action=redecompose.
    validator_result = _run_validator_in_process(
        coverage_path, spec_path, castings_dir,
    )
    if validator_result is None:
        return {
            "passed": False,
            "action": "tooling_error",
            "reason": (
                "intent-coverage validator missing or errored — "
                "foundry_mcp.scripts.validate_intent_coverage.main() could "
                "not be imported or did not return a valid exit code. This "
                "is a tooling failure, NOT a spec-coverage problem; do not "
                "re-decompose."
            ),
            "validator_exit": None,
            "validator_stdout": "",
            "validator_stderr": "",
        }
    validator_exit, validator_stdout, validator_stderr = validator_result

    try:
        coverage = json.loads(coverage_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {
            "passed": False,
            "action": "rerun_intent_carrier",
            "reason": "intent-coverage.json malformed — agent must re-emit",
            "validator_stdout": validator_stdout,
            "validator_stderr": validator_stderr,
            "validator_exit": validator_exit,
        }

    matrix = coverage.get("matrix", [])
    # Per-answer gate aggregation — the same rule, in the same words, as
    # the validator docstring and agents/intent-carrier.md: an answer_id
    # blocks the gate only when every casting's cell for it is DROPPED.
    # D-019: computed through the validator module's
    # aggregate_matrix_coverage — the SAME function the validator's
    # blocking decision uses — so the cannot-contribute normalization
    # (missing required keys, ghost casting, unverifiable casting) can
    # never diverge between the two layers. The import cannot realistically
    # fail here (_run_validator_in_process just imported the module), but
    # a failure is still surfaced as a tooling error, never a fake verdict.
    try:
        from foundry_mcp.scripts.validate_intent_coverage import (
            aggregate_matrix_coverage,
        )

        cell_verdicts_by_answer, verification_checked = (
            aggregate_matrix_coverage(matrix, castings_dir=castings_dir)
        )
    except Exception:  # noqa: BLE001 — mirror the CT-002 tooling-error stance.
        return {
            "passed": False,
            "action": "tooling_error",
            "reason": (
                "intent-coverage validator module lost between the "
                "validator run and the aggregation fold — "
                "aggregate_matrix_coverage could not be imported or "
                "raised. This is a tooling failure, NOT a spec-coverage "
                "problem; do not re-decompose."
            ),
            "validator_exit": validator_exit,
            "validator_stdout": validator_stdout,
            "validator_stderr": validator_stderr,
        }
    # Spec→matrix completeness (D-009 — omission IS zero coverage): a spec
    # appendix answer with NO matrix cell at all must block exactly like
    # an all-DROPPED answer. The answer-set extraction is imported from
    # the canonical validator module (no second parser), guarded the same
    # way _run_validator_in_process guards its main() import.
    missing_from_matrix: list[str] = []
    # D-014b: completeness_checked discloses whether the spec→matrix
    # completeness fold-in actually ran. False when no spec is resolvable
    # (or the answer-set extraction failed) — the skip is then visible in
    # every verdict-bearing payload and the persisted summary instead of
    # being masked by a stamped .f07-intent-clean.
    completeness_checked = False
    if spec_path is not None:
        try:
            from foundry_mcp.scripts.validate_intent_coverage import (
                extract_answer_ids_from_spec,
            )

            spec_answer_ids = extract_answer_ids_from_spec(
                spec_path.read_text(encoding="utf-8")
            )
            completeness_checked = True
        except Exception:  # noqa: BLE001 — a broken validator module or
            # unreadable spec is already surfaced via the validator run
            # above; the fold-in stays best-effort here.
            spec_answer_ids = set()
        missing_from_matrix = sorted(
            spec_answer_ids - set(cell_verdicts_by_answer)
        )
    dropped = sorted(
        set(missing_from_matrix)
        | {
            ans
            for ans, cell_verdicts in cell_verdicts_by_answer.items()
            if all(v == "DROPPED" for v in cell_verdicts)
        }
    )
    paraphrased = sorted(
        {c["answer_id"] for c in matrix if c.get("verdict") == "PARAPHRASED"}
    )
    propagated_count = sum(
        1 for c in matrix if c.get("verdict") == "PROPAGATED"
    )
    # GRIND D-006 / AC-002: per-cell verdict counts — the reported summary
    # must carry the full per-cell picture, including DROPPED cells that
    # do not block the gate because their answer is covered elsewhere.
    # Per-cell derivation stays per-cell; gate semantics are untouched.
    cell_verdict_counts = {
        verdict: sum(1 for c in matrix if c.get("verdict") == verdict)
        for verdict in ("PROPAGATED", "PARAPHRASED", "DROPPED")
    }

    if validator_exit == 0 and not dropped:
        # Locked decision (Open Question 2): stamp marker file on pass.
        # Orchestrator's F0.9 sub-check 7m reads this marker to confirm
        # F0.7 actually ran (anti-skip discipline).
        (fdir / ".f07-intent-clean").write_text("ok\n", encoding="utf-8")

        # FR-009 / Bug2: append manifest.intent_coverage_summary to
        # castings/manifest.json atomically, alongside the .f07-intent-clean
        # stamp, so F0.9 sub-check 7m (foundry_validate.py:570-581) sees the
        # key present. The summary reuses the already-computed locals; 7m
        # only requires the key to exist as a non-null object.
        summary = {
            "stream": "INTENT-01",
            "phase": "F0.7",
            "passed": True,
            "propagated_count": propagated_count,
            "paraphrased_count": len(paraphrased),
            "paraphrased_answers": paraphrased,
            "cell_verdict_counts": cell_verdict_counts,
            "dropped_answers": [],
            "completeness_checked": completeness_checked,
            "verification_checked": verification_checked,
            "matrix_path": str(coverage_path),
        }
        manifest_path = fdir / "castings" / "manifest.json"
        if manifest_path.exists():
            try:
                manifest = json.loads(
                    manifest_path.read_text(encoding="utf-8")
                )
            except json.JSONDecodeError:
                manifest = {}
            if not isinstance(manifest, dict):
                manifest = {}
            manifest["intent_coverage_summary"] = summary
            _save_json_atomic(manifest_path, manifest)

        return {
            "passed": True,
            "action": "proceed_to_validate",
            "propagated_count": propagated_count,
            "paraphrased_answers": paraphrased,
            "cell_verdict_counts": cell_verdict_counts,
            "dropped_answers": [],
            "completeness_checked": completeness_checked,
            "verification_checked": verification_checked,
            "matrix_path": str(coverage_path),
            "intent_coverage_summary": summary,
        }

    # D-015: redecompose is reserved for a non-empty zero-coverage answer
    # set; when the validator fails for non-coverage reasons (zero-coverage
    # answer set empty, validator exit nonzero) the gate returns action:
    # rerun_intent_carrier with the validator output, because
    # re-decomposition cannot fix a malformed matrix (schema violation,
    # verdict mismatch, embedding-audit hit, or a ghost/unverifiable cell
    # whose answer has real coverage elsewhere). D-019: because the
    # aggregation above normalizes cannot-contribute cells to DROPPED, an
    # answer whose only coverage was a ghost or unverifiable cell lands in
    # the dropped set and routes as redecompose below, with its answer_id
    # named in dropped_answers / redecompose_hints.
    if not dropped:
        return {
            "passed": False,
            "action": "rerun_intent_carrier",
            "reason": (
                "validator failed for non-coverage reasons (zero-coverage "
                "answer set empty, validator exit nonzero) — the matrix "
                "itself is invalid; re-run the intent-carrier agent "
                "against the real spec + castings. Re-decomposition "
                "cannot fix a malformed matrix."
            ),
            "cell_verdict_counts": cell_verdict_counts,
            "completeness_checked": completeness_checked,
            "verification_checked": verification_checked,
            "matrix_path": str(coverage_path),
            "validator_stdout": validator_stdout,
            "validator_stderr": validator_stderr,
            "validator_exit": validator_exit,
        }

    # Build redecompose_hints: per dropped answer_id, name the first
    # casting_id with a DROPPED cell + the citation_chain from the matrix.
    # Heuristic only — author can refine; the structural guarantee is
    # that every zero-coverage answer_id surfaces as a hint entry. Answers
    # omitted from the matrix entirely (D-009) have no cell to draw from:
    # they surface with suggested_casting=None and a singleton
    # citation_chain of their own answer_id.
    redecompose_hints = []
    for ans in dropped:
        first_drop_cell = next(
            (
                c
                for c in matrix
                if c.get("verdict") == "DROPPED"
                and c.get("answer_id") == ans
            ),
            None,
        )
        redecompose_hints.append(
            {
                "answer_id": ans,
                "suggested_casting": (
                    first_drop_cell.get("casting_id")
                    if first_drop_cell
                    else None
                ),
                "citation_chain": (
                    first_drop_cell.get("citation_chain", [ans])
                    if first_drop_cell
                    else [ans]
                ),
            }
        )

    return {
        "passed": False,
        "action": "redecompose",
        "dropped_answers": dropped,
        "redecompose_hints": redecompose_hints,
        # D-010 / AC-002: the lead receiving redecompose needs the per-cell
        # picture and the matrix pointer just as much as the pass path does.
        "cell_verdict_counts": cell_verdict_counts,
        "completeness_checked": completeness_checked,
        "verification_checked": verification_checked,
        "matrix_path": str(coverage_path),
        "validator_stdout": validator_stdout,
        "validator_stderr": validator_stderr,
        "validator_exit": validator_exit,
        "hint": (
            "F0.5 DECOMPOSE must re-run with these A-NNN entries as "
            "additional citation anchors. Do NOT amend casting prompts "
            "in place — re-run F0.5 from spec.md."
        ),
    }
