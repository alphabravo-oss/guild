# Interview Transcript: typed-state-empty-fixture

*Verbatim Q/A record. Phase 2 fixture — exercises sentinel exemption from Jaccard rule 3.*

This fixture has invariants and contracts populated, but NO state-machine language —
state-transitions table will be a SENTINEL row. The sentinel row must be exempt from
the Jaccard ≥0.7 content-difference check (CONTEXT.md Pitfall 2).

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

## Q-005
**Question:** Does this feature have any state machine?
**Options presented:** yes | no — stateless

## A-005
None — user confirms this is a stateless CSS color change with no state machine. [from Q-005]
