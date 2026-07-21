<!-- spec_format_version: v2.1 -->
# Interview Transcript: versioned-v21-missing-typed-fixture

*Verbatim Q/A record. Phase 3 fixture — NEGATIVE TEST for TYPE-02 warn→fail upgrade (typed sections missing).*

This fixture pairs with a synthesized spec where:
  - frontmatter declares `spec_format_version: v2.1`
  - typed-table sections are OMITTED (`with_typed_tables=False`)
  - [IMPLICIT_FACT:*] tags ARE present so Phase 1's check passes — isolating
    the negative signal to TYPE_TABLES_MISSING only

Expected validator behavior in Phase 3 (post Plan 03-03):
  - rule 1 (typed-table presence): FAIL (was WARN under v2.0; v2.1 upgrades to FAIL)
  - TYPE_TABLES_MISSING token surfaces in stdout
  - IMPLICIT_FACT_SKIPPED: NOT EMITTED (tags present — keeps the negative
    signal narrow to the typed-tables case)
  - returncode != 0

Plan 03-03 wires `TYPE_TABLES_MISSING` as `report.fail()` when version >= v2.1.

---

## Q-001
**Question:** What CSS color should the badge use?
**Options presented:** red | orange | yellow

## A-001 [IMPLICIT_FACT:OTHER]
red — chosen because brand guidelines require attention-grabbing color [from Q-001]

## Q-002
**Question:** Should the badge animate on hover?
**Options presented:** yes | no

## A-002 [IMPLICIT_FACT:OTHER]
no — animation distracts from content [from Q-002]

## Q-003
**Question:** Where in the page does the badge render?
**Options presented:** top-right | top-left | bottom-right | bottom-left

## A-003 [IMPLICIT_FACT:OTHER]
top-right [from Q-003]

## Q-004
**Question:** What runtime do agents use?
**Options presented:** Python 3.11 | Python 3.12 | Node | Go

## A-004 [IMPLICIT_FACT:RUNTIME]
Python 3.11 [from Q-004]
