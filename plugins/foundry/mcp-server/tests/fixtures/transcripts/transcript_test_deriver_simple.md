# Transcript: test-deriver-simple fixture

*Verbatim Q/A record. Phase 7 fixture — minimal pair for spec_test_deriver_simple.md. Carries A-001 Locked surface contract (POST /api/login → 200 + JWT cookie on valid creds; 401 on invalid) so TEST-01 has a single-row CT-001 to derive hypothesis-jsonschema strategies against.*

---

## Q-001
**Question:** What is the surface contract for the login endpoint?
**Options presented:** REST endpoint | MCP tool | CLI

## A-001 [Locked]
POST /api/login returns 200 with `{token: str, expires_at: str}` on valid credentials; returns 401 with `{error: "invalid_credentials"}` on invalid credentials. Malformed payloads return 400. The surface is stateless — every call authenticates fresh from the credential store. [from Q-001]

---

*Transcript locks A-001 at the surface-contract level. Spec spec_test_deriver_simple.md cites A-001 across GI-001 / ST-001 / CT-001 / FR-1 / FR-2 / US-1.*
