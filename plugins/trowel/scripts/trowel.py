#!/usr/bin/env python3
"""trowel's code steps, one command each. Every command prints one JSON line.

    trowel args        SPEC [--max-rounds N] [--max-agents N] [--resume RUN]
    trowel init        --spec PATH --run NAME [--max-rounds N] [--max-agents N] [--no-branch]
    trowel preflight   --run NAME [--command CMD [--cwd DIR] [--need CMD ...] | --none]
    trowel plan        --run NAME
    trowel commit      --run NAME --as ACTOR [--also SLICES] -m MESSAGE [PATH ...]
    trowel integration --run NAME
    trowel checks      --run NAME
    trowel verify      --run NAME --round N
    trowel merge-spec  --run NAME --parts N
    trowel resume      --run NAME
    trowel report      --run NAME
    trowel index       --spec PATH

Global: --root DIR (the project; default the current directory), --job KEY
and --calls JSON. With --job the command runs detached and this process waits
up to TROWEL_JOB_WAIT seconds (default 540) for it. If it has not finished it
prints {"pending": true, ...}; running the same command again waits again,
and once it has finished prints its result. That keeps a long build or
integration suite alive past one tool call's time limit. --calls records the
workflow's agent-call count in the run's state, for the report.

Exit codes: 0 when a JSON result was printed (read "ok" in it), 2 on a usage error.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import detect  # noqa: E402
import gates  # noqa: E402
import land  # noqa: E402
import report  # noqa: E402
import seal  # noqa: E402
import spec_index  # noqa: E402
from common import ARCHIVE, branch_and_tag_refs, git, head, load_state, mark, read_json, run_dir, tree_key, update_state, write_json  # noqa: E402


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40] or "run"


def run_name(spec: str) -> str:
    p = Path(spec)
    return f"{_slug(p.parent.name if p.stem.lower() in ('spec', 'readme') else p.stem)}-{datetime.now():%Y%m%d-%H%M%S}"


def cmd_args(root: Path, a) -> dict:
    """/trowel:build's arguments, parsed by code, with the roots and run name the workflow needs."""
    if not a.spec and not a.resume:
        return {"ok": False, "stop": "usage: /trowel:build <spec> [--max-rounds N] [--max-agents N] [--resume RUN]"}
    if a.max_rounds < 0:
        return {"ok": False, "stop": "--max-rounds must be 0 or more"}
    if a.max_agents < 4:
        return {"ok": False, "stop": "--max-agents must be at least 4 (init, build, verify and report)"}
    if a.spec and not a.resume and not ((root / a.spec).is_file() or Path(a.spec).is_file()):
        return {"ok": False, "stop": f"no spec at {a.spec}"}
    return {"ok": True, "stop": "", "args": {
        "plugin_root": str(Path(__file__).resolve().parents[1]), "project_root": str(root), "spec": a.spec or "",
        "run": a.resume or run_name(str((root / a.spec).resolve())), "max_rounds": a.max_rounds, "max_agents": a.max_agents, "resume": a.resume}}


def _plan_summary(state: dict, rdir: Path) -> dict | None:
    plan = state.get("plan")
    if not plan:
        return None
    return {"mode": plan.get("mode", ""), "waves": plan.get("waves", []),
            "slices": [{"id": sid, "title": s.get("title", ""), "requirement_ids": s.get("requirement_ids", []),
                        "files": s.get("files", []), "depends_on": s.get("depends_on", []),
                        "brief": str(rdir / "slices" / f"{sid}.md") if (rdir / "slices" / f"{sid}.md").is_file() else ""}
                       for sid, s in (plan.get("slices") or {}).items()],
            "shares": gates.shares(plan, read_json(rdir / "spec-index.json", {}) or {})}


def cmd_init(root: Path, a) -> dict:
    if git(root, "rev-parse", "--git-dir").returncode != 0:
        return {"ok": False, "stop": f"{root} is not a git repository"}
    spec = Path(a.spec)
    spec = spec if spec.is_absolute() else root / spec
    if not spec.is_file():
        return {"ok": False, "stop": f"no spec at {spec}"}
    sl = seal.check(spec)
    if sl["status"] == "broken":
        return {"ok": False, "stop": f"the spec's seal does not hold: {sl['message']}", "seal": sl}
    dirty = [ln for ln in git(root, "status", "--porcelain", "--untracked-files=no").stdout.splitlines()
             if not ln[3:].startswith(f"{ARCHIVE}/")]
    if dirty:
        return {"ok": False, "stop": "the working tree has uncommitted changes; commit or stash them first:\n" + "\n".join(dirty[:20])}
    untracked = [ln[3:] for ln in git(root, "status", "--porcelain", "--untracked-files=all").stdout.splitlines()
                 if ln.startswith("??") and not ln[3:].startswith(f"{ARCHIVE}/")]
    text = spec.read_text(encoding="utf-8")
    index = spec_index.index_text(text)
    index["spec"] = str(spec)
    if not index["ids"]:
        return {"ok": False, "stop": f"{spec} has no requirements to build: no requirement IDs and no list items"}
    rdir = run_dir(root, a.run)
    if (rdir / "state.json").exists():
        return {"ok": False, "stop": f"run {a.run} already exists; continue it with --resume {a.run}"}
    rdir.mkdir(parents=True, exist_ok=True)
    (root / ARCHIVE / ".gitignore").write_text("*\n", encoding="utf-8")
    base = head(root)
    branch = git(root, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    if not a.no_branch:
        r = git(root, "checkout", "-q", "-b", f"trowel/{a.run}")
        if r.returncode != 0:
            return {"ok": False, "stop": f"could not create branch trowel/{a.run}: {r.stderr.strip()}"}
        branch = f"trowel/{a.run}"
    write_json(rdir / "spec-index.json", index)
    (rdir / "spec.md").write_text(text, encoding="utf-8")
    commands = detect.detect(root)
    try:
        spec_rel = str(spec.resolve().relative_to(root.resolve()))
    except ValueError:
        spec_rel = ""
    size = gates.measure(root, text, index, spec_rel)
    found = detect.integration(root, text)
    warnings = []
    if sl["status"] == "unsealed":
        warnings.append(sl["message"])
    if index["synthetic"]:
        warnings.append(f"the spec tags no requirement IDs; its {len(index['ids'])} top-level list items are numbered SPEC-1.. instead")
    if not commands["build"] and not commands["test"]:
        warnings.append("no build or test command was detected; verification runs the integration check only")
    if untracked:
        warnings.append(f"{len(untracked)} untracked files are left alone: {', '.join(untracked[:8])}")
    with update_state(rdir) as st:
        st.update({"run": a.run, "root": str(root), "spec": str(spec), "spec_sha": index["sha256"], "base_commit": base,
                   "branch": branch, "max_rounds": a.max_rounds, "max_agents": a.max_agents, "commands": commands,
                   "measure": size, "integration_asked": found["asked"], "integration": found["found"],
                   "untracked_at_start": untracked, "refs_at_start": branch_and_tag_refs(root), "concerns": list(warnings), "resumes": 0})
        mark(st, "init")
        if size["fits"]:
            st["plan"] = gates.single_plan(index)
            mark(st, "plan", by="measure")
    out = {"ok": True, "stop": "", "run": a.run, "run_dir": str(rdir), "base_commit": base, "branch": branch,
           "seal": sl["status"], "ids": len(index["ids"]), "commands": commands, "measure": size,
           "mode": "single" if size["fits"] else "plan", "warnings": warnings, "epoch": 0,
           "finder": found["asked"] and not found["found"], "blocked": "", "plan": _plan_summary(load_state(rdir), rdir)}
    if not out["finder"]:
        pf = land.preflight(root, rdir, found["found"])
        out.update({"integration": pf["integration"], "blocked": pf["stop"], "preflight": pf["ran"]})
        out["warnings"] += pf.get("warnings") or []
    return out


def cmd_preflight(root: Path, a, rdir: Path) -> dict:
    state = load_state(rdir)
    if a.none:
        integ = None
    elif a.command:
        integ = detect.describe(root, a.command, a.cwd, "named by the finder agent")
    else:
        # A resume after "fix this, then continue": the repository may have changed since
        # init named the check, so name it again from the stored spec; a check the finder
        # agent named stays when detection finds nothing.
        found = detect.integration(root, (rdir / "spec.md").read_text(encoding="utf-8"))
        integ = found["found"] or state.get("integration")
    pf = land.preflight(root, rdir, integ, a.need or [])
    return {"ok": True, "blocked": pf["stop"], "integration": pf["integration"], "ran": pf["ran"],
            "warnings": pf.get("warnings") or []}


def cmd_plan(root: Path, rdir: Path) -> dict:
    index = read_json(rdir / "spec-index.json", {})
    p = gates.check_plan(root, rdir, index)
    if not p["ok"]:
        return {"ok": True, "passed": False, "errors": p["errors"], "warnings": p.get("warnings", [])}
    briefs = gates.render_briefs(root, rdir, index, read_json(rdir / "plan.json", {}), p)
    with update_state(rdir) as st:
        st["plan"] = {"mode": p["mode"], "waves": p["waves"],
                      "slices": {sid: {k: s.get(k) for k in ("title", "files", "requirement_ids", "depends_on", "owns_all")}
                                 for sid, s in briefs["slices"].items()}}
        st.setdefault("concerns", []).extend((p.get("warnings") or []) + (p.get("notes") or []))
        mark(st, "plan", by="planner")
    return {"ok": True, "passed": True, "errors": [], "warnings": p["warnings"], "notes": p["notes"],
            "plan": _plan_summary(load_state(rdir), rdir)}


def _spec_open(rdir: Path, rnd: int) -> list[dict] | None:
    """The gaps still open after round ``rnd``'s spec check, or None when it has no readable result."""
    first = read_json(rdir / "checks" / "spec-r1.json")
    if not isinstance(first, dict) or not isinstance(first.get("gaps"), list):
        return None
    gaps = {g.get("id"): g for g in first["gaps"] if isinstance(g, dict) and g.get("id")}
    brief = lambda g: {"id": g["id"], "file": g.get("file", ""), "requirement_ids": g.get("requirement_ids") or []}  # noqa: E731
    if rnd == 1:
        return [brief(g) for g in gaps.values()]
    o = read_json(rdir / "checks" / f"spec-r{rnd}.json")
    if not isinstance(o, dict) or not isinstance(o.get("rechecked"), list):
        return None
    return [brief(gaps[r["id"]]) for r in o["rechecked"]
            if isinstance(r, dict) and r.get("status") != "fixed" and r.get("id") in gaps]


def cmd_merge_spec(rdir: Path, parts: int) -> dict:
    """Join the round-1 spec checkers' files into checks/spec-r1.json, gaps renumbered G-001.. in share order.

    A share whose file is missing or unreadable leaves its requirements
    without a verdict, which the report shows as not checked."""
    merged = {"round": 1, "method_note": "", "verdicts": [], "gaps": [], "scenario_coverage": [], "patterns": []}
    missing, notes = [], []
    for k in range(1, parts + 1):
        part = read_json(rdir / "checks" / f"spec-r1-{k}.json")
        if not isinstance(part, dict) or not isinstance(part.get("verdicts"), list):
            missing.append(k)
            continue
        merged["verdicts"] += [v for v in part["verdicts"] if isinstance(v, dict)]
        for g in part.get("gaps") or []:
            if isinstance(g, dict):
                merged["gaps"].append({**g, "id": f"G-{len(merged['gaps']) + 1:03d}", "share": k, "share_id": g.get("id")})
        for key in ("scenario_coverage", "patterns"):
            v = part.get(key)
            merged[key] += v if isinstance(v, list) else [v] if v else []
        if part.get("method_note"):
            notes.append(f"share {k}: {part['method_note']}")
    merged["method_note"] = "\n".join(notes)
    merged["missing_shares"] = missing
    write_json(rdir / "checks" / "spec-r1.json", merged)
    return {"ok": True, "missing": missing, "open": _spec_open(rdir, 1)}


def cmd_resume(root: Path, a, rdir: Path) -> dict:
    state = load_state(rdir)
    branch = git(root, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    if branch != state.get("branch"):
        return {"ok": False, "stop": f"run {a.run} builds on branch {state.get('branch')}; check it out first (now on {branch})"}
    with update_state(rdir) as st:
        st["resumes"] = int(st.get("resumes") or 0) + 1
        epoch = st["resumes"]
    done = state.get("steps_done") or []
    rounds = {}
    for n in sorted(int(s.split(":")[1]) for s in done if s.startswith("verify:")):
        v = read_json(rdir / "checks" / f"verify-r{n}.json", {}) or {}
        rounds[str(n)] = {"verify": v.get("failures"),
                          "spec": _spec_open(rdir, n) if (rdir / "checks" / f"spec-r{n}.json").is_file() else None,
                          "fixed": any((rdir / "fixes").glob(f"r{n}-*.json"))}
    built = sorted({actor for actor in (state.get("commits") or {}) if not actor.startswith("fix:")})
    return {"ok": True, "stop": "", "run": a.run, "run_dir": str(rdir), "base_commit": state.get("base_commit"),
            "branch": state.get("branch"), "steps_done": done, "epoch": epoch, "max_rounds": state.get("max_rounds", 2),
            "max_agents": state.get("max_agents", 60), "commands": state.get("commands", {}),
            "integration": state.get("integration"), "measure": state.get("measure"),
            "finder": "preflight" not in done and bool(state.get("integration_asked")) and not state.get("integration"),
            "plan": _plan_summary(state, rdir), "built": built, "rounds": rounds,
            "slice_checks": sorted(int(p.stem.split("-")[-1]) for p in (rdir / "checks").glob("spec-r1-*.json") if p.stem.split("-")[-1].isdigit())}


def _dispatch(root: Path, a) -> dict:
    if a.cmd == "args":
        return cmd_args(root, a)
    if a.cmd == "init":
        return cmd_init(root, a)
    if a.cmd == "index":
        return spec_index.index_file(Path(a.spec))
    rdir = run_dir(root, a.run)
    if not (rdir / "state.json").exists():
        return {"ok": False, "stop": f"no run {a.run} under {root / ARCHIVE}"}
    if a.cmd == "resume":
        return cmd_resume(root, a, rdir)
    if a.cmd == "preflight":
        return cmd_preflight(root, a, rdir)
    if a.cmd == "plan":
        return cmd_plan(root, rdir)
    if a.cmd == "commit":
        return land.commit(root, rdir, a.actor, a.message, a.paths, tuple(x for x in a.also.split(",") if x))
    if a.cmd == "integration":
        return land.integration(root, rdir)
    if a.cmd == "checks":
        return land.agent_checks(root, rdir)
    if a.cmd == "verify":
        return land.verify(root, rdir, a.round)
    if a.cmd == "merge-spec":
        return cmd_merge_spec(rdir, a.parts)
    if a.cmd == "report":
        return {"ok": True, **report.build(root, rdir)}
    return {"ok": False, "error": f"unknown command {a.cmd}"}


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="trowel", description=__doc__.split("\n\n")[0])
    p.add_argument("--root", default=os.getcwd())
    p.add_argument("--job", default="")
    p.add_argument("--calls", default="")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("args")
    s.add_argument("spec", nargs="?", default="")
    s.add_argument("--max-rounds", type=int, default=2)
    s.add_argument("--max-agents", type=int, default=60)
    s.add_argument("--resume", default="")
    s = sub.add_parser("init")
    s.add_argument("--spec", required=True)
    s.add_argument("--run", required=True)
    s.add_argument("--max-rounds", type=int, default=2)
    s.add_argument("--max-agents", type=int, default=60)
    s.add_argument("--no-branch", action="store_true")
    s = sub.add_parser("index")
    s.add_argument("--spec", required=True)
    for name in ("resume", "integration", "checks", "report", "plan"):
        sub.add_parser(name).add_argument("--run", required=True)
    s = sub.add_parser("preflight")
    s.add_argument("--run", required=True)
    s.add_argument("--command", default="")
    s.add_argument("--cwd", default="")
    s.add_argument("--need", action="append")
    s.add_argument("--none", action="store_true")
    s = sub.add_parser("commit")
    s.add_argument("--run", required=True)
    s.add_argument("--as", dest="actor", required=True)
    s.add_argument("-m", dest="message", required=True)
    s.add_argument("--also", default="", help="fixers: slices no fixer works on this round, comma-separated")
    s.add_argument("paths", nargs="*")
    s = sub.add_parser("merge-spec")
    s.add_argument("--run", required=True)
    s.add_argument("--parts", type=int, required=True)
    s = sub.add_parser("verify")
    s.add_argument("--run", required=True)
    s.add_argument("--round", type=int, required=True)
    return p


def _job(root: Path, a, argv: list[str]) -> dict:
    """Run this command detached under ``--job KEY`` and wait for its result."""
    jobs = run_dir(root, a.run) / "jobs"
    jobs.mkdir(parents=True, exist_ok=True)
    out, pid_file = jobs / f"{a.job}.json", jobs / f"{a.job}.pid"
    if out.exists() and a.cmd in ("integration", "checks"):
        # A finished check is an answer about the code it ran on; once the code changes, run it again.
        done = read_json(out, {}) or {}
        if done.get("tree") and done.get("tree") != tree_key(root, f"{ARCHIVE}/"):
            out.unlink()
    if not out.exists():
        alive = False
        if pid_file.exists():
            try:
                os.kill(int(pid_file.read_text().strip()), 0)
                alive = True
            except (OSError, ValueError):
                alive = False
        if not alive:
            child = [sys.executable, str(Path(__file__).resolve()), *[x for i, x in enumerate(argv)
                                                                     if x != "--job" and (i == 0 or argv[i - 1] != "--job")]]
            proc = subprocess.Popen(child, cwd=str(root), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                    start_new_session=True, env={**os.environ, "TROWEL_JOB_OUT": str(out)})
            pid_file.write_text(str(proc.pid))
    deadline = time.monotonic() + int(os.environ.get("TROWEL_JOB_WAIT", "540"))
    while time.monotonic() < deadline:
        if out.exists():
            return read_json(out, {"ok": False, "error": f"job {a.job} wrote an unreadable result"})
        try:
            os.kill(int(pid_file.read_text().strip()), 0)
        except (OSError, ValueError):
            if out.exists():
                continue
            return {"ok": False, "error": f"job {a.job} ended without a result"}
        time.sleep(2)
    return {"pending": True, "job": a.job, "hint": "still running; run the same command again to keep waiting"}


def _record_calls(root: Path, a) -> None:
    """Keep the workflow's agent-call count for this epoch of the run."""
    try:
        calls = json.loads(a.calls)
    except ValueError:
        return
    rdir = run_dir(root, a.run)
    if isinstance(calls, dict) and (rdir / "state.json").exists():
        with update_state(rdir) as st:
            st.setdefault("agent_calls", {})[str(calls.get("epoch", 0))] = calls


def main(argv: list[str]) -> int:
    try:
        a = _parser().parse_args(argv)
    except SystemExit as exc:
        return 2 if exc.code else 0
    root = Path(a.root).resolve()
    if a.calls and getattr(a, "run", None) and not os.environ.get("TROWEL_JOB_OUT"):
        _record_calls(root, a)
    if a.job and getattr(a, "run", None):
        result = _job(root, a, argv)
    else:
        try:
            result = _dispatch(root, a)
        except Exception as exc:  # one JSON line even when a step crashes, so the caller can report it
            result = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        if os.environ.get("TROWEL_JOB_OUT"):
            write_json(Path(os.environ["TROWEL_JOB_OUT"]), result)
    print(json.dumps(result, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
