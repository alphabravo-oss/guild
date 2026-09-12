"""The park door: one blocked item set aside with its question, and the answer
that clears it.

Driven through the real door (`orchestration/park.py#foundry_park`) on a
synthetic run from `tests/orchestration/_env.py#run_env`, then read back from
`state.json` — the file the router, the Stop hook, the report and the halt
door all read. Named for the module it imports, because every shipped
orchestration module has a test module that does.

Citations are interview answer ids from the should-not-stop spec's transcript,
per the lead's ruling on concern C-003: that spec is not one of the three this
directory's citation convention can qualify, so its requirement ids stay out of
this module's prose and the answers they derive from stand in.

  A-007  the closed set of reasons a run involves the human after CAST
  A-008  park only the blocked item; ask only when nothing else can move
  A-016  a server-owned park door persisted in state.json, cleared by an answer
  A-027  a halt answer seals HALTED with user_stop and the human's text
  A-029  post-CAST user_stop only on human-origin proof
  A-031  the awaiting_human marker, the one thing that lets a turn end
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime

import pytest

from foundry_mcp.schemas import vocab
from foundry_mcp.tools.orchestration import park as _park
from foundry_mcp.tools.orchestration.gates import foundry_gate
from foundry_mcp.tools.orchestration.park import (
    PARK_ACTION_UNKNOWN,
    PARK_ALREADY_ANSWERED,
    PARK_ANSWER_EMPTY,
    PARK_CATEGORY_UNKNOWN,
    PARK_HALT_INDICATOR_INVALID,
    PARK_HALT_NOT_ASKED,
    PARK_ITEM_ALREADY_PARKED,
    PARK_ITEM_LOOP,
    PARK_ITEM_REF_INVALID,
    PARK_PHASE_NOT_LIVE,
    PARK_QUESTION_EMPTY,
    PARK_RUN_HALTED,
    PARK_STATE_UNREADABLE,
    PARK_UNKNOWN_ID,
    clear_awaiting_human,
    foundry_park,
    open_parked_items,
    read_parked,
    set_awaiting_human,
)
from foundry_mcp.tools.orchestration.transitions import foundry_mark_phase_complete

from tests.orchestration._env import (  # noqa: F401
    _arm_ordering_token,
    _arrange_passing,
    _write_state,
    run_env,
)


def _state(fdir) -> dict:
    return json.loads((fdir / "state.json").read_text(encoding="utf-8"))


def _park_one(
    project_root,
    *,
    item_ref: object = "casting:3",
    category: object = "spec_wrong",
    question: object = "Which of the spec's two retry rules wins?",
) -> dict:
    return foundry_park(
        action="park", item_ref=item_ref, category=category, question=question,
        project_root=project_root,
    )


def _answer(project_root, parked_id: object, answer: object, halt: object = None) -> dict:
    return foundry_park(
        action="answer", parked_id=parked_id, answer=answer, halt=halt,
        project_root=project_root,
    )


# --------------------------------------------------------------------------- #
# The park action: what it persists (A-016, A-008).
# --------------------------------------------------------------------------- #


def test_parking_persists_every_field_of_the_fixed_shape(run_env):
    """One park writes one item carrying every field, in the declared order,
    under the one top-level key, and touches nothing else in the run's state."""
    project_root, fdir = run_env
    _write_state(fdir, phase="F3", cycle=2, keep="untouched")

    result = _park_one(project_root, question="  Which of the spec's two retry rules wins?  ")

    assert result["ok"] is True, result
    state = _state(fdir)
    assert set(state[vocab.PARKED_STATE_KEY]) == {
        vocab.PARKED_ITEMS_KEY, vocab.PARKED_AWAITING_HUMAN_KEY,
    }, state
    item = state[vocab.PARKED_STATE_KEY][vocab.PARKED_ITEMS_KEY][0]
    assert list(item) == list(vocab.PARKED_ITEM_FIELDS), item
    assert item["id"] == "P-001"
    assert item["item_ref"] == "casting:3"
    assert item["category"] == "spec_wrong"
    assert item["question"] == "Which of the spec's two retry rules wins?"
    assert item["cycle"] == 2
    assert datetime.fromisoformat(item["parked_at"]).tzinfo is not None, item
    assert item["answer"] is None and item["answered_at"] is None
    assert item["answer_is_halt"] is False
    assert state[vocab.PARKED_STATE_KEY][vocab.PARKED_AWAITING_HUMAN_KEY] is None
    # Nothing else moved: parking is not a transition.
    assert state["phase"] == "F3" and state["cycle"] == 2
    assert state["keep"] == "untouched"
    assert result["parked"] == item
    assert result["open_items"] == ["P-001"]
    assert "Foundry-Next" in result["message"], result["message"]


def test_ids_run_in_sequence_and_the_ref_is_normalised(run_env):
    project_root, fdir = run_env
    _write_state(fdir, phase="F2", cycle=1)

    assert _park_one(project_root)["parked"]["id"] == "P-001"
    second = _park_one(project_root, item_ref="  defect: D-012 ", category="env_broken")

    assert second["parked"]["id"] == "P-002", second
    assert second["parked"]["item_ref"] == "defect:D-012", second
    assert [i["id"] for i in open_parked_items(fdir)] == ["P-001", "P-002"]


@pytest.mark.parametrize("phase", sorted(vocab.POST_CAST_RUN_PHASES))
def test_every_build_phase_accepts_a_park(run_env, phase):
    project_root, fdir = run_env
    _write_state(fdir, phase=phase, cycle=1)
    assert _park_one(project_root)["ok"] is True


@pytest.mark.parametrize("category", sorted(vocab.PARK_CATEGORIES))
def test_every_closed_category_is_accepted(run_env, category):
    project_root, fdir = run_env
    _write_state(fdir, phase="F3", cycle=1)
    assert _park_one(project_root, category=category)["parked"]["category"] == category


# --------------------------------------------------------------------------- #
# The park action: every named refusal, and that a refusal writes nothing
# (A-007 for the closed category set, A-016 for the door's refusals).
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "category",
    ["triage_preference", "push", "release", "diminishing_returns", "SPEC_WRONG", "", None],
)
def test_a_category_outside_the_closed_set_is_refused_by_name(run_env, category):
    """A triage preference or a push is not a reason to involve the human, and
    the refusal names the set it does accept."""
    project_root, fdir = run_env
    _write_state(fdir, phase="F3", cycle=1)

    result = _park_one(project_root, category=category)

    assert result["phase"] == PARK_CATEGORY_UNKNOWN, result
    for member in vocab.PARK_CATEGORIES:
        assert member in result["error"], (member, result["error"])
    assert vocab.PARKED_STATE_KEY not in _state(fdir)


@pytest.mark.parametrize("question", ["", "   \n\t ", None])
def test_an_empty_question_is_refused(run_env, question):
    project_root, fdir = run_env
    _write_state(fdir, phase="F3", cycle=1)

    result = _park_one(project_root, question=question)

    assert result["phase"] == PARK_QUESTION_EMPTY, result
    assert vocab.PARKED_STATE_KEY not in _state(fdir)


@pytest.mark.parametrize("ref", ["", "3", "casting:", "wave:1", ":3", "casting 3", None, 3])
def test_an_item_ref_the_router_cannot_read_is_refused(run_env, ref):
    """The router routes around a parked item by its ref, so a ref naming no
    kind and id would park work behind nothing it can name."""
    project_root, fdir = run_env
    _write_state(fdir, phase="F3", cycle=1)

    result = _park_one(project_root, item_ref=ref)

    assert result["phase"] == PARK_ITEM_REF_INVALID, result
    assert vocab.park_item_ref_phrase() in result["hint"], result["hint"]
    assert vocab.PARKED_STATE_KEY not in _state(fdir)


def test_a_halted_run_refuses_both_actions_and_is_left_byte_for_byte(run_env):
    project_root, fdir = run_env
    _write_state(
        fdir, phase=vocab.RUN_PHASE_HALTED, cycle=3, halted_at_cycle=3,
        halted_reason={"reason": "cap_reached", "text": "cap"},
        parked={"items": [{
            "id": "P-001", "item_ref": "casting:3", "category": "spec_wrong",
            "question": "q?", "cycle": 3, "parked_at": "2026-09-11T00:00:00+00:00",
            "answer": None, "answered_at": None, "answer_is_halt": False,
        }], "awaiting_human": None},
    )
    before = (fdir / "state.json").read_text(encoding="utf-8")

    assert _park_one(project_root)["phase"] == PARK_RUN_HALTED
    assert _answer(project_root, "P-001", "carry on")["phase"] == PARK_RUN_HALTED

    assert (fdir / "state.json").read_text(encoding="utf-8") == before


@pytest.mark.parametrize("phase", ["F0", "F0.5", "F0.9", "F6"])
def test_parking_outside_the_build_is_refused(run_env, phase):
    """Before start_cast the operator is at the keyboard and is asked directly;
    after the run has ended there is nothing to wait on."""
    project_root, fdir = run_env
    _write_state(fdir, phase=phase, cycle=0)

    result = _park_one(project_root)

    assert result["phase"] == PARK_PHASE_NOT_LIVE, result
    assert vocab.PARKED_STATE_KEY not in _state(fdir)


def test_a_corrupt_state_is_refused_and_never_overwritten(run_env):
    """The state transaction reads a malformed document as empty, so a door that
    wrote through it without looking first would replace the whole run state
    with one key. The house artifact guard refuses first, naming the file, and
    it does so for both actions."""
    project_root, fdir = run_env
    (fdir / "state.json").write_text("{ not json", encoding="utf-8")

    for result in (_park_one(project_root), _answer(project_root, "P-001", "yes")):
        assert result.get("ok") is not True, result
        assert any("state.json" in p for p in result["corrupt_artifacts"]), result
    assert (fdir / "state.json").read_text(encoding="utf-8") == "{ not json"


def test_a_malformed_parked_value_is_refused_by_writers_and_tolerated_by_readers(run_env):
    project_root, fdir = run_env
    _write_state(fdir, phase="F3", cycle=1, parked=["not", "the", "shape"])
    before = (fdir / "state.json").read_text(encoding="utf-8")

    assert _park_one(project_root)["phase"] == PARK_STATE_UNREADABLE
    assert set_awaiting_human(fdir, ["P-001"]) is None
    assert clear_awaiting_human(fdir) is False
    assert (fdir / "state.json").read_text(encoding="utf-8") == before
    # The router still gets an answer on this run: nothing parked, nothing asked.
    assert read_parked(fdir) == {"items": [], "awaiting_human": None}


def test_an_unknown_action_is_refused_by_name(run_env):
    project_root, fdir = run_env
    _write_state(fdir, phase="F3", cycle=1)
    for action in ("", "unpark", "PARK"):
        result = foundry_park(action=action, project_root=project_root)
        assert result["phase"] == PARK_ACTION_UNKNOWN, (action, result)


# --------------------------------------------------------------------------- #
# Additive migration (A-016).
# --------------------------------------------------------------------------- #


def test_an_archive_with_no_parked_key_reads_as_nothing_parked(run_env):
    project_root, fdir = run_env
    _write_state(fdir, phase="F3", cycle=1)

    assert read_parked(fdir) == {"items": [], "awaiting_human": None}
    assert open_parked_items(fdir) == []
    assert set_awaiting_human(fdir, ["P-001"]) is None
    assert clear_awaiting_human(fdir) is False
    assert vocab.PARKED_STATE_KEY not in _state(fdir)

    # ...and a run directory with no state.json at all is the same answer,
    # without raising.
    (fdir / "state.json").unlink()
    assert read_parked(fdir) == {"items": [], "awaiting_human": None}

    # The first park creates the key.
    _write_state(fdir, phase="F3", cycle=1)
    assert _park_one(project_root)["ok"] is True
    assert vocab.PARKED_STATE_KEY in _state(fdir)


# --------------------------------------------------------------------------- #
# The answer action (A-016, and A-031 for the ask marker it clears).
# --------------------------------------------------------------------------- #


def test_one_open_question_per_item(run_env):
    """Two open questions on one item would each hold it back, so answering one
    would release nothing."""
    project_root, fdir = run_env
    _write_state(fdir, phase="F3", cycle=1)
    _park_one(project_root)

    again = _park_one(project_root, question="A different question about casting 3?")
    assert again["phase"] == PARK_ITEM_ALREADY_PARKED, again
    assert "P-001" in again["error"], again

    _answer(project_root, "P-001", "Rule A wins.")
    # Once answered, the ref parks again — on a question that is not the one
    # just answered. The identical question is a loop, and the rung below owns
    # it.
    assert _park_one(
        project_root, question="Rule A needs a migration; write one now?",
    )["parked"]["id"] == "P-002"


def test_an_answered_question_parked_again_unchanged_is_refused_as_a_loop(run_env):
    """The door's own backstop for the loop D-009 named (A-008, A-016).

    The rung above holds only a ref whose item is still OPEN, so the identical
    question on a ref the human had ALREADY answered landed as a fresh item and
    the next ask put it to them a second time. The router half no longer
    re-derives such a park — `guidance.py#_casting_routes` releases the answered
    ref — which leaves this door the last thing between a caller and the same
    question twice, and it carried no check of its own.
    """
    project_root, fdir = run_env
    _write_state(fdir, phase="F3", cycle=1)
    question = _park_one(project_root)["parked"]["question"]
    _answer(project_root, "P-001", "Rule A wins.")

    looped = _park_one(project_root)

    assert looped["phase"] == PARK_ITEM_LOOP, looped
    assert "P-001" in looped["error"], looped
    # Refused is refused: nothing was appended.
    assert [i["id"] for i in read_parked(fdir)["items"]] == ["P-001"]
    # ...and the comparison is on the question as STORED, so whitespace around
    # the same question is the same question.
    assert _park_one(project_root, question=f"  {question}  ")["phase"] == PARK_ITEM_LOOP

    # All three together, never the ref alone. A genuinely different question
    # about the same casting still parks...
    fresh = _park_one(project_root, question="Does rule A need a migration?")
    assert fresh["parked"]["id"] == "P-002", fresh

    # ...and so does the same question raised for a different reason, which is
    # the double-park a run legitimately makes: `spec_wrong` during CAST, then
    # `env_broken` later.
    _answer(project_root, "P-002", "No migration needed.")
    recategorised = _park_one(project_root, category="env_broken")
    assert recategorised["parked"]["id"] == "P-003", recategorised


@pytest.mark.parametrize("parked_id", ["P-009", "", "   ", None])
def test_an_unknown_id_is_refused(run_env, parked_id):
    project_root, fdir = run_env
    _write_state(fdir, phase="F3", cycle=1)
    _park_one(project_root)
    before = (fdir / "state.json").read_text(encoding="utf-8")

    result = _answer(project_root, parked_id, "Rule A wins.")

    assert result["phase"] == PARK_UNKNOWN_ID, result
    assert (fdir / "state.json").read_text(encoding="utf-8") == before


def test_an_already_answered_id_is_refused_and_the_first_answer_kept(run_env):
    project_root, fdir = run_env
    _write_state(fdir, phase="F3", cycle=1)
    _park_one(project_root)
    _answer(project_root, "P-001", "Rule A wins.")

    second = _answer(project_root, "P-001", "Actually rule B.")

    assert second["phase"] == PARK_ALREADY_ANSWERED, second
    assert read_parked(fdir)["items"][0]["answer"] == "Rule A wins."


@pytest.mark.parametrize("answer", ["", "  \n", None])
def test_an_empty_answer_is_refused(run_env, answer):
    project_root, fdir = run_env
    _write_state(fdir, phase="F3", cycle=1)
    _park_one(project_root)

    assert _answer(project_root, "P-001", answer)["phase"] == PARK_ANSWER_EMPTY
    assert read_parked(fdir)["items"][0]["answered_at"] is None


@pytest.mark.parametrize("halt", ["true", 1, "yes"])
def test_the_halt_indicator_must_be_a_boolean(run_env, halt):
    """Halt is an explicit indicator, never read out of a value's spelling."""
    project_root, fdir = run_env
    _write_state(fdir, phase="F3", cycle=1)
    _park_one(project_root)

    result = _answer(project_root, "P-001", "Stop.", halt=halt)

    assert result["phase"] == PARK_HALT_INDICATOR_INVALID, result
    assert read_parked(fdir)["items"][0]["answered_at"] is None


def test_an_answer_clears_its_item_and_the_ask(run_env):
    project_root, fdir = run_env
    _write_state(fdir, phase="F3", cycle=4)
    _park_one(project_root)
    _park_one(project_root, item_ref="stream:prove", category="env_broken",
              question="Playwright will not install; retry or skip SIGHT?")
    marker = set_awaiting_human(fdir, ["P-001", "P-002"])
    assert marker is not None
    assert list(marker) == list(vocab.AWAITING_HUMAN_FIELDS), marker
    assert marker["item_ids"] == ["P-001", "P-002"] and marker["cycle"] == 4
    assert read_parked(fdir)["awaiting_human"] == marker

    result = _answer(project_root, "P-001", "  Rule A wins.  ")

    assert result["ok"] is True and result["halt"] is False, result
    assert "next_call" not in result, result
    assert "Foundry-Next" in result["message"], result["message"]
    item = read_parked(fdir)["items"][0]
    assert item["answer"] == "Rule A wins."
    assert datetime.fromisoformat(item["answered_at"]).tzinfo is not None
    assert item["answer_is_halt"] is False
    # Recording ANY answer clears the ask; the router decides afresh.
    assert read_parked(fdir)["awaiting_human"] is None
    assert [i["id"] for i in open_parked_items(fdir)] == ["P-002"]
    assert result["open_items"] == ["P-002"]


def test_the_ask_marker_names_only_open_items_and_is_never_empty(run_env):
    """The marker is the one thing that lets the Stop hook allow a mid-build
    turn-end, so it can never name nothing, and never an answered item."""
    project_root, fdir = run_env
    _write_state(fdir, phase="F3", cycle=1)
    _park_one(project_root)
    _park_one(project_root, item_ref="casting:4")
    _answer(project_root, "P-002", "Skip it.")

    marker = set_awaiting_human(fdir, ["P-001", "P-404", 7, "P-001", "P-002"])
    assert marker["item_ids"] == ["P-001"], marker

    assert clear_awaiting_human(fdir) is True
    assert clear_awaiting_human(fdir) is False
    assert set_awaiting_human(fdir, []) is None
    assert set_awaiting_human(fdir, ["P-002"]) is None
    assert read_parked(fdir)["awaiting_human"] is None


# --------------------------------------------------------------------------- #
# The halt answer: the human's halt, and only the human's (A-027, A-029).
# --------------------------------------------------------------------------- #


def test_a_halt_answer_needs_the_question_to_have_been_asked(run_env):
    project_root, fdir = run_env
    _write_state(fdir, phase="F3", cycle=1)
    _park_one(project_root)
    _park_one(project_root, item_ref="casting:4")

    unasked = _answer(project_root, "P-001", "Halt the run.", halt=True)
    assert unasked["phase"] == PARK_HALT_NOT_ASKED, unasked

    set_awaiting_human(fdir, ["P-002"])
    other = _answer(project_root, "P-001", "Halt the run.", halt=True)
    assert other["phase"] == PARK_HALT_NOT_ASKED, other
    assert read_parked(fdir)["items"][0]["answered_at"] is None


def test_halt_is_never_read_out_of_the_answer_words(run_env):
    """An answer that SAYS halt, recorded without the indicator, is not a halt
    answer, and the halt door still has no human-origin proof."""
    project_root, fdir = run_env
    _arrange_passing(project_root, fdir, "halt")
    _park_one(project_root)
    set_awaiting_human(fdir, ["P-001"])

    answered = _answer(project_root, "P-001", "halt the run")
    assert answered["halt"] is False
    assert read_parked(fdir)["items"][0]["answer_is_halt"] is False

    _arm_ordering_token(fdir)
    refused = foundry_mark_phase_complete(
        "halt", str(project_root), reason="user_stop", text="halt the run",
    )
    assert refused.get("ok") is not True, refused
    assert _state(fdir)["phase"] == "F3"


def test_a_halt_answer_is_the_proof_the_halt_door_seals_user_stop_on(run_env):
    """The whole path: park, ask, the human answers halt, and the lead seals
    HALTED with reason user_stop and the human's own words."""
    project_root, fdir = run_env
    _arrange_passing(project_root, fdir, "halt")
    _park_one(project_root)
    set_awaiting_human(fdir, ["P-001"])
    words = "Stop here. I will rewrite the spec and start again."

    answered = _answer(project_root, "P-001", words, halt=True)

    assert answered["ok"] is True and answered["halt"] is True, answered
    assert read_parked(fdir)["items"][0]["answer_is_halt"] is True
    assert answered["next_call"] == {
        "tool": "Foundry-Phase", "phase": "halt",
        "reason": vocab.HALT_REASON_USER_STOP, "text": words,
    }, answered

    # The gate reports the proof as data and consumes nothing.
    _arm_ordering_token(fdir)
    gate = foundry_gate("halt", str(project_root), reason="user_stop", text=words)
    assert gate["passed"] is True, gate
    row = next(c for c in gate["checklist"] if c["check"].startswith("human_origin_proof"))
    assert row["ok"] is True and "parked_answer" in row["check"], row

    _arm_ordering_token(fdir)
    sealed = foundry_mark_phase_complete(
        "halt", str(project_root), reason="user_stop", text=words,
    )

    assert sealed.get("ok") is True, sealed
    state = _state(fdir)
    assert state["phase"] == vocab.RUN_PHASE_HALTED, state
    assert state["halted_reason"] == {"reason": "user_stop", "text": words}, state
    assert sealed["halt_proof"]["kind"] == vocab.HALT_PROOF_PARKED_ANSWER, sealed
    assert sealed["halt_proof"]["item_id"] == "P-001", sealed
    assert (fdir / "REPORT.md").exists()
    # The proof proved one halt: the run is HALTED, and a second is refused.
    _arm_ordering_token(fdir)
    again = foundry_mark_phase_complete(
        "halt", str(project_root), reason="user_stop", text=words,
    )
    assert again.get("ok") is not True and "HALTED" in again["error"], again


# --------------------------------------------------------------------------- #
# The registration this casting owns in server.py.
# --------------------------------------------------------------------------- #


def _tools() -> dict:
    from foundry_mcp import server as srv

    return {t.name: t for t in asyncio.run(srv.list_tools())}


def test_the_park_door_is_registered_under_the_vocabulary_name():
    from foundry_mcp import server as srv

    tools = _tools()
    assert vocab.PARK_TOOL_NAME in tools, sorted(tools)
    assert vocab.PARK_TOOL_NAME in srv._DISPATCH
    props = tools[vocab.PARK_TOOL_NAME].inputSchema["properties"]
    assert set(props) == {
        "action", "item_ref", "category", "question", "parked_id", "answer", "halt",
    }, sorted(props)
    # Closed sets travel in prose, never as an enum the schema check would
    # answer before the door's own named refusal.
    for key in ("action", "category", "item_ref"):
        assert "enum" not in props[key], (key, props[key])
    for member in vocab.PARK_CATEGORIES:
        assert member in props["category"]["description"], member
    for action in vocab.PARK_ACTIONS:
        assert action in props["action"]["description"], action
    assert props["halt"]["type"] == "boolean"
    assert _park.foundry_park is srv.foundry_park


def test_an_unknown_category_over_the_wire_reaches_the_doors_own_refusal(run_env):
    """Through `call_tool`, schema check included: the named refusal speaks."""
    from foundry_mcp import server as srv

    project_root, fdir = run_env
    _write_state(fdir, phase="F3", cycle=1)
    srv._project_root = str(project_root)
    try:
        blocks = asyncio.run(srv.call_tool(vocab.PARK_TOOL_NAME, {
            "action": "park", "item_ref": "casting:3", "category": "push",
            "question": "Push to main?",
        }))
        absent = srv._DISPATCH[vocab.PARK_TOOL_NAME]({"action": "park"})
        parked = srv._DISPATCH[vocab.PARK_TOOL_NAME]({
            "action": "park", "item_ref": "casting:3", "category": "spec_wrong",
            "question": "Which rule wins?",
        })
    finally:
        srv._project_root = "."

    assert PARK_CATEGORY_UNKNOWN in blocks[0].text, blocks[0].text
    # Absence travels as absence and is named by the door, not a KeyError.
    assert absent["phase"] == PARK_CATEGORY_UNKNOWN, absent
    assert parked["ok"] is True, parked


def test_the_concern_door_hands_on_blocker_kind_only_when_sent(monkeypatch):
    from foundry_mcp import server as srv

    seen: list[dict] = []
    monkeypatch.setattr(srv, "foundry_concern", lambda **kw: seen.append(kw) or {"ok": True})
    base = {"casting_id": 1, "cycle": 0, "target": "3", "text": "blocked"}

    srv._DISPATCH["Foundry-Concern"]({**base, "blocker_kind": "missing_prerequisite"})
    srv._DISPATCH["Foundry-Concern"](dict(base))

    assert seen[0]["blocker_kind"] == "missing_prerequisite", seen[0]
    assert "blocker_kind" not in seen[1], seen[1]
    prop = _tools()["Foundry-Concern"].inputSchema["properties"]["blocker_kind"]
    assert "enum" not in prop, prop
    for kind in vocab.BLOCKER_KINDS:
        assert kind in prop["description"], kind


def test_the_spawn_door_hands_on_defect_ids_only_when_sent(monkeypatch):
    from foundry_mcp import server as srv

    seen: list[dict] = []
    monkeypatch.setattr(
        srv, "foundry_spawn_teammate", lambda **kw: seen.append(kw) or {"ok": True},
    )

    srv._DISPATCH["Foundry-Spawn-Teammate"]({
        "casting_id": 2, "phase": "grind", "defect_ids": ["D-001", "D-004"],
    })
    srv._DISPATCH["Foundry-Spawn-Teammate"]({"casting_id": 2, "phase": "grind"})

    assert seen[0]["defect_ids"] == ["D-001", "D-004"], seen[0]
    assert "defect_ids" not in seen[1], seen[1]
    prop = _tools()["Foundry-Spawn-Teammate"].inputSchema["properties"]["defect_ids"]
    assert prop["type"] == "array" and prop["items"] == {"type": "string"}, prop
