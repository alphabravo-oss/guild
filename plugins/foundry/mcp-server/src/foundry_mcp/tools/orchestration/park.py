"""The park door: one blocked item set aside with its question, while the run keeps moving.

The should-not-stop spec's park door and parked state. A LIFECYCLE module: it
owns every writer of `state.json`'s `parked` key and nothing else in the run's
state, and it reaches only leaves (`schemas/vocab.py`, `tools/artifacts.py`,
`tools/foundry_state.py`) plus the house named refusal in `tools/rosters.py`.

WHO READS THE PARKED STATE, and why the readers are not all here:

  * the router (`orchestration/guidance.py`) imports `read_parked`,
    `open_parked_items`, `set_awaiting_human` and `clear_awaiting_human` from
    this module — lifecycle to lifecycle;
  * the halt door's human-origin proof (`orchestration/halt.py`) reads the same
    key from the file, because `transitions.py` is a verifier that reaches
    `halt.py` and nothing past it;
  * the Stop hook reads it from disk, because a hook may not import this
    package at all.

The shape all of them read is fixed and spelled once, in `schemas/vocab.py`
beside `PARKED_STATE_KEY`.

WHY A DOOR AND NOT A NOTE. A run that met something only the human could settle
used to stop there, with the question in free text — a handoff, a directive, a
paragraph of concerns.md — that nothing could route around. A parked item is
state the server owns: what it blocks, why (a category from a closed set), and
the question. So the router keeps every other casting, defect and stream moving
and asks once, every question in one batch, when nothing else can move.
"""
from __future__ import annotations

from pathlib import Path

from foundry_mcp.schemas.vocab import (
    AWAITING_FIELD_CYCLE,
    AWAITING_FIELD_ITEM_IDS,
    AWAITING_FIELD_SET_AT,
    HALT_REASON_USER_STOP,
    PARK_ACTION_ANSWER,
    PARK_ACTION_PARK,
    PARK_ACTIONS,
    PARK_CATEGORIES,
    PARK_CATEGORY_UNKNOWN_DEADLOCK,
    PARK_TOOL_NAME,
    PARKED_AWAITING_HUMAN_KEY,
    PARKED_FIELD_ANSWER,
    PARKED_FIELD_ANSWER_IS_HALT,
    PARKED_FIELD_ANSWERED_AT,
    PARKED_FIELD_CATEGORY,
    PARKED_FIELD_CYCLE,
    PARKED_FIELD_ID,
    PARKED_FIELD_ITEM_REF,
    PARKED_FIELD_PARKED_AT,
    PARKED_FIELD_QUESTION,
    PARKED_ID_PREFIX,
    PARKED_ITEMS_KEY,
    PARKED_STATE_KEY,
    POST_CAST_RUN_PHASES,
    RUN_PHASE_HALTED,
    park_category_phrase,
    park_item_ref,
    park_item_ref_phrase,
    parse_park_item_ref,
)
from foundry_mcp.tools.artifacts import _artifact_guard, _document_transaction
from foundry_mcp.tools.foundry_state import (
    current_cycle,
    get_run_dir,
    now_iso,
    read_document,
)
# The ONE implementation of the house `{error, hint, phase}` refusal, where
# `phase` carries the refusal's NAME. Imported rather than re-typed: a second
# body is the fork `test_concerns.py` pins against.
from foundry_mcp.tools.rosters import _named_refusal


#: Named refusal: `action` was neither of the door's two actions.
PARK_ACTION_UNKNOWN = "PARK_ACTION_UNKNOWN"

#: Named refusal: `category` is not a member of `PARK_CATEGORIES`.
PARK_CATEGORY_UNKNOWN = "PARK_CATEGORY_UNKNOWN"

#: Named refusal: the question was empty or whitespace.
PARK_QUESTION_EMPTY = "PARK_QUESTION_EMPTY"

#: Named refusal: `item_ref` names no kind and id the router can read.
PARK_ITEM_REF_INVALID = "PARK_ITEM_REF_INVALID"

#: Named refusal: an unanswered parked item already holds this `item_ref`.
PARK_ITEM_ALREADY_PARKED = "PARK_ITEM_ALREADY_PARKED"

#: Named refusal: an ANSWERED parked item already holds this `item_ref` with the
#: same category and the same question, so parking it again records the same
#: question twice rather than a new one. `PARK_ITEM_ALREADY_PARKED`'s
#: counterpart: that rung keeps ONE OPEN question per item, this one keeps a
#: question the human has already answered from being asked a second time.
PARK_ITEM_LOOP = "PARK_ITEM_LOOP"

#: Named refusal: the run is HALTED, and nothing leaves HALTED.
PARK_RUN_HALTED = "PARK_RUN_HALTED"

#: Named refusal: the run is not between start_cast and NYQUIST.
PARK_PHASE_NOT_LIVE = "PARK_PHASE_NOT_LIVE"

#: Named refusal: `state.json`'s `parked` value is not the documented shape.
#: Refused rather than normalised away — see `_writable_parked`.
PARK_STATE_UNREADABLE = "PARK_STATE_UNREADABLE"

#: Named refusal: `parked_id` names no parked item.
PARK_UNKNOWN_ID = "PARK_UNKNOWN_ID"

#: Named refusal: the parked item already carries an answer.
PARK_ALREADY_ANSWERED = "PARK_ALREADY_ANSWERED"

#: Named refusal: the answer was empty or whitespace.
PARK_ANSWER_EMPTY = "PARK_ANSWER_EMPTY"

#: Named refusal: `halt` was something other than true, false or absent.
PARK_HALT_INDICATOR_INVALID = "PARK_HALT_INDICATOR_INVALID"

#: Named refusal: a halt answer for an item the human was never asked about.
PARK_HALT_NOT_ASKED = "PARK_HALT_NOT_ASKED"


def foundry_park(
    action: str = "",
    item_ref: str = "",
    category: str = "",
    question: str = "",
    parked_id: str = "",
    answer: str = "",
    halt: bool | None = None,
    project_root: str = ".",
) -> dict:
    """Park one blocked item, or record the human's answer to one.

    Two actions, one door — the `Foundry-Concern` shape. ``action='park'``
    takes ``item_ref``, ``category`` and ``question``. ``action='answer'`` takes
    ``parked_id``, ``answer`` and, ONLY when the human chose to halt the run,
    ``halt=True``. Every argument defaults to absence and absence is refused by
    name, so the transport hands on what it was given and nothing it guessed.
    """
    fdir = get_run_dir(project_root)
    if not fdir or not fdir.exists():
        return {"error": "No active foundry run"}
    # The house guard every orchestrator door runs first: an unreadable run
    # artifact or declared external input is refused by name here, before any
    # argument is judged — and before `_document_transaction`, which reads a
    # malformed state.json as `{}` and would write the parked key over it.
    if (corrupt := _artifact_guard(fdir)):
        return corrupt
    if action == PARK_ACTION_PARK:
        return _park_item(fdir, item_ref, category, question)
    if action == PARK_ACTION_ANSWER:
        return _answer_item(fdir, parked_id, answer, halt)
    return _named_refusal(
        f"{PARK_TOOL_NAME} takes action "
        f"{' or '.join(repr(a) for a in PARK_ACTIONS)}, and was given {action!r}.",
        f"Call {PARK_TOOL_NAME}(action='{PARK_ACTION_PARK}', item_ref=..., "
        f"category=..., question=...) to park one blocked item, or "
        f"{PARK_TOOL_NAME}(action='{PARK_ACTION_ANSWER}', parked_id=..., "
        "answer=...) to record the human's answer to one.",
        PARK_ACTION_UNKNOWN,
    )


def _park_item(fdir: Path, item_ref: object, category: object, question: object) -> dict:
    """The park action: one appended item, or one named refusal and no write.

    An accepted park also tears down any `awaiting_human` marker, in the same
    transaction as the append — see the comment at the append itself.
    """
    # The input-shape rungs come first and read nothing — the `_open_concern`
    # order: a call that can never succeed does not earn a state read, and the
    # cheaper, more local refusal is the one the caller should see.
    if not isinstance(category, str) or category not in PARK_CATEGORIES:
        return _named_refusal(
            f"{category!r} is not a park category. After start_cast a run "
            f"involves the human for exactly these reasons: "
            f"{park_category_phrase()}. Anything else — a triage preference, a "
            "push, a release, diminishing returns — the run decides for itself "
            "or leaves for the report.",
            "Keep the run moving. If the item really is blocked on one of those "
            f"reasons, call {PARK_TOOL_NAME}(action='{PARK_ACTION_PARK}', "
            f"item_ref=..., category=<one of: {park_category_phrase()}>, "
            "question=...).",
            PARK_CATEGORY_UNKNOWN,
        )
    body = question.strip() if isinstance(question, str) else ""
    if not body:
        return _named_refusal(
            "A parked item needs the question the human has to answer: an empty "
            "question parks work behind nothing anyone can reply to.",
            f"Call {PARK_TOOL_NAME}(action='{PARK_ACTION_PARK}', ..., "
            "question='what the human must decide, in words they can answer "
            "without the transcript').",
            PARK_QUESTION_EMPTY,
        )
    parsed = parse_park_item_ref(item_ref)
    if parsed is None:
        return _named_refusal(
            f"item_ref {item_ref!r} does not name what the item blocks. The "
            "router routes the rest of the run around a parked item by its ref, "
            "so a ref it cannot read would block everything.",
            f"Pass item_ref as <kind>:<id>, one of: {park_item_ref_phrase()} — "
            "for example casting:3 or defect:D-012.",
            PARK_ITEM_REF_INVALID,
        )
    ref = park_item_ref(*parsed)

    refused = _park_state_refusal(fdir)
    if refused is not None:
        return refused

    cycle = current_cycle(fdir)
    with _document_transaction(fdir / "state.json") as doc:
        section = _writable_parked(doc)
        if section is None:
            return _parked_shape_refusal()
        items = section[PARKED_ITEMS_KEY]
        already = [
            item[PARKED_FIELD_ID] for item in items
            if item.get(PARKED_FIELD_ITEM_REF) == ref
            and not item.get(PARKED_FIELD_ANSWERED_AT)
        ]
        if already:
            # One open question per item. Two would each hold the item back, so
            # answering one would release nothing — the work would wait on a
            # second answer the human never knew was the one that mattered.
            return _named_refusal(
                f"{ref} is already parked as {already[0]}, and that question "
                "has not been answered yet.",
                f"Leave {already[0]} to be answered — Foundry-Next asks the "
                "human when nothing else can move — or, if the question itself "
                f"has changed, record its answer with {PARK_TOOL_NAME}"
                f"(action='{PARK_ACTION_ANSWER}', ...) and park the new one.",
                PARK_ITEM_ALREADY_PARKED,
            )
        looped = [
            prior.get(PARKED_FIELD_ID) for prior in items
            if prior.get(PARKED_FIELD_ITEM_REF) == ref
            and prior.get(PARKED_FIELD_CATEGORY) == category
            and prior.get(PARKED_FIELD_QUESTION) == body
            and prior.get(PARKED_FIELD_ANSWERED_AT)
        ]
        if looped:
            # The ANSWERED counterpart of the rung above, and the door's own
            # backstop against the loop D-009 named: the router re-derived a
            # park from inputs an answer does not change, the rung above holds
            # only a ref whose item is still OPEN, so the identical question
            # landed as a fresh item and the next ask put it to the human
            # again — for as long as they kept answering it. The same ref, the
            # same category AND the same question is that loop by definition.
            # All three together, never the ref alone: a casting legitimately
            # parks twice in a run for different reasons (`env_broken` during
            # CAST, `spec_wrong` later), so a genuinely new question differs in
            # one of the three and still parks.
            return _named_refusal(
                f"{ref} was already parked as {looped[-1]} with this category "
                "and this question, and the human answered it. Parking it again "
                "records the same question twice rather than a new one, and the "
                "answer already on file is the answer.",
                "Act on the answer that item carries. If the item genuinely "
                "cannot move for a DIFFERENT reason, park it under that "
                "category, or with the question that reason raises — one of the "
                "two has to differ. If nothing can move and no rule says why, "
                f"that reason is {PARK_CATEGORY_UNKNOWN_DEADLOCK!r}.",
                PARK_ITEM_LOOP,
            )
        item = {
            PARKED_FIELD_ID: _next_parked_id(items),
            PARKED_FIELD_ITEM_REF: ref,
            PARKED_FIELD_CATEGORY: category,
            PARKED_FIELD_QUESTION: body,
            PARKED_FIELD_CYCLE: cycle,
            PARKED_FIELD_PARKED_AT: now_iso(),
            PARKED_FIELD_ANSWER: None,
            PARKED_FIELD_ANSWERED_AT: None,
            PARKED_FIELD_ANSWER_IS_HALT: False,
        }
        items.append(item)
        # THE ASK THIS PARK INVALIDATES COMES DOWN WITH IT (D-028). The marker
        # records an ask Foundry-Next put to the human naming the ids open AT
        # THAT MOMENT, and it is the ONE thing that lets the Stop hook end a
        # mid-build turn. The item just appended is a question that ask does not
        # name, and the hook's reader keys on "an object naming at least one
        # id": it cannot tell a complete ask from one this park has outgrown. So
        # a marker left standing here ends the turn with this item open and
        # named by no ask — nobody waiting on a question nobody was asked.
        #
        # `guidance.py#_sync_awaiting_human` tears a stale marker down too, but
        # only on the NEXT router call, which is one call too late for the turn
        # that ends first. Torn down HERE, inside the transaction that appends,
        # so the two land together: a separate clear afterwards would leave the
        # same window, only narrower.
        #
        # ON THE SUCCESS PATH ONLY. Every refusal rung above returns before the
        # append, so a REFUSED park appends no unnamed question and leaves a
        # standing ask exactly as it was.
        asked = section[PARKED_AWAITING_HUMAN_KEY] is not None
        section[PARKED_AWAITING_HUMAN_KEY] = None
        doc[PARKED_STATE_KEY] = section
        doc["updated_at"] = now_iso()
    open_ids = [i[PARKED_FIELD_ID] for i in items if not i.get(PARKED_FIELD_ANSWERED_AT)]
    message = (
        f"Parked {item[PARKED_FIELD_ID]} ({category}) on {ref}. Only {ref} "
        "waits on the human: keep every other casting, defect and stream "
        "moving, and call Foundry-Next — it asks the human, every parked "
        "question in one batch, only when nothing else can move."
    )
    if asked:
        message += (
            f" The ask outstanding when {item[PARKED_FIELD_ID]} was parked did "
            "not name it, so that ask has been cleared and your turn no longer "
            "ends here: call Foundry-Next, and it asks again over every open "
            "question, this one included."
        )
    return {
        "ok": True,
        "action": PARK_ACTION_PARK,
        "parked": item,
        "open_items": open_ids,
        "message": message,
    }


def _answer_item(fdir: Path, parked_id: object, answer: object, halt: object) -> dict:
    """The answer action: the item answered and the ask marker cleared, or one
    named refusal and no write."""
    ident = parked_id.strip() if isinstance(parked_id, str) else ""
    if not ident:
        return _named_refusal(
            "An answer names the parked item it answers, and no parked_id was "
            "given.",
            f"Call {PARK_TOOL_NAME}(action='{PARK_ACTION_ANSWER}', "
            f"parked_id='{PARKED_ID_PREFIX}NNN', answer=...). Foundry-Next lists "
            "the parked ids in its ask step.",
            PARK_UNKNOWN_ID,
        )
    words = answer.strip() if isinstance(answer, str) else ""
    if not words:
        return _named_refusal(
            f"The answer to {ident} is empty, and an empty answer records "
            "nothing the run can act on.",
            f"Call {PARK_TOOL_NAME}(action='{PARK_ACTION_ANSWER}', "
            f"parked_id='{ident}', answer=<the human's answer, verbatim>).",
            PARK_ANSWER_EMPTY,
        )
    if halt is not None and not isinstance(halt, bool):
        return _named_refusal(
            f"halt must be true, false or absent, and arrived as {halt!r}. Halt "
            "is an explicit indicator, never read out of a value's spelling.",
            "Pass halt=true ONLY when the human chose to halt the run; "
            "otherwise omit it.",
            PARK_HALT_INDICATOR_INVALID,
        )
    wants_halt = halt is True

    refused = _park_state_refusal(fdir)
    if refused is not None:
        return refused

    with _document_transaction(fdir / "state.json") as doc:
        section = _writable_parked(doc)
        if section is None:
            return _parked_shape_refusal()
        items = section[PARKED_ITEMS_KEY]
        item = next((i for i in items if i.get(PARKED_FIELD_ID) == ident), None)
        if item is None:
            open_ids = [
                i.get(PARKED_FIELD_ID) for i in items
                if not i.get(PARKED_FIELD_ANSWERED_AT)
            ]
            return _named_refusal(
                f"No parked item is {ident!r}. Open parked items: "
                f"{', '.join(str(i) for i in open_ids) or 'none'}.",
                "Answer one of the open ids; Foundry-Next lists them in its ask "
                "step.",
                PARK_UNKNOWN_ID,
            )
        if item.get(PARKED_FIELD_ANSWERED_AT):
            return _named_refusal(
                f"{ident} was already answered at "
                f"{item[PARKED_FIELD_ANSWERED_AT]} ({item.get(PARKED_FIELD_ANSWER)!r}). "
                "An answer is recorded once, so the archive keeps the one the "
                "run acted on.",
                f"If the human has a new question to settle, park it as a new "
                f"item with {PARK_TOOL_NAME}(action='{PARK_ACTION_PARK}', ...).",
                PARK_ALREADY_ANSWERED,
            )
        if wants_halt:
            # The stricter reading of the halt answer: it proves a human halt
            # only when the human was actually ASKED, which is what the router's
            # `awaiting_human` marker records. A halt recorded against a
            # question nobody put to the human would be the lead's word again.
            awaiting = section[PARKED_AWAITING_HUMAN_KEY]
            asked = awaiting.get(AWAITING_FIELD_ITEM_IDS) if isinstance(awaiting, dict) else None
            if not (isinstance(asked, list) and ident in asked):
                return _named_refusal(
                    f"{ident} has not been put to the human: halt is accepted "
                    "only as the answer to a question Foundry-Next's ask step "
                    "actually asked, and no outstanding ask names it.",
                    "Call Foundry-Next; when nothing else can move it asks the "
                    "human every parked question. Record halt=true only if the "
                    "human answers that ask with halt.",
                    PARK_HALT_NOT_ASKED,
                )
        item[PARKED_FIELD_ANSWER] = words
        item[PARKED_FIELD_ANSWERED_AT] = now_iso()
        item[PARKED_FIELD_ANSWER_IS_HALT] = wants_halt
        # Recording ANY answer clears the ask: the next Foundry-Next decides
        # afresh whether everything left is parked, and sets it again if so.
        section[PARKED_AWAITING_HUMAN_KEY] = None
        doc[PARKED_STATE_KEY] = section
        doc["updated_at"] = now_iso()

    open_ids = [i[PARKED_FIELD_ID] for i in items if not i.get(PARKED_FIELD_ANSWERED_AT)]
    result = {
        "ok": True,
        "action": PARK_ACTION_ANSWER,
        "answered": item,
        "halt": wants_halt,
        "open_items": open_ids,
    }
    if wants_halt:
        result["next_call"] = {
            "tool": "Foundry-Phase",
            "phase": "halt",
            "reason": HALT_REASON_USER_STOP,
            "text": words,
        }
        result["message"] = (
            f"The human answered {ident} with halt, and that answer is the "
            f"proof the halt door accepts for {HALT_REASON_USER_STOP!r}. Stop "
            "every in-flight agent, bring any registered team down with "
            "Foundry-Team-Down, then call Foundry-Phase(phase='halt', "
            f"reason='{HALT_REASON_USER_STOP}', text=<the human's answer, "
            "verbatim>). That transition seals HALTED and regenerates the "
            "report; HALTED is terminal."
        )
    else:
        result["message"] = (
            f"{ident} is answered and no longer holds back "
            f"{item[PARKED_FIELD_ITEM_REF]}. Call Foundry-Next: it routes the "
            "released work, and asks the human again only if nothing else can "
            "move."
        )
    return result


# --------------------------------------------------------------------------- #
# The parked state, for the router. Readers tolerate every shape; writers
# refuse a malformed one rather than normalise it away.
# --------------------------------------------------------------------------- #


def read_parked(fdir: Path) -> dict:
    """The run's parked state: ``{"items": [...], "awaiting_human": dict | None}``.

    Never raises and never refuses. An absent key, an unreadable `state.json`
    and a `parked` value that is not the documented shape all read as no parked
    items and no ask outstanding — the additive-migration reading, and the one
    a ROUTER needs, since it must answer on any run. Items that are not
    mappings are skipped. The writers in this module refuse a malformed shape
    instead, so nothing a reader tolerates is ever silently overwritten.
    """
    state, _problem = read_document(fdir / "state.json")
    raw = state.get(PARKED_STATE_KEY)
    if not isinstance(raw, dict):
        return {PARKED_ITEMS_KEY: [], PARKED_AWAITING_HUMAN_KEY: None}
    items = raw.get(PARKED_ITEMS_KEY)
    awaiting = raw.get(PARKED_AWAITING_HUMAN_KEY)
    return {
        PARKED_ITEMS_KEY: (
            [dict(item) for item in items if isinstance(item, dict)]
            if isinstance(items, list) else []
        ),
        PARKED_AWAITING_HUMAN_KEY: dict(awaiting) if isinstance(awaiting, dict) else None,
    }


def open_parked_items(fdir: Path) -> list[dict]:
    """The parked items still waiting on an answer, in the order they were parked."""
    return [
        item for item in read_parked(fdir)[PARKED_ITEMS_KEY]
        if not item.get(PARKED_FIELD_ANSWERED_AT)
    ]


def set_awaiting_human(fdir: Path, item_ids) -> dict | None:
    """Write the `awaiting_human` marker for an ask naming ``item_ids``.

    The router calls this when it emits the ask step because every remaining
    unit of work is parked. It is the ONE thing that lets the Stop hook allow a
    mid-build turn-end, so it names only ids that are genuinely open parked
    items, in the order given, and it is never written empty: an ask with
    nothing to ask would let the turn end with nothing to wait for.

    Returns the marker written, or None when nothing was written — no open id
    among ``item_ids``, a `state.json` that cannot be read, or a `parked` value
    that is not the documented shape.
    """
    _state, problem = read_document(fdir / "state.json")
    if problem is not None:
        return None
    cycle = current_cycle(fdir)
    with _document_transaction(fdir / "state.json") as doc:
        section = _writable_parked(doc)
        if section is None:
            return None
        open_ids = {
            item.get(PARKED_FIELD_ID) for item in section[PARKED_ITEMS_KEY]
            if not item.get(PARKED_FIELD_ANSWERED_AT)
        }
        asked: list[str] = []
        for ident in item_ids or ():
            if isinstance(ident, str) and ident in open_ids and ident not in asked:
                asked.append(ident)
        if not asked:
            return None
        marker = {
            AWAITING_FIELD_SET_AT: now_iso(),
            AWAITING_FIELD_CYCLE: cycle,
            AWAITING_FIELD_ITEM_IDS: asked,
        }
        section[PARKED_AWAITING_HUMAN_KEY] = marker
        doc[PARKED_STATE_KEY] = section
        doc["updated_at"] = now_iso()
    return marker


def clear_awaiting_human(fdir: Path) -> bool:
    """Clear the `awaiting_human` marker. True when one was set and is now gone.

    Recording an answer clears it already; this is for the router, when work
    it had found blocked can move again without an answer — an upstream casting
    accepted, say — so the Stop hook goes back to holding the turn.
    """
    _state, problem = read_document(fdir / "state.json")
    if problem is not None:
        return False
    with _document_transaction(fdir / "state.json") as doc:
        section = _writable_parked(doc)
        if section is None or section[PARKED_AWAITING_HUMAN_KEY] is None:
            return False
        section[PARKED_AWAITING_HUMAN_KEY] = None
        doc[PARKED_STATE_KEY] = section
        doc["updated_at"] = now_iso()
    return True


# --------------------------------------------------------------------------- #
# Shared rungs.
# --------------------------------------------------------------------------- #


def _park_state_refusal(fdir: Path) -> dict | None:
    """The refusal a park-door write owes the run's phase, or None to proceed.

    An unreadable `state.json` never reaches here: `foundry_park` runs the
    house `_artifact_guard` first and refuses it by name.
    """
    state, _problem = read_document(fdir / "state.json")
    phase = state.get("phase")
    if phase == RUN_PHASE_HALTED:
        return _named_refusal(
            "This run is HALTED, and nothing leaves HALTED: there is no build "
            "left for a parked item to wait on and no question left to ask.",
            "Read REPORT.md and tell the user what remains open. To carry the "
            "work forward, start a NEW run.",
            PARK_RUN_HALTED,
        )
    if not isinstance(phase, str) or phase not in POST_CAST_RUN_PHASES:
        return _named_refusal(
            f"The run is in {phase or 'F0'}, and items are parked only while "
            f"the build runs with the operator away — "
            f"{', '.join(sorted(POST_CAST_RUN_PHASES))}, from start_cast to "
            "NYQUIST.",
            "Before CAST the operator is at the keyboard: ask them directly "
            "with AskUserQuestion. After the run has ended there is nothing "
            "left to park.",
            PARK_PHASE_NOT_LIVE,
        )
    return None


def _writable_parked(doc: dict) -> dict | None:
    """A fresh copy of ``doc``'s parked section to mutate, or None if malformed.

    Absent reads as empty — the additive migration. A present value that is not
    ``{"items": [<mapping>, ...], "awaiting_human": null | <mapping>}`` is None,
    and every writer refuses on it: what is there may be the only record of a
    question, and a writer that normalised it away would drop it silently.

    A COPY, so a refusal returned from inside `_document_transaction` has
    mutated nothing and the transaction writes nothing.
    """
    raw = doc.get(PARKED_STATE_KEY)
    if raw is None:
        return {PARKED_ITEMS_KEY: [], PARKED_AWAITING_HUMAN_KEY: None}
    if not isinstance(raw, dict):
        return None
    items = raw.get(PARKED_ITEMS_KEY, [])
    awaiting = raw.get(PARKED_AWAITING_HUMAN_KEY)
    if not isinstance(items, list) or not all(isinstance(i, dict) for i in items):
        return None
    if awaiting is not None and not isinstance(awaiting, dict):
        return None
    return {
        PARKED_ITEMS_KEY: [dict(item) for item in items],
        PARKED_AWAITING_HUMAN_KEY: dict(awaiting) if awaiting is not None else None,
    }


def _parked_shape_refusal() -> dict:
    """The refusal every writer owes a `parked` value of the wrong shape."""
    return _named_refusal(
        f"state.json's {PARKED_STATE_KEY!r} value is not the shape the park "
        "door writes, so it refuses rather than normalise it away — what is "
        "there may be the only record of a question.",
        f"Repair state.json's {PARKED_STATE_KEY!r} to "
        f"{{{PARKED_ITEMS_KEY!r}: [...], {PARKED_AWAITING_HUMAN_KEY!r}: null}}, "
        "the shape schemas/vocab.py documents, then retry.",
        PARK_STATE_UNREADABLE,
    )


def _next_parked_id(items: list[dict]) -> str:
    """The next `P-NNN`: one past the highest sequence any item carries."""
    highest = 0
    for item in items:
        ident = item.get(PARKED_FIELD_ID)
        if isinstance(ident, str) and ident.startswith(PARKED_ID_PREFIX):
            sequence = ident[len(PARKED_ID_PREFIX):]
            if sequence.isdigit():
                highest = max(highest, int(sequence))
    return f"{PARKED_ID_PREFIX}{highest + 1:03d}"
