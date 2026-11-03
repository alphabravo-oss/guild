"""Run the project's own commands and compare a commit with its base.

``new_failures`` is the commit check: a command that fails at HEAD is run
again at the base commit in a detached worktree, and only failures HEAD has
and the base does not count. The idea and the dependency-directory linking
are mason's (mill_mcp/tools/commit_check.py, worktree_helpers.py); this is a
smaller, language-neutral version that compares the failing names a run
prints (test names, file: message pairs) instead of per-language checkers.

``fail_before_pass_after`` is the Agentless rule ("The generated test should
be able to be used to both reproduce the issue as well as to verify the issue
has been fixed"): a fix's test, copied into a checkout of the code before the
fix, must fail there and pass at HEAD.

Results of a command at a commit are cached in ``<run>/checks/``, keyed by
commit and command, so a base is run once per run however many slices and
rounds compare against it.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from common import git, head, read_json, tail, write_json

DEFAULT_TIMEOUT = int(os.environ.get("TROWEL_CHECK_TIMEOUT", "1800"))
DEP_DIRS = ("node_modules", ".venv", "venv", "vendor", ".bundle", "target")

# Lines that name one failure. Each pattern's last group is the failure's name;
# line and column numbers are dropped so a failure keeps its name when the
# code above it moves.
FAILURE_PATTERNS = [
    re.compile(r"^\s*--- FAIL: (\S+)"),                         # go test
    re.compile(r"^FAIL\s+(\S+)\s+(?:[\d.]+s|\[.*\])\s*$"),         # go package failed / build failed
    re.compile(r"^(?:FAILED|ERROR) (\S+)"),                      # pytest
    re.compile(r"^\s*(?:FAIL|×|✕|✗)\s+(.+?)\s*$"),               # jest, vitest
    re.compile(r"^test (\S+) \.\.\. FAILED"),                    # cargo test
    re.compile(r"^\s*Failed (\S+)"),                             # dotnet test
    re.compile(r"^\[ERROR\]\s+(?:Tests run:.*?<<< FAILURE!\s*)?(\S.*?)\s*$"),  # maven
    re.compile(r"^(\S+ > \S+) FAILED"),                          # gradle
]
DURATION = re.compile(r"\s+\(?[\d.]+\s?m?s\)?$")
COMPILER_ERROR = re.compile(r"^(\.{0,2}/?[\w./@+-]+\.\w+):\d+(?::\d+)?:\s*(?:error:?\s*)?(.+?)\s*$")


def failure_names(output: str) -> set[str]:
    names = set()
    for line in output.splitlines():
        for rx in FAILURE_PATTERNS:
            m = rx.match(line)
            if m:
                names.add(DURATION.sub("", m.group(m.lastindex).strip()))
                break
        else:
            m = COMPILER_ERROR.match(line)
            if m:
                names.add(f"{m.group(1)}: {m.group(2)}")
    return names


def run_command(cmd: str, cwd: Path, timeout: int = DEFAULT_TIMEOUT) -> dict:
    start = time.monotonic()
    try:
        r = subprocess.run(cmd, shell=True, cwd=str(cwd), capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=timeout)
        out, code = (r.stdout or "") + (r.stderr or ""), r.returncode
    except subprocess.TimeoutExpired as exc:
        out = ((exc.stdout or b"").decode("utf-8", "replace") if isinstance(exc.stdout, bytes) else (exc.stdout or ""))
        out += f"\n[trowel] timed out after {timeout}s"
        code = 124
    except OSError as exc:
        out, code = f"[trowel] could not run: {exc}", 127
    return {"cmd": cmd, "exit": code, "seconds": round(time.monotonic() - start, 1),
            "failures": sorted(failure_names(out)) if code else [], "tail": tail(out, 80)}


def _cache_key(commit: str, cmd: str, cwd: str) -> str:
    return hashlib.sha256(f"{commit}\0{cwd}\0{cmd}".encode()).hexdigest()[:16]


def run_at(root: Path, rdir: Path, commit: str, command: dict, timeout: int = DEFAULT_TIMEOUT) -> dict:
    """Run one command at a commit: in the working tree when the commit is HEAD
    and the tree has no changes at all, else in a detached worktree. Untracked
    files count: a new test file an agent has not committed yet is not part of
    the commit, and a result cached for the commit must not include it."""
    cwd_rel = command.get("cwd") or ""
    path = Path(rdir) / "checks" / f"{_cache_key(commit, command['cmd'], cwd_rel)}.json"
    cached = read_json(path)
    if cached is not None:
        return cached
    dirty = git(root, "status", "--porcelain", "--untracked-files=normal").stdout.strip()
    if commit == head(root) and not dirty:
        result = run_command(command["cmd"], Path(root) / cwd_rel, timeout)
    else:
        with worktree(root, commit) as tree:
            result = run_command(command["cmd"], tree / cwd_rel, timeout)
    result.update({"commit": commit, "cwd": cwd_rel})
    write_json(path, result)
    return result


class worktree:
    """A detached checkout of ``commit`` with the project's untracked
    dependency directories (node_modules, virtualenvs, vendor, a Cargo target)
    symlinked in, removed on exit."""

    def __init__(self, root: Path, commit: str):
        self.root, self.commit = Path(root), commit
        self.path = Path(tempfile.mkdtemp(prefix="trowel-wt-")) / "tree"

    def __enter__(self) -> Path:
        git(self.root, "worktree", "add", "--detach", str(self.path), self.commit, check=True)
        link_dependency_dirs(self.root, self.path)
        return self.path

    def __exit__(self, *exc):
        git(self.root, "worktree", "remove", "--force", str(self.path))
        shutil.rmtree(self.path.parent, ignore_errors=True)
        git(self.root, "worktree", "prune")


def link_dependency_dirs(root: Path, tree: Path) -> list[str]:
    r = git(root, "ls-files", "--others", "--directory", "-z")
    linked = []
    for entry in r.stdout.split("\0"):
        rel = entry.strip().rstrip("/")
        if not rel or Path(rel).name not in DEP_DIRS:
            continue
        src, dst = Path(root) / rel, Path(tree) / rel
        if src.name == "target" and not (src.parent / "Cargo.toml").is_file():
            continue
        if src.is_dir() and not dst.exists() and dst.parent.is_dir():
            try:
                dst.symlink_to(src.resolve())
                linked.append(rel)
            except OSError:
                pass
    return linked


def new_failures(root: Path, rdir: Path, base: str, commands: list[dict], timeout: int = DEFAULT_TIMEOUT) -> list[dict]:
    """Every command at HEAD; for each that fails, what is new against ``base``.

    Returns one entry per command: {cmd, cwd, exit, new: [names], status}
    where status is pass, new_failures or pre_existing."""
    sha = head(root)
    out = []
    for c in commands:
        now_r = run_at(root, rdir, sha, c, timeout)
        entry = {"cmd": c["cmd"], "cwd": c.get("cwd", ""), "exit": now_r["exit"], "new": [], "status": "pass",
                 "tail": "" if now_r["exit"] == 0 else now_r["tail"]}
        if now_r["exit"] != 0:
            base_r = run_at(root, rdir, base, c, timeout) if base else {"exit": 0, "failures": []}
            if base_r["exit"] == 0:
                entry["new"] = now_r["failures"] or [f"`{c['cmd']}` exits {now_r['exit']}"]
            else:
                entry["new"] = sorted(set(now_r["failures"]) - set(base_r["failures"]))
            entry["status"] = "new_failures" if entry["new"] else "pre_existing"
        out.append(entry)
    return out


def fail_before_pass_after(root: Path, before: str, test_files: list[str], cmd: str, cwd: str = "",
                           timeout: int = DEFAULT_TIMEOUT) -> dict:
    """Copy ``test_files`` as they are at HEAD into a checkout of ``before``;
    the command must fail there and pass in the working tree."""
    root = Path(root)
    missing = [f for f in test_files if not (root / f).is_file()]
    if missing or not test_files or not cmd:
        return {"ok": False, "why": f"no test to run: files missing {missing}" if missing else "no test files or command given"}
    with worktree(root, before) as tree:
        for f in test_files:
            (tree / f).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(root / f, tree / f)
        b = run_command(cmd, tree / cwd, timeout)
    a = run_command(cmd, root / cwd, timeout)
    ok = b["exit"] != 0 and a["exit"] == 0
    why = ("fails before the fix and passes after it" if ok else
           "the test passes before the fix, so it does not reproduce the finding" if b["exit"] == 0 else
           "the test still fails after the fix")
    return {"ok": ok, "why": why, "before": {"exit": b["exit"], "tail": tail(b["tail"], 20)},
            "after": {"exit": a["exit"], "tail": tail(a["tail"], 20)}}
