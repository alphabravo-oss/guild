"""validate_report tool — JSON schema validation for foundry verification reports."""

from __future__ import annotations

import json
from pathlib import Path

import jsonschema

from foundry_mcp.parsers.report import extract_last_json
from foundry_mcp.schemas.findings import SCHEMAS


def validate_report(
    report_path: str,
    schema_name: str = "trace",
    schema_path: str | None = None,
    auto_fix: bool = False,
    project_root: str = ".",
) -> dict:
    """Validate a report's JSON block against a schema.

    Args:
        report_path: Path to markdown report file.
        schema_name: Built-in schema name ("trace", "prove", "temper") or "custom" with schema_path.
        schema_path: Custom schema file path (overrides schema_name).
        auto_fix: Attempt to fix common issues (type coercion, missing fields).
        project_root: Project root for resolving relative paths.

    Returns:
        {valid, errors[], stats{}, fixed_json?}
    """
    root = Path(project_root)
    rpath = root / report_path if not Path(report_path).is_absolute() else Path(report_path)

    if not rpath.exists():
        return {"valid": False, "errors": [f"File not found: {report_path}"], "stats": {}}

    text = rpath.read_text(encoding="utf-8")
    block = extract_last_json(text)

    if block is None:
        return {"valid": False, "errors": ["No JSON block found in report"], "stats": {}}

    # Load schema
    if schema_path:
        spath = root / schema_path if not Path(schema_path).is_absolute() else Path(schema_path)
        schema = json.loads(spath.read_text(encoding="utf-8"))
    elif schema_name in SCHEMAS:
        schema = SCHEMAS[schema_name]
    else:
        return {
            "valid": False,
            "errors": [f"Unknown schema: {schema_name}. Available: {list(SCHEMAS.keys())}"],
            "stats": {},
        }

    data = block.data
    errors: list[str] = []

    # Auto-fix pass
    fixed_json = None
    if auto_fix and isinstance(data, dict):
        data, fix_notes = _auto_fix(data, schema_name)
        if fix_notes:
            fixed_json = data
            errors.extend(f"[auto-fixed] {n}" for n in fix_notes)

    # Validate
    validator = jsonschema.Draft202012Validator(schema)
    validation_errors = sorted(validator.iter_errors(data), key=lambda e: list(e.path))
    for err in validation_errors:
        path = ".".join(str(p) for p in err.absolute_path)
        errors.append(f"{path}: {err.message}" if path else err.message)

    # Stats
    stats = _compute_stats(data, schema_name)

    result: dict = {
        "valid": len(validation_errors) == 0,
        "errors": errors,
        "stats": stats,
        "json_block_lines": [block.start_line, block.end_line],
    }
    if fixed_json is not None:
        result["fixed_json"] = fixed_json
    return result


def _auto_fix(data: dict, schema_name: str) -> tuple[dict, list[str]]:
    """Attempt common fixes. Returns (fixed_data, notes)."""
    notes: list[str] = []

    if schema_name == "trace" and "findings" in data:
        for f in data["findings"]:
            # Infer severity from category if missing
            if "severity" not in f and "category" in f:
                cat = f["category"]
                if cat in ("missing-wiring", "stub-implementation", "incomplete-flow"):
                    f["severity"] = "critical"
                elif cat in ("spec-drift", "data-inconsistency", "error-handling-gap"):
                    f["severity"] = "major"
                else:
                    f["severity"] = "minor"
                notes.append(f"Inferred severity '{f['severity']}' for {f.get('id', '?')} from category '{cat}'")

            # Ensure line is int or null
            if "line" in f and isinstance(f["line"], str):
                try:
                    f["line"] = int(f["line"])
                    notes.append(f"Coerced line to int for {f.get('id', '?')}")
                except ValueError:
                    f["line"] = None

    if schema_name == "prove" and "verdicts" in data:
        for v in data["verdicts"]:
            # Normalize verdict casing
            if "verdict" in v and isinstance(v["verdict"], str):
                normalized = v["verdict"].strip().upper().replace(" ", "-")
                if normalized != v["verdict"]:
                    notes.append(f"Normalized verdict '{v['verdict']}' → '{normalized}' for {v.get('id', '?')}")
                    v["verdict"] = normalized

    return data, notes


def _compute_stats(data: dict | list, schema_name: str) -> dict:
    """Compute summary statistics from validated data."""
    if not isinstance(data, dict):
        return {}

    if schema_name == "trace":
        findings = data.get("findings", [])
        return {
            "total_findings": len(findings),
            "by_severity": _count_by(findings, "severity"),
            "by_category": _count_by(findings, "category"),
        }
    elif schema_name == "prove":
        verdicts = data.get("verdicts", [])
        by_v = _count_by(verdicts, "verdict")
        verified = by_v.get("VERIFIED", 0)
        total = len(verdicts)
        return {
            "total_verdicts": total,
            "verified": verified,
            "non_verified": total - verified,
            "by_verdict": by_v,
            "pass_rate": f"{verified / total * 100:.0f}%" if total > 0 else "N/A",
        }
    elif schema_name == "temper":
        domains = data.get("domains", [])
        return {
            "total_domains": len(domains),
            "by_status": _count_by(domains, "status"),
        }
    return {}


def _count_by(items: list[dict], key: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
        val = item.get(key, "unknown")
        counts[val] = counts.get(val, 0) + 1
    return counts
