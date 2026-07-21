<!-- spec_format_version: v2.1 -->
# Interview Transcript: versioned-modern-fixture

*Verbatim Q/A record. Phase 3 fixture — exercises TYPE-02 happy path (v2.1 declared, all rules pass at fail-level).*

This fixture pairs with a synthesized spec where:
  - frontmatter declares `spec_format_version: v2.1`
  - typed-table sections are populated (`with_typed_tables=True`)
  - at least one transcript answer carries an [IMPLICIT_FACT:*] tag

Expected validator behavior in Phase 3 (post Plan 03-03):
  - rule 1 (typed-table presence): PASS (tables present)
  - IMPLICIT_FACT_SKIPPED: NOT EMITTED (tag present)
  - SPEC_FORMAT_VERSION_UNKNOWN: NOT EMITTED (v2.1 in allowlist)
  - returncode == 0

The leading `<!-- spec_format_version: v2.1 -->` HTML comment is what the conftest
`run_versioned_validator_subprocess` fixture grep-extracts when the test does NOT
pass an explicit `spec_format_version=` kwarg. Tests that pass an explicit
kwarg ignore the comment.

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

## Q-004
**Question:** What runtime do agents use?
**Options presented:** Python 3.11 | Python 3.12 | Node | Go

## A-004 [IMPLICIT_FACT:RUNTIME]
Python 3.11 [from Q-004]

## Q-005
**Question:** What scale of casting throughput?
**Options presented:** ≤10/hr | 10-100/hr | 100+/hr

## A-005 [IMPLICIT_FACT:SCALE]
≤10/hr [from Q-005]
