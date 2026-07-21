# Interview Transcript: typed-dangling-citation-fixture

*Verbatim Q/A record. Phase 2 fixture — NEGATIVE TEST for rule 2 (citation integrity).*

This fixture is paired with a synthesized spec containing an invariants row whose
citation cell reads `[from A-999]`, but the transcript has no `## A-999` block.
Validator must FAIL with `TYPED_ROW_DANGLING`.

The conftest spec-builder injects the dangling row at synthesis time. The transcript
itself contains 3-4 valid A-NNN entries so the OTHER validator structural checks pass.

---

## Q-001
**Question:** Where should the agent-dispatch logic live?
**Options presented:** operator package | dispatcher package | new package

## A-001 [ARCH_INVARIANT, IMPLICIT_FACT:DEPLOYMENT]
The operator stays generic — only the dispatcher knows about specific agent types. [from Q-001]

## Q-002
**Question:** What is the surface contract for casting acceptance?
**Options presented:** REST endpoint | MCP tool | CLI

## A-002
The Foundry-Accept-Casting tool takes a casting_id (string) and returns {accepted: bool, provenance: {sha256, mtime}}. Errors include INVALID_CASTING_ID and EVIDENCE_MISMATCH. [from Q-002]

## Q-003
**Question:** What runtime do agents use?
**Options presented:** Python 3.11 | Python 3.12 | Node | Go

## A-003 [IMPLICIT_FACT:RUNTIME]
Python 3.11 [from Q-003]

## Q-004
**Question:** What scale of casting throughput?
**Options presented:** ≤10/hr | 10-100/hr | 100+/hr

## A-004 [IMPLICIT_FACT:SCALE]
≤10/hr [from Q-004]
