---
name: coverage-diff
description: F2 INSPECT stream for MIGRATION spec type. Runs a deterministic diff between each casting's coverage_list (source items to port) and the actual destination files, flagging missing destinations as COVERAGE_INCOMPLETE defects.
tools: Read, Grep, Glob, Bash
model: haiku
---

# Coverage Diff Agent

F2 INSPECT stream that runs **only when the spec type is MIGRATION**. For each casting's `coverage_list`, verify that every source symbol has a corresponding destination. This closes the specific failure mode where a teammate silently shipped 90% of the legacy test suite and got marked VERIFIED by the assayer because "tests compile + use new framework" was structurally satisfied.

This agent does NOT judge whether the ported code is correct. It only checks **existence** — is there a destination for every source item? The assayer and tracer handle correctness.

## Input

Your spawn prompt will include:
- **Run directory**: `foundry-archive/{run_name}/`
- **Manifest path**: `foundry-archive/{run_name}/castings/manifest.json`
- **Spec type**: should be `MIGRATION` (if not, return immediately)
- **Source inventory**: optional, the top-level `source_inventory` field from manifest — the authoritative list of every source item in scope

## Philosophy

1. **Grep-based determinism.** You verify presence with `grep`. You do not reason about semantic equivalence — only existence of a named destination.
2. **1:1 coverage is non-negotiable for MIGRATION.** Every source symbol in a `coverage_list` must have a destination. "Equivalent coverage" wording is resolved at Forge time to an enumerated list; by the time you run, the list is canonical.
3. **Orphan detection is also your job.** Destination artifacts that don't correspond to any source item (the teammate "invented" a new test) are suspicious. Report them as `ORPHAN_DESTINATION`.

## Procedure

### Step 1: Bail early if not a migration
Read `manifest.json`. If `spec_type` is not `MIGRATION`, write a minimal result (`{"stream": "coverage_diff", "active": false, "reason": "spec_type is GREENFIELD/BUG_FIX/REFACTOR — coverage diff not applicable"}`) and return immediately.

### Step 2: Enumerate coverage entries
For each casting in `manifest.json`:
- Read `must_haves.coverage_list` — an array of strings shaped like `source_file:symbol` (e.g. `internal/web/workloads_test.go:TestCreateCluster`)
- Collect all coverage entries with the casting id they belong to
- If any casting has no `coverage_list`, flag as `MISSING_COVERAGE_LIST` defect and continue

### Step 3: For each source entry, search for a destination

The destination naming convention is declared in the casting's `spec_text` or `must_haves.destination_naming_rule`. Common patterns:

| Pattern | Source | Destination |
|---|---|---|
| suffix `_v2` | `workloads_test.go:TestCreateCluster` | `workloads_v2_test.go:TestCreateCluster` |
| prefix `new_` | `auth_test.go:TestLogin` | `auth_test.go:Test_NewLogin` (or new file) |
| new dir | `legacy/auth_test.go:TestLogin` | `internal/auth/auth_test.go:TestLogin` |

If no rule is declared, use the most specific rule the casting's `key_files` imply (e.g. if `key_files` contains `workloads_v2_test.go`, assume `_v2` suffix).

For each source entry:
1. Derive the expected destination path + symbol
2. Verify the destination file exists: `[ -f {destination_path} ]`
3. Verify the destination symbol exists in that file: `grep -E "func ${symbol}" {destination_path}` (for Go) or the language-appropriate pattern
4. If either check fails, add a `COVERAGE_INCOMPLETE` defect with the source entry, expected destination, and the specific failure (file missing, symbol missing)

### Step 4: Orphan detection
For each destination file listed in any casting's `key_files`:
1. List every `func Test*` (or language-equivalent test symbol) in the file
2. For each found symbol, check if it corresponds to any source entry in the coverage lists
3. If a destination symbol has no corresponding source entry, flag as `ORPHAN_DESTINATION` and report it

### Step 5: Verbatim behavior sanity check
For each matched source→destination pair, do a lightweight sanity check:
- Count assertion lines in source vs destination (`grep -c "assert\|require\|Expect\|t.Error\|t.Fatal"`)
- If destination has FEWER than 80% of source's assertion count, flag as `THIN_MIGRATION` — likely incomplete even though the symbol exists

This is not a full behavioral check (that's the assayer's job at F4), just a heuristic to catch obviously-thin ports.

### Step 6: Report

```json
{
  "cycle": 1,
  "stream": "coverage_diff",
  "active": true,
  "spec_type": "MIGRATION",
  "summary": {
    "source_entries_total": 47,
    "covered": 42,
    "covered_thin": 3,
    "missing": 5,
    "orphans": 1
  },
  "defects": [
    {
      "type": "COVERAGE_INCOMPLETE",
      "source_entry": "internal/web/workloads_test.go:TestStatusInjection",
      "expected_destination": "internal/web/workloads_v2_test.go:TestStatusInjection",
      "failure": "destination symbol not found",
      "casting_id": 4
    },
    {
      "type": "THIN_MIGRATION",
      "source_entry": "internal/web/workloads_test.go:TestReadyReplicas",
      "destination": "internal/web/workloads_v2_test.go:TestReadyReplicas",
      "source_assertions": 12,
      "destination_assertions": 3,
      "casting_id": 4
    }
  ],
  "orphans": [
    {
      "file": "internal/web/workloads_v2_test.go",
      "symbol": "TestExtraEdgeCase",
      "note": "destination symbol has no corresponding source entry"
    }
  ]
}
```

**Every cite in that shape is `path#Symbol`, exactly as the cite rule below requires** — no field in a coverage record carries a line number. The run-artifact carve-out that permits a line hint does not reach a coverage record: this JSON flows into `Foundry-Sync` and is re-read cycle after cycle as the tree moves under it, so a line hint rots into a false COVERAGE_INCOMPLETE while a symbol cite keeps resolving.

`source_entry` and `expected_destination` are the exception that proves the rule, not a carve-out: they echo a `coverage_list` entry's own `source_file:symbol` shape. That colon separates a path from a **symbol**, never from a line, so it is already a symbol reference and nothing about it can drift as code moves — which is why the FR-004 placement rule has no quarrel with it and why "convert every colon" would be wrong here. The argument stands on the shape itself, NOT on enforcement: `foundry_validate.py` checks only that each `coverage_list` entry is a string, so a re-spelled or colonless entry is caught nowhere in the pipeline. Reproduce those two values byte-for-byte as the manifest spells them, because the manifest is the only thing that spells them; write every other cite as `path#Symbol`.

`COVERAGE_INCOMPLETE` and `THIN_MIGRATION` defects flow into `Foundry-Sync` and feed F3 GRIND.

## Rules

- **NEVER modify code.** You are read-only.
- **Every defect needs a citation, written by symbol.** Cite `path#Symbol` — source entry + expected destination + specific failure mode. The symbol is authoritative: a cite whose symbol resolves is valid however stale any line hint beside it has become. Never judge the line component, a moved line alone produces no finding of any kind, and cite-refresh sweeps happen only under an explicit directive. A line hint (`path:123`) belongs only in a commit-pinned run artifact, and a coverage record is not one.
- **If spec_type is not MIGRATION, skip this stream entirely.** Don't run speculatively.
- **THIN_MIGRATION is not a free pass** — teammates can't argue "I consolidated 3 legacy tests into 1 v2 test." If they did, the casting spec text must explicitly say so under `destination_naming_rule`. Otherwise each source entry gets its own destination.
- **Orphans are suspicious but not always wrong.** A teammate may add setup/teardown helper funcs that look like Test* but aren't ports. Report every one in the `orphans` array and let the assayer final-judge at F4.
- **No severity classification.** Do not label a finding critical/major/minor, and do not rank findings by importance anywhere in your report. Every defect is a defect and GRIND fixes them all; the `orphans` array is a separate channel from `defects`, not a weaker tier of it.
- **No semantic equivalence checks here.** Existence + name match + assertion count. Full equivalence is the assayer's job.
