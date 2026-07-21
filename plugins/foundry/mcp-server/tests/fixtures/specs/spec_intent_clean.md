---
spec_format_version: v2.1
---

# Spec: spec-intent-clean

*Phase 8 fixture — minimal v2.1 spec for INTENT-01 intent-carrier coverage. Has populated `## Appendix: Interview Transcript` with 6 A-NNN entries (A-001..A-006) plus 2 A-AUTO-NNN entries; typed tables (Global Invariants / State Transitions / Contracts) cite specific A-NNN rows so PROPAGATED-via-typed verdicts have a structurally well-defined target.*

---

## Mandatory rules

1. Authenticate user credentials before issuing tokens.
2. Reject malformed payloads with a 4xx response.
3. Persist no plaintext credentials.
4. Hash passwords with a memory-hard algorithm.

---

## Global Invariants

| ID | statement | applies-to | citation |
|----|-----------|------------|----------|
| GI-001 | The login surface stays stateless — every request authenticates fresh from the credential store with no session cache. | login-surface | [from A-001] |

---

## State Transitions

| ID | from-state | to-state | trigger | citation |
|----|------------|----------|---------|----------|
| ST-001 | UNAUTHENTICATED | AUTHENTICATED | POST /api/login with valid credentials | [from A-002] |

---

## Contracts

| ID | surface | input | output | errors | citation |
|----|---------|-------|--------|--------|----------|
| CT-001 | POST /api/login | `{username: str, password: str}` | `{token: str, expires_at: str}` | `401 invalid_credentials`, `400 malformed_payload` | [from A-005] |

---

<spec_requirements>
- FR-1: The login endpoint MUST return a JWT token shape on valid credentials. [from A-001]
- US-1: As a registered user, I want to authenticate with username/password so I can receive a session token. [from A-002]
</spec_requirements>

---

## Acceptance criteria

- A-001 surface contract honored at runtime.
- A-002 state-transition observable.
- A-005 implicit-fact constraint (bcrypt hashing) verified at acceptance.

---

## Appendix: Interview Transcript

## Q-001
**Question:** What is the surface contract for the login endpoint?

## A-001 [Locked]
POST /api/login returns 200 with `{token: str, expires_at: str}` on valid credentials; 401 on invalid; 400 on malformed payloads. Stateless. [from Q-001]

## Q-002
**Question:** What state transition does login produce?

## A-002 [Locked]
The session moves from UNAUTHENTICATED to AUTHENTICATED only after POST /api/login succeeds with valid credentials. [from Q-002]

## Q-003
**Question:** What rate-limiting applies?

## A-003 [Locked]
Rate-limit at 10 requests per minute per IP via the existing middleware. [from Q-003]

## Q-004
**Question:** What logging is required?

## A-004 [Locked]
Log every login attempt with timestamp + IP + outcome — never log passwords. [from Q-004]

## Q-005
**Question:** How are passwords stored?

## A-005 [Locked]
Passwords are hashed with bcrypt (cost factor 12) and stored as opaque strings; the plaintext password never crosses persistence boundaries. [from Q-005]

## Q-006
**Question:** What token TTL is used?

## A-006 [Locked]
JWT tokens expire after 24 hours. [from Q-006]

## A-AUTO-001 [DEPLOYMENT]
The login endpoint deploys via the existing kubernetes manifest at infra/k8s/login.yaml. [auto-extracted]

## A-AUTO-002 [SCALE]
Expected peak load is ~500 logins per second based on the existing dashboard. [auto-extracted]
