"""HALTED: the one writer, the one reader, and the cap that reaches it.

Survey block P. OUTSIDE the verifier set by GI-033, and reached from
`transitions.py` through a ONE-WAY seam: the halt token and the terminal seal
dispatch into here, and nothing flows back.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from foundry_mcp.schemas.vocab import (
    DEFECT_TIERS,
    HALT_PROOF_PARKED_ANSWER,
    HALT_PROOF_STOP_TOKEN,
    HALT_REASON_CAP_REACHED,
    PARKED_FIELD_ANSWER,
    PARKED_FIELD_ANSWER_IS_HALT,
    PARKED_FIELD_ANSWERED_AT,
    PARKED_FIELD_ID,
    PARKED_ITEMS_KEY,
    PARKED_STATE_KEY,
    RUN_PHASE_HALTED,
    STOP_TOKEN_FIELD_CONSUMED_AT,
    STOP_TOKEN_FIELD_CREATED_AT,
    STOP_TOKEN_FIELD_NONCE,
    STOP_TOKEN_FIELD_RUN,
    STOP_TOKEN_FILENAME,
    STOP_TOKEN_MAX_AGE_SECONDS,
    TIER_HARDENING,
    TIER_UNKNOWN,
    defect_tier,
)
from foundry_mcp.tools.artifacts import (
    _document_transaction,
)
from foundry_mcp.tools.foundry_state import (
    blocking_defects,
    current_cycle,
    now_iso,
    # fallout AC-022 / GI-014 (C-077) — the reader that HAS every tier's
    # bucket. `blocking_defects` returns only the blocking three by design.
    open_defects_by_tier,
    read_document,
)
from pathlib import Path
from foundry_mcp.tools.orchestration.report_seal import (
    _lead_prose_clause,
    _regenerate_report_preserving_lead_prose,
    _seal_run_report,
    _sealed_report_sentence,
)














# --------------------------------------------------------------------------- #
# should-not-stop — HUMAN-ORIGIN PROOF FOR A POST-CAST `user_stop`.
#
# From start_cast to NYQUIST the halt door takes `user_stop` from the lead only
# on proof the human asked for it, and there are exactly two proofs:
#
#   * an unused /foundry:stop token for this run, written into the archive by
#     `commands/stop.md`'s own shell step when the human invoked the command;
#   * a parked question the human answered with halt, recorded through the park
#     door with its explicit halt indicator.
#
# THE READ AND THE WRITE ARE BOTH HERE, AND THEY ARE NOT THE SAME CALL.
# `transitions._halt_preconditions` asks `_human_halt_proof` over the one seam
# the layering allows, and that routine is shared by Foundry-Gate('halt') and
# Foundry-Phase('halt'), so the question has to be read-only: a gate that
# reported a token as proof and burned it doing so would leave the very
# transition it had cleared refused. The token is consumed by `_seal_halted`,
# after the HALTED write, on the one path that actually halts.
#
# Both read `state.json` and the archive DIRECTLY through the leaf reader and
# not through `park.py`, which owns the writers: a verifier reaches this module
# and nothing past it, so what is read here is read from the file.
# --------------------------------------------------------------------------- #

#: How far in the future a token's `created_at` may sit before it is refused.
#: Clock skew between the shell step and the server is seconds; a token dated
#: further ahead than this was not written by that shell step.
_STOP_TOKEN_FUTURE_SKEW_SECONDS = 300


def _human_halt_proof(fdir: Path) -> dict:
    """The human-origin proof a post-CAST `user_stop` would halt on. READ-ONLY.

    Returns ``{"kind", "detail", "problems"}`` plus the proof's own identity.
    ``kind`` is a `HALT_PROOF_KINDS` member, or None when nothing proves the
    human asked. ``detail`` is the sentence naming what proved it. ``problems``
    names everything looked at that did NOT count — a missing, reused, stale or
    foreign token, and the absence of a halt answer — so a refusal built on this
    says why and not only that. A token proof carries ``nonce``, which is how
    the seal consumes exactly the token that was judged; a parked proof carries
    ``item_id``.

    An unused token is preferred when both exist: it is the human's most direct
    act, and the one of the two a halt consumes. Never raises.
    """
    token, token_problem = _usable_stop_token(fdir)
    if token is not None:
        return {
            "kind": HALT_PROOF_STOP_TOKEN,
            "detail": (
                f"the human's /foundry:stop token for run {fdir.name}, written "
                f"at {token.get(STOP_TOKEN_FIELD_CREATED_AT)}"
            ),
            "nonce": token.get(STOP_TOKEN_FIELD_NONCE),
            "problems": [],
        }
    item = _halt_answered_parked_item(fdir)
    if item is not None:
        return {
            "kind": HALT_PROOF_PARKED_ANSWER,
            "detail": (
                f"the human's halt answer to parked item "
                f"{item[PARKED_FIELD_ID]}: {item.get(PARKED_FIELD_ANSWER)!r}"
            ),
            "item_id": item[PARKED_FIELD_ID],
            "problems": [token_problem],
        }
    return {
        "kind": None,
        "detail": "",
        "problems": [token_problem, "no parked question has been answered with halt"],
    }


def _usable_stop_token(fdir: Path) -> tuple[dict | None, str]:
    """This run's /foundry:stop token when it still proves a halt, else why not.

    Refused, each by its own sentence: a token that is absent, unreadable,
    names another run, already carries `consumed_at`, carries no readable UTC
    `created_at`, is older than `STOP_TOKEN_MAX_AGE_SECONDS`, or is dated in the
    future. A consumed token is refused as REUSED rather than as missing, which
    is why the seal stamps it instead of deleting it.
    """
    path = fdir / STOP_TOKEN_FILENAME
    if not path.exists():
        return None, (
            f"there is no /foundry:stop token in the run archive "
            f"({STOP_TOKEN_FILENAME} is absent)"
        )
    token, problem = read_document(path)
    if problem is not None:
        return None, f"the /foundry:stop token cannot be read: {problem}"
    named = token.get(STOP_TOKEN_FIELD_RUN)
    if named != fdir.name:
        return None, (
            f"the /foundry:stop token names run {named!r}, not this run "
            f"({fdir.name!r})"
        )
    if token.get(STOP_TOKEN_FIELD_CONSUMED_AT):
        return None, (
            f"the /foundry:stop token was already used — consumed at "
            f"{token[STOP_TOKEN_FIELD_CONSUMED_AT]} by the halt it proved, and "
            "a token proves one halt"
        )
    created = _stop_token_created_at(token.get(STOP_TOKEN_FIELD_CREATED_AT))
    if created is None:
        return None, "the /foundry:stop token carries no readable UTC created_at"
    age = (datetime.now(tz=timezone.utc) - created).total_seconds()
    if age > STOP_TOKEN_MAX_AGE_SECONDS:
        return None, (
            f"the /foundry:stop token is stale: it was written "
            f"{int(age // 60)} minute(s) ago, and a token proves a halt for "
            f"{STOP_TOKEN_MAX_AGE_SECONDS // 60} minutes"
        )
    if age < -_STOP_TOKEN_FUTURE_SKEW_SECONDS:
        return None, (
            "the /foundry:stop token is dated in the future, so no "
            "/foundry:stop invocation wrote it"
        )
    return token, ""


def _stop_token_created_at(value: object) -> datetime | None:
    """A token's `created_at` as an aware datetime, or None. Never raises.

    A stamp with no timezone is None too: the shell step writes UTC with its
    offset, so a naive stamp is one that step did not write, and guessing its
    zone is how a stale token would be read as a fresh one.
    """
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.strip())
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def _halt_answered_parked_item(fdir: Path) -> dict | None:
    """The most recent parked item the human answered with halt, or None.

    Answered with halt is `answer_is_halt` exactly True AND an `answered_at`:
    the park door sets both in one write, and a record carrying only one of
    them was not written by it. Tolerant of every other shape — an absent
    `parked` key and a malformed one both read as no such item.
    """
    state, _problem = read_document(fdir / "state.json")
    parked = state.get(PARKED_STATE_KEY)
    items = parked.get(PARKED_ITEMS_KEY) if isinstance(parked, dict) else None
    for item in reversed(items if isinstance(items, list) else []):
        if (
            isinstance(item, dict)
            and item.get(PARKED_FIELD_ANSWER_IS_HALT) is True
            and item.get(PARKED_FIELD_ANSWERED_AT)
            and isinstance(item.get(PARKED_FIELD_ID), str)
        ):
            return item
    return None


def _consume_stop_token(fdir: Path, nonce: object) -> tuple[bool, str]:
    """Stamp `consumed_at` on the token a halt was sealed on.

    Returns ``(consumed, error)``. Only the token the halt door JUDGED is
    stamped — matched by its nonce — so a token rewritten between the check and
    the seal is left alone rather than consumed on another token's authority.
    """
    try:
        with _document_transaction(fdir / STOP_TOKEN_FILENAME) as token:
            if (
                token.get(STOP_TOKEN_FIELD_NONCE) != nonce
                or token.get(STOP_TOKEN_FIELD_CONSUMED_AT)
            ):
                return False, (
                    "the token on disk is no longer the unused one the halt "
                    "door judged"
                )
            token[STOP_TOKEN_FIELD_CONSUMED_AT] = now_iso()
    except OSError as exc:
        return False, f"{type(exc).__name__}: {exc}"
    return True, ""


def _spend_halt_proof(fdir: Path, proof: dict) -> dict:
    """What the seal did with the proof it halted on, as the result reports it.

    A token is consumed here, after the HALTED write, so it proves this halt
    and no other. A parked answer needs no write: the halt it proves makes the
    run HALTED, and nothing leaves HALTED, so it can prove nothing further.
    """
    record = {"kind": proof.get("kind"), "detail": proof.get("detail", "")}
    if proof.get("kind") == HALT_PROOF_STOP_TOKEN:
        consumed, error = _consume_stop_token(fdir, proof.get("nonce"))
        record["consumed"] = consumed
        record["consume_error"] = error
    elif proof.get("kind") == HALT_PROOF_PARKED_ANSWER:
        record["item_id"] = proof.get("item_id")
    return record


def _seal_halted(
    fdir: Path,
    project_root: str,
    *,
    reason: str,
    text: str = "",
    token: str,
    detail: str = "",
    update_phase,
    proof: dict | None = None,
) -> dict:
    """fallout FR-046 / CT-004 / ST-001 / AC-025 / AC-029 — THE ONE HALTED WRITER.

    Both endings a run can have that are not DONE come through here: the cycle
    cap (`_halt_if_capped`, reason `cap_reached`) and the lead's own ruling
    (`Foundry-Phase('halt', reason, text)`). One writer, so the two produce the
    same document, the same regenerated report and the same history row; a
    second writer is how two endings come to disagree about what HALTED means.

    THIS IS A TRANSITION, NOT A REFUSAL, and the distinction is the whole
    requirement (fallout ST-001 / CT-004 / AC-025). A refusal would leave the run sitting where it
    was with the lead free to call the same token again, having produced nothing
    — a cap that only annoys. Instead `state.json` becomes HALTED, the report is
    generated naming every open defect of every tier (fallout AC-022 / C-077:
    the sentence said "LIVE and LATENT" while `DEFECT_TIERS` has three members,
    and HARDENING is non-blocking, so a halt summary is one of the very few
    places its records reach the lead at all), and the call returns
    ``ok: True``. HALTED is a named terminal state and is emphatically NOT DONE:
    it is where a run that ran out of cycles, or that the lead stopped, stops —
    with its open work written down.

    WRITTEN THROUGH `_update_phase` (AC-029). The cap path used to write
    `state["phase"]` inside a bare transaction, so a halted run's `phase_times`
    kept the phase it stopped in OPEN with no `ended_at` and `phase_history`
    never recorded HALTED at all: the one row that says how a run ended was the
    one row the history did not carry. `_update_phase` closes every open timing
    entry and appends the row, which is what every other transition in this
    module has always done.

    `halted_reason` is stored as ``{"reason": <HALT_REASONS member>, "text":
    <the lead's own words>}``. Neither substitutes for the other (CT-004): the
    member is what `measure-run.py` and the report group on, and the text is why
    THIS run ended, which no closed set can carry. Every reader still accepts the
    bare f-string archives written before this release carry (FR-054); see
    `gates.py#_halted_state`.
    """
    cycle = current_cycle(fdir)
    sentence = f"{reason}: {text}" if text else (detail or reason)

    update_phase(fdir, RUN_PHASE_HALTED)
    with _document_transaction(fdir / "state.json") as doc:
        doc["halted_at_cycle"] = cycle
        doc["halted_reason"] = {"reason": reason, "text": text or detail}
        doc["updated_at"] = now_iso()

    # should-not-stop — THE PROOF A POST-CAST `user_stop` HALTED ON IS SPENT
    # HERE, after the HALTED write and on this path alone. `proof` travels down
    # the seam from `_halt_preconditions`; the cap path passes none and is
    # exactly what it was.
    proof_record = _spend_halt_proof(fdir, proof) if proof is not None else None
    # fallout CT-004 / AC-025 / OT-023: the seal "regenerat[es] REPORT.md with
    # lead prose preserved" as part of the transition.
    # Generated as PART of this transition rather than left to the lead, because
    # a halted run whose open work was never written down is the outcome the
    # halt is supposed to prevent, not a variant of it.
    #
    # D-165 — AND ITS VERDICT IS READ, BECAUSE THE MESSAGE ASSERTS IT.
    # ---------------------------------------------------------------
    # `report` was discarded. Driven at the real door: max_cycles 2 at cycle 2,
    # one open LIVE and one open LATENT defect, and a deliberately corrupt
    # verdicts.json — this returned ok True, wrote phase HALTED and said "The
    # report has been generated naming 1 open LIVE, 0 untiered and 1 open LATENT
    # defect(s)", while REPORT.md did not exist on disk and the nested result
    # carried ok False, "verdicts.json is not valid JSON". Every later call then
    # compounded it: `_halted_refusal` told the operator to read a report that
    # was never written, and HALTED has no exit by design, so nothing would ever
    # regenerate it. CT-004 makes the regenerated report part of what this
    # transition PRODUCES, so a generation that fails is a designed and
    # reachable branch of it rather than a theoretical one.
    #
    # The transition still HAPPENS — a run that ran out of cycles has run out of
    # cycles whether or not its ledgers can be rendered. What changes is that
    # the outcome is RECORDED and SAID: the halt names the failure, and every
    # later refusal names the one call that can still write the report.
    #
    # D-224: through the SAME preserving regeneration both F6 doors run. The cap
    # path carried its own copy of the overwrite, so a run that ended at the cap
    # lost the lead's appended prose exactly as a run that ended at DONE did — a
    # rule enforced at one terminal transition and not the other is one the run
    # walks around by ending the other way.
    sealed = _regenerate_report_preserving_lead_prose(project_root, fdir)
    report = sealed["report"]
    report_ok = sealed["report_generated"]
    report_error = sealed["report_error"]
    if not report_ok:
        # A second short transaction rather than one around the generator: the
        # report is generated AFTER `phase` is HALTED so that it renders the
        # halted run, and `_document_transaction` is an fcntl-locked critical
        # section that must not be held across it.
        with _document_transaction(fdir / "state.json") as doc:
            doc["halted_report_error"] = report_error
            doc["updated_at"] = now_iso()
    blocking = blocking_defects(
        fdir, tiers=DEFECT_TIERS, unknown_tier=TIER_UNKNOWN, tier_of=defect_tier
    )

    # fallout AC-022 / GI-014 (casting 10's concern C-077) — THE BACKLOG THIS
    # SEAL NAMES IS EVERY TIER, AND IT WAS TWO.
    # ----------------------------------------------------------------------
    # `counts` read "N open LIVE, N untiered and N open LATENT defect(s)", and
    # `DEFECT_TIERS` has THREE members. A run halted with open HARDENING records
    # reported them in no field of this result and in no clause of the sentence
    # `display.py` renders — the same AC-022 under-statement D-162/D-163/D-164
    # closed on three other surfaces, on the one surface their co-dispatch could
    # not reach. HARDENING is non-blocking by design, so a halt summary is one
    # of the very few places its records would ever reach the lead who stopped
    # the run: HALTED exists to record a NAMED BACKLOG, and a backlog missing a
    # third of its vocabulary is not named.
    #
    # DERIVED FROM THE VOCABULARY, so a fourth tier cannot be dropped the same
    # way. `blocking_defects` returns exactly {blocking, live, unknown, latent}
    # and discards the HARDENING bucket `open_defects_by_tier` already builds;
    # casting 10 declined to widen that return unilaterally for a caller that
    # did not yet read it, and was right to. So the count comes from the leaf
    # reader that HAS every bucket — one more read of one ledger, not a second
    # derivation of the rule (GI-024) — and the sentence is built by walking
    # `DEFECT_TIERS` rather than by naming members.
    by_tier = open_defects_by_tier(
        fdir, tiers=DEFECT_TIERS, unknown_tier=TIER_UNKNOWN, tier_of=defect_tier
    )
    clauses = [f"{len(by_tier.get(t) or [])} open {t}" for t in sorted(DEFECT_TIERS)]
    clauses.append(f"{len(blocking['unknown'])} untiered")
    counts = ", ".join(clauses[:-1]) + f" and {clauses[-1]} defect(s)"
    result = {
        "ok": True,
        "halted": True,
        "phase": RUN_PHASE_HALTED,
        "cycle": cycle,
        "halted_at_cycle": cycle,
        "halted_reason": reason,
        "halted_text": text or detail,
        "requested_token": token,
        "report": report,
        "report_generated": report_ok,
        "report_error": report_error,
        # D-224: what the halt carried over, named where the operator reads it.
        "lead_prose_lines": sealed["lead_prose_lines"],
        "lead_prose_error": sealed["lead_prose_error"],
        "open_live_defects": blocking["live"],
        "open_unknown_tier_defects": blocking["unknown"],
        "open_latent_defects": blocking["latent"],
        # fallout AC-022 / GI-014 (C-077) — the third member, in a FIELD and not
        # only in the sentence. A lead reading the payload rather than the
        # rendered line must reach the same backlog.
        "open_hardening_defects": by_tier.get(TIER_HARDENING) or [],
        # D-233: the prose clauses come from `_lead_prose_clause`, the ONE
        # spelling the two F6 doors say through `_sealed_report_sentence`. The
        # cap path regenerates the report exactly as they do and said nothing
        # about what the regeneration did to the lead's own additions — neither
        # that they were carried, nor, when the write-back failed, that they
        # were LOST. `display.py` renders this `message` and no other field, so
        # a fact absent from it is a fact the operator never sees.
        "message": (
            (
                f"Run HALTED — {sentence}. The report has been generated naming "
                f"{counts}. HALTED is not DONE: this run stopped with open "
                "work, and the report says what."
                + _lead_prose_clause(sealed)
            )
            if report_ok
            else (
                f"Run HALTED — {sentence}. The report could NOT be generated: "
                f"{report_error}. The {counts} named above are read from "
                "defects.json, which is intact; it is the report that is "
                "missing. Repair what the error names, then call Foundry-Report "
                "— it is not a phase transition, so it still runs on a halted "
                "run. HALTED is not DONE: this run stopped with open work."
                + _lead_prose_clause(sealed)
            )
        ),
    }
    # should-not-stop — which human act this halt was sealed on, on the result
    # the operator reads. Only on the path that has one: a cap has no proof,
    # and a field that means something on one path and nothing on the other is
    # the arrangement `_halt_if_capped` already declines for `max_cycles`.
    if proof_record is not None:
        result["halt_proof"] = proof_record
        spent = (
            "" if proof_record.get("consume_error") in (None, "")
            else (
                " — the token could NOT be marked consumed "
                f"({proof_record['consume_error']}), but the run is HALTED, so "
                "it can prove nothing further"
            )
        )
        result["message"] += (
            f" Sealed on human-origin proof: {proof_record['detail']}{spent}."
        )
    return result




def _halt_if_capped(
    fdir: Path, project_root: str, token: str, outcome: dict, *, update_phase
) -> dict | None:
    """fallout ST-002 / ST-015 / FR-062 / AC-060 — halt the run instead of
    opening GRIND number N+1.

    Returns None when the run may proceed, otherwise the SUCCESS result of the
    HALTED transition. Called from both transitions that open a GRIND.

    fallout FR-062 / GI-032 / ST-015 / AC-060 — THE CAP IS NOT READ HERE ANY
    MORE. `would_halt` arrives on ``outcome``, computed by
    `_grind_start_preconditions` from the single `persisted_max_cycles` read
    this run has, so `Foundry-Gate('grind')` can REPORT the fact without acting
    on it and no transition branch reads the cap itself. This function is now
    the ACTION the fact licenses, and nothing else.

    THE ARITHMETIC, which lives with the fact rather than here. The server
    counter advances only at `inspect_start`, so the GRIND a call is about to
    open is always `cycle + 1`. With `max_cycles` 2: GRIND 1 opens at counter 0,
    GRIND 2 at counter 1, and the call at counter 2 would open GRIND 3 — which
    is the one that halts (fallout ST-015 / AC-060 / OT-044). ``max_cycles`` 0 is unbounded and
    is the default, so a run that never passed the flag is unaffected.
    """
    if not outcome.get("would_halt"):
        return None
    max_cycles = outcome["max_cycles"]
    opening = outcome["opening"]
    sealed = _seal_halted(
        fdir,
        project_root,
        # CT-005: the cap path writes the MEMBER, named in the vocabulary rather
        # than spelled at the door, so the transition that halts on the cap and
        # the report section that groups by reason cannot come to disagree about
        # which member that is.
        reason=HALT_REASON_CAP_REACHED,
        text=(
            f"--max-cycles {max_cycles} reached: opening GRIND cycle {opening} "
            f"would exceed it"
        ),
        token=token,
        update_phase=update_phase,
    )
    # The cap this run was HELD to, on the result the operator reads, beside the
    # cycle the seal recorded. `_seal_halted` cannot name it: a lead's ruling has
    # no cap, and a field that means something on one path and nothing on the
    # other is worse than a field only the path that has it carries.
    sealed["max_cycles"] = max_cycles
    sealed["opening_cycle"] = opening
    return sealed


# --------------------------------------------------------------------------- #
# fallout GI-033 / FR-063 / AC-061 — THE TERMINAL SEAL, THROUGH THE ONE SEAM.
#
# GI-033 names the exception in full: "transitions dispatch the `halt` token AND
# THE TERMINAL SEAL through a one-way seam into halt.py". `transitions.py` was
# doing the first half and reaching `report_seal.py` directly for the second,
# which is a SECOND verifier-to-lifecycle crossing where the rule permits one.
#
# These are the seal half of that seam. Thin by design: they exist so the
# crossing is written in one place and nothing is re-decided here — the two F6
# doors and the halt path all reach the same `report_seal` implementation, and
# `halt.py` already reaches it for the HALTED regeneration, so no new coupling
# is created by routing the F6 seal alongside it. Nothing flows back.
# --------------------------------------------------------------------------- #


def seal_run_report(project_root: str, fdir) -> dict:
    """`report_seal._seal_run_report`, through the transitions-to-halt seam."""
    return _seal_run_report(project_root, fdir)


def sealed_report_sentence(sealed: dict, fdir) -> str:
    """`report_seal._sealed_report_sentence`, through the same seam."""
    return _sealed_report_sentence(sealed, fdir)
