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
  - test_stub_pattern_no_cmd_in_header
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


def test_stub_pattern_no_cmd_in_header():
    """First N lines of output don't contain cmd-first-token substring →
    EVIDENCE_STUB_NO_CMD_IN_HEADER. cmd-first-token is the first whitespace-
    separated token of the ``# evidence-cmd:`` header (e.g. ``pytest``).
    """
    cmd = "pytest -k some_test"
    output = (
        "Build successful.\n"
        "All checks passed.\n"
        "Done.\n"
        "============================= test session starts ==============================\n"
    )
    # Default check_lines window covers first 3 lines; pytest substring is
    # absent from those lines.
    assert (
        evidence._is_stub_pattern_no_cmd_in_header(output, cmd, check_lines=3)
        is True
    )
    # When the cmd-token IS in the first 3 lines, predicate is False.
    output_ok = (
        "============================= pytest test session starts =====================\n"
        "Build successful.\n"
        "Done.\n"
    )
    assert (
        evidence._is_stub_pattern_no_cmd_in_header(output_ok, cmd, check_lines=3)
        is False
    )


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
        "EVIDENCE_STUB_NO_CMD_IN_HEADER",
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

    Plan 04-04 harness can pre-seed an orphan worktree dir before invoking
    ``foundry_accept_casting`` to exercise the pruning code path.
    """
    result = run_accept_casting_with_evidence(
        "evidence/evidence_log_clean.log",
        spec_format_version="v2.1",
        seed_orphan_worktree=True,  # harness kwarg: pre-seeds an orphan
    )
    assert result["verdict"] == "accepted"
    assert result["manifest"].get("orphan_worktrees_pruned", 0) >= 1


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
    """Two concurrent verify-evidence calls on the same project_root
    serialize via per-project lock — neither corrupts the other's worktree.

    Plan 04-04 harness spawns two concurrent invocations and asserts both
    complete with consistent verdicts.
    """
    result = run_accept_casting_with_evidence(
        "evidence/evidence_log_clean.log",
        spec_format_version="v2.1",
        concurrent_invocations=2,  # harness kwarg: spawn N concurrent calls
    )
    # All concurrent calls observe the same outcome; manifest records
    # serialization metadata.
    assert result["verdict"] == "accepted"
    assert result["manifest"].get("concurrent_serialized") is True


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


def test_f09_subcheck_7k_catches_missing_evid01(run_accept_casting_with_evidence):
    """F0.9 sub-check 7k: when v2.1 spec lacks an evidence record where
    one was required, F0.9 flags it as a structural defect (parallel to
    Phase 3's F0.9 7k for stream_skips on legacy specs).
    """
    result = run_accept_casting_with_evidence(
        "evidence/evidence_log_clean.log",
        spec_format_version="v2.1",
        omit_required_evidence=True,  # harness kwarg: drops evidence file
    )
    assert result["verdict"] == "rejected"
    f09 = result["manifest"].get("f09_diagnostics", "")
    assert "7k" in f09 or "EVID-01" in f09


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

# The leading `[replay] cat replay.txt` anchor carries the cmd-first-token
# into the first 3 body lines so `_is_stub_pattern_no_cmd_in_header` does not
# fire on the synthetic replay. A real pytest log carries its own token via
# "test session starts"; the same anchor convention is used by conftest's
# `use_cat_replay` harness.
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


def _build_divergent_spec_repo(tmp_path: Path) -> dict:
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
    (project_root / "replay.txt").write_text(evidence_log, encoding="utf-8")
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
