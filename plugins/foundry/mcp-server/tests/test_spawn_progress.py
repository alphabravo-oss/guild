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
import importlib
import json
import os
import re
import shutil
import subprocess
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


def _expr_path(expr: ast.AST, tainted: dict[str, str]) -> str | None:
    """The manifest key path ``expr`` evaluates to, or None.

    Indexing first, bare reference second, so ``manifest.get("waves") or []``
    resolves to ``waves`` rather than to the manifest root. The ``or []`` /
    ``if isinstance(...) else []`` idioms this module uses are handled by
    walking the subtree instead of matching a statement shape.
    """
    for node in ast.walk(expr):
        hit = _indexed(node, tainted)
        if hit is not None:
            base, key = hit
            return f"{base}.{key}" if base else key
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
                if any(
                    isinstance(n, ast.Constant) and n.value == "manifest.json"
                    for n in ast.walk(node.value)
                ):
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
                path = _expr_path(node.value, tainted)
                if path is None:
                    path = _returned_path(node.value, returning)
                if path is not None:
                    for name in names:
                        tainted.setdefault(name, path)
            elif isinstance(node, (ast.For, ast.AsyncFor, ast.comprehension)):
                path = _expr_path(node.iter, tainted)
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
# D-138 — no read in this module can raise a decode error
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


def _reader_namespace(tree: ast.Module, module_obj=None) -> dict:
    """The names a scanned module can see, for resolving exception spellings.

    Derived in layers, none of them typed: builtins, the real module object
    when there is one, and the modules the source's own ``import X [as Y]``
    statements name -- which is what resolves the dotted
    ``json.JSONDecodeError`` spelling in a planted source that is not
    importable at all.
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
) -> tuple[list[str], list[str], list[str]]:
    """``(all_reads, unguarded_reads, unresolvable_handler_spellings)``."""
    every: list[str] = []
    offenders: list[str] = []
    unresolved: list[str] = []
    tree = ast.parse(source)

    def visit(node: ast.AST, fn: str, guarded: bool) -> None:
        if not _is_text_read(node):
            return
        site = f"{module}::{fn}:{node.lineno}"
        every.append(site)
        if not guarded:
            offenders.append(site)

    _walk_decode_guarded(
        tree, "<module>", False, visit, _reader_namespace(tree, module_obj), unresolved
    )
    return sorted(set(every)), sorted(set(offenders)), sorted(set(unresolved))


def test_no_read_in_this_module_can_raise_a_decode_error() -> None:
    """D-138, as a property of the whole module rather than of one read.

    The subject is located through the module's own import, so a rename or a
    move cannot silently empty the scan.
    """
    source = Path(fs.__file__).read_text(encoding="utf-8")
    every, offenders, unresolved = _text_reads(source, "foundry_spawn.py", fs)

    assert not unresolved, (
        f"{unresolved} name exceptions this resolver could not turn into "
        f"classes, so it cannot say what they catch and has counted them as "
        f"catching nothing. Fix the resolver rather than the handler."
    )

    # The scan must SEE something, or the assertion below passes vacuously --
    # the failure mode of every derived-membership check, and the one that
    # actually fired here when the manifest reads moved behind a primitive.
    assert every, (
        "the scan found no file read at all in foundry_spawn.py. Either every "
        "read now routes through a primitive (in which case delete this "
        "assertion deliberately, not by accident) or _is_text_read has stopped "
        "recognising the module's spelling and is testing nothing."
    )

    assert not offenders, (
        f"{offenders} read a file's text without handling the decode failure, "
        f"so one non-UTF-8 byte in an artifact FOUNDRY ITSELF WROTE raises "
        f"UnicodeDecodeError across the MCP boundary as a traceback naming no "
        f"file. `except OSError` and `except json.JSONDecodeError` do not "
        f"catch it -- UnicodeDecodeError is a ValueError. Route the read "
        f"through foundry_state.read_text_file / read_json and return "
        f"document_refusal (one file) or _unreadable_artifacts_refusal "
        f"(several). There is no allow-list to add yourself to: the guarded "
        f"primitive passes this rule on its own merits."
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
    _every, offenders, _unresolved = _text_reads(source, "planted.py")
    assert [site.rsplit(":", 1)[0] for site in offenders] == [
        "planted.py::no_handler",
        "planted.py::only_json",
        "planted.py::only_oserror",
        "planted.py::path_open",
    ]


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
    _every, offenders, _unresolved = _text_reads(source, "planted.py")
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


def _scanned_roots() -> list[Path]:
    """The two source trees a manifest reader can live in.

    Both derived from the installed package's own location rather than typed,
    and both asserted to exist by the test below -- a scan whose root has moved
    finds nothing and passes, which is the failure mode these rules exist to
    avoid.

    ``plugins/foundry/scripts`` is here because it is shipped source that reads
    run artifacts (``measure-run.py``, ``migrate-archive.py``,
    ``validate-intent-coverage.py``) and is NOT importable as part of the
    package, so a package-only rglob would leave it permanently outside every
    derived rule in the suite -- D-066's boundary mistake, one directory over.
    """
    pkg = Path(foundry_mcp.__file__).resolve().parent
    # .../plugins/foundry/mcp-server/src/foundry_mcp -> .../plugins/foundry
    return [pkg, pkg.parents[2] / "scripts"]


def _scanned_modules() -> list[Path]:
    """Every ``.py`` under either root, membership derived on both axes."""
    return sorted(p for root in _scanned_roots() for p in root.rglob("*.py"))


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


def _has_literal_taint(fn: ast.AST) -> bool:
    """True when the manifest enters this function through its own literal."""
    return any(
        isinstance(n, ast.Constant) and n.value == "manifest.json" for n in ast.walk(fn)
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
            if any(
                isinstance(n, ast.Constant) and n.value == "manifest.json"
                for n in ast.walk(node.value)
            ):
                returning.setdefault(name, "")
                continue
            path = _expr_path(node.value, tainted)
            if path is not None:
                returning.setdefault(name, path)
    return returning


def _param_taint(
    funcs: dict[str, ast.AST], returning: dict[str, str] | None = None
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
            tainted = _taint_map(fn, seeds.get(caller_name), returning)
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
                    path = _expr_path(arg, tainted)
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


def _unestablished_record_indexes(path: Path) -> list[str]:
    """Every index of a manifest RECORD rung whose shape nobody established.

    ``guarded`` here is not a name on a list; it is one of three facts about
    the code: the function calls a shared validator, or it isinstance-guards
    the very name it indexes, or every caller that handed it the document has
    already done one of those.
    """
    source = path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(source)
    except SyntaxError:  # pragma: no cover - a source file that will not parse
        return []
    funcs = _functions_by_name(tree)
    if not funcs:
        return []

    rungs = _record_rungs()
    validators = _shape_validator_names()
    returning = _returning_taint(funcs)
    seeds, callers = _param_taint(funcs, returning)

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

    sites: dict[str, list[int]] = {}
    for name, fn in funcs.items():
        tainted = _taint_map(fn, seeds.get(name), returning)
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
            if name in established or base.id in typed_names:
                continue
            sites.setdefault(name, []).append(node.lineno)

    # Cited as `path#Symbol`, with no `:line` and no site count: both drift
    # under any edit to the cited file, and a citation that changes when the
    # file above it changes is a citation that goes stale on its own.
    return [f"{_cite(path)}#{name}" for name in sorted(sites)]


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


def test_the_manifest_scan_looks_in_both_shipped_source_trees() -> None:
    """Neither root may be missing, and neither may be silently empty.

    Derived membership is worth nothing if the derivation runs over an empty
    set, and a root that has MOVED produces exactly that: a scan that finds no
    offenders and a test that passes for the wrong reason. ``os.walk`` is the
    independent oracle rather than a second ``rglob``, so a future narrowing of
    the discovery to a directory literal is caught by disagreement.
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
    """
    offenders = [o for p in _scanned_modules() for o in _unestablished_record_indexes(p)]

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
    offenders = _unestablished_record_indexes(planted)

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

    assert _unestablished_record_indexes(_plant(tmp_path, "v.py", guarded_by_validator)) == []
    assert _unestablished_record_indexes(_plant(tmp_path, "i.py", guarded_by_isinstance)) == []
    assert _unestablished_record_indexes(
        _plant(tmp_path, "n.py", guarded_by_nothing)
    ) == ["n.py#a_door"]


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
    assert _unestablished_record_indexes(
        _plant(tmp_path, "u.py", unguarded_caller)
    ) == ["u.py#helper"]

    guarded_caller = unguarded_caller.replace(
        "    return helper(fdir, manifest)",
        "    if _manifest_shape_error(manifest, fdir):\n"
        "        return None\n"
        "    return helper(fdir, manifest)",
    )
    assert _unestablished_record_indexes(_plant(tmp_path, "g.py", guarded_caller)) == []


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
    assert _unestablished_record_indexes(
        _plant(tmp_path, "r.py", builds_the_path)
    ) == ["r.py#a_writer"]

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
    assert _unestablished_record_indexes(_plant(tmp_path, "l.py", generic_loader)) == []


def test_the_provider_module_passes_its_own_package_wide_rule() -> None:
    """foundry_spawn.py is a member of the rule it provides the validator for.

    D-132's fix routed all four of this module's manifest readers through one
    declaration; this asserts that from OUTSIDE the module, under the package
    rule, so the provider cannot quietly become the exception.
    """
    assert _unestablished_record_indexes(Path(fs.__file__)) == []


def test_the_isinstance_route_clears_the_real_intent_coverage_reader() -> None:
    """The lead's explicit check: guarded BY THE RULE, not by an allow-list.

    TRACE ruled ``load_manifest_casting_ids`` out of D-134 because it carries
    ``isinstance(manifest_data.get("castings"), list)`` plus a per-element
    ``isinstance(c, dict)``. This asserts the scan reaches that same verdict on
    the real file, and that the verdict is the rule's rather than a name
    exemption — the function is not mentioned anywhere in this module.
    """
    module = Path(foundry_mcp.__file__).resolve().parent / "scripts" / "validate_intent_coverage.py"
    assert module.is_file(), f"the reader D-134 clears has moved: {module}"

    source = module.read_text(encoding="utf-8")
    assert "def load_manifest_casting_ids" in source
    assert "isinstance(c, dict)" in source

    offenders = _unestablished_record_indexes(module)
    assert not any("load_manifest_casting_ids" in o for o in offenders), offenders


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

    That ordering is the whole of what this fix owes: a teammate whose prompt
    could not be read must not appear in ``spawns.log``, because
    ``foundry_liveness`` derives its roster from that log and would otherwise
    report a teammate that was never dispatched as silent past the threshold —
    a false alarm manufactured by the refusal itself.

    NOT asserted here, and deliberately: the bulk loop logs each casting as it
    goes, so castings EARLIER in the wave than the refusal are already logged
    when it fires. That is pre-existing and shared with the
    prompt-does-not-exist and prompt-is-empty branches beside this one — a
    partial-dispatch record on any late refusal, not a decode problem — so
    fixing it here would be remodelling during a bug fix. It is reported to the
    lead rather than silently widened into this change.
    """
    project_root, fdir = run_env
    _corrupt_prompt(fdir, 2)

    result = fs.foundry_cast_wave(1, "cast", project_root)

    assert result["ok"] is False
    assert "casting-2-prompt.md" in result["error"]

    logged = [
        json.loads(line)["casting_id"]
        for line in (fdir / "spawns.log").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert 2 not in logged, logged


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
        _every, offenders, unresolved = _text_reads(source, "planted.py")
        assert not unresolved, (clause, unresolved)
        assert (not offenders) is expected_guarded, (clause, offenders)


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
    _every, offenders, unresolved = _text_reads(source, "planted.py")

    assert unresolved == ["SomeAliasNobodyImported"]
    assert [site.rsplit(":", 1)[0] for site in offenders] == ["planted.py::a_reader"]


# --------------------------------------------------------------------------- #
# Rendered derivations (evidence bodies)
# --------------------------------------------------------------------------- #


def test_render_decode_read_derivation() -> None:
    """Print the D-138 read rule's subject, verdict, and its own adjacent path."""
    source = Path(fs.__file__).read_text(encoding="utf-8")
    every, offenders, unresolved = _text_reads(source, "foundry_spawn.py", fs)

    lines = [
        "SUBJECT   every read in foundry_spawn.py, located by the module's own import",
        f"READS     {len(every)} file reads found",
        f"OFFENDERS {len(offenders)} not covered for the decode family",
        f"UNRESOLVED handler spellings: {unresolved or 'none'}",
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
    _e, planted_off, _u = _text_reads(planted, "planted.py")
    reported = {site.rsplit(":", 1)[0].split("::")[1] for site in planted_off}
    for name in planted_fns:
        lines.append(f"  {'REPORTED' if name in reported else 'clean   '}  {name}")
    lines.append(
        "  (properly_guarded is clean on its own merits — there is no list to join)"
    )

    print("\n".join(lines))
    assert not offenders and not unresolved


def test_render_manifest_reader_derivation() -> None:
    """Print the D-134 package rule's three derived axes and its verdict."""
    roots = _scanned_roots()
    modules = _scanned_modules()
    offenders = [o for p in modules for o in _unestablished_record_indexes(p)]

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
                "AXIS 3 — WHICH READERS (taint from the literal, across calls and",
                "         through path-building helpers; allow-list is EMPTY):",
                f"  shared validators, read off foundry_spawn: "
                f"{sorted(_shape_validator_names())}",
                "",
                "VERDICT — reaches inside a manifest record with nobody having",
                "          established its shape:",
                *(f"  {o}" for o in offenders),
                "",
                "NOT LISTED, and not by exemption — these pass the same rule:",
                "  foundry_spawn.py                    (routes through the validator)",
                "  scripts/validate_intent_coverage.py "
                "(isinstance-guards the rung it indexes)",
            ]
        )
    )
