"""HALTED: the one writer, the one reader, and the cap that reaches it.

Survey block P. OUTSIDE the verifier set by GI-033, and reached from
`transitions.py` through a ONE-WAY seam: the halt token and the terminal seal
dispatch into here, and nothing flows back.
"""
from __future__ import annotations

from pathlib import Path

from foundry_mcp.schemas.vocab import (
    HALT_REASON_CAP_REACHED,
    REPORT_MD_FILENAME,
    RUN_PHASE_HALTED,
    halt_reason,
)
from foundry_mcp.tools.artifacts import (
    _document_transaction,
)
from foundry_mcp.tools.foundry_state import (
    current_cycle,
    halted_state,
    now_iso,
)
from pathlib import Path
from foundry_mcp.tools.orchestration.report_seal import (
    _lead_prose_clause,
    _regenerate_report_preserving_lead_prose,
    _seal_run_report,
    _sealed_report_sentence,
)




def _persisted_max_cycles(state: dict) -> int:
    """CT-016 — THE ONE READ of `state.json.max_cycles`, in the door's terms.

    Returns the cap in force: a positive int, or 0 for "no cap", which is the
    default and means unbounded.

    D-225 — THE DOOR ACCEPTED A CAP THIS READ SILENTLY DISCARDED.
    ------------------------------------------------------------
    `Foundry-Init` advertises `max_cycles` as `{"type": "integer"}` and
    `server.py`'s `_argument_refusal` validates it with Draft202012Validator,
    in which a zero-fraction float IS an integer — so `2.0` is ACCEPTED at the
    door and `foundry_init` persists `2.0`. The guard below then read
    `isinstance(max_cycles, int)`, and `isinstance(2.0, int)` is False, so the
    cap read as absent. Driven: cap 2.0 persisted, counter at 99,
    `Foundry-Phase('grind_start')` returned ok True and phase F3 — an operator
    who asked for a cap of 2 opened GRIND cycle 100 with no notice. The schema
    had no `minimum` either, so `-1` was accepted at the same door and read
    here as unbounded by the `<= 0` arm.

    The fix is on BOTH sides and they meet exactly: `server.py` now advertises
    `minimum: 0`, so a negative cap is refused where the operator can see it
    rather than discarded here; and this read accepts the zero-fraction float
    the schema calls an integer, because JSON has no integer type and `2.0` is
    the integer 2 by the rule the door validated against.

    WHY NORMALISE HERE RATHER THAN ONLY AT THE DISPATCH. `state.json` is not
    always written by this server's current door — a resumed archive, a
    hand-edited file, a fixture — and the deciding read is the one place that
    must never mistake a cap for its absence. Anything that is not a usable cap
    (a string, a fractional float, a bool, a negative) still reads as 0/no cap,
    because this function cannot refuse: it is consulted from inside a
    transition whose only other answer is "proceed".
    """
    raw = state.get("max_cycles", 0)
    if isinstance(raw, bool):
        return 0
    if isinstance(raw, int):
        return raw if raw > 0 else 0
    if isinstance(raw, float) and raw.is_integer():
        return int(raw) if raw > 0 else 0
    return 0




def _halted_state(fdir: Path) -> dict | None:
    """The run's HALTED record, or None when the run is not halted.

    fallout GI-033 / FR-063 / AC-061 (concern C-017) — THE READ IS THE LEAF'S.

    THE ONLY READ of `state.json.phase == RUN_PHASE_HALTED`, for the reason
    `_current_inspect_mode` is the only read of the recorded width: a terminal
    state that each door decides for itself is a terminal state each door can
    decide differently. What changed is WHERE the only read lives.
    `gates.py` is a VERIFIER and this module is lifecycle, so a gate reaching
    here for the halt record was a second verifier-to-lifecycle crossing on top
    of the one seam GI-033 names — and the record is a document read, which is
    leaf material. `foundry_state.halted_state` holds it now.

    The three closed-set values it needs are supplied here rather than known
    there, because the leaf's own contract is json and pathlib: the HALTED
    phase token, the reason vocabulary, and this module's cap normaliser. That
    last one is why the delegation stays a function rather than becoming an
    import at each caller — `_persisted_max_cycles` is halt.py's, and passing
    it in is what lets the leaf answer without knowing what a cap is.
    """
    return halted_state(
        fdir,
        halted_phase=RUN_PHASE_HALTED,
        reason_of=halt_reason,
        max_cycles_of=_persisted_max_cycles,
    )


def _halted_refusal(fdir: Path, surface: str) -> dict | None:
    """ST-008 / CT-016 / FR-024 / FR-045 / FR-052 — HALTED is terminal, and
    every door that could leave it reads THIS.

    Returns None when the run may proceed, otherwise the refusal `surface`
    should return, carrying `error` / `hint` in the house shape plus the halt
    record. `foundry_gate` reshapes the same two strings into its
    `reason` / `hint` pair; nothing recomputes the judgement.

    D-081 / D-082 — HALTED WAS WRITTEN AND READ BY NOTHING.
    ------------------------------------------------------
    ST-008 says "HALTED is not DONE", CT-016 calls it "a named terminal state
    distinct from DONE", FR-024 says "the run ends in a named HALTED state
    rather than DONE" — and `_halt_if_capped` was the only code in the server
    that mentioned the state at all. It wrote `phase = HALTED` from the two
    doors that open a GRIND and then nothing, anywhere, asked.

    Driven, both halves. (1) max_cycles 2 at cycle 2 with one open LATENT
    defect: `Foundry-Phase('grind_start')` returned ok / halted True and
    state.phase HALTED; two calls later `Foundry-Gate('done')` returned passed
    True with reason None and `Foundry-Phase('done')` returned ok, phase F6,
    "Run archived." The cap exists precisely so the run does not end as DONE,
    and the F6 state recorded nothing about it having fired. (2) From
    state.phase HALTED: `Foundry-Phase('inspect_start')` returned ok, set F2
    and ADVANCED the cycle counter; `cast` returned ok and set F2; `temper`
    returned ok and set F5; `nyquist` returned ok and set F5.5. Only
    `grind_start` and `assay_fail` re-halted, because only they call
    `_halt_if_capped`; every other branch had no HALTED precondition at all. So
    a halted run resumed and kept dispatching with no refusal and no record
    that the cap had been overridden — and FR-052's "Foundry-Next reports
    halted and stops dispatching" rested on lead discipline, which is the thing
    the cap exists to replace.

    NOT A WAIVER AND NOT A RESUME PATH. There is deliberately no token, flag or
    argument that leaves HALTED, because "the operator may override the cap
    in-place" is how a bounded run becomes an unbounded one. The remedy the
    hint names is a NEW run with a higher `--max-cycles`, which starts a fresh
    counter and a fresh state file, so the override is a decision someone makes
    on the record rather than one more call in the loop.
    """
    halted = _halted_state(fdir)
    if halted is None:
        return None
    cycle_text = (
        f"cycle {halted['halted_at_cycle']}"
        if isinstance(halted["halted_at_cycle"], int)
        else "its cycle cap"
    )
    # D-165 — THE REPORT IS ASSERTED ONLY WHERE IT WAS WRITTEN.
    #
    # This said "the report says what" and pointed at REPORT.md unconditionally,
    # because `_halt_if_capped` asserted the same thing unconditionally. On a
    # halt whose report generation failed (CT-014's designed unreadable-ledger
    # branch) the operator was sent to read a file that does not exist, from a
    # state with no exit, with no reason given to regenerate it. Both halves are
    # read from the record the transition now leaves: the presence of the file,
    # and the error the generator returned.
    #
    # THE FILE'S PRESENCE IS THE GROUND TRUTH, and the recorded error only
    # supplies the REASON when it is absent. Reading the recorded error as
    # authoritative would go stale the moment the operator follows this hint and
    # calls Foundry-Report — a refusal that then still said "NOT written" about
    # a file sitting on disk would be this same defect with the sign flipped.
    report_path = fdir / REPORT_MD_FILENAME
    report_error = halted["halted_report_error"]
    report_present = report_path.exists()
    return {
        "error": (
            f"Cannot call {surface} — this run is HALTED. It stopped at "
            f"{cycle_text} because {halted['halted_reason']}. HALTED is a "
            "terminal state and it is NOT DONE: the run ended with open work"
            + (
                " and the report says what."
                if report_present
                else (
                    f", and the report was NOT written — {report_error or 'it is not present at ' + str(report_path)}."
                )
            )
        ),
        "hint": (
            (
                f"Nothing leaves HALTED — no phase token, no gate. Read "
                f"{REPORT_MD_FILENAME}, tell the user what remains open by "
                "tier, and stop. To carry the remaining work forward, start a "
                "NEW run (Foundry-Init) with a higher --max-cycles; the cap is "
                "not overridden in place."
            )
            if report_present
            else (
                "Nothing leaves HALTED — no phase token, no gate — but the "
                "report is not a phase transition and Foundry-Report still "
                "runs on a halted run. Repair what the error above names, call "
                f"Foundry-Report to write {REPORT_MD_FILENAME}, then read it, "
                "tell the user what remains open by tier, and stop. Until it "
                "is written, read defects.json directly: the open work is "
                "recorded there whatever the report generator could not "
                "render. To carry the remaining work forward, start a NEW run "
                "(Foundry-Init) with a higher --max-cycles; the cap is not "
                "overridden in place."
            )
        ),
        "halted": True,
        "phase": RUN_PHASE_HALTED,
        "halted_at_cycle": halted["halted_at_cycle"],
        "halted_reason": halted["halted_reason"],
        "max_cycles": halted["max_cycles"],
        # Named as what it IS rather than always as a path, so a caller cannot
        # read a promise out of the field's presence.
        "report": str(report_path) if report_present else None,
        "report_generated": report_present,
        "report_error": report_error,
    }




def _seal_halted(
    fdir: Path,
    project_root: str,
    *,
    reason: str,
    text: str = "",
    token: str,
    detail: str = "",
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
    `_halted_state`.
    """
    # fallout FR-004 / GI-033 -- LAZY SEAM, written once per symbol.
    # `gates, transitions` import(s) this module, so a module-top import here would
    # close a cycle that takes every tool in this server down at once.
    # Unguarded, so a wiring break fails loudly at the one call site that
    # needs the symbol rather than hiding behind a silent fallback.
    from foundry_mcp.tools.orchestration.gates import _blocking_defects
    from foundry_mcp.tools.orchestration.transitions import _update_phase
    cycle = current_cycle(fdir)
    sentence = f"{reason}: {text}" if text else (detail or reason)

    _update_phase(fdir, RUN_PHASE_HALTED)
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
    blocking = _blocking_defects(fdir)

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
    fdir: Path, project_root: str, token: str, outcome: dict
) -> dict | None:
    """ST-008 / CT-016 — halt the run instead of opening GRIND number N+1.

    Returns None when the run may proceed, otherwise the SUCCESS result of the
    HALTED transition. Called from both transitions that open a GRIND.

    fallout FR-062 / GI-032 / ST-015 / AC-060 — THE CAP IS NOT READ HERE ANY
    MORE. `would_halt` arrives on ``outcome``, computed by
    `_grind_start_preconditions` from the single `_persisted_max_cycles` read
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
