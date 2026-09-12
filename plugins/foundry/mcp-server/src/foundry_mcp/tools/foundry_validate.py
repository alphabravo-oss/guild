"""Foundry casting validation — the mechanical quality gate before CAST.

Validates that castings will deliver the spec before any building starts.
A 5-minute validation saves hours of GRIND cycles.

ONE DIMENSION PER QUESTION ASKED, and the dimensions are not counted here.
`foundry_validate_castings` names every one of them in the payload it returns,
which is the only place the set can be read without going stale: this sentence
said "9-dimension" while the function answered in twelve, because a count typed
into prose beside a set that grows is a count that is wrong from the next
commit onward.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

# D-150: the requirement-ID families are declared ONCE, in the vocabulary
# module. This file held THREE hand-typed copies of the same literal, all
# knowing the same seven families and none knowing GI- or OT-, so a spec's
# invariants and observable truths were invisible to requirement coverage:
# never counted as spec requirements, and never counted as covered by a
# casting that cites them. NFR-002: the canonical pattern is a strict
# SUPERSET, so no ID that matched before stops matching.
from foundry_mcp.schemas.vocab import REQUIREMENT_ID_RE
# The artifact leaf, `tools/artifacts.py`: the house door guard and the total,
# tolerant document read. Both were reached at the top of the stack until the
# leaf existed — this module wanted two document utilities and imported a
# 15,000-line state machine to get them, which is what made the orchestrator
# the package's de-facto persistence layer. The bodies are the same bodies;
# only the module that defines them changed.
from foundry_mcp.tools.artifacts import (
    # fallout D-117: the marker's one spelling and the one digest spelling. The
    # F0.7 gate writes the matrix digest into `INTENT_CLEAN_MARKER` and sub-check
    # 7m below recomputes it with the same `_hash_file`, so the two sides of that
    # comparison cannot drift into two answers.
    INTENT_CLEAN_MARKER,
    _artifact_guard,
    _hash_file,
    _load_json,
    # D-134: the SHARED nested-shape validator, so "unusable manifest" means one
    # thing in every module that reads castings/manifest.json. It was reached
    # through `foundry_spawn` until concern C-060 moved it into the leaf, where
    # the verifier modules that also ask it may reach it legally — this module
    # imported a LIFECYCLE module to borrow a document predicate, which is the
    # edge GI-033 forbids and the one the widened boundary guard reports.
    #
    # BOUND UNDER THE OLD NAME, AND THAT IS LOAD-BEARING RATHER THAN LAZY.
    # D-134's scan recognises a reader as guarded by the NAME it calls, and
    # `test_the_locked_validator_names_are_still_the_ones_the_scan_looks_for`
    # pins that set to `_manifest_shape_problem` / `_manifest_shape_error`.
    # Spelling the call sites here `manifest_shape_problem` would leave the
    # guard in place and the scan blind to it, reporting both of this module's
    # manifest readers as unguarded — a failure that looks like a finding,
    # which the pin's own docstring names as worse than an import error.
    manifest_shape_problem as _manifest_shape_problem,
    # The rungs themselves, for the one reader that needs the PATH and not the
    # text: the cache fingerprint, which hashes the spec's bytes before any
    # dimension has run.
    _spec_path_from,
    # fallout GI-033 / concern C-018 — the two-rung spec ladder is LEAF
    # material, not this module's. `orchestration/gates.py` needs the same
    # climb for the DONE gate's requirement count and P3 verdict
    # synthesis, and a verifier module and a lifecycle module have no legal
    # edge between them — so neither can import the other's copy and the
    # answer to "which file is this run's spec" has to live below both.
    _spec_requirement_ids,
)
# D-180: the ONE derivation of "which requirement IDs does this casting own",
# shared with the acceptance gate that demands evidence for each of them. See
# `declared_requirement_ids`' docstring for the driven case, and the coverage
# dimension below, which is its third caller. The edge is acyclic: foundry_handoff
# imports the artifact leaf and foundry_state, and nothing in that chain imports
# foundry_validate (only server.py does).
#
# fallout concern C-067 — AND IT IS STILL THE WRONG HOME, WHICH IS RECORDED HERE
# BECAUSE THE MOVE DID NOT FIT IN ONE CASTING. `tools/evidence.py` reads the same
# function across the layer boundary fallout GI-033 draws, and the one remedy
# that invariant admits is a leaf move. It was driven in this cycle and backed
# out: `tests/test_handoff_records.py#test_neither_reader_derives_the_declared_set_inline`
# and `tests/test_evidence.py#test_no_reader_of_the_owned_set_derives_it_inline`
# each assert the function is defined exactly once across a scan set of
# `foundry_handoff` and THIS module, so a definition in a third makes both read
# zero. fallout GI-026 requires an AST pin to be repointed in the same casting as
# the source move, and neither test module is casting 7's — so the move is one
# dispatch, not one file. THIS import is legal either way, both ends being
# lifecycle, which is exactly why it would have gone on pointing at the old home
# unnoticed.
# fallout D-181 — AND ITS SIBLING, WHICH ANSWERS THE OTHER QUESTION. "Which
# ids is this casting ANSWERABLE for" (position) and "which ids does its
# excerpt MENTION" (every occurrence but a `Maps to:` cross-reference) are two
# questions, and AC-001 / OT-001 / FR-040 all ask the second one in the word
# CITES. One derivation served both until D-181, so an id cited mid-line, mid-
# prose or inside backticks was invisible to the ownership dimension and a
# casting citing an id it did not own validated clean. Widening the first
# reader is what re-files D-180; importing the second is what closes D-181.
#
# fallout GI-010 / GI-033 (D-191 / D-192) — AND THE PAIR IS READ FROM TWO
# MODULES NOW, BECAUSE THE LAYERING RULE SPLIT IT.
#
# `declared_requirement_ids` left `foundry_handoff.py` for the leaf under D-191:
# `tools/evidence.py` reads it from the verifier side and this module from the
# lifecycle side, and a symbol read from both can live in neither. This import
# went on naming `foundry_handoff` for a cycle after the definition left it,
# which resolved — Python is happy to hand back a name a module imported — and
# was exactly the facade read GI-010 forbids: the reader could not tell from the
# line which module owns the rule, and a later deletion of that module's own
# import would have broken this one for no reason a reader could see.
#
# `cited_requirement_ids` STAYS in `foundry_handoff.py`, and the split is the
# arithmetic rather than a half-move: its readers are that module and this one,
# both lifecycle, so nothing forces it into a leaf.
from foundry_mcp.tools.artifacts import declared_requirement_ids
from foundry_mcp.tools.foundry_handoff import cited_requirement_ids
from foundry_mcp.tools.foundry_state import (
    # fallout D-172: the surface walk excludes the run archive by the name
    # the state module gives it, never by a second spelling of it here.
    ARCHIVE_DIR,
    document_refusal,
    get_run_dir,
    read_document,
    read_text_file,
)


#: The archive schema at which a casting's `requirement_ids` becomes MANDATORY.
#:
#: `forge-specs/foundry-run-fallout/spec.md` FR-054: the field is additive, so
#: every archive written before it existed must keep validating. The floor is
#: what separates "this run predates the field" from "this run was created
#: under a release that writes it and a casting is missing it" — the first is
#: reported NOT COMPUTABLE and passes, the second is refused.
#:
#: NOT DERIVED FROM THE GENERATION, AND THAT IS THE WHOLE POINT OF DECLARING IT
#: SEPARATELY. `foundry_state.py#ARCHIVE_SCHEMA_VERSION` is now the leaf home of
#: "which generation is current", and its own comment names this module among
#: the consumers that should reach it. This constant deliberately does not:
#: they read 4 apiece today only because `requirement_ids` became mandatory in
#: the generation that happens to be current, and that coincidence is the trap.
#:
#: The generation says what a run's artefacts ARE and MOVES at every bump. This
#: says where `requirement_ids` STARTED being mandatory, which is a historical
#: fact that must stay put — define it as the generation and the next bump
#: silently re-reads every schema-4 archive as predating a field it carries,
#: turning a whole shelf of valid archives into refusals nobody asked for.
#:
#: So what binds them is a RELATION, never an assignment:
#: `ARCHIVE_SCHEMA_VERSION >= REQUIREMENT_IDS_SCHEMA_FLOOR`. It is held where
#: each writer of the marker can break it — `tests/test_migrate_archive.py` for
#: the value the migration stamps, and `tests/test_validate_ownership.py` for
#: the value a run this server just created carries, read back off its own
#: state.json rather than out of a fixture.
REQUIREMENT_IDS_SCHEMA_FLOOR = 4

#: How many castings may own one requirement id before F0.9 wants a reason.
#:
#: `forge-specs/foundry-run-fallout/spec.md` FR-013 / AC-042: "refuse at F0.9
#: when any requirement spans more than two castings without a recorded
#: reason". The number appears ONCE in this file and both the refusal message
#: and the printed table derive from it, so the prose a lead reads and the
#: threshold the gate applies cannot drift apart.
REQUIREMENT_SPAN_MAX = 2

#: The refusal token for a span above `REQUIREMENT_SPAN_MAX` with no recorded
#: reason. Spelled once; the issue, the top-level message and the hint all
#: derive from this name, and the lead protocol quotes it.
REQUIREMENT_SPAN_EXCEEDED = "REQUIREMENT_SPAN_EXCEEDED"


#: The two exits a lead can actually take when the span token fires. One
#: sentence, derived from the threshold above, because more than one arm emits
#: it and a second spelling of it is a second rule.
_SPAN_EXITS_HINT = (
    f"Regroup the requirement onto at most {REQUIREMENT_SPAN_MAX} castings, or "
    f"record a `split_reason` entry naming the id and why the surfaces cannot "
    f"share an owner."
)


def _owner_sort_key(owner: object) -> tuple:
    """Order casting ids so a lead reads them in the order they think in.

    Casting ids are integers in every manifest this package writes, and sorting
    them by their printed form puts #10 between #1 and #2 — a table a reader
    has to re-sort in their head, in the F0.9 output AND in the F6 report that
    renders the same records. So integers order numerically and everything else
    orders after them by its printed form, which keeps the ordering TOTAL
    (a manifest is free to spell an id as a string) without ever comparing an
    int to a str.

    ``bool`` is excluded from the numeric arm for the same reason
    ``_archive_schema_version`` excludes it: it is a subclass of ``int`` and
    would sort as 0 or 1 beside real ids.
    """
    if isinstance(owner, bool) or not isinstance(owner, int):
        return (1, str(owner))
    return (0, owner)


def _recorded_split_reasons(manifest: dict, castings: list) -> dict[str, str]:
    """``{requirement id: the reason recorded for it}`` across the manifest.

    TWO POSITIONS, ONE DERIVATION. The manifest records a reason in either of
    two places and both are authoritative, so this is the single answer to
    "is there a recorded reason for this id" rather than two scans that could
    disagree:

      * per casting — ``castings[*].split_reason``, a ``{id: text}`` map. This
        is the shape F0.5 DECOMPOSE emits: the casting that had to be given a
        share of the requirement records why, next to its own ownership list.
      * top level — ``manifest["split_reason"]``, the same map for a reason
        that belongs to no single casting.

    A reason recorded ANYWHERE in the manifest exempts the id it names, and
    only the id it names: a reason for a different requirement is not a waiver
    for this one, which is the whole difference between a recorded reason and a
    blanket one. Where two positions record a reason for the same id, the
    per-casting one is kept, because it is the one written beside the ownership
    it explains.

    Total: a `split_reason` of the wrong type, or one whose values are not
    strings, contributes nothing rather than raising.
    """
    reasons: dict[str, str] = {}
    top = manifest.get("split_reason")
    if isinstance(top, dict):
        reasons.update(
            {k: v for k, v in top.items() if isinstance(k, str) and isinstance(v, str) and v}
        )
    for casting in castings:
        per = casting.get("split_reason")
        if not isinstance(per, dict):
            continue
        reasons.update(
            {k: v for k, v in per.items() if isinstance(k, str) and isinstance(v, str) and v}
        )
    return reasons


def _requirement_span_rows(
    spec_req_ids: set,
    castings: list,
    ownership: dict,
    reasons: dict,
) -> list[dict]:
    """One record per requirement id: the id, its owning castings, its span.

    THE ONE COMPUTATION. The refusal and the printed table both read this list,
    so what a lead is shown and what the gate acts on cannot disagree about who
    owns what.

    Computed from the PERSISTED ``requirement_ids``, never from the prose. That
    is the same rule the ownership dimension enforces read from the other end:
    ownership is a claim the manifest makes, and deriving it from `spec_text`
    at the moment a consumer asks is what having a persisted field replaces.

    MEMBERSHIP IS THE UNION, and each half is here for its own reason. Every id
    THE SPEC DECLARES is a row, including the ones with a single owner and the
    ones with none, because the table's job is to show a lead the whole
    ownership picture rather than only its problems. Every id A CASTING OWNS is
    also a row, even one the spec does not declare, because the span rule is
    about ownership: an id could otherwise carry three owners and no row.

    Owners are ordered by ``_owner_sort_key``, so the table and the refusal are
    byte-stable across runs whether casting ids are integers or strings — and
    are in the order a lead reads them in rather than in the order that puts
    #10 between #1 and #2.
    """
    owners: dict[str, list] = {}
    for casting in castings:
        # The casting's id AS THE MANIFEST SPELLS IT, so the table a lead reads
        # names what they would search the manifest for. `ownership` is keyed by
        # the printed form because that is what makes the lookup total; the
        # display value comes from the record.
        cid = casting.get("id", "?")
        _present, owned = ownership.get(str(cid), (False, set()))
        for rid in owned:
            held = owners.setdefault(rid, [])
            # Two castings sharing an id would otherwise be counted twice and
            # inflate the span past a threshold nothing really crossed.
            if cid not in held:
                held.append(cid)
    rows = []
    for rid in sorted(set(spec_req_ids) | set(owners)):
        held = sorted(owners.get(rid, []), key=_owner_sort_key)
        rows.append({
            "id": rid,
            "owners": held,
            "span": len(held),
            "split_reason": reasons.get(rid),
        })
    return rows


#: What the table prints where a row has no recorded reason. One spelling, so a
#: reader scanning the column sees one shape rather than two ways of saying
#: nothing.
_SPAN_NO_REASON = "—"


def _span_cell(value: object) -> str:
    """One cell of the span table: no pipe opens a column, no break opens a row.

    D-029 / D-030 (should-not-stop FR-032) — A FIELD A LINE IS BUILT FROM CAN
    NEVER ADD A LINE. The row below is concatenated from four values a manifest
    supplies, and `_owned_requirement_ids` keeps ANY non-empty string while
    `_recorded_split_reasons` keeps any non-empty reason. So an id or a reason
    carrying a line break did not merely wrap: it forged a `## ` heading, and
    `foundry_state.markdown_sections` — which splits the F6 report by whole
    `## ` lines for the DONE gate's read and the seal's boundary — then read the
    genuine `Requirement span` section as ENDING there, its body cut mid-table
    right after the first cell. An added heading is the visible half; the
    severed section is the one that loses content.

    THE READER SETTLES WHICH CHARACTERS COUNT, NOT THE KEYBOARD. That reader is
    `str.splitlines()`, which honours ELEVEN separators, and `Path.read_text`
    translates a lone `\\r` into a real `\\n` on the way back in besides.
    Replacing `"\\n"` alone would close the separator a human types and leave
    the ten a paste carries: `\\r`, `\\f`, `\\v`, `\\x85` and `\\u2028` were each
    driven through `generate_report` and each forged a heading. Every one of
    the eleven is `isspace()`-true, so collapsing whitespace closes the whole
    set in one expression rather than in a character list that goes stale the
    next time the reader learns a separator.

    NOT ROUTED THROUGH A FALSY-COALESCING FLATTENER, deliberately. `span` is an
    int and an unowned requirement's span is `0`, which a `str(value or "")`
    spelling renders as an EMPTY cell — a measured zero turned into "nothing
    recorded", which is a different claim. The None check is explicit for that
    reason, and `0` renders as `0`.
    """
    return " ".join(str("" if value is None else value).split()).replace("|", "\\|")


def _render_span_table(rows: list[dict], not_computable: bool) -> str:
    """The span table as a markdown block, for a surface with no formatter.

    `Foundry-Validate-Castings` returns a payload and has no display module of
    its own, so the table ships BOTH ways: `rows` for a reader that will render
    it and this block for the lead reading F0.9's output directly. Both come
    from the one computation, so they cannot disagree about who owns what.

    THE OTHER READER NOW EXISTS, and it did not when this said so. The sentence
    "the F6 report draws the same table from the same records" was written here
    as a promise and D-039 was filed on its being false — the report had no such
    section. It has one now, and it reaches this computation through
    `requirement_span_table` rather than mirroring it, which is what makes the
    promise a fact instead of a plan (fallout AC-044).

    An archive that predates the persisted ownership field gets a sentence
    saying so rather than an empty table, because an empty table reads as "no
    requirements" and that is a different and alarming claim.
    """
    if not_computable:
        return (
            "Requirement span: not computable — no casting in this manifest "
            "carries `requirement_ids`, and the archive predates schema "
            f"{REQUIREMENT_IDS_SCHEMA_FLOOR}."
        )
    if not rows:
        return "Requirement span: no requirement ids declared or owned."
    lines = [
        # These two interpolate nothing, so they are the only lines here no
        # manifest value can reach. Every line below them is built from one.
        "| requirement | owners | span | recorded reason |",
        "|---|---|---|---|",
    ]
    for row in rows:
        # All four cells, because all four are manifest values. The owners
        # column is no safer than the id column: `_requirement_span_rows` takes
        # the casting id AS THE MANIFEST SPELLS IT, so what a lead reads there
        # is an unvalidated document field too.
        owners = ", ".join(
            f"#{_span_cell(owner)}" for owner in row["owners"]
        ) or _SPAN_NO_REASON
        reason = _span_cell(row["split_reason"]) or _SPAN_NO_REASON
        lines.append(
            f"| {_span_cell(row['id'])} | {owners} | "
            f"{_span_cell(row['span'])} | {reason} |"
        )
    return "\n".join(lines)


def _archive_schema_version(state: dict) -> int:
    """The run's archive schema marker, or 0 when it is absent or unusable.

    0 rather than None so every comparison against
    ``REQUIREMENT_IDS_SCHEMA_FLOOR`` is an int comparison with no second shape
    to remember. An archive written before the marker existed and one whose
    marker is a string both read as "below the floor", which is the same
    answer: this run predates the field.

    ``bool`` is excluded explicitly because it is a subclass of ``int`` and
    ``True < 4`` would otherwise answer a question nobody asked.
    """
    raw = state.get("archive_schema_version")
    if isinstance(raw, bool) or not isinstance(raw, int):
        return 0
    return raw


def _owned_requirement_ids(casting: dict) -> tuple[bool, set[str]]:
    """``(the field is present, the ids it names)`` for one casting.

    PRESENCE AND EMPTINESS ARE DIFFERENT CLAIMS, and the F0.9 rule turns on the
    difference. An ABSENT list is an un-migrated record, which an archive
    written before the field existed is allowed to be and a run created under
    the current schema is not. An EMPTY list is a casting stating that it owns
    nothing, which is a claim this gate can check.

    Total, like every other read on this surface: a `requirement_ids` that is
    not a list at all is not a shape `_manifest_shape_problem` judges, so it
    reads as present-and-empty and every id the casting's own excerpt declares
    is then reported unowned BY NAME, rather than raising here.
    """
    raw = casting.get("requirement_ids")
    if raw is None:
        return False, set()
    if not isinstance(raw, list):
        return True, set()
    return True, {v for v in raw if isinstance(v, str) and v}


def _ownership_and_computability(
    castings: list, schema_version: int
) -> tuple[dict, bool]:
    """``({casting id: (field present, ids owned)}, not computable)``.

    ONE DERIVATION OF BOTH, because the two travel together: every rung that
    reads ownership also has to know whether ownership is knowable for this
    archive, and a surface that recomputed one without the other would report a
    manifest as inconsistent for lacking a field its release never wrote.

    NOT COMPUTABLE, AND ONLY FOR AN ARCHIVE THAT PREDATES THE FIELD (FR-054).
    Three conditions, each carrying its own weight: there ARE castings, NO
    casting carries the list, and the run is below the schema floor. A run
    created under the current release is refused for the same manifest, which
    is what "fail closed only for new runs" means. A partially-filled manifest
    is evaluated whatever the schema — somebody has started populating it and
    the gaps are real.

    ``bool(castings)`` is the rung F0.9 never reaches and the F6 report does:
    the gate refuses an empty manifest before it gets here, while the report
    renders one, and without this the "no casting carries it" clause is
    vacuously true over an empty list and the sentence blames a schema floor
    for what is really an absent decomposition.
    """
    ownership: dict[str, tuple[bool, set[str]]] = {
        str(c.get("id", "?")): _owned_requirement_ids(c)
        for c in castings
        if isinstance(c, dict)
    }
    not_computable = bool(castings) and (
        not any(present for present, _ in ownership.values())
        and schema_version < REQUIREMENT_IDS_SCHEMA_FLOOR
    )
    return ownership, not_computable


def _requirement_span_payload(
    manifest: dict,
    castings: list,
    spec_req_ids: set,
    ownership: dict,
    not_computable: bool,
) -> dict:
    """The span table as data plus text, assembled from documents already read.

    ``{"threshold", "not_computable", "rows", "text"}``. The inner half of
    ``requirement_span_table``: this one takes what a caller has already read,
    so the F0.9 gate — which has the manifest, the spec and the schema marker
    in hand — reaches the computation without opening any of them a second
    time. A reader that holds only paths calls the public entry point below,
    which reads them once and arrives here.

    ``rows`` and ``text`` come from the same list, so the records a surface
    renders and the block it prints cannot name different owners; and because
    the refusal reads these same rows, neither can disagree with the gate.
    """
    reasons = _recorded_split_reasons(manifest, castings)
    rows = (
        []
        if not_computable
        else _requirement_span_rows(
            spec_req_ids,
            [c for c in castings if isinstance(c, dict)],
            ownership,
            reasons,
        )
    )
    return {
        "threshold": REQUIREMENT_SPAN_MAX,
        "not_computable": not_computable,
        "rows": rows,
        "text": _render_span_table(rows, not_computable),
    }


def requirement_span_table(project_root=".", fdir: Path | None = None) -> dict:
    """AC-044 — THE requirement span table, for any surface that renders it.

    ``{"threshold", "not_computable", "rows", "text", "problem"}``. ``rows`` is
    one record per requirement id — ``{"id", "owners", "span", "split_reason"}``
    — covering every id the spec declares and every id a casting owns, ordered
    deterministically. ``problem`` is the named reason the manifest could not be
    read OR is not shaped like a manifest, or None; on a problem the other keys
    carry the empty table rather than a half-built one.

    ONE COMPUTATION, TWO SURFACES (fallout AC-044, D-039). The F0.9 gate refuses
    on this table and the F6 report prints it, and they must be the SAME table:
    a second assembly anywhere would be a second answer to "who owns this
    requirement", free to disagree with the answer a run was passed or refused
    on. This is the one public entry point for the second surface — it exists so
    a renderer imports ONE name instead of reaching five privates and re-
    spelling the not-computable rule and the spec ladder, which is how the two
    answers drift apart. ``foundry_validate_castings`` reaches the same
    computation through ``_requirement_span_payload`` with the documents it has
    already read, so neither surface reads anything twice.

    TOTAL, ON EVERY SHAPE IT CAN MEET, because the F6 report must not refuse.
    No active run, no manifest, an unreadable manifest and a manifest whose
    ``castings`` is not a list each answer with the empty table — and an archive
    that predates ``requirement_ids`` answers NOT COMPUTABLE, with the sentence
    saying so rather than an empty table, because an empty table reads as "no
    requirements" and that is a different and alarming claim.
    """
    if fdir is None:
        fdir = get_run_dir(project_root)
    if not fdir:
        return {
            "threshold": REQUIREMENT_SPAN_MAX,
            "not_computable": False,
            "rows": [],
            "text": _render_span_table([], False),
            "problem": None,
        }

    manifest, problem = read_document(Path(fdir) / "castings" / "manifest.json")
    if problem is not None:
        return {
            "threshold": REQUIREMENT_SPAN_MAX,
            "not_computable": False,
            "rows": [],
            "text": _render_span_table([], False),
            "problem": problem,
        }

    # D-132 / D-134 — THE SHAPE BEFORE THE RECORDS, at this door too. A
    # manifest that is valid JSON of the wrong shape (`castings` a string, or a
    # list of ints or nulls) parses cleanly and then meets `.get()` inside
    # `_recorded_split_reasons`, which is an AttributeError raised across the
    # MCP boundary from a surface whose whole contract is a named answer. The
    # gate runs this check in its prelude; this entry point is a SECOND door
    # onto the same document and owes the same check rather than inheriting it.
    # An absent manifest reads as `{}` here and is not a shape problem, so the
    # empty-table path above is unaffected.
    if (records := _manifest_shape_problem(manifest)) is not None:
        return {
            "threshold": REQUIREMENT_SPAN_MAX,
            "not_computable": False,
            "rows": [],
            "text": _render_span_table([], False),
            "problem": records,
        }

    castings = manifest.get("castings")
    castings = castings if isinstance(castings, list) else []
    state = _load_json(Path(fdir) / "state.json")
    ownership, not_computable = _ownership_and_computability(
        castings, _archive_schema_version(state)
    )
    _text, spec_req_ids, _path, _problem = _spec_requirement_ids(
        project_root, Path(fdir), state
    )
    payload = _requirement_span_payload(
        manifest, castings, spec_req_ids, ownership, not_computable
    )
    return {**payload, "problem": None}


#: fallout D-172 — the refusal token for a shipped surface no casting owns.
#: Spelled once; the per-file issue, the top-level message and the hint all
#: derive from this name, and the lead protocol quotes it.
SURFACE_UNOWNED = "SURFACE_UNOWNED"


#: The two exits a lead can actually take when the surface token fires. One
#: sentence, because more than one arm emits it and a second spelling of it is
#: a second rule. Both exits are things a lead can DO to the manifest: the
#: first widens ownership, the second narrows the claim about what ships.
_SURFACE_EXITS_HINT = (
    "Add the file to the `key_files` of the casting that answers for it — a "
    "directory entry ending in `/` claims every path beneath it — or narrow "
    "`surface_globs` so the file is not claimed as shipped surface."
)


#: Path segments that are never shipped surface, whatever a declared glob
#: reaches through.
#:
#: A glob names the surface by TREE and EXTENSION; these are the positions
#: inside such a tree that are build output, dependency payload, tool cache or
#: this server's own run scratch — nothing a casting could own, and nothing a
#: lead should have to exclude by hand in every pattern. The run archive is
#: taken from `ARCHIVE_DIR` rather than re-typed: which directory a run writes
#: into is the state module's answer, and this module has no second one.
_SURFACE_EXCLUDED_SEGMENTS = frozenset({
    ARCHIVE_DIR,
    ".git",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    # The detached worktrees the evidence sweep materialises. They live under
    # `ARCHIVE_DIR` today and are named here anyway, because a sweep root
    # configured elsewhere is still a copy of the tree and not a second
    # surface to own.
    "worktrees",
})


#: How many unowned surfaces get a prose issue row of their own. The
#: dimension's `unowned` list carries every one of them whatever this says —
#: the cap is on the NARRATION, so a manifest that owns nothing does not bury
#: the other twelve dimensions under a thousand paragraphs saying one thing.
#: Dimension 10 caps its scope-creep rows at the same number for the same
#: reason.
_SURFACE_ISSUE_CAP = 20


def _declared_surface_globs(manifest: dict) -> tuple[bool, list[str], list[list[str]]]:
    """The manifest's `surface_globs` claim: is one made, and what can be used?

    Returns ``(declared, usable, rejected)`` where ``rejected`` is a
    ``[entry, why]`` pair per entry that is not a project-relative pattern.

    ABSENT AND EMPTY ARE DIFFERENT CLAIMS, exactly as they are for
    `requirement_ids` one dimension up. No field at all is a manifest that
    predates the question — reported not computable, never refused. A field
    present but reaching nothing is a manifest that ANSWERED the question with
    a declaration that cannot do its job, which is worth saying out loud: a
    check that silently self-disables is the same shape as the gap D-172 was
    filed against.

    Total, like every other read on this surface: a `surface_globs` that is a
    string, a number or a list of them is REPORTED, never raised, because a
    tool never raises across the MCP boundary.
    """
    raw = manifest.get("surface_globs")
    if raw is None:
        return False, [], []
    if not isinstance(raw, list):
        return True, [], [[repr(raw)[:120], "`surface_globs` is not a list of patterns"]]
    usable: list[str] = []
    rejected: list[list[str]] = []
    for entry in raw:
        if not isinstance(entry, str) or not entry.strip():
            rejected.append([repr(entry)[:120], "not a non-empty string"])
            continue
        pattern = entry.strip()
        # `Path.glob` raises on an absolute pattern on the 3.12 floor and
        # resolves one differently above it, and either way a surface outside
        # the project is a surface no `key_files` entry could ever name.
        if pattern.startswith(("/", "~")):
            rejected.append([pattern, "absolute pattern; `surface_globs` is project-relative"])
            continue
        if ".." in Path(pattern).parts:
            rejected.append([pattern, "`..` reaches outside the project root"])
            continue
        usable.append(pattern)
    # Order preserved, duplicates dropped: two spellings of one pattern are one
    # claim, and counting a surface twice would say nothing true.
    return True, list(dict.fromkeys(usable)), rejected


def _shipped_surfaces(
    project_root: Path, globs: list[str]
) -> tuple[list[str], dict[str, int], list[list[str]]]:
    """Every file under ``project_root`` the declared globs reach.

    Returns ``(surfaces, matched_per_glob, unreadable)`` — the surfaces sorted
    and de-duplicated, the per-pattern count so a pattern that reaches nothing
    can be named, and a ``[pattern, why]`` pair for any expansion the
    filesystem refused.

    NORMALISED THROUGH THE SAME ONE NORMALISER `key_files` GOES THROUGH, which
    is what makes the diff below a comparison rather than two vocabularies. A
    path `_normalize_file_path` returns empty for is dropped here on purpose:
    if this module cannot spell it, no `key_files` entry can name it either, so
    reporting it unowned would be reporting a file no lead could ever own.
    """
    surfaces: set[str] = set()
    matched: dict[str, int] = {}
    unreadable: list[list[str]] = []
    for pattern in globs:
        hit_count = 0
        try:
            hits = list(project_root.glob(pattern))
        except (OSError, ValueError, IndexError, NotImplementedError) as exc:
            unreadable.append([pattern, f"{type(exc).__name__}: {exc}"])
            matched[pattern] = 0
            continue
        for hit in hits:
            try:
                if not hit.is_file():
                    continue
                relative = hit.relative_to(project_root)
            except (OSError, ValueError):
                continue
            if _SURFACE_EXCLUDED_SEGMENTS.intersection(relative.parts):
                continue
            normalised = _normalize_file_path(relative.as_posix())
            if not normalised:
                continue
            hit_count += 1
            surfaces.add(normalised)
        matched[pattern] = hit_count
    return sorted(surfaces), matched, unreadable


def _surface_facts(project_root: Path, manifest: dict) -> dict:
    """What the PROJECT TREE says, for the dimension that asks it.

    The same shape of fact `_run_dir_facts` gathers about the run directory,
    and gathered here for the same two readers: the cache fingerprint and the
    dimension. A shipped surface appearing after a passing verdict moves no
    byte of the manifest and no byte of the spec, so a fingerprint built from
    documents alone would serve the pass given before the file existed.

    Costs nothing when no claim is made: an undeclared surface walks no tree.
    """
    declared, globs, rejected = _declared_surface_globs(manifest)
    if not declared:
        return {
            "declared": False,
            "globs": [],
            "rejected": [],
            "matched": {},
            "surfaces": [],
        }
    surfaces, matched, unreadable = _shipped_surfaces(project_root, globs)
    return {
        "declared": True,
        "globs": globs,
        "rejected": rejected + unreadable,
        "matched": matched,
        "surfaces": surfaces,
    }


def _unowned_surfaces(surfaces: list[str], castings: list) -> list[str]:
    """The shipped surfaces no casting's `key_files` reaches.

    THROUGH `_key_file_covers`, WHICH IS THE ONE READER OF THAT QUESTION. A
    `key_files` entry is a file path or a directory spelled with a trailing
    slash, and the difference is decided in exactly one place in this module.
    Splitting the two cases open here to save a comparison would be a second
    reading of the manifest format — D-170's shape, which cost thirteen files
    resolving to no casting at all because two readers of one rule disagreed
    with nothing comparing them.
    """
    entries = sorted({normalised for normalised, _cid in _declared_key_files(castings)})
    return [
        path
        for path in surfaces
        if not any(_key_file_covers(entry, path) for entry in entries)
    ]


#: The run-directory position F0.9 asks about by PRESENCE, not by content: the
#: directory the research artifacts land in. Spelled once because two surfaces
#: ask about it — the dimension that reports on it and the cache key that has to
#: notice it appear — and a second spelling is a second answer waiting to drift.
#: `foundry_spawn.py#_expected_inspect_stream_agents` asks the same question of
#: the same directory -- is there research for RESEARCH_AUDIT to audit -- and
#: asks it with `is_dir`, which is the spelling that is total: a
#: plain FILE at that path answers `exists` yes and then raises out of
#: `iterdir`, and a tool never raises across the MCP boundary.
_RESEARCH_DIRNAME = "research"

#: The other one: the F0.7 matrix, whose ABSENCE is what prompt fidelity
#: refuses on. The refusal below names the path, and derives it from here, so
#: the file the message tells an operator to look for is the file the check
#: looked for.
_INTENT_COVERAGE_BASENAME = "intent-coverage.json"

#: fallout D-117 — the token sub-check 7m refuses a STALE F0.7 verdict with,
#: spelled once here and derived by both the issue and the hint below. It is a
#: different finding from `INTENT_COVERAGE_RECORD_INCOMPLETE`, which says F0.7
#: left no record; this one says F0.7 left a record for a DIFFERENT matrix, and
#: a lead reading the two needs to be told apart which of "run it" and "run it
#: AGAIN" they are being told.
INTENT_COVERAGE_STALE = "INTENT_COVERAGE_STALE"


def _intent_marker_staleness(fdir: Path, coverage_path: Path) -> str | None:
    """Why the standing F0.7 verdict does not answer for the matrix on disk.

    Returns the reason, or None when the marker vouches for exactly this
    matrix.

    fallout D-117 — WHAT MADE THE ANTI-SKIP GUARD FAIL OPEN WAS THAT NOTHING
    READ THE MARKER. `intent_coverage.py`'s docstring said "Orchestrator's F0.9
    sub-check 7m reads this marker to confirm F0.7 actually ran (anti-skip
    discipline)" and 7m did not: it read whether intent-coverage.json exists
    and whether the manifest carries a summary, both of them absent-value
    predicates that any earlier pass satisfies forever. A run that passed F0.7,
    regenerated its matrix and re-entered F0.9 was waved through on a verdict
    about a document that no longer existed.

    So the marker is read, and it carries the digest of the matrix it passed on
    rather than the word "ok". Recomputing that digest here is what turns
    "F0.7 ran at some point" into "F0.7 ran on THIS matrix" — the only one of
    the two that is an anti-skip check, and the only one a lead who edits the
    matrix and skips the gate cannot satisfy by doing nothing.

    Total, like every other read in this module: an unreadable marker is a
    stale marker, never a raise across the MCP boundary. A marker written by a
    release before the digest existed reads as its own literal (`ok`) and is
    named as such — re-running the gate is the fix in both cases, and it is the
    same fix, so the two do not need separate tokens.
    """
    marker = fdir / INTENT_CLEAN_MARKER
    try:
        recorded = marker.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError):
        recorded = ""
    if not recorded:
        return (
            f"{marker.name} is missing or unreadable, so nothing records that "
            f"F0.7 INTENT-CARRIER ever ran on {coverage_path.name}"
        )

    actual = _hash_file(coverage_path)
    if recorded != actual:
        return (
            f"{marker.name} vouches for matrix {recorded}, but "
            f"{coverage_path.name} now hashes to {actual}"
        )
    return None


# --------------------------------------------------------------------------- #
# fallout GI-004 (D-151, concern C-072) — THE DIMENSIONS READ A CASTING
# TOLERANTLY AND REPORT WHAT THEY FOUND. THEY DO NOT RAISE ON IT.
#
# `must_haves = c.get("must_haves", {})` supplies a mapping when the key is
# ABSENT and passes a present non-mapping straight through, so a manifest
# carrying `"must_haves": ["a list, not a mapping"]` reached
# `must_haves.get("truths", [])` and raised `AttributeError: 'list' object has
# no attribute 'get'` across the MCP boundary — call_tool's unhandled-error
# banner where the house shape is `{error, hint}`. Driven by casting 2 against
# the shipped validator and filed as C-072. Two more of the same shape sat
# beside it in the same loop, one line apart and equally reachable:
# `observable_truths` as an integer raised `TypeError: 'int' object is not
# iterable`, and `must_haves.truths` as an integer raised `TypeError: object of
# type 'int' has no len()`.
#
# A-000's sentence, which GI-004 carries, is unqualified: "A REACHABLE RAISE ...
# REMAINS A BLOCKING DEFECT AT FULL WEIGHT." No writer in the plugin emits any
# of these shapes, so reaching one needs a hand-edited, migrated or
# partially-written manifest — which is exactly the population
# `scripts/migrate-archive.py` and the resume path operate on.
#
# WHY A TOLERANT READER AND NOT A RUNG ON `_MANIFEST_DOCUMENT_SHAPE`. Declaring
# `must_haves` in the leaf's manifest shape would be the stronger check and the
# wrong one HERE: `manifest_shape_problem` refuses the WHOLE DOCUMENT, so a
# malformed `must_haves` would stop dimension 1 as well and F0.9 would again
# report nothing. What C-072 asks for is the opposite — that the ownership and
# span dimensions keep running and the operator is told WHICH casting is
# malformed, in the same issue shape every other dimension already uses. So
# these two readers degrade, exactly as `_load_json` degrades, and dimension 2
# below carries the refusal, exactly as `_artifact_guard` carries it there.
# The tolerance and the report are a pair; neither is the other's substitute.
# --------------------------------------------------------------------------- #


def _must_haves(casting: dict) -> dict:
    """A casting's ``must_haves`` as a MAPPING — ``{}`` when it is not one.

    Every dimension that reads the block reads it through here, so "present but
    the wrong type" answers the same way at all four sites rather than at
    whichever one a defect was reported against.
    """
    value = casting.get("must_haves", {})
    return value if isinstance(value, dict) else {}


def _listed(container: dict, key: str) -> list:
    """``container[key]`` as a LIST — ``[]`` when it is not one.

    The rung below ``_must_haves``: a mapping whose ``truths`` is an integer is
    a shape the reader above cannot catch, because the block itself is fine.
    """
    value = container.get(key, [])
    return value if isinstance(value, list) else []


def _shape_issue(cid: object, title: str, key: str, value: object) -> dict:
    """The dimension-2 row naming a casting whose ``key`` is the wrong type.

    One spelling, because three checks emit it and a fourth will: the operator
    needs the casting, the key and what was actually found, and a row that
    named only two of the three would send them to read the manifest to learn
    the third.
    """
    return {
        "casting": cid,
        "title": title,
        "issue": (
            f"{key} is of type {type(value).__name__}, not "
            f"{'a mapping' if key == 'must_haves' else 'a list'} — "
            f"the entry is ignored and every check that reads it is skipped"
        ),
    }


def _run_dir_facts(fdir: Path) -> dict:
    """What the run DIRECTORY says, for the dimensions that ask it.

    Two of F0.9's dimensions read a fact no document carries — whether the run
    holds research artifacts, and whether F0.7 emitted the intent matrix — and
    both facts are cache inputs for exactly the reason they are dimension
    inputs: the verdict moves when they move. ONE derivation, computed in the
    prelude and read by both the fingerprint and the dimensions, so the answer
    the cache is keyed on and the answer the report is written from are the same
    answer rather than two agreeing statements of the same path.

    Total, like every other read here: an unreadable or vanished run directory
    answers false rather than raising.
    """
    research_dir = fdir / _RESEARCH_DIRNAME
    try:
        has_research = research_dir.is_dir() and any(research_dir.iterdir())
    except OSError:
        has_research = False
    return {
        "has_research": has_research,
        "has_intent_coverage": (fdir / _INTENT_COVERAGE_BASENAME).exists(),
    }


def _fingerprint_inputs(
    fdir: Path,
    manifest: dict,
    schema_version: int = 0,
    *,
    spec_path: Path | None,
    run_facts: dict,
    surface_facts: dict,
) -> dict:
    """Hash the inputs that validator dimensions depend on.

    Returns {spec_hash, manifest_hash, castings: {id: {prompt_hash, manifest_entry_hash}}}.
    Any change in these hashes invalidates the cached validation result
    for the affected casting (or all castings when spec/manifest-level
    inputs change).

    THE SPEC IS THE ONE THE VALIDATOR READ, which is why the path is handed in
    rather than built here. A run that kept no copy of its spec in the run
    directory resolves it through `state.spec_path`, the second rung of the one
    ladder `_spec_requirement_ids` climbs for every dimension that counts
    requirements — and the digest of a file that is not there never moves, so a
    fingerprint that reached only for `<run>/spec.md` served that run its first
    verdict forever. `spec_path` is keyword-only and has no default because
    "which file is this run's spec" is not a question this hasher may answer by
    omission: `None` is the caller SAYING the run has no resolvable spec.
    """
    spec_bytes = (
        spec_path.read_bytes()
        if spec_path is not None and spec_path.exists()
        else b""
    )
    spec_hash = hashlib.sha256(spec_bytes).hexdigest()[:16]

    shared_fields = {
        # The ownership and span dimensions read the archive schema marker, so
        # a migration that bumps it can turn a passing manifest into a refused
        # one with no byte of the manifest or the spec changed. Absent from the
        # fingerprint, that bump would be served a cached pass forever. It is
        # the one input here that is not IN the document.
        "archive_schema_version": schema_version,
        # EVERY OTHER SHARED FIELD, DERIVED FROM THE DOCUMENT RATHER THAN
        # LISTED. A hand-written list of the shared fields is a list that has
        # to be remembered, and the rule above states exactly what forgetting
        # costs: five fields were named here and `split_reason` was not, so
        # deleting the only recorded reason for a three-way span moved no hash
        # and F0.9 served the pass that reason had bought — the span refusal
        # this file exists to make, cached past the removal of its own
        # exemption. `global_invariants`, `mandatory_rules`, `stream_skips` and
        # `source_inventory` were four more of the same, each read by a
        # dimension below and none of them hashed.
        #
        # `castings` is excluded because it is fingerprinted BETTER below: per
        # entry, beside the prompt file that entry is copied into. Everything
        # else the manifest carries is shared, so the safe direction is to hash
        # it all — a field this misses serves a wrong verdict, a field it need
        # not have hashed costs one re-run.
        "manifest": {k: v for k, v in manifest.items() if k != "castings"},
        # THE RUN DIRECTORY, hashed as the container for the same reason the
        # manifest is: a fact added to `_run_dir_facts` is fingerprinted by
        # construction, where a fact named here would have to be remembered.
        # These two are the inputs no DOCUMENT carries — research appearing
        # turns a clean research-integration dimension into a warning, and the
        # F0.7 matrix going missing turns a passing run into a refusal — so a
        # fingerprint built from documents alone served the verdict given
        # before either had moved.
        "run_dir": run_facts,
        # THE PROJECT TREE, hashed for the reason the run directory is. The
        # surface-ownership dimension asks whether every shipped surface is
        # owned, and a surface appearing after a passing verdict moves no byte
        # of the manifest and no byte of the spec — a new unowned module would
        # be served the pass given before it existed. `surface_facts` carries
        # the declared patterns AND the paths they reached, so both halves of
        # that verdict are keyed: re-declaring the globs invalidates, and so
        # does a file arriving under globs that did not change.
        "surface": surface_facts,
    }
    manifest_hash = hashlib.sha256(
        json.dumps(shared_fields, sort_keys=True).encode("utf-8")
    ).hexdigest()[:16]

    castings_fp: dict[str, dict] = {}
    if _manifest_shape_problem(manifest) is not None:
        return {"spec_hash": spec_hash, "manifest_hash": manifest_hash, "castings": {}}
    for c in manifest.get("castings", []):
        cid = str(c.get("id"))
        prompt_path = fdir / "castings" / f"casting-{cid}-prompt.md"
        prompt_bytes = prompt_path.read_bytes() if prompt_path.exists() else b""
        entry_bytes = json.dumps(c, sort_keys=True).encode("utf-8")
        castings_fp[cid] = {
            "prompt_hash": hashlib.sha256(prompt_bytes).hexdigest()[:16],
            "manifest_entry_hash": hashlib.sha256(entry_bytes).hexdigest()[:16],
        }

    return {"spec_hash": spec_hash, "manifest_hash": manifest_hash, "castings": castings_fp}


def _load_validate_cache(fdir: Path) -> dict:
    cache_path = fdir / ".validate-cache.json"
    if not cache_path.exists():
        return {}
    return read_document(cache_path)[0]


def _save_validate_cache(fdir: Path, cache: dict) -> None:
    try:
        (fdir / ".validate-cache.json").write_text(
            json.dumps(cache, indent=2), encoding="utf-8"
        )
    except OSError:
        pass


def foundry_validate_castings(
    project_root: str = ".",
) -> dict:
    """Validate castings against the spec, one dimension per question asked.

    Every dimension answers in the same shape — ``{"ok": bool, "issues": [...]}``
    plus whatever facts that dimension measured — and adds an entry to the
    top-level ``issues`` list at ``error`` or ``warning`` severity. ``passed``
    fails on errors alone; warnings and informational entries are reported and
    do not block.

    Returns:
        {
            "passed": bool,
            "dimensions": {
                "requirement_coverage": {"ok", "issues", "covered", "total"},
                "casting_completeness": {"ok", "issues"},
                "dependency_correctness": {"ok", "issues"},
                "key_links_planned": {"ok", "issues", "warnings"},
                "scope_sanity": {"ok", "issues"},
                "research_integration": {"ok", "issues"},
                "prompt_fidelity": {"ok", "issues"},
                "migration_coverage": {"ok", "issues", "spec_type", "active"},
                "spec_structure": {"ok", "issues", "errors", "warnings"},
                "file_change_map_coverage": {"ok", "issues", "active", ...},
                "requirement_ownership": {"ok", "issues", "not_computable",
                                          "archive_schema_version",
                                          "schema_floor"},
                "requirement_span": {"ok", "issues", "not_computable",
                                     "threshold", "rows"},
            },
            "issues": [...],
            "revision_hints": [...],
            "requirement_span": {"threshold", "not_computable", "rows", "text"},
            "summary": {...},
        }

    ``requirement_span.rows`` is one record per requirement id —
    ``{"id", "owners", "span", "split_reason"}`` — ordered by id, and
    ``requirement_span.text`` is the same records as a markdown block for a
    surface with no formatter of its own.
    """
    fdir = get_run_dir(project_root)
    if not fdir:
        return {"passed": False, "error": "No active foundry run"}

    if (corrupt := _artifact_guard(fdir)):
        return {"passed": False, **corrupt}

    manifest_path = fdir / "castings" / "manifest.json"
    if not manifest_path.exists():
        return {"passed": False, "error": "No manifest.json found"}

    # D-130's class, found by the package-wide scan. Both reads below were
    # `json.loads(...read_text(...))`, so a corrupt manifest.json or state.json
    # raised out of Foundry-Validate-Castings instead of naming the file. The
    # guard above reports both by name; these loads are now total.
    manifest = _load_json(manifest_path)
    # D-134: the RECORDS, not just the container. `_load_json` guards the
    # document being a dict and `.get("castings", [])` guards the key being
    # present — neither guards what the members inside are, so a manifest whose
    # `castings` is a string reached `c.get(...)` below and raised
    # AttributeError out of Foundry-Validate-Castings. The house refusal names
    # the file and carries `corrupt_artifacts`, the way every other artifact
    # refusal on this surface does.
    if (records := _manifest_shape_problem(manifest)) is not None:
        return {
            "passed": False,
            "error": (
                f"Run artifacts cannot be read: {records}. This tool refuses "
                f"rather than acting on a document it had to guess at."
            ),
            "hint": (
                "Re-run F0.5 DECOMPOSE. The manifest is a JSON object whose "
                "`castings` and `waves` are lists of objects — each casting "
                "carrying an `id`, each wave a `wave` number."
            ),
            "corrupt_artifacts": [records],
        }
    castings = manifest.get("castings", [])
    spec_type = (manifest.get("spec_type") or "GREENFIELD").upper()

    if not castings:
        return {"passed": False, "error": "No castings in manifest"}

    # Short-circuit: if everything is byte-identical to the last passing
    # run and the prior verdict was pass, return cached result instead of
    # re-running all 10 dimensions. Reject→fix→revalidate loops fix only
    # a subset of castings per iteration; when the lead accidentally
    # re-calls Foundry-Validate-Castings without any code change (happens
    # on retry paths), the cache short-circuits to near-zero cost.
    _started_wall = datetime.now(timezone.utc)
    # Read ABOVE the fingerprint, not beside the spec fallback below, because
    # the fingerprint now carries the archive schema marker this document
    # holds. One read, two readers — the spec fallback still uses this `state`.
    state = _load_json(fdir / "state.json")
    schema_version = _archive_schema_version(state)
    # The one ladder, climbed here because the prelude already holds the two
    # documents it takes — the same climb `_spec_requirement_ids` makes below,
    # so the spec the cache is keyed on and the spec the dimensions read are
    # the same file by construction rather than by two agreeing guesses.
    # Read ONCE, here, because the fingerprint below and the two dimensions
    # further down are the same two readers of the same two facts.
    run_facts = _run_dir_facts(fdir)
    # The same kind of fact about the PROJECT tree rather than the run dir, and
    # read here for the same two readers: the fingerprint below and the
    # surface-ownership dimension at the foot of this function. A manifest that
    # declares no `surface_globs` walks no tree, so this costs nothing until a
    # run makes the claim.
    surface_facts = _surface_facts(Path(project_root), manifest)
    fingerprints = _fingerprint_inputs(
        fdir,
        manifest,
        schema_version,
        spec_path=_spec_path_from(project_root, fdir, state),
        run_facts=run_facts,
        surface_facts=surface_facts,
    )
    cache = _load_validate_cache(fdir)
    cached_result = cache.get("last_pass")
    if cached_result and cached_result.get("fingerprints") == fingerprints:
        return {
            **cached_result["result"],
            "cache": {"hit": True, "cached_at": cached_result.get("cached_at")},
        }

    # Load spec to extract requirements, through the one ladder the F6 span
    # section climbs too — see `_spec_requirement_ids`, which carries D-145's
    # tolerance and the reason this read is the one that leaves the run dir.
    spec_text, spec_req_ids, spec_path, spec_problem = _spec_requirement_ids(
        project_root, fdir, state
    )
    if spec_problem is not None:
        return {"passed": False, **document_refusal(spec_path, spec_problem)}

    issues: list[dict] = []
    revision_hints: list[str] = []
    dimensions: dict[str, dict] = {}

    # ── Dimension 1: Requirement Coverage ──
    #: The union of every id some casting's excerpt DECLARES, plus the ids its
    #: observable truths name. Filled by the loop below; read by the coverage
    #: verdict here and by the payload's `covered_requirements` count, and by
    #: nothing else, which is the whole of what "answerable for" is allowed to
    #: decide at F0.9. There is deliberately NO per-casting map of the declared
    #: side beside it: the ownership dimension asks its question of
    #: `cited_by_casting` and of the manifest's persisted `requirement_ids`, so
    #: a second dict keyed by casting would be a derivation with no reader —
    #: which is what it was, until concern C-099 found it (fallout GI-024).
    covered_reqs: set[str] = set()
    #: casting id -> the ids that casting's own excerpt CITES. The same loop,
    #: the other question (fallout D-181), read by the ownership dimension.
    cited_by_casting: dict[str, set[str]] = {}
    for c in castings:
        # D-180: a casting's `spec_text` IS the verbatim `<spec_requirements>`
        # block its prompt carries, so "which requirements does this casting
        # own" must be answered here exactly as the acceptance gate answers it
        # — through the one `declared_requirement_ids` derivation. A bare
        # `findall` over the block credited a casting with any ID quoted inside
        # another requirement's prose, so this dimension could report a
        # requirement COVERED while `foundry_accept_casting` demanded evidence
        # for it from nobody. Two readers of one question, disagreeing with no
        # surface that compares them, is the shape D-180 was filed against.
        #
        # A `spec_text` that is not a string is not a shape
        # `_manifest_shape_problem` judges, and `declared_requirement_ids`
        # splits it into lines — so reading it totally here is what keeps a
        # malformed excerpt a REPORTED gap rather than a traceback across the
        # MCP boundary. This blob is scanned ONCE, here, for both questions the
        # excerpt can answer: the ownership dimension below reads
        # `cited_by_casting` off this same loop rather than opening the blob a
        # second time, and it takes the OWNED side from the manifest's
        # persisted `requirement_ids` through `_ownership_and_computability`,
        # never from this prose at all. One question, one derivation, no
        # surface where two readers of it can disagree with nothing comparing
        # them.
        spec_text_field = c.get("spec_text", "")
        if not isinstance(spec_text_field, str):
            spec_text_field = ""
        casting_reqs = set(declared_requirement_ids(spec_text_field))
        cited_by_casting[str(c.get("id", "?"))] = set(
            cited_requirement_ids(spec_text_field)
        )
        covered_reqs.update(casting_reqs)
        # Also check observable truths text.
        #
        # This one stays a PROSE scan on purpose. An observable truth is a
        # sentence, not a requirement declaration — "...refuses a filing whose
        # tier is absent (AC-006, CT-001, FR-004)" names its requirements
        # mid-line by design — so the position rule that separates a
        # declaration from a quotation has nothing to bite on here.
        for truth in _listed(c, "observable_truths"):
            truth_reqs = set(REQUIREMENT_ID_RE.findall(str(truth)))
            covered_reqs.update(truth_reqs)

    uncovered = spec_req_ids - covered_reqs
    dim1_ok = len(uncovered) == 0
    dim1_issues = []
    if uncovered:
        dim1_issues.append({"type": "uncovered_requirements", "ids": sorted(uncovered)})
        issues.append({"dimension": "requirement_coverage", "severity": "error",
                       "message": f"{len(uncovered)} requirements not in any casting: {', '.join(sorted(uncovered))}"})
        revision_hints.append(f"Add uncovered requirements to appropriate castings: {', '.join(sorted(uncovered))}")
    dimensions["requirement_coverage"] = {"ok": dim1_ok, "issues": dim1_issues,
                                          "covered": len(covered_reqs), "total": len(spec_req_ids)}

    # ── Dimension 2: Casting Completeness ──
    dim2_issues = []
    for c in castings:
        cid = c.get("id", "?")
        title = c.get("title", "Untitled")

        # Check observable truths
        declared_truths = c.get("observable_truths", [])
        if declared_truths and not isinstance(declared_truths, list):
            # fallout GI-004 (C-072): reported, not raised. `len()` on an
            # integer is the raise; a row naming the casting is the refusal.
            dim2_issues.append(
                _shape_issue(cid, title, "observable_truths", declared_truths)
            )
            revision_hints.append(
                f"Casting #{cid} '{title}': observable_truths must be a list of "
                f"strings, not a {type(declared_truths).__name__}"
            )
        else:
            truths = _listed(c, "observable_truths")
            if len(truths) < 3:
                dim2_issues.append({"casting": cid, "issue": f"Only {len(truths)} observable truths (min 3)", "title": title})
                revision_hints.append(f"Casting #{cid} '{title}': add more observable truths (currently {len(truths)}, need 3+)")

        # Check must_haves if present
        declared_must_haves = c.get("must_haves", {})
        if declared_must_haves and not isinstance(declared_must_haves, dict):
            dim2_issues.append(
                _shape_issue(cid, title, "must_haves", declared_must_haves)
            )
            revision_hints.append(
                f"Casting #{cid} '{title}': must_haves must be a mapping with "
                f"truths / artifacts / key_links, not a "
                f"{type(declared_must_haves).__name__}"
            )
        must_haves = _must_haves(c)
        if must_haves:
            # One loop over the three keys rather than three copies of the
            # guard: the emptiness rule and the shape rule are the same rule at
            # two depths, and the messages below are the ones they always were.
            for key in ("truths", "artifacts", "key_links"):
                declared = must_haves.get(key, [])
                if declared and not isinstance(declared, list):
                    dim2_issues.append(
                        _shape_issue(cid, title, f"must_haves.{key}", declared)
                    )
                    revision_hints.append(
                        f"Casting #{cid} '{title}': must_haves.{key} must be a "
                        f"list, not a {type(declared).__name__}"
                    )
                elif len(_listed(must_haves, key)) < 1:
                    dim2_issues.append({"casting": cid, "issue": f"must_haves.{key} is empty", "title": title})

    dim2_ok = len(dim2_issues) == 0
    if dim2_issues:
        issues.append({"dimension": "casting_completeness", "severity": "warning",
                       "message": f"{len(dim2_issues)} completeness issues found"})
    dimensions["casting_completeness"] = {"ok": dim2_ok, "issues": dim2_issues}

    # ── Dimension 3: Dependency Correctness ──
    dim3_issues = []
    file_to_casting: dict[str, list] = {}
    for c in castings:
        cid = c.get("id", "?")
        for f in c.get("key_files", []):
            # A `key_files` entry is unconstrained below the list rung by the
            # shared manifest shape guard, so a non-string reaches here. Keyed
            # by it, two castings carrying the same wrong-typed cell became an
            # overlap on a path that does not exist — a finding invented out of
            # a type error, which is the reading `test_foundry_validate_key_links`
            # rules out one field over.
            if not isinstance(f, str):
                continue
            file_to_casting.setdefault(f, []).append(cid)

    overlaps = {f: cids for f, cids in file_to_casting.items() if len(cids) > 1}

    # fallout FR-009 — D-170's class, on this dimension's own surface.
    #
    # The pass above compares `key_files` as bare strings. That is the whole of
    # the rule while every entry names a file, and none of it the moment one
    # names a DIRECTORY: `tools/orchestration/` and
    # `tools/orchestration/directives.py` are two different strings and the
    # same file, so a manifest could hand one file to two teammates and this
    # dimension — the one whose entire job is "no two castings own the same
    # file" — would call it clean. Two teammates overwriting each other is
    # exactly what the error this dimension raises exists to prevent, so being
    # blind to the directory spelling makes it blind in the direction that
    # costs the most.
    #
    # Asked as coverage rather than equality, through the ONE reading of what a
    # `key_files` entry means. Only a directory entry can reach a string the
    # exact pass did not already pair, so a manifest with no directory entry
    # leaves `overlaps` exactly as the line above built it.
    overlap_via: dict[str, str] = {}
    declared = _declared_key_files(castings)
    for entry, owner in declared:
        if not entry.endswith(KEY_FILE_DIRECTORY_SUFFIX):
            continue
        for covered, other in declared:
            if other == owner or not _key_file_covers(entry, covered):
                continue
            cids = overlaps.setdefault(covered, [other])
            if owner not in cids:
                cids.append(owner)
            overlap_via.setdefault(covered, entry)

    if overlaps:
        for f, cids in overlaps.items():
            record = {"file": f, "castings": cids, "issue": "File claimed by multiple castings"}
            via = overlap_via.get(f)
            if via:
                # The directory is the fact the lead cannot see from the file
                # name: `f` appears in nobody's `key_files` list literally, so
                # a hint naming only `f` sends them looking for a line that is
                # not there.
                record["via_directory"] = via
                revision_hints.append(
                    f"File '{f}' is in castings {cids} — casting "
                    f"{cids[-1]} declares the directory '{via}', which covers "
                    f"it. Narrow the directory entry, or move '{f}' out of it."
                )
            else:
                revision_hints.append(f"File '{f}' is in castings {cids} — move to one casting or split")
            dim3_issues.append(record)
        issues.append({"dimension": "dependency_correctness", "severity": "error",
                       "message": f"{len(overlaps)} file overlaps between castings"})

    dim3_ok = len(dim3_issues) == 0
    dimensions["dependency_correctness"] = {"ok": dim3_ok, "issues": dim3_issues}

    # ── Dimension 4: Key Links Planned ──
    dim4_issues = []
    all_artifacts: set[str] = set()
    all_link_targets: set[str] = set()
    # Expected must_haves shapes (what Decompose should emit):
    #   - artifacts:  [{"path": "src/foo.ts", "min_lines": 20}, ...]
    #   - key_links:  [{"from": "src/a.ts", "to": "src/b.ts"}, ...]
    #                 (each link names the two endpoints — the "from" source
    #                  and the "to" target — of one wiring connection).
    # Decompose occasionally emits a plain STRING instead (a free-text link or
    # artifact description like "LoginForm -> /api/login"). A string has no
    # .get(), so the reads below would raise
    #   AttributeError: 'str' object has no attribute 'get'
    # and the entire 10-dimension report would fail to render at F0.9. Guard
    # each read with isinstance(...): a dict entry behaves EXACTLY as before,
    # while a string entry is accepted as a plain description and recorded as a
    # NON-BLOCKING warning (severity "warning", kept out of dim4_ok) that names
    # the casting and the offending entry.
    for c in castings:
        cid = c.get("id", "?")
        title = c.get("title", "Untitled")
        must_haves = _must_haves(c)
        for art in _listed(must_haves, "artifacts"):
            if isinstance(art, dict):
                all_artifacts.add(art.get("path", ""))
            else:
                all_artifacts.add(str(art))
                dim4_issues.append({
                    "casting": cid, "title": title, "severity": "warning",
                    "issue": (
                        f"Casting #{cid} '{title}': must_haves.artifacts entry is a "
                        f"string, not a {{path: ...}} object ({art!r}) — treated as a "
                        f"plain artifact description"
                    ),
                })
        for link in _listed(must_haves, "key_links"):
            if isinstance(link, dict):
                all_link_targets.add(link.get("from", ""))
                all_link_targets.add(link.get("to", ""))
            else:
                dim4_issues.append({
                    "casting": cid, "title": title, "severity": "warning",
                    "issue": (
                        f"Casting #{cid} '{title}': must_haves.key_links entry is a "
                        f"string, not a {{from, to}} object ({link!r}) — treated as a "
                        f"plain link description"
                    ),
                })

    # Check if any casting has artifacts but no key_links (isolated)
    for c in castings:
        cid = c.get("id", "?")
        title = c.get("title", "Untitled")
        must_haves = _must_haves(c)
        artifacts = _listed(must_haves, "artifacts")
        links = _listed(must_haves, "key_links")
        if len(artifacts) >= 2 and len(links) == 0:
            dim4_issues.append({"casting": cid, "title": title,
                               "issue": f"Has {len(artifacts)} artifacts but no key_links — isolated"})
            revision_hints.append(f"Casting #{cid} '{title}': add key_links showing how artifacts connect")

    # String-entry warnings are non-blocking: exclude them from dim4_ok and
    # surface them as a warning-severity entry in the top-level issues list,
    # which the overall `passed` computation ignores (it fails only on
    # severity=="error"). Non-warning issues (isolated castings) keep their
    # prior weight, so dict-entry behavior is unchanged.
    dim4_warnings = [i for i in dim4_issues if i.get("severity") == "warning"]
    dim4_errors = [i for i in dim4_issues if i.get("severity") != "warning"]
    dim4_ok = len(dim4_errors) == 0
    if dim4_warnings:
        issues.append({
            "dimension": "key_links_planned",
            "severity": "warning",
            "message": (
                f"{len(dim4_warnings)} must_haves key_links/artifacts entr"
                f"{'y is a string' if len(dim4_warnings) == 1 else 'ies are strings'}, "
                f"not object(s) — treated as plain description(s) (non-blocking)"
            ),
        })
    dimensions["key_links_planned"] = {
        "ok": dim4_ok, "issues": dim4_issues, "warnings": len(dim4_warnings),
    }

    # ── Dimension 5: Scope Sanity ──
    dim5_issues = []
    for c in castings:
        cid = c.get("id", "?")
        title = c.get("title", "Untitled")
        kf = len(c.get("key_files", []))
        if kf > 8:
            dim5_issues.append({"casting": cid, "title": title, "key_files": kf,
                               "issue": f"Too many key_files ({kf} > 8)"})
            revision_hints.append(f"Casting #{cid} '{title}': split into smaller castings (currently {kf} files)")

        # Check observable truths are user-facing
        truths = _listed(c, "observable_truths")
        impl_detail_patterns = [
            r"import\b", r"export\b", r"function\b", r"class\b",
            r"instanceof", r"typeof", r"\.ts\b", r"\.js\b",
        ]
        non_user_facing = []
        for truth in truths:
            for pattern in impl_detail_patterns:
                if re.search(pattern, str(truth), re.IGNORECASE):
                    non_user_facing.append(truth)
                    break
        if non_user_facing:
            dim5_issues.append({"casting": cid, "title": title,
                               "issue": f"{len(non_user_facing)} truths look like implementation details, not user-facing behaviors",
                               "examples": non_user_facing[:3]})

    dim5_ok = len(dim5_issues) == 0
    dimensions["scope_sanity"] = {"ok": dim5_ok, "issues": dim5_issues}

    # ── Dimension 6: Research Integration ──
    dim6_issues = []
    if run_facts["has_research"]:
        castings_with_research = sum(1 for c in castings if c.get("research_context"))
        if castings_with_research == 0:
            dim6_issues.append({"issue": "Research artifacts exist but no casting references them"})
            revision_hints.append("Research was conducted but no casting has research_context — link relevant findings")
            issues.append({"dimension": "research_integration", "severity": "warning",
                          "message": "Research exists but no casting references it"})

    dim6_ok = len(dim6_issues) == 0
    dimensions["research_integration"] = {"ok": dim6_ok, "issues": dim6_issues}

    # ── Dimension 7: Prompt Fidelity ──
    #
    # Every casting must have a pre-authored teammate prompt file at
    # `castings/casting-{id}-prompt.md`. The prompt MUST contain the spec
    # requirements for this casting as a literal substring of the master
    # spec.md — no paraphrasing allowed. Plans are prompts: authored once
    # from the spec, handed directly to teammates without lead re-translation.
    #
    # Sub-check 7e: every prompt must also contain a <global_invariants> block
    # whose content is byte-identical across all castings AND matches
    # manifest.global_invariants verbatim AND is a verbatim substring of
    # spec.md. This propagates cross-cutting rules (auth, validation, naming,
    # security) to every teammate without relying on decompose's judgment
    # about which casting "needs" which rule.
    dim7_issues = []
    castings_dir = fdir / "castings"
    normalized_spec = _normalize(spec_text)
    manifest_invariants = manifest.get("global_invariants", "") or ""
    normalized_manifest_invariants = _normalize(manifest_invariants)
    manifest_rules = manifest.get("mandatory_rules", "") or ""
    normalized_manifest_rules = _normalize(manifest_rules)
    # Track per-casting invariant hashes so we can verify byte-identical
    # propagation across every casting prompt.
    import hashlib as _hashlib
    invariant_hashes: dict = {}  # casting_id -> sha256 of normalized block
    rules_hashes: dict = {}  # casting_id -> sha256 of normalized mandatory_rules block

    for c in castings:
        cid = c.get("id", "?")
        title = c.get("title", "Untitled")
        prompt_path = castings_dir / f"casting-{cid}-prompt.md"

        # 7a: the prompt file must exist
        if not prompt_path.exists():
            dim7_issues.append({
                "casting": cid,
                "title": title,
                "issue": "missing_prompt_file",
                "detail": f"casting-{cid}-prompt.md does not exist",
            })
            revision_hints.append(
                f"Casting #{cid} '{title}': decompose must write castings/casting-{cid}-prompt.md. "
                f"Re-run F0.5 DECOMPOSE."
            )
            continue

        # D-145's second unguarded read in this module. It is covered TODAY
        # only because a casting prompt happens to live inside the run dir,
        # where the artifact guard's rglob reaches it — which is a fact about
        # where the file sits, not a property of this reader. Made total, so
        # the reader holds on its own merits wherever the path resolves.
        prompt_text, prompt_problem = read_text_file(prompt_path)
        if prompt_problem is not None:
            return {"passed": False, **document_refusal(prompt_path, prompt_problem)}

        if not prompt_text.strip():
            dim7_issues.append({
                "casting": cid,
                "title": title,
                "issue": "empty_prompt_file",
                "detail": f"casting-{cid}-prompt.md is empty",
            })
            continue

        # 7b: the prompt must contain a <spec_requirements> block
        spec_block = _extract_spec_block(prompt_text)
        if spec_block is None:
            dim7_issues.append({
                "casting": cid,
                "title": title,
                "issue": "missing_spec_block",
                "detail": (
                    f"casting-{cid}-prompt.md has no <spec_requirements>...</spec_requirements> "
                    f"section. The spec requirements must be included verbatim in that block."
                ),
            })
            revision_hints.append(
                f"Casting #{cid} '{title}': add a <spec_requirements> block containing "
                f"the verbatim spec text for this casting's ACs."
            )
            continue

        # 7c: every non-trivial line in the spec block must appear verbatim in spec.md
        if not normalized_spec:
            dim7_issues.append({
                "casting": cid,
                "title": title,
                "issue": "spec_unreadable",
                "detail": "spec.md could not be read; cannot verify substring integrity",
            })
            continue

        drift_lines = _find_drift(spec_block, normalized_spec)
        if drift_lines:
            dim7_issues.append({
                "casting": cid,
                "title": title,
                "issue": "spec_drift_detected",
                "detail": (
                    f"{len(drift_lines)} line(s) in the prompt's <spec_requirements> block do not "
                    f"appear verbatim in spec.md"
                ),
                "examples": drift_lines[:3],
            })
            revision_hints.append(
                f"Casting #{cid} '{title}': the <spec_requirements> block must be a literal copy-paste "
                f"from spec.md. Paraphrasing and summarizing are forbidden. Re-run F0.5 DECOMPOSE and "
                f"copy spec text character-for-character."
            )

        # 7d: forbidden scope-cutting phrases.
        # teammate.md lives in foundry:teammate's system prompt (not inlined
        # into casting-{id}-prompt.md), so the file contains only decompose-
        # authored content — mandatory_rules block, global_invariants block,
        # spec_requirements block, metadata, classification. All of that
        # should be scanned for scope-cutting language.
        forbidden_found = _find_forbidden_phrases(prompt_text)
        if forbidden_found:
            dim7_issues.append({
                "casting": cid,
                "title": title,
                "issue": "forbidden_scope_phrase",
                "detail": f"prompt contains scope-cutting language",
                "phrases": forbidden_found,
            })
            revision_hints.append(
                f"Casting #{cid} '{title}': remove forbidden phrases from the prompt "
                f"({', '.join(repr(p) for p in forbidden_found[:3])}). These phrases silently "
                f"authorize scope cuts and are banned from teammate prompts."
            )

        # 7e: global_invariants block propagation.
        # Required unconditionally: every prompt must contain the block so
        # F0.9 can verify uniform propagation. If manifest_invariants is
        # empty, an empty block is still required (the block's presence is
        # what enables mechanical verification across castings).
        invariant_block = _extract_invariants_block(prompt_text)
        if invariant_block is None:
            dim7_issues.append({
                "casting": cid,
                "title": title,
                "issue": "missing_global_invariants_block",
                "detail": (
                    f"casting-{cid}-prompt.md has no <global_invariants>...</global_invariants> "
                    f"section. Every casting prompt must contain this block so cross-cutting "
                    f"rules propagate uniformly to every teammate."
                ),
            })
            revision_hints.append(
                f"Casting #{cid} '{title}': add a <global_invariants> block containing "
                f"manifest.global_invariants verbatim. If manifest.global_invariants is empty, "
                f"the block should still exist but be empty."
            )
        else:
            normalized_block = _normalize(invariant_block)
            # Hash the normalized block so we can detect drift across castings.
            h = _hashlib.sha256(normalized_block.encode("utf-8")).hexdigest()[:16]
            invariant_hashes[cid] = h

            # 7e.1: block content must match manifest.global_invariants.
            # Dim 9 separately verifies manifest↔spec fidelity, so chaining
            # 7e.1 with Dim 9 gives us transitive spec↔casting fidelity.
            if normalized_block != normalized_manifest_invariants:
                dim7_issues.append({
                    "casting": cid,
                    "title": title,
                    "issue": "global_invariants_drift_from_manifest",
                    "detail": (
                        f"casting-{cid}-prompt.md's <global_invariants> block does not match "
                        f"manifest.global_invariants verbatim (after normalization). Decompose "
                        f"must paste the manifest's invariants character-for-character."
                    ),
                })
                revision_hints.append(
                    f"Casting #{cid} '{title}': re-copy manifest.global_invariants into the "
                    f"<global_invariants> block. Never paraphrase or summarize cross-cutting rules."
                )

        # 7g: mandatory_rules block propagation.
        # Every prompt must contain a <mandatory_rules> block — the CLAUDE.md /
        # AGENTS.md / .cursorrules imperatives propagated identically to every
        # casting. Same mechanics as 7e: byte-identical across castings, verbatim
        # from manifest.mandatory_rules. Presence required regardless of whether
        # the project has a CLAUDE.md (empty block is valid; absent block is not).
        rules_block = _extract_mandatory_rules_block(prompt_text)
        if rules_block is None:
            dim7_issues.append({
                "casting": cid,
                "title": title,
                "issue": "missing_mandatory_rules_block",
                "detail": (
                    f"casting-{cid}-prompt.md has no <mandatory_rules>...</mandatory_rules> "
                    f"section. Every casting prompt must contain this block so CLAUDE.md "
                    f"imperatives propagate uniformly to every teammate."
                ),
            })
            revision_hints.append(
                f"Casting #{cid} '{title}': add a <mandatory_rules> block containing "
                f"manifest.mandatory_rules verbatim. If the project has no CLAUDE.md, "
                f"the block should still exist but be empty."
            )
        else:
            normalized_rules_block = _normalize(rules_block)
            rules_h = _hashlib.sha256(normalized_rules_block.encode("utf-8")).hexdigest()[:16]
            rules_hashes[cid] = rules_h
            if normalized_rules_block != normalized_manifest_rules:
                dim7_issues.append({
                    "casting": cid,
                    "title": title,
                    "issue": "mandatory_rules_drift_from_manifest",
                    "detail": (
                        f"casting-{cid}-prompt.md's <mandatory_rules> block does not match "
                        f"manifest.mandatory_rules verbatim (after normalization). Decompose "
                        f"must paste the manifest's rules character-for-character."
                    ),
                })
                revision_hints.append(
                    f"Casting #{cid} '{title}': re-copy manifest.mandatory_rules into the "
                    f"<mandatory_rules> block. Never paraphrase or filter CLAUDE.md rules."
                )

    # 7e.3: block content must be byte-identical across EVERY casting.
    # Run this check after the per-casting loop so we have all hashes.
    if len(set(invariant_hashes.values())) > 1:
        hash_to_castings: dict = {}
        for cid, h in invariant_hashes.items():
            hash_to_castings.setdefault(h, []).append(cid)
        dim7_issues.append({
            "issue": "global_invariants_inconsistent_across_castings",
            "detail": (
                f"Different castings have different <global_invariants> blocks. "
                f"Every casting must contain byte-identical invariants."
            ),
            "groups": {h: cids for h, cids in hash_to_castings.items()},
        })
        revision_hints.append(
            "Different castings have different <global_invariants> content. Re-run decompose "
            "and propagate manifest.global_invariants byte-identical to every casting prompt."
        )

    # 7g.3: mandatory_rules must be byte-identical across every casting too.
    if len(set(rules_hashes.values())) > 1:
        hash_to_castings_r: dict = {}
        for cid, h in rules_hashes.items():
            hash_to_castings_r.setdefault(h, []).append(cid)
        dim7_issues.append({
            "issue": "mandatory_rules_inconsistent_across_castings",
            "detail": (
                f"Different castings have different <mandatory_rules> blocks. "
                f"Every casting must contain byte-identical CLAUDE.md rules."
            ),
            "groups": {h: cids for h, cids in hash_to_castings_r.items()},
        })
        revision_hints.append(
            "Different castings have different <mandatory_rules> content. Re-run decompose "
            "and propagate manifest.mandatory_rules byte-identical to every casting prompt."
        )

    # Sub-check 7m (Phase 8 / INTENT-01): intent-coverage.json present when
    # INTENT-01 not stream-skipped. Mirror of sub-check 7k's stream_skips
    # re-derivation discipline applied at the file-presence + manifest-summary
    # level. By-reference to 7k's roster derivation: if INTENT-01 is NOT in
    # manifest.stream_skips (i.e., the F0.5 step 2b roster routed it as an
    # active stream on a v2.1+ spec), F0.7 must have produced the matrix —
    # absence is itself a defect that fires INTENT_COVERAGE_RECORD_INCOMPLETE.
    # Defense-in-depth: 7k validates the roster derivation; 7m validates that
    # the active streams actually emitted their artifacts.
    intent_in_skips = any(
        (s.get("stream_id") if isinstance(s, dict) else None) == "INTENT-01"
        for s in manifest.get("stream_skips", []) or []
    )
    if not intent_in_skips:
        intent_coverage_path = fdir / _INTENT_COVERAGE_BASENAME
        if not run_facts["has_intent_coverage"]:
            dim7_issues.append({
                "issue": "intent_coverage_record_incomplete",
                "detail": (
                    f"INTENT_COVERAGE_RECORD_INCOMPLETE: intent-coverage.json missing at "
                    f"{intent_coverage_path}; INTENT-01 not in manifest.stream_skips so "
                    f"the F0.7 stream should have produced the matrix"
                ),
            })
            revision_hints.append(
                "INTENT_COVERAGE_RECORD_INCOMPLETE: re-run F0.7 INTENT-CARRIER to produce "
                "intent-coverage.json. INTENT-01 is an active stream on this spec_format_version."
            )
        elif "intent_coverage_summary" not in manifest:
            dim7_issues.append({
                "issue": "intent_coverage_record_incomplete",
                "detail": (
                    "INTENT_COVERAGE_RECORD_INCOMPLETE: intent-coverage.json present but "
                    "manifest.intent_coverage_summary missing; F0.7 marker stamping incomplete"
                ),
            })
            revision_hints.append(
                "INTENT_COVERAGE_RECORD_INCOMPLETE: re-run Foundry-Intent-Coverage to stamp "
                ".f07-intent-clean marker and append manifest.intent_coverage_summary."
            )
        else:
            stale = _intent_marker_staleness(fdir, intent_coverage_path)
            if stale is not None:
                dim7_issues.append({
                    "issue": "intent_coverage_stale",
                    "detail": f"{INTENT_COVERAGE_STALE}: {stale}",
                })
                revision_hints.append(
                    f"{INTENT_COVERAGE_STALE}: re-run Foundry-Intent-Coverage. The "
                    "matrix on disk is not the matrix F0.7 passed on, so the standing "
                    "verdict answers for a document that has since changed."
                )

    dim7_ok = len(dim7_issues) == 0
    if not dim7_ok:
        issues.append({
            "dimension": "prompt_fidelity",
            "severity": "error",
            "message": f"{len(dim7_issues)} prompt fidelity issue(s) detected",
        })
    dimensions["prompt_fidelity"] = {"ok": dim7_ok, "issues": dim7_issues}

    # ── Dimension 8: Migration Coverage ──
    #
    # Only runs when spec_type is MIGRATION. For migration specs, every
    # casting MUST declare a coverage_list under must_haves enumerating
    # the source_file:symbol entries it is responsible for porting. Every
    # source symbol must be assigned to exactly one casting (no duplicates,
    # no gaps). This enforces the "full 1:1 coverage" invariant that
    # unambiguous migration specs require.
    dim8_issues = []
    if spec_type == "MIGRATION":
        per_casting_coverage: dict[int, list] = {}
        all_source_entries: dict[str, list] = {}  # entry -> [casting_ids]

        for c in castings:
            cid = c.get("id", "?")
            title = c.get("title", "Untitled")
            mh = _must_haves(c)
            cov = mh.get("coverage_list", [])

            if not isinstance(cov, list) or not cov:
                dim8_issues.append({
                    "casting": cid,
                    "title": title,
                    "issue": "missing_coverage_list",
                    "detail": (
                        f"spec_type is MIGRATION but casting #{cid} has no "
                        f"must_haves.coverage_list. Migration specs require every "
                        f"casting to enumerate the source_file:symbol entries it ports."
                    ),
                })
                revision_hints.append(
                    f"Casting #{cid} '{title}': add a coverage_list array under must_haves "
                    f"with every source_file:symbol this casting must port (1:1)."
                )
                continue

            per_casting_coverage[cid] = cov
            for entry in cov:
                if not isinstance(entry, str):
                    dim8_issues.append({
                        "casting": cid,
                        "title": title,
                        "issue": "invalid_coverage_entry",
                        "detail": f"coverage_list must contain strings like 'path/to/file.go:TestSymbolName', got {entry!r}",
                    })
                    continue
                all_source_entries.setdefault(entry, []).append(cid)

        # Detect duplicates — same source entry claimed by multiple castings
        dupes = {e: cids for e, cids in all_source_entries.items() if len(cids) > 1}
        for entry, cids in dupes.items():
            dim8_issues.append({
                "issue": "duplicate_coverage_entry",
                "entry": entry,
                "castings": cids,
                "detail": f"source entry '{entry}' is claimed by castings {cids}; assign to exactly one",
            })
            revision_hints.append(
                f"Source entry '{entry}' is in multiple castings ({cids}) — assign to one casting only."
            )

        # Completeness: if the spec declares a source_inventory, verify every
        # inventory entry appears in some casting's coverage_list. The Forge
        # migration mode writes this inventory to the manifest under
        # `source_inventory` after R2 INTERVIEW.
        source_inventory = manifest.get("source_inventory", [])
        if source_inventory:
            claimed = set(all_source_entries.keys())
            missing = [e for e in source_inventory if e not in claimed]
            for entry in missing:
                dim8_issues.append({
                    "issue": "uncovered_source_entry",
                    "entry": entry,
                    "detail": f"source inventory entry '{entry}' is not covered by any casting",
                })
            if missing:
                revision_hints.append(
                    f"{len(missing)} source inventory entries are not in any casting's "
                    f"coverage_list. First 3: {missing[:3]}. Either assign them or justify omission "
                    f"in the spec."
                )

    dim8_ok = len(dim8_issues) == 0
    if not dim8_ok:
        issues.append({
            "dimension": "migration_coverage",
            "severity": "error",
            "message": f"{len(dim8_issues)} migration coverage issue(s) detected",
        })
    dimensions["migration_coverage"] = {
        "ok": dim8_ok,
        "issues": dim8_issues,
        "spec_type": spec_type,
        "active": spec_type == "MIGRATION",
    }

    # ── Dimension 9: Spec Structure ──
    #
    # Validates the master spec.md has the minimum structure foundry needs
    # to prevent drift:
    #   9a (error): spec contains at least one tagged requirement ID
    #       (US-N, FR-N, NFR-N, AC-N, VC-N, IR-N, TR-N). Without IDs,
    #       Dimension 1 coverage tracking is impossible and Phase 3
    #       citation checks cannot be enforced.
    #   9b (warning): spec has a `## Global Invariants` section (or
    #       <global_invariants> block). Missing → decompose has nothing
    #       to propagate, so cross-cutting rules must be embedded per
    #       casting which reintroduces tunnel-vision drift. Warning-only
    #       to allow gradual adoption on existing specs.
    #
    # 9a also confirms that manifest.global_invariants, if present, was
    # populated from the spec's section — catches decompose inventing
    # invariants. (The per-casting verbatim check in 7e already handles
    # this, but we surface it here too for clearer diagnostics.)
    dim9_issues = []

    if not spec_text:
        dim9_issues.append({
            "severity": "error",
            "issue": "spec_unreadable",
            "detail": "spec.md could not be read — cannot validate spec structure",
        })
    else:
        # 9a: requirement IDs
        if not spec_req_ids:
            dim9_issues.append({
                "severity": "error",
                "issue": "no_tagged_requirements",
                "detail": (
                    "spec.md contains no tagged requirement IDs. Foundry requires "
                    "every requirement to be tagged with an ID like US-1, FR-2, "
                    "NFR-3, AC-4, VC-5, IR-6, or TR-7 so coverage and citations "
                    "can be tracked mechanically. Add IDs to every requirement, "
                    "or re-generate the spec via Forge/Lisa."
                ),
            })
            revision_hints.append(
                "Add tagged requirement IDs to spec.md (US-N, FR-N, NFR-N, AC-N, etc.). "
                "Without IDs, Foundry cannot track coverage or enforce citations."
            )

        # 9b: global invariants section (warning-only for backward compat)
        spec_invariants = _extract_spec_invariants_section(spec_text)
        if not spec_invariants:
            dim9_issues.append({
                "severity": "warning",
                "issue": "no_global_invariants_section",
                "detail": (
                    "spec.md has no '## Global Invariants' section. Cross-cutting "
                    "rules (auth, validation, naming, error handling, security) "
                    "must be propagated to every casting to prevent tunnel-vision "
                    "drift. Add a '## Global Invariants' section listing rules "
                    "that apply to every casting regardless of its slice."
                ),
            })
            revision_hints.append(
                "Add a `## Global Invariants` section to spec.md listing cross-cutting "
                "rules that apply to every casting (auth, validation, naming, error "
                "handling). These will be propagated verbatim to every teammate prompt."
            )
        else:
            # If the spec HAS invariants, manifest.global_invariants must
            # match them verbatim. Surface the check here too for clearer
            # diagnostics than Dimension 7's per-casting drift messages.
            if not manifest_invariants:
                dim9_issues.append({
                    "severity": "error",
                    "issue": "manifest_invariants_missing",
                    "detail": (
                        "spec.md declares a '## Global Invariants' section but "
                        "manifest.global_invariants is empty. Decompose must copy "
                        "the section verbatim into the manifest."
                    ),
                })
                revision_hints.append(
                    "Copy spec.md's `## Global Invariants` section verbatim into "
                    "manifest.global_invariants (top-level field)."
                )
            elif _normalize(spec_invariants) != normalized_manifest_invariants:
                dim9_issues.append({
                    "severity": "error",
                    "issue": "manifest_invariants_drift",
                    "detail": (
                        "manifest.global_invariants does not match spec.md's "
                        "'## Global Invariants' section verbatim (after normalization). "
                        "Decompose must paste it character-for-character."
                    ),
                })
                revision_hints.append(
                    "Re-copy spec.md's `## Global Invariants` section into "
                    "manifest.global_invariants. Never paraphrase cross-cutting rules."
                )

    dim9_errors = [i for i in dim9_issues if i.get("severity") == "error"]
    dim9_warnings = [i for i in dim9_issues if i.get("severity") == "warning"]
    dim9_ok = len(dim9_errors) == 0
    if dim9_errors:
        issues.append({
            "dimension": "spec_structure",
            "severity": "error",
            "message": f"{len(dim9_errors)} spec structure error(s) detected",
        })
    if dim9_warnings:
        issues.append({
            "dimension": "spec_structure",
            "severity": "warning",
            "message": f"{len(dim9_warnings)} spec structure warning(s): add `## Global Invariants` section to prevent cross-cutting drift",
        })
    dimensions["spec_structure"] = {
        "ok": dim9_ok,
        "issues": dim9_issues,
        "errors": len(dim9_errors),
        "warnings": len(dim9_warnings),
    }

    # ── Dimension 10: File Change Map ↔ key_files cross-check ──
    #
    # The spec's `## File Change Map` section enumerates every file that
    # should be modified or created. Every casting declares its `key_files`
    # boundary — the files the teammate is allowed to touch. These two MUST
    # cross-check:
    #
    #   - Every file in the File Change Map MUST appear in exactly one
    #     casting's key_files (else the change is unimplementable — no
    #     teammate can reach it). ERROR.
    #   - Files in some casting's key_files but NOT in the File Change Map
    #     are flagged as scope creep (warning) — the teammate has access to
    #     a file the spec didn't authorize them to change.
    #
    # The check is skipped if the spec has no File Change Map section (older
    # specs or pure-doc specs). Current Forge templates always emit one.
    dim10_issues = []
    map_files = _extract_file_change_map_files(spec_text)

    if not map_files:
        dimensions["file_change_map_coverage"] = {
            "ok": True,
            "issues": [],
            "active": False,
            "reason": "spec has no File Change Map section (or none parseable)",
        }
    else:
        # Build {file: [casting_ids]} from key_files (already normalized
        # via _normalize_file_path so map_files and key_files compare
        # apples-to-apples).
        casting_files: dict[str, list] = {}
        for normalized, cid in _declared_key_files(castings):
            casting_files.setdefault(normalized, []).append(cid)

        # fallout FR-009 — D-170's class again, and here it points BOTH ways.
        # The File Change Map names files; a casting may name the directory
        # they sit in. Compared as bare strings, every file under a directory
        # entry reads as an orphan no teammate can reach (10a, an ERROR that
        # would refuse a manifest whose slicing is fine) and the directory
        # entry itself reads as scope creep (10b). Coverage is the question,
        # and for an entry naming a file it is the same question equality was,
        # so a manifest with no directory entry is answered exactly as before.
        covered_by: dict[str, list] = {}
        for path in map_files:
            for entry, cids in casting_files.items():
                if _key_file_covers(entry, path):
                    covered_by.setdefault(path, []).extend(cids)

        # Check 10a: every File Change Map entry is reached by some casting
        unimplementable = sorted(map_files - set(covered_by))
        for path in unimplementable:
            dim10_issues.append({
                "severity": "error",
                "issue": "file_change_map_orphan",
                "file": path,
                "detail": (
                    f"spec.md File Change Map declares '{path}' must change, "
                    f"but no casting has it in key_files. No teammate will "
                    f"reach this file — the change is unimplementable as "
                    f"sliced. Either add '{path}' to a casting's key_files, "
                    f"or remove it from the File Change Map if it shouldn't "
                    f"actually change."
                ),
            })
        if unimplementable:
            issues.append({
                "dimension": "file_change_map_coverage",
                "severity": "error",
                "message": (
                    f"{len(unimplementable)} file(s) in spec's File Change "
                    f"Map are not in any casting's key_files (unimplementable)"
                ),
            })
            revision_hints.append(
                f"Decompose missed {len(unimplementable)} files from the "
                f"File Change Map. Either widen a casting's key_files to "
                f"include them, add a new casting that owns them, or remove "
                f"them from the File Change Map. First 5: "
                f"{unimplementable[:5]}"
            )

        # Check 10b: scope creep — castings have files not in the map
        # (warning, not error — sometimes castings legitimately touch
        # adjacent files like test fixtures or import sites)
        scope_creep = sorted(
            entry
            for entry in casting_files
            if not any(_key_file_covers(entry, path) for path in map_files)
        )
        for path in scope_creep[:20]:  # cap to avoid noise
            cids = casting_files[path]
            dim10_issues.append({
                "severity": "warning",
                "issue": "file_change_map_scope_creep",
                "file": path,
                "castings": cids,
                "detail": (
                    f"casting(s) {cids} declare '{path}' in key_files but "
                    f"the spec's File Change Map does not list it. Either "
                    f"the spec is incomplete (add the file to the map and "
                    f"explain why it changes) or the casting is overreaching "
                    f"(remove from key_files)."
                ),
            })
        if scope_creep:
            issues.append({
                "dimension": "file_change_map_coverage",
                "severity": "warning",
                "message": (
                    f"{len(scope_creep)} file(s) are in casting key_files "
                    f"but not in spec's File Change Map (potential scope creep)"
                ),
            })

        dim10_errors = [i for i in dim10_issues if i.get("severity") == "error"]
        dimensions["file_change_map_coverage"] = {
            "ok": len(dim10_errors) == 0,
            "issues": dim10_issues,
            "active": True,
            "map_files": len(map_files),
            "covered": len(map_files - set(covered_by)),
            "scope_creep": len(scope_creep),
        }

    # ── Dimension 11: Requirement Ownership ──
    #
    # `forge-specs/foundry-run-fallout/spec.md` FR-009, FR-040, AC-001, OT-001,
    # GI-012, CT-011. "Persist `requirement_ids` per casting at F0.5, validated
    # at F0.9."
    #
    # WHY A PERSISTED FIELD AND NOT THE PROSE. Ownership used to live only
    # inside each casting's `spec_text` blob, so every consumer that needed to
    # know which casting answers for an id re-derived it from prose at the
    # moment it asked — the acceptance gate at one time, the co-dispatch set at
    # another, this validator at a third. A persisted list is a claim the
    # manifest makes once, and this dimension is what makes the claim
    # answerable: it must agree, in BOTH directions, with what the casting's
    # own excerpt declares.
    #
    #   cited but not owned — the excerpt names a requirement its ownership
    #       list omits. Nobody is answerable for it: the acceptance gate will
    #       not demand evidence for it and the co-dispatch set will not route a
    #       fix to this casting.
    #   owned but not cited — the ownership list claims a requirement the
    #       excerpt never names. The teammate is handed no text for it and
    #       cannot build it, while the manifest reports it covered.
    #
    # CITES, NOT DECLARES — AND THEY ARE DIFFERENT QUESTIONS (fallout D-181).
    # All three sources use the same verb: AC-001 "cites an id in `spec_text`
    # that is absent from its `requirement_ids`", OT-001 "whose prose CITES an
    # id outside that list", FR-040 the same word in both directions. This
    # dimension read `declared_requirement_ids` instead — the SUBJECT-POSITION
    # reading the acceptance gate needs — so an id named mid-line, mid-prose or
    # inside backticks was invisible here and a casting citing an id it did not
    # own passed F0.9 clean, which is the state AC-001 says is refused.
    #
    # The fix is a second derivation, never a wider first one. Widening
    # `declared_requirement_ids` is D-180 verbatim: the acceptance gate would
    # demand evidence for a requirement another casting owns and leave the
    # teammate no exit but a false `# evidence-for:` header. So
    # `cited_requirement_ids` answers "what does this excerpt MENTION" for this
    # dimension, `declared_requirement_ids` goes on answering "what is this
    # casting ANSWERABLE for" for the gate and for the coverage verdict above,
    # and neither reader has to compromise for the other.
    #
    # BOTH DIRECTIONS READ THE SAME POPULATION, and that is not tidiness. A
    # forward check on cites with a reverse check on declarations leaves a
    # cited-but-undeclared id with NO accepting state: owning it trips
    # `owned_but_not_cited`, disowning it trips `cited_but_not_owned`, and the
    # lead loops between two refusals forever. The harm the reverse direction
    # guards — a teammate handed no text — is still caught, one dimension over:
    # `covered_reqs` above is built from DECLARATIONS, so a requirement no
    # casting declares is still reported uncovered by dimension 1.
    dim11_issues: list[dict] = []
    # ONE DERIVATION, shared with the F6 span section — see
    # `_ownership_and_computability`, which carries the FR-054 rule about which
    # archives the ownership checks are knowable for. This gate has already
    # refused an empty manifest above, so its `bool(castings)` rung is always
    # true here and the answer is the one this dimension always gave.
    ownership, ownership_not_computable = _ownership_and_computability(
        castings, schema_version
    )
    if ownership_not_computable:
        dim11_issues.append({
            "severity": "info",
            "issue": "requirement_ids_not_computable",
            "detail": (
                f"No casting in this manifest carries `requirement_ids`, and "
                f"the run's archive_schema_version is {schema_version}, below "
                f"the {REQUIREMENT_IDS_SCHEMA_FLOOR} at which the field became "
                f"mandatory. Ownership consistency and requirement span are "
                f"not computable for this archive and are not checked. Run "
                f"scripts/migrate-archive.py to fill the field."
            ),
        })
    else:
        for c in castings:
            cid = c.get("id", "?")
            title = c.get("title", "Untitled")
            present, owned = ownership[str(cid)]
            cited = cited_by_casting.get(str(cid), set())
            if not present:
                dim11_issues.append({
                    "severity": "error",
                    "casting": cid,
                    "title": title,
                    "issue": "missing_requirement_ids",
                    "detail": (
                        f"Casting #{cid} '{title}' has no `requirement_ids`. "
                        f"This run is on archive schema {schema_version}, at or "
                        f"above the {REQUIREMENT_IDS_SCHEMA_FLOOR} that makes "
                        f"the field mandatory, so ownership cannot be left to "
                        f"the prose."
                    ),
                })
                revision_hints.append(
                    f"Casting #{cid} '{title}': add a `requirement_ids` list to the "
                    f"manifest entry naming every requirement id the casting's "
                    f"<spec_requirements> block cites."
                )
                continue
            unowned = sorted(cited - owned)
            if unowned:
                dim11_issues.append({
                    "severity": "error",
                    "casting": cid,
                    "title": title,
                    "issue": "cited_but_not_owned",
                    "ids": unowned,
                    "detail": (
                        f"Casting #{cid} '{title}' cites {', '.join(unowned)} in "
                        f"its <spec_requirements> block but does not name "
                        f"{'them' if len(unowned) > 1 else 'it'} in "
                        f"`requirement_ids`. Nobody is answerable for "
                        f"{'those requirements' if len(unowned) > 1 else 'that requirement'}."
                    ),
                })
                revision_hints.append(
                    f"Casting #{cid} '{title}': add {', '.join(unowned)} to "
                    f"`requirement_ids`, or remove the citation(s) from its "
                    f"<spec_requirements> block."
                )
            undeclared = sorted(owned - cited)
            if undeclared:
                dim11_issues.append({
                    "severity": "error",
                    "casting": cid,
                    "title": title,
                    "issue": "owned_but_not_cited",
                    "ids": undeclared,
                    "detail": (
                        f"Casting #{cid} '{title}' names {', '.join(undeclared)} in "
                        f"`requirement_ids` but its <spec_requirements> block never "
                        f"cites "
                        f"{'them' if len(undeclared) > 1 else 'it'}. The teammate is "
                        f"handed no text to build from while the manifest reports "
                        f"the requirement covered."
                    ),
                })
                revision_hints.append(
                    f"Casting #{cid} '{title}': cite {', '.join(undeclared)} in its "
                    f"<spec_requirements> block, or remove "
                    f"{'them' if len(undeclared) > 1 else 'it'} from `requirement_ids`."
                )

    dim11_errors = [i for i in dim11_issues if i.get("severity") == "error"]
    dim11_ok = len(dim11_errors) == 0
    if dim11_errors:
        issues.append({
            "dimension": "requirement_ownership",
            "severity": "error",
            "message": (
                f"{len(dim11_errors)} casting(s) whose `requirement_ids` and "
                f"<spec_requirements> block disagree about what they own"
            ),
        })
    dimensions["requirement_ownership"] = {
        "ok": dim11_ok,
        "issues": dim11_issues,
        "not_computable": ownership_not_computable,
        "archive_schema_version": schema_version,
        "schema_floor": REQUIREMENT_IDS_SCHEMA_FLOOR,
    }

    # ── Dimension 12: Requirement Span ──
    #
    # `forge-specs/foundry-run-fallout/spec.md` FR-013, FR-052, AC-042, AC-044,
    # OT-038, GI-018, CT-011. "Report the span; refuse at F0.9 when any
    # requirement spans more than two castings without a recorded reason."
    #
    # THE SPAN IS THE COST OF A SLICE, MADE VISIBLE. A requirement split across
    # many castings is a requirement no single teammate can see whole: each one
    # builds its share against a partial reading, the GRIND fix for it reaches
    # one surface, and the ones nobody dispatched keep the old behaviour. Two
    # owners is a boundary a lead can hold in their head. Above that the
    # manifest must say WHY the surfaces cannot share an owner — not to make
    # the split legal, but to make it a decision somebody took rather than one
    # decompose fell into.
    #
    # REPORTED FIRST, REFUSED SECOND. The table is emitted whether or not
    # anything is over the threshold, because a lead reading F0.9 should see the
    # whole ownership picture and not only its failures. Both the table and the
    # refusal read `span_rows` — ONE computation — so the printed owners and
    # the refused owners can never disagree.
    dim12_issues: list[dict] = []
    # THE one assembly, shared with `requirement_span_table` and therefore with
    # the F6 report: this gate refuses on `span_table["rows"]` and every surface
    # renders those same records, so what a lead is shown and what the gate
    # acted on cannot name different owners.
    span_table = _requirement_span_payload(
        manifest, castings, spec_req_ids, ownership, ownership_not_computable
    )
    span_rows: list[dict] = span_table["rows"]
    if not ownership_not_computable:
        for row in span_rows:
            if row["span"] <= REQUIREMENT_SPAN_MAX:
                continue
            owners = ", ".join(f"#{owner}" for owner in row["owners"])
            if row["split_reason"]:
                # Exempt, and the recorded reason is printed rather than merely
                # honoured: a waiver nobody reads is a waiver nobody reviews.
                dim12_issues.append({
                    "severity": "info",
                    "issue": "requirement_span_recorded",
                    "id": row["id"],
                    "castings": row["owners"],
                    "span": row["span"],
                    "split_reason": row["split_reason"],
                    "detail": (
                        f"{row['id']} is owned by {row['span']} castings "
                        f"({owners}); recorded reason: {row['split_reason']}"
                    ),
                })
                continue
            dim12_issues.append({
                "severity": "error",
                "issue": REQUIREMENT_SPAN_EXCEEDED,
                "id": row["id"],
                "castings": row["owners"],
                "span": row["span"],
                "detail": (
                    f"{REQUIREMENT_SPAN_EXCEEDED}: {row['id']} is owned by "
                    f"{row['span']} castings ({owners}), above the "
                    f"{REQUIREMENT_SPAN_MAX} F0.9 accepts without a recorded "
                    f"reason, and no `split_reason` entry in the manifest names "
                    f"it."
                ),
                "hint": _SPAN_EXITS_HINT,
            })
            revision_hints.append(
                f"{REQUIREMENT_SPAN_EXCEEDED} {row['id']} (owned by {owners}): "
                f"{_SPAN_EXITS_HINT}"
            )
    else:
        dim12_issues.append({
            "severity": "info",
            "issue": "requirement_span_not_computable",
            "detail": (
                f"Requirement span is computed from the persisted "
                f"`requirement_ids`, which no casting in this manifest carries, "
                f"on an archive below schema "
                f"{REQUIREMENT_IDS_SCHEMA_FLOOR}. Not computable for this "
                f"archive and not checked."
            ),
        })

    dim12_errors = [i for i in dim12_issues if i.get("severity") == "error"]
    dim12_ok = len(dim12_errors) == 0
    if dim12_errors:
        issues.append({
            "dimension": "requirement_span",
            "severity": "error",
            "message": (
                f"{REQUIREMENT_SPAN_EXCEEDED}: {len(dim12_errors)} requirement(s) "
                f"owned by more than {REQUIREMENT_SPAN_MAX} castings with no "
                f"recorded reason: "
                + ", ".join(i["id"] for i in dim12_errors)
            ),
        })
    dimensions["requirement_span"] = {
        "ok": dim12_ok,
        "issues": dim12_issues,
        "not_computable": ownership_not_computable,
        "threshold": REQUIREMENT_SPAN_MAX,
        "rows": span_rows,
    }

    # ── Dimension 13: Surface Ownership ──
    #
    # fallout D-172. `forge-specs/foundry-run-fallout/spec.md` FR-009, GI-012,
    # CT-011: the manifest's ownership claims are what every downstream door
    # reads, so the question "is every shipped surface owned by SOMEBODY" is
    # F0.9's to ask.
    #
    # THE QUESTION NO DIMENSION ASKED. Dimension 10 above cross-checks the
    # spec's `## File Change Map` against `key_files` in both directions, so a
    # file the map NAMES that no casting owns is refused as unimplementable. A
    # file in NEITHER the map nor any `key_files` list is invisible to it by
    # construction — and when D-172 measured this repository's own plugin, 61
    # of its 159 shipped surfaces were exactly that: a parser package, the
    # findings schema, the citation and test-deriver tools, eight agent
    # definitions and five commands, owned by nobody, refused by nothing.
    # Four defects across two GRIND cycles then landed on files no casting
    # owned, and each had to be routed by a lead ruling on adjacency instead of
    # by the manifest. Casting 5 demonstrated that routing is not merely
    # inelegant but WRONG — the defect's requirement belonged to a casting the
    # adjacency did not name — and adjacency does not generalise anyway: one of
    # the four had a directory neighbour, one had none.
    #
    # THE SURFACE IS DECLARED, NEVER GUESSED, and that is the whole design.
    # Deriving it — every directory a `key_files` entry sits in, or the common
    # ancestor of the union — reads correctly on a run that owns its whole
    # product and catastrophically on any other: a brownfield casting touching
    # three files of a sixty-file directory would be refused for the other
    # fifty-seven, which are not part of the build and never were. So the
    # manifest SAYS what ships, F0.5 writes the claim, and this dimension
    # measures the difference between that claim and the `key_files` union.
    #
    # AND NO SCHEMA FLOOR, WHICH IS NOT AN OVERSIGHT. The ownership dimension
    # above can refuse a MISSING `requirement_ids` because that field became
    # mandatory in the generation that bumped the archive marker to
    # `REQUIREMENT_IDS_SCHEMA_FLOOR`, so the marker separates a manifest that
    # predates the field from one that omits it. `surface_globs` arrives INSIDE
    # that same generation with no bump, so no marker separates the two cases:
    # a floor here would refuse every existing archive of this generation —
    # this run's own included — for a field its decompose never wrote. That is
    # precisely the mis-reading `REQUIREMENT_IDS_SCHEMA_FLOOR`'s own comment
    # warns against. The declaration itself is therefore the trigger: absent is
    # NOT COMPUTABLE and passes, present is measured and refuses.
    dim13_issues: list[dict] = []
    surface_unowned: list[str] = []
    if not surface_facts["declared"]:
        dim13_issues.append({
            "severity": "info",
            "issue": "surface_globs_not_computable",
            "detail": (
                "This manifest declares no `surface_globs`, so what the run "
                "SHIPS is not stated and cannot be diffed against the "
                "`key_files` union. Surface ownership is not computable for "
                "this manifest and is not checked. Add `surface_globs` at F0.5 "
                "— the project-relative patterns naming every file this build "
                "ships — to make an unowned surface a refusal."
            ),
        })
    else:
        for pattern, why in surface_facts["rejected"]:
            dim13_issues.append({
                "severity": "warning",
                "issue": "surface_glob_unusable",
                "glob": pattern,
                "detail": (
                    f"`surface_globs` entry {pattern} is not usable: {why}. It "
                    f"reaches no file, so any surface it was meant to cover is "
                    f"unmeasured."
                ),
            })
        if not surface_facts["globs"]:
            dim13_issues.append({
                "severity": "warning",
                "issue": "surface_globs_empty",
                "detail": (
                    "`surface_globs` is declared but reaches no pattern at all, "
                    "so this dimension can find nothing and passes vacuously. A "
                    "run with castings ships something; say what."
                ),
            })
        for pattern in surface_facts["globs"]:
            if surface_facts["matched"].get(pattern):
                continue
            dim13_issues.append({
                "severity": "warning",
                "issue": "surface_glob_matched_nothing",
                "glob": pattern,
                "detail": (
                    f"`surface_globs` entry `{pattern}` matched no file under "
                    f"the project root. A pattern that reaches nothing checks "
                    f"nothing — correct it, or drop it so the declaration says "
                    f"what it means."
                ),
            })
        surface_unowned = _unowned_surfaces(surface_facts["surfaces"], castings)
        for path in surface_unowned[:_SURFACE_ISSUE_CAP]:
            dim13_issues.append({
                "severity": "error",
                "issue": SURFACE_UNOWNED,
                "file": path,
                "detail": (
                    f"{SURFACE_UNOWNED}: `{path}` is declared shipped surface "
                    f"and no casting's `key_files` reaches it. No teammate "
                    f"answers for it: a defect landing there can be routed only "
                    f"by a lead ruling on adjacency, which is a guess the "
                    f"manifest exists to replace."
                ),
                "hint": _SURFACE_EXITS_HINT,
            })
        if surface_unowned:
            issues.append({
                "dimension": "surface_ownership",
                "severity": "error",
                "message": (
                    f"{SURFACE_UNOWNED}: {len(surface_unowned)} shipped "
                    f"surface(s) of {len(surface_facts['surfaces'])} are owned "
                    f"by no casting: "
                    + ", ".join(surface_unowned[:5])
                    + ("…" if len(surface_unowned) > 5 else "")
                ),
            })
            revision_hints.append(
                f"{SURFACE_UNOWNED}: {len(surface_unowned)} shipped surface(s) "
                f"reach no casting. {_SURFACE_EXITS_HINT}"
            )

    dim13_errors = [i for i in dim13_issues if i.get("severity") == "error"]
    dimensions["surface_ownership"] = {
        "ok": len(dim13_errors) == 0,
        "issues": dim13_issues,
        "not_computable": not surface_facts["declared"],
        "globs": surface_facts["globs"],
        "surfaces": len(surface_facts["surfaces"]),
        # EVERY unowned path, whatever `_SURFACE_ISSUE_CAP` did to the prose
        # rows above. The cap exists so one broken manifest cannot bury the
        # other twelve dimensions in narration; the list a lead has to act on
        # is not the thing to truncate.
        "unowned": surface_unowned,
        "unowned_count": len(surface_unowned),
    }

    # ── Overall result ──
    # Fail on errors, warn on warnings
    error_count = sum(1 for i in issues if i.get("severity") == "error")
    passed = error_count == 0

    elapsed_ms = int((datetime.now(timezone.utc) - _started_wall).total_seconds() * 1000)
    result_payload = {
        "passed": passed,
        "dimensions": dimensions,
        "issues": issues,
        "revision_hints": revision_hints,
        # The span table at the top level, beside the dimension that computed
        # it: every requirement id the spec declares or a casting owns, its
        # owners, its span and any recorded reason. `rows` is the SAME list the
        # `requirement_span` dimension holds — one computation, so a reader
        # that renders the records and a reader that reads the block below can
        # never disagree with the refusal about who owns what.
        "requirement_span": span_table,
        "summary": {
            "castings": len(castings),
            "spec_requirements": len(spec_req_ids),
            "covered_requirements": len(covered_reqs),
            "error_count": error_count,
            "warning_count": len(issues) - error_count,
            "elapsed_ms": elapsed_ms,
        },
    }

    # Pass-marker lets foundry_next_action stamp F0.9 VALIDATE end time;
    # invalidated on fail so the marker only reflects the latest verdict.
    pass_marker = fdir / ".validate-passed"
    if passed:
        pass_marker.write_text(datetime.now(timezone.utc).isoformat(), encoding="utf-8")
        _save_validate_cache(
            fdir,
            {
                "last_pass": {
                    "fingerprints": fingerprints,
                    "result": result_payload,
                    "cached_at": datetime.now(timezone.utc).isoformat(),
                }
            },
        )
    else:
        pass_marker.unlink(missing_ok=True)

    return {**result_payload, "cache": {"hit": False}}


# ── Helpers for Dimension 7: Prompt Fidelity ──────────────────────────


_FORBIDDEN_PHRASES = [
    # Scope-cutting patterns
    "pick the core",
    "pick the most important",
    "don't port every",
    "do not port every",
    "skip the edge cases",
    "skip the edge case",
    "core coverage",
    "main cases",
    "the important ones",
    "follow-up pr",
    "follow up pr",
    "user will validate manually",
    "user will manually validate",
    "user will confirm later",
    "validate equivalence manually",
    "intentionally out-of-scope",
    "intentionally out of scope",
    "reduced scope",
    "target line count",
    "target ~",
    "aim for ~",
    "keep it under",
    # Hedge patterns
    "sufficient coverage",
    "equivalent to legacy for the main",
    "prove the framework is sufficient",
]


def _normalize(text: str) -> str:
    """Strip markdown formatting and collapse whitespace so substring
    matching compares meaningful content rather than formatting.

    Removes:
      - Leading list markers (`-`, `*`, `+`, `1.`, etc.)
      - Bold/italic wrappers, in the spelling the substitutions below use:
        **X**, __X__, *X*, _X_
      - Leading/trailing whitespace on each line
      - Consecutive blank lines (collapsed to single)

    This means the prompt's <spec_requirements> block can render the
    requirement without the spec's bullet formatting, but the meaningful
    content (e.g. "US-1: User can click ...") must match character-for-
    character after normalization.
    """
    if not text:
        return ""
    lines = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        # Strip leading list markers (-, *, +, 1., 1), a), etc.)
        line = re.sub(r"^\s*(?:[-*+]|\d+[\.\)]|[a-z]\))\s+", "", line)
        # Strip bold/italic wrappers: **X**, __X__, *X*, _X_
        line = re.sub(r"\*\*([^*]+)\*\*", r"\1", line)
        line = re.sub(r"__([^_]+)__", r"\1", line)
        line = re.sub(r"\*([^*]+)\*", r"\1", line)
        line = re.sub(r"_([^_]+)_", r"\1", line)
        # Normalize internal whitespace
        line = re.sub(r"\s+", " ", line).strip()
        lines.append(line)
    # Collapse consecutive blank lines
    out = []
    prev_blank = False
    for ln in lines:
        if not ln:
            if not prev_blank:
                out.append("")
            prev_blank = True
        else:
            out.append(ln)
            prev_blank = False
    return "\n".join(out)


def _extract_spec_block(prompt_text: str) -> str | None:
    """Extract content between <spec_requirements>...</spec_requirements>.
    Returns the normalized block content, or None if the block is missing.
    """
    match = re.search(
        r"<spec_requirements>(.*?)</spec_requirements>",
        prompt_text,
        flags=re.DOTALL | re.IGNORECASE,
    )
    if not match:
        return None
    return _normalize(match.group(1))


def _extract_invariants_block(prompt_text: str) -> str | None:
    """Extract content between <global_invariants>...</global_invariants>.
    Returns raw content (not normalized — caller decides), or None if the
    block is missing. An empty block returns the empty string, not None.
    """
    match = re.search(
        r"<global_invariants>(.*?)</global_invariants>",
        prompt_text,
        flags=re.DOTALL | re.IGNORECASE,
    )
    if not match:
        return None
    return match.group(1)


def _extract_mandatory_rules_block(prompt_text: str) -> str | None:
    """Extract content between <mandatory_rules>...</mandatory_rules>.
    Returns raw content (not normalized — caller decides), or None if the
    block is missing. An empty block returns the empty string, not None.
    """
    match = re.search(
        r"<mandatory_rules>(.*?)</mandatory_rules>",
        prompt_text,
        flags=re.DOTALL | re.IGNORECASE,
    )
    if not match:
        return None
    return match.group(1)


def _extract_file_change_map_files(spec_text: str) -> set[str]:
    """Extract every file path declared in the spec's `## File Change Map`
    section. Returns a set of normalized file paths (no backticks, no line
    refs, no leading slashes). Empty set if the section is missing or
    contains no parseable file rows.

    Recognizes two layouts:
      1. Markdown tables (most common — current forge template uses these)
         | File | What Changes | ... |
         | `models/user.go` | Add field | ... |
      2. Bullet lists (some specs use these instead of tables)
         - `models/user.go` — Add field [from A-NNN]

    File paths are normalized:
      - Stripped of surrounding backticks
      - Stripped of `:N` line refs (e.g. `foo.go:145` → `foo.go`)
      - Stripped of leading `./` or `/`
      - Trailing whitespace removed
    """
    if not spec_text:
        return set()
    section_match = re.search(
        r"^\s*##\s+File\s+Change\s+Map\s*\n(.*?)(?=^\s*##\s+|\Z)",
        spec_text,
        flags=re.MULTILINE | re.DOTALL | re.IGNORECASE,
    )
    if not section_match:
        return set()
    section = section_match.group(1)
    files: set[str] = set()
    for raw_line in section.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        # Skip section sub-headings (### Modified Files / ### New Files)
        if line.startswith("#"):
            continue
        # Skip blockquote guidance lines
        if line.startswith(">"):
            continue
        # Table row — extract first cell
        if line.startswith("|"):
            # Skip separator rows like |---|---|
            inner = line.strip("|").replace(" ", "")
            if inner and set(inner) <= set("-:|"):
                continue
            cells = [c.strip() for c in line.strip("|").split("|")]
            if not cells:
                continue
            first = cells[0]
            # Skip header rows: first cell is literally "File"
            if first.lower() in ("file", "path", "filename"):
                continue
            candidate = first
        # Bullet row — strip marker, take text up to first delimiter
        elif re.match(r"^[-*]\s+", line):
            content = re.sub(r"^[-*]\s+", "", line)
            # Take text up to first delimiter we recognize as "end of path"
            candidate = re.split(r"\s+(?:—|--|–|-|:|\(|\[)", content, 1)[0]
        else:
            continue
        # Normalize the candidate path
        path = _normalize_file_path(candidate)
        if path:
            files.add(path)
    return files


def _normalize_file_path(raw: str) -> str:
    """Strip backticks, line refs, leading ./, trailing whitespace.
    Returns empty string for non-paths (URLs, plain prose, etc.)."""
    s = raw.strip()
    # Strip backticks
    s = s.strip("`").strip()
    # Strip surrounding markdown link syntax [text](url) — keep the text
    link_match = re.match(r"^\[([^\]]+)\]\([^)]*\)$", s)
    if link_match:
        s = link_match.group(1).strip("`").strip()
    # Strip line refs: foo.go:145, foo.go:145-200
    s = re.sub(r":\d+(?:-\d+)?$", "", s)
    # Strip leading ./
    if s.startswith("./"):
        s = s[2:]
    # Strip leading / (treat as relative path)
    s = s.lstrip("/")
    # Reject obvious non-paths
    if not s or "/" not in s and "." not in s:
        return ""
    if " " in s:
        return ""
    if s.startswith(("http://", "https://")):
        return ""
    return s


#: fallout FR-009 — the one character that decides what a `key_files` entry IS.
#: An entry ending in it names a DIRECTORY; every other entry names a file.
#: Spelled once here so the reading below and the prose that quotes it cannot
#: drift into two answers.
KEY_FILE_DIRECTORY_SUFFIX = "/"


def _key_file_covers(key_file: str, path: str) -> bool:
    """Does this ``key_files`` entry reach ``path``? (fallout FR-009 — D-170.)

    THE MANIFEST FORMAT, STATED WHERE THE DOOR THAT ACCEPTS IT CAN BE READ.
    A ``key_files`` entry is either a FILE path or a DIRECTORY, spelled with a
    trailing slash, and a directory entry covers every path beneath it. That is
    not a convenience: ``Foundry-Gate('cast')`` caps a casting at eight entries,
    so a casting carving a whole new package fits under the cap by naming the
    package once — this run's own manifest carries
    ``.../tools/orchestration/`` and ``tests/orchestration/`` for exactly that
    reason, and F0.9 accepted it.

    D-170 is what accepting it without saying it costs. The GRIND ownership
    resolver compared ``key_files`` by exact set membership, so a directory
    entry matched nothing beneath it and thirteen files resolved to no casting
    at all — silently, with no refusal, which is the wrong direction for a
    dispatcher to be wrong in. Two dimensions of THIS module compared the same
    strings the same way: a file two castings both reach through a directory
    was not an overlap, and a File Change Map row reached only through a
    directory looked unimplementable.

    So the question every reader here asks is coverage, never equality, and it
    is asked in ONE place. For an entry naming a file the two are the same
    question, which is why a manifest with no directory entry is answered
    exactly as it was before this function existed.

    Both arguments are expected pre-normalised through ``_normalize_file_path``
    — this module's one path normaliser, which preserves the trailing slash the
    reading turns on.
    """
    if not key_file or not path:
        return False
    if key_file.endswith(KEY_FILE_DIRECTORY_SUFFIX):
        return path.startswith(key_file)
    return key_file == path


def _declared_key_files(castings: list) -> list[tuple[str, object]]:
    """Every ``(normalised key_files entry, owning casting id)`` in the manifest.

    ``key_files`` entries are unconstrained BELOW the list rung by
    ``_MANIFEST_DOCUMENT_SHAPE`` — the shape guard demands a list and says
    nothing about what is in it — so an entry that is not a string reaches
    every reader here. ``_normalize_file_path`` calls ``.strip()`` on what it
    is handed, so ``key_files: [123]`` used to raise ``AttributeError`` out of
    dimension 10 and take the whole F0.9 report down with it: the same failure
    ``test_foundry_validate_key_links.py`` was written against one dimension
    over, where a string where a mapping belonged raised ``AttributeError`` and
    no dimension rendered. The guard is this module's documented answer to that
    class — skip the entry, render the report.
    """
    declared: list[tuple[str, object]] = []
    for c in castings:
        cid = c.get("id", "?")
        for raw in c.get("key_files") or []:
            if not isinstance(raw, str):
                continue
            normalised = _normalize_file_path(raw)
            if normalised:
                declared.append((normalised, cid))
    return declared


def _extract_spec_invariants_section(spec_text: str) -> str:
    """Extract the `## Global Invariants` section from spec.md, if present.
    Returns the section body (everything until the next `## ` heading or
    end-of-file), stripped. Returns empty string if no such section exists.
    """
    if not spec_text:
        return ""
    match = re.search(
        r"^\s*##\s+Global\s+Invariants\s*\n(.*?)(?=^\s*##\s+|\Z)",
        spec_text,
        flags=re.MULTILINE | re.DOTALL | re.IGNORECASE,
    )
    if not match:
        # Fallback: <global_invariants> block inline in the spec
        block = re.search(
            r"<global_invariants>(.*?)</global_invariants>",
            spec_text,
            flags=re.DOTALL | re.IGNORECASE,
        )
        if block:
            return block.group(1).strip()
        return ""
    return match.group(1).strip()


def _find_drift(spec_block: str, normalized_spec: str) -> list[str]:
    """Return lines from the prompt's spec block that don't appear in
    normalized spec.md. The spec_block is already normalized when passed
    in (via _extract_spec_block → _normalize). We split the normalized
    spec by lines AND also check substring containment for multi-line
    cases.

    Short lines (<8 chars) are skipped to avoid false positives on
    things like '---' or 'EOF'.
    """
    drift: list[str] = []
    spec_lines = set(ln for ln in normalized_spec.splitlines() if ln.strip())
    for line in spec_block.splitlines():
        stripped = line.strip()
        if len(stripped) < 8:
            continue
        if stripped in spec_lines:
            continue
        # Fallback: substring match against the full normalized spec
        # (handles cases where the prompt wraps a requirement across
        # fewer or more lines than the spec does)
        if stripped in normalized_spec:
            continue
        drift.append(stripped)
    return drift


def _find_forbidden_phrases(prompt_text: str) -> list[str]:
    """Return any forbidden scope-cutting phrases found in the prompt."""
    lower = prompt_text.lower()
    found: list[str] = []
    for phrase in _FORBIDDEN_PHRASES:
        if phrase in lower:
            found.append(phrase)
    return found
