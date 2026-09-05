#!/usr/bin/env python3
"""FR-021 / NFR-003 — migrate-archive.py.

Upgrades ONE old ``foundry-archive/{run}/`` directory, in place, to the run
schemas this release introduces. Stdlib only; no runtime deps beyond the
canonical vocabulary module, which is itself stdlib-only.

Twelve steps, each independently guarded on absence so the whole tool is
idempotent (NFR-003) even against a hand-edited or half-migrated archive:

  1. defects.json     — every record gains ``class: null`` and
                        ``classification: "DEFECT"`` when absent
  2. defects.json     — every record gains the evidence-tier fields this
                        release adds: ``tier: "unknown"`` (FR-051 — NEVER
                        LATENT) plus the four companions Foundry-Fix and the
                        LATENT door later populate. Shares step 1's single
                        read/modify/write of the file
  3. observations.json — created as {"observations": []} when absent
  4. stream-rollup.json — the per-cycle roll-up, re-derived from the
                        archive's own data in the shape its consumer reads
  5. progress/        — per-agent progress-ledger directory, created empty
  6. state.json       — ``cycle`` repaired from the run's own data when the
                        recorded value is missing/invalid/too low. Reads the
                        SAME evidence step 4 keys the roll-up on, so a
                        migrated archive can never sit behind its own roll-up
  7. state.json       — ``archive_schema_version`` marker recorded

SCHEMA 4 ADDS FIVE MORE (FR-028 / FR-054 / CT-018). One per independent schema
change, which is the register's own rule and not a preference: steps 1 and 2
already share one file and report two outcomes, because an operator repairing a
half-migrated archive needs to read which change landed and not merely that
something did. All five are guarded on absence exactly as the seven above:

  8. stream-rollup.json — every (cycle, stream) bucket's top-level totals
                        rewritten to its LAST ``records[]`` entry, with every
                        earlier record KEPT (AC-034 / OT-032). The additive
                        writer accumulated those totals, so nine daring-orca
                        rows read above 100% — cycle 29's trace at 1084/542
                        over two records of 542/542 is the row the acceptance
                        criterion names. Cycle-level facts are not buckets and
                        are not touched
  9. defects.json     — every record gains ``fallout_of: null`` and
                        ``supersedes: null``. Shares steps 1-2's single
                        read/modify/write of the file
 10. castings/manifest.json — every casting gains ``requirement_ids``, and
                        ``split_reason`` where its ownership is shared
 11. concerns.json    — created as {"concerns": []} when absent
 12. rosters/         — per-stream roster directory, created empty

WHY STEP 10 TRANSCRIBES AND DOES NOT INVENT. `foundry_validate`'s F0.9 door
reads a MISSING ``requirement_ids`` as "this archive predates the field" only
while the schema marker is below 4 — and step 7 raises it to 4. So leaving the
field absent, or filling it ``[]``, turns one informational line into an error
per casting: absent reads as un-migrated at a schema that says otherwise, and
``[]`` is a casting positively CLAIMING it owns nothing, which the door checks
against the casting's own prose and refuses. Neither is honest and neither is
what "fills defaults" can mean here.

What the archive already states IS its ownership: each casting entry carries the
verbatim ``<spec_requirements>`` block its prompt was built from, and
``foundry_handoff.declared_requirement_ids`` is the ONE derivation in the tree of
which ids a block declares — the same function the F0.9 door itself uses to
decide what a casting declared (D-180). Step 10 transcribes that answer into the
field. It reads ownership out of the archive; it does not compute one.

``split_reason`` rides with it for the same reason and in the same breath, which
is why CT-018 names the pair. A pre-field manifest was decomposed with no span
rule in force, so ids DO span more than two castings — thirteen of daring-orca's
do — and the door refuses a span above two that no reason names. The reason
recorded is the true one and is the same for every such id: the split predates
the field and was not a decision anyone took at decompose time. Recording it is
what makes the span REVIEWABLE rather than either silently exempt or wrongly
refused.

WHAT THIS TOOL DOES **NOT** CREATE. escalation.json, spend.jsonl and
state.json's ``inspect_modes`` are artifacts of a run that executed under this
release. A pre-change archive did not produce them, and writing an empty one
would turn "never measured" into "measured zero" — the lie measure-run.py's
structurally-missing columns exist to avoid. Their absence is reported as
missing, never as a zero.

An empty ``concerns.json`` (step 11) and an empty ``rosters/`` (step 12) are
NOT that lie, and the difference is what each document asserts. A spend ledger
of zero rows would assert the run cost nothing; an empty concern ledger asserts
that no concern was RECORDED, which is exactly true of an archive written before
`Foundry-Concern` existed, and an empty roster directory asserts that no roster
was persisted, likewise. Both are the shapes their readers already expect for
"nothing here" — ``read_concerns`` over a missing file and one holding ``[]``
answer the same question the same way — so creating them adds a container and
claims no measurement. Step 12 creates the DIRECTORY and never a
``rosters/<stream>.json``: a roster file holding zero items WOULD be the lie,
because it asserts a stream derived a roster and found nothing in it.

``fallout_of: null`` (step 9) is the same distinction one field down. The KEY's
presence is what `foundry_state.fallout_rows` counts as measured, and null is
the value a filing carries when the stream declares no parent — the honest
reading of a record filed as a standalone defect. It is stamped for the reason
the four tier companions in step 2 are stamped: a migrated record and a fresh
one then carry the same key set, and every reader can use one shape.

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
        TIER_UNKNOWN,
        WIRE_TO_CANONICAL,
        canonical_stream_id,
        defect_tier,
    )
    from foundry_mcp.tools.foundry_handoff import declared_requirement_ids
    from foundry_mcp.tools.foundry_state import (
        derive_cycle_count,
        is_stream_record,
        read_json,
        read_text_file,
    )
except ModuleNotFoundError:  # Dev / non-installed checkout — add src/ to path.
    _SRC = Path(__file__).resolve().parents[1] / "mcp-server" / "src"
    if _SRC.is_dir() and str(_SRC) not in sys.path:
        sys.path.insert(0, str(_SRC))
    from foundry_mcp.schemas.vocab import (
        STREAM_WIRE_IDS,
        TIER_UNKNOWN,
        WIRE_TO_CANONICAL,
        canonical_stream_id,
        defect_tier,
    )
    from foundry_mcp.tools.foundry_handoff import declared_requirement_ids
    from foundry_mcp.tools.foundry_state import (
        derive_cycle_count,
        is_stream_record,
        read_json,
        read_text_file,
    )


# The schema generation this tool brings an archive to. Bump when a migration
# step is added (add it to MIGRATION_STEPS below) or when a step's OUTPUT shape
# changes — v1 wrote a stream-rollup.json its own consumer could not read.
#
# v3: the evidence-tier step. A pre-change defect record has no `tier` key, and
# every gate in this release branches on one.
#
# v4: replace semantics for the roll-up, and the fields this release adds to a
# defect record and a casting entry. The OUTPUT shape of step 4 is unchanged;
# what changed is what step 8 then makes the top-level totals MEAN, which is a
# generation an archive has to be able to declare — `foundry_validate` reads
# this marker to tell an archive that predates `requirement_ids` from a run
# created under the schema that mandates it.
#
# BUMPING THIS ALONE IS NOT ENOUGH. Since D-052 this tool is no longer the only
# writer of `state.json`'s marker: `foundry_init` stamps a run with the current
# generation at creation, because the fail-closed half of FR-054 fires only on
# runs that can say what they are. So the number lives in more than one file,
# and a bump applied here and nowhere else leaves every newly created run
# claiming the previous generation — silently, since nothing about such a run
# looks wrong until the F0.9 door goes quiet on it. The join that catches that
# lives in `plugins/foundry/mcp-server/tests/test_migrate_archive.py`, under
# `test_the_two_writers_of_the_schema_marker_agree_and_the_reader_accepts_it`.
# It drives both writers and asks the reader rather than comparing constants,
# so run it with the bump.
ARCHIVE_SCHEMA_VERSION = 4

# CLOSED VOCABULARY — the twelve migration steps, in execution order. The
# summary reports one outcome per step under exactly these names.
# Extend only via phase-level RFC.
MIGRATION_STEPS = (
    "defects",
    "defect_tier",
    "observations",
    "stream_rollup",
    "progress",
    "state_cycle",
    "archive_schema_version",
    # Schema 4 (FR-028 / FR-054 / CT-018).
    "rollup_totals",
    "defect_fallout",
    "casting_ownership",
    "concerns",
    "rosters",
)  # 12 steps

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
# Steps 1 + 2 — defects.json gains class/classification, then the tier fields.
#
# Two steps, one read/modify/write. Mirrors _migrate_state, which reports the
# cycle repair and the version marker separately while touching state.json
# once: two independent schema changes to the same file is a shape this tool
# already has, and splitting the WRITE would double the window in which an
# interrupted run leaves a half-migrated ledger.
# ---------------------------------------------------------------------------


# The evidence-tier fields this release adds to a defect record, and the value
# a PRE-CHANGE record takes for each.
#
# `tier` is the load-bearing one and the value is NOT negotiable: FR-051 says a
# record with no tier reads as unknown and NEVER as LATENT. The distinction is
# not cosmetic — LATENT blocks no gate at all (FR-006 passes INSPECT-clean,
# ASSAY, TEMPER, NYQUIST and DONE alike on a LATENT-only backlog), so migrating
# 162 unclassified thunder-viper records to LATENT would silently clear every
# one of them on defects no stream ever looked at. TIER_UNKNOWN is imported
# rather than spelled here so the sentinel has exactly one definition (the same
# discipline that made the vocabulary module exist).
#
# The other four are `null` because that is what Foundry-Fix and the LATENT
# door write before they are populated — a migrated record and a fresh one then
# carry the same key set, which is what lets every reader use one shape.
# Extend only via phase-level RFC.
DEFECT_TIER_FIELDS: tuple[tuple[str, Any], ...] = (
    ("tier", TIER_UNKNOWN),
    ("reproduction_attempted", None),
    ("regression_test", None),
    ("authored_by", None),
    ("fix_commit", None),
)  # 5 fields

# Step 9 — the two schema-4 fields a defect record gains (FR-025 / A-023), and
# the value a PRE-CHANGE record takes for each.
#
# Both are `null` for the reason the four companions above are: it is what the
# filing doors write when the stream declares neither, so a migrated record and
# a fresh one carry the same key set and every reader uses one shape.
#
# `fallout_of` names the earlier defect a filing is fallout OF. Its presence as
# a KEY is what `foundry_state.fallout_rows` counts as measured, which is why it
# is stamped rather than left absent — the acceptance figure NFR-006 states is
# defined over cycles, and a cycle holding one un-keyed record cannot be
# measured at all. Null is the honest value: the record WAS filed as a
# standalone defect, and null is exactly what a stream filing a standalone
# defect writes today.
#
# `supersedes` names the HARDENING record a new filing retires (ST-006). It is
# never re-tiered in place, so an archived record that superseded nothing says
# so.
# Extend only via phase-level RFC.
DEFECT_FALLOUT_FIELDS: tuple[tuple[str, Any], ...] = (
    ("fallout_of", None),
    ("supersedes", None),
)  # 2 fields


def _add_missing(record: dict[str, Any], fields: tuple[tuple[str, Any], ...]) -> bool:
    """Set each absent field to its migration default. True when any was added.

    Absence-guarded per field rather than per record, so a half-migrated
    ledger (NFR-003) converges instead of being skipped or overwritten. An
    EXISTING key is never touched, including an existing ``tier: null`` — see
    the note in ``_migrate_defect_tier``.
    """
    touched = False
    for name, default in fields:
        if name not in record:
            record[name] = default
            touched = True
    return touched


def _migrate_defects_document(run_dir: Path) -> tuple[Path, dict[str, Any] | None]:
    """(path, document) for defects.json, or (path, None) when it is absent."""
    path = run_dir / "defects.json"
    if not path.exists():
        return path, None
    data = _load_json(path)
    if not isinstance(data, dict) or not isinstance(data.get("defects"), list):
        raise _Malformed("MIGRATE_DEFECTS_FILE_MALFORMED")
    for record in data["defects"]:
        if not isinstance(record, dict):
            raise _Malformed("MIGRATE_DEFECTS_FILE_MALFORMED")
    return path, data


def _migrate_defects(
    run_dir: Path, dry_run: bool
) -> tuple[tuple[str, dict[str, Any]], ...]:
    """Steps 1, 2 and 9 over one read/modify/write of defects.json.

    Returns one ``(outcome, detail)`` pair per step, in step order. Three
    independent schema changes to one file share one write for the reason steps
    1 and 2 already did: splitting it would triple the window in which an
    interrupted run leaves a half-migrated ledger, and the ledger is the one
    artifact AC-026 forbids losing data from.
    """
    path, data = _migrate_defects_document(run_dir)
    if data is None:
        absent = {"reason": "defects.json absent"}
        return (("no-op", absent), ("no-op", dict(absent)), ("no-op", dict(absent)))

    records: list[dict[str, Any]] = data["defects"]
    class_changed = 0
    for record in records:
        if _add_missing(
            record,
            # Optional stream-declared class field (escalation keys on it), and
            # the DEFECT / OBSERVATION classification axis. Everything already
            # in a defect ledger was filed as a defect; migration never
            # reclassifies.
            (("class", None), ("classification", "DEFECT")),
        ):
            class_changed += 1

    tier_changed, tier_unknown = _migrate_defect_tier(records)
    fallout_changed, fallout_named = _migrate_defect_fallout(records)

    if class_changed or tier_changed or fallout_changed:
        if not dry_run:
            _save_json(path, data)

    return (
        (
            "upgraded" if class_changed else "no-op",
            {"records": len(records), "upgraded": class_changed},
        ),
        (
            "upgraded" if tier_changed else "no-op",
            {
                "records": len(records),
                "upgraded": tier_changed,
                "tier_unknown": tier_unknown,
                "fields": [name for name, _ in DEFECT_TIER_FIELDS],
            },
        ),
        (
            "upgraded" if fallout_changed else "no-op",
            {
                "records": len(records),
                "upgraded": fallout_changed,
                "fallout_named": fallout_named,
                "fields": [name for name, _ in DEFECT_FALLOUT_FIELDS],
            },
        ),
    )


def _migrate_defect_fallout(records: list[dict[str, Any]]) -> tuple[int, int]:
    """Step 9 — stamp `fallout_of` and `supersedes`. Returns (changed, named).

    ``named`` counts every record that NAMES a parent afterwards, not the ones
    this run stamped, for the reason step 2 counts unknown tiers the same way:
    an archive migrated once and measured later must report the same number, and
    a record that already carried a real ``fallout_of`` is left exactly as found
    while still counting. On a pre-change ledger it is always 0, and that is the
    honest reading — nothing in the archive claims a parent — as against the
    per-cycle census `measure-run.py` publishes, where a cycle whose records all
    read null is a MEASURED zero precisely because this step ran.

    An existing value is never touched, including an existing ``fallout_of:
    null``. `_add_missing` is per FIELD, so a ledger half-stamped by an
    interrupted run converges on the next pass rather than being skipped whole
    or overwritten whole.
    """
    changed = 0
    named = 0
    for record in records:
        if _add_missing(record, DEFECT_FALLOUT_FIELDS):
            changed += 1
        parent = record.get("fallout_of")
        if isinstance(parent, str) and parent.strip():
            named += 1
    return changed, named


def _migrate_defect_tier(records: list[dict[str, Any]]) -> tuple[int, int]:
    """Step 2 — stamp the evidence-tier fields. Returns (changed, unknown).

    ``unknown`` counts every record that READS as unknown-tier afterwards, not
    just the ones this run stamped: an archive migrated once and measured later
    must report the same number, and a record that already carried
    ``tier: null`` (an interrupted write, or a hand edit) is left exactly as
    found while still counting as unknown. That is the FR-051 rule stated once
    — absence and null both read as unknown, and neither is ever LATENT.

    The count goes through ``vocab.defect_tier`` rather than an inline test,
    so this tool and every gate agree on what "unknown" means. A hand-edited
    ``tier: "MINOR"`` is unknown to the gates; an inline ``isinstance(str)``
    check here would have called it classified.
    """
    changed = 0
    unknown = 0
    for record in records:
        if _add_missing(record, DEFECT_TIER_FIELDS):
            changed += 1
        if defect_tier(record) == TIER_UNKNOWN:
            unknown += 1
    return changed, unknown


# ---------------------------------------------------------------------------
# Step 3 — observations.json ledger.
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
# Step 4 — stream-rollup.json, re-derived from the archive's own data.
# ---------------------------------------------------------------------------


# The roll-up is READ BACK by orchestration.streams._rollup_totals, which looks
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
    whitespace-separated — orchestration.streams._marker_counts reads only
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
    """True when the stored roll-up holds NOTHING a current reader can read.

    v1 of this tool wrote ``cycles[c][<CANONICAL>] = <int>``. Those documents
    are inert — ``_rollup_totals`` requires a dict and reads an int back as "no
    record for this cycle" — and must be re-derived, or an archive "migrated"
    by v1 keeps a roll-up its consumer silently ignores.

    Anything else is left exactly as found. The archive's own data cannot
    reconstruct what the server observed, so a rebuild over server-written data
    is data loss, and this predicate is the only thing standing between the two.

    D-188 / NFR-003 — A CYCLE BUCKET IS NOT ALL STREAMS, AND HAS NOT BEEN
    SINCE C-6. THIS IS THE FOURTH WALKER TO LEARN THAT.
    ---------------------------------------------------------------------
    The old rule was ``any(not isinstance(entry, dict) for entry in
    bucket.values())`` — every key in a cycle bucket assumed to be a stream, and
    the VALUE SHAPE hand-tested. That was true of the document when it was
    written and stopped being true when the spec's Data Model widened it:
    ``_record_cycle_rollup`` writes ``inspect_mode`` and ``inspect_rule`` as
    STRINGS into the same mapping as the stream tranches, so the two NEWEST
    fields in the document read as the OLDEST shape the tool knows.

    Driven end to end on a byte copy of this run's own archive: ``migrate-
    archive.py <copy>`` exited 0 reporting step ``stream_rollup`` outcome
    ``upgraded`` and re-derived the roll-up from markers. 45 keys were lost,
    including every ``inspect_mode`` / ``inspect_rule`` / ``stream_scope`` /
    ``evidence_sweep`` entry for cycles 10-12 — the exact per-cycle record
    GI-008 requires the transition to write and FR-023/AC-036 require the
    report's full-vs-delta section to be derivable from — plus whole stream
    entries in eleven of thirteen cycles. Cycle 11's PROVE went from
    ``{items_checked 172, items_total 172, findings 2, records [one audit
    row]}`` to ``{items_checked 0, items_total 0, findings 2, records []}``.
    ``/foundry:resume`` runs this tool on EVERY resume, so the destruction was
    one resume away from any run under this release.

    THE VALUE TEST IS SCOPED BY THE KEY, AND PRESERVING DOMINATES
    ------------------------------------------------------------
    Two rules, in this order:

      * A key the stream roster does not resolve is NOT a stream, so its value
        shape says nothing about the document's generation. That is exactly
        ``measure-run.py._read_stream_rollup``'s structure (D-182) — resolve the
        key, then test the value with the ONE definition in
        ``foundry_state.is_stream_record``. A denylist of the C-6 key NAMES was
        rejected for the reason stated there: ``temper_entry`` is already a
        fifth and the next C-6 field would be a sixth.
      * ANY evidence the server wrote this document VETOES the rebuild, wherever
        in the document it sits: a tranche carrying ``records``, or a key the
        roster does not know. Scoping the value test alone (the smaller fix)
        would still rebuild a MIXED document — a v1-migrated archive RESUMED
        under this release, whose old cycles are inert ints while its new ones
        carry the server's audit trail and cycle facts. That shape is reachable
        precisely because this tool exists to make old archives resumable, and
        rebuilding it destroys the half that cannot be re-derived to repair the
        half that can. The docstring above has always called that data loss;
        this is the veto that makes the claim true rather than aspirational.

    So an unknown key is read as "a later writer put something here I do not
    understand", never as "this is an old document" — which is what A-044's
    additive-compatibility regime demands of any second reader of a widened
    document, and what the absence of that reading cost here.

    Pure and total: takes the decoded JSON and returns False rather than raising
    on any type.
    """
    if not isinstance(existing, dict):
        return True
    cycles = existing.get("cycles")
    if not isinstance(cycles, dict):
        return True

    # Order-independent by construction: a veto returns immediately and is the
    # answer wherever it was found, so the verdict cannot depend on dict order.
    inert = False
    for bucket in cycles.values():
        if not isinstance(bucket, dict):
            # Not the documented shape at all, and nothing in it to preserve.
            inert = True
            continue
        for key, entry in bucket.items():
            if is_stream_record(entry):
                return False  # server-written or already re-derived
            if not isinstance(key, str) or canonical_stream_id(key) is None:
                return False  # a cycle-level fact, not a stream — preserve it
            inert = True
    return inert


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
        return "no-op", {
            # D-188: "readable" was the old, narrower claim. A no-op here now
            # also covers a document carrying server-written cycle facts a
            # rebuild could not reconstruct — see _rollup_needs_rebuild.
            "reason": "stream-rollup.json carries data no rebuild could reconstruct"
        }
    outcome = "upgraded" if path.exists() else "created"
    document, non_stream = _derive_stream_rollup(run_dir)
    if not dry_run:
        _save_json(path, document)
    return outcome, _rollup_detail(document, non_stream)


# ---------------------------------------------------------------------------
# Step 8 — replace semantics: the top-level totals ARE the last record.
# ---------------------------------------------------------------------------


#: The three totals a (cycle, stream) bucket carries beside its ``records[]``.
#: Derived nowhere else and listed here rather than beside ROLLUP_ENTRY_KEYS
#: because that constant includes ``records`` itself, which is the one key this
#: step must never rewrite.
#: Extend only via phase-level RFC.
ROLLUP_TOTAL_KEYS = ("items_checked", "items_total", "findings")  # 3 keys


def _last_record_totals(entry: dict[str, Any]) -> dict[str, int] | None:
    """The LAST ``records[]`` entry's totals, or None when there are none.

    ONLY the keys that record actually states are returned. A record carrying
    ``items_checked`` and no ``findings`` rewrites the first and leaves the
    second exactly as found — a rewrite is a transcription of what the last
    record says, and inventing a 0 for a key it does not mention would be this
    tool asserting a measurement in the one step whose whole purpose is to stop
    the totals asserting one.

    Total over any shape: a ``records`` that is not a list, is empty, or whose
    last element is not a mapping all read as "no last record".
    """
    records = entry.get("records")
    if not isinstance(records, list) or not records:
        return None
    last = records[-1]
    if not isinstance(last, dict):
        return None
    return {
        key: last[key]
        for key in ROLLUP_TOTAL_KEYS
        if isinstance(last.get(key), int) and not isinstance(last.get(key), bool)
    }


def _migrate_rollup_totals(run_dir: Path, dry_run: bool) -> tuple[str, dict[str, Any]]:
    """Step 8 — AC-034 / OT-032 / GI-006. Totals become the LAST record's.

    THE OLD WRITER ADDED AND THE NEW ONE REPLACES, AND NINE ROWS ARE THE PROOF.
    ---------------------------------------------------------------------------
    `Foundry-Stream` used to append a record AND accumulate the top-level
    totals, so a stream that recorded twice in one cycle read as having checked
    twice as many items as exist. Nine daring-orca (cycle, stream) rows read
    above 100% because of it, and the one AC-034 and OT-032 name is cycle 29's
    ``trace``: ``1084/542`` over two records that each say ``542/542``. The run
    checked 542 items; the document said it checked 1084.

    GI-006 IS THE CONSTRAINT ON THE REPAIR, not a caveat to it. Its named
    violation is "a replace-semantics write that drops history", so every
    earlier record STAYS under ``records[]`` and only the totals move. The
    superseded records are the evidence that the row was recorded twice — the
    thing that makes the over-100% figure explicable rather than merely wrong —
    and `foundry_state.stream_rollup_rows` publishes ``replaced_count`` off
    exactly that list. A repair that discarded them would fix the number by
    destroying the reason for it.

    ABSENCE-GUARDED THREE WAYS, WHICH IS WHAT MAKES A SECOND RUN A NO-OP:

      * no ``stream-rollup.json`` at all — nothing to rewrite;
      * a bucket key whose value is not a stream tranche — ``inspect_mode``,
        ``inspect_rule``, ``stream_scope``, ``evidence_sweep``, ``temper_entry``
        and whatever C-6 adds next are cycle-level FACTS, resolved by value
        through the one definition in ``foundry_state.is_stream_record`` and
        never by a denylist of names (D-182 / D-188 — this is the fifth walker
        of this document and the rule has one owner);
      * a tranche with no ``records[]`` history, or one whose totals already
        equal its last record's. The first is a bucket written before the
        history existed and there is nothing to take a total FROM; the second is
        an archive this step has already run on, or one the replace-semantics
        writer wrote correctly from the start.

    The third guard is what makes the step converge rather than merely repeat:
    after one run every rewritable bucket satisfies it, so the second run
    reports ``no-op`` and writes no byte — which is the assertion NFR-010 and
    CT-018 ("none new; idempotent") both name.
    """
    path = run_dir / "stream-rollup.json"
    if not path.exists():
        return "no-op", {"reason": "stream-rollup.json absent"}
    document = _load_json(path)
    cycles = document.get("cycles") if isinstance(document, dict) else None
    if not isinstance(cycles, dict):
        # Not the documented shape. Step 4 owns the rebuild decision for that
        # case and has already run; this step does not get a second opinion.
        return "no-op", {"reason": "stream-rollup.json holds no cycles mapping"}

    rewritten: list[dict[str, Any]] = []
    for cycle_key in sorted(cycles, key=str):
        bucket = cycles[cycle_key]
        if not isinstance(bucket, dict):
            continue
        for stream in sorted(bucket, key=str):
            entry = bucket[stream]
            if not is_stream_record(entry):
                continue
            totals = _last_record_totals(entry)
            if totals is None:
                continue
            before = {key: entry.get(key) for key in totals}
            if before == totals:
                continue
            entry.update(totals)
            rewritten.append({
                "cycle": str(cycle_key),
                "stream": str(stream),
                "from": before,
                "to": dict(totals),
                "records_kept": len(entry["records"]),
            })

    if not rewritten:
        return "no-op", {
            "reason": "every tranche's totals already read its last record",
        }
    if not dry_run:
        _save_json(path, document)
    return "upgraded", {"rewritten": len(rewritten), "rows": rewritten}


# ---------------------------------------------------------------------------
# Step 10 — per-casting requirement ownership, transcribed from the archive.
# ---------------------------------------------------------------------------


#: The reason recorded against a shared requirement id by step 10. It is one
#: sentence and it is the same for every id, because the fact it records is the
#: same for every id: the manifest was decomposed before the span rule existed,
#: so no reason was taken at the time and none can be invented now. F0.9 prints
#: it beside the span rather than merely honouring it — "a waiver nobody reads
#: is a waiver nobody reviews" — which is why it says where the ownership came
#: from and not just that it is exempt.
SPLIT_REASON_MIGRATED = (
    "Ownership transcribed by scripts/migrate-archive.py from this casting's "
    "own <spec_requirements> block. The manifest was decomposed before "
    "requirement_ids and the span rule existed, so the split predates the "
    "field and no reason was recorded at decompose time."
)


def _migrate_casting_ownership(
    run_dir: Path, dry_run: bool
) -> tuple[str, dict[str, Any]]:
    """Step 10 — FR-054 / AC-049. `requirement_ids` and `split_reason` filled.

    WHY THIS STEP EXISTS AT ALL, stated where the code is: step 7 raises the
    schema marker to 4, and `foundry_validate` reads a missing `requirement_ids`
    as "predates the field" only BELOW 4. Bumping the marker without filling the
    field converts one informational line into one error per casting, and CT-018
    lists the two together for that reason.

    IT TRANSCRIBES. ``declared_requirement_ids`` is the one derivation in the
    tree of which ids a `<spec_requirements>` block declares, and it is the same
    function the F0.9 door uses to decide what each casting declared. Reading
    the archive's own answer into the archive's own field is not manufacturing
    ownership; computing one would be, and the two shapes that would have to
    compute one are refused here: ``[]`` is a casting positively claiming it owns
    nothing (a claim the door checks against the prose and refuses), and a
    ``null`` reads as un-migrated at a schema that says otherwise.

    THE SPAN REASON IS RECORDED PER CASTING AND ONLY WHERE OWNERSHIP IS SHARED.
    An id owned by one casting needs no reason and gets none — a waiver on
    something nobody would refuse is noise in a table a lead reads. Sharing is
    counted across the WHOLE manifest and the reason is written into each
    sharing casting's own map, which is where `_recorded_split_reasons` reads a
    per-casting reason from and where a lead re-deciding the split would look.

    GUARDED ON ABSENCE PER FIELD PER CASTING, so a manifest half-filled by hand
    converges. An EXISTING ``split_reason`` is left whole rather than added to:
    it is somebody's recorded decision, and editing a decision is not migrating
    a schema.
    """
    path = run_dir / "castings" / "manifest.json"
    if not path.exists():
        return "no-op", {"reason": "castings/manifest.json absent"}
    manifest = _load_json(path)
    castings = manifest.get("castings") if isinstance(manifest, dict) else None
    if not isinstance(castings, list):
        return "no-op", {"reason": "castings/manifest.json holds no castings list"}

    #: casting index -> the ids its own block declares. Computed for every
    #: casting before anything is written, because the span of an id is a fact
    #: about the WHOLE manifest and a per-casting loop cannot see it.
    declared: dict[int, list[str]] = {}
    span: dict[str, int] = {}
    for index, casting in enumerate(castings):
        if not isinstance(casting, dict):
            continue
        block = casting.get("spec_text")
        ids = declared_requirement_ids(block) if isinstance(block, str) else []
        declared[index] = ids
        for rid in ids:
            span[rid] = span.get(rid, 0) + 1

    ownership_filled = 0
    reasons_filled = 0
    for index, casting in enumerate(castings):
        if index not in declared:
            continue
        ids = declared[index]
        if "requirement_ids" not in casting:
            casting["requirement_ids"] = list(ids)
            ownership_filled += 1
        if "split_reason" not in casting:
            shared = {rid: SPLIT_REASON_MIGRATED for rid in ids if span[rid] > 1}
            casting["split_reason"] = shared
            reasons_filled += 1

    if not (ownership_filled or reasons_filled):
        return "no-op", {
            "reason": "every casting already carries requirement_ids and split_reason",
            "castings": len(castings),
        }
    if not dry_run:
        _save_json(path, manifest)
    return "upgraded", {
        "castings": len(castings),
        "requirement_ids_filled": ownership_filled,
        "split_reason_filled": reasons_filled,
        "shared_requirement_ids": sorted(rid for rid, n in span.items() if n > 1),
    }


# ---------------------------------------------------------------------------
# Steps 11 + 12 — the concern ledger and the roster directory.
# ---------------------------------------------------------------------------


def _migrate_concerns(run_dir: Path, dry_run: bool) -> tuple[str, dict[str, Any]]:
    """Step 11 — CT-001's ledger, created empty when absent.

    Empty is not a fabricated measurement here, and step 3 is the precedent:
    ``{"concerns": []}`` asserts that no concern was RECORDED, which is exactly
    true of an archive written before `Foundry-Concern` existed. Its reader
    answers a missing file and an empty list the same way, so the container adds
    a shape and claims nothing — unlike `spend.jsonl`, whose empty form would
    assert a run cost nothing.

    The filename and the collection key are `concerns.py`'s, not literals: it
    spells them once and a second spelling here is a ledger two readers can
    disagree about the shape of.

    WHY THE IMPORT IS INSIDE THE FUNCTION. `concerns.py` reaches
    `tools/foundry.py` — the package's MCP-facing surface, and by a long way its
    largest module — so a module-level import here would put every one of this
    tool's twelve steps behind it. That is a real cost on a script whose header
    states a narrow one and whose whole job is repairing archives, sometimes
    from a checkout mid-change: the ten steps that need none of it should not
    stop running because a module they do not use will not load. A call-time
    import runs when the step runs, so the constants stay this module's ONE
    source for those two names and the coupling stays the width of the step
    that has it. (`foundry_state`'s package-free leaf contract is the same
    argument one layer down — it exists so this script and `measure-run.py` can
    read a derivation without buying a package.)
    """
    from foundry_mcp.tools.concerns import (
        CONCERNS_COLLECTION_KEY,
        CONCERNS_FILENAME,
    )

    path = run_dir / CONCERNS_FILENAME
    if path.exists():
        return "no-op", {"reason": f"{CONCERNS_FILENAME} already present"}
    if not dry_run:
        _save_json(path, {CONCERNS_COLLECTION_KEY: []})
    return "created", {"concerns": 0}


def _migrate_rosters(run_dir: Path, dry_run: bool) -> tuple[str, dict[str, Any]]:
    """Step 12 — CT-002's directory, created empty when absent.

    THE DIRECTORY, AND NEVER A ``rosters/<stream>.json`` INSIDE IT. An empty
    directory says no roster was persisted, which is true. A roster FILE holding
    zero items says a stream derived its item list and the list was empty — a
    measurement nobody took — and `Foundry-Roster` would then refuse the real
    derivation with ``ROSTER_EXISTS`` on the strength of it. Step 5 makes the
    same call for ``progress/`` and for the same reason.

    The directory name is `rosters.py`'s, imported at call time for the reason
    stated on the step above it.
    """
    from foundry_mcp.tools.rosters import ROSTERS_DIRNAME

    path = run_dir / ROSTERS_DIRNAME
    if path.is_dir():
        return "no-op", {"reason": f"{ROSTERS_DIRNAME}/ already present"}
    if path.exists():
        raise _Malformed("MIGRATE_WRITE_FAILED")
    if not dry_run:
        path.mkdir(parents=True)
    return "created", {"path": f"{ROSTERS_DIRNAME}/"}


# ---------------------------------------------------------------------------
# Step 5 — per-agent progress ledger directory.
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
# Steps 6 + 7 — state.json cycle repair and schema-version marker.
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


# A private "highest cycle key in the roll-up ALREADY ON DISK" helper used to
# live here — that axis is what keeps step 6's post-condition unconditional
# even for a roll-up this tool did not derive (D-060). It is gone because
# `derive_cycle_count` reads exactly that axis, and D-084 is what a SECOND
# reader of one artifact costs: keeping a private copy of the rollup rule
# beside a private copy of the defect rule is how this tool came to write an
# index `derive_cycle_count` then read as a different number.


def _observed_max_cycle(run_dir: Path) -> int:
    """The 0-based cycle INDEX the run's own data proves it reached.

    MUST consult every source step 3 keys the roll-up on. Reading only
    defects.json + verdicts.json left state.json BEHIND stream-rollup.json's
    highest key in the ORDINARY case — the last INSPECT cycle's clean streams
    file no defects, so their ``.{stream}-complete`` markers record a cycle no
    defect record carries. measure-run._reconcile_final_cycle_index then fired
    PHASE9_CYCLE_COUNT_INVALID on the archive this tool had just repaired, and
    re-running never healed it because step 5 never consulted what step 3 wrote
    (D-060). grand-vulture masked it by coincidence: all three of its markers
    carry cycle=17, equal to its max defect cycle.

    THE VALUE WRITTEN TO ``state.json["cycle"]`` IS AN INDEX (D-084)
    ----------------------------------------------------------------
    ``foundry_state.derive_cycle_count`` reads that field as the server's
    0-based counter and publishes ``index + 1`` as the cycle COUNT, so this
    tool and that reader have to agree about which of the two numbers the
    field holds. They did not. This folded ``fixed_in_cycle`` and
    ``reopened_in_cycle`` into the index while ``derive_cycle_count``'s defect
    source reads ``cycle`` alone, and thunder-viper is the archive where those
    diverge: max ``cycle`` 21, max ``fixed_in_cycle`` 22. Driven twice on
    copies — measure-run on the pristine archive printed ``cycles 22``;
    migrate then measure printed ``cycles 23``, with ``state.json["cycle"]``
    moved from 0 to 22. OT-030 requires 22, re-running the migration is a
    no-op so the corruption never healed, and no test migrated the one archive
    that has the shape.

    So the defect-ledger axis is `derive_cycle_count`'s, called rather than
    re-derived: one rule about which fields prove an index, hosted where both
    readers already reach. A fix ``cycle`` stamp is not discarded information —
    it is a stamp made by whichever door filed the fix, and the counter it
    would inflate is the one every later count is built on.

    Sources, none invented:

      * `derive_cycle_count` — defects.json ``cycle``, the roll-up's highest
        key, and the recorded counter, reconciled by the one function that
        owns that reconciliation
      * verdicts.json — ``cycle``
      * each ``.{stream}-complete`` marker — the cycle it terminates

    The marker source is what makes the derivation self-contained under
    ``--dry-run``, where step 3 writes nothing: the reported ``observed`` is
    identical to the value a real run would record.
    """
    derived = derive_cycle_count(run_dir)["index"]
    observed = derived if isinstance(derived, int) else 0
    verdicts = _load_json(run_dir / "verdicts.json")
    if isinstance(verdicts, dict):
        cycle = _as_cycle(verdicts.get("cycle"))
        if cycle is not None and cycle > observed:
            observed = cycle
    return max(observed, _marker_max_cycle(run_dir))


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
        # Steps 1, 2 and 9 — three schema changes over one read/modify/write of
        # defects.json, reported under their own three names.
        defect_steps = _migrate_defects(run_dir, dry_run)
        for name, (outcome, detail) in zip(
            ("defects", "defect_tier", "defect_fallout"), defect_steps
        ):
            steps[name] = {"outcome": outcome, **detail}
        outcome, detail = _migrate_observations(run_dir, dry_run)
        steps["observations"] = {"outcome": outcome, **detail}
        outcome, detail = _migrate_stream_rollup(run_dir, dry_run)
        steps["stream_rollup"] = {"outcome": outcome, **detail}
        # Step 8 runs AFTER step 4 and reads what step 4 may have just written.
        # A roll-up this tool re-derived carries one record per tranche at most,
        # so the rewrite finds nothing to do on it; a roll-up step 4 preserved is
        # the server-written one whose totals are exactly what AC-034 is about.
        # Ordering them the other way would leave a freshly derived document
        # unexamined for one run.
        outcome, detail = _migrate_rollup_totals(run_dir, dry_run)
        steps["rollup_totals"] = {"outcome": outcome, **detail}
        outcome, detail = _migrate_progress(run_dir, dry_run)
        steps["progress"] = {"outcome": outcome, **detail}
        outcome, detail = _migrate_casting_ownership(run_dir, dry_run)
        steps["casting_ownership"] = {"outcome": outcome, **detail}
        outcome, detail = _migrate_concerns(run_dir, dry_run)
        steps["concerns"] = {"outcome": outcome, **detail}
        outcome, detail = _migrate_rosters(run_dir, dry_run)
        steps["rosters"] = {"outcome": outcome, **detail}
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
