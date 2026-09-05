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
        existed = "derived_at" in data
        if existed and not revise:
            prior = data.get("items")
            count = len(prior) if isinstance(prior, list) else 0
            refusal = _named_refusal(
                f"A roster for {stream!r} already exists: {count} item(s) "
                f"derived at {data.get('derived_at')}. It is written ONCE, at "
                f"first derivation, and later cycles read it rather than "
                f"re-deriving one.",
                f"Read it with Foundry-Roster's reader, or — if the item list "
                f"genuinely changed — call Foundry-Roster(stream={stream!r}, "
                f"items=[...], revise=True, reason='why it changed'), which "
                f"keeps the current {count} item(s) under "
                f"{ROSTER_REVISIONS_KEY!r}.",
                ROSTER_EXISTS,
            )
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
    """
    data, problem = read_document(roster_path(fdir, stream))
    if problem is not None:
        return None, problem
    if "derived_at" not in data:
        return None, None
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
