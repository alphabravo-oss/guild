"""The plan: one builder or slices, decided in code first.

``measure``         sizes the work from the spec alone, before any agent runs:
                    the existing files and directories the spec names, the
                    lines a builder must read in them, and a new-code estimate
                    from the number of requirements. When that fits joiner's
                    S4 budget the run uses one builder and no planner.
``check_plan``      the S1-S10 rules of joiner's docs/research/decompose-study.md
                    section 4, the S4 size budget and the waves, for a plan the
                    planner agent wrote because the spec did not fit. On a pass
                    it writes one brief per slice, assembled from the spec index
                    and the plan by code, so no agent paraphrases the spec into it.
``shares``          splits the requirements among the round-1 spec checkers that
                    run side by side, keeping each slice's requirements together.

Copied from joiner's gates.py; joiner's probe and contract checks are gone.
"""

from __future__ import annotations

import re
from pathlib import Path

from common import ID_RE, is_doc_file, read_json, skipped, write_json

# S4: "<= 8 key_files entries (a directory entry counts as one ...), <= ~800 LOC
# to read, <= ~1,500 LOC new." The "~" is read as 25% headroom.
MAX_ENTRIES = 8
MAX_READ_LOC = 800
MAX_NEW_LOC = 1500
HEADROOM = 1.25
MANY_SLICES = 8  # S5: "More than 8 is a smell"
# A proxy for "LOC new" that code can compute before anything is planned: new
# lines per requirement ID. Chosen, not measured; see the README's limits.
NEW_LOC_PER_REQUIREMENT = 25
COVERED_BY = ("co_owned", "contract", "anchor")


# ---------------------------------------------------------------------------
# Plan
# ---------------------------------------------------------------------------
def _norm(p: str) -> str:
    p = p.strip().replace("\\", "/")
    while p.startswith("./"):
        p = p[2:]
    return p.lower()


def overlaps(a: str, b: str) -> bool:
    a, b = _norm(a), _norm(b)
    if a == b:
        return True
    if a.endswith("/") and b.startswith(a):
        return True
    return b.endswith("/") and a.startswith(b)


def _text_lines(path: Path) -> int:
    try:
        return path.read_bytes().count(b"\n")
    except OSError:
        return 0


def read_loc(root: Path, entries: list[str]) -> int:
    """Lines of the existing files a slice owns: a builder reads what it changes.
    Dependency, build and hidden directories under a named directory are not its code."""
    total = 0
    for e in entries:
        p = Path(root) / e.strip("/")
        if p.is_file():
            total += _text_lines(p)
        elif p.is_dir():
            for f in p.rglob("*"):
                if skipped(str(f.relative_to(root))):
                    continue
                if f.is_file() and f.suffix not in (".png", ".jpg", ".gif", ".ico", ".lock", ".sum") and f.stat().st_size < 1_000_000:
                    total += _text_lines(f)
    return total


def fits(entries: int, read: int, new: int) -> bool:
    return entries <= MAX_ENTRIES and read <= MAX_READ_LOC * HEADROOM and new <= MAX_NEW_LOC * HEADROOM


def _nonempty(x) -> bool:
    return isinstance(x, str) and bool(x.strip())


# A path the spec names: a backticked token, or a bare token with a slash.
PATH_TOKEN = re.compile(r"`([^`\s]+)`|(?<![\w/.:-])((?:[\w.-]+/)+[\w.-]*)")


def named_paths(root: Path, text: str, skip: str = "") -> list[str]:
    """Existing files and directories of the project that the spec names."""
    out = []
    for m in PATH_TOKEN.finditer(text):
        tok = (m.group(1) or m.group(2) or "").strip().rstrip(".,;)")
        tok = re.sub(r":\d+(?:-\d+)?$", "", tok)
        while tok.startswith("./"):
            tok = tok[2:]
        if not tok or tok.startswith(("/", "..", "http")) or tok in (".", "/"):
            continue
        p = Path(root) / tok
        if not (p.is_file() or p.is_dir()) or tok.rstrip("/") == skip:
            continue
        entry = tok.rstrip("/") + "/" if p.is_dir() else tok
        if entry not in out:
            out.append(entry)
    return out


def measure(root: Path, spec_text: str, index: dict, spec_rel: str = "") -> dict:
    """The S4 measures of building the whole spec with one builder."""
    paths = named_paths(root, spec_text, spec_rel)
    size = {"entries": len(paths), "read": read_loc(root, paths), "new": NEW_LOC_PER_REQUIREMENT * len(index.get("ids") or [])}
    return {**size, "paths": paths, "fits": fits(**size),
            "budget": {"entries": MAX_ENTRIES, "read": MAX_READ_LOC, "new": MAX_NEW_LOC, "headroom": HEADROOM}}


def single_plan(index: dict) -> dict:
    """The plan of a spec that fits: one slice that owns the whole repository."""
    return {"mode": "single", "waves": [["all"]],
            "slices": {"all": {"title": "the whole spec", "files": [], "requirement_ids": list(index.get("ids") or []),
                               "depends_on": [], "owns_all": True}}}


SHARE_SIZE = 20  # requirements one spec checker judges before a second one is worth starting
MAX_SHARES = 3


def shares(plan: dict, index: dict) -> list[list[str]]:
    """The requirement IDs each round-1 spec checker judges, in plan order.

    Every ID is in exactly one share. A slice's requirements stay together
    where the cut allows, so one checker sees the code that implements them;
    IDs no slice names follow in spec order. A spec under SHARE_SIZE
    requirements gets one checker."""
    order = []
    for sid in [x for wave in plan.get("waves") or [] for x in wave]:
        order += [r for r in ((plan.get("slices") or {}).get(sid) or {}).get("requirement_ids") or [] if r not in order]
    order += [r for r in index.get("ids") or [] if r not in order]
    n = max(1, min(MAX_SHARES, len(order) // SHARE_SIZE))
    size = -(-len(order) // n) if order else 0
    return [order[i:i + size] for i in range(0, len(order), size)] if order else [[]]


def _anchor_ok(root: Path, at: str) -> str:
    path, _, line = str(at).rpartition(":")
    if not path or not line.isdigit():
        return f"anchor {at!r} is not file:line"
    f = Path(root) / path
    if not f.is_file():
        return f"anchor {at!r}: {path} does not exist"
    if int(line) < 1 or int(line) > _text_lines(f) + 1:
        return f"anchor {at!r}: {path} has {_text_lines(f)} lines"
    return ""


def _excerpt(root: Path, spec: str):
    path, _, rng = str(spec).rpartition(":")
    a, _, b = rng.partition("-")
    f = Path(root) / path
    if not path or not a.isdigit() or not b.isdigit() or not f.is_file() or int(a) > int(b):
        return None
    lines = f.read_text(encoding="utf-8", errors="replace").splitlines()
    if int(a) < 1 or int(b) > len(lines):
        return None
    return "\n".join(lines[int(a) - 1:int(b)])


def _waves(slices: dict) -> tuple[list[list[str]], str]:
    level, order = {}, []
    remaining = dict(slices)
    while remaining:
        ready = [i for i, s in remaining.items() if all(d in level for d in s.get("depends_on") or [])]
        if not ready:
            return [], "depends_on has a cycle among: " + ", ".join(sorted(remaining))
        for i in ready:
            level[i] = 1 + max([level[d] for d in remaining[i].get("depends_on") or []] or [0])
            order.append(i)
            del remaining[i]
    waves = [[] for _ in range(max(level.values()))]
    for i in order:
        waves[level[i] - 1].append(i)
    return waves, ""


def check_plan(root: Path, rdir: Path, index: dict) -> dict:
    plan = read_json(Path(rdir) / "plan.json")
    if not isinstance(plan, dict):
        return {"ok": False, "errors": ["plan.json is missing or not a JSON object"], "warnings": []}
    ids = index["ids"]
    errors, warnings, notes = [], [], []
    raw = plan.get("slices") or []
    if not isinstance(raw, list) or not raw:
        return {"ok": False, "errors": ["slices: empty"], "warnings": []}
    slices = {}
    for n, s in enumerate(raw):
        sid = str(s.get("id", "")).strip() if isinstance(s, dict) else ""
        if not sid or sid in slices:
            errors.append(f"slices[{n}]: id missing or repeated")
            continue
        slices[sid] = s
    owners: dict[str, list[str]] = {i: [] for i in ids}
    for sid, s in slices.items():
        kind = s.get("kind", "behaviour")
        reqs = s.get("requirement_ids") or []
        files = s.get("files") or []
        unknown = [r for r in reqs if r not in owners]
        if unknown:
            errors.append(f"{sid}: requirement_ids not in the spec: {', '.join(unknown)}")
        for r in reqs:
            if r in owners:
                owners[r].append(sid)
        if not files:
            errors.append(f"{sid}: files is empty; a slice owns the files it writes")
        if kind not in ("behaviour", "docs"):
            errors.append(f"{sid}: kind must be behaviour or docs")
        elif kind == "behaviour" and not reqs:
            errors.append(f"{sid}: writes behaviour but owns no requirement (S2). Give it the clauses its code satisfies, "
                          "co-owned where several slices write parts of one clause (S3), or merge it")
        elif kind == "docs" and [f for f in files if not is_doc_file(f)]:
            errors.append(f"{sid}: a docs slice may own no requirement only if it writes docs; it lists "
                          f"{', '.join(f for f in files if not is_doc_file(f))}")
        for d in s.get("depends_on") or []:
            if d not in slices:
                errors.append(f"{sid}: depends_on names unknown slice {d!r}")
        for a in s.get("anchors") or []:
            msg = _anchor_ok(root, (a or {}).get("at", "")) if isinstance(a, dict) else "anchor is not an object"
            if msg:
                errors.append(f"{sid}: {msg}")
            elif not _nonempty(a.get("fact")):
                errors.append(f"{sid}: anchor {a.get('at')} states no fact")
        for p in s.get("patterns") or []:
            if not isinstance(p, dict) or _excerpt(root, p.get("from", "")) is None:
                errors.append(f"{sid}: pattern {p!r} must name an existing file and a line range, path:start-end")
        for u in s.get("reuse") or []:
            if not isinstance(u, dict) or not _nonempty(u.get("need")) or not _nonempty(u.get("use")):
                errors.append(f"{sid}: reuse entry {u!r} needs need, use and at")
            elif msg := _anchor_ok(root, u.get("at", "")):
                errors.append(f"{sid}: reuse {u.get('use')!r}: {msg} (at points to where it already exists: its definition, or the dependency's line in the manifest)")
    unowned = [i for i, o in owners.items() if not o]
    if unowned:
        errors.append(f"requirements no slice owns: {', '.join(unowned)}")
    for rid, o in owners.items():
        if len(o) > 2 and not any(rid in (slices[x].get("split_reason") or {}) for x in o):
            errors.append(f"{rid} has {len(o)} owners ({', '.join(o)}); more than 2 needs a split_reason naming which part each owner builds (S3)")
    ids_list = list(slices)
    for i, a in enumerate(ids_list):
        for b in ids_list[i + 1:]:
            for fa in slices[a].get("files") or []:
                for fb in slices[b].get("files") or []:
                    if overlaps(fa, fb):
                        errors.append(f"{a} and {b} both own {fa if len(fa) >= len(fb) else fb}")
    # A docs slice describes what the behaviour slices build, so it starts after all of them. Left in an early
    # wave, its builder sits polling for files that do not exist yet (47 minutes of sleep loops in one P1.1 run).
    behaviour = [x for x, s in slices.items() if s.get("kind", "behaviour") == "behaviour"]
    for sid, s in slices.items():
        if s.get("kind") == "docs":
            missing = [b for b in behaviour if b not in (s.get("depends_on") or [])]
            if missing:
                s["depends_on"] = list(s.get("depends_on") or []) + missing
                notes.append(f"{sid} documents what the other slices build, so it now starts after {', '.join(missing)}")
                write_json(Path(rdir) / "plan.json", plan)
    waves, cyc = _waves(slices) if not errors else ([], "")
    if cyc:
        errors.append(cyc)

    # Size: S4 per slice; one builder when the whole build fits.
    sizes = {}
    for sid, s in slices.items():
        files = s.get("files") or []
        sizes[sid] = {"entries": len(files), "read": read_loc(root, files), "new": int(s.get("new_loc") or 0)}
    total = {"entries": sum(x["entries"] for x in sizes.values()), "read": sum(x["read"] for x in sizes.values()),
             "new": sum(x["new"] for x in sizes.values())}
    whole_fits = fits(**total)
    mode = plan.get("mode", "split" if len(slices) > 1 else "single")
    if mode == "single" and len(slices) != 1:
        errors.append("mode single needs exactly one slice")
    if mode == "single" and not whole_fits and not _nonempty(plan.get("single_reason")):
        errors.append(f"one builder is over the S4 budget ({total['entries']} entries, {total['read']} LOC to read, "
                      f"{total['new']} new; budget {MAX_ENTRIES}/{MAX_READ_LOC}/{MAX_NEW_LOC}). Split by S1-S10, or give "
                      "single_reason: why no split leaves every slice with a requirement it can test")
    if mode == "split" and whole_fits and not errors:
        notes.append(f"the whole build fits one builder ({total['entries']} entries, {total['read']} LOC to read, "
                     f"{total['new']} new), so it runs as one slice")
        mode = "single"
    if mode == "split":
        for sid, z in sizes.items():
            if not fits(**z):
                msg = f"{sid} is over the S4 budget ({z['entries']} entries, {z['read']} LOC to read, {z['new']} new)"
                if z["entries"] > 1:
                    errors.append(msg + "; split it further")
                else:
                    warnings.append(msg + "; one file cannot be split")
        if len(slices) > MANY_SLICES:
            warnings.append(f"{len(slices)} slices: more than {MANY_SLICES} is a smell (S5)")
        if not _nonempty(plan.get("contract_block")):
            errors.append("contract_block is empty: a split pins exact signatures, names and config keys for every slice (S6)")
        for sid, s in slices.items():
            if not s.get("anchors"):
                errors.append(f"{sid}: no code anchors (S7)")
        errors.extend(_seam_errors(plan, slices))
    elif not any(s.get("anchors") for s in slices.values()):
        errors.append("no code anchors: name the real constructors, call sites and lifecycle facts the work touches (S7)")
    for sid, s in slices.items():
        if s.get("kind", "behaviour") == "behaviour" and not s.get("patterns"):
            warnings.append(f"{sid}: no code-pattern excerpt; the builder mirrors no existing code")
        if s.get("kind", "behaviour") == "behaviour" and not s.get("reuse"):
            warnings.append(f"{sid}: names nothing to reuse; its builder writes everything new")
    return {"ok": not errors, "errors": errors, "warnings": warnings, "notes": notes, "mode": mode,
            "slices": slices, "waves": waves if mode == "split" else [["all"]] if slices else [], "sizes": sizes, "total": total}


def _seam_errors(plan: dict, slices: dict) -> list[str]:
    errors = []
    seams = plan.get("seams") or []
    # A docs slice only describes what others built; waiting for them is ordering, not shared behaviour.
    docs = {x for x, s in slices.items() if s.get("kind") == "docs"}
    edges = {tuple(sorted((a, d))) for a, s in slices.items() for d in s.get("depends_on") or [] if a not in docs and d not in docs}
    covered_pairs = set()
    contract = plan.get("contract_block") or ""
    for n, sm in enumerate(seams):
        pair = sm.get("slices") or [] if isinstance(sm, dict) else []
        if len(pair) < 2 or any(p not in slices for p in pair):
            errors.append(f"seams[{n}]: slices must name two or more known slices")
            continue
        how, ref = sm.get("covered_by"), str(sm.get("ref", ""))
        if how not in COVERED_BY or not ref.strip():
            errors.append(f"seams[{n}]: covered_by must be one of {', '.join(COVERED_BY)} with a ref")
            continue
        if how == "co_owned":
            for rid in ID_RE.findall(ref) or [ref]:
                lack = [p for p in pair if rid not in (slices[p].get("requirement_ids") or [])]
                if lack:
                    errors.append(f"seams[{n}]: {rid} is said to be co-owned but {', '.join(lack)} does not own it")
        elif how == "contract" and ref not in contract:
            errors.append(f"seams[{n}]: ref is not text from contract_block")
        elif how == "anchor" and not any(ref in f"{a.get('at', '')} {a.get('fact', '')}"
                                         for p in pair for a in slices[p].get("anchors") or [] if isinstance(a, dict)):
            errors.append(f"seams[{n}]: ref is not in an anchor of {' or '.join(pair)}")
        for i, a in enumerate(pair):
            for b in pair[i + 1:]:
                covered_pairs.add(tuple(sorted((a, b))))
    for a, b in sorted(edges - covered_pairs):
        errors.append(f"{a} and {b} depend on each other but no seam says how their shared behaviour is covered (S10)")
    return errors


# ---------------------------------------------------------------------------
# Briefs
# ---------------------------------------------------------------------------
def render_briefs(root: Path, rdir: Path, index: dict, plan: dict, result: dict) -> dict:
    """One brief per slice, assembled by code (S9: inline what binds)."""
    slices = result["slices"]
    if result["mode"] == "single":
        merged = {"id": "all", "title": "the whole spec", "kind": "behaviour", "requirement_ids": list(index["ids"]),
                  "files": sorted({f for s in slices.values() for f in s.get("files") or []}), "depends_on": [],
                  "anchors": [a for s in slices.values() for a in s.get("anchors") or []],
                  "patterns": [p for s in slices.values() for p in s.get("patterns") or []],
                  "flexible": [x for s in slices.values() for x in s.get("flexible") or []], "owns_all": True}
        slices = {"all": merged}
    owners = {}
    for sid, s in slices.items():
        for r in s.get("requirement_ids") or []:
            owners.setdefault(r, []).append(sid)
    paths = {}
    for sid, s in slices.items():
        reqs = s.get("requirement_ids") or []
        parts = [f"# Slice {sid}: {s.get('title', '')}\n"]
        parts.append("## Your requirements, verbatim from the spec\n")
        for r in reqs:
            others = [o for o in owners.get(r, []) if o != sid]
            reason = (s.get("split_reason") or {}).get(r, "")
            share = f"\n\n_Co-owned with {', '.join(others)}: build and test your part.{(' ' + reason) if reason else ''}_" if others else ""
            parts.append(f"### {r}\n\n{index['clauses'].get(r, '')}{share}\n")
        if index.get("global_sections"):
            parts.append("## Rules that bind every slice, verbatim from the spec\n\n" + index["global_sections"] + "\n")
        if plan.get("contract_block") and result["mode"] == "split":
            parts.append("## Cross-slice contract (identical in every slice's brief)\n\n" + plan["contract_block"] + "\n")
        opens = s.get("flexible") or []
        if opens:
            parts.append("## Left open by the spec: decide, state it in a code comment, test it\n")
            parts.extend(f"- {q}" for q in opens)
            parts.append("")
        if s.get("anchors"):
            parts.append("## Code anchors (facts read from the code)\n")
            parts.extend(f"- `{a.get('at')}`: {a.get('fact')}" for a in s["anchors"])
            parts.append("")
        if s.get("reuse"):
            parts.append("## Reuse these: they already exist here (do not write your own)\n")
            parts.extend(f"- {u.get('need')}: use `{u.get('use')}` (`{u.get('at')}`)" for u in s["reuse"])
            parts.append("")
        for p in s.get("patterns") or []:
            ex = _excerpt(root, p.get("from", ""))
            if ex is not None:
                parts.append(f"## Pattern to mirror for `{p.get('for', '')}`, from `{p['from']}`\n\n```\n{ex}\n```\n")
        own = "the whole repository (one builder)" if s.get("owns_all") else ", ".join(f"`{f}`" for f in s.get("files") or [])
        parts.append(f"## Files you own\n\n{own}\n")
        deps = s.get("depends_on") or []
        if deps:
            parts.append("## Built before you (read, do not edit)\n")
            parts.extend(f"- {d}: {slices[d].get('title', '')} ({', '.join(slices[d].get('files') or [])})" for d in deps)
            parts.append("")
        path = Path(rdir) / "slices" / f"{sid}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(parts), encoding="utf-8")
        paths[sid] = str(path)
    return {"briefs": paths, "slices": slices}
