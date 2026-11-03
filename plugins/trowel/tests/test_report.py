import json

from conftest import j, write

from common import load_state, write_json

SUB = "def add(a, b):\n    return a + b\n\n\ndef sub(a, b):\n    return a - b\n"


def _built(run):
    root, name, rdir = run
    write(root, "calc.py", SUB)
    assert j(root, "commit", "--run", name, "--as", "all", "-m", "sub")["ok"]
    return root, name, rdir


def test_report_verdicts_from_spec_checks_and_rechecks(run):
    root, name, rdir = _built(run)
    j(root, "verify", "--run", name, "--round", "1")
    write_json(rdir / "checks" / "spec-r1.json", {
        "verdicts": [{"requirement_id": "GI-001", "verdict": "VERIFIED", "evidence": "no print", "code_location": "calc.py:1"},
                     {"requirement_id": "FR-002", "verdict": "WRONG", "evidence": "order", "code_location": "calc.py:6"},
                     {"requirement_id": "FR-001", "verdict": "THIN", "evidence": "x", "code_location": "calc.py:2"}],
        "gaps": [{"id": "G-001", "requirement_ids": ["FR-002"], "title": "sub reversed", "file": "calc.py", "line": 6},
                 {"id": "G-002", "requirement_ids": ["FR-001"], "title": "add untested", "file": "calc.py", "line": 2}]})
    write_json(rdir / "fixes" / "r1-all.json", {"fixes": [{"item": "G-002", "outcome": "disputed", "note": "add is tested"}]})
    j(root, "verify", "--run", name, "--round", "2")
    write_json(rdir / "checks" / "spec-r2.json", {"rechecked": [{"id": "G-001", "status": "fixed", "evidence": "now a - b"},
                                                                 {"id": "G-002", "status": "still_open", "evidence": "no test"}]})
    j(root, "--calls", json.dumps({"epoch": 0, "total": 9, "by": {"clerk": 5, "builder": 1}, "concerns": ["from the workflow"]}),
      "report", "--run", name)
    d = json.loads((rdir / "report.json").read_text())
    status = {r["id"]: r["status"] for r in d["requirements"]}
    assert status == {"GI-001": "MET", "FR-001": "NOT MET", "FR-002": "MET"}
    assert d["counts"] == {"MET": 2, "NOT MET": 1, "NOT CHECKED": 0}
    assert d["agents"]["total"] == 9 and d["agents"]["by"]["clerk"] == 5 and d["agents"]["cap"] == 40
    md = (rdir / "report.md").read_text()
    assert "**2 met, 1 not met, 0 not checked** of 3 requirements." in md
    assert "Agent calls: 9 of a cap of 40 (builder 1, clerk 5)." in md
    assert "- **G-002** (FR-001) at `calc.py:2`: add untested" in md and "Fixer: disputed: add is tested" in md
    assert "from the workflow" in md and "| verify:2 |" in md
    assert "report" in load_state(rdir)["steps_done"]


def test_report_without_a_spec_check_marks_requirements_not_checked(run):
    root, name, rdir = _built(run)
    j(root, "verify", "--run", name, "--round", "1")
    r = j(root, "report", "--run", name)
    assert r["counts"] == {"MET": 0, "NOT MET": 0, "NOT CHECKED": 3} and r["failures"] == 0


def test_report_lists_remaining_failures_from_the_last_round(run):
    root, name, rdir = run
    j(root, "verify", "--run", name, "--round", "1")
    r = j(root, "report", "--run", name)
    assert r["failures"] == 1
    assert "nothing was committed since the base commit" in (rdir / "report.md").read_text()


def test_report_lists_what_the_fixer_was_stopped_from_changing(run):
    root, name, rdir = _built(run)
    j(root, "verify", "--run", name, "--round", "1")
    write_json(rdir / "checks" / "spec-r1.json", {"verdicts": [], "gaps": [
        {"id": "G-001", "requirement_ids": ["FR-001"], "title": "old test contradicts the spec", "file": "calc_test.py"},
        {"id": "G-002", "requirement_ids": ["FR-002"], "title": "fixed one", "file": "calc.py"}]})
    write_json(rdir / "fixes" / "r1-all.json", {"fixes": [{"item": "G-001", "outcome": "blocked", "note": "first try"}]})
    write_json(rdir / "fixes" / "r2-all.json", {"fixes": [
        {"item": "G-001", "outcome": "blocked", "note": "the test predates the build and the spec removes what it checks"},
        {"item": "V2-1", "outcome": "disputed", "note": "not a regression"},
        {"item": "G-002", "outcome": "fixed", "note": ""}]})
    write_json(rdir / "checks" / "spec-r2.json", {"rechecked": [{"id": "G-002", "status": "fixed", "evidence": "ok"}]})
    r = j(root, "report", "--run", name)
    md = (rdir / "report.md").read_text()
    assert "**2 need your call**" in md and "## Needs your call" in md
    assert "**G-001** (blocked): the test predates the build" in md and "**V2-1** (disputed)" in md
    assert "first try" not in md  # only the last fix round counts
