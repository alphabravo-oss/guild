#!/usr/bin/env python3
"""FR-021 / NFR-003 — migrate-archive.py.

Upgrades ONE old ``foundry-archive/{run}/`` directory, in place, to the run
schemas this release introduces. Stdlib only; no runtime deps beyond the
canonical vocabulary module, which is itself stdlib-only.

Six steps, each independently guarded on absence so the whole tool is
idempotent (NFR-003) even against a hand-edited or half-migrated archive:

  1. defects.json     — every record gains ``class: null`` and
                        ``classification: "DEFECT"`` when absent
  2. observations.json — created as {"observations": []} when absent
  3. stream-rollup.json — per-cycle, per-canonical-stream defect counts,
                        re-derived from the archive's own data
  4. progress/        — per-agent progress-ledger directory, created empty
  5. state.json       — ``cycle`` repaired from the run's own data when the
                        recorded value is missing/invalid/too low
  6. state.json       — ``archive_schema_version`` marker recorded

ARCHIVED HISTORY IS NOT NORMALISED. Defect ``type`` and ``source`` values are
preserved verbatim even when they fall outside the reconciled vocabulary
(grand-vulture carries 42 records typed FALSE_DOCUMENTED_CONTRACT and one
typed TEST_RECORD_SCOPE, which entered through the unvalidated sync path).
The reconciled vocabulary governs NEW input, not history. AC-026 requires no
data loss, and that includes values the current enums would reject.

Every write is atomic — write ``.tmp``, then rename — so an interrupted run
can never leave a half-written defects.json behind. ``--dry-run`` reports
what would change and writes nothing at all.

Exit 0 OK; 1 on migration failure; 2 on usage error.
"""
from __future__ import annotations
import argparse, json, sys
from pathlib import Path
from typing import Any

try:  # Installed (uvx/pip) case — package is already importable.
    from foundry_mcp.schemas.vocab import canonical_stream_id
except ModuleNotFoundError:  # Dev / non-installed checkout — add src/ to path.
    _SRC = Path(__file__).resolve().parents[1] / "mcp-server" / "src"
    if _SRC.is_dir() and str(_SRC) not in sys.path:
        sys.path.insert(0, str(_SRC))
    from foundry_mcp.schemas.vocab import canonical_stream_id


# The schema generation this tool brings an archive to. Bump only when a new
# migration step is added, and add the step to MIGRATION_STEPS below.
ARCHIVE_SCHEMA_VERSION = 1

# CLOSED VOCABULARY — the six migration steps, in execution order. The
# summary reports one outcome per step under exactly these names.
# Extend only via phase-level RFC.
MIGRATION_STEPS = (
    "defects",
    "observations",
    "stream_rollup",
    "progress",
    "state_cycle",
    "archive_schema_version",
)  # 6 steps

# CLOSED VOCABULARY — per-step outcomes.
# Extend only via phase-level RFC.
MIGRATION_OUTCOMES = frozenset({"created", "upgraded", "no-op", "failed"})  # 4 outcomes

# CLOSED VOCABULARY — named refusals. Mirrors measure-run.py's failure-token
# discipline: a step that cannot proceed names why rather than guessing.
MIGRATION_FAILURE_TOKENS = frozenset({
    "MIGRATE_RUN_DIR_INVALID",
    "MIGRATE_DEFECTS_FILE_MALFORMED",
    "MIGRATE_STATE_FILE_MALFORMED",
    "MIGRATE_VERDICTS_FILE_MALFORMED",
    "MIGRATE_WRITE_FAILED",
})  # 5 tokens


# ---------------------------------------------------------------------------
# JSON helpers — tolerant reader, atomic writer.
# ---------------------------------------------------------------------------


class _Malformed(Exception):
    """Raised when a file exists but cannot be parsed as the expected shape."""


def _load_json(path: Path) -> Any:
    """Read+parse a JSON file; return None on missing/malformed."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, FileNotFoundError):
        return None


def _save_json(path: Path, data: Any) -> None:
    """Atomic JSON write — write to .tmp then rename.

    Never json.dump straight to the destination: a half-written defects.json
    is exactly the data loss AC-026 forbids.
    """
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    tmp.rename(path)


def _as_cycle(value: Any) -> int | None:
    """Return value when it is a real non-negative int, else None."""
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


# ---------------------------------------------------------------------------
# Step 1 — defects.json gains the class + classification fields.
# ---------------------------------------------------------------------------


def _migrate_defects(run_dir: Path, dry_run: bool) -> tuple[str, dict[str, Any]]:
    path = run_dir / "defects.json"
    if not path.exists():
        return "no-op", {"reason": "defects.json absent"}
    data = _load_json(path)
    if not isinstance(data, dict) or not isinstance(data.get("defects"), list):
        raise _Malformed("MIGRATE_DEFECTS_FILE_MALFORMED")

    changed = 0
    for record in data["defects"]:
        if not isinstance(record, dict):
            raise _Malformed("MIGRATE_DEFECTS_FILE_MALFORMED")
        touched = False
        # Optional stream-declared class field (escalation keys on it).
        if "class" not in record:
            record["class"] = None
            touched = True
        # The DEFECT / OBSERVATION classification axis. Everything already in
        # a defect ledger was filed as a defect; migration never reclassifies.
        if "classification" not in record:
            record["classification"] = "DEFECT"
            touched = True
        if touched:
            changed += 1

    if changed == 0:
        return "no-op", {"records": len(data["defects"]), "upgraded": 0}
    if not dry_run:
        _save_json(path, data)
    return "upgraded", {"records": len(data["defects"]), "upgraded": changed}


# ---------------------------------------------------------------------------
# Step 2 — observations.json ledger.
# ---------------------------------------------------------------------------


def _migrate_observations(run_dir: Path, dry_run: bool) -> tuple[str, dict[str, Any]]:
    path = run_dir / "observations.json"
    if path.exists():
        return "no-op", {"reason": "observations.json already present"}
    # Never populated from defects.json: migration does not reclassify
    # anything that was already filed as a defect.
    if not dry_run:
        _save_json(path, {"observations": []})
    return "created", {"observations": 0}


# ---------------------------------------------------------------------------
# Step 3 — stream-rollup.json, re-derived from the archive's own data.
# ---------------------------------------------------------------------------


def _derive_stream_rollup(run_dir: Path) -> dict[str, Any]:
    """Per-cycle, per-canonical-stream defect counts.

    Maps each record's ``source`` through vocab.canonical_stream_id exactly
    as measure-run.py does, so the two artifacts can never disagree. Output
    is built in a fixed order (numeric by cycle, alphabetical by stream) so
    re-deriving from the same archive yields a byte-identical document.
    """
    data = _load_json(run_dir / "defects.json")
    records = data.get("defects") if isinstance(data, dict) else None
    if not isinstance(records, list):
        records = []

    per_cycle: dict[int, dict[str, int]] = {}
    unresolved: dict[str, int] = {}
    for record in records:
        if not isinstance(record, dict):
            continue
        cycle = _as_cycle(record.get("cycle"))
        if cycle is None:
            continue
        raw = record.get("source")
        if raw is None:
            raw = record.get("stream")
        if not isinstance(raw, str):
            continue
        stream = canonical_stream_id(raw)
        if stream is None:
            # Named, not coerced — history keeps its value and the roll-up
            # records that it falls outside the reconciled vocabulary.
            unresolved[raw] = unresolved.get(raw, 0) + 1
            continue
        streams = per_cycle.setdefault(cycle, {})
        streams[stream] = streams.get(stream, 0) + 1

    totals: dict[str, int] = {}
    for streams in per_cycle.values():
        for stream, count in streams.items():
            totals[stream] = totals.get(stream, 0) + count

    return {
        "schema_version": ARCHIVE_SCHEMA_VERSION,
        "cycles": {
            str(cycle): {k: per_cycle[cycle][k] for k in sorted(per_cycle[cycle])}
            for cycle in sorted(per_cycle)
        },
        "totals": {k: totals[k] for k in sorted(totals)},
        "unresolved_sources": {k: unresolved[k] for k in sorted(unresolved)},
    }


def _migrate_stream_rollup(run_dir: Path, dry_run: bool) -> tuple[str, dict[str, Any]]:
    path = run_dir / "stream-rollup.json"
    derived = _derive_stream_rollup(run_dir)
    if path.exists():
        return "no-op", {"reason": "stream-rollup.json already present"}
    if not dry_run:
        _save_json(path, derived)
    return "created", {
        "cycles": len(derived["cycles"]),
        "streams": len(derived["totals"]),
    }


# ---------------------------------------------------------------------------
# Step 4 — per-agent progress ledger directory.
# ---------------------------------------------------------------------------


def _migrate_progress(run_dir: Path, dry_run: bool) -> tuple[str, dict[str, Any]]:
    path = run_dir / "progress"
    if path.is_dir():
        return "no-op", {"reason": "progress/ already present"}
    if path.exists():
        raise _Malformed("MIGRATE_WRITE_FAILED")
    if not dry_run:
        path.mkdir(parents=True)
    return "created", {"path": "progress/"}


# ---------------------------------------------------------------------------
# Steps 5 + 6 — state.json cycle repair and schema-version marker.
# ---------------------------------------------------------------------------


def _observed_max_cycle(run_dir: Path) -> int:
    """Highest cycle the run's own data proves it reached."""
    observed = 0
    data = _load_json(run_dir / "defects.json")
    records = data.get("defects") if isinstance(data, dict) else None
    if isinstance(records, list):
        for record in records:
            if not isinstance(record, dict):
                continue
            for key in ("cycle", "fixed_in_cycle", "reopened_in_cycle"):
                cycle = _as_cycle(record.get(key))
                if cycle is not None and cycle > observed:
                    observed = cycle
    verdicts = _load_json(run_dir / "verdicts.json")
    if isinstance(verdicts, dict):
        cycle = _as_cycle(verdicts.get("cycle"))
        if cycle is not None and cycle > observed:
            observed = cycle
    return observed


def _migrate_state(
    run_dir: Path, dry_run: bool
) -> tuple[str, dict[str, Any], str, dict[str, Any]]:
    """Repair state.json's cycle and stamp the schema marker.

    Returns (cycle_outcome, cycle_detail, marker_outcome, marker_detail).
    Both steps write the same file, so they share one read/modify/write.
    """
    path = run_dir / "state.json"
    existed = path.exists()
    if existed:
        state = _load_json(path)
        if not isinstance(state, dict):
            raise _Malformed("MIGRATE_STATE_FILE_MALFORMED")
    else:
        state = {}

    observed = _observed_max_cycle(run_dir)
    recorded = _as_cycle(state.get("cycle"))
    # Never lower an already-higher recorded value.
    repaired = observed if recorded is None else max(recorded, observed)

    if recorded == repaired:
        cycle_outcome = "no-op"
    elif recorded is None:
        cycle_outcome = "created" if not existed else "upgraded"
    else:
        cycle_outcome = "upgraded"
    cycle_detail = {"recorded": recorded, "observed": observed, "cycle": repaired}

    marker = state.get("archive_schema_version")
    if marker == ARCHIVE_SCHEMA_VERSION:
        marker_outcome = "no-op"
    else:
        marker_outcome = "upgraded" if existed else "created"
    marker_detail = {
        "from": marker,
        "to": ARCHIVE_SCHEMA_VERSION,
    }

    if cycle_outcome != "no-op" or marker_outcome != "no-op":
        state["cycle"] = repaired
        state["archive_schema_version"] = ARCHIVE_SCHEMA_VERSION
        if not dry_run:
            _save_json(path, state)

    return cycle_outcome, cycle_detail, marker_outcome, marker_detail


# ---------------------------------------------------------------------------
# Driver.
# ---------------------------------------------------------------------------


def migrate_archive(run_dir: Path, dry_run: bool = False) -> tuple[int, dict[str, Any]]:
    """Migrate one run dir. Returns (exit_code, summary)."""
    if not run_dir.exists() or not run_dir.is_dir():
        return 2, {
            "run_dir": str(run_dir),
            "failure_tokens": ["MIGRATE_RUN_DIR_INVALID"],
        }

    steps: dict[str, Any] = {}
    try:
        outcome, detail = _migrate_defects(run_dir, dry_run)
        steps["defects"] = {"outcome": outcome, **detail}
        outcome, detail = _migrate_observations(run_dir, dry_run)
        steps["observations"] = {"outcome": outcome, **detail}
        outcome, detail = _migrate_stream_rollup(run_dir, dry_run)
        steps["stream_rollup"] = {"outcome": outcome, **detail}
        outcome, detail = _migrate_progress(run_dir, dry_run)
        steps["progress"] = {"outcome": outcome, **detail}
        c_out, c_detail, m_out, m_detail = _migrate_state(run_dir, dry_run)
        steps["state_cycle"] = {"outcome": c_out, **c_detail}
        steps["archive_schema_version"] = {"outcome": m_out, **m_detail}
    except _Malformed as exc:
        token = str(exc)
        return 1, {
            "run_dir": str(run_dir),
            "dry_run": dry_run,
            "steps": steps,
            "failure_tokens": [token],
        }
    except OSError:
        return 1, {
            "run_dir": str(run_dir),
            "dry_run": dry_run,
            "steps": steps,
            "failure_tokens": ["MIGRATE_WRITE_FAILED"],
        }

    changed = any(s["outcome"] in ("created", "upgraded") for s in steps.values())
    return 0, {
        "run_dir": str(run_dir),
        "dry_run": dry_run,
        "archive_schema_version": ARCHIVE_SCHEMA_VERSION,
        "migrated": changed,
        "steps": steps,
        "failure_tokens": [],
    }


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="migrate-archive.py",
        description=(
            "FR-021 — upgrade one foundry-archive run directory, in place, "
            "to the current run schemas. Idempotent; safe to re-run."
        ),
    )
    p.add_argument("run_dir", type=Path, help="the foundry-archive/{run} directory")
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="report what would change and write nothing",
    )
    return p


def main(argv: list[str]) -> int:
    args = _build_parser().parse_args(argv)
    exit_code, summary = migrate_archive(args.run_dir, dry_run=args.dry_run)
    sys.stdout.write(json.dumps(summary, indent=2) + "\n")
    return exit_code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
