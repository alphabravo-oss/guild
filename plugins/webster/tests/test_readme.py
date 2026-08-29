"""Tests for the claims plugins/webster/README.md makes about this suite (spec US-010).

Every other module in this directory tests a script. This one tests the README,
because the README is the only place in the plugin that states a number nothing
verified. FR-038 asks the Status section to carry the run command and the
passing count; AC-042 asks it to carry the version and ``WEBSTER_SURVEY``;
NFR-001 asks that ``cd plugins/webster && uvx pytest`` exit 0 *with the count
recorded in README*. A hard-coded count with no test behind it is a claim that
reports a pass without having checked anything, which is the class of failure
this whole spec exists to remove — and it fails silently, in the direction that
looks fine: the first test anybody adds makes the sentence false while every
gate stays green.

Which claim each test pins, and what it did before this module existed
(FR-039, AC-039 — the red-first property is recorded here rather than enforced
at runtime, matching test_slop.py and test_scaffold.py):

- ``test_status_passing_count_matches_the_collected_suite`` — FR-038 / AC-042 /
  NFR-001. RED on the README as it stood: the Status section read ``69
  passing`` while ``tests/test_*.py`` defined 77 test functions. The count was
  written by hand when the suite had 69 and never moved again, which is exactly
  the drift this test makes impossible to commit.
- ``test_status_section_names_the_run_command_and_the_version`` — AC-042 /
  OT-040 / NFR-003. Green before and after by design: it guards the run command
  and the 0.11.0 bump against being lost while the count line is edited.
- ``test_readme_documents_webster_survey_and_the_python_floor`` — AC-042 /
  FR-038 / NFR-002, the other half of the same guard, for the two claims that
  live outside the Status section.
- ``test_the_script_table_names_drift_pys_own_status_vocabulary`` — FR-038 /
  AC-042. RED on the README as it stood: the ``scripts/drift.py`` row said
  ``no_anchors`` and "Exit 1 on drift, 2 on nothing to measure", which was the
  vocabulary before this spec changed it. ``unrelated_changes`` — the status
  US-002 exists to add, and the one that tells a reader ordinary development is
  not a finding — appeared nowhere in the file, and neither did ``no_git``,
  ``head_missing`` or ``hashes_partial``. Same failure as the count above, one
  table row over: prose describing a script that moved under it.

The count is taken from the suite's own source rather than from
``len(request.session.items)``, and the reason is a false red rather than a
false pass: ``session.items`` holds what *this* invocation collected, so
``uvx pytest tests/test_readme.py`` would collect one item and this module
would report the README wrong when it is right. A-029 forbids ``skip`` and
``xfail``, so there is no honest way to sit that case out. ``session.items`` is
still used, in the direction that holds under any invocation — every node id
pytest collected must be one the counter already knew about.

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

# Read as text and parsed with ``ast``, never imported: every script binds ROOT,
# DOCS and its allowlists from ``sys.argv`` and the environment at module level
# (GI-004), so an import here would run that binding against pytest's own argv.
DRIFT = PLUGIN_ROOT / "scripts" / "drift.py"

# The first column of the row this module checks in the README's script table.
# Matched on the cell rather than on a line number so the check survives the
# table being reordered.
DRIFT_TABLE_CELL = "`scripts/drift.py`"

# The command the Status section must print, verbatim. It is written from the
# repository root, not from the plugin, because that is where a reader of this
# README is standing (A-024, NFR-001).
RUN_COMMAND = "cd plugins/webster && uvx pytest"

# NFR-003 / OT-040 locks this release at 0.11.0. Pinning the literal here means
# the next bump has to come through this line as well as through plugin.json and
# the README, and that is the point: the version lives in more than one place,
# and a release that moved only some of them is the half-done release this test
# is here to catch. marketplace.json and the root badge are deliberately NOT
# checked — GI-006 puts their pre-existing drift out of scope for this change.
RELEASE_VERSION = "0.11.0"

# ``python_files`` in pyproject.toml. Kept as a literal rather than read back
# out of the config, so that a change to the glob shows up here as a failing
# count instead of quietly redefining what "the suite" means.
TEST_MODULE_GLOB = "test_*.py"


def _read(path: Path) -> str:
    """Read one repository file, failing with the path when it is missing."""
    if not path.is_file():
        raise RuntimeError(f"expected {path} to exist; the suite cannot check a file it cannot read")
    return path.read_text(encoding="utf-8")


def status_section() -> str:
    """The body under ``## Status``, up to the next ``## `` heading.

    Scoped to the section rather than the whole file because the README names
    version numbers and counts elsewhere (the merge table, the seven gates),
    and a whole-file search would happily match one of those and call the
    Status section verified.
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


def _scan_module(path: Path) -> tuple[list[str], list[str]]:
    """Top-level ``test_*`` function names in one file, and any generative collection found.

    The parse is AST rather than a grep for ``^def test_``: a helper that
    defines a nested ``def test_...`` inside a factory, or a commented-out test,
    would make a text count wrong in the direction that reads as fine.
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

    Node ids are reduced to the file's basename because the prefix moves with
    the rootdir: ``tests/test_slop.py::test_x`` from inside the plugin,
    ``plugins/webster/tests/test_slop.py::test_x`` from the repository root.
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
        f"while {PLUGIN_JSON} does. The README carried 'Version 0.2.0' through "
        f"eight releases of the manifest before anything compared the two."
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
