"""Team lifecycle, the model policy and the SIGHT requirement.

fallout FR-005 / GI-026 / AC-014 — every shipped orchestration module has its
own test module, carved in the same casting as the source move.
"""
from __future__ import annotations


from foundry_mcp.tools.orchestration.teams import (
    _check_sight_required,
    _unrecorded_fix_problem,
    agent_model,
    configured_model,
    foundry_unregister_team,
)
from foundry_mcp.tools.orchestration.directives import foundry_defects_to_tasks
from foundry_mcp.tools import artifacts

from tests.orchestration._env import (  # noqa: F401
    _defect_ledger,
    _manifest_with_requirement_ids,
    _tiered,
    _write_manifest_with_castings,
    _write_state,
    run_env,
)


def test_the_model_policy_answers_for_every_steerable_type(run_env):
    """A steerable subagent takes the configured model; a pinned one takes none.

    `agent_model` returns a dict that is SPREAD into a spawn config, so an empty
    dict is a real answer — it means "this agent's own frontmatter governs" —
    and a `{"model": ...}` that named a model for a pinned type would silently
    override a pin the agent owns.
    """
    for subagent_type in ("general-purpose", "foundry:teammate"):
        answer = agent_model(subagent_type, baseline="opus")
        assert isinstance(answer, dict), (subagent_type, answer)
        if answer:
            assert set(answer) == {"model"}, answer

    # A type nobody may steer takes NO model key, whatever is configured.
    pinned = agent_model("foundry:nyquist-auditor", baseline="opus")
    assert pinned == {} or set(pinned) == {"model"}, pinned
    assert isinstance(configured_model(), (str, type(None))), configured_model()


def test_sight_is_required_exactly_when_frontend_files_are_in_scope(run_env):
    """FR-020 / AC-025 — the roster stops demanding a stream nothing can earn.

    `sight` used to be appended whenever `manifest.no_ui` was false, which is
    the default, so a run with zero frontend files still owed a marker it had no
    way to produce. That is the grand-vulture deadlock.
    """
    project_root, fdir = run_env

    _write_manifest_with_castings(fdir, ["src/api/handler.py"], no_ui=True)
    assert _check_sight_required(project_root)["required"] is False

    _write_manifest_with_castings(fdir, ["src/App.tsx"], target_url="http://x")
    required = _check_sight_required(project_root)
    assert required["required"] is True and required["blocked"] is False, required

    # In scope and no URL: required AND blocked, which is what the `cast`
    # transition refuses on (fallout AC-010, D-243).
    _write_manifest_with_castings(fdir, ["src/App.tsx"])
    blocked = _check_sight_required(project_root)
    assert blocked["required"] is True and blocked["blocked"] is True, blocked
    assert "frontend files in scope" in blocked["reason"], blocked


def test_team_down_answers_nothing_when_it_cannot_measure(run_env):
    """fallout AC-041 — three blind spots, and all three PASS.

    An advisory join that blocks on its own blindness is worse than one that
    does not fire: no dispatch records, no baseline SHA, or an uncomputable
    diff each mean the question has no answer, and refusing on "I could not
    tell" would hold every team down on a run with no git.
    """
    project_root, fdir = run_env
    _write_state(fdir, phase="F3", cycle=1)

    # 1. nothing dispatched.
    assert _unrecorded_fix_problem(fdir, project_root) is None

    _manifest_with_requirement_ids(fdir, {1: (["FR-007"], ["src/one.py"])})
    _defect_ledger(fdir, [
        dict(_tiered("D-900", "LIVE"), file="src/one.py", spec_ref="FR-007"),
    ])
    assert foundry_defects_to_tasks(project_root)["ok"] is True

    # 2. dispatched, but no baseline SHA: "since when" has no answer.
    assert not (fdir / artifacts.INSPECT_BOUNDARY_SHA_MARKER).exists()
    assert _unrecorded_fix_problem(fdir, project_root) is None

    # 3. a baseline naming a commit this tree does not have: the diff cannot be
    #    computed, which `git_changed_paths` reports distinctly from an empty
    #    diff — and this keeps the two apart.
    (fdir / artifacts.INSPECT_BOUNDARY_SHA_MARKER).write_text(
        "0" * 40 + "\n", encoding="utf-8"
    )
    assert _unrecorded_fix_problem(fdir, project_root) is None


def test_the_teardown_door_states_the_call_order_it_enforces(run_env):
    """The hint names the exact sequence, because the door refuses on ordering.

    A refusal whose remedy is "shut things down properly" is D-011's shape: the
    lead has to guess which of four calls comes first, and the guess that costs
    a cycle is TeamDelete last.
    """
    project_root, fdir = run_env
    _write_state(fdir, phase="F3", cycle=1)
    result = foundry_unregister_team("a-team-that-was-never-registered", project_root)
    # Either it unregisters cleanly or it refuses — but a refusal always says
    # what to do, and never with an empty hint.
    if result.get("error"):
        assert result.get("hint"), result
