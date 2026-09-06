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
from foundry_mcp.tools import artifacts, foundry_state

# fallout FR-004 / AC-013 — THE MODULE OBJECTS, UNDER UNDERSCORE ALIASES.
#
# `streams`, `spend`, `width`, `gates`, `directives` and `teams` are all LOCAL
# variable names somewhere in this suite, and a local rebinding shadows a
# module for the rest of its function. The aliases are what `ORCHESTRATION`,
# `owning_module` and every `monkeypatch.setattr` resolve through; individual
# SYMBOLS are imported by name below, which is how the carved modules read.
from foundry_mcp.tools.orchestration import directives as _directives
from foundry_mcp.tools.orchestration import escalation as _escalation
from foundry_mcp.tools.orchestration import evidence_boundary as _evidence_boundary
from foundry_mcp.tools.orchestration import fix_gate as _fix_gate
from foundry_mcp.tools.orchestration import gates as _gates
from foundry_mcp.tools.orchestration import guidance as _guidance
from foundry_mcp.tools.orchestration import halt as _halt
from foundry_mcp.tools.orchestration import report_seal as _report_seal
from foundry_mcp.tools.orchestration import spend as _spend
from foundry_mcp.tools.orchestration import streams as _streams
from foundry_mcp.tools.orchestration import teams as _teams
from foundry_mcp.tools.orchestration import transitions as _transitions
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

#: fallout FR-004 / AC-014 — WHAT `fo` USED TO MEAN, NOW THAT IT MEANS THIRTEEN
#: THINGS.
#:
#: Every pin that read `Path(fo.__file__).read_text()` was asking about THE
#: ORCHESTRATOR. That is thirteen files now, so the honest translation of the
#: question is all thirteen — and it stays the honest translation when a
#: fourteenth is added, which a hand-listed pair of modules would not.
ORCHESTRATION = (
    _report_seal, _escalation, _streams, _teams, _width, _evidence_boundary,
    _spend, _halt, _gates, _transitions, _fix_gate, _directives, _guidance,
)


def orchestration_source() -> str:
    """The concatenated source of every shipped orchestration module."""
    return chr(10).join(
        Path(m.__file__).read_text(encoding="utf-8") for m in ORCHESTRATION
    )


def owning_module(symbol: str):
    """The orchestration module that DEFINES `symbol`.

    A patch has to reach the module each CALLER resolves the name through, and
    after the carve that is a binding per importer rather than one module
    attribute. Patching only the module that defines a symbol leaves every
    importer on the real one, which is the silent half of a broken pin.
    """
    for module in ORCHESTRATION:
        value = vars(module).get(symbol)
        if value is None:
            continue
        if getattr(value, "__module__", module.__name__) == module.__name__:
            return module
    for module in ORCHESTRATION:
        if symbol in vars(module):
            return module
    raise AssertionError(f"no orchestration module defines {symbol!r}")


def patch_everywhere(monkeypatch, name: str, value) -> None:
    """Patch `name` in EVERY module that carries it.

    fallout FR-004 / AC-014 — WHAT A MODULE-ATTRIBUTE PATCH USED TO MEAN.

    There was one module, so patching it patched the only binding. After the
    carve a symbol is imported BY NAME into each caller's namespace, so patching
    the module that DEFINES it leaves every importer resolving the real one —
    and a patch that reaches some callers and not others is worse than no patch,
    because the drive then exercises a state no run can be in. This patches
    every binding, which is the same fact the single module used to make true by
    construction.
    """
    for module in (*ORCHESTRATION, artifacts, foundry_state):
        if name in vars(module):
            monkeypatch.setattr(module, name, value)


def orchestration_has(symbol: str) -> bool:
    """True when any orchestration module carries `symbol`."""
    return any(symbol in vars(m) for m in ORCHESTRATION)

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
    stream = foundry_mark_stream("test", cycle=1, items_checked=5, project_root=project_root)
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




def test_coverage_diff_stream_recordable_writes_marker(run_env):
    """D-008: recording coverage_diff succeeds instead of 'Invalid stream'
    (the tool the next-action guidance names accepts the stream the guidance
    names) and writes .coverage_diff-complete."""
    project_root, fdir = run_env
    result = foundry_mark_stream(
        "coverage_diff", cycle=1, items_checked=9, project_root=project_root
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
            stream, cycle=1, items_checked=3, project_root=project_root
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

    # Re-recording over an old-format marker still succeeds.
    result = foundry_mark_stream(
        "trace", cycle=2, items_checked=3, items_total=0, project_root=project_root
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
            new, cycle=1, items_checked=2, project_root=project_root
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
            stream, cycle=0, items_checked=3, project_root=project_root
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
