"""Extract requirement identifiers from spec files.

D-150: WHICH FAMILIES exist is not decided here. Both patterns below used to
name their own seven — ``US|FR|AC|VC|NFR|TR|IR`` — so a spec's observable
truths, invariants, contracts, state transitions and locked requirements were
not requirements as far as this parser was concerned, and
``citation.verify_citations``' traceability matrix could not carry a row for
one. That was five hand-typed copies of one rule across the package; the
families are one declaration in ``schemas.vocab`` now, and the two patterns
here embed the exported pattern rather than restating its alternation.

What this module still decides is its own business and is unchanged: how a
requirement is ANCHORED in a line (leading text, then the id, then the
separator run) and how the requirement TEXT is captured after it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from foundry_mcp.schemas.vocab import REQUIREMENT_ID_RE


@dataclass
class Requirement:
    """A single requirement extracted from a spec."""

    id: str
    text: str
    line: int


# A requirement line: anything, then a requirement id, then the separator run,
# then the text. The id sub-pattern is the CANONICAL one, embedded rather than
# restated — it carries its own `\b` anchors, so the group closes around them
# and the two capture groups keep their numbers (1 = the id, 2 = the text).
_REQ_PATTERN = re.compile(
    r"^.*?(" + REQUIREMENT_ID_RE.pattern + r")[:\s—–-]*(.+?)$",
    re.MULTILINE,
)


def extract_requirements(text: str) -> dict[str, Requirement]:
    """Build a requirements map {id: Requirement} from spec text.

    Scans for lines carrying a requirement ID of any family the vocabulary
    declares, and extracts the requirement text from the rest of the line.
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
    """Return a sorted list of unique requirement IDs found in text.

    The second of the two copies this module carried. No anchoring of its own
    to preserve — it is the canonical pattern and nothing else — so it uses the
    export directly; ``group(0)`` because the exported alternation is
    non-capturing.
    """
    return sorted({m.group(0) for m in REQUIREMENT_ID_RE.finditer(text)})
