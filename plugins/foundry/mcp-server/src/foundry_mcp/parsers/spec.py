"""Extract requirement identifiers (US-N, FR-N, AC-N, VC-N) from spec files."""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass
class Requirement:
    """A single requirement extracted from a spec."""

    id: str
    text: str
    line: int


# Matches identifiers like US-1, FR-12, AC-3.2, VC-42
_REQ_PATTERN = re.compile(
    r"^.*?\b((?:US|FR|AC|VC|NFR|TR|IR)-\d+(?:\.\d+)?)\b[:\s—–-]*(.+?)$",
    re.MULTILINE,
)


def extract_requirements(text: str) -> dict[str, Requirement]:
    """Build a requirements map {id: Requirement} from spec text.

    Scans for lines containing requirement IDs (US-N, FR-N, AC-N, VC-N, etc.)
    and extracts the requirement text from the rest of the line.
    """
    reqs: dict[str, Requirement] = {}
    for m in _REQ_PATTERN.finditer(text):
        req_id = m.group(1)
        req_text = re.sub(r"^[\s*_:—–-]+", "", m.group(2)).strip().rstrip("*_").strip()
        # Calculate line number
        line = text[: m.start()].count("\n") + 1
        reqs[req_id] = Requirement(id=req_id, text=req_text, line=line)
    return reqs


def extract_requirement_ids(text: str) -> list[str]:
    """Return a sorted list of unique requirement IDs found in text."""
    ids = set()
    for m in re.finditer(r"\b((?:US|FR|AC|VC|NFR|TR|IR)-\d+(?:\.\d+)?)\b", text):
        ids.add(m.group(1))
    return sorted(ids)
