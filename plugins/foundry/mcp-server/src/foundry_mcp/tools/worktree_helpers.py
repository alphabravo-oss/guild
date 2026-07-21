"""Phase 7 / TEST-01 — factored worktree management for ephemeral execution.

Originally lived inline in plugins/foundry/mcp-server/src/foundry_mcp/tools/evidence.py
(Phase 4 / EVID-01 + Phase 5 / EVID-02). Factored here per RESEARCH.md
Open Question 1 recommendation so Phase 7 / TEST-01 (test_deriver.py) can
reuse the same patterns without copy-paste drift.

Phase 4/5 byte-equivalence: function bodies copied verbatim; evidence.py
is updated to import-only.

Added: ``dir_prefix`` kwarg on ``_setup_worktree`` so Phase 4 callers pass
``dir_prefix="casting-"`` (preserving worktrees/casting-{id}/) and Phase 7
callers pass ``dir_prefix="test-deriver-cycle-"`` (creating
worktrees/test-deriver-cycle-{N}/). The default value preserves Phase 4
backwards-compat — existing callers ``_setup_worktree(project_root, casting_id,
commit_hash, run_dir)`` work unchanged.

Module-level state ``_WORKTREE_LOCK`` and ``_PRUNE_DONE_FOR`` is owned here
and re-exported via ``from foundry_mcp.tools.worktree_helpers import ...`` in
evidence.py — single identity preserved across the import boundary so all
Phase 4 + Phase 5 + Phase 7 callers serialize on the same lock.
"""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import threading
import time
from pathlib import Path

# ---------------------------------------------------------------------------
# Module-level state (owned here; re-exported by evidence.py).
#
# Pitfall 1 (RESEARCH.md): ``git worktree`` accumulates orphaned dirs from
# crashed prior runs. ``_prune_orphaned_worktrees`` runs once per session
# per project_root.
#
# Pitfall 2 (RESEARCH.md): concurrent ``git worktree add`` invocations on
# the same repo race on ``.git/config.lock``. ``_WORKTREE_LOCK`` serializes
# them at module level (within-process); cross-process serialization
# delegates to git's own locking.
# ---------------------------------------------------------------------------
_WORKTREE_LOCK: threading.Lock = threading.Lock()
_PRUNE_DONE_FOR: set[str] = set()  # project_root strings already pruned this session


# ---------------------------------------------------------------------------
# Subprocess re-execution with descendant cleanup.
#
# Pitfall 3 (RESEARCH.md): ``subprocess.run(timeout=N, start_new_session=True)``
# kills the IMMEDIATE child but leaves descendants running. The Popen +
# manual ``os.killpg`` path kills the entire process group on timeout.
#
# Pitfall 4 (RESEARCH.md): non-UTF-8 captured output crashes the comparator
# unless ``errors='replace'`` is paired with ``text=True`` + ``encoding``.
# U+FFFD substitutes invalid bytes deterministically.
#
# stderr is merged into stdout (CONTEXT.md "stdout+stderr-merged byte-match")
# so a single captured string compares against the committed log.
# ---------------------------------------------------------------------------
def _run_command_with_timeout(
    cmd: str,
    cwd: Path,
    timeout: int,
) -> tuple[int, str, float]:
    """Re-execute ``cmd`` in ``cwd`` with timeout enforcement.

    Args:
        cmd: shell command string (executed via ``shell=True``).
        cwd: working directory (typically the worktree path).
        timeout: wall-clock seconds before SIGTERM/SIGKILL escalation.

    Returns:
        ``(exit_code, merged_stdout_stderr, elapsed_seconds)``.
        On timeout, ``exit_code == -1`` and ``merged_stdout_stderr`` carries
        whatever the child managed to flush before being killed.

    Discipline (per CONTEXT.md + RESEARCH.md Pitfalls 3 & 4):
      - ``shell=True`` so users can write pipelines / multi-token cmds in
        the ``# evidence-cmd:`` header.
      - ``stderr=subprocess.STDOUT`` merges streams (single-string compare).
      - ``text=True, encoding='utf-8', errors='replace'`` makes binary or
        non-UTF-8 output survive comparator entry.
      - ``start_new_session=True`` puts the child in a fresh process group
        so ``os.killpg`` reaches descendants.
      - ``env=os.environ.copy()`` inherits the lead's env (CONTEXT.md).

    Timeout escalation: SIGTERM → 2s grace → SIGKILL. Wrapped in
    ``ProcessLookupError``/``OSError`` guards because the child may have
    already exited between the timeout and the killpg call (race).
    """
    started = time.monotonic()
    proc = subprocess.Popen(
        cmd,
        shell=True,
        cwd=str(cwd),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,  # merge stderr→stdout per CONTEXT.md
        text=True,
        encoding="utf-8",
        errors="replace",
        start_new_session=True,
        env=os.environ.copy(),  # inherit lead's env per CONTEXT.md
    )
    try:
        stdout, _ = proc.communicate(timeout=timeout)
        elapsed = time.monotonic() - started
        return proc.returncode, stdout or "", elapsed
    except subprocess.TimeoutExpired:
        # Pitfall 3: kill the entire process group, not just the immediate child.
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        except (ProcessLookupError, OSError):
            pass
        try:
            stdout, _ = proc.communicate(timeout=2)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except (ProcessLookupError, OSError):
                pass
            try:
                stdout, _ = proc.communicate(timeout=2)
            except subprocess.TimeoutExpired:
                stdout = ""
        elapsed = time.monotonic() - started
        return -1, stdout or "", elapsed


# ---------------------------------------------------------------------------
# Worktree management with concurrent-safety serialization.
# ---------------------------------------------------------------------------
def _setup_worktree(
    project_root: Path,
    casting_id: int | str,
    commit_hash: str,
    run_dir: Path,
    *,
    dir_prefix: str = "casting-",  # Phase 4 default preserves backwards-compat
) -> Path:
    """Create a detached worktree at ``commit_hash`` under ``run_dir``.

    Args:
        project_root: repo containing ``.git/``.
        casting_id: int or str; embedded in the worktree dir name.
        commit_hash: full SHA (or any rev-parseable ref) to check out.
        run_dir: parent directory; worktree lives at
            ``run_dir / 'worktrees' / f'{dir_prefix}{id}'``.
        dir_prefix: directory-name prefix for the worktree dir. Defaults to
            ``"casting-"`` so Phase 4 callers
            ``_setup_worktree(project_root, casting_id, commit_hash, run_dir)``
            produce ``run_dir/worktrees/casting-{id}/`` byte-identically to
            the pre-refactor implementation. Phase 7 callers pass
            ``dir_prefix="test-deriver-cycle-"`` to land at
            ``run_dir/worktrees/test-deriver-cycle-{id}/``.

    Returns:
        Absolute path to the new worktree.

    Raises:
        RuntimeError when ``git worktree add`` fails (translated by
        ``verify_evidence`` to ``EVIDENCE_COMMIT_MISSING``).

    Idempotency: a stale worktree dir from a prior crash is torn down before
    re-creation. The ``_WORKTREE_LOCK`` serializes within-process so two
    threads don't race on ``.git/config.lock`` (Pitfall 2).
    """
    worktree_path = run_dir / "worktrees" / f"{dir_prefix}{casting_id}"
    worktree_path.parent.mkdir(parents=True, exist_ok=True)
    if worktree_path.exists():
        _teardown_worktree(project_root, worktree_path)
    with _WORKTREE_LOCK:
        result = subprocess.run(
            [
                "git",
                "-C",
                str(project_root),
                "worktree",
                "add",
                "--detach",
                str(worktree_path),
                commit_hash,
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
    if result.returncode != 0:
        raise RuntimeError(
            f"git worktree add failed (commit {commit_hash[:12]}): "
            f"{result.stderr.strip()}"
        )
    return worktree_path


def _teardown_worktree(project_root: Path, worktree_path: Path) -> None:
    """Idempotent teardown: ``git worktree remove --force`` → ``shutil.rmtree``
    fallback → ``git worktree prune``.

    Safe to call on a non-existent worktree (Pitfall 1: prior-crash teardowns
    must not crash the current run). ``capture_output=True`` swallows the
    inevitable "not a working tree" stderr on the prune path.
    """
    subprocess.run(
        [
            "git",
            "-C",
            str(project_root),
            "worktree",
            "remove",
            "--force",
            str(worktree_path),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
    )
    if worktree_path.exists():
        shutil.rmtree(worktree_path, ignore_errors=True)
    subprocess.run(
        ["git", "-C", str(project_root), "worktree", "prune"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=15,
    )


def _prune_orphaned_worktrees(project_root: Path) -> None:
    """Run ``git worktree prune`` once per session per ``project_root``.

    Pitfall 1: orphan worktrees from prior crashes stay registered in
    ``.git/worktrees/`` until pruned. The module-level ``_PRUNE_DONE_FOR``
    guard avoids re-pruning on every ``verify_evidence`` call.
    """
    key = str(project_root.resolve()) if project_root.exists() else str(project_root)
    if key in _PRUNE_DONE_FOR:
        return
    subprocess.run(
        ["git", "-C", str(project_root), "worktree", "prune"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=15,
    )
    _PRUNE_DONE_FOR.add(key)
