"""Casting 2 — the lead_fix handoff record and the two acceptance rungs.

One regression test per acceptance criterion, each docstring quoting the
requirement it proves. Built on the synthetic-run shape
``tests/test_escalation.py`` establishes.

Every requirement id in this module's prose is a
``forge-specs/foundry-run-convergence`` id and says so. Six of them —
convergence GI-003 / AC-022 / FR-010 / FR-016 / NFR-002 / OT-005 / OT-010 —
also exist in ``forge-specs/foundry-run-process-fixes`` with entirely
unrelated text (that spec's convergence GI-003 deletes a shell script), so the bare form
names no spec at all. tests/test_spec_id_convention.py refuses it.

  convergence GI-003 / AC-022 / OT-010
      the SERVER appends the lead_fix record, carrying the defect id, tier,
      file, line count and test. BOTH tiers are measured; a LATENT record is
      "recorded, lane limit not applied", and null in file/line_count means the
      measurement was UNAVAILABLE — git could not read the commit (D-074).
  convergence CT-011 / AC-030
      ``check_reported_prompt_hash`` is the one implementation of the
      pointer-dispatch hash rung, and names both hashes when they differ.
  convergence CT-015 / AC-015 / FR-010 / OT-027
      ``foundry_accept_casting`` without casting_commit is refused naming the
      parameter, and a successful acceptance always carries
      evidence_provenance.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from foundry_mcp.schemas.vocab import HANDOFF_EVENT_LEAD_FIX
from foundry_mcp.tools.foundry import foundry_init
# fallout AC-061 / GI-033 (D-192) — THREE MODULES NOW, AND THE SPLIT IS THE
# LAYERING RULE RATHER THAN A REORGANISATION. `foundry_accept_casting` RUNS
# `verify_evidence`, so it sits in `tools/evidence.py` beside the engine; the
# digest helper, the prompt-hash rung and the spec-hash reader are read from
# both layers and sit in the leaf; the `Foundry-Handoff` door and the server's
# own lead-fix writer stay in the lifecycle module this file is named for.
from foundry_mcp.tools.artifacts import (
    _hash_str,
    check_reported_prompt_hash,
    foundry_spec_hash,
)
from foundry_mcp.tools.evidence import foundry_accept_casting
from foundry_mcp.tools.foundry_handoff import (
    foundry_handoff,
    record_lead_fix_handoff,
)
from foundry_mcp.tools.foundry_state import clear_active_run


PROMPT_TEXT = (
    "# Casting 1\n\n"
    "<spec_requirements>\n- **AC-015**: casting_commit is required\n"
    "</spec_requirements>\n"
)
CASTING_COMMIT = "0" * 40


@pytest.fixture
def run_env(tmp_path):
    """Activate a run with a spec and one casting prompt; yield (root, fdir)."""
    result = foundry_init(project_root=str(tmp_path))
    fdir = Path(result["foundry_dir"])
    (fdir / "spec.md").write_text(
        "# Spec\n\nAC-015 the acceptance gate requires a casting commit.\n",
        encoding="utf-8",
    )
    (fdir / "castings").mkdir(parents=True, exist_ok=True)
    (fdir / "castings" / "casting-1-prompt.md").write_text(
        PROMPT_TEXT, encoding="utf-8"
    )
    # The acceptance gate resolves every `path#Symbol` cite in the completion
    # report against the tree, so the symbol the report cites has to be a real
    # one. Cheaper than weakening the report, and it keeps these tests honest
    # about the rung they are NOT the subject of.
    (tmp_path / "src").mkdir(parents=True, exist_ok=True)
    (tmp_path / "src" / "gate.py").write_text(
        "def accept_casting():\n    return True\n", encoding="utf-8"
    )
    try:
        yield str(tmp_path), fdir
    finally:
        clear_active_run()


def _records(fdir: Path) -> list[dict]:
    path = fdir / "handoffs.jsonl"
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _lead_fixes(fdir: Path) -> list[dict]:
    return [r for r in _records(fdir) if r["event"] == HANDOFF_EVENT_LEAD_FIX]


def _mirror_labels(fdir: Path) -> list[str]:
    """The bullet labels of the LAST handoffs.md block, in written order.

    The mirror's ORDER is part of its shape: a reader diffing the two channels
    reads them side by side, so a field that lands in one position in the JSONL
    and another in the markdown is a disagreement even when both carry it.
    """
    block = (fdir / "handoffs.md").read_text(encoding="utf-8").split("## ")[-1]
    return [
        line[2:].split(":", 1)[0]
        for line in block.splitlines()
        if line.startswith("- ")
    ]


def _accept(root: str, **overrides) -> dict:
    args = {
        "casting_id": 1,
        "spec_hash": foundry_spec_hash(project_root=root)["spec_hash"],
        "prompt_hash": _hash_str(PROMPT_TEXT),
        "completion_report": "AC-015: implemented at src/gate.py#accept_casting\n",
        "project_root": root,
        "casting_commit": CASTING_COMMIT,
    }
    args.update(overrides)
    return foundry_accept_casting(**args)


# --- convergence GI-003 / AC-022 / OT-010: the lead_fix record ---------------
def test_a_lead_fix_appends_a_record_carrying_every_named_field(run_env):
    """convergence OT-010 verbatim: 'After a lead fix, handoffs.jsonl contains a lead_fix
    record with the defect id, tier, file, line count and test.' convergence GI-003 adds
    that the SERVER appends it — a lead fix recorded as free prose in a
    hand-written handoff is a fix nothing can total, list, or re-measure."""
    _, fdir = run_env

    returned = record_lead_fix_handoff(
        fdir,
        defect_id="D-042",
        tier="LIVE",
        file="src/api/handler.py",
        line_count=14,
        test="tests/test_handler.py::test_rejects_unwired_call",
        fix_commit="abc1234",
    )

    records = _lead_fixes(fdir)
    assert len(records) == 1, records
    record = records[0]
    assert record == returned, "the returned record is the record written"
    assert record["event"] == HANDOFF_EVENT_LEAD_FIX
    assert record["defect_id"] == "D-042"
    assert record["tier"] == "LIVE"
    assert record["file"] == "src/api/handler.py"
    assert record["line_count"] == 14
    assert record["test"] == "tests/test_handler.py::test_rejects_unwired_call"
    assert record["fix_commit"] == "abc1234"
    assert record["timestamp"] and record["handoff_id"]


def test_a_latent_lead_fix_is_measured_like_any_other(run_env):
    """D-074 — convergence ST-004 / CT-006's 'a LATENT lead fix is not measured' is about
    the lane ELIGIBILITY test, not about whether the numbers are written down.
    convergence GI-003 states the field list with no tier carve-out, and the caller
    measures on both lanes, so a LATENT record carries its real file and count
    and is described as 'recorded, lane limit not applied'."""
    _, fdir = run_env

    record = record_lead_fix_handoff(
        fdir,
        defect_id="D-043",
        tier="LATENT",
        files=[
            {
                "path": "src/foundry_mcp/tools/foundry_report.py",
                "added": 48,
                "deleted": 16,
                "renamed_from": None,
            }
        ],
        test="tests/test_sweep.py::test_covers_both_roots",
        fix_commit="def5678",
    )

    assert record["file"] == "src/foundry_mcp/tools/foundry_report.py"
    assert record["line_count"] == 64
    assert record["fix_commit"] == "def5678", (
        "fix_commit is required on EVERY lead fix regardless of tier; only the "
        "lane LIMIT is not applied to LATENT"
    )
    mirror = (fdir / "handoffs.md").read_text(encoding="utf-8")
    assert "lane: recorded, lane limit not applied (LATENT)" in mirror
    assert "unmeasured" not in mirror


def test_none_in_the_measurement_means_git_could_not_read_the_commit(run_env):
    """D-074 — with the caller measuring on both lanes, the ONLY remaining way
    a record reaches None is git failing to read the commit. Labelling that
    'unmeasured (LATENT)' asserted a deliberate policy skip in exactly the case
    where the measurement had FAILED, which is the D-046 failure relocated
    rather than closed. The fields are None rather than absent so every record
    has one shape."""
    _, fdir = run_env

    record = record_lead_fix_handoff(
        fdir,
        defect_id="D-046",
        tier="LATENT",
        test="tests/test_sweep.py::test_covers_both_roots",
        fix_commit="0" * 40,
    )

    assert record["file"] is None
    assert record["line_count"] is None
    assert record["files"] is None
    assert "file" in record and "line_count" in record

    mirror = (fdir / "handoffs.md").read_text(encoding="utf-8")
    assert "file: measurement unavailable — git could not read the commit" in mirror
    assert (
        "line_count: measurement unavailable — git could not read the commit" in mirror
    )
    assert "unmeasured" not in mirror


def test_a_multi_file_commit_reports_its_files_rather_than_a_blank(run_env):
    """D-074 — ``file`` is the single path when exactly ONE non-test file
    changed and None otherwise, so None on a three-file commit is not a failed
    measurement and must not read as one. The rows are on the record, and the
    mirror names them."""
    _, fdir = run_env

    record = record_lead_fix_handoff(
        fdir,
        defect_id="D-047",
        tier="LIVE",
        files=[
            {"path": "src/a.py", "added": 3, "deleted": 1, "renamed_from": None},
            {"path": "src/b.py", "added": 2, "deleted": 0, "renamed_from": "src/c.py"},
        ],
        test="tests/test_ab.py::test_both",
        fix_commit="feed001",
    )

    assert record["file"] is None
    assert record["line_count"] == 6
    assert [r["path"] for r in record["files"]] == ["src/a.py", "src/b.py"]

    mirror = (fdir / "handoffs.md").read_text(encoding="utf-8")
    assert "file: 2 non-test files: src/a.py, src/b.py" in mirror
    assert "line_count: 6" in mirror
    assert "lane: measured against the LIVE lead lane" in mirror


def test_the_rows_are_recorded_as_handed_over_not_re_classified(run_env):
    """convergence FR-016 excludes test files from the count, and ``_numstat_measurement``
    is where that happens — D-075 made it rename-aware, so a file now living
    under tests/ but renamed out of src/ is production code and STAYS in the
    measurement. A second ``is_test_file(path)`` pass here would drop exactly
    the row that rule keeps, and the audit record would disagree with the lane
    measurement it exists to make re-derivable. One classifier, at the
    measurement."""
    _, fdir = run_env

    record = record_lead_fix_handoff(
        fdir,
        defect_id="D-048",
        tier="LIVE",
        files=[
            {
                "path": "tests/helpers/sweep.py",
                "added": 7,
                "deleted": 2,
                "renamed_from": "src/sweep.py",
            }
        ],
        test="tests/test_sweep.py::test_guard",
        fix_commit="feed002",
    )

    assert record["file"] == "tests/helpers/sweep.py", (
        "a row the measurement kept is a row the record keeps — this writer "
        "does not second-guess the classification"
    )
    assert record["line_count"] == 9
    assert [r["path"] for r in record["files"]] == ["tests/helpers/sweep.py"]


def test_a_binary_row_counts_a_file_and_no_lines(run_env):
    """numstat reports ``-`` for both cells on a binary file. It contributes a
    file and zero lines — the honest reading, and the same one
    ``_numstat_measurement`` takes — and it never raises on the way through."""
    _, fdir = run_env

    record = record_lead_fix_handoff(
        fdir,
        defect_id="D-049",
        tier="LIVE",
        files=[{"path": "src/logo.png", "added": "-", "deleted": "-", "renamed_from": None}],
        test="tests/test_assets.py::test_logo",
        fix_commit="feed003",
    )

    assert record["file"] == "src/logo.png"
    assert record["line_count"] == 0
    mirror = (fdir / "handoffs.md").read_text(encoding="utf-8")
    assert "line_count: 0" in mirror, "a measured zero is not an absent measurement"


def test_the_pre_ruling_call_shape_still_records(run_env):
    """The caller (``foundry_mark_defect_fixed``, casting 3's file) moves to
    ``files=`` in the same GRIND cycle this writer gained it. A writer that
    demanded the new shape would make the tree red between the two commits, so
    ``file``/``line_count`` stay accepted and authoritative when no ``files``
    list is handed over."""
    _, fdir = run_env

    record = record_lead_fix_handoff(
        fdir,
        defect_id="D-050",
        tier="LIVE",
        file="src/api/handler.py",
        line_count=11,
        test="tests/test_handler.py::test_guard",
        fix_commit="feed004",
    )

    assert record["file"] == "src/api/handler.py"
    assert record["line_count"] == 11
    assert record["files"] is None


# --- D-170: one field per locator; `test` is not a slot two tests compete for -
def test_a_regression_test_is_recorded_beside_the_adjacent_path_test(run_env):
    """convergence GI-003 / AC-022 — the test is one of the five things a reader must be
    able to re-derive, and for a LIVE lead fix the test that holds the fix is
    the MANDATED adjacent-path test.

    Driven at the door: a LIVE lead fix supplying both the mandated
    adjacent-path test and an optional regression test was accepted, and the
    record — plus its handoffs.md mirror row — carried only the optional
    regression locator, because the caller resolved a one-field collision with
    ``test=regression_ref or test_ref``. An auditor asking which test held a
    fix nobody else reviewed was shown a different one. Two locators, two
    fields, both channels."""
    _, fdir = run_env

    record = record_lead_fix_handoff(
        fdir,
        defect_id="D-170",
        tier="LIVE",
        file="src/foundry_mcp/tools/foundry_handoff.py",
        line_count=9,
        test="tests/test_lane.py::test_retry_branch_still_reaches_helper",
        regression_test="tests/test_lane.py::test_lane_holds",
        fix_commit="d170abc",
    )

    assert record["test"] == (
        "tests/test_lane.py::test_retry_branch_still_reaches_helper"
    ), "the optional locator displaced the mandated one again"
    assert record["regression_test"] == "tests/test_lane.py::test_lane_holds"
    assert list(record) == [
        "handoff_id",
        "timestamp",
        "event",
        "defect_id",
        "tier",
        "file",
        "line_count",
        "files",
        "test",
        "regression_test",
        "fix_commit",
    ]

    mirror = (fdir / "handoffs.md").read_text(encoding="utf-8")
    assert "test: `tests/test_lane.py::test_retry_branch_still_reaches_helper`" in mirror
    assert "regression_test: `tests/test_lane.py::test_lane_holds`" in mirror
    assert _mirror_labels(fdir) == [
        "defect_id",
        "tier",
        "lane",
        "file",
        "line_count",
        "test",
        "regression_test",
        "fix_commit",
    ], "the two channels order the same field differently"


def test_a_fix_naming_no_regression_test_keeps_the_record_shape_it_had(run_env):
    """The field is emitted ONLY when a regression locator was named — in both
    channels — so every record written without one is byte-identical to the
    shape that predates D-170.

    That is not cosmetic: the evidence logs committed against this writer are
    re-executed byte-for-byte at the GRIND boundary, and a key added
    unconditionally would fail every one of them for a field nobody claimed.
    It is also why this differs from ``files``, which IS null-when-absent: an
    absent measurement is a fact about the commit, an absent regression test is
    just a locator nobody named."""
    _, fdir = run_env

    record = record_lead_fix_handoff(
        fdir,
        defect_id="D-171",
        tier="LIVE",
        file="src/a.py",
        line_count=4,
        test="tests/test_a.py::test_adjacent",
        fix_commit="d171def",
    )

    assert "regression_test" not in record
    assert list(record) == [
        "handoff_id",
        "timestamp",
        "event",
        "defect_id",
        "tier",
        "file",
        "line_count",
        "files",
        "test",
        "fix_commit",
    ]
    assert _mirror_labels(fdir) == [
        "defect_id",
        "tier",
        "lane",
        "file",
        "line_count",
        "test",
        "fix_commit",
    ]
    assert "regression_test" not in (fdir / "handoffs.md").read_text(encoding="utf-8")


def test_an_empty_regression_test_is_no_regression_test(run_env):
    """The caller strips the argument before it arrives, so what reaches here
    for an unclaimed locator is ``""`` as readily as ``None``. Writing a key
    whose value is an empty string would put a blank cell in the report's
    regression column and an empty bullet in the mirror — an auditor reads that
    as a locator that could not be resolved rather than one nobody claimed."""
    _, fdir = run_env

    record = record_lead_fix_handoff(
        fdir,
        defect_id="D-172",
        tier="LATENT",
        file="src/b.py",
        line_count=2,
        test="tests/test_b.py::test_regression",
        regression_test="",
        fix_commit="d172aaa",
    )

    assert "regression_test" not in record
    assert "regression_test" not in (fdir / "handoffs.md").read_text(encoding="utf-8")


def test_the_lead_fix_record_is_mirrored_into_handoffs_md(run_env):
    """convergence GI-003 — the audit log has two channels and is only useful while they
    agree. The lead_fix record goes through the SAME writer ``foundry_handoff``
    uses, so a reader of the human mirror sees the fix a reader of the JSONL
    sees."""
    _, fdir = run_env

    record_lead_fix_handoff(
        fdir,
        defect_id="D-044",
        tier="LIVE",
        file="src/api/handler.py",
        line_count=3,
        test="tests/test_handler.py::test_guard",
        fix_commit="cafe123",
    )

    mirror = (fdir / "handoffs.md").read_text(encoding="utf-8")
    assert mirror.startswith("# Foundry Handoff Audit Log")
    assert f"## {HANDOFF_EVENT_LEAD_FIX} —" in mirror
    assert "D-044" in mirror
    assert "LIVE" in mirror
    assert "src/api/handler.py" in mirror
    assert "line_count: 3" in mirror
    assert "tests/test_handler.py::test_guard" in mirror
    assert "cafe123" in mirror


def test_the_mirror_names_a_failed_measurement_rather_than_printing_nothing(run_env):
    """The mirror skips EMPTY values, so a None file would silently vanish from
    the human channel and read as a fix in no file at all. It is spelled out
    instead — and D-074 fixed WHAT it spells: the fact is that git could not
    read the commit, not that a measurement was deliberately skipped."""
    _, fdir = run_env

    record_lead_fix_handoff(
        fdir,
        defect_id="D-045",
        tier="LATENT",
        file=None,
        line_count=None,
        test="tests/test_sweep.py::test_x",
        fix_commit="beef456",
    )

    mirror = (fdir / "handoffs.md").read_text(encoding="utf-8")
    assert "file: measurement unavailable — git could not read the commit" in mirror
    assert (
        "line_count: measurement unavailable — git could not read the commit" in mirror
    )
    assert "unmeasured (LATENT)" not in mirror, (
        "the label asserted a deliberate policy skip in the one case where the "
        "measurement had FAILED"
    )


def test_lead_fix_records_share_the_log_with_ordinary_handoffs(run_env):
    """Two writers, one log. Both channels must carry both record kinds, in
    order — a lead_fix record in a file of its own would not be an audit trail
    of the run, it would be a second one nobody reads."""
    root, fdir = run_env

    foundry_handoff(
        event="inspect_to_grind",
        summary="cycle 1 defects to tasks",
        source_reread=True,
        project_root=root,
    )
    record_lead_fix_handoff(
        fdir,
        defect_id="D-046",
        tier="LIVE",
        file="src/a.py",
        line_count=2,
        test="tests/test_a.py::test_b",
        fix_commit="0ddba11",
    )
    foundry_handoff(
        event="grind_to_inspect", summary="cycle 1 fixes", project_root=root
    )

    events = [r["event"] for r in _records(fdir)]
    assert events == ["inspect_to_grind", HANDOFF_EVENT_LEAD_FIX, "grind_to_inspect"]

    mirror = (fdir / "handoffs.md").read_text(encoding="utf-8")
    assert mirror.count("# Foundry Handoff Audit Log") == 1, (
        "the header is bootstrapped once, by whichever writer got there first"
    )


def test_the_event_name_is_read_from_the_vocabulary(run_env):
    """The closed vocabulary lives in vocab.py and is READ, never re-typed —
    the writer, the report generator and the F6 section that lists these must
    all spell the event identically or the report lists nothing."""
    _, fdir = run_env

    record = record_lead_fix_handoff(
        fdir,
        defect_id="D-047",
        tier="LIVE",
        file="src/a.py",
        line_count=1,
        test="tests/test_a.py::test_b",
        fix_commit="1234abc",
    )

    assert record["event"] == HANDOFF_EVENT_LEAD_FIX == "lead_fix"


def test_the_writer_creates_the_run_directory_it_is_handed(tmp_path):
    """The fix door may append the first handoff a run has ever written. The
    writer must not depend on some earlier call having made the directory."""
    fdir = tmp_path / "foundry-archive" / "never-created"

    record_lead_fix_handoff(
        fdir,
        defect_id="D-048",
        tier="LIVE",
        file="src/a.py",
        line_count=1,
        test="tests/test_a.py::test_b",
        fix_commit="99ff00a",
    )

    assert (fdir / "handoffs.jsonl").is_file()
    assert (fdir / "handoffs.md").is_file()


# --- convergence CT-011 / AC-030: the reported prompt hash -------------------
def test_a_matching_reported_hash_returns_none(run_env):
    """convergence CT-011 — pointer dispatch hands the teammate a path and a hash instead
    of the prompt text; only an agent that actually read the file can state
    the value back. None means 'this one did'."""
    _, fdir = run_env

    assert check_reported_prompt_hash(fdir, 1, _hash_str(PROMPT_TEXT)) is None


def test_the_comparison_value_is_the_published_spelling(run_env):
    """The dispatch block tells the teammate to state the hash 'character for
    character', and what it publishes is ``sha256:`` + the first 16 hex
    digits. A bare hexdigest or the full 64 here would make every honest
    report a mismatch, so the checker must agree with the publisher exactly."""
    import hashlib

    _, fdir = run_env
    full = hashlib.sha256(PROMPT_TEXT.encode("utf-8")).hexdigest()

    assert check_reported_prompt_hash(fdir, 1, f"sha256:{full[:16]}") is None
    assert check_reported_prompt_hash(fdir, 1, full) is not None
    assert check_reported_prompt_hash(fdir, 1, f"sha256:{full}") is not None


def test_a_mismatched_hash_refuses_naming_expected_and_reported(run_env):
    """convergence AC-030 verbatim: 'Foundry-Accept-Casting and Foundry-Fix refuse when the
    hash the teammate reports differs from the file's.' Both values are named:
    a refusal that reports only 'mismatch' leaves the lead unable to tell a
    teammate who read a STALE prompt from one who read no prompt at all."""
    _, fdir = run_env

    refusal = check_reported_prompt_hash(fdir, 1, "sha256:deadbeefdeadbeef")

    assert refusal is not None
    assert refusal["ok"] is False
    assert refusal["error"] == "stale_prompt_hash"
    assert refusal["expected_hash"] == _hash_str(PROMPT_TEXT)
    assert refusal["reported_hash"] == "sha256:deadbeefdeadbeef"
    assert refusal["expected_hash"] in refusal["hint"]
    assert "deadbeefdeadbeef" in refusal["hint"]


def test_a_reported_hash_of_none_is_a_mismatch(run_env):
    """A teammate who reported nothing has not been shown to have read the
    prompt. Absence is not agreement."""
    _, fdir = run_env

    refusal = check_reported_prompt_hash(fdir, 1, None)

    assert refusal is not None
    assert refusal["reported_hash"] is None


def test_a_missing_prompt_file_refuses_rather_than_returning_none(run_env):
    """'An unreadable or missing prompt file returns the house
    ``document_refusal`` rather than None: nothing was compared, and I could
    not read the file is not the same answer as the hashes agree.'"""
    _, fdir = run_env

    refusal = check_reported_prompt_hash(fdir, 9, "sha256:whatever")

    assert refusal is not None
    assert refusal["ok"] is False
    assert "casting-9-prompt.md" in refusal["error"]
    assert refusal["hint"]


def test_an_undecodable_prompt_file_refuses_in_band(run_env):
    """convergence NFR-005 — a tool never raises across the MCP boundary. The read and the
    decode are ONE operation: ``UnicodeDecodeError`` is not a JSONDecodeError
    and is raised before any parse, which is the D-137 family this rung would
    otherwise re-open."""
    _, fdir = run_env
    (fdir / "castings" / "casting-2-prompt.md").write_bytes(b"\xff\xfe not utf-8")

    refusal = check_reported_prompt_hash(fdir, 2, "sha256:whatever")

    assert refusal is not None
    assert refusal["ok"] is False
    assert "casting-2-prompt.md" in refusal["error"]


# --- D-108: the published hash is over the file's BYTES ----------------------
#
# The publisher and the checker have to agree with a THIRD party the teammate
# actually uses: the shell command `agents/teammate.md` documents for producing
# the hash. Only the bytes digest is computable by all three.
CRLF_PROMPT = b"# Casting 3\r\n\r\n<spec_requirements>\r\nAC-030\r\n</spec_requirements>\r\n"


def _shell_style_digest(path: Path) -> str:
    """What `sha256sum` / `shasum -a 256` prints, in the published spelling.

    Deliberately NOT routed through either module under test: a pin where both
    sides derive the value the same wrong way is green on a defect."""
    import hashlib

    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def test_a_crlf_prompt_hashes_identically_on_both_sides(run_env):
    """D-108 / convergence CT-011 / AC-030 — the publisher, the checker and the teammate's
    own shell command produce ONE value.

    Both sides used to hash ``prompt_text.encode("utf-8")`` where the text came
    from a TEXT read, which applies universal-newline translation — so every
    ``\\r\\n`` was gone before the digest was taken. The teammate's command
    hashes the file and cannot translate anything, so on a CRLF prompt the
    honest report was refused as stale, and the refusal's own remedy ('re-read
    the prompt file in full') produced the same right answer again."""
    from foundry_mcp.tools import foundry_spawn as fs

    _, fdir = run_env
    path = fdir / "castings" / "casting-3-prompt.md"
    path.write_bytes(CRLF_PROMPT)

    published = fs._published_prompt_hash(path)

    assert published == _shell_style_digest(path), "publisher disagrees with the shell"
    assert check_reported_prompt_hash(fdir, 3, published) is None, (
        "the checker refused the value the publisher published"
    )


def test_the_decoded_text_digest_is_a_different_value_and_is_refused(run_env):
    """The falsifier for the test above: on a CRLF file the two derivations
    genuinely differ, so the agreement asserted there is not vacuous."""
    _, fdir = run_env
    path = fdir / "castings" / "casting-3-prompt.md"
    path.write_bytes(CRLF_PROMPT)

    text_digest = _hash_str(path.read_text(encoding="utf-8"))

    assert text_digest != _shell_style_digest(path), (
        "this file's text and bytes digests agree, so it pins nothing — give "
        "it CRLF line endings"
    )
    assert check_reported_prompt_hash(fdir, 3, text_digest) is not None


def test_an_lf_prompt_is_unchanged_by_the_bytes_rule(run_env):
    """...and the change is confined to files the old rule got wrong. Every
    prompt foundry writes itself is LF, so a run in flight when this landed
    keeps every hash it had already published."""
    from foundry_mcp.tools import foundry_spawn as fs

    _, fdir = run_env
    path = fdir / "castings" / "casting-1-prompt.md"

    assert fs._published_prompt_hash(path) == _hash_str(PROMPT_TEXT)
    assert check_reported_prompt_hash(fdir, 1, _hash_str(PROMPT_TEXT)) is None


def test_neither_spawn_door_derives_a_prompt_hash_inline():
    """Both publish sites go through the one helper (D-108).

    Two doors that each hash the prompt themselves is the D-119 shape: they
    agree until one is edited. The structural pin is what keeps the CRLF
    property from being re-broken at whichever door the next author touches."""
    import ast

    from foundry_mcp.tools import foundry_spawn as fs

    tree = ast.parse(Path(fs.__file__).read_text(encoding="utf-8"))

    # Asked of the AST rather than of the text, so the prose that EXPLAINS the
    # old spelling in `_published_prompt_hash`'s docstring cannot fail the pin
    # that forbids it — a scan a comment can trip teaches the next author to
    # delete the comment.
    hashing_functions = sorted(
        node.name
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef)
        and any(
            isinstance(call.func, ast.Attribute)
            and call.func.attr == "sha256"
            for call in ast.walk(node)
            if isinstance(call, ast.Call)
        )
    )
    assert hashing_functions == ["_published_prompt_hash"], (
        f"{hashing_functions} derive a digest in this module. Exactly one may: "
        f"two doors that each hash the prompt agree until one is edited, which "
        f"is how the CRLF gap (D-108) reached both publish sites at once."
    )

    calls = sum(
        1
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "_published_prompt_hash"
    )
    assert calls == 2, (
        f"the helper is called {calls} times; the single door and the bulk "
        f"door must both publish through it"
    )


# --- D-106: `lead_fix` is a token only the server may write ------------------
def test_the_public_handoff_door_refuses_the_reserved_lead_fix_event(run_env):
    """convergence GI-003 / AC-022 — 'the server itself appends a lead_fix handoff record'.

    That is only a guarantee if the token cannot ALSO be written by hand.
    Driven: `foundry_handoff(event="lead_fix", summary="hand-written, never
    measured")` returned ok=True, and the generated report then read '2
    lead-authored fixes (convergence GI-003 / AC-022), 2 file rows' — the forged row
    rendering as 'measurement unavailable', which is D-078's sentinel for a
    real record whose git read failed. convergence GI-003's named violation is precisely
    'a lead fix recorded only as free prose in a hand-written handoff'."""
    root, fdir = run_env

    result = foundry_handoff(
        event=HANDOFF_EVENT_LEAD_FIX,
        summary="hand-written, never measured",
        project_root=root,
    )

    assert result["ok"] is False, result
    assert HANDOFF_EVENT_LEAD_FIX in result["error"]
    assert "record_lead_fix_handoff" in result["error"]
    assert "Foundry-Fix" in result["hint"]
    assert result["field"] == "event"
    assert result["reserved_event"] == HANDOFF_EVENT_LEAD_FIX


def test_a_refused_lead_fix_handoff_writes_to_neither_channel(run_env):
    """A refusal that had already appended is not a refusal. Both channels —
    the JSONL the report reads and the markdown a human reads — must be
    untouched, or the forgery survives in whichever one was written first."""
    root, fdir = run_env

    foundry_handoff(
        event=HANDOFF_EVENT_LEAD_FIX, summary="forged", project_root=root
    )

    assert _records(fdir) == []
    assert not (fdir / "handoffs.md").exists()


def test_the_reserved_rung_is_reached_before_the_run_is_resolved(run_env):
    """The rung reads its argument and nothing else, so it is the FIRST one —
    the house precondition-ladder shape, cheapest and most specific first. A
    caller with no active run still learns the real reason the call is wrong.
    """
    root, _fdir = run_env
    clear_active_run()

    result = foundry_handoff(
        event=HANDOFF_EVENT_LEAD_FIX, summary="forged", project_root=root
    )

    assert result["ok"] is False
    assert result["reserved_event"] == HANDOFF_EVENT_LEAD_FIX, (
        "with no active run the door answered 'No active foundry run' and "
        "never reached the reserved-token rung"
    )


# --- D-227: the rung reserves the NAME, not one spelling of it ---------------
@pytest.mark.parametrize(
    "spelling",
    ["LEAD_FIX", " lead_fix ", "Lead_Fix", "lead-fix", "lead fix", "leadfix"],
)
def test_every_spelling_of_the_reserved_name_is_refused(run_env, spelling):
    """convergence GI-003 — 'a lead fix recorded only as free prose in a
    hand-written handoff' is the named violation, and the rung that answers it
    compared `event == HANDOFF_EVENT_LEAD_FIX` with no normalisation. Driven
    through this door: 'lead_fix' was refused while 'LEAD_FIX', ' lead_fix '
    and 'Lead_Fix' each returned ok=True and were appended, putting
    '## LEAD_FIX', '##  lead_fix ' and '## Lead_Fix' into handoffs.md beside
    the server-written blocks, indistinguishable to the human who reads that
    file. The guard's subject was narrower than the harm it names."""
    root, fdir = run_env

    result = foundry_handoff(
        event=spelling, summary="hand-written, never measured", project_root=root
    )

    assert result["ok"] is False, result
    assert result["field"] == "event"
    assert result["reserved_event"] == HANDOFF_EVENT_LEAD_FIX
    assert repr(spelling) in result["error"], (
        "the refusal must name the spelling that was passed, or a lead who "
        "typed 'LEAD_FIX' reads an error quoting a token they did not use"
    )

    # A refusal that had already appended is not a refusal — and the mirror is
    # the channel this defect actually reached.
    assert _records(fdir) == []
    assert not (fdir / "handoffs.md").exists()


@pytest.mark.parametrize("name", ["lead_fixes", "lead_fix_note", "inspect_to_grind"])
def test_a_name_that_merely_contains_the_reserved_word_is_still_admitted(run_env, name):
    """The converse of the test above, and the reason it is here: a guard
    widened until it cannot be evaded is worthless if it also swallows the
    events the log exists to carry. The equivalence stops at the word — only
    the reserved NAME is reserved, not everything that mentions it."""
    root, fdir = run_env

    result = foundry_handoff(event=name, summary="ordinary handoff", project_root=root)

    assert result["ok"] is True, result
    assert [r["event"] for r in _records(fdir)] == [name]
    assert f"## {name} —" in (fdir / "handoffs.md").read_text(encoding="utf-8")


def test_the_server_writer_still_appends_the_reserved_event(run_env):
    """The token is reserved TO the server, not retired. `Foundry-Fix`'s own
    writer must still land the record the F6 report reads, or reserving the
    name would have closed the forgery by closing the feature."""
    root, fdir = run_env

    record_lead_fix_handoff(
        fdir,
        defect_id="D-106",
        tier="LIVE",
        file="src/a.py",
        line_count=3,
        test="tests/test_a.py::test_b",
        fix_commit="0ddba11",
    )
    assert foundry_handoff(
        event=HANDOFF_EVENT_LEAD_FIX, summary="forged", project_root=root
    )["ok"] is False

    lead_fixes = _lead_fixes(fdir)

    assert len(lead_fixes) == 1, (
        "the count a report reads must be the number of fixes the server "
        "MEASURED, not that plus whatever was typed at it"
    )
    assert lead_fixes[0]["defect_id"] == "D-106"
    assert lead_fixes[0]["fix_commit"] == "0ddba11"


def test_the_acceptance_gate_uses_the_shared_hash_check(run_env):
    """convergence CT-011 — one implementation for both gates. The acceptance gate's
    refusal is the helper's, token included, so a lead cannot learn two
    different stories about one prompt depending on which door they walked."""
    root, fdir = run_env

    result = _accept(root, prompt_hash="sha256:deadbeefdeadbeef")

    assert result["ok"] is False
    assert result["error"] == "stale_prompt_hash"
    assert result["expected_hash"] == _hash_str(PROMPT_TEXT)
    assert result["reported_hash"] == "sha256:deadbeefdeadbeef"


# --- convergence CT-015 / AC-015 / FR-010 / OT-027: casting_commit is required ---
def test_acceptance_without_a_casting_commit_is_refused_naming_it(run_env):
    """convergence OT-027 verbatim: 'Foundry-Accept-Casting without casting_commit is
    refused naming the parameter.'

    convergence FR-010's rule is 'No acceptance without EVID-01/EVID-02 running', and an
    optional casting_commit was that rule's exact negation: omitting it
    bypassed both checks and still returned ok:true — a green acceptance that
    verified nothing, which is the most expensive failure mode there is
    because the run proceeds on it."""
    root, _ = run_env

    result = _accept(root, casting_commit=None)

    assert result["ok"] is False, result
    assert "casting_commit" in result["error"]
    assert result["field"] == "casting_commit"
    assert result["hint"]


@pytest.mark.parametrize("empty", ["", None])
def test_an_empty_casting_commit_is_refused_too(run_env, empty):
    """An empty string is not a commit. It would reach ``verify_evidence`` and
    fail there, one rung further along and named as an evidence failure rather
    than as the missing parameter it is."""
    root, _ = run_env

    result = _accept(root, casting_commit=empty)

    assert result["ok"] is False
    assert result["field"] == "casting_commit"


def test_the_commit_refusal_is_the_first_rung(run_env):
    """convergence CT-015 — 'positioned so the refusal is reached before any worktree or
    subprocess work'. Driven with EVERY other precondition also broken: a
    missing required parameter is a fault in the CALL, so a lead must not have
    to produce a fresh spec hash before learning they omitted it."""
    root, _ = run_env

    result = _accept(
        root,
        casting_commit=None,
        spec_hash="sha256:staleeeeeeeeeeee",
        prompt_hash="sha256:alsostaleeeeeee",
        casting_id=99,
    )

    assert result["field"] == "casting_commit", result
    assert result["error"] != "stale_spec_hash"


def test_a_successful_acceptance_always_carries_evidence_provenance(run_env):
    """convergence CT-015 verbatim: 'acceptance with evidence_provenance always populated'.

    On a v2.0 spec the list is legitimately EMPTY and a stream-skip is
    recorded — 'always populated' means the key always carries the RESULT of
    running the evidence path, never a default left behind by a bypassed
    block. That distinction is the whole of the change: the old default and a
    real v2.0 result looked identical to the lead."""
    root, _ = run_env

    result = _accept(root)

    assert result["ok"] is True, result.get("warning")
    assert "evidence_provenance" in result
    assert isinstance(result["evidence_provenance"], list)
    assert result["evidence_verdict"] is not None, (
        "a verdict of None is what a BYPASSED evidence path used to return; "
        "the path now always runs and always reports"
    )
    assert result["evidence_spec_path"] is not None


def test_the_handler_keeps_a_none_default_so_the_refusal_is_its_own(run_env):
    """The refusal must read identically however the call arrived — over MCP,
    from a test, or from another handler. A required-by-signature parameter
    would make an omission a TypeError at one door and a named refusal at the
    next, and a traceback across the MCP boundary is the one thing the house
    rule forbids."""
    import inspect

    # fallout GI-026 / GI-033 (D-192): the pin follows the handler. It moved to
    # `tools/evidence.py` because it RUNS `verify_evidence`, and a pin left
    # naming the module it came from reads zero attributes rather than the one
    # it is asserting about.
    from foundry_mcp.tools import evidence as accept_module

    params = inspect.signature(accept_module.foundry_accept_casting).parameters
    assert params["casting_commit"].default is None


# --- D-180: the gate demands what the casting DECLARES, not what it quotes ---
#
# `foundry_accept_casting` harvested requirement IDs with a bare
# `REQUIREMENT_ID_RE.findall` over the whole `<spec_requirements>` block, which
# cannot tell a requirement ASSIGNED to the casting from one QUOTED as an
# example inside another requirement's prose. Driven on this run: casting 2's
# block names convergence NFR-002 exactly once, inside convergence OT-005's own
# statement text, and carries no convergence NFR-002 requirement line — yet the
# gate demanded a citation and an evidence binding for it and refused
# acceptance with
# EVIDENCE_REQUIREMENT_UNBOUND for a requirement casting 5 owns.
#
# `_QUOTING_BLOCK` reproduces that shape in miniature: one declared requirement
# whose own prose names another requirement as an example.
_QUOTING_BLOCK = (
    "- **AC-015** [derived from A-031]: the gate refuses without casting_commit;\n"
    "  a LATENT filing citing NFR-002 with a scan-gap description is accepted.\n"
)
_QUOTING_PROMPT = (
    "# Casting 1\n\n"
    f"<spec_requirements>\n{_QUOTING_BLOCK}</spec_requirements>\n"
)


def test_a_requirement_quoted_in_another_requirements_prose_is_not_owned(run_env):
    """D-180 at the door that refused: the harvested set is the DECLARED set.

    convergence NFR-002 appears once in this block, inside convergence
    AC-015's own statement, and the casting has no convergence NFR-002 line.
    Before this change the gate collected it and then demanded of the teammate
    a citation and an evidence binding for a
    requirement another casting owns — a demand no honest report can satisfy,
    whose only workaround was a knowingly false `# evidence-for:` header.
    """
    root, fdir = run_env
    (fdir / "castings" / "casting-1-prompt.md").write_text(
        _QUOTING_PROMPT, encoding="utf-8"
    )

    result = _accept(root, prompt_hash=_hash_str(_QUOTING_PROMPT))

    assert result["requirement_ids"] == ["AC-015"], result["requirement_ids"]
    assert "NFR-002" not in result["requirement_ids"]


def test_the_citation_check_reads_the_same_declared_set(run_env):
    """The SECOND consumer of the one list, reached through the same door.

    `casting_req_ids` feeds both the EVID-02 binding check and the 300-char
    citation window, so the wrong subject was demanded twice from one mistake.
    A report citing only what the casting declares is complete; the quoted ID
    is not missing, because it was never this casting's to cite.
    """
    root, fdir = run_env
    (fdir / "castings" / "casting-1-prompt.md").write_text(
        _QUOTING_PROMPT, encoding="utf-8"
    )

    result = _accept(root, prompt_hash=_hash_str(_QUOTING_PROMPT))

    assert result["missing_citations"] == [], result["missing_citations"]
    assert result["ok"] is True, result.get("warning")


def test_a_declared_requirement_with_no_citation_is_still_named(run_env):
    """The falsifier for the test above: narrowing the subject must not
    disarm the check. A DECLARED requirement the report never cites is still
    named as missing, so "no missing citations" above means the report was
    complete rather than that the gate stopped looking."""
    root, fdir = run_env
    (fdir / "castings" / "casting-1-prompt.md").write_text(
        _QUOTING_PROMPT, encoding="utf-8"
    )

    result = _accept(
        root,
        prompt_hash=_hash_str(_QUOTING_PROMPT),
        completion_report="nothing was cited here\n",
    )

    assert result["missing_citations"] == ["AC-015"], result["missing_citations"]
    assert result["ok"] is False


def test_every_shape_decompose_emits_declares_its_requirement(run_env):
    """The narrower reading this fix REJECTS, pinned so it cannot be adopted.

    "Derive the IDs from each requirement's own `- **ID**` tag line" drops
    every typed-table row and story heading — on casting 2 that is 10 of its
    38 Locked requirement IDs, the convergence CT-001 through convergence
    CT-016 contract rows and convergence ST-009 among them,
    silently no longer demanding evidence. All four shapes F0.5 DECOMPOSE
    emits are declarations; only prose position is judged."""
    root, fdir = run_env
    block = (
        "## Contracts\n"
        "\n"
        "| ID     | surface | citation |\n"
        "|--------|---------|----------|\n"
        "| CT-015 | Foundry-Accept-Casting | [from A-031] |\n"
        "\n"
        "| ST-009 | F0 init requested | F0 refused |\n"
        "\n"
        "### US-006: A self-targeting run executes on its own build\n"
        "\n"
        "- **AC-015** [derived from A-031]: refused naming the parameter.\n"
        "- FR-010: no acceptance without EVID-01 running.\n"
        "  - Maps to: US-003\n"
    )
    prompt = f"# Casting 1\n\n<spec_requirements>\n{block}</spec_requirements>\n"
    (fdir / "castings" / "casting-1-prompt.md").write_text(prompt, encoding="utf-8")

    result = _accept(root, prompt_hash=_hash_str(prompt), completion_report="")

    assert result["requirement_ids"] == [
        "AC-015", "CT-015", "FR-010", "ST-009", "US-006",
    ], result["requirement_ids"]
    assert "US-003" not in result["requirement_ids"], (
        "`Maps to: US-003` is a cross-reference to another story, not a "
        "requirement this casting is answerable for"
    )


# --- D-180 adjacent path: the F0.9 validator, the OTHER caller of the one
# derivation. `foundry_validate_castings`' Dimension 1 answers the same
# question over the same text — a casting's `spec_text` IS the verbatim
# `<spec_requirements>` block its prompt carries — from a different module, in
# a different phase, with no surface that compares the two answers. When they
# disagree, F0.9 reports a requirement COVERED because some casting quoted it
# as an example while the acceptance gate demands evidence for it from nobody,
# and the run ships a requirement nothing verified.
def test_the_validator_and_the_gate_agree_on_what_a_casting_owns(tmp_path):
    """convergence AC-015 / FR-010 adjacent path — one derivation, two callers.

    Driven through `foundry_validate_castings` rather than through the
    acceptance gate: the casting DECLARES convergence AC-015 and merely quotes
    convergence NFR-002 inside its prose, so F0.9 must report convergence
    NFR-002 uncovered — the same verdict the gate reaches when it declines to
    demand it of this casting.
    """
    from foundry_mcp.tools.foundry_state import ARCHIVE_DIR, set_active_run
    from foundry_mcp.tools.foundry_validate import foundry_validate_castings

    run_name = "d180-validator-agreement"
    fdir = tmp_path / ARCHIVE_DIR / run_name
    (fdir / "castings").mkdir(parents=True, exist_ok=True)
    (fdir / "spec.md").write_text(
        "# Spec\n\n"
        "- **AC-015**: the gate refuses without casting_commit.\n"
        "- **NFR-002**: the report totals tokens and minutes.\n",
        encoding="utf-8",
    )
    (fdir / "castings" / "manifest.json").write_text(
        json.dumps(
            {
                "spec_type": "GREENFIELD",
                "castings": [
                    {
                        "id": "1",
                        "title": "the quoting casting",
                        "spec_text": _QUOTING_BLOCK,
                        # Prose scans on purpose: an observable truth names its
                        # requirements mid-sentence by design. Kept clear of
                        # both IDs so this test measures `spec_text` alone.
                        "observable_truths": ["a", "b", "c"],
                        "key_files": ["src/one.py"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    set_active_run(run_name)
    try:
        result = foundry_validate_castings(str(tmp_path))
    finally:
        clear_active_run()

    dim1 = result["dimensions"]["requirement_coverage"]
    uncovered = [i for i in dim1["issues"] if i["type"] == "uncovered_requirements"]
    assert uncovered, dim1
    assert uncovered[0]["ids"] == ["NFR-002"], (
        "F0.9 credited the casting with a requirement it only quoted, so the "
        "validator and the acceptance gate disagree about what it owns"
    )


def test_neither_reader_derives_the_declared_set_inline():
    """The KEY LINK, asserted where its loss would be silent (D-180).

    Two modules that each harvest the block themselves agree until one is
    edited — which is how the gate and the validator came to disagree in the
    first place. Pinned structurally: neither may reach for the raw scanner on
    a casting's own block again.

    THE DEFINER JOINED THE SCAN SET (D-191). `declared_requirement_ids` now
    lives in the leaf `tools/artifacts.py`, because the third reader —
    `tools/evidence.py` — is a verifier module and fallout GI-033 forbids it
    reading a
    lifecycle module for a symbol both layers use. A scan set naming only the
    two readers would find zero definitions among them, so the pin has to name
    the module that DEFINES it or the `== ["declared_requirement_ids"]`
    assertion below stops meaning "exactly one implementation" and starts
    meaning "still defined where it used to be"."""
    import ast

    from foundry_mcp.tools import artifacts as artifacts_module
    from foundry_mcp.tools import evidence as evidence_module
    from foundry_mcp.tools import foundry_validate as validate_module

    # fallout GI-026 (D-192) — AND THE READER SET MOVED AGAIN, THE SAME WAY.
    # `foundry_accept_casting` is the reader that made this a both-layers
    # symbol, and it is in `tools/evidence.py` now; `foundry_handoff.py` reads
    # the declaration rule nowhere at all any more. Naming it here would assert
    # a string is present in a module that has no reason to carry it, which is
    # the failure this docstring describes one module along.
    for module in (artifacts_module, evidence_module, validate_module):
        source = Path(module.__file__).read_text(encoding="utf-8")
        assert "declared_requirement_ids" in source, Path(module.__file__).name

    # The helper is DEFINED once. Asked of the AST rather than of the text so
    # the prose explaining the rejected reading cannot trip the pin.
    definitions = [
        node.name
        for module in (artifacts_module, evidence_module, validate_module)
        for node in ast.walk(
            ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
        )
        if isinstance(node, ast.FunctionDef)
        and node.name == "declared_requirement_ids"
    ]
    assert definitions == ["declared_requirement_ids"], definitions


def test_both_callers_derive_the_same_set_from_one_block(tmp_path):
    """D-180's whole point, asserted as AGREEMENT rather than twice separately.

    The two tests above each drive one caller on ``_QUOTING_BLOCK`` and each
    gets the right answer, which is not the same as showing the two answers are
    the SAME answer. This drives both on that one block in one test and
    compares them: the acceptance gate's demanded set (what it will require a
    citation and an evidence binding for) against the F0.9 validator's covered
    set (which requirements it credits this casting with). They were computed
    separately, with the same regex, over the same text, by two modules with no
    surface that compared them — so they could drift apart and the first sign
    would be a run shipping a requirement nothing verified.
    """
    from foundry_mcp.tools.foundry import foundry_init
    from foundry_mcp.tools.foundry_state import set_active_run
    from foundry_mcp.tools.foundry_validate import foundry_validate_castings

    # One spec naming both IDs, so "covered" and "uncovered" are complementary
    # halves of a known whole and the validator's answer can be read as a SET.
    spec_text = (
        "# Spec\n\n"
        "- **AC-015**: the gate refuses without casting_commit.\n"
        "- **NFR-002**: the report totals tokens and minutes.\n"
    )
    init = foundry_init(project_root=str(tmp_path))
    fdir = Path(init["foundry_dir"])
    (fdir / "spec.md").write_text(spec_text, encoding="utf-8")
    (fdir / "castings").mkdir(parents=True, exist_ok=True)
    (fdir / "castings" / "casting-1-prompt.md").write_text(
        _QUOTING_PROMPT, encoding="utf-8"
    )
    (fdir / "castings" / "manifest.json").write_text(
        json.dumps(
            {
                "spec_type": "GREENFIELD",
                "castings": [
                    {
                        "id": "1",
                        "title": "the quoting casting",
                        # The SAME block the prompt carries, which is what
                        # F0.5 DECOMPOSE stores here.
                        "spec_text": _QUOTING_BLOCK,
                        "observable_truths": ["a", "b", "c"],
                        "key_files": ["src/gate.py"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "src").mkdir(parents=True, exist_ok=True)
    (tmp_path / "src" / "gate.py").write_text(
        "def accept_casting():\n    return True\n", encoding="utf-8"
    )

    set_active_run(init["run_name"])
    try:
        gate = foundry_accept_casting(
            casting_id=1,
            spec_hash=foundry_spec_hash(project_root=str(tmp_path))["spec_hash"],
            prompt_hash=_hash_str(_QUOTING_PROMPT),
            completion_report="AC-015: implemented at src/gate.py#accept_casting\n",
            project_root=str(tmp_path),
            casting_commit=CASTING_COMMIT,
        )
        validator = foundry_validate_castings(str(tmp_path))
    finally:
        clear_active_run()

    gate_demands = set(gate["requirement_ids"])

    dim1 = validator["dimensions"]["requirement_coverage"]
    uncovered = {
        rid
        for issue in dim1["issues"]
        if issue["type"] == "uncovered_requirements"
        for rid in issue["ids"]
    }
    validator_credits = {"AC-015", "NFR-002"} - uncovered

    assert gate_demands == validator_credits, (
        f"the acceptance gate demands {sorted(gate_demands)} of this casting "
        f"while F0.9 credits it with {sorted(validator_credits)} — two "
        f"answers to one question, from one block"
    )
    assert gate_demands == {"AC-015"}, sorted(gate_demands)
    assert "NFR-002" not in gate_demands, (
        "both callers agree, but on the OLD answer: NFR-002 is quoted inside "
        "AC-015's prose and declared nowhere in this casting"
    )
