#!/usr/bin/env python3
"""Phase 9 / RUN-01 — measure-run.py.

Two-mode CLI: per-run JSON extractor + cohort --matrix aggregator. Mirrors
Phase 4-8 closed-vocabulary discipline (stdlib only; no runtime deps).
Three frozensets locked at module top: KNOWN_PHASE9_STREAM_IDS (15),
KNOWN_PHASE9_FAILURE_TOKENS (8), KNOWN_PHASE9_COHORT_IDS (10). Wall-clock =
first/last handoffs.jsonl timestamp delta (Pitfall 5 / 09-RESEARCH.md).
Exit 0 OK; 1 on token rejection / gate FAIL; 2 on usage error.
"""
from __future__ import annotations
import argparse, csv, io, json, sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any


KNOWN_PHASE9_STREAM_IDS = frozenset({
    "TRACE", "FLOW_TRACE", "PROVE", "RESEARCH_AUDIT", "COVERAGE_DIFF",
    "TEST-01", "SIGHT", "TEST",
    "EVID-01", "EVID-02",
    "INTV-01", "TYPE-01", "TYPE-02",
    "PROBE-01", "INTENT-01",
})  # 15 streams

KNOWN_PHASE9_FAILURE_TOKENS = frozenset({
    "PHASE9_UNKNOWN_STREAM", "PHASE9_UNKNOWN_COHORT", "PHASE9_RUN_DIR_INVALID",
    "PHASE9_CONTEXT_FILE_MISSING", "PHASE9_WALL_CLOCK_UNAVAILABLE",
    "PHASE9_CYCLE_COUNT_INVALID", "PHASE9_SCHEMA_INVALID",
    "PHASE9_DEFECTS_FILE_MALFORMED",
})  # 8 tokens

KNOWN_PHASE9_COHORT_IDS = frozenset({
    "v4_2_0_baseline", "all_enabled_baseline",
    "no_INTV_01", "no_TYPE_01", "no_TYPE_02",
    "no_EVID_01", "no_EVID_02",
    "no_PROBE_01", "no_TEST_01", "no_INTENT_01",
})  # 10 cohorts

# RUN-01 quantitative gate thresholds (locked per CONTEXT.md).
MAX_CYCLES_FOR_CONVERGENCE = 8
DEFECT_YIELD_PCT_MIN = 5.0
DEFECT_YIELD_PCT_MAX = 50.0
MAX_F2_CONTEXT_PCT = 50.0
MAX_WALL_CLOCK_REGRESSION_PCT = 50.0

# Saturation thresholds (dual-criterion per 09-RESEARCH.md).
SATURATION_THRESHOLD_DEFECT_YIELD_PCT = 10.0
SATURATION_THRESHOLD_DEFECT_COUNT_FLOOR = 1

CSV_COLUMNS = (
    "cohort_id", "disable_lever", "cycles", "per_stream_defects_json",
    "f2_context_pct", "wall_clock_seconds", "wall_clock_regression_pct",
    "gate_verdict_overall", "failure_tokens_csv",
)


@dataclass
class MeasureResult:
    cohort_id: str = ""
    cycles: int = 0
    per_stream_defects: dict[str, int] = field(default_factory=dict)
    f2_context_pct: float | None = None
    wall_clock_seconds: float = 0.0
    gate_verdicts: dict[str, str] = field(default_factory=dict)
    failure_tokens: list[str] = field(default_factory=list)
    disable_lever: str = ""
    wall_clock_regression_pct: float | None = None

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "cohort_id": self.cohort_id, "cycles": self.cycles,
            "per_stream_defects": self.per_stream_defects,
            "f2_context_pct": self.f2_context_pct,
            "wall_clock_seconds": self.wall_clock_seconds,
            "gate_verdicts": self.gate_verdicts,
            "failure_tokens": self.failure_tokens,
        }


def _parse_iso8601(stamp: Any) -> datetime | None:
    if not isinstance(stamp, str):
        return None
    s = stamp.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return None


def _load_json(path: Path) -> Any:
    """Read+parse a JSON file; return None on missing/malformed."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, FileNotFoundError):
        return None


def _read_cohort_json(run_dir: Path) -> tuple[str | None, str, list[str]]:
    """Return (cohort_id, disable_lever, failure_tokens)."""
    data = _load_json(run_dir / "cohort.json")
    if not isinstance(data, dict):
        return None, "", ["PHASE9_SCHEMA_INVALID"]
    cohort_id = data.get("cohort_id")
    lever = data.get("disable_lever_mechanism", "")
    lever = lever if isinstance(lever, str) else ""
    if not isinstance(cohort_id, str):
        return None, lever, ["PHASE9_SCHEMA_INVALID"]
    if cohort_id not in KNOWN_PHASE9_COHORT_IDS:
        return cohort_id, lever, ["PHASE9_UNKNOWN_COHORT"]
    return cohort_id, lever, []


def _read_handoffs_wall_clock(run_dir: Path) -> tuple[float, list[str]]:
    UNAVAIL = ["PHASE9_WALL_CLOCK_UNAVAILABLE"]
    path = run_dir / "handoffs.jsonl"
    if not path.exists():
        return 0.0, UNAVAIL
    try:
        lines = [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    except OSError:
        return 0.0, UNAVAIL
    if not lines:
        return 0.0, UNAVAIL
    first_ts = last_ts = None
    for ln in lines:
        try:
            entry = json.loads(ln)
        except json.JSONDecodeError:
            return 0.0, UNAVAIL
        ts = _parse_iso8601(entry.get("timestamp")) if isinstance(entry, dict) else None
        if ts is None:
            continue
        if first_ts is None:
            first_ts = ts
        last_ts = ts
    if first_ts is None or last_ts is None:
        return 0.0, UNAVAIL
    seconds = (last_ts - first_ts).total_seconds()
    return (0.0, UNAVAIL) if seconds < 0 else (float(seconds), [])


def _read_state_cycle_count(run_dir: Path) -> tuple[int, list[str]]:
    INV = ["PHASE9_CYCLE_COUNT_INVALID"]
    data = _load_json(run_dir / "state.json")
    if not isinstance(data, dict):
        return 0, INV
    cycle = data.get("cycle")
    if isinstance(cycle, bool) or not isinstance(cycle, int) or cycle < 0:
        return 0, INV
    return cycle, []


def _read_defects_per_stream(run_dir: Path) -> tuple[dict[str, int], list[str]]:
    path = run_dir / "defects.json"
    if not path.exists():
        return {}, []
    data = _load_json(path)
    if not isinstance(data, dict) or not isinstance(data.get("defects"), list):
        return {}, ["PHASE9_DEFECTS_FILE_MALFORMED"]
    counts: dict[str, int] = {}
    fts: list[str] = []
    for d in data["defects"]:
        if not isinstance(d, dict):
            fts.append("PHASE9_DEFECTS_FILE_MALFORMED"); continue
        stream = d.get("stream")
        if not isinstance(stream, str):
            fts.append("PHASE9_DEFECTS_FILE_MALFORMED"); continue
        if stream not in KNOWN_PHASE9_STREAM_IDS:
            fts.append(f"PHASE9_UNKNOWN_STREAM:{stream}"); continue
        counts[stream] = counts.get(stream, 0) + 1
    return counts, fts


def _read_context_pct(run_dir: Path, strict: bool) -> tuple[float | None, list[str]]:
    miss = ["PHASE9_CONTEXT_FILE_MISSING"] if strict else []
    path = run_dir / "context-at-f2.txt"
    if not path.exists():
        return None, miss
    try:
        text = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None, miss
    if not text:
        return None, miss
    try:
        return float(text), []
    except ValueError:
        return None, miss


def _yield_band_verdict(per_stream_defects: dict[str, int]) -> str:
    total = sum(per_stream_defects.values())
    if total == 0:
        return "PASS"
    for count in per_stream_defects.values():
        pct = (count / total) * 100.0
        if pct < DEFECT_YIELD_PCT_MIN or pct > DEFECT_YIELD_PCT_MAX:
            return "FAIL"
    return "PASS"


def _compute_gate_verdicts(
    cycles: int, per_stream_defects: dict[str, int],
    f2_context_pct: float | None, wall_clock_regression_pct: float | None,
) -> dict[str, str]:
    return {
        "cycles": "PASS" if cycles <= MAX_CYCLES_FOR_CONVERGENCE else "FAIL",
        "defect_yield_per_stream": _yield_band_verdict(per_stream_defects),
        "f2_context_pct": (
            "MISSING" if f2_context_pct is None
            else "PASS" if f2_context_pct < MAX_F2_CONTEXT_PCT else "FAIL"
        ),
        "wall_clock_regression_pct": (
            "MISSING" if wall_clock_regression_pct is None
            else "PASS" if wall_clock_regression_pct < MAX_WALL_CLOCK_REGRESSION_PCT
            else "FAIL"
        ),
    }


def _overall_verdict(verdicts: dict[str, str]) -> str:
    if any(v == "FAIL" for v in verdicts.values()):
        return "FAIL"
    if any(v == "MISSING" for v in verdicts.values()):
        return "MISSING"
    return "PASS"


def _is_saturated(
    baseline_count: int, cohort_count: int,
    baseline_yield_pct: float, cohort_yield_pct: float,
) -> bool:
    """Dual-criterion saturation: count-floor for ≤5 baseline; relative
    yield drop % for >5 baseline (per 09-RESEARCH.md §Saturation Threshold
    Numeric — drop measured as ((baseline - cohort) / baseline) * 100)."""
    if baseline_count <= 5:
        return abs(cohort_count - baseline_count) <= SATURATION_THRESHOLD_DEFECT_COUNT_FLOOR
    if baseline_yield_pct == 0:
        return cohort_yield_pct == 0
    rel_drop_pct = abs(baseline_yield_pct - cohort_yield_pct) / baseline_yield_pct * 100.0
    return rel_drop_pct <= SATURATION_THRESHOLD_DEFECT_YIELD_PCT


def _extract_per_run(run_dir: Path, strict: bool, baseline_wall_clock: float | None = None) -> MeasureResult:
    r = MeasureResult()
    failure_tokens: list[str] = []
    if not run_dir.exists() or not run_dir.is_dir():
        r.failure_tokens = ["PHASE9_RUN_DIR_INVALID"]; return r
    cohort_id, lever, cf = _read_cohort_json(run_dir)
    r.cohort_id, r.disable_lever = cohort_id or "", lever; failure_tokens.extend(cf)
    wall_clock, wf = _read_handoffs_wall_clock(run_dir)
    r.wall_clock_seconds = wall_clock; failure_tokens.extend(wf)
    cycles, cycf = _read_state_cycle_count(run_dir)
    r.cycles = cycles; failure_tokens.extend(cycf)
    per_stream, df = _read_defects_per_stream(run_dir)
    r.per_stream_defects = per_stream; failure_tokens.extend(df)
    context_pct, ctxf = _read_context_pct(run_dir, strict)
    r.f2_context_pct = context_pct; failure_tokens.extend(ctxf)
    if baseline_wall_clock is not None and baseline_wall_clock > 0:
        r.wall_clock_regression_pct = (wall_clock / baseline_wall_clock - 1.0) * 100.0
    elif baseline_wall_clock == 0:
        r.wall_clock_regression_pct = 0.0
    r.gate_verdicts = _compute_gate_verdicts(
        cycles=cycles, per_stream_defects=per_stream,
        f2_context_pct=context_pct,
        wall_clock_regression_pct=r.wall_clock_regression_pct,
    )
    r.failure_tokens = failure_tokens
    return r


def _emit_per_run(run_dir: Path, strict: bool) -> int:
    result = _extract_per_run(run_dir, strict=strict)
    sys.stdout.write(json.dumps(result.to_json_dict(), indent=2) + "\n")
    return 1 if result.failure_tokens else 0


def _matrix_row(result: MeasureResult) -> list[str]:
    overall = _overall_verdict(result.gate_verdicts)
    return [
        result.cohort_id, result.disable_lever, str(result.cycles),
        json.dumps(result.per_stream_defects, sort_keys=True),
        "" if result.f2_context_pct is None else f"{result.f2_context_pct:.2f}",
        f"{result.wall_clock_seconds:.2f}",
        "" if result.wall_clock_regression_pct is None else f"{result.wall_clock_regression_pct:.2f}",
        overall, ";".join(result.failure_tokens),
    ]


def _emit_matrix(runs_dir: Path, strict: bool, fmt: str) -> int:
    if not runs_dir.exists() or not runs_dir.is_dir():
        sys.stderr.write(f"PHASE9_RUN_DIR_INVALID: {runs_dir} is not a directory\n"); return 1
    cohorts = sorted(s.name for s in runs_dir.iterdir() if s.is_dir() and s.name in KNOWN_PHASE9_COHORT_IDS)
    pre = {c: _extract_per_run(runs_dir / c, strict=strict) for c in cohorts}
    baseline = pre.get("v4_2_0_baseline")
    bsec = baseline.wall_clock_seconds if baseline else None
    results: list[MeasureResult] = []
    for c in cohorts:
        sub = runs_dir / c
        if c == "v4_2_0_baseline":
            results.append(_extract_per_run(sub, strict=strict, baseline_wall_clock=0.0))
        elif bsec is not None and bsec > 0:
            results.append(_extract_per_run(sub, strict=strict, baseline_wall_clock=bsec))
        else:
            results.append(_extract_per_run(sub, strict=strict))
    rendered = [_matrix_row(r) for r in results]
    out = io.StringIO()
    if fmt in ("csv", "both"):
        w = csv.writer(out); w.writerow(list(CSV_COLUMNS))
        for row in rendered:
            w.writerow(row)
    if fmt == "both":
        out.write("\n")
    if fmt in ("markdown", "both"):
        numeric = {"cycles", "f2_context_pct", "wall_clock_seconds", "wall_clock_regression_pct"}
        out.write("| " + " | ".join(CSV_COLUMNS) + " |\n")
        out.write("| " + " | ".join("---:" if c in numeric else "---" for c in CSV_COLUMNS) + " |\n")
        for row in rendered:
            out.write("| " + " | ".join(row) + " |\n")
    sys.stdout.write(out.getvalue())
    return 1 if any(r.failure_tokens for r in results) else 0


def _emit_compute_regression(baseline: float, cohort: float) -> int:
    if baseline <= 0:
        sys.stderr.write("PHASE9_WALL_CLOCK_UNAVAILABLE: baseline must be positive\n"); return 1
    sys.stdout.write(json.dumps({"wall_clock_regression_pct": (cohort / baseline - 1.0) * 100.0}) + "\n")
    return 0


def _emit_evaluate_gates(cycles: int, yield_pct: float, context_pct: float, regression_pct: float) -> int:
    verdicts = {
        "cycles": "PASS" if cycles <= MAX_CYCLES_FOR_CONVERGENCE else "FAIL",
        "defect_yield_per_stream": "PASS" if DEFECT_YIELD_PCT_MIN <= yield_pct <= DEFECT_YIELD_PCT_MAX else "FAIL",
        "f2_context_pct": "PASS" if context_pct < MAX_F2_CONTEXT_PCT else "FAIL",
        "wall_clock_regression_pct": "PASS" if regression_pct < MAX_WALL_CLOCK_REGRESSION_PCT else "FAIL",
    }
    overall = "PASS" if all(v == "PASS" for v in verdicts.values()) else "FAIL"
    sys.stdout.write(json.dumps({"overall_verdict": overall, "gate_verdicts": verdicts}) + "\n")
    return 0


def _emit_evaluate_saturation(bc: int, cc: int, byp: float, cyp: float) -> int:
    sys.stdout.write(json.dumps({"saturated": _is_saturated(bc, cc, byp, cyp)}) + "\n")
    return 0


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="measure-run.py",
        description="Phase 9 / RUN-01 — per-run JSON + cohort --matrix aggregator (--strict supported).")
    p.add_argument("run_dir", nargs="?", type=Path, default=None)
    p.add_argument("--matrix", type=Path, default=None, metavar="RUNS_DIR")
    p.add_argument("--format", choices=("csv", "markdown", "both"), default="both")
    p.add_argument("--strict", action="store_true")
    p.add_argument("--compute-regression", action="store_true")
    p.add_argument("--baseline-seconds", type=float, default=None)
    p.add_argument("--cohort-seconds", type=float, default=None)
    p.add_argument("--evaluate-gates", action="store_true")
    p.add_argument("--cycles", type=int, default=None)
    p.add_argument("--yield-pct", type=float, default=None)
    p.add_argument("--context-pct", type=float, default=None)
    p.add_argument("--regression-pct", type=float, default=None)
    p.add_argument("--evaluate-saturation", action="store_true")
    p.add_argument("--baseline-count", type=int, default=None)
    p.add_argument("--cohort-count", type=int, default=None)
    p.add_argument("--baseline-yield-pct", type=float, default=None)
    p.add_argument("--cohort-yield-pct", type=float, default=None)
    return p


def main(argv: list[str]) -> int:
    args = _build_parser().parse_args(argv)
    if args.compute_regression:
        if args.baseline_seconds is None or args.cohort_seconds is None:
            sys.stderr.write("--compute-regression needs --baseline-seconds + --cohort-seconds\n"); return 2
        return _emit_compute_regression(args.baseline_seconds, args.cohort_seconds)
    if args.evaluate_gates:
        if any(x is None for x in (args.cycles, args.yield_pct, args.context_pct, args.regression_pct)):
            sys.stderr.write("--evaluate-gates needs --cycles + --yield-pct + --context-pct + --regression-pct\n"); return 2
        return _emit_evaluate_gates(args.cycles, args.yield_pct, args.context_pct, args.regression_pct)
    if args.evaluate_saturation:
        if any(x is None for x in (args.baseline_count, args.cohort_count, args.baseline_yield_pct, args.cohort_yield_pct)):
            sys.stderr.write("--evaluate-saturation needs --baseline-count + --cohort-count + --baseline-yield-pct + --cohort-yield-pct\n"); return 2
        return _emit_evaluate_saturation(args.baseline_count, args.cohort_count, args.baseline_yield_pct, args.cohort_yield_pct)
    if args.matrix is not None:
        return _emit_matrix(args.matrix, strict=args.strict, fmt=args.format)
    if args.run_dir is None:
        _build_parser().error("either run_dir or --matrix RUNS_DIR is required")
    return _emit_per_run(args.run_dir, strict=args.strict)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
