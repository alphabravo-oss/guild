"""Tests for plugins/webster/scripts/slop.py (spec US-008).

Every test drives the script through the ``run_script`` conftest helper
(GI-004, CT-007). Nothing here imports slop.py: its module-level ``TARGETS``
binds ``sys.argv`` at import time, so an import would freeze the wrong targets
before a test could choose any.

Everything below cites slop.py by symbol. Line numbers were tried here first and
did not survive one edit: the citation on the ``TARGETS`` sentence above named a
line that a later fix to slop.py turned into a blank one, and a docstring that
points at whitespace is worse than one that points at nothing, because it reads
as checked.

Which fix each test pins, the revision it is RED against, and what it did
before the fix (FR-039, AC-039 — the red-first property is recorded here rather
than enforced at runtime). The revision column is measured, not reasoned about.
Each cell is one run: copy the plugin to a scratch directory, put that
revision's slop.py in it, and run this module against it --

    cp -R plugins/webster "$scratch/webster"
    git show REV:plugins/webster/scripts/slop.py \
        > "$scratch/webster/scripts/slop.py"
    cd "$scratch/webster" && uvx pytest tests/test_slop.py -v

The revisions measured are every commit that has touched slop.py since the
pre-change script AC-039 names, in order: cfafe8e, af10189, ba33497, ecc7dd5,
ad38ed9, f33f7c2 — read back from ``git log --oneline --reverse cfafe8e..HEAD
-- plugins/webster/scripts/slop.py`` rather than remembered. The version of
this sentence that stopped at ad38ed9 named five and so left out the revision
of slop.py that actually ships: f33f7c2 rewrote the missing-target comment in
``main()`` in the same commit that first wrote this table, and a list written
inside a commit cannot name that commit. Adding it is therefore a separate
commit, and that commit touches no file under ``scripts/`` — which is what
keeps this list closed rather than one short again.

"red against REV" means this file fails when the suite runs against the slop.py
at REV and passes against the next revision in that list. "guard" means it
passed at all six and exists to stop a fix from taking something else away
with it. "added" is the commit the row's test first appeared in; no test in
this module has been amended since.

=======================================================  ============  =======
test                                                     red against   added
=======================================================  ============  =======
test_weak_verb_page_reports_medium_and_exits_zero        cfafe8e       af10189
test_supercharge_still_exits_one                         guard         af10189
test_human_co_author_trailer_exits_zero                  cfafe8e       af10189
test_ai_co_author_trailer_reports_agent_attribution      guard         af10189
test_text_marks_in_headings_are_not_emoji                cfafe8e       af10189
test_presentation_emoji_in_heading_is_reported           cfafe8e       af10189
test_missing_target_exits_two                            cfafe8e       af10189
test_dangling_symlink_page_exits_two                     af10189       ba33497
test_kept_supplementary_range_reports_a_non_emoji_block  guard         ba33497
test_pruned_directories_are_neither_checked_nor_counted  guard         ad38ed9
=======================================================  ============  =======

The one row naming a revision later than cfafe8e, ``test_dangling_symlink_page_exits_two``,
is red at cfafe8e as well. The four rows filed "guard" are the four that pass
at cfafe8e, and they pass at all six; no row in this module passes at cfafe8e
and fails later. Run across the six the module goes 6 red at cfafe8e, 1 red at
af10189, and 10 green at each of ba33497, ecc7dd5, ad38ed9 and f33f7c2. The
last of those is green for a reason worth writing down rather than inferring:
f33f7c2 changed slop.py's missing-target comment and nothing else, so there was
no behaviour there for a row to move on, and re-running it is how that is known
instead of assumed. The measurement was run because the same table in
``test_scaffold.py`` carried a row labelled "green before and after by design"
that is red against five of the seven revisions measured there. Every row below
survived it unchanged.

- ``test_weak_verb_page_reports_medium_and_exits_zero`` — FR-024 / OT-030.
  RED on the pre-change script: robust, leverage, elevate and empower were
  alternatives inside the ``marketing-buzzword`` entry of ``RULES``, whose
  ``high`` severity failed the gate on them, so the page exited 1.
- ``test_supercharge_still_exits_one`` — FR-024 / OT-030, the other half. Green
  before and after by design: it guards the rest of the high list against being
  demoted along with the four verbs.
- ``test_human_co_author_trailer_exits_zero`` — FR-025 / OT-031. RED before:
  the ``agent-attribution`` entry of ``RULES`` carried a bare
  ``Co-Authored-By:`` alternative that fired on any trailer, human or not.
- ``test_ai_co_author_trailer_reports_agent_attribution`` — FR-025 / OT-031,
  the other half. Guards against fixing the false positive by deleting the rule.
- ``test_text_marks_in_headings_are_not_emoji`` — FR-026 / OT-032. RED before:
  the ``emoji-heading`` and ``emoji-bullet`` entries of ``RULES`` each spelled
  the class ``☀-➿`` (U+2600-U+27BF) inline, and it swallowed ✓ U+2713 and
  ➜ U+279C, which emoji-data.txt 16.0 does not list at all, and ✔ U+2714,
  which it does list, as text-presentation. None of the three renders as a
  colour emoji, which is the tell the rule is for; only two of the three are
  outside the emoji data entirely, and the sentence that said none of them
  was an emoji was wrong about ✔. Both rules now share the ``EMOJI`` constant.
- ``test_presentation_emoji_in_heading_is_reported`` — FR-026 / OT-032, the
  other half. ⭐ U+2B50 was outside the old class entirely and went unreported.
- ``test_missing_target_exits_two`` — FR-027 / OT-033 / CT-006. RED before:
  ``files()`` walked a nonexistent path, found nothing, and ``main()`` printed
  "no slop found across 0 files" and returned 0 (survey finding FI-1).
- ``test_dangling_symlink_page_exits_two`` — FR-027 / CT-006. RED before: the
  missing-target guard asked "is it there", never "can it be read", so a
  ``*.md`` that is a dangling symlink cleared it, was walked like any other
  page, and raised ``FileNotFoundError`` at ``open()``. That traceback exits 1
  — the code this script reserves for high severity slop — against a file
  nothing ever read.

  A dangling symlink is the one unreadable target that reproduces for every
  user. ``chmod 000`` does not: root reads it anyway, so a permission-based
  test would pass silently for a root runner, which is the same shape of false
  pass this suite exists to catch. The fix also turns an unlistable *directory*
  (``chmod 000`` on the docs tree — previously "no slop found across 0 files"
  at exit 0) into exit 2, and that half is deliberately left untested for the
  same root reason rather than covered by a test that lies under root.
- ``test_kept_supplementary_range_reports_a_non_emoji_block`` — FR-026. Green
  before and after: it pins the half of FR-026 no other test reads, that
  ``\U0001F300-\U0001FAFF`` is kept whole. That range is exactly ten Unicode
  blocks end to end, and four of the ten carry no Emoji property at all in
  emoji-data.txt 16.0 — Ornamental Dingbats, Alchemical Symbols, Supplemental
  Arrows-C and Chess Symbols — so keeping the range is a decision the spec
  made, not a property lookup, and a later "cleanup" that carves those blocks
  back out fails here. Chess Symbols is the one this sentence left out while
  it named three; it is the only one of the four that does not sit in the same
  stretch as the others, which is what made it easy to leave out of a list
  written from memory.
- ``test_pruned_directories_are_neither_checked_nor_counted`` — FR-027. Green
  before and after by design: the pruning is the correct behaviour and this
  pins it unchanged. What was wrong was the sentence over it — ``files()`` said
  it returned "every markdown file under TARGETS" while the walk dropped
  dot-directories and node_modules with everything beneath them — and a
  docstring has no exit code to go red on. What this closes is the reason the
  sentence could stay wrong: no test reached into a pruned directory, and no
  test read the count in the "no slop found across N files" line, which is
  ``len()`` of that same list. Both halves are asserted here, so the next
  person to widen or narrow the prune has to come and say so.

No test uses ``@pytest.mark.skip`` or ``xfail`` (A-029).
"""

from __future__ import annotations

from pathlib import Path


def write_page(tmp_path: Path, name: str, body: str) -> Path:
    """Write one markdown page for slop.py to read.

    Bodies are chosen so that exactly the rule under test fires. A stray em
    dash or a three-word title-case heading would add a finding from an
    unrelated rule and turn an exit-code assertion into a puzzle.
    """
    page = tmp_path / name
    page.write_text(body, encoding="utf-8")
    return page


def outcome(result) -> str:
    """The message every assertion below appends (CT-007)."""
    return f"\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"


# -----------------------------------------------------------------------------
# FR-024 / OT-030 / AC-032: the four weak verbs surface at medium, and the
# rest of the marketing-buzzword list still fails the gate.
# -----------------------------------------------------------------------------
def test_weak_verb_page_reports_medium_and_exits_zero(run_script, tmp_path):
    page = write_page(
        tmp_path,
        "weak.md",
        "# Rollout notes\n"
        "\n"
        "The exporter is robust.\n"
        "Teams leverage the queue.\n"
        "Reviews elevate the draft.\n"
        "Editors empower reviewers.\n",
    )

    result = run_script("slop.py", page)

    assert result.returncode == 0, (
        f"Expected exit 0: robust/leverage/elevate/empower are weak-verb at "
        f"medium and medium never fails the gate; got {result.returncode}"
        + outcome(result)
    )
    assert "weak-verb" in result.stdout, (
        "Expected a weak-verb finding for all four verbs" + outcome(result)
    )
    assert "[medium]" in result.stdout, (
        "Expected the weak-verb finding at medium severity" + outcome(result)
    )
    assert "marketing-buzzword" not in result.stdout, (
        "The four verbs must have left marketing-buzzword, not been copied into "
        "a second rule alongside it" + outcome(result)
    )


def test_supercharge_still_exits_one(run_script, tmp_path):
    page = write_page(
        tmp_path,
        "buzz.md",
        "# Rollout notes\n\nThis release will supercharge the queue.\n",
    )

    result = run_script("slop.py", page)

    assert result.returncode == 1, (
        f"Expected exit 1: supercharge stays high in marketing-buzzword; "
        f"got {result.returncode}" + outcome(result)
    )
    assert "marketing-buzzword" in result.stdout, (
        "Expected the marketing-buzzword rule to still name supercharge"
        + outcome(result)
    )


# -----------------------------------------------------------------------------
# FR-025 / OT-031 / AC-033: Co-Authored-By fires on an AI name, not on a human.
# -----------------------------------------------------------------------------
def test_human_co_author_trailer_exits_zero(run_script, tmp_path):
    page = write_page(
        tmp_path,
        "human-trailer.md",
        "# Release notes\n\nCo-Authored-By: Jane <jane@example.com>\n",
    )

    result = run_script("slop.py", page)

    assert result.returncode == 0, (
        f"Expected exit 0: a human co-author trailer is not an agent byline; "
        f"got {result.returncode}" + outcome(result)
    )
    assert "agent-attribution" not in result.stdout, (
        "A human co-author must not be reported as an agent byline"
        + outcome(result)
    )


def test_ai_co_author_trailer_reports_agent_attribution(run_script, tmp_path):
    page = write_page(
        tmp_path,
        "ai-trailer.md",
        "# Release notes\n\nCo-Authored-By: Claude <noreply@anthropic.com>\n",
    )

    result = run_script("slop.py", page)

    assert result.returncode == 1, (
        f"Expected exit 1: the trailer names an agent; got {result.returncode}"
        + outcome(result)
    )
    assert "agent-attribution" in result.stdout, (
        "Expected the agent-attribution rule to fire on an AI co-author"
        + outcome(result)
    )


# -----------------------------------------------------------------------------
# FR-026 / OT-032 / AC-034: only Emoji_Presentation=Yes code points count.
# -----------------------------------------------------------------------------
def test_text_marks_in_headings_are_not_emoji(run_script, tmp_path):
    # ✓ U+2713 and ➜ U+279C are not in emoji-data.txt 16.0 at all; ✔ U+2714 and
    # ⬆ U+2B06 are in it, as text-presentation. The two pairs interleave in code
    # point order, so they are named rather than counted off the front and back
    # of the list: "the first two" and "the last two" split them the wrong way.
    page = write_page(
        tmp_path,
        "marks.md",
        "## Done ✓\n\n## Shipped ✔\n\n## Next ➜\n\n## Upgrade ⬆\n",
    )

    result = run_script("slop.py", page)

    assert result.returncode == 0, (
        f"Expected exit 0: ✓ ✔ ➜ ⬆ are check marks and "
        f"arrows, not emoji; got {result.returncode}" + outcome(result)
    )
    assert "emoji-heading" not in result.stdout, (
        "A bare check mark or arrow in a heading is not the markdown tell"
        + outcome(result)
    )


def test_presentation_emoji_in_heading_is_reported(run_script, tmp_path):
    # U+2705, U+2B50, U+1F680: all Emoji_Presentation=Yes. U+2B50 sat outside
    # the old U+2600-U+27BF class, so the star used to go unreported.
    page = write_page(
        tmp_path,
        "emoji.md",
        "## Done ✅\n\n## Star ⭐\n\n## Launch \U0001f680\n",
    )

    result = run_script("slop.py", page)

    assert result.returncode == 1, (
        f"Expected exit 1: ✅ ⭐ \U0001f680 render as colour emoji; "
        f"got {result.returncode}" + outcome(result)
    )
    assert "emoji-heading" in result.stdout, (
        "Expected emoji-heading on each of the three headings" + outcome(result)
    )
    assert result.stdout.count("emoji-heading") == 3, (
        "Expected all three headings reported. The old class already reached "
        "two of the three — ✅ U+2705 inside U+2600-U+27BF and \U0001f680 "
        "U+1F680 inside the kept supplementary range — and missed ⭐ U+2B50, "
        "which sits above both. A count of two here is that old behaviour. "
        "Only one of the three is in the supplementary plane, so the two are "
        "not the supplementary pair" + outcome(result)
    )


# -----------------------------------------------------------------------------
# FR-027 / OT-033 / AC-035 / CT-006: a target that is not there cannot pass.
# -----------------------------------------------------------------------------
def test_missing_target_exits_two(run_script, tmp_path):
    missing = tmp_path / "no" / "such" / "dir"

    result = run_script("slop.py", missing)

    assert result.returncode == 2, (
        f"Expected exit 2 (could not check) for a target that is neither a "
        f"file nor a directory; got {result.returncode}" + outcome(result)
    )
    assert f"no such target: {missing}" in result.stdout, (
        "Expected the missing target named on stdout" + outcome(result)
    )
    assert "no slop found" not in result.stdout, (
        "Walking nothing must not be reported as having checked something"
        + outcome(result)
    )


def test_dangling_symlink_page_exits_two(run_script, tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()
    # A readable page beside the broken one, so a pass here cannot come from
    # having walked an empty tree.
    write_page(docs, "real.md", "# Release notes\n\nThe queue drains in order.\n")
    broken = docs / "broken.md"
    broken.symlink_to("nowhere.md")

    result = run_script("slop.py", docs)

    assert result.returncode == 2, (
        f"Expected exit 2 (could not check) for a page that is there to os.walk "
        f"and gone to open(); got {result.returncode}" + outcome(result)
    )
    assert f"cannot read target: {broken}" in result.stdout, (
        "Expected the unreadable page named on stdout, the way a missing target "
        "is named" + outcome(result)
    )
    assert "Traceback" not in result.stderr, (
        "An unreadable page must be reported, not raised: the traceback exits 1, "
        "which this script reserves for high severity slop" + outcome(result)
    )
    assert "no slop found" not in result.stdout, (
        "A page that was never read must not be counted as checked"
        + outcome(result)
    )


# -----------------------------------------------------------------------------
# FR-026: the supplementary half of the class is kept whole, over-match included.
# -----------------------------------------------------------------------------
def test_kept_supplementary_range_reports_a_non_emoji_block(run_script, tmp_path):
    # U+1F700 ALCHEMICAL SYMBOL FOR QUINTESSENCE: inside \U0001F300-\U0001FAFF,
    # and Emoji=No in emoji-data.txt 16.0. Reported anyway, because FR-026 says
    # keep that range rather than test each code point's Emoji property.
    page = write_page(tmp_path, "alchemy.md", "## Quintessence \U0001f700\n")

    result = run_script("slop.py", page)

    assert result.returncode == 1, (
        f"Expected exit 1: FR-026 keeps \\U0001F300-\\U0001FAFF whole, so a code "
        f"point inside it is reported whether or not it carries the Emoji "
        f"property; got {result.returncode}" + outcome(result)
    )
    assert "emoji-heading" in result.stdout, (
        "Carving the non-emoji blocks out of the supplementary range is a change "
        "to FR-026, not a cleanup" + outcome(result)
    )


# -----------------------------------------------------------------------------
# FR-027: what files() returns is what the count line reports. Both directories
# pruned by the walk are planted with a page that would fail the gate, so a
# prune that stopped working is an exit 1 here and not a silent recount.
# -----------------------------------------------------------------------------
def test_pruned_directories_are_neither_checked_nor_counted(run_script, tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()
    write_page(docs, "ok.md", "# Release notes\n\nThe queue drains in order.\n")
    for pruned in ("node_modules", ".vitepress"):
        (docs / pruned).mkdir()
        # A heading that the emoji-heading rule reports at high severity, which
        # is exit 1. Reading either page changes the exit code, so this test
        # cannot pass by having walked an empty pruned directory.
        write_page(docs / pruned, "vendored.md", "# Shipped ✅\n")

    result = run_script("slop.py", docs)

    assert result.returncode == 0, (
        f"Expected exit 0: the only page under TARGETS that the walk keeps is "
        f"ok.md, and it is clean. A non-zero code here means a pruned directory "
        f"was read; got {result.returncode}" + outcome(result)
    )
    assert "no slop found across 1 files" in result.stdout, (
        "Expected the count to name the population files() actually returns. "
        "Three .md files are on disk and one of them is a candidate; a count "
        "that says 3 means the prune stopped, and a count that says 1 while the "
        "docstring promises every markdown file means the sentence is wrong"
        + outcome(result)
    )
