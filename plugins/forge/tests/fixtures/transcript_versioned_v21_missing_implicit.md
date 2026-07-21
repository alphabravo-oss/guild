<!-- spec_format_version: v2.1 -->
# Interview Transcript: versioned-v21-missing-implicit-fixture

*Verbatim Q/A record. Phase 3 fixture — NEGATIVE TEST for TYPE-02 warn→fail upgrade (IMPLICIT_FACT tags missing).*

This fixture pairs with a synthesized spec where:
  - frontmatter declares `spec_format_version: v2.1`
  - typed-table sections ARE populated (`with_typed_tables=True`)
  - [IMPLICIT_FACT:*] tags are STRIPPED via `with_implicit_fact_tags=False`
    in the conftest fixture call (Plan 03-01 fixture supports this kwarg)

Expected validator behavior in Phase 3 (post Plan 03-03):
  - Phase 1's IMPLICIT_FACT_SKIPPED: FAIL (was WARN under v2.0; v2.1 upgrades to FAIL)
  - IMPLICIT_FACT_SKIPPED token surfaces in stdout
  - TYPE_TABLES_MISSING: NOT EMITTED (typed tables present — keeps the negative
    signal narrow to the IMPLICIT_FACT case)
  - returncode != 0

Plan 03-03 wires `IMPLICIT_FACT_SKIPPED` as `report.fail()` when version >= v2.1.

The fixture has [ARCH_INVARIANT] + ample Locked content so the typed-tables
synthesis path produces non-trivial rows that pass rules 1/2/3 cleanly.

---

## Q-001
**Question:** Where should the agent-dispatch logic live?
**Options presented:** operator package | dispatcher package | new package

## A-001 [ARCH_INVARIANT, IMPLICIT_FACT:DEPLOYMENT]
The operator stays generic — only the dispatcher knows about specific agent types. The operator must not import any agent-specific package. [from Q-001]

## Q-002
**Question:** What is the casting lifecycle?
**Options presented:** linear | branching | cyclical

## A-002
When a casting reaches DONE, the orchestrator transitions it from RUNNING to COMPLETED. After F4 ASSAY signs off, COMPLETED becomes ARCHIVED. [from Q-002]

## Q-003
**Question:** What is the surface contract for casting acceptance?
**Options presented:** REST endpoint | MCP tool | CLI

## A-003
The Foundry-Accept-Casting tool takes a casting_id (string) and returns {accepted: bool, provenance: {sha256, mtime}}. Errors include INVALID_CASTING_ID and EVIDENCE_MISMATCH. [from Q-003]
