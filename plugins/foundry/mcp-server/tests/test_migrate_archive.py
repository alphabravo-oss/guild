"""FR-021 / NFR-003 / AC-026 / OT-012 — the shipped archive migration tool.

Subprocess-invokes ``plugins/foundry/scripts/migrate-archive.py`` the way
test_measure_run.py invokes measure-run.py (``sys.executable`` + script path),
so the tests exercise the real CLI contract including its exit codes.

Three fixtures:

  * a committed SYNTHETIC pre-change archive that exercises all seven migration
    steps. Unconditional — migration correctness and idempotency are covered
    on any checkout.
  * a committed CLEAN-LAST-CYCLE archive whose ``.{stream}-complete`` markers
    record a cycle no defect record carries — the ORDINARY shape, because the
    last INSPECT cycle's clean streams file no defects. Both the synthetic
    fixture above and grand-vulture happen to have marker cycle == max defect
    cycle, and that coincidence is what hid D-060.
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

# The real pre-change archive named by AC-026 and OT-012. git-ignored, so it
# is present in a working checkout and absent from a clean clone.
GRAND_VULTURE = REPO_ROOT / "foundry-archive" / "grand-vulture"

# v2: step 4's output shape changed — v1 wrote a stream-rollup.json that its
# own consumer (foundry_orchestrator._rollup_totals) could not read (D-029).
# v3: the evidence-tier step joined the list (GI-001 / FR-051).
ARCHIVE_SCHEMA_VERSION = 3


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

    LATENT stops blocking at TEMPER, NYQUIST and DONE. So migrating records
    nobody ever classified to LATENT would silently clear three gates on every
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

    foundry_orchestrator._rollup_totals looks an entry up as
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
    from foundry_mcp.tools.foundry_orchestrator import _rollup_totals

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

    The fixture's defects span cycles 0-5 with a fixed_in_cycle of 6, so the
    run's own data proves it reached cycle 6.
    """
    summary = _migrate(archive)
    step = summary["steps"]["state_cycle"]
    assert step["outcome"] == "upgraded"
    assert step["recorded"] == 0
    assert step["observed"] == 6
    assert json.loads((archive / "state.json").read_text())["cycle"] == 6


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
        # TEMPER, NYQUIST and DONE.
        assert after["tier"] == "unknown"
    assert summary["steps"]["defect_tier"]["tier_unknown"] == 168
    assert _measure(dest)["defects_by_tier"] == {
        "LATENT": 0, "LIVE": 0, "unknown": 168,
    }

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
    from foundry_mcp.tools.foundry_orchestrator import _rollup_totals

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
