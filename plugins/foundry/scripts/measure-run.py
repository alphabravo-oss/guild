#!/usr/bin/env python3
"""Phase 9 / RUN-01 — measure-run.py.

Two-mode CLI: per-run JSON extractor + cohort --matrix aggregator. Mirrors
Phase 4-8 closed-vocabulary discipline (stdlib only; no runtime deps).
Three frozensets locked at module top: KNOWN_PHASE9_STREAM_IDS (15),
KNOWN_PHASE9_FAILURE_TOKENS (9), KNOWN_PHASE9_COHORT_IDS (10). Wall-clock =
first/last handoffs.jsonl timestamp delta (Pitfall 5 / 09-RESEARCH.md), null
when the ledger cannot supply two timestamps — never 0.0 (D-087).

EXIT CONTRACT: 0 OK; 1 when an artifact would not READ (the seven
UNREADABLE_ARTIFACT_TOKENS); 2 on usage error. THE NUMBERS ARE NOT A GATE
(NFR-001 / D-086) — no verdict in this payload reaches the process status, and
neither do the two tokens that name a measurement nobody took
(PHASE9_WALL_CLOCK_UNAVAILABLE, PHASE9_CONTEXT_FILE_MISSING). Every entry
point still returns through ``_exit_status``, which is D-105's structural
lesson kept while its calibration is replaced; see that function's docstring.

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

NFR-001 says the numbers are "the target, not a gate", and nothing anywhere in
this payload reaches ``_exit_status``. Missing the convergence target is
reported; it is never a refusal. The whole ``baseline_comparison`` object is
`foundry_report._baseline_comparison_section`'s — imported, not re-derived, so
this CLI and the F6 report cannot print different comparisons of the same two
archives (D-085 / D-088).

``cycles`` in the payload is a COUNT. The server's counter is 0-based, so the
count is the final index + 1 — the conversion happens exactly once, in
_extract_per_run. NFR-001 states grand-vulture's baseline as "18 cycles, 168
defects"; migrate-then-measure on a copy of that archive must print both.
"""
from __future__ import annotations
import argparse, csv, io, json, sys
from dataclasses import dataclass, field
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
        escalation_status,
        escalation_status_is_unknown,
    )
    from foundry_mcp.tools.foundry_report import _baseline_comparison_section
    from foundry_mcp.tools.foundry_state import (
        derive_cycle_count,
        handoffs_wall_clock_seconds,
        is_stream_record,
        read_json,
        read_text_file,
    )
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
        escalation_status,
        escalation_status_is_unknown,
    )
    from foundry_mcp.tools.foundry_report import _baseline_comparison_section
    from foundry_mcp.tools.foundry_state import (
        derive_cycle_count,
        handoffs_wall_clock_seconds,
        is_stream_record,
        read_json,
        read_text_file,
    )


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

# RUN-01 quantitative advisory thresholds.
#
# D-086 — THERE IS NO SECOND CYCLE THRESHOLD ANY MORE.
#
# ``MAX_CYCLES_FOR_CONVERGENCE = 8`` used to live here beside
# ``vocab.CONVERGENCE_TARGET["grind_cycles"] = 12``: two thresholds for ONE
# number, both evaluated in the same payload. Driven on a synthetic 10-cycle
# run, ``baseline_comparison.meets_target.grind_cycles`` was true (10 <= 12)
# while ``gate_verdicts.cycles`` was FAIL (10 > 8) — and the FAILING verdict
# was the one that reached the process status. An operator running the effort's
# own acceptance instrument on a run that MEETS the effort's stated target was
# told it had failed.
#
# The cycles verdict is now `_cycles_meet` against ``CONVERGENCE_TARGET``,
# which is the same comparison ``meets_target`` publishes, computed once. The
# constant is READ from vocab and never re-typed here (FR-013).
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
    # A COUNT of the cycles the run executed, not the server's 0-based index.
    # `foundry_state.derive_cycle_count` is where that conversion happens, once,
    # for this command AND for foundry_report (D-036). None means NO ledger in
    # the archive could supply a number — distinct from a measured 0, and the
    # distinction is load-bearing: `meets_target` is never True on None.
    cycles: int | None = None
    per_stream_defects: dict[str, int] = field(default_factory=dict)
    f2_context_pct: float | None = None
    # None, never 0.0, when no `handoffs.jsonl` could supply a span (D-087):
    # "took no measurable time" and "nobody measured it" are different facts,
    # and `foundry_report` has always refused to print the first for the
    # second. The two are columns of ONE comparison (NFR-001).
    wall_clock_seconds: float | None = None
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


def _read_handoffs_wall_clock(run_dir: Path) -> tuple[float | None, list[str]]:
    """The run's wall clock in seconds, or ``(None, [token])``. D-087.

    NONE, NEVER 0.0. This walked the ledger itself and published ``0.0`` for
    every run it could not measure, while `foundry_report._wall_clock_minutes`
    published ``null`` for the same input and its docstring forbade the other
    spelling by name — "a run that took no measurable time and a run nobody
    measured are different facts, and NFR-001's comparison is unreadable if
    they print the same". Both are surfaces of that one comparison, so this
    command was fabricating exactly the number the report module refuses to
    fabricate, in the one table where the two sit side by side.

    The ledger walk, the timestamp parse and the None rule are now
    `foundry_state.handoffs_wall_clock_seconds`', hosted beside
    `derive_cycle_count` for the same reason that one is hosted there (D-036):
    it is the only module both readers already import. What stays here is this
    command's own vocabulary — turning "there is no number" into the named
    PHASE9_* token its payload speaks in.
    """
    seconds, problem = handoffs_wall_clock_seconds(run_dir)
    return (None, ["PHASE9_WALL_CLOCK_UNAVAILABLE"]) if problem else (seconds, [])


def _read_cycle_count(run_dir: Path) -> tuple[int | None, list[str]]:
    """The run's cycle COUNT and the tokens its derivation earned. D-036.

    ONE derivation, and it is not this file's. `foundry_state.derive_cycle_count`
    hosts it because this script and `foundry_mcp.tools.foundry_report` both
    need it and they had grown two: this one published
    ``_reconcile_final_cycle_index + 1`` while the report published the raw
    ``state.json["cycle"]``. They differ by exactly one, which is enough to
    straddle ``CONVERGENCE_TARGET["grind_cycles"]`` — a run at index 12 met the
    effort's own target on one surface and missed it on the other.

    THE THIRD SOURCE, AND WHAT IT RESCUED (D-022)
    ---------------------------------------------
    The old pair of sources — ``state.json["cycle"]`` and the roll-up's highest
    key — were BOTH blind on thunder-viper: its counter was written once as 0
    and never incremented, and it wrote no ``stream-rollup.json`` at all. This
    command therefore reported the 22-cycle baseline as ONE cycle and printed
    ``meets_target: true`` for it — certifying the very run whose 22 cycles are
    the entire reason ``CONVERGENCE_TARGET`` exists.

    The defect ledger stamps the cycle each filing was made in, so its highest
    is a floor on the cycles a run executed, and adding it reproduces BOTH
    published baselines from their own archives: thunder-viper 21 + 1 = 22, and
    grand-vulture 17 + 1 = 18, which is NFR-001's "18 cycles, 168 defects".

    Two tokens, and only the first two conditions earn one. A `state.json` that
    cannot supply a counter at all is malformed (Test 5). A roll-up proving a
    HIGHER cycle than the counter names the stale counter (Test 23 /
    survey/data.md FI-1). A defect ledger proving more cycles does NOT: the
    roll-up is keyed BY the counter, so it is direct evidence the counter is
    stale, while a defect's `cycle` is stamped by whichever door filed it.
    Treating the ledger as an indictment would make every healthy 4.7.3-era
    archive exit nonzero, which is the over-firing calibration D-034 undid.
    """
    derived = derive_cycle_count(run_dir)
    tokens: list[str] = []
    if derived["sources"]["state_cycle"] is None:
        tokens.append("PHASE9_CYCLE_COUNT_INVALID")
    elif derived["stale_counter"]:
        tokens.append("PHASE9_CYCLE_COUNT_INVALID")
    return derived["count"], tokens


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

    D-182 — A CYCLE BUCKET IS NOT ALL STREAMS, AND HAS NOT BEEN SINCE C-6.
    ---------------------------------------------------------------------
    This walked ``bucket.items()`` treating EVERY key as a stream id, which was
    true of the document when it was written and stopped being true when the
    spec's Data Model widened it: ``inspect_mode`` and ``inspect_rule`` are
    strings and tripped the ``not isinstance(entry, dict)`` branch, while
    ``stream_scope`` and ``evidence_sweep`` are dicts whose names no stream
    roster resolves. Driven at 056f51a, this run's own archive produced eight
    failure tokens and exit 1 while thunder-viper -- which predates those keys
    -- produced none, so the acceptance instrument NFR-001 calls "the target,
    not a gate" was asserting a fault about a document that IS the documented
    shape. The numbers were right the whole time; the verdict on the artifact
    was not.

    The keys are skipped by VALUE shape through the ONE definition in
    ``foundry_state.is_stream_record`` -- never a denylist of key names, which
    would need an edit at every new C-6 field and already misses a fifth
    (``temper_entry``). See that function's comment for the full history.

    WHAT IS NOT REPUBLISHED HERE, AND WHY. The bucket's ``inspect_mode`` and
    ``inspect_rule`` now READ cleanly, and this reader still does not return
    them. ``_read_inspect_modes`` already publishes that decision into the
    ``inspect_modes`` column from ``state.json``'s append-only ``inspect_modes``
    list, which is the source C-4 declares authoritative; the roll-up copy is
    the one D-070 showed a later F5 entry can overwrite in place. A second
    derivation of one published number is the defect this fix is closing, not a
    column to add while closing it.
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
            if not isinstance(raw_stream, str):
                fts.append("PHASE9_SCHEMA_INVALID"); continue
            stream = canonical_stream_id(raw_stream)
            if not is_stream_record(entry):
                # D-182 — a cycle-level fact, not a stream. Skipped in silence,
                # because it is the document doing what its own Data Model says.
                #
                # A key the ROSTER KNOWS whose value is not a tranche is still
                # the schema fault this branch has always named: `prove` holding
                # a string is a broken record, not a cycle-level fact, and
                # filtering by value alone would have traded the old false
                # alarm for a new silence over the one case that is genuinely
                # corrupt. The key is what tells the two apart, so it is
                # resolved before the value test decides.
                if stream is not None:
                    fts.append("PHASE9_SCHEMA_INVALID")
                continue
            if stream is None:
                fts.append(f"PHASE9_UNKNOWN_STREAM:{raw_stream}"); continue
            counts: dict[str, int] = {}
            for key in ("items_checked", "items_total", "findings"):
                value = entry.get(key, 0)
                counts[key] = 0 if isinstance(value, bool) or not isinstance(value, int) else value
            coverage.setdefault(str(cycle), {})[stream] = counts
    return coverage, highest, fts


def _cycles_meet(value: int | None, limit: int) -> bool | None:
    """``value <= limit``, or None when nothing measured the value.

    D-022's rule, in one place: `meets_target` is NEVER True on a number this
    archive could not supply. "Did not meet" and "cannot say" are different
    answers, and printing the first for the second is what let this command
    certify an archive it had failed to measure.
    """
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value <= limit


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
        # orchestration/fix_gate.py foundry_sync_defects). Reading
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
    # Agent NAMES per bucket, so `agents` can be a count of the SET rather than
    # of the rows (D-090). Keyed by the same ("scope", key) pair the buckets
    # are, so the two maps cannot fall out of step.
    seen: dict[tuple[str, str], set[str]] = {}
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
        agent = entry.get("agent")

        buckets = [(("run", "total"), total)]
        if isinstance(phase, str) and phase:
            buckets.append((("by_phase", phase),
                            by_phase.setdefault(phase, _new_spend_bucket())))
        if isinstance(cycle, int) and not isinstance(cycle, bool) and cycle >= 0:
            buckets.append((("by_cycle", str(cycle)),
                            by_cycle.setdefault(str(cycle), _new_spend_bucket())))
        for scope, bucket in buckets:
            bucket["tokens"] += tokens
            bucket["duration_ms"] += duration_ms
            bucket["records"] += 1
            if isinstance(agent, str) and agent:
                seen.setdefault(scope, set()).add(agent)

    for scope, bucket in (
        *((("by_phase", k), v) for k, v in by_phase.items()),
        *((("by_cycle", k), v) for k, v in by_cycle.items()),
        (("run", "total"), total),
    ):
        bucket["minutes"] = round(bucket["duration_ms"] / 60_000.0, 2)
        bucket["agents"] = len(seen.get(scope, ()))
    return {
        "by_phase": {k: by_phase[k] for k in sorted(by_phase)},
        "by_cycle": {k: by_cycle[k] for k in sorted(by_cycle, key=_cycle_sort_key)},
        "total": total,
    }


def _new_spend_bucket() -> dict[str, Any]:
    """A spend bucket. ``records`` counts ROWS; ``agents`` counts AGENTS.

    D-090: these were one key, and it meant ROWS here and in
    `foundry_report._read_spend` while `orchestration.spend._spend_summary`
    published the same key as a count of DISTINCT agents (D-038 made it so,
    over a set, from this same ledger). One field name, two meanings, across
    three surfaces of one run — and they parted the moment any agent reported
    twice, which a re-dispatched GRIND teammate does every cycle.

    So ``agents`` means DISTINCT agents everywhere now, and the row count keeps
    its own honest name. ``distinct_agents`` is gone from ``total`` with it: it
    existed only because ``agents`` had been taken, and two spellings of one
    number in one bucket is the same defect one shape smaller.
    """
    return {"tokens": 0, "duration_ms": 0, "minutes": 0.0, "records": 0,
            "agents": 0}


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
    for decision in per_cycle.values():
        if decision["mode"] in by_mode:
            by_mode[decision["mode"]] += 1
    # COUNTED OFF `per_cycle`, so the last-entry-wins rule applies to this
    # number too. It used to be accumulated in the loop above, over EVERY entry
    # rather than the surviving one, which is a second rule for one fact:
    # a cycle whose F5 INSPECT was followed by an F2 one counted here and not
    # in `foundry_report._archive_metrics`, and the two publish this number
    # into the same NFR-001 comparison (D-088).
    return {
        "per_cycle": {k: per_cycle[k] for k in sorted(per_cycle, key=_cycle_sort_key)},
        "by_mode": by_mode,
        "post_verification_cycles": sum(
            1 for d in per_cycle.values() if d["phase"] == "F5"
        ),
    }


def _read_escalation(run_dir: Path) -> dict[str, Any] | None:
    """Escalated-class census from escalation.json (FR-028), or None.

    Reads the machine-readable exit reason FR-028 requires: a class that left
    ESCALATED did so either because it drew clean cycles or because its
    structural-pass budget ran out, and "which" is the whole point of recording
    it. A class with no `status` predates this release's fields and is counted
    as ESCALATED — the state it was in when it was written.

    EVERY CLASS IS COUNTED, IN EXACTLY ONE BUCKET (D-215)
    -----------------------------------------------------
    This loop opened `if not isinstance(entry, dict): continue`, ONE LINE above
    its own `unknown_status` counter — so the entries that counter exists for
    were the entries that never reached it. Driven on classes {"K1":
    {"status": "ESCALATED"}, "K2": "just a string", "K3": ["ESCALATED"],
    "K4": null} the CLI printed `{"classes": 4, "by_status": {"CLEARED": 0,
    "ESCALATED": 1}, "unknown_status": 0}`: three of four classes vanished off
    the census while `Foundry-Gate('done')` was blocking on all four, so the
    metric an operator reads and the gate that stops the run disagreed about
    how many classes the run even had.

    `vocab.escalation_status` is the ONE resolver in the tree now (the
    structural fix for D-214/D-215 moved it out of the orchestrator,
    which was the only one of `escalation.json`'s three readers that had it),
    and it is total over `ESCALATION_STATUSES`. So there is no `continue`, no
    shape test ahead of the resolver and no membership guard on the increment:
    `classes == sum(by_status.values())` holds for every document that parses.
    `unknown_status` is `vocab.escalation_status_is_unknown` — the entries the
    resolver had to DEFAULT (not a mapping, absent, null, empty, or a string
    outside the vocabulary) — counted beside the buckets rather than instead of
    them, so it says how many classes never declared a status without hiding
    any of them from the census.
    """
    data = _load_json(run_dir / "escalation.json")
    classes = data.get("classes") if isinstance(data, dict) else None
    if not isinstance(classes, dict):
        return None

    by_status = dict.fromkeys(sorted(ESCALATION_STATUSES), 0)
    by_exit_reason = dict.fromkeys(sorted(ESCALATION_EXIT_REASONS), 0)
    unknown_status = 0
    for entry in classes.values():
        by_status[escalation_status(entry)] += 1
        if escalation_status_is_unknown(entry):
            unknown_status += 1
        # Every OTHER field, read off the mapping or off nothing. The status is
        # decided above, on the RAW entry, so this cannot drop a class.
        fields = entry if isinstance(entry, dict) else {}
        reason = fields.get("exit_reason")
        if isinstance(reason, str) and reason in by_exit_reason:
            by_exit_reason[reason] += 1
    return {
        "classes": len(classes),
        "by_status": by_status,
        "by_exit_reason": by_exit_reason,
        "unknown_status": unknown_status,
    }


def _baseline_comparison(run_dir: Path) -> dict[str, Any]:
    """NFR-001 / AC-039 / OT-030 — this run's numbers beside thunder-viper's.

    ONE derivation, and it is not this file's (D-088). AC-039 requires this
    command to report "cycles, defects by tier, tokens and wall clock for a run
    beside the thunder-viper baseline"; only CYCLES were ever beside it. The
    other three were top-level current-run-only keys with no baseline cell at
    all, while `foundry_report._archive_metrics` derived all five for BOTH
    columns from whichever archive it was handed — so the F6 report did the
    side-by-side that the CLI named in the requirement did not.

    The whole section is therefore `foundry_report._baseline_comparison_section`'s
    now, imported rather than re-assembled. Assembling it twice is what let the
    two surfaces disagree about the floor rule (D-085) even while they agreed
    about the cycle COUNT (D-036) — a side-by-side table is exactly the surface
    where a half-unit of drift is invisible and decisive.

    THE IMPORT COSTS THIS SCRIPT'S STDLIB-ONLY CONTRACT NOTHING, on the same
    terms as `foundry_state`'s (D-141): `foundry_report` imports
    `schemas.vocab` and `tools.foundry_state` at module level and nothing else,
    and both are stdlib-only.

    D-022's floor rule is gone from here because it is gone from there. The
    recorded constant is now the baseline COLUMN unconditionally and the
    archive's own derivation is published beside it as `baseline_derived`; a
    floor that could be EXCEEDED did not protect the constant it named.
    """
    return _baseline_comparison_section(run_dir)


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


def _verdict_of(met: bool | None) -> str:
    """`_cycles_meet`'s three answers in this payload's verdict vocabulary.

    D-086: the cycles verdict and ``meets_target.grind_cycles`` are ONE
    comparison. They used to be two, against two different constants, and they
    contradicted each other in the same document. There is one comparison now
    and this is the only place it is spelled twice — as a word instead of a
    bool.
    """
    return "MISSING" if met is None else "PASS" if met else "FAIL"


def _compute_gate_verdicts(
    cycles: int | None, per_stream_defects: dict[str, int],
    f2_context_pct: float | None, wall_clock_regression_pct: float | None,
) -> dict[str, str]:
    """The ADVISORY verdict table. Nothing here reaches the process status.

    D-086 / NFR-001 verbatim: "Numbers are the target, not a gate." These
    verdicts are published for an operator to gate on; this command does not
    gate on them itself. `_exit_status` reads only whether the archive could be
    READ — see its docstring.

    D-022 — an UNMEASURED cycle count is MISSING, never PASS. The count used to
    be forced to an int, so an archive no ledger could speak for arrived here
    as 1 and reported PASS on a number nobody had. MISSING is the same verdict
    the two operator-supplied measurements use, for the same reason.
    """
    return {
        "cycles": _verdict_of(
            _cycles_meet(cycles, CONVERGENCE_TARGET["grind_cycles"])
        ),
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


#: The tokens that mean AN ARTIFACT WOULD NOT READ, as against the two that
#: mean a measurement was simply unavailable. Only the first kind is a nonzero
#: process status (D-086). Splitting the vocabulary is what lets "the archive
#: is broken" and "this run never recorded a context percentage" stop sharing
#: an exit code — the asymmetry `_exit_status` named and did not fix.
UNREADABLE_ARTIFACT_TOKENS = frozenset({
    "PHASE9_RUN_DIR_INVALID", "PHASE9_SCHEMA_INVALID",
    "PHASE9_DEFECTS_FILE_MALFORMED", "PHASE9_CYCLE_COUNT_INVALID",
    "PHASE9_UNKNOWN_STREAM", "PHASE9_UNKNOWN_COHORT", "PHASE9_NO_COHORTS",
})  # 7 of the 9; the other two are PHASE9_WALL_CLOCK_UNAVAILABLE and
# PHASE9_CONTEXT_FILE_MISSING, which name a measurement nobody took.

assert UNREADABLE_ARTIFACT_TOKENS <= KNOWN_PHASE9_FAILURE_TOKENS, sorted(
    UNREADABLE_ARTIFACT_TOKENS - KNOWN_PHASE9_FAILURE_TOKENS
)


def _exit_status(gate_verdicts: dict[str, str], failure_tokens: list[str]) -> int:
    """Nonzero only for an artifact this command could not READ. D-086.

    NFR-001 verbatim: "Numbers are the target, not a gate." This command is the
    effort's acceptance instrument and it used to gate on the numbers and exit
    nonzero, against a threshold that disagreed with the effort's own stated
    target: a 10-cycle run had ``meets_target.grind_cycles`` true (10 <= the
    CONVERGENCE_TARGET of 12) and ``gate_verdicts.cycles`` FAIL (10 > the local
    constant of 8), and the FAILING verdict was the one that reached the
    process status. An operator was told a converging run had failed.

    So verdicts are PUBLISHED and gate nothing here. The status answers one
    question — could this command read what it was pointed at — because that is
    the only thing an exit code can say that the payload cannot say better.
    Usage errors are 2 and are returned by `main` before anything is read.

    WHAT THIS KEEPS FROM D-105. That defect's real finding was an ASYMMETRY:
    the tool "failed loud on 'could not measure the wall clock' and stayed
    silent on 'the convergence gate FAILED'". D-105 closed it by making the
    quiet half loud; NFR-001 requires the other direction, so it is closed here
    by making the loud half quiet. PHASE9_WALL_CLOCK_UNAVAILABLE and
    PHASE9_CONTEXT_FILE_MISSING name a measurement nobody took — the ordinary
    state of every archive written before this release, and of every run
    measured without ``--context-pct`` — and they are reported in
    ``failure_tokens`` where an operator and a cohort matrix can both see them.
    The seven tokens that name a BROKEN artifact still exit 1: an archive that
    will not parse is not a measurement, it is a fault in the input.

    ``gate_verdicts`` is still a parameter so that every emit path keeps
    routing through this one function. D-105's structural lesson stands even
    though its calibration does not: a status derived at three call sites is
    how one of them came to forget.
    """
    del gate_verdicts  # advisory; NFR-001 forbids gating the status on it
    # Split on ``:`` first: two emitters qualify the token with the offending
    # value (``PHASE9_UNKNOWN_STREAM:FOO``), so a bare membership test would
    # read every qualified token as unknown and exit 0 on a genuinely
    # unreadable archive. The token NAME is the part before the colon.
    return 1 if any(
        t.split(":", 1)[0] in UNREADABLE_ARTIFACT_TOKENS for t in failure_tokens
    ) else 0


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
    # INDEX -> COUNT, converted exactly once and NOT here: the server's counter
    # is 0-based, so a run that executed N cycles ends at index N-1, and
    # publishing the raw index reported every run one cycle short. That
    # conversion, and the choice of which ledgers get a vote, now live in
    # `foundry_state.derive_cycle_count` so this command and `foundry_report`
    # cannot answer differently (D-036 / D-022 — see `_read_cycle_count`).
    r.cycles, cycf = _read_cycle_count(run_dir)
    failure_tokens.extend(cycf)
    per_stream, df = _read_defects_per_stream(run_dir)
    r.per_stream_defects = per_stream; failure_tokens.extend(df)
    # The NFR-001 / AC-039 columns. None of them contributes a failure token or
    # a gate verdict: they are the convergence REPORT, and NFR-001 states in as
    # many words that the numbers are the target, not a gate.
    r.defects_by_tier = _read_defects_by_tier(run_dir)
    r.spend = _read_spend(run_dir)
    r.inspect_modes = _read_inspect_modes(run_dir)
    r.escalation = _read_escalation(run_dir)
    r.baseline_comparison = _baseline_comparison(run_dir)
    context_pct, ctxf = _read_context_pct(run_dir, strict, context_pct_override)
    r.f2_context_pct = context_pct; failure_tokens.extend(ctxf)
    # An UNMEASURED wall clock cannot produce a regression percentage either.
    # It used to produce 0.0/x = -100%, which is a measurement of nothing
    # (D-087).
    if wall_clock is None:
        r.wall_clock_regression_pct = None
    elif baseline_wall_clock is not None and baseline_wall_clock > 0:
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
        # Empty, not "0.00": the same D-087 distinction the payload makes, in
        # the column a cohort matrix is read from.
        "" if result.wall_clock_seconds is None
        else f"{result.wall_clock_seconds:.2f}",
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
        # One threshold for one number (D-086): the same `_cycles_meet` against
        # the same `CONVERGENCE_TARGET` the per-run path publishes as
        # `meets_target`. This path used to read a second constant.
        "cycles": _verdict_of(
            _cycles_meet(cycles, CONVERGENCE_TARGET["grind_cycles"])
        ),
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
