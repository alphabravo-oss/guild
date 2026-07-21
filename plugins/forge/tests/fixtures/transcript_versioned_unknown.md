<!-- spec_format_version: v9.0 -->
# Interview Transcript: versioned-unknown-fixture

*Verbatim Q/A record. Phase 3 fixture — NEGATIVE TEST for TYPE-02 allowlist enforcement.*

This fixture pairs with a synthesized spec where:
  - frontmatter declares `spec_format_version: v9.0` (NOT in KNOWN_SPEC_FORMAT_VERSIONS)
  - body content can be minimal — the unknown-version check fires before
    any other typed-section / implicit-fact rule

Expected validator behavior in Phase 3 (post Plan 03-03):
  - SPEC_FORMAT_VERSION_UNKNOWN token surfaces in stdout
  - returncode != 0
  - Other downstream tokens MAY also fire (depends on whether 03-03 short-
    circuits or continues); test 6 only asserts on SPEC_FORMAT_VERSION_UNKNOWN
    + non-zero exit so either short-circuit policy is acceptable.

Plan 03-02 lands `KNOWN_SPEC_FORMAT_VERSIONS = ("v2.0", "v2.1")`.
Plan 03-03 emits `SPEC_FORMAT_VERSION_UNKNOWN` as `report.fail()` when the
declared version is not in that allowlist.

---

## Q-001
**Question:** What CSS color should the badge use?
**Options presented:** red | orange | yellow

## A-001
red [from Q-001]

## Q-002
**Question:** Should the badge animate on hover?
**Options presented:** yes | no

## A-002
no [from Q-002]
