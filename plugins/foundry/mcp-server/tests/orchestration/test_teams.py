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
    _repo_with_commit,
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


def test_sight_is_not_required_when_the_run_declares_no_ui(run_env):
    """fallout AC-052 / FR-055 — the flag's ONE meaning, at the gate.

    `NO_UI_MEANING` is the sentence README, `setup-foundry.sh` and
    `commands/start.md` all quote: the flag declares the run has no browsable
    UI, so the SIGHT browser audit is not part of it. This gate read it the
    other way — `no_ui` plus any UI extension in scope answered `required:
    True, blocked: True`, `_check_streams_complete` appended `sight` to the
    roster, and `_cast_preconditions` then failed at the config rung. Declaring
    a run had no UI was the one way to make the browser audit both mandatory
    and unsatisfiable.

    THE KEY FILE IS `.tsx`, and that is the whole of why this was not caught:
    every `no_ui` arrangement in this suite used `.py` files, where the
    extension scan returns "No frontend files in castings" before the flag is
    read at all. The arm needed a UI extension AND the flag together.
    """
    from foundry_mcp.tools.foundry import NO_UI_MEANING

    project_root, fdir = run_env

    _write_manifest_with_castings(fdir, ["src/App.tsx"], no_ui=True)
    declared = _check_sight_required(project_root)
    assert declared["required"] is False, declared
    assert declared["blocked"] is False, declared
    # The count is reported so the operator sees the tension between what they
    # declared and what is in scope — as a fact, never as an overrule.
    assert declared["ui_files"] == 1, declared
    assert declared["reason"] == NO_UI_MEANING, declared

    # ...and a URL does not change the answer: the flag says the audit is not
    # part of this run, and a URL is only how the audit would be reached.
    _write_manifest_with_castings(
        fdir, ["src/App.tsx"], target_url="http://localhost:3000", no_ui=True
    )
    assert _check_sight_required(project_root)["required"] is False

    # The roster the declaration feeds carries no sight stream either, which is
    # the surface an operator actually hits (the `cast` config rung reads this).
    from foundry_mcp.tools.orchestration.streams import _check_streams_complete

    _write_manifest_with_castings(fdir, ["src/App.tsx"], no_ui=True)
    assert "sight" not in _check_streams_complete(project_root)["required"]


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


def test_the_teardown_refusal_names_the_commit_that_made_the_change(run_env):
    """fallout AC-039 — "refuses naming the id AND the commit".

    It named the id, the file, and the BASELINE sha — the revision the diff is
    measured FROM, which is by construction the one commit that did not make
    the change. The touching commit was never resolved at all, because
    `git_changed_paths` runs `git diff --name-only` and that prints paths and
    no revisions; the operator was handed a SHA that could not be the answer
    and had to run the log themselves to find the one that was.

    Driven over a real repository with two commits: the baseline, and one that
    touches the dispatched defect's file. The refusal must name the second.
    """
    project_root, fdir = run_env
    _write_state(fdir, phase="F3", cycle=1)

    base = _repo_with_commit(project_root, "src/one.py", "print('before')\n")
    (fdir / artifacts.INSPECT_BOUNDARY_SHA_MARKER).write_text(
        base + "\n", encoding="utf-8"
    )

    _manifest_with_requirement_ids(fdir, {1: (["FR-007"], ["src/one.py"])})
    _defect_ledger(fdir, [
        dict(_tiered("D-900", "LIVE"), file="src/one.py", spec_ref="FR-007"),
    ])
    assert foundry_defects_to_tasks(project_root)["ok"] is True

    # Nothing has moved since the baseline, so nothing is unrecorded yet.
    assert _unrecorded_fix_problem(fdir, project_root) is None

    fix = _repo_with_commit(project_root, "src/one.py", "print('after')\n")

    problem = _unrecorded_fix_problem(fdir, project_root)
    assert problem is not None, "a commit touched the open defect's file"
    assert "D-900" in problem["reason"], problem

    # Machine-readable beside the id, so a caller need not parse prose. Compared
    # by PREFIX rather than to a hand-sliced `fix[:7]`: `--format=%h` abbreviates
    # to whatever is unambiguous in the repo, and pinning a width would make
    # this test a fact about repository size.
    named = problem["defects"][0]["commit"]
    assert len(named) >= 7 and fix.startswith(named), (named, fix)
    assert problem["commits"] == [named], problem
    # ...and in the sentence the lead reads, beside the file it belongs to.
    assert named in problem["reason"], (named, problem["reason"])
    assert f"src/one.py @ {named}" in problem["reason"], problem["reason"]
    # The baseline is still reported — it is what "since when" means — and the
    # defect was that it was the ONLY sha the refusal carried.
    assert problem["baseline_sha"] == base, problem
    assert named != base, (named, base)


def test_an_open_concern_naming_the_id_is_the_exit_the_hint_promises(run_env):
    """fallout AC-041 (D-050) — the third exit, driven both ways.

    The refusal's hint offers three ways past it and the third is "or leave it
    open and say so in the cycle's concerns". This function read the dispatch
    rows, the defect ledger, the baseline SHA and the diff, and read
    `concerns.json` never — so filing the concern the hint asks for changed
    nothing and the door refused identically. It was driven at this run's own
    cycle-1 door: two ids deliberately open as a partial fix of a ruled
    structural packet, a concern filed naming both, the same refusal returned.

    ONE ARRANGEMENT, TWO DRIVES. The same open defect, the same touching
    commit, the same baseline — the ONLY thing that differs between the two
    halves is whether an open concern of this cycle names the id, which is what
    makes this a test of the exit rather than of the arrangement.
    """
    from foundry_mcp.tools.concerns import foundry_concern

    project_root, fdir = run_env
    _write_state(fdir, phase="F3", cycle=0)

    base = _repo_with_commit(project_root, "src/one.py", "print('before')\n")
    (fdir / artifacts.INSPECT_BOUNDARY_SHA_MARKER).write_text(
        base + "\n", encoding="utf-8"
    )
    _manifest_with_requirement_ids(fdir, {
        1: (["FR-007"], ["src/one.py"]),
        2: (["FR-008"], ["src/two.py"]),
    })
    _defect_ledger(fdir, [
        dict(_tiered("D-900", "LIVE"), file="src/one.py", spec_ref="FR-007"),
    ])
    assert foundry_defects_to_tasks(project_root)["ok"] is True
    _repo_with_commit(project_root, "src/one.py", "print('after')\n")

    # WITHOUT the concern: the door refuses, naming the id.
    before = _unrecorded_fix_problem(fdir, project_root)
    assert before is not None, "the arrangement does not reach the refusal"
    assert "D-900" in before["reason"], before
    assert before.get("excused") == {}, before

    # The hint the operator is following says to do exactly this.
    assert "leave it open and say so in the cycle's concerns" in before["hint"]

    # WITH it: the same door, the same commit, one open concern naming the id.
    #
    # Filed at cycle 1 while the server counter reads 0 — which is the ordinary
    # case, not a quirk: the counter advances at `inspect_start`, so during
    # GRIND N it reads N-1 and a lead declares N. An equality test on the cycle
    # would exclude every concern anyone ever files about the GRIND they are
    # standing in.
    opened = foundry_concern(
        casting_id=1,
        cycle=1,
        target="2",
        text=(
            "D-900 is deliberately left open: the fix is partial by ruling and "
            "the remainder is a structural packet for the next cycle."
        ),
        project_root=project_root,
    )
    assert opened.get("ok") is True, opened

    after = _unrecorded_fix_problem(fdir, project_root)
    assert after is None, after


def test_a_concern_excuses_only_the_id_it_actually_names(run_env):
    """fallout AC-041 (D-050) — the exit is per ID, and the match is bounded.

    An excuse that leaked across ids would be worse than no exit at all: one
    concern about one deliberately-partial fix would silently clear every other
    dispatched id whose fix nobody recorded, which is the exact state
    `DISPATCHED_DEFECT_UNRECORDED` exists to make visible.

    Two ids are dispatched and touched; the concern names ONE. The other must
    still refuse, and the refusal must say which id was excused and by which
    concern — an operator reading a shrunken count needs to see why, not infer
    that the check stopped running.
    """
    from foundry_mcp.tools.concerns import foundry_concern

    project_root, fdir = run_env
    _write_state(fdir, phase="F3", cycle=0)

    base = _repo_with_commit(project_root, "src/one.py", "print('a')\n")
    (fdir / artifacts.INSPECT_BOUNDARY_SHA_MARKER).write_text(
        base + "\n", encoding="utf-8"
    )
    _manifest_with_requirement_ids(fdir, {
        1: (["FR-007"], ["src/one.py"]),
        2: (["FR-008"], ["src/two.py"]),
    })
    _defect_ledger(fdir, [
        dict(_tiered("D-900", "LIVE"), file="src/one.py", spec_ref="FR-007"),
        dict(_tiered("D-901", "LIVE"), file="src/two.py", spec_ref="FR-008"),
    ])
    assert foundry_defects_to_tasks(project_root)["ok"] is True
    _repo_with_commit(project_root, "src/one.py", "print('b')\n")
    _repo_with_commit(project_root, "src/two.py", "print('c')\n")

    assert foundry_concern(
        casting_id=1, cycle=1, target="2",
        text="D-900 stays open by ruling; D-9010 is a different id entirely.",
        project_root=project_root,
    ).get("ok") is True

    problem = _unrecorded_fix_problem(fdir, project_root)
    assert problem is not None, "the unnamed id must still refuse"
    ids = {d["id"] for d in problem["defects"]}
    assert ids == {"D-901"}, problem["defects"]
    # ...and the excused one is REPORTED beside the concern that excused it.
    assert problem["excused"] == {"D-900": "C-001"}, problem["excused"]
    assert "D-900 (C-001)" in problem["reason"], problem["reason"]
    # The bounded match: `D-9010` in the text is not a mention of `D-901`.
    assert "D-901" not in problem["excused"], problem["excused"]


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
