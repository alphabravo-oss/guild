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
from foundry_mcp.tools import foundry_state, rosters
from foundry_mcp.tools.rosters import (
    ROSTERS_DIRNAME,
    ROSTER_EXISTS,
    ROSTER_ITEM_NOT_NAMED,
    ROSTER_ITEMS_DUPLICATED,
    ROSTER_ITEMS_EMPTY,
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
# fallout FR-050 / CT-002 / AC-032, defects D-105 / D-106 — the population door.
# The roster length is the denominator `Foundry-Stream` enforces, so a list
# this door accepts is a number no later door is able to question.
# --------------------------------------------------------------------------- #


def test_a_roster_of_zero_items_is_refused_at_publication(run_env):
    """fallout FR-050, defect D-105 — THE FAILING-THEN-PASSING TEST, and THE
    REFUSAL TEST FOR ``ROSTER_ITEMS_EMPTY``.

    `scripts/migrate-archive.py#_migrate_rosters` states the ruling for the
    whole run, and states it as its reason for creating `rosters/` and never a
    file inside it: "A roster FILE holding zero items says a stream derived its
    item list and the list was empty — a measurement nobody took — and
    `Foundry-Roster` would then refuse the real derivation with ROSTER_EXISTS
    on the strength of it." This door created by hand exactly the state the
    migration refuses to create.

    And the state WEDGES the stream, which is why the refusal has to be here
    rather than two doors downstream: with the zero-item document written, the
    real derivation is refused ROSTER_EXISTS, every honest `items_total` is
    refused ROSTER_MISMATCH, and `items_checked=0` is refused by the roll-up's
    own positive-count guard — no legal recording is left at all.
    """
    project_root, fdir = run_env

    result = foundry_roster("trace", [], project_root=project_root)

    assert result["phase"] == ROSTER_ITEMS_EMPTY, result
    assert "trace" in result["error"]
    assert "measurement nobody took" in result["error"], result["error"]
    # Nothing was created for a call that wrote no roster.
    assert not (fdir / ROSTERS_DIRNAME).exists()
    assert roster_length(fdir, "trace") == (None, None)

    # An omitted list is the same absence spelled differently.
    assert foundry_roster("trace", project_root=project_root)["phase"] == (
        ROSTER_ITEMS_EMPTY
    )

    # ...and the real derivation, which the zero-item document would have
    # locked out, lands.
    real = foundry_roster("trace", ["a", "b", "c"], project_root=project_root)
    assert real.get("ok") is True, real
    assert roster_length(fdir, "trace") == (3, None)


def test_the_wedge_the_zero_item_roster_created_is_gone(run_env):
    """THE ADJACENT PATH for defect D-105: casting 2's `Foundry-Stream`.

    The harm was never at this door — it was at the door that reads what this
    one wrote. Driven end to end: the stream that would have been wedged now
    records, on the roster it actually derived.
    """
    from foundry_mcp.tools.orchestration.streams import foundry_mark_stream

    project_root, fdir = run_env
    (fdir / "state.json").write_text(
        json.dumps({"phase": "F2", "cycle": 1}), encoding="utf-8"
    )

    assert foundry_roster("trace", [], project_root=project_root)["phase"] == (
        ROSTER_ITEMS_EMPTY
    )
    assert foundry_roster(
        "trace", ["x", "y", "z"], project_root=project_root
    ).get("ok") is True

    recorded = foundry_mark_stream("trace", 1, 3, 3, 0, project_root)
    assert recorded["ok"] is True, recorded


def test_a_repeated_item_is_refused_because_the_length_is_a_denominator(run_env):
    """fallout FR-050, defect D-106 — THE FAILING-THEN-PASSING TEST, and THE
    REFUSAL TEST FOR ``ROSTER_ITEMS_DUPLICATED``.

    ``items=['a', 'a', 'b']`` persisted ``items_total=3`` for two distinct
    items, and `Foundry-Stream` then REQUIRED 3 — every other value refused
    ROSTER_MISMATCH — so 3 of 3 recorded "100%" for a population one of whose
    members was counted twice. That is the double-count
    `orchestration/streams.py#_record_stream_rollup` was rewritten to end,
    reintroduced one door upstream where the population is DECLARED.
    """
    project_root, fdir = run_env

    result = foundry_roster("prove", ["a", "a", "b"], project_root=project_root)

    assert result["phase"] == ROSTER_ITEMS_DUPLICATED, result
    assert "'a'" in result["error"], result["error"]
    assert "2 distinct" in result["error"], result["error"]
    assert not (fdir / ROSTERS_DIRNAME).exists()

    # The distinct list this call was trying to be is accepted.
    assert foundry_roster(
        "prove", ["a", "b"], project_root=project_root
    )["items_total"] == 2


def test_an_item_that_names_nothing_is_refused(run_env):
    """fallout FR-050, defect D-106 second half — THE REFUSAL TEST FOR
    ``ROSTER_ITEM_NOT_NAMED``.

    ``items=[1, 2]`` returned ``ok: True`` and persisted integers where every
    consumer reads a path, a requirement id or an ``RA-n`` line; ``['']``
    counted a member no agent can report against. Both inflate the denominator
    with something nothing can be checked against, which is the same harm as a
    duplicate spelled a different way.
    """
    project_root, fdir = run_env

    integers = foundry_roster("prove", [1, 2], project_root=project_root)
    assert integers["phase"] == ROSTER_ITEM_NOT_NAMED, integers
    assert "int" in integers["error"], integers["error"]

    blank = foundry_roster("prove", ["RA-1", "   "], project_root=project_root)
    assert blank["phase"] == ROSTER_ITEM_NOT_NAMED, blank
    assert "item 1" in blank["error"], blank["error"]

    assert not (fdir / ROSTERS_DIRNAME).exists()


def test_the_population_door_holds_on_the_revise_arm_too(run_env):
    """A revision publishes a population exactly as a first write does.

    ``revise=True`` is the repair for a list that CHANGED, and a list that
    changed to nothing — or to the same item twice — is the same untrue
    denominator arriving through the other arm. It also may not silently
    destroy the roster it was revising.
    """
    project_root, fdir = run_env
    foundry_roster("prove", ["a", "b"], project_root=project_root)

    for bad, token in (
        ([], ROSTER_ITEMS_EMPTY),
        (["c", "c"], ROSTER_ITEMS_DUPLICATED),
        ([7], ROSTER_ITEM_NOT_NAMED),
    ):
        result = foundry_roster(
            "prove", bad, revise=True, reason="the source material changed",
            project_root=project_root,
        )
        assert result["phase"] == token, result

    assert _document(fdir, "prove")["items"] == ["a", "b"]


def test_the_derivation_stamp_comes_from_the_leaf(run_env, monkeypatch):
    """fallout GI-024, defect D-124 — THE FAILING-THEN-PASSING TEST.

    ``_now`` here spelled ``datetime.now(timezone.utc).isoformat()`` inline —
    a second derivation outside `foundry_state`, which is that invariant's violation
    column verbatim, and the third `_now` in a package whose one delegation
    documents itself as one of two. It is a call-through now, so the leaf's
    answer IS this document's stamp, in BOTH places this module stamps one.

    Two assertions because the duplication had two halves: the module no
    longer holds the apparatus to derive a timestamp (``datetime`` is not in
    its namespace at all), and every stamp it writes is the leaf's answer.
    """
    project_root, fdir = run_env
    assert not hasattr(rosters, "datetime"), (
        "the module imports datetime again — a second derivation is back"
    )

    monkeypatch.setattr(rosters, "now_iso", lambda **_kwargs: "STAMP-1")
    foundry_roster("prove", ["a"], project_root=project_root)
    assert _document(fdir, "prove")["derived_at"] == "STAMP-1"

    # ...and the revision stamp, which is the module's other one.
    monkeypatch.setattr(rosters, "now_iso", lambda **_kwargs: "STAMP-2")
    foundry_roster(
        "prove", ["b"], revise=True, reason="the list changed",
        project_root=project_root,
    )
    doc = _document(fdir, "prove")
    assert doc["derived_at"] == "STAMP-2"
    assert doc[ROSTER_REVISIONS_KEY][0]["at"] == "STAMP-2"


# --------------------------------------------------------------------------- #
# fallout CT-003 / OT-031 / AC-032 — the reader Foundry-Stream calls
# (fallout AC-032's second half)
# --------------------------------------------------------------------------- #


def test_roster_length_distinguishes_no_roster_from_a_roster_of_zero_items(run_env):
    """Foundry-Stream must only refuse an items_total mismatch when a roster
    ACTUALLY EXISTS, so 'absent' and 'empty' cannot be the same answer.

    THE ZERO-ITEM DOCUMENT IS SEEDED BY HAND, and that is the point rather than
    a convenience: since fallout D-105 the write door REFUSES to publish one
    (``ROSTER_ITEMS_EMPTY``), so the only way this shape reaches the reader now
    is from another hand — an older archive, a migration, an operator's editor.
    The reader's three answers are unchanged by that refusal, because the
    reader's job is to describe what is on disk and the door's job is to stop
    writing a lie there. A reader that collapsed "absent" into "zero" would
    still have Foundry-Stream refuse every record against a population nobody
    derived.
    """
    _project_root, fdir = run_env

    assert roster_length(fdir, "research_audit") == (None, None)
    assert read_roster(fdir, "research_audit") == (None, None)

    path = roster_path(fdir, "research_audit")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "stream": "research_audit",
                "items": [],
                "derived_at": "2026-09-01T00:00:00+00:00",
                ROSTER_REVISIONS_KEY: [],
            }
        ),
        encoding="utf-8",
    )

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


# --------------------------------------------------------------------------- #
# fallout AC-032 / GI-020, defect D-071 — a roster this door did not stamp is
# still a roster, and both halves of the guard have to see it
# --------------------------------------------------------------------------- #


#: The EXACT shape this run's own ``rosters/research_audit.json`` carries:
#: ``{items, stream, total}``, 42 real items, no ``derived_at`` and no
#: ``revisions``. Seeded from the live document rather than invented, because
#: the whole of fallout D-071 is that a shape nobody wrote a test for is the
#: shape the guard met.
def _seed_unstamped_roster(fdir: Path, stream: str, count: int = 42) -> list[str]:
    items = [f"RA-{n}: recommendation {n}" for n in range(1, count + 1)]
    path = roster_path(fdir, stream)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"items": items, "stream": stream, "total": count}),
        encoding="utf-8",
    )
    return items


def test_a_roster_written_without_this_doors_stamp_is_read_as_a_roster(run_env):
    """fallout AC-032, defect D-071 — THE FAILING-THEN-PASSING TEST.

    Both readers used to test for ``derived_at`` and answer "NO ROSTER" when it
    was absent, so a 42-item roster persisted by another hand was invisible:
    ``read_roster`` -> ``(None, None)`` and ``roster_length`` -> ``(None,
    None)``. ``derived_at`` is provenance; the ``items`` list is what makes a
    document a roster.
    """
    _project_root, fdir = run_env
    items = _seed_unstamped_roster(fdir, "research_audit")

    doc, problem = read_roster(fdir, "research_audit")
    assert problem is None, problem
    assert doc is not None and doc["items"] == items

    assert roster_length(fdir, "research_audit") == (42, None)


def test_a_second_write_over_an_unstamped_roster_is_refused_not_silently_taken(
    run_env,
):
    """fallout GI-006, defect D-071 — 'a replace-semantics write that drops history'.

    Driven in the ledger record: ``foundry_roster('research_audit',
    ['ONLY-ONE-ITEM'])`` returned ``ok: True``, replaced the 42 items with 1 and
    left ``revisions`` empty, so the prior list was GONE with no revise and no
    reason. The write-once door scopes on the DOCUMENT now, so it refuses.
    """
    project_root, fdir = run_env
    items = _seed_unstamped_roster(fdir, "research_audit")

    result = foundry_roster(
        "research_audit", ["ONLY-ONE-ITEM"], project_root=project_root
    )

    assert result["phase"] == ROSTER_EXISTS, result
    assert "42 item" in result["error"], result["error"]
    # The sentence degrades honestly rather than interpolating an absent stamp:
    # "derived at None" is how the old wording read over exactly this shape.
    assert "None" not in result["error"], result["error"]
    assert _document(fdir, "research_audit")["items"] == items


def test_revising_an_unstamped_roster_keeps_its_items_and_repairs_the_shape(
    run_env,
):
    """The one way past the door also fixes the document it refused over.

    fallout GI-006: the prior 42 items survive under ``revisions``, and the
    document that comes out carries the stamp the next reader wants.
    """
    project_root, fdir = run_env
    items = _seed_unstamped_roster(fdir, "research_audit")

    result = foundry_roster(
        "research_audit",
        ["RA-1: the one recommendation left"],
        revise=True,
        reason="research/ was rewritten down to a single item",
        project_root=project_root,
    )

    assert result.get("ok") is True, result
    doc = _document(fdir, "research_audit")
    assert doc["items"] == ["RA-1: the one recommendation left"]
    assert doc["derived_at"]
    assert [r["items"] for r in doc[ROSTER_REVISIONS_KEY]] == [items]


def test_foundry_stream_refuses_an_items_total_against_an_unstamped_roster(
    run_env,
):
    """THE ADJACENT PATH: the OTHER caller of this module's reader.

    ``read_roster``'s defect was found through ``Foundry-Roster``'s own door.
    Its other caller is ``streams.foundry_mark_stream``, which reaches
    ``roster_length`` for its ``ROSTER_MISMATCH`` refusal (fallout CT-003 /
    OT-031 / AC-032 second half) — a transition into this module that the
    write door never walks. Driven in the ledger record:
    ``Foundry-Stream(research_audit, items_checked=7, items_total=7)`` returned
    ``ok: True`` at 100% coverage against a 42-item roster, because the reader
    handed the arm no length to compare with.

    The test lives in casting 1's module because casting 1 owns the reader; it
    drives casting 2's door to prove the reader answers it.
    """
    from foundry_mcp.tools.orchestration.streams import ROSTER_MISMATCH, foundry_mark_stream

    project_root, fdir = run_env
    (fdir / "state.json").write_text(
        json.dumps({"phase": "F2", "cycle": 1}), encoding="utf-8"
    )
    _seed_unstamped_roster(fdir, "research_audit")

    refused = foundry_mark_stream("research_audit", 1, 7, 7, 0, project_root)

    assert refused.get("ok") is not True, refused
    assert refused["error"] == ROSTER_MISMATCH, refused
    assert refused["roster_length"] == 42, refused

    accepted = foundry_mark_stream("research_audit", 1, 42, 42, 0, project_root)
    assert accepted["ok"] is True, accepted


def test_a_document_at_a_rosters_path_that_holds_no_items_list_is_named(run_env):
    """The THIRD answer: not absent, not a roster.

    'No roster' and 'a document here I cannot read as one' send the operator to
    look at different things. Answering absent for the second is the fail-open
    fallout D-071 names; answering a length of zero would make Foundry-Stream
    refuse every record against a population nobody derived.
    """
    project_root, fdir = run_env
    path = roster_path(fdir, "research_audit")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"note": "not a roster"}), encoding="utf-8")

    doc, problem = read_roster(fdir, "research_audit")
    assert doc is None
    assert problem is not None and "research_audit.json" in problem

    length, problem = roster_length(fdir, "research_audit")
    assert length is None and problem is not None

    # And the write door still refuses to overwrite it unseen.
    result = foundry_roster("research_audit", ["RA-1"], project_root=project_root)
    assert result["phase"] == ROSTER_EXISTS, result
    assert "`items` list" in result["error"], result["error"]


# --------------------------------------------------------------------------- #
# fallout GI-020, defect D-091 — the refusal hands back the roster it refuses over
# --------------------------------------------------------------------------- #


def test_the_roster_exists_refusal_carries_the_roster_and_names_reachable_exits(
    run_env,
):
    """fallout GI-020, defect D-091 — THE FAILING-THEN-PASSING TEST.

    The refusal used to be ``{error, hint, phase}`` and nothing else, and its
    first remedy read "Read it with Foundry-Roster's reader" — an exit on no
    boundary at all, since ``read_roster`` is a Python function no MCP tool
    surfaces. That left ``revise=True`` as the only actionable branch, and
    ``revise=True`` REPLACES the roster with the agent's re-derived list, which
    is fallout GI-020's own named violation.

    Every exit the hint now names is DRIVEN here rather than merely spelled.
    """
    project_root, fdir = run_env
    foundry_roster("research_audit", ["RA-1", "RA-2"], project_root=project_root)

    result = foundry_roster("research_audit", ["RA-9"], project_root=project_root)
    assert result["phase"] == ROSTER_EXISTS, result

    # Exit 1 — the roster arrives IN the refusal.
    assert result["items"] == ["RA-1", "RA-2"], result
    assert result["items_total"] == 2, result
    assert result["derived_at"], result
    assert result["stream"] == "research_audit", result

    # Exit 2 — the path it names holds that same list.
    persisted = json.loads(Path(result["path"]).read_text(encoding="utf-8"))
    assert persisted["items"] == result["items"]

    # Exit 3 — the revise form it names is accepted by this door.
    revised = foundry_roster(
        "research_audit", ["RA-9"], revise=True, reason="the list changed",
        project_root=project_root,
    )
    assert revised.get("ok") is True, revised

    # And the exit that was on no boundary is gone from the sentence.
    assert "Foundry-Roster's reader" not in result["hint"], result["hint"]
    assert "`items`" in result["hint"] and "`path`" in result["hint"], result["hint"]
