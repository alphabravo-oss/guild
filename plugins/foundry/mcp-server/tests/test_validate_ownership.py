"""F0.9 VALIDATE: who owns a requirement, and how many owners it may have.

Requirements: ``forge-specs/foundry-run-fallout/spec.md``. The rows each test
proves are named, with the symbol each lands on, in casting 7's completion
report and in the ``# evidence-for:`` headers of
``evidence/casting-7-ownership-consistency.log``,
``evidence/casting-7-requirement-span.log`` and
``evidence/casting-7-span-table.log``. They are deliberately absent from the
prose here: ``tests/test_spec_id_convention.py`` demands that every three-digit
requirement id in a docstring or comment in this directory carry one of two
qualifications, ``process-fixes`` or ``convergence``, and a third spec is
installed now with no legal qualification of its own — so the only spellings
that would pass this directory's pin name a DIFFERENT spec's requirement.
Recorded in ``foundry-archive/foundry-run-fallout/concerns.md``.

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

from foundry_mcp.tools.foundry_state import (
    ARCHIVE_DIR,
    clear_active_run,
    set_active_run,
)
from foundry_mcp.tools.foundry_validate import (
    REQUIREMENT_IDS_SCHEMA_FLOOR,
    foundry_validate_castings,
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
) -> dict:
    """Write a minimal run and invoke the validator against it.

    ``spec_text`` defaults to empty so the dimensions this module is about are
    isolated from requirement coverage and the file-change-map cross-check.
    ``state`` writes state.json, which is where the archive schema marker lives
    — absent by default, which is what a run created before the marker existed
    looks like.

    ``complete=True`` writes the rest of what F0.5 emits — a prompt file per
    casting carrying the three blocks, a spec.md the excerpts are a verbatim
    copy of, and the stream-skip entry that tells F0.9 the intent matrix was
    not routed. It is what a test asserting the whole run VALIDATES needs; a
    test asserting one dimension does not, and paying for it everywhere would
    hide which dimension a failure came from.
    """
    run_name = "ownership-test"
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
