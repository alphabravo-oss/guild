#!/usr/bin/env python3
"""Check what actually shipped, not what the markdown said.

  check [build_dir]   scan built HTML for internals that reached the reader. exit 1 on a leak

Every other checker in this plugin reads markdown as text. This one reads the pages a browser
would. The distinction is not theoretical: an anchor lives in an HTML comment precisely so a
reader never sees it, and nothing verified that until this existed. A comment written somewhere
MDX treats differently, a frontmatter key a theme decides to print, a working-note tag inside a
code fence that survives highlighting: each would pass every markdown check and still be on the
page.

Verified against a real Docusaurus 3 build: frontmatter keys render nowhere, and an anchor
comment beside a claim renders nowhere. This script exists so that stays true rather than being
assumed.
"""
import os, re, sys

SRC_EXT = ("ts|tsx|js|jsx|mjs|cjs|py|go|rs|rb|java|kt|swift|c|h|cc|cpp|cs|php|sh|bash|zsh"
           "|sql|css|scss|vue|svelte|astro|toml|ini|lock")

LEAKS = [
 ("anchor", rf"(?<![\w./-])[\w./-]+\.(?:{SRC_EXT}):\d+\b",
  "a file:line the reader can see. It belongs in an HTML comment, which renders to nothing"),
 ("source-path", rf"(?<![\w./-])(?:src|lib|internal|pkg|cmd|app)/[\w./-]+\.(?:{SRC_EXT})(?![\w/])",
  "an implementation path on a published page"),
 ("working-tag", r"\[\?\]|\[SPEC\]|\[NOTE\]|\[BUG\]",
  "a working-note tag that was meant to be resolved before publishing"),
 ("frontmatter-key", r"\b(?:doc_type|sidebar_position|sidebar_label)\s*:",
  "a frontmatter key printed as text"),
 ("agent-file", r"\b(?:CLAUDE|AGENTS|GEMINI)\.md\b",
  "an internal instruction file, which means nothing to a reader"),
 ("stub-marker", r"webster: not written yet",
  "a page that was scaffolded and never written"),
]

# A docs site legitimately shows code. A leak inside <code> or <pre> is usually the example
# doing its job, so those are stripped before the prose is scanned.
STRIP = re.compile(r"<pre\b.*?</pre>|<code\b.*?</code>|<script\b.*?</script>|"
                   r"<style\b.*?</style>|<!--.*?-->", re.S | re.I)
TAG = re.compile(r"<[^>]+>")
ENTITY = re.compile(r"&[a-zA-Z#0-9]+;")


def visible_text(html):
    return ENTITY.sub(" ", TAG.sub(" ", STRIP.sub(" ", html)))


def main():
    build = sys.argv[2] if len(sys.argv) > 2 else "website/build"
    if not os.path.isdir(build):
        print(f"no built site at {build}. Build it first, or report the gate not_checked")
        return 2

    pages, findings = 0, []
    for dirpath, dirnames, filenames in os.walk(build):
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        for fn in filenames:
            if not fn.endswith(".html"):
                continue
            pages += 1
            path = os.path.join(dirpath, fn)
            text = visible_text(open(path, encoding="utf-8", errors="replace").read())
            for rid, pat, why in LEAKS:
                for m in re.finditer(pat, text):
                    findings.append((os.path.relpath(path, build), rid,
                                     m.group(0)[:60].strip(), why))
                    break

    print(f"{pages} rendered pages scanned")
    if not findings:
        print("nothing internal reached the reader")
        return 0

    by_rule = {}
    for page, rid, what, why in findings:
        by_rule.setdefault(rid, []).append((page, what, why))
    print(f"\nLEAKS ({len(findings)})")
    for rid in sorted(by_rule, key=lambda r: -len(by_rule[r])):
        group = by_rule[rid]
        print(f"\n  {rid}  ({len(group)})\n      {group[0][2]}")
        for page, what, _ in group[:6]:
            print(f"    {page}  {what!r}")
        if len(group) > 6:
            print(f"    ... and {len(group) - 6} more")
    return 1


if __name__ == "__main__":
    sys.exit(main())
