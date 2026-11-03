"""Shared fixtures: a small git project with a Makefile build and test, and a
helper that runs trowel's CLI in-process and returns its JSON."""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
from contextlib import redirect_stdout
from pathlib import Path

import pytest

# The fixture rewrites calc.py within one second at the same size; a cached .pyc
# would hide the change.
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"

PLUGIN = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN / "scripts"))

import trowel  # noqa: E402

SPEC = """---
spec_format_version: v2.1
---

# Specification: calculator

## Global Invariants

- **GI-001**: never print to stdout from library code

## Requirements

- **FR-001**: `add(a, b)` returns the sum of a and b.
- **FR-002**: `sub(a, b)` returns a minus b.
"""

CALC = "def add(a, b):\n    return a + b\n"
TEST = "from calc import add\n\nassert add(2, 3) == 5\nprint('ok')\n"
MAKEFILE = "build:\n\tpython3 -c \"import ast; ast.parse(open('calc.py').read())\"\n\ntest:\n\tpython3 tests/run.py\n"
RUNNER = ("import glob, runpy, sys\nsys.path.insert(0, '.')\nbad = []\n"
          "for f in sorted(glob.glob('tests/test_*.py')):\n"
          "    try:\n        runpy.run_path(f)\n    except Exception as e:\n        bad.append(f)\n        print(f'FAILED {f} - {e!r}')\n"
          "sys.exit(1 if bad else 0)\n")


def git(root: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True, text=True).stdout.strip()


def write(root: Path, rel: str, text: str) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)


@pytest.fixture
def repo(tmp_path) -> Path:
    root = tmp_path / "proj"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.email", "t@example.com")
    git(root, "config", "user.name", "t")
    git(root, "config", "commit.gpgsign", "false")
    for rel, text in (("spec.md", SPEC), ("calc.py", CALC), ("tests/test_calc.py", TEST), ("Makefile", MAKEFILE),
                      ("tests/run.py", RUNNER), (".gitignore", "__pycache__/\n")):
        write(root, rel, text)
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "base")
    return root


def j(root: Path, *args: str) -> dict:
    buf = io.StringIO()
    with redirect_stdout(buf):
        code = trowel.main(["--root", str(root), *args])
    assert code == 0, buf.getvalue()
    return json.loads(buf.getvalue().strip().splitlines()[-1])


@pytest.fixture
def run(repo):
    """An initialised run on the fixture project: (root, run name, run dir)."""
    r = j(repo, "init", "--spec", "spec.md", "--run", "t1")
    assert r["ok"], r
    return repo, "t1", repo / "trowel-archive" / "t1"


def single_plan(**over):
    p = {"mode": "single", "slices": [{"id": "s1", "title": "calc", "kind": "behaviour",
                                         "requirement_ids": ["GI-001", "FR-001", "FR-002"], "files": ["calc.py"],
                                         "anchors": [{"at": "calc.py:1", "fact": "add is the only function"}],
                                         "patterns": [{"for": "calc.py", "from": "calc.py:1-2"}], "new_loc": 10}]}
    p.update(over)
    return p
