---
id: PHASE3-TEST-STREAM
min_spec_format_version: v2.1
model: sonnet
effort: med
---

# Synthetic test stream agent (Phase 3 fixture)

This file is a Phase 3 test fixture only. It is NEVER read by a real Foundry run —
F0.5's roster is hardcoded paths in `plugins/foundry/commands/start.md`, never a
glob (per RESEARCH.md Pitfall 4). The file's only purpose is to give Plan 03-04
a synthetic agent whose `min_spec_format_version: v2.1` exceeds a legacy v2.0
spec, so the F0.5 enumeration logic emits a `manifest.stream_skips` record
under deterministic test conditions.

## Why this fixture exists

Plan 03-04 implements F0.5 step 2.5: when the spec declares
`spec_format_version: v2.0` but a stream-agent declares
`min_spec_format_version: v2.1`, F0.5 emits a `stream_skips` record:

```yaml
stream_skips:
  - stream_id: PHASE3-TEST-STREAM
    reason: spec_format_version
    spec_version: v2.0
    stream_min: v2.1
    agent_path: plugins/forge/tests/fixtures/agents/agent_phase3_test_stream.md
```

The conftest `run_f05_decompose_with_test_roster` fixture (Plan 03-04 implements)
will inject this fixture's path into the F0.5 roster enumeration so the
emission is verifiable in unit-test conditions without modifying the real
`plugins/foundry/commands/start.md` roster.
