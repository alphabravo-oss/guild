"""Tests for the harness itself: conftest's helpers, the committed fixture, and
the change surface (US-010; FR-029, FR-030, FR-042, FR-043; CT-007, GI-005,
GI-007; OT-038, OT-043; AC-039, AC-040).

Every other module in this suite tests a webster script. This one tests the
things those modules stand on, because a harness claim nobody measures is the
same false pass ``drift.py`` and ``doctype.py`` are being fixed to stop
reporting — and three such claims had already gone bad:

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
  harness that deliberately has no ``check=``. Citations here now name symbols,
  not line numbers, for every file this change is allowed to edit; a line
  number appears only for a file GI-007 freezes.

Which requirement each test pins, and whether it is a red-first fix test or a
guard over a truth that was asserted but never measured (FR-039 asks for this
distinction to be recorded here rather than enforced at runtime):

======================================================  ==============  ==========================
test                                                    pins            kind
======================================================  ==============  ==========================
test_run_script_child_python3_clears_the_plugin_floor   CT-007 FR-029   guard (docstring was wrong)
test_two_fixture_repo_builds_produce_the_same_head      FR-030 OT-038   guard (build_repo unused)
test_committed_fixture_excludes_git_website_manifest    FR-042 AC-040   guard (GI-005 unmeasured)
test_only_the_writable_file_set_is_tracked_and_changed  FR-043 OT-043   guard (GI-007 unmeasured)
======================================================  ==============  ==========================

None of the four is red-first in the ``AC-039`` sense: they pin no script fix,
so there is no pre-change script they fail against. They are the tests that
would have caught the two harness claims above, and FR-042 / FR-043 were the
only two FR ids in this casting named by no test at all.

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
BASELINE_COMMIT = "cfafe8e"

GIT_TIMEOUT = 30

# Same reasoning as ``conftest.py``'s ``_GIT_ENV`` and ``test_drift.py``'s
# ``GIT_ENV``: a developer's ~/.gitconfig arrives through HOME, and ``status.relativePaths``
# or a pathspec alias would change what these queries return on that machine
# only. Read no configuration but the repository's own.
GIT_ENV = {"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}

# GI-007's writable file set, as a predicate. ``scripts/*.py`` is deliberately
# depth-1 and ``.py``-only rather than ``scripts/**``: A-035 enumerates the
# scripts themselves, not the directory holding them. Partway through this
# change a tree of generated forge-spec markdown sat untracked under
# ``scripts/``, and a recursive glob would have waved the whole tree through
# the moment anyone committed it. The predicate is written against that shape
# and asserts nothing about what the working tree happens to hold at any given
# moment — a guard whose correctness depends on today's directory listing is a
# guard that stops guarding the day the listing changes.
_WRITABLE_FILES = frozenset(
    {"README.md", "pyproject.toml", ".claude-plugin/plugin.json"}
)

# GI-001 makes the markdown layers read-only for this change; it does not make
# them untracked, and they have been in the repository since long before it.
# GI-007 enumerates what may be *written*, not what may exist, so these are the
# tracked paths that are legitimately outside the writable set.
READ_ONLY_LAYERS = ("agents", "commands", "skills")

# The Foundry run's own bookkeeping. Each casting commits its re-executable
# evidence logs under this prefix and the lead strips the directory before the
# change lands, so the paths pass through the branch's history without ever
# being part of the change FR-043 describes.
#
# This is not a hole in ``changed_path_allowed``. That predicate answers GI-007
# and nothing else, and it answers "no" for every path here — an earlier
# version returned "yes" for anything under ``evidence/``, which widened the
# only automated guard behind GI-007 past the five locations it exists to
# enforce. The prefix is used exactly once below, to drop a path the diff still
# reports and the working tree no longer has: a strip that has been made and
# not yet committed. A path that survives to land is on disk and is judged like
# any other, so this exception cannot admit anything. It is the only exception
# in this file.
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

    ``-z`` rather than the newline forms for the reason A-003 switched
    ``drift.py`` to ``--porcelain -z``: git quotes and backslash-escapes any
    path with a space or a non-ASCII byte in the text formats, and a scope
    check that silently mis-reads such a path is a scope check that passes when
    it should not.
    """
    return [path for path in result.stdout.split("\0") if path]


def in_writable_set(rel: str) -> bool:
    """True when ``rel``, relative to ``plugins/webster``, is one of GI-007's five."""
    if rel in _WRITABLE_FILES:
        return True
    parts = rel.split("/")
    if parts[0] == "tests":
        return True
    return len(parts) == 2 and parts[0] == "scripts" and parts[1].endswith(".py")


def changed_path_allowed(path: str) -> bool:
    """True when a repository-relative changed path is one of GI-007's five locations.

    Nothing outside ``plugins/webster`` is allowed, including the run's own
    ``evidence/`` logs. See ``RUN_EVIDENCE_PREFIX`` for why that is not the
    same as failing on them forever.
    """
    prefix = PLUGIN_REL + "/"
    if path.startswith(prefix):
        return in_writable_set(path[len(prefix) :])
    return False


def strip_already_made(path: str) -> bool:
    """True when ``path`` is a run evidence log the working tree no longer holds.

    The sole exception documented at ``RUN_EVIDENCE_PREFIX``: the diff still
    reports the file because the deletion is not committed yet. Anything that
    lands exists on disk, so it never reaches this branch and is judged by
    ``changed_path_allowed`` like every other path.
    """
    return path.startswith(RUN_EVIDENCE_PREFIX) and not (REPO_ROOT / path).exists()


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
    env = {key: os.environ[key] for key in ("PATH", "HOME") if key in os.environ}
    env.update(GIT_ENV)
    return subprocess.run(
        ["git", *args],
        cwd=str(repo),
        capture_output=True,
        text=True,
        check=True,
        timeout=GIT_TIMEOUT,
        env=env,
    )


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
    every other module in this suite depends on.

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
    The property is not decoration: ``GIT_AUTHOR_DATE``/``GIT_COMMITTER_DATE``
    carry an explicit ``+0000`` offset precisely because a bare date picks up
    the host's timezone, and a fixture whose HEAD moves between machines turns
    every drift assertion that names a commit into a machine-local result.
    """
    first = build_repo(tmp_path / "first")
    second = build_repo(tmp_path / "second")

    heads = []
    for label, repo in (("first", first), ("second", second)):
        result = git_in(repo, "rev-parse", "HEAD")
        heads.append((label, result.stdout.strip()))

    assert heads[0][1] == heads[1][1], (
        f"two consecutive fixture_repo builds disagree on HEAD: "
        f"{heads[0][0]}={heads[0][1]}, {heads[1][0]}={heads[1][1]}. The commit "
        f"dates in conftest.FIXTURE_COMMITS must carry an explicit timezone "
        f"offset, and nothing outside the fixture may reach the commit objects."
    )

    # splitlines(), not split(): one subject is "touch main", and splitting on
    # whitespace would report four commits where there are three.
    subjects = git_in(first, "log", "--format=%s", "--reverse").stdout.splitlines()
    assert len(subjects) == 3, (
        f"the fixture is meant to replay the source repo's three commits "
        f"(init, docs, touch main); got {subjects!r}. A collapsed history "
        f"still gives two identical HEADs, so the equality above cannot catch it."
    )


def test_committed_fixture_excludes_git_website_manifest():
    """The committed fixture carries none of GI-005's three exclusions (FR-042, AC-040).

    Walked on the filesystem rather than read out of git, because two of the
    three can appear without being tracked: a ``.git`` directory left behind by
    a test that built in-place, and a ``docs/.webster.json`` written by a
    ``drift.py record`` that ran against the source tree instead of a copy.
    Either one makes the next run's results depend on the last run's mess, and
    a pre-recorded manifest in particular would let a drift test pass without
    ``record`` ever being exercised.
    """
    assert FIXTURE_REPO_SRC.is_dir(), (
        f"the committed fixture tree is missing at {FIXTURE_REPO_SRC}; every "
        f"other module in this suite builds its repository from it."
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

    The changed half needs a baseline to diff against and is the one that
    states FR-043 directly. It allows exactly GI-007's five locations and
    nothing else, so a bumped ``marketplace.json``, an edited root README badge
    (both GI-006 violations), another plugin or a stray report directory all
    fail here. It used to allow anything under ``evidence/`` outright, which
    made the only automated guard behind GI-007 accept a sixth location the
    invariant never named.

    One tolerance survives, and it can admit nothing: a path the diff reports
    that is under ``evidence/`` and is no longer in the working tree — a strip
    made but not yet committed (``strip_already_made``). Anything that lands is
    on disk and is judged normally.

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

    changed = nul_separated(git("diff", "--name-only", "-z", f"{baseline}..HEAD"))
    outside = sorted(
        path
        for path in changed
        if not changed_path_allowed(path) and not strip_already_made(path)
    )
    assert not outside, (
        f"the change touches paths outside its declared surface. Baseline "
        f"resolved from {how} ({baseline[:7]}); {len(changed)} paths changed. "
        f"GI-007 allows only {PLUGIN_REL}/{{scripts/*.py, "
        f".claude-plugin/plugin.json, README.md, pyproject.toml, tests/**}} "
        f"and nothing else in the repository. Committed {RUN_EVIDENCE_PREFIX} "
        f"logs from this Foundry run appear here until the lead strips them, "
        f"which is the run's own bookkeeping showing through; every other "
        f"entry is a violation to fix. Outside:\n  "
        + "\n  ".join(outside)
    )
