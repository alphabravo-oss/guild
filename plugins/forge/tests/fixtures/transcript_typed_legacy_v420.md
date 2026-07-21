# Interview Transcript: typed-legacy-v420-fixture

*Verbatim Q/A record. Phase 2 fixture — pre-Phase-2 transcript with no typed-table synthesis.*

This fixture exercises the BACKWARDS-COMPAT contract (TYPE-01 #4): a v4.2.0-style
transcript with no [ARCH_INVARIANT] tags, no state-machine language, no contract-
defining surfaces. The synthesized spec for this fixture OMITS the three typed-table
headings entirely (with_typed_tables=False).

Expected validator behavior in Phase 2:
  - rule 1 (presence) emits TYPE_TABLES_MISSING as report.warn() (returncode 0)
  - Phase 3 will upgrade this to report.fail() when spec_format_version >= v2.1

This fixture also has no IMPLICIT_FACT tags (in the closed vocabulary), so it
ALSO trips Phase 1's IMPLICIT_FACT_SKIPPED warning. Both warnings are
warn-not-fail, so exit code 0 is preserved.

---

## Q-001
**Question:** What CSS color should the badge use?
**Options presented:** red | orange | yellow

## A-001
red [from Q-001]

## Q-002
**Question:** Should the badge animate on hover?
**Options presented:** yes | no

## A-002
no [from Q-002]

## Q-003
**Question:** Where in the page does the badge render?
**Options presented:** top-right | top-left | bottom-right | bottom-left

## A-003
top-right [from Q-003]
