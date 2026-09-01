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

import json
import os
import subprocess
from pathlib import Path

import pytest

# Plan 04-02 ships the module skeleton. Until then, the entire module SKIPs
# cleanly. importorskip is the canonical RED-or-SKIP discipline for
# downstream-wave-owned implementation modules — mirrors Phase 2 Plan 02-01.
evidence = pytest.importorskip("foundry_mcp.tools.evidence")


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


def _build_divergent_spec_repo(tmp_path: Path, *, replay_body_only: bool = False) -> dict:
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
        "# evidence-for: AC-023\n"
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
        "# Run spec\n\nAC-023 the gate reads the run's spec.\n",
        encoding="utf-8",
    )

    prompt_text = (
        "<spec_requirements>\n"
        "- **AC-023**: evidence runs against the run's actual spec\n"
        "</spec_requirements>\n"
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
    leave ``worktrees/`` empty — the check the harness's ``worktree_torn_down``
    flag can no longer make on its own once suffixed siblings are possible."""
    from foundry_mcp.tools import worktree_helpers as wh

    repo, run_dir, head = _worktree_repo(tmp_path)

    for casting_id in (1, 2, 3):
        path = wh._setup_worktree(repo, casting_id, head, run_dir)
        assert path.is_dir()
        wh._teardown_worktree(repo, path)

    leftovers = sorted(p.name for p in (run_dir / "worktrees").iterdir())
    assert leftovers == [], f"worktree dirs leaked: {leftovers}"


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
