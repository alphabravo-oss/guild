"""Foundry-Gate: the ranked refusal ladder, and nothing else.

Survey blocks E, G, H and I. VERIFIER-SET module. The gate composes NOTHING:
it looks its token up in `GATE_TO_TRANSITION`, calls the transition's own
preconditions routine, and absorbs the ranked refusals that come back.
"""
from __future__ import annotations

import json
from pathlib import Path

from foundry_mcp.schemas.vocab import (
    BLOCKING_TIERS,
    DEFECT_TIERS,
    DELTA_CONDITIONAL_STREAMS,
    FULL_ROSTER_STREAMS,
    INSPECT_MODES,
    DEFECT_TYPES,
    REPORT_JSON_FILENAME,
    REPORT_MD_FILENAME,
    REQUIREMENT_ID_RE,
    RUN_PHASE_HALTED,
    STRUCTURAL_PASS_BUDGET,
    TIER_UNKNOWN,
    defect_tier,
    halt_reason,
)
from foundry_mcp.tools.artifacts import (
    count_spec_requirements,
    report_document_status,
    GATE_PASSED_MARKER,
    NEXT_ACTION_CALLED_MARKER,
    _artifact_guard,
    _load_json,
    _spec_requirement_ids,
    _stream_marker,
)
from foundry_mcp.tools.foundry_state import (
    active_teams,
    check_streams_complete,
    get_run_dir,
    halted_state,
    now_iso,
    persisted_max_cycles,
)
from pathlib import Path
from foundry_mcp.tools.orchestration.escalation import (
    ESCALATION_FILENAME,
    _escalated_classes,
    _escalation_exit_distances,
    _persisted_escalations,
)
from foundry_mcp.tools.orchestration.width import (
    _sight_required,
)

# fallout GI-033 / AC-061 / FR-063 (D-080) — the width refusal is the LEAF's.
# It is read from both layers at once (this module and `transitions.py` are
# verifier, `streams.py` is lifecycle), and GI-033 makes those two mutually
# unreachable, so it can live nowhere else. Bound under the name this package's
# call sites and prose already use.
from foundry_mcp.tools.foundry_state import (
    unrecorded_width_problem as _unrecorded_width_problem,
)
from foundry_mcp.tools.orchestration.evidence_boundary import (
    EVIDENCE_STRIPPED_TOKEN,
    _terminal_evidence_refusal,
    _terminal_evidence_state,
)






# --------------------------------------------------------------------------- #
# Server-owned cycle counter (FR-005 / ST-001 / AC-008).
#
# Before this, ``state.json["cycle"]`` was written once as 0 by foundry_init and
# never incremented by any code path, so every cycle number in the data model
# was an integer the LEAD asserted as a tool argument. grand-vulture's state.json
# reads "cycle": 0 while its defects.json spans caller-asserted cycles 0-17.
#
# The counter now advances as an effect of handling the F3 GRIND -> F2 INSPECT
# boundary in ``foundry_mark_phase_complete`` (the ``inspect_start`` token). It
# is the key for the per-cycle stream roll-up (FR-014) and for the per-class
# consecutive-cycle escalation count (FR-006), both of which are meaningless
# against a caller-asserted number. Tools that used to trust a caller-supplied
# ``cycle`` for persistence now stamp this value instead.
# --------------------------------------------------------------------------- #




# Single compiled source of truth for the requirement-ID grammar. Used by
# BOTH the requirement count and the requirement-ID list so the P3 verdict
# synthesis writes exactly one row per ID the DONE gate's verdict_coverage
# check counts (analog note 3: do not fork a second regex/path resolver).
# D-150: the requirement-ID families are declared ONCE, in the vocabulary
# module, and read from there. This was a hand-typed literal, one of six copies
# across four modules, and every copy knew the same seven families and not
# OT- or GI- — 15 of this spec's 71 IDs. So the DONE gate's requirement count
# and the verdict-coverage synthesis below it could not see an observable truth
# at all, and no evidence could ever bind to one. NFR-002: the canonical
# pattern is a strict SUPERSET of what this copy matched, so nothing that was
# counted before stops being counted.
_REQ_ID_RE = REQUIREMENT_ID_RE




def _sorted_spec_requirement_ids(project_root: str) -> list[str]:
    """The run's requirement ids, sorted — the leaf's climb, shaped for here.

    fallout GI-033 / FR-063 / AC-061 / AC-011 (concern C-018) — ONE LADDER,
    AND IT IS LEAF MATERIAL.
    ---------------------------------------------------------------------
    This module used to climb the spec itself: resolve the path, read the
    text, run the regex. `tools/foundry_validate.py` climbed it too, so one
    question — "which spec is this run's" — had two answers, free to read
    different files, and the two surfaces that asked it sit in layers GI-033
    forbids from importing each other. Casting 7 put the climb in the leaf
    where both may reach it (`artifacts._spec_requirement_ids`), and this is
    the whole of what is left here: take the ids off the leaf's answer and
    sort them.

    NAMED DIFFERENTLY ON PURPOSE. A wrapper that kept the leaf symbol's own
    name would be a second top-level definition of it, which the package-wide
    single-definition guard refuses and which would make "where is
    `_spec_requirement_ids` defined" have two answers again — the shape this
    change exists to end.

    D-150's ruling is unchanged and now lives one layer down: which FAMILIES
    count is `schemas.vocab`'s declaration, not a literal here.
    """
    return sorted(_spec_requirement_ids(project_root)[1])




# fallout GI-025 / AC-011 / OT-011 (concern C-062, casting 12) — ONE FACT, ONE
# LADDER, AND THE UNDERSCORE IS WHY NOBODY NOTICED.
#
# A private counter stood here returning
# `len(_sorted_spec_requirement_ids(project_root))` while
# `artifacts.count_spec_requirements` returned
# `len(_spec_requirement_ids(project_root)[1])` — two answers to "how many
# requirement ids does this spec declare", reached through two ladders, agreeing
# only while both ladders return the same set. C-060 row 3 hoisted the count to
# the leaf so `streams.py` could ask it without a lifecycle module reaching the
# verifier set, and this copy was left behind: the leaf's own docstring says the
# count "sat a layer above the ids, in orchestration/gates.py" as though it had
# moved, and it had only been added.
#
# The package-wide single-definition guard could not see it. That guard is keyed
# by NAME, and these two differ by a leading underscore, so a semantic
# duplication wore a spelling the guard reads as two different symbols — which
# is Holmes naming-6's drifting-same-named-primitives hazard with the sign
# flipped. `test_the_spec_requirement_count_is_asked_in_one_place` in
# `tests/orchestration/test_gates.py` states the rule the name hid.
#
# `_sorted_spec_requirement_ids` STAYS: it is a different shape with a real
# second caller (`width.py` needs the sorted list, not the count), and it is
# already the leaf's climb with a sort on top rather than a second climb.




# CLOSED VOCABULARY — the verdict axis (FR-013 / CT-002). A requirement's
# verdict is either VERIFIED or one of the canonical defect types; that is the
# whole set, and it is DERIVED from schemas/vocab.py rather than hand-typed.
# server.py's Foundry-Verdict enum was a hand-typed baseline copy that rejected
# MISPLACED — a verdict agents/assayer.md mandates and commands/start.md routes
# into this very tool, so a verdict the protocol tells an agent to emit was
# unrepresentable on the surface that records it. "VERIFIED" is the one member
# that is not a defect type, which is why it is the one literal here.
# Extend the defect half via schemas/vocab.py, never here.
VERDICT_VALUES = frozenset({"VERIFIED"}) | DEFECT_TYPES








# --------------------------------------------------------------------------- #
# Tier-aware defect reads (CT-008 / FR-006 / FR-051 / GI-001)
#
# ONE helper, consulted by every gate, because the alternative was measured on
# this exact module: `sum(1 for d in ... if d.get("status") == "open")` was
# hand-written at SIX sites (assay, grind, done, inspect_clean, next-action,
# the status display), and D-119 is the shipped instance of two of those copies
# disagreeing. Adding "and what tier is it" to six copies is that defect with a
# second field.
#
# THE TIER READ ITSELF IS NOT DECIDED HERE. `vocab.defect_tier` is total over
# DEFECT_TIER_OR_UNKNOWN and is the one place a missing key, a null, a non-string
# and an unknown string all resolve to TIER_UNKNOWN. FR-051 turns on that
# resolution: an untiered pre-change record blocks exactly like LIVE, and reading
# it as LATENT would silently clear every gate on records nobody ever classified.
# --------------------------------------------------------------------------- #

#: CT-008 — the tiers that BLOCK. LIVE is a reachable failure and blocks as it
#: always did; unknown is a record no stream has classified and is treated
#: identically until one re-files it with a tier (FR-051). LATENT blocks nothing:
#: it stays open, tracked, and named in the F6 backlog (FR-006).





def _open_defects_by_tier(fdir: Path) -> dict[str, list[dict]]:
    """Every OPEN defect in the ledger, bucketed by the tier it READS as.

    Keys are every member of `DEFECT_TIERS` plus TIER_UNKNOWN, always present
    and possibly empty — a caller that has to check whether a bucket exists
    before counting it will eventually forget to, and an absent bucket reads as
    zero blocking defects, which is the direction that fails open.

    fallout GI-014 / AC-011 — THE BUCKETS ARE DERIVED FROM THE VOCABULARY.
    ---------------------------------------------------------------------
    They were three hand-typed keys, and `defect_tier` is total over
    `DEFECT_TIERS` — so the moment `HARDENING` joined that frozenset this
    function raised `KeyError: 'HARDENING'` on any ledger carrying one. Not a
    theoretical reach: `_blocking_defects` calls this, every gate and every
    transition calls that, and the tier exists precisely so streams will file
    into it. A hand-typed copy of a closed vocabulary is the drift `vocab.py`
    was built to end, and this was the copy that had not been repointed.

    Non-dict historical records are skipped, not guessed at: the same tolerance
    `_dict_records` holds for the ledger's writers (D-128).
    """
    buckets: dict[str, list[dict]] = {t: [] for t in sorted(DEFECT_TIERS)}
    buckets[TIER_UNKNOWN] = []
    for d in _load_json(fdir / "defects.json").get("defects", []):
        if not isinstance(d, dict) or d.get("status") != "open":
            continue
        buckets[defect_tier(d)].append(d)
    return buckets




def _active_teams(project_root: str) -> dict:
    """Is any team still holding the tree? The VERIFIER layer's composition.

    fallout GI-033 / AC-061 / FR-063 (D-021 / D-035, concern C-027). Both
    halves of the answer — the registered directories and the live tmux panes —
    are `foundry_state.active_teams`, because `orchestration/teams.py` is
    LIFECYCLE by GI-033's own violation column and this module is a verifier.
    What is composed here is the two things a leaf may not know: where this
    machine keeps its team directories, and the sentence to print.

    NO SENTENCE IS PASSED, DELIBERATELY. `hint_for` is omitted, so the leaf
    reports the two lists and no prose, and every arm below falls back to
    `_TEAMS_DOWN_HINT` — the one spelling of "shut the teammates down" that
    already exists here precisely so the gate arms cannot drift apart. The
    lifecycle side passes `teams._teammate_shutdown_hint`, which is the shape
    the ruling describes: the read is shared, the prose belongs to the doors
    that own it.
    """
    fdir = get_run_dir(project_root)
    if not fdir:
        return {"active": False, "teams": [], "live_panes": []}
    return active_teams(fdir, teams_dir=Path.home() / ".claude" / "teams")




#: The run phases that ARE an INSPECT. `streams.py` spells the lifecycle side's
#: copy (this module is a verifier and may not import it), and
#: `test_both_streams_complete_compositions_answer_the_same_thing` drives both
#: over one run directory so the two cannot drift apart.
_INSPECT_PHASES = ("F2", "F5")


def _streams_complete(project_root: str) -> dict:
    """Have this cycle's required verification streams completed? Verifier side.

    fallout GI-033 / AC-061 / FR-063 (D-021 / D-035, concern C-027). The check
    itself is `foundry_state.check_streams_complete`; `orchestration/streams.py`
    is LIFECYCLE and `transitions.py`, which opens and closes the INSPECT this
    reports on, is a VERIFIER, so the answer had to move to a leaf both could
    reach. What is composed here is the closed-set values and leaf helpers a
    leaf may not import.

    D-117's UNRECORDED-WIDTH ARM IS INJECTED HERE TOO (concern C-040). The
    first version of this composition omitted it on a double-refusal argument:
    `_inspect_start_preconditions` and `_inspect_clean_preconditions` ask
    `_unrecorded_width_problem` themselves and rank it at `_GATE_RANK_WIDTH`,
    so a second refusal at `_GATE_RANK_STREAMS` says nothing new. The refusal
    the operator READS is unchanged — width outranks streams, so the width
    sentence still speaks — but the CHECKLIST is protocol too, and
    `all_streams_complete ok=True` on an INSPECT whose width was never recorded
    is a line a lead reads and believes. A checklist that disagrees with the
    verdict is the shape D-119 is filed under.
    """
    fdir = get_run_dir(project_root)
    if not fdir:
        return {"complete": False, "missing": "all", "required": [], "shortfalls": []}
    return check_streams_complete(
        fdir,
        modes=INSPECT_MODES,
        marker_of=_stream_marker,
        # C-081: `project_root` is already in hand — it is passed to
        # `count_spec_requirements` on the very next line — and without it a
        # directory `key_files` entry reads as "no frontend files".
        sight=_sight_required(fdir, project_root),
        spec_requirement_count=count_spec_requirements(project_root),
        inspect_phases=_INSPECT_PHASES,
        # DERIVED, never a second hand list: the pre-width roster is exactly the
        # FULL roster minus the two streams DELTA makes conditional.
        fallback_streams=[
            s for s in FULL_ROSTER_STREAMS if s not in DELTA_CONDITIONAL_STREAMS
        ],
        # The leaf takes the closed set as an argument — it may not name one —
        # so the vocabulary is bound HERE, at the caller, exactly as the
        # `modes=INSPECT_MODES` above it is.
        unrecorded_width_problem=lambda d: _unrecorded_width_problem(
            d, modes=INSPECT_MODES
        ),
    )




def _blocking_defects(fdir: Path) -> dict:
    """The tier-aware "may this gate pass?" answer, with the refusal prose.

    Returns ``{"blocking": int, "live": [ids], "unknown": [ids],
    "latent": [ids], "reason": str, "hint": str}``. ``reason`` and ``hint`` are
    empty strings when nothing blocks.

    CT-008 requires the refusal to NAME the open LIVE and unknown-tier defects,
    and to tell them apart: they block for different reasons and the operator's
    next move differs. A LIVE defect needs fixing; an unknown-tier defect needs a
    stream to re-file it with a tier, after which it may well stop blocking. One
    undifferentiated count would send a lead hunting for a reproduction that no
    stream ever claimed to have.
    """
    buckets = _open_defects_by_tier(fdir)
    live = [d.get("id", "?") for d in buckets["LIVE"]]
    unknown = [d.get("id", "?") for d in buckets[TIER_UNKNOWN]]
    latent = [d.get("id", "?") for d in buckets["LATENT"]]

    parts = []
    if live:
        parts.append(f"{len(live)} open LIVE defect(s): {', '.join(live)}")
    if unknown:
        parts.append(
            f"{len(unknown)} open defect(s) with no tier, which block like LIVE "
            f"until a stream re-files them: {', '.join(unknown)}"
        )

    hint = ""
    if parts:
        hint = (
            "Fix the LIVE defects in GRIND. "
            if live
            else ""
        ) + (
            # D-077: the hint names BOTH doors, because both now honour it.
            # It used to say "have the filing stream re-file it" while only
            # `Foundry-Defect` matched an existing untiered record — so a
            # stream that followed the hint through `Foundry-Sync`, which is
            # the door a whole INSPECT stream files through, got a SECOND open
            # record beside the untiered one and the blocking count did not
            # move. Naming one door and meaning one door is what made the hint
            # actionable only by accident.
            "Have the filing stream re-file each untiered defect with tier=LIVE "
            "or tier=LATENT — through Foundry-Defect or Foundry-Sync; either "
            "door matches the open untiered record on (source, type, file, "
            "symbol) and re-tiers it IN PLACE, so it keeps its id and every "
            "citation naming it stays valid. An untiered record is not a "
            "judgement, it is a record made before the tier existed."
            if unknown
            else ""
        )
        if latent:
            hint += (
                f" The {len(latent)} open LATENT defect(s) do not block: they "
                "stay open, tracked, and named in the F6 backlog."
            )

    return {
        # fallout GI-014 / GI-033 (C-027) — DERIVED FROM THE VOCABULARY, so
        # this count and `guidance._open_by_blocking_tier`'s cannot disagree
        # about which tiers block. `len(live) + len(unknown)` was the same
        # answer spelled by hand, and a hand-spelled copy of a closed
        # vocabulary is the drift `vocab.py` exists to end.
        "blocking": sum(len(buckets[tier]) for tier in BLOCKING_TIERS),
        "live": live,
        "unknown": unknown,
        "latent": latent,
        "reason": "; ".join(parts),
        "hint": hint.strip(),
    }




# --------------------------------------------------------------------------- #
# Cross-casting seam: tools/foundry_report.py (C-10) and tools/evidence.py (C-7).
#
# LAZY, for the reason the seam at the bottom of this file spells out at length:
# `foundry_report` and `evidence` both import from this package, and a
# module-top import here closes an import cycle that takes EVERY tool in this
# server down at once. Unguarded, so a wiring break fails loudly at the one call
# site that needs the symbol instead of hiding behind a silent fallback.
#
# These are thin: they exist so the lazy import is written ONCE per symbol
# rather than at each of the four call sites, not to re-decide anything the
# owning module already decided.
# --------------------------------------------------------------------------- #


def _report_status(fdir: Path) -> dict:
    """C-10's report read — {"present", "missing_sections", ...}.

    fallout GI-033 / AC-061 / FR-063 (D-080, concern C-060 row 2) — THE READ
    COMES OFF THE LEAF NOW.

    This lazily imported `foundry_report.report_status`, which is a VERIFIER
    module reaching PRESENTATION and is GI-033's violation column by name. The
    obstacle was never the layering: the read needs the section TITLES, and a
    copy of that table would have the seal writing headings from one and this
    gate checking them against another, so a renamed heading would refuse a run
    for a document the seal had just written correctly. Casting 10 moved
    `REPORT_SECTION_TITLES` into `schemas/vocab.py` beside
    `REPORT_REQUIRED_SECTIONS`, after which the read was leaf material and
    casting 7 took it. The GENERATOR stays in `foundry_report`; only the read
    was ever the crossing.
    """
    return report_document_status(fdir)




# fallout GI-033 / AC-061 / FR-063 (D-080, concern C-060) — THE GENERATOR SEAM
# IS GONE FROM THIS MODULE.
#
# `_generate_report` stood here and `report_seal.py` — LIFECYCLE — imported it,
# so a presentation module reached into the verifier set to borrow a one-line
# wrapper around a presentation function. It lives with its caller now
# (`report_seal.py#_generate_report`), which is a lifecycle-to-lifecycle edge
# and never was a crossing. This module does not generate a report: the DONE
# rung READS one, and that read is the leaf's `report_document_status`.




# --- Phase gate ---


def _done_preconditions(
    fdir: Path, project_root: str, *, token: str = "done"
) -> dict:
    """Evaluate the substantive preconditions for entering F6 DONE.

    Returns ``{"passed": bool, "reason": str, "hint": str, "checklist": [...],
    "refusals": [...]}``.

    WHY THIS IS A FUNCTION (AC-011 / D-037)
    ---------------------------------------
    These checks lived inline in ``foundry_gate``'s "done" branch, and
    ``foundry_mark_phase_complete("done")`` — the call that actually writes F6
    and archives the run — read NONE of them. It was an unconditional
    ``_update_phase`` + ``clear_active_run``. Driven: a run reached DONE with
    six open escalated-class defects and zero verdicts, straight past a gate
    that would have refused it, because consulting the gate was a convention
    the lead was trusted to follow rather than something the transition did.
    AC-011 says "the RUN cannot reach DONE while any escalated-class defect
    remains open" — that is a property of the transition, not of an advisory
    query about the transition.

    So there is one evaluation and two callers. Re-implementing the checks at
    the transition would have satisfied the same test today and drifted from
    the gate by the next cycle, which is the shape of the defect being fixed
    here, not a fix for it.

    Deliberately EXCLUDED, because they are ``foundry_gate``'s call-ordering
    protocol rather than preconditions of being done:

      - the ``.next-action-called`` handshake, which the transition has
        already consumed by the time it asks (re-checking it here would refuse
        every done transition on a token nobody re-armed);
      - the ``.gate-passed`` stamp, which records that an advisory gate ran.

    That exclusion is the whole discipline of this helper: it enforces exactly
    the gate's checks, no more.

    D-190 / D-191 — AND THE ORDERING IS DECLARED, NOT POSITIONAL.
    ------------------------------------------------------------
    This function used to run on the three locals ``passed`` / ``reason`` /
    ``hint`` down a ladder of ten independent ``if`` statements, so the LAST
    failing check owned the pair a terminal prints. That is the generator
    D-186 replaced inside ``foundry_gate``, in the one function that fix
    deliberately did not convert — and both halves of the harm were then driven
    through ``server.call_tool`` at the F6 doors:

      * D-190 (AC-003). A CLEARED class with one open LIVE instance D-900, a
        committed evidence log that no longer reproduces. The evidence rung
        claimed ``reason`` after the blocking-defect arm, so
        ``Foundry-Gate('done')`` answered "1 committed evidence log(s) no
        longer reproduce at HEAD" and named D-900 nowhere — a byte-identical
        refusal to the one the SAME state produces with no defect open at all,
        while ``Foundry-Gate('nyquist')``, whose defect read goes through
        ``_GateLadder``, named it. AC-003's two doors disagreed, and only this
        one was wrong.
      * D-191 (NFR-005 / FR-026 / CT-008). Four checks failed at once — no
        report, one open LIVE defect, a registered team, 2 verdicts of 5 — and
        the rendered remedy was the verdict-coverage arm's "ASSAY must write
        ALL verdicts", which ``Foundry-Gate('assay')`` then refuses at
        ``_GATE_RANK_DEFECTS`` for the very defect this door declined to
        mention. ``refusals`` published exactly ONE entry, the delegated
        verdict, so the three refusals the call had already computed were
        discarded. On that state the ladder now renders the team check, whose
        remedy nothing failing defeats, and publishes all four; the filing's
        parenthetical that "Call Foundry-Report" is the undefeated remedy is
        answered at ``_GATE_RANK_REPORT``, where the reasoning is set out.

    So every check now enters ``_GateLadder`` with its own rank and its own
    remedy, under that class's one rule: THE REFUSAL THAT SPEAKS IS THE ONE
    WHOSE REMEDY IS NOT DEFEATED BY ANOTHER FAILING CHECK ON THE SAME CALL.
    Nothing is discarded — every failing check is published under ``refusals``,
    and both F6 gates and both F6 transitions pass that list through.

    THE HALT STILL HAS THE LAST WORD, and no longer needs to be the last
    writer to get it. It used to be asserted TWICE — once in its own branch and
    again after the evidence rung — precisely because position was the only
    ordering this function had. It is now one ``fail`` at
    ``_GATE_RANK_HALTED``, the lowest rank there is, which says the same thing
    for a stated reason: on a run that has already stopped every other remedy
    is work that cannot be gated, so no other failing check can defeat it and
    it defeats them all.
    """
    # fallout FR-004 / GI-033 -- LAZY SEAM, written once per symbol.
    # `transitions` import(s) this module, so a module-top import here would
    # close a cycle that takes every tool in this server down at once.
    # Unguarded, so a wiring break fails loudly at the one call site that
    # needs the symbol rather than hiding behind a silent fallback.
    from foundry_mcp.tools.orchestration.transitions import (
        _halted_outcome,
        _source_phase_rung,
    )

    # fallout AC-062 / AC-008 (D-088) — THE HALTED RUNG, FIRST AND REACHABLE.
    # Both F6 doors short-circuited above this function, so the rank-0 refusal
    # below could not be provoked through either; and failing the ladder without
    # returning meant a halted run paid for the evidence sweep before being
    # refused. One spelling for every token, and it returns.
    if (halted := _halted_outcome(fdir)) is not None:
        return halted

    checklist: list[dict] = []
    # D-190 / D-191: the three locals this used to carry (`passed`, `reason`,
    # `hint`) were a last-writer-wins ladder. Every failing check now enters
    # the ladder with its own rank and its own remedy; see `_GateLadder` and
    # the `_GATE_RANK_*` block below.
    ladder = _GateLadder()

    # fallout AC-056 / GI-029 — D-164's SOURCE CHECK IS A RUNG NOW, SO BOTH
    # DOORS MAKE IT.
    #
    # It lived in the two F6 transition branches and in NEITHER gate, so
    # `Foundry-Gate('done')` reported the run ready to finish from F2 while
    # `Foundry-Phase('done')` refused it from there — the same shape as the four
    # LIVE defects this release closes, on the one door where the run ends.
    # `token` is what tells the two F6 doors apart: `done` leaves the terminal
    # phase the run's own flags decide, `nyquist_done` leaves F5.5 and nowhere
    # else. Everything BELOW this rung is what "the run may finish" means, and
    # that is one definition for both.
    source = _source_phase_rung(ladder, checklist, fdir, token)

    verdicts = _load_json(fdir / "verdicts.json")
    verdict_list = verdicts.get("requirements", [])
    non_verified = sum(1 for r in verdict_list if r.get("verdict") != "VERIFIED")
    blocking = _blocking_defects(fdir)
    open_count = blocking["blocking"]
    teams_result = _active_teams(project_root)
    spec_count = count_spec_requirements(project_root)

    # fallout GI-006 / CT-004 — DONE REQUIRES THE GENERATED REPORT.
    #
    # Its own named precondition, because it is the one refusal a lead clears
    # with a tool call rather than with work: `Foundry-Report`. Read through
    # casting 5's `report_status`, which answers "present, and which sections
    # are missing" from BOTH documents — `report.json` for "which sections were
    # generated" and REPORT.md for "which sections a reader can still find" —
    # and returns the union.
    #
    # D-050: this block used to claim `report_status` read the JSON "and never
    # from REPORT.md", and argued from that claim that markdown could not be
    # confused by appended prose. The claim outlived the code it described:
    # D-015 moved the read onto both documents after `rm REPORT.md` left the
    # DONE gate passing, and this block kept the case for the behaviour that
    # drive removed. A maintainer reading it would have concluded REPORT.md was
    # unchecked and could have reverted D-015 as redundant, which is the whole
    # cost of stale prose surviving beside new prose.
    #
    # The prose GI-006 explicitly permits the lead to append BELOW the sections
    # still cannot make a present section look absent, but that is now true for
    # a stated mechanism rather than by not looking: `artifacts._markdown_missing_report_sections`
    # matches whole heading lines, so appended paragraphs add headings without
    # removing any.
    #
    # RANKED LAST, AT `_GATE_RANK_REPORT`, which is what this block already
    # argued when the ordering was positional: "STATED FIRST, deliberately ...
    # the least specific check goes at the top", so that every later failing
    # branch overwrote it, because "a run short a report is usually also short
    # something more specific, and a lead should be told about the open defect
    # or the unparsed spec rather than about the paperwork". D-191 read the
    # defeat rule the other way — `Foundry-Report` is refused by no open
    # defect, no registered team and no unparsed spec, so its remedy looked
    # undefeated — and the CALL is indeed never refused. The DOCUMENT is:
    # `generate_report` derives every section from the same ledgers the checks
    # above read, so a report generated beside an open defect, a missing
    # verdict or an unparsed spec is a report the next cleared check
    # invalidates, and the lead must call it again. A remedy that has to be
    # repeated after another failing check is fixed is defeated by that check.
    # Three tests pinned that reading before this conversion and still do —
    # `test_a_more_specific_done_failure_still_names_itself` says it outright,
    # "the reason a lead reads is still the concrete one, not the report".
    # On an otherwise-clean run nothing outranks it and the refusal names the
    # report (OT-025); on a run failing four checks its sentence is published
    # under `refusals` rather than discarded, which is the half of D-191 that
    # was a defect either way.
    report = _report_status(fdir)
    report_json_path = fdir / REPORT_JSON_FILENAME
    if not report["present"]:
        missing_sections = report.get("missing_sections") or []
        if not report_json_path.exists():
            report_reason = (
                f"no generated report — {REPORT_MD_FILENAME} and "
                f"{REPORT_JSON_FILENAME} have not been written"
            )
        elif report.get("problem"):
            report_reason = f"the generated report is unusable — {report['problem']}"
        else:
            report_reason = (
                f"the generated report is missing {len(missing_sections)} "
                f"section(s): " + ", ".join(missing_sections)
            )
        ladder.fail(
            _GATE_RANK_REPORT,
            report_reason,
            "Call Foundry-Report. The report is generated, not written by hand: "
            "you may append prose under your own `## ` heading (the F6 seal "
            "carries it verbatim into `## Lead notes (carried by the seal)`), "
            "you may not omit a section, and DONE is refused until every "
            "section is present.",
        )
    checklist.append({
        "check": (
            "report_generated "
            f"(missing_sections={len(report.get('missing_sections') or [])})"
        ),
        "ok": report["present"],
        "missing_sections": report.get("missing_sections") or [],
    })

    # FR-020 / AC-025 — the auto-VERIFY hole, closed at the gate.
    #
    # Every other check below is vacuously satisfied by a spec that parses
    # to ZERO requirements: no requirement can be non-VERIFIED, and the
    # verdict_coverage check guarded itself with `spec_count > 0` and so
    # skipped. A run whose spec is unresolvable or carries no tagged
    # requirement IDs therefore sailed through DONE having proved nothing.
    #
    # RANKED AT `_GATE_RANK_CONFIG`, which keeps exactly the standing this arm
    # was given when it was written FIRST in a last-writer ladder ("so a more
    # specific failure below still claims `reason`"): how the run was
    # configured, and the last thing a lead is told when anything more concrete
    # is also wrong. It contests nothing it used to contest — with `spec_count`
    # at zero `non_verified` is zero and `verdict_coverage` skips, so the two
    # verdict arms cannot fire beside it.
    if spec_count <= 0:
        ladder.fail(
            _GATE_RANK_CONFIG,
            "The spec parses to ZERO requirement IDs — nothing has been "
            "verified, so DONE is vacuous.",
            "Check that the run's spec resolves (foundry-archive/{run}/spec.md, "
            "else state.json's spec_path) and that it carries tagged "
            "requirement IDs (US-N / FR-N / NFR-N / AC-N / VC-N / IR-N / TR-N).",
        )

    if non_verified > 0:
        ladder.fail(
            _GATE_RANK_VERDICTS,
            f"{non_verified} requirement(s) not VERIFIED — THIN/PARTIAL are defects, not follow-ups",
            "Fix all non-VERIFIED requirements. Every THIN item must be fully implemented.",
        )
    if open_count > 0:
        # FR-026 — THE STALE HINT.
        #
        # This branch set `reason` and left `hint` alone, so whatever the
        # PREVIOUS check happened to write stayed attached to it. A run with
        # open defects and at least one non-VERIFIED requirement was refused
        # with reason "N open defect(s) remain" beside hint "Fix all
        # non-VERIFIED requirements. Every THIN item must be fully
        # implemented." — an instruction for a different check entirely, and
        # the lead's only stated next move. `hint` is now a REQUIRED positional
        # on `_GateLadder.fail`, so an arm that claims `reason` without stating
        # what clears IT is no longer expressible here either.
        ladder.fail(_GATE_RANK_DEFECTS, blocking["reason"], blocking["hint"])

    # AC-011 / ST-003: escalation NEVER waives closure. Swapping N
    # per-instance packets for one structural packet changes the shape of
    # the work, not whether every instance must reach fixed. Stated as its
    # own named check so the guarantee is visible in the checklist rather
    # than merely implied by the open-defect count above.
    #
    # D-002 / FR-006 — TIER-AWARE, LIKE EVERY OTHER BRANCH IN THIS FUNCTION.
    #
    # This branch counted `bucket["open"]` regardless of tier while every
    # sibling above reads `_blocking_defects`, so a class with three open LATENT
    # instances and nothing reproduced refused DONE — against FR-006 verbatim,
    # "INSPECT-clean, ASSAY, TEMPER, NYQUIST and DONE all pass when the only
    # open defects are LATENT". Worse, `foundry_gate("nyquist")` consults
    # `_escalated_classes` not at all, so the two F6 doors returned OPPOSITE
    # verdicts on identical run state — the drift shape
    # `test_both_doors_into_f6_enforce_the_same_preconditions` exists to prevent,
    # one door along.
    #
    # D-034 — LEAD RULING ON THE ST-010 / FR-006 TENSION: BOTH GUARDS APPLY.
    #
    # The D-002 fix keyed this branch on `open_live_defect_ids`, reasoning that
    # a class carrying only LATENT instances has no remaining work of either
    # shape and so should not hold DONE open. FR-006 supports that for the
    # DEFECTS; ST-010 is about the CLASS, and it says "every escalated class
    # CLEARED". Those are different axes, and the previous reconciliation
    # silently dropped the second one: a class could sit at status ESCALATED
    # forever, having had neither structural pass nor two clean cycles, and
    # DONE would pass it because its open instances all happened to be LATENT.
    #
    # The ruling (recorded SPEC_AMBIGUOUS in the run's state.json) is that DONE
    # requires BOTH: (a) no open LIVE or unknown-tier defect — the tier check
    # above, which still blocks a LIVE instance of a CLEARED class — and (b)
    # every class that is still escalating to have left escalation through an
    # arm. A LATENT-only backlog does not by itself clear a class; ST-001's two
    # clean cycles or ST-002's structural budget does, and both are mechanical,
    # so this cannot hold a run open indefinitely.
    #
    # KEYED ON THE PERSISTED STATUS, which is what `_escalated_classes` already
    # reads: it skips any class `escalation.json` records as CLEARED, and it
    # returns nothing for a class whose defects have all closed. So "still
    # returned here" IS "still ESCALATED with open work", and a class that
    # cleared — or emptied — does not appear.
    #
    # This block used to add that reading `escalation.json` directly would
    # DEADLOCK, because both exit arms iterated this same function and so a
    # class that escalated and was then fully fixed reached neither arm. That
    # was true when it was written and D-043 made it false: the arms now walk
    # the persisted entries themselves (see `_persisted_escalations`), so such a
    # class advances a clean cycle at every boundary and CLEARS with
    # `clean_cycles`. The guard nonetheless STAYS on `_escalated_classes`, per
    # the D-034 ruling — that is where the operator's escalation overrides are
    # filtered, and a DONE guard that bypassed them would refuse a run the
    # operator had explicitly de-escalated.
    # D-059 — THE GUARD READS THE PERSISTED STATUS, NOT ONLY THE LEDGER
    # RECURRENCE.
    # ------------------------------------------------------------------
    # ST-010's clause is "every escalated class CLEARED", and this measured
    # "still escalated" solely from `_escalated_classes`, which opens with
    # `if not bucket["open"]: continue` and then `if run_len < ESCALATION_CYCLES:
    # continue`. A class with zero open instances is invisible to both lines
    # WHATEVER escalation.json says, and only the boundary arms ever write
    # CLEARED — needing LIVE_CLEAN_CYCLES_TO_CLEAR crossings, while ONE clean
    # crossing is enough to pass ASSAY/TEMPER/NYQUIST and reach DONE. Driven:
    # class SCAN_GAP persisted `status: ESCALATED` with live_clean_cycles 1 and
    # all three instances fixed -> Foundry-Gate('done') PASSED with checklist
    # "escalated_classes_cleared (still escalated=0)" and Foundry-Phase('done')
    # returned ok, phase F6, while escalation.json on disk still read ESCALATED
    # and the run's OWN report said by_status {CLEARED: 0, ESCALATED: 1} with a
    # null exit reason. The run's two artifacts contradicted each other about
    # whether it had finished.
    #
    # The union is what ST-010 asks for. Overrides stay honoured on both halves:
    # `_persisted_escalations` filters them exactly as `_escalated_classes`
    # does, so a class the operator de-escalated still does not block (D-034).
    # This cannot deadlock — a fully fixed class draws zero LIVE instances by
    # construction, so the clean arm clears it within two boundaries.
    escalated_open = _escalated_classes(fdir, project_root)
    persisted_classes = _load_json(fdir / ESCALATION_FILENAME).get("classes", {})
    if not isinstance(persisted_classes, dict):
        persisted_classes = {}
    persisted_escalated = _persisted_escalations(
        fdir, project_root, persisted_classes
    )
    still_escalated = sorted(set(escalated_open) | set(persisted_escalated))
    latent_only_classes = sorted(
        key for key, info in escalated_open.items()
        if not info.get("open_live_defect_ids")
    )
    if still_escalated:
        blocking_ids = sorted(
            did
            for info in escalated_open.values()
            for did in info.get("open_live_defect_ids", [])
        )
        ladder.fail(
            # RANKED ABOVE THE DEFECT LEDGER, and this is the one place at this
            # door where the two contest each other. "Fix the LIVE defects in
            # GRIND" does NOT clear a class — ST-010 needs an arm to fire — so
            # a lead who follows the defect remedy and returns is refused again
            # by THIS check: that remedy is defeated. The escalation remedy is
            # defeated by nothing here, and it already names the blocking LIVE
            # instances below, so following it does the defect work too. It is
            # also the only remedy at this door measured in CYCLES rather than
            # in calls, and D-129 is what learning that at F5.5 costs: every
            # remaining boundary becomes a full post-verification loop instead
            # of one crossing from F2.
            _GATE_RANK_ESCALATION,
            f"{len(still_escalated)} defect class(es) are still ESCALATED: "
            f"{', '.join(still_escalated)}",
            "ST-010: every escalated class must be CLEARED before DONE. A "
            "class leaves escalation mechanically — two consecutive INSPECT "
            f"cycles drawing zero LIVE instances (ST-001), or "
            f"{STRUCTURAL_PASS_BUDGET} structural packets dispatched (ST-002) "
            "— and a LATENT-only backlog does not by itself clear one. A "
            "structural fix must still close every LIVE defect of the class; "
            "escalation is not a waiver."
            + (f" Blocking LIVE instances: {', '.join(blocking_ids)}."
               if blocking_ids else "")
            # D-111: the arm and the distance, PER CLASS. This hint used to
            # offer one undifferentiated sentence — "cross the GRIND->INSPECT
            # boundary so the clean-cycle arm counts, or spend the structural
            # budget" — and a lead that followed it six times walked the counter
            # from 4 to 9 with `escalation.json` still absent, because for a
            # class with no persisted record BOTH named routes were no-ops. The
            # record is now written at the boundary (see the `inspect_start`
            # branch), so the distance to each arm is a number this can state.
            + _escalation_exit_distances(fdir, project_root, still_escalated),
        )
    checklist.append({
        "check": (
            f"escalated_classes_cleared (still escalated={len(still_escalated)}, "
            f"latent_only={len(latent_only_classes)})"
        ),
        "ok": not still_escalated,
        "classes": still_escalated,
        # Named apart so a reader can tell WHY a class is still escalating: it
        # is recurring in the ledger, or escalation.json has never recorded an
        # exit for it, or both.
        "recurring_classes": sorted(escalated_open),
        "persisted_escalated_classes": persisted_escalated,
        # Named separately even though both block now, because "escalated and
        # carrying only a LATENT backlog" and "escalated with LIVE work open"
        # clear by different routes: the first waits for an arm, the second
        # needs the defects fixed first.
        "latent_only_classes": latent_only_classes,
    })

    if teams_result["active"]:
        # FR-026, same stale-hint repair as the open-defect branch above, and
        # the same rank the three `foundry_gate` branches give it: a registered
        # team holds the tree, every remedy that spawns an agent is refused
        # until it is down, and shutting it down is refused by nothing.
        ladder.fail(
            _GATE_RANK_TEAMS,
            f"Active teams: {', '.join(teams_result['teams'])}",
            teams_result.get("hint") or _TEAMS_DOWN_HINT,
        )

    verdict_count = len(verdict_list)
    verdicts_complete = True
    if spec_count > 0 and verdict_count < spec_count:
        skipped = spec_count - verdict_count
        ladder.fail(
            _GATE_RANK_VERDICTS,
            f"Only {verdict_count} verdicts but spec has {spec_count} requirements. {skipped} skipped.",
            "ASSAY must write ALL verdicts to verdicts.json — including THIN/PARTIAL, not just VERIFIED.",
        )
        verdicts_complete = False

    # fallout ST-001 / CT-004 / CT-013 / D-081 — "HALTED IS NOT DONE", AS A PRECONDITION
    # OF BEING DONE.
    #
    # `_GATE_RANK_HALTED` is the lowest rank there is, which is how this keeps
    # the last word now that the ordering is declared rather than positional.
    # It used to be asserted TWICE — here, and again after the evidence rung —
    # because source position was the only ordering this function had, and the
    # rung below it would otherwise have overwritten `reason`. One `fail` says
    # the same thing for a stated reason: on a halted run every other failure
    # is a detail of a run that has already stopped, so no other failing
    # check's remedy can defeat this one and this one defeats them all.
    # Telling a lead to fix three open LIVE defects, or to re-capture an
    # evidence log, on a run that ended two cycles ago sends them to do work
    # that cannot be gated.
    #
    # Its own named branch here, and not merely covered by the blanket guards at
    # the gate and the transition, because THIS is the shared evaluation of "may
    # this run finish" that both F6 doors consult (D-037 / D-043 / D-044). A
    # precondition of finishing that only two of its three readers can see is
    # the drift shape this helper exists to prevent, and the checklist a lead
    # reads is produced here.
    # fallout AC-062 (D-088) — asked THROUGH the shared rung, and short-
    # circuiting. It used to fail the ladder here and carry on, which meant a
    # halted run still ran the evidence sweep below before refusing; and both
    # public doors refused above this function anyway, so the rung never spoke
    # through either of them.
    checklist.append({"check": "run_not_halted", "ok": True})

    checklist.append({
        "check": f"spec_requirements_parsed (count={spec_count})",
        "ok": spec_count > 0,
    })
    checklist.append({"check": f"all_verified (non_verified={non_verified})", "ok": non_verified == 0})
    checklist.append({
        # CT-008: the count is the BLOCKING count, and the LATENT backlog rides
        # alongside it so a passing checklist still says what is being carried.
        "check": (
            f"zero_blocking_defects (live={len(blocking['live'])} "
            f"unknown_tier={len(blocking['unknown'])} latent_backlog={len(blocking['latent'])})"
        ),
        "ok": open_count == 0,
        "live": blocking["live"],
        "unknown_tier": blocking["unknown"],
        "latent_backlog": blocking["latent"],
    })
    checklist.append({"check": "no_active_teams", "ok": not teams_result["active"]})
    checklist.append({"check": f"verdict_coverage ({verdict_count}/{spec_count})", "ok": verdicts_complete})

    # GI-002 / FR-042 / FR-009 — THE CORPUS RE-EXECUTES BEFORE DONE. D-133.
    #
    # GI-002 (Locked) sweeps the whole corpus "before ASSAY/NYQUIST/DONE" and
    # this checklist had no evidence rung at all: report_generated,
    # escalated_classes_cleared, run_not_halted, spec_requirements_parsed,
    # all_verified, zero_blocking_defects, no_active_teams, verdict_coverage.
    # So a fix landing in F5 or F5.5 — which is exactly what the lead lane
    # US-005 opens is for — reached DONE with the committed evidence never
    # re-executed over it, and `fixes_after_decision`, the stamp that catches
    # the same shape one phase earlier, is read only by `inspect_clean`.
    #
    # STATED HERE rather than only in the two F6 transitions, because this
    # function IS the shared evaluation both F6 doors and both gates consult
    # (D-037 / D-043 / D-044). An evidence rung present in the transition and
    # absent from the gate would be the drift this helper exists to prevent:
    # the gate would pass a run the transition then refuses.
    #
    # The sweep is memoised on HEAD (see `_terminal_evidence_sweep`), so the
    # gate-then-transition pair pays for one re-execution rather than two, and
    # any commit between them invalidates the memo and re-runs it.
    #
    # RANKED AT `_GATE_RANK_EVIDENCE`, below the defect and verdict ledgers and
    # above nothing else at this door: re-capturing a log is defeated by every
    # open defect and every missing verdict, because the corpus moves again
    # when those are fixed and the log has to be captured a second time. That
    # is the same judgement the retired ladder made by putting this branch
    # LAST — D-190 is what it cost to make it by source position, where the
    # blocking-defect arm above simply lost its `reason` to this one and the
    # open LIVE defect the door was refusing on went unnamed.
    # D-149 — AND A SWEEP THAT COVERED NOTHING IS NOT A SWEEP THAT PASSED.
    #
    # `commands/start.md` mandates `git rm -r evidence/` as an F6 step, so the
    # tree this rung asks about may have no corpus in it at all. Driven at
    # cycle 8: the same non-reproducing log refused DONE before that strip and
    # passed after it, because `select_sweep_scope` reads the evidence
    # directory in the TREE and a deleted directory yields no logs — zero
    # mismatches, ok True, over nothing. The rung reported only the mismatch
    # count, so a vacuous sweep was indistinguishable from a clean whole-corpus
    # one in the very checklist the lead reads.
    #
    # Three states, and only three (the same three start.md documents):
    # corpus present, swept now; corpus absent with a recorded pre-strip pass,
    # which is what the mandated order EARNS by running Gate(done) before the
    # `git rm`; corpus absent with no such pass, which is refused. A run that
    # committed no evidence at all still passes — it has no corpus history to
    # have stripped, which is what `_evidence_corpus_existed` separates.
    # D-159: the three states are derived by `_terminal_evidence_state` and by
    # nothing else, so the NYQUIST entry — the third crossing GI-002 names —
    # refuses on exactly this evaluation rather than on `ok` alone.
    evidence_state = _terminal_evidence_state(fdir, project_root)
    evidence = evidence_state["evidence"]
    sweep_record = evidence_state["record"]
    logs_reexecuted = evidence_state["logs_reexecuted"]
    corpus_size = evidence_state["corpus_size"]
    prior_pass = evidence_state["prior_pass"]
    stripped = evidence_state["stripped"]
    evidence_ok = evidence_state["ok"]
    if not evidence_ok:
        refusal = _terminal_evidence_refusal(evidence_state, "mark the run DONE")
        ladder.fail(
            _GATE_RANK_EVIDENCE,
            (refusal or {})["error"].replace("Cannot mark the run DONE — ", ""),
            (refusal or {})["hint"],
        )
    checklist.append({
        # D-149: the number of logs, beside the mismatch count. A vacuous sweep
        # is visibly logs=0; the two numbers used to be one number.
        "check": (
            f"evidence_reproduces_at_head (logs={logs_reexecuted}, "
            f"mismatches={len(evidence.get('mismatches') or [])})"
        ),
        "ok": evidence_ok,
        "token": EVIDENCE_STRIPPED_TOKEN if stripped else "",
        "logs_reexecuted": logs_reexecuted,
        "corpus_size": corpus_size,
        "scope": sweep_record.get("scope", ""),
        "mismatches": [
            m.get("log", "?") for m in (evidence.get("mismatches") or [])
        ],
        "swept_head": evidence.get("head", ""),
        "cached": bool(evidence.get("cached")),
        # Present only when this door passed on the RECORDED pass rather than
        # on a sweep it just took, so a reader can never mistake one for the
        # other.
        "pre_strip_pass": prior_pass if corpus_size == 0 else None,
    })

    outcome = {
        "passed": ladder.passed,
        "checklist": checklist,
        # D-191: every failing check's own sentence, ranked, so the four the
        # F6 doors used to compute and discard are PUBLISHED. Both gates and
        # both transitions pass this through.
        "refusals": ladder.refusals(),
        # fallout CT-020 (D-084) — THE SOURCE RUNG'S FACTS, PUBLISHED.
        #
        # `source` was assigned above and never read — a dead local — so the two
        # F6 doors alone published no `accepted_from` and no `source_phase`,
        # while every other token's routine spreads them through
        # `_preconditions_outcome`. `transitions.py#_transition_refusal`'s
        # docstring says a caller reading one of those keys "would otherwise
        # have lost it to the reshaping", and at these two doors it had.
        **source,
    }
    # Written through the RESULT rather than through two locals, exactly as
    # `foundry_gate` renders its own ladder: a function that never names
    # `reason` or `hint` as locals has nothing a later arm could overwrite,
    # which is the property `test_the_done_evaluation_ranks_every_arm_through_
    # named_constants` derives from this function's own AST.
    outcome["reason"], outcome["hint"] = ladder.outcome()
    return outcome




# --------------------------------------------------------------------------- #
# D-186 / D-183 / NFR-005 / FR-011 / AC-016 — ONE REFUSAL SPEAKS, AND IT IS THE
# ONE THAT HAS TO BE ACTED ON FIRST.
#
# `foundry_gate`'s branches were ladders of independent checks, each writing the
# function-locals `passed`, `reason` and `hint`, so the LAST failing check owned
# the two strings a terminal prints. That is a generator, not a bug in one rung:
# D-183 fixed the `.inspect-clean` rung by guarding it, and D-186 was the rung
# BELOW it — `no_active_teams` — doing the same thing one cycle later, and doing
# it while assigning `reason` and NO hint at all.
#
# Driven, before this change, through `server.call_tool` on synthetic runs:
#   * FULL/final_gate, one fixed defect, a registered team -> reason "Active
#     teams: foundry-cast" beside hint "Re-run the INSPECT this GRIND owes, then
#     close it with Foundry-Phase(phase='inspect_clean')". The hint answered a
#     different check than the reason, and FOLLOWING it was harmful: on the same
#     run `Foundry-Phase(phase='inspect_clean')` returned ok True and set F4, so
#     a lead reading the refusal crossed into ASSAY with the team still
#     registered — defeating the very check that refused.
#   * DELTA/delta, one fixed defect, a registered team -> reason "Active teams:
#     foundry-cast", with the FR-011 / AC-016 width refusal the arm above had
#     already computed discarded. D-183's displacement, one rung lower.
#   * teams-active as the only failure -> reason "Active teams: foundry-cast"
#     with hint "" — a refusal with no remedy at all.
#
# So the ordering is DECLARED rather than left to source position, and it is
# declared by one rule: THE REFUSAL THAT SPEAKS IS THE ONE WHOSE REMEDY IS NOT
# DEFEATED BY ANOTHER FAILING CHECK ON THE SAME CALL. A remedy the server would
# reject, or that another open check makes impossible, is D-011's shape — a
# refusal with no usable next move — and that is what ranking prevents. Ties
# break on declaration order, so a branch's own source order still decides
# between two checks of equal standing.
#
# `hint` is a REQUIRED positional on `fail`, so an arm cannot claim `reason`
# without stating what clears it; `test_every_gate_refusal_states_a_remedy`
# derives that obligation from this module's own AST rather than from a list.
#
# D-190 / D-191 — AND IT IS APPLIED TO `_done_preconditions` TOO.
#
# This block used to end "NOT applied to `_done_preconditions`, deliberately",
# on three grounds: that function documented an explicit ordering discipline of
# its own ("LAST of the reason-claiming branches ... means it WINS"), every one
# of its arms already carried a hint, and no instance of this class had been
# driven there. The third ground is what the other two rested on, and PROVE
# removed it by driving two — at the F6 doors, through `server.call_tool`:
#
#   * D-190. A CLEARED class with one open LIVE instance and a committed
#     evidence log that no longer reproduces. `Foundry-Gate('done')` answered
#     with the evidence rung — which claims `reason` after the blocking-defect
#     arm — and named the defect NOWHERE, returning a refusal byte-identical to
#     the one the same state produces with no defect open at all, while
#     `Foundry-Gate('nyquist')` named it. AC-003's two doors disagreed.
#   * D-191. Four checks failing at once rendered the verdict-coverage remedy,
#     which `Foundry-Gate('assay')` then refuses at `_GATE_RANK_DEFECTS` for
#     the very defect this door declined to mention; and `refusals` carried
#     exactly ONE entry — the single delegated verdict — so the three other
#     computed refusals were discarded at the two doors D-186 had not reached.
#
# "Every arm carries a hint" was never the guarantee: the guarantee is that the
# hint a lead READS belongs to the check that speaks, and a positional ladder
# cannot make that true. So the discipline is the same one, in the same
# mechanism, with the same rule.
# --------------------------------------------------------------------------- #

#: Remedy precedence at a gate. LOWER WINS. Each rank names the KIND of thing
#: that is wrong, and the ordering is the order in which a lead can actually act:
#: a remedy at rank N is not defeated by any failing check at a rank above it.
#:
#: D-190/D-191 added the four `_done_preconditions` needs and retired
#: `_GATE_RANK_DELEGATED` (0, "a verdict another evaluation already ordered"):
#: that rank existed because the delegated evaluation had no ranks of its own to
#: offer, and it now offers one per CHECK. `_GateLadder.absorb` re-enters those.
#: fallout GI-031 / AC-056 — the key_files ceiling, named once. The gate's
#: refusal quotes it and the decompose plan is written against it; a literal 8
#: in a sentence beside a literal 8 in a comparison is the drift this module
#: declares constants to end.
CASTING_KEY_FILE_CAP = 8



_GATE_RANK_HALTED = 0      # the run has already stopped: no other remedy can be


_GATE_RANK_ESCALATION = 5  # a class still ESCALATED — the only remedy measured


_GATE_RANK_WIDTH = 10      # the recorded INSPECT width — `inspect_start` clears


_GATE_RANK_TEAMS = 20      # a registered team still holds the tree; every


_GATE_RANK_CONFLICT = 30   # two castings own one file — teammates about to


_GATE_RANK_DEFECTS = 40    # the defect ledger: what GRIND is for


_GATE_RANK_VERDICTS = 50   # the verdict ledger: what ASSAY is for


_GATE_RANK_EVIDENCE = 55   # a committed evidence log that no longer reproduces:


_GATE_RANK_STREAMS = 60    # a required stream has not reported


_GATE_RANK_MARKER = 70     # a phase marker the previous transition writes


_GATE_RANK_CONFIG = 80     # how the run was configured or sized


_GATE_RANK_SOURCE = 85     # fallout GI-029 — the run is in a phase this token is


_GATE_RANK_REPORT = 90     # the generated report: DERIVED from every ledger



#: The one spelling of "shut the teammates down", so the three gate arms that
#: fall back to it cannot drift apart again (D-186: the assay copy had no
#: fallback at all, and the other two spelled theirs differently).
_TEAMS_DOWN_HINT = (
    "Shut down all teammates, call TeamDelete, then Foundry-Team-Down"
)




class _GateLadder:
    """The failing checks of one `foundry_gate` branch, ranked by remedy.

    ``fail(rank, reason, hint)`` records one failing check. ``outcome()``
    returns the ``(reason, hint)`` of the LOWEST-ranked failure — ties broken by
    declaration order — and ``refusals()`` returns every failure in that same
    order, so a computed refusal that loses the one rendered line is still
    PUBLISHED rather than discarded. "The width refusal the arm above computed
    is discarded" is how D-186 names the harm; nothing is discarded now, one
    thing simply speaks.
    """

    def __init__(self) -> None:
        # (rank, declaration order, reason, hint) — the second element is what
        # keeps the sort stable without depending on the sort's own guarantees.
        self._failures: list[tuple[int, int, str, str]] = []

    def fail(self, rank: int, reason: str, hint: str) -> None:
        """Record one failing check. ``hint`` is required, and is what clears
        THIS check — never what clears the one above or below it."""
        self._failures.append(
            (int(rank), len(self._failures), str(reason), str(hint))
        )

    def absorb(self, refusals: list[dict]) -> None:
        """Re-enter a DELEGATED evaluation's failures at their own ranks.

        `_done_preconditions` is one evaluation with four callers, and it ranks
        its own checks (D-190 / D-191). Before that it produced a single opaque
        verdict, so `foundry_gate`'s "done" branch entered it at a rank invented
        for the purpose — `_GATE_RANK_DELEGATED` — and published ONE refusals
        entry for a call that had computed four. Absorbing the ranked list
        instead keeps the delegated ordering intact (the ranks and the order are
        the ones that evaluation declared) AND publishes every one of them, so
        "nothing is discarded, one thing speaks" holds at the F6 doors on the
        same terms as everywhere else.

        A malformed entry is not silently dropped: `rank`, `reason` and `hint`
        are read positionally through the same `fail`, so an entry missing one
        raises here rather than losing a refusal downstream.
        """
        for refusal in refusals:
            self.fail(refusal["rank"], refusal["reason"], refusal["hint"])

    @property
    def passed(self) -> bool:
        return not self._failures

    def _ordered(self) -> list[tuple[int, int, str, str]]:
        return sorted(self._failures, key=lambda item: (item[0], item[1]))

    def outcome(self) -> tuple[str, str]:
        """``(reason, hint)`` for the refusal that speaks, or two empty
        strings when nothing failed."""
        ordered = self._ordered()
        if not ordered:
            return "", ""
        return ordered[0][2], ordered[0][3]

    def refusals(self) -> list[dict]:
        """Every failing check, highest-standing first."""
        return [
            {"rank": rank, "reason": reason, "hint": hint}
            for rank, _seq, reason, hint in self._ordered()
        ]




# --------------------------------------------------------------------------- #
# fallout FR-007 / FR-061 / GI-011 / GI-031 / AC-007 / AC-056 / AC-059 —
# ONE PRECONDITIONS ROUTINE PER TRANSITION TOKEN, AND ONE TABLE FROM GATE TO
# TRANSITION.
#
# `_done_preconditions` above was the ONE phase whose composition was shared.
# Every other phase had its checks composed independently in `foundry_gate` and
# again in `_phase_transition`, by two different mechanisms — a ranked ladder on
# one side, an ordered chain of early returns on the other — and the four open
# LIVE defects this run inherited are all one instance of that: a check the gate
# made with no twin at the transition it advertises.
#
#   D-240  the verdict read at `temper`. Gate refused on a non-VERIFIED
#          requirement; the transition entered F5 without looking.
#   D-241  the same read at `nyquist`, a second inline copy on the gate side and
#          again no twin.
#   D-242  `state.nyquist`. The gate refused a run that never asked for F5.5;
#          the transition wrote F5.5 anyway.
#   D-243  the SIGHT url at `cast`. `_check_sight_required` was consulted by the
#          gate alone, so a run with frontend files in scope and no URL crossed
#          into INSPECT with the stream it owes unrunnable.
#
# Patching four rungs would have closed four defects and left the generator
# running. What closes the CLASS is that there is now exactly one function per
# TRANSITION token which builds the ladder, and both doors read it and nothing
# else. A check added to a token is a check both doors make on the day it is
# written, and the invariant test
# tests/orchestration/test_transitions.py#test_a_gate_and_its_transition_refuse_the_same_check
# walks every token against every rung to say so.
#
# WHAT LIVES IN A ROUTINE, AND WHAT DOES NOT. The routine owns the pure,
# read-only checks: teams, streams, blocking defects, verdict coverage,
# `state.nyquist`, the sight url, the recorded width, `fixes_after_decision`,
# the manifest's shape and size, and the SOURCE PHASE. Left outside, in the
# callers, is the call-ordering protocol that is not a precondition of the
# transition at all: `.next-action-called`, `.gate-passed`, and the HALTED guard
# stated once above the branch chain.
#
# THE SOURCE PHASE IS INSIDE, and that is a deliberate departure from Holmes
# `flow-1`, which proposed leaving `_phase_entry_source_problem` in the callers
# as protocol. AC-056 is the tie-break and it is unambiguous: "anything a
# transition must check lives in the shared function". A source check made by
# the transition and not by the gate is the D-240 shape exactly — one door
# refusing what the other admits — and this is the run that exists to end it.
# Putting it in the routine gives every gate a source check it never had, and it
# makes AC-009's AST pin trivially satisfiable: a transition branch reads its
# routine and nothing else.
# --------------------------------------------------------------------------- #

#: fallout AC-059 / ST-014 / CT-020 — the ONLY mapping from a gate token to the
#: transition token(s) it guards. Read by `foundry_gate` and by the invariant
#: test, which asserts every gate token appears here and that every member of
#: `PHASE_TOKENS` is some gate's target, so a token added on either side without
#: its counterpart fails CI rather than shipping ungated.
#:
#: THE ENUMERATION IS THE SPEC'S AND IT IS EXHAUSTIVE. AC-059 names four
#: exceptions — `inspect` to `cast`, `grind` to `grind_start` and `assay_fail`,
#: `assay` to `inspect_clean`, `validate` to `start_cast` — and says every other
#: token maps same-name, halt included. `cast` is not among the four, so it maps
#: to the transition `cast`.
#:
#: This row shipped as `cast` to `start_cast`, on the reasoning that the lead's
#: own guidance said "Foundry-Gate(phase='cast') to validate, then
#: Foundry-Phase(phase='start_cast')". That reasoning inverted the direction of
#: authority: the guidance string is a consumer of this table, not evidence
#: about it, so the fix is to repoint `_ACTION_TO_GATE` at the `validate` gate —
#: which the spec's own row already assigns to `start_cast` — rather than to
#: bend the table to the string. Nothing is lost by it: `validate` evaluates the
#: identical `_start_cast_preconditions`, so the manifest / oversize /
#: file-overlap rungs the CAST wave depends on are still asked, at the one token
#: the spec assigns them to.
GATE_TO_TRANSITION: dict[str, tuple[str, ...]] = {
    "validate": ("start_cast",),
    "cast": ("cast",),
    "inspect": ("cast",),
    "inspect_start": ("inspect_start",),
    "grind": ("grind_start", "assay_fail"),
    "assay": ("inspect_clean",),
    "temper": ("temper",),
    "nyquist": ("nyquist",),
    "nyquist_done": ("nyquist_done",),
    "done": ("done",),
    "halt": ("halt",),
}





def foundry_gate(
    phase: str,
    project_root: str = ".",
    *,
    reason: str = "",
    text: str = "",
) -> dict:
    """Check if preconditions are met to enter a phase.

    fallout FR-007 / FR-061 / AC-056 / AC-059 / CT-013 / CT-020 — THE GATE
    COMPOSES NOTHING.
    ---------------------------------------------------------------------
    Five hundred lines of per-phase branches lived here, each one a ladder of
    reads that `_phase_transition` then made again, differently. This function
    is now: look the gate token up in `GATE_TO_TRANSITION`, call the routine(s)
    the mapped transition token(s) name, absorb their ranked refusals into this
    door's ladder, and extend this door's checklist. It reads no ledger and no
    marker of its own.

    What is left outside is this door's PROTOCOL and nothing else — the
    `.next-action-called` handshake it requires and the `.gate-passed` marker it
    writes on the way out. Neither is a precondition of the transition: the
    first is about the order the lead called things in, and the second is a
    guidance effect. The HALTED guard above them is stated once for the same
    reason `_phase_transition` states it once.

    `reason` and `text` are the halt token's arguments and are passed through to
    `_halt_preconditions`; every other token ignores them. CT-021 is what they
    are for: `Foundry-Gate('halt', reason, text)` reports the three checks the
    halt transition refuses on, as data, without acting on any of them.
    """
    # fallout FR-004 / GI-033 -- LAZY SEAM, written once per symbol.
    # `transitions` import(s) this module, so a module-top import here would
    # close a cycle that takes every tool in this server down at once.
    # Unguarded, so a wiring break fails loudly at the one call site that
    # needs the symbol rather than hiding behind a silent fallback.
    from foundry_mcp.tools.orchestration.transitions import (
        _halted_outcome,
        _token_preconditions,
    )
    fdir = get_run_dir(project_root)
    if not fdir:
        return {"phase": phase, "passed": False, "reason": "No active foundry run", "hint": "Call Foundry-Init first"}

    if not fdir.exists():
        return {"phase": phase, "passed": False, "reason": "foundry directory not found", "hint": "Run foundry_init first"}

    # fallout FR-046 / CT-004 / CT-013 (D-090) — THE HALT TOKEN IS SCOPED OUT OF
    # THIS DOOR'S OWN PROTOCOL, ON BOTH SIDES.
    #
    # FR-046 says `Foundry-Phase('halt', reason, text)` refuses ONLY on
    # `_halt_preconditions`, and CT-013 says this door and that one refuse the
    # identical set — so a check this door makes that the halt transition does
    # not would break the pair. Both the artifact guard and the ordering token
    # are this door's PROTOCOL, not the token's preconditions, and neither is
    # something a lead reaching the halt door can act on: a run whose ledgers
    # will not parse is exactly the run a lead halts with `spec_change_required`
    # or `lead_ruling`, and it was the one run that could not be sealed.
    halt_scoped = phase == "halt"

    if not halt_scoped and (corrupt := _artifact_guard(fdir)):
        return {
            "phase": phase,
            "passed": False,
            "reason": corrupt["error"],
            "hint": corrupt["hint"],
            "corrupt_artifacts": corrupt["corrupt_artifacts"],
        }

    if phase not in GATE_TO_TRANSITION:
        return {"phase": phase, "passed": False, "reason": f"Unknown phase: {phase}",
                "hint": ("Valid phases: " + ", ".join(GATE_TO_TRANSITION))}

    # fallout ST-001 / CT-013 / CT-020 / D-081 — EVERY GATE REFUSES FROM HALTED,
    # NAMING THE HALT.
    # A halted run has no next gate, so no gate may report itself passed;
    # `Foundry-Gate('done')` answering passed True on a HALTED run is what made
    # that a defect.
    #
    # fallout AC-062 / GI-029 (D-088) — BUT THE REFUSAL IS THE ROUTINE'S NOW.
    # This door called `_halted_refusal` here and returned its sentence, which
    # made `_halt_preconditions`' `not_already_halted` rung and
    # `_done_preconditions`' HALTED rung unreachable through it: the routines
    # computed a rank-0 refusal nobody could provoke and this door answered with
    # no `refusals` key at all. Every routine makes the rung now
    # (`_halted_outcome`), so the loop below produces the refusal in the same
    # ranked shape as every other check and the invariant test walks a real
    # rung rather than a short-circuit.
    #
    # What is kept is the ORDERING the guard was here for: "you must call
    # Foundry-Next first" is an instruction to prepare for a call that cannot
    # succeed, so the ordering token is not asked of a run that has stopped.
    if not halt_scoped and _halted_outcome(fdir) is None:
        nac = fdir / NEXT_ACTION_CALLED_MARKER
        if not nac.exists():
            return {
                "phase": phase,
                "passed": False,
                "reason": "Must call Foundry-Next before any gate check",
                "hint": "Call Foundry-Next first — it shows the status display and tells you what to do next.",
                "checklist": [{"check": "next_action_called", "ok": False}],
            }
    # ST-011 / FR-044 / AC-035 / OT-028 — THE GATE NO LONGER CONSUMES THE TOKEN.
    #
    # This read `nac.unlink(missing_ok=True)`, so the documented sequence
    # Gate -> Phase could not be executed: `foundry_gate` destroyed the very
    # marker `foundry_mark_phase_complete` demands, and the lead's next call was
    # refused with "Must call Foundry-Next before phase transitions" for having
    # done exactly what start.md told it to. The workaround it forced — a
    # Foundry-Next between every gate and its transition — is what made
    # Foundry-Next look mandatory there, and FR-044 says it is optional.
    #
    # `foundry_mark_phase_complete` remains the ONE consumer. The token means
    # "a Foundry-Next preceded this transition", and a gate check is not a
    # transition: it writes no phase, advances no counter and, on the passing
    # path, only stamps `.gate-passed`. Reading a marker it does not act on and
    # then deleting it was never the handshake, it was a side effect of one.
    checklist: list[dict] = []
    # D-186: the three locals this used to carry (`passed`, `reason`, `hint`)
    # were a last-writer-wins ladder. Every failing check now enters the ladder
    # with its own rank and its own remedy; see `_GateLadder` above.
    ladder = _GateLadder()
    facts: dict = {}

    # `grind` is the one gate token that maps to TWO transitions — `grind_start`
    # and `assay_fail`, two doors into F3 — and it reports both. They share one
    # evaluation, so the second call adds nothing the first did not; absorbing
    # both is what makes the table the whole mapping rather than the table plus
    # a rule about which of the pair a gate really means.
    # fallout CT-020 (D-085) — ONE LADDER PER DISTINCT CHECK, NOT PER TOKEN.
    #
    # `grind` maps to TWO transitions and `_assay_fail_preconditions` IS
    # `_grind_start_preconditions`, so the identical ladder was absorbed twice
    # and the door published every refusal twice: driven with a team active,
    # `Foundry-Gate('grind')` returned two rank-20 refusals reading "Active
    # teammates: Team dirs: c3-team" for one registered team. The checklist was
    # already deduped one line down; the refusals were not, and the invariant
    # test could not see it because it compares `{r['reason'] for r in ...}` — a
    # SET, which is exactly what a duplicate survives.
    #
    # Deduped HERE rather than inside `_GateLadder`: the ladder's contract is
    # that nothing it is handed is discarded, and two callers passing one list
    # twice is this loop's fact about the mapping table, not the ladder's about
    # its input.
    absorbed: list[dict] = []
    for token in GATE_TO_TRANSITION[phase]:
        outcome = _token_preconditions(
            token, fdir, project_root, reason=reason, text=text
        )
        if outcome is None:  # pragma: no cover — the table's own invariant test
            continue          # asserts every mapped token has a routine
        # D-186 / D-190 / D-191: `absorb` re-enters every failing check at the
        # rank the evaluation that owns the checks declared, so the ordering is
        # still that evaluation's AND this door publishes all of them under
        # `refusals`. It used to publish one entry for a call that had computed
        # four.
        fresh = [r for r in outcome["refusals"] if r not in absorbed]
        absorbed.extend(fresh)
        ladder.absorb(fresh)
        for entry in outcome["checklist"]:
            if entry not in checklist:
                checklist.append(entry)
        # fallout GI-032 / AC-060 — NON-REFUSING FACTS ARE REPORTED, NOT ACTED
        # ON. `would_halt` is the one this run adds: the gate PASSES at the
        # cycle cap and says so, because reaching the cap is not something a
        # lead clears at the door — the run stops, with its open work written
        # down, and that is a successful transition.
        for key, value in outcome.items():
            if key not in ("passed", "reason", "hint", "checklist", "refusals"):
                facts.setdefault(key, value)

    # fallout CT-020 (D-084) — A ROUTINE'S FACTS NEVER SHADOW THE DOOR'S OWN
    # IDENTITY, AND THAT IS STRUCTURAL RATHER THAN A LIST OF SAFE NAMES.
    #
    # This read `{"phase": phase, ..., **facts}`, so any fact named `phase`,
    # `passed` or `checklist` replaced the door's answer with a precondition's.
    # One did: `_source_phase_rung` published the run's CURRENT phase under
    # `phase`, and `Foundry-Gate('temper')` therefore answered `phase: "F0"`.
    # That key is renamed at its source (`source_phase`), and the spread is
    # moved BELOW the literals so the next fact to collide cannot do it again —
    # the collision was the generator, the name was one instance of it.
    result = {**facts, "phase": phase, "passed": ladder.passed, "checklist": checklist}
    if not ladder.passed:
        result["reason"], result["hint"] = ladder.outcome()
        # D-186: every failing check's own sentence, in the same order, so a
        # refusal that loses the one rendered line is PUBLISHED rather than
        # discarded — "the width refusal the arm above computed is discarded"
        # is how the defect names the harm. `reason` and `hint` are still the
        # single pair a terminal prints (NFR-005); this is the machine-readable
        # rest of what the gate worked out.
        result["refusals"] = ladder.refusals()
    else:
        # P4 (FR-005 / ST-002): a passing gate advances the guidance state.
        # Record which gate passed so the next Foundry-Next emits the
        # transition step instead of re-running this now-satisfied gate.
        # Cleared by _update_phase when the phase actually advances.
        try:
            (fdir / GATE_PASSED_MARKER).write_text(
                json.dumps({"phase": phase, "at": now_iso()}),
                encoding="utf-8",
            )
        except OSError:
            pass
    return result


# --------------------------------------------------------------------------- #
# fallout GI-033 / AC-061 / FR-063 (D-021 / D-035, concern C-027) — THE HALTED
# READ AND ITS REFUSAL, MOVED HERE FROM `orchestration/halt.py`.
#
# GI-033 names ONE seam, `transitions.py` -> `halt.py`, one-way. A gate reaching
# into halt.py for the halt record was a SECOND crossing that the seam table
# never covered and the guard's allowlist excused. Neither had a halt.py
# caller: `_halted_state`'s only one was `_halted_refusal`, and `_halted_refusal`
# had none at all. Their real callers are `foundry_gate` in this module and the
# three transition doors — every one of them a VERIFIER — so the pair moves to
# the layer that reads it and the row is deleted rather than narrowed. halt.py
# keeps what the seam is FOR: `_halt_if_capped` and the terminal seal.
# --------------------------------------------------------------------------- #

def _halted_state(fdir: Path) -> dict | None:
    """The run's HALTED record, or None when the run is not halted.

    fallout GI-033 / FR-063 / AC-061 (C-017, then C-027) — THE READ IS THE
    LEAF'S AND THIS DELEGATION LIVES WITH ITS TWO CALLERS.

    THE ONLY READ of `state.json.phase == RUN_PHASE_HALTED`, for the reason
    `_current_inspect_mode` is the only read of the recorded width: a terminal
    state that each door decides for itself is a terminal state each door can
    decide differently. What changed is WHERE the only read lives.
    `gates.py` is a VERIFIER and `halt.py` is lifecycle, so a gate reaching
    into halt.py for the halt record was a second verifier-to-lifecycle
    crossing on top of the one seam GI-033 names. The record itself is a
    document read and was already leaf material; what was left in halt.py was
    this DELEGATION, and its only two callers are `foundry_gate` below and
    `transitions.py` — both verifier. So the delegation moved to them, and
    halt.py, which never called it, lost the coupling with it.

    The three closed-set values are supplied here rather than known there
    because a leaf may not know a vocabulary: the HALTED phase token, the
    reason vocabulary, and the cap normaliser — which is
    `foundry_state.persisted_max_cycles` since C-027 landed it, so nothing in
    this call reaches outside the leaf and the vocabulary any more.
    """
    return halted_state(
        fdir,
        halted_phase=RUN_PHASE_HALTED,
        reason_of=halt_reason,
        max_cycles_of=persisted_max_cycles,
    )


def _halted_refusal(fdir: Path, surface: str) -> dict | None:
    """fallout ST-001 / CT-004 / CT-007 / CT-013 / AC-025 — HALTED is terminal, and
    every door that could leave it reads THIS.

    Returns None when the run may proceed, otherwise the refusal `surface`
    should return, carrying `error` / `hint` in the house shape plus the halt
    record. `foundry_gate` reshapes the same two strings into its
    `reason` / `hint` pair; nothing recomputes the judgement.

    D-081 / D-082 — HALTED WAS WRITTEN AND READ BY NOTHING.
    ------------------------------------------------------
    ST-001 makes HALTED a to-state reached from "any live run phase (F1..F5.5)",
    CT-004 gives it its own door and record, and CT-007 makes `heading_for` say
    "DONE or HALTED" — so the two are named endings and not one. Yet
    `_halt_if_capped` was the only code in the server
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
    that the cap had been overridden — and CT-007's promise that every
    `Foundry-Next` response carries `heading_for` rested on lead discipline,
    which is the thing the cap exists to replace.

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
    # halt whose report generation failed (CT-004's designed report-regeneration
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
    reason, hint = _halted_sentences(fdir, halted, cycle_text)
    return {
        "error": f"Cannot call {surface} \u2014 {reason}",
        "hint": hint,
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


def _halted_sentences(
    fdir: Path, halted: dict, cycle_text: str
) -> tuple[str, str]:
    """The halt's reason and its remedy, WITHOUT any door's leading clause.

    fallout AC-062 / GI-029 (D-088) \u2014 ONE SPELLING, TWO SHAPES.

    `_halted_refusal` prefixes "Cannot call <surface> \u2014 " and returns the
    house `{error, hint}` for a door that answers directly.
    `transitions._halted_outcome` carries this SAME pair as a ranked rung, where
    the leading clause belongs to the transition (`_transition_refusal` adds it)
    and the gate renders it bare. Splitting the sentence out is what lets the
    rung speak from inside the routine without the two doors re-wording it,
    which is the drift this module has already paid for at both F6 doors.

    D-165 lives here: the report half is read from the FILE's presence, with the
    recorded generator error supplying the reason only when it is absent. A
    refusal that said "NOT written" about a file sitting on disk would be that
    defect with the sign flipped.
    """
    report_path = fdir / REPORT_MD_FILENAME
    report_error = halted["halted_report_error"]
    report_present = report_path.exists()
    reason = (
        f"this run is HALTED. It stopped at "
        f"{cycle_text} because {halted['halted_reason']}. HALTED is a "
        "terminal state and it is NOT DONE: the run ended with open work"
        + (
            " and the report says what."
            if report_present
            else (
                f", and the report was NOT written \u2014 {report_error or 'it is not present at ' + str(report_path)}."
            )
        )
    )
    hint = (
        (
            f"Nothing leaves HALTED \u2014 no phase token, no gate. Read "
            f"{REPORT_MD_FILENAME}, tell the user what remains open by "
            "tier, and stop. To carry the remaining work forward, start a "
            "NEW run (Foundry-Init) with a higher --max-cycles; the cap is "
            "not overridden in place."
        )
        if report_present
        else (
            "Nothing leaves HALTED \u2014 no phase token, no gate \u2014 but the "
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
    )
    return reason, hint
