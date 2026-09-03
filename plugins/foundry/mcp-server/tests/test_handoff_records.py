"""Casting 2 — the lead_fix handoff record and the two acceptance rungs.

One regression test per acceptance criterion, each docstring quoting the
requirement it proves. Built on the synthetic-run shape
``tests/test_escalation.py`` establishes.

  GI-003 / AC-022 / OT-010   the SERVER appends the lead_fix record, carrying
                             the defect id, tier, file, line count and test.
                             BOTH tiers are measured; a LATENT record is
                             "recorded, lane limit not applied", and null in
                             file/line_count means the measurement was
                             UNAVAILABLE — git could not read the commit
                             (D-074).
  CT-011 / AC-030            ``check_reported_prompt_hash`` is the one
                             implementation of the pointer-dispatch hash rung,
                             and names both hashes when they differ.
  CT-015 / AC-015 / FR-010 / OT-027
                             ``foundry_accept_casting`` without casting_commit
                             is refused naming the parameter, and a successful
                             acceptance always carries evidence_provenance.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from foundry_mcp.schemas.vocab import HANDOFF_EVENT_LEAD_FIX
from foundry_mcp.tools.foundry import foundry_init
from foundry_mcp.tools.foundry_handoff import (
    _hash_str,
    check_reported_prompt_hash,
    foundry_accept_casting,
    foundry_handoff,
    foundry_spec_hash,
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


# --- GI-003 / AC-022 / OT-010: the lead_fix record ---------------------------
def test_a_lead_fix_appends_a_record_carrying_every_named_field(run_env):
    """OT-010 verbatim: 'After a lead fix, handoffs.jsonl contains a lead_fix
    record with the defect id, tier, file, line count and test.' GI-003 adds
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
    """D-074 — ST-004 / CT-006's 'a LATENT lead fix is not measured' is about
    the lane ELIGIBILITY test, not about whether the numbers are written down.
    GI-003 states the field list with no tier carve-out, and the caller
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
    """FR-016 excludes test files from the count, and ``_numstat_measurement``
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


def test_the_lead_fix_record_is_mirrored_into_handoffs_md(run_env):
    """GI-003 — the audit log has two channels and is only useful while they
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


# --- CT-011 / AC-030: the reported prompt hash -------------------------------
def test_a_matching_reported_hash_returns_none(run_env):
    """CT-011 — pointer dispatch hands the teammate a path and a hash instead
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
    """AC-030 verbatim: 'Foundry-Accept-Casting and Foundry-Fix refuse when the
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
    """NFR-005 — a tool never raises across the MCP boundary. The read and the
    decode are ONE operation: ``UnicodeDecodeError`` is not a JSONDecodeError
    and is raised before any parse, which is the D-137 family this rung would
    otherwise re-open."""
    _, fdir = run_env
    (fdir / "castings" / "casting-2-prompt.md").write_bytes(b"\xff\xfe not utf-8")

    refusal = check_reported_prompt_hash(fdir, 2, "sha256:whatever")

    assert refusal is not None
    assert refusal["ok"] is False
    assert "casting-2-prompt.md" in refusal["error"]


def test_the_acceptance_gate_uses_the_shared_hash_check(run_env):
    """CT-011 — one implementation for both gates. The acceptance gate's
    refusal is the helper's, token included, so a lead cannot learn two
    different stories about one prompt depending on which door they walked."""
    root, fdir = run_env

    result = _accept(root, prompt_hash="sha256:deadbeefdeadbeef")

    assert result["ok"] is False
    assert result["error"] == "stale_prompt_hash"
    assert result["expected_hash"] == _hash_str(PROMPT_TEXT)
    assert result["reported_hash"] == "sha256:deadbeefdeadbeef"


# --- CT-015 / AC-015 / FR-010 / OT-027: casting_commit is required -----------
def test_acceptance_without_a_casting_commit_is_refused_naming_it(run_env):
    """OT-027 verbatim: 'Foundry-Accept-Casting without casting_commit is
    refused naming the parameter.'

    FR-010's rule is 'No acceptance without EVID-01/EVID-02 running', and an
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
    """CT-015 — 'positioned so the refusal is reached before any worktree or
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
    """CT-015 verbatim: 'acceptance with evidence_provenance always populated'.

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

    from foundry_mcp.tools import foundry_handoff as handoff_module

    params = inspect.signature(handoff_module.foundry_accept_casting).parameters
    assert params["casting_commit"].default is None
