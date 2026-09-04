"""Foundry handoff audit log.

Every handoff event in a Foundry run must be recorded through
`Foundry-Handoff`. This creates an inspectable trail showing which
artifacts were produced from which sources, with integrity hashes,
and whether the lead re-read the source before the handoff happened.

Handoff events:
  - spec_to_casting:     spec.md → castings/manifest.json + casting-N-prompt.md
  - casting_to_teammate: casting-N-prompt.md → Agent spawn
  - teammate_to_accepted: teammate completion report → lead acceptance
  - inspect_to_grind:    defects → grind tasks
  - grind_to_inspect:    grind fixes → re-verification
  - assay_to_done:       ASSAY verdicts → F6 DONE
  - spec_to_decompose:   (re-read) lead re-reads spec before decomposing
  - any other transition the lead wants audited

The log is JSONL at `foundry-archive/{run}/handoffs.jsonl` (machine
readable) and mirrored to `handoffs.md` (human readable).
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from foundry_mcp.schemas.vocab import HANDOFF_EVENT_LEAD_FIX, REQUIREMENT_ID_RE
from foundry_mcp.tools.citation import CITATION_PATTERN, unresolved_symbol_cites
from foundry_mcp.tools.foundry_orchestrator import _artifact_guard, _load_json
from foundry_mcp.tools.foundry_state import (
    document_refusal,
    get_run_dir,
    read_text_file,
)


#: The DECLARATION grammar: a requirement ID in SUBJECT position on its line.
#:
#: Built from ``REQUIREMENT_ID_RE.pattern`` rather than re-typed beside it, so
#: the families stay the single axis this module already reads (D-150). What is
#: added here is only the POSITION: everything before the ID on the line must
#: be structural markdown — list bullets, blockquote markers, heading hashes,
#: table pipes, emphasis stars, whitespace — and nothing else. Prose before the
#: ID means the ID is being talked ABOUT, not declared.
_DECLARED_REQUIREMENT_ID_RE: re.Pattern[str] = re.compile(
    r"^[\s>|*+#-]*(" + REQUIREMENT_ID_RE.pattern + r")"
)


def declared_requirement_ids(block_text: str) -> list[str]:
    """The requirement IDs a casting is ANSWERABLE for, sorted and deduped.

    THE ONE DERIVATION (D-180)
    --------------------------
    This is the only answer in the tree to "which requirement IDs does this
    casting own", and it has two callers: the acceptance gate below, which
    demands a citation and an evidence binding for each of them, and
    ``foundry_validate``'s F0.9 coverage dimension, which asks whether every
    spec requirement landed in SOME casting. Those two used to compute it
    separately with a bare ``REQUIREMENT_ID_RE.findall`` over the whole block,
    which is not a second implementation of one rule so much as one rule with
    no owner: the gate and the validator could disagree about what a casting
    owns and nothing would ever compare them.

    WHY POSITION, AND NOT JUST THE FAMILY (D-180)
    ---------------------------------------------
    ``findall`` over the whole block cannot tell a requirement ASSIGNED to the
    casting from one merely QUOTED as an example inside another requirement's
    prose. Driven: casting 2's block mentions ``NFR-002`` exactly once, inside
    OT-005's own statement text ("...a LATENT filing citing NFR-002 with a
    scan-gap description is accepted"), and has no ``NFR-002`` requirement line
    of its own — yet the gate collected it as one of that casting's 45 demanded
    IDs and refused acceptance with ``EVIDENCE_REQUIREMENT_UNBOUND: NFR-002``
    for a requirement another casting owns. The teammate's only way through was
    to bind a knowingly false ``# evidence-for: NFR-002`` header to an
    unrelated log, which is a green gate recording a lie.

    A DECLARATION is the ID in subject position on its own line. All four
    shapes F0.5 DECOMPOSE emits qualify, because in each the ID is the first
    thing on the line that is not structural markdown::

        - **AC-006** [derived from A-008]: ...      bold bullet
        - FR-1: synthesized requirement            plain bullet
        | CT-001 | Foundry-Defect and ... |        typed-table row
        ### US-002: Every defect carries a tier    story heading

    ...and the two shapes that are NOT declarations stay out::

          - Maps to: US-003                        a cross-reference
        ...a LATENT filing citing NFR-002 with...  an example in prose

    WHY NOT NARROWER (the shape this rejects)
    -----------------------------------------
    Reading only ``- **ID**`` bullets — the obvious reading of "the
    requirement's own tag line" — drops every typed-table row and story
    heading. On casting 2 that is 10 of its 38 Locked requirement IDs,
    including every CT- contract and ST-009, silently no longer demanding
    evidence. Over-demanding costs a teammate an argument; under-demanding
    costs the run its gate, so the rule is anchored at the LINE, not at one
    markdown shape.

    Deduped and sorted because both callers compare it as a set and the gate
    reports it to the lead; order was never meaningful.
    """
    ids: set[str] = set()
    for line in block_text.splitlines():
        match = _DECLARED_REQUIREMENT_ID_RE.match(line)
        if match:
            ids.add(match.group(1))
    return sorted(ids)


def _hash_file(path: Path) -> str | None:
    """The published spelling of a FILE's digest: sha256 over its bytes.

    ``None`` when there is no file to hash. This is the value a shell's
    ``sha256sum`` / ``shasum -a 256`` prints (truncated to the published 16
    characters), which is why it — and never ``_hash_str`` on decoded text —
    is what ``check_reported_prompt_hash`` compares a teammate's report
    against (D-108). Text and bytes differ for any file whose line endings are
    not already LF, and only one of the two can be computed from outside this
    process.
    """
    if not path.exists() or not path.is_file():
        return None
    h = hashlib.sha256(path.read_bytes()).hexdigest()
    return f"sha256:{h[:16]}"


def _hash_str(text: str) -> str:
    """The published spelling of a STRING's digest.

    For values that are strings in the first place — a handoff id assembled
    from a timestamp and an event, an evidence log's redacted body. NOT for
    hashing a file: see ``_hash_file`` and D-108. Passing decoded file text
    here re-introduces the newline-translation gap that made an honest
    teammate's report of a CRLF prompt read as stale.
    """
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _reserved_event_key(event: object) -> str:
    """The spelling-insensitive key of a handoff event name.

    Casefolded with every non-alphanumeric character dropped, so ``lead_fix``,
    ``LEAD_FIX``, ``" lead_fix "``, ``Lead-Fix``, ``lead fix`` and ``leadfix``
    all key to ``leadfix`` — one key per NAME, not per spelling of it. The
    equivalence stops at the word: ``lead_fixes`` keys to ``leadfixes`` and
    ``lead_fix_note`` to ``leadfixnote``, so a genuinely different event name
    is still a different key and still admitted.

    A non-string keys to ``""``, which is in no reserved set. The comparison
    this replaced was ``==``, which answered False for a non-string rather
    than raising, and a rung that starts raising where it used to return would
    put an exception across the MCP boundary — the one shape a tool never
    takes.
    """
    if not isinstance(event, str):
        return ""
    return "".join(ch for ch in event if ch.isalnum()).casefold()


#: D-227 / GI-003 — the handoff events only the SERVER writes, keyed by
#: ``_reserved_event_key`` so the set holds each event's NAME rather than one
#: chosen spelling of it. Built from the vocab constant, never re-typed: the
#: token is casting 1's to name and this module's to reserve.
_SERVER_WRITTEN_EVENT_KEYS: frozenset[str] = frozenset(
    {_reserved_event_key(HANDOFF_EVENT_LEAD_FIX)}
)  # 1 event


def _append_handoff_record(
    fdir: Path,
    entry: dict,
    md_fields: list[tuple[str, str]],
) -> None:
    """Append one record to BOTH handoff channels — JSONL and the md mirror.

    WHY THIS IS A FUNCTION (GI-003)
    -------------------------------
    The audit log has two channels and they are only useful while they agree.
    While ``foundry_handoff`` was the sole writer, "append to both" was one
    block of straight-line code and could not disagree with itself. GI-003
    adds a SECOND writer — the server's own ``lead_fix`` record — and the
    moment there are two, "both channels, same format, header bootstrapped
    once" becomes a convention each is trusted to remember. That is the shape
    D-127 and D-119 both took (two doors, one remembered a step, the other did
    not), so it is factored here before it can happen a third time rather than
    after.

    ``entry`` is written to handoffs.jsonl verbatim, so each caller owns its
    own record shape — the lead_fix record carries defect_id/tier/file/
    line_count/files/test/regression_test/fix_commit as FIRST-CLASS keys, not
    prose squeezed into a summary field, because the F6 report reads them back
    by name. The
    two channels do NOT carry identical text: the JSONL keeps the raw values
    (None for an unavailable measurement) and ``md_fields`` carries the
    reader's rendering of them (D-074). ``md_fields``
    is the ordered human mirror; empty values are skipped, mirroring
    ``foundry._ledger_mirror``'s rule so an absent field prints nothing rather
    than an empty bullet.
    """
    fdir.mkdir(parents=True, exist_ok=True)

    jsonl_path = fdir / "handoffs.jsonl"
    with jsonl_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")

    md_path = fdir / "handoffs.md"
    header_needed = not md_path.exists()
    with md_path.open("a", encoding="utf-8") as f:
        if header_needed:
            f.write("# Foundry Handoff Audit Log\n\n")
            f.write("Every transition between phases or artifacts is recorded here.\n\n")
        f.write(f"## {entry['event']} — {entry['timestamp']}\n")
        for label, value in md_fields:
            if value:
                f.write(f"- {label}: {value}\n")
        f.write("\n")


MEASUREMENT_UNAVAILABLE = "measurement unavailable — git could not read the commit"
LANE_NOT_APPLIED = "recorded, lane limit not applied (LATENT)"
LANE_APPLIED = "measured against the LIVE lead lane"


def _numstat_count(value: object) -> int:
    """One ``git show --numstat`` cell as a line count.

    Binary files report ``-`` for both cells, which is zero lines — a binary
    blob is not twenty lines of anything. Anything else unparseable counts
    zero rather than raising: this runs under an MCP handler, and a record
    that cannot be written is worse than a count that under-reports a shape
    git does not actually emit.
    """
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return 0


def record_lead_fix_handoff(
    run_dir: Path,
    *,
    defect_id: str,
    tier: str,
    test: str,
    fix_commit: str,
    files: list[dict] | None = None,
    file: str | None = None,
    line_count: int | None = None,
    regression_test: str | None = None,
) -> dict:
    """Append the server's own ``lead_fix`` record. Returns the record.

    GI-003 / AC-022 / OT-010 — THE SERVER WRITES THIS, NOT THE LEAD. A lead
    fix recorded as free prose in a hand-written handoff is a fix nothing can
    measure or count: it cannot be totalled, the F6 report cannot list it, and
    the lane the fix was supposed to fit inside is unfalsifiable after the
    fact. So ``Foundry-Fix`` appends this itself on every accepted lead fix,
    and the record carries the five things a reader needs to re-derive the
    decision — which defect, at which tier, in which file, how many lines, and
    the test that holds it — plus the commit those were measured from.

    THE MEASUREMENT AND THE LANE ARE DIFFERENT FACTS (D-046, then D-074)
    -------------------------------------------------------------------
    Both tiers are MEASURED. What is LIVE-only is the lane ELIGIBILITY test
    (ST-004 / CT-006 / FR-046) — a limit on the numbers, not a reason to stop
    reading them. D-046 fixed the caller so a LATENT lead fix records its real
    file and count; this docstring went on claiming the opposite ("file and
    line_count are None for a LATENT lead fix, which lands unmeasured"), and
    the markdown mirror went on printing ``unmeasured (LATENT)`` for a None.

    That relocated D-046 rather than closing it. With the caller measuring on
    both lanes, the ONLY remaining way a LATENT record reaches None is git
    failing to read the commit — so the label asserted a deliberate policy
    skip in exactly the case where the measurement had FAILED. Driven: a
    LATENT lead fix carrying ``fix_commit = "0" * 40`` was accepted (there is
    no measurement gate on the LATENT lane) and handoffs.md read
    ``file: unmeasured (LATENT)``.

    So, LEAD RULING, GRIND cycle 4:

    * ``files`` is the measurement — the ``git show --numstat`` rows the lane
      was measured over, each ``{"path", "added", "deleted", "renamed_from"}``.
      ``file`` is the single path when exactly one non-test file changed and
      None otherwise; ``line_count`` is added-plus-deleted over the listed
      rows.

      The rows arrive ALREADY classified and this does not re-classify them.
      ``_numstat_measurement`` drops the test rows on its way to building them
      (FR-016), and D-075 made that classification rename-aware — a rename out
      of the tests tree changes production code and stays in. Re-running a
      plain ``is_test_file(path)`` here would silently drop exactly the rows
      that rule keeps, and the audit record would then disagree with the lane
      measurement it exists to make re-derivable. One classifier, at the
      measurement, as its own comment says.
    * A LATENT record is "recorded, lane limit not applied". Never
      "unmeasured".
    * None in ``file``/``line_count`` means the measurement was UNAVAILABLE —
      git could not read the commit — and the mirror says exactly that.

    ONE FIELD PER LOCATOR — ``test`` IS NOT A SLOT TWO TESTS COMPETE FOR
    (D-170)
    -------------------------------------------------------------------
    A LIVE lead fix must declare an adjacent-path test; a regression test is
    additionally allowed on either lane. Both are locators, and this record
    used to have ONE field for them, so the caller resolved the collision with
    ``test=regression_ref or test_ref``. Driven at the door: a LIVE lead fix
    that supplied both was accepted, and the record — plus its handoffs.md
    mirror row — carried only the optional regression locator, while the
    MANDATED adjacent-path test appeared in neither channel. AC-022 names the
    test as one of the five things a reader must be able to re-derive, and for
    a LIVE lead fix the test that holds the fix is the adjacent-path one, so
    the audit trail for a fix nobody else reviewed showed a reader a different
    test than the one the declaration was judged on.

    So ``regression_test`` is its own field and ``test`` keeps carrying
    whatever the caller passes. It is written ONLY when non-empty — in both
    channels — rather than as the ``None``-when-absent shape ``files`` takes
    two paragraphs above. The two rules differ on purpose: ``files`` is a
    MEASUREMENT, and its absence is a fact about the commit worth recording,
    whereas an absent regression test is simply a locator nobody claimed. And
    a key added unconditionally would change the bytes of every record written
    without one, which is what the committed evidence logs re-execute against.

    ``files``, ``file`` and ``line_count`` all default to None so the call
    shape that predates this ruling keeps working while the caller
    (``foundry_mark_defect_fixed``, casting 3's file) moves to ``files=`` in
    the same cycle: a writer that demanded the new shape would make the tree
    red between the two commits. When ``files`` is given it is authoritative
    and ``file``/``line_count`` are derived from it — two sources for one
    measurement is how they come to disagree.

    Takes ``run_dir`` rather than ``project_root`` because its one caller —
    ``foundry_mark_defect_fixed``, which has just written the defect ledger —
    already holds the resolved run dir. Re-resolving it here would be a second
    derivation of a path the caller has, and the two could disagree.
    """
    measured: list[dict] | None = None
    if files is not None:
        measured = [row for row in files if isinstance(row, dict)]
        file = str(measured[0].get("path")) if len(measured) == 1 else None
        line_count = sum(
            _numstat_count(row.get("added")) + _numstat_count(row.get("deleted"))
            for row in measured
        )

    # ``line_count`` is the availability signal, not ``file``: a commit
    # touching three non-test files has a real count and NO single path, and
    # calling that "unavailable" would be the same lie one shape along.
    if line_count is None:
        file_label = MEASUREMENT_UNAVAILABLE
        line_label = MEASUREMENT_UNAVAILABLE
    else:
        line_label = str(line_count)
        if file is not None:
            file_label = file
        elif measured is None:
            file_label = "more than one non-test file (no single path)"
        elif not measured:
            file_label = "no non-test file in the commit"
        else:
            file_label = f"{len(measured)} non-test files: " + ", ".join(
                str(row.get("path")) for row in measured
            )

    timestamp = datetime.now(timezone.utc).isoformat()
    entry = {
        "handoff_id": _hash_str(f"{timestamp}|{HANDOFF_EVENT_LEAD_FIX}|{defect_id}"),
        "timestamp": timestamp,
        "event": HANDOFF_EVENT_LEAD_FIX,
        "defect_id": defect_id,
        "tier": tier,
        "file": file,
        "line_count": line_count,
        # D-074: the rows themselves, so a reader of a record whose ``file`` is
        # None can see WHICH files rather than infer from a blank cell. None
        # rather than absent when the caller passed none, so every record has
        # one shape.
        "files": measured,
        "test": test,
        # D-170: beside ``test``, never instead of it, and present only when a
        # regression locator was actually named. Both channels put it in this
        # same position — the log is only useful while they agree.
        **({"regression_test": regression_test} if regression_test else {}),
        "fix_commit": fix_commit,
    }
    _append_handoff_record(
        run_dir,
        entry,
        [
            ("defect_id", f"`{defect_id}`"),
            ("tier", tier),
            ("lane", LANE_NOT_APPLIED if tier == "LATENT" else LANE_APPLIED),
            ("file", file_label),
            ("line_count", line_label),
            ("test", f"`{test}`"),
            *(
                [("regression_test", f"`{regression_test}`")]
                if regression_test
                else []
            ),
            ("fix_commit", f"`{fix_commit}`"),
        ],
    )
    return entry


def check_reported_prompt_hash(
    run_dir: Path,
    casting_id: int | str,
    reported_hash: str | None,
) -> dict | None:
    """None when the teammate's reported prompt hash is the file's, else a refusal.

    CT-011 / AC-030 — pointer dispatch hands the teammate a PATH and a HASH
    instead of the prompt text, and this is what turns that from advice into
    something checkable: only an agent that actually read the file can state
    the value back. Both consuming gates use this one function —
    ``foundry_accept_casting`` below and ``Foundry-Fix`` — because a hash rung
    that exists at one door and not the other lets an unread prompt through
    whichever door the lead happens to walk.

    THE COMPARISON VALUE IS THE PUBLISHED SPELLING, OVER THE FILE'S BYTES
    (D-108). ``"sha256:" + hexdigest()[:16]`` is byte-for-byte what
    ``foundry_spawn`` publishes as ``prompt_hash`` and what its dispatch block
    tells the teammate to state back "character for character". A bare
    hexdigest or the full 64 characters here would make every honest report a
    mismatch, so there is exactly one spelling in the package and this reads
    it rather than re-deriving one.

    WHAT IS HASHED IS THE BYTES, NOT THE DECODED TEXT (D-108). ``_hash_file``
    hashes ``path.read_bytes()``; ``_hash_str`` hashes ``text.encode("utf-8")``
    AFTER ``read_text_file`` has already translated newlines, which is what
    this rung used. On a prompt file written with CRLF line endings the two
    disagree — the text digest is taken over a document with every ``\\r``
    silently removed — and the teammate's own documented command
    (``sha256sum`` / ``shasum -a 256`` on the file) can only ever produce the
    BYTES digest. So the honest report of a CRLF prompt was refused as stale
    while nothing was stale. The bytes are what both sides can independently
    compute, so the bytes are what is compared.

    An unreadable or missing prompt file returns the house ``document_refusal``
    rather than None: nothing was compared, and "I could not read the file" is
    not the same answer as "the hashes agree". The DECODE guard below stays
    even though the digest no longer needs the text — a prompt this server
    cannot decode is one no teammate can be handed, and that answer is owed
    whether or not a hash would have matched.
    """
    prompt_path = run_dir / "castings" / f"casting-{casting_id}-prompt.md"
    if not prompt_path.exists():
        return document_refusal(prompt_path, f"{prompt_path.name} not found")

    _prompt_text, problem = read_text_file(prompt_path)
    if problem is not None:
        return document_refusal(prompt_path, problem)

    expected = _hash_file(prompt_path)
    if expected is None:
        # Lost between the exists check and the read: still "I could not read
        # the file", still not a match.
        return document_refusal(prompt_path, f"{prompt_path.name} not found")

    if reported_hash == expected:
        return None

    return {
        "ok": False,
        # The error TOKEN is unchanged from the inline rung this replaced.
        # Callers and tests key on it, and a rung that starts naming itself
        # differently the day it is shared is a behaviour change smuggled in
        # under a refactor.
        "error": "stale_prompt_hash",
        "hint": (
            f"Casting prompt hash mismatch. The file at {prompt_path.name} "
            f"hashes to {expected!r}; the value reported was "
            f"{reported_hash!r}. Call Foundry-Spawn-Teammate for a fresh "
            f"prompt hash — or, if the teammate reported it, have them re-read "
            f"the prompt file in full and state its hash character for "
            f"character. Only reading the file produces the right answer, "
            f"which is the point."
        ),
        "expected_hash": expected,
        "reported_hash": reported_hash,
    }


def foundry_handoff(
    event: str,
    source: str = "",
    destination: str = "",
    source_reread: bool = False,
    summary: str = "",
    information_loss: str = "",
    project_root: str = ".",
) -> dict:
    """Record a handoff event.

    Args:
        event: One of spec_to_casting, casting_to_teammate, teammate_to_accepted,
            inspect_to_grind, grind_to_inspect, assay_to_done, spec_reread,
            or a custom short name — with one RESERVED exception.
            ``HANDOFF_EVENT_LEAD_FIX``, in ANY spelling, is refused here:
            that token names a record only the server writes, through
            ``record_lead_fix_handoff`` on a successful Foundry-Fix. See the
            D-106 / D-227 block below.
        source: Path to the source artifact (relative to project root). If the
            path exists, its hash is recorded automatically.
        destination: Path to the destination artifact.
        source_reread: The lead MUST set this to True if the handoff involves
            re-reading the source (e.g. spec → casting, spec → teammate prompt,
            spec → acceptance). False means "the lead acted from prior memory,"
            which is logged but flagged.
        summary: One-line description of what this handoff accomplished.
        information_loss: If the destination artifact contains less of the
            spec than the source, describe what was dropped. Non-empty value
            is a warning flag (prompts the lead to justify).

    Returns:
        {
            "ok": True,
            "event": ...,
            "handoff_id": "uuid",
            "source_hash": ...,
            "destination_hash": ...,
            "source_reread": bool,
            "warning": str | None
        }
    """
    # D-106 — THE `lead_fix` TOKEN IS RESERVED TO THE SERVER (GI-003 / AC-022).
    #
    # GI-003's named violation is "a lead fix recorded only as free prose in a
    # hand-written handoff", and the mechanism that answers it is that the
    # SERVER appends the record itself, carrying the defect id, tier, file,
    # line count and test it MEASURED. That guarantee is only worth the bytes
    # it is written in if the token cannot also be written by hand — and it
    # could be, because this door took `event` as a free string.
    #
    # Driven at 5dd9dad, after one real lead fix:
    # `foundry_handoff(event="lead_fix", summary="hand-written, never
    # measured")` returned ok=True, and the generated report then read
    # "2 lead-authored fixes (GI-003 / AC-022), 2 file rows" with the forged
    # row rendered as "measurement unavailable — git could not read the
    # commit" and report.json's lead_fix_records.count at 2 — a row whose
    # defect_id, tier, file and fix_commit were all null. Worse than a bare
    # forgery: it borrowed D-078's measurement-unavailable sentinel, so it
    # read as a SERVER record whose git read had merely failed.
    #
    # The rung is FIRST, ahead of the run-dir resolution, because it needs
    # nothing but the argument — the house precondition-ladder shape, cheapest
    # and most specific rung first.
    #
    # D-227 — THE RUNG RESERVES THE NAME, NOT ONE SPELLING OF IT.
    #
    # It was written as `event == HANDOFF_EVENT_LEAD_FIX`, a bare equality
    # with no normalisation, on the reasoning that `lead_fix` is the only
    # event the server writes and a frozenset of one would be the same
    # comparison with a cross-module dependency bolted on. Both halves of that
    # were wrong. Driven through this door on a scratch run: `event="lead_fix"`
    # was refused as reserved while `event="LEAD_FIX"`, `event=" lead_fix "`
    # and `event="Lead_Fix"` each returned ok=True and were appended, putting
    # the headings `## LEAD_FIX`, `##  lead_fix ` and `## Lead_Fix` into
    # handoffs.md beside the server-written ones, indistinguishable from them
    # to the human who reads that file. report.json stayed honest — the report
    # reader filters on the exact token — so the bypass reached the
    # human-readable mirror only, which is the whole of the harm and enough of
    # it: GI-003's named violation is "a lead fix recorded only as free prose
    # in a hand-written handoff", and a forged `## LEAD_FIX` block IS that.
    #
    # So the comparison is over `_reserved_event_key`, and the reserved side
    # is the named set the sentence this replaced promised for "when a second
    # server-written event lands" — brought forward, because the set is also
    # what closes the spelling gap. The key folds case and every non-
    # alphanumeric character, so each of the driven spellings plus `lead-fix`
    # and `lead fix` keys to the reserved `leadfix`. It stops at the word:
    # `lead_fixes` and `lead_fix_note` key elsewhere and are still admitted,
    # so the refusal is bounded to exactly the name the server owns rather
    # than to everything that mentions it. The set stays HERE rather than in
    # vocab.py because vocab.py names the token and this module reserves it —
    # two different jobs, and the second is this door's.
    if _reserved_event_key(event) in _SERVER_WRITTEN_EVENT_KEYS:
        spelling_note = (
            ""
            if event == HANDOFF_EVENT_LEAD_FIX
            else f" — {event!r} is that same name in another spelling"
        )
        return {
            "ok": False,
            "error": (
                f"Refused: {HANDOFF_EVENT_LEAD_FIX!r} is a reserved handoff "
                f"event{spelling_note}. Only the server writes it, through "
                f"record_lead_fix_handoff, on a successful Foundry-Fix with "
                f"authored_by=lead."
            ),
            "hint": (
                "A lead fix is recorded by MAKING it: call Foundry-Fix with "
                "authored_by=lead and fix_commit, and the server appends the "
                "record itself with the defect id, tier, file, line count and "
                "test it measured from the commit. GI-003 exists so a "
                "lead-authored fix cannot be a hand-written claim, so writing "
                "the record by hand is the one thing this door will not do. "
                "For a note about a lead fix, use a different event NAME — a "
                "different spelling of this one (case, spaces, hyphens) is "
                "the same name and is refused the same way."
            ),
            "field": "event",
            "reserved_event": HANDOFF_EVENT_LEAD_FIX,
        }

    fdir = get_run_dir(project_root)
    if not fdir:
        return {"ok": False, "error": "No active foundry run"}
    if not fdir.exists():
        fdir.mkdir(parents=True, exist_ok=True)

    root = Path(project_root).resolve()
    source_path = (root / source) if source and not Path(source).is_absolute() else Path(source) if source else None
    dest_path = (root / destination) if destination and not Path(destination).is_absolute() else Path(destination) if destination else None

    source_hash = _hash_file(source_path) if source_path else None
    dest_hash = _hash_file(dest_path) if dest_path else None

    timestamp = datetime.now(timezone.utc).isoformat()
    handoff_id = _hash_str(f"{timestamp}|{event}|{source}|{destination}")

    entry = {
        "handoff_id": handoff_id,
        "timestamp": timestamp,
        "event": event,
        "source": source,
        "source_hash": source_hash,
        "destination": destination,
        "destination_hash": dest_hash,
        "source_reread": bool(source_reread),
        "summary": summary,
        "information_loss": information_loss,
    }

    warning = None
    if information_loss:
        warning = f"Information loss reported: {information_loss}. Lead must justify or re-decompose."
    if not source_reread and event in {"spec_to_casting", "spec_reread", "spec_to_decompose", "acceptance"}:
        warning = (warning + "; " if warning else "") + (
            f"source_reread=False for event '{event}'. Lead acted from memory, "
            f"not a fresh read of the source. Context rot risk."
        )

    # Both channels, through the writer the lead_fix record also uses, so the
    # two records cannot land in different files or in different formats.
    _append_handoff_record(
        fdir,
        entry,
        [
            ("handoff_id", f"`{handoff_id}`"),
            ("source", f"`{source}` ({source_hash or 'no file'})" if source else ""),
            (
                "destination",
                f"`{destination}` ({dest_hash or 'no file'})" if destination else "",
            ),
            ("source_reread", f"`{source_reread}`"),
            ("summary", summary),
            ("**information_loss**", information_loss),
            ("**WARNING**", warning or ""),
        ],
    )

    return {
        "ok": True,
        "handoff_id": handoff_id,
        "event": event,
        "source_hash": source_hash,
        "destination_hash": dest_hash,
        "source_reread": source_reread,
        "warning": warning,
        "log_entry": entry,
    }


def foundry_spec_hash(project_root: str = ".") -> dict:
    """Return the current sha256 of spec.md. Lead calls this to obtain a
    hash that must be passed to `Foundry-Spawn-Teammate` and
    `Foundry-Accept-Casting`. The tools verify the hash matches the
    current file content, forcing the lead to actually Read the spec
    rather than relying on prior context.
    """
    fdir = get_run_dir(project_root)
    if not fdir:
        return {"ok": False, "error": "No active foundry run"}
    if (corrupt := _artifact_guard(fdir)):
        return {"ok": False, **corrupt}

    spec_path = fdir / "spec.md"
    if not spec_path.exists():
        state_path = fdir / "state.json"
        if state_path.exists():
            # D-130's class, found by the package-wide scan rather than by a
            # defect report: this was `json.loads(state_path.read_text(...))`,
            # so a corrupt state.json raised out of Foundry-Spec-Hash -- the
            # tool every Foundry-Spawn-Teammate and Foundry-Accept-Casting call
            # depends on -- as a traceback naming no file. Routed through the
            # orchestrator's tolerant loader; the guard above it names the file.
            state = _load_json(state_path)
            sp = state.get("spec_path", "")
            if sp:
                candidate = Path(project_root) / sp
                if candidate.exists():
                    spec_path = candidate

    if not spec_path.exists():
        return {"ok": False, "error": "spec.md not found in run directory or state"}

    h = _hash_file(spec_path)
    size = spec_path.stat().st_size
    mtime = datetime.fromtimestamp(spec_path.stat().st_mtime, tz=timezone.utc).isoformat()

    return {
        "ok": True,
        "spec_path": str(spec_path),
        "spec_hash": h,
        "size_bytes": size,
        "mtime": mtime,
        "instruction": (
            "Read the spec.md file now. Then pass the spec_hash to every "
            "Foundry-Spawn-Teammate and Foundry-Accept-Casting call. If you "
            "do not re-Read the spec first, you are acting from memory — "
            "this violates the context-rot prevention rule."
        ),
    }


def foundry_accept_casting(
    casting_id: int | str,
    spec_hash: str,
    prompt_hash: str,
    completion_report: str,
    project_root: str = ".",
    *,
    casting_commit: str | None = None,
) -> dict:
    """Gate the acceptance of a completed casting.

    The lead MUST call this before marking any casting done. The tool:
      1. Verifies spec_hash matches the current spec.md (forces re-read)
      2. Verifies prompt_hash matches the casting's prompt file (forces
         the lead to have read the authoritative prompt, not a memory)
      3. Records the acceptance as a handoff entry
      4. Returns the list of acceptance criteria from the casting's
         <spec_requirements> block so the lead can verify each against
         the completion report
      5. **Phase 4 / EVID-01:** Re-runs the teammate's cited evidence
         commands server-side via ``verify_evidence``. On v2.0 specs,
         records an EVID-01 stream-skip (Phase 1/2/3 backwards-compat).
         On v2.1+ specs, re-executes each ``evidence/casting-{id}-*.log``
         in an isolated worktree and rejects on byte-mismatch, timeout,
         non-zero exit, missing command, malformed volatile regex, or
         stub-pattern hit.

    It does NOT mechanically check that the completion report satisfies
    the ACs — that requires semantic understanding. It provides the
    authoritative AC list and forces the lead to acknowledge it.

    Args:
        casting_id: Casting id from manifest.json
        spec_hash: Fresh sha256 of spec.md (from Foundry-Spec-Hash)
        prompt_hash: Hash of casting-{id}-prompt.md (from Foundry-Spawn-Teammate)
        completion_report: The teammate's completion report text
        project_root: Repo root
        casting_commit: REQUIRED (CT-015 / FR-010 / AC-015) — full SHA of the
            casting's commit (rev-parseable). ``verify_evidence`` checks this
            commit out in a detached worktree, so acceptance without it would
            have nothing to verify. Omitting it is a refusal naming the
            parameter, on the first rung of the precondition ladder. The
            parameter keeps its ``None`` default so the refusal is the
            handler's and reads identically however the call arrived — over
            MCP, from a test, or from another handler — rather than being an
            argument-binding TypeError at one door and a named refusal at the
            next.

            The backwards-compat shim this used to carry is GONE: when
            ``casting_commit`` was optional, omitting it bypassed BOTH EVID-01
            and EVID-02 and still returned ``ok: true``, which bought a green
            acceptance that verified nothing. Note that ``evidence_provenance``
            being an EMPTY list is still a legitimate outcome — a v2.0 spec
            routes through the stream-skip branch — but it is now always the
            RESULT of running the evidence path rather than a default from a
            bypassed block.

    Returns:
        On success:
            {"ok": True, "casting_id": N, "acceptance_criteria": [...],
             "must_verify": [...], "warning": str | None,
             "unresolved_symbol_cites": [],
             "evidence_verdict": "accepted" | "skipped",
             "evidence_provenance": [...],
             "evidence_tally": {"accepted": N, "rejected": N,
                                "failure_tokens": [...]} | None,
             "evidence_spec_path": str | None}
        On failure:
            {"ok": False, "error": "...", "hint": "..."}
        On evidence rejection:
            {"ok": False, "failure_token": "EVIDENCE_*", "failure_detail": "...",
             "evidence_provenance": [...], "evidence_tally": {...}}

    ``evidence_tally`` is the per-casting verdict count. It is a RETURN VALUE
    and never a printed line (D-149): this server speaks JSON-RPC over stdio,
    so anything written to stdout lands inside the protocol channel.

    ``evidence_spec_path`` names the spec the evidence run actually read —
    resolved from the RUN (``foundry_spec_hash``), never re-derived from a
    fixed ``<project_root>/specs/spec.md`` guess. It is the observable proof
    that a v2.1 run engaged evidence re-execution rather than silently routing
    through the v2.0 stream-skip branch.
    """
    fdir = get_run_dir(project_root)
    if not fdir:
        return {"ok": False, "error": "No active foundry run"}

    # CT-015 / FR-010 / AC-015 / OT-027 — casting_commit is REQUIRED.
    #
    # It was optional, and optional is what made it dangerous: omitting it
    # bypassed BOTH EVID-01 (evidence re-execution) and EVID-02 (per-requirement
    # binding) and still returned ok:true. The failure mode was not an error a
    # lead would notice, it was a GREEN ACCEPTANCE THAT VERIFIED NOTHING —
    # the most expensive kind, because the run proceeds on it. "No acceptance
    # without EVID-01/EVID-02 running" (FR-010) is the rule, and a default that
    # silently disables both is that rule's exact negation.
    #
    # It is the FIRST rung deliberately. A missing required parameter is a
    # fault in the CALL, not in the run's artifacts, so making a lead produce a
    # fresh spec hash before learning they omitted a parameter answers a
    # question they did not ask. It also lands before any worktree or
    # subprocess work by construction rather than by placement luck.
    if not casting_commit:
        return {
            "ok": False,
            "error": (
                "casting_commit is required. Acceptance re-executes the "
                "casting's committed evidence at that commit; without it "
                "there is nothing to check out and nothing to verify."
            ),
            "hint": (
                "Pass the full SHA of the casting's commit — the teammate "
                "states it in the completion report, and `git rev-parse HEAD` "
                "in the casting's worktree produces it. Acceptance is not "
                "available without evidence re-execution: a casting that "
                "cannot name its commit has not been shown to have built "
                "anything."
            ),
            "casting_id": casting_id,
            "field": "casting_commit",
        }

    # Verify spec hash
    spec_result = foundry_spec_hash(project_root=project_root)
    if not spec_result.get("ok"):
        return {"ok": False, "error": f"Cannot hash spec: {spec_result.get('error')}"}
    current_spec_hash = spec_result["spec_hash"]
    if spec_hash != current_spec_hash:
        return {
            "ok": False,
            "error": "stale_spec_hash",
            "hint": (
                f"Spec hash mismatch. You passed {spec_hash!r} but current is "
                f"{current_spec_hash!r}. Re-read spec.md and try again with the "
                f"fresh hash. Never accept a casting using a spec hash from "
                f"memory — the spec may have been updated mid-run."
            ),
        }

    # Load the casting prompt
    prompt_path = fdir / "castings" / f"casting-{casting_id}-prompt.md"
    if not prompt_path.exists():
        return {
            "ok": False,
            "error": f"casting-{casting_id}-prompt.md not found",
            "hint": "Re-run F0.5 DECOMPOSE",
        }

    # D-146: the whole read sits behind ONE guarded call. A casting prompt that
    # exists but cannot be decoded used to raise UnicodeDecodeError straight
    # across the MCP boundary, where the lead sees a traceback instead of a
    # named refusal — the D-137 family's exact shape, one door further along.
    prompt_text, prompt_problem = read_text_file(prompt_path)
    if prompt_problem is not None:
        return document_refusal(prompt_path, prompt_problem)

    # CT-011 / AC-030 — the hash rung is `check_reported_prompt_hash`, not a
    # second inline comparison. Foundry-Fix applies the same check to the same
    # value, and two implementations of "does the reported hash match the
    # file" is how one door ends up accepting a prompt the other would refuse.
    # The helper re-reads the file rather than taking `prompt_text` as an
    # argument, so the bytes it hashes are the bytes on disk at the moment of
    # the check at BOTH doors.
    hash_refusal = check_reported_prompt_hash(fdir, casting_id, prompt_hash)
    if hash_refusal is not None:
        return hash_refusal

    # Extract acceptance criteria from the <spec_requirements> block
    match = re.search(
        r"<spec_requirements>(.*?)</spec_requirements>",
        prompt_text,
        flags=re.DOTALL | re.IGNORECASE,
    )
    if not match:
        return {
            "ok": False,
            "error": "casting prompt has no <spec_requirements> block",
            "hint": "F0.9 VALIDATE should have caught this. Re-run validation.",
        }

    spec_block = match.group(1).strip()
    acs = [ln.strip() for ln in spec_block.splitlines() if ln.strip()]

    # Requirement-ID citation check.
    #
    # Parse every tagged requirement ID from the casting's <spec_requirements>
    # block. For each ID, verify the completion report contains a citation
    # within 300 chars of the ID mention. Missing citations mean the teammate
    # did not (or cannot) prove that requirement was implemented — mechanical
    # proof-of-coverage, prevents drift between what the spec asked for and
    # what the teammate claims was built.
    #
    # FR-004 / AC-005: BOTH cite forms count — the durable `path#Symbol` form
    # and the legacy `file:line` form. The grammar lives in
    # `citation.CITATION_PATTERN` so the gate and the resolution guard below
    # cannot drift apart; widening it here never narrows what was accepted
    # before, because the file:line alternative is carried through unchanged.
    # THE WINDOW IS SYMMETRIC (D-118). It used to be
    # `completion_report[start:start + 300]` — forward from the ID only — while
    # agents/teammate.md tells every teammate the gate "verifies each
    # requirement ID has a citation WITHIN 300 CHARACTERS OF the ID mention".
    # So `src/mod.py#foo implements FR-001` satisfies the documented
    # instruction and was rejected, costing a re-dispatch bounce on a report
    # that was already correct. The failure was safe (over-strict, never
    # over-permissive), which is why it survived — a gate that only ever
    # refuses too much produces no bad acceptances to notice, just wasted
    # cycles. Widening the code to match the prose is the fix; the prose is
    # another casting's file and needs no change.
    CITATION_WINDOW = 300
    # D-150: the requirement-ID families are declared ONCE, in the vocabulary
    # module. This was the first of seven hand-typed copies, and like the rest
    # it knew seven families and neither OT- nor GI-, so an observable truth
    # quoted into a casting's <spec_requirements> block was invisible here: no
    # citation was ever demanded for it and none could be credited. The
    # canonical pattern is a strict SUPERSET, so every ID that required a
    # citation before still does (NFR-002) — and OT-/GI-/CT-/ST- rows quoted
    # into the block now require one too, which is the point.
    #
    # D-180: ...and WHICH of those rows the casting owns is `declared_requirement_ids`,
    # not a `findall` over the block. D-150 widened the FAMILIES this reader can
    # see; it left the SUBJECT unjudged, so an ID quoted as an example inside
    # another requirement's prose was harvested exactly like a requirement
    # assigned to the casting. Both consumers below read this one list — the
    # citation window and the EVID-02 binding check — so the wrong subject was
    # demanded twice from the same mistake. The helper's docstring carries the
    # driven case and why the narrower `- **ID**` reading was rejected.
    casting_req_ids = declared_requirement_ids(spec_block)
    citation_pattern = CITATION_PATTERN
    missing_citations: list = []
    for rid in casting_req_ids:
        # Find every occurrence of the requirement ID in the report.
        found_citation = False
        for m in re.finditer(re.escape(rid), completion_report):
            start = m.start()
            window = completion_report[
                max(0, start - CITATION_WINDOW):m.end() + CITATION_WINDOW
            ]
            if citation_pattern.search(window):
                found_citation = True
                break
        if not found_citation:
            missing_citations.append(rid)

    # Mechanical symbol-resolution guard (FR-004 / AC-006 / AC-007).
    #
    # A new rung on the precondition ladder: every `path#Symbol` cite in the
    # report must resolve in the tree. A cite whose symbol resolves is VALID
    # however stale a `:line` hint beside it has become — the guard never
    # reads the line component, so a moved line produces no finding of any
    # kind. A symbol that resolves nowhere is a defect and blocks acceptance.
    unresolved_cites = unresolved_symbol_cites(completion_report, project_root)

    # ============================================================
    # Phase 4 / EVID-01: server-side evidence re-execution.
    #
    # Inserted between the req-ID citation check and the scope-flag check.
    # On v2.0 specs, verify_evidence routes through manifest.stream_skips
    # (Phase 1/2/3 backwards-compat — same machinery as Phase 3 stream-skips
    # but with EVID-01 as a virtual stream owned by foundry_accept_casting).
    # On v2.1+ specs, every cited evidence command is re-run server-side
    # and rejected on byte-mismatch, timeout, non-zero exit, missing
    # command, malformed volatile regex, or stub-pattern hit.
    #
    # THIS BLOCK IS NO LONGER CONDITIONAL (CT-015 / FR-010). It used to sit
    # under `if casting_commit is not None:` — a backwards-compat shim for
    # callsites that had not migrated — and the branch is dead now that the
    # first rung refuses an absent commit. It is REMOVED rather than left
    # standing as an unreachable guard, because an `if` that can no longer be
    # false still reads as a supported mode to the next author, and the mode it
    # advertised was "acceptance with no verification". `evidence_provenance`
    # is therefore always the result of running this path.
    # ============================================================
    evidence_verdict = None
    evidence_tally: dict | None = None
    evidence_provenance: list[dict] = []
    evidence_spec_path: Path | None = None
    evidence_stream_skips: list[dict] = []
    from foundry_mcp.tools.evidence import (
        _declared_spec_format_version,
        _read_spec_format_version,
        verify_evidence,
    )

    # Resolve run_dir for worktree storage. fdir is the active foundry
    # run dir (computed at function entry); pass it through so the
    # worktree lives under foundry-archive/{run}/worktrees/.
    #
    # FR-017 / AC-023: the spec path is the RUN's spec, not a third
    # re-derivation. `foundry_spec_hash` already resolved it above
    # (fdir/spec.md, else the spec_path recorded in state.json) and the
    # result is in `spec_result`. The hardcoded
    # `<project_root>/specs/spec.md` this replaces pointed at a file most
    # runs do not have; `verify_evidence` reads `spec_format_version` off
    # whatever path it is handed and defaults to v2.0 on a miss, so the
    # wrong path silently downgraded every v2.1 run to the stream-skip
    # branch and no evidence was ever re-executed.
    evidence_spec_path = Path(spec_result["spec_path"])

    # A DECLARED but unparseable spec_format_version is a precondition
    # failure, and it is refused HERE, in the same {ok, error, hint} shape
    # as every other rung of this ladder. It is not an evidence failure —
    # nothing was re-executed and no evidence file is at fault — so it
    # carries no KNOWN_EVIDENCE_FAILURE_TOKENS name. What it must not do is
    # what it used to: parse as v2.0, skip re-execution, and return
    # `ok: true`. A typo in one frontmatter line bought a green gate.
    if _read_spec_format_version(evidence_spec_path) is None:
        declared = _declared_spec_format_version(evidence_spec_path)
        return {
            "ok": False,
            "casting_id": casting_id,
            "error": "malformed_spec_format_version",
            "evidence_spec_path": str(evidence_spec_path),
            "declared_spec_format_version": declared,
            "hint": (
                f"{evidence_spec_path} declares spec_format_version "
                f"{declared!r}, which is not a vN.N version. Evidence "
                f"verification will not guess a version and will not "
                f"silently downgrade the run to v2.0. Fix the spec's "
                f"frontmatter to a real version (e.g. `spec_format_version: "
                f"v2.1`) and re-run acceptance."
            ),
        }

    evidence_result = verify_evidence(
        casting_id=casting_id,
        project_root=Path(project_root),
        casting_commit=casting_commit,
        spec_path=evidence_spec_path,
        run_dir=fdir,
    )
    evidence_verdict = evidence_result["verdict"]
    evidence_provenance = list(evidence_result.get("provenance_records", []))
    # A v2.0 stream-skip means evidence verification was structurally
    # bypassed for this casting. It is persisted in the run's manifest, but
    # the lead reads THIS return — surfacing the record here is what makes
    # the bypass visible at the moment it happens rather than only to
    # whoever later opens the manifest.
    evidence_stream_skips = list(
        evidence_result.get("manifest_updates", {}).get("stream_skips", [])
    )

    # Audit-log per evidence file (two-channel audit: manifest +
    # handoffs.jsonl). Mirrors Phase 1/2/3 dual-channel pattern.
    for record in evidence_provenance:
        foundry_handoff(
            event="evidence_verified",
            source=f"castings/casting-{casting_id}-prompt.md",
            destination=record.get("evidence_path", ""),
            source_reread=True,
            summary=(
                f"casting {casting_id} evidence verdict={record.get('verdict')} "
                f"token={record.get('failure_token') or 'none'} "
                f"elapsed={record.get('elapsed_seconds')}s"
            ),
            information_loss=record.get("failure_detail") or "",
            project_root=project_root,
        )

    # D-149 — THE TALLY IS A RETURN VALUE, NOT A PRINT.
    #
    # This block used to end in a bare ``print(..., flush=True)``, carried
    # over from an "F0.5 stdout-summary precedent" that belongs to CLI
    # scripts, not to a handler. The MCP server speaks JSON-RPC over stdio:
    # stdout IS the protocol channel. One non-protocol line ahead of the
    # response frame and a conforming client's parser fails on the whole
    # message — and the only way to reach it was to pass ``casting_commit``,
    # which is precisely the path FR-017 exists to make reachable over MCP.
    # Wiring the evidence gate would therefore have broken the channel the
    # first time it fired.
    #
    # The tally is data the caller asked for, so it travels in the returned
    # dict beside ``evidence_verdict`` and ``evidence_provenance``. Nothing
    # in the handler tree writes to stdout now, and
    # ``test_no_handler_writes_to_the_protocol_channel`` derives that over
    # the whole installed package rather than over this one site.
    evidence_tally = {
        "accepted": sum(
            1 for r in evidence_provenance if r.get("verdict") == "accepted"
        ),
        "rejected": sum(
            1 for r in evidence_provenance if r.get("verdict") == "rejected"
        ),
        "failure_tokens": sorted(
            {
                r.get("failure_token")
                for r in evidence_provenance
                if r.get("failure_token")
            }
        ),
    }

    # Hard-reject on evidence verdict='rejected'. Skip path (v2.0)
    # falls through to scope-flag check; the manifest.stream_skips
    # record is the audit signal that evidence verification was
    # structurally bypassed for this run.
    if evidence_verdict == "rejected":
        return {
            "ok": False,
            "casting_id": casting_id,
            "failure_token": evidence_result["failure_token"],
            "failure_detail": evidence_result["failure_detail"],
            "evidence_provenance": evidence_provenance,
            "evidence_tally": evidence_tally,
            "evidence_spec_path": str(evidence_spec_path),
            "hint": (
                "Evidence re-execution rejected the casting. The teammate's "
                "committed log diverges from a clean re-execution of "
                "`# evidence-cmd:`. Re-run the command yourself, inspect "
                "the diff, and re-dispatch with corrected evidence."
            ),
        }

    # ============================================================
    # Phase 5 / EVID-02: per-requirement-coverage check (strictness
    # upgrade to EVID-01).
    #
    # Runs only when:
    #   1. evidence verification engaged AND verdict was "accepted"
    #      (skipped → v2.0 stream-skip routing; rejected → Phase 4
    #      already returned; both bypass this check)
    #   2. casting_req_ids is non-empty (zero-req castings — refactors,
    #      doc edits — legitimately need no per-requirement binding)
    #
    # Computes the set difference of casting requirement IDs against
    # the union of `evidence_for` lists across all provenance records.
    # Non-empty difference → reject with named missing IDs.
    #
    # casting_commit=None bypasses the entire enclosing block (Phase 4
    # backwards-compat shim — Pitfall 7); this check inherits.
    #
    # Why set(casting_req_ids) - bound_ids (not the reverse): "unbound"
    # = "in the casting but not bound by any artifact". The reverse
    # direction would surface "over-coverage" (artifact cites IDs not
    # in the casting), which 05-RESEARCH.md decided to silently drop
    # (closed-vocabulary minimization). Over-coverage is not an error.
    # ============================================================
    if (
        evidence_verdict == "accepted"  # don't double-reject after Phase 4 fail
        and casting_req_ids  # zero-req castings need no per-req binding
    ):
        bound_ids: set[str] = set()
        for record in evidence_provenance:
            for rid in record.get("evidence_for", []):
                bound_ids.add(rid)
        unbound = sorted(set(casting_req_ids) - bound_ids)
        if unbound:
            # Hard-reject with named missing IDs (SC#4 satisfied).
            return {
                "ok": False,
                "casting_id": casting_id,
                "failure_token": "EVIDENCE_REQUIREMENT_UNBOUND",
                "failure_detail": (
                    f"casting {casting_id} has no evidence artifact bound to "
                    f"requirement(s): {', '.join(unbound)}. Each committed "
                    f"evidence file must carry a `# evidence-for: <ids>` "
                    f"header listing the requirement IDs it demonstrates."
                ),
                "unbound_requirements": unbound,
                "evidence_verdict": evidence_verdict,
                "evidence_provenance": evidence_provenance,
                "evidence_tally": evidence_tally,
                "requirement_ids": casting_req_ids,
                "hint": (
                    f"Add a `# evidence-for: {', '.join(unbound)}` header "
                    f"line to the relevant evidence file(s) and re-commit. "
                    f"Multiple files may bind to the same requirement; one "
                    f"file may bind to multiple requirements (comma-separated "
                    f"list). See plugins/foundry/agents/teammate.md Step 11 "
                    f"for the canonical evidence-file format."
                ),
            }

    # Check for "out of scope" or "cut scope" mentions in the teammate report
    warning_phrases = [
        "out-of-scope",
        "out of scope",
        "intentionally skipped",
        "deferred",
        "partial coverage",
        "subset of",
        "core only",
        "manual validation",
        "follow-up",
    ]
    report_lower = completion_report.lower()
    scope_flags = [p for p in warning_phrases if p in report_lower]
    warning = None
    if scope_flags:
        warning = (
            f"Teammate completion report contains scope-flag phrases: {scope_flags}. "
            f"Do NOT accept this casting. Re-dispatch with explicit instruction to "
            f"complete the missing work. Build-green is necessary but NOT sufficient."
        )
    elif missing_citations:
        warning = (
            f"Completion report is missing citations for "
            f"{len(missing_citations)} requirement(s): {', '.join(missing_citations)}. "
            f"Every requirement ID in the casting's <spec_requirements> block must "
            f"have a corresponding citation in the completion report proving where it "
            f"was implemented. Do NOT accept this casting. Re-dispatch with "
            f"instruction: 'For each requirement ID (US-N, FR-N, etc.) cite the exact "
            f"path#Symbol where it was implemented.' Build-green is necessary but NOT sufficient."
        )
    elif unresolved_cites:
        # AC-006 — an unresolvable symbol is a defect, not a warning about
        # formatting. Named individually so the teammate can fix the cite
        # rather than re-scan the whole report.
        warning = (
            f"Completion report cites {len(unresolved_cites)} symbol(s) that resolve "
            f"nowhere in the tree: "
            f"{', '.join(c['cite'] for c in unresolved_cites)}. "
            f"Each is a defect from the mechanical symbol-resolution guard. Do NOT "
            f"accept this casting. Re-dispatch with instruction: 'Every path#Symbol "
            f"cite must name a symbol that exists in the named file.' A stale :line "
            f"hint is NOT the problem — the line component is never judged, so do not "
            f"run a cite-refresh sweep."
        )

    # Record the acceptance attempt as a handoff entry.
    # Use the raw path string to avoid macOS /tmp ↔ /private/tmp symlink
    # mismatches during relative_to computation.
    foundry_handoff(
        event="acceptance",
        source=f"castings/casting-{casting_id}-prompt.md",
        destination=f"casting-{casting_id}-accepted",
        source_reread=True,  # the MCP tool enforces it by requiring fresh hashes
        summary=f"casting {casting_id} acceptance check",
        information_loss=", ".join(scope_flags) if scope_flags else "",
        project_root=project_root,
    )

    return {
        "ok": warning is None,
        "casting_id": casting_id,
        "acceptance_criteria": acs,
        "requirement_ids": casting_req_ids,
        "missing_citations": missing_citations,
        "unresolved_symbol_cites": unresolved_cites,
        "must_verify": [
            f"Every AC above has a corresponding artifact/behavior in the completion report",
            f"Every requirement ID has a path#Symbol or file:line citation in the completion report",
            f"Every path#Symbol cite resolves in the tree (a stale :line hint is never a finding)",
            f"Build is green AND tests pass",
            f"No scope-flag phrases in the completion report",
            f"Research compliance check (if research_context applies): each recommendation honored",
        ],
        "warning": warning,
        "evidence_verdict": evidence_verdict,
        "evidence_provenance": evidence_provenance,
        "evidence_tally": evidence_tally,
        "evidence_stream_skips": evidence_stream_skips,
        "evidence_spec_path": (
            str(evidence_spec_path) if evidence_spec_path is not None else None
        ),
    }
