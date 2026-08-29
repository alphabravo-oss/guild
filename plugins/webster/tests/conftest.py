"""Pytest harness for the plugins/webster scripts (spec US-010).

Run the suite with:

    cd plugins/webster && uvx pytest

(The machine is PEP 668-managed; do NOT pip-install pytest globally. ``uvx``
runs pytest in an isolated, ephemeral virtualenv. ``plugins/forge/tests/conftest.py``
opens with the same runner and the same PEP 668 warning, but not the same
command line: the one it prints is ``uvx pytest tests/``, while this suite is
run from the plugin root and finds its tests through ``testpaths`` in
``pyproject.toml``. Shared tool and shared reason, not a shared string.)

The literal ``"python3"`` is deliberate, not an oversight (A-024).
``run_script`` shells out to that string — not to ``sys.executable`` — and
passes PATH straight through from the environment pytest inherited, so the
child is whichever ``python3`` *that* PATH resolves. The first version of this
docstring called the consequence a skew: the child "resolves through PATH to
3.14.6" and "must be the one on PATH, not the one running pytest". Measured
inside a real ``uvx pytest`` run, both halves were false; the version that
fixed the first half kept the second for the other launch, where it is false
as well. Both launches have now been measured, the collector's
``sys.executable`` realpath and version against the child's:

- ``cd plugins/webster && uvx pytest`` — uv puts its ephemeral environment's
  ``bin`` at the front of PATH, so the ``python3`` ``run_script`` resolves is
  uv's own CPython and is the interpreter collecting the tests: one realpath,
  both 3.11.14 here;
- ``python3 -m pytest`` — PATH is the shell's, and the ``python3`` the shell
  resolved in order to start pytest is the one ``run_script`` resolves for the
  child: one realpath, both 3.14.6 here. (Measured through a virtualenv built
  on that interpreter, because the PEP 668 constraint above leaves PATH's own
  ``python3`` with no pytest for ``-m`` to find.)

Collector and child agree under both. What differs between the two launches is
which interpreter both of them are, and that is a property of the machine and
of the launch rather than of this suite. So what the harness guarantees is
weaker and checkable: the child clears the plugin's 3.11 floor (A-001),
because ``survey.py`` imports ``tomllib`` at module scope and nothing below
3.11 has it. ``tests/test_harness.py`` measures that rather than pinning a
version number the suite does not control.

Do not strip the venv from PATH to force the second case. Passing PATH through
is CT-007's contract, and the point of the literal ``"python3"`` is to exercise
what a reader exercises: every webster script ships a ``#!/usr/bin/env python3``
shebang and Claude Code invokes it as
``python3 ${CLAUDE_PLUGIN_ROOT}/scripts/X.py``.

Nothing here imports a webster script, and no test module may either (GI-004).
Five of the plugin's seven scripts bind their root, their docs directory or
their allowlists at module import, from argv or ``os.environ``, before a test
could set any of them: ``drift.py``'s ``ROOT`` and ``_DOCS_ARG``;
``llmstxt.py``'s ``ROOT``, ``DOCS`` and ``BASE``; ``doctype.py``'s
``LENS_ALLOW`` and ``SURVEY_PATH``; ``survey.py``'s ``ROOT``; ``slop.py``'s
``TARGETS``. Each of those is a module-level assignment, so an ``import``
freezes all of them at the importing process's values — measured: after this
process chdir'd away, an imported ``drift.py``'s ``ROOT`` still named the
directory the import had run in. What this paragraph used to conclude from
that, "subprocess is the only honest way to drive the script at all", the
measurement does not support: executing the module a second time re-binds
them, and ``runpy.run_path`` drove ``drift.py check`` to a real JSON line and
exit 2 inside this process. What an in-process run of those five costs is a
fresh execution per scenario, and what it skips is the process boundary where
the environment ``run_script`` builds and the interpreter A-024 picked apply
at all. The other two read their arguments inside ``main`` instead —
``scaffold.py`` through ``argparse``, ``rendered.py`` through ``sys.argv`` —
so a test of either can import once and call ``main`` with ``sys.argv`` set:
measured, that returns scaffold.py's ``bad_subject`` JSON and exit 2 with no
subprocess at all. ``run_script`` takes a script basename, so it can drive any
of the seven — handed ``rendered.py`` it runs it, exit 0 and ``0 rendered pages
scanned`` against an empty directory. Of these two the suite calls it on
``scaffold.py`` only, from ``test_scaffold.py``, and calls it there rather than
taking the cheaper in-process shape because one invocation shape across the
suite beats two kinds of test plus a rule about which script gets which, and
the subprocess shape is the one a reader runs. ``rendered.py`` nothing here
drives: there is no ``test_rendered.py``, and no test module passes that name
to ``run_script`` at all. That is a gap in coverage rather than a property of
the helper, and the README's paragraph on what is not yet exercised records the
same absence. This passage used to say ``run_script`` "drives those two the
same way anyway", which was true of one of them. The count is stated because
this paragraph once opened "Every script binds ...", listed five names, and was
read as covering all seven: it was false for exactly the two scripts it did not
name, and one of them, ``scaffold.py``, has a test module sitting beside this
file.

Those are named by symbol and not by ``file:line`` deliberately. An earlier
version of this paragraph cited five line numbers into the scripts, and all
five had moved by the time anyone read them: the same run that fixed the
scripts inserted the 3.11 floor comment above ``import tomllib`` in
``llmstxt.py`` and ``survey.py``, grew ``drift.py``'s module docstring, and
added branches above ``doctype.py``'s allowlists. The ``slop.py`` anchor, off
by a single line, landed on a blank one. A citation that points at a blank line is prose
asserting something untrue, which is the exact failure this suite exists to
stop. A module-level name survives every edit short of deleting the
thing it names, so line numbers appear below only for files outside this
change's writable surface (GI-007), which nothing in this run may move. There
are two of them and no others: ``plugins/forge/tests/conftest.py`` and
``plugins/forge/tests/test_validate_spec.py``. One ``file:line`` string below
does point inside the surface, and it is not a citation:
``src/app/main.py:15`` is the anchor the fixture plants and ``drift.py``
parses back out of ``docs/items/create-item.md``. Its line moving is the
scenario, not a reference gone stale.

Fixtures exposed to test modules:

- ``run_script``  -> ``run_script(name, *args, cwd=None, env=None)`` returning a
  ``subprocess.CompletedProcess`` (CT-007).
- ``fixture_repo`` -> ``Path`` to a git repo built from ``tests/fixtures/repo/``,
  replaying the source fixture's three commits (FR-030).
- ``build_repo``  -> the ``build_fixture_repo`` callable, for the tests that need
  a second independent copy (OT-038's two-consecutive-builds check) or a build
  under a different environment (the timezone pair that measures AC-040).
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Callable

import pytest


# Repo paths are computed once at module import. ``conftest.py`` lives at
# ``plugins/webster/tests/conftest.py`` so the webster plugin root is the parent
# of the tests directory.
PLUGIN_ROOT = Path(__file__).resolve().parent.parent  # plugins/webster/
SCRIPTS_DIR = PLUGIN_ROOT / "scripts"
FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"
FIXTURE_REPO_SRC = FIXTURES_DIR / "repo"

# Matches the explicit ``timeout=30`` on the validator-runner ``subprocess.run``
# in plugins/forge/tests/conftest.py, line 545 — a file outside this change's
# writable surface (GI-007), so unlike the webster scripts it cannot move under
# this citation. Every subprocess in this suite gets an explicit ceiling so a
# script that waits on stdin fails the test instead of hanging the run
# (NFR-004).
SCRIPT_TIMEOUT = 30

# FR-029: the base environment is PATH and HOME, plus whatever a test passes
# explicitly. PATH so ``python3`` and ``git`` resolve, HOME so git can find a
# user identity if a test does not supply one. Nothing else is inherited: a
# developer with WEBSTER_DOCS or WEBSTER_LENS_ALLOW exported in their shell
# would otherwise silently change what the script under test sees, and the
# suite would pass on their machine and fail in review.
_BASE_ENV_KEYS = ("PATH", "HOME")

# The source fixture's three commits, replayed in order. Times are the source
# repo's own clock times restated at +00:00. The offset must be explicit: git
# reads a date that carries none in whatever timezone the committing process is
# in, so one string becomes a different instant, records a different offset
# field, and produces a different commit object — a different hash — on a
# machine set to a different zone. Measured under git 2.50.1: the bare form of
# the first date below commits at +1400 under TZ=Pacific/Kiritimati and at
# -0700 under TZ=America/Los_Angeles, two distinct HEADs; with the offset
# written in, both zones give one.
#
# That is a claim about *other machines*, and the two-consecutive-builds check
# OT-038 asks for cannot reach it: both of those builds run in one process, on
# one host, in one zone, and agree with or without the offset. An earlier
# version of this comment named them as its evidence anyway. What proves this
# sentence is test_harness.py's
# ``test_fixture_head_survives_a_change_of_host_timezone``, which builds the
# fixture under two zones 21 hours apart and asserts one HEAD (AC-040).
FIXTURE_AUTHOR_NAME = "t"
FIXTURE_AUTHOR_EMAIL = "t@t"
TOUCH_MAIN_SUBJECT = "touch main"
FIXTURE_COMMITS = (
    (
        "init",
        "2026-08-27T16:49:56+00:00",
        ("README.md", "pyproject.toml", "src/app/main.py", "src/cli/main.py"),
    ),
    ("docs", "2026-08-27T16:50:40+00:00", ("docs",)),
    (TOUCH_MAIN_SUBJECT, "2026-08-27T16:51:11+00:00", ("src/app/main.py",)),
)

# The third commit prepends this line to src/app/main.py. That one line is what
# pushes the cited line `src/app/main.py:15` off the ``@app.post(...)`` decorator
# that ``docs/items/create-item.md`` anchors to — the drift scenario the whole
# suite is built around. The committed fixture holds the post-touch file, so the
# first commit stages it with this line removed and the third puts it back.
TOUCH_MAIN_MARKER = "# new comment line\n"

# A developer's ~/.gitconfig reaches the fixture repo through HOME, and the
# suite cannot enumerate what it holds. This comment used to name three
# settings and say of them "each one changes HEAD on that machine only". All
# three were wrong. Measured through GIT_CONFIG_GLOBAL against this builder,
# five configurations one at a time: ``core.autocrlf`` at ``true``,
# ``core.autocrlf`` at ``input`` and ``init.templateDir`` each leave HEAD
# byte-identical — this fixture holds no CRLF for autocrlf to rewrite, and a
# template's files are copied into ``.git`` rather than into the work tree;
# ``commit.gpgsign = true`` fails the very first commit ("gpg failed to sign
# the data"), so there is no HEAD to move; and a ``core.excludesFile`` naming
# one fixture page drops it from the ``docs`` commit and does move HEAD. One
# hit in five, and not one of the three that were named, is the argument for
# reading no configuration but the repo's own rather than for listing the
# settings that bite.
#
# The identity is pinned for the same reason the dates carry an offset.
# Measured with both pins stripped — these four variables and the
# ``git config user.*`` calls in ``build_fixture_repo`` — the build still
# succeeds and still reaches one HEAD under either timezone, but a different
# HEAD from the pinned build: git falls back to an identity assembled from the
# machine's OS account and hostname, which is per-machine. AC-040's
# deterministic hashes across machines need this pin as much as the offset.
_GIT_ENV = {
    "GIT_AUTHOR_NAME": FIXTURE_AUTHOR_NAME,
    "GIT_AUTHOR_EMAIL": FIXTURE_AUTHOR_EMAIL,
    "GIT_COMMITTER_NAME": FIXTURE_AUTHOR_NAME,
    "GIT_COMMITTER_EMAIL": FIXTURE_AUTHOR_EMAIL,
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_NOSYSTEM": "1",
}


def base_env() -> dict[str, str]:
    """The minimal environment every subprocess in this suite starts from."""
    return {key: os.environ[key] for key in _BASE_ENV_KEYS if key in os.environ}


def run_script(
    name: str,
    *args: str | Path,
    cwd: str | Path | None = None,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess:
    """Run ``plugins/webster/scripts/<name>`` the way a reader runs it (CT-007).

    ``name`` is the script basename (``"drift.py"``), ``args`` become argv,
    ``cwd`` is the directory the script runs in — which matters, because
    drift.py, llmstxt.py and survey.py all default their root to ``"."`` — and
    ``env`` adds to (or overrides) the minimal base environment.

    Returns the ``subprocess.CompletedProcess`` unexamined: exit code, stdout
    and stderr all belong to the calling test's assertion, which must quote them
    on failure — the shape of the returncode assertion in
    ``plugins/forge/tests/test_validate_spec.py``, lines 43-46, which dumps both
    streams into the message. That is another file this change may not touch.
    """
    script = SCRIPTS_DIR / name
    if not script.is_file():
        available = ", ".join(sorted(p.name for p in SCRIPTS_DIR.glob("*.py")))
        raise FileNotFoundError(
            f"no such webster script: {script}. Available: {available}"
        )
    proc_env = base_env()
    if env:
        proc_env.update({key: str(value) for key, value in env.items()})
    return subprocess.run(
        ["python3", str(script), *(str(a) for a in args)],
        capture_output=True,
        text=True,
        timeout=SCRIPT_TIMEOUT,
        cwd=str(cwd) if cwd is not None else None,
        env=proc_env,
    )


def _git(
    args: list[str],
    *,
    cwd: Path,
    date: str | None = None,
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess:
    """Run one git command inside ``cwd`` with a fixed identity and no user config.

    ``check=True``: a failure here is a broken harness, not a finding, and it
    should surface as an error at fixture setup rather than as a confusing
    assertion failure three lines into a test.

    ``extra_env`` is applied last and may therefore override anything above it.
    See ``build_fixture_repo`` for the one caller that passes it and why the
    environment has to be reached here rather than in the test process.
    """
    env = base_env()
    env.update(_GIT_ENV)
    if date is not None:
        env["GIT_AUTHOR_DATE"] = date
        env["GIT_COMMITTER_DATE"] = date
    if extra_env:
        env.update({key: str(value) for key, value in extra_env.items()})
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
        timeout=SCRIPT_TIMEOUT,
        env=env,
    )


def build_fixture_repo(
    dest: str | Path, *, env: dict[str, str] | None = None
) -> Path:
    """Copy ``tests/fixtures/repo/`` to ``dest`` and replay its three commits.

    The committed tree carries no ``.git``, no ``website/`` and no
    ``docs/.webster.json`` (GI-005), so the history is rebuilt here and any
    manifest a test needs is produced by running ``drift.py record`` through
    ``run_script`` — never shipped pre-recorded.

    ``env`` adds to (or overrides) the environment every git call in this
    function runs under, the way ``run_script``'s ``env`` does for a script. It
    exists for one caller and one reason: the only way to ask what this
    fixture's HEAD would be on a machine in a different timezone is to hand git
    a different ``TZ``, and ``_git`` builds its environment from ``base_env`` —
    PATH and HOME, nothing else (FR-029) — so a ``TZ`` set in the test process
    with ``os.environ`` and ``time.tzset()`` never reaches the child at all.
    Measured: with ``TZ=Pacific/Kiritimati`` exported in the parent and dropped
    by that ``env=``, git dated a bare-date commit at the host's ``-0600``. A
    test written that way would compare two builds in the same zone and report
    a pass.

    Returns the repository root. Two calls produce identical HEAD hashes on
    this machine (OT-038, ``test_two_fixture_repo_builds_produce_the_same_head``)
    and on a machine in any other timezone (AC-040,
    ``test_fixture_head_survives_a_change_of_host_timezone``).
    """
    dest = Path(dest)
    shutil.copytree(FIXTURE_REPO_SRC, dest)

    main_py = dest / "src" / "app" / "main.py"
    committed_main = main_py.read_text(encoding="utf-8")
    if not committed_main.startswith(TOUCH_MAIN_MARKER):
        raise RuntimeError(
            f"{FIXTURE_REPO_SRC}/src/app/main.py no longer starts with "
            f"{TOUCH_MAIN_MARKER!r}. Without that line the '{TOUCH_MAIN_SUBJECT}' "
            f"commit is empty and the anchor src/app/main.py:15 stops drifting, "
            f"which silently deletes the scenario this fixture exists for."
        )
    pre_touch_main = committed_main[len(TOUCH_MAIN_MARKER) :]

    _git(
        ["-c", "init.defaultBranch=main", "init", "-q", "."],
        cwd=dest,
        extra_env=env,
    )
    _git(["config", "user.name", FIXTURE_AUTHOR_NAME], cwd=dest, extra_env=env)
    _git(["config", "user.email", FIXTURE_AUTHOR_EMAIL], cwd=dest, extra_env=env)

    # Commits 1 and 2 see main.py without the marker line; commit 3 restores it
    # and is therefore a real one-line diff, as in the source fixture.
    main_py.write_text(pre_touch_main, encoding="utf-8")
    for subject, date, paths in FIXTURE_COMMITS:
        if subject == TOUCH_MAIN_SUBJECT:
            main_py.write_text(committed_main, encoding="utf-8")
        _git(["add", "--", *paths], cwd=dest, extra_env=env)
        _git(["commit", "-q", "-m", subject], cwd=dest, date=date, extra_env=env)

    return dest


@pytest.fixture(name="run_script")
def run_script_fixture() -> Callable[..., subprocess.CompletedProcess]:
    """The one way a test invokes a webster script (GI-004, CT-007).

    Not "the one subprocess helper for this suite", which is what this line
    used to say and what nothing in the suite supports: ``_git`` above shells
    out, ``build_fixture_repo`` reaches git through it for every commit, and
    ``test_harness.py`` has ``git``, ``git_in`` and one bare ``subprocess.run``
    that measures the child interpreter. Those all drive git or python
    directly; none of them drives a script under test.

    What GI-004 and CT-007 actually constrain is the narrower thing: every
    invocation of a script *under test* goes through this helper, so exactly
    one place decides the interpreter, the environment and the timeout. The
    fixture builders shell out to git because building the repository is the
    ground a test stands on, not the subject it measures.

    There is deliberately no per-script fixture. Seven near-identical wrappers
    would drift apart, and the first one to forget ``env=`` would start reading
    the developer's shell.
    """
    return run_script


@pytest.fixture
def fixture_repo(tmp_path: Path) -> Path:
    """A fresh git repo per test, built from the committed fixture (FR-030).

    Per-test, not session-scoped: tests mutate this tree — editing main.py,
    renaming files, rebasing — and a shared copy would make one test's mutation
    another test's premise (NFR-005).
    """
    return build_fixture_repo(tmp_path / "repo")


@pytest.fixture
def build_repo() -> Callable[..., Path]:
    """Return ``build_fixture_repo`` for tests needing a second, independent copy.

    ``fixture_repo`` can only hand a test one repository, and it hands it over
    already built, so a test cannot vary the environment the build ran in.
    Both matter here: OT-038 asks for two consecutive builds to produce the same
    HEAD, AC-040 asks for the same HEAD on another machine — which is two builds
    under two ``TZ`` values — and a rename or rebase scenario may want a
    pristine reference alongside the mutated copy.
    """
    return build_fixture_repo
