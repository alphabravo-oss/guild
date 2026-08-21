#!/usr/bin/env python3
"""Build an llms.txt from the docs tree, to the llmstxt.org format.

Prints to stdout. Never invents a page: every entry is a file that exists on disk.
"""
import json, os, re, subprocess, sys

ROOT = os.path.abspath(os.environ.get("WEBSTER_ROOT", "."))
DOCS = os.path.join(ROOT, os.environ.get("WEBSTER_DOCS", "docs"))
BASE = os.environ.get("WEBSTER_BASE_URL", "").rstrip("/")

# Internal artifacts. Real pages for a reader, not working files.
EXCLUDE = ("docs-plan.md", "CHANGELOG.md", ".webster.json")

# Fallback ordering when there is no index to follow.
ORDER = ["index", "readme", "quickstart", "getting-started", "tutorial", "run",
         "how-to", "guide", "reference", "api", "config", "explanation", "architecture"]


def title_and_summary(path):
    title, summary = None, None
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            s = line.strip()
            if not title and s.startswith("# "):
                title = s[2:].strip()
                continue
            if title and s and not s.startswith("#"):
                summary = re.sub(r"[*_`\[\]]|\(.*?\)", "", s)[:160].strip()
                break
    return title or os.path.basename(path), summary or ""


def index_order(docs_dir):
    """The order the index links its pages in. An author's ordering beats a heuristic."""
    for cand in ("index.md", "README.md"):
        idx = os.path.join(docs_dir, cand)
        if os.path.isfile(idx):
            links = re.findall(r"\]\(([^)]+\.md)\)", open(idx, encoding="utf-8", errors="replace").read())
            seen, order = set(), [cand]
            for l in links:
                l = os.path.normpath(l)
                if l not in seen and l != cand:
                    seen.add(l); order.append(l)
            return order
    return []


def rank(rel_path, docs_dir, linked):
    """Pages the index links, in its order. Then the keyword list. Then the rest."""
    base = os.path.basename(rel_path)
    if base in linked:
        return linked.index(base)
    low = rel_path.lower()
    for i, key in enumerate(ORDER):
        if key in low:
            return len(linked) + i
    return len(linked) + len(ORDER)


def main():
    if not os.path.isdir(DOCS):
        sys.stderr.write(f"no docs directory at {DOCS}\n"); return 2

    pages = []
    for dirpath, dirnames, filenames in os.walk(DOCS):
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        for fn in sorted(filenames):
            if fn.endswith(".md") and not fn.startswith(".") and fn not in EXCLUDE:
                pages.append(os.path.join(dirpath, fn))
    if not pages:
        sys.stderr.write("no markdown pages found\n"); return 2
    linked = index_order(DOCS)
    pages.sort(key=lambda p: (rank(os.path.relpath(p, ROOT), DOCS, linked), p))

    pkg = {}
    pkg_path = os.path.join(ROOT, "package.json")
    if os.path.exists(pkg_path):
        try:
            pkg = json.load(open(pkg_path))
        except Exception:
            pkg = {}

    readme = os.path.join(ROOT, "README.md")
    name = pkg.get("name") or os.path.basename(ROOT)
    summary = pkg.get("description") or (title_and_summary(readme)[1] if os.path.exists(readme) else "")

    out = [f"# {name}", ""]
    if summary:
        out += [f"> {summary}", ""]

    core, optional = [], []
    for p in pages:
        rel = os.path.relpath(p, ROOT)
        t, s = title_and_summary(p)
        url = f"{BASE}/{rel}" if BASE else rel
        line = f"- [{t}]({url})" + (f": {s}" if s else "")
        unranked = rank(rel, DOCS, linked) == len(linked) + len(ORDER)
        (optional if unranked else core).append(line)

    if core:
        out += ["## Docs", ""] + core + [""]
    if optional:
        out += ["## Optional", ""] + optional + [""]
    print("\n".join(out).rstrip() + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
