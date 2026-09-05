"""The per-stream roster ledger — fallout FR-024 / FR-050 / CT-002 / ST-010 / AC-032 /
OT-030.

A-019: "``rosters/<stream>.json`` written by a new ``Foundry-Roster`` tool at
first derivation; later cycles read it"

Three behaviours changed, each of which the old shape got wrong for the same
underlying reason — a list that is re-derived every cycle has no identity
across cycles:

  1. NO FILE PERSISTED A ROSTER. ``RA-1..RA-n`` existed only in
     ``agents/research-auditor.md``'s prose and that agent's returned JSON; a
     grep for ``RA-[0-9]`` across ``foundry_mcp/`` found nothing, so RA
     numbering was not stable across cycles and the HONORED -> IGNORED
     regression check had no prior state to compare against.
  2. COVERAGE WAS A BARE COUNT. daring-orca's cycle 29 records
     ``research_audit`` as 4/4 with no way to know WHICH four. The persisted
     roster is what makes ``items_total`` checkable rather than asserted.
  3. A REWRITE LOST THE PRIOR LIST SILENTLY. The write-once door makes a second
     derivation an explicit act, and the prior items are kept under
     ``revisions`` rather than replaced (fallout GI-006).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from foundry_mcp.schemas.vocab import STREAM_WIRE_IDS
from foundry_mcp.tools import foundry_state
from foundry_mcp.tools.rosters import (
    ROSTERS_DIRNAME,
    ROSTER_EXISTS,
    ROSTER_ITEMS_NOT_A_LIST,
    ROSTER_REVISE_REASON_REQUIRED,
    ROSTER_REVISIONS_KEY,
    ROSTER_STREAM_PHRASE,
    ROSTER_UNKNOWN_STREAM,
    foundry_roster,
    read_roster,
    roster_length,
    roster_path,
)


# --------------------------------------------------------------------------- #
# Fixtures — this suite deliberately does not share one through conftest.py
# --------------------------------------------------------------------------- #


@pytest.fixture
def run_env(tmp_path):
    """Activate a foundry run under tmp_path; yield (project_root, fdir)."""
    project_root = tmp_path
    run_name = "roster-run"
    fdir = project_root / "foundry-archive" / run_name
    (fdir / "castings").mkdir(parents=True, exist_ok=True)

    foundry_state.set_active_run(run_name)
    try:
        yield str(project_root), fdir
    finally:
        foundry_state.clear_active_run()


def _document(fdir: Path, stream: str) -> dict:
    return json.loads(roster_path(fdir, stream).read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- #
# fallout ST-010 / OT-030 / CT-002 — the first write
# --------------------------------------------------------------------------- #


def test_the_first_write_persists_the_document_with_all_four_fields(run_env):
    """fallout CT-002: '`rosters/<stream>.json` {stream, items, derived_at, revisions[]}'.

    THE FAILING-THEN-PASSING TEST FOR fallout FR-024. Before this module no file
    persisted a roster at all — the call itself was unavailable, so this
    assertion could not have been made.
    """
    project_root, fdir = run_env

    result = foundry_roster(
        "research_audit", ["RA-1: pin the client", "RA-2: fake clientset"],
        project_root=project_root,
    )

    assert result.get("ok") is True, result
    assert result["items_total"] == 2

    doc = _document(fdir, "research_audit")
    assert doc["stream"] == "research_audit"
    assert doc["items"] == ["RA-1: pin the client", "RA-2: fake clientset"]
    assert doc["derived_at"]
    assert doc[ROSTER_REVISIONS_KEY] == []

    # It lands under the run's rosters/ directory, created on demand.
    assert roster_path(fdir, "research_audit").parent.name == ROSTERS_DIRNAME


def test_two_streams_keep_two_independent_documents(run_env):
    """One document per stream: a roster is a stream's item list, not the
    run's."""
    project_root, fdir = run_env

    foundry_roster("research_audit", ["RA-1"], project_root=project_root)
    foundry_roster("test01", ["T-1", "T-2", "T-3"], project_root=project_root)

    assert _document(fdir, "research_audit")["items"] == ["RA-1"]
    assert len(_document(fdir, "test01")["items"]) == 3
    assert roster_length(fdir, "research_audit") == (1, None)
    assert roster_length(fdir, "test01") == (3, None)


# --------------------------------------------------------------------------- #
# fallout AC-032 / OT-030 — the write-once door
# --------------------------------------------------------------------------- #


def test_a_second_write_is_refused_naming_the_existing_roster_and_the_revise_form(
    run_env,
):
    """fallout OT-030 verbatim: 'Foundry-Roster writes rosters/<stream>.json once and
    refuses a second write without revise=true and a reason.'

    THE REFUSAL TEST FOR ``ROSTER_EXISTS``.
    """
    project_root, fdir = run_env
    foundry_roster("research_audit", ["RA-1", "RA-2"], project_root=project_root)

    result = foundry_roster("research_audit", ["RA-9"], project_root=project_root)

    assert result["phase"] == ROSTER_EXISTS
    # The hint names the existing roster...
    assert "2 item" in result["error"]
    assert "research_audit" in result["error"]
    # ...and the exact form the caller must use instead.
    assert "revise=True" in result["hint"]
    assert "reason=" in result["hint"]

    # And the roster is untouched.
    assert _document(fdir, "research_audit")["items"] == ["RA-1", "RA-2"]


def test_revise_with_a_reason_succeeds_and_the_prior_items_survive(run_env):
    """fallout AC-032: 'the same call with revise=true and a reason succeeds and keeps
    the prior item list under the roster's revisions.' fallout GI-006 — a ledger writer
    never drops history."""
    project_root, fdir = run_env
    foundry_roster("research_audit", ["RA-1", "RA-2"], project_root=project_root)

    result = foundry_roster(
        "research_audit",
        ["RA-1", "RA-2", "RA-3"],
        revise=True,
        reason="a third recommendation landed in research/",
        project_root=project_root,
    )

    assert result.get("ok") is True, result
    assert result["revised"] is True
    assert result["items_total"] == 3

    doc = _document(fdir, "research_audit")
    assert doc["items"] == ["RA-1", "RA-2", "RA-3"]
    assert len(doc[ROSTER_REVISIONS_KEY]) == 1
    revision = doc[ROSTER_REVISIONS_KEY][0]
    assert revision["items"] == ["RA-1", "RA-2"]          # the PRIOR list
    assert revision["reason"] == "a third recommendation landed in research/"
    assert revision["at"]


def test_a_second_revision_keeps_both_prior_lists(run_env):
    """History accumulates; it is never replaced."""
    project_root, fdir = run_env
    foundry_roster("research_audit", ["RA-1"], project_root=project_root)
    foundry_roster(
        "research_audit", ["RA-2"], revise=True, reason="one", project_root=project_root
    )
    foundry_roster(
        "research_audit", ["RA-3"], revise=True, reason="two", project_root=project_root
    )

    revisions = _document(fdir, "research_audit")[ROSTER_REVISIONS_KEY]
    assert [r["items"] for r in revisions] == [["RA-1"], ["RA-2"]]
    assert [r["reason"] for r in revisions] == ["one", "two"]


def test_revise_without_a_reason_is_refused(run_env):
    """fallout CT-002 error column: 'revise without reason'.

    THE REFUSAL TEST FOR ``ROSTER_REVISE_REASON_REQUIRED``. Whitespace is not a
    reason.
    """
    project_root, fdir = run_env
    foundry_roster("research_audit", ["RA-1"], project_root=project_root)

    for empty in ("", "   "):
        result = foundry_roster(
            "research_audit", ["RA-2"], revise=True, reason=empty,
            project_root=project_root,
        )
        assert result["phase"] == ROSTER_REVISE_REASON_REQUIRED, result

    assert _document(fdir, "research_audit")["items"] == ["RA-1"]


# --------------------------------------------------------------------------- #
# fallout CT-002 error column — the closed stream vocabulary
# --------------------------------------------------------------------------- #


def test_an_unknown_stream_is_refused_with_the_accepted_names_in_the_hint(run_env):
    """fallout CT-002 error column: 'unknown stream'.

    THE REFUSAL TEST FOR ``ROSTER_UNKNOWN_STREAM``. The hint is DERIVED from
    ``vocab.STREAM_WIRE_IDS``, never a second hand list, so adding a stream
    there cannot leave this sentence behind.
    """
    project_root, fdir = run_env

    result = foundry_roster("reasearch_audit", ["RA-1"], project_root=project_root)

    assert result["phase"] == ROSTER_UNKNOWN_STREAM
    assert "reasearch_audit" in result["error"]
    for stream in STREAM_WIRE_IDS:
        assert stream in result["hint"]
    assert ROSTER_STREAM_PHRASE in result["hint"]
    assert not (fdir / ROSTERS_DIRNAME).exists()


def test_the_stream_phrase_is_derived_from_the_vocabulary(run_env):
    """Prose that names a set is built from the constant that defines it."""
    assert set(ROSTER_STREAM_PHRASE.split(", ")) == set(STREAM_WIRE_IDS)


def test_items_that_are_not_a_list_are_refused(run_env):
    """A roster is a LIST of items; a string is one item spelled as many."""
    project_root, fdir = run_env

    result = foundry_roster("research_audit", "RA-1", project_root=project_root)

    assert result["phase"] == ROSTER_ITEMS_NOT_A_LIST
    assert not (fdir / ROSTERS_DIRNAME).exists()


# --------------------------------------------------------------------------- #
# fallout CT-003 / OT-031 / AC-032 — the reader Foundry-Stream calls (AC-032's
# second half)
# --------------------------------------------------------------------------- #


def test_roster_length_distinguishes_no_roster_from_a_roster_of_zero_items(run_env):
    """Foundry-Stream must only refuse an items_total mismatch when a roster
    ACTUALLY EXISTS, so 'absent' and 'empty' cannot be the same answer."""
    project_root, fdir = run_env

    assert roster_length(fdir, "research_audit") == (None, None)
    assert read_roster(fdir, "research_audit") == (None, None)

    foundry_roster("research_audit", [], project_root=project_root)

    assert roster_length(fdir, "research_audit") == (0, None)
    doc, problem = read_roster(fdir, "research_audit")
    assert problem is None and doc["items"] == []


def test_roster_length_answers_after_a_write(run_env):
    """The length Foundry-Stream compares items_total against."""
    project_root, fdir = run_env
    foundry_roster("test01", ["T-1", "T-2", "T-3", "T-4"], project_root=project_root)

    assert roster_length(fdir, "test01") == (4, None)


# --------------------------------------------------------------------------- #
# The house rule: a tool never raises across the MCP boundary
# --------------------------------------------------------------------------- #


def test_a_corrupt_roster_document_is_refused_by_name_rather_than_raising(run_env):
    """D-095 / D-096 / D-127: a document that cannot be read produces the house
    refusal naming the file, not a traceback, and is never written over."""
    project_root, fdir = run_env
    path = roster_path(fdir, "research_audit")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{ not json", encoding="utf-8")

    result = foundry_roster("research_audit", ["RA-1"], project_root=project_root)

    assert "error" in result
    assert "research_audit.json" in result["error"]
    assert path.read_text(encoding="utf-8") == "{ not json"


def test_the_readers_name_a_corrupt_roster_rather_than_answering_absent(run_env):
    """'No roster' and 'a roster I could not read' send the operator to look at
    different things, so the readers keep them apart."""
    _project_root, fdir = run_env
    path = roster_path(fdir, "research_audit")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("[]", encoding="utf-8")

    doc, problem = read_roster(fdir, "research_audit")
    assert doc is None
    assert problem is not None and "research_audit.json" in problem

    length, problem = roster_length(fdir, "research_audit")
    assert length is None
    assert problem is not None and "research_audit.json" in problem


def test_no_active_run_is_refused_in_band(run_env):
    """A tool never raises across the MCP boundary."""
    _project_root, _fdir = run_env
    foundry_state.clear_active_run()

    result = foundry_roster("research_audit", ["RA-1"])

    assert "error" in result
