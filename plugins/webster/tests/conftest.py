"""Pytest harness for the plugins/webster scripts (spec US-010).

Run the suite with:

    cd plugins/webster && uvx pytest

(The machine is PEP 668-managed; do NOT pip-install pytest globally. ``uvx``
runs pytest in an isolated, ephemeral virtualenv — the same invocation
``plugins/forge/tests/conftest.py`` documents.)

Interpreter skew is deliberate, not an oversight, but it is not the skew an
earlier version of this docstring claimed. ``run_script`` shells out to the
literal string ``"python3"`` and passes PATH straight through from the
environment pytest inherited, so the child is whichever ``python3`` *that* PATH
resolves — never ``sys.executable`` (A-024). Which interpreter that turns out
to be depends on how the suite was launched, and both answers are correct:

- under the blessed ``cd plugins/webster && uvx pytest``, uv prepends its
  ephemeral virtualenv's ``bin`` to PATH, so the child is uv's own CPython and
  is therefore the *same* interpreter running pytest — measured at 3.11.14 here;
- under a plain ``python3 -m pytest``, PATH is the shell's, so the child is the
  shell's ``python3`` — Homebrew's 3.14.6 on this machine, and a different
  interpreter from the one collecting the tests.

This docstring previously asserted the second case unconditionally ("resolves
through PATH to 3.14.6", "not the one running pytest"). Measured inside a real
``uvx pytest`` run, both halves were false. What the harness actually
guarantees is weaker and checkable: the child clears the plugin's 3.11 floor
(A-001), because ``survey.py:11`` imports ``tomllib`` at module scope and
nothing below 3.11 has it. ``tests/test_harness.py`` measures that rather than
pinning a version number that is a property of the machine, not of the suite.

Do not strip the venv from PATH to force the second case. Passing PATH through
is CT-007's contract, and the point of the literal ``"python3"`` is to exercise
what a reader exercises: every webster script ships a ``#!/usr/bin/env python3``
shebang and Claude Code invokes it as
``python3 ${CLAUDE_PLUGIN_ROOT}/scripts/X.py``.

Nothing here imports a webster script, and no test module may either (GI-004).
Every script parses argv and ``os.environ`` at module import — ``drift.py:16-17``,
``llmstxt.py:8-10``, ``doctype.py:167``, ``survey.py:9``, ``slop.py:11`` — so an
import would freeze the wrong root, the wrong docs directory and the wrong lens
allowlist before a test could set any of them. Subprocess is the only honest way
to drive these scripts, and it is also how a reader runs them.

Fixtures exposed to test modules:

- ``run_script``  -> ``run_script(name, *args, cwd=None, env=None)`` returning a
  ``subprocess.CompletedProcess`` (CT-007).
- ``fixture_repo`` -> ``Path`` to a git repo built from ``tests/fixtures/repo/``,
  replaying the source fixture's three commits (FR-030).
- ``build_repo``  -> the ``build_fixture_repo`` callable, for the tests that need
  a second independent copy (OT-038's two-consecutive-builds check).
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

# Matches plugins/forge/tests/conftest.py:545. Every subprocess in this suite
# gets an explicit ceiling so a script that waits on stdin fails the test
# instead of hanging the run (NFR-004).
SCRIPT_TIMEOUT = 30

# FR-029: the base environment is PATH and HOME, plus whatever a test passes
# explicitly. PATH so ``python3`` and ``git`` resolve, HOME so git can find a
# user identity if a test does not supply one. Nothing else is inherited: a
# developer with WEBSTER_DOCS or WEBSTER_LENS_ALLOW exported in their shell
# would otherwise silently change what the script under test sees, and the
# suite would pass on their machine and fail in review.
_BASE_ENV_KEYS = ("PATH", "HOME")

# The source fixture's three commits, replayed in order. Times are the source
# repo's own clock times restated at +0000. The offset must be explicit: a bare
# date string picks up the host's timezone, which changes the commit object and
# therefore the hash, which is exactly what OT-038 asks two consecutive builds
# to prove cannot happen.
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
# that ``docs/items/create-item.md:12`` anchors to — the drift scenario the whole
# suite is built around. The committed fixture holds the post-touch file, so the
# first commit stages it with this line removed and the third puts it back.
TOUCH_MAIN_MARKER = "# new comment line\n"

# A developer's ~/.gitconfig reaches the fixture repo through HOME.
# ``commit.gpgsign`` would block the commit, ``core.autocrlf`` would rewrite the
# blobs, and ``init.templateDir`` would add files to the tree — each one changes
# HEAD on that machine only. Read no configuration but the repo's own.
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
    on failure (see plugins/forge/tests/test_validate_spec.py:43-46).
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
) -> subprocess.CompletedProcess:
    """Run one git command inside ``cwd`` with a fixed identity and no user config.

    ``check=True``: a failure here is a broken harness, not a finding, and it
    should surface as an error at fixture setup rather than as a confusing
    assertion failure three lines into a test.
    """
    env = base_env()
    env.update(_GIT_ENV)
    if date is not None:
        env["GIT_AUTHOR_DATE"] = date
        env["GIT_COMMITTER_DATE"] = date
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


def build_fixture_repo(dest: str | Path) -> Path:
    """Copy ``tests/fixtures/repo/`` to ``dest`` and replay its three commits.

    The committed tree carries no ``.git``, no ``website/`` and no
    ``docs/.webster.json`` (GI-005), so the history is rebuilt here and any
    manifest a test needs is produced by running ``drift.py record`` through
    ``run_script`` — never shipped pre-recorded.

    Returns the repository root. Two calls produce identical HEAD hashes, on
    this machine and on any other (OT-038).
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

    _git(["-c", "init.defaultBranch=main", "init", "-q", "."], cwd=dest)
    _git(["config", "user.name", FIXTURE_AUTHOR_NAME], cwd=dest)
    _git(["config", "user.email", FIXTURE_AUTHOR_EMAIL], cwd=dest)

    # Commits 1 and 2 see main.py without the marker line; commit 3 restores it
    # and is therefore a real one-line diff, as in the source fixture.
    main_py.write_text(pre_touch_main, encoding="utf-8")
    for subject, date, paths in FIXTURE_COMMITS:
        if subject == TOUCH_MAIN_SUBJECT:
            main_py.write_text(committed_main, encoding="utf-8")
        _git(["add", "--", *paths], cwd=dest)
        _git(["commit", "-q", "-m", subject], cwd=dest, date=date)

    return dest


@pytest.fixture(name="run_script")
def run_script_fixture() -> Callable[..., subprocess.CompletedProcess]:
    """The one subprocess helper for this suite (GI-004, CT-007).

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
def build_repo() -> Callable[[str | Path], Path]:
    """Return ``build_fixture_repo`` for tests needing a second, independent copy.

    ``fixture_repo`` can only hand a test one repository. OT-038 asks for two
    consecutive builds to produce the same HEAD, and a rename or rebase scenario
    may want a pristine reference alongside the mutated copy.
    """
    return build_fixture_repo
