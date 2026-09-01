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
from datetime import datetime, timezone
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
