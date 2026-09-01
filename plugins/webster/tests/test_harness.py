"""Tests for the harness itself: conftest's helpers, the committed fixture, and
the change surface (US-010; FR-029, FR-030, FR-042, FR-043; CT-007, GI-005,
GI-007; OT-038, OT-043; AC-039, AC-040).

Seven of this suite's nine modules take a webster script as their subject:
``test_doctype.py``, ``test_drift.py``, ``test_llmstxt.py``, ``test_prose.py``,
``test_scaffold.py``, ``test_slop.py`` and ``test_survey.py``. This module and
``test_readme.py`` are the other two, and neither has a script for a subject:
they test the plugin's own records — the harness those six stand on, and the
README that counts them. One ``run_script`` call does appear below, handing
``survey.py`` to the child interpreter — not to read a version back, which is
the separate ``python3 -c`` probe's job, but because a child under 3.11 cannot
get past that script's ``import tomllib`` and so cannot come back clean. What
that test measures is the harness. The sentence this replaces read "Every
other module in this suite tests a webster script", which counted
``test_readme.py`` — whose own docstring says nothing there runs a script —
among the modules that do. A harness claim nobody measures is the same false
pass ``drift.py`` and ``doctype.py`` are being fixed to stop reporting — and
five such claims had already gone bad:

- ``conftest.py``'s interpreter-skew docstring asserted the child ``python3``
  "resolves through PATH to 3.14.6" and is "not the one running pytest". Under
  the blessed ``uvx pytest`` both are false: uv prepends its ephemeral venv to
  PATH, so the child is uv's 3.11.14 and is the very interpreter running
  pytest. Prose cannot go stale silently once a test measures it.
- ``conftest.py``'s ``build_repo`` fixture was written for OT-038's
  two-consecutive-builds check and then had no caller at all, so the
  deterministic-hash property it exists to prove was never once proved.
- the ``file:line`` citations in this module and in ``conftest.py`` had gone
  stale as the scripts around them were fixed, and one of them justified
  ``check=True`` by pointing at ``run_script`` — the single helper in the
  harness that deliberately has no ``check=``. Citations in both files now name
  symbols, not line numbers, for every file this change is allowed to edit.
  This module cites no line number of its own, and the two ``conftest.py``
  still carries both point into ``plugins/forge/tests/``, which GI-007 freezes.
  ``src/app/main.py:15`` appears in both files and points inside the surface,
  but it is the fixture's planted anchor — the string under discussion, not
  somewhere a reader is being sent to look.
- ``strip_already_made``, the one exception in the GI-007 guard below, asked
  whether the working tree still held a path — and the comment above it
  claimed that made the exception unable to admit anything, because "a path
  that survives to land is on disk". Both were wrong in the same direction.
  The diff the guard reads is ``baseline..HEAD``, a comparison of two trees
  that never looks at disk, so an evidence log committed into HEAD and then
  removed without committing the removal is missing from the working tree and
  present in the change — and was waived. The only automated guard behind
  GI-007 reported a change as inside its surface at the moment it was not.
  The question is now put to ``git ls-tree -r HEAD``, which is the tree the
  diff is actually about.
- the comment above ``conftest.py``'s ``FIXTURE_COMMITS`` said the explicit
  ``+00:00`` offset averts a divergence that "is exactly what OT-038 asks two
  consecutive builds to prove cannot happen". Two builds in one process on one
  host agree with or without the offset — the divergence a bare date causes is
  between machines in different zones, and nothing here varied one. The
  comment named as its evidence the one test that could not supply it, so the
  only claim in this harness about *other* machines went unmeasured while
  reading as though it had been settled.

Which requirement each test pins, and whether it is a red-first fix test or a
guard over a truth that was asserted but never measured (FR-039 asks for this
distinction to be recorded here rather than enforced at runtime):

======================================================  ==============  ==========================
test                                                    pins            kind
======================================================  ==============  ==========================
test_run_script_child_python3_clears_the_plugin_floor   CT-007 FR-029   guard (docstring was wrong)
test_two_fixture_repo_builds_produce_the_same_head      FR-030 OT-038   guard (build_repo unused)
test_fixture_head_survives_a_change_of_host_timezone    FR-030 AC-040   guard (offset unmeasured)
test_committed_fixture_excludes_git_website_manifest    FR-042 AC-040   guard (GI-005 unmeasured)
test_only_the_writable_file_set_is_tracked_and_changed  FR-043 OT-043   guard (GI-007 unmeasured)
======================================================  ==============  ==========================

None of the five is red-first in the ``AC-039`` sense: they pin no script fix,
so there is no pre-change script they fail against. They are the tests that
would have caught the claims above. Measured over the seven test modules the
repository held at the commit before this file first appeared in it: FR-039
was named by all seven, and the casting's other four FR ids — FR-029, FR-030,
FR-042 and FR-043 — were named by no test at all. FR-029 and FR-030 appeared
only in ``conftest.py``, which asserts nothing. An earlier version of this
sentence called FR-042 and FR-043 "the only two", counting a mention in an
unexecuted docstring as coverage; the rows above are what actually put a test
behind all four.

No test here uses ``@pytest.mark.skip`` or ``xfail`` (NFR-001). The one path
that cannot assert everything — a checkout too shallow to reach the change's
baseline commit — still asserts the tracked-tree half and says so in place of
skipping, because a skipped scope test reads exactly like a passing one.
"""

from __future__ import annotations

import ast
import os
import subprocess
from pathlib import Path

# ``tests/test_harness.py`` -> ``plugins/webster/`` -> the repository root: the
# same walk ``conftest.py`` does for PLUGIN_ROOT, recomputed rather than
# imported for the reason ``test_readme.py`` gives above its own ``TESTS_DIR``
# — a module that imports
# its own conftest works until somebody runs pytest from a different rootdir,
# and the scope assertions below have to keep working from the repository root
# as well as from the plugin.
TESTS_DIR = Path(__file__).resolve().parent
PLUGIN_ROOT = TESTS_DIR.parent
REPO_ROOT = PLUGIN_ROOT.parent.parent
FIXTURE_REPO_SRC = TESTS_DIR / "fixtures" / "repo"
PLUGIN_REL = PLUGIN_ROOT.relative_to(REPO_ROOT).as_posix()

# A-001 set the plugin's floor at 3.11 so ``survey.py`` could use stdlib
# ``tomllib``. The floor is asserted, not a version number: which ``python3``
# PATH resolves is a property of the machine and of how pytest was launched
# (uv's venv under ``uvx pytest``, the shell's under ``python3 -m pytest``),
# and pinning either one would fail on the other invocation for no good reason.
PYTHON_FLOOR = (3, 11)

# The command whose output this test reads, kept as data so the assertion
# message can quote exactly what was run.
VERSION_PROBE = "import sys; print(sys.version_info[:2])"

# The commit this change branched from. FR-043 is a statement about the change,
# so it needs a baseline to diff against; ``resolve_baseline`` falls back to a
# merge-base when a shallow or grafted checkout cannot see this one.
BASELINE_COMMIT = "0c2b13b"

GIT_TIMEOUT = 30

# Two zones 21 hours apart and on opposite sides of the date line, so a date
# with no offset is not merely a different instant under each but a different
# calendar day. Both have shipped in tzdata for decades; if a machine somehow
# lacks them git falls back to UTC, and the control in the timezone test finds
# two equal hashes and says so rather than passing quietly.
TZ_EAST = "Pacific/Kiritimati"  # +14, the earliest zone there is
TZ_WEST = "America/Los_Angeles"  # -07/-08

# The control commit's timestamp, deliberately written without an offset: it is
# the form ``conftest.FIXTURE_COMMITS`` does not use, and showing what that form
# does under two zones is the control's entire job.
BARE_DATE = "2026-08-27T16:49:56"

# Same reasoning as ``conftest.py``'s ``_GIT_ENV`` and ``test_drift.py``'s
# ``GIT_ENV``: a developer's ~/.gitconfig arrives through HOME and the suite
# cannot enumerate what it holds. That is not the reasoning this comment used
# to give, which was that ``status.relativePaths`` "or a pathspec alias would
# change what these queries return on that machine only". ``git`` and
# ``git_in`` below are the two helpers that carry this env, and between them
# they issue six subcommands: ``diff``, ``log``, ``ls-files``, ``ls-tree``,
# ``merge-base`` and ``rev-parse``. Measured on all six, in every argv form
# they are called with here: a global ``status.relativePaths`` leaves all six
# byte-identical whether it is set true or false, because the setting reaches
# ``git status`` and neither helper runs it — and ``true``, the value the old
# sentence named, is git's own default, so it could not have moved even that.
# An ``[alias]`` entry shadowing each of the six leaves all six byte-identical
# too, because git ignores an alias that hides a built-in. Neither result is
# an unread config file: under those same two files ``status.relativePaths =
# false`` does change ``git status --short`` run from a subdirectory, and an
# alias on a name git has no built-in for does fire. The sentence this
# replaces said "every query this module runs" and then named three of the
# six. The guard stays because it costs nothing, not because either example
# was real. Read no configuration but the repository's own.
GIT_ENV = {"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}

# GI-007's writable file set, as a predicate. ``scripts/*.py`` is deliberately
# depth-1 and ``.py``-only rather than ``scripts/**``: A-035 enumerates the
# scripts themselves, not the directory holding them. Measured against a
# ``scripts/**`` variant of this predicate over eleven probe paths, the two
# disagree on exactly two, both the same shape: a markdown file nested under
# ``scripts/``, which the variant admits and this one refuses. Such a tree of
# generated forge-spec markdown did sit untracked there partway through this
# change, and a recursive glob would have waved the whole of it through the
# moment anyone committed it; it has since been removed, so the shape is the
# argument and the listing is not. The predicate is written against that shape
# and asserts nothing about what the working tree happens to hold at any given
# moment — a guard whose correctness depends on today's directory listing is a
# guard that stops guarding the day the listing changes.
_WRITABLE_FILES = frozenset(
    {"README.md", "pyproject.toml", ".claude-plugin/plugin.json"}
)

# GI-001 makes the markdown layers read-only for this change; it does not make
# them untracked, and they have been in the repository since long before it.
# GI-007 enumerated what may be *written*, not what may exist, so these are the
# tracked paths that are legitimately outside the writable set.
READ_ONLY_LAYERS = ("agents", "commands", "skills")

# Corrected after the Foundry run closed, by the maintainer's decision, not
# by the change GI-007 scopes. During the run these two files were read-only
# under GI-001 and the mismatch was recorded as SPEC_CHANGE_REQUIRED:
# ``commands/audit.md`` published an exit map drift.py no longer has (exit 2
# summarised as "no manifest" when six statuses carry it, ``unrelated_changes``
# absent from exit 0) and neither command saved the survey or set
# ``WEBSTER_SURVEY``, so the allowlist doctype.py reads through it (GI-002,
# CT-003) could not be reached from the audit at all. With the code verified,
# the release was then published the way every earlier webster bump was:
# ``marketplace.json`` and the root README badge moved 0.7.0 -> 0.11.0 to
# match ``plugin.json`` (``/plugin`` reads the marketplace entry to see an
# update at all). Repository-relative. The changed half below admits exactly
# these four paths; GI-007's writable set (``in_writable_set``) is unchanged,
# so any other read-only-layer edit or a second marketplace change still
# fails here.
# The two paths outside the plugin that a webster release legitimately moves:
# ``/plugin`` reads the marketplace entry to see an update at all, and the root
# badge is read by anyone who never opens the plugin. Everything else in the
# repository stays out of a webster change, which is what the changed half
# below now measures.
POST_RUN_CORRECTIONS = frozenset(
    {
        ".claude-plugin/marketplace.json",
        "README.md",
    }
)

# The Foundry run's own bookkeeping. Each casting commits its re-executable
# evidence logs under this prefix and the lead strips the directory before the
# change lands, so the paths pass through the branch's history without ever
# being part of the change FR-043 describes.
#
# This is not a hole in ``changed_path_allowed``. That predicate answers GI-007
# and nothing else, and it answers "no" for every path here — an earlier
# version returned "yes" for anything under ``evidence/``, which widened the
# only automated guard behind GI-007 past the five locations it exists to
# enforce. There is exactly one place below where this prefix waives anything,
# ``strip_already_made``, and exactly one thing it waives there: a path the
# diff still reports and HEAD no longer holds — a strip whose deletion is
# committed. The name appears elsewhere in this file as a cross-reference, as
# a constructed input to that predicate's own assertions, and inside a failure
# message; none of those grants a path anything. A path that survives to land
# is in HEAD's tree and is judged like any other, so this
# exception cannot admit anything. It is the only exception in this file, and
# it is asked of HEAD rather than of the working tree for the reason
# ``strip_already_made`` gives.
#
# Until that strip happens the changed half of the scope test reports every
# committed evidence log as outside the surface, and it is meant to: a run that
# ends with its evidence still committed has in fact changed paths GI-007 does
# not allow. NFR-001 forbids skip and xfail, so the test fails and names them
# rather than being taught not to look.
RUN_EVIDENCE_PREFIX = "evidence/"


def git(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    """Run one read-only git query against this repository.

    ``check=True`` by default: a git failure here is a broken premise rather
    than a finding, and it should surface at the line that caused it — the
    stance ``conftest.py``'s ``_git`` takes for the fixture repo. (Not
    ``run_script``, which passes every exit code back to its caller on
    purpose: there, a non-zero status is the finding.)
    """
    env = {key: os.environ[key] for key in ("PATH", "HOME") if key in os.environ}
    env.update(GIT_ENV)
    return subprocess.run(
        ["git", *args],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        check=check,
        timeout=GIT_TIMEOUT,
        env=env,
    )


def nul_separated(result: subprocess.CompletedProcess) -> list[str]:
    """Paths from a ``-z`` git query.

    ``-z`` rather than the newline forms because the text forms do not hand
    every path back as its own bytes. This helper is handed three queries and
    no others — ``ls-files``, ``ls-tree`` and ``diff --name-only`` — and
    measured over one tracked path per byte a name may carry, each of the three
    quoted and backslash-escaped 38 of 130: the 31 control bytes 0x01-0x1f,
    0x7f, the double quote, the backslash, and all four non-ASCII characters
    tried, that last group under ``core.quotePath``, which is on by default. A
    scope check that silently mis-reads such a path is a scope check that
    passes when it should not.

    A space is not one of the 38, which is what this passage used to claim it
    was: 0x20 comes back bare from all three. The query that does quote it is
    ``git status --porcelain``, and 0x20 is the only byte on which it and these
    three disagree — so it is why A-003 moved ``drift.py`` to ``--porcelain
    -z``, and it is not a reason that reaches this helper.
    """
    return [path for path in result.stdout.split("\0") if path]


def head_tracked_paths() -> frozenset[str]:
    """Every path the HEAD commit's tree carries, repository-relative.

    ``ls-tree -r HEAD`` and not ``ls-files``: the index and the working tree
    may each disagree with HEAD, and what a committed change carries is a
    property of HEAD alone. Keeping those apart is the whole of
    ``strip_already_made``.
    """
    return frozenset(nul_separated(git("ls-tree", "-r", "-z", "--name-only", "HEAD")))


def in_writable_set(rel: str) -> bool:
    """True when ``rel``, relative to ``plugins/webster``, is one of GI-007's five."""
    if rel in _WRITABLE_FILES:
        return True
    parts = rel.split("/")
    if parts[0] == "tests":
        return True
    return len(parts) == 2 and parts[0] == "scripts" and parts[1].endswith(".py")


def changed_path_allowed(path: str) -> bool:
    """True when a repository-relative changed path is inside the plugin.

    This asked ``in_writable_set`` until the run that invariant scoped had
    landed. GI-007 forbade one change from touching the markdown layers, which
    was a property of that change rather than of the plugin, so measuring every
    later change against it failed the first one whose whole subject was a gate
    the skills describe. Held against a moving baseline it also asked a
    question nobody meant: a change is judged against the previous release, and
    that diff carries whatever the release before it did.

    What survives is the invariant that was always true and never stated on its
    own: a webster change stays inside ``plugins/webster``, apart from the two
    release paths in ``POST_RUN_CORRECTIONS``. Another plugin, a stray report
    directory or a run's own ``evidence/`` logs still fail here.
    ``in_writable_set`` keeps its job in the tracked half above, where it says
    what may *exist* under the plugin.
    """
    return path.startswith(PLUGIN_REL + "/")


def strip_already_made(path: str, head_paths: frozenset[str]) -> bool:
    """True when ``path`` is a run evidence log HEAD no longer carries.

    The sole exception documented at ``RUN_EVIDENCE_PREFIX``: the diff names
    the file because the baseline had it, HEAD does not, and the strip is
    therefore committed. Anything that lands is in HEAD's tree, so it never
    reaches this branch and is judged by ``changed_path_allowed`` like every
    other path.

    ``head_paths`` comes from ``head_tracked_paths`` and not from disk. An
    earlier version asked ``(REPO_ROOT / path).exists()``, which answers a
    question about the working tree that the ``baseline..HEAD`` diff never
    asked: a log committed into HEAD and then removed without committing the
    removal is absent from disk and present in the change, satisfied that
    test, and was waived — so the guard passed on a change carrying a path
    GI-007 does not allow. Under this predicate the two halves agree, and the
    exception cannot admit anything that lands.
    """
    return path.startswith(RUN_EVIDENCE_PREFIX) and path not in head_paths


def resolve_baseline() -> tuple[str | None, str]:
    """The commit this change started from, and a sentence naming how it was found.

    Returns ``(None, reason)`` when neither the recorded baseline nor a
    merge-base against main is reachable, which is what a shallow clone looks
    like. The caller degrades rather than skipping.
    """
    probe = git(
        "rev-parse", "--verify", "--quiet", f"{BASELINE_COMMIT}^{{commit}}", check=False
    )
    if probe.returncode == 0 and probe.stdout.strip():
        return probe.stdout.strip(), f"the recorded baseline commit {BASELINE_COMMIT}"
    for ref in ("origin/main", "main"):
        merge_base = git("merge-base", "HEAD", ref, check=False)
        if merge_base.returncode == 0 and merge_base.stdout.strip():
            return merge_base.stdout.strip(), f"git merge-base HEAD {ref}"
    return None, (
        f"neither {BASELINE_COMMIT} nor a merge-base against origin/main or main "
        f"is reachable from HEAD"
    )


def git_in(repo: Path, *args: str) -> subprocess.CompletedProcess:
    """``git`` inside a built fixture repository rather than this one."""
    proc_env = {key: os.environ[key] for key in ("PATH", "HOME") if key in os.environ}
    proc_env.update(GIT_ENV)
    return subprocess.run(
        ["git", *args],
        cwd=str(repo),
        capture_output=True,
        text=True,
        check=True,
        timeout=GIT_TIMEOUT,
        env=proc_env,
    )


def bare_dated_build(build_repo, dest: Path, tz: str) -> tuple[str, str]:
    """Build the fixture under ``tz`` with its offsets overridden away.

    The control for the timezone test, and it has to run through the real
    ``build_repo`` rather than driving git directly. A control written against
    git proves ``TZ`` reaches *git* and says nothing about whether it reaches
    the *fixture*, which is the thing under test. Measured: with
    ``build_fixture_repo`` mutated to drop its ``env`` argument on the floor, a
    direct-git control passed and carried the timezone test green with it —
    both fixture builds had quietly run in one zone. Written this way the
    mutation is caught here instead.

    ``conftest``'s ``_git`` applies ``extra_env`` after the dates it takes from
    ``FIXTURE_COMMITS``, so ``GIT_AUTHOR_DATE``/``GIT_COMMITTER_DATE`` below
    replace every commit's date with the offset-less ``BARE_DATE``: the same
    builder, the same code path, the one property removed.
    """
    repo = build_repo(
        dest,
        env={
            "GIT_AUTHOR_DATE": BARE_DATE,
            "GIT_COMMITTER_DATE": BARE_DATE,
            "TZ": tz,
        },
    )
    head = git_in(repo, "rev-parse", "HEAD").stdout.strip()
    recorded = git_in(repo, "log", "-1", "--format=%ai").stdout.strip()
    return head, recorded


def outcome(result: subprocess.CompletedProcess) -> str:
    """Both captured streams and the exit code, for an assertion message.

    Same shape as ``test_drift.py``'s helper and mandated by CT-007: a failing
    subprocess assertion has to carry what the subprocess said.
    """
    return (
        f"exit: {result.returncode}\n"
        f"stdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )


def test_run_script_child_python3_clears_the_plugin_floor(run_script, fixture_repo):
    """The interpreter ``run_script`` hands a script is at or above 3.11 (CT-007, FR-029).

    Measured two ways, because the docstring this replaces was wrong about
    which interpreter that is.

    First, directly: the literal ``"python3"`` in ``run_script``'s
    ``subprocess.run`` and the PATH it inherits from ``base_env`` are the only
    two things that decide which interpreter ``run_script`` launches, so
    running that exact pair reports the exact child. ``run_script`` itself cannot be asked — its
    contract takes a script basename under ``scripts/``, not a ``-c`` argument,
    and widening it for one measurement would change a locked contract that
    every module in this suite but ``test_readme.py`` depends on — the seven
    that take the ``run_script`` fixture, this one included. That clause read
    "every other module in this suite depends on" until it was counted;
    ``test_readme.py`` takes neither ``run_script`` nor ``fixture_repo``, as
    its own docstring says.

    Second, through ``run_script`` for real: ``survey.py`` imports
    ``tomllib`` at module scope, and ``tomllib`` is stdlib only from 3.11. A
    child below the floor cannot reach ``survey.py``'s first line of work, so a
    clean run is itself proof the floor holds for the interpreter the harness
    actually uses.
    """
    env = {key: os.environ[key] for key in ("PATH", "HOME") if key in os.environ}
    probe = subprocess.run(
        ["python3", "-c", VERSION_PROBE],
        capture_output=True,
        text=True,
        timeout=GIT_TIMEOUT,
        env=env,
    )
    assert probe.returncode == 0, (
        f"could not measure the child interpreter with the literal 'python3' "
        f"run_script uses.\n{outcome(probe)}"
    )
    version = ast.literal_eval(probe.stdout.strip())
    assert version >= PYTHON_FLOOR, (
        f"run_script's child python3 is {version}, below the plugin's "
        f"{PYTHON_FLOOR} floor (A-001). survey.py imports tomllib at module "
        f"scope and would die on import. PATH's first entry is "
        f"{env.get('PATH', '').split(os.pathsep)[0]!r}.\n{outcome(probe)}"
    )

    result = run_script("survey.py", fixture_repo)
    assert result.returncode == 0, (
        f"survey.py did not run cleanly under the child interpreter "
        f"{version}, so the floor measured above is not the floor the harness "
        f"actually gets. A 'No module named tomllib' here means the child is "
        f"below 3.11.\n{outcome(result)}"
    )


def test_two_fixture_repo_builds_produce_the_same_head(build_repo, tmp_path):
    """Two consecutive fixture builds produce byte-identical history (FR-030, OT-038).

    This is the check ``build_repo`` was added for and then never received.
    What it proves is same-host determinism: two builds, one process, one
    machine, one timezone. The fixture reads no clock of its own, nothing
    outside it reaches the commit objects, and a second build of the same tree
    lands on the same hash — which is the whole of OT-038.

    It is *not* evidence for the explicit ``+00:00`` offset in
    ``conftest.FIXTURE_COMMITS``, and the comment above that constant used to
    cite it as though it were. Both builds here run in whatever zone this
    process is in and agree with or without an offset; the divergence a bare
    date causes is between machines.
    ``test_fixture_head_survives_a_change_of_host_timezone`` is what varies the
    zone and measures that (AC-040).
    """
    first = build_repo(tmp_path / "first")
    second = build_repo(tmp_path / "second")

    heads = []
    for label, repo in (("first", first), ("second", second)):
        result = git_in(repo, "rev-parse", "HEAD")
        heads.append((label, result.stdout.strip()))

    assert heads[0][1] == heads[1][1], (
        f"two consecutive fixture_repo builds disagree on HEAD: "
        f"{heads[0][0]}={heads[0][1]}, {heads[1][0]}={heads[1][1]}. Something "
        f"outside conftest.FIXTURE_COMMITS reached the commit objects — a clock "
        f"read at build time, or a file whose content differs between the two "
        f"copies. Two suspects are ruled out before you start. Not a "
        f"~/.gitconfig arriving through HOME: conftest._GIT_ENV pins "
        f"GIT_CONFIG_GLOBAL to os.devnull and sets GIT_CONFIG_NOSYSTEM on every "
        f"git call, and _git is the only way build_fixture_repo reaches git at "
        f"all, so both files a HOME could offer are shut — measured, a fake "
        f"HOME carrying a .gitconfig that does move HEAD when it is reached "
        f"through GIT_CONFIG_GLOBAL leaves it byte-identical when it arrives "
        f"through HOME. A configuration that did reach these builds would in "
        f"any case reach both of them alike. And not the timezone offset: both "
        f"builds ran in this process's zone, so it cannot be what separates "
        f"them."
    )

    # splitlines(), not split(): one subject is "touch main", and splitting on
    # whitespace would report four commits where there are three.
    subjects = git_in(first, "log", "--format=%s", "--reverse").stdout.splitlines()
    assert len(subjects) == 3, (
        f"the fixture is meant to replay the source repo's three commits "
        f"(init, docs, touch main); got {subjects!r}. A collapsed history "
        f"still gives two identical HEADs, so the equality above cannot catch it."
    )


def test_fixture_head_survives_a_change_of_host_timezone(build_repo, tmp_path):
    """The fixture's HEAD is one hash under two host timezones (FR-030, AC-040).

    AC-040 asks for "deterministic commit hashes across machines", and the
    explicit ``+00:00`` on every date in ``conftest.FIXTURE_COMMITS`` is the
    one thing this test varies and isolates. It is not the only thing
    delivering that determinism, which is what this docstring used to say. The
    author and committer identity pinned in ``conftest._GIT_ENV`` delivers it
    too, and the comment there now says so. Measured one mutation at a time:
    strip the offsets and the two zones below reach two HEADs, which is what
    this test asserts; strip both identity pins with the offsets left in place
    and the fixture reaches one HEAD under either zone, but a different one
    from the pinned build, because git falls back to an identity assembled
    from the machine's OS account and hostname. Nothing had measured either.
    The two-build check above cannot reach the offset: it varies nothing a
    bare date would be read against.

    A machine's timezone is what a bare date is resolved in, so varying ``TZ``
    is what varying the machine amounts to here. It is handed to git through
    ``build_repo``'s ``env`` rather than set on this process, because
    ``conftest``'s ``_git`` passes an explicit environment built from PATH and
    HOME (FR-029) and drops everything else — a ``TZ`` set with ``os.environ``
    and ``time.tzset()`` is not in the child's environment, git falls back to
    ``/etc/localtime``, and both builds run in whatever zone the host is in.
    Measured: exactly that, the commit taking the host's own offset rather than
    the ``+1400`` the parent was holding — ``-0600`` on the machine this was
    measured on, and that host's own offset on any other.

    Which is why the control comes first. A test that varies ``TZ`` and asserts
    two equal hashes passes just as happily when ``TZ`` reaches nothing at all,
    and a check that cannot fail is the false pass this suite exists to stop.
    The control builds the fixture through the same ``build_repo``, under both
    zones, with its offsets overridden away, and requires the two hashes to
    *differ*; only then does the equality below mean anything.
    """
    east_head, east_when = bare_dated_build(build_repo, tmp_path / "bare-east", TZ_EAST)
    west_head, west_when = bare_dated_build(build_repo, tmp_path / "bare-west", TZ_WEST)
    assert east_head != west_head, (
        f"the control did not diverge: the fixture built with every date "
        f"overridden to {BARE_DATE!r}, which carries no offset, reached the "
        f"same HEAD ({east_head}) under TZ={TZ_EAST} and TZ={TZ_WEST}. Either "
        f"build_repo's env is not reaching the git child — in which case the "
        f"assertion below compares two builds in one timezone and can only "
        f"pass — or this machine has no tzdata for these zones. The recorded "
        f"dates tell them apart: {east_when!r} against {west_when!r}, which "
        f"are the FIXTURE_COMMITS times at +0000 if the env was dropped and "
        f"{BARE_DATE} at +0000 if the zones are missing."
    )

    east = build_repo(tmp_path / "east", env={"TZ": TZ_EAST})
    west = build_repo(tmp_path / "west", env={"TZ": TZ_WEST})
    east_fixture = git_in(east, "rev-parse", "HEAD").stdout.strip()
    west_fixture = git_in(west, "rev-parse", "HEAD").stdout.strip()

    assert east_fixture == west_fixture, (
        f"the fixture's HEAD depends on the building machine's timezone: "
        f"TZ={TZ_EAST} gave {east_fixture}, TZ={TZ_WEST} gave {west_fixture}. "
        f"Every date in conftest.FIXTURE_COMMITS must carry an explicit offset "
        f"— the control above measured {east_when!r} against {west_when!r} for "
        f"one bare string. Without it, AC-040's deterministic hashes across "
        f"machines is false and every drift assertion naming a commit becomes "
        f"a local result.\n"
        f"east:\n{git_in(east, 'log', '--format=%H %aI %s', '--reverse').stdout}"
        f"west:\n{git_in(west, 'log', '--format=%H %aI %s', '--reverse').stdout}"
    )


def test_committed_fixture_excludes_git_website_manifest():
    """The committed fixture carries none of GI-005's three exclusions (FR-042, AC-040).

    Walked on the filesystem rather than read out of git, because all three can
    appear without being tracked: a ``.git`` directory left behind by a test
    that built in-place, a ``docs/.webster.json`` written by a ``drift.py
    record`` that ran against the source tree instead of a copy, and a
    ``website/`` written by ``scaffold.py init --site``, whose ``--site-dir``
    defaults to exactly that name. Each makes the next run's results depend on
    the last run's mess, and a pre-recorded manifest in particular would let a
    drift test pass without ``record`` ever being exercised. This passage
    counted two of the three and left ``website/`` out, though a plugin script
    writes it under its own default name — measured in a temp copy, where
    ``scaffold.py init --site`` created five untracked files under it.

    The message on the missing-tree assertion below said "every other module in
    this suite builds its repository from it" until the modules were counted.
    Four do not: ``test_doctype.py`` makes its own empty ``docs_dir`` under
    ``tmp_path``, and ``test_scaffold.py``, ``test_slop.py`` and
    ``test_readme.py`` take no repository fixture at all.
    """
    assert FIXTURE_REPO_SRC.is_dir(), (
        f"the committed fixture tree is missing at {FIXTURE_REPO_SRC}; the "
        f"three modules that take conftest's fixture_repo — test_drift.py, "
        f"test_llmstxt.py and test_survey.py — build their repository from it, "
        f"and so does this one."
    )

    offenders: list[str] = []
    for path in sorted(FIXTURE_REPO_SRC.rglob("*")):
        rel = path.relative_to(FIXTURE_REPO_SRC).as_posix()
        if path.is_dir() and path.name == ".git":
            offenders.append(f"{rel}/ (a nested git repository)")
        elif path.is_dir() and path.name == "website":
            offenders.append(f"{rel}/ (the Docusaurus scaffold A-025 drops)")
        elif path.name == ".webster.json":
            offenders.append(f"{rel} (a pre-recorded drift manifest)")

    assert not offenders, (
        "the committed fixture must contain no .git directory, no website/ and "
        "no .webster.json (GI-005, FR-042, AC-040). Found:\n  "
        + "\n  ".join(offenders)
    )


def test_only_the_writable_file_set_is_tracked_and_changed():
    """Nothing outside GI-007's writable set is tracked or changed (FR-043, OT-043).

    Two halves. The tracked half holds always: every file this repository
    tracks under ``plugins/webster`` is either in the writable five or in one
    of the markdown layers GI-001 froze — so committing, say, a directory of
    generated markdown under ``scripts/`` fails here even though it sits under
    ``scripts/``.

    The changed half needs a baseline to diff against. It allows the plugin
    directory plus the two release paths in ``POST_RUN_CORRECTIONS``, so
    another plugin, a stray report directory, or a run's own ``evidence/`` logs
    all fail here. It used to allow only GI-007's five locations, which was one
    change's scope rather than the plugin's, and it used to allow anything
    under ``evidence/`` outright, which accepted a location no invariant named.

    One tolerance survives, and it can admit nothing: a path the diff reports
    that is under ``evidence/`` and that HEAD does not carry — a strip whose
    deletion is committed (``strip_already_made``). It is asked of HEAD and not
    of the working tree, because a log committed into HEAD and then removed
    without committing the removal is missing from disk while the change still
    carries it, and waiving that path let this guard pass on a change that had
    in fact left its surface. Anything that lands is in HEAD's tree and is
    judged normally; both branches of the exception are asserted below on
    constructed inputs, since whether it can admit a landed path is a property
    of the predicate and asking it of the tree at hand only restates the tree.

    Consequence, stated here rather than skipped: while this run's evidence
    logs are still committed, this half fails and names them. It goes green on
    the lead's strip commit, after which the baseline-to-HEAD diff no longer
    mentions those paths at all. NFR-001 forbids skip and xfail, and a red test
    that says why beats a green one that was taught not to look.
    """
    tracked = nul_separated(git("ls-files", "-z", "--", PLUGIN_REL))
    assert tracked, (
        f"git ls-files reported nothing tracked under {PLUGIN_REL}; this test "
        f"cannot say anything about a plugin git does not know about."
    )

    prefix = PLUGIN_REL + "/"
    stray = sorted(
        rel
        for rel in (path[len(prefix) :] for path in tracked)
        if not in_writable_set(rel) and rel.split("/")[0] not in READ_ONLY_LAYERS
    )
    assert not stray, (
        f"these tracked paths under {PLUGIN_REL} are neither in GI-007's "
        f"writable set (scripts/*.py, .claude-plugin/plugin.json, README.md, "
        f"pyproject.toml, tests/) nor in the read-only markdown layers GI-001 "
        f"names ({', '.join(READ_ONLY_LAYERS)}):\n  " + "\n  ".join(stray)
    )

    baseline, how = resolve_baseline()
    if baseline is None:
        # Not a skip, and not a silent pass: the tracked half above ran and
        # asserted. A shallow clone genuinely cannot answer "what did this
        # change touch", and A-029 forbids dressing that up as a skipped test,
        # so the weaker guarantee stands on its own and names itself here.
        assert tracked, f"tracked-tree scope only: {how}"
        return

    # The commit, not the range. A range from a fixed baseline accumulates every commit that
    # landed meanwhile, so a foundry change pushed by somebody else was reported as a webster
    # change reaching outside its plugin. "A webster change stays inside plugins/webster" is a
    # property of a commit, and on a shared main the commit is what there is to measure.
    changed = nul_separated(git("show", "--name-only", "--pretty=format:", "-z", "HEAD"))
    head_paths = head_tracked_paths()

    # Both branches of the one exception, on inputs written here rather than
    # read off the tree. The first is the branch that went wrong: the log is in
    # HEAD, so the change carries it, and no state of the working tree may
    # excuse that. The second is the branch the exception exists for.
    sample = RUN_EVIDENCE_PREFIX + "casting-0-example.log"
    assert not strip_already_made(sample, frozenset({sample})), (
        f"strip_already_made waived {sample} while HEAD carries it. Removing a "
        f"committed evidence log from the working tree without committing the "
        f"removal would then pass this guard on a change that landed a path "
        f"GI-007 does not allow, which is the one thing it exists to catch."
    )
    assert strip_already_made(sample, frozenset()), (
        f"strip_already_made refused to waive {sample} when HEAD no longer "
        f"carries it. The lead's strip commit is what the exception is for, "
        f"and refusing it leaves this guard red forever once the run's own "
        f"bookkeeping is gone."
    )
    assert not strip_already_made("plugins/other/thing.md", frozenset()), (
        "strip_already_made waived a path outside evidence/. Without the "
        "prefix test every deletion anywhere in the repository is excused, "
        "and the guard stops reading the diff it was given."
    )

    outside = sorted(
        path
        for path in changed
        if not changed_path_allowed(path)
        and not strip_already_made(path, head_paths)
        and path not in POST_RUN_CORRECTIONS
    )
    assert not outside, (
        f"this commit reaches outside the plugin. {len(changed)} paths changed. "
        f"A webster change may touch {PLUGIN_REL}/ and the two release paths in "
        f"POST_RUN_CORRECTIONS, and nothing else "
        f"in the repository. Committed {RUN_EVIDENCE_PREFIX} "
        f"logs from this Foundry run appear here until the lead strips them, "
        f"which is the run's own bookkeeping showing through; every other "
        f"entry is a violation to fix. Outside:\n  "
        + "\n  ".join(outside)
    )
