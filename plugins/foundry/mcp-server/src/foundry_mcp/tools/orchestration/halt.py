"""HALTED: the one writer, the one reader, and the cap that reaches it.

Survey block P. OUTSIDE the verifier set by GI-033, and reached from
`transitions.py` through a ONE-WAY seam: the halt token and the terminal seal
dispatch into here, and nothing flows back.
"""
from __future__ import annotations

from pathlib import Path

from foundry_mcp.schemas.vocab import (
    DEFECT_TIERS,
    HALT_REASON_CAP_REACHED,
    RUN_PHASE_HALTED,
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
)
from pathlib import Path
from foundry_mcp.tools.orchestration.report_seal import (
    _lead_prose_clause,
    _regenerate_report_preserving_lead_prose,
    _seal_run_report,
    _sealed_report_sentence,
)














def _seal_halted(
    fdir: Path,
    project_root: str,
    *,
    reason: str,
    text: str = "",
    token: str,
    detail: str = "",
    update_phase,
) -> dict:
    """fallout FR-046 / CT-004 / ST-001 / AC-025 / AC-029 — THE ONE HALTED WRITER.

    Both endings a run can have that are not DONE come through here: the cycle
    cap (`_halt_if_capped`, reason `cap_reached`) and the lead's own ruling
    (`Foundry-Phase('halt', reason, text)`). One writer, so the two produce the
    same document, the same regenerated report and the same history row; a
    second writer is how two endings come to disagree about what HALTED means.

    THIS IS A TRANSITION, NOT A REFUSAL, and the distinction is the whole
    requirement (FR-045 / A-048). A refusal would leave the run sitting where it
    was with the lead free to call the same token again, having produced nothing
    — a cap that only annoys. Instead `state.json` becomes HALTED, the report is
    generated naming every open LIVE and LATENT defect, and the call returns
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

    # FR-045: "the report is written naming every open LIVE and LATENT defect".
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
    # regenerate it. CT-014 SPECIFIES that failure branch (the unreadable-ledger
    # refusal), so it is designed and reachable, not a theoretical one.
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

    counts = (
        f"{len(blocking['live'])} open LIVE, "
        f"{len(blocking['unknown'])} untiered and "
        f"{len(blocking['latent'])} open LATENT defect(s)"
    )
    return {
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




def _halt_if_capped(
    fdir: Path, project_root: str, token: str, outcome: dict, *, update_phase
) -> dict | None:
    """ST-008 / CT-016 — halt the run instead of opening GRIND number N+1.

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
    is the one that halts (AC-037 / OT-026). ``max_cycles`` 0 is unbounded and
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
