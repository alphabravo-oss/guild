"""Run directory, state file, git and the patterns every trowel helper shares.

A run lives in ``<project>/trowel-archive/<run>/``. ``state.json`` is the
record the workflow resumes from; every other file in the run dir is an input
or an output of one step. Writes to state.json go through ``update_state``,
which holds an exclusive lock, because builders commit in parallel and each
commit is recorded there.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import subprocess
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

ARCHIVE = "trowel-archive"

# A requirement ID: an upper-case prefix, a dash, a number (FR-1, AC-014, GI-007, FR-2.1).
ID_TOKEN = r"[A-Z][A-Z0-9]{0,7}-\d+(?:\.\d+)*"
ID_RE = re.compile(rf"\b{ID_TOKEN}\b")

# A test file, by the naming conventions the common runners collect
# (copied from mason's mill_validate.TEST_FILE), plus files under a tests/,
# test/, __tests__/ or spec/ directory.
TEST_FILE = re.compile(
    r"(^|/)(test_[^/]+\.(py|c|cc|cpp)|[^/]+_test\.(py|go|rb|exs|dart|c|cc|cpp)|[^/]+_spec\.rb"
    r"|[^/]+\.(test|spec)\.[cm]?[jt]sx?|[^/]+(Test|Tests|Spec)\.(java|kt|kts|scala|groovy|cs|swift|php)"
    r"|tests\/([^/]+\/)*[^/]+\.rs)$"
    r"|(^|/)(tests?|__tests__|spec|testdata)/"
)
DOC_SUFFIXES = (".md", ".mdx", ".rst", ".txt", ".adoc")


def is_test_file(rel: str) -> bool:
    return bool(TEST_FILE.search(rel))


# Dependency, build and tool directories: never the project's own code.
SKIP_DIRS = {"node_modules", "vendor", ".git", "trowel-archive", "dist", "build", "target", ".venv", "venv", "__pycache__"}
_TEST_AFFIX = re.compile(r"^test_|(_test|_spec|Tests?|Spec)$")


def unit_of(rel: str) -> str:
    """The unit a file belongs to, by name: a test and the source it tests share one.

    `AISettings.settings.test.tsx`, `AISettings.test.tsx` and `AISettings.tsx`
    are all `aisettings`; `foo_test.go` and `foo.go` are `foo`; `test_foo.py`
    is `foo`; `FooTest.java` is `foo`."""
    return _TEST_AFFIX.sub("", Path(rel).name.split(".")[0]).lower()


def skipped(rel: str) -> bool:
    """True when ``rel`` sits under a dependency, build or hidden directory."""
    return any(part in SKIP_DIRS or part.startswith(".") for part in Path(rel).parts[:-1])


def tree_key(root: Path, skip: str = "") -> str:
    """HEAD plus every uncommitted change, as one hash: two runs with the same key ran on the same code."""
    h = hashlib.sha256(head(root).encode())
    status = git(root, "status", "--porcelain=v1", "-z", "--untracked-files=all").stdout.split("\0")
    for entry in sorted(e for e in status if len(e) > 3):
        rel = entry[3:]
        if skip and rel.startswith(skip):
            continue
        h.update(entry.encode())
        f = Path(root) / rel
        if f.is_file():
            h.update(f.read_bytes())
    return h.hexdigest()[:16]


@contextmanager
def queued(path: Path):
    """Hold an exclusive lock on ``path`` for the block: agents that share one machine take turns."""
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def branch_and_tag_refs(root: Path) -> list[str]:
    return sorted(x for x in git(root, "for-each-ref", "--format=%(refname)", "refs/heads", "refs/tags").stdout.splitlines() if x)


def is_doc_file(rel: str) -> bool:
    return rel.lower().endswith(DOC_SUFFIXES) or rel.startswith("docs/")


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha256_file(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest() if Path(path).is_file() else ""


def read_json(path: Path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def write_json(path: Path, data) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=False) + "\n", encoding="utf-8")
    tmp.replace(path)


def run_dir(root: Path, run: str) -> Path:
    return Path(root) / ARCHIVE / run


@contextmanager
def _locked(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path.with_suffix(".lock"), "a+") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def load_state(rdir: Path) -> dict:
    return read_json(Path(rdir) / "state.json", {}) or {}


@contextmanager
def update_state(rdir: Path):
    """Read state.json under an exclusive lock, let the caller change it, write it back."""
    path = Path(rdir) / "state.json"
    with _locked(path):
        state = read_json(path, {}) or {}
        yield state
        write_json(path, state)


def mark(state: dict, step: str, event: str = "done", **extra) -> None:
    """Record a step event with a timestamp; 'done' also adds the step to steps_done."""
    state.setdefault("events", []).append({"step": step, "event": event, "at": now(), **extra})
    if event == "done" and step not in state.setdefault("steps_done", []):
        state["steps_done"].append(step)


def git(root: Path, *args: str, timeout: int = 60, check: bool = False) -> subprocess.CompletedProcess:
    r = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=timeout)
    if check and r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {r.stderr.strip() or r.stdout.strip()}")
    return r


def head(root: Path) -> str:
    return git(root, "rev-parse", "HEAD").stdout.strip()


def git_retry(root: Path, *args: str, tries: int = 30) -> subprocess.CompletedProcess:
    """git with a retry while another process holds the index lock (parallel builders)."""
    r = git(root, *args)
    for _ in range(tries):
        if r.returncode == 0 or "index.lock" not in (r.stderr + r.stdout):
            return r
        time.sleep(1)
        r = git(root, *args)
    return r


def tail(text: str, lines: int = 60) -> str:
    return "\n".join((text or "").rstrip().splitlines()[-lines:])
