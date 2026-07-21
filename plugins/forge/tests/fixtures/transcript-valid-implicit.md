# Interview Transcript: add-workloads-page

*Verbatim Q/A record. Started: 2026-05-01*

---

## A-AUTO-001 [IMPLICIT_FACT:RUNTIME] (auto-discovered runtime)
Go 1.21 + client-go v0.29 [from survey/architecture.md]

## A-AUTO-002 [IMPLICIT_FACT:DEPLOYMENT] (auto-discovered deployment)
Kubernetes — operator runs as a Deployment in cluster [from survey/infra.md]

## Q-001
**Question:** R1.5 research found htmx 2.x moved SSE to a separate package — confirm new code stays on 1.9 or migrate?
**Options presented:** stay on 1.9 | migrate to 2.x

## A-001 [IMPLICIT_FACT:FRAMEWORK_VERSION]
stay on 1.9 [from Q-001]

## Q-002
**Question:** What scale should the workloads page handle?
**Options presented:** ≤100 deployments | 100-1000 | 1000+ | unsure

## A-002 [IMPLICIT_FACT:SCALE]
≤100 deployments [from Q-002]

## Q-003
**Question:** Auth model for the new page — extend existing JWT/RBAC or new?
**Options presented:** extend JWT/RBAC | new

## A-003 [IMPLICIT_FACT:SECURITY]
extend JWT/RBAC [from Q-003]

## Q-004
**Question:** Where should the page handler live?
**Options presented:** internal/handlers | new package

## A-004 [ARCH_INVARIANT]
internal/handlers — operator stays generic, page rendering happens in agent [from Q-004]
