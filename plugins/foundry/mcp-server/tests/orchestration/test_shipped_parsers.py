"""The two shipped parsers nothing drove — `parsers/report.py`, `parsers/prove.py`.

fallout AC-014 / OT-015 (D-082) — THE ALLOWLIST IS EMPTY BECAUSE THE MODULES
ARE TESTED, NOT BECAUSE THEY WERE EXCUSED.

`test_every_shipped_module_is_imported_by_some_test_module` ended in
``assert sorted(unimported) == named``, so its PASSING state was a tree in which
the two modules named in `_SHIPPED_WITHOUT_A_TEST_MODULE` had no test module at
all. Both are REACHED over MCP — `tools/validation.py` imports
`extract_last_json` and `Validate-Report` runs through it; `tools/citation.py`
imports `Verdict` and `parse_prove_report`, so the parser that reads a
verification stream's own report was driven by nothing — and a grep of the whole
`tests/` tree for `parsers.prove` or `parsers.report` returned only the two
allowlist rows themselves. Each row named an honest reason ("in no casting's
key_files this run"), which is what made the guard hollow rather than lenient:
the assertion passed, so nothing downstream could tell the tree had the gap.

The remedy fallout AC-014 actually asks for is a test module, so this is one. It lives
under `tests/orchestration/` because that is where this casting's guard lives
and `test_every_shipped_module_is_imported_by_some_test_module` walks
`tests_dir.rglob("test_*.py")` — the whole tree, not one directory. The parsers
are in no casting's key_files, so nobody else was going to write it.

These are drives, not smoke: each asserts the behaviour the CALLER depends on,
because a test that merely imports a module satisfies the letter of the guard
and leaves the parser as undriven as it was.
"""
from __future__ import annotations

import json

from foundry_mcp.parsers.prove import (
    CodeReference,
    ProveVerdict,
    Verdict,
    count_verdicts,
    parse_prove_report,
)
from foundry_mcp.parsers.report import (
    JsonBlock,
    extract_json_blocks,
    extract_last_json,
)


# --------------------------------------------------------------------------- #
# parsers/report.py — the block `Validate-Report` validates.
# --------------------------------------------------------------------------- #


def test_the_last_block_is_the_one_validate_report_reads():
    """`tools/validation.py` takes `extract_last_json`, and the convention the
    docstring states is that the LAST fenced block is the structured output.

    A report is prose with examples in it, so the first block is routinely an
    illustration and the last is the payload. A parser that returned the first
    would hand the validator a schema-shaped example and report the real output
    unchecked.
    """
    text = (
        "# Report\n\n"
        "Here is the shape we expect:\n\n"
        "```json\n"
        '{"example": true}\n'
        "```\n\n"
        "And here is the run's own output:\n\n"
        "```json\n"
        '{"requirements": [{"id": "FR-1", "verdict": "VERIFIED"}]}\n'
        "```\n"
    )

    blocks = extract_json_blocks(text)
    assert len(blocks) == 2, blocks
    assert blocks[0].data == {"example": True}

    last = extract_last_json(text)
    assert last is not None
    assert last.data["requirements"][0]["id"] == "FR-1", last.data
    assert last is not blocks[0]
    assert last.data == blocks[-1].data


def test_a_document_with_no_json_block_answers_none_rather_than_raising():
    """The validator's degrade path. A report whose generator failed carries
    prose and no block, and a parser that raised there would turn a missing
    payload into an unhandled error at the MCP boundary."""
    assert extract_json_blocks("# Report\n\nNothing structured here.\n") == []
    assert extract_last_json("") is None


def test_a_malformed_block_is_skipped_and_the_good_ones_survive():
    """SKIPPED, not fatal, and that is the parser's own choice — `json.loads`
    failures `continue`. A half-written fence in a long report must not cost the
    payload three fences later, because the payload is what the validator is
    there to judge."""
    text = (
        "```json\n"
        "{not json at all,\n"
        "```\n\n"
        "```json\n"
        '{"ok": true}\n'
        "```\n"
    )
    blocks = extract_json_blocks(text)
    assert [b.data for b in blocks] == [{"ok": True}], blocks


def test_a_block_carries_the_line_span_a_reader_needs_to_find_it():
    """`JsonBlock` publishes `start_line` / `end_line` so a refusal can name
    WHERE in a long report the bad payload is. A span that did not move with the
    block would send the reader to the wrong fence."""
    text = "intro\n\n" + "```json\n" + '{"a": 1}\n' + "```\n"
    block = extract_last_json(text)
    assert isinstance(block, JsonBlock)
    assert block.start_line >= 3, block
    assert block.end_line >= block.start_line, block
    assert json.loads(block.raw) == {"a": 1}


# --------------------------------------------------------------------------- #
# parsers/prove.py — the parser for a verification stream's OWN report.
# --------------------------------------------------------------------------- #


def test_a_prove_report_parses_into_one_verdict_per_heading():
    """`tools/citation.py` takes `parse_prove_report`, so this is how a PROVE
    stream's findings reach the run at all. One heading, one verdict."""
    text = (
        "### VC-1: The login endpoint hashes with bcrypt\n"
        "**Verdict:** VERIFIED\n"
        "src/api/login.py:42 does the work.\n\n"
        "### VC-2: The form submits\n"
        "**Verdict:** HOLLOW\n"
        "The handler is empty.\n"
    )
    verdicts = parse_prove_report(text)

    assert [v.id for v in verdicts] == ["VC-1", "VC-2"], verdicts
    assert verdicts[0].verdict is Verdict.VERIFIED
    assert verdicts[1].verdict is Verdict.HOLLOW
    assert "bcrypt" in verdicts[0].description
    assert isinstance(verdicts[0], ProveVerdict)


def test_a_section_with_no_verdict_line_reads_unknown_and_not_verified():
    """The direction of the degrade is the whole safety property. A section the
    parser cannot read a verdict out of must NOT come back VERIFIED: that would
    turn an unparsable report into a passing requirement, which is the one
    reading a verification stream can never be allowed to produce."""
    verdicts = parse_prove_report(
        "### VC-9: Something nobody stated a verdict for\nprose only\n"
    )
    assert [v.verdict for v in verdicts] == [Verdict.UNKNOWN], verdicts
    assert Verdict.from_str("nonsense") is Verdict.UNKNOWN
    # ...and the spelled forms the report actually uses do resolve.
    assert Verdict.from_str("letter only") is Verdict.LETTER_ONLY
    assert Verdict.from_str(" verified ") is Verdict.VERIFIED


def test_a_report_with_no_headings_yields_nothing_rather_than_one_blob():
    """A PROVE report that failed to render its headings has no verdicts in it.
    Returning one catch-all verdict over the whole text would report a finding
    nobody wrote."""
    assert parse_prove_report("just some prose, no headings at all") == []


def test_code_references_are_extracted_and_urls_are_not_files():
    """The refs are what a lead follows to the finding. A URL captured as a file
    path sends them to a path that does not exist."""
    text = (
        "### VC-3: A thing\n"
        "**Verdict:** WRONG\n"
        "See src/api/login.py:42 and https://example.com/docs:80 for context.\n"
    )
    refs = parse_prove_report(text)[0].code_refs
    assert any(
        isinstance(r, CodeReference) and r.file == "src/api/login.py" and r.line == 42
        for r in refs
    ), refs
    assert not any(r.file.startswith("http") for r in refs), refs


def test_the_counts_are_keyed_by_the_verdict_vocabulary():
    """`count_verdicts` is what turns a parsed report into the numbers a stream
    records. Keyed by the enum's own values, so a caller grouping on them cannot
    disagree with the parser about what a verdict is called."""
    verdicts = parse_prove_report(
        "### VC-1: a\n**Verdict:** VERIFIED\n\n"
        "### VC-2: b\n**Verdict:** VERIFIED\n\n"
        "### VC-3: c\n**Verdict:** MISSING\n"
    )
    counts = count_verdicts(verdicts)
    assert counts == {"VERIFIED": 2, "MISSING": 1}, counts
    assert set(counts) <= {v.value for v in Verdict}, counts
    assert count_verdicts([]) == {}
