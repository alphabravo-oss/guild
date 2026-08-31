"""Tests for ``scripts/prose.py``: the shape of the prose, as against its statistics.

Every case here is one that reached this suite by defeating something. The two
calibration pages are the pair that showed the Flesch-Kincaid ceiling in
``doctype.py`` pointing backwards, and the rest are shapes that passed every
check webster had while being unreadable: a sentence too long to hold open
hidden inside short ones, nine hundred words with nothing to break them, and an
outline with a sentence under each heading.

Two are regressions in ``prose.py`` itself, found by running it over real
documentation rather than over fixtures. A markdown table has no sentence
terminator, so a five-column table read as one sentence of 147 words until
tables stopped counting as prose; and excluding them then dropped the word
count on reference pages far enough that ``fragmented`` fired on every one of
them, until heading density started counting a table as the content it is.
"""

from pathlib import Path

import pytest

# Held to the same ceiling of 10 by ``doctype.py``, which rates the first at
# grade -0.2 and the second at 10.2. Kept as literals rather than generated,
# because the point of the pair is the exact prose.
IMPENETRABLE = (
    "It is set by it. The setting of it is done by the system. It is then used by it "
    "for the doing of the thing. The doing of it is not done by you. It is done for "
    "you. It can be seen by you. It is shown in it. The use of it is made by it. "
) * 10

CLEAR = (
    "Open the Deployments page and choose New deployment, then pick the cloud provider "
    "you already have a credential for. Pioneer sizes the control plane for you unless "
    "you change it. The Agents tab tells you when each machine has finished enrolling. "
) * 3

STUB_BODY = "\n<!-- webster: not written yet -->\n"


def write_page(docs: Path, name: str, frontmatter: str, body: str) -> Path:
    page = docs / name
    page.parent.mkdir(parents=True, exist_ok=True)
    page.write_text(f"---\n{frontmatter.strip()}\n---\n\n{body.lstrip()}", encoding="utf-8")
    return page


def page(docs: Path, body: str, audience: str = "user", name: str = "index.md") -> Path:
    return write_page(
        docs, name, f'title: "T"\ndoc_type: how-to\naudience: {audience}', f"# T\n\n{body}\n"
    )


def check(run_script, docs: Path, **env: str):
    """``prose.py check <docs>`` with the finding limit lifted, as test_doctype does."""
    environment = {"WEBSTER_SHOW_PER_RULE": "50"}
    environment.update(env)
    return run_script("prose.py", "check", docs, env=environment)


def outcome(result) -> str:
    return f"exit {result.returncode}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"


@pytest.fixture
def docs_dir(tmp_path: Path) -> Path:
    docs = tmp_path / "docs"
    docs.mkdir()
    return docs


# -----------------------------------------------------------------------------
# The pair that motivates the script.
# -----------------------------------------------------------------------------
def test_impenetrable_prose_is_caught_though_its_grade_is_negative(run_script, docs_dir):
    page(docs_dir, IMPENETRABLE)

    result = check(run_script, docs_dir)

    assert "passive-voice" in result.stdout, (
        f"The page that scores grade -0.2 and passes doctype.py has to be caught by "
        f"something, or nothing in webster measures readability at all.\n{outcome(result)}"
    )


def test_clear_prose_is_not_caught_for_naming_the_product(run_script, docs_dir):
    page(docs_dir, CLEAR)

    result = check(run_script, docs_dir)

    for rule in ("long-sentence", "dense-section", "passive-voice", "nominalisation"):
        assert rule not in result.stdout, (
            f"{rule} fired on the prose reader-lens asks for. doctype.py already charges "
            f"this page 10.2 for naming Deployments and credential; a second check that "
            f"punishes it too would push writing away from the product twice.\n"
            f"{outcome(result)}"
        )


# -----------------------------------------------------------------------------
# Shapes that passed every other check.
# -----------------------------------------------------------------------------
def test_one_long_sentence_among_short_ones_is_found(run_script, docs_dir):
    buried = "Open the page and " + "then pick the option and " * 20 + "finally save it."
    page(docs_dir, f"Open it. Save it. Done. {buried} Open it. Save it. Done.")

    result = check(run_script, docs_dir)

    assert "long-sentence" in result.stdout, (
        f"A mean absorbs one long sentence among short ones, which is why the reading "
        f"grade never saw this and why the measure here is the longest sentence and not "
        f"the average.\n{outcome(result)}"
    )
    assert result.returncode == 1, f"A sentence past the ceiling is a defect.\n{outcome(result)}"


def test_a_slab_with_no_headings_is_a_dense_section(run_script, docs_dir):
    page(docs_dir, "You open the page and you choose the thing and you save it. " * 130)

    result = check(run_script, docs_dir)

    assert "dense-section" in result.stdout, (
        f"Text with no heading to break it is a section that ran long, not a case needing "
        f"its own rule.\n{outcome(result)}"
    )


def test_a_heading_every_few_words_is_fragmented(run_script, docs_dir):
    page(docs_dir, "\n".join(f"## Step {i}\n\nDo the thing." for i in range(1, 41)))

    result = check(run_script, docs_dir)

    assert "fragmented" in result.stdout, (
        f"Forty headings over one sentence each is a list wearing an outline.\n"
        f"{outcome(result)}"
    )
    assert result.returncode == 0, (
        f"Fragmentation is a judgement about shape, not a defect: a glossary is legitimately "
        f"one heading per entry.\n{outcome(result)}"
    )


# -----------------------------------------------------------------------------
# Regressions found by running the script over real documentation.
# -----------------------------------------------------------------------------
def test_a_table_is_not_measured_as_a_sentence(run_script, docs_dir):
    rows = "\n".join(f"| Field{i} | string | `field_{i}` | omitted if empty |" for i in range(12))
    page(docs_dir, f"| Field | Type | JSON key | Notes |\n|---|---|---|---|\n{rows}\n")

    result = check(run_script, docs_dir)

    assert "long-sentence" not in result.stdout, (
        f"A table row carries words and no terminator, so a table read as prose is one "
        f"enormous sentence. Real reference pages reported 147-word sentences that were "
        f"tables.\n{outcome(result)}"
    )


def test_heading_density_counts_a_table_as_content(run_script, docs_dir):
    rows = "\n".join(
        f"| `field_{j}` | string | required when the deployment declares a distribution |"
        for j in range(6)
    )
    sections = "\n".join(
        f"## Field group {i}\n\n| Name | Type | Notes |\n|---|---|---|\n{rows}\n"
        for i in range(8)
    )
    page(docs_dir, sections)

    result = check(run_script, docs_dir)

    assert "fragmented" not in result.stdout, (
        f"Dropping tables from the prose measurement must not drop them from the content "
        f"count as well, or every reference page becomes fragmented the moment its tables "
        f"stop being sentences.\n{outcome(result)}"
    )


def test_a_fence_ends_the_paragraph_around_it(run_script, docs_dir):
    half = "You open the page and you choose the thing you want. " * 8
    page(docs_dir, f"{half}\n\n```bash\nrun it\n```\n\n{half}")

    result = check(run_script, docs_dir)

    assert "long-paragraph" not in result.stdout, (
        f"A code block ends the paragraph above it and starts a new one below. Dropping "
        f"fenced lines outright welds the two into one and reports a length neither "
        f"has.\n{outcome(result)}"
    )


def test_each_list_item_is_its_own_paragraph(run_script, docs_dir):
    items = "\n".join(f"- You open the page and choose option {i} from the menu." for i in range(20))
    page(docs_dir, items)

    result = check(run_script, docs_dir)

    assert "long-paragraph" not in result.stdout, (
        f"A reader lands at every bullet, so a twenty-item list is not the wall twenty "
        f"sentences of prose would be.\n{outcome(result)}"
    )


# -----------------------------------------------------------------------------
# The audience decides the threshold, as it decides the grade ceiling.
# -----------------------------------------------------------------------------
def test_the_sentence_ceiling_follows_the_declared_audience(run_script, docs_dir):
    forty = " ".join(["word"] * 38) + " ends here."

    page(docs_dir, forty, audience="user")
    as_user = check(run_script, docs_dir)
    page(docs_dir, forty, audience="developer")
    as_developer = check(run_script, docs_dir)

    assert "long-sentence" in as_user.stdout, (
        f"40 words is past the 35 a 'user' page is held to.\n{outcome(as_user)}"
    )
    assert "long-sentence" not in as_developer.stdout, (
        f"The same sentence is inside the 55 a 'developer' page is held to. A page for "
        f"someone with no development background and a page for whoever reads the source "
        f"cannot be held to the same sentence.\n{outcome(as_developer)}"
    )


# -----------------------------------------------------------------------------
# The exit contract the README publishes.
# -----------------------------------------------------------------------------
def test_advisories_alone_exit_zero(run_script, docs_dir):
    page(docs_dir, "\n".join(f"## Step {i}\n\nDo the thing." for i in range(1, 41)))

    result = check(run_script, docs_dir)

    assert result.returncode == 0, (
        f"Only fragmented fired, and an advisory is reported and judged rather than "
        f"failing a gate.\n{outcome(result)}"
    )


def test_a_missing_docs_directory_exits_two(run_script, tmp_path):
    result = check(run_script, tmp_path / "absent")

    assert result.returncode == 2, (
        f"Nothing to measure is not a clean page set. exit 2 is what the README files "
        f"under 'no docs directory at the resolved path'.\n{outcome(result)}"
    )


def test_a_tree_of_nothing_but_stubs_exits_two(run_script, docs_dir):
    write_page(docs_dir, "index.md", 'title: "T"\ndoc_type: how-to\naudience: user',
               f"# T\n{STUB_BODY}")

    result = check(run_script, docs_dir)

    assert result.returncode == 2, (
        f"An unwritten page has no prose to measure, and a set of them measured clean "
        f"would report a shape nobody has written yet.\n{outcome(result)}"
    )


def test_limits_publishes_every_audience(run_script):
    result = run_script("prose.py", "limits")

    assert result.returncode == 0, f"limits reads a table and cannot fail.\n{outcome(result)}"
    for audience in ("user", "operator", "developer"):
        assert audience in result.stdout, (
            f"limits omits '{audience}'. A threshold a caller cannot look up is one they "
            f"will guess at.\n{outcome(result)}"
        )
