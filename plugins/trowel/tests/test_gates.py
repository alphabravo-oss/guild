import json
from pathlib import Path

from conftest import single_plan

import gates
from common import write_json


def _plan(rdir, plan):
    write_json(rdir / "plan.json", plan)


INDEX = {"ids": ["GI-001", "FR-001", "FR-002"], "clauses": {"GI-001": "- **GI-001**: never print",
         "FR-001": "- **FR-001**: add", "FR-002": "- **FR-002**: sub"}, "global_sections": "## Global Invariants\n\n- **GI-001**: never print"}


# --- plan --------------------------------------------------------------
def _split(**over):
    p = {"mode": "split", "contract_block": "def sub(a: int, b: int) -> int",
         "slices": [
             {"id": "s1", "title": "add", "requirement_ids": ["GI-001", "FR-001"], "files": ["calc.py"],
              "anchors": [{"at": "calc.py:1", "fact": "add lives here"}], "new_loc": 1000},
             {"id": "s2", "title": "sub", "requirement_ids": ["GI-001", "FR-002"], "files": ["sub.py", "tests/test_sub.py"],
              "depends_on": ["s1"], "anchors": [{"at": "calc.py:2", "fact": "returns a + b"}], "new_loc": 1000}],
         "seams": [{"slices": ["s1", "s2"], "what": "sub uses add", "covered_by": "contract", "ref": "def sub"}]}
    p.update(over)
    return p


def test_single_plan_that_fits(run):
    root, _, rdir = run
    _plan(rdir, single_plan())
    r = gates.check_plan(root, rdir, INDEX)
    assert r["ok"], r["errors"]
    assert r["mode"] == "single" and r["waves"] == [["all"]]


def test_split_that_fits_one_builder_collapses(run):
    root, _, rdir = run
    p = _split()
    for s in p["slices"]:
        s["new_loc"] = 100
    _plan(rdir, p)
    r = gates.check_plan(root, rdir, INDEX)
    assert r["ok"] and r["mode"] == "single" and "fits one builder" in r["notes"][0]


def test_split_over_budget_stays_split_with_waves(run):
    root, _, rdir = run
    _plan(rdir, _split())
    r = gates.check_plan(root, rdir, INDEX)
    assert r["ok"], r["errors"]
    assert r["mode"] == "split" and r["waves"] == [["s1"], ["s2"]]


def test_single_over_budget_needs_a_reason(run):
    root, _, rdir = run
    p = single_plan()
    p["slices"][0]["new_loc"] = 5000
    _plan(rdir, p)
    assert any("over the S4 budget" in e for e in gates.check_plan(root, rdir, INDEX)["errors"])
    p["single_reason"] = "no split leaves each slice a testable requirement"
    _plan(rdir, p)
    assert gates.check_plan(root, rdir, INDEX)["ok"]


def _errors(run, plan):
    root, _, rdir = run
    _plan(rdir, plan)
    return " | ".join(gates.check_plan(root, rdir, INDEX)["errors"])


def test_s2_behaviour_slice_must_own_a_requirement(run):
    p = _split()
    p["slices"][1]["requirement_ids"] = []
    e = _errors(run, p)
    assert "s2: writes behaviour but owns no requirement (S2)" in e and "requirements no slice owns: FR-002" in e


def test_docs_slice_may_own_nothing_only_if_it_writes_docs(run):
    p = _split()
    p["slices"].append({"id": "d", "kind": "docs", "files": ["docs/ops.md", "x.go"], "anchors": [{"at": "calc.py:1", "fact": "f"}]})
    assert "d: a docs slice may own no requirement only if it writes docs; it lists x.go" in _errors(run, p)


def test_file_overlap_including_directories_and_case(run):
    p = _split()
    p["slices"][1]["files"] = ["CALC.py"]
    assert "s1 and s2 both own" in _errors(run, p)
    p["slices"][0]["files"] = ["pkg/"]
    p["slices"][1]["files"] = ["pkg/x.go"]
    assert "s1 and s2 both own pkg/" in _errors(run, p)


def test_unknown_dependency_and_cycle(run):
    p = _split()
    p["slices"][1]["depends_on"] = ["nope"]
    assert "unknown slice 'nope'" in _errors(run, p)
    p["slices"][1]["depends_on"] = ["s1"]
    p["slices"][0]["depends_on"] = ["s2"]
    assert "depends_on has a cycle" in _errors(run, p)


def test_s3_more_than_two_owners_needs_a_reason(run):
    p = _split()
    p["slices"].append({"id": "s3", "requirement_ids": ["GI-001"], "files": ["c.py"], "anchors": [{"at": "calc.py:1", "fact": "f"}], "new_loc": 1000})
    assert "GI-001 has 3 owners" in _errors(run, p)
    p["slices"][2]["split_reason"] = {"GI-001": "each owner keeps its own module free of prints"}
    assert "GI-001 has 3 owners" not in _errors(run, p)


def test_s6_s7_s10_contract_anchors_and_seams(run):
    p = _split(contract_block="", seams=[])
    p["slices"][1]["anchors"] = [{"at": "calc.py:99", "fact": "x"}]
    e = _errors(run, p)
    assert "contract_block is empty" in e
    assert "calc.py has 2 lines" in e
    assert "s1 and s2 depend on each other but no seam" in e


def test_seam_coverage_is_checked_against_what_it_cites(run):
    p = _split(seams=[{"slices": ["s1", "s2"], "covered_by": "co_owned", "ref": "FR-001"}])
    assert "FR-001 is said to be co-owned but s2 does not own it" in _errors(run, p)
    p = _split(seams=[{"slices": ["s1", "s2"], "covered_by": "contract", "ref": "func Missing"}])
    assert "ref is not text from contract_block" in _errors(run, p)
    p = _split(seams=[{"slices": ["s1", "s2"], "covered_by": "co_owned", "ref": "GI-001"}])
    assert _errors(run, p) == ""


def test_pattern_must_resolve(run):
    p = single_plan()
    p["slices"][0]["patterns"] = [{"for": "calc.py", "from": "calc.py:1-50"}]
    assert "must name an existing file and a line range" in _errors(run, p)


def test_briefs_carry_verbatim_clauses_and_the_same_contract(run):
    root, _, rdir = run
    p = _split()
    _plan(rdir, p)
    r = gates.check_plan(root, rdir, INDEX)
    out = gates.render_briefs(root, rdir, INDEX, p, r)
    b1 = (rdir / "slices" / "s1.md").read_text()
    b2 = (rdir / "slices" / "s2.md").read_text()
    assert "- **FR-001**: add" in b1 and "- **FR-002**: sub" in b2 and "FR-002**: sub" not in b1
    assert "Co-owned with s2" in b1
    block = "## Cross-slice contract (identical in every slice's brief)\n\ndef sub(a: int, b: int) -> int"
    assert block in b1 and block in b2
    assert "## Rules that bind every slice" in b1
    assert "`calc.py:1`: add lives here" in b1
    assert set(out["briefs"]) == {"s1", "s2"}


def test_single_brief_owns_everything_and_has_patterns(run):
    root, _, rdir = run
    p = single_plan()
    _plan(rdir, p)
    r = gates.check_plan(root, rdir, INDEX)
    out = gates.render_briefs(root, rdir, INDEX, p, r)
    b = (rdir / "slices" / "all.md").read_text()
    assert out["slices"]["all"]["owns_all"] and "the whole repository" in b
    assert "```\ndef add(a, b):\n    return a + b\n```" in b


# --- measure -----------------------------------------------------------
def test_measure_counts_the_paths_the_spec_names(run):
    root, _, _ = run
    text = "Change `calc.py` and tests/test_calc.py:3; see spec.md and `nope/x.go`, https://x.io/a/b.\n"
    m = gates.measure(root, text, INDEX, "spec.md")
    assert m["paths"] == ["calc.py", "tests/test_calc.py"]
    assert m["entries"] == 2 and m["read"] == 6 and m["new"] == 3 * gates.NEW_LOC_PER_REQUIREMENT and m["fits"]


def test_measure_over_budget_on_entries_or_requirements(run):
    root, _, _ = run
    for i in range(11):
        (root / f"m{i}.py").write_text("x\n")
    text = " ".join(f"`m{i}.py`" for i in range(11))
    assert not gates.measure(root, text, INDEX)["fits"]
    many = {"ids": [f"FR-{i}" for i in range(100)]}
    assert not gates.measure(root, "", many)["fits"]


def test_single_plan_owns_everything():
    p = gates.single_plan(INDEX)
    assert p["mode"] == "single" and p["waves"] == [["all"]] and p["slices"]["all"]["owns_all"]
    assert p["slices"]["all"]["requirement_ids"] == INDEX["ids"]


def test_shares_keep_slices_together_and_cover_every_id_once():
    ids = [f"FR-{i:03d}" for i in range(1, 61)]
    plan = {"waves": [["b"], ["a"]], "slices": {"a": {"requirement_ids": ids[:30]}, "b": {"requirement_ids": ids[30:50] + ids[:2]}}}
    sh = gates.shares(plan, {"ids": ids})
    assert len(sh) == 3 and [len(x) for x in sh] == [20, 20, 20]
    assert sh[0] == ids[30:50]  # the first wave's slice leads, its own IDs together
    assert sorted(sum(sh, [])) == ids  # every ID once, the unowned ones (FR-051..060) included


def test_shares_one_checker_for_a_small_spec_and_at_most_three():
    assert gates.shares(gates.single_plan({"ids": ["FR-1", "FR-2"]}), {"ids": ["FR-1", "FR-2"]}) == [["FR-1", "FR-2"]]
    big = [f"FR-{i}" for i in range(200)]
    assert len(gates.shares(gates.single_plan({"ids": big}), {"ids": big})) == 3
    assert gates.shares({}, {"ids": []}) == [[]]


def test_measure_skips_dependency_and_hidden_directories(run):
    root, name, rdir = run
    (root / "web" / "node_modules" / "pkg").mkdir(parents=True)
    (root / "web" / "node_modules" / "pkg" / "index.js").write_text("x\n" * 5000)
    (root / "web" / ".cache").mkdir()
    (root / "web" / ".cache" / "c.js").write_text("x\n" * 5000)
    (root / "web" / "app.js").write_text("a\nb\n")
    assert gates.read_loc(root, ["web/"]) == 2


def test_a_docs_slice_starts_after_every_behaviour_slice(run):
    root, _, rdir = run
    p = _split()
    p["slices"].append({"id": "docs", "title": "docs", "kind": "docs", "requirement_ids": [], "files": ["README.md"],
                        "anchors": [{"at": "calc.py:1", "fact": "add lives here"}], "new_loc": 50})
    _plan(rdir, p)
    r = gates.check_plan(root, rdir, INDEX)
    assert r["ok"], r["errors"]
    assert r["waves"] == [["s1"], ["s2"], ["docs"]]
    assert any("docs documents what the other slices build" in n for n in r["notes"])
    assert json.loads((rdir / "plan.json").read_text())["slices"][2]["depends_on"] == ["s1", "s2"]


def test_reuse_entries_must_point_at_code_that_exists_and_reach_the_brief(run):
    root, _, rdir = run
    p = _split()
    p["slices"][0]["reuse"] = [{"need": "add two numbers", "use": "calc.add", "at": "calc.py:1"}]
    p["slices"][1]["reuse"] = [{"need": "a parser", "use": "nowhere.parse", "at": "nowhere.py:3"}]
    _plan(rdir, p)
    r = gates.check_plan(root, rdir, INDEX)
    assert any("s2: reuse 'nowhere.parse'" in e for e in r["errors"])
    p["slices"][1].pop("reuse")
    _plan(rdir, p)
    r = gates.check_plan(root, rdir, INDEX)
    assert r["ok"], r["errors"]
    assert any("s2: names nothing to reuse" in w for w in r["warnings"])
    briefs = gates.render_briefs(root, rdir, INDEX, json.loads((rdir / "plan.json").read_text()), r)
    text = Path(briefs["briefs"]["s1"]).read_text()
    assert "## Reuse these: they already exist here" in text and "add two numbers: use `calc.add` (`calc.py:1`)" in text
