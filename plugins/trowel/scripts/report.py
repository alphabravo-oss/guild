"""report.md and report.json: a verdict per requirement with its evidence,
what was fixed, what remains, how long each step took and how many agents ran.

A requirement is

  MET          the spec-checker gave it VERIFIED, or every gap against it was
               re-checked and found fixed
  NOT MET      a gap against it is still open, or it got a non-VERIFIED verdict
               with no gap filed
  NOT CHECKED  the spec-checker gave it no verdict

What the code checks found (build, lint, unit tests, the integration check,
uncommitted work, IDs in production code, unpinned fixes) is reported beside
the requirements from the last verify round.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from common import head, load_state, mark, read_json, update_state, write_json


def _rounds(rdir: Path, kind: str) -> list[int]:
    return sorted(int(p.stem.split("-r")[1]) for p in (rdir / "checks").glob(f"{kind}-r*.json") if p.stem.split("-r")[1].isdigit())


def _gaps(rdir: Path) -> tuple[dict, dict]:
    """The round-1 verdicts per requirement, and every gap with its final status."""
    first = read_json(rdir / "checks" / "spec-r1.json", {}) or {}
    verdicts = {v["requirement_id"]: v for v in first.get("verdicts") or [] if isinstance(v, dict) and v.get("requirement_id")}
    gaps = {g["id"]: {**g, "status": "open", "evidence": "", "checked_in": 1}
            for g in first.get("gaps") or [] if isinstance(g, dict) and g.get("id")}
    for n in _rounds(rdir, "spec"):
        if n == 1:
            continue
        for rc in (read_json(rdir / "checks" / f"spec-r{n}.json", {}) or {}).get("rechecked") or []:
            if isinstance(rc, dict) and rc.get("id") in gaps:
                gaps[rc["id"]].update(status="fixed" if rc.get("status") == "fixed" else "open",
                                      evidence=rc.get("evidence", ""), checked_in=n)
    for path in sorted((rdir / "fixes").glob("r*-*.json")):
        for fx in (read_json(path, {}) or {}).get("fixes") or []:
            if isinstance(fx, dict) and fx.get("item") in gaps:
                gaps[fx["item"]]["fixer"] = {k: fx.get(k) for k in ("outcome", "note", "test_command")}
    return verdicts, gaps


def _needs_call(rdir: Path, gaps: dict) -> list[dict]:
    """What a fixer would not or could not change in the last fix round, for a person to decide.

    A blocked or disputed fix of a gap still open, or of any failure: trowel's
    rules stopped the fixer (a test that predates the build, a file it may not
    commit, a requirement about how the work is delivered), so retrying will
    not settle it."""
    rounds = sorted({p.name.split("-")[0] for p in (rdir / "fixes").glob("r*-*.json")}, key=lambda r: int(r[1:]) if r[1:].isdigit() else 0)
    if not rounds:
        return []
    out = []
    for path in sorted((rdir / "fixes").glob(f"{rounds[-1]}-*.json")):
        for fx in (read_json(path, {}) or {}).get("fixes") or []:
            item = str(fx.get("item", ""))
            if fx.get("outcome") in ("blocked", "disputed") and (item not in gaps or gaps[item]["status"] != "fixed"):
                out.append({"item": item, "outcome": fx["outcome"], "note": fx.get("note") or ""})
    return out


def _duration(a: str, b: str) -> str:
    try:
        s = (datetime.fromisoformat(b.replace("Z", "+00:00")) - datetime.fromisoformat(a.replace("Z", "+00:00"))).total_seconds()
    except (ValueError, AttributeError):
        return "?"
    return f"{int(s // 3600)}h{int(s % 3600 // 60):02d}m{int(s % 60):02d}s" if s >= 3600 else f"{int(s // 60)}m{int(s % 60):02d}s"


def _calls(state: dict) -> dict:
    by_epoch = state.get("agent_calls") or {}
    total, by = 0, {}
    for c in by_epoch.values():
        total += int(c.get("total") or 0)
        for k, v in (c.get("by") or {}).items():
            by[k] = by.get(k, 0) + int(v)
    return {"total": total, "by": by, "cap": state.get("max_agents"), "invocations": len(by_epoch)}


def build(root: Path, rdir: Path) -> dict:
    rdir = Path(rdir)
    state = load_state(rdir)
    index = read_json(rdir / "spec-index.json", {}) or {}
    verdicts, gaps = _gaps(rdir)
    rows = []
    for rid in index.get("ids") or []:
        mine = [g for g in gaps.values() if rid in (g.get("requirement_ids") or [])]
        open_ = [g for g in mine if g["status"] != "fixed"]
        v = verdicts.get(rid)
        if open_:
            status = "NOT MET"
        elif v and (v.get("verdict") == "VERIFIED" or mine):
            status = "MET"
        elif v:
            status = "NOT MET"
        else:
            status = "NOT CHECKED"
        rows.append({"id": rid, "status": status, "verdict": v, "gaps": [g["id"] for g in mine],
                     "fixed": bool(mine) and not open_})
    vr = _rounds(rdir, "verify")
    last = read_json(rdir / "checks" / f"verify-r{vr[-1]}.json", {}) if vr else {}
    events = state.get("events") or []
    steps, prev = [], events[0]["at"] if events else ""
    for e in events:
        if e.get("event") == "done":
            steps.append({"step": e["step"], "took": _duration(prev, e["at"]), "at": e["at"]})
            prev = e["at"]
    with update_state(rdir) as st:
        mark(st, "report")
        events = st["events"]
    total = _duration(events[0]["at"], events[-1]["at"]) if events else "?"
    counts = {s: sum(1 for r in rows if r["status"] == s) for s in ("MET", "NOT MET", "NOT CHECKED")}
    data = {"run": state.get("run"), "spec": state.get("spec"), "base_commit": state.get("base_commit"), "head": head(root),
            "mode": (state.get("plan") or {}).get("mode"), "counts": counts, "requirements": rows,
            "gaps": list(gaps.values()), "failures": (last or {}).get("failures") or [],
            "integration": (last or {}).get("integration"), "rounds": len(vr), "steps": steps, "total": total,
            "agents": _calls(state), "needs_call": _needs_call(rdir, gaps),
            "concerns": (state.get("concerns") or []) + [x for c in (state.get("agent_calls") or {}).values()
                                                         for x in c.get("concerns") or []]}
    write_json(rdir / "report.json", data)
    (rdir / "report.md").write_text(_markdown(data), encoding="utf-8")
    return {"report": str(rdir / "report.md"), "counts": counts,
            "fixed": sum(1 for g in gaps.values() if g["status"] == "fixed"),
            "open_gaps": sum(1 for g in gaps.values() if g["status"] != "fixed"),
            "failures": len(data["failures"]), "total": total, "agents": data["agents"]}


def _clip(s: str, n: int = 600) -> str:
    s = (s or "").strip()
    return s if len(s) <= n else s[:n] + " ..."


def _cell(s: str, n: int = 400) -> str:
    return _clip(s, n).replace("|", "/").replace("\n", " ")


def _markdown(d: dict) -> str:
    c, ag = d["counts"], d["agents"]
    out = [f"# trowel report: {d['run']}", "",
           f"- **{c['MET']} met, {c['NOT MET']} not met, {c['NOT CHECKED']} not checked** of {len(d['requirements'])} requirements.",
           f"- {len(d['failures'])} failures from the last code check (round {d['rounds']}); "
           f"{sum(1 for g in d['gaps'] if g['status'] == 'fixed')} gaps fixed, {sum(1 for g in d['gaps'] if g['status'] != 'fixed')} open.",
           f"- Spec `{d['spec']}`, built from {str(d['base_commit'])[:10]} to {d['head'][:10]} as "
           f"{'one builder' if d['mode'] == 'single' else 'several slices'}, in {d['total']}.",
           f"- Agent calls: {ag['total']} of a cap of {ag['cap']}"
           f"{' over ' + str(ag['invocations']) + ' invocations' if ag['invocations'] > 1 else ''}"
           f" ({', '.join(f'{k} {v}' for k, v in sorted(ag['by'].items()))})."]
    if d.get("needs_call"):
        out.append(f"- **{len(d['needs_call'])} need your call**: trowel's rules stopped the fixer, so another round would not settle them.")
    integ = d.get("integration")
    if integ:
        stale = "" if integ.get("head") == d["head"] else f" (on {str(integ.get('head'))[:10]}, not the final commit)"
        out.append(f"- Integration check `{integ['command']}`: exit {integ['exit']} in {integ['seconds']}s{stale}.")
    else:
        out.append("- Integration check: none.")
    out += ["", "## Requirements", "", "| ID | Status | Evidence |", "|---|---|---|"]
    for r in d["requirements"]:
        v = r["verdict"] or {}
        ev = f"{v.get('verdict')}: {v.get('evidence', '')} ({v.get('code_location', '')})" if v else "no verdict"
        if r["gaps"]:
            ev += f" / gaps {', '.join(r['gaps'])}{' (all fixed)' if r['fixed'] else ''}"
        out.append(f"| {r['id']} | {r['status']} | {_cell(ev)} |")
    if d.get("needs_call"):
        out += ["", "## Needs your call", ""]
        out += [f"- **{x['item']}** ({x['outcome']}): {_clip(x['note'], 500)}" for x in d["needs_call"]]
    if d["failures"]:
        out += ["", "## Remaining failures (last code check)", ""]
        for f in d["failures"]:
            out.append(f"- **{f['id']}** ({f['kind']}): {f['title']}")
    for title, want in (("Open gaps", "open"), ("Fixed gaps", "fixed")):
        gs = [g for g in d["gaps"] if g["status"] == want]
        if not gs:
            continue
        out += ["", f"## {title}", ""]
        for g in gs:
            loc = f" at `{g.get('file')}:{g.get('line')}`" if g.get("file") else ""
            out.append(f"- **{g['id']}** ({', '.join(g.get('requirement_ids') or []) or 'no requirement'}){loc}: {g.get('title', '')}")
            if g.get("evidence"):
                out.append(f"  - Re-checked in round {g['checked_in']}: {_clip(g['evidence'], 300)}")
            fx = g.get("fixer") or {}
            if fx.get("outcome") and fx.get("outcome") != "fixed":
                out.append(f"  - Fixer: {fx['outcome']}: {_clip(fx.get('note') or '', 300)}")
    if d["concerns"]:
        out += ["", "## Concerns", ""] + [f"- {x}" for x in d["concerns"]]
    out += ["", "## Timings", "", "| Step | Took | Finished |", "|---|---|---|"]
    out += [f"| {s['step']} | {s['took']} | {s['at']} |" for s in d["steps"]]
    return "\n".join(out) + "\n"
