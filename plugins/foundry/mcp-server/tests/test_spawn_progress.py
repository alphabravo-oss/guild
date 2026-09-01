"""The spawn output's WRITE half — progress protocol and bulk-GRIND context.

Two behavioural areas, both properties of what ``foundry_spawn.py`` hands back
to the lead:

**FR-015 — the progress-protocol block.** ``foundry_liveness`` can only report
what agents write, and agents only write what the spawn prompt told them to.
``test_liveness.py`` proves the reader works against ledgers the test authored
itself; that proves nothing about whether a real agent would ever produce one.
So the assertions here are on the INSTRUCTION, and the closing test writes a
line at the path the block names and reads it back through the real tool —
the loop closed end to end rather than at both ends separately. If the emitted
path and the parsed path ever drift apart, that test is what goes red.

**FR-020 / AC-025 — the bulk-GRIND ``grind_cycle_context`` hole.** The single
spawn path has built this block since it was added; ``foundry_cast_wave``
never did, while its own instructions told the lead to append a block it never
produced. Since bulk dispatch is how GRIND waves are actually spawned, the
practical effect was that nearly every GRIND teammate went in blind to what
prior cycles had changed. These tests use a REAL throwaway git repository,
because the context is built from ``git diff --name-only baseline..HEAD`` and
a mocked diff would only prove the mock agrees with the test author.

Run with the rest of the suite: ``uv run --with pytest pytest`` from
``plugins/foundry/mcp-server``.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from foundry_mcp.tools import foundry_spawn as fs
from foundry_mcp.tools import foundry_state


RUN_NAME = "c4-spawn-progress-run"

# The file the fixture repo changes between the CAST baseline and HEAD, and
# which the casting declares as a key_file — so a correct implementation files
# it under "your casting's key_files that changed" rather than "other files".
KEY_FILE = "src/owned_by_the_casting.py"


# --------------------------------------------------------------------------- #
# Fixtures & helpers
# --------------------------------------------------------------------------- #


def _git_env(tmp_path: Path) -> dict[str, str]:
    """Isolate git from the developer's machine.

    Mirrors ``test_commit_guard._git_env``: without this, a global gitconfig
    or hooks path on the machine running the suite leaks into the throwaway
    repo and the result depends on who ran the tests.
    """
    fake_home = tmp_path / "fake-home"
    fake_home.mkdir(exist_ok=True)
    env = dict(os.environ)
    env.update(
        {
            "HOME": str(fake_home),
            "GIT_CONFIG_GLOBAL": str(fake_home / "gitconfig"),
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_AUTHOR_NAME": "Test",
            "GIT_AUTHOR_EMAIL": "test@example.com",
            "GIT_COMMITTER_NAME": "Test",
            "GIT_COMMITTER_EMAIL": "test@example.com",
        }
    )
    (fake_home / "gitconfig").write_text("", encoding="utf-8")
    return env


def _git(args: list[str], *, cwd: Path, env: dict[str, str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git"] + args,
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
        timeout=60,
    )


def _write_manifest(fdir: Path, casting_ids: list[int]) -> None:
    (fdir / "castings").mkdir(parents=True, exist_ok=True)
    (fdir / "castings" / "manifest.json").write_text(
        json.dumps(
            {
                "castings": [
                    {"id": cid, "key_files": [KEY_FILE]} for cid in casting_ids
                ],
                "waves": [{"wave": 1, "casting_ids": casting_ids}],
            }
        ),
        encoding="utf-8",
    )
    for cid in casting_ids:
        (fdir / "castings" / f"casting-{cid}-prompt.md").write_text(
            f"# Casting {cid}\n\nBuild the thing.\n", encoding="utf-8"
        )


@pytest.fixture
def run_env(tmp_path, monkeypatch):
    """A foundry run under tmp_path with a wave-1 manifest and two castings.

    FOUNDRY_MODEL is cleared so the model clause (asserted by
    ``test_model_config.py``) cannot vary with the ambient environment this
    server really runs inside.
    """
    monkeypatch.delenv("FOUNDRY_MODEL", raising=False)
    project_root = tmp_path
    fdir = project_root / "foundry-archive" / RUN_NAME
    fdir.mkdir(parents=True, exist_ok=True)
    _write_manifest(fdir, [1, 2])

    foundry_state.set_active_run(RUN_NAME)
    try:
        yield str(project_root), fdir
    finally:
        foundry_state.clear_active_run()


@pytest.fixture
def grind_repo(tmp_path, monkeypatch):
    """A real git repo whose HEAD moved past the stamped CAST baseline.

    Yields (project_root, fdir). ``.cast-baseline-sha`` holds the first
    commit; HEAD holds a second commit that modified the casting's key_file,
    which is exactly the state a GRIND cycle 2+ dispatch happens in.
    """
    monkeypatch.delenv("FOUNDRY_MODEL", raising=False)
    env = _git_env(tmp_path)
    project_root = tmp_path / "repo"
    project_root.mkdir()

    _git(["init", "-q", "-b", "main", "."], cwd=project_root, env=env)
    target = project_root / KEY_FILE
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("original = 1\n", encoding="utf-8")
    _git(["add", KEY_FILE], cwd=project_root, env=env)
    _git(["commit", "-q", "-m", "cast"], cwd=project_root, env=env)
    baseline = _git(["rev-parse", "HEAD"], cwd=project_root, env=env).stdout.strip()

    target.write_text("original = 2  # changed by an earlier GRIND cycle\n", encoding="utf-8")
    _git(["add", KEY_FILE], cwd=project_root, env=env)
    _git(["commit", "-q", "-m", "grind cycle 1"], cwd=project_root, env=env)

    fdir = project_root / "foundry-archive" / RUN_NAME
    fdir.mkdir(parents=True, exist_ok=True)
    _write_manifest(fdir, [1, 2])
    (fdir / ".cast-baseline-sha").write_text(baseline + "\n", encoding="utf-8")

    foundry_state.set_active_run(RUN_NAME)
    try:
        yield str(project_root), fdir
    finally:
        foundry_state.clear_active_run()


# --------------------------------------------------------------------------- #
# FR-015 — the progress-protocol block reaches the lead
# --------------------------------------------------------------------------- #


def test_single_spawn_returns_a_progress_protocol_block(run_env) -> None:
    project_root, _fdir = run_env
    result = fs.foundry_spawn_teammate(1, "cast", project_root)

    assert result["ok"] is True
    assert result["progress_protocol"].strip()


def test_the_block_names_the_exact_ledger_path_for_that_casting(run_env) -> None:
    """FR-015: an instruction that does not name the path is not an instruction."""
    project_root, _fdir = run_env
    block = fs.foundry_spawn_teammate(1, "cast", project_root)["progress_protocol"]

    assert f"foundry-archive/{RUN_NAME}/progress/casting-1.jsonl" in block


def test_the_block_names_the_three_required_line_fields(run_env) -> None:
    """phase / step / timestamp — FR-015 names all three, so the prose must."""
    project_root, _fdir = run_env
    block = fs.foundry_spawn_teammate(1, "cast", project_root)["progress_protocol"]

    for field in ("timestamp", "phase", "step"):
        assert f"`{field}`" in block, field
    assert "ISO-8601" in block
    assert "append mode" in block


def test_the_block_states_the_cadence_and_threshold_it_will_be_judged_by(run_env) -> None:
    """The prose an agent obeys is interpolated from the constants, not retyped.

    A hardcoded "5 minutes" in the block would keep passing after someone
    retuned ``PROGRESS_CADENCE_SECONDS``, and the agent would then be writing
    to a cadence the tool no longer expects.
    """
    project_root, _fdir = run_env
    block = fs.foundry_spawn_teammate(1, "cast", project_root)["progress_protocol"]

    assert f"{fs.PROGRESS_CADENCE_SECONDS // 60} minutes" in block
    assert f"{fs.STALL_THRESHOLD_SECONDS // 60} minutes" in block


def test_the_block_explains_why_step_must_change(run_env) -> None:
    """The ledger's whole advantage over a heartbeat rides on this sentence.

    An agent told only "write a line periodically" will happily restate one
    step forever, which reports as ``no_progress`` — so the block has to say
    so, and name the status, or the instruction quietly degrades into a ping.
    """
    project_root, _fdir = run_env
    block = fs.foundry_spawn_teammate(1, "cast", project_root)["progress_protocol"]

    assert fs.STATUS_NO_PROGRESS in block
    assert fs.STATUS_STALLED in block
    assert "Foundry-Liveness" in block


def test_the_instructions_tell_the_lead_to_append_the_block(run_env) -> None:
    """The lead is the courier; an unappended block reaches nobody."""
    project_root, _fdir = run_env
    instructions = fs.foundry_spawn_teammate(1, "cast", project_root)["instructions"]

    assert "progress_protocol" in instructions
    assert "BELOW the prompt" in instructions


def test_the_block_is_appended_last_so_the_established_order_is_undisturbed(
    run_env,
) -> None:
    """The orchestrator's own guidance pins "(a) context (b) defects".

    Slotting a third block between them would contradict prose this casting
    does not own, so the instruction has to place it after both.
    """
    project_root, _fdir = run_env
    instructions = fs.foundry_spawn_teammate(1, "cast", project_root)["instructions"]

    assert "LAST" in instructions
    assert "after any grind_cycle_context and defect blocks" in instructions


def test_the_prompt_itself_is_never_modified_by_the_progress_block(run_env) -> None:
    """The verbatim-prompt contract is the reason this is a separate field."""
    project_root, fdir = run_env
    on_disk = (fdir / "castings" / "casting-1-prompt.md").read_text(encoding="utf-8")

    result = fs.foundry_spawn_teammate(1, "cast", project_root)

    assert result["prompt"] == on_disk
    assert "progress" not in result["prompt"]


def test_bulk_spawn_gives_every_casting_its_own_block(run_env) -> None:
    """One ledger per casting, so one block per casting — not one per wave."""
    project_root, _fdir = run_env
    result = fs.foundry_cast_wave(1, "cast", project_root)

    assert result["ok"] is True
    blocks = {c["casting_id"]: c["progress_protocol"] for c in result["castings"]}
    assert set(blocks) == {1, 2}
    assert "progress/casting-1.jsonl" in blocks[1]
    assert "progress/casting-2.jsonl" in blocks[2]
    assert "casting-2.jsonl" not in blocks[1]


def test_bulk_instructions_tell_the_lead_to_append_each_block(run_env) -> None:
    project_root, _fdir = run_env
    instructions = fs.foundry_cast_wave(1, "cast", project_root)["instructions"]

    assert "progress_protocol" in instructions
    assert "BELOW its prompt" in instructions


# --------------------------------------------------------------------------- #
# FR-015 — the loop, closed
# --------------------------------------------------------------------------- #


def test_a_line_written_where_the_block_says_is_read_back_by_liveness(run_env) -> None:
    """The end-to-end round trip: emitted path and line shape, parsed back.

    This test is the reason the two halves cannot drift. It takes the ledger
    path out of the block the spawn emitted — never out of a literal — writes
    one line in the documented shape, and asks the real tool about it.
    """
    project_root, _fdir = run_env
    block = fs.foundry_spawn_teammate(1, "cast", project_root)["progress_protocol"]

    # Recover the path the agent was told to use, FROM THE BLOCK ITSELF —
    # never from a literal, or the two halves could drift and this would
    # still pass.
    match = re.search(r"`([^`]+\.jsonl)`", block)
    assert match, f"the block names no .jsonl ledger path:\n{block}"
    ledger = Path(project_root) / match.group(1)
    ledger.parent.mkdir(parents=True, exist_ok=True)
    ledger.write_text(
        json.dumps(
            {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "phase": "cast",
                "step": "read floor complete",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    result = fs.foundry_liveness(project_root=project_root)

    assert result["ok"] is True
    assert len(result["agents"]) == 1
    record = result["agents"][0]
    assert record["agent"] == "casting-1"
    assert record["status"] == fs.STATUS_PROGRESSING
    assert record["step"] == "read floor complete"
    assert record["last_progress_age_seconds"] <= 60
    assert result["needs_attention"] == []


def test_the_agent_id_in_the_block_matches_the_one_liveness_reports(run_env) -> None:
    """A ledger the lead cannot address by name is a ledger it cannot query."""
    project_root, fdir = run_env
    fs.foundry_cast_wave(1, "cast", project_root)

    pdir = fdir / "progress"
    pdir.mkdir(parents=True)
    for cid in (1, 2):
        (pdir / f"casting-{cid}.jsonl").write_text(
            json.dumps(
                {
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "phase": "cast",
                    "step": f"casting {cid} working",
                }
            )
            + "\n",
            encoding="utf-8",
        )

    single = fs.foundry_liveness(agent="casting-2", project_root=project_root)

    assert single["ok"] is True
    assert single["agents"][0]["step"] == "casting 2 working"


# --------------------------------------------------------------------------- #
# D-022 — both spawn paths tell the agent how to declare itself finished
# --------------------------------------------------------------------------- #


def test_single_spawn_block_carries_the_terminal_line_instruction(run_env) -> None:
    """The read-side DONE status is unreachable unless the prompt asks for it."""
    project_root, _fdir = run_env
    block = fs.foundry_spawn_teammate(1, "cast", project_root)["progress_protocol"]
    assert '"done": true' in block
    assert fs.STATUS_DONE in block


def test_bulk_spawn_blocks_carry_the_terminal_line_instruction(run_env) -> None:
    project_root, _fdir = run_env
    result = fs.foundry_cast_wave(1, "cast", project_root)
    for casting in result["castings"]:
        assert '"done": true' in casting["progress_protocol"]


def test_a_terminal_line_written_as_instructed_reports_done(run_env) -> None:
    """The D-022 round trip, both halves, through the real spawn output.

    The ledger path and the terminal field both come out of the emitted block
    rather than out of literals, so a drift in either half fails here.
    """
    project_root, _fdir = run_env
    block = fs.foundry_spawn_teammate(1, "cast", project_root)["progress_protocol"]

    ledger = Path(project_root) / re.search(r"`([^`]+\.jsonl)`", block).group(1)
    ledger.parent.mkdir(parents=True, exist_ok=True)
    long_ago = datetime.now(timezone.utc) - timedelta(
        seconds=fs.STALL_THRESHOLD_SECONDS * 2
    )
    ledger.write_text(
        json.dumps(
            {"timestamp": long_ago.isoformat(), "phase": "cast", "step": "self-check"}
        )
        + "\n"
        + json.dumps(
            {
                "timestamp": long_ago.isoformat(),
                "phase": "cast",
                "step": "committed",
                fs.TERMINAL_FIELD: True,
            }
        )
        + "\n",
        encoding="utf-8",
    )

    result = fs.foundry_liveness(project_root=project_root)

    assert result["agents"][0]["status"] == fs.STATUS_DONE
    assert result["needs_attention"] == []


# --------------------------------------------------------------------------- #
# D-021 — the block serves stream agents, not only teammates
# --------------------------------------------------------------------------- #


def test_the_teammate_variant_is_what_the_spawn_tools_still_emit(run_env) -> None:
    """Generalizing the block must not change what a teammate receives."""
    project_root, _fdir = run_env
    block = fs.foundry_spawn_teammate(1, "cast", project_root)["progress_protocol"]
    assert "`cast` or `grind`" in block
    assert "Read Floor" in block


def test_the_stream_variant_names_the_phase_a_stream_agent_is_actually_in() -> None:
    """A stream agent handed the teammate wording writes a truthful-looking
    ledger describing work it is not doing — `phase: cast` from an agent that
    is running INSPECT is worse than no line at all."""
    block = fs._progress_protocol_block(
        "some-run", "trace", fs.STREAM_PHASE_HINT, fs.STREAM_STEP_EXAMPLES
    )
    assert "`inspect`" in block
    assert "`cast` or `grind`" not in block
    assert "Read Floor" not in block
    assert "Foundry-Sync" in block


def test_the_two_variants_share_every_machine_read_field() -> None:
    """Only the examples may differ. The path, the field names, the cadence and
    the terminal field are the half liveness parses, and a variant that drifted
    on any of them would produce a ledger the tool cannot read."""
    teammate = fs._progress_protocol_block("some-run", "casting-1")
    stream = fs._progress_protocol_block(
        "some-run", "trace", fs.STREAM_PHASE_HINT, fs.STREAM_STEP_EXAMPLES
    )
    for shared in (
        "`timestamp`",
        "`phase`",
        "`step`",
        '"done": true',
        f"foundry-archive/some-run/{fs.PROGRESS_DIR_NAME}/",
        str(fs.STALL_THRESHOLD_SECONDS // 60),
        str(fs.PROGRESS_CADENCE_SECONDS // 60),
    ):
        assert shared in teammate, shared
        assert shared in stream, shared


def test_a_stream_agent_obeying_its_block_becomes_visible_to_liveness(
    run_env,
) -> None:
    """D-021 end to end: the gap is reported, the block that comes with the
    report is obeyed, and the stream stops being invisible.

    The ledger path is taken out of the block the tool itself handed over, so
    this fails if the reported remedy names a file the reader does not read.
    """
    project_root, fdir = run_env
    entered = datetime.now(timezone.utc) - timedelta(
        seconds=fs.STALL_THRESHOLD_SECONDS * 2
    )
    (fdir / "state.json").write_text(
        json.dumps(
            {
                "phase": "F2",
                "phase_times": {"F2": {"started_at": entered.isoformat()}},
            }
        ),
        encoding="utf-8",
    )

    before = fs.foundry_liveness(project_root=project_root)
    trace = next(r for r in before["agents"] if r["agent"] == "trace")
    assert trace["status"] == fs.STATUS_NO_LEDGER
    assert "trace" in before["needs_attention"]

    ledger = Path(project_root) / re.search(
        r"`([^`]+\.jsonl)`", trace["progress_protocol"]
    ).group(1)
    ledger.parent.mkdir(parents=True, exist_ok=True)
    ledger.write_text(
        json.dumps(
            {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "phase": "inspect",
                "step": "sweeping casting 3",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    after = fs.foundry_liveness(project_root=project_root)
    trace_after = next(r for r in after["agents"] if r["agent"] == "trace")
    assert trace_after["status"] == fs.STATUS_PROGRESSING
    assert trace_after["step"] == "sweeping casting 3"
    assert "trace" not in after["needs_attention"]


# --------------------------------------------------------------------------- #
# FR-020 / AC-025 — bulk GRIND spawns carry grind_cycle_context
# --------------------------------------------------------------------------- #


requires_git = pytest.mark.skipif(
    shutil.which("git") is None,
    reason="git is not available on PATH; the real-git harness cannot run",
)


@requires_git
def test_bulk_grind_returns_grind_cycle_context_per_casting(grind_repo) -> None:
    """AC-025: the hole this casting closes.

    Before the fix ``foundry_cast_wave`` produced no context at all while its
    instructions told the lead to append one.
    """
    project_root, _fdir = grind_repo

    result = fs.foundry_cast_wave(1, "grind", project_root)

    assert result["ok"] is True
    for casting in result["castings"]:
        assert casting["grind_cycle_context"].strip(), casting["casting_id"]
        assert KEY_FILE in casting["grind_cycle_context"]


@requires_git
def test_bulk_cast_returns_no_grind_cycle_context(grind_repo) -> None:
    """The context is GRIND-only; a CAST wave has no prior cycle to report."""
    project_root, _fdir = grind_repo

    result = fs.foundry_cast_wave(1, "cast", project_root)

    for casting in result["castings"]:
        assert "grind_cycle_context" not in casting


@requires_git
def test_bulk_grind_matches_the_single_spawn_path_it_was_missing(grind_repo) -> None:
    """Both dispatch paths must hand the teammate the same picture.

    The single-spawn path was the correct one all along; this pins the bulk
    path to it rather than to a re-derived copy, so a future change to
    ``_build_grind_cycle_context`` cannot fix one path and miss the other.
    """
    project_root, _fdir = grind_repo

    bulk = fs.foundry_cast_wave(1, "grind", project_root)
    single = fs.foundry_spawn_teammate(1, "grind", project_root)

    bulk_context = next(
        c["grind_cycle_context"] for c in bulk["castings"] if c["casting_id"] == 1
    )
    assert bulk_context == single["grind_cycle_context"]


@requires_git
def test_bulk_grind_context_is_scoped_to_each_castings_key_files(grind_repo) -> None:
    """Per casting, not once per wave — the filter is per-casting key_files.

    One shared block would label every teammate's changed files as somebody
    else's, which is worse than no block: it reads as authoritative.
    """
    project_root, fdir = grind_repo
    # Casting 2 owns nothing that changed; casting 1 owns the changed file.
    manifest = json.loads((fdir / "castings" / "manifest.json").read_text(encoding="utf-8"))
    for casting in manifest["castings"]:
        if casting["id"] == 2:
            casting["key_files"] = ["src/untouched_by_this_run.py"]
    (fdir / "castings" / "manifest.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )

    result = fs.foundry_cast_wave(1, "grind", project_root)
    contexts = {c["casting_id"]: c["grind_cycle_context"] for c in result["castings"]}

    assert "### Your casting's key_files that changed:" in contexts[1]
    assert "### Your casting's key_files that changed:" not in contexts[2]
    assert "Other files changed" in contexts[2]


@requires_git
def test_bulk_grind_still_carries_the_progress_block(grind_repo) -> None:
    """The two additions to this path are independent; neither may shadow the other."""
    project_root, _fdir = grind_repo

    result = fs.foundry_cast_wave(1, "grind", project_root)

    for casting in result["castings"]:
        assert casting["progress_protocol"].strip()
        assert casting["grind_cycle_context"].strip()


def test_bulk_grind_without_a_baseline_omits_the_context_quietly(run_env) -> None:
    """Cycle-1 pre-edit: no baseline stamped yet, so there is nothing to report.

    An empty block must be omitted rather than emitted empty — the lead's
    append step keys on the field's presence.
    """
    project_root, _fdir = run_env

    result = fs.foundry_cast_wave(1, "grind", project_root)

    assert result["ok"] is True
    for casting in result["castings"]:
        assert "grind_cycle_context" not in casting
        assert casting["progress_protocol"].strip()


# --------------------------------------------------------------------------- #
# D-058 adjacent path — the real spawn WRITERS feed the liveness READER
# --------------------------------------------------------------------------- #
#
# `test_liveness.py` proves the roster union against spawns.log records the
# test authored itself, which proves nothing about whether either spawn tool
# emits a record the reader can key. These two tests drive the real writers —
# a different caller than the defect path — into the real reader, so the field
# names, the casting_id type and the phase spelling are all checked against
# what production actually writes rather than against a fixture's idea of it.


def _age_every_spawn_record(fdir: Path, seconds: float) -> None:
    """Backdate spawns.log so its records are past the stall threshold.

    ONLY the timestamp moves; every other field stays byte-identical to what
    the spawn tool wrote. The module reads real wall-clock time by design (no
    test-only clock seam — see test_liveness.py's header), and a record written
    a millisecond ago is correctly invisible, so ageing it is the only way to
    reach the overdue branch with a genuine record.
    """
    path = fdir / "spawns.log"
    moment = (datetime.now(timezone.utc) - timedelta(seconds=seconds)).isoformat()
    aged = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        record["timestamp"] = moment
        aged.append(json.dumps(record))
    path.write_text("\n".join(aged) + "\n", encoding="utf-8")


def _enter_grind(fdir: Path, minutes_ago: float = 200) -> None:
    entered = datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)
    (fdir / "state.json").write_text(
        json.dumps(
            {"phase": "F3", "phase_times": {"F3": {"started_at": entered.isoformat()}}}
        ),
        encoding="utf-8",
    )


def test_a_real_bulk_dispatch_becomes_a_liveness_row(run_env) -> None:
    """foundry_cast_wave writes spawns.log; foundry_liveness must read it back.

    The bulk record carries `wave` and `bulk` fields the single-spawn one does
    not, so it is a genuinely different shape reaching the same parser.
    """
    project_root, fdir = run_env

    wave = fs.foundry_cast_wave(wave=1, phase="grind", project_root=project_root)
    assert wave["ok"] is True

    _enter_grind(fdir)
    _age_every_spawn_record(fdir, seconds=3600)

    result = fs.foundry_liveness(project_root=project_root)

    assert [r["agent"] for r in result["agents"]] == ["casting-1", "casting-2"]
    assert result["needs_attention"] == ["casting-1", "casting-2"]
    assert all(r["status"] == fs.STATUS_NO_LEDGER for r in result["agents"])


def test_a_real_single_dispatch_becomes_a_liveness_row(run_env) -> None:
    """The other writer, whose record carries neither `wave` nor `bulk`."""
    project_root, fdir = run_env

    spawn = fs.foundry_spawn_teammate(casting_id=2, phase="grind", project_root=project_root)
    assert spawn["ok"] is True

    _enter_grind(fdir)
    _age_every_spawn_record(fdir, seconds=3600)

    result = fs.foundry_liveness(project_root=project_root)

    assert [r["agent"] for r in result["agents"]] == ["casting-2"]
    assert result["agents"][0]["status"] == fs.STATUS_NO_LEDGER


def test_the_row_points_at_the_path_the_spawn_block_told_the_agent_to_write(
    run_env,
) -> None:
    """The closed loop, in its failure state.

    The spawn block names where the teammate must write; the no_ledger row
    names where the lead should look. If those two paths ever drift apart, the
    lead reads an empty directory and concludes the agent is fine.
    """
    project_root, fdir = run_env

    spawn = fs.foundry_spawn_teammate(casting_id=2, phase="grind", project_root=project_root)
    _enter_grind(fdir)
    _age_every_spawn_record(fdir, seconds=3600)

    row = fs.foundry_liveness(agent="casting-2", project_root=project_root)["agents"][0]

    assert row["ledger"] in spawn["progress_protocol"]


def test_an_agent_that_obeys_its_block_leaves_the_missing_roster(run_env) -> None:
    """The fix must be self-clearing, not a row that can only ever be added.

    Same dispatch, same phase — the only thing that changes is that the
    teammate writes the line its block asked for.
    """
    project_root, fdir = run_env

    fs.foundry_spawn_teammate(casting_id=2, phase="grind", project_root=project_root)
    _enter_grind(fdir)
    _age_every_spawn_record(fdir, seconds=3600)

    before = fs.foundry_liveness(project_root=project_root)
    assert before["needs_attention"] == ["casting-2"]

    pdir = fdir / "progress"
    pdir.mkdir(parents=True, exist_ok=True)
    (pdir / "casting-2.jsonl").write_text(
        json.dumps(
            {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "phase": "grind",
                "step": "read floor complete",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    after = fs.foundry_liveness(project_root=project_root)
    assert after["needs_attention"] == []
    assert [r["agent"] for r in after["agents"]] == ["casting-2"]
    assert after["agents"][0]["status"] == fs.STATUS_PROGRESSING


# --------------------------------------------------------------------------- #
# D-115 — a manifest that is valid JSON of the WRONG TYPE
# --------------------------------------------------------------------------- #
#
# `json.JSONDecodeError` catches only text that is not JSON at all. `[1,2,3]`,
# `null` and a bare string all parse cleanly and then meet `.get()`, which is
# an AttributeError raised across the MCP boundary from a module whose whole
# error contract is a named `{"ok": False, ...}` refusal. Both spawn doors read
# the manifest, so both were affected, and both are pinned here.

WRONG_TYPED_MANIFESTS = ["[1, 2, 3]", "null", '"a bare string"', "42"]


def _overwrite_manifest(fdir: Path, body: str) -> None:
    (fdir / "castings" / "manifest.json").write_text(body, encoding="utf-8")


@pytest.mark.parametrize("body", WRONG_TYPED_MANIFESTS)
def test_the_single_door_refuses_a_wrong_typed_manifest(run_env, body) -> None:
    """A named refusal that says what was found and where, never a traceback."""
    project_root, fdir = run_env
    _overwrite_manifest(fdir, body)

    result = fs.foundry_spawn_teammate(1, "cast", project_root)

    assert result["ok"] is False
    assert "manifest.json" in result["error"]
    assert "DECOMPOSE" in result["hint"]


@pytest.mark.parametrize("body", WRONG_TYPED_MANIFESTS)
def test_the_bulk_door_refuses_a_wrong_typed_manifest(run_env, body) -> None:
    """The same input at the other door — this one crashed on `null` first."""
    project_root, fdir = run_env
    _overwrite_manifest(fdir, body)

    result = fs.foundry_cast_wave(1, "cast", project_root)

    assert result["ok"] is False
    assert "manifest.json" in result["error"]
    assert "DECOMPOSE" in result["hint"]


def test_the_refusal_names_the_type_it_actually_found(run_env) -> None:
    """"Malformed" is not actionable; "parsed as list" tells the lead what to open.

    The offending file is named too, because a run has more than one JSON
    artifact and the lead should not have to guess which one to look at.
    """
    project_root, fdir = run_env
    _overwrite_manifest(fdir, "[1, 2, 3]")

    result = fs.foundry_spawn_teammate(1, "cast", project_root)

    assert "list" in result["error"]
    assert str(fdir / "castings" / "manifest.json") in result["error"]


def test_both_doors_refuse_a_wrong_typed_manifest_with_one_policy(run_env) -> None:
    """A manifest is malformed for the bulk path and the single path alike.

    Two hand-written guards would drift — one gaining a hint, one gaining a
    type name — and the lead would learn two different stories about the same
    file. One helper produces both refusals, and this is what says so.
    """
    project_root, fdir = run_env
    _overwrite_manifest(fdir, "null")

    single = fs.foundry_spawn_teammate(1, "cast", project_root)
    bulk = fs.foundry_cast_wave(1, "cast", project_root)

    assert single["error"] == bulk["error"]
    assert single["hint"] == bulk["hint"]


def test_a_torn_manifest_keeps_the_parse_error_it_already_had(run_env) -> None:
    """The path that was already correct must not be re-routed by the fix.

    Text that is not JSON at all still reports a parse error naming the
    syntax problem, which is strictly more useful than "not an object".
    """
    project_root, fdir = run_env
    _overwrite_manifest(fdir, "{truncated")

    single = fs.foundry_spawn_teammate(1, "cast", project_root)
    bulk = fs.foundry_cast_wave(1, "cast", project_root)

    assert single["ok"] is False and bulk["ok"] is False
    assert "parse error" in single["error"]
    assert "parse error" in bulk["error"]


def test_a_well_formed_manifest_is_untouched_by_the_guard(run_env) -> None:
    """The guard must cost the normal path nothing."""
    project_root, _fdir = run_env

    single = fs.foundry_spawn_teammate(1, "cast", project_root)
    bulk = fs.foundry_cast_wave(1, "cast", project_root)

    assert single["ok"] is True
    assert bulk["ok"] is True
    assert [c["casting_id"] for c in bulk["castings"]] == [1, 2]


def test_the_grind_context_builder_survives_a_wrong_typed_manifest(grind_repo) -> None:
    """The third manifest reader in the module, driven directly.

    Both doors now refuse before they reach this builder, so the only way to
    put a wrong-typed manifest in front of it is to call it as its callers do.
    Its contract is that it never fails a spawn — it returns a string — so the
    guard here degrades rather than refusing, producing exactly the unscoped
    context an unparseable manifest already produces.
    """
    project_root, fdir = grind_repo

    _overwrite_manifest(fdir, "{truncated")
    torn = fs._build_grind_cycle_context(fdir, 1, project_root)

    _overwrite_manifest(fdir, "[1, 2, 3]")
    wrong_typed = fs._build_grind_cycle_context(fdir, 1, project_root)

    assert isinstance(wrong_typed, str)
    assert wrong_typed == torn


def test_a_wrong_typed_manifest_cannot_reach_the_builder_through_a_door(
    grind_repo,
) -> None:
    """The door's refusal comes first, so a GRIND spawn never half-succeeds.

    Without the guard this call raised from inside the builder AFTER the tool
    had already read the prompt and written its spawns.log record — a spawn
    that is logged as dispatched and returns an exception.
    """
    project_root, fdir = grind_repo
    _overwrite_manifest(fdir, "[1, 2, 3]")

    result = fs.foundry_cast_wave(1, "grind", project_root)

    assert result["ok"] is False
    assert "manifest.json" in result["error"]
    assert not (fdir / "spawns.log").exists()
