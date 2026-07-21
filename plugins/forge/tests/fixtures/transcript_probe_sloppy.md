# Transcript: probe-sloppy fixture

*Verbatim Q/A record. Phase 6 fixture — exercises PROBE-01 adversarial-spec-reviewer rubric. The A-007 body deliberately carries a transcript-grounded ambiguity (bcrypt OR argon2) that downstream Locked rows resolve unilaterally — Plan 06-03's reviewer must surface this as a flag.*

---

## Q-001
**Question:** Where should the agent-dispatch logic live?
**Options presented:** operator package | dispatcher package | new package

## A-001 [ARCH_INVARIANT]
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

## A-004
Python 3.11 [from Q-004]

## Q-005
**Question:** What scale of casting throughput?
**Options presented:** less-than-10/hr | 10-100/hr | 100-plus/hr

## A-005
less-than-10/hr [from Q-005]

## Q-006
**Question:** Where does the validator state live?
**Options presented:** in-memory | redis | disk

## A-006
Validator state stays stateless — every spec validation runs fresh from disk with no caching layer. [from Q-006]

## Q-007
**Question:** What hashing algorithm should the auth flow use?
**Options presented:** bcrypt | argon2 | scrypt

## A-007
We use bcrypt for hashing. Or argon2 — both are fine for our threat model. Pick one when the spec is written. [from Q-007]

## Q-008
**Question:** Which hashing algorithm gets locked into the Contracts row?
**Options presented:** follow A-007 | pick bcrypt | pick argon2

## A-008
The Contracts row will lock bcrypt. Argon2 was on the table per A-007 but the spec only carries one. [from Q-008]
