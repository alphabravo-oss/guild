#!/usr/bin/env python3
"""Build an llms.txt from the docs tree, to the llmstxt.org format.

Prints to stdout. Never invents a page: every entry is a file that exists on disk.

The written file is what exit 0 means here, and there are two ways to return exit 2 instead:
no docs directory at the resolved path, and a tree holding no page to publish. This script
reports no findings and has no gate to fail, so it signals nothing with the code between
them. The second exit 2 covers a tree of nothing but stubs as well as an empty one -- an
unwritten page is dropped before the count is taken, and a header with no pages under it is a
file claiming a product has no documentation.
"""
import json, os, re, subprocess, sys
# tomllib is standard library from Python 3.11, which is this plugin's floor: webster requires
# 3.11 or newer. A-001 chose no fallback, so an older interpreter fails on the next line with
# ModuleNotFoundError rather than half-running -- exit 1, which the docstring above says this
# script signals nothing with. Nothing here chooses which interpreter runs: the commands
# invoke this file as a bare `python3 scripts/llmstxt.py`, so which one runs is whatever PATH
# resolves, and /usr/bin/python3 on the machine this was written on is 3.9.6. The floor is
# stated because it is a requirement on the caller; the import is what enforces it.
import tomllib

ROOT = os.path.abspath(os.environ.get("WEBSTER_ROOT", "."))
DOCS = os.path.join(ROOT, os.environ.get("WEBSTER_DOCS", "docs"))
BASE = os.environ.get("WEBSTER_BASE_URL", "").rstrip("/")

# Internal artifacts. Real pages for a reader, not working files.
EXCLUDE = ("docs-plan.md", "CHANGELOG.md", ".webster.json")

# Fallback ordering when there is no index to follow.
ORDER = ["index", "readme", "quickstart", "getting-started", "tutorial", "run",
         "how-to", "guide", "reference", "api", "config", "explanation", "architecture"]

# An anchor comment is a note from one writer to the next. It was reaching the published
# summary intact, so the line a machine reader got for docs/items/create-item.md ended
# "...to your store. <!-- src/app/main.py:15 -->", which reads as the page's own prose. re.S
# because a comment is free to span lines. The second alternative carries no closing marker:
# `<!--.*?-->` needs one, so an anchor comment nobody closed survived the strip and was
# published in full as the summary. An unclosed comment is read as running to the end of the
# text, which is also what a reader's markdown renderer does with it.
HTML_COMMENT = re.compile(r"<!--.*?-->|<!--.*", re.S)


def strip_comments(text):
    """Take the HTML comments out of anything on its way to the published file.

    Three routes reach a reader and all three come through here: the page body, the page's
    frontmatter, and -- via one_line -- the header's own name and description. Each was found
    open in turn, every time because the fix before it closed only the route it was looking at.
    Cleaning the body left the frontmatter `description` that wins over the body; cleaning both
    left the pyproject.toml/package.json description that wins over both and is published with
    no page in front of it at all. The first line above is the whole rule, not a description of
    whichever routes happen to be closed today."""
    return HTML_COMMENT.sub("", text or "").strip()


def one_line(value):
    """One publishable line: comments out, line breaks folded, a non-string dropped.

    The header is the only place this script publishes a string with no page behind it, so it
    printed whatever pyproject.toml or package.json held. A description ending in an anchor
    comment reached the reader verbatim. A TOML description written across three lines printed
    its middle line outside the `> ` blockquote entirely, which is why the fold matters as much
    as the strip: a summary that breaks across lines has stopped being the one line the format
    asks for. A non-string -- a JSON object under `description` -- yields nothing rather than a
    Python repr, and the next source in the chain is used instead.

    A value still carrying the stub marker, or a brace from one of scaffold.py's `{placeholder}`
    headings, is dropped on the same grounds. The header line is a claim that this repo is a
    thing and that this is what it does, and a skeleton nobody has filled in is the one text on
    disk that states the opposite. Dropped rather than published empty, so the next source in
    the chain gets its turn."""
    if not isinstance(value, str):
        return ""
    line = re.sub(r"\s+", " ", strip_comments(value)).strip()
    if STUB_MARKER in line or "{" in line or "}" in line:
        return ""
    return line

# A stub is the skeleton scaffold.py writes before anyone has filled it in. Listing one states
# that the page exists and says something, and that is the single claim this script is not
# allowed to make. Its headings are placeholders in braces, so what got published was a
# summary of a form nobody wrote.
STUB_MARKER = "webster: not written yet"


def frontmatter_field(text, field):
    """Read one frontmatter field. The author wrote it for this purpose, so it wins."""
    if not text.startswith("---"):
        return None
    end = text.find("\n---", 3)
    if end == -1:
        return None
    m = re.search(rf"^{field}:\s*(.+)$", text[3:end], re.M)
    if not m:
        return None
    return m.group(1).strip().strip('"').strip("'") or None


def body_after_frontmatter(text):
    """The page body with its frontmatter block removed, or the text unchanged without one.

    The body scan below no longer waits for an H1 before it accepts a line, so the frontmatter
    has to go first: otherwise the first line it meets on a page whose title lives up there is
    `title: "Create an item"` and that becomes the published summary."""
    if not text.startswith("---"):
        return text
    end = text.find("\n---", 3)
    return text if end == -1 else text[end + 4:]


def pyproject_meta(root):
    """name and description from pyproject.toml: PEP 621 [project] first, then Poetry's table.

    A Python repo has no package.json, so its llms.txt was titled with the checkout's directory
    name — the one line every machine reader takes as the product's name. Read per field: Poetry
    2.0 writes [project] but older files still carry the description under [tool.poetry] alone.
    An unparseable file is ignored rather than fatal, because a pyproject.toml is a syntax error
    for as long as somebody is editing it."""
    path = os.path.join(root, "pyproject.toml")
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, "rb") as f:
            data = tomllib.load(f)
    except Exception:
        return {}
    tool = data.get("tool")
    tables = [data.get("project"), tool.get("poetry") if isinstance(tool, dict) else None]
    meta = {}
    for table in tables:
        if isinstance(table, dict):
            for key in ("name", "description"):
                if key not in meta and isinstance(table.get(key), str):
                    meta[key] = table[key]
    return meta


def title_and_summary(path):
    """Title and one-line summary for a page.

    The frontmatter `description` is what the author wrote to describe the page, so it is used
    when present. Falling back to the first body line produces a fragment whenever that line is
    short or runs past the cut, which is how entries like "They are two different things" and
    summaries ending mid-clause get published."""
    text = open(path, encoding="utf-8", errors="replace").read()
    title = strip_comments(frontmatter_field(text, "title")) or None
    summary = strip_comments(frontmatter_field(text, "description")) or None

    if not title or not summary:
        # Comments come out of the whole body before a line is chosen, not out of the chosen
        # line afterwards: a comment on its own line would otherwise be picked as the summary
        # and published as an empty one.
        body = strip_comments(body_after_frontmatter(text))
        body_title, body_summary = None, None
        for line in body.splitlines():
            s = line.strip()
            if not body_title and s.startswith("# "):
                body_title = s[2:].strip()
                continue
            # No H1 required. A page that puts its title in frontmatter has no `# ` line to
            # wait for, and waiting for one published it with a title and no summary at all.
            if s and not s.startswith("#") and not s.startswith("---"):
                body_summary = re.sub(r"[*_`\[\]]|\(.*?\)", "", s).strip()
                # a summary cut mid-sentence reads as a defect, so keep whole sentences
                if len(body_summary) > 160:
                    stop = body_summary.rfind(". ", 0, 161)
                    body_summary = body_summary[:stop + 1] if stop > 40 else body_summary[:160].rstrip() + "..."
                break
        title = title or body_title
        summary = summary or body_summary

    return title or os.path.basename(path), summary or ""


def readme_summary(root):
    """The README's summary line, or nothing when the README is itself unwritten.

    main() keeps a stub out of the listing by reading each page and looking for the marker, but
    the README behind the header's `> ` line is not in the docs tree that loop walks, so the
    exclusion was applied on one of the two routes into this file. A repo whose README is still
    the skeleton scaffold.py wrote published that skeleton's first placeholder as the product's
    summary -- the line a machine reader takes for what the repo is, taken from the one page
    whose entire content is that nobody has written it yet. The marker sits inside an HTML
    comment, so strip_comments removes it before one_line could ever see it: the file is read
    for it here, the same way the page loop reads for it."""
    path = os.path.join(root, "README.md")
    if not os.path.isfile(path):
        return ""
    if STUB_MARKER in open(path, encoding="utf-8", errors="replace").read():
        return ""
    return title_and_summary(path)[1]


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
                path = os.path.join(dirpath, fn)
                if STUB_MARKER in open(path, encoding="utf-8", errors="replace").read():
                    continue
                pages.append(path)
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

    # package.json first, then pyproject.toml, then the README's opening line. package.json
    # keeps winning where both exist, because a repo carrying one is a Node project whose
    # pyproject.toml is tooling configuration rather than the product's own name.
    # Cleaned one source at a time rather than once at the end, because a name that is nothing
    # but a comment has to fall through to the next source instead of publishing an empty `# `
    # as the first line every machine reader takes for the product's name. Every authored
    # source -- both names, both descriptions, the README's line -- runs through one_line for
    # the same reason the page loop above tests every page for the marker: a header sourced
    # from an unwritten stub publishes the skeleton, which is the one claim about a page this
    # script is not allowed to make. The sixth source is not authored and does not: the
    # checkout's directory name is the last resort with nothing behind it, so dropping it would
    # leave the file with no `# ` line at all, and a directory called `{app}` is published as
    # `# {app}` rather than as nothing.
    py = pyproject_meta(ROOT)
    name = one_line(pkg.get("name")) or one_line(py.get("name")) or os.path.basename(ROOT)
    summary = (one_line(pkg.get("description")) or one_line(py.get("description"))
               or one_line(readme_summary(ROOT)))

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
