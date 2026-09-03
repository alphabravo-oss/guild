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


def derive_cycle_count(run_dir: Path) -> dict:
    """The run's GRIND cycle count. ONE derivation, read by every surface.

    Returns, and never raises::

        {"count": int | None,     # cycles EXECUTED — index + 1
         "index": int | None,     # the server's 0-based counter at the end
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

    known = [v for v in (state_cycle, rollup_highest, defect_max) if v is not None]
    index = max(known) if known else None
    return {
        "count": None if index is None else index + 1,
        "index": index,
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
