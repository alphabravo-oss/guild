"""The per-cycle stream roll-up and the streams-complete check.

Survey blocks J and V. One record per (stream, cycle), replacing the last;
`records[]` keeps the history.
"""
from __future__ import annotations

from pathlib import Path

from foundry_mcp.schemas.vocab import (
    DELTA_CONDITIONAL_STREAMS,
    FULL_ROSTER_STREAMS,
    INSPECT_MODES,
    STREAM_WIRE_IDS,
)
from foundry_mcp.tools.artifacts import (
    ROLLUP_FILENAME,
    _spec_requirement_ids,
    count_spec_requirements,
    TRACE_CLEAN_AT_MARKER,
    _artifact_guard,
    _document_transaction,
    _load_json,
    _read_text,
    _stream_marker,
)
from foundry_mcp.tools.foundry_state import (
    check_streams_complete,
    current_inspect_mode,
    current_cycle,
    get_run_dir,
    now_iso,
    prove_is_clean,
)
from foundry_mcp.tools.rosters import roster_length
from pathlib import Path




def _marker_counts(marker: Path) -> dict | None:
    """Parse a ``.{stream}-complete`` marker's ``key=value`` body.

    Returns ``{"items_checked", "items_total", "findings"}`` with ``findings``
    possibly None (the key absent), or None when the marker cannot be read.
    Kept as the load path for archives written before the per-cycle roll-up
    existed — those runs have markers and no ``stream-rollup.json``.
    """
    if not marker.exists():
        return None
    # D-098: UnicodeDecodeError is a ValueError, not an OSError, so a marker
    # with one non-UTF-8 byte raised straight through the old `except OSError`.
    # An unreadable marker still yields the zero-counts record rather than None:
    # None means "no marker", and a PRESENT marker whose numbers cannot be read
    # must fail the coverage threshold, not skip it.
    text = _read_text(marker)
    counts: dict = {"items_checked": 0, "items_total": 0, "findings": None}
    for line in text.splitlines():
        for key in ("items_checked", "items_total", "findings"):
            if line.startswith(f"{key}="):
                try:
                    counts[key] = int(line.split("=", 1)[1].strip())
                except (ValueError, IndexError):
                    pass
    return counts




def _prove_is_clean(fdir: Path, project_root: str) -> bool:
    """True when the recorded PROVE stream is clean: 0 findings AND >=95%
    requirement coverage across THIS cycle's records.

    Coverage is read from the per-cycle roll-up (FR-014) so a PROVE run
    delivered as several partial tranches is judged on its cycle TOTAL rather
    than on whichever tranche happened to be written last. Archives predating
    the roll-up fall back to the marker's aggregate counts.

    A spec that parses to ZERO requirements is never clean (FR-020 / AC-025).
    Previously the >=95% check was skipped when ``spec_count == 0``, so any
    ``.prove-complete`` with ``findings=0`` on an unresolvable or unparseable
    spec drove the F4 auto-VERIFY path — manufacturing a passing run out of a
    spec nothing had actually been proved against.
    """
    # fallout FR-004 / GI-033 -- LAZY SEAM, written once per symbol.
    # `gates` import(s) this module, so a module-top import here would
    # close a cycle that takes every tool in this server down at once.
    # Unguarded, so a wiring break fails loudly at the one call site that
    # needs the symbol rather than hiding behind a silent fallback.
    marker = fdir / _stream_marker("prove")
    if not marker.exists():
        return False
    # fallout GI-024 / AC-011 — THE RULE IS THE LEAF'S; THE INPUTS ARE THIS
    # MODULE'S.
    #
    # What this function is FOR is gathering the two facts the decision needs
    # off THIS run: the cycle's roll-up totals (falling back to the marker's
    # aggregate on an archive that predates the roll-up) and the number of
    # requirement ids the run's spec declares. The decision it used to make
    # inline — findings absent is not findings zero, a zero-requirement spec is
    # never clean, coverage at 95% — is one implementation in
    # `foundry_state.prove_is_clean` now, and `tools/foundry_report.py` reaches
    # the same one. Two readers of one threshold is how a run gets called clean
    # at one door and unclean at the next.
    return prove_is_clean(
        totals=_rollup_totals(fdir, current_cycle(fdir), "prove")
        or _marker_counts(marker),
        spec_requirement_count=count_spec_requirements(project_root),
    )




# --- Stream markers ---

# Verification streams recordable via Foundry-Stream.
#
# FR-013 / CT-002: this is no longer a declaration, it is a READ of the one
# canonical vocabulary module. The set used to be re-typed here, in server.py's
# JSON-Schema enum, in foundry_sync_defects' local `valid_sources`, in two
# display loops and in the marker-clear lists — six copies that drifted
# independently (the schema advertised streams the runtime rejected; the sync
# coercion set disagreed with both). Every one of those sites now reads
# schemas/vocab.py.
#
# The name is retained as an alias because it is part of this module's public
# surface. Recordable is NOT required: the required-stream computation in
# _check_streams_complete is intentionally independent.
#: fallout FR-050 / CT-003 / OT-031 — the named refusal `Foundry-Stream` gives
#: an `items_total` that disagrees with the stream's persisted roster. Declared
#: HERE, at the door that raises it: `tools/rosters.py` owns the roster and its
#: own four refusal tokens, and this is a condition of RECORDING a stream rather
#: than of writing a roster. The reader it consults is casting 1's.
ROSTER_MISMATCH = "ROSTER_MISMATCH"

#: fallout FR-050 / GI-020 / CT-003 (concern C-058) — the named refusal a
#: record gets when the roster it declares a population against cannot be read.
#: DISTINCT from `ROSTER_MISMATCH`, which is a record disagreeing with a roster
#: that read fine: the remedy is different (repair the artifact, not the
#: record), so the token is different.
ROSTER_UNREADABLE = "ROSTER_UNREADABLE"



VALID_STREAMS = STREAM_WIRE_IDS


#: The run phases that ARE an INSPECT, so a run in neither has no INSPECT whose
#: width could be missing. `gates.py` spells the verifier side's copy of this
#: (it may not import this lifecycle module), and
#: `test_both_streams_complete_compositions_answer_the_same_thing` drives both
#: over one run directory so the two cannot drift apart.
_INSPECT_PHASES = ("F2", "F5")








# --------------------------------------------------------------------------- #
# Per-cycle stream roll-up (FR-014 / CT-003 / AC-020 / OT-009).
#
# The `.{stream}-complete` marker is a single file OVERWRITTEN on every record,
# so it can only ever carry the last write. That is why the old drop-warning
# compared against "the previous write of this file" rather than against cycle
# N-1, and why a PROVE run delivered as two partial tranches lost the first one.
#
# The roll-up is the accumulation surface the marker cannot be: records are
# appended under the SERVER cycle counter, partial records are accepted and
# stored as they arrive, coverage thresholds are evaluated exactly once per
# cycle at the streams-complete check (where the full picture exists), and drop
# warnings compare cycle N's total against cycle N-1's total.
#
# Accumulation rule across the records of one cycle:
#   items_checked, findings -> SUM  (each record covers a disjoint tranche)
#   items_total             -> MAX  (every tranche reports the same population
#                                    denominator; summing would multiply it)
# --------------------------------------------------------------------------- #

# fallout GI-033 / FR-063 / AC-061 (concern C-018 / C-019) — THE NAME IS LEAF
# MATERIAL AND IT LIVES IN THE LEAF NOW.
#
# `stream-rollup.json`'s name was declared here, in a LIFECYCLE module, and read
# by two VERIFIER modules — `evidence_boundary.py` for the sweep record and
# `width.py` for the cycle roll-up — which is two rows of the layering debt for
# one string. `tools/artifacts.py` already holds every `*_MARKER` filename;
# a run-artifact name beside them is exactly what that leaf is for, and a
# verifier reaching a leaf is legal by GI-033 with no exception needed.
#
# Re-exported by name here, NOT re-declared: this module is still where the
# roll-up is written, so a reader that reaches `streams.ROLLUP_FILENAME` keeps
# working, and there is one definition of the string.




def _recorded_stream_scope(fdir: Path, cycle: int, stream: str) -> str:
    """The scope THIS cycle's recorded decision drew for one stream (D-139).

    ``"full"`` / ``"delta"`` / ``"skipped"`` as `_decide_inspect_mode` wrote
    them, or ``""`` when no decision applies to this cycle — an older archive,
    a run whose INSPECT was opened before the width existed, or a decision
    stamped with a different cycle number.

    The cycle stamp is checked, not assumed: a caller reading a scope off a
    decision made for another cycle would be narrowing this one against a width
    nothing recorded for it — GI-008's named violation, arriving through a
    helper instead of through a recomputation.

    D-216: that check now lives in the read, as its `cycle` argument, rather
    than being re-stated here. It was one of the two places the module already
    held the rule while `_current_inspect_mode` itself returned the newest entry
    whatever crossing wrote it, so the narrow readers were guarded and the doors
    that decide on the width were not. One subject, passed in; not two readers.
    """
    # fallout FR-004 / GI-033 -- LAZY SEAM, written once per symbol.
    # `width` import(s) this module, so a module-top import here would
    # close a cycle that takes every tool in this server down at once.
    # Unguarded, so a wiring break fails loudly at the one call site that
    # needs the symbol rather than hiding behind a silent fallback.
    recorded = current_inspect_mode(fdir, cycle, modes=INSPECT_MODES)
    if not isinstance(recorded, dict):
        return ""
    scope = recorded.get("stream_scope")
    if not isinstance(scope, dict):
        return ""
    entry = scope.get(stream)
    if not isinstance(entry, dict):
        return ""
    value = entry.get("scope")
    return value if isinstance(value, str) else ""




def _rollup_totals(fdir: Path, cycle: int, stream: str) -> dict | None:
    """Return this cycle's accumulated totals for one stream, or None.

    None means the cycle has no record for that stream at all \u2014 distinct from
    a recorded zero, which callers must be able to tell apart.
    """
    data = _load_json(fdir / ROLLUP_FILENAME)
    entry = data.get("cycles", {}).get(str(cycle), {}).get(stream)
    if not isinstance(entry, dict):
        return None
    return {
        "items_checked": entry.get("items_checked", 0),
        "items_total": entry.get("items_total", 0),
        "findings": entry.get("findings", 0),
        "records": len(entry.get("records", [])),
    }




def _record_stream_rollup(
    fdir: Path,
    cycle: int,
    stream: str,
    items_checked: int,
    items_total: int,
    findings_count: int,
    declared_cycle: int,
) -> dict:
    """Record one stream run against the cycle's roll-up, REPLACING the last.

    ``declared_cycle`` is what the caller asserted; it is retained per record
    for audit but is NEVER the key -- the key is the server counter (FR-005).
    Returns the cycle's totals AFTER this record, plus ``replaced``: the record
    this one supersedes, or None on the first write.

    fallout FR-023 / FR-049 / GI-016 / CT-003 / ST-008 / ST-009 / AC-030 /
    OT-028 -- REPLACE PER (STREAM, CYCLE), NOT ACCUMULATE.
    -------------------------------------------------------------------------
    The three totals were `+= items_checked`, `max(items_total)` and
    `+= findings`, which is the arithmetic for TRANCHES of one run: a PROVE
    delivered in two halves summed to the whole. It is the wrong arithmetic for
    what actually happens, which is a stream RE-RUNNING inside one cycle -- a
    re-dispatched TRACE, a PROVE the lead asked for again after a fix. Then 40
    of 40 recorded twice reads as 80 of 40, and coverage passes 100%: the
    threshold `_coverage_shortfall` evaluates is satisfied by the same work
    counted twice, which is the one direction a coverage check must never fail.

    So the top-level totals are the LAST record's values, `records[]` keeps
    every one of them in order, and the result names what was replaced. A run
    genuinely delivered in tranches is recorded as the agent's own running
    total, which is what the agent has and the server does not -- and GI-016
    puts the recording in the agent's hands for exactly that reason.

    THE HISTORY IS NOT LOST, which is what makes this safe (GI-006). Every
    record ever written stays under `records[]`; what changes is which of them
    the top-level fields report. A reader wanting the tranche history reads the
    list, and `Foundry-Stream` hands the caller the record it displaced so the
    replacement is visible at the door rather than only in the artifact.
    """
    path = fdir / ROLLUP_FILENAME
    # D-103: THE concurrency site. F2 runs 4-8 parallel streams and each calls
    # Foundry-Stream as it finishes, so the read-modify-write here is the
    # designed path, not an edge case. Unlocked, a 4-process x 40-call drive
    # lost 107 of 160 tranches — a direct violation of CT-003's "accepted and
    # stored as they arrive".
    with _document_transaction(path) as data:
        cycles = data.setdefault("cycles", {})
        if not isinstance(cycles, dict):
            cycles = data["cycles"] = {}
        bucket = cycles.setdefault(str(cycle), {})
        entry = bucket.setdefault(
            stream, {"items_checked": 0, "items_total": 0, "findings": 0, "records": []}
        )

        history = entry.get("records")
        if not isinstance(history, list):
            history = entry["records"] = []
        # The record this one supersedes, captured BEFORE the append so the door
        # can name it. None on the first write, which is what "replaced" meaning
        # "nothing" has to look like.
        replaced = (
            dict(history[-1])
            if history and isinstance(history[-1], dict)
            else None
        )
        history.append(
            {
                "recorded_at": now_iso(),
                "items_checked": items_checked,
                "items_total": items_total,
                "findings": findings_count,
                "declared_cycle": declared_cycle,
            }
        )
        entry["items_checked"] = items_checked
        entry["items_total"] = items_total
        entry["findings"] = findings_count

        data["updated_at"] = now_iso()
    return {
        "items_checked": entry["items_checked"],
        "items_total": entry["items_total"],
        "findings": entry["findings"],
        "records": len(entry["records"]),
        "replaced": replaced,
    }




def _recorded_prove_roster(fdir: Path, cycle: int) -> list[str] | None:
    """The PROVE rows THIS cycle's recorded DELTA decision named, or None.

    None means "no DELTA roster applies to this cycle" — the run is at FULL, the
    run predates the width, or the newest recorded decision belongs to another
    cycle — and the caller then measures against the spec exactly as it did
    before the width existed. An empty LIST is a different answer from None: the
    decision recorded a roster and the roster is empty, which
    `_prove_delta_sample` produces only when the spec parses to zero rows.

    THE CYCLE MUST MATCH, and that is not defensive padding. A width is a fact
    about one crossing: a roster decided for cycle 5 says nothing about what
    cycle 4's PROVE owed. On the live path the two are equal by construction —
    `inspect_start` stamps `entry["cycle"]` from the same counter
    `_check_streams_complete` and `foundry_mark_stream` read — so the check
    costs nothing there, and a resumed archive whose counter and ledger
    disagree falls back rather than measuring one cycle's work against another
    cycle's roster.

    D-216: the match is made by the read, which takes the cycle as its subject,
    rather than being re-stated here against an entry the read had already
    handed back unconditionally. This helper and `_recorded_stream_scope` were
    the two places the module held the rule while the read that every DECIDING
    door goes through did not.
    """
    # fallout FR-004 / GI-033 -- LAZY SEAM, written once per symbol.
    # `width` import(s) this module, so a module-top import here would
    # close a cycle that takes every tool in this server down at once.
    # Unguarded, so a wiring break fails loudly at the one call site that
    # needs the symbol rather than hiding behind a silent fallback.
    recorded = current_inspect_mode(fdir, cycle, modes=INSPECT_MODES)
    if not recorded or recorded.get("mode") != "DELTA":
        return None
    sample = recorded.get("prove_sample")
    if not isinstance(sample, list):
        return None
    return [row for row in sample if isinstance(row, str)]




def _coverage_shortfall(fdir: Path, project_root: str, stream: str, cycle: int) -> dict | None:
    """Evaluate this stream's per-cycle coverage threshold, once, on the total.

    Returns a named shortfall dict, or None when the stream either has no
    threshold or clears it. Called from ``_check_streams_complete`` \u2014 the
    streams-complete check is the single point where the whole cycle's records
    are in hand (CT-003). ``foundry_mark_stream`` deliberately does NOT
    evaluate it: a partial tranche must be stored, not refused.

    Falls back to the marker's aggregate counts when this cycle has no roll-up
    entry, exactly as ``_prove_is_clean`` does. Without the fallback an archive
    written before the roll-up existed — or one whose roll-up was lost — had a
    marker the streams-complete check counted as PRESENT while the threshold
    silently evaluated nothing, so 40% coverage passed. "No numbers" must mean
    "read them from the marker", never "assume the threshold is met".

    D-080 — THE PROVE THRESHOLD IS MEASURED AGAINST THE WIDTH THE SERVER
    RECORDED, AND UNTIL IT WAS, DELTA WAS UNREACHABLE ON THE GUIDED PATH.
    -------------------------------------------------------------------
    `_check_streams_complete` was made to READ the recorded roster — GI-008
    names "a streams-complete check that reads a roster nothing recorded" as
    the violation — and then handed each member of that roster to this
    function, which measured PROVE against
    `count_spec_requirements(project_root) * 0.95` and consulted no recorded
    decision at all. So the one check that CONSUMES the roster ignored the
    width the `inspect_start` transition had just decided, and a PROVE that
    checked exactly the roster the server itself recorded was reported
    incomplete forever.

    Driven end to end on a 40-requirement spec with a recorded DELTA decision
    whose `prove_sample` is 12 rows: `Foundry-Stream(prove, items_checked=12)`
    warned "PROVE checked 12 requirements across cycle 5 but the spec has 40.
    Coverage is 30%"; `_check_streams_complete` returned complete False,
    missing 'prove'; `Foundry-Phase('inspect_clean')` refused with "streams
    incomplete: prove"; and `Foundry-Next`'s F2 branch returned
    action=run_streams with "Missing: prove" and kept returning it. Re-marking
    prove at 38/40 cleared it immediately — that is, a DELTA cycle could only
    be closed by running PROVE at FULL width, the lead never reached the
    DELTA-width refusal that would have told them to widen, and AC-018's
    saving was exactly zero.

    NO 0.95 SLACK ON THE DELTA ARM. At FULL the denominator is the whole spec
    and the 5% is tolerance for a matrix that moved under a long stream. A
    DELTA roster is a NAMED, FINITE list of rows the server itself drew — the
    rows tied to the defects the preceding GRIND fixed, plus the sampled
    remainder, by id — so "which of these did you not check" has an answer and
    there is nothing to be tolerant of. `checked >= len(roster)` is the whole
    test.
    """
    # fallout FR-004 / GI-033 -- LAZY SEAM, written once per symbol.
    # `gates` import(s) this module, so a module-top import here would
    # close a cycle that takes every tool in this server down at once.
    # Unguarded, so a wiring break fails loudly at the one call site that
    # needs the symbol rather than hiding behind a silent fallback.
    totals = _rollup_totals(fdir, cycle, stream) or _marker_counts(
        fdir / _stream_marker(stream)
    )
    if totals is None:
        return None
    checked = totals["items_checked"]

    if stream == "prove":
        # AC-018 / FR-012 / FR-013 / AC-017: the recorded roster first. A DELTA
        # cycle owes the rows its own decision named and nothing else; only a
        # cycle with no DELTA roster falls through to the spec-wide threshold.
        roster = _recorded_prove_roster(fdir, cycle)
        if roster is not None:
            required = len(roster)
            if required > 0 and checked < required:
                return {
                    "stream": "prove",
                    "checked": checked,
                    "required": required,
                    "coverage": f"{checked / required * 100:.0f}%",
                    "mode": "DELTA",
                    "roster": roster,
                    "reason": (
                        f"PROVE checked {checked} of the {required} requirement "
                        f"row(s) cycle {cycle}'s recorded DELTA roster names "
                        f"({', '.join(roster[:12])}"
                        + (", ..." if len(roster) > 12 else "")
                        + "). That roster IS this INSPECT's width — the rows "
                        "tied to the defects the preceding GRIND fixed plus the "
                        "sampled remainder — so every row in it is owed, and no "
                        "row outside it is."
                    ),
                }
            return None

        spec_count = count_spec_requirements(project_root)
        if spec_count > 0 and checked < spec_count * 0.95:
            return {
                "stream": "prove",
                "checked": checked,
                "required": spec_count,
                "coverage": f"{checked / spec_count * 100:.0f}%",
                "reason": (
                    f"PROVE checked {checked} requirements across cycle {cycle} but the "
                    f"spec has {spec_count}. Coverage is {checked / spec_count * 100:.0f}% "
                    "\u2014 must be \u226595%."
                ),
            }
        return None

    if stream == "trace":
        declared = totals["items_total"]
        if declared > 0 and checked < declared * 0.95:
            return {
                "stream": "trace",
                "checked": checked,
                "required": declared,
                "coverage": f"{checked / declared * 100:.0f}%",
                "reason": (
                    f"TRACE checked {checked}/{declared} symbols across cycle {cycle} "
                    f"({checked / declared * 100:.0f}%). Must check \u226595% of declared symbols."
                ),
            }
    return None




def foundry_mark_stream(
    stream: str,
    cycle: int,
    items_checked: int = 0,
    items_total: int = 0,
    findings_count: int = 0,
    project_root: str = ".",
) -> dict:
    """Record a verification stream's coverage for this cycle.

    Partial records are ACCEPTED and stored (CT-003 / AC-020). The >=95%
    coverage thresholds are no longer enforced here \u2014 enforcing them at record
    time discarded the tranche entirely, so a PROVE run split across two calls
    lost its first half. They are evaluated once per cycle in
    ``_check_streams_complete`` against the cycle TOTAL instead.
    """
    if stream not in STREAM_WIRE_IDS:
        return {"error": f"Invalid stream: {stream}. Must be one of: {', '.join(sorted(STREAM_WIRE_IDS))}"}

    fdir = get_run_dir(project_root)
    if not fdir or not fdir.exists():
        return {"error": "No active foundry run"}
    if (corrupt := _artifact_guard(fdir)):
        return corrupt

    if items_checked <= 0:
        return {
            "error": f"Cannot mark {stream} complete with items_checked={items_checked}. "
                     "You must report how many items were actually checked. "
                     "trace: symbols checked. prove: requirements checked. "
                     "sight: pages/elements exercised. test: tests run. "
                     "probe: endpoints hit. research_audit: recommendations audited. "
                     "flow_trace: flow-delta packets verified. "
                     "coverage_diff: coverage_list source items diffed. "
                     "test01: derived spec-test hypotheses executed.",
            "hint": "If the stream genuinely checked 0 items, the scope may be wrong.",
        }

    # D-100: the guard above refused items_checked <= 0 while findings_count had
    # no lower bound at all, and that asymmetry was load-bearing:
    # mark_stream("prove", 2, findings=9) then mark_stream("prove", 1,
    # findings=-9) drives the cycle's findings to a value `_prove_is_clean` reads
    # as clean, flipping it False -> True. On TRACE the same value stamps
    # .trace-clean-at — the anchor that lets a LATER cycle skip the TRACE stream
    # outright.
    #
    # fallout GI-016 / FR-023 / CT-003 (D-096) — THE REFUSAL STATES THE MODEL
    # THE WRITER ACTUALLY IMPLEMENTS.
    # ----------------------------------------------------------------------
    # Both this comment and the sentence below described accumulate-by-ADDITION
    # across a cycle's tranches — "a negative count would erase findings an
    # earlier record of this cycle already reported" — which is the arithmetic
    # `_record_stream_rollup` had before this release and has not had since:
    # GI-016 made the record REPLACE the cycle's totals, so a negative count
    # erases nothing and simply BECOMES the cycle's finding count. The refusal
    # was right and its published reason taught every caller it refused a
    # contract the door no longer holds, which is the one audience guaranteed to
    # be reading it. Same rung, same refusal, the model it names corrected.
    if findings_count < 0:
        return {
            "error": (
                f"Cannot record {stream} with findings_count={findings_count}. "
                "A record REPLACES this cycle's totals for the stream, so a "
                "negative count does not offset an earlier record — it becomes "
                "the cycle's finding count, and a cycle cannot have found less "
                "than nothing."
            ),
            "hint": (
                "Report the findings this record covers — zero or more, never "
                "negative. A stream delivered in parts reports its own RUNNING "
                "total, because the last record for a (stream, cycle) is the "
                "one the cycle's totals read."
            ),
        }

    if items_total < 0:
        return {
            "error": (
                f"Cannot record {stream} with items_total={items_total}. "
                "The population a tranche was drawn from cannot be negative."
            ),
            "hint": "Report the size of the population, or 0 when this stream has no fixed denominator.",
        }

    # The near-miss beside the same guard: 1667% coverage was accepted in
    # silence and trivially satisfied the >=95% gate. Judged PER RECORD, where
    # "checked more than exist" is unambiguous — the cycle TOTAL is deliberately
    # not judged here, because a legitimate re-record of a cycle would trip it
    # and CT-003 requires tranches be stored, not refused.
    if items_total > 0 and items_checked > items_total:
        return {
            "error": (
                f"Cannot record {stream} with items_checked={items_checked} against "
                f"items_total={items_total}: a tranche cannot check more items than "
                "the population it declares."
            ),
            "hint": "Either items_checked is overstated or items_total understates the population.",
        }

    # The roll-up is keyed by the SERVER counter, never by the caller's `cycle`
    # (FR-005). The caller's value is kept on the record for audit only.
    server_cycle = current_cycle(fdir)
    # fallout FR-050 / CT-003 / ST-008 / OT-031 — THE ROSTER IS THE POPULATION.
    #
    # `items_total` is the size of the set this stream is checking, and when a
    # roster has been persisted for the stream that size is not the agent's to
    # assert: it is `rosters/<stream>.json`'s length. A stream that re-derives a
    # SHORTER list and reports 12 of 12 clears the >=95% threshold on a
    # population two thirds the size of the one the run agreed to check, and
    # nothing on the record says so. The roster is read through casting 1's
    # `tools/rosters.py` reader and never re-derived here — a second derivation
    # of the population is the very drift the roster exists to end.
    roster_len, roster_problem = roster_length(fdir, stream)

    # fallout FR-050 / GI-020 / CT-003 (concern C-058, same class as D-071) —
    # A ROSTER THAT WILL NOT READ IS A REFUSAL, NOT AN UNCONSTRAINED RECORD.
    # ------------------------------------------------------------------------
    # The rung below opened `if roster_problem is None and ...`, which reads the
    # problem channel as "no constraint" and DISCARDS the string. Casting 1's
    # D-071 fix changed what that channel means: it used to carry only "the JSON
    # could not be read", and it now also carries the third answer — a document
    # exists at `rosters/<stream>.json` and no population can be read from it.
    # The whole point of that fix is that "no roster" and "a document here I
    # cannot read as one" are different facts; consuming both as `None` put them
    # back together one door over, and on the side that ACCEPTS.
    #
    # THE DIRECTION IS GI-020's. The roster is written at first derivation and
    # read by every later cycle, so a record whose declared population cannot be
    # read is a record nothing can check the >=95% threshold against — and the
    # threshold is the only thing `items_total` is for. Accepting it is the
    # survey's "4/4 with no way to know which four", which is the shape FR-050
    # exists to end. It is refused, naming the problem the reader gave, so the
    # operator repairs the artifact rather than discovering at the streams-
    # complete check that a cycle's coverage was measured against nothing.
    #
    # ABSENT IS STILL ABSENT: `roster_length` answers `(None, None)` for a
    # stream with no roster and that is not a problem — a stream may run before
    # anyone derives a roster for it, which is why the two answers had to become
    # distinguishable in the first place.
    if roster_problem is not None:
        return {
            "error": ROSTER_UNREADABLE,
            "reason": (
                f"Cannot record {stream} with items_total={items_total}: the "
                f"persisted roster for this stream could not be read — "
                f"{roster_problem}"
            ),
            "hint": (
                "`items_total` is checked against the roster, so a roster "
                "nothing can read leaves the coverage threshold measured "
                "against a population nobody knows. Repair "
                f"rosters/{stream}.json, or re-derive it with "
                "Foundry-Roster(stream, items, revise=true, reason=...). A "
                "stream with NO roster at all is not this refusal — that is "
                "unconstrained by design."
            ),
            "roster_problem": roster_problem,
            "items_total": items_total,
        }

    if roster_len is not None and items_total != roster_len:
        return {
            "error": ROSTER_MISMATCH,
            "reason": (
                f"Cannot record {stream} with items_total={items_total}: the "
                f"persisted roster for this stream names {roster_len} item(s)."
            ),
            "hint": (
                f"Report items_total={roster_len} — the roster is the population "
                "this stream agreed to check, and a smaller total clears the "
                "coverage threshold on a smaller set. If the roster itself is "
                "wrong, revise it: Foundry-Roster(stream, items, revise=true, "
                "reason=...)."
            ),
            "roster_length": roster_len,
            "items_total": items_total,
        }

    prev_totals = _rollup_totals(fdir, server_cycle - 1, stream) if server_cycle > 0 else None
    totals = _record_stream_rollup(
        fdir, server_cycle, stream, items_checked, items_total, findings_count, cycle
    )

    # Drop warning: cycle N's TOTAL against cycle N-1's TOTAL (CT-003). The
    # old comparison read the previous write of this same marker file, which
    # made a second partial tranche in the SAME cycle look like a collapse.
    #
    # D-139 / FR-012 / AC-018 — AND IT NEVER FIRES AGAINST A WIDTH THE SERVER
    # ITSELF DREW.
    # ----------------------------------------------------------------------
    # This rung consulted no recorded decision at all, so on every DELTA cycle
    # that followed a FULL one it accused a stream of rushing for running
    # EXACTLY the roster the server handed it. Driven: cycle 1 prove recorded
    # 172/172 at FULL; cycle 2's `inspect_start` recorded a DELTA roster of 13
    # rows; `Foundry-Stream(prove, cycle 2, items_checked=13, items_total=13)`
    # returned ok with `coverage_shortfall` null — the rung that DOES read the
    # roster was satisfied — beside the warning "Coverage dropped: prove
    # checked 13 items in cycle 2 vs 172 in cycle 1. Are you rushing?". FR-012
    # fixes the DELTA PROVE width as the fixed-defect rows plus ten sampled
    # rows, so 13 of 13 is complete delivery of the declared width, and the
    # same false accusation fired for TRACE on every DELTA cycle after a FULL
    # one.
    #
    # The recorded per-stream SCOPE is what decides. `_decide_inspect_mode`
    # writes `stream_scope[wire]["scope"]` as "full", "delta" or "skipped" at
    # the transition that opened this INSPECT, and a stream recorded "delta"
    # is one whose population the server deliberately narrowed — there is
    # nothing to compare against last cycle's, because they are not the same
    # denominator. `_coverage_shortfall` already measures a DELTA stream
    # against its own roster (D-080), so the width is not going unchecked; it
    # is being checked by the rung that knows what the width IS.
    #
    # A stream recorded "full" on a DELTA cycle — TEST, which AC-019 keeps full
    # and cold — still gets the drop rung, because its denominator did not
    # move. So does every stream on a FULL cycle, and every stream on a run
    # with no recorded decision.
    coverage_warning = ""
    recorded_scope = _recorded_stream_scope(fdir, server_cycle, stream)
    narrowed_by_the_server = recorded_scope == "delta"
    if (
        not narrowed_by_the_server
        and prev_totals is not None
        and prev_totals["items_checked"] > 0
    ):
        if totals["items_checked"] < prev_totals["items_checked"] * 0.7:
            coverage_warning = (
                f"Coverage dropped: {stream} checked {totals['items_checked']} items in "
                f"cycle {server_cycle} vs {prev_totals['items_checked']} in cycle "
                f"{server_cycle - 1}. Are you rushing?"
            )

    coverage_pct = (
        f"{totals['items_checked'] / totals['items_total'] * 100:.0f}%"
        if totals["items_total"] > 0
        else "N/A"
    )

    marker = fdir / _stream_marker(stream)
    marker.write_text(
        f"{now_iso()} cycle={server_cycle}\n"
        f"items_checked={totals['items_checked']}\n"
        f"items_total={totals['items_total']}\n"
        f"coverage={coverage_pct}\n"
        f"findings={totals['findings']}\n",
        encoding="utf-8",
    )

    # TRACE skip-gate anchor: when TRACE passes with zero findings, stamp the
    # current HEAD SHA. Future F2 entries can compare HEAD vs this SHA
    # restricted to manifest key_files — if no overlap, skip TRACE.
    # Deterministic, verbatim the same as re-running LSP: topology unchanged.
    if stream == "trace" and totals["findings"] == 0:
        import subprocess
        try:
            rev = subprocess.run(
                ["git", "-C", project_root, "rev-parse", "HEAD"],
                capture_output=True, text=True, timeout=5,
            )
            if rev.returncode == 0 and rev.stdout.strip():
                import json as _json
                (fdir / TRACE_CLEAN_AT_MARKER).write_text(
                    _json.dumps({
                        "head_sha": rev.stdout.strip(),
                        "stamped_at": now_iso(),
                        "cycle": server_cycle,
                        "items_checked": totals["items_checked"],
                    }),
                    encoding="utf-8",
                )
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            pass

    result: dict = {
        "ok": True,
        "stream": stream,
        "cycle": server_cycle,
        "declared_cycle": cycle,
        "items_checked": totals["items_checked"],
        "items_total": totals["items_total"],
        "coverage": coverage_pct,
        "findings": totals["findings"],
        "records_this_cycle": totals["records"],
        # fallout CT-003 / ST-009 / AC-030 / OT-028 — WHAT THIS RECORD REPLACED.
        # The prior record for this (stream, cycle), or None on the first write.
        # Named at the door rather than left in the artifact, because a
        # replacement a caller cannot see is a replacement it will make twice.
        "replaced": totals["replaced"],
        "recorded": {
            "items_checked": items_checked,
            "items_total": items_total,
            "findings": findings_count,
        },
    }

    # A shortfall is REPORTED here so the lead sees it immediately, but it does
    # not refuse the record — the threshold is enforced once per cycle at the
    # streams-complete check (CT-003), where a later tranche can still clear it.
    shortfall = _coverage_shortfall(fdir, project_root, stream, server_cycle)
    warnings = [w for w in (coverage_warning, shortfall["reason"] if shortfall else "") if w]
    if shortfall:
        result["coverage_shortfall"] = shortfall
    if warnings:
        result["warning"] = " | ".join(warnings)

    return result




def _check_streams_complete(project_root: str) -> dict:
    """Have this cycle's required verification streams completed?

    fallout GI-033 / AC-061 / FR-063 (D-021 / D-035, concern C-027) — THE
    CHECK IS THE LEAF'S; THIS IS THE LIFECYCLE LAYER'S COMPOSITION.
    ------------------------------------------------------------------
    The whole of it — the recorded roster read (AC-017 / ST-006 / ST-007 /
    GI-008), D-117's "a run with no recorded decision is INCOMPLETE, not
    pre-change", the manifest fallback and the per-cycle coverage thresholds
    (FR-014 / CT-003 / AC-020) — is `foundry_state.check_streams_complete`. It
    went to the leaf because `transitions.py` is a VERIFIER module that opens
    and closes the INSPECT this reports on, and GI-033 forbids it importing
    this lifecycle one; two implementations of "are the streams done" is how a
    cycle comes to disagree with itself about what it required.

    D-117's UNRECORDED-WIDTH ARM IS INJECTED, AND OMITTING IT WAS A DEFECT
    (concern C-040, filed by casting 12 against the first version of this
    composition).
    -------------------------------------------------------------------------
    The leaf still holds the arm, but it is gated on this injection, so a
    composition that omits it silently gets the PRE-WIDTH fallback roster
    instead: on a mode-less INSPECT the answer was `complete: True` over
    trace/prove/test, with `research_audit` and `test01` dropped, and
    Foundry-Next then routed the lead to ASSAY. That is GI-008's named
    violation verbatim — "a streams-complete check that reads a roster nothing
    recorded" — and it is exactly what D-117 was filed for.

    THE SCOPING NOTE THAT LOOKED LIKE PERMISSION SAYS SOMETHING ELSE. "The two
    doors that matter ask `_unrecorded_width_problem` themselves rather than
    inferring it from this result" is the tail of the original's paragraph on
    the `in_inspect` PHASE guard — it justifies the arm firing only in F2/F5,
    and ends "so nothing rests on the scope being wider than the phase". The
    three lines under it computed the refusal and returned it from this very
    function. The doors being safe was never the claim; Foundry-Next is a THIRD
    caller and it refuses nothing of its own.

    LAZY SEAM, written once per symbol: `teams` imports this module, so a
    module-top import here closes a cycle that takes every tool in the server
    down at load. `width` is reached the same way and for a second reason —
    this module is lifecycle and width is a VERIFIER, so a module-top import
    would be the crossing GI-033 refuses. It is a LAZY reach that the boundary
    guard does not judge, it predates this cycle, and the principled removal is
    the leaf firing the arm on its own `current_inspect_mode` read and taking
    only the PROSE as an injection — `foundry_state.check_streams_complete`'s
    ground, recorded here so the next reader does not take the seam for a
    design.
    """
    from foundry_mcp.tools.orchestration.teams import _check_sight_required
    from foundry_mcp.tools.orchestration.width import _unrecorded_width_problem

    fdir = get_run_dir(project_root)
    if not fdir:
        return {"complete": False, "missing": "all", "required": [], "shortfalls": []}
    return check_streams_complete(
        fdir,
        modes=INSPECT_MODES,
        marker_of=_stream_marker,
        sight=_check_sight_required(project_root),
        spec_requirement_count=len(_spec_requirement_ids(project_root)[1]),
        # DERIVED, never a second hand list: the pre-width roster is exactly the
        # FULL roster minus the two streams DELTA makes conditional, and both
        # sets are `vocab.py`'s. `gates.py` composes the verifier side's call
        # from the same two vocabulary members, and
        # `test_both_streams_complete_compositions_answer_the_same_thing`
        # drives the two over one run directory so they cannot drift.
        inspect_phases=_INSPECT_PHASES,
        fallback_streams=[
            s for s in FULL_ROSTER_STREAMS if s not in DELTA_CONDITIONAL_STREAMS
        ],
        unrecorded_width_problem=_unrecorded_width_problem,
    )
