"""Extract JSON blocks from markdown report files."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass


@dataclass
class JsonBlock:
    """A parsed JSON block from a markdown file."""

    data: dict | list
    start_line: int
    end_line: int
    raw: str


def extract_json_blocks(text: str) -> list[JsonBlock]:
    """Find all fenced ```json blocks in markdown text and parse them.

    Returns parsed blocks in document order. Use blocks[-1] for the
    canonical report JSON (convention: last block is the structured output).
    """
    blocks: list[JsonBlock] = []
    pattern = re.compile(r"^```json\s*\n(.*?)^```", re.MULTILINE | re.DOTALL)

    lines_before_pos: dict[int, int] = {}
    line_num = 1
    for i, ch in enumerate(text):
        lines_before_pos[i] = line_num
        if ch == "\n":
            line_num += 1

    for m in pattern.finditer(text):
        raw = m.group(1).strip()
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        start = lines_before_pos.get(m.start(), 1)
        end = lines_before_pos.get(m.end(), start)
        blocks.append(JsonBlock(data=data, start_line=start, end_line=end, raw=raw))

    return blocks


def extract_last_json(text: str) -> JsonBlock | None:
    """Return the last JSON block in a markdown file, or None."""
    blocks = extract_json_blocks(text)
    return blocks[-1] if blocks else None
