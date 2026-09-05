"""F0.9 VALIDATE: who owns a requirement, and how many owners it may have.

Requirements: ``forge-specs/foundry-run-fallout/spec.md``. The rows each test
proves are named, with the symbol each lands on, in casting 7's completion
report and in the ``# evidence-for:`` headers of
``evidence/casting-7-ownership-consistency.log``,
``evidence/casting-7-requirement-span.log`` and
``evidence/casting-7-span-table.log``. They stay out of the prose below except
where naming one is the point: ``tests/test_spec_id_convention.py`` demands that
every three-digit requirement id in a docstring or comment in this directory
name its spec, and this release's qualification — ``fallout`` — did not exist
when this module was written, so the only spellings that would then have passed
the pin named a DIFFERENT spec's requirement. It exists now, so a citation that
earns its place carries it.

The two answers this module is built from, verbatim:

    "Persist `requirement_ids` per casting at F0.5, validated at F0.9"

    "Report the span; refuse at F0.9 when any requirement spans more than two
    castings without a recorded reason"

Two dimensions, and each is a claim the manifest makes that F0.9 can check.

  OWNERSHIP. `requirement_ids` is a persisted list; the casting's own
  <spec_requirements> excerpt is prose. They must agree in BOTH directions. A
  requirement declared in the excerpt but absent from the list has nobody
  answerable for it — the acceptance gate demands no evidence for it and a fix
  is routed to no casting. A requirement in the list that the excerpt never
  declares hands the teammate no text to build from while the manifest reports
  it covered.

  SPAN. How many castings own one requirement, computed from the PERSISTED
  field and never from the prose. Above the threshold the manifest must carry a
  recorded reason naming the id, or F0.9 refuses with a named token.

WHAT IS ACTUALLY AT RISK, and it is not the arithmetic. Both dimensions are
easy to write in a way that answers a slightly different question than the one
asked — counting ids a casting merely QUOTES as if it owned them, or exempting
a span because SOME reason was recorded rather than a reason for THAT id. So
the tests below drive the negative control beside every positive one: the
cross-reference line that is not a declaration, the mid-prose mention that is
not a declaration, and the recorded reason for a different id that does not
exempt.

Every test drives ``foundry_validate_castings`` itself against a ``tmp_path``
run directory with a written manifest and spec, and reads the returned
dimensions, issues and table. The module carries its own harness rather than
sharing one through ``conftest.py``, which is this suite's convention.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from foundry_mcp.tools.foundry import foundry_init
from foundry_mcp.tools.foundry_state import (
    ARCHIVE_DIR,
    clear_active_run,
    set_active_run,
)
from foundry_mcp.tools.foundry_validate import (
    REQUIREMENT_IDS_SCHEMA_FLOOR,
    REQUIREMENT_SPAN_EXCEEDED,
    REQUIREMENT_SPAN_MAX,
    _archive_schema_version,
    foundry_validate_castings,
    requirement_span_table,
)


# ── Harness ───────────────────────────────────────────────────────────────


def _run_validate(
    project_root: Path,
    castings: list[dict],
    *,
    spec_text: str = "",
    state: dict | None = None,
    manifest_extra: dict | None = None,
    complete: bool = False,
    run_name: str = "ownership-test",
) -> dict:
    """Write a minimal run and invoke the validator against it.

    ``spec_text`` defaults to empty so the dimensions this module is about are
    isolated from requirement coverage and the file-change-map cross-check.
    ``state`` writes state.json, which is where the archive schema marker lives
    — absent by default, which is what a run created before the marker existed
    looks like. ``state=None`` therefore also means "leave whatever is already
    there", which is what the one test below that creates its run through
    ``Foundry-Init`` relies on.

    ``run_name`` exists for that same one test: it points the manifest and the
    validator at a run directory this harness did not invent, so the state
    document the door reads is the one the real creating call wrote. Every
    other caller takes the default and the two are the same thing.

    ``complete=True`` writes the rest of what F0.5 emits — a prompt file per
    casting carrying the three blocks, a spec.md the excerpts are a verbatim
    copy of, and the stream-skip entry that tells F0.9 the intent matrix was
    not routed. It is what a test asserting the whole run VALIDATES needs; a
    test asserting one dimension does not, and paying for it everywhere would
    hide which dimension a failure came from.
    """
    fdir = project_root / ARCHIVE_DIR / run_name
    (fdir / "castings").mkdir(parents=True, exist_ok=True)
    manifest = {"castings": castings, "spec_type": "GREENFIELD"}
    if complete:
        manifest["stream_skips"] = [{"stream_id": "INTENT-01"}]
    manifest.update(manifest_extra or {})
    (fdir / "castings" / "manifest.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )
    if complete:
        for c in castings:
            (fdir / "castings" / f"casting-{c['id']}-prompt.md").write_text(
                "<mandatory_rules></mandatory_rules>\n"
                "<global_invariants></global_invariants>\n"
                f"<spec_requirements>\n{c.get('spec_text', '')}\n</spec_requirements>\n",
                encoding="utf-8",
            )
    (fdir / "spec.md").write_text(spec_text, encoding="utf-8")
    if state is not None:
        (fdir / "state.json").write_text(json.dumps(state), encoding="utf-8")

    set_active_run(run_name)
    try:
        return foundry_validate_castings(str(project_root))
    finally:
        clear_active_run()


def _casting(
    cid,
    *,
    excerpt: str = "",
    owns=None,
    split_reason: dict | None = None,
    include_owns: bool = True,
) -> dict:
    """One casting entry.

    ``excerpt`` is the verbatim <spec_requirements> blob, which is what
    ``spec_text`` holds in a real manifest. ``include_owns=False`` writes NO
    `requirement_ids` key at all, which is the un-migrated record shape — a
    different claim from an empty list, and the two must not be conflated.
    """
    entry = {
        "id": cid,
        "title": f"Casting {cid}",
        "spec_text": excerpt,
        "observable_truths": ["user sees X", "user sees Y", "user sees Z"],
        "key_files": [f"src/{cid}.py"],
        "must_haves": {
            "truths": ["does the thing"],
            "artifacts": [{"path": f"src/{cid}.py"}],
            "key_links": [{"from": f"src/{cid}.py", "to": "src/shared.py"}],
        },
    }
    if include_owns:
        entry["requirement_ids"] = list(owns or [])
    if split_reason is not None:
        entry["split_reason"] = split_reason
    return entry


def _ownership(result: dict) -> dict:
    return result["dimensions"]["requirement_ownership"]


def _issue_kinds(dimension: dict) -> list[str]:
    return sorted(i.get("issue", "") for i in dimension["issues"])


def _span(result: dict) -> dict:
    return result["dimensions"]["requirement_span"]


def _row(result: dict, requirement_id: str) -> dict:
    """The span row for one requirement, or a failure that names what is there."""
    rows = {r["id"]: r for r in _span(result)["rows"]}
    assert requirement_id in rows, sorted(rows)
    return rows[requirement_id]


def _sharers(count: int, *, reason_on=None, reason: str = "") -> list[dict]:
    """``count`` castings that all declare and own the same one requirement.

    ``reason_on`` is the casting id that records a ``split_reason``, which is
    the shape decompose emits: the reason sits beside the ownership it
    explains. ``reason_on=0`` writes it at the manifest's top level instead,
    the other position the manifest may record one in.
    """
    out = []
    for cid in range(1, count + 1):
        entry = _casting(cid, excerpt=CLEAN_EXCERPT, owns=["US-001", "FR-009"])
        if reason_on == cid:
            entry["split_reason"] = {"FR-009": reason}
        out.append(entry)
    return out


#: A run created under the current release: the schema marker at the floor.
CURRENT_RUN = {"archive_schema_version": REQUIREMENT_IDS_SCHEMA_FLOOR}

#: The excerpt shape F0.5 emits: bold bullets, typed-table rows and story
#: headings all declare, and the two shapes that are NOT declarations sit in it
#: as controls — a `Maps to:` cross-reference and an id quoted mid-prose.
DECLARES_TWO = (
    "### US-001: A fix reaches every surface of its rule\n"
    "\n"
    "- **FR-009** [from A-009]: persist the ownership list at decompose time\n"
    "  - Maps to: US-777\n"
    "\n"
    "The gate refuses a filing that cites FR-888 with no reproduction.\n"
)

#: The same two declarations with the two control lines removed, so the spec
#: this excerpt is copied from names exactly the ids the castings own. Used by
#: the tests that assert the WHOLE run validates, where an id nobody covers
#: would fail requirement coverage for a reason this module is not about.
CLEAN_EXCERPT = (
    "### US-001: A fix reaches every surface of its rule\n"
    "\n"
    "- **FR-009** [from A-009]: persist the ownership list at decompose time\n"
)


# ── The consistency check, in both directions ─────────────────────────────


def test_a_manifest_whose_lists_match_its_excerpts_passes_the_ownership_check(
    tmp_path: Path,
):
    """The positive control, and it comes first: a dimension that refused
    everything would pass every negative test below and block every run.

    The excerpt declares exactly two ids, the ownership list names exactly
    those two, and the dimension is clean.
    """
    castings = [_casting(1, excerpt=DECLARES_TWO, owns=["US-001", "FR-009"])]

    result = _run_validate(tmp_path, castings, state=CURRENT_RUN)
    dim = _ownership(result)

    assert dim["ok"] is True, dim["issues"]
    assert dim["issues"] == []
    assert dim["not_computable"] is False


def test_a_declaration_the_ownership_list_omits_is_refused_naming_both(
    tmp_path: Path,
):
    """"a manifest whose casting cites an id in `spec_text` that is absent from
    its `requirement_ids` is refused by F0.9 VALIDATE naming the casting and
    the id."

    Nobody is answerable for it: the acceptance gate demands no evidence and a
    fix is routed to no casting.
    """
    castings = [_casting(1, excerpt=DECLARES_TWO, owns=["US-001"])]

    result = _run_validate(tmp_path, castings, state=CURRENT_RUN)
    dim = _ownership(result)

    assert dim["ok"] is False
    assert _issue_kinds(dim) == ["declared_but_not_owned"]
    issue = dim["issues"][0]
    assert issue["casting"] == 1
    assert issue["ids"] == ["FR-009"]
    assert "FR-009" in issue["detail"]
    assert "Casting 1" in issue["detail"]
    # A blocking error, and the whole report still renders.
    assert result["passed"] is False
    assert any(
        i.get("dimension") == "requirement_ownership" and i.get("severity") == "error"
        for i in result["issues"]
    )
    # The hint names the two edits a lead can actually make.
    assert any(
        "FR-009" in h and "requirement_ids" in h for h in result["revision_hints"]
    )


def test_an_owned_id_the_excerpt_never_declares_is_refused_naming_both(
    tmp_path: Path,
):
    """"and a casting whose `requirement_ids` names an id its `spec_text` never
    cites" — the same rule read the other way.

    The teammate is handed no text to build from while the manifest reports the
    requirement covered, which is the more dangerous of the two directions
    because it reports success.
    """
    castings = [
        _casting(1, excerpt=DECLARES_TWO, owns=["US-001", "FR-009", "AC-042"])
    ]

    result = _run_validate(tmp_path, castings, state=CURRENT_RUN)
    dim = _ownership(result)

    assert dim["ok"] is False
    assert _issue_kinds(dim) == ["owned_but_not_declared"]
    issue = dim["issues"][0]
    assert issue["casting"] == 1
    assert issue["ids"] == ["AC-042"]
    assert "AC-042" in issue["detail"]
    assert result["passed"] is False


def test_both_directions_are_reported_together_rather_than_one_at_a_time(
    tmp_path: Path,
):
    """A lead fixing a manifest should learn everything wrong with it in one
    pass. Reporting one direction and returning would send them round the loop
    twice for one edit.
    """
    castings = [_casting(1, excerpt=DECLARES_TWO, owns=["US-001", "CT-011"])]

    dim = _ownership(_run_validate(tmp_path, castings, state=CURRENT_RUN))

    assert _issue_kinds(dim) == ["declared_but_not_owned", "owned_but_not_declared"]


def test_a_cross_reference_line_is_not_a_declaration(tmp_path: Path):
    """A `Maps to:` line names a requirement in a position that is not a
    declaration, so the casting is NOT answerable for it.

    This is the negative control that separates "which requirements does this
    casting own" from "which ones appear anywhere in this blob". A bare scan
    over the excerpt would credit this casting with a requirement another
    casting owns, then demand evidence for it at the acceptance gate — a green
    gate recording a lie.

    The cross-reference the fixture carries is the second line of
    ``DECLARES_TWO``, which is CODE and not a citation.
    """
    castings = [_casting(1, excerpt=DECLARES_TWO, owns=["US-001", "FR-009"])]

    dim = _ownership(_run_validate(tmp_path, castings, state=CURRENT_RUN))

    assert dim["ok"] is True, dim["issues"]


def test_an_id_quoted_mid_prose_is_not_a_declaration(tmp_path: Path):
    """The fixture's last line quotes a requirement inside another
    requirement's own statement text, mid-sentence and mid-line.

    Same control, the other shape, and it is the one the position rule was
    filed against: a requirement mentioned once inside a truth's prose was
    collected as one of the casting's demanded ones, and the teammate's only
    way through the gate was to bind a knowingly false evidence header to an
    unrelated log.
    """
    castings = [_casting(1, excerpt=DECLARES_TWO, owns=["US-001", "FR-009"])]

    dim = _ownership(_run_validate(tmp_path, castings, state=CURRENT_RUN))

    assert dim["issues"] == []


def test_every_casting_is_judged_not_only_the_first(tmp_path: Path):
    """A loop that returned on the first offender would report one casting and
    leave the rest of the manifest unexamined.
    """
    castings = [
        _casting(1, excerpt=DECLARES_TWO, owns=["US-001", "FR-009"]),
        _casting(2, excerpt=DECLARES_TWO, owns=["US-001"]),
        _casting(3, excerpt=DECLARES_TWO, owns=["US-001", "FR-009", "OT-038"]),
    ]

    dim = _ownership(_run_validate(tmp_path, castings, state=CURRENT_RUN))

    offenders = sorted(i["casting"] for i in dim["issues"])
    assert offenders == [2, 3]


# ── The legacy-versus-new-run reading ─────────────────────────────────────


def test_an_archive_that_predates_the_field_reports_not_computable_and_passes(
    tmp_path: Path,
):
    """"on a legacy manifest without the field the check reports not computable
    rather than failing."

    Every archive written before the field existed must keep validating, so the
    dimension is `ok`, the fact is in the payload, and the explanation is an
    informational issue rather than a blocking one.
    """
    castings = [
        _casting(1, excerpt=CLEAN_EXCERPT, include_owns=False),
        _casting(2, excerpt=CLEAN_EXCERPT, include_owns=False),
    ]

    result = _run_validate(
        tmp_path, castings, spec_text=CLEAN_EXCERPT, state={"cycle": 0}, complete=True
    )
    dim = _ownership(result)

    assert dim["not_computable"] is True
    assert dim["ok"] is True
    assert dim["archive_schema_version"] == 0
    assert _issue_kinds(dim) == ["requirement_ids_not_computable"]
    assert dim["issues"][0]["severity"] == "info"
    # The run still validates, which is the half of this rule that matters: an
    # archive written before the field existed must not be blocked by it.
    assert result["passed"] is True, result["issues"]
    # And the explanation is reported without moving a counted number: an
    # informational entry stays inside the dimension rather than joining the
    # top-level list, where it would be counted as a warning.
    assert not any(
        i.get("dimension") == "requirement_ownership" for i in result["issues"]
    )
    assert result["summary"]["error_count"] == 0


def test_an_archive_with_no_state_document_at_all_reports_not_computable(
    tmp_path: Path,
):
    """A run whose state.json was never written reads as below the floor. An
    absent marker and a marker below the floor are the same answer — this run
    predates the field — and neither may raise.
    """
    castings = [_casting(1, excerpt=DECLARES_TWO, include_owns=False)]

    dim = _ownership(_run_validate(tmp_path, castings))

    assert dim["not_computable"] is True
    assert dim["ok"] is True


def test_a_marker_that_is_not_an_integer_reads_as_below_the_floor(tmp_path: Path):
    """A schema marker of the wrong type is not a reason to raise, and not a
    reason to fail closed on an archive that may well predate the field.
    """
    castings = [_casting(1, excerpt=DECLARES_TWO, include_owns=False)]

    dim = _ownership(
        _run_validate(tmp_path, castings, state={"archive_schema_version": "four"})
    )

    assert dim["archive_schema_version"] == 0
    assert dim["not_computable"] is True


def test_a_run_created_under_the_current_schema_is_refused_for_a_missing_list(
    tmp_path: Path,
):
    """"a run created under the current schema is refused when the list is
    missing."

    The other half of "fail closed only for new runs": the same manifest that
    passes on a legacy archive is refused here, and the refusal names the
    casting.
    """
    castings = [_casting(1, excerpt=DECLARES_TWO, include_owns=False)]

    result = _run_validate(tmp_path, castings, state=CURRENT_RUN)
    dim = _ownership(result)

    assert dim["not_computable"] is False
    assert dim["ok"] is False
    assert _issue_kinds(dim) == ["missing_requirement_ids"]
    assert dim["issues"][0]["casting"] == 1
    assert result["passed"] is False


def test_a_run_this_server_created_is_refused_the_same_way_a_hand_written_one_is(
    tmp_path: Path,
):
    """The door reads the marker a REAL run-creating call writes, not a literal.

    Every other test in this section stands a hand-written state document in
    for "a run created under the current release", and a stand-in nobody checks
    is a suite that proves the door against a shape that need not exist. It did
    not exist: for a whole cycle nothing stamped the marker at creation, so a
    run this server had made minutes earlier read as version 0 — the same
    answer an archive written long before the field existed gives — and the
    fail-closed half of fallout FR-054 could not fire on one real run while
    every test above this one passed. A fixture cannot see that, because the
    value it asserts against is the value it just wrote.

    So this is the one test in the module that takes no state document from the
    harness. It creates a run through the real door, and ``state=None`` below
    is the load-bearing argument: what that door wrote is left exactly where it
    is, and F0.9 is driven against it. A marker written under another key, into
    another document, with another value, or on no branch at all fails here and
    passes everything above it.

    The first assertion reads the marker through ``_archive_schema_version``,
    the reader the dimension itself compares — not the raw key — because a
    marker of the wrong TYPE under the right key coerces to 0 there and would
    sail past an assertion on the literal, leaving the refusal below to explain
    a failure whose cause is two frames away.
    """
    spec = tmp_path / "spec.md"
    spec.write_text(DECLARES_TWO, encoding="utf-8")

    created = foundry_init(spec_path=str(spec), project_root=str(tmp_path))
    state = json.loads(
        (Path(created["foundry_dir"]) / "state.json").read_text(encoding="utf-8")
    )

    assert _archive_schema_version(state) >= REQUIREMENT_IDS_SCHEMA_FLOOR, (
        f"a run this server just created reads as schema "
        f"{_archive_schema_version(state)}, below the "
        f"{REQUIREMENT_IDS_SCHEMA_FLOOR} every CURRENT_RUN test above assumes — "
        f"so those tests describe a run that cannot be created and the "
        f"fail-closed half of this rule is unreachable. state.json: {state}"
    )

    castings = [_casting(1, excerpt=DECLARES_TWO, include_owns=False)]
    result = _run_validate(
        tmp_path, castings, run_name=created["run_name"], state=None
    )
    dim = _ownership(result)

    assert dim["archive_schema_version"] == _archive_schema_version(state)
    assert dim["not_computable"] is False
    assert dim["ok"] is False
    assert _issue_kinds(dim) == ["missing_requirement_ids"]
    assert dim["issues"][0]["severity"] == "error"
    assert dim["issues"][0]["casting"] == 1
    assert result["passed"] is False


def test_a_partly_filled_manifest_is_judged_whatever_the_schema(tmp_path: Path):
    """Not computable needs BOTH halves: no casting carrying the field AND an
    archive below the floor.

    Somebody has started populating this one, so its gaps are real and are
    reported even though the schema marker is absent. Answering "not
    computable" here would let a half-migrated manifest hide behind its own
    incompleteness.
    """
    castings = [
        _casting(1, excerpt=DECLARES_TWO, owns=["US-001", "FR-009"]),
        _casting(2, excerpt=DECLARES_TWO, include_owns=False),
    ]

    dim = _ownership(_run_validate(tmp_path, castings, state={"cycle": 0}))

    assert dim["not_computable"] is False
    assert _issue_kinds(dim) == ["missing_requirement_ids"]
    assert dim["issues"][0]["casting"] == 2


def test_an_empty_list_is_a_claim_and_an_absent_one_is_not(tmp_path: Path):
    """Presence and emptiness are different claims. A casting that owns nothing
    and says so is checkable — and is checked: its excerpt must declare nothing
    either.
    """
    castings = [_casting(1, excerpt="Nothing declared here.\n", owns=[])]

    dim = _ownership(_run_validate(tmp_path, castings, state=CURRENT_RUN))

    assert dim["not_computable"] is False
    assert dim["ok"] is True, dim["issues"]


def test_a_requirement_ids_field_of_the_wrong_type_is_reported_not_raised(
    tmp_path: Path,
):
    """A `requirement_ids` that is a string is not a shape the manifest's own
    nested-shape guard judges, and a tool never raises across the MCP boundary.

    It reads as present-and-empty, so every id the excerpt declares is reported
    unowned by name — the operator learns what is wrong with the file rather
    than receiving a traceback.
    """
    castings = [_casting(1, excerpt=DECLARES_TWO)]
    castings[0]["requirement_ids"] = "US-001, FR-009"

    result = _run_validate(tmp_path, castings, state=CURRENT_RUN)
    dim = _ownership(result)

    assert isinstance(result, dict)
    assert _issue_kinds(dim) == ["declared_but_not_owned"]
    assert dim["issues"][0]["ids"] == ["FR-009", "US-001"]


def test_a_spec_text_of_the_wrong_type_is_reported_not_raised(tmp_path: Path):
    """The sibling read, guarded the same way. A `spec_text` that is not a
    string reaches a line-splitting derivation, so an unguarded read raises out
    of F0.9 rather than rendering the report.
    """
    castings = [_casting(1, owns=["US-001"])]
    castings[0]["spec_text"] = {"not": "a string"}

    result = _run_validate(tmp_path, castings, state=CURRENT_RUN)  # must not raise
    dim = _ownership(result)

    assert isinstance(result, dict)
    assert _issue_kinds(dim) == ["owned_but_not_declared"]
    assert dim["issues"][0]["ids"] == ["US-001"]


# ── The archive-schema marker reaches the cache fingerprint ───────────────


def test_a_schema_bump_alone_invalidates_a_cached_pass(tmp_path: Path):
    """The cache is keyed on what the verdict depends on, and the verdict now
    depends on the archive schema marker.

    Driven end to end: the same manifest passes on a legacy archive, the marker
    is bumped with no byte of the manifest or the spec touched, and the second
    call must recompute and refuse rather than serve the cached pass.
    """
    castings = [_casting(1, excerpt=CLEAN_EXCERPT, include_owns=False)]
    args = dict(spec_text=CLEAN_EXCERPT, complete=True)

    first = _run_validate(tmp_path, castings, state={"cycle": 0}, **args)
    assert first["passed"] is True, first["issues"]
    assert first["cache"]["hit"] is False

    # Proof the cache is live at all, or the assertion below proves nothing.
    again = _run_validate(tmp_path, castings, state={"cycle": 0}, **args)
    assert again["cache"]["hit"] is True

    second = _run_validate(tmp_path, castings, state=CURRENT_RUN, **args)

    assert second["cache"]["hit"] is False
    assert second["passed"] is False
    assert _issue_kinds(_ownership(second)) == ["missing_requirement_ids"]


# ── The span, and the token that fires above the threshold ────────────────


def test_the_span_table_names_every_requirement_the_spec_declares(tmp_path: Path):
    """"A lead running F0.9 VALIDATE sees a span table listing every
    requirement id the spec declares, the castings that own it, and the number
    of owners."

    Every id, not only the ones with a problem: the ones with a single owner
    are the ownership picture too, and a table that showed only failures would
    tell a lead nothing about the slice they are about to build.
    """
    castings = [
        _casting(1, excerpt=CLEAN_EXCERPT, owns=["US-001", "FR-009"]),
        _casting(2, excerpt=CLEAN_EXCERPT, owns=["US-001", "FR-009"]),
    ]

    result = _run_validate(
        tmp_path, castings, spec_text=CLEAN_EXCERPT, state=CURRENT_RUN, complete=True
    )

    assert sorted(r["id"] for r in _span(result)["rows"]) == ["FR-009", "US-001"]
    assert _row(result, "US-001")["owners"] == [1, 2]
    assert _row(result, "US-001")["span"] == 2
    assert _span(result)["threshold"] == REQUIREMENT_SPAN_MAX


def test_a_requirement_with_one_owner_is_a_row_like_any_other(tmp_path: Path):
    """A single owner is the common case and the table's most useful row: it is
    how a lead confirms who to dispatch a fix to.
    """
    castings = [
        _casting(1, excerpt=CLEAN_EXCERPT, owns=["US-001", "FR-009"]),
    ]

    result = _run_validate(
        tmp_path, castings, spec_text=CLEAN_EXCERPT, state=CURRENT_RUN, complete=True
    )

    assert _row(result, "FR-009") == {
        "id": "FR-009",
        "owners": [1],
        "span": 1,
        "split_reason": None,
    }
    assert _span(result)["ok"] is True
    assert result["passed"] is True, result["issues"]


def test_a_span_at_the_threshold_passes(tmp_path: Path):
    """The boundary itself is accepted: "more than two" is the refusal, so two
    is the largest span that needs no reason. An off-by-one here would demand a
    recorded reason for every ordinary two-surface requirement in the spec.
    """
    result = _run_validate(
        tmp_path,
        _sharers(REQUIREMENT_SPAN_MAX),
        spec_text=CLEAN_EXCERPT,
        state=CURRENT_RUN,
        complete=True,
    )

    assert _row(result, "FR-009")["span"] == REQUIREMENT_SPAN_MAX
    assert _span(result)["ok"] is True
    assert _span(result)["issues"] == []
    assert result["passed"] is True, result["issues"]


def test_a_span_above_the_threshold_with_no_reason_refuses_with_the_token(
    tmp_path: Path,
):
    """"F0.9 refuses a requirement spanning three castings without
    `split_reason`" — the token, the id and all three owners, named.

    A refusal that named only the id would leave the lead grepping the manifest
    for who has it; the owners are the actionable half.
    """
    result = _run_validate(
        tmp_path,
        _sharers(REQUIREMENT_SPAN_MAX + 1),
        spec_text=CLEAN_EXCERPT,
        state=CURRENT_RUN,
        complete=True,
    )
    dim = _span(result)

    assert dim["ok"] is False
    errors = [i for i in dim["issues"] if i.get("severity") == "error"]
    assert len(errors) == 2, dim["issues"]  # both shared ids are over
    offender = next(i for i in errors if i["id"] == "FR-009")
    assert offender["issue"] == REQUIREMENT_SPAN_EXCEEDED
    assert offender["castings"] == [1, 2, 3]
    assert offender["span"] == 3
    assert REQUIREMENT_SPAN_EXCEEDED in offender["detail"]
    assert "FR-009" in offender["detail"]
    for owner in ("#1", "#2", "#3"):
        assert owner in offender["detail"]
    assert result["passed"] is False


def test_the_refusal_hint_names_the_two_exits_a_lead_can_take(tmp_path: Path):
    """A refusal with no way out teaches the reader to reach for --no-verify.

    Both exits are real: regroup the requirement onto at most the threshold, or
    record a reason. The second is driven by the very next test, which takes it
    and passes.
    """
    result = _run_validate(
        tmp_path,
        _sharers(REQUIREMENT_SPAN_MAX + 1),
        spec_text=CLEAN_EXCERPT,
        state=CURRENT_RUN,
        complete=True,
    )
    offender = next(
        i for i in _span(result)["issues"] if i.get("id") == "FR-009"
    )

    assert "split_reason" in offender["hint"]
    assert str(REQUIREMENT_SPAN_MAX) in offender["hint"]
    assert any(
        REQUIREMENT_SPAN_EXCEEDED in h and "FR-009" in h
        for h in result["revision_hints"]
    )
    top = [i for i in result["issues"] if i.get("dimension") == "requirement_span"]
    assert len(top) == 1
    assert REQUIREMENT_SPAN_EXCEEDED in top[0]["message"]
    assert "FR-009" in top[0]["message"]


def test_the_same_manifest_passes_once_a_reason_names_the_id(tmp_path: Path):
    """"adding a recorded reason for that id makes the same manifest pass, and
    the reason is printed."

    The SAME manifest — same castings, same ownership, same span — with one
    `split_reason` entry added. Anything else changing between the two would
    make this prove nothing.
    """
    reason = "the door, its report row and its command prose cannot share an owner"
    before = _sharers(REQUIREMENT_SPAN_MAX + 1)
    after = _sharers(REQUIREMENT_SPAN_MAX + 1, reason_on=1, reason=reason)
    assert [c["requirement_ids"] for c in before] == [
        c["requirement_ids"] for c in after
    ]

    refused = _run_validate(
        tmp_path, before, spec_text=CLEAN_EXCERPT, state=CURRENT_RUN, complete=True
    )
    assert refused["passed"] is False

    allowed = _run_validate(
        tmp_path, after, spec_text=CLEAN_EXCERPT, state=CURRENT_RUN, complete=True
    )

    row = _row(allowed, "FR-009")
    assert row["span"] == 3
    assert row["split_reason"] == reason
    # The recorded reason is PRINTED, not merely honoured.
    recorded = [
        i for i in _span(allowed)["issues"] if i.get("issue") == "requirement_span_recorded"
    ]
    assert len(recorded) == 1
    assert recorded[0]["id"] == "FR-009"
    assert reason in recorded[0]["detail"]
    assert recorded[0]["severity"] == "info"
    # The OTHER shared requirement is still over the threshold with no reason,
    # so the run is still refused — the exemption is for the requirement the
    # reason names and for nothing else.
    assert any(
        i.get("id") == "US-001" and i.get("issue") == REQUIREMENT_SPAN_EXCEEDED
        for i in _span(allowed)["issues"]
    )


def test_a_reason_recorded_at_the_manifest_top_level_exempts_too(tmp_path: Path):
    """The other position a reason may be recorded in.

    Both are authoritative and one derivation reads both, so a lead who records
    the reason where it belongs — beside the casting, or at the top for a
    reason that belongs to no single casting — is not refused for choosing the
    other one.
    """
    reason = "one requirement, three surfaces, no shared owner"
    castings = _sharers(REQUIREMENT_SPAN_MAX + 1)

    result = _run_validate(
        tmp_path,
        castings,
        spec_text=CLEAN_EXCERPT,
        state=CURRENT_RUN,
        complete=True,
        manifest_extra={"split_reason": {"FR-009": reason, "US-001": reason}},
    )

    assert _row(result, "FR-009")["split_reason"] == reason
    assert _span(result)["ok"] is True
    assert result["passed"] is True, result["issues"]


def test_a_reason_for_a_different_id_does_not_exempt(tmp_path: Path):
    """The negative control that separates a recorded reason from a blanket
    waiver: a reason names an id, and exempts THAT id.

    Without this the cheapest way past the gate would be to record any reason
    at all, which is a waiver dressed as a decision.
    """
    castings = _sharers(
        REQUIREMENT_SPAN_MAX + 1, reason_on=1, reason="about something else"
    )
    castings[0]["split_reason"] = {"OT-038": "a requirement no casting here owns"}

    result = _run_validate(
        tmp_path, castings, spec_text=CLEAN_EXCERPT, state=CURRENT_RUN, complete=True
    )

    assert _row(result, "FR-009")["split_reason"] is None
    assert any(
        i.get("id") == "FR-009" and i.get("issue") == REQUIREMENT_SPAN_EXCEEDED
        for i in _span(result)["issues"]
    )
    assert result["passed"] is False


def test_the_span_is_computed_from_the_persisted_field_and_not_the_prose(
    tmp_path: Path,
):
    """"computes each requirement id's span from `requirement_ids`" — the
    persisted claim, never the excerpt.

    Driven where the two answers differ: three castings all DECLARE the same
    requirement in their excerpts, and only one owns it. The span is one. A
    prose-derived span would read three and refuse a manifest whose ownership
    is unambiguous — and the ownership dimension is what reports the excerpts
    that disagree, which is a different finding with a different fix.
    """
    castings = [
        _casting(1, excerpt=CLEAN_EXCERPT, owns=["US-001", "FR-009"]),
        _casting(2, excerpt=CLEAN_EXCERPT, owns=[]),
        _casting(3, excerpt=CLEAN_EXCERPT, owns=[]),
    ]

    result = _run_validate(
        tmp_path, castings, spec_text=CLEAN_EXCERPT, state=CURRENT_RUN, complete=True
    )

    assert _row(result, "FR-009")["span"] == 1
    assert _span(result)["ok"] is True
    # ...and the disagreement IS reported, by the dimension whose question it is.
    assert _issue_kinds(_ownership(result)) == [
        "declared_but_not_owned",
        "declared_but_not_owned",
    ]


def test_an_id_a_casting_owns_that_the_spec_never_declares_is_still_spanned(
    tmp_path: Path,
):
    """Membership is the union of what the spec declares and what castings own.

    An id could otherwise carry three owners and no row at all, and the span
    rule — which is about ownership — would have nothing to act on.
    """
    castings = _sharers(REQUIREMENT_SPAN_MAX + 1)
    for c in castings:
        c["requirement_ids"] = ["US-001", "FR-009", "AC-042"]
        c["spec_text"] = CLEAN_EXCERPT + "\n- **AC-042** [from A-017]: the span rule\n"

    result = _run_validate(
        tmp_path, castings, spec_text=CLEAN_EXCERPT, state=CURRENT_RUN, complete=True
    )

    assert "AC-042" not in set(CLEAN_EXCERPT.split())
    assert _row(result, "AC-042")["span"] == 3
    assert any(
        i.get("id") == "AC-042" and i.get("issue") == REQUIREMENT_SPAN_EXCEEDED
        for i in _span(result)["issues"]
    )


def test_the_span_reports_not_computable_on_an_archive_predating_the_field(
    tmp_path: Path,
):
    """The span is computed from the persisted field, so an archive that has no
    such field has no span to report — and must not be blocked by one.
    """
    castings = [
        _casting(1, excerpt=CLEAN_EXCERPT, include_owns=False),
        _casting(2, excerpt=CLEAN_EXCERPT, include_owns=False),
    ]

    result = _run_validate(
        tmp_path, castings, spec_text=CLEAN_EXCERPT, state={"cycle": 0}, complete=True
    )
    dim = _span(result)

    assert dim["not_computable"] is True
    assert dim["ok"] is True
    assert dim["rows"] == []
    assert _issue_kinds(dim) == ["requirement_span_not_computable"]
    assert result["passed"] is True, result["issues"]


def test_a_split_reason_of_the_wrong_type_is_ignored_not_raised(tmp_path: Path):
    """`split_reason` is not a shape the manifest's nested-shape guard judges.

    A string where a map belongs must leave the id unexempted and the report
    rendered — a tool never raises across the MCP boundary, and failing OPEN
    here would be worse than failing closed: it would waive the rule.
    """
    castings = _sharers(REQUIREMENT_SPAN_MAX + 1)
    castings[0]["split_reason"] = "because I said so"

    result = _run_validate(
        tmp_path,
        castings,
        spec_text=CLEAN_EXCERPT,
        state=CURRENT_RUN,
        complete=True,
        manifest_extra={"split_reason": ["not", "a", "map"]},
    )

    assert isinstance(result, dict)
    assert _row(result, "FR-009")["split_reason"] is None
    assert result["passed"] is False


# ── The table in the F0.9 payload ─────────────────────────────────────────


def test_the_span_table_ships_as_records_and_as_a_rendered_block(tmp_path: Path):
    """"The span table appears in the F0.9 output."

    Both ways, because the tool has no display module of its own: `rows` for a
    reader that will render them — the F6 report draws the same table — and
    `text` for the lead reading F0.9's output directly.
    """
    castings = [
        _casting(1, excerpt=CLEAN_EXCERPT, owns=["US-001", "FR-009"]),
        _casting(2, excerpt=CLEAN_EXCERPT, owns=["FR-009"]),
    ]
    castings[1]["spec_text"] = (
        "- **FR-009** [from A-009]: persist the ownership list at decompose time\n"
    )

    result = _run_validate(
        tmp_path, castings, spec_text=CLEAN_EXCERPT, state=CURRENT_RUN, complete=True
    )
    table = result["requirement_span"]

    assert table["threshold"] == REQUIREMENT_SPAN_MAX
    assert table["not_computable"] is False
    assert [r["id"] for r in table["rows"]] == ["FR-009", "US-001"]
    assert "| requirement | owners | span | recorded reason |" in table["text"]
    assert "| FR-009 | #1, #2 | 2 |" in table["text"]
    assert "| US-001 | #1 | 1 |" in table["text"]


def test_one_computation_feeds_the_table_and_the_refusal(tmp_path: Path):
    """"Compute it ONCE and let both the refusal and the table read that one
    computation, so the printed table and the refusal can never disagree about
    who owns what."

    Asserted as identity, not as equality: two lists that happen to match today
    are two computations that can drift tomorrow, which is the whole shape this
    rule exists to prevent.
    """
    result = _run_validate(
        tmp_path,
        _sharers(REQUIREMENT_SPAN_MAX + 1),
        spec_text=CLEAN_EXCERPT,
        state=CURRENT_RUN,
        complete=True,
    )

    assert result["requirement_span"]["rows"] is _span(result)["rows"]
    offender = next(
        i for i in _span(result)["issues"] if i.get("id") == "FR-009"
    )
    assert offender["castings"] == _row(result, "FR-009")["owners"]


def test_the_table_is_present_on_a_passing_manifest_as_well_as_a_failing_one(
    tmp_path: Path,
):
    """The table is a REPORT, not a failure artefact. A lead reading a green
    F0.9 should still see who owns what before dispatching the wave.
    """
    passing = _run_validate(
        tmp_path,
        _sharers(REQUIREMENT_SPAN_MAX),
        spec_text=CLEAN_EXCERPT,
        state=CURRENT_RUN,
        complete=True,
    )
    assert passing["passed"] is True, passing["issues"]
    assert len(passing["requirement_span"]["rows"]) == 2
    assert "| FR-009 | #1, #2 | 2 |" in passing["requirement_span"]["text"]

    failing = _run_validate(
        tmp_path,
        _sharers(REQUIREMENT_SPAN_MAX + 1),
        spec_text=CLEAN_EXCERPT,
        state=CURRENT_RUN,
        complete=True,
    )
    assert failing["passed"] is False
    assert len(failing["requirement_span"]["rows"]) == 2
    assert "| FR-009 | #1, #2, #3 | 3 |" in failing["requirement_span"]["text"]


def test_an_exempted_row_carries_its_recorded_reason_into_the_table(tmp_path: Path):
    """"with the recorded reason on any exempted row."

    A waiver nobody can see is a waiver nobody reviews, so the reason travels
    with the row rather than living only in the manifest a reader would have to
    go open.
    """
    reason = "the door and its report row cannot share an owner"
    result = _run_validate(
        tmp_path,
        _sharers(REQUIREMENT_SPAN_MAX + 1, reason_on=2, reason=reason),
        spec_text=CLEAN_EXCERPT,
        state=CURRENT_RUN,
        complete=True,
    )

    assert _row(result, "FR-009")["split_reason"] == reason
    assert f"| FR-009 | #1, #2, #3 | 3 | {reason} |" in result["requirement_span"]["text"]
    # A row with no reason prints one spelling of "nothing", not an empty cell.
    assert "| US-001 | #1, #2, #3 | 3 | — |" in result["requirement_span"]["text"]


def test_the_table_says_so_rather_than_rendering_empty_when_not_computable(
    tmp_path: Path,
):
    """An empty table reads as "this spec has no requirements", which is a
    different and alarming claim from "this archive predates the field".
    """
    castings = [_casting(1, excerpt=CLEAN_EXCERPT, include_owns=False)]

    result = _run_validate(
        tmp_path, castings, spec_text=CLEAN_EXCERPT, state={"cycle": 0}, complete=True
    )
    table = result["requirement_span"]

    assert table["not_computable"] is True
    assert table["rows"] == []
    assert "not computable" in table["text"]
    assert str(REQUIREMENT_IDS_SCHEMA_FLOOR) in table["text"]
    assert "|---|" not in table["text"]


def test_the_table_orders_its_rows_deterministically(tmp_path: Path):
    """A table whose row order depends on dict iteration is a table two runs
    disagree about, and the F6 report renders these same records.
    """
    excerpt = (
        "- **US-001** [from A-009]: a fix reaches every surface\n"
        "- **FR-009** [from A-009]: persist the ownership list\n"
        "- **AC-042** [from A-017]: refuse above the threshold\n"
        "- **OT-038** [from A-017]: three castings without a reason\n"
    )
    castings = [_casting(1, excerpt=excerpt, owns=["OT-038", "AC-042", "FR-009", "US-001"])]

    first = _run_validate(
        tmp_path, castings, spec_text=excerpt, state=CURRENT_RUN, complete=True
    )
    second = _run_validate(
        tmp_path, list(reversed(castings)), spec_text=excerpt, state=CURRENT_RUN,
        complete=True,
    )

    assert [r["id"] for r in first["requirement_span"]["rows"]] == [
        "AC-042", "FR-009", "OT-038", "US-001",
    ]
    assert first["requirement_span"]["text"] == second["requirement_span"]["text"]


# ── The archives this release has to keep validating ──────────────────────
#
# THE SHAPES BELOW WERE MEASURED, NOT ASSUMED. Read off the three real archives
# in this repository at the time this was written:
#
#   foundry-archive/daring-orca      archive_schema_version 3, 8 castings,
#                                    none carrying `requirement_ids`
#   foundry-archive/thunder-viper    no schema marker at all, 6 castings, none
#                                    carrying `requirement_ids`
#   foundry-archive/grand-vulture    no schema marker at all, 6 castings, none
#                                    carrying `requirement_ids`
#
# Every one of them has requirement ids inside each casting's `spec_text` blob
# and nowhere else, which is the state this field was added to replace — so all
# three are exactly the archive a naive check would refuse, and all three must
# validate. The shapes are rebuilt here against `tmp_path` rather than read out
# of `foundry-archive/`, for two reasons: the validator WRITES to the run it
# judges (a pass marker and a cache), which has no business touching a sealed
# archive, and those directories are untracked, so a test that read them would
# pass or skip depending on who ran it. The live check against the real
# directories is the test after these; it skips where they are absent.


def test_the_shape_of_a_schema_three_archive_still_validates(tmp_path: Path):
    """`daring-orca`'s shape: a schema marker below the floor, eight castings,
    no ownership list on any of them, requirement ids only in the prose.

    The marker is 3 rather than absent, which is the case a floor comparison
    has to get right and a truthiness test would not: `3` is present, non-zero
    and still below the floor.
    """
    castings = [
        _casting(cid, excerpt=CLEAN_EXCERPT, include_owns=False) for cid in range(1, 9)
    ]

    result = _run_validate(
        tmp_path,
        castings,
        spec_text=CLEAN_EXCERPT,
        state={"archive_schema_version": 3, "cycle": 29, "phase": "HALTED"},
        complete=True,
    )

    assert _ownership(result)["archive_schema_version"] == 3
    assert _ownership(result)["not_computable"] is True
    assert _span(result)["not_computable"] is True
    assert result["passed"] is True, result["issues"]


def test_the_shape_of_an_archive_with_no_marker_at_all_still_validates(
    tmp_path: Path,
):
    """`thunder-viper`'s and `grand-vulture`'s shape: no marker, six castings,
    no ownership list.

    Written before the marker existed, so absence is the only signal there is,
    and it must read as "predates the field" rather than as "unknown, refuse".
    """
    castings = [
        _casting(cid, excerpt=CLEAN_EXCERPT, include_owns=False) for cid in range(1, 7)
    ]

    result = _run_validate(
        tmp_path,
        castings,
        spec_text=CLEAN_EXCERPT,
        state={"cycle": 0, "phase": "F6"},
        complete=True,
    )

    assert _ownership(result)["archive_schema_version"] == 0
    assert _ownership(result)["not_computable"] is True
    assert _span(result)["not_computable"] is True
    assert result["passed"] is True, result["issues"]


@pytest.mark.parametrize("run_name", ["daring-orca", "thunder-viper", "grand-vulture"])
def test_the_real_archives_in_this_repository_report_not_computable(
    tmp_path: Path, run_name: str
):
    """The same answer, driven against the ACTUAL manifest and state document.

    The two tests above rebuild the shape; this one takes the bytes. The
    documents are COPIED into a tmp_path run rather than judged in place,
    because `foundry_validate_castings` writes a pass marker and a cache into
    the run it judges and a sealed archive is not a place to write.

    Skipped where the archives are absent — they are untracked, so a fresh
    checkout has none, and a test that silently changed its verdict with the
    contents of an ignored directory would be worse than one that says it did
    not run.
    """
    source = Path(__file__).resolve().parents[4] / "foundry-archive" / run_name
    manifest_path = source / "castings" / "manifest.json"
    if not manifest_path.exists():
        pytest.skip(f"{run_name} is not present in this checkout (untracked)")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    state_path = source / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {}

    result = _run_validate(
        tmp_path,
        manifest.get("castings", []),
        manifest_extra={
            k: v for k, v in manifest.items() if k not in ("castings",)
        },
        state=state,
    )

    assert _ownership(result)["not_computable"] is True, _ownership(result)["issues"]
    assert _span(result)["not_computable"] is True
    assert result["requirement_span"]["rows"] == []
    # Whatever else this archive is told about, it is told nothing about a
    # field that did not exist when it was written.
    assert not any(
        i.get("dimension") in ("requirement_ownership", "requirement_span")
        for i in result["issues"]
    )


def test_owners_are_ordered_the_way_a_lead_reads_them(tmp_path: Path):
    """Casting ids are integers, and ordering them by their printed form puts
    #10 between #1 and #2 — in the F0.9 table AND in the F6 report that renders
    the same records.

    Driven at the only width where the two orderings differ, which is why this
    needs twelve castings and not three: with single digits the wrong ordering
    is indistinguishable from the right one.
    """
    castings = [
        _casting(cid, excerpt=CLEAN_EXCERPT, owns=["US-001", "FR-009"])
        for cid in range(1, 13)
    ]
    castings[0]["split_reason"] = {
        "US-001": "twelve surfaces, no shared owner",
        "FR-009": "twelve surfaces, no shared owner",
    }

    result = _run_validate(
        tmp_path, castings, spec_text=CLEAN_EXCERPT, state=CURRENT_RUN, complete=True
    )

    assert _row(result, "FR-009")["owners"] == list(range(1, 13))
    assert (
        "| FR-009 | #1, #2, #3, #4, #5, #6, #7, #8, #9, #10, #11, #12 | 12 |"
        in result["requirement_span"]["text"]
    )


def test_an_id_spelled_as_a_string_still_orders_and_does_not_raise(tmp_path: Path):
    """The ordering has to be TOTAL: a manifest is free to spell a casting id
    as a string, and a sort that compared one to an integer would raise out of
    F0.9 rather than render the table.
    """
    castings = [
        _casting(2, excerpt=CLEAN_EXCERPT, owns=["US-001", "FR-009"]),
        _casting("alpha", excerpt=CLEAN_EXCERPT, owns=["US-001", "FR-009"]),
        _casting(10, excerpt=CLEAN_EXCERPT, owns=["US-001", "FR-009"]),
    ]

    result = _run_validate(
        tmp_path, castings, spec_text=CLEAN_EXCERPT, state=CURRENT_RUN, complete=True
    )

    assert _row(result, "FR-009")["owners"] == [2, 10, "alpha"]


# ── The public span entry point: one table, two surfaces ──────────────────
#
# fallout AC-044 / CT-011 — the F0.9 gate refuses on this table and the F6
# report prints it, and they have to be the SAME table. A second assembly on
# the reporting side would be a second answer to "who owns this requirement",
# free to disagree with the answer a run was passed or refused on, which is
# exactly what "the span table appears in the F0.9 output AND in the F6 report"
# forbids. `requirement_span_table` is the one public entry point that makes
# that structural rather than a matter of two implementations agreeing.
#
# The tests below drive it the way the reporting surface does — with a run
# directory and nothing else in hand — and drive the gate over the SAME run,
# then compare. They also drive every shape the reporting surface can meet that
# the gate never does, because the report must render where the gate refuses.


def _span_table_for(project_root: Path, run_name: str = "ownership-test") -> dict:
    """`requirement_span_table` the way a renderer calls it: paths only."""
    fdir = project_root / ARCHIVE_DIR / run_name
    return requirement_span_table(str(project_root), fdir)


def test_the_entry_point_returns_the_gate_s_own_table(tmp_path: Path):
    """The property the whole entry point exists for: a renderer that never
    ran the gate still gets the gate's answer, key for key.

    Driven over a manifest with a real over-threshold span, so the comparison
    is over a table with something IN it — two empty tables matching proves
    nothing about who owns what.
    """
    result = _run_validate(
        tmp_path,
        _sharers(REQUIREMENT_SPAN_MAX + 1),
        spec_text=CLEAN_EXCERPT,
        state=CURRENT_RUN,
        complete=True,
    )
    gate_table = result["requirement_span"]
    assert gate_table["rows"], "the drive must produce a non-empty table"

    table = _span_table_for(tmp_path)

    assert table["problem"] is None
    assert {k: v for k, v in table.items() if k != "problem"} == gate_table
    # And the refusal the gate raised names the owners this table prints, so a
    # lead reading the report sees what the gate acted on.
    assert _row(result, "FR-009")["owners"] == next(
        r for r in table["rows"] if r["id"] == "FR-009"
    )["owners"]


def test_the_entry_point_finds_the_run_when_it_is_given_no_directory(
    tmp_path: Path,
):
    """`fdir=None` resolves the active run, for a caller that has only a root."""
    _run_validate(
        tmp_path,
        _sharers(REQUIREMENT_SPAN_MAX + 1),
        spec_text=CLEAN_EXCERPT,
        state=CURRENT_RUN,
        complete=True,
    )
    set_active_run("ownership-test")
    try:
        table = requirement_span_table(str(tmp_path))
    finally:
        clear_active_run()

    assert table["rows"] == _span_table_for(tmp_path)["rows"]


def test_an_absent_manifest_renders_an_empty_table_rather_than_refusing(
    tmp_path: Path,
):
    """The reporting surface must not refuse. A run halted before decompose
    legitimately has no manifest, and an empty table is what is true of it —
    NOT "not computable", which would blame a schema floor for an absent
    decomposition.
    """
    fdir = tmp_path / ARCHIVE_DIR / "no-manifest"
    fdir.mkdir(parents=True)

    table = requirement_span_table(str(tmp_path), fdir)

    assert table["rows"] == []
    assert table["not_computable"] is False
    assert table["threshold"] == REQUIREMENT_SPAN_MAX
    assert "no requirement ids" in table["text"].lower()


def test_an_unreadable_manifest_is_named_rather_than_read_as_empty(
    tmp_path: Path,
):
    """A corrupt manifest and an absent one are different claims, and a
    renderer that could not tell them apart would print "no requirements" over
    a file it simply failed to parse.
    """
    fdir = tmp_path / ARCHIVE_DIR / "corrupt"
    (fdir / "castings").mkdir(parents=True)
    (fdir / "castings" / "manifest.json").write_text('{"castings":', encoding="utf-8")

    table = requirement_span_table(str(tmp_path), fdir)

    assert table["problem"] is not None
    assert "manifest.json" in table["problem"]
    assert table["rows"] == []


@pytest.mark.parametrize(
    "castings",
    ["not a list", [1, 2], [None]],
    ids=["a-string", "a-list-of-ints", "a-list-of-nulls"],
)
def test_a_manifest_of_the_wrong_shape_is_named_rather_than_indexed(
    tmp_path: Path, castings
):
    """D-132's shapes, at this door. Each is valid JSON that parses cleanly and
    then meets `.get()` one rung in — an AttributeError across the MCP boundary
    from a surface whose whole contract is a named answer.

    Named rather than rendered empty, for the same reason the corrupt manifest
    above is: an empty table reads as "no requirements", which is a different
    and alarming claim about a file that is simply not a manifest.
    """
    fdir = tmp_path / ARCHIVE_DIR / "wrong-type"
    (fdir / "castings").mkdir(parents=True)
    (fdir / "castings" / "manifest.json").write_text(
        json.dumps({"castings": castings}), encoding="utf-8"
    )

    table = requirement_span_table(str(tmp_path), fdir)

    assert table["problem"] is not None
    assert "castings" in table["problem"]
    assert table["rows"] == []
    assert table["not_computable"] is False


def test_the_entry_point_reports_not_computable_on_a_legacy_archive(
    tmp_path: Path,
):
    """fallout FR-054 — an archive predating the persisted ownership field
    answers with the sentence the gate prints, not with an empty table, and the
    two surfaces spell it the same because they share one rule.
    """
    result = _run_validate(
        tmp_path,
        [_casting(1, excerpt=CLEAN_EXCERPT, include_owns=False)],
        spec_text=CLEAN_EXCERPT,
        state={"archive_schema_version": REQUIREMENT_IDS_SCHEMA_FLOOR - 1},
    )
    table = _span_table_for(tmp_path)

    assert table["not_computable"] is True
    assert result["requirement_span"]["not_computable"] is True
    assert table["text"] == result["requirement_span"]["text"]
    assert "not computable" in table["text"].lower()
