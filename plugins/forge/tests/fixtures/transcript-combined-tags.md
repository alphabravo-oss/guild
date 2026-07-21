# Interview Transcript: combined-tags-feature

*Verbatim Q/A record. Started: 2026-05-01*

---

## Q-001
**Question:** Where should the auth check live and what compliance regime applies?
**Options presented:** open answer

## A-001 [ARCH_INVARIANT, IMPLICIT_FACT:SECURITY]
Operator stays generic; auth happens in the gateway, not the operator. SOC2 controls in effect. [from Q-001]
