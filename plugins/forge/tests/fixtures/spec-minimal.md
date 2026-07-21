---
spec_format_version: v2.0
feature: fixture-minimal
created: 2026-05-01
---

# Spec: fixture-minimal

## Problem Statement

- Users need a minimal spec body for paired validator runs in the test suite [from A-001].

## Scope

In scope: exercising validate-spec.py against fixture transcripts.

Out of scope: real product work.

## Functional Requirements

### Locked

- **FR-001** [from A-001]: "stay on 1.9"

## Global Invariants

- The fixture-minimal spec exercises validate-spec.py without doing real product work.

## Appendix: Interview Transcript

The transcript content paired with this spec at test time is supplied separately
by the test harness (see ``run_validator_subprocess`` fixture in conftest.py).
This appendix exists as a structural placeholder so the validator's
``check_appendix_present`` does not fail.
