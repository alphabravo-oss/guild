"""Per-session foundry run state, and the guarded reads every module shares.

Each MCP server process (= each Claude Code session) holds its own
_active_run_name in module-level state. Concurrent sessions on the same
repo don't conflict because each has its own server process.

All foundry runs live under ARCHIVE_DIR at the project root.

# --------------------------------------------------------------------------- #
# D-137 — THE READ AND THE DECODE ARE ONE OPERATION.
#
# Fourteen sites across seven modules wrote
# ``json.loads(path.read_text(encoding="utf-8"))`` under a handler naming
# ``json.JSONDecodeError`` (nine of them adding ``OSError``). None of those
# handlers catch what ``read_text`` itself raises: ``UnicodeDecodeError`` is a
# subclass of ``ValueError``, NOT of ``JSONDecodeError``, and it is raised
# BEFORE ``json.loads`` is ever reached. One non-UTF-8 byte in
# castings/manifest.json therefore raised straight across the MCP boundary from
# both spawn doors -- the exact doors the cycle before had hardened for
# JSON-shape corruption.
#
# The structural half was worse: the package-wide scan that exists to make this
# class unrepresentable COUNTED THOSE SITES AS GUARDED, because it matched
# handler names against a hand-kept frozenset that happened to contain
# ``JSONDecodeError``. A guard whose own membership table is hand-kept is an
# instance of the class it was written to close.
#
# So the primitive lives HERE, in the package's leaf module -- the one thing
# both ``foundry.py`` and ``foundry_orchestrator.py`` already import, and which
# imports nothing from the package itself. Every module can reach it with no
# risk of closing a cycle in the import graph, which is the reason the two
# tolerant loaders were separate copies in the first place.
#
#   ``read_text_file`` — the tolerant core: (text, named problem). Handles the
#                        raise set of the READ: OSError (missing mid-flight, a
#                        directory occupying the name, permissions) and
#                        UnicodeDecodeError.
#   ``read_document``  — ``read_text_file`` plus the decode: adds ValueError
#                        (JSONDecodeError's parent) and the mapping check.
#                        One call, one raise set, nothing left between them.
#
# Neither raises. A caller that must TELL the operator which file is broken
# reports the ``problem`` string, which always NAMES THE FILE.
# --------------------------------------------------------------------------- #
"""

from __future__ import annotations

import json
from pathlib import Path

_active_run_name: str | None = None
ARCHIVE_DIR = "foundry-archive"


def read_text_file(path: Path) -> tuple[str, str | None]:
    """Read UTF-8 text. Returns ``(text, problem)``; never raises.

    ``problem`` NAMES THE FILE when it exists but cannot be read as UTF-8 text,
    else None. An ABSENT file is not a problem — a run legitimately has
    artifacts it has not written yet, and conflating "absent" with "corrupt" is
    what would make a fresh run refuse to start.
    """
    if not path.exists():
        return "", None
    try:
        return path.read_text(encoding="utf-8"), None
    except (OSError, UnicodeDecodeError) as exc:
        return "", f"{path.name} could not be read ({type(exc).__name__}: {exc})"


def read_json(path: Path) -> tuple[object, str | None]:
    """Read parsed JSON of ANY type. Returns ``(value, problem)``; never raises.

    The READ rung, and only that rung. The whole operation — open, decode the
    bytes, parse the text — sits behind ONE call, because every seam between
    those steps is a place a caller's ``except`` clause has historically failed
    to cover (D-137). The raise set is closed here rather than re-decided at
    each call site: ``OSError`` and ``UnicodeDecodeError`` from the read,
    ``ValueError`` (``JSONDecodeError``'s parent) from the parse.

    ``value`` is ``None`` when there is a problem, and otherwise whatever the
    document parsed to — a list, a bare string, ``null``, a number. Callers
    that own a RICHER shape validator than "is it a mapping" use this and hand
    the parsed value to that validator, which names the offending rung
    (``foundry_spawn``'s ``_manifest_shape_error``). Collapsing a wrong-TYPED
    document to ``{}`` here would take that value away from them and answer a
    structural fault with a generic one.
    """
    if not path.exists():
        return None, None
    raw, problem = read_text_file(path)
    if problem is not None:
        return None, problem
    try:
        return json.loads(raw), None
    except ValueError as exc:
        # "parse error" is load-bearing: a torn document must stay
        # distinguishable from a well-formed one of the wrong TYPE, because the
        # two send the operator to look at different things — a syntax fault
        # versus a structural one. `foundry_spawn`'s door tests pin the
        # distinction on this phrase.
        return None, f"{path.name} is not valid JSON — parse error ({exc})"


def read_document(path: Path) -> tuple[dict, str | None]:
    """Read a JSON OBJECT. Returns ``(data, problem)``; never raises.

    ``read_json`` plus the mapping check, for the majority of callers whose
    only shape requirement is "every run artifact is a mapping". ``data`` is
    ``{}`` whenever there is a problem, so a caller that degrades rather than
    refuses can ignore the second element entirely.
    """
    value, problem = read_json(path)
    if problem is not None:
        return {}, problem
    if value is None and not path.exists():
        return {}, None
    if not isinstance(value, dict):
        return {}, (
            f"{path.name} is not a JSON object (found "
            f"{type(value).__name__}) — every run artifact is a mapping"
        )
    return value, None


def read_jsonl(path: Path) -> tuple[list[dict], str | None]:
    """Read an append-only JSONL ledger. Returns ``(records, problem)``; never raises.

    The third rung of the same ladder, for the run artifacts that are NOT one
    document: ``handoffs.jsonl``, ``spend.jsonl``, ``spawns.log``. It exists
    here rather than in each reader for the reason the module comment gives —
    the raise set of a read is decided ONCE — and because three separate
    line-loops had already grown three separate opinions about what a torn
    line means (``foundry_spawn._latest_teammate_dispatches`` skips it,
    ``measure-run._read_spend`` skips it, and the report generator would have
    been the third to re-decide).

    THE ASYMMETRY IS DELIBERATE, and it is the whole reason this is not just
    ``read_text_file`` plus ``json.loads`` at each call site:

      * bytes that will not DECODE are a PROBLEM. The file is corrupt, the
        caller names it, and nothing is guessed at — same rule as every reader
        above.
      * a single LINE that will not parse is SKIPPED, silently. These ledgers
        are appended by many concurrent agents under an ``flock``, so a torn
        final line is an ordinary crash artifact; failing the read over it
        would cost the other eighty-four records, which is a strictly worse
        answer than reporting eighty-four of eighty-five.

    A line that parses to something other than an object is skipped on the
    same grounds: every ledger record in this protocol is a mapping, and a
    bare string on line 40 is the same class of debris as a torn one.

    ``records`` is ``[]`` whenever there is a problem, so a caller that
    degrades rather than refuses can ignore the second element entirely — the
    same shape ``read_document`` holds. An ABSENT ledger is not a problem: a
    run legitimately has ledgers no agent has written to yet, and conflating
    "nobody spent anything" with "the spend ledger is corrupt" is exactly the
    confusion the report's refusal rule turns on.
    """
    raw, problem = read_text_file(path)
    if problem is not None:
        return [], problem
    records: list[dict] = []
    for line in raw.splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if isinstance(record, dict):
            records.append(record)
    return records, None


def handoffs_wall_clock_seconds(run_dir: Path) -> tuple[float | None, str | None]:
    """Seconds spanned by `handoffs.jsonl`, or ``(None, why)``. ONE derivation.

    Returns ``(seconds, problem)`` and never raises. ``seconds`` is the delta
    between the earliest and latest parseable ``timestamp`` in the ledger;
    ``problem`` names why there is no number when there is none.

    WHY THIS LIVES IN THE LEAF MODULE (D-087)
    -----------------------------------------
    Two surfaces published NFR-001's wall-clock column and they printed
    different things for the same unmeasured run. ``measure-run.py`` emitted
    ``wall_clock_seconds: 0.0``; ``foundry_report._wall_clock_minutes`` emitted
    ``null`` and its docstring forbade the other spelling by name — "a run that
    took no measurable time and a run nobody measured are different facts, and
    NFR-001's comparison is unreadable if they print the same". Driven on a run
    with no ``handoffs.jsonl``: the CLI fabricated exactly the number the report
    module refuses to fabricate, and NFR-001's comparison is the one surface
    where the two sit in the same table.

    So the read is hosted HERE, beside ``derive_cycle_count``, for the same
    reason D-036 put that one here: this is the only module both readers
    already import, and it is reachable from either without closing a cycle in
    the import graph.

    TWO ENDPOINTS OR NOTHING. A single record cannot bound a span, so one
    parseable timestamp is ``(None, ...)`` and not ``0.0`` — that is the
    report's rule, adopted whole, because it is the rule the requirement's own
    wording names.

    ``datetime`` IS IMPORTED INSIDE THE FUNCTION. The leaf contract at the top
    of this file is about the MODULE's import list — it is what lets
    ``measure-run.py`` state a stdlib-only, package-free import cost and what
    keeps ``foundry.py`` and ``foundry_orchestrator.py`` free of an import
    cycle. A call-time import adds nothing to that list, so the contract reads
    exactly as it did before this function existed. The alternative — hand-
    parsing ISO-8601 to avoid the import — would be a third timestamp parser in
    a module written to end second derivations.
    """
    from datetime import datetime

    records, problem = read_jsonl(run_dir / "handoffs.jsonl")
    if problem is not None:
        return None, problem
    if not records:
        return None, "handoffs.jsonl is absent or empty"
    moments: list[datetime] = []
    for record in records:
        raw = record.get("timestamp")
        if not isinstance(raw, str) or not raw:
            continue
        try:
            moments.append(datetime.fromisoformat(raw.replace("Z", "+00:00")))
        except ValueError:
            continue
    if len(moments) < 2:
        return None, "handoffs.jsonl carries fewer than two parseable timestamps"
    span = (max(moments) - min(moments)).total_seconds()
    if span < 0:
        return None, "handoffs.jsonl timestamps span a negative interval"
    return float(span), None


def derive_cycle_count(run_dir: Path) -> dict:
    """The run's GRIND cycle count. ONE derivation, read by every surface.

    Returns, and never raises::

        {"count": int | None,     # GRIND cycles that OPENED (see the halt rule)
         "index": int | None,     # the server's 0-based counter at the end
         "halted": bool,          # state.json carries a halt record
         "sources": {"state_cycle": int | None,
                     "rollup_highest": int | None,
                     "defect_max_cycle": int | None},
         "stale_counter": bool,   # the roll-up proves the counter did not move
         "problems": [str]}       # artifacts that exist and would not read

    WHY THIS LIVES IN THE LEAF MODULE (D-036)
    -----------------------------------------
    There were two derivations of this one fact and they disagreed.
    ``measure-run.py::_extract_per_run`` published ``_reconcile_final_cycle_index
    + 1``; ``foundry_report.py::_baseline_comparison_section`` published the raw
    ``state.json["cycle"]``. They differ by exactly one, which is enough to
    straddle ``CONVERGENCE_TARGET["grind_cycles"]``: a run at index 12 passed
    the report's ``<= 12`` and failed the CLI's, so the same archive met the
    effort's own target on one surface and missed it on the other.

    So it is hosted once, HERE, because this is the only module both readers
    already import and it is reachable from either without closing a cycle in
    the import graph. The derivation needs ``json`` and ``pathlib`` and nothing
    else, so the leaf contract at the top of this file holds unchanged — a
    caller that needs a name from ``vocab`` passes it in or reads it itself.

    WHY THREE SOURCES AND NOT ONE (D-022)
    -------------------------------------
    ``state.json["cycle"]`` alone reported thunder-viper — a 22-cycle run, the
    baseline the whole convergence target exists to beat — as ONE cycle, and
    ``measure-run.py`` duly certified it ``meets_target: true``. Its counter was
    written once as 0 and never incremented (survey/data.md FI-1), and it wrote
    no ``stream-rollup.json`` at all, so both of the old sources were blind.

    The defect ledger is not blind: every filing stamps the cycle it was filed
    in, so the highest is a floor on the cycles the run executed. Adding it as a
    third source reproduces BOTH known baselines from their own archives rather
    than from a constant — thunder-viper 21 + 1 = 22, which is
    ``THUNDER_VIPER_BASELINE["grind_cycles"]``, and grand-vulture 17 + 1 = 18,
    which is NFR-001's "18 cycles, 168 defects". Two independent confirmations
    that the formula measures the thing it names.

    ``stale_counter`` stays keyed to the ROLL-UP alone, never to the defect
    ledger. The roll-up is keyed BY the server counter (FR-005 / ST-001), so a
    roll-up key above the counter is direct proof the counter is stale and
    worth naming. A defect's ``cycle`` is stamped at filing time and, on a
    pre-release archive, by whichever door filed it — it proves cycles ran
    without indicting the counter. Reporting it as staleness would make every
    healthy 4.7.3-era archive exit nonzero, which is the over-firing
    calibration D-034 already had to undo.

    WHY A HALTED RUN IS ``index`` AND NOT ``index + 1`` (D-175)
    -----------------------------------------------------------
    The counter advances at ``inspect_start`` and nowhere else, so it counts
    INSPECTs, and ``index + 1`` is the GRIND that opened AT that counter. On a
    halted run that GRIND is precisely the one the cap refused to open, so
    adding it publishes a cycle the run never ran.

    Driven end to end at the real doors with ``--max-cycles 2``: the
    ``Foundry-Phase('grind_start')`` that would open GRIND 3 returned ok, wrote
    ``phase`` HALTED and the reason "--max-cycles 2 reached: opening GRIND
    cycle 3 would exceed it" — so two GRIND cycles ran, which is
    ``_halt_if_capped``'s own stated arithmetic ("GRIND 1 opens at counter 0,
    GRIND 2 at counter 1") — and generated the report inside that same call,
    whose ``baseline_comparison.current.grind_cycles`` read 3 and whose
    REPORT.md rendered "| GRIND cycles | 22 | | 12 | 3 |" beside
    ``run.max_cycles`` 2 in the same document. One transition wrote both
    numbers and they contradicted each other about the one quantity the cap is
    defined over.

    So the halt subtracts exactly the GRIND it prevented, and nothing else:
    ``count`` is ``index`` on a halted run and ``index + 1`` on every other,
    which is why both published baselines still reproduce from their own
    archives (thunder-viper 21 + 1 = 22, grand-vulture 17 + 1 = 18 — neither
    halted).

    THE HALT IS READ FROM ``halted_at_cycle`` / ``halted_reason``, NOT FROM
    ``phase``. ``_halt_if_capped`` writes all three in one transaction, and
    these two are DATA while the phase value is a vocabulary token — and the
    leaf contract at the top of this file forbids importing ``vocab``, so
    keying on the phase would mean re-typing ``RUN_PHASE_HALTED`` here, which
    is the hand-copied-enum drift the house rule bans outright.

    Re-basing the whole derivation on ``phase_history``'s F3 entries was the
    alternative and it is worse: thunder-viper's counter never moved and its
    history carries no per-cycle F3 stamps, so the two archives that calibrate
    this function would stop reproducing their own published numbers. Fixing
    one halted run by changing what every run means is not a fix.

    ``count`` is None only when NO source could supply a number: "cannot say"
    and "one cycle" are different answers, and the caller that turns this into
    a target verdict has to be able to tell them apart.
    """
    problems: list[str] = []

    def _cycle(value: object) -> int | None:
        # ``bool`` is an ``int`` subclass and ``True`` is not cycle 1.
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            return None
        return value

    state, problem = read_document(run_dir / "state.json")
    if problem is not None:
        problems.append(problem)
    state_cycle = _cycle(state.get("cycle"))

    rollup_highest: int | None = None
    rollup, problem = read_document(run_dir / "stream-rollup.json")
    if problem is not None:
        problems.append(problem)
    cycles = rollup.get("cycles")
    if isinstance(cycles, dict):
        for raw_key in cycles:
            try:
                key = int(raw_key)
            except (TypeError, ValueError):
                continue
            if key >= 0 and (rollup_highest is None or key > rollup_highest):
                rollup_highest = key

    defect_max: int | None = None
    defects, problem = read_document(run_dir / "defects.json")
    if problem is not None:
        problems.append(problem)
    records = defects.get("defects")
    if isinstance(records, list):
        for record in records:
            if not isinstance(record, dict):
                continue
            value = _cycle(record.get("cycle"))
            if value is not None and (defect_max is None or value > defect_max):
                defect_max = value

    # D-175 — the two fields `_halt_if_capped` writes beside `phase` HALTED, in
    # the same transaction. Either one present is a halt: a run may be halted
    # by hand with only the reason recorded, and a report that then claimed the
    # refused GRIND is the defect either way.
    halted_reason = state.get("halted_reason")
    halted = _cycle(state.get("halted_at_cycle")) is not None or bool(
        isinstance(halted_reason, str) and halted_reason.strip()
    )

    known = [v for v in (state_cycle, rollup_highest, defect_max) if v is not None]
    index = max(known) if known else None
    return {
        # The GRIND at `index + 1` opened, UNLESS the halt is what stopped it.
        "count": None if index is None else index + (0 if halted else 1),
        "index": index,
        "halted": halted,
        "sources": {
            "state_cycle": state_cycle,
            "rollup_highest": rollup_highest,
            "defect_max_cycle": defect_max,
        },
        "stale_counter": (
            rollup_highest is not None
            and rollup_highest > (state_cycle if state_cycle is not None else -1)
        ),
        "problems": problems,
    }


# --------------------------------------------------------------------------- #
# D-182 — WHICH KEYS IN A CYCLE BUCKET ARE STREAM RECORDS. ONE DEFINITION.
#
# ``stream-rollup.json``'s ``cycles[<cycle>]`` bucket holds two kinds of key,
# because it has two writers. ``_record_stream_rollup`` accumulates one STREAM's
# tranches under its wire id; ``_record_cycle_facts`` writes the C-6 facts that
# describe the CYCLE ITSELF -- ``inspect_mode``, ``inspect_rule``,
# ``stream_scope``, ``evidence_sweep``, and a nested ``temper_entry`` sub-bucket
# -- BESIDE them, in the same mapping. Three modules walk that bucket and every
# one of them has to know the difference: ``foundry_orchestrator``
# ``_stream_dispatch_cycles``, ``foundry_report``
# ``_read_unreported_dispatches``, and ``measure-run.py``
# ``_read_stream_rollup``.
#
# Two of the three grew the rule by hand and the third never learned it. Driven
# at 056f51a as a real process,
# ``python3 plugins/foundry/scripts/measure-run.py foundry-archive/daring-orca``
# exited 1 with eight failure tokens -- PHASE9_SCHEMA_INVALID x4 for the two
# string-valued keys of cycles 10 and 11, and
# PHASE9_UNKNOWN_STREAM:{stream_scope,evidence_sweep} x2 -- declaring the run's
# own well-formed roll-up a broken artifact, while the SAME command on
# thunder-viper (an archive predating the C-6 keys) exited 0. AC-039's
# acceptance instrument was rejecting the shape the spec's own Data Model had
# widened the document to.
#
# So the rule is stated ONCE, here, in the leaf module all three already import
# -- the same home and the same reason as ``unreported_dispatch_pairs``
# (D-047 / D-048): two derivations of one question are exactly how the two came
# to disagree, and a third derivation is not an answer to that.
#
# THE TEST IS ON THE VALUE, NEVER A DENYLIST OF KEY NAMES. A denylist of the
# four keys the filing names would already have been wrong: ``temper_entry`` is
# a fifth, and the next C-6 field would be a sixth. A stream tranche is exactly
# a mapping carrying ``records``, because ``_record_stream_rollup`` appends to
# ``entry["records"]`` on every mark and no cycle-level fact has that key.
# --------------------------------------------------------------------------- #


def is_stream_record(entry: object) -> bool:
    """True when a ``stream-rollup.json`` cycle-bucket VALUE is a stream tranche.

    The one test that separates ``cycles[<cycle>][<wire stream id>]`` from the
    cycle-level facts sitting beside it. Pure and total: it takes the VALUE and
    not the key, and returns False rather than raising on any JSON type.

    A caller that must also tell a CORRUPT tranche from a cycle-level fact
    resolves the KEY against the stream roster after this returns False -- a key
    the roster knows whose value is not a tranche is a broken record, while a
    key the roster does not know is simply not a stream. That second half stays
    at the call site that already holds the resolver: this module imports
    ``json`` and ``pathlib`` and nothing else, deliberately (see the module
    docstring), so ``vocab`` is not reachable from here and must not become so.
    """
    return isinstance(entry, dict) and "records" in entry


def unreported_dispatch_pairs(
    *,
    dispatch_rows: list[dict],
    stream_roster: dict[str, list[str]],
    spend_rows: list[dict],
    phase_of_dispatch: dict[str, str],
    agent_id_of=None,
) -> list[dict]:
    """Every dispatched ``(agent, phase)`` with no spend line for THAT phase.

    Returns a sorted ``[{"agent": str, "phase": str}]``, and never raises.

    WHY THIS LIVES IN THE LEAF MODULE (D-047 / D-048)
    -------------------------------------------------
    Two surfaces answer this one question — ``Foundry-Next`` renders
    ``foundry_orchestrator._unreported_dispatches`` and the F6 report renders
    ``foundry_report._read_unreported_dispatches`` — and they had two
    derivations of it. D-013 had already unified the agent-ID spelling between
    them; what it unified was the WRONG rule, adopted verbatim on both sides so
    that they agreed with each other while both disagreeing with FR-022.

    So the rule is hosted once, HERE, for the reason ``derive_cycle_count``
    is: this is the only module both readers already import, and it is
    reachable from either without closing a cycle in the import graph. The
    derivation needs ``json`` and ``pathlib`` and nothing else, so the leaf
    contract at the top of this file holds unchanged — a caller that needs a
    name from ``vocab``, or the dispatch-verb mapping ``foundry_orchestrator``
    owns, reads it itself and passes it in.

    THE TWO THINGS THE OLD RULE GOT WRONG
    -------------------------------------
    D-047 — the axis. The old clause was "an agent that reported spend in ANY
    phase is a reported agent", so casting-3 dispatched at two phases and
    accounted for at one of them appeared nowhere. FR-022 verbatim: "the report
    shows N agents unreported per phase so the gap is visible". A pair is
    unreported here when NO spend row carries that exact ``(agent, phase)``,
    full stop; the agent-wide clause is gone.

    D-048 — the vocabulary. That agent-wide clause was not a judgement call; it
    was a workaround. ``spawns.log`` records the DISPATCH VERB (``cast``,
    ``grind``) while ``Foundry-Spend``'s schema documents the phase as "e.g.
    F1, F2, F3", so the exact pair could NEVER match for a teammate dispatch
    and the fallback was the only clause that ever cleared one. Reconciling the
    two vocabularies through ``phase_of_dispatch`` is what makes the exact test
    the workable one, and it is also what stops ``by_phase`` growing a phantom
    bucket keyed by a verb that is not a phase.

    Args:
        dispatch_rows: ``spawns.log`` rows. Each contributes ``(agent, phase)``
            where the phase is the row's dispatch verb mapped through
            ``phase_of_dispatch``. The agent id must be the one
            ``foundry_spawn._agent_id_for_casting`` mints — pass rows already
            carrying it under ``agent``, or hand in ``agent_id_of`` and let a
            row supply its ``casting_id``. It is never re-spelled here: this
            module imports nothing from the package, and a local copy of that
            spelling is the exact drift D-013 closed.
        stream_roster: ``{run phase id: [stream agent ids]}`` — the F2 streams
            recorded in ``stream-rollup.json``, which appear in no
            ``spawns.log`` row at all. Its keys are already run phase ids, so
            they are NOT mapped.
        spend_rows: ``spend.jsonl`` rows. A row clears exactly the
            ``(agent, phase)`` it names.
        phase_of_dispatch: ``{dispatch verb: run phase id}``, owned by
            ``foundry_orchestrator`` and passed in. A verb the mapping does not
            know is kept AS SPELLED rather than dropped: dropping it would hide
            a dispatch, which is the under-reporting FR-022 exists to prevent,
            while an oddly named bucket is a visible sign the mapping has gone
            stale.
        agent_id_of: optional callable turning a ``casting_id`` into an agent
            id, for a caller handing over raw ``spawns.log`` rows. A row with
            neither an ``agent`` key nor a resolvable ``casting_id`` is skipped
            — there is no id to report it under.

    ADVISORY, ALWAYS (AC-034). Nothing here refuses, and no gate reads the
    result. An unreported dispatch is a gap in the MEASUREMENT, not a defect in
    the build, and a run that could not reach DONE over a missed bookkeeping
    call would teach the lead to stop measuring.
    """
    reported: set[tuple[str, str]] = set()
    for row in spend_rows:
        if not isinstance(row, dict):
            continue
        agent = row.get("agent")
        phase = row.get("phase")
        if isinstance(agent, str) and agent and isinstance(phase, str) and phase:
            reported.add((agent, phase))

    dispatched: set[tuple[str, str]] = set()
    for row in dispatch_rows:
        if not isinstance(row, dict):
            continue
        agent = row.get("agent") or row.get("agent_id")
        if not agent and agent_id_of is not None:
            casting_id = row.get("casting_id")
            # ``bool`` is an ``int`` subclass, and ``casting-True`` is not an
            # agent. The same guard the report's reader has always carried.
            if not isinstance(casting_id, bool) and isinstance(
                casting_id, (int, str)
            ):
                token = str(casting_id).strip()
                if token:
                    agent = agent_id_of(token)
        verb = row.get("phase")
        if not agent or not isinstance(verb, str) or not verb:
            continue
        dispatched.add((str(agent), phase_of_dispatch.get(verb, verb)))

    for phase, agents in stream_roster.items():
        if not isinstance(phase, str) or not phase:
            continue
        for agent in agents or []:
            if isinstance(agent, str) and agent:
                dispatched.add((agent, phase))

    return [
        {"agent": agent, "phase": phase}
        for agent, phase in sorted(dispatched)
        if (agent, phase) not in reported
    ]


def unreported_dispatch_summary(
    *,
    dispatch_rows: list[dict],
    stream_roster: dict[str, list[str]],
    spend_rows: list[dict],
    phase_of_dispatch: dict[str, str],
    agent_id_of=None,
    cycles_of_agent: dict[str, list[str]] | None = None,
) -> dict:
    """The unreported-dispatch COUNTS, derived once, for every surface.

    Returns, and never raises::

        {"count": int,                            # unreported PAIRS
         "dispatched": int,                       # every dispatched pair
         "reported": int,                         # dispatched - count
         "pairs": [{"agent": str, "phase": str}], # the unreported pairs
         "by_phase": {phase: [agent ids]},
         "by_cycle": {str(cycle): [agent ids]},
         "pairs_without_cycle": [{"agent": str, "phase": str}]}

    The first five arguments are ``unreported_dispatch_pairs``' arguments,
    passed straight through — that function is still the RULE and this one is
    the arithmetic over it, so there is no second opinion about what an
    unreported dispatch IS.

    WHY THE COUNTS MOVED HERE TOO (D-163)
    -------------------------------------
    ``unreported_dispatch_pairs`` ended the two derivations of the RULE
    (D-047 / D-048) and left two derivations of the NUMBER. One document
    published both: ``report.json`` carried
    ``spend_per_phase_and_cycle.total.unreported: 0`` and
    ``by_phase {"F1": {"unreported": 0}}`` beside
    ``unreported_dispatches {"count": 1, "by_phase": {"F1": ["casting-2"]}}``,
    on a run with exactly one unreported dispatch. The spend section copied its
    number out of ``state.json.spend``, on the stated premise that the
    orchestrator's roll-up was "the orchestrator's derivation and the only
    one"; but the orchestrator applies that derivation to a THROWAWAY DEEP COPY
    inside its display path ("a reader that mutated the document it read would
    make every display call a write"), so the value that reached the persisted
    document was the ``0`` its empty-bucket seed put there. A field seeded to
    zero and never incremented is not a derivation, and the report published it
    as one beside the true count.

    So both surfaces now call THIS, and the number they publish is the same
    integer because it is the same object. Hosting it here rather than in
    either caller is ``derive_cycle_count``'s reason unchanged: this is the
    only module both readers already import, and the derivation needs ``json``
    and ``pathlib`` and nothing else, so the leaf contract at the top of this
    file holds — the verb mapping and the agent-id spelling are still the
    caller's to pass in.

    THE PAIR AND THE CYCLE ARE DIFFERENT AXES, AND SAYING SO IS THE POINT
    --------------------------------------------------------------------
    ``count`` and ``by_phase`` are keyed on the PAIR, because the pair is what
    FR-022 asks about: "N agents unreported per phase". EVERY unreported pair
    is on that axis. ``by_cycle`` is keyed on the cycle stamps
    ``cycles_of_agent`` supplies, and one F2 stream agent unreported across
    three cycles is ONE pair listed under THREE cycles. So
    ``sum(len(v) for v in by_cycle.values())`` need not equal ``count``, BY
    CONSTRUCTION, and a caller writing both into one table has to render them
    as the different measurements they are rather than reconciling them.

    THE CYCLE AXIS IS ALSO NARROWER, AND ``pairs_without_cycle`` SAYS SO
    -------------------------------------------------------------------
    D-175's sibling, D-172. The two axes differ in COVERAGE as well as in
    keying: a pair can only reach ``by_cycle`` if ``cycles_of_agent`` carries
    its agent, and today the only per-dispatch record in the archive that
    stamps a cycle is ``stream-rollup.json``'s own cycle bucket — so both
    callers can supply stamps for the F2 streams and for nothing else.
    ``spawns.log`` records a teammate dispatch as timestamp, casting_id, phase,
    wave and prompt_hash, with no cycle anywhere in the row, so a CAST or GRIND
    teammate CANNOT be attributed to a cycle here however many cycles it ran
    in.

    Driven over a live archive: 19 unreported pairs, 14 of them CAST and GRIND
    teammates such as casting-1@F1 and casting-1@F3, and ``by_cycle`` named
    ZERO teammates in any of cycles 0 through 9 — only the five F2 stream
    agents ever appeared there — while the report's prose beside that column
    stated it "names the cycles those agents were dispatched in", which was
    false for 14 of the 19. The blindness is structural and the sentence
    described a derivation the archive never made.

    So the pairs the cycle axis cannot carry are RETURNED, not dropped: a
    caller rendering the cycle column has the number that makes its zeros
    readable, and no caller has to re-derive the axis's membership to find out
    what is missing from it. Fabricating a stamp — correlating spawn timestamps
    against phase windows, say — was the alternative and it is the same defect
    in a new place: the filing is that the axis is derived from a proxy, and a
    second proxy does not answer it. What would answer it is a real cycle in
    the dispatch record, which is the spawn writer's field to add.

    Args:
        dispatch_rows, stream_roster, spend_rows, phase_of_dispatch,
        agent_id_of: exactly as ``unreported_dispatch_pairs`` documents them.
        cycles_of_agent: ``{agent id: [cycle stamps]}`` for the agents that
            have one — the F2 stream dispatch cycles, which
            ``foundry_orchestrator`` builds as ``_stream_dispatch_cycles`` and
            the report builds from ``stream-rollup.json``'s own cycle keys.
            Omitted, ``by_cycle`` is empty and every pair is in
            ``pairs_without_cycle``: no cycle was supplied, so none is claimed.
            Stamps are stringified so the keys match the ``by_cycle`` spelling
            C-4 uses in ``state.json.spend``.

    ADVISORY, ALWAYS (AC-034), the same as the rule it counts. Nothing here
    refuses and no gate reads the result.
    """
    common = {
        "dispatch_rows": dispatch_rows,
        "stream_roster": stream_roster,
        "phase_of_dispatch": phase_of_dispatch,
        "agent_id_of": agent_id_of,
    }
    pairs = unreported_dispatch_pairs(spend_rows=spend_rows, **common)
    # The DENOMINATOR is the same rule asked with an empty ledger — "every
    # dispatched pair, nothing cleared" — rather than a second walk of the two
    # dispatch sources here. A count and a list that disagreed about what a
    # dispatch IS is the shape this whole section keeps being fixed for.
    dispatched = unreported_dispatch_pairs(spend_rows=[], **common)

    by_phase: dict[str, list[str]] = {}
    by_cycle: dict[str, list[str]] = {}
    # D-172 — the complement of the cycle axis, off the SAME walk that builds
    # it. Deriving it in a caller instead would be a second opinion about which
    # agents the axis covers, which is the shape this whole section keeps being
    # fixed for.
    without_cycle: list[dict] = []
    for pair in pairs:
        by_phase.setdefault(pair["phase"], []).append(pair["agent"])
        stamps = (cycles_of_agent or {}).get(pair["agent"]) or []
        for stamp in stamps:
            by_cycle.setdefault(str(stamp), []).append(pair["agent"])
        if not stamps:
            without_cycle.append(pair)

    return {
        "count": len(pairs),
        "dispatched": len(dispatched),
        "reported": len(dispatched) - len(pairs),
        "pairs": pairs,
        "by_phase": {k: sorted(set(v)) for k, v in sorted(by_phase.items())},
        "by_cycle": {k: sorted(set(v)) for k, v in sorted(by_cycle.items())},
        "pairs_without_cycle": without_cycle,
    }


def document_refusal(path: Path, problem: str) -> dict:
    """The house named refusal for an unreadable document, shaped ONCE.

    Mirrors ``foundry_spawn``'s ``_manifest_shape_error`` exactly — ``ok:
    False``, an ``error`` naming the fault and the file, a ``hint`` naming the
    action — because the two answer the SAME question one rung apart: this one
    when the bytes could not be read, that one when they read fine and the
    shape is wrong. Both spawn doors return this, so the lead cannot learn two
    different stories about one file depending on which door it walked
    through (D-132's property, re-derived one rung down for D-137).
    """
    return {
        "ok": False,
        "error": f"{problem}: {path}",
        "hint": (
            "Repair or delete the named file, then retry. A corrupt run "
            "artifact is never silently overwritten, nor guessed at."
        ),
    }


def set_active_run(name: str) -> None:
    global _active_run_name
    _active_run_name = name


def get_active_run() -> str | None:
    return _active_run_name


def clear_active_run() -> None:
    global _active_run_name
    _active_run_name = None


def get_run_dir(project_root: str, name: str | None = None) -> Path | None:
    """Return the run directory for the given or active run.

    Returns None if no run is active and no name is provided.
    """
    n = name or _active_run_name
    if not n:
        return None
    return Path(project_root) / ARCHIVE_DIR / n
