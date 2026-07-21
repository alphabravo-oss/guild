# Casting 2 — Auth middleware (clean prompt)

*Phase 8 fixture — second casting touching the same spec_intent_clean.md from a different angle. Cites A-002/A-003/A-004/A-005/A-006 plus A-AUTO-001/A-AUTO-002 in body so the (A-AUTO-NNN, casting-2) cells are PROPAGATED.*

---

<spec_requirements>
- FR-1: The login endpoint MUST return a JWT token shape on valid credentials.
- US-1: As a registered user, I want to authenticate with username/password so I can receive a session token.
</spec_requirements>

---

## Build directive

Implement the auth middleware that consumes tokens issued by the login endpoint. The state transition from A-002 (UNAUTHENTICATED → AUTHENTICATED) is preserved across requests via the JWT signature verification. Apply rate-limiting per A-003 at the same 10 req/min per IP threshold for parity with login. Logging per A-004 — every middleware-rejected request logs timestamp + IP + outcome.

Token verification requires the secret derived from the deployment manifest per A-AUTO-001 (infra/k8s/login.yaml carries the auth-secret reference) and capacity per A-AUTO-002 (~500 logins/s peak — middleware MUST sustain that). Password hashing details from A-005 are out-of-scope for the middleware (consumed not produced). Token TTL per A-006 (24h) drives the middleware's expiry-check branch.

A-001 surface boundary is the upstream contract; middleware reads tokens from the Authorization header and validates against the issuer.

---

<invariants>
| ID | statement | applies-to | citation |
|----|-----------|------------|----------|
| GI-001 | The login surface stays stateless. | login-surface | [from A-001] |
</invariants>

<state_transitions>
| ID | from-state | to-state | trigger | citation |
|----|------------|----------|---------|----------|
| ST-001 | UNAUTHENTICATED | AUTHENTICATED | POST /api/login with valid credentials | [from A-002] |
</state_transitions>

<contracts>
| ID | surface | input | output | errors | citation |
|----|---------|-------|--------|--------|----------|
| CT-001 | POST /api/login | `{username: str, password: str}` | `{token: str, expires_at: str}` | `401 invalid_credentials`, `400 malformed_payload` | [from A-005] |
</contracts>

---

*Casting 2 — clean prompt.*
