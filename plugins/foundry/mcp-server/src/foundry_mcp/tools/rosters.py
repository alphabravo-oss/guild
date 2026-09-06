"""The per-stream roster ledger — FR-024 / FR-050 / CT-002 / ST-010 / AC-032 / OT-030.

A-019: "``rosters/<stream>.json`` written by a new ``Foundry-Roster`` tool at
first derivation; later cycles read it"

Three behaviours this module establishes, each of which the old shape got wrong
for the same underlying reason — *a list that is re-derived every cycle has no
identity across cycles*:

  1. NO FILE PERSISTED A ROSTER. ``RA-1..RA-n`` existed only inside
     ``agents/research-auditor.md``'s prose and that agent's returned JSON; a
     grep for ``RA-[0-9]`` across ``foundry_mcp/`` found nothing. Every cycle's
     auditor re-derived its list from ``research/`` plus the spec's
     ``## Informational`` section, so RA numbering was not stable across
     cycles and the ``HONORED -> IGNORED`` regression check had no prior state
     to compare against.
  2. COVERAGE WAS A BARE COUNT. ``research_audit`` reached
     ``stream-rollup.json`` as ``items_checked`` alone — daring-orca's cycle 29
     records 4/4 with, in the survey's words, "no way to know which four". The
     persisted roster is what makes ``items_total`` checkable rather than
     asserted: casting 2's ``Foundry-Stream`` refuses a record whose
     ``items_total`` differs from the roster length (CT-003, OT-031), and
     ``roster_length`` below is the reader it asks.
  3. A REWRITE LOST THE PRIOR LIST SILENTLY. The write-once door
     (``ROSTER_EXISTS``) makes a second derivation an explicit act:
     ``revise=true`` with a reason, and the PRIOR items are kept under
     ``revisions[]`` rather than replaced, because GI-006 forbids a
     replace-semantics write that drops history.

WHAT THIS MODULE MAY IMPORT, AND WHY IT MATTERS
-----------------------------------------------
Not ``foundry_orchestrator`` — not at module top, not lazily. That module is
deleted in wave 2, this casting does not run again, and an orchestrator import
written here is an import nobody is able to rewrite.

And no lock of its own. Holmes ``share-1`` / ``coh-2`` record that this package
already carries TWO locked read-modify-write primitives over the same run
artifacts with two independent in-process locks; a third copy here would be the
same defect a third time. ``foundry._locked_document`` is the package's
declared whole-document write path — read, decide and write happen inside ONE
exclusive section, which is what the write-once door needs. A
``read_document`` + ``write_document`` pair would decide against a document a
concurrent caller may already have replaced, which is D-125 exactly.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from foundry_mcp.schemas.vocab import STREAM_WIRE_IDS
from foundry_mcp.tools.foundry import _locked_document, ledger_refusals
from foundry_mcp.tools.foundry_state import get_run_dir, read_document

# --------------------------------------------------------------------------- #
# Constants — declared FIRST so every message, every reader and every test
# derives from them rather than re-spelling a literal.
# --------------------------------------------------------------------------- #

#: The directory holding one document per stream, under the run directory.
ROSTERS_DIRNAME = "rosters"

#: The record container inside a roster document: the prior item lists, newest
#: last. Never replaced — see ``ROSTER_EXISTS``.
ROSTER_REVISIONS_KEY = "revisions"

#: Named refusal: a roster for this stream already exists and ``revise`` was
#: not set.
ROSTER_EXISTS = "ROSTER_EXISTS"

#: Named refusal: ``revise=True`` arrived without a reason.
ROSTER_REVISE_REASON_REQUIRED = "ROSTER_REVISE_REASON_REQUIRED"

#: Named refusal: the stream is not a member of the run's stream vocabulary.
ROSTER_UNKNOWN_STREAM = "ROSTER_UNKNOWN_STREAM"

#: Named refusal: ``items`` was not a list.
ROSTER_ITEMS_NOT_A_LIST = "ROSTER_ITEMS_NOT_A_LIST"

#: Derived from ``vocab.STREAM_WIRE_IDS``, never a second hand list — the
#: closed vocabulary lives in ``schemas/vocab.py`` and every consumer derives
#: from it, so adding a stream there cannot leave this hint behind.
ROSTER_STREAM_PHRASE = ", ".join(sorted(STREAM_WIRE_IDS))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _named_refusal(error: str, hint: str, phase: str) -> dict:
    """The house named refusal, shaped ONCE for every door in this module.

    ``{error, hint, phase}`` mirrors ``foundry_unregister_team``'s shape, where
    ``phase`` carries the NAME of the refusal rather than a run phase. A tool
    never raises across the MCP boundary — it returns this.
    """
    return {"error": error, "hint": hint, "phase": phase}


def roster_path(fdir: Path, stream: str) -> Path:
    """The one path a stream's roster lives at. Spelled ONCE.

    Callers that need the path (casting 3's migration fills ``rosters/`` among
    the schema-4 defaults; casting 4's ``foundry_init`` creates the directory)
    build it from here rather than re-joining the two components.
    """
    return fdir / ROSTERS_DIRNAME / f"{stream}.json"


def _roster_shape_problem(path: Path, data: dict) -> str | None:
    """Why ``data`` at ``path`` is not a roster, or None when it is one.

    A ROSTER IS AN ``items`` LIST. ``derived_at`` is provenance — the stamp
    THIS door happens to write — and fallout D-071 is what came of scoping
    both halves of AC-032 on it instead. This run's own
    ``rosters/research_audit.json`` carries ``{stream, items, total}``: 42 real
    items persisted by another hand, with no stamp. Reading the absent stamp as
    "no roster" made that document invisible to both doors at once — a second
    ``Foundry-Roster`` write replaced the 42 items with 1 and left
    ``revisions`` empty (GI-006's named violation, a replace-semantics write
    that drops history), and ``Foundry-Stream`` accepted ``items_total=7``
    against it at 100% coverage because ``ROSTER_MISMATCH`` had no length to
    compare with.

    So the predicate is the LIST, and the absence of a list is a NAMED problem
    rather than silence: "no roster" and "a document here I cannot read as one"
    send the operator to look at different things, and a reader that collapses
    them is the fail-open this function exists to close.
    """
    if not isinstance(data.get("items"), list):
        return (
            f"{path.name} exists but carries no `items` list — a roster is the "
            f"item list a stream agreed to check, and this document does not "
            f"hold one"
        )
    return None


def _existing_roster_facts(path: Path, data: dict) -> tuple[str, dict]:
    """NAME the document already at a roster's path, and HAND IT BACK.

    Returns the sentence ``ROSTER_EXISTS`` reads and the payload keys it
    carries beside ``{error, hint, phase}``.

    THE PAYLOAD IS THE POINT (fallout D-091). The refusal used to be the three
    named-refusal keys and nothing else, so a refused agent held neither the
    persisted items nor a machine-readable count, and the hint's first remedy
    sent it to "Foundry-Roster's reader" — an exit that does not exist on the
    boundary, since ``read_roster`` below is a Python function no MCP tool
    surfaces. The only actionable branch left was ``revise=True``, which
    REPLACES the roster with the agent's re-derived list: GI-020's own named
    violation, "an auditor that re-derives its item list when a roster exists".
    A refusal that carries the roster has no such gap — the list arrives in the
    same return value as the refusal, so the exit is reachable by construction.

    The sentence degrades honestly on a document this door did not write: an
    absent ``derived_at`` is described rather than interpolated, because
    "derived at None" is how the old wording read over exactly the shape
    fallout D-071 was filed on.
    """
    prior = data.get("items")
    stamp = data.get("derived_at")
    facts: dict = {
        "items": prior if isinstance(prior, list) else None,
        "items_total": len(prior) if isinstance(prior, list) else None,
        "derived_at": stamp,
        "path": str(path),
    }
    if not isinstance(prior, list):
        return "a document carrying no `items` list at all", facts
    if stamp:
        return f"{len(prior)} item(s) derived at {stamp}", facts
    return (
        f"{len(prior)} item(s) with no derivation stamp — it was written by "
        f"something other than this door"
    ), facts


# --------------------------------------------------------------------------- #
# The tool (CT-002 / FR-024 / ST-010 / AC-032 / OT-030)
# --------------------------------------------------------------------------- #


@ledger_refusals
def foundry_roster(
    stream: str = "",
    items: list | None = None,
    revise: bool = False,
    reason: str = "",
    project_root: str = ".",
) -> dict:
    """Persist a stream's derived item list, once (CT-002).

    Writes ``rosters/<stream>.json`` carrying ``{stream, items, derived_at,
    revisions}``. The FIRST write for a stream succeeds; a second is refused
    with ``ROSTER_EXISTS`` unless ``revise=True`` is passed with a reason, in
    which case the PRIOR items are appended to ``revisions`` before the new
    list takes their place.

    CALLERS: ``agents/research-auditor.md`` and ``agents/spec-test-deriver.md``
    at first derivation (FR-050, GI-020) — later cycles read the persisted
    roster through ``read_roster`` and derive only when it is absent.
    """
    if stream not in STREAM_WIRE_IDS:
        return _named_refusal(
            f"Unknown stream {str(stream)!r}: a roster belongs to a "
            f"verification stream this run recognises.",
            f"Accepted streams: {ROSTER_STREAM_PHRASE}.",
            ROSTER_UNKNOWN_STREAM,
        )

    if items is None:
        items = []
    if not isinstance(items, list):
        return _named_refusal(
            f"Roster items for {stream!r} must be a list, not "
            f"{type(items).__name__}.",
            "Call Foundry-Roster(stream=..., items=['RA-1: ...', 'RA-2: ...']) "
            "with one entry per item the stream will check.",
            ROSTER_ITEMS_NOT_A_LIST,
        )

    why = str(reason or "").strip()
    if revise and not why:
        return _named_refusal(
            f"Revising the {stream!r} roster needs a reason: the document "
            f"records WHY the item list changed, not merely that it did.",
            f"Call Foundry-Roster(stream={stream!r}, items=[...], revise=True, "
            f"reason='what changed in the source material and why').",
            ROSTER_REVISE_REASON_REQUIRED,
        )

    fdir = get_run_dir(project_root)
    if not fdir or not fdir.exists():
        return {"error": "No active foundry run"}

    path = roster_path(fdir, stream)
    refusal: dict | None = None
    with _locked_document(path) as data:
        # THE DOOR SCOPES ON THE DOCUMENT, NOT ON ONE OF ITS FIELDS (fallout
        # D-071). Anything already at this path is something a write would
        # destroy, whether or not this door is what wrote it — and
        # `foundry_init` and `migrate-archive.py` both create `rosters/` and
        # put NOTHING in it, precisely so an invented stub can never refuse a
        # real first derivation. So a non-empty document IS a roster for the
        # purposes of the write-once door, and `revise=True` with a reason
        # stays the one way past it, which also repairs the shape.
        existed = bool(data)
        if existed and not revise:
            described, facts = _existing_roster_facts(path, data)
            count = facts["items_total"]
            keeps = (
                f"the current {count} item(s)" if count is not None
                else "whatever the document holds"
            )
            refusal = {
                **_named_refusal(
                    f"A roster for {stream!r} already exists: {described}. It "
                    f"is written ONCE, at first derivation, and later cycles "
                    f"read it rather than re-deriving one.",
                    # Every exit named here is reachable FROM THIS REFUSAL:
                    # `items` and `path` are keys of this very return value,
                    # and `revise`/`reason` are declared properties of the
                    # Foundry-Roster input schema that `server.py#_DISPATCH`
                    # passes through. The exit this hint used to name first —
                    # "Foundry-Roster's reader" — was on no boundary at all.
                    f"The roster is IN this refusal: `items` carries the "
                    f"persisted list, `items_total` its length and `path` the "
                    f"document — read either rather than deriving a second "
                    f"list. If the item list genuinely CHANGED, call "
                    f"Foundry-Roster(stream={stream!r}, items=[...], "
                    f"revise=True, reason='why it changed'), which keeps "
                    f"{keeps} under {ROSTER_REVISIONS_KEY!r}.",
                    ROSTER_EXISTS,
                ),
                "stream": stream,
                **facts,
            }
        else:
            revisions = data.get(ROSTER_REVISIONS_KEY)
            if not isinstance(revisions, list):
                revisions = data[ROSTER_REVISIONS_KEY] = []
            if existed:
                # The PRIOR items, kept rather than replaced (GI-006). The
                # revision records what was superseded; `items` below records
                # what superseded it.
                revisions.append(
                    {
                        "at": _now(),
                        "reason": why,
                        "items": data.get("items") if isinstance(data.get("items"), list) else [],
                    }
                )
            data["stream"] = stream
            data["items"] = list(items)
            data["derived_at"] = _now()
            written = dict(data)

    if refusal is not None:
        return refusal
    return {
        "ok": True,
        "stream": stream,
        "items": written["items"],
        "items_total": len(written["items"]),
        "derived_at": written["derived_at"],
        "revisions": len(written[ROSTER_REVISIONS_KEY]),
        "revised": bool(revise and written[ROSTER_REVISIONS_KEY]),
        "path": str(path),
    }


# --------------------------------------------------------------------------- #
# The readers Foundry-Stream calls
# --------------------------------------------------------------------------- #


def read_roster(fdir: Path, stream: str) -> tuple[dict | None, str | None]:
    """The persisted roster document for ``stream``, or ``(None, problem)``.

    CALLERS: casting 2's ``Foundry-Stream`` (through ``roster_length``), and
    the stream agents that read a roster if present and derive only if absent
    (FR-050, GI-020).

    The ``(value, problem)`` pair the leaf module uses everywhere else. ``None``
    with no problem means NO ROSTER — which is not the same fact as a broken
    one, and a reader that collapsed the two would have Foundry-Stream refuse a
    perfectly good record because a document was unreadable, or accept any
    ``items_total`` at all because it was.

    THERE ARE THREE ANSWERS, NOT TWO (fallout D-071). This reader used to test
    for ``derived_at`` and answer "no roster" when it was absent, which is that
    same collapse spelled the other way round: a roster document written
    without this door's stamp read as ABSENT, so ``roster_length`` handed
    Foundry-Stream no length and every ``items_total`` cleared. The stamp is
    provenance; ``_roster_shape_problem`` holds the predicate that decides
    whether this is a roster, and an unreadable one is NAMED.
    """
    path = roster_path(fdir, stream)
    data, problem = read_document(path)
    if problem is not None:
        return None, problem
    if not data:
        return None, None
    problem = _roster_shape_problem(path, data)
    if problem is not None:
        return None, problem
    return data, None


def roster_length(fdir: Path, stream: str) -> tuple[int | None, str | None]:
    """How many items ``stream``'s persisted roster carries, or ``None``.

    CALLER: casting 2's ``Foundry-Stream``, whose ``ROSTER_MISMATCH`` refusal
    fires when a record's ``items_total`` differs from this length (CT-003,
    OT-031, AC-032 second half).

    ``None`` means NO ROSTER, distinguishably from ``0`` meaning a roster of
    zero items — Foundry-Stream must only refuse when a roster actually exists,
    so the two cannot be the same answer.
    """
    data, problem = read_roster(fdir, stream)
    if problem is not None:
        return None, problem
    if data is None:
        return None, None
    items = data.get("items")
    return (len(items) if isinstance(items, list) else 0), None
