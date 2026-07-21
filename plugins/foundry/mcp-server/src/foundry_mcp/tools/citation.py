"""verify_citations tool — cross-reference spec requirements with PROVE verdicts."""

from __future__ import annotations

from pathlib import Path

from foundry_mcp.parsers.prove import Verdict, parse_prove_report
from foundry_mcp.parsers.spec import extract_requirements


def verify_citations(
    spec_path: str,
    report_path: str,
    strict: bool = False,
    project_root: str = ".",
) -> dict:
    """Cross-reference a spec with a critic report for traceability.

    Args:
        spec_path: Path to LISA spec file.
        report_path: Path to critic report file.
        strict: If True, fail on any uncovered requirement.
        project_root: Project root for resolving relative paths.

    Returns:
        {traceability_matrix[], summary{}, pass}
    """
    root = Path(project_root)

    # Load spec
    spath = root / spec_path if not Path(spec_path).is_absolute() else Path(spec_path)
    if not spath.exists():
        return {"pass": False, "error": f"Spec not found: {spec_path}", "traceability_matrix": [], "summary": {}}
    spec_text = spath.read_text(encoding="utf-8")

    # Load critic report
    rpath = root / report_path if not Path(report_path).is_absolute() else Path(report_path)
    if not rpath.exists():
        return {"pass": False, "error": f"Report not found: {report_path}", "traceability_matrix": [], "summary": {}}
    report_text = rpath.read_text(encoding="utf-8")

    # Parse both
    requirements = extract_requirements(spec_text)
    req_ids = set(requirements.keys())
    verdicts = parse_prove_report(report_text)
    verdict_ids = {v.id for v in verdicts}

    # Build traceability matrix
    matrix: list[dict] = []
    issues: list[str] = []

    # Check every spec requirement has a verdict
    uncovered_reqs: list[str] = []
    for req_id, req in sorted(requirements.items()):
        # Map requirement to VC — simple heuristic: check if any verdict mentions this req
        matching_verdicts = [v for v in verdicts if req_id in v.reasoning or req_id in v.description]
        if matching_verdicts:
            for v in matching_verdicts:
                matrix.append({
                    "requirement_id": req_id,
                    "requirement_text": req.text[:200],
                    "verdict_id": v.id,
                    "verdict": v.verdict.value,
                    "has_code_ref": len(v.code_refs) > 0,
                    "has_spec_cite": len(v.cited_spec_text) > 0,
                    "status": "covered",
                })
        else:
            uncovered_reqs.append(req_id)
            matrix.append({
                "requirement_id": req_id,
                "requirement_text": req.text[:200],
                "verdict_id": None,
                "verdict": None,
                "has_code_ref": False,
                "has_spec_cite": False,
                "status": "uncovered",
            })

    # Check every verdict for completeness
    uncited_verdicts: list[str] = []
    orphan_verdicts: list[str] = []
    for v in verdicts:
        if not v.cited_spec_text and v.verdict != Verdict.VERIFIED:
            uncited_verdicts.append(v.id)
            issues.append(f"{v.id}: non-VERIFIED verdict without spec citation")

        # Check if verdict references any known requirement
        refs_req = any(rid in v.reasoning or rid in v.description for rid in req_ids)
        if not refs_req:
            orphan_verdicts.append(v.id)

    if uncovered_reqs:
        issues.append(f"Requirements without verdicts: {', '.join(uncovered_reqs)}")
    if orphan_verdicts:
        issues.append(f"Verdicts not linked to requirements: {', '.join(orphan_verdicts)}")

    # Summary
    total_reqs = len(requirements)
    covered = total_reqs - len(uncovered_reqs)
    total_verdicts = len(verdicts)
    verified_count = sum(1 for v in verdicts if v.verdict == Verdict.VERIFIED)

    summary = {
        "total_requirements": total_reqs,
        "covered_requirements": covered,
        "uncovered_requirements": len(uncovered_reqs),
        "coverage_pct": f"{covered / total_reqs * 100:.0f}%" if total_reqs > 0 else "N/A",
        "total_verdicts": total_verdicts,
        "verified_verdicts": verified_count,
        "non_verified_verdicts": total_verdicts - verified_count,
        "uncited_verdicts": len(uncited_verdicts),
        "orphan_verdicts": len(orphan_verdicts),
        "issues": issues,
    }

    # Pass/fail
    passed = True
    if strict and uncovered_reqs:
        passed = False
    if uncited_verdicts:
        passed = False

    return {
        "pass": passed,
        "traceability_matrix": matrix,
        "summary": summary,
    }
