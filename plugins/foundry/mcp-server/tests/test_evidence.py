"""Phase 4 / EVID-01 — server-side evidence re-execution tests.

24 RED-or-SKIP stubs covering VALIDATION.md per-task verification map.

Plan 04-02 territory (parser + constants + module skeleton — 4 unit tests):
  - test_no_cmd_header_rejects
  - test_volatile_malformed_rejects
  - test_volatile_order_is_respected
  - test_failure_tokens_are_in_allowlist

Plan 04-03 territory (worktree + subprocess + redaction + comparator + stub
library — 15 tests, mix of integration via run_accept_casting_with_evidence
and unit tests against the ``evidence._is_stub_pattern_*`` family):
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
import dataclasses
import inspect
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


def test_the_syntax_refusal_names_a_token_in_the_closed_allowlist():
    """CT-015's output half, and the reason it is a NAMED token at all.

    The contract asks the sweep to refuse an unparseable command "with
    EVIDENCE_COMMAND_SYNTAX before executing it". A refusal token that is not a
    member of this tuple is not a refusal an operator can read: every renderer
    downstream of the sweep prints the token it was handed, and one absent from
    the allowlist arrives as an unexplained failure beside ten explained ones.

    The tuple is a CLOSED vocabulary, so this membership is also what makes the
    addition code-edit forced rather than something a caller can spell into
    existence — the same discipline
    ``test_failure_tokens_are_in_allowlist`` holds for Phase 4's eight.
    """
    assert "EVIDENCE_COMMAND_SYNTAX" in evidence.KNOWN_EVIDENCE_FAILURE_TOKENS, (
        "the sweep's parse-before-execute refusal names a token the closed "
        "allowlist does not carry, so an operator reading the sweep result "
        "sees an unnamed reason"
    )
    # Appended, not inserted: every earlier member keeps its position, which is
    # the ordering CONTEXT.md documents and the two prior extensions preserved.
    assert evidence.KNOWN_EVIDENCE_FAILURE_TOKENS[-1] == "EVIDENCE_COMMAND_SYNTAX"
    assert len(set(evidence.KNOWN_EVIDENCE_FAILURE_TOKENS)) == len(
        evidence.KNOWN_EVIDENCE_FAILURE_TOKENS
    ), "a token is spelled twice in the allowlist"


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
    evidence_body: str = _EVIDENCE_BODY,
    header_prose: str = "",
    evidence_cmd: str = "cat replay.txt",
) -> dict:
    """A repo whose stale ``specs/spec.md`` is v2.0 and whose RUN spec is v2.1.

    Returns the arguments ``foundry_accept_casting`` needs, plus the two spec
    paths so a test can assert which one was read.

    ``evidence_body`` is what the committed log's BODY is — everything below
    the header block's blank separator, i.e. exactly what the re-executed
    command emits. ``header_prose`` is free ``#`` prose appended to the header
    directives, ABOVE that separator, which is where every writer in this
    plugin puts it. The two knobs exist for D-198: the whole defect is that
    the stub detector could not tell a ``#`` line in one position from a ``#``
    line in the other, so a test that pins the distinction has to be able to
    put a comment line on either side of the separator.

    ``evidence_cmd`` is the committed log's ``# evidence-cmd:``. It defaults to
    the deterministic ``cat replay.txt`` replay every other caller wants; a
    caller overrides it when the property under test is a property of the
    EXECUTION rather than of the comparison — D-175's environment scrub, whose
    whole question is what the child process could see, cannot be asked of a
    command that only reads a file back.
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
        f"# evidence-cmd: {evidence_cmd}\n"
        f"# evidence-for: {', '.join(req_ids)}\n"
        + header_prose
        + "\n" + evidence_body
    )
    # replay_body_only mirrors what a REAL evidence command does: it emits the
    # body alone, never the `# evidence-*:` header lines that only exist in the
    # committed file. The default (full-file replay) mirrors conftest's
    # use_cat_replay harness. Both must verify.
    (project_root / "replay.txt").write_text(
        evidence_body if replay_body_only else evidence_log, encoding="utf-8"
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


def test_accept_casting_without_a_commit_is_refused_naming_casting_commit(tmp_path):
    """CT-015 / AC-015 / FR-010 / OT-027 verbatim: 'Foundry-Accept-Casting
    (casting_commit required) ... refusal naming casting_commit when omitted.'

    THIS TEST'S SUBJECT CHANGED, AND THE ASSERTION GOT STRONGER, NOT WEAKER.
    It used to pin the `casting_commit=None` backwards-compat shim, which
    returned `ok: True` with `evidence_verdict: None` — acceptance granted with
    evidence verification structurally bypassed. That shim is exactly what this
    effort retires: a casting that cannot name its commit has not been shown to
    have built anything, so the same call is now a REFUSAL and the refusal is
    the requirement.

    Kept rather than deleted, and re-pointed rather than relaxed, because the
    call it drives is the one a lead makes by accident and the answer to it is
    the whole point of making the parameter required."""
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

    assert result["ok"] is False, result
    # The refusal NAMES the missing parameter, in both the machine field and
    # the prose — a lead who reads either one learns what to pass.
    assert result["field"] == "casting_commit", result
    assert "casting_commit" in result["error"], result
    assert result["hint"], result
    # And it refuses BEFORE any verification could have happened, so there is
    # no verdict to report rather than a `None` verdict standing in for one.
    assert "evidence_verdict" not in result, result


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


# --------------------------------------------------------------------------- #
# D-200 / FR-010 / GI-002 / ST-005 / CT-007 — the byte comparator's strip is
# ONE decision over BOTH sides.
#
# D-198 (below) closed the false-REFUSAL half of this helper family. This is
# the false-ACCEPT half, and it is the reason the strip stopped being two
# independent calls. `_EVIDENCE_HEADER_BLOCK_RE` alternates `#` lines and
# blank lines in ONE run, so it does not stop at the header/body separator:
# applied to the CAPTURE, it eats however much leading `#`/blank content the
# command really emitted. Both comparator call sites ran it on each side
# separately, so two logs whose leading content genuinely DIFFERS reduced to
# the same bytes and matched.
#
# Driven live at f5b487b, before the fix:
#
#   committed = "# evidence-cmd: ...\n# evidence-for: FR-010\n"
#               "# FABRICATED: the guard holds\n\nREAL_TAIL_MATCHES\n"
#   captured  = "# a completely different leading comment\n"
#               "# written by the real command\n\nREAL_TAIL_MATCHES\n"
#
# both reduced to `"REAL_TAIL_MATCHES\n"`, and `_compare_byte_match` returned
# `(True, None)` — a false ACCEPT at `Foundry-Accept-Casting` (FR-010) and, one
# door further, at `Foundry-Phase(inspect_start)`, which reported no error, no
# mismatches, and advanced the cycle counter (GI-002 / ST-005 / CT-007).
#
# WHAT THE COMPARATOR NOW STRIPS ON BOTH SIDES, AND WHY IT CANNOT CANCEL.
# The capture is stripped of NOTHING — a re-execution capture is all output.
# The committed side loses exactly the prefix the capture did not emit, found
# by asking whether the capture's own leading `#`/blank run is a LINE-ALIGNED
# SUFFIX of the committed one. Because the only text ever removed from the
# committed side is text the capture was shown not to contain, no strip can
# erase a disagreement: differing leading content is never removed from either
# side, so it always reaches the diff. The previous shape could cancel
# precisely because each side chose its own strip in ignorance of the other.
#
# The four cases and the true accepts each one keeps are the table below.
# --------------------------------------------------------------------------- #

_D200_COMMITTED_FORGERY = (
    "# evidence-cmd: cat replay.txt\n"
    "# evidence-for: FR-010\n"
    "# FABRICATED: the guard holds\n"
    "\n"
    "REAL_TAIL_MATCHES\n"
)
_D200_CAPTURED_REAL = (
    "# a completely different leading comment\n"
    "# written by the real command\n"
    "\n"
    "REAL_TAIL_MATCHES\n"
)


def _d200_compare(committed: str, captured: str):
    """The comparator exactly as both call sites now reach it."""
    body_c, body_k = evidence._header_stripped_pair(committed, captured)
    matched, diff, _, _ = evidence._compare_byte_match(body_c, body_k, [])
    return matched, diff


def test_a_fabricated_leading_comment_block_is_refused_with_a_naming_diff():
    """D-200's pair: differing leading comment blocks, identical tail.

    The tail matching is the whole trick — it is what made the forgery look
    like a reproduction once both leading blocks had been eaten. The refusal
    must NAME the disagreement, not merely report inequality, or a lead reading
    the mismatch record cannot tell which line was fabricated.
    """
    matched, diff = _d200_compare(_D200_COMMITTED_FORGERY, _D200_CAPTURED_REAL)

    assert matched is False
    assert diff is not None
    # The first differing line is named on both sides.
    assert "-# FABRICATED: the guard holds" in diff
    assert "+# a completely different leading comment" in diff
    # And the identical tail is NOT what the refusal is about.
    assert "-REAL_TAIL_MATCHES" not in diff


def test_an_honest_log_whose_captured_body_starts_with_comments_still_matches():
    """The true accept the narrowing must keep — D-198's own subject, seen
    from the comparator instead of from the stub detector.

    A teammate whose command shows the source it changed captures a body that
    legitimately BEGINS with `#` lines. Under the old symmetric strip those
    lines vanished from both sides, which is what made the forgery above
    possible; a fix that instead refused them would re-open D-198 from the
    other end. They are compared now, and they agree, so the log is accepted.
    """
    committed = (
        "# evidence-cmd: sed -n '1,3p' src/guard.py\n"
        "# evidence-for: FR-010\n"
        "\n"
        "# guard: refuse on mismatch, never on shape\n"
        "# see D-198 for why punctuation is not the discriminator\n"
        "def guard(log):\n"
    )
    captured = (
        "# guard: refuse on mismatch, never on shape\n"
        "# see D-198 for why punctuation is not the discriminator\n"
        "def guard(log):\n"
    )

    assert _d200_compare(committed, captured) == (True, None)
    # And the captured `#` lines really are inside what was compared, rather
    # than agreeing because both sides were emptied.
    body_c, body_k = evidence._header_stripped_pair(committed, captured)
    assert body_c.startswith("# guard: refuse on mismatch")
    assert body_k == captured


@pytest.mark.parametrize(
    "label,committed,captured,expect_match",
    [
        (
            "capture emits no leading run — every corpus log; verdict is the "
            "one the old code reached, since it stripped nothing here either",
            "# evidence-cmd: pytest\n# evidence-for: AC-1\n\n2 passed\n",
            "2 passed\n",
            True,
        ),
        (
            "capture IS the full file — the `use_cat_replay` harness, whose "
            "replay file holds the whole rewritten evidence",
            _D200_COMMITTED_FORGERY,
            _D200_COMMITTED_FORGERY,
            True,
        ),
        (
            "capture's run is a proper suffix — the honest `#`-bodied log",
            "# evidence-cmd: x\n\n# real output\nrest\n",
            "# real output\nrest\n",
            True,
        ),
        (
            "capture's run is NOT a suffix — the D-200 forgery",
            _D200_COMMITTED_FORGERY,
            _D200_CAPTURED_REAL,
            False,
        ),
        (
            "the writer left two blank lines and the command emits the second "
            "— the separator is one line, the rest is output",
            "# evidence-cmd: x\n# evidence-for: AC-1\n\n\n=== keys ===\n",
            "\n=== keys ===\n",
            True,
        ),
    ],
)
def test_the_strip_is_decided_by_what_the_capture_did_not_emit(
    label, committed, captured, expect_match
):
    """All four branches of `_split_committed_header`, plus the two-blank
    shape two logs in this run's own corpus carry.

    Stated as one table because the branches are one decision: the header is
    the part of the committed leading run the re-execution did not produce.
    """
    assert _d200_compare(committed, captured)[0] is expect_match, label


def test_the_suffix_test_is_line_aligned_not_a_bare_endswith():
    """A raw `str.endswith` would split a directive line MID-LINE.

    Committed run `"# evidence-cmd: X\\n"` ends with `"\\n"`, so a capture
    whose leading run is a single blank line would "match" as a suffix and the
    directive's own newline would be donated to the body — accepting a
    committed log that is missing the blank line its command actually emits.
    The comparison is over lines, so the suffix test is over lines.
    """
    matched, diff = _d200_compare("# evidence-cmd: X\nTAIL\n", "\nTAIL\n")
    assert matched is False
    assert diff is not None


def _leading_hash_blank_run(text: str) -> list[str]:
    """The WIDE leading `#`/blank run — the superset the comparator rejected.

    D-205 removed this shape from `evidence.py` entirely, because a helper
    that hands back a superset is what the accept branch kept reaching for.
    It survives HERE, in the tests, as the measuring instrument: the census
    below needs to say how much of a committed leading run the header grammar
    does NOT account for, which is precisely `wide minus provable`.
    """
    match = evidence._EVIDENCE_HEADER_BLOCK_RE.match(text)
    return match.group(0).splitlines(keepends=True) if match else []


def _corpus_logs() -> list[Path]:
    evidence_dir = REPO_ROOT / "evidence"
    return sorted(evidence_dir.glob("*.log")) if evidence_dir.exists() else []


def _ungrammatical_leading_lines(text: str) -> list[str]:
    """Lines in the committed leading run the header grammar does not account
    for — hand-typed writer prose the command never printed."""
    wide = _leading_hash_blank_run(text)
    provable = evidence._provable_header_lines(text)
    return [line for line in wide[len(provable):] if line.strip()]


def test_the_strip_discards_only_lines_the_header_grammar_accounts_for():
    """D-205 / FR-010 / GI-002 — the invariant that replaced "inert".

    This test used to assert the fix was INERT on the shipped corpus, and it
    passed for exactly the reason D-205 names: the strip discarded the whole
    leading `#`/blank run, so a log carrying hand-typed prose in that run was
    "unchanged" because the prose was thrown away unread on both the old path
    and the new one. Inertness was never the property worth pinning — it is
    the property a fabricator relies on.

    What is pinned now is the lead's binding ruling on D-205: the committed
    leading `#` run is INSIDE the byte-identical guarantee, and the only text
    outside it is what the writers emit BY GRAMMAR — contiguous known
    `# evidence-<directive>:` lines plus one blank separator. So over the real
    committed corpus, every line the strip discards must be one of those. A
    corpus log with writer prose above its body is not an exception to this
    test; it is a log that must be RECAPTURED, and the census below names it.
    """
    logs = _corpus_logs()
    if not logs:
        pytest.skip(f"no committed evidence corpus at {REPO_ROOT / 'evidence'}")

    offenders: list[str] = []
    for log in logs:
        text = log.read_text(encoding="utf-8")
        # The capture a real re-execution produces for these logs emits no
        # directive line of its own, which is the branch every corpus log
        # lands on: the header is the committed provable header, whole.
        header = evidence._split_committed_header(text, "irrelevant body\n")
        discarded = header.splitlines(keepends=True)
        grammar = evidence._provable_header_lines(text)
        if discarded != grammar:
            offenders.append(f"{log.name}: discarded {len(discarded)} lines, "
                             f"grammar accounts for {len(grammar)}")
        for line in discarded:
            if line.strip() and not evidence._is_directive_line(line):
                offenders.append(f"{log.name}: discarded non-directive {line!r}")
    assert offenders == [], offenders


def test_committed_prose_outside_the_grammar_reaches_the_comparison():
    """D-205's operative consequence, driven on the real corpus.

    A committed line the grammar does not account for is BODY, so it must land
    inside what `_compare_byte_match` sees — a command that does not print it
    then mismatches, which is the refusal D-205 asks for. Vacuous only when
    every log has already been recaptured, and that is the state this run is
    driving toward; until then this asserts the property on real offenders.
    """
    logs = _corpus_logs()
    if not logs:
        pytest.skip(f"no committed evidence corpus at {REPO_ROOT / 'evidence'}")

    for log in logs:
        text = log.read_text(encoding="utf-8")
        prose = _ungrammatical_leading_lines(text)
        if not prose:
            continue
        body, _ = evidence._header_stripped_pair(text, "a capture without it\n")
        for line in prose:
            assert line in body, (
                f"{log.name}: {line!r} was discarded rather than compared"
            )
        # And the log therefore does NOT reproduce against a capture of its
        # own body-below-the-prose, which is what the recapture is for.
        below = "".join(
            text.splitlines(keepends=True)[
                len(_leading_hash_blank_run(text)):
            ]
        )
        matched, _, _, _ = evidence._compare_byte_match(body, below, [])
        assert matched is False, f"{log.name}: prose still cancels"


# The forgery as a committed evidence file and the capture its command really
# produces. Same tail, different leading comment block — the shape that used to
# reduce to identical bytes on both sides.
#
# Both are comfortably over `EVIDENCE_STUB_MIN_BYTES`, deliberately: a short
# body would be refused by the stub ladder at step 6 for being small, and the
# door would then look like it had caught the forgery when the byte comparator
# at step 5 had waved it through. The RED these pin has to be the comparator's.
_D200_DOOR_TAIL = (
    "REAL_TAIL_MATCHES\n"
    "collected 3 items\n"
    "\n"
    "tests/test_guard.py::test_refuses_on_mismatch PASSED\n"
    "tests/test_guard.py::test_accepts_a_reproduction PASSED\n"
    "tests/test_guard.py::test_names_the_log PASSED\n"
    "\n"
    "3 passed\n"
)
_D200_DOOR_BODY = "# FABRICATED: the guard holds\n\n" + _D200_DOOR_TAIL
_D200_DOOR_CAPTURE = (
    "# a completely different leading comment\n"
    "# written by the real command\n"
    "\n" + _D200_DOOR_TAIL
)


def test_the_acceptance_door_refuses_a_fabricated_leading_comment_block(tmp_path):
    """D-200 at the wire — FR-010 / CT-015.

    Driven through the MCP request handler, not the helper: the false ACCEPT
    was reachable by a client, so the refusal has to be too. The committed log
    carries a fabricated leading comment; `replay.txt` — what the command
    actually emits — carries a different one over the same tail. Before the
    fix this was `evidence_verdict: accepted`.
    """
    from foundry_mcp import server
    from foundry_mcp.tools.foundry_state import clear_active_run

    env = _build_divergent_spec_repo(
        tmp_path, evidence_body=_D200_DOOR_BODY, replay_body_only=True
    )
    # The command's REAL output diverges from the committed body's leading
    # comment block only. Committed at its own commit so the worktree the
    # verifier checks out carries it.
    (env["project_root"] / "replay.txt").write_text(
        _D200_DOOR_CAPTURE, encoding="utf-8"
    )
    _run_git(["add", "replay.txt"], env["project_root"])
    _run_git(["commit", "-q", "-m", "the capture diverges"], env["project_root"])
    casting_commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=env["project_root"], check=True, capture_output=True, text=True,
    ).stdout.strip()

    server_project_root = server._project_root
    server._project_root = str(env["project_root"])
    try:
        result = _drive_accept_casting_over_mcp({
            "casting_id": 1,
            "spec_hash": env["spec_hash"],
            "prompt_hash": env["prompt_hash"],
            "completion_report": (
                "AC-023 implemented at src/gate.py#accept_casting\n"
            ),
            "casting_commit": casting_commit,
        })
    finally:
        server._project_root = server_project_root
        clear_active_run()

    assert result["ok"] is False, result
    assert result["failure_token"] == "EVIDENCE_OUTPUT_MISMATCH", result
    assert "FABRICATED" in result["failure_detail"], result


@pytest.fixture
def _d200_run_env(tmp_path, monkeypatch):
    """A run inside a real git repo, armed for `inspect_start`.

    Mirrors `test_inspect_mode.py`'s `run_env` in miniature — the same active
    run, the same `_check_active_teams` patch so nothing depends on an ambient
    tmux session, and the same `/foundry-archive/` ignore rule, without which
    the run's own artifacts would land in every GRIND diff.

    The patch goes through `tests/orchestration/_env.py#patch_everywhere`
    rather than `monkeypatch.setattr` on one module. Before the carve there was
    ONE module, so patching it patched the only binding; after it the symbol is
    imported BY NAME into `orchestration/transitions.py`, `orchestration/
    gates.py`, `orchestration/width.py` and `orchestration/guidance.py`, and a
    patch reaching some of those and not others exercises a state no run can be
    in. That helper is the post-split spelling of the fact the monolith used to
    make true by construction.
    """
    from foundry_mcp.tools import foundry_state

    run_name = "d200-run"
    project_root = tmp_path / "repo"
    project_root.mkdir()
    for args in (
        ("init", "-q", "-b", "main"),
        ("config", "user.email", "foundry@example.invalid"),
        ("config", "user.name", "foundry"),
    ):
        _run_git(list(args), project_root)
    (project_root / ".gitignore").write_text("/foundry-archive/\n", encoding="utf-8")
    (project_root / "src").mkdir()
    (project_root / "src" / "handler.py").write_text(
        "def handle():\n    pass\n", encoding="utf-8"
    )
    fdir = project_root / "foundry-archive" / run_name
    (fdir / "castings").mkdir(parents=True, exist_ok=True)
    (fdir / "castings" / "manifest.json").write_text(
        json.dumps({"castings": [{"id": 1, "key_files": ["src/handler.py"]}]}),
        encoding="utf-8",
    )
    from tests.orchestration._env import patch_everywhere

    patch_everywhere(
        monkeypatch,
        "_check_active_teams",
        lambda _pr: {"active": False, "teams": [], "live_panes": []},
    )
    foundry_state.set_active_run(run_name)
    try:
        yield str(project_root), fdir
    finally:
        foundry_state.clear_active_run()


def test_inspect_start_refuses_a_fabricated_leading_comment_block(_d200_run_env):
    """D-200 at the boundary — GI-002 / ST-005 / CT-007 / AC-013 / OT-008.

    PROVE drove this door at f5b487b and it returned no error, `mismatches:
    []`, and an ADVANCED cycle counter on a committed log whose three captured
    `#` lines disagreed with HEAD — while the identical drift on a captured
    line not starting with `#` was correctly refused. So the sweep's blindness
    was punctuation, exactly as D-198's was. All three observations are pinned
    here: the transition refuses NAMING the log, `evidence_sweep` carries the
    mismatch, and the counter is unchanged.
    """
    # The carve put these three in three different places, so they are imported
    # from three different modules rather than through one alias standing for
    # the monolith: `INSPECT_BOUNDARY_SHA_MARKER` and `now_iso` are LEAF facts
    # (`tools/artifacts.py`, `tools/foundry_state.py`), `current_cycle` is the
    # consolidated reader in `tools/foundry_state.py`, and the transition is
    # `tools/orchestration/transitions.py#foundry_mark_phase_complete`.
    from foundry_mcp.tools.artifacts import INSPECT_BOUNDARY_SHA_MARKER
    from foundry_mcp.tools.foundry_state import current_cycle, now_iso
    from foundry_mcp.tools.orchestration.transitions import (
        foundry_mark_phase_complete,
    )

    project_root, fdir = _d200_run_env
    root = Path(project_root)

    # The capture the command really produces, and a committed log whose
    # leading comment block is not it.
    (root / "replay.txt").write_text(_D200_DOOR_CAPTURE, encoding="utf-8")
    evidence_dir = root / "evidence"
    evidence_dir.mkdir()
    (evidence_dir / "casting-1-handler.log").write_text(
        "# evidence-cmd: cat replay.txt\n"
        "# evidence-for: CT-007\n"
        "\n" + _D200_DOOR_BODY,
        encoding="utf-8",
    )
    (fdir / "state.json").write_text(
        json.dumps({"phase": "F3", "cycle": 1}), encoding="utf-8"
    )
    (fdir / "defects.json").write_text(
        json.dumps({"defects": [{
            "id": "D-001", "cycle": 1, "source": "trace", "type": "UNWIRED",
            "description": "the handler never calls the store",
            "spec_ref": "FR-001", "symbol": "handle", "file": "src/handler.py",
            "status": "open", "tier": "LIVE", "class": "UNWIRED_SURFACE",
            "fixed_in_cycle": None,
        }]}),
        encoding="utf-8",
    )
    _run_git(["add", "-A"], root)
    _run_git(["commit", "-q", "-m", "pre-boundary"], root)
    (fdir / INSPECT_BOUNDARY_SHA_MARKER).write_text(
        subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=root, check=True, capture_output=True, text=True,
        ).stdout.strip() + "\n",
        encoding="utf-8",
    )
    # The GRIND's work, which is what puts casting 1's log in the delta scope.
    (root / "src" / "handler.py").write_text(
        "def handle():\n    return 1\n", encoding="utf-8"
    )
    _run_git(["add", "-A"], root)
    _run_git(["commit", "-q", "-m", "a GRIND cycle"], root)
    (fdir / ".next-action-called").write_text(f"{now_iso()}\n", encoding="utf-8")

    result = foundry_mark_phase_complete("inspect_start", project_root)

    assert result.get("ok") is not True, result
    # The refusal NAMES the log — AC-013's operative clause.
    assert "casting-1-handler.log" in result["error"], result
    assert result["mismatches"], result
    assert result["mismatches"][0]["failure_token"] == "EVIDENCE_OUTPUT_MISMATCH"
    assert "casting-1-handler.log" in result["mismatches"][0]["log"]
    # AC-013's second clause, and OT-008 verbatim: the counter is unchanged and
    # no INSPECT mode was recorded for a transition that did not happen.
    assert current_cycle(fdir) == 1
    state = json.loads((fdir / "state.json").read_text(encoding="utf-8"))
    assert state["cycle"] == 1 and state["phase"] == "F3"
    assert "inspect_modes" not in state


def test_both_replay_conventions_pass_together_through_the_sweep_pool(tmp_path):
    """The adjacent path D-200's fix must not have broken.

    `_header_stripped_pair` is reached by TWO callers — the acceptance door's
    `_verify_one_evidence_file` and the boundary's `_sweep_one_log` — and the
    second runs concurrently in a bounded thread pool over ONE shared detached
    worktree. It is also reached by two different replay CONVENTIONS: a log
    whose command emits the body alone (a real evidence command) and one whose
    command emits the whole committed file, header included (the `use_cat_
    replay` harness shape, which lands on the equal-runs branch where the
    header the fix computes is empty).

    Both conventions in one corpus, swept together in the pool, so the branch
    the D-200 pair never walks is walked here.
    """
    body_only = _sweep_log("casting-1-alpha", for_ids="CT-007")
    full_file = _sweep_log("casting-2-beta", for_ids="CT-014")
    env = _build_sweep_repo(tmp_path, logs={
        "casting-1-alpha.log": body_only,
        "casting-2-beta.log": full_file,
    })
    # `_build_sweep_repo` writes each replay file as the log's BODY. Re-point
    # casting 2's at the FULL committed file so the two conventions differ.
    (env["project_root"] / "replay-casting-2-beta.txt").write_text(
        full_file, encoding="utf-8"
    )
    _run_git(["add", "-A"], env["project_root"])
    _run_git(["commit", "-q", "-m", "full-file replay"], env["project_root"])

    result = _sweep(env, full=True)

    assert result["ok"] is True, result["mismatches"]
    assert len(result["logs_reexecuted"]) == 2, result
    assert result["pool_size"] >= 1


# --------------------------------------------------------------------------- #
# D-205 / GI-002 / ST-005 / CT-007 / AC-013 / OT-008 / FR-010 — the accept
# branch proves every line it discards.
#
# D-200 (above) fixed the SYMMETRY of the header strip and left its GRAMMAR
# wide on the one branch that decides ACCEPT. `_split_committed_header` took
# that branch whenever the capture's leading `#`/blank run was empty or a
# line-aligned suffix, and returned the WHOLE committed run — writer prose
# included — without ever testing a line of it against the directive grammar
# the same module defines. So a committed log could carry content HEAD's
# command never emits, and both doors certified it.
#
# Driven at the wire at cb77e83, three runs identical but for the committed
# log's body, against the SAME command:
#
#   A  `<directives> + blank + REAL_TAIL`, command emits REAL_TAIL
#          -> accepted, mismatches [], counter 1 -> 2       (correct)
#   B  the same log with `# FABRICATED: all 47 assertions passed on a clean
#      tree` inserted between the directives and the body
#          -> ACCEPTED, mismatches [], counter advanced      (the defect)
#   C  the identical claim WITHOUT the leading `#`
#          -> refused naming the log, counter held           (correct)
#
# B and C differ by one character. The lead's binding ruling: the committed
# leading `#` run is INSIDE the byte-identical guarantee, and the only text
# outside it is what the writers emit by grammar — contiguous leading
# `# evidence-<known>:` directives plus one blank separator. Every other line,
# `#`-prefixed or not, is body.
# --------------------------------------------------------------------------- #

_D205_CLAIM = "FABRICATED: all 47 assertions passed on a clean tree"


def _d205_accept(tmp_path, subdir: str, header_prose: str) -> dict:
    """One `foundry_accept_casting` drive over a body-only replay."""
    from foundry_mcp.tools.foundry_handoff import foundry_accept_casting
    from foundry_mcp.tools.foundry_state import clear_active_run

    root = tmp_path / subdir
    root.mkdir()
    env = _build_divergent_spec_repo(
        root,
        replay_body_only=True,
        evidence_body=_D200_DOOR_TAIL,
        header_prose=header_prose,
    )
    try:
        return foundry_accept_casting(
            casting_id=1,
            spec_hash=env["spec_hash"],
            prompt_hash=env["prompt_hash"],
            completion_report=(
                "AC-023 implemented at src/gate.py#accept_casting\n"
            ),
            project_root=str(env["project_root"]),
            casting_commit=env["casting_commit"],
        )
    finally:
        clear_active_run()


def test_the_acceptance_door_refuses_a_hash_prefixed_committed_only_claim(
    tmp_path,
):
    """D-205 shapes A and B at the wire — FR-010 / CT-015.

    One log, one command, one added line. The honest log is accepted; the
    same log with the claim inserted between the directives and the body is
    refused with the claim NAMED in the failure detail. Before this fix the
    second call returned `evidence_verdict: accepted`, because the whole
    leading `#` run was discarded unread.
    """
    honest = _d205_accept(tmp_path, "honest", "")
    assert honest["ok"] is True, honest
    assert honest["evidence_verdict"] == "accepted", honest

    forged = _d205_accept(tmp_path, "forged", f"# {_D205_CLAIM}\n")
    assert forged["ok"] is False, forged
    assert forged["failure_token"] == "EVIDENCE_OUTPUT_MISMATCH", forged
    assert "FABRICATED" in forged["failure_detail"], forged


def test_the_acceptance_door_refuses_the_same_claim_without_the_hash(tmp_path):
    """D-205's control C — the one-character difference, at the same door.

    The claim with no leading `#` was ALWAYS refused; that is the whole reason
    B was a defect rather than a policy. Pinned so a later widening of the
    grammar cannot quietly make B agree with A instead of with C.
    """
    from foundry_mcp.tools.foundry_handoff import foundry_accept_casting
    from foundry_mcp.tools.foundry_state import clear_active_run

    env = _build_divergent_spec_repo(
        tmp_path,
        replay_body_only=True,
        evidence_body=f"{_D205_CLAIM}\n\n{_D200_DOOR_TAIL}",
    )
    # The command emits the tail alone — the claim is committed-only, exactly
    # as in shape B, and differs from it only by the missing `#`.
    (env["project_root"] / "replay.txt").write_text(
        _D200_DOOR_TAIL, encoding="utf-8"
    )
    _run_git(["add", "replay.txt"], env["project_root"])
    _run_git(["commit", "-q", "-m", "the claim is committed-only"],
             env["project_root"])
    casting_commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=env["project_root"], check=True, capture_output=True, text=True,
    ).stdout.strip()

    try:
        result = foundry_accept_casting(
            casting_id=1,
            spec_hash=env["spec_hash"],
            prompt_hash=env["prompt_hash"],
            completion_report=(
                "AC-023 implemented at src/gate.py#accept_casting\n"
            ),
            project_root=str(env["project_root"]),
            casting_commit=casting_commit,
        )
    finally:
        clear_active_run()

    assert result["ok"] is False, result
    assert result["failure_token"] == "EVIDENCE_OUTPUT_MISMATCH", result
    assert "FABRICATED" in result["failure_detail"], result


def _d205_boundary_log(prose: str, tail: str = _D200_DOOR_TAIL) -> str:
    """A committed evidence log whose command replays `tail`."""
    return (
        "# evidence-cmd: cat replay.txt\n"
        "# evidence-for: CT-007\n"
        f"{prose}"
        "\n" + tail
    )


def _drive_d205_boundary(run_env, log_text: str, capture: str = _D200_DOOR_TAIL):
    """Arm `_d200_run_env` with one committed log and call `inspect_start`.

    Returns `(result, cycle_after, fdir)`. Identical arrangement to the D-200
    boundary test — one casting-1 log in the delta scope, one GRIND commit
    touching that casting's key_file — so the only variable between the drives
    below is the committed log's own bytes.
    """
    # The carve put these three in three different places, so they are imported
    # from three different modules rather than through one alias standing for
    # the monolith: `INSPECT_BOUNDARY_SHA_MARKER` and `now_iso` are LEAF facts
    # (`tools/artifacts.py`, `tools/foundry_state.py`), `current_cycle` is the
    # consolidated reader in `tools/foundry_state.py`, and the transition is
    # `tools/orchestration/transitions.py#foundry_mark_phase_complete`.
    from foundry_mcp.tools.artifacts import INSPECT_BOUNDARY_SHA_MARKER
    from foundry_mcp.tools.foundry_state import current_cycle, now_iso
    from foundry_mcp.tools.orchestration.transitions import (
        foundry_mark_phase_complete,
    )

    project_root, fdir = run_env
    root = Path(project_root)

    (root / "replay.txt").write_text(capture, encoding="utf-8")
    evidence_dir = root / "evidence"
    evidence_dir.mkdir(exist_ok=True)
    (evidence_dir / "casting-1-handler.log").write_text(
        log_text, encoding="utf-8"
    )
    (fdir / "state.json").write_text(
        json.dumps({"phase": "F3", "cycle": 1}), encoding="utf-8"
    )
    (fdir / "defects.json").write_text(
        json.dumps({"defects": [{
            "id": "D-001", "cycle": 1, "source": "trace", "type": "UNWIRED",
            "description": "the handler never calls the store",
            "spec_ref": "FR-001", "symbol": "handle", "file": "src/handler.py",
            "status": "open", "tier": "LIVE", "class": "UNWIRED_SURFACE",
            "fixed_in_cycle": None,
        }]}),
        encoding="utf-8",
    )
    _run_git(["add", "-A"], root)
    _run_git(["commit", "-q", "-m", "pre-boundary"], root)
    (fdir / INSPECT_BOUNDARY_SHA_MARKER).write_text(
        subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=root, check=True, capture_output=True, text=True,
        ).stdout.strip() + "\n",
        encoding="utf-8",
    )
    # The GRIND's work, which is what puts casting 1's log in the delta scope.
    (root / "src" / "handler.py").write_text(
        "def handle():\n    return 1\n", encoding="utf-8"
    )
    _run_git(["add", "-A"], root)
    _run_git(["commit", "-q", "-m", "a GRIND cycle"], root)
    (fdir / ".next-action-called").write_text(f"{now_iso()}\n", encoding="utf-8")

    return foundry_mark_phase_complete("inspect_start", project_root), \
        current_cycle(fdir), fdir


def test_inspect_start_refuses_a_hash_prefixed_committed_only_claim(
    _d200_run_env,
):
    """D-205 shape B at the boundary — GI-002 / ST-005 / CT-007 / AC-013 /
    OT-008.

    At cb77e83 this transition returned ok, `mismatches: []` and advanced the
    counter 1 -> 2 on a committed log carrying a claim its command never
    printed, because the claim wore a `#`. All three observations are pinned:
    the transition refuses NAMING the log, `mismatches` carries it, and the
    counter is unchanged (OT-008 verbatim).
    """
    result, cycle, fdir = _drive_d205_boundary(
        _d200_run_env, _d205_boundary_log(f"# {_D205_CLAIM}\n")
    )

    assert result.get("ok") is not True, result
    assert "casting-1-handler.log" in result["error"], result
    assert result["mismatches"], result
    assert result["mismatches"][0]["failure_token"] == "EVIDENCE_OUTPUT_MISMATCH"
    assert "casting-1-handler.log" in result["mismatches"][0]["log"]
    assert "FABRICATED" in result["mismatches"][0]["reason"], result
    assert cycle == 1
    state = json.loads((fdir / "state.json").read_text(encoding="utf-8"))
    assert state["cycle"] == 1 and state["phase"] == "F3"
    assert "inspect_modes" not in state


def test_inspect_start_still_advances_on_the_same_log_without_the_claim(
    _d200_run_env,
):
    """D-205 shape A at the boundary — the true accept the refusal must keep.

    Byte-identical to the test above but for the one committed line. If the
    narrowed grammar refused this too, the fix would have closed the door on
    every honest log instead of on the forgery, and the counter would never
    advance again.
    """
    result, cycle, _ = _drive_d205_boundary(
        _d200_run_env, _d205_boundary_log("")
    )

    assert result.get("ok") is True, result
    assert result["phase"] == "F2"
    assert result["cycle"] == 2
    assert cycle == 2


_D205_HASH_LEADING_CAPTURE = (
    "# guard: refuse on mismatch, never on shape\n"
    "# see D-198 for why punctuation is not the discriminator\n"
    + _D200_DOOR_TAIL
)


def test_inspect_start_refuses_a_forged_line_above_a_hash_leading_capture(
    _d200_run_env,
):
    """D-205's SECOND accepted shape, at the boundary.

    A forged `#` line placed ABOVE a capture whose own output legitimately
    begins with `#` lines — D-198's subject. The capture's leading run is
    still a line-aligned SUFFIX of the committed one, which is exactly what
    the old accept branch tested, so the forged line was discarded and the log
    "reproduced". Under the header grammar only the directives and one
    separator were ever header, so the forged line is body and is compared.
    """
    log_text = _d205_boundary_log(
        "", tail=f"# {_D205_CLAIM}\n" + _D205_HASH_LEADING_CAPTURE
    )
    result, cycle, _ = _drive_d205_boundary(
        _d200_run_env, log_text, capture=_D205_HASH_LEADING_CAPTURE
    )

    assert result.get("ok") is not True, result
    assert "casting-1-handler.log" in result["error"], result
    assert "FABRICATED" in result["mismatches"][0]["reason"], result
    assert cycle == 1


def test_inspect_start_advances_on_an_honest_hash_leading_capture(
    _d200_run_env,
):
    """The true accept the shape above must not have taken with it.

    D-198 established that a captured body legitimately beginning with `#`
    lines is honest evidence. It still reproduces: those lines are compared on
    both sides and agree, rather than vanishing from both.
    """
    log_text = _d205_boundary_log("", tail=_D205_HASH_LEADING_CAPTURE)
    result, cycle, _ = _drive_d205_boundary(
        _d200_run_env, log_text, capture=_D205_HASH_LEADING_CAPTURE
    )

    assert result.get("ok") is True, result
    assert result["cycle"] == 2 and cycle == 2


def test_an_unknown_evidence_directive_is_body_not_header():
    """D-205's grammar is the KNOWN directive set, not the `evidence-*` shape.

    `_parse_evidence_header` silently ignores a directive it does not know, so
    an unknown one is unread text. A grammar keyed on the SHAPE would have
    discarded `# evidence-summary: all 47 assertions passed` unread — the same
    hole one notch narrower, reachable by anyone who reads the regex.
    """
    smuggled = (
        "# evidence-cmd: cat replay.txt\n"
        "# evidence-summary: all 47 assertions passed\n"
        "\n"
        "REAL_TAIL\n"
    )
    assert evidence._is_directive_line("# evidence-cmd: x\n") is True
    assert evidence._is_directive_line("# evidence-summary: x\n") is False
    matched, _ = _d200_compare(smuggled, "REAL_TAIL\n")
    assert matched is False
    # The parser agrees it never read the line: the unknown directive reaches
    # no key of the parsed header, which is what makes it body.
    parsed = evidence._parse_evidence_header(smuggled)
    assert set(parsed) == {"cmd", "volatile", "timeout", "evidence_for"}
    assert parsed["cmd"] == "cat replay.txt"


# --------------------------------------------------------------------------- #
# D-198 / FR-010 / CT-015 / AC-015 — the stub detector discriminates
# fabrication, not punctuation.
#
# `_strip_header_and_blank_lines` dropped every line whose lstrip started with
# `#`, anywhere in the file, and both stub rules then judged a body missing its
# captured lines. Driven end to end at the MCP door on two runs identical but
# for three characters. The command in both:
#
#     sed -n '1,3p' src/guard.py && printf '<three ISO stamps>'
#
# — a teammate showing the source it changed plus three stamps. Run A, where
# guard.py's first three lines are comments in this codebase's own house style:
# ACCEPTANCE REFUSED, EVIDENCE_STUB_DETECTED / EVIDENCE_STUB_TIMESTAMP_CLUSTER,
# because a six-line body of which three are timestamps (50%) was read as a
# three-line body of which three are timestamps (100%) — the strip deleted the
# denominator. Run B, the same three lines with the leading `# ` removed and
# nothing else changed: accepted. The bare-ack arm failed the same way: nine
# comment lines then `PASS` fullmatched `_STUB_BARE_ACK_RE` on a body reduced
# to the word `PASS`.
#
# Not hypothetical on this corpus: 15 of the run's 70 committed logs already
# have a majority-`#` body under the old strip, and none of them trips a
# pattern only because of what the rest of their bodies happen to hold.
#
# THE TRUE POSITIVES THE NARROWING KEEPS
# --------------------------------------
# A genuine bare-ack stub (header, then `PASS`) and a genuine timestamp cluster
# (header, then only timestamps) have no `#` line below the separator to
# restore, so both still fire with their own sub-token. Those two are pinned
# here beside the two false refusals this closes, at the unit surface and again
# at the wire, so a future widening of the strip cannot pass by making the
# detector blind instead of accurate.
# --------------------------------------------------------------------------- #

#: Run A: a `sed`-style capture whose first three lines are source comments,
#: then three timestamps. Real evidence, 50% timestamps.
_D198_RUN_A_BODY = (
    "# The guard's decoder table is its statement of which names a reader "
    "opens\n"
    "# AS a document -- one declaration, so the two cannot drift.\n"
    "# D-195: a dot is not a document type.\n"
    "2026-09-04T05:00:00\n"
    "2026-09-04T05:00:01\n"
    "2026-09-04T05:00:02\n"
)

#: Run B: byte-for-byte Run A with the three leading `# ` removed. The ONLY
#: difference between the two, and under the old strip the whole verdict.
_D198_RUN_B_BODY = _D198_RUN_A_BODY.replace("# ", "", 3)

#: A genuine bare-ack stub: the body really is one acknowledgement.
_D198_BARE_ACK_BODY = "PASS\n"

#: A genuine fabricated-bulk cluster: the body really is only timestamps.
_D198_TIMESTAMP_CLUSTER_BODY = "".join(
    f"2026-05-05T10:00:0{i}Z\n" for i in range(5)
)

#: Header prose, `#` lines ABOVE the blank separator. Two jobs: it is where a
#: real writer puts the log's explanation, and it carries every shape here past
#: EVIDENCE_STUB_MIN_BYTES so TOO_SMALL cannot pre-empt the rule under test —
#: a bare-ack body is 5 bytes, and a TOO_SMALL hit would pin nothing about the
#: bare-ack rule at all.
_D198_HEADER_PROSE = (
    "#\n"
    "# D-198 fixture. The lines below this block are captured output, and a\n"
    "# `#` among them is a comment the command PRINTED, not a directive this\n"
    "# file declares. The separator is the blank line, exactly as every\n"
    "# writer in this plugin emits it.\n"
)


def _d198_log(body: str) -> str:
    """A committed evidence file: directives, prose, separator, then body."""
    return (
        "# evidence-cmd: cat replay.txt\n"
        "# evidence-for: AC-023\n"
        + _D198_HEADER_PROSE
        + "\n"
        + body
    )


def test_a_hash_line_below_the_separator_is_captured_output_not_header():
    """D-198: the header is the CONTIGUOUS leading `#` run, and a `#` line in
    the captured body survives into what the stub rules judge.

    The old strip deleted both, so the two rules that divide by the body's
    length divided by the wrong number.
    """
    from foundry_mcp.tools.evidence import _strip_header_and_blank_lines

    body = _strip_header_and_blank_lines(_d198_log(_D198_RUN_A_BODY))

    # Every directive and prose line above the separator is gone...
    assert not any("evidence-cmd" in ln for ln in body), body
    assert not any("D-198 fixture" in ln for ln in body), body
    # ...and all six captured lines survive, comments included, so the
    # timestamp ratio is 3/6 rather than 3/3.
    assert body == _D198_RUN_A_BODY.splitlines(), body
    assert len(body) == 6 and sum(
        1 for ln in body if ln.startswith("2026-")
    ) == 3, body

    # No header at all: nothing to strip, blanks still dropped.
    assert _strip_header_and_blank_lines("a\n\nb\n") == ["a", "b"]


@pytest.mark.parametrize(
    "body, token",
    [
        pytest.param(_D198_RUN_A_BODY, None, id="run-A-comments-then-stamps"),
        pytest.param(_D198_RUN_B_BODY, None, id="run-B-plain-then-stamps"),
        pytest.param(
            "".join(f"# comment line {i} of the capture\n" for i in range(9))
            + "PASS\n",
            None,
            id="nine-captured-comments-then-PASS",
        ),
        pytest.param(
            _D198_BARE_ACK_BODY,
            "EVIDENCE_STUB_BARE_PASS",
            id="true-positive-bare-ack",
        ),
        pytest.param(
            _D198_TIMESTAMP_CLUSTER_BODY,
            "EVIDENCE_STUB_TIMESTAMP_CLUSTER",
            id="true-positive-timestamp-cluster",
        ),
    ],
)
def test_the_stub_rules_judge_the_captured_body_and_still_catch_real_stubs(
    body, token
):
    """D-198 both directions in one table.

    The first three rows are the false refusals the narrowing closes; the last
    two are the true positives it keeps. `cat` is deliberately non-vacuous
    (see the stub-library header), so rule 2 never fires here and each row
    lands on the rule it names.
    """
    assert evidence._check_stub_patterns(
        _d198_log(body), "cat replay.txt"
    ) == token


def _drive_accept_casting_over_mcp(arguments: dict) -> dict:
    """Call Foundry-Accept-Casting through the MCP REQUEST HANDLER.

    Not `_DISPATCH`, and not `server.call_tool`: the request handler is the
    transport a client reaches, and it validates arguments against the
    advertised `inputSchema` before dispatching.

    `Foundry-Accept-Casting` has no display formatter, so `format_result`
    falls through to indented JSON and the response IS the result with no
    `RESULT_JSON_MARKER` above it. Both shapes are read here rather than only
    the marked one, so registering a formatter for this tool later changes
    which branch runs and not whether the test can see the result.
    """
    import asyncio

    from mcp import types

    import foundry_mcp.server as srv
    from foundry_mcp.tools.display import RESULT_JSON_MARKER

    handler = srv.server.request_handlers[types.CallToolRequest]
    request = types.CallToolRequest(
        method="tools/call",
        params=types.CallToolRequestParams(
            name="Foundry-Accept-Casting", arguments=arguments
        ),
    )
    text = asyncio.run(handler(request)).root.content[0].text
    plain = re.sub(r"\x1b\[[0-9;]*m", "", text)
    if RESULT_JSON_MARKER in plain:
        _, _, plain = plain.partition(RESULT_JSON_MARKER + "\n")
    return json.loads(plain)


@pytest.mark.parametrize(
    "body, sub_token",
    [
        pytest.param(_D198_RUN_A_BODY, None, id="run-A-comments-then-stamps"),
        pytest.param(_D198_RUN_B_BODY, None, id="run-B-plain-then-stamps"),
        pytest.param(
            _D198_BARE_ACK_BODY,
            "EVIDENCE_STUB_BARE_PASS",
            id="true-positive-bare-ack",
        ),
        pytest.param(
            _D198_TIMESTAMP_CLUSTER_BODY,
            "EVIDENCE_STUB_TIMESTAMP_CLUSTER",
            id="true-positive-timestamp-cluster",
        ),
    ],
)
def test_the_acceptance_door_refuses_stubs_and_not_honest_comment_output(
    tmp_path, body, sub_token
):
    """D-198 at the wire — FR-010's converse.

    "No acceptance without EVID-01/EVID-02 running" is not satisfied by a door
    that also refuses evidence which ran, reproduces byte-identically and is
    honest. Driven through the MCP request handler on the SAME repo shape with
    only the committed body changed: Run A and Run B are both accepted, and
    both genuine stubs are still refused with EVIDENCE_STUB_DETECTED naming
    their own sub-token in `failure_detail`.
    """
    from foundry_mcp import server
    from foundry_mcp.tools.foundry_state import clear_active_run

    env = _build_divergent_spec_repo(
        tmp_path, evidence_body=body, header_prose=_D198_HEADER_PROSE
    )
    server_project_root = server._project_root
    server._project_root = str(env["project_root"])
    try:
        result = _drive_accept_casting_over_mcp({
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

    if sub_token is None:
        assert result["evidence_verdict"] == "accepted", result
        assert result["evidence_tally"]["rejected"] == 0, result
        assert result["ok"] is True, result
    else:
        assert result["ok"] is False, result
        assert result["failure_token"] == "EVIDENCE_STUB_DETECTED", result
        assert sub_token in result["failure_detail"], result


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
    default ``casting-`` and Phase 7's ``test_deriver.derive_and_run_tests``
    passes
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
# D-135 shipped a token test of exactly `t.isalpha() or t.isdigit()` — the
# helper is gone, so there is no name here to look up — and refused when
# either side was true. That is a denylist of two shapes with
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


def _corpus_witness_fields_by_log() -> dict:
    """Every ``(key_context, token)`` the corpus erases, BY the log that erases it.

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

    D-051: the ATTRIBUTION is the other half, and it is why this returns a
    mapping rather than one flat set. A flat set answers "does the corpus
    witness this shape somewhere", which is not the question ``witness`` asks —
    ``witness`` names ONE log, and a name nothing resolves is a name that can
    rot in silence. Keyed by log name, the sweep can hold each entry to the
    log it actually cites.
    """
    import re as _re

    by_log: dict = {}
    evidence_dir = REPO_ROOT / "evidence"
    for log in sorted(evidence_dir.glob("*.log")):
        text = log.read_text(encoding="utf-8")
        body = evidence._strip_leading_header_block(text)
        fields = set()
        for pattern in evidence._parse_evidence_header(text).get("volatile", []):
            try:
                matches = list(_re.finditer(pattern, body))
            except _re.error:
                continue
            for match in matches:
                tokens = match.group(0).split()
                for index, token in enumerate(tokens):
                    fields.add((" ".join(tokens[:index]), token))
        by_log[log.name] = fields
    return by_log


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

    D-051 added the NAME. Until it, the corpus branch never read ``witness`` at
    all — it asked whether SOME committed log erased the shape, so the name
    beside the shape was prose, and four entries went on citing
    `casting-1-pytest.log`, `casting-3-observations.log` and
    `casting-8-suite.log` for cycles after the tree stopped holding them. The
    protocol branch had always resolved its witness; the corpus branch now does
    the same, in the two directions a pointer can rot: the named log is gone,
    or the named log is committed but its own declarations no longer erase this
    shape. Sharing one log between entries stays legal — the key half already
    tells the fields apart — but citing a log that does not witness you does
    not.
    """
    corpus_by_log = _corpus_witness_fields_by_log()
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
            under_key = (
                f" under the key {grammar.key.pattern!r}"
                if grammar.key is not None
                else ""
            )
            witness_fields = corpus_by_log.get(grammar.witness)
            if witness_fields is None:
                offenders.append(
                    f"{name}: declares the corpus witness {grammar.witness!r}, "
                    f"which evidence/ no longer holds — repoint it at a log the "
                    f"tree carries today whose own patterns erase a token of "
                    f"this shape{under_key}"
                )
            elif not any(
                grammar.token.fullmatch(tok)
                and (grammar.key is None or grammar.key.search(context))
                for context, tok in witness_fields
            ):
                offenders.append(
                    f"{name}: declares the corpus witness {grammar.witness!r}, "
                    f"which is committed but whose own patterns no longer erase "
                    f"a token of this shape{under_key}"
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


def test_every_grammar_admits_its_witness_pair_and_refuses_its_falsifier():
    """DERIVED MEMBERSHIP again, this time over each entry's OWN two triples.

    `_EnvironmentalGrammar` documents `witness_pair` as "a REAL disagreement
    this grammar must ADMIT" and `falsifier` as "the same triple with the
    grammar's identifier removed, which the whole registry must REFUSE", and
    says the registry sweep drives both. The sweep above drives neither — it
    confirms a witness EXISTS in the corpus, which is a different claim — so
    the two fields were prose until something ran them.

    They are run here through `_field_disagreement_problem`, the guard's own
    door, so an entry that is too wide (its falsifier gets admitted) or too
    narrow (its own witness gets refused) turns this red rather than surfacing
    as a sweep refusal on someone's evidence log. That is not hypothetical:
    `archive_root` was added because a suite log declaring
    `/[^ ]*/foundry-archive/[^ ]*` could be re-captured any number of times and
    never re-execute — `foundry-archive/` is git-ignored, so the skip naming it
    appears ONLY in the detached worktree a sweep re-executes in, and the
    registry had no grammar to admit the path that moved with it.

    Membership is read off the registry, so a grammar added tomorrow is driven
    the day it lands."""
    offenders: list[str] = []
    for name, grammar in evidence._ENVIRONMENTAL_GRAMMARS.items():
        context, side_a, side_b = grammar.witness_pair
        admitted = evidence._field_disagreement_problem(
            f"<{name}>", "log", f"{context} {side_a}".strip(),
            "capture", f"{context} {side_b}".strip(),
        )
        if admitted is not None:
            offenders.append(f"{name}: refuses its own witness pair — {admitted}")
        context, side_a, side_b = grammar.falsifier
        refused = evidence._field_disagreement_problem(
            f"<{name}>", "log", f"{context} {side_a}".strip(),
            "capture", f"{context} {side_b}".strip(),
        )
        if refused is None:
            offenders.append(
                f"{name}: the WHOLE registry admits its falsifier "
                f"{side_a!r}/{side_b!r}, so some grammar is wider than the "
                f"thing keeping it alive"
            )
    assert offenders == [], offenders


def test_the_checkout_root_moves_but_the_claim_beside_it_does_not():
    """`archive_root`, stated as the property rather than as a table row.

    The line is `thunder-viper archive not present in this checkout: <path>`.
    What the environment varies is the checkout; what the command REPORTED is
    that the archive is absent, and that half is byte-identical on both sides
    and must stay visible. So the relocated path is admitted with the
    `/foundry-archive/` anchor in the token, and refused without it — a bare
    `/x/y` says nothing about whether it is where the run happened or what the
    run found."""
    anchored = evidence._field_disagreement_problem(
        r"/[^ ]*/foundry-archive/[^ ]*",
        "log", "/private/tmp/c3wt/foundry-archive/thunder-viper",
        "capture", "/private/tmp/other/wt/foundry-archive/thunder-viper",
    )
    assert anchored is None, anchored

    bare = evidence._field_disagreement_problem(
        r"/[^ ]*",
        "log", "/private/tmp/c3wt/thunder-viper",
        "capture", "/private/tmp/other/wt/thunder-viper",
    )
    assert bare is not None and "not field-shaped" in bare


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


def test_a_corpus_witness_pointer_must_name_a_log_that_still_witnesses_it(
    monkeypatch,
):
    """D-051: the NAMED witness, not merely SOME witness.

    The plant that would have caught the defect. Before this rung the corpus
    branch never read ``witness``: it asked whether SOME committed log erased a
    token of the shape, and 40-odd logs erase a duration, so `duration_seconds`
    could go on citing `casting-1-pytest.log` — a log the tree had not held for
    cycles — and the sweep stayed green. `pytest_rootdir`, `planning_root` and
    `archive_root` rotted the same way behind the same green. That is the
    registry-coherence half of GI-006: the corpus stays re-executable, and the
    pointers INTO it stay resolvable, or the provenance the block comment calls
    "checked, not asserted" is asserted after all.

    Two plants, because a pointer rots in two directions, and BOTH were green
    under the old rung:

      1. it names a log the corpus no longer holds, while the shape it claims
         is witnessed by plenty of siblings — exactly D-051's shape;
      2. it names a log the corpus DOES hold, which does not declare this shape
         — a live pointer aimed at the wrong log, which reads as provenance and
         proves nothing.

    Everything but the pointer is the real entry (``dataclasses.replace`` over
    a registry member), so a green here cannot come from a plant that was
    unwitnessable for some other reason. The anchor grammar and the
    non-witnessing log are both DERIVED from what ships — hardcoding either
    would plant, in the regression test for stale pointers, a stale pointer.
    """
    if not (REPO_ROOT / "evidence").exists():
        pytest.skip("no committed evidence corpus")

    anchor = next(
        (
            name
            for name, g in evidence._ENVIRONMENTAL_GRAMMARS.items()
            if g.witness_kind == "corpus"
        ),
        None,
    )
    assert anchor is not None, "the registry declares no corpus witness at all"
    real = evidence._ENVIRONMENTAL_GRAMMARS[anchor]

    # Direction 1 — a name evidence/ does not hold.
    dead = "casting-0-this-log-was-never-committed.log"
    assert not (REPO_ROOT / "evidence" / dead).exists(), (
        f"{dead} exists, so it cannot stand in for a retired log"
    )
    planted = dict(evidence._ENVIRONMENTAL_GRAMMARS)
    planted[anchor] = dataclasses.replace(real, witness=dead)
    monkeypatch.setattr(evidence, "_ENVIRONMENTAL_GRAMMARS", planted)
    _, offenders = _grammar_witness_sweep()
    assert any(anchor in o and dead in o for o in offenders), (
        f"a witness naming a log the corpus no longer holds went unreported "
        f"— this is D-051 exactly: {offenders}"
    )

    # Direction 2 — a name evidence/ DOES hold, which does not witness it.
    by_log = _corpus_witness_fields_by_log()
    wrong = next(
        (
            log_name
            for log_name in sorted(by_log)
            if not any(real.token.fullmatch(tok) for _, tok in by_log[log_name])
        ),
        None,
    )
    if wrong is None:
        pytest.skip(
            f"every committed log erases a {anchor!r}-shaped token, so the "
            f"corpus offers no live-but-wrong pointer to plant"
        )
    planted = dict(evidence._ENVIRONMENTAL_GRAMMARS)
    planted[anchor] = dataclasses.replace(real, witness=wrong)
    monkeypatch.setattr(evidence, "_ENVIRONMENTAL_GRAMMARS", planted)
    _, offenders = _grammar_witness_sweep()
    assert any(anchor in o and wrong in o for o in offenders), (
        f"a witness naming a committed log that does not declare this shape "
        f"went unreported: {offenders}"
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
        witness="casting-5-both-doors.log",  # D-051: was casting-1-pytest.log
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
    ``tests/orchestration/test_module_boundaries.py``; that file's ``_scan``
    docstring records the ``(seen, offenders)`` tuple as the agreed contract
    ACROSS test modules, so the shape is re-declared here rather than imported
    across test files.
    """
    return sorted(root.rglob("*.py"))


def _scan(modules: list, rule) -> tuple:
    """Run one ``(seen, offenders)`` rule over a corpus and union both halves.

    D-142's shape. ``seen`` names every site the rule's recogniser identified
    as a MEMBER of the class it polices — offending or not — because
    ``assert not offenders`` is green in two different worlds: the one where
    the corpus is clean, and the one where the derivation has quietly stopped
    recognising the corpus's spelling. Callers assert against ``seen`` to tell
    the two apart by name. ``tests/orchestration/test_module_boundaries.py``
    records this tuple as the agreed contract across test modules.
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
    from tests.orchestration.test_module_boundaries import (
        _UNRESOLVED,
        _resolve_dotted,
    )

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
    from tests.orchestration.test_module_boundaries import (
        _UNRESOLVED,
        _resolve_dotted,
    )

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
    from tests.orchestration.test_module_boundaries import _module_namespace

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
    is imported solely inside ``intent_coverage._run_validator_in_process``,
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

    Adjacent path 1 — the SAME handler WITHOUT ``casting_commit``, which never
    reaches the evidence block and returns through a different branch. That
    branch used to be the backwards-compat shim's `ok: True`; since CT-015 made
    the parameter required it is the named refusal. Either way it is a
    DIFFERENT return path than the accepting one above, which is the property
    this adjacent path was chosen for, so the path still covers what it was
    written to cover.

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
    # Non-vacuity for adjacent path 1: it really did return through the
    # non-evidence branch, which since CT-015 is the named refusal rather than
    # the retired shim. Asserted rather than assumed — a call that had somehow
    # reached the evidence block would prove nothing about the branch this
    # adjacent path exists to cover.
    assert no_commit["ok"] is False, no_commit
    assert no_commit["field"] == "casting_commit", no_commit
    assert "evidence_verdict" not in no_commit, no_commit
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

    # `foundry_orchestrator.py` was one of the seven, and the carve deleted it.
    # The entry is REPOINTED, not dropped: the two modules that now hold the
    # code D-150 was filed against are `orchestration/gates.py` (which carries
    # the `_REQ_ID_RE = REQUIREMENT_ID_RE` alias the monolith's copy became) and
    # `orchestration/directives.py` (the `REQUIREMENT_ID_RE.findall(directive)`
    # site). A dropped entry would let the scan stop reaching them and still
    # report a clean zero, which is the exact failure this list exists to name.
    filed_on = [
        "foundry_handoff.py", "evidence.py", "foundry_validate.py",
        "gates.py", "directives.py", "foundry.py", "vocab.py",
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
    counted, cited and bound. Written against the exported pattern, so the swap
    of `foundry_validate.py` and of what was then the orchestrator monolith is
    checked by this test too -- neither of us can narrow the shared seam alone.
    The monolith's own copy is now `orchestration/gates.py#_REQ_ID_RE` and the
    `findall` site in `orchestration/directives.py`; the seam they read through
    is `foundry_mcp.schemas.vocab#REQUIREMENT_ID_RE`, unchanged by the carve.
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
            # A real commit, now that CT-015 makes the parameter required. The
            # fixture repo's HEAD is the casting's commit, so no new machinery
            # is needed — and the subject of this test is unchanged: the
            # undecodable-prompt refusal answers one rung BEFORE any evidence
            # work, so supplying a commit cannot make it pass by another route.
            casting_commit=env["casting_commit"],
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


# --------------------------------------------------------------------------- #
# GI-002 / ST-005 / CT-007 / AC-013 / AC-014 / FR-009 / FR-031 / FR-042 /
# OT-008 / OT-016 — the GRIND-boundary evidence sweep.
#
# The sweep is a new CALLER of the machinery above, not a new engine, so these
# tests drive it against a REAL git repo with a REAL committed corpus and
# REAL re-execution. Nothing here stubs the comparator: the property under test
# is "does the committed corpus still reproduce at HEAD", and only running it
# can show that.
#
# The harness lives here rather than in conftest.py. `run_accept_casting_with_
# evidence` is built around one casting's own commit and around
# `verify_evidence`'s v2.0 routing, neither of which the sweep has; casting 5
# does not own conftest.py, and duplicating that fixture's knobs into it to
# serve a different question would have made both harder to read.
# --------------------------------------------------------------------------- #

_SWEEP_MANIFEST = {
    "castings": [
        {
            "id": 1,
            "key_files": ["src/alpha.py", "tests/test_alpha.py"],
            "spec_text": "- **CT-007**: the sweep re-executes at HEAD\n",
        },
        {
            "id": 2,
            "key_files": ["src/beta.py"],
            "spec_text": "- **CT-014**: the report carries every section\n",
        },
        {
            # A directory key_file, spelled with a trailing slash exactly as
            # casting 5's own manifest entry spells its fixture directory. A
            # diff touching a file INSIDE it must count as touching casting 3.
            "id": 3,
            "key_files": ["tests/fixtures/gamma/"],
            "spec_text": "- **AC-036**: the report names every section\n",
        },
    ]
}


def _build_sweep_repo(tmp_path: Path, *, logs: dict[str, str] | None = None) -> dict:
    """A repo whose committed `evidence/` corpus reproduces at HEAD.

    Each log is a `cat`-replay of its own body, which is what makes
    re-execution byte-exact without depending on anything outside the worktree.
    `cat` is emphatically not a vacuous command under the stub library's rule 2
    (D-062), but the sweep does not reach that library anyway — see the module
    comment on `sweep_evidence_at_head`.

    Returns the two paths the sweep takes plus the corpus directory, so a test
    can perturb one log and re-run.
    """
    project_root = tmp_path / "repo"
    (project_root / "src").mkdir(parents=True)
    (project_root / "tests" / "fixtures" / "gamma").mkdir(parents=True)
    _run_git(["init", "-q", "-b", "main"], project_root)

    for name in ("alpha", "beta"):
        (project_root / "src" / f"{name}.py").write_text(
            f"def {name}():\n    return {name!r}\n", encoding="utf-8"
        )
    (project_root / "tests" / "test_alpha.py").write_text(
        "def test_alpha():\n    assert True\n", encoding="utf-8"
    )
    (project_root / "tests" / "fixtures" / "gamma" / "rows.json").write_text(
        '{"rows": []}\n', encoding="utf-8"
    )

    evidence_dir = project_root / "evidence"
    evidence_dir.mkdir()
    # Imported from the module under test rather than re-spelled here: the
    # harness has to strip the header block EXACTLY as the comparator does, or
    # a replay file would differ from the captured body for a reason that is
    # the harness's fault and would read as a sweep defect.
    from foundry_mcp.tools.evidence import _strip_leading_header_block

    corpus = logs if logs is not None else _default_sweep_corpus()
    for filename, text in corpus.items():
        (evidence_dir / filename).write_text(text, encoding="utf-8")
        # The replay file each log's command cats back. Named after the log so
        # two logs can never replay each other's body.
        body = _strip_leading_header_block(text)
        (project_root / f"replay-{Path(filename).stem}.txt").write_text(
            body, encoding="utf-8"
        )

    _run_git(["add", "-A"], project_root)
    _run_git(["commit", "-q", "-m", "committed corpus"], project_root)

    run_dir = tmp_path / "run"
    run_dir.mkdir()
    return {
        "project_root": project_root,
        "run_dir": run_dir,
        "evidence_dir": evidence_dir,
        "manifest": _SWEEP_MANIFEST,
    }


def _sweep_log(stem: str, *, for_ids: str, cmd: str | None = None,
               body: str | None = None, extra_headers: str = "") -> str:
    """One evidence log whose command replays its own body."""
    command = cmd if cmd is not None else f"cat replay-{stem}.txt"
    text = body if body is not None else (
        f"[replay] {stem}\n"
        "collected 3 items\n"
        "\n"
        f"tests/test_{stem.split('-')[-1]}.py::test_one PASSED\n"
        f"tests/test_{stem.split('-')[-1]}.py::test_two PASSED\n"
        f"tests/test_{stem.split('-')[-1]}.py::test_three PASSED\n"
        "\n"
        "3 passed\n"
    )
    return (
        f"# evidence-cmd: {command}\n"
        f"# evidence-for: {for_ids}\n"
        f"{extra_headers}"
        "\n" + text
    )


def _default_sweep_corpus() -> dict[str, str]:
    return {
        # Keyed to casting 1 by BOTH sources: the filename convention and the
        # `# evidence-for:` header, whose CT-007 resolves through the manifest.
        "casting-1-alpha.log": _sweep_log("casting-1-alpha", for_ids="CT-007"),
        # Keyed to casting 2 by filename; its header names CT-014.
        "casting-2-beta.log": _sweep_log("casting-2-beta", for_ids="CT-014"),
        # Keyed to NEITHER by filename — the name is off-convention — and only
        # by its `# evidence-for: AC-036`, which resolves to casting 3.
        "wave-report-sections.log": _sweep_log(
            "wave-report-sections", for_ids="AC-036"
        ),
    }


def _sweep_scope_names(env: dict, touched: list, *, full: bool) -> list:
    from foundry_mcp.tools.evidence import select_sweep_scope

    return sorted(
        p.name
        for p in select_sweep_scope(
            manifest=env["manifest"],
            evidence_dir=env["evidence_dir"],
            touched_files=touched,
            full=full,
        )
    )


# --------------------------------------------------------------------------- #
# select_sweep_scope — WHICH logs run.
# --------------------------------------------------------------------------- #


def test_a_full_sweep_selects_every_committed_log(tmp_path):
    """GI-002 verbatim: '... and the whole corpus when the FULL rule fires or
    before ASSAY/NYQUIST/DONE.'

    `touched_files` is deliberately non-empty and irrelevant here: `full=True`
    is the decision the calling transition already made (GI-009 puts it there
    and nowhere else), so this function is told the answer and must not
    re-derive a narrower one from the diff."""
    env = _build_sweep_repo(tmp_path)
    assert _sweep_scope_names(env, ["src/alpha.py"], full=True) == [
        "casting-1-alpha.log", "casting-2-beta.log", "wave-report-sections.log",
    ]
    assert _sweep_scope_names(env, [], full=True) == [
        "casting-1-alpha.log", "casting-2-beta.log", "wave-report-sections.log",
    ]


def test_a_delta_sweep_selects_only_the_touched_castings_logs(tmp_path):
    """OT-016 verbatim: 'A DELTA sweep after a GRIND touching one file
    re-executes only the logs tied to that file's casting or referencing that
    file; the sweep before ASSAY re-executes every log.'

    FR-042's first arm: 'casting key_files intersect the diff'."""
    env = _build_sweep_repo(tmp_path)
    assert _sweep_scope_names(env, ["src/alpha.py"], full=False) == [
        "casting-1-alpha.log"
    ]
    assert _sweep_scope_names(env, ["src/beta.py"], full=False) == [
        "casting-2-beta.log"
    ]


def test_a_directory_key_file_is_matched_as_a_prefix(tmp_path):
    """A `key_files` entry may be a DIRECTORY, spelled with a trailing slash —
    casting 5's own manifest entry spells its fixture directory that way. A
    diff touching a file inside it must count as touching that casting;
    comparing the two as bare strings would miss every directory entry."""
    env = _build_sweep_repo(tmp_path)
    assert _sweep_scope_names(
        env, ["tests/fixtures/gamma/rows.json"], full=False
    ) == ["wave-report-sections.log"]
    # And a sibling directory that merely shares a prefix does NOT match.
    assert _sweep_scope_names(env, ["tests/fixtures/gamma-other/x.json"],
                              full=False) == []


def test_a_log_is_keyed_by_its_evidence_for_header_when_the_name_cannot(tmp_path):
    """FR-009 / the sweep-scope contract: 'A log is keyed to its casting by the
    `casting-{id}-*.log` filename convention and by the casting id its
    `# evidence-for:` header resolves to when that header is present.'

    `wave-report-sections.log` matches no filename convention at all. Its only
    key is `# evidence-for: AC-036`, resolved through casting 3's `spec_text`
    in the manifest — so a diff touching casting 3's key_files must still
    select it. One source is not enough, which is why both are used."""
    env = _build_sweep_repo(tmp_path)
    selected = _sweep_scope_names(env, ["tests/fixtures/gamma/rows.json"],
                                  full=False)
    assert selected == ["wave-report-sections.log"]

    # The falsifier: strip the header and the same diff selects nothing, so
    # the selection above cannot have come from the filename.
    #
    # The edit is COMMITTED, and that is not ceremony (D-016): the scope reads
    # the corpus at HEAD, so an uncommitted edit is not the corpus and would
    # correctly change nothing. `test_the_scope_reads_the_corpus_at_head_not_the_working_tree`
    # below drives that distinction directly.
    (env["evidence_dir"] / "wave-report-sections.log").write_text(
        _sweep_log("wave-report-sections", for_ids="AC-036").replace(
            "# evidence-for: AC-036\n", ""
        ),
        encoding="utf-8",
    )
    _run_git(["add", "-A"], env["project_root"])
    _run_git(["commit", "-q", "-m", "strip the evidence-for header"],
             env["project_root"])
    assert _sweep_scope_names(env, ["tests/fixtures/gamma/rows.json"],
                              full=False) == []


def test_a_delta_sweep_selects_a_log_whose_command_references_a_touched_file(tmp_path):
    """FR-042's second arm: '... or command references a touched file.'

    The log below belongs to no casting the diff touched — the diff is on
    `src/delta_only.py`, which is in nobody's key_files — and is selected
    solely because its command names that file."""
    corpus = _default_sweep_corpus()
    corpus["casting-9-cmdref.log"] = _sweep_log(
        "casting-9-cmdref",
        for_ids="OT-016",
        cmd="cat src/delta_only.py",
        body="def delta_only():\n    return 'delta'\n",
    )
    env = _build_sweep_repo(tmp_path, logs=corpus)
    selected = _sweep_scope_names(env, ["src/delta_only.py"], full=False)
    assert selected == ["casting-9-cmdref.log"]


def test_a_command_reference_matches_a_path_suffix_but_not_a_longer_name(tmp_path):
    """The boundary rule inside the command-reference test. Commands do not
    spell paths the way a diff does — the committed corpus is full of
    `cd plugins/foundry/mcp-server && pytest tests/test_vocab.py` where the
    diff says the full repo-relative path — so any trailing suffix counts. But
    the suffix has to begin at a path boundary, or a diff touching
    `evidence.py` would select every log whose command runs
    `tests/test_evidence.py`."""
    from foundry_mcp.tools.evidence import _sweep_command_references

    cmd = "cd plugins/foundry/mcp-server && pytest tests/test_evidence.py"
    assert _sweep_command_references(
        cmd, ["plugins/foundry/mcp-server/tests/test_evidence.py"]
    )
    assert not _sweep_command_references(
        cmd, ["plugins/foundry/mcp-server/src/foundry_mcp/tools/evidence.py"]
    )
    assert _sweep_command_references("grep -n x vocab.py", ["schemas/vocab.py"])
    assert not _sweep_command_references("grep -n x myvocab.py",
                                         ["schemas/vocab.py"])


def test_a_delta_sweep_over_an_untouched_tree_selects_nothing(tmp_path):
    """AC-014 verbatim: 'When the GRIND diff touches no casting key_files and
    no file referenced by a log's command, the sweep re-executes zero logs and
    records the delta scope.'

    Zero is a complete answer, not a degenerate one — it is the whole point of
    DELTA and the reason a GRIND cycle does not pay for a full corpus."""
    env = _build_sweep_repo(tmp_path)
    assert _sweep_scope_names(env, ["docs/README.md"], full=False) == []
    assert _sweep_scope_names(env, [], full=False) == []


def test_the_scope_reads_the_corpus_at_head_not_the_working_tree(tmp_path):
    """D-016 / GI-002 / ST-005: the sweep proves the COMMITTED corpus still
    reproduces, so the enumeration has to be at HEAD too.

    `select_sweep_scope` globbed the live tree while `sweep_evidence_at_head`
    compared inside a detached worktree at HEAD, so the two halves of one
    boundary disagreed about what the corpus IS. Driven the way it bites:
    delete a committed log from the working tree and the FULL scope went to
    ZERO, so the boundary returned `ok: True` having checked nothing."""
    env = _build_sweep_repo(tmp_path)
    (env["evidence_dir"] / "casting-1-alpha.log").unlink()
    (env["evidence_dir"] / "casting-2-beta.log").unlink()

    assert _sweep_scope_names(env, [], full=True) == [
        "casting-1-alpha.log", "casting-2-beta.log", "wave-report-sections.log",
    ], "a log removed from the working tree is still committed at HEAD"

    # And the sweep really does re-execute them: the committed bytes and the
    # command both come out of the worktree, so a working tree missing the log
    # entirely is no obstacle.
    result = _sweep(env, full=True)
    assert result["ok"] is True, result["mismatches"]
    assert sorted(Path(name).name for name in result["logs_reexecuted"]) == [
        "casting-1-alpha.log", "casting-2-beta.log", "wave-report-sections.log",
    ]


def test_a_log_in_an_evidence_subdirectory_is_in_scope(tmp_path):
    """D-016's second half: the old enumeration was `glob('*.log')`, which is
    FLAT, so a log under `evidence/<subdir>/` was in no scope ever — not even a
    FULL one. `ls-tree -r` is recursive, which is the whole fix."""
    env = _build_sweep_repo(tmp_path)
    nested = env["evidence_dir"] / "wave-2"
    nested.mkdir()
    (nested / "casting-6-nested.log").write_text(
        _sweep_log("casting-6-nested", for_ids="CT-007"), encoding="utf-8"
    )
    from foundry_mcp.tools.evidence import _strip_leading_header_block

    (env["project_root"] / "replay-casting-6-nested.txt").write_text(
        _strip_leading_header_block(
            _sweep_log("casting-6-nested", for_ids="CT-007")
        ),
        encoding="utf-8",
    )
    _run_git(["add", "-A"], env["project_root"])
    _run_git(["commit", "-q", "-m", "a nested evidence log"], env["project_root"])

    assert "casting-6-nested.log" in _sweep_scope_names(env, [], full=True)
    result = _sweep(env, full=True)
    assert result["ok"] is True, result["mismatches"]


def test_the_delta_arm_recognises_a_directory_a_command_walks(tmp_path):
    """D-030 / FR-042's second arm, on the spellings the corpus actually uses.

    The reference test matched literal path suffixes only, so `pytest tests/`,
    a bare `pytest`, and `grep -r foo src/` all answered False — every one of
    which genuinely re-executes the changed surface. The delta arm therefore
    under-selected, and under-selection is the error this test exists to avoid:
    a log NOT swept that should have been is a broken artifact carried past the
    boundary that would have caught it."""
    from foundry_mcp.tools.evidence import _sweep_command_references

    touched = ["plugins/foundry/mcp-server/tests/test_vocab.py"]
    for cmd in (
        "cd plugins/foundry/mcp-server && pytest tests/",
        "cd plugins/foundry/mcp-server && uv run --with pytest pytest -q",
        "cd plugins/foundry/mcp-server && uv run --with pytest pytest -q "
        "-p no:cacheprovider tests",
        "grep -rn 'def test_' plugins/foundry/mcp-server/tests",
        "pytest",
    ):
        assert _sweep_command_references(cmd, touched), cmd

    # A walker pointed somewhere else does NOT reach it.
    assert not _sweep_command_references(
        "cd plugins/foundry/mcp-server && pytest src/", touched
    )


def test_a_cd_is_not_a_walk_root_on_its_own(tmp_path):
    """D-030's boundary, and the reason the two arms are different tests.

    `cd X && pytest tests/test_evidence.py` names its target exactly. Treating
    the `cd` operand as a walk root would make that command reference every
    file under `plugins/foundry/mcp-server`, which is the over-matching the
    original suffix rule was written to prevent. A `cd` sets the working
    directory; only a walker with NO path operand promotes it to a root."""
    from foundry_mcp.tools.evidence import _sweep_command_references

    cmd = "cd plugins/foundry/mcp-server && pytest tests/test_evidence.py"
    assert not _sweep_command_references(
        cmd, ["plugins/foundry/mcp-server/src/foundry_mcp/tools/evidence.py"]
    )
    assert _sweep_command_references(
        cmd, ["plugins/foundry/mcp-server/tests/test_evidence.py"]
    )


def test_the_delta_arm_expands_a_shell_glob_path_operand(tmp_path):
    """D-045 / FR-009 verbatim: 'any log whose command references a touched
    file'.

    D-030 named 'glob forms' among the shapes that 'all resolve to nothing' and
    closed only the directory-operand, bare-command and `grep -r` halves. Every
    command below answered False at the door, and each fails a DIFFERENT arm:
    `cat` and `wc` reach no walker at all, so only the literal-suffix arm ran
    and it searched the command text for a path a glob never spells; `pytest`,
    `grep` and `ruff` DO reach the walk-root arm, which then compared the
    operand `src/*.py` as a literal path segment that `src/mod.py` neither
    equals nor sits beneath. Under-selection is the error this predicate exists
    to avoid — a log not swept that should have been is a broken evidence
    artifact carried silently past the boundary that would have caught it."""
    from foundry_mcp.tools.evidence import _sweep_command_references

    for cmd, touched in (
        ("cat src/*.py", "src/mod.py"),
        ("pytest tests/*.py", "tests/test_x.py"),
        ("grep foo src/**/*.py", "src/a/b.py"),
        ("grep foo src/**/*.py", "src/b.py"),          # `**/` spans zero dirs
        ("wc -l evidence/*.log", "evidence/casting-5-x.log"),
        ("uv run pytest tests/test_*.py",
         "plugins/foundry/mcp-server/tests/test_vocab.py"),
        ("ruff check src/*.py", "src/mod.py"),
        ("wc -l evidence/casting-?-x.log", "evidence/casting-5-x.log"),
        ("wc -l evidence/casting-[0-9]-x.log", "evidence/casting-5-x.log"),
    ):
        assert _sweep_command_references(cmd, [touched]), (cmd, touched)

    # A glob that is a WALK ROOT reads the subtrees it names, so a file any
    # depth below a matched directory is referenced.
    assert _sweep_command_references(
        "pytest tests/*", ["plugins/x/tests/sub/test_y.py"]
    )

    # The separator rules are the shell's, not `fnmatch`'s: a `*` stays inside
    # one path segment, so a glob is still a discriminating test rather than a
    # DELTA scope that quietly became a FULL one.
    for cmd, touched in (
        ("cat src/*.py", "docs/README.md"),
        ("cat src/*.py", "src/mod.txt"),               # the extension holds
        ("cat src/*.py", "src/a/b.py"),                # `*` does not cross `/`
        ("wc -l evidence/casting-[!0-9]-x.log", "evidence/casting-5-x.log"),
    ):
        assert not _sweep_command_references(cmd, [touched]), (cmd, touched)

    # A flag's VALUE is not a path. `-k` takes a pytest name selector, and
    # reading it as a glob would select every log whose command filters by
    # name — over-selection wide enough to make DELTA meaningless.
    assert not _sweep_command_references(
        'pytest -k "test_*" tests/test_evidence.py', ["src/other.py"]
    )

    # An unlexable command fails OPEN on this rule rather than raising: the
    # literal arm still runs and a FULL sweep re-executes the log regardless.
    assert _sweep_command_references('echo "unbalanced src/mod.py',
                                     ["src/mod.py"])
    assert not _sweep_command_references('echo "unbalanced', ["src/mod.py"])


def test_a_glob_referenced_log_is_selected_and_re_executed(tmp_path):
    """D-045 through the doors the scope actually flows through.

    The test above drives the predicate; this drives what the predicate is FOR.
    The log belongs to no casting the diff touched — `src/alpha.py` is casting
    1's, and this log is casting 9's — so the command-reference arm is its only
    delta test, and the glob is the only thing in the command that names the
    touched file. Selection then has to survive the next rung: the scope is
    handed to `sweep_evidence_at_head`, which re-executes it in the bounded
    pool, so a log selected here is a log actually re-run at HEAD."""
    corpus = _default_sweep_corpus()
    corpus["casting-9-globref.log"] = _sweep_log(
        "casting-9-globref",
        for_ids="OT-016",
        cmd="cat replay-casting-9-globref.txt src/*.py",
        body="def alpha():\n    return 'alpha'\n"
             "def beta():\n    return 'beta'\n",
    )
    env = _build_sweep_repo(tmp_path, logs=corpus)
    # `cat replay… src/*.py` concatenates the replay body and both sources, so
    # the committed log has to carry all three or it would not reproduce.
    (env["evidence_dir"] / "casting-9-globref.log").write_text(
        "# evidence-cmd: cat replay-casting-9-globref.txt src/*.py\n"
        "# evidence-for: OT-016\n"
        "\n"
        "def alpha():\n    return 'alpha'\n"
        "def beta():\n    return 'beta'\n",
        encoding="utf-8",
    )
    (env["project_root"] / "replay-casting-9-globref.txt").write_text(
        "", encoding="utf-8"
    )
    _run_git(["add", "-A"], env["project_root"])
    _run_git(["commit", "-q", "--amend", "--no-edit"], env["project_root"])

    assert "casting-9-globref.log" in _sweep_scope_names(
        env, ["src/beta.py"], full=False
    ), "the glob operand names src/beta.py; nothing else in the command does"
    result = _sweep(env, full=False, touched=["src/beta.py"])
    assert result["ok"] is True, result["mismatches"]
    assert any(
        name.endswith("casting-9-globref.log") for name in result["logs_reexecuted"]
    ), result["logs_reexecuted"]


# --- D-184 / D-187: the sweep keys a log by what a casting DECLARES --------- #
#
# `_sweep_requirement_to_castings` resolved a log's `# evidence-for:` ids
# through a bare `REQUIREMENT_ID_RE.findall` over each casting's whole
# `<spec_requirements>` block — the exact full-text scan D-180 replaced at the
# acceptance gate and at the F0.9 validator, left standing here because this
# third reader was never migrated with them. An id merely QUOTED inside another
# requirement's prose keyed the log to the quoting casting, so a DELTA sweep
# re-executed logs FR-009's rule does not select.
#
# `_QUOTING_SWEEP_MANIFEST` is that shape in miniature: casting 2 declares
# CT-014 and quotes casting 3's AC-036 inside CT-014's own statement text.

_QUOTING_SWEEP_MANIFEST = {
    "castings": [
        {
            "id": 1,
            "key_files": ["src/alpha.py"],
            "spec_text": "- **CT-007**: the sweep re-executes at HEAD\n",
        },
        {
            "id": 2,
            "key_files": ["src/beta.py"],
            # One declaration, whose prose names another casting's requirement
            # as an example. AC-036 is declared NOWHERE in this block.
            "spec_text": (
                "- **CT-014** [derived from A-024]: the report carries every\n"
                "  section, so a run whose AC-036 sections are absent is\n"
                "  refused at the done transition.\n"
            ),
        },
        {
            "id": 3,
            "key_files": ["tests/fixtures/gamma/"],
            "spec_text": "- **AC-036**: the report names every section\n",
        },
    ]
}


def test_a_requirement_quoted_in_another_castings_prose_does_not_key_a_log(tmp_path):
    """FR-009 verbatim: 'Per cycle: re-execute only logs whose casting's
    key_files intersect the GRIND diff, plus any log whose command references a
    touched file.'

    `wave-report-sections.log` is off-convention, so its ONLY key is
    `# evidence-for: AC-036`. AC-036 is declared by casting 3 and quoted by
    casting 2, and the log's command names no file in either casting. A diff
    touching casting 2's key_files therefore satisfies neither arm of FR-009,
    and the log must stay out of scope; a diff touching casting 3's must still
    select it, or the fix would have bought correctness by losing the key
    entirely."""
    env = _build_sweep_repo(tmp_path)
    env["manifest"] = _QUOTING_SWEEP_MANIFEST

    assert _sweep_scope_names(env, ["src/beta.py"], full=False) == [
        "casting-2-beta.log"
    ], (
        "a log keyed only by an id casting 2 QUOTES was selected on casting 2's "
        "diff — neither FR-009 arm holds for it"
    )
    # The falsifier: the DECLARED key still works, so the header source is
    # narrowed rather than dropped.
    assert _sweep_scope_names(
        env, ["tests/fixtures/gamma/rows.json"], full=False
    ) == ["wave-report-sections.log"]
    # And FULL is untouched — the whole-corpus arm never consulted the mapping.
    assert _sweep_scope_names(env, ["src/beta.py"], full=True) == [
        "casting-1-alpha.log", "casting-2-beta.log", "wave-report-sections.log",
    ]


def test_the_sweep_resolves_a_header_id_to_the_declaring_casting_only(tmp_path):
    """D-184 at the mapping itself, where the widening starts.

    Driven on this run's own manifest the same way: `NFR-002` resolved to
    castings `{'5', '2'}` because casting 2's block quotes it once inside
    OT-005's statement text, and casting 5 — which declares it — found its own
    evidence keyed to a casting that does not own the requirement."""
    from foundry_mcp.tools.evidence import _sweep_requirement_to_castings

    mapping = _sweep_requirement_to_castings(_QUOTING_SWEEP_MANIFEST)

    assert mapping["AC-036"] == {"3"}, (
        f"AC-036 is declared by casting 3 and quoted by casting 2; the mapping "
        f"resolved it to {sorted(mapping['AC-036'])}"
    )
    assert mapping["CT-014"] == {"2"}
    assert mapping["CT-007"] == {"1"}
    # A-024 is an ANSWER id, not a requirement family, and never was in scope.
    assert set(mapping) == {"AC-036", "CT-014", "CT-007"}, sorted(mapping)


def test_all_three_readers_derive_one_owned_set_from_one_block(tmp_path):
    """D-184 / D-187 as AGREEMENT, which is the property that was actually
    lost — not "the sweep is right" but "the sweep, the acceptance gate and the
    F0.9 validator give ONE answer to one question."

    D-180 made the gate and the validator share `declared_requirement_ids` and
    pinned the two of them against one block. This casting's sweep was the
    third reader of the same question and kept the scan D-180 removed, so the
    rule had two owners and a survivor: the gate could demand evidence of
    casting 3 for AC-036 while the boundary re-executed casting 3's log on
    casting 2's diff, and nothing in the tree compared them.

    The validator is driven for real here. The gate's demanded set is
    `declared_requirement_ids` itself — `foundry_accept_casting` calls it and
    `test_a_requirement_quoted_in_another_requirements_prose_is_not_owned`
    drives that door — and the test below pins that no reader may re-derive it.
    """
    from foundry_mcp.tools.foundry import foundry_init
    from foundry_mcp.tools.foundry_handoff import declared_requirement_ids
    from foundry_mcp.tools.foundry_state import clear_active_run, set_active_run
    from foundry_mcp.tools.foundry_validate import foundry_validate_castings

    from foundry_mcp.tools.evidence import _sweep_requirement_to_castings

    quoting = next(
        c for c in _QUOTING_SWEEP_MANIFEST["castings"] if c["id"] == 2
    )
    block = quoting["spec_text"]

    # One spec naming both ids, so the validator's "covered" and "uncovered"
    # are complementary halves of a known whole and its answer reads as a SET.
    init = foundry_init(project_root=str(tmp_path))
    fdir = Path(init["foundry_dir"])
    (fdir / "spec.md").write_text(
        "# Spec\n\n"
        "- **CT-014**: the report carries every section.\n"
        "- **AC-036**: the report names every section.\n",
        encoding="utf-8",
    )
    (fdir / "castings").mkdir(parents=True, exist_ok=True)
    (fdir / "castings" / "manifest.json").write_text(
        json.dumps(
            {
                "spec_type": "GREENFIELD",
                "castings": [
                    {
                        "id": "2",
                        "title": "the quoting casting",
                        "spec_text": block,
                        "observable_truths": ["a", "b", "c"],
                        "key_files": ["src/beta.py"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "src").mkdir(parents=True, exist_ok=True)
    (tmp_path / "src" / "beta.py").write_text(
        "def beta():\n    return 'beta'\n", encoding="utf-8"
    )

    set_active_run(init["run_name"])
    try:
        validator = foundry_validate_castings(str(tmp_path))
    finally:
        clear_active_run()

    gate_demands = set(declared_requirement_ids(block))

    dim1 = validator["dimensions"]["requirement_coverage"]
    uncovered = {
        rid
        for issue in dim1["issues"]
        if issue["type"] == "uncovered_requirements"
        for rid in issue["ids"]
    }
    validator_credits = {"CT-014", "AC-036"} - uncovered

    mapping = _sweep_requirement_to_castings(
        {"castings": [{"id": "2", "key_files": ["src/beta.py"],
                       "spec_text": block}]}
    )
    sweep_keys = {rid for rid, ids in mapping.items() if "2" in ids}

    assert gate_demands == validator_credits == sweep_keys, (
        f"one block, three answers: the acceptance gate demands "
        f"{sorted(gate_demands)}, F0.9 credits {sorted(validator_credits)} and "
        f"the evidence sweep keys {sorted(sweep_keys)}"
    )
    assert sweep_keys == {"CT-014"}, sorted(sweep_keys)
    assert "AC-036" not in sweep_keys, (
        "all three agree, but on the OLD answer: AC-036 is quoted inside "
        "CT-014's prose and declared nowhere in this casting"
    )


def test_no_reader_of_the_owned_set_derives_it_inline():
    """The KEY LINK, asserted where its loss would be silent.

    D-180 pinned two modules against re-deriving the declared set inline, and
    the pin held — for those two. This module was the third reader and was
    outside it, which is precisely how the scan survived a defect filed against
    it. Extended to all three so a fourth reader cannot appear the same way."""
    from foundry_mcp.tools import evidence as evidence_module
    from foundry_mcp.tools import foundry_handoff as handoff_module
    from foundry_mcp.tools import foundry_validate as validate_module

    modules = (evidence_module, handoff_module, validate_module)
    for module in modules:
        source = Path(module.__file__).read_text(encoding="utf-8")
        assert "declared_requirement_ids" in source, Path(module.__file__).name

    # Defined ONCE. Asked of the AST rather than of the text, so the prose in
    # each module explaining the rejected reading cannot trip the pin.
    definitions = [
        node.name
        for module in modules
        for node in ast.walk(
            ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
        )
        if isinstance(node, ast.FunctionDef)
        and node.name == "declared_requirement_ids"
    ]
    assert definitions == ["declared_requirement_ids"], definitions

    # And the one place in THIS module that still scans a whole text for
    # requirement ids is the `# evidence-for:` header, which is a
    # comma-separated LIST and has no subject position to judge. Asked of the
    # AST for the same reason as above: the docstring that explains the
    # rejected reading names the call, and a text scan would count the prose.
    findall_args = [
        node.args[0].id if isinstance(node.args[0], ast.Name) else "<expr>"
        for node in ast.walk(
            ast.parse(
                Path(evidence_module.__file__).read_text(encoding="utf-8")
            )
        )
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "findall"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "_REQUIREMENT_ID_RE"
        and node.args
    ]
    assert findall_args == ["raw_val"], (
        f"a full-text requirement scan reappeared in evidence.py, over "
        f"{findall_args} — the only text this module may scan whole is the "
        f"`# evidence-for:` header value"
    )


def test_the_narrowed_delta_scope_still_re_executes_in_the_shared_pool(tmp_path):
    """The ADJACENT path to D-184 / D-187: what runs concurrently downstream of
    the scope decision.

    The defect was found in the SELECTION. What consumes that selection is
    `sweep_evidence_at_head`, which opens ONE detached worktree at HEAD and
    re-executes every selected log inside it in a bounded thread pool — so a
    change to which logs come out of `select_sweep_scope` changes what those
    workers share a worktree with. Narrowing the scope must still produce a
    real sweep of the logs that remain, not an empty pass: `ok: True` with an
    empty `logs_reexecuted` is exactly the shape a broken boundary wears.

    Driven on the quoting manifest, on casting 3's diff — the arm the fix
    KEEPS — so the pool is exercised on a scope that reached it through the
    changed mapping."""
    env = _build_sweep_repo(tmp_path)
    env["manifest"] = _QUOTING_SWEEP_MANIFEST

    result = _sweep(env, full=False, touched=["tests/fixtures/gamma/rows.json"])

    assert result["ok"] is True, result["mismatches"]
    assert result["scope_count"] == 1
    assert result["logs_reexecuted"] == ["evidence/wave-report-sections.log"]
    assert result["pool_size"] == 1, "one log, one worker — derived, not fixed"
    assert result["mismatches"] == []

    # And the arm the fix REMOVES costs the pool nothing at all: casting 2's
    # diff selects only casting 2's own log, so the log keyed through a
    # quotation is not carried into the worktree to be re-run for nothing.
    on_casting_2 = _sweep(env, full=False, touched=["src/beta.py"])
    assert on_casting_2["ok"] is True, on_casting_2["mismatches"]
    assert on_casting_2["logs_reexecuted"] == ["evidence/casting-2-beta.log"]

    # The FULL transition through the same caller never consults the mapping
    # at all, and still sweeps the whole committed corpus concurrently.
    every = _sweep(env, full=True)
    assert every["ok"] is True, every["mismatches"]
    assert sorted(Path(n).name for n in every["logs_reexecuted"]) == [
        "casting-1-alpha.log", "casting-2-beta.log", "wave-report-sections.log",
    ]
    assert every["pool_size"] >= 1


def test_select_sweep_scope_returns_sorted_paths(tmp_path):
    """A sweep result is read by a human diffing cycle N against cycle N-1. A
    set's iteration order would make two identical sweeps look different."""
    from foundry_mcp.tools.evidence import select_sweep_scope

    env = _build_sweep_repo(tmp_path)
    selected = select_sweep_scope(
        manifest=env["manifest"], evidence_dir=env["evidence_dir"],
        touched_files=[], full=True,
    )
    assert selected == sorted(selected)
    assert all(p.is_absolute() for p in selected)


def test_select_sweep_scope_on_a_missing_corpus_returns_nothing(tmp_path):
    """An evidence directory that is not there is not this function's refusal
    to make: it returns nothing and the caller's own sweep reports a corpus of
    zero, rather than a traceback crossing the MCP boundary."""
    from foundry_mcp.tools.evidence import select_sweep_scope

    assert select_sweep_scope(
        manifest={}, evidence_dir=tmp_path / "nope", touched_files=["a"],
        full=True,
    ) == []
    assert select_sweep_scope(
        manifest={"castings": "not a list"}, evidence_dir=tmp_path / "nope",
        touched_files=["a"], full=False,
    ) == []


# --------------------------------------------------------------------------- #
# sweep_evidence_at_head — the re-execution itself.
# --------------------------------------------------------------------------- #


def _sweep(env: dict, *, full: bool = True, touched: list | None = None, **kwargs):
    from foundry_mcp.tools.evidence import select_sweep_scope, sweep_evidence_at_head

    logs = select_sweep_scope(
        manifest=env["manifest"], evidence_dir=env["evidence_dir"],
        touched_files=touched or [], full=full,
    )
    return sweep_evidence_at_head(
        project_root=env["project_root"], run_dir=env["run_dir"], logs=logs,
        **kwargs,
    )


def test_the_sweep_re_executes_the_corpus_at_head_and_passes(tmp_path):
    """ST-005 verbatim: 'every evidence log in the sweep scope (delta by
    default; whole corpus when the FULL rule fires or before ASSAY, NYQUIST or
    DONE) re-executes byte-identical at HEAD in a detached worktree'.

    CT-007's output half: 'sweep result recorded per log with scope (delta or
    full) and elapsed seconds'."""
    env = _build_sweep_repo(tmp_path)
    result = _sweep(env, full=True)

    assert result["ok"] is True, result
    assert result["error"] is None
    assert result["scope_count"] == 3
    assert sorted(result["logs_reexecuted"]) == [
        "evidence/casting-1-alpha.log",
        "evidence/casting-2-beta.log",
        "evidence/wave-report-sections.log",
    ]
    assert result["mismatches"] == []
    assert result["elapsed_seconds"] >= 0.0
    assert result["pool_size"] >= 1
    # The commit it swept is named, so a reader can tell which tree passed.
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=env["project_root"],
        check=True, capture_output=True, text=True,
    ).stdout.strip()
    assert result["head_commit"] == head


def test_a_committed_log_that_no_longer_reproduces_is_named(tmp_path):
    """OT-008 verbatim: 'Foundry-Phase inspect_start on a tree whose committed
    evidence log no longer reproduces is refused naming that log and the cycle
    counter is unchanged.'

    AC-013's first half: '... refuses the transition naming any log whose
    output mismatches.' This casting owns the naming; casting 3 owns the
    refusal and the counter.

    Driven the way it actually happens: a later commit changes what the command
    emits while the committed log still holds the old bytes."""
    env = _build_sweep_repo(tmp_path)
    replay = env["project_root"] / "replay-casting-2-beta.txt"
    replay.write_text(
        replay.read_text(encoding="utf-8").replace("3 passed", "2 passed, 1 failed"),
        encoding="utf-8",
    )
    _run_git(["add", "-A"], env["project_root"])
    _run_git(["commit", "-q", "-m", "a GRIND cycle broke casting 2's evidence"],
             env["project_root"])

    result = _sweep(env, full=True)

    assert result["ok"] is False, result
    assert result["error"] is None, "this is a mismatch, not a sweep that could not run"
    assert [m["log"] for m in result["mismatches"]] == ["evidence/casting-2-beta.log"]
    mismatch = result["mismatches"][0]
    assert mismatch["failure_token"] == "EVIDENCE_OUTPUT_MISMATCH"
    assert "2 passed, 1 failed" in mismatch["reason"]
    # The other two logs still reproduced and are not implicated.
    assert len(result["logs_reexecuted"]) == 3


def test_the_sweep_refuses_an_unparseable_command_before_running_it(
    tmp_path, monkeypatch
):
    """OT-034 verbatim: 'The sweep refuses an unparseable command with
    EVIDENCE_COMMAND_SYNTAX before executing it.'

    CT-015's error column, and the word BEFORE is the whole of it. Both halves
    are asserted, because only one of them is visible in the result:

      * the token is EVIDENCE_COMMAND_SYNTAX and not EVIDENCE_EXIT_NONZERO.
        That distinction is the observable difference between parsing first and
        running first — a shell handed a broken script exits 2, so a sweep that
        executed would have refused this same log under the exit-code token and
        told the operator "your command failed" for what the shell had already
        diagnosed as a typo, in a sentence it printed and the sweep discarded.
      * the runner was never reached. Spied rather than inferred: a command that
        `_run_command_with_timeout` never sees is a command nothing could have
        executed, at any content. The spy is the only way to tell "parsed first"
        from "ran and happened to fail the same way".

    And the refusal is PER LOG. The good log in the same corpus still runs and
    still matches, so one unparseable command cannot take down a sweep — which
    matters because this sweep gates a phase transition for the whole run.
    """
    env = _build_sweep_repo(tmp_path, logs={
        "casting-1-alpha.log": _sweep_log("casting-1-alpha", for_ids="CT-007"),
        # `(` opens a subshell nothing closes. Chosen because a shell REPORTS
        # this one clearly on every implementation, and because the `touch`
        # before it would have run had anything executed the script at all.
        "casting-2-beta.log": _sweep_log(
            "casting-2-beta",
            for_ids="CT-015",
            cmd="touch never-created.txt && (",
        ),
    })

    real_runner = evidence._run_command_with_timeout
    ran: list[str] = []

    def _spy(*, cmd, cwd, timeout):
        ran.append(cmd)
        return real_runner(cmd=cmd, cwd=cwd, timeout=timeout)

    monkeypatch.setattr(evidence, "_run_command_with_timeout", _spy)

    result = _sweep(env, full=True)

    assert result["ok"] is False, result
    assert result["error"] is None, "this is a per-log refusal, not a dead sweep"
    assert [m["log"] for m in result["mismatches"]] == ["evidence/casting-2-beta.log"]

    mismatch = result["mismatches"][0]
    assert mismatch["failure_token"] == "EVIDENCE_COMMAND_SYNTAX", (
        "the sweep executed the command and reported the shell's exit code "
        "instead of parsing it first"
    )
    assert mismatch["failure_token"] in evidence.KNOWN_EVIDENCE_FAILURE_TOKENS
    # The refusal carries the shell's own complaint, not a rewrite of it.
    assert "casting-2-beta.log" in mismatch["reason"]
    assert "/bin/sh -n" in mismatch["reason"]
    assert "NOT executed" in mismatch["reason"]

    # BEFORE: the runner never saw it.
    assert "touch never-created.txt && (" not in ran, (
        "the unparseable command reached _run_command_with_timeout — the parse "
        "check is running after execution, not before it"
    )
    assert not (env["project_root"] / "never-created.txt").exists()

    # ...and the neighbouring log in the same corpus was unaffected.
    assert "cat replay-casting-1-alpha.txt" in ran
    assert sorted(result["logs_reexecuted"]) == [
        "evidence/casting-1-alpha.log", "evidence/casting-2-beta.log",
    ]


def test_the_acceptance_door_refuses_an_unparseable_command_before_running_it(
    tmp_path, monkeypatch
):
    """FR-002's "EVERY crossing", at the crossing the sweep is not (D-107).

    THE SWEEP IS ONE OF TWO SERVER-SIDE EXECUTORS, AND ONLY IT WAS PARSING.
    `_sweep_one_log` calls `_shell_parse_problem` before the runner;
    `_verify_one_evidence_file` — the door `verify_evidence` walks, which
    `foundry_handoff#foundry_accept_casting` calls at F1 acceptance — went from
    `header["cmd"]` straight to `_run_command_with_timeout`. So a casting could
    be REFUSED at the boundary sweep for a command that had already been RUN at
    its own acceptance, which is the same command reaching two doors that
    disagree about whether it may execute.

    Driven the way the damage happens rather than on a command that merely
    fails: the `touch` before the unclosed subshell is a side effect the shell
    performs and THEN abandons the script, so a door that executes leaves the
    file behind and reports EVIDENCE_EXIT_NONZERO. Both halves are asserted for
    the reason the sweep's own test states — the token alone cannot tell
    "parsed first" from "ran and happened to fail", so the runner is spied and
    the side effect is looked for on disk.
    """
    worktree = tmp_path / "worktree"
    (worktree / "evidence").mkdir(parents=True)
    witness = worktree / "the-acceptance-door-executed-it"
    log = worktree / "evidence" / "casting-5-unparseable.log"
    log.write_text(
        f"# evidence-cmd: touch {witness.name} && (\n"
        "# evidence-for: FR-002\n"
        "\n"
        "a body long enough to clear the stub library's 128-byte TOO_SMALL "
        "floor, so nothing but the parse check can be what refuses this log\n",
        encoding="utf-8",
    )

    ran: list[str] = []
    real_runner = evidence._run_command_with_timeout

    def _spy(*, cmd, cwd, timeout):
        ran.append(cmd)
        return real_runner(cmd=cmd, cwd=cwd, timeout=timeout)

    monkeypatch.setattr(evidence, "_run_command_with_timeout", _spy)

    record = evidence._verify_one_evidence_file(
        evidence_path=log, worktree_path=worktree, casting_commit="0" * 40,
    )

    assert record["verdict"] == "rejected", record
    assert record["failure_token"] == "EVIDENCE_COMMAND_SYNTAX", (
        "the acceptance door executed the command and reported the shell's "
        "exit code instead of parsing it first"
    )
    assert record["failure_token"] in evidence.KNOWN_EVIDENCE_FAILURE_TOKENS
    # The refusal names the log, the shell, and the fact that nothing ran —
    # the same three things the sweep's refusal names, so an operator reading
    # either door reads one sentence.
    assert "casting-5-unparseable.log" in record["failure_detail"]
    assert "/bin/sh -n" in record["failure_detail"]
    assert "NOT executed" in record["failure_detail"]

    # BEFORE: neither the runner nor the shell ever saw it.
    assert ran == [], f"the unparseable command reached the runner: {ran}"
    assert not witness.exists(), (
        "the leading `touch` ran, so the door executed a command it could not "
        "parse — 'it only failed to parse' is never 'it had no effect'"
    )


def test_a_parseable_command_still_reaches_the_acceptance_runner(tmp_path):
    """The other side: the new rung refuses SYNTAX and nothing else.

    A check that returned a problem for every command would satisfy the test
    above and break the door, so a well-formed command is driven through the
    same entry point and has to arrive at the comparison — which it can only do
    by having been executed.
    """
    worktree = tmp_path / "worktree"
    (worktree / "evidence").mkdir(parents=True)
    body = (
        "a body long enough to clear the stub library's 128-byte TOO_SMALL "
        "floor so the verdict below is decided by the comparison and not by "
        "the stub library\n"
    )
    log = worktree / "evidence" / "casting-5-parseable.log"
    log.write_text(
        "# evidence-cmd: python3 -c \"print(open('evidence/body.txt').read(), "
        "end='')\"\n"
        "# evidence-for: FR-002\n"
        "\n" + body,
        encoding="utf-8",
    )
    (worktree / "evidence" / "body.txt").write_text(body, encoding="utf-8")

    record = evidence._verify_one_evidence_file(
        evidence_path=log, worktree_path=worktree, casting_commit="0" * 40,
    )

    assert record["verdict"] == "accepted", record
    assert record["failure_token"] is None, record
    assert record["exit_code"] == 0, record


def test_both_server_side_doors_parse_with_the_same_one_lint():
    """GI-019's "server refuses at EVERY crossing", as a property of the code.

    The two tests above drive each door once. This one asserts they cannot come
    apart: both bodies name `_shell_parse_problem`, so a future edit that gives
    one door its own inline parse — a second `sh -n`, a dialect grep, a
    remembered try/except — leaves this red rather than leaving the two doors
    quietly judging commands by different rules.
    """
    for door in (
        evidence._verify_one_evidence_file,
        evidence._sweep_one_log,
    ):
        source = inspect.getsource(door)
        assert "_shell_parse_problem(" in source, (
            f"{door.__name__} does not reach the shared lint; a crossing that "
            "parses with anything else is a crossing that disagrees with the "
            "commit guard about which shell judges the command"
        )
        assert "EVIDENCE_COMMAND_SYNTAX" in source, (
            f"{door.__name__} parses but does not name the token, so its "
            "refusal reaches the operator unnamed"
        )


def test_the_syntax_check_never_executes_what_it_parses(tmp_path):
    """The property `-n` is carried for, driven directly on the helper.

    The sweep test above proves an UNPARSEABLE command does not run, which a
    check that forgot `-n` would also satisfy — a broken script exits without
    executing anything either way, so that drive alone cannot tell a parse from
    a run. This one hands the helper a command that is perfectly VALID and whose
    entire purpose is a side effect. `-n` is the only reason the side effect
    does not happen, so dropping the flag turns this test red immediately.
    """
    witness = tmp_path / "the-lint-executed-it"
    problem = evidence._shell_parse_problem(f"touch {witness}")

    assert problem is None, f"a valid command was reported unparseable: {problem}"
    assert not witness.exists(), (
        "_shell_parse_problem EXECUTED the command it was asked to parse — the "
        "`-n` flag is missing, and the lint is now running unreviewed commands "
        "at every crossing and at every commit"
    )


#: The population floor for the corpus-wide lint below (fallout D-166).
#:
#: A verdict is only as good as the population it was computed over, and the
#: rule below used to record one without the other: it asserted that the logs
#: it found all parsed, and "all of them" is true of one log and true of none
#: that carry a command. A corpus that lost 77 of its 78 logs would have gone
#: green here, and the committed witness log that renders the same sweep in
#: `evidence/casting-5-corpus-lint.log` would have reproduced byte-identically
#: while doing it. GI-006 -- "Run artefacts stay complete" -- is what makes
#: that a defect rather than a tolerance.
#:
#: A RATCHET, not an equality: growth is the normal state of this corpus and
#: pinning the exact count would turn every casting's new log into a failure
#: here. It may be RAISED when someone wants a tighter floor. It is never
#: lowered -- a corpus that shrank below it is the event this constant exists
#: to report, and editing the number to make the report go away is the one
#: response that is always wrong.
_CORPUS_POPULATION_FLOOR = 78


def test_every_committed_evidence_command_parses_under_the_host_shell():
    """AC-038 verbatim, over the corpus as it actually stands.

    'The lint passes on both fleet hosts for the existing corpus, including the
    `set -o pipefail` log, because `sh -n` judges syntax only.'

    Asserted LOG BY LOG over whatever `evidence/` holds, not against a
    remembered list: the corpus grows every casting, and a test naming the logs
    it knew about would go green over a corpus it had stopped reading. A log
    added tomorrow is judged the day it lands.

    Run on the host, so "both fleet hosts" is a property this suite re-decides
    wherever it runs rather than a claim about somebody else's machine.

    And judged against `_CORPUS_POPULATION_FLOOR`, so the verdict names the
    population it was computed over (fallout D-166). "Every log parsed" is a
    claim about a set, and until the floor landed nothing here said how big
    that set had to be -- so the rule could keep passing over a corpus that had
    quietly collapsed to a single log, which is the one circumstance in which
    its answer would be worthless.
    """
    evidence_dir = REPO_ROOT / "evidence"
    if not evidence_dir.exists():
        pytest.skip("no committed evidence corpus")

    logs = sorted(evidence_dir.glob("*.log"))
    assert logs, "the corpus is empty, so this rule is reading nothing"

    checked, failures = [], []
    for log in logs:
        header = evidence._parse_evidence_header(
            log.read_text(encoding="utf-8", errors="replace")
        )
        cmd = header.get("cmd")
        if cmd is None:
            continue
        checked.append(log.name)
        problem = evidence._shell_parse_problem(cmd)
        if problem is not None:
            failures.append(f"{log.name}: {problem}")

    assert checked, (
        "no committed log declared a `# evidence-cmd:` — the header parse is "
        "reading nothing and this rule would pass over any corpus at all"
    )
    assert len(checked) >= _CORPUS_POPULATION_FLOOR, (
        f"the lint ran over {len(checked)} commands from {len(logs)} committed "
        f"logs, below the floor of {_CORPUS_POPULATION_FLOOR}. Either the "
        f"corpus SHRANK or logs stopped declaring a `# evidence-cmd:`; either "
        f"way a green verdict over what is left says nothing about what was "
        f"lost. Raise the floor only to tighten it — never lower it to restore "
        f"green."
    )
    assert failures == [], (
        f"committed evidence commands do not parse under "
        f"{evidence._EVIDENCE_SHELL} -n, so the boundary sweep will refuse "
        f"them at the next crossing: {failures}"
    )


def test_the_lint_judges_syntax_only_so_a_pipefail_command_survives():
    """AC-038's 'because `sh -n` judges syntax only', as its own property.

    The corpus sweep above passes today for two different reasons that it
    cannot tell apart: the lint is genuinely syntax-only, or no committed log
    currently uses a construct that would expose the difference. That second
    world is real — `set -o pipefail` is the one dialect-sensitive construct in
    Foundry's history of this corpus (it opened the shared suite log), and there
    are runs where no log in the tree carries it.

    So the property is driven directly, on strings, rather than left to depend
    on which logs happen to be committed the day this runs. `-n` never reaches
    the `set`, which is exactly why a shell that would REJECT `pipefail` at run
    time still parses it — and why the lint may not be swapped for a dialect
    grep or a shellcheck that would have an opinion about it.
    """
    assert evidence._shell_parse_problem(
        "set -o pipefail; cd plugins/foundry/mcp-server && pytest -q 2>&1 | sed -E 's/a/b/'"
    ) is None, (
        "a `set -o pipefail` command was rejected — the lint has acquired an "
        "opinion about dialect, which AC-036 forbids and which would make the "
        "shared suite log uncapturable"
    )
    # The other side of the same coin: it still catches a real parse error, so
    # the test above is not passing because the lint accepts everything.
    assert evidence._shell_parse_problem("if [ 1 ; then") is not None


def test_no_header_directive_match_ever_spans_a_newline():
    """D-076: a directive is ONE LINE, and the grammar has to say so itself.

    `_EVIDENCE_HEADER_LINE_RE` is applied with `re.MULTILINE` to a multi-line
    block, and Python's `\\s` MATCHES `\\n`. Every `\\s*` in it was therefore free
    to walk off the end of its own line, and one of them did: a directive with
    an empty value took its value from the FOLLOWING line of the header. This
    drives the property rather than reading the pattern, so any future spelling
    that reintroduces a newline-crossing class fails here whatever it looks
    like.

    The probe is every known directive with an empty value, stacked, which is
    the exact arrangement that made the old pattern reach forward.
    """
    probe = (
        "# evidence-cmd:\n"
        "# evidence-volatile:\n"
        "# evidence-timeout:\n"
        "# evidence-for:\n"
        "# evidence-cmd: echo hi\n"
    )
    for match in evidence._EVIDENCE_HEADER_LINE_RE.finditer(probe):
        assert "\n" not in match.group(0), (
            f"a header directive match spans a newline, so an empty value is "
            f"read from the next line: {match.group(0)!r}"
        )


def test_an_empty_directive_value_is_skipped_not_read_from_the_next_line():
    """D-076's drive, at the parser.

    `# evidence-cmd:` followed by the real directive used to resolve to the
    WHOLE of that second line — `'# evidence-cmd: if [ 1 ; then'` — a string
    that is an inert shell comment. The sweep then ran a no-op, compared its
    empty output against the committed body and refused the crossing as an
    output mismatch, naming nothing about the typo that caused it.

    A value has to BEGIN on its directive's own line. A directive that carries
    none is not a match at all, so the scan continues and the first line
    actually carrying a value wins — which is also what the commit guard's
    Check 4 does, driven against this parser in `test_commit_guard.py`.
    """
    resolved = evidence._parse_evidence_header(
        "# evidence-cmd:\n# evidence-cmd: if [ 1 ; then\n\nbody\n"
    )
    assert resolved["cmd"] == "if [ 1 ; then", (
        "the empty directive swallowed the next line instead of being skipped"
    )
    assert evidence._shell_parse_problem(resolved["cmd"]) is not None, (
        "the command the parser now resolves has to be the one the sweep "
        "refuses; if it parses, the mis-resolution is still in place"
    )

    # A whitespace-only value is the same case with the mistake harder to see.
    assert (
        evidence._parse_evidence_header(
            "# evidence-cmd:   \n# evidence-cmd: echo hi\n\nbody\n"
        )["cmd"]
        == "echo hi"
    )
    # And a header carrying ONLY an empty directive resolves nothing, so the
    # caller reaches EVIDENCE_COMMAND_MISSING rather than running a comment.
    assert evidence._parse_evidence_header("# evidence-cmd:\n\nbody\n")["cmd"] is None


def test_every_line_the_reader_reads_is_a_line_the_writer_accounts_for():
    """The intra-module half of D-076's `two-grammars-one-rule`.

    `evidence.py` holds two directive patterns and they had drifted apart:
    `_EVIDENCE_DIRECTIVE_LINE_RE` (documented as "the WRITER'S grammar", used by
    `_is_directive_line` to decide which lines are header rather than captured
    body) is strictly line-oriented, while the reader's
    `_EVIDENCE_HEADER_LINE_RE` was not.

    They are not required to AGREE outright — the writer accounts for
    `# evidence-cmd:` as header text while the reader resolves no value from it,
    and that difference is deliberate. The load-bearing direction is the
    inclusion: every line the READER takes a value from must be a line the
    WRITER already calls header. The converse would put a line the parser read
    into the comparator's body and produce a mismatch nobody can explain.
    """
    lines = [
        "# evidence-cmd: echo hi\n",
        "#evidence-cmd:echo hi\n",
        "#\tevidence-cmd:\techo hi\n",
        "  # evidence-cmd: echo hi\n",
        "# evidence-volatile: \\d+ms\n",
        "# evidence-timeout: 300\n",
        "# evidence-for: FR-002\n",
        "# evidence-cmd: echo hi\r\n",
        "# evidence-cmd:\n",
        "# not a directive\n",
        "echo hi\n",
    ]
    for line in lines:
        match = evidence._EVIDENCE_HEADER_LINE_RE.match(line)
        read_by_parser = (
            match is not None and match.group(1) in evidence._KNOWN_HEADER_DIRECTIVES
        )
        if read_by_parser:
            assert evidence._is_directive_line(line), (
                f"the parser reads a value out of {line!r} but the writer's "
                f"grammar calls that line captured output, so the comparator "
                f"will judge a header line as body"
            )


def test_the_sweeps_lint_reaches_for_the_host_shell_and_nothing_else():
    """AC-036 verbatim: '`/bin/sh -n` on the host' — the lint uses the host's
    `/bin/sh` and nothing else; no shellcheck, no bashism grep.

    Read off the shipped source, because the behavioural tests above cannot see
    the difference: a lint that shelled out to `shellcheck` and fell back to
    `/bin/sh` would pass every one of them on a machine with no shellcheck
    installed, and change its verdict on a machine that has one.
    """
    source = inspect.getsource(evidence._shell_parse_problem)
    assert evidence._EVIDENCE_SHELL == "/bin/sh"
    assert "-n" in source and "-c" in source

    for second_opinion in ("shellcheck", "bash -n", "zsh", "checkbashisms"):
        assert second_opinion not in source, (
            f"the lint consults {second_opinion!r}. The shell that JUDGES an "
            f"evidence command has to be the shell that RUNS it, and the runner "
            f"is Popen(shell=True) — /bin/sh -c on POSIX."
        )


def test_the_runner_states_the_shell_the_lint_models():
    """OT-035's second clause and the File Change Map's '`executable` left as
    `/bin/sh` (documented)'.

    The lint's choice of shell is only verifiable if the LAUNCH says which shell
    it launches. `Popen(shell=True)` names no shell anywhere in its call, so a
    reader of `_run_command_with_timeout` had to know a CPython implementation
    detail to check that the guard and the sweep parse with the right one. The
    statement is pinned here and the absence of `executable=` is pinned with it,
    because the sentence is only true while that argument stays absent.
    """
    from foundry_mcp.tools import worktree_helpers

    fn = ast.parse(
        inspect.getsource(worktree_helpers._run_command_with_timeout)
    ).body[0]
    documentation = ast.get_docstring(fn) or ""
    # The docstring DISCUSSES `executable=` by name, so that a reader knows what
    # is deliberately absent — the same split
    # `test_commit_guard.py#_executable_lines` makes for the same reason. Only
    # what Python actually executes is scanned for it.
    executed = "\n".join(ast.unparse(node) for node in fn.body[1:])

    assert "shell=True" in executed
    assert "executable=" not in executed, (
        "an `executable=` argument now pins the shell at the launch, so the "
        "documented `/bin/sh` fact — and the two lints that model it — are "
        "describing a shell this call no longer uses"
    )
    assert "/bin/sh" in documentation, (
        "the launch does not state which shell it hands the command to, so the "
        "guard's and the sweep's `/bin/sh -n` is an assumption rather than a "
        "documented agreement"
    )
    assert "Popen(shell=True)" in documentation, (
        "the statement no longer says WHICH call the /bin/sh fact is about"
    )


def test_every_caller_of_the_runner_parses_the_command_first():
    """fallout FR-002 / GI-019 (D-107) — "server refuses at EVERY crossing", as
    a property of the tree rather than of the two crossings anyone remembered.

    D-107 was not a missing check so much as a missing RULE: the lint lived in
    `_sweep_one_log`, the discipline block in `worktree_helpers` described it as
    "the sweep's own", and `_verify_one_evidence_file` — the acceptance door,
    the other caller of the same runner — executed what it could not parse for a
    whole run. Nothing was wrong with either function on its own reading. What
    was missing was anything that looked at BOTH.

    So the rule is derived, never listed: every function in the shipped module
    that reaches `_run_command_with_timeout` must also reach
    `_shell_parse_problem`. A third executor added tomorrow is judged the day it
    lands, and it fails here rather than at whichever crossing first hands a
    typo to a shell.
    """
    tree = ast.parse(
        Path(evidence.__file__).read_text(encoding="utf-8")
    )

    def _names_called(node: ast.AST) -> set[str]:
        return {
            call.func.id
            for call in ast.walk(node)
            if isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
        }

    executors, unlinted = set(), []
    for func in ast.walk(tree):
        if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        called = _names_called(func)
        if "_run_command_with_timeout" not in called:
            continue
        executors.add(func.name)
        if "_shell_parse_problem" not in called:
            unlinted.append(func.name)

    # Named rather than counted: an AST walk that has gone blind returns an
    # empty set, and "no unlinted executors" is true of nothing at all.
    assert executors >= {"_sweep_one_log", "_verify_one_evidence_file"}, (
        f"the scan cannot see the two known executors, so it is proving "
        f"nothing about the tree: {sorted(executors)}"
    )
    assert unlinted == [], (
        f"function(s) that execute an evidence command without parsing it "
        f"first: {unlinted}. Every crossing refuses EVIDENCE_COMMAND_SYNTAX "
        f"before the runner, or the server refuses at some crossings and "
        f"discovers at the rest."
    )


def test_the_runner_documents_the_callers_that_must_lint():
    """The prose half of the rule above, kept honest by the rule above.

    `_run_command_with_timeout` cannot enforce anything — the lint is in its
    callers, by design, because only they can turn a parse failure into a named
    per-log refusal. What it CAN do is tell the next person adding a caller that
    the obligation exists, which is exactly what its discipline block failed to
    do when it named one caller and called the shared lint "the sweep's own".
    """
    from foundry_mcp.tools import worktree_helpers

    documentation = ast.get_docstring(
        ast.parse(
            inspect.getsource(worktree_helpers._run_command_with_timeout)
        ).body[0]
    ) or ""

    assert "_shell_parse_problem" in documentation, (
        "the launch does not tell a new caller that it must parse the command "
        "first, which is the omission D-107 was filed over"
    )
    for caller in ("_sweep_one_log", "_verify_one_evidence_file"):
        assert caller in documentation, (
            f"{caller} executes an evidence command and the launch does not "
            f"name it, so the enumeration is short by one again"
        )


# ---------------------------------------------------------------------------
# fallout D-175 — the environment the runner hands a command.
#
# The launch used to pass `os.environ.copy()`, so whether the committed corpus
# reproduced was a function of HOW THE MCP SERVER HAPPENED TO BE LAUNCHED. The
# observed instance: a lead with `plugins/foundry/mcp-server/.venv` activated
# exported `VIRTUAL_ENV` naming the MAIN checkout, the sweep's detached
# worktree has its own `.venv` at a different absolute path, and `uv` wrote a
# one-line warning onto stderr — merged into the capture — for a log nothing
# in the tree had touched. `Foundry-Gate('inspect_start')` refused the
# crossing on it.
#
# These four are unit-level and cheap on purpose. The property is a property
# of one function, and pinning it at the executor rather than through a sweep
# means a regression is named at the line that caused it instead of arriving
# as one mismatched log out of ninety-six.
# ---------------------------------------------------------------------------
_D175_POLLUTION: dict[str, str] = {
    # The variable actually observed, spelled with the path shape that
    # produced the warning.
    "VIRTUAL_ENV": "/somewhere/else/plugins/foundry/mcp-server/.venv",
    # Same class, different tool: each one redirects an interpreter, a
    # resolver or a test runner at state outside the checkout.
    #
    # `COLUMNS` belongs to this class and is deliberately NOT here. Setting it
    # mid-run changes the width PYTEST ITSELF renders its result lines at, so
    # the evidence log capturing this test would carry ~900 columns of padding
    # produced by the test rather than by the behaviour — a log whose bytes are
    # an artefact of its own assertion. The launch drops it either way: the
    # allowlist decides that, not this dict.
    "PYTHONPATH": "/somewhere/else/src",
    "PYTHONWARNINGS": "error",
    "UV_PROJECT_ENVIRONMENT": "/somewhere/else/.venv",
    "PYTEST_ADDOPTS": "-p no:randomly",
    "CONDA_PREFIX": "/somewhere/else/miniconda3/envs/guild",
    # Not a reproducibility hazard — a capability one. An evidence command is
    # arbitrary committed shell; it has no business holding the lead's tokens.
    "GITHUB_TOKEN": "ghp_not_a_real_token",
}


def _pollute(monkeypatch) -> None:
    """Put every D-175-class variable into the server's own environment."""
    for name, value in _D175_POLLUTION.items():
        monkeypatch.setenv(name, value)


def test_the_runner_hands_a_closed_allowlist_not_the_servers_environment(
    tmp_path, monkeypatch
):
    """D-175 REGRESSION, driven rather than read.

    The environment is asked of the CHILD, not of `_child_environment`'s return
    value, because the only thing that matters is what the process on the far
    side of `Popen` could see. A helper that computed the right mapping and a
    launch that ignored it would pass a test written the other way.
    """
    from foundry_mcp.tools.worktree_helpers import _run_command_with_timeout

    _pollute(monkeypatch)
    probe = " ".join(
        f'"{name}=${{{name}:-<absent>}}"' for name in sorted(_D175_POLLUTION)
    )
    exit_code, captured, _elapsed = _run_command_with_timeout(
        f"printf '%s\n' {probe} \"HOME=${{HOME:+<present>}}\" "
        f"\"PATH=${{PATH:+<present>}}\"",
        tmp_path,
        30,
    )
    assert exit_code == 0, captured
    lines = captured.splitlines()
    for name in sorted(_D175_POLLUTION):
        assert f"{name}=<absent>" in lines, (
            f"{name} reached the command. The runner is handing the child the "
            f"server's own environment again, which is what made a committed "
            f"log's reproducibility a property of the operator's shell "
            f"(D-175). Captured:\n{captured}"
        )
    assert "HOME=<present>" in lines, (
        "HOME did not reach the command; every cache and config lookup a "
        "corpus command makes is under it"
    )
    assert "PATH=<present>" in lines, (
        "PATH did not reach the command; nothing at all is found without it"
    )


def test_the_same_command_under_a_polluted_and_a_clean_environment_matches(
    tmp_path, monkeypatch
):
    """The pin D-175's fix shape asks for, in its own words: 'the same command
    run under a polluted environment and a clean one must produce identical
    bytes'.

    `env` is the command precisely because its whole output IS the environment,
    so this is the strongest form of the claim available — not "the variables I
    thought to check are absent" but "the child could not tell the two server
    processes apart at all". The shell's own additions (`PWD`, `SHLVL`, `_`)
    are constant across the pair because the cwd and the invocation are.
    """
    from foundry_mcp.tools.worktree_helpers import _run_command_with_timeout

    for name in _D175_POLLUTION:
        monkeypatch.delenv(name, raising=False)
    _, clean, _ = _run_command_with_timeout("env | sort", tmp_path, 30)

    _pollute(monkeypatch)
    _, polluted, _ = _run_command_with_timeout("env | sort", tmp_path, 30)

    assert polluted == clean, (
        "the same command produced different bytes under two server "
        "environments, so whether a committed log reproduces still depends on "
        "how the server was launched rather than on the tree (D-175). "
        f"Lines only the polluted run emitted: "
        f"{sorted(set(polluted.splitlines()) - set(clean.splitlines()))}"
    )


def test_the_allowlist_is_closed_so_an_unnamed_variable_never_reaches_a_command(
    monkeypatch,
):
    """The membership rule, stated as a property rather than as a list.

    A denylist would have to name `VIRTUAL_ENV`, then the next `UV_*` uv
    invents, then the one after. This asserts the shape that makes those
    future variables somebody else's non-problem: the child's environment is a
    SUBSET of the declared allowlist, so a name nobody has thought of is
    dropped before it is invented — which is the only reading under which
    D-175's 'closes one door in a corridor' is answered.
    """
    from foundry_mcp.tools.worktree_helpers import (
        _CHILD_ENV_ALLOWLIST,
        _child_environment,
    )

    _pollute(monkeypatch)
    monkeypatch.setenv("UV_A_VARIABLE_UV_HAS_NOT_INVENTED_YET", "1")

    leaked = set(_child_environment()) - set(_CHILD_ENV_ALLOWLIST)
    assert not leaked, (
        f"{sorted(leaked)} reached a command without being declared. The "
        f"allowlist is the whole mechanism: an environment assembled any other "
        f"way fails open on the variable nobody has met yet."
    )
    assert set(_child_environment()) <= set(os.environ), (
        "a name the server does not have was invented for the child; 'unset' "
        "and 'set to empty' are different questions to every shell"
    )


def test_the_provenance_env_trail_names_what_the_command_saw(tmp_path, monkeypatch):
    """`env_keys_present` is documented as the names present AT RE-EXEC TIME.

    That field and `os.environ` stopped being the same list when the runner's
    inherited copy became an allowlist, so a record still built from
    `os.environ` would name variables the command could not see and would hide
    the fact that the door drops them — an abuse trail describing a process
    that never ran.
    """
    from foundry_mcp.tools.evidence import _make_provenance_record
    from foundry_mcp.tools.worktree_helpers import _child_environment

    _pollute(monkeypatch)
    record = _make_provenance_record(
        evidence_path=tmp_path / "evidence" / "casting-1-x.log",
        evidence_cmd="cat replay.txt",
        casting_commit="0" * 40,
        log_text="body\n",
        captured_text="body\n",
        redacted_log="body\n",
        redacted_captured="body\n",
        exit_code=0,
        elapsed_seconds=0.1,
        verdict="verified",
        failure_token=None,
        failure_detail=None,
    )
    assert record["env_keys_present"] == sorted(_child_environment()), (
        "the provenance trail is not derived from the function the launch "
        "uses, so the two can drift into describing different processes"
    )
    for name in _D175_POLLUTION:
        assert name not in record["env_keys_present"], (
            f"the trail names {name}, which the command could not see"
        )


def test_the_acceptance_door_scrubs_the_environment_too(tmp_path, monkeypatch):
    """D-175 ADJACENT-PATH TEST.

    The defect was driven at the BOUNDARY SWEEP — `_sweep_one_log`, reached
    from `Foundry-Gate('inspect_start')`. The adjacent path driven here is the
    OTHER door onto the same executor: `_verify_one_evidence_file`, reached
    from `foundry_handoff#foundry_accept_casting` at casting acceptance. Both
    call `_run_command_with_timeout`, so a fix applied at either caller instead
    of at the launch would leave this one inheriting the operator's shell, and
    a casting would be ACCEPTED or REJECTED on the strength of how the server
    was started.

    The committed log's command emits nothing when the environment is clean and
    one `LEAKED:` line per inherited variable when it is not, so the verdict
    itself is the assertion: a leak is a body the comparison has never seen.
    """
    from foundry_mcp.tools.evidence import verify_evidence
    from foundry_mcp.tools.foundry_state import clear_active_run

    _pollute(monkeypatch)
    # NOT A BACKSLASH IN SIGHT, deliberately. `tests/orchestration/
    # test_module_boundaries.py#_private_names_defined_anywhere` speculatively
    # `ast.parse`s every string constant this file ships, and a `sed` BRE like
    # `s/^\\(A\\|B\\)/…/` reads to that parser as a Python string literal
    # carrying an invalid escape — so the parse emitted a SyntaxWarning that
    # landed in the warnings summary of every suite log in the corpus and broke
    # five of them at the boundary. `grep -E` states the same alternation with
    # no escape for anything to misread.
    leak_probe = (
        "env | grep -E '^(VIRTUAL_ENV|PYTHONPATH|GITHUB_TOKEN)='"
        " | cut -d= -f1 | sed 's/^/LEAKED: /'"
        "; cat replay.txt"
    )
    env = _build_divergent_spec_repo(
        tmp_path, replay_body_only=True, evidence_cmd=leak_probe
    )
    clear_active_run()
    run_dir = tmp_path / "run-env-scrub"
    run_dir.mkdir()

    result = verify_evidence(
        casting_id=1,
        project_root=env["project_root"],
        casting_commit=env["casting_commit"],
        spec_path=env["run_spec"],
        run_dir=run_dir,
    )
    assert result["verdict"] == "accepted", (
        "the acceptance door's re-execution saw the server's own environment: "
        f"{result}"
    )
    assert result["failure_token"] is None, result


def test_a_mismatch_record_carries_both_hash_vocabularies(tmp_path):
    """The sweep-scope contract's second clause: 'Carry the provenance
    spellings `redacted_log_sha256` and `redacted_captured_sha256` ALONGSIDE
    [`expected_sha256` / `actual_sha256`] in the same mismatch record, so a
    reader that knows either vocabulary is satisfied. Compute the values once.'

    A reader arriving from a sweep refusal and a reader correlating it against
    the casting's accepted provenance must find the same two values, so both
    spellings carry the SAME object."""
    env = _build_sweep_repo(tmp_path)
    replay = env["project_root"] / "replay-casting-1-alpha.txt"
    replay.write_text("totally different output\n", encoding="utf-8")
    _run_git(["add", "-A"], env["project_root"])
    _run_git(["commit", "-q", "-m", "break casting 1"], env["project_root"])

    mismatch = _sweep(env, full=True)["mismatches"][0]
    assert set(mismatch) >= {
        "log", "reason", "failure_token", "expected_sha256", "actual_sha256",
        "redacted_log_sha256", "redacted_captured_sha256",
    }
    assert mismatch["expected_sha256"] == mismatch["redacted_log_sha256"]
    assert mismatch["actual_sha256"] == mismatch["redacted_captured_sha256"]
    assert mismatch["expected_sha256"] != mismatch["actual_sha256"]
    # Same spelling `_make_provenance_record` writes, so the two are comparable.
    assert mismatch["expected_sha256"].startswith("sha256:")


def test_hashes_are_none_where_redaction_never_ran(tmp_path):
    """The other half of the same record. `_hash_str("")` is a real, stable,
    meaningless value, and publishing it on a path where redaction never
    happened would let a reader compare two logs that were never compared and
    find them equal. A non-zero exit never reaches the comparator, so all four
    hash fields are None."""
    corpus = _default_sweep_corpus()
    corpus["casting-1-alpha.log"] = _sweep_log(
        "casting-1-alpha", for_ids="CT-007", cmd="exit 3"
    )
    env = _build_sweep_repo(tmp_path, logs=corpus)
    result = _sweep(env, full=True)

    assert result["ok"] is False
    mismatch = [m for m in result["mismatches"] if "casting-1" in m["log"]][0]
    assert mismatch["failure_token"] == "EVIDENCE_EXIT_NONZERO"
    assert mismatch["exit_code"] == 3
    for field in ("expected_sha256", "actual_sha256", "redacted_log_sha256",
                  "redacted_captured_sha256"):
        assert mismatch[field] is None, field


def test_the_sweep_honours_declared_volatile_redaction(tmp_path):
    """GI-002 / the must_have: the sweep 'neither duplicates verify_evidence's
    comparison logic nor its volatile-redaction rules — both route through the
    existing helpers.'

    Driven end to end: the command emits a different duration on every run and
    the log declares that field volatile, so the sweep passes. Remove the
    declaration and the same corpus fails. If the sweep had its own redaction —
    or none — one of these two would come out wrong."""
    body = "collected 3 items\n\n3 passed in 0.41s\n"
    declared = _sweep_log(
        "casting-1-alpha", for_ids="CT-007",
        cmd="printf 'collected 3 items\\n\\n3 passed in 9.87s\\n'",
        body=body, extra_headers="# evidence-volatile: \\d+\\.\\d+s\n",
    )
    env = _build_sweep_repo(tmp_path, logs={"casting-1-alpha.log": declared})
    assert _sweep(env, full=True)["ok"] is True

    undeclared = declared.replace("# evidence-volatile: \\d+\\.\\d+s\n", "")
    env2 = _build_sweep_repo(tmp_path / "second",
                             logs={"casting-1-alpha.log": undeclared})
    result = _sweep(env2, full=True)
    assert result["ok"] is False
    assert result["mismatches"][0]["failure_token"] == "EVIDENCE_OUTPUT_MISMATCH"


def test_a_redaction_that_erases_the_log_is_refused_by_the_shared_guard(tmp_path):
    """The same point one rung deeper. D-126's residue floor and D-135's
    disagreement guard live inside `_compare_byte_match`, and the sweep must
    inherit both rather than re-deciding what a byte-match is. A declaration
    broad enough to erase the log is refused here exactly as it is at
    acceptance."""
    body = "collected 3 items\n\n3 passed\n"
    greedy = _sweep_log(
        "casting-1-alpha", for_ids="CT-007",
        cmd=f"printf '{body}'".replace("\n", "\\n"),
        body=body, extra_headers="# evidence-volatile: .*\n",
    )
    env = _build_sweep_repo(tmp_path, logs={"casting-1-alpha.log": greedy})
    result = _sweep(env, full=True)
    assert result["ok"] is False
    assert result["mismatches"][0]["failure_token"] == "EVIDENCE_VOLATILE_MALFORMED"


def test_a_log_with_no_command_is_named_rather_than_skipped(tmp_path):
    """A log the sweep cannot run is a finding, not a silence. The token is the
    same one acceptance uses for the same fault, because a closed vocabulary
    that gained a member per caller would not be closed."""
    corpus = _default_sweep_corpus()
    corpus["casting-1-alpha.log"] = "# evidence-for: CT-007\n\nno command here\n"
    env = _build_sweep_repo(tmp_path, logs=corpus)
    result = _sweep(env, full=True)
    assert result["ok"] is False
    mismatch = [m for m in result["mismatches"] if "casting-1" in m["log"]][0]
    assert mismatch["failure_token"] == "EVIDENCE_COMMAND_MISSING"
    assert "casting-1-alpha.log" in mismatch["reason"]


def test_a_log_that_is_not_committed_at_head_is_named(tmp_path):
    """ST-005 taken literally: the sweep compares the COMMITTED corpus at HEAD.

    A log sitting in the working tree that no commit carries has nothing to
    re-execute against, and it is not part of the corpus this boundary checks —
    whether a casting committed its evidence is `Foundry-Accept-Casting`'s
    question, asked at the casting commit.

    Two halves, and D-016 is why they are separate. The SCOPE is enumerated at
    HEAD, so an uncommitted log is never selected. The sweep's own guard stays
    anyway, for a caller that hands it a path directly: reporting such a log as
    a PASS would let an artifact with no committed counterpart clear a boundary
    that exists to check committed ones."""
    from foundry_mcp.tools.evidence import sweep_evidence_at_head

    env = _build_sweep_repo(tmp_path)
    uncommitted = env["evidence_dir"] / "casting-4-uncommitted.log"
    uncommitted.write_text(
        _sweep_log("casting-4-uncommitted", for_ids="CT-007"), encoding="utf-8"
    )

    # Half one: it is not in the committed corpus, so a FULL sweep passes and
    # never sees it.
    result = _sweep(env, full=True)
    assert result["ok"] is True, result["mismatches"]
    assert not any("casting-4" in name for name in result["logs_reexecuted"])

    # Half two: handed to the sweep directly, it is NAMED, never dropped.
    forced = sweep_evidence_at_head(
        project_root=env["project_root"], run_dir=env["run_dir"],
        logs=[uncommitted],
    )
    assert forced["ok"] is False
    mismatch = [m for m in forced["mismatches"] if "casting-4" in m["log"]][0]
    assert "not committed at HEAD" in mismatch["reason"]


def test_every_log_in_scope_reports_its_own_elapsed_seconds(tmp_path):
    """D-032 / CT-007 verbatim: 'sweep result recorded per log with scope
    (delta or full) and elapsed seconds'.

    Only mismatches carried a time, so the column the contract specifies was
    absent for exactly the logs that passed — and a lead tuning NFR-004's pool
    could not see which log was the straggler, because a straggler that PASSES
    is the ordinary case."""
    env = _build_sweep_repo(tmp_path)
    result = _sweep(env, full=True)
    assert result["ok"] is True, result["mismatches"]

    per_log = result["per_log"]
    assert [row["log"] for row in per_log] == result["logs_reexecuted"], (
        "one row per log in scope, in the same order as the path list"
    )
    for row in per_log:
        assert row["matched"] is True
        assert row["exit_code"] == 0
        assert row["failure_token"] is None
        assert isinstance(row["elapsed_seconds"], float)
        assert row["elapsed_seconds"] >= 0.0


def test_a_mismatched_log_carries_timing_on_both_records(tmp_path):
    """The per-log row exists for a FAILING log too, and it does not replace
    the mismatch's own `elapsed_seconds`: the mismatch times the COMMAND, the
    per-log row times the whole operation, and a lead debugging a timeout
    wants the first while a lead tuning the pool wants the second."""
    corpus = _default_sweep_corpus()
    corpus["casting-1-alpha.log"] = _sweep_log(
        "casting-1-alpha", for_ids="CT-007", cmd="echo drifted",
        body="the committed body\n",
    )
    env = _build_sweep_repo(tmp_path, logs=corpus)
    result = _sweep(env, full=True)
    assert result["ok"] is False

    row = [r for r in result["per_log"] if "casting-1-alpha" in r["log"]][0]
    assert row["matched"] is False
    assert row["failure_token"] == "EVIDENCE_OUTPUT_MISMATCH"
    assert row["elapsed_seconds"] >= 0.0
    mismatch = [m for m in result["mismatches"] if "casting-1-alpha" in m["log"]][0]
    assert "elapsed_seconds" in mismatch
    # And the clean logs still get their rows.
    assert len(result["per_log"]) == len(result["logs_reexecuted"]) == 3


def test_the_pool_dispatches_the_longest_log_first(tmp_path):
    """D-040 / NFR-004: 'pool size and log ordering are tuned to that', where
    'that' is finishing well inside the INSPECT the sweep precedes.

    The order was a plain `sorted()` — tuned for a reader diffing two sweeps,
    which is a real goal but not the one the requirement names. Longest
    Processing Time first is the makespan heuristic: with a fixed pool,
    dispatching the 300-second log last leaves every other worker idle behind
    it. The estimate is the log's own declared `# evidence-timeout:`."""
    from foundry_mcp.tools.evidence import _sweep_submission_order

    corpus = {
        "casting-1-alpha.log": _sweep_log(
            "casting-1-alpha", for_ids="CT-007",
            extra_headers="# evidence-timeout: 5\n",
        ),
        "casting-2-beta.log": _sweep_log(
            "casting-2-beta", for_ids="CT-014",
            extra_headers="# evidence-timeout: 300\n",
        ),
        "casting-3-gamma.log": _sweep_log(
            "casting-3-gamma", for_ids="AC-036",
            extra_headers="# evidence-timeout: 60\n",
        ),
    }
    env = _build_sweep_repo(tmp_path, logs=corpus)
    logs = sorted(env["evidence_dir"].glob("*.log"))
    assert [p.name for p in logs] == [
        "casting-1-alpha.log", "casting-2-beta.log", "casting-3-gamma.log"
    ], "alphabetical order puts the 300-second log in the middle"

    order = _sweep_submission_order(
        logs, project_root=env["project_root"],
        worktree_path=env["project_root"],   # HEAD == the working tree here
    )
    assert [p.name for p in order] == [
        "casting-2-beta.log", "casting-3-gamma.log", "casting-1-alpha.log"
    ], "longest declared timeout dispatched first"

    # REPORTING order is unchanged — a lead diffing cycle N against N-1 still
    # sees the caller's order, which is what the old `sorted()` was for.
    result = _sweep(env, full=True)
    assert result["ok"] is True, result["mismatches"]
    assert [Path(n).name for n in result["logs_reexecuted"]] == [
        "casting-1-alpha.log", "casting-2-beta.log", "casting-3-gamma.log"
    ]


def test_the_sweep_kills_a_log_that_exceeds_its_declared_timeout(tmp_path):
    """A hung evidence command must not hang the boundary. The declared
    `# evidence-timeout:` is the author's own measurement and is enforced by
    the SAME `_run_command_with_timeout` acceptance uses, so the process group
    is killed rather than the immediate child alone (Pitfall 3)."""
    corpus = {
        "casting-1-alpha.log": _sweep_log(
            "casting-1-alpha", for_ids="CT-007", cmd="sleep 45",
            body="never emitted\n", extra_headers="# evidence-timeout: 1\n",
        )
    }
    env = _build_sweep_repo(tmp_path, logs=corpus)
    result = _sweep(env, full=True)
    assert result["ok"] is False
    mismatch = result["mismatches"][0]
    assert mismatch["failure_token"] == "EVIDENCE_TIMEOUT"
    assert "1s" in mismatch["reason"]
    assert mismatch["elapsed_seconds"] < 30, "the killer did not fire"


# --------------------------------------------------------------------------- #
# AC-014 / NFR-004 / FR-031 — cost, and where it does not go.
# --------------------------------------------------------------------------- #


def test_an_empty_scope_creates_no_worktree_and_spawns_nothing(tmp_path):
    """AC-014's zero-log case, asserted where it costs: a DELTA sweep whose
    GRIND touched nothing in scope must not pay for a worktree,
    `.git/config.lock` contention or a subprocess. Creating one and tearing it
    straight down would be the same ANSWER at a cost NFR-004 exists to avoid,
    so the assertion is structural — the worktree machinery is never called."""
    from foundry_mcp.tools import evidence as ev

    env = _build_sweep_repo(tmp_path)
    calls: list = []
    original = ev._setup_worktree
    try:
        ev._setup_worktree = lambda *a, **k: calls.append(a) or original(*a, **k)
        result = _sweep(env, full=False, touched=["docs/README.md"])
    finally:
        ev._setup_worktree = original

    assert calls == [], "an empty sweep created a worktree"
    assert result["ok"] is True
    assert result["scope_count"] == 0
    assert result["logs_reexecuted"] == []
    assert result["pool_size"] == 0
    assert result["head_commit"] is None
    assert not (env["run_dir"] / "worktrees").exists()


def test_the_whole_sweep_shares_one_worktree(tmp_path):
    """The C-7 clause: 'creates ONE detached worktree at HEAD of project_root
    for the WHOLE sweep.'

    One per log would put N concurrent `git worktree add` calls on the same
    `.git/config.lock` — Pitfall 2, the race `_WORKTREE_LOCK` exists for — and
    would serialise the setup it was meant to parallelise."""
    from foundry_mcp.tools import evidence as ev

    env = _build_sweep_repo(tmp_path)
    calls: list = []
    original = ev._setup_worktree

    def _spy(*args, **kwargs):
        calls.append((args, kwargs))
        return original(*args, **kwargs)

    try:
        ev._setup_worktree = _spy
        result = _sweep(env, full=True)
    finally:
        ev._setup_worktree = original

    assert result["scope_count"] == 3
    assert len(calls) == 1, f"one worktree for the whole sweep, got {len(calls)}"
    assert calls[0][1]["dir_prefix"] == ev.SWEEP_WORKTREE_PREFIX


@pytest.mark.parametrize("break_it", [False, True])
def test_the_worktree_is_torn_down_on_success_and_on_failure(tmp_path, break_it):
    """Parametrized over both outcomes on purpose: a teardown that runs only on
    the success path leaks one directory per GRIND cycle, which at the observed
    run scale is twenty-odd orphaned checkouts by DONE."""
    env = _build_sweep_repo(tmp_path)
    if break_it:
        replay = env["project_root"] / "replay-casting-1-alpha.txt"
        replay.write_text("different\n", encoding="utf-8")
        _run_git(["add", "-A"], env["project_root"])
        _run_git(["commit", "-q", "-m", "break it"], env["project_root"])

    result = _sweep(env, full=True)
    assert result["ok"] is not break_it

    leftovers = [
        p for p in (env["run_dir"] / "worktrees").iterdir() if p.is_dir()
    ] if (env["run_dir"] / "worktrees").exists() else []
    assert leftovers == [], f"worktree left behind: {leftovers}"


def test_the_pool_size_is_derived_from_the_corpus_not_decreed(tmp_path):
    """FR-031 verbatim: 'The parallel pool size for the evidence sweep and the
    per-log timeout are implementer's choice, DERIVED FROM THE COMMITTED CORPUS
    rather than a generic constant.'

    Derived means it MOVES with the corpus. A three-log sweep asks for at most
    three workers however many cores the box has; a large corpus is capped by
    the ceiling; and never more workers than there is work."""
    from foundry_mcp.tools.evidence import SWEEP_POOL_CEILING, _derive_sweep_pool_size

    assert _derive_sweep_pool_size([], None) == 0
    assert _derive_sweep_pool_size([Path("a.log")], None) == 1
    assert _derive_sweep_pool_size([Path(f"{i}.log") for i in range(3)], None) <= 3
    big = [Path(f"{i}.log") for i in range(200)]
    assert _derive_sweep_pool_size(big, None) <= SWEEP_POOL_CEILING
    assert _derive_sweep_pool_size(big, None) >= 1
    # An explicit override wins, clamped to at least one — a pool of zero would
    # simply hang, and forcing serialisation is what a lead debugging a flaky
    # log actually wants.
    assert _derive_sweep_pool_size(big, 1) == 1
    assert _derive_sweep_pool_size(big, 0) == 1

    env = _build_sweep_repo(tmp_path)
    assert _sweep(env, full=True)["pool_size"] == min(
        3, os.cpu_count() or 1, SWEEP_POOL_CEILING
    )


def test_the_per_log_timeout_comes_from_the_log_that_declared_it(tmp_path):
    """FR-031's other half. A `# evidence-timeout:` is the artifact author's
    own measurement, already range-checked by `_parse_evidence_header`.

    Honouring it is what keeps the sweep and acceptance agreeing about the same
    log: `_verify_one_evidence_file` reads exactly this value, and a sweep that
    imposed its own would kill a 300-second integration log that acceptance had
    already passed."""
    from foundry_mcp.tools.evidence import (
        EVIDENCE_TIMEOUT_DEFAULT_SECONDS,
        _sweep_log_timeout,
    )

    assert _sweep_log_timeout({"timeout": 300}, None) == 300
    assert _sweep_log_timeout({"timeout": None}, None) == EVIDENCE_TIMEOUT_DEFAULT_SECONDS
    assert _sweep_log_timeout({}, None) == EVIDENCE_TIMEOUT_DEFAULT_SECONDS
    # A caller-supplied ceiling wins for every log, which is the knob a lead
    # uses to bound a whole sweep.
    assert _sweep_log_timeout({"timeout": 300}, 30) == 30


def test_logs_are_reported_in_input_order_however_they_finish(tmp_path):
    """`executor.map` preserves INPUT order regardless of completion order, and
    that is load-bearing rather than incidental: a lead diffing cycle N's sweep
    against cycle N-1's needs an unchanged sweep to look unchanged.
    `as_completed` would reshuffle it every run."""
    from foundry_mcp.tools.evidence import select_sweep_scope, sweep_evidence_at_head

    # The first log sleeps, so completion order is the reverse of input order.
    corpus = _default_sweep_corpus()
    corpus["casting-1-alpha.log"] = _sweep_log(
        "casting-1-alpha", for_ids="CT-007",
        cmd="sleep 0.4; cat replay-casting-1-alpha.txt",
    )
    env = _build_sweep_repo(tmp_path, logs=corpus)
    logs = select_sweep_scope(
        manifest=env["manifest"], evidence_dir=env["evidence_dir"],
        touched_files=[], full=True,
    )
    result = sweep_evidence_at_head(
        project_root=env["project_root"], run_dir=env["run_dir"], logs=logs
    )
    assert result["ok"] is True, result
    assert result["logs_reexecuted"] == [
        f"evidence/{p.name}" for p in logs
    ]


def test_a_sweep_that_could_not_run_is_never_reported_as_a_pass(tmp_path):
    """The shape that would let a broken sweep quietly clear the boundary it
    exists to hold: `ok: True` with an empty mismatch list, on a sweep that
    never ran a thing.

    A tree with no HEAD to resolve reports `ok: False` with a named `error`
    instead, so casting 3's refusal can say what happened rather than passing
    the transition."""
    from foundry_mcp.tools.evidence import sweep_evidence_at_head

    env = _build_sweep_repo(tmp_path)
    not_a_repo = tmp_path / "bare"
    not_a_repo.mkdir()
    result = sweep_evidence_at_head(
        project_root=not_a_repo, run_dir=env["run_dir"],
        logs=[env["evidence_dir"] / "casting-1-alpha.log"],
    )
    assert result["ok"] is False
    assert result["error"] is not None
    assert "HEAD" in result["error"]
    assert result["mismatches"] == []


def test_the_sweep_never_raises_across_the_boundary(tmp_path):
    """The house rule, at the surface casting 3 calls. A worker that raised
    would surface at `future.result()` and take the whole sweep — and with it
    the `inspect_start` transition — down with a traceback naming no log.

    Driven with a log that is a DIRECTORY, which makes every read of it raise
    OSError from somewhere the code does not name."""
    from foundry_mcp.tools.evidence import sweep_evidence_at_head

    env = _build_sweep_repo(tmp_path)
    result = sweep_evidence_at_head(
        project_root=env["project_root"], run_dir=env["run_dir"],
        logs=[env["evidence_dir"]],  # a directory, not a log
    )
    assert result["ok"] is False
    assert len(result["mismatches"]) == 1
    assert result["mismatches"][0]["reason"]


def test_the_sweep_writes_nothing_into_the_run_or_the_manifest(tmp_path):
    """The sweep is a READ. The refusal, the cycle counter and the
    `evidence_sweep` roll-up all belong to the `inspect_start` transition;
    a sweep that appended provenance at every GRIND boundary would corrupt the
    acceptance audit trail the manifest keeps."""
    env = _build_sweep_repo(tmp_path)
    before = sorted(p.name for p in env["run_dir"].iterdir())
    _sweep(env, full=True)
    after = [p.name for p in env["run_dir"].iterdir() if p.name != "worktrees"]
    assert sorted(after) == before


# --------------------------------------------------------------------------- #
# The demonstration test whose captured stdout is committed as evidence.
#
# Real assertions behind every printed line, so the transcript cannot drift
# from the behaviour. Nothing environment-dependent is printed — no tmp path,
# no clock reading, and the pool derivation is reported as RELATIONS rather
# than as raw numbers, because `os.cpu_count()` is a property of the machine
# and this output is byte-compared in a detached worktree.
# --------------------------------------------------------------------------- #


def test_demo_sweep_scope_and_cost(tmp_path, capsys):
    """GI-002 / FR-042 / FR-009 / AC-014 / OT-016 / FR-031 / NFR-004, printed.

    The scope selection on a real committed corpus, then the cost derivation
    that keeps a per-cycle sweep inside the wall time of the INSPECT it
    precedes."""
    from foundry_mcp.tools.evidence import (
        EVIDENCE_TIMEOUT_DEFAULT_SECONDS,
        SWEEP_POOL_CEILING,
        _derive_sweep_pool_size,
        _sweep_log_timeout,
    )

    env = _build_sweep_repo(tmp_path)
    scopes = [
        ("FULL rule fired / before ASSAY, NYQUIST or DONE",
         True, ["src/alpha.py"]),
        ("DELTA, diff touches casting 1's key_files",
         False, ["src/alpha.py"]),
        ("DELTA, diff touches casting 2's key_files",
         False, ["src/beta.py"]),
        ("DELTA, diff touches a file under casting 3's key_files DIRECTORY",
         False, ["tests/fixtures/gamma/rows.json"]),
        ("DELTA, diff touches nothing any casting or command names",
         False, ["docs/README.md"]),
    ]
    with capsys.disabled():
        print()
        print("=== select_sweep_scope: which logs the boundary re-executes ===")
        for label, full, touched in scopes:
            names = _sweep_scope_names(env, touched, full=full)
            print(f"  {label}")
            print(f"    touched={touched}")
            print(f"    scope={names}")

        print("=== the empty DELTA scope costs no worktree and no subprocess ===")
        empty = _sweep(env, full=False, touched=["docs/README.md"])
        print(f"    ok={empty['ok']}  scope_count={empty['scope_count']}  "
              f"logs_reexecuted={empty['logs_reexecuted']}")
        print(f"    head_commit={empty['head_commit']}  pool_size={empty['pool_size']}")

        print("=== a FULL sweep re-executes the whole committed corpus at HEAD ===")
        full_sweep = _sweep(env, full=True)
        print(f"    ok={full_sweep['ok']}  scope_count={full_sweep['scope_count']}")
        for name in full_sweep["logs_reexecuted"]:
            print(f"    reexecuted {name}")
        print(f"    mismatches={full_sweep['mismatches']}")

        print("=== FR-009: a command reaches a touched file by glob too "
              "(D-045) ===")
        from foundry_mcp.tools.evidence import _sweep_command_references

        for cmd, touched in (
            ("cat src/*.py", "src/mod.py"),
            ("pytest tests/*.py", "tests/test_x.py"),
            ("grep foo src/**/*.py", "src/a/b.py"),
            ("wc -l evidence/*.log", "evidence/casting-5-x.log"),
            ("uv run pytest tests/test_*.py",
             "plugins/foundry/mcp-server/tests/test_vocab.py"),
            ("ruff check src/*.py", "src/mod.py"),
            ("cat src/*.py", "src/a/b.py"),
            ("cat src/*.py", "src/mod.txt"),
            ("cat src/*.py", "docs/README.md"),
        ):
            verdict = _sweep_command_references(cmd, [touched])
            print(f"    {'in scope    ' if verdict else 'out of scope'}  "
                  f"{cmd!r} vs {touched}")

        print("=== FR-031: the pool size is derived from the corpus ===")
        print(f"    ceiling (measured from the committed corpus) = {SWEEP_POOL_CEILING}")
        print(f"    pool([])            == 0            : "
              f"{_derive_sweep_pool_size([], None) == 0}")
        print(f"    pool(1 log)         == 1            : "
              f"{_derive_sweep_pool_size([Path('a.log')], None) == 1}")
        print(f"    pool(3 logs)        <= 3            : "
              f"{_derive_sweep_pool_size([Path(f'{i}.log') for i in range(3)], None) <= 3}")
        print(f"    pool(200 logs)      <= ceiling      : "
              f"{_derive_sweep_pool_size([Path(f'{i}.log') for i in range(200)], None) <= SWEEP_POOL_CEILING}")
        print(f"    pool(200, override=1) == 1          : "
              f"{_derive_sweep_pool_size([Path(f'{i}.log') for i in range(200)], 1) == 1}")

        print("=== FR-031: the per-log timeout is the log's own measurement ===")
        print(f"    declared 300s                       : "
              f"{_sweep_log_timeout({'timeout': 300}, None)}")
        print(f"    undeclared falls back to the default: "
              f"{_sweep_log_timeout({}, None) == EVIDENCE_TIMEOUT_DEFAULT_SECONDS}")
        print(f"    a caller ceiling wins for every log : "
              f"{_sweep_log_timeout({'timeout': 300}, 30)}")

    assert _sweep_scope_names(env, ["src/alpha.py"], full=False) == [
        "casting-1-alpha.log"
    ]
    assert _sweep_scope_names(env, ["docs/README.md"], full=False) == []
    assert _sweep(env, full=True)["ok"] is True
    assert _derive_sweep_pool_size([], None) == 0
    assert _sweep_log_timeout({"timeout": 300}, None) == 300


# --------------------------------------------------------------------------- #
# GRIND cycle 13 — D-184 and D-187, one symbol, one fix.
#
# `_sweep_requirement_to_castings` was the third reader of "which requirement
# IDs does this casting own" and the only one D-180 did not migrate off the
# full-text scan. Printed here as the three readers agreeing on one block, and
# as the DELTA scope FR-009 actually names.
# --------------------------------------------------------------------------- #


def test_demo_grind_cycle_13_declared_ownership_keys_the_sweep(tmp_path, capsys):
    """D-184 / D-187: FR-009, FR-042, GI-002, AC-014, OT-016, ST-005, CT-007.

    Everything printed is asserted below the print block, and nothing printed
    is environment-dependent — no tmp path, no clock, no cpu count — because
    this transcript is byte-compared in a detached worktree."""
    import copy

    from foundry_mcp.schemas.vocab import REQUIREMENT_ID_RE
    from foundry_mcp.tools.evidence import _sweep_requirement_to_castings
    from foundry_mcp.tools.foundry import foundry_init
    from foundry_mcp.tools.foundry_handoff import declared_requirement_ids
    from foundry_mcp.tools.foundry_state import clear_active_run, set_active_run
    from foundry_mcp.tools.foundry_validate import foundry_validate_castings

    env = _build_sweep_repo(tmp_path)
    env["manifest"] = _QUOTING_SWEEP_MANIFEST
    block = next(
        c for c in _QUOTING_SWEEP_MANIFEST["castings"] if c["id"] == 2
    )["spec_text"]

    scanned = sorted(set(REQUIREMENT_ID_RE.findall(block)))
    declared = declared_requirement_ids(block)
    mapping = _sweep_requirement_to_castings(_QUOTING_SWEEP_MANIFEST)

    # The scope the OLD reader produced, reconstructed by DECLARING what the
    # scan merely found: that is exactly the belief `findall` handed the
    # keying, so running the shipped selector over it reproduces the shipped
    # bug without a second copy of the selector.
    as_scanned = copy.deepcopy(_QUOTING_SWEEP_MANIFEST)
    for casting in as_scanned["castings"]:
        casting["spec_text"] = "".join(
            f"- **{rid}**\n"
            for rid in sorted(set(REQUIREMENT_ID_RE.findall(casting["spec_text"])))
        )
    old_env = dict(env, manifest=as_scanned)

    # The F0.9 validator, driven for real on the same block.
    init = foundry_init(project_root=str(tmp_path / "validate"))
    fdir = Path(init["foundry_dir"])
    (fdir / "spec.md").write_text(
        "# Spec\n\n"
        "- **CT-014**: the report carries every section.\n"
        "- **AC-036**: the report names every section.\n",
        encoding="utf-8",
    )
    (fdir / "castings").mkdir(parents=True, exist_ok=True)
    (fdir / "castings" / "manifest.json").write_text(
        json.dumps({
            "spec_type": "GREENFIELD",
            "castings": [{
                "id": "2", "title": "the quoting casting", "spec_text": block,
                "observable_truths": ["a", "b", "c"],
                "key_files": ["src/beta.py"],
            }],
        }),
        encoding="utf-8",
    )
    (tmp_path / "validate" / "src").mkdir(parents=True, exist_ok=True)
    (tmp_path / "validate" / "src" / "beta.py").write_text(
        "def beta():\n    return 'beta'\n", encoding="utf-8"
    )
    set_active_run(init["run_name"])
    try:
        validator = foundry_validate_castings(str(tmp_path / "validate"))
    finally:
        clear_active_run()
    uncovered = {
        rid
        for issue in validator["dimensions"]["requirement_coverage"]["issues"]
        if issue["type"] == "uncovered_requirements"
        for rid in issue["ids"]
    }
    validator_credits = sorted({"CT-014", "AC-036"} - uncovered)
    sweep_keys = sorted(rid for rid, ids in mapping.items() if "2" in ids)

    with capsys.disabled():
        print()
        print("=== the block: one declaration whose prose names another "
              "casting's requirement ===")
        for line in block.rstrip("\n").splitlines():
            print(f"    {line}")
        print("=== what the two readers make of it ===")
        print(f"    REQUIREMENT_ID_RE.findall over the whole block : {scanned}")
        print(f"    declared_requirement_ids (subject position)    : {declared}")
        print("    AC-036 is DECLARED by casting 3; casting 2 only names it "
              "inside CT-014's statement.")

        print("=== the mapping a `# evidence-for:` header is keyed through ===")
        for rid in sorted(mapping):
            print(f"    {rid} -> {sorted(mapping[rid])}")

        print("=== three readers of one question, on one block (D-180 pinned "
              "two; this was the third) ===")
        print(f"    acceptance gate demands : {declared}")
        print(f"    F0.9 validator credits  : {validator_credits}")
        print(f"    evidence sweep keys     : {sweep_keys}")
        print(f"    all three agree         : "
              f"{declared == validator_credits == sweep_keys}")

        print("=== FR-009 — which logs the DELTA boundary re-executes ===")
        print("    wave-report-sections.log is off-convention, so its only key "
              "is `# evidence-for: AC-036`")
        for label, touched in (
            ("diff touches casting 2's key_files", ["src/beta.py"]),
            ("diff touches casting 3's key_files",
             ["tests/fixtures/gamma/rows.json"]),
        ):
            print(f"    {label}  touched={touched}")
            print(f"      keyed by declaration : "
                  f"{_sweep_scope_names(env, touched, full=False)}")
            print(f"      keyed by the scan    : "
                  f"{_sweep_scope_names(old_env, touched, full=False)}")
        print("    the extra log satisfies NEITHER FR-009 arm: casting 3's "
              "key_files are not in the diff and its command names no touched "
              "file.")
        print(f"    FULL rule fired  scope="
              f"{_sweep_scope_names(env, ['src/beta.py'], full=True)}")

    assert scanned == ["AC-036", "CT-014"]
    assert declared == ["CT-014"]
    assert mapping == {"AC-036": {"3"}, "CT-007": {"1"}, "CT-014": {"2"}}
    assert declared == validator_credits == sweep_keys
    assert _sweep_scope_names(env, ["src/beta.py"], full=False) == [
        "casting-2-beta.log"
    ]
    assert _sweep_scope_names(old_env, ["src/beta.py"], full=False) == [
        "casting-2-beta.log", "wave-report-sections.log"
    ]
    assert _sweep_scope_names(
        env, ["tests/fixtures/gamma/rows.json"], full=False
    ) == ["wave-report-sections.log"]
    assert _sweep_scope_names(env, ["src/beta.py"], full=True) == [
        "casting-1-alpha.log", "casting-2-beta.log", "wave-report-sections.log",
    ]



# --------------------------------------------------------------------------- #
# GRIND cycle 17 — D-198, one symbol, one narrowing.
#
# The stub library's discriminator was punctuation, not fabrication. Printed
# here as the two runs the defect drove, the corpus that already sits one
# capture-shape away from the same refusal, and the true positives the
# narrowing keeps.
# --------------------------------------------------------------------------- #

#: The corpus is pinned at the commit D-198 was driven against, extracted with
#: `git show`, so these counts stay frozen while `evidence/` grows every cycle.
_D198_CORPUS_COMMIT = "d872362cd792e7ae9ae2e90f0999186ba8b8c1fb"


def _d198_old_strip(text: str) -> list[str]:
    """The strip this cycle replaced, reconstructed verbatim.

    Every line whose lstrip started with `#`, anywhere in the file. That is
    precisely the belief the two stub rules were handed, so the transcript
    below compares the SHIPPED helper against the shipped behaviour it
    replaced rather than against a paraphrase of it.
    """
    return [
        ln for ln in text.splitlines()
        if ln.strip() and not ln.lstrip().startswith("#")
    ]


def _d198_old_token(text: str, cmd: str) -> str | None:
    """`_check_stub_patterns` as it read before the narrowing.

    Rules 1 and 2 never touched the strip and are called through to the
    shipped code; rules 3 and 4 are the two that divided by the body's length,
    and they are re-expressed here over `_d198_old_strip` so the old verdict is
    computed rather than remembered.
    """
    if evidence._is_stub_pattern_too_small(text, evidence.EVIDENCE_STUB_MIN_BYTES):
        return evidence.EVIDENCE_STUB_TOO_SMALL
    if cmd and evidence._is_stub_pattern_vacuous_cmd(cmd):
        return evidence.EVIDENCE_STUB_VACUOUS_CMD
    body = _d198_old_strip(text)
    body_text = "\n".join(body).strip()
    if body_text and evidence._STUB_BARE_ACK_RE.fullmatch(body_text):
        return evidence.EVIDENCE_STUB_BARE_PASS
    stamps = sum(1 for ln in body if evidence._STUB_TIMESTAMP_LINE_RE.match(ln))
    if len(body) >= 3 and stamps >= 3 and stamps >= int(0.8 * len(body)):
        return evidence.EVIDENCE_STUB_TIMESTAMP_CLUSTER
    return None


def _d198_pinned_corpus() -> list[tuple[str, str]]:
    """(name, text) for every evidence log committed at the pinned commit."""
    def git(*args: str) -> str:
        return subprocess.run(
            ["git", "-C", str(REPO_ROOT), *args],
            check=True, capture_output=True, text=True,
        ).stdout

    names = sorted(
        n for n in git(
            "ls-tree", "-r", "--name-only", _D198_CORPUS_COMMIT, "--", "evidence/"
        ).splitlines() if n.endswith(".log")
    )
    return [(Path(n).name, git("show", f"{_D198_CORPUS_COMMIT}:{n}")) for n in names]


def test_demo_grind_cycle_17_the_stub_rules_judge_the_captured_body(capsys):
    """D-198: FR-010, CT-015, AC-015.

    Everything printed is asserted below the print block, and nothing printed
    is environment-dependent — no tmp path, no clock, no cpu count, and a
    corpus pinned by commit — because this transcript is byte-compared in a
    detached worktree.
    """
    cmd = "cat replay.txt"
    shapes = {
        "run A — three captured comment lines, then three stamps":
            _d198_log(_D198_RUN_A_BODY),
        "run B — the same, leading '# ' removed, nothing else changed":
            _d198_log(_D198_RUN_B_BODY),
        "the bare-ack arm — nine captured comment lines, then PASS":
            _d198_log(
                "".join(f"# comment line {i} of the capture\n" for i in range(9))
                + "PASS\n"
            ),
    }
    keepers = {
        "a genuine bare ack — header, then PASS":
            _d198_log(_D198_BARE_ACK_BODY),
        "a genuine cluster — header, then timestamps only":
            _d198_log(_D198_TIMESTAMP_CLUSTER_BODY),
    }

    corpus = _d198_pinned_corpus()
    nonblank = lambda text: [ln for ln in text.splitlines() if ln.strip()]
    #: PROVE's metric: the body the old strip left was a minority of the file's
    #: non-blank lines, so most of what it judged had been deleted.
    majority_hash = sorted(
        (name, len(nonblank(text)) - len(_d198_old_strip(text)), len(nonblank(text)))
        for name, text in corpus
        if len(nonblank(text)) - len(_d198_old_strip(text))
        > len(nonblank(text)) / 2
    )
    #: The load-bearing set: logs carrying `#` lines BELOW the separator, i.e.
    #: in captured output. Every one of these is a capture-shape away from the
    #: refusal Run A took.
    reshaped = sorted(
        (
            (name, len(_d198_old_strip(text)),
             len(evidence._strip_header_and_blank_lines(text)))
            for name, text in corpus
            if len(_d198_old_strip(text))
            != len(evidence._strip_header_and_blank_lines(text))
        ),
        key=lambda row: (row[1] - row[2], row[0]),
    )
    hits = {
        name: token for name, text in corpus
        if (token := evidence._check_stub_patterns(
            text, evidence._parse_evidence_header(text).get("cmd") or ""
        ))
    }
    old_hits = {
        name: token for name, text in corpus
        if (token := _d198_old_token(
            text, evidence._parse_evidence_header(text).get("cmd") or ""
        ))
    }

    with capsys.disabled():
        print("=== D-198: two runs identical but for three characters ===")
        print("    command in both: sed -n '1,3p' src/guard.py && printf "
              "'<three ISO stamps>'")
        for label, text in shapes.items():
            old_body = _d198_old_strip(text)
            new_body = evidence._strip_header_and_blank_lines(text)
            print(f"    {label}")
            print(f"        old strip: body lines {len(old_body):>2} -> "
                  f"{_d198_old_token(text, cmd)}")
            print(f"        shipped  : body lines {len(new_body):>2} -> "
                  f"{evidence._check_stub_patterns(text, cmd)}")
        print("=== the true positives the narrowing keeps ===")
        for label, text in keepers.items():
            print(f"    {label}")
            print(f"        old strip: {_d198_old_token(text, cmd)}")
            print(f"        shipped  : {evidence._check_stub_patterns(text, cmd)}")
        print(f"=== the run's own corpus at {_D198_CORPUS_COMMIT[:7]} ===")
        print(f"    committed evidence logs                             : "
              f"{len(corpus)}")
        print(f"    whose body was a minority of the file, old strip    : "
              f"{len(majority_hash)}")
        for name, dropped, total in majority_hash:
            print(f"        {name:<44} {dropped:>4} of {total:>4} dropped")
        print(f"    carrying `#` lines BELOW the separator (captured)   : "
              f"{len(reshaped)}")
        for name, old_n, new_n in reshaped:
            print(f"        {name:<44} {old_n:>4} -> {new_n:>4}")
        print(f"    stub hits over the corpus, old strip                : "
              f"{old_hits}")
        print(f"    stub hits over the corpus, shipped strip            : "
              f"{hits}")
        print("    None trips a pattern today, which is why this had not fired")
        print("    in production and not why it could not.")

    assert [evidence._check_stub_patterns(t, cmd) for t in shapes.values()] == [
        None, None, None
    ]
    assert [_d198_old_token(t, cmd) for t in shapes.values()] == [
        "EVIDENCE_STUB_TIMESTAMP_CLUSTER", None, "EVIDENCE_STUB_BARE_PASS"
    ]
    assert [evidence._check_stub_patterns(t, cmd) for t in keepers.values()] == [
        "EVIDENCE_STUB_BARE_PASS", "EVIDENCE_STUB_TIMESTAMP_CLUSTER"
    ]
    assert len(corpus) == 70
    assert len(majority_hash) == 15
    assert len(reshaped) == 10
    assert reshaped[0] == ("casting-1-rollup-preservation.log", 11, 63)
    assert reshaped[-1] == ("casting-1-migration-tier-unknown.log", 59, 63)
    # The extreme of the first metric: three non-blank lines, two of them
    # header, so the old strip judged a ONE-LINE body. Its two `#` lines are
    # both above the separator, which is why it is not in `reshaped` — it is
    # the shape one captured comment line away from a one-line denominator.
    assert ("casting-8-mcp-version.log", 2, 3) in majority_hash
    assert old_hits == {} and hits == {}


# --------------------------------------------------------------------------- #
# The GRIND cycle 19 demonstration test whose captured stdout is committed as
# `evidence/casting-5-grind-cycle-19.log`.
#
# It REPLACES the cycle-18 demo (`..._the_strip_is_one_decision_over_both_
# sides`) rather than sitting beside it. That demo's closing section argued,
# measured on the corpus, that "strip only the `# evidence-*:` directives plus
# one blank" had to be REJECTED because it moved 19 committed logs. D-205 and
# the lead's binding ruling overturn exactly that argument: those 19 logs were
# the defect, not the counter-example. A demo whose conclusion the run no
# longer believes is not worth preserving as a standing claim, so it is folded
# forward — and `evidence/casting-5-grind-cycle-18.log` goes with it, every
# requirement id its `# evidence-for:` bound being bound by this cycle's log.
#
# The corpus is PINNED at cb77e83 — the commit D-205 was driven against — and
# extracted with `git show`, so the census below is frozen against a corpus
# that changes as each owning casting recaptures. Same idiom as the cycle-13
# and cycle-18 demos, including reaching the main repo through
# `--git-common-dir` because the sweep re-executes this command in a detached
# worktree.
#
# Nothing environment-dependent is printed: no tmp path, no clock reading, no
# pool size. Every printed line has an assertion behind it.
# --------------------------------------------------------------------------- #

#: The commit D-205 was filed and driven against. Its `evidence/` tree is the
#: corpus the corrected grammar measures, and freezing it here is what keeps
#: this log reproducible after the owners recapture.
_D205_CORPUS_COMMIT = "cb77e8329411419d44948a8214d9c8ccea37486d"


def _corpus_at_pinned_commit(commit: str) -> dict[str, str]:
    """The committed evidence logs at `commit`, name -> text."""
    common = subprocess.run(
        ["git", "rev-parse", "--git-common-dir"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    main = Path(common).resolve().parent
    names = [
        n for n in subprocess.run(
            ["git", "-C", str(main), "ls-tree", "-r", "--name-only",
             commit, "--", "evidence/"],
            capture_output=True, text=True, check=True,
        ).stdout.splitlines() if n.endswith(".log")
    ]
    return {
        Path(n).name: subprocess.run(
            ["git", "-C", str(main), "show", f"{commit}:{n}"],
            capture_output=True, text=True, check=True,
        ).stdout
        for n in names
    }


def _owning_casting(log_name: str) -> str:
    """Which casting recaptures this log.

    The filename convention `casting-{id}-*.log` names the owner, with one
    standing exception the lead declared for this run: `casting-8-suite.log`
    is the shared whole-suite log, and casting 3 recaptures it last, after
    every other recapture has landed.
    """
    if log_name == "casting-8-suite.log":
        return "casting 3 (shared suite log)"
    match = re.match(r"casting-(\d+)-", log_name)
    return f"casting {match.group(1)}" if match else "unowned"


def test_demo_grind_cycle_19_the_accept_branch_proves_every_line_it_discards(
    capsys,
):
    """D-205 printed: GI-002 / ST-005 / CT-007 / AC-013 / FR-042 / OT-008 /
    FR-010, and the escalated class `guard-narrowed-past-the-harm-it-names`.

    The single principle the three instances of that class share, the header
    grammar that now decides what may be discarded, the three runs that differ
    by one character, the second accepted shape the defect named, the true
    accepts the narrowing keeps, and the census of committed logs that stop
    reproducing under the corrected grammar — grouped by the casting that
    recaptures each one.
    """
    corpus = _corpus_at_pinned_commit(_D205_CORPUS_COMMIT)

    with capsys.disabled():
        print()
        print("=== D-205: the class root cause, stated once ===")
        print("    D-197, D-201 and D-205 are one class. Each guard asked a")
        print("    NARROWER question than the harm it names:")
        print()
        print("      %-12s %-34s %s"
              % ("defect", "the question it asked", "the harm it names"))
        for did, asked, harm in (
            ("D-197/D-201", "is this suffix in a table",
             "does a reader OPEN this name"),
            ("D-200", "is the strip symmetric",
             "did the command EMIT this"),
            ("D-205", "is the capture's run a suffix",
             "is every discarded line HEADER"),
        ):
            print("      %-12s %-34s %s" % (did, asked, harm))
        print()
        print("    the principle, pinned in evidence.py:")
        print("      a guard that discards, skips or exempts input must PROVE")
        print("      each discarded unit against the rule that names the")
        print("      exemption; anything not proven is subject to the check.")

        print()
        print("=== the header grammar — what may be discarded, and nothing "
              "else ===")
        known = sorted(evidence._KNOWN_HEADER_DIRECTIVES)
        assert known == ["cmd", "for", "timeout", "volatile"]
        print("    known directives : %s" % ", ".join(known))
        print("    header           : contiguous leading "
              "'# evidence-<known>:' lines")
        print("                       plus ONE blank separator")
        print("    everything else  : BODY — '#'-prefixed or not — compared")
        print("                       byte for byte against the capture")

        print()
        print("=== the three runs that differ by one character ===")
        honest = (
            "# evidence-cmd: cat replay.txt\n"
            "# evidence-for: FR-010\n"
            "\n"
            "REAL_TAIL\n"
        )
        claim = "FABRICATED: all 47 assertions passed on a clean tree"
        hashed = honest.replace("\n\nREAL_TAIL", "\n# %s\n\nREAL_TAIL" % claim)
        bare = honest.replace("\n\nREAL_TAIL", "\n%s\n\nREAL_TAIL" % claim)
        rows = []
        for label, committed, expect in (
            ("A  honest log", honest, True),
            ("B  the same log, claim prefixed with '#'", hashed, False),
            ("C  control: the same claim, no '#'", bare, False),
        ):
            matched, _ = _d200_compare(committed, "REAL_TAIL\n")
            assert matched is expect, label
            rows.append((label, matched))
        for label, matched in rows:
            print("    %-42s %s"
                  % (label, "accepted" if matched else "REFUSED"))
        print("    B and C differ by ONE character. At cb77e83, B was")
        print("    ACCEPTED at both doors: Foundry-Accept-Casting reported an")
        print("    accepted evidence verdict, and Foundry-Phase(inspect_start)")
        print("    reported mismatches [] and advanced the cycle counter.")

        print()
        print("=== the second accepted shape the defect named ===")
        print("    a forged '#' line ABOVE a capture whose own output")
        print("    legitimately begins with '#' lines, where the capture's")
        print("    run is still a suffix of the committed one:")
        forged_above = (
            "# evidence-cmd: sed -n '1,2p' src/guard.py\n"
            "# evidence-for: FR-010\n"
            "\n"
            "# FORGED: the guard holds on every path\n"
            "# real comment 1\n"
            "# real comment 2\n"
            "def guard():\n"
        )
        real_capture = "# real comment 1\n# real comment 2\ndef guard():\n"
        matched, diff = _d200_compare(forged_above, real_capture)
        assert matched is False and diff is not None
        assert "-# FORGED: the guard holds on every path" in diff
        print("      REFUSED, and the diff names the forged line:")
        for line in diff.rsplit("@@", 1)[-1].splitlines():
            if line.startswith("-# FORGED"):
                print("        %s" % line)

        print()
        print("=== the true accepts the narrowing keeps ===")
        keeps = (
            ("honest capture beginning with '#' lines (D-198's subject)",
             forged_above.replace(
                 "# FORGED: the guard holds on every path\n", ""),
             real_capture),
            ("cat-replay: the capture IS the whole committed file",
             honest, honest),
            ("two blank lines written; the command emits the second",
             "# evidence-cmd: x\n# evidence-for: AC-1\n\n\n=== keys ===\n",
             "\n=== keys ===\n"),
        )
        for label, committed, captured in keeps:
            matched, _ = _d200_compare(committed, captured)
            assert matched is True, label
            print("    %-58s accepted" % label)

        print()
        print("=== the smuggling shape the KNOWN-directive set closes ===")
        smuggled = (
            "# evidence-cmd: cat replay.txt\n"
            "# evidence-summary: all 47 assertions passed\n"
            "\n"
            "REAL_TAIL\n"
        )
        matched, _ = _d200_compare(smuggled, "REAL_TAIL\n")
        assert matched is False
        print("    '# evidence-summary:' is not a directive the parser reads,")
        print("    so it is body, and a command that never printed it is")
        print("    %s. A grammar of 'any # evidence-*: shape' would have"
              % ("REFUSED" if not matched else "accepted"))
        print("    discarded it unread — the same hole, one notch narrower.")

        print()
        print("=== corpus census at %s: %d committed logs ==="
              % (_D205_CORPUS_COMMIT[:7], len(corpus)))
        census: dict[str, list[str]] = {}
        for name, text in sorted(corpus.items()):
            if _ungrammatical_leading_lines(text):
                census.setdefault(_owning_casting(name), []).append(name)
        total = sum(len(v) for v in census.values())
        assert total, "the census is the point; an empty one means it misread"
        print("    logs carrying hand-typed lines in the leading run — text")
        print("    the command never printed — which therefore no longer")
        print("    reproduce under the corrected grammar and must be")
        print("    RECAPTURED by their owning casting:")
        print()
        for owner in sorted(census):
            print("    %-28s %2d" % (owner, len(census[owner])))
            for name in census[owner]:
                print("        %s" % name)
        print()
        print("    %-28s %2d of %d"
              % ("total needing recapture", total, len(corpus)))
        print("    the other %d reproduce unchanged: their whole leading run"
              % (len(corpus) - total))
        print("    is directives and one separator, which is the grammar.")

        # Every censused log's prose really does reach the comparison now —
        # which is what "no longer reproduces" MEANS, asserted rather than
        # merely printed.
        for names in census.values():
            for name in names:
                text = corpus[name]
                body, _ = evidence._header_stripped_pair(text, "other\n")
                for line in _ungrammatical_leading_lines(text):
                    assert line in body, (name, line)
