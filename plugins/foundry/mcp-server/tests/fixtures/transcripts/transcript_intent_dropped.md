# Transcript: intent-dropped fixture

*Phase 8 fixture — same shape as transcript_intent_clean.md. A-005 explicitly references the bcrypt password-hashing constraint that is the canonical "implicit constraint dropped at casting time" target. Used to construct a DROPPED-bearing matrix when paired with casting-1-prompt-dropped.md (which omits A-005).*

---

## Q-001
**Question:** What is the surface contract for the login endpoint?

## A-001 [Locked]
POST /api/login returns 200 with `{token: str, expires_at: str}` on valid credentials; returns 401 on invalid; 400 on malformed payloads. Stateless. [from Q-001]

---

## Q-002
**Question:** What state transition does login produce?

## A-002 [Locked]
The session moves from UNAUTHENTICATED to AUTHENTICATED only after POST /api/login succeeds with valid credentials. [from Q-002]

---

## Q-003
**Question:** What rate-limiting applies?

## A-003 [Locked]
Rate-limit at 10 requests per minute per IP via the existing middleware. [from Q-003]

---

## Q-004
**Question:** What logging is required?

## A-004 [Locked]
Log every login attempt with timestamp + IP + outcome — never log passwords. [from Q-004]

---

## Q-005
**Question:** How are passwords stored?

## A-005 [Locked]
Passwords use bcrypt password hashing (cost factor 12) and are stored as opaque strings; the plaintext password never crosses persistence boundaries. [from Q-005]

---

## Q-006
**Question:** What token TTL is used?

## A-006 [Locked]
JWT tokens expire after 24 hours. [from Q-006]
