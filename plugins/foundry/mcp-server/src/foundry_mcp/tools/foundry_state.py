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


def _halt_recorded(value: object) -> bool:
    """True when a persisted ``halted_reason`` records a halt, EITHER shape.

    Total over any JSON type, and pure. FR-019 changed the field from a free
    f-string — "--max-cycles 2 reached: opening GRIND cycle 3 would exceed it"
    — to ``{"reason": <HALT_REASONS member>, "text": <the lead's words>}``, and
    every archive written before that release carries the string. Both are a
    halt, so both answer True and neither is guessed onto the other.

    MEMBERSHIP IS NOT CHECKED HERE, and that is the leaf contract rather than a
    shortcut: this module imports ``json`` and ``pathlib`` and nothing else, so
    ``HALT_REASONS`` is not reachable from it (see the module docstring). The
    question this answers is "did something record a halt", which any non-blank
    reason or text settles; "is that reason a member" is
    ``vocab.halt_reason``'s, asked by the surfaces that can reach it.

    A dict carrying a blank reason AND a blank text is not a halt record — it
    is an empty mapping with two keys, and reading it as a halt would subtract
    a GRIND cycle from every run that ever wrote one.
    """
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, dict):
        return any(
            isinstance(field, str) and field.strip()
            for field in (value.get("reason"), value.get("text"))
        )
    return False


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

    # D-175 — the two fields the halt writes beside `phase` HALTED, in the same
    # transaction. Either one present is a halt: a run may be halted by hand
    # with only the reason recorded, and a report that then claimed the refused
    # GRIND is the defect either way.
    #
    # BOTH PERSISTED SHAPES, BECAUSE BOTH EXIST (D-102 / FR-019 / FR-054). This
    # tested `isinstance(halted_reason, str)` alone, which was the whole of the
    # field before FR-019; `orchestration/halt.py#_halt_run` now writes
    # `{"reason": <member>, "text": <the lead's words>}`, and a dict is not a
    # str. So a halted run read as still running: `count` came back `index + 1`
    # — one phantom GRIND cycle, the very cycle the halt refused to open — and
    # `halted` came back False, on the two figures that feed
    # `baseline_comparison.current.grind_cycles` and measure-run's `cycles`
    # gate verdict. Driven at the reader with the FR-019 shape it returned
    # {'count': 6, 'halted': False} where the legacy string returned
    # {'count': 5, 'halted': True}: one run, one halt, two answers.
    halted = (
        _cycle(state.get("halted_at_cycle")) is not None
        or _halt_recorded(state.get("halted_reason"))
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


def _resolves_to_stream(key: object, stream_id_of) -> bool:
    """True when the roster resolver recognises this cycle-bucket KEY.

    The second half ``is_stream_record``'s docstring assigns to the call site,
    written once here because BOTH walkers in this module need it (D-111).
    ``stream_id_of`` is the caller's resolver — ``vocab.canonical_stream_id`` at
    every real call site — handed in because this module reaches no vocabulary.

    Total, and False whenever no resolver was supplied: a walker with no roster
    cannot tell an additive tranche from a cycle-level fact and must not guess.
    A resolver that raises on an odd key answers False rather than taking the
    whole read down with it — this module's rule for every derivation.
    """
    if stream_id_of is None or not isinstance(key, str):
        return False
    try:
        return stream_id_of(key) is not None
    except Exception:
        return False


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
# `## ` heading rule, `_now`, `_prove_is_clean`, the cycle-count read, the cycle
# sort key and `_as_count` each had two. Holmes `share-2` names the pattern:
# "byte-identical" cycle readers -- the orchestrator's spelling and the
# server-cycle one -- "each kept as a 'deliberate second copy' instead of living
# in the leaf module built for that". Both spellings are gone; `current_cycle`
# below is the one read that replaced them.
#
# The previous fix for the spend pair made it worse in a way worth recording:
# `foundry_report` imported the overlay -- `overlay_unreported` below, under
# its private spelling at the time -- BACK OUT of the orchestrator through a
# function-local import, "closing the import cycle rather than sharing the
# rule". Both halves of that pair now live here, so the report reaches only
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


def stream_rollup_rows(run_dir: Path, *, stream_id_of=None) -> dict:
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

    ``stream_id_of`` IS WHAT MAKES THAT LAST SENTENCE TRUE (D-111 / FR-054).
    ---------------------------------------------------------------------
    It was prose and not code. ``is_stream_record`` tests the VALUE — a mapping
    carrying ``records`` — so a bucket written by the ADDITIVE writer, which is
    the only kind that lacks the key, failed it and was ``continue``d before the
    ``buckets_without_records`` branch could ever run. Driven on
    ``{"trace": {"items_checked": 40, "items_total": 50, "findings": 3}}`` the
    pair was absent from ``cycles``, absent from ``buckets_without_records`` and
    counted in neither total: the reader silently dropped the exact ledger shape
    FR-054 tells it to tolerate, and the migration that repairs the shape is not
    a precondition of reading it — the F6 report runs on unmigrated runs.

    Widening ``is_stream_record`` was the alternative and is wrong: its rule is
    read by ``measure-run.py`` and twice by ``migrate-archive.py``, and one of
    those cases is `prove` holding a bare string, which is a BROKEN record and
    not a cycle-level fact. Its own docstring says where the second half goes —
    "a caller that must also tell a CORRUPT tranche from a cycle-level fact
    resolves the KEY against the stream roster after this returns False" — and
    both sibling walkers under ``plugins/foundry/scripts/`` already do exactly
    that, resolving the bucket KEY through ``canonical_stream_id`` before the
    value test is allowed to decide. So this walker does it too, and the
    resolver is HANDED IN because the roster is
    a vocabulary and this module reaches no vocabulary: the same shape
    ``current_inspect_mode(modes=...)`` and ``escalated_class_rows(status_of=...)``
    already take.

    Omitted, the walk is exactly what it was — value test only, additive buckets
    dropped — so a caller with no roster to offer gets a narrower answer and
    never a wrong one.
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
                    # The KEY decides the leftovers, and only when a resolver
                    # was handed in. A mapping the roster knows is a stream
                    # tranche written before `records[]` existed; anything else
                    # is a cycle-level fact sitting beside the streams (D-182).
                    if not (
                        isinstance(entry, dict)
                        and _resolves_to_stream(stream, stream_id_of)
                    ):
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


#: AC-046 / FR-026 — the ratio A-037 states: "FULL cycles / total INSPECT
#: cycles below 50%". The number appears ONCE in this module and BOTH the
#: `threshold` this reports and the comparison `passes` applies derive from it,
#: so the sentence the F6 report prints and the verdict `measure-run.py`
#: publishes cannot come to disagree — the `foundry_validate.REQUIREMENT_SPAN_MAX`
#: shape, which is the precedent NFR-011 names for a prose rule pinned to a
#: constant (concern C-056, fallout D-095).
#:
#: THE BOUND IS EXCLUSIVE, which is why this constant carries no MAX suffix. A run at
#: exactly 0.5 FAILS: A-037 says BELOW 50%, and `REQUIREMENT_SPAN_MAX = 2` — the
#: constant this one is modelled on — is inclusive, so borrowing its suffix here
#: would name an exclusive bound with an inclusive word.
FULL_CYCLE_RATIO_THRESHOLD = 0.5


# --------------------------------------------------------------------------- #
# fallout GI-033 / D-080 (concern C-059) — HOISTED FROM THE VERIFIER LAYER.
#
# Each function below was read by a VERIFIER module and a LIFECYCLE module at
# once. GI-033's arithmetic is that the two layers are mutually unreachable, so
# a symbol both read can live in neither and belongs in a leaf. They are MOVED,
# not re-implemented — the bodies are the ones `orchestration/width.py`,
# `orchestration/gates.py` and `tools/foundry_spawn.py` shipped, so nothing
# about the behaviour is new and casting 2 deletes the old copies when it
# repoints.
#
# THE VOCABULARY IS PASSED IN, every time. This module imports `json` and
# `pathlib` and nothing else, because `scripts/measure-run.py` reads it with no
# package on the path; a hoist that imported `vocab` to get a marker name or a
# tier set would end that, so the names arrive as arguments in the shape
# `unreported_dispatch_summary` and `current_inspect_mode` established.
#
# THE TWO GIT HELPERS KEEP THEIR NAMES, and that is a decision rather than an
# oversight. `test_no_top_level_symbol_is_defined_in_two_shipped_modules` is RED
# from this commit until casting 2 deletes `width.py`'s copies in the commit
# straight after, which casting 2 asked for in that order: deleting first would
# break every caller in its package at IMPORT time, and a missing symbol is the
# whole server down where a name collision is one failing test. Renaming them
# here instead would buy a green guard for one commit at the price of a
# permanently worse name, which is the trade this run keeps refusing.
# --------------------------------------------------------------------------- #


def git_changed_paths(
    project_root: str, base: str, head: str = "HEAD", *, timeout: float = 30.0
) -> dict:
    """Paths changed between two commits. ``{"ok", "files", "error"}``.

    Total: a missing git, a bad revision or a timeout all return ok=False with a
    named error and an empty file list, never a raise. ok=False and an empty
    list are DIFFERENT facts from ok=True and an empty list — the second means
    nothing changed, and a caller that cannot tell them apart will read a broken
    git as a clean tree.

    ``-z`` with ``core.quotepath=false`` is what makes a path carrying a space
    or a non-ASCII byte come back as ONE token rather than a quoted, escaped
    approximation of itself. ``--end-of-options`` stops a ref beginning with a
    dash being read as a flag.

    ``subprocess`` is imported in the body, not at the top: `measure-run.py`
    reads this module with no package on the path, and the import cost belongs
    to the callers that actually shell out.
    """
    import subprocess
    try:
        proc = subprocess.run(
            [
                "git", "-C", project_root,
                "-c", "core.quotepath=false",
                "diff", "--name-only", "-z",
                "--end-of-options", base, head,
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except (FileNotFoundError, OSError, subprocess.SubprocessError) as exc:
        return {
            "ok": False, "files": [],
            "error": f"git unavailable: {type(exc).__name__}",
        }
    if proc.returncode != 0:
        return {
            "ok": False, "files": [],
            "error": f"git diff failed: {proc.stderr.strip()[:120]}",
        }
    return {
        "ok": True,
        "files": sorted({tok for tok in proc.stdout.split("\0") if tok.strip()}),
        "error": "",
    }


def git_touching_commit(
    project_root: str, base: str, path: str, head: str = "HEAD",
    *, timeout: float = 30.0,
) -> str:
    """The newest commit in ``base..head`` touching ``path``, or "".

    Total, and "" means BOTH "no such commit" and "git could not answer". A
    caller needing to tell those apart asks `git_changed_paths` first, which
    reports its own failure; empty is the safe answer either way because it
    never claims a commit that is not there.
    """
    import subprocess
    try:
        proc = subprocess.run(
            [
                "git", "-C", project_root,
                "-c", "core.quotepath=false",
                "log", "-1", "--format=%h",
                "--end-of-options", f"{base}..{head}", "--", path,
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except (FileNotFoundError, OSError, subprocess.SubprocessError):
        return ""
    if proc.returncode != 0:
        return ""
    return proc.stdout.strip()


def boundary_base_sha(
    run_dir: Path, *, boundary_marker: str, trace_marker: str, cast_marker: str
) -> tuple[str, str]:
    """The commit a cycle's diff is measured FROM, and which marker named it.

    ``(sha, marker_name)``, or ``("", "")`` when no marker carries one. The
    three markers are tried in the order they become true about a run — the
    INSPECT boundary, then the TRACE-clean stamp, then the CAST baseline — so
    the newest fact wins and an older marker left behind cannot answer for it.

    The marker BASENAMES are passed in: they are declared in `artifacts.py`,
    which this stdlib-only module may not import. Reads through this module's
    own tolerant primitives, so a torn or non-UTF-8 marker reads as absent
    rather than raising. Total; never raises.
    """
    marker = run_dir / boundary_marker
    if marker.exists():
        sha, _ = read_text_file(marker)
        if sha.strip():
            return sha.strip(), boundary_marker
    trace = run_dir / trace_marker
    if trace.exists():
        data, problem = read_document(trace)
        if problem is None and data.get("head_sha"):
            return str(data["head_sha"]), trace_marker
    cast = run_dir / cast_marker
    if cast.exists():
        sha, _ = read_text_file(cast)
        if sha.strip():
            return sha.strip(), cast_marker
    return "", ""


def blocking_defects(
    run_dir: Path, *, tiers, unknown_tier: str, tier_of
) -> dict:
    """The FACTS behind "may this gate pass?" — counts and ids, no prose.

    Returns, and never raises::

        {"blocking": int,        # len(live) + len(unknown)
         "live": [ids],
         "unknown": [ids],
         "latent": [ids]}

    THE REFUSAL PROSE IS DELIBERATELY NOT HERE, and this module's own contract
    is why: a sentence naming both filing doors and the GRIND phase is
    lifecycle knowledge and stays in the lifecycle layer, while "the READ each
    of them is built on comes here". `_blocking_defects`' hint is the example
    that contract names by name. So the gate keeps its sentence and this
    answers the numbers it is built from — which is also the shape C-059
    offered.

    LIVE and unknown are counted separately and BOTH block, because CT-008
    requires the refusal to tell them apart: a LIVE defect needs fixing, an
    untiered one needs a stream to re-file it with a tier, and one
    undifferentiated count sends a lead hunting for a reproduction no stream
    ever claimed. LATENT is returned because a caller reporting the backlog
    wants it, and it blocks nothing.
    """
    buckets = open_defects_by_tier(
        run_dir, tiers=tiers, unknown_tier=unknown_tier, tier_of=tier_of
    )
    live = [d.get("id", "?") for d in buckets.get("LIVE", [])]
    unknown = [d.get("id", "?") for d in buckets.get(unknown_tier, [])]
    latent = [d.get("id", "?") for d in buckets.get("LATENT", [])]
    return {
        "blocking": len(live) + len(unknown),
        "live": live,
        "unknown": unknown,
        "latent": latent,
    }


def skipped_stream_ids(
    run_dir: Path, *, wire_ids, wire_to_canonical, shape_problem
) -> set[str]:
    """The wire ids of streams this run DECLARED it would not spawn.

    ``manifest.stream_skips`` is F0.5's predictive skip list. An entry is a
    mapping carrying ``stream_id`` (canonical UPPERCASE) or a bare string, so
    an older manifest still reads. The canonical spelling maps back through
    ``wire_to_canonical`` rather than by lowercasing, because the two
    spellings are not related by case alone (``TEST-01`` / ``test01``).

    Degrades to "no declared skips" on any shape the readers cannot index,
    decided by the SHARED validator passed in as ``shape_problem`` rather than
    by a private `isinstance` — that private check is the reason
    ``stream_skips: 42`` reached ``for entry in 42`` and raised TypeError out
    of Foundry-Liveness, from the very reader the prose held up as the one
    that had always guarded it (D-132).

    ``wire_ids`` and ``wire_to_canonical`` are `vocab.STREAM_WIRE_IDS` and
    `vocab.WIRE_TO_CANONICAL`, passed in for the reason at the top of this
    section. Total; never raises.
    """
    document, problem = read_json(run_dir / "castings" / "manifest.json")
    if problem is not None:
        return set()
    if shape_problem(document) is not None:
        return set()
    manifest = document if isinstance(document, dict) else {}
    canonical_to_wire = {
        canonical: wire for wire, canonical in wire_to_canonical.items()
    }
    skipped: set[str] = set()
    # TOTAL INDEPENDENTLY OF THE VALIDATOR PASSED IN, which is this module's
    # contract and is STRICTER than the one this body had at its old home.
    # There, `shape_problem` was always `_manifest_shape_problem` and caught a
    # non-indexable `stream_skips` on the way past; here it is an argument, so a
    # caller handing in a laxer validator would reach `for entry in 42` and raise
    # TypeError out of a leaf that promises never to. That is D-132's own defect
    # arriving through the new seam — the shared validator still DECIDES, and
    # this guard is what keeps the promise when it is not the one that ran.
    entries = manifest.get("stream_skips")
    for entry in entries if isinstance(entries, list) else []:
        if isinstance(entry, dict):
            raw = entry.get("stream_id") or entry.get("stream") or entry.get("id")
        else:
            raw = entry
        if not isinstance(raw, str) or not raw.strip():
            continue
        token = raw.strip()
        if token.lower() in wire_ids:
            skipped.add(token.lower())
        elif token.upper() in canonical_to_wire:
            skipped.add(canonical_to_wire[token.upper()])
    return skipped


def full_cycle_ratio(inspect_modes: dict, *, full_mode: str = "FULL") -> dict:
    """AC-046 / FR-053 — FULL cycles divided by total INSPECT cycles.

    Returns, and never raises::

        {"full_cycles": int,        # cycles carrying at least one FULL decision
         "total_cycles": int,       # cycles carrying any decision
         "ratio": float | None,     # None when no cycle carries a decision
         "threshold": FULL_CYCLE_RATIO_THRESHOLD,
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
        "threshold": FULL_CYCLE_RATIO_THRESHOLD,
        "passes": None if ratio is None else ratio < FULL_CYCLE_RATIO_THRESHOLD,
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

    NO LEDGER IS NOT AN EMPTY LEDGER (D-104 / FR-053 / GI-024). ``read_document``
    is total and answers ``({}, None)`` for a file that is not there, which is
    the right contract for a reader that must not raise and the wrong INPUT for
    an acceptance verdict: with no records at all, nothing is unmeasured and
    nothing carries ``fallout_of``, so every rung below falls through to
    ``pass`` and the census certifies AC-045/NFR-006 — "cycles [0, 1] ... each
    recorded zero filings carrying `fallout_of`" — on a directory holding only
    a `state.json`. That is the strongest acceptance result in the document put
    against the weakest possible evidence.

    ``scripts/measure-run.py#_read_fallout`` already refused exactly this, with
    that sentence in its own docstring, by testing ``path.exists()`` before
    calling here — so the two surfaces published PASS and MISSING for one
    figure on one archive, which is the divergence FR-053 and GI-024 exist to
    end. The test belongs in the reader both surfaces read, so it is here: the
    absence of the ledger is a property of the run directory, not of the
    command that happened to ask. measure-run's guard still fires first and
    still answers None; it now agrees with this reader instead of correcting
    it.
    """
    if not (run_dir / "defects.json").exists():
        return {
            "per_cycle": {},
            "total": 0,
            "measured_records": 0,
            "unmeasured_records": 0,
            "last_two_cycles": [],
            "verdict": "not_measurable",
            "verdict_reason": (
                "this run directory holds no defects.json at all, so there is "
                "no ledger to count `fallout_of` in; an absent ledger is not an "
                "empty one, and certifying the acceptance figure on it would "
                "put the strongest result in the report against the weakest "
                "possible evidence"
            ),
            "problem": None,
        }

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


def unreported_dispatch_inputs(run_dir: Path, *, stream_id_of=None) -> dict:
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

    ``stream_id_of`` is the roster resolver, optional, and it means here what it
    means at ``stream_rollup_rows``: with it, a cycle-bucket mapping whose KEY
    the roster knows counts as a stream even when it carries no ``records[]``,
    which is every bucket written before the replace semantics (D-111 / FR-054).
    Without it the walk is value-test-only, exactly as before. See that
    function's docstring for why the resolver is handed in rather than imported
    and why the shared predicate was not widened instead.

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
                entry = bucket.get(stream)
                # D-111 — the same two-part test `stream_rollup_rows` makes, so
                # the roster and the coverage table cannot come to disagree
                # about which keys in one bucket are streams. A tranche written
                # before `records[]` existed carries the stream's WORK, and
                # dropping it here dropped that stream's agent out of the
                # unreported-dispatch derivation entirely.
                if is_stream_record(entry) or (
                    isinstance(entry, dict)
                    and _resolves_to_stream(stream, stream_id_of)
                ):
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
# GI-033 lets a verifier module (gates, transitions, width) reach a leaf and
# nothing in the lifecycle layer, and lets a lifecycle module reach no verifier
# at all. It permits exactly one exception, and the boundary guard spells that
# exception as a one-edge frozenset, `_VERIFIER_TO_LIFECYCLE_SEAM`:
# transitions dispatching the halt token and the terminal seal into `halt.py`,
# one-way. None of the reads below is that seam. There is no table to record a
# SECOND exception in either -- D-035 deleted the one that existed, and the
# boundary pin now says so in its own failure message rather than offering a
# row to write.
#
# So every read below had to MOVE, and each was the same shape: a PURE READ of
# a run artifact that happened to be defined in the module whose feature it
# serves, so the module that needed the fact had to import the module that
# owned the feature. A leaf is the layer both sides may reach, which is why
# hosting the read here costs no exception at all.
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
#
#     ONE SHAPER CAME HERE ANYWAY, and the exception is named rather than left
#     for a reader to discover — `unrecorded_width_problem`, below, with
#     `inspect_mode_gap` and `WIDTH_RECORDING_TRANSITIONS` as one unit (D-080,
#     concern C-059 row 5, on the lead's ruling). Two facts about it defeat the
#     rule above rather than bending it. It is read from BOTH layers at once —
#     `gates.py` and `transitions.py` are verifier, `streams.py` is lifecycle —
#     and GI-033's arithmetic makes the two mutually unreachable, so a symbol
#     read from both can live only in a leaf. And its sentence cannot be split
#     per door the way `_blocking_defects`' hint was: its own docstring records
#     that SIX permissive per-door fallbacks once agreed on the wrong answer and
#     admitted a mode-less INSPECT as full width at every door at once, which is
#     what "ONE PREDICATE, SIX CALLERS" was written to end. Leaving the prose
#     with the doors would recreate exactly the defect the predicate is. So the
#     sentence travels with the predicate, and this bullet records why that is a
#     ruling about one symbol and not a new general licence.
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


#: The transitions GI-009 names as the ones that record a width, quoted in
#: every refusal that finds none, so the remedy is always a call the lead can
#: make rather than a fact about the archive.
#:
#: fallout GI-033 / AC-061 (D-080) — IT LIVES HERE AND NOT IN `schemas/vocab.py`,
#: for ARCHIVE_SCHEMA_VERSION's reason at the top of this module: a closed
#: vocabulary is a set of values a DOOR MAY ACCEPT, and this is a remedy
#: sentence with exactly one reader. The leaf may not import vocab either, so
#: landing it there would cost `unrecorded_width_problem` a second injected
#: argument at all six of its call sites — one sentence that never varies,
#: threaded through six places that could each get it wrong, which is the shape
#: that predicate's own docstring exists to end.
WIDTH_RECORDING_TRANSITIONS = (
    "Foundry-Phase(phase='cast') for the F2 entry, "
    "Foundry-Phase(phase='temper') for the F5 entry, or "
    "Foundry-Phase(phase='inspect_start') for a GRIND->INSPECT crossing"
)


def inspect_mode_gap(run_dir: Path, cycle: int, *, modes) -> str:
    """Why the newest ``inspect_modes`` entry is not cycle ``cycle``'s width.

    Diagnosis only — ``current_inspect_mode`` above is the one place the
    question is DECIDED, and this is called only after it has already answered
    None. It exists because D-216's refusal reads very differently depending on
    which of the three ways an archive can fail to carry this INSPECT's width it
    hit, and "cycle 2 has no recorded width" on a run whose state.json visibly
    holds thirteen inspect_modes entries sends the lead hunting for a file that
    is right there. NFR-005: the one line a terminal prints has to say what was
    found, not only what was missing.

    ``modes`` is `vocab.INSPECT_MODES`, passed in for the reason at the top of
    this section. Total; never raises.
    """
    entries = read_document(run_dir / "state.json")[0].get("inspect_modes")
    if not isinstance(entries, list) or not entries:
        return "nothing has recorded an inspect_modes entry for it"
    entry = entries[-1]
    if not isinstance(entry, dict) or entry.get("mode") not in modes:
        return (
            "the newest inspect_modes entry records no width this server "
            f"spells — {', '.join(sorted(modes))} are the only two"
        )
    stamped = entry.get("cycle")
    if isinstance(stamped, bool) or not isinstance(stamped, int):
        return "the newest inspect_modes entry carries no usable cycle stamp"
    return (
        f"the newest inspect_modes entry is stamped for cycle {stamped}, and a "
        f"width is a fact about ONE crossing — cycle {stamped}'s decision says "
        f"nothing about what cycle {cycle} was opened with"
    )


def unrecorded_width_problem(run_dir: Path, *, modes) -> dict | None:
    """``{"reason", "hint"}`` when this INSPECT has NO recorded width, else None.

    D-117 — AN UNRECORDED WIDTH IS NOT FULL WIDTH.
    ---------------------------------------------
    GI-009's named violation is "A first INSPECT of a phase with no recorded
    mode" and GI-008's is "a streams-complete check that reads a roster nothing
    recorded". Every consumer degraded PERMISSIVELY instead of refusing, and
    each degradation was individually defensible — "a resumed archive should get
    the pre-change behaviour" — while together they admitted a mode-less INSPECT
    as full width at every door at once:

      the streams-complete check fell back to the pre-width roster
      trace/prove/test, so `research_audit` and `test01` were never required;
      `foundry_gate('assay')` tested `mode != "DELTA"`, which "unrecorded"
      passes; `inspect_clean`'s DELTA refusal tested `== "DELTA"`, which
      "unrecorded" also passes; and the display-time TRACE fence handled FULL
      and DELTA explicitly then fell through to a legacy last-clean-TRACE
      predicate and auto-stamped `.trace-complete`. That fence is gone, and so
      is the predicate (fallout D-057): `width._trace_skip_from_width` decides at
      the transition and there is no third answer for an unrecorded width to
      fall into.

    Driven end to end through the shipped `Foundry-Init(resume=...)`, which
    reactivates any archive with whatever `state.json` it holds, on a
    legacy-shaped archive with the committed evidence deliberately stale at
    HEAD: streams-complete required only trace, prove and test and reported
    complete; `inspect_clean` returned ok and moved the run to F4; and
    `Foundry-Gate('assay')` PASSED carrying the checklist line
    `inspect_ran_at_full_width (mode=unrecorded rule=unrecorded) ok=True` — the
    assertion that is false. With a `.trace-clean-at` marker present TRACE never
    ran either. ASSAY opened having run PROVE and TEST only, over an evidence
    corpus no boundary sweep had ever re-executed.

    ONE PREDICATE, SIX CALLERS. The permissive fallbacks were six separate
    judgement calls in six functions, which is exactly how they came to agree on
    the wrong answer without any of them saying so. The refusal names the
    missing record AND the transition that writes it, because "there is no
    recorded width" is not an action.

    fallout GI-033 / AC-061 (D-080) — WHY THE SENTENCE CAME TO THE LEAF WITH THE
    FACT, when `_blocking_defects`' hint deliberately did not. This is read from
    both layers at once — `gates.py` and `transitions.py` are verifier modules,
    `streams.py` is lifecycle — and GI-033 makes those two mutually unreachable,
    so it can live nowhere but here. Splitting the refusal prose per door to
    leave a pure counter behind is not available to THIS predicate: six per-door
    judgements agreeing on the wrong answer is the defect it was written for, so
    the shape that would satisfy the layering rule is the shape that reopens
    D-117. It stays SINGLE.

    ``modes`` is `vocab.INSPECT_MODES`, passed in for the reason at the top of
    this section. ``check_streams_complete`` further down takes a same-named
    INJECTION parameter which SHADOWS this function inside that body: its
    default is still None and omitting it still skips the arm, because whether a
    composition refuses on this is the CALLER's decision and not a fallback this
    module makes for it. Total; never raises.
    """
    if current_inspect_mode(run_dir, modes=modes) is not None:
        return None
    cycle = current_cycle(run_dir)
    return {
        "reason": (
            f"this INSPECT (cycle {cycle}) has no recorded width — "
            f"{inspect_mode_gap(run_dir, cycle, modes=modes)}, so the roster, "
            "the rule and the evidence sweep it was opened with are all unknown"
        ),
        "hint": (
            "An INSPECT is opened by the transition that decides and records "
            "its width (GI-009), and an unrecorded width is never read as FULL: "
            "a roster nothing recorded is not a roster that ran. Cross the "
            f"boundary that records one — {WIDTH_RECORDING_TRANSITIONS} — "
            "which also sweeps the evidence corpus at HEAD, then run exactly "
            "the roster it names."
        ),
    }


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


def registered_team_dirs(run_dir: Path) -> list[str]:
    """The teams the run's ledger has registered, in recorded order.

    THE ARTIFACT HALF of "is a team still holding the tree". A team is active
    from the `Foundry-Team-Up` that writes its name into `state.json`
    `active_teams` until the `Foundry-Team-Down` that removes it, and nothing
    else starts or ends one.

    should-not-stop GI-001 / GI-010 / FR-021 (A-005) — THE LEDGER IS THE WHOLE
    ANSWER. This used to count a registered name only while
    `~/.claude/teams/<name>` was still a directory, on the theory that the
    directory going away was what `TeamDelete` did. `TeamCreate` and
    `TeamDelete` were removed as Claude Code tools in v2.1.178, so no directory
    is ever created: every registered team read as ended the moment it was
    registered, and every gate that asks this question was answering from a
    directory no step of the protocol makes. Teammates are named Agent spawns;
    a team is a ledger entry and is read as one. The name is kept because the
    readers that compose it are named for it.

    The OTHER half is a scan of the machine for live teammate panes, which
    reads no run artifact and belongs with the module that knows how to look;
    so does the hint naming SendMessage and `tmux kill-pane`. Both halves must
    be clear for a gate to pass, and this is the half a run artifact can
    answer. Total: a non-list roster and a non-string member each contribute
    nothing rather than raising.
    """
    state, _ = read_document(run_dir / "state.json")
    teams = state.get("active_teams")
    return [
        name for name in (teams if isinstance(teams, list) else [])
        if isinstance(name, str) and name
    ]


def sight_required(
    run_dir: Path,
    *,
    shape_problem,
    no_ui_meaning: str,
    project_root: Path | None = None,
    directory_suffix: str = "/",
) -> dict:
    """Whether the SIGHT browser audit is part of this run, from the manifest.

    Returns ``{"required": bool}`` plus, as they apply, ``blocked``, ``no_ui``,
    ``ui_files``, ``url``, ``undetermined_directories`` and ``reason``. A run
    with no manifest yet requires nothing, which is what is true of it.

    ``project_root`` and ``directory_suffix`` are optional and both concern
    DIRECTORY `key_files` entries; see the C-081 note in the body for why a
    directory entry used to read as "no frontend files" and what each argument
    buys. A caller that passes neither gets the same verdicts it always did,
    with a reason that no longer overstates what was measured.

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
    entries = [
        f
        for casting in data.get("castings", [])
        if isinstance(casting, dict)
        for f in (casting.get("key_files") or [])
        if isinstance(f, str) and f.strip()
    ]
    # fallout C-081 / D-170 — A DIRECTORY ENTRY IS NOT "NO FRONTEND FILES".
    #
    # A `key_files` entry is a file path OR a directory spelled with a trailing
    # slash covering everything beneath it (`foundry_validate._key_file_covers`
    # states the format). This scan asked `f.endswith(ui_exts)` of every entry,
    # and a directory ends in a slash, so a casting that owns a UI package by
    # naming it once contributed ZERO ui_files — the spelling the cast gate's
    # eight-entry cap pushes a lead into, and the one this run's own manifest
    # uses. The answer was then `required: False, "No frontend files in
    # castings"`: a whole verification stream skipped on a run that has a
    # frontend, under a reason that reads as a measurement rather than a miss.
    # GI-002 is that every verification stream keeps existing, and this was a
    # way for one to stop existing with nobody deciding to drop it.
    #
    # A directory's extension is unknowable from the manifest alone, so there
    # are exactly two honest answers and this returns whichever it can:
    #
    #   * WALK IT, when the caller passes `project_root`. Then the question is
    #     answered for real and a directory of `.tsx` files requires SIGHT.
    #   * SAY SO, when it does not. The entries are counted, reported on the
    #     answer as `undetermined_directories`, and named in `reason` — so the
    #     skip is visible instead of silent.
    #
    # `required: True` on an undetermined directory was considered and is
    # wrong: this run's own manifest names `tools/orchestration/` with no
    # frontend anywhere, so it would block the cast gate of every backend run
    # that fits under the cap. Fail-VISIBLE is the improvement available to a
    # reader that cannot see the disk; fail-CLOSED here would be fail-wrong.
    #
    # `directory_suffix` is injected with a default rather than imported: this
    # module holds the leaf contract (no package imports, so `measure-run.py`
    # keeps its package-free read), which is why `shape_problem` and
    # `no_ui_meaning` arrive the same way. The default is the format's actual
    # spelling, so a caller that passes neither new argument gets today's
    # behaviour plus the honest reason.
    directories = [f for f in entries if f.endswith(directory_suffix)]
    ui_files = [f for f in entries if f.endswith(ui_exts)]

    undetermined: list[str] = []
    for entry in directories:
        if project_root is None:
            undetermined.append(entry)
            continue
        # AN ABSENT DIRECTORY IS UNDETERMINED, NOT EMPTY, and this is the case
        # that matters most. `rglob` on a path that does not exist yields
        # nothing and raises nothing, so a missing directory would otherwise
        # read as "walked it, no frontend" — which is the SAME fail-open in a
        # new costume, and it fires exactly where C-081 hurts: at the CAST
        # gate of a greenfield run, where the UI package the manifest declares
        # has not been built yet. Existence is checked first so "not there" is
        # reported as not measured. Total: never raises, whatever the path is.
        try:
            root = Path(project_root) / entry
            walkable = root.is_dir()
            found = (
                [
                    str(p)
                    for p in root.rglob("*")
                    if p.is_file() and p.name.endswith(ui_exts)
                ]
                if walkable
                else []
            )
        except (OSError, ValueError):
            walkable, found = False, []
        if not walkable:
            undetermined.append(entry)
        ui_files.extend(found)

    if data.get("no_ui", False):
        return {
            "required": False,
            "blocked": False,
            "no_ui": True,
            "ui_files": len(ui_files),
            "reason": no_ui_meaning,
        }
    if not ui_files:
        if undetermined:
            # The miss, named. Every word here is a fact the reader can act on:
            # which entries were not inspected, and the one argument that would
            # have inspected them.
            return {
                "required": False,
                "undetermined_directories": len(undetermined),
                "reason": (
                    f"No frontend files among the "
                    f"{len(entries) - len(directories)} file entries in "
                    f"castings; {len(undetermined)} directory entry(s) "
                    f"({', '.join(sorted(undetermined))}) were NOT inspected, "
                    f"so this is not a measurement that the run has no "
                    f"frontend. Pass project_root to walk them."
                ),
            }
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


# --------------------------------------------------------------------------- #
# fallout GI-033 / FR-063 / AC-061 / D-021 / D-035 (concerns C-027, C-030) —
# THE LEAF MOVES, and the arithmetic that makes them the only available home.
#
# The boundary guard forbids verifier -> lifecycle AND lifecycle -> verifier at
# module top, with one enumerated seam (transitions -> halt). That makes the
# two layers mutually unreachable, so a symbol read by BOTH — say
# `orchestration/transitions.py` (verifier) and `orchestration/guidance.py`
# (lifecycle) — can live in neither. It must live in a leaf, and orchestration
# modules cannot be leaves under the guard. Every reader below is one such
# symbol, moved here from the layer that happened to declare it first.
#
# EVERY ONE IS A READ. Nothing here writes a run artifact: the writers and the
# rule engines stayed where they were, for the reasons the section above this
# one already gives. A pane scan is a read of the MACHINE rather than of the
# run directory, which is the one line the ruling drew differently from this
# module's older `json and pathlib` phrasing — a readers-only leaf that lists
# panes read-only is still a leaf, and the alternative was a gate that passed
# while teammates were still holding the tree.
#
# NO PARITY PINS. Casting 2 and casting 1 delete their copies and repoint in
# the same wave, so a test asserting "this equals the copy it came from" would
# be comparing an expression to itself and then to nothing. Every test for
# these is built on hand-written expected values.
# --------------------------------------------------------------------------- #


def persisted_max_cycles(state: dict) -> int:
    """CT-016 — THE ONE READ of `state.json.max_cycles`, in the door's terms.

    Returns the cap in force: a positive int, or 0 for "no cap", which is the
    default and means unbounded.

    D-225 — THE DOOR ACCEPTED A CAP THIS READ SILENTLY DISCARDED.
    ------------------------------------------------------------
    `Foundry-Init` advertises `max_cycles` as `{"type": "integer"}` and
    `server.py` validates it with Draft202012Validator, in which a zero-fraction
    float IS an integer — so `2.0` is ACCEPTED at the door and persisted as
    `2.0`. The guard below read `isinstance(max_cycles, int)`, and
    `isinstance(2.0, int)` is False, so the cap read as absent. Driven: cap 2.0
    persisted, counter at 99, `Foundry-Phase('grind_start')` returned ok True
    and phase F3 — an operator who asked for a cap of 2 opened GRIND cycle 100
    with no notice. The schema had no `minimum` either, so `-1` was accepted at
    the same door and read here as unbounded.

    The fix is on BOTH sides and they meet exactly: the door advertises
    `minimum: 0`, so a negative cap is refused where the operator can see it
    rather than discarded here; and this read accepts the zero-fraction float
    the schema calls an integer, because JSON has no integer type and `2.0` is
    the integer 2 by the rule the door validated against.

    WHY NORMALISE HERE RATHER THAN ONLY AT THE DISPATCH. `state.json` is not
    always written by this server's current door — a resumed archive, a
    hand-edited file, a fixture — and the deciding read is the one place that
    must never mistake a cap for its absence. Anything that is not a usable cap
    (a string, a fractional float, a bool, a negative) reads as 0/no cap,
    because this function cannot refuse: it is consulted from inside a
    transition whose only other answer is "proceed".

    `bool` is tested FIRST because it is an `int` subclass and `True` is not a
    cap of 1.
    """
    raw = state.get("max_cycles", 0)
    if isinstance(raw, bool):
        return 0
    if isinstance(raw, int):
        return raw if raw > 0 else 0
    if isinstance(raw, float) and raw.is_integer():
        return int(raw) if raw > 0 else 0
    return 0


def finalize_open_phase_entry(entry: dict, now: str) -> None:
    """Stamp `ended_at` and `duration` on an OPEN `phase_times` entry.

    Mutates in place and returns None. An entry with no `started_at`, or one
    that already carries `ended_at`, is left exactly as it is — closing a phase
    twice would overwrite the moment it really ended with the moment somebody
    looked.

    TWO CALLERS IN TWO LAYERS, which is why it is here: the phase transition
    closes every still-open entry before opening the new one, and the passive
    sub-phase stamping closes F0 / F0.5 / F0.9 from file-state signals. One
    implementation or the two eventually disagree about what a closed phase
    looks like, and the pair sits across the layer boundary.

    UNPARSEABLE TIMESTAMPS STILL CLOSE THE ENTRY. `ended_at` is written before
    the arithmetic, so a `started_at` that is not ISO-8601 — a hand-edited
    archive, a fixture — leaves a closed entry with no `duration` rather than
    an entry that stays open forever and is re-closed at every later
    transition.

    ``datetime`` is imported INSIDE the function, the convention this module's
    other time readers already follow for the leaf-contract reason stated at
    ``handoffs_wall_clock_seconds``.
    """
    from datetime import datetime

    if "started_at" not in entry or "ended_at" in entry:
        return
    entry["ended_at"] = now
    try:
        start = datetime.fromisoformat(entry["started_at"])
        end = datetime.fromisoformat(now)
        delta = end - start
        mins = int(delta.total_seconds() // 60)
        secs = int(delta.total_seconds() % 60)
        entry["duration"] = f"{mins}m {secs}s"
    except (ValueError, KeyError, TypeError):
        pass


def open_cross_casting_concerns(
    run_dir: Path, *, status_open: str, cycle: int | None = None
) -> list[dict]:
    """Open concerns whose target casting is NOT the casting that filed them.

    Returns the records themselves, in ledger order. Pass `cycle` to scope the
    answer to one GRIND; omit it for every open cross-casting concern.

    "Unaddressed" is `status == status_open` and nothing else (GI-023 / ST-005
    / FR-039): `dispatched` is the mark Foundry-Tasks leaves when the concern
    reaches the casting that owns it, and a dispatched concern has been
    addressed by definition. The status member is PASSED IN rather than typed
    here, on the same rule every other closed-set value in this module follows.

    fallout GI-033 (concern C-030) — WHY THE READ IS THE LEAF'S. The INSPECT
    door refuses on this list, and `orchestration/transitions.py` is a VERIFIER
    module while `tools/concerns.py` reaches `tools/foundry.py` at module top
    for its ledger apparatus — so the edge did not merely cross into the
    lifecycle layer, it pulled the largest lifecycle module in the tree across
    with it. The ledger's WRITERS stay in `tools/concerns.py` with the
    transaction and the markdown render; this is the read, and casting 1's own
    reader delegates here so there is one implementation (GI-024).

    A record with no `target_casting_id` is not a cross-casting concern: it
    named a target nothing resolved to a casting, and the door may not refuse
    on a concern that lands on nobody. The comparison is on `str()` of both
    sides because a manifest may carry casting ids as integers and a filing may
    carry the same id as a string.
    """
    document, _problem = read_document(Path(run_dir) / "concerns.json")
    records = document.get("concerns")
    out: list[dict] = []
    for record in records if isinstance(records, list) else []:
        if not isinstance(record, dict):
            continue
        if record.get("status") != status_open:
            continue
        if cycle is not None and record.get("cycle") != cycle:
            continue
        target_casting = record.get("target_casting_id")
        if target_casting is None:
            continue
        if str(target_casting) == str(record.get("source_casting")):
            continue
        out.append(record)
    return out


#: The pane titles that mark a tmux pane as a Claude Code teammate rather than
#: one of the operator's own. Claude Code prefixes teammate titles with "@";
#: the phase and stream names are foundry's own agent naming. A pane matching
#: none of these is the USER's and is never counted, never listed and never
#: killed.
TEAMMATE_PANE_TITLE_PATTERN = (
    r"^@|"
    r"cast[-_]|grind[-_]|inspect[-_]|"
    r"assay[-_]|temper[-_]|decompose[-_]|"
    r"trace[-_]|prove[-_]|sight[-_]|"
    r"test[-_]|probe[-_]|"
    r"^teammate-|^agent-"
)


def _pane_pid_has_children(pid: str) -> bool:
    """True when a pane's process has children — i.e. an agent is still running.

    A live teammate's shell has the agent as a child; a zombie pane is the
    shell alone. Anything that is not a plain decimal PID is False rather than
    an argument handed to `pgrep`.
    """
    import subprocess

    if not pid or not pid.strip().isdigit():
        return False
    try:
        result = subprocess.run(
            ["pgrep", "-P", pid.strip()], capture_output=True, timeout=3
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return False


def live_teammate_panes() -> dict:
    """Scan tmux and classify every pane. Returns, and never raises::

        {"available": bool,               # tmux answered at all
         "live":   [(pane_id, title, cmd)],   # teammate, agent still running
         "zombie": [(pane_id, title, cmd)],   # teammate, dead or childless
         "user":   [(pane_id, title, cmd)],   # not a teammate — never touched
         "lead":   (pane_id, title) | None}   # the active pane

    THE OTHER HALF of "is a team still holding the tree", beside
    `registered_team_dirs`. Both must be clear for a gate to pass: a check that
    took only the artifact half passes while teammates are still running, which
    is the drive this exists for.

    fallout GI-033 / D-021 / D-035 (concern C-027) — WHY AN ENVIRONMENT READ IS
    LEAF MATERIAL. `orchestration/teams.py` is LIFECYCLE and the gates and
    transitions that ask the question are VERIFIER, so the scan could be
    reached from only one of the two layers wherever it sat inside
    orchestration. It is a READ with no writer — it lists panes and kills
    nothing; `_kill_panes` stays in the lifecycle module with `register` and
    `unregister` — and a readers-only leaf that reads the machine instead of
    the run directory is still a leaf. That is the one line this module's older
    "json and pathlib" phrasing drew differently, and it was a casting
    convention rather than the spec.

    CLASSIFICATION, and why it is not just `pane_current_command`. For a live
    teammate that command is the Claude Code VERSION NUMBER (e.g. "2.1.80"),
    not "claude" or "node"; a zombie shows "bash"/"zsh" because the agent
    exited and the shell is what is left. So the title says WHETHER a pane is a
    teammate and the child-process check says whether it is still working, and
    neither alone is enough.

    Every failure mode — no tmux binary, tmux not answering, a timeout, a
    malformed row — degrades to `available: False` or to skipping the row. A
    gate that cannot see the machine must not therefore believe it is empty:
    `available` is what tells a caller "this half could not be checked" apart
    from "this half is clear".
    """
    import re
    import subprocess

    empty: dict = {
        "available": False, "live": [], "zombie": [], "user": [], "lead": None,
    }
    try:
        check = subprocess.run(["tmux", "list-sessions"], capture_output=True, timeout=5)
        if check.returncode != 0:
            return empty
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return empty

    teammate_re = re.compile(TEAMMATE_PANE_TITLE_PATTERN, re.IGNORECASE)

    try:
        result = subprocess.run(
            ["tmux", "list-panes", "-a", "-F",
             "#{session_name}:#{window_index}.#{pane_index}\t"
             "#{pane_title}\t#{pane_dead}\t#{pane_current_command}\t"
             "#{pane_active}\t#{pane_pid}"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode != 0:
            return empty
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return empty

    live: list = []
    zombie: list = []
    user: list = []
    lead = None

    for line in result.stdout.strip().split("\n"):
        if not line.strip():
            continue
        parts = line.split("\t", 5)
        if len(parts) < 6:
            continue
        pane_id, title, dead, cmd, active, pid = parts

        if active == "1":
            lead = (pane_id, title)
            continue
        if not teammate_re.search(title):
            user.append((pane_id, title, cmd))
            continue
        if dead == "1":
            zombie.append((pane_id, title, cmd))
            continue
        if _pane_pid_has_children(pid):
            live.append((pane_id, title, cmd))
        else:
            zombie.append((pane_id, title, cmd))

    return {"available": True, "live": live, "zombie": zombie, "user": user, "lead": lead}


def active_teams(run_dir: Path, *, hint_for=None, scan=None) -> dict:
    """Is any team still holding the tree? Both halves, composed. Never raises.

    Returns ``{"active": bool, "teams": [names], "live_panes": [titles]}``,
    plus ``hint`` when ``hint_for`` is given and there are live panes but no
    registered team left — the case where the roster says the team is gone and
    the machine says it is not, which is the one an operator cannot work out
    from the two lists alone.

    TWO LAYERS, BOTH REQUIRED. `registered_team_dirs` answers the artifact
    half: the `state.json.active_teams` ledger, which `Foundry-Team-Up` writes
    and `Foundry-Team-Down` clears (should-not-stop GI-001 / GI-010, A-005 —
    no `~/.claude/teams` directory is consulted, because nothing makes one any
    more). `live_teammate_panes` answers the machine half. Either one alone lets
    a gate pass while teammates are still running, which is why the composition
    is here and not at each caller.

    ``hint_for`` takes the live pane titles and returns the shutdown sentence —
    protocol prose naming SendMessage and `tmux kill-pane`, which belongs to the
    lifecycle module that owns those doors, so it is passed in on the same rule
    every other sentence in this module follows. ``scan`` is the pane reader,
    defaulting to `live_teammate_panes`; a test passes its own rather than
    shelling out to a tmux the machine may not have.
    """
    teams = registered_team_dirs(run_dir)

    panes = (scan or live_teammate_panes)()
    live_panes: list[str] = []
    if isinstance(panes, dict) and panes.get("available") and panes.get("live"):
        live_panes = [
            row[1] for row in panes["live"]
            if isinstance(row, (list, tuple)) and len(row) > 1 and isinstance(row[1], str)
        ]

    result: dict = {
        "active": bool(teams) or bool(live_panes),
        "teams": teams,
        "live_panes": live_panes,
    }
    if live_panes and not teams and hint_for is not None:
        result["hint"] = hint_for(live_panes)
    return result


def marker_counts(marker: Path) -> dict | None:
    """Parse a ``.{stream}-complete`` marker's ``key=value`` body. Never raises.

    Returns ``{"items_checked", "items_total", "findings"}`` with ``findings``
    possibly None (the key absent), or None when the marker is not there.

    THE LOAD PATH FOR ARCHIVES WRITTEN BEFORE THE PER-CYCLE ROLL-UP EXISTED —
    those runs have markers and no `stream-rollup.json`, and the coverage
    threshold has to be measurable on them.

    AN UNREADABLE MARKER IS NOT AN ABSENT ONE (D-098). A marker with one
    non-UTF-8 byte still yields the zero-counts record, because None means "no
    marker" and a PRESENT marker whose numbers cannot be read must FAIL the
    coverage threshold rather than skip it. `read_text_file` is the total read
    that makes the distinction reachable: UnicodeDecodeError is a ValueError,
    not an OSError, and an `except OSError` around this raised straight
    through.
    """
    marker = Path(marker)
    if not marker.exists():
        return None
    text, _problem = read_text_file(marker)
    counts: dict = {"items_checked": 0, "items_total": 0, "findings": None}
    for line in text.splitlines():
        for key in ("items_checked", "items_total", "findings"):
            if line.startswith(f"{key}="):
                try:
                    counts[key] = int(line.split("=", 1)[1].strip())
                except (ValueError, IndexError):
                    pass
    return counts


def rollup_totals(run_dir: Path, cycle: int, stream: str) -> dict | None:
    """This cycle's accumulated totals for one stream, or None. Never raises.

    Returns ``{"items_checked", "items_total", "findings", "records"}``, where
    ``records`` is how many tranches the bucket kept.

    None means the cycle has no record for that stream AT ALL, which callers
    must be able to tell from a recorded zero: "nobody ran it" and "it ran and
    found nothing" are different answers, and only the first may fall back to
    the marker.
    """
    document, _problem = read_document(Path(run_dir) / "stream-rollup.json")
    cycles = document.get("cycles")
    bucket = cycles.get(str(cycle)) if isinstance(cycles, dict) else None
    entry = bucket.get(stream) if isinstance(bucket, dict) else None
    if not isinstance(entry, dict):
        return None
    records = entry.get("records")
    return {
        "items_checked": as_count(entry.get("items_checked")),
        "items_total": as_count(entry.get("items_total")),
        "findings": as_count(entry.get("findings")),
        "records": len(records) if isinstance(records, list) else 0,
    }


def recorded_prove_roster(
    run_dir: Path, cycle: int, *, modes
) -> list[str] | None:
    """The PROVE rows THIS cycle's recorded DELTA decision named, or None.

    None means "no DELTA roster applies to this cycle" — the run is at FULL,
    the run predates the width, or the newest recorded decision belongs to
    another cycle — and the caller then measures against the spec exactly as it
    did before the width existed. An empty LIST is a DIFFERENT answer: the
    decision recorded a roster and the roster is empty, which happens only when
    the spec parses to zero rows.

    THE CYCLE MUST MATCH, and that is not defensive padding (D-216). A width is
    a fact about one crossing: a roster decided for cycle 5 says nothing about
    what cycle 4's PROVE owed. On the live path the two are equal by
    construction, so the check costs nothing there, and a resumed archive whose
    counter and ledger disagree falls back rather than measuring one cycle's
    work against another cycle's roster. The match is made by
    `current_inspect_mode`, which takes the cycle as its subject.
    """
    recorded = current_inspect_mode(run_dir, cycle, modes=modes)
    if not recorded or recorded.get("mode") != "DELTA":
        return None
    sample = recorded.get("prove_sample")
    if not isinstance(sample, list):
        return None
    return [row for row in sample if isinstance(row, str)]


def coverage_shortfall(
    run_dir: Path,
    stream: str,
    cycle: int,
    *,
    marker_of,
    modes,
    spec_requirement_count: int,
) -> dict | None:
    """This stream's per-cycle coverage threshold, evaluated once on the total.

    Returns a named shortfall dict, or None when the stream has no threshold or
    clears it. Called from `check_streams_complete` — the streams-complete
    check is the single point where the whole cycle's records are in hand
    (CT-003). The mark-stream door deliberately does NOT evaluate it: a partial
    tranche must be stored, not refused.

    Falls back to the marker's aggregate counts when this cycle has no roll-up
    entry. Without the fallback, an archive written before the roll-up existed
    had a marker the streams-complete check counted as PRESENT while the
    threshold silently evaluated nothing, so 40% coverage passed. "No numbers"
    must mean "read them from the marker", never "assume the threshold is met".

    D-080 — THE PROVE THRESHOLD IS MEASURED AGAINST THE WIDTH THE SERVER
    RECORDED. The streams-complete check reads the recorded roster — GI-008
    names "a streams-complete check that reads a roster nothing recorded" as
    the violation — and then hands each member of it here, so this must consult
    the same recorded decision. Measuring a DELTA cycle against the whole spec
    made DELTA unreachable on the guided path: a PROVE that checked exactly the
    roster the server itself recorded was reported incomplete forever.

    NO 0.95 SLACK ON THE DELTA ARM. At FULL the denominator is the whole spec
    and the 5% is tolerance for a matrix that moved under a long stream. A
    DELTA roster is a NAMED, FINITE list of rows the server itself drew, so
    "which of these did you not check" has an answer and there is nothing to be
    tolerant of. `checked >= len(roster)` is the whole test.

    ``spec_requirement_count`` is PASSED IN — the spec-path climb and the
    requirement-ID grammar both live in leaf modules this one may not import,
    and which spec is this run's is their question, not this one's.
    """
    totals = rollup_totals(run_dir, cycle, stream) or marker_counts(
        Path(run_dir) / marker_of(stream)
    )
    if totals is None:
        return None
    checked = totals["items_checked"]

    if stream == "prove":
        roster = recorded_prove_roster(run_dir, cycle, modes=modes)
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

        if spec_requirement_count > 0 and checked < spec_requirement_count * 0.95:
            pct = checked / spec_requirement_count * 100
            return {
                "stream": "prove",
                "checked": checked,
                "required": spec_requirement_count,
                "coverage": f"{pct:.0f}%",
                "reason": (
                    f"PROVE checked {checked} requirements across cycle {cycle} but the "
                    f"spec has {spec_requirement_count}. Coverage is {pct:.0f}% "
                    "— must be ≥95%."
                ),
            }
        return None

    if stream == "trace":
        declared = totals["items_total"]
        if declared > 0 and checked < declared * 0.95:
            pct = checked / declared * 100
            return {
                "stream": "trace",
                "checked": checked,
                "required": declared,
                "coverage": f"{pct:.0f}%",
                "reason": (
                    f"TRACE checked {checked}/{declared} symbols across cycle {cycle} "
                    f"({pct:.0f}%). Must check ≥95% of declared symbols."
                ),
            }
    return None


def check_streams_complete(
    run_dir: Path,
    *,
    modes,
    marker_of,
    sight: dict,
    spec_requirement_count: int,
    inspect_phases,
    fallback_streams,
    unrecorded_width_problem=None,
) -> dict:
    """Have this cycle's required verification streams completed? Never raises.

    Returns ``{"complete", "missing", "required", "shortfalls",
    "inspect_mode", "inspect_rule", "stream_scope"}``; the unrecorded-width arm
    additionally carries ``unrecorded_width``, ``reason`` and ``hint``.
    ``missing`` is a SPACE-JOINED STRING, not a list, because every existing
    caller blocks on it being non-empty.

    THE ROSTER IS READ, NOT RECOMPUTED (AC-017 / ST-006 / ST-007 / GI-008).
    The transition that opened this INSPECT already decided which streams it
    requires and at what scope, and recorded both. Re-deriving them here would
    let the check disagree with the cycle that actually ran: the mode is a
    function of a GRIND diff measured at the boundary, and by the time this is
    called the tree has moved on.

    D-117 — A RUN WITH NO RECORDED DECISION IS INCOMPLETE, NOT PRE-CHANGE. This
    used to fall back to the roster the function required before the width
    existed and call that "what a resumed archive should get". That is exactly
    GI-008's named violation, and the fallback silently dropped two streams from
    every mode-less INSPECT while reporting `complete: True` — which is how
    ASSAY came to open on a three-stream roster. `missing` carries the sentinel
    `inspect_mode` so every existing caller blocks here too without being taught
    a new key.

    SCOPED TO A RUN THAT IS ACTUALLY IN AN INSPECT. The width belongs to an
    INSPECT, so a run in none has no INSPECT whose width could be missing — and
    this is also the plain "did these markers record and clear the threshold"
    query, which a run that never entered F2 may ask.

    WHAT IS INJECTED, AND WHY EACH ONE (fallout GI-033, concern C-027). This is
    the composition; the pieces that hold DOOR PROTOCOL stay with their doors,
    on the rule stated at the top of the reader section:

      * ``unrecorded_width_problem`` — the refusal that names the transitions
        which record a width and the remedy. That is door protocol, so it stays
        in the module that owns those transitions and is passed in by the
        callers that refuse on it. Omit it and the arm is skipped, which is
        correct for a caller that only REPORTS: the two doors that refuse ask
        the shaper themselves rather than inferring it from this result.
      * ``sight`` — the already-computed `sight_required` answer, so this makes
        no second decision about whether a run has a browsable UI.
      * ``modes``, ``inspect_phases``, ``fallback_streams``, ``marker_of`` and
        ``spec_requirement_count`` — closed-set values and leaf helpers this
        module may not import, passed in exactly as `sight_required`'s
        ``shape_problem`` and ``no_ui_meaning`` are.
    """
    run_dir = Path(run_dir)
    state, _problem = read_document(run_dir / "state.json")

    recorded = current_inspect_mode(run_dir, modes=modes)
    inspect_mode = ""
    inspect_rule = ""
    stream_scope: dict = {}

    in_inspect = state.get("phase") in tuple(inspect_phases)
    unrecorded = (
        unrecorded_width_problem(run_dir)
        if in_inspect and unrecorded_width_problem is not None
        else None
    )
    if unrecorded is not None:
        return {
            "complete": False,
            "missing": "inspect_mode",
            "required": [],
            "shortfalls": [],
            "inspect_mode": "",
            "inspect_rule": "",
            "stream_scope": {},
            "unrecorded_width": True,
            "reason": unrecorded["reason"],
            "hint": unrecorded["hint"],
        }

    if isinstance(recorded, dict) and isinstance(recorded.get("required_streams"), list):
        required = [s for s in recorded["required_streams"] if isinstance(s, str)]
        inspect_mode = recorded.get("mode", "")
        inspect_rule = recorded.get("rule", "")
        raw_scope = recorded.get("stream_scope")
        stream_scope = raw_scope if isinstance(raw_scope, dict) else {}
    else:
        # A recorded entry carrying a mode but no usable roster. The width IS
        # recorded, so this is not the D-117 hole; the roster is rebuilt from
        # the run's own manifest exactly as it was before the width existed.
        required = list(fallback_streams)
        manifest, _mproblem = read_document(run_dir / "castings" / "manifest.json")
        if sight.get("required"):
            required.append("sight")
        if manifest.get("target_url", ""):
            required.append("probe")
        inspect_mode = (recorded or {}).get("mode", "")
        inspect_rule = (recorded or {}).get("rule", "")

    missing = [s for s in required if not (run_dir / marker_of(s)).exists()]

    cycle = current_cycle(run_dir)
    shortfalls = []
    for s in required:
        if s in missing:
            continue
        shortfall = coverage_shortfall(
            run_dir, s, cycle,
            marker_of=marker_of, modes=modes,
            spec_requirement_count=spec_requirement_count,
        )
        if shortfall:
            shortfalls.append(shortfall)
            missing.append(s)

    return {
        "complete": len(missing) == 0,
        "missing": " ".join(missing),
        "required": required,
        "shortfalls": shortfalls,
        # Reported, never decided here (GI-008). Empty strings on a run with no
        # recorded decision, which is how a caller tells "this run predates the
        # width" from "this run is at FULL".
        "inspect_mode": inspect_mode,
        "inspect_rule": inspect_rule,
        "stream_scope": stream_scope,
    }
