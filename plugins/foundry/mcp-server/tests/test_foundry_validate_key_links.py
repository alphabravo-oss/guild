"""Regression tests for Foundry-Validate-Castings dimension 4 key_links / artifacts
string type-guard (Casting C4 — FR-006 / CT-003 / NFR-001).

Background
----------
Dimension 4 of ``foundry_validate_castings`` reads each casting's
``must_haves.key_links[].from/.to`` and ``must_haves.artifacts[].path``. The
reads assumed every entry is a dict. When Decompose emitted a plain STRING
entry (a free-text link/artifact description), ``str.get(...)`` raised
``AttributeError: 'str' object has no attribute 'get'`` and the whole
10-dimension report failed to render at F0.9 with an uncaught traceback.

The fix guards each read with ``isinstance(...)``:
  - a dict entry behaves EXACTLY as before (from/to / path extracted), and
  - a string entry is accepted as a plain description and recorded as a
    NON-BLOCKING warning (severity "warning") that names the casting and the
    offending entry — never a traceback, never a hard block.

Each test below maps to one acceptance criterion (NFR-001: one regression test
per acceptance criterion).
"""

from __future__ import annotations

import json
from pathlib import Path

from foundry_mcp.tools.foundry_state import (
    ARCHIVE_DIR,
    clear_active_run,
    set_active_run,
)
from foundry_mcp.tools.foundry_validate import foundry_validate_castings


# ── Harness ───────────────────────────────────────────────────────────────
def _run_validate(project_root: Path, castings: list[dict], *, spec_text: str = "") -> dict:
    """Write a minimal run (manifest.json + spec.md) under an active run name
    and invoke ``foundry_validate_castings`` against it.

    ``spec_text`` defaults to empty (no requirement IDs, no File Change Map)
    so dimensions 1 and 10 stay clean and the tests isolate dimension 4.
    """
    run_name = "c4-key-links-test"
    fdir = project_root / ARCHIVE_DIR / run_name
    (fdir / "castings").mkdir(parents=True, exist_ok=True)
    (fdir / "castings" / "manifest.json").write_text(
        json.dumps({"castings": castings, "spec_type": "GREENFIELD"}),
        encoding="utf-8",
    )
    (fdir / "spec.md").write_text(spec_text, encoding="utf-8")

    set_active_run(run_name)
    try:
        return foundry_validate_castings(str(project_root))
    finally:
        clear_active_run()


def _casting(cid: str, *, key_links, artifacts=None, truths=None) -> dict:
    """Build a single casting dict with the given must_haves shapes."""
    return {
        "id": cid,
        "title": f"Casting {cid}",
        "spec_text": "",
        "observable_truths": truths
        or ["user sees X", "user sees Y", "user sees Z"],
        "key_files": [f"src/{cid}.ts"],
        "must_haves": {
            "truths": ["does the thing"],
            "artifacts": artifacts if artifacts is not None else [{"path": f"src/{cid}.ts"}],
            "key_links": key_links,
        },
    }


def _dim4(result: dict) -> dict:
    return result["dimensions"]["key_links_planned"]


# ── AC 1 (FR-006/CT-003): a string key_links entry no longer crashes ───────
def test_string_key_links_entry_does_not_raise_attribute_error(tmp_path: Path):
    """A STRING key_links entry (previously ``AttributeError: 'str' object has
    no attribute 'get'``) must produce a rendered report, not a traceback."""
    castings = [_casting("C1", key_links=["LoginForm -> /api/login"])]

    # Must not raise — the whole point of the guard.
    result = _run_validate(tmp_path, castings)

    assert isinstance(result, dict)
    assert "dimensions" in result
    assert "key_links_planned" in result["dimensions"]


# ── AC 2 (FR-006/CT-003): non-blocking warning names casting + entry ───────
def test_string_key_links_entry_emits_warning_naming_casting_and_entry(tmp_path: Path):
    """The string entry is surfaced as a severity=='warning' issue that names
    both the casting and the offending entry text."""
    entry = "LoginForm -> /api/login"
    castings = [_casting("C1", key_links=[entry])]

    result = _run_validate(tmp_path, castings)
    dim4 = _dim4(result)

    warnings = [i for i in dim4["issues"] if i.get("severity") == "warning"]
    assert len(warnings) == 1, dim4["issues"]
    w = warnings[0]
    # Names the casting (both as a structured field and inline in the message)
    assert w["casting"] == "C1"
    assert "C1" in w["issue"]
    # Names the offending entry text
    assert entry in w["issue"]
    # Dimension records the warning count.
    assert dim4.get("warnings") == 1

    # A non-blocking, warning-severity summary is also surfaced at the top level.
    top = [
        i for i in result["issues"]
        if i.get("dimension") == "key_links_planned" and i.get("severity") == "warning"
    ]
    assert len(top) == 1


# ── AC 3 (FR-006/CT-003): non-blocking — never a hard block at F0.9 ────────
def test_string_key_links_entry_is_non_blocking(tmp_path: Path):
    """Swapping a valid dict link for a string link must NOT change ``passed``
    or the error count — the string entry adds only a warning, never an error."""
    dict_castings = [_casting("C1", key_links=[{"from": "src/a.ts", "to": "src/b.ts"}])]
    str_castings = [_casting("C1", key_links=["src/a.ts -> src/b.ts"])]

    dict_result = _run_validate(tmp_path, dict_castings)
    str_result = _run_validate(tmp_path, str_castings)

    # The string entry introduces zero new blocking errors.
    assert str_result["summary"]["error_count"] == dict_result["summary"]["error_count"]
    assert str_result["passed"] == dict_result["passed"]
    # And it is not recorded as a dimension-4 error.
    assert _dim4(str_result)["ok"] == _dim4(dict_result)["ok"]


# ── AC 4 (FR-006/CT-003): dict entries behave EXACTLY as before ────────────
def test_dict_key_links_entry_behaves_unchanged(tmp_path: Path):
    """A dict key_links entry produces no string-entry warning and keeps the
    dimension clean (from/to are read via the unchanged code path)."""
    castings = [_casting("C1", key_links=[{"from": "src/a.ts", "to": "src/b.ts"}])]

    result = _run_validate(tmp_path, castings)
    dim4 = _dim4(result)

    warnings = [i for i in dim4["issues"] if i.get("severity") == "warning"]
    assert warnings == []
    assert dim4.get("warnings", 0) == 0
    assert dim4["ok"] is True
    # No key_links warning bubbles to the top-level issues list either.
    top = [
        i for i in result["issues"]
        if i.get("dimension") == "key_links_planned" and i.get("severity") == "warning"
    ]
    assert top == []


# ── AC 5 (FR-006 sibling artifacts read at :227): string artifact guarded ──
def test_string_artifacts_entry_does_not_crash_and_warns(tmp_path: Path):
    """The sibling ``artifacts`` read is guarded the same way: a string
    artifacts entry is accepted as a description with a non-blocking warning
    instead of raising ``AttributeError``."""
    castings = [
        _casting(
            "C1",
            key_links=[{"from": "src/a.ts", "to": "src/b.ts"}],
            artifacts=["src/a.ts is the login form"],
        )
    ]

    result = _run_validate(tmp_path, castings)  # must not raise
    dim4 = _dim4(result)

    warnings = [i for i in dim4["issues"] if i.get("severity") == "warning"]
    assert len(warnings) == 1
    assert "artifacts" in warnings[0]["issue"]
    assert "src/a.ts is the login form" in warnings[0]["issue"]
    # Still non-blocking.
    assert dim4["ok"] is True


# ── NFR-001 guard: the module and its regression suite are importable ──────
def test_validate_module_importable():
    """Sanity guard so the regression suite fails loudly if the target symbol
    is renamed or the module stops importing."""
    assert callable(foundry_validate_castings)


# ── The same regressions, against the widened dimension set ───────────────
#
# The report grew two dimensions after these tests were written: one checking a
# casting's persisted ownership list against its own spec excerpt, one
# computing how many castings own each requirement. Neither existed when the
# string type-guard was filed, and the manifests these tests write carry no
# ownership list at all — which is exactly the archive shape those dimensions
# have to tolerate. So the guard's own regressions are the natural place to
# hold the line: a manifest with no ownership list must still render the WHOLE
# report, every dimension present, with the string entry still a non-blocking
# warning and nothing else changed.


def test_the_whole_report_renders_for_a_manifest_with_no_ownership_list(
    tmp_path: Path,
):
    """Every dimension answers, including the two that read a field this
    manifest does not have.

    A dimension that raised, or that quietly went missing from the payload on
    an archive predating its field, would take the whole F0.9 report down with
    it — which is the same failure mode the string type-guard was filed
    against, one dimension over.
    """
    castings = [_casting("C1", key_links=["LoginForm -> /api/login"])]

    result = _run_validate(tmp_path, castings)

    # This harness writes no prompt files and an empty spec, so dimensions 7
    # and 9 report on that and `passed` is False for reasons that predate this
    # module. What must hold is that EVERY dimension answered.
    for name in (
        "requirement_coverage",
        "casting_completeness",
        "dependency_correctness",
        "key_links_planned",
        "scope_sanity",
        "research_integration",
        "prompt_fidelity",
        "migration_coverage",
        "spec_structure",
        "file_change_map_coverage",
        "requirement_ownership",
        "requirement_span",
    ):
        assert name in result["dimensions"], sorted(result["dimensions"])
        assert "ok" in result["dimensions"][name]
        assert "issues" in result["dimensions"][name]


def test_a_manifest_with_no_ownership_list_reports_not_computable_not_failure(
    tmp_path: Path,
):
    """The two new dimensions must not block an archive written before their
    field existed, and must say why rather than passing in silence.

    Silence would be indistinguishable from "checked and clean", which is the
    reading that lets an un-migrated manifest look verified.
    """
    castings = [_casting("C1", key_links=[{"from": "src/a.ts", "to": "src/b.ts"}])]

    result = _run_validate(tmp_path, castings)

    for name in ("requirement_ownership", "requirement_span"):
        dimension = result["dimensions"][name]
        assert dimension["not_computable"] is True
        assert dimension["ok"] is True
        assert dimension["issues"], f"{name} passed with no explanation"
        assert all(i.get("severity") == "info" for i in dimension["issues"])
    # And neither adds a counted entry to the top-level list, so the string
    # entry's warning is still the only thing this manifest is told about.
    assert not any(
        i.get("dimension") in ("requirement_ownership", "requirement_span")
        for i in result["issues"]
    )


def test_the_string_entry_warning_is_unchanged_by_the_widened_report(
    tmp_path: Path,
):
    """The original regression, re-run whole: a string entry is still a
    non-blocking warning that names the casting and the entry, and the report
    still renders.

    Two dimensions were added to this payload since; a dimension that appended
    an error for an archive it cannot judge would flip `passed` and silently
    change what this guard proves.
    """
    entry = "LoginForm -> /api/login"
    string_result = _run_validate(tmp_path, [_casting("C1", key_links=[entry])])
    dim4 = _dim4(string_result)

    warnings = [i for i in dim4["issues"] if i.get("severity") == "warning"]
    assert len(warnings) == 1
    assert warnings[0]["casting"] == "C1"
    assert entry in warnings[0]["issue"]
    assert dim4["ok"] is True

    # Non-blocking, measured the way the original guard measures it: against
    # the same manifest with a dict entry. A new dimension that appended an
    # error for an archive it cannot judge would move this number for both runs
    # and the comparison would still hold — so the absolute count is checked
    # too, against the dimensions that were reporting before.
    dict_result = _run_validate(
        tmp_path, [_casting("C1", key_links=[{"from": "src/a.ts", "to": "src/b.ts"}])]
    )
    assert string_result["summary"]["error_count"] == dict_result["summary"]["error_count"]
    assert sorted(
        i["dimension"] for i in string_result["issues"] if i.get("severity") == "error"
    ) == ["prompt_fidelity", "spec_structure"]


def test_the_span_table_is_present_and_says_it_cannot_be_computed(tmp_path: Path):
    """The table ships on every call, so a reader can always ask for it without
    first checking whether this run is one that has one.
    """
    result = _run_validate(tmp_path, [_casting("C1", key_links=["a -> b"])])

    assert result["requirement_span"]["not_computable"] is True
    assert result["requirement_span"]["rows"] == []
    assert "not computable" in result["requirement_span"]["text"]


# ── fallout GI-004 (D-151 / concern C-072): the same rule, one rung up ─────
#
# The module above guards the ENTRIES of `must_haves.artifacts` and
# `must_haves.key_links`. Three reachable raises sat one rung ABOVE that, on
# the containers themselves, and casting 2 drove the first of them against the
# shipped validator: a casting whose `must_haves` is a LIST reached
# `must_haves.get("truths", [])` and raised `AttributeError: 'list' object has
# no attribute 'get'` across the MCP boundary. `observable_truths` as an
# integer raised `TypeError: 'int' object is not iterable` in dimension 1, and
# `must_haves.truths` as an integer raised `TypeError: object of type 'int' has
# no len()` in dimension 2.
#
# Each landed BEFORE the ownership and span dimensions, so F0.9 returned an
# unhandled-error banner and reported nothing at all — strictly worse than the
# named refusal every other dimension produces. A-000's sentence is unqualified:
# a reachable raise remains a blocking defect at full weight.
#
# The tests below drive each shape and assert two things every time: the call
# RETURNS, and the report NAMES the casting. Either alone would pass while the
# other failed — a bare isinstance guard returns and says nothing, and that is
# the failure mode the operator actually pays for.


def _dim2(result: dict) -> dict:
    return result["dimensions"]["casting_completeness"]


def _malformed(**bad) -> dict:
    """A casting that is well-formed except for the one shape under test."""
    casting = _casting("m", key_links=[{"from": "a", "to": "b"}])
    casting.update(bad)
    return casting


def test_a_must_haves_that_is_a_list_is_named_rather_than_raised(tmp_path: Path):
    """C-072's driven shape, verbatim: `"must_haves": ["a list, not a mapping"]`."""
    result = _run_validate(tmp_path, [_malformed(must_haves=["a list, not a mapping"])])

    rows = [i["issue"] for i in _dim2(result)["issues"]]
    assert any("must_haves is of type list, not a mapping" in r for r in rows), rows
    assert any(i.get("casting") == "m" for i in _dim2(result)["issues"])
    assert any("must_haves must be a mapping" in h for h in result["revision_hints"])


def test_a_must_haves_that_is_a_string_is_named_the_same_way(tmp_path: Path):
    """The other non-mapping JSON container reaches the same read."""
    result = _run_validate(tmp_path, [_malformed(must_haves="truths, artifacts")])

    rows = [i["issue"] for i in _dim2(result)["issues"]]
    assert any("must_haves is of type str, not a mapping" in r for r in rows), rows


def test_observable_truths_that_is_not_a_list_is_named_rather_than_raised(
    tmp_path: Path,
):
    """The raise one line above C-072's, in dimension 1 rather than dimension 2.

    This one is reached FIRST — `for truth in c.get("observable_truths", [])`
    runs in the coverage fold — so a fix that guarded only `must_haves` would
    have left the report unrenderable for a manifest carrying this instead.
    """
    result = _run_validate(tmp_path, [_malformed(observable_truths=3)])

    rows = [i["issue"] for i in _dim2(result)["issues"]]
    assert any("observable_truths is of type int, not a list" in r for r in rows), rows


def test_a_non_string_observable_truth_is_scanned_rather_than_raised(
    tmp_path: Path,
):
    """One rung further down, and it is the module's own established answer.

    Dimension 4 already records a non-dict artifacts ENTRY as a plain
    description via `str(...)` rather than refusing it. A non-string truth is
    read the same way, so the requirement-id scan and the user-facing scan both
    see text instead of raising on an integer.
    """
    result = _run_validate(tmp_path, [_malformed(observable_truths=[1, 2, 3])])

    assert isinstance(result, dict) and "dimensions" in result


def test_a_must_haves_key_that_is_not_a_list_is_named_rather_than_raised(
    tmp_path: Path,
):
    """The mapping is fine; the value under one of its keys is not."""
    result = _run_validate(
        tmp_path,
        [
            _malformed(
                must_haves={
                    "truths": 5,
                    "artifacts": [{"path": "src/m.ts"}],
                    "key_links": [{"from": "a", "to": "b"}],
                }
            )
        ],
    )

    rows = [i["issue"] for i in _dim2(result)["issues"]]
    assert any("must_haves.truths is of type int, not a list" in r for r in rows), rows
    # The shape row SUPERSEDES the emptiness row: reporting both would tell the
    # operator to fill a key whose real problem is that it is not a list.
    assert not any("must_haves.truths is empty" in r for r in rows), rows


def test_a_non_list_artifacts_container_does_not_reach_the_entry_guard(
    tmp_path: Path,
):
    """Dimensions 4's entry loop is fed a list or nothing, never a string.

    `for art in "notalist"` iterates CHARACTERS, so an unguarded container
    turned one malformed value into one warning per character.
    """
    result = _run_validate(
        tmp_path,
        [_malformed(must_haves={"truths": ["t"], "artifacts": "notalist", "key_links": {"a": 1}})],
    )

    rows = [i["issue"] for i in _dim2(result)["issues"]]
    assert any("must_haves.artifacts is of type str, not a list" in r for r in rows), rows
    assert any("must_haves.key_links is of type dict, not a list" in r for r in rows), rows
    dim4_rows = [i.get("issue", "") for i in _dim4(result)["issues"]]
    assert not any("'n'" in r for r in dim4_rows), dim4_rows


def test_a_well_formed_casting_is_completely_unchanged_by_the_guards(
    tmp_path: Path,
):
    """The positive control. A guard that reported on every casting would pass
    every test above and make F0.9 unusable for every real run."""
    result = _run_validate(tmp_path, [_casting("ok", key_links=[{"from": "a", "to": "b"}])])

    rows = [i["issue"] for i in _dim2(result)["issues"]]
    assert not any("is of type" in r for r in rows), rows
    assert _dim2(result)["ok"] is True, _dim2(result)["issues"]


# ── The same class, one field over: key_files (fallout FR-009) ────────────
#
# This module's rule is that a wrong-typed cell is a WARNING and never a
# traceback, because a traceback at F0.9 costs the operator all ten dimensions
# to report one bad entry. `must_haves` was where the rule was learned;
# `key_files` is the other list the manifest shape guard admits without
# constraining what is in it — `_MANIFEST_DOCUMENT_SHAPE` spells its entries
# `[None]`, "list, contents unconstrained" — and dimension 10 handed each entry
# straight to `_normalize_file_path`, which calls `.strip()` on it. An integer
# there raised `AttributeError` out of the same door, past the same guard, for
# the same reason.


def test_a_non_string_key_files_entry_renders_the_whole_report(tmp_path: Path):
    """`key_files: [123]` is a manifest the shape guard accepts, so every
    dimension has to survive it.

    The spec carries a File Change Map so dimension 10 — the reader that
    normalises each entry — is ACTIVE. Without one the dimension short-circuits
    and the drive proves nothing.
    """
    castings = [
        {
            **_casting("C1", key_links=[{"from": "src/a.py", "to": "src/b.py"}]),
            "key_files": [123, None, {"path": "src/a.py"}],
        }
    ]

    result = _run_validate(
        tmp_path,
        castings,
        spec_text=(
            "## File Change Map\n"
            "\n"
            "| File | What changes |\n"
            "|---|---|\n"
            "| `src/a.py` | add |\n"
        ),
    )

    assert "dimensions" in result, result
    assert result["dimensions"]["file_change_map_coverage"]["active"] is True


def test_a_non_string_key_files_entry_claims_no_file(tmp_path: Path):
    """Skipped, not coerced. `str(123)` would enter the report as a path named
    `123` — a file overlap or a scope-creep warning against a path that does
    not exist, which is a finding invented out of a type error.
    """
    castings = [
        {**_casting("C1", key_links=[{"from": "src/a.py", "to": "src/b.py"}]),
         "key_files": [123]},
        {**_casting("C2", key_links=[{"from": "src/c.py", "to": "src/d.py"}]),
         "key_files": [123]},
    ]

    result = _run_validate(tmp_path, castings)

    dim3 = result["dimensions"]["dependency_correctness"]
    assert dim3["ok"] is True, dim3["issues"]
