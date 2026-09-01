"""FR-021 / NFR-003 / AC-026 / OT-012 — the shipped archive migration tool.

Subprocess-invokes ``plugins/foundry/scripts/migrate-archive.py`` the way
test_measure_run.py invokes measure-run.py (``sys.executable`` + script path),
so the tests exercise the real CLI contract including its exit codes.

Two fixtures:

  * a committed SYNTHETIC pre-change archive that exercises all six migration
    steps. Unconditional — migration correctness and idempotency are covered
    on any checkout.
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
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "migrate_archive"
PRE_CHANGE = FIXTURES / "pre_change"

# The real pre-change archive named by AC-026 and OT-012. git-ignored, so it
# is present in a working checkout and absent from a clean clone.
GRAND_VULTURE = REPO_ROOT / "foundry-archive" / "grand-vulture"

ARCHIVE_SCHEMA_VERSION = 1


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


@pytest.fixture
def archive(tmp_path: Path) -> Path:
    """A writable copy of the synthetic pre-change archive."""
    dest = tmp_path / "synthetic-run"
    shutil.copytree(PRE_CHANGE, dest)
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
    assert json.loads((archive / "state.json").read_text())["cycle"] == 0
    assert not (archive / "observations.json").exists()
    assert not (archive / "stream-rollup.json").exists()
    assert not (archive / "progress").exists()


# ---------------------------------------------------------------------------
# The six migration steps.
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


def test_step_2_observations_ledger_created_empty(archive: Path) -> None:
    summary = _migrate(archive)
    assert _outcomes(summary)["observations"] == "created"
    payload = json.loads((archive / "observations.json").read_text())
    # Never populated from defects.json — migration reclassifies nothing.
    assert payload == {"observations": []}


def test_step_3_stream_rollup_is_derived_per_cycle(archive: Path) -> None:
    summary = _migrate(archive)
    assert _outcomes(summary)["stream_rollup"] == "created"
    rollup = json.loads((archive / "stream-rollup.json").read_text())
    # Keyed by cycle; lowercase wire sources mapped onto canonical ids by the
    # same vocab.canonical_stream_id call measure-run.py uses.
    assert rollup["cycles"] == {
        "0": {"PROVE": 1, "TRACE": 1},
        "2": {"PROVE": 1, "TEST-01": 1},
        "5": {"PROVE": 1},
    }
    assert rollup["totals"] == {"PROVE": 3, "TEST-01": 1, "TRACE": 1}
    # An unknown source is NAMED, never coerced onto a known stream.
    assert rollup["unresolved_sources"] == {"legacy_stream": 1}


def test_step_3_rederivation_is_deterministic(archive: Path, tmp_path: Path) -> None:
    """Re-deriving from the same archive yields a byte-identical document."""
    _migrate(archive)
    first = (archive / "stream-rollup.json").read_bytes()

    twin = tmp_path / "twin"
    shutil.copytree(PRE_CHANGE, twin)
    _migrate(twin)
    assert (twin / "stream-rollup.json").read_bytes() == first


def test_step_4_progress_directory_created_empty(archive: Path) -> None:
    summary = _migrate(archive)
    assert _outcomes(summary)["progress"] == "created"
    progress = archive / "progress"
    assert progress.is_dir()
    assert list(progress.iterdir()) == []


def test_step_5_cycle_repaired_from_the_runs_own_data(archive: Path) -> None:
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


def test_step_5_never_lowers_an_already_higher_value(archive: Path) -> None:
    state = json.loads((archive / "state.json").read_text())
    state["cycle"] = 99
    (archive / "state.json").write_text(json.dumps(state), encoding="utf-8")

    _migrate(archive)
    assert json.loads((archive / "state.json").read_text())["cycle"] == 99


def test_step_5_repairs_a_non_integer_cycle(archive: Path) -> None:
    state = json.loads((archive / "state.json").read_text())
    state["cycle"] = "seventeen"
    (archive / "state.json").write_text(json.dumps(state), encoding="utf-8")

    _migrate(archive)
    assert json.loads((archive / "state.json").read_text())["cycle"] == 6


def test_step_5_preserves_the_rest_of_state(archive: Path) -> None:
    before = json.loads((archive / "state.json").read_text())
    _migrate(archive)
    after = json.loads((archive / "state.json").read_text())
    for key, value in before.items():
        if key == "cycle":
            continue
        assert after[key] == value, f"state.json lost {key}"


def test_step_6_schema_version_marker_recorded(archive: Path) -> None:
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
    produce a no-op on every step — proving steps 1-5 guard on absence
    themselves rather than relying on the marker.
    """
    _migrate(archive)
    state = json.loads((archive / "state.json").read_text())
    del state["archive_schema_version"]
    (archive / "state.json").write_text(json.dumps(state), encoding="utf-8")

    summary = _migrate(archive)
    outcomes = _outcomes(summary)
    assert outcomes["defects"] == "no-op"
    assert outcomes["observations"] == "no-op"
    assert outcomes["stream_rollup"] == "no-op"
    assert outcomes["progress"] == "no-op"
    assert outcomes["state_cycle"] == "no-op"
    # Only the stripped marker is rewritten.
    assert outcomes["archive_schema_version"] == "upgraded"


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

    # --- roll-up carries the real per-stream counts ---
    rollup = json.loads((dest / "stream-rollup.json").read_text())
    assert rollup["totals"] == {"PROVE": 165, "TEST": 2, "TRACE": 1}
    assert sum(rollup["totals"].values()) == 168

    # --- OT-012 second half: a second run is a no-op ---
    hash_after_first = _tree_hash(dest)
    second = _migrate(dest)
    assert _tree_hash(dest) == hash_after_first
    assert second["migrated"] is False
    assert set(_outcomes(second).values()) == {"no-op"}

    # --- and the real archive was never touched ---
    assert json.loads((GRAND_VULTURE / "state.json").read_text())["cycle"] == 0
    assert not (GRAND_VULTURE / "observations.json").exists()
