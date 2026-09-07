"""The concern ledger — FR-010 / FR-039 / CT-001 / ST-004 / AC-005 / OT-005.

A-010: "New ``Foundry-Concern`` tool writes a structured ledger; concerns.md
stays prose"

Three behaviours this module establishes, each of which the old shape got wrong
for the same underlying reason — *a hand-written artifact has no writer, so it
has no reader either*:

  1. ``concerns.md`` was 287 KB and 168 headings in daring-orca, written by
     agents per prose contract and read back only by other agents. Every code
     reference to it in the package is a COMMENT citing a recorded concern,
     never a read. There was no id, no cycle stamp, no status and no parser, so
     no gate could ask "is a concern from this GRIND still open?" — which is
     exactly what GI-023 / ST-005 need ``_inspect_start_preconditions`` to ask.
     ``concerns.json`` is that structured half; ``concerns.md`` becomes its
     rendered twin.
  2. A concern named a target in prose, so nothing checked that the target
     EXISTED. A teammate could file against a casting, a file or a symbol that
     the run's manifest has never heard of, and the misdirection was invisible
     until a lead read the paragraph. Target resolution is now a door
     (``CONCERN_TARGET_UNRESOLVED``), and its refusal names the casting ids and
     files the run actually knows, built from the manifest that was just read
     so it can never name a stale set.
  3. "Addressed" had no representation at all. A concern was open forever, or
     until someone edited the prose. The status is a closed vocabulary now
     (``vocab.CONCERN_STATUSES``) with one writer per move: ``Foundry-Concern``
     opens, ``mark_concerns_dispatched`` dispatches (CT-008 / FR-039), and
     ``Foundry-Concern(close=id, reason)`` closes with a handoff record
     (ST-004).

WHAT THIS MODULE MAY IMPORT, AND WHY IT MATTERS
-----------------------------------------------
Not ``foundry_orchestrator`` — not at module top, not lazily. That module is
deleted in wave 2, this casting does not run again, and an orchestrator import
written here is an import nobody is able to rewrite. The locked
read-modify-write and the id allocator come from ``tools.foundry``, the run-dir
and document reads from the leaf ``tools.foundry_state``, and the symbol-cite
grammar from ``tools.citation``.

A THIRD locked read-modify-write stack is likewise forbidden. Holmes ``share-1``
and ``coh-2`` record that this package already carries TWO primitives over the
same run artifacts with two independent in-process locks;
``foundry.ledger_transaction``'s own docstring states the ruling in the tree —
"This is the write discipline every ledger writer must use". So this module
writes through it and defines no lock of its own. For the same reason target
resolution reaches ``castings/manifest.json`` through ``foundry_state``'s
tolerant read rather than opening a fifth read-and-validate prelude of its own
(Holmes ``share-5``).

And the cross-casting READ is the leaf's, not this module's — concern C-030 and
the lead's ruling ``lead_ruling_gi_033_leaf_moves``. The INSPECT door refuses on
that list and ``orchestration/transitions.py`` is a VERIFIER module, so an edge
from there into this one pulled ``tools/foundry.py`` — the largest lifecycle
module in the tree — across the layer boundary with it (GI-033). The WRITERS
stay here with the transaction and the render; the filter is
``foundry_state.open_cross_casting_concerns`` and this module imports it, so
there is exactly one body of it (GI-024). See
``open_concerns_for_other_castings`` for the one thing left on this side.
"""

from __future__ import annotations

import json
from pathlib import Path

from foundry_mcp.schemas.vocab import (
    CONCERN_STATUS_CLOSED,
    CONCERN_STATUS_DISPATCHED,
    CONCERN_STATUS_OPEN,
    CONCERN_STATUSES,
)
from foundry_mcp.tools.citation import iter_symbol_cites
from foundry_mcp.tools.foundry import (
    _dict_records,
    allocate_record_id,
    ledger_refusals,
    ledger_transaction,
)
from foundry_mcp.tools.foundry_state import (
    get_run_dir,
    now_iso,
    open_cross_casting_concerns,
    read_document,
    read_text_file,
)
# fallout FR-009 (D-170, casting 7's concern C-080) — WHAT A ``key_files`` ENTRY
# IS, READ FROM THE ONE MODULE THAT SAYS IT.
#
# C-080 asked whether this module could depend on an existing statement of the
# rule or had to write its own, and named the hazard: four spellings already
# exist and adding a fifth closes the concern while growing the problem. The
# answer is the leaf. ``orchestration/keyfiles.py`` imports NOTHING, which is
# the strongest form of the property ``_LEAF_MODULES`` is checked on, and a leaf
# is the layer BOTH sides of GI-033 may reach — the package-wide
# single-definition guard states the outcome in its own words: "an IMPORT is not
# a definition: a module that imports a name is reaching the one definition,
# which is the outcome this guard exists to produce rather than to forbid".
# ``foundry_spawn.py`` reaches it the same way, at module top, from outside the
# orchestration package.
#
# ``orchestration/__init__.py`` re-exports nothing and ``keyfiles.py`` imports
# nothing, so this edge reaches no module that can reach back: there is no cycle
# for a lazy seam to defer, and deferring it would be a seam written for a
# hazard that is not there.
from foundry_mcp.tools.orchestration.keyfiles import (
    DIRECTORY_ENTRY_SUFFIX,
    covers_path,
)
# fallout D-167 (casting 2's concern C-083) — THE REFUSAL SHAPE, BOUND RATHER
# THAN RE-STATED. `rosters.py#_named_refusal` holds the one implementation and
# its docstring carries the reasoning for the direction; the binding below is
# this module's spelling of it. The edge adds nothing: every module `rosters.py`
# reaches, this one already reached.
from foundry_mcp.tools.rosters import _named_refusal as _roster_named_refusal

# --------------------------------------------------------------------------- #
# Constants — declared FIRST so every message, every reader and every test
# derives from them rather than re-spelling a literal.
# --------------------------------------------------------------------------- #

#: The structured ledger. The store of record; ``concerns.md`` is its rendering.
CONCERNS_FILENAME = "concerns.json"

#: The rendered twin. GENERATED from the ledger on every write — see
#: ``render_concerns_markdown`` for what happens to prose already in the file.
CONCERNS_MARKDOWN_FILENAME = "concerns.md"

#: The record container inside ``concerns.json``, and the key
#: ``ledger_transaction`` projects onto.
CONCERNS_COLLECTION_KEY = "concerns"

#: The ``{prefix}-NNN`` family for a concern id. ``allocate_record_id`` takes
#: max(suffix)+1 INSIDE the lock, so uniqueness comes from the transaction.
CONCERN_ID_PREFIX = "C"

# THE CLOSED STATUS VOCABULARY IS IMPORTED, NOT DECLARED HERE — concern C-033
# and the same GI-033 layering the reader above moved for. It was declared in
# this module, and this module reaches ``tools/foundry.py`` at module top for
# the ledger apparatus, so ``orchestration/transitions.py`` — a VERIFIER module
# — could reach the leaf's READ for GI-023's CONCERN_OPEN rung and still not
# reach the MEMBER that read takes as an argument. A closed vocabulary is
# ``schemas/vocab.py``'s by the house convention (top convention 3), so
# ``CONCERN_STATUSES`` and its three members are declared there and this module
# imports them: one declaration, reachable from every layer (GI-024).
#
# WHAT STAYED HERE IS THE LEDGER, which is the division the whole ruling draws:
# the transaction, the id allocation, the markdown render, and one writer per
# status move — ``foundry_concern`` opens, ``mark_concerns_dispatched``
# dispatches, ``foundry_concern(close=...)`` closes.

#: Derived from ``vocab.CONCERN_STATUSES``, never re-typed beside it — the
#: ``_PYTEST_DISCOVERY_PHRASE`` rule: prose that names a set is built from the
#: constant that DEFINES the set, so the two cannot drift. The PHRASE stays in
#: this module rather than following the set to ``vocab``: it is the sentence
#: ``concerns.md`` opens with, so it belongs beside the renderer that writes it.
CONCERN_STATUS_PHRASE = (
    ", ".join(CONCERN_STATUSES[:-1]) + f" or {CONCERN_STATUSES[-1]}"
)

#: Named refusal: the target named no casting id, no key file and no cited
#: symbol in the run's casting manifest.
CONCERN_TARGET_UNRESOLVED = "CONCERN_TARGET_UNRESOLVED"

#: Named refusal: the concern text was empty or whitespace.
CONCERN_TEXT_EMPTY = "CONCERN_TEXT_EMPTY"

#: Named refusal: ``cycle`` was missing, or was not a whole non-negative
#: number. The field is REQUIRED on the write arm and nothing is coerced —
#: see ``_cycle_refusal`` for what the coercion cost.
CONCERN_CYCLE_REQUIRED = "CONCERN_CYCLE_REQUIRED"

#: Named refusal: ``close=id`` named an id the ledger does not carry.
CONCERN_UNKNOWN_ID = "CONCERN_UNKNOWN_ID"

#: Named refusal: ``close=id`` arrived without a reason.
CONCERN_CLOSE_REASON_REQUIRED = "CONCERN_CLOSE_REASON_REQUIRED"

#: The three kinds of thing a concern may target, per CT-001.
TARGET_KIND_CASTING = "casting"
TARGET_KIND_FILE = "file"
TARGET_KIND_SYMBOL = "symbol"

#: The sentence the unresolved-target hint adds WHEN THE MANIFEST ACTUALLY HAS
#: a directory entry (fallout FR-009, the second half of C-080's ask).
#:
#: Without it the hint lists ``.../tools/orchestration/`` among "the key files"
#: and says nothing else, so a filer who typed ``streams.py`` reads a list of
#: paths, sees nothing resembling what they meant, and goes looking for a file
#: path the manifest will never contain — because a covered file appears in
#: nobody's ``key_files`` list literally.
#:
#: Built from ``DIRECTORY_ENTRY_SUFFIX`` rather than re-typing the character —
#: the ``_PYTEST_DISCOVERY_PHRASE`` rule: prose that names a rule is derived
#: from the constant that DEFINES it, so the sentence and the reading cannot
#: come to say two things. Emitted only when there IS such an entry: a hint
#: describes the run it was built from, not the format in general.
_DIRECTORY_ENTRY_MEANING = (
    "a key file ending in "
    f"'{DIRECTORY_ENTRY_SUFFIX}' names a DIRECTORY and stands for every path "
    "beneath it, so name such a path IN FULL rather than by its basename"
)

#: The handoff event a close appends (ST-004: "a handoff record is appended").
HANDOFF_EVENT_CONCERN_CLOSED = "concern_closed"

#: The generated file's first line, and the ONE thing that identifies it as
#: generated. `_carried_prose` tests for this rather than for the whole
#: preamble below it: the preamble is prose that will be reworded, and a
#: reworded preamble must not silently reclassify every file this module ever
#: wrote as hand-written. No `concerns.md` written by hand in this repo's
#: archives opens with it — they all open `# Concerns — <run>`.
_CONCERNS_HEADING = "# Foundry Concerns\n"

_CONCERNS_PREAMBLE = (
    _CONCERNS_HEADING
    + "\n"
    f"Generated from `{CONCERNS_FILENAME}` on every `Foundry-Concern` write.\n"
    "The JSON ledger is the store of record; this file is its rendering, so an\n"
    "edit made here is never read back into the ledger. The generated region\n"
    "ends at the marker line at the foot of this file, and everything BELOW\n"
    "that line is preserved verbatim by every later render — so that is where\n"
    "prose goes, whether it predates the generated region or is written after\n"
    "it.\n"
    "\n"
    f"Statuses: {CONCERN_STATUS_PHRASE}.\n"
    "\n"
)

#: Everything after this marker is carried forward untouched by every later
#: render. GI-006 — a ledger writer never drops history, and `concerns.md` held
#: hand-written prose that two agent contracts (`agents/research-auditor.md`,
#: `agents/assayer.md`) read back to flip IGNORED -> HONORED_WITH_OVERRIDE.
#: Replacing that with a generated file would be a replace-semantics write that
#: drops history, which is the violation GI-006 names.
#:
#: The sentence says what the render DOES, which is not what it used to say.
#: "prose below this line predates the generated region" described the only
#: prose the old render could ever carry, because it wrote the marker only when
#: a tail already existed — and that is fallout D-073: prose appended to an
#: already-generated file postdates the region, carried no marker, and was
#: destroyed.
CARRIED_PROSE_MARKER = (
    "<!-- concerns.md: the generated region ends here. Everything below this "
    "line is preserved verbatim by every later render — write prose below it. -->"
)

#: Spellings this module used for the marker before the current one. Scanned so
#: that a file already carrying an older marker keeps the prose under it: that
#: prose is the only copy, and GI-006 forbids the write that would drop it.
_LEGACY_PROSE_MARKERS = (
    "<!-- concerns.md: prose below this line predates the generated region "
    "and is preserved verbatim -->",
)


def _now() -> str:
    """The ledger stamps — from the leaf, at full precision.

    fallout GI-024 / D-124: this was one of THREE ``_now`` definitions and one
    of the two still spelling ``datetime.now(timezone.utc).isoformat()``
    inline, beside `foundry_report._now`'s docstring asserting there had only
    ever been two. GI-024's violation column is "a second derivation outside
    `foundry_state`", and this module stamps `recorded_at`, `closed_at` and
    the handoff record — values a reader compares against the leaf's stamps —
    so the derivation being a second one is the whole of the cost. `now_iso`
    holds the one implementation and its default precision is the one these
    stamps have always published, so this is a call-through binding and
    nothing else, which is what `_DELIBERATE_REDEFINITIONS["_now"]` says every
    module binding the name is.
    """
    return now_iso()


def _named_refusal(error: str, hint: str, phase: str) -> dict:
    """This module's spelling of the house named refusal — A BINDING, NOT A BODY.

    ``{error, hint, phase}``, where ``phase`` carries the NAME of the refusal
    rather than a run phase. A tool never raises across the MCP boundary — it
    returns this. The implementation is ``rosters.py#_named_refusal`` and its
    docstring carries why it lives there; this name exists so the seven doors
    below read the same as they always did.

    fallout D-167 (casting 2's concern C-083) — WHY A BINDING AND NOT AN IMPORT.
    This was a byte-identical SECOND BODY, and the fix for that is one
    implementation. A bare re-export would have been the tidier spelling and it
    is not available: ``test_the_named_refusal_forks_are_the_two_the_row_names_
    and_not_the_third`` asserts by AST that this module defines a function of
    this name taking these three arguments, casting 2 owns that file and has
    finished, so deleting the definition would leave a red tree with nobody able
    to green it. The binding satisfies both — one implementation, and the
    signature the pin reads is still here — which is the ``_now`` arrangement
    the guard already names as closed: "one implementation and three bindings".
    ``test_concerns.py#test_the_named_refusal_has_one_implementation_however_
    it_is_spelled`` is the pin C-083 says was missing, and it is what makes this
    a delegation rather than a fork wearing an exemption.
    """
    return _roster_named_refusal(error, hint, phase)


# --------------------------------------------------------------------------- #
# Target resolution (CT-001 error column, AC-005 second half)
# --------------------------------------------------------------------------- #


def _normalise_path(value: str) -> str:
    """A path as the manifest and a human would each spell it."""
    return value.strip().replace("\\", "/").removeprefix("./")


def _manifest_castings(fdir: Path) -> tuple[list[dict], str | None]:
    """The run's castings, read through the leaf. Never a new prelude.

    Holmes ``share-5`` records that ``castings/manifest.json`` is already opened
    through four different read-and-validate preludes. This is not a fifth: the
    read is ``foundry_state.read_document``'s (tolerant, total, names the file
    when it is broken) and the only shape question asked here is the one this
    module actually needs — "is ``castings`` a list of mappings".
    """
    path = fdir / "castings" / "manifest.json"
    data, problem = read_document(path)
    if problem is not None:
        return [], problem
    castings = data.get("castings")
    if not isinstance(castings, list):
        return [], None
    return [c for c in castings if isinstance(c, dict)], None


def _casting_symbols(casting: dict) -> set[str]:
    """Every ``path#Symbol`` cite this casting carries, as symbol and as cite.

    The casting is serialised and handed to the package's ONE symbol-cite
    grammar rather than walked field by field: cites live in ``spec_text``,
    ``must_haves``, ``observable_truths``, ``pattern_refs`` and
    ``research_context`` alike, and a field-by-field walk is a second opinion
    about which fields may carry one.
    """
    text = json.dumps(casting, ensure_ascii=False)
    found: set[str] = set()
    for cite in iter_symbol_cites(text):
        found.add(cite["symbol"])
        found.add(cite["cite"])
    return found


def resolve_target(castings: list[dict], target: str) -> dict | None:
    """Resolve ``target`` to one casting, or None.

    A target resolves when it names a casting id, a ``key_files`` entry, or a
    symbol cited in the manifest's castings — the three kinds CT-001 lists.
    Returns ``{"kind", "casting_id", "matched"}``; ``kind`` is one of
    ``TARGET_KIND_CASTING`` / ``TARGET_KIND_FILE`` / ``TARGET_KIND_SYMBOL``.

    The resolution is STORED on the record rather than recomputed by each
    reader, so ``open_cross_casting_concerns`` answers "does this target
    another casting?" from the manifest as it stood when the concern was filed.
    """
    wanted = target.strip()
    if not wanted:
        return None

    for casting in castings:
        cid = casting.get("id")
        if cid is None:
            continue
        if wanted in {str(cid), f"casting-{cid}", f"casting {cid}"}:
            return {
                "kind": TARGET_KIND_CASTING,
                "casting_id": cid,
                "matched": str(cid),
            }

    wanted_path = _normalise_path(wanted)
    for casting in castings:
        for key_file in casting.get("key_files") or []:
            if not isinstance(key_file, str):
                continue
            normalised = _normalise_path(key_file)
            # Exact, or a tail on a SEGMENT boundary: a concern naming
            # `concerns.py` must reach `.../tools/concerns.py`, while one
            # naming `erns.py` must not.
            if normalised == wanted_path or normalised.endswith("/" + wanted_path):
                return {
                    "kind": TARGET_KIND_FILE,
                    "casting_id": casting.get("id"),
                    "matched": key_file,
                }

    # fallout FR-009 (D-170, casting 7's concern C-080) — AND THEN DOWNWARD.
    #
    # The two readings above go UP a path and sideways; neither goes down into
    # a directory entry. Casting 2's `key_files` are `.../tools/orchestration/`
    # and `tests/orchestration/`, so a concern naming any of the thirteen
    # modules beneath them resolved to nothing and was refused — the refusal a
    # stream or teammate hits at precisely the moment it has found something
    # inside that package. C-079 had to be filed against the directory STRING
    # for that reason: naming the module it actually meant would have been
    # refused, which is this door failing exactly when it is most needed.
    #
    # A SECOND PASS RATHER THAN A WIDER FIRST ONE, so the fix cannot change any
    # answer that already existed. The pass above returns on its first hit, so
    # folding coverage into it would let an earlier casting's directory entry
    # beat a later casting's exact entry — an over-match, which is the worse
    # direction for a resolver to be wrong in: it attaches a concern to a
    # casting that does not own it, silently. Reaching this line at all means
    # no entry equalled or tailed the target, so only a directory entry can
    # match here.
    #
    # `matched` carries the ENTRY, not the target: the covered path appears in
    # nobody's `key_files` list literally, so a record naming only the path
    # sends a lead looking for a manifest line that is not there.
    #
    # THE READING IS THE LEAF'S, BY IMPORT RATHER THAN BY AGREEMENT. C-080 asked
    # for the judgement and the import block above records it: a fourth spelling
    # pinned by a behaviour test would close this concern and grow the problem
    # the concern is about, and the leaf that ends the problem is committed and
    # reachable from this layer.
    for casting in castings:
        for key_file in casting.get("key_files") or []:
            if not isinstance(key_file, str):
                continue
            if covers_path(key_file, wanted_path):
                return {
                    "kind": TARGET_KIND_FILE,
                    "casting_id": casting.get("id"),
                    "matched": key_file,
                }

    for casting in castings:
        if wanted in _casting_symbols(casting):
            return {
                "kind": TARGET_KIND_SYMBOL,
                "casting_id": casting.get("id"),
                "matched": wanted,
            }
    return None


def _known_targets(castings: list[dict]) -> tuple[list[str], list[str], str]:
    """The casting ids, key files and directory note this run knows, for the hint.

    The third element is the other half of C-080 (fallout FR-009). Listing
    ``.../tools/orchestration/`` among "the key files" and saying nothing else
    tells a filer who typed a module name that their target is absent, when
    what is true is that it is COVERED under a spelling the list does not
    explain. The note says what a trailing mark means, and it is "" for a
    manifest that has no directory entry — a hint must describe the run it was
    built from, not the format in general.
    """
    ids = [str(c.get("id")) for c in castings if c.get("id") is not None]
    files: list[str] = []
    for casting in castings:
        for key_file in casting.get("key_files") or []:
            if isinstance(key_file, str) and key_file not in files:
                files.append(key_file)
    has_directory = any(
        _normalise_path(f).endswith(DIRECTORY_ENTRY_SUFFIX) for f in files
    )
    return ids, files, (_DIRECTORY_ENTRY_MEANING if has_directory else "")


# --------------------------------------------------------------------------- #
# The rendered twin (FR-039 / OT-005 / AC-005)
# --------------------------------------------------------------------------- #


def _render_entry(entry: dict) -> str:
    heading = f"## {entry.get('id', '?')} — {entry.get('status', '?')}"
    lines = [heading, ""]
    lines.append(
        f"- **Cycle:** {entry.get('cycle')}  "
        f"**From:** casting {entry.get('source_casting')}  "
        f"**Target:** `{entry.get('target')}` "
        f"({entry.get('target_kind')}, casting {entry.get('target_casting_id')})"
    )
    lines.append(f"- **Recorded:** {entry.get('recorded_at')}")
    if entry.get("dispatched_at"):
        lines.append(f"- **Dispatched:** {entry['dispatched_at']}")
    if entry.get("status") == CONCERN_STATUS_CLOSED:
        lines.append(
            f"- **Closed:** {entry.get('closed_at')} — {entry.get('close_reason')}"
        )
    lines.append("")
    lines.append(str(entry.get("text", "")).strip())
    lines.append("")
    return "\n".join(lines)


def _carried_prose(existing: str) -> str:
    """The part of an existing ``concerns.md`` a render must NOT overwrite.

    THE MARKER IS THE ONLY DELIMITER, and ``render_concerns_markdown`` writes
    it on EVERY render (fallout D-073). It used to be written only when a tail
    already existed, so a file this module generated with no prose in it had
    nowhere to put any: prose appended below the last entry carried no marker,
    fell into the "generated by an earlier render" branch below, and was
    destroyed on the next write — while the preamble the same module emits
    promised the reader it would be preserved. A delimiter that appears only
    once there is already something to delimit delimits nothing.

    The heading rather than the whole preamble decides "generated", so that
    rewording the preamble cannot reclassify a generated file as hand-written
    and carry its own body forward as prose. Reaching that branch at all means
    a file generated BEFORE the marker became unconditional, and every such
    file that held prose held the marker with it.
    """
    if not existing.strip():
        return ""
    for marker in (CARRIED_PROSE_MARKER, *_LEGACY_PROSE_MARKERS):
        found = existing.find(marker)
        if found != -1:
            return existing[found + len(marker):]
    if existing.startswith(_CONCERNS_HEADING):
        # Generated by an earlier render and carrying no prose tail.
        return ""
    # Written by hand before this module existed. Carried whole.
    return existing


def render_concerns_markdown(fdir: Path, records: list[dict]) -> str | None:
    """Regenerate ``concerns.md`` from ``records``. Returns a problem, or None.

    Reaches elements through ``_dict_records`` — the ONE place this package
    names the malformed-record tolerance (D-128). Every other scan assumed
    dicts, and a hand-applied ``isinstance`` at each site is the copy-per-site
    that IS that defect class.

    TOTAL BY CONTRACT. Called from inside the ledger transaction so the two
    documents cannot disagree about a concern's status — an append-only mirror
    could only ADD a line saying C-001 closed, leaving the earlier line saying
    it was open — but a rendering fault must never cost the ledger entry, so
    every failure becomes a returned string and none becomes a raise.

    Refuses rather than overwrites when the existing file cannot be read as
    UTF-8 text: the prose it holds may be the only copy, and writing over bytes
    we could not read is the data loss GI-006 names.
    """
    path = fdir / CONCERNS_MARKDOWN_FILENAME
    try:
        existing, problem = read_text_file(path)
        if problem is not None:
            return problem
        tail = _carried_prose(existing)
        body = [_CONCERNS_PREAMBLE]
        body.extend(_render_entry(entry) for entry in _dict_records(records))
        # UNCONDITIONAL (fallout D-073). The marker is where the generated
        # region ENDS, and a file only has somewhere safe to write prose if the
        # end is marked BEFORE there is prose to keep. Emitting it only when a
        # tail already existed is what made every freshly generated file a
        # place where prose silently died.
        body.append(CARRIED_PROSE_MARKER)
        if tail.strip():
            body.append(tail.rstrip() + "\n")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(body).rstrip() + "\n", encoding="utf-8")
    except OSError as exc:
        return f"{CONCERNS_MARKDOWN_FILENAME} could not be rendered ({exc})"
    return None


# --------------------------------------------------------------------------- #
# The tool (CT-001 / FR-010 / FR-039 / ST-004)
# --------------------------------------------------------------------------- #


@ledger_refusals
def foundry_concern(
    casting_id: str | int = "",
    cycle: int | None = None,
    target: str = "",
    text: str = "",
    close: str = "",
    reason: str = "",
    project_root: str = ".",
) -> dict:
    """Record a cross-casting concern, or close one (CT-001).

    Two arms, one door. ``close=id`` with a ``reason`` takes the close arm
    (ST-004); everything else takes the write arm, which needs
    ``casting_id``, ``cycle``, ``target`` and ``text``.

    ``cycle`` is the caller's declaration, exactly as CT-001 lists it, and on
    the write arm it is REQUIRED. This module does NOT derive one:
    ``state.json['cycle']`` already has two readers held equal by cross-door
    pins, and a third derivation here is the shape GI-024 names — and
    ``foundry_state.current_cycle`` answers 0 for a malformed state file, so
    deriving would reintroduce the silent zero by a longer route.

    THE DEFAULT IS A SENTINEL, NOT A CYCLE (fallout D-060 / AC-004). ``None``
    means "not passed" and is refused by ``_cycle_refusal``; ``0`` is a real
    cycle a concern raised during CAST carries, and the two must stay
    distinguishable at this door because nothing downstream can tell them
    apart once one is written. Callers reaching this over MCP must pass
    absence THROUGH as absence rather than substituting a default of their
    own, which is what ``server.py``'s dispatch entry does.

    The close arm takes no ``cycle`` and never asks for one: it moves a
    record that already carries its stamp.
    """
    fdir = get_run_dir(project_root)
    if not fdir or not fdir.exists():
        return {"error": "No active foundry run"}

    if close:
        return _close_concern(fdir, str(close).strip(), reason)
    return _open_concern(fdir, casting_id, cycle, target, text)


def _cycle_refusal(cycle: object) -> dict | None:
    """Refuse a ``cycle`` that is missing, or is not a whole non-negative number.

    THE FIELD IS REQUIRED, WITH NO DEFAULT AND NO COERCION (fallout D-060 /
    AC-004). The write arm used to declare ``cycle: int = 0`` and collapse
    every bool, non-int and negative onto ``0``, and that is the whole of the
    defect: a caller who omitted ``cycle`` — or passed the JSON string ``"4"``
    a transport handed through unconverted — filed a concern stamped cycle 0
    and was told the call succeeded.

    WHAT THE SILENT ZERO COSTS, because it is not a cosmetic field.
    ``foundry_state.open_cross_casting_concerns`` scopes by EXACT cycle
    equality, and it is right to: GI-023 / ST-005 ask for the concerns "from
    the closing GRIND", and no other reading of that phrase exists. So a
    concern stamped 0 while the run is at cycle 4 is invisible to the
    ``inspect_start`` CONCERN_OPEN rung that exists to refuse on it, and
    INSPECT opens over exactly the tree the concern was filed to hold shut.
    The reader was never the broken half; the WRITER was, which is why the
    fix is a refusal here rather than a widened filter there. A silently
    defaulted scope field is worse than a refused call, because the refusal
    is visible and the default is not.

    ``0`` IS A LEGAL CYCLE — a concern raised during CAST carries it — so
    this check is on the SHAPE of the value and never on whether it is zero.
    Telling an omitted argument from an explicit ``0`` is precisely what the
    old default destroyed, and it is why the sentinel is ``None``.

    ``bool`` is excluded before ``int`` is tested, for
    ``foundry_state.current_cycle``'s reason: ``True`` is not cycle 1, and
    ``isinstance(True, int)`` is ``True``.

    Returns the refusal, or ``None`` when the value may be stored as it is.
    One token for all four shapes, on the ``CONCERN_TEXT_EMPTY`` precedent:
    the remedy is the same sentence in every case, and it is the MESSAGE
    that names which of the four arrived.
    """
    if cycle is None:
        what = "was not passed at all"
    elif isinstance(cycle, bool):
        what = f"arrived as the boolean {cycle!r}"
    elif not isinstance(cycle, int):
        what = f"arrived as {type(cycle).__name__} {cycle!r}, not an integer"
    elif cycle < 0:
        what = f"arrived as {cycle}, which is before the run's first cycle"
    else:
        return None
    return _named_refusal(
        f"A concern is stamped with the cycle it was raised in, and this "
        f"one {what}. The stamp is never defaulted or coerced: the "
        f"inspect_start rung scopes its refusal on exact cycle equality, so "
        f"a guessed 0 would file the concern where no door is looking.",
        "Call Foundry-Concern(casting_id=..., cycle=<this cycle, a "
        "non-negative integer>, target=..., text=...). Foundry-Context "
        "reports the run's current cycle.",
        CONCERN_CYCLE_REQUIRED,
    )


def _open_concern(
    fdir: Path,
    casting_id: str | int,
    cycle: int | None,
    target: str,
    text: str,
) -> dict:
    """The write arm: one appended entry, one rendered entry (AC-005/OT-005)."""
    body = str(text or "").strip()
    if not body:
        return _named_refusal(
            "A concern needs text: the empty string records nothing a lead "
            "could act on.",
            "Call Foundry-Concern(casting_id=..., cycle=..., target=..., "
            "text='what you found and why it belongs to another casting').",
            CONCERN_TEXT_EMPTY,
        )

    # Both pure input-shape rungs before the first read: a call that can
    # never succeed does not earn a manifest read, and the cheaper, more
    # local refusal is the one an operator should see.
    cycle_refused = _cycle_refusal(cycle)
    if cycle_refused is not None:
        return cycle_refused

    castings, problem = _manifest_castings(fdir)
    if problem is not None:
        return _named_refusal(
            f"The casting manifest could not be read, so no target can be "
            f"resolved against it: {problem}",
            "Repair or delete castings/manifest.json, then retry. A corrupt "
            "run artifact is never silently overwritten, nor guessed at.",
            CONCERN_TARGET_UNRESOLVED,
        )

    resolution = resolve_target(castings, str(target or ""))
    if resolution is None:
        ids, files, directory_note = _known_targets(castings)
        return _named_refusal(
            f"Concern target {str(target)!r} names no casting id, no key file "
            f"and no cited symbol in this run's casting manifest.",
            "Name one of the casting ids "
            + (", ".join(ids) if ids else "(none — run F0.5 DECOMPOSE first)")
            + "; or one of the key files "
            + (", ".join(files) if files else "(none)")
            + "; or a `path#Symbol` cited by a casting."
            + (f" Note that {directory_note}." if directory_note else ""),
            CONCERN_TARGET_UNRESOLVED,
        )

    path = fdir / CONCERNS_FILENAME
    with ledger_transaction(path, CONCERNS_COLLECTION_KEY) as records:
        entry = {
            "id": allocate_record_id(records, CONCERN_ID_PREFIX),
            "cycle": cycle,
            "source_casting": casting_id,
            "target": str(target).strip(),
            "target_kind": resolution["kind"],
            "target_casting_id": resolution["casting_id"],
            "target_matched": resolution["matched"],
            "text": body,
            "status": CONCERN_STATUS_OPEN,
            "recorded_at": _now(),
        }
        records.append(entry)
        render_problem = render_concerns_markdown(fdir, records)

    result = {
        "ok": True,
        "concern": entry,
        "concern_id": entry["id"],
        "status": CONCERN_STATUS_OPEN,
        "rendered": CONCERNS_MARKDOWN_FILENAME,
    }
    if render_problem is not None:
        result["render_problem"] = render_problem
    return result


def _close_concern(fdir: Path, concern_id: str, reason: str) -> dict:
    """The close arm (ST-004): status closed, handoff appended, md restated."""
    why = str(reason or "").strip()
    if not why:
        return _named_refusal(
            f"Closing {concern_id} needs a reason: the ledger records WHY a "
            f"concern stopped being open, not merely that it did.",
            f"Call Foundry-Concern(close={concern_id!r}, reason='what was "
            f"done, or why no action is needed').",
            CONCERN_CLOSE_REASON_REQUIRED,
        )

    path = fdir / CONCERNS_FILENAME
    closed: dict | None = None
    with ledger_transaction(path, CONCERNS_COLLECTION_KEY) as records:
        # `_dict_records` is the ONE place this package names the
        # malformed-record tolerance (D-128); a scan that iterates the binding
        # directly has bypassed it however carefully its body is written.
        entries = _dict_records(records)
        known = [str(r.get("id")) for r in entries if r.get("id") is not None]
        for record in entries:
            if str(record.get("id")) == concern_id:
                closed = record
                break
        if closed is not None:
            closed["status"] = CONCERN_STATUS_CLOSED
            closed["close_reason"] = why
            closed["closed_at"] = _now()
            render_problem = render_concerns_markdown(fdir, records)

    if closed is None:
        return _named_refusal(
            f"No concern {concern_id!r} in {CONCERNS_FILENAME}.",
            "Recorded ids: "
            + (", ".join(known) if known else "(none — nothing has been filed)")
            + ".",
            CONCERN_UNKNOWN_ID,
        )

    handoff_problem = _append_close_handoff(fdir, closed)

    result = {
        "ok": True,
        "concern": closed,
        "concern_id": closed["id"],
        "status": CONCERN_STATUS_CLOSED,
        "rendered": CONCERNS_MARKDOWN_FILENAME,
    }
    if render_problem is not None:
        result["render_problem"] = render_problem
    # fallout FR-039 / ST-004 (D-145 / D-146) — THE SECOND GUARD ARTEFACT
    # SPEAKS ON THE SAME CHANNEL AS THE FIRST. ST-004's guard column names two
    # conditions for the close, "reason non-empty; a handoff record is
    # appended", and the render one line up already reports its own failure as
    # `render_problem`. The handoff outcome was DISCARDED at this call site and
    # swallowed inside the appender, so a close whose handoff record was never
    # written returned `ok: True` with nothing on it to say so — the operator
    # reading the result had no way to learn that the record ST-004 requires
    # does not exist. Letting the close proceed is not the defect; the entry is
    # already closed in the ledger by the time we get here, and reporting a
    # committed status move as a refusal would be the worse lie. The asymmetry
    # was the defect, and this is the symmetry.
    if handoff_problem is not None:
        result["handoff_problem"] = handoff_problem
    return result


def _append_close_handoff(fdir: Path, entry: dict) -> str | None:
    """Append the ST-004 handoff record. Returns a problem, or None.

    IMPORTED LAZILY, DELIBERATELY. ``foundry_handoff`` imports
    ``foundry_orchestrator`` at module top today, so a module-top import of it
    here would give this module a transitive edge into the module wave 2
    deletes — and would close a cycle the moment casting 2's ``transitions.py``
    imports this one for the ``inspect_start`` CONCERN_OPEN rung. A
    function-local import creates no module-top edge either way. (The clean fix
    is hoisting ``_artifact_guard``/``_load_json`` into a leaf, which is Holmes
    reshaping #4 and casting 2's to make; recorded as a concern rather than
    reached for here.)

    TOTAL, AND AUDIBLE (fallout D-145 / D-146). A handoff channel that cannot
    be written must not cost the close — the ledger entry is already closed
    inside a committed transaction by the time this runs. But "must not cost
    the close" is not "must not be reported": the returned string is the
    `render_concerns_markdown` contract exactly, `str | None`, and
    `_close_concern` surfaces it as `handoff_problem` beside that function's
    `render_problem`. The two failures were shaped differently for no reason —
    one named, one silent — and it was the silent one that ST-004 names as a
    guard.

    The two exception kinds mean different things and say so. `ImportError` is
    this module's lazy edge into `foundry_handoff` failing, which is a broken
    package rather than a broken run artifact; `OSError` is the channel itself
    — a `handoffs.jsonl` that is a directory, a read-only archive, a full
    disk. Sending an operator to look at the wrong one costs the same as
    telling them nothing.
    """
    try:
        from foundry_mcp.tools.foundry_handoff import _append_handoff_record

        _append_handoff_record(
            fdir,
            {
                "timestamp": entry.get("closed_at") or _now(),
                "event": HANDOFF_EVENT_CONCERN_CLOSED,
                "concern_id": entry.get("id"),
                "cycle": entry.get("cycle"),
                "source_casting": entry.get("source_casting"),
                "target": entry.get("target"),
                "target_casting_id": entry.get("target_casting_id"),
                "reason": entry.get("close_reason"),
            },
            [
                ("Concern", str(entry.get("id"))),
                ("Target", str(entry.get("target"))),
                ("Reason", str(entry.get("close_reason"))),
            ],
        )
    except ImportError as exc:
        return (
            f"the {HANDOFF_EVENT_CONCERN_CLOSED} handoff record could not be "
            f"appended: foundry_handoff could not be imported ({exc})"
        )
    except OSError as exc:
        return (
            f"the {HANDOFF_EVENT_CONCERN_CLOSED} handoff record could not be "
            f"appended to handoffs.jsonl/handoffs.md ({exc})"
        )
    return None


# --------------------------------------------------------------------------- #
# The readers later castings call
# --------------------------------------------------------------------------- #


def read_concerns(fdir: Path) -> tuple[list[dict], str | None]:
    """Every concern record, plus the named problem when the ledger is broken.

    The ``(value, problem)`` pair the leaf module uses everywhere else: a
    caller that must TELL the operator which file is broken reads ``problem``,
    because an empty list cannot distinguish "nothing filed" from "corrupt".
    """
    data, problem = read_document(fdir / CONCERNS_FILENAME)
    if problem is not None:
        return [], problem
    records = data.get(CONCERNS_COLLECTION_KEY)
    if not isinstance(records, list):
        return [], None
    return [r for r in records if isinstance(r, dict)], None


def open_concerns_for_other_castings(
    fdir: Path,
    *,
    cycle: int | None = None,
) -> list[dict]:
    """Open concerns whose target is a casting OTHER than the source casting.

    CALLERS: ``orchestration/transitions.py``'s ``_inspect_start_preconditions``
    — the CONCERN_OPEN rung that refuses to open INSPECT while a cross-casting
    concern from the closing GRIND is unaddressed (GI-023, ST-005) — and
    ``orchestration/directives.py``'s co-dispatch join, which widens the set a
    task carries so ``Foundry-Tasks`` can mark the concern dispatched (CT-008,
    FR-039). Pass ``cycle`` to scope the answer to that GRIND; omit it for every
    open cross-casting concern.

    THE FILTER IS NOT HERE, AND THAT IS THE POINT (concern C-030, ruling
    ``lead_ruling_gi_033_leaf_moves`` item 3). This module used to carry its own
    copy of it beside the leaf's, which is two bodies of one rule — the shape
    GI-024 names and the package-wide single-definition guard is red on. The
    body now lives once, in ``foundry_state.open_cross_casting_concerns``, and a
    verifier module may read it there without reaching through this module into
    ``tools/foundry.py`` (GI-033).

    WHAT IS LEFT FOR THIS FUNCTION TO DO, which is why it is not merely the
    re-export. The leaf takes its status member as an ARGUMENT rather than
    typing one, and its callers divide on whether naming the member is worth
    it. A VERIFIER reaches ``vocab.CONCERN_STATUS_OPEN`` and the leaf directly,
    which is what ``orchestration/transitions.py`` does for the CONCERN_OPEN
    rung — it may not import this module at all. A lifecycle caller asking the
    ledger a ledger question does not need to spell the member at each site,
    and ``orchestration/directives.py``'s two co-dispatch reads take it here
    for that reason.

    It also keeps the import above HONEST. ``open_cross_casting_concerns`` is
    named in code by this body, so no repoint of an outside caller can turn it
    into the unused import that ``test_no_shipped_module_holds_an_unused_import``
    fails on — a file no later casting is dispatched to edit.

    "Unaddressed" is ``CONCERN_STATUS_OPEN`` and nothing else: FR-039 makes
    ``dispatched`` the mark Foundry-Tasks leaves when the concern reaches the
    casting that owns it, and a dispatched concern has been addressed by
    definition.
    """
    return open_cross_casting_concerns(
        fdir, status_open=CONCERN_STATUS_OPEN, cycle=cycle
    )


@ledger_refusals
def mark_concerns_dispatched(fdir: Path, concern_ids: list[str]) -> dict:
    """Move each named OPEN concern to ``dispatched``, and re-render.

    CALLER: casting 2's ``foundry_defects_to_tasks`` — the co-dispatch set
    marks the concerns it carried to their target casting (CT-008, FR-039).
    Never re-implemented by that caller; the status vocabulary and the render
    live here with the ledger.

    Only an OPEN concern moves. A closed one stays closed — dispatching after a
    close would walk the lifecycle backwards — and is reported under
    ``skipped``.
    """
    wanted = {str(cid) for cid in concern_ids or []}
    dispatched: list[str] = []
    skipped: list[str] = []
    unknown: list[str] = []
    if not wanted:
        return {"ok": True, "dispatched": [], "skipped": [], "unknown": []}

    path = fdir / CONCERNS_FILENAME
    with ledger_transaction(path, CONCERNS_COLLECTION_KEY) as records:
        seen = set()
        stamp = _now()
        # `_dict_records` — the one named tolerance again (D-128).
        for record in _dict_records(records):
            rid = str(record.get("id"))
            if rid not in wanted:
                continue
            seen.add(rid)
            if record.get("status") != CONCERN_STATUS_OPEN:
                skipped.append(rid)
                continue
            record["status"] = CONCERN_STATUS_DISPATCHED
            record["dispatched_at"] = stamp
            dispatched.append(rid)
        unknown = sorted(wanted - seen)
        render_problem = render_concerns_markdown(fdir, records)

    result = {
        "ok": True,
        "dispatched": dispatched,
        "skipped": skipped,
        "unknown": unknown,
    }
    if render_problem is not None:
        result["render_problem"] = render_problem
    return result
