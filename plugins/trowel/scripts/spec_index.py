"""Index a spec: every requirement ID and the verbatim text that defines it.

The plan check, the slice briefs and the contract check all work from this
index, so a slice owns spec text copied byte for byte (decompose study S1, S9)
rather than a writer's paraphrase of it.

An ID is *defined* where it starts a list item, a table row, a heading or a
line; elsewhere (``see FR-2``, ``[from A-009]``) it is only a reference. The
clause is that line plus what belongs to it:

  list item   the item and its continuation lines, up to a blank line, the
              next item at the same or a shallower indent, or a heading
  table row   the table's header row and the row
  heading     the heading and its body up to the next heading or definition
  line        the paragraph, up to a blank line

An ID defined more than once (a bullet and a table row) gets every clause,
joined by a blank line. The frontmatter, fenced code and an
``## Appendix: Interview Transcript`` section are not indexed, and an ID
prefix the appendix uses as a heading (drew's answer blocks, ``## A-001``) is
a citation of the transcript, not a requirement, wherever it appears.

A spec with no IDs at all is indexed by its top-level list items, numbered
SPEC-1, SPEC-2, ... in order, so any spec can be built; ``synthetic`` says so.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

from common import ID_TOKEN

FRONTMATTER_RE = re.compile(r"\A---\s*\n.*?\n---\s*\n", re.DOTALL)
APPENDIX_RE = re.compile(r"^##\s+Appendix:\s*Interview\s+Transcript\b", re.IGNORECASE)
FENCE_RE = re.compile(r"^\s*(```|~~~)")
HEADING_RE = re.compile(r"^(#{1,6})\s+")
ITEM_RE = re.compile(r"^(\s*)(?:[-*+]|\d+[.)])\s+")
TABLE_RE = re.compile(r"^\s*\|")
TABLE_SEP_RE = re.compile(r"^\s*\|[\s:|-]+\|\s*$")

_LEAD = r"(?:\*\*|__|`|\[)?"
DEF_ITEM = re.compile(rf"^(\s*)(?:[-*+]|\d+[.)])\s+{_LEAD}({ID_TOKEN})\b")
DEF_ROW = re.compile(rf"^\s*\|\s*{_LEAD}({ID_TOKEN})\b")
DEF_HEAD = re.compile(rf"^#{{2,6}}\s+{_LEAD}({ID_TOKEN})\b")
DEF_LINE = re.compile(rf"^{_LEAD}({ID_TOKEN})(?:\*\*|__|`|\])?\s*[:.)\-—]")


def _body_lines(text: str) -> list[str]:
    """The spec's lines with frontmatter, fenced code and the appendix blanked out.

    Blanking (rather than removing) keeps line numbers aligned with the file."""
    m = FRONTMATTER_RE.match(text)
    lines = text.splitlines()
    out = []
    skip_to = text[: m.end()].count("\n") if m else 0
    in_fence = False
    in_appendix = False
    for i, line in enumerate(lines):
        if i < skip_to or in_appendix:
            out.append("")
            continue
        if FENCE_RE.match(line):
            in_fence = not in_fence
            out.append("")
            continue
        if in_fence:
            out.append("")
            continue
        if APPENDIX_RE.match(line):
            in_appendix = True
            out.append("")
            continue
        out.append(line)
    return out


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" \t"))


def _definition(line: str):
    for kind, rx in (("item", DEF_ITEM), ("row", DEF_ROW), ("head", DEF_HEAD), ("line", DEF_LINE)):
        m = rx.match(line)
        if m:
            return kind, m.group(m.lastindex)
    return None, None


def _clause(lines: list[str], i: int, kind: str) -> str:
    line = lines[i]
    if kind == "row":
        start = i
        while start > 0 and TABLE_RE.match(lines[start - 1]):
            start -= 1
        header = lines[start] if start < i and not TABLE_SEP_RE.match(lines[start]) else ""
        return f"{header}\n{line}".strip("\n") if header and header != line else line
    out = [line]
    if kind == "item":
        base = _indent(line)
        for nxt in lines[i + 1:]:
            if not nxt.strip() or HEADING_RE.match(nxt):
                break
            if ITEM_RE.match(nxt) and _indent(nxt) <= base:
                break
            out.append(nxt)
    elif kind == "head":
        for nxt in lines[i + 1:]:
            if HEADING_RE.match(nxt) or _definition(nxt)[0]:
                break
            out.append(nxt)
    else:
        for nxt in lines[i + 1:]:
            if not nxt.strip() or HEADING_RE.match(nxt) or _definition(nxt)[0]:
                break
            out.append(nxt)
    return "\n".join(out).strip("\n")


def _appendix_prefixes(text: str) -> set[str]:
    m = re.search(r"^##\s+Appendix:\s*Interview\s+Transcript\b", text, re.MULTILINE | re.IGNORECASE)
    if not m:
        return set()
    out = set()
    for line in text[m.end():].splitlines():
        kind, rid = _definition(line)
        if kind == "head":
            out.add(rid.split("-")[0])
    return out


def index_text(text: str) -> dict:
    lines = _body_lines(text)
    cited = _appendix_prefixes(text)
    ids: list[str] = []
    clauses: dict[str, list[str]] = {}
    for i, line in enumerate(lines):
        kind, rid = _definition(line)
        if not kind or rid.split("-")[0] in cited:
            continue
        clause = _clause(lines, i, kind)
        if rid not in clauses:
            ids.append(rid)
            clauses[rid] = []
        if clause not in clauses[rid]:
            clauses[rid].append(clause)
    synthetic = False
    if not ids:
        synthetic = True
        for i, line in enumerate(lines):
            if ITEM_RE.match(line) and _indent(line) == 0:
                rid = f"SPEC-{len(ids) + 1}"
                ids.append(rid)
                clauses[rid] = [_clause(lines, i, "item")]
    return {
        "ids": ids,
        "clauses": {k: "\n\n".join(v) for k, v in clauses.items()},
        "synthetic": synthetic,
        "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "bytes": len(text.encode("utf-8")),
        "global_sections": global_sections(text),
    }


GLOBAL_TITLE = re.compile(r"invariant|constraint|mandatory|non-functional|global|out of scope", re.IGNORECASE)


def global_sections(text: str) -> str:
    """Verbatim ``##`` sections whose title names rules that bind every slice
    (global invariants, constraints, mandatory rules, out of scope). Every
    slice brief carries them whole."""
    lines = _body_lines(text)
    out, keep = [], False
    for line in lines:
        m = re.match(r"^##\s+(.+?)\s*$", line)
        if m:
            keep = bool(GLOBAL_TITLE.search(m.group(1)))
        if keep:
            out.append(line)
    return "\n".join(out).strip("\n")


def index_file(path: Path) -> dict:
    data = index_text(Path(path).read_text(encoding="utf-8"))
    data["spec"] = str(path)
    return data
