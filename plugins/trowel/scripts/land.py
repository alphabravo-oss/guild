"""Landing and verifying work, in code.

``preflight``     before anything is built: the integration check's needs (its
                  runner on PATH, a Docker daemon), a dry start of the check,
                  and the build at the base commit. Any that fails stops the
                  run with the command and its output.
``commit``        how builders and fixers commit. In a split build it stages
                  only the files the actor's slice owns and refuses the rest.
``integration``   runs the spec's end-to-end check and records its exit code
                  against HEAD, so "the suite passes" is a fact code holds.
``verify``        one round's code check: every change committed, no
                  requirement IDs in production code, ownership in a split
                  build, build, lint and the full unit test suite with no
                  failure the base did not have, the integration check, and
                  (after a fix round) that each behavioural fix's test fails
                  before the fix and passes after it.

``owner_of``, ``may_commit``, ``commit``, ``integration`` and the fix check are
cut down from joiner's land.py; joiner's frozen tests and its QA commits are gone.
"""

from __future__ import annotations

from pathlib import Path

import checks
from common import (ARCHIVE, ID_RE, branch_and_tag_refs, git, git_retry, head, is_doc_file, is_test_file, load_state, mark,
                    queued, read_json, tail, tree_key, unit_of, update_state, write_json)
from gates import _norm, overlaps


def _slices(state: dict) -> dict:
    return (state.get("plan") or {}).get("slices") or {}


def _entry_owns(entry: str, path: str) -> bool:
    return overlaps(entry, path) if entry.endswith("/") else _norm(entry) == _norm(path)


def owner_of(state: dict, path: str) -> str:
    """The slice that owns ``path``: a listed file or directory, else, for a
    test file, the slice whose listed file in the same directory is the unit it
    tests (`Foo.test.tsx` and `Foo.tsx`), else the slice with any listed file in
    that directory. "" when none does."""
    slices = _slices(state)
    for sid, s in slices.items():
        if s.get("owns_all"):
            return sid
        if any(_entry_owns(e, path) for e in s.get("files") or []):
            return sid
    if is_test_file(path):
        d, unit = str(Path(path).parent).lower(), unit_of(path)
        beside = [(sid, e) for sid, s in slices.items() for e in s.get("files") or []
                  if not e.endswith("/") and str(Path(e).parent).lower() == d]
        for sid, e in beside:
            if unit_of(e) == unit:
                return sid
        if beside:
            return beside[0][0]
    return ""


def may_commit(state: dict, actor: str, path: str, also: tuple[str, ...] = ()) -> str:
    """"" when ``actor`` may commit ``path``, else why not. A fixer may also
    commit a file no slice owns, since a failure can sit outside every slice,
    and a file of a slice in ``also``: the slices no fixer works on this round,
    so no two fixers ever write the same file."""
    if path.startswith(f"{ARCHIVE}/"):
        return "the run directory is never committed"
    kind, _, sid = actor.partition(":")
    slice_id = sid if kind == "fix" else actor
    if slice_id not in _slices(state):
        return f"unknown actor {actor!r}"
    owner = owner_of(state, path)
    if owner == slice_id or (kind == "fix" and (not owner or owner in also)):
        return ""
    return f"owned by {owner or 'no slice'}"


def _changed_paths(root: Path) -> list[str]:
    out = []
    entries = git(root, "status", "--porcelain=v1", "-z", "--untracked-files=all").stdout.split("\0")
    i = 0
    while i < len(entries):
        e = entries[i]
        i += 1
        if len(e) < 4:
            continue
        if e[0] in "RC":
            i += 1  # the rename's source path follows
        out.append(e[3:])
    return [p for p in out if not p.startswith(f"{ARCHIVE}/")]


def commit(root: Path, rdir: Path, actor: str, message: str, paths: list[str], also: tuple[str, ...] = ()) -> dict:
    state = load_state(rdir)
    explicit = [p[2:] if p.startswith("./") else p for p in paths]
    targets = explicit or _changed_paths(root)
    refused = {p: why for p in targets if (why := may_commit(state, actor, p, also))}
    if explicit and refused:
        return {"ok": False, "error": "refused: you may not commit these files", "refused": refused}
    targets = [p for p in targets if p not in refused]
    if not targets:
        return {"ok": False, "error": "nothing of yours to commit", "left_uncommitted": refused}
    r = git_retry(root, "add", "-A", "--", *targets)
    if r.returncode != 0:
        return {"ok": False, "error": f"git add: {r.stderr.strip()}"}
    r = git_retry(root, "commit", "-q", "-m", message, "--only", "--", *targets)
    if r.returncode != 0:
        return {"ok": False, "error": f"git commit: {(r.stderr or r.stdout).strip()}"}
    sha = head(root)
    with update_state(rdir) as st:
        st.setdefault("commits", {}).setdefault(actor, []).append(sha)
        if also:
            st.setdefault("commit_also", {})[sha] = list(also)
    return {"ok": True, "commit": sha, "files": targets, "left_uncommitted": refused}


# ---------------------------------------------------------------------------
# Preflight
# ---------------------------------------------------------------------------
def preflight(root: Path, rdir: Path, integ: dict | None, extra_needs: list[str] = ()) -> dict:
    """Run what the integration check needs, its dry start, and the base build.
    Records the check in state and marks ``preflight`` when everything passes."""
    state = load_state(rdir)
    ran = []
    if integ:
        for n in list(integ.get("needs") or []) + [{"what": "named by the finder", "command": c} for c in extra_needs if c]:
            r = checks.run_command(n["command"], Path(root) / (integ.get("cwd") or ""), timeout=300)
            ran.append({"what": n["what"], "cmd": n["command"], "exit": r["exit"], "tail": tail(r["tail"], 15)})
        if integ.get("dry_run"):
            r = checks.run_command(integ["dry_run"], Path(root) / (integ.get("cwd") or ""), timeout=900)
            ran.append({"what": "dry start of the integration check", "cmd": integ["dry_run"], "exit": r["exit"],
                        "tail": tail(r["tail"], 15)})
    for c in state.get("commands", {}).get("build", []):
        r = checks.run_at(root, rdir, state["base_commit"], c)
        ran.append({"what": "the build at the base commit", "cmd": c["cmd"], "exit": r["exit"], "tail": tail(r["tail"], 15)})
    failed = [x for x in ran if x["exit"] != 0]
    warnings = [] if integ else ["no integration or end-to-end check: verification runs build, lint and unit tests only"]
    if integ and not integ.get("exists", True):
        warnings.append(f"`{integ['command']}` does not exist yet; the builder writes it")
    with update_state(rdir) as st:
        st["integration"] = integ
        st["preflight"] = ran
        if not failed:
            st.setdefault("concerns", []).extend(warnings)
            mark(st, "preflight")
    if failed:
        stop = "The build cannot start here. " + " ".join(
            f"{x['what']}: `{x['cmd']}` exited {x['exit']}:\n{x['tail']}\n" for x in failed)
        stop += f"Fix this, then continue with /trowel:build --resume {state.get('run')}."
        return {"ok": False, "stop": stop, "ran": ran, "integration": integ}
    return {"ok": True, "stop": "", "ran": ran, "integration": integ, "warnings": warnings}


# ---------------------------------------------------------------------------
# Integration and verify
# ---------------------------------------------------------------------------
def integration(root: Path, rdir: Path) -> dict:
    """Run the spec's end-to-end check, one agent at a time, and reuse a result already taken on the same code.

    Agents building side by side used to start the suite at once and fight over its ports and the machine; in one
    P1.1 run fixers ran it 9-11 times each and spent minutes in loops waiting for a port to free."""
    integ = load_state(rdir).get("integration") or {}
    if not integ.get("command"):
        return {"ok": True, "ran": False, "why": "the spec names no integration check"}
    latest_path = Path(rdir) / "checks" / "integration-latest.json"
    with queued(Path(rdir) / "integration.lock"):
        key = tree_key(root, f"{ARCHIVE}/")
        latest = read_json(latest_path) or {}
        if latest.get("tree") == key:
            return {"ok": latest.get("exit") == 0, "ran": False, "reused": True, "tree": key,
                    **{k: latest.get(k) for k in ("command", "exit", "seconds", "head", "failures")}, "tail": tail(latest.get("tail", ""), 40)}
        r = checks.run_command(integ["command"], Path(root) / (integ.get("cwd") or ""), int(integ.get("timeout_s") or 3600))
        rec = {"command": integ["command"], "cwd": integ.get("cwd") or "", "exit": r["exit"], "seconds": r["seconds"],
               "head": head(root), "tree": key, "tail": r["tail"], "failures": r["failures"]}
        write_json(latest_path, rec)
    return {"ok": r["exit"] == 0, "ran": True, **{k: rec[k] for k in ("command", "exit", "seconds", "head", "tree", "failures")},
            "tail": tail(r["tail"], 40)}


def agent_checks(root: Path, rdir: Path) -> dict:
    """The project's build, lint and test commands on the working tree as it is now, for a builder or fixer.

    One agent at a time, and a result taken on the same code is reused: in the ux-35 run agents ran the full web
    suite 254 times, often several at once on one machine. A failure is reported as new only when the base commit
    does not fail the same way, as verify does."""
    state = load_state(rdir)
    out_dir = Path(rdir) / "checks"
    with queued(Path(rdir) / "checks.lock"):
        key = tree_key(root, f"{ARCHIVE}/")
        cached = read_json(out_dir / f"agent-{key}.json")
        if cached:
            return {**cached, "reused": True}
        results = []
        for c in [c for k in ("build", "lint", "test") for c in state.get("commands", {}).get(k, [])]:
            r = checks.run_command(c["cmd"], Path(root) / (c.get("cwd") or ""))
            entry = {"cmd": c["cmd"], "cwd": c.get("cwd", ""), "exit": r["exit"], "seconds": r["seconds"], "new": [], "status": "pass"}
            if r["exit"] != 0:
                base_r = checks.run_at(root, rdir, state["base_commit"], c)
                new = (r["failures"] or [f"`{c['cmd']}` exits {r['exit']}"]) if base_r["exit"] == 0 else sorted(set(r["failures"]) - set(base_r["failures"]))
                entry.update(new=new, status="new_failures" if new else "pre_existing", tail=tail(r["tail"], 40))
            results.append(entry)
        rec = {"ok": not any(e["status"] == "new_failures" for e in results), "tree": key, "head": head(root), "checks": results}
        write_json(out_dir / f"agent-{key}.json", rec)
    return {**rec, "reused": False}


def _commits(root: Path, base: str) -> list[str]:
    return git(root, "rev-list", "--reverse", f"{base}..HEAD").stdout.split()


def _files(root: Path, sha: str) -> list[str]:
    return [f for f in git(root, "show", "--name-only", "--format=", sha).stdout.splitlines() if f.strip()]


def ids_in_production(root: Path, base: str, ids: set) -> list[tuple[str, int, str]]:
    """Added lines of non-test, non-doc files that name a spec requirement ID."""
    out, cur, line = [], "", 0
    for ln in git(root, "diff", "-U0", f"{base}..HEAD").stdout.splitlines():
        if ln.startswith("+++ "):
            cur = ln[6:] if ln.startswith("+++ b/") else ""
        elif ln.startswith("@@"):
            try:
                line = int(ln.split("+")[1].split(",")[0].split(" ")[0])
            except (IndexError, ValueError):
                line = 0
        elif ln.startswith("+") and cur and not is_test_file(cur) and not is_doc_file(cur):
            hit = [m for m in ID_RE.findall(ln) if m in ids]
            if hit:
                out.append((cur, line, hit[0]))
            line += 1
    return out


def _fixcheck(root: Path, rdir: Path, rnd: int, base: str) -> list[dict]:
    """Each fix the fixers of round ``rnd`` reported for a behavioural gap: its
    test must fail on the code before the round and pass now."""
    gaps = {g.get("id"): g for g in (read_json(Path(rdir) / "checks" / "spec-r1.json", {}) or {}).get("gaps") or []
            if isinstance(g, dict)}
    out = []
    for path in sorted((Path(rdir) / "fixes").glob(f"r{rnd}-*.json")):
        for fx in (read_json(path, {}) or {}).get("fixes") or []:
            item = str(fx.get("item", ""))
            gap = gaps.get(item)
            if not gap or gap.get("behavioural") is False or fx.get("outcome") != "fixed":
                continue
            tests = fx.get("test_files") or []
            bad = [t for t in tests if not is_test_file(t)]
            res = ({"ok": False, "why": f"not test files: {bad}"} if bad else
                   checks.fail_before_pass_after(root, base, tests, fx.get("test_command", ""), fx.get("cwd") or ""))
            out.append({"item": item, "ok": res["ok"], "why": res["why"], "test_command": fx.get("test_command", "")})
    return out


def verify(root: Path, rdir: Path, rnd: int) -> dict:
    root, rdir = Path(root), Path(rdir)
    state = load_state(rdir)
    sha = head(root)
    prior = read_json(rdir / "checks" / f"verify-r{rnd}.json")
    if prior and prior.get("head") == sha:
        return prior
    base = state["base_commit"]
    index = read_json(rdir / "spec-index.json", {}) or {}
    failures = []

    def fail(kind: str, title: str, detail: str = "", names=None, file: str = "", line: int = 0):
        failures.append({"id": f"V{rnd}-{len(failures) + 1}", "kind": kind, "title": title, "detail": detail,
                         "names": list(names or []), "file": file, "line": line})

    if rnd > 1:
        for fx in _fixcheck(root, rdir, rnd - 1, (state.get("round_base") or {}).get(str(rnd - 1), base)):
            if not fx["ok"]:
                fail("fixcheck", f"the fix for {fx['item']} is not pinned by a test that fails before it: {fx['why']}",
                     f"`{fx['test_command']}`")
    left = [p for p in _changed_paths(root) if p not in set(state.get("untracked_at_start") or [])]
    if left:
        fail("uncommitted", f"{len(left)} changed files were never committed", ", ".join(left[:20]))
    if not _commits(root, base):
        fail("uncommitted", "nothing was committed since the base commit")
    if (state.get("plan") or {}).get("mode") == "split":
        actors = {s: a for a, shas in (state.get("commits") or {}).items() for s in shas}
        for c in _commits(root, base):
            for f in _files(root, c):
                why = ("not made with `trowel commit`" if c not in actors
                       else may_commit(state, actors[c], f, tuple((state.get("commit_also") or {}).get(c) or ())))
                if why:
                    fail("ownership", f"commit {c[:10]} changes {f}: {why}", file=f)
    for f, line, rid in ids_in_production(root, base, set(index.get("ids") or [])):
        fail("ids", f"{f}:{line} names requirement {rid} in production code; IDs belong in tests and docs", file=f, line=line)
    cmds = [c for k in ("build", "lint", "test") for c in state.get("commands", {}).get(k, [])]
    results = checks.new_failures(root, rdir, base, cmds)
    for r in results:
        if r["status"] == "new_failures":
            fail("checks", f"`{r['cmd']}` has new failures: {', '.join(r['new'][:12])}", tail(r["tail"], 40), r["new"])
    integ = state.get("integration") or {}
    irec = None
    if integ.get("command"):
        latest = read_json(rdir / "checks" / "integration-latest.json")
        irec = latest if latest and latest.get("head") == sha else integration(root, rdir)
        if irec.get("exit") != 0:
            fail("integration", f"the integration check `{integ['command']}` fails (exit {irec.get('exit')})",
                 tail(irec.get("tail", ""), 60), irec.get("failures"))
    # A new branch or tag is reported, never failed: in a shared repository another session may have made it,
    # and a fixer told to remove it could delete someone's work.
    start = set(state.get("refs_at_start") or [])
    new_refs = [r for r in branch_and_tag_refs(root) if r not in start and r != f"refs/heads/{state.get('branch')}"] if start else []
    out = {"ok": True, "round": rnd, "head": sha, "failures": failures, "new_refs": new_refs,
           "checks": [{k: r[k] for k in ("cmd", "status", "new")} for r in results],
           "integration": {k: irec.get(k) for k in ("command", "exit", "seconds", "head")} if irec else None}
    write_json(rdir / "checks" / f"verify-r{rnd}.json", out)
    with update_state(rdir) as st:
        st.setdefault("round_base", {})[str(rnd)] = sha
        mark(st, f"verify:{rnd}", failures=len(failures))
        note = f"branches or tags appeared while the run was going: {', '.join(new_refs)}. trowel's agents must not make them; check who did before removing any"
        if new_refs and note not in (st.get("concerns") or []):
            st.setdefault("concerns", []).append(note)
    return out
