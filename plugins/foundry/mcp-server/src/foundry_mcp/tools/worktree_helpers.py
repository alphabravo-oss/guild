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
``_WORKTREE_CLAIMS`` (D-111 / D-131) is owned here and not re-exported: nothing
outside this module reads it, and the guarantee it provides — that two
concurrent invocations for the same casting get disjoint trees — belongs to
``_setup_worktree``/``_teardown_worktree``, not to their callers.
"""

from __future__ import annotations

import fcntl
import os
import shutil
import signal
import subprocess
import threading
import time
from pathlib import Path
from typing import IO

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
#
# D-111: the lock covered ``git worktree add`` and nothing else, which left the
# far more damaging race wide open. Two concurrent ``verify_evidence`` calls for
# the SAME casting derive the SAME path, and ``_setup_worktree``'s
# "tear down whatever is already there" step ran OUTSIDE the lock — so the
# second call DELETED the first call's live worktree while its evidence
# commands were still running against it, and the first surfaced a spurious
# EVIDENCE_OUTPUT_MISMATCH with nothing in the verdict hinting that concurrency
# caused it. Serializing ``add`` harder cannot fix that: the collision is on the
# PATH, and it spans the caller's whole use of the tree, not one git call.
#
# The claim closes it by making the path itself the contended resource. A path
# is claimed for the whole setup -> use -> teardown lifetime, so a second
# invocation that would have collided takes ``{base}-1`` instead and the two
# trees are disjoint. Claiming, rather than unconditionally uniquifying every
# path, is what preserves the two properties a bare unique suffix would have
# cost: the single-invocation path stays exactly ``worktrees/{dir_prefix}{id}``
# (which is what makes a teardown check falsifiable, and what the evidence
# harness asserts on), and a leftover dir from a crashed prior run is still
# reclaimed by name on the next attempt (Pitfall 1) instead of accumulating one
# orphan per GRIND cycle.
#
# D-131: the claim used to be a module-level Python set, and a set is invisible
# to another OS PROCESS, so the original harm reproduced in full one level up.
# The justification for keeping it process-local — that each process "derives
# its worktrees under its own run dir" — is false: ``run_dir`` is
# ``{project_root}/foundry-archive/{run}`` (foundry_handoff.py:452), a function
# of the RUN, not of the process. A lead re-running Foundry-Accept-Casting while
# a teammate's verification is in flight, or two INSPECT streams verifying the
# same casting, both derive the same path from different processes; process 1
# found it unclaimed IN ITS OWN SET, tore it down, and destroyed process 0's
# live tree.
#
# The claim is now an ``fcntl`` exclusive lock on a ``{path}.lock`` sidecar —
# the same primitive ``foundry.ledger_transaction`` uses for exactly the same
# reason (D-103), and for the same documented runtime floor (POSIX). One
# mechanism covers both scopes because an ``flock`` conflicts between OPEN FILE
# DESCRIPTIONS, not between processes: two threads here open the sidecar
# separately and so contend with each other just as two processes do. It is
# taken NON-BLOCKING, because the D-111 contract is "a colliding peer takes the
# next suffix", never "a colliding peer waits" — the two trees have to be
# disjoint, and serializing them would only make the second invocation slow
# instead of safe.
#
# ``_WORKTREE_CLAIMS`` holds the open descriptor, not the claim itself. The
# claim lives in the kernel; this dict is only how ``_teardown_worktree``
# finds the descriptor to release, given the path its caller passes. Losing
# the dict entry therefore cannot grant a second claim — it can only leak one,
# which costs the next invocation a suffix and never deadlocks anybody.
#
# The sidecar is NEVER unlinked. Removing a lock file is the classic race: a
# peer that has already opened the same path would take its lock on an unlinked
# inode and both would believe they held the path. The files are empty, one per
# worktree name, and sit beside the trees exactly as ``{ledger}.lock`` sits
# beside each ledger.
# ---------------------------------------------------------------------------
_WORKTREE_LOCK: threading.Lock = threading.Lock()
_PRUNE_DONE_FOR: set[str] = set()  # project_root strings already pruned this session
#: worktree path -> the open descriptor whose ``flock`` is that path's claim.
_WORKTREE_CLAIMS: dict[str, IO[str]] = {}


def _claim_sidecar(worktree_path: Path) -> Path:
    """The lock file whose ``flock`` is ``worktree_path``'s claim.

    A SIBLING of the worktree dir, never a child: ``git worktree add`` requires
    the target not to exist, and a teardown removes the tree wholesale, so a
    lock file inside it would be destroyed by the very operation it guards.
    """
    return worktree_path.with_name(worktree_path.name + ".lock")


def _acquire_claim(worktree_path: Path) -> IO[str] | None:
    """Take ``worktree_path``'s cross-process claim, or None if a peer holds it.

    Non-blocking by contract (see the module comment): a held path is not
    something to wait for, it is something to step around.
    """
    sidecar = _claim_sidecar(worktree_path)
    sidecar.parent.mkdir(parents=True, exist_ok=True)
    handle = open(sidecar, "a+", encoding="utf-8")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        # BlockingIOError (EWOULDBLOCK) is the contended case; any other OSError
        # means this path is not usable as a claim, and stepping to the next
        # suffix is the same correct answer.
        handle.close()
        return None
    return handle


def _release_claim(worktree_path: Path) -> None:
    """Release ``worktree_path``'s claim if this process holds it. Idempotent.

    Deliberately does NOT take ``_WORKTREE_LOCK``: ``_claim_worktree_path``
    calls ``_teardown_worktree`` (which ends here) while already holding that
    non-reentrant lock, and a re-acquire would deadlock. The dict mutation
    needs no lock of its own — ``pop`` is atomic, and the exclusion that
    matters is the kernel's.
    """
    handle = _WORKTREE_CLAIMS.pop(str(worktree_path), None)
    if handle is None:
        return
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    except OSError:
        pass  # closing the descriptor releases it regardless
    finally:
        handle.close()


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
def _claim_worktree_path(base: Path, project_root: Path) -> Path:
    """Claim a worktree path no live invocation ON THIS MACHINE is using.

    Returns ``base`` itself whenever it is free — the overwhelmingly common
    single-invocation case, whose directory name must not drift. A path a live
    invocation already holds yields ``{base}-1``, ``{base}-2``, and so on,
    whether that invocation is another thread here or another OS process
    (D-131).

    A directory that exists but is claimed by nobody is a prior crash's
    leftover, and is torn down here so the name is reusable (Pitfall 1). That
    check is the one D-111 moved: it used to run before any claim and outside
    the lock, where "already there" could not distinguish a dead run's litter
    from a peer's live tree, and it destroyed the peer. The teardown runs AFTER
    the claim is taken and BEFORE it is registered, so the release inside
    ``_teardown_worktree`` finds no registration and cannot drop the claim it
    is being run under.

    The caller MUST hold ``_WORKTREE_LOCK``: within one process, selection and
    claim have to be one atomic step, and the stale-dir reclamation between
    them must not interleave with a peer thread's ``git worktree add``
    (Pitfall 2). Across processes the ``flock`` alone carries it. The claim is
    released by ``_teardown_worktree``.
    """
    candidate = base
    suffix = 1
    while True:
        handle = (
            None
            if str(candidate) in _WORKTREE_CLAIMS
            else _acquire_claim(candidate)
        )
        if handle is not None:
            break
        candidate = base.with_name(f"{base.name}-{suffix}")
        suffix += 1
    if candidate.exists():
        _teardown_worktree(project_root, candidate)
    _WORKTREE_CLAIMS[str(candidate)] = handle
    return candidate


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
        Absolute path to the new worktree. Normally
        ``run_dir/worktrees/{dir_prefix}{casting_id}``, but a caller racing a
        live invocation for the same id gets a suffixed sibling instead
        (D-111) — so USE THE RETURNED PATH, never a re-derived one.

    Raises:
        RuntimeError when ``git worktree add`` fails (translated by
        ``verify_evidence`` to ``EVIDENCE_COMMIT_MISSING``).

    Idempotency: a stale worktree dir from a prior crash is torn down before
    re-creation. The ``_WORKTREE_LOCK`` serializes within-process so two
    threads don't race on ``.git/config.lock`` (Pitfall 2) and so path
    selection is atomic against a peer's (D-111).
    """
    base = run_dir / "worktrees" / f"{dir_prefix}{casting_id}"
    base.parent.mkdir(parents=True, exist_ok=True)
    with _WORKTREE_LOCK:
        worktree_path = _claim_worktree_path(base, project_root)
        try:
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
        except BaseException:
            # A claim outlives this call by design, so every path that leaves
            # WITHOUT a live worktree to protect has to release it here — a
            # `git` that is missing or hung raises out of `subprocess.run`
            # (D-116), and neither leaves anything behind to guard.
            _release_claim(worktree_path)
            raise
    if result.returncode != 0:
        _release_claim(worktree_path)
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

    Also releases the D-111 / D-131 path claim ``_setup_worktree`` took, which
    is what lets the next invocation for the same casting reuse the unsuffixed
    name. Released LAST, after the directory is gone, so a peer that then
    selects this path cannot collide with a teardown still in flight — and in a
    ``finally``, because a hung or missing ``git`` raises out of these calls
    (D-116) and a claim held past the last use of the tree would strand the base
    name for the life of the process, which is the accumulation this design
    exists to avoid. A peer that reclaims a path whose cleanup failed tears it
    down again before reusing it. A caller that skips teardown entirely only
    costs the next one a suffix: the claim is taken NON-BLOCKING, so leaking one
    can never deadlock anybody — and a process that dies holding one has it
    released by the kernel when its descriptors close, which is precisely the
    property the module-level set could not provide.

    Releasing an unheld path is a no-op, which is what makes it safe to call
    from ``_claim_worktree_path``'s stale-dir reclamation while that function
    holds the very claim being cleaned up under.
    """
    try:
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
    finally:
        _release_claim(worktree_path)


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
