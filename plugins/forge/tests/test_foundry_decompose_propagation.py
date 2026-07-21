"""Phase 2 RED stubs for plugins/foundry/commands/start.md F0.5/F0.9 propagation.

Wave 0 (Plan 02-01) baseline: every test in this file MUST be RED initially.
Plan 02-04 turns them green by extending start.md as follows:

  - F0.5 V2 step 6 prompt template gains three new top-level blocks
    (<invariants>, <state_transitions>, <contracts>) immediately AFTER the
    existing <global_invariants> block and BEFORE <spec_requirements>. Block
    order is locked per RESEARCH.md Pitfall 7.
  - F0.5 V3 PACKET-DERIVED template DOES NOT receive these blocks (V3's
    flow-delta is its structural anchor; CONTEXT.md F0.5 propagation
    strategy explicitly excludes V3).
  - F0.9 step 7 gains three new sub-checks (7h/7i/7j) parallel to existing
    7e (<global_invariants>) and 7g (<mandatory_rules>) — each verifies one
    of the three new prompt blocks is byte-identical to its manifest field.

Tests read start.md from disk (no subprocess) and assert structural content
via regex. No mocks — the real markdown file is the contract.

No tests use ``@pytest.mark.skip`` or ``xfail``. Failures here are the red
baseline by design.
"""

from __future__ import annotations

import re
from pathlib import Path


# Resolve start.md once for all three tests.
START_MD_PATH = (
    Path(__file__).resolve().parent.parent.parent
    / "foundry"
    / "commands"
    / "start.md"
)


def _read_start_md() -> str:
    """Read plugins/foundry/commands/start.md or fail with a clear diagnostic."""
    if not START_MD_PATH.is_file():
        raise FileNotFoundError(
            f"plugins/foundry/commands/start.md not found at {START_MD_PATH}. "
            "Phase 2 propagation tests require this file."
        )
    return START_MD_PATH.read_text(encoding="utf-8")


# -----------------------------------------------------------------------------
# Test 1: V2 step 6 prompt template contains the three new typed blocks
#         in the locked order — between <global_invariants> and <spec_requirements>
# -----------------------------------------------------------------------------
def test_typed_blocks_in_casting_template():
    """TYPE-01 #3 — F0.5 V2 prompt template must propagate typed sections.

    Locked block order (RESEARCH.md Pitfall 7):

        <mandatory_rules>
          ...
        </mandatory_rules>
        <global_invariants>
          ...
        </global_invariants>
        <invariants>            <-- NEW (Plan 02-04)
          ...
        </invariants>
        <state_transitions>     <-- NEW (Plan 02-04)
          ...
        </state_transitions>
        <contracts>             <-- NEW (Plan 02-04)
          ...
        </contracts>
        <spec_requirements>
          ...
        </spec_requirements>
        ...

    The regex below uses ``re.DOTALL`` so ``.*?`` spans newlines, and the
    lazy quantifier ensures the assertion pinpoints the FIRST occurrence
    (the V2 template at start.md:102-128). The V3 template's
    <global_invariants> block at start.md:194-196 has NO subsequent
    <invariants> block, so the regex will NOT incidentally match V3
    content first if V2 lacks the new blocks.

    Until Plan 02-04 lands the three new blocks, this regex does not match
    and the assertion fails — RED baseline.
    """
    text = _read_start_md()

    # Match the V2 ordered block sequence inside the V2 template. The lazy
    # ``.*?`` handles arbitrary content (placeholder text + blank lines)
    # between blocks. Locked-order: global_invariants → invariants →
    # state_transitions → contracts → spec_requirements.
    locked_order_re = re.compile(
        r"<global_invariants>"
        r".*?"
        r"</global_invariants>"
        r".*?"
        r"<invariants>"
        r".*?"
        r"</invariants>"
        r".*?"
        r"<state_transitions>"
        r".*?"
        r"</state_transitions>"
        r".*?"
        r"<contracts>"
        r".*?"
        r"</contracts>"
        r".*?"
        r"<spec_requirements>",
        re.DOTALL,
    )

    assert locked_order_re.search(text), (
        "F0.5 V2 step 6 prompt template must contain the three Phase 2 typed "
        "blocks (<invariants>, <state_transitions>, <contracts>) in fixed "
        "order between <global_invariants> and <spec_requirements>. "
        "Plan 02-04 inserts these blocks; Plan 02-01 stub locks the order. "
        "Locked order per RESEARCH.md Pitfall 7."
    )


# -----------------------------------------------------------------------------
# Test 2: V3 PACKET-DERIVED template does NOT receive the typed blocks
# -----------------------------------------------------------------------------
def test_v3_template_does_not_get_typed_blocks():
    """CONTEXT.md "F0.5 DECOMPOSE propagation strategy" — V3 unchanged.

    V3's flow-delta is the structural anchor; spec.md is compatibility-only
    in V3 mode. Adding typed blocks to V3 castings would be a regression
    against the V3 design (the per-packet <upstream_anchor> /
    <prerequisite_hops> / <this_hop> / <downstream_contract> blocks are V3's
    grounding mechanism, not a typed-section excerpt).

    This test is VACUOUSLY GREEN today (V3 has no typed blocks because
    Plan 02-04 has not landed). After Plan 02-04 lands, the V3 template
    section MUST STILL contain none of <invariants> / <state_transitions> /
    <contracts> — explicit guard against accidental V3 contamination.

    Implementation: locate the V3 section header and truncate text to the
    region between '### F0.5 V3' and the next top-level '###' heading.
    Assert none of the three Phase 2 block tags appear in that region.
    """
    text = _read_start_md()

    v3_header_re = re.compile(r"^###\s+F0\.5\s+V3\b", re.MULTILINE)
    v3_match = v3_header_re.search(text)
    assert v3_match is not None, (
        "Expected a '### F0.5 V3' heading in commands/start.md. "
        "If the heading was renamed, update this test."
    )

    # Truncate to next top-level '###' heading (or EOF) — this is the V3
    # section's body.
    next_heading_re = re.compile(r"^###\s+\S", re.MULTILINE)
    next_match = next_heading_re.search(text, pos=v3_match.end())
    v3_section = text[v3_match.start() : (next_match.start() if next_match else len(text))]

    for forbidden in ("<invariants>", "<state_transitions>", "<contracts>"):
        assert forbidden not in v3_section, (
            f"V3 PACKET-DERIVED template must NOT contain {forbidden!r} — "
            f"Phase 2 typed blocks apply only to V2 mode "
            f"(CONTEXT.md F0.5 propagation strategy). "
            f"Found {forbidden!r} inside V3 section."
        )


# -----------------------------------------------------------------------------
# Test 3: F0.9 step 7 sub-checks 7h / 7i / 7j exist for the three typed blocks
# -----------------------------------------------------------------------------
def test_f09_subchecks_for_typed_blocks_exist():
    """TYPE-01 #3 — F0.9 VALIDATE must verify byte-identical propagation.

    Existing sub-checks (start.md:286):
      - 7e: <global_invariants> propagation byte-identical
      - 7g: <mandatory_rules> propagation byte-identical

    Plan 02-04 adds three sibling sub-checks:
      - 7h: <invariants> propagation byte-identical
      - 7i: <state_transitions> propagation byte-identical
      - 7j: <contracts> propagation byte-identical

    The regex tolerates Plan 02-04's wording flexibility: the sub-letter may
    appear as ``7h.``, ``7h:``, ``sub-check 7h``, etc. (CONTEXT.md "Claude's
    Discretion"). The assertion locks only that EACH typed block has a
    sub-check entry referencing the block by name.

    Until Plan 02-04 lands the new sub-checks, this assertion fails —
    RED baseline.
    """
    text = _read_start_md()

    # Helper: match either 'sub-check 7X' / '7X.' / '7X:' followed by anything
    # then the block-name within the same VALIDATE step 7 paragraph.
    def _has_subcheck(letter: str, block_tag: str) -> bool:
        # Match the sub-letter token (7h / 7i / 7j) on the same line that
        # also references the block tag <invariants> / <state_transitions> /
        # <contracts>. Tolerate either order.
        pattern = re.compile(
            r"(?:sub-check\s+7"
            + letter
            + r"|7"
            + letter
            + r"[:.\)]|7"
            + letter
            + r"\s+verifies)"
            r".*?"
            + re.escape(block_tag),
            re.IGNORECASE | re.DOTALL,
        )
        # Also accept the reverse order (block_tag first, then sub-letter
        # within ~200 chars) — Plan 02-04's exact phrasing is flexible.
        reverse = re.compile(
            re.escape(block_tag)
            + r".{0,200}?"
            + r"(?:sub-check\s+7"
            + letter
            + r"|7"
            + letter
            + r"[:.\)])",
            re.IGNORECASE | re.DOTALL,
        )
        return bool(pattern.search(text) or reverse.search(text))

    assert _has_subcheck("h", "<invariants>"), (
        "F0.9 step 7 must contain a sub-check '7h' referencing <invariants> "
        "propagation byte-identical (parallel to existing 7e for "
        "<global_invariants>); Plan 02-04 adds this."
    )
    assert _has_subcheck("i", "<state_transitions>"), (
        "F0.9 step 7 must contain a sub-check '7i' referencing "
        "<state_transitions> propagation byte-identical; Plan 02-04 adds this."
    )
    assert _has_subcheck("j", "<contracts>"), (
        "F0.9 step 7 must contain a sub-check '7j' referencing <contracts> "
        "propagation byte-identical; Plan 02-04 adds this."
    )
