---
spec_format_version: v2.1
---

# Spec: spec-test-deriver-simple

*Phase 7 fixture — minimal v2.1 spec. Provides populated `<spec_requirements>` block, typed `## Contracts` table with one CT-001 row, and minimal Global Invariants + State Transitions tables to satisfy TYPE-01 typed-table presence. Mirrors the shape `validate-spec.py` enforces in v2.1 mode.*

---

## Mandatory rules

1. Authenticate user credentials before issuing tokens.
2. Reject malformed payloads with a 4xx response.
3. Persist no plaintext credentials.

---

## Global Invariants

| ID | statement | applies-to | citation |
|----|-----------|------------|----------|
| GI-001 | The login surface stays stateless — every request authenticates fresh from the credential store with no session cache. | login-surface | [from A-001] |

---

## State Transitions

| ID | from-state | to-state | trigger | citation |
|----|------------|----------|---------|----------|
| ST-001 | UNAUTHENTICATED | AUTHENTICATED | POST /api/login with valid credentials | [from A-001] |

---

## Contracts

| ID | surface | input | output | errors | citation |
|----|---------|-------|--------|--------|----------|
| CT-001 | POST /api/login | `{username: str, password: str}` | `{token: str, expires_at: str}` | `401 invalid_credentials`, `400 malformed_payload` | [from A-001] |

---

<spec_requirements>
- FR-1: The login endpoint MUST return a JWT token shape on valid credentials. [from A-001]
- FR-2: The login endpoint MUST reject invalid credentials with a 401 status. [from A-001]
- US-1: As a registered user, I want to authenticate with username/password so I can receive a session token. [from A-001]
</spec_requirements>

---

## Acceptance criteria

- A-001 surface contract honored at runtime; observable shape matches CT-001.
- All three requirements (FR-1, FR-2, US-1) cited verbatim in this spec body.

---

*Spec format: v2.1 — engages TEST-01, EVID-01, EVID-02, PROBE-01, INTV-01, TYPE-01, TYPE-02 streams per F0.5 step 2b roster.*
