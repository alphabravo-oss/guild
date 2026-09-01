"""Tests for the claims plugins/webster/README.md makes about this suite (spec US-010).

Six of this suite's eight modules take a webster script as their subject:
``test_doctype.py``, ``test_drift.py``, ``test_llmstxt.py``,
``test_scaffold.py``, ``test_slop.py`` and ``test_survey.py``. This module and
``test_harness.py`` are the other two, and neither has a script for a subject:
they test the plugin's own records — the README that counts the suite, and the
harness those six stand on. The sentence this replaces read "Every other module
in this directory tests a script", which counted ``test_harness.py`` — whose own
docstring opens "Tests for the harness itself" — among the modules that do, and
``test_harness.py`` carried the mirror image of the same miscount about this
file.

This one tests the README, because the README is the only place in the plugin
that states a number nothing verified. FR-038 asks the Status section to carry
the run command and the passing count; AC-042 asks ``plugin.json`` to read
0.18.0 and the README to name ``WEBSTER_SURVEY``, which is a claim on the file
and not on that section -- the variable is introduced in the paragraph beside
``WEBSTER_LENS_ALLOW``, above the ``## Status`` heading, so the test that pins
it reads the whole file; NFR-001 asks that ``cd plugins/webster && uvx pytest``
exit 0 *with the count recorded in README*. The sentence this replaces put
``WEBSTER_SURVEY`` in the Status section, which is where nothing checks for it
and where it is not, and the row below on that test already said so.
A hard-coded count with no test behind it is a claim that
reports a pass without having checked anything, which is the class of failure
this whole spec exists to remove — and it fails silently, in the direction that
looks fine: the first test anybody adds makes the sentence false while every
gate stays green.

Which claim each test pins, and the revision it is RED against (FR-039,
AC-039 — the red-first property is recorded here rather than enforced at
runtime). Measured, not reasoned about, and measured on a different axis from
the sibling modules'. What this file reads is four surfaces and 18 files: the
README, ``plugin.json``, the seven scripts' usage texts, and -- through
``_scan_module`` -- the suite's own nine modules, the eight ``tests/test_*.py``
and ``conftest.py``. Putting one old script back proves nothing about the rest,
so the recipe below moves the first three together. It does not move the
fourth. ``tests/`` stays at today's tree, which is why the count row's red
against 61fb1df is ``69 == 149`` and not that revision's own ``69 == 77``:
still a genuine red, and on a comparison one surface wider than the sentence
this replaces described, which named three surfaces and said all of them moved.
Each row is one run --

    cp -R plugins/webster "$scratch/webster"
    rm -rf "$scratch/webster/scripts"
    git archive REV plugins/webster/scripts \
        | tar -x --strip-components=2 -C "$scratch/webster"
    git show REV:plugins/webster/README.md > "$scratch/webster/README.md"
    git show REV:plugins/webster/.claude-plugin/plugin.json \
        > "$scratch/webster/.claude-plugin/plugin.json"
    cd "$scratch/webster" && uvx pytest tests/test_readme.py -v

Two revisions are recorded because the two answers differ and an earlier
version of this record named neither. cfafe8e is the pre-change state AC-039
names, the one every sibling module measures against; 61fb1df is the commit
before this module existed, which is what the header over these rows used to
say. Against cfafe8e all six of these are red. Against 61fb1df four are, and
the two that are not are the two that used to be labelled "green before and
after by design" — true of that axis, false of the other, and the header named
the axis loosely enough that the label read as a claim about both.

- ``test_status_passing_count_matches_the_collected_suite`` — FR-038 / AC-042 /
  NFR-001. RED against both. The cfafe8e Status section states no count at all,
  so the read raises before there is anything to compare; at 61fb1df it read
  ``69 passing``, written by hand at 9859317 when the suite defined exactly 69
  and never moved again — 77 by 61fb1df, 149 now. The revision is named because
  "by the time this row was first written" was not a moment anyone could
  re-measure to 77: this module arrived with three tests of its own, so the
  commit that first wrote this row defined 80, and 77 is 61fb1df, the state the
  stale 69 had fallen behind. That is exactly the drift this test makes
  impossible to commit.
- ``test_status_section_names_the_run_command_and_the_version`` — AC-042 /
  OT-040 / NFR-003. RED against cfafe8e, green against 61fb1df. The cfafe8e
  Status section carries neither half: no ``cd plugins/webster && uvx pytest``,
  because there was no suite to run, and "Version 0.2.0." against a manifest
  already shipping 0.10.0. Both had been written by 61fb1df, which is the whole
  of what the old "green before and after by design" was about.
- ``test_readme_documents_webster_survey_and_the_python_floor`` — AC-042 /
  FR-038 / NFR-002. RED against cfafe8e, green against 61fb1df, on the same
  axis and for the matching reason: the cfafe8e paragraph that introduces
  ``WEBSTER_LENS_ALLOW`` names no second variable, so ``WEBSTER_SURVEY`` — the
  transport GI-002 and CT-003 put in ``doctype.py`` — is absent from the file a
  reader would look it up in. What made it "the other half" of the row above is
  only that the two claims it pins live outside the Status section.
- ``test_the_script_table_names_drift_pys_own_status_vocabulary`` — FR-038 /
  AC-042. RED against both, and by a different route at each. At cfafe8e the
  read raises before any comparison: that ``drift.py``'s usage text has no
  sentence putting any status at exit 0, because the exit-0 vocabulary US-002
  adds did not exist to be put there. At 61fb1df the script had grown it and
  the row had not — the ``scripts/drift.py`` row named none of
  ``unrelated_changes``, ``no_docs``, ``no_manifest``, ``no_git`` or
  ``head_missing``, and ``unrelated_changes`` is the one that tells a reader
  ordinary development is not a finding. Same failure as the count above, one
  table row over: prose describing a script that moved under it.
- ``test_the_script_table_publishes_each_scripts_own_exit_set`` — FR-038 /
  AC-042 / OT-040. RED against both, and the assertion that fires first at
  either is the silence one: ``llmstxt.py`` names an exit code in neither its
  usage text nor its row, while its source calls ``sys.exit``. Behind that, and
  what this test was first written for, three rows published an exit
  contract narrower than the script's own usage text. The ``scaffold.py`` row
  named its exit 1 alone, and neither the ``ok`` envelope init returns at exit
  0 nor the ``bad_subject``, ``cannot_write``, ``no_docs`` and ``cannot_read``
  it returns at exit 2; the ``doctype.py`` row named its exit 1 alone, and
  neither the exit 0 it returns when advisories are all that is left nor the
  exit 2 it returns on a stub-only tree or a missing docs directory; the
  ``slop.py`` row named its exit 1 alone, and not the exit 2 a missing or
  unreadable target had just been given. A caller handed a code the README has
  no entry for is in the same position as one handed a status it has no entry
  for, and three of the seven rows had put it there. The ``llmstxt.py`` silence
  is the second thing this test went red on, later and separately, when that
  script's usage text had grown a contract its row still published nothing of.
  What the check could not see either time
  was both sides going quiet at once: two empty sets are equal, so a row and a
  usage text that agree on saying nothing passed it. The silence is now read
  against the script's own ``sys.exit`` calls instead — ``survey.py`` has none
  and publishes, in its own usage text, that a run which reads the repo returns
  0 whatever it found, which is what makes its silence an answer rather than an
  absence; every other script here ends in ``sys.exit(main())``.
- ``test_the_script_table_puts_each_status_under_its_scripts_exit_code`` —
  FR-038 / AC-042. RED against both, and at either revision the assertion that
  fires first is the unread one: ``doctype.py``'s usage text publishes exits 0
  and 1, and nothing in its row was placed against either of them. Behind that,
  and what this test was first written for, the ``scaffold.py``
  row backticked ``check`` inside its exit-1 clause where that script's
  usage text names the word only at exit 2. The check above asked whether each
  status was *named* in the row and never where, so swapping the ``drift.py``
  row's exit-0 and exit-2 groups outright — telling a reader that a missing
  repository is a clean run and that ordinary development is a not-checked one
  — passed every assertion in this module. The rows with no status to be placed
  by are what it went red on the second time, later and separately.
  Only ``drift.py`` and ``scaffold.py`` print a
  JSON status, so five of the seven rows compared nothing at all here, and one
  counter across the whole table stayed positive on the strength of those two:
  the ``doctype.py`` and ``slop.py`` rows could have their exit groups
  inverted and this module stayed green, which was measured rather than
  supposed. The counter is now per row, and a row whose script publishes more
  than one code has to have had something placed against it. For the three
  such rows with no status — ``doctype.py``, ``slop.py``, ``llmstxt.py`` — that
  something is the case under each code, compared word for word. The two left
  over, ``rendered.py`` and ``survey.py``, publish one code and none: a single
  code has no second place to be filed under, and that is why they are exempt
  rather than overlooked.

The count is taken from the suite's own source rather than from
``len(request.session.items)``, and the reason is a false red rather than a
false pass: ``session.items`` holds what *this* invocation collected, and an
invocation naming one module collects that module's tests and nothing else, a
strict subset of what the README counts — so reading it as the size of the
suite would report the README wrong on a run that was right. Measured with
``uvx pytest tests/test_readme.py --collect-only -q``. No number is written
here: the sentence this replaces put one in, said that invocation "would
collect one item", and one item was never what that command prints; a count in
a docstring nothing re-runs is the stale prose this module exists to stop.
A-029 forbids ``skip`` and ``xfail``, so there is no honest way to sit that
case out. ``session.items`` is still used, in the direction that holds under
any invocation — every node id pytest collected must be one the counter
already knew about.

Nothing here runs a script, so neither ``run_script`` nor ``fixture_repo`` is
needed, and nothing is imported from ``scripts/`` (GI-004). Spawning a second
pytest to count the first one's tests would be the obvious way to do this and
is not done: a suite that runs itself recursively pays for the whole suite
twice per invocation and hangs rather than fails when the inner run waits on
something.

No test uses ``@pytest.mark.skip`` or ``xfail`` (A-029).
"""

from __future__ import annotations

import ast
import itertools
import json
import re
from pathlib import Path

# ``tests/test_readme.py`` -> ``plugins/webster/``, the same walk
# ``conftest.py`` does for PLUGIN_ROOT. Recomputed rather than imported from
# conftest: a test module that imports its own conftest works until somebody
# runs pytest from a different rootdir, and this module's whole job is to keep
# working under invocations other than the blessed one.
TESTS_DIR = Path(__file__).resolve().parent
PLUGIN_ROOT = TESTS_DIR.parent
README = PLUGIN_ROOT / "README.md"
PLUGIN_JSON = PLUGIN_ROOT / ".claude-plugin" / "plugin.json"
CONFTEST = TESTS_DIR / "conftest.py"

# Read as text and parsed with ``ast``, never imported. GI-004 bars the import,
# and what an import would cost is the binding: drift.py, llmstxt.py,
# doctype.py, survey.py and slop.py take their root, their docs directory or
# their allowlists out of ``sys.argv`` and the environment at module level, so
# importing one from here runs that binding against pytest's own argv —
# measured, under an argv of ``["pytest", "tests/test_readme.py", "-v"]``
# drift.py's docs directory binds to ``-v``, and the next import hands back the
# cached module still holding it, after this process has chdir'd away.
# scaffold.py and rendered.py read theirs inside ``main`` and would survive it.
# Frozen is not sealed, though, and this comment used to say it was, in the
# words "there would be no other way in anyway": ``runpy.run_path`` re-executes
# the body and re-binds all five, measured on the ``ROOT`` of drift.py,
# llmstxt.py and survey.py, on doctype.py's ``LENS_ALLOW`` and on slop.py's
# ``TARGETS``; under ``run_name="__main__"`` it drove ``drift.py check`` to a
# real JSON line, status ``no_manifest`` at exit 2, in this process and against
# the tree that re-execution had just bound rather than the one the import
# held. What an in-process run of those five skips is the process boundary
# where the environment ``run_script`` builds and the interpreter it resolves
# apply at all — a cost, not an impossibility. All seven are parsed the same
# way regardless, because a rule about which script may be imported is a rule
# somebody has to keep, and ``ast.parse`` keeps none. The five are counted
# because this comment once opened "every script binds", named no script at
# all, and was therefore read as covering all seven, when it was false for
# scaffold.py and for rendered.py. conftest.py and the README each wrote that
# overstatement in their own words rather than a copy of this one, so each had
# to be corrected on its own; both now name the five, and this was the last
# copy standing.
SCRIPTS = PLUGIN_ROOT / "scripts"
DRIFT = SCRIPTS / "drift.py"

# The first column of the row this module checks in the README's script table.
# Matched on the cell rather than on a line number so the check survives the
# table being reordered.
DRIFT_TABLE_CELL = "`scripts/drift.py`"

# The command the Status section must print, verbatim. It is written from the
# repository root, not from the plugin, because that is where a reader of this
# README is standing (A-024, NFR-001).
RUN_COMMAND = "cd plugins/webster && uvx pytest"

# NFR-003 / OT-040 locked the previous release at 0.11.0; this one is 0.12.0. Pinning the
# the next bump has to come through this line as well as through plugin.json and
# the README, and that is the point: the version lives in more than one place,
# and a release that moved only some of them is the half-done release this test
# is here to catch. marketplace.json and the root badge are deliberately NOT
# checked — GI-006 puts their pre-existing drift out of scope for this change.
RELEASE_VERSION = "0.18.0"

# ``python_files`` in pyproject.toml. Kept as a literal rather than read back
# out of the config, so that a change to the glob shows up here as a failing
# count instead of quietly redefining what "the suite" means.
TEST_MODULE_GLOB = "test_*.py"

# The script each row of the README's script table is about. Discovered from
# the table rather than listed here, for the reason ``drift_statuses`` reads a
# docstring rather than a literal: a list in this file is a second place the
# table's membership lives, and the row nobody remembered to add to it is
# exactly the unread prose this module exists to stop.
SCRIPT_CELL = re.compile(r"^\| `scripts/([a-z_]+\.py)` \|")

# An exit-code claim, in both of the forms these files write it. The second is
# the elision ``doctype.py``'s usage text falls into once it has said "exit"
# for the first code in a list -- "exit 1 on a defect, 0 when only advisories
# remain, 2 when there was nothing to check". Read with the first pattern
# alone that contract is exit 1 and nothing else, which is precisely the
# narrower contract the README row had copied out of it.
EXIT_SPELLED = re.compile(r"\bexits?\s+(\d)\b", re.IGNORECASE)
EXIT_ELIDED = re.compile(r",\s+(\d)(?=\s+(?:when|on|if|once|unless)\b)")

# A status as a usage text writes it, and as the README writes it. The prose
# form requires an underscore because English prose has none, so no ordinary
# sentence in a docstring can donate a status that does not exist. The
# single-word statuses -- ``clean``, ``drift``, ``ok``, ``violations`` -- are
# reached from the README side instead, where a backtick is what marks them,
# and checked back against the docstring by name.
PROSE_STATUS = re.compile(r"\b[a-z][a-z0-9]*(?:_[a-z0-9]+)+\b")
TICKED_STATUS = re.compile(r"`([a-z][a-z0-9_]*)`")


def _read(path: Path) -> str:
    """Read one repository file, failing with the path when it is missing."""
    if not path.is_file():
        raise RuntimeError(f"expected {path} to exist; the suite cannot check a file it cannot read")
    return path.read_text(encoding="utf-8")


def status_section() -> str:
    """The body under ``## Status``, up to the next ``## `` heading.

    Scoped to the section so that a copy of one of its claims written anywhere
    else in the README cannot stand in for the claim itself. Nothing outside
    the section stands in for one today, which is the correction: the sentence
    this replaces said the README named version numbers and counts elsewhere
    and that a whole-file search would happily match one of those, and measured
    against the file as it stands none of the three matches anything outside
    the section -- no second ``<N> passing`` line, no second ``Version
    <digits>``, no second copy of the run command. The counts the file does
    state elsewhere it spells as words, nine criteria and seven gates, and the
    digits it carries outside the section are exit codes, ISO/IEC/IEEE 26514,
    the 3.11 floor and the 67 pages of the pre-rule run. So the scope is a
    guard against a copy nobody has written yet rather than against one
    standing now, and saying otherwise put a measurement in this docstring that
    the file refutes.
    """
    lines = _read(README).splitlines()
    try:
        start = lines.index("## Status")
    except ValueError:
        raise RuntimeError(
            f"{README} has no '## Status' heading. FR-038 and AC-042 both name "
            f"that section as where the run command, the count and the version live."
        ) from None
    body = []
    for line in lines[start + 1 :]:
        if line.startswith("## "):
            break
        body.append(line)
    return "\n".join(body)


def status_passing_count() -> int:
    """The N in the Status section's ``N passing`` sentence.

    Exactly one match is required in both directions. Zero means the claim was
    dropped; two means the section states the size of the suite twice and the
    two can disagree, which is the same silent drift one unchecked number has.
    """
    section = status_section()
    matches = re.findall(r"^(\d+) passing\b", section, re.MULTILINE)
    if len(matches) != 1:
        raise RuntimeError(
            f"expected exactly one '<N> passing' line in the {README} Status "
            f"section, found {len(matches)}: {matches}. NFR-001 records the "
            f"suite size there and one number cannot be recorded twice."
        )
    return int(matches[0])


def script_table_row(cell: str) -> str:
    """The one row of the README's script table whose first column is ``cell``.

    Scoped to the row for the reason ``status_section`` is scoped to its
    section: ``clean`` is an ordinary English word this README uses about
    prose and about gates, and a whole-file search for it would report the
    ``drift.py`` row as naming a status it does not name.
    """
    rows = [line for line in _read(README).splitlines() if line.startswith(f"| {cell} |")]
    if len(rows) != 1:
        raise RuntimeError(
            f"expected exactly one {README} table row whose first column is "
            f"{cell}, found {len(rows)}. FR-038 puts the script's contract in "
            f"that row and a contract stated twice can be stated two ways."
        )
    return rows[0]


def drift_statuses(exit_code: str) -> list[str]:
    """The statuses ``drift.py``'s own docstring puts at ``exit_code``.

    Taken out of the script instead of listed here on purpose. A list written
    in this file would be a third place the vocabulary lives, and three copies
    go stale the way the two did: the README row named the pre-spec set for a
    whole run while ``drift.py`` printed ``unrelated_changes``, ``no_git``,
    ``head_missing`` and ``hashes_partial``. Read from the docstring rather
    than from the string literals in the code because the docstring is what
    ``argparse`` publishes as the usage text, so it is the copy a reader of
    either file can reach.
    """
    doc = ast.get_docstring(ast.parse(_read(DRIFT), filename=str(DRIFT)))
    if not doc:
        raise RuntimeError(
            f"{DRIFT} has no module docstring; it is the usage text this test "
            f"reads the status vocabulary out of, and the README row above has "
            f"nothing left to be checked against."
        )
    # One line, so the sentence can be matched across the wrap in the source.
    # The character class admits letters, underscores, commas and spaces and
    # nothing else, which is what keeps each clause from running through the
    # "exit 0," and "exits 1," that separate them.
    sentence = " ".join(doc.split())
    match = re.search(rf"([a-z_][a-z_,\s]*?)\s+exits? {exit_code}\b", sentence)
    if not match:
        raise RuntimeError(
            f"{DRIFT}'s docstring no longer says which statuses exit "
            f"{exit_code}. That sentence is this test's source of truth for "
            f"what the README must name; without it the check below would "
            f"pass on an empty list, which is the shape of gate this suite "
            f"exists to remove."
        )
    names = [part.strip() for part in re.split(r",|\band\b", match.group(1))]
    found = [name for name in names if name]
    if not found:
        raise RuntimeError(
            f"parsed no status names out of {DRIFT}'s 'exit {exit_code}' "
            f"clause: {match.group(1)!r}"
        )
    return found


def script_table_scripts() -> list[str]:
    """The script named in the first column of every row of the script table."""
    names = [
        found.group(1)
        for found in (SCRIPT_CELL.match(line) for line in _read(README).splitlines())
        if found
    ]
    if not names:
        raise RuntimeError(
            f"no `scripts/<name>.py` rows found in {README}. FR-038 puts each "
            f"script's contract in that table, and a check that finds no rows "
            f"to read passes without having read one."
        )
    return names


def script_docstring(filename: str) -> str:
    """One script's module docstring: the usage text ``argparse`` publishes."""
    path = SCRIPTS / filename
    doc = ast.get_docstring(ast.parse(_read(path), filename=str(path)))
    if not doc:
        raise RuntimeError(
            f"{path} has no module docstring. It is the usage text a reader of "
            f"either file can reach, and the README row describing the script "
            f"has nothing left to be checked against."
        )
    return doc


def exit_clauses(text: str) -> list[list[tuple[str, str]]]:
    """Per sentence, every (exit code, the clause of that sentence claiming it).

    One parser, run over a script's usage text and over the README row that
    publishes it. Whatever it reads loosely it reads loosely on both sides and
    the two still have to agree, which is the property a second parser written
    for the README alone would not have.

    Segmentation is paragraph, then sentence, then exit-code claim. Splitting
    paragraphs first is what keeps ``doctype.py``'s usage block from running on
    into the prose beneath it, where ``doc_type`` would otherwise be read as a
    status at exit 2.

    Which side of its code a clause lies on is read off the sentence rather
    than assumed, because both orders are written in these files. A sentence
    that names the case first -- ``drift.py``'s "clean and unrelated_changes
    exit 0, drift exits 1" -- closes each clause AT its code, so the clause is
    the text before it, and whatever follows the last claim belongs to that
    claim; the last part is what reaches ``scaffold.py``'s exit-2 statuses,
    named after the code where ``drift.py`` names them before it. A sentence
    that opens with the code instead -- ``doctype.py``'s "exit 1 on a defect,
    0 when only advisories remain, 2 when there was nothing to check" -- has
    nothing in front of its first code to be that code's clause, and reading
    it the first way filed every case one code late: "on a defect" under exit
    0, "when only advisories remain" under exit 2. That is not a cosmetic
    misread. It is the whole mapping this module checks, shifted, on the three
    rows that have no status name to be placed by instead -- ``doctype.py``,
    ``slop.py`` and ``llmstxt.py``. The sentence this replaces said two, and
    described those two as the scripts here that print human lines rather than
    a JSON status, which is a different set again: five of the seven publish no
    status, four of them printing plain lines and ``survey.py`` printing one
    JSON document with no ``status`` key, and two of those five are exempt for
    publishing one exit code and none.
    """
    sentences: list[list[tuple[str, str]]] = []
    for paragraph in text.split("\n\n"):
        for sentence in re.split(r"(?<=\.)\s+", " ".join(paragraph.split())):
            claims: list[tuple[int, int, str]] = []
            for found in sorted(
                [*EXIT_SPELLED.finditer(sentence), *EXIT_ELIDED.finditer(sentence)],
                key=lambda match: match.start(),
            ):
                # The two patterns can cover the same digit; the first wins,
                # so a code is never counted as two claims in a row.
                if claims and found.start() < claims[-1][1]:
                    continue
                claims.append((found.start(), found.end(), found.group(1)))
            if not claims:
                continue
            # Punctuation and whitespace only: a lead of "and" or "status ok
            # at" is a case, and the sentence is read the other way round.
            code_first = not sentence[: claims[0][0]].strip(" \t-—–:;,")
            found_here: list[tuple[str, str]] = []
            for index, (_, end, code) in enumerate(claims):
                if code_first:
                    start = end
                    stop = claims[index + 1][0] if index + 1 < len(claims) else len(sentence)
                else:
                    start = claims[index - 1][1] if index else 0
                    stop = end if index + 1 < len(claims) else len(sentence)
                found_here.append((code, sentence[start:stop]))
            sentences.append(found_here)
    return sentences


def contract_clauses(text: str) -> list[tuple[str, str]]:
    """The (exit code, clause) pairs of the sentences that publish the contract.

    A sentence that assigns more than one exit code is the contract; one that
    names a single code is commentary about it, and reading the second as the
    first is what left ``hashes_partial`` accepted at exit 0 as well as at
    exit 2 -- ``drift.py``'s usage text ends a later sentence "has not earned
    exit 0", and a status accepted at two codes is a status whose placement
    nothing checked. The exit *set* is still read from every sentence, because
    a code named only in passing ("init never exits 1") is still a code the
    script returns.

    A script whose whole contract is one code has no such sentence --
    ``rendered.py``'s is "exit 1 on a leak" -- so for those every clause is
    contract. Falling back per script rather than globally is what keeps one
    single-code script from dropping silently out of a check the others pass.
    """
    sentences = exit_clauses(text)
    contract = [found for found in sentences if len({code for code, _ in found}) > 1]
    return [pair for found in (contract or sentences) for pair in found]


def exit_codes(text: str) -> set[str]:
    """Every exit code ``text`` claims, in a docstring or in a table row."""
    return {code for found in exit_clauses(text) for code, _ in found}


def statuses_claimed(text: str, marker: re.Pattern) -> dict[str, set[str]]:
    """Each status ``marker`` finds, mapped to the exit codes whose clauses hold it."""
    claimed: dict[str, set[str]] = {}
    for code, clause in contract_clauses(text):
        for name in marker.findall(clause):
            claimed.setdefault(name, set()).add(code)
    return claimed


def exit_codes_naming(text: str, name: str) -> set[str]:
    """The exit codes whose clauses in ``text`` name ``name`` as a whole word.

    A set rather than one code: nothing stops a usage text from naming one
    status in two clauses of the same contract sentence, and a check that
    demanded a single answer would raise on prose that is not wrong. It is
    read from ``contract_clauses`` and not from every sentence, so commentary
    about a status cannot widen the set of codes the README is allowed to file
    it under -- which it did, for ``hashes_partial``, until it was not.
    """
    word = re.compile(rf"\b{re.escape(name)}\b")
    return {code for code, clause in contract_clauses(text) if word.search(clause)}


def clauses_by_code(text: str) -> dict[str, str]:
    """Each exit code ``text`` claims, mapped to all the clause text filed under it.

    Joined rather than kept apart because a script can publish one code from
    two sentences: ``scaffold.py``'s usage text states an exit 2 for ``init``
    and another for ``check``, and both describe what a 2 from that script
    means.
    """
    filed: dict[str, list[str]] = {}
    for code, clause in contract_clauses(text):
        filed.setdefault(code, []).append(clause)
    return {code: " ".join(parts) for code, parts in filed.items()}


# The words of a clause that say which case is being filed. ``exit`` and
# ``exits`` are dropped because every clause has one and a word both sides
# always share distinguishes nothing; the digits go with the pattern that
# found them. Underscores are kept inside a word so ``no_docs`` stays one
# token rather than two.
CASE_WORD = re.compile(r"[a-z][a-z0-9_]*")


def case_words(clause: str) -> set[str]:
    """The words of one clause, as the pairing below compares them."""
    return set(CASE_WORD.findall(clause.lower())) - {"exit", "exits"}


def better_pairing(doc: dict[str, str], row: dict[str, str]) -> tuple[int, int, dict[str, str]] | None:
    """A code-to-code pairing that fits the row to the usage text better than the row's own does.

    ``None`` when nothing beats filing each of the row's clauses under the
    code the row filed it under -- which is the pass.

    This is what places the rows for ``doctype.py``, ``slop.py`` and
    ``llmstxt.py``. Only ``drift.py`` and ``scaffold.py`` print a JSON status,
    and those three print none, so they have no status name for the check above
    to place, and the only thing their row and their usage text both publish is
    which case sits under which code. ``llmstxt.py`` is named here because it
    was not: this paragraph said "those two", written while that script's usage
    text still published no exit code at all and its row was reached by nothing.
    Comparing the two
    by shared words, and asking only that the row's own filing be as good as
    every other, is what makes an inverted row visible: swapping the
    ``doctype.py`` row's exit 1 and exit 2 leaves the exit *set* identical --
    every permutation does -- so a check on the set cannot see it, while the
    words follow the cases and go with them. Measured on the table as it
    stands: each row's own filing is its own best, 23 for ``doctype.py``, 11
    for ``slop.py`` and 17 for ``llmstxt.py``. ``slop.py`` and ``llmstxt.py``
    publish two codes each, so each has exactly one rearrangement, and those
    score 0 and 3. ``doctype.py`` publishes three, so
    "inverting" names no single rearrangement of it: swapping its exits 1 and
    2 drops it to 11, its exits 0 and 2 to 4, its exits 0 and 1 to 20, and the
    two three-cycles to 5 and to 7. Every one of the five is below 23, which
    is the whole of what this asks. The figure recorded here was 11 with no
    swap named beside it -- the 1-and-2 number, inherited without the
    antecedent that produces it, which is a measurement no reader can re-run.
    The README carried the same sentence and was corrected one cycle before
    this copy of it was.

    Ties pass. The claim being made is that the row is filed at least as well
    as any other way, not that it is the uniquely best English, so rewording
    one side cannot turn this red on its own.
    """
    codes = sorted(set(doc) & set(row))
    if len(codes) < 2:
        return None

    def score(order: tuple[str, ...]) -> int:
        return sum(len(case_words(doc[code]) & case_words(row[under]))
                   for code, under in zip(codes, order))

    own = score(tuple(codes))
    best = max(itertools.permutations(codes), key=score)
    if score(best) <= own:
        return None
    return own, score(best), dict(zip(codes, best))


# The calls that can hand a caller an exit code. Having none of them is not on
# its own a contract: a script with no `sys.exit` can still end in a traceback
# at exit 1, and `survey.py` did, on three package manifests it could read. What
# makes a silent row honest is the script publishing that it exits 0 on every
# input it can read and then keeping that -- which `survey.py`'s usage text
# states and its guards hold, reading a malformed package.json, dependency
# table or script target as absent instead of fatally. Both sides being silent
# about a script like that is the truth rather than an omission.
EXIT_CALLS = {"sys.exit", "os._exit", "exit", "SystemExit"}


def exit_sites(filename: str) -> set[str]:
    """The calls in one script's source that can end the process with a code."""
    path = SCRIPTS / filename
    tree = ast.parse(_read(path), filename=str(path))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            called = ast.unparse(node.func)
        elif isinstance(node, ast.Raise) and node.exc is not None:
            called = ast.unparse(node.exc).split("(")[0]
        else:
            continue
        if called in EXIT_CALLS:
            found.add(called)
    return found


def _scan_module(path: Path) -> tuple[list[str], list[str]]:
    """Top-level ``test_*`` function names in one file, and any generative collection found.

    The parse is AST rather than a grep for ``^def test_``. The two constructs
    this sentence used to name cost that grep nothing, and both were measured:
    a nested ``def test_...`` inside a factory is indented and a commented-out
    one carries its ``#`` at column 0, so ``^`` misses each of them exactly as
    this parse does, and on a module holding one real test beside either, grep,
    this parse and pytest all answer 1. What does part them is a column-0
    ``def test_`` inside a triple-quoted string -- a docstring showing the
    shape of a case, which is prose here and is the shape this very file
    writes. Grep answers 2 on that module, this parse and pytest 1, and a text
    count reading one test high is wrong in the direction that reads as fine:
    the README's number would sit above the suite while every gate stayed
    green.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: list[str] = []
    generative: list[str] = []

    def decorator_reasons(node: ast.AST, owner: str) -> None:
        for decorator in getattr(node, "decorator_list", []):
            for sub in ast.walk(decorator):
                if isinstance(sub, ast.Attribute) and sub.attr == "parametrize":
                    generative.append(f"{path.name}::{owner} is parametrized")
                if isinstance(sub, ast.keyword) and sub.arg == "params":
                    generative.append(f"{path.name}::{owner} is a fixture with params")

    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            decorator_reasons(node, node.name)
            if node.name.startswith("test"):
                names.append(node.name)
            if node.name == "pytest_generate_tests":
                generative.append(f"{path.name} defines pytest_generate_tests")
        elif isinstance(node, ast.ClassDef) and node.name.startswith("Test"):
            generative.append(f"{path.name}::{node.name} is a collected test class")
    return names, generative


def suite_tests() -> set[tuple[str, str]]:
    """Every ``(module filename, test name)`` this suite defines.

    One item per name is only true while the suite collects one item per
    function. Parametrized tests, fixtures with ``params``, collected classes
    and ``pytest_generate_tests`` all multiply that, so each is refused here
    with the reason rather than allowed to make the count quietly low — a
    RuntimeError at collection time is loud, and a count that is short by three
    is not.
    """
    found: set[tuple[str, str]] = set()
    generative: list[str] = []
    modules = sorted(TESTS_DIR.glob(TEST_MODULE_GLOB))
    if not modules:
        raise RuntimeError(f"no {TEST_MODULE_GLOB} modules under {TESTS_DIR}")
    for path in [*modules, CONFTEST]:
        names, reasons = _scan_module(path)
        generative.extend(reasons)
        if path != CONFTEST:
            found.update((path.name, name) for name in names)
    if generative:
        raise RuntimeError(
            "this counter assumes one collected test per test function, and "
            "that is no longer true: "
            + "; ".join(sorted(generative))
            + f". Teach {Path(__file__).name} to expand those before adding them, "
            f"or the README's passing count goes quietly low."
        )
    return found


def collected_tests(session) -> set[tuple[str, str]]:
    """``(module filename, test name)`` for every item pytest collected this run.

    Node ids are reduced to the file's basename because the prefix is relative
    to the rootdir, and the rootdir is not the directory pytest was launched
    from. Measured: from inside the plugin and from the repository root alike
    the prefix is ``tests/``, because ``pyproject.toml`` here carries a
    ``[tool.pytest.ini_options]`` table and pytest walks up to it from the path
    it was handed either way. ``plugins/webster/tests/`` takes forcing it --
    ``--rootdir .`` from the repository root. The sentence this replaces gave
    the long prefix to the repository root on its own, which is a launch
    directory rather than a rootdir and does not produce it.
    """
    collected = set()
    for item in session.items:
        parts = item.nodeid.split("::")
        collected.add((Path(parts[0]).name, parts[-1]))
    return collected


# -----------------------------------------------------------------------------
# FR-038 / AC-042 / NFR-001: the Status section's count is the suite's size.
# -----------------------------------------------------------------------------
def test_status_passing_count_matches_the_collected_suite(request):
    defined = suite_tests()
    declared = status_passing_count()

    assert declared == len(defined), (
        f"{README} Status section claims {declared} passing, but "
        f"{TESTS_DIR}/{TEST_MODULE_GLOB} defines {len(defined)} tests. "
        f"NFR-001 records the suite size in the README, so a test added "
        f"without touching that line makes the README state a count nobody "
        f"can reach. Run `{RUN_COMMAND}` and write the number it reports."
    )

    # The live half. ``session.items`` is what pytest actually collected in
    # this invocation, which under `-k`, a single module, or a single node id
    # is a subset — so it is checked for membership, not for size. What it
    # proves is the half the source scan cannot: that nothing pytest collects
    # is invisible to the counter above.
    unknown = collected_tests(request.session) - defined
    assert not unknown, (
        f"pytest collected {len(unknown)} test(s) this run that the source scan "
        f"did not find: {sorted(unknown)}. The count in {README} is therefore "
        f"lower than what `{RUN_COMMAND}` reports, and every assertion above it "
        f"is measuring the wrong suite."
    )


# -----------------------------------------------------------------------------
# AC-042 / OT-040 / NFR-003: the Status section names how to run the suite, and
# the version it names is the one the plugin manifest ships.
# -----------------------------------------------------------------------------
def test_status_section_names_the_run_command_and_the_version():
    section = status_section()

    assert RUN_COMMAND in section, (
        f"{README} Status section does not contain `{RUN_COMMAND}`. AC-042 asks "
        f"the README to name the run command; a recorded count with no way to "
        f"reproduce it is a number the reader has to take on trust."
    )

    manifest_version = json.loads(_read(PLUGIN_JSON))["version"]
    assert manifest_version == RELEASE_VERSION, (
        f"{PLUGIN_JSON} reads {manifest_version!r}; NFR-003 and OT-040 pin this "
        f"release at {RELEASE_VERSION!r}."
    )
    assert f"Version {manifest_version}." in section, (
        f"{README} Status section does not say 'Version {manifest_version}.' "
        f"while {PLUGIN_JSON} does. The README read 'Version 0.2.0.' at every "
        f"one of the eleven manifest releases from 0.3.0 to 0.10.0 before "
        f"anything compared the two."
    )


# -----------------------------------------------------------------------------
# AC-042 / FR-038 / NFR-002: the two claims that live outside Status — the new
# env var beside the old one, and the interpreter floor tomllib requires.
# -----------------------------------------------------------------------------
def test_readme_documents_webster_survey_and_the_python_floor():
    text = _read(README)

    # "Beside", not merely "somewhere in the file": a reader who has found
    # WEBSTER_LENS_ALLOW is the reader who needs to know the second variable
    # exists, and a mention four sections away is one they will not reach.
    paragraphs = [p for p in text.split("\n\n") if "WEBSTER_LENS_ALLOW" in p]
    assert len(paragraphs) == 1, (
        f"expected exactly one {README} paragraph to introduce "
        f"WEBSTER_LENS_ALLOW, found {len(paragraphs)}"
    )
    assert "WEBSTER_SURVEY" in paragraphs[0], (
        f"{README} documents WEBSTER_LENS_ALLOW without WEBSTER_SURVEY beside "
        f"it. AC-042 asks for both: doctype.py reads WEBSTER_SURVEY (GI-002, "
        f"CT-003), and an env var no page names is one nobody sets."
    )

    assert "Python 3.11" in text, (
        f"{README} does not name the Python 3.11 floor. NFR-002 asks for it in "
        f"a comment or the README because survey.py and llmstxt.py import "
        f"tomllib with no fallback path, and on 3.10 that is an ImportError "
        f"rather than a message."
    )


# -----------------------------------------------------------------------------
# FR-038 / AC-042: the script table's drift.py row states the vocabulary the
# script actually prints, including the status US-002 added.
# -----------------------------------------------------------------------------
def test_the_script_table_names_drift_pys_own_status_vocabulary():
    row = script_table_row(DRIFT_TABLE_CELL)
    exit_zero = drift_statuses("0")
    exit_two = drift_statuses("2")

    # Asserted rather than assumed: the exit-0 clause is parsed out of prose,
    # and a docstring edit that dropped US-002's status would quietly shrink
    # what the row below is required to name.
    assert "unrelated_changes" in exit_zero, (
        f"{DRIFT}'s docstring no longer puts 'unrelated_changes' at exit 0. "
        f"ST-002 and FR-008 are that status: code that changed under no "
        f"citation exits 0, because a gate that fails on ordinary development "
        f"teaches the reader to stop reading the gate."
    )

    # Backticked, not merely present. `clean` and `drift` are also ordinary
    # words in this README's prose, and a row that happened to use one in a
    # sentence would otherwise read as a row that publishes the status.
    missing = [name for name in exit_zero + exit_two if f"`{name}`" not in row]
    assert not missing, (
        f"the {DRIFT_TABLE_CELL} row of {README} does not name "
        f"{', '.join(missing)} as `literal(s)`, but {DRIFT}'s own usage text "
        f"does: exit 0 is {exit_zero}, exit 2 is {exit_two}. FR-038 asks the "
        f"README to describe the scripts as they now are; the row carried the "
        f"pre-spec vocabulary through the whole change, so a reader looking up "
        f"a status the tool had just printed at them found no entry for it."
    )


# -----------------------------------------------------------------------------
# FR-038 / AC-042 / OT-040: every row publishes the exit set its own script
# publishes, and not the part of it somebody happened to write down.
# -----------------------------------------------------------------------------
def test_the_script_table_publishes_each_scripts_own_exit_set():
    narrower = []
    silent = []
    for filename in script_table_scripts():
        published = exit_codes(script_docstring(filename))
        stated = exit_codes(script_table_row(f"`scripts/{filename}`"))
        if published or stated:
            if published != stated:
                narrower.append(
                    f"{filename}: its usage text exits {sorted(published) or ['nothing']}, "
                    f"the README row exits {sorted(stated) or ['nothing']}"
                )
            continue
        # Neither side names a code, so there is no comparison to make and the
        # row would otherwise pass on two empty sets -- the shape of gate this
        # suite exists to remove, and the shape the counter this replaced had:
        # one global tally, satisfied by the rows that did publish something,
        # while a row nobody compared sat under it. So the silence is checked
        # against the script rather than taken for the answer. `survey.py` is
        # the honest case, and on a stated property rather than on an absence:
        # it has no sys.exit anywhere, and its usage text publishes that a run
        # which reads the repo returns 0 whatever it found. A package.json that
        # is not an object, a dependency field that is not a table and a script
        # target that is not a string are read as absent under that contract;
        # each ended the run at exit 1 before its guard went in, and each has a
        # test in test_survey.py. Any other script here ends in
        # `sys.exit(main())`, so the same silence from one of those is a
        # contract that went unwritten on both sides at once.
        sites = exit_sites(filename)
        if sites:
            silent.append(
                f"{filename}: neither its usage text nor the README row names an "
                f"exit code, and its source calls {', '.join(sorted(sites))}"
            )

    assert not silent, (
        f"these scripts can hand a caller an exit code that neither their "
        f"usage text nor their {README} row publishes:\n  " + "\n  ".join(silent)
        + f"\nThe comparison above has nothing to make of a row like that, so "
        f"it passed on two empty sets. A code with no entry anywhere is worse "
        f"than one entry disagreeing with another: the reader has nowhere left "
        f"to look."
    )
    assert not narrower, (
        f"these {README} rows publish a different exit set from the script "
        f"they describe:\n  " + "\n  ".join(narrower) + f"\nFR-038 asks the "
        f"table to describe the scripts as they now are. A caller handed a "
        f"code the README has no entry for is in exactly the position of one "
        f"handed a status it has no entry for, and the row is where both are "
        f"looked up."
    )


# -----------------------------------------------------------------------------
# FR-038 / AC-042: each status is published under the exit code its own script
# returns it at. Naming it somewhere in the row is what the check above does.
# -----------------------------------------------------------------------------
def test_the_script_table_puts_each_status_under_its_scripts_exit_code():
    misplaced = []
    unpublished = []
    inverted = []
    unread = []
    contracts = []
    for filename in script_table_scripts():
        doc = script_docstring(filename)
        row = script_table_row(f"`scripts/{filename}`")
        in_row = statuses_claimed(row, TICKED_STATUS)
        compared = 0

        for name, codes in sorted(in_row.items()):
            compared += 1
            allowed = exit_codes_naming(doc, name)
            if not allowed:
                misplaced.append(
                    f"{filename}: the row publishes `{name}` at exit "
                    f"{sorted(codes)}, and the script's usage text names it at "
                    f"no exit code at all"
                )
            elif not codes <= allowed:
                misplaced.append(
                    f"{filename}: the row puts `{name}` at exit {sorted(codes)}, "
                    f"its usage text at exit {sorted(allowed)}"
                )

        for name, codes in sorted(statuses_claimed(doc, PROSE_STATUS).items()):
            compared += 1
            if not in_row.get(name, set()) & codes:
                unpublished.append(
                    f"{filename}: its usage text puts {name} at exit "
                    f"{sorted(codes)}, the row at exit "
                    f"{sorted(in_row.get(name, set())) or ['none']}"
                )

        # Both loops above are driven by status names, so a script that prints
        # no status leaves them empty and the row goes unplaced. That is not a
        # small corner: only `drift.py` and `scaffold.py` print a JSON status,
        # so five of the seven rows reached this point with nothing compared,
        # and the tally that guarded it was one counter across the whole table
        # which those two kept above zero. What a row and its usage text both
        # publish even with no status between them is which case sits under
        # which code, so that is what is placed.
        filed = clauses_by_code(doc)
        published = clauses_by_code(row)
        pairing = better_pairing(filed, published)
        if len(set(filed) & set(published)) > 1:
            compared += 1
        if pairing is not None:
            own, best, better = pairing
            inverted.append(
                f"{filename}: the row's clauses fit its usage text's better "
                f"read as {better} ({best} words shared) than as each code's "
                f"own ({own})"
            )

        if len(exit_codes(doc)) > 1:
            contracts.append(filename)
            if not compared:
                unread.append(
                    f"{filename}: its usage text publishes exits "
                    f"{sorted(exit_codes(doc))} and nothing in its row was "
                    f"placed against any of them"
                )

    assert contracts, (
        f"no script in the {README} table publishes more than one exit code, "
        f"so there was no placement anywhere for the assertions below to be "
        f"about. Finding none means the parser stopped reading the usage "
        f"texts, not that the scripts stopped assigning cases to codes."
    )
    assert not unread, (
        f"these {README} rows had nothing at all placed against the script "
        f"they describe, so every assertion below held over them "
        f"vacuously:\n  " + "\n  ".join(unread) + f"\nA row nobody compared is "
        f"a row that can say anything. The two rows whose scripts publish one "
        f"code or none — `rendered.py` and `survey.py` — are not here and "
        f"cannot be: a single code has no second place to be filed under."
    )
    assert not inverted, (
        f"these {README} rows describe their script's cases under the wrong "
        f"exit codes:\n  " + "\n  ".join(inverted) + f"\nThe row's own filing "
        f"has to fit the usage text at least as well as any other pairing of "
        f"the two. Swapping a row's exit groups leaves the exit set it "
        f"publishes identical, so the check above cannot see it, and the "
        f"reader it misleads is the one who came to the table holding a code."
    )
    assert not misplaced, (
        f"these {README} rows file a status under an exit code its script "
        f"does not return it at:\n  " + "\n  ".join(misplaced) + f"\nNaming "
        f"every status somewhere in the row was already checked above and is "
        f"not the claim a reader makes of the table: they read the row to "
        f"learn which code they were handed, and a row whose groups are "
        f"inverted answers that question wrongly while naming everything."
    )
    assert not unpublished, (
        f"these statuses appear in a script's usage text at an exit code the "
        f"{README} row does not put them at:\n  " + "\n  ".join(unpublished) + f"\n"
        f"FR-038 asks the table to describe the scripts as they now are, and "
        f"the row is the only place a reader can look one of these up."
    )
