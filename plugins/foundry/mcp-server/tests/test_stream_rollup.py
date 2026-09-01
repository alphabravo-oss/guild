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
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from foundry_mcp.tools import foundry_orchestrator as fo
from foundry_mcp.tools import foundry_state
from foundry_mcp.tools.foundry_orchestrator import (
    _check_streams_complete,
    _prove_is_clean,
    _rollup_totals,
    foundry_mark_stream,
)


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

    monkeypatch.setattr(
        fo,
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


def test_two_partial_prove_records_in_one_cycle_are_both_stored(run_env):
    """OT-009 verbatim: 'Two partial PROVE records in one cycle are both
    stored, and the >=95% evaluation runs once against their cycle total.'"""
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
    assert totals["items_checked"] == 96      # 40 + 56, summed — neither lost
    assert totals["items_total"] == 100       # max, not 200 — same denominator

    rollup = json.loads((fdir / "stream-rollup.json").read_text(encoding="utf-8"))
    stored = rollup["cycles"]["0"]["prove"]["records"]
    assert [r["items_checked"] for r in stored] == [40, 56]


def test_findings_accumulate_across_tranches(run_env):
    """Each tranche reports the findings it produced; the cycle carries their
    sum, so a clean second tranche cannot erase a dirty first one."""
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

    assert _rollup_totals(fdir, 0, "prove")["findings"] == 3


def test_the_marker_carries_the_cycle_total_not_the_last_tranche(run_env):
    """The marker stays the completion signal, but its counts are now the
    cycle's totals — so anything still reading the marker (old archives, the
    status display) sees the whole cycle rather than the last write."""
    project_root, fdir = run_env
    _write_spec(fdir, 100)

    foundry_mark_stream(
        "prove", cycle=0, items_checked=40, items_total=100, project_root=project_root
    )
    foundry_mark_stream(
        "prove", cycle=0, items_checked=56, items_total=100, project_root=project_root
    )

    assert fo._marker_counts(fdir / ".prove-complete")["items_checked"] == 96


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


def test_a_later_tranche_clears_the_shortfall_the_earlier_one_caused(run_env):
    """The whole point of evaluating per cycle rather than per call: the second
    tranche completes the coverage the first one started, and the cycle passes.
    Under the old record-time check the first call was refused and the second
    never happened."""
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
        "prove", cycle=0, items_checked=56, items_total=100, project_root=project_root
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
    assert not (fdir / fo.ROLLUP_FILENAME).exists()

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

    assert fo._coverage_shortfall(fdir, project_root, "prove", 0) is None


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


def test_prove_is_clean_reads_the_cycle_total_not_the_last_tranche(run_env):
    """A PROVE run split into two clean tranches covering the whole spec is
    clean. Judged on the last tranche alone it would look like 5% coverage."""
    project_root, fdir = run_env
    _write_spec(fdir, 100)

    foundry_mark_stream(
        "prove", cycle=0, items_checked=95, items_total=100, findings_count=0,
        project_root=project_root,
    )
    foundry_mark_stream(
        "prove", cycle=0, items_checked=5, items_total=100, findings_count=0,
        project_root=project_root,
    )

    assert _prove_is_clean(fdir, project_root) is True


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


def test_partial_tranches_of_one_cycle_are_still_accepted(run_env):
    """The guards must not re-refuse what CT-003 requires be stored. Two
    tranches whose SUM is under the declared population stay legal, and so does
    a tranche reporting zero findings."""
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
    assert (totals["items_checked"], totals["findings"]) == (95, 3)


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
    from foundry_mcp.tools.foundry_orchestrator import foundry_mark_stream as mark

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
        f"{expected - totals['records']} of {expected} tranches lost"
    )
    assert totals["items_checked"] == expected


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

    with fo._document_transaction(path) as doc:
        assert doc["cycle"] == 2

    assert path.read_text(encoding="utf-8") == before


def test_a_nested_transaction_on_one_path_does_not_deadlock(run_env):
    """foundry_mark_phase_complete nests a state.json write inside
    _update_phase's. Without per-path re-entrancy that blocks forever on our
    own flock; with it, both mutations land in ONE write."""
    project_root, fdir = run_env
    path = fdir / "state.json"

    with fo._document_transaction(path) as outer:
        outer["phase"] = "F2"
        with fo._document_transaction(path) as inner:
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
        with fo._document_transaction(path) as doc:
            doc["cycle"] = 99
            raise RuntimeError("boom")

    assert json.loads(path.read_text())["cycle"] == 1
