import json
import os
import subprocess
import sys

from conftest import PLUGIN, git, j, single_plan, write

from common import load_state, write_json


def test_args_names_the_run_and_checks_flags(repo):
    r = j(repo, "args", "spec.md", "--max-rounds", "1")
    assert r["ok"] and r["args"]["run"].startswith("proj-") and r["args"]["max_rounds"] == 1
    assert r["args"]["max_agents"] == 60 and r["args"]["plugin_root"] == str(PLUGIN)
    assert j(repo, "args", "--resume", "r9")["args"]["run"] == "r9"
    assert "--max-rounds must be 0 or more" in j(repo, "args", "spec.md", "--max-rounds", "-1")["stop"]
    assert "--max-agents must be at least 4" in j(repo, "args", "spec.md", "--max-agents", "3")["stop"]
    assert "no spec at" in j(repo, "args", "nope.md")["stop"]


def test_init_measures_plans_single_and_runs_preflight(repo):
    r = j(repo, "init", "--spec", "spec.md", "--run", "r1", "--max-rounds", "3")
    assert r["ok"] and not r["blocked"] and r["branch"] == "trowel/r1" and r["ids"] == 3 and r["seal"] == "unsealed"
    assert r["mode"] == "single" and r["measure"]["fits"] and not r["finder"]
    assert [x["what"] for x in r["preflight"]] == ["the build at the base commit"]
    st = load_state(repo / "trowel-archive" / "r1")
    assert {"init", "preflight", "plan"} <= set(st["steps_done"])
    assert st["plan"]["slices"]["all"]["owns_all"] and st["max_rounds"] == 3
    assert git(repo, "status", "--porcelain") == ""  # the archive ignores itself
    again = j(repo, "init", "--spec", "spec.md", "--run", "r1", "--no-branch")
    assert not again["ok"] and "--resume r1" in again["stop"]


def test_init_stops_on_a_dirty_tree_a_missing_spec_or_no_requirements(repo):
    write(repo, "calc.py", "changed\n")
    assert "uncommitted changes" in j(repo, "init", "--spec", "spec.md", "--run", "a")["stop"]
    git(repo, "checkout", "--", "calc.py")
    assert "no spec at" in j(repo, "init", "--spec", "nope.md", "--run", "b")["stop"]
    write(repo, "empty.md", "# nothing here\n\nJust prose.\n")
    git(repo, "add", "-A"); git(repo, "commit", "-qm", "e")
    assert "no requirements to build" in j(repo, "init", "--spec", "empty.md", "--run", "c")["stop"]


def test_init_stops_on_a_broken_seal(repo, monkeypatch):
    monkeypatch.setenv("TROWEL_DREW_VERIFIER", "none")
    write(repo, "sealed.md", "---\nsealed_by: validate-spec.py sha256:" + "0" * 64 + "\n---\n\n- FR-1: x\n")
    git(repo, "add", "-A"); git(repo, "commit", "-qm", "s")
    assert "seal does not hold" in j(repo, "init", "--spec", "sealed.md", "--run", "d")["stop"]


def test_init_blocks_when_the_integration_check_cannot_start(repo, monkeypatch):
    write(repo, "Makefile", "build:\n\ttrue\n\ntest:\n\tpython3 tests/run.py\n\ne2e:\n\tdocker compose up\n")
    write(repo, "spec.md", "- FR-1: x\n- AC-1: the end-to-end check `make e2e` passes.\n")
    git(repo, "add", "-A"); git(repo, "commit", "-qm", "e2e")
    monkeypatch.setenv("PATH", "/usr/bin:/bin")  # make is in /usr/bin; docker is not
    r = j(repo, "init", "--spec", "spec.md", "--run", "e")
    assert r["ok"] and r["integration"]["command"] == "make e2e"
    assert "The build cannot start here." in r["blocked"] and "docker info" in r["blocked"]
    assert "--resume e" in r["blocked"]
    assert "preflight" not in load_state(repo / "trowel-archive" / "e")["steps_done"]


def test_preflight_on_resume_names_the_check_again_from_the_changed_repository(repo, monkeypatch):
    write(repo, "Makefile", "build:\n\ttrue\n\ntest:\n\tpython3 tests/run.py\n\ne2e:\n\tdocker compose up\n")
    write(repo, "spec.md", "- FR-1: x\n- AC-1: the end-to-end check `make e2e` passes.\n")
    git(repo, "add", "-A"); git(repo, "commit", "-qm", "e2e")
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    assert "docker info" in j(repo, "init", "--spec", "spec.md", "--run", "g")["blocked"]
    write(repo, "Makefile", "build:\n\ttrue\n\ntest:\n\tpython3 tests/run.py\n\ne2e:\n\tpython3 tests/e2e.py\n")
    git(repo, "add", "-A"); git(repo, "commit", "-qm", "no docker")
    p = j(repo, "preflight", "--run", "g")
    assert not p["blocked"] and not any(n["command"] == "docker info" for n in p["integration"]["needs"])
    assert "preflight" in load_state(repo / "trowel-archive" / "g")["steps_done"]


def test_init_asks_for_the_finder_and_preflight_takes_its_answer(repo):
    write(repo, "spec.md", "- FR-1: x\n- AC-1: an integration test proves it.\n")
    git(repo, "add", "-A"); git(repo, "commit", "-qm", "s")
    r = j(repo, "init", "--spec", "spec.md", "--run", "f")
    assert r["finder"] and "preflight" not in r
    assert j(repo, "resume", "--run", "f")["finder"]
    p = j(repo, "preflight", "--run", "f", "--command", "make test", "--need", "python3 -V")
    assert p["ok"] and not p["blocked"] and p["integration"]["command"] == "make test"
    assert [x["what"] for x in p["ran"]][:2] == ["make on PATH", "named by the finder"]
    assert not j(repo, "resume", "--run", "f")["finder"]
    none = j(repo, "preflight", "--run", "f", "--none")
    assert none["integration"] is None and none["warnings"]


def test_plan_gate_writes_briefs_or_lists_errors(run):
    root, name, rdir = run
    write_json(rdir / "plan.json", single_plan(slices=[]))
    bad = j(root, "plan", "--run", name)
    assert bad["ok"] and not bad["passed"] and bad["errors"] == ["slices: empty"]
    write_json(rdir / "plan.json", single_plan())
    good = j(root, "plan", "--run", name)
    assert good["passed"] and good["plan"]["mode"] == "single" and good["plan"]["slices"][0]["brief"].endswith("all.md")


def test_resume_reports_rounds_and_bumps_the_epoch(run):
    root, name, rdir = run
    r = j(root, "resume", "--run", name)
    assert r["ok"] and r["epoch"] == 1 and r["rounds"] == {} and r["built"] == [] and r["plan"]["mode"] == "single"
    write(root, "calc.py", "def add(a, b):\n    return a + b\n\n\ndef sub(a, b):\n    return a - b\n")
    assert j(root, "commit", "--run", name, "--as", "all", "-m", "sub")["ok"]
    j(root, "verify", "--run", name, "--round", "1")
    write_json(rdir / "checks" / "spec-r1.json", {"verdicts": [], "gaps": [{"id": "G-001", "file": "calc.py", "requirement_ids": ["FR-002"]}]})
    r = j(root, "resume", "--run", name)
    assert r["epoch"] == 2 and r["built"] == ["all"]
    assert r["rounds"]["1"] == {"verify": [], "spec": [{"id": "G-001", "file": "calc.py", "requirement_ids": ["FR-002"]}], "fixed": False}
    git(root, "checkout", "-q", "main")
    assert "check it out first" in j(root, "resume", "--run", name)["stop"]
    assert "no run" in j(root, "resume", "--run", "ghost")["stop"]


def test_calls_are_recorded_per_epoch(run):
    root, name, rdir = run
    j(root, "--calls", json.dumps({"epoch": 0, "total": 5, "by": {"clerk": 3}, "concerns": ["c"]}), "resume", "--run", name)
    assert load_state(rdir)["agent_calls"]["0"]["total"] == 5


def test_job_runs_detached_and_returns_the_result_when_done(run):
    root, name, rdir = run
    env = {**os.environ, "TROWEL_JOB_WAIT": "60"}
    cmd = [sys.executable, str(PLUGIN / "scripts" / "trowel.py"), "--root", str(root), "--job", "v1", "verify", "--run", name, "--round", "1"]
    out = json.loads(subprocess.run(cmd, capture_output=True, text=True, env=env, check=True).stdout)
    assert out["round"] == 1 and (rdir / "jobs" / "v1.json").is_file()
    again = json.loads(subprocess.run(cmd, capture_output=True, text=True, env=env, check=True).stdout)
    assert again == out


def test_usage_error_exits_2(repo):
    assert subprocess.run([sys.executable, str(PLUGIN / "scripts" / "trowel.py"), "bogus"], capture_output=True).returncode == 2


def test_merge_spec_joins_shares_and_renumbers_gaps(run):
    root, name, rdir = run
    write_json(rdir / "checks" / "spec-r1-1.json", {"verdicts": [{"requirement_id": "FR-001", "verdict": "WRONG"}],
                                                    "gaps": [{"id": "G-001", "file": "calc.py", "requirement_ids": ["FR-001"]}], "patterns": ["p1"]})
    write_json(rdir / "checks" / "spec-r1-2.json", {"verdicts": [{"requirement_id": "FR-002", "verdict": "PARTIAL"}],
                                                    "gaps": [{"id": "G-001", "file": "", "requirement_ids": ["FR-002"]}], "method_note": "n2"})
    r = j(root, "merge-spec", "--run", name, "--parts", "3")
    assert r["ok"] and r["missing"] == [3]
    assert [g["id"] for g in r["open"]] == ["G-001", "G-002"] and r["open"][1]["requirement_ids"] == ["FR-002"]
    merged = json.loads((rdir / "checks" / "spec-r1.json").read_text())
    assert [v["requirement_id"] for v in merged["verdicts"]] == ["FR-001", "FR-002"] and merged["patterns"] == ["p1"]
    assert merged["gaps"][1]["share"] == 2 and merged["gaps"][1]["share_id"] == "G-001" and "share 2: n2" in merged["method_note"]


def test_init_hands_the_workflow_its_spec_check_shares(repo):
    r = j(repo, "init", "--spec", "spec.md", "--run", "sh1")
    assert r["plan"]["shares"] == [load_state(repo / "trowel-archive" / "sh1")["plan"]["slices"]["all"]["requirement_ids"]]
