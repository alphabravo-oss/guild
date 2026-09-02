"""Phase 4 / EVID-01 — server-side evidence re-execution tests.

24 RED-or-SKIP stubs covering VALIDATION.md per-task verification map.

Plan 04-02 territory (parser + constants + module skeleton — 4 unit tests):
  - test_no_cmd_header_rejects
  - test_volatile_malformed_rejects
  - test_volatile_order_is_respected
  - test_failure_tokens_are_in_allowlist

Plan 04-03 territory (worktree + subprocess + redaction + comparator + stub
library — 15 tests, mix of integration via run_accept_casting_with_evidence
and unit tests against ``evidence._is_stub_pattern`` family):
  - test_clean_evidence_accepts_with_provenance
  - test_volatile_undeclared_rejects_with_diff
  - test_volatile_declared_redaction_passes
  - test_timeout_kills_and_rejects
  - test_exit_nonzero_rejects
  - test_stub_pattern_too_small
  - test_stub_pattern_vacuous_cmd (was test_stub_pattern_no_cmd_in_header
    until D-062 retired the first-token-in-output reading)
  - test_stub_pattern_bare_pass
  - test_stub_pattern_timestamp_cluster
  - test_stub_pattern_overrides_match
  - test_orphaned_commit_rejects
  - test_worktree_torn_down_on_success_and_failure
  - test_orphan_worktree_pruned_on_startup
  - test_non_utf8_output_handled
  - test_concurrent_verify_evidence_serializes

Plan 04-04 territory (foundry_accept_casting integration + v2.0 routing +
provenance + F0.9 7k extension — 5 integration tests):
  - test_failure_records_have_token_and_detail
  - test_provenance_record_has_required_fields
  - test_legacy_v20_routes_through_stream_skip
  - test_v21_engages_evidence_verification
  - test_f09_subcheck_7k_catches_missing_evid01

RED-or-SKIP discipline:

- ``importorskip`` at module top: ``foundry_mcp.tools.evidence`` doesn't exist
  in Plan 04-01; Plan 04-02 ships the skeleton; the entire module SKIPs in
  Plan 04-01 and proceeds to per-test RED-then-GREEN as Plans 04-02/03/04
  add the constants/parser/comparator/integration logic.
- Tests using ``run_accept_casting_with_evidence(...)`` integration fixture
  SKIP via the conftest fixture-body stub until Plan 04-04 swaps in the
  real harness (signature already locked in Plan 04-01).
- Unit tests calling ``evidence._<helper>(...)`` directly fail with
  ``AttributeError`` once the module exists but the helper hasn't shipped
  yet (Plan 04-02 / 04-03 territory).
"""

from __future__ import annotations

import ast
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

# Plan 04-02 ships the module skeleton. Until then, the entire module SKIPs
# cleanly. importorskip is the canonical RED-or-SKIP discipline for
# downstream-wave-owned implementation modules — mirrors Phase 2 Plan 02-01.
evidence = pytest.importorskip("foundry_mcp.tools.evidence")
vocab = pytest.importorskip("foundry_mcp.schemas.vocab")


# ---------------------------------------------------------------------------
# Plan 04-02 territory — parser + constants
# ---------------------------------------------------------------------------


def test_no_cmd_header_rejects(load_fixture):
    """Fixture missing ``# evidence-cmd:`` header → EVIDENCE_COMMAND_MISSING.

    Plan 04-02 lands ``_parse_evidence_header``. RED until then with
    AttributeError on the helper. Also covers SC#3 (file-existence rejected)
    per VALIDATION.md row 04-04-* — same test, two requirements.
    """
    text = load_fixture("evidence/evidence_log_no_cmd.log")
    # Plan 04-02 author's discretion: parser raises ValueError with token
    # prefix, OR returns dict with ``cmd=None`` and caller translates. This
    # test asserts the END-TO-END contract — either form surfaces the token.
    with pytest.raises(ValueError, match="EVIDENCE_COMMAND_MISSING"):
        result = evidence._parse_evidence_header(text)
        if result.get("cmd") is None:
            raise ValueError("EVIDENCE_COMMAND_MISSING: no cmd header")


def test_volatile_malformed_rejects(load_fixture):
    """Invalid regex ``[unclosed`` → EVIDENCE_VOLATILE_MALFORMED.

    Plan 04-02 lands header parser; Plan 04-03 lands volatile-application.
    The malformed-regex check fires at application time (re.compile raises
    re.error inside ``_apply_volatile_redaction``).
    """
    text = load_fixture("evidence/evidence_log_volatile_malformed.log")
    parsed = evidence._parse_evidence_header(text)
    # Parser accepts the malformed string verbatim; application-time check
    # raises with the token. (Plan 04-03 author's discretion: parser-time vs
    # app-time; this test asserts the END-TO-END token surfaces.)
    with pytest.raises(ValueError, match="EVIDENCE_VOLATILE_MALFORMED"):
        evidence._apply_volatile_redaction("some text", parsed["volatile"])


def test_volatile_order_is_respected():
    """Two volatile patterns where reordering changes outcome → both orders honored.

    Pitfall 5 from RESEARCH.md: ``re.sub`` iterative-application is
    non-commutative. Pattern A: ``\\d+ms`` → ``<VOLATILE>``. Pattern B:
    ``completed in <VOLATILE>`` → ``<TIMING>`` (matches the rewritten
    output). Reverse order: pattern B fires first against ``completed in
    42ms``, no match (because ``<VOLATILE>`` hasn't been substituted yet);
    pattern A then redacts ``42ms`` → ``<VOLATILE>``.

    Plan 04-03 lands ``_apply_volatile_redaction``.
    """
    text = "completed in 42ms"
    out_ab = evidence._apply_volatile_redaction(
        text, [r"\d+ms", r"completed in <VOLATILE>"]
    )
    assert out_ab == "<TIMING>"
    out_ba = evidence._apply_volatile_redaction(
        text, [r"completed in <VOLATILE>", r"\d+ms"]
    )
    assert out_ba == "completed in <VOLATILE>"


def test_failure_tokens_are_in_allowlist():
    """Phase 4's 8 tokens remain in ``KNOWN_EVIDENCE_FAILURE_TOKENS``.

    Closed-vocabulary discipline: any new token = code-edit forced. Mirrors
    Phase 1 ``VALID_IMPLICIT_FACT_CATEGORIES`` + Phase 3
    ``KNOWN_SPEC_FORMAT_VERSIONS``.

    Phase 5 / EVID-02 (Plan 05-02) extends the tuple from 8 to 10 entries by
    appending ``EVIDENCE_REQUIREMENT_UNBOUND`` + ``EVIDENCE_FOR_MALFORMED``
    at the END (preserves Phase 4 token positions). This test asserts the
    Phase 4 allowlist is a SUBSET of the live tuple — Phase-5+ extensions
    are validated by the parallel ``test_failure_tokens_includes_unbound_and_malformed``
    test in ``tests/test_evidence_for.py``.
    """
    phase_4_tokens = frozenset(
        {
            "EVIDENCE_COMMAND_MISSING",
            "EVIDENCE_TIMEOUT",
            "EVIDENCE_EXIT_NONZERO",
            "EVIDENCE_OUTPUT_MISMATCH",
            "EVIDENCE_STUB_DETECTED",
            "EVIDENCE_VOLATILE_MALFORMED",
            "EVIDENCE_COMMIT_MISSING",
            "EVIDENCE_NETWORK_VIOLATION",
        }
    )
    actual = frozenset(evidence.KNOWN_EVIDENCE_FAILURE_TOKENS)
    missing_phase_4 = phase_4_tokens - actual
    assert not missing_phase_4, (
        f"Phase 4 token allowlist regression — these Phase 4 tokens disappeared "
        f"from KNOWN_EVIDENCE_FAILURE_TOKENS: {sorted(missing_phase_4)}"
    )


# ---------------------------------------------------------------------------
# Plan 04-03 territory — integration tests via run_accept_casting_with_evidence
# ---------------------------------------------------------------------------


def test_clean_evidence_accepts_with_provenance(run_accept_casting_with_evidence):
    """SC#1: well-formed evidence → re-execution succeeds + byte-match passes
    + provenance record written with all 13 fields.

    Plan 04-03 wires the harness body via Plan 04-04. Until Plan 04-04 ships,
    fixture raises pytest.skip — this test is SKIP (not RED) in Plan 04-01.
    """
    result = run_accept_casting_with_evidence(
        "evidence/evidence_log_clean.log",
        spec_format_version="v2.1",
    )
    assert result["verdict"] == "accepted"
    assert result["failure_token"] is None
    prov = result["provenance"]
    assert prov is not None
    required = {
        "evidence_path",
        "evidence_cmd",
        "casting_commit",
        "log_sha256",
        "captured_sha256",
        "redacted_log_sha256",
        "redacted_captured_sha256",
        "server_mtime",
        "exit_code",
        "elapsed_seconds",
        "env_keys_present",
        "verdict",
        "failure_token",
    }
    assert required.issubset(prov.keys())


def test_volatile_undeclared_rejects_with_diff(run_accept_casting_with_evidence):
    """SC#2: output diverges from re-execution due to undeclared timing
    variance → EVIDENCE_OUTPUT_MISMATCH + diff hint in failure detail.
    """
    result = run_accept_casting_with_evidence(
        "evidence/evidence_log_volatile_undeclared.log",
        spec_format_version="v2.1",
    )
    assert result["verdict"] == "rejected"
    assert result["failure_token"] == "EVIDENCE_OUTPUT_MISMATCH"


def test_volatile_declared_redaction_passes(run_accept_casting_with_evidence):
    """SC#2 inverse: same fixture but with ``# evidence-volatile:`` declared
    → redaction reconciles output, byte-match passes after redaction.

    Uses evidence_log_clean.log which DOES declare the volatile pattern.
    """
    result = run_accept_casting_with_evidence(
        "evidence/evidence_log_clean.log",
        spec_format_version="v2.1",
    )
    assert result["verdict"] == "accepted"
    assert result["failure_token"] is None


def test_timeout_kills_and_rejects(run_accept_casting_with_evidence):
    """SC#4 timeout: cmd ``sleep 999`` with ``# evidence-timeout: 5`` →
    killed at 5s, EVIDENCE_TIMEOUT token, partial output captured.
    """
    result = run_accept_casting_with_evidence(
        "evidence/evidence_log_timeout.log",
        spec_format_version="v2.1",
    )
    assert result["verdict"] == "rejected"
    assert result["failure_token"] == "EVIDENCE_TIMEOUT"
    # Elapsed should be near 5s (declared timeout), well under 999s
    assert result["provenance"]["elapsed_seconds"] < 30


def test_exit_nonzero_rejects(run_accept_casting_with_evidence):
    """SC#4: re-execution exits non-zero → EVIDENCE_EXIT_NONZERO token,
    exit code captured in provenance.

    Plan 04-03 author's discretion which fixture / synthesizer pattern to
    use — Plan 04-04's harness can synthesize a ``cmd: false`` evidence
    file in-test. This test asserts the END-TO-END token surfaces.
    """
    result = run_accept_casting_with_evidence(
        "evidence/evidence_log_clean.log",
        spec_format_version="v2.1",
        force_exit_code=42,  # harness kwarg: forces re-execution exit code
    )
    assert result["verdict"] == "rejected"
    assert result["failure_token"] == "EVIDENCE_EXIT_NONZERO"
    assert result["provenance"]["exit_code"] == 42


# ---------------------------------------------------------------------------
# Plan 04-03 territory — stub-pattern unit tests (direct calls)
# ---------------------------------------------------------------------------


def test_stub_pattern_too_small():
    """Output < 128 bytes (stub threshold) → EVIDENCE_STUB_DETECTED.

    Plan 04-03 lands ``_is_stub_pattern_too_small`` (or the equivalent
    helper inside the stub-library family).
    """
    short_output = "PASS\n"  # 5 bytes — well under 128
    assert evidence._is_stub_pattern_too_small(short_output, threshold=128) is True
    long_output = "x" * 200
    assert evidence._is_stub_pattern_too_small(long_output, threshold=128) is False


def test_stub_pattern_vacuous_cmd():
    """A command that runs only no-ops and output emitters →
    EVIDENCE_STUB_VACUOUS_CMD. Judged over the WHOLE command at every command
    position, recursing into ``sh -c`` payloads — never over the log's text.

    This replaces the first-token-in-output rule (D-062), which rejected 19 of
    the 25 evidence logs this effort committed because `cd … && uv run pytest`
    starts with `cd` and `pytest -q` output quotes nothing from its own command
    line. The rule that survives byte-match is about the COMMAND: a body a
    command can emit without touching the tree byte-matches itself forever,
    which is the one fabrication re-execution cannot see.
    """
    # Vacuous: emits a canned body, reads nothing, runs nothing.
    assert evidence._is_stub_pattern_vacuous_cmd("echo PASS") is True
    assert evidence._is_stub_pattern_vacuous_cmd("true") is True
    assert (
        evidence._is_stub_pattern_vacuous_cmd(
            "echo 'collected 42 items'; printf '42 passed in 1.2s\\n'"
        )
        is True
    )
    # …including when a shell wrapper hides it — the payload is what counts.
    assert evidence._is_stub_pattern_vacuous_cmd("sh -c 'echo a; echo b'") is True

    # Real work anywhere in the command clears the rule, whatever the first
    # token is. These four shapes are the corpus's actual command grammar.
    for cmd in (
        "pytest -k some_test",
        "cd plugins/foundry/mcp-server && uv run --with pytest pytest tests/x.py -q",
        "sh -c 'echo === section ===; python3 -c \"print(1)\"'",
        "echo '== plugin.json =='; grep -n version plugin.json",
    ):
        assert evidence._is_stub_pattern_vacuous_cmd(cmd) is False, cmd

    # `cat` reads a file committed at the casting commit — real evidence about
    # the tree, and the replay harness depends on it staying legitimate.
    assert evidence._is_stub_pattern_vacuous_cmd("cat replay.txt") is False

    # Fails OPEN where it cannot judge: never reject a log on this rule's word
    # because its command was empty or unlexable.
    assert evidence._is_stub_pattern_vacuous_cmd("") is False
    assert evidence._is_stub_pattern_vacuous_cmd("echo 'unbalanced") is False


def test_stub_pattern_bare_pass():
    """Single-line ``PASS`` with no other content → EVIDENCE_STUB_BARE_PASS.

    Closely-related to ``too_small``; the stub library encodes BOTH (defense
    in depth — bare-PASS lookup is independent of byte threshold).
    """
    assert evidence._is_stub_pattern_bare_pass("PASS\n") is True
    assert evidence._is_stub_pattern_bare_pass("PASS") is True
    assert evidence._is_stub_pattern_bare_pass("OK\n") is False
    assert (
        evidence._is_stub_pattern_bare_pass(
            "PASS\nsomething else here\n"
        )
        is False
    )


def test_stub_pattern_timestamp_cluster():
    """Output is just a cluster of identical-shape timestamp lines (no real
    test output) → EVIDENCE_STUB_TIMESTAMP_CLUSTER.

    Defense against fabricated logs that look bulky but contain no
    semantic content.
    """
    # All lines match the same timestamp shape; fabricated bulk.
    cluster = (
        "2026-05-05T10:00:00Z\n"
        "2026-05-05T10:00:01Z\n"
        "2026-05-05T10:00:02Z\n"
        "2026-05-05T10:00:03Z\n"
        "2026-05-05T10:00:04Z\n"
    )
    assert evidence._is_stub_pattern_timestamp_cluster(cluster) is True
    # Real test output with mixed content survives.
    real = (
        "tests/test_login.py::test_x PASSED [33%]\n"
        "tests/test_login.py::test_y PASSED [66%]\n"
        "============================== 2 passed in 0.04s ===========================\n"
    )
    assert evidence._is_stub_pattern_timestamp_cluster(real) is False


def test_stub_pattern_overrides_match():
    """Even if byte-match against captured re-execution succeeds, a positive
    stub-pattern hit on the SOURCE log rejects.

    Closes Pitfall 4 from RESEARCH.md: a self-consistent fabricated log
    (e.g., bare ``PASS`` re-played by a fabricated ``true``-equivalent cmd)
    must not byte-match its way to acceptance. The stub-library check fires
    on the LOG content unconditionally.
    """
    # Plan 04-03 author's discretion which exact API surfaces this — the
    # likely shape is ``_check_stub_patterns(log_text, cmd) -> token | None``.
    log_text = "PASS\n"
    cmd = "pytest"
    token = evidence._check_stub_patterns(log_text, cmd)
    # Bare-PASS or too-small fires regardless; the token is one of the
    # stub-family tokens, NOT None.
    assert token in {
        "EVIDENCE_STUB_DETECTED",
        "EVIDENCE_STUB_BARE_PASS",
        "EVIDENCE_STUB_TOO_SMALL",
        "EVIDENCE_STUB_VACUOUS_CMD",
    }


# ---------------------------------------------------------------------------
# Plan 04-03 territory — worktree + concurrency integration
# ---------------------------------------------------------------------------


def test_orphaned_commit_rejects(run_accept_casting_with_evidence):
    """Casting commit hash referenced in evidence is not present in the
    synthesized repo → EVIDENCE_COMMIT_MISSING token.

    Harness-level orphaning: Plan 04-04 fixture body passes a deliberately
    bogus ``casting_commit`` kwarg.
    """
    result = run_accept_casting_with_evidence(
        "evidence/evidence_log_orphaned_commit.log",
        casting_commit="deadbeef" * 5,  # 40-char hex; not in synth repo
        spec_format_version="v2.1",
    )
    assert result["verdict"] == "rejected"
    assert result["failure_token"] == "EVIDENCE_COMMIT_MISSING"


def test_worktree_torn_down_on_success_and_failure(
    run_accept_casting_with_evidence,
):
    """``git worktree`` torn down regardless of verdict — no leaks.

    Asserts manifest carries a ``worktree_torn_down: True`` flag (or
    equivalent) on BOTH the accepted and rejected paths.
    """
    accepted = run_accept_casting_with_evidence(
        "evidence/evidence_log_clean.log",
        spec_format_version="v2.1",
    )
    assert accepted["manifest"].get("worktree_torn_down") is True

    rejected = run_accept_casting_with_evidence(
        "evidence/evidence_log_volatile_undeclared.log",
        spec_format_version="v2.1",
    )
    assert rejected["manifest"].get("worktree_torn_down") is True


def test_orphan_worktree_pruned_on_startup(run_accept_casting_with_evidence):
    """Orphaned worktree from a prior crashed run is pruned at startup.

    The harness pre-seeds a real orphan (worktree dir deleted, ``.git/worktrees``
    metadata left dangling) before invoking verification.

    ``orphan_worktrees_pruned`` is a real measurement — the harness counts
    ``.git/worktrees`` metadata dirs before and after and reports the drop — so
    a non-zero value means production's ``_prune_orphaned_worktrees`` actually
    removed the dangling entry.

    What it does NOT establish on its own is that pruning left the run it was
    pruning for intact, so the production outputs are asserted alongside it: a
    real provenance record for the real commit, and the run's own worktree torn
    down afterwards. A prune that also ate the live worktree would satisfy the
    count and fail these.
    """
    result = run_accept_casting_with_evidence(
        "evidence/evidence_log_clean.log",
        spec_format_version="v2.1",
        seed_orphan_worktree=True,  # harness kwarg: pre-seeds an orphan
    )
    assert result["verdict"] == "accepted"
    assert result["manifest"]["orphan_worktrees_pruned"] >= 1

    prov = result["provenance"]
    assert prov is not None
    assert prov["verdict"] == "accepted"
    assert prov["failure_token"] is None
    # Re-execution really happened against the casting commit: the committed
    # log and the captured output hashed to the same bytes.
    assert prov["exit_code"] == 0
    assert prov["redacted_log_sha256"] == prov["redacted_captured_sha256"]
    assert result["manifest"]["worktree_torn_down"] is True


def test_non_utf8_output_handled(run_accept_casting_with_evidence):
    """Re-executed cmd emits non-UTF-8 bytes → captured + redacted with
    ``errors='replace'``; no crash; comparison proceeds against replaced form.
    """
    result = run_accept_casting_with_evidence(
        "evidence/evidence_log_clean.log",
        spec_format_version="v2.1",
        inject_non_utf8=True,  # harness kwarg: forces non-UTF-8 in re-exec
    )
    # Either verdict is acceptable — what matters is no crash AND the
    # captured output survives encoding via the replace error handler.
    assert result["verdict"] in {"accepted", "rejected"}
    assert result["provenance"] is not None


def test_concurrent_verify_evidence_serializes(run_accept_casting_with_evidence):
    """Two concurrent verify-evidence calls on the same project_root serialize
    via the worktree lock — neither corrupts the other's worktree, and BOTH
    complete their manifest write.

    The old assertion read ``manifest['concurrent_serialized']``, which the
    harness sets to ``concurrent_invocations > 1`` — it restated the test's own
    input and would have passed against a build with no locking at all.

    The evidence of real serialization is in the persisted manifest: each
    thread verifies a different casting id and appends its own provenance
    record. Two populated ``evidence_provenance`` arrays mean both threads got
    a worktree, re-executed, and completed a read-modify-write of the same
    manifest file without either clobbering the other.
    """
    result = run_accept_casting_with_evidence(
        "evidence/evidence_log_clean.log",
        spec_format_version="v2.1",
        concurrent_invocations=2,  # harness kwarg: spawn N concurrent calls
    )
    assert result["verdict"] == "accepted"

    castings = {str(c.get("id")): c for c in result["manifest"]["castings"]}
    assert set(castings) == {"1", "2"}, (
        f"both concurrent castings must reach the manifest; got {sorted(castings)}"
    )
    for cid, entry in castings.items():
        records = entry.get("evidence_provenance", [])
        assert len(records) == 1, (
            f"casting {cid} wrote {len(records)} provenance records — a lost "
            f"write means the concurrent manifest updates raced"
        )
        assert records[0]["verdict"] == "accepted"


# ---------------------------------------------------------------------------
# Plan 04-04 territory — foundry_accept_casting integration + v2.0 routing
# ---------------------------------------------------------------------------


def test_failure_records_have_token_and_detail(run_accept_casting_with_evidence):
    """SC#4: every failure verdict carries a closed-vocabulary token AND a
    human-readable detail string in the failure record.
    """
    result = run_accept_casting_with_evidence(
        "evidence/evidence_log_volatile_undeclared.log",
        spec_format_version="v2.1",
    )
    assert result["verdict"] == "rejected"
    assert result["failure_token"] in evidence.KNOWN_EVIDENCE_FAILURE_TOKENS
    failures = result["manifest"].get("failures", [])
    assert len(failures) >= 1
    f = failures[0]
    assert "token" in f
    assert "detail" in f
    assert isinstance(f["detail"], str) and len(f["detail"]) > 0


def test_provenance_record_has_required_fields(run_accept_casting_with_evidence):
    """SC#1: provenance schema is exactly the 13 fields locked in CONTEXT.md.

    Closed-schema discipline mirrors the closed-vocabulary token allowlist.
    Any new field = code-edit forced.
    """
    result = run_accept_casting_with_evidence(
        "evidence/evidence_log_clean.log",
        spec_format_version="v2.1",
    )
    assert result["verdict"] == "accepted"
    prov = result["provenance"]
    expected_fields = frozenset(
        {
            "evidence_path",
            "evidence_cmd",
            "casting_commit",
            "log_sha256",
            "captured_sha256",
            "redacted_log_sha256",
            "redacted_captured_sha256",
            "server_mtime",
            "exit_code",
            "elapsed_seconds",
            "env_keys_present",
            "verdict",
            "failure_token",
        }
    )
    actual_fields = frozenset(prov.keys())
    assert expected_fields.issubset(actual_fields), (
        f"provenance missing fields: {expected_fields - actual_fields}"
    )


def test_legacy_v20_routes_through_stream_skip(run_accept_casting_with_evidence):
    """v2.0 spec → manifest.stream_skips records EVID-01 skip; no re-execution.

    Mirrors Phase 3 SC#4: absence of stream-skipped record on legacy spec
    is itself a defect. Phase 4 emits the skip via the same machinery.
    """
    result = run_accept_casting_with_evidence(
        "evidence/evidence_log_clean.log",  # log content irrelevant on v2.0 path
        spec_format_version="v2.0",
    )
    assert result["verdict"] == "skipped"
    skips = result["manifest"].get("stream_skips", [])
    evid01_skips = [s for s in skips if s.get("stream_id") == "EVID-01"]
    assert len(evid01_skips) == 1
    assert evid01_skips[0]["spec_version"] == "v2.0"
    assert evid01_skips[0]["stream_min"] == "v2.1"
    assert evid01_skips[0]["agent_path"] is None


def test_v21_engages_evidence_verification(run_accept_casting_with_evidence):
    """v2.1 spec → re-execution + byte-match runs; manifest.stream_skips
    does NOT contain an EVID-01 entry.
    """
    result = run_accept_casting_with_evidence(
        "evidence/evidence_log_clean.log",
        spec_format_version="v2.1",
    )
    assert result["verdict"] == "accepted"
    skips = result["manifest"].get("stream_skips", [])
    evid01_skips = [s for s in skips if s.get("stream_id") == "EVID-01"]
    assert len(evid01_skips) == 0


def test_missing_evidence_on_a_v21_spec_is_rejected(run_accept_casting_with_evidence):
    """A v2.1 casting that commits no evidence file at all is REJECTED, with the
    closed-vocabulary token that names why.

    The old assertion searched ``manifest['f09_diagnostics']`` for "7k" or
    "EVID-01" — a string the harness itself composes whenever
    ``omit_required_evidence`` is set. It asserted the harness's own input back
    at itself and could not fail for any behaviour of the code under test.

    The real signal is the one F0.9's 7k re-derivation actually consumes:
    verification engaged (so this is not a v2.0 skip), produced no provenance
    record, and rejected with EVIDENCE_COMMAND_MISSING naming the expected
    filename glob.
    """
    result = run_accept_casting_with_evidence(
        "evidence/evidence_log_clean.log",
        spec_format_version="v2.1",
        omit_required_evidence=True,  # harness kwarg: drops evidence file
    )
    assert result["verdict"] == "rejected"
    assert result["failure_token"] == "EVIDENCE_COMMAND_MISSING"
    assert "evidence/casting-1-*.log" in result["failure_detail"]
    # Engaged, not skipped: no EVID-01 stream-skip record was written, so the
    # absence of a provenance record is a defect rather than a legacy bypass.
    assert result["all_provenance"] == []
    skips = [
        s for s in result["manifest"]["stream_skips"]
        if s.get("stream_id") == "EVID-01"
    ]
    assert skips == []


# ===========================================================================
# Casting 3 / FR-017 / AC-023 / OT-011 — the evidence gate resolves the RUN's
# actual spec path.
#
# `foundry_accept_casting` passed a hardcoded `<project_root>/specs/spec.md`
# into `verify_evidence`. Most runs have no such file, and
# `_read_spec_format_version` defaults to v2.0 on a miss — so every v2.1 run
# was silently downgraded to the stream-skip branch and no evidence was ever
# re-executed. The run's real spec was already resolved 110 lines earlier in
# the same function (`foundry_spec_hash`) and thrown away.
#
# The decisive test builds a repo where the two paths DISAGREE: the stale
# `specs/spec.md` is v2.0 while the run's actual spec is v2.1. Reading the
# wrong one skips; reading the right one engages.
# ===========================================================================

# The leading `[replay] cat replay.txt` line is a historical anchor: it existed
# to carry the cmd-first-token into the first 3 body lines back when the stub
# library judged the LOG against its command. D-062 retired that reading (the
# rule now judges the COMMAND for vacuity, and `cat` is not vacuous), so the
# anchor is inert here — kept because conftest's `use_cat_replay` harness still
# writes the same line into both the committed log and the replay file, and the
# comparison is byte-exact on both sides.
_EVIDENCE_BODY = (
    "[replay] cat replay.txt\n"
    "collected 3 items\n"
    "\n"
    "tests/test_gate.py::test_accepts_symbol_cite PASSED\n"
    "tests/test_gate.py::test_rejects_unresolvable_symbol PASSED\n"
    "tests/test_gate.py::test_ignores_stale_line_hint PASSED\n"
    "\n"
    "3 passed\n"
)


def _run_git(args: list[str], cwd: Path) -> None:
    subprocess.run(
        [
            "git",
            "-c", "user.name=Foundry Test",
            "-c", "user.email=test@example.invalid",
            "-c", "commit.gpgsign=false",
            *args,
        ],
        cwd=cwd,
        check=True,
        capture_output=True,
    )


def _build_divergent_spec_repo(
    tmp_path: Path,
    *,
    replay_body_only: bool = False,
    req_ids: tuple = ("AC-023",),
) -> dict:
    """A repo whose stale ``specs/spec.md`` is v2.0 and whose RUN spec is v2.1.

    Returns the arguments ``foundry_accept_casting`` needs, plus the two spec
    paths so a test can assert which one was read.
    """
    from foundry_mcp.tools.foundry import foundry_init
    from foundry_mcp.tools.foundry_handoff import _hash_str, foundry_spec_hash
    from foundry_mcp.tools.foundry_state import set_active_run

    project_root = tmp_path / "repo"
    project_root.mkdir()
    _run_git(["init", "-q", "-b", "main"], project_root)

    # The STALE path the gate used to hardcode. Deliberately v2.0: if the gate
    # reads this one, evidence verification skips and the test fails.
    stale_spec = project_root / "specs" / "spec.md"
    stale_spec.parent.mkdir(parents=True, exist_ok=True)
    stale_spec.write_text(
        "---\nspec_format_version: v2.0\n---\n# Stale spec\n", encoding="utf-8"
    )

    (project_root / "castings").mkdir(parents=True, exist_ok=True)
    (project_root / "castings" / "manifest.json").write_text(
        '{"castings": [{"id": "1", "evidence_provenance": []}]}\n', encoding="utf-8"
    )

    # A real source file so the completion report's path#Symbol cite resolves.
    (project_root / "src").mkdir()
    (project_root / "src" / "gate.py").write_text(
        "def accept_casting():\n    return True\n", encoding="utf-8"
    )

    # Committed evidence: a deterministic cat-replay so re-execution
    # byte-matches the committed log. The comparator matches the FULL
    # committed file (header lines included), so replay.txt must hold the
    # whole log rather than just its body — same discipline as conftest's
    # ``use_cat_replay`` harness.
    evidence_log = (
        "# evidence-cmd: cat replay.txt\n"
        f"# evidence-for: {', '.join(req_ids)}\n"
        "\n" + _EVIDENCE_BODY
    )
    # replay_body_only mirrors what a REAL evidence command does: it emits the
    # body alone, never the `# evidence-*:` header lines that only exist in the
    # committed file. The default (full-file replay) mirrors conftest's
    # use_cat_replay harness. Both must verify.
    (project_root / "replay.txt").write_text(
        _EVIDENCE_BODY if replay_body_only else evidence_log, encoding="utf-8"
    )
    evidence_dir = project_root / "evidence"
    evidence_dir.mkdir()
    (evidence_dir / "casting-1-gate.log").write_text(evidence_log, encoding="utf-8")

    _run_git(["add", "-A"], project_root)
    _run_git(["commit", "-q", "-m", "casting 1"], project_root)
    casting_commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=project_root, check=True, capture_output=True, text=True,
    ).stdout.strip()

    # The RUN's actual spec — v2.1 — written where foundry_spec_hash looks.
    init = foundry_init(project_root=str(project_root))
    fdir = Path(init["foundry_dir"])
    set_active_run(init["run_name"])
    run_spec = fdir / "spec.md"
    run_spec.write_text(
        "---\nspec_format_version: v2.1\n---\n"
        "# Run spec\n\n"
        + "".join(f"{r} the gate reads the run's spec.\n" for r in req_ids),
        encoding="utf-8",
    )

    prompt_text = (
        "<spec_requirements>\n"
        + "".join(
            f"- **{r}**: evidence runs against the run's actual spec\n"
            for r in req_ids
        )
        + "</spec_requirements>\n"
    )
    (fdir / "castings").mkdir(parents=True, exist_ok=True)
    (fdir / "castings" / "casting-1-prompt.md").write_text(
        prompt_text, encoding="utf-8"
    )

    return {
        "project_root": project_root,
        "casting_commit": casting_commit,
        "spec_hash": foundry_spec_hash(project_root=str(project_root))["spec_hash"],
        "prompt_hash": _hash_str(prompt_text),
        "run_spec": run_spec,
        "stale_spec": stale_spec,
        "fdir": fdir,
        # The two manifests that must not be confused: the RUN's, which
        # foundry_init created and every reader loads, and the project-root
        # decoy the evidence writers used to build.
        "run_manifest": fdir / "castings" / "manifest.json",
        "stale_manifest": project_root / "castings" / "manifest.json",
    }


def test_accept_casting_resolves_the_runs_actual_spec_path(tmp_path):
    """AC-023 / OT-011 — Foundry-Accept-Casting with a casting commit
    re-executes evidence in a worktree against the RUN's spec.

    The repo's `specs/spec.md` is v2.0 and the run's spec is v2.1. Engagement
    (verdict "accepted", not "skipped") is only possible if the gate read the
    run's spec — so this test fails on the hardcoded path it replaces."""
    from foundry_mcp.tools.foundry_handoff import foundry_accept_casting
    from foundry_mcp.tools.foundry_state import clear_active_run

    env = _build_divergent_spec_repo(tmp_path)
    try:
        result = foundry_accept_casting(
            casting_id=1,
            spec_hash=env["spec_hash"],
            prompt_hash=env["prompt_hash"],
            completion_report="AC-023 implemented at src/gate.py#accept_casting\n",
            project_root=str(env["project_root"]),
            casting_commit=env["casting_commit"],
        )
    finally:
        clear_active_run()

    # The spec actually read is the run's, NOT <project_root>/specs/spec.md.
    assert result["evidence_spec_path"] == str(env["run_spec"])
    assert result["evidence_spec_path"] != str(env["stale_spec"])

    # Engaged rather than skipped — the observable consequence of reading the
    # right spec, and the whole point of FR-017.
    assert result["evidence_verdict"] == "accepted", result
    assert len(result["evidence_provenance"]) == 1
    assert result["evidence_provenance"][0]["verdict"] == "accepted"
    assert result["ok"] is True, result["warning"]


def test_evidence_provenance_is_written_to_the_RUN_manifest(tmp_path):
    """FR-017 / AC-023 — the provenance record lands in the manifest the run
    actually keeps, at ``foundry-archive/{run}/castings/manifest.json``.

    Both manifest writers built ``<project_root>/castings/manifest.json``, a
    path no real run has. The append hit the "manifest is missing" guard and
    silently no-op'd, so the live run finished with ``evidence_provenance: []``
    on every casting while verification was in fact running and accepting —
    the exact hole wiring the casting-commit path was meant to close.

    The repo below has BOTH files, so the assertion is directional rather than
    existential: provenance must appear in the run's manifest and must NOT
    appear in the project-root decoy. Against the old path the two assertions
    swap, so this cannot pass by accident either way."""
    from foundry_mcp.tools.foundry_handoff import foundry_accept_casting
    from foundry_mcp.tools.foundry_state import clear_active_run

    env = _build_divergent_spec_repo(tmp_path)
    # Precondition: the run's manifest exists (foundry_init wrote it) and holds
    # no provenance yet.
    run_manifest = json.loads(env["run_manifest"].read_text(encoding="utf-8"))
    assert run_manifest["castings"] == []

    try:
        result = foundry_accept_casting(
            casting_id=1,
            spec_hash=env["spec_hash"],
            prompt_hash=env["prompt_hash"],
            completion_report="AC-023 implemented at src/gate.py#accept_casting\n",
            project_root=str(env["project_root"]),
            casting_commit=env["casting_commit"],
        )
    finally:
        clear_active_run()

    assert result["evidence_verdict"] == "accepted", result

    written = json.loads(env["run_manifest"].read_text(encoding="utf-8"))
    castings = {str(c.get("id")): c for c in written["castings"]}
    assert "1" in castings, (
        f"provenance never reached the run manifest: {written}"
    )
    records = castings["1"]["evidence_provenance"]
    assert len(records) == 1, records
    assert records[0]["verdict"] == "accepted"
    assert records[0]["casting_commit"] == env["casting_commit"]
    assert records[0]["evidence_for"] == ["AC-023"]

    # The project-root decoy is untouched — a writer that still hits it would
    # leave the run's own manifest empty, which is the bug.
    stale = json.loads(env["stale_manifest"].read_text(encoding="utf-8"))
    assert stale["castings"][0]["evidence_provenance"] == [], (
        "provenance was written to <project_root>/castings/manifest.json, "
        "which no run reads"
    )


def test_malformed_spec_format_version_is_refused_not_downgraded(tmp_path):
    """D-028 — a DECLARED but unparseable ``spec_format_version`` is an error,
    never a silent read as v2.0.

    Reading it as v2.0 routed the run to the stream-skip branch and returned
    ``ok: true`` with zero evidence re-executed: a one-character frontmatter
    typo bought a green gate. Absence still defaults to v2.0 — only an
    unintelligible declaration is refused."""
    from foundry_mcp.tools.evidence import verify_evidence
    from foundry_mcp.tools.foundry_handoff import foundry_accept_casting
    from foundry_mcp.tools.foundry_state import clear_active_run, set_active_run

    env = _build_divergent_spec_repo(tmp_path)
    env["run_spec"].write_text(
        "---\nspec_format_version: 2.1\n---\n# Run spec\n", encoding="utf-8"
    )
    from foundry_mcp.tools.foundry_handoff import foundry_spec_hash

    fresh_hash = foundry_spec_hash(project_root=str(env["project_root"]))["spec_hash"]
    try:
        result = foundry_accept_casting(
            casting_id=1,
            spec_hash=fresh_hash,
            prompt_hash=env["prompt_hash"],
            completion_report="AC-023 implemented at src/gate.py#accept_casting\n",
            project_root=str(env["project_root"]),
            casting_commit=env["casting_commit"],
        )
        assert result["ok"] is False, result
        assert result["error"] == "malformed_spec_format_version"
        assert result["declared_spec_format_version"] == "2.1"
        # The hint names the offending value AND the action, per the
        # named-refusal house rule.
        assert "2.1" in result["hint"] and "v2.1" in result["hint"]

        # verify_evidence refuses on its own too — the gate is not the only
        # caller, and a direct caller must not get the silent downgrade either.
        set_active_run(env["fdir"].name)
        direct = verify_evidence(
            casting_id=1,
            project_root=env["project_root"],
            casting_commit=env["casting_commit"],
            spec_path=env["run_spec"],
            run_dir=env["fdir"],
        )
    finally:
        clear_active_run()

    assert direct["verdict"] == "rejected", direct
    assert direct["spec_format_version"] == "2.1"
    assert direct["provenance_records"] == []
    # Emphatically not the v2.0 skip branch: no EVID-01 skip record was
    # written, because nothing was legitimately skipped.
    assert direct["manifest_updates"] == {}


def test_absent_spec_format_version_still_defaults_to_v20(tmp_path):
    """The other half of D-028's distinction, pinned so the refusal above does
    not creep into specs that simply predate the key. A spec with no version
    declared is a legacy spec, and legacy specs stream-skip as before."""
    from foundry_mcp.tools.evidence import verify_evidence
    from foundry_mcp.tools.foundry_state import clear_active_run, set_active_run

    env = _build_divergent_spec_repo(tmp_path)
    env["run_spec"].write_text("# Run spec, no frontmatter\n", encoding="utf-8")
    set_active_run(env["fdir"].name)
    try:
        result = verify_evidence(
            casting_id=1,
            project_root=env["project_root"],
            casting_commit=env["casting_commit"],
            spec_path=env["run_spec"],
            run_dir=env["fdir"],
        )
    finally:
        clear_active_run()

    assert result["verdict"] == "skipped"
    assert result["spec_format_version"] == "v2.0"
    # And the skip record is PERSISTED where the run can see it (D-028's
    # visibility clause) — the same manifest-path fix as the provenance write.
    assert result["manifest_path"] == str(env["run_manifest"])
    written = json.loads(env["run_manifest"].read_text(encoding="utf-8"))
    evid01 = [s for s in written["stream_skips"] if s["stream_id"] == "EVID-01"]
    assert len(evid01) == 1, written
    assert evid01[0]["spec_version"] == "v2.0"


def test_accept_casting_surfaces_a_v20_stream_skip_to_the_lead(tmp_path):
    """D-028's visibility clause at the surface the lead actually reads.

    A v2.0 skip means evidence verification was structurally bypassed for this
    casting. It is persisted in the manifest, but acceptance still returns
    ``ok: true`` — so the skip record is surfaced in the return as well, rather
    than being discoverable only by whoever later opens the manifest."""
    from foundry_mcp.tools.foundry_handoff import foundry_accept_casting, foundry_spec_hash
    from foundry_mcp.tools.foundry_state import clear_active_run

    env = _build_divergent_spec_repo(tmp_path)
    env["run_spec"].write_text(
        "---\nspec_format_version: v2.0\n---\n# Legacy run spec\n", encoding="utf-8"
    )
    fresh_hash = foundry_spec_hash(project_root=str(env["project_root"]))["spec_hash"]
    try:
        result = foundry_accept_casting(
            casting_id=1,
            spec_hash=fresh_hash,
            prompt_hash=env["prompt_hash"],
            completion_report="AC-023 implemented at src/gate.py#accept_casting\n",
            project_root=str(env["project_root"]),
            casting_commit=env["casting_commit"],
        )
    finally:
        clear_active_run()

    assert result["evidence_verdict"] == "skipped"
    assert len(result["evidence_stream_skips"]) == 1
    assert result["evidence_stream_skips"][0]["stream_id"] == "EVID-01"
    assert result["evidence_stream_skips"][0]["reason"] == "spec_format_version"


def test_accept_casting_without_a_commit_reports_no_spec_path(tmp_path):
    """No-regression — the casting_commit=None backwards-compat shim still
    bypasses evidence verification entirely."""
    from foundry_mcp.tools.foundry_handoff import foundry_accept_casting
    from foundry_mcp.tools.foundry_state import clear_active_run

    env = _build_divergent_spec_repo(tmp_path)
    try:
        result = foundry_accept_casting(
            casting_id=1,
            spec_hash=env["spec_hash"],
            prompt_hash=env["prompt_hash"],
            completion_report="AC-023 implemented at src/gate.py#accept_casting\n",
            project_root=str(env["project_root"]),
        )
    finally:
        clear_active_run()

    assert result["evidence_verdict"] is None
    assert result["evidence_spec_path"] is None
    assert result["ok"] is True, result["warning"]


def test_verify_evidence_reports_which_spec_drove_the_routing(tmp_path):
    """Both branches name the spec they read and the version they parsed — the
    only signal distinguishing "legitimately v2.0" from "the caller pointed at
    a spec that isn't there"."""
    from foundry_mcp.tools.evidence import verify_evidence
    from foundry_mcp.tools.foundry_state import clear_active_run

    env = _build_divergent_spec_repo(tmp_path)
    clear_active_run()

    skipped = verify_evidence(
        casting_id=1,
        project_root=env["project_root"],
        casting_commit=env["casting_commit"],
        spec_path=env["stale_spec"],
        run_dir=tmp_path / "run-skip",
    )
    assert skipped["verdict"] == "skipped"
    assert skipped["spec_path"] == str(env["stale_spec"])
    assert skipped["spec_format_version"] == "v2.0"

    run_dir = tmp_path / "run-engage"
    run_dir.mkdir()
    engaged = verify_evidence(
        casting_id=1,
        project_root=env["project_root"],
        casting_commit=env["casting_commit"],
        spec_path=env["run_spec"],
        run_dir=run_dir,
    )
    assert engaged["verdict"] == "accepted", engaged
    assert engaged["spec_path"] == str(env["run_spec"])
    assert engaged["spec_format_version"] == "v2.1"


def test_header_block_is_not_compared_against_command_output(tmp_path):
    """A real evidence file verifies: its command emits the BODY, while the
    committed file carries `# evidence-*:` header lines on top.

    `_compare_byte_match` documents its `committed` argument as "the
    evidence-file body" but was handed the whole file, so every correctly
    formatted evidence log mismatched on its own header. The bug was invisible
    because `casting_commit` was unreachable over MCP — this comparison had
    never run outside the harness, whose replay file deliberately contains the
    full evidence text.

    Both conventions are pinned: this test covers body-only replay, and
    `test_accept_casting_resolves_the_runs_actual_spec_path` above covers the
    full-file replay the conftest harness uses."""
    from foundry_mcp.tools.evidence import verify_evidence
    from foundry_mcp.tools.foundry_state import clear_active_run

    env = _build_divergent_spec_repo(tmp_path, replay_body_only=True)
    clear_active_run()
    run_dir = tmp_path / "run-body-only"
    run_dir.mkdir()

    result = verify_evidence(
        casting_id=1,
        project_root=env["project_root"],
        casting_commit=env["casting_commit"],
        spec_path=env["run_spec"],
        run_dir=run_dir,
    )
    assert result["verdict"] == "accepted", result
    assert result["failure_token"] is None


def test_strip_leading_header_block_keeps_the_body_verbatim():
    """Only a LEADING run of comment/blank lines is dropped — interior blank
    lines and any later `#` line are body content and must survive, or the
    byte comparison would stop being exact."""
    from foundry_mcp.tools.evidence import _strip_leading_header_block

    text = (
        "# evidence-cmd: pytest\n"
        "# evidence-for: AC-1\n"
        "\n"
        "collected 2 items\n"
        "\n"
        "# a hash line that is real output\n"
        "2 passed\n"
    )
    assert _strip_leading_header_block(text) == (
        "collected 2 items\n"
        "\n"
        "# a hash line that is real output\n"
        "2 passed\n"
    )
    # No header at all → unchanged.
    assert _strip_leading_header_block("plain\noutput\n") == "plain\noutput\n"


def test_missing_spec_path_is_visible_as_a_v20_downgrade(tmp_path):
    """The failure mode FR-017 fixes, pinned: a spec path that does not exist
    parses as v2.0 and skips. Reporting the path is what makes that
    diagnosable instead of silent."""
    from foundry_mcp.tools.evidence import verify_evidence
    from foundry_mcp.tools.foundry_state import clear_active_run

    env = _build_divergent_spec_repo(tmp_path)
    clear_active_run()
    absent = env["project_root"] / "specs" / "does-not-exist.md"

    result = verify_evidence(
        casting_id=1,
        project_root=env["project_root"],
        casting_commit=env["casting_commit"],
        spec_path=absent,
        run_dir=tmp_path / "run-absent",
    )
    assert result["verdict"] == "skipped"
    assert result["spec_format_version"] == "v2.0"
    assert result["spec_path"] == str(absent)


# ---------------------------------------------------------------------------
# D-062 — the corpus and the heuristic can never drift apart again
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parents[4]


def test_no_committed_evidence_log_is_a_stub():
    """Every committed ``evidence/*.log`` clears the stub library, judged by
    the SHIPPED predicate on the SHIPPED bytes.

    This is the pin D-062 asks for, and it is environment-free: no subprocess,
    no worktree, no clone — ``_check_stub_patterns`` is a pure function of the
    file's committed bytes and its declared ``# evidence-cmd:``. It is the
    exact call the gate makes at step 6 of ``_verify_one_evidence_file``.

    It failed for 19 of 25 logs before D-062, because the rule required the
    command's FIRST TOKEN in the first three body lines and this repo's
    evidence commands begin `cd …`/`sh -c …`/`QG=…`. A gate that rejects its
    own corpus is wedged the moment it becomes reachable, so the corpus is now
    a test input: change the heuristic and this fails, commit a stub log and
    this fails.
    """
    evidence_dir = REPO_ROOT / "evidence"
    logs = sorted(evidence_dir.glob("*.log")) if evidence_dir.exists() else []
    if not logs:
        pytest.skip(f"no committed evidence corpus at {evidence_dir}")

    stub_hits = {}
    for log_path in logs:
        text = log_path.read_text(encoding="utf-8")
        header = evidence._parse_evidence_header(text)
        token = evidence._check_stub_patterns(text, header.get("cmd") or "")
        if token:
            stub_hits[log_path.name] = token

    assert stub_hits == {}, (
        f"{len(stub_hits)} of {len(logs)} committed evidence logs are rejected "
        f"as stubs by the shipped gate: {stub_hits}"
    )


def test_every_committed_evidence_log_declares_a_command():
    """The corpus also has to clear the rung ABOVE the stub library: a log with
    no ``# evidence-cmd:`` header is rejected as EVIDENCE_COMMAND_MISSING
    before re-execution is even attempted, so it can never reach the sweep
    above and would otherwise pass it vacuously."""
    evidence_dir = REPO_ROOT / "evidence"
    logs = sorted(evidence_dir.glob("*.log")) if evidence_dir.exists() else []
    if not logs:
        pytest.skip(f"no committed evidence corpus at {evidence_dir}")

    undeclared = [
        p.name
        for p in logs
        if not (evidence._parse_evidence_header(p.read_text(encoding="utf-8")).get("cmd") or "").strip()
    ]
    assert undeclared == [], f"evidence logs with no `# evidence-cmd:`: {undeclared}"


# ---------------------------------------------------------------------------
# D-109 / D-116 — the evidence gate cannot be bypassed by a total-redaction
# pattern, and a worktree failure becomes a named token instead of a traceback.
#
# SECURITY-RELEVANT (A-AUTO-005). The run's binding security constraint is
# process-level: the verification loop's behavioural and security defect
# standard must not be weakened. D-109 weakened it to nothing — one header line
# made EVID-01 accept any output for any command — so these are the tests that
# fail if the escape-hatch stops being closed.
# ---------------------------------------------------------------------------

#: Every shape of "matches everything" that defeats the byte-match. The last
#: three are placeholder-anchored: they erase only in the direction they reach,
#: so a guard probing a single sample with the placeholder in the middle passes
#: them — which is how ``[\s\S]*<VOLATILE>`` slipped through the first cut.
_TOTAL_REDACTION_PATTERNS = [
    r"[\s\S]*",
    r"[\s\S]+",
    r"[\S\s]*?",
    r"(?s).*",
    r"(?s)^.*$",
    r".*",
    r"[\s\S]*<VOLATILE>",
    r"<VOLATILE>[\s\S]*",
    r"[\s\S]*<VOLATILE>[\s\S]*",
]


@pytest.mark.parametrize("pattern", _TOTAL_REDACTION_PATTERNS)
def test_a_total_redaction_pattern_is_refused(pattern):
    """D-109. ``_apply_volatile_redaction`` applies each declared pattern to
    BOTH the committed log and the re-execution capture, so a pattern matching
    everything collapses both sides to the same string and ``_compare_byte_match``
    returns matched=True for ANY output.

    The module header calls this a "closed escape-hatch: ONLY declared
    volatility tolerated". Total redaction is where the hatch stops being
    closed, because what is declared is no longer volatility — it is the
    evidence.
    """
    with pytest.raises(ValueError) as exc:
        evidence._apply_volatile_redaction(
            "real output line one\nreal output line two\n", [pattern]
        )
    assert "EVIDENCE_VOLATILE_MALFORMED" in str(exc.value)


@pytest.mark.parametrize(
    "pattern",
    [
        r"\d+\.\d+s",
        r"\b\d+ms\b",
        r"pid=\d+",
        r"20\d{2}-\d{2}-\d{2}T",
        r"rootdir: \S+",
        r"rootdir: .*",
        r"cachedir: .*",
        r"judged: \d+",
        r"\d+ deselected",
        r"Installed \d+ packages? in \d+(\.\d+)?ms",
        r"platform \S+ -- Python \S+, pytest-\S+, pluggy-\S+ -- \S+",
        r"completed in <VOLATILE>",
    ],
)
def test_narrow_volatile_patterns_are_still_accepted(pattern):
    """The false-positive floor for D-109's guard, and the reason it probes the
    PATTERN rather than the log.

    Asking "did this substitution empty this text?" conflates two different
    things: ``[\\s\\S]*`` empties every text and is a bypass, while the ladder
    pattern ``completed in <VOLATILE>`` empties only a log that happens to
    consist of nothing but that phrase. Every pattern here is one the shipped
    corpus or agents/teammate.md actually declares; a guard that refused any of
    them would break real evidence.
    """
    evidence._apply_volatile_redaction(
        "completed in 42ms\nrootdir: /x\nreal content\n", [pattern]
    )


def test_every_pattern_the_committed_corpus_declares_is_accepted():
    """The same floor, DERIVED from the corpus instead of typed beside it, so a
    pattern added to a real evidence log is covered the day it is written."""
    evidence_dir = REPO_ROOT / "evidence"
    logs = sorted(evidence_dir.glob("*.log")) if evidence_dir.exists() else []
    if not logs:
        pytest.skip(f"no committed evidence corpus at {evidence_dir}")

    refused = []
    for log in logs:
        header = evidence._parse_evidence_header(log.read_text(encoding="utf-8"))
        for pattern in header.get("volatile", []):
            replacement = (
                "<TIMING>"
                if evidence.VOLATILE_PLACEHOLDER in pattern
                else evidence.VOLATILE_PLACEHOLDER
            )
            if evidence._pattern_redacts_everything(pattern, replacement):
                refused.append(f"{log.name}: {pattern!r}")
    assert refused == [], f"the guard refuses a pattern real evidence uses: {refused}"


def test_a_fabricated_log_cannot_buy_a_pass_with_total_redaction(tmp_path):
    """D-109 end to end, at the surface where it was exploitable.

    A fabricated body, a real-but-unrelated command, and one
    ``# evidence-volatile: [\\s\\S]*`` line used to produce
    ``verdict='accepted'`` while ``log_sha256 != captured_sha256`` — the
    redacted forms were equal because both had been erased. The stub library
    could not save it either: TOO_SMALL reads the RAW log and VACUOUS_CMD saw a
    real command.
    """
    worktree = tmp_path / "worktree"
    (worktree / "evidence").mkdir(parents=True)
    log = worktree / "evidence" / "casting-3-fabricated.log"
    # A REAL command (python3, not a shell builtin) and a body long enough to
    # clear the 128-byte floor, so neither VACUOUS_CMD nor TOO_SMALL can be
    # what rejects this. The redaction guard has to be the thing that catches
    # it, which is the whole claim.
    log.write_text(
        "# evidence-cmd: python3 -c \"print('genuine output from a real command')\"\n"
        "# evidence-for: FR-017\n"
        "# evidence-volatile: [\\s\\S]*\n"
        "\n"
        "this body was never produced by that command, and padding follows so "
        "the stub library's 128-byte TOO_SMALL floor cannot be what rejects it\n",
        encoding="utf-8",
    )

    record = evidence._verify_one_evidence_file(
        evidence_path=log, worktree_path=worktree, casting_commit="0" * 40,
    )

    assert record["verdict"] == "rejected", record
    assert record["failure_token"] == "EVIDENCE_VOLATILE_MALFORMED", record
    assert record["failure_token"] in evidence.KNOWN_EVIDENCE_FAILURE_TOKENS


def test_a_narrow_volatile_declaration_still_verifies(tmp_path):
    """The counterpart: a log whose declared volatility is genuinely narrow
    still byte-matches a clean re-execution. Without this, D-109's guard could
    have been 'fixed' by refusing all volatility."""
    worktree = tmp_path / "worktree"
    (worktree / "evidence").mkdir(parents=True)
    log = worktree / "evidence" / "casting-3-honest.log"
    body = (
        "verification complete for the honest case\n"
        "elapsed 1.25s\n"
        "every other line of this body is byte-stable across runs\n"
    )
    log.write_text(
        "# evidence-cmd: python3 -c \"print('verification complete for the "
        "honest case'); print('elapsed 1.25s'); print('every other line of "
        "this body is byte-stable across runs')\"\n"
        "# evidence-for: FR-017\n"
        "# evidence-volatile: \\d+\\.\\d+s\n"
        "\n" + body,
        encoding="utf-8",
    )

    record = evidence._verify_one_evidence_file(
        evidence_path=log, worktree_path=worktree, casting_commit="0" * 40,
    )
    assert record["verdict"] == "accepted", record


# --- D-116: worktree failures are translated, never escaped ------------------
def _git_shim(tmp_path, script: str) -> Path:
    """A directory holding a fake ``git`` that behaves as ``script`` says."""
    shim = tmp_path / "shim"
    shim.mkdir()
    fake = shim / "git"
    fake.write_text(script, encoding="utf-8")
    fake.chmod(0o755)
    return shim


def test_a_hung_git_becomes_a_named_token_not_a_traceback(tmp_path, monkeypatch):
    """D-116. ``_setup_worktree`` raises RuntimeError only for a non-zero ``git
    worktree add``. Its ``subprocess.run(..., timeout=30)`` also raises
    TimeoutExpired, which is NOT a RuntimeError, so it escaped
    ``verify_evidence`` untranslated and the gate returned a traceback instead
    of a verdict.

    ``worktree_helpers.py`` is a read-only dependency, so the translation lands
    at THIS module's call boundary.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    monkeypatch.setenv(
        "PATH", f"{_git_shim(tmp_path, '#!/bin/sh\nsleep 60\n')}:{os.environ['PATH']}"
    )

    result = evidence._verify_evidence_v21_body(
        casting_id=3, project_root=repo, casting_commit="a" * 40,
        run_dir=repo / "run",
    )

    assert result["verdict"] == "rejected"
    assert result["failure_token"] == "EVIDENCE_COMMIT_MISSING"
    assert result["failure_token"] in evidence.KNOWN_EVIDENCE_FAILURE_TOKENS
    assert "TimeoutExpired" in result["failure_detail"], (
        "the detail must name the REAL cause, or the operator hunts a bad SHA "
        "when git is simply hung"
    )


def test_a_missing_git_becomes_a_named_token_not_a_traceback(tmp_path, monkeypatch):
    """D-116's sibling hole: a missing git binary raises FileNotFoundError,
    which is an OSError and equally not a RuntimeError."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    empty = tmp_path / "empty-path"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))

    result = evidence._verify_evidence_v21_body(
        casting_id=3, project_root=repo, casting_commit="a" * 40,
        run_dir=repo / "run",
    )

    assert result["verdict"] == "rejected"
    assert result["failure_token"] == "EVIDENCE_COMMIT_MISSING"
    assert "FileNotFoundError" in result["failure_detail"]


def test_a_failing_teardown_does_not_destroy_the_verdict(tmp_path, monkeypatch):
    """D-116's most damaging variant. Teardown makes three more timeout-bounded
    git calls and runs in a ``finally``, where a raised exception REPLACES
    whatever the body was returning — so a slow git during cleanup could
    discard a verdict that had already been computed correctly.
    """
    import foundry_mcp.tools.evidence as ev

    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)

    def _explode(*_args, **_kwargs):
        raise subprocess.TimeoutExpired(cmd="git worktree remove", timeout=30)

    monkeypatch.setattr(ev, "_teardown_worktree", _explode)
    monkeypatch.setattr(ev, "_setup_worktree", lambda *a, **k: tmp_path / "wt")
    (tmp_path / "wt").mkdir()

    result = ev._verify_evidence_v21_body(
        casting_id=3, project_root=repo, casting_commit="a" * 40,
        run_dir=repo / "run",
    )

    # No evidence files in the synthetic worktree, so the BODY's own verdict is
    # EVIDENCE_COMMAND_MISSING. The point is that it survives teardown.
    assert result["failure_token"] == "EVIDENCE_COMMAND_MISSING", result


# --- D-111: two live worktrees for one casting never share a directory -------
#
# TV-C-03. `_setup_worktree` derived its path from the casting id alone and then
# tore down "whatever is already there" OUTSIDE `_WORKTREE_LOCK`, which covered
# only `git worktree add`. Driven with two threads on casting 1: A's marker file
# was present before B ran and gone after, because B deleted A's LIVE worktree
# and re-created it — A's evidence commands then ran against a tree that had
# been destroyed and rebuilt mid-flight, and the operator saw an
# EVIDENCE_OUTPUT_MISMATCH with nothing pointing at concurrency.
#
# These drive `worktree_helpers` directly rather than through the
# `run_accept_casting_with_evidence` harness because the harness deliberately
# gives each concurrent thread a DIFFERENT casting id (conftest.py:634) — the
# exact collision under test is the one it is built to avoid.


def _worktree_repo(tmp_path: Path) -> tuple[Path, Path, str]:
    """A real one-commit git repo. Returns (project_root, run_dir, commit)."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "t"], check=True)
    (repo / "tracked.txt").write_text("content\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "tracked.txt"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "init"], check=True)
    head = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    return repo, run_dir, head


@pytest.mark.parametrize("dir_prefix", ["casting-", "test-deriver-cycle-"])
def test_a_same_casting_peer_cannot_destroy_a_live_worktree(tmp_path, dir_prefix):
    """The D-111 repro. Two threads, one casting id: the first thread's tree
    must still be intact — same path, same marker — after the second has set up
    and torn down its own.

    Driven at the HELPER, over BOTH prefixes, because the helper is the whole
    surface the two production callers share: ``verify_evidence`` passes the
    default ``casting-`` and Phase 7's ``test_deriver.run_test_deriver`` passes
    ``test-deriver-cycle-``, and the latter has no test module of its own — so
    a guarantee pinned only through ``verify_evidence`` would leave the second
    caller's collision unasserted.
    """
    import threading

    from foundry_mcp.tools import worktree_helpers as wh

    repo, run_dir, head = _worktree_repo(tmp_path)

    a_ready = threading.Event()
    b_done = threading.Event()
    paths: dict[str, Path] = {}
    observed: dict[str, bool] = {}
    errors: list[BaseException] = []

    def _thread_a() -> None:
        try:
            path = wh._setup_worktree(repo, 1, head, run_dir, dir_prefix=dir_prefix)
            paths["a"] = path
            (path / "A_IS_LIVE").write_text("a", encoding="utf-8")
            a_ready.set()
            b_done.wait(timeout=60)
            # Recorded HERE, inside A's own lifetime — A tears its tree down
            # two lines below, so a Path checked after the join would report
            # A's own cleanup as the peer's damage.
            observed["marker"] = (path / "A_IS_LIVE").is_file()
            observed["checkout"] = (path / "tracked.txt").is_file()
            wh._teardown_worktree(repo, path)
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)
            a_ready.set()

    def _thread_b() -> None:
        try:
            a_ready.wait(timeout=60)
            path = wh._setup_worktree(repo, 1, head, run_dir, dir_prefix=dir_prefix)
            paths["b"] = path
            wh._teardown_worktree(repo, path)
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)
        finally:
            b_done.set()

    threads = [threading.Thread(target=t) for t in (_thread_a, _thread_b)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=90)
    assert not errors, errors

    assert paths["a"] != paths["b"], (
        f"both invocations claimed {paths['a']} — a same-casting peer still "
        f"derives the live tree's path"
    )
    assert observed["marker"], (
        "the peer destroyed the live worktree: A's marker was written before B "
        "ran and was gone after"
    )
    assert observed["checkout"], "A's checkout did not survive B's setup intact"


def test_the_uncontended_path_keeps_the_plain_casting_name(tmp_path):
    """The claim is contention-only. A lone invocation must land on exactly
    ``worktrees/casting-{id}``: the evidence harness measures teardown by
    asserting that path is gone (conftest.py:690), and a name that is unique per
    call would make that assertion pass without measuring anything.
    """
    from foundry_mcp.tools import worktree_helpers as wh

    repo, run_dir, head = _worktree_repo(tmp_path)

    path = wh._setup_worktree(repo, 7, head, run_dir)
    assert path == run_dir / "worktrees" / "casting-7"
    wh._teardown_worktree(repo, path)
    assert not path.exists()

    # And nothing is stranded: the same id reuses the same name next time.
    again = wh._setup_worktree(repo, 7, head, run_dir)
    assert again == path
    wh._teardown_worktree(repo, again)


def test_teardown_leaves_no_worktree_dir_behind(tmp_path):
    """Non-vacuous leak check. Serial setup/teardown of three castings must
    leave no worktree DIRECTORY behind — the check the harness's
    ``worktree_torn_down`` flag can no longer make on its own once suffixed
    siblings are possible.

    The empty ``{name}.lock`` claim sidecars (D-131) are the one thing that
    does survive, and deliberately: unlinking a lock file is the classic race
    — a peer that has already opened the same path would take its lock on an
    unlinked inode and both would believe they held the tree. They are asserted
    positively rather than merely tolerated, so a fix that silently stopped
    taking claims at all could not pass this by leaving the directory tidy.
    """
    from foundry_mcp.tools import worktree_helpers as wh

    repo, run_dir, head = _worktree_repo(tmp_path)

    for casting_id in (1, 2, 3):
        path = wh._setup_worktree(repo, casting_id, head, run_dir)
        assert path.is_dir()
        wh._teardown_worktree(repo, path)

    leftovers = sorted(p.name for p in (run_dir / "worktrees").iterdir())
    dirs = sorted(p.name for p in (run_dir / "worktrees").iterdir() if p.is_dir())
    assert dirs == [], f"worktree dirs leaked: {dirs}"
    assert leftovers == ["casting-1.lock", "casting-2.lock", "casting-3.lock"], (
        f"unexpected residue beside the worktrees: {leftovers}"
    )
    # And every claim is RELEASED, not merely file-shaped: a peer must be able
    # to take each one back.
    for name in leftovers:
        reclaimed = wh._acquire_claim(run_dir / "worktrees" / name[: -len(".lock")])
        assert reclaimed is not None, f"{name} is still held after teardown"
        reclaimed.close()


def test_a_crashed_runs_leftover_is_reclaimed_by_name(tmp_path):
    """Pitfall 1, which a per-call unique path would have silently retired. A
    dir left by a process that died before teardown is claimed by nobody, so
    the next invocation for that casting tears it down and reuses the name
    instead of accumulating one orphan per GRIND cycle."""
    from foundry_mcp.tools import worktree_helpers as wh

    repo, run_dir, head = _worktree_repo(tmp_path)

    stale = run_dir / "worktrees" / "casting-4"
    stale.mkdir(parents=True)
    (stale / "LITTER").write_text("from a dead run\n", encoding="utf-8")

    path = wh._setup_worktree(repo, 4, head, run_dir)
    assert path == stale
    assert not (path / "LITTER").exists(), "the leftover was not cleaned up"
    assert (path / "tracked.txt").is_file()
    wh._teardown_worktree(repo, path)


def test_a_failed_setup_does_not_strand_the_casting_name(tmp_path):
    """The claim outlives the call by design, so a setup that returns no live
    tree has to release it — otherwise one unresolvable commit would push every
    later invocation for that casting onto a suffixed path forever."""
    from foundry_mcp.tools import worktree_helpers as wh

    repo, run_dir, head = _worktree_repo(tmp_path)

    with pytest.raises(RuntimeError):
        wh._setup_worktree(repo, 5, "0" * 40, run_dir)  # unresolvable commit

    path = wh._setup_worktree(repo, 5, head, run_dir)
    assert path == run_dir / "worktrees" / "casting-5"
    wh._teardown_worktree(repo, path)


# --------------------------------------------------------------------------- #
# D-126 — the composed-redaction residue gate (FR-017 / AC-023 / OT-011).
#
# D-109 was closed with a per-pattern probe against three FIXED canary strings,
# under a docstring that argued "THE TEST IS ON THE PATTERN, NOT ON THE LOG".
# That argument was the hole. A probe that never meets the real text cannot see
# a pattern anchored on a token the real text contains and the canaries do not,
# and a probe that runs inside the per-pattern loop cannot see two patterns that
# are harmless apart and annihilating together.
#
# Every bypass below was driven end to end through
# ``_DISPATCH["Foundry-Accept-Casting"]`` with ``casting_commit`` set and came
# back ``ok=True``, ``evidence_verdict='accepted'``, ``log_sha256 !=
# captured_sha256`` — a fabricated log buying a pass with one header line.
# --------------------------------------------------------------------------- #

#: The five bypasses PROVE drove, each with the fabricated log and the real
#: capture it was driven with. Every one carries the anchor it exploits, which
#: is the point: the first three are invisible to a fixed canary because the
#: canary does not contain their anchor, the fourth fingerprints the canary's
#: own literal to match everything EXCEPT a canary, and the fifth is a PAIR
#: whose halves each pass the per-pattern probe and compose to nothing.
_DRIVEN_REDACTION_BYPASSES = [
    pytest.param(
        [r"== test session starts ==[\s\S]*"],
        "== test session starts ==\n9999 passed\n",
        "== test session starts ==\n1 failed, 2 passed\n",
        id="anchored-on-a-token-no-canary-contains",
    ),
    pytest.param(
        [r"(?s)(?<=\A.)[\s\S]*"],
        "x9999 passed in 0.01s\nfabricated\n",
        "x1 failed, 2 passed in 9.99s\nreal\n",
        id="everything-after-the-first-character",
    ),
    pytest.param(
        [r"__init__\.py[\s\S]*"],
        "src/__init__.py\n9999 passed\nfabricated\n",
        "src/__init__.py\n1 failed, 2 passed\nreal\n",
        id="anchored-on-a-real-source-path",
    ),
    pytest.param(
        [r"(?![\s\S]*4f2a)[\s\S]*"],
        "9999 passed\nfabricated body\n",
        "1 failed, 2 passed\nreal body\n",
        id="fingerprints-the-canary-literal",
    ),
    pytest.param(
        [r"\A[^\n]*", r"(?s)(?<=\n)[\s\S]*"],
        "first line\n9999 passed\nfabricated\n",
        "first line\n1 failed, 2 passed\nreal\n",
        id="a-PAIR-that-only-annihilates-composed",
    ),
]


@pytest.mark.parametrize("patterns,fabricated,real", _DRIVEN_REDACTION_BYPASSES)
def test_a_driven_redaction_bypass_is_refused(patterns, fabricated, real):
    """Each bypass must REJECT at the comparator, naming a closed-vocabulary
    token — not quietly report ``matched=True`` for two texts that plainly
    differ."""
    with pytest.raises(ValueError) as exc:
        evidence._compare_byte_match(fabricated, real, patterns)
    assert "EVIDENCE_VOLATILE_MALFORMED" in str(exc.value)


@pytest.mark.parametrize("patterns,fabricated,real", _DRIVEN_REDACTION_BYPASSES)
def test_the_per_pattern_canary_probe_could_not_have_caught_these(
    patterns, fabricated, real
):
    """The falsifier for "D-109 already covered this".

    If any bypass here were refused by the old per-pattern probe, this file
    would be re-asserting a guarantee that already held and the gate below
    would be untested scaffolding. Every one passes the probe, which is why the
    composed check had to exist.
    """
    for pattern in patterns:
        replacement = (
            evidence.TIMING_PLACEHOLDER
            if evidence.VOLATILE_PLACEHOLDER in pattern
            else evidence.VOLATILE_PLACEHOLDER
        )
        assert not evidence._pattern_redacts_everything(pattern, replacement), (
            f"{pattern!r} is caught by the per-pattern probe, so it is not a "
            f"witness for the hole the composed gate closes"
        )


@pytest.mark.parametrize("annihilated_side", ["committed", "captured"])
def test_both_sides_of_the_comparison_are_guarded(annihilated_side):
    """DERIVED MEMBERSHIP over the sides of the comparison.

    The redaction runs on the committed log AND on the re-execution capture,
    so "the guard is applied" is two claims, not one. A fix that guarded only
    the side its defect report happened to name would leave the other open —
    and an attacker picks the side. Parametrizing over the side is what makes
    a half-bound guard a failing test rather than a passing one.

    The annihilating pattern is anchored on a token present in ONE side only,
    so exactly one side collapses and the other survives intact.
    """
    intact = "alpha bravo charlie delta echo foxtrot golf hotel india\n"
    doomed = "ANCHOR\nzulu yankee xray whiskey victor uniform tango sierra\n"
    pattern = r"ANCHOR[\s\S]*"
    if annihilated_side == "committed":
        committed, captured = doomed, intact
    else:
        committed, captured = intact, doomed

    with pytest.raises(ValueError) as exc:
        evidence._compare_byte_match(committed, captured, [pattern])
    assert "EVIDENCE_VOLATILE_MALFORMED" in str(exc.value)
    # And it names WHICH side collapsed, so the operator is not left diffing
    # two logs to find out.
    expected = "committed log" if annihilated_side == "committed" else "re-execution capture"
    assert expected in str(exc.value)


def test_a_bypass_cannot_buy_a_pass_through_the_whole_verifier():
    """The bypass at the surface where it was exploitable, not at the helper.

    A fabricated body, a REAL command whose output does not match it, and one
    ``# evidence-volatile:`` line anchored on a token both bodies share. Before
    the composed gate this produced ``verdict='accepted'`` with
    ``log_sha256 != captured_sha256``. The stub library cannot be what saves
    it: the command is real and the body clears the 128-byte floor.
    """
    import tempfile

    workdir = Path(tempfile.mkdtemp())
    (workdir / "evidence").mkdir()
    log = workdir / "evidence" / "casting-3-fabricated.log"
    log.write_text(
        "# evidence-cmd: python3 -c \"print('ANCHOR'); print('1 failed, 2 passed')\"\n"
        "# evidence-for: FR-017\n"
        "# evidence-volatile: ANCHOR[\\s\\S]*\n"
        "\n"
        "ANCHOR\n"
        "9999 passed and nothing failed, which this command never printed; "
        "this padding exists so the stub library's 128-byte TOO_SMALL floor "
        "cannot be what rejects the log\n",
        encoding="utf-8",
    )

    record = evidence._verify_one_evidence_file(
        evidence_path=log, worktree_path=workdir, casting_commit="0" * 40
    )

    assert record["verdict"] == "rejected", record
    assert record["failure_token"] in evidence.KNOWN_EVIDENCE_FAILURE_TOKENS
    assert record["failure_token"] == "EVIDENCE_VOLATILE_MALFORMED", record


def test_the_composed_gate_does_not_over_correct_on_the_real_corpus():
    """The false-positive floor, DERIVED from the shipped corpus rather than
    typed beside it, so a log added tomorrow is covered the day it lands.

    D-126's fix changes what the gate ACCEPTS, and a residue floor set too high
    would reject real evidence — which is the failure mode that would matter
    most, because it would look like a behavioural regression in whatever the
    log happened to prove. Every committed log's own declared patterns, applied
    to its own body, must survive.
    """
    evidence_dir = REPO_ROOT / "evidence"
    logs = sorted(evidence_dir.glob("*.log")) if evidence_dir.exists() else []
    if not logs:
        pytest.skip(f"no committed evidence corpus at {evidence_dir}")

    refused = []
    for log in logs:
        text = log.read_text(encoding="utf-8")
        header = evidence._parse_evidence_header(text)
        body = evidence._strip_leading_header_block(text)
        try:
            evidence._compare_byte_match(body, body, header.get("volatile", []))
        except ValueError as exc:  # noqa: PERF203
            refused.append(f"{log.name}: {exc}")
    assert refused == [], f"the residue gate refuses real evidence: {refused}"


def test_a_silent_command_is_not_blamed_on_its_volatile_declaration():
    """The other over-correction. A body with no content of its own has nothing
    for the redaction to erase, so the emptiness is the command's and refusing
    it would punish a correctly-quiet command for a declaration that never
    fired."""
    assert evidence._compare_byte_match("", "", [r"\d+\.\d+s"])[0] is True
    assert evidence._compare_byte_match("\n\n", "\n\n", [r"\d+\.\d+s"])[0] is True


def test_an_undeclared_redaction_never_reaches_the_gate():
    """No declared patterns means the redaction is the identity function, so a
    log that is genuinely mostly whitespace or genuinely short is compared, not
    judged. The gate exists to police DECLARATIONS."""
    assert evidence._compare_byte_match("x\n", "x\n", [])[0] is True
    matched, diff, _, _ = evidence._compare_byte_match("x\n", "y\n", [])
    assert matched is False and diff


# --------------------------------------------------------------------------- #
# D-131 — the worktree claim has to hold across OS PROCESSES (OT-011 / FR-017).
#
# D-111 closed the same-path collision with a module-level Python set, and a
# set is invisible to another process. The module justified that by saying each
# process "derives its worktrees under its own run dir"; ``run_dir`` is
# ``{project_root}/foundry-archive/{run}`` (foundry_handoff.py:452), a function
# of the RUN. A lead re-running Foundry-Accept-Casting while a teammate's
# verification is in flight, or two INSPECT streams verifying one casting, land
# on the same path from different processes — and process 1, finding it
# unclaimed IN ITS OWN SET, tore down process 0's LIVE worktree and left it
# with a spurious EVIDENCE_OUTPUT_MISMATCH.
#
# These drive two real subprocesses, which is the shape of the repro. Threads
# cannot witness this defect: the old set was correct within one process, and
# the four thread-driven tests above passed throughout.
# --------------------------------------------------------------------------- #

_WORKTREE_PEER_SCRIPT = '''
import json, sys, time
from pathlib import Path
from foundry_mcp.tools import worktree_helpers as wh

role, repo, run_dir, head, sync, out = sys.argv[1:7]
repo, run_dir, sync, out = Path(repo), Path(run_dir), Path(sync), Path(out)


def wait_for(marker, timeout=90.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if marker.exists():
            return True
        time.sleep(0.02)
    return False


result = {"role": role}
try:
    if role == "holder":
        path = wh._setup_worktree(repo, 1, head, run_dir)
        result["path"] = str(path)
        (path / "HOLDER_IS_LIVE").write_text("live", encoding="utf-8")
        (sync / "holder-ready").write_text("", encoding="utf-8")
        result["peer_finished"] = wait_for(sync / "peer-done")
        # Measured HERE, inside the holder's own lifetime: it tears its tree
        # down two lines below, so a check made after the process exits would
        # report the holder's own cleanup as the peer's damage.
        result["marker_survived"] = (path / "HOLDER_IS_LIVE").is_file()
        result["checkout_survived"] = (path / "tracked.txt").is_file()
        wh._teardown_worktree(repo, path)
    else:
        result["holder_ready"] = wait_for(sync / "holder-ready")
        path = wh._setup_worktree(repo, 1, head, run_dir)
        result["path"] = str(path)
        wh._teardown_worktree(repo, path)
finally:
    if role != "holder":
        (sync / "peer-done").write_text("", encoding="utf-8")
    out.write_text(json.dumps(result), encoding="utf-8")
'''


def _run_worktree_peers(tmp_path: Path) -> tuple[dict, dict]:
    """Drive the holder/peer pair as two real OS processes. Returns both reports."""
    repo, run_dir, head = _worktree_repo(tmp_path)
    sync = tmp_path / "sync"
    sync.mkdir()
    script = tmp_path / "peer.py"
    script.write_text(_WORKTREE_PEER_SCRIPT, encoding="utf-8")

    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO_ROOT / "plugins" / "foundry" / "mcp-server" / "src")

    procs = {}
    for role in ("holder", "peer"):
        procs[role] = subprocess.Popen(
            [
                sys.executable, str(script), role, str(repo), str(run_dir),
                head, str(sync), str(tmp_path / f"{role}.json"),
            ],
            env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        )
    reports = {}
    for role, proc in procs.items():
        out, _ = proc.communicate(timeout=180)
        report_path = tmp_path / f"{role}.json"
        assert report_path.is_file(), f"{role} produced no report; output:\n{out}"
        reports[role] = json.loads(report_path.read_text(encoding="utf-8"))
    return reports["holder"], reports["peer"]


def test_a_second_PROCESS_cannot_destroy_a_live_worktree(tmp_path):
    """The D-131 repro, driven the way PROVE drove it.

    Two OS processes, one casting id, one run dir. The claim has to be visible
    to both or the peer computes the holder's path, judges it unclaimed, and
    deletes a checkout that has commands running against it.
    """
    holder, peer = _run_worktree_peers(tmp_path)

    assert "error" not in holder and "error" not in peer, (holder, peer)
    assert holder["peer_finished"], "the peer never ran; the race was not driven"
    assert peer["holder_ready"], "the holder never came up; the race was not driven"
    assert holder["path"] != peer["path"], (
        f"both processes claimed {holder['path']} — the claim is still invisible "
        f"across the process boundary"
    )
    assert holder["marker_survived"], (
        "the peer process destroyed the live worktree: the holder's marker was "
        "written before the peer ran and was gone after"
    )
    assert holder["checkout_survived"], (
        "the holder's checkout did not survive the peer's setup intact"
    )


def test_the_unsuffixed_name_goes_to_whichever_process_arrives_first(tmp_path):
    """The claim is contention-only ACROSS processes too, not just within one.

    One of the two must land on the plain ``worktrees/casting-1`` — the name
    the evidence harness measures teardown against (conftest.py:690) — and the
    other on a suffixed sibling. If both were uniquified the harness's teardown
    assertion would pass without measuring anything.
    """
    holder, peer = _run_worktree_peers(tmp_path)
    landed = {Path(holder["path"]).name, Path(peer["path"]).name}
    assert "casting-1" in landed, f"nobody took the unsuffixed name: {landed}"
    assert landed == {"casting-1", "casting-1-1"}, landed


def test_the_claim_itself_is_refused_to_another_process(tmp_path):
    """The mechanism under the repro, asserted directly.

    ``_acquire_claim`` is what the two tests above exercise through four git
    invocations; this one asks it the question straight, so a regression is
    reported as "the claim stopped excluding" rather than as a worktree
    mystery. A held path must be refused to a second process, and released the
    moment the holder lets go.
    """
    from foundry_mcp.tools import worktree_helpers as wh

    target = tmp_path / "worktrees" / "casting-9"
    target.parent.mkdir(parents=True)

    probe = tmp_path / "probe.py"
    probe.write_text(
        "import sys\n"
        "from pathlib import Path\n"
        "from foundry_mcp.tools import worktree_helpers as wh\n"
        "handle = wh._acquire_claim(Path(sys.argv[1]))\n"
        "print('FREE' if handle is not None else 'HELD')\n",
        encoding="utf-8",
    )
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO_ROOT / "plugins" / "foundry" / "mcp-server" / "src")

    def _probe() -> str:
        return subprocess.run(
            [sys.executable, str(probe), str(target)],
            env=env, capture_output=True, text=True, timeout=60,
        ).stdout.strip()

    assert _probe() == "FREE", "an unclaimed path must be claimable"

    held = wh._acquire_claim(target)
    assert held is not None
    wh._WORKTREE_CLAIMS[str(target)] = held
    try:
        assert _probe() == "HELD", (
            "a second PROCESS took a claim this process holds — the exact "
            "invisibility that let a peer delete a live worktree"
        )
    finally:
        wh._release_claim(target)

    assert _probe() == "FREE", "the claim outlived its holder's release"


# --------------------------------------------------------------------------- #
# D-135 — the residue floor measures VOLUME; these measure DISCRIMINATION.
# (FR-017 / AC-023 / OT-011 / NFR-002 — SECURITY-RELEVANT, A-AUTO-005.)
#
# D-126 replaced the per-pattern canary probe with a composed residue floor and
# genuinely closed the annihilation family. It did not close the gate. PROVE
# drove a fabricated log END TO END through the shipped ``verify_evidence`` on
# a real git repo at a real casting commit: a committed log claiming
# ``== 1650 passed, 4 skipped in 75.00s ==`` against a command that actually
# prints ``== 1631 passed, 19 failed, 4 skipped in 78.11s ==``, declared
# volatile as ``['== \\d+ passed.*?==', '\\d+\\.\\d+s']`` — "the summary line
# has a duration in it, so it is volatile". Result: ``verdict='accepted'``,
# ``failure_token=None``, ``log_sha256=sha256:6bfea6b52f8cb162`` against
# ``captured_sha256=sha256:618dc112c827e21c`` — the raw texts plainly differ —
# and both redacted forms collapsing to ``sha256:9a56c07c7fa727b0``. The
# per-pattern probe reported 'clears' for BOTH patterns and the composed
# residue came in at 99.4%, comfortably over the 25% floor.
#
# One line redacted, 99.4% of the log surviving, and a log claiming a fully
# green 1650-test suite byte-matching a capture containing 19 FAILURES. A gate
# that can be talked out of comparing is not a gate.
#
# EVERY FIXTURE BELOW CARRIES A REAL BODY. That is not decoration: a one-line
# fixture is annihilated by the D-126 volume floor, which would make this whole
# block re-assert a guarantee that already held. ``_BODY`` is what keeps the
# residue high enough that only the discrimination guard can be what refuses.
# --------------------------------------------------------------------------- #

#: A plausible surrounding log body, long enough that redacting one line leaves
#: the composed residue far above ``EVIDENCE_MIN_RESIDUE_RATIO``. Mirrors the
#: shape of the corpus: a header block, some per-item lines, then the summary.
_BODY = (
    "== test session starts ==\n"
    "collected 1650 items across 41 files in the mcp-server package\n"
    "tests/test_evidence.py ................................ [ 24%]\n"
    "tests/test_foundry_init.py ...................... [ 51%]\n"
    "tests/test_observations.py .......................... [ 78%]\n"
    "tests/test_symbol_cites.py ................ [100%]\n"
)


def _residue_ratio(text: str, patterns: list) -> float:
    """The D-126 measure, so a fixture can assert it is NOT what refused."""
    before = evidence._content_residue(text)
    after = evidence._content_residue(
        evidence._apply_volatile_redaction(text, patterns)
    )
    return len(after) / len(before) if before else 1.0


#: PROVE's exact end-to-end forgery, and the shapes it generalises to. Each is
#: (patterns, committed, captured, culprit) with the SAME structure as the
#: driven case: raw texts that differ, a declaration that reconciles them, and
#: a residue far above the volume floor. The first entry is the drive verbatim.
_DISCRIMINATION_FORGERIES = [
    pytest.param(
        [r"== \d+ passed.*?==", r"\d+\.\d+s"],
        _BODY + "== 1650 passed, 4 skipped in 75.00s ==\n",
        _BODY + "== 1631 passed, 19 failed, 4 skipped in 78.11s ==\n",
        r"== \d+ passed.*?==",
        id="PROVE-driven-1650-passed-vs-19-failures",
    ),
    pytest.param(
        [r"\d+ passed", r"\d+\.\d+s"],
        _BODY + "== 1650 passed, 4 skipped in 75.00s ==\n",
        _BODY + "== 1631 passed, 4 skipped in 78.11s ==\n",
        r"\d+ passed",
        id="the-same-forgery-with-a-narrow-count-pattern",
    ),
    pytest.param(
        [r"\d+ \w+ in \d+\.\d+s"],
        _BODY + "===== 84 passed in 0.17s =====\n",
        _BODY + "===== 84 failed in 0.17s =====\n",
        r"\d+ \w+ in \d+\.\d+s",
        id="casting-4-liveness-tests-passed-flipped-to-failed",
    ),
    pytest.param(
        [r"summary: .*"],
        _BODY + "summary: the suite is green and nothing regressed\n",
        _BODY + "summary: the suite is red and four things regressed\n",
        r"summary: .*",
        id="a-whole-prose-summary-line-declared-volatile",
    ),
]


@pytest.mark.parametrize(
    "patterns,committed,captured,culprit", _DISCRIMINATION_FORGERIES
)
def test_a_redaction_that_erases_a_disagreement_is_refused(
    patterns, committed, captured, culprit
):
    """The D-135 bar: the comparator must REFUSE, and must name the pattern.

    An operator handed "the redaction annihilated your log" for a declaration
    that erased 0.6% of it would go looking in the wrong place. The refusal has
    to say WHICH pattern erased the disagreement and WHAT each side said there,
    because the fix is to narrow that one pattern.
    """
    with pytest.raises(ValueError) as exc:
        evidence._compare_byte_match(committed, captured, patterns)
    message = str(exc.value)
    assert "EVIDENCE_VOLATILE_MALFORMED" in message
    assert message.split(":", 1)[0] in evidence.KNOWN_EVIDENCE_FAILURE_TOKENS
    # repr() is how the house refusal quotes a pattern back at its author.
    assert repr(culprit) in message, f"the refusal names no culprit: {message}"


@pytest.mark.parametrize(
    "patterns,committed,captured,culprit", _DISCRIMINATION_FORGERIES
)
def test_the_residue_floor_could_not_have_caught_these(
    patterns, committed, captured, culprit
):
    """The falsifier for "D-126 already covered this".

    If any forgery above were refused by the composed residue floor, this block
    would be re-asserting a guarantee that already held and the discrimination
    guard would be untested scaffolding. Every one leaves residue far above the
    floor on BOTH sides — which is exactly why volume was the wrong question.
    """
    for label, text in (
        ("committed log", committed),
        ("re-execution capture", captured),
    ):
        redacted = evidence._apply_volatile_redaction(text, patterns)
        assert (
            evidence._composed_redaction_problem(label, text, redacted) is None
        ), f"{label} is caught by the volume floor, so it is not a D-135 witness"
        assert _residue_ratio(text, patterns) > 0.5, (
            f"{label} survives at {_residue_ratio(text, patterns):.1%}, close "
            f"enough to the floor that volume could plausibly be the refuser"
        )


def test_the_forgery_reaches_a_byte_match_without_the_discrimination_guard():
    """The negative control that makes the block above non-vacuous.

    Driven the way PROVE drove it: the two texts byte-match after redaction
    while their RAW sha256s differ — the precondition of a forgery. A gate
    probe is only meaningful when the forgery actually succeeds without the
    fix, which is the mistake PROVE reported making on its first attempt.
    """
    patterns = [r"== \d+ passed.*?==", r"\d+\.\d+s"]
    committed = _BODY + "== 1650 passed, 4 skipped in 75.00s ==\n"
    captured = _BODY + "== 1631 passed, 19 failed, 4 skipped in 78.11s ==\n"

    assert evidence._hash_str(committed) != evidence._hash_str(captured)
    assert evidence._apply_volatile_redaction(
        committed, patterns
    ) == evidence._apply_volatile_redaction(captured, patterns), (
        "the redaction no longer collapses the two texts, so this fixture has "
        "stopped being the forgery it is here to reproduce"
    )
    # And the pre-fix ladder — canary probe plus volume floor — clears it.
    for pattern in patterns:
        replacement = (
            evidence.TIMING_PLACEHOLDER
            if evidence.VOLATILE_PLACEHOLDER in pattern
            else evidence.VOLATILE_PLACEHOLDER
        )
        assert not evidence._pattern_redacts_everything(pattern, replacement)
    assert _residue_ratio(committed, patterns) > 0.9


@pytest.mark.parametrize("forged_side", ["committed", "captured"])
def test_both_sides_are_walked_for_erased_disagreements(forged_side):
    """DERIVED MEMBERSHIP over the sides, the axis D-126 established.

    An attacker picks which side carries the fabrication. A guard bound to the
    side its defect report happened to name leaves the other open, so the side
    is parametrized rather than assumed.
    """
    honest = _BODY + "== 1650 passed, 4 skipped in 75.00s ==\n"
    forged = _BODY + "== 1631 passed, 19 failed, 4 skipped in 78.11s ==\n"
    committed, captured = (
        (forged, honest) if forged_side == "committed" else (honest, forged)
    )
    with pytest.raises(ValueError) as exc:
        evidence._compare_byte_match(committed, captured, [r"== \d+ passed.*?=="])
    assert "EVIDENCE_VOLATILE_MALFORMED" in str(exc.value)


def test_a_third_side_is_reported_rather_than_silently_unwalked():
    """The unrecognised-member rung on the SIDES axis.

    ``_erased_disagreement_problem`` aligns a PAIR. If a side is ever added to
    the comparison, the guard must say so rather than truncate to the first two
    and leave the newcomer redacted-but-unchecked — which is the shape of every
    defect in this escalated class.
    """
    problem = evidence._erased_disagreement_problem(
        {"committed log": "a\n", "re-execution capture": "b\n", "third": "c\n"},
        [r"\d+\.\d+s"],
    )
    assert problem is not None
    assert "3 sides" in problem and "third" in problem


def test_a_pattern_firing_a_different_number_of_times_is_reported():
    """The unrecognised-member rung on the SPANS axis.

    Two spans lists of different lengths cannot be aligned, and ``zip`` would
    silently drop the tail — the exact "guard the members you remembered" shape
    this class keeps re-appearing as. The predicate is driven directly because
    ``_compare_byte_match`` only reaches it once the redacted texts are equal,
    and unequal match counts almost always break that equality first; the rung
    exists so the alignment cannot go wrong if they ever do not.
    """
    problem = evidence._erased_disagreement_problem(
        {
            "committed log": "worker pid=41 and pid=42 reported in\n",
            "re-execution capture": "worker pid=41 reported in\n",
        },
        [r"pid=\d+"],
    )
    assert problem is not None
    assert "2 time(s)" in problem and "1 time(s)" in problem
    assert "pid=" in problem


#: Decoys that match real text on their OWN lines and erase nothing
#: evidentiary. Deliberately non-overlapping with the forging pattern, so
#: declared order cannot be what makes the plant land.
_DECOY_PATTERNS = [r"rootdir: \S+", r"pid=\d+", r"20\d{2}-\d{2}-\d{2}"]


@pytest.mark.parametrize("index", range(len(_DECOY_PATTERNS) + 1))
def test_the_forging_pattern_is_caught_at_every_declared_index(index):
    """DERIVED MEMBERSHIP over the declared pattern list.

    ``range(len(_DECOY_PATTERNS) + 1)`` is the plant: adding a decoy adds a
    position automatically, so the sweep cannot silently stop tracking the list
    it is meant to be total over. A guard bound to the first declared pattern —
    or to the last — goes red here.
    """
    forging = r"summary: .*"
    patterns = _DECOY_PATTERNS[:index] + [forging] + _DECOY_PATTERNS[index:]
    committed = (
        _BODY + "rootdir: /a/b\npid=41\n2026-08-14\nsummary: green\n"
    )
    captured = (
        _BODY + "rootdir: /c/d\npid=52\n2026-09-01\nsummary: broken\n"
    )
    with pytest.raises(ValueError) as exc:
        evidence._compare_byte_match(committed, captured, patterns)
    assert repr(forging) in str(exc.value)


#: The words a forged span is planted into, one position at a time.
_SPAN_WORDS = ["alpha", "bravo", "charlie", "delta"]


@pytest.mark.parametrize("position", range(len(_SPAN_WORDS)))
def test_a_claim_word_is_caught_at_every_token_position_in_the_span(position):
    """DERIVED MEMBERSHIP over the tokens INSIDE an erased span.

    The disagreement is planted at each token offset in turn, so a guard that
    compared only the first token — or only the span as a whole — goes red.
    ``range(len(_SPAN_WORDS))`` tracks the fixture, so widening it widens the
    sweep rather than leaving the new offset unprobed.
    """
    forged = list(_SPAN_WORDS)
    forged[position] = "omega"
    with pytest.raises(ValueError) as exc:
        evidence._compare_byte_match(
            _BODY + f"summary: {' '.join(_SPAN_WORDS)}\n",
            _BODY + f"summary: {' '.join(forged)}\n",
            [r"summary: .*"],
        )
    message = str(exc.value)
    assert "EVIDENCE_VOLATILE_MALFORMED" in message
    assert "omega" in message and _SPAN_WORDS[position] in message


#: The narrowness floor: every shape of value a volatile field ACTUALLY takes,
#: each with two genuinely different values, each of which must still
#: reconcile. Derived from the cold drive of all 56 committed logs at d3820c5 —
#: durations, rootdir paths, uv build-dir paths, .planning roots, ms sizes —
#: plus the shapes agents/teammate.md tells authors to declare.
_NARROWNESS_CONTROLS = [
    pytest.param(
        [r"\d+\.\d+s"],
        _BODY + "==== 84 passed in 0.17s ====\n",
        _BODY + "==== 84 passed in 0.19s ====\n",
        id="a-duration",
    ),
    pytest.param(
        [r"rootdir: .*"],
        _BODY + "rootdir: /Users/ray/guild/plugins/foundry/mcp-server\n",
        _BODY + "rootdir: /tmp/wt/casting-3/plugins/foundry/mcp-server\n",
        id="a-rootdir-line-swallowed-whole",
    ),
    pytest.param(
        [r"platform \S+ -- Python \S+, pytest-\S+, pluggy-\S+ -- \S+"],
        _BODY + "platform darwin -- Python 3.14.6, pytest-9.1.1, pluggy-1.6.0 "
        "-- /u/.cache/uv/builds-v0/.tmphgnUSu/bin/python\n",
        _BODY + "platform darwin -- Python 3.14.6, pytest-9.1.1, pluggy-1.6.0 "
        "-- /u/.cache/uv/builds-v0/.tmpDURf54/bin/python\n",
        id="a-uv-build-dir-inside-a-whole-platform-line",
    ),
    pytest.param(
        [r"20\d{2}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}"],
        _BODY + "started 2026-08-14T10:23:45 ok\n",
        _BODY + "started 2026-09-01T04:11:02 ok\n",
        id="an-iso-timestamp",
    ),
    pytest.param(
        [r"pid=\d+"],
        _BODY + "worker pid=1234 up\n",
        _BODY + "worker pid=5678 up\n",
        id="a-pid",
    ),
    pytest.param(
        [r"Installed \d+ packages? in \d+(\.\d+)?ms"],
        _BODY + "Installed 5 packages in 12ms\n",
        _BODY + "Installed 5 packages in 15ms\n",
        id="a-millisecond-size",
    ),
    pytest.param(
        [r"/[^ ]*/\.planning/[^ ]*"],
        _BODY + "root /var/folders/kq/T/tmp.X6ktF5/wt/.planning/phases/09 x\n",
        _BODY + "root /private/tmp/scratch/clone/.planning/phases/09 x\n",
        id="a-planning-root",
    ),
    pytest.param(
        [r"\d+\.\d+s", r"completed in <VOLATILE>"],
        _BODY + "completed in 1.25s\n",
        _BODY + "completed in 9.90s\n",
        id="the-second-rung-of-the-placeholder-ladder",
    ),
]


@pytest.mark.parametrize("patterns,committed,captured", _NARROWNESS_CONTROLS)
def test_a_genuinely_varying_field_still_reconciles(patterns, committed, captured):
    """The false-positive floor. D-135's guard changes what the gate ACCEPTS,
    and a rule that refused these would break every pytest log in the corpus —
    a regression that would look like whatever the log happened to prove.

    The assertion that the two texts genuinely DIFFER is load-bearing: a
    control whose sides are identical exercises nothing, because the guard
    returns early when there is no disagreement to erase.
    """
    assert committed != captured, "this control reconciles nothing"
    matched, diff, _, _ = evidence._compare_byte_match(
        committed, captured, patterns
    )
    assert matched is True, diff


def test_the_discrimination_guard_does_not_over_correct_on_the_real_corpus():
    """The same floor DERIVED from the shipped corpus, so a log added tomorrow
    is covered the day it lands. Each log's own declared patterns are applied
    to its own body against itself — the guard must be inert where there is no
    disagreement to erase."""
    evidence_dir = REPO_ROOT / "evidence"
    logs = sorted(evidence_dir.glob("*.log")) if evidence_dir.exists() else []
    if not logs:
        pytest.skip(f"no committed evidence corpus at {evidence_dir}")

    refused = []
    for log in logs:
        text = log.read_text(encoding="utf-8")
        header = evidence._parse_evidence_header(text)
        body = evidence._strip_leading_header_block(text)
        try:
            evidence._compare_byte_match(body, body, header.get("volatile", []))
        except ValueError as exc:  # noqa: PERF203
            refused.append(f"{log.name}: {exc}")
    assert refused == [], f"the discrimination guard refuses real evidence: {refused}"


def test_a_forged_log_cannot_buy_a_pass_through_the_whole_verifier():
    """D-135 at the surface where it was exploitable, not at the helper.

    A real command, a body that differs from its output in exactly one line,
    and the single most ordinary volatile declaration a reviewer would wave
    through. Before the discrimination guard this produced
    ``verdict='accepted'`` with ``log_sha256 != captured_sha256``. Neither stub
    rung can be what saves it: the command is real and the body clears the
    128-byte TOO_SMALL floor.
    """
    import tempfile

    workdir = Path(tempfile.mkdtemp())
    (workdir / "evidence").mkdir()
    log = workdir / "evidence" / "casting-3-forged-summary.log"
    log.write_text(
        "# evidence-cmd: python3 -c \"print('collected 1650 items across the "
        "whole suite, which is padding so neither the stub library nor the "
        "volume floor can be what rejects this log'); print('== 1631 passed, "
        "19 failed, 4 skipped in 78.11s ==')\"\n"
        "# evidence-for: FR-017\n"
        "# evidence-volatile: == \\d+ passed.*?==\n"
        "\n"
        "collected 1650 items across the whole suite, which is padding so "
        "neither the stub library nor the volume floor can be what rejects "
        "this log\n"
        "== 1650 passed, 4 skipped in 75.00s ==\n",
        encoding="utf-8",
    )

    record = evidence._verify_one_evidence_file(
        evidence_path=log, worktree_path=workdir, casting_commit="0" * 40
    )

    assert record["verdict"] == "rejected", record
    assert record["failure_token"] == "EVIDENCE_VOLATILE_MALFORMED", record
    assert record["failure_token"] in evidence.KNOWN_EVIDENCE_FAILURE_TOKENS
    assert record["log_sha256"] != record["captured_sha256"], record
    assert repr(r"== \d+ passed.*?==") in record["failure_detail"], record


def test_an_honest_log_with_a_varying_duration_still_passes_the_whole_verifier():
    """The end-to-end counterpart. Without this, D-135's guard could have been
    'fixed' by refusing every declaration that reconciles anything at all — the
    over-correction that would take the whole corpus with it."""
    import tempfile

    workdir = Path(tempfile.mkdtemp())
    (workdir / "evidence").mkdir()
    log = workdir / "evidence" / "casting-3-honest-duration.log"
    log.write_text(
        "# evidence-cmd: python3 -c \"import os; print('every line of this "
        "body is byte-stable across runs except the duration below, which is "
        "why it is the one field declared volatile'); print('suite complete "
        "in %d.%02ds' % (len(os.sep), 42))\"\n"
        "# evidence-for: FR-017\n"
        "# evidence-volatile: \\d+\\.\\d+s\n"
        "\n"
        "every line of this body is byte-stable across runs except the "
        "duration below, which is why it is the one field declared volatile\n"
        "suite complete in 9.99s\n",
        encoding="utf-8",
    )

    record = evidence._verify_one_evidence_file(
        evidence_path=log, worktree_path=workdir, casting_commit="0" * 40
    )

    assert record["verdict"] == "accepted", record
    assert record["log_sha256"] != record["captured_sha256"], (
        "the duration did not actually vary, so this control proves nothing"
    )


# --------------------------------------------------------------------------- #
# D-143 — the discrimination rung's DENYLIST becomes an ALLOWLIST.
# (FR-017 / AC-023 / OT-011 — SECURITY-RELEVANT, A-AUTO-005.)
#
# D-135 shipped `_is_unstructured_token(t) = t.isalpha() or t.isdigit()` and
# refused when either side was true. That is a denylist of two shapes with
# ACCEPT as the default, so one non-alphanumeric character anywhere in the
# disagreeing token made a verdict read as a volatile field. PROVE drove 13
# forgery shapes and 8 were accepted end to end through the shipped
# `_verify_one_evidence_file` on a real git repo at a real casting commit —
# among them the process exit code and the failure count.
#
# The block below is the bar. Refusal is now the default and membership is
# derived from the corpus: cold-driven over all 65 committed logs at d8215c5
# from a clean worktree, every differing token pair in the entire corpus is a
# duration or an absolute filesystem path. Nothing else has ever varied, so
# nothing else is admitted.
# --------------------------------------------------------------------------- #

#: The shapes D-143 was filed on, plus the five the rung already held against.
#: Each is (patterns, committed, captured, culprit). Every one must REFUSE.
#: The three path variants are deliberate: PROVE reported the forgery as a
#: RELATIVE path (`out/1650/summary.json`), and a rule that refused only
#: relative paths would be bound to the spelling it was reported in — so the
#: absolute and word-shaped-segment spellings are driven beside it.
_D143_FORGERIES = [
    pytest.param(
        [r"coverage: [\d.]+%"],
        _BODY + "coverage: 95.2%\n", _BODY + "coverage: 41.0%\n",
        r"coverage: [\d.]+%", id="coverage-percentage",
    ),
    pytest.param(
        [r"exit=\d+"],
        _BODY + "exit=0\n", _BODY + "exit=1\n",
        r"exit=\d+", id="process-exit-code",
    ),
    pytest.param(
        [r"result [\d/]+"],
        _BODY + "result 1650/1650\n", _BODY + "result 1631/1650\n",
        r"result [\d/]+", id="pass-ratio",
    ),
    pytest.param(
        [r"failures: [\d/]+"],
        _BODY + "failures: 0/1650\n", _BODY + "failures: 19/1650\n",
        r"failures: [\d/]+", id="failure-ratio",
    ),
    pytest.param(
        [r"out/\d+/summary\.json"],
        _BODY + "out/1650/summary.json\n", _BODY + "out/1631/summary.json\n",
        r"out/\d+/summary\.json", id="count-inside-a-relative-path",
    ),
    pytest.param(
        [r"/tmp/out/\d+/summary\.json"],
        _BODY + "/tmp/out/1650/summary.json\n",
        _BODY + "/tmp/out/1631/summary.json\n",
        r"/tmp/out/\d+/summary\.json", id="count-inside-an-absolute-path",
    ),
    pytest.param(
        [r"/tmp/run-\d+/x"],
        _BODY + "/tmp/run-1650/x\n", _BODY + "/tmp/run-1631/x\n",
        r"/tmp/run-\d+/x", id="count-inside-a-word-shaped-path-segment",
    ),
    pytest.param(
        [r"rootdir: .*"],
        _BODY + "rootdir: /a/b passed=1650\n",
        _BODY + "rootdir: /c/d passed=1631\n",
        r"rootdir: .*", id="count-riding-a-rootdir-line",
    ),
    pytest.param(
        [r"status: [\w-]+"],
        _BODY + "status: all-passed\n", _BODY + "status: some-failed\n",
        r"status: [\w-]+", id="hyphenated-verdict-word",
    ),
    pytest.param(
        [r"platform \S+ -- \S+"],
        _BODY + "platform darwin -- run=passed-1650\n",
        _BODY + "platform darwin -- run=failed-1631\n",
        r"platform \S+ -- \S+", id="verdict-assigned-in-a-platform-line",
    ),
    # The five the denylist already held against — driven here too, because a
    # rewrite that closed the eight and dropped one of these is a regression.
    pytest.param(
        [r"\d+ passed"],
        _BODY + "== 1650 passed ==\n", _BODY + "== 1631 passed ==\n",
        r"\d+ passed", id="held-bare-integer",
    ),
    pytest.param(
        [r"\d+ \w+ in \d+\.\d+s"],
        _BODY + "===== 84 passed in 0.17s =====\n",
        _BODY + "===== 84 failed in 0.17s =====\n",
        r"\d+ \w+ in \d+\.\d+s", id="held-bare-word",
    ),
    pytest.param(
        [r"\d+ passed in \d+\.\d+s"],
        _BODY + "1650 passed in 4.86s\n", _BODY + "1631 passed in 4.91s\n",
        r"\d+ passed in \d+\.\d+s", id="held-count-adjacent-to-a-duration",
    ),
    pytest.param(
        [r"pid=\d+ \w+"],
        _BODY + "pid=41 green\n", _BODY + "pid=52 broken\n",
        r"pid=\d+ \w+", id="held-verdict-word-adjacent-to-a-pid",
    ),
    pytest.param(
        [r"Installed \d+ packages? in \d+(\.\d+)?ms"],
        _BODY + "Installed 42 packages in 137ms\n",
        _BODY + "Installed 5 packages in 137ms\n",
        r"Installed \d+ packages? in \d+(\.\d+)?ms",
        id="held-corpus-installed-packages-count",
    ),
]

#: The shapes the DEFECT names, by the id they carry above. This is the
#: "members_seen includes the sites the defect was filed on" rung: the sweep
#: below reports by name any shape D-143 was filed on that the table stopped
#: driving, so the table cannot quietly shrink back to the reported spelling.
_D143_SITES_FILED_ON = frozenset({
    "coverage-percentage",
    "process-exit-code",
    "pass-ratio",
    "failure-ratio",
    "count-inside-a-relative-path",
    "count-riding-a-rootdir-line",
    "hyphenated-verdict-word",
    "verdict-assigned-in-a-platform-line",
})  # 8 shapes accepted at cycle 20


def _d143_shape_ids() -> set:
    """The table's ids, read off the table itself rather than re-listed."""
    return {p.id for p in _D143_FORGERIES}


def test_the_d143_table_still_drives_every_shape_the_defect_was_filed_on():
    """members_seen vs offenders on the SHAPES axis.

    A sweep that stops covering a site it was filed on still reports a number
    and still passes its own tests — the exact way this run's escalated class
    hides. So the roster is compared against the defect's own list and the
    missing members are named.
    """
    members_seen = _d143_shape_ids()
    offenders = sorted(_D143_SITES_FILED_ON - members_seen)
    assert offenders == [], (
        f"the D-143 forgery table no longer drives {offenders}, which D-143 "
        f"was filed on. It drives {sorted(members_seen)}"
    )


@pytest.mark.parametrize(
    "patterns,committed,captured,culprit", _D143_FORGERIES
)
def test_a_forged_field_outside_the_allowlist_is_refused(
    patterns, committed, captured, culprit
):
    """The D-143 bar. Every shape refuses, and the refusal names the pattern
    whose narrowing is the fix."""
    with pytest.raises(ValueError) as exc:
        evidence._compare_byte_match(committed, captured, patterns)
    message = str(exc.value)
    assert "EVIDENCE_VOLATILE_MALFORMED" in message
    assert message.split(":", 1)[0] in evidence.KNOWN_EVIDENCE_FAILURE_TOKENS
    assert repr(culprit) in message, f"the refusal names no culprit: {message}"


@pytest.mark.parametrize(
    "patterns,committed,captured,culprit", _D143_FORGERIES
)
def test_neither_earlier_rung_could_have_caught_the_d143_shapes(
    patterns, committed, captured, culprit
):
    """The falsifier for "D-126 or the canary probe already covered this".

    PROVE was explicit that the volume floor never fired on any of these — the
    bodies are long and the residue sits far above 25%. If any shape were
    refused by an earlier rung, this block would be re-asserting a guarantee
    that already held and the allowlist would be untested scaffolding.
    """
    for label, text in (
        ("committed log", committed), ("re-execution capture", captured),
    ):
        redacted = evidence._apply_volatile_redaction(text, patterns)
        assert evidence._composed_redaction_problem(label, text, redacted) is None
        assert _residue_ratio(text, patterns) > 0.5
    for pattern in patterns:
        replacement = (
            evidence.TIMING_PLACEHOLDER
            if evidence.VOLATILE_PLACEHOLDER in pattern
            else evidence.VOLATILE_PLACEHOLDER
        )
        assert not evidence._pattern_redacts_everything(pattern, replacement)


@pytest.mark.parametrize(
    "patterns,committed,captured,culprit", _D143_FORGERIES
)
def test_each_d143_shape_really_did_buy_a_byte_match(
    patterns, committed, captured, culprit
):
    """The negative control that makes the block above non-vacuous.

    A forgery probe is only meaningful when the forgery actually succeeds
    without the fix: the raw texts must differ and the declared redaction must
    collapse them to one string. Without this, a typo'd fixture that never
    reconciled anything would "pass" the refusal test for the wrong reason.
    """
    assert evidence._hash_str(committed) != evidence._hash_str(captured)
    assert evidence._apply_volatile_redaction(
        committed, patterns
    ) == evidence._apply_volatile_redaction(captured, patterns), (
        "this fixture no longer reconciles, so it has stopped being a forgery"
    )


# --------------------------------------------------------------------------- #
# The registry's own derivation — every grammar carries a live witness.
# --------------------------------------------------------------------------- #

_TEAMMATE_PROTOCOL = REPO_ROOT / "plugins/foundry/agents/teammate.md"


def _corpus_witness_fields() -> set:
    """Every ``(key_context, token)`` the shipped corpus's declarations erase.

    Derived: each committed log's declared patterns applied to that log's own
    body, matches tokenized the way the guard tokenizes them, and each token
    paired with the span text PRECEDING it — which is exactly the pair
    ``_field_disagreement_problem`` hands the registry. This is what "the
    corpus exercises this grammar" means mechanically — no hand-copied list of
    which log proves what.

    D-156: the context half is the whole point. Before it, six grammars could
    share one witness because every one of them fullmatched the same `/x/y`
    token; the sweep confirmed a witness EXISTS without confirming the grammar
    was no WIDER than it. A `rootdir:` path and an interpreter path are the
    same token and different fields, and only the context tells them apart.
    """
    import re as _re

    fields = set()
    evidence_dir = REPO_ROOT / "evidence"
    for log in sorted(evidence_dir.glob("*.log")):
        text = log.read_text(encoding="utf-8")
        body = evidence._strip_leading_header_block(text)
        for pattern in evidence._parse_evidence_header(text).get("volatile", []):
            try:
                matches = list(_re.finditer(pattern, body))
            except _re.error:
                continue
            for match in matches:
                tokens = match.group(0).split()
                for index, token in enumerate(tokens):
                    fields.add((" ".join(tokens[:index]), token))
    return fields


def _grammar_witness_sweep() -> tuple:
    """Returns ``(members_seen, offenders)`` over the grammar registry.

    ``members_seen`` is read off ``_ENVIRONMENTAL_GRAMMARS`` itself, so a
    grammar added tomorrow is swept the day it lands rather than the day
    someone remembers to extend a list here. ``offenders`` are the grammars
    whose declared witness no longer exercises them — a dead grammar, which is
    how an allowlist silently widens.

    D-156 added the KEY to the corpus rung: a grammar that identifies its field
    by the text beside the token must find a committed span that carries that
    key, not merely a token of the right shape. Six of the seven entries
    fullmatch `/x/y`; without the key half, one rootdir line would witness them
    all and a grammar could be arbitrarily wider than the thing keeping it
    alive.
    """
    corpus_fields = _corpus_witness_fields()
    protocol_text = (
        _TEAMMATE_PROTOCOL.read_text(encoding="utf-8")
        if _TEAMMATE_PROTOCOL.exists()
        else ""
    )
    members_seen, offenders = set(), []
    for name, grammar in evidence._ENVIRONMENTAL_GRAMMARS.items():
        members_seen.add(name)
        if not grammar.token.fullmatch(grammar.sample):
            offenders.append(
                f"{name}: its own sample {grammar.sample!r} does not match it"
            )
            continue
        if grammar.witness_kind == "corpus":
            if not any(
                grammar.token.fullmatch(tok)
                and (grammar.key is None or grammar.key.search(context))
                for context, tok in corpus_fields
            ):
                offenders.append(
                    f"{name}: declares a corpus witness, but no committed "
                    f"evidence log's own patterns erase a token of this shape"
                    + (
                        f" under the key {grammar.key.pattern!r}"
                        if grammar.key is not None
                        else ""
                    )
                )
        elif grammar.witness_kind == "protocol":
            if grammar.witness not in protocol_text:
                offenders.append(
                    f"{name}: declares the protocol witness "
                    f"{grammar.witness!r}, which {_TEAMMATE_PROTOCOL.name} no "
                    f"longer ships"
                )
        else:
            offenders.append(
                f"{name}: witness_kind={grammar.witness_kind!r} is neither "
                f"'corpus' nor 'protocol', so nothing keeps it alive"
            )
    return members_seen, offenders


def test_every_declared_grammar_has_a_live_witness():
    """DERIVED MEMBERSHIP over the allowlist itself.

    An allowlist widens by accretion: a grammar goes in for a log that later
    changes, and nothing ever takes it out. Each entry names what keeps it
    alive — a committed log whose own declaration erases a token of that
    shape, or the literal `# evidence-volatile:` example agents/teammate.md
    ships to authors — and this sweep re-derives both from the tree.
    """
    if not (REPO_ROOT / "evidence").exists():
        pytest.skip("no committed evidence corpus")
    members_seen, offenders = _grammar_witness_sweep()
    assert members_seen == set(evidence._ENVIRONMENTAL_GRAMMARS), (
        "the sweep did not walk the whole registry"
    )
    assert offenders == [], f"grammars with no live witness: {offenders}"


def test_a_grammar_with_no_live_witness_is_reported_by_name(monkeypatch):
    """The plant: a NEW unbound member must turn this rule red.

    A witness sweep that cannot fail is decoration. A grammar is planted whose
    protocol witness exists nowhere in the tree; the sweep must name it rather
    than report a clean number over the members it happened to know.
    """
    if not (REPO_ROOT / "evidence").exists():
        pytest.skip("no committed evidence corpus")
    planted = dict(evidence._ENVIRONMENTAL_GRAMMARS)
    planted["build_number"] = evidence._EnvironmentalGrammar(
        token=re.compile(r"build#\d+"),
        varies_in="digits",
        key=None,
        witness_kind="protocol",
        witness="# evidence-volatile: build#[0-9]+  (never shipped)",
        witness_pair=("", "build#42", "build#43"),
        falsifier=("", "42", "43"),
        note="planted by the test; nothing in the tree witnesses it",
    )
    monkeypatch.setattr(evidence, "_ENVIRONMENTAL_GRAMMARS", planted)
    members_seen, offenders = _grammar_witness_sweep()
    assert "build_number" in members_seen, (
        "the sweep did not even walk the planted grammar"
    )
    assert any("build_number" in o for o in offenders), (
        f"a grammar with no witness went unreported: {offenders}"
    )


def test_every_grammar_declares_a_known_variation_site():
    """The `varies_in` axis is closed and total over the registry."""
    unknown = {
        name: g.varies_in
        for name, g in evidence._ENVIRONMENTAL_GRAMMARS.items()
        if g.varies_in not in evidence._KNOWN_VARIATION_SITES
    }
    assert unknown == {}, f"grammars with an unreadable variation site: {unknown}"


def test_an_unreadable_variation_site_is_reported_not_admitted(monkeypatch):
    """The unrecognised-member rung on the `varies_in` axis.

    If a grammar ever declares a direction of variation this module cannot
    read, the token must be REFUSED and the bad value named — never admitted
    because the shape matched.
    """
    planted = dict(evidence._ENVIRONMENTAL_GRAMMARS)
    planted["duration_seconds"] = evidence._EnvironmentalGrammar(
        token=re.compile(r"\d+\.\d+s"),
        varies_in="whenever",
        key=None,
        witness_kind="corpus",
        witness="casting-1-pytest.log",
        witness_pair=("", "4.86s", "4.91s"),
        falsifier=("", "4.86", "4.91"),
        note="planted",
    )
    monkeypatch.setattr(evidence, "_ENVIRONMENTAL_GRAMMARS", planted)
    grammar, note = evidence._environmental_field("4.86s", "4.91s")
    assert grammar is None, "an unreadable variation site admitted the token"
    assert "whenever" in note, note


#: Shapes no declared grammar recognises. None is a forgery in itself — the
#: point is the POLARITY: an unanticipated shape is refused and reported, where
#: the denylist would have waved every one of them through.
_UNRECOGNISED_SHAPES = [
    pytest.param("a1b2c3d4", "e5f6a7b8", id="a-content-hash"),
    pytest.param("1.2MB", "3.4MB", id="a-byte-size"),
    pytest.param("v1.2.3", "v1.2.4", id="a-semver"),
    pytest.param("port=8080", "port=9090", id="a-port-assignment"),
    pytest.param("host-01", "host-02", id="a-hyphenated-host"),
    pytest.param("out/1650/x", "out/1631/x", id="a-relative-path"),
]


@pytest.mark.parametrize("token_a,token_b", _UNRECOGNISED_SHAPES)
def test_an_unrecognised_shape_is_refused_by_default(token_a, token_b):
    """Refusal is the default (D-143's whole polarity change).

    Under `isalpha() or isdigit()` every shape here was ACCEPTED, because each
    carries a non-alphanumeric character or mixes cases. Under the allowlist
    each is refused and reported. A byte-size and a content hash are in this
    list on purpose: the brief anticipated both, no committed log varies
    either, so neither is admitted until something in the tree witnesses it.
    """
    grammar, _ = evidence._environmental_field(token_a, token_b)
    assert grammar is None, (
        f"{token_a!r}/{token_b!r} was admitted as {grammar!r} with nothing in "
        f"the tree witnessing that shape"
    )


#: D-156's three end-to-end forgeries, plus the residual the cycle-21 suite had
#: pinned as admitted. Under the `/\S*` grammar every one of these rode out of
#: the comparison; under the keyed grammars a bare path token is not a field.
_BARE_PATH_FORGERIES = [
    pytest.param("/logs/passed/run", "/logs/failed/run", id="verdict-word-in-a-segment"),
    pytest.param(
        "/var/run/failed/report.txt", "/var/run/passed/report.txt", id="d156-forgery-1"
    ),
    pytest.param("/FAILED", "/PASSED", id="d156-forgery-2"),
    pytest.param("/deadbeefcafe1234", "/0badc0de99887766", id="d156-forgery-3"),
    pytest.param("/logs/1650/run", "/logs/1631/run", id="count-in-a-segment"),
    pytest.param("/logs/run-1650", "/logs/run-1631", id="count-in-a-word-segment"),
]


@pytest.mark.parametrize("token_a,token_b", _BARE_PATH_FORGERIES)
def test_a_bare_path_token_is_not_a_field_whatever_it_carries(token_a, token_b):
    """D-156: what made the old residual a residual was the grammar, not the corpus.

    The cycle-21 suite pinned "a verdict word inside an absolute path segment
    is still admitted" as a known limit. PROVE-21 refuted the limit from the
    registry's own principle (what makes a pid environmental is that it is a
    pid, not that it has an `=` in it): the corpus declares only KEYED path
    variation, so a path is a field only where a key identifies it. A bare
    slash-token is therefore refused, and there is no residual left to pin.
    """
    grammar, why = evidence._environmental_field(token_a, token_b)
    assert grammar is None, (token_a, token_b, grammar)
    assert "starts with a slash" in why, why


# --------------------------------------------------------------------------- #
# ADJACENT PATHS (A-017/A-018). D-143 was reported on the COMMITTED side of a
# single declared pattern. These drive the two paths beside it: the forgery
# planted in the RE-EXECUTION CAPTURE, and the forging pattern at every
# declared index other than the reported one, behind decoys that must not fire.
# --------------------------------------------------------------------------- #

#: Decoys that erase real environmental fields on their own lines — one per
#: grammar family, so a rewrite that dropped a grammar goes red here rather
#: than in a fixture nobody re-reads. Each carries its OWN pair of lines, so
#: the per-decoy control below can name which grammar died; the combined body
#: is derived from the same triples rather than written out a second time.
_D143_DECOY_FIELDS = [
    (r"rootdir: \S+", "rootdir: /a/b\n", "rootdir: /c/d\n"),
    (r"pid=\d+", "pid=41\n", "pid=52\n"),
    (r"20\d{2}-\d{2}-\d{2}", "2026-08-14\n", "2026-09-01\n"),
    (r"\d+\.\d+s", "took 1.25s\n", "took 9.90s\n"),
]
_D143_DECOYS = [pattern for pattern, _, _ in _D143_DECOY_FIELDS]
_D143_DECOY_COMMITTED = "".join(a for _, a, _ in _D143_DECOY_FIELDS)
_D143_DECOY_CAPTURED = "".join(b for _, _, b in _D143_DECOY_FIELDS)


@pytest.mark.parametrize("forged_side", ["committed", "captured"])
@pytest.mark.parametrize("index", range(len(_D143_DECOYS) + 1))
def test_the_allowlist_holds_on_the_capture_side_at_every_declared_index(
    forged_side, index
):
    """The NAMED adjacent paths, distinct from the path D-143 was filed on.

    Adjacent path 1 — the CAPTURE side. An attacker picks which side carries
    the fabrication; D-143 was driven with the forgery in the committed log,
    so the mirror is driven here.

    Adjacent path 2 — a declared index OTHER than 0. D-143's drive used one
    declared pattern. Here the forging pattern is slid through every position
    in a list of four decoys, each of which erases a genuinely environmental
    field that must NOT be what refuses. `range(len(_D143_DECOYS) + 1)` tracks
    the decoy list, so adding a decoy adds a position automatically.

    The decoys double as a live check on the registry: `rootdir:` exercises
    absolute_path, `pid=` process_id, the date iso_timestamp and `1.25s`
    duration_seconds. Drop a grammar and these stop reconciling, so the
    refusal names a decoy instead of the forging pattern and this goes red.
    """
    forging = r"failures: [\d/]+"
    patterns = _D143_DECOYS[:index] + [forging] + _D143_DECOYS[index:]
    honest = _BODY + _D143_DECOY_COMMITTED + "failures: 0/1650\n"
    forged = _BODY + _D143_DECOY_CAPTURED + "failures: 19/1650\n"
    committed, captured = (
        (forged, honest) if forged_side == "committed" else (honest, forged)
    )
    with pytest.raises(ValueError) as exc:
        evidence._compare_byte_match(committed, captured, patterns)
    message = str(exc.value)
    assert "EVIDENCE_VOLATILE_MALFORMED" in message
    assert repr(forging) in message, (
        f"a decoy was blamed instead of the forging pattern: {message}"
    )


@pytest.mark.parametrize("pattern,line_a,line_b", _D143_DECOY_FIELDS)
def test_every_decoy_field_still_reconciles_on_its_own(pattern, line_a, line_b):
    """The false-positive floor for the decoys above.

    If any decoy stopped reconciling, the adjacent-path sweep would go red for
    the wrong reason and would stop proving what it claims. Each is driven
    against its OWN pair of lines, so the failure names the grammar that died
    rather than reporting that four fields differ.
    """
    matched, diff, _, _ = evidence._compare_byte_match(
        _BODY + line_a, _BODY + line_b, [pattern]
    )
    assert matched is True, f"{pattern!r} no longer reconciles: {diff}"


def test_a_forged_count_cannot_buy_a_pass_through_the_whole_verifier():
    """D-143 at the surface where PROVE drove it, not at the helper.

    PROVE's end-to-end drive: a committed log claiming `failures: 0/1650`
    against a command that really prints `failures: 19/1650`, declared volatile
    as `failures: [\\d/]+`. At cycle 20 this returned verdict='accepted' with
    failure_token=None and the two raw shas plainly different. Neither stub
    rung can be what saves it — the command is real and the body clears the
    128-byte floor.
    """
    import tempfile

    workdir = Path(tempfile.mkdtemp())
    (workdir / "evidence").mkdir()
    log = workdir / "evidence" / "casting-3-forged-failure-count.log"
    log.write_text(
        "# evidence-cmd: python3 -c \"print('collected 1650 items across the "
        "whole suite, which is padding so neither the stub library nor the "
        "volume floor can be what rejects this log'); print('failures: "
        "19/1650')\"\n"
        "# evidence-for: FR-017\n"
        "# evidence-volatile: failures: [\\d/]+\n"
        "\n"
        "collected 1650 items across the whole suite, which is padding so "
        "neither the stub library nor the volume floor can be what rejects "
        "this log\n"
        "failures: 0/1650\n",
        encoding="utf-8",
    )

    record = evidence._verify_one_evidence_file(
        evidence_path=log, worktree_path=workdir, casting_commit="0" * 40
    )

    assert record["verdict"] == "rejected", record
    assert record["failure_token"] == "EVIDENCE_VOLATILE_MALFORMED", record
    assert record["failure_token"] in evidence.KNOWN_EVIDENCE_FAILURE_TOKENS
    assert record["log_sha256"] != record["captured_sha256"], record
    assert "0/1650" in record["failure_detail"], record
    assert "19/1650" in record["failure_detail"], record


# --------------------------------------------------------------------------- #
# D-149 — stdout IS the protocol channel (FR-017 / AC-023 / OT-011).
#
# `foundry_accept_casting` ended its evidence block with a bare
# `print(..., flush=True)` of the verdict tally. This server speaks JSON-RPC
# over stdio, so that line lands INSIDE the channel, ahead of the response
# frame, and a conforming client's parser fails on the message. The only way to
# reach it was to pass `casting_commit` — the exact path FR-017 exists to make
# reachable over MCP. Wiring the evidence gate would have broken the channel the
# first time it fired.
#
# The tally now travels in the returned dict. The guard below is derived over
# the whole installed package rather than bound to the one site that was
# reported, because "the handler that happened to print" is not the class —
# "a handler that prints" is.
# --------------------------------------------------------------------------- #

_SERVER_PKG = REPO_ROOT / "plugins/foundry/mcp-server/src/foundry_mcp"


def _package_modules(root: Path) -> list:
    """Every source module in the package tree rooted at ``root``.

    Membership is derived on BOTH axes — the files in a directory and the
    directories in the package — so neither a new module nor a new subpackage
    has to be remembered anywhere. Same derivation the D-137 family uses in
    ``test_orchestrator_gates.py``; that file's ``_scan`` docstring records the
    ``(seen, offenders)`` tuple as the agreed contract ACROSS test modules, so
    the shape is re-declared here rather than imported across test files.
    """
    return sorted(root.rglob("*.py"))


def _scan(modules: list, rule) -> tuple:
    """Run one ``(seen, offenders)`` rule over a corpus and union both halves.

    D-142's shape. ``seen`` names every site the rule's recogniser identified
    as a MEMBER of the class it polices — offending or not — because
    ``assert not offenders`` is green in two different worlds: the one where
    the corpus is clean, and the one where the derivation has quietly stopped
    recognising the corpus's spelling. Callers assert against ``seen`` to tell
    the two apart by name. ``test_orchestrator_gates`` records this tuple as
    the agreed contract across test modules.
    """
    seen: list = []
    offenders: list = []
    for path in modules:
        module_seen, module_offenders = rule(path)
        seen.extend(module_seen)
        offenders.extend(module_offenders)
    return sorted(set(seen)), sorted(set(offenders))


def _module_name(path: Path) -> str:
    rel = path.relative_to(_SERVER_PKG.parent).with_suffix("")
    parts = [p for p in rel.parts]
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _stdout_objects() -> tuple:
    """The objects that ARE the process's stdout, read off ``sys`` itself."""
    return (sys.stdout, sys.__stdout__)


def _stderr_objects() -> tuple:
    return (sys.stderr, sys.__stderr__)


def _resolves_to_stdout(node: ast.AST, namespace: dict) -> bool:
    """True when ``node`` names the real stdout under ANY spelling.

    D-158: the earlier recogniser matched the literal ``sys.`` — the same bug
    D-147 fixed in the decode rule one packet earlier. Now the expression is
    RESOLVED through the module's namespace (the decode rule's own resolver),
    so ``sys.stdout``, ``s.stdout`` under ``import sys as s``, a bare
    ``stdout`` under ``from sys import stdout``, ``getattr(sys, "stdout")``
    and ``sys.__stdout__`` all land on the same object — while a
    ``subprocess`` result's ``.stdout`` resolves to nothing and stays quiet,
    which is the false-positive family the literal was protecting against.
    """
    from tests.test_orchestrator_gates import _UNRESOLVED, _resolve_dotted

    if isinstance(node, ast.Call):
        callee, _ = _resolve_dotted(node.func, namespace)
        if callee is getattr and len(node.args) >= 2:
            owner, _ = _resolve_dotted(node.args[0], namespace)
            attr = node.args[1]
            if owner is sys and isinstance(attr, ast.Constant):
                return any(
                    getattr(sys, str(attr.value), None) is o for o in _stdout_objects()
                )
        return False
    if isinstance(node, (ast.Name, ast.Attribute)):
        obj, _ = _resolve_dotted(node, namespace)
        return obj is not _UNRESOLVED and any(obj is o for o in _stdout_objects())
    return False


def _writes_to_stdout(node: ast.AST, namespace: dict) -> bool:
    """True when this expression writes to the process's real stdout.

    Every spelling is decided by RESOLUTION, not by matching text: a
    ``print()`` whose ``file=`` is absent or resolves to anything other than
    stderr; any expression resolving to the stdout object (see
    ``_resolves_to_stdout``); ``os.write`` / ``os.fdopen`` on descriptor 1,
    resolved through the namespace so ``import os as o`` is the same call.
    A ``logging.StreamHandler(<stdout expr>)`` is caught by its argument.
    """
    from tests.test_orchestrator_gates import _UNRESOLVED, _resolve_dotted

    if isinstance(node, ast.Call):
        callee, _ = _resolve_dotted(node.func, namespace)
        if callee is print:
            target = next((k.value for k in node.keywords if k.arg == "file"), None)
            if target is None:
                return True
            obj, _ = _resolve_dotted(target, namespace)
            return not (obj is not _UNRESOLVED and any(obj is e for e in _stderr_objects()))
        if callee in (os.write, os.fdopen) and node.args:
            fd = node.args[0]
            if isinstance(fd, ast.Constant) and fd.value == 1:
                return True
            # ``os.write(sys.stdout.fileno(), ...)`` — the descriptor is
            # derived from the stdout object somewhere inside the argument.
            return any(_resolves_to_stdout(n, namespace) for n in ast.walk(fd))
        return _resolves_to_stdout(node, namespace)
    return _resolves_to_stdout(node, namespace)


def _loud_functions(path: Path) -> set:
    """Every function in ``path`` that can reach a stdout write.

    Transitive, over the module's own call graph: the four prints in the CLI
    validator all sit in one function that nothing imports, and it is entered
    only through ``main``. A rule that stopped at the function CONTAINING the
    write would call that function unreachable and clear it for the wrong
    reason, then miss the same shape in a handler's private helper.

    ``"<module>"`` stands for module-level statements, which run on import.
    """
    from tests.test_orchestrator_gates import _module_namespace

    tree = ast.parse(path.read_text(encoding="utf-8"))
    namespace = _module_namespace(path, tree)
    functions, calls, loud = {}, {}, set()
    for fn in ast.walk(tree):
        if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            functions[fn.name] = fn
            body = list(ast.walk(fn))
            calls[fn.name] = {
                (c.func.id if isinstance(c.func, ast.Name) else c.func.attr)
                for c in body
                if isinstance(c, ast.Call)
                and isinstance(c.func, (ast.Name, ast.Attribute))
            }
            if any(_writes_to_stdout(n, namespace) for n in body):
                loud.add(fn.name)
    nested = {
        n.name
        for fn in functions.values()
        for n in ast.walk(fn)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        and n.name != fn.name
    }
    top_level = [
        n for n in tree.body
        if not isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    ]
    if any(_writes_to_stdout(x, namespace) for n in top_level for x in ast.walk(n)):
        loud.add("<module>")
    # Backward closure: a caller of a loud function is loud.
    changed = True
    while changed:
        changed = False
        for name, callees in calls.items():
            if name not in loud and callees & loud:
                loud.add(name)
                changed = True
    return loud - (nested - set(functions))


def _redirect_guarded(fn: ast.AST) -> bool:
    """True when this function wraps its work in ``redirect_stdout``."""
    return any(
        isinstance(n, ast.Call)
        and (
            (isinstance(n.func, ast.Name) and n.func.id == "redirect_stdout")
            or (isinstance(n.func, ast.Attribute) and n.func.attr == "redirect_stdout")
        )
        for n in ast.walk(fn)
    )


def _protocol_stdout_scan() -> tuple:
    """``(seen, offenders)`` over the whole installed package.

    ``seen`` names every loud function found anywhere in the package, CLI
    scripts included — it is what tells a clean run apart from a blind one. A
    scan that reports zero loud functions is not passing; the recogniser has
    stopped recognising a print, and the caller asserts against ``seen`` to
    tell the two apart by name.

    ``offenders`` are the loud functions a client can actually reach over the
    protocol. Reachability is DERIVED, not typed: the import graph rooted at
    ``foundry_mcp.server`` decides which modules are on the channel, and for
    each such module every name a protocol-path module imports from it is
    checked — a loud entry name offends unless every one of its import sites
    sits in a function that redirects stdout. No directory is exempt by
    NAME. The CLI validator clears this because its one entry point, ``main``,
    is imported solely inside ``intent_coverage._call_validator_in_process``,
    which redirects; delete that redirect and it becomes an offender.
    """
    modules = {_module_name(p): p for p in _package_modules(_SERVER_PKG)}
    trees = {n: ast.parse(p.read_text(encoding="utf-8")) for n, p in modules.items()}

    def imports_of(name):
        out = set()
        for n in ast.walk(trees[name]):
            if isinstance(n, ast.Import):
                out |= {a.name for a in n.names}
            elif isinstance(n, ast.ImportFrom) and n.module:
                out.add(n.module)
                out |= {f"{n.module}.{a.name}" for a in n.names}
        return {m for m in out if m in modules}

    reach, stack = set(), ["foundry_mcp.server"]
    while stack:
        m = stack.pop()
        if m in reach or m not in modules:
            continue
        reach.add(m)
        stack.extend(imports_of(m))

    loud = {n: _loud_functions(modules[n]) for n in modules}
    seen = sorted(f"{n}#{f}" for n, fns in loud.items() for f in fns)

    offenders = []
    for name in sorted(reach):
        for fn_name in sorted(loud[name]):
            if fn_name == "<module>":
                offenders.append(f"{name}#<module> prints at import time")
                continue
            sites = []
            for importer in sorted(reach):
                for node in ast.walk(trees[importer]):
                    if isinstance(node, ast.ImportFrom) and node.module == name:
                        if any(a.name == fn_name for a in node.names):
                            sites.append((importer, node))
                    elif isinstance(node, ast.Import) and any(
                        a.name == name for a in node.names
                    ):
                        sites.append((importer, node))
            if not sites:
                continue  # no protocol path reaches this name
            for importer, node in sites:
                enclosing = [
                    f for f in ast.walk(trees[importer])
                    if isinstance(f, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and any(n is node for n in ast.walk(f))
                ]
                if not any(_redirect_guarded(f) for f in enclosing):
                    offenders.append(
                        f"{name}#{fn_name} reaches stdout and is imported by "
                        f"{importer} without a redirect_stdout"
                    )
                    break
    return seen, sorted(set(offenders))


def test_no_handler_reachable_over_the_protocol_writes_to_stdout():
    """D-149, derived over the installed package rather than the reported site.

    The anchors are what keep this honest. ``seen`` must be non-empty, because
    the package really does contain a loud function (the CLI validator) — if
    the print-recogniser ever breaks, ``seen`` empties and ``offenders == []``
    would go green over a corpus it can no longer read. And the module corpus
    must contain both the defect's own module and ``server.py``, so a
    ``_package_modules`` that stopped reaching them fails BY NAME instead of
    reporting a clean zero.
    """
    scanned = {_module_name(p) for p in _package_modules(_SERVER_PKG)}
    for anchor in ("foundry_mcp.tools.foundry_handoff", "foundry_mcp.server"):
        assert anchor in scanned, (
            f"the scan no longer reaches {anchor}, so a print there would be "
            f"invisible — this is the derivation going blind"
        )
    seen, offenders = _protocol_stdout_scan()
    assert seen, (
        "the scan found no stdout writer anywhere in the package, including "
        "the CLI validator that certainly has four — the recogniser is blind"
    )
    assert offenders == [], (
        f"stdout is the JSON-RPC channel; these reach it: {offenders}"
    )


def test_a_new_handler_that_prints_is_reported_by_name(tmp_path, monkeypatch):
    """The plant: a NEW unbound member must turn this rule red.

    A brand-new module, in a brand-new subpackage, imported by the server
    without a redirect. Nothing about it is on any list this test maintains —
    if the scan only knew the modules that exist today, this stays green and
    the rule is decoration.
    """
    pkg = tmp_path / "foundry_mcp"
    (pkg / "handlers").mkdir(parents=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "handlers" / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "handlers" / "planted.py").write_text(
        "def handle_thing():\n"
        "    print('tally: 3 accepted')\n"
        "    return {'ok': True}\n",
        encoding="utf-8",
    )
    (pkg / "server.py").write_text(
        "from foundry_mcp.handlers.planted import handle_thing\n"
        "_DISPATCH = {'Thing': lambda args: handle_thing()}\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        sys.modules[__name__], "_SERVER_PKG", pkg, raising=False
    )
    seen, offenders = _protocol_stdout_scan()
    assert "foundry_mcp.handlers.planted#handle_thing" in seen, seen
    assert any("planted" in o for o in offenders), (
        f"a printing handler in a new subpackage went unreported: {offenders}"
    )


def test_a_redirected_cli_entry_point_is_not_an_offender(tmp_path, monkeypatch):
    """The narrowness control for the plant above.

    The rule must not simply refuse every print in the package — the CLI
    validator's four are legitimate and are cleared by the redirect at its one
    protocol entry. Driven on a synthetic pair so the clearing is shown to come
    from the redirect and not from the module's path.
    """
    pkg = tmp_path / "foundry_mcp"
    (pkg / "scripts").mkdir(parents=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "scripts" / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "scripts" / "cli.py").write_text(
        "def report():\n    print('human readable')\n\n"
        "def main(argv):\n    report()\n    return 0\n",
        encoding="utf-8",
    )
    (pkg / "server.py").write_text(
        "import contextlib, io\n"
        "def run():\n"
        "    from foundry_mcp.scripts.cli import main\n"
        "    buf = io.StringIO()\n"
        "    with contextlib.redirect_stdout(buf):\n"
        "        return main([])\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        sys.modules[__name__], "_SERVER_PKG", pkg, raising=False
    )
    seen, offenders = _protocol_stdout_scan()
    assert "foundry_mcp.scripts.cli#report" in seen, seen
    assert "foundry_mcp.scripts.cli#main" in seen, (
        "loudness did not propagate to the entry point that calls report()"
    )
    assert offenders == [], (
        f"a redirect-guarded CLI entry point was wrongly flagged: {offenders}"
    )


def test_a_function_level_import_without_a_redirect_is_reported(tmp_path, monkeypatch):
    """D-154: the anchor for the rule's one clearing predicate.

    ``_redirect_guarded`` is the whole difference between "cleared" and "not
    looked at" for a loud name imported inside a function. The module-level
    plant never consults it (``enclosing`` is empty there), so a predicate
    blinded toward acceptance left the suite green. This plant imports the
    loud name INSIDE a function that does NOT redirect — the exact mirror of
    the control above — and must be reported by name.
    """
    pkg = tmp_path / "foundry_mcp"
    (pkg / "scripts").mkdir(parents=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "scripts" / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "scripts" / "cli.py").write_text(
        "def report():\n    print('human readable')\n\n"
        "def main(argv):\n    report()\n    return 0\n",
        encoding="utf-8",
    )
    (pkg / "server.py").write_text(
        "def run():\n"
        "    from foundry_mcp.scripts.cli import main\n"
        "    return main([])\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(sys.modules[__name__], "_SERVER_PKG", pkg, raising=False)
    seen, offenders = _protocol_stdout_scan()
    assert "foundry_mcp.scripts.cli#main" in seen, seen
    assert any("cli#main" in o and "without a redirect_stdout" in o for o in offenders), (
        f"a function-level import with no redirect went unreported: {offenders}"
    )


#: D-158's invisible spellings, each a way of reaching the real stdout that a
#: literal ``sys.`` match cannot see, plus the subprocess control that must
#: stay quiet. Every one is decided by resolving the expression, not by text.
_STDOUT_SPELLINGS = [
    pytest.param("import os\ndef f():\n    os.write(1, b'x')\n", True, id="os-write-fd-1"),
    pytest.param(
        "from sys import stdout\ndef f():\n    stdout.write('x')\n", True, id="from-sys-import"
    ),
    pytest.param(
        "import sys as s\ndef f():\n    s.stdout.write('x')\n", True, id="import-sys-as"
    ),
    pytest.param(
        "import sys\ndef f():\n    getattr(sys, 'stdout').write('x')\n", True, id="getattr"
    ),
    pytest.param(
        "import os, logging\ndef f():\n    logging.StreamHandler(os.fdopen(1, 'w'))\n",
        True, id="fdopen-1",
    ),
    pytest.param(
        "import os, sys\ndef f():\n    os.write(sys.stdout.fileno(), b'x')\n",
        True, id="os-write-fileno",
    ),
    pytest.param(
        "import sys\ndef f():\n    print('x', file=sys.stderr)\n", False, id="print-to-stderr"
    ),
    pytest.param(
        "import subprocess\ndef f():\n    r = subprocess.run(['x'], capture_output=True)\n"
        "    return r.stdout\n",
        False, id="subprocess-result-stdout-is-quiet",
    ),
    pytest.param("import logging\ndef f():\n    logging.StreamHandler()\n", False, id="stream-handler-default-stderr"),
]


@pytest.mark.parametrize("source,loud", _STDOUT_SPELLINGS)
def test_the_stdout_rule_resolves_the_spelling_rather_than_matching_it(tmp_path, source, loud):
    """D-158: what reaches stdout is decided by what the name RESOLVES to."""
    module = tmp_path / "planted.py"
    module.write_text(source, encoding="utf-8")
    assert ("f" in _loud_functions(module)) is loud, (source, _loud_functions(module))


def test_the_dispatched_evidence_path_emits_nothing_on_stdout(tmp_path, capsys):
    """D-149 at the surface a client actually meets.

    Driven through ``server._DISPATCH["Foundry-Accept-Casting"]`` — the
    dispatcher a real MCP client reaches — WITH ``casting_commit``, which is the
    only way into the tally block and the path FR-017 makes reachable. Before
    the fix this emitted one `Foundry-Accept-Casting: casting 1 — evidence
    verdicts: ...` line into the JSON-RPC channel.

    The tally is asserted present in the RETURN, so "no stdout" cannot be
    satisfied by deleting the information instead of moving it.
    """
    from foundry_mcp import server
    from foundry_mcp.tools.foundry_state import clear_active_run

    env = _build_divergent_spec_repo(tmp_path)
    server_project_root = server._project_root
    server._project_root = str(env["project_root"])
    capsys.readouterr()
    try:
        result = server._DISPATCH["Foundry-Accept-Casting"]({
            "casting_id": 1,
            "spec_hash": env["spec_hash"],
            "prompt_hash": env["prompt_hash"],
            "completion_report": (
                "AC-023 implemented at src/gate.py#accept_casting\n"
            ),
            "casting_commit": env["casting_commit"],
        })
    finally:
        server._project_root = server_project_root
        clear_active_run()
    captured = capsys.readouterr()

    assert captured.out == "", (
        f"the handler wrote {captured.out!r} into the JSON-RPC channel"
    )
    # Non-vacuity: the tally block really was reached on this call.
    assert result["evidence_verdict"] == "accepted", result
    assert result["evidence_tally"] == {
        "accepted": 1, "rejected": 0, "failure_tokens": [],
    }, result


def test_the_adjacent_dispatch_paths_also_emit_nothing_on_stdout(tmp_path, capsys):
    """The NAMED adjacent paths for D-149.

    Adjacent path 1 — the SAME handler WITHOUT ``casting_commit``, which skips
    the evidence block entirely and returns through a different branch.

    Adjacent path 2 — a DIFFERENT tool through the same dispatch table
    (``Foundry-Context``), so the guarantee is shown to be a property of the
    channel rather than of the one handler the defect was filed on.
    """
    from foundry_mcp import server
    from foundry_mcp.tools.foundry_state import clear_active_run

    env = _build_divergent_spec_repo(tmp_path)
    server_project_root = server._project_root
    server._project_root = str(env["project_root"])
    capsys.readouterr()
    try:
        no_commit = server._DISPATCH["Foundry-Accept-Casting"]({
            "casting_id": 1,
            "spec_hash": env["spec_hash"],
            "prompt_hash": env["prompt_hash"],
            "completion_report": (
                "AC-023 implemented at src/gate.py#accept_casting\n"
            ),
        })
        other_tool = server._DISPATCH["Foundry-Context"]({})
    finally:
        server._project_root = server_project_root
        clear_active_run()
    captured = capsys.readouterr()

    assert captured.out == "", (
        f"an adjacent dispatch path wrote {captured.out!r} to stdout"
    )
    assert no_commit["evidence_verdict"] is None, no_commit
    assert no_commit["evidence_tally"] is None, no_commit
    assert isinstance(other_tool, dict), other_tool


# --------------------------------------------------------------------------- #
# D-150 — the requirement-ID grammar, declared once (FR-017 / AC-023).
#
# The literal `\b(?:US|FR|NFR|AC|VC|IR|TR)-\d+(?:\.\d+)?\b` was hand-typed in
# SEVEN places across five modules, and every wide copy knew the same seven
# families and neither OT- nor GI-. Fifteen of this run's own 71 spec IDs were
# invisible to the acceptance gate, the DONE gate's requirement count and the
# verdict-coverage synthesis -- and `# evidence-for: OT-011` parsed to the
# EMPTY LIST, so no evidence could ever bind to an observable truth.
# --------------------------------------------------------------------------- #

_ID_LITERAL_MARKER = "-\\d"


def _enumerated_prefixes(text: str) -> frozenset:
    """The requirement-ID families a string constant enumerates.

    Derived against ``REQUIREMENT_ID_PREFIXES`` rather than a list written
    here, so a family added to the vocabulary is recognised by this scan the
    day it lands.
    """
    return frozenset(
        p for p in vocab.REQUIREMENT_ID_PREFIXES
        if re.search(rf"(?<![A-Z]){re.escape(p)}(?![A-Z])", text)
    )


def _requirement_id_literals(path: Path) -> tuple:
    """``(seen, offenders)`` for one module.

    A MEMBER is a string constant that enumerates two or more requirement-ID
    families AND carries the regex marker ``-\\d`` -- the pair is what
    separates a grammar from prose that happens to mention ``FR`` and ``AC``.

    A member is WAIVED when the same module also declares a constant anchored
    to a LITERAL marker (``^`` or ``\\A`` followed by ordinary text, not a
    wildcard) enumerating exactly the same families. That is the structural
    signature of a selector bounded by a declared input FORMAT rather than one
    scanning free prose: ``test_deriver`` reads the ``# tests-spec: US-1, FR-2``
    header its own ``^#\\s*tests-spec:`` regex defines, and narrowing to US/FR
    there is the format, not a forgotten copy.

    THE LITERAL REQUIREMENT IS LOAD-BEARING. A first cut waived on ``^``
    alone, and ``parsers/spec.py`` slipped through: its ``^.*?\\b(...)``
    matches ANY line containing an ID, so it is a prose scanner wearing an
    anchor. An anchor followed by ``.`` or ``(`` constrains nothing, and a
    waiver that accepts one excuses exactly the sites this rule exists to
    find.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    constants = [
        n.value for n in ast.walk(tree)
        if isinstance(n, ast.Constant) and isinstance(n.value, str)
    ]
    anchored = set()
    for const in constants:
        for anchor in ("^", "\\A"):
            if const.startswith(anchor):
                rest = const[len(anchor):]
                if rest[:1] and rest[0] not in ".([\\":
                    anchored.add(_enumerated_prefixes(const))
    seen, offenders = [], []
    for const in constants:
        families = _enumerated_prefixes(const)
        if len(families) < 2 or _ID_LITERAL_MARKER not in const:
            continue
        label = f"{path.name}#{'|'.join(sorted(families))}"
        seen.append(label)
        if families not in anchored:
            offenders.append(label)
    return seen, offenders


def test_no_module_declares_its_own_requirement_id_grammar():
    """DERIVED MEMBERSHIP over the package, on the axis D-150 was filed on.

    The anchors are load-bearing. ``seen`` must be non-empty, because the
    package still legitimately contains one format-bounded selector -- if the
    recogniser breaks, ``seen`` empties and ``offenders == []`` goes green over
    a corpus it can no longer read. And every module that carried one of the
    seven copies must still be SCANNED, so a ``_package_modules`` that stopped
    reaching them fails by name rather than reporting a clean zero.
    """
    # D-155: the roots are the SHARED derivation every package-wide rule uses,
    # not a package-only literal. `plugins/foundry/scripts/` is a shipped
    # tree the package cannot import; `_scanned_modules` parses it from disk,
    # and `validate-test-observations.py` was the copy this rule could not
    # see while it started from `_SERVER_PKG` alone.
    from tests.test_spawn_progress import _scanned_modules

    filed_on = [
        "foundry_handoff.py", "evidence.py", "foundry_validate.py",
        "foundry_orchestrator.py", "foundry.py", "vocab.py",
        "validate-test-observations.py",
    ]
    modules = _scanned_modules()
    names = {p.name for p in modules}
    missing = [m for m in filed_on if m not in names]
    assert missing == [], f"the scan no longer reaches {missing}"

    seen, offenders = _scan(modules, _requirement_id_literals)
    assert seen, (
        "the scan found no requirement-ID grammar anywhere in the package, "
        "though test_deriver still declares a format-bounded one -- blind"
    )

    # This casting's key_files plus the lead's vocab.py grant. Splitting on
    # ownership is not a softening of the rule — it is what lets the rule ship
    # strict while other castings land their own halves.
    owned = {
        "foundry_handoff.py", "evidence.py", "citation.py", "foundry.py",
        "vocab.py",
    }
    mine = [o for o in offenders if o.split("#")[0] in owned]
    assert mine == [], (
        f"these files of MINE declare their own requirement-ID grammar instead "
        f"of importing REQUIREMENT_ID_RE from foundry_mcp.schemas.vocab: {mine}"
    )

    # Offenders outside this casting's grant, pinned so the backlog cannot grow
    # in silence. `parsers/spec.py` was found BY this rule after the waiver was
    # tightened: `extract_requirements` and its sibling both carried the wide
    # seven-family literal, so `citation.verify_citations`' traceability matrix
    # could not see an OT-, GI-, CT-, ST- or LR- requirement either. Routed to
    # casting 2 under a lead grant and closed there, so the backlog is EMPTY --
    # which is the state this pin exists to hold, not a softening of it: a new
    # unowned grammar re-fails this assertion by name the day it is written.
    pending_unowned: set[str] = set()
    theirs = {o for o in offenders if o.split("#")[0] not in owned}
    assert theirs == pending_unowned, (
        f"the set of requirement-ID grammars outside this casting's grant "
        f"changed: now {sorted(theirs)}, pinned {sorted(pending_unowned)}. A "
        f"NEW one must be routed, not absorbed."
    )


def test_the_canonical_grammar_never_narrows_what_was_counted_before():
    """NFR-002 at the seam every consumer now shares.

    The pre-D-150 literal is reproduced here EXACTLY as it stood in all five
    wide copies. The canonical pattern must be a strict superset of it: every
    ID any consumer counted, cited or bound before this change must still be
    counted, cited and bound. Written against the exported pattern, so
    casting 2's swap of foundry_validate and foundry_orchestrator is checked by
    this test too -- neither of us can narrow the shared seam alone.
    """
    old = re.compile(r"\b(?:US|FR|NFR|AC|VC|IR|TR)-\d+(?:\.\d+)?\b")
    corpus = (
        "US-1 FR-2 NFR-3 AC-4 VC-5 IR-6 TR-7 FR-2.1 "
        "OT-011 GI-001 CT-002 ST-004 "
        "A-AUTO-003 FLAG-001 TEST-01 EVID-01 D-150 P-6"
    )
    before, after = set(old.findall(corpus)), set(
        vocab.REQUIREMENT_ID_RE.findall(corpus)
    )
    assert before <= after, f"the canonical grammar NARROWED: lost {before - after}"
    assert {"OT-011", "GI-001", "CT-002", "ST-004"} <= after, (
        f"the families D-150 was filed on are still not matched: {after}"
    )
    # ...and it did not widen into namespaces that are not requirements.
    for foreign in ("A-AUTO-003", "FLAG-001", "TEST-01", "EVID-01", "D-150", "P-6"):
        assert foreign not in after, (
            f"{foreign} is not a requirement -- counting it would inflate every "
            f"coverage denominator in the run"
        )


def test_every_id_prefix_in_a_real_spec_is_classified():
    """A new ID family must be REPORTED, never silently dropped.

    Scans a spec corpus for ``XX-NNN``-shaped tokens and partitions the
    prefixes across the two declared sets. A prefix in NEITHER is the failure
    this exists to surface: it is exactly how OT- and GI- went missing for the
    whole of this run, matching nothing and complaining about nothing.

    D-162: the corpus is COMMITTED, so this never skips. ``forge-specs/`` is
    git-ignored (``.gitignore``), so a guard that read only the real specs was
    switched off in every clean checkout — including the worktree the evidence
    gate re-executes in, which is how the log for the evidence gate itself
    came to pin a count only the author's tree could produce (D-160). The
    committed fixtures carry every family forge emits; the real specs are
    scanned too whenever they happen to be present, as extra witnesses.
    """
    fixtures = sorted((Path(__file__).parent / "fixtures" / "specs").glob("*.md"))
    specs = fixtures + sorted((REPO_ROOT / "forge-specs").glob("*/spec.md"))
    assert any(p.name == "spec_every_id_family.md" for p in fixtures), (
        "the committed every-family fixture is gone — the guard would be "
        "vacuous over whatever is left"
    )
    known = vocab.REQUIREMENT_ID_PREFIXES | vocab.NON_REQUIREMENT_ID_PREFIXES
    unclassified = {}
    # Two or more capitals, and not preceded by a letter, digit or hyphen.
    # Every real ID family is at least two letters, and the negative lookbehind
    # is what stops the tail of a compound word from reading as a family:
    # without it `LEAD-RULING-5` yields "RULING" and the prose "cycle N to N-1"
    # yields "N", and a test that reports those reports nothing anyone acts on.
    family = re.compile(r"(?<![A-Za-z0-9-])([A-Z]{2,6})-\d+(?:\.\d+)?\b")
    witnessed: set = set()
    for spec in specs:
        text = spec.read_text(encoding="utf-8", errors="replace")
        for token in family.findall(text):
            if spec in fixtures:
                witnessed.add(token)
            if token not in known:
                unclassified.setdefault(token, spec.name)
    # Non-vacuity: the committed corpus must exercise every requirement family
    # except the three foundry-only ones retained under NFR-002, which no forge
    # spec has ever emitted and which therefore have no witness by design.
    unwitnessed = vocab.REQUIREMENT_ID_PREFIXES - witnessed - {"VC", "IR", "TR"}
    assert unwitnessed == set(), (
        f"requirement families with no committed witness: {sorted(unwitnessed)}"
    )
    assert unclassified == {}, (
        f"ID prefixes classified as neither requirement nor non-requirement: "
        f"{unclassified}. Add each to REQUIREMENT_ID_PREFIXES or to "
        f"NON_REQUIREMENT_ID_PREFIXES in schemas/vocab.py, with provenance."
    )


def test_an_observable_truth_binds_evidence_end_to_end(tmp_path):
    """D-150's whole point, at the surface EVID-02 actually gates.

    A casting whose ``<spec_requirements>`` cite OT-011, an evidence log whose
    header binds ONLY OT-011, and a completion report citing it. Before this
    change `# evidence-for: OT-011` parsed to the empty list, so the binding
    check saw an unbound requirement and hard-rejected with
    EVIDENCE_REQUIREMENT_UNBOUND -- an observable truth could never be
    evidenced at all.
    """
    from foundry_mcp.tools.foundry_handoff import foundry_accept_casting
    from foundry_mcp.tools.foundry_state import clear_active_run

    env = _build_divergent_spec_repo(tmp_path, req_ids=("OT-011",))
    try:
        result = foundry_accept_casting(
            casting_id=1,
            spec_hash=env["spec_hash"],
            prompt_hash=env["prompt_hash"],
            completion_report="OT-011 implemented at src/gate.py#accept_casting\n",
            project_root=str(env["project_root"]),
            casting_commit=env["casting_commit"],
        )
    finally:
        clear_active_run()

    assert result.get("failure_token") != "EVIDENCE_REQUIREMENT_UNBOUND", result
    assert result["evidence_verdict"] == "accepted", result
    assert result["evidence_provenance"][0]["evidence_for"] == ["OT-011"], result
    assert result["requirement_ids"] == ["OT-011"], result
    assert result["missing_citations"] == [], result
    assert result["ok"] is True, result.get("warning")


def test_an_uncited_observable_truth_is_now_caught(tmp_path):
    """The falsifier for the test above.

    If the gate still could not see OT-011, it would demand no citation for it
    and the report below -- which cites nothing -- would pass. The widening has
    to bite in BOTH directions or it has not happened.
    """
    from foundry_mcp.tools.foundry_handoff import foundry_accept_casting
    from foundry_mcp.tools.foundry_state import clear_active_run

    env = _build_divergent_spec_repo(tmp_path, req_ids=("OT-011",))
    try:
        result = foundry_accept_casting(
            casting_id=1,
            spec_hash=env["spec_hash"],
            prompt_hash=env["prompt_hash"],
            completion_report="I implemented OT-011, trust me.\n",
            project_root=str(env["project_root"]),
            casting_commit=env["casting_commit"],
        )
    finally:
        clear_active_run()

    assert result["missing_citations"] == ["OT-011"], result
    assert result["ok"] is False, result


# --------------------------------------------------------------------------- #
# D-146 — an undecodable document is a named refusal, never a traceback.
#
# Casting 4's package-wide scan named ONE site in this casting's files. These
# cover all four it should have named: the casting-prompt read in
# foundry_accept_casting, both reads in citation.verify_citations, and the spec
# copy in foundry_init. Each is driven with real undecodable bytes rather than
# asserted from the source, because the property is "does not raise", and only
# running it can show that.
# --------------------------------------------------------------------------- #

#: Bytes that are not valid UTF-8 in any position -- a lone continuation byte.
_UNDECODABLE = b"\xff\xfe stray continuation \x80\x81\n"


def test_an_undecodable_casting_prompt_is_refused_not_raised(tmp_path):
    """D-146 at the site the scan named."""
    from foundry_mcp.tools.foundry_handoff import foundry_accept_casting
    from foundry_mcp.tools.foundry_state import clear_active_run

    env = _build_divergent_spec_repo(tmp_path)
    (env["fdir"] / "castings" / "casting-1-prompt.md").write_bytes(_UNDECODABLE)
    try:
        result = foundry_accept_casting(
            casting_id=1,
            spec_hash=env["spec_hash"],
            prompt_hash=env["prompt_hash"],
            completion_report="AC-023 at src/gate.py#accept_casting\n",
            project_root=str(env["project_root"]),
        )
    finally:
        clear_active_run()

    assert result["ok"] is False, result
    assert "casting-1-prompt.md" in result["error"], result
    assert "could not be read" in result["error"], result
    # It is the run-artifact guard one rung EARLIER that answers here, and that
    # is the correct outcome, not a weaker one: the whole run directory is
    # swept before the gate reads any single document. The read inside
    # foundry_accept_casting is the second rung, and it is exercised directly
    # by the citation and init doors below where no such sweep runs first.
    assert "UnicodeDecodeError" in result["error"], result


@pytest.mark.parametrize("corrupt", ["spec", "report"])
def test_an_undecodable_spec_or_report_is_refused_not_raised(tmp_path, corrupt):
    """ADJACENT PATH 1 — a different door, and BOTH of its reads.

    ``verify_citations`` is reached by a different tool than the acceptance
    gate and has its own refusal shape. Parametrizing over which of its two
    documents is corrupt is the point: a guard added to the first read and not
    the second is the shape this whole class keeps recurring as.
    """
    from foundry_mcp.tools.citation import verify_citations

    spec = tmp_path / "spec.md"
    report = tmp_path / "report.md"
    spec.write_bytes(_UNDECODABLE if corrupt == "spec" else b"# Spec\n\nFR-1 x\n")
    report.write_bytes(_UNDECODABLE if corrupt == "report" else b"# Report\n")

    result = verify_citations(
        spec_path=str(spec), report_path=str(report), project_root=str(tmp_path)
    )
    assert result["pass"] is False, result
    assert "could not be read" in result["error"], result


def test_an_undecodable_spec_is_refused_at_init_not_copied(tmp_path):
    """ADJACENT PATH 2 — a different tool again, and the WRITE side.

    ``foundry_init`` copies the spec into the run directory. The tempting fix
    there is ``errors="replace"``, which does not raise and is worse than
    raising: it would seed the run with a silently mangled spec that every
    later phase hashes and trusts. So this asserts the copy did NOT happen.
    """
    from foundry_mcp.tools.foundry import foundry_init
    from foundry_mcp.tools.foundry_state import clear_active_run

    project_root = tmp_path / "repo"
    (project_root / "specs").mkdir(parents=True)
    spec = project_root / "specs" / "spec.md"
    spec.write_bytes(_UNDECODABLE)

    try:
        result = foundry_init(
            project_root=str(project_root), spec_path="specs/spec.md"
        )
    finally:
        clear_active_run()

    assert result["ok"] is False, result
    assert "could not be read" in result["error"], result
    copies = list((project_root / "foundry-archive").rglob("spec.md"))
    assert copies == [], f"a spec that could not be decoded was copied: {copies}"
