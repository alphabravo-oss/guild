"""Tests for plugins/webster/scripts/llmstxt.py (spec US-007).

One of the test modules US-010 asks for, one per script.

Which spec fix each test pins, and the revision it is RED against (FR-039,
AC-039). Measured, not reasoned about. Each row is one run: copy the plugin to
a scratch directory, put that revision's llmstxt.py in it, and run this module
against it --

    cp -R plugins/webster "$scratch/webster"
    git show REV:plugins/webster/scripts/llmstxt.py \
        > "$scratch/webster/scripts/llmstxt.py"
    cd "$scratch/webster" && uvx pytest tests/test_llmstxt.py -v

This table is the record, and it is the only one. An earlier version of it
cited a committed evidence log for the run; the run's evidence is stripped from
the repository once consumed, so the citation named a file no reader can open
and the table was left asserting a measurement nobody could reach. The command
above is here instead, because a record whose backing nobody can re-run is a
record that reports a pass without checking -- which is what the two corrections
below are corrections of.

"red against REV" means this file fails when the suite runs against the
llmstxt.py at REV. "guard" means it passed at every revision measured and exists
to stop a fix from taking something else away with it. cfafe8e is the
pre-change script AC-039 names; a test added by a later fix names the commit it
was written against.

====================================================================  =======  =======================
test                                                                  fix      red against
====================================================================  =======  =======================
test_html_comment_never_reaches_a_summary                             FR-023   cfafe8e
test_html_comment_in_a_frontmatter_description_is_stripped            FR-023   a802345 (added 4ae7334)
test_unterminated_html_comment_publishes_nothing_after_it             FR-023   a802345 (added 4ae7334)
test_html_comment_never_reaches_the_header                            FR-023   4ae7334 (added b520ca9)
test_multi_line_header_description_folds_to_one_line                  FR-023   4ae7334 (added b520ca9)
test_a_stub_readme_is_not_the_header_summary                          FR-023   f31b144 (added cycle 3)
test_a_stub_marker_in_a_description_falls_through_to_the_next_source  FR-023   f31b144 (added cycle 3)
test_stub_pages_are_absent_from_llms_txt                              FR-023   cfafe8e
test_first_prose_line_is_taken_without_an_h1                          FR-023   cfafe8e
test_header_falls_back_to_the_pyproject_project_table                 FR-023   cfafe8e
test_header_falls_back_to_the_poetry_table                            FR-023   cfafe8e
test_package_json_still_wins_for_the_header                           FR-023   guard
test_frontmatter_description_still_wins                               FR-023   guard
test_unparseable_pyproject_does_not_break_the_header                  FR-023   guard
test_tomllib_imported_at_module_level_with_the_floor_comment          NFR-002  cfafe8e
====================================================================  =======  =======================

Every row naming a later commit is red at cfafe8e as well, except these two,
which pass there. The distinction is the correction this table exists for: an
earlier version of it was written by reading the diffs rather than by running
anything, and recorded the first of them as red against the pre-change script.

- ``test_unterminated_html_comment_publishes_nothing_after_it`` passes at
  cfafe8e, and for the wrong reason: that summary scan waited for an ``# ``
  heading before it would take a line, and this page has none, so the page was
  published with a title and no summary at all. Every assertion held because
  nothing was published, not because the unclosed comment was handled. a802345
  dropped the H1 gate and opened the route; that is the commit it is red at.
- ``test_a_stub_marker_in_a_description_falls_through_to_the_next_source``
  passes at cfafe8e because cfafe8e read no pyproject.toml, so a marker sitting
  in one could not reach the header. a802345 opened that route too.

Every test drives the script through the conftest ``run_script`` helper with
WEBSTER_ROOT and WEBSTER_DOCS in ``env``. Nothing here imports llmstxt.py: its
module-level ``ROOT``, ``DOCS`` and ``BASE`` resolve from ``os.environ`` at
import time, so an import would freeze the wrong docs directory before a test
could set one (GI-004). Named rather than numbered, for the reason conftest.py's
docstring sets out at greater length: the line numbers this suite's first draft
cited into the scripts had every one of them moved by the time anybody read
them.

No test uses ``@pytest.mark.skip`` or ``xfail``.
"""

from __future__ import annotations

from pathlib import Path

# Read as text, never imported — see the module docstring.
LLMSTXT_SOURCE = Path(__file__).resolve().parent.parent / "scripts" / "llmstxt.py"

STUB_MARKER = "webster: not written yet"

# A local reader, not a fixture: it calls the one ``run_script`` helper rather
# than re-implementing subprocess, which is the thing GI-004 forbids copying.
def llmstxt(run_script, root, docs="docs"):
    result = run_script(
        "llmstxt.py", env={"WEBSTER_ROOT": str(root), "WEBSTER_DOCS": docs}
    )
    assert result.returncode == 0, (
        f"Expected llmstxt.py to exit 0 for root {root}; got {result.returncode}\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    return result.stdout


def docs_tree(tmp_path, name, pages, pyproject=None, package_json=None, readme=None):
    """A repo root with a docs tree. ``pages`` maps a relative path to its text."""
    root = tmp_path / name
    for rel, text in pages.items():
        page = root / "docs" / rel
        page.parent.mkdir(parents=True, exist_ok=True)
        page.write_text(text, encoding="utf-8")
    for filename, text in (
        ("pyproject.toml", pyproject),
        ("package.json", package_json),
        ("README.md", readme),
    ):
        if text is not None:
            root.joinpath(filename).write_text(text, encoding="utf-8")
    return root


# -----------------------------------------------------------------------------
# FR-023: an anchor comment is a note to the next writer, not published prose
# -----------------------------------------------------------------------------
def test_html_comment_never_reaches_a_summary(run_script, fixture_repo):
    """AC-028, OT-027: docs/items/create-item.md ends its first prose line with one."""
    source = (fixture_repo / "docs" / "items" / "create-item.md").read_text()
    assert "<!-- src/app/main.py:15 -->" in source, (
        "This test is about that planted anchor; the fixture page no longer carries it"
    )

    out = llmstxt(run_script, fixture_repo)

    assert "create-item.md" in out, f"the page itself is missing from:\n{out}"
    assert "<!--" not in out, f"a comment reached the published summary:\n{out}"
    assert "src/app/main.py:15" not in out, f"the anchor reached the reader:\n{out}"
    assert "This page shows you how to add an item to your store." in out, (
        f"the prose either side of the comment is the summary:\n{out}"
    )


def test_html_comment_in_a_frontmatter_description_is_stripped(run_script, tmp_path):
    """FR-023, OT-027: the description is published verbatim, so it is cleaned too.

    Stripping comments out of the body alone leaves the shorter route open: a
    frontmatter ``description`` wins over the body and reaches the reader
    untouched, which publishes OT-027's exact forbidden string.
    """
    root = docs_tree(
        tmp_path,
        "fmcomment",
        {
            "items/create-item.md": (
                "---\n"
                'title: "Create an item"\n'
                'description: "Add an item to your store. <!-- src/app/main.py:15 -->"\n'
                "---\n"
                "\n# Create an item\n"
                "\nBody prose nobody should see in the summary.\n"
            )
        },
        pyproject='[project]\nname = "fmapp"\n',
    )

    out = llmstxt(run_script, root)

    assert "<!--" not in out, f"a comment reached the published summary:\n{out}"
    assert "src/app/main.py:15" not in out, f"the anchor reached the reader:\n{out}"
    assert (
        "- [Create an item](docs/items/create-item.md): "
        "Add an item to your store." in out
    ), f"the prose the author wrote is still the summary:\n{out}"


def test_unterminated_html_comment_publishes_nothing_after_it(run_script, tmp_path):
    """FR-023, OT-027: a comment nobody closed is still a comment.

    ``<!--.*?-->`` needs the closing marker, so an unterminated anchor comment
    survived the strip and was picked as the summary — publishing the working
    note in full. It runs to the end of the text instead.
    """
    root = docs_tree(
        tmp_path,
        "unterminated",
        {
            "limits.md": (
                "---\n"
                'title: "Item limits"\n'
                "---\n"
                "\n<!-- src/app/main.py:15\n"
                "\nA working note nobody closed.\n"
            )
        },
        pyproject='[project]\nname = "untermapp"\n',
    )

    out = llmstxt(run_script, root)

    assert "<!--" not in out, f"an unclosed comment reached the reader:\n{out}"
    assert "src/app/main.py:15" not in out, f"the anchor reached the reader:\n{out}"
    assert "A working note nobody closed" not in out, (
        f"everything after an unclosed comment is inside it:\n{out}"
    )
    assert "- [Item limits](docs/limits.md)" in out, (
        f"the page is still listed, with a title and no summary:\n{out}"
    )


def test_html_comment_never_reaches_the_header(run_script, tmp_path):
    """FR-023, OT-027: the header is the one string published with no page behind it.

    The third route. The body was cleaned, then the frontmatter ``description``
    that wins over the body -- and the pyproject/package.json description that
    wins over both still went to the ``> `` line untouched, publishing OT-027's
    exact forbidden string on the second line of the file.
    """
    root = docs_tree(
        tmp_path,
        "hdrcomment",
        {"index.md": "# Guide\n\nHow to drive it.\n"},
        pyproject=(
            "[project]\n"
            'name = "hdrapp"\n'
            'description = "A small item store served over HTTP. '
            '<!-- src/app/main.py:15 -->"\n'
        ),
    )

    out = llmstxt(run_script, root)

    assert "<!--" not in out, f"a comment reached the published header:\n{out}"
    assert "src/app/main.py:15" not in out, f"the anchor reached the reader:\n{out}"
    assert out.startswith("# hdrapp\n"), f"the name is still the product's:\n{out}"
    assert "> A small item store served over HTTP." in out, (
        f"the prose either side of the comment is still the summary:\n{out}"
    )


def test_multi_line_header_description_folds_to_one_line(run_script, tmp_path):
    """FR-023, OT-027: a summary that breaks across lines has stopped being one.

    TOML lets a description span lines, so taking the comment out of one is not
    enough by itself: the lines that remain were printed under a single ``> ``
    and everything after the first left the blockquote, reaching the reader as
    top-level llms.txt content instead of as the product's summary.
    """
    root = docs_tree(
        tmp_path,
        "hdrmultiline",
        {"index.md": "# Guide\n\nHow to drive it.\n"},
        pyproject=(
            "[project]\n"
            'name = "multiapp"\n'
            'description = """\n'
            "A small item store.\n"
            "<!-- src/app/main.py:15 -->\n"
            "Served over HTTP.\n"
            '"""\n'
        ),
    )

    out = llmstxt(run_script, root)

    assert "<!--" not in out, f"a comment reached the published header:\n{out}"
    assert "src/app/main.py:15" not in out, f"the anchor reached the reader:\n{out}"
    assert "> A small item store. Served over HTTP." in out, (
        f"expected the whole description on the one blockquote line:\n{out}"
    )
    assert "\nServed over HTTP." not in out, (
        f"a line published outside the `> ` blockquote is not the summary:\n{out}"
    )


def test_a_stub_readme_is_not_the_header_summary(run_script, tmp_path):
    """AC-029, OT-028: the second route into this file, which the page loop never walked.

    The page loop reads every page for the marker, so a stub under docs/ is
    dropped. The README behind the header's ``> `` line is not under docs/, so a
    repo whose README is still the skeleton scaffold.py wrote published that
    skeleton's first ``{placeholder}`` as the product's summary -- sourced from
    the one page on disk whose whole content is that nobody has written it yet.
    """
    root = docs_tree(
        tmp_path,
        "stubreadme",
        {"index.md": "# Guide\n\nHow to drive it.\n"},
        pyproject='[project]\nname = "stubapp"\n',
        readme=(
            "---\n"
            'title: "Stubapp"\n'
            "doc_type: how-to\n"
            "---\n"
            "\n# Stubapp\n"
            f"\n<!-- {STUB_MARKER} -->\n"
            "\n## Overview\n"
            "\n{The one problem this solves, in a sentence.}\n"
        ),
    )

    out = llmstxt(run_script, root)

    assert STUB_MARKER not in out, f"the marker itself was published:\n{out}"
    assert "The one problem this solves" not in out, (
        f"a skeleton heading nobody filled in became the product's summary:\n{out}"
    )
    assert "{" not in out and "}" not in out, (
        f"a placeholder brace is a form, not prose:\n{out}"
    )
    assert out.startswith("# stubapp\n"), f"the name is still the product's:\n{out}"
    assert "\n> " not in out, (
        "With no describable source left the header carries no summary line at "
        f"all, which is the honest answer:\n{out}"
    )
    assert "- [Guide](docs/index.md): How to drive it." in out, (
        f"the written pages are still listed:\n{out}"
    )


def test_a_stub_marker_in_a_description_falls_through_to_the_next_source(
    run_script, tmp_path
):
    """OT-028: no published line carries the marker, whichever source held it.

    The README is a page and is read for the marker as a page. A description in
    pyproject.toml or package.json is a bare string with no page behind it, and
    a repo that pasted the marker into one published it on the second line of
    the file. The chain is a chain: an unusable source is dropped and the next
    one is used.
    """
    root = docs_tree(
        tmp_path,
        "markerdesc",
        {"index.md": "# Guide\n\nHow to drive it.\n"},
        pyproject=f'[project]\nname = "markerapp"\ndescription = "{STUB_MARKER}"\n',
        readme="# Markerapp\n\nA small item store served over HTTP.\n",
    )

    out = llmstxt(run_script, root)

    assert STUB_MARKER not in out, f"the marker reached a published line:\n{out}"
    assert "> A small item store served over HTTP." in out, (
        "The next source in the chain is the README, and dropping the unusable "
        f"one is what lets it be reached:\n{out}"
    )


def test_stub_pages_are_absent_from_llms_txt(run_script, fixture_repo):
    """AC-029, OT-028: ten of the fixture's thirteen pages are unwritten skeletons."""
    stubs = sorted(
        p.name
        for p in (fixture_repo / "docs").rglob("*.md")
        if STUB_MARKER in p.read_text(encoding="utf-8")
    )
    assert stubs, "This test is about stub pages; the fixture has none"

    out = llmstxt(run_script, fixture_repo)

    assert STUB_MARKER not in out, f"the marker itself was published:\n{out}"
    for stub in stubs:
        assert stub not in out, (
            f"{stub} carries the stub marker, so listing it tells a machine reader "
            f"the page says something:\n{out}"
        )
    assert "delete-item.md" in out, (
        f"the written pages are still listed:\n{out}"
    )


def test_first_prose_line_is_taken_without_an_h1(run_script, tmp_path):
    """AC-030, OT-029: a page whose title is in frontmatter has no `# ` line to wait for."""
    root = docs_tree(
        tmp_path,
        "noh1",
        {
            "limits.md": (
                "---\n"
                'title: "Item limits"\n'
                "doc_type: explanation\n"
                "---\n"
                "\n<!-- src/app/limits.py:4 -->\n"
                "\nEach store holds a fixed number of items, and new ones are refused past it.\n"
                "\n## Where to next\n"
            )
        },
        pyproject='[project]\nname = "noh1app"\n',
    )

    out = llmstxt(run_script, root)

    assert (
        "- [Item limits](docs/limits.md): Each store holds a fixed number of items, "
        "and new ones are refused past it." in out
    ), f"expected the frontmatter title and the first prose line:\n{out}"


def test_header_falls_back_to_the_pyproject_project_table(run_script, fixture_repo):
    """AC-031, OT-029: the fixture is a Python repo with no package.json."""
    assert not (fixture_repo / "package.json").exists(), (
        "This test is about the no-package.json path; the fixture grew one"
    )

    out = llmstxt(run_script, fixture_repo)

    assert out.startswith("# fixapp\n"), (
        "The directory name is the checkout's, not the product's; the first line "
        f"of an llms.txt is the name every machine reader takes:\n{out}"
    )
    assert "> A small item store served over HTTP." in out, (
        f"expected the [project] description:\n{out}"
    )


def test_header_falls_back_to_the_poetry_table(run_script, tmp_path):
    """FR-023: same precedence as survey.py — [project], then [tool.poetry]."""
    root = docs_tree(
        tmp_path,
        "poetrydocs",
        {"index.md": "# Guide\n\nHow to drive it.\n"},
        pyproject=(
            "[tool.poetry]\n"
            'name = "poetryapp"\n'
            'description = "Legacy Poetry layout."\n'
        ),
    )

    out = llmstxt(run_script, root)

    assert out.startswith("# poetryapp\n"), f"got:\n{out}"
    assert "> Legacy Poetry layout." in out, f"got:\n{out}"


def test_package_json_still_wins_for_the_header(run_script, tmp_path):
    """FR-023: a repo with a package.json is a Node project that also has tooling config."""
    root = docs_tree(
        tmp_path,
        "bothdocs",
        {"index.md": "# Guide\n\nHow to drive it.\n"},
        pyproject='[project]\nname = "pyapp"\ndescription = "From pyproject."\n',
        package_json='{"name": "nodeapp", "description": "From package.json."}',
    )

    out = llmstxt(run_script, root)

    assert out.startswith("# nodeapp\n"), f"got:\n{out}"
    assert "> From package.json." in out, f"got:\n{out}"
    assert "From pyproject." not in out, f"got:\n{out}"


def test_frontmatter_description_still_wins(run_script, tmp_path):
    """FR-023: the author wrote the description for this purpose, so the body never overrides it."""
    root = docs_tree(
        tmp_path,
        "described",
        {
            "reference.md": (
                "---\n"
                'title: "Reference"\n'
                'description: "Every flag, in one table."\n'
                "---\n"
                "\n# Reference\n"
                "\nA body line nobody should see in the summary.\n"
            )
        },
        pyproject='[project]\nname = "describedapp"\n',
    )

    out = llmstxt(run_script, root)

    assert "- [Reference](docs/reference.md): Every flag, in one table." in out, (
        f"got:\n{out}"
    )
    assert "A body line nobody should see" not in out, f"got:\n{out}"


def test_unparseable_pyproject_does_not_break_the_header(run_script, tmp_path):
    """FR-023: a pyproject.toml is a syntax error while somebody is editing it."""
    root = docs_tree(
        tmp_path,
        "brokentoml",
        {"index.md": "# Guide\n\nHow to drive it.\n"},
        pyproject='[project]\nname = \ndescription = "never read"\n',
        readme="# Readme title\n\nThe README's opening line.\n",
    )

    out = llmstxt(run_script, root)

    assert out.startswith("# brokentoml\n"), (
        f"expected the directory-name fallback, and no crash:\n{out}"
    )
    assert "> The README's opening line." in out, (
        f"the README fallback is still behind pyproject:\n{out}"
    )


# -----------------------------------------------------------------------------
# NFR-002 / OT-041: the Python floor is stated where the dependency is taken
# -----------------------------------------------------------------------------
def test_tomllib_imported_at_module_level_with_the_floor_comment():
    """NFR-002, OT-041: tomllib is 3.11+, and the floor is written down."""
    source = LLMSTXT_SOURCE.read_text(encoding="utf-8")
    head = source.split("ROOT =")[0]

    assert "import tomllib" in head, (
        "tomllib is imported at module level, not inside the function that needs it; "
        f"the header of {LLMSTXT_SOURCE} was:\n{head}"
    )
    assert "3.11" in head, (
        f"A version floor nobody wrote down is a floor nobody knows about:\n{head}"
    )
