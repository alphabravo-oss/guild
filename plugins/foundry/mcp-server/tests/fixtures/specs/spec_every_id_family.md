---
spec_format_version: v2.1
---

# Every requirement-ID family forge emits, in one committed spec

This fixture exists so `test_every_id_prefix_in_a_real_spec_is_classified`
never skips (D-162): `forge-specs/` is git-ignored, so a guard that read only
real specs was inert in every clean checkout — including the worktree the
evidence gate re-executes in. Each family below is one forge actually emits
(`plugins/forge/scripts/validate-spec.py` and the typed tables), so a family
that vanishes from `schemas/vocab.py` is reported here by name.

## Global Invariants

| ID | statement | applies-to | violation | citation |
|----|-----------|------------|-----------|----------|
| GI-001 | one declaration per vocabulary | every reader | a second copy | [from A-001] |

## User Stories

### US-001: the operator sees every family
**Acceptance Criteria:**
- **AC-001** [derived from A-001]: an ID of any emitted family is recognised.
- **AC-002** [derived from A-001]: an ID of an unknown family is reported.

## Functional Requirements

- **FR-001** [from A-001]: recognise US, FR, NFR, AC, GI, ST, CT, OT and LR.
- **NFR-001** [from A-001]: never narrow what was counted before.

### Locked
- **LR-001**: the vocabulary is imported, never copied.

## State Transitions

| ID | from-state | to-state | trigger | guard | citation |
|----|------------|----------|---------|-------|----------|
| ST-001 | unknown | reported | an unclassified prefix | none | [from A-001] |

## Contracts

| ID | surface | input | output | errors | citation |
|----|---------|-------|--------|--------|----------|
| CT-001 | is_requirement_id | token | bool | none | [from A-001] |

## Observable Truths

- **OT-001** [derived from A-001]: `# evidence-for: OT-001` binds evidence.
