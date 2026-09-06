"""The concern ledger — fallout FR-010 / FR-039 / CT-001 / ST-004 / AC-005 / OT-005.

A-010: "New ``Foundry-Concern`` tool writes a structured ledger; concerns.md
stays prose"

Three behaviours changed, each of which the old shape got wrong for the same
underlying reason — a hand-written artifact has no writer, so it has no reader
either:

  1. ``concerns.md`` was 287 KB of prose in daring-orca with no id, no cycle
     stamp, no status and no parser, so no gate could ask "is a concern from
     this GRIND still open?" — the question fallout GI-023 / ST-005 need
     ``_inspect_start_preconditions`` to ask. ``concerns.json`` is that
     structured half and ``concerns.md`` is now its rendering.
  2. Nothing checked that a concern's target EXISTED. A teammate could file
     against a casting, a file or a symbol the manifest has never heard of and
     the misdirection was invisible until a lead read the paragraph.
  3. "Addressed" had no representation, so a concern was open forever. The
     status is a closed vocabulary now with one writer per move.

Every door in this module refuses in-band: a tool never raises across the MCP
boundary, it returns ``{error, hint, phase}``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from foundry_mcp.schemas import vocab
from foundry_mcp.tools import foundry_state
from foundry_mcp.tools.concerns import (
    CARRIED_PROSE_MARKER,
    CONCERNS_FILENAME,
    CONCERNS_MARKDOWN_FILENAME,
    CONCERN_CLOSE_REASON_REQUIRED,
    CONCERN_ID_PREFIX,
    CONCERN_STATUSES,
    CONCERN_STATUS_CLOSED,
    CONCERN_STATUS_DISPATCHED,
    CONCERN_STATUS_OPEN,
    CONCERN_STATUS_PHRASE,
    CONCERN_TARGET_UNRESOLVED,
    CONCERN_TEXT_EMPTY,
    CONCERN_UNKNOWN_ID,
    TARGET_KIND_CASTING,
    TARGET_KIND_FILE,
    TARGET_KIND_SYMBOL,
    foundry_concern,
    mark_concerns_dispatched,
    open_concerns_for_other_castings,
    open_cross_casting_concerns,
    read_concerns,
)


# --------------------------------------------------------------------------- #
# Fixtures — this suite deliberately does not share one through conftest.py
# --------------------------------------------------------------------------- #


@pytest.fixture
def run_env(tmp_path):
    """Activate a foundry run under tmp_path; yield (project_root, fdir)."""
    project_root = tmp_path
    run_name = "concern-run"
    fdir = project_root / "foundry-archive" / run_name
    (fdir / "castings").mkdir(parents=True, exist_ok=True)
    _write_manifest(fdir)

    foundry_state.set_active_run(run_name)
    try:
        yield str(project_root), fdir
    finally:
        foundry_state.clear_active_run()


def _write_manifest(fdir: Path) -> None:
    """Two castings: ids, key files, and a `path#Symbol` cite in spec_text."""
    (fdir / "castings" / "manifest.json").write_text(
        json.dumps(
            {
                "castings": [
                    {
                        "id": 1,
                        "title": "Concern and roster ledgers",
                        "key_files": [
                            "plugins/foundry/mcp-server/src/foundry_mcp/tools/concerns.py",
                        ],
                        "spec_text": "mirrors src/foundry_mcp/tools/foundry.py#ledger_transaction",
                    },
                    {
                        "id": 2,
                        "title": "Server registration",
                        "key_files": ["src/foundry_mcp/server.py"],
                        "spec_text": "registers in src/foundry_mcp/server.py#list_tools",
                    },
                ]
            }
        ),
        encoding="utf-8",
    )


def _file(fdir: Path, name: str) -> Path:
    return fdir / name


def _ledger(fdir: Path) -> list[dict]:
    return json.loads(_file(fdir, CONCERNS_FILENAME).read_text(encoding="utf-8"))[
        "concerns"
    ]


def _open_one(project_root: str, **kwargs) -> dict:
    args = {
        "casting_id": 1,
        "cycle": 3,
        "target": "2",
        "text": "casting 2 must register this handler",
        "project_root": project_root,
    }
    args.update(kwargs)
    return foundry_concern(**args)


# --------------------------------------------------------------------------- #
# fallout OT-005 / AC-005 — the append and its rendered twin
# --------------------------------------------------------------------------- #


def test_a_concern_appends_to_the_ledger_and_renders_into_the_markdown(run_env):
    """fallout OT-005 verbatim: 'A Foundry-Concern call appends to concerns.json and
    the same entry appears in concerns.md.'

    THE FAILING-THEN-PASSING TEST FOR fallout FR-010. Before this module,
    ``concerns.json`` did not exist and no tool wrote ``concerns.md``: the call
    itself was unavailable, so this assertion could not have been made at all.
    """
    project_root, fdir = run_env

    result = _open_one(project_root)

    assert result.get("ok") is True, result
    entry = result["concern"]
    assert entry["id"] == f"{CONCERN_ID_PREFIX}-001"
    assert entry["cycle"] == 3
    assert entry["source_casting"] == 1
    assert entry["target"] == "2"
    assert entry["status"] == CONCERN_STATUS_OPEN
    assert entry["text"] == "casting 2 must register this handler"

    # The ledger holds it...
    assert _ledger(fdir) == [entry]

    # ...and the SAME entry is rendered as prose.
    rendered = _file(fdir, CONCERNS_MARKDOWN_FILENAME).read_text(encoding="utf-8")
    assert f"## {entry['id']}" in rendered
    assert entry["text"] in rendered
    assert CONCERN_STATUS_OPEN in rendered


def test_the_rendered_file_names_the_status_vocabulary_from_the_constant(run_env):
    """Prose that names a set is derived from the constant that defines it.

    AND THE CONSTANT IS THE LEAF ONE (concern C-033). The set was declared in
    ``tools/concerns.py``, which reaches ``tools/foundry.py`` at module top, so
    a VERIFIER module could reach the leaf read for fallout GI-023's
    CONCERN_OPEN rung and still not reach the member that read takes as an
    argument. It is declared in ``schemas/vocab.py`` now and this module
    imports it, so the names below are the vocabulary itself rather than a
    second copy agreeing with it today.
    """
    project_root, fdir = run_env
    _open_one(project_root)

    assert CONCERN_STATUSES is vocab.CONCERN_STATUSES
    assert (CONCERN_STATUS_OPEN, CONCERN_STATUS_DISPATCHED, CONCERN_STATUS_CLOSED) == (
        vocab.CONCERN_STATUS_OPEN,
        vocab.CONCERN_STATUS_DISPATCHED,
        vocab.CONCERN_STATUS_CLOSED,
    )

    rendered = _file(fdir, CONCERNS_MARKDOWN_FILENAME).read_text(encoding="utf-8")
    assert CONCERN_STATUS_PHRASE in rendered
    for status in CONCERN_STATUSES:
        assert status in CONCERN_STATUS_PHRASE


# --------------------------------------------------------------------------- #
# fallout AC-005 / CT-001 — target resolution
# (fallout AC-005's second half, fallout CT-001's error column)
# --------------------------------------------------------------------------- #


def test_an_unresolvable_target_is_refused_and_the_hint_names_what_the_run_knows(
    run_env,
):
    """fallout AC-005: 'a target that resolves to no casting, file or symbol in the
    manifest is refused.'

    THE REFUSAL TEST FOR ``CONCERN_TARGET_UNRESOLVED``. The hint is built from
    the manifest that was just read, so it can never name a stale set.
    """
    project_root, fdir = run_env

    result = _open_one(project_root, target="src/nothing/here.py")

    assert result["phase"] == CONCERN_TARGET_UNRESOLVED
    assert "src/nothing/here.py" in result["error"]
    # The casting ids the run knows...
    assert "1" in result["hint"] and "2" in result["hint"]
    # ...and the files.
    assert "src/foundry_mcp/server.py" in result["hint"]
    # Nothing was written.
    assert not _file(fdir, CONCERNS_FILENAME).exists()


@pytest.mark.parametrize(
    "target,expected_kind,expected_casting",
    [
        ("2", TARGET_KIND_CASTING, 2),
        ("casting-2", TARGET_KIND_CASTING, 2),
        ("src/foundry_mcp/server.py", TARGET_KIND_FILE, 2),
        ("server.py", TARGET_KIND_FILE, 2),
        ("list_tools", TARGET_KIND_SYMBOL, 2),
    ],
)
def test_each_target_kind_resolves(run_env, target, expected_kind, expected_casting):
    """fallout CT-001: a target may name a casting id, a key file, or a cited symbol."""
    project_root, fdir = run_env

    result = _open_one(project_root, target=target)

    assert result.get("ok") is True, result
    assert result["concern"]["target_kind"] == expected_kind
    assert result["concern"]["target_casting_id"] == expected_casting


def test_a_file_target_matches_only_on_a_segment_boundary(run_env):
    """`erver.py` is not `server.py`: a tail match is a PATH tail, not a
    string tail, or a concern would silently attach to the wrong casting."""
    project_root, _fdir = run_env

    result = _open_one(project_root, target="erver.py")

    assert result["phase"] == CONCERN_TARGET_UNRESOLVED


def test_empty_text_is_refused(run_env):
    """fallout CT-001 error column: 'empty text'.

    THE REFUSAL TEST FOR ``CONCERN_TEXT_EMPTY``. Whitespace is empty too — a
    ledger entry a lead cannot act on is not an entry.
    """
    project_root, fdir = run_env

    for empty in ("", "   \n\t "):
        result = _open_one(project_root, text=empty)
        assert result["phase"] == CONCERN_TEXT_EMPTY, result
    assert not _file(fdir, CONCERNS_FILENAME).exists()


# --------------------------------------------------------------------------- #
# fallout ST-004 — the close arm
# --------------------------------------------------------------------------- #


def test_closing_with_a_reason_closes_the_entry_and_appends_a_handoff(run_env):
    """fallout ST-004: 'concern open -> concern closed ... reason non-empty; a handoff
    record is appended.'"""
    project_root, fdir = run_env
    opened = _open_one(project_root)["concern"]

    result = foundry_concern(
        close=opened["id"], reason="casting 2 registered it", project_root=project_root
    )

    assert result.get("ok") is True, result
    assert result["concern"]["status"] == CONCERN_STATUS_CLOSED
    assert result["concern"]["close_reason"] == "casting 2 registered it"
    assert _ledger(fdir)[0]["status"] == CONCERN_STATUS_CLOSED

    # The handoff record, in BOTH channels.
    rows = [
        json.loads(line)
        for line in _file(fdir, "handoffs.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert [r for r in rows if r.get("concern_id") == opened["id"]]
    assert opened["id"] in _file(fdir, "handoffs.md").read_text(encoding="utf-8")


def test_the_rendered_entry_is_restated_as_closed(run_env):
    """The two documents cannot disagree: the render is regenerated from the
    ledger inside the same locked section, so a closed concern reads as closed
    in BOTH — an append-only mirror could only add a second line saying so."""
    project_root, fdir = run_env
    opened = _open_one(project_root)["concern"]

    rendered_open = _file(fdir, CONCERNS_MARKDOWN_FILENAME).read_text(encoding="utf-8")
    assert f"## {opened['id']} — {CONCERN_STATUS_OPEN}" in rendered_open

    foundry_concern(close=opened["id"], reason="done", project_root=project_root)

    rendered_closed = _file(fdir, CONCERNS_MARKDOWN_FILENAME).read_text(encoding="utf-8")
    assert f"## {opened['id']} — {CONCERN_STATUS_CLOSED}" in rendered_closed
    assert f"## {opened['id']} — {CONCERN_STATUS_OPEN}" not in rendered_closed
    assert "done" in rendered_closed


def test_closing_an_unknown_id_is_refused_naming_the_ids_that_exist(run_env):
    """fallout CT-001 error column: 'close of unknown id'.

    THE REFUSAL TEST FOR ``CONCERN_UNKNOWN_ID``.
    """
    project_root, fdir = run_env
    opened = _open_one(project_root)["concern"]

    result = foundry_concern(close="C-999", reason="whatever", project_root=project_root)

    assert result["phase"] == CONCERN_UNKNOWN_ID
    assert "C-999" in result["error"]
    assert opened["id"] in result["hint"]
    assert _ledger(fdir)[0]["status"] == CONCERN_STATUS_OPEN


def test_closing_without_a_reason_is_refused(run_env):
    """fallout CT-001 error column: 'close without reason'.

    THE REFUSAL TEST FOR ``CONCERN_CLOSE_REASON_REQUIRED``. Whitespace is not a
    reason.
    """
    project_root, fdir = run_env
    opened = _open_one(project_root)["concern"]

    for empty in ("", "   "):
        result = foundry_concern(
            close=opened["id"], reason=empty, project_root=project_root
        )
        assert result["phase"] == CONCERN_CLOSE_REASON_REQUIRED, result
    assert _ledger(fdir)[0]["status"] == CONCERN_STATUS_OPEN


# --------------------------------------------------------------------------- #
# fallout FR-039 / CT-008 / GI-023 / ST-005 — the readers casting 2 calls
# --------------------------------------------------------------------------- #


def test_the_cross_casting_filter_has_one_body_and_it_is_the_leafs(run_env):
    """fallout GI-024 / GI-033 — concern C-030, ruling ``lead_ruling_gi_033_leaf_moves``.

    THE FAILING-THEN-PASSING TEST FOR THE DELEGATION. Before this change
    ``tools/concerns.py`` defined its own body of the cross-casting filter
    beside ``foundry_state``'s, so this identity was False and
    ``test_no_top_level_symbol_is_defined_in_two_shipped_modules`` named
    ``open_cross_casting_concerns`` as defined in two shipped modules. Two
    bodies of one rule drift; there is one now, and it is the leaf's, so a
    VERIFIER module can read it without reaching through this module into
    ``tools/foundry.py``.
    """
    _project_root, fdir = run_env

    assert open_cross_casting_concerns is foundry_state.open_cross_casting_concerns

    # ...and it is the LEAF's contract that arrives, not a local function
    # wearing the leaf's name. The leaf declines to TYPE a status member, so
    # the bare call has none to read and refuses rather than guessing one.
    # Every caller either names the member or asks through the wrapper below.
    with pytest.raises(TypeError):
        open_cross_casting_concerns(fdir)


def test_this_module_supplies_its_own_open_status_to_the_leafs_read(run_env):
    """fallout GI-023 / ST-005 / FR-039 — what is left on this side of the delegation.

    The leaf takes its status member as an argument, and this module supplies
    ``vocab.CONCERN_STATUS_OPEN`` for the lifecycle callers that ask the ledger
    a ledger question — ``orchestration/directives.py``'s two co-dispatch
    reads. A verifier does not come through here at all: it reaches the
    vocabulary and the leaf directly, which is what concern C-033 moved the
    declaration into ``schemas/vocab.py`` to make possible.

    The ledger is hand-built rather than driven through ``Foundry-Concern``:
    the expected ids below are written out, so this pins the ANSWER rather than
    agreeing with whatever the two implementations happen to say together.
    """
    _project_root, fdir = run_env
    (fdir / CONCERNS_FILENAME).write_text(
        json.dumps(
            {
                "concerns": [
                    # open, lands elsewhere, cycle 7 — the door's subject.
                    {"id": "C-001", "cycle": 7, "source_casting": 1,
                     "target_casting_id": 2, "status": "open"},
                    # dispatched: addressed by definition (fallout FR-039).
                    {"id": "C-002", "cycle": 7, "source_casting": 1,
                     "target_casting_id": 2, "status": "dispatched"},
                    {"id": "C-003", "cycle": 7, "source_casting": 1,
                     "target_casting_id": 2, "status": "closed"},
                    # open, but lands on the casting that filed it.
                    {"id": "C-004", "cycle": 7, "source_casting": 3,
                     "target_casting_id": 3, "status": "open"},
                    # open, lands elsewhere, a LATER cycle.
                    {"id": "C-005", "cycle": 8, "source_casting": 1,
                     "target_casting_id": 2, "status": "open"},
                ]
            }
        ),
        encoding="utf-8",
    )

    assert [c["id"] for c in open_concerns_for_other_castings(fdir)] == ["C-001", "C-005"]
    assert [c["id"] for c in open_concerns_for_other_castings(fdir, cycle=7)] == ["C-001"]
    assert open_concerns_for_other_castings(fdir, cycle=9) == []

    # The member supplied really is `open` and not "whatever the leaf defaults
    # to": the same read asking for another member of THIS module's vocabulary
    # answers with the other records.
    assert [
        c["id"]
        for c in open_cross_casting_concerns(fdir, status_open=CONCERN_STATUS_DISPATCHED)
    ] == ["C-002"]


def test_dispatching_moves_the_status_and_the_reader_stops_returning_it(run_env):
    """fallout FR-039: 'Foundry-Tasks marks dispatched.' fallout GI-023 / ST-005: the
    inspect_start rung asks for the OPEN cross-casting concerns, and a
    dispatched one has been addressed by definition."""
    project_root, fdir = run_env
    opened = _open_one(project_root)["concern"]

    assert [c["id"] for c in open_concerns_for_other_castings(fdir)] == [opened["id"]]

    result = mark_concerns_dispatched(fdir, [opened["id"]])

    assert result["dispatched"] == [opened["id"]]
    assert _ledger(fdir)[0]["status"] == CONCERN_STATUS_DISPATCHED
    assert open_concerns_for_other_castings(fdir) == []
    rendered = _file(fdir, CONCERNS_MARKDOWN_FILENAME).read_text(encoding="utf-8")
    assert f"## {opened['id']} — {CONCERN_STATUS_DISPATCHED}" in rendered


def test_dispatching_reports_unknown_ids_and_skips_a_closed_concern(run_env):
    """Only an OPEN concern moves — dispatching after a close would walk the
    lifecycle backwards."""
    project_root, fdir = run_env
    opened = _open_one(project_root)["concern"]
    foundry_concern(close=opened["id"], reason="done", project_root=project_root)

    result = mark_concerns_dispatched(fdir, [opened["id"], "C-404"])

    assert result["dispatched"] == []
    assert result["skipped"] == [opened["id"]]
    assert result["unknown"] == ["C-404"]
    assert _ledger(fdir)[0]["status"] == CONCERN_STATUS_CLOSED


def test_a_self_targeted_concern_is_not_cross_casting(run_env):
    """The rung asks for concerns targeting ANOTHER casting: a teammate noting
    something about its own file is not what blocks INSPECT."""
    project_root, fdir = run_env

    _open_one(
        project_root,
        casting_id=1,
        target="plugins/foundry/mcp-server/src/foundry_mcp/tools/concerns.py",
        text="my own file",
    )

    assert open_concerns_for_other_castings(fdir) == []


def test_the_cross_casting_reader_scopes_to_a_cycle_when_asked(run_env):
    """fallout ST-005 scopes to 'the closing GRIND'; the unscoped call answers for the
    whole run."""
    project_root, fdir = run_env
    _open_one(project_root, cycle=3, text="from cycle 3")
    _open_one(project_root, cycle=4, text="from cycle 4")

    assert len(open_concerns_for_other_castings(fdir)) == 2
    scoped = open_concerns_for_other_castings(fdir, cycle=4)
    assert [c["text"] for c in scoped] == ["from cycle 4"]


# --------------------------------------------------------------------------- #
# fallout GI-006 — every write preserves an existing value
# --------------------------------------------------------------------------- #


def test_a_second_concern_gets_the_next_id_and_the_first_entry_survives(run_env):
    """'Every write preserves an existing value' — the single-writer ledger
    rule this module's analog states. The id comes from max(suffix)+1 inside
    the lock, not from the list length."""
    project_root, fdir = run_env

    first = _open_one(project_root, text="first")["concern"]
    second = _open_one(project_root, text="second")["concern"]

    assert first["id"] == f"{CONCERN_ID_PREFIX}-001"
    assert second["id"] == f"{CONCERN_ID_PREFIX}-002"
    records = _ledger(fdir)
    assert [r["text"] for r in records] == ["first", "second"]

    rendered = _file(fdir, CONCERNS_MARKDOWN_FILENAME).read_text(encoding="utf-8")
    assert f"## {first['id']}" in rendered and f"## {second['id']}" in rendered


def test_hand_written_prose_already_in_the_markdown_is_carried_forward(run_env):
    """fallout GI-006: 'a replace-semantics write that drops history' is the violation.

    ``concerns.md`` held 287 KB of hand-written prose in daring-orca, and two
    agent contracts (``agents/research-auditor.md``, ``agents/assayer.md``)
    read it back to flip IGNORED -> HONORED_WITH_OVERRIDE. Generating the file
    must not cost that.
    """
    project_root, fdir = run_env
    prose = "## Concern: hand written\n\nSomething a teammate typed by hand.\n"
    _file(fdir, CONCERNS_MARKDOWN_FILENAME).write_text(prose, encoding="utf-8")

    first = _open_one(project_root, text="first")["concern"]
    rendered = _file(fdir, CONCERNS_MARKDOWN_FILENAME).read_text(encoding="utf-8")
    assert CARRIED_PROSE_MARKER in rendered
    assert "Something a teammate typed by hand." in rendered
    assert f"## {first['id']}" in rendered

    # Idempotent: a second render carries it once, not twice.
    _open_one(project_root, text="second")
    rendered = _file(fdir, CONCERNS_MARKDOWN_FILENAME).read_text(encoding="utf-8")
    assert rendered.count(CARRIED_PROSE_MARKER) == 1
    assert rendered.count("Something a teammate typed by hand.") == 1


# --------------------------------------------------------------------------- #
# The house rule: a tool never raises across the MCP boundary
# --------------------------------------------------------------------------- #


def test_a_corrupt_ledger_is_refused_by_name_rather_than_raising(run_env):
    """D-095 / D-096 / D-127: a ledger whose record container is the wrong
    shape must produce the house refusal naming the file, not a traceback, and
    must not be written over."""
    project_root, fdir = run_env
    _file(fdir, CONCERNS_FILENAME).write_text(
        json.dumps({"concerns": {"C-001": "not a list"}}), encoding="utf-8"
    )

    result = _open_one(project_root)

    assert "error" in result
    assert CONCERNS_FILENAME in result["error"]
    # Nothing was written over.
    assert json.loads(_file(fdir, CONCERNS_FILENAME).read_text(encoding="utf-8")) == {
        "concerns": {"C-001": "not a list"}
    }


def test_a_torn_ledger_document_is_refused_by_name(run_env):
    """The same rung one level up: unparseable bytes, not a wrong container."""
    project_root, fdir = run_env
    _file(fdir, CONCERNS_FILENAME).write_text("{not json", encoding="utf-8")

    result = foundry_concern(close="C-001", reason="x", project_root=project_root)

    assert "error" in result
    assert CONCERNS_FILENAME in result["error"]


def test_an_unreadable_manifest_is_refused_by_name(run_env):
    """Target resolution is judged against the manifest, so a manifest that
    cannot be read must refuse rather than resolve nothing silently."""
    project_root, fdir = run_env
    (fdir / "castings" / "manifest.json").write_text("{ broken", encoding="utf-8")

    result = _open_one(project_root)

    assert result["phase"] == CONCERN_TARGET_UNRESOLVED
    assert "manifest.json" in result["error"]


def test_read_concerns_names_a_broken_ledger_rather_than_answering_empty(run_env):
    """An empty list cannot distinguish 'nothing filed' from 'corrupt', so the
    reader returns the (value, problem) pair the leaf module uses."""
    project_root, fdir = run_env

    records, problem = read_concerns(fdir)
    assert (records, problem) == ([], None)

    _open_one(project_root)
    records, problem = read_concerns(fdir)
    assert len(records) == 1 and problem is None

    _file(fdir, CONCERNS_FILENAME).write_text("nope", encoding="utf-8")
    records, problem = read_concerns(fdir)
    assert records == []
    assert problem is not None and CONCERNS_FILENAME in problem


def test_no_active_run_is_refused_in_band(run_env):
    """A tool never raises across the MCP boundary."""
    _project_root, _fdir = run_env
    foundry_state.clear_active_run()

    result = foundry_concern(casting_id=1, cycle=0, target="2", text="x")

    assert "error" in result
