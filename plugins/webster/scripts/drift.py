#!/usr/bin/env python3
"""Drift detection for documentation, on the openwiki model.

  record   store HEAD, a hash of the docs tree, and every anchor the docs cite
  check    report what changed since the last record, and which anchors no longer resolve

Exit codes for check: 0 nothing to do, 1 drift found, 2 no manifest yet.
An anchor that no longer resolves is drift, and drift is a P0.
"""
import hashlib, json, os, re, subprocess, sys

ROOT = os.path.abspath(os.environ.get("WEBSTER_ROOT", "."))
DOCS = os.path.join(ROOT, os.environ.get("WEBSTER_DOCS", "docs"))
MANIFEST = os.path.join(DOCS, ".webster.json")
# Only real source/config extensions. Without this, "127.0.0.1:3000" reads as an anchor.
SRC_EXT = ("ts|tsx|js|jsx|mjs|cjs|py|go|rs|rb|java|kt|swift|c|h|cc|cpp|cs|php|sh|bash|zsh"
           "|sql|css|scss|html|vue|svelte|astro|json|jsonc|yaml|yml|toml|ini|env|md|mdx|txt|lock")
ANCHOR = re.compile(rf"\b([\w./-]+\.(?:{SRC_EXT})):(\d+)\b")


def git(*args):
    try:
        return subprocess.run(["git", "--no-pager", *args], cwd=ROOT, check=True,
                              capture_output=True, text=True).stdout.strip()
    except Exception:
        return ""


def doc_files():
    out = []
    for dirpath, dirnames, filenames in os.walk(DOCS):
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        out += [os.path.join(dirpath, f) for f in filenames if f.endswith(".md")]
    return sorted(out)


def tree_hash(paths):
    h = hashlib.sha256()
    for p in paths:
        h.update(os.path.relpath(p, ROOT).encode())
        with open(p, "rb") as f:
            h.update(hashlib.sha256(f.read()).digest())
    return h.hexdigest()


def collect_anchors(paths):
    """Every file:line the docs cite, mapped back to the page that cites it."""
    found = {}
    for p in paths:
        with open(p, encoding="utf-8", errors="replace") as f:
            for lineno, line in enumerate(f, 1):
                if line.lstrip().startswith(("```", "    ")):
                    continue
                for target, tline in ANCHOR.findall(line):
                    found.setdefault(f"{target}:{tline}", []).append(
                        f"{os.path.relpath(p, ROOT)}:{lineno}")
    return found


def resolves(anchor):
    target, _, lineno = anchor.rpartition(":")
    path = os.path.join(ROOT, target)
    if not os.path.isfile(path):
        return False, "file does not exist"
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            n = sum(1 for _ in f)
    except OSError:
        return False, "unreadable"
    if int(lineno) > n:
        return False, f"file has only {n} lines"
    return True, ""


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "check"
    if not os.path.isdir(DOCS):
        print(json.dumps({"status": "no_docs", "docs_dir": DOCS})); return 2

    paths = doc_files()
    anchors = collect_anchors(paths)
    head = git("rev-parse", "HEAD")

    if mode == "record":
        os.makedirs(DOCS, exist_ok=True)
        with open(MANIFEST, "w") as f:
            json.dump({"gitHead": head, "docsHash": tree_hash(paths),
                       "pages": [os.path.relpath(p, ROOT) for p in paths],
                       "anchors": anchors}, f, indent=2)
        print(json.dumps({"status": "recorded", "gitHead": head,
                          "pages": len(paths), "anchors": len(anchors)}))
        return 0

    # check
    broken = []
    for a, cited_by in sorted(anchors.items()):
        ok, why = resolves(a)
        if not ok:
            broken.append({"anchor": a, "reason": why, "cited_by": cited_by})

    if not os.path.exists(MANIFEST):
        print(json.dumps({"status": "no_manifest", "anchors": len(anchors),
                          "broken": broken}, indent=2))
        return 2

    old = json.load(open(MANIFEST))
    changed = []
    if old.get("gitHead") and head and head != old["gitHead"]:
        changed = [f for f in git("diff", "--name-only", f"{old['gitHead']}..HEAD").splitlines()
                   if f and not f.startswith(os.path.relpath(DOCS, ROOT) + "/")]
    dirty = [l[3:] for l in git("status", "--short", "--untracked-files=all").splitlines()
             if l[3:] and not l[3:].startswith(os.path.relpath(DOCS, ROOT) + "/")]

    # a page is suspect when code it cites appears in the changed set
    suspect = {}
    for a, cited_by in anchors.items():
        target = a.rpartition(":")[0]
        if target in changed or target in dirty:
            for page in cited_by:
                suspect.setdefault(page.split(":")[0], []).append(a)

    clean = not broken and not suspect and tree_hash(paths) == old.get("docsHash") and not changed
    print(json.dumps({
        "status": "clean" if clean else "drift",
        "gitHead": {"recorded": old.get("gitHead"), "current": head},
        "docs_edited_since_record": tree_hash(paths) != old.get("docsHash"),
        "code_files_changed": len(changed),
        "broken_anchors": broken,
        "suspect_pages": suspect,
    }, indent=2))
    return 0 if clean else 1


if __name__ == "__main__":
    sys.exit(main())
