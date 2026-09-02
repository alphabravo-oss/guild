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

import ast
import builtins
import codecs
import fcntl
import functools
import importlib
import inspect
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import foundry_mcp
from foundry_mcp.tools import foundry_spawn as fs
from foundry_mcp.tools import foundry_state


RUN_NAME = "c4-spawn-progress-run"

# tests/test_spawn_progress.py -> parents:
#   [0]=tests, [1]=mcp-server, [2]=foundry, [3]=plugins, [4]=repo-root.
# Mirrors test_measure_run.py / test_intent_coverage.py precedent, and is what
# lets an offender be cited as a full repo-relative `path#Symbol`.
REPO_ROOT = Path(__file__).resolve().parents[4]

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
# D-120 — the header and the file list leave the builder together or not at all
# --------------------------------------------------------------------------- #
#
# FR-020's blindness survived one branch in. A casting that declares no
# `key_files` was handed the header — "earlier cycles modified the files listed
# below" — over an empty list, because the whole diff went to `relevant` whose
# guard then demanded key_files exist. The `### Files changed since CAST:`
# label written for exactly that case was unreachable. These tests pin the
# repaired branch AND the one that already worked as one property, so a future
# edit cannot fix either in a way that separates the prose from the filenames.

CONTEXT_HEADER = "## Prior-cycle file changes (READ BEFORE ACTING ON DEFECTS)"


def _strip_key_files(fdir: Path) -> None:
    """Drop every casting's key_files, leaving the manifest otherwise intact.

    A casting with no declared key_files is legal — nothing in the schema
    requires them — and it is the state the builder's docstring has always
    promised to handle by falling back to the unfiltered diff.
    """
    path = fdir / "castings" / "manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    for casting in manifest["castings"]:
        casting.pop("key_files", None)
    path.write_text(json.dumps(manifest), encoding="utf-8")


def _listed_files(block: str) -> list[str]:
    """The filenames the block actually RENDERS, in order.

    Read off the emitted text rather than from the inputs, because the whole
    defect was a block whose inputs were computed and then never emitted.
    """
    return re.findall(r"^- `([^`]+)`$", block, flags=re.MULTILINE)


@requires_git
@pytest.mark.parametrize("declares_key_files", [True, False])
def test_the_header_is_never_emitted_without_the_files_it_promises(
    grind_repo, declares_key_files
) -> None:
    """One property, both branches — that is the point of parametrising it.

    The header's own words are "the files listed below". Whichever branch
    renders it must render them, and this is the assertion that says the two
    cannot come apart again.
    """
    project_root, fdir = grind_repo
    if not declares_key_files:
        _strip_key_files(fdir)

    block = fs._build_grind_cycle_context(fdir, 1, project_root)

    assert (CONTEXT_HEADER in block) == bool(_listed_files(block))
    # Not vacuously — this fixture's HEAD really did move past the baseline.
    assert _listed_files(block) == [KEY_FILE]


@requires_git
def test_a_casting_with_no_key_files_gets_the_fallback_diff(grind_repo) -> None:
    """The docstring's fallback promise, kept.

    Before the fix this block was the header prose and nothing else, and the
    label below was dead code: it needed `casting_keyfiles` to be falsy, which
    was exactly when the list it labelled was empty.
    """
    project_root, fdir = grind_repo
    _strip_key_files(fdir)

    block = fs._build_grind_cycle_context(fdir, 1, project_root)

    assert "### Files changed since CAST:" in block
    assert "### Your casting's key_files that changed:" not in block
    assert _listed_files(block) == [KEY_FILE]


@requires_git
def test_the_key_files_branch_is_unmoved_by_the_fallback_repair(grind_repo) -> None:
    """The branch that already worked must not shift while the other is fixed."""
    project_root, fdir = grind_repo

    block = fs._build_grind_cycle_context(fdir, 1, project_root)

    assert "### Your casting's key_files that changed:" in block
    assert "### Files changed since CAST:" not in block
    assert _listed_files(block) == [KEY_FILE]


@requires_git
def test_the_fallback_block_reaches_both_spawn_doors_alike(grind_repo) -> None:
    """The builder's other caller, on the branch D-120 repaired.

    ``test_bulk_grind_matches_the_single_spawn_path_it_was_missing`` pins the
    with-key_files case across both doors; this pins the repaired one, so a
    fix to the builder cannot land at one door and leave the other reading a
    re-derived copy.
    """
    project_root, fdir = grind_repo
    _strip_key_files(fdir)

    bulk = fs.foundry_cast_wave(1, "grind", project_root)
    single = fs.foundry_spawn_teammate(1, "grind", project_root)

    bulk_context = next(
        c["grind_cycle_context"] for c in bulk["castings"] if c["casting_id"] == 1
    )
    assert bulk_context == single["grind_cycle_context"]
    assert "### Files changed since CAST:" in bulk_context
    assert _listed_files(bulk_context) == [KEY_FILE]


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

    Text that is not JSON at all still reports the SYNTAX problem, naming the
    position the decoder gave up at, which is strictly more useful than "not an
    object" — a message that would send the operator looking for a type fault
    in a file whose real fault is a missing brace.

    Asserted on the decoder's own words rather than on the sentence wrapped
    around them. That wrapper has legitimately changed twice as the read moved
    behind one primitive (``manifest.json parse error: {e}`` became
    ``manifest.json is not valid JSON ({e})``), and a test that pins the
    wrapper goes red on a refactor while a test that pins the decoder's output
    goes red only when the operator actually loses information — which is the
    thing worth defending.
    """
    project_root, fdir = run_env
    _overwrite_manifest(fdir, "{truncated")

    single = fs.foundry_spawn_teammate(1, "cast", project_root)
    bulk = fs.foundry_cast_wave(1, "cast", project_root)

    assert single["ok"] is False and bulk["ok"] is False
    for door in (single, bulk):
        assert "Expecting property name" in door["error"]
        assert "column 2" in door["error"]
        # A torn document is not a wrong-TYPED one. Reporting it through the
        # shape validator would be the re-routing this test exists to catch.
        assert "is not a JSON object" not in door["error"]
    # D-132's property, which survives whichever rung produced the refusal.
    assert single["error"] == bulk["error"]
    assert single["hint"] == bulk["hint"]


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


# --------------------------------------------------------------------------- #
# D-132 — the RECORDS inside the container, one rung below D-115
# --------------------------------------------------------------------------- #
#
# D-115 guarded the top-level container and stopped. A manifest that IS a JSON
# object but whose `castings` is a string, a list of ints, or a list of nulls
# parsed cleanly, passed that guard, and then met `c.get("id")` — an
# AttributeError across the MCP boundary from a module whose whole error
# contract is a named refusal. Driven through _DISPATCH before the fix, five
# nested shapes at two doors gave 4/10 RAISE and 0/10 naming manifest.json.
#
# THE ASYMMETRY IS THE TELL, and it is what these tests pin. Cast-Wave
# TOLERATED every castings-shaped corruption that made Spawn-Teammate raise,
# and Spawn-Teammate tolerated the waves-shaped one that made Cast-Wave raise.
# A tolerated corrupt manifest is arguably worse than the raise: Cast-Wave
# reported "wave 1 not found in manifest" for a structurally corrupt document
# and sent the operator to rebuild wave groupings that were never the problem.
#
# So the fix is not five filters at five index sites — that is the escalated
# class itself, a hardening bound by hand to the site a defect was reported at.
# It is ONE declaration of the shape (`_MANIFEST_SHAPE`) walked by ONE
# recursive validator that all four manifest readers in the module consult.
# `test_every_manifest_key_the_module_indexes_is_declared` is what holds that
# property: it derives the indexed key paths from the module's own AST, so a
# reader that starts indexing an undeclared key fails the suite rather than
# opening the same hole one rung further down.

#: (label, manifest body) for each nested shape D-132 drove. The label is the
#: row name in the parity report, so evidence and tests use one vocabulary.
NESTED_SHAPE_ROWS = [
    ('castings="nope"', {"castings": "nope", "waves": [{"wave": 1, "casting_ids": [1]}]}),
    ("castings=[1,2,3]", {"castings": [1, 2, 3], "waves": [{"wave": 1, "casting_ids": [1]}]}),
    ("castings=[null]", {"castings": [None], "waves": [{"wave": 1, "casting_ids": [1]}]}),
    ('waves="nope"', {"castings": [{"id": 1}], "waves": "nope"}),
    ('castings=[{"no_id":1}]', {"castings": [{"no_id": 1}], "waves": [{"wave": 1, "casting_ids": [1]}]}),
]


@pytest.mark.parametrize(
    "body", [body for _label, body in NESTED_SHAPE_ROWS],
    ids=[label for label, _body in NESTED_SHAPE_ROWS],
)
def test_both_doors_refuse_a_nested_shape_with_identical_text(run_env, body) -> None:
    """THE PARITY PIN. One validator, so one refusal, whichever door you enter.

    Before D-132 these ten cells held four AttributeErrors, four silent
    successes, one "casting_id not found" and one "wave not found" — the two
    doors telling the lead different stories about the same corrupt file, which
    is the D-097 asymmetry that says the hardening was bound to a site rather
    than derived from the document. Byte equality of `error` AND `hint` is what
    makes "both doors share one policy" a fact rather than a comment: two
    hand-written guards can pass every other assertion here and still drift.
    """
    project_root, fdir = run_env
    _overwrite_manifest(fdir, json.dumps(body))

    single = fs.foundry_spawn_teammate(1, "cast", project_root)
    bulk = fs.foundry_cast_wave(1, "cast", project_root)

    assert single["ok"] is False and bulk["ok"] is False
    assert single["error"] == bulk["error"]
    assert single["hint"] == bulk["hint"]
    assert "manifest.json" in single["error"]
    assert "DECOMPOSE" in single["hint"]


def test_demo_shape_parity_report(run_env) -> None:
    """The 10-cell drive, RENDERED — the asymmetry gone, not merely coded.

    Run it with ``-s`` to read the table:

        uv run --with pytest pytest tests/test_spawn_progress.py -q -s -k demo

    Mirrors ``test_liveness.py``'s demo_report: the parity test above asserts
    equality, and this prints the table that lets a reader see WHAT the two
    doors now agree on. The same ten cells before the fix:

        castings="nope"         Spawn-Teammate RAISES AttributeError 'str'
                                Cast-Wave      tolerated
        castings=[1,2,3]        Spawn-Teammate RAISES AttributeError 'int'
                                Cast-Wave      tolerated
        castings=[null]         Spawn-Teammate RAISES AttributeError 'NoneType'
                                Cast-Wave      tolerated
        waves="nope"            Spawn-Teammate tolerated
                                Cast-Wave      RAISES AttributeError 'str'
        castings=[{"no_id":1}]  both tolerated
        TOTAL 4/10 RAISE, 0/10 name manifest.json

    The run directory is elided from the printed text so the render is
    byte-stable under re-execution; the tests above assert against the real
    absolute path, which is what the operator actually receives.
    """
    project_root, fdir = run_env
    manifest_path = fdir / "castings" / "manifest.json"

    def drive(call) -> tuple[str, str]:
        try:
            result = call()
        except Exception as exc:  # noqa: BLE001 — the outcome under test
            return "RAISE", f"{type(exc).__name__}: {exc}"
        text = str(result.get("error", "")).replace(
            str(manifest_path), "<run>/castings/manifest.json"
        )
        return f"ok={result['ok']}", text

    print("\nD-132 — five nested manifest shapes x two spawn doors\n")
    print(f"{'row':<24} {'spawn':<8} {'wave':<8} refusal text (identical at both doors)")
    print(f"{'-' * 24} {'-' * 8} {'-' * 8} {'-' * 46}")

    raises = named = identical = 0
    for label, body in NESTED_SHAPE_ROWS:
        _overwrite_manifest(fdir, json.dumps(body))
        single_ok, single_text = drive(
            lambda: fs.foundry_spawn_teammate(1, "cast", project_root)
        )
        bulk_ok, bulk_text = drive(lambda: fs.foundry_cast_wave(1, "cast", project_root))
        raises += [single_ok, bulk_ok].count("RAISE")
        named += sum("manifest.json" in t for t in (single_text, bulk_text))
        identical += single_text == bulk_text
        print(f"{label:<24} {single_ok:<8} {bulk_ok:<8} {single_text}")

    cells = 2 * len(NESTED_SHAPE_ROWS)
    print(
        f"\nTOTAL {raises}/{cells} RAISE, {named}/{cells} name manifest.json, "
        f"{identical}/{len(NESTED_SHAPE_ROWS)} rows byte-identical across doors"
    )

    assert (raises, named, identical) == (0, cells, len(NESTED_SHAPE_ROWS))


@pytest.mark.parametrize(
    ("body", "expected_path"),
    [
        ({"castings": "nope"}, "manifest.json.castings"),
        ({"castings": [1]}, "manifest.json.castings[0]"),
        ({"castings": [{"no_id": 1}]}, "manifest.json.castings[0].id"),
        ({"castings": [{"id": 1, "key_files": 7}]}, "manifest.json.castings[0].key_files"),
        ({"waves": "nope"}, "manifest.json.waves"),
        ({"waves": [{"wave": 1, "casting_ids": 42}]}, "manifest.json.waves[0].casting_ids"),
    ],
)
def test_the_refusal_names_the_key_path_that_failed(run_env, body, expected_path) -> None:
    """"The manifest is bad" is not actionable in a document with four rungs.

    The operator has to be told WHICH rung, or the only available action is to
    re-read the whole file. The last two rows are past the shapes D-132 itself
    drove — `casting_ids: 42` raised `TypeError: 'int' object is not iterable`
    out of Cast-Wave, and `key_files: "abc"` silently scoped a GRIND context to
    the CHARACTERS of the string. Neither was in the defect report; both are
    the same class, and both are closed by declaring the rung rather than by
    guarding the two rungs that were reported.
    """
    project_root, fdir = run_env
    _overwrite_manifest(fdir, json.dumps(body))

    result = fs.foundry_spawn_teammate(1, "cast", project_root)

    assert result["ok"] is False
    assert expected_path in result["error"]


def test_a_nested_failure_does_not_claim_the_document_is_not_an_object(run_env) -> None:
    """The self-contradicting message, pinned so it cannot come back.

    Reusing D-115's top-level wording one rung down produces "manifest.json is
    not a JSON object — parsed as dict", which is false on its face and sends
    the reader to inspect a container that is fine. Each rung states the shape
    IT expected.
    """
    project_root, fdir = run_env
    _overwrite_manifest(fdir, json.dumps({"castings": [1, 2, 3]}))

    error = fs.foundry_spawn_teammate(1, "cast", project_root)["error"]

    assert "parsed as dict" not in error
    assert error.startswith("manifest.json.castings[0] is not a JSON object")


def test_a_manifest_missing_a_key_entirely_is_not_a_shape_problem(run_env) -> None:
    """Absent is not corrupt, and the validator must not conflate them.

    `waves` is absent from a manifest the single door reads perfectly well, and
    `stream_skips` is absent from nearly every real manifest. A validator that
    demanded every declared key would refuse the ordinary document — which is
    how a shape guard turns into an outage.
    """
    project_root, fdir = run_env
    _overwrite_manifest(fdir, json.dumps({"castings": [{"id": 1}]}))

    assert fs.foundry_spawn_teammate(1, "cast", project_root)["ok"] is True

    # And the reverse: no castings key, but a wave the bulk door can read.
    _overwrite_manifest(fdir, json.dumps({"waves": [{"wave": 1, "casting_ids": [1]}]}))

    assert fs.foundry_cast_wave(1, "cast", project_root)["ok"] is True


@pytest.mark.parametrize(
    "body", [body for _label, body in NESTED_SHAPE_ROWS],
    ids=[label for label, _body in NESTED_SHAPE_ROWS],
)
def test_the_grind_context_builder_degrades_on_every_nested_shape(grind_repo, body) -> None:
    """The third reader, on the same evidence as the two doors.

    Its contract is that it never fails a spawn, so it degrades rather than
    refusing — but it must decide "unusable" the SAME way the doors do, or a
    document the doors accept can still raise here, past their guards, from the
    helper that promises not to fail. That is exactly what happened:
    `castings: [1,2,3]` reached `c.get("id")` inside this builder.

    The unscoped context an unparseable manifest already produces is the
    yardstick, so the assertion is equality with that, not merely "no raise".
    """
    project_root, fdir = grind_repo

    _overwrite_manifest(fdir, "{truncated")
    degraded = fs._build_grind_cycle_context(fdir, 1, project_root)

    _overwrite_manifest(fdir, json.dumps(body))
    nested = fs._build_grind_cycle_context(fdir, 1, project_root)

    assert nested == degraded
    assert KEY_FILE in nested  # the diff is still reported, just unscoped


# --------------------------------------------------------------------------- #
# The derived-membership pin: the shape table cannot fall behind the readers
# --------------------------------------------------------------------------- #
#
# Mirrors GUARDED_CYCLE_READERS in test_orchestrator_gates.py. That scan asks
# "does every read of state.json's cycle go through a guarded reader?"; this one
# cannot, because at D-132 both doors DID call the guard — they called it at the
# top rung and then indexed the records below it. Presence of the guard was
# never the property. COVERAGE of it is.
#
# So the question here is "is every manifest key path this module indexes
# declared in the shape the validator walks?", and the key paths are parsed out
# of the module on disk rather than listed beside it. Against the pre-fix source
# the answer is no for all seven, because there was no shape at all.


#: The mapping accessors that reach a member off a loaded document. All three,
#: because a reader that reaches a member by ``m["k"]`` or ``m.setdefault("k",
#: [])`` is doing the same thing as one that reaches it by ``m.get("k")`` and
#: must not escape the scan by choosing a different spelling. ``setdefault``
#: earns its place on evidence rather than symmetry: ``evidence.py``'s manifest
#: writers reach ``castings`` that way and are invisible to a scan that knows
#: only the first two.
_MEMBER_ACCESSORS = frozenset({"get", "setdefault"})  # 2 names


def _indexed(node: ast.AST, tainted: dict[str, str]) -> tuple[str, str] | None:
    """``(base_path, key)`` when ``node`` indexes a manifest-derived name.

    Every spelling, per ``_MEMBER_ACCESSORS`` above. Only ``Load`` subscripts:
    ``entry["model"] = model`` builds a NEW document, it does not read this one.
    """
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in _MEMBER_ACCESSORS
        and node.args
        and isinstance(node.args[0], ast.Constant)
        and isinstance(node.args[0].value, str)
    ):
        base, key = node.func.value, node.args[0].value
    elif (
        isinstance(node, ast.Subscript)
        and isinstance(node.ctx, ast.Load)
        and isinstance(node.slice, ast.Constant)
        and isinstance(node.slice.value, str)
    ):
        base, key = node.value, node.slice.value
    else:
        return None
    if isinstance(base, ast.Name) and base.id in tainted:
        return tainted[base.id], key
    return None


def _record_call_path(
    node: ast.AST, tainted: dict[str, str], records: dict[str, str] | None
) -> str | None:
    """The manifest path a call to a RECORD-RETURNING helper yields (D-148).

    ``records`` holds helpers that were handed the manifest itself and hand
    back a rung of it — ``def _rows(m): return m.get("castings", [])``. Only a
    call that is itself HANDED something tainted resolves: the taint travels
    with the argument, so ``_rows(manifest)`` is records and ``_rows(anything)``
    is nothing at all. That check is what keeps this from becoming the
    laundering ``_returned_path`` was written narrow to avoid.
    """
    if not (records and isinstance(node, ast.Call) and isinstance(node.func, ast.Name)):
        return None
    path = records.get(node.func.id)
    if path is None:
        return None
    arguments = list(node.args) + [kw.value for kw in node.keywords]
    if any(_expr_path(arg, tainted) is not None for arg in arguments):
        return path
    return None


def _expr_path(
    expr: ast.AST, tainted: dict[str, str], records: dict[str, str] | None = None
) -> str | None:
    """The manifest key path ``expr`` evaluates to, or None.

    Indexing first, bare reference last, so ``manifest.get("waves") or []``
    resolves to ``waves`` rather than to the manifest root. The ``or []`` /
    ``if isinstance(...) else []`` idioms this module uses are handled by
    walking the subtree instead of matching a statement shape.

    A record-returning call sits BETWEEN those two, and the order is
    load-bearing (D-148): ``for c in _rows(manifest)`` contains the tainted
    name ``manifest``, so the bare-reference rung would resolve it to the
    manifest ROOT and ``c`` would carry ``[]`` instead of ``castings[]`` —
    the scan would follow the chain and then miss the rung at the end of it.
    """
    for node in ast.walk(expr):
        hit = _indexed(node, tainted)
        if hit is not None:
            base, key = hit
            return f"{base}.{key}" if base else key
    for node in ast.walk(expr):
        path = _record_call_path(node, tainted, records)
        if path is not None:
            return path
    for node in ast.walk(expr):
        if isinstance(node, ast.Name) and node.id in tainted:
            return tainted[node.id]
    return None


#: Building a new container FROM manifest values yields a new document, not the
#: manifest — `entry = {"casting_id": cid, ...}` must not inherit cid's path.
_CONTAINER_NODES = (
    ast.Dict, ast.List, ast.Set, ast.Tuple,
    ast.DictComp, ast.ListComp, ast.SetComp, ast.GeneratorExp,
)


def _assigned_names(target: ast.AST) -> list[str]:
    """The plain names one assignment target binds.

    A TUPLE target binds all of them, and every element carries the taint. That
    is deliberately conservative: ``manifest, problem = read_document(path)``
    taints ``problem`` too, which is a string nobody ever indexes, so the
    over-approximation costs nothing — while the alternative of taking element
    0 only would encode an assumption about which slot the document sits in,
    and be wrong the first time a primitive returns them the other way round.

    The narrow ``ast.Name``-only rule this replaces is how the scan went blind:
    the moment the manifest reads moved behind a ``(value, problem)`` primitive,
    every taint chain in the module started at a tuple target, the scan found
    NOTHING, and only the vacuity assertion below stood between that and a
    green test asserting nothing at all.
    """
    if isinstance(target, ast.Name):
        return [target.id]
    if isinstance(target, (ast.Tuple, ast.List)):
        return [n for element in target.elts for n in _assigned_names(element)]
    return []


def _returned_path(value: ast.AST, returning: dict[str, str] | None) -> str | None:
    """The manifest path a direct call to a manifest-building function returns.

    Deliberately narrow: only a call by bare name, and only to a function that
    carries the ``"manifest.json"`` literal in its OWN body. Propagating
    the return of any function whose parameters happen to be tainted would make
    a shared loader poison every caller — ``_load_json(fdir / "state.json")``
    would be read as a manifest the moment some other call site passed it the
    real one. The literal is what makes the answer about this document rather
    than about whatever the caller supplied.

    Walked over the subtree rather than matched against a statement shape, for
    the same reason ``_expr_path`` is: ``manifest_path = resolve(root)`` and
    ``manifest = json.loads(resolve(root).read_text(...))`` are one reader
    written two ways, and a rule that only recognises the first is a rule about
    punctuation.
    """
    if not returning:
        return None
    for node in ast.walk(value):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            path = returning.get(node.func.id)
            if path is not None:
                return path
    return None


def _taint_map(
    fn: ast.AST,
    seed: dict[str, str] | None = None,
    returning: dict[str, str] | None = None,
    records: dict[str, str] | None = None,
) -> dict[str, str]:
    """Name -> manifest key path, for every manifest-derived name in ``fn``.

    Taint starts at the ``"manifest.json"`` literal — or at ``seed``, the
    parameter taint a CALLER established (D-134) — and propagates by
    assignment, aliasing, iteration and comprehension to a fixed point, so the
    scan follows the module's real chain — ``manifest_path`` to ``manifest`` to
    ``castings`` to ``c`` — rather than only recognising reads written directly
    off the parsed document. D-132's own reads are three hops from the literal;
    a scan that stopped at one hop would have reported the module clean.
    """
    tainted: dict[str, str] = dict(seed or {})
    nodes = list(ast.walk(fn))
    for _ in range(len(nodes) or 1):
        before = dict(tainted)
        for node in nodes:
            if isinstance(node, ast.Assign) and len(node.targets) == 1:
                names = _assigned_names(node.targets[0])
                if not names:
                    continue
                if _has_literal_taint(node.value):
                    for name in names:
                        tainted[name] = ""
                    continue
                if isinstance(node.value, _CONTAINER_NODES):
                    continue
                # A callee that builds the manifest path ITSELF and hands it
                # back, tried only after the direct chain fails. `evidence.py`
                # reaches the manifest exactly this way — `_resolve_manifest_path`
                # owns the literal and its callers never see one — so a scan
                # that follows only the literal and the argument list reports
                # that module clean while both of its manifest writers index
                # records unguarded.
                path = _expr_path(node.value, tainted, records)
                if path is None:
                    path = _returned_path(node.value, returning)
                if path is not None:
                    for name in names:
                        tainted.setdefault(name, path)
            elif isinstance(node, (ast.For, ast.AsyncFor, ast.comprehension)):
                path = _expr_path(node.iter, tainted, records)
                if path is not None:
                    for name in _assigned_names(node.target):
                        tainted.setdefault(name, f"{path}[]")
        if tainted == before:
            break
    return tainted


def _manifest_paths_in_function(
    fn: ast.AST, seed: dict[str, str] | None = None
) -> set[str]:
    """Every manifest key path indexed inside one function."""
    tainted = _taint_map(fn, seed)
    found: set[str] = set()
    for node in ast.walk(fn):
        hit = _indexed(node, tainted)
        if hit is not None:
            base, key = hit
            found.add(f"{base}.{key}" if base else key)
    return found


def _manifest_paths_in(source: str) -> set[str]:
    """Every manifest key path the source indexes, in any function."""
    return {
        path
        for fn in ast.walk(ast.parse(source))
        if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef))
        for path in _manifest_paths_in_function(fn)
    }


def _shape_covers(path: str) -> bool:
    """True when ``_MANIFEST_SHAPE`` declares ``path`` or explicitly frees it.

    "Explicitly frees" is the ``None`` element shape: reaching one means the
    table has STATED that the reader below it is on its own, which is a
    decision someone made rather than a rung nobody thought about.
    """
    shape: object = fs._MANIFEST_SHAPE
    for token in re.findall(r"\[\]|[^.\[\]]+", path):
        if shape is None:
            return True
        if token == "[]":
            if not isinstance(shape, list):
                return False
            shape = shape[0]
            continue
        if not isinstance(shape, dict) or token not in shape:
            return False
        shape = shape[token]
    return True


def test_every_manifest_key_the_module_indexes_is_declared() -> None:
    """THE TEST THAT FAILS WHEN A READER BYPASSES THE VALIDATOR.

    The subject is ``foundry_spawn.py``, located through its own import rather
    than by a typed path. The boundary is the module and not the package —
    unlike D-066, where stopping at ``tools/`` excluded the file most on the
    request path for no reason that survived being written down, the boundary
    here is the write surface of the packet that owns ``_MANIFEST_SHAPE``.
    Four more modules index this same document with their own top-rung-only
    guards (``foundry_orchestrator.py``, ``foundry_validate.py``,
    ``intent_coverage.py``, ``scripts/validate_intent_coverage.py``); widening
    to them is a real and recommended follow-up, logged to concerns.md, and it
    is a change to files this casting is forbidden to touch while castings 2
    and 3 are editing them.
    """
    paths = _manifest_paths_in(Path(fs.__file__).read_text(encoding="utf-8"))

    # The scan must SEE something, or the assertion below passes vacuously —
    # the failure mode of every derived-membership check.
    assert {"castings[].id", "waves[].wave"} <= paths, (
        f"the scan found {sorted(paths)}, which does not include the two reads "
        f"D-132 was filed on. It has stopped following the module's taint "
        f"chain and is no longer testing anything."
    )

    undeclared = sorted(p for p in paths if not _shape_covers(p))
    assert not undeclared, (
        f"{undeclared} are manifest key paths foundry_spawn.py indexes but "
        f"_MANIFEST_SHAPE does not declare. Every such path is a rung the "
        f"shared validator does not check and every reader therefore indexes "
        f"unguarded — which is D-115 (container guarded, records not) and "
        f"D-132 (records guarded, their members not) repeating one level "
        f"further down. Declare the rung in _MANIFEST_SHAPE, or declare it "
        f"None to state on the record that the reader guards itself. Do not "
        f"delete the path from this assertion."
    )


def test_the_scan_catches_a_reader_that_indexes_an_undeclared_key() -> None:
    """The detector's own adjacent path — proof it is not vacuously passing.

    The path the defect was found on is a read written directly against the
    parsed document. The ADJACENT path driven here is the one D-132 actually
    lived on and a naive scan would miss: a read three hops downstream of the
    literal, reached through a Path variable, a list member and a loop target.
    Both shapes must be reported, or the assertion above is decorative.
    """
    source = '''
def a_reader(fdir):
    manifest_path = fdir / "castings" / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    teams = manifest.get("teams") or []
    for t in teams:
        if t["roster"]:
            return t.get("lead")
    return manifest["castings"]
'''
    paths = _manifest_paths_in(source)

    assert paths == {"teams", "teams[].roster", "teams[].lead", "castings"}
    assert sorted(p for p in paths if not _shape_covers(p)) == [
        "teams",
        "teams[].lead",
        "teams[].roster",
    ]


def test_the_shape_table_states_the_two_keys_the_readers_address_records_by() -> None:
    """`id` and `wave` are REQUIRED, not merely typed, and that is the point.

    Every reader in the module locates its record by one of them. An entry
    without one is not addressable, so tolerating it produces the worst outcome
    available: `castings: [{"no_id": 1}]` made Spawn-Teammate report
    "casting_id 1 not found in manifest — available ids: [None]", which is a
    named refusal about the WRONG THING. It sends the lead to look for a
    missing casting in a manifest whose castings are all corrupt.
    """
    castings_entry = fs._MANIFEST_SHAPE["castings"][0]
    waves_entry = fs._MANIFEST_SHAPE["waves"][0]

    assert castings_entry["id"] is fs._REQUIRED
    assert waves_entry["wave"] is fs._REQUIRED


# --------------------------------------------------------------------------- #
# SHARED MEMBERSHIP — which files every package-wide rule in this module judges
# --------------------------------------------------------------------------- #
#
# Both structural rules below (the decode-read rule and the manifest-record
# rule) are properties of the SHIPPED SOURCE rather than of one module, so they
# ask the same question about which files that is, in one place. Membership was
# the axis D-146 found bound to a single module: the manifest rule derived its
# files and the read rule named one, and the read rule was blind to every other
# module in the package for exactly as long as that difference stood.


def _plugin_root() -> Path:
    """``plugins/foundry`` -- located from the installed package's own file."""
    # .../plugins/foundry/mcp-server/src/foundry_mcp/__init__.py -> parents:
    #   [0]=foundry_mcp, [1]=src, [2]=mcp-server, [3]=plugins/foundry.
    return Path(foundry_mcp.__file__).resolve().parents[3]


def _unshipped_dir_names() -> frozenset[str]:
    """Directory names the repository itself declares it does not ship.

    Read out of the repo's own ``.gitignore`` -- the one place that already
    states this -- rather than typed here, so a newly ignored tree is excluded
    the day it is declared and nobody has to remember a second copy. Only the
    trailing-slash entries are directory declarations; ``*.pyc`` and friends
    name files and are none of this census's business.

    This is what keeps the enumeration below both fast and STABLE. ``.venv``
    holds several thousand third-party modules and exists only in a checkout
    where somebody has run ``uv``; ``__pycache__`` holds none of ours and comes
    and goes with the last test run. A census that let either in would render
    differently in the working tree and in the detached worktree the evidence
    gate builds, which is D-152/D-161's failure on the neighbouring axis.
    """
    names: set[str] = set()
    for line in (REPO_ROOT / ".gitignore").read_text(encoding="utf-8").splitlines():
        entry = line.strip()
        if not entry or entry.startswith("#") or not entry.endswith("/"):
            continue
        names.add(entry.strip("/").rsplit("/", 1)[-1])
    return frozenset(names)


def _shipped_python_dirs(plugin_root: Path) -> list[Path]:
    """Every directory under ``plugin_root`` that DIRECTLY holds a shipped ``.py``.

    ``os.walk`` rather than ``rglob`` because the unshipped trees have to be
    PRUNED rather than filtered afterwards -- walking ``.venv`` to throw it
    away costs seconds and buys nothing.

    This is the census D-159 found missing. It answers "which directories does
    this plugin ship Python in", from the tree, so that WHICH ROOTS EXIST can
    be derived by ``_derived_roots`` and whatever is still outside them can be
    named by ``_unscanned_shipped_dirs`` instead of being absent.
    """
    unshipped = _unshipped_dir_names()
    found: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(plugin_root):
        dirnames[:] = sorted(d for d in dirnames if d not in unshipped)
        if any(name.endswith(".py") for name in filenames):
            found.append(Path(dirpath).resolve())
    return sorted(found)


#: Directories holding a shipped ``.py`` that no package-wide rule judges, each
#: mapped to the reason it is outside. Keys are relative to ``plugins/foundry``.
#:
#: The pin in ``test_every_shipped_python_directory_is_scanned_or_declared``
#: asserts the unscanned set EQUALS these keys BOTH WAYS -- the two-sided shape
#: ``test_evidence.py#test_no_module_declares_its_own_requirement_id_grammar``
#: uses for offenders outside its grant (``pending_unowned``). A new shipped
#: directory therefore fails BY NAME, which is precisely what D-159 drove and
#: could not get: a planted four-violation reader in ``plugins/foundry/lib/``
#: left the suite byte-identical, because being outside every root and being
#: clean were the same observation.
_UNSCANNED_SHIPPED_DIRS = {
    "mcp-server/tests": (
        "the suite itself. These files ARE the package-wide rules; scanning "
        "them would report every planted violation string in this module as a "
        "package offender, and would make each rule's membership include its "
        "own judge."
    ),
}  # 1 declared exemption


def _derived_roots(pkg: Path) -> list[Path]:
    """The source trees every package-wide rule judges, membership DERIVED.

    D-159, the axis: membership WITHIN a root was derived by rglob, but WHICH
    ROOTS EXIST was a two-element list extended by hand. ``scripts`` was in it
    only because D-141 reported the D-137 document-load rule sitting inside the
    package boundary and the fix added that one directory. A shipped reader in
    a NEW top-level directory under ``plugins/foundry/`` was therefore outside
    every package-wide rule at once -- and not reported as unscanned either,
    just absent, which reads exactly like clean.

    So the roots are walked out of the plugin tree now. Both ORIGINAL roots
    keep their identity AND their position, because both are contracts:

      ``[0]``   the importable package, located from ``foundry_mcp.__file__``.
      ``[-1]``  ``plugins/foundry/scripts`` -- shipped source that reads run
                artifacts (``measure-run.py``, ``migrate-archive.py``,
                ``validate-intent-coverage.py``) and is NOT importable as part
                of the package (hyphenated filenames), so it is parsed from
                disk. ``test_orchestrator_gates.py`` addresses it positionally
                as ``_scanned_roots()[-1]``; it stays last by construction
                rather than by alphabetical luck.

    Anything else holding a shipped ``.py`` lands in between, discovered, and
    is judged by every rule riding this list the day it appears -- including
    the two in the other test modules that import this helper. Today the walk
    yields exactly the two roots the literal named, so nothing else moves.
    """
    pkg = pkg.resolve()
    plugin_root = pkg.parents[2]
    scripts = plugin_root / "scripts"
    exempt = [plugin_root / key for key in _UNSCANNED_SHIPPED_DIRS]
    discovered: list[Path] = []
    for candidate in _shipped_python_dirs(plugin_root):
        if candidate == pkg or pkg in candidate.parents:
            continue
        if candidate == scripts or scripts in candidate.parents:
            continue
        if any(candidate == e or e in candidate.parents for e in exempt):
            continue
        if any(root in candidate.parents for root in discovered):
            continue  # already covered by a root discovered above it
        discovered.append(candidate)
    return [pkg, *discovered, scripts]


def _unscanned_shipped_dirs(
    plugin_root: Path | None = None, roots: list[Path] | None = None
) -> list[str]:
    """Shipped-``.py`` directories that no root in ``roots`` reaches.

    The other half of D-159's exhaustiveness rule: ``_derived_roots`` says
    which roots exist, and this says what the derivation still does not reach,
    so a directory outside every rule ANNOUNCES itself instead of being absent.

    Rendered relative to the plugin root, which makes the census a claim about
    the tree rather than about where the tree happens to be checked out
    (D-152/D-161). ``plugin_root``/``roots`` are the plant tests' override, the
    same shape ``_scanned_modules`` already uses.
    """
    plugin_root = (_plugin_root() if plugin_root is None else plugin_root).resolve()
    roots = _scanned_roots() if roots is None else [r.resolve() for r in roots]
    return sorted(
        candidate.relative_to(plugin_root).as_posix()
        for candidate in _shipped_python_dirs(plugin_root)
        if not any(candidate == r or r in candidate.parents for r in roots)
    )


def _scanned_roots() -> list[Path]:
    """The source trees a shipped reader can live in, walked out of the tree.

    Derived from the installed package's own location rather than typed, and
    every root asserted to exist by
    ``test_the_manifest_scan_looks_in_both_shipped_source_trees`` below -- a
    scan whose root has moved finds nothing and passes, which is the failure
    mode these rules exist to avoid.

    WHICH roots exist is derived too, since D-159. See ``_derived_roots`` for
    the shape and for the two positional contracts it preserves.
    """
    return _derived_roots(Path(foundry_mcp.__file__).resolve().parent)


def _scanned_modules(roots: list[Path] | None = None) -> list[Path]:
    """Every ``.py`` under either root, membership derived on both axes.

    ``roots`` overrides the derivation for the plant tests ONLY, which drive
    this same rglob over a temporary tree. They cannot plant into the shipped
    one: two other castings run this suite against the same working tree, and a
    module that exists for four seconds inside ``src/foundry_mcp`` is a
    spurious offender in somebody else's run. Driving the real discovery
    function over a temporary root exercises the composition the plant is
    about — rglob finds a NEW nested module — while the ``os.walk`` oracle
    below proves the real roots are enumerated exhaustively.
    """
    return sorted(p for root in (roots or _scanned_roots()) for p in root.rglob("*.py"))


#: One module carrying a member of BOTH package-wide rules in this file: an
#: unguarded strict-decoding read, and a reach inside a manifest record whose
#: shape nobody established. Both rules ride the same membership, so one plant
#: proves the discovery reaches a new nested module for both of them.
_PLANTED_MEMBER_OF_BOTH_RULES = '''
from pathlib import Path


def loads_notes(path):
    return path.read_text(encoding="utf-8")


def reads_the_records(fdir):
    manifest = _load_json(fdir / "castings" / "manifest.json")
    for c in manifest.get("castings", []):
        print(c.get("id"))
'''


# --------------------------------------------------------------------------- #
# D-138 / D-146 — no read in the shipped source can raise a decode error
# --------------------------------------------------------------------------- #
#
# The escalated class, on the READ edge of this module. Five reads, guarded five
# different ways, and not one of the five caught the same thing:
#
#   _read_progress_ledger        except OSError
#   _latest_teammate_dispatches  except OSError            <- the reported site
#   _build_grind_cycle_context   except OSError
#   foundry_spawn_teammate       (the prompt read: no handler at all)
#   foundry_cast_wave            (the prompt read: no handler at all)
#
# `UnicodeDecodeError` is a `ValueError`, so `except OSError` does not catch it
# and neither does `except json.JSONDecodeError`. DRIVEN at ab5a430: one
# 0xe9 byte in spawns.log -- a log foundry itself writes -- made
# Foundry-Liveness raise `UnicodeDecodeError: 'utf-8' codec can't decode byte
# 0xe9 in position 109` across the MCP boundary, naming no file, from
# CT-004/AC-021/OT-010's entire surface.
#
# So the rule below is not "guard the spawns.log read". It is a property of
# EVERY read the module contains, computed from each call site's own code, with
# NO ALLOW-LIST: `read_text_file` passes it on its own merits because its body
# carries the try/except, and a caller that routes through it has no `read_text`
# for the scan to find. There is nothing to enrol and nothing a future edit can
# quietly add itself to.

#: What a TEXT read itself raises, as CLASSES. `json.JSONDecodeError` is not
#: here: a JSONL ledger decodes each LINE, and a line is a string in memory, so
#: that raise belongs to the parse and not to the read.
#:
#: Coverage is decided by asking Python, never by comparing names -- D-137's
#: structural half, which this rule would otherwise repeat one rung over:
#:
#:   issubclass(UnicodeDecodeError, ValueError)            is True
#:   issubclass(UnicodeDecodeError, OSError)               is False
#:   issubclass(UnicodeDecodeError, json.JSONDecodeError)  is False
#:
#: So `except ValueError` covers this family and `except OSError` does not,
#: because Python says so. A new exception spelling needs no edit here, and no
#: spelling can be admitted by being added to a list. An UNRESOLVABLE handler
#: name is REPORTED rather than silently skipped: it covers nothing (the
#: conservative direction -- it can only over-report) and it is named in the
#: failure, so a spelling this resolver cannot see announces itself instead of
#: quietly widening the gap.
_TEXT_READ_RAISES: tuple[type[BaseException], ...] = (OSError, UnicodeDecodeError)

#: How bytes enter a module. `read_bytes` is not a member: bytes are never
#: decoded, so no read of them can raise this family -- an exclusion by property
#: rather than by name, which is why it is safe to state.
_TEXT_READ_METHODS = frozenset({"read_text", "open"})  # 2 names

@functools.lru_cache(maxsize=None)
def _positional_index(spelling: str, parameter: str) -> int | None:
    """Where ``parameter`` sits POSITIONALLY in the call this spelling makes.

    Read off Python's own signatures rather than counted by hand, because the
    three spellings do not agree and the disagreement is invisible in the
    source: ``open(p, "r")`` puts the mode at index 1, ``p.open("r")`` puts it
    at index 0 (the bound method has already eaten ``self``), and ``errors``
    sits at 4, 3 and 1 respectively. A hand-written index that was right for
    one spelling reads the FILENAME as the mode in another, and a rule that
    misreads its own argument is worse than one that does not look.
    """
    probe = {
        "open": open,
        "Path.open": Path("probe").open,
        "Path.read_text": Path("probe").read_text,
    }[spelling]
    try:
        names = list(inspect.signature(probe).parameters)
    except (TypeError, ValueError):  # pragma: no cover - a builtin with no sig
        return None
    return names.index(parameter) if parameter in names else None


@functools.lru_cache(maxsize=None)
def _stream_facts(mode: str) -> tuple[bool, bool] | None:
    """``(decodes, readable)`` for an ``open()`` in ``mode`` -- asked of Python.

    THE PREDICATE IS THE AXIS THIS RULE'S CLASS RETREATED TO. Membership got
    derived over the package (D-146) while ``open`` stayed a bare NAME, so a
    rule about decode failures conscripted every append-only log write in the
    shipped source -- ``open(forge_log, "a", encoding="utf-8")`` cannot decode
    anything, and demanding a ``UnicodeDecodeError`` handler on it is a finding
    about punctuation. The other direction is worse: ``p.open("rb")`` and
    ``p.open("r")`` differ by one letter and by the entire question this rule
    asks.

    So the mode is not parsed and no letter is interpreted. A scratch file is
    opened in that very mode and the object Python hands back is INSPECTED:
    ``isinstance(handle, io.TextIOBase)`` is whether bytes are decoded at all,
    ``handle.readable()`` is whether anything can be read out of it. A mode
    Python itself rejects returns ``None``, which the scan REPORTS rather than
    skipping -- the direction D-138 fixed one rung over, where an unrecognised
    spelling hit a bare ``continue`` and came back clean.
    """
    with tempfile.TemporaryDirectory() as tmp:
        probe = Path(tmp) / "probe"
        if "x" not in mode:
            probe.write_bytes(b"")
        try:
            handle = open(probe, mode)
        except (ValueError, OSError, TypeError):
            return None
        try:
            return isinstance(handle, io.TextIOBase), handle.readable()
        finally:
            handle.close()


@functools.lru_cache(maxsize=None)
def _errors_handler_raises(name: str) -> bool:
    """True when this codec error handler RE-RAISES instead of substituting.

    ``read_text(encoding="utf-8", errors="replace")`` cannot raise
    ``UnicodeDecodeError`` -- the handler substitutes and returns -- so it is
    not a member of the decode family however unguarded it is. That is a fact
    about the codecs registry, so it is asked of the registry: the registered
    handler is handed a real ``UnicodeDecodeError`` and either answers or
    raises. A name Python does not know raises ``LookupError`` at read time,
    which is not a decode failure either, but it is also not a promise this
    resolver can keep -- so it answers the conservative way (assume it raises)
    and the site stays in the rule.
    """
    try:
        handler = codecs.lookup_error(name)
    except LookupError:
        return True
    try:
        handler(UnicodeDecodeError("utf-8", b"\xe9", 0, 1, "invalid start byte"))
    except Exception:
        return True
    return False


@functools.lru_cache(maxsize=None)
def _attribute_decodes(attr: str) -> bool:
    """True when ``handle.<attr>()`` on a TEXT stream can raise a decode error.

    The last axis of this rule that was still being decided by punctuation. A
    readable text mode is not enough to make a site a decode site: four of the
    package's ``open(lock_path, "a+", encoding="utf-8")`` calls exist purely to
    hold an ``fcntl.flock`` and never read a byte from the handle they open.
    Decoding happens on the READ, not on the open, so reporting those demands a
    ``UnicodeDecodeError`` handler on a lock — guidance that is simply wrong,
    and the kind of false positive that teaches people to stop believing a
    rule.

    So the question is put to Python instead of to a list of method names: a
    real text handle is opened over a byte that is not valid UTF-8, the
    attribute is called, and whether ``UnicodeDecodeError`` comes out is the
    answer. ``read``/``readline``/``readlines``/``__iter__`` say yes;
    ``fileno``/``flush``/``close``/``write``/``readable`` say no, each on its
    own merits. A method added to ``io.TextIOBase`` tomorrow is classified the
    day it is used, with nothing to update here.
    """
    with tempfile.TemporaryDirectory() as tmp:
        probe = Path(tmp) / "probe"
        probe.write_bytes(b"\xe9\n")
        with open(probe, "r", encoding="utf-8") as handle:
            member = getattr(handle, attr, None)
            if member is None or not callable(member):
                return False
            try:
                value = member()
            except UnicodeDecodeError:
                return True
            except Exception:
                return False
            try:
                # A lazy iterator decodes only once something consumes it.
                for _ in value:
                    break
            except UnicodeDecodeError:
                return True
            except Exception:
                return False
    return False


def _read_from_names(tree: ast.Module) -> set[str]:
    """Every name in this source that something READS FROM.

    Deliberately module-wide and therefore conservative: two same-named handles
    in different functions merge, and merging can only ever ADD a name, which
    is the over-reporting direction this whole family fails in on purpose.

    A name handed bare to a call counts, because the callee may read it —
    ``json.load(handle)`` decodes just as surely as ``handle.read()`` does, and
    from here there is no way to know which callee does. What does NOT count is
    an attribute the probe above says cannot decode, which is how
    ``fcntl.flock(lock_file.fileno(), ...)`` leaves the rule.
    """
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            if _attribute_decodes(node.attr):
                names.add(node.value.id)
        elif isinstance(node, (ast.For, ast.AsyncFor, ast.comprehension)):
            if isinstance(node.iter, ast.Name):
                names.add(node.iter.id)
        elif isinstance(node, ast.Call):
            for argument in list(node.args) + [kw.value for kw in node.keywords]:
                if isinstance(argument, ast.Name):
                    names.add(argument.id)
    return names


def _bound_handles(tree: ast.Module) -> dict[int, str]:
    """Call-node identity -> the name its result is bound to, if any.

    Only the two shapes an ``open()`` handle is ever bound by: ``with open(...)
    as name`` and ``name = open(...)``. A call bound to nothing is anonymous
    (``json.load(open(p))``), and an anonymous handle is treated as read —
    something took it, and this cannot see what.
    """
    bound: dict[int, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.With, ast.AsyncWith)):
            for item in node.items:
                if isinstance(item.optional_vars, ast.Name):
                    bound[id(item.context_expr)] = item.optional_vars.id
        elif (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
        ):
            bound[id(node.value)] = node.targets[0].id
    return bound


def _call_argument(node: ast.Call, index: int | None, keyword: str) -> ast.AST | None:
    """The argument at ``index`` or under ``keyword``, whichever the call used."""
    for kw in node.keywords:
        if kw.arg == keyword:
            return kw.value
    if index is not None and len(node.args) > index:
        return node.args[index]
    return None


def _literal_str(node: ast.AST | None) -> str | None:
    """``node``'s string value when it is a literal, else None."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _reader_namespace(tree: ast.Module, module_obj=None) -> dict:
    """The names a scanned module can see, for resolving exception spellings.

    Derived in layers, none of them typed: builtins, the real module object
    when there is one, the modules the source's own ``import X [as Y]``
    statements name -- which is what resolves the dotted
    ``json.JSONDecodeError`` spelling in a planted source that is not
    importable at all -- and the exception classes the source DEFINES ITSELF.

    That last layer is what package-wide membership needed (D-146). A module's
    own ``class LedgerShapeError(RuntimeError)`` is neither a builtin nor an
    import, so a namespace built from the first three layers cannot say what
    ``except LedgerShapeError:`` catches, counts it as catching nothing, and
    reports a perfectly well-formed guard. Reaching for the module object does
    not close it either: ``migrate-archive.py`` defines ``_Malformed`` and its
    hyphenated filename means the interpreter can never import it. So the class
    is REBUILT from the bases the source names and handed to Python --
    ``issubclass`` then answers about the real hierarchy rather than about a
    spelling, for an importable module and an unimportable script alike.
    """
    namespace = dict(builtins.__dict__)
    if module_obj is not None:
        namespace.update(vars(module_obj))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Import):
            continue
        for alias in node.names:
            try:
                namespace.setdefault(
                    alias.asname or alias.name.split(".")[0],
                    importlib.import_module(alias.name),
                )
            except Exception:  # pragma: no cover - an unimportable alias
                pass
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef) or node.name in namespace:
            continue
        bases = []
        for base in node.bases:
            if isinstance(base, ast.Name):
                bases.append(namespace.get(base.id))
            elif isinstance(base, ast.Attribute):
                owner = namespace.get(getattr(base.value, "id", ""))
                bases.append(getattr(owner, base.attr, None) if owner else None)
        raised = tuple(
            b for b in bases if isinstance(b, type) and issubclass(b, BaseException)
        )
        if not raised:
            continue
        try:
            namespace[node.name] = type(node.name, raised, {})
        except TypeError:  # pragma: no cover - an unbuildable base layout
            pass
    return namespace


def _handler_type_nodes(node: ast.AST) -> list[ast.AST]:
    """The exception expressions an ``except`` clause names, and only those.

    NOT ``ast.walk``: walking ``json.JSONDecodeError`` also yields the inner
    ``json`` Name, which resolves to a module rather than an exception and
    would be reported as unrecognised on a perfectly well-formed handler.
    """
    return list(node.elts) if isinstance(node, ast.Tuple) else [node]


def _handler_classes(
    handler: ast.ExceptHandler, namespace: dict
) -> tuple[list[type[BaseException]], list[str]]:
    """The exception CLASSES this ``except`` catches, and the names it could not."""
    if handler.type is None:
        return [BaseException], []  # bare `except:` catches everything

    resolved: list[type[BaseException]] = []
    unresolved: list[str] = []
    for node in _handler_type_nodes(handler.type):
        if isinstance(node, ast.Attribute):
            owner_name = getattr(node.value, "id", "?")
            spelling = f"{owner_name}.{node.attr}"
            owner = namespace.get(owner_name)
            obj = getattr(owner, node.attr, None) if owner is not None else None
        elif isinstance(node, ast.Name):
            spelling, obj = node.id, namespace.get(node.id)
        else:
            spelling, obj = ast.dump(node), None
        if isinstance(obj, type) and issubclass(obj, BaseException):
            resolved.append(obj)
        else:
            unresolved.append(spelling)
    return resolved, unresolved


def _handler_covers_text_decode(
    handler: ast.ExceptHandler, namespace: dict, unresolved: list[str]
) -> bool:
    """True when this ``except`` catches everything a text read can raise."""
    caught, missing = _handler_classes(handler, namespace)
    unresolved.extend(missing)
    return all(
        any(issubclass(raised, caught_cls) for caught_cls in caught)
        for raised in _TEXT_READ_RAISES
    )


def _is_text_read(node: ast.AST) -> bool:
    """True for a call that reads or opens a file's text.

    Both spellings of ``open`` -- the builtin and ``Path.open`` -- because a
    reader must not escape the scan by choosing the other one.
    """
    if not isinstance(node, ast.Call):
        return False
    if isinstance(node.func, ast.Attribute) and node.func.attr in _TEXT_READ_METHODS:
        return True
    return isinstance(node.func, ast.Name) and node.func.id == "open"


def _decode_site(
    node: ast.AST,
    bound: dict[int, str] | None = None,
    read_from: set[str] | None = None,
) -> tuple[bool, str | None]:
    """``(can_raise_a_decode_error, what_could_not_be_classified)``.

    The membership question this rule actually asks, one call site at a time.
    A site is a member when bytes it reads get decoded STRICTLY, because that
    and only that is what raises ``UnicodeDecodeError`` -- and this rule is
    named for that raise.

    Three shapes, and every one of them is decided by asking Python rather than
    by reading the source's punctuation:

      ``p.read_text(...)``   decodes, unless its ``errors=`` handler answers
                             instead of raising (``_errors_handler_raises``).
      ``open(p, mode)``      decodes when the mode yields a readable TEXT
                             stream (``_stream_facts``) AND something actually
                             reads the handle (``_attribute_decodes`` via
                             ``_read_from_names``). An append-only log write, a
                             ``"rb"`` lock handle and an ``"a+"`` handle that
                             is only ever flocked are therefore not members --
                             not because anybody enrolled them out, but
                             because none of them can decode a byte.
      anything else          not a read at all.

    WHAT THIS RULE DOES NOT POLICE, stated so it cannot be mistaken for an
    exemption: a write-mode ``open`` still raises ``OSError`` at an unguarded
    call site, and that is a real NFR-002 concern -- it is simply a different
    class from this one, with a different remedy, and folding it in here would
    make the decode rule's offender list unreadable at exactly the moment it
    went package-wide.

    A mode or an ``errors=`` value that is not a literal (a variable, an
    f-string, a computed value) cannot be resolved from the AST alone. It is
    REPORTED by name and counted as a MEMBER -- over-reporting is recoverable
    and visible, under-reporting is the defect this whole family exists to
    close.
    """
    if not isinstance(node, ast.Call):
        return False, None
    func = node.func
    is_read_text = isinstance(func, ast.Attribute) and func.attr == "read_text"
    is_open = (isinstance(func, ast.Attribute) and func.attr == "open") or (
        isinstance(func, ast.Name) and func.id == "open"
    )
    if not (is_read_text or is_open):
        return False, None

    if is_read_text:
        spelling = "Path.read_text"
    elif isinstance(func, ast.Attribute):
        spelling = "Path.open"
    else:
        spelling = "open"

    if is_open:
        mode_node = _call_argument(node, _positional_index(spelling, "mode"), "mode")
        mode = "r" if mode_node is None else _literal_str(mode_node)
        if mode is None:
            return True, f"open(mode={ast.dump(mode_node)}) is not a literal mode"
        facts = _stream_facts(mode)
        if facts is None:
            return True, f"open(mode={mode!r}) is a mode Python does not accept"
        decodes, readable = facts
        if not (decodes and readable):
            return False, None
        # The stream CAN decode; whether it ever does is the next question.
        handle = (bound or {}).get(id(node))
        if handle is not None and handle not in (read_from or set()):
            return False, None

    errors_node = _call_argument(
        node, _positional_index(spelling, "errors"), "errors"
    )
    if errors_node is not None:
        errors = _literal_str(errors_node)
        if errors is None:
            return True, f"errors={ast.dump(errors_node)} is not a literal handler"
        if not _errors_handler_raises(errors):
            return False, None
    return True, None


def _walk_decode_guarded(
    node: ast.AST, fn: str, guarded: bool, visit, namespace: dict, unresolved: list[str]
) -> None:
    """Depth-first walk carrying the enclosing function and decode coverage.

    ``guarded`` is True inside the BODY of a ``try`` whose handlers catch the
    decode family. It resets at every function boundary -- a function defined
    inside a ``try`` is not protected at the point it is CALLED -- and it does
    not extend into the handlers, ``else`` or ``finally``, where a second raise
    would propagate. Mirrors ``test_orchestrator_gates._walk_guarded``, which
    asks the same question about the JSON rung; kept as its own copy rather
    than imported because that module belongs to a concurrent casting and a
    test that imports a peer's test module fails for reasons about the peer.
    """
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        fn, guarded = node.name, False
    elif isinstance(node, (ast.Try, ast.TryStar)):
        # A `try` covers its body when ANY ONE handler catches the whole raise
        # set; two handlers that each catch half do not compose, because the
        # first matching one runs alone.
        covered = guarded or any(
            _handler_covers_text_decode(h, namespace, unresolved) for h in node.handlers
        )
        for stmt in node.body:
            _walk_decode_guarded(stmt, fn, covered, visit, namespace, unresolved)
        for part in (*node.handlers, *node.orelse, *node.finalbody):
            _walk_decode_guarded(part, fn, guarded, visit, namespace, unresolved)
        return
    visit(node, fn, guarded)
    for child in ast.iter_child_nodes(node):
        _walk_decode_guarded(child, fn, guarded, visit, namespace, unresolved)


def _text_reads(
    source: str, module: str, module_obj=None
) -> tuple[list[str], list[str], list[str], list[str]]:
    """``(seen, offenders, unresolvable_handlers, unclassified_reads)``.

    ``seen`` is D-142's anchor: every decode site the scan RECOGNISED, whether
    guarded or not. Without it ``assert not offenders`` is green both when the
    source is clean and when the matcher has quietly stopped recognising the
    shape it is looking for, and those are the two states this rule exists to
    tell apart.

    ``unclassified_reads`` is the same discipline on the other axis: a read
    whose mode or ``errors=`` handler could not be resolved is named, and
    counted as a member while it is.
    """
    every: list[str] = []
    offenders: list[str] = []
    unresolved: list[str] = []
    unclassified: list[str] = []
    tree = ast.parse(source)
    bound = _bound_handles(tree)
    read_from = _read_from_names(tree)

    def visit(node: ast.AST, fn: str, guarded: bool) -> None:
        member, unknown = _decode_site(node, bound, read_from)
        if not member:
            return
        site = f"{module}::{fn}:{node.lineno}"
        every.append(site)
        if unknown:
            unclassified.append(f"{site} — {unknown}")
        if not guarded:
            offenders.append(site)

    _walk_decode_guarded(
        tree, "<module>", False, visit, _reader_namespace(tree, module_obj), unresolved
    )
    return (
        sorted(set(every)),
        sorted(set(offenders)),
        sorted(set(unresolved)),
        sorted(set(unclassified)),
    )


#: The read D-138's whole family is named after: ``foundry_state.read_text_file``
#: IS the guarded primitive, so its own ``path.read_text`` is the one decode
#: site in the shipped source that can never legitimately disappear. It is this
#: rule's anchor for exactly that reason -- if the scan ever stops naming it,
#: the scan has stopped recognising ``read_text``, and every green it reports
#: after that is a green about nothing (D-142).
_DECODE_SCAN_ANCHOR = (
    "plugins/foundry/mcp-server/src/foundry_mcp/tools/foundry_state.py::read_text_file"
)


def _symbol_cites(sites: list[str]) -> list[str]:
    """``module::function`` for each site, deduplicated — the reporting form.

    The scan carries ``:line`` internally because one function can hold two
    reads and they are different sites. Nothing REPORTED carries it: a line
    number in a file this casting does not own goes stale the next time
    somebody touches that file, and the evidence corpus is re-executed and
    byte-compared. ``_unestablished_record_indexes`` cites the same way for the
    same reason.
    """
    return sorted({site.rsplit(":", 1)[0] for site in sites})


def _package_text_reads(
    roots: list[Path] | None = None,
) -> tuple[list[str], list[str], list[str], list[str]]:
    """The decode-read rule over every module in both shipped source trees."""
    seen: list[str] = []
    offenders: list[str] = []
    unresolved: list[str] = []
    unclassified: list[str] = []
    for path in _scanned_modules(roots):
        s, o, u, c = _text_reads(path.read_text(encoding="utf-8"), _cite(path))
        seen += s
        offenders += o
        unresolved += u
        unclassified += c
    return sorted(seen), sorted(offenders), sorted(set(unresolved)), sorted(unclassified)


def test_no_read_in_this_module_can_raise_a_decode_error() -> None:
    """D-138 as a property of the SHIPPED SOURCE, not of one module (D-146).

    The rule was bound to ``Path(fs.__file__)`` -- foundry_spawn.py alone --
    while its three sibling structural rules all derived their membership over
    the package. That difference is the ST-003 class living inside D-138's own
    fix: an unguarded ``read_text`` anywhere else was invisible, and D-146
    proved it by planting one in a NEW subpackage, one in foundry_validate.py
    and one in foundry_orchestrator.py, all three GREEN against the full suite.

    Membership now comes from ``_scanned_modules`` -- the same rglob over both
    shipped trees the manifest rule uses -- so there is no module the rule
    cannot see and no list to enrol one in. The predicate is derived too: a
    site is judged when Python says its stream decodes strictly, so an
    append-only log write leaves the rule by property rather than by exemption.
    """
    seen, offenders, unresolved, unclassified = _package_text_reads()

    assert not unresolved, (
        f"{unresolved} name exceptions this resolver could not turn into "
        f"classes, so it cannot say what they catch and has counted them as "
        f"catching nothing. Fix the resolver rather than the handler."
    )
    assert not unclassified, (
        f"{unclassified} are reads whose mode or errors= handler this scan "
        f"could not resolve from the source, so it cannot say whether they "
        f"decode. They are counted as members meanwhile. Fix the resolver."
    )

    # D-142's anchor. `assert not offenders` is green both when the source is
    # clean AND when the matcher has stopped recognising the spelling it looks
    # for; naming the site the rule is ABOUT is what tells those apart.
    assert any(site.startswith(_DECODE_SCAN_ANCHOR) for site in seen), (
        f"the scan no longer names {_DECODE_SCAN_ANCHOR}, which is the guarded "
        f"primitive every read in this package is supposed to route through -- "
        f"it holds the one `path.read_text` that cannot legitimately go away. "
        f"Its absence means `_decode_site` has stopped recognising read_text, "
        f"not that the package got safer. Saw {len(seen)} decode sites: {seen}"
    )

    assert not offenders, (
        "these read a file's text without handling the decode failure, so one "
        "non-UTF-8 byte raises UnicodeDecodeError across the MCP boundary as a "
        "traceback naming no file. `except OSError` and `except "
        "json.JSONDecodeError` do not catch it -- UnicodeDecodeError is a "
        "ValueError. Route the read through foundry_state.read_text_file / "
        "read_json and return document_refusal (one file) or "
        "_unreadable_artifacts_refusal (several). There is no allow-list to "
        "add yourself to: the guarded primitive passes this rule on its own "
        "merits.\n  " + "\n  ".join(_symbol_cites(offenders))
    )


def test_the_read_scan_catches_a_freshly_planted_unguarded_read() -> None:
    """The detector's own adjacent path -- proof it is not vacuously passing.

    Four shapes, because the four are how the module actually got here: no
    handler at all, `except OSError`, `except json.JSONDecodeError`, and the
    `Path.open` spelling. A scan that caught only the bare read would have
    reported four of this module's five reads clean.
    """
    source = '''
def no_handler(p):
    return p.read_text(encoding="utf-8")

def only_oserror(p):
    try:
        return p.read_text(encoding="utf-8")
    except OSError:
        return ""

def only_json(p):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}

def path_open(p):
    with p.open("r", encoding="utf-8") as handle:
        return handle.read()

def properly_guarded(p):
    try:
        return p.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""
'''
    _every, offenders, _unresolved, _unclassified = _text_reads(source, "planted.py")
    assert [site.rsplit(":", 1)[0] for site in offenders] == [
        "planted.py::no_handler",
        "planted.py::only_json",
        "planted.py::only_oserror",
        "planted.py::path_open",
    ]


def test_a_read_planted_in_a_new_subpackage_is_named_by_the_package_rule(
    tmp_path,
) -> None:
    """D-146's own drive, run the other way round — and both rules at once.

    PROVE planted an unguarded ``read_text`` in a NEW module in a NEW
    subpackage (``foundry_mcp/schemas/notes.py``) and the full suite came back
    GREEN, because this rule's subject was one module. A per-module call proves
    the PREDICATE while leaving the MEMBERSHIP axis — the one that was broken
    — untested: a scan rooted at ``fs.__file__`` passes every predicate test in
    this file and still cannot see a single other module.

    So this drives the COMPOSITION: the shipped ``_package_text_reads`` /
    ``_package_record_readers``, over the shipped ``_scanned_modules`` rglob,
    against a root that holds one nested module it has never seen. The root is
    temporary rather than the real ``src/foundry_mcp`` because two other
    castings run this suite against the same working tree, and a plant that
    exists for four seconds inside the shipped package is a spurious offender
    in somebody else's run. What the real roots owe — that they exist and are
    walked EXHAUSTIVELY — is asserted against them directly by
    ``test_the_manifest_scan_looks_in_both_shipped_source_trees``'s ``os.walk``
    oracle.
    """
    root = tmp_path / "plantbed"
    planted = root / "a_new_subpackage" / "notes.py"
    planted.parent.mkdir(parents=True)
    planted.write_text(_PLANTED_MEMBER_OF_BOTH_RULES, encoding="utf-8")

    assert _scanned_modules([root]) == [planted], (
        "the shipped discovery did not find a module nested one directory "
        "below the root it was given, so its membership does not reach a new "
        "subpackage — D-146 exactly."
    )

    read_seen, read_offenders, _unresolved, _unclassified = _package_text_reads([root])
    record_seen, record_offenders = _package_record_readers([root])
    cite = _cite(planted)

    assert any(s.startswith(f"{cite}::loads_notes") for s in read_seen), (
        f"the package scan did not even SEE the planted read. Seen: {read_seen}"
    )
    assert any(o.startswith(f"{cite}::loads_notes") for o in read_offenders), (
        f"the package scan saw the planted read but did not report it as "
        f"unguarded, so the predicate has stopped recognising a bare "
        f"read_text. Offenders: {read_offenders}"
    )
    # The manifest rule rides the same membership, so the same plant proves it.
    assert record_seen == [f"{cite}#reads_the_records"], record_seen
    assert record_offenders == [f"{cite}#reads_the_records"], record_offenders


def test_the_read_scan_does_not_accept_a_guard_in_the_wrong_place() -> None:
    """A handler is not a guard for code that runs outside the try's body.

    Both shapes below LOOK guarded to a scan that only asks "does this function
    contain a decode handler somewhere": the read in the ``except`` clause runs
    while the first exception is propagating, and the nested ``def`` is called
    later, from somewhere else entirely.
    """
    source = '''
def read_in_the_handler(p, q):
    try:
        return p.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return q.read_text(encoding="utf-8")

def defines_a_reader_inside_a_try(p):
    try:
        def later(q):
            return q.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        later = None
    return later
'''
    _every, offenders, _unresolved, _unclassified = _text_reads(source, "planted.py")
    assert [site.rsplit(":", 1)[0] for site in offenders] == [
        "planted.py::later",
        "planted.py::read_in_the_handler",
    ]


# --------------------------------------------------------------------------- #
# D-134 — the manifest-shape validator's membership, derived over the PACKAGE
# --------------------------------------------------------------------------- #
#
# `test_every_manifest_key_the_module_indexes_is_declared` above binds ONE
# module, and its own docstring named the residual: four other modules index
# castings/manifest.json with their own top-rung-only guards. That residual is
# D-134, and this is it closed.
#
# THE HARM, driven live at 9b12e0f against the real _DISPATCH with
# castings/manifest.json = {"castings": "nope", "spec_type": "GREENFIELD"}:
#   Foundry-Validate-Castings -> AttributeError: 'str' object has no attribute 'get'
#   Foundry-Next              -> the identical AttributeError, via
#                                _compute_next_action -> _check_streams_complete
#                                -> _check_sight_required
# Foundry-Next is the mandatory handshake before EVERY phase transition, so this
# is the most-travelled door in the tool surface, not an edge case.
#
# WHY THE PROPERTY IS NOT "IS THE KEY DECLARED". At D-132 both spawn doors DID
# call the guard -- at the top rung, and then indexed the records below it.
# Presence of a declaration was never the property; COVERAGE of the rung a
# reader indexes is. `_check_sight_required` indexes `castings[].key_files`,
# which _MANIFEST_SHAPE declares perfectly well, and still raises, because that
# function never asks the validator anything.
#
# So the rule below is: a reader that reaches INSIDE a manifest record must
# first have ESTABLISHED that record's shape. Three axes, all derived:
#
#   which files    rglob over the installed package AND plugins/foundry/scripts,
#                  both located from the package's own __file__.
#   which rungs    walked out of _MANIFEST_SHAPE, so a rung added to the table
#                  is policed the day it is declared and a rung the table
#                  explicitly frees (element shape None) is not policed at all.
#   which readers  taint from the "manifest.json" literal, propagated ACROSS
#                  CALLS within a module, so a helper handed the parsed document
#                  is a member exactly as its caller is.
#
# THE ALLOW-LIST IS EMPTY. `scripts/validate_intent_coverage.py`'s
# `load_manifest_casting_ids` passes on its own merits -- it isinstance-guards
# the rung it indexes -- and not because anyone enrolled it. That second route
# is accepted because it satisfies the property, not because it is as good: one
# shared validator gives the operator one message, and an inline isinstance is
# a hand-applied copy of it. What the class forbids is indexing a rung whose
# shape NOBODY established, and an inline guard is somebody.


# ``_scanned_roots`` / ``_scanned_modules`` -- axis 1, "which files" -- now sit
# above, beside the decode rule that shares them (D-146). One derivation of the
# shipped source tree, judged by two rules, so neither can be widened without
# the other.


def _record_rungs(shape: object = None, path: str = "") -> set[str]:
    """Every ``_MANIFEST_SHAPE`` path whose ELEMENTS a reader indexes as records.

    Walked out of the shape table rather than listed beside it, so the rungs
    this rule polices are exactly the rungs the validator checks. Today that is
    ``castings[]`` and ``waves[]``; ``castings[].key_files`` and
    ``waves[].casting_ids`` are lists of NON-records (element shape ``None`` --
    the table's explicit statement that the reader below is on its own), so
    they are correctly not rungs, and ``stream_skips`` is the same by the same
    rule rather than by an exception written for it.
    """
    if shape is None and not path:
        shape = fs._MANIFEST_SHAPE
    rungs: set[str] = set()
    if isinstance(shape, list) and shape:
        element = shape[0]
        if isinstance(element, dict):
            rungs.add(f"{path}[]")
        rungs |= _record_rungs(element, f"{path}[]")
    elif isinstance(shape, dict):
        for key, sub in shape.items():
            rungs |= _record_rungs(sub, f"{path}.{key}" if path else key)
    return rungs


def _shape_validator_names() -> frozenset[str]:
    """The shared validators, read off the provider module rather than typed.

    D-134's contract LOCKS both names as exports of ``foundry_spawn.py`` so the
    two consumer modules can import them. Deriving the set from that module
    means a third validator is honoured the day it lands, and a rename shows up
    as the empty-set assertion below rather than as every consumer silently
    ceasing to count as guarded.
    """
    return frozenset(
        name
        for name in vars(fs)
        if name.startswith("_manifest_shape_") and callable(getattr(fs, name))
    )


def _index_base(node: ast.AST) -> tuple[ast.AST, str] | None:
    """``(base_node, key)`` for any member access, tainted or not."""
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in _MEMBER_ACCESSORS
        and node.args
        and isinstance(node.args[0], ast.Constant)
        and isinstance(node.args[0].value, str)
    ):
        return node.func.value, node.args[0].value
    if (
        isinstance(node, ast.Subscript)
        and isinstance(node.ctx, ast.Load)
        and isinstance(node.slice, ast.Constant)
        and isinstance(node.slice.value, str)
    ):
        return node.value, node.slice.value
    return None


def _functions_by_name(tree: ast.AST) -> dict[str, ast.AST]:
    """Every function in a module, keyed by name (nested ones included)."""
    return {
        node.name: node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


#: The document this whole rule is about, named once. Every decision about
#: whether a subtree touches the manifest goes through `_has_literal_taint`
#: below, and this is the only place the spelling appears.
_MANIFEST_LITERAL = "manifest.json"


def _has_literal_taint(node: ast.AST) -> bool:
    """True when this subtree names the manifest document by its own literal.

    THE ONE PRIMITIVE EVERY MEMBER OF THIS RULE CALLS (D-142). Taint used to
    start in three places — here, in ``_taint_map``'s assignment branch and in
    ``_returning_taint``'s return branch — each with its own inline copy of the
    same ``isinstance(n, ast.Constant) and n.value == "manifest.json"``. Three
    copies is three chances for the derivation to go blind in one place and
    stay green everywhere else, and it is why the vacuity probe below could not
    be written against a single seam. Now there is one seam: blind THIS and the
    whole scan sees nothing, which is exactly what the probe asserts, and what
    every anchor in this section fails by name on.

    Takes any node, not only a function: "does this expression name the
    manifest" and "does this function name it anywhere" are the same question
    asked of different subtrees.
    """
    return any(
        isinstance(n, ast.Constant) and n.value == _MANIFEST_LITERAL
        for n in ast.walk(node)
    )


def _returning_taint(funcs: dict[str, ast.AST]) -> dict[str, str]:
    """Function name -> the manifest path it RETURNS, for path-building helpers.

    One pass, not a fixed point, because the entry condition is the function's
    own literal: a helper that resolves ``castings/manifest.json`` and returns
    it does so without help from anybody.
    """
    returning: dict[str, str] = {}
    for name, fn in funcs.items():
        if not _has_literal_taint(fn):
            continue
        tainted = _taint_map(fn)
        for node in ast.walk(fn):
            if not isinstance(node, ast.Return) or node.value is None:
                continue
            # `return root / "castings" / "manifest.json"` builds the path in
            # the return expression itself and binds no name on the way, so the
            # taint map has nothing to say about it. That is the shape
            # `evidence.py#_resolve_manifest_path`'s last line takes.
            if _has_literal_taint(node.value):
                returning.setdefault(name, "")
                continue
            path = _expr_path(node.value, tainted)
            if path is not None:
                returning.setdefault(name, path)
    return returning


def _param_taint(
    funcs: dict[str, ast.AST],
    returning: dict[str, str] | None = None,
    records: dict[str, str] | None = None,
) -> tuple[dict[str, dict], dict[str, set]]:
    """Propagate manifest taint across calls inside one module, to a fixed point.

    ``_fingerprint_inputs(fdir, manifest)`` is why this exists: it indexes
    ``castings[].id`` off a document it never loaded, so a scan that starts
    taint only at the literal reports it clean while it raises on exactly the
    input the defect names. The caller's taint is the callee's.

    Returns ``(seed_by_function, callers_by_function)``. Keyed by NAME, so two
    same-named functions in one module merge -- conservative, which is the safe
    direction for a detector, and reported rather than assumed by the vacuity
    assertions on each rule.
    """
    seeds: dict[str, dict[str, str]] = {}
    callers: dict[str, set[str]] = {}
    for _ in range(len(funcs) + 1):
        grew = False
        for caller_name, fn in funcs.items():
            tainted = _taint_map(fn, seeds.get(caller_name), returning, records)
            for node in ast.walk(fn):
                if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)):
                    continue
                callee = funcs.get(node.func.id)
                if callee is None:
                    continue
                params = [a.arg for a in callee.args.posonlyargs + callee.args.args]
                for index, arg in enumerate(node.args):
                    if index >= len(params):
                        break
                    path = _expr_path(arg, tainted, records)
                    if path is None:
                        continue
                    seed = seeds.setdefault(node.func.id, {})
                    if params[index] not in seed:
                        seed[params[index]] = path
                        grew = True
                    if caller_name not in callers.setdefault(node.func.id, set()):
                        callers[node.func.id].add(caller_name)
                        grew = True
        if not grew:
            break
    return seeds, callers


def _record_returning(
    funcs: dict[str, ast.AST],
    seeds: dict[str, dict],
    returning: dict[str, str] | None,
) -> dict[str, str]:
    """Function name -> the manifest RUNG it hands back, for record accessors.

    D-148's axis, and the third of the three ways a reader can reach a record.
    ``_param_taint`` already follows the manifest INTO a helper; this follows
    the records back OUT of one:

        def _rows(m):     return m.get("castings", [])
        def a_reader(fdir):
            manifest = _load_json(fdir / "castings" / "manifest.json")
            for c in _rows(manifest):
                c.get("id")            # <- AttributeError on castings="nope"

    ``_rows`` indexes nothing dangerous itself and ``a_reader`` indexes nothing
    the old scan could see, so the whole module read CLEAN while raising the
    exact ``AttributeError`` the rule exists to forbid. Two hops
    (``_hop`` calling ``_rows``) is the same shape one call further out, which
    is why this runs to a fixed point rather than one pass.

    THE DESCENT TEST IS WHAT KEEPS THIS FROM LAUNDERING. A helper qualifies
    only when the path it returns is strictly BELOW the paths it was handed:
    ``_rows`` was handed the root ``""`` and returns ``castings``, so it
    descends. The generic loader ``def load(path): return
    json.loads(path.read_text(...))`` returns the same path it was handed, so
    it does not descend and never propagates — which is exactly the property
    ``test_the_scan_follows_a_manifest_path_a_helper_built`` was written to
    protect, and it is protected here by a derivation rather than by an
    exception written for it. A shared loader cannot poison its other callers
    because it hands back what it was given, and this rule only follows a
    helper that hands back something SMALLER.
    """
    records: dict[str, str] = {}
    for _ in range(len(funcs) + 1):
        grew = False
        for name, fn in funcs.items():
            if name in records:
                continue
            handed = seeds.get(name)
            if not handed:
                continue
            tainted = _taint_map(fn, handed, returning, records)
            for node in ast.walk(fn):
                if not isinstance(node, ast.Return) or node.value is None:
                    continue
                path = _expr_path(node.value, tainted, records)
                if path is None or path in set(handed.values()):
                    continue
                records[name] = path
                grew = True
                break
        if not grew:
            break
    return records


def _propagated_taint(
    funcs: dict[str, ast.AST], returning: dict[str, str] | None
) -> tuple[dict[str, dict], dict[str, set], dict[str, str]]:
    """``(seeds, callers, records)`` to a JOINT fixed point.

    The two propagations feed each other — a record-returning helper's output
    can be the argument that seeds the next helper — so neither can be computed
    once and handed to the other. They are iterated together until nothing
    moves, which is the only ordering that does not depend on which of the two
    a module happens to spell first.
    """
    seeds: dict[str, dict] = {}
    callers: dict[str, set] = {}
    records: dict[str, str] = {}
    for _ in range(len(funcs) + 1):
        next_seeds, next_callers = _param_taint(funcs, returning, records)
        next_records = _record_returning(funcs, next_seeds, returning)
        if (next_seeds, next_callers, next_records) == (seeds, callers, records):
            break
        seeds, callers, records = next_seeds, next_callers, next_records
    return seeds, callers, records


def _unestablished_record_indexes(path: Path) -> tuple[list[str], list[str]]:
    """``(seen, offenders)`` — every manifest-record reader, and the unguarded ones.

    ``seen`` is D-142's anchor: every function this scan RECOGNISED as reaching
    inside a manifest record, guarded or not. The rule used to return offenders
    alone, so ``assert not offenders`` was green both when the package was
    clean AND when the taint tracker had silently stopped recognising the
    package's spelling — and the suite's own comment records that the second
    thing actually happened during GRIND-20, when the manifest reads moved
    behind a ``(value, problem)`` primitive and every taint chain in the module
    started at a tuple target. A rule that cannot say what it saw cannot be
    asked whether it saw anything.

    ``guarded`` here is not a name on a list; it is one of three facts about
    the code: the function calls a shared validator, or it isinstance-guards
    the very name it indexes, or every caller that handed it the document has
    already done one of those.
    """
    source = path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(source)
    except SyntaxError:  # pragma: no cover - a source file that will not parse
        return [], []
    funcs = _functions_by_name(tree)
    if not funcs:
        return [], []

    rungs = _record_rungs()
    validators = _shape_validator_names()
    returning = _returning_taint(funcs)
    seeds, callers, records = _propagated_taint(funcs, returning)

    def calls_a_validator(fn: ast.AST) -> bool:
        return any(
            (node.attr if isinstance(node, ast.Attribute) else node.id) in validators
            for node in ast.walk(fn)
            if isinstance(node, (ast.Name, ast.Attribute))
        )

    established = {name for name, fn in funcs.items() if calls_a_validator(fn)}
    for _ in range(len(funcs) + 1):
        grew = False
        for name, fn in funcs.items():
            if name in established or name not in callers or _has_literal_taint(fn):
                continue
            if all(caller in established for caller in callers[name]):
                established.add(name)
                grew = True
        if not grew:
            break

    seen: set[str] = set()
    sites: dict[str, list[int]] = {}
    for name, fn in funcs.items():
        tainted = _taint_map(fn, seeds.get(name), returning, records)
        typed_names = {
            call.args[0].id
            for call in ast.walk(fn)
            if isinstance(call, ast.Call)
            and isinstance(call.func, ast.Name)
            and call.func.id == "isinstance"
            and call.args
            and isinstance(call.args[0], ast.Name)
        }
        for node in ast.walk(fn):
            parts = _index_base(node)
            if parts is None:
                continue
            base = parts[0]
            if not isinstance(base, ast.Name) or tainted.get(base.id) not in rungs:
                continue
            # SEEN before GUARDED. A reader the scan recognised counts toward
            # the anchor whether or not it is an offender — the anchor asks
            # "did the derivation still find the readers it was built on",
            # and a reader that is correctly guarded answers that just as well
            # as one that is not.
            seen.add(name)
            if name in established or base.id in typed_names:
                continue
            sites.setdefault(name, []).append(node.lineno)

    # Cited as `path#Symbol`, with no `:line` and no site count: both drift
    # under any edit to the cited file, and a citation that changes when the
    # file above it changes is a citation that goes stale on its own.
    cite = _cite(path)
    return (
        [f"{cite}#{name}" for name in sorted(seen)],
        [f"{cite}#{name}" for name in sorted(sites)],
    )


def _cite(path: Path) -> str:
    """A repo-relative path, so an offender reads as a citation not a basename.

    Falls back to the basename for a planted module under tmp_path, which is
    outside the repo by construction.
    """
    try:
        return str(path.resolve().relative_to(REPO_ROOT))
    except ValueError:
        return path.name


def _plant(tmp_path: Path, name: str, source: str) -> Path:
    """Write a synthetic module and return its path."""
    path = tmp_path / name
    path.write_text(source, encoding="utf-8")
    return path


#: The readers D-134 was FILED ON, as `path#Symbol`. Every one was driven
#: raising AttributeError across the MCP boundary, so every one is a reader
#: this scan must go on recognising — the anchor that makes `assert not
#: offenders` a claim about the package rather than about the scan's eyesight
#: (D-142). Six were unguarded when filed; `load_manifest_casting_ids` was
#: cleared BY THE RULE and belongs here for the same reason: the rule saw it.
_D134_FILED_READERS = (
    "plugins/foundry/mcp-server/src/foundry_mcp/tools/evidence.py"
    "#_append_to_manifest_evidence_provenance",
    "plugins/foundry/mcp-server/src/foundry_mcp/tools/foundry_orchestrator.py"
    "#_check_sight_required",
    "plugins/foundry/mcp-server/src/foundry_mcp/tools/foundry_orchestrator.py"
    "#_trace_skip_check",
    "plugins/foundry/mcp-server/src/foundry_mcp/tools/foundry_orchestrator.py"
    "#foundry_gate",
    "plugins/foundry/mcp-server/src/foundry_mcp/tools/foundry_validate.py"
    "#_fingerprint_inputs",
    "plugins/foundry/mcp-server/src/foundry_mcp/tools/foundry_validate.py"
    "#foundry_validate_castings",
    "plugins/foundry/mcp-server/src/foundry_mcp/scripts/validate_intent_coverage.py"
    "#load_manifest_casting_ids",
)  # 7 readers


def _package_record_readers(
    roots: list[Path] | None = None,
) -> tuple[list[str], list[str]]:
    """``(seen, offenders)`` for the manifest-record rule over both trees."""
    seen: list[str] = []
    offenders: list[str] = []
    for module in _scanned_modules(roots):
        module_seen, module_offenders = _unestablished_record_indexes(module)
        seen += module_seen
        offenders += module_offenders
    return sorted(seen), sorted(offenders)


def test_the_manifest_scan_looks_in_both_shipped_source_trees() -> None:
    """Neither root may be missing, and neither may be silently unproductive.

    Derived membership is worth nothing if the derivation runs over an empty
    set, and a root that has MOVED produces exactly that: a scan that finds no
    offenders and a test that passes for the wrong reason. ``os.walk`` is the
    independent oracle rather than a second ``rglob``, so a future narrowing of
    the discovery to a directory literal is caught by disagreement.

    A MODULE COUNT WAS NOT ENOUGH (D-142). This asserted ``> 20`` modules
    discovered, which stays true under a taint tracker that recognises nothing:
    thirty modules scanned and zero readers found reads as a clean package. So
    each root is now asserted to PRODUCE a member of each rule — by planting
    one in it and requiring the rule to name it.

    Planting rather than counting, because counting what the trees hold today
    is not a stable claim about the scan. The hyphenated scripts tree held four
    decode sites this morning and holds none now (D-141 routed them through the
    primitive), and it has never held a manifest reader — its
    ``validate-intent-coverage.py`` is a shim delegating to the package's own
    copy. A census over it would therefore have to assert zero, which is the
    vacuity this rule is trying to escape, and it would go green whether the
    root is scanned or has moved. A planted member cannot: it is found only if
    the root is really being walked and the predicates really recognise it.
    """
    roots = _scanned_roots()
    for root in roots:
        assert root.is_dir(), (
            f"{root} does not exist, so this rule is scanning nothing. The "
            f"roots are derived from foundry_mcp.__file__; if the tree moved, "
            f"fix _scanned_roots rather than deleting the root."
        )

    discovered = {p.resolve() for p in _scanned_modules()}
    walked = {
        Path(dirpath, filename).resolve()
        for root in roots
        for dirpath, _dirnames, filenames in os.walk(root)
        for filename in filenames
        if filename.endswith(".py")
    }
    assert discovered == walked, (
        f"discovery disagrees with an independent walk of the same roots: "
        f"{sorted(str(p) for p in walked ^ discovered)}"
    )
    assert len(discovered) > 20, f"only {len(discovered)} modules discovered"

    # ...and the manifest rule must still be recognising REAL readers in the
    # REAL tree. This is the assertion a module count could not make: thirty
    # modules discovered and zero readers recognised is what a blinded tracker
    # produces, and it read as a clean package.
    readers, _offenders = _package_record_readers()
    assert readers, (
        "the manifest-record rule recognised no reader anywhere in either "
        "tree, so every offender-only assertion built on it is vacuous"
    )


def test_every_shipped_python_directory_is_scanned_or_declared() -> None:
    """D-159 — exhaustiveness over WHICH ROOTS EXIST, not just within them.

    The rule above proves each root is really walked. It cannot prove the set
    of roots is complete, and that is the whole of D-159: ``_scanned_roots``
    named two directories, membership WITHIN them was derived by rglob, and
    membership OF them was a literal extended by hand. PROVE planted
    ``plugins/foundry/lib/reader.py`` carrying FOUR violations at once and the
    suite was BYTE-IDENTICAL -- outside every root and clean are the same
    observation, so nothing had anything to say.

    So: enumerate the directories under ``plugins/foundry`` that hold shipped
    ``.py``, and assert each is either inside a scanned root or declared in
    ``_UNSCANNED_SHIPPED_DIRS`` with its reason. The pin is TWO-SIDED, the
    shape ``test_evidence.py#test_no_module_declares_its_own_requirement_id_grammar``
    uses for offenders outside its grant: the unscanned set must EQUAL the
    declared set, so a new shipped directory fails by name and a declaration
    that stops being true fails too.
    """
    plugin_root = _plugin_root()
    census = [
        str(d.relative_to(plugin_root)) for d in _shipped_python_dirs(plugin_root)
    ]

    # ANCHORS (D-142). An enumeration that has gone blind reports nothing
    # unscanned, and the two-sided pin below then goes green over a census it
    # can no longer read. So the census is required to still contain the two
    # roots this rule was built on, a subpackage inside one of them, and
    # nothing from the trees the repository says it does not ship.
    assert "mcp-server/src/foundry_mcp" in census, census
    assert "mcp-server/src/foundry_mcp/tools" in census, census
    assert "scripts" in census, census
    assert "mcp-server/tests" in census, census
    unshipped = _unshipped_dir_names()
    assert {".venv", "__pycache__"} <= unshipped, sorted(unshipped)
    leaked = [d for d in census if set(Path(d).parts) & unshipped]
    assert leaked == [], (
        f"the census walked into a tree .gitignore excludes: {leaked}. That "
        f"makes it a function of whether anyone has run `uv` in this checkout."
    )

    # Every derived root is a real directory that the census found -- the roots
    # and the census have to be answers about the same tree.
    for root in _scanned_roots():
        assert root.is_dir(), (
            f"{root} does not exist, so this rule is scanning nothing. The "
            f"roots are derived from foundry_mcp.__file__; if the tree moved, "
            f"fix _derived_roots rather than deleting the root."
        )
        assert str(root.relative_to(plugin_root)) in census, (root, census)

    # THE PIN, both ways.
    unscanned = _unscanned_shipped_dirs()
    assert unscanned == sorted(_UNSCANNED_SHIPPED_DIRS), (
        f"the set of shipped-.py directories that NO package-wide rule judges "
        f"changed: now {unscanned}, declared {sorted(_UNSCANNED_SHIPPED_DIRS)}. "
        f"A new one must be scanned or declared with the reason it is outside "
        f"-- it may not simply be absent, which is what D-159 was filed on."
    )
    assert all(reason.strip() for reason in _UNSCANNED_SHIPPED_DIRS.values()), (
        "an exemption without a stated reason is a literal with better "
        "manners; every key here states why that directory is outside"
    )


#: PROVE's D-159 plant, in shape: ONE module in a NEW top-level directory under
#: the plugin root carrying four package-rule violations at once -- an unguarded
#: strict-decoding ``read_text``, a raw ``json.loads`` of it, a bare ``print``
#: on a shipped path, and an unlocked ``rename`` onto a run artifact -- plus a
#: reach inside a manifest record whose shape nobody established, so BOTH
#: package-wide rules in this module have a member in it. Filed with the suite
#: byte-identical either way: `597 passed, 1 skipped` with and without it, and
#: no test naming the file.
_D159_PLANTED_READER = '''
import json


def loads_notes(path):
    return json.loads(path.read_text(encoding="utf-8"))


def reads_the_records(fdir):
    manifest = _load_json(fdir / "castings" / "manifest.json")
    for c in manifest.get("castings", []):
        print("planted reader tally: " + str(c.get("id")))


def publishes(tmp, dest):
    tmp.rename(dest)
'''


def test_a_shipped_reader_in_a_new_top_level_directory_is_not_invisible(
    tmp_path,
) -> None:
    """D-159 driven, on the plant that left the suite byte-identical.

    A faithful miniature of the plugin tree: the importable package under
    ``mcp-server/src/foundry_mcp``, the hyphenated-CLI ``scripts`` tree, the
    suite's own ``mcp-server/tests``, and a NEW top-level ``lib/`` holding
    PROVE's four-violation reader. Planted into a temporary tree rather than
    the shipped one for the reason ``_scanned_modules`` records: two other
    castings run this suite against the same working tree, and a module that
    exists for four seconds inside ``src/foundry_mcp`` is a spurious offender
    in somebody else's run.

    BOTH halves of the fix are driven on the SAME bed, because either alone is
    still D-159:

      under the OLD two-literal roots ``lib`` is not scanned -- and it is now
      REPORTED, so the directory announces itself instead of being absent;

      under the DERIVED roots ``lib`` IS a root, and both package-wide rules in
      this module name the file inside it.
    """
    bed = tmp_path / "foundry"
    pkg = bed / "mcp-server" / "src" / "foundry_mcp"
    tests = bed / "mcp-server" / "tests"
    scripts = bed / "scripts"
    lib = bed / "lib"
    for d in (pkg, tests, scripts, lib):
        d.mkdir(parents=True)
    bed = bed.resolve()
    pkg, tests, scripts, lib = (p.resolve() for p in (pkg, tests, scripts, lib))
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (scripts / "measure-run.py").write_text("VERSION = 1\n", encoding="utf-8")
    (tests / "test_x.py").write_text("VERSION = 1\n", encoding="utf-8")
    (lib / "reader.py").write_text(_D159_PLANTED_READER, encoding="utf-8")

    # BEFORE — the literal the defect was filed on. `lib` is outside it, and
    # the report is what makes that observable rather than silent.
    assert _unscanned_shipped_dirs(bed, [pkg, scripts]) == ["lib", "mcp-server/tests"]

    # AFTER — the derivation reaches it, and the two positional contracts hold.
    roots = _derived_roots(pkg)
    assert roots == [pkg, lib, scripts]
    assert roots[0] == pkg and roots[-1] == scripts
    assert _unscanned_shipped_dirs(bed, roots) == ["mcp-server/tests"]

    # ...and now the rules NAME the file, which is the observation the plant
    # could not produce: a four-violation module and a byte-identical suite.
    _every, decode_offenders, _u, _c = _package_text_reads(roots)
    assert [o.rsplit(":", 1)[0] for o in decode_offenders] == [
        "reader.py::loads_notes"
    ], decode_offenders
    _seen, record_offenders = _package_record_readers(roots)
    assert record_offenders == ["reader.py#reads_the_records"], record_offenders


def test_the_record_rungs_come_out_of_the_shape_table() -> None:
    """The rungs policed are the rungs the validator checks — not a typed list.

    ``castings[].key_files`` and ``waves[].casting_ids`` are lists whose element
    shape is ``None``: the table's explicit statement that the reader below is
    on its own. They are therefore NOT rungs, and ``stream_skips`` is excluded
    by that same rule rather than by an exception written for it. Add a list of
    objects to ``_MANIFEST_SHAPE`` and this rule polices it the same day.
    """
    assert _record_rungs() == {"castings[]", "waves[]"}

    grown = dict(fs._MANIFEST_SHAPE)
    grown["teams"] = [{"lead": fs._REQUIRED}]
    assert _record_rungs(grown) == {"castings[]", "waves[]", "teams[]"}

    freed = {"castings": [None]}
    assert _record_rungs(freed) == set()


def test_the_locked_validator_names_are_still_the_ones_the_scan_looks_for() -> None:
    """D-134's LOCKED contract, pinned where its loss would silently matter.

    Two consumer modules import these two names. If either is renamed, the
    imports break loudly — but the SCAN would quietly stop recognising the
    validator route and report every correctly-guarded reader as an offender,
    which is a worse failure than the import error because it looks like a
    finding.
    """
    assert _shape_validator_names() == {
        "_manifest_shape_problem",
        "_manifest_shape_error",
    }


def test_every_manifest_record_reader_in_the_package_establishes_the_shape() -> None:
    """D-134, as a property of every module that reads castings/manifest.json.

    RED at 9b12e0f on SIX functions, not the two the report named — and the
    four the hand search missed are the argument for the scan. Every one was
    driven and every one raises ``AttributeError`` across the MCP boundary on
    ``{"castings": [1,2,3]}`` or ``{"castings": "nope"}``:

      foundry_orchestrator.py#_check_sight_required   Foundry-Next  (reported)
      foundry_orchestrator.py#foundry_gate            Foundry-Gate  (found)
      foundry_orchestrator.py#_trace_skip_check       Foundry-Next's TRACE-skip
                                                                    (found)
      foundry_validate.py#foundry_validate_castings   Foundry-Validate-Castings
                                                                    (reported)
      foundry_validate.py#_fingerprint_inputs         same door, one call deeper
                                                                    (found)
      evidence.py#_append_to_manifest_evidence_provenance
                                                      evidence verification
                                                                    (found)

    ``scripts/validate_intent_coverage.py#load_manifest_casting_ids`` reads the
    same document and is NOT here, because it isinstance-guards the rung it
    indexes — recognised by the rule, not by an allow-list. There is no
    allow-list to add anything to.

    D-142's anchor sits above the offender assertion: every function named in
    the docstring above is a reader this scan must still RECOGNISE. Under the
    blind-taint probe (``test_a_blinded_derivation_fails_every_manifest_rule_by_name``)
    the derivation sees nothing at all, the offender list is empty, and without
    this anchor the test is green while asserting nothing whatsoever.
    """
    seen, offenders = _package_record_readers()

    missing = [reader for reader in _D134_FILED_READERS if reader not in seen]
    assert not missing, (
        "the scan no longer recognises these as manifest-record readers, which "
        "means its derivation went blind rather than that the package got "
        "safer — every one of them was DRIVEN raising AttributeError across "
        "the MCP boundary when D-134 was filed:\n  " + "\n  ".join(missing) + "\n"
        f"It saw {len(seen)} readers in all: {seen}"
    )

    assert not offenders, (
        "these reach INSIDE a castings/manifest.json record without "
        "establishing its shape first, so a manifest whose `castings` or "
        "`waves` is a string, a list of ints or a list of nulls meets `.get()` "
        "and raises AttributeError across the MCP boundary — a traceback "
        "naming no file, from doors whose whole error contract is a named "
        "refusal:\n  " + "\n  ".join(offenders) + "\n"
        "Call foundry_spawn's `_manifest_shape_error` (a refusing door) or "
        "`_manifest_shape_problem` (a tolerant reader) before indexing, or "
        "isinstance-guard the record you index. A caller that establishes the "
        "shape covers the helpers it hands the document to, so guarding the "
        "door usually clears everything below it. There is no allow-list here: "
        "the guarded readers pass on their own merits."
    )


def test_the_manifest_scan_catches_a_reader_planted_in_a_new_module(tmp_path) -> None:
    """The detector's own adjacent path — a NEW module, not the reported one.

    Four shapes at once, because the four are how the package actually got
    here: the top-rung `.get` guard that stops one rung short, the `_load_json`
    route, the `["k"]` spelling, and the `setdefault` spelling that no earlier
    scan in this suite recognised.
    """
    planted = _plant(
        tmp_path,
        "a_new_door.py",
        '''
def top_rung_only(fdir):
    manifest = _load_json(fdir / "castings" / "manifest.json")
    for c in manifest.get("castings", []):
        for f in c.get("key_files", []):
            print(f)

def subscript_spelling(fdir):
    manifest = json.loads((fdir / "castings" / "manifest.json").read_text())
    for w in manifest["waves"]:
        print(w["casting_ids"])

def setdefault_spelling(fdir):
    manifest = _load_json(fdir / "castings" / "manifest.json")
    for c in manifest.setdefault("castings", []):
        print(c.setdefault("id", None))
''',
    )
    _seen, offenders = _unestablished_record_indexes(planted)

    assert offenders == [
        "a_new_door.py#setdefault_spelling",
        "a_new_door.py#subscript_spelling",
        "a_new_door.py#top_rung_only",
    ]


def test_a_planted_reader_is_cleared_by_either_route_and_by_neither_alone(
    tmp_path,
) -> None:
    """Both establishment routes, and the proof each one is doing the work.

    The isinstance route is the one ``load_manifest_casting_ids`` passes on, so
    it is driven here on a planted reader too: with the guard the module is
    clean, and with the guard deleted — everything else identical — it is not.
    """
    guarded_by_validator = '''
def a_door(fdir):
    manifest = _load_json(fdir / "castings" / "manifest.json")
    if _manifest_shape_problem(manifest) is not None:
        return None
    for c in manifest.get("castings", []):
        print(c.get("id"))
'''
    guarded_by_isinstance = '''
def a_door(fdir):
    manifest = _load_json(fdir / "castings" / "manifest.json")
    for c in manifest.get("castings", []):
        if not isinstance(c, dict):
            continue
        print(c.get("id"))
'''
    guarded_by_nothing = guarded_by_isinstance.replace(
        "        if not isinstance(c, dict):\n            continue\n", ""
    )

    for label, source in (("v.py", guarded_by_validator), ("i.py", guarded_by_isinstance)):
        seen, offenders = _unestablished_record_indexes(_plant(tmp_path, label, source))
        # SEEN and cleared, not unseen — the two the anchor exists to separate.
        assert seen == [f"{label}#a_door"], (label, seen)
        assert offenders == [], (label, offenders)

    seen, offenders = _unestablished_record_indexes(
        _plant(tmp_path, "n.py", guarded_by_nothing)
    )
    assert seen == ["n.py#a_door"]
    assert offenders == ["n.py#a_door"]


def test_the_scan_follows_the_manifest_through_a_call(tmp_path) -> None:
    """``_fingerprint_inputs``' axis: a helper handed the parsed document.

    It indexes ``castings[].id`` off a manifest it never loaded, so a scan that
    starts taint only at the literal calls it clean while it raises on exactly
    the input the defect names. And when the CALLER establishes the shape, the
    helper is covered — which is why guarding a door usually clears the
    functions below it rather than requiring a guard in each.
    """
    unguarded_caller = '''
def helper(fdir, manifest):
    for c in manifest.get("castings", []):
        print(c.get("id"))

def a_door(fdir):
    manifest = _load_json(fdir / "castings" / "manifest.json")
    return helper(fdir, manifest)
'''
    _seen, offenders = _unestablished_record_indexes(
        _plant(tmp_path, "u.py", unguarded_caller)
    )
    assert offenders == ["u.py#helper"]

    guarded_caller = unguarded_caller.replace(
        "    return helper(fdir, manifest)",
        "    if _manifest_shape_error(manifest, fdir):\n"
        "        return None\n"
        "    return helper(fdir, manifest)",
    )
    _seen, offenders = _unestablished_record_indexes(
        _plant(tmp_path, "g.py", guarded_caller)
    )
    assert offenders == []


def test_the_scan_follows_a_manifest_path_a_helper_built(tmp_path) -> None:
    """``evidence.py``'s axis: the literal lives in a resolver, not the reader.

    Both of that module's manifest writers reach the document this way, so a
    scan following only the literal and the argument list reports the whole
    module clean while ``[1,2,3]`` raises AttributeError out of it.

    The propagation is narrow on purpose, and the second half proves it: a
    helper WITHOUT the literal must not launder taint, or one tainted call to a
    shared loader would make every other caller of it read as a manifest.
    """
    builds_the_path = '''
def resolve(root):
    return root / "castings" / "manifest.json"

def a_writer(root):
    manifest = json.loads(resolve(root).read_text(encoding="utf-8"))
    for c in manifest.setdefault("castings", []):
        print(c.get("id"))
'''
    _seen, offenders = _unestablished_record_indexes(
        _plant(tmp_path, "r.py", builds_the_path)
    )
    assert offenders == ["r.py#a_writer"]

    generic_loader = '''
def load(path):
    return json.loads(path.read_text(encoding="utf-8"))

def reads_the_manifest(root):
    manifest = load(root / "castings" / "manifest.json")
    return len(manifest.get("castings", []))

def reads_something_else(root):
    other = load(root / "state.json")
    for c in other.get("castings", []):
        print(c.get("id"))
'''
    # `load` is tainted by the first caller. If its RETURN laundered that taint,
    # `reads_something_else` — which reads a different file entirely — would be
    # reported as an unguarded manifest reader.
    _seen, offenders = _unestablished_record_indexes(
        _plant(tmp_path, "l.py", generic_loader)
    )
    assert offenders == []


def test_the_scan_follows_the_records_back_out_of_a_helper(tmp_path) -> None:
    """D-148: a helper handed the ACTUAL manifest, whose RETURN is the records.

    Neither of the two axes above. ``_fingerprint_inputs``' axis is the
    manifest going INTO a helper; ``evidence.py``'s axis is a helper building
    the PATH. This is the third: the document goes in and a rung comes back
    out, and the reader indexes what came back.

        def _rows(m):  return m.get("castings", [])
        for c in _rows(manifest): c.get("id")

    Both hops raise on the input the rule names — with ``castings`` a string,
    ``_rows`` returns the string, ``for c in <str>`` iterates characters and
    ``c.get("id")`` is AttributeError — and both read CLEAN before this. There
    is no live instance in the package; the class is filed because a seventh
    reader written in this shape would not fail on the day it was written.
    """
    one_hop = '''
def _rows(m):
    return m.get("castings", [])

def a_reader(fdir):
    manifest = _load_json(fdir / "castings" / "manifest.json")
    for c in _rows(manifest):
        print(c.get("id"))
'''
    seen, offenders = _unestablished_record_indexes(_plant(tmp_path, "h1.py", one_hop))
    assert offenders == ["h1.py#a_reader"], (seen, offenders)

    two_hops = '''
def _rows(m):
    return m.get("castings", [])

def _hop(m):
    return _rows(m)

def a_reader(fdir):
    manifest = _load_json(fdir / "castings" / "manifest.json")
    for c in _hop(manifest):
        print(c.get("id"))
'''
    seen, offenders = _unestablished_record_indexes(_plant(tmp_path, "h2.py", two_hops))
    assert offenders == ["h2.py#a_reader"], (seen, offenders)

    # ...and the guarded control: establishing the shape at the door clears the
    # reader through exactly the same chain, so what the rule forbids is the
    # unestablished index and not the helper.
    guarded = one_hop.replace(
        "    for c in _rows(manifest):",
        "    if _manifest_shape_problem(manifest) is not None:\n"
        "        return None\n"
        "    for c in _rows(manifest):",
    )
    _seen, offenders = _unestablished_record_indexes(_plant(tmp_path, "h3.py", guarded))
    assert offenders == []


def test_following_a_return_still_does_not_launder_a_shared_loader(tmp_path) -> None:
    """D-148's adjacent path, and the reason the rule is a DESCENT test.

    ``test_the_scan_follows_a_manifest_path_a_helper_built`` proves a shared
    loader must not poison its other callers — but its plant passes the path as
    an expression, so the loader is never seeded and the guard is never really
    exercised. Here the path is BOUND FIRST, so ``load`` genuinely carries
    manifest taint on its parameter, and the return rule still must not
    propagate: ``load`` hands back the same rung it was handed (the document
    root), so it does not descend, so ``reads_something_else`` — which reads
    ``state.json`` — stays clean.

    A rule that followed the return of any tainted-parameter helper would
    report it, and that is the failure this shape exists to make impossible.
    """
    source = '''
def load(path):
    return json.loads(path.read_text(encoding="utf-8"))

def reads_the_manifest(root):
    manifest_path = root / "castings" / "manifest.json"
    manifest = load(manifest_path)
    return len(manifest.get("castings", []))

def reads_something_else(root):
    other = load(root / "state.json")
    for c in other.get("castings", []):
        print(c.get("id"))
'''
    _seen, offenders = _unestablished_record_indexes(
        _plant(tmp_path, "seeded_loader.py", source)
    )
    assert offenders == [], (
        f"{offenders} — the shared loader laundered manifest taint onto a "
        f"caller that reads a different file entirely."
    )


def test_the_provider_module_passes_its_own_package_wide_rule() -> None:
    """foundry_spawn.py is a member of the rule it provides the validator for.

    D-132's fix routed all four of this module's manifest readers through one
    declaration; this asserts that from OUTSIDE the module, under the package
    rule, so the provider cannot quietly become the exception.

    ``== []`` on a real module is the vacuity shape D-142 named: it is green
    when the module is clean and green when the scan sees nothing in it, and
    those are opposite facts. The readers are named first.
    """
    seen, offenders = _unestablished_record_indexes(Path(fs.__file__))

    assert set(seen) == {
        f"{_cite(Path(fs.__file__))}#{name}"
        for name in (
            "_build_grind_cycle_context",
            "foundry_cast_wave",
            "foundry_spawn_teammate",
        )
    }, (
        f"the scan no longer recognises the same readers in its own provider "
        f"module: {seen}. These three functions index castings/manifest.json "
        f"records and must stay visible to the rule they supply the validator "
        f"for — losing them means the derivation went blind, not that the "
        f"module stopped reading the manifest."
    )
    assert offenders == []


def test_the_isinstance_route_clears_the_real_intent_coverage_reader() -> None:
    """The lead's explicit check: guarded BY THE RULE, not by an allow-list.

    TRACE ruled ``load_manifest_casting_ids`` out of D-134 because it carries
    ``isinstance(manifest_data.get("castings"), list)`` plus a per-element
    ``isinstance(c, dict)``. This asserts the scan reaches that same verdict on
    the real file, and that the verdict is the rule's rather than a name
    exemption — the function is not mentioned anywhere in this module.

    D-142's anchor is the whole difference between "cleared" and "not looked
    at" here, and this test is where the two are least distinguishable: an
    offender list that omits ``load_manifest_casting_ids`` is exactly what a
    blinded tracker produces. The reader must be SEEN and then cleared.
    """
    module = Path(foundry_mcp.__file__).resolve().parent / "scripts" / "validate_intent_coverage.py"
    assert module.is_file(), f"the reader D-134 clears has moved: {module}"

    source = module.read_text(encoding="utf-8")
    assert "def load_manifest_casting_ids" in source
    assert "isinstance(c, dict)" in source

    seen, offenders = _unestablished_record_indexes(module)
    assert any("load_manifest_casting_ids" in s for s in seen), (
        f"the scan did not recognise load_manifest_casting_ids as a manifest "
        f"reader at all, so 'it is clear of the rule' is a statement about the "
        f"scan's blindness rather than about the isinstance guard. Saw: {seen}"
    )
    assert not any("load_manifest_casting_ids" in o for o in offenders), offenders


# --------------------------------------------------------------------------- #
# D-142 — a rule that cannot say what it SAW cannot be asked whether it saw
# --------------------------------------------------------------------------- #
#
# Every structural rule in this file is an offender-only assertion over a
# DERIVED membership, and that pair has a silent failure mode: the assertion is
# green when the package is clean and green when the derivation has stopped
# recognising the package's spelling. The probe below is what tells them apart.
# It blinds the ONE primitive every member of the manifest rule calls and
# asserts each rule fails BY NAME — which is only possible because each scan
# now returns what it saw alongside what it objects to.
#
# The planted-module tests above prove the detector recognises a PLANTED
# spelling. They cannot prove it still recognises the REAL package's, and that
# is exactly the distinction that lapsed in GRIND-20 when the manifest reads
# moved behind a `(value, problem)` primitive: every plant still passed while
# every real chain started at a tuple target and the scan found nothing.


@pytest.fixture
def blind_taint(monkeypatch):
    """Make the manifest-taint derivation see nothing at all.

    ``_has_literal_taint`` is where taint enters — every literal decision in
    ``_taint_map``, ``_returning_taint`` and the establishment walk goes
    through it — so blinding it is blinding the whole scan, not one branch of
    it. That is the point of routing them through one primitive.
    """
    monkeypatch.setattr(
        sys.modules[__name__], "_has_literal_taint", lambda node: False
    )


@pytest.mark.parametrize(
    "rule",
    [
        test_the_manifest_scan_looks_in_both_shipped_source_trees,
        test_every_manifest_record_reader_in_the_package_establishes_the_shape,
        test_the_provider_module_passes_its_own_package_wide_rule,
        test_the_isinstance_route_clears_the_real_intent_coverage_reader,
    ],
    ids=lambda fn: fn.__name__,
)
def test_a_blinded_derivation_fails_every_manifest_rule_by_name(
    blind_taint, rule
) -> None:
    """All four rules go RED the moment the derivation stops seeing readers.

    Driven exactly as PROVE drove it: with the taint primitive replaced by one
    that recognises nothing, the scan reports zero real manifest readers. Every
    one of these four PASSED under that probe before the anchors landed —
    ``assert not offenders`` and ``== []`` are trivially satisfied by an empty
    scan, and the roots rule only counted MODULES, which a blind tracker does
    not change.

    The rules are called as functions rather than re-implemented, so what is
    asserted here is the shipped rule and not a copy that could drift from it.
    """
    assert not _package_record_readers()[0], (
        "the probe did not actually blind the derivation — it still sees "
        "readers, so the assertions below would prove nothing"
    )

    with pytest.raises(AssertionError) as raised:
        rule()

    assert "blind" in str(raised.value) or "recognis" in str(raised.value), (
        f"{rule.__name__} failed under the blind probe, but its message does "
        f"not say the derivation went blind — it will be read as a finding "
        f"about the package: {raised.value}"
    )


# --------------------------------------------------------------------------- #
# D-138's ADJACENT PATH — the same decode family at the two spawn doors
# --------------------------------------------------------------------------- #
#
# The defect was reported on Foundry-Liveness reading spawns.log. The doors
# below are a DIFFERENT CALLER of the same class on a different artifact: the
# pre-authored casting prompt, read with NO handler at all — not even the
# `except OSError` the reported site had. One non-UTF-8 byte in a file F0.5
# DECOMPOSE wrote raised UnicodeDecodeError out of Foundry-Spawn-Teammate and
# Foundry-Cast-Wave, one line after each had honoured its named-refusal
# contract twice for the manifest beside it.
#
# This is the half a fix bound to the reported site cannot reach, and the
# reason the binding is the module-wide scan above rather than a guard at :794.


def _corrupt_prompt(fdir: Path, casting_id: int) -> Path:
    """Make one casting's prompt file undecodable, leaving the rest healthy."""
    path = fdir / "castings" / f"casting-{casting_id}-prompt.md"
    with path.open("ab") as handle:
        handle.write(b"\xe9\n")
    return path


def test_the_single_door_refuses_an_undecodable_prompt(run_env) -> None:
    """Foundry-Spawn-Teammate names the file instead of raising."""
    project_root, fdir = run_env
    path = _corrupt_prompt(fdir, 1)

    result = fs.foundry_spawn_teammate(1, "cast", project_root)

    assert result["ok"] is False
    assert "casting-1-prompt.md" in result["error"]
    assert str(path) in result["error"]
    assert result["hint"].strip()


def test_the_bulk_door_refuses_an_undecodable_prompt(run_env) -> None:
    """The same input at the other door, with the same policy.

    D-132's property one rung down: two hand-written guards would drift and the
    lead would learn two different stories about one file.
    """
    project_root, fdir = run_env
    _corrupt_prompt(fdir, 1)

    single = fs.foundry_spawn_teammate(1, "cast", project_root)
    bulk = fs.foundry_cast_wave(1, "cast", project_root)

    assert bulk["ok"] is False
    assert single["error"] == bulk["error"]
    assert single["hint"] == bulk["hint"]


def test_the_refused_casting_is_not_logged_as_dispatched(run_env) -> None:
    """The refusing casting leaves no spawn record, because the read comes first.

    That ordering is the whole of what D-138 owed: a teammate whose prompt
    could not be read must not appear in ``spawns.log``, because
    ``foundry_liveness`` derives its roster from that log and would otherwise
    report a teammate that was never dispatched as silent past the threshold —
    a false alarm manufactured by the refusal itself.

    The rest of the wave is D-144, and is asserted by the tests below it: this
    one keeps its original subject so the two properties stay separable.
    """
    project_root, fdir = run_env
    _corrupt_prompt(fdir, 2)

    result = fs.foundry_cast_wave(1, "cast", project_root)

    assert result["ok"] is False
    assert "casting-2-prompt.md" in result["error"]
    assert 2 not in _logged_casting_ids(fdir)


# --------------------------------------------------------------------------- #
# D-144 — a spawn record is a claim that an agent exists
# --------------------------------------------------------------------------- #
#
# The bulk door wrote its dispatch records from INSIDE its per-casting loop, so
# a wave refused on its third casting had already claimed the first two. Driven
# through the real dispatch at d8215c5 with the third prompt undecodable /
# missing / empty, spawns.log held records for castings 1 and 2 every time, and
# Foundry-Liveness — aged past the 900s threshold — reported both as
# `no_ledger`, "dispatched 60 minutes ago and has written no progress ledger at
# all ... or it died before its first line".
#
# That is not a cosmetic audit-trail gap. CT-004/AC-021/OT-010 exist to tell a
# stalled agent from a slow one, and this reports a NON-EXISTENT agent as one
# that may have died — the worst available answer, since the lead's only
# recourse is to go looking for a teammate that was never spawned.
#
# The three refusal shapes are driven separately because they are three
# different branches of the same loop, and the pre-existing test above covers
# only the first. A fix bound to the decode branch would leave the other two.


def _logged_casting_ids(fdir: Path) -> list:
    """Every casting id in spawns.log, in the order the log records them."""
    raw, problem = foundry_state.read_text_file(fdir / "spawns.log")
    assert problem is None, problem
    return [
        json.loads(line)["casting_id"] for line in raw.splitlines() if line.strip()
    ]


def _break_third_prompt(fdir: Path, how: str) -> None:
    """Make casting 3's prompt unusable in one of the three ways the loop refuses."""
    path = fdir / "castings" / "casting-3-prompt.md"
    if how == "undecodable":
        with path.open("ab") as handle:
            handle.write(b"\xe9\n")
    elif how == "missing":
        path.unlink()
    elif how == "empty":
        path.write_text("   \n", encoding="utf-8")
    else:  # pragma: no cover - a shape nobody declared
        raise AssertionError(how)


@pytest.fixture
def wave_of_three(tmp_path, monkeypatch):
    """``run_env`` with a THIRD casting, so a refusal can land mid-wave.

    Two castings cannot express D-144: the refusal has to fall on a casting
    that is neither the first nor the last, or "records the ones before it" and
    "records nothing" are the same observation. ``state.json`` sits at F1 so
    ``TEAMMATE_DISPATCH_PHASES`` maps it to the ``cast`` records the wave
    writes and Foundry-Liveness reads them back.
    """
    monkeypatch.delenv("FOUNDRY_MODEL", raising=False)
    project_root = tmp_path
    fdir = project_root / "foundry-archive" / RUN_NAME
    fdir.mkdir(parents=True, exist_ok=True)
    _write_manifest(fdir, [1, 2, 3])
    (fdir / "state.json").write_text(
        json.dumps({"phase": "F1", "cycle": 0}), encoding="utf-8"
    )

    foundry_state.set_active_run(RUN_NAME)
    try:
        yield str(project_root), fdir
    finally:
        foundry_state.clear_active_run()


@pytest.mark.parametrize("how", ["undecodable", "missing", "empty"])
def test_a_refused_wave_records_no_dispatch_at_all(wave_of_three, how) -> None:
    """All three refusal branches, and none of them claims a teammate.

    The property is ZERO records, not "not the refused one": castings 1 and 2
    cleared their own checks, but the wave they belong to was never handed out,
    and a record for them is a claim about an agent that does not exist.
    """
    project_root, fdir = wave_of_three
    _break_third_prompt(fdir, how)

    result = fs.foundry_cast_wave(1, "cast", project_root)

    assert result["ok"] is False, result
    assert _logged_casting_ids(fdir) == [], (
        f"a wave refused on its third casting ({how}) still recorded "
        f"{_logged_casting_ids(fdir)} as dispatched"
    )


@pytest.mark.parametrize("how", ["undecodable", "missing", "empty"])
def test_liveness_reports_no_phantom_teammate_after_a_refused_wave(
    wave_of_three, how
) -> None:
    """The harm itself, at the far end of the loop D-144 was filed on.

    The refused wave must leave Foundry-Liveness with nothing to report — and
    the second half is what makes that mean something. Repairing the prompt and
    dispatching the same wave for real, aged an hour past the 900s threshold,
    puts all three castings on the roster as ``no_ledger``. So the empty answer
    above is the refusal being invisible, not the threshold being unreachable
    or the roster being broken: the identical run reports three phantoms' worth
    of rows the moment the dispatch actually happens.
    """
    project_root, fdir = wave_of_three
    _break_third_prompt(fdir, how)

    assert fs.foundry_cast_wave(1, "cast", project_root)["ok"] is False

    refused = fs.foundry_liveness(project_root=project_root)
    assert refused["ok"] is True, refused
    assert refused["agents"] == [], refused["agents"]
    assert refused["needs_attention"] == [], refused["needs_attention"]

    # The same run, the same threshold, the same reader — with a wave that was
    # really handed out.
    (fdir / "castings" / "casting-3-prompt.md").write_text(
        "# Casting 3\n\nBuild the thing.\n", encoding="utf-8"
    )
    assert fs.foundry_cast_wave(1, "cast", project_root)["ok"] is True
    _age_every_spawn_record(fdir, 3600)

    dispatched = fs.foundry_liveness(project_root=project_root)
    assert [row["agent"] for row in dispatched["agents"]] == [
        "casting-1",
        "casting-2",
        "casting-3",
    ]
    assert all(row["status"] == fs.STATUS_NO_LEDGER for row in dispatched["agents"])


def test_the_healthy_wave_still_records_every_casting(wave_of_three) -> None:
    """The control. Buffering must cost the working path nothing at all."""
    project_root, fdir = wave_of_three

    result = fs.foundry_cast_wave(1, "cast", project_root)

    assert result["ok"] is True, result
    assert [c["casting_id"] for c in result["castings"]] == [1, 2, 3]
    assert _logged_casting_ids(fdir) == [1, 2, 3]


def test_the_single_door_keeps_its_own_write_after_check_ordering(run_env) -> None:
    """D-144's ADJACENT PATH: the other door, on the same artifact.

    ``foundry_spawn_teammate`` is a different caller of ``spawns.log`` with its
    own refusal branches, and it is not covered by anything the bulk loop does.
    Both doors now write through ``_append_spawn_records``, so this asserts the
    single door's ordering directly: a refusal writes nothing, and the healthy
    call that follows writes exactly one record.
    """
    project_root, fdir = run_env
    _corrupt_prompt(fdir, 1)

    refused = fs.foundry_spawn_teammate(1, "cast", project_root)
    assert refused["ok"] is False
    assert _logged_casting_ids(fdir) == []

    # ...and a casting whose prompt is fine still lands exactly one record.
    assert fs.foundry_spawn_teammate(2, "cast", project_root)["ok"] is True
    assert _logged_casting_ids(fdir) == [2]

    missing = fs.foundry_spawn_teammate(99, "cast", project_root)
    assert missing["ok"] is False
    assert _logged_casting_ids(fdir) == [2]


def test_the_whole_wave_is_appended_in_one_write(wave_of_three, monkeypatch) -> None:
    """The wave is ONE append, which is what makes "all or none" mechanical.

    Three appends that happen to be adjacent would satisfy the refusal tests
    above and still let a reader land between the second and the third. The
    unit is the call, so the call is what is asserted.
    """
    project_root, fdir = wave_of_three
    calls: list[list[dict]] = []
    real = fs._append_spawn_records

    def recording(spawn_log, records):
        calls.append(list(records))
        return real(spawn_log, records)

    monkeypatch.setattr(fs, "_append_spawn_records", recording)
    assert fs.foundry_cast_wave(1, "cast", project_root)["ok"] is True

    assert len(calls) == 1, calls
    assert [record["casting_id"] for record in calls[0]] == [1, 2, 3]


def test_a_concurrent_liveness_read_never_sees_a_partial_wave(wave_of_three) -> None:
    """The flock half of the discipline, driven against a real lock holder.

    "Appended after every check" stops a REFUSAL leaving phantoms; it says
    nothing about a Foundry-Liveness call that arrives while the append is in
    flight. The writer holds ``LOCK_EX`` for the whole payload and the reader
    takes ``LOCK_SH`` around the read, so the reader waits rather than reading
    a half-written wave.

    Driven by holding the exclusive lock from this test, starting a reader in a
    thread, and asserting it is still blocked — then writing the whole wave and
    releasing. What the reader returns is therefore either everything or
    nothing, never the prefix that was on disk when it started.
    """
    _project_root, fdir = wave_of_three
    spawn_log = fdir / "spawns.log"
    spawn_log.write_text("", encoding="utf-8")

    wave = "".join(
        json.dumps({"casting_id": cid, "phase": "cast", "wave": 1}) + "\n"
        for cid in (1, 2, 3)
    )
    answer: list[tuple[str, str | None]] = []
    reading = threading.Event()

    def read_it() -> None:
        reading.set()
        answer.append(fs._read_spawn_log(spawn_log))

    with spawn_log.open("a", encoding="utf-8") as writer:
        fcntl.flock(writer.fileno(), fcntl.LOCK_EX)
        reader = threading.Thread(target=read_it)
        reader.start()
        assert reading.wait(5)
        # The reader is inside `_read_spawn_log` and blocked on the lock: the
        # log is empty on disk right now, and if it were not blocked it would
        # already have answered with that empty prefix.
        reader.join(0.5)
        assert reader.is_alive(), "the reader did not wait for the writer's lock"
        writer.write(wave)
        writer.flush()
        fcntl.flock(writer.fileno(), fcntl.LOCK_UN)

    reader.join(5)
    assert not reader.is_alive()
    raw, problem = answer[0]
    assert problem is None, problem
    assert [json.loads(line)["casting_id"] for line in raw.splitlines() if line.strip()] == [
        1,
        2,
        3,
    ]


def test_a_healthy_prompt_is_untouched_by_the_prompt_guard(run_env) -> None:
    """The control: the guard must cost the working path nothing."""
    project_root, _fdir = run_env

    single = fs.foundry_spawn_teammate(1, "cast", project_root)
    bulk = fs.foundry_cast_wave(1, "cast", project_root)

    assert single["ok"] is True and bulk["ok"] is True
    assert single["prompt"].startswith("# Casting 1")
    assert [c["casting_id"] for c in bulk["castings"]] == [1, 2]


def test_an_undecodable_baseline_marker_still_never_fails_a_spawn(grind_repo) -> None:
    """The tolerant member of the same family, driven.

    ``_build_grind_cycle_context``'s contract is that it never fails a spawn,
    so its answer to an unreadable ``.cast-baseline-sha`` is the same empty
    string an absent one produces — but it must reach that answer through the
    shared primitive rather than through its own ``except OSError``, which
    caught the missing file and not the undecodable one.
    """
    project_root, fdir = grind_repo
    with (fdir / ".cast-baseline-sha").open("wb") as handle:
        handle.write(b"\xe9\n")

    assert fs._build_grind_cycle_context(fdir, 1, project_root) == ""
    result = fs.foundry_spawn_teammate(1, "grind", project_root)
    assert result["ok"] is True
    assert "grind_cycle_context" not in result


def test_handler_coverage_is_decided_by_python_not_by_a_name_list() -> None:
    """D-137's structural half, asserted so this rule cannot repeat it.

    The previous shape of this scan compared handler SPELLINGS against a
    frozenset someone typed. That table is the escalated class living inside
    the guard written to make the class unrepresentable, and its failure mode
    is silent ACCEPTANCE: a handler is admitted by being on the list, and the
    fourteen sites D-137 found were all admitted that way.

    Every row below is a fact about Python's exception hierarchy rather than
    about this file, and each one is asserted twice — once as the hierarchy
    fact, once as the scan's verdict — so the scan cannot drift away from the
    language it claims to be asking.
    """
    cases = [
        ("except OSError:", False),
        ("except json.JSONDecodeError:", False),
        ("except (OSError, json.JSONDecodeError):", False),
        ("except UnicodeDecodeError:", False),  # covers the decode, not the OSError
        ("except (OSError, UnicodeDecodeError):", True),
        ("except (OSError, ValueError):", True),  # ValueError is the decode's parent
        ("except Exception:", True),
        ("except BaseException:", True),
        ("except:", True),
    ]
    # The hierarchy the verdicts above are claims about.
    assert issubclass(UnicodeDecodeError, ValueError)
    assert not issubclass(UnicodeDecodeError, OSError)
    assert not issubclass(UnicodeDecodeError, json.JSONDecodeError)

    for clause, expected_guarded in cases:
        source = f'''
import json
def a_reader(p):
    try:
        return p.read_text(encoding="utf-8")
    {clause}
        return ""
'''
        _every, offenders, unresolved, _unclassified = _text_reads(source, "planted.py")
        assert not unresolved, (clause, unresolved)
        assert (not offenders) is expected_guarded, (clause, offenders)


def test_membership_is_decided_by_python_not_by_the_shape_of_the_mode() -> None:
    """The read rule's PREDICATE, asserted the way its handler axis already is.

    D-137 fixed "what does this handler catch" by asking Python instead of
    comparing names; this is the same claim for "does this call decode". Every
    row is a fact about ``open`` and about the codecs registry, and each is
    asserted twice — once as the fact, once as the scan's verdict — so the
    scan cannot drift away from the language it claims to be asking.

    The rows that matter most are the two the module-scoped rule got wrong the
    moment it went package-wide: an append-only log write is not a decode site,
    and neither is a handle opened readable and then only flocked.
    """
    assert _stream_facts("a") == (True, False)  # text, not readable
    assert _stream_facts("rb") == (False, True)  # readable, not text
    assert _stream_facts("r") == (True, True)
    assert _stream_facts("a+") == (True, True)
    assert _stream_facts("not-a-mode") is None
    assert _errors_handler_raises("strict") is True
    assert _errors_handler_raises("replace") is False
    assert _errors_handler_raises("ignore") is False
    assert _attribute_decodes("read") is True
    assert _attribute_decodes("readline") is True
    assert _attribute_decodes("__iter__") is True
    assert _attribute_decodes("fileno") is False
    assert _attribute_decodes("close") is False
    assert _attribute_decodes("readable") is False  # a predicate, not a read

    source = '''
import fcntl
def appends(path, payload):
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(payload)

def locks_only(path):
    with open(path, "a+", encoding="utf-8") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        return None

def reads_bytes(path):
    with path.open("rb") as handle:
        return handle.read()

def substitutes(path):
    return path.read_text(encoding="utf-8", errors="replace")

def reads_the_handle(path):
    with open(path, "a+", encoding="utf-8") as handle:
        return handle.read()

def reads_text(path):
    return path.read_text(encoding="utf-8")
'''
    seen, offenders, unresolved, unclassified = _text_reads(source, "planted.py")
    assert not unresolved and not unclassified
    assert {site.rsplit(":", 1)[0].split("::")[1] for site in seen} == {
        "reads_the_handle",
        "reads_text",
    }, seen
    assert [site.rsplit(":", 1)[0] for site in offenders] == [
        "planted.py::reads_text",
        "planted.py::reads_the_handle",
    ]


def test_a_mode_this_scan_cannot_resolve_is_reported_not_skipped() -> None:
    """The REPORTING direction, on the axis that was silently deciding.

    A mode or an ``errors=`` value that is not a literal cannot be resolved
    from the AST. Treating it as "not a decode site" is the failure mode this
    whole family exists to close — a bare ``continue`` that comes back clean —
    so it is counted as a member AND named, and the rule fails until somebody
    has looked at it.
    """
    computed_mode = '''
def a_reader(path, mode):
    with open(path, mode) as handle:
        return handle.read()
'''
    seen, offenders, _unresolved, unclassified = _text_reads(computed_mode, "m.py")
    assert [s.rsplit(":", 1)[0] for s in seen] == ["m.py::a_reader"]
    assert [o.rsplit(":", 1)[0] for o in offenders] == ["m.py::a_reader"]
    assert len(unclassified) == 1 and "not a literal mode" in unclassified[0]

    computed_errors = '''
def a_reader(path, policy):
    return path.read_text(encoding="utf-8", errors=policy)
'''
    _seen, offenders, _unresolved, unclassified = _text_reads(computed_errors, "e.py")
    assert [o.rsplit(":", 1)[0] for o in offenders] == ["e.py::a_reader"]
    assert len(unclassified) == 1 and "not a literal handler" in unclassified[0]

    rejected_mode = '''
def a_reader(path):
    with open(path, "qz") as handle:
        return handle.read()
'''
    _seen, offenders, _unresolved, unclassified = _text_reads(rejected_mode, "q.py")
    assert [o.rsplit(":", 1)[0] for o in offenders] == ["q.py::a_reader"]
    assert len(unclassified) == 1 and "does not accept" in unclassified[0]


def test_the_positional_argument_indexes_come_from_pythons_signatures() -> None:
    """The three spellings disagree, and a hand-written index misreads two.

    ``open(p, "r")`` puts the mode at 1 and ``p.open("r")`` puts it at 0,
    because the bound method has already eaten ``self``. An index that was
    right for one spelling reads the FILENAME as the mode in the other, and
    ``_stream_facts`` would then be asked whether ``"some/path"`` decodes.
    """
    assert _positional_index("open", "mode") == 1
    assert _positional_index("Path.open", "mode") == 0
    assert _positional_index("open", "errors") == 4
    assert _positional_index("Path.open", "errors") == 3
    assert _positional_index("Path.read_text", "errors") == 1

    # ...and the scan reads each spelling at its own index, positionally.
    source = '''
def builtin_positional(p):
    with open(p, "rb") as h:
        return h.read()

def path_positional(p):
    with p.open("rb") as h:
        return h.read()
'''
    seen, _offenders, _unresolved, unclassified = _text_reads(source, "p.py")
    assert seen == [] and unclassified == [], (seen, unclassified)


def test_an_unresolvable_handler_name_is_reported_not_skipped() -> None:
    """A spelling the resolver cannot see announces itself.

    Silently treating it as "not covering" would be conservative and invisible;
    silently treating it as covering would be D-137 again. It covers nothing
    AND it is named, so the gap cannot widen without somebody being told.
    """
    source = '''
def a_reader(p):
    try:
        return p.read_text(encoding="utf-8")
    except SomeAliasNobodyImported:
        return ""
'''
    _every, offenders, unresolved, _unclassified = _text_reads(source, "planted.py")

    assert unresolved == ["SomeAliasNobodyImported"]
    assert [site.rsplit(":", 1)[0] for site in offenders] == ["planted.py::a_reader"]


# --------------------------------------------------------------------------- #
# Rendered derivations (evidence bodies)
# --------------------------------------------------------------------------- #


def test_render_scanned_root_derivation() -> None:
    """Print D-159's census: which roots exist, and what is outside them.

    Every path here is relative to ``plugins/foundry``, so the body is a claim
    about the tree and not about the machine that captured it (D-152/D-161).
    """
    plugin_root = _plugin_root()
    roots = _scanned_roots()
    unscanned = _unscanned_shipped_dirs()
    covered = {str(r.relative_to(plugin_root)) for r in roots}

    lines = [
        "SUBJECT   WHICH SOURCE TREES every package-wide rule in this module",
        "          judges -- walked out of plugins/foundry, not extended by",
        "          hand (D-159). Paths below are relative to plugins/foundry.",
        "",
        "NOT SHIPPED — pruned by name, read off the repository's own",
        f"          .gitignore rather than typed: {sorted(_unshipped_dir_names())}",
        "",
        "CENSUS    every directory holding a shipped .py:",
    ]
    for d in _shipped_python_dirs(plugin_root):
        rel = str(d.relative_to(plugin_root))
        if rel in covered:
            verdict = "SCANNED — a root"
        elif any(r in d.parents for r in roots):
            verdict = "scanned — inside a root"
        else:
            verdict = "UNSCANNED — declared below"
        lines.append(f"  {rel:<38} {verdict}")

    lines += [
        "",
        "ROOTS     position is a contract, not alphabetical luck:",
        f"  [0]   {str(roots[0].relative_to(plugin_root)):<32} "
        f"the importable package",
        f"  [-1]  {str(roots[-1].relative_to(plugin_root)):<32} "
        f"the hyphenated CLI tree, addressed",
        f"        {'':<32} positionally by test_orchestrator_gates.py",
        "",
        "UNSCANNED — reported, not absent. This is the line D-159 was filed",
        "          for: a shipped reader in a NEW top-level directory used to",
        "          be outside every rule AND outside every report, which reads",
        "          exactly like clean. The set is pinned BOTH ways.",
    ]
    for rel in unscanned:
        lines.append(f"  {rel}")
        lines.append(f"      declared: {_UNSCANNED_SHIPPED_DIRS[rel]}")

    print("\n".join(lines))
    assert unscanned == sorted(_UNSCANNED_SHIPPED_DIRS)
    assert roots[-1].name == "scripts"


def test_render_decode_read_derivation() -> None:
    """Print the D-138 read rule's subject, verdict, and its own adjacent path."""
    every, offenders, unresolved, unclassified = _package_text_reads()

    lines = [
        "SUBJECT   every decode site in both shipped source trees, membership",
        "          derived by rglob from the package's own __file__ (D-146)",
        # Repo-relative, the same rendering the sibling
        # `test_render_manifest_reader_derivation` below already uses on these
        # same roots. Printed absolute (D-152/D-161) this body pinned the
        # capture machine's home directory, so the shipped verifier -- which
        # always re-executes inside a worktree it creates, never at the capture
        # path -- refused the log with EVIDENCE_OUTPUT_MISMATCH everywhere on
        # earth except one laptop. The fix is to stop printing a fact about
        # where the repo is checked out, NOT to declare that fact volatile: a
        # path-shaped volatile pattern would have been ADMITTED by the
        # `absolute_path` grammar (D-156) and the log would have passed while
        # still pinning output no other machine produces.
        *(f"          {r.relative_to(REPO_ROOT)} " for r in _scanned_roots()),
        f"READS     {len(every)} decode sites found in {len(_scanned_modules())} modules",
        f"OFFENDERS {len(offenders)} not covered for the decode family",
        f"UNRESOLVED handler spellings: {unresolved or 'none'}",
        f"UNCLASSIFIED reads: {unclassified or 'none'}",
        "",
        "WHAT COUNTS AS COVERAGE — asked of Python, not of a list of names:",
        f"  issubclass(UnicodeDecodeError, ValueError)           = "
        f"{issubclass(UnicodeDecodeError, ValueError)}",
        f"  issubclass(UnicodeDecodeError, OSError)              = "
        f"{issubclass(UnicodeDecodeError, OSError)}",
        f"  issubclass(UnicodeDecodeError, json.JSONDecodeError) = "
        f"{issubclass(UnicodeDecodeError, json.JSONDecodeError)}",
        "",
        "A FRESH MEMBER PLANTED IN EACH SHAPE THIS MODULE HAS WORN:",
    ]
    planted = '''
import json
def no_handler(p):
    return p.read_text(encoding="utf-8")
def only_oserror(p):
    try:
        return p.read_text(encoding="utf-8")
    except OSError:
        return ""
def only_json(p):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
def path_open(p):
    with p.open("r", encoding="utf-8") as h:
        return h.read()
def read_in_the_handler(p, q):
    try:
        return p.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return q.read_text(encoding="utf-8")
def properly_guarded(p):
    try:
        return p.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""
'''
    tree = ast.parse(planted)
    planted_fns = [
        n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)
    ]
    _e, planted_off, _u, _c = _text_reads(planted, "planted.py")
    reported = {site.rsplit(":", 1)[0].split("::")[1] for site in planted_off}
    for name in planted_fns:
        lines.append(f"  {'REPORTED' if name in reported else 'clean   '}  {name}")
    lines.append(
        "  (properly_guarded is clean on its own merits — there is no list to join)"
    )
    lines.append("")
    lines.append("VERDICT — reads a file's text with the decode family uncovered:")
    lines.extend(f"  {site}" for site in _symbol_cites(offenders))

    print("\n".join(lines))
    # What this asserts is the DERIVATION, not the package: the resolver
    # resolved every handler spelling and classified every read it found, and
    # the plant table shows each shape this module has worn being reported.
    # The offender list is the rule's finding and is asserted by
    # `test_no_read_in_this_module_can_raise_a_decode_error`, which owns it.
    assert not unresolved and not unclassified
    assert every, "the scan recognised no decode site anywhere in either tree"
    assert reported == {
        "no_handler",
        "only_oserror",
        "only_json",
        "path_open",
        "read_in_the_handler",
    }, reported


#: The five shapes D-148 was driven on, as (label, source, expected verdict).
#: The first two are the shapes the scan already got right and are here as the
#: controls that make the middle two mean something; the last is the laundering
#: guard, written so the shared loader is GENUINELY seeded.
_RETURN_TAINT_SHAPES = [
    (
        "direct           for c in manifest.get('castings')",
        '''
def a(fdir):
    m = _load_json(fdir / "castings" / "manifest.json")
    for c in m.get("castings", []):
        print(c.get("id"))
''',
        ["a"],
    ),
    (
        "guarded control  per-element isinstance",
        '''
def a(fdir):
    m = _load_json(fdir / "castings" / "manifest.json")
    for c in m.get("castings", []):
        if not isinstance(c, dict):
            continue
        print(c.get("id"))
''',
        [],
    ),
    (
        "one hop          for c in _rows(manifest)",
        '''
def _rows(m):
    return m.get("castings", [])

def a(fdir):
    m = _load_json(fdir / "castings" / "manifest.json")
    for c in _rows(m):
        print(c.get("id"))
''',
        ["a"],
    ),
    (
        "two hops         _hop -> _rows",
        '''
def _rows(m):
    return m.get("castings", [])

def _hop(m):
    return _rows(m)

def a(fdir):
    m = _load_json(fdir / "castings" / "manifest.json")
    for c in _hop(m):
        print(c.get("id"))
''',
        ["a"],
    ),
    (
        "shared loader    load() reused for state.json, path bound first",
        '''
def load(p):
    return json.loads(p.read_text(encoding="utf-8"))

def reads_manifest(root):
    mp = root / "castings" / "manifest.json"
    m = load(mp)
    return len(m.get("castings", []))

def reads_state(root):
    o = load(root / "state.json")
    for c in o.get("castings", []):
        print(c.get("id"))
''',
        [],
    ),
]


def test_render_return_taint_derivation(tmp_path) -> None:
    """Print D-148's third axis: the records coming back OUT of a helper.

    Every row is driven through the shipped scan and asserted, so the table is
    a claim this suite holds rather than a picture printed beside it.
    """
    lines = [
        "SUBJECT   a helper handed the ACTUAL manifest, whose RETURN is the records",
        "",
        "  the shape                                                "
        "seen            verdict",
        "  " + "-" * 56 + " " + "-" * 14 + "  " + "-" * 8,
    ]
    for label, source, expected in _RETURN_TAINT_SHAPES:
        planted = _plant(tmp_path, "planted.py", source)
        seen, offenders = _unestablished_record_indexes(planted)
        seen_names = [s.split("#")[1] for s in seen]
        offender_names = [o.split("#")[1] for o in offenders]
        lines.append(
            "  %-56s %-14s  %s"
            % (
                label,
                ",".join(seen_names) or "none",
                "REPORTED " + ",".join(offender_names) if offender_names else "clean",
            )
        )
        assert offender_names == expected, (label, seen, offenders)

    lines += [
        "",
        "THE RULE IS A DESCENT TEST, not 'follow every seeded helper':",
        "  _rows was handed the document ROOT and returns `castings`, which is",
        "  strictly below it — so it propagates. `load` returns the same rung it",
        "  was handed, so it does not, and its other caller stays clean even",
        "  though the path was bound to a name first and the loader really is",
        "  carrying manifest taint on its parameter.",
        "",
        "SEEN is D-142's anchor on the same rows: the guarded control is SEEN",
        "  and cleared, not unseen — the distinction an offender-only scan",
        "  cannot make, and the one it goes blind through.",
    ]
    print("\n".join(lines))


def test_render_manifest_reader_derivation() -> None:
    """Print the D-134 package rule's three derived axes and its verdict."""
    roots = _scanned_roots()
    modules = _scanned_modules()
    seen, offenders = _package_record_readers()

    print(
        "\n".join(
            [
                "AXIS 1 — WHICH FILES (rglob, both shipped source trees):",
                *(f"  {r.relative_to(REPO_ROOT)}" for r in roots),
                f"  {len(modules)} modules",
                "",
                "AXIS 2 — WHICH RUNGS (walked out of _MANIFEST_SHAPE, not typed):",
                f"  {sorted(_record_rungs())}",
                "  castings[].key_files and waves[].casting_ids are lists of",
                "  NON-records (element shape None), so they are correctly not rungs",
                "",
                "AXIS 3 — WHICH READERS (taint from the literal, across calls,",
                "         through path-building helpers, and back OUT of a helper's",
                "         RETURN when it descends below what it was handed [D-148];",
                "         allow-list is EMPTY):",
                f"  shared validators, read off foundry_spawn: "
                f"{sorted(_shape_validator_names())}",
                "",
                "SEEN — the readers the derivation actually RECOGNISED. Without",
                "       this line the VERDICT below is green whether the package is",
                "       clean or the tracker has gone blind [D-142]:",
                *(f"  {s}" for s in seen),
                "",
                "VERDICT — reaches inside a manifest record with nobody having",
                "          established its shape:",
                *(f"  {o}" for o in offenders or ["none"]),
                "",
                "NOT LISTED, and not by exemption — these pass the same rule:",
                "  foundry_spawn.py                    (routes through the validator)",
                "  scripts/validate_intent_coverage.py "
                "(isinstance-guards the rung it indexes)",
            ]
        )
    )
    # The render is evidence, so it asserts what it claims: every reader the
    # defect was filed on is still recognised, and the package is still clean.
    assert set(_D134_FILED_READERS) <= set(seen), sorted(
        set(_D134_FILED_READERS) - set(seen)
    )
    assert not offenders, offenders
