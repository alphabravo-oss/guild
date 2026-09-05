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
# both ``foundry.py`` and the orchestration package already import, and which
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

#: The archive-schema GENERATION every artefact under ``ARCHIVE_DIR`` is
#: written at, and the marker ``scripts/migrate-archive.py`` writes when it
#: finishes bumping an older one (CT-018).
#:
#: fallout C-023 — THE LEAF IS THE HOME BECAUSE THE INTEGER HAD FOUR. It was
#: spelled independently in ``foundry.py#ARCHIVE_SCHEMA_VERSION`` (the value
#: ``foundry_init`` writes into state.json),
#: ``scripts/migrate-archive.py#ARCHIVE_SCHEMA_VERSION`` (the module that does
#: the bump), ``foundry_validate.py#REQUIREMENT_IDS_SCHEMA_FLOOR`` (the floor
#: at which a manifest is required to carry ``requirement_ids``) and a literal
#: in ``tests/test_migrate_archive.py``. Four copies of one generation number
#: is the shape where a writer bumps and a reader does not, so the archive a
#: run just wrote reads as one generation to the tool that made it and another
#: to the tool that migrates it.
#:
#: It lives HERE and not in ``schemas/vocab.py`` for the same reason
#: ``ARCHIVE_DIR`` does: this is a run-artefact LAYOUT fact, not a closed
#: vocabulary of values a door may accept. And it lives in the leaf because
#: the leaf is the one module every consumer already imports — ``foundry.py``,
#: ``foundry_validate.py`` and ``migrate-archive.py`` on both its installed
#: and its source-tree branch — with no layering rule in the way, and because
#: this module imports nothing from the package itself, so reaching it can
#: never close a cycle in the import graph.
#:
#: Bumping it is a MIGRATION, never an edit: raise it here and give
#: ``migrate-archive.py`` the step that carries a schema-N archive to N+1.
ARCHIVE_SCHEMA_VERSION = 4


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
    ``measure-run.py::_extract_per_run`` published its own reconciled 0-based
    cycle INDEX plus one; ``foundry_report.py::_baseline_comparison_section``
    published the raw
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
# tranches under its wire id; ``_record_cycle_rollup`` writes the C-6 facts that
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


# --------------------------------------------------------------------------- #
# GI-024 / FR-008 / OT-010 — EVERY DERIVED RUN TABLE HAS ONE IMPLEMENTATION,
# AND IT IS HERE.
#
# `survey/architecture.md` §3.2 inventoried the duplication and §3.3 counted the
# renderings: "Three renderings, two derivations." The spend bucket shape was
# typed three times (the orchestrator's persisted seed, the report's derived
# seed, `scripts/measure-run.py`'s third copy); the spend roll-up was aggregated
# three times off two different ledgers; the inspect-mode census, the
# unreported-dispatch input assembly, the escalated-class rows, the REPORT.md
# `## ` heading rule, `_now`, `_current_cycle`, `_prove_is_clean`, the cycle
# sort key and `_as_count` each had two. Holmes `share-2` names the pattern:
# "byte-identical `_current_cycle` / `_server_cycle` ... each kept as a
# 'deliberate second copy' instead of living in the leaf module built for that".
#
# The previous fix for the spend pair made it worse in a way worth recording:
# `foundry_report` imported `_overlay_unreported` BACK OUT of the orchestrator
# through a function-local import, "closing the import cycle rather than sharing
# the rule". Both halves of that pair now live here, so the report reaches only
# the leaf and its own module header — "`schemas.vocab` and
# `tools.foundry_state`, and nothing else from the package" — is true rather
# than aspirational.
#
# THE LEAF CONTRACT IS UNCHANGED, AND IT IS WHAT SHAPES THESE SIGNATURES.
# ---------------------------------------------------------------------
# The module docstring's contract — `json` and `pathlib` and nothing from the
# package — is what lets `scripts/measure-run.py` state a stdlib-only,
# package-free import cost (`survey/infra.md` §9) and what keeps every caller
# free of an import cycle. So a rule that needs a name from `vocab` (the
# INSPECT modes, the escalation statuses, the escalation-status resolver) or a
# mapping the orchestrator owns takes it as an ARGUMENT, exactly as
# `unreported_dispatch_pairs` above takes `phase_of_dispatch` and `agent_id_of`.
# The rule moves here; the vocabulary stays where it is declared.
#
# AND THERE IS NO SPEC-PATH RESOLVER HERE. Holmes `share-7` proposes one
# ("re-inlined in three modules"), and this run routes it into
# `tools/artifacts.py` instead. `prove_is_clean` below therefore takes the
# requirement COUNT rather than resolving the spec — a fourth copy of that
# ladder is the thing the proposal exists to prevent, not a step toward it.
#
# EVERY READER IS TOTAL. It returns an empty derived table rather than raising,
# exactly as `unreported_dispatch_summary` does, and documents its return shape
# in its own docstring. A caller that must TELL the operator which file is
# broken uses the artifact guard or the `(value, problem)` readers above; it
# never inspects one of these return values for that, because an empty table
# cannot distinguish "absent" from "corrupt" by design.
# --------------------------------------------------------------------------- #

#: D-048 — the dispatch VERB a teammate was handed out under, mapped to the RUN
#: PHASE its spend is bucketed under. `spawns.log` records `cast` and `grind`
#: while `Foundry-Spend` records `F1` and `F3`, so the exact `(agent, phase)`
#: pair could never match a teammate dispatch until the two vocabularies were
#: reconciled through this table.
#:
#: DECLARED HERE, not in the module that owns the dispatch side, because THREE
#: surfaces read it — `Foundry-Next`, the report and (from casting 3)
#: `scripts/measure-run.py` — and the report already reached back into the
#: orchestrator through a function-local import plus a `getattr(..., {})`
#: degradation to get at it. A constant behind a `getattr` default is a
#: constant that can silently go missing; a constant in the leaf both readers
#: already import cannot.
#:
#: fallout D-013 — ITS HOME IS `schemas/vocab.py`, AND THE MOVE IS NOT THIS
#: COMMIT'S. This is a closed vocabulary (the two dispatch verbs `spawns.log`
#: records, and the run phase each maps onto), and the module convention is
#: that closed vocabularies live in `schemas/vocab.py` and every consumer
#: derives from them. It cannot move from here in isolation, for a reason that
#: is structural rather than a preference:
#:
#:   * this module may not import it back. The leaf contract is `json` and
#:     `pathlib` and nothing from the package — `scripts/measure-run.py`
#:     depends on that being true — and the contract's own rule for exactly
#:     this case is that "a name from `vocab` or a `tools/` module is passed
#:     IN", which is what `unreported_dispatch_summary(phase_of_dispatch=...)`
#:     already does. Nothing in this module reads the mapping.
#:   * deleting it here is not additive. `orchestration/spend.py` imports the
#:     name FROM this module, so a declaration in `vocab.py` and a deletion
#:     here is an ImportError for the whole package until that import is
#:     repointed — and re-declaring it in `vocab.py` while leaving this
#:     binding is a second definition of one name, which the package-wide
#:     single-definition guard refuses by construction.
#:
#: So the move is ONE commit that declares it in `vocab.py` and repoints both
#: consumers at once, and that commit belongs to the casting that owns
#: `orchestration/spend.py`. It is raised as a cross-casting concern rather
#: than half-done here; `tests/test_foundry_state_readers.py` carries the
#: shrink-only inventory of the assembly still derived outside this module.
DISPATCH_PHASE_TO_RUN_PHASE = {"cast": "F1", "grind": "F3"}


def now_iso(*, timespec: str = "auto") -> str:
    """The house UTC timestamp. ONE implementation, one documented precision knob.

    THE RECONCILIATION, STATED RATHER THAN PICKED
    ---------------------------------------------
    The two copies this replaces did NOT agree.
    ``foundry_orchestrator._now`` returned full precision
    (``2026-09-05T04:11:07.482913+00:00``) and ``foundry_report._now`` returned
    ``timespec="seconds"`` (``2026-09-05T04:11:07+00:00``). Collapsing them onto
    one precision is what a silent pick would do, and it would be wrong in one
    direction or the other:

      * onto SECONDS — the ledger writers stamp `handoffs.jsonl`, `spawns.log`
        and `spend.jsonl` with this, and ``handoffs_wall_clock_seconds`` above
        measures a SPAN between two of those stamps. Two records appended
        inside one second would then span 0.0 seconds, which is precisely the
        fabrication that function's docstring refuses by name ("a run that took
        no measurable time and a run nobody measured are different facts").
      * onto MICROSECONDS — `report.json`'s ``generated_at`` and REPORT.md's
        banner are operator-facing, and six digits of noise in a line a human
        reads is what makes a line stop being read.

    So the precision is a NAMED ARGUMENT with the reason recorded here, and the
    two callers keep the answers they already published. What is no longer
    duplicated is the thing that actually drifted: the timezone, the ``utc``
    spelling, and the fact that this is ISO-8601 at all.

    ``datetime`` is imported INSIDE the function for the reason
    ``handoffs_wall_clock_seconds`` states: the leaf contract is about the
    MODULE's import list, and a call-time import adds nothing to it.
    """
    from datetime import datetime, timezone

    return datetime.now(tz=timezone.utc).isoformat(timespec=timespec)


def as_count(value: object) -> int:
    """A non-negative int, or 0. Bools are not counts (``True`` is not 1 here).

    The house integer coercion, spelled once. ``bool`` is an ``int`` subclass
    and ``True`` is not a count of anything — the same guard ``derive_cycle_count``
    applies to a cycle and ``_read_spend`` applied to a token total, which is
    why the two had to agree and had no shared spelling to agree through.
    """
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return 0
    return value


def cycle_sort_key(raw: object) -> tuple[int, object]:
    """Numeric order for cycle keys, with any non-numeric key sorted after.

    ``by_cycle``'s keys are ``str(cycle)`` (C-4 / FR-037), so ``sorted()`` on the
    raw mapping orders cycle 21 before cycle 3 and cycle 10 before cycle 2.
    D-220 filed that against the display; `foundry_report` already ordered both
    of its cycle axes through its own copy and `scripts/measure-run.py` through
    a third, so one document had two orderings depending on which surface
    printed it.

    ``by_phase`` is deliberately safe to pass through here too: phase tokens
    (``"F1"``, ``"F5.5"``) map to ``(1, raw)`` — after every numeric key, and
    among themselves in exactly the lexicographic order ``sorted()`` gives them.
    One key function over both axes, and only the axis that was wrong moves.
    """
    try:
        return (0, int(raw))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return (1, raw)


def current_cycle(run_dir: Path) -> int:
    """The server-owned cycle counter. Never caller-supplied. Never raises.

    Returns 0 for a missing, absent, or malformed value so every reader gets a
    usable integer rather than having to guard the state file's shape.

    "Every reader" is enforced, not aspirational (D-059): a raw
    ``state.json["cycle"]`` read hands on whatever the file holds, and a
    str/None/list/dict raised an unhandled TypeError out of Foundry-Next — the
    mandatory handshake before every phase transition and gate — while -3 and
    2.5 propagated silently into responses and onto every row of a synthesized
    verdict. ``bool`` is excluded for ``derive_cycle_count``'s reason: ``True``
    is not cycle 1.

    fallout D-012 — THIS IS THE ONE READER, AND THE REASON THERE WERE TWO IS
    GONE. ``tools/foundry.py#_server_cycle`` is a byte-equivalent second copy:
    same read, same coercion, same 0 for missing/absent/malformed. Its own
    docstring stated the justification — "the orchestrator imports THIS module,
    so importing back would close a cycle in the import graph" — and that
    justification no longer holds: ``foundry_orchestrator`` is deleted, and
    ``tools/foundry.py`` already imports this module at module top. A second
    copy costs exactly what D-119 cost when the two disagreed: the SAME finding
    filed through Foundry-Defect and through Foundry-Sync landed in different
    cycles, and a class that recurred three straight cycles evaded ST-002
    escalation because mixed-door filing broke the consecutive run.

    THE CONTRACT THE SECOND COPY MUST BE DELETED ONTO, stated here so the
    repoint is a substitution and not a re-decision:

      * the stamp is SERVER-OWNED. A caller-supplied ``cycle`` is never trusted
        against it, and the degraded case resolves to the same deterministic 0
        both filing doors already agreed on for a corrupt CONTAINER — trusting
        the caller there is exactly what ST-001 exists to remove.
      * what the caller CLAIMED is not discarded. Both doors persist it beside
        the stamp as ``declared_cycle``; that is the door's field to write, not
        this reader's, and nothing here reads it.

    The deletion itself is `tools/foundry.py`'s, which is another casting's
    file; `tests/test_foundry_state_readers.py` carries the shrink-only
    inventory that names the one remaining copy and fails on a second.
    """
    state, _ = read_document(run_dir / "state.json")
    return as_count(state.get("cycle"))


def spend_bucket(*, persisted: bool = False) -> dict:
    """An empty spend bucket. ONE shape, with the two variants NAMED.

    ``persisted=True`` is the bucket ``foundry_record_spend`` writes into
    ``state.json.spend``; the default is the DERIVED bucket the report's spend
    table is built from. They differ in exactly two fields and both differences
    are load-bearing, which is why this is one function with an argument rather
    than one shape forced onto two documents:

      * ``records`` and ``minutes`` exist only on the derived bucket. ``records``
        counts ledger ROWS, which is a different number from ``agents`` whenever
        an agent reported twice (D-090); ``minutes`` is ``duration_ms`` divided
        out so an operator is not asked to divide 34_620_000 by 60_000 in their
        head. Neither is persisted, because both are derivable from what is.
      * ``agents`` seeds ``None`` on the derived bucket and ``0`` on the
        persisted one. The persisted document is written by the door that
        counted the agents, so 0 there means "no agents"; the derived table
        FILLS the field from the roll-up, so ``None`` there means "nobody
        recorded how many", and the report refuses to conflate that pair by
        name.

    ``unreported`` seeds 0 in both. Unlike ``agents`` it is DERIVED on every
    run from the dispatch record (D-163), so there is no run on which it is
    unknown: a run with no dispatch record at all has no unreported dispatch,
    and 0 is that fact.
    """
    bucket: dict = {"tokens": 0, "duration_ms": 0}
    if not persisted:
        bucket["minutes"] = 0.0
        bucket["records"] = 0
    bucket["agents"] = 0 if persisted else None
    bucket["unreported"] = 0
    return bucket


def overlay_unreported(spend: dict, summary: dict) -> dict:
    """Write the DERIVED unreported counts onto the C-4 buckets (D-031).

    ``summary`` is ``unreported_dispatch_summary``'s document. This distributes
    its counts across ``by_phase``, ``by_cycle`` and ``total``, and returns the
    same object it was handed. It MUTATES: `_spend_summary` overlays a deep copy
    so a display call is not a write, and `foundry_record_spend` overlays the
    persisted document inside its transaction so `state.json` is repaired in
    place by the next spend call rather than by hand.

    D-162 — TWO DERIVATIONS OF ONE NUMBER, AND THE PAIR WAS THE RIGHT ONE.
    ---------------------------------------------------------------------
    This took the unreported ROW list and incremented ``by_phase`` once per row,
    with ``total["unreported"] = len(rows)``, while the row list re-expands the
    pair set into one row per cycle stamp for every F2 stream agent — so a
    stream agent unreported across nine cycles was NINE rows and ONE pair.
    Driven through the real doors: `Foundry-Next` returned
    ``spend.unreported_count 51`` with ``by_phase {F1 8, F3 6, F2 37}`` while
    the report returned ``count 19`` with ``by_phase {F1 8, F2 5, F3 6}`` — two
    surfaces, one run, one question, two numbers. So the counts are READ off the
    one deriver: ``total`` and each ``by_phase`` bucket count PAIRS, ``by_cycle``
    counts the per-cycle appearances, and nothing here re-derives either axis.

    D-031 — A FIELD INITIALISED AND NORMALISED BUT NEVER WRITTEN. The
    ``unreported`` key existed since C-4 and nothing in the tree incremented it,
    so a consumer read a permanent 0 that could not be distinguished from "every
    dispatch in this phase reported" — the exact opposite of the truth on a run
    where nobody called `Foundry-Spend`. It is DERIVED here rather than
    accumulated at the door because an unreported dispatch is the ABSENCE of a
    record: the number changes when an agent is DISPATCHED, which is a different
    tool's call.

    D-229 — AND AN ALL-ZERO CYCLE BUCKET IS PRUNED. The seeding cannot retract
    what it seeds: a cycle named by the summary gets a bucket, the summary's
    cycle axis then clears the moment the PAIR reports spend — for an F2 stream
    agent that is every cycle stamp at once — and 0/0/0/0 is what is left. An
    ABSENT row is honest where a zero row is a claim. Only the CYCLE axis can
    reach all-zero: `foundry_record_spend` buckets a row under its phase AND its
    server cycle with ``agents`` at least 1, so a phase bucket whose unreported
    clears was written by the very call that cleared it.
    """
    for section in ("by_phase", "by_cycle"):
        if not isinstance(spend.get(section), dict):
            spend[section] = {}
    if not isinstance(spend.get("total"), dict):
        spend["total"] = spend_bucket(persisted=True)

    for bucket in (
        *spend["by_phase"].values(), *spend["by_cycle"].values(), spend["total"],
    ):
        if isinstance(bucket, dict):
            bucket["unreported"] = 0

    for phase, agents in (summary.get("by_phase") or {}).items():
        bucket = spend["by_phase"].setdefault(str(phase), spend_bucket(persisted=True))
        bucket["unreported"] = len(agents)
    for cycle, agents in (summary.get("by_cycle") or {}).items():
        bucket = spend["by_cycle"].setdefault(str(cycle), spend_bucket(persisted=True))
        bucket["unreported"] = len(agents)
    spend["total"]["unreported"] = as_count(summary.get("count"))

    spend["by_cycle"] = {
        key: bucket
        for key, bucket in spend["by_cycle"].items()
        if not (
            isinstance(bucket, dict)
            and all(
                not bucket.get(field)
                for field in ("tokens", "duration_ms", "agents", "unreported")
            )
        )
    }
    return spend


def spend_rollup(
    *,
    spend_rows: list[dict],
    state_rollup: object,
    dispatch_summary: dict | None = None,
) -> dict:
    """NFR-002 — tokens and MINUTES per phase, per cycle, and the run total.

    Returns, and never raises::

        {"records": int,                   # ledger ROWS
         "by_phase": {phase: bucket},
         "by_cycle": {str(cycle): bucket},
         "total": bucket,
         "state_rollup": dict | None,      # state.json.spend, verbatim
         "disagreements": [{"scope", "key", "field", "ledger", "state_rollup"}],
         "unreported_without_cycle": int}

    where a bucket is ``spend_bucket()``'s derived shape. The section PROSE is
    the caller's: this returns the numbers, and the sentence that explains the
    two axes is rendering.

    THE RECONCILIATION OF THE TWO AGGREGATIONS, AS A DECISION AND NOT A PICK
    -----------------------------------------------------------------------
    There were three copies of this arithmetic and the two live ones read
    DIFFERENT SOURCES: `foundry_orchestrator._spend_summary` read
    ``state.json.spend`` (the roll-up the server writes as it goes) while
    `foundry_report._read_spend` re-aggregated ``spend.jsonl`` (the append-only
    ledger). `_read_spend`'s own docstring documents four defects caused by the
    pair disagreeing — D-038, D-090, D-162, D-163 — so the reconciliation is
    stated here rather than silently resolved:

      * THE LEDGER IS THE AUTHORITY for tokens, milliseconds and row counts.
        It is what was actually recorded, row by row; the roll-up is a running
        summary that a crashed or hand-edited run can leave stale.
      * THE ROLL-UP IS THE AUTHORITY for ``agents``, and the ONLY source for it.
        It counts DISTINCT agent ids (D-038 made it so); this reader's row count
        is a different number by construction whenever an agent reported twice,
        and publishing that row count under the name ``agents`` is exactly what
        D-090 filed. ``agents`` is None, never 0, when the roll-up carries none:
        "nobody recorded how many agents" and "no agents ran" are different
        facts.
      * ``unreported`` IS DERIVED, from neither of them (D-163). The roll-up's
        copy of that one field is structurally 0 — the only writer seeds it and
        never increments it, and the overlay that would fill it runs on a
        throwaway copy — so one report published ``total.unreported: 0`` beside
        its own ``unreported_dispatches {"count": 1}``.
      * WHERE THE TWO CAN BE COMPARED THEY ARE, and the difference is NAMED.
        ``disagreements`` carries one entry per bucket and field where the
        roll-up's integer differs from the ledger's, instead of one of the two
        being printed under a single label. A drift signal is only useful while
        it is quiet on a healthy run, which is why ``agents`` is checked against
        the ledger's DISTINCT NAMES and never against its row count.

    THE SEEDING SOURCE IS THE OVERLAY'S VIEW, NOT THE RAW PERSISTED DOCUMENT
    (D-232 / D-235). A bucket the ledger does not know still gets a row, because
    the run where every `Foundry-Spend` call was forgotten is the one whose gap
    most needs a line — but the all-zero cycle buckets `overlay_unreported`
    prunes must not be re-created here, or a report generated after the last
    spend call publishes rows the display does not have. The predicate is not
    restated: the overlay is handed a deep copy and the key sets it hands back
    are what this seeds from. Seeding is ``setdefault`` over buckets the ledger
    loop already built, so a row the LEDGER measured can never be pruned away by
    a stale roll-up.
    """
    dispatch_summary = dispatch_summary or {}
    unreported_by_phase = dispatch_summary.get("by_phase") or {}
    unreported_by_cycle = dispatch_summary.get("by_cycle") or {}
    rollup = state_rollup if isinstance(state_rollup, dict) else None

    def _rollup_bucket(section: str | None, key: str | None) -> dict:
        if rollup is None:
            return {}
        if section is None:
            found = rollup.get("total")
        else:
            group = rollup.get(section)
            found = group.get(key) if isinstance(group, dict) else None
        return found if isinstance(found, dict) else {}

    by_phase: dict[str, dict] = {}
    by_cycle: dict[str, dict] = {}
    total = spend_bucket()
    records = 0
    # Agent NAMES per bucket. NOT published — `agents` is the roll-up's number
    # and only the roll-up's. This is the CHECK.
    seen: dict[tuple[str, str], set[str]] = {}
    for entry in spend_rows:
        if not isinstance(entry, dict):
            continue
        records += 1
        tokens = as_count(entry.get("tokens"))
        duration_ms = as_count(entry.get("duration_ms"))
        phase = entry.get("phase")
        cycle = entry.get("cycle")
        agent = entry.get("agent")

        buckets = [(("run", "total"), total)]
        if isinstance(phase, str) and phase:
            buckets.append((("by_phase", phase),
                            by_phase.setdefault(phase, spend_bucket())))
        if isinstance(cycle, int) and not isinstance(cycle, bool) and cycle >= 0:
            buckets.append((("by_cycle", str(cycle)),
                            by_cycle.setdefault(str(cycle), spend_bucket())))
        for scope, bucket in buckets:
            bucket["tokens"] += tokens
            bucket["duration_ms"] += duration_ms
            bucket["records"] += 1
            if isinstance(agent, str) and agent:
                seen.setdefault(scope, set()).add(agent)

    seed_view = overlay_unreported(
        json.loads(json.dumps(rollup or {})), dispatch_summary
    )
    for section, target, unreported_keys in (
        ("by_phase", by_phase, unreported_by_phase),
        ("by_cycle", by_cycle, unreported_by_cycle),
    ):
        group = seed_view.get(section)
        keys = list(group) if isinstance(group, dict) else []
        keys += list(unreported_keys)
        for key in keys:
            if isinstance(key, str):
                target.setdefault(key, spend_bucket())

    disagreements: list[dict] = []
    for section, key, bucket in (
        *(("by_phase", k, v) for k, v in by_phase.items()),
        *(("by_cycle", k, v) for k, v in by_cycle.items()),
        (None, "total", total),
    ):
        bucket["minutes"] = round(bucket["duration_ms"] / 60_000.0, 2)
        scope = "run" if section is None else section
        recorded = _rollup_bucket(section, key)
        agents = recorded.get("agents")
        bucket["agents"] = (
            agents if isinstance(agents, int) and not isinstance(agents, bool)
            else None
        )
        if section is None:
            bucket["unreported"] = as_count(dispatch_summary.get("count"))
        else:
            bucket["unreported"] = len(
                (unreported_by_phase if section == "by_phase"
                 else unreported_by_cycle).get(key, ())
            )
        ledger_side = {
            "tokens": bucket["tokens"],
            "duration_ms": bucket["duration_ms"],
            "agents": len(seen.get((scope, key), ())),
            "unreported": bucket["unreported"],
        }
        for field, ledger_value in ledger_side.items():
            value = recorded.get(field)
            if (
                isinstance(value, int)
                and not isinstance(value, bool)
                and value != ledger_value
            ):
                disagreements.append({
                    "scope": scope,
                    "key": key,
                    "field": field,
                    "ledger": ledger_value,
                    "state_rollup": value,
                })

    return {
        "records": records,
        "by_phase": {k: by_phase[k] for k in sorted(by_phase)},
        "by_cycle": {k: by_cycle[k] for k in sorted(by_cycle, key=cycle_sort_key)},
        "total": total,
        "state_rollup": rollup,
        "disagreements": disagreements,
        "unreported_without_cycle": len(
            dispatch_summary.get("pairs_without_cycle") or []
        ),
    }


def inspect_mode_rows(*, state: dict, derived: dict, modes) -> dict:
    """FR-023 / AC-036 — EVERY recorded FULL/DELTA decision, per cycle and phase.

    Returns, and never raises::

        {"count": int,                     # DECISIONS, not cycles
         "cycle_count": int,               # cycles THAT CARRY one
         "cycle_axis_length": int | None,  # cycles the RUN ran
         "cycles_without_decision": [int] | None,
         "axis_top": int | None,
         "axis_extended": bool,            # a decision names a cycle above the counter
         "by_mode": {mode: int},
         "per_cycle": {str(cycle): [decision]},
         "entries": [raw entry]}

    ``derived`` is a ``derive_cycle_count`` document and ``modes`` is the mode
    roster — ``vocab.INSPECT_MODES``, passed in because the leaf contract at the
    top of this file forbids importing it. The disclosure SENTENCE is the
    caller's: this publishes the numbers the sentence is built from, including
    ``axis_top`` and ``axis_extended``, so the report and the status display draw
    the same table and no second walk decides what a decision is.

    LAST-ENTRY-WINS FABRICATED A WIDTH FOR THE ORDINARY RUN (D-119). A census
    that keeps one of two answers is not a census, and the collapse was the
    F2-to-F5 path rather than a corner case: the counter does not advance
    entering F5, so TEMPER's entry is stamped with the cycle the preceding F2
    INSPECT already used. One row per DECISION, and neither is dropped.

    THE AXIS IS THE RUN'S, NOT THE LEDGER'S (D-193). ``cycle_count`` counts the
    cycles this ledger has a decision for, and publishing it as the width of the
    axis put two numbers for one axis in one document. The axis comes from
    ``derived["index"]`` — the counter's own highest value — so ``0..index`` is
    exactly the set of counter values a decision could carry. A decision ABOVE
    that top widens the axis (``axis_extended``), because such a decision is
    direct evidence the cycle ran; when the counter cannot be derived at all the
    axis is None rather than falling back on the highest cycle THIS ledger
    names, which would be complete by construction.
    """
    entries = state.get("inspect_modes")
    if not isinstance(entries, list):
        entries = []
    per_cycle: dict[str, list[dict]] = {}
    by_mode = dict.fromkeys(sorted(modes), 0)
    history: list[dict] = []
    decisions = 0
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        history.append(entry)
        cycle = entry.get("cycle")
        if isinstance(cycle, bool) or not isinstance(cycle, int):
            continue
        decision = {
            "cycle": cycle,
            "phase": entry.get("phase"),
            "mode": entry.get("mode"),
            "rule": entry.get("rule"),
            "decided_by": entry.get("decided_by"),
            "required_streams": entry.get("required_streams"),
        }
        per_cycle.setdefault(str(cycle), []).append(decision)
        decisions += 1
        if decision["mode"] in by_mode:
            by_mode[decision["mode"]] += 1

    recorded_cycles = {int(key) for key in per_cycle}
    index = derived.get("index") if isinstance(derived, dict) else None
    highest_recorded = max(recorded_cycles) if recorded_cycles else None
    axis_top: int | None = None
    axis_length: int | None = None
    without: list[int] | None = None
    extended = False
    if isinstance(index, int) and not isinstance(index, bool):
        extended = highest_recorded is not None and highest_recorded > index
        axis_top = index if highest_recorded is None else max(index, highest_recorded)
        axis_length = axis_top + 1
        without = [c for c in range(axis_length) if c not in recorded_cycles]

    return {
        "count": decisions,
        "cycle_count": len(per_cycle),
        "cycle_axis_length": axis_length,
        "cycles_without_decision": without,
        "axis_top": axis_top,
        "axis_extended": extended,
        "by_mode": by_mode,
        "per_cycle": {k: per_cycle[k] for k in sorted(per_cycle, key=cycle_sort_key)},
        "entries": history,
    }


def inspect_decisions(inspect_modes: dict) -> list[dict]:
    """Every decision ``inspect_mode_rows`` recorded, cycle order preserved.

    The flattening lives beside the census rather than in each reader, so the
    markdown table, the JSON section and the archive metrics walk one list built
    one way. Two flattenings of one append-only ledger is how the collapse D-119
    names got two different answers out of the same ``inspect_modes``.
    """
    if not isinstance(inspect_modes, dict):
        return []
    return [
        decision
        for group in (inspect_modes.get("per_cycle") or {}).values()
        for decision in (group or [])
    ]


def stream_rollup_rows(run_dir: Path) -> dict:
    """CT-003 / AC-030 — per (stream, cycle) coverage, with replacements named.

    Returns, and never raises::

        {"cycles": {str(cycle): {stream: {"items_checked", "items_total",
                                          "findings", "record_count",
                                          "replaced_count", "records",
                                          "over_total"}}},
         "cycle_count": int,
         "stream_count": int,
         "replaced": [{"cycle", "stream", "replaced_count"}],
         "over_total": [{"cycle", "stream", "items_checked", "items_total"}],
         "buckets_without_records": [{"cycle", "stream"}],
         "problem": str | None}

    ``is_stream_record`` above is the one test separating a stream tranche from
    the cycle-level facts written beside it (D-182), and it is applied here
    rather than re-spelled — a key whose value is not a tranche is not a stream,
    whatever its name.

    BOTH LEDGER SHAPES, BECAUSE BOTH EXIST (FR-054). The replace semantics
    (``records[]`` keeping history, the top-level totals rewritten to the LAST
    record) arrive with casting 2 and the migration with casting 3. A bucket
    written by the old ADDITIVE writer carries no ``records`` list of its own
    beyond what it accumulated, and ``daring-orca``'s buckets genuinely read
    ABOVE 100%. That is rendered and NAMED — ``over_total`` — never silently
    normalised: a coverage figure quietly clamped to its total is a measurement
    replaced by an assertion, and the whole point of the replace semantics is
    that the run can see which records were superseded.

    ``replaced_count`` is ``len(records) - 1`` and never negative: the FIRST
    record for a (stream, cycle) replaced nothing, and every later one replaced
    exactly the record before it (ST-009). A bucket with no ``records`` key at
    all reports ``record_count`` 0 and is listed in ``buckets_without_records``,
    so "written by the additive writer" stays distinguishable from "recorded
    once".
    """
    document, problem = read_document(run_dir / "stream-rollup.json")
    cycles_out: dict[str, dict] = {}
    replaced: list[dict] = []
    over_total: list[dict] = []
    without_records: list[dict] = []
    streams: set[str] = set()

    cycles = document.get("cycles")
    if isinstance(cycles, dict):
        for cycle_key in sorted(cycles, key=cycle_sort_key):
            bucket = cycles.get(cycle_key)
            if not isinstance(bucket, dict):
                continue
            per_stream: dict[str, dict] = {}
            for stream in sorted(bucket):
                entry = bucket.get(stream)
                if not is_stream_record(entry):
                    continue
                streams.add(str(stream))
                records = entry.get("records")
                records = records if isinstance(records, list) else []
                checked = as_count(entry.get("items_checked"))
                total = as_count(entry.get("items_total"))
                row = {
                    "items_checked": checked,
                    "items_total": total,
                    "findings": entry.get("findings"),
                    "record_count": len(records),
                    "replaced_count": max(len(records) - 1, 0),
                    "records": records,
                    "over_total": bool(total) and checked > total,
                }
                per_stream[str(stream)] = row
                if row["replaced_count"]:
                    replaced.append({
                        "cycle": str(cycle_key),
                        "stream": str(stream),
                        "replaced_count": row["replaced_count"],
                    })
                if row["over_total"]:
                    over_total.append({
                        "cycle": str(cycle_key),
                        "stream": str(stream),
                        "items_checked": checked,
                        "items_total": total,
                    })
                if not records:
                    without_records.append(
                        {"cycle": str(cycle_key), "stream": str(stream)}
                    )
            if per_stream:
                cycles_out[str(cycle_key)] = per_stream

    return {
        "cycles": cycles_out,
        "cycle_count": len(cycles_out),
        "stream_count": len(streams),
        "replaced": replaced,
        "over_total": over_total,
        "buckets_without_records": without_records,
        "problem": problem,
    }


def full_cycle_ratio(inspect_modes: dict, *, full_mode: str = "FULL") -> dict:
    """AC-046 / FR-053 — FULL cycles divided by total INSPECT cycles.

    Returns, and never raises::

        {"full_cycles": int,        # cycles carrying at least one FULL decision
         "total_cycles": int,       # cycles carrying any decision
         "ratio": float | None,     # None when no cycle carries a decision
         "threshold": 0.5,
         "passes": bool | None}     # None when the ratio cannot be derived

    ``inspect_modes`` is ``inspect_mode_rows``' document. A-037's figure is a
    RATIO and not a count, so it is derived from the recorded widths rather
    than from anything a lead asserts.

    THE AXIS IS CYCLES, NOT DECISIONS. One cycle can carry two decisions — the
    F2 INSPECT and the F5 one TEMPER opens without advancing the counter
    (D-119) — and counting decisions would make a run that reopened one cycle
    at FULL look wider than a run that ran two cycles at FULL. A cycle counts
    as FULL when ANY decision in it was FULL, because a cycle that ran a
    five-stream INSPECT paid for one however many times it was reopened.

    ``passes`` is None, never False, when the ratio cannot be derived: "no
    INSPECT recorded a width" and "more than half of them were FULL" are
    different answers, and a caller stating a pass/fail line has to be able to
    tell them apart. This is `handoffs_wall_clock_seconds`' rule applied to the
    second acceptance figure.

    ``measure-run.py`` reads THIS (casting 3) rather than deriving a second
    ratio, which is why the derivation is hosted in the leaf module and not in
    the report: AC-046 puts the same number in two documents, and two
    derivations of one acceptance figure is the shape §3.2 inventories.
    """
    per_cycle = inspect_modes.get("per_cycle") if isinstance(inspect_modes, dict) else None
    per_cycle = per_cycle if isinstance(per_cycle, dict) else {}
    total = len(per_cycle)
    full = sum(
        1
        for group in per_cycle.values()
        if any(
            isinstance(d, dict) and d.get("mode") == full_mode
            for d in (group or [])
        )
    )
    ratio = (full / total) if total else None
    return {
        "full_cycles": full,
        "total_cycles": total,
        "ratio": None if ratio is None else round(ratio, 4),
        "threshold": 0.5,
        "passes": None if ratio is None else ratio < 0.5,
    }


def fallout_rows(run_dir: Path, *, axis_top: int | None = None) -> dict:
    """AC-046's sibling, FR-025 — filings that are FALLOUT of an earlier defect.

    Returns, and never raises::

        {"per_cycle": {str(cycle): {"fallout": int, "measured": int,
                                    "unmeasured": int, "ids": [str]}},
         "total": int,
         "measured_records": int,      # records carrying the `fallout_of` KEY
         "unmeasured_records": int,    # records with no such key at all
         "last_two_cycles": [int],
         "verdict": "pass" | "fail" | "not_measurable",
         "verdict_reason": str,
         "problem": str | None}

    A-020: "Optional `fallout_of: D-NNN` on the defect record, set by the
    filing stream; measure-run counts it per cycle." The acceptance figure is
    zero across the last two INSPECT cycles, and this returns the verdict
    rather than the caller computing it — `measure-run.py` publishes the same
    two numbers (casting 3) and two derivations of one acceptance figure is
    exactly what §3.2 inventories.

    AN ABSENT FIELD IS NOT A MEASURED ZERO (FR-054). Every record written
    before `fallout_of` existed carries no such key, and counting those cycles
    as "zero fallout" would certify the acceptance criterion on an archive that
    never measured it — the same fabrication `handoffs_wall_clock_seconds`
    refuses for the wall clock. So the KEY's presence is what makes a record
    measured (`fallout_of: null` counts, because `migrate-archive.py` fills
    that default deliberately), and a cycle containing an unmeasured record
    cannot contribute to a pass.

    A RUN WITH FEWER THAN TWO INSPECT CYCLES CANNOT PASS, AND SAYS SO. The
    criterion is defined over a PAIR of cycles; a one-cycle run has no pair,
    and reporting `pass` because nothing contradicted it would make the figure
    easiest to satisfy on the runs that did the least work.

    ``axis_top`` is ``derive_cycle_count``'s ``index`` — the counter's own
    highest value — passed in so this reader and the inspect-mode census sit on
    ONE axis. Omitted, the axis falls back to the highest cycle the defect
    ledger names, which is honest for a count and is stated in
    ``verdict_reason`` when it changes the answer.
    """
    document, problem = read_document(run_dir / "defects.json")
    records = document.get("defects")
    records = records if isinstance(records, list) else []

    per_cycle: dict[str, dict] = {}
    total = 0
    measured_records = 0
    unmeasured_records = 0
    ledger_top: int | None = None
    for record in records:
        if not isinstance(record, dict):
            continue
        cycle = record.get("cycle")
        cycle = cycle if isinstance(cycle, int) and not isinstance(cycle, bool) else None
        if cycle is not None and cycle >= 0:
            ledger_top = cycle if ledger_top is None else max(ledger_top, cycle)
        key = str(cycle) if cycle is not None else "unrecorded"
        bucket = per_cycle.setdefault(
            key, {"fallout": 0, "measured": 0, "unmeasured": 0, "ids": []}
        )
        if "fallout_of" in record:
            measured_records += 1
            bucket["measured"] += 1
            parent = record.get("fallout_of")
            if isinstance(parent, str) and parent.strip():
                total += 1
                bucket["fallout"] += 1
                bucket["ids"].append(record.get("id"))
        else:
            unmeasured_records += 1
            bucket["unmeasured"] += 1

    top = axis_top if isinstance(axis_top, int) and not isinstance(axis_top, bool) else ledger_top
    if top is None or top < 1:
        return {
            "per_cycle": {k: per_cycle[k] for k in sorted(per_cycle, key=cycle_sort_key)},
            "total": total,
            "measured_records": measured_records,
            "unmeasured_records": unmeasured_records,
            "last_two_cycles": [],
            "verdict": "not_measurable",
            "verdict_reason": (
                "the acceptance figure is defined over the LAST TWO INSPECT "
                "cycles and this run records fewer than two, so there is no "
                "pair to measure; a pass here would be easiest to earn on the "
                "runs that did the least work"
            ),
            "problem": problem,
        }

    pair = [top - 1, top]
    unmeasured_in_pair = [
        c for c in pair if per_cycle.get(str(c), {}).get("unmeasured", 0)
    ]
    fallout_in_pair = [c for c in pair if per_cycle.get(str(c), {}).get("fallout", 0)]
    if unmeasured_in_pair:
        verdict = "not_measurable"
        reason = (
            f"cycle(s) {unmeasured_in_pair} carry defect records with no "
            f"`fallout_of` key at all, which is structurally absent and not a "
            f"measured zero — run `migrate-archive.py` to fill the default, "
            f"then the pair can be measured"
        )
    elif fallout_in_pair:
        verdict = "fail"
        reason = (
            f"cycle(s) {fallout_in_pair} of the closing pair {pair} recorded a "
            f"filing that is fallout of an earlier defect; the criterion is "
            f"zero across BOTH"
        )
    else:
        verdict = "pass"
        reason = (
            f"cycles {pair} — the last two INSPECT cycles — each recorded zero "
            f"filings carrying `fallout_of`"
        )
    return {
        "per_cycle": {k: per_cycle[k] for k in sorted(per_cycle, key=cycle_sort_key)},
        "total": total,
        "measured_records": measured_records,
        "unmeasured_records": unmeasured_records,
        "last_two_cycles": pair,
        "verdict": verdict,
        "verdict_reason": reason,
        "problem": problem,
    }


def unreported_dispatch_inputs(run_dir: Path) -> dict:
    """The three ledgers ``unreported_dispatch_summary`` runs over, assembled ONCE.

    Returns, and never raises::

        {"dispatch_rows": [row],            # spawns.log
         "spend_rows": [row],               # spend.jsonl
         "stream_roster": {"F2": [stream]}, # from stream-rollup.json
         "cycles_of_agent": {stream: [str(cycle)]},
         "problem": str | None}             # the FIRST unreadable ledger

    ``unreported_dispatch_summary`` above was already the consolidated RULE; what
    was still derived twice was this ASSEMBLY — the orchestrator built it from
    `_spawn_rows` / `_stream_roster` / `_stream_dispatch_cycles` /
    `_spend_ledger_rows` and `foundry_report._read_dispatch_summary` built it
    again from three inline walks. Hosting it beside the rule is what makes
    "one derivation, two renderings" true of the INPUT as well as the output.

    fallout D-013 — ONE HALF IS CLOSED AND THE OTHER IS NAMED.
    ----------------------------------------------------------
    `foundry_report._read_dispatch_summary` calls THIS function now and walks
    no ledger of its own. The orchestrator's four helpers survived the split
    into `orchestration/spend.py#_spawn_rows`, `#_spend_ledger_rows`,
    `#_stream_dispatch_cycles` and `#_dispatch_pairs`, each still re-spelling
    `read_jsonl`'s splitlines/json.loads/isinstance loop and its own walk of
    the roll-up. That module is another casting's file, so the second copy is
    RAISED rather than reached into: one derivation of the answer over two
    derivations of the question is the same defect whichever module holds the
    duplicate. `tests/test_foundry_state_readers.py` carries the shrink-only
    inventory that names it and fails the moment a third appears.

    THE ROSTER AND THE CYCLE MAP COME OFF ONE WALK, because they are the same
    fact one key up — the roll-up's cycle bucket — and walking the document
    twice is how the two would come to disagree about which cycles a stream ran
    in. The cycle map is also the ONLY cycle stamp the archive has (D-172):
    `spawns.log` records a teammate dispatch as timestamp, casting_id, phase,
    wave and prompt_hash with no cycle anywhere, so a CAST or GRIND teammate can
    never be attributed to a cycle here. The summary returns the pairs that
    source cannot cover as ``pairs_without_cycle`` rather than inventing a stamp
    — correlating spawn timestamps against phase windows would be a second
    proxy, and "the axis is derived from a proxy" is the filing.

    The caller still supplies ``phase_of_dispatch`` and ``agent_id_of``: the
    verb mapping is ``DISPATCH_PHASE_TO_RUN_PHASE`` above and the agent-id
    spelling is `foundry_spawn`'s, which this module deliberately does not
    import.
    """
    dispatch_rows, problem = read_jsonl(run_dir / "spawns.log")
    if problem is not None:
        return {"dispatch_rows": [], "spend_rows": [], "stream_roster": {},
                "cycles_of_agent": {}, "problem": problem}
    spend_rows, problem = read_jsonl(run_dir / "spend.jsonl")
    if problem is not None:
        return {"dispatch_rows": [], "spend_rows": [], "stream_roster": {},
                "cycles_of_agent": {}, "problem": problem}
    rollup, problem = read_document(run_dir / "stream-rollup.json")
    if problem is not None:
        return {"dispatch_rows": [], "spend_rows": [], "stream_roster": {},
                "cycles_of_agent": {}, "problem": problem}

    stream_roster: dict[str, list[str]] = {}
    cycles_of_agent: dict[str, list[str]] = {}
    cycles = rollup.get("cycles")
    if isinstance(cycles, dict):
        for cycle_key, bucket in cycles.items():
            if not isinstance(bucket, dict):
                continue
            for stream in bucket:
                if is_stream_record(bucket.get(stream)):
                    stream_roster.setdefault("F2", []).append(str(stream))
                    cycles_of_agent.setdefault(str(stream), []).append(str(cycle_key))

    return {
        "dispatch_rows": dispatch_rows,
        "spend_rows": spend_rows,
        "stream_roster": stream_roster,
        "cycles_of_agent": cycles_of_agent,
        "problem": None,
    }


def escalated_class_rows(
    document: dict,
    *,
    statuses,
    exit_reasons,
    status_of,
) -> dict:
    """AC-004 — per class: status, exit reason, cleared cycle, packets used.

    Returns, and never raises::

        {"count": int,
         "by_status": {status: int},        # keys are `statuses`, always all of them
         "by_exit_reason": {reason: int},
         "classes": [row]}

    ``statuses`` and ``exit_reasons`` are the closed vocabularies and
    ``status_of`` is ``vocab.escalation_status`` — all three passed in, because
    the leaf contract at the top of this file forbids importing `vocab` and a
    re-typed copy of a closed vocabulary is the hand-copied-enum drift the house
    rule bans outright.

    D-214 — THE VOCABULARY DECIDES, AND IT DECIDES ON THE RAW ENTRY. The read
    this replaces was ``status if isinstance(status, str) else "ESCALATED"``,
    which is a shape test wearing the vocabulary's default: ANY string passed
    through, so a document carrying ``{"status": "BOGUS"}`` reported ``count`` 3
    while ``by_status`` summed to 2 and the row read a status no writer emits.
    ``status_of`` is total over ``statuses``, so the increment below needs no
    membership guard and ``count == sum(by_status.values())`` by construction.

    It is called BEFORE the mapping normalisation, so nothing pre-empts it the
    way D-212's ``continue`` did: an entry that is not a mapping carries no
    CLEARED and resolves to ESCALATED, and a class with no ``status`` predates
    this release's fields and is reported in the state it was written in.
    Defaulting either to CLEARED would silently retire a class nobody cleared.
    """
    classes = document.get("classes") if isinstance(document, dict) else None
    if not isinstance(classes, dict):
        classes = {}

    rows: list[dict] = []
    by_status = dict.fromkeys(sorted(statuses), 0)
    by_exit_reason = dict.fromkeys(sorted(exit_reasons), 0)
    for name, entry in sorted(classes.items()):
        status = status_of(entry)
        fields = entry if isinstance(entry, dict) else {}
        reason = fields.get("exit_reason")
        rows.append(
            {
                "class": name,
                "status": status,
                "exit_reason": reason if isinstance(reason, str) else None,
                "escalated_at_cycle": fields.get("escalated_at_cycle"),
                "cleared_at_cycle": fields.get("cleared_at_cycle"),
                "structural_packets_dispatched": fields.get(
                    "structural_packets_dispatched"
                ),
                "structural_packet_cycles": fields.get("structural_packet_cycles"),
                "live_clean_cycles": fields.get("live_clean_cycles"),
                "open_latent_defect_ids": fields.get("open_latent_defect_ids"),
                "defect_ids": fields.get("defect_ids"),
                "proposal": fields.get("proposal"),
            }
        )
        if status in by_status:
            by_status[status] += 1
        if isinstance(reason, str) and reason in by_exit_reason:
            by_exit_reason[reason] += 1
    return {
        "count": len(rows),
        "by_status": by_status,
        "by_exit_reason": by_exit_reason,
        "classes": rows,
    }


def markdown_sections(text: str) -> tuple[list[str], list[tuple[str, list[str]]]]:
    """Split a REPORT.md into ``(header_lines, [(heading, body_lines), ...])``.

    A heading is a whole trimmed line beginning ``"## "``. Holmes `share-10`:
    the seal's splitter and the DONE gate's presence check "agree by convention"
    — one built blocks by this rule and the other built
    ``{line.strip() for line in text.splitlines()}``, "same effective rule,
    coded independently". Both halves of GI-006 now read the document by ONE
    rule, so the seal can never preserve something the gate would call missing,
    or drop something it would call present.

    ``markdown_headings`` below is the same walk asked the other question, and
    it is derived from this function rather than spelled beside it.
    """
    header: list[str] = []
    blocks: list[tuple[str, list[str]]] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("## "):
            blocks.append((stripped, []))
        elif blocks:
            blocks[-1][1].append(line)
        else:
            header.append(line)
    return header, blocks


def markdown_headings(text: str) -> set[str]:
    """Every ``## `` heading line a REPORT.md carries, trimmed.

    DERIVED from ``markdown_sections`` rather than spelled beside it, which is
    the whole of Holmes `share-10`: the presence check matched a whole trimmed
    line anywhere in the document while the seal matched a line that STARTS a
    block, and the two agreed only by convention. The match stays on the WHOLE
    trimmed line rather than a prefix, so a lead's own ``## Appendix`` never
    counts as a generated section and a generated heading with a suffix bolted
    on reads as the edit it is.
    """
    return {heading for heading, _ in markdown_sections(text)[1]}


def prove_is_clean(*, totals: dict | None, spec_requirement_count: int) -> bool:
    """True when a recorded PROVE tranche set is clean: 0 findings AND >=95% cover.

    ``totals`` is the cycle's roll-up totals (``{"items_checked", "items_total",
    "findings"}``) or the marker's aggregate for an archive predating the
    roll-up; ``spec_requirement_count`` is the number of requirement ids the
    run's spec declares. BOTH are passed in, because resolving the spec needs
    the path ladder Holmes `share-7` is moving into `tools/artifacts.py` and a
    fourth copy of it here is the thing that proposal exists to prevent.

    A spec that parses to ZERO requirements is never clean (FR-020 / AC-025).
    The >=95% check used to be SKIPPED when the count was 0, so any
    ``.prove-complete`` with ``findings=0`` on an unresolvable or unparseable
    spec drove the F4 auto-VERIFY path — manufacturing a passing run out of a
    spec nothing had actually been proved against.

    ``findings`` absent is not ``findings`` zero: a tranche that recorded no
    finding count has not been shown to be clean, so it is not.
    """
    if not isinstance(totals, dict):
        return False
    if totals.get("findings") is None or totals.get("findings") != 0:
        return False
    if spec_requirement_count <= 0:
        return False
    return as_count(totals.get("items_checked")) >= spec_requirement_count * 0.95


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


# --------------------------------------------------------------------------- #
# fallout GI-033 / D-021 / D-035 (concern C-017) — THE READS THE LAYERING PUTS
# HERE.
#
# `_LAYERING_DEBT` records edges where a verifier module (gates, transitions,
# width) reaches into the lifecycle layer, or a lifecycle module reaches into a
# verifier. GI-033 permits exactly one exception and none of these is it. Every
# row below was the same shape: a PURE READ of a run artifact that happened to
# be defined in the module whose feature it serves, so the module that needed
# the fact had to import the module that owned the feature.
#
# The reads live here now. This is the leaf GI-033 enumerates and GI-024
# already designates for consolidated readers, so a verifier reaching one of
# these crosses no layer at all.
#
# WHAT DOES NOT COME HERE, AND WHY IT IS NOT AN OMISSION.
# -------------------------------------------------------
#   * WRITERS AND EFFECTS. `_synthesize_clean_prove_verdicts` opens a
#     `_document_transaction` on verdicts.json; it is a writer, and the locked
#     read-modify-write primitive lives in `artifacts.py`, which this module
#     may not import. It belongs with the four writer/effect rows already ruled
#     a structural packet, not with these reads.
#   * REFUSAL SHAPERS THAT HOLD PROTOCOL KNOWLEDGE. `_halted_refusal` names the
#     tokens that do not leave HALTED, the report file and the remedy;
#     `_blocking_defects`' hint names both filing doors and the GRIND phase.
#     Those sentences are lifecycle knowledge and stay in the lifecycle layer.
#     The READ each of them is built on comes here, which is the whole of the
#     edge: the shaper then needs no cross-layer import to get its facts.
#   * RULE ENGINES. `_escalated_classes` walks the consecutive-cycle rule
#     through `_class_buckets` / `_class_info` / `_consecutive_run`, and
#     `_escalation_exit_distances` walks the clean arm and prints how far each
#     arm has left. Neither is a read of a document; both are ST-002's rule,
#     and dragging them here would move the rule to keep an import tidy.
#
# THE VOCABULARY IS PASSED IN, every time, in the shape
# `unreported_dispatch_summary` established and the leaf contract's own pin
# spells out: "a name from `vocab` or a `tools/` module is passed IN". That is
# what lets these be reads in a module that imports `json` and `pathlib` and
# nothing else.
# --------------------------------------------------------------------------- #


def current_inspect_mode(
    run_dir: Path, cycle: int | None = None, *, modes
) -> dict | None:
    """The decision the last INSPECT-opening transition recorded, or None.

    THE ONLY READ of the recorded width. A lazily-computed mode is GI-008's and
    GI-009's named violation, so every consumer — the streams-complete check,
    the guidance engine, the status display, the report — comes through here
    and none of them re-derives anything. None means a run whose INSPECT has
    not been opened since the record landed, and each caller degrades to its
    pre-change behaviour rather than guessing a width.

    Three axes decide, and each was a defect before it was a rule:

      * WHICH ENTRY (D-212). The LAST one. `inspect_modes` is append-only and
        the last entry IS the current decision, so a malformed current decision
        can never be answered with an older cycle's valid one — walking back
        would report cycle N-1's FULL as cycle N's width and pass the ASSAY
        gate that refuses today.
      * WHAT ITS MODE IS (D-212). Membership of ``modes``, never truthiness, so
        a hand-edited `"delta"` or `"BOGUS"` is not a recorded width.
      * WHICH CYCLE IT BELONGS TO (D-216). The entry must be stamped for the
        cycle being asked about. Driven at F2 cycle 2 with a single cycle-1
        entry, `Foundry-Gate('assay')` PASSED carrying an assertion about cycle
        2 answered by cycle 1's record.

    An unusable record reads as NO record — D-117's ruling ("an unrecorded
    width is not full width") applied to a value that is present and wrong
    rather than to one that is absent. ``cycle`` is WHICH crossing is being
    asked about and defaults to the server counter; the narrow readers that
    measure one named cycle's coverage pass it. Total; never raises.
    """
    state, _ = read_document(run_dir / "state.json")
    entries = state.get("inspect_modes")
    if not isinstance(entries, list) or not entries:
        return None
    entry = entries[-1]
    if not isinstance(entry, dict):
        return None
    if entry.get("mode") not in modes:
        return None
    stamped = entry.get("cycle")
    if isinstance(stamped, bool) or not isinstance(stamped, int):
        return None
    if stamped != (current_cycle(run_dir) if cycle is None else cycle):
        return None
    return entry


def open_defects_by_tier(
    run_dir: Path, *, tiers, unknown_tier: str, tier_of
) -> dict[str, list[dict]]:
    """Every OPEN defect in the ledger, bucketed by the tier it READS as.

    Keys are every member of ``tiers`` plus ``unknown_tier``, always present
    and possibly empty — a caller that has to check whether a bucket exists
    before counting it will eventually forget to, and an absent bucket reads as
    zero blocking defects, which is the direction that fails open.

    THE BUCKETS ARE DERIVED FROM THE VOCABULARY PASSED IN. They were three
    hand-typed keys while ``tier_of`` was total over the whole tier set, so the
    moment `HARDENING` joined that frozenset this raised `KeyError:
    'HARDENING'` on any ledger carrying one — and the tier exists precisely so
    streams will file into it. A hand-typed copy of a closed vocabulary is the
    drift `vocab.py` was built to end.

    ``tier_of`` resolves a record to a tier and is the one place a missing key,
    a null, a non-string and an unknown string all land on ``unknown_tier``:
    an untiered pre-change record blocks exactly like LIVE, and reading it as
    LATENT would silently clear every gate on records nobody classified.

    Non-dict historical records are skipped, not guessed at (D-128). Total.
    """
    buckets: dict[str, list[dict]] = {t: [] for t in sorted(tiers)}
    buckets.setdefault(unknown_tier, [])
    document, _ = read_document(run_dir / "defects.json")
    records = document.get("defects")
    for record in records if isinstance(records, list) else []:
        if not isinstance(record, dict) or record.get("status") != "open":
            continue
        buckets.setdefault(tier_of(record), []).append(record)
    return buckets


def open_defect_ids_by_tier(
    run_dir: Path, *, tiers, unknown_tier: str, tier_of
) -> dict[str, list[str]]:
    """`open_defects_by_tier` reduced to the ids, which is what a refusal names.

    Same buckets, same keys, `"?"` for a record carrying no id. The refusal
    PROSE is not built here: CT-008 requires a gate to name the open LIVE and
    the open unknown-tier defects and to tell them apart, because they block
    for different reasons and the operator's next move differs — a LIVE defect
    needs fixing, an unknown-tier one needs a stream to re-file it with a tier.
    That sentence names the filing doors and the GRIND phase, which is protocol
    knowledge, so it stays with the gate and only the ids come from here.
    """
    return {
        tier: [str(record.get("id", "?")) for record in bucket]
        for tier, bucket in open_defects_by_tier(
            run_dir, tiers=tiers, unknown_tier=unknown_tier, tier_of=tier_of
        ).items()
    }


def halted_state(
    run_dir: Path, *, halted_phase: str, reason_of, max_cycles_of
) -> dict | None:
    """The run's HALTED record, or None when the run is not halted.

    THE ONLY READ of `state.json.phase == HALTED`, for the reason
    `current_inspect_mode` is the only read of the recorded width: a terminal
    state each door decides for itself is a terminal state each door can decide
    differently.

    Returns, and never raises::

        {"halted_at_cycle": object,        # as recorded; may be absent/None
         "halted_reason": str,             # the SENTENCE an operator reads
         "halted_reason_member": str,      # the vocabulary member, or ""
         "max_cycles": int,                # the cap, read as the halt read it
         "halted_report_error": str}       # "" when the report was written

    TWO SHAPES, ONE READ (CT-004 / FR-054). `halted_reason` is
    ``{"reason": <member>, "text": <the lead's words>}`` from this release on
    and a bare f-string on every archive written before it. Both are read here
    so no caller has to know which it got, and the member is never GUESSED out
    of a pre-release sentence — that is how a run's ending gets reclassified by
    a reader. ``reason_of`` returns the member for a recognised value and None
    otherwise, so "" here means "this record carries text and no member".

    ``max_cycles_of`` reads the cap off the same document the halt read it
    from (D-225): displaying a raw field beside a decision made on a normalised
    one is how a refusal comes to name a number no code acted on.
    """
    state, _ = read_document(run_dir / "state.json")
    if state.get("phase") != halted_phase:
        return None
    raw = state.get("halted_reason")
    if isinstance(raw, dict):
        member = reason_of(raw.get("reason")) or ""
        detail = str(raw.get("text") or "").strip()
        sentence = f"{member}: {detail}" if member and detail else (member or detail)
    else:
        member = ""
        sentence = str(raw or "").strip()
    return {
        "halted_at_cycle": state.get("halted_at_cycle"),
        "halted_reason": sentence or "the configured cycle cap was reached",
        "halted_reason_member": member,
        "max_cycles": max_cycles_of(state),
        "halted_report_error": str(state.get("halted_report_error") or "").strip(),
    }


def registered_team_dirs(run_dir: Path, *, teams_dir: Path) -> list[str]:
    """The registered teams whose directory still exists, in recorded order.

    THE ARTIFACT HALF of "is a team still holding the tree". A team is active
    while `state.json.active_teams` names it AND its directory is still there —
    the directory going away is what `TeamDelete` does, and a name with no
    directory is a roster entry nobody cleaned up.

    The OTHER half is a scan of the machine for live teammate panes, which
    reads no run artifact and belongs with the module that knows how to look;
    so does the hint naming SendMessage, TeamDelete and `tmux kill-pane`. Both
    halves must be clear for a gate to pass, and this is the half a run
    artifact can answer. Total: a non-list roster and a non-string member each
    contribute nothing rather than raising.
    """
    state, _ = read_document(run_dir / "state.json")
    teams = state.get("active_teams")
    return [
        name for name in (teams if isinstance(teams, list) else [])
        if isinstance(name, str) and name and (Path(teams_dir) / name).is_dir()
    ]


def sight_required(
    run_dir: Path, *, shape_problem, no_ui_meaning: str
) -> dict:
    """Whether the SIGHT browser audit is part of this run, from the manifest.

    Returns ``{"required": bool}`` plus, as they apply, ``blocked``, ``no_ui``,
    ``ui_files``, ``url`` and ``reason``. A run with no manifest yet requires
    nothing, which is what is true of it.

    `--no-ui` IS A DECLARATION AND THIS HONOURS IT (AC-052 / FR-055). The flag
    is read BEFORE the extension scan's own answer, because the flag is the
    operator's statement about the run and the extensions are an inference
    about it. The previous shape implemented the opposite: with the flag set
    and any UI extension in scope it answered `required: True, blocked: True`,
    the streams roster then demanded `sight`, and the CAST precondition failed
    — so declaring a run had no browsable UI was the one way to make the
    browser audit mandatory AND unsatisfiable. ``ui_files`` is still reported
    so the operator can see the tension between what they declared and what is
    in scope; it is a fact on the answer, never a reason to overrule the
    declaration. The extension scan stays for runs that did NOT declare the
    flag: absence of the flag is not a claim either way.

    ``shape_problem`` is the shared manifest-shape validator and ``no_ui_meaning``
    the one sentence that spells what the flag means; both are passed in
    because the leaf contract keeps this module free of package imports, and
    both live where AC-052's "one documented meaning" puts them. A manifest
    whose records are unusable requires nothing and says so (D-134): the
    records, not just the container, because `castings: "nope"` used to meet
    `.get()` and raise AttributeError out of Foundry-Next.
    """
    manifest_path = Path(run_dir) / "castings" / "manifest.json"
    if not manifest_path.exists():
        return {"required": False}
    data, _ = read_document(manifest_path)
    if shape_problem(data) is not None:
        return {
            "required": False,
            "reason": "castings/manifest.json records are unreadable",
        }

    ui_exts = (".tsx", ".jsx", ".vue", ".svelte", ".css", ".scss", ".html", ".astro")
    ui_files = [
        f
        for casting in data.get("castings", [])
        if isinstance(casting, dict)
        for f in (casting.get("key_files") or [])
        if isinstance(f, str) and f.endswith(ui_exts)
    ]

    if data.get("no_ui", False):
        return {
            "required": False,
            "blocked": False,
            "no_ui": True,
            "ui_files": len(ui_files),
            "reason": no_ui_meaning,
        }
    if not ui_files:
        return {"required": False, "reason": "No frontend files in castings"}

    url = data.get("target_url", "")
    if not url:
        return {
            "required": True,
            "blocked": True,
            "ui_files": len(ui_files),
            "reason": (
                f"No --url provided but {len(ui_files)} frontend files in scope"
            ),
        }
    return {"required": True, "blocked": False, "url": url, "ui_files": len(ui_files)}


def persisted_escalated_classes(
    classes, *, overrides, status_of, escalated: str
) -> list[str]:
    """The class keys a persisted escalation document records as ESCALATED.

    Sorted, so both escalation arms walk in one order and the document they
    write is stable across runs. ``overrides`` is the set of classes the
    operator de-escalated by directive; ``"*"`` in it clears every class.

    OVERRIDES ARE HONOURED HERE TOO. An arm reading the document directly would
    bypass the filter and eventually stamp a class CLEARED with an exit reason
    no rule earned — recording the operator's decision as the machine's,
    irreversibly, since CLEARED is terminal and a withdrawn directive could
    never bring the class back.

    ``status_of`` decides, and NOTHING PRE-FILTERS THE SHAPE AHEAD OF IT
    (D-210 / D-212). This tested `isinstance(entry, dict)` before the resolver,
    so its third rung — "present and NOT a member, or an entry that is not a
    mapping at all, reads as ESCALATED" — was unreachable through this door,
    and a non-mapping entry was DROPPED from a list the DONE gate refuses on.
    Driven at cdb9322 through `Foundry-Gate('done')`: `{"status": "BOGUS"}`
    blocked correctly, while `"just a string"`, `["ESCALATED"]` and `null` each
    let the run reach DONE. ST-010 is "every escalated class CLEARED", and an
    entry that is not a mapping carries no CLEARED.

    ``escalated`` is the vocabulary's own spelling of the member, passed in
    rather than typed here: a literal in this module would be exactly the
    second opinion of the field D-210 closed one caller over.
    """
    if "*" in overrides:
        return []
    if not isinstance(classes, dict):
        return []
    return sorted(
        key
        for key, entry in classes.items()
        if key not in overrides and status_of(entry) == escalated
    )
