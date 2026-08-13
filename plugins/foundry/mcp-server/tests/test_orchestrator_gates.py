"""Regression tests for Casting C3 — orchestrator gate fixes (NFR-001).

Two behavioral areas, one test (or more) per acceptance criterion:

P3 (FR-003 / FR-004 / ST-001) — verdict synthesis on clean PROVE:
  the F4 router's auto-pass path synthesizes a VERIFIED verdict for every
  spec requirement ID into verdicts.json BEFORE emitting the auto-pass, so
  the DONE gate's ``verdict_coverage`` read is N/N rather than 0/N.

P4 (FR-005 / FR-008 / ST-002) — passing-gate guidance advance + token decouple:
  a passing Foundry-Gate advances the guidance state (next Foundry-Next
  emits the transition step, not a re-run of the satisfied gate); and the
  ``.next-action-called`` ordering token is decoupled from the stall clock
  (now ``.last-next-at``) so gate/phase consumption and read-only intervening
  calls do not blind the watchdog, while real stalls still warn.

NFR-002 — no regression: the refactored ``_count_spec_requirements`` counts
identically, and synthesis is a no-op when verdicts are already complete.

US-004 (AC-013 / AC-014) — every INSPECT stream is recordable:
  ``research_audit`` and ``flow_trace`` join the ``Foundry-Stream`` valid set
  (recordable, NOT required), the invalid-stream error lists all seven names
  sorted, the MCP tool's JSON-Schema enum and the runtime guard agree exactly,
  and state written by the old five-name build still loads and gates
  identically.

All tests are hermetic: ``_check_active_teams`` is monkeypatched inactive so
no test depends on the ambient tmux session or ~/.claude/teams state.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from foundry_mcp.tools import foundry_orchestrator as fo
from foundry_mcp.tools import foundry_state
from foundry_mcp.tools.foundry_orchestrator import (
    _compute_next_action,
    _count_spec_requirements,
    _prove_is_clean,
    _spec_requirement_ids,
    _synthesize_clean_prove_verdicts,
    foundry_gate,
    foundry_mark_stream,
    foundry_next_action,
)


# --------------------------------------------------------------------------- #
# Fixtures & helpers
# --------------------------------------------------------------------------- #


@pytest.fixture
def run_env(tmp_path, monkeypatch):
    """Activate a foundry run under tmp_path; yield (project_root, fdir).

    Patches ``_check_active_teams`` inactive so gate/router logic never
    depends on the ambient tmux session or ~/.claude/teams directories.
    """
    project_root = tmp_path
    run_name = "c3-test-run"
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


def _write_spec(fdir: Path, ids: list[str]) -> None:
    body = "\n".join(f"- {rid}: synthesized requirement for testing" for rid in ids)
    (fdir / "spec.md").write_text(f"# Spec\n{body}\n", encoding="utf-8")


def _write_state(fdir: Path, phase: str = "F4", **extra) -> None:
    state = {"phase": phase}
    state.update(extra)
    (fdir / "state.json").write_text(json.dumps(state), encoding="utf-8")


def _write_prove(fdir: Path, items_checked: int, items_total: int, findings: int) -> None:
    (fdir / ".prove-complete").write_text(
        "2020-01-01T00:00:00+00:00 cycle=1\n"
        f"items_checked={items_checked}\n"
        f"items_total={items_total}\n"
        "coverage=100%\n"
        f"findings={findings}\n",
        encoding="utf-8",
    )


def _write_verdicts(fdir: Path, rows: list[dict]) -> None:
    (fdir / "verdicts.json").write_text(
        json.dumps({"requirements": rows}, indent=2), encoding="utf-8"
    )


def _arm_ordering_token(fdir: Path) -> None:
    """Simulate a preceding Foundry-Next so a gate's ordering check passes."""
    (fdir / ".next-action-called").write_text(f"{fo._now()}\n", encoding="utf-8")


# --------------------------------------------------------------------------- #
# P3 — verdict synthesis on clean PROVE (FR-003 / FR-004 / ST-001)
# --------------------------------------------------------------------------- #


def test_clean_prove_autopass_synthesizes_verified_verdict_per_id(run_env):
    """AC FR-004: clean-PROVE auto-pass writes one VERIFIED row per spec ID."""
    project_root, fdir = run_env
    ids = ["FR-1", "FR-2", "US-3", "AC-4"]
    _write_spec(fdir, ids)
    _write_state(fdir, phase="F4", temper=False)
    _write_prove(fdir, items_checked=len(ids), items_total=len(ids), findings=0)
    # verdicts.json does not exist yet — .prove-complete stores only aggregates.
    assert not (fdir / "verdicts.json").exists()

    result = _compute_next_action(project_root)

    assert result["action"] == "transition_to_done"
    verdicts = json.loads((fdir / "verdicts.json").read_text(encoding="utf-8"))
    got = {r["id"]: r["verdict"] for r in verdicts["requirements"]}
    assert set(got) == set(ids)
    assert all(v == "VERIFIED" for v in got.values())


def test_done_gate_verdict_coverage_passes_after_synthesis(run_env):
    """AC FR-003 (end-to-end): after the auto-pass synthesis, the DONE gate's
    ``verdict_coverage`` check reads N/N and the gate passes."""
    project_root, fdir = run_env
    ids = ["FR-1", "FR-2", "US-3"]
    _write_spec(fdir, ids)
    _write_state(fdir, phase="F4")
    _write_prove(fdir, len(ids), len(ids), 0)

    # Router auto-pass path synthesizes verdicts.
    _compute_next_action(project_root)

    _arm_ordering_token(fdir)
    gate = foundry_gate("done", project_root)

    assert gate["passed"] is True, gate
    coverage = [c for c in gate["checklist"] if c["check"].startswith("verdict_coverage")]
    assert coverage and coverage[0]["ok"] is True, gate["checklist"]
    non_verified = [c for c in gate["checklist"] if c["check"].startswith("all_verified")]
    assert non_verified and non_verified[0]["ok"] is True


def test_synthesized_rows_match_foundry_verdict_schema_and_preserve_existing(run_env):
    """AC FR-004 (schema + dedup): synthesized rows carry the Foundry-Verdict
    schema keys, existing real verdicts are never overwritten, and no ID is
    duplicated."""
    project_root, fdir = run_env
    ids = ["FR-1", "FR-2"]
    _write_spec(fdir, ids)
    _write_state(fdir, phase="F4")
    _write_prove(fdir, len(ids), len(ids), 0)

    # A real ASSAY verdict already exists for FR-1 with rich evidence.
    real_row = {
        "id": "FR-1",
        "verdict": "VERIFIED",
        "evidence": "Real ASSAY evidence — do not overwrite.",
        "spec_text_cited": "FR-1 spec text",
        "code_location": "src/foo.py:10",
        "cycle": 2,
        "recorded_at": "2020-01-01T00:00:00+00:00",
    }
    _write_verdicts(fdir, [real_row])

    count = _synthesize_clean_prove_verdicts(fdir, project_root, cycle=1)

    assert count == 1  # only FR-2 added; FR-1 skipped
    verdicts = json.loads((fdir / "verdicts.json").read_text(encoding="utf-8"))
    rows = verdicts["requirements"]
    ids_present = [r["id"] for r in rows]
    assert ids_present.count("FR-1") == 1  # no duplicate
    assert set(ids_present) == {"FR-1", "FR-2"}

    fr1 = next(r for r in rows if r["id"] == "FR-1")
    assert fr1 == real_row  # untouched, evidence preserved

    fr2 = next(r for r in rows if r["id"] == "FR-2")
    for key in ("id", "verdict", "evidence", "spec_text_cited", "code_location",
                "cycle", "recorded_at"):
        assert key in fr2, f"synthesized row missing schema key: {key}"
    assert fr2["verdict"] == "VERIFIED"


def test_no_synthesis_when_prove_has_findings(run_env):
    """AC (P3 guard): PROVE with >0 findings is NOT clean → no fabrication."""
    project_root, fdir = run_env
    ids = ["FR-1", "FR-2"]
    _write_spec(fdir, ids)
    _write_state(fdir, phase="F4")
    _write_prove(fdir, items_checked=len(ids), items_total=len(ids), findings=3)

    assert _prove_is_clean(fdir, project_root) is False
    _compute_next_action(project_root)
    # No verdicts synthesized — verdicts.json stays absent/empty.
    verdicts = fo._load_json(fdir / "verdicts.json")
    assert verdicts.get("requirements", []) == []


def test_no_synthesis_when_prove_coverage_below_threshold(run_env):
    """AC (P3 guard): PROVE below 95% coverage is NOT clean → no fabrication."""
    project_root, fdir = run_env
    ids = ["FR-1", "FR-2", "US-3", "AC-4", "VC-5"]  # 5 requirements
    _write_spec(fdir, ids)
    _write_state(fdir, phase="F4")
    # Only 2/5 = 40% checked, well below 95%.
    _write_prove(fdir, items_checked=2, items_total=5, findings=0)

    assert _prove_is_clean(fdir, project_root) is False
    _compute_next_action(project_root)
    verdicts = fo._load_json(fdir / "verdicts.json")
    assert verdicts.get("requirements", []) == []


# --------------------------------------------------------------------------- #
# P4 — passing-gate guidance advance (FR-005 / ST-002)
# --------------------------------------------------------------------------- #


def test_passing_gate_advances_guidance_state(run_env):
    """AC FR-005 / ST-002: a passing Foundry-Gate advances the guidance state
    so the NEXT Foundry-Next surfaces the gate as satisfied (proceed to the
    transition step) instead of re-instructing the now-satisfied gate."""
    project_root, fdir = run_env
    ids = ["FR-1", "FR-2"]
    _write_spec(fdir, ids)
    _write_state(fdir, phase="F4")
    _write_verdicts(
        fdir,
        [
            {"id": rid, "verdict": "VERIFIED", "evidence": "", "spec_text_cited": "",
             "code_location": "", "cycle": 1, "recorded_at": "2020-01-01T00:00:00+00:00"}
            for rid in ids
        ],
    )

    # Before the gate passes: no gate_advanced signal.
    first = foundry_next_action(project_root)
    assert first["action"] == "transition_to_done"
    assert "gate_advanced" not in first
    assert "ALREADY" not in first["instructions"]

    # Pass the DONE gate (foundry_next_action armed the ordering token).
    gate = foundry_gate("done", project_root)
    assert gate["passed"] is True, gate
    assert (fdir / ".gate-passed").exists()

    # After the gate passes: the next Foundry-Next advances guidance.
    second = foundry_next_action(project_root)
    assert second.get("gate_advanced", {}).get("passed_gate") == "done"
    assert "ALREADY PASSED" in second["instructions"]


def test_phase_advance_clears_gate_passed_marker(run_env):
    """AC ST-002: a real phase advance (_update_phase) clears the gate-passed
    marker so guidance does not get stuck on a stale 'already passed' note."""
    project_root, fdir = run_env
    _write_state(fdir, phase="F4")
    (fdir / ".gate-passed").write_text(
        json.dumps({"phase": "done", "at": fo._now()}), encoding="utf-8"
    )

    fo._update_phase(fdir, "F6")

    assert not (fdir / ".gate-passed").exists()


# --------------------------------------------------------------------------- #
# P4 — ordering-token / stall-clock decouple (FR-005 / FR-008)
# --------------------------------------------------------------------------- #


def test_stall_clock_decoupled_from_ordering_token(run_env):
    """AC FR-005 (decouple): the stall clock reads ``.last-next-at`` — which
    gate/phase never unlink — so a consumed ordering token does NOT blind the
    watchdog. A large gap still warns even when ``.next-action-called`` is
    gone."""
    project_root, fdir = run_env
    _write_state(fdir, phase="F0")

    # Simulate: a prior Foundry-Next stamped .last-next-at 200s ago, and an
    # intervening gate/phase consumed (unlinked) the ordering token.
    old = (datetime.now(timezone.utc) - timedelta(seconds=200)).isoformat()
    (fdir / ".last-next-at").write_text(f"{old}\n", encoding="utf-8")
    assert not (fdir / ".next-action-called").exists()

    result = foundry_next_action(project_root)

    assert result.get("stall_detected_seconds", 0) >= 180
    assert "STALL DETECTED" in result["instructions"]
    # Both markers are (re)written by Foundry-Next.
    assert (fdir / ".next-action-called").exists()
    assert (fdir / ".last-next-at").exists()


def test_readonly_intervening_call_does_not_reset_ordering(run_env):
    """AC FR-005: an intervening read-only Foundry-Stream call does not touch
    the ordering token or the stall timestamp, so ordering survives and a
    subsequent gate's ordering check still passes."""
    project_root, fdir = run_env
    manifest = {"castings": [{"id": 1, "title": "c1", "key_files": ["a.py"]}]}
    (fdir / "castings" / "manifest.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )
    _write_state(fdir, phase="F0")

    foundry_next_action(project_root)
    token_before = (fdir / ".next-action-called").read_text(encoding="utf-8")
    stall_before = (fdir / ".last-next-at").read_text(encoding="utf-8")

    # Read-only intervening call.
    stream = foundry_mark_stream("test", cycle=1, items_checked=5, project_root=project_root)
    assert stream.get("ok") is True

    # Neither marker was reset by the read-only call.
    assert (fdir / ".next-action-called").read_text(encoding="utf-8") == token_before
    assert (fdir / ".last-next-at").read_text(encoding="utf-8") == stall_before

    # The gate's ordering check still passes (token intact → not the
    # "Must call Foundry-Next before any gate check" rejection).
    gate = foundry_gate("cast", project_root)
    assert gate.get("reason", "") != "Must call Foundry-Next before any gate check"


def test_real_stall_still_warns(run_env):
    """AC FR-008: a genuine >180s gap between Foundry-Next calls warns."""
    project_root, fdir = run_env
    _write_state(fdir, phase="F0")

    old = (datetime.now(timezone.utc) - timedelta(seconds=240)).isoformat()
    (fdir / ".last-next-at").write_text(f"{old}\n", encoding="utf-8")
    (fdir / ".next-action-called").write_text(f"{old}\n", encoding="utf-8")

    result = foundry_next_action(project_root)

    assert result.get("stall_detected_seconds", 0) >= 180
    assert "STALL DETECTED" in result["instructions"]


def test_no_false_stall_on_recent_activity(run_env):
    """AC FR-008 (no false positive): back-to-back Foundry-Next calls with a
    tiny gap do NOT warn."""
    project_root, fdir = run_env
    _write_state(fdir, phase="F0")

    foundry_next_action(project_root)  # stamps .last-next-at = now
    result = foundry_next_action(project_root)  # gap ~0s

    assert "stall_detected_seconds" not in result
    assert "STALL DETECTED" not in result["instructions"]


# --------------------------------------------------------------------------- #
# NFR-002 — no regression to the count / normal-run behavior
# --------------------------------------------------------------------------- #


def test_count_spec_requirements_dedups_after_refactor(run_env):
    """NFR-002: the refactored counter still returns the count of UNIQUE IDs,
    and the new id-list helper returns the sorted unique set."""
    project_root, fdir = run_env
    # FR-1 appears twice; the set-dedup must collapse it.
    (fdir / "spec.md").write_text(
        "# Spec\n- FR-1 first mention\n- FR-1 again\n- US-2\n- NFR-3\n",
        encoding="utf-8",
    )
    assert _count_spec_requirements(project_root) == 3
    assert _spec_requirement_ids(project_root) == ["FR-1", "NFR-3", "US-2"]


def test_synthesis_is_noop_when_verdicts_already_complete(run_env):
    """NFR-002: on a normal ASSAY run where verdicts already cover every ID,
    synthesis adds nothing and leaves verdicts.json byte-for-byte unchanged."""
    project_root, fdir = run_env
    ids = ["FR-1", "FR-2"]
    _write_spec(fdir, ids)
    _write_prove(fdir, len(ids), len(ids), 0)
    _write_verdicts(
        fdir,
        [
            {"id": rid, "verdict": "VERIFIED", "evidence": "real", "spec_text_cited": "",
             "code_location": "", "cycle": 1, "recorded_at": "2020-01-01T00:00:00+00:00"}
            for rid in ids
        ],
    )
    before = (fdir / "verdicts.json").read_text(encoding="utf-8")

    count = _synthesize_clean_prove_verdicts(fdir, project_root, cycle=1)

    assert count == 0
    assert (fdir / "verdicts.json").read_text(encoding="utf-8") == before


# --------------------------------------------------------------------------- #
# US-004 — every INSPECT stream is recordable (AC-013 / AC-014)
# --------------------------------------------------------------------------- #

# The closed seven-name vocabulary AC-013 lands on. No eighth name (notably
# NOT coverage_diff — never accepted by the runtime, not added by AC-013).
EXPECTED_STREAMS = {
    "trace", "prove", "sight", "test", "probe", "research_audit", "flow_trace",
}

OLD_FIVE = ["trace", "prove", "sight", "test", "probe"]


def _old_marker_body(items_checked: int = 10, items_total: int = 10) -> str:
    """Byte-format the five-name build wrote — AC-014's load-compat target."""
    return (
        "2020-01-01T00:00:00+00:00 cycle=1\n"
        f"items_checked={items_checked}\n"
        f"items_total={items_total}\n"
        "coverage=100%\n"
        "findings=0\n"
    )


def test_valid_streams_vocabulary_is_exactly_seven():
    """AC-013 (closed vocabulary): the valid set is exactly the seven names."""
    assert set(fo.VALID_STREAMS) == EXPECTED_STREAMS


def test_research_audit_stream_recordable(run_env):
    """AC-013: recording research_audit succeeds instead of 'Invalid stream'."""
    project_root, fdir = run_env
    result = foundry_mark_stream(
        "research_audit", cycle=1, items_checked=7, project_root=project_root
    )
    assert result.get("ok") is True, result
    assert result["stream"] == "research_audit"
    assert (fdir / ".research_audit-complete").exists()


def test_flow_trace_stream_recordable_writes_marker(run_env):
    """AC-013: recording flow_trace succeeds and writes .flow_trace-complete."""
    project_root, fdir = run_env
    result = foundry_mark_stream(
        "flow_trace", cycle=1, items_checked=4, project_root=project_root
    )
    assert result.get("ok") is True, result
    marker = fdir / ".flow_trace-complete"
    assert marker.exists()
    assert "items_checked=4" in marker.read_text(encoding="utf-8")


def test_all_seven_streams_recordable(run_env):
    """AC-013 + AC-014: every name in the valid set records ok, and the five
    pre-existing names produce byte-identical marker filenames."""
    project_root, fdir = run_env
    for stream in sorted(fo.VALID_STREAMS):
        result = foundry_mark_stream(
            stream, cycle=1, items_checked=3, project_root=project_root
        )
        assert result.get("ok") is True, (stream, result)
        assert (fdir / f".{stream}-complete").exists()
    for old in OLD_FIVE:
        assert (fdir / f".{old}-complete").exists()


def test_invalid_stream_error_lists_all_seven_sorted(run_env):
    """AC-013: an unknown stream errors with the sorted seven-name list."""
    project_root, _fdir = run_env
    result = foundry_mark_stream(
        "bogus", cycle=1, items_checked=1, project_root=project_root
    )
    assert "error" in result
    assert (
        "flow_trace, probe, prove, research_audit, sight, test, trace"
        in result["error"]
    )


def test_zero_items_hint_enumerates_all_seven_streams(run_env):
    """key_link 5: the items_checked<=0 hint prose names a counting unit for
    every stream in the enum it sits beside — including the two new names."""
    project_root, _fdir = run_env
    result = foundry_mark_stream(
        "research_audit", cycle=1, items_checked=0, project_root=project_root
    )
    assert "error" in result
    for stream in EXPECTED_STREAMS:
        assert f"{stream}:" in result["error"], f"hint missing unit for {stream}"


def test_old_five_name_state_still_loads_and_gates_identically(run_env):
    """AC-014: markers written by the old five-name build still load (the
    coverage-drop parse works) and _check_streams_complete gates identically —
    required stays [trace, prove, test, sight] with no manifest, and the two
    new names are recordable, never required."""
    project_root, fdir = run_env
    # A run directory left behind by the old build: old-format markers only.
    for old in ["trace", "prove", "test", "sight"]:
        (fdir / f".{old}-complete").write_text(
            _old_marker_body(items_checked=10), encoding="utf-8"
        )

    streams = fo._check_streams_complete(project_root)
    assert streams["complete"] is True, streams
    assert streams["required"] == ["trace", "prove", "test", "sight"]
    assert "research_audit" not in streams["required"]
    assert "flow_trace" not in streams["required"]

    # Re-recording over an old-format marker parses its items_checked= line:
    # 3 < 10 * 0.7 triggers the coverage-drop warning (proof the old body
    # loaded). items_total=0 keeps the trace 95% guard out of the way.
    result = foundry_mark_stream(
        "trace", cycle=2, items_checked=3, items_total=0, project_root=project_root
    )
    assert result.get("ok") is True, result
    assert "Coverage dropped" in result.get("warning", "")


def test_new_streams_recordable_but_not_required(run_env):
    """NFR-002: recording both new streams does not alter the required set or
    satisfy the gate — recordable is not required."""
    project_root, fdir = run_env
    for new in ["research_audit", "flow_trace"]:
        result = foundry_mark_stream(
            new, cycle=1, items_checked=2, project_root=project_root
        )
        assert result.get("ok") is True, result

    streams = fo._check_streams_complete(project_root)
    assert streams["complete"] is False
    assert streams["required"] == ["trace", "prove", "test", "sight"]


def test_tool_schema_enum_matches_runtime_valid_set():
    """must_have truth 5: the Foundry-Stream JSON-Schema enum and the runtime
    guard accept exactly the same seven names — nothing advertised that the
    runtime rejects (the AC-013 defect), and nothing accepted but hidden."""
    from foundry_mcp import server as foundry_server

    tools = asyncio.run(foundry_server.list_tools())
    stream_tool = next(t for t in tools if t.name == "Foundry-Stream")
    enum = stream_tool.inputSchema["properties"]["stream"]["enum"]
    assert set(enum) == set(fo.VALID_STREAMS)
    assert enum == sorted(fo.VALID_STREAMS)


def test_grind_start_clears_all_seven_stream_markers(run_env):
    """Honest completion state across GRIND cycles: grind_start clears every
    recordable stream's marker — including the two new names — so no stale
    'complete' survives into the next INSPECT. Required set is untouched."""
    project_root, fdir = run_env
    _write_state(fdir, phase="F3")
    for stream in sorted(fo.VALID_STREAMS):
        (fdir / f".{stream}-complete").write_text(
            _old_marker_body(), encoding="utf-8"
        )

    _arm_ordering_token(fdir)
    result = fo.foundry_mark_phase_complete("grind_start", project_root)

    assert result.get("ok") is True, result
    for stream in fo.VALID_STREAMS:
        assert not (fdir / f".{stream}-complete").exists(), stream
