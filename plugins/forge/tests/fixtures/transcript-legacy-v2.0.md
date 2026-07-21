# Interview Transcript: legacy-feature

*Verbatim Q/A record. Started: 2025-12-01 (pre-v2.1 / pre-Phase-1)*

---

## Q-001
**Question:** What problem does this feature solve?
**Options presented:** open answer

## A-001
We need a way for users to reset their password without contacting support.

## Q-002
**Question:** Where should the reset link be sent?
**Options presented:** email | SMS | both

## A-002
email — we already have a verified email column in users table

## Q-003
**Question:** How long should the reset token live?
**Options presented:** 15min | 1hr | 24hr

## A-003
1 hour — long enough that users can finish coffee, short enough that stolen tokens go stale fast
