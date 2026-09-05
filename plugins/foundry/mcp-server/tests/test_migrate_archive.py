"""FR-021 / NFR-003 / AC-026 / OT-012 — the shipped archive migration tool.

Subprocess-invokes ``plugins/foundry/scripts/migrate-archive.py`` the way
test_measure_run.py invokes measure-run.py (``sys.executable`` + script path),
so the tests exercise the real CLI contract including its exit codes.

Four fixtures:

  * a committed SYNTHETIC pre-change archive that exercises all seven migration
    steps. Unconditional — migration correctness and idempotency are covered
    on any checkout.
  * a committed CLEAN-LAST-CYCLE archive whose ``.{stream}-complete`` markers
    record a cycle no defect record carries — the ORDINARY shape, because the
    last INSPECT cycle's clean streams file no defects. Both the synthetic
    fixture above and grand-vulture happen to have marker cycle == max defect
    cycle, and that coincidence is what hid D-060.
  * a committed C-6 archive — one that already executed UNDER this release, so
    its stream-rollup.json carries the cycle-level facts (``inspect_mode``,
    ``inspect_rule``, ``stream_scope``, ``evidence_sweep``, ``temper_entry``)
    beside the stream tranches. D-188 is what this fixture exists to hold
    down: every step must no-op on it, and step 4 must not touch a byte.
  * the real grand-vulture archive, copied to tmp_path and migrated there.
    ``foundry-archive/`` is git-ignored, so that one test is skipif-guarded.
    The real archive is NEVER opened for writing.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

# AC-024 / GI-014 — the tier roster this module asserts migrated shapes against.
# Imported rather than re-typed for the reason the migration itself imports
# `TIER_UNKNOWN`: a sentinel or a roster with two spellings has two answers.
from foundry_mcp.schemas.vocab import DEFECT_TIER_OR_UNKNOWN

# tests/test_migrate_archive.py -> [0]=tests, [1]=mcp-server, [2]=foundry,
# [3]=plugins, [4]=repo-root. Mirrors test_measure_run.py's precedent.
REPO_ROOT = Path(__file__).resolve().parents[4]
SCRIPT = REPO_ROOT / "plugins" / "foundry" / "scripts" / "migrate-archive.py"
# D-060 is a disagreement BETWEEN the two shipped tools, so the migration's own
# detector is invoked here rather than only in test_measure_run.py.
MEASURE_SCRIPT = REPO_ROOT / "plugins" / "foundry" / "scripts" / "measure-run.py"
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "migrate_archive"
PRE_CHANGE = FIXTURES / "pre_change"
CLEAN_LAST_CYCLE = FIXTURES / "clean_last_cycle"
# D-188: an archive written by THIS release. Its roll-up is the widened
# document the rebuild detector mistook for the inert v1 shape.
C6_ROLLUP = FIXTURES / "c6_rollup"

# The real pre-change archive named by AC-026 and OT-012. git-ignored, so it
# is present in a working checkout and absent from a clean clone.
GRAND_VULTURE = REPO_ROOT / "foundry-archive" / "grand-vulture"

# v2: step 4's output shape changed — v1 wrote a stream-rollup.json that its
# own consumer (orchestration.streams._rollup_totals) could not read (D-029).
# v3: the evidence-tier step joined the list (GI-001 / FR-051).
# v4: replace semantics for the roll-up totals, `fallout_of` / `supersedes` on
# every defect record, `requirement_ids` / `split_reason` on every casting, and
# the concern ledger and roster directory (FR-028 / FR-054 / CT-018).
ARCHIVE_SCHEMA_VERSION = 4


def _invoke_migrate(*args: str) -> tuple[int, str, str]:
    """Subprocess-invoke ``migrate-archive.py {args}``."""
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True,
        text=True,
    )
    return proc.returncode, proc.stdout, proc.stderr


def _migrate(run_dir: Path, *flags: str) -> dict:
    exit_code, stdout, stderr = _invoke_migrate(str(run_dir), *flags)
    assert exit_code == 0, (exit_code, stdout, stderr)
    return json.loads(stdout)


def _tree_hash(root: Path) -> str:
    """Recursive content hash of every file in the tree, path-sensitive."""
    digest = hashlib.sha256()
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        digest.update(str(path.relative_to(root)).encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    # Directory names matter too — progress/ is created empty.
    for path in sorted(p for p in root.rglob("*") if p.is_dir()):
        digest.update(b"DIR:")
        digest.update(str(path.relative_to(root)).encode("utf-8"))
        digest.update(b"\0")
    return digest.hexdigest()


def _measure(run_dir: Path) -> dict:
    """Run the shipped detector over an archive and return its payload."""
    proc = subprocess.run(
        [sys.executable, str(MEASURE_SCRIPT), str(run_dir)],
        capture_output=True,
        text=True,
    )
    return json.loads(proc.stdout)


@pytest.fixture
def archive(tmp_path: Path) -> Path:
    """A writable copy of the synthetic pre-change archive."""
    dest = tmp_path / "synthetic-run"
    shutil.copytree(PRE_CHANGE, dest)
    return dest


@pytest.fixture
def clean_archive(tmp_path: Path) -> Path:
    """A writable copy of the clean-last-cycle archive (D-060's shape)."""
    dest = tmp_path / "clean-last-cycle-run"
    shutil.copytree(CLEAN_LAST_CYCLE, dest)
    return dest


@pytest.fixture
def c6_archive(tmp_path: Path) -> Path:
    """A writable copy of the C-6 archive (D-188's shape)."""
    dest = tmp_path / "c6-rollup-run"
    shutil.copytree(C6_ROLLUP, dest)
    return dest


def _outcomes(summary: dict) -> dict[str, str]:
    return {name: step["outcome"] for name, step in summary["steps"].items()}


# ---------------------------------------------------------------------------
# Preconditions — the fixture really is pre-change.
# ---------------------------------------------------------------------------


def test_fixture_is_a_pre_change_archive(archive: Path) -> None:
    records = json.loads((archive / "defects.json").read_text())["defects"]
    assert records, "fixture must carry defect records"
    assert not any("class" in r or "classification" in r for r in records)
    assert not any("tier" in r for r in records), (
        "the fixture must predate the evidence tier, or step 2 proves nothing"
    )
    assert json.loads((archive / "state.json").read_text())["cycle"] == 0
    assert not (archive / "observations.json").exists()
    assert not (archive / "stream-rollup.json").exists()
    assert not (archive / "progress").exists()


# ---------------------------------------------------------------------------
# The seven migration steps.
# ---------------------------------------------------------------------------


def test_step_1_defects_gain_class_and_classification(archive: Path) -> None:
    before = json.loads((archive / "defects.json").read_text())["defects"]
    summary = _migrate(archive)
    assert _outcomes(summary)["defects"] == "upgraded"

    after = json.loads((archive / "defects.json").read_text())["defects"]
    assert len(after) == len(before), "records must never be dropped"
    for original, migrated in zip(before, after):
        # Every pre-existing key and value preserved exactly.
        for key, value in original.items():
            assert migrated[key] == value, f"{original['id']} lost {key}"
        assert migrated["class"] is None
        assert migrated["classification"] == "DEFECT"
    # Order preserved — not renumbered, not reordered.
    assert [r["id"] for r in after] == [r["id"] for r in before]


def test_step_1_preserves_values_outside_the_reconciled_vocabulary(
    archive: Path,
) -> None:
    """AC-026 no-data-loss: the reconciled vocabulary governs NEW input, not
    archived history. Types and sources that entered through the unvalidated
    sync path survive verbatim.
    """
    _migrate(archive)
    records = json.loads((archive / "defects.json").read_text())["defects"]
    by_id = {r["id"]: r for r in records}
    assert by_id["D-003"]["type"] == "FALSE_DOCUMENTED_CONTRACT"
    assert by_id["D-006"]["type"] == "TEST_RECORD_SCOPE"
    assert by_id["D-006"]["source"] == "legacy_stream"


# ---------------------------------------------------------------------------
# Step 2 — the evidence tier (GI-001 / FR-051 / A-044's compatibility regime).
# ---------------------------------------------------------------------------


def test_step_2_defects_gain_the_evidence_tier_fields(archive: Path) -> None:
    """Every pre-change record gains all five fields, losing nothing.

    A migrated record and a record written fresh by this release then carry
    the same key set, which is what lets every reader use one shape instead of
    branching on the archive's provenance.
    """
    before = json.loads((archive / "defects.json").read_text())["defects"]
    summary = _migrate(archive)
    assert _outcomes(summary)["defect_tier"] == "upgraded"

    step = summary["steps"]["defect_tier"]
    assert step["records"] == len(before)
    assert step["upgraded"] == len(before)
    assert step["fields"] == [
        "tier", "reproduction_attempted", "regression_test", "authored_by",
        "fix_commit",
    ]

    after = json.loads((archive / "defects.json").read_text())["defects"]
    assert len(after) == len(before), "records must never be dropped"
    for original, migrated in zip(before, after):
        for key, value in original.items():
            assert migrated[key] == value, f"{original['id']} lost {key}"
        assert migrated["tier"] == "unknown"
        # Written as null, not omitted — Foundry-Fix and the LATENT door fill
        # these in later, and a reader must not have to tell "absent" from
        # "not yet set".
        for key in ("reproduction_attempted", "regression_test", "authored_by",
                    "fix_commit"):
            assert key in migrated and migrated[key] is None, (
                f"{original['id']} is missing {key}"
            )
    assert [r["id"] for r in after] == [r["id"] for r in before]


def test_step_2_never_migrates_an_untiered_record_to_latent(archive: Path) -> None:
    """FR-051 — the load-bearing negative, stated as a negative.

    LATENT blocks no gate: FR-006 passes INSPECT-clean, ASSAY, TEMPER, NYQUIST
    and DONE alike on a LATENT-only backlog. So migrating records nobody ever
    classified to LATENT would silently clear every one of them on every
    archive resumed under this release — grand-vulture's 168 and
    thunder-viper's 162 among them. Unknown blocks like LIVE; LATENT does not.
    That is the whole difference, and it is why the default is not "the
    quieter one".
    """
    _migrate(archive)
    records = json.loads((archive / "defects.json").read_text())["defects"]
    assert records, "fixture must carry records for this to prove anything"
    tiers = {r["tier"] for r in records}
    assert tiers == {"unknown"}, tiers
    assert "LATENT" not in tiers
    assert "LIVE" not in tiers


def test_step_2_reports_the_unknown_count_the_measurer_will_read(
    archive: Path,
) -> None:
    """The migration's count and measure-run's column must be the same number.

    Two tools reading one file and disagreeing about it is D-060's shape.
    Here the agreement is checked directly rather than assumed.
    """
    summary = _migrate(archive)
    reported = summary["steps"]["defect_tier"]["tier_unknown"]
    measured = _measure(archive)["defects_by_tier"]
    assert reported == measured["unknown"] == 6
    assert measured["LIVE"] == 0 and measured["LATENT"] == 0


def test_step_2_leaves_an_already_tiered_record_exactly_as_found(
    archive: Path,
) -> None:
    """Idempotency at the RECORD level, not just the file level (NFR-003).

    A half-migrated ledger is the ordinary state after an interrupted run, and
    a record already carrying LIVE must keep it. The `tier_unknown` count is
    still the honest total, because it counts what the file READS as rather
    than what this invocation happened to stamp.
    """
    path = archive / "defects.json"
    data = json.loads(path.read_text())
    data["defects"][0]["tier"] = "LIVE"
    data["defects"][1]["tier"] = "LATENT"
    data["defects"][1]["reproduction_attempted"] = "AST sweep finds 0 call sites"
    path.write_text(json.dumps(data), encoding="utf-8")

    summary = _migrate(archive)
    records = json.loads(path.read_text())["defects"]
    assert records[0]["tier"] == "LIVE"
    assert records[1]["tier"] == "LATENT"
    assert records[1]["reproduction_attempted"] == "AST sweep finds 0 call sites"
    assert summary["steps"]["defect_tier"]["tier_unknown"] == 4


def test_step_2_counts_a_hand_edited_bogus_tier_as_unknown(archive: Path) -> None:
    """The count goes through vocab.defect_tier, so the tools cannot disagree.

    An inline ``isinstance(value, str)`` check would have called ``"MINOR"``
    classified while every gate reads it as unknown — a grade smuggled into
    the ledger and then reported as if it had been triaged.
    """
    path = archive / "defects.json"
    data = json.loads(path.read_text())
    data["defects"][0]["tier"] = "MINOR"
    path.write_text(json.dumps(data), encoding="utf-8")

    summary = _migrate(archive)
    assert summary["steps"]["defect_tier"]["tier_unknown"] == 6
    assert _measure(archive)["defects_by_tier"]["unknown"] == 6
    # Archived history is preserved verbatim, exactly as step 1 preserves a
    # `type` outside the reconciled vocabulary.
    assert json.loads(path.read_text())["defects"][0]["tier"] == "MINOR"


def test_steps_1_and_2_share_one_write_of_defects_json(archive: Path) -> None:
    """Two steps, one read/modify/write — the _migrate_state shape.

    Splitting the write would double the window in which an interrupted
    migration leaves a ledger with `class` stamped and `tier` not, which is
    the half-migrated state AC-026 forbids. Proven by the temp file: an atomic
    write leaves no `defects.tmp` behind, and two writes would have produced
    two renames of it.
    """
    summary = _migrate(archive)
    assert _outcomes(summary)["defects"] == "upgraded"
    assert _outcomes(summary)["defect_tier"] == "upgraded"
    assert not (archive / "defects.tmp").exists()
    record = json.loads((archive / "defects.json").read_text())["defects"][0]
    assert record["classification"] == "DEFECT" and record["tier"] == "unknown"


def test_migration_creates_no_artifact_the_run_never_produced(archive: Path) -> None:
    """The regime's other half: additive means ADDING FIELDS, not inventing runs.

    escalation.json, spend.jsonl and state.json's `inspect_modes` are written
    by a run that executed under this release. Creating empty ones would turn
    "never measured" into "measured zero" — and measure-run's convergence
    columns would then report 0 tokens spent and 0 escalated classes for a run
    that spent 1M tokens and escalated three.
    """
    _migrate(archive)
    assert not (archive / "escalation.json").exists()
    assert not (archive / "spend.jsonl").exists()
    assert "inspect_modes" not in json.loads((archive / "state.json").read_text())

    measured = _measure(archive)
    for column in ("spend", "inspect_modes", "escalation"):
        assert measured[column] is None, (
            f"{column} reports {measured[column]!r} for an archive that never "
            f"produced it; structurally missing is null, never a zero"
        )


def test_step_3_observations_ledger_created_empty(archive: Path) -> None:
    summary = _migrate(archive)
    assert _outcomes(summary)["observations"] == "created"
    payload = json.loads((archive / "observations.json").read_text())
    # Never populated from defects.json — migration reclassifies nothing.
    assert payload == {"observations": []}


def test_step_4_stream_rollup_is_derived_per_cycle(archive: Path) -> None:
    """D-029 — the migrated document must be the shape its CONSUMER reads.

    orchestration.streams._rollup_totals looks an entry up as
    ``cycles[str(cycle)][<lowercase wire id>]`` and requires a dict carrying
    items_checked / items_total / findings / records. v1 wrote
    ``cycles[c][<CANONICAL>] = <int>``, which reads back as "no record for this
    cycle" — the artifact existed and fed nothing.

    ``findings`` comes from the ledger; ``items_checked`` / ``items_total``
    come from the ``.{stream}-complete`` marker at the ONE cycle it records,
    and are 0 elsewhere because coverage is not derivable per cycle.
    """
    summary = _migrate(archive)
    assert _outcomes(summary)["stream_rollup"] == "created"
    rollup = json.loads((archive / "stream-rollup.json").read_text())
    assert rollup["cycles"] == {
        # trace@0: ledger findings 1 raised to the marker's 3 (never-weaken),
        # and the marker's real coverage carried through.
        "0": {
            "prove": {"items_checked": 0, "items_total": 0, "findings": 1, "records": []},
            "trace": {"items_checked": 12, "items_total": 15, "findings": 3, "records": []},
        },
        "2": {
            "prove": {"items_checked": 0, "items_total": 0, "findings": 1, "records": []},
            "test01": {"items_checked": 0, "items_total": 0, "findings": 1, "records": []},
        },
        "5": {
            "prove": {"items_checked": 0, "items_total": 0, "findings": 1, "records": []},
        },
        # prove@6 exists ONLY because the marker records it — a cycle with
        # coverage but no defects must still appear, or the clean-stream check
        # reads "no record" where the run has a 100%-coverage marker.
        "6": {
            "prove": {"items_checked": 40, "items_total": 40, "findings": 0, "records": []},
        },
    }
    # Aggregates live in the SUMMARY, never in the document: a persisted total
    # beside live per-cycle data goes stale on the server's next append.
    assert "totals" not in rollup
    step = summary["steps"]["stream_rollup"]
    assert step["findings_per_stream"] == {"prove": 3, "test01": 1, "trace": 3}
    # A source that names no stream is NAMED, never coerced onto one.
    assert step["non_stream_sources"] == {"legacy_stream": 1}


def test_step_4_rollup_is_readable_by_its_consumer(archive: Path) -> None:
    """The D-029 regression test — read the migrated file the way the server does.

    Imports the real ``_rollup_totals`` rather than re-implementing its lookup,
    so the assertion tracks the consumer instead of a copy of it.
    """
    from foundry_mcp.tools.orchestration.streams import _rollup_totals

    _migrate(archive)
    assert _rollup_totals(archive, 0, "trace") == {
        "items_checked": 12, "items_total": 15, "findings": 3, "records": 0,
    }
    assert _rollup_totals(archive, 6, "prove") == {
        "items_checked": 40, "items_total": 40, "findings": 0, "records": 0,
    }
    assert _rollup_totals(archive, 2, "test01") == {
        "items_checked": 0, "items_total": 0, "findings": 1, "records": 0,
    }
    # A (cycle, stream) the archive proves nothing about still reads as None,
    # so the marker fallback in _coverage_shortfall keeps working.
    assert _rollup_totals(archive, 2, "trace") is None


def test_step_4_rebuilds_an_unreadable_v1_document(archive: Path) -> None:
    """An archive migrated by v1 carries a roll-up its consumer ignores.

    The version marker is a fast path, not the detector: step 3 recognises the
    old int-valued shape itself and re-derives.
    """
    (archive / "stream-rollup.json").write_text(
        json.dumps({"schema_version": 1, "cycles": {"0": {"PROVE": 1, "TRACE": 1}}}),
        encoding="utf-8",
    )
    summary = _migrate(archive)
    assert _outcomes(summary)["stream_rollup"] == "upgraded"
    rollup = json.loads((archive / "stream-rollup.json").read_text())
    assert rollup["cycles"]["0"]["trace"]["items_checked"] == 12


def test_step_4_never_rebuilds_a_server_written_document(archive: Path) -> None:
    """A live roll-up holds records the archive's own data cannot reconstruct.

    Re-deriving over it would be data loss, so an already-dict-valued document
    is left exactly as found.
    """
    live = {
        "cycles": {
            "0": {
                "prove": {
                    "items_checked": 165,
                    "items_total": 165,
                    "findings": 0,
                    "records": [{"recorded_at": "2026-08-30T04:34:50Z", "findings": 0}],
                }
            }
        },
        "updated_at": "2026-08-30T04:34:50+00:00",
    }
    (archive / "stream-rollup.json").write_text(json.dumps(live), encoding="utf-8")
    summary = _migrate(archive)
    assert _outcomes(summary)["stream_rollup"] == "no-op"
    assert json.loads((archive / "stream-rollup.json").read_text()) == live


# ---------------------------------------------------------------------------
# D-188 — THE REBUILD DETECTOR AND THE WIDENED DOCUMENT.
#
# ``cycles[<cycle>]`` has held two kinds of key since the spec's Data Model
# added the C-6 cycle facts: STREAM TRANCHES under a wire id, and facts about
# the CYCLE ITSELF beside them. ``_rollup_needs_rebuild`` tested every value's
# shape by hand, so ``inspect_mode`` and ``inspect_rule`` -- plain strings --
# read as the v1 int-valued shape the rebuild exists to replace. Driven on a
# byte copy of this run's own archive, the tool reported ``upgraded`` and
# re-derived over 45 server-written keys, PROVE's cycle-11 audit row among
# them. ``/foundry:resume`` runs this tool on every resume.
#
# These four tests hold both halves of the fix down: the roster scopes the
# value test (so a cycle-level fact is not evidence of an old document), and
# any server-written evidence anywhere VETOES the rebuild (so a mixed document
# is preserved rather than half-repaired and half-destroyed).
# ---------------------------------------------------------------------------


def test_c6_fixture_really_carries_the_cycle_level_facts(c6_archive: Path) -> None:
    """Precondition: without the widened keys the next three prove nothing.

    Asserts the two STRING-valued keys by name, because they are the ones that
    tripped the old ``not isinstance(entry, dict)`` branch, and ``temper_entry``
    because it is the fifth such key -- the one a denylist of the four names
    D-188 quoted would already have missed.
    """
    rollup = json.loads((c6_archive / "stream-rollup.json").read_text())
    cycle_11 = rollup["cycles"]["11"]
    assert isinstance(cycle_11["inspect_mode"], str)
    assert isinstance(cycle_11["inspect_rule"], str)
    assert "records" in cycle_11["prove"], "tranches sit in the same mapping"
    cycle_12 = rollup["cycles"]["12"]
    assert set(cycle_12) >= {
        "inspect_mode",
        "inspect_rule",
        "stream_scope",
        "evidence_sweep",
        "temper_entry",
    }


def test_step_4_never_rebuilds_over_c6_cycle_facts(c6_archive: Path) -> None:
    """D-188: the widened document is left byte-identical, not re-derived.

    Byte-identity rather than an outcome check alone: ``no-op`` is the claim,
    and the only proof of it is that the file on disk did not move.
    """
    before = (c6_archive / "stream-rollup.json").read_bytes()
    summary = _migrate(c6_archive)
    assert _outcomes(summary)["stream_rollup"] == "no-op"
    assert (c6_archive / "stream-rollup.json").read_bytes() == before


def test_a_c6_archive_migrates_as_a_whole_no_op(c6_archive: Path) -> None:
    """NFR-003 / NFR-010 — the SECOND run changes not one byte, on any archive.

    THE CLAIM MOVED ONE RUN LATER, AND THE FIXTURE IS WHY. This asserted that
    every step no-ops on the FIRST run against a C-6 archive, on the ground that
    such an archive was written by "this release" and had nothing left to do.
    That was true while C-6 was the current schema and stopped being true the
    moment schema 4 added five steps: the fixture is a schema-3 archive, its
    defect records carry no `fallout_of`, its manifest carries no
    `requirement_ids`, and it has no concern ledger — so a first run that
    no-op'd on it would mean the new steps do not work, which is the opposite of
    the property this test exists to hold.

    Idempotency is the property, and it is asserted where it lives: migrate
    once, hash the tree, migrate again, and require `migrated` False, every step
    `no-op`, and a byte-identical tree. That is strictly stronger than the old
    reading — it covers the five new steps as well as the seven — and it is the
    assertion CT-018 ("none new; idempotent") and NFR-010 both name.

    What has NOT moved is D-188: step 4 must still leave this fixture's roll-up
    untouched, and the test below this one asserts exactly that against the
    first run.
    """
    first = _migrate(c6_archive)
    assert first["archive_schema_version"] == ARCHIVE_SCHEMA_VERSION

    before = _tree_hash(c6_archive)
    summary = _migrate(c6_archive)
    assert set(_outcomes(summary).values()) == {"no-op"}, _outcomes(summary)
    assert summary["migrated"] is False
    assert _tree_hash(c6_archive) == before


def test_step_4_preserves_a_mixed_document_rather_than_half_rebuilding_it(
    archive: Path,
) -> None:
    """Server-written evidence anywhere vetoes the rebuild.

    The reachable mixed shape is a v1-migrated archive RESUMED under this
    release: its old cycles are inert ints, its new ones carry the server's
    audit trail and cycle facts. Re-deriving destroys the half that cannot be
    reconstructed in order to repair the half that can, so the whole document
    is left as found and the operator is told which cycle is still inert by
    reading it.
    """
    mixed = {
        "cycles": {
            "0": {"PROVE": 1, "TRACE": 1},
            "5": {
                "inspect_mode": "FULL",
                "prove": {
                    "items_checked": 172,
                    "items_total": 172,
                    "findings": 2,
                    "records": [{"recorded_at": "2026-09-01T09:31:00Z"}],
                },
            },
        }
    }
    (archive / "stream-rollup.json").write_text(json.dumps(mixed), encoding="utf-8")
    summary = _migrate(archive)
    assert _outcomes(summary)["stream_rollup"] == "no-op"
    assert json.loads((archive / "stream-rollup.json").read_text()) == mixed


def test_step_6_reads_a_c6_rollup_step_4_left_as_found(archive: Path) -> None:
    """D-188 adjacent path: step 4's verdict propagates into step 6.

    Not the transition the defect was driven on. Step 6 repairs
    ``state.json["cycle"]`` from ``derive_cycle_count``, which reads the roll-up
    ALREADY ON DISK -- so what step 4 decides about a widened document decides a
    number in a different file, in the same invocation. Before the fix step 4
    re-derived this document, and the cycle keys step 6 then read were the
    re-derived ones rather than the server's; the post-condition held only by
    accident of the two agreeing. Here the roll-up's highest key (11) is a cycle
    NO defect record in the fixture carries, so the assertion can only pass if
    step 6 read the preserved document.
    """
    (archive / "stream-rollup.json").write_text(
        json.dumps(
            {
                "cycles": {
                    "11": {
                        "inspect_mode": "DELTA",
                        "inspect_rule": "delta",
                        "stream_scope": {"prove": {"scope": "delta", "detail": None}},
                        "evidence_sweep": {"scope": "delta", "mismatches": []},
                        "prove": {
                            "items_checked": 172,
                            "items_total": 172,
                            "findings": 2,
                            "records": [{"recorded_at": "2026-09-01T09:31:00Z"}],
                        },
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    summary = _migrate(archive)
    assert _outcomes(summary)["stream_rollup"] == "no-op", "widened doc left as found"
    assert summary["steps"]["state_cycle"]["observed"] == 11
    assert json.loads((archive / "state.json").read_text())["cycle"] == 11


def test_step_4_rederivation_is_deterministic(archive: Path, tmp_path: Path) -> None:
    """Re-deriving from the same archive yields a byte-identical document."""
    _migrate(archive)
    first = (archive / "stream-rollup.json").read_bytes()

    twin = tmp_path / "twin"
    shutil.copytree(PRE_CHANGE, twin)
    _migrate(twin)
    assert (twin / "stream-rollup.json").read_bytes() == first


def test_step_5_progress_directory_created_empty(archive: Path) -> None:
    summary = _migrate(archive)
    assert _outcomes(summary)["progress"] == "created"
    progress = archive / "progress"
    assert progress.is_dir()
    assert list(progress.iterdir()) == []


def test_step_6_cycle_repaired_from_the_runs_own_data(archive: Path) -> None:
    """FI-1: state.json["cycle"] is written once as 0 and never incremented.

    The fixture's defects span cycles 0-5 and its `.prove-complete` marker
    records cycle=6, so the run's own data proves it reached index 6.

    D-084 re-points the SOURCE, not the number. The fixture also carries a
    `fixed_in_cycle` of 6, and that used to be what supplied this value; the
    marker supplies it now, and the test below pins that the fix stamp alone
    can no longer inflate the index.
    """
    summary = _migrate(archive)
    step = summary["steps"]["state_cycle"]
    assert step["outcome"] == "upgraded"
    assert step["recorded"] == 0
    assert step["observed"] == 6
    assert json.loads((archive / "state.json").read_text())["cycle"] == 6


def test_step_6_writes_the_index_derive_cycle_count_reads_not_a_fix_stamp(
    archive: Path,
) -> None:
    """D-084 — the field is an INDEX, and one function decides what proves it.

    `state.json["cycle"]` is read by `foundry_state.derive_cycle_count` as the
    server's 0-based counter and republished as index + 1, so this tool and
    that reader have to agree about which number the field holds. They did
    not: this folded `fixed_in_cycle` and `reopened_in_cycle` in, while
    `derive_cycle_count`'s defect axis reads `cycle` alone. On thunder-viper —
    max `cycle` 21, max `fixed_in_cycle` 22 — migrating turned a 22-cycle run
    into 23, permanently, because re-running the migration is a no-op.

    Driven here on the isolated shape: every other source is stripped, one
    record is stamped `cycle: 3` and `fixed_in_cycle: 9`, and the fix stamp
    must move nothing. `test_migrating_the_baseline_archive_still_derives_its_
    22_cycles` in test_measure_run.py drives the same property on the real
    archive; this one holds when that archive is not in the checkout.
    """
    from foundry_mcp.tools.foundry_state import derive_cycle_count

    for marker in archive.glob(".*-complete"):
        marker.unlink()
    (archive / "verdicts.json").unlink()
    (archive / "defects.json").write_text(
        json.dumps({"defects": [
            {"id": "D-001", "cycle": 3, "status": "fixed", "fixed_in_cycle": 9},
        ]}),
        encoding="utf-8",
    )

    summary = _migrate(archive)
    step = summary["steps"]["state_cycle"]
    assert step["observed"] == 3, (
        "a fix stamp is not evidence of an index the counter reached"
    )
    written = json.loads((archive / "state.json").read_text())["cycle"]
    assert written == 3

    # The contract the two share, asserted as the identity it is.
    derived = derive_cycle_count(archive)
    assert derived["index"] == written
    assert derived["count"] == written + 1

    # And migrating again changes nothing — the corruption D-084 describes was
    # permanent precisely because this step is idempotent.
    _migrate(archive)
    assert json.loads((archive / "state.json").read_text())["cycle"] == written
    assert derive_cycle_count(archive)["count"] == derived["count"]


def test_step_6_never_lowers_an_already_higher_value(archive: Path) -> None:
    state = json.loads((archive / "state.json").read_text())
    state["cycle"] = 99
    (archive / "state.json").write_text(json.dumps(state), encoding="utf-8")

    _migrate(archive)
    assert json.loads((archive / "state.json").read_text())["cycle"] == 99


def test_step_6_repairs_a_non_integer_cycle(archive: Path) -> None:
    state = json.loads((archive / "state.json").read_text())
    state["cycle"] = "seventeen"
    (archive / "state.json").write_text(json.dumps(state), encoding="utf-8")

    _migrate(archive)
    assert json.loads((archive / "state.json").read_text())["cycle"] == 6


def test_step_6_preserves_the_rest_of_state(archive: Path) -> None:
    before = json.loads((archive / "state.json").read_text())
    _migrate(archive)
    after = json.loads((archive / "state.json").read_text())
    for key, value in before.items():
        if key == "cycle":
            continue
        assert after[key] == value, f"state.json lost {key}"


def test_step_7_schema_version_marker_recorded(archive: Path) -> None:
    summary = _migrate(archive)
    assert _outcomes(summary)["archive_schema_version"] == "upgraded"
    state = json.loads((archive / "state.json").read_text())
    assert state["archive_schema_version"] == ARCHIVE_SCHEMA_VERSION
    assert summary["migrated"] is True


# ---------------------------------------------------------------------------
# Idempotency (NFR-003, OT-012).
# ---------------------------------------------------------------------------


def test_second_run_is_a_no_op(archive: Path) -> None:
    """NFR-003 — safe to re-run on an already-migrated archive."""
    _migrate(archive)
    hash_after_first = _tree_hash(archive)

    summary = _migrate(archive)
    assert _tree_hash(archive) == hash_after_first, "second run changed the tree"
    assert summary["migrated"] is False
    assert set(_outcomes(summary).values()) == {"no-op"}


def test_each_step_is_individually_idempotent(archive: Path) -> None:
    """The version marker is a fast path, not the safety mechanism.

    Stripping the marker from an otherwise-migrated archive must still
    produce a no-op on every step — proving steps 1-6 guard on absence
    themselves rather than relying on the marker.
    """
    _migrate(archive)
    state = json.loads((archive / "state.json").read_text())
    del state["archive_schema_version"]
    (archive / "state.json").write_text(json.dumps(state), encoding="utf-8")

    summary = _migrate(archive)
    outcomes = _outcomes(summary)
    assert outcomes["defects"] == "no-op"
    assert outcomes["defect_tier"] == "no-op"
    assert outcomes["observations"] == "no-op"
    assert outcomes["stream_rollup"] == "no-op"
    assert outcomes["progress"] == "no-op"
    assert outcomes["state_cycle"] == "no-op"
    # Only the stripped marker is rewritten.
    assert outcomes["archive_schema_version"] == "upgraded"


# ---------------------------------------------------------------------------
# D-060 — step 6 must consult the same evidence step 4 keys the roll-up on.
#
# The ORDINARY case: the last INSPECT cycle's streams come back clean, so their
# ``.{stream}-complete`` markers record a cycle that no defect record carries.
# Reading only defects.json left state.json BEHIND stream-rollup.json's highest
# key, and measure-run fired PHASE9_CYCLE_COUNT_INVALID on the archive the
# migration had just repaired — with re-running unable to heal it.
# ---------------------------------------------------------------------------


def test_clean_last_cycle_fixture_marker_exceeds_every_defect_cycle(
    clean_archive: Path,
) -> None:
    """The precondition that makes this fixture a D-060 repro at all.

    Both the synthetic pre_change fixture (marker 6 == max defect cycle 6) and
    grand-vulture (all three markers at 17 == max defect cycle 17) agree by
    coincidence. This one deliberately does not.
    """
    records = json.loads((clean_archive / "defects.json").read_text())["defects"]
    max_defect_cycle = max(r["cycle"] for r in records)
    assert max_defect_cycle == 2

    marker = (clean_archive / ".prove-complete").read_text()
    assert "cycle=3" in marker
    assert "findings=0" in marker, "a clean final INSPECT files no defects"
    assert json.loads((clean_archive / "state.json").read_text())["cycle"] == 0


def test_step_6_consults_the_markers_step_4_keys_on(clean_archive: Path) -> None:
    """D-060 — the repaired cycle must not sit behind the roll-up's own keys.

    Step 4 keys entries by BOTH defect cycles and each marker's ``cycle=``;
    step 6 read only defects.json, so it wrote 2 where the roll-up wrote 3.
    """
    summary = _migrate(clean_archive)
    step = summary["steps"]["state_cycle"]
    assert step["recorded"] == 0
    assert step["observed"] == 3, "the marker's cycle is evidence too"
    assert json.loads((clean_archive / "state.json").read_text())["cycle"] == 3

    rollup = json.loads((clean_archive / "stream-rollup.json").read_text())
    highest_rollup_key = max(int(k) for k in rollup["cycles"])
    assert highest_rollup_key == 3
    assert step["cycle"] >= highest_rollup_key, (
        "state.json must never sit behind the roll-up written in the same run"
    )


def test_migrated_archive_passes_its_own_shipped_detector(
    clean_archive: Path,
) -> None:
    """The cross-tool pin: migrate -> measure yields zero failure tokens.

    Both halves landed in the same commit and read different evidence for the
    same number, so the migration produced an archive its own detector called
    stale. Nothing here may fire PHASE9_CYCLE_COUNT_INVALID.
    """
    _migrate(clean_archive)
    after = _measure(clean_archive)
    assert "PHASE9_CYCLE_COUNT_INVALID" not in after["failure_tokens"], (
        after["failure_tokens"]
    )

    # Negative control — the detector is not merely silent. Wind state.json
    # back to 2, the value the defects-only derivation produced, and it fires
    # again against the very roll-up the migration wrote.
    state_path = clean_archive / "state.json"
    state = json.loads(state_path.read_text())
    state["cycle"] = 2
    state_path.write_text(json.dumps(state), encoding="utf-8")
    assert "PHASE9_CYCLE_COUNT_INVALID" in _measure(clean_archive)["failure_tokens"]


def test_second_migration_of_a_clean_last_cycle_archive_is_a_no_op(
    clean_archive: Path,
) -> None:
    """The bug was permanent: re-running never healed it. Idempotency here is
    the assertion that the FIRST run already left nothing to heal.
    """
    _migrate(clean_archive)
    hash_after_first = _tree_hash(clean_archive)

    summary = _migrate(clean_archive)
    assert _tree_hash(clean_archive) == hash_after_first
    assert set(_outcomes(summary).values()) == {"no-op"}


def test_dry_run_reports_the_cycle_a_real_run_would_record(
    clean_archive: Path, tmp_path: Path
) -> None:
    """--dry-run writes no roll-up, so step 6's evidence must be self-contained.

    Reading the markers directly (rather than the roll-up step 4 would have
    written) is what keeps the dry-run report honest.
    """
    dry = _migrate(clean_archive, "--dry-run")
    assert dry["steps"]["state_cycle"]["cycle"] == 3
    assert not (clean_archive / "stream-rollup.json").exists()

    twin = tmp_path / "twin"
    shutil.copytree(CLEAN_LAST_CYCLE, twin)
    real = _migrate(twin)
    assert real["steps"]["state_cycle"] == dry["steps"]["state_cycle"]


def test_step_6_honours_a_server_written_rollups_own_keys(archive: Path) -> None:
    """Step 4 leaves a live roll-up exactly as found, so step 6 reads its keys.

    Without this the post-condition would hold only for roll-ups this tool
    derived itself.
    """
    (archive / "stream-rollup.json").write_text(
        json.dumps(
            {
                "cycles": {
                    "11": {
                        "prove": {
                            "items_checked": 9,
                            "items_total": 9,
                            "findings": 0,
                            "records": [{"recorded_at": "2026-08-30T04:34:50Z"}],
                        }
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    summary = _migrate(archive)
    assert _outcomes(summary)["stream_rollup"] == "no-op", "live doc left as found"
    assert summary["steps"]["state_cycle"]["observed"] == 11
    assert json.loads((archive / "state.json").read_text())["cycle"] == 11


# ---------------------------------------------------------------------------
# --dry-run and refusals.
# ---------------------------------------------------------------------------


def test_dry_run_writes_nothing(archive: Path) -> None:
    before = _tree_hash(archive)
    summary = _migrate(archive, "--dry-run")
    assert summary["dry_run"] is True
    assert summary["migrated"] is True, "dry-run still reports what would change"
    assert _tree_hash(archive) == before, "--dry-run must write nothing at all"
    assert not (archive / "observations.json").exists()
    assert not (archive / "progress").exists()


def test_missing_run_dir_is_a_usage_error(tmp_path: Path) -> None:
    exit_code, stdout, _ = _invoke_migrate(str(tmp_path / "nope"))
    assert exit_code == 2
    assert "MIGRATE_RUN_DIR_INVALID" in json.loads(stdout)["failure_tokens"]


def test_malformed_defects_file_fails_without_writing(archive: Path) -> None:
    """A malformed archive must fail loudly, not half-migrate."""
    (archive / "defects.json").write_text("{not json", encoding="utf-8")
    before = _tree_hash(archive)

    exit_code, stdout, stderr = _invoke_migrate(str(archive))
    assert exit_code == 1, (stdout, stderr)
    assert "MIGRATE_DEFECTS_FILE_MALFORMED" in json.loads(stdout)["failure_tokens"]
    assert _tree_hash(archive) == before, "failed migration must not write"


# ---------------------------------------------------------------------------
# AC-026 / OT-012 — the real grand-vulture archive.
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not GRAND_VULTURE.exists(),
    reason=(
        f"grand-vulture archive not present in this checkout: {GRAND_VULTURE} "
        "(foundry-archive/ is git-ignored)"
    ),
)
def test_grand_vulture_migration(tmp_path: Path) -> None:
    """AC-026 + OT-012 — the acceptance fixture, migrated as a COPY.

    The real archive is never opened for writing: shutil.copytree first, then
    migrate the copy.
    """
    original = json.loads((GRAND_VULTURE / "defects.json").read_text())["defects"]
    assert len(original) == 168, "grand-vulture baseline is 168 defects"

    dest = tmp_path / "grand-vulture"
    shutil.copytree(GRAND_VULTURE, dest)

    summary = _migrate(dest)
    assert summary["migrated"] is True

    # --- no data loss across all 168 records ---
    migrated = json.loads((dest / "defects.json").read_text())["defects"]
    assert len(migrated) == 168
    for before, after in zip(original, migrated):
        for key in ("id", "source", "type", "description", "status"):
            assert after[key] == before[key], f"{before['id']} lost {key}"
        assert after["class"] is None
        assert after["classification"] == "DEFECT"
        # FR-051 on the real acceptance fixture: 168 records nobody ever
        # classified read as unknown, which blocks like LIVE. Had they migrated
        # to LATENT, resuming this archive would have walked straight through
        # INSPECT-clean, ASSAY, TEMPER, NYQUIST and DONE alike — LATENT blocks
        # none of them (FR-006).
        assert after["tier"] == "unknown"
    assert summary["steps"]["defect_tier"]["tier_unknown"] == 168
    # AC-024 / GI-014 — the tier ROSTER, never a hand-typed list. HARDENING
    # joined `vocab.DEFECT_TIERS` this release and `measure-run.py` seeds its
    # counts from `DEFECT_TIER_OR_UNKNOWN`, so an expected shape re-typed here
    # would be a second list drifting against the first — on the surface whose
    # whole job is to notice drift.
    expected = dict.fromkeys(sorted(DEFECT_TIER_OR_UNKNOWN), 0)
    expected["unknown"] = 168
    assert _measure(dest)["defects_by_tier"] == expected

    # The 43 records whose types are outside the reconciled vocabulary — they
    # entered through the unvalidated sync path and survive verbatim.
    types = [r["type"] for r in migrated]
    assert types.count("FALSE_DOCUMENTED_CONTRACT") == 42
    assert types.count("TEST_RECORD_SCOPE") == 1

    # --- new artifacts exist ---
    assert (dest / "observations.json").exists()
    assert (dest / "stream-rollup.json").exists()
    assert (dest / "progress").is_dir()

    # --- cycle repaired from 0 to 17 ---
    assert json.loads((GRAND_VULTURE / "state.json").read_text())["cycle"] == 0
    state = json.loads((dest / "state.json").read_text())
    assert state["cycle"] == 17
    assert state["archive_schema_version"] == ARCHIVE_SCHEMA_VERSION

    # --- roll-up carries the real per-stream counts, and its consumer can
    #     actually read them (D-029) ---
    from foundry_mcp.tools.orchestration.streams import _rollup_totals

    findings_per_stream = summary["steps"]["stream_rollup"]["findings_per_stream"]
    assert findings_per_stream == {"prove": 165, "test": 2, "trace": 1}
    assert sum(findings_per_stream.values()) == 168, "every defect accounted for"
    # cycle 17 is the repaired current cycle AND the markers' cycle, so the
    # entries there carry grand-vulture's real terminal coverage rather than
    # shadowing the marker fallback with zeros.
    assert _rollup_totals(dest, 17, "prove") == {
        "items_checked": 165, "items_total": 165, "findings": 0, "records": 0,
    }
    assert _rollup_totals(dest, 17, "trace") == {
        "items_checked": 38, "items_total": 38, "findings": 0, "records": 0,
    }
    assert _rollup_totals(dest, 0, "prove")["findings"] == 9

    # --- OT-012 second half: a second run is a no-op ---
    hash_after_first = _tree_hash(dest)
    second = _migrate(dest)
    assert _tree_hash(dest) == hash_after_first
    assert second["migrated"] is False
    assert set(_outcomes(second).values()) == {"no-op"}

    # --- and the real archive was never touched ---
    assert json.loads((GRAND_VULTURE / "state.json").read_text())["cycle"] == 0
    assert not (GRAND_VULTURE / "observations.json").exists()


# ---------------------------------------------------------------------------
# SCHEMA 4 — steps 8 to 12 (FR-028 / FR-054 / CT-018 / AC-034 / AC-049).
#
# The five new steps join the existing four-fixture register rather than
# getting one of their own: the synthetic pre-change archive is the ordinary
# case, the C-6 archive is the one written under a release whose roll-up must
# survive contact with them, and the two real archives are the acceptance
# fixtures NFR-010 names — migrated on COPIES, never opened for writing.
#
# WHAT MAKES THESE STEPS DIFFERENT FROM THE SEVEN ABOVE, and what the tests
# therefore have to hold down: steps 1-7 CREATE what is absent, while step 8
# REWRITES what is present. A rewrite can destroy, and GI-006 names the way it
# would — "a replace-semantics write that drops history" — so every assertion
# below that moves a total also asserts the records are still there.
# ---------------------------------------------------------------------------


def _rollup_bucket(checked: int, total: int, findings: int, records: list) -> dict:
    return {
        "items_checked": checked, "items_total": total,
        "findings": findings, "records": records,
    }


#: The OT-032 row, verbatim from daring-orca's cycle 29: two records a minute
#: apart, each 542/542, accumulated by the additive writer into 1084/542.
OT_032_RECORDS = [
    {"recorded_at": "2026-09-05T01:03:47.428012+00:00", "items_checked": 542,
     "items_total": 542, "findings": 4, "declared_cycle": 29},
    {"recorded_at": "2026-09-05T01:04:52.960145+00:00", "items_checked": 542,
     "items_total": 542, "findings": 4, "declared_cycle": 29},
]


def test_step_8_rewrites_the_ot_032_row_and_keeps_both_records(
    archive: Path,
) -> None:
    """AC-034 / OT-032 — "1084/542 reads 542/542 with two records retained".

    The acceptance criterion names one row and this is that row, transcribed
    from daring-orca's own stream-rollup.json. The additive writer appended a
    record AND added its numbers into the totals, so a stream that recorded
    twice in one cycle reads as having checked twice as many items as exist.

    BOTH HALVES ARE THE ASSERTION. Moving the totals is the easy half; GI-006's
    named violation is "a replace-semantics write that drops history", and the
    superseded record is the only evidence that the row was recorded twice —
    the thing that makes the over-100% figure explicable rather than merely
    wrong. `foundry_state.stream_rollup_rows` publishes `replaced_count` off
    exactly that list, so a repair that tidied it away would fix one number by
    blinding the reader that explains it.
    """
    (archive / "stream-rollup.json").write_text(
        json.dumps({
            "schema_version": 3,
            "cycles": {
                "29": {"trace": _rollup_bucket(1084, 542, 8, OT_032_RECORDS)},
            },
        }),
        encoding="utf-8",
    )
    summary = _migrate(archive)
    assert _outcomes(summary)["rollup_totals"] == "upgraded"

    entry = json.loads(
        (archive / "stream-rollup.json").read_text()
    )["cycles"]["29"]["trace"]
    assert entry["items_checked"] == 542, "the LAST record's value, not the sum"
    assert entry["items_total"] == 542
    assert entry["findings"] == 4
    assert entry["records"] == OT_032_RECORDS, (
        "every earlier record stays under records[] — GI-006 names dropping "
        "history as the violation, and the superseded record is the evidence "
        "that explains the number that was wrong"
    )

    row = summary["steps"]["rollup_totals"]["rows"][0]
    assert row["cycle"] == "29" and row["stream"] == "trace"
    assert row["from"] == {"items_checked": 1084, "items_total": 542, "findings": 8}
    assert row["to"] == {"items_checked": 542, "items_total": 542, "findings": 4}
    assert row["records_kept"] == 2


def test_step_8_leaves_cycle_level_facts_and_recordless_tranches_alone(
    archive: Path,
) -> None:
    """D-182 / D-188 — this is the fifth walker of the cycle bucket.

    Four readers before it assumed every key in a cycle bucket was a stream, and
    the last one to get it wrong destroyed 45 keys of a real archive. So the
    key's value decides, through the ONE definition in
    `foundry_state.is_stream_record` — never a denylist of the C-6 names, which
    would need an edit at every new cycle-level field and already misses
    `temper_entry`.

    A tranche with no `records[]` is left alone for a different reason: there is
    nothing to take a total FROM. Inventing one would be this step asserting a
    measurement, in the step whose whole purpose is to stop the totals asserting
    one.
    """
    document = {
        "cycles": {
            "7": {
                "inspect_mode": "FULL",
                "inspect_rule": "verifier_touched",
                "stream_scope": {"trace": {"scope": "full"}},
                "evidence_sweep": {"scope": "full", "corpus_size": 94},
                "temper_entry": {"opened": True},
                # No records at all — the additive writer before histories.
                "prove": {"items_checked": 30, "items_total": 20, "findings": 1},
                # Records present and the totals already the last one's.
                "trace": _rollup_bucket(12, 12, 0, [
                    {"items_checked": 12, "items_total": 12, "findings": 0},
                ]),
            },
        },
    }
    (archive / "stream-rollup.json").write_text(
        json.dumps(document), encoding="utf-8"
    )
    summary = _migrate(archive)
    assert _outcomes(summary)["rollup_totals"] == "no-op"
    assert json.loads((archive / "stream-rollup.json").read_text()) == document, (
        "not one byte: no stream tranche had a history to rewrite from, and "
        "the five cycle-level facts are not tranches at all"
    )


def test_step_8_takes_only_the_keys_the_last_record_states(archive: Path) -> None:
    """A rewrite transcribes; it does not fill in.

    A record carrying `items_checked` and no `findings` rewrites the first and
    leaves the second exactly as found. Writing 0 for the key the record does
    not mention would put a number in the document that nothing in the archive
    ever measured — which is the lie this tool's own header exists to name.
    """
    (archive / "stream-rollup.json").write_text(
        json.dumps({
            "cycles": {
                "2": {"prove": _rollup_bucket(99, 50, 7, [
                    {"items_checked": 50, "items_total": 50},
                ])},
            },
        }),
        encoding="utf-8",
    )
    _migrate(archive)
    entry = json.loads(
        (archive / "stream-rollup.json").read_text()
    )["cycles"]["2"]["prove"]
    assert entry["items_checked"] == 50
    assert entry["items_total"] == 50
    assert entry["findings"] == 7, "the last record states no findings count"


def test_step_9_stamps_fallout_and_supersedes_without_touching_a_value(
    archive: Path,
) -> None:
    """FR-025 / FR-054 — the KEY's presence is what makes a cycle measurable.

    `foundry_state.fallout_rows` counts a record as measured when it carries the
    `fallout_of` KEY, because an absent key is a record written before anyone
    asked and a null is a stream answering "no parent". A cycle holding one
    un-keyed record cannot be measured at all, so NFR-006's acceptance figure is
    unreachable on an un-migrated archive — which is what this step is for.

    An EXISTING value is never touched, including an existing null: `_add_missing`
    is per field, so a ledger half-stamped by an interrupted run converges on the
    next pass rather than being skipped whole or overwritten whole.
    """
    (archive / "defects.json").write_text(
        json.dumps({"defects": [
            {"id": "D-001", "cycle": 0, "source": "prove", "type": "THIN"},
            {"id": "D-002", "cycle": 1, "source": "trace", "type": "THIN",
             "fallout_of": "D-001"},
            {"id": "D-003", "cycle": 1, "source": "trace", "type": "THIN",
             "fallout_of": None, "supersedes": "D-000"},
        ]}),
        encoding="utf-8",
    )
    summary = _migrate(archive)
    step = summary["steps"]["defect_fallout"]
    assert step["outcome"] == "upgraded"
    assert step["upgraded"] == 2, "D-003 already carries both keys"
    assert step["fallout_named"] == 1, "only D-002 names a parent"
    assert step["fields"] == ["fallout_of", "supersedes"]

    records = json.loads((archive / "defects.json").read_text())["defects"]
    by_id = {r["id"]: r for r in records}
    assert by_id["D-001"]["fallout_of"] is None
    assert by_id["D-001"]["supersedes"] is None
    assert by_id["D-002"]["fallout_of"] == "D-001", "an existing value is kept"
    assert by_id["D-002"]["supersedes"] is None
    assert by_id["D-003"]["supersedes"] == "D-000"


def test_a_migrated_ledger_is_measurable_and_an_unmigrated_one_is_not(
    archive: Path,
) -> None:
    """AC-045 / NFR-006 end to end — the two tools on one archive.

    Before the migration the closing pair holds records with no `fallout_of` key
    and `measure-run.py` reports `not_measurable`, naming the cycles and telling
    the operator to run this tool. After it, the same pair is a MEASURED zero
    and the acceptance figure has an answer. That transition is the whole reason
    step 9 stamps a default rather than leaving the field off, and it is the
    only place in this suite where both halves of the pair are driven together.
    """
    (archive / "state.json").write_text(
        json.dumps({"phase": "F3", "cycle": 2}), encoding="utf-8"
    )
    (archive / "defects.json").write_text(
        json.dumps({"defects": [
            {"id": "D-001", "cycle": 1, "source": "prove", "type": "THIN"},
            {"id": "D-002", "cycle": 2, "source": "trace", "type": "THIN"},
        ]}),
        encoding="utf-8",
    )

    before = _measure(archive)["fallout_per_cycle"]
    assert before["verdict"] == "not_measurable"
    assert before["unmeasured_records"] == 2
    assert "migrate-archive.py" in before["verdict_reason"]

    _migrate(archive)

    after = _measure(archive)["fallout_per_cycle"]
    assert after["unmeasured_records"] == 0
    assert after["measured_records"] == 2
    assert after["verdict"] == "pass"
    # The pair is `derive_cycle_count`'s axis, not this fixture's defect
    # stamps, and step 6 raises the recorded cycle to what the archive's own
    # markers and verdicts prove it reached. So the closing pair is asserted as
    # a PAIR at the top of that axis rather than as two literals — hard-coding
    # them here would be this test carrying its own idea of how many cycles the
    # run ran, which is the second-derivation shape the axis argument exists to
    # prevent.
    pair = after["last_two_cycles"]
    assert len(pair) == 2 and pair[1] == pair[0] + 1
    for cycle in pair:
        bucket = after["per_cycle"].get(str(cycle), {})
        assert bucket.get("fallout", 0) == 0
        assert bucket.get("unmeasured", 0) == 0


def _manifest(*castings: dict) -> dict:
    return {"spec_type": "GREENFIELD", "castings": list(castings)}


def _casting(cid: int, *ids: str) -> dict:
    """A casting entry whose `spec_text` DECLARES exactly `ids`.

    The block is written in F0.5's own bold-bullet shape, because
    `declared_requirement_ids` reads position and not merely the family — an id
    in subject position on its own line is a declaration and one quoted inside
    another requirement's prose is not (D-180). A fixture that faked the shape
    would be testing a regex this tool does not use.
    """
    lines = "\n".join(f"- **{rid}** [from A-001]: statement text" for rid in ids)
    return {"id": cid, "title": f"Casting {cid}", "spec_text": lines}


def test_step_10_transcribes_ownership_from_the_castings_own_blocks(
    archive: Path,
) -> None:
    """FR-054 / AC-049 — the field is filled from the archive, not computed.

    `foundry_handoff.declared_requirement_ids` is the ONE derivation in the tree
    of which ids a `<spec_requirements>` block declares, and it is the same
    function F0.9's ownership dimension uses to decide what a casting declared.
    Reading the archive's own answer into the archive's own field is
    transcription; computing one would be manufacturing, and the door would then
    be checking this tool's opinion against the prose instead of the prose
    against itself.
    """
    (archive / "castings").mkdir()
    (archive / "castings" / "manifest.json").write_text(
        json.dumps(_manifest(
            _casting(1, "FR-001", "AC-001"),
            _casting(2, "FR-001", "FR-002"),
        )),
        encoding="utf-8",
    )
    summary = _migrate(archive)
    step = summary["steps"]["casting_ownership"]
    assert step["outcome"] == "upgraded"
    assert step["requirement_ids_filled"] == 2
    assert step["shared_requirement_ids"] == ["FR-001"]

    castings = json.loads(
        (archive / "castings" / "manifest.json").read_text()
    )["castings"]
    assert castings[0]["requirement_ids"] == ["AC-001", "FR-001"]
    assert castings[1]["requirement_ids"] == ["FR-001", "FR-002"]
    # The reason is recorded ONLY where ownership is shared. A waiver on
    # something nobody would refuse is noise in a table a lead reads.
    assert set(castings[0]["split_reason"]) == {"FR-001"}
    assert set(castings[1]["split_reason"]) == {"FR-001"}
    assert "AC-001" not in castings[0]["split_reason"]


def test_step_10_never_fills_an_empty_list_or_leaves_the_field_absent(
    archive: Path,
) -> None:
    """FR-054's negative — the two fills that would trip the door it must not.

    Step 7 raises the schema marker to 4, and `foundry_validate` reads a missing
    `requirement_ids` as "predates the field" only BELOW 4. So after this tool
    runs there are exactly two ways to fail the door, and both are shapes a
    naive "fill the default" would produce:

      * ABSENT (or null, which `_owned_requirement_ids` reads as absent) — an
        un-migrated record at a schema that says otherwise, reported as
        `missing_requirement_ids` per casting;
      * `[]` — a casting positively CLAIMING it owns nothing, which the door
        checks against the casting's own prose and refuses id by id as
        `declared_but_not_owned`.

    A casting whose block genuinely declares nothing DOES get `[]`, and that is
    not the same claim: it is the transcription of an empty declaration, and the
    door agrees with it because `declared` is empty too.
    """
    (archive / "castings").mkdir()
    (archive / "castings" / "manifest.json").write_text(
        json.dumps(_manifest(
            _casting(1, "FR-001"),
            {"id": 2, "title": "Docs only", "spec_text": "No requirements here."},
        )),
        encoding="utf-8",
    )
    _migrate(archive)
    castings = json.loads(
        (archive / "castings" / "manifest.json").read_text()
    )["castings"]

    assert castings[0]["requirement_ids"] == ["FR-001"], "not empty, not absent"
    assert castings[1]["requirement_ids"] == [], (
        "a block that declares nothing transcribes to nothing, and the door "
        "compares that against a declared set that is also empty"
    )
    for casting in castings:
        assert casting["requirement_ids"] is not None


def test_step_10_leaves_a_recorded_split_reason_whole(archive: Path) -> None:
    """A recorded decision is not a schema gap.

    `split_reason` present is somebody's answer to "why can these surfaces not
    share an owner", and adding entries to it would be this tool editing a
    decision rather than migrating a schema. Guarded on the KEY, like every
    other default here.
    """
    kept = {"FR-001": "the door and its report cannot share an owner"}
    (archive / "castings").mkdir()
    (archive / "castings" / "manifest.json").write_text(
        json.dumps(_manifest(
            {**_casting(1, "FR-001", "FR-002"), "split_reason": dict(kept)},
            _casting(2, "FR-001"),
        )),
        encoding="utf-8",
    )
    _migrate(archive)
    castings = json.loads(
        (archive / "castings" / "manifest.json").read_text()
    )["castings"]
    assert castings[0]["split_reason"] == kept, "untouched"
    assert set(castings[1]["split_reason"]) == {"FR-001"}, "filled where absent"


def test_the_migrated_manifest_does_not_trip_the_f0_9_ownership_or_span_door(
    archive: Path,
) -> None:
    """AC-049 / FR-054 — "a migrated archive does not trip it".

    Driven through the door's OWN helpers rather than a restatement of its
    rules: `_owned_requirement_ids` decides presence, `_recorded_split_reasons`
    collects the waivers from both the manifest and the castings, and
    `_requirement_span_rows` computes the spans. If casting 7 changes any of the
    three, this test changes with it instead of quietly asserting a rule the
    door no longer applies.

    The fixture is the shape that makes both dimensions bite: FR-001 owned by
    three castings, which is above `REQUIREMENT_SPAN_MAX` and would be refused
    with no reason recorded against it.
    """
    from foundry_mcp.tools.foundry_handoff import declared_requirement_ids
    from foundry_mcp.tools.foundry_validate import (
        REQUIREMENT_IDS_SCHEMA_FLOOR,
        REQUIREMENT_SPAN_MAX,
        _owned_requirement_ids,
        _recorded_split_reasons,
        _requirement_span_rows,
    )

    (archive / "castings").mkdir()
    (archive / "castings" / "manifest.json").write_text(
        json.dumps(_manifest(
            _casting(1, "FR-001", "AC-001"),
            _casting(2, "FR-001"),
            _casting(3, "FR-001", "AC-002"),
        )),
        encoding="utf-8",
    )
    _migrate(archive)

    manifest = json.loads((archive / "castings" / "manifest.json").read_text())
    castings = manifest["castings"]
    state = json.loads((archive / "state.json").read_text())
    assert state["archive_schema_version"] >= REQUIREMENT_IDS_SCHEMA_FLOOR, (
        "the schema bump is what makes this door bite at all"
    )

    ownership = {str(c["id"]): _owned_requirement_ids(c) for c in castings}
    assert all(present for present, _ in ownership.values()), (
        "no casting reports `missing_requirement_ids`"
    )
    for casting in castings:
        _present, owned = ownership[str(casting["id"])]
        declared = set(declared_requirement_ids(casting["spec_text"]))
        assert owned == declared, (
            "neither `declared_but_not_owned` nor `owned_but_not_declared`"
        )

    spec_ids = {
        rid for c in castings for rid in declared_requirement_ids(c["spec_text"])
    }
    rows = _requirement_span_rows(
        spec_ids, castings, ownership, _recorded_split_reasons(manifest, castings)
    )
    over = [row for row in rows if row["span"] > REQUIREMENT_SPAN_MAX]
    assert [row["id"] for row in over] == ["FR-001"], "the fixture's shared id"
    assert all(row["split_reason"] for row in over), (
        "a span above the maximum with no recorded reason is what F0.9 refuses"
    )


def test_steps_11_and_12_create_the_containers_and_claim_no_measurement(
    archive: Path,
) -> None:
    """CT-001 / CT-002 — an empty ledger and an empty directory, and no files.

    An empty `concerns.json` asserts that no concern was RECORDED, which is true
    of every archive written before `Foundry-Concern` existed, and its reader
    answers a missing file and an empty list the same way. An empty `rosters/`
    asserts that no roster was persisted, likewise.

    A `rosters/<stream>.json` holding zero items would be the lie the header
    names: it asserts a stream DERIVED its item list and found nothing, and
    `Foundry-Roster` would then refuse the real derivation with `ROSTER_EXISTS`
    on the strength of a file this tool invented. So the directory is created
    and nothing is put in it.
    """
    from foundry_mcp.tools.concerns import CONCERNS_COLLECTION_KEY, CONCERNS_FILENAME
    from foundry_mcp.tools.rosters import ROSTERS_DIRNAME

    summary = _migrate(archive)
    assert _outcomes(summary)["concerns"] == "created"
    assert _outcomes(summary)["rosters"] == "created"

    ledger = json.loads((archive / CONCERNS_FILENAME).read_text())
    assert ledger == {CONCERNS_COLLECTION_KEY: []}

    rosters = archive / ROSTERS_DIRNAME
    assert rosters.is_dir()
    assert list(rosters.iterdir()) == [], (
        "a roster file holding zero items would assert a derivation nobody made"
    )

    # And the reader casting 1 ships agrees the ledger is empty rather than
    # broken — the container is a shape its consumer already expects.
    from foundry_mcp.tools.concerns import read_concerns

    records, problem = read_concerns(archive)
    assert problem is None and records == []


def test_dry_run_writes_none_of_the_five_new_steps(archive: Path) -> None:
    """`--dry-run` reports what would change and writes nothing at all.

    The five new steps include the only two that REWRITE rather than create, so
    the honest-dry-run rule matters more to them than to the seven above: an
    operator inspecting an archive before committing to a rewrite of its
    coverage totals must be able to see the rows without moving them.
    """
    (archive / "castings").mkdir()
    (archive / "castings" / "manifest.json").write_text(
        json.dumps(_manifest(_casting(1, "FR-001"))), encoding="utf-8"
    )
    (archive / "stream-rollup.json").write_text(
        json.dumps({"cycles": {
            "29": {"trace": _rollup_bucket(1084, 542, 8, OT_032_RECORDS)},
        }}),
        encoding="utf-8",
    )

    before = _tree_hash(archive)
    summary = _migrate(archive, "--dry-run")
    assert summary["dry_run"] is True
    assert summary["migrated"] is True, "it reports what it WOULD do"
    assert _outcomes(summary)["rollup_totals"] == "upgraded"
    assert _outcomes(summary)["casting_ownership"] == "upgraded"
    assert _outcomes(summary)["concerns"] == "created"
    assert _outcomes(summary)["rosters"] == "created"
    assert _tree_hash(archive) == before, "and writes not one byte of it"


#: NFR-010's three named archives. `foundry-archive/` is git-ignored, so each
#: is present in a working checkout and absent from a clean clone — the same
#: skipif discipline the grand-vulture test above uses, one archive wider.
REAL_ARCHIVES = {
    "daring-orca": REPO_ROOT / "foundry-archive" / "daring-orca",
    "thunder-viper": REPO_ROOT / "foundry-archive" / "thunder-viper",
    "grand-vulture": REPO_ROOT / "foundry-archive" / "grand-vulture",
}


@pytest.mark.parametrize("name", sorted(REAL_ARCHIVES))
def test_the_schema_4_migration_is_idempotent_on_every_named_archive(
    name: str, tmp_path: Path
) -> None:
    """NFR-010 / CT-018 — "idempotent, verified against the three archives".

    THE COPY IS THE TEST'S FIRST ASSERTION, not its setup. The real archives are
    NEVER opened for writing — that is the fixture register's own rule and it is
    load-bearing here, because two of these three are the published baselines
    NFR-001 compares every run against and the third is this effort's own
    predecessor run. A migration that mutated one would destroy the numbers the
    release is measured by, and no later test could tell.

    The three cover three different shapes: daring-orca is the schema-3 archive
    with the nine over-100% rows and the eight-casting manifest carrying no
    `requirement_ids`; thunder-viper ran on the 4.7.3 cache and has no
    escalation ledger, no spend ledger and a counter that stayed at 0 for all 22
    cycles; grand-vulture is the AC-026 acceptance fixture. Idempotency has to
    hold on all three or it is a property of one fixture.
    """
    source = REAL_ARCHIVES[name]
    if not source.exists():
        pytest.skip(f"{name} not present in this checkout: {source}")

    source_hash = _tree_hash(source)
    dest = tmp_path / name
    shutil.copytree(source, dest)

    first = _migrate(dest)
    assert first["failure_tokens"] == []
    assert first["archive_schema_version"] == ARCHIVE_SCHEMA_VERSION

    after_first = _tree_hash(dest)
    second = _migrate(dest)
    assert second["migrated"] is False, _outcomes(second)
    assert set(_outcomes(second).values()) == {"no-op"}, _outcomes(second)
    assert _tree_hash(dest) == after_first, "the second run writes not one byte"

    assert _tree_hash(source) == source_hash, (
        "the real archive was opened for writing — it is a published baseline "
        "and this suite never touches it"
    )


def test_demo_ot_032_the_row_before_and_after_migration(archive: Path) -> None:
    """AC-034 / OT-032 / GI-006 — the acceptance row, printed either side.

    A demonstration rather than another assertion of the same fact: the
    criterion is stated as a before-and-after ("a row that read 1084/542 reads
    542/542 with two records retained"), and a log that shows only the after
    cannot be checked against it. Mirrors this suite's other `test_demo_*`
    narratives — everything printed is derived from the run, and the assertions
    below are the same ones the non-demo tests make.
    """
    (archive / "stream-rollup.json").write_text(
        json.dumps({"cycles": {
            "29": {
                "inspect_mode": "FULL",
                "trace": _rollup_bucket(1084, 542, 8, OT_032_RECORDS),
            },
        }}),
        encoding="utf-8",
    )

    def _row() -> dict:
        return json.loads(
            (archive / "stream-rollup.json").read_text()
        )["cycles"]["29"]["trace"]

    before = _row()
    print("\n=== daring-orca cycle 29, stream trace (AC-034 / OT-032) ===")
    print(f"  before: items_checked={before['items_checked']} "
          f"items_total={before['items_total']} "
          f"findings={before['findings']} records={len(before['records'])}")
    print("  the additive writer added each record's numbers INTO the totals,")
    print("  so a stream that recorded twice reads as checking twice the items")
    print(f"  that exist: {before['items_checked']}/{before['items_total']} is "
          f"{before['items_checked'] * 100 // before['items_total']}%.")

    summary = _migrate(archive)
    after = _row()
    print(f"\n  step 8 outcome: {_outcomes(summary)['rollup_totals']}")
    print(f"  after:  items_checked={after['items_checked']} "
          f"items_total={after['items_total']} "
          f"findings={after['findings']} records={len(after['records'])}")
    print("  the totals are the LAST record's values and every earlier record")
    print("  is still under records[] — GI-006 names dropping history as the")
    print("  violation, and the superseded record is the evidence that")
    print("  explains the number that was wrong.")
    for index, record in enumerate(after["records"]):
        print(f"    records[{index}]: {record['items_checked']}/"
              f"{record['items_total']} findings={record['findings']}")

    second = _migrate(archive)
    print(f"\n  second run: migrated={second['migrated']} "
          f"step 8 outcome={_outcomes(second)['rollup_totals']}")
    print("  idempotent (CT-018 'none new; idempotent', NFR-010).")

    assert after["items_checked"] == 542
    assert after["items_total"] == 542
    assert after["records"] == OT_032_RECORDS
    assert second["migrated"] is False
    assert "inspect_mode" in json.loads(
        (archive / "stream-rollup.json").read_text()
    )["cycles"]["29"], "the cycle-level fact is not a tranche and was not touched"


def test_demo_the_f0_9_door_reads_a_migrated_manifest(archive: Path) -> None:
    """AC-049 / FR-054 — the door's verdict on a manifest before and after.

    Printed because the claim is a TRANSITION and the failure it guards against
    is silent: step 7 raises the schema marker past the floor at which
    `requirement_ids` becomes mandatory, so an archive migrated without step 10
    would go from one informational line to one error per casting and nothing
    in the migration summary would say so.
    """
    from foundry_mcp.tools.foundry_handoff import declared_requirement_ids
    from foundry_mcp.tools.foundry_validate import (
        REQUIREMENT_IDS_SCHEMA_FLOOR,
        REQUIREMENT_SPAN_MAX,
        _owned_requirement_ids,
        _recorded_split_reasons,
        _requirement_span_rows,
    )

    manifest_path = archive / "castings"
    manifest_path.mkdir()
    (manifest_path / "manifest.json").write_text(
        json.dumps(_manifest(
            _casting(1, "FR-001", "AC-001"),
            _casting(2, "FR-001"),
            _casting(3, "FR-001", "AC-002"),
        )),
        encoding="utf-8",
    )

    def _door() -> tuple[int, list[str], int]:
        manifest = json.loads((manifest_path / "manifest.json").read_text())
        castings = manifest["castings"]
        state = json.loads((archive / "state.json").read_text())
        schema = state.get("archive_schema_version", 0)
        ownership = {str(c["id"]): _owned_requirement_ids(c) for c in castings}
        not_computable = (
            not any(present for present, _ in ownership.values())
            and schema < REQUIREMENT_IDS_SCHEMA_FLOOR
        )
        missing = [] if not_computable else [
            str(c["id"]) for c in castings
            if not ownership[str(c["id"])][0]
        ]
        spec_ids = {
            rid for c in castings
            for rid in declared_requirement_ids(c["spec_text"])
        }
        rows = (
            [] if not_computable else
            _requirement_span_rows(
                spec_ids, castings, ownership,
                _recorded_split_reasons(manifest, castings),
            )
        )
        refused = [
            row["id"] for row in rows
            if row["span"] > REQUIREMENT_SPAN_MAX and not row["split_reason"]
        ]
        return schema, missing, len(refused)

    schema, missing, refused = _door()
    print("\n=== F0.9 ownership + span, before migration (AC-049) ===")
    print(f"  archive_schema_version={schema} "
          f"(floor is {REQUIREMENT_IDS_SCHEMA_FLOOR})")
    print(f"  castings reported missing requirement_ids: {len(missing)}")
    print(f"  requirement spans refused for want of a reason: {refused}")
    print("  below the floor the door reports `not computable` and checks")
    print("  neither dimension — which is what 'fail closed only for new runs'")
    print("  means, and what the schema bump would otherwise take away.")

    _migrate(archive)
    schema, missing, refused = _door()
    castings = json.loads((manifest_path / "manifest.json").read_text())["castings"]
    print("\n=== after migration ===")
    print(f"  archive_schema_version={schema}")
    for casting in castings:
        print(f"  casting {casting['id']}: "
              f"requirement_ids={casting['requirement_ids']} "
              f"split_reason keys={sorted(casting['split_reason'])}")
    print(f"  castings reported missing requirement_ids: {len(missing)}")
    print(f"  requirement spans refused for want of a reason: {refused}")
    print("  step 10 transcribed ownership from each casting's own")
    print("  <spec_requirements> block through the SAME derivation the door")
    print("  uses, and recorded a reason on every shared id — so the marker")
    print("  moved and the door still passes.")

    assert schema >= REQUIREMENT_IDS_SCHEMA_FLOOR
    assert missing == []
    assert refused == 0
