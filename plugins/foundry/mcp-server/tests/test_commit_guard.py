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
import re
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


@pytest.mark.parametrize("payload_bytes", [1_500_000, 4_200_000])
def test_guard_blocks_conflict_markers_in_a_large_staged_blob(
    guarded_repo: Path, env: dict[str, str], payload_bytes: int
):
    """A violation must block at ANY blob size (D-014 regression lock).

    The obvious spelling of the marker check —

        git cat-file blob ":0:$path" | grep -q PATTERN

    fails OPEN above roughly the pipe-buffer size. ``grep -q`` exits 0 the
    instant it matches and closes the pipe; ``git cat-file`` is still writing,
    takes SIGPIPE and dies 141; ``set -o pipefail`` reports the whole pipeline
    as 141; and the enclosing ``if`` reads that nonzero status as "no match".
    The bigger the violation, the more certainly it was waved through. This was
    not theoretical: a 4.2 MB file with a marker on line 1 committed cleanly.

    Both sizes are exercised on purpose. 1.5 MB is comfortably past the 64 KiB
    pipe buffer where the race first appears; 4.2 MB is the size PROVE actually
    demonstrated committing. A fix that only enlarges a buffer passes the
    small case and fails the large one.
    """
    filler = "a" * 4096 + "\n"
    body = filler * (payload_bytes // len(filler))
    # Marker on line 1, so a matcher that stops at the first hit stops almost
    # immediately — with megabytes still unread behind it. That gap is the bug.
    _write(guarded_repo, "big_conflict.txt", CONFLICT_BLOCK + body)
    _git(["add", "big_conflict.txt"], cwd=guarded_repo, env=env)
    head_before = _git(["rev-parse", "HEAD"], cwd=guarded_repo, env=env).stdout

    result = _git(
        ["commit", "-m", "big", "--", "big_conflict.txt"],
        cwd=guarded_repo,
        env=env,
        check=False,
    )

    combined = result.stdout + result.stderr
    assert result.returncode != 0, (
        f"FAIL-OPEN: a {payload_bytes}-byte staged blob with conflict markers "
        "on line 1 was committed. The guard is reading staged content through "
        "a pipe into an early-exiting matcher again.\n" + combined
    )
    assert "big_conflict.txt" in combined, "a blocked commit must name the path"
    assert head_before == _git(
        ["rev-parse", "HEAD"], cwd=guarded_repo, env=env
    ).stdout, "the commit landed despite a nonzero exit"


def test_guard_allows_a_large_clean_staged_blob(
    guarded_repo: Path, env: dict[str, str]
):
    """The size cases above must block on CONTENT, not merely on being large.

    Without this, a guard that blocked every blob over a megabyte would pass
    the regression lock while being useless.
    """
    _write(guarded_repo, "big_clean.txt", ("b" * 4096 + "\n") * 500)
    _git(["add", "big_clean.txt"], cwd=guarded_repo, env=env)

    result = _git(
        ["commit", "-m", "clean", "--", "big_clean.txt"],
        cwd=guarded_repo,
        env=env,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_guard_exempts_binary_blobs_from_the_text_checks(
    guarded_repo: Path, env: dict[str, str]
):
    """A binary blob whose bytes spell a marker is not a merge conflict.

    Pins the binary probe that guards the marker check, so that fixing the
    fail-open did not turn every image containing an unlucky byte run into a
    blocked commit.
    """
    payload = CONFLICT_BLOCK.encode() + b"\x00\x01\x02" + b"c" * 200_000
    (guarded_repo / "asset.bin").write_bytes(payload)
    _git(["add", "asset.bin"], cwd=guarded_repo, env=env)

    result = _git(
        ["commit", "-m", "bin", "--", "asset.bin"],
        cwd=guarded_repo,
        env=env,
        check=False,
    )
    assert result.returncode == 0, (
        "a binary blob was blocked by a text check\n" + result.stdout + result.stderr
    )


# ---------------------------------------------------------------------------
# AC-035 / AC-036 / OT-033 — Check 4, the `/bin/sh -n` lint on staged evidence
# ---------------------------------------------------------------------------


def _evidence_log(cmd: str, *, body: str = "some captured output\n") -> str:
    """One evidence log, in the shape the server's own parser reads.

    The leading comment block is the header, and the first `# evidence-cmd:` in
    it is the command — `evidence.py#_parse_evidence_header`'s grammar, which
    the guard's Check 4 reads the same way so that the two doors cannot disagree
    about which text they are judging.
    """
    return f"# evidence-cmd: {cmd}\n# evidence-for: FR-001\n\n{body}"


def test_guard_blocks_a_staged_evidence_log_whose_command_does_not_parse(
    guarded_repo: Path, env: dict[str, str]
):
    """OT-033 verbatim: 'A staged evidence log whose command fails `/bin/sh -n`
    is BLOCKED at commit naming the log.'

    CT-014's output column asks for two things in the message and both are
    asserted: the log, so the teammate knows WHICH of the several files in a
    commit is wrong, and the shell's own complaint, so they do not have to
    re-run the shell by hand to find out what it objected to. A block that
    said only "an evidence command did not parse" would be a worse outcome
    than no block, because the teammate would reach for --no-verify.
    """
    _write(guarded_repo, "evidence/broken.log", _evidence_log("if [ 1 ; then"))
    _git(["add", "evidence/broken.log"], cwd=guarded_repo, env=env)
    head_before = _git(["rev-parse", "HEAD"], cwd=guarded_repo, env=env).stdout

    result = _git(
        ["commit", "-m", "bad evidence", "--", "evidence/broken.log"],
        cwd=guarded_repo,
        env=env,
        check=False,
    )

    assert result.returncode != 0, "the guard let an unparseable command through"
    combined = result.stdout + result.stderr
    assert "evidence/broken.log" in combined, (
        "a blocked commit must name the offending log; with several evidence "
        "files in one commit an unnamed block says nothing actionable"
    )
    assert "/bin/sh -n" in combined, (
        "the message does not name the check that fired, so the teammate "
        "cannot tell this block from the conflict-marker or size checks"
    )
    # The shell's own words, forwarded rather than replaced by a summary.
    assert "syntax error" in combined.lower() or "unexpected" in combined.lower(), (
        "the shell's message was discarded, so the teammate has to re-run the "
        "shell by hand to learn what it objected to"
    )
    assert head_before == _git(
        ["rev-parse", "HEAD"], cwd=guarded_repo, env=env
    ).stdout


def test_guard_lints_the_command_without_running_it(
    guarded_repo: Path, env: dict[str, str], tmp_path: Path
):
    """`-n` reads and parses. It does not execute — and that is the only reason
    handing an unreviewed staged command to a shell is safe at all.

    A broken command proves nothing here: a shell abandons a script it cannot
    parse without executing any of it, so the BLOCKED case above would look
    identical whether or not `-n` were present. This stages a command that is
    perfectly VALID and whose whole purpose is a side effect. The guard must
    pass it, and the side effect must not have happened — so a Check 4 that
    lost its `-n` fails here immediately, which is where it must fail, because
    the alternative is a hook that executes whatever anyone stages.
    """
    witness = tmp_path / "the-guard-executed-it"
    _write(guarded_repo, "evidence/side-effect.log", _evidence_log(f"touch {witness}"))
    _git(["add", "evidence/side-effect.log"], cwd=guarded_repo, env=env)

    result = _git(
        ["commit", "-m", "valid", "--", "evidence/side-effect.log"],
        cwd=guarded_repo,
        env=env,
        check=False,
    )

    assert result.returncode == 0, (
        f"a valid command was blocked: {result.stdout + result.stderr}"
    )
    assert not witness.exists(), (
        "the guard EXECUTED a staged evidence command. Check 4 is missing its "
        "`-n`, and every commit now runs whatever the staged header says."
    )


def test_guard_allows_an_evidence_log_whose_command_parses(
    guarded_repo: Path, env: dict[str, str]
):
    """AC-036's consequence, and AC-038's: the lint judges SYNTAX and nothing
    else, so the dialect-sensitive constructs a real corpus uses still commit.

    `set -o pipefail` is the case with history: it opens the shared whole-suite
    log, it is the one construct in this corpus a researcher once claimed a
    fleet host would reject, and `sh -n` accepts it on every host because
    parsing never reaches the `set`. A lint that grepped for bashisms — or ran
    shellcheck, which has opinions about far more than this — would block the
    single log every FULL sweep re-executes.

    The guard is also silent, not merely non-blocking: a check that printed a
    warning on every well-formed evidence log would train teammates to ignore
    its output, which is how the conflict-marker block gets ignored too.
    """
    _write(
        guarded_repo,
        "evidence/fine.log",
        _evidence_log("set -o pipefail; ls | sort 2>&1 | sed -E 's/a/b/'"),
    )
    _git(["add", "evidence/fine.log"], cwd=guarded_repo, env=env)

    result = _git(
        ["commit", "-m", "good evidence", "--", "evidence/fine.log"],
        cwd=guarded_repo,
        env=env,
        check=False,
    )

    assert result.returncode == 0, (
        f"a well-formed command was blocked: {result.stdout + result.stderr}"
    )
    assert "evidence/fine.log" in _tree_paths(guarded_repo, env)
    assert "foundry-guard" not in (result.stdout + result.stderr), (
        "the guard spoke about a file it had no complaint over"
    )


def test_guard_lints_only_what_declares_an_evidence_command(
    guarded_repo: Path, env: dict[str, str]
):
    """The lint's membership rule, from the other side.

    Check 4 identifies an evidence log by CONTENT — a `# evidence-cmd:`
    directive in the leading comment block — and never by path, because this
    file is a template installed into repositories that have no `evidence/`
    directory and never will. Three files that would break a careless
    recogniser are staged together:

      * a shell script whose leading block is a shebang and prose;
      * a Markdown document that QUOTES an unparseable evidence command inside
        a fenced block, well past its own leading block — the shape every
        casting prompt and `agents/teammate.md` actually have;
      * a Python module whose leading comment mentions evidence at all.

    None of them declares a command, so none is linted, and the commit lands.
    """
    _write(guarded_repo, "run.sh", "#!/bin/sh\n# a normal script\nset -e\nls\n")
    _write(
        guarded_repo,
        "docs/protocol.md",
        "# Evidence protocol\n\nAuthors write a header:\n\n"
        "```\n# evidence-cmd: if [ 1 ; then\n```\n\n...and it is documented.\n",
    )
    _write(
        guarded_repo,
        "reader.py",
        "# Reads evidence logs and their evidence-cmd headers.\nX = 1\n",
    )
    paths = ["run.sh", "docs/protocol.md", "reader.py"]
    _git(["add", *paths], cwd=guarded_repo, env=env)

    result = _git(
        ["commit", "-m", "not evidence", "--", *paths],
        cwd=guarded_repo,
        env=env,
        check=False,
    )

    assert result.returncode == 0, (
        f"Check 4 fired on a file that declares no evidence command: "
        f"{result.stdout + result.stderr}"
    )
    assert set(paths) <= set(_tree_paths(guarded_repo, env))


def test_guard_lint_reaches_for_the_host_shell_and_nothing_else():
    """AC-036 verbatim: '`/bin/sh -n` on the host' — the lint uses the host's
    `/bin/sh` and nothing else; no shellcheck, no bashism grep.

    Source-level, because the behavioural tests cannot see this. A guard that
    ran `shellcheck` when it was installed and `/bin/sh -n` otherwise would
    pass every drive above on a machine without shellcheck and change its
    verdict on a machine with one — a hook whose answer depends on what the
    teammate happens to have on PATH is worse than one that is merely strict.

    The Out of Scope line is the rule: 'A bashism grep or shellcheck in the
    lint; `/bin/sh -n` only.'
    """
    code = "\n".join(_executable_lines(GUARD_SRC.read_text(encoding="utf-8")))

    assert "/bin/sh -n" in code, (
        "the guard does not parse staged evidence commands with the host shell"
    )
    for second_opinion in ("shellcheck", "bash -n", "checkbashisms", "zsh -n"):
        assert second_opinion not in code, (
            f"the guard consults {second_opinion!r}. The shell that JUDGES an "
            f"evidence command must be the shell that RUNS it, and the server "
            f"runs it under Popen(shell=True) — /bin/sh -c on POSIX."
        )


def test_guard_lint_never_executes_the_staged_command_by_construction():
    """The `-n` flag, pinned at the source so it cannot be lost in a refactor.

    The behavioural test above catches this the moment it happens, but only if
    someone runs the suite. This states the property where a reviewer reading a
    diff to the guard will see it: every invocation of a shell on staged
    content carries `-n`, and none of them is a bare `-c`.
    """
    for line in _executable_lines(GUARD_SRC.read_text(encoding="utf-8")):
        if "/bin/sh" not in line:
            continue
        assert "-n" in line, (
            f"the guard invokes a shell on staged content without `-n`, so it "
            f"EXECUTES what it was asked to parse:\n  {line.strip()}"
        )


def test_guard_never_pipes_blob_content_into_an_early_exiting_matcher():
    """Source-level lock on the D-014 root cause.

    The behavioural tests above catch the bug at the sizes they exercise. This
    catches the SHAPE, so a reviewer does not have to guess whether some new
    check reintroduced it at a size nobody tested.
    """
    for line in _executable_lines(GUARD_SRC.read_text(encoding="utf-8")):
        if "cat-file blob" not in line:
            continue
        # `||` is the OR operator, not a pipe; only a real pipe is the hazard.
        piped = line.replace("||", "")
        assert "|" not in piped, (
            "staged blob content is piped somewhere in the guard:\n"
            f"  {line.strip()}\n"
            "Under `set -o pipefail` a matcher that exits early (grep -q, head, "
            "sed -n 1q) kills git with SIGPIPE and the pipeline reports 141, "
            "which an `if` swallows as 'no match' — the check fails OPEN on "
            "large blobs. Redirect the blob to a file and check the file."
        )


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


# The husky/lefthook layout: the hook body is a TRACKED file in the work tree
# and the path git actually runs is a relative symlink pointing into it.
PROJECT_HOOK_LINK = "../../scripts/my-hook.sh"
PROJECT_HOOK_BODY = "#!/bin/sh\necho 'the project owns this hook'\n"


def _symlinked_project_hook(repo: Path, env: dict[str, str]) -> tuple[Path, Path]:
    """Give ``repo`` a version-controlled hook reached through a symlink.

    Returns ``(link, script)`` — the path git runs, and the tracked file at the
    far end of it. The script is committed, so any write that reaches it shows
    up in ``git status`` and can be captured by the next pathspec commit.
    """
    script = _write(repo, "scripts/my-hook.sh", PROJECT_HOOK_BODY)
    script.chmod(0o755)
    _git(["add", "scripts/my-hook.sh"], cwd=repo, env=env)
    _git(["commit", "-q", "-m", "project hook"], cwd=repo, env=env)

    hooks = _hook_dir(repo, env)
    hooks.mkdir(parents=True, exist_ok=True)
    link = hooks / "pre-commit"
    link.symlink_to(PROJECT_HOOK_LINK)
    return link, script


def test_installer_replaces_a_symlinked_hook_without_writing_through_it(
    repo: Path, env: dict[str, str]
):
    """D-107: a symlinked hook is UNLINKED, never written through.

    ``cp`` and ``chmod`` follow a symlink at the destination and act on the
    file it points AT. Against the layout above, a bare
    ``cp "$GUARD_SRC" "$HOOK_DEST"`` rewrote two lines of tracked repo source
    into the whole guard, left ``pre-commit`` as the original link, and then
    passed its own post-install check because ``-x`` and the marker grep
    followed the link too — printing "Commit guard active" over the clobber.
    start.md and resume.md run this installer against every target repo at the
    top of every run, so that mutation landed before any casting began and the
    next teammate's pathspec commit could capture it.
    """
    link, script = _symlinked_project_hook(repo, env)
    before = script.read_bytes()

    result = _run_installer(repo, env=env)

    # The tracked file at the far end is untouched, byte for byte.
    assert script.read_bytes() == before, (
        "D-107: the installer wrote THROUGH the symlink and rewrote a tracked "
        "file in the target repo's work tree"
    )
    assert _git(["status", "--porcelain"], cwd=repo, env=env).stdout == "", (
        "D-107: installing the guard dirtied the target repo's working tree"
    )

    # The hook path itself is now a real, executable, marked file.
    assert not link.is_symlink(), "the symlink was never replaced"
    assert link.is_file() and os.access(link, os.X_OK)
    assert link.read_bytes() == GUARD_SRC.read_bytes()

    # A symlinked hook is a deliberate layout, so the operator is TOLD, and
    # told about the real file rather than only the link.
    assert "SYMLINK" in result.stdout, result.stdout
    assert PROJECT_HOOK_LINK in result.stdout, (
        "the report does not name the link it replaced"
    )
    resolved = [ln for ln in result.stdout.splitlines() if "resolving to:" in ln]
    assert resolved, "the report never resolves the link to a real path"
    reported = Path(resolved[0].split("resolving to:")[1].strip())
    assert reported.resolve() == script.resolve(), (
        f"the report points at {reported}, not the file the link led to"
    )

    # The backup preserves the LINK, so the restore command it prints actually
    # restores a link. A dereferenced backup would put a copy of the script
    # where the project deliberately keeps a pointer.
    backups = [p for p in link.parent.iterdir() if ".foundry-backup." in p.name]
    assert len(backups) == 1, [p.name for p in link.parent.iterdir()]
    assert backups[0].is_symlink(), "the backup dereferenced the link"
    assert os.readlink(backups[0]) == PROJECT_HOOK_LINK


def test_installer_no_clobber_refuses_a_symlinked_hook(
    repo: Path, env: dict[str, str]
):
    """--no-clobber over a symlink refuses and leaves BOTH ends alone."""
    link, script = _symlinked_project_hook(repo, env)
    before = script.read_bytes()

    result = _run_installer(repo, "--no-clobber", env=env, check=False)

    assert result.returncode != 0
    assert "no-clobber" in result.stderr
    assert "SYMLINK" in result.stderr, result.stderr
    assert link.is_symlink(), "--no-clobber unlinked the hook anyway"
    assert script.read_bytes() == before
    assert _git(["status", "--porcelain"], cwd=repo, env=env).stdout == ""


def test_installer_replaces_a_dangling_symlink(repo: Path, env: dict[str, str]):
    """A dangling link is still a link, and ``-e`` cannot see that.

    ``-e`` follows the link, so a ``pre-commit`` pointing at a path that does
    not exist read as "nothing is here" and took the plain install branch —
    where ``cp`` refuses to write through a dangling symlink on both GNU and
    BSD, so ``set -e`` aborted the installer on a bare ``cp:`` error with no
    named cause, in violation of its own exit contract, leaving the repo
    unguarded. ``-L`` is true for live and dangling links alike, which is why
    it is tested first.
    """
    hooks = _hook_dir(repo, env)
    hooks.mkdir(parents=True, exist_ok=True)
    link = hooks / "pre-commit"
    link.symlink_to("../../absent-hook.sh")
    victim = repo / "absent-hook.sh"

    result = _run_installer(repo, env=env)

    assert not victim.exists(), "the installer materialised the link's target"
    assert _git(["status", "--porcelain"], cwd=repo, env=env).stdout == ""
    assert not link.is_symlink()
    assert link.read_bytes() == GUARD_SRC.read_bytes()
    assert os.access(link, os.X_OK)
    assert "dangling" in result.stdout, result.stdout


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


def test_installer_never_writes_content_through_the_hook_destination():
    """Source-level lock on the D-107 root cause.

    ``cp``, ``tee`` and shell redirection all FOLLOW a symlink at the
    destination: they write the file it points at and leave the link standing.
    The behavioural tests above catch that on the branches they exercise, but
    a bare ``cp`` reintroduced in the plain-install branch would never meet a
    symlink in those tests. This catches the SHAPE instead, so the hazard
    cannot come back on a branch nobody parametrized. The guard is written to
    a temp file and renamed, because rename(2) replaces the LINK ITSELF.
    """
    writers = ("cp", "install", "tee", "cat", "printf", "echo")
    for line in _executable_lines(INSTALLER.read_text(encoding="utf-8")):
        stripped = line.strip()
        assert not re.search(r'>>?\s*"\$HOOK_DEST"', stripped), (
            "the installer redirects output onto the hook destination:\n"
            f"  {stripped}\n"
            "Redirection follows a symlink and truncates its target, which in "
            "a version-controlled hook layout is a TRACKED file. Write a temp "
            "file in HOOKS_DIR and `mv` it into place."
        )
        if stripped.startswith(writers) and stripped.endswith('"$HOOK_DEST"'):
            raise AssertionError(
                "the installer copies onto the hook destination:\n"
                f"  {stripped}\n"
                "`cp` follows a symlink at the destination and writes the file "
                "it points AT, leaving the hook unreplaced while a tracked "
                "file in the target repo is silently rewritten (D-107). Write "
                "a temp file in HOOKS_DIR and `mv` it into place."
            )


def test_installer_copies_rather_than_symlinks():
    """The plugin cache is version-namespaced; a symlink goes stale on update."""
    code = "\n".join(_executable_lines(INSTALLER.read_text(encoding="utf-8")))
    assert "ln -s" not in code, (
        "a symlink into the version-namespaced plugin cache breaks on update"
    )
    assert "cp " in code
