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

import re
from datetime import datetime, timezone
from pathlib import Path

from foundry_mcp.schemas.vocab import HANDOFF_EVENT_LEAD_FIX, REQUIREMENT_ID_RE
# The artifact leaf, `tools/artifacts.py`: this module's run-artifact primitives.
# They were reached at the top of the stack until the leaf existed — this module
# wanted a couple of utilities and imported a 15,000-line state machine to get
# them, which is what made the orchestrator the package's de-facto persistence
# layer. The bodies are the same bodies; only the module that defines them
# changed, and every hoist below happened for one reason stated four times.
#
# THE ARITHMETIC, ONCE, BECAUSE IT IS THE SAME EVERY TIME (fallout GI-033). The
# verifier and lifecycle layers are mutually unreachable at module top, so a
# symbol read from BOTH can live in neither — it belongs in a leaf. Each hoist
# below is a symbol that acquired a reader on the other side of that line.
#
#   ``_hash_file`` / ``_hash_str`` (fallout D-128, concern C-067) — defined
#     here, read by ``foundry_validate`` from this side and ``tools/evidence.py``
#     from the other. C-067 is casting 5 saying the same thing from the far end:
#     they closed their ``foundry_spawn`` edge by reading the leaf and could not
#     close this one, because the definitions were here.
#   ``declared_requirement_ids`` (fallout FR-063 / GI-033, D-191) — the same
#     move, held for a cycle because it needs the source module, the leaf and
#     the two AST pins asserting the function is defined exactly once
#     (``tests/test_handoff_records.py#test_neither_reader_derives_the_declared_set_inline``,
#     ``tests/test_evidence.py#test_no_reader_of_the_owned_set_derives_it_inline``),
#     and GI-026 requires a pin repointed in the same casting as the source
#     move. It is no longer imported HERE at all: its one reader in this module
#     was the acceptance door, which has left.
#   ``_append_handoff_record`` and ``record_handoff_event``
#     (fallout AC-061 / FR-063 / GI-033, D-192, concern C-107) — the ledger
#     writer and the body of the ``Foundry-Handoff`` door below. The acceptance
#     door records through them from the verifier side now;
#     ``record_lead_fix_handoff`` below, ``tools/concerns.py`` and
#     ``orchestration/directives.py`` reach them from this side.
#
# WHAT LEFT AND WHAT STAYED, AND WHY THE SPLIT IS THE LAYERING FACT.
# ``foundry_accept_casting`` is no longer in this module: it RUNS
# ``verify_evidence``, GI-033's violation column is "any lifecycle module
# importing a verifier module", and C-107 measured that what it reached was not
# a symbol but the evidence ENGINE — so the edge could only be reversed or
# eliminated, and moving the door to ``tools/evidence.py`` eliminates it.
# ``check_reported_prompt_hash`` and ``foundry_spec_hash`` went to the leaf with
# it, the first because ``orchestration/fix_gate.py`` reads it from this side
# (CT-011 / AC-030 exists to keep those two gates identical), the second because
# the registrar binds it as ``Foundry-Spec-Hash``. The ``Foundry-Handoff`` door
# below did NOT go: its first rung reserves the ``lead_fix`` token to the
# server, and that is a policy of the DOOR rather than of the writer —
# ``record_lead_fix_handoff`` reaches the same writer and writes exactly the
# record the door refuses.
#
# ``cited_requirement_ids`` STAYS, for the arithmetic rather than for symmetry:
# its readers are this module and ``foundry_validate``, both lifecycle, so
# nothing forces it out. The prose at each end cites the other by module.
#
# The bodies moved unchanged, and the ones named here are still this module's to
# USE: the import is the fix, not a facade.
from foundry_mcp.tools.artifacts import (
    _append_handoff_record,
    _hash_str,
    record_handoff_event,
)


#: The one line shape in a `<spec_requirements>` excerpt that NAMES a
#: requirement id without CITING it: the `Maps to:` back-pointer F0.5 DECOMPOSE
#: transcribes under each requirement row, whose object is the user story that
#: requirement serves.
#:
#: It is a property OF the row, copied verbatim out of the spec — not a
#: sentence the casting wrote about a requirement — and it is the only field of
#: its kind in the grammar: a scan of every sub-bullet field name in the spec
#: finds `Maps to` and nothing else. The prefix before it is the same
#: structural-markdown run `artifacts.py#_DECLARED_REQUIREMENT_ID_RE` allows
#: — the sibling rule moved to the leaf under D-191 and this one did not, for
#: the reason the import banner records — so the two rules read a line's
#: opening the same way.
_CROSS_REFERENCE_LINE_RE: re.Pattern[str] = re.compile(
    r"^[\s>|*+#-]*Maps to:", re.IGNORECASE
)


def cited_requirement_ids(block_text: str) -> list[str]:
    """The requirement IDs a casting's excerpt MENTIONS, sorted and deduped.

    THE SECOND POPULATION, AND WHY THERE ARE TWO (D-181)
    ----------------------------------------------------
    ``artifacts.py#declared_requirement_ids`` answers "which requirements is
    this casting ANSWERABLE for" and has three consumers that need exactly that
    reading — the acceptance gate's citation window, EVID-02's per-requirement
    evidence binding, and the legacy ownership fill in
    ``scripts/migrate-archive.py``. Every one of them turns an ID in that list
    into a DEMAND on a teammate, which is why it judges position: an ID quoted
    inside another requirement's prose is not work this casting owes.

    F0.9's ownership dimension asks a different question.
    `forge-specs/foundry-run-fallout/spec.md` AC-001 — "a manifest whose
    casting CITES an id in `spec_text` that is absent from its
    `requirement_ids` is refused" — OT-001 ("whose prose CITES an id outside
    that list") and FR-040 (same verb, both directions) are about what the
    excerpt MENTIONS, not what it assigns. One derivation served both, so
    the three shapes below were invisible to F0.9 and a casting citing an id it
    did not own validated clean::

        - **FR-009**: persist ids, the way FR-007 already demands   mid-line
        This casting also touches what AC-042 states about span.    mid-prose
        The `AC-042` rule, restated.                                in code

    Widening the DECLARATION rule to reach them is the fix this function
    exists to avoid: that is D-180 verbatim, where a bare ``findall`` made the
    acceptance gate demand evidence for a requirement another casting owned and
    left a teammate no way through but a knowingly false ``# evidence-for:``
    header. Two questions, two derivations, each read by the callers that ask
    it.

    THE ONE EXEMPTION, AND WHY IT IS NOT A NARROWING (start.md F0.5)
    ---------------------------------------------------------------
    A ``Maps to:`` line is skipped, and it is the only thing skipped. The rule
    F0.5 states in its own words is that "prose that merely quotes an id is not
    a claim to own it"; a ``Maps to:`` line is not even prose the casting
    wrote. It is DECOMPOSE transcribing the spec's own back-pointer from a
    requirement to the user story it serves, so its object is a fact about the
    CITED requirement rather than a claim by the casting carrying it.

    Counting it would not make F0.9 stricter, it would make F0.9 unpassable.
    Driven on this run's own manifest: six of twelve castings carry a
    ``Maps to:`` naming a user story they do not own, and the only exit the
    refusal offers — add the id to ``requirement_ids`` — drives US-001 to a
    span of five, US-007 to six, US-011 to five and US-012 to six, every one of
    them above the span that spec's AC-042 refuses without a recorded reason.
    AC-001 and AC-042 would then be mutually destructive: the manifest
    satisfying one is the manifest the other rejects. An exemption is what
    keeps both reachable.

    A CITE IS A SUPERSET OF A DECLARATION, BY CONSTRUCTION
    -----------------------------------------------------
    Every declaration is also a cite: a subject-position ID is on a line this
    scan reads, and a ``Maps to:`` line can never carry one, because
    ``artifacts.py#_DECLARED_REQUIREMENT_ID_RE`` needs the ID immediately after the
    structural markdown and those lines open with the field name instead. So
    skipping cross-references cannot drop a declaration, and F0.9 comparing
    this list against ``requirement_ids`` in BOTH directions is strictly
    kinder in one and strictly stricter in the other — never inconsistent
    between them. That symmetry is load-bearing: a forward check on cites with
    a reverse check on declarations gives a cited-but-undeclared ID no
    accepting state at all, since owning it trips one refusal and disowning it
    trips the other.

    Deduped and sorted for the same reason the sibling is: the caller compares
    it as a set and prints it to a lead.
    """
    ids: set[str] = set()
    for line in block_text.splitlines():
        if _CROSS_REFERENCE_LINE_RE.match(line):
            continue
        ids.update(REQUIREMENT_ID_RE.findall(line))
    return sorted(ids)


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

    return record_handoff_event(
        event=event,
        source=source,
        destination=destination,
        source_reread=source_reread,
        summary=summary,
        information_loss=information_loss,
        project_root=project_root,
    )
