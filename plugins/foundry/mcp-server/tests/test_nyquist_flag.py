"""The ``--nyquist`` flag reaches F5.5 — the dead-flag chain, end to end.

``--nyquist`` was READ but never WRITTEN on the MCP path, so F5.5 NYQUIST was
unreachable: ``foundry_init`` had no ``nyquist`` parameter, its state dict wrote
no ``nyquist`` key, and every ``state.get("nyquist", False)`` therefore read
False forever. ``Foundry-Gate(phase="nyquist")`` and
``Foundry-Phase(phase="nyquist_done")`` were advertised in the tool schema with
no implementation behind either.

The chain this module pins, one link per section:

  invocation   ``setup-foundry.sh`` echoes ``FOUNDRY_NYQUIST=true``
  threading    ``commands/start.md`` tells the lead to pass ``nyquist=`` on
               Foundry-Init (the link whose absence made the rest moot)
  schema       Foundry-Init declares ``nyquist``; Foundry-Phase advertises the
               ``nyquist`` / ``nyquist_done`` tokens
  persistence  ``foundry_init(nyquist=True)`` writes the key to BOTH state.json
               and castings/manifest.json, mirroring foundry.sh:178 / :196
  gate         ``Foundry-Gate(phase="nyquist")`` has a branch and enforces
               all-VERIFIED plus the flag itself
  phase        ``Foundry-Phase(phase="nyquist")`` enters F5.5 and
               ``nyquist_done`` leaves it for F6
  transition   ``_compute_next_action`` routes F4 → F5.5 (no --temper) and
               F5 → F5.5 (with --temper), and is byte-identical when unset

The "when unset" assertions matter as much as the positive ones: the flag is
opt-in, so a run that did not ask for F5.5 must behave exactly as it did before
this existed.

Run with the rest of the suite: ``uv run --with pytest pytest`` from
``plugins/foundry/mcp-server``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from foundry_mcp.tools import foundry_orchestrator as fo
from foundry_mcp.tools import foundry_state
from foundry_mcp.tools.foundry import foundry_init
from foundry_mcp.tools.foundry_orchestrator import (
    _compute_next_action,
    foundry_gate,
    foundry_mark_phase_complete,
)


# tests/test_nyquist_flag.py -> parents: [0]=tests, [1]=mcp-server, [2]=foundry.
_FOUNDRY_ROOT = Path(__file__).resolve().parents[2]
_START_MD = _FOUNDRY_ROOT / "commands" / "start.md"
_SETUP_SH = _FOUNDRY_ROOT / "scripts" / "setup-foundry.sh"


# --------------------------------------------------------------------------- #
# Fixtures & helpers
# --------------------------------------------------------------------------- #


@pytest.fixture
def run_env(tmp_path, monkeypatch):
    """Activate a foundry run under tmp_path; yield (project_root, fdir).

    Mirrors ``test_orchestrator_gates.run_env`` — ``_check_active_teams`` is
    patched inactive so gate and router logic never depend on the ambient tmux
    session or ~/.claude/teams state.
    """
    project_root = tmp_path
    run_name = "nyquist-test-run"
    fdir = project_root / "foundry-archive" / run_name
    (fdir / "castings").mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(
        fo,
        "_check_active_teams",
        lambda _pr: {"active": False, "teams": [], "live_panes": []},
    )

    foundry_state.set_active_run(run_name)
    try:
        yield str(project_root), fdir
    finally:
        foundry_state.clear_active_run()


@pytest.fixture(autouse=True)
def _isolate_active_run():
    """``foundry_init`` sets the active run as a module-level side effect."""
    foundry_state.clear_active_run()
    yield
    foundry_state.clear_active_run()


def _write_state(fdir: Path, phase: str, **extra) -> None:
    state = {"phase": phase}
    state.update(extra)
    (fdir / "state.json").write_text(json.dumps(state), encoding="utf-8")


def _write_verdicts(fdir: Path, verdicts: list[str]) -> None:
    rows = [
        {"id": f"FR-{i}", "verdict": v, "evidence": "x"}
        for i, v in enumerate(verdicts, start=1)
    ]
    (fdir / "verdicts.json").write_text(
        json.dumps({"requirements": rows}), encoding="utf-8"
    )


def _arm_ordering_token(fdir: Path) -> None:
    """Simulate a preceding Foundry-Next so a gate's ordering check passes."""
    (fdir / ".next-action-called").write_text(f"{fo._now()}\n", encoding="utf-8")


def _read_state(fdir: Path) -> dict:
    return json.loads((fdir / "state.json").read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- #
# Invocation & threading — the link whose absence made the rest unreachable
# --------------------------------------------------------------------------- #


def test_setup_script_still_echoes_the_flag() -> None:
    """The value the lead threads has to exist to be threaded."""
    text = _SETUP_SH.read_text(encoding="utf-8")
    assert "FOUNDRY_NYQUIST=$NYQUIST" in text


def test_start_md_threads_nyquist_into_foundry_init() -> None:
    """start.md must tell the lead to pass ``nyquist=`` on Foundry-Init.

    Without this sentence the parameter exists but nothing ever supplies it,
    which is the shape the original bug had: readable, never written.
    """
    text = _START_MD.read_text(encoding="utf-8")
    assert "nyquist=<FOUNDRY_NYQUIST" in text, (
        "start.md does not thread FOUNDRY_NYQUIST into Foundry-Init; the flag "
        "is dead again even though the parameter exists"
    )
    assert "Foundry-Init" in text


# --------------------------------------------------------------------------- #
# Schema — the declared surface matches the implemented one
# --------------------------------------------------------------------------- #


def test_init_schema_exposes_nyquist_property() -> None:
    from foundry_mcp import server

    src = Path(server.__file__).read_text(encoding="utf-8")
    assert '"nyquist": {"type": "boolean"' in src


@pytest.mark.parametrize("token", ["nyquist", "nyquist_done"])
def test_phase_tool_advertises_both_nyquist_tokens(token: str) -> None:
    """Advertised tokens must be implemented — the original defect was a
    schema that offered ``nyquist_done`` with no branch behind it."""
    from foundry_mcp import server

    src = Path(server.__file__).read_text(encoding="utf-8")
    assert f'"{token}"' in src


@pytest.mark.parametrize("token", ["nyquist", "nyquist_done"])
def test_advertised_phase_tokens_are_not_rejected(run_env, token: str) -> None:
    """Every advertised token resolves to a real branch, not the else-error."""
    project_root, fdir = run_env
    _write_state(fdir, "F5" if token == "nyquist" else "F5.5", nyquist=True)
    _arm_ordering_token(fdir)

    result = foundry_mark_phase_complete(token, project_root=project_root)

    assert "error" not in result, result


def test_dispatch_lambda_forwards_nyquist(tmp_path, monkeypatch) -> None:
    """The Foundry-Init dispatch lambda threads ``args['nyquist']`` through to
    the persisted state (exercises the real server dispatch, not the helper)."""
    from foundry_mcp import server

    monkeypatch.setattr(server, "_project_root", str(tmp_path))
    result = server._DISPATCH["Foundry-Init"]({"nyquist": True})

    state = _read_state(Path(result["foundry_dir"]))
    assert state["nyquist"] is True


# --------------------------------------------------------------------------- #
# Persistence — both stores of record, mirroring the bash init
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("value", [True, False])
def test_init_persists_nyquist_to_state_and_manifest(tmp_path, value: bool) -> None:
    """foundry.sh writes ``nyquist`` into BOTH files (:178 manifest, :196
    state); the Python init path must produce a compatible pair or a run
    created here reads differently from one created by the shell."""
    result = foundry_init(nyquist=value, project_root=str(tmp_path))
    fdir = Path(result["foundry_dir"])

    assert _read_state(fdir)["nyquist"] is value
    manifest = json.loads(
        (fdir / "castings" / "manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["nyquist"] is value


def test_init_defaults_nyquist_off(tmp_path) -> None:
    """Opt-in: a run created without the flag is not a NYQUIST run."""
    result = foundry_init(project_root=str(tmp_path))
    assert _read_state(Path(result["foundry_dir"]))["nyquist"] is False


def test_init_keeps_temper_independent(tmp_path) -> None:
    """The two optional phases are separate dials — setting one must not
    imply the other."""
    result = foundry_init(temper=True, project_root=str(tmp_path))
    state = _read_state(Path(result["foundry_dir"]))
    assert state["temper"] is True
    assert state["nyquist"] is False


# --------------------------------------------------------------------------- #
# Gate — Foundry-Gate(phase="nyquist")
# --------------------------------------------------------------------------- #


def test_nyquist_gate_passes_when_verified_and_enabled(run_env) -> None:
    project_root, fdir = run_env
    _write_state(fdir, "F4", nyquist=True)
    _write_verdicts(fdir, ["VERIFIED", "VERIFIED"])
    _arm_ordering_token(fdir)

    result = foundry_gate("nyquist", project_root=project_root)

    assert result["passed"] is True, result


def test_nyquist_gate_blocks_when_a_requirement_is_unverified(run_env) -> None:
    """F5.5 locks in behavior that ASSAY verified; there is nothing to lock in
    while a requirement is still THIN/MISSING."""
    project_root, fdir = run_env
    _write_state(fdir, "F4", nyquist=True)
    _write_verdicts(fdir, ["VERIFIED", "THIN"])
    _arm_ordering_token(fdir)

    result = foundry_gate("nyquist", project_root=project_root)

    assert result["passed"] is False
    assert "not verified" in result["reason"]


def test_nyquist_gate_blocks_when_the_flag_is_off(run_env) -> None:
    """Entering F5.5 on a run that never asked for it would spawn auditors the
    invocation did not request."""
    project_root, fdir = run_env
    _write_state(fdir, "F4")
    _write_verdicts(fdir, ["VERIFIED"])
    _arm_ordering_token(fdir)

    result = foundry_gate("nyquist", project_root=project_root)

    assert result["passed"] is False
    assert "--nyquist" in result["reason"]


def test_nyquist_gate_is_a_real_branch_not_the_unknown_phase_fallback(run_env) -> None:
    """Before the fix, "nyquist" fell through to ``Unknown phase``."""
    project_root, fdir = run_env
    _write_state(fdir, "F4", nyquist=True)
    _write_verdicts(fdir, ["VERIFIED"])
    _arm_ordering_token(fdir)

    result = foundry_gate("nyquist", project_root=project_root)

    assert "Unknown phase" not in result.get("reason", "")
    checks = {c["check"] for c in result["checklist"]}
    assert "nyquist_enabled" in checks


# --------------------------------------------------------------------------- #
# Phase marks — entering and leaving F5.5
# --------------------------------------------------------------------------- #


def test_phase_nyquist_enters_f5_5(run_env) -> None:
    """The state write is what makes the F5.5 router branch reachable at all —
    ``_compute_next_action`` dispatches on ``state["phase"]``."""
    project_root, fdir = run_env
    _write_state(fdir, "F4", nyquist=True)
    _arm_ordering_token(fdir)

    result = foundry_mark_phase_complete("nyquist", project_root=project_root)

    assert result["phase"] == "F5.5"
    assert _read_state(fdir)["phase"] == "F5.5"


def test_phase_nyquist_done_leaves_for_f6(run_env) -> None:
    project_root, fdir = run_env
    _write_state(fdir, "F5.5", nyquist=True)
    _arm_ordering_token(fdir)

    result = foundry_mark_phase_complete("nyquist_done", project_root=project_root)

    assert result["phase"] == "F6"
    assert _read_state(fdir)["phase"] == "F6"


# --------------------------------------------------------------------------- #
# Transitions — the routing that was unreachable
# --------------------------------------------------------------------------- #


def test_f4_routes_to_nyquist_when_set(run_env) -> None:
    """The core regression: ASSAY passed, --nyquist set, no --temper."""
    project_root, fdir = run_env
    _write_state(fdir, "F4", nyquist=True)
    _write_verdicts(fdir, ["VERIFIED", "VERIFIED"])

    result = _compute_next_action(project_root)

    assert result["action"] == "transition_to_nyquist"
    assert "Foundry-Gate(phase='nyquist')" in result["instructions"]


def test_f4_routes_to_done_when_unset(run_env) -> None:
    """No-regression: a run without the flag takes the path it always took."""
    project_root, fdir = run_env
    _write_state(fdir, "F4")
    _write_verdicts(fdir, ["VERIFIED"])

    assert _compute_next_action(project_root)["action"] == "transition_to_done"


def test_f4_prefers_temper_when_both_are_set(run_env) -> None:
    """Ordering is F4 → F5 → F5.5 → F6, so TEMPER comes first and NYQUIST is
    reached from F5 rather than being skipped."""
    project_root, fdir = run_env
    _write_state(fdir, "F4", temper=True, nyquist=True)
    _write_verdicts(fdir, ["VERIFIED"])

    assert _compute_next_action(project_root)["action"] == "transition_to_temper"


def test_f5_points_at_nyquist_when_set(run_env) -> None:
    """The --temper --nyquist composition: without this, setting both silently
    dropped F5.5 because TEMPER's exit hardcoded the DONE gate."""
    project_root, fdir = run_env
    _write_state(fdir, "F5", temper=True, nyquist=True)

    result = _compute_next_action(project_root)

    assert result["action"] == "run_temper"
    assert "Foundry-Gate(phase='nyquist')" in result["instructions"]


def test_f5_points_at_done_when_nyquist_unset(run_env) -> None:
    project_root, fdir = run_env
    _write_state(fdir, "F5", temper=True)

    result = _compute_next_action(project_root)

    assert "Foundry-Gate(phase='done')" in result["instructions"]


def test_f5_5_emits_the_auditor_step(run_env) -> None:
    project_root, fdir = run_env
    _write_state(fdir, "F5.5", nyquist=True)

    result = _compute_next_action(project_root)

    assert result["action"] == "run_nyquist"
    assert result["details"]["agent_config"]["subagent_type"] == "foundry:nyquist-auditor"
    assert "nyquist_done" in result["instructions"]


def test_f5_5_agent_config_carries_no_model_key(run_env) -> None:
    """nyquist-auditor is not steerable and holds a sonnet frontmatter pin, so
    this site emits no model at any setting of the option (AC-004, FR-005)."""
    project_root, fdir = run_env
    _write_state(fdir, "F5.5", nyquist=True)

    config = _compute_next_action(project_root)["details"]["agent_config"]

    assert "model" not in config, config


def test_nyquist_transition_maps_to_its_gate() -> None:
    """P4 guidance advance: a passing nyquist gate must advance past the
    transition step rather than asking for the same gate again."""
    assert fo._expected_gate_for_action("transition_to_nyquist") == "nyquist"


# --------------------------------------------------------------------------- #
# The whole chain, in one pass
# --------------------------------------------------------------------------- #


def test_init_to_f5_5_end_to_end(tmp_path, monkeypatch) -> None:
    """init(nyquist=True) → F4 routes to NYQUIST → gate → phase → F5.5 step.

    Each link was individually broken before the fix; this drives them in the
    order a real run does, so a regression in any one of them fails here too.
    """
    monkeypatch.setattr(
        fo,
        "_check_active_teams",
        lambda _pr: {"active": False, "teams": [], "live_panes": []},
    )
    result = foundry_init(nyquist=True, project_root=str(tmp_path))
    fdir = Path(result["foundry_dir"])

    state = _read_state(fdir)
    state["phase"] = "F4"
    (fdir / "state.json").write_text(json.dumps(state), encoding="utf-8")
    _write_verdicts(fdir, ["VERIFIED", "VERIFIED"])

    assert _compute_next_action(str(tmp_path))["action"] == "transition_to_nyquist"

    _arm_ordering_token(fdir)
    assert foundry_gate("nyquist", project_root=str(tmp_path))["passed"] is True

    _arm_ordering_token(fdir)
    assert (
        foundry_mark_phase_complete("nyquist", project_root=str(tmp_path))["phase"]
        == "F5.5"
    )

    assert _compute_next_action(str(tmp_path))["action"] == "run_nyquist"
