#!/usr/bin/env python3
"""Phase 9 / RUN-01 — measure-run.py.

Two-mode CLI: per-run JSON extractor + cohort --matrix aggregator. Mirrors
Phase 4-8 closed-vocabulary discipline (stdlib only; no runtime deps).
Three frozensets locked at module top: KNOWN_PHASE9_STREAM_IDS (15),
KNOWN_PHASE9_FAILURE_TOKENS (9), KNOWN_PHASE9_COHORT_IDS (10). Wall-clock =
first/last handoffs.jsonl timestamp delta (Pitfall 5 / 09-RESEARCH.md).
Exit 0 OK; 1 on token rejection / gate FAIL; 2 on usage error.

Both halves of that exit contract run through ``_exit_status`` — see its
docstring for why a MISSING gate is a 0 and only a blown one is a 1. Every
entry point that evaluates gates returns through it, so a verdict can never
again be printed without reaching the process status (D-105).

The stream roster is DERIVED from foundry_mcp.schemas.vocab (FR-013): this
script was one of six independently re-typed copies of the same vocabulary.
vocab is stdlib-only and imports nothing from foundry_mcp.tools, so this
script keeps its "stdlib only; no runtime deps" contract.

Every read of a run artifact goes through foundry_mcp.tools.foundry_state
(D-141), for the same reason and on the same terms: that module imports json
and pathlib and nothing else -- not even from its own package -- so it costs
this script's stdlib-only contract nothing, and it is where the raise set of a
document read is decided ONCE. This script's own `_load_json` used to name
`(json.JSONDecodeError, OSError, FileNotFoundError)`, which is the majority
spelling of the fourteen sites D-137 closed inside the package and leaks
UnicodeDecodeError exactly as they did -- driven live, one non-UTF-8 byte in a
run artifact raised straight out of this CLI.

Four artifacts are read per run, and each is optional in the way a REAL archive
makes it optional (FR-018 / AC-024 — the gates must operate on real data):
defects.json (what streams found), stream-rollup.json (what they checked, keyed
by the server cycle counter), state.json (the recorded cycle), handoffs.jsonl
(wall clock). cohort.json and context-at-f2.txt are cohort-study inputs no run
writes; their absence is strict-gated, never a schema violation.

THE CONVERGENCE COLUMNS (NFR-001 / AC-039 / OT-030 / FR-051)
------------------------------------------------------------
Five more columns say whether a run converged and at what cost:
``defects_by_tier`` (LIVE / LATENT / unknown — an untiered pre-change record
reads as unknown and never as LATENT), ``spend`` (tokens and minutes per phase
and per cycle, from spend.jsonl), ``inspect_modes`` (FULL or DELTA per cycle,
from state.json), ``escalation`` (the class census and its machine-readable
exit reasons), and ``baseline_comparison`` (this run's numbers beside
thunder-viper's 22 cycles / 8 post-verification and the <=12 / <=3 target).

Three of those artifacts postdate thunder-viper, which executed on the 4.7.3
cache: it has no escalation.json, no spend.jsonl and no ``inspect_modes``, and
its cycle counter stayed at 0 for all 22 cycles. OT-030 nonetheless requires
this command to read that archive without a traceback, so every column it
cannot supply reports ``null`` — never 0, and never a failure token that would
make the command exit nonzero on a healthy archive. The baseline's own two
numbers are therefore READ FROM vocab.THUNDER_VIPER_BASELINE, not derived from
the archive that cannot hold them.

NFR-001 says the numbers are "the target, not a gate", and nothing in this
paragraph reaches ``_compute_gate_verdicts`` or ``_exit_status``. Missing the
convergence target is reported; it is never a refusal.

``cycles`` in the payload is a COUNT. The server's counter is 0-based, so the
count is the final index + 1 — the conversion happens exactly once, in
_extract_per_run. NFR-001 states grand-vulture's baseline as "18 cycles, 168
defects"; migrate-then-measure on a copy of that archive must print both.
"""
from __future__ import annotations
import argparse, csv, io, json, sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

try:  # Installed (uvx/pip) case — package is already importable.
    from foundry_mcp.schemas.vocab import (
        CANONICAL_STREAM_IDS,
        CONVERGENCE_TARGET,
        DEFECT_TIER_OR_UNKNOWN,
        ESCALATION_EXIT_REASONS,
        ESCALATION_STATUSES,
        INSPECT_MODES,
        SPEND_LEDGER_FILENAME,
        THUNDER_VIPER_BASELINE,
        canonical_defect_source,
        canonical_stream_id,
        defect_tier,
    )
    from foundry_mcp.tools.foundry_state import read_json, read_text_file
except ModuleNotFoundError:  # Dev / non-installed checkout — add src/ to path.
    _SRC = Path(__file__).resolve().parents[1] / "mcp-server" / "src"
    if _SRC.is_dir() and str(_SRC) not in sys.path:
        sys.path.insert(0, str(_SRC))
    from foundry_mcp.schemas.vocab import (
        CANONICAL_STREAM_IDS,
        CONVERGENCE_TARGET,
        DEFECT_TIER_OR_UNKNOWN,
        ESCALATION_EXIT_REASONS,
        ESCALATION_STATUSES,
        INSPECT_MODES,
        SPEND_LEDGER_FILENAME,
        THUNDER_VIPER_BASELINE,
        canonical_defect_source,
        canonical_stream_id,
        defect_tier,
    )
    from foundry_mcp.tools.foundry_state import read_json, read_text_file


# Derived, never re-typed — foundry_mcp.schemas.vocab is the single source of
# truth for the canonical roster. The name is retained because the anti-drift
# cross-grep in tests/test_measure_run.py pins it.
KNOWN_PHASE9_STREAM_IDS = CANONICAL_STREAM_IDS  # 15 streams

KNOWN_PHASE9_FAILURE_TOKENS = frozenset({
    "PHASE9_UNKNOWN_STREAM", "PHASE9_UNKNOWN_COHORT", "PHASE9_RUN_DIR_INVALID",
    "PHASE9_CONTEXT_FILE_MISSING", "PHASE9_WALL_CLOCK_UNAVAILABLE",
    "PHASE9_CYCLE_COUNT_INVALID", "PHASE9_SCHEMA_INVALID",
    "PHASE9_DEFECTS_FILE_MALFORMED",
    # D-106 extends the vocabulary from the 8 tokens locked per CONTEXT.md to
    # 9. --matrix over a real directory holding no cohort arm printed an empty
    # table and exited 0, so the ORDINARY operator mistake — the archive root
    # instead of the runs dir, or arms named other than the ten baked-in ids —
    # was the one case that reported success. PHASE9_RUN_DIR_INVALID could not
    # carry it: that token means "not a directory", and an operator who cannot
    # tell "fix the path" from "fix the arm names" from the refusal is left
    # with the same diagnosis problem. A distinct mistake earns a distinct
    # name. (Extending a closed token vocabulary with provenance is the
    # established shape here — cf. KNOWN_EVIDENCE_FAILURE_TOKENS, Phase 4.)
    "PHASE9_NO_COHORTS",
})  # 9 tokens

KNOWN_PHASE9_COHORT_IDS = frozenset({
    "v4_2_0_baseline", "all_enabled_baseline",
    "no_INTV_01", "no_TYPE_01", "no_TYPE_02",
    "no_EVID_01", "no_EVID_02",
    "no_PROBE_01", "no_TEST_01", "no_INTENT_01",
})  # 10 cohorts

# RUN-01 quantitative gate thresholds (locked per CONTEXT.md).
#
# MAX_CYCLES_FOR_CONVERGENCE is a COUNT of cycles, and both paths that evaluate
# it now feed it one: the per-run extractor (which converts the server's 0-based
# index once, in _extract_per_run) and the operator-supplied
# ``--evaluate-gates --cycles N``. Feeding the raw index down the first path
# admitted one cycle more than this number names, at every threshold value.
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
    # A COUNT of the cycles the run executed, not the server's 0-based index
    # (see _reconcile_final_cycle_index). 0 means no measurement was possible —
    # only an invalid run dir short-circuits before the count is computed.
    cycles: int = 0
    per_stream_defects: dict[str, int] = field(default_factory=dict)
    f2_context_pct: float | None = None
    wall_clock_seconds: float = 0.0
    gate_verdicts: dict[str, str] = field(default_factory=dict)
    failure_tokens: list[str] = field(default_factory=list)
    disable_lever: str = ""
    wall_clock_regression_pct: float | None = None
    per_cycle_coverage: dict[str, dict[str, dict[str, int]]] = field(default_factory=dict)
    # NFR-001 / AC-039 / FR-051 columns. Each is None when the archive cannot
    # supply it — see the section comment above _read_defects_by_tier for why
    # that is not 0 and not a failure token. `baseline_comparison` is never
    # None: its baseline and target come from vocab constants that are always
    # available, and the current-run half carries its own None where needed.
    defects_by_tier: dict[str, int] | None = None
    spend: dict[str, Any] | None = None
    inspect_modes: dict[str, Any] | None = None
    escalation: dict[str, Any] | None = None
    baseline_comparison: dict[str, Any] = field(default_factory=dict)

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "cohort_id": self.cohort_id, "cycles": self.cycles,
            "per_stream_defects": self.per_stream_defects,
            "defects_by_tier": self.defects_by_tier,
            "per_cycle_coverage": self.per_cycle_coverage,
            "inspect_modes": self.inspect_modes,
            "escalation": self.escalation,
            "spend": self.spend,
            "f2_context_pct": self.f2_context_pct,
            "wall_clock_seconds": self.wall_clock_seconds,
            "baseline_comparison": self.baseline_comparison,
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
    """Read+parse a JSON file; return None on missing/malformed.

    D-141: the handler set is no longer re-decided here. ``read_json`` closes
    OSError, UnicodeDecodeError and the parse in ONE call -- the read and the
    decode are one operation, and every seam between them is a place a hand-
    written ``except`` has historically failed to cover. The returned
    ``problem`` is discarded on purpose: this reader's whole contract is
    "None on missing/malformed", and its callers turn that into the named
    PHASE9_* failure tokens they own.
    """
    return read_json(path)[0]


def _read_cohort_json(run_dir: Path, strict: bool) -> tuple[str | None, str, list[str]]:
    """Return (cohort_id, disable_lever, failure_tokens).

    ``cohort.json`` is a Phase-9 A/B *cohort study* input, hand-placed beside
    the archive to name which arm it is; no foundry run writes one. Its ABSENCE
    therefore means "an ordinary run, not a cohort arm" — not a schema
    violation. Treating it as one made EVERY real archive emit
    PHASE9_SCHEMA_INVALID and exit 1, which is the failing half of "its
    run-quality gates operate on real data" (FR-018 / AC-024).

    Absence is strict-gated exactly as context-at-f2.txt is, so the cohort-study
    workflow keeps its strictness. A cohort.json that EXISTS but is malformed
    is still a schema violation.
    """
    path = run_dir / "cohort.json"
    if not path.exists():
        return None, "", (["PHASE9_SCHEMA_INVALID"] if strict else [])
    data = _load_json(path)
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
    # D-141: `except OSError` alone leaks UnicodeDecodeError, which is the same
    # residual one rung up from `_load_json`'s. handoffs.jsonl is a run
    # artifact like any other, so it is read the same way.
    text, problem = read_text_file(path)
    if problem is not None:
        return 0.0, UNAVAIL
    lines = [ln for ln in text.splitlines() if ln.strip()]
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


def _read_stream_rollup(
    run_dir: Path,
) -> tuple[dict[str, dict[str, dict[str, int]]], int | None, list[str]]:
    """Read the per-cycle roll-up (CT-003) — the coverage the ledger cannot show.

    ``defects.json`` records what each stream FOUND; only stream-rollup.json
    records what each stream CHECKED, and it is the sole artifact keyed by the
    server-side cycle counter. Leaving it unread is why the instrumentation
    reported defect yield without the denominator that makes yield meaningful.

    Returns (per_cycle_coverage, highest_cycle_key, failure_tokens). Coverage is
    re-keyed onto the canonical UPPERCASE ids so the whole payload speaks one
    spelling, matching per_stream_defects.

    Absence is NOT a failure: archives written before the roll-up existed are
    precisely the ones this instrumentation has to measure. A file that exists
    but is not the documented shape is PHASE9_SCHEMA_INVALID.
    """
    path = run_dir / "stream-rollup.json"
    if not path.exists():
        return {}, None, []
    data = _load_json(path)
    if not isinstance(data, dict) or not isinstance(data.get("cycles"), dict):
        return {}, None, ["PHASE9_SCHEMA_INVALID"]

    coverage: dict[str, dict[str, dict[str, int]]] = {}
    highest: int | None = None
    fts: list[str] = []
    for raw_cycle, bucket in data["cycles"].items():
        try:
            cycle = int(raw_cycle)
        except (TypeError, ValueError):
            fts.append("PHASE9_SCHEMA_INVALID"); continue
        if cycle < 0 or not isinstance(bucket, dict):
            fts.append("PHASE9_SCHEMA_INVALID"); continue
        if highest is None or cycle > highest:
            highest = cycle
        for raw_stream, entry in bucket.items():
            if not isinstance(raw_stream, str) or not isinstance(entry, dict):
                fts.append("PHASE9_SCHEMA_INVALID"); continue
            stream = canonical_stream_id(raw_stream)
            if stream is None:
                fts.append(f"PHASE9_UNKNOWN_STREAM:{raw_stream}"); continue
            counts: dict[str, int] = {}
            for key in ("items_checked", "items_total", "findings"):
                value = entry.get(key, 0)
                counts[key] = 0 if isinstance(value, bool) or not isinstance(value, int) else value
            coverage.setdefault(str(cycle), {})[stream] = counts
    return coverage, highest, fts


def _reconcile_final_cycle_index(
    recorded: int, rollup_highest: int | None
) -> tuple[int, list[str]]:
    """Reconcile state.json's counter against the roll-up's own cycle keys.

    Returns the final cycle INDEX, not a count. The server's counter is
    0-based: Foundry-Init writes 0, the F1 -> F2 entry from CAST is the run's
    first INSPECT rather than a new cycle, and only the F3 GRIND -> F2 INSPECT
    boundary increments (foundry_orchestrator.foundry_mark_phase_complete,
    ``inspect_start``). So a run that executed N cycles ends at index N-1, and
    the caller converts once — see ``_extract_per_run``.

    This is NOT a second counter. The roll-up is keyed BY that same counter
    (FR-005 / ST-001), so its highest key is the counter's own value as of the
    last stream record — read from the artifact rather than recomputed.

    survey/data.md FI-1: ``state.json["cycle"]`` was written once as 0 and never
    incremented, so an unrepaired archive reports 0 cycles and the convergence
    gate PASSes on a number the run never had. When the roll-up proves a higher
    cycle, report the proven value and NAME the stale counter rather than
    passing a gate on fiction. Migration (migrate-archive.py step 5) is what
    repairs the archive; this is the detector that stops it going unnoticed.
    """
    if rollup_highest is None or rollup_highest <= recorded:
        return recorded, []
    return rollup_highest, ["PHASE9_CYCLE_COUNT_INVALID"]


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
        # Every writer of defects.json persists the filing stream as
        # `source`, lowercase (tools/foundry.py foundry_add_defect,
        # tools/foundry_orchestrator.py foundry_sync_defects). Reading
        # `stream` matched nothing on a real archive — that was FR-018's
        # key half. `stream` is kept as a legacy fallback so an archive
        # written under either shape still counts and nothing is lost.
        raw = d.get("source")
        if raw is None:
            raw = d.get("stream")
        if not isinstance(raw, str):
            fts.append("PHASE9_DEFECTS_FILE_MALFORMED"); continue
        # Case half of FR-018: persisted values are lowercase wire ids,
        # the roster is UPPERCASE. Accumulate under the canonical id.
        #
        # Resolved against the DEFECT-SOURCE vocabulary, not the stream
        # roster. D-091: this read `canonical_stream_id`, which knows only
        # the nine stream wire ids, while the field being read is `source`,
        # whose vocabulary is vocab.DEFECT_SOURCE_IDS — the same nine plus
        # `assay` and `temper`. Every ASSAY- and TEMPER-filed defect was
        # therefore dropped from the counts AND reported as
        # PHASE9_UNKNOWN_STREAM, so the yield gate was evaluated on a
        # truncated total and the run emitted a failure token naming a value
        # its own protocol declares legal. PHASE9_UNKNOWN_STREAM is now
        # reserved for values outside DEFECT_SOURCE_IDS, which is what it
        # always claimed to mean.
        stream = canonical_defect_source(raw)
        if stream is None:
            fts.append(f"PHASE9_UNKNOWN_STREAM:{raw}"); continue
        counts[stream] = counts.get(stream, 0) + 1
    return counts, fts


# ---------------------------------------------------------------------------
# NFR-001 / AC-039 / FR-051 — the convergence columns.
#
# STRUCTURALLY MISSING IS `None`, NEVER 0, AND NEVER A TOKEN.
# ----------------------------------------------------------
# Every reader below can be handed an archive that predates the artifact it
# reads. thunder-viper is the case that matters: it executed on the 4.7.3
# cache, so it has no escalation.json, no spend.jsonl and no `inspect_modes`,
# and OT-030 requires this command to read it without a traceback. D-034 is the
# precedent for how — the two cohort-study inputs no run writes report `None`
# and emit no failure token, because "not measured" and "measured zero" are
# different facts and only one of them is true. A zero here would put a real
# number in a column the run never populated; a token here would make the
# command exit 1 on a healthy archive, which is the over-firing calibration
# D-034 already had to undo.
#
# `defects_by_tier` is the ONE exception, and deliberately: when defects.json
# is readable, an untiered record is not unmeasured — FR-051 says it READS as
# unknown. So the tier counts are real counts over a real ledger, and the
# `unknown` column is where a pre-change archive's records land.
# ---------------------------------------------------------------------------


def _read_defects_by_tier(run_dir: Path) -> dict[str, int] | None:
    """Count the defect ledger by evidence tier, or None when there is none.

    A SECOND reader over defects.json rather than an extra return value from
    ``_read_defects_per_stream``: that function's body is pinned by
    ``test_the_defect_reader_uses_the_defect_source_resolver``, which inspects
    its source for the resolver D-091 got wrong. Widening it would put an
    unrelated axis inside a surface that exists to stay narrow, and these
    ledgers are small local JSON (~340 KB at the observed ceiling), so the
    second parse is not a cost worth trading that pin for.

    Failure tokens are ``_read_defects_per_stream``'s to emit — this reader is
    silent on the same malformed file rather than double-reporting it.

    Every member of DEFECT_TIER_OR_UNKNOWN is always present, including zeros:
    "0 LATENT defects in this run" is a measurement an operator needs to be
    able to read, and omitting the key would make it indistinguishable from
    the unmeasured case this function returns None for.
    """
    path = run_dir / "defects.json"
    if not path.exists():
        return None
    data = _load_json(path)
    if not isinstance(data, dict) or not isinstance(data.get("defects"), list):
        return None
    counts = dict.fromkeys(sorted(DEFECT_TIER_OR_UNKNOWN), 0)
    for record in data["defects"]:
        if isinstance(record, dict):
            counts[defect_tier(record)] += 1
    return counts


def _read_spend(run_dir: Path) -> dict[str, Any] | None:
    """Roll spend.jsonl up per phase and per cycle (CT-013), or None.

    One JSON object per line: agent, phase, cycle, tokens, duration_ms,
    recorded_at. Minutes are reported beside milliseconds because the question
    an operator actually asks is "how long did F3 take", and asking them to
    divide 34_620_000 by 60_000 in their head is how a column stops being read.

    A malformed LINE is skipped rather than failing the read: the ledger is
    append-only from many concurrent agents, so a torn final line is an
    ordinary crash artifact and must not cost the other 84 records.
    """
    path = run_dir / SPEND_LEDGER_FILENAME
    if not path.exists():
        return None
    text, problem = read_text_file(path)
    if problem is not None:
        return None

    by_phase: dict[str, dict[str, Any]] = {}
    by_cycle: dict[str, dict[str, Any]] = {}
    total = _new_spend_bucket()
    agents: set[str] = set()
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(entry, dict):
            continue
        tokens = _as_count(entry.get("tokens"))
        duration_ms = _as_count(entry.get("duration_ms"))
        phase = entry.get("phase")
        cycle = entry.get("cycle")

        buckets = [total]
        if isinstance(phase, str) and phase:
            buckets.append(by_phase.setdefault(phase, _new_spend_bucket()))
        if isinstance(cycle, int) and not isinstance(cycle, bool) and cycle >= 0:
            buckets.append(by_cycle.setdefault(str(cycle), _new_spend_bucket()))
        for bucket in buckets:
            bucket["tokens"] += tokens
            bucket["duration_ms"] += duration_ms
            bucket["agents"] += 1

        agent = entry.get("agent")
        if isinstance(agent, str) and agent:
            agents.add(agent)

    for bucket in (*by_phase.values(), *by_cycle.values(), total):
        bucket["minutes"] = round(bucket["duration_ms"] / 60_000.0, 2)
    total["distinct_agents"] = len(agents)
    return {
        "by_phase": {k: by_phase[k] for k in sorted(by_phase)},
        "by_cycle": {k: by_cycle[k] for k in sorted(by_cycle, key=_cycle_sort_key)},
        "total": total,
    }


def _new_spend_bucket() -> dict[str, Any]:
    return {"tokens": 0, "duration_ms": 0, "minutes": 0.0, "agents": 0}


def _as_count(value: Any) -> int:
    """A non-negative int, or 0. Bools are not counts (``True`` is not 1 here)."""
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return 0
    return value


def _cycle_sort_key(raw: str) -> tuple[int, int | str]:
    """Numeric order for cycle keys, with any non-numeric key sorted after."""
    try:
        return (0, int(raw))
    except (TypeError, ValueError):
        return (1, raw)


def _read_inspect_modes(run_dir: Path) -> dict[str, Any] | None:
    """Per-cycle INSPECT width from state.json's `inspect_modes` (GI-009).

    The list is APPEND-ONLY and one cycle can appear twice — an F2 INSPECT and
    a later F5 one both carry their own entry — so the map is keyed by cycle
    with the LAST entry winning, which is the same "current decision is the
    last entry" rule C-4 states. `post_verification_cycles` counts the distinct
    cycles whose INSPECT was opened in F5: thunder-viper's REPORT.md records
    that its build was verified at cycle 14 and cycles 15-22 were TEMPER
    hardening, which is where the baseline's 8 comes from.
    """
    data = _load_json(run_dir / "state.json")
    entries = data.get("inspect_modes") if isinstance(data, dict) else None
    if not isinstance(entries, list):
        return None

    per_cycle: dict[str, dict[str, Any]] = {}
    by_mode: dict[str, int] = dict.fromkeys(sorted(INSPECT_MODES), 0)
    post_verification: set[int] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        cycle = entry.get("cycle")
        if isinstance(cycle, bool) or not isinstance(cycle, int):
            continue
        mode = entry.get("mode")
        phase = entry.get("phase")
        per_cycle[str(cycle)] = {
            "phase": phase if isinstance(phase, str) else None,
            "mode": mode if isinstance(mode, str) else None,
            "rule": entry.get("rule") if isinstance(entry.get("rule"), str) else None,
        }
        if phase == "F5":
            post_verification.add(cycle)
    for decision in per_cycle.values():
        if decision["mode"] in by_mode:
            by_mode[decision["mode"]] += 1
    return {
        "per_cycle": {k: per_cycle[k] for k in sorted(per_cycle, key=_cycle_sort_key)},
        "by_mode": by_mode,
        "post_verification_cycles": len(post_verification),
    }


def _read_escalation(run_dir: Path) -> dict[str, Any] | None:
    """Escalated-class census from escalation.json (FR-028), or None.

    Reads the machine-readable exit reason FR-028 requires: a class that left
    ESCALATED did so either because it drew clean cycles or because its
    structural-pass budget ran out, and "which" is the whole point of recording
    it. A class with no `status` predates this release's fields and is counted
    as ESCALATED — the state it was in when it was written.
    """
    data = _load_json(run_dir / "escalation.json")
    classes = data.get("classes") if isinstance(data, dict) else None
    if not isinstance(classes, dict):
        return None

    by_status = dict.fromkeys(sorted(ESCALATION_STATUSES), 0)
    by_exit_reason = dict.fromkeys(sorted(ESCALATION_EXIT_REASONS), 0)
    unknown_status = 0
    for entry in classes.values():
        if not isinstance(entry, dict):
            continue
        status = entry.get("status")
        if not isinstance(status, str):
            status = "ESCALATED"
        if status in by_status:
            by_status[status] += 1
        else:
            unknown_status += 1
        reason = entry.get("exit_reason")
        if isinstance(reason, str) and reason in by_exit_reason:
            by_exit_reason[reason] += 1
    return {
        "classes": len(classes),
        "by_status": by_status,
        "by_exit_reason": by_exit_reason,
        "unknown_status": unknown_status,
    }


def _baseline_comparison(
    run_dir: Path, cycles: int, post_verification_cycles: int | None
) -> dict[str, Any]:
    """NFR-001 / AC-039 / OT-030 — this run's numbers beside thunder-viper's.

    The baseline is READ from vocab, never re-typed here: OT-030 asks for 22
    cycles and 8 post-verification cycles, and thunder-viper's own archive
    cannot supply either (its cycle counter stayed at 0 and it wrote no
    inspect_modes). Re-deriving them from its defect ledger would reproduce 22
    today by coincidence and silently drift the moment a record moved.

    NFR-001: "Numbers are the target, not a gate." Nothing here feeds
    ``_compute_gate_verdicts`` or ``_exit_status`` — missing the target is
    reported, never refused. `meets_target` is None for a number this archive
    could not supply, because "did not meet" and "cannot say" are different
    answers.
    """
    target_cycles = CONVERGENCE_TARGET["grind_cycles"]
    target_post = CONVERGENCE_TARGET["post_verification_cycles"]
    return {
        "baseline": dict(THUNDER_VIPER_BASELINE),
        "target": dict(CONVERGENCE_TARGET),
        "current": {
            "run": run_dir.name,
            "grind_cycles": cycles,
            "post_verification_cycles": post_verification_cycles,
        },
        "meets_target": {
            "grind_cycles": cycles <= target_cycles,
            "post_verification_cycles": (
                None if post_verification_cycles is None
                else post_verification_cycles <= target_post
            ),
        },
    }


def _read_context_pct(
    run_dir: Path, strict: bool, override: float | None = None
) -> tuple[float | None, list[str]]:
    """Read the F2 context percentage, or take the operator's measurement.

    No run writes context-at-f2.txt — the F2 context percentage is observed by
    the lead at runtime, not persisted in the archive — so without an input path
    this gate is structurally incapable of ever being anything but MISSING.
    ``--context-pct`` supplies the measurement, exactly as ``--baseline-seconds``
    supplies the wall-clock baseline; an explicit value wins over the file.
    """
    if override is not None:
        return override, []
    miss = ["PHASE9_CONTEXT_FILE_MISSING"] if strict else []
    path = run_dir / "context-at-f2.txt"
    if not path.exists():
        return None, miss
    raw, problem = read_text_file(path)
    if problem is not None:
        return None, miss
    text = raw.strip()
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


def _exit_status(gate_verdicts: dict[str, str], failure_tokens: list[str]) -> int:
    """The process status the module docstring's exit contract promises.

    D-105: the contract names two halves — "1 on token rejection / gate FAIL" —
    and only the first was implemented. Gate verdicts were computed, rolled up,
    printed, and then dropped: _emit_per_run and _emit_matrix derived status
    from failure_tokens alone and _emit_evaluate_gates returned 0
    unconditionally, printing overall_verdict FAIL on its way out. On the
    canonical baseline archive (grand-vulture, NFR-001's "18 cycles, 168
    defects") two hard gates FAIL and the process exited 0.

    The asymmetry is what made it decisive rather than cosmetic: the tool DID
    exit 1 for a purely informational token — an archive with no handoffs.jsonl
    emits PHASE9_WALL_CLOCK_UNAVAILABLE and exits 1 with every gate PASS. It
    failed loud on "could not measure the wall clock" and stayed silent on "the
    convergence gate FAILED". NFR-001 makes this the effort's acceptance
    instrument, and a gate whose verdict never reaches its exit status is not a
    gate.

    MISSING maps to 0 deliberately: an unmeasured gate is honest, not failed.
    Two of the four gates read measurements no archive holds (f2_context_pct,
    wall_clock_regression_pct — see _read_context_pct), so MISSING is the
    ordinary state of any run measured without --context-pct /
    --baseline-seconds. Mapping it to 1 would fail nearly every real run, which
    is the over-firing calibration D-034 already had to undo. Only FAIL — a
    gate that WAS measured and was blown — is a nonzero status.
    """
    if failure_tokens:
        return 1
    return 1 if _overall_verdict(gate_verdicts) == "FAIL" else 0


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


def _extract_per_run(
    run_dir: Path,
    strict: bool,
    baseline_wall_clock: float | None = None,
    context_pct_override: float | None = None,
) -> MeasureResult:
    r = MeasureResult()
    failure_tokens: list[str] = []
    if not run_dir.exists() or not run_dir.is_dir():
        r.failure_tokens = ["PHASE9_RUN_DIR_INVALID"]; return r
    cohort_id, lever, cf = _read_cohort_json(run_dir, strict)
    r.cohort_id, r.disable_lever = cohort_id or "", lever; failure_tokens.extend(cf)
    wall_clock, wf = _read_handoffs_wall_clock(run_dir)
    r.wall_clock_seconds = wall_clock; failure_tokens.extend(wf)
    coverage, rollup_highest, rf = _read_stream_rollup(run_dir)
    r.per_cycle_coverage = coverage; failure_tokens.extend(rf)
    recorded, cycf = _read_state_cycle_count(run_dir)
    failure_tokens.extend(cycf)
    final_index, recf = _reconcile_final_cycle_index(recorded, rollup_highest)
    # INDEX -> COUNT, converted exactly once. The server's counter is 0-based
    # (see _reconcile_final_cycle_index), so a run that executed N cycles ends
    # at index N-1. ``cycles`` is named as a COUNT and its gate reads as one,
    # so publishing the raw index reported every run one cycle short: on
    # grand-vulture — whose defects span 18 distinct cycles and whose defect
    # total the same command reads as exactly 168 — it printed 17 against
    # NFR-001's baseline sentence "18 cycles, 168 defects", and the convergence
    # gate admitted one more cycle than MAX_CYCLES_FOR_CONVERGENCE names.
    r.cycles = final_index + 1; failure_tokens.extend(recf)
    per_stream, df = _read_defects_per_stream(run_dir)
    r.per_stream_defects = per_stream; failure_tokens.extend(df)
    # The NFR-001 / AC-039 columns. None of them contributes a failure token or
    # a gate verdict: they are the convergence REPORT, and NFR-001 states in as
    # many words that the numbers are the target, not a gate.
    r.defects_by_tier = _read_defects_by_tier(run_dir)
    r.spend = _read_spend(run_dir)
    r.inspect_modes = _read_inspect_modes(run_dir)
    r.escalation = _read_escalation(run_dir)
    r.baseline_comparison = _baseline_comparison(
        run_dir,
        r.cycles,
        None if r.inspect_modes is None else r.inspect_modes["post_verification_cycles"],
    )
    context_pct, ctxf = _read_context_pct(run_dir, strict, context_pct_override)
    r.f2_context_pct = context_pct; failure_tokens.extend(ctxf)
    if baseline_wall_clock is not None and baseline_wall_clock > 0:
        r.wall_clock_regression_pct = (wall_clock / baseline_wall_clock - 1.0) * 100.0
    elif baseline_wall_clock == 0:
        r.wall_clock_regression_pct = 0.0
    r.gate_verdicts = _compute_gate_verdicts(
        cycles=r.cycles, per_stream_defects=per_stream,
        f2_context_pct=context_pct,
        wall_clock_regression_pct=r.wall_clock_regression_pct,
    )
    r.failure_tokens = failure_tokens
    return r


def _emit_per_run(
    run_dir: Path,
    strict: bool,
    baseline_wall_clock: float | None = None,
    context_pct_override: float | None = None,
) -> int:
    result = _extract_per_run(
        run_dir,
        strict=strict,
        baseline_wall_clock=baseline_wall_clock,
        context_pct_override=context_pct_override,
    )
    sys.stdout.write(json.dumps(result.to_json_dict(), indent=2) + "\n")
    return _exit_status(result.gate_verdicts, result.failure_tokens)


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
    # D-106: the roster filter drops every subdir not named after one of the
    # ten cohort ids. Dropping them SILENTLY meant a directory matching none of
    # them produced an empty table and exit 0 — a clean bill of health for a
    # completely unmeasured directory, and the ordinary mistake (archive root
    # instead of runs dir, or arms named otherwise) was the only input that
    # reported success while --matrix <a file> and --matrix <nonexistent> were
    # both correctly refused. What is ignored is now counted and said aloud.
    subdirs = sorted(s.name for s in runs_dir.iterdir() if s.is_dir())
    cohorts = [name for name in subdirs if name in KNOWN_PHASE9_COHORT_IDS]
    skipped = [name for name in subdirs if name not in KNOWN_PHASE9_COHORT_IDS]
    if skipped:
        # Advisory, NOT a PHASE9_* token: measuring ten arms beside a docs/
        # directory is ordinary, so this must not become a refusal. In this
        # file a token always IS a refusal that reaches the exit status; giving
        # one a second, softer meaning here would break that. Lowercase note.
        sys.stderr.write(
            f"note: {len(skipped)} non-cohort subdirector"
            f"{'y' if len(skipped) == 1 else 'ies'} ignored: {', '.join(skipped)}\n"
        )
    if not cohorts:
        sys.stderr.write(
            f"PHASE9_NO_COHORTS: {runs_dir} holds no cohort run directory "
            f"({len(skipped)} subdirector"
            f"{'y' if len(skipped) == 1 else 'ies'} ignored). Expected at least "
            f"one of: {', '.join(sorted(KNOWN_PHASE9_COHORT_IDS))}\n"
        )
        return 1
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
    # ``results`` is non-empty — the roster guard above returns before here —
    # so max() needs no default. That default WAS the silent pass D-106 names:
    # any(...) over an empty list is False, which read as "nothing failed".
    return max(_exit_status(r.gate_verdicts, r.failure_tokens) for r in results)


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
    # Reuses the shared rollup rather than re-deriving it. Every gate here is
    # operator-supplied, so MISSING cannot arise and the two expressions were
    # provably identical — but keeping a second copy of the policy beside the
    # site that forgot to apply it is how D-105 happened in the first place.
    overall = _overall_verdict(verdicts)
    sys.stdout.write(json.dumps({"overall_verdict": overall, "gate_verdicts": verdicts}) + "\n")
    return _exit_status(verdicts, [])


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
    # --baseline-seconds and --context-pct carry the two measurements no
    # archive holds. Supplying either turns its gate from MISSING into a real
    # verdict; omitting both leaves the honest MISSING.
    return _emit_per_run(
        args.run_dir,
        strict=args.strict,
        baseline_wall_clock=args.baseline_seconds,
        context_pct_override=args.context_pct,
    )


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
