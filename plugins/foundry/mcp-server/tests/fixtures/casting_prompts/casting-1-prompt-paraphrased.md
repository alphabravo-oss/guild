# Casting 1 — Login surface (paraphrased prompt)

*Phase 8 fixture — body cites only A-001/A-002/A-003/A-004/A-006 directly; the implicit-fact answer surfaces ONLY through the `<contracts>` typed-row citation. Drives PARAPHRASED verdict for the corresponding cell with citation_chain pointing into `<contracts>`. Locked decision A: typed-row indirection IS the canonical PARAPHRASED state.*

---

<spec_requirements>
- FR-1: The login endpoint MUST return a JWT token shape on valid credentials.
- US-1: As a registered user, I want to authenticate with username/password so I can receive a session token.
</spec_requirements>

---

## Build directive

Implement the login endpoint surface contract per A-001: POST /api/login returns 200 with `{token: str, expires_at: str}` on valid credentials; 401 on invalid; 400 on malformed payloads. The surface is stateless per the GI-001 invariant.

The state transition lifecycle from A-002 (UNAUTHENTICATED → AUTHENTICATED on valid credentials) is observable through the response. Apply rate-limiting per A-003 (10 req/min per IP via the existing middleware). Log every attempt per A-004 with timestamp + IP + outcome — but NEVER log passwords. Hash passwords per the contracts table specification. Token TTL is 24 hours per A-006.

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

*Casting 1 — paraphrased prompt: A-005 carried via typed-row citation, NOT via body literal.*
