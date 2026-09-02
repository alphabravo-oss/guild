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
  3. stream-rollup.json — the per-cycle roll-up, re-derived from the
                        archive's own data in the shape its consumer reads
  4. progress/        — per-agent progress-ledger directory, created empty
  5. state.json       — ``cycle`` repaired from the run's own data when the
                        recorded value is missing/invalid/too low. Reads the
                        SAME evidence step 3 keys the roll-up on, so a
                        migrated archive can never sit behind its own roll-up
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
    from foundry_mcp.schemas.vocab import (
        STREAM_WIRE_IDS,
        WIRE_TO_CANONICAL,
        canonical_stream_id,
    )
    from foundry_mcp.tools.foundry_state import read_json, read_text_file
except ModuleNotFoundError:  # Dev / non-installed checkout — add src/ to path.
    _SRC = Path(__file__).resolve().parents[1] / "mcp-server" / "src"
    if _SRC.is_dir() and str(_SRC) not in sys.path:
        sys.path.insert(0, str(_SRC))
    from foundry_mcp.schemas.vocab import (
        STREAM_WIRE_IDS,
        WIRE_TO_CANONICAL,
        canonical_stream_id,
    )
    from foundry_mcp.tools.foundry_state import read_json, read_text_file


# The schema generation this tool brings an archive to. Bump when a migration
# step is added (add it to MIGRATION_STEPS below) or when a step's OUTPUT shape
# changes — v1 wrote a stream-rollup.json its own consumer could not read.
ARCHIVE_SCHEMA_VERSION = 2

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
    """Read+parse a JSON file; return None on missing/malformed.

    D-141: byte-identical to measure-run.py's copy, and it leaked the same
    exception for the same reason -- ``(json.JSONDecodeError, OSError,
    FileNotFoundError)`` does not name UnicodeDecodeError, which
    ``read_text`` raises BEFORE ``json.loads`` is ever reached. Driven live
    on ``{"a": "caf\\xe9"}``, this raised out of the CLI. The raise set is
    now closed once, in ``foundry_state.read_json``, for both scripts and for
    every module in the package.
    """
    return read_json(path)[0]


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


# The roll-up is READ BACK by foundry_orchestrator._rollup_totals, which looks
# an entry up as ``cycles[str(cycle)][<wire stream id>]`` and requires a dict
# carrying these four keys. A migrated archive must therefore speak the
# server's own document shape rather than a derived summary of it: v1 wrote
# ``cycles[c][<CANONICAL>] = <int>``, which reads back as "no record for this
# cycle" — silently disabling the very coverage gate the artifact feeds.
# ``records`` must be present and a list even when empty: _record_stream_rollup
# appends to ``entry["records"]`` without a setdefault, so omitting it makes the
# first post-migration stream mark raise KeyError.
# Extend only via phase-level RFC.
ROLLUP_ENTRY_KEYS = ("items_checked", "items_total", "findings", "records")  # 4 keys

# Canonical id -> wire id. WIRE_TO_CANONICAL is injective, so the inverse is
# well-defined; it lets a record whose source was persisted in the canonical
# UPPERCASE spelling still land under the lowercase key the consumer reads.
_CANONICAL_TO_WIRE = {canon: wire for wire, canon in WIRE_TO_CANONICAL.items()}

# The key=value fields a ``.{stream}-complete`` marker carries.
_MARKER_FIELDS = ("cycle", "items_checked", "items_total", "findings")  # 4 fields


def _new_rollup_entry() -> dict[str, Any]:
    """A zero entry carrying exactly ROLLUP_ENTRY_KEYS, in that order.

    Derived from the constant rather than re-typing the same four keys one
    line below it, so the declared contract and the built object cannot drift.
    """
    entry: dict[str, Any] = dict.fromkeys(ROLLUP_ENTRY_KEYS, 0)
    entry["records"] = []
    return entry


def _wire_stream_for(raw: str) -> str | None:
    """Return the wire spelling of a defect ``source``, or None.

    Accepts either spelling — a wire id passes through, a canonical id is
    inverted — because the consumer keys strictly by wire id. Sources that are
    legal but are not streams (``assay``, ``temper``) and values outside the
    vocabulary alike return None; the caller records them by name rather than
    coercing them onto a stream that did not file them.
    """
    canonical = canonical_stream_id(raw)
    if canonical is None:
        return None
    return _CANONICAL_TO_WIRE.get(canonical)


def _read_stream_marker(run_dir: Path, wire_stream: str) -> dict[str, int] | None:
    """Parse a ``.{stream}-complete`` marker's key=value fields.

    The marker is the ONLY place a pre-roll-up archive records coverage, and it
    holds one cycle's terminal totals. ``cycle=`` sits on the timestamp line
    rather than at the start of its own line, so tokens are scanned
    whitespace-separated — foundry_orchestrator._marker_counts reads only
    line-leading keys and therefore never sees ``cycle`` at all.

    Returns None when the marker is absent, unreadable, or carries no usable
    cycle; a marker without a cycle cannot be attributed to one.
    """
    path = run_dir / f".{wire_stream}-complete"
    if not path.exists():
        return None
    # D-141: `except OSError` alone leaks UnicodeDecodeError. A stream marker
    # is a run artifact and is read the same way every other one is.
    text, problem = read_text_file(path)
    if problem is not None:
        return None
    found: dict[str, int] = {}
    for token in text.split():
        key, sep, value = token.partition("=")
        if not sep or key not in _MARKER_FIELDS:
            continue
        try:
            found[key] = int(value)
        except ValueError:
            continue
    cycle = _as_cycle(found.get("cycle"))
    if cycle is None:
        return None
    return {
        "cycle": cycle,
        "items_checked": max(found.get("items_checked", 0), 0),
        "items_total": max(found.get("items_total", 0), 0),
        "findings": max(found.get("findings", 0), 0),
    }


def _derive_stream_rollup(run_dir: Path) -> tuple[dict[str, Any], dict[str, int]]:
    """Re-derive the per-cycle roll-up from the archive's own data.

    Two sources, neither invented:

      * ``defects.json`` gives ``findings`` per (cycle, stream) — the ledger is
        authoritative for what was actually filed, and it covers every cycle.
      * each ``.{stream}-complete`` marker gives ``items_checked`` /
        ``items_total`` for the ONE cycle it records. Coverage is not
        derivable for any other cycle, so those entries carry 0 rather than a
        fabricated number: inventing coverage would let a migrated archive
        clear a >=95% gate on data the run never produced.

    The marker's own ``findings`` is taken as a floor. _coverage_shortfall and
    _prove_is_clean read ``_rollup_totals(...) or _marker_counts(...)``, and a
    non-empty dict is truthy — so any entry written at the marker's cycle
    SHADOWS the marker fallback. Taking the max keeps the migrated entry at
    least as strict as the fallback it replaces; a stream can never be judged
    cleaner after migration than before it.

    Returns (document, non_stream_source_counts). The document is built in a
    fixed order (numeric by cycle, alphabetical by stream) so re-deriving from
    the same archive yields a byte-identical file.
    """
    data = _load_json(run_dir / "defects.json")
    records = data.get("defects") if isinstance(data, dict) else None
    if not isinstance(records, list):
        records = []

    per_cycle: dict[int, dict[str, dict[str, Any]]] = {}
    non_stream: dict[str, int] = {}

    def entry_for(cycle: int, wire: str) -> dict[str, Any]:
        return per_cycle.setdefault(cycle, {}).setdefault(wire, _new_rollup_entry())

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
        wire = _wire_stream_for(raw)
        if wire is None:
            # Named, not coerced — history keeps its value and the summary
            # reports that it names no stream.
            non_stream[raw] = non_stream.get(raw, 0) + 1
            continue
        entry_for(cycle, wire)["findings"] += 1

    for wire in sorted(STREAM_WIRE_IDS):
        marker = _read_stream_marker(run_dir, wire)
        if marker is None:
            continue
        entry = entry_for(marker["cycle"], wire)
        entry["items_checked"] = max(entry["items_checked"], marker["items_checked"])
        entry["items_total"] = max(entry["items_total"], marker["items_total"])
        entry["findings"] = max(entry["findings"], marker["findings"])

    document = {
        "schema_version": ARCHIVE_SCHEMA_VERSION,
        "cycles": {
            str(cycle): {
                wire: per_cycle[cycle][wire] for wire in sorted(per_cycle[cycle])
            }
            for cycle in sorted(per_cycle)
        },
    }
    return document, {k: non_stream[k] for k in sorted(non_stream)}


def _rollup_needs_rebuild(existing: Any) -> bool:
    """True when the stored roll-up is in a shape _rollup_totals cannot read.

    v1 of this tool wrote ``cycles[c][<CANONICAL>] = <int>``. Those documents
    are inert and must be re-derived, or an archive "migrated" by v1 keeps a
    roll-up its consumer silently ignores.

    A document whose entries are ALREADY dicts is either server-written — with
    a real ``records`` audit trail and real coverage numbers — or already v2.
    Never re-derive it: the archive's own data cannot reconstruct what the
    server observed, so a rebuild would be data loss.
    """
    if not isinstance(existing, dict):
        return True
    cycles = existing.get("cycles")
    if not isinstance(cycles, dict):
        return True
    for bucket in cycles.values():
        if not isinstance(bucket, dict):
            return True
        if any(not isinstance(entry, dict) for entry in bucket.values()):
            return True
    return False


def _rollup_detail(document: dict[str, Any], non_stream: dict[str, int]) -> dict[str, Any]:
    """Point-in-time report of what the derivation found.

    Aggregates live in the SUMMARY, never in the document: a persisted total
    sitting beside live per-cycle data goes stale the moment the server appends
    the next stream record.
    """
    findings: dict[str, int] = {}
    for bucket in document["cycles"].values():
        for wire, entry in bucket.items():
            findings[wire] = findings.get(wire, 0) + entry["findings"]
    return {
        "cycles": len(document["cycles"]),
        "streams": len(findings),
        "findings_per_stream": {k: findings[k] for k in sorted(findings)},
        "non_stream_sources": non_stream,
    }


def _migrate_stream_rollup(run_dir: Path, dry_run: bool) -> tuple[str, dict[str, Any]]:
    path = run_dir / "stream-rollup.json"
    if path.exists() and not _rollup_needs_rebuild(_load_json(path)):
        return "no-op", {"reason": "stream-rollup.json already readable by _rollup_totals"}
    outcome = "upgraded" if path.exists() else "created"
    document, non_stream = _derive_stream_rollup(run_dir)
    if not dry_run:
        _save_json(path, document)
    return outcome, _rollup_detail(document, non_stream)


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


def _marker_max_cycle(run_dir: Path) -> int:
    """Highest cycle any ``.{stream}-complete`` marker records.

    Reads through the SAME parser and the SAME stream set step 3 keys the
    roll-up on (_read_stream_marker over STREAM_WIRE_IDS), so the two steps
    cannot read different evidence for the same number again (D-060).
    """
    highest = 0
    for wire in sorted(STREAM_WIRE_IDS):
        marker = _read_stream_marker(run_dir, wire)
        if marker is not None and marker["cycle"] > highest:
            highest = marker["cycle"]
    return highest


def _rollup_max_cycle(run_dir: Path) -> int:
    """Highest cycle key in the roll-up ALREADY ON DISK.

    Step 3 leaves a server-written document exactly as found, and that
    document's keys are the server's own counter values. Reading them here
    keeps the post-condition unconditional even for a roll-up this tool did
    not derive.
    """
    data = _load_json(run_dir / "stream-rollup.json")
    cycles = data.get("cycles") if isinstance(data, dict) else None
    if not isinstance(cycles, dict):
        return 0
    highest = 0
    for raw_cycle in cycles:
        try:
            cycle = int(raw_cycle)
        except (TypeError, ValueError):
            continue
        if cycle > highest:
            highest = cycle
    return highest


def _observed_max_cycle(run_dir: Path) -> int:
    """Highest cycle the run's own data proves it reached.

    MUST consult every source step 3 keys the roll-up on. Reading only
    defects.json + verdicts.json left state.json BEHIND stream-rollup.json's
    highest key in the ORDINARY case — the last INSPECT cycle's clean streams
    file no defects, so their ``.{stream}-complete`` markers record a cycle no
    defect record carries. measure-run._reconcile_final_cycle_index then fired
    PHASE9_CYCLE_COUNT_INVALID on the archive this tool had just repaired, and
    re-running never healed it because step 5 never consulted what step 3 wrote
    (D-060). grand-vulture masked it by coincidence: all three of its markers
    carry cycle=17, equal to its max defect cycle.

    Four sources, none invented:

      * defects.json — ``cycle`` / ``fixed_in_cycle`` / ``reopened_in_cycle``
      * verdicts.json — ``cycle``
      * each ``.{stream}-complete`` marker — the cycle it terminates
      * the roll-up already on disk — a server-written document's own keys

    The marker source is what makes the derivation self-contained under
    ``--dry-run``, where step 3 writes nothing: the reported ``observed`` is
    identical to the value a real run would record.
    """
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
    return max(observed, _marker_max_cycle(run_dir), _rollup_max_cycle(run_dir))


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
