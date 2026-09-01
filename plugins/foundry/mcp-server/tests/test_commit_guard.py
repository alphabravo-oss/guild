"""Real-git tests for the pathspec commit protocol and the shipped commit guard.

Covers AC-015 / OT-005 (pathspec-scoped commits isolate concurrent teammates),
AC-016 (the shipped guard judges staged content only), the installer's
idempotency and ``core.hooksPath`` handling, and a regression lock on the
GI-002 anti-pattern.

Every test builds a REAL throwaway git repository under ``tmp_path`` and runs
the REAL shipped scripts against it. Nothing here is mocked: the properties
under test are properties of git's actual behaviour, and a mock would only
prove that the mock agrees with the test author. Nothing here ever runs git
against the guild repository itself.

The commit-protocol PROSE these mechanics back lives in
``plugins/foundry/agents/teammate.md`` (owned by another casting); this file is
what proves the mechanics it prescribes are real.
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest


# tests/ -> mcp-server/ -> foundry/ -> plugins/ -> repo root.
REPO_ROOT = Path(__file__).resolve().parents[4]
PLUGIN_ROOT = REPO_ROOT / "plugins" / "foundry"
GUARD_SRC = PLUGIN_ROOT / "hooks" / "pre-commit-guard.sh"
INSTALLER = PLUGIN_ROOT / "scripts" / "install-commit-guard.sh"


pytestmark = pytest.mark.skipif(
    shutil.which("git") is None,
    reason="git is not available on PATH; the real-git harness cannot run",
)


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------


def _git_env(tmp_path: Path) -> dict[str, str]:
    """Return an environment that isolates git from the developer's machine.

    Without this, a global ``core.hooksPath``, a system gitconfig, or a global
    hooks directory on the machine running the suite would leak into every
    throwaway repo and could make the installer target somewhere unexpected —
    a false pass or a false failure that depends on who is running the tests.
    """
    fake_home = tmp_path / "fake-home"
    fake_home.mkdir(exist_ok=True)
    env = dict(os.environ)
    env.update(
        {
            "HOME": str(fake_home),
            "GIT_CONFIG_GLOBAL": str(fake_home / "gitconfig"),
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_AUTHOR_NAME": "Test",
            "GIT_AUTHOR_EMAIL": "test@example.com",
            "GIT_COMMITTER_NAME": "Test",
            "GIT_COMMITTER_EMAIL": "test@example.com",
        }
    )
    (fake_home / "gitconfig").write_text("", encoding="utf-8")
    return env


def _git(
    args: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
    check: bool = True,
) -> subprocess.CompletedProcess:
    """Run one git command inside ``cwd``."""
    return subprocess.run(
        ["git"] + args,
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=check,
        timeout=60,
    )


def _run_installer(
    project: Path,
    *extra: str,
    env: dict[str, str],
    check: bool = True,
) -> subprocess.CompletedProcess:
    """Invoke the real shipped installer against ``project``."""
    return subprocess.run(
        ["bash", str(INSTALLER), "--project", str(project), *extra],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=check,
        timeout=60,
        env=env,
    )


def _staged_paths(repo: Path, env: dict[str, str]) -> list[str]:
    """Paths currently staged in ``repo`` (index versus HEAD)."""
    out = _git(["diff", "--cached", "--name-only"], cwd=repo, env=env).stdout
    return [line for line in out.splitlines() if line]


def _tree_paths(repo: Path, env: dict[str, str], rev: str = "HEAD") -> list[str]:
    """Every path recorded in ``rev``'s tree."""
    out = _git(["ls-tree", "-r", "--name-only", rev], cwd=repo, env=env).stdout
    return [line for line in out.splitlines() if line]


def _write(repo: Path, name: str, content: str) -> Path:
    target = repo / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return target


# A merge-conflict marker assembled at runtime rather than written literally.
# The guard only matches these at the START of a line, so an indented literal
# in this file would be harmless — but building them keeps this test file safe
# to commit through the very guard it is testing, under any future tightening.
CONFLICT_BLOCK = (
    ("<" * 7) + " HEAD\nours\n" + ("=" * 7) + "\ntheirs\n" + (">" * 7) + " branch\n"
)


@pytest.fixture
def env(tmp_path: Path) -> dict[str, str]:
    return _git_env(tmp_path)


@pytest.fixture
def repo(tmp_path: Path, env: dict[str, str]) -> Path:
    """A real git repo with one base commit."""
    work = tmp_path / "repo"
    work.mkdir()
    _git(["init", "-q", "-b", "main", "."], cwd=work, env=env)
    _write(work, "base.txt", "base\n")
    _git(["add", "base.txt"], cwd=work, env=env)
    _git(["commit", "-q", "-m", "base"], cwd=work, env=env)
    return work


@pytest.fixture
def guarded_repo(repo: Path, env: dict[str, str]) -> Path:
    """A real git repo with the shipped guard installed."""
    _run_installer(repo, env=env)
    return repo


# ---------------------------------------------------------------------------
# AC-015 / OT-005 — pathspec-scoped commits isolate concurrent teammates
# ---------------------------------------------------------------------------


def test_pathspec_commit_records_only_its_own_paths(repo: Path, env: dict[str, str]):
    """Two agents stage into one index; A's pathspec commit records only A's."""
    _write(repo, "agent_a_one.txt", "A1\n")
    _write(repo, "agent_a_two.txt", "A2\n")
    _write(repo, "agent_b_one.txt", "B1\n")
    _git(
        ["add", "agent_a_one.txt", "agent_a_two.txt", "agent_b_one.txt"],
        cwd=repo,
        env=env,
    )
    # Precondition: all three really are in one shared index.
    assert set(_staged_paths(repo, env)) == {
        "agent_a_one.txt",
        "agent_a_two.txt",
        "agent_b_one.txt",
    }

    _git(
        ["commit", "-q", "-m", "A", "--", "agent_a_one.txt", "agent_a_two.txt"],
        cwd=repo,
        env=env,
    )

    recorded = _tree_paths(repo, env)
    assert "agent_a_one.txt" in recorded
    assert "agent_a_two.txt" in recorded
    assert "agent_b_one.txt" not in recorded, (
        "AC-015 violated: the pathspec commit captured a peer's staged file"
    )


def test_pathspec_commit_leaves_peer_files_staged(repo: Path, env: dict[str, str]):
    """B's staged files are held back, not lost — still staged for B's commit."""
    _write(repo, "agent_a_one.txt", "A1\n")
    _write(repo, "agent_b_one.txt", "B1\n")
    _git(["add", "agent_a_one.txt", "agent_b_one.txt"], cwd=repo, env=env)

    _git(["commit", "-q", "-m", "A", "--", "agent_a_one.txt"], cwd=repo, env=env)

    assert _staged_paths(repo, env) == ["agent_b_one.txt"], (
        "AC-015 violated: the peer's staged file did not survive A's commit"
    )

    # And B can still commit it afterwards, unharmed.
    _git(["commit", "-q", "-m", "B", "--", "agent_b_one.txt"], cwd=repo, env=env)
    assert "agent_b_one.txt" in _tree_paths(repo, env)
    assert _staged_paths(repo, env) == []


def test_bare_commit_captures_peer_files(repo: Path, env: dict[str, str]):
    """The contrast case that motivates the protocol.

    A pathspec-less ``git commit`` records the ENTIRE index by git's documented
    default, so it swallows the peer's staged work. This test exists so the
    protocol's reason is executable rather than folklore: if git ever stopped
    behaving this way, the pathspec rule could be revisited.
    """
    _write(repo, "agent_a_one.txt", "A1\n")
    _write(repo, "agent_b_one.txt", "B1\n")
    _git(["add", "agent_a_one.txt", "agent_b_one.txt"], cwd=repo, env=env)

    _git(["commit", "-q", "-m", "bare"], cwd=repo, env=env)

    recorded = _tree_paths(repo, env)
    assert "agent_b_one.txt" in recorded, (
        "expected the documented whole-index behaviour of a bare commit"
    )
    assert _staged_paths(repo, env) == []


# ---------------------------------------------------------------------------
# AC-016 — the shipped guard judges staged content only
# ---------------------------------------------------------------------------


def test_guard_allows_clean_staged_content(guarded_repo: Path, env: dict[str, str]):
    """Sanity: the guard does not block an ordinary commit."""
    _write(guarded_repo, "clean.txt", "nothing wrong here\n")
    _git(["add", "clean.txt"], cwd=guarded_repo, env=env)
    result = _git(
        ["commit", "-m", "clean", "--", "clean.txt"],
        cwd=guarded_repo,
        env=env,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "clean.txt" in _tree_paths(guarded_repo, env)


def test_guard_ignores_peer_untracked_working_tree_file(
    guarded_repo: Path, env: dict[str, str]
):
    """A peer's untracked WIP carrying markers must not fire the guard."""
    _write(guarded_repo, "peer_wip.txt", CONFLICT_BLOCK)  # never staged
    _write(guarded_repo, "mine.txt", "fine\n")
    _git(["add", "mine.txt"], cwd=guarded_repo, env=env)

    result = _git(
        ["commit", "-m", "mine", "--", "mine.txt"],
        cwd=guarded_repo,
        env=env,
        check=False,
    )
    assert result.returncode == 0, (
        "AC-016 violated: a peer's unstaged file fired the guard\n" + result.stderr
    )


def test_guard_ignores_peer_unstaged_edit_to_tracked_file(
    guarded_repo: Path, env: dict[str, str]
):
    """The sharper case: markers in a TRACKED file, edited but not staged.

    ``git diff --name-only HEAD`` — the GI-002 anti-pattern — WOULD see this,
    because the working tree differs from HEAD. ``git diff --cached`` does not.
    This is the test that actually distinguishes the two queries.
    """
    _write(guarded_repo, "shared.txt", "original\n")
    _git(["add", "shared.txt"], cwd=guarded_repo, env=env)
    _git(["commit", "-q", "-m", "add shared"], cwd=guarded_repo, env=env)

    # Peer edits the tracked file in the shared tree and does NOT stage it.
    _write(guarded_repo, "shared.txt", CONFLICT_BLOCK)

    _write(guarded_repo, "mine.txt", "fine\n")
    _git(["add", "mine.txt"], cwd=guarded_repo, env=env)
    result = _git(
        ["commit", "-m", "mine", "--", "mine.txt"],
        cwd=guarded_repo,
        env=env,
        check=False,
    )
    assert result.returncode == 0, (
        "AC-016 violated: an unstaged edit to a tracked file fired the guard "
        "— the guard is reading the working tree\n" + result.stderr
    )


def test_guard_ignores_peer_staged_file_outside_the_pathspec(
    guarded_repo: Path, env: dict[str, str]
):
    """Even a peer's STAGED violation is invisible to a pathspec commit.

    git builds a temporary index for a partial commit and points
    ``GIT_INDEX_FILE`` at it, so the hook's ``git diff --cached`` reports only
    the paths this commit will record.
    """
    _write(guarded_repo, "peer_staged.txt", CONFLICT_BLOCK)
    _write(guarded_repo, "mine.txt", "fine\n")
    _git(["add", "peer_staged.txt", "mine.txt"], cwd=guarded_repo, env=env)

    result = _git(
        ["commit", "-m", "mine", "--", "mine.txt"],
        cwd=guarded_repo,
        env=env,
        check=False,
    )
    assert result.returncode == 0, (
        "a peer's staged file outside this commit's pathspec fired the guard\n"
        + result.stderr
    )
    assert _staged_paths(guarded_repo, env) == ["peer_staged.txt"]


def test_guard_reads_content_from_the_index_not_the_working_tree(
    guarded_repo: Path, env: dict[str, str]
):
    """Staged content clean, working-tree content dirty — must NOT block.

    This pins the CONTENT source, which the path-list tests above cannot: they
    only pin which paths are judged. Here the offending path IS under
    judgement, and only the question "whose bytes?" decides the outcome. A
    guard that grepped the file on disk instead of the blob at ``:0:<path>``
    fires here; one that reads the index does not.

    A bare commit is used deliberately: the guard ships to arbitrary repos
    where bare commits are ordinary, and it must be correct there too — not
    merely correct because a pathspec narrowed the temp index for it.
    """
    _write(guarded_repo, "staged_clean.txt", "perfectly fine\n")
    _git(["add", "staged_clean.txt"], cwd=guarded_repo, env=env)
    # Now dirty the WORKING TREE copy, leaving the staged blob clean.
    _write(guarded_repo, "staged_clean.txt", CONFLICT_BLOCK)

    result = _git(["commit", "-m", "bare"], cwd=guarded_repo, env=env, check=False)

    assert result.returncode == 0, (
        "AC-016 violated: the guard judged working-tree bytes instead of the "
        "staged blob\n" + result.stdout + result.stderr
    )
    # What landed is the clean staged version, confirming the setup was real.
    blob = _git(
        ["show", "HEAD:staged_clean.txt"], cwd=guarded_repo, env=env
    ).stdout
    assert "perfectly fine" in blob


def test_guard_blocks_when_only_the_staged_blob_is_dirty(
    guarded_repo: Path, env: dict[str, str]
):
    """The inverse: staged content dirty, working tree clean — must block.

    Together with the test above this pins the content source from both
    directions. A working-tree reader sees a clean file here and waves the
    commit through, recording conflict markers into history.
    """
    _write(guarded_repo, "sneaky.txt", CONFLICT_BLOCK)
    _git(["add", "sneaky.txt"], cwd=guarded_repo, env=env)
    # Tidy the working tree afterwards; the index still holds the markers.
    _write(guarded_repo, "sneaky.txt", "looks clean on disk\n")

    result = _git(["commit", "-m", "bare"], cwd=guarded_repo, env=env, check=False)

    assert result.returncode != 0, (
        "AC-016 violated: the guard missed a violation that exists only in the "
        "index — it is reading the working tree"
    )
    assert "sneaky.txt" in result.stdout + result.stderr


def test_guard_blocks_staged_conflict_markers(
    guarded_repo: Path, env: dict[str, str]
):
    """Staged content that fails the check is refused, with a stated reason."""
    _write(guarded_repo, "conflicted.txt", CONFLICT_BLOCK)
    _git(["add", "conflicted.txt"], cwd=guarded_repo, env=env)
    head_before = _git(["rev-parse", "HEAD"], cwd=guarded_repo, env=env).stdout

    result = _git(
        ["commit", "-m", "bad", "--", "conflicted.txt"],
        cwd=guarded_repo,
        env=env,
        check=False,
    )

    assert result.returncode != 0, "the guard failed to block a staged violation"
    combined = result.stdout + result.stderr
    assert "conflicted.txt" in combined, (
        "a blocked commit must name the offending path; a silent block is a defect"
    )
    assert "foundry-guard" in combined
    # Nothing landed.
    head_after = _git(["rev-parse", "HEAD"], cwd=guarded_repo, env=env).stdout
    assert head_before == head_after


def test_guard_blocks_oversize_staged_blob(guarded_repo: Path, env: dict[str, str]):
    """The size check reads the staged blob and reports why it blocked."""
    _write(guarded_repo, "big.txt", "x" * 300)
    _git(["add", "big.txt"], cwd=guarded_repo, env=env)

    tight = dict(env)
    tight["FOUNDRY_GUARD_MAX_FILE_SIZE"] = "64"
    result = _git(
        ["commit", "-m", "big", "--", "big.txt"],
        cwd=guarded_repo,
        env=tight,
        check=False,
    )

    assert result.returncode != 0
    combined = result.stdout + result.stderr
    assert "big.txt" in combined
    assert "limit" in combined

    # The same content passes under the shipped default, so the check is the
    # threshold and not an accident of the file existing.
    ok = _git(
        ["commit", "-m", "big", "--", "big.txt"],
        cwd=guarded_repo,
        env=env,
        check=False,
    )
    assert ok.returncode == 0, ok.stderr


def test_guard_allows_first_commit_on_unborn_branch(
    tmp_path: Path, env: dict[str, str]
):
    """A repo with no HEAD yet must not crash the guard (empty-tree fallback)."""
    work = tmp_path / "fresh"
    work.mkdir()
    _git(["init", "-q", "-b", "main", "."], cwd=work, env=env)
    _run_installer(work, env=env)

    _write(work, "first.txt", "hello\n")
    _git(["add", "first.txt"], cwd=work, env=env)
    result = _git(
        ["commit", "-m", "first", "--", "first.txt"], cwd=work, env=env, check=False
    )
    assert result.returncode == 0, result.stdout + result.stderr

    # And it still blocks on an unborn branch rather than failing open.
    work2 = tmp_path / "fresh2"
    work2.mkdir()
    _git(["init", "-q", "-b", "main", "."], cwd=work2, env=env)
    _run_installer(work2, env=env)
    _write(work2, "bad.txt", CONFLICT_BLOCK)
    _git(["add", "bad.txt"], cwd=work2, env=env)
    blocked = _git(
        ["commit", "-m", "bad", "--", "bad.txt"], cwd=work2, env=env, check=False
    )
    assert blocked.returncode != 0


# ---------------------------------------------------------------------------
# Installer
# ---------------------------------------------------------------------------


def _hook_dir(repo: Path, env: dict[str, str]) -> Path:
    raw = _git(["rev-parse", "--git-path", "hooks"], cwd=repo, env=env).stdout.strip()
    path = Path(raw)
    return path if path.is_absolute() else repo / path


def test_installer_is_idempotent(repo: Path, env: dict[str, str]):
    """Two runs leave exactly one working, executable guard, and say so."""
    first = _run_installer(repo, env=env)
    assert "installed" in first.stdout

    second = _run_installer(repo, env=env)
    assert "already installed and current" in second.stdout, second.stdout

    hooks = _hook_dir(repo, env)
    hook = hooks / "pre-commit"

    # Exactly one guard: no duplicate, no sibling copy, no self-append.
    installed = [
        p.name
        for p in hooks.iterdir()
        if p.name.startswith("pre-commit") and not p.name.endswith(".sample")
    ]
    assert installed == ["pre-commit"], installed
    assert hook.read_bytes() == GUARD_SRC.read_bytes(), (
        "the second run altered the installed hook"
    )
    assert os.access(hook, os.X_OK)

    # And it still functions after the second run.
    _write(repo, "conflicted.txt", CONFLICT_BLOCK)
    _git(["add", "conflicted.txt"], cwd=repo, env=env)
    result = _git(
        ["commit", "-m", "bad", "--", "conflicted.txt"],
        cwd=repo,
        env=env,
        check=False,
    )
    assert result.returncode != 0, "the guard stopped working after re-installation"


def test_installer_restores_a_modified_copy(repo: Path, env: dict[str, str]):
    """Re-running after the shipped asset changes updates the installed copy."""
    _run_installer(repo, env=env)
    hook = _hook_dir(repo, env) / "pre-commit"

    # Simulate an installed copy that has drifted from the shipped asset while
    # still carrying the marker (i.e. an older shipped version).
    hook.write_text(
        hook.read_text(encoding="utf-8") + "\n# drifted\n", encoding="utf-8"
    )
    assert hook.read_bytes() != GUARD_SRC.read_bytes()

    result = _run_installer(repo, env=env)
    assert "updated" in result.stdout, result.stdout
    assert hook.read_bytes() == GUARD_SRC.read_bytes()
    assert os.access(hook, os.X_OK)


def test_installer_restores_lost_executable_bit(repo: Path, env: dict[str, str]):
    """A present-but-not-executable hook is silently skipped by git."""
    _run_installer(repo, env=env)
    hook = _hook_dir(repo, env) / "pre-commit"
    hook.chmod(0o644)
    assert not os.access(hook, os.X_OK)

    _run_installer(repo, env=env)
    assert os.access(hook, os.X_OK), (
        "the installer reported success over a hook git will never run"
    )


def test_installer_honors_core_hookspath(tmp_path: Path, env: dict[str, str]):
    """A repo with core.hooksPath must get a hook that actually runs."""
    work = tmp_path / "custom"
    work.mkdir()
    _git(["init", "-q", "-b", "main", "."], cwd=work, env=env)
    _git(["config", "core.hooksPath", "myhooks"], cwd=work, env=env)
    _write(work, "base.txt", "base\n")
    _git(["add", "base.txt"], cwd=work, env=env)
    _git(["commit", "-q", "-m", "base"], cwd=work, env=env)

    result = _run_installer(work, env=env)
    assert "core.hooksPath" in result.stdout

    assert (work / "myhooks" / "pre-commit").is_file()
    assert os.access(work / "myhooks" / "pre-commit", os.X_OK)
    assert not (work / ".git" / "hooks" / "pre-commit").exists(), (
        "installed into .git/hooks, which git will never run for this repo"
    )

    # Prove it is live, not merely present.
    _write(work, "conflicted.txt", CONFLICT_BLOCK)
    _git(["add", "conflicted.txt"], cwd=work, env=env)
    blocked = _git(
        ["commit", "-m", "bad", "--", "conflicted.txt"],
        cwd=work,
        env=env,
        check=False,
    )
    assert blocked.returncode != 0
    assert "foundry-guard" in blocked.stdout + blocked.stderr

    # Idempotency holds on this path too.
    again = _run_installer(work, env=env)
    assert "already installed and current" in again.stdout


def test_installer_backs_up_a_foreign_hook(repo: Path, env: dict[str, str]):
    """An unrelated pre-commit hook is never destroyed without a word."""
    hooks = _hook_dir(repo, env)
    hooks.mkdir(parents=True, exist_ok=True)
    foreign = hooks / "pre-commit"
    foreign_body = "#!/bin/sh\necho 'the repo owns this hook'\n"
    foreign.write_text(foreign_body, encoding="utf-8")
    foreign.chmod(0o755)

    result = _run_installer(repo, env=env)
    assert "REPLACED" in result.stdout

    backups = [p for p in hooks.iterdir() if ".foundry-backup." in p.name]
    assert len(backups) == 1, [p.name for p in hooks.iterdir()]
    assert backups[0].read_text(encoding="utf-8") == foreign_body, (
        "the backup does not contain the original hook"
    )
    assert foreign.read_bytes() == GUARD_SRC.read_bytes()


def test_installer_no_clobber_refuses_foreign_hook(repo: Path, env: dict[str, str]):
    """--no-clobber turns the replacement into a reported refusal."""
    hooks = _hook_dir(repo, env)
    hooks.mkdir(parents=True, exist_ok=True)
    foreign = hooks / "pre-commit"
    foreign_body = "#!/bin/sh\necho 'the repo owns this hook'\n"
    foreign.write_text(foreign_body, encoding="utf-8")
    foreign.chmod(0o755)

    result = _run_installer(repo, "--no-clobber", env=env, check=False)
    assert result.returncode != 0
    assert "no-clobber" in result.stderr
    assert foreign.read_text(encoding="utf-8") == foreign_body, (
        "--no-clobber modified the existing hook anyway"
    )


def test_installer_fails_on_non_git_target(tmp_path: Path, env: dict[str, str]):
    """A non-repository target exits non-zero with a named cause."""
    plain = tmp_path / "not-a-repo"
    plain.mkdir()
    result = _run_installer(plain, env=env, check=False)
    assert result.returncode != 0
    assert "Not a git repository" in result.stderr


def test_installer_fails_on_missing_target(tmp_path: Path, env: dict[str, str]):
    result = _run_installer(tmp_path / "nope", env=env, check=False)
    assert result.returncode != 0
    assert "does not exist" in result.stderr


# ---------------------------------------------------------------------------
# GI-002 regression lock — the anti-pattern must never come back
# ---------------------------------------------------------------------------


def _executable_lines(source: str) -> list[str]:
    """Source lines with comments and blanks removed.

    The guard's own header DISCUSSES the anti-pattern by name so that a future
    reader knows what never to reintroduce, so a naive substring scan over the
    whole file would flag the documentation. Only what the shell actually
    executes is scanned.
    """
    kept = []
    for line in source.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        kept.append(line)
    return kept


def test_shipped_assets_exist_and_are_executable():
    for asset in (GUARD_SRC, INSTALLER):
        assert asset.is_file(), f"shipped asset missing: {asset}"
        assert asset.stat().st_mode & stat.S_IXUSR, (
            f"{asset} is not executable; git silently skips a non-executable hook"
        )


def test_guard_never_queries_the_working_tree():
    """GI-002: the guard must judge the index and only the index."""
    code = "\n".join(_executable_lines(GUARD_SRC.read_text(encoding="utf-8")))

    forbidden = [
        "diff --name-only HEAD",  # the named GI-002 anti-pattern
        "diff HEAD",
        "git stash",
        "diff-index HEAD",
    ]
    for pattern in forbidden:
        assert pattern not in code, (
            f"GI-002 violated: the guard executes {pattern!r}, which can observe "
            "a peer's unstaged working-tree content"
        )

    # Every `git diff` the guard runs must be an index query.
    for line in _executable_lines(GUARD_SRC.read_text(encoding="utf-8")):
        if "git diff" in line:
            assert "--cached" in line, f"non-cached git diff in guard: {line.strip()}"


def test_guard_actually_uses_the_cached_query():
    """A guard that reads nothing would pass the negative test above."""
    code = "\n".join(_executable_lines(GUARD_SRC.read_text(encoding="utf-8")))
    assert "diff --cached" in code
    assert "cat-file" in code, "the guard should read staged blobs from the index"


def test_installer_never_stashes_or_reads_the_working_tree():
    code = "\n".join(_executable_lines(INSTALLER.read_text(encoding="utf-8")))
    assert "git stash" not in code
    assert "diff --name-only HEAD" not in code


def test_installer_copies_rather_than_symlinks():
    """The plugin cache is version-namespaced; a symlink goes stale on update."""
    code = "\n".join(_executable_lines(INSTALLER.read_text(encoding="utf-8")))
    assert "ln -s" not in code, (
        "a symlink into the version-namespaced plugin cache breaks on update"
    )
    assert "cp " in code
