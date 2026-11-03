from conftest import git, write

import checks
from common import head


def test_failure_names_cover_common_runners():
    out = "\n".join([
        "--- FAIL: TestRotate (0.01s)", "FAIL\tgithub.com/x/certs\t0.512s", "FAIL\tgithub.com/x/b [build failed]",
        "internal/a.go:12:3: undefined: Foo", "FAILED tests/test_x.py::test_a - AssertionError",
        " FAIL  src/a.test.ts > suite > name 12ms", "test it::works ... FAILED", "    foo_test.go:9: want 1",
    ])
    assert checks.failure_names(out) == {"TestRotate", "github.com/x/certs", "github.com/x/b", "internal/a.go: undefined: Foo",
                                         "tests/test_x.py::test_a", "src/a.test.ts > suite > name", "it::works"}


def test_run_command_reports_exit_and_timeout(tmp_path):
    assert checks.run_command("exit 3", tmp_path)["exit"] == 3
    r = checks.run_command("sleep 5", tmp_path, timeout=1)
    assert r["exit"] == 124 and "timed out" in r["tail"]


def _cmd(c):
    return {"cmd": c, "cwd": ""}


def test_new_failures_against_a_passing_base(repo, tmp_path):
    base = head(repo)
    write(repo, "tests/test_more.py", "assert False, 'new'\n")
    git(repo, "add", "-A"); git(repo, "commit", "-qm", "x")
    r = checks.new_failures(repo, tmp_path / "rd", base, [_cmd("make test"), _cmd("make build")])
    assert r[0]["status"] == "new_failures" and r[0]["new"] == ["tests/test_more.py"]
    assert r[1]["status"] == "pass"


def test_failures_the_base_already_had_are_not_new(repo, tmp_path):
    write(repo, "tests/test_old.py", "assert False, 'old'\n")
    git(repo, "add", "-A"); git(repo, "commit", "-qm", "old failure")
    base = head(repo)
    write(repo, "README.md", "x\n")
    git(repo, "add", "-A"); git(repo, "commit", "-qm", "docs")
    r = checks.new_failures(repo, tmp_path / "rd", base, [_cmd("make test")])
    assert r[0]["status"] == "pre_existing" and r[0]["new"] == []
    write(repo, "tests/test_new.py", "assert False\n")
    git(repo, "add", "-A"); git(repo, "commit", "-qm", "new failure")
    r = checks.new_failures(repo, tmp_path / "rd2", base, [_cmd("make test")])
    assert r[0]["new"] == ["tests/test_new.py"]


def test_results_are_cached_per_commit(repo, tmp_path):
    sha = head(repo)
    first = checks.run_at(repo, tmp_path, sha, _cmd("date +%s%N"))
    assert checks.run_at(repo, tmp_path, sha, _cmd("date +%s%N")) == first


def test_fail_before_pass_after(repo):
    before = head(repo)
    write(repo, "calc.py", "def add(a, b):\n    return a + b\n\n\ndef sub(a, b):\n    return a - b\n")
    write(repo, "tests/test_sub.py", "from calc import sub\nassert sub(5, 3) == 2\n")
    git(repo, "add", "-A"); git(repo, "commit", "-qm", "sub")
    ok = checks.fail_before_pass_after(repo, before, ["tests/test_sub.py"], "PYTHONPATH=. python3 tests/test_sub.py")
    assert ok["ok"], ok
    weak = checks.fail_before_pass_after(repo, before, ["tests/test_calc.py"], "PYTHONPATH=. python3 tests/test_calc.py")
    assert not weak["ok"] and "passes before the fix" in weak["why"]
    assert not checks.fail_before_pass_after(repo, before, ["tests/nope.py"], "true")["ok"]
