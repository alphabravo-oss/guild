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
from foundry_mcp.tools import concerns as _concerns
from foundry_mcp.tools import foundry_state
from foundry_mcp.tools.concerns import (
    CARRIED_PROSE_MARKER,
    CONCERNS_FILENAME,
    CONCERNS_MARKDOWN_FILENAME,
    CONCERN_CLOSE_REASON_REQUIRED,
    CONCERN_CYCLE_REQUIRED,
    CONCERN_ID_PREFIX,
    CONCERN_STATUSES,
    CONCERN_STATUS_CLOSED,
    CONCERN_STATUS_DISPATCHED,
    CONCERN_STATUS_OPEN,
    CONCERN_STATUS_PHRASE,
    CONCERN_TARGET_UNRESOLVED,
    CONCERN_TEXT_EMPTY,
    CONCERN_UNKNOWN_ID,
    HANDOFF_EVENT_CONCERN_CLOSED,
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


#: fallout FR-009 (D-170, casting 7's concern C-080) — THE DIRECTORY ENTRY THE
#: REAL MANIFEST CARRIES, spelled here because the bug only exists under it.
#:
#: `Foundry-Gate('cast')` caps a casting at eight `key_files`, so a casting
#: carving a whole package names the package once. This run's own manifest does
#: exactly that for casting 2, and a fixture with only file entries cannot show
#: the door failing on the spelling the cap pushes a lead into.
_DIRECTORY_KEY_FILE = "src/foundry_mcp/tools/orchestration/"


def _write_manifest(fdir: Path) -> None:
    """Three castings: ids, key files, and a `path#Symbol` cite in spec_text.

    The third owns a DIRECTORY entry (fallout FR-009). Its files are never
    named in the manifest — that is the whole point of the spelling, and the
    reason a resolver comparing entries as bare strings misses them.
    """
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
                    {
                        "id": 3,
                        "title": "The orchestration package",
                        "key_files": [_DIRECTORY_KEY_FILE, "tests/orchestration/"],
                        "spec_text": "carves the package",
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


# --------------------------------------------------------------------------- #
# fallout FR-009 (D-170, casting 7's concern C-080) — the file rung reaches
# DOWNWARD into a directory entry
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "target",
    [
        _DIRECTORY_KEY_FILE + "streams.py",
        _DIRECTORY_KEY_FILE + "transitions.py",
        _DIRECTORY_KEY_FILE + "sub/deeper.py",
        "./" + _DIRECTORY_KEY_FILE + "halt.py",
    ],
)
def test_a_target_beneath_a_directory_key_file_resolves_to_its_owner(
    run_env, target
):
    """THE FAILING-THEN-PASSING TEST FOR fallout FR-009 (concern C-080).

    Before this rung, `resolve_target` matched a target against `key_files` as
    exact-or-tail — up a path and sideways, never down — so every one of these
    was refused with ``CONCERN_TARGET_UNRESOLVED``. The failure is
    self-referential: the concern ledger's own door refused a concern about a
    file inside casting 2's package, which is precisely when a filer most needs
    it. C-079 had to be filed against the DIRECTORY STRING for that reason,
    because naming the module it meant would have been refused.
    """
    project_root, _fdir = run_env

    result = _open_one(project_root, target=target)

    assert result.get("ok") is True, result
    assert result["concern"]["target_kind"] == TARGET_KIND_FILE
    assert result["concern"]["target_casting_id"] == 3
    # `matched` carries the manifest ENTRY, not the target: the covered path
    # appears in nobody's `key_files` list literally, so a record naming only
    # the path sends a lead looking for a line that is not there.
    assert result["concern"]["target_matched"] == _DIRECTORY_KEY_FILE
    # ...and the target the filer typed survives verbatim beside it.
    assert result["concern"]["target"] == target.strip()


@pytest.mark.parametrize(
    "target",
    [
        # A sibling directory sharing a string prefix but not a SEGMENT.
        _DIRECTORY_KEY_FILE.rstrip("/") + "XX/a.py",
        # The directory itself with its mark stripped is not a path beneath it.
        _DIRECTORY_KEY_FILE.rstrip("/"),
        # A bare basename: the manifest alone cannot say WHICH directory holds
        # a `streams.py`, and guessing attaches the concern to a casting that
        # may not own it. Refused, and the hint teaches the spelling instead.
        "streams.py",
    ],
)
def test_a_directory_entry_reaches_downward_and_not_sideways(run_env, target):
    """fallout FR-009 — the widening must not become an over-match.

    Under-matching costs a refusal a filer can read and act on. Over-matching
    files the concern against a casting that does not own it, with nothing to
    say so — which is the direction D-170 was filed for, one character over.
    """
    project_root, _fdir = run_env

    result = _open_one(project_root, target=target)

    assert result["phase"] == CONCERN_TARGET_UNRESOLVED, result


def test_a_file_entry_is_answered_exactly_as_it_always_was(run_env):
    """fallout FR-009 — the fix WIDENS what matches and changes nothing that did.

    The exact-or-tail pass runs FIRST and over every casting, so a manifest
    with no directory entry is resolved exactly as it was before the coverage
    rung existed, and an exact entry can never lose to another casting's
    directory.
    """
    project_root, _fdir = run_env

    exact = _open_one(project_root, target="src/foundry_mcp/server.py")
    assert exact["concern"]["target_casting_id"] == 2
    assert exact["concern"]["target_matched"] == "src/foundry_mcp/server.py"

    tail = _open_one(project_root, target="server.py")
    assert tail["concern"]["target_casting_id"] == 2


def test_the_hint_says_a_directory_entry_stands_for_everything_beneath_it(run_env):
    """fallout FR-009, the second half of C-080's ask.

    A hint that lists `.../orchestration/` among "the key files" and explains
    nothing sends a filer who typed a module name looking for a file path the
    manifest will never contain — a covered file is in nobody's `key_files`
    list literally. The sentence is derived from the mark that decides the
    reading, so the two cannot drift into two answers.
    """
    project_root, _fdir = run_env

    result = _open_one(project_root, target="streams.py")

    assert result["phase"] == CONCERN_TARGET_UNRESOLVED
    assert _concerns._DIRECTORY_ENTRY_MEANING in result["hint"]
    assert _concerns._DIRECTORY_ENTRY_MARK in _concerns._DIRECTORY_ENTRY_MEANING
    assert _DIRECTORY_KEY_FILE in result["hint"]


def test_the_hint_stays_silent_about_directories_a_manifest_does_not_have(
    run_env,
):
    """fallout FR-009 — the hint describes THIS run, not the format in general.

    A note about directory entries on a manifest whose castings all name files
    is prose the reader must first disprove, which is the cost every hint pays
    for saying more than it measured.
    """
    project_root, fdir = run_env
    (fdir / "castings" / "manifest.json").write_text(
        json.dumps({"castings": [{"id": 1, "key_files": ["src/only/a/file.py"]}]}),
        encoding="utf-8",
    )

    result = _open_one(project_root, target="nowhere.py")

    assert result["phase"] == CONCERN_TARGET_UNRESOLVED
    assert _concerns._DIRECTORY_ENTRY_MEANING not in result["hint"]


def test_the_coverage_reading_agrees_with_every_committed_statement_of_it():
    """fallout FR-009 — THE PIN THAT STOPS A FOURTH SPELLING DRIFTING.

    C-080 asked whether this module could depend on an existing statement of
    "does this `key_files` entry cover this path" rather than adding a fifth.
    It cannot today, and the reasons are in `_key_file_reaches`'s own docstring:
    `orchestration/keyfiles.py#covers_path` is the right home and is not
    committed — a module-top import of a module that does not exist takes
    `server.py` down at startup for every tool — and `foundry_validate`'s is
    private and documents that its arguments arrive through that module's own
    normaliser, which is F0.9 validation policy rather than what a concern
    target means.

    So the fork is pinned by BEHAVIOUR instead of by import, which is the
    remedy this package already uses for a rule two layers read and neither may
    import (`_DELIBERATE_REDEFINITIONS['_INSPECT_PHASES']`). One corpus, every
    committed body, and a failure the day any two answer differently — which is
    what a drift would actually break, as opposed to a comment saying they
    agree.

    THE CORPUS ARRIVES PRE-NORMALISED, on purpose. What is held equal here is
    the COVERAGE reading; the path normalisers are separately spelled and their
    duplication is its own recorded row. Feeding raw spellings would make this
    fail on a normaliser difference and report it as a coverage disagreement.

    `keyfiles.covers_path` is driven TOO when it is importable, so the pin
    widens by itself on the day casting 2 commits the leaf. It is conditional
    only because that module does not exist in the committed tree yet, and the
    assertion below refuses to pass on a corpus no peer body saw — a pin that
    can go vacuous is a pin that stops being one.
    """
    from foundry_mcp.tools.foundry_validate import _key_file_covers

    peers = [("foundry_validate._key_file_covers", _key_file_covers)]
    try:  # pragma: no cover - present only once casting 2 commits the leaf
        from foundry_mcp.tools.orchestration.keyfiles import covers_path
    except ImportError:
        pass
    else:
        peers.append(("keyfiles.covers_path", covers_path))

    entries = [
        "src/foundry_mcp/tools/orchestration/",
        "tests/orchestration/",
        "src/foundry_mcp/server.py",
        "src/one.py",
        "",
        "   ",
    ]
    paths = [
        "src/foundry_mcp/tools/orchestration/streams.py",
        "src/foundry_mcp/tools/orchestration/sub/deeper.py",
        "src/foundry_mcp/tools/orchestration",
        "src/foundry_mcp/tools/orchestrationXX/a.py",
        "src/foundry_mcp/server.py",
        "src/one.py",
        "src/one.pyc",
        "other/src/one.py",
        "",
    ]

    disagreements = []
    for entry in entries:
        for path in paths:
            mine = _concerns._key_file_reaches(entry, path)
            for name, body in peers:
                if body(entry, path) != mine:
                    disagreements.append(f"{name}({entry!r}, {path!r}) != {mine}")

    assert disagreements == [], (
        "the coverage reading has forked: " + "; ".join(disagreements) + ". "
        "One of these bodies changed without the others. The exit is one "
        "repoint of tools/concerns.py onto the committed leaf, not a second "
        "edit here."
    )
    # ...and the corpus reached a real peer, so this cannot pass on an empty
    # comparison the day an import quietly stops resolving.
    assert peers, "no committed statement of the rule was driven"


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
# fallout AC-004 / CT-001 — the cycle stamp is REQUIRED, and never defaulted
#
# D-060 drove this door three ways with the run at cycle 4. `cycle=4` stored 4
# and `Foundry-Phase(inspect_start)` refused, which is the behaviour fallout
# AC-004 asks for. `cycle` OMITTED stored 0 and the gate PASSED. `cycle='4'`
# as a JSON string stored 0 and the gate PASSED too. The write arm declared
# `cycle: int = 0` and collapsed every bool, non-int and negative onto 0 with
# no refusal, so two of the three drives filed a concern into a cycle no door
# scopes on, and reported success. The READER is right —
# `open_cross_casting_concerns` scopes by exact equality because fallout
# GI-023 / ST-005 ask for the concerns "from the closing GRIND" — so the whole
# of the fix is a refusal at the writer.
# --------------------------------------------------------------------------- #


def test_an_omitted_cycle_is_refused_rather_than_stored_as_zero(run_env):
    """THE FAILING-THEN-PASSING TEST FOR fallout AC-004's omitted-cycle drive.

    Before this change the call below returned ``ok`` and appended an entry
    stamped cycle 0, which the cycle-scoped reader then hid from every cycle
    but 0. ``_open_one`` cannot express this drive because it always supplies a
    cycle, so the handler is called directly with the argument simply ABSENT —
    exactly as a dispatch that passes absence through as absence delivers it.
    """
    project_root, fdir = run_env

    result = foundry_concern(
        casting_id=1,
        target="2",
        text="casting 2 must register this handler",
        project_root=project_root,
    )

    assert result["phase"] == CONCERN_CYCLE_REQUIRED, result
    assert "not passed" in result["error"]
    # The hint names the argument the caller left out, in the call form.
    assert "cycle=" in result["hint"]
    # And nothing was written: a refused call leaves no stamp to misread.
    assert not _file(fdir, CONCERNS_FILENAME).exists()


@pytest.mark.parametrize(
    "bad, named",
    [
        ("4", "str"),
        (True, "boolean"),
        (False, "boolean"),
        (2.5, "float"),
        (4.0, "float"),
        (-1, "before the run"),
        (None, "not passed"),
        ([4], "list"),
    ],
)
def test_a_cycle_that_is_not_a_whole_non_negative_number_is_refused(
    run_env, bad, named
):
    """THE REFUSAL TEST FOR ``CONCERN_CYCLE_REQUIRED``, one row per shape the
    old arm silently coerced.

    ``'4'`` is the string drive D-060 recorded — a transport that hands a JSON
    value through unconverted delivers exactly this. ``True`` is excluded
    before ``int`` is tested for ``current_cycle``'s reason: ``True`` is not
    cycle 1. ``4.0`` is a WHOLE float and still refused, because a stamp read
    back by exact equality has no room for a value that only equals an integer
    sometimes. One token covers all of them; the MESSAGE names which arrived.
    """
    project_root, fdir = run_env

    result = _open_one(project_root, cycle=bad)

    assert result["phase"] == CONCERN_CYCLE_REQUIRED, result
    assert named in result["error"], result["error"]
    assert not _file(fdir, CONCERNS_FILENAME).exists()


def test_cycle_zero_is_a_real_cycle_and_is_stored_as_one(run_env):
    """The door judges the SHAPE of the value, never whether it is zero.

    A concern raised during CAST carries cycle 0, so 0 must pass. Telling that
    concern apart from one whose cycle was never passed is precisely what
    ``cycle: int = 0`` destroyed — absent and zero arrived identically, so
    neither could be refused without refusing both. The sentinel is ``None``
    for this reason and no other.
    """
    project_root, fdir = run_env

    result = foundry_concern(
        casting_id=1,
        cycle=0,
        target="2",
        text="raised during CAST, before the first GRIND",
        project_root=project_root,
    )

    assert result.get("error") is None, result
    assert _ledger(fdir)[0]["cycle"] == 0


def test_the_stored_stamp_is_the_one_the_inspect_start_rung_scopes_on(run_env):
    """fallout AC-004's harm, asserted at the layer that caused it.

    The rung calls ``open_cross_casting_concerns(fdir, cycle=<current>)``,
    which filters on exact equality. A stamp the WRITER guessed is a concern
    that door cannot see. This pins both directions: the cycle the caller
    declared is the cycle the reader finds the concern under, and no other
    cycle finds it.
    """
    project_root, fdir = run_env

    foundry_concern(
        casting_id=1,
        cycle=4,
        target="2",
        text="from cycle 4",
        project_root=project_root,
    )

    found = open_concerns_for_other_castings(fdir, cycle=4)
    assert [c["text"] for c in found] == ["from cycle 4"]
    assert open_concerns_for_other_castings(fdir, cycle=0) == []


def test_the_close_arm_asks_for_no_cycle(run_env):
    """The requirement is the WRITE arm's alone.

    A close moves a record that already carries its stamp, so a close call
    passes no cycle and must not be refused for the absence of one — the
    sentinel default would otherwise turn every close into a refusal.
    """
    project_root, fdir = run_env
    opened = _open_one(project_root)["concern"]

    closed = foundry_concern(
        close=opened["id"],
        reason="casting 2 landed the registration",
        project_root=project_root,
    )

    assert closed.get("error") is None, closed
    entry = _ledger(fdir)[0]
    assert entry["status"] == CONCERN_STATUS_CLOSED
    # The stamp the write arm recorded survives the close untouched.
    assert entry["cycle"] == 3


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


def test_a_close_whose_handoff_cannot_be_appended_says_so(run_env):
    """fallout FR-039 / ST-004, defects D-145 / D-146 — THE FAILING-THEN-PASSING
    TEST.

    That transition's guard column names TWO conditions for the close: "reason
    non-empty; a handoff record is appended". The first is a named refusal.
    The second was best-effort AND silent — `_close_concern` discarded
    `_append_close_handoff`'s outcome and the appender swallowed the failure —
    so a close whose required record was never written returned `ok: True` with
    nothing on it to say so.

    The contrast was already in the same function one line up: a render that
    cannot be written reports `render_problem` on an otherwise successful
    result. The asymmetry was the defect, not the decision to let the close
    proceed, so this drives the same arrangement on both channels and asserts
    they now answer the same way.
    """
    project_root, fdir = run_env
    opened = _open_one(project_root)["concern"]

    # The channel is made unwritable in the way the drive found it: a
    # directory where the appender opens a file, which raises IsADirectoryError
    # — an OSError, from inside the lazy import's frame.
    (fdir / "handoffs.jsonl").mkdir(parents=True, exist_ok=True)

    result = foundry_concern(
        close=opened["id"], reason="casting 2 registered it",
        project_root=project_root,
    )

    # The close still stands — the entry is closed inside a committed
    # transaction before the handoff is attempted at all.
    assert result.get("ok") is True, result
    assert _ledger(fdir)[0]["status"] == CONCERN_STATUS_CLOSED

    # ...and the guard artefact that did NOT get written says so, by name.
    problem = result.get("handoff_problem")
    assert problem is not None, result
    assert HANDOFF_EVENT_CONCERN_CLOSED in problem, problem
    assert "handoffs.jsonl" in problem, problem

    # The sibling channel, driven the same way, for the symmetry this restores.
    other = _open_one(project_root)["concern"]
    (fdir / CONCERNS_MARKDOWN_FILENAME).unlink()
    (fdir / CONCERNS_MARKDOWN_FILENAME).mkdir()
    rendered = foundry_concern(
        close=other["id"], reason="and this one", project_root=project_root
    )
    assert rendered.get("ok") is True, rendered
    assert rendered.get("render_problem") is not None, rendered


def test_a_close_whose_handoff_lands_reports_no_problem(run_env):
    """The other half of the same channel: silence means written.

    A key that appears only on failure is worth nothing if it also appears on
    success, and worth nothing if the failure arm is the only one anyone
    drives.
    """
    project_root, fdir = run_env
    opened = _open_one(project_root)["concern"]

    result = foundry_concern(
        close=opened["id"], reason="done", project_root=project_root
    )

    assert result.get("ok") is True, result
    assert "handoff_problem" not in result, result
    assert opened["id"] in _file(fdir, "handoffs.md").read_text(encoding="utf-8")


def test_the_ledger_stamps_come_from_the_leaf(run_env, monkeypatch):
    """fallout GI-024, defect D-124 — THE FAILING-THEN-PASSING TEST.

    ``_now`` here spelled ``datetime.now(timezone.utc).isoformat()`` inline —
    "a second derivation outside `foundry_state`" is that invariant's violation
    column verbatim — and this module's stamps are compared against the leaf's by the
    readers that scope a concern to a cycle. It is a call-through now.

    Two assertions, because the duplication had two halves: the module no
    longer holds the apparatus to derive a timestamp, and every stamp it
    writes is the leaf's answer.
    """
    project_root, fdir = run_env
    assert not hasattr(_concerns, "datetime"), (
        "the module imports datetime again — a second derivation is back"
    )

    monkeypatch.setattr(_concerns, "now_iso", lambda **_kwargs: "STAMP-1")
    opened = _open_one(project_root)["concern"]
    assert opened["recorded_at"] == "STAMP-1"

    monkeypatch.setattr(_concerns, "now_iso", lambda **_kwargs: "STAMP-2")
    closed = foundry_concern(
        close=opened["id"], reason="done", project_root=project_root
    )["concern"]
    assert closed["closed_at"] == "STAMP-2"

    # The handoff record carries the same stamp rather than deriving its own.
    rows = [
        json.loads(line)
        for line in _file(fdir, "handoffs.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert [r for r in rows if r.get("timestamp") == "STAMP-2"], rows


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


# --------------------------------------------------------------------------- #
# fallout GI-006 / GI-013, defect D-073 — the generated file is a place prose
# can be written, not only a place prose can survive having been written
# --------------------------------------------------------------------------- #


def test_prose_appended_after_a_render_survives_the_next_render(run_env):
    """fallout GI-013, defect D-073 — THE FAILING-THEN-PASSING TEST.

    fallout GI-013 reserves this document for prose: "``Foundry-Concern``
    writes a structured ledger; concerns.md STAYS PROSE". The marker used to be
    written only when a tail already existed, so a file generated with no prose
    in it had nowhere safe to put any — a lead ruling appended below the last
    entry carried no marker, was read as part of the generated region, and was
    destroyed on the next write. The preamble the same module emits promised
    the opposite in the same file.

    Driven exactly as the ledger record drove it: render, append a lead ruling,
    file a second concern.
    """
    project_root, fdir = run_env
    _open_one(project_root, text="first")

    path = _file(fdir, CONCERNS_MARKDOWN_FILENAME)
    ruling = "## Lead ruling: keep the old spelling in casting 2"
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(f"\n{ruling}\n")

    _open_one(project_root, text="second")

    rendered = path.read_text(encoding="utf-8")
    assert ruling in rendered, rendered
    assert rendered.count(ruling) == 1, rendered
    # And the generated half is still whole beside it.
    assert f"## {CONCERN_ID_PREFIX}-001" in rendered
    assert f"## {CONCERN_ID_PREFIX}-002" in rendered


def test_every_generated_file_carries_the_marker_even_with_no_prose_under_it(
    run_env,
):
    """The delimiter is where the generated region ENDS, so it is unconditional.

    A marker written only once there is already something to delimit delimits
    nothing: it is absent from exactly the file a reader would append to first.
    """
    project_root, fdir = run_env
    _open_one(project_root, text="first")

    rendered = _file(fdir, CONCERNS_MARKDOWN_FILENAME).read_text(encoding="utf-8")
    assert rendered.count(CARRIED_PROSE_MARKER) == 1, rendered
    # It is the LAST thing in the file, so an append lands under it.
    assert rendered.rstrip().endswith(CARRIED_PROSE_MARKER), rendered[-200:]


def test_the_preamble_states_the_rule_the_render_actually_enforces(run_env):
    """fallout GI-013: the promise in the file and the behaviour agree.

    The preamble is what a reader consults before typing into this document.
    It used to say prose "that predates the generated region" is preserved,
    which described the only prose the old render could carry — and the reader
    it misdirected is the one whose prose was destroyed.
    """
    project_root, fdir = run_env
    _open_one(project_root, text="first")

    rendered = _file(fdir, CONCERNS_MARKDOWN_FILENAME).read_text(encoding="utf-8")
    head = rendered.split(CARRIED_PROSE_MARKER)[0]
    assert "preserved verbatim by every later render" in head, head
    assert "predates the generated region or is written after" in head, head


def test_an_older_marker_spelling_still_carries_the_prose_under_it(run_env):
    """fallout GI-006 — the prose under an older marker is the only copy.

    Every archive already generated by this module carries the marker in the
    spelling that shipped before this one. A render that recognised only the
    current spelling would classify such a file as hand-written, carry its
    whole generated body forward as prose, and duplicate it — or, if it
    recognised the heading instead, drop the prose. Both are the
    replace-semantics write fallout GI-006 names.
    """
    project_root, fdir = run_env
    legacy = (
        "<!-- concerns.md: prose below this line predates the generated region "
        "and is preserved verbatim -->"
    )
    path = _file(fdir, CONCERNS_MARKDOWN_FILENAME)
    path.write_text(
        "# Foundry Concerns\n\nGenerated by an earlier release.\n\n"
        "## C-000 — open\n\nan entry from the older render\n\n"
        f"{legacy}\n## Concern: raised by hand under the old marker\n",
        encoding="utf-8",
    )

    _open_one(project_root, text="first")

    rendered = path.read_text(encoding="utf-8")
    assert "## Concern: raised by hand under the old marker" in rendered
    # Migrated to the current spelling, once, and the older body is not carried
    # forward as prose beside the ledger's own rendering of it.
    assert rendered.count(CARRIED_PROSE_MARKER) == 1, rendered
    assert legacy not in rendered, rendered
    assert "an entry from the older render" not in rendered, rendered
