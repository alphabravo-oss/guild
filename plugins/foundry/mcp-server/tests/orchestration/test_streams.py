"""The per-cycle stream roll-up and the streams-complete check.

Carved from `tests/test_orchestrator_gates.py` (fallout FR-005 / GI-026 /
AC-014 / OT-016): one test module per shipped orchestration module, landed in
the same casting as the source move so no pin is ever left pointing at a module
that no longer exists.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path


from foundry_mcp.schemas import vocab
from foundry_mcp.tools import artifacts

# fallout FR-004 / AC-013 — THE MODULE OBJECTS, UNDER UNDERSCORE ALIASES.
#
# `streams`, `spend`, `width`, `gates`, `directives` and `teams` are all LOCAL
# variable names somewhere in this suite, and a local rebinding shadows a
# module for the rest of its function. The aliases are what `ORCHESTRATION`,
# `owning_module` and every `monkeypatch.setattr` resolve through; individual
# SYMBOLS are imported by name below, which is how the carved modules read.
from foundry_mcp.tools.orchestration import streams as _streams
from foundry_mcp.tools.orchestration import width as _width

# fallout AC-014 — THE TWO SIBLING SUITES THE CARVE MUST KEEP REACHING.
#
# `test_observations` owns the never-demote corpus and `test_spawn_progress`
# owns the shipped-source-tree derivation. Both are imported rather than copied,
# for the reason the monolith imported them: a parity test that owned its own
# copy of the corpus would keep passing while the two corpora drifted, and two
# scans over "the shipped source" must not be able to disagree about what that
# is. If either renames a symbol the ImportError says so by name, which is the
# loud failure rather than the silent one.

# fallout FR-004 / AC-014 (D-183) — THE ROSTER AND ITS HELPERS COME FROM
# `tests/orchestration/_env.py`, WHICH IS THE ONE PLACE THEY ARE STATED.
#
# This module carried its own byte-identical copy of a hand-typed thirteen-tuple
# and of `orchestration_source`, `owning_module`, `patch_everywhere` and
# `orchestration_has`. Fourteen copies of one roster is fourteen places to
# forget a module, and `keyfiles.py` — shipped in cycle 5 — was forgotten in
# every one of them: `owning_module` answered the IMPORTING module for
# `covers_path` and raised for `owning_entries`, and `patch_everywhere` could
# not reach a binding inside it. The roster is derived from the package
# directory now, so there is one of it and it cannot go stale.

from tests.orchestration._env import (  # noqa: F401
    _old_marker_body,
    _write_manifest_with_castings,
    _write_prove,
    _write_spec,
    _write_state,
    run_env,
)

from foundry_mcp.tools.orchestration.gates import (  # noqa: F401
    foundry_gate,
)

from foundry_mcp.tools.orchestration.guidance import (  # noqa: F401
    _compute_next_action,
    foundry_next_action,
)

from foundry_mcp.tools.orchestration.streams import (  # noqa: F401
    ROLLUP_FILENAME,
    ROSTER_MISMATCH,
    VALID_STREAMS,
    _check_streams_complete,
    _marker_counts,
    _prove_is_clean,
    foundry_mark_stream,
)

from foundry_mcp.tools.orchestration.teams import (  # noqa: F401
    _check_sight_required,
)

from foundry_mcp.tools.orchestration.guidance import (  # noqa: F401
    _stamp_trace_skip,
)

from tests.orchestration._env import (  # noqa: F401
    EXPECTED_STREAMS,
    OLD_FIVE,
    PRE_FR013_EIGHT,
)




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
    verdicts = artifacts._load_json(fdir / "verdicts.json")
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
    verdicts = artifacts._load_json(fdir / "verdicts.json")
    assert verdicts.get("requirements", []) == []




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
    stream = foundry_mark_stream(
        "test", cycle=1, items_checked=5, items_total=5, project_root=project_root
    )
    assert stream.get("ok") is True

    # Neither marker was reset by the read-only call.
    assert (fdir / ".next-action-called").read_text(encoding="utf-8") == token_before
    assert (fdir / ".last-next-at").read_text(encoding="utf-8") == stall_before

    # The gate's ordering check still passes (token intact → not the
    # "Must call Foundry-Next before any gate check" rejection).
    gate = foundry_gate("cast", project_root)
    assert gate.get("reason", "") != "Must call Foundry-Next before any gate check"




def test_valid_streams_reads_the_canonical_vocabulary():
    """AC-013 + D-008 + FR-013: the orchestrator's valid set is no longer a
    declaration, it IS the canonical vocabulary — so the two cannot drift.

    NFR-002: nothing the pre-FR-013 build accepted was dropped, and `test01`
    (AC-018) is the addition.
    """
    assert VALID_STREAMS is vocab.STREAM_WIRE_IDS
    assert PRE_FR013_EIGHT <= set(VALID_STREAMS), (
        f"narrowed: {PRE_FR013_EIGHT - set(VALID_STREAMS)}"
    )
    assert "test01" in VALID_STREAMS




def test_research_audit_stream_recordable(run_env):
    """AC-013: recording research_audit succeeds instead of 'Invalid stream'."""
    project_root, fdir = run_env
    result = foundry_mark_stream(
        "research_audit", cycle=1, items_checked=7, items_total=7,
        project_root=project_root
    )
    assert result.get("ok") is True, result
    assert result["stream"] == "research_audit"
    assert (fdir / ".research_audit-complete").exists()




def test_flow_trace_stream_recordable_writes_marker(run_env):
    """AC-013: recording flow_trace succeeds and writes .flow_trace-complete."""
    project_root, fdir = run_env
    result = foundry_mark_stream(
        "flow_trace", cycle=1, items_checked=4, items_total=4,
        project_root=project_root
    )
    assert result.get("ok") is True, result
    marker = fdir / ".flow_trace-complete"
    assert marker.exists()
    assert "items_checked=4" in marker.read_text(encoding="utf-8")




def test_coverage_diff_stream_recordable_writes_marker(run_env):
    """D-008: recording coverage_diff succeeds instead of 'Invalid stream'
    (the tool the next-action guidance names accepts the stream the guidance
    names) and writes .coverage_diff-complete."""
    project_root, fdir = run_env
    result = foundry_mark_stream(
        "coverage_diff", cycle=1, items_checked=9, items_total=9,
        project_root=project_root
    )
    assert result.get("ok") is True, result
    assert result["stream"] == "coverage_diff"
    marker = fdir / ".coverage_diff-complete"
    assert marker.exists()
    assert "items_checked=9" in marker.read_text(encoding="utf-8")




def test_every_stream_in_the_vocabulary_is_recordable(run_env):
    """AC-013 + AC-014 + D-008 + AC-018: every name in the valid set records
    ok, and the five pre-existing names produce byte-identical marker
    filenames."""
    project_root, fdir = run_env
    for stream in sorted(VALID_STREAMS):
        result = foundry_mark_stream(
            stream, cycle=1, items_checked=3, items_total=3,
            project_root=project_root
        )
        assert result.get("ok") is True, (stream, result)
        assert (fdir / f".{stream}-complete").exists()
    for old in OLD_FIVE:
        assert (fdir / f".{old}-complete").exists()




def test_invalid_stream_error_lists_the_whole_sorted_vocabulary(run_env):
    """AC-013 + D-008 + AC-018: an unknown stream errors with the sorted list
    of every legal name, derived from the guard's own set — never coerced onto
    a known stream."""
    project_root, _fdir = run_env
    result = foundry_mark_stream(
        "bogus", cycle=1, items_checked=1, project_root=project_root
    )
    assert "error" in result
    assert ", ".join(sorted(vocab.STREAM_WIRE_IDS)) in result["error"]
    assert "test01" in result["error"]




def test_zero_items_hint_enumerates_every_stream(run_env):
    """key_link 5: the items_checked<=0 hint prose names a counting unit for
    every stream in the enum it sits beside — including all three new names."""
    project_root, _fdir = run_env
    result = foundry_mark_stream(
        "research_audit", cycle=1, items_checked=0, project_root=project_root
    )
    assert "error" in result
    for stream in EXPECTED_STREAMS:
        assert f"{stream}:" in result["error"], f"hint missing unit for {stream}"




def test_old_marker_state_still_loads_and_gates(run_env):
    """AC-014: markers written by the pre-FR-013 build still load and
    _check_streams_complete still gates on them, and the streams added since
    are recordable but never required.

    Two claims from the original version of this test moved on purpose:

    - ``sight`` is no longer in ``required`` here. FR-020 / AC-025: sight used
      to be appended whenever ``manifest.no_ui`` was false — the default — so a
      run with no frontend files in scope deadlocked on a marker it could never
      earn. It is now driven by whether any casting key_file actually carries a
      UI extension, and this fixture declares no castings at all. The UI case is
      covered by ``test_sight_still_required_when_ui_files_are_in_scope``.
    - the coverage-drop assertion moved to test_stream_rollup.py. FR-014 / CT-003
      re-points that warning at cycle N vs cycle N-1 in the roll-up; comparing
      against "the previous write of this same marker file" is the behaviour
      being removed, because it fires on a second partial tranche of the SAME
      cycle.
    """
    project_root, fdir = run_env
    # A run directory left behind by the old build: old-format markers only.
    for old in ["trace", "prove", "test"]:
        (fdir / f".{old}-complete").write_text(
            _old_marker_body(items_checked=10), encoding="utf-8"
        )

    streams = _check_streams_complete(project_root)
    assert streams["complete"] is True, streams
    assert streams["required"] == ["trace", "prove", "test"]
    for recordable in ("research_audit", "flow_trace", "coverage_diff", "test01"):
        assert recordable not in streams["required"]

    # An old-format marker body still parses.
    counts = _marker_counts(fdir / ".trace-complete")
    assert counts == {"items_checked": 10, "items_total": 10, "findings": 0}

    # Re-recording over an old-format marker still succeeds. `items_total` is
    # DECLARED here rather than left at 0: fallout ST-008 (D-159) made the
    # `items_checked <= items_total` bound unconditional, so a record that
    # declares no population is refused whatever it is recording over. The
    # subject of this test is the OLD MARKER BODY, not the exemption.
    result = foundry_mark_stream(
        "trace", cycle=2, items_checked=3, items_total=3, project_root=project_root
    )
    assert result.get("ok") is True, result




def test_sight_still_required_when_ui_files_are_in_scope(run_env):
    """FR-020 / AC-025 does NOT weaken SIGHT: a run whose castings carry
    frontend files still requires the sight stream, and the inspect gate still
    blocks when no target_url is set for it."""
    project_root, fdir = run_env
    _write_manifest_with_castings(
        fdir, key_files=["src/App.tsx"], target_url="http://localhost:3000"
    )
    streams = _check_streams_complete(project_root)
    assert "sight" in streams["required"]
    assert "sight" in streams["missing"]

    sight = _check_sight_required(project_root)
    assert sight["required"] is True
    assert sight["blocked"] is False




def test_sight_not_required_on_a_clean_non_ui_run(run_env):
    """FR-020 / AC-025 (the grand-vulture deadlock): a run with real castings
    and zero frontend files passes the streams-complete check without ever
    producing a sight marker.

    Before this, ``sight`` was appended whenever manifest.no_ui was false, and
    no_ui defaults to false — so a fully clean cycle-17 INSPECT blocked forever
    on a stream that had nothing to look at.
    """
    project_root, fdir = run_env
    _write_manifest_with_castings(
        fdir, key_files=["src/api/login.py", "src/api/session.py"]
    )
    _write_spec(fdir, ["FR-001"])
    for s in ("trace", "prove", "test"):
        foundry_mark_stream(
            s, cycle=0, items_checked=5, items_total=5, project_root=project_root
        )

    streams = _check_streams_complete(project_root)
    assert "sight" not in streams["required"], streams
    assert streams["complete"] is True, streams




def test_new_streams_recordable_but_not_required(run_env):
    """NFR-002: recording the new streams does not alter the required set or
    satisfy the gate — recordable is not required (coverage_diff included:
    D-008 makes it recordable, never required, MIGRATION or not)."""
    project_root, fdir = run_env
    for new in ["research_audit", "flow_trace", "coverage_diff"]:
        result = foundry_mark_stream(
            new, cycle=1, items_checked=2, items_total=2,
            project_root=project_root
        )
        assert result.get("ok") is True, result

    streams = _check_streams_complete(project_root)
    assert streams["complete"] is False
    assert streams["required"] == ["trace", "prove", "test"]




def test_tool_schema_enum_matches_runtime_valid_set():
    """must_have truth 5: the Foundry-Stream JSON-Schema enum and the runtime
    guard accept exactly the same eight names — nothing advertised that the
    runtime rejects (the AC-013 / D-008 defect), and nothing accepted but
    hidden."""
    from foundry_mcp import server as foundry_server

    tools = asyncio.run(foundry_server.list_tools())
    stream_tool = next(t for t in tools if t.name == "Foundry-Stream")
    enum = stream_tool.inputSchema["properties"]["stream"]["enum"]
    assert set(enum) == set(VALID_STREAMS)
    assert enum == sorted(VALID_STREAMS)




def test_every_roster_stream_is_recordable_including_test01(run_env):
    """AC-018: 'Recording a research_audit, coverage_diff, flow_trace, or
    test01 stream via Foundry-Stream succeeds.'"""
    project_root, fdir = run_env
    for stream in ("research_audit", "coverage_diff", "flow_trace", "test01"):
        result = foundry_mark_stream(
            stream, cycle=0, items_checked=3, items_total=3,
            project_root=project_root
        )
        assert result.get("ok") is True, (stream, result)
        assert (fdir / f".{stream}-complete").exists()




def test_unknown_stream_is_rejected_server_side_not_coerced(run_env):
    """AC-018's second half: 'an unknown value is rejected server-side rather
    than coerced.'"""
    project_root, fdir = run_env
    result = foundry_mark_stream(
        "trace_but_typoed", cycle=0, items_checked=3, project_root=project_root
    )
    assert "error" in result
    assert not list(fdir.glob(".*-complete"))




def test_non_utf8_stream_marker_does_not_raise(run_env):
    """UnicodeDecodeError is a ValueError, not an OSError, so it walked straight
    through _marker_counts's `except OSError`."""
    _project_root, fdir = run_env
    marker = fdir / ".prove-complete"
    marker.write_bytes(b"items_checked=\xff\xfe\n")

    counts = _marker_counts(marker)
    # Present-but-unreadable stays a RECORD of zero, never None: None means "no
    # marker", and a present marker whose numbers cannot be read must fail the
    # coverage threshold rather than skip it.
    assert counts == {"items_checked": 0, "items_total": 0, "findings": None}




# --------------------------------------------------------------------------- #
# D-071 — the TRACE auto-skip cannot satisfy a FULL roster
# --------------------------------------------------------------------------- #


def test_the_trace_skip_never_fires_on_a_full_cycle(run_env):
    """AC-017 / FR-012 / GI-007. D-071.

    FR-012 sanctions exactly two exceptions to the FULL roster — a stream in
    `manifest.stream_skips`, or a `research_skipped` record — and the TRACE
    auto-skip is neither. It predates the width rule and referenced it not at
    all, so on a cycle recorded FULL/final_gate (the INSPECT before ASSAY) whose
    GRIND touched a non-key_file, Foundry-Next auto-stamped `.trace-complete`
    and the streams check returned complete with TRACE never run.
    """
    project_root, fdir = run_env
    _write_state(fdir, phase="F2", cycle=2, inspect_modes=[{
        "cycle": 2, "phase": "F2", "mode": "FULL", "rule": "final_gate",
        "decided_by": "inspect_start",
        "required_streams": ["trace", "prove", "test"],
        "stream_scope": {}, "prove_sample": [], "touched_files": [],
        "trace_skip": {"skip": False, "reason": "this INSPECT is recorded FULL (rule final_gate) — the full roster runs, and TRACE is in it"},
    }])
    _write_manifest_with_castings(fdir, ["src/api/a.py"], no_ui=True)
    (fdir / ".trace-clean-at").write_text(
        json.dumps({"head_sha": "0" * 40}), encoding="utf-8"
    )

    decision = _stamp_trace_skip(fdir)

    assert decision["skip"] is False, decision
    assert "FULL" in decision["reason"]
    assert not (fdir / ".trace-complete").exists()
    assert "trace" in _check_streams_complete(project_root)["missing"]




def test_a_second_stream_record_replaces_the_cycles_totals_and_names_what_it_replaced(run_env):
    """fallout FR-023 / CT-003 / ST-008 / ST-009 / AC-030 / OT-028.

    The three totals were `+=`, `max` and `+=` — the arithmetic for TRANCHES of
    one run. It is the wrong arithmetic for what actually happens, which is a
    stream RE-RUNNING inside one cycle: 40 of 40 recorded twice reads as 80 of
    40 and coverage passes 100% on the same work counted twice, which is the one
    direction a coverage check must never fail.
    """
    project_root, fdir = run_env
    _write_state(fdir, phase="F2", cycle=1)

    first = foundry_mark_stream("prove", 1, 20, 40, 3, project_root)
    assert first["ok"] is True, first
    assert first["items_checked"] == 20 and first["items_total"] == 40, first
    assert first["findings"] == 3, first
    assert first["replaced"] is None, first

    second = foundry_mark_stream("prove", 1, 40, 40, 0, project_root)
    assert second["ok"] is True, second
    # REPLACED, not summed: 40 of 40, not 60 of 40.
    assert second["items_checked"] == 40 and second["items_total"] == 40, second
    assert second["findings"] == 0, second
    assert second["replaced"]["items_checked"] == 20, second
    assert second["replaced"]["findings"] == 3, second
    # ...and the history is kept, which is what makes the replacement safe.
    assert second["records_this_cycle"] == 2, second
    rollup = json.loads((fdir / ROLLUP_FILENAME).read_text(encoding="utf-8"))
    entry = rollup["cycles"]["1"]["prove"]
    assert [r["items_checked"] for r in entry["records"]] == [20, 40], entry
    assert entry["items_checked"] == 40, entry




def test_a_stream_total_that_disagrees_with_its_roster_is_refused(run_env):
    """fallout FR-050 / CT-003 / ST-008 / OT-031.

    `items_total` is the size of the population, and when a roster exists that
    size is not the agent's to assert: a stream that re-derives a SHORTER list
    and reports 12 of 12 clears the >=95% threshold on two thirds of the set the
    run agreed to check, with nothing on the record saying so.
    """
    from foundry_mcp.tools.rosters import foundry_roster

    project_root, fdir = run_env
    _write_state(fdir, phase="F2", cycle=1)
    written = foundry_roster(
        stream="prove", items=[f"FR-{n}" for n in range(1, 19)],
        project_root=project_root,
    )
    assert written.get("error") is None, written

    refused = foundry_mark_stream("prove", 1, 12, 12, 0, project_root)
    assert refused.get("ok") is not True, refused
    assert refused["error"] == ROSTER_MISMATCH, refused
    assert refused["roster_length"] == 18, refused
    assert "18" in refused["reason"], refused

    accepted = foundry_mark_stream("prove", 1, 18, 18, 0, project_root)
    assert accepted["ok"] is True, accepted

    # No roster for a stream means no refusal — "no roster" and "a roster of
    # zero items" are different answers and only the second could refuse.
    assert foundry_mark_stream("trace", 1, 3, 3, 0, project_root)["ok"] is True


def test_the_negative_findings_refusal_states_the_replace_model_it_enforces():
    """fallout GI-016 / FR-023 / CT-003 (D-096) — the door teaches the contract
    it holds, not the one it used to hold.

    The refusal itself was never wrong: a negative finding count is refused
    before and after GI-016. What was wrong is the reason it PUBLISHES. It read
    "A cycle's findings accumulate across tranches, so a negative count would
    erase findings an earlier record of this cycle already reported", which is
    the accumulate-by-addition model `_record_stream_rollup` implemented before
    this release and stopped implementing when the totals became the LAST
    record's values. Under replace semantics nothing is erased — the negative
    simply becomes the cycle's finding count — so every caller the door refused
    was handed a correct refusal and an incorrect contract, and the callers a
    door refuses are the ones reading it hardest.

    Asserted against the SOURCE rather than by driving a run, because this is
    the sentence a caller reads and the sentence is the defect: the drive that
    produces it is already pinned by `tests/test_stream_rollup.py`.
    """
    import inspect

    source = inspect.getsource(_streams.foundry_mark_stream)
    head, _, tail = source.partition("if findings_count < 0:")
    assert tail, "the findings_count rung has moved; this pin has no subject"
    rung = tail.split("if items_total < 0:")[0]

    # The superseded model is gone from the rung a caller is shown...
    assert "accumulate" not in rung.lower(), rung
    assert "erase" not in rung.lower(), rung
    # ...and the model the writer actually implements is what it names.
    assert "REPLACES this cycle's totals" in rung, rung

    # ...and the comment above it, which is the other half of what a maintainer
    # reads, no longer argues from the retired arithmetic either.
    prologue = head.split("if items_checked <= 0:")[-1]
    assert "accumulate by ADDITION" not in prologue, prologue


def test_the_replace_writer_and_the_refusal_agree_about_what_a_record_does():
    """fallout GI-016 (D-096) — the pin above is anchored to real behaviour.

    A prose pin over a sentence is only worth its anchor: if `_record_stream_
    rollup` ever went back to summing, the sentence the test above demands would
    become the false one. So the arithmetic itself is driven here — two records
    for one (stream, cycle), and the totals are the SECOND one's values rather
    than the pair's sum.
    """
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        fdir = Path(tmp)
        first = _streams._record_stream_rollup(fdir, 1, "prove", 10, 20, 3, 1)
        assert (first["items_checked"], first["findings"]) == (10, 3), first
        second = _streams._record_stream_rollup(fdir, 1, "prove", 7, 20, 2, 1)
        # REPLACED, not 17 and not 5.
        assert (second["items_checked"], second["findings"]) == (7, 2), second
        assert second["replaced"]["items_checked"] == 10, second
        assert second["records"] == 2, second


# --------------------------------------------------------------------------- #
# fallout FR-050 / GI-020 / CT-003 (concern C-058, same class as D-071) — THE
# ROSTER'S PROBLEM CHANNEL IS A REFUSAL, NOT AN ABSENT CONSTRAINT.
# --------------------------------------------------------------------------- #


def _unreadable_roster(fdir, stream: str) -> None:
    """A roster document that PARSES and is not a roster.

    Deliberately valid JSON: `_artifact_guard` already refuses the whole door on
    a file that will not parse, so an unparseable roster never reaches the rung
    and is not the state C-058 is about. The reachable third answer is a
    document `read_document` accepts and `_roster_shape_problem` then NAMES —
    which is exactly what casting 1's D-071 fix created.
    """
    rosters = fdir / "rosters"
    rosters.mkdir(parents=True, exist_ok=True)
    (rosters / f"{stream}.json").write_text(
        json.dumps({"stream": stream, "note": "no items list here"}),
        encoding="utf-8",
    )


def test_a_roster_that_cannot_be_read_refuses_the_record(run_env):
    """fallout FR-050 / GI-020 (C-058).

    The rung read `if roster_problem is None and ...`, so every answer on the
    problem channel skipped the check, accepted the record with `items_total`
    unchecked, and DISCARDED the string. Casting 1's D-071 fix had just made
    that channel carry a second, different fact — "a document is here and no
    population can be read from it" — and consuming both as "no roster" put the
    two back together on the accepting side.
    """
    from foundry_mcp.tools.rosters import roster_length

    project_root, fdir = run_env
    _write_state(fdir, phase="F2", cycle=1)
    _unreadable_roster(fdir, "prove")
    # The arrangement reaches the channel this rung branches on...
    assert roster_length(fdir, "prove")[1] is not None, "no problem to report"

    refused = foundry_mark_stream("prove", 1, 12, 12, 0, project_root)

    assert refused.get("ok") is not True, refused
    assert refused["error"] == _streams.ROSTER_UNREADABLE, refused
    # The problem the reader gave is SURFACED, not discarded.
    assert refused["roster_problem"], refused
    assert refused["roster_problem"] in refused["reason"], refused
    assert "prove.json" in refused["hint"], refused["hint"]
    # ...and nothing was recorded against a population nobody could read.
    assert _streams._rollup_totals(fdir, 1, "prove") is None, "the record was written"


def test_no_roster_at_all_is_still_unconstrained(run_env):
    """fallout FR-050 (C-058) — the fix must not turn absence into a refusal.

    "No roster" and "a roster I cannot read" became different answers precisely
    so that a stream running before anyone derives a roster keeps working. If
    the new refusal fired on absence it would block every first-cycle record,
    which is the opposite failure and a worse one.
    """
    project_root, fdir = run_env
    _write_state(fdir, phase="F2", cycle=1)
    assert not (fdir / "rosters" / "prove.json").exists()

    assert foundry_mark_stream("prove", 1, 12, 12, 0, project_root)["ok"] is True


def test_the_two_roster_refusals_carry_different_tokens_and_remedies(run_env):
    """fallout CT-003 (C-058) — a record disagreeing with a roster and a roster
    nothing can read are different findings with different remedies.

    ROSTER_MISMATCH says fix the RECORD (or revise the roster deliberately);
    ROSTER_UNREADABLE says repair the ARTIFACT. Folding them into one token
    would send an operator to revise a roster that cannot be parsed.
    """
    from foundry_mcp.tools.rosters import foundry_roster

    project_root, fdir = run_env
    _write_state(fdir, phase="F2", cycle=1)
    assert foundry_roster(
        stream="prove", items=[f"FR-{n}" for n in range(1, 19)],
        project_root=project_root,
    ).get("error") is None

    mismatch = foundry_mark_stream("prove", 1, 12, 12, 0, project_root)
    assert mismatch["error"] == ROSTER_MISMATCH, mismatch
    assert mismatch["roster_length"] == 18, mismatch
    assert "roster_problem" not in mismatch, mismatch

    _unreadable_roster(fdir, "prove")
    unreadable = foundry_mark_stream("prove", 1, 12, 12, 0, project_root)
    assert unreadable["error"] == _streams.ROSTER_UNREADABLE, unreadable
    assert "roster_length" not in unreadable, unreadable
    assert _streams.ROSTER_UNREADABLE != ROSTER_MISMATCH


# --------------------------------------------------------------------------- #
# fallout ST-008 / CT-003 (D-159) — THE UPPER BOUND IS UNCONDITIONAL.
# --------------------------------------------------------------------------- #


def test_a_record_declaring_no_population_has_no_upper_bound_and_is_refused(run_env):
    """fallout ST-008 / CT-003 (D-159) — `items_checked` at most `items_total`,
    with no exemption for the denominator nobody declared.

    ST-008's guard column reads "items_checked at most items_total" and CT-003's
    errors column reads "items_checked above items_total"; neither carves out
    the undeclared case, and the `Foundry-Stream` schema requires only stream,
    cycle and items_checked, so `items_total` arrives as 0 whenever a caller
    omits it. The rung opened `if items_total > 0 and ...`, so 0 bought a record
    BOTH exits at once — no ratio bound, and no denominator for the coverage
    threshold to be evaluated against.

    DRIVEN before the fix on a run with no roster for trace: this exact call
    returned ok True with coverage "N/A" and `_coverage_shortfall` answered None
    for the (stream, cycle), while the same call with items_total=10 was
    correctly refused. The control below is that same refusal, which must not
    have changed.
    """
    project_root, fdir = run_env
    _write_state(fdir, phase="F2")

    refused = foundry_mark_stream(
        "trace", cycle=1, items_checked=9999, items_total=0, project_root=project_root
    )
    assert refused.get("ok") is not True, refused
    assert "items_total=0" in refused["error"], refused
    # The refusal names what an undeclared population actually costs, rather
    # than reporting a ratio against a number the caller never gave.
    assert "declares no coverage denominator" in refused["error"], refused
    assert "not optional" in refused["hint"], refused
    # ...and nothing was written, so the coverage rung has nothing to be
    # satisfied by.
    assert _streams._rollup_totals(fdir, 0, "trace") is None, "the record was written"

    # The control: a DECLARED population still refuses on the ratio, in the
    # sentence it has always used.
    over = foundry_mark_stream(
        "trace", cycle=1, items_checked=9999, items_total=10, project_root=project_root
    )
    assert over.get("ok") is not True, over
    assert "a tranche cannot check more items than the population it declares" in over["error"]

    # ...and an honest record is unaffected.
    ok = foundry_mark_stream(
        "trace", cycle=1, items_checked=9, items_total=10, project_root=project_root
    )
    assert ok["ok"] is True, ok
    assert ok["coverage"] == "90%", ok


# --------------------------------------------------------------------------- #
# fallout FR-050 / FR-023 / ST-008 (D-156) — THE ROSTER IS NOT THE DENOMINATOR
# ON A WIDTH THE SERVER ITSELF NARROWED.
# --------------------------------------------------------------------------- #


def _delta_scope(fdir, stream: str, cycle: int = 0) -> None:
    """Record an INSPECT decision that narrowed `stream` to DELTA width."""
    _write_state(fdir, phase="F2", cycle=cycle, inspect_modes=[{
        "cycle": cycle,
        "mode": "DELTA",
        "rule": "",
        "decided_by": "inspect_start",
        "required_streams": [stream],
        "stream_scope": {
            stream: {"scope": "delta", "detail": "symbols in the 3 file(s) touched"}
        },
        "touched_files": ["src/a.py", "src/b.py", "src/c.py"],
    }])


def test_a_server_narrowed_stream_records_the_width_it_was_given(run_env):
    """fallout FR-050 / FR-023 / CT-003 / ST-008 (D-156) — the DELTA width is
    this cycle's population, and the roster rung stands down for it.

    DRIVEN before the fix, on a run carrying an 84-item `rosters/trace.json` and
    a recorded DELTA decision whose `stream_scope.trace` is {scope: "delta"}:
    `Foundry-Stream(trace, items_checked=6, items_total=6)` returned
    ROSTER_MISMATCH "the persisted roster for this stream names 84 item(s)", so
    the stream had exactly two moves and both were wrong — report the width it
    was given and be refused (no record at all, so `_check_streams_complete`
    stays short and `inspect_clean` cannot pass), or report 84/84 and assert a
    full walk the DELTA width forbade.

    `agents/tracer.md` and `skills/trace/SKILL.md` both bind `items_total` to
    `inspect_mode.touched_files` on a DELTA cycle, and this is the door that
    made that instruction unfollowable.
    """
    from foundry_mcp.tools.rosters import foundry_roster

    project_root, fdir = run_env
    _delta_scope(fdir, "trace")
    assert foundry_roster(
        stream="trace", items=[f"src/f{n}.py#S{n}" for n in range(84)],
        project_root=project_root,
    ).get("error") is None

    narrowed = foundry_mark_stream("trace", 0, 6, 6, 0, project_root)
    assert narrowed.get("ok") is True, narrowed
    assert narrowed["coverage"] == "100%", narrowed
    # The record SAYS which population it was measured against, because a total
    # that means the roster on one cycle and the drawn width on the next is a
    # number a later reader cannot interpret.
    assert narrowed["measured_against"] == "delta_width", narrowed
    assert narrowed["roster_length"] == 84, narrowed


def test_the_narrowed_cycle_still_bounds_items_total_by_the_roster(run_env):
    """fallout ST-008 / CT-003 / FR-050 / OT-031 (D-179) — THE STAND-DOWN
    LOWERED THE BOUND; IT DID NOT REMOVE IT.

    D-156's fix wrote `and not narrowed_by_the_server` onto an EQUALITY, which
    left a DELTA cycle's declared population unconstrained in BOTH directions.
    Driven at HEAD against exactly the arrangement above — an 84-item
    `rosters/trace.json` with the cycle recorded DELTA for trace —
    `items_total=999` and `items_total=100000` both returned ok True: a declared
    population larger than the FULL roster, on a cycle whose width is by
    definition NARROWER than it.

    The direction is what survives the narrowing. A width drawn FROM the roster
    cannot exceed it, so `items_total <= roster_len` holds on a DELTA cycle
    exactly as `items_total == roster_len` holds on a FULL one, and nothing in
    ST-008's guard or CT-003's errors column sanctions a total above the roster
    at any width.

    THE FAILING-THEN-PASSING PAIR IS IN ONE TEST because the bound has two
    sides and only one of them moved: 6 of 84 stays accepted (D-156's whole
    point), and 999 of 84 is refused.
    """
    from foundry_mcp.tools.rosters import foundry_roster

    project_root, fdir = run_env
    _delta_scope(fdir, "trace")
    assert foundry_roster(
        stream="trace", items=[f"src/f{n}.py#S{n}" for n in range(84)],
        project_root=project_root,
    ).get("error") is None

    # The side D-156 opened, unchanged: a narrower total is the drawn width.
    assert foundry_mark_stream("trace", 0, 6, 6, 0, project_root).get("ok") is True

    # The side D-156 opened by accident, closed.
    for over in (85, 999, 100000):
        refused = foundry_mark_stream("trace", 0, 1, over, 0, project_root)
        assert refused.get("ok") is not True, (over, refused)
        assert refused["error"] == ROSTER_MISMATCH, (over, refused)
        assert refused["roster_length"] == 84, refused
        assert refused["items_total"] == over, refused
        # The refusal says which population it was measured against and why the
        # narrowing does not excuse this direction.
        assert refused["measured_against"] == "delta_width", refused
        assert "DELTA" in refused["reason"], refused["reason"]
        # ...and the hint names a number this door actually accepts, rather
        # than the roster length a narrowed cycle must not report.
        assert "at most 84" in refused["hint"], refused["hint"]

    # The boundary itself is accepted: 84 of 84 is a DELTA that happened to draw
    # the whole roster, which is a width, not an over-declaration.
    assert foundry_mark_stream("trace", 0, 84, 84, 0, project_root).get("ok") is True


def _delta_scope_touching(fdir, stream: str, touched: list[str], cycle: int = 0) -> None:
    """`_delta_scope`, with the drawn width NAMED rather than left disjoint.

    The fixture above writes `touched_files` that intersect no roster item, so
    the population the decision enumerates is empty and the bound below is
    vacuous — which is why every D-156 / D-179 test kept passing while the
    lower half of the contract was missing.
    """
    _write_state(fdir, phase="F2", cycle=cycle, inspect_modes=[{
        "cycle": cycle,
        "mode": "DELTA",
        "rule": "",
        "decided_by": "inspect_start",
        "required_streams": [stream],
        "stream_scope": {
            stream: {
                "scope": "delta",
                "detail": f"symbols in the {len(touched)} file(s) the GRIND touched",
            }
        },
        "touched_files": list(touched),
        "prove_sample": [],
    }])


def test_a_narrowed_cycle_bounds_items_total_from_below_by_the_width_it_drew(run_env):
    """fallout FR-050 / CT-003 / ST-008 / OT-031 (D-220) — THE HALF D-179 LEFT
    OPEN.

    OT-031, CT-003's errors column and ST-008's guard state the equality
    UNQUALIFIED. D-156 stood the rung down on a server-narrowed cycle and D-179
    restored the UPPER bound only, so what survived was `items_total <=
    roster_len` — and any total at or below the roster length was then accepted
    at 100%.

    DRIVEN at ac89f59 against an 84-item `rosters/trace.json` with the width
    stamped DELTA for the same cycle: `items_checked=1, items_total=1` returned
    ok True, coverage "100%", measured_against "delta_width". TRACE could report
    one of one as full coverage of an 84-item population — a shorter list
    reported as full coverage of a population it quietly shrank, which is the
    exact failure `agents/research-auditor.md` names.

    THE POPULATION MEASURED AGAINST IS THE ONE THE DECISION ENUMERATES, which
    is PROVE's own shape (D-080) and what `agents/tracer.md` calls the TRACE
    roster on a DELTA cycle: "`inspect_mode.touched_files` ... This is the TRACE
    roster on a `DELTA` cycle."

    A BOUND AND NOT AN EQUALITY, because the two are in different units: the
    record counts SYMBOLS and the enumeration counts FILES. Measured over this
    run's own five DELTA cycles, `touched_files` was 0/38/2/10/8 where
    `items_total` was -/69/9/25/9, so an equality would have refused every real
    record. What holds by construction is the direction — a roster file declares
    at least one symbol — and that is what refuses 1 of 1 on a width that drew
    seven.
    """
    from foundry_mcp.tools.rosters import foundry_roster

    project_root, fdir = run_env
    roster = [f"src/f{n}.py" for n in range(84)]
    drawn = roster[:7]
    _delta_scope_touching(fdir, "trace", drawn + ["docs/not_on_the_roster.md"])
    assert foundry_roster(
        stream="trace", items=roster, project_root=project_root,
    ).get("error") is None

    # THE DRIVE. One of one, on a width that drew seven.
    refused = foundry_mark_stream("trace", 0, 1, 1, 0, project_root)
    assert refused.get("ok") is not True, refused
    assert refused["error"] == ROSTER_MISMATCH, refused
    assert refused["narrowed_length"] == 7, refused
    assert refused["roster_length"] == 84, refused
    assert refused["measured_against"] == "delta_width", refused
    # The hint names the width rather than the roster, because reporting 84 is
    # the OTHER wrong answer D-156 closed.
    assert "at least 7" in refused["hint"], refused["hint"]
    assert "src/f0.py" in refused["hint"], refused["hint"]

    # THE WIDTH ITSELF IS ACCEPTED, which is the boundary of the bound.
    assert foundry_mark_stream("trace", 0, 7, 7, 0, project_root).get("ok") is True

    # ...and so is a LARGER total, because the units differ: 25 declared symbols
    # across 7 touched files is the shape every real DELTA record in this run
    # has. A bound that refused this would be D-156 reopened.
    assert foundry_mark_stream("trace", 0, 25, 25, 0, project_root).get("ok") is True

    # ...while the upper bound D-179 restored is untouched.
    over = foundry_mark_stream("trace", 0, 1, 85, 0, project_root)
    assert over["error"] == ROSTER_MISMATCH, over
    assert "at most 84" in over["hint"], over["hint"]


def test_a_narrowed_prove_is_bounded_by_the_sample_the_server_drew(run_env):
    """fallout FR-050 / CT-003 / OT-031 (D-220) — the same bound, on the stream
    whose enumeration is in the record's OWN unit.

    `prove_sample` is a NAMED, FINITE list of requirement rows — the rows tied
    to the defects the preceding GRIND fixed plus the sampled remainder — so
    for PROVE the enumeration and the record count the same things.
    `_coverage_shortfall` has measured against it since D-080; the DOOR did not,
    so a PROVE record could declare a population smaller than the sample the
    server itself drew and be accepted at 100% before the threshold ever saw it.
    """
    from foundry_mcp.tools.rosters import foundry_roster

    project_root, fdir = run_env
    _write_state(fdir, phase="F2", cycle=0, inspect_modes=[{
        "cycle": 0,
        "mode": "DELTA",
        "rule": "",
        "decided_by": "inspect_start",
        "required_streams": ["prove"],
        "stream_scope": {
            "prove": {"scope": "delta", "detail": "12 row(s)"}
        },
        "touched_files": [],
        "prove_sample": [f"FR-{n:03d}" for n in range(12)],
    }])
    assert foundry_roster(
        stream="prove", items=[f"FR-{n:03d}" for n in range(40)],
        project_root=project_root,
    ).get("error") is None

    refused = foundry_mark_stream("prove", 0, 3, 3, 0, project_root)
    assert refused.get("ok") is not True, refused
    assert refused["error"] == ROSTER_MISMATCH, refused
    assert refused["narrowed_length"] == 12, refused
    assert "FR-000" in refused["hint"], refused["hint"]

    # The sample itself is what the door accepts, and it is what every real
    # DELTA PROVE record in this run reported.
    assert foundry_mark_stream("prove", 0, 12, 12, 0, project_root).get("ok") is True


def test_a_width_that_drew_no_roster_item_bounds_nothing(run_env):
    """fallout FR-050 / OT-031 (D-220) — the empty enumeration, stated.

    A DELTA cycle whose GRIND touched no file on the stream's roster drew a
    width of nothing, and demanding a total against it would be inventing a
    population the server never drew. This run's own cycle 9 is that state:
    mode DELTA, `stream_scope.trace` delta, `touched_files` empty. The upper
    bound still applies, so the record is not unconstrained — it is bounded by
    the one fact that is still knowable.

    Written down rather than left implicit because "the derivation came back
    empty, so nothing is checked" is the fail-open shape this run has filed
    twice (D-207, D-221). It is not one here: an empty intersection is a
    MEASURED width of zero, not a width the server failed to compute.
    """
    from foundry_mcp.tools.rosters import foundry_roster

    project_root, fdir = run_env
    _delta_scope_touching(fdir, "trace", ["docs/only.md", "README.md"])
    assert foundry_roster(
        stream="trace", items=[f"src/f{n}.py" for n in range(84)],
        project_root=project_root,
    ).get("error") is None

    assert foundry_mark_stream("trace", 0, 1, 1, 0, project_root).get("ok") is True
    # ...and the upper bound is still there.
    assert foundry_mark_stream("trace", 0, 1, 85, 0, project_root).get("ok") is not True


def test_the_roster_rung_still_refuses_on_a_width_the_server_did_not_narrow(run_env):
    """fallout FR-050 / OT-031 (D-156) — the control, on the same run.

    The stand-down is keyed to the server's OWN recorded decision, not to the
    presence of a roster and not to the stream's name. With the same roster and
    the same stream recorded `full` for the cycle, `ROSTER_MISMATCH` is
    unchanged — which is what keeps OT-031 true everywhere the width was not
    narrowed.
    """
    from foundry_mcp.tools.rosters import foundry_roster

    project_root, fdir = run_env
    _write_state(fdir, phase="F2", cycle=0, inspect_modes=[{
        "cycle": 0, "mode": "FULL", "rule": "final_gate",
        "decided_by": "inspect_start", "required_streams": ["trace"],
        "stream_scope": {"trace": {"scope": "full", "detail": "every item in scope"}},
    }])
    assert foundry_roster(
        stream="trace", items=[f"src/f{n}.py#S{n}" for n in range(84)],
        project_root=project_root,
    ).get("error") is None

    refused = foundry_mark_stream("trace", 0, 6, 6, 0, project_root)
    assert refused["error"] == ROSTER_MISMATCH, refused
    assert refused["roster_length"] == 84, refused

    full = foundry_mark_stream("trace", 0, 84, 84, 0, project_root)
    assert full["ok"] is True, full
    assert full["measured_against"] == "roster", full


def test_the_stand_down_reaches_only_the_streams_the_server_narrows(run_env):
    """fallout FR-050 (D-156) — `research_audit` and `test01` are never `delta`.

    `width._decide_inspect_mode` writes scope "delta" for `trace` and `prove`
    only: `test` is held FULL and cold (AC-019), the two
    `DELTA_CONDITIONAL_STREAMS` record "full" or "skipped", and
    `_base_required_streams` records "full". So the two streams whose loaded
    prose names `ROSTER_MISMATCH` by token — `agents/research-auditor.md` and
    `agents/spec-test-deriver.md` — cannot reach the stand-down, and their
    sentence stays literally true.

    Asserted on the DECISION rather than on the door, because the claim is about
    which scopes the server ever writes; a door drive would pass while the
    decision started writing "delta" for a third stream.
    """
    project_root, fdir = run_env
    _write_manifest_with_castings(fdir, key_files=["src/a.py"], no_ui=True)
    _write_spec(fdir, ["FR-001", "FR-002"])
    entry = _width._decide_inspect_mode(
        fdir, project_root, decided_by="inspect_start", phase="F2", cycle=1,
    )
    narrowed = {
        wire for wire, cell in (entry.get("stream_scope") or {}).items()
        if isinstance(cell, dict) and cell.get("scope") == "delta"
    }
    assert narrowed <= {"trace", "prove"}, entry["stream_scope"]
    for wire in sorted(vocab.DELTA_CONDITIONAL_STREAMS):
        assert wire not in narrowed, (wire, entry["stream_scope"])


def test_the_items_total_hint_names_a_call_this_door_actually_accepts(run_env):
    """fallout ST-008 / CT-003 (D-159, casting 12's concern C-087) — a hint that
    instructs a caller into a guaranteed refusal.

    The `items_total < 0` hint read "Report the size of the population, or 0 when
    this stream has no fixed denominator" — the exact sentence the D-159 comment
    one rung below names as where the exemption was INVENTED. D-159 removed the
    exemption from the bound and left it standing in the advice, so the door
    said: report -1, get told to report 0, report 0, get refused. And because
    `items_checked` is required above zero, `items_total=0` can NEVER be
    accepted, so the advice was unreachable for every caller that could read it.

    A hint is the one piece of prose a reader meets at the moment they are
    already wrong, which makes it the worst place in the system for a false
    statement: a caller who disbelieves the error still follows the hint.

    DRIVEN AS A ROUND TRIP rather than as a string comparison, because the claim
    is not "the sentence changed" but "obeying it works" — and only the round
    trip can fail when a future edit makes the two rungs disagree again.
    """
    project_root, fdir = run_env
    _write_state(fdir, phase="F2")

    negative = foundry_mark_stream(
        "trace", cycle=1, items_checked=9, items_total=-1, project_root=project_root
    )
    assert negative.get("ok") is not True, negative

    # The sentence that made the round trip a dead end is gone...
    assert "or 0 when this stream has no fixed denominator" not in negative["hint"]
    # ...and 0 is named as what it IS rather than offered as a value.
    assert "never 0" in negative["hint"], negative["hint"]

    # THE ROUND TRIP: the number the hint names is a number this door takes.
    # "items_checked itself" is the arm a caller with no wider population reads.
    obeyed = foundry_mark_stream(
        "trace", cycle=1, items_checked=9, items_total=9, project_root=project_root
    )
    assert obeyed["ok"] is True, obeyed
    assert obeyed["coverage"] == "100%", obeyed
    assert obeyed["measured_against"] == "declared", obeyed

    # ...and the value the OLD hint named is still refused, which is why the
    # sentence had to change rather than the bound.
    assert foundry_mark_stream(
        "trace", cycle=1, items_checked=9, items_total=0, project_root=project_root
    ).get("ok") is not True


def test_both_rungs_that_refuse_an_items_total_give_one_answer(run_env):
    """fallout CT-003 (C-087) — two rungs, one question, one sentence.

    The negative rung and the bound rung both answer "what number belongs in
    items_total", and they answered it differently — one offering an exemption
    the other refuses. That is how the contradiction survived D-159: the fix
    changed the rung that enforced the rule and not the rung that described it.
    Said once, they cannot drift again.
    """
    project_root, fdir = run_env
    _write_state(fdir, phase="F2")

    negative = foundry_mark_stream(
        "trace", cycle=1, items_checked=9, items_total=-1, project_root=project_root
    )
    undeclared = foundry_mark_stream(
        "trace", cycle=1, items_checked=9, items_total=0, project_root=project_root
    )

    assert negative["hint"] == undeclared["hint"], (negative["hint"], undeclared["hint"])
    assert negative["hint"] == _streams._ITEMS_TOTAL_HINT

    # The two ERRORS still differ, which is the half that must NOT be collapsed:
    # a negative population and an undeclared one are different mistakes.
    assert negative["error"] != undeclared["error"]
    assert "cannot be negative" in negative["error"], negative["error"]
    assert "cannot check more items" in undeclared["error"], undeclared["error"]
