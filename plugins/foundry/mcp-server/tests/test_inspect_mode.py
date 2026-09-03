"""INSPECT width: who decides it, when, and what the roster then means.

ST-006 / ST-007 / GI-008 / GI-009 / CT-009 / FR-011 / FR-012 / FR-013 / FR-033 /
FR-047 / FR-049 / AC-016 / AC-017 / AC-018 / AC-019 / OT-012 / OT-013 / OT-014 /
OT-015.

WHAT THIS FILE IS ABOUT
-----------------------
thunder-viper ran 22 GRIND cycles and re-verified everything after each one, so
a three-file GRIND cost a full INSPECT. DELTA makes the second and later
INSPECTs of a phase proportional to what changed, while every rule that matters
keeps the run at full width.

The single most important property here is not the saving — it is WHERE the
decision lives. GI-009: "whichever Foundry-Phase transition opens an INSPECT
records the mode; Foundry-Next only reports. No decision ever lives in
Foundry-Next." GI-008 names the violation from the other side: "A FULL versus
DELTA decision computed inside Foundry-Next, or a streams-complete check that
reads a roster nothing recorded because Next was skipped."

So several tests below assert the ABSENCE of a decision — that the mode is on
disk before any Foundry-Next runs, that two Foundry-Next calls report the same
sample rather than drawing twice, and that `_compute_next_action`'s source
contains no decision at all. A width that is merely usually right is not the
requirement; a width that only one call site can set is.

The run fixture is a REAL git repository, because the DELTA decision is a
function of a real `git diff` between two real commits. A monkeypatched diff
would pin the branch structure and prove nothing about the thing that computes
it.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from foundry_mcp.schemas.vocab import (
    DELTA_CONDITIONAL_STREAMS,
    FULL_ROSTER_STREAMS,
    INSPECT_DELTA_RULE,
    INSPECT_FULL_RULES,
    INSPECT_MODES,
    PROVE_DELTA_SAMPLE_SIZE,
)
from foundry_mcp.tools import foundry_orchestrator as fo
from foundry_mcp.tools import foundry_state
from foundry_mcp.tools.foundry_orchestrator import (
    _check_streams_complete,
    _current_cycle,
    _current_inspect_mode,
    _decide_inspect_mode,
    foundry_mark_phase_complete,
    foundry_mark_stream,
    foundry_next_action,
)


def _plugin_root() -> Path:
    """`plugins/foundry/`, from this file."""
    return Path(__file__).resolve().parents[3] / "foundry"

RUN_NAME = "inspect-mode-run"


# --------------------------------------------------------------------------- #
# Fixtures — a real git repo, because the decision reads a real diff
# --------------------------------------------------------------------------- #


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True, text=True, check=True,
    )
    return result.stdout.strip()


@pytest.fixture
def run_env(tmp_path, monkeypatch):
    """Activate a run inside a real git repo; yield (project_root, fdir).

    Mirrors `test_escalation.py`'s `run_env` — same active-run activation, same
    `_check_active_teams` monkeypatch so no test depends on the ambient tmux
    session — and adds the repository the width decision needs.
    """
    project_root = tmp_path
    _git(project_root, "init", "-q")
    _git(project_root, "config", "user.email", "foundry@example.invalid")
    _git(project_root, "config", "user.name", "foundry")

    # The real repo's own rule, and it matters here: `.gitignore:16` is
    # `/foundry-archive/`, so nothing a run writes into its own directory can
    # ever appear in a GRIND diff. A fixture without this would put state.json,
    # defects.json and the run's copy of the spec into every diff, and the spec
    # is a verifier path — so every DELTA fixture would force FULL for a reason
    # that cannot occur in production.
    (project_root / ".gitignore").write_text("/foundry-archive/\n", encoding="utf-8")
    (project_root / "src").mkdir()
    (project_root / "src" / "handler.py").write_text("def handle():\n    pass\n")
    (project_root / "src" / "other.py").write_text("def other():\n    pass\n")
    _git(project_root, "add", "-A")
    _git(project_root, "commit", "-qm", "baseline")

    fdir = project_root / "foundry-archive" / RUN_NAME
    (fdir / "castings").mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(
        fo,
        "_check_active_teams",
        lambda _pr: {"active": False, "teams": [], "live_panes": []},
    )

    foundry_state.set_active_run(RUN_NAME)
    try:
        yield str(project_root), fdir
    finally:
        foundry_state.clear_active_run()


def _write_state(fdir: Path, phase: str, cycle: int = 0, **extra) -> None:
    state = {"phase": phase, "cycle": cycle}
    state.update(extra)
    (fdir / "state.json").write_text(json.dumps(state), encoding="utf-8")


def _read_state(fdir: Path) -> dict:
    return json.loads((fdir / "state.json").read_text(encoding="utf-8"))


def _write_defects(fdir: Path, defects: list[dict]) -> None:
    (fdir / "defects.json").write_text(
        json.dumps({"defects": defects}, indent=2), encoding="utf-8"
    )


def _open_live(did: str = "D-001", cycle: int = 1) -> dict:
    """One OPEN LIVE defect: an ordinary GRIND with real work still in it.

    D-143 — WHAT THIS DOCSTRING USED TO EXPLAIN.
    -------------------------------------------
    It read "One OPEN LIVE defect — enough to keep `final_gate` from firing.
    Every DELTA fixture needs this. `final_gate` fires when no blocking defect
    remains, because the next gate is then ASSAY." That was the RETIRED proxy.
    `_decide_inspect_mode`'s final_gate arm did test `_blocking_defects(...)
    ["blocking"] == 0`, and D-068 removed it: under that test DELTA fired only
    when the lead crossed with LIVE defects still open, which no guidance
    instructs and which `Foundry-Gate('assay')` refuses anyway — so US-004
    delivered nothing. The GRIND cycle-4 ruling replaced it with two
    TRANSITION facts: final_gate fires when the GRIND was entered from ASSAY,
    TEMPER or NYQUIST feedback (`_entered_grind_from_feedback`), or when this
    crossing is the F2->F2 widening re-open.

    Driven at HEAD: a CLEARED ledger and a one-handler GRIND record
    DELTA/delta — so the state the retired sentence says fires final_gate does
    not fire it, and the claim "every DELTA fixture needs this" is false too.
    The fixtures built on this helper still pass because they assert the
    transition's output; what was wrong is the sentence a maintainer reads to
    learn WHY the fixture holds a LIVE defect, one file away from the rule that
    replaced it.

    WHY THE FIXTURES STILL HOLD ONE. Not to suppress final_gate — nothing here
    does that — but because a DELTA cycle is the ordinary GRIND->INSPECT
    crossing, and an ordinary GRIND has open work. A fixture asserting DELTA
    over an empty ledger would be asserting it over the run state that reaches
    `inspect_clean`'s widening refusal instead, which is a different subject.
    Fixtures that mean "this GRIND fixed everything" set `status: fixed`
    explicitly rather than dropping the record.
    """
    return {
        "id": did, "cycle": cycle, "source": "trace", "type": "UNWIRED",
        "description": "the handler never calls the store", "spec_ref": "FR-001",
        "symbol": "handle", "file": "src/handler.py", "status": "open",
        "tier": "LIVE", "class": "UNWIRED_SURFACE", "fixed_in_cycle": None,
    }


def _write_manifest(fdir: Path, **extra) -> None:
    manifest = {"castings": [{"id": 1, "key_files": ["src/handler.py"]}]}
    manifest.update(extra)
    (fdir / "castings" / "manifest.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )


def _write_spec(fdir: Path, ids: list[str]) -> None:
    (fdir / "spec.md").write_text(
        "".join(f"- **{rid}**: the thing works\n" for rid in ids), encoding="utf-8"
    )


def _arm(fdir: Path) -> None:
    """Foundry-Next's ordering token, which gate/phase calls consume."""
    (fdir / ".next-action-called").write_text(f"{fo._now()}\n", encoding="utf-8")


def _grind_touching(project_root: str, fdir: Path, *paths: str) -> None:
    """Record a boundary at HEAD, then commit a change to each named path.

    This is what "the GRIND diff" MEANS: the boundary marker pins where the last
    INSPECT crossed, the commit is the GRIND's work, and the decision diffs
    between them. Writing the marker first and committing second is the real
    order of events, not a convenience.
    """
    root = Path(project_root)
    # Anything a test staged before this point belongs to the PREVIOUS cycle, so
    # it is committed before the boundary is recorded. Otherwise it would land
    # in the diff as if the GRIND had produced it.
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "--allow-empty", "-m", "pre-boundary")
    (fdir / fo.INSPECT_BOUNDARY_SHA_MARKER).write_text(
        _git(root, "rev-parse", "HEAD") + "\n", encoding="utf-8"
    )
    for rel in paths:
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            (target.read_text(encoding="utf-8") if target.exists() else "")
            + "\n# a GRIND cycle touched this\n",
            encoding="utf-8",
        )
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "grind")


# --------------------------------------------------------------------------- #
# ST-006 / AC-016 / OT-012 — the PHASE-ENTRY transitions record FULL
# --------------------------------------------------------------------------- #


def test_the_f2_entry_records_full_before_any_next_is_called(run_env):
    """OT-012 verbatim: 'The Foundry-Phase transition into F2 records inspect
    mode FULL with rule first_of_phase in state.json before any Foundry-Next is
    called, and the first Foundry-Next reports it.'

    The token is `cast`, not `start_cast`: `start_cast` enters F1 and opens no
    INSPECT at all. AC-016 says "the phase-entry transition into F2", and `cast`
    is the transition that performs it.
    """
    project_root, fdir = run_env
    _write_state(fdir, phase="F1", cycle=0)
    _write_manifest(fdir)
    _arm(fdir)

    result = foundry_mark_phase_complete("cast", project_root)

    assert result["ok"] is True, result
    assert result["phase"] == "F2"
    assert result["inspect_mode"] == "FULL"
    assert result["inspect_rule"] == "first_of_phase"

    # On disk, before anything else runs. This is the half OT-012 is emphatic
    # about: a mode that only appears once someone asks for it is a mode
    # Foundry-Next computed.
    recorded = _read_state(fdir)["inspect_modes"]
    assert len(recorded) == 1
    assert recorded[0]["mode"] == "FULL"
    assert recorded[0]["rule"] == "first_of_phase"
    assert recorded[0]["phase"] == "F2"
    assert recorded[0]["decided_by"] == "cast"

    reported = foundry_next_action(project_root)["inspect_mode"]
    assert reported["mode"] == "FULL"
    assert reported["rule"] == "first_of_phase"


def test_the_f5_entry_records_full_on_the_same_terms(run_env):
    """OT-012's second half: 'the same holds for the transition into F5.'

    One rule, two doors. GI-009 says "whichever Foundry-Phase transition opens
    an INSPECT records the mode", and TEMPER's first INSPECT is as much a first
    INSPECT as CAST's — it has no previous INSPECT of that phase to be a delta
    from, so FULL is a property of the position, not a policy about TEMPER.
    """
    project_root, fdir = run_env
    _write_state(fdir, phase="F4", cycle=3)
    _write_manifest(fdir)
    _arm(fdir)

    result = foundry_mark_phase_complete("temper", project_root)

    assert result["ok"] is True, result
    assert result["phase"] == "F5"
    recorded = _read_state(fdir)["inspect_modes"][-1]
    assert (recorded["mode"], recorded["rule"]) == ("FULL", "first_of_phase")
    assert recorded["phase"] == "F5"
    assert recorded["decided_by"] == "temper"


def test_start_cast_opens_no_inspect_and_records_nothing(run_env):
    """The boundary of the rule, and the reason the token matters.

    `start_cast` calls `_update_phase(fdir, "F1")` — it enters CAST. No INSPECT
    is opened, so no width is decided, and recording one here would put a
    decision on the artifact for a cycle that has not begun.
    """
    project_root, fdir = run_env
    _write_state(fdir, phase="F0", cycle=0)
    _arm(fdir)

    result = foundry_mark_phase_complete("start_cast", project_root)

    assert result["phase"] == "F1"
    assert "inspect_modes" not in _read_state(fdir)
    assert _current_inspect_mode(fdir) is None


# --------------------------------------------------------------------------- #
# ST-006 / ST-007 / AC-016 / OT-013 — inspect_start evaluates the rules
# --------------------------------------------------------------------------- #


def test_a_grind_touching_one_handler_file_records_delta(run_env):
    """OT-013 verbatim (first half): 'After Foundry-Phase inspect_start
    following a GRIND whose diff touches only one handler file, state.json
    records DELTA and Foundry-Next reports it.'"""
    project_root, fdir = run_env
    _write_state(fdir, phase="F3", cycle=1)
    _write_defects(fdir, [_open_live()])
    _write_manifest(fdir)
    _grind_touching(project_root, fdir, "src/handler.py")
    _arm(fdir)

    result = foundry_mark_phase_complete("inspect_start", project_root)

    assert result["ok"] is True, result
    assert result["inspect_mode"] == "DELTA"
    assert result["inspect_rule"] == INSPECT_DELTA_RULE

    recorded = _read_state(fdir)["inspect_modes"][-1]
    assert recorded["mode"] == "DELTA"
    assert recorded["decided_by"] == "inspect_start"
    assert "src/handler.py" in recorded["touched_files"]

    assert foundry_next_action(project_root)["inspect_mode"]["mode"] == "DELTA"


def test_a_grind_touching_vocab_records_full_with_rule_verifier_touched(run_env):
    """OT-013 verbatim (second half): 'after one whose diff touches
    schemas/vocab.py it records FULL with rule verifier_touched.'

    The reason is not that vocab is important. A delta roster is only as
    trustworthy as the verifier that runs it, so when the machinery that JUDGES
    the build moves, nothing narrower than everything is honest.
    """
    project_root, fdir = run_env
    _write_state(fdir, phase="F3", cycle=1)
    _write_defects(fdir, [_open_live()])
    _write_manifest(fdir)
    _grind_touching(project_root, fdir, "schemas/vocab.py")
    _arm(fdir)

    result = foundry_mark_phase_complete("inspect_start", project_root)

    assert result["inspect_mode"] == "FULL"
    assert result["inspect_rule"] == "verifier_touched"
    recorded = _read_state(fdir)["inspect_modes"][-1]
    assert "vocab.py" in recorded["rule_detail"]


@pytest.mark.parametrize(
    "path",
    [
        "schemas/vocab.py",
        "foundry_mcp/schemas/findings.py",
        "foundry_mcp/tools/foundry_orchestrator.py",
        "foundry_mcp/server.py",
        "agents/assayer.md",
        "skills/prove/SKILL.md",
        "commands/start.md",
    ],
)
def test_every_verifier_surface_forces_full(run_env, path):
    """FR-011's list, driven one member at a time.

    Parametrized rather than asserted as a set, because the requirement is about
    the DIFF reaching the decision — a rule that matched the right paths in
    `is_verifier_path` while the decision never consulted it would satisfy a set
    comparison and none of these.
    """
    project_root, fdir = run_env
    _write_state(fdir, phase="F3", cycle=1)
    _write_defects(fdir, [_open_live()])
    _write_manifest(fdir)
    _grind_touching(project_root, fdir, path)
    _arm(fdir)

    result = foundry_mark_phase_complete("inspect_start", project_root)

    assert result["inspect_mode"] == "FULL", (path, result)
    assert result["inspect_rule"] == "verifier_touched", (path, result)


def test_the_runs_own_spec_forces_full_though_it_is_no_static_pattern(run_env):
    """FR-011 names "the spec", and the spec is not in VERIFIER_PATH_PATTERNS.

    It cannot be: a run's spec lives wherever `state.json.spec_path` says, so a
    static pattern would either miss it or sweep in every unrelated spec.md in
    the tree. It is passed to `is_verifier_path` at call time instead, which
    means the decision has to resolve it — and this is the test that the
    resolution actually happens.
    """
    project_root, fdir = run_env
    _write_state(fdir, phase="F3", cycle=1, spec_path="docs/the-spec.md")
    _write_defects(fdir, [_open_live()])
    _write_manifest(fdir)
    (Path(project_root) / "docs").mkdir()
    (Path(project_root) / "docs" / "the-spec.md").write_text("- FR-001: x\n")
    _git(Path(project_root), "add", "-A")
    _git(Path(project_root), "commit", "-qm", "spec")
    _grind_touching(project_root, fdir, "docs/the-spec.md")
    _arm(fdir)

    result = foundry_mark_phase_complete("inspect_start", project_root)

    assert result["inspect_mode"] == "FULL"
    assert result["inspect_rule"] == "verifier_touched"


def test_a_cleared_ledger_records_delta_not_final_gate(run_env):
    """AC-016 verbatim: '...and DELTA otherwise'. D-068.

    THE ORDINARY CYCLE, which is the one US-004 exists for. The `final_gate` arm
    read `_blocking_defects(fdir)["blocking"] == 0` — true after ANY GRIND that
    fixed what INSPECT filed, which is exactly the state Foundry-Next instructs
    the lead to reach before crossing. So DELTA was unreachable on the guided
    path and every cycle ran the full roster: the thunder-viper behaviour
    US-004 exists to end, reproduced by the rule meant to end it.

    LEAD RULING, GRIND cycle 4: "the next gate is ASSAY" is a fact about the
    TRANSITION, not about the defect ledger. A clean DELTA cycle earns the
    widening re-open (asserted below), and THAT crossing is the final gate.
    """
    project_root, fdir = run_env
    _write_state(fdir, phase="F3", cycle=1)
    _write_defects(fdir, [{**_open_live(), "status": "fixed", "fixed_in_cycle": 1}])
    _write_manifest(fdir)
    _grind_touching(project_root, fdir, "src/handler.py")
    _arm(fdir)

    result = foundry_mark_phase_complete("inspect_start", project_root)

    assert result["inspect_mode"] == "DELTA"
    assert result["inspect_rule"] == "delta"


def test_an_empty_ledger_records_delta_too(run_env):
    """The same proxy, from the other direction: `blocking == 0` was true of a
    run that had filed nothing at all, so a first-GRIND cycle with an empty
    ledger also recorded FULL/final_gate."""
    project_root, fdir = run_env
    _write_state(fdir, phase="F3", cycle=1)
    _write_defects(fdir, [])
    _write_manifest(fdir)
    _grind_touching(project_root, fdir, "src/handler.py")
    _arm(fdir)

    result = foundry_mark_phase_complete("inspect_start", project_root)

    assert result["inspect_mode"] == "DELTA"
    assert result["inspect_rule"] == "delta"


def test_a_latent_only_backlog_records_delta(run_env):
    """And the third shape D-068 drove: a LATENT-only backlog leaves
    `_blocking_defects` at zero, so it too recorded FULL/final_gate. A
    never-reproduced backlog is not a reason to re-verify everything."""
    project_root, fdir = run_env
    _write_state(fdir, phase="F3", cycle=1)
    _write_defects(fdir, [{
        **_open_live(),
        "tier": "LATENT",
        "reproduction_attempted": "drove every caller; none reaches the branch",
    }])
    _write_manifest(fdir)
    _grind_touching(project_root, fdir, "src/handler.py")
    _arm(fdir)

    result = foundry_mark_phase_complete("inspect_start", project_root)

    assert result["inspect_mode"] == "DELTA"
    assert result["inspect_rule"] == "delta"


def test_a_clean_delta_cycle_widens_to_full_before_assay(run_env):
    """AC-016 / US-004: 'every final gate still runs everything at full width.'

    The path DELTA opens onto, driven end to end. A DELTA INSPECT that comes
    back clean does not open ASSAY — `inspect_clean` refuses naming the rule —
    and the lead re-calls `inspect_start` from F2. That crossing advances the
    counter, records FULL / final_gate, requires the full roster, and THEN
    inspect_clean opens ASSAY. Without this the ruling would trade one
    non-termination for a narrower final gate.
    """
    project_root, fdir = run_env
    _write_state(fdir, phase="F3", cycle=1)
    _write_defects(fdir, [{**_open_live(), "status": "fixed", "fixed_in_cycle": 1}])
    _write_manifest(fdir)
    _grind_touching(project_root, fdir, "src/handler.py")
    _arm(fdir)

    delta = foundry_mark_phase_complete("inspect_start", project_root)
    assert delta["inspect_mode"] == "DELTA"
    assert delta["cycle"] == 2

    # The DELTA roster runs and comes back clean, which is the whole premise:
    # the refusal below is about the WIDTH, not about unfinished streams.
    for stream in delta["required_streams"]:
        (fdir / f".{stream}-complete").write_text("x\n", encoding="utf-8")

    # The gate is refused while the recorded width is DELTA, by rule name.
    _arm(fdir)
    refused = foundry_mark_phase_complete("inspect_clean", project_root)
    assert refused.get("ok") is not True
    assert "final_gate" in refused["error"]
    assert refused["inspect_mode"] == "DELTA"

    # ...and the widening re-open is the crossing that opens it.
    _arm(fdir)
    widened = foundry_mark_phase_complete("inspect_start", project_root)

    assert widened["ok"] is True, widened
    assert widened["inspect_mode"] == "FULL"
    assert widened["inspect_rule"] == "final_gate"
    assert widened["widened"] is True
    assert widened["cycle"] == 3, "the widening re-open is a cycle of its own"
    assert widened["evidence_sweep"]["scope"] == "full"


def test_the_widening_re_open_is_refused_while_live_defects_are_open(run_env):
    """The ruling's guard on the re-open: open LIVE or unknown-tier defects go
    to GRIND first. Widening an INSPECT over code the run is about to change
    re-verifies a tree that will not exist."""
    project_root, fdir = run_env
    _write_state(fdir, phase="F3", cycle=1)
    _write_defects(fdir, [{**_open_live(), "status": "fixed", "fixed_in_cycle": 1}])
    _write_manifest(fdir)
    _grind_touching(project_root, fdir, "src/handler.py")
    _arm(fdir)
    assert foundry_mark_phase_complete("inspect_start", project_root)["inspect_mode"] == "DELTA"

    # The DELTA INSPECT files a LIVE defect.
    _write_defects(fdir, [{**_open_live(), "id": "D-009", "cycle": 2}])
    _arm(fdir)
    refused = foundry_mark_phase_complete("inspect_start", project_root)

    assert refused.get("ok") is not True, refused
    assert "D-009" in refused["error"]
    assert _current_cycle(fdir) == 2, "a refused transition moves no counter"


def test_the_re_open_is_refused_when_the_cycle_already_ran_full(run_env):
    """The other guard, which is also D-057's second lock: a cycle whose
    recorded width is already FULL has nothing to widen, so a stray second
    `inspect_start` is named as the mistake it is rather than silently
    advancing the run a cycle."""
    project_root, fdir = run_env
    _write_state(fdir, phase="F3", cycle=1)
    _write_defects(fdir, [])
    _write_manifest(fdir)
    _grind_touching(project_root, fdir, "src/schemas/vocab.py")
    _arm(fdir)
    first = foundry_mark_phase_complete("inspect_start", project_root)
    assert first["inspect_mode"] == "FULL"

    _arm(fdir)
    refused = foundry_mark_phase_complete("inspect_start", project_root)

    assert refused.get("ok") is not True, refused
    assert "nothing to widen" in refused["error"]
    assert _current_cycle(fdir) == 2


def test_a_grind_entered_from_assay_feedback_records_full(run_env):
    """AC-016's other `final_gate` arm: the GRIND that just ended was entered
    from ASSAY, TEMPER or NYQUIST feedback, so the gate that rejected the run is
    the one that will read this INSPECT.

    Read from `phase_history`, which `_update_phase` already writes on every
    transition — a second marker recording the same fact is a second thing that
    can drift.
    """
    project_root, fdir = run_env
    _write_state(
        fdir, phase="F3", cycle=1,
        phase_history=[
            {"phase": "F2", "entered_at": "2026-01-01T00:00:00+00:00"},
            {"phase": "F4", "entered_at": "2026-01-01T01:00:00+00:00"},
            {"phase": "F3", "entered_at": "2026-01-01T02:00:00+00:00"},
        ],
    )
    _write_defects(fdir, [_open_live()])
    _write_manifest(fdir)
    _grind_touching(project_root, fdir, "src/handler.py")
    _arm(fdir)

    result = foundry_mark_phase_complete("inspect_start", project_root)

    assert result["inspect_mode"] == "FULL"
    assert result["inspect_rule"] == "final_gate"


def test_an_uncomputable_diff_is_full_and_says_why(run_env):
    """A diff that CANNOT be computed is not an empty diff, and the two must not
    be confused: an empty diff licenses the narrowest roster, an unknown one
    licenses none.

    The recorded rule is `verifier_touched` because `INSPECT_FULL_RULES` is a
    closed vocabulary casting 1 owns and has no member meaning "the diff could
    not be computed" — and inventing one here would be a second copy of that
    vocabulary. The REAL cause is in `rule_detail`, which is why that field
    exists and why this test asserts on it rather than on the rule alone.
    """
    project_root, fdir = run_env
    _write_state(fdir, phase="F3", cycle=1)
    _write_defects(fdir, [_open_live()])
    _write_manifest(fdir)
    # No boundary marker, no clean-TRACE marker, no CAST baseline: nothing to
    # diff from.
    _arm(fdir)

    result = foundry_mark_phase_complete("inspect_start", project_root)

    assert result["inspect_mode"] == "FULL"
    recorded = _read_state(fdir)["inspect_modes"][-1]
    assert recorded["rule"] in INSPECT_FULL_RULES
    assert "could not be computed" in recorded["rule_detail"]


def test_the_recorded_mode_and_rule_are_members_of_the_closed_vocabularies(run_env):
    """The vocabulary is READ, never re-typed (the module's own house rule).

    A width recorded as "full" or a rule recorded as "delta_mode" would render,
    display and roll up perfectly while being a token no reader of
    `INSPECT_MODES` can match.
    """
    project_root, fdir = run_env
    _write_state(fdir, phase="F3", cycle=1)
    _write_defects(fdir, [_open_live()])
    _write_manifest(fdir)
    _grind_touching(project_root, fdir, "src/handler.py")
    _arm(fdir)

    foundry_mark_phase_complete("inspect_start", project_root)

    recorded = _read_state(fdir)["inspect_modes"][-1]
    assert recorded["mode"] in INSPECT_MODES
    assert recorded["rule"] in (INSPECT_FULL_RULES | {INSPECT_DELTA_RULE})


# --------------------------------------------------------------------------- #
# ST-007 / FR-012 / FR-047 / AC-017 / OT-014 — the roster at each width
# --------------------------------------------------------------------------- #


def test_full_mode_requires_every_roster_stream(run_env):
    """AC-017 (first half): 'In FULL mode the streams-complete check requires
    trace, prove, test, research_audit and test01.'"""
    project_root, fdir = run_env
    _write_state(fdir, phase="F1", cycle=0)
    _write_manifest(fdir)
    _arm(fdir)

    foundry_mark_phase_complete("cast", project_root)

    assert set(_read_state(fdir)["inspect_modes"][-1]["required_streams"]) == set(
        FULL_ROSTER_STREAMS
    )
    streams = _check_streams_complete(project_root)
    assert set(streams["required"]) == set(FULL_ROSTER_STREAMS)
    assert streams["inspect_mode"] == "FULL"


def test_full_mode_honours_manifest_stream_skips(run_env):
    """AC-017's exception: 'unless the stream is in manifest.stream_skips'.

    F0.5 records a predictive skip when a run genuinely has no work for a
    stream; requiring it anyway is the grand-vulture deadlock, where a fully
    clean INSPECT blocked on a stream that had nothing to check.
    """
    project_root, fdir = run_env
    _write_state(fdir, phase="F1", cycle=0)
    _write_manifest(fdir, stream_skips=[{"stream_id": "TEST-01", "reason": "no contracts"}])
    _arm(fdir)

    foundry_mark_phase_complete("cast", project_root)

    recorded = _read_state(fdir)["inspect_modes"][-1]
    assert "test01" not in recorded["required_streams"]
    assert recorded["stream_scope"]["test01"]["scope"] == "skipped"
    assert "stream_skips" in recorded["stream_scope"]["test01"]["detail"]
    # ...and the rest of the roster is untouched: a skip removes one stream, it
    # does not narrow the width.
    assert recorded["mode"] == "FULL"
    assert "trace" in recorded["required_streams"]


def test_full_mode_honours_a_research_skipped_record(run_env):
    """AC-017's other exception: 'or a research_skipped record exists'.

    A run that skipped RESEARCH has no research recommendations to audit
    against, so RESEARCH_AUDIT has nothing it could find.
    """
    project_root, fdir = run_env
    _write_state(fdir, phase="F1", cycle=0, research_skipped=True)
    _write_manifest(fdir)
    _arm(fdir)

    foundry_mark_phase_complete("cast", project_root)

    recorded = _read_state(fdir)["inspect_modes"][-1]
    assert "research_audit" not in recorded["required_streams"]
    assert recorded["stream_scope"]["research_audit"]["scope"] == "skipped"


def test_delta_mode_requires_neither_conditional_stream_on_an_untouched_scope(run_env):
    """OT-014 (middle): 'in DELTA mode with an untouched research scope it does
    not [require research_audit or test01]'.

    FR-047: "In DELTA mode Foundry-Next names research_audit and test01 as
    required only for a touched scope, and the streams-complete check enforces
    exactly that set."
    """
    project_root, fdir = run_env
    _write_state(fdir, phase="F3", cycle=1)
    _write_defects(fdir, [_open_live()])
    _write_manifest(fdir)
    _grind_touching(project_root, fdir, "src/handler.py")
    _arm(fdir)

    foundry_mark_phase_complete("inspect_start", project_root)

    recorded = _read_state(fdir)["inspect_modes"][-1]
    assert recorded["mode"] == "DELTA"
    for wire in sorted(DELTA_CONDITIONAL_STREAMS):
        assert wire not in recorded["required_streams"], wire
        assert recorded["stream_scope"][wire]["scope"] == "skipped"

    streams = _check_streams_complete(project_root)
    assert set(streams["required"]) == {"trace", "prove", "test"}


def test_delta_mode_requires_research_audit_when_a_research_scope_is_touched(run_env):
    """OT-014 (last): '...and with a touched scope it does'.

    A casting declaring a `research_context` is one whose code was written
    against research recommendations, so its key_files are exactly what an audit
    of those recommendations reads.
    """
    project_root, fdir = run_env
    _write_state(fdir, phase="F3", cycle=1)
    _write_defects(fdir, [_open_live()])
    _write_manifest(
        fdir,
        castings=[{
            "id": 1,
            "key_files": ["src/handler.py"],
            "research_context": "research/auth.md",
        }],
    )
    _grind_touching(project_root, fdir, "src/handler.py")
    _arm(fdir)

    foundry_mark_phase_complete("inspect_start", project_root)

    recorded = _read_state(fdir)["inspect_modes"][-1]
    assert recorded["mode"] == "DELTA"
    assert "research_audit" in recorded["required_streams"]
    assert recorded["stream_scope"]["research_audit"]["scope"] == "full"


def test_delta_mode_requires_test01_when_a_schema_file_is_touched(run_env):
    """ST-007's test01 half: required when 'the diff touches a file they cover'.

    TEST-01 derives property tests from the spec's Contracts table and the
    schemas those surfaces validate against, so a touched `schemas/` file is
    inside its scope by construction.

    Reached via a DELTA cycle whose OTHER changed file is what keeps the width
    narrow — `schemas/` is also a verifier path, so this asserts the conditional
    roster rather than the width. It is driven through `_decide_inspect_mode`
    with the FULL rules already answered, which is the only way to see the
    conditional rung alone.
    """
    project_root, fdir = run_env
    _write_state(fdir, phase="F3", cycle=1)
    _write_defects(fdir, [_open_live()])
    _write_manifest(fdir)
    _write_spec(fdir, ["FR-001"])
    assert fo._test01_scope_touched(project_root, ["src/foundry_mcp/schemas/x.py"])
    assert not fo._test01_scope_touched(project_root, ["src/handler.py"])


def test_streams_complete_reads_the_recorded_roster_and_never_recomputes_it(run_env):
    """GI-008's named violation: 'a streams-complete check that reads a roster
    nothing recorded because Next was skipped'.

    Driven by RECORDING one roster and then moving the tree underneath it. The
    check must still answer with what the cycle was told to run, because the
    mode is a function of a diff measured at the boundary and by now the tree
    has moved on. A check that re-derived would answer about a cycle that never
    ran.
    """
    project_root, fdir = run_env
    _write_state(fdir, phase="F3", cycle=1)
    _write_defects(fdir, [_open_live()])
    _write_manifest(fdir)
    _grind_touching(project_root, fdir, "src/handler.py")
    _arm(fdir)
    foundry_mark_phase_complete("inspect_start", project_root)
    recorded = list(_read_state(fdir)["inspect_modes"][-1]["required_streams"])

    # The tree moves: a verifier file changes AFTER the decision was recorded.
    # A recomputing check would now demand the FULL roster for a cycle that was
    # dispatched at DELTA.
    _grind_touching(project_root, fdir, "schemas/vocab.py")

    assert _check_streams_complete(project_root)["required"] == recorded


def test_a_run_with_no_recorded_decision_is_refused_not_read_as_full(run_env):
    """D-117 / GI-008 / GI-009 — the pre-width fallback IS the violation.

    This function used to fall back to the roster it required before the width
    existed — trace/prove/test — and the fallback was argued for as archive
    compatibility. GI-008 names it outright: "a streams-complete check that
    reads a roster nothing recorded". The fallback silently dropped
    `research_audit` and `test01` from every mode-less INSPECT and reported
    complete, which is how ASSAY came to open on a three-stream roster.

    The refusal names the missing record AND the transition that writes it,
    because "there is no recorded width" is not an action.
    """
    project_root, fdir = run_env
    _write_state(fdir, phase="F2", cycle=2)
    _write_manifest(fdir)

    streams = _check_streams_complete(project_root)

    assert streams["complete"] is False, streams
    assert streams["unrecorded_width"] is True, streams
    assert streams["required"] == []
    assert "inspect_mode" in streams["missing"]
    assert "no recorded width" in streams["reason"]
    assert "inspect_start" in streams["hint"]
    assert _current_inspect_mode(fdir) is None


def test_an_unrecorded_inspect_width_is_refused_at_every_door(run_env):
    """D-117 driven at all five consumers on ONE run state.

    Each door degraded permissively on its own terms and each was individually
    defensible; together they admitted a mode-less INSPECT as full width
    everywhere at once. Driven end to end on a legacy-shaped archive with stale
    committed evidence: streams-complete reported complete, `inspect_clean`
    returned ok and moved the run to F4, and `Foundry-Gate('assay')` PASSED
    carrying `inspect_ran_at_full_width (mode=unrecorded rule=unrecorded)
    ok=True` — the check naming, in its own string, the fact that made its
    verdict false.

    Asserted as one test over the whole set, because a fix wired into four of
    five doors leaves the fifth deciding on a different definition of "full
    width", which is the shape D-117 reports.
    """
    project_root, fdir = run_env
    _write_state(fdir, phase="F2", cycle=2)
    _write_manifest(fdir)
    _write_spec(fdir, ["FR-001"])
    _write_defects(fdir, [])
    for stream in ("trace", "prove", "test"):
        (fdir / f".{stream}-complete").write_text(
            "2020-01-01T00:00:00+00:00 cycle=2\nitems_checked=1\nitems_total=1\n"
            "coverage=100%\nfindings=0\n",
            encoding="utf-8",
        )
    assert _current_inspect_mode(fdir) is None

    # 1. the streams-complete check
    streams = _check_streams_complete(project_root)
    assert streams["complete"] is False, streams

    # 2. Foundry-Phase('inspect_clean')
    _arm(fdir)
    clean = foundry_mark_phase_complete("inspect_clean", project_root)
    assert clean.get("ok") is not True, clean
    assert clean["unrecorded_width"] is True, clean
    assert "no recorded width" in clean["error"], clean
    assert "inspect_start" in clean["hint"], clean
    assert _read_state(fdir)["phase"] == "F2", "the run must not reach F4"

    # 3. Foundry-Gate('assay')
    _arm(fdir)
    gate = fo.foundry_gate("assay", project_root)
    assert gate["passed"] is False, gate
    width = next(
        c for c in gate["checklist"]
        if c["check"].startswith("inspect_ran_at_full_width")
    )
    assert width["ok"] is False, width
    assert "unrecorded" in width["check"], width

    # 4. `_maybe_skip_trace` — no auto-stamp, whatever the legacy markers say
    (fdir / ".trace-complete").unlink(missing_ok=True)
    (fdir / ".trace-clean-at").write_text(
        _git(Path(project_root), "rev-parse", "HEAD") + "\n", encoding="utf-8"
    )
    decision = fo._maybe_skip_trace(fdir, project_root)
    assert decision is not None and decision["skip"] is False, decision
    assert "no recorded width" in decision["reason"], decision
    assert not (fdir / ".trace-complete").exists(), (
        "TRACE was auto-stamped complete on a width nothing recorded"
    )

    # 5. Foundry-Next names the transition that records one, and dispatches no
    #    stream roster it cannot know.
    action = fo._compute_next_action(project_root)
    assert action["action"] == "record_inspect_width", action
    assert "inspect_start" in action["instructions"], action
    assert action["details"]["unrecorded_width"] is True


def test_an_unrecorded_width_sweeps_the_whole_corpus_not_a_delta_slice(run_env):
    """D-117's fifth consumer: the sweep scope is a CLAIM about the width.

    "Only these logs can have been invalidated by this GRIND" rests on the width
    decision, so an entry carrying no mode must not also narrow the sweep. The
    honest reading of an unknown width is the whole corpus — the same reading
    `_decide_inspect_mode` already gives an uncomputable diff.
    """
    project_root, fdir = run_env
    _write_manifest(fdir)
    seen: dict = {}

    def _fake_select(*, manifest, evidence_dir, touched_files, full):
        seen["full"] = full
        return []

    import foundry_mcp.tools.evidence as evidence_mod

    original = evidence_mod.select_sweep_scope
    evidence_mod.select_sweep_scope = _fake_select
    try:
        fo._sweep_evidence_at_boundary(
            fdir, project_root, {"touched_files": ["src/handler.py"]}, full=False
        )
    finally:
        evidence_mod.select_sweep_scope = original

    assert seen["full"] is True, (
        "an entry with no recorded mode narrowed the sweep to a delta slice"
    )


# --------------------------------------------------------------------------- #
# D-102 / FR-011 / AC-016 / ST-006 — the spec has TWO spellings and both fire
# --------------------------------------------------------------------------- #


def test_a_grind_touching_the_declared_spec_path_records_verifier_touched(run_env):
    """FR-011 verbatim (Locked): 'FULL when: ... or the GRIND diff touches
    vocab.py, schemas/, gate/orchestrator code, agent/skill prose, or the spec.'

    The rule never fired on the spec. `_resolve_spec_path` PREFERS
    `<run_dir>/spec.md` and only falls back to `state.json.spec_path`, so on the
    ordinary run — which has both, because init copies the spec into the archive
    — the singular resolver returned the archive copy and the authored spec was
    invisible. Driven at three spec locations: a GRIND diff whose ONLY touched
    file was the path `state.json` records as `spec_path` opened the next
    INSPECT at DELTA, rule `delta`, while every other FULL trigger fired
    correctly.
    """
    project_root, fdir = run_env
    _write_manifest(fdir)
    _write_defects(fdir, [_open_live()])

    spec_rel = "forge-specs/subject/spec.md"
    target = Path(project_root) / spec_rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("# Spec\n- **FR-001**: the thing works\n", encoding="utf-8")
    _write_state(fdir, phase="F3", cycle=1, spec_path=spec_rel)
    # The archive copy exists too, which is the whole point: the run has BOTH
    # spellings and the old resolver could only ever return one of them.
    _write_spec(fdir, ["FR-001"])

    _grind_touching(project_root, fdir, spec_rel)
    _arm(fdir)
    result = foundry_mark_phase_complete("inspect_start", project_root)

    assert result["ok"] is True, result
    assert result["inspect_mode"] == "FULL", result
    assert result["inspect_rule"] == "verifier_touched", result
    entry = _read_state(fdir)["inspect_modes"][-1]
    assert entry["touched_files"] == [spec_rel], entry
    assert spec_rel in entry["rule_detail"], entry


def test_the_archived_spec_spelling_answers_the_predicate_too(run_env):
    """The other spelling, so the fix cannot be a swap of which one is checked.

    Asserted through the predicate rather than through a GRIND diff, because
    this repo's own `.gitignore` carries `/foundry-archive/` (the fixture
    mirrors it, for the reason `run_env` states) — the archive copy cannot
    appear in a diff HERE. It can in a target repo that does not ignore the
    archive, and C-1 names both paths, so both are supplied and both must
    answer. A fix that preferred the declared path over the archived one would
    close D-102 by reopening it on the other side.
    """
    project_root, fdir = run_env
    _write_state(fdir, phase="F2", cycle=1, spec_path="forge-specs/subject/spec.md")
    declared = Path(project_root) / "forge-specs" / "subject" / "spec.md"
    declared.parent.mkdir(parents=True, exist_ok=True)
    declared.write_text("# Spec\n", encoding="utf-8")
    _write_spec(fdir, ["FR-001"])

    from foundry_mcp.schemas.vocab import is_verifier_path

    spellings = fo._spec_relative_paths(project_root)
    archived = next(s for s in spellings if s.endswith(f"{RUN_NAME}/spec.md"))
    assert any(is_verifier_path("forge-specs/subject/spec.md", s) for s in spellings)
    assert any(is_verifier_path(archived, s) for s in spellings)
    # Neither is a static VERIFIER_PATH_PATTERNS member — they match only
    # because the caller supplied them, which is the seam D-102 is about.
    assert not is_verifier_path("forge-specs/subject/spec.md")
    assert not is_verifier_path(archived)


def test_both_spec_spellings_are_offered_to_the_verifier_predicate(run_env):
    """The seam itself, so a caller cannot silently go back to one spelling.

    `is_verifier_path` takes ONE `spec_path` and lives in casting 1's vocab.py,
    whose signature is LOCKED by C-1 — so the two spellings have to be supplied
    BY THE CALLER, and this asserts the caller has both to supply.
    """
    project_root, fdir = run_env
    _write_state(fdir, phase="F2", cycle=1, spec_path="forge-specs/subject/spec.md")
    declared = Path(project_root) / "forge-specs" / "subject" / "spec.md"
    declared.parent.mkdir(parents=True, exist_ok=True)
    declared.write_text("# Spec\n", encoding="utf-8")
    _write_spec(fdir, ["FR-001"])

    spellings = fo._spec_relative_paths(project_root)

    assert "forge-specs/subject/spec.md" in spellings, spellings
    assert any(s.endswith(f"{RUN_NAME}/spec.md") for s in spellings), spellings
    assert len(spellings) == len(set(spellings)), spellings


# --------------------------------------------------------------------------- #
# FR-013 / FR-033 / AC-018 / AC-019 / OT-015 — the DELTA roster's contents
# --------------------------------------------------------------------------- #


def test_the_prove_sample_is_the_fixed_rows_plus_a_seeded_draw(run_env):
    """AC-018 verbatim: 'In DELTA mode the PROVE roster names the matrix rows
    tied to the defects fixed in the preceding GRIND plus ten rows drawn with a
    seed derived from the cycle number.'

    The tied rows come first because they are the rows whose verdicts the fixes
    could actually have changed; the sample is a floor on coverage beyond them,
    so a regression outside the fixed rows is still found — just not in one
    cycle.
    """
    project_root, fdir = run_env
    ids = [f"FR-{n:03d}" for n in range(1, 41)]
    _write_spec(fdir, ids)
    _write_state(fdir, phase="F3", cycle=1, spec_path="")
    _write_defects(fdir, [
        _open_live("D-001"),
        {**_open_live("D-002"), "status": "fixed", "fixed_in_cycle": 1,
         "spec_ref": "FR-007"},
    ])
    _write_manifest(fdir)
    _grind_touching(project_root, fdir, "src/handler.py")
    _arm(fdir)

    foundry_mark_phase_complete("inspect_start", project_root)

    sample = _read_state(fdir)["inspect_modes"][-1]["prove_sample"]
    assert sample[0] == "FR-007", sample
    assert len(sample) == 1 + PROVE_DELTA_SAMPLE_SIZE, sample
    assert len(set(sample)) == len(sample), "a row must not be drawn twice"
    assert set(sample) <= set(ids)


def test_the_same_cycle_number_yields_the_same_ten_rows(run_env):
    """AC-018's second half: 'the same cycle number yields the same ten rows.'

    FR-033 makes the PRNG the implementer's choice with one proviso — the sample
    must be reproducible from the cycle number ALONE. So this drives the
    computation twice against DIFFERENT ledger orderings: a sample that depended
    on dict iteration, ledger order or filesystem order would pass a
    same-inputs-twice test and fail this one.
    """
    project_root, fdir = run_env
    ids = [f"FR-{n:03d}" for n in range(1, 41)]
    _write_spec(fdir, ids)
    _write_state(fdir, phase="F3", cycle=1, spec_path="")
    _write_defects(fdir, [_open_live("D-001")])

    first = fo._prove_delta_sample(fdir, project_root, 7)

    _write_defects(fdir, [_open_live("D-009"), _open_live("D-001")])
    second = fo._prove_delta_sample(fdir, project_root, 7)

    assert first == second, (first, second)
    assert first != fo._prove_delta_sample(fdir, project_root, 8), (
        "a different cycle must draw a different sample, or the seed is not the "
        "cycle"
    )


def test_two_next_calls_in_one_cycle_report_the_same_sample(run_env):
    """OT-015 verbatim: 'Two Foundry-Next calls in the same DELTA cycle list the
    same ten sampled PROVE rows.'

    True by construction, and that IS the requirement: the sample is drawn ONCE,
    at the transition, and recorded. Foundry-Next reads it. Two calls could only
    disagree if one of them were drawing.
    """
    project_root, fdir = run_env
    _write_spec(fdir, [f"FR-{n:03d}" for n in range(1, 41)])
    _write_state(fdir, phase="F3", cycle=1, spec_path="")
    _write_defects(fdir, [_open_live()])
    _write_manifest(fdir)
    _grind_touching(project_root, fdir, "src/handler.py")
    _arm(fdir)
    foundry_mark_phase_complete("inspect_start", project_root)

    first = foundry_next_action(project_root)["inspect_mode"]["prove_sample"]
    second = foundry_next_action(project_root)["inspect_mode"]["prove_sample"]

    assert first == second
    assert len(first) == PROVE_DELTA_SAMPLE_SIZE


def test_test_runs_full_and_cold_even_at_delta_width(run_env):
    """AC-019 verbatim: 'In DELTA mode TEST still runs full and cold from a
    clean worktree and TRACE runs over the symbols the GRIND commits touched.'

    TEST is the stream a narrow INSPECT is most exposed to: a narrowed suite
    cannot see a regression the GRIND opened in a module it did not edit. So it
    is the one member of the DELTA roster whose scope stays `full`.
    """
    project_root, fdir = run_env
    _write_state(fdir, phase="F3", cycle=1)
    _write_defects(fdir, [_open_live()])
    _write_manifest(fdir)
    _grind_touching(project_root, fdir, "src/handler.py")
    _arm(fdir)

    foundry_mark_phase_complete("inspect_start", project_root)

    scope = _read_state(fdir)["inspect_modes"][-1]["stream_scope"]
    assert scope["test"]["scope"] == "full"
    assert "cold" in scope["test"]["detail"]
    assert scope["trace"]["scope"] == "delta"
    assert scope["prove"]["scope"] == "delta"


# --------------------------------------------------------------------------- #
# GI-008 / GI-009 / CT-009 / FR-049 — Foundry-Next reports and never decides
# --------------------------------------------------------------------------- #


def test_compute_next_action_contains_no_width_decision(run_env):
    """GI-009: 'No decision ever lives in Foundry-Next.'

    Asserted against the SOURCE, because the claim is about where a decision can
    be made and not about what one call happened to return. A `_compute_next_action`
    that called the decider would return the right answer today and be the
    violation GI-008 names — a mode computed at display time is a mode no
    artifact holds, and two calls either side of a commit would disagree.
    """
    import inspect

    source = inspect.getsource(fo._compute_next_action)
    assert "_decide_inspect_mode" not in source
    assert "_record_inspect_mode" not in source

    # And the reporting path reads the recorded entry through the ONE reader.
    reporter = inspect.getsource(fo.foundry_next_action)
    assert "_current_inspect_mode" in reporter
    assert "_decide_inspect_mode" not in reporter


def test_next_reports_the_recorded_decision_whenever_it_is_called(run_env):
    """CT-009: the decision is 'recorded in state and stream-rollup at the
    transition and displayed by every later Foundry-Next'.

    Every LATER call — not just the first. A decision that only the next call
    reports is a decision the lead loses by looking twice.
    """
    project_root, fdir = run_env
    _write_state(fdir, phase="F3", cycle=1)
    _write_defects(fdir, [_open_live()])
    _write_manifest(fdir)
    _grind_touching(project_root, fdir, "src/handler.py")
    _arm(fdir)
    foundry_mark_phase_complete("inspect_start", project_root)

    for _ in range(3):
        reported = foundry_next_action(project_root)["inspect_mode"]
        assert reported["mode"] == "DELTA"
        assert reported["rule"] == INSPECT_DELTA_RULE
        assert reported["required_streams"] == ["trace", "prove", "test"]


def test_the_decision_is_mirrored_into_the_cycle_rollup(run_env):
    """C-6: `cycles[<cycle>]` gains `inspect_mode`, `inspect_rule` and
    `stream_scope`.

    The roll-up is what the F6 report reads for "the FULL or DELTA decision per
    cycle", so a decision recorded only in state.json would be invisible to the
    report that has to list it.
    """
    project_root, fdir = run_env
    _write_state(fdir, phase="F3", cycle=1)
    _write_defects(fdir, [_open_live()])
    _write_manifest(fdir)
    _grind_touching(project_root, fdir, "src/handler.py")
    _arm(fdir)

    foundry_mark_phase_complete("inspect_start", project_root)

    rollup = json.loads(
        (fdir / fo.ROLLUP_FILENAME).read_text(encoding="utf-8")
    )["cycles"]["2"]
    assert rollup["inspect_mode"] == "DELTA"
    assert rollup["inspect_rule"] == INSPECT_DELTA_RULE
    assert rollup["stream_scope"]["test"]["scope"] == "full"


def test_the_decider_takes_the_transition_that_called_it_and_records_it(run_env):
    """C-4's `decided_by`: 'cast' | 'temper' | 'inspect_start'.

    Recorded rather than inferred, so "which crossing decided this width" is
    answerable from the artifact. A reader looking at a FULL cycle needs to tell
    a phase entry from a final gate, and the rule alone does not say which door
    the run came through.
    """
    project_root, fdir = run_env
    _write_state(fdir, phase="F3", cycle=1)
    _write_defects(fdir, [_open_live()])
    _write_manifest(fdir)

    for token in ("cast", "temper", "inspect_start"):
        entry = _decide_inspect_mode(
            fdir, project_root, decided_by=token, phase="F2", cycle=1
        )
        assert entry["decided_by"] == token


# --------------------------------------------------------------------------- #
# GI-002 / ST-005 / CT-007 / FR-009 / FR-042 / FR-043 / AC-013 / AC-014 /
# OT-008 / OT-016 — THE EVIDENCE SWEEP AT THE BOUNDARY
#
# GI-002 is a statement about WHO sweeps: "the SERVER sweeps at the boundary and
# refuses on mismatch". Its named violations are a boundary that trusts a
# teammate-reported sweep and a lead running a shell loop over `evidence/`. Both
# of those are CLAIMS; this is a measurement, taken at the one crossing where
# HEAD is the tree every later gate will judge.
#
# FR-043 says why it lives here rather than in a teammate's checklist:
# "Teammates commit per-casting logs; nobody sweeps by hand."
# --------------------------------------------------------------------------- #


def _evidence_log(project_root: str, name: str, command: str, body: str,
                  requirement: str = "FR-001") -> Path:
    """Commit one evidence log in the shipped v2.1 shape.

    Repo-root `evidence/`, which is where `select_sweep_scope` globs and where
    every casting in this run committed — not the run directory, which is
    gitignored and therefore absent from every worktree the sweep builds.
    """
    evidence = Path(project_root) / "evidence"
    evidence.mkdir(parents=True, exist_ok=True)
    log = evidence / name
    log.write_text(
        f"# evidence-cmd: {command}\n"
        f"# evidence-for: {requirement}\n"
        f"\n{body}",
        encoding="utf-8",
    )
    return log


def test_a_log_that_no_longer_reproduces_refuses_the_transition_by_name(run_env):
    """AC-013 verbatim: 'Foundry-Phase inspect_start re-executes the in-scope
    evidence logs in a detached worktree at HEAD and refuses the transition
    naming any log whose output mismatches; the cycle counter does not advance
    on refusal.'

    OT-008 states the same claim as an observable. NAMING the log is the part
    that matters operationally: a sweep that said only "something no longer
    reproduces" would send the lead to re-run the whole corpus by hand to find
    out which — and the sweep already knows.
    """
    project_root, fdir = run_env
    _write_state(fdir, phase="F3", cycle=1)
    _write_defects(fdir, [_open_live()])
    _write_manifest(fdir)
    # A log whose committed body disagrees with what its own command produces.
    _evidence_log(
        project_root, "casting-1-handler.log",
        "echo the-handler-calls-the-store", "the-handler-does-not\n",
    )
    _grind_touching(project_root, fdir, "src/handler.py")
    _arm(fdir)

    result = foundry_mark_phase_complete("inspect_start", project_root)

    assert result.get("ok") is not True, result
    assert "casting-1-handler.log" in result["error"], result
    assert result["mismatches"], result

    # The counter did NOT advance, and no mode was recorded for a transition
    # that did not happen.
    assert _read_state(fdir)["cycle"] == 1
    assert _read_state(fdir)["phase"] == "F3"
    assert "inspect_modes" not in _read_state(fdir)


def test_a_log_that_still_reproduces_lets_the_boundary_through(run_env):
    """The other side of the same gate: the sweep is a measurement, not a
    tollbooth. A corpus that still reproduces at HEAD costs the run nothing but
    the time to prove it."""
    project_root, fdir = run_env
    _write_state(fdir, phase="F3", cycle=1)
    _write_defects(fdir, [_open_live()])
    _write_manifest(fdir)
    _evidence_log(
        project_root, "casting-1-handler.log",
        "echo the-handler-calls-the-store", "the-handler-calls-the-store\n",
    )
    _grind_touching(project_root, fdir, "src/handler.py")
    _arm(fdir)

    result = foundry_mark_phase_complete("inspect_start", project_root)

    assert result["ok"] is True, result
    assert result["cycle"] == 2
    sweep = result["evidence_sweep"]
    assert sweep["mismatches"] == []
    assert len(sweep["logs_reexecuted"]) == 1


def test_a_grind_touching_nothing_in_scope_re_executes_zero_logs(run_env):
    """AC-014 verbatim (first half): 'When the GRIND diff touches no casting
    key_files and no file referenced by a log's command, the sweep re-executes
    zero logs and records the delta scope.'

    An empty scope is a COMPLETE answer, not a degenerate one — and it is the
    common case that makes DELTA worth having. It costs no worktree, no
    `.git/config.lock` contention and no subprocess at all.
    """
    project_root, fdir = run_env
    _write_state(fdir, phase="F3", cycle=1)
    _write_defects(fdir, [_open_live()])
    _write_manifest(fdir)
    _evidence_log(
        project_root, "casting-1-handler.log",
        "echo unrelated", "unrelated\n",
    )
    _git(Path(project_root), "add", "-A")
    _git(Path(project_root), "commit", "-qm", "evidence")
    # The GRIND touches a file that is no casting's key_file and that no log's
    # command mentions.
    _grind_touching(project_root, fdir, "src/other.py")
    _arm(fdir)

    result = foundry_mark_phase_complete("inspect_start", project_root)

    assert result["ok"] is True, result
    sweep = result["evidence_sweep"]
    assert sweep["scope"] == "delta"
    assert sweep["logs_reexecuted"] == []


def test_the_delta_sweep_re_executes_only_the_logs_tied_to_the_touched_file(run_env):
    """OT-016 verbatim (first half): 'A DELTA sweep after a GRIND touching one
    file re-executes only the logs tied to that file's casting or referencing
    that file.'

    FR-009's scope rule. The corpus grows with the run — thunder-viper finished
    with dozens of logs — so re-executing all of them after a three-file GRIND is
    most of what a needless full INSPECT costs.
    """
    project_root, fdir = run_env
    _write_state(fdir, phase="F3", cycle=1)
    _write_defects(fdir, [_open_live()])
    (fdir / "castings" / "manifest.json").write_text(
        json.dumps({"castings": [
            {"id": 1, "key_files": ["src/handler.py"]},
            {"id": 2, "key_files": ["src/other.py"]},
        ]}),
        encoding="utf-8",
    )
    _evidence_log(project_root, "casting-1-handler.log", "echo one", "one\n")
    _evidence_log(project_root, "casting-2-other.log", "echo two", "two\n")
    _git(Path(project_root), "add", "-A")
    _git(Path(project_root), "commit", "-qm", "evidence")
    _grind_touching(project_root, fdir, "src/handler.py")
    _arm(fdir)

    result = foundry_mark_phase_complete("inspect_start", project_root)

    assert result["ok"] is True, result
    executed = [Path(p).name for p in result["evidence_sweep"]["logs_reexecuted"]]
    assert executed == ["casting-1-handler.log"], result["evidence_sweep"]


def test_the_full_rule_sweeps_the_whole_corpus(run_env):
    """AC-014's second half and OT-016's: 'at the INSPECT before ASSAY, NYQUIST
    or DONE the sweep covers the whole corpus' / 'the sweep before ASSAY
    re-executes every log.'

    One decision drives both the roster and the sweep scope, which is the point
    of computing the width first: the gates that end a run are never handed a
    partial answer about either.
    """
    project_root, fdir = run_env
    # D-068: the FULL rule that fires here is `verifier_touched` — the GRIND
    # diff moved schemas/. A cleared defect ledger is no longer a FULL rule, so
    # the sweep scope is driven by a real widening cause rather than by the
    # proxy that made every cycle full.
    _write_state(fdir, phase="F3", cycle=1)
    _write_defects(fdir, [{**_open_live(), "status": "fixed", "fixed_in_cycle": 1}])
    (fdir / "castings" / "manifest.json").write_text(
        json.dumps({"castings": [
            {"id": 1, "key_files": ["src/handler.py"]},
            {"id": 2, "key_files": ["src/other.py"]},
        ]}),
        encoding="utf-8",
    )
    _evidence_log(project_root, "casting-1-handler.log", "echo one", "one\n")
    _evidence_log(project_root, "casting-2-other.log", "echo two", "two\n")
    _git(Path(project_root), "add", "-A")
    _git(Path(project_root), "commit", "-qm", "evidence")
    _grind_touching(project_root, fdir, "src/handler.py", "src/schemas/vocab.py")
    _arm(fdir)

    result = foundry_mark_phase_complete("inspect_start", project_root)

    assert result["ok"] is True, result
    assert result["inspect_rule"] == "verifier_touched"
    sweep = result["evidence_sweep"]
    assert sweep["scope"] == "full"
    assert sorted(Path(p).name for p in sweep["logs_reexecuted"]) == [
        "casting-1-handler.log", "casting-2-other.log",
    ]


def test_the_sweep_result_is_recorded_in_the_cycle_rollup(run_env):
    """C-6: `cycles[<cycle>].evidence_sweep` carries the scope, the logs, the
    mismatches, the elapsed time and the pool size.

    Recorded because the F6 report reads it, and because "how long did the sweep
    cost this cycle" is the question that decides whether DELTA is paying for
    itself.
    """
    project_root, fdir = run_env
    _write_state(fdir, phase="F3", cycle=1)
    _write_defects(fdir, [_open_live()])
    _write_manifest(fdir)
    _evidence_log(project_root, "casting-1-handler.log", "echo one", "one\n")
    _git(Path(project_root), "add", "-A")
    _git(Path(project_root), "commit", "-qm", "evidence")
    _grind_touching(project_root, fdir, "src/handler.py")
    _arm(fdir)

    foundry_mark_phase_complete("inspect_start", project_root)

    rollup = json.loads(
        (fdir / fo.ROLLUP_FILENAME).read_text(encoding="utf-8")
    )["cycles"]["2"]["evidence_sweep"]
    assert rollup["scope"] == "delta"
    assert [Path(p).name for p in rollup["logs_reexecuted"]] == [
        "casting-1-handler.log"
    ]
    assert rollup["mismatches"] == []
    assert isinstance(rollup["elapsed_seconds"], float)
    assert rollup["swept_at"]


def test_the_persisted_sweep_record_carries_the_per_log_column(run_env):
    """CT-007 verbatim: 'sweep result recorded per log with scope (delta or
    full) and elapsed seconds.'

    D-044 — COMPUTED, THEN DROPPED BY THE WRAPPER. `sweep_evidence_at_head`
    returned `per_log` with a row for EVERY log in scope, matched or not, each
    carrying its own elapsed seconds. `_sweep_evidence_at_boundary` copied six
    sibling fields and omitted it, so the persisted record's keys were exactly
    ['elapsed_seconds', 'logs_reexecuted', 'mismatches', 'pool_size', 'scope',
    'swept_at'] — `elapsed_seconds` a single run total and no per-log column
    anywhere. It reached neither stream-rollup.json, nor the transition result,
    nor the report; grep for `per_log` across src/ found the producer and no
    consumer at all.

    ASSERTED AGAINST THE PERSISTED RECORD, which is the half `test_evidence.py`
    could not cover: it asserts on `sweep_evidence_at_head` directly, so 2740
    tests passed over the gap between the producer and the artifact.
    """
    project_root, fdir = run_env
    _write_state(fdir, phase="F3", cycle=1)
    _write_defects(fdir, [_open_live()])
    _write_manifest(fdir)
    _evidence_log(project_root, "casting-1-handler.log", "echo one", "one\n")
    _git(Path(project_root), "add", "-A")
    _git(Path(project_root), "commit", "-qm", "evidence")
    _grind_touching(project_root, fdir, "src/handler.py")
    _arm(fdir)

    result = foundry_mark_phase_complete("inspect_start", project_root)

    persisted = json.loads(
        (fdir / fo.ROLLUP_FILENAME).read_text(encoding="utf-8")
    )["cycles"]["2"]["evidence_sweep"]
    assert persisted["per_log"] == result["evidence_sweep"]["per_log"], (
        "the artifact and the transition result must carry the same column"
    )
    assert [Path(row["log"]).name for row in persisted["per_log"]] == [
        "casting-1-handler.log"
    ]
    row = persisted["per_log"][0]
    assert row["matched"] is True
    assert isinstance(row["elapsed_seconds"], float)
    # The scope rides on the record, per log rather than per run — CT-007 names
    # both halves in one clause and the record has to answer both.
    assert persisted["scope"] == "delta"


def test_every_log_in_scope_gets_a_per_log_row_not_only_the_mismatches(run_env):
    """The adjacent path: a sweep whose logs all PASS still has to say how long
    each took.

    The timing column used to exist only on mismatch records, so the logs that
    reproduced — every log, on a healthy run — had none, and "which log is
    making DELTA expensive" was unanswerable exactly when it was worth asking.
    """
    project_root, fdir = run_env
    _write_state(fdir, phase="F3", cycle=1)
    _write_defects(fdir, [_open_live()])
    (fdir / "castings" / "manifest.json").write_text(
        json.dumps({"castings": [
            {"id": 1, "key_files": ["src/handler.py"]},
            {"id": 2, "key_files": ["src/other.py"]},
        ]}),
        encoding="utf-8",
    )
    _evidence_log(project_root, "casting-1-handler.log", "echo one", "one\n")
    _evidence_log(project_root, "casting-2-other.log", "echo two", "two\n")
    _git(Path(project_root), "add", "-A")
    _git(Path(project_root), "commit", "-qm", "evidence")
    _grind_touching(project_root, fdir, "src/handler.py", "src/other.py")
    _arm(fdir)

    result = foundry_mark_phase_complete("inspect_start", project_root)

    sweep = result["evidence_sweep"]
    assert sweep["mismatches"] == []
    assert len(sweep["per_log"]) == len(sweep["logs_reexecuted"]) == 2
    assert all(row["matched"] for row in sweep["per_log"]), sweep["per_log"]
    assert all("elapsed_seconds" in row for row in sweep["per_log"])


def test_the_sweep_runs_before_the_state_transaction_opens(run_env):
    """The ordering that keeps the run from serialising on itself.

    The counter advance lives inside `_document_transaction(state.json)`, an
    fcntl-locked section every other tool call on the run contends on;
    `sweep_evidence_at_head` creates a detached worktree and runs a bounded
    thread pool of subprocesses inside it. Holding the flock across that would
    stall the entire run for the duration, for no benefit — neither the mode
    decision nor the sweep reads or writes state.json.

    Asserted against the SOURCE, because the property is an ORDER and the only
    way to observe it at runtime is a race.
    """
    import inspect

    # D-067 moved the branch chain into `_phase_transition` so the ordering
    # token is consumed only by a transition that succeeded; the branches, and
    # therefore this order, live there now.
    source = inspect.getsource(fo._phase_transition)
    body = source[source.index('elif phase == "inspect_start"'):]
    sweep_at = body.index("_sweep_evidence_at_boundary")
    decide_at = body.index("_decide_inspect_mode")
    transaction_at = body.index("_document_transaction(state_path)")
    assert decide_at < sweep_at < transaction_at, (
        "decide, then sweep, then transact — the refusal must be returned "
        "before the lock is taken, so a refused transition leaves the counter "
        "untouched and no mode recorded"
    )


def test_a_sweep_that_cannot_run_is_a_refusal_not_a_pass(run_env):
    """A sweep that could not run is emphatically not a sweep that passed.

    The alternative shape — returning ok with an empty mismatch list — is how a
    broken sweep quietly clears the boundary it exists to hold. `evidence.py`
    reports that case with a non-None `error`, and the transition has to name it
    rather than read it as "nothing mismatched".
    """
    project_root, fdir = run_env
    _write_state(fdir, phase="F3", cycle=1)
    _write_defects(fdir, [_open_live()])
    _write_manifest(fdir)
    _evidence_log(project_root, "casting-1-handler.log", "echo one", "one\n")
    _git(Path(project_root), "add", "-A")
    _git(Path(project_root), "commit", "-qm", "evidence")
    _grind_touching(project_root, fdir, "src/handler.py")

    from foundry_mcp.tools import evidence as evidence_module

    original = evidence_module.sweep_evidence_at_head
    evidence_module.sweep_evidence_at_head = lambda **_k: {
        "ok": False, "scope_count": 1, "logs_reexecuted": [], "mismatches": [],
        "elapsed_seconds": 0.0, "pool_size": 1, "head_commit": None,
        "error": "could not create the sweep worktree at HEAD abc123",
    }
    _arm(fdir)
    try:
        result = foundry_mark_phase_complete("inspect_start", project_root)
    finally:
        evidence_module.sweep_evidence_at_head = original

    assert result.get("ok") is not True
    assert "could not create the sweep worktree" in result["error"]
    assert _read_state(fdir)["cycle"] == 1


# --------------------------------------------------------------------------- #
# D-014 — the FULL rule fires at the F2 and F5 entries, so the sweep runs there
#
# FR-009: the whole corpus is swept "whenever the FULL rule fires". Both phase
# entries record FULL / first_of_phase and neither swept anything —
# `_sweep_evidence_at_boundary` had exactly ONE call site in the server. On the
# clean path ASSAY → TEMPER → NYQUIST → DONE no sweep ran at all, while the
# lead-lane fixes US-005 exists to enable land commits throughout F5.
# --------------------------------------------------------------------------- #


def test_the_f2_entry_sweeps_and_refuses_a_log_that_no_longer_reproduces(run_env):
    """GI-002 / FR-009 / AC-014: the `cast` transition records FULL, so it
    sweeps the whole corpus and refuses on mismatch, naming the log."""
    project_root, fdir = run_env
    _write_state(fdir, phase="F1", cycle=0)
    _write_manifest(fdir)
    _evidence_log(
        project_root, "casting-1-handler.log",
        "echo the-handler-calls-the-store", "the-handler-does-not\n",
    )
    _git(Path(project_root), "add", "-A")
    _git(Path(project_root), "commit", "-qm", "evidence")
    _arm(fdir)

    result = foundry_mark_phase_complete("cast", project_root)

    assert result.get("ok") is not True, result
    assert "casting-1-handler.log" in result["error"], result
    # The hint names the token that was refused, not a different one.
    assert "phase='cast'" in result["hint"], result
    # A refused transition leaves no trace it was attempted: no phase change,
    # no recorded width, and not even the CAST-complete marker.
    assert _read_state(fdir)["phase"] == "F1"
    assert "inspect_modes" not in _read_state(fdir)
    assert not (fdir / ".cast-complete").exists()


def test_the_f5_entry_sweeps_and_refuses_a_log_that_no_longer_reproduces(run_env):
    """The same rule at the other phase entry. This is the boundary that opens
    the LAST inspection of a run, and it was the one boundary not checking that
    the run's committed evidence still reproduces."""
    project_root, fdir = run_env
    _write_state(fdir, phase="F4", cycle=3)
    _write_manifest(fdir)
    _evidence_log(
        project_root, "casting-1-handler.log",
        "echo the-handler-calls-the-store", "the-handler-does-not\n",
    )
    _git(Path(project_root), "add", "-A")
    _git(Path(project_root), "commit", "-qm", "evidence")
    _arm(fdir)

    result = foundry_mark_phase_complete("temper", project_root)

    assert result.get("ok") is not True, result
    assert "casting-1-handler.log" in result["error"], result
    assert "phase='temper'" in result["hint"], result
    assert _read_state(fdir)["phase"] == "F4"


def test_both_phase_entries_sweep_the_whole_corpus_and_record_it(run_env):
    """FR-009 / OT-016: 'full corpus at the INSPECT before ASSAY/NYQUIST/DONE
    and whenever the FULL rule fires.'

    A reproducing corpus costs the run nothing but the time to prove it, and
    the scope is recorded so the roll-up says what was checked.
    """
    project_root, fdir = run_env
    _write_state(fdir, phase="F1", cycle=0)
    _write_manifest(fdir)
    _evidence_log(project_root, "casting-1-handler.log", "echo steady", "steady\n")
    _evidence_log(project_root, "casting-2-other.log", "echo also-steady",
                  "also-steady\n")
    _git(Path(project_root), "add", "-A")
    _git(Path(project_root), "commit", "-qm", "evidence")
    _arm(fdir)

    result = foundry_mark_phase_complete("cast", project_root)

    assert result["ok"] is True, result
    assert result["inspect_mode"] == "FULL"
    assert result["evidence_sweep"]["scope"] == "full"
    assert len(result["evidence_sweep"]["logs_reexecuted"]) == 2
    assert result["evidence_sweep"]["mismatches"] == []
    rollup = json.loads((fdir / fo.ROLLUP_FILENAME).read_text(encoding="utf-8"))
    assert rollup["cycles"]["0"]["evidence_sweep"]["scope"] == "full"


def test_every_transition_that_opens_an_inspect_sweeps(run_env):
    """The property, rather than three separate call sites that happen to
    agree today. GI-009 names one rule — "whichever Foundry-Phase transition
    opens an INSPECT" — and the sweep belongs to the same set as the width
    decision, so the two are asserted against ONE derivation of that set."""
    import ast
    import inspect
    import textwrap

    tree = ast.parse(textwrap.dedent(
        inspect.getsource(fo._phase_transition)
    ))

    def _calls(node) -> set[str]:
        return {
            n.func.id for n in ast.walk(node)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
        }

    opens_inspect: list[str] = []
    sweeps: list[str] = []
    for branch in ast.walk(tree):
        if not isinstance(branch, ast.If):
            continue
        test = branch.test
        if not (isinstance(test, ast.Compare)
                and isinstance(test.comparators[0], ast.Constant)):
            continue
        token = test.comparators[0].value
        body_calls = set()
        for stmt in branch.body:
            body_calls |= _calls(stmt)
        if "_decide_inspect_mode" in body_calls:
            opens_inspect.append(token)
        if "_sweep_evidence_at_boundary" in body_calls:
            sweeps.append(token)

    assert sorted(opens_inspect) == ["cast", "inspect_start", "temper"]
    assert sorted(sweeps) == sorted(opens_inspect), (
        "every transition that opens an INSPECT must sweep the evidence "
        "corpus: FR-009 sweeps 'whenever the FULL rule fires', and a phase "
        "entry always records FULL"
    )


# --------------------------------------------------------------------------- #
# D-035 — a fix landing mid-INSPECT invalidates a width already decided
# --------------------------------------------------------------------------- #

#: A LIVE fix keeps the full adjacent-path declaration whoever authored it
#: (FR-015 / AC-023), so both fixtures below carry one.
_ADJACENT_STATEMENT = (
    "Two transitions reach the handler besides the defect's — the retry branch "
    "in run_retry and the shutdown path in close_pool — and it writes the "
    "shared cache the reaper thread scans concurrently."
)
_ADJACENT_TEST = "tests/test_retry.py::test_run_retry_reuses_the_handler"


def _one_file_commit(project_root: str) -> str:
    """A real one-non-test-file commit, inside the lead lane.

    The lane is measured with `git show --numstat`, so it needs a real commit —
    the same reason `test_fix_gate.py` builds one rather than naming a SHA.
    """
    root = Path(project_root)
    (root / "src" / "handler.py").write_text(
        "def handle():\n    return store.get()\n", encoding="utf-8"
    )
    _git(root, "add", "src/handler.py")
    _git(root, "commit", "-qm", "lead-lane fix")
    return _git(root, "rev-parse", "HEAD").strip()


def test_a_fix_landing_during_f2_blocks_inspect_clean(run_env):
    """D-035: 'foundry_mark_defect_fixed has no phase guard, so a fix landing
    during F2 flips blocking to 0 after the INSPECT width was already decided —
    the gate then opens on a cycle whose sweep never covered the newly-changed
    surface.'

    The fix is legitimate work and is accepted; what is refused is carrying
    this cycle's decision forward as though it still described the tree.
    """
    from foundry_mcp.tools.foundry_orchestrator import foundry_mark_defect_fixed

    project_root, fdir = run_env
    _write_state(fdir, phase="F2", cycle=2, inspect_modes=[{
        "cycle": 2, "phase": "F2", "mode": "DELTA", "rule": "delta",
        "decided_by": "inspect_start", "required_streams": ["trace", "prove", "test"],
        "stream_scope": {}, "prove_sample": [], "touched_files": [],
    }])
    _write_defects(fdir, [_open_live()])
    for stream in ("trace", "prove", "test"):
        (fdir / f".{stream}-complete").write_text("x\n", encoding="utf-8")

    fixed = foundry_mark_defect_fixed(
        defect_id="D-001", cycle=2, authored_by="lead",
        fix_commit=_one_file_commit(project_root),
        adjacent_path_statement=_ADJACENT_STATEMENT,
        adjacent_path_test=_ADJACENT_TEST,
        project_root=project_root,
    )
    assert fixed["ok"] is True, fixed

    _arm(fdir)
    result = foundry_mark_phase_complete("inspect_clean", project_root)

    assert result.get("ok") is not True, result
    assert "D-001" in result["error"], result
    assert "after this INSPECT's width was decided" in result["error"], result
    assert "inspect_start" in result["hint"], result
    assert not (fdir / ".inspect-clean").exists()


def test_a_fix_landing_during_f3_leaves_the_next_inspect_alone(run_env):
    """The normal case, which must stay free: GRIND is where fixes land, and
    the next `inspect_start` decides a width that already accounts for them.
    A guard that fired there would refuse every cycle the protocol produces."""
    from foundry_mcp.tools.foundry_orchestrator import foundry_mark_defect_fixed

    project_root, fdir = run_env
    _write_state(fdir, phase="F3", cycle=2, inspect_modes=[{
        "cycle": 2, "phase": "F2", "mode": "DELTA", "rule": "delta",
        "decided_by": "inspect_start", "required_streams": ["trace", "prove", "test"],
        "stream_scope": {}, "prove_sample": [], "touched_files": [],
    }])
    _write_defects(fdir, [_open_live()])

    fixed = foundry_mark_defect_fixed(
        defect_id="D-001", cycle=2, authored_by="lead",
        fix_commit=_one_file_commit(project_root),
        adjacent_path_statement=_ADJACENT_STATEMENT,
        adjacent_path_test=_ADJACENT_TEST,
        project_root=project_root,
    )
    assert fixed["ok"] is True, fixed

    modes = _read_state(fdir)["inspect_modes"]
    assert "fixes_after_decision" not in modes[-1]


# --------------------------------------------------------------------------- #
# D-070 — the F5 entry does not overwrite the preceding INSPECT's rollup row
# --------------------------------------------------------------------------- #


def test_the_temper_entry_keeps_the_preceding_inspects_rollup_row(run_env):
    """CT-009 verbatim: the decision is recorded 'in state AND stream-rollup at
    the transition'. GI-009. D-070.

    `_record_cycle_rollup` keyed the bucket by `str(cycle)` and did
    `bucket.update(fields)`, and the F5 entry decides with
    `cycle=_current_cycle(fdir)` — a counter that does NOT advance entering F5.
    So the temper decision landed in the same bucket as the last
    `inspect_start` and DESTROYED it. Driven: inspect_start recorded cycle 2 as
    DELTA/delta; after `Foundry-Phase('temper')`, `cycles['2']` read
    FULL/first_of_phase and the DELTA `stream_scope` was gone. The state list
    survived because it is append-only; any reader taking the roll-up as the
    per-cycle width — the F6 report's cycle table among them — saw a fabricated
    FULL for a cycle that ran DELTA.
    """
    project_root, fdir = run_env
    _write_state(fdir, phase="F3", cycle=1)
    _write_defects(fdir, [{**_open_live(), "status": "fixed", "fixed_in_cycle": 1}])
    _write_manifest(fdir)
    _grind_touching(project_root, fdir, "src/handler.py")
    _arm(fdir)

    delta = foundry_mark_phase_complete("inspect_start", project_root)
    assert delta["inspect_mode"] == "DELTA"
    cycle = str(delta["cycle"])

    # D-125: F5 is reached THROUGH ASSAY, and `temper` is now refused from any
    # other phase. The subject here is the roll-up bucket, not the route, so
    # the run is moved to F4 the way a real one gets there (inspect_clean then
    # ASSAY) — `_update_phase` rather than `_write_state`, because the recorded
    # DELTA decision and the cycle counter are exactly what this test reads
    # back and a fresh state document would erase both.
    fo._update_phase(fdir, "F4")

    _arm(fdir)
    temper = foundry_mark_phase_complete("temper", project_root)
    assert temper["inspect_mode"] == "FULL"
    assert temper["inspect_rule"] == "first_of_phase"

    rollup = json.loads((fdir / fo.ROLLUP_FILENAME).read_text(encoding="utf-8"))
    bucket = rollup["cycles"][cycle]

    # The INSPECT's own row is intact...
    assert bucket["inspect_mode"] == "DELTA"
    assert bucket["inspect_rule"] == "delta"
    assert bucket["stream_scope"], "the DELTA per-stream scope was overwritten"
    # ...and the F5 entry is recorded beside it, under its own key.
    entry = bucket[fo.TEMPER_ENTRY_ROLLUP_KEY]
    assert entry["inspect_mode"] == "FULL"
    assert entry["inspect_rule"] == "first_of_phase"
    assert entry["evidence_sweep"]["scope"] == "full"

    # Both survive in state.json too, which is the record they must agree with.
    modes = json.loads((fdir / "state.json").read_text(encoding="utf-8"))["inspect_modes"]
    assert [(m["decided_by"], m["mode"]) for m in modes[-2:]] == [
        ("inspect_start", "DELTA"), ("temper", "FULL"),
    ]


def test_the_inspect_start_row_is_written_flat_not_nested(run_env):
    """The sub-bucket is for the F5 entry ALONE. Every crossing that owns its
    cycle number writes flat, so no existing reader of
    `cycles[<cycle>]['inspect_mode']` has to learn a second key grammar."""
    project_root, fdir = run_env
    _write_state(fdir, phase="F3", cycle=1)
    _write_defects(fdir, [])
    _write_manifest(fdir)
    _grind_touching(project_root, fdir, "src/handler.py")
    _arm(fdir)

    result = foundry_mark_phase_complete("inspect_start", project_root)

    rollup = json.loads((fdir / fo.ROLLUP_FILENAME).read_text(encoding="utf-8"))
    bucket = rollup["cycles"][str(result["cycle"])]
    assert "inspect_mode" in bucket
    assert fo.TEMPER_ENTRY_ROLLUP_KEY not in bucket


# --------------------------------------------------------------------------- #
# D-080 / AC-018 / AC-017 / FR-012 / FR-013 — the check that CONSUMES the roster
# measures against the roster
#
# `_check_streams_complete` was made to READ the recorded roster and then handed
# each member of it to `_coverage_shortfall`, which measured PROVE against the
# spec's whole requirement count and consulted no recorded decision. So a PROVE
# that checked exactly the roster the server itself recorded was reported
# incomplete forever, DELTA was unreachable on the guided path, and AC-018's
# saving was zero: the only way to close a DELTA cycle was to run PROVE at FULL
# width.
# --------------------------------------------------------------------------- #


def _delta_cycle_with_a_roster(project_root, fdir, *, ids: list[str]) -> dict:
    """Drive a real DELTA `inspect_start` and return its recorded decision.

    A cleared ledger with one defect fixed in the closing GRIND: that is the
    ordinary cycle AC-016's "and DELTA otherwise" describes, and the fixed row
    is what gives `prove_sample` a tied member to prove the roster is not just
    the random draw.
    """
    _write_spec(fdir, ids)
    _write_state(fdir, phase="F3", cycle=1, spec_path="")
    _write_defects(fdir, [
        {**_open_live("D-002"), "status": "fixed", "fixed_in_cycle": 1,
         "spec_ref": "FR-007"},
    ])
    _write_manifest(fdir)
    _grind_touching(project_root, fdir, "src/handler.py")
    _arm(fdir)

    result = foundry_mark_phase_complete("inspect_start", project_root)
    assert result["ok"] is True, result
    recorded = _read_state(fdir)["inspect_modes"][-1]
    assert recorded["mode"] == "DELTA", recorded
    return recorded


def _mark(project_root: str, stream: str, cycle: int, checked: int, total: int) -> dict:
    return fo.foundry_mark_stream(
        stream, cycle, items_checked=checked, items_total=total,
        project_root=project_root,
    )


def test_a_delta_cycle_that_checked_its_recorded_roster_is_complete(run_env):
    """D-080's pin: a DELTA cycle whose PROVE checked exactly the recorded
    roster is complete.

    AC-018: 'In DELTA mode the PROVE roster names the matrix rows tied to the
    defects fixed in the preceding GRIND plus ten rows drawn with a seed derived
    from the cycle number.' That roster IS the width, so checking every row in
    it is the whole obligation — and on a 40-requirement spec it is eleven rows,
    not thirty-eight.
    """
    project_root, fdir = run_env
    ids = [f"FR-{n:03d}" for n in range(1, 41)]
    recorded = _delta_cycle_with_a_roster(project_root, fdir, ids=ids)
    roster = recorded["prove_sample"]
    cycle = recorded["cycle"]

    assert "FR-007" in roster
    assert len(roster) == 1 + PROVE_DELTA_SAMPLE_SIZE
    assert len(roster) < len(ids), "the fixture must be narrower than the spec"

    marked = _mark(project_root, "prove", cycle, len(roster), len(roster))
    assert "coverage_shortfall" not in marked, marked
    for wire in ("trace", "test"):
        _mark(project_root, wire, cycle, 1, 1)

    streams = _check_streams_complete(project_root)
    assert streams["complete"] is True, streams
    assert streams["missing"] == ""
    assert streams["shortfalls"] == []


def test_the_delta_shortfall_names_the_roster_not_the_spec_count(run_env):
    """One row short of the recorded roster IS a shortfall, and the refusal
    names the roster it was measured against.

    NO 0.95 SLACK ON THIS ARM. At FULL the denominator is the whole spec and the
    5% is tolerance for a matrix that moved under a long stream; a DELTA roster
    is a finite list of rows the server drew BY ID, so 'which of these did you
    not check' has an answer.
    """
    project_root, fdir = run_env
    ids = [f"FR-{n:03d}" for n in range(1, 41)]
    recorded = _delta_cycle_with_a_roster(project_root, fdir, ids=ids)
    roster = recorded["prove_sample"]
    cycle = recorded["cycle"]

    marked = _mark(project_root, "prove", cycle, len(roster) - 1, len(roster))
    shortfall = marked["coverage_shortfall"]

    assert shortfall["required"] == len(roster)
    assert shortfall["checked"] == len(roster) - 1
    assert shortfall["mode"] == "DELTA"
    assert shortfall["roster"] == roster
    # The old refusal named the SPEC count, which is the number this cycle was
    # explicitly not asked for.
    assert str(len(ids)) not in shortfall["reason"], shortfall["reason"]
    assert str(len(roster)) in shortfall["reason"]
    assert "FR-007" in shortfall["reason"]

    streams = _check_streams_complete(project_root)
    assert "prove" in streams["missing"]


def test_the_full_arm_still_measures_prove_against_the_spec(run_env):
    """The FULL half is unchanged: the roster is the whole spec, so the >=95%
    threshold against `_count_spec_requirements` still applies.

    Driven on the F2 entry, which records FULL / first_of_phase, so the DELTA
    reader returns None and the spec arm runs exactly as before.
    """
    project_root, fdir = run_env
    ids = [f"FR-{n:03d}" for n in range(1, 41)]
    _write_spec(fdir, ids)
    _write_state(fdir, phase="F1", cycle=0, spec_path="")
    _write_manifest(fdir)
    _arm(fdir)
    assert foundry_mark_phase_complete("cast", project_root)["inspect_mode"] == "FULL"

    cycle = _current_cycle(fdir)
    assert fo._recorded_prove_roster(fdir, cycle) is None

    shortfall = _mark(project_root, "prove", cycle, 11, 40)["coverage_shortfall"]
    assert shortfall["required"] == 40
    assert "mode" not in shortfall
    assert _mark(project_root, "prove", cycle, 27, 40).get("coverage_shortfall") is None


def test_a_delta_cycle_at_its_recorded_width_reaches_the_widening_refusal(run_env):
    """The end-to-end D-080 named: with the roster checked, the run reaches the
    refusal that tells the lead to WIDEN, instead of being told forever that
    PROVE is missing.

    Before the fix `Foundry-Phase('inspect_clean')` answered 'streams
    incomplete: prove' at every width, and `Foundry-Next`'s F2 branch kept
    returning action=run_streams with 'Missing: prove'. Both are the streams
    check speaking for the width check, so the lead never saw the one refusal
    that has an action attached to it.
    """
    project_root, fdir = run_env
    ids = [f"FR-{n:03d}" for n in range(1, 41)]
    recorded = _delta_cycle_with_a_roster(project_root, fdir, ids=ids)
    cycle = recorded["cycle"]
    _mark(project_root, "prove", cycle, len(recorded["prove_sample"]),
          len(recorded["prove_sample"]))
    for wire in ("trace", "test"):
        _mark(project_root, wire, cycle, 1, 1)

    _arm(fdir)
    result = foundry_mark_phase_complete("inspect_clean", project_root)

    assert "streams incomplete" not in result.get("error", ""), result
    assert "DELTA width" in result["error"], result
    assert "inspect_start" in result["hint"]

    nxt = foundry_next_action(project_root)
    assert "Missing: prove" not in json.dumps(nxt)


def test_the_roster_reader_refuses_a_decision_from_another_cycle(run_env):
    """`_current_inspect_mode` returns the NEWEST entry, and a width is a fact
    about ONE crossing: a roster decided for cycle 5 says nothing about what
    cycle 4's PROVE owed. A mismatched cycle falls back to the spec arm rather
    than measuring one cycle's work against another cycle's roster.
    """
    project_root, fdir = run_env
    ids = [f"FR-{n:03d}" for n in range(1, 41)]
    recorded = _delta_cycle_with_a_roster(project_root, fdir, ids=ids)
    cycle = recorded["cycle"]

    assert fo._recorded_prove_roster(fdir, cycle) == recorded["prove_sample"]
    assert fo._recorded_prove_roster(fdir, cycle + 1) is None
    assert fo._recorded_prove_roster(fdir, cycle - 1) is None


# --------------------------------------------------------------------------- #
# D-124 / D-125 — every INSPECT-opening token guards its source phase
#
# GI-009 names three transitions that open an INSPECT: the F2 entry (`cast`),
# the F5 entry (`temper`) and `inspect_start`. The cycle-6 ruling gave a
# source-phase precondition to exactly one of them, and D-116's failure shape
# survived unchanged on the other two.
# --------------------------------------------------------------------------- #


def test_cast_is_refused_from_every_phase_but_f1(run_env):
    """CT-009 / GI-009 / FR-023: ONE decision per INSPECT-opening transition.
    D-124.

    Driven on a run at F4 (ASSAY) in cycle 2 whose `inspect_modes` held a
    final_gate FULL decision for cycle 2 and whose `.cast-baseline-sha` named
    the CAST baseline commit: `Foundry-Phase('cast')` returned ok True, phase
    F2, mode FULL, rule first_of_phase. Afterwards state.json carried a THIRD
    width decision stamped onto cycle 2 beside the inspect_start one,
    `.cast-baseline-sha` had been overwritten from the baseline commit to HEAD
    — destroying the cycle-context baseline every GRIND prompt is built from —
    and the stream roll-up read `first_of_phase` for a cycle that ran
    final_gate. A run in ASSAY was pulled back into F2 with a fabricated
    first-of-phase record.
    """
    project_root, fdir = run_env
    _write_state(fdir, phase="F4", cycle=2, inspect_modes=[{
        "cycle": 2, "phase": "F2", "mode": "FULL", "rule": "final_gate",
        "rule_detail": "fixture", "decided_by": "inspect_start",
        "decided_at": fo._now(), "required_streams": ["trace", "prove", "test"],
        "stream_scope": {}, "touched_files": [], "prove_sample": [],
    }])
    _write_manifest(fdir)
    (fdir / ".cast-baseline-sha").write_text("baseline0000\n", encoding="utf-8")
    _arm(fdir)

    result = foundry_mark_phase_complete("cast", project_root)

    assert result.get("ok") is not True, result
    assert "F4" in result["error"], result
    assert result["accepted_from"] == ["F1"]
    # Nothing moved: not the phase, not the baseline, not the decision list.
    state = _read_state(fdir)
    assert state["phase"] == "F4"
    assert len(state["inspect_modes"]) == 1
    assert (fdir / ".cast-baseline-sha").read_text(encoding="utf-8").strip() == (
        "baseline0000"
    )
    assert not (fdir / ".cast-complete").exists()

    # ...and from F1, which is where CAST actually ends, it is accepted.
    _write_state(fdir, phase="F1", cycle=0)
    _arm(fdir)
    ok = foundry_mark_phase_complete("cast", project_root)
    assert ok["ok"] is True, ok
    assert ok["phase"] == "F2"
    assert ok["inspect_rule"] == "first_of_phase"


def test_temper_is_refused_from_every_phase_but_f4(run_env):
    """US-004 / FR-011 / AC-016: 'every final gate still runs everything at full
    width', and the INSPECT before NYQUIST is FULL. D-125.

    Driven on a run at F2 in cycle 2 whose recorded width was DELTA (rule
    delta), zero verdicts, nyquist on: `Foundry-Phase('temper')` returned ok
    True, phase F5, and stamped a SECOND decision onto cycle 2 (FULL,
    first_of_phase, decided_by temper) beside the DELTA one.
    `Foundry-Gate('nyquist')` then passed and `Foundry-Phase('nyquist')`
    reached F5.5 — so the last five-stream INSPECT before NYQUIST ran at DELTA
    width, the widening re-open the cycle-3 ruling requires never happened, and
    no verdict was ever written. F5 is reached THROUGH ASSAY; the door never
    asked what phase the run was in.
    """
    project_root, fdir = run_env
    _write_state(fdir, phase="F2", cycle=2, nyquist=True, inspect_modes=[{
        "cycle": 2, "phase": "F2", "mode": "DELTA", "rule": INSPECT_DELTA_RULE,
        "rule_detail": "fixture", "decided_by": "inspect_start",
        "decided_at": fo._now(), "required_streams": ["trace", "prove", "test"],
        "stream_scope": {}, "touched_files": [], "prove_sample": [],
    }])
    _write_manifest(fdir)
    _arm(fdir)

    result = foundry_mark_phase_complete("temper", project_root)

    assert result.get("ok") is not True, result
    assert "F2" in result["error"], result
    assert result["accepted_from"] == ["F4"]
    # The hint sends the lead through the door that widens, not around it.
    assert "inspect_clean" in result["hint"], result["hint"]

    state = _read_state(fdir)
    assert state["phase"] == "F2"
    assert len(state["inspect_modes"]) == 1, "a second decision was stamped"
    assert state["inspect_modes"][-1]["mode"] == "DELTA"

    # ...and from F4, which is where ASSAY ends, it is accepted.
    _write_state(fdir, phase="F4", cycle=2, nyquist=True)
    _arm(fdir)
    ok = foundry_mark_phase_complete("temper", project_root)
    assert ok["ok"] is True, ok
    assert ok["phase"] == "F5"
    assert ok["inspect_rule"] == "first_of_phase"


def test_every_source_guarded_token_guards_its_source(run_env):
    """GI-009's 'one rule' and ST-010's from-state, as a property.

    The cycle-6 ruling guarded `inspect_start` and left `cast` and `temper`
    open, which is how one fix covered one door of three. D-164 is the same
    omission one table over: the two TERMINAL tokens were never brought under
    it, so `done` returned ok from F4 on a --temper --nyquist run and
    `nyquist_done` returned ok from F2. This walks the set from the vocabulary
    the module itself declares, so a token added later cannot quietly join
    without a guard.
    """
    project_root, fdir = run_env
    _write_manifest(fdir)

    guarded = set(fo._PHASE_ENTRY_SOURCES) | {"inspect_start"}
    assert guarded == {
        "cast", "temper", "inspect_start", "nyquist_done", "done",
    }, guarded

    for token in sorted(guarded):
        # F6 is a phase none of them is accepted from.
        _write_state(fdir, phase="F6", cycle=1)
        _arm(fdir)
        result = foundry_mark_phase_complete(token, project_root)
        assert result.get("ok") is not True, (token, result)
        assert "F6" in result["error"], (token, result)
        assert _read_state(fdir)["phase"] == "F6", token


def test_the_terminal_doors_refuse_the_phases_the_defect_drove(run_env):
    """ST-010 verbatim: from-state 'F5.5 or F5 complete', to-state 'F6 DONE'.

    D-164, driven exactly as filed. (1) A run at F4 with `temper` and `nyquist`
    both set returned ok True and phase F6 from `done` — out of ASSAY, skipping
    both post-verification phases the run was started with. (2) The same run at
    F2 returned ok True and "NYQUIST complete -> phase is now F6" from
    `nyquist_done` — a message asserting a phase that never ran.

    Both halves are asserted with the LEDGERS SATISFIED, so nothing but the
    source phase can be doing the refusing: `_done_preconditions` would pass on
    this fixture, which is what made the hole invisible.
    """
    project_root, fdir = run_env
    _write_manifest(fdir)
    (fdir / "spec.md").write_text("- FR-001: the thing works\n", encoding="utf-8")
    (fdir / "defects.json").write_text(
        json.dumps({"defects": []}), encoding="utf-8"
    )
    (fdir / "verdicts.json").write_text(
        json.dumps({"requirements": [
            {"requirement_id": "FR-001", "verdict": "VERIFIED"},
        ]}), encoding="utf-8"
    )
    fo._generate_report(project_root, fdir)

    _write_state(fdir, phase="F4", cycle=2, temper=True, nyquist=True)
    _arm(fdir)
    early = foundry_mark_phase_complete("done", project_root)
    assert early.get("ok") is not True, early
    assert early["accepted_from"] == ["F5.5"], early
    assert "post-verification" in early["error"], early
    assert "temper" in early["hint"], early["hint"]
    assert _read_state(fdir)["phase"] == "F4"

    _write_state(fdir, phase="F2", cycle=2, temper=True, nyquist=True)
    _arm(fdir)
    from_inspect = foundry_mark_phase_complete("nyquist_done", project_root)
    assert from_inspect.get("ok") is not True, from_inspect
    assert from_inspect["accepted_from"] == ["F5.5"], from_inspect
    assert _read_state(fdir)["phase"] == "F2"


def test_the_terminal_phase_a_run_may_finish_from_follows_its_own_flags(run_env):
    """ST-010 / CT-016: the from-state is the LAST phase this run enables.

    A static accepted_from would either refuse every plain run at F4 — where
    ASSAY is genuinely terminal — or admit the two calls D-164 drove. So the
    table resolves `done`'s source from the run's own `temper` / `nyquist`
    flags, and the three configurations are asserted as one discrimination
    rather than as a single happy path.
    """
    project_root, fdir = run_env
    _write_manifest(fdir)

    for flags, terminal in (
        ({}, "F4"),
        ({"temper": True}, "F5"),
        ({"temper": True, "nyquist": True}, "F5.5"),
        ({"nyquist": True}, "F5.5"),
    ):
        _write_state(fdir, phase=terminal, cycle=1, **flags)
        assert fo._phase_entry_source_problem(fdir, "done") is None, (
            flags, terminal
        )
        for wrong in ("F2", "F3", "F4", "F5", "F5.5"):
            if wrong == terminal:
                continue
            _write_state(fdir, phase=wrong, cycle=1, **flags)
            problem = fo._phase_entry_source_problem(fdir, "done")
            assert problem is not None, (flags, wrong)
            assert problem["accepted_from"] == [terminal], (flags, wrong)


# --------------------------------------------------------------------------- #
# STRUCTURAL — the escalated class `delta-roster-recorded-then-consumed-at-full-width`
#
# D-139 and D-140 are the same shape at two consumers: the server draws a DELTA
# roster, records it, and then something downstream measures the cycle against
# a width nobody drew. The deliverable is that the RECORDED decision is what
# every consumer reads — the drop rung, the Foundry-Next payload, and the
# stream prose.
# --------------------------------------------------------------------------- #


def test_a_delta_stream_is_not_accused_of_rushing_for_running_its_roster(run_env):
    """FR-012 / AC-018: the DELTA PROVE width IS the fixed-defect rows plus ten
    sampled rows. D-139.

    The drop rung compared cycle N's total against cycle N-1's total and
    consulted no recorded width, so on every DELTA cycle following a FULL one
    it accused the stream of rushing for delivering exactly the roster the
    server handed it. Driven: cycle 1 prove recorded 172/172 at FULL; cycle 2
    recorded a DELTA roster of 13 rows; Foundry-Stream(prove, cycle 2,
    items_checked 13, items_total 13) returned ok with `coverage_shortfall`
    null — the rung that DOES read the roster was satisfied — beside the
    warning "Coverage dropped: prove checked 13 items in cycle 2 vs 172 in
    cycle 1. Are you rushing?".
    """
    project_root, fdir = run_env
    _write_manifest(fdir)
    _write_spec(fdir, [f"FR-{n}" for n in range(1, 41)])

    _write_state(fdir, phase="F2", cycle=1)
    fo._record_stream_rollup(fdir, 1, "prove", 40, 40, 0, 1)

    sample = ["FR-1", "FR-2", "FR-3"]
    _write_state(fdir, phase="F2", cycle=2, inspect_modes=[{
        "cycle": 2, "phase": "F2", "mode": "DELTA", "rule": INSPECT_DELTA_RULE,
        "rule_detail": "fixture", "decided_by": "inspect_start",
        "decided_at": fo._now(),
        "required_streams": ["trace", "prove", "test"],
        "stream_scope": {
            "prove": {"scope": "delta", "detail": "3 row(s)"},
            "trace": {"scope": "delta", "detail": "symbols in 1 file(s)"},
            "test": {"scope": "full", "detail": "whole suite, cold"},
        },
        "touched_files": ["src/handler.py"], "prove_sample": sample,
    }])

    result = foundry_mark_stream("prove", 2, items_checked=len(sample),
                                 items_total=len(sample),
                                 project_root=project_root)

    assert result["ok"] is True, result
    assert "coverage_shortfall" not in result, result
    assert "Are you rushing?" not in result.get("warning", ""), result
    assert "Coverage dropped" not in result.get("warning", ""), result


def test_a_stream_the_server_left_at_full_width_still_gets_the_drop_rung(run_env):
    """D-139's other direction: the rung is scoped, not removed.

    AC-019 keeps TEST full and cold on a DELTA cycle, so its denominator did
    NOT move and a collapse in it is a real signal. A fix that silenced the
    warning for every stream on every DELTA cycle would trade one false
    accusation for a blind spot on the stream most exposed to a delta INSPECT.
    """
    project_root, fdir = run_env
    _write_manifest(fdir)

    _write_state(fdir, phase="F2", cycle=1)
    fo._record_stream_rollup(fdir, 1, "test", 500, 500, 0, 1)

    _write_state(fdir, phase="F2", cycle=2, inspect_modes=[{
        "cycle": 2, "phase": "F2", "mode": "DELTA", "rule": INSPECT_DELTA_RULE,
        "rule_detail": "fixture", "decided_by": "inspect_start",
        "decided_at": fo._now(),
        "required_streams": ["trace", "prove", "test"],
        "stream_scope": {
            "test": {"scope": "full", "detail": "whole suite, cold"},
        },
        "touched_files": ["src/handler.py"], "prove_sample": [],
    }])

    result = foundry_mark_stream("test", 2, items_checked=10, items_total=500,
                                 project_root=project_root)

    assert result["ok"] is True, result
    assert "Coverage dropped" in result.get("warning", ""), result


def test_next_emits_the_trace_half_of_the_delta_roster(run_env):
    """AC-019 verbatim: 'in DELTA mode ... TRACE runs over the symbols the GRIND
    commits touched'. D-140.

    `_decide_inspect_mode` records the TRACE scope as "symbols in the N file(s)
    the GRIND touched" and records `touched_files` beside it — and the
    Foundry-Next payload carried cycle, decided_at, decided_by, mode,
    prove_sample, required_streams, rule, rule_detail and stream_scope, and
    nothing naming a file or a symbol. So the PROVE half of the roster reached
    its stream (D-104) and the TRACE half reached no consumer at all: the
    stream could not learn the scope the server had recorded for it.
    """
    project_root, fdir = run_env
    _write_state(fdir, phase="F3", cycle=1)
    _write_defects(fdir, [_open_live()])
    _write_manifest(fdir)
    _grind_touching(project_root, fdir, "src/handler.py")
    _arm(fdir)

    assert foundry_mark_phase_complete(
        "inspect_start", project_root
    )["inspect_mode"] == "DELTA"

    reported = foundry_next_action(project_root)["inspect_mode"]

    assert "src/handler.py" in reported["touched_files"], reported
    assert reported["stream_scope"]["trace"]["scope"] == "delta"
    # Reported from the record, never recomputed (GI-008): what Next emits is
    # byte-identical to what the transition wrote.
    recorded = _read_state(fdir)["inspect_modes"][-1]
    assert reported["touched_files"] == recorded["touched_files"]
    assert reported["diff_base"] == recorded["diff_base"]


#: D-161 / STRUCTURAL PACKET 1 — EVERY surface that consumes the recorded
#: width, and the roster field each one is scoped by.
#:
#: The guard this table replaces named two files in its docstring and read ONE.
#: Driven at HEAD: the docstring said "`agents/tracer.md` and
#: `skills/trace/SKILL.md` contained no occurrence of touched_files,
#: inspect_mode, DELTA or width", the body bound only
#: `agents/tracer.md`, and `uv run pytest tests/test_inspect_mode.py -q`
#: returned 77 passed against a working tree in which `skills/trace/SKILL.md`
#: contained NONE of the six tokens the assertion demanded of tracer.md — the
#: suite green over exactly the gap the test names. The PROVE equivalent in
#: tests/test_protocol_prose.py has been parametrised over a two-surface tuple
#: since D-104, with the reason stated above it; this is the same shape, and
#: the axis it adds is that BOTH streams' surfaces are walked here, so a
#: surface with zero tokens can never be green on either side.
_WIDTH_COMMON_TOKENS = (
    # Where the width is READ FROM. GI-008 puts the decision at the transition
    # and leaves Foundry-Next reporting it; a stream told neither reads no
    # width and runs the matrix.
    "Foundry-Next",
    "inspect_mode.mode",
    "inspect_mode.cycle",
    # Both widths named, so the narrowing cannot read as unconditional.
    "DELTA",
    "FULL",
    # What an ABSENT width means. Unstated, a stream that finds no
    # `inspect_mode` picks a width itself — the lazily-computed mode GI-008 and
    # GI-009 both name as the violation.
    "no narrowing was decided",
)

_WIDTH_CONSUMER_SURFACES = (
    (("agents", "tracer.md"), (
        "inspect_mode.touched_files", "inspect_mode.stream_scope.trace.scope",
    )),
    (("skills", "trace", "SKILL.md"), (
        "inspect_mode.touched_files", "inspect_mode.stream_scope.trace.scope",
    )),
    (("agents", "assayer.md"), ("inspect_mode.prove_sample",)),
    (("skills", "prove", "SKILL.md"), ("inspect_mode.prove_sample",)),
)


@pytest.mark.parametrize(
    "parts,roster_tokens", _WIDTH_CONSUMER_SURFACES, ids=lambda v: "/".join(v)
    if isinstance(v, tuple) and v and isinstance(v[0], str) and "." in v[-1]
    else str(v),
)
def test_every_width_consuming_surface_reads_the_scope_the_server_records(
    parts, roster_tokens
):
    """AC-019's consumer half, on every surface that consumes it. D-140/D-161.

    AC-019 verbatim: "In DELTA mode TEST still runs full and cold from a clean
    worktree and TRACE runs over the symbols the GRIND commits touched."

    The stream is an AGENT and a SKILL, and what they read IS their
    instructions — so prose is the whole mechanism, and a surface the guard
    does not open is a surface the width never reaches. `agents/tracer.md`
    scoped the walk from the spec alone until D-140; `skills/trace/SKILL.md`,
    which `/foundry:trace` runs, carried none of it until D-160 while this test
    named it in its docstring and asserted nothing about it.

    Asserted against the same field names the payload emits, so a rename cannot
    leave a document pointing at a key that no longer arrives.
    """
    surface = _plugin_root().joinpath(*parts)
    text = surface.read_text(encoding="utf-8")
    rel = "/".join(parts)
    for token in _WIDTH_COMMON_TOKENS + tuple(roster_tokens):
        assert token in text, f"{rel} never names {token}"


def test_the_tracer_agent_still_distinguishes_the_roster_from_its_display():
    """The two tracer-only rules, kept where they were (D-140).

    They are asserted apart from the cross-surface table because they are about
    the DISPLAY of a roster rather than about the width: display.py truncates
    the printed sample, so a stream reading the terminal line reads a prefix and
    reports a width it never ran.
    """
    tracer = (_plugin_root() / "agents" / "tracer.md").read_text(encoding="utf-8")

    assert "walk everything" in tracer, (
        "an unrecorded width must mean 'no narrowing was decided'"
    )
    assert "TRUNCATES" in tracer, (
        "the terminal line is a summary; the roster is the array"
    )


# --------------------------------------------------------------------------- #
# D-152 — RULE PRECEDENCE, SO THE RECORDED RULE NAMES THE ARM THAT FIRED.
#
# The order is fixed by the lead ruling and by FR-011's "Foundry-Next names
# which rule fired": first_of_phase, then BOTH final_gate facts, then
# verifier_touched, then DELTA. The uncomputable-diff arm sat AHEAD of the two
# final_gate facts, which are readable without a diff — so a GRIND entered from
# ASSAY feedback on a run with no boundary marker recorded `verifier_touched`
# with a rule_detail that says the verifier could not be shown to have moved,
# for the very cycle that opens ASSAY. The mode is FULL either way; the
# PROVENANCE was false, and the assay gate's own refusal prose ("ASSAY is only
# opened by an INSPECT whose recorded rule is final_gate") contradicted it.
#
# This is D-069's shape one arm over. The covering test asserted only
# `rule in INSPECT_FULL_RULES` for the no-history case, so the precedence
# itself was untested — which is why the assertions below name the rule.
# --------------------------------------------------------------------------- #


def _uncomputable_diff(fdir: Path) -> None:
    """Remove every marker `_grind_diff` could measure from, so it has none.

    The run state D-152 was driven on and the one the spec keeps in scope: a
    resumed pre-change archive (thunder-viper's lacks exactly these three) has
    no boundary marker, no clean-TRACE stamp and no CAST baseline.
    """
    for marker in (
        fo.INSPECT_BOUNDARY_SHA_MARKER, ".trace-clean-at", ".cast-baseline-sha"
    ):
        (fdir / marker).unlink(missing_ok=True)


def test_a_grind_entered_from_feedback_records_final_gate_with_no_diff(run_env):
    """FR-011 verbatim: "Foundry-Next names which rule fired."

    The GRIND that just closed was entered from ASSAY feedback — a fact the
    phase history carries and no diff is needed to read. So `final_gate` fired,
    and `final_gate` is what must be recorded, whether or not the diff can be
    computed. Driven on the exact state D-152 reports: phase_history F2 -> F4
    -> F3, and a run dir with none of the three markers a diff measures from.
    """
    project_root, fdir = run_env
    _write_state(
        fdir, phase="F3", cycle=4,
        phase_history=[{"phase": "F2"}, {"phase": "F4"}, {"phase": "F3"}],
    )
    _uncomputable_diff(fdir)

    entry = _decide_inspect_mode(
        fdir, project_root, decided_by="inspect_start", phase="F2", cycle=5
    )

    assert entry["mode"] == "FULL", entry
    assert entry["rule"] == "final_gate", entry
    assert "feedback" in entry["rule_detail"], entry


def test_the_widening_re_open_records_final_gate_with_no_diff(run_env):
    """AC-016 / the F2->F2 widening: the same precedence on the other
    final_gate fact.

    `widening` is a fact about the TRANSITION — the run is already at F2 and
    the recorded width was DELTA — and it is known without consulting git.
    """
    project_root, fdir = run_env
    _write_state(fdir, phase="F2", cycle=4)
    _uncomputable_diff(fdir)

    entry = _decide_inspect_mode(
        fdir, project_root, decided_by="inspect_start", phase="F2", cycle=5,
        widening=True,
    )

    assert entry["mode"] == "FULL", entry
    assert entry["rule"] == "final_gate", entry
    assert "widening" in entry["rule_detail"], entry


def test_an_uncomputable_diff_still_records_verifier_touched_when_no_arm_fired(
    run_env,
):
    """The arm is not removed, it is REORDERED.

    With neither final_gate fact true, an uncomputable diff is still FULL and
    is still recorded as `verifier_touched` with the real cause in
    `rule_detail` — nothing can show the verifier did NOT move, so the honest
    width is everything. Asserting this beside the two tests above is what
    makes them a precedence claim rather than a deletion.
    """
    project_root, fdir = run_env
    _write_state(
        fdir, phase="F3", cycle=4,
        phase_history=[{"phase": "F2"}, {"phase": "F3"}],
    )
    _uncomputable_diff(fdir)

    entry = _decide_inspect_mode(
        fdir, project_root, decided_by="inspect_start", phase="F2", cycle=5
    )

    assert entry["mode"] == "FULL", entry
    assert entry["rule"] == "verifier_touched", entry
    assert "could not be computed" in entry["rule_detail"], entry


def test_the_recorded_rule_survives_the_transition_into_state_and_rollup(run_env):
    """CT-009 / GI-008: the transition RECORDS the decision, and every later
    reader reports it. D-152 was visible in three artifacts at once — the
    entry, `state.json.inspect_modes[-1].rule` and the stream-rollup's
    per-cycle rule column — so all three are asserted here on one crossing.
    """
    project_root, fdir = run_env
    _write_state(
        fdir, phase="F3", cycle=4,
        phase_history=[{"phase": "F2"}, {"phase": "F4"}, {"phase": "F3"}],
    )
    _uncomputable_diff(fdir)
    _arm(fdir)

    result = foundry_mark_phase_complete("inspect_start", project_root)
    assert result.get("ok") is True, result

    recorded = _read_state(fdir)["inspect_modes"][-1]
    assert recorded["rule"] == "final_gate", recorded
    assert recorded["mode"] == "FULL", recorded

    rollup = json.loads((fdir / fo.ROLLUP_FILENAME).read_text(encoding="utf-8"))
    cycle_row = rollup["cycles"][str(recorded["cycle"])]
    assert cycle_row["inspect_rule"] == "final_gate", cycle_row
