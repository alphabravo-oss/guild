# Casting 1 — Login surface (section-ref-only prompt)

*Phase 8 fixture — body cites `[per spec §3.2]` (section number) instead of A-NNN literal; A-005 NOT in body and NOT in any typed-table row. Drives DROPPED verdict per RESEARCH.md Pitfall 5: section-number references are NOT load-bearing for INTENT-01 traceability — only A-NNN literals + typed-row citations count.*

---

<spec_requirements>
- FR-1: The login endpoint MUST return a JWT token shape on valid credentials.
- US-1: As a registered user, I want to authenticate with username/password so I can receive a session token.
</spec_requirements>

---

## Build directive

Implement the login endpoint surface contract per spec §3.2: POST /api/login returns 200 with `{token: str, expires_at: str}` on valid credentials; 401 on invalid; 400 on malformed payloads. The surface is stateless per spec §3.1.

The state transition lifecycle from spec §3.3 (UNAUTHENTICATED → AUTHENTICATED on valid credentials) is observable through the response. Apply rate-limiting per spec §4.1 (10 req/min per IP via the existing middleware). Log every attempt per spec §4.2 with timestamp + IP + outcome — but NEVER log passwords. Hash passwords per the standard project policy. Token TTL is 24 hours per spec §3.5.

---

<invariants>
| ID | statement | applies-to | citation |
|----|-----------|------------|----------|
| GI-001 | The login surface stays stateless. | login-surface | [per §3.1] |
</invariants>

<state_transitions>
| ID | from-state | to-state | trigger | citation |
|----|------------|----------|---------|----------|
| ST-001 | UNAUTHENTICATED | AUTHENTICATED | POST /api/login with valid credentials | [per §3.3] |
</state_transitions>

<contracts>
| ID | surface | input | output | errors | citation |
|----|---------|-------|--------|--------|----------|
| CT-001 | POST /api/login | `{username: str, password: str}` | `{token: str, expires_at: str}` | `401 invalid_credentials`, `400 malformed_payload` | [per §3.4] |
</contracts>

---

*Casting 1 — section-ref-only prompt: ALL citations use section-number form; ZERO A-NNN literals; A-005 effectively DROPPED.*
