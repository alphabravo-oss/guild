# Interview Transcript: versioned-legacy-fixture

*Verbatim Q/A record. Phase 3 fixture — exercises TYPE-02 backwards-compat path (no frontmatter, defaults to implicit v2.0).*

This fixture has NO `<!-- spec_format_version: ... -->` comment, so the conftest
`run_versioned_validator_subprocess` fixture (when called without an explicit
`spec_format_version=` kwarg) emits NO frontmatter on the synthesized spec.
Phase 3's frontmatter parser (Plan 03-02) defaults missing frontmatter to v2.0.

Body shape is intentionally legacy v4.2.0:
  - few Q/A pairs
  - simple Locked entries
  - NO [IMPLICIT_FACT:*] tags  → trips Phase 1 IMPLICIT_FACT_SKIPPED warn
  - NO [ARCH_INVARIANT] tags   → no GI bullets needed (typed tables omitted via with_typed_tables=False)
  - NO typed-table-grade content

Expected validator behavior under implicit v2.0 (Phase 3 post Plan 03-03):
  - IMPLICIT_FACT_SKIPPED:   WARN (not fail) — backwards-compat preserved
  - TYPE_TABLES_MISSING:     WARN (not fail) — backwards-compat preserved
  - returncode == 0

This is the same expected behavior Phase 2 already produces; Phase 3 must
NOT regress it for the no-frontmatter and explicit-v2.0 paths.

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
