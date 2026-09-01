---
spec_format_version: v2.1
---

# Spec: contract-surfaces

*Phase 7 fixture — v2.1 spec whose `## Contracts` table declares executable
surfaces living under `FORBIDDEN_SOURCE_ROOTS` paths (a plugin script, a Go
`cmd/` entrypoint, a `src/` CLI, a `lib/` node binary). Exercises the 7c
contract-surface exemption (AC-006/AC-007): observations may reference these
surfaces verbatim without tripping `WRONG_TEST_SOURCE_LEAK`, while paths
absent from the surface column still leak.*

---

## Global Invariants

| ID | statement | applies-to | citation |
|----|-----------|------------|----------|
| GI-001 | Every declared CLI surface exits non-zero on out-of-contract input. | all-surfaces | [from A-001] |

---

## State Transitions

| ID | from-state | to-state | trigger | citation |
|----|------------|----------|---------|----------|
| ST-001 | UNVALIDATED | VALIDATED | a declared surface invoked with in-contract input | [from A-001] |

---

## Contracts

| ID | surface | input | output | errors | citation |
|----|---------|-------|--------|--------|----------|
| CT-001 | `python plugins/foundry/scripts/validate-test-observations.py <channel-json>` | `{channel: path}` | `{exit: int}` | `exit 1 on failure`, `exit 2 on usage` | [from A-001] |
| CT-002 | `go run ./cmd/mytool --version` | `{}` | `{version: str}` | `exit 1 on unknown flag` | [from A-001] |
| CT-003 | `python src/cli.py --help` | `{}` | `{usage: str}` | `exit 2 on bad flag` | [from A-001] |
| CT-004 | `node lib/bin/mytool.js --json` | `{}` | `{ok: bool}` | `exit 1 on error` | [from A-001] |

---

<spec_requirements>
- FR-1: Each declared CLI surface MUST be executable exactly as documented in the contracts table. [from A-001]
- FR-2: Each declared CLI surface MUST fail loudly on out-of-contract input. [from A-001]
</spec_requirements>

---

*Spec format: v2.1 — engages TEST-01, EVID-01, EVID-02, PROBE-01, INTV-01, TYPE-01, TYPE-02 streams per F0.5 step 2b roster.*
