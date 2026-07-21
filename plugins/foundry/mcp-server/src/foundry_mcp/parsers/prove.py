"""Parse PROVE stream verdicts and citations."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum


class Verdict(str, Enum):
    VERIFIED = "VERIFIED"
    HOLLOW = "HOLLOW"
    PARTIAL = "PARTIAL"
    LETTER_ONLY = "LETTER-ONLY"
    MISSING = "MISSING"
    WRONG = "WRONG"
    UNKNOWN = "UNKNOWN"

    @classmethod
    def from_str(cls, s: str) -> "Verdict":
        s = s.strip().upper().replace(" ", "-")
        for v in cls:
            if v.value == s:
                return v
        return cls.UNKNOWN


@dataclass
class CodeReference:
    """A file:line reference from a PROVE verdict."""

    file: str
    line: int | None = None

    def __str__(self) -> str:
        if self.line:
            return f"{self.file}:{self.line}"
        return self.file


@dataclass
class ProveVerdict:
    """A single parsed verdict from a PROVE report."""

    id: str
    description: str
    verdict: Verdict
    code_refs: list[CodeReference] = field(default_factory=list)
    cited_spec_text: list[str] = field(default_factory=list)
    reasoning: str = ""


# Matches "### VC-1: description" or "**VC-1**: description" headings
_HEADING_PATTERN = re.compile(
    r"^#{2,4}\s+(VC-\d+(?:\.\d+)?)[:\s—–-]+(.+?)$", re.MULTILINE
)

# Matches verdict lines like "**Verdict:** VERIFIED" or "Verdict: HOLLOW"
_VERDICT_PATTERN = re.compile(
    r"\*{0,2}Verdict:?\*{0,2}[:\s]+(\w[\w-]*)", re.IGNORECASE
)

# Matches file:line references
_CODE_REF_PATTERN = re.compile(r"`([^`]+?):(\d+)`|(?:^|\s)(\S+\.\w+):(\d+)")

# Matches quoted spec text (> prefixed or in quotes)
_SPEC_QUOTE_PATTERN = re.compile(r'^>\s*(.+)$|"([^"]{10,})"', re.MULTILINE)


def parse_prove_report(text: str) -> list[ProveVerdict]:
    """Parse a PROVE report into structured verdicts.

    Expects the format:
        ### VC-N: Description
        **Verdict:** VERIFIED|HOLLOW|PARTIAL|MISSING|WRONG|LETTER-ONLY
        ...code references, spec quotes, reasoning...
    """
    verdicts: list[ProveVerdict] = []

    # Split by VC headings
    headings = list(_HEADING_PATTERN.finditer(text))
    if not headings:
        return verdicts

    for i, heading in enumerate(headings):
        vc_id = heading.group(1)
        description = heading.group(2).strip()

        # Get section text (until next heading or end)
        start = heading.end()
        end = headings[i + 1].start() if i + 1 < len(headings) else len(text)
        section = text[start:end]

        # Extract verdict
        verdict_match = _VERDICT_PATTERN.search(section)
        verdict = Verdict.from_str(verdict_match.group(1)) if verdict_match else Verdict.UNKNOWN

        # Extract code references
        code_refs: list[CodeReference] = []
        for m in _CODE_REF_PATTERN.finditer(section):
            file_path = m.group(1) or m.group(3)
            line_num = m.group(2) or m.group(4)
            if file_path and not file_path.startswith("http"):
                code_refs.append(CodeReference(file=file_path, line=int(line_num) if line_num else None))

        # Extract spec quotes
        cited_spec: list[str] = []
        for m in _SPEC_QUOTE_PATTERN.finditer(section):
            quote = (m.group(1) or m.group(2) or "").strip()
            if quote:
                cited_spec.append(quote)

        verdicts.append(ProveVerdict(
            id=vc_id,
            description=description,
            verdict=verdict,
            code_refs=code_refs,
            cited_spec_text=cited_spec,
            reasoning=section.strip(),
        ))

    return verdicts


def count_verdicts(verdicts: list[ProveVerdict]) -> dict[str, int]:
    """Count verdicts by type."""
    counts: dict[str, int] = {}
    for v in verdicts:
        key = v.verdict.value
        counts[key] = counts.get(key, 0) + 1
    return counts
