---
spec_format_version: v2.0
---

# Spec: spec-intent-v20-legacy

*Phase 8 fixture — legacy v2.0 spec (pre-Phase-1 implicit-fact extraction; pre-Phase-2 typed tables). No `## Global Invariants` / `## State Transitions` / `## Contracts` typed tables; no implicit-fact A-AUTO-NNN entries. Used by `test_v20_spec_skips_intent_carrier` to exercise Phase 3's stream-skip path: a v2.0 spec MUST NOT engage INTENT-01 because INTENT-01 has `min_spec_format_version: v2.1`.*

---

## Mandatory rules

1. Authenticate user credentials before issuing tokens.
2. Reject malformed payloads with a 4xx response.

---

<spec_requirements>
- FR-1: Login endpoint returns a token on valid credentials.
- US-1: Registered user authenticates via username/password.
</spec_requirements>

---

## Acceptance criteria

- Surface contract honored at runtime.

---

## Appendix: Interview Transcript

## Q-001
**Question:** What is the surface contract?

## A-001 [Locked]
POST /api/login returns 200 with a token on valid credentials, 401 on invalid. [from Q-001]

## Q-002
**Question:** What logs are required?

## A-002 [Locked]
Log every login attempt with timestamp and outcome. [from Q-002]

## Q-003
**Question:** Rate-limit policy?

## A-003 [Locked]
Rate-limit at 10 requests per minute per IP. [from Q-003]

## Q-004
**Question:** Token TTL?

## A-004 [Locked]
Tokens expire after 24 hours. [from Q-004]
