"""Tests for plugins/webster/scripts/scaffold.py (spec US-009).

Every one of the twelve tests drives the script through the ``run_script``
conftest helper (GI-004, CT-007). Eleven of them build a ``tmp_path`` docs
directory and point ``--docs`` at it, so ``init`` never writes into the
repository and ``check`` never reads it. The twelfth,
``test_help_names_an_exit_set_for_both_modes``, is the exception and names no
docs directory of any kind: it runs ``scaffold.py --help``, which argparse
answers out of the module docstring before either mode runs, so it has nothing
to write into or read from. Both counts are taken from the argument lists by
AST rather than from this paragraph, which is what an earlier version of it
spoke for twelve tests on the strength of.

Which fix each test pins, the revision it is RED against, and what it did
before the fix (FR-039, AC-039 — the red-first property is recorded here rather
than enforced at runtime). The revision column is measured, not reasoned about.
Each cell is one run: copy the plugin to a scratch directory, put that
revision's scaffold.py in it, and run this module against it --

    cp -R plugins/webster "$scratch/webster"
    git show REV:plugins/webster/scripts/scaffold.py \
        > "$scratch/webster/scripts/scaffold.py"
    cd "$scratch/webster" && uvx pytest tests/test_scaffold.py -v

The revisions measured are every commit that has touched scaffold.py since the
pre-change script AC-039 names, in order: cfafe8e, af10189, ecc7dd5, 3dadc71,
8387b46, 5fc0761, ad38ed9, f3e30a5 — read back from ``git log --oneline
--reverse cfafe8e..HEAD -- plugins/webster/scripts/scaffold.py`` rather than
remembered. This list stayed complete without anyone arranging it until
f3e30a5, because until then no commit that wrote the list had also touched
scaffold.py. f3e30a5 corrected a sentence in that script's module docstring, so
it had to be added by a later commit that touches no file under ``scripts/`` —
the rule the sibling table in ``test_slop.py`` states, which now binds here too.

"red against REV" means this file fails when the suite runs against the
scaffold.py at REV and passes against the next revision in that list. "guard"
means it passed at all eight and exists to stop a fix from taking something
else away with it. "added" is the commit the row's test first appeared in;
where further commits are named, the test's body was amended there.

==========================================================  ============  =========================
test                                                        red against   added
==========================================================  ============  =========================
test_bad_subject_key_writes_nothing_and_exits_two           cfafe8e       af10189
test_valid_subject_key_creates_the_landing_page             guard         af10189
test_unwritable_docs_path_exits_two_without_a_traceback     af10189       ecc7dd5
test_unreadable_page_exits_two_without_a_traceback          ecc7dd5       3dadc71, 8387b46, ad38ed9
test_malformed_category_json_stays_a_violation              guard         3dadc71, 8387b46
test_help_names_an_exit_set_for_both_modes                  5fc0761       3dadc71, ad38ed9
test_every_documented_envelope_leads_with_status            3dadc71       8387b46
test_unwritable_subject_label_writes_nothing_and_exits_two  8387b46       5fc0761
test_unwritable_title_writes_nothing_and_exits_two          8387b46       5fc0761
test_unlistable_directory_below_the_top_level_exits_two     8387b46       5fc0761, ad38ed9
test_unstattable_docs_directory_is_a_could_not_read         5fc0761       ad38ed9
test_a_refused_read_drops_the_violations_it_had_collected   8387b46       ad38ed9
==========================================================  ============  =========================

Every row naming a revision later than cfafe8e is red at cfafe8e as well, with
no exception in this module: the only two rows that pass at cfafe8e are the two
filed "guard", and those pass at all eight.

Two rows are red against a revision later than the commit that added them, and
they are not the same case. ``test_help_names_an_exit_set_for_both_modes`` was
added at 3dadc71 and amended at ad38ed9, and it is the amended text that is red
through 5fc0761 -- the second wording defect the row below describes.
``test_a_refused_read_drops_the_violations_it_had_collected`` was never
amended, and it is the correction this table exists for. It was recorded here
as "green before and after by design". Run against the eight revisions above it
is red against five of them, green only from 5fc0761 -- the commit that gave
``os.walk`` its ``onerror``, and the scaffold.py revision immediately before
ad38ed9, where the test was written. It was therefore already green on the day
it was added, which is the observation the old label was made from; what the
label went on to claim, that the test cannot go red, is answered by the five.

- ``test_bad_subject_key_writes_nothing_and_exits_two`` — FR-028 / OT-034 /
  CT-005. RED on the pre-change script: ``do_init`` called ``parse_subjects``
  only after ``stub`` had already put index.md, faq.md and the ``FRONT``
  sections on disk, and a bad key left ``parse_subjects`` through
  ``sys.exit("subject key ...")`` — stderr text, empty stdout, and exit 1, the
  same code ``check`` uses for a real layout violation. ``parse_subjects`` now
  raises ``BadSubject`` and ``do_init`` calls it above the first write.
- ``test_valid_subject_key_creates_the_landing_page`` — FR-028 / OT-035.
  Green before and after by design: hoisting the validation must not change
  what a good key produces.
- ``test_unwritable_docs_path_exits_two_without_a_traceback`` — FR-028 /
  CT-005. RED on the pre-change script: ``do_init`` called ``os.makedirs``
  unguarded, so a ``--docs`` naming a regular file raised ``FileExistsError``
  out of ``main()`` — a traceback on stderr, an empty stdout where the envelope
  belongs, and exit 1, which CT-005 reserves for a layout violation. It now
  reports ``cannot_write``. Its control is the test above, which runs the same
  argv with only ``--docs`` changed and still reaches exit 0 and
  clusters/clusters.md; the pair isolates the docs target as the one variable,
  which is why no third copy of the happy path was added here.
- ``test_help_names_an_exit_set_for_both_modes`` — FR-028 / CT-005. RED on the
  pre-change script: the module docstring is argparse's ``description``, so it
  *is* ``scaffold.py --help``, and it stated an exit contract for ``check``
  alone ("exit 1 on any violation") while ``init`` had grown two exit-2
  envelopes whose whole purpose is to be distinguishable from that 1. A caller
  reading exit codes had no published place to learn them. RED a second time,
  on the wording: the text called check's whole exit-2 set "when there was
  nothing to check", which is false of ``cannot_read`` twice over — something
  was there, and part of it had been read before the refusal — and said nothing
  about what becomes of the violations found by then. "nothing to check" is
  doctype.py's phrase, for a population that was genuinely empty.
- ``test_unreadable_page_exits_two_without_a_traceback`` — FR-028 / CT-005.
  RED on the pre-change script: ``do_check`` read every page through a bare
  ``open()``, so one dangling symlink under docs/ raised ``FileNotFoundError``
  out of ``main()`` — a traceback on stderr, nothing on stdout, and exit 1,
  which this script reserves for a real layout violation. It now reports
  ``cannot_read``. The same test runs ``check`` on the same tree before
  planting the symlink, so the exit 2 cannot come from a tree that was already
  failing.
- ``test_malformed_category_json_stays_a_violation`` — FR-028 / CT-005. Green
  before and after by design: the ``_category_.json`` handler was narrowed from
  ``except Exception`` to the three families a malformed file raises, so that
  an OSError reaches the ``cannot_read`` boundary instead of being filed as a
  violation. This pins the half that must not move.
- ``test_unwritable_subject_label_writes_nothing_and_exits_two`` — FR-028 /
  CT-005 / US-009. RED on the script as it stood: ``parse_subjects`` checked the
  key against ``SLUG`` and asked nothing of the label, so a label carrying a byte
  that is not valid UTF-8 — a lone surrogate by the time argv is decoded —
  passed validation and reached ``stub``, where ``f.write`` raised
  ``UnicodeEncodeError``. That is a ``ValueError``, not the ``OSError`` the write
  boundary catches, so init left through a traceback: exit 1, with index.md,
  faq.md, getting-started/, install/ and the subject directory on disk. Measured
  on the pre-change script, which is where those five names come from.
- ``test_unwritable_title_writes_nothing_and_exits_two`` — FR-028 / CT-005 /
  US-009. The same class through the other argument, and the reason both are
  here: the label is judged by ``parse_subjects`` and the title by a check of its
  own, so neither test stands for the other. RED the same way, one page in —
  index.md was open and truncated when the write raised.
- ``test_unlistable_directory_below_the_top_level_exits_two`` — FR-028 / CT-005.
  RED on the script as it stood, and in the direction that reads as a pass:
  ``do_check`` called ``os.walk`` without an ``onerror``, so a directory the
  filesystem refused to scan was dropped and the walk carried on. The directory
  held a page with no frontmatter under a filename that is not a slug, and check
  reported ``ok`` at exit 0. ``os.listdir`` already raised for docs/ and for the
  directories directly under it, which is why the fixture puts the refusal
  further down than either of those reaches.
- ``test_every_documented_envelope_leads_with_status`` — FR-028 / CT-005. RED on
  the script as it stood: the docstring claimed "every one of those is a JSON
  object on stdout whose first key is ``status``", and ``init``'s exit-0
  envelope led with ``created`` and had no ``status`` key at all, so the one
  assertion that reads the claim back — first key, then value, on each of the
  seven envelopes the docstring enumerates — raised ``KeyError: 'status'`` on
  the ordinary success path. The other tests here each check one envelope in
  depth and so could not see that the *set* was described wrongly. This is the
  test that reads the published contract back off the script; it provokes each
  envelope once, so ``cannot_read`` is reached here through the unreadable page
  and its unlistable-directory half is left to the test above, which is about
  that half and nothing else.
- ``test_unstattable_docs_directory_is_a_could_not_read`` — FR-028 / CT-005.
  RED on the script as it stood, and in the direction that reads as a tree
  nobody has written yet: ``do_check`` opened with ``os.path.isdir(docs)``,
  which calls ``os.stat`` and answers False for every error that call raises,
  so a docs directory that is there and holds a page but that the filesystem
  refuses to stat was reported ``no_docs`` — absent. ``init`` on the identical
  path in the identical state reported ``cannot_write``, so one script answered
  one question two ways, and the wrong answer is the one a caller reads as
  "nothing has been scaffolded here yet". The ``no_docs`` control in the same
  test is a path whose parent is a regular file: ENOTDIR, where there really is
  no directory at ``--docs``, and it must keep reporting ``no_docs``.
- ``test_a_refused_read_drops_the_violations_it_had_collected`` — FR-028 /
  CT-005. Red against 8387b46 and every scaffold.py revision before it, per the
  table above; it pins no fix of its own, and is the measurement the ``--help``
  sentence rests on. ``--help``
  used to file all of check's exit-2 statuses under "when there was nothing to
  check"; in the ``cannot_read`` case there was plenty to check and some of it
  had been checked — ``do_check`` accumulates violations through the root
  pages, the required sections, the landing pages and the ``_category_.json``
  files before the walk raises — and those violations are then dropped. This
  runs the identical tree twice, once readable and once with one unlistable
  directory added, so the violation the first run publishes is provably one the
  second run collected and did not; without the readable half, an absent
  findings list is equally explained by there having been nothing to find, which
  is the reading the old sentence invited. Dropping it is the behaviour the
  boundary was written for and stays. The test that was RED for this fix is
  ``test_help_names_an_exit_set_for_both_modes``, where the wording lives.

No test uses ``@pytest.mark.skip`` or ``xfail`` (A-029).
"""

from __future__ import annotations

import json
import os
from pathlib import Path


def outcome(result) -> str:
    """The message every assertion below appends (CT-007)."""
    return f"\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"


def unlistable_directory(under: Path) -> str:
    """Create a directory under ``under`` that a walk can see and cannot scan.

    Returns its path, which is the one the script has to name in the envelope.

    Not ``chmod 000``, for the reason the dangling symlink below is a symlink:
    root reads straight through a mode bit, so a permissions fixture passes by
    not running. What is refused for every user, root included, is a path longer
    than the platform's ``PATH_MAX``. Each level is created with
    ``os.mkdir(name, dir_fd=...)``, which hands the kernel one short component
    and an already-open directory rather than the whole path, so the tree can be
    built past a length nothing can afterwards open by name.

    The last level is the one the walk trips on and the levels above it are not:
    the loop stops adding while the next path still fits, so every ancestor can
    be scanned and only the final one cannot. Its parent still lists it — readdir
    returns the name and the type without resolving anything — so ``os.walk``
    files it as a directory and then fails to scan it, which is the shape of an
    unlistable directory below the top level. A page is planted inside so that
    what the swallowed error hid is a real violation and not an empty room.

    The same over-long path is refused by ``os.stat`` as well as by a scan,
    which is the second vantage this helper is called from: handed to ``check``
    as ``--docs`` itself, it is a directory that is there, that holds a page,
    and that the filesystem will not answer for — the case ``os.path.isdir``
    used to report as no directory at all.
    """
    limit = os.pathconf(str(under), "PC_PATH_MAX")
    name = "d" * os.pathconf(str(under), "PC_NAME_MAX")
    path = str(under)
    fd = os.open(path, os.O_RDONLY)
    try:
        while len(os.path.join(path, name)) < limit:
            os.mkdir(name, dir_fd=fd)
            deeper = os.open(name, os.O_RDONLY, dir_fd=fd)
            os.close(fd)
            fd, path = deeper, os.path.join(path, name)
        os.mkdir(name, dir_fd=fd)
        inside = os.open(name, os.O_RDONLY, dir_fd=fd)
        try:
            page = os.open("Not A Slug.md", os.O_WRONLY | os.O_CREAT, 0o644, dir_fd=inside)
            os.write(page, b"a page with no frontmatter, under a name that is not a slug\n")
            os.close(page)
        finally:
            os.close(inside)
    finally:
        os.close(fd)
    return os.path.join(path, name)


# argv is bytes, and a byte that is not valid UTF-8 reaches a Python process as
# a lone surrogate (PEP 383). ``os.fsdecode`` is how the two tests below produce
# one without writing a surrogate into this file, where saving it would ask an
# encoder for the same impossible thing the script is being tested about.
UNWRITABLE_LABEL = os.fsdecode(b"clusters:Clu\xffsters")
UNWRITABLE_TITLE = os.fsdecode(b"Do\xffcs")


# -----------------------------------------------------------------------------
# FR-028 / OT-034 / AC-036 / CT-005: validate before the first write.
# -----------------------------------------------------------------------------
def test_bad_subject_key_writes_nothing_and_exits_two(run_script, tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()

    result = run_script(
        "scaffold.py", "init", "--docs", docs, "--subject", "Bad Key:Label"
    )

    assert result.returncode == 2, (
        f"Expected exit 2 (could not run): a bad subject key is not a layout "
        f"violation, and exit 1 is reserved for those; got {result.returncode}"
        + outcome(result)
    )
    payload = json.loads(result.stdout)
    assert payload["status"] == "bad_subject", (
        "Expected a bad_subject JSON envelope on stdout, not a bare error line"
        + outcome(result)
    )
    assert payload["subject"] == "Bad Key", (
        "Expected the offending key named in the envelope" + outcome(result)
    )
    assert payload.get("error"), (
        "Expected the envelope to say what is wrong with the key"
        + outcome(result)
    )
    left_behind = sorted(p.name for p in docs.iterdir())
    assert left_behind == [], (
        f"Expected nothing written: a typo must not leave a half-built tree. "
        f"Found {left_behind}" + outcome(result)
    )


# -----------------------------------------------------------------------------
# FR-028 / OT-035 / AC-037: a good key still scaffolds its subject.
# -----------------------------------------------------------------------------
def test_valid_subject_key_creates_the_landing_page(run_script, tmp_path):
    docs = tmp_path / "docs"

    result = run_script(
        "scaffold.py", "init", "--docs", docs, "--subject", "clusters:Clusters"
    )

    assert result.returncode == 0, (
        f"Expected exit 0 for a well-formed subject key; got "
        f"{result.returncode}" + outcome(result)
    )
    landing = docs / "clusters" / "clusters.md"
    assert landing.is_file(), (
        f"Expected the subject landing page at {landing}" + outcome(result)
    )
    payload = json.loads(result.stdout)
    assert payload["subjects"] == ["clusters"], (
        "Expected the parsed subject reported back in the init envelope"
        + outcome(result)
    )
    assert "clusters/clusters.md" in payload["created"], (
        "Expected the landing page listed as created" + outcome(result)
    )


# -----------------------------------------------------------------------------
# FR-028 / CT-005: a write the filesystem refuses is a could-not-run, not a
# layout violation.
# -----------------------------------------------------------------------------
def test_unwritable_docs_path_exits_two_without_a_traceback(run_script, tmp_path):
    # A regular file where the docs directory should be. Chosen over `chmod 000`
    # because root writes through a mode bit and would turn this into a test that
    # passes by not running, which is the failure shape this suite exists to catch.
    docs = tmp_path / "docs"
    docs.write_text("a file, not a directory\n", encoding="utf-8")

    result = run_script(
        "scaffold.py", "init", "--docs", docs, "--subject", "clusters:Clusters"
    )

    assert result.returncode == 2, (
        f"Expected exit 2 (could not run): the filesystem refused the write, "
        f"which is not the layout violation exit 1 means; got "
        f"{result.returncode}" + outcome(result)
    )
    assert "Traceback" not in result.stderr, (
        "A refused write must be reported, not raised: the traceback exits 1 "
        "with an empty stdout, so a caller reading JSON sees no envelope at all"
        + outcome(result)
    )
    payload = json.loads(result.stdout)
    assert payload["status"] == "cannot_write", (
        "Expected a cannot_write JSON envelope on stdout" + outcome(result)
    )
    assert payload["path"] == str(docs), (
        f"Expected the refused path named in the envelope; got "
        f"{payload.get('path')!r}" + outcome(result)
    )
    assert payload.get("error"), (
        "Expected the envelope to carry what the filesystem said" + outcome(result)
    )
    assert payload["created"] == [], (
        f"Expected nothing created: the first makedirs failed, so no page was "
        f"written. Envelope claims {payload['created']}" + outcome(result)
    )
    assert docs.read_text(encoding="utf-8") == "a file, not a directory\n", (
        "The refused target must be left exactly as it was found" + outcome(result)
    )


# -----------------------------------------------------------------------------
# FR-028 / CT-005 / US-009: a byte no page can hold is found before the write and
# not by it. "init never exits 1" is a claim about every argument, and these are
# the two that become page text without passing through json.dumps on the way.
# -----------------------------------------------------------------------------
def test_unwritable_subject_label_writes_nothing_and_exits_two(run_script, tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()

    result = run_script(
        "scaffold.py", "init", "--docs", docs, "--subject", UNWRITABLE_LABEL
    )

    assert result.returncode == 2, (
        f"Expected exit 2 (could not run): a label that cannot be encoded is a "
        f"bad argument, and exit 1 is reserved for layout violations; got "
        f"{result.returncode}" + outcome(result)
    )
    assert "Traceback" not in result.stderr, (
        "An argument that cannot be written must be refused, not raised: the "
        "traceback exits 1 with an empty stdout, so a caller reading JSON sees "
        "no envelope at all" + outcome(result)
    )
    payload = json.loads(result.stdout)
    assert payload["status"] == "bad_subject", (
        "Expected the bad_subject envelope: the key parsed and the label did "
        "not, and both are what make a --subject item usable" + outcome(result)
    )
    assert payload["subject"] == "clusters", (
        f"Expected the subject whose label was refused to be named; got "
        f"{payload.get('subject')!r}" + outcome(result)
    )
    assert payload.get("error"), (
        "Expected the envelope to say what is wrong with the label"
        + outcome(result)
    )
    left_behind = sorted(p.name for p in docs.iterdir())
    assert left_behind == [], (
        f"Expected nothing written: the label is judged above the first write, "
        f"so a mistyped byte must not leave a half-built tree. Found "
        f"{left_behind}" + outcome(result)
    )


def test_unwritable_title_writes_nothing_and_exits_two(run_script, tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()

    result = run_script(
        "scaffold.py", "init", "--docs", docs, "--title", UNWRITABLE_TITLE
    )

    assert result.returncode == 2, (
        f"Expected exit 2 (could not run) for a --title no page can hold; got "
        f"{result.returncode}" + outcome(result)
    )
    assert "Traceback" not in result.stderr, (
        "A --title that cannot be written must be refused, not raised"
        + outcome(result)
    )
    payload = json.loads(result.stdout)
    assert payload["status"] == "cannot_write", (
        "Expected cannot_write: it is a page whose text cannot be written, "
        "which is the status this script already publishes for one"
        + outcome(result)
    )
    assert payload["path"] == str(docs / "index.md"), (
        f"Expected the page the title was headed for to be named; got "
        f"{payload.get('path')!r}" + outcome(result)
    )
    assert "--title" in payload.get("error", ""), (
        "Expected the envelope to name the argument at fault: two arguments "
        "reach this check and the caller has to know which one to retype"
        + outcome(result)
    )
    assert payload["created"] == [], (
        f"Expected nothing created: the answer is known before the first "
        f"makedirs. Envelope claims {payload['created']}" + outcome(result)
    )
    left_behind = sorted(p.name for p in docs.iterdir())
    assert left_behind == [], (
        f"Expected nothing written; found {left_behind}" + outcome(result)
    )


# -----------------------------------------------------------------------------
# FR-028 / CT-005: a directory the walk cannot scan is a could-not-check at any
# depth. os.listdir already raises for docs/ and for what sits directly under
# it; below that the walk is the only reader, and it used to drop the answer.
# -----------------------------------------------------------------------------
def test_unlistable_directory_below_the_top_level_exits_two(run_script, tmp_path):
    docs = tmp_path / "docs"
    built = run_script("scaffold.py", "init", "--docs", docs)
    assert built.returncode == 0, (
        f"The tree this test breaks has to be built first; init gave "
        f"{built.returncode}" + outcome(built)
    )

    # The control runs first and on the same tree, so the exit 2 below can only
    # come from the directory planted after it. It is also the false pass the
    # fix removes: run against the pre-change script, the check at the end
    # returns this same 0 with this same `ok` envelope.
    clean = run_script("scaffold.py", "check", "--docs", docs)
    assert clean.returncode == 0, (
        f"Expected the freshly scaffolded tree to pass its own check before the "
        f"unlistable directory is planted; got {clean.returncode}"
        + outcome(clean)
    )

    unlistable = unlistable_directory(docs / "install")

    result = run_script("scaffold.py", "check", "--docs", docs)

    assert result.returncode == 2, (
        f"Expected exit 2 (the check could not run to completion): a "
        f"directory os.walk names and cannot scan leaves every page inside it "
        f"unread, and reporting that as a sound tree is the false pass this "
        f"exit code exists to refuse; got "
        f"{result.returncode}" + outcome(result)
    )
    assert "Traceback" not in result.stderr, (
        "An unlistable directory must be reported, not raised" + outcome(result)
    )
    payload = json.loads(result.stdout)
    assert payload["status"] == "cannot_read", (
        "Expected a cannot_read JSON envelope on stdout" + outcome(result)
    )
    assert payload["path"] == unlistable, (
        f"Expected the directory that could not be scanned to be named, not the "
        f"docs root: it is the one a reader has to go and fix. Got "
        f"{payload.get('path')!r}" + outcome(result)
    )
    assert payload.get("error"), (
        "Expected the envelope to carry what the filesystem said" + outcome(result)
    )
    assert "violations" not in payload, (
        "A scan that stopped part way through has not earned a findings list: "
        "the page inside that directory was never read, and a list that omits "
        "it would be read as though it had been" + outcome(result)
    )


# -----------------------------------------------------------------------------
# FR-028 / CT-005: a docs directory that is there and cannot be stat'd is a
# could-not-read, and an argument with genuinely no directory at it is still a
# no_docs. Both halves run here, because the fix is that the two came apart.
# -----------------------------------------------------------------------------
def test_unstattable_docs_directory_is_a_could_not_read(run_script, tmp_path):
    docs = unlistable_directory(tmp_path)

    result = run_script("scaffold.py", "check", "--docs", docs)

    assert result.returncode == 2, (
        f"Expected exit 2; got {result.returncode}" + outcome(result)
    )
    payload = json.loads(result.stdout)
    assert payload["status"] == "cannot_read", (
        "The directory is there and holds a page; what failed is the stat. "
        "Reporting that as no_docs tells a caller to go and scaffold a tree "
        "that already exists, and it is the one refused-read case this script "
        f"still answered by pretending the path was empty. Got "
        f"{payload['status']!r}" + outcome(result)
    )
    assert payload["path"] == docs, (
        f"Expected the refused path named, the way the other cannot_read "
        f"envelopes name theirs. Got {payload.get('path')!r}" + outcome(result)
    )
    assert payload.get("error"), (
        "Expected the envelope to carry what the filesystem said" + outcome(result)
    )

    # init is the control, on the same path in the same state: it reaches
    # os.makedirs, which does not swallow, and has reported the refusal all
    # along. It is what made the check answer above wrong rather than merely
    # coarse — one script, one question, two answers.
    written = run_script("scaffold.py", "init", "--docs", docs)
    assert json.loads(written.stdout)["status"] == "cannot_write", (
        "init on this path reports the refusal; check has to agree that there "
        "is something there to be refused" + outcome(written)
    )

    # The no_docs half, which must not move: a path whose parent is a regular
    # file. ENOTDIR, and there really is no directory at --docs.
    not_a_directory = tmp_path / "regular-file"
    not_a_directory.write_text("a file, not a directory\n", encoding="utf-8")

    absent = run_script("scaffold.py", "check", "--docs", not_a_directory / "docs")

    assert absent.returncode == 2, (
        f"Expected exit 2; got {absent.returncode}" + outcome(absent)
    )
    assert json.loads(absent.stdout)["status"] == "no_docs", (
        "Narrowing no_docs must not empty it: nothing is at this path, which is "
        "what the status is for" + outcome(absent)
    )


# -----------------------------------------------------------------------------
# FR-028 / CT-005: the same tree twice, once readable and once not. The exit-2
# envelope is not a report on an empty tree — it is a report on a scan that
# started, found things, and stopped — and the things it found go with it.
# -----------------------------------------------------------------------------
def test_a_refused_read_drops_the_violations_it_had_collected(run_script, tmp_path):
    docs = tmp_path / "docs"
    built = run_script("scaffold.py", "init", "--docs", docs)
    assert built.returncode == 0, (
        f"The tree this test breaks has to be built first; init gave "
        f"{built.returncode}" + outcome(built)
    )
    # A violation from the first loop in do_check, so it is collected before the
    # walk that raises below and cannot be mistaken for one the refusal
    # prevented from being found.
    (docs / "index.md").unlink()

    readable = run_script("scaffold.py", "check", "--docs", docs)

    assert readable.returncode == 1, (
        f"Expected exit 1: the tree is missing a required root page; got "
        f"{readable.returncode}" + outcome(readable)
    )
    published = json.loads(readable.stdout)
    assert published["status"] == "violations", (
        f"Expected status violations; got {published['status']!r}"
        + outcome(readable)
    )
    dropped = [v for v in published["violations"] if v["where"].endswith("index.md")]
    assert dropped, (
        "This test needs one named violation that the unreadable twin below "
        "will collect and discard; the readable run is where it is shown to be "
        "real" + outcome(readable)
    )

    unlistable_directory(docs / "install")

    refused = run_script("scaffold.py", "check", "--docs", docs)

    assert refused.returncode == 2, (
        f"Expected exit 2 (the check could not run to completion); got "
        f"{refused.returncode}" + outcome(refused)
    )
    envelope = json.loads(refused.stdout)
    assert envelope["status"] == "cannot_read", (
        f"Expected a cannot_read envelope; got {envelope['status']!r}"
        + outcome(refused)
    )
    assert "violations" not in envelope, (
        "The missing root page was collected on this run too — it is found "
        "before the walk — and it is not published. That is the decision the "
        "boundary makes and the one --help now states: a findings list from a "
        "scan that stopped reads as the findings and is not them. Publishing "
        "it here is a different contract, not a better message" + outcome(refused)
    )
    assert dropped[0]["where"] not in refused.stdout, (
        "The dropped violation must not reappear under any other key: a caller "
        "that greps stdout for a path would otherwise read a partial answer as "
        "a whole one" + outcome(refused)
    )


# -----------------------------------------------------------------------------
# FR-028 / CT-005: the exit contract is published where a caller can read it.
# -----------------------------------------------------------------------------
def test_help_names_an_exit_set_for_both_modes(run_script):
    result = run_script("scaffold.py", "--help")

    assert result.returncode == 0, (
        f"Expected --help to exit 0; got {result.returncode}" + outcome(result)
    )
    help_text = result.stdout
    assert "  init " in help_text and "  check " in help_text, (
        "Expected --help to describe both modes; the module docstring is "
        "argparse's description, so this is where the contract is published"
        + outcome(result)
    )
    init_block = help_text.split("  init ", 1)[1].split("  check ", 1)[0]
    check_block = help_text.split("  check ", 1)[1]

    # init is the mode that grew the exit-2 envelopes, and it was the one the
    # docstring said nothing about. Naming exit 2 only under check would leave a
    # caller reading `bad_subject` with no published reason it is not a 1.
    assert "exit 2" in init_block, (
        "Expected --help to name exit 2 for init: a bad key and a refused write "
        "are could-not-run, and the whole point of the code is that it is not "
        "check's violation 1" + outcome(result)
    )
    for status in ("bad_subject", "cannot_write"):
        assert status in init_block, (
            f"Expected --help to name init's {status} status; an exit code with "
            f"no published status leaves a caller parsing prose" + outcome(result)
        )
    assert "exit 1" in check_block and "exit 2" in check_block, (
        "Expected --help to keep check's exit 1 and name its exit 2"
        + outcome(result)
    )
    for status in ("no_docs", "cannot_read"):
        assert status in check_block, (
            f"Expected --help to name check's {status} status" + outcome(result)
        )

    # The two exit-2 reasons are not the same reason, and the text used to give
    # them one name. cannot_read fires after do_check has collected violations
    # through the root pages, the sections, the landing pages and the
    # _category_.json files, and those are then dropped -- so "nothing to check"
    # was false twice over: something was there, and part of it had been read.
    assert "could not run to completion" in check_block, (
        "Expected --help to say what check's exit 2 is: a check that started "
        "and could not finish. 'nothing to check' is doctype.py's phrase for a "
        "population that was genuinely empty" + outcome(result)
    )
    assert "nothing to check" not in check_block, (
        "The old phrase must go rather than sit beside the new one; a caller "
        "reading either sentence has to reach the same answer. That is also why "
        "the text does not name doctype.py to contrast with it: --help is where "
        "this script states its own contract, and a reader holding one sentence "
        "that is true and one that is disowned has to work out which is which"
        + outcome(result)
    )
    assert "dropped" in check_block, (
        "Expected --help to state what happens to the violations collected "
        "before the refusal. A caller who is not told they are dropped reads "
        "the absent list as an absence of findings" + outcome(result)
    )


# -----------------------------------------------------------------------------
# FR-028 / CT-005: a page that cannot be read is a could-not-check, not a
# layout violation.
# -----------------------------------------------------------------------------
def test_unreadable_page_exits_two_without_a_traceback(run_script, tmp_path):
    docs = tmp_path / "docs"
    built = run_script("scaffold.py", "init", "--docs", docs)
    assert built.returncode == 0, (
        f"The tree this test breaks has to be built first; init gave "
        f"{built.returncode}" + outcome(built)
    )

    # The control runs first and on the same tree, so the exit 2 below can only
    # come from the symlink. A separate happy-path test would not prove that:
    # scaffold check reports on a whole tree, and one already-failing page in it
    # would produce the same non-zero code for an unrelated reason.
    clean = run_script("scaffold.py", "check", "--docs", docs)
    assert clean.returncode == 0, (
        f"Expected the freshly scaffolded tree to pass its own check before the "
        f"symlink is planted; got {clean.returncode}" + outcome(clean)
    )

    # A dangling symlink, not `chmod 000`: root reads straight through a mode
    # bit, so a permissions fixture passes by not running, which is the failure
    # shape this suite exists to catch. A broken link is refused for everyone.
    broken = docs / "install" / "broken.md"
    broken.symlink_to("nowhere.md")

    result = run_script("scaffold.py", "check", "--docs", docs)

    assert result.returncode == 2, (
        f"Expected exit 2 (the check could not run to completion): a page "
        f"os.listdir names and open() refuses is not a layout violation, and "
        f"exit 1 is reserved for those; got {result.returncode}" + outcome(result)
    )
    assert "Traceback" not in result.stderr, (
        "An unreadable page must be reported, not raised: the traceback exits 1 "
        "with an empty stdout, so a caller reading JSON sees no envelope at all"
        + outcome(result)
    )
    payload = json.loads(result.stdout)
    assert payload["status"] == "cannot_read", (
        "Expected a cannot_read JSON envelope on stdout" + outcome(result)
    )
    assert payload["path"] == str(broken), (
        f"Expected the unreadable page named in the envelope; got "
        f"{payload.get('path')!r}" + outcome(result)
    )
    assert payload.get("error"), (
        "Expected the envelope to carry what the filesystem said" + outcome(result)
    )
    assert "violations" not in payload, (
        "A scan that stopped part way through has not earned a findings list: "
        "reported as `violations` it is a partial answer read as a whole one"
        + outcome(result)
    )


# -----------------------------------------------------------------------------
# FR-028 / CT-005: narrowing the _category_.json handler must not reclassify
# the malformed file it was written for.
# -----------------------------------------------------------------------------
def test_malformed_category_json_stays_a_violation(run_script, tmp_path):
    docs = tmp_path / "docs"
    built = run_script("scaffold.py", "init", "--docs", docs)
    assert built.returncode == 0, (
        f"The tree this test breaks has to be built first; init gave "
        f"{built.returncode}" + outcome(built)
    )
    category = docs / "install" / "_category_.json"

    # One shape per family the narrowed handler still catches: bytes that are
    # not JSON (ValueError), a top-level array, which has no .get
    # (AttributeError), and a position that cannot be a dict key (TypeError).
    # `except Exception` caught all three and OSError with them; only the OSError
    # was meant to move.
    for body in ('not json at all\n', '[1, 2]\n', '{"position": [1]}\n'):
        category.write_text(body, encoding="utf-8")

        result = run_script("scaffold.py", "check", "--docs", docs)

        assert result.returncode == 1, (
            f"Expected exit 1 for _category_.json holding {body!r}: Docusaurus "
            f"cannot order a sidebar from it, which is a layout violation, not a "
            f"file that could not be read; got {result.returncode}"
            + outcome(result)
        )
        assert "Traceback" not in result.stderr, (
            f"_category_.json holding {body!r} must be reported, not raised"
            + outcome(result)
        )
        payload = json.loads(result.stdout)
        assert payload["status"] == "violations", (
            f"Expected a violations envelope for {body!r}; got "
            f"{payload.get('status')!r}" + outcome(result)
        )
        problems = [v["problem"] for v in payload["violations"]]
        assert "unreadable _category_.json" in problems, (
            f"Expected the malformed file named as the violation for {body!r}; "
            f"got {problems}" + outcome(result)
        )


# -----------------------------------------------------------------------------
# FR-028 / CT-005: the docstring enumerates seven envelopes and claims one shape
# for all of them. This is the assertion that reads that claim back.
# -----------------------------------------------------------------------------
def test_every_documented_envelope_leads_with_status(run_script, tmp_path):
    docs = tmp_path / "docs"
    not_a_directory = tmp_path / "regular-file"
    not_a_directory.write_text("a file, not a directory\n", encoding="utf-8")

    built = run_script("scaffold.py", "init", "--docs", docs)
    clean = run_script("scaffold.py", "check", "--docs", docs)

    # cannot_read is provoked before the tree is broken and the link is pulled
    # straight back out, so the violations run below reports the missing root
    # page rather than a page the boundary refused to open.
    broken = docs / "install" / "broken.md"
    broken.symlink_to("nowhere.md")
    unreadable = run_script("scaffold.py", "check", "--docs", docs)
    broken.unlink()

    (docs / "index.md").unlink()
    violations = run_script("scaffold.py", "check", "--docs", docs)

    documented = [
        ("init", 0, "ok", built),
        ("init", 2, "bad_subject",
         run_script("scaffold.py", "init", "--docs", tmp_path / "typo",
                    "--subject", "Bad Key:Label")),
        ("init", 2, "cannot_write",
         run_script("scaffold.py", "init", "--docs", not_a_directory)),
        ("check", 0, "ok", clean),
        ("check", 1, "violations", violations),
        ("check", 2, "no_docs",
         run_script("scaffold.py", "check", "--docs", tmp_path / "nowhere")),
        ("check", 2, "cannot_read", unreadable),
    ]

    for mode, code, status, result in documented:
        assert result.returncode == code, (
            f"The docstring puts {mode}'s {status} envelope at exit {code}; got "
            f"{result.returncode}" + outcome(result)
        )
        payload = json.loads(result.stdout)
        leading = next(iter(payload))
        # json.loads preserves document order, so the first key of the dict is
        # the first key a reader sees. The docstring promises it is `status` on
        # every one of these, which is what lets a caller read a single field to
        # tell a refused write from a bad key from a tree that is simply fine.
        assert leading == "status", (
            f"The docstring says every envelope leads with `status`; {mode}'s "
            f"{status} envelope leads with {leading!r}" + outcome(result)
        )
        assert payload["status"] == status, (
            f"Expected {mode} to report status {status!r} at exit {code}; got "
            f"{payload['status']!r}" + outcome(result)
        )
