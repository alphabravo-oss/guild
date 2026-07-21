# Transcript: intent-clean fixture

*Phase 8 fixture — verbatim Q/A record matching spec_intent_clean.md's appendix shape. 6 A-NNN Locked answers + 2 A-AUTO-NNN implicit-fact entries (A-AUTO-001 [DEPLOYMENT], A-AUTO-002 [SCALE]). All A-001..A-006 carry traceable surface details that the typed-table rows in spec_intent_clean.md cite.*

---

## Q-001
**Question:** What is the surface contract for the login endpoint?

## A-001 [Locked]
POST /api/login returns 200 with `{token: str, expires_at: str}` on valid credentials; returns 401 with `{error: "invalid_credentials"}` on invalid; 400 on malformed payloads. The surface is stateless — every call authenticates fresh from the credential store. [from Q-001]

---

## Q-002
**Question:** What state transition does login produce?

## A-002 [Locked]
The session moves from UNAUTHENTICATED to AUTHENTICATED only after POST /api/login succeeds with valid credentials. The transition is observable through the AUTHENTICATED token returned in the response. [from Q-002]

---

## Q-003
**Question:** What rate-limiting applies?

## A-003 [Locked]
Rate-limit at 10 requests per minute per IP via the existing middleware shared with other authenticated endpoints. [from Q-003]

---

## Q-004
**Question:** What logging is required?

## A-004 [Locked]
Log every login attempt with timestamp + IP + outcome — never log passwords. Logs go to the standard application sink. [from Q-004]

---

## Q-005
**Question:** How are passwords stored?

## A-005 [Locked]
Passwords are hashed with bcrypt (cost factor 12) and stored as opaque strings; the plaintext password never crosses persistence boundaries. [from Q-005]

---

## Q-006
**Question:** What token TTL is used?

## A-006 [Locked]
JWT tokens expire after 24 hours. Refresh requires re-authentication. [from Q-006]

---

## A-AUTO-001 [DEPLOYMENT]
The login endpoint deploys via the existing kubernetes manifest at infra/k8s/login.yaml. [auto-extracted]

---

## A-AUTO-002 [SCALE]
Expected peak load is ~500 logins per second based on the existing dashboard. [auto-extracted]
