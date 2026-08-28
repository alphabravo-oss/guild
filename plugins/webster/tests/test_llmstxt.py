"""Tests for plugins/webster/scripts/llmstxt.py (spec US-007).

One of the test modules US-010 asks for, one per script.

Which spec fix each test pins, and whether it was RED against the pre-change
script (FR-039, AC-039). "Red" means the test fails when run against the
llmstxt.py at the commit before this one; "guard" means it passed there too and
exists to stop the fix from taking something else away with it.

=========================================================  =======  =======
test                                                        fix      state
=========================================================  =======  =======
test_html_comment_never_reaches_a_summary                   FR-023   red
test_html_comment_in_a_frontmatter_description_is_stripped  FR-023   red
test_unterminated_html_comment_publishes_nothing_after_it   FR-023   red
test_html_comment_never_reaches_the_header                  FR-023   red
test_multi_line_header_description_folds_to_one_line        FR-023   red
test_stub_pages_are_absent_from_llms_txt                    FR-023   red
test_first_prose_line_is_taken_without_an_h1                FR-023   red
test_header_falls_back_to_the_pyproject_project_table       FR-023   red
test_header_falls_back_to_the_poetry_table                  FR-023   red
test_package_json_still_wins_for_the_header                 FR-023   guard
test_frontmatter_description_still_wins                     FR-023   guard
test_unparseable_pyproject_does_not_break_the_header        FR-023   guard
test_tomllib_imported_at_module_level_with_the_floor_com…    NFR-002  red
=========================================================  =======  =======

Every test drives the script through the conftest ``run_script`` helper with
WEBSTER_ROOT and WEBSTER_DOCS in ``env``. Nothing here imports llmstxt.py: it
resolves ROOT, DOCS and BASE from ``os.environ`` at module import
(llmstxt.py:12-14), so an import would freeze the wrong docs directory before a
test could set one (GI-004).

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
