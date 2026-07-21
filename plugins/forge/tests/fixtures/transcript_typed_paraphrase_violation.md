# Interview Transcript: typed-paraphrase-violation-fixture

*Verbatim Q/A record. Phase 2 fixture — NEGATIVE TEST for rule 3 (Jaccard content-difference).*

This fixture is paired with a synthesized spec where one invariants row's content cells
overlap the same `## Global Invariants` section's prose paragraph at Jaccard ≥0.7.
Validator must FAIL with `TYPED_ROW_PARAPHRASE`.

## Jaccard calculation (manual verification)

The conftest spec-builder injects a prose paragraph adjacent to the invariants table:

    "The operator package must remain generic. Agent specific types live in the dispatcher only."

The injected invariants row content cells (statement | applies-to | violation):

    "operator package remain generic | operator package | agent specific types dispatcher"

Tokenization rules (per CONTEXT.md Content-difference scope):
  - lowercase + strip punctuation + split on whitespace
  - drop stop-words: {the, a, an, is, are, was, were, be, been, being, of, in, on, at,
    to, for, with, by, from, as, and, or, but, if, then, else, this, that, these, those}
  - dedupe (frozenset)

Row tokens (deduped, stop-words removed):
  {operator, package, remain, generic, agent, specific, types, dispatcher}  → 8 tokens

Prose tokens (deduped, stop-words removed):
  {operator, package, must, remain, generic, agent, specific, types, live, dispatcher, only}
  → 11 tokens

Intersection: {operator, package, remain, generic, agent, specific, types, dispatcher} → 8
Union:        {operator, package, must, remain, generic, agent, specific, types, live, dispatcher, only}
              → 11

Jaccard = |intersection| / |union| = 8 / 11 = 0.727... ≥ 0.7 ✓

Therefore: rule 3 must FAIL with TYPED_ROW_PARAPHRASE on this fixture.

---

## Q-001
**Question:** Where should the agent-dispatch logic live?
**Options presented:** operator package | dispatcher package | new package

## A-001 [ARCH_INVARIANT, IMPLICIT_FACT:DEPLOYMENT]
The operator package must remain generic — agent specific types live in dispatcher only. [from Q-001]

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
