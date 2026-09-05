"""Per-cycle stream roll-up — FR-014 / CT-003 / AC-020 / OT-009.

A-024: "Stream records accumulate into a per-cycle roll-up artifact (keyed by
the new server-side cycle counter); partial records are accepted and stored;
thresholds are evaluated per cycle at the streams-complete check, where the full
picture exists; drop-warnings compare cycle N to cycle N-1."

Three behaviours changed, each of which the old shape got wrong for the same
underlying reason — a single overwritten marker file cannot accumulate:

  1. A partial PROVE tranche used to be REFUSED outright by
     ``foundry_mark_stream``, so the first half of a two-part PROVE run was
     discarded and its work had to be redone.
  2. The >=95% thresholds were evaluated at record time against ONE tranche
     rather than once per cycle against the cycle total.
  3. The coverage-drop warning compared against "the previous write of this
     same marker file", which is not cycle N-1 — it fires on a second tranche
     of the SAME cycle and stays silent across a real cycle boundary when only
     one write happened.

Also covered: FR-020 / AC-025's auto-VERIFY hole, which lives in the same
``_prove_is_clean`` read path.

fallout AC-030 / OT-028 / CT-003 / ST-008 / ST-009 — AND THE ARITHMETIC ABOVE IS
NO LONGER A SUM.

A-015: "Replace per (stream, cycle); the AGENT records; lead never does."

Point 2 above stands and point 1's fix stands, but the "cycle total" both were
written against was ``+= items_checked`` — the arithmetic for TRANCHES of one
run. What actually happens is a stream RE-RUNNING inside one cycle: a
re-dispatched TRACE, a PROVE the lead asked for again after a fix. Then 40 of 40
recorded twice reads as 80 of 40 and coverage passes 100%, satisfying the >=95%
threshold with the same work counted twice — the one direction a coverage check
must never fail. Three behaviours changed again, for that one reason:

  4. The cycle's totals are the LAST record's values, and the door returns
     ``replaced``: the record this one supersedes, or None on the first write
     for the pair.
  5. ``records[]`` keeps every record ever written, oldest first, so GI-006's
     "a replace-semantics write that drops history" does not describe this —
     what changes is which record the top-level fields report, never what the
     artifact holds.
  6. No cycle's coverage can exceed its own population, which is the
     daring-orca defect the first three points left open.

The register for those is at the foot of this module, driving the real door
against a ``tmp_path`` run dir and reading the persisted document back.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from foundry_mcp.tools import foundry_state

# fallout FR-005 / GI-010 / GI-026 / AC-014 — THE ROLL-UP HAS A MODULE, AND IT
# IS NAMED HERE.
#
# The `fo` alias reached three concerns through one name. The single
# orchestrator module it named is gone and GI-010 forbids a re-export shim
# standing in for it, so each reach names the module that DEFINES the
# symbol: the roll-up and its readers are `orchestration/streams.py`, the
# document transaction and the marker names are the leaf `tools/artifacts.py`,
# and the team scan is `orchestration/teams.py` — patched through
# `patch_everywhere` because five modules bind that name and patching only the
# definer leaves the other four on the real one.
from foundry_mcp.tools import artifacts as _artifacts
from foundry_mcp.tools.orchestration import streams as _streams
from foundry_mcp.tools.orchestration.streams import (
    _check_streams_complete,
    _prove_is_clean,
    _rollup_totals,
    foundry_mark_stream,
)

from tests.orchestration._env import patch_everywhere


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #


@pytest.fixture
def run_env(tmp_path, monkeypatch):
    """Activate a foundry run under tmp_path; yield (project_root, fdir)."""
    project_root = tmp_path
    run_name = "rollup-run"
    fdir = project_root / "foundry-archive" / run_name
    (fdir / "castings").mkdir(parents=True, exist_ok=True)

    patch_everywhere(
        monkeypatch,
        "_check_active_teams",
        lambda _pr: {"active": False, "teams": [], "live_panes": []},
    )

    foundry_state.set_active_run(run_name)
    try:
        yield str(project_root), fdir
    finally:
        foundry_state.clear_active_run()


def _write_spec(fdir: Path, count: int) -> None:
    """Write a spec parsing to `count` distinct requirement IDs."""
    body = "\n".join(f"- FR-{i:03d}: requirement {i}" for i in range(1, count + 1))
    (fdir / "spec.md").write_text(f"# Spec\n{body}\n", encoding="utf-8")


def _set_cycle(fdir: Path, cycle: int) -> None:
    """Set the SERVER cycle counter directly (the roll-up key)."""
    state_path = fdir / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {}
    state["cycle"] = cycle
    state_path.write_text(json.dumps(state), encoding="utf-8")


def _write_castings(fdir: Path, key_files: list[str]) -> None:
    (fdir / "castings" / "manifest.json").write_text(
        json.dumps({
            "target_url": "",
            "no_ui": True,
            "castings": [{"id": 1, "title": "t", "key_files": key_files}],
        }),
        encoding="utf-8",
    )


# --------------------------------------------------------------------------- #
# AC-020 / OT-009 — partial records are accepted and stored
# --------------------------------------------------------------------------- #


def test_a_partial_prove_tranche_is_accepted_not_refused(run_env):
    """AC-020: 'Recording a partial PROVE tranche is accepted and stored.'

    This is the exact reversal: 40 of 100 requirements used to return a hard
    error, discarding the tranche entirely.
    """
    project_root, fdir = run_env
    _write_spec(fdir, 100)

    result = foundry_mark_stream(
        "prove", cycle=0, items_checked=40, items_total=100, project_root=project_root
    )

    assert result.get("ok") is True, result
    assert "error" not in result
    assert _rollup_totals(fdir, 0, "prove")["items_checked"] == 40


def test_two_prove_records_in_one_cycle_are_both_stored(run_env):
    """OT-009's STORAGE half, which stands: neither record is discarded.

    fallout AC-030 / OT-028 / CT-003 / ST-009 — ITS ARITHMETIC HALF DOES NOT.
    OT-009 said the >=95% evaluation "runs once against their cycle total", and
    the total was the SUM. That is the arithmetic for tranches of one run, and
    the wrong arithmetic for what actually happens: a stream RE-RUNNING inside
    one cycle. 40 of 40 recorded twice then reads as 80 of 40 and clears the
    coverage threshold on the same work counted twice — the one direction a
    coverage check must never fail. The cycle's totals are the LAST record's
    values now; `records[]` keeps every one of them, so nothing is lost and
    GI-006's "a replace-semantics write that drops history" does not apply.
    """
    project_root, fdir = run_env
    _write_spec(fdir, 100)

    foundry_mark_stream(
        "prove", cycle=0, items_checked=40, items_total=100, project_root=project_root
    )
    foundry_mark_stream(
        "prove", cycle=0, items_checked=56, items_total=100, project_root=project_root
    )

    totals = _rollup_totals(fdir, 0, "prove")
    assert totals["records"] == 2
    assert totals["items_checked"] == 56      # the LAST record, not 40 + 56
    assert totals["items_total"] == 100       # the same denominator, not 200

    rollup = json.loads((fdir / "stream-rollup.json").read_text(encoding="utf-8"))
    stored = rollup["cycles"]["0"]["prove"]["records"]
    assert [r["items_checked"] for r in stored] == [40, 56]


def test_a_later_record_replaces_the_findings_the_earlier_one_reported(run_env):
    """fallout AC-030 / OT-028 / ST-009 — the cycle reports the LAST count.

    This used to assert the sum, so a clean second record could not erase a
    dirty first one. Under replace semantics it can, and deliberately: the
    agent records the state of ITS OWN run, and a stream re-run after a fix
    that finds nothing is reporting zero findings, not withdrawing three. The
    history is what keeps that honest — both counts stay under `records[]`, so
    a reader wanting the earlier one has it.
    """
    project_root, fdir = run_env
    _write_spec(fdir, 10)

    foundry_mark_stream(
        "prove", cycle=0, items_checked=5, items_total=10, findings_count=3,
        project_root=project_root,
    )
    foundry_mark_stream(
        "prove", cycle=0, items_checked=5, items_total=10, findings_count=0,
        project_root=project_root,
    )

    assert _rollup_totals(fdir, 0, "prove")["findings"] == 0

    rollup = json.loads((fdir / "stream-rollup.json").read_text(encoding="utf-8"))
    stored = rollup["cycles"]["0"]["prove"]["records"]
    assert [r["findings"] for r in stored] == [3, 0], stored


def test_the_marker_carries_the_cycle_total_not_a_sum(run_env):
    """The marker stays the completion signal, and its counts are the cycle's
    totals — so anything still reading the marker (old archives, the status
    display) sees what the roll-up says rather than a second derivation.

    fallout AC-030 / OT-028: the cycle's total IS the last record now, so this
    reads 56 where it once read 96. The claim is unchanged — the marker agrees
    with `_rollup_totals` — and it is the claim that matters, because a marker
    that disagreed with the roll-up would be the second reading of one fact.
    """
    project_root, fdir = run_env
    _write_spec(fdir, 100)

    foundry_mark_stream(
        "prove", cycle=0, items_checked=40, items_total=100, project_root=project_root
    )
    foundry_mark_stream(
        "prove", cycle=0, items_checked=56, items_total=100, project_root=project_root
    )

    assert _streams._marker_counts(fdir / ".prove-complete")["items_checked"] == 56
    assert (
        _streams._marker_counts(fdir / ".prove-complete")["items_checked"]
        == _rollup_totals(fdir, 0, "prove")["items_checked"]
    )


# --------------------------------------------------------------------------- #
# CT-003 — keyed by the SERVER cycle counter, never the caller's
# --------------------------------------------------------------------------- #


def test_rollup_is_keyed_by_the_server_counter_not_the_caller_value(run_env):
    """CT-003: 'a per-cycle roll-up artifact keyed by the server cycle
    counter'. FR-005 is the reason: a caller-asserted cycle is exactly what the
    old data model trusted, and grand-vulture's state.json read cycle 0 for 18
    cycles while its defects claimed 0-17."""
    project_root, fdir = run_env
    _write_spec(fdir, 10)
    _set_cycle(fdir, 4)

    result = foundry_mark_stream(
        "prove", cycle=99, items_checked=10, items_total=10, project_root=project_root
    )

    assert result["cycle"] == 4
    assert result["declared_cycle"] == 99
    rollup = json.loads((fdir / "stream-rollup.json").read_text(encoding="utf-8"))
    assert set(rollup["cycles"]) == {"4"}
    assert "99" not in rollup["cycles"]
    # The caller's assertion survives on the record for audit, as a claim.
    assert rollup["cycles"]["4"]["prove"]["records"][0]["declared_cycle"] == 99


def test_records_in_different_cycles_land_in_different_buckets(run_env):
    """Cycle N and cycle N+1 accumulate separately — that separation is what
    makes an N vs N-1 comparison possible at all."""
    project_root, fdir = run_env
    _write_spec(fdir, 10)

    _set_cycle(fdir, 0)
    foundry_mark_stream("trace", cycle=0, items_checked=30, project_root=project_root)
    _set_cycle(fdir, 1)
    foundry_mark_stream("trace", cycle=1, items_checked=28, project_root=project_root)

    assert _rollup_totals(fdir, 0, "trace")["items_checked"] == 30
    assert _rollup_totals(fdir, 1, "trace")["items_checked"] == 28


def test_a_cycle_with_no_record_for_a_stream_reads_as_none(run_env):
    """A missing record and a recorded zero must be distinguishable, or the
    drop comparison would read 'never ran' as 'ran and collapsed'."""
    _project_root, fdir = run_env
    assert _rollup_totals(fdir, 0, "prove") is None


# --------------------------------------------------------------------------- #
# CT-003 — thresholds evaluated ONCE PER CYCLE at the streams-complete check
# --------------------------------------------------------------------------- #


def test_threshold_shortfall_blocks_at_the_streams_complete_check(run_env):
    """AC-020: 'the >=95% evaluation happens once per cycle at the
    streams-complete check'. A short PROVE records fine but does not let the
    cycle complete."""
    project_root, fdir = run_env
    _write_spec(fdir, 100)
    _write_castings(fdir, ["src/api/login.py"])

    foundry_mark_stream(
        "prove", cycle=0, items_checked=40, items_total=100, project_root=project_root
    )
    foundry_mark_stream("trace", cycle=0, items_checked=10, project_root=project_root)
    foundry_mark_stream("test", cycle=0, items_checked=10, project_root=project_root)

    streams = _check_streams_complete(project_root)
    assert streams["complete"] is False
    assert "prove" in streams["missing"]
    assert [s["stream"] for s in streams["shortfalls"]] == ["prove"]
    assert streams["shortfalls"][0]["checked"] == 40
    assert streams["shortfalls"][0]["required"] == 100


def test_a_later_record_clears_the_shortfall_the_earlier_one_caused(run_env):
    """The whole point of evaluating per cycle rather than per call: the second
    record states the coverage the run reached, and the cycle passes. Under the
    old record-time check the first call was REFUSED and the second never
    happened, so the partial work was discarded and had to be redone.

    fallout AC-030 / OT-028 — WHAT THE SECOND RECORD SAYS.
    The second call reports 96 of 100, not the 56 that "completes" 40. Under
    replace semantics the agent states its own running total, which is what the
    agent has and the server does not (GI-016), and the threshold is evaluated
    against that one number rather than against a sum the server invented.
    """
    project_root, fdir = run_env
    _write_spec(fdir, 100)
    _write_castings(fdir, ["src/api/login.py"])
    foundry_mark_stream("trace", cycle=0, items_checked=10, project_root=project_root)
    foundry_mark_stream("test", cycle=0, items_checked=10, project_root=project_root)

    foundry_mark_stream(
        "prove", cycle=0, items_checked=40, items_total=100, project_root=project_root
    )
    assert _check_streams_complete(project_root)["complete"] is False

    foundry_mark_stream(
        "prove", cycle=0, items_checked=96, items_total=100, project_root=project_root
    )
    streams = _check_streams_complete(project_root)
    assert streams["complete"] is True, streams
    assert streams["shortfalls"] == []


def _write_legacy_marker(
    fdir: Path, stream: str, items_checked: int, items_total: int, findings: int = 0
) -> None:
    """Write a ``.{stream}-complete`` marker with NO roll-up beside it.

    The shape every archive written before the per-cycle roll-up existed has:
    the marker carried the whole history, because it was the only artifact.
    """
    (fdir / f".{stream}-complete").write_text(
        f"2020-01-01T00:00:00+00:00 cycle=0\n"
        f"items_checked={items_checked}\n"
        f"items_total={items_total}\n"
        f"coverage=n/a\n"
        f"findings={findings}\n",
        encoding="utf-8",
    )


def test_a_marker_only_archive_is_still_measured_against_the_threshold(run_env):
    """D-030 / AC-020 / CT-003: coverage evaluation read the roll-up and treated
    "no roll-up entry" as "no shortfall".

    On a legacy or migrated archive — markers present, ``stream-rollup.json``
    absent — the streams-complete check therefore counted the stream as PRESENT
    while the >=95% threshold silently evaluated nothing, so 40% coverage
    passed. "No numbers here" has to mean "read them from the marker", never
    "assume the threshold is met".
    """
    project_root, fdir = run_env
    _write_spec(fdir, 100)
    _write_castings(fdir, ["src/api/login.py"])

    _write_legacy_marker(fdir, "prove", items_checked=40, items_total=100)
    _write_legacy_marker(fdir, "trace", items_checked=10, items_total=10)
    _write_legacy_marker(fdir, "test", items_checked=10, items_total=10)
    assert not (fdir / _streams.ROLLUP_FILENAME).exists()

    streams = _check_streams_complete(project_root)

    assert streams["complete"] is False, streams
    assert [s["stream"] for s in streams["shortfalls"]] == ["prove"]
    assert streams["shortfalls"][0]["checked"] == 40
    assert streams["shortfalls"][0]["required"] == 100


def test_a_marker_only_archive_that_meets_the_threshold_still_passes(run_env):
    """The fallback reads real numbers — it does not simply block every legacy
    archive, which would be the same bug with the sign flipped."""
    project_root, fdir = run_env
    _write_spec(fdir, 100)
    _write_castings(fdir, ["src/api/login.py"])

    _write_legacy_marker(fdir, "prove", items_checked=100, items_total=100)
    _write_legacy_marker(fdir, "trace", items_checked=10, items_total=10)
    _write_legacy_marker(fdir, "test", items_checked=10, items_total=10)

    streams = _check_streams_complete(project_root)

    assert streams["complete"] is True, streams
    assert streams["shortfalls"] == []


def test_the_rollup_wins_over_the_marker_when_both_exist(run_env):
    """The marker is a FALLBACK, not a second opinion: a current cycle's roll-up
    is the authority even when a stale marker disagrees."""
    project_root, fdir = run_env
    _write_spec(fdir, 100)
    _write_castings(fdir, ["src/api/login.py"])

    foundry_mark_stream(
        "prove", cycle=0, items_checked=100, items_total=100, project_root=project_root
    )
    # Overwrite the marker the record just wrote with a short one.
    _write_legacy_marker(fdir, "prove", items_checked=5, items_total=100)

    assert _streams._coverage_shortfall(fdir, project_root, "prove", 0) is None


def test_trace_ratio_threshold_also_moved_to_the_streams_complete_check(run_env):
    """The TRACE >=95%-of-declared-symbols ratio had the same record-time
    refusal and moves for the same reason."""
    project_root, fdir = run_env
    _write_spec(fdir, 10)
    _write_castings(fdir, ["src/api/login.py"])

    result = foundry_mark_stream(
        "trace", cycle=0, items_checked=50, items_total=100, project_root=project_root
    )
    assert result.get("ok") is True, result

    streams = _check_streams_complete(project_root)
    assert "trace" in streams["missing"]
    assert [s["stream"] for s in streams["shortfalls"]] == ["trace"]


def test_a_shortfall_is_reported_on_the_record_call_without_refusing_it(run_env):
    """The lead should not have to wait for the streams-complete check to learn
    it is short — but being told is not the same as being refused."""
    project_root, fdir = run_env
    _write_spec(fdir, 100)

    result = foundry_mark_stream(
        "prove", cycle=0, items_checked=40, items_total=100, project_root=project_root
    )

    assert result["ok"] is True
    assert result["coverage_shortfall"]["stream"] == "prove"
    assert "must be" in result["warning"]


# --------------------------------------------------------------------------- #
# CT-003 — drop warnings compare cycle N to cycle N-1
# --------------------------------------------------------------------------- #


def test_drop_warning_compares_cycle_n_to_cycle_n_minus_one(run_env):
    """CT-003: 'drop warnings compare cycle N to N-1'."""
    project_root, fdir = run_env
    _write_spec(fdir, 10)

    _set_cycle(fdir, 0)
    foundry_mark_stream("trace", cycle=0, items_checked=100, project_root=project_root)

    _set_cycle(fdir, 1)
    result = foundry_mark_stream(
        "trace", cycle=1, items_checked=20, project_root=project_root
    )

    assert "Coverage dropped" in result.get("warning", "")
    assert "cycle 1" in result["warning"]
    assert "cycle 0" in result["warning"]


def test_a_second_tranche_of_the_same_cycle_is_not_a_drop(run_env):
    """The false positive the old marker comparison produced: a second partial
    record of the SAME cycle looked like a collapse, because 'the previous
    write of this file' is not 'the previous cycle'."""
    project_root, fdir = run_env
    _write_spec(fdir, 10)
    _set_cycle(fdir, 0)

    foundry_mark_stream("trace", cycle=0, items_checked=100, project_root=project_root)
    result = foundry_mark_stream(
        "trace", cycle=0, items_checked=5, project_root=project_root
    )

    assert "Coverage dropped" not in result.get("warning", "")


def test_no_drop_warning_when_the_previous_cycle_never_ran_the_stream(run_env):
    """Nothing to compare against is not a drop."""
    project_root, fdir = run_env
    _write_spec(fdir, 10)
    _set_cycle(fdir, 3)

    result = foundry_mark_stream(
        "trace", cycle=3, items_checked=1, project_root=project_root
    )

    assert "Coverage dropped" not in result.get("warning", "")


def test_cycle_totals_are_what_the_drop_compares_not_single_records(run_env):
    """Cycle 0 delivered as two tranches totalling 100 is not a drop when
    cycle 1 delivers 90 in one — comparing single records would say it was."""
    project_root, fdir = run_env
    _write_spec(fdir, 10)

    _set_cycle(fdir, 0)
    foundry_mark_stream("trace", cycle=0, items_checked=50, project_root=project_root)
    foundry_mark_stream("trace", cycle=0, items_checked=50, project_root=project_root)

    _set_cycle(fdir, 1)
    result = foundry_mark_stream(
        "trace", cycle=1, items_checked=90, project_root=project_root
    )

    assert "Coverage dropped" not in result.get("warning", "")


# --------------------------------------------------------------------------- #
# _prove_is_clean reads the cycle total (FR-014) and refuses a zero-requirement
# spec (FR-020 / AC-025)
# --------------------------------------------------------------------------- #


def test_prove_is_clean_reads_the_cycle_rollup_not_the_marker(run_env):
    """A PROVE run recorded twice in one cycle is judged on the cycle's roll-up.

    fallout AC-030 / OT-028 — AND THE ROLL-UP IS THE LAST RECORD.
    This drove two tranches summing to 100 and asserted the sum cleared 95%.
    That is the arithmetic the replace semantics removed, and removing it is the
    point: a stream re-run inside one cycle must not clear the threshold twice
    over the same work. The second record here states the run's own total (100
    of 100), which is what an agent re-recording actually reports.
    """
    project_root, fdir = run_env
    _write_spec(fdir, 100)

    foundry_mark_stream(
        "prove", cycle=0, items_checked=95, items_total=100, findings_count=0,
        project_root=project_root,
    )
    foundry_mark_stream(
        "prove", cycle=0, items_checked=100, items_total=100, findings_count=0,
        project_root=project_root,
    )

    assert _rollup_totals(fdir, 0, "prove")["items_checked"] == 100
    assert _prove_is_clean(fdir, project_root) is True


def test_a_re_record_cannot_clear_the_threshold_on_work_counted_twice(run_env):
    """fallout AC-030 / OT-028 — THE DEFECT THE REPLACE SEMANTICS CLOSE.

    Two records of 40 of 100 are one stream re-running, not 80 of 100. Under
    the summing arithmetic the pair reported 80% and a third such record
    reported 120% — coverage above its own denominator, which is the one
    direction a coverage check must never fail. The cycle reports 40 here, and
    `_prove_is_clean` refuses on it.
    """
    project_root, fdir = run_env
    _write_spec(fdir, 100)

    for _ in range(3):
        foundry_mark_stream(
            "prove", cycle=0, items_checked=40, items_total=100, findings_count=0,
            project_root=project_root,
        )

    totals = _rollup_totals(fdir, 0, "prove")
    assert totals["records"] == 3
    assert totals["items_checked"] == 40
    assert totals["items_checked"] <= totals["items_total"], totals
    assert _prove_is_clean(fdir, project_root) is False


def test_prove_is_not_clean_when_the_spec_parses_to_zero_requirements(run_env):
    """AC-025 / FR-020 — the auto-VERIFY hole.

    ``_prove_is_clean`` used to SKIP its coverage check when the spec parsed to
    zero requirements, so any ``.prove-complete`` with findings=0 drove the F4
    auto-pass. On an unresolvable or unparseable spec that manufactured a
    passing run out of a spec nothing had been proved against.
    """
    project_root, fdir = run_env
    (fdir / "spec.md").write_text("# Spec\n\nProse with no requirement IDs.\n", encoding="utf-8")

    foundry_mark_stream(
        "prove", cycle=0, items_checked=50, items_total=50, findings_count=0,
        project_root=project_root,
    )

    assert _prove_is_clean(fdir, project_root) is False


def test_prove_is_not_clean_with_no_spec_at_all(run_env):
    """Same hole via the other route: an unresolvable spec path also parses to
    zero requirements."""
    project_root, fdir = run_env
    assert not (fdir / "spec.md").exists()

    foundry_mark_stream(
        "prove", cycle=0, items_checked=50, items_total=50, findings_count=0,
        project_root=project_root,
    )

    assert _prove_is_clean(fdir, project_root) is False


def test_prove_with_findings_is_never_clean(run_env):
    """Preserved: a cycle carrying findings is not clean regardless of
    coverage."""
    project_root, fdir = run_env
    _write_spec(fdir, 10)

    foundry_mark_stream(
        "prove", cycle=0, items_checked=10, items_total=10, findings_count=2,
        project_root=project_root,
    )

    assert _prove_is_clean(fdir, project_root) is False


# --------------------------------------------------------------------------- #
# D-100 — a negative findings_count erases real findings
#
# TV-B-02: the counts accumulate by ADDITION across a cycle's tranches, and
# findings_count had no lower bound while items_checked <= 0 was already
# refused one line away. The asymmetry was load-bearing, not cosmetic: the
# cancellation flips a dirty stream clean, and on TRACE it stamps the anchor
# that lets a LATER cycle skip the stream entirely.
# --------------------------------------------------------------------------- #


def test_a_negative_findings_count_is_refused(run_env):
    project_root, fdir = run_env
    _write_spec(fdir, 10)

    result = foundry_mark_stream(
        "prove", cycle=0, items_checked=1, items_total=10,
        findings_count=-9, project_root=project_root,
    )

    assert "error" in result
    assert "findings_count=-9" in result["error"]
    assert "hint" in result


def test_the_refusal_names_the_offending_value_and_the_action(run_env):
    """The house refusal shape: `error` names the offending value, `hint` names
    the action. Same rung as the items_checked guard beside it."""
    project_root, fdir = run_env
    _write_spec(fdir, 10)

    negative = foundry_mark_stream(
        "prove", cycle=0, items_checked=1, findings_count=-1, project_root=project_root
    )
    zero_checked = foundry_mark_stream(
        "prove", cycle=0, items_checked=0, findings_count=1, project_root=project_root
    )

    for refusal in (negative, zero_checked):
        assert set(refusal) >= {"error", "hint"}
        assert "ok" not in refusal


def test_a_negative_findings_count_cannot_cancel_a_dirty_prove_clean(run_env):
    """The repro verbatim: mark_stream(prove, 2, findings=9) then
    (prove, 1, findings=-9) used to cancel the cycle's findings to 0 and flip
    _prove_is_clean False -> True."""
    project_root, fdir = run_env
    _write_spec(fdir, 2)

    foundry_mark_stream(
        "prove", cycle=0, items_checked=2, items_total=2,
        findings_count=9, project_root=project_root,
    )
    assert _prove_is_clean(fdir, project_root) is False

    cancel = foundry_mark_stream(
        "prove", cycle=0, items_checked=1, items_total=2,
        findings_count=-9, project_root=project_root,
    )

    assert "error" in cancel
    assert _rollup_totals(fdir, 0, "prove")["findings"] == 9
    assert _prove_is_clean(fdir, project_root) is False


def test_a_negative_findings_count_cannot_stamp_the_trace_skip_anchor(run_env):
    """The sharper edge of the same cancellation. .trace-clean-at is the anchor
    that lets a later cycle SKIP the TRACE stream, so forging it removes a
    whole verification stream from the run."""
    project_root, fdir = run_env

    foundry_mark_stream(
        "trace", cycle=0, items_checked=100, items_total=100,
        findings_count=4, project_root=project_root,
    )
    assert not (fdir / ".trace-clean-at").exists()

    foundry_mark_stream(
        "trace", cycle=0, items_checked=1, items_total=100,
        findings_count=-4, project_root=project_root,
    )

    assert not (fdir / ".trace-clean-at").exists()
    assert _rollup_totals(fdir, 0, "trace")["findings"] == 4


def test_checking_more_items_than_exist_is_refused(run_env):
    """The named near-miss beside the same guard: 1667% coverage was accepted
    in silence and trivially satisfied the >=95% gate."""
    project_root, fdir = run_env

    result = foundry_mark_stream(
        "trace", cycle=0, items_checked=100, items_total=6, project_root=project_root
    )

    assert "error" in result
    assert "items_checked=100" in result["error"]
    assert "items_total=6" in result["error"]


def test_a_negative_items_total_is_refused(run_env):
    project_root, fdir = run_env

    result = foundry_mark_stream(
        "trace", cycle=0, items_checked=5, items_total=-5, project_root=project_root
    )

    assert "error" in result
    assert "items_total=-5" in result["error"]


def test_partial_records_of_one_cycle_are_still_accepted(run_env):
    """The guards must not re-refuse what CT-003 requires be stored. Two
    records under the declared population stay legal, and so does one reporting
    zero findings.

    fallout AC-030 / OT-028: the cycle carries the SECOND record's numbers, not
    the pair's sum. What this test is about is unchanged — neither call is
    refused — and that is asserted on `ok` at both doors before the totals.
    """
    project_root, fdir = run_env
    _write_spec(fdir, 100)

    first = foundry_mark_stream(
        "prove", cycle=0, items_checked=40, items_total=100,
        findings_count=0, project_root=project_root,
    )
    second = foundry_mark_stream(
        "prove", cycle=0, items_checked=55, items_total=100,
        findings_count=3, project_root=project_root,
    )

    assert first.get("ok") is True, first
    assert second.get("ok") is True, second
    totals = _rollup_totals(fdir, 0, "prove")
    assert (totals["items_checked"], totals["findings"]) == (55, 3)
    assert totals["records"] == 2


def test_a_stream_with_no_declared_population_is_still_accepted(run_env):
    """items_total=0 means "no fixed denominator", not "checked more than
    exist" — the ratio guard must not fire on it."""
    project_root, fdir = run_env

    result = foundry_mark_stream(
        "sight", cycle=0, items_checked=12, items_total=0, project_root=project_root
    )

    assert result.get("ok") is True, result


# --------------------------------------------------------------------------- #
# D-103 — concurrent stream records must all survive
#
# TV-B-05: _save_json wrote a FIXED {stem}.tmp then renamed, with no lock, over
# a read-modify-write. A real 4-process x 40-call drive on foundry_mark_stream
# SILENTLY LOST 107 of 160 tranches (67%) and raised 98 FileNotFoundError as
# one process renamed the shared tmp out from under another.
#
# This is the DESIGNED path, not an edge case: F2 runs 4-8 parallel streams and
# each calls Foundry-Stream as it completes. CT-003 requires partial records be
# "accepted and stored as they arrive"; 67% loss violates it directly.
#
# Mirrors the defects-ledger concurrency test, and the discipline mirrors
# foundry.py#ledger_transaction (RLock + fcntl.flock) by CONVENTION — that
# primitive yields a list under a collection key, which does not fit
# stream-rollup.json, whose payload is the document itself.
# --------------------------------------------------------------------------- #


_CONCURRENCY_WORKERS = 4
_CALLS_PER_WORKER = 40


def _drive_mark_stream(project_root: str, run_name: str, worker: int) -> int:
    """Record _CALLS_PER_WORKER single-item tranches. Returns failures."""
    import sys

    sys.path.insert(0, "src")
    from foundry_mcp.tools import foundry_state
    from foundry_mcp.tools.orchestration.streams import foundry_mark_stream as mark

    foundry_state.set_active_run(run_name)
    failures = 0
    for _ in range(_CALLS_PER_WORKER):
        if mark("prove", cycle=0, items_checked=1, items_total=1000,
                findings_count=0, project_root=project_root).get("ok") is not True:
            failures += 1
    return failures


def test_concurrent_stream_records_are_not_lost(run_env):
    """Every tranche filed by every worker is stored, and none raises.

    The pre-fix numbers on this exact shape: 107/160 lost, 98 raised.
    """
    import multiprocessing as mp

    project_root, fdir = run_env
    _write_spec(fdir, 1000)
    run_name = fdir.name

    ctx = mp.get_context("fork")
    with ctx.Pool(_CONCURRENCY_WORKERS) as pool:
        failures = pool.starmap(
            _drive_mark_stream,
            [(project_root, run_name, w) for w in range(_CONCURRENCY_WORKERS)],
        )

    expected = _CONCURRENCY_WORKERS * _CALLS_PER_WORKER
    assert sum(failures) == 0, f"{sum(failures)} calls refused or raised"

    totals = _rollup_totals(fdir, 0, "prove")
    assert totals["records"] == expected, (
        f"{expected - totals['records']} of {expected} records lost"
    )

    # fallout AC-030 / OT-028 — THE HISTORY IS WHERE "NOT LOST" IS NOW READ.
    # This asserted `items_checked == 160`, which was the SUM of 160 one-item
    # records. Under replace semantics the top-level total is the last record's
    # single item, so the loss claim moves to the artifact `records[]` keeps —
    # which is where it always belonged, because it is the list a lost write
    # would be missing from. Read off the persisted document rather than off
    # the returned count, so a lock that dropped a write cannot be reported
    # complete by the same call that dropped it.
    assert totals["items_checked"] == 1, totals
    rollup = json.loads((fdir / "stream-rollup.json").read_text(encoding="utf-8"))
    stored = rollup["cycles"]["0"]["prove"]["records"]
    assert len(stored) == expected, (
        f"{expected - len(stored)} of {expected} records lost from the history"
    )
    assert {r["items_checked"] for r in stored} == {1}, stored


def test_no_shared_tmp_sidecar_survives_a_concurrent_drive(run_env):
    """The mechanism, not just the symptom: the tmp name must be unique per
    writer, and nothing may be left behind."""
    import multiprocessing as mp

    project_root, fdir = run_env
    _write_spec(fdir, 1000)
    run_name = fdir.name

    ctx = mp.get_context("fork")
    with ctx.Pool(_CONCURRENCY_WORKERS) as pool:
        pool.starmap(
            _drive_mark_stream,
            [(project_root, run_name, w) for w in range(_CONCURRENCY_WORKERS)],
        )

    assert list(fdir.glob("*.tmp")) == []
    assert (fdir / "stream-rollup.json").exists()


def test_a_transaction_that_changes_nothing_writes_nothing(run_env):
    """A no-op block must leave the artifact byte-identical — that is what
    keeps verdict synthesis from rewriting verdicts.json on a clean ASSAY, and
    it means a caller that only READ a corrupt artifact cannot replace it."""
    project_root, fdir = run_env
    path = fdir / "state.json"
    path.write_text('{"phase":"F3",   "cycle": 2}', encoding="utf-8")
    before = path.read_text(encoding="utf-8")

    with _artifacts._document_transaction(path) as doc:
        assert doc["cycle"] == 2

    assert path.read_text(encoding="utf-8") == before


def test_a_nested_transaction_on_one_path_does_not_deadlock(run_env):
    """foundry_mark_phase_complete nests a state.json write inside
    _update_phase's. Without per-path re-entrancy that blocks forever on our
    own flock; with it, both mutations land in ONE write."""
    project_root, fdir = run_env
    path = fdir / "state.json"

    with _artifacts._document_transaction(path) as outer:
        outer["phase"] = "F2"
        with _artifacts._document_transaction(path) as inner:
            assert inner is outer
            inner["cycle"] = 5

    assert json.loads(path.read_text()) == {"phase": "F2", "cycle": 5}


def test_a_failed_transaction_writes_nothing(run_env):
    """An exception inside the block must leave the artifact untouched, so a
    half-applied mutation cannot be persisted."""
    project_root, fdir = run_env
    path = fdir / "state.json"
    path.write_text(json.dumps({"phase": "F3", "cycle": 1}), encoding="utf-8")

    with pytest.raises(RuntimeError):
        with _artifacts._document_transaction(path) as doc:
            doc["cycle"] = 99
            raise RuntimeError("boom")

    assert json.loads(path.read_text())["cycle"] == 1


# --------------------------------------------------------------------------- #
# fallout AC-030 / OT-028 / CT-003 / ST-008 / ST-009 — REPLACE PER (STREAM,
# CYCLE), AND SAY SO AT THE DOOR.
#
# A-015: "Replace per (stream, cycle); the AGENT records; lead never does."
#
# The three totals were `+= items_checked`, `max(items_total)` and `+= findings`
# — the arithmetic for TRANCHES of one run. What actually happens is a stream
# RE-RUNNING inside one cycle: a re-dispatched TRACE, a PROVE the lead asked for
# again after a fix. Then 40 of 40 recorded twice reads as 80 of 40 and coverage
# passes 100%, so the threshold `_coverage_shortfall` evaluates is satisfied by
# the same work counted twice — the one direction a coverage check must never
# fail. That is the daring-orca defect this register closes.
#
# Pinned HERE, in the roll-up's own module, and not only at the door: the door
# reports `replaced` and the artifact holds the history, and a claim proved at
# only one of the two is a claim about the report rather than about the record.
# GI-006 names "a replace-semantics write that drops history" as the harm, so
# the `records[]` assertions are not optional garnish — they are the half that
# makes replacing safe.
# --------------------------------------------------------------------------- #


def test_the_first_record_for_a_stream_cycle_replaces_nothing(run_env):
    """ST-008: the first record for a (stream, cycle) pair carries no prior.

    `replaced` is None, not an empty dict and not an absent key. A caller reads
    "was there one?" off this field, and absent-versus-None is the distinction
    every degraded reader in this package gets wrong when the field is optional.
    """
    project_root, fdir = run_env
    _write_spec(fdir, 100)

    result = foundry_mark_stream(
        "prove", cycle=0, items_checked=40, items_total=100, findings_count=2,
        project_root=project_root,
    )

    assert result.get("ok") is True, result
    assert "replaced" in result, result
    assert result["replaced"] is None, result["replaced"]
    assert result["records_this_cycle"] == 1


def test_a_second_record_names_the_one_it_replaced(run_env):
    """ST-009 / CT-003: 'the result names what it replaced'.

    Named at the DOOR rather than left in the artifact, because a replacement
    the caller cannot see is a replacement it will make twice. The prior
    record's own numbers come back, so the agent can tell an intended re-record
    from an accidental second call on numbers it did not mean to move.
    """
    project_root, fdir = run_env
    _write_spec(fdir, 100)

    foundry_mark_stream(
        "prove", cycle=0, items_checked=40, items_total=100, findings_count=2,
        project_root=project_root,
    )
    second = foundry_mark_stream(
        "prove", cycle=0, items_checked=96, items_total=100, findings_count=0,
        project_root=project_root,
    )

    assert second.get("ok") is True, second
    replaced = second["replaced"]
    assert replaced is not None, second
    assert replaced["items_checked"] == 40, replaced
    assert replaced["items_total"] == 100, replaced
    assert replaced["findings"] == 2, replaced
    # And the cycle now reports the record that displaced it, not their sum.
    assert second["items_checked"] == 96
    assert second["findings"] == 0
    assert _rollup_totals(fdir, 0, "prove")["items_checked"] == 96


def test_the_replaced_record_is_a_snapshot_not_the_live_entry(run_env):
    """The prior record handed back must not move under a third write.

    `replaced` is captured BEFORE the append, and copied. A caller holding the
    dict from call two and reading it after call three would otherwise see call
    three's numbers — the aliasing bug this shape invites, asserted rather than
    assumed because nothing about the returned value announces which it is.
    """
    project_root, fdir = run_env
    _write_spec(fdir, 100)

    foundry_mark_stream(
        "prove", cycle=0, items_checked=10, items_total=100, project_root=project_root
    )
    second = foundry_mark_stream(
        "prove", cycle=0, items_checked=20, items_total=100, project_root=project_root
    )
    held = second["replaced"]
    assert held["items_checked"] == 10

    foundry_mark_stream(
        "prove", cycle=0, items_checked=30, items_total=100, project_root=project_root
    )

    assert held["items_checked"] == 10, held


def test_every_record_stays_in_the_history_oldest_first(run_env):
    """GI-006: replacing the totals must not drop what was replaced.

    "A replace-semantics write that drops history" is GI-006's own violation
    column. Every record ever written stays under `records[]` in the order it
    arrived; what changes is which of them the top-level fields report. A reader
    wanting the tranche history reads the list.
    """
    project_root, fdir = run_env
    _write_spec(fdir, 100)

    for checked in (11, 22, 33, 44):
        foundry_mark_stream(
            "prove", cycle=0, items_checked=checked, items_total=100,
            findings_count=checked % 3, project_root=project_root,
        )

    rollup = json.loads((fdir / "stream-rollup.json").read_text(encoding="utf-8"))
    stored = rollup["cycles"]["0"]["prove"]["records"]
    assert [r["items_checked"] for r in stored] == [11, 22, 33, 44], stored
    assert [r["findings"] for r in stored] == [2, 1, 0, 2], stored

    totals = _rollup_totals(fdir, 0, "prove")
    assert totals["records"] == 4
    assert totals["items_checked"] == 44
    assert totals["findings"] == 44 % 3


def test_a_replaced_record_does_not_disturb_another_stream_or_cycle(run_env):
    """The key is (stream, cycle), and replacement is scoped to that pair.

    A rewrite that replaced the CYCLE's bucket rather than the stream's entry
    inside it would pass every assertion above and silently erase the sibling
    streams the same cycle recorded — the adjacent path a keyed replace opens.
    """
    project_root, fdir = run_env
    _write_spec(fdir, 100)

    foundry_mark_stream(
        "trace", cycle=0, items_checked=30, items_total=30, project_root=project_root
    )
    _set_cycle(fdir, 1)
    foundry_mark_stream(
        "prove", cycle=1, items_checked=10, items_total=100, project_root=project_root
    )
    _set_cycle(fdir, 0)
    foundry_mark_stream(
        "prove", cycle=0, items_checked=40, items_total=100, project_root=project_root
    )
    foundry_mark_stream(
        "prove", cycle=0, items_checked=60, items_total=100, project_root=project_root
    )

    assert _rollup_totals(fdir, 0, "prove")["items_checked"] == 60
    assert _rollup_totals(fdir, 0, "trace")["items_checked"] == 30
    assert _rollup_totals(fdir, 1, "prove")["items_checked"] == 10


def test_no_cycles_coverage_can_exceed_its_own_population(run_env):
    """OT-028's arithmetic half, as the property rather than one drive.

    The summing shape produced 1667% in the wild and cleared every >=95% gate
    on the way. Driven over the whole stream vocabulary, ten records each, so
    the claim is about the RULE and not about the one stream that was noticed.
    Both halves: the persisted totals, and the percentage the door renders.
    """
    project_root, fdir = run_env
    _write_spec(fdir, 100)

    checked = 0
    for stream in sorted(_streams.VALID_STREAMS):
        for _ in range(10):
            result = foundry_mark_stream(
                stream, cycle=0, items_checked=90, items_total=100,
                project_root=project_root,
            )
            assert result.get("ok") is True, result
            assert result["coverage"] == "90%", result
            checked += 1

        totals = _rollup_totals(fdir, 0, stream)
        assert totals["records"] == 10, (stream, totals)
        assert totals["items_checked"] == 90, (stream, totals)
        assert totals["items_checked"] <= totals["items_total"], (stream, totals)

    # The scan must SEE its subject: an empty stream vocabulary would make every
    # assertion above vacuous, which is the failure mode of every derived drive.
    assert checked == 10 * len(_streams.VALID_STREAMS), checked
    assert checked > 0
