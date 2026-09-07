"""Foundry-Phase: one preconditions routine per token, then the mutation.

Survey blocks W and X. VERIFIER-SET module. Every branch is one call, one
refusal, then the effect — no ledger read of its own, no check the gate for
the same token does not also make.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from datetime import datetime
from foundry_mcp.schemas.vocab import (
    INSPECT_MODES,
    REPORT_MD_FILENAME,
    CONCERN_STATUS_OPEN,
    HALT_REASONS,
    STREAM_WIRE_IDS,
    halt_reason,
    halt_reason_phrase,
)
from foundry_mcp.tools.artifacts import (
    # fallout GI-033 / AC-061 (D-080, concern C-060) — ALIASED, and the alias
    # is load-bearing rather than a leftover: D-134's scan recognises a manifest
    # reader as GUARDED by the NAME it calls, and
    # `tests/test_spawn_progress.py` pins that set to `_manifest_shape_problem`
    # and `_manifest_shape_error`. Calling the leaf's public spelling directly
    # would report every reader here as UNGUARDED, which is worse than an
    # import error because it looks like a finding.
    manifest_shape_problem as _manifest_shape_problem,
    CAST_BASELINE_SHA_MARKER,
    CAST_COMPLETE_MARKER,
    GATE_PASSED_MARKER,
    INSPECT_BOUNDARY_SHA_MARKER,
    INSPECT_CLEAN_MARKER,
    NEXT_ACTION_CALLED_MARKER,
    TASKS_GENERATED_MARKER,
    _artifact_guard,
    _document_transaction,
    _load_json,
    _stream_marker,
)
from foundry_mcp.tools.foundry_state import (
    current_inspect_mode,
    clear_active_run,
    current_cycle,
    finalize_open_phase_entry,
    get_run_dir,
    now_iso,
    open_cross_casting_concerns,
    persisted_max_cycles,
    # fallout ST-005 (D-158) — the leaf's (document, problem) read, so the
    # CONCERN_OPEN rung can name a ledger it could not read instead of
    # inheriting the tolerant reader's empty list for it.
    read_document,
)
from pathlib import Path
from foundry_mcp.tools.orchestration.keyfiles import (
    DIRECTORY_ENTRY_SUFFIX,
    covers_path,
    manifest_spelling,
)
from foundry_mcp.tools.orchestration.escalation import (
    _advance_escalation_exits,
    _escalated_classes,
    _record_escalation_proposals,
)
from foundry_mcp.tools.orchestration.width import (
    NYQUIST_ENTRY_ROLLUP_KEY,
    TEMPER_ENTRY_ROLLUP_KEY,
    _decide_inspect_mode,
    _head_sha,
    _record_cycle_rollup,
    _record_inspect_mode,
    _sight_required,
)

# fallout GI-033 / AC-061 / FR-063 (D-080) — the width refusal is the LEAF's,
# for the reason `gates.py` states at its own import of it: read from both
# layers, and GI-033 makes those two mutually unreachable. Bound under the name
# the refusal-readers scan in `test_transitions.py` resolves.
from foundry_mcp.tools.foundry_state import (
    unrecorded_width_problem as _unrecorded_width_problem,
)
from foundry_mcp.tools.orchestration.evidence_boundary import (
    _sweep_evidence_at_boundary,
    _sweep_refusal,
    _terminal_evidence_refusal,
    _terminal_evidence_state,
)
# fallout GI-033 / FR-063 / AC-061 — THE ONE-WAY SEAM, AND BOTH HALVES OF IT.
#
# GI-033's exception is "transitions dispatch the `halt` token AND THE
# TERMINAL SEAL through a one-way seam into halt.py". The seal half used to
# go straight to `report_seal.py`, which made it a SECOND verifier-to-
# lifecycle crossing where the rule permits exactly one. `seal_run_report`
# and `sealed_report_sentence` are that half, reached here through the same
# module the halt token already goes through. Nothing flows back.
from foundry_mcp.tools.orchestration.halt import (
    _halt_if_capped,
    _seal_halted,
    seal_run_report,
    sealed_report_sentence,
)
from foundry_mcp.tools.orchestration.gates import (
    CASTING_KEY_FILE_CAP,
    _active_teams,
    _streams_complete,
    _halted_sentences,
    _halted_state,
    _GATE_RANK_CONFIG,
    _GATE_RANK_CONFLICT,
    _GATE_RANK_DEFECTS,
    _GATE_RANK_EVIDENCE,
    _GATE_RANK_HALTED,
    _GATE_RANK_MARKER,
    _GATE_RANK_SOURCE,
    _GATE_RANK_STREAMS,
    _GATE_RANK_TEAMS,
    _GATE_RANK_VERDICTS,
    _GATE_RANK_WIDTH,
    _GateLadder,
    _TEAMS_DOWN_HINT,
    _blocking_defects,
    _done_preconditions,
)




def _preconditions_outcome(
    ladder: "_GateLadder", checklist: list[dict], **facts: object
) -> dict:
    """The shape every `_<token>_preconditions` returns, built in one place.

    ``{"passed", "reason", "hint", "checklist", "refusals"}`` plus any
    NON-REFUSING facts the token computed — `would_halt` is the one this run
    adds. Exactly what `_done_preconditions` already returned, which is why
    `_GateLadder.absorb` consumes any of them unchanged.

    `reason` and `hint` are written through the RESULT rather than through two
    locals, for the reason `_done_preconditions` gives: a function that never
    names them as locals has nothing a later arm can overwrite.
    """
    outcome: dict = {
        "passed": ladder.passed,
        "checklist": checklist,
        "refusals": ladder.refusals(),
        **facts,
    }
    outcome["reason"], outcome["hint"] = ladder.outcome()
    return outcome




def _halted_outcome(fdir: Path) -> dict | None:
    """The preconditions outcome a HALTED run owes, or None to carry on.

    fallout AC-062 / AC-008 / GI-011 / GI-029 (D-088) — THE HALTED CHECK IS A
    RUNG THE TOKEN'S ROUTINE MAKES, AND NO LONGER A GUARD THE DOOR MAKES ABOVE
    IT.
    -------------------------------------------------------------------------
    `foundry_gate` and `_phase_transition` both called `_halted_refusal` above
    their branch chains, which made `_halt_preconditions`' `not_already_halted`
    rung and `_done_preconditions`' HALTED rung UNREACHABLE through either
    public door: calling the routine directly returned `passed: False` with a
    rank-0 refusal, while both doors returned the short-circuit's sentence with
    no `refusals` key at all. AC-062 says `Foundry-Phase('halt')` "refuses on
    THAT FUNCTION ALONE", and three of the invariant test's derived cases
    (halt / done / nyquist_done x `_GATE_RANK_HALTED`) were asserting on the
    guard rather than on the rung the derivation named — the test's own `else:`
    branch was written for that path.

    SO EVERY ROUTINE MAKES IT, AND THE SHORT-CIRCUIT SURVIVES. Called first by
    each `_<token>_preconditions`, returning a complete one-rung outcome, so a
    halted run costs one `state.json` read and NOT the evidence sweep
    `_done_preconditions` would otherwise run before refusing. That is the
    property the door-level guard was really buying, and it is kept here where
    the rank already says the same thing: `_GATE_RANK_HALTED` is 0, the lowest
    there is, so no other failing check could out-rank it anyway.

    The refusal's own three fields ride out as non-refusing facts, so both doors
    still publish `halted`, `halted_at_cycle` and `halted_reason` exactly as the
    guard did.
    """
    halted = _halted_state(fdir)
    if halted is None:
        return None
    cycle_text = (
        f"cycle {halted['halted_at_cycle']}"
        if isinstance(halted["halted_at_cycle"], int)
        else "its cycle cap"
    )
    # ONE SPELLING. `_halted_refusal` renders the same pair with its own leading
    # clause for a door that answers directly; the rung takes it bare and lets
    # `_transition_refusal` add the clause. Re-wording it here is the drift both
    # F6 doors have already paid for once.
    reason, hint = _halted_sentences(fdir, halted, cycle_text)
    ladder = _GateLadder()
    ladder.fail(_GATE_RANK_HALTED, reason, hint)
    checklist = [{
        "check": f"run_not_halted (halted_at_cycle={halted['halted_at_cycle']})",
        "ok": False,
        "halted_reason": halted["halted_reason"],
    }]
    # EVERY FACT THE DOOR-LEVEL GUARD PUBLISHED, still published. The guard
    # returned `_halted_refusal`'s whole dict, so a caller reading `report`,
    # `report_generated`, `report_error` or `max_cycles` off a halted refusal
    # kept them; a rung that carried only the reason would have moved the
    # refusal and lost four fields on the way, which is the shape
    # `_transition_refusal`'s own docstring warns about.
    report_path = fdir / REPORT_MD_FILENAME
    report_present = report_path.exists()
    return _preconditions_outcome(
        ladder,
        checklist,
        halted=True,
        halted_at_cycle=halted["halted_at_cycle"],
        halted_reason=halted["halted_reason"],
        max_cycles=halted["max_cycles"],
        report=str(report_path) if report_present else None,
        report_generated=report_present,
        report_error=halted["halted_report_error"],
    )




def _source_phase_rung(
    ladder: "_GateLadder", checklist: list[dict], fdir: Path, token: str
) -> None:
    """The `_PHASE_ENTRY_SOURCES` check, as a rung both doors make.

    A no-op for a token the table does not guard, so every routine may call it
    unconditionally and a row added to the table is enforced at both doors the
    day it is written. Returns the refusal's own two fields so the caller can
    publish them, which is what every branch that returned
    `_phase_entry_source_problem`'s dict verbatim used to carry.

    fallout CT-020 (D-084) — THE FACT IS `source_phase`, AND THE RENAME IS THE
    FIX.
    -------------------------------------------------------------------------
    It was `phase`, and a routine's non-refusing facts are SPREAD into the
    door's result — so at `foundry_gate`, where the result opens
    `{"phase": <the gate token>, ...}` and the spread lands after it, the run's
    CURRENT phase overwrote the gate token in the door's own identity field.
    `Foundry-Gate('temper')` answered `phase: "F0"`, and `display.py#_fmt_
    foundry_gate` renders `PHASE_NAMES.get(phase.upper(), phase)`, so a lead who
    ran the temper gate was shown "Gate RESEARCH: not ready". A regression: the
    pre-wave-1 gate returned `{"phase", "passed", "checklist"}` and spread
    nothing.

    `source_phase` says what the value IS — the phase the transition was
    attempted FROM — and collides with no door's identity. The `accepted_from`
    half is unchanged, because nothing is named that.

    The CHECKLIST entry keeps `phase`: it is nested inside a checklist dict,
    spread into nothing, and `tests/test_inspect_mode.py` reads it there.
    """
    wrong = _phase_entry_source_problem(fdir, token)
    if wrong is not None:
        ladder.fail(_GATE_RANK_SOURCE, wrong["error"], wrong["hint"])
    checklist.append({
        "check": f"entered_from_accepted_phase (token={token})",
        "ok": wrong is None,
        **({"accepted_from": wrong["accepted_from"], "phase": wrong["phase"]}
           if wrong is not None else {}),
    })
    return {} if wrong is None else {
        "accepted_from": wrong["accepted_from"], "source_phase": wrong["phase"]
    }




def _teams_rung(
    ladder: "_GateLadder", checklist: list[dict], project_root: str
) -> None:
    """The registered-team check, in ONE spelling for every token that makes it.

    Three gate branches and one transition branch each carried their own, and
    D-186 is what that cost: one of the three fell back to no hint at all. The
    sentence is the gate's richer form — it names live panes as well as team
    directories — and the transition wraps it in its own "Cannot <do X> — "
    clause, exactly as the `inspect_clean` branch already wraps
    `_blocking_defects`' reason.
    """
    teams_result = _active_teams(project_root)
    if teams_result["active"]:
        parts = []
        if teams_result["teams"]:
            parts.append(f"Team dirs: {', '.join(teams_result['teams'])}")
        if teams_result.get("live_panes"):
            parts.append(f"Live panes: {', '.join(teams_result['live_panes'])}")
        ladder.fail(
            _GATE_RANK_TEAMS,
            f"Active teammates: {'; '.join(parts)}",
            teams_result.get("hint") or _TEAMS_DOWN_HINT,
        )
    checklist.append({
        "check": "no_active_teams",
        "ok": not teams_result["active"],
        "teams": teams_result["teams"],
        "live_panes": teams_result.get("live_panes", []),
    })




def _blocking_defects_rung(
    ladder: "_GateLadder", checklist: list[dict], fdir: Path
) -> dict:
    """The tier-aware open-defect check. Returns the read, for callers that
    report its parts."""
    blocking = _blocking_defects(fdir)
    if blocking["blocking"] > 0:
        ladder.fail(_GATE_RANK_DEFECTS, blocking["reason"], blocking["hint"])
    checklist.append({
        "check": (
            f"zero_blocking_defects (live={len(blocking['live'])} "
            f"unknown_tier={len(blocking['unknown'])} "
            f"latent_backlog={len(blocking['latent'])})"
        ),
        "ok": blocking["blocking"] == 0,
        "live": blocking["live"],
        "unknown_tier": blocking["unknown"],
        "latent_backlog": blocking["latent"],
    })
    return blocking




def _all_verified_rung(
    ladder: "_GateLadder", checklist: list[dict], fdir: Path, destination: str
) -> None:
    """fallout AC-010 — `verdict != "VERIFIED"` as ONE named predicate.

    It was hand-written at three sites (the temper gate, the nyquist gate and
    `_done_preconditions`) and present at NO transition, which is D-240 and
    D-241 in one sentence. One predicate, one caller shape, both doors.
    """
    verdicts = _load_json(fdir / "verdicts.json")
    non_verified = sum(
        1 for r in verdicts.get("requirements", []) if r.get("verdict") != "VERIFIED"
    )
    if non_verified > 0:
        ladder.fail(
            _GATE_RANK_VERDICTS,
            f"{non_verified} requirement(s) not verified",
            "Every THIN / PARTIAL requirement is a defect, not a follow-up. "
            f"Fix them and re-run ASSAY before entering {destination}.",
        )
    checklist.append({
        "check": f"all_verified (non_verified={non_verified})",
        "ok": non_verified == 0,
    })




def _boundary_evidence_rung(
    ladder: "_GateLadder",
    checklist: list[dict],
    fdir: Path,
    project_root: str,
    *,
    entry: dict,
    full: bool,
    token: str,
) -> dict:
    """The INSPECT-boundary evidence sweep, as a rung BOTH doors make.

    fallout FR-058 / GI-029 / ST-012 / AC-056 / CT-013 — THE SWEEP IS A
    PRECONDITION, NOT AN ARM IN THE TRANSITION.
    -----------------------------------------------------------------------
    "Exactly the preconditions function; no extra reads or refusals" (FR-058).
    Three branches — `cast`, `inspect_start` and `temper` — each swept the
    corpus themselves and each returned `_sweep_refusal(...)` on a mismatch,
    while none of the three routines read the corpus at all. Driven with one
    non-reproducing log: `Foundry-Gate('inspect')`, `('inspect_start')` and
    `('temper')` all answered `passed: True` while the matching
    `Foundry-Phase` calls refused, and because the refusal was built outside
    the ladder it carried `refusals: None` — so the one thing both doors
    publish did not contain the check that actually stopped the crossing.
    That is D-240's shape restored at a fourth site, which is exactly what the
    per-token routines exist to make impossible.

    `_nyquist_preconditions` is the shape copied here: take the sweep as a
    rung, hand the record back as a NON-REFUSING fact, and let the branch
    consume the record the rung produced rather than sweeping again.

    THE WIDTH DECISION COMES WITH IT, because the scope of the sweep IS the
    width decision (`_decide_inspect_mode` writes nothing, so computing it here
    costs a read and decides nothing twice). The branch reads `inspect_entry`
    off the outcome and records it after the crossing is allowed.

    The refusal strings stay `_sweep_refusal`'s, minus the clause
    `_transition_refusal` now supplies, so a lead reads the same sentence it
    read before — including the named logs and the retry naming this token.
    """
    # NOT TAKEN WHEN THE CROSSING IS ALREADY REFUSED, and that is a statement
    # about what a measurement MEANS, not only about what it costs. A
    # whole-corpus re-execution is minutes, and this rung is the last one every
    # routine adds — so a token whose SOURCE PHASE rung has already failed would
    # otherwise sweep the corpus for a crossing that cannot happen from where
    # the run is. `Foundry-Gate('inspect')` from F3 is exactly that call: it
    # guards the `cast` transition, which is refused from F3 on its first rung,
    # and the evidence verdict could not change the answer.
    #
    # Both doors take the same branch, because both reach it through this one
    # function — so gate and transition still refuse the identical set, which is
    # the whole of AC-056. What differs is only whether an already-decided
    # checklist row was measured, and the row says which.
    if not ladder.passed:
        checklist.append({
            "check": "evidence_reproduces_at_head (not taken)",
            "ok": True,
            "refuses": False,
            "detail": (
                "an earlier rung already refuses this crossing, so the corpus "
                "was not re-executed for a transition that cannot happen"
            ),
        })
        return {"inspect_entry": entry, "evidence_sweep": {}, "mismatches": []}

    sweep = _sweep_evidence_at_boundary(fdir, project_root, entry, full=full)
    if not sweep["ok"]:
        refusal = _sweep_refusal(sweep, current_cycle(fdir), token=token)
        ladder.fail(
            _GATE_RANK_EVIDENCE,
            refusal["error"].replace("Cannot cross into INSPECT — ", ""),
            refusal["hint"],
        )
    checklist.append({
        "check": (
            "evidence_reproduces_at_head "
            f"(scope={sweep['record'].get('scope', '?')}, "
            f"corpus={sweep['record'].get('corpus_size', 0)}, "
            f"mismatches={len(sweep['mismatches'])})"
        ),
        "ok": bool(sweep["ok"]),
    })
    return {
        "inspect_entry": entry,
        "evidence_sweep": sweep["record"],
        "mismatches": sweep["mismatches"],
    }




def _start_cast_preconditions(fdir: Path, project_root: str) -> dict:
    """Preconditions for `start_cast` — gates `validate` and `cast`.

    The manifest has to exist, parse, carry at least one casting, hold every
    casting to the eight-key_file cap and give no two castings the same file.
    Every one of those was a gate-only check and `start_cast` was
    `_update_phase(fdir, "F1")` under the halted guard alone, so a CAST wave
    could be opened over a manifest the gate would have refused.

    The first three used to be EARLY RETURNS carrying no checklist. They are
    rungs now, so a run with no manifest still reports what the other checks
    would have said — which is the property `_GateLadder` exists for.
    """
    # fallout AC-062 / AC-008 / GI-011 (D-088) — THE HALTED RUNG, MADE BY
    # THE ROUTINE RATHER THAN BY THE DOOR ABOVE IT. Short-circuits, so a
    # halted run costs one state.json read and none of the work below.
    if (halted := _halted_outcome(fdir)) is not None:
        return halted

    checklist: list[dict] = []
    ladder = _GateLadder()

    manifest = fdir / "castings" / "manifest.json"
    data = _load_json(manifest) if manifest.exists() else {}
    if not manifest.exists():
        ladder.fail(
            _GATE_RANK_CONFIG,
            "No manifest.json",
            "Run F0.5 DECOMPOSE first to create castings",
        )
    elif (records := _manifest_shape_problem(data)) is not None:
        ladder.fail(
            _GATE_RANK_CONFIG,
            records,
            "Re-run F0.5 DECOMPOSE — the manifest's records are unusable",
        )
    checklist.append({"check": "manifest_exists", "ok": manifest.exists()})

    castings = data.get("castings", []) if isinstance(data.get("castings"), list) else []
    count = len(castings)
    if manifest.exists() and count < 1:
        ladder.fail(
            _GATE_RANK_CONFIG,
            "No castings in manifest",
            "Add castings before CAST",
        )
    checklist.append({"check": f"castings_count={count}", "ok": count >= 1})

    oversized = []
    for c in castings:
        if not isinstance(c, dict):
            continue
        kf = len(c.get("key_files", []) or [])
        if kf > CASTING_KEY_FILE_CAP:
            oversized.append({"id": c.get("id"), "title": c.get("title", ""), "key_files": kf})
    if oversized:
        names = ", ".join(f"#{c['id']} ({c['key_files']} files)" for c in oversized)
        ladder.fail(
            _GATE_RANK_CONFIG,
            f"Oversized castings: {names}. Max {CASTING_KEY_FILE_CAP} key_files per casting.",
            "Split large castings into smaller ones (2-5 tasks, 2-8 files "
            "each). No teammate should get 1000 lines of work.",
        )
        checklist.append({"check": "casting_size", "ok": False, "oversized": oversized})
    else:
        checklist.append({"check": "casting_size", "ok": True})

    # fallout FR-009 / GI-011 (D-170, casting 7's concern C-079) — THE SAME
    # RULE THIS RUN'S OTHER DOOR ASKS, ASKED THE SAME WAY.
    # ----------------------------------------------------------------------
    # `Foundry-Gate('validate')` maps to this transition and this rung computes
    # the no-file-overlap rule that `Foundry-Validate-Castings` dimension 3
    # computes. Both compared `key_files` by exact string membership, so neither
    # saw a directory entry — and when casting 7 corrected its half at 5f6e7e9,
    # the two doors began giving OPPOSITE answers about the same manifest: one
    # where casting A names `tools/orchestration/` and casting B names a file
    # inside it now FAILED `Foundry-Validate-Castings` and PASSED
    # `Foundry-Gate('validate')`. Two doors, one rule, two answers is the exact
    # shape this release exists to close, and it was introduced this cycle.
    #
    # A DIRECTORY ENTRY OVERLAPS WHAT IT COVERS. The reading is
    # `keyfiles.covers_path`'s, which is the leaf both layers may read — this
    # module is a VERIFIER and `foundry_validate.py` is lifecycle, so importing
    # casting 7's statement is the one thing GI-033 forbids outright, and the
    # boundary guard's own arithmetic ("a symbol read from BOTH can live only in
    # a leaf") gives the remedy this uses instead.
    #
    # THE OVERLAP IS REPORTED AT THE COVERED PATH, not at the directory, and
    # names the entry it came through: a file two castings both reach appears in
    # nobody's `key_files` literally, so a refusal naming only the file sends
    # the lead looking for a line that is not there. Same shape casting 7's
    # `via_directory` record carries.
    declared: list[tuple[str, int]] = []
    for c in castings:
        if not isinstance(c, dict):
            continue
        cid = c.get("id", 0)
        for f in c.get("key_files", []) or []:
            if isinstance(f, str) and manifest_spelling(f):
                declared.append((manifest_spelling(f), cid))

    file_to_casting: dict[str, list[int]] = {}
    overlap_via: dict[str, str] = {}
    for entry, owner in declared:
        file_to_casting.setdefault(entry, []).append(owner)
    for entry, owner in declared:
        if not entry.endswith(DIRECTORY_ENTRY_SUFFIX):
            continue
        for covered, other in declared:
            if other == owner or not covers_path(entry, covered):
                continue
            cids = file_to_casting.setdefault(covered, [])
            if owner not in cids:
                cids.append(owner)
            overlap_via.setdefault(covered, entry)

    overlaps = {f: cids for f, cids in file_to_casting.items() if len(cids) > 1}
    if overlaps:
        overlap_details = [
            f"{f}: castings {cids}"
            + (f" (casting {cids[-1]} declares the directory '{overlap_via[f]}', "
               f"which covers it)" if f in overlap_via else "")
            for f, cids in overlaps.items()
        ]
        # Ranked ABOVE the size check: an oversized casting is a slow wave,
        # a shared file is two teammates overwriting each other, and the
        # second has to be resolved before the first is worth resizing.
        ladder.fail(
            _GATE_RANK_CONFLICT,
            f"File overlap between castings: {'; '.join(overlap_details)}",
            "Two castings editing the same file will cause conflicts. Move "
            "shared files to an earlier casting or merge the overlapping "
            "castings.",
        )
        checklist.append({"check": "no_file_overlap", "ok": False, "overlaps": overlaps})
    else:
        checklist.append({"check": "no_file_overlap", "ok": True})

    return _preconditions_outcome(ladder, checklist, castings_count=count)




def _cast_preconditions(fdir: Path, project_root: str) -> dict:
    """Preconditions for `cast`, the F1 -> F2 crossing — gate `inspect`.

    fallout AC-010 — D-243 CLOSES HERE. `_check_sight_required` was read by the
    `inspect` gate and by NO transition, so a run with frontend files in scope
    and no `--url` crossed into INSPECT owing a SIGHT stream it could not run.

    `.cast-complete` IS NOT A RUNG, and cannot be. This transition is the only
    writer of that marker, so requiring it would make the transition refuse
    itself and make the F1 guidance ("Foundry-Gate(phase='inspect') ... then
    Foundry-Phase(phase='cast')") name a gate that cannot pass until after the
    call it precedes. It is reported as the FACT it is: the checklist still
    carries `cast_complete`, and nothing refuses on a post-condition of itself.
    """
    # fallout AC-062 / AC-008 / GI-011 (D-088) — THE HALTED RUNG, MADE BY
    # THE ROUTINE RATHER THAN BY THE DOOR ABOVE IT. Short-circuits, so a
    # halted run costs one state.json read and none of the work below.
    if (halted := _halted_outcome(fdir)) is not None:
        return halted
    checklist: list[dict] = []
    ladder = _GateLadder()

    source = _source_phase_rung(ladder, checklist, fdir, "cast")
    _teams_rung(ladder, checklist, project_root)

    # C-081: with the run root, a directory `key_files` entry is WALKED
    # rather than read as carrying no frontend file.
    sight = _sight_required(fdir, project_root)
    if sight.get("required") and sight.get("blocked"):
        ladder.fail(
            _GATE_RANK_CONFIG,
            sight["reason"],
            "Provide --url for SIGHT audit or update manifest.json target_url",
        )
        checklist.append({"check": "sight_url", "ok": False, "reason": sight["reason"]})
    else:
        checklist.append({"check": "sight_url", "ok": True})

    checklist.append({
        "check": "cast_complete",
        "ok": (fdir / CAST_COMPLETE_MARKER).exists(),
        "refuses": False,
    })

    # fallout FR-058 / AC-056 — the F2 entry's own width decision and sweep.
    # This branch, not `start_cast`: `cast` is the token that enters F2, so it
    # opens the run's first INSPECT and therefore decides its width.
    evidence = _boundary_evidence_rung(
        ladder, checklist, fdir, project_root,
        entry=_decide_inspect_mode(
            fdir, project_root, decided_by="cast", phase="F2",
            cycle=current_cycle(fdir),
        ),
        full=True,
        token="cast",
    )
    return _preconditions_outcome(ladder, checklist, **source, **evidence)




#: fallout ST-005 / GI-033 (D-158) — THE LEDGER'S TWO NAMES, SPELLED HERE
#: BECAUSE THE MODULE THAT DECLARES THEM IS ON THE OTHER SIDE OF THE LAYERING.
#:
#: `tools/concerns.py` declares `CONCERNS_FILENAME` and
#: `CONCERNS_COLLECTION_KEY`, and it imports `tools/foundry.py` at module top —
#: so a VERIFIER module importing the declaration pulls the largest lifecycle
#: module in the tree across the boundary GI-033 draws, which is the crossing
#: AC-061 refuses entirely. `foundry_state.open_cross_casting_concerns` spells
#: the same two literals for the same reason, so this is the second spelling and
#: not the first. The honest home is `schemas/vocab.py`, beside
#: `CONCERN_STATUSES`, which every layer may read; that file is casting 10's and
#: the move is raised as a cross-casting concern rather than forked into a leaf
#: of this package's own.
_CONCERNS_FILENAME = "concerns.json"
_CONCERNS_COLLECTION_KEY = "concerns"


def _concern_ledger_problem(fdir: Path) -> str | None:
    """Why `concerns.json` is not a ledger this door can read, else None.

    fallout ST-005 / GI-023 / AC-004 (D-158) — the shape check the tolerant
    reader deliberately does not make.

    `foundry_state.open_cross_casting_concerns` and `concerns.read_concerns`
    both answer the EMPTY LIST for a `concerns` cell that is not a list of
    mappings, which is correct for a reader that degrades and wrong for a rung
    that REFUSES on the list being non-empty: "no open concern" and "no concern
    I could read" are opposite facts, and the second arriving as the first is
    the whole of D-158.

    ABSENT IS NOT A PROBLEM. A run that has filed no concern has no
    `concerns.json`, and every run starts that way. The problem is a document
    that EXISTS and cannot be read as this ledger — the parse failure the
    artifact guard already names, and the valid-JSON-wrong-shape route it does
    not.

    Read through `foundry_state.read_document` rather than through
    `concerns.read_concerns`: this is a VERIFIER module and `tools/concerns.py`
    imports `tools/foundry.py` at module top, so reaching the named reader would
    pull the largest lifecycle module in the tree across the layering
    (GI-033 / AC-061). The leaf read plus the shape test is the same answer
    without the crossing.
    """
    path = fdir / _CONCERNS_FILENAME
    if not path.exists():
        return None
    document, problem = read_document(path)
    if problem is not None:
        return problem
    records = document.get(_CONCERNS_COLLECTION_KEY)
    if records is None:
        return (
            f"{_CONCERNS_FILENAME} carries no `{_CONCERNS_COLLECTION_KEY}` cell, "
            "so no concern can be read from it"
        )
    if not isinstance(records, list):
        return (
            f"{_CONCERNS_FILENAME}'s `{_CONCERNS_COLLECTION_KEY}` cell is a "
            f"{type(records).__name__}, not the list of records this ledger is"
        )
    bad = [i for i, record in enumerate(records) if not isinstance(record, dict)]
    if bad:
        return (
            f"{_CONCERNS_FILENAME} holds {len(bad)} entr(y/ies) that are not "
            f"records (positions {', '.join(str(i) for i in bad[:5])}"
            + (", ..." if len(bad) > 5 else "")
            + "); a concern is a mapping and nothing else can be read as one"
        )
    return None




def _inspect_start_preconditions(fdir: Path, project_root: str) -> dict:
    """Preconditions for `inspect_start`, the GRIND -> INSPECT crossing.

    Accepted from EXACTLY TWO source phases — F3, the crossing ST-005 names, and
    F2, the widening re-open of a DELTA cycle — and both advance the counter.
    The two widening arms (nothing to widen; blocking defects) apply only to the
    F2 arm and are stated here rather than inside the transition, so
    `Foundry-Gate('inspect_start')` answers the same question.

    D-113 / D-114 / D-116 are why the source check exists at all; see
    `_PHASE_ENTRY_SOURCES` for the drives. It is written here as its own rung
    rather than through `_source_phase_rung` because `inspect_start` accepts two
    phases for two different reasons and its refusal names both.
    """
    # fallout AC-062 / AC-008 / GI-011 (D-088) — THE HALTED RUNG, MADE BY
    # THE ROUTINE RATHER THAN BY THE DOOR ABOVE IT. Short-circuits, so a
    # halted run costs one state.json read and none of the work below.
    if (halted := _halted_outcome(fdir)) is not None:
        return halted
    checklist: list[dict] = []
    ladder = _GateLadder()

    phase = _load_json(fdir / "state.json").get("phase", "")
    accepted = phase in ("F3", "F2")
    if not accepted:
        ladder.fail(
            _GATE_RANK_SOURCE,
            (
                f"Cannot start an INSPECT from phase {phase or 'F0'} — "
                "Foundry-Phase(phase='inspect_start') is the GRIND->INSPECT "
                "crossing (ST-005), accepted from F3, and from F2 as the "
                "widening re-open of a DELTA cycle. It is the call that "
                "ADVANCES the cycle counter, so from any other phase it "
                "would record a second INSPECT decision against a cycle that "
                "has not ended and evaluate the escalation exit arms on a "
                "cycle still in flight."
            ),
            _INSPECT_START_SOURCE_HINTS.get(
                phase,
                "Reach F3 first: Foundry-Phase(phase='grind_start') opens a "
                "GRIND, and its completion is what this transition closes.",
            ),
        )
    checklist.append({
        "check": f"entered_from_accepted_phase (token=inspect_start, phase={phase or 'F0'})",
        "ok": accepted,
        "accepted_from": ["F3", "F2"],
    })

    widening = phase == "F2"
    if widening:
        recorded_now = current_inspect_mode(fdir, modes=INSPECT_MODES) or {}
        if recorded_now.get("mode") != "DELTA":
            ladder.fail(
                _GATE_RANK_WIDTH,
                (
                    "Cannot re-open INSPECT — this cycle's recorded width is "
                    f"{recorded_now.get('mode') or 'unrecorded'}"
                    + (f" (rule {recorded_now['rule']})"
                       if recorded_now.get("rule") else "")
                    + ", so there is nothing to widen."
                ),
                (
                    "The F2->F2 re-open exists to widen a DELTA INSPECT to "
                    "FULL before ASSAY. From a FULL cycle, call "
                    "Foundry-Phase(phase='inspect_clean') to open ASSAY, or "
                    "Foundry-Phase(phase='grind_start') to open a GRIND. The "
                    "cycle counter has NOT moved."
                ),
            )
        checklist.append({
            "check": f"widening_a_delta_cycle (mode={recorded_now.get('mode') or 'unrecorded'})",
            "ok": recorded_now.get("mode") == "DELTA",
        })
        widen_blocking = _blocking_defects(fdir)
        if widen_blocking["blocking"] > 0:
            ladder.fail(
                _GATE_RANK_DEFECTS,
                (
                    "Cannot re-open INSPECT at full width — "
                    f"{widen_blocking['reason']}"
                ),
                (
                    "Fix them in GRIND first: Foundry-Tasks, then "
                    "Foundry-Gate(phase='grind'), then "
                    "Foundry-Phase(phase='grind_start'). Widening an INSPECT "
                    "over code the run is about to change re-verifies a tree "
                    "that will not exist. The cycle counter has NOT moved."
                ),
            )
        checklist.append({
            "check": (
                f"zero_blocking_defects (live={len(widen_blocking['live'])} "
                f"unknown_tier={len(widen_blocking['unknown'])} "
                f"latent_backlog={len(widen_blocking['latent'])})"
            ),
            "ok": widen_blocking["blocking"] == 0,
            "live": widen_blocking["live"],
            "unknown_tier": widen_blocking["unknown"],
        })

    # fallout FR-012 / GI-023 / ST-005 / AC-004 / OT-004 — A CROSS-CASTING
    # CONCERN FROM THE CLOSING GRIND HOLDS THE INSPECT DOOR.
    #
    # A teammate that found its fix reaches another casting's files filed a
    # concern saying so. Opening the next INSPECT over a tree where one casting
    # carries the new rule and its sibling carries the old one verifies a state
    # the run already knows is half-done — and the finding it produces is the
    # one the concern already made, a cycle later. Two exits, both cheap: let
    # `Foundry-Tasks` dispatch it (the co-dispatch set carries it to its target),
    # or close it with a reason. Neither is "fix everything"; both are decisions
    # that leave a record.
    # fallout ST-005 / GI-023 / AC-004 (D-158) — A LEDGER THIS DOOR CANNOT READ
    # IS A REFUSAL, NOT AN EMPTY LIST.
    # ------------------------------------------------------------------------
    # `foundry_state.open_cross_casting_concerns` is a TOLERANT reader by
    # design: it calls `read_document` and discards the problem, so every
    # `concerns` value that is not a list of mappings answers as the empty list.
    # That is the right contract for the reader and the wrong one for THIS rung,
    # whose whole subject is "is a concern from the closing GRIND still open" —
    # an answer of "none" from a document nobody could read is the fail-open
    # ST-005's guard exists to prevent.
    #
    # DRIVEN on a run at F3 cycle 4 with a manifest of castings 3 and 5: C-001
    # filed from casting 3 targeting src/five.py made this rung refuse by id,
    # and rewriting concerns.json to {"concerns": "C-001 open"} or to
    # {"concerns": ["C-001"]} made it PASS — both are valid JSON, so
    # `_artifact_guard` reported no problem and the route stayed open. Only
    # UNPARSEABLE JSON was caught, which is the one broken shape the guard above
    # already handles; the valid-JSON-wrong-shape route reached here untouched.
    #
    # SAME RULING `streams.py` TOOK FOR THE ROSTER (C-058): the leaf answers
    # tolerantly and the DOOR refuses on the problem, naming the file, so the
    # operator repairs the artifact rather than discovering a cycle later that
    # the rung was measuring nothing.
    ledger_problem = _concern_ledger_problem(fdir)
    if ledger_problem is not None:
        ladder.fail(
            _GATE_RANK_MARKER,
            (
                "Cannot start an INSPECT — the cross-casting concern ledger "
                f"cannot be read as one: {ledger_problem}"
            ),
            (
                "This rung refuses while a concern from the closing GRIND is "
                "open, and a ledger nothing can read answers 'none' for a "
                "reason that is not 'none'. Repair "
                "concerns.json — its `concerns` cell is a LIST OF RECORDS, one "
                "mapping per concern — or delete it if this run filed none. "
                "The cycle counter has NOT moved."
            ),
        )
    checklist.append({
        "check": f"concern_ledger_readable ({ledger_problem or 'ok'})",
        "ok": ledger_problem is None,
    })

    # fallout ST-005 / GI-023 (D-158, concern C-082) — THE SECOND ROUTE TO THE
    # SAME FAIL-OPEN IS STILL OPEN, AND CLOSING IT IS THE LEAF'S TO DO.
    #
    # This rung scopes on EXACT cycle equality and `foundry_state.current_cycle`
    # answers 0 for a missing, absent OR MALFORMED counter — deliberately, so
    # every reader gets a usable integer — so a `state.json` carrying
    # `"cycle": "four"` makes this look for cycle-0 concerns only, and a concern
    # filed at cycle 5 is invisible. The honest reading of an unknown scope is
    # EVERY open cross-casting concern (`cycle=None`), the same fail-closed
    # direction `_decide_inspect_mode` takes on an uncomputable diff and
    # `_sweep_evidence_at_boundary` takes on an entry with no mode.
    #
    # WHAT THIS MODULE MAY NOT DO IS TELL THE TWO APART. The only distinguisher
    # is the RAW `state.json["cycle"]` value, and
    # `test_module_boundaries.py#test_every_state_cycle_read_goes_through_a_
    # guarded_reader` forbids exactly that read outside the leaf's total readers
    # — correctly: a raw read hands on whatever the file holds. It caught the
    # first attempt at this arm here, by name. The distinction belongs beside
    # `current_cycle` as a total reader in the shape `rosters.roster_length`
    # already uses — `(value, problem)` — which is `tools/foundry_state.py`, and
    # so is raised as a cross-casting concern rather than forked into a second
    # counter read here. Until it lands the scope stays the counter's answer.
    open_concerns = [
        # fallout GI-033 / AC-061 / FR-063 (D-021 / D-035, ruling item 3,
        # concerns C-030 and C-032) — THE READ FROM THE LEAF, THE MEMBER FROM
        # THE VOCABULARY. `tools/concerns.py` imports `tools/foundry.py` at
        # module top, so a verifier reaching the ledger through it pulled the
        # largest lifecycle module in the tree into this layer transitively.
        # `foundry_state.open_cross_casting_concerns` holds the one body of the
        # filter and takes its status member as an argument, and
        # `vocab.CONCERN_STATUSES` is where the closed set lives — so nothing
        # here respells "open" and nothing reaches a lifecycle module for it.
        c for c in open_cross_casting_concerns(
            fdir, status_open=CONCERN_STATUS_OPEN, cycle=current_cycle(fdir)
        )
    ]
    if open_concerns:
        named = ", ".join(str(c.get("id")) for c in open_concerns)
        ladder.fail(
            _GATE_RANK_MARKER,
            (
                f"{len(open_concerns)} cross-casting concern(s) from this GRIND "
                f"are still open: {named}"
            ),
            (
                "Two exits, and both leave a record: call Foundry-Tasks, whose "
                "co-dispatch set carries the concern to the casting it names and "
                "marks it dispatched — or close it deliberately with "
                "Foundry-Concern(close=<id>, reason=...). An INSPECT opened over "
                "a tree one casting has updated and its sibling has not "
                "re-discovers the concern as a defect a cycle later."
            ),
        )
    checklist.append({
        "check": f"no_open_cross_casting_concerns ({len(open_concerns)})",
        "ok": not open_concerns,
        "concerns": [str(c.get("id")) for c in open_concerns],
    })

    # fallout FR-058 / AC-056 — the crossing's width decision and its sweep,
    # in the routine both doors call. The cycle is `completed + 1` because this
    # is the call that advances the counter, and `widening` is the answer the
    # source rung above already computed rather than a second read of
    # `state.phase`.
    entry = _decide_inspect_mode(
        fdir, project_root, decided_by="inspect_start", phase="F2",
        cycle=current_cycle(fdir) + 1, widening=widening,
    )
    evidence = _boundary_evidence_rung(
        ladder, checklist, fdir, project_root,
        entry=entry, full=entry["mode"] == "FULL", token="inspect_start",
    )
    return _preconditions_outcome(
        ladder, checklist, widening=widening, **evidence
    )




def _inspect_clean_preconditions(fdir: Path, project_root: str) -> dict:
    """Preconditions for `inspect_clean`, the F2 -> F4 crossing — gate `assay`.

    `.inspect-clean` IS NOT A RUNG, for the reason `.cast-complete` is not one
    at the `cast` token: this transition is that marker's only writer. The
    substance the gate's `.inspect-clean` arm was reaching for -- "GRIND fixed
    defects and INSPECT has not re-verified" -- is carried as a REFUSAL by the
    `fixes_after_decision` rung below, which measures the same thing against the
    recorded width decision instead of against a marker.
    """
    # fallout AC-062 / AC-008 / GI-011 (D-088) — THE HALTED RUNG, MADE BY
    # THE ROUTINE RATHER THAN BY THE DOOR ABOVE IT. Short-circuits, so a
    # halted run costs one state.json read and none of the work below.
    if (halted := _halted_outcome(fdir)) is not None:
        return halted
    checklist: list[dict] = []
    ladder = _GateLadder()

    unrecorded = _unrecorded_width_problem(fdir, modes=INSPECT_MODES)
    if unrecorded is not None:
        ladder.fail(_GATE_RANK_WIDTH, unrecorded["reason"], unrecorded["hint"])
    checklist.append({
        "check": "inspect_width_recorded",
        "ok": unrecorded is None,
    })
    width_facts = {} if unrecorded is None else {"unrecorded_width": True}

    streams = _streams_complete(project_root)
    if not streams["complete"]:
        required_now = streams.get("required") or []
        ladder.fail(
            _GATE_RANK_STREAMS,
            f"Verification streams incomplete: {streams.get('missing', '')}",
            "This INSPECT's recorded roster is "
            + (", ".join(required_now) if required_now else "not recorded")
            + f" — every one of them must complete before ASSAY. Missing: "
            f"{streams.get('missing', '') or 'none'}. Re-dispatch each missing "
            "stream; the AGENT records its own run with Foundry-Stream "
            "(fallout GI-016), so a missing record is a stream to re-run and "
            "never a number for you to supply.",
        )
    checklist.append({
        "check": "all_streams_complete",
        "ok": streams["complete"],
        "missing": streams.get("missing", ""),
    })

    _blocking_defects_rung(ladder, checklist, fdir)

    recorded_mode = current_inspect_mode(fdir, modes=INSPECT_MODES) or {}
    superseded = recorded_mode.get("fixes_after_decision") or []
    if superseded:
        # RANKED AT WIDTH, and DECLARED BEFORE the DELTA arm below — which is
        # how the ordering this branch has always documented survives the move
        # onto ranks. "Open defects and fixes-landed-mid-INSPECT are both more
        # specific than 'this cycle was narrow', and a lead told about the width
        # when a defect is open would widen an INSPECT it is about to
        # invalidate." Both remedies are a boundary crossing and neither is
        # defeated by the other, so the tie breaks on declaration order and the
        # more specific sentence speaks.
        ladder.fail(
            _GATE_RANK_WIDTH,
            (
                f"{len(superseded)} defect(s) were fixed after this INSPECT's "
                f"width was decided: {', '.join(superseded)}"
            ),
            (
                f"This cycle was swept at {recorded_mode.get('mode', '?')} "
                "width before those fixes landed, so its evidence does not "
                "cover the surface they changed. Cross the boundary again — "
                "Foundry-Phase(phase='grind_start') then "
                "Foundry-Phase(phase='inspect_start') — which re-decides the "
                "width and re-sweeps at the new HEAD, and re-run the streams."
            ),
        )
    checklist.append({
        "check": f"no_fixes_after_width_decision ({len(superseded)})",
        "ok": not superseded,
        "fixes_after_decision": list(superseded),
    })

    # AC-016 / D-068 / D-169 — THE WIDTH IS ASSERTED POSITIVELY: FULL, or refuse.
    # Ranked at WIDTH beside the unrecorded arm above, never below the marker
    # rungs: D-183 and D-186 are both this refusal being displaced by a check a
    # lead cannot act on until the width is fixed.
    width_ok = recorded_mode.get("mode") == "FULL"
    if unrecorded is None and not width_ok:
        ladder.fail(
            _GATE_RANK_WIDTH,
            f"cycle {recorded_mode.get('cycle', '?')} ran at "
            f"{recorded_mode.get('mode') or 'unrecorded'} width (rule "
            f"{recorded_mode.get('rule') or 'unrecorded'}) — ASSAY is only "
            "opened by an INSPECT whose recorded mode is FULL",
            "Call Foundry-Phase(phase='inspect_start') again from F2: the "
            "widening re-open advances the cycle, sweeps the whole evidence "
            "corpus and records FULL. Which rule it records — final_gate "
            "from the widening re-open, verifier_touched when the diff "
            "cannot be measured — does not enter this check; the width "
            "does.",
        )
    checklist.append({
        "check": (
            f"inspect_ran_at_full_width (mode={recorded_mode.get('mode') or 'unrecorded'} "
            f"rule={recorded_mode.get('rule') or 'unrecorded'})"
        ),
        "ok": width_ok,
    })

    _teams_rung(ladder, checklist, project_root)
    checklist.append({
        "check": "inspect_clean",
        "ok": (fdir / INSPECT_CLEAN_MARKER).exists(),
        "refuses": False,
    })
    return _preconditions_outcome(
        ladder,
        checklist,
        fixes_after_decision=list(superseded),
        inspect_mode=recorded_mode.get("mode", ""),
        inspect_rule=recorded_mode.get("rule", ""),
        **width_facts,
    )




def _grind_start_preconditions(fdir: Path, project_root: str) -> dict:
    """Preconditions for `grind_start` — gate `grind`.

    fallout FR-062 / GI-032 / ST-015 / AC-060 / OT-044 — THE CAP IS A FACT HERE,
    NOT A REFUSAL. `would_halt` is computed from the SINGLE
    `persisted_max_cycles` read this run has, published on the outcome, and
    acted on by the two transitions that open a GRIND. `Foundry-Gate('grind')`
    PASSES at the cap and shows `would_halt: true`, because reaching the cap is
    not something a lead can fix at the door: the run stops, with its open work
    written down, and that is a successful transition (fallout ST-001 / CT-004).
    """
    # fallout AC-062 / AC-008 / GI-011 (D-088) — THE HALTED RUNG, MADE BY
    # THE ROUTINE RATHER THAN BY THE DOOR ABOVE IT. Short-circuits, so a
    # halted run costs one state.json read and none of the work below.
    if (halted := _halted_outcome(fdir)) is not None:
        return halted
    checklist: list[dict] = []
    ladder = _GateLadder()

    defects = _load_json(fdir / "defects.json")
    open_count = sum(1 for d in defects.get("defects", []) if d.get("status") == "open")
    if open_count < 1:
        # Ranked ABOVE the tasks marker: on a ledger with nothing open,
        # `Foundry-Tasks` has nothing to packet, so telling the lead to call
        # it — which is what the last-writer-wins ladder did — sends them to
        # do work that cannot change this gate's answer.
        ladder.fail(
            _GATE_RANK_DEFECTS,
            "No open defects to grind",
            "Nothing to fix — skip to ASSAY",
        )
    checklist.append({"check": f"open_defects={open_count}", "ok": open_count >= 1})

    _teams_rung(ladder, checklist, project_root)

    tasks_generated = (fdir / TASKS_GENERATED_MARKER).exists()
    if not tasks_generated:
        ladder.fail(
            _GATE_RANK_MARKER,
            "defects-to-tasks has not been run",
            "Call Foundry-Tasks before entering GRIND",
        )
    checklist.append({"check": "tasks_generated", "ok": tasks_generated})

    state = _load_json(fdir / "state.json")
    max_cycles = persisted_max_cycles(state)
    cycle = current_cycle(fdir)
    opening = cycle + 1
    would_halt = max_cycles > 0 and opening > max_cycles
    checklist.append({
        "check": (
            f"within_cycle_cap (max_cycles={max_cycles or 'unbounded'} "
            f"opening={opening})"
        ),
        "ok": True,
        "would_halt": would_halt,
        "refuses": False,
    })
    return _preconditions_outcome(
        ladder,
        checklist,
        would_halt=would_halt,
        max_cycles=max_cycles,
        cycle=cycle,
        opening=opening,
    )




def _assay_fail_preconditions(fdir: Path, project_root: str) -> dict:
    """Preconditions for `assay_fail` — the OTHER door into F3, gate `grind`.

    `assay_fail` clears the same markers and calls the same
    `_update_phase(fdir, "F3")` as `grind_start`; it is a second door into the
    same phase, not a different kind of transition, and a precondition wired to
    one of them would let a run looping back through ASSAY failure walk past
    what a run looping through GRIND is held to. So it IS `grind_start`'s
    evaluation — called, not copied.
    """
    return _grind_start_preconditions(fdir, project_root)




def _temper_preconditions(fdir: Path, project_root: str) -> dict:
    """Preconditions for `temper`, the F4 -> F5 crossing.

    fallout AC-010 — D-240 CLOSES HERE. The verdict read was the `temper`
    gate's alone; this transition entered F5 on a ledger full of THIN rows.
    """
    # fallout AC-062 / AC-008 / GI-011 (D-088) — THE HALTED RUNG, MADE BY
    # THE ROUTINE RATHER THAN BY THE DOOR ABOVE IT. Short-circuits, so a
    # halted run costs one state.json read and none of the work below.
    if (halted := _halted_outcome(fdir)) is not None:
        return halted
    checklist: list[dict] = []
    ladder = _GateLadder()
    source = _source_phase_rung(ladder, checklist, fdir, "temper")
    _all_verified_rung(ladder, checklist, fdir, "TEMPER")
    _blocking_defects_rung(ladder, checklist, fdir)
    # fallout FR-058 / AC-056 — the F5 entry opens TEMPER's first INSPECT and
    # records FULL / first_of_phase on the same terms as the F2 entry, so it
    # sweeps on the same terms too — in the routine, where the gate sees it.
    evidence = _boundary_evidence_rung(
        ladder, checklist, fdir, project_root,
        entry=_decide_inspect_mode(
            fdir, project_root, decided_by="temper", phase="F5",
            cycle=current_cycle(fdir),
        ),
        full=True,
        token="temper",
    )

    # fallout FR-016 / FR-060 / GI-015 / GI-030 / AC-021 (D-143 / D-144) — THE
    # OPT-IN RUNG, AT THE DOOR RATHER THAN IN THE ROUTER.
    #
    # FR-016 is "Same split, but TEMPER STAYS OPT-IN" and FR-060 restates it.
    # The routing half was implemented — `_compute_next_action` sends F4 to F6
    # without the flag — and the DOOR was not, so the opt-in was enforced only
    # for a lead that followed the guidance. `_nyquist_preconditions`, the
    # sibling optional phase, has carried this rung all along.
    #
    # DRIVEN on a run with `state.json.temper` false: `Foundry-Gate('temper')`
    # returned passed=True with no `temper_enabled` row (control:
    # `Foundry-Gate('nyquist')` on the same run returned passed=False with
    # `nyquist_enabled` ok=False), and `Foundry-Phase('temper')` then moved the
    # run to F5.
    #
    # AND THE RUN IS THEN TRAPPED, which is why this is a refusal rather than a
    # tidiness. `PHASE_TOKENS['done']`'s accepted_from is `('F5.5',)` if nyquist
    # else `('F5',)` if temper else `('F4',)` — with temper false that is
    # `('F4',)`, so `Foundry-Gate('done')` is refused FROM F5 and the run can
    # never reach F6, while `Foundry-Next` at F5 keeps returning `run_temper`.
    # A crossing that strands a run is worse than one that refuses it.
    #
    # RANKED AT `_GATE_RANK_CONFIG`, shaped on the nyquist rung it mirrors:
    # below the defect and verdict ladders, because the remedy offered here —
    # leave through `done` — is itself refused while a blocking defect is open,
    # and rendering a remedy the next check rejects is what the ranking exists
    # to prevent.
    temper_on = bool(_load_json(fdir / "state.json").get("temper", False))
    if not temper_on:
        ladder.fail(
            _GATE_RANK_CONFIG,
            "F5 TEMPER is opt-in and this run was not started with --temper",
            "Re-run with --temper, or skip F5: call Foundry-Gate(phase='done').",
        )
    checklist.append({"check": "temper_enabled", "ok": temper_on})
    return _preconditions_outcome(ladder, checklist, **source, **evidence)




def _nyquist_preconditions(fdir: Path, project_root: str) -> dict:
    """Preconditions for `nyquist`, the entry to F5.5.

    fallout AC-010 — D-241 AND D-242 CLOSE HERE, and the source phase with
    them. The gate read the verdicts and `state.nyquist`; the transition read
    neither, so a run that never asked for F5.5 could enter it and generate
    regression tests locking in behaviour a stream had already ruled wrong.
    """
    # fallout AC-062 / AC-008 / GI-011 (D-088) — THE HALTED RUNG, MADE BY
    # THE ROUTINE RATHER THAN BY THE DOOR ABOVE IT. Short-circuits, so a
    # halted run costs one state.json read and none of the work below.
    if (halted := _halted_outcome(fdir)) is not None:
        return halted
    checklist: list[dict] = []
    ladder = _GateLadder()
    source = _source_phase_rung(ladder, checklist, fdir, "nyquist")
    _all_verified_rung(ladder, checklist, fdir, "NYQUIST")
    _blocking_defects_rung(ladder, checklist, fdir)

    # fallout AC-056 / GI-002 / D-133 / D-159 — THE TERMINAL SWEEP IS A RUNG
    # HERE, NOT AN ARM IN THE TRANSITION.
    #
    # GI-002 names three terminal boundaries — "before ASSAY/NYQUIST/DONE" — and
    # `_done_preconditions` already takes this rung for the other two, so
    # `Foundry-Gate('done')` has always swept. Leaving NYQUIST's copy inside the
    # transition would leave one crossing of the three whose gate answers a
    # different question than its transition, which is the shape this release
    # exists to end. It costs the same at both doors because it is the same
    # call: `_terminal_evidence_state` caches its pass, and the transition below
    # consumes the record this rung produced rather than sweeping twice.
    evidence_state = _terminal_evidence_state(fdir, project_root)
    if (refusal := _terminal_evidence_refusal(evidence_state, "enter NYQUIST")) is not None:
        ladder.fail(
            _GATE_RANK_EVIDENCE,
            refusal["error"].replace("Cannot enter NYQUIST — ", ""),
            refusal["hint"],
        )
    checklist.append({
        "check": (
            f"evidence_reproduces_at_head (logs={evidence_state['logs_reexecuted']}, "
            f"mismatches={len(evidence_state['evidence'].get('mismatches') or [])})"
        ),
        "ok": bool(evidence_state["evidence"]["ok"]) and not evidence_state["stripped"],
        "corpus_size": evidence_state["corpus_size"],
    })

    nyquist_on = bool(_load_json(fdir / "state.json").get("nyquist", False))
    if not nyquist_on:
        # Ranked BELOW the defect read, and that is a change from the
        # source-order ladder this replaced. The remedy here offers
        # "call Foundry-Gate(phase='done')" as the way past F5.5, and that
        # call is refused while a LIVE or unknown-tier defect is open
        # (CT-008) — so on a run failing both checks the old last-writer
        # rendered the remedy the other failing check would reject.
        ladder.fail(
            _GATE_RANK_CONFIG,
            "F5.5 NYQUIST is opt-in and this run was not started with --nyquist",
            "Re-run with --nyquist, or skip F5.5: call Foundry-Gate(phase='done').",
        )
    checklist.append({"check": "nyquist_enabled", "ok": nyquist_on})
    return _preconditions_outcome(
        ladder, checklist, evidence_record=evidence_state["record"], **source
    )




def _nyquist_done_preconditions(fdir: Path, project_root: str) -> dict:
    """Preconditions for `nyquist_done` — F6's second door.

    There is ONE definition of "the run may finish" and both doors ask it; a
    second implementation is the drift that produced D-043 and D-044, not a fix
    for it. What differs between the two doors is the phase each is reached
    from, and `_done_preconditions` takes that as its `token`.
    """
    return _done_preconditions(fdir, project_root, token="nyquist_done")




def _halt_preconditions(
    fdir: Path, project_root: str, *, reason: str = "", text: str = ""
) -> dict:
    """fallout FR-064 / GI-034 / CT-004 / CT-021 / AC-062 — three checks, as rungs.

    `halt` is a FULL member of `PHASE_TOKENS`: it has a preconditions routine
    like every other token and a gate token that reports it as data.
    `Foundry-Gate('halt', reason, text)` and `Foundry-Phase('halt', ...)` refuse
    the identical set, which is the whole point of the token existing rather
    than the halt being a special case with inline refusals.

    ``text`` is accepted and REPORTED, never refused (fallout D-121). CT-004
    makes the member the thing a grouper reads and the text the thing a human
    reads, and a run may end for a reason no closed set carries.

    fallout FR-046 / CT-021 / AC-062 / OT-045 (D-121) — WHY THE EMPTINESS IS A
    FACT AND NOT A FOURTH REFUSAL.
    ------------------------------------------------------------------------
    This door's refusal hint reads "plus `text` saying why THIS run ended, which
    no closed set can carry", and the door then accepted no text at all: driven
    with `reason='lead_ruling'` and no `text` argument, the transition returned
    ok=True, sealed the report and wrote `halted_reason {"reason":
    "lead_ruling", "text": ""}`. HALTED is irreversible — every subsequent
    Foundry-Phase token and all eleven Foundry-Gate tokens are refused with
    "Nothing leaves HALTED" — so that run's only free-text explanation is
    permanently the empty string. Prose said required; behaviour said optional.

    THE PROSE IS WHAT MOVED, because the behaviour is specified. FR-046 says
    this token "refuses only on `_halt_preconditions` (reason member, no team
    registered, not already HALTED)", and CT-021, AC-062 and OT-045 each name
    THREE checks. A fourth refusal would close the contradiction by falsifying
    four spec rows, so the emptiness is published the way GI-032 publishes
    `would_halt`: a NON-REFUSING checklist fact the caller sees before it seals,
    while the hint stops claiming an enforcement this door does not make and
    says instead what an empty text COSTS — which is the thing a lead about to
    end a run irreversibly actually needs to know.
    """
    # fallout AC-062 (D-088) — THE `not_already_halted` RUNG, FIRST AND
    # REACHABLE. It was built below, and both doors short-circuited above this
    # function, so it never spoke through either of them: the routine answered
    # rank-0 `refusals` and the doors answered a bare sentence with no
    # `refusals` key. Asked here, in the shared spelling every other routine
    # uses, so `Foundry-Phase('halt')` refuses on THIS FUNCTION ALONE.
    if (halted := _halted_outcome(fdir)) is not None:
        return halted

    checklist: list[dict] = []
    ladder = _GateLadder()

    member = halt_reason(reason)
    if member is None:
        ladder.fail(
            _GATE_RANK_CONFIG,
            (
                f"halt reason {reason!r} is not a member of the halt vocabulary"
                if reason
                else "Foundry-Phase(phase='halt') requires a reason"
            ),
            # Derived from the constant, never re-typed: the `_PYTEST_DISCOVERY_
            # PHRASE` shape, so the door that refuses an unknown reason cannot
            # advertise a set the vocabulary no longer holds.
            #
            # fallout D-121: the hint says what `text` IS and what omitting it
            # costs, and no longer implies this door enforces it. It does not
            # (FR-046 names three refusals and this is not one of them), and a
            # hint that names a requirement the door declines to make sends the
            # caller looking for a refusal that will never arrive.
            f"Pass one of: {halt_reason_phrase()} — plus `text` saying why THIS "
            "run ended, which no closed set can carry. `text` is recorded, not "
            "required: omit it and the archive's stated cause is the bare "
            "vocabulary member, permanently, because nothing leaves HALTED.",
        )
    checklist.append({
        "check": f"halt_reason_is_a_member (reason={reason or 'absent'})",
        "ok": member is not None,
        "accepted": sorted(HALT_REASONS),
    })

    # fallout D-121 — REPORTED, NOT ACTED ON, exactly as `would_halt` is at the
    # GRIND door (GI-032). `ok: False` here refuses nothing and the crossing
    # still succeeds; what it does is put the emptiness in front of a lead
    # calling `Foundry-Gate('halt', ...)` BEFORE the irreversible transition,
    # which is the only moment at which it can still be supplied.
    checklist.append({
        "check": f"halt_text_present (chars={len(text.strip()) if isinstance(text, str) else 0})",
        "ok": bool(isinstance(text, str) and text.strip()),
        "refuses": False,
        "note": (
            "not a refusal — FR-046 gives this door three checks and this is "
            "not one of them. An empty text seals a terminal record whose only "
            "stated cause is the vocabulary member."
        ),
    })

    _teams_rung(ladder, checklist, project_root)

    # The rung is made ABOVE, by `_halted_outcome`, which is the one spelling
    # every routine uses. Reaching this line means the run is not halted, so the
    # checklist records the check that passed.
    checklist.append({"check": "not_already_halted", "ok": True})
    return _preconditions_outcome(ladder, checklist, halt_reason=member, halt_text=text)




def _token_preconditions(
    token: str, fdir: Path, project_root: str, *, reason: str = "", text: str = ""
) -> dict | None:
    """The ONE mapping from a `PHASE_TOKENS` member to its routine.

    An explicit chain rather than a dict of callables: a `GATE_CHECKS[token]`
    predicate registry is the natural end state once the split has made the
    checks uniform, and it is explicitly OUT OF SCOPE for this effort. What is
    in scope is that there is exactly one routine per token and exactly one
    place that says which.

    Returns None for a token this function does not know, which is what lets
    both doors keep their own unknown-token refusals in their own words.
    """
    if token == "start_cast":
        return _start_cast_preconditions(fdir, project_root)
    if token == "cast":
        return _cast_preconditions(fdir, project_root)
    if token == "inspect_start":
        return _inspect_start_preconditions(fdir, project_root)
    if token == "inspect_clean":
        return _inspect_clean_preconditions(fdir, project_root)
    if token == "grind_start":
        return _grind_start_preconditions(fdir, project_root)
    if token == "assay_fail":
        return _assay_fail_preconditions(fdir, project_root)
    if token == "temper":
        return _temper_preconditions(fdir, project_root)
    if token == "nyquist":
        return _nyquist_preconditions(fdir, project_root)
    if token == "nyquist_done":
        return _nyquist_done_preconditions(fdir, project_root)
    if token == "done":
        return _done_preconditions(fdir, project_root)
    if token == "halt":
        return _halt_preconditions(fdir, project_root, reason=reason, text=text)
    return None




def _transition_refusal(outcome: dict, clause: str) -> dict:
    """The refusal a transition returns when its shared routine refuses.

    ONE shape, so the two doors say the same thing about the same check and
    differ only in the leading clause a lead reads. The gate answers "may I?"
    and renders `reason` / `hint` bare; the transition answers "do it" and
    prefixes what it could not do — which is the shape the `inspect_clean`
    branch already held against `_blocking_defects`' reason, generalised.

    `checklist` and `refusals` are passed through unaltered. D-191's harm was a
    door that computed four refusals and published one; both doors publish all
    of them now, because they are the same list.
    """
    facts = {
        key: value for key, value in outcome.items()
        if key not in ("passed", "reason", "hint", "checklist", "refusals")
    }
    return {
        "error": f"{clause} — {outcome['reason']}",
        "hint": outcome["hint"],
        "checklist": outcome["checklist"],
        "refusals": outcome["refusals"],
        # The routine's NON-REFUSING facts travel with the refusal, exactly as
        # they do at the gate. Each branch used to return the offending
        # predicate's own dict verbatim — `accepted_from` and `phase` from the
        # source check, `unrecorded_width` from the width check — and a caller
        # that read one of those keys would otherwise have lost it to the
        # reshaping rather than to any decision.
        **facts,
    }




# --- Phase lifecycle markers ---
#
# fallout GI-033 / AC-061 (D-021 / D-035, concern C-027) — the phase-entry
# stamper used to be declared here and `guidance.py` read it, which made a
# lifecycle module import this verifier one. It is twelve pure lines over a
# `phase_times` entry and belongs to neither layer, so it is
# `foundry_state.finalize_open_phase_entry` now and both callers read the leaf.


def _update_phase(fdir: Path, new_phase: str) -> None:
    """Update state.json with the new phase. Tracks timing per phase.

    Closes EVERY still-open phase_times entry before opening the new one.
    Passive sub-phase stamping (see _stamp_subphase_transitions) opens
    F0.5/F0.9 based on file-state signals, so a single `prev_phase` close
    isn't sufficient — the F0 → F1 jump skips F0.5/F0.9 at the state-level
    even though those sub-phases did elapse in wall time.
    """
    state_path = fdir / "state.json"
    now = now_iso()

    with _document_transaction(state_path) as state:
        phase_times = state.get("phase_times", {})
        if not isinstance(phase_times, dict):
            phase_times = {}
        for entry in phase_times.values():
            finalize_open_phase_entry(entry, now)

        phase_times[new_phase] = {"started_at": now}

        state["phase"] = new_phase
        state["updated_at"] = now
        state["phase_times"] = phase_times
        history = state.get("phase_history", [])
        if not isinstance(history, list):
            history = []
        history.append({"phase": new_phase, "entered_at": now})
        state["phase_history"] = history

        if new_phase == "F6":
            state["ended_at"] = now
            started = state.get("started_at", "")
            if started:
                try:
                    start = datetime.fromisoformat(started)
                    end = datetime.fromisoformat(now)
                    delta = end - start
                    hours = int(delta.total_seconds() // 3600)
                    mins = int((delta.total_seconds() % 3600) // 60)
                    secs = int(delta.total_seconds() % 60)
                    state["total_duration"] = f"{hours}h {mins}m {secs}s"
                except (ValueError, TypeError):
                    pass

    # P4 (ST-002): a real phase advance supersedes any pending gate-passed
    # guidance marker. Clear it so the next Foundry-Next emits the NEW phase's
    # fresh imperative rather than a stale "gate already passed" note.
    (fdir / GATE_PASSED_MARKER).unlink(missing_ok=True)




# CLOSED VOCABULARY — every phase token ``foundry_mark_phase_complete`` handles,
# in lifecycle order. The handler is the authority for this one (unlike the wire
# vocabularies, which come from schemas/vocab.py): a token means something only
# because a branch below implements it.
#
# server.py's Foundry-Phase enum is the second copy, and it had drifted in BOTH
# directions at once — advertising research_done / decompose_done /
# validate_done, which this handler has no branch for and refuses, while
# OMITTING inspect_start, the one token whose branch advances the cycle counter.
# The MCP SDK validates arguments against the advertised enum BEFORE dispatch,
# so that omission made the counter unable to leave 0 over MCP however this
# handler behaved.
#
# Adding a token here without a branch below (or vice versa, or without the
# schema entry) fails the drift guard in tests/test_orchestrator_gates.py, which
# derives the accepted set from this function's own AST and asserts all three
# copies are equal.
PHASE_TOKENS = (
    "start_cast",
    "cast",
    "inspect_start",
    "inspect_clean",
    "grind_start",
    "assay_fail",
    "temper",
    "nyquist",
    "nyquist_done",
    "done",
    # fallout FR-064 / GI-034 / AC-062 — `halt` is a FULL member, not a special
    # case. It has `_halt_preconditions`, a `halt` gate token, a row in
    # `GATE_TO_TRANSITION` and a branch below, and the invariant test walks it
    # with every other member. server.py's enum carries it too — the three
    # copies are pinned equal by the AST drift guard, and the token, the branch
    # and the enum entry landed in one commit for exactly that reason.
    "halt",
)




def foundry_mark_phase_complete(
    phase: str,
    project_root: str = ".",
    *,
    reason: str = "",
    text: str = "",
) -> dict:
    """Mark a phase transition. Validates preconditions AND updates state.json.phase.

    D-067 \u2014 THE ORDERING TOKEN IS CONSUMED BY A TRANSITION THAT HAPPENED.
    --------------------------------------------------------------------
    This unlinked `.next-action-called` before evaluating ANY branch, so a
    REFUSED transition burned it, and every refusal whose hint says "fix this
    and re-call Foundry-Phase" named a call that was then refused for a second,
    different reason. Driven on the evidence sweep: broke a committed log,
    called `Foundry-Phase(inspect_start)`, got the correct refusal naming the
    log, repaired the log, and did exactly what the hint said -> "Must call
    Foundry-Next before phase transitions". `_sweep_refusal` carries a `token`
    parameter for no purpose other than naming the right transition to retry,
    so the remedy was engineered to be actionable and the token consumption
    made it not.

    The check stays where it was \u2014 a transition still requires that a
    Foundry-Next preceded it \u2014 and only the CONSUMPTION moves, to the one place
    that knows the transition succeeded. Keyed on `ok` rather than unlinked at
    each success return, because there are a dozen of those and the next branch
    added would forget one; an unknown-token refusal leaves the token in place
    too, which is right for the same reason (a typo is not a transition).
    """
    fdir = get_run_dir(project_root)
    if not fdir or not fdir.exists():
        return {"error": "No active foundry run"}
    # fallout FR-046 / CT-004 (D-090) \u2014 THE HALT TOKEN REFUSES ON
    # `_halt_preconditions` AND ON NOTHING ELSE.
    # ----------------------------------------------------------------------
    # FR-046 is exact about the size of this door for this token: "refuses ONLY
    # on `_halt_preconditions` (reason member, no team registered, not already
    # HALTED)", and CT-004's errors column is "the refusals `_halt_preconditions`
    # reports". The branch itself was already clean; this PREAMBLE was not. Two
    # checks the routine does not report refused ahead of it:
    #
    #   * `_artifact_guard`, for any unreadable run artifact. Driven:
    #     `Foundry-Phase('halt', reason='spec_change_required', text=...)` on a
    #     run with a corrupt verdicts.json returned "Run artifacts cannot be
    #     read" with `state.json.phase` still F3, and the same for a corrupt
    #     defects.json or state.json. The halt door exists so a ruling is
    #     RECORDED in the archive instead of hand-edited (US-006), and a run
    #     whose ledger will not parse is exactly the run a lead reaches it for
    #     with `spec_change_required` or `lead_ruling` \u2014 it was the one run
    #     that could not be sealed. This feature already decouples the same
    #     thing elsewhere on purpose: `_terminal_outlook` answers on a corrupt
    #     run, and the cap seal transitions even when the report cannot be
    #     generated.
    #   * the `.next-action-called` ordering token. "Call Foundry-Next first" is
    #     protocol for a run that is CONTINUING; a lead ending the run on a
    #     ruling is not consulting guidance about what to do next.
    #
    # fallout ST-001 / FR-046 / CT-004 (D-160) — THE THIRD CHECK ST-001'S GUARD
    # NAMES, AND WHY THIS DOOR DOES NOT MAKE IT.
    # ----------------------------------------------------------------------
    # ST-001's guard column names three conditions — "`_halt_preconditions`
    # passes …; report regeneration succeeds; written through `_update_phase`"
    # — and only two are enforced here. That is deliberate and it is a
    # spec-INTERNAL conflict rather than an omission: FR-046 says this token
    # "refuses ONLY on `_halt_preconditions` (reason member, no team
    # registered, not already HALTED)", which forbids the guard ST-001 asserts.
    #
    # This comment used to cite "the lead's ruling on ST-001 vs FR-046" and no
    # such ruling existed — grep for "regeneration succeeds" returned the ST-001
    # row and nothing else, so the citation resolved to nothing and the choice
    # was, in effect, unrecorded. D-160 filed exactly that. The ruling now
    # exists; it was made on the GRIND cycle-5 dispatch of casting 2 and is
    # recorded in the run's handoffs as `lead_ruling_st_001_vs_fr_046`. Quoted
    # here rather than only cited, so a reader resolves it without leaving the
    # file:
    #
    #   "FR-046 governs the refusal question. The halt token must NOT refuse
    #    because report regeneration failed. A run that cannot write its report
    #    must still be able to stop; refusing the halt would strand it in a
    #    worse state than the one the guard is trying to prevent — neither
    #    halted nor reported, with the operator holding a refusal instead of a
    #    backlog. ST-001's intent is nonetheless real: HALTED exists to record a
    #    named backlog, and a seal that returns ok: True while REPORT.md is
    #    absent asserts the deliverable exists when it does not. So: keep the
    #    transition non-refusing, and make the failed seal impossible to mistake
    #    for a clean one."
    #
    # WHERE THE SECOND HALF IS DISCHARGED, because a ruling with no enforcement
    # is the same phantom by another route: `halt._seal_halted` carries
    # `report_generated` False and `report_error` and SAYS so in the `message`
    # `display.py` renders; `gates._halted_refusal` and
    # `_halted_outcome` below read the FILE's presence and name the one call
    # that can still write it; and `guidance._terminal_outlook` — the block on
    # every `Foundry-Next` response — carries `report_written`, so the surface
    # that reports the run's ending reports whether the ending was recorded.
    # `state.json.halted_report_error` persists across all of them.
    halt_scoped = phase == "halt"

    if not halt_scoped and (corrupt := _artifact_guard(fdir)):
        return corrupt

    # fallout ST-001 / CT-004 \u2014 THE ORDERING TOKEN IS NOT ASKED OF A RUN THAT HAS
    # STOPPED, AND THE ORDER MATTERS.
    #
    # The token handshake is a protocol precondition of a transition; the halt
    # is the fact that there are no more transitions. A halted run whose lead
    # called Foundry-Phase without a preceding Foundry-Next would otherwise be
    # told "Must call Foundry-Next before phase transitions" \u2014 an
    # instruction to go and arm a token for a call that can never succeed.
    #
    # fallout AC-062 / GI-029 (D-088) \u2014 WHAT MOVED IS THE REFUSAL, NOT THE
    # ORDERING. This called `_halted_refusal` and RETURNED its sentence, which
    # made `_halt_preconditions`' `not_already_halted` rung unreachable through
    # this door: the routine computed a rank-0 refusal nobody could provoke and
    # the door answered a bare sentence carrying no `refusals` at all. The rung
    # is the routine's now (`_halted_outcome`, the first thing every
    # `_<token>_preconditions` asks), so what is left here is ONE read deciding
    # whether the ORDERING check applies \u2014 a read the routine makes too,
    # which is what GI-011 asks of a door.
    if not halt_scoped and _halted_outcome(fdir) is None:
        nac = fdir / NEXT_ACTION_CALLED_MARKER
        if not nac.exists():
            return {
                "error": "Must call Foundry-Next before phase transitions",
                "hint": "Call Foundry-Next first \u2014 it shows status and guides you.",
            }

    result = _phase_transition(phase, project_root, fdir, reason=reason, text=text)
    if result.get("ok"):
        # D-067: consumed by a transition that HAPPENED, and by nothing else.
        # Unlinked here rather than through the local the check above binds,
        # because that check no longer runs on every path (D-090): the halt
        # token is not asked for the token and a halted run is not asked
        # either, and a success on one of those paths still ends the handshake
        # the marker records.
        (fdir / NEXT_ACTION_CALLED_MARKER).unlink(missing_ok=True)
    return result




#: D-113 / D-114 / D-116 — the transition that actually applies from each phase
#: `inspect_start` is refused from. A refusal that only says "not from here"
#: leaves the lead to guess, and the guess that cost these three defects was
#: "call it anyway"; every entry below names a call the server accepts in that
#: phase.
_INSPECT_START_SOURCE_HINTS = {
    "F0": (
        "The run has not entered CAST. Call Foundry-Phase(phase='start_cast') "
        "to enter F1, build, then Foundry-Phase(phase='cast') — that is the "
        "transition that opens the run's FIRST INSPECT and records its width."
    ),
    "F1": (
        "CAST is still open. Call Foundry-Phase(phase='cast') when the wave is "
        "down — that transition enters F2, records FULL / first_of_phase and "
        "sweeps the evidence corpus. inspect_start would skip CAST entirely."
    ),
    "F4": (
        "The run is in ASSAY. Call Foundry-Phase(phase='assay_fail') to open a "
        "GRIND from an ASSAY rejection (that is the door that enters F3), then "
        "Foundry-Phase(phase='inspect_start') when the GRIND is done."
    ),
    "F5": (
        "The run is in TEMPER. Call Foundry-Phase(phase='grind_start') to open "
        "a GRIND on what TEMPER filed, then Foundry-Phase(phase='inspect_start') "
        "to close it. TEMPER's own INSPECT was opened and recorded by the "
        "Foundry-Phase(phase='temper') transition."
    ),
    "F5.5": (
        "The run is in NYQUIST. Call Foundry-Phase(phase='grind_start') to open "
        "a GRIND on what NYQUIST filed, then Foundry-Phase(phase='inspect_start') "
        "to close it."
    ),
    "F6": (
        "The run is DONE. There is no further INSPECT to open; start a new run "
        "rather than re-opening this one."
    ),
}




#: D-124 / D-125 — the source phases the OTHER TWO INSPECT-opening tokens are
#: accepted from, and the call that applies instead.
#:
#: GI-009 names three transitions that open an INSPECT — the F2 entry (`cast`),
#: the F5 entry (`temper`) and `inspect_start` — and the cycle-6 ruling gave a
#: source-phase precondition to exactly ONE of them. The other two kept
#: D-116's failure shape verbatim, one door over:
#:
#:   D-124  `cast` from F4 (ASSAY), cycle 2, with a `final_gate` decision
#:          already recorded for cycle 2: returned ok, phase F2, mode FULL,
#:          rule first_of_phase. Afterwards state.json carried a THIRD width
#:          decision stamped onto cycle 2, `.cast-baseline-sha` had been
#:          overwritten from the CAST baseline commit to HEAD — destroying the
#:          cycle-context baseline every GRIND prompt is built from — and the
#:          stream roll-up's cycle 2 row read `first_of_phase` where it had
#:          read `final_gate`. CT-009 records ONE decision per INSPECT-opening
#:          transition and FR-023 reports them per cycle; a run in ASSAY was
#:          pulled back into F2 with a fabricated first-of-phase record.
#:
#:   D-125  `temper` from F2, cycle 2, on a run whose recorded width was DELTA
#:          with zero verdicts: returned ok, phase F5, and stamped a SECOND
#:          decision (FULL / first_of_phase / decided_by temper) onto cycle 2
#:          beside the DELTA one. `Foundry-Gate('nyquist')` then passed and
#:          `Foundry-Phase('nyquist')` reached F5.5 — so the last INSPECT
#:          before NYQUIST ran at DELTA width, the widening re-open the cycle-3
#:          ruling requires never happened, and no verdict was ever written.
#:          FR-011 makes the INSPECT before ASSAY/NYQUIST/DONE FULL and US-004
#:          says every final gate still runs everything at full width; neither
#:          held, because the door never asked what phase the run was in.
#:
#: Stated as a TABLE consulted by one helper rather than as an arm inside each
#: branch, because "three transitions open an INSPECT and each guards its own
#: source phase" is precisely the three-copies shape that let two of the three
#: go unguarded for six cycles. `inspect_start` keeps its own inline guard: it
#: admits two phases with different meanings (F3 crossing, F2 widening) and
#: threads `widening` into the decision, which a table cannot express.
#:
#: D-164 — AND THE TWO TERMINAL TOKENS WERE NEVER BROUGHT UNDER IT.
#:
#: ST-010's from-state is "F5.5 or F5 complete", and neither `done` nor
#: `nyquist_done` read `state.phase` at all: both called `_done_preconditions`,
#: whose checklist (report_generated, escalated_classes_cleared, run_not_halted,
#: spec_requirements_parsed, all_verified, zero_blocking_defects,
#: no_active_teams, verdict_coverage, evidence_reproduces_at_head) has no
#: source-phase entry. Driven twice at the real door:
#:
#:   (1) a run at F4 with `temper` and `nyquist` both set, 172/172 VERIFIED,
#:       zero open defects and a generated report: `Foundry-Phase('done')`
#:       returned ok True, phase F6, "Run archived." — straight out of ASSAY,
#:       skipping both post-verification phases the run was STARTED with.
#:   (2) the same run at F2: `Foundry-Phase('nyquist_done')` returned ok True,
#:       phase F6, "NYQUIST complete -> phase is now F6 (DONE)" — a message
#:       asserting a phase that never ran, from inside INSPECT.
#:
#: So `accepted_from` may be a CALLABLE of `state.json`, because `done`'s
#: source phase is a fact about the run's own flags rather than a constant:
#: the terminal phase is F5.5 when `--nyquist` was passed, else F5 when
#: `--temper` was, else F4. A static tuple would either refuse every plain run
#: at F4 or admit the two the defect drove. `nyquist_done` stays a constant —
#: F5.5 is the only phase NYQUIST can be finished from, whatever the flags.
_PHASE_ENTRY_SOURCES: dict[str, dict] = {
    "cast": {
        "accepted_from": ("F1",),
        "opens": "F2",
        "what": (
            "the CAST->INSPECT crossing: it closes F1, stamps the CAST "
            "baseline SHA every GRIND cycle-context block is built from, and "
            "records the run's FIRST INSPECT at FULL / first_of_phase"
        ),
        "why": (
            "From any other phase it would record a second INSPECT decision "
            "against a cycle that already has one."
        ),
        "hints": {
            "F0": (
                "The run has not entered CAST. Call "
                "Foundry-Phase(phase='start_cast') to enter F1 and build the "
                "wave; `cast` is what closes it."
            ),
            "F2": (
                "The run is already in INSPECT. To widen a DELTA cycle before "
                "ASSAY call Foundry-Phase(phase='inspect_start'); to open a "
                "GRIND call Foundry-Phase(phase='grind_start'). Re-running "
                "`cast` would overwrite the CAST baseline SHA with HEAD."
            ),
            "F3": (
                "The run is in GRIND. Close it with "
                "Foundry-Phase(phase='inspect_start') — that is the crossing "
                "that advances the cycle counter and decides this INSPECT's "
                "width."
            ),
            "F4": (
                "The run is in ASSAY. Call Foundry-Phase(phase='assay_fail') "
                "to open a GRIND from an ASSAY rejection, or "
                "Foundry-Phase(phase='temper') to enter TEMPER. `cast` would "
                "pull the run back to F2 and destroy the CAST baseline."
            ),
            "F5": (
                "The run is in TEMPER. Call Foundry-Phase(phase='grind_start') "
                "to open a GRIND on what TEMPER filed, or "
                "Foundry-Phase(phase='nyquist') to enter NYQUIST."
            ),
            "F5.5": (
                "The run is in NYQUIST. Call Foundry-Phase(phase='grind_start') "
                "to open a GRIND on what NYQUIST filed, or "
                "Foundry-Phase(phase='nyquist_done') to finish."
            ),
            "F6": (
                "The run is DONE. Start a new run rather than re-opening this "
                "one."
            ),
        },
    },
    "temper": {
        "accepted_from": ("F4",),
        "opens": "F5",
        "what": (
            "the ASSAY->TEMPER crossing: it enters F5 and opens TEMPER's own "
            "INSPECT at FULL / first_of_phase"
        ),
        "why": (
            "From any other phase it would record a second INSPECT decision "
            "against a cycle that already has one."
        ),
        "hints": {
            "F0": (
                "The run has not started. Call "
                "Foundry-Phase(phase='start_cast') and build first."
            ),
            "F1": (
                "CAST is still open. Call Foundry-Phase(phase='cast') when the "
                "wave is down."
            ),
            "F2": (
                "The run is in INSPECT, and TEMPER is reached THROUGH ASSAY. "
                "Close this INSPECT with "
                # D-169: the width, which is what that door reads. Naming the
                # rule here made this hint the fourth surface stating a
                # condition no code evaluates.
                "Foundry-Phase(phase='inspect_clean') — which refuses a DELTA "
                "cycle until the widening re-open records FULL — "
                "then Foundry-Gate(phase='assay'), run ASSAY, and only then "
                "Foundry-Phase(phase='temper'). Entering F5 from here would "
                "leave the last INSPECT before NYQUIST at DELTA width with no "
                "verdict written (FR-011 / US-004)."
            ),
            "F3": (
                "The run is in GRIND. Close it with "
                "Foundry-Phase(phase='inspect_start'), run the roster it "
                "names, then inspect_clean and ASSAY."
            ),
            "F5": (
                "The run is already in TEMPER. Call "
                "Foundry-Phase(phase='nyquist') to enter NYQUIST, or "
                "Foundry-Phase(phase='grind_start') to open a GRIND on what "
                "TEMPER filed."
            ),
            "F5.5": (
                "The run is in NYQUIST, which follows TEMPER. Call "
                "Foundry-Phase(phase='nyquist_done') to finish, or "
                "Foundry-Phase(phase='grind_start') to open a GRIND."
            ),
            "F6": (
                "The run is DONE. Start a new run rather than re-opening this "
                "one."
            ),
        },
    },
    # fallout AC-010 / OT-008 — THE NYQUIST TRANSITION GAINS A SOURCE CHECK.
    #
    # It had none, and its own guidance says F5.5 is reached from two places and
    # only two: "a --nyquist run without --temper arrives from F4 (ASSAY
    # passed), and one with both arrives from F5 (TEMPER clean)"
    # (`_nyquist_transition`). Without the check, `nyquist` from F2 wrote F5.5
    # over a live INSPECT and made the auditors reachable on a tree no ASSAY had
    # judged. `accepted_from` is a CALLABLE for the reason `done`'s is: which
    # phase precedes F5.5 is a fact about the run's own flags, and a static
    # tuple would either refuse every plain --nyquist run at F4 or admit the F2
    # crossing this closes.
    "nyquist": {
        "accepted_from": lambda state: ("F5",) if state.get("temper") else ("F4",),
        "opens": "F5.5",
        "what": (
            "the entry to NYQUIST: it closes the phase ASSAY (or TEMPER) left "
            "the run in, sweeps the whole evidence corpus and opens F5.5, where "
            "regression tests are generated for VERIFIED requirements"
        ),
        "why": (
            "From any other phase it would lock in behaviour no ASSAY has "
            "judged, which is the one thing F5.5 must never do."
        ),
        "hints": {
            "F0": (
                "The run has not been built. Foundry-Next names the transition "
                "that applies; NYQUIST is the last phase, not the first."
            ),
            "F1": (
                "The run is in CAST. Close it with Foundry-Phase(phase='cast')."
            ),
            "F2": (
                "The run is in INSPECT. Close it with "
                "Foundry-Phase(phase='inspect_clean') to open ASSAY — NYQUIST "
                "is reached THROUGH ASSAY, and entering it from here would "
                "generate regression tests for verdicts nothing has written."
            ),
            "F3": (
                "The run is in GRIND. Close it with "
                "Foundry-Phase(phase='inspect_start')."
            ),
            "F4": (
                "This run was started with --temper, so F5 comes first: call "
                "Foundry-Phase(phase='temper'), and reach NYQUIST from there."
            ),
            "F5": (
                "This run was not started with --temper, so NYQUIST is entered "
                "from F4. A run already in F5 reached it some other way; "
                "Foundry-Next names the transition that applies."
            ),
            "F5.5": (
                "The run is already in NYQUIST. Call "
                "Foundry-Phase(phase='nyquist_done') to finish it."
            ),
            "F6": (
                "The run is DONE. Start a new run rather than re-opening this "
                "one."
            ),
        },
    },
    "nyquist_done": {
        "accepted_from": ("F5.5",),
        "opens": "F6",
        "what": (
            "the NYQUIST->DONE crossing: it closes F5.5, enters F6 and archives "
            "the run"
        ),
        "why": (
            "From any other phase its own success message — \"NYQUIST complete\" "
            "— asserts a phase that never ran."
        ),
        "hints": {
            "F0": (
                "The run has not started. Call "
                "Foundry-Phase(phase='start_cast') and build first."
            ),
            "F1": (
                "CAST is still open. Call Foundry-Phase(phase='cast') when the "
                "wave is down."
            ),
            "F2": (
                "The run is in INSPECT. NYQUIST is reached THROUGH ASSAY and "
                "TEMPER: close this INSPECT with "
                "Foundry-Phase(phase='inspect_clean'), pass "
                "Foundry-Gate(phase='assay'), run ASSAY, and follow "
                "Foundry-Next from there. `nyquist_done` claims a phase this "
                "run has not entered."
            ),
            "F3": (
                "The run is in GRIND. Close it with "
                "Foundry-Phase(phase='inspect_start'), run the roster it names, "
                "then inspect_clean and ASSAY."
            ),
            "F4": (
                "The run is in ASSAY. NYQUIST has not been entered, so it "
                "cannot be finished. Follow Foundry-Next: it names "
                "Foundry-Phase(phase='temper') or "
                "Foundry-Phase(phase='nyquist') according to the flags this "
                "run was started with."
            ),
            "F5": (
                "The run is in TEMPER. Enter NYQUIST first with "
                "Foundry-Phase(phase='nyquist') — which sweeps the whole "
                "evidence corpus — and finish it with `nyquist_done`."
            ),
            "F6": (
                "The run is already DONE. Start a new run rather than "
                "re-closing this one."
            ),
        },
    },
    "done": {
        # D-164: a CALLABLE, because the terminal phase is the run's own flags.
        "accepted_from": lambda state: (
            ("F5.5",)
            if state.get("nyquist")
            else ("F5",) if state.get("temper") else ("F4",)
        ),
        "opens": "F6",
        "what": (
            "the run's terminal transition: it enters F6, archives the run and "
            "clears the active-run marker"
        ),
        "why": (
            "ST-010 crosses into F6 from the LAST phase this run's own flags "
            "make terminal — F5.5 with --nyquist, else F5 with --temper, else "
            "F4 — so from any earlier phase it would finish the run without "
            "the post-verification phases it was started with."
        ),
        "hints": {
            "F0": (
                "The run has not started. Call "
                "Foundry-Phase(phase='start_cast') and build first."
            ),
            "F1": (
                "CAST is still open. Call Foundry-Phase(phase='cast') when the "
                "wave is down."
            ),
            "F2": (
                "The run is in INSPECT. Close it with "
                "Foundry-Phase(phase='inspect_clean') and pass "
                "Foundry-Gate(phase='assay'); DONE is reached through ASSAY, "
                "never from inside an INSPECT."
            ),
            "F3": (
                "The run is in GRIND. Close it with "
                "Foundry-Phase(phase='inspect_start'), run the roster it names, "
                "then inspect_clean and ASSAY."
            ),
            "F4": (
                "ASSAY has passed, but this run was started with "
                "post-verification phases it has not run. --temper enters "
                "TEMPER through Foundry-Phase(phase='temper'); --nyquist enters "
                "NYQUIST through Foundry-Phase(phase='nyquist') and is finished "
                "with Foundry-Phase(phase='nyquist_done'). Foundry-Next names "
                "which applies here."
            ),
            "F5": (
                "The run is in TEMPER and --nyquist is set, so F5 is not the "
                "terminal phase. Call Foundry-Phase(phase='nyquist') to enter "
                "NYQUIST, then Foundry-Phase(phase='nyquist_done')."
            ),
            "F5.5": (
                "NYQUIST is open. Finish it with "
                "Foundry-Phase(phase='nyquist_done'), which is the F6 door from "
                "F5.5."
            ),
            "F6": (
                "The run is already DONE. Start a new run rather than "
                "re-closing this one."
            ),
        },
    },
}




def _phase_entry_source_problem(fdir: Path, token: str) -> dict | None:
    """The refusal a guarded token owes from a wrong source phase.

    None when the run is in a phase `token` is accepted from. See
    `_PHASE_ENTRY_SOURCES` for the four defects this closes and for why the
    table is one table.

    Reads the phase from state.json at call time, exactly as `inspect_start`'s
    inline guard does, and refuses BEFORE any decision, sweep or marker write —
    so a transition the server refused leaves no trace it was attempted.

    D-164: `accepted_from` is a tuple, or a callable of the state document for a
    token whose source phase depends on the run's own flags. Resolved HERE, so
    every branch that consults the table gets the same resolution and no branch
    re-derives "which phase is terminal for this run".
    """
    spec = _PHASE_ENTRY_SOURCES.get(token)
    if spec is None:
        return None
    state = _load_json(fdir / "state.json")
    current = state.get("phase", "")
    accepted = spec["accepted_from"]
    if callable(accepted):
        accepted = accepted(state)
    if current in accepted:
        return None
    return {
        "error": (
            f"Cannot enter {spec['opens']} from phase {current or 'F0'} — "
            f"Foundry-Phase(phase='{token}') is {spec['what']}, accepted from "
            f"{' or '.join(accepted)} and from nowhere else. "
            + spec["why"]
        ),
        "hint": spec["hints"].get(
            current,
            f"Reach {' or '.join(accepted)} first; "
            f"Foundry-Next names the transition that applies in {current or 'F0'}.",
        ),
        "phase": current,
        "accepted_from": list(accepted),
    }




def _phase_transition(
    phase: str,
    project_root: str,
    fdir: Path,
    *,
    reason: str = "",
    text: str = "",
) -> dict:
    """The branch chain behind `foundry_mark_phase_complete`.

    Split out for D-067 alone: the caller owns the ordering-token handshake and
    consumes the token only when this returns a success. Every branch is
    verbatim what lived in the public function, and the guards that derive the
    accepted phase-token set from an AST walk read THIS function, because this
    is where the branches are.

    D-082 — NO TRANSITION LEAVES HALTED, AND THE GUARD IS STATED ONCE.
    -----------------------------------------------------------------
    Stated ONCE, and after D-088 the once is `_halted_outcome`, the first rung
    of every `_<token>_preconditions`. One copy per branch of one precondition
    is exactly the shape that produced D-082: `_halt_if_capped` was wired into
    `grind_start` and `assay_fail` and every other branch silently resumed the
    run. A new branch inherits the guard by having a routine, which it must have
    anyway (GI-011).

    fallout AC-062 / GI-029 (D-088) — IT IS NOT STATED HERE ANY MORE, AND THAT
    IS THE FIX. `_halted_refusal` above this chain refused BEFORE the routine
    ran, so `_halt_preconditions`' `not_already_halted` rung and
    `_done_preconditions`' HALTED rung were unreachable through this door: the
    routine computed a rank-0 refusal that nobody could provoke, and the door
    answered a sentence carrying no `refusals` at all. GI-029 is "exactly the
    preconditions function; no extra reads or refusals", and a guard the routine
    also makes is an extra refusal however well it agrees. The short-circuit
    survives inside `_halted_outcome`, so a halted run still costs one
    `state.json` read and no sweep.

    fallout FR-007 / GI-029 / ST-012 / AC-009 / AC-056 — EVERY BRANCH IS ONE
    CALL, ONE REFUSAL, THEN THE MUTATION.
    ------------------------------------------------------------------------
    A branch consults `_<token>_preconditions` and NOTHING ELSE for its
    refusals: no ledger read of its own, no marker read of its own, no check the
    gate for the same token does not also make. Everything below the refusal is
    effect — decide, sweep, clear, transact, record — and every one of those
    runs only after the shared routine passed, so a refused crossing leaves no
    trace it was attempted.

    That is the whole of what closed D-240..D-243. Four checks the gate made and
    this function did not is not four defects, it is one generator, and it stops
    generating when there is exactly one place a check can be written.

    `reason` and `text` belong to the `halt` token alone and are passed through
    untouched; every other branch ignores them.
    """
    if phase == "start_cast":
        outcome = _start_cast_preconditions(fdir, project_root)
        if not outcome["passed"]:
            return _transition_refusal(outcome, "Cannot enter CAST")
        _update_phase(fdir, "F1")
        return {"ok": True, "phase": "F1", "message": "Phase is now F1 (CAST). Create team and build."}

    elif phase == "cast":
        outcome = _cast_preconditions(fdir, project_root)
        if not outcome["passed"]:
            return _transition_refusal(outcome, "Cannot mark CAST complete")
        # GI-009 / ST-006 / AC-016 / OT-012 \u2014 THE F2 ENTRY RECORDS FULL.
        #
        # This branch, not `start_cast`: `start_cast` calls
        # `_update_phase(fdir, "F1")` and enters CAST, opening no INSPECT at
        # all. `cast` is the token that enters F2, so it is the transition that
        # opens the run's first INSPECT and therefore the one that decides its
        # width \u2014 recorded here, before any Foundry-Next is called (OT-012).
        #
        # fallout FR-058 / GI-029 \u2014 DECIDED AND SWEPT BY THE ROUTINE, READ HERE.
        # D-014's sweep and its `_sweep_refusal` stood in this branch, which made
        # them a refusal the gate for this token could not make.
        # `_boundary_evidence_rung` owns both now and what is left here is the
        # effect. The ordering the old arm defended survives by construction:
        # the routine refuses before this line is reached, so a refused
        # transition still leaves no trace it was attempted.
        entry = outcome["inspect_entry"]

        # D-221 / GI-009 / AC-016 — AND THE COMPLETION HALF HERE TOO.
        #
        # This door was ruled safe by construction: F1 is entered by
        # `start_cast`, "before any stream can have reported". `start_cast`
        # carries NO entry-source precondition — it is `_update_phase(fdir,
        # "F1")` under the halted guard alone — so F1 is reachable from any
        # live phase, markers and all. Driven: from F2 with five markers on
        # disk, `start_cast` -> `cast` returned ok with the five-stream FULL
        # roster and every marker untouched, and `_check_streams_complete`
        # answered four of five complete for an INSPECT that had not begun.
        #
        # Cleared here rather than guarded upstream: the width this branch just
        # recorded and the completion state it opens on are one rule, and a rule
        # enforced at the door needs no argument about how the door was reached.
        # AFTER the sweep refusal above, so a refused crossing leaves the run's
        # completion state exactly as it found it.
        cleared = _clear_stream_completion_markers(fdir)

        (fdir / CAST_COMPLETE_MARKER).write_text(f"{now_iso()}\n", encoding="utf-8")
        # Stamp the CAST baseline HEAD SHA so GRIND cycles can show teammates
        # what has changed since CAST ended. Used by foundry_spawn_teammate
        # (phase='grind') to build a cycle-context block the lead appends to
        # the GRIND prompt, saving redundant re-exploration of files that
        # earlier cycles already touched.
        # fallout research/holmes-orchestrator.md#share-4 (D-098) — THROUGH THE
        # ONE ADAPTER, which this module already imports and already uses at the
        # `inspect_start` boundary. An inline copy beside an import of the same
        # helper is the shape share-4 names: `_head_sha` existed and was not the
        # exclusive point, so the same invocation carried four timeouts and four
        # exception tuples across the package.
        if (baseline := _head_sha(project_root)):
            (fdir / CAST_BASELINE_SHA_MARKER).write_text(baseline, encoding="utf-8")
        _update_phase(fdir, "F2")
        _record_inspect_mode(fdir, entry)
        _record_cycle_rollup(
            fdir, current_cycle(fdir), evidence_sweep=outcome["evidence_sweep"]
        )
        return {
            "ok": True,
            "phase": "F2",
            "inspect_mode": entry["mode"],
            "inspect_rule": entry["rule"],
            "required_streams": entry["required_streams"],
            "evidence_sweep": outcome["evidence_sweep"],
            "cleared_markers": cleared,
            "message": (
                f"CAST complete \u2192 phase is now F2 (INSPECT), mode {entry['mode']} "
                f"(rule {entry['rule']}). Required streams: "
                f"{', '.join(entry['required_streams'])}. Evidence sweep "
                f"re-executed {len(outcome['evidence_sweep']['logs_reexecuted'])} log(s) "
                f"at {outcome['evidence_sweep']['scope']} scope."
            ),
        }

    elif phase == "inspect_clean":
        outcome = _inspect_clean_preconditions(fdir, project_root)
        if not outcome["passed"]:
            return _transition_refusal(outcome, "Cannot mark INSPECT clean")
        (fdir / INSPECT_CLEAN_MARKER).write_text(f"{now_iso()}\n", encoding="utf-8")
        _update_phase(fdir, "F4")
        return {"ok": True, "phase": "F4", "message": "INSPECT clean \u2192 phase is now F4 (ASSAY)"}

    elif phase == "inspect_start":
        # ST-001 / FR-005 / AC-008 — the GRIND -> INSPECT boundary.
        #
        # This token did not exist. The guidance engine told the lead to
        # "update state to F2" with no tool that does it, so a run looping
        # GRIND -> INSPECT never left F3 in state.json and the cycle counter
        # never moved: grand-vulture recorded "cycle": 0 across 18 cycles while
        # its defects carried lead-asserted cycles 0-17.
        #
        # The increment is an EFFECT of handling the boundary crossing, not a
        # value any caller supplies: this handler takes no cycle argument and
        # consults none. Only F3 -> F2 advances it — the F1 -> F2 entry from
        # CAST is the run's first INSPECT, not a new cycle.
        #
        # D-113 / D-114 / D-116 — THE SOURCE PHASE IS A PRECONDITION, and the
        # two widening arms with it. All three used to be stated here and
        # nowhere else, so `Foundry-Gate` answered a question about a crossing
        # it could not refuse. They are `_inspect_start_preconditions` now, and
        # `widening` is read off the outcome rather than re-derived: one read of
        # `state.phase`, one answer, two doors.
        outcome = _inspect_start_preconditions(fdir, project_root)
        if not outcome["passed"]:
            return _transition_refusal(outcome, "Cannot start an INSPECT")
        state_path = fdir / "state.json"
        completed_cycle = current_cycle(fdir)
        widening = outcome["widening"]

        # ST-006 / GI-002 / ST-005 / CT-007 — DECIDE, THEN SWEEP, THEN
        # TRANSACT. IN THAT ORDER, AND OUTSIDE THE LOCK.
        #
        # The counter advance below lives inside `_document_transaction`, an
        # fcntl-locked critical section over state.json that every other tool
        # call on this run contends on. `sweep_evidence_at_head` creates a
        # detached worktree and runs a bounded thread pool of subprocesses
        # inside it — seconds to minutes. Holding the flock across that would
        # serialise the entire run for the duration, for no benefit: neither
        # the mode decision nor the sweep reads or writes state.json.
        #
        # It also gets the refusal semantics right for free. A mismatched log
        # returns BEFORE the transaction opens, so the cycle counter is
        # untouched (AC-013 / OT-008) and no mode is recorded for a transition
        # that did not happen. A transition the server refused must leave no
        # trace that it was attempted.
        # fallout FR-058 / GI-029: the decision and the sweep are rungs of
        # `_inspect_start_preconditions` now, so `Foundry-Gate('inspect_start')`
        # refuses the mismatch this branch used to refuse alone. Read off the
        # outcome rather than re-taken: one decision, one sweep, two doors.
        entry = outcome["inspect_entry"]

        # D-221 / GI-009 / AC-016 / AC-017 — THIS DOOR OPENS AN INSPECT, SO IT
        # CLEARS THE PREVIOUS ONE'S COMPLETION STATE. BOTH ARMS.
        #
        # The F2->F2 widening re-open is the arm the defect was driven on. It
        # is the FINAL GATE: it advances the counter, records FULL / final_gate
        # and requires the five-stream roster — over the completion markers the
        # DELTA cycle wrote. Driven at cycle 2 DELTA with trace, prove and test
        # recorded at 40/40, the widening crossing returned ok at cycle 3 with
        # the FULL roster and left all three markers on disk;
        # `_check_streams_complete` then named only research_audit and test01,
        # so the gate US-004 says "still runs everything at full width" was
        # satisfied by three streams that never ran at that width. The coverage
        # arm could not catch it either: cycle 3 has no roll-up yet, so
        # `_coverage_shortfall` falls back to `_marker_counts` on the stale
        # cycle-2 marker and reads 40/40.
        #
        # UNCONDITIONAL, not `if widening`. The F3 arm inherits a cleared state
        # from `grind_start`/`assay_fail` today, and that inheritance is exactly
        # the kind of by-construction argument that licensed this defect and
        # D-219 before it. Clearing on both arms makes "opening an INSPECT
        # clears the previous one" a property of the door instead of a property
        # of the path taken to it; on the F3 arm the call returns an empty list,
        # which is the proof there was nothing stale rather than a reason to
        # skip it. Anything a stream reported DURING the GRIND belongs to no
        # INSPECT and is stale here by definition.
        #
        # Placed after the sweep refusal and before the state transaction —
        # the ordering the `temper` branch already holds — so a refused crossing
        # leaves the run's completion state exactly as it found it, and the
        # clear does not run under the state.json flock.
        cleared_markers = _clear_stream_completion_markers(fdir)

        # D-103: read-phase, advance-phase and increment are ONE critical
        # section. They used to be three separate read-modify-writes over the
        # same file (the increment re-read state.json AFTER _update_phase had
        # written it), so a concurrent writer landing between them lost either
        # the phase or the counter. _update_phase nests inside this transaction
        # and mutates the same in-flight document — which is what the
        # re-entrancy in _document_transaction exists for.
        advanced = False
        with _document_transaction(state_path) as state:
            prev_phase = state.get("phase", "")
            _update_phase(fdir, "F2")
            # F3 is the ordinary GRIND->INSPECT crossing; F2 is the widening
            # re-open, which the ruling makes a cycle of its own — it sweeps the
            # whole corpus and runs the full roster, so every record it produces
            # belongs to a new cycle number rather than overwriting the DELTA
            # cycle's.
            #
            # D-113 / D-114: the source-phase precondition above admits exactly
            # these two, so this condition is now always true and `advanced` is
            # always True. Both are KEPT rather than simplified away — the
            # counter advance is the fact both escalation exit arms are stated
            # in terms of, and reading it off the transaction that performed it
            # is what makes "the arms run only on a transition that advanced the
            # counter" a property of the code rather than of a comment.
            advanced = prev_phase == "F3" or (prev_phase == "F2" and widening)
            if advanced:
                # _current_cycle still reads from disk, and that is correct
                # here: the flock guarantees no peer is mid-write and this
                # transaction has not flushed, so disk still holds the
                # pre-increment counter. Keeping the read on the ONE guarded
                # reader is what D-059's derived-membership test requires.
                state["cycle"] = current_cycle(fdir) + 1
                state["updated_at"] = now_iso()
            modes = state.get("inspect_modes")
            if not isinstance(modes, list):
                modes = []
            entry["cycle"] = state.get("cycle", completed_cycle + 1)
            modes.append(entry)
            state["inspect_modes"] = modes

        cycle = current_cycle(fdir)
        _record_cycle_rollup(
            fdir,
            cycle,
            inspect_mode=entry["mode"],
            inspect_rule=entry["rule"],
            stream_scope=entry["stream_scope"],
            evidence_sweep=outcome["evidence_sweep"],
        )
        # The commit this boundary crossed at, so the NEXT crossing's diff is
        # measured from here rather than from whatever older marker happened to
        # survive. Written after the transition commits: a marker naming a
        # boundary the server refused would silently narrow the next sweep.
        head = _head_sha(project_root)
        if head:
            (fdir / INSPECT_BOUNDARY_SHA_MARKER).write_text(
                f"{head}\n", encoding="utf-8"
            )

        # D-057 / D-058: BOTH exit arms, at the one event that knows a cycle
        # has ended. `completed_cycle` is the cycle that just closed — the one
        # whose filings are all in and whose structural packet has now closed —
        # and `cycle` is the counter this crossing produced, which is what
        # `cleared_at_cycle` records.
        #
        # D-113 / D-114: GUARDED ON THE ADVANCE. Both arms are stated in terms
        # of a cycle having ENDED ("two consecutive INSPECT cycles", "the second
        # structural packet CLOSED"), and the only evidence a cycle ended is the
        # counter moving. A crossing that did not move it has closed nothing, so
        # there is nothing for either arm to evaluate.
        cleared: list[dict] = []
        if advanced:
            # D-111 — THE RECORD BOTH ARMS NEED IS WRITTEN AT THIS BOUNDARY.
            #
            # `_advance_escalation_exits` returns immediately unless
            # `_persisted_escalations` finds a record, and the ONLY writer of
            # `escalation.json` was `_record_escalation_proposals`, reached only
            # from `foundry_defects_to_tasks`. So an escalated class whose open
            # backlog is entirely LATENT never acquired an entry: the DONE guard
            # blocks on `_escalated_classes`, which is derived from ledger
            # recurrence and is NOT tier-aware, while Foundry-Next's
            # transition_to_grind branch — the only caller of
            # `_escalation_notice` and the only branch naming Foundry-Tasks — is
            # guarded by `open_count > 0`, which counts LIVE and untiered only.
            #
            # Driven: three LATENT instances of class FDC at server cycles 1, 2
            # and 3 gave `escalated ['FDC']` with `escalation.json` ABSENT;
            # Foundry-Next never named the class or Foundry-Tasks; and
            # `Foundry-Gate('done')` refused "1 defect class(es) are still
            # ESCALATED: FDC". Following that refusal's own hint six times
            # (inspect_start) walked the counter from 4 to 9 with
            # `escalation.json` still absent and the class still escalated — the
            # hint named a call that was provably a no-op in that state, and its
            # alternative named no call at all. D-034's ruling promises the block
            # is bounded at "at most two more INSPECT cycles or one structural
            # packet"; there it was unbounded.
            #
            # Recorded through the ONE writer rather than synthesised here: a
            # second creator of an escalation.json entry is the drift shape this
            # module has paid for in D-001, D-043 and D-058, and
            # `_record_escalation_proposals` already writes the full C-3 entry
            # with `escalated_at_cycle` latched by `setdefault`.
            _record_escalation_proposals(
                fdir, _escalated_classes(fdir, project_root)
            )
            cleared = _advance_escalation_exits(
                fdir, project_root, completed_cycle, cycle
            )

        result = {
            "ok": True,
            "phase": "F2",
            "cycle": cycle,
            "inspect_mode": entry["mode"],
            "inspect_rule": entry["rule"],
            "required_streams": entry["required_streams"],
            "evidence_sweep": outcome["evidence_sweep"],
            "widened": widening,
            "cleared_markers": cleared_markers,
            "message": (
                (
                    "INSPECT re-opened at full width → "
                    if widening
                    else "GRIND complete → "
                )
                + f"phase is now F2 (INSPECT), cycle {cycle}, "
                f"mode {entry['mode']} (rule {entry['rule']}). Required "
                f"streams: {', '.join(entry['required_streams'])}. Evidence "
                f"sweep re-executed {len(outcome['evidence_sweep']['logs_reexecuted'])} "
                f"log(s) at {outcome['evidence_sweep']['scope']} scope."
            ),
        }
        if cleared:
            result["escalation_cleared"] = cleared
        return result

    elif phase == "grind_start":
        # fallout GI-032 / ST-015 / AC-060 — THE CAP IS ACTED ON, NEVER READ
        # HERE. `would_halt` is computed by the shared routine from the one
        # `persisted_max_cycles` read this run has; this branch does what the
        # fact says. A cap read inside a transition branch is the second
        # derivation GI-032 exists to forbid.
        outcome = _grind_start_preconditions(fdir, project_root)
        if not outcome["passed"]:
            return _transition_refusal(outcome, "Cannot open a GRIND")
        if (halt := _halt_if_capped(
            fdir, project_root, "grind_start", outcome,
            update_phase=_update_phase,
        )) is not None:
            return halt
        # Every recordable stream marker is cleared (derived from the canonical
        # stream vocabulary so new streams cannot go stale across GRIND cycles),
        # not just the required subset — completion state must stay honest.
        # D-219: through the ONE spelling, which the `temper` branch needed and
        # a third copy of this loop would not have given it.
        _clear_stream_completion_markers(fdir)
        _update_phase(fdir, "F3")
        return {"ok": True, "phase": "F3",
                "message": "All markers cleared \u2192 phase is now F3 (GRIND). Full INSPECT must re-run after."}

    elif phase == "assay_fail":
        # fallout ST-001 / CT-004 - AND ON THIS DOOR TOO.
        #
        # `assay_fail` clears the same markers and calls the same
        # `_update_phase(fdir, "F3")` as `grind_start`; it is a second door into
        # the same phase, not a different kind of transition. A cap wired to one
        # of them would let a run looping back through ASSAY failure run forever
        # while a run looping through GRIND halts - and the ASSAY loop is
        # exactly the one --max-cycles exists to bound.
        outcome = _assay_fail_preconditions(fdir, project_root)
        if not outcome["passed"]:
            return _transition_refusal(outcome, "Cannot open a GRIND from an ASSAY rejection")
        if (halt := _halt_if_capped(
            fdir, project_root, "assay_fail", outcome,
            update_phase=_update_phase,
        )) is not None:
            return halt
        _clear_stream_completion_markers(fdir)
        _update_phase(fdir, "F3")
        return {"ok": True, "phase": "F3",
                "message": "ASSAY failed \u2192 phase is now F3 (GRIND). Fix defects, then full INSPECT, then ASSAY again."}

    elif phase == "temper":
        # fallout AC-010 — D-240 CLOSES HERE. The verdict read was the temper
        # GATE's alone; this branch entered F5 without it. Refused before the
        # mode is decided, before the corpus is swept into a detached worktree,
        # and before any marker is cleared — so a refused crossing costs nothing
        # and leaves the run exactly as it found it.
        outcome = _temper_preconditions(fdir, project_root)
        if not outcome["passed"]:
            return _transition_refusal(outcome, "Cannot enter F5 TEMPER")
        # GI-009 / AC-016: the F5 entry opens TEMPER's first INSPECT, so it
        # records FULL / first_of_phase on exactly the same terms as the F2
        # entry above. One rule, two doors — which is what GI-009 means by
        # "whichever Foundry-Phase transition opens an INSPECT records the
        # mode".
        entry = outcome["inspect_entry"]
        # D-014 — AND HERE, ON THE SAME TERMS.
        #
        # FR-009: the whole corpus is swept "whenever the FULL rule fires", and
        # this entry records FULL / first_of_phase exactly as the F2 entry does.
        # It swept nothing, and `_sweep_evidence_at_boundary` had exactly ONE
        # call site in the server — so on the clean path ASSAY → TEMPER →
        # NYQUIST → DONE no sweep ran at all, while the lead-lane fixes that
        # US-005 exists to enable were landing commits throughout F5. The
        # boundary that opens the LAST inspection of a run was the one boundary
        # not checking that the run's committed evidence still reproduces.

        # D-219 / GI-009 / AC-016 — AND THE OTHER HALF OF "ONE RULE, TWO DOORS".
        #
        # The width half was carried across to this door and the completion
        # half was not: this branch recorded FULL / first_of_phase with the
        # five-stream roster and left the F2 INSPECT's `.{stream}-complete`
        # markers on disk, so the roster it had just recorded was satisfied on
        # arrival by markers written before ASSAY. Driven: five markers present
        # at F4, `temper` returns ok with `required_streams ['trace','prove',
        # 'test','research_audit','test01']`, and `_check_streams_complete`
        # then answers `complete True, missing ''` for an INSPECT in which none
        # of the five ran. `grind_start` and `assay_fail` have cleared them
        # since they were written, under the reason "completion state must stay
        # honest"; that reason is a property of opening a fresh INSPECT, not of
        # entering GRIND, and F5 is the one entry no earlier crossing cleared
        # for. See `_clear_stream_completion_markers` for why the other two
        # INSPECT-opening doors inherit a cleared state without calling it.
        #
        # AFTER the sweep refusal above and immediately before the phase write,
        # so a refused crossing leaves the run's completion state exactly as it
        # found it — the same ordering this branch already holds for the mode
        # record and the roll-up.
        cleared = _clear_stream_completion_markers(fdir)
        _update_phase(fdir, "F5")
        _record_inspect_mode(fdir, entry)
        # D-070: under the F5 entry's own key, beside the mode it just
        # recorded — this sweep belongs to the TEMPER crossing, not to the
        # INSPECT cycle whose number it happens to share.
        _record_cycle_rollup(
            fdir,
            current_cycle(fdir),
            sub=TEMPER_ENTRY_ROLLUP_KEY,
            evidence_sweep=outcome["evidence_sweep"],
        )
        return {
            "ok": True,
            "phase": "F5",
            "inspect_mode": entry["mode"],
            "inspect_rule": entry["rule"],
            "required_streams": entry["required_streams"],
            "evidence_sweep": outcome["evidence_sweep"],
            "cleared_markers": cleared,
            "message": (
                f"Phase is now F5 (TEMPER), mode {entry['mode']} "
                f"(rule {entry['rule']}). Required streams: "
                f"{', '.join(entry['required_streams'])} — TEMPER's INSPECT "
                f"runs them itself; {len(cleared)} stale completion marker(s) "
                "from the previous INSPECT were cleared. Evidence sweep "
                f"re-executed {len(outcome['evidence_sweep']['logs_reexecuted'])} log(s) "
                f"at {outcome['evidence_sweep']['scope']} scope."
            ),
        }

    elif phase == "nyquist":
        # Enter F5.5. Mirrors the "temper" token: the phase mark is what makes
        # _compute_next_action's F5.5 branch reachable at all, since it
        # dispatches on state["phase"].
        #
        # fallout AC-010 — D-241 AND D-242 CLOSE HERE. The verdict read and the
        # `state.nyquist` read were the nyquist GATE's alone, so a run that
        # never asked for F5.5 entered it and generated regression tests locking
        # in behaviour a stream had already ruled wrong, by calling Phase
        # without Gate or straight past a Gate that refused. The source phase
        # joins them: F5.5 is reached from F4 (or F5 on a --temper run) and from
        # nowhere else.
        outcome = _nyquist_preconditions(fdir, project_root)
        if not outcome["passed"]:
            return _transition_refusal(outcome, "Cannot enter F5.5 NYQUIST")
        # D-133 / D-159 / GI-002 — AND THE CORPUS IS SWEPT BEFORE NYQUIST, BY
        # NAME. GI-002 names three terminal boundaries — "before
        # ASSAY/NYQUIST/DONE" — and this was the one crossing between them that
        # re-executed nothing. TEMPER lands lead-lane fixes throughout F5 (that
        # is what US-005 exists to enable), and each one changes the tree the
        # committed evidence was captured against. It takes the same three-state
        # rung the other two do: a whole-corpus sweep over a corpus no longer in
        # the tree yields zero logs, zero mismatches and ok True, which is how a
        # --nyquist run entered F5.5 through the door that did not apply the
        # rule. Kept OUT of the shared routine deliberately: the sweep spawns a
        # detached worktree and a bounded pool of subprocesses, and a gate is a
        # query a lead runs freely.
        nyq_record = outcome["evidence_record"]
        _update_phase(fdir, "F5.5")
        _record_cycle_rollup(
            fdir,
            current_cycle(fdir),
            sub=NYQUIST_ENTRY_ROLLUP_KEY,
            evidence_sweep=nyq_record,
        )
        return {
            "ok": True,
            "phase": "F5.5",
            "evidence_sweep": nyq_record,
            "message": (
                "Phase is now F5.5 (NYQUIST). Evidence sweep re-executed "
                f"{len(nyq_record.get('logs_reexecuted', []))} log(s) "
                "at full scope."
            ),
        }

    elif phase == "nyquist_done":
        # Leave F5.5 for F6. Distinct from the "temper" shape, which exits via
        # "done", because NYQUIST has its own completion semantics: auditors
        # can escalate ESCALATE_IMPL_BUG back into GRIND, so "the phase ran"
        # and "the run is finished" are separate facts.
        #
        # AC-011 / D-043 / D-044 — F6 HAS TWO DOORS AND BOTH ARE LOCKED.
        #
        # This comment used to assert "The DONE gate still runs first" while
        # the branch below it was an unconditional _update_phase +
        # clear_active_run consulting nothing: the claim rested entirely on the
        # lead following guidance prose. D-037 bound _done_preconditions to the
        # `done` branch and left this sibling terminal branch unbound, so the
        # fix covered one door of two — and on a --nyquist run, which
        # commands/start.md:578 routes through this token, it was the door the
        # run would actually use. Driven at the MCP boundary: a state with six
        # open escalated-class defects and no verdicts.json at all was refused
        # by Foundry-Phase("done") and admitted by Foundry-Phase("nyquist_done")
        # in the same breath.
        #
        # AC-011 constrains THE RUN — "the run cannot reach DONE while any
        # escalated-class defect remains open" — with no exception for how F6
        # is entered. So this calls the same _done_preconditions the `done`
        # branch and the gate call, on the same terms. Not a copy of the
        # checks: a second implementation of "done" is the drift that produced
        # this defect, not a fix for it.
        #
        # D-164 — AND WHICH PHASE IT IS LEAVING IS A PRECONDITION TOO.
        # `_done_preconditions` reads the ledgers and never `state.phase`, so
        # this returned ok True from F2 — "NYQUIST complete → phase is now F6"
        # asserted from inside an INSPECT, on a run where NYQUIST had never
        # opened. Guarded through the SAME table the three INSPECT-opening
        # tokens use; see `_PHASE_ENTRY_SOURCES`.
        outcome = _nyquist_done_preconditions(fdir, project_root)
        if not outcome["passed"]:
            return _transition_refusal(outcome, "Cannot leave NYQUIST for DONE")
        _update_phase(fdir, "F6")
        # D-218 / FR-001 / GI-006: the artifact is written by the transition
        # that closes the run, on the same terms `_halt_if_capped` writes it at
        # the other terminal transition. Both F6 doors, one helper.
        sealed = seal_run_report(project_root, fdir)
        message = (
            "NYQUIST complete → phase is now F6 (DONE). "
            + sealed_report_sentence(sealed, fdir)
            + " Run archived."
        )
        clear_active_run()
        return {"ok": True, "phase": "F6", **sealed, "message": message}

    elif phase == "done":
        # AC-011 / D-037 — the transition, not just the gate, enforces closure.
        #
        # This branch was an unconditional _update_phase + clear_active_run: it
        # read no verdicts, no open defects and no escalated classes, so the
        # careful checks in foundry_gate("done") were advisory and a run
        # reached F6 with six open escalated-class defects and zero verdicts by
        # simply not calling the gate. AC-011 constrains the RUN ("the run
        # cannot reach DONE"), which is this call.
        #
        # It consults _done_preconditions — the SAME evaluation foundry_gate
        # runs, exactly its checks and no others. A precondition the gate does
        # not enforce must not be invented here: the transition and the gate
        # disagreeing about what "done" means is the failure this is fixing.
        #
        # D-164 — EXCEPT THE ONE THING _done_preconditions CANNOT SEE.
        # Its checklist reads the ledgers; ST-010 also constrains the FROM-state
        # ("F5.5 or F5 complete"), and this branch read `state.phase` never.
        # Driven: a run at F4 with `temper` and `nyquist` both set, every
        # requirement VERIFIED and no open defects, returned ok True and phase
        # F6 — out of ASSAY, skipping both post-verification phases the run was
        # started with. The guard is the same table the INSPECT-opening tokens
        # use, and it is a SOURCE-PHASE check, not a second closure check: what
        # "done" means is still `_done_preconditions` and only that.
        outcome = _done_preconditions(fdir, project_root)
        if not outcome["passed"]:
            return _transition_refusal(outcome, "Cannot mark the run DONE")
        _update_phase(fdir, "F6")
        # D-218 / FR-001 / GI-006: same helper, same terms, the other door.
        sealed = seal_run_report(project_root, fdir)
        message = (
            "Phase is now F6 (DONE). "
            + sealed_report_sentence(sealed, fdir)
            + " Run archived. Start a new run with foundry_init."
        )
        # Clear the active run — session is done with this run
        clear_active_run()
        return {"ok": True, "phase": "F6", **sealed, "message": message}

    elif phase == "halt":
        # fallout FR-018 / FR-046 / FR-064 / GI-034 / CT-004 / ST-001 / AC-025 —
        # THE RUN ENDS ON A RULING, THROUGH THE SEAL THE CAP ALREADY USES.
        #
        # `halt` is a FULL member of PHASE_TOKENS, not a special case with
        # inline refusals: it has a preconditions routine like every other
        # token, a gate token that reports that routine as data (CT-021), and a
        # row in `GATE_TO_TRANSITION`. The invariant test walks it beside every
        # other member, which is what "full member" has to mean to be worth
        # anything.
        #
        # HALTED is written by ONE function. `_seal_halted` below is that
        # function, and the cap path reaches it through `_halt_if_capped` — so
        # a run that ends at its cycle cap and a run the lead stops deliberately
        # produce the same state document, the same regenerated report with the
        # lead's own prose carried, and the same HALTED row in `phase_history`.
        # A second writer is how the two endings come to disagree about what
        # HALTED means.
        outcome = _halt_preconditions(fdir, project_root, reason=reason, text=text)
        if not outcome["passed"]:
            return _transition_refusal(outcome, "Cannot halt the run")
        # fallout GI-033 / AC-061 / FR-063 (D-080) — THE SEAM IS ONE-WAY, AND
        # THE PHASE WRITER TRAVELS ALONG IT.
        #
        # `halt.py` is LIFECYCLE by GI-033's own naming and reached through one
        # seam from here, "and nothing flows back". It reached back: two lazy
        # imports inside `_seal_halted` pulled `gates._blocking_defects` and
        # `transitions._update_phase` into it, so the seam the invariant calls
        # one-way was bidirectional in fact and a lifecycle module could write a
        # phase and read the defect ledger through the verifier set. The defect
        # read now comes off the leaf (`foundry_state.blocking_defects`); the
        # WRITER is passed down the seam it is dispatched through, which is what
        # makes the direction a property of the code rather than of a comment.
        return _seal_halted(
            fdir,
            project_root,
            reason=outcome["halt_reason"],
            text=outcome["halt_text"],
            token="halt",
            update_phase=_update_phase,
        )

    else:
        return {"error": f"Invalid phase: {phase}. Valid: {', '.join(PHASE_TOKENS)}"}


# --------------------------------------------------------------------------- #
# fallout GI-033 / AC-061 / FR-063 (D-021 / D-035) — MOVED HERE FROM
# `orchestration/streams.py`, WHOSE ONLY CALLER OF IT WAS THIS MODULE.
#
# This module is in the VERIFIER set and `streams.py` is not, so
# `from ...streams import _clear_stream_completion_markers` was a
# verifier-to-lifecycle edge the guard's allowlist excused rather than closed.
# The clear is not a stream fact: it is what an INSPECT-OPENING TRANSITION does
# to the completion state of the INSPECT that is ending (D-221), and the four
# branches that do it are all below in this file. The roster it walks comes from
# `vocab.STREAM_WIRE_IDS` — the same closed vocabulary `streams.VALID_STREAMS`
# aliases, so this is one spelling reaching the vocabulary directly, not a
# second list.
# --------------------------------------------------------------------------- #

def _clear_stream_completion_markers(fdir: Path) -> list[str]:
    """Unlink every completion marker the INSPECT that is ending left behind.

    Returns the markers that actually existed, so a caller can say what it
    cleared. Derived from the canonical stream vocabulary rather than listed,
    for the reason stated above this function: the marker-clear lists were two
    of the six hand-typed copies of that vocabulary, and a stream added to
    `vocab.py` has to be cleared the day it is added or its marker goes stale
    across a boundary.

    D-219 — ONE SPELLING, BECAUSE THE THIRD DOOR NEEDED IT AND DID NOT HAVE IT.
    -------------------------------------------------------------------------
    This was two byte-identical loops inside `foundry_mark_phase_complete`, in
    `grind_start` and `assay_fail`, each carrying the comment "completion state
    must stay honest". The `temper` branch — which opens TEMPER's INSPECT, and
    records the five-stream FULL roster to prove it (GI-009: "One rule, two
    doors") — carried only the width half of that rule. Driven through the real
    handlers: a run at F4 with `.trace-complete`, `.prove-complete`,
    `.test-complete`, `.research_audit-complete` and `.test01-complete` all
    present from the F2 INSPECT, `foundry_mark_phase_complete('temper')`
    returned ok with mode FULL, rule first_of_phase and the five-stream roster;
    all five markers were still on disk afterwards; `_check_streams_complete`
    answered `complete True, missing ''` for F5 and `_format_status_display`
    drew `[✓]prove [✓]research_audit [✓]test [✓]test01 [✓]trace` — five streams
    reported complete for a TEMPER INSPECT in which none of them ran. It
    compounded through `_coverage_shortfall`, which is consulted only for a
    stream that is NOT missing and reads the current cycle's roll-up: in F5
    that is still the F2 cycle's numbers, so the earlier INSPECT's coverage met
    the thresholds too. Clearing the markers closes both halves — a missing
    stream is never coverage-checked.

    D-221 — EVERY DOOR THAT OPENS AN INSPECT CALLS THIS, UNCONDITIONALLY.
    ---------------------------------------------------------------------
    This paragraph used to argue the opposite for two of the four doors, and
    the argument is what the next defect was made of. It read: "`cast` enters
    F2 from F1, which is entered by `start_cast` before any stream can have
    reported; `inspect_start` enters F2 from F3, and F3 is entered ONLY by the
    two doors below, both of which clear on the way in ... a call here would
    have no reachable state behind it." Both halves are false at the doors
    themselves. `inspect_start` admits **F2** as a source — the F2->F2 widening
    re-open, which advances the counter, records FULL / final_gate with the
    five-stream roster and sweeps the whole corpus. `start_cast` carries no
    entry-source precondition at all (it is `_update_phase(fdir, "F1")` under
    the halted guard alone), so F1 is reachable from a phase that has markers.

    Driven at the widening door: a DELTA cycle 2 with `.trace-complete`,
    `.prove-complete` and `.test-complete` recorded at 40/40, then a second
    `inspect_start` returning ok at cycle 3 with mode FULL, rule final_gate and
    the five-stream roster — with all three markers still on disk.
    `_check_streams_complete` answered missing "research_audit test01" only, so
    trace, prove and test were COMPLETE for a FULL INSPECT in which none of
    them ran; `_rollup_totals` had nothing for cycle 3, so `_coverage_shortfall`
    fell back to `_marker_counts` on the stale cycle-2 marker and cleared the
    0.95 threshold too. Driven at the `cast` door on the same terms: F2 with
    five markers -> `start_cast` -> `cast` -> ok, FULL, five-stream roster,
    markers untouched, four of five reported complete.

    So the call is the guarantee and the reachability argument is retired. A
    door standing on genuinely cleared state gets an empty return, and that
    empty return is the PROOF there was nothing stale — not a reason to omit
    the call and re-derive the proof in prose. `test_inspect_mode.py`'s
    `test_every_inspect_opening_door_clears_the_previous_inspects_completion_state`
    derives the obligation from this module's own AST: any branch of
    `_phase_transition` that calls `_decide_inspect_mode` must call this too,
    so a fifth door inherits the rule the day it is written.
    """
    markers = [_stream_marker(stream) for stream in sorted(STREAM_WIRE_IDS)] + [
        INSPECT_CLEAN_MARKER,
        TASKS_GENERATED_MARKER,
    ]
    cleared: list[str] = []
    for marker in markers:
        path = fdir / marker
        if path.exists():
            cleared.append(marker)
        path.unlink(missing_ok=True)
    return cleared

