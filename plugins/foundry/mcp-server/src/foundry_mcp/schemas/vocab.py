"""Canonical vocabulary for foundry streams, defects, and findings (FR-013).

Single source of truth for every closed vocabulary the run protocol puts on
the wire or persists. Before this module the same enums were re-typed in six
places that drifted independently:

    | copy                        | location                                  |
    |-----------------------------|-------------------------------------------|
    | client schema, `source`     | server.py Foundry-Defect inputSchema      |
    | client schema, `defect_type`| server.py Foundry-Defect inputSchema      |
    | handler valid set           | foundry_orchestrator.VALID_STREAMS        |
    | sync coercion set           | foundry_orchestrator `valid_sources`      |
    | marker-clear lists          | foundry_orchestrator grind_start/assay    |
    | instrumentation roster      | measure-run.KNOWN_PHASE9_STREAM_IDS       |

D-071 found a SEVENTH copy the original survey missed — `schemas/findings.py`,
the report schemas the `Validate-Report` tool serves. It read none of this
module and still required the `severity` axis the effort abolished, so the
validator two shipped skills instruct a stream to run rejected that stream's
own documented output. It now derives from here like every other copy.

Wave 1 ships this module and derives `measure-run.py`'s roster from it. The
remaining consumers are wired by later castings against the names exported
here; the export surface is a cross-casting contract and must not be renamed.

PURITY RULE (load-bearing)
--------------------------
Pure data plus pure predicates. Standard library only. No filesystem, no
network, no subprocess, and no import of `foundry_mcp.tools.*` or `server`.
This module sits at the bottom of the import graph so that the stdlib-only
CLIs under `plugins/foundry/scripts/` can import it through their `sys.path`
shim without dragging in the MCP SDK.

Symbol and cite resolution is therefore NOT performed here. A caller that has
resolved a cite hands the outcome in as a field on the finding mapping; that
is what keeps this module I/O-free.

FINDING MAPPING FIELDS
----------------------
Every predicate takes one `Mapping[str, object]` and returns `str | None` —
the matched class name, or `None`. Predicates are pure and never raise: a
missing, `None`, or wrong-typed key simply means "this class does not match".
The fields read are:

    description     str   the finding's prose; the primary text surface
    spec_ref        str   requirement reference, e.g. "AC-002"; non-empty
                          is itself a spec-required-behaviour claim
    target_kind     str   what the finding is about. "comment" means the
                          subject is a code comment; ANY other present value
                          matches the NON_COMMENT denylist entry. Callers
                          MUST populate this — its absence is a caller bug,
                          not a licence to demote (see AC-002 note below).
    symbol_resolved bool  caller-supplied cite-resolution outcome. False
                          matches UNRESOLVABLE_CITE; absent means unknown.
    line_hint_stale bool  caller-supplied line-hint comparison outcome.

`description`-driven matching is heuristic by nature — these are prose
classes, not machine types.

An earlier revision of this docstring argued the scheme's safety "does not
rest on the [prose] regexes being exhaustive; it rests on the precedence rule
below." D-093 showed that is true in only one direction. The precedence rule
protects the DEMOTE direction: a denylist hit keeps a finding a defect no
matter what the observation regexes say. It says nothing about the PROMOTE
direction, where a caller that refuses a defect filing on an observation-class
match alone turns a false positive here into a blocked real defect. Callers
owe that direction a fail-safe of their own; this module owes both directions
predicates that match the vocabulary real findings are written in, which is
what D-090 and D-093 widened the denylist to do.

PRECEDENCE RULE (AC-002 — the never-weaken guarantee)
-----------------------------------------------------
`never_demote_class` OUTRANKS `observation_class`. A finding matching both is
a DEFECT, and the denylist match is what a caller reports so the audit
tripwire can name exactly which entry fired. When SEVERAL denylist entries
match one finding, the entry reported is the most specific one — the generic
NON_COMMENT catch-all is evaluated last, so it never masks the entry a
refusal also names (D-083; see `_NEVER_DEMOTE_PREDICATES`). Because of this ordering the
denylist patterns are deliberately biased toward OVER-matching: a false
denylist hit costs one observation that stays a defect, while a false miss
would demote a real security or spec-behaviour finding. Only the second
failure mode is unacceptable.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from fnmatch import fnmatchcase
from types import MappingProxyType

# ---------------------------------------------------------------------------
# Stream vocabularies.
# ---------------------------------------------------------------------------

# CLOSED VOCABULARY — the canonical 15-id verification-stream roster.
# These are the UPPERCASE spellings used by instrumentation and by phase
# planning documents. Byte-equal to the roster measure-run.py carried as
# KNOWN_PHASE9_STREAM_IDS, which was the correct superset before this module
# existed; measure-run.py now derives its roster from here rather than
# re-typing it. Extend only via phase-level RFC.
CANONICAL_STREAM_IDS = frozenset(
    {
        "TRACE", "FLOW_TRACE", "PROVE", "RESEARCH_AUDIT", "COVERAGE_DIFF",
        "TEST-01", "SIGHT", "TEST",
        "EVID-01", "EVID-02",
        "INTV-01", "TYPE-01", "TYPE-02",
        "PROBE-01", "INTENT-01",
    }
)  # 15 items

# CLOSED VOCABULARY — the lowercase spellings the protocol actually puts on
# the wire (Foundry-Stream's `stream` argument) and persists (a defect
# record's `source`). Superset of the orchestrator's VALID_STREAMS: every
# value accepted before this module keeps working (NFR-002, no narrowing).
# `test01` and `flow_trace` are the two values FR-013/AC-018 add.
# Extend only via phase-level RFC.
STREAM_WIRE_IDS = frozenset(
    {
        "trace",
        "prove",
        "sight",
        "test",
        "probe",           # canonical id is PROBE-01, not PROBE
        "research_audit",
        "flow_trace",
        "coverage_diff",
        "test01",          # canonical id is TEST-01; added by FR-013 / AC-018
    }
)  # 9 items

# Total over STREAM_WIRE_IDS; every value is a member of CANONICAL_STREAM_IDS.
# This mapping is the case half of the FR-018 instrumentation repair: the
# roster is UPPERCASE while every persisted `source` value is lowercase, so
# renaming the key alone would still have matched nothing.
WIRE_TO_CANONICAL: Mapping[str, str] = MappingProxyType(
    {
        "trace": "TRACE",
        "prove": "PROVE",
        "sight": "SIGHT",
        "test": "TEST",
        "probe": "PROBE-01",
        "research_audit": "RESEARCH_AUDIT",
        "flow_trace": "FLOW_TRACE",
        "coverage_diff": "COVERAGE_DIFF",
        "test01": "TEST-01",
    }
)

# CLOSED VOCABULARY — legal `source` values on a defect record: every stream
# wire id, plus the two non-stream filers. `assay` and `temper` are carried
# by server.py's existing Foundry-Defect source enum and NFR-002 forbids
# dropping a value the surface accepts today.
# Extend only via phase-level RFC.
DEFECT_SOURCE_IDS = frozenset(STREAM_WIRE_IDS | {"assay", "temper"})  # 11 items

# The two legal defect sources that are NOT verification streams. ASSAY
# adjudicates and TEMPER probes; both file defects, neither ever files a
# stream coverage record, so their absence from CANONICAL_STREAM_IDS is
# correct rather than an omission.
NON_STREAM_DEFECT_SOURCES = frozenset(DEFECT_SOURCE_IDS - STREAM_WIRE_IDS)  # 2 items

# Total over DEFECT_SOURCE_IDS — the resolver for a defect record's `source`.
#
# D-091: this table is a SIBLING of WIRE_TO_CANONICAL, not an extension of it.
# The two vocabularies answer different questions — "which stream reported
# coverage" (9 wire ids) versus "who filed this defect" (11 sources) — and
# measure-run.py resolved the second against the first, so every `assay`- and
# `temper`-filed defect was discarded from per_stream_defects AND reported as
# PHASE9_UNKNOWN_STREAM, a failure token naming a value this protocol declares
# legal. Streams keep their canonical stream id; the two non-stream filers map
# to their own UPPERCASE names, which are deliberately NOT members of
# CANONICAL_STREAM_IDS.
DEFECT_SOURCE_TO_CANONICAL: Mapping[str, str] = MappingProxyType(
    {
        **WIRE_TO_CANONICAL,
        **{source: source.upper() for source in sorted(NON_STREAM_DEFECT_SOURCES)},
    }
)

# The spellings `canonical_defect_source` accepts by identity. NOT
# DEFECT_SOURCE_TO_CANONICAL.values(): only nine canonical stream ids have a
# wire spelling, so deriving from the mapping's values would REJECT the six
# that do not (EVID-01, EVID-02, INTV-01, TYPE-01, TYPE-02, INTENT-01) even
# though canonical_stream_id accepts them today. NFR-002 forbids that
# narrowing — a defect archive already records those ids verbatim.
_CANONICAL_DEFECT_SOURCES = frozenset(
    CANONICAL_STREAM_IDS | {source.upper() for source in NON_STREAM_DEFECT_SOURCES}
)  # 17 items

# ---------------------------------------------------------------------------
# Defect vocabularies.
# ---------------------------------------------------------------------------

# CLOSED VOCABULARY — legal `defect_type` values. The first ten are
# server.py's existing enum, preserved verbatim (NFR-002). FR-013 adds
# PARTIAL and the pair "MISPLACED/ARCHITECTURAL_PLACEMENT": both spellings
# are live in agent contracts that must not break — agents/tracer.md and
# agents/assayer.md instruct streams to persist type "ARCHITECTURAL_PLACEMENT"
# while using MISPLACED as the verdict word in the same tables. Both are
# members; canonical_defect_type() folds MISPLACED onto the longer spelling.
# Extend only via phase-level RFC.
DEFECT_TYPES = frozenset(
    {
        "MISSING",
        "WRONG",
        "THIN",
        "HOLLOW",
        "UNWIRED",
        "BROKEN",
        "FAIL",
        "RESEARCH_DEVIATION",
        "COVERAGE_INCOMPLETE",
        "THIN_MIGRATION",
        "PARTIAL",
        "ARCHITECTURAL_PLACEMENT",
        "MISPLACED",
    }
)  # 13 items

# The canonical spelling for each member that has an alias. Every other
# member is its own canonical form.
_DEFECT_TYPE_ALIASES: Mapping[str, str] = MappingProxyType(
    {"MISPLACED": "ARCHITECTURAL_PLACEMENT"}
)

# CLOSED VOCABULARY — the classification axis distinguishing a defect from a
# non-defect finding at filing time. Extend only via phase-level RFC.
FINDING_CLASSES = frozenset({"DEFECT", "OBSERVATION"})  # 2 items

# ---------------------------------------------------------------------------
# The evidence tier (GI-001 / CT-001 / FR-004).
#
# WHY THIS IS NOT THE ABOLISHED GRADE COMING BACK
# -----------------------------------------------
# D-041 removed `severity` because a work-effort grade (minor/major/critical)
# invites a real defect to be quietly downgraded rather than fixed, and
# `schemas/findings.py` still enforces its absence with
# `additionalProperties: False`. `tier` measures something else entirely: NOT
# how much the defect matters, but WHAT THE FILING STREAM ACTUALLY DID.
#
#   LIVE       the stream drove the door and observed the wrong result. The
#              description carries the reproduction.
#   LATENT     the stream looked for the failure and did not find one — a gap
#              reasoned about, not reproduced. The filing must carry a
#              `reproduction_attempted` statement naming what was driven and
#              what it found (`reproduction_attempted_problem` below is the
#              check).
#   HARDENING  the stream DROVE a probe of its own devising and observed a
#              wrong result that no requirement asks about. Same evidence
#              standard as LIVE — a reproduction, not a worry — and a
#              different SUBJECT: nothing in the spec is unmet, so nothing is
#              owed before the run may move.
#
# All three tiers are DEFECTS and all three get fixed. FR-006 and CT-008 give
# ONE gate rule and it has no per-door exception in it: INSPECT-clean, ASSAY,
# TEMPER, NYQUIST and DONE ALL pass when the only open defects are LATENT, and
# all five refuse on an open LIVE or unknown-tier defect (FR-051). HARDENING
# joins LATENT on the passing side of that same rule — `BLOCKING_TIERS` is
# untouched by this member, which is the whole of its gate semantics. So the
# tier does not select a gate. What it decides is TASKING — which defects a
# GRIND cycle must clear before the run can move, and which escalation-clearing
# cycles count (ST-001: a LATENT instance does not reset the clean-cycle
# counter) — and REPORTING: a LATENT defect stays open, tracked, and named in
# the F6 backlog, and so does a HARDENING one, in a backlog section of its own
# beside it (AC-024).
#
# WHY HARDENING IS NOT THE GRADE RETURNING UNDER A NEW NAME EITHER (GI-014)
# -------------------------------------------------------------------------
# The paragraph below forbids "a third value that means 'less important than
# LIVE'", and that is the right test to apply — so apply it. HARDENING does not
# say a finding matters less. It says the finding is OFF-SPEC: GI-030 gives
# PROVE an adversarial half at INSPECT on a TEMPER-off run, and a probe that
# finds a real wrong result outside the requirements matrix has, until now, had
# two homes and both are wrong. Filed LIVE, it holds every gate shut over
# behaviour no requirement asks for, which is how a run learns to stop driving
# probes. Dropped, it is the finding nobody records.
#
# THE DISCRIMINATOR IS `spec_ref`, AND IT IS MECHANICAL (GI-028 / CT-012). A
# HARDENING filing carrying ANY `spec_ref` is REFUSED at both doors — not
# down-ranked, refused — because a spec reference is the statement that a
# requirement IS at stake, and a record that makes that statement while sitting
# in the non-blocking tier is the downgrade this axis exists to prevent. That
# is a door rule and lives at the doors; what lives here is the member.
#
# AND THE DENYLIST STILL OUTRANKS IT (GI-004). `never_demote_class` is checked
# before the tier and is unchanged by this member: a reachable raise, a forged
# evidence log or a security-property claim filed as HARDENING is refused and
# the audit tripwire fires, exactly as it is when filed as LATENT. A tier is
# never a route around the never-weaken guarantee.
#
# PROMOTION IS A NEW FILING, NEVER A RE-TIER IN PLACE (GI-022 / ST-006). A
# HARDENING record that turns out to break a requirement is superseded by a
# filing that cites it through `supersedes` and carries its own tier and its own
# reproduction. Re-tiering in place would rewrite what a stream said it saw.
#
# D-148 — this paragraph used to hand ASSAY an exception, saying it blocked on
# either tier while the other doors passed on LATENT. No shipped gate ever did
# that: `foundry_orchestrator.BLOCKING_TIERS` is LIVE plus the unknown
# sentinel, and all five doors ask one helper. The rule was invented HERE, in
# the file that DEFINES the tier — which is the file a maintainer reads to
# learn what the tier means, so a rule invented here is a rule someone
# eventually implements. State the gate rule the requirements give, or state
# none.
#
# A grade would let a stream write "minor" and move on; this cannot, because
# neither value is an excuse — it is a statement about evidence the stream is
# answerable for.
#
# Extend only via phase-level RFC — and never with a third value that means
# "less important than LIVE", which is the grade returning under a new name.
# ---------------------------------------------------------------------------

# CLOSED VOCABULARY — the evidence tier a filing stream sets on every defect.
DEFECT_TIERS = frozenset({"LIVE", "LATENT", "HARDENING"})  # 3 items

#: FR-014 / GI-014 — the off-spec driven failure's own name, so a door, a
#: report section and a filing stream spell it identically. A member of
#: DEFECT_TIERS above; declared separately for the same reason the observation
#: class names are, which is that a consumer naming the tier in a refusal or a
#: section key must read it rather than re-type it.
TIER_HARDENING = "HARDENING"

#: READ-SIDE SENTINEL ONLY — never written by a filing door, and deliberately
#: NOT a member of DEFECT_TIERS. A record persisted before this release has no
#: `tier` key at all; every reader resolves that absence to this value rather
#: than guessing. FR-051: an unknown-tier defect blocks exactly like LIVE and
#: is reported in its own column — reading it as LATENT would silently clear
#: gates on records nobody ever classified.
TIER_UNKNOWN = "unknown"

# The full READ vocabulary: what a reader may see, as opposed to what a door
# may write. Derived from DEFECT_TIERS so adding a tier needs one edit — which
# is what FR-014 just proved: HARDENING joined the set above and every reader
# that walks this one, including the report's tier cross-tab, gained the column
# without a second edit.
DEFECT_TIER_OR_UNKNOWN = frozenset(DEFECT_TIERS | {TIER_UNKNOWN})  # 4 items

#: CT-008 / AC-022 — the tiers that BLOCK a gate. LIVE is a reachable failure
#: and blocks as it always did; `unknown` is a record no stream has classified
#: and blocks identically until one re-files it with a tier (FR-051). LATENT
#: and HARDENING block nothing: both stay open, tracked, and named in their own
#: F6 backlog section (FR-006 / GI-014).
#:
#: fallout GI-033 / D-021 / D-035 (concern C-027) — WHY IT IS HERE AND NOT AT
#: THE GATE. GI-014's own applies-to column has always named this tuple beside
#: DEFECT_TIERS in this module, and the layering guard is what forced the move
#: to actually happen: it was declared in `orchestration/gates.py`, a VERIFIER
#: module, while `orchestration/guidance.py` — LIFECYCLE — needed the same
#: membership to count blocking defects for the status display. A symbol both
#: layers read can live in neither, so it lives in the leaf that declares the
#: vocabulary it is derived from.
#:
#: A TUPLE, NOT A FROZENSET, and deliberately so: it is an ORDERED pair that a
#: refusal reads out in this order ("LIVE, then unknown"), and the order is the
#: one a reader of that sentence expects. Membership is the only test any
#: consumer applies, so nothing rests on the container beyond that.
#:
#: Derived from `TIER_UNKNOWN` rather than spelling "unknown" a second time —
#: the sentinel has exactly one spelling and this is a reader of it.
#:
#: Extend only via phase-level RFC. In particular a new tier is NOT a member
#: by default: DEFECT_TIERS grew by HARDENING and this tuple deliberately did
#: not, which is the whole of AC-022's second clause.
BLOCKING_TIERS = ("LIVE", TIER_UNKNOWN)  # 2 members

#: The tiers whose filings owe a `reproduction_attempted` statement (CT-001 /
#: FR-004). ORDERED, because the wire descriptions that name them read them
#: out in this order.
#:
#: DERIVED as "every tier that is not the one you drove to a spec-required
#: failure": LIVE puts its reproduction in the description because the door it
#: drove IS the requirement, and the other two each owe the field for their own
#: reason — LATENT because the negative result is the only evidence there is,
#: HARDENING because a probe nobody asked for is trusted on nothing else.
#:
#: fallout D-171 — WHY THIS IS HERE AND NOT AT A DOOR. Two published wire
#: strings scope this obligation, `server.py`'s two filing-door schemas and
#: `schemas/findings.py`'s finding item, and both stated it as LATENT-only long
#: after HARDENING started owing it — the same stale sentence D-163 and D-164
#: found on the PROVE and TRACE skills. Each door computing "which tiers owe
#: this" for itself is how one of them keeps being the last to hear that the
#: set changed, and top convention 3 is that a closed vocabulary lives here and
#: every consumer derives from it, never a second hand list.
#:
#: `server.py#_TIERS_OWING_A_REPRODUCTION` is that second list and it still
#: exists: it is casting 2's file and repointing it is casting 2's edit, raised
#: as a concern rather than reached across a key_file boundary. Until it lands
#: the two agree by construction, because this expression is the one it spells.
TIERS_OWING_A_REPRODUCTION = tuple(
    tier for tier in sorted(DEFECT_TIERS) if tier != "LIVE"
)  # 2 members


def defect_tier(record: Mapping[str, object]) -> str:
    """The tier a defect record READS as — never raises, never guesses.

    A missing key, a `null`, a non-string and a string outside DEFECT_TIERS
    all resolve to TIER_UNKNOWN. That last case is deliberate: a record
    carrying `tier: "MINOR"` is not a tier this protocol knows, and coercing
    it onto LATENT would be exactly the silent downgrade the axis exists to
    prevent. Total over DEFECT_TIER_OR_UNKNOWN.
    """
    value = record.get("tier")
    return value if isinstance(value, str) and value in DEFECT_TIERS else TIER_UNKNOWN


#: Statements that name no evidence at all. A filing on either tier that owes
#: this field must say what was DRIVEN and what it produced; these are the
#: spellings of "I did not". Matched against the whole stripped, lowercased
#: statement, so a real sentence that happens to contain "none" is unaffected.
#: Extend only via phase-level RFC.
REPRODUCTION_PLACEHOLDERS = frozenset(
    {"", "-", "--", "n/a", "n.a.", "na", "nil", "none", "no", "not attempted",
     "tbd", "todo", "unknown"}
)  # 13 items

#: The floor on a `reproduction_attempted` statement. Twenty characters is not
#: a quality bar — it is the shortest string that can carry a subject and a
#: negative result ("AST sweep finds 0 sites" is 23). Anything shorter is a
#: token, and a token is what REPRODUCTION_PLACEHOLDERS already refuses.
REPRODUCTION_ATTEMPTED_MIN_CHARS = 20


def reproduction_attempted_problem(statement: object) -> str | None:
    """Why this `reproduction_attempted` statement is unacceptable, or None.

    CT-001 / FR-004: the server refuses a LATENT filing without one. This is
    the named check the two filing doors call so that "a statement naming what
    was driven and found nothing" is enforced in ONE place rather than
    re-spelled at each door — the drift shape this whole module exists to stop.

    Returns None when the statement is acceptable, else the refusal reason,
    phrased for the `error` field of the door's own refusal shape. Never
    raises: the JSON layer can hand this anything.

    THE SENTENCE IS TIER-NEUTRAL, AND THAT IS THE WHOLE POINT (D-115 / CT-012).
    ---------------------------------------------------------------------------
    Two tiers reach this rung and they owe DIFFERENT evidence: a LATENT filing
    owes a negative result ("what I drove and did not find"), a HARDENING filing
    owes a probe it drove and the wrong result it saw. `foundry.py` already
    picks between `_LATENT_REPRODUCTION_HINT` and `_HARDENING_REPRODUCTION_HINT`
    on the filing's own tier for exactly that reason — "a single hint would tell
    half its readers to write the wrong thing".

    This function does not know the tier and must not name one. It used to: the
    placeholder branch read "A LATENT filing must say what was driven and what
    it found (e.g. 'AST sweep of both roots finds 0 sites')", and a HARDENING
    filing missing its reproduction was refused with that sentence sitting
    BESIDE the correct HARDENING hint. A negative-result AST sweep is precisely
    the evidence a HARDENING filing must NOT offer, so the refusal instructed
    the stream to satisfy it with the one thing that would make the filing
    wrong — the two halves of one refusal contradicting each other.

    Taking a `tier` argument was the alternative and it is worse: this is the
    ONE check both doors share, the tier-specific wording already exists at the
    doors where the tier is known, and re-spelling it here would put two
    statements of one instruction in two modules — the drift shape this module
    exists to stop. So the error states the OBLIGATION (a statement of what was
    driven and what it produced, which both tiers owe) and the hint beside it
    states which evidence that is.
    """
    if not isinstance(statement, str):
        return (
            f"reproduction_attempted must be a string naming what was driven "
            f"and what it found, got {type(statement).__name__}"
        )
    stripped = statement.strip()
    if stripped.lower() in REPRODUCTION_PLACEHOLDERS:
        return (
            f"reproduction_attempted is the placeholder {stripped!r}, which "
            f"names no evidence. The tier you filed under owes a statement of "
            f"what was DRIVEN and what it produced; the hint beside this names "
            f"which evidence that tier owes."
        )
    if len(stripped) < REPRODUCTION_ATTEMPTED_MIN_CHARS:
        return (
            f"reproduction_attempted is {len(stripped)} characters; at least "
            f"{REPRODUCTION_ATTEMPTED_MIN_CHARS} are needed to name what was "
            f"driven and what it produced. The hint beside this names the "
            f"evidence the tier you filed under owes."
        )
    return None


# ---------------------------------------------------------------------------
# Escalation lifecycle (FR-002 / FR-003 / FR-028 / ST-001 / ST-002).
#
# A CLASS escalates when the same root cause recurs, and leaves that state by
# exactly two doors. Both are recorded, because "why did this class stop being
# escalated" is unanswerable from a status flag alone — and an unanswerable
# exit is how a class silently stops receiving structural packets while its
# instances keep arriving.
# ---------------------------------------------------------------------------

# CLOSED VOCABULARY — the two states a defect class can be in once escalation
# has looked at it. Extend only via phase-level RFC.
#
# THE MEMBERS ARE NAMED HERE AND THE FROZENSET IS BUILT FROM THE NAMES, so the
# vocabulary and the two comparands every reader asks about cannot drift apart:
# there is no second place in the tree where either string is typed.
ESCALATION_STATUS_ESCALATED = "ESCALATED"
ESCALATION_STATUS_CLEARED = "CLEARED"
ESCALATION_STATUSES = frozenset(
    {ESCALATION_STATUS_ESCALATED, ESCALATION_STATUS_CLEARED}
)  # 2 items


def _escalation_status_member(entry: object) -> str | None:
    """The vocabulary member this class entry's ``status`` spells, or None.

    THE ONE MEMBERSHIP TEST IN THE TREE. Both public functions below are thin
    over it, so "which values are the vocabulary" is decided in exactly one
    place and `escalation_status` and `escalation_status_is_unknown` cannot
    answer inconsistently about the same entry.

    None means the entry carries no status the vocabulary spells — because it
    is not a mapping at all, because the key is absent, or because the value is
    `null`, `""`, a non-string, or a string such as `"cleared"` or `"BOGUS"`
    that nothing in this system writes.
    """
    if not isinstance(entry, dict):
        return None
    raw = entry.get("status")
    if isinstance(raw, str) and raw in ESCALATION_STATUSES:
        return raw
    return None


def escalation_status(entry: object) -> str:
    """One class's persisted status, resolved against the CLOSED vocabulary.

    Returns a member of `ESCALATION_STATUSES` and nothing else, so no caller
    ever compares a raw persisted value to anything. Three ways in, ONE way
    out:

      * a member of the vocabulary          -> itself
      * absent, or `null`                   -> ESCALATED (a class nothing has
                                               cleared has not been cleared)
      * present and NOT a member, or an
        entry that is not a mapping at all  -> ESCALATED (D-210 / D-212)

    WHY IT LIVES IN vocab.py AND NOT BESIDE ONE OF ITS CALLERS (D-214 / D-215)
    --------------------------------------------------------------------------
    It lived in `tools/foundry_orchestrator.py`, PRIVATE to that module, while
    `escalation.json` has THREE readers: the orchestrator's two deciding reads,
    `tools/foundry_report.py#_read_escalated_classes`, and
    `scripts/measure-run.py#_read_escalation`. The two the resolver could not
    reach each grew their own opinion of the same field, and each got it wrong
    in its own way.

    The report read `status if isinstance(status, str) else "ESCALATED"`, so
    ANY string rendered verbatim into the row and fell into neither `by_status`
    bucket: on classes {"clean-class": CLEARED, "bogus-class": "BOGUS",
    "live-class": ESCALATED} the section's `count` was 3 while `by_status`
    summed to 2 and the row read "BOGUS" (D-214). measure-run held
    `if not isinstance(entry, dict): continue` ONE LINE ABOVE its own
    `unknown_status` counter, so on classes {"K1": {"status": "ESCALATED"},
    "K2": "just a string", "K3": ["ESCALATED"], "K4": null} the CLI printed
    `{"classes": 4, "by_status": {"CLEARED": 0, "ESCALATED": 1},
    "unknown_status": 0}` — three of four classes vanished from the census
    while `Foundry-Gate('done')` blocked on all of them (D-215).

    That is ONE root cause with three copies, and it recurred for three
    consecutive cycles (20, 21, 22) as the class
    `closed-vocabulary-not-enforced-on-the-deciding-read` because each cycle
    fixed a copy. The fix is the PLACEMENT, not the logic: the resolver is
    public, it lives with the vocabulary it enforces, every reader imports it,
    and `tests/test_vocab.py` discovers the readers by AST over the shipped
    tree — so a FOURTH reader that spells `"ESCALATED"` itself fails a test
    instead of shipping a fourth opinion.

    OUT OF VOCABULARY FAILS CLOSED. CLEARED is terminal: it retires a class
    from structural work for the rest of the run, and no value nothing in this
    system writes should be able to buy that.
    """
    member = _escalation_status_member(entry)
    return ESCALATION_STATUS_ESCALATED if member is None else member


def escalation_status_is_unknown(entry: object) -> bool:
    """True when `escalation_status` had to DEFAULT rather than read a member.

    The census column `measure-run.py` prints as `unknown_status`: how many
    classes carried no status the vocabulary spells and were therefore reported
    in the state they were written in rather than in one they declared.
    Derived from `_escalation_status_member` for exactly the reason the
    resolver is — a caller answering this by re-testing the field would be the
    fourth private opinion of it (D-215).

    NOT the same question as `escalation_status(e) == ESCALATION_STATUS_
    ESCALATED`: a class that genuinely records ESCALATED answers True to that
    and False to this. Counting the defaults separately is what keeps
    `classes == sum(by_status.values())` true while still saying how many of
    those classes never declared anything.
    """
    return _escalation_status_member(entry) is None

# CLOSED VOCABULARY — how a class reached CLEARED. Machine-readable per
# FR-028, so the F6 report can say which door each class left by.
#
#   clean_cycles  ST-001 — LIVE_CLEAN_CYCLES_TO_CLEAR consecutive INSPECT
#                 cycles drew zero LIVE instances of the class.
#   budget        ST-002 — the structural-pass budget was exhausted. Open LIVE
#                 instances stay blocking and are fixed per-instance; open
#                 LATENT instances go to the F6 named backlog.
#
# Extend only via phase-level RFC.
ESCALATION_EXIT_REASONS = frozenset({"clean_cycles", "budget"})  # 2 items

# FR-002 — one structural pass plus one retry, and then no more. The cap is
# what stops a class that structural work cannot fix from consuming every
# remaining cycle.
STRUCTURAL_PASS_BUDGET = 2

# FR-003 / ST-001 — consecutive server-counted INSPECT cycles with zero LIVE
# instances before an escalated class clears. LATENT instances do not reset
# the count (ST-001), which is what lets a class whose remaining instances are
# all reasoned-about gaps stop consuming structural packets.
LIVE_CLEAN_CYCLES_TO_CLEAR = 2

# ---------------------------------------------------------------------------
# CT-001 / GI-013 / GI-023 / ST-003 / ST-004 / ST-005 — the concern ledger's
# own lifecycle, beside the escalation lifecycle above for the same reason
# both are here: they are the closed sets a ledger record's `status` field may
# carry, and every reader of that field derives from the declaration rather
# than re-typing a literal.
#
# fallout GI-033 / D-021 / D-035 (concern C-033) — WHY IT MOVED. It was
# declared in `tools/concerns.py`, which reaches `tools/foundry.py` at module
# top for the ledger apparatus — so `orchestration/transitions.py`, a VERIFIER
# module, could not read the member for GI-023's CONCERN_OPEN rung without
# pulling the largest lifecycle module in the tree across the layer boundary.
# Casting 1 had already delegated the READ to
# `foundry_state.open_cross_casting_concerns`, and this was the last thing
# standing: the reader takes the member as an argument, and the argument had
# nowhere to come from. A closed vocabulary is this module's by the house
# convention, so it comes here and the ledger's writers keep the transaction,
# the id allocation and the markdown render.
#
# A TUPLE, NOT A FROZENSET, and the reason is the same one `BLOCKING_TIERS`
# gives: the ORDER is the lifecycle, and the refusal hints read out in it.
# ONE WRITER PER MOVE — `Foundry-Concern` opens, `Foundry-Tasks`' co-dispatch
# set dispatches, `Foundry-Concern(close=...)` closes — which is what makes
# each transition attributable to one door.
#
# `dispatched` IS ADDRESSED, and that is the member most easily got wrong.
# FR-039 makes it the mark left when the co-dispatch set carried the concern to
# the casting that owns it, so the INSPECT door refuses on `open` alone: a rung
# that also refused on `dispatched` would hold the phase shut over work already
# handed to its owner.
#
# Extend only via phase-level RFC.
CONCERN_STATUSES: tuple[str, ...] = ("open", "dispatched", "closed")  # 3 items

#: The three members by name, unpacked from the declaration above so a rename
#: cannot leave a constant pointing at a value the set no longer holds.
CONCERN_STATUS_OPEN, CONCERN_STATUS_DISPATCHED, CONCERN_STATUS_CLOSED = CONCERN_STATUSES

# ---------------------------------------------------------------------------
# INSPECT width (ST-006 / ST-007 / GI-008 / GI-009 / FR-032).
#
# GI-009: the Foundry-Phase transition that OPENS an INSPECT decides the mode
# and records it. Nothing decides lazily at display time — a mode computed
# inside Foundry-Next is a decision no artifact holds, which is why the
# vocabulary lives here and the rule that fired is persisted beside the mode.
# ---------------------------------------------------------------------------

# CLOSED VOCABULARY — the two widths an INSPECT cycle can run at.
INSPECT_MODES = frozenset({"FULL", "DELTA"})  # 2 items

# CLOSED VOCABULARY — the rules that force FULL. Exactly one is recorded as
# the rule that fired.
#
#   first_of_phase    the phase-entry transition into F2 or F5
#   final_gate        the INSPECT immediately before ASSAY, NYQUIST or DONE
#   verifier_touched  the GRIND diff touched the verifier itself
#                     (VERIFIER_PATH_PATTERNS / is_verifier_path)
#
# Extend only via phase-level RFC.
INSPECT_FULL_RULES = frozenset(
    {"first_of_phase", "final_gate", "verifier_touched"}
)  # 3 items

#: Recorded as the rule when NO member of INSPECT_FULL_RULES fired. Not a
#: member of that set: "nothing forced FULL" is the absence of a rule, and
#: enrolling it would make `rule in INSPECT_FULL_RULES` mean the opposite of
#: what it reads as.
INSPECT_DELTA_RULE = "delta"

#: The streams a FULL INSPECT requires, in dispatch order. A TUPLE, not a
#: frozenset: the roster is displayed and the order is the order the lead
#: reads. `sight` and `probe` join it when the run has a UI / a temper phase,
#: which is a per-run fact and therefore not baked in here.
#: Every member is a STREAM_WIRE_IDS member (pinned in tests/test_vocab.py).
FULL_ROSTER_STREAMS = ("trace", "prove", "test", "research_audit", "test01")

#: ST-007 — the two streams a DELTA INSPECT requires only when the diff
#: touches a file they cover, and does not require otherwise. Everything else
#: in FULL_ROSTER_STREAMS is required at both widths.
DELTA_CONDITIONAL_STREAMS = frozenset({"research_audit", "test01"})  # 2 items

#: How many requirement rows PROVE samples on a DELTA cycle.
PROVE_DELTA_SAMPLE_SIZE = 10

# FR-032 — THE single constant defining "this diff touched the verifier".
#
# One constant, not one literal per caller: the rule is consulted by the
# inspect-mode decision, by the evidence sweep and by the report, and three
# copies of a path list is the drift shape D-071 and FR-013 both describe.
# Anchored with `(?:^|/)` rather than `^` so a repo-relative path and an
# absolute one answer the same, and every alternative is a SEGMENT boundary —
# `schemas/` matches the directory, never a file called `myschemas.py`.
#
# The SPEC is deliberately absent from this tuple. It is matched by the
# `spec_path` argument of `is_verifier_path`, because a run's spec lives
# wherever `state.json.spec_path` says (and again at
# `foundry-archive/{run}/spec.md`) — a static pattern would either miss it or
# sweep in every unrelated spec.md in the tree.
#
# WHY THE PACKAGE RULE IS GONE, AND WHAT REPLACED IT (FR-003 / GI-009 / AC-012)
# ------------------------------------------------------------------------------
# D-033 WIDENED this to the whole `foundry_mcp/` package. The reasoning was
# sound for the tree it was written against: the pattern had been an alternation
# of five `tools/` basenames, `is_verifier_path` answered False for
# `foundry_report.py` — whose generated documents the DONE precondition reads —
# `foundry_spawn.py`, `foundry_state.py`, `display.py` and `citation.py`, and
# naming the five misses would have fixed the instance and kept the class. With
# one 15,000-line module holding every gate, every transition, the width
# decision, the spend roll-up, the report seal and the halt door, "everything in
# the package is a gate or something a gate imports" was TRUE.
#
# It stopped being true when that module was split. A-005 states the
# replacement verbatim: "Narrow to: the gate/transition module(s), the width
# decision, `schemas/`, `vocab.py`, the evidence sweep, and the loaded prose of
# the VERIFYING agents (assayer, tracer, research-auditor, spec-test-deriver,
# skills prove/trace/sight/temper). Display, report seal, spend, halt,
# directives, teams, `commands/*.md`, `teammate.md` and `references/` earn
# DELTA."
#
# THE COST THE PACKAGE RULE WAS ACTUALLY CHARGING. A self-targeting run has
# `foundry_mcp/` in almost every diff, so `verifier_touched` fired on a GRIND
# that moved a colour constant in `display.py` or a sentence in
# `commands/start.md`, and the next INSPECT paid for five streams. AC-046 puts a
# ceiling on that — FULL cycles under half of all INSPECT cycles — and the
# package rule made the ceiling unreachable on the one run type this server is
# built to run against itself.
#
# WHAT THE NARROWED SET IS FOR, WHICH IS THE WHOLE TEST OF MEMBERSHIP. The rule
# exists so that a GRIND diff which moved the machinery that JUDGES the build
# cannot be judged by a narrow roster (ST-006). So the question for any path is
# not "is it important" but "could a previous cycle's VERDICT be wrong because
# this moved". A gate, a transition, the width decision, the evidence sweep, a
# schema, the vocabulary and a verifying stream's own loaded contract can each
# make an earlier verdict wrong. A renderer, a spend ledger, a halt door, a
# directive writer, a team registry, the lead's protocol and the teammate
# contract cannot: they act on verdicts, or they describe them, and moving them
# leaves every verdict already reached exactly as sound as it was.
#
# `references/` IS ON THE DELTA SIDE, AND THAT IS A DELIBERATE NARROWING OF
# D-118. That defect widened the prose rule to `agents|commands|references`
# because `references/verification-patterns.md` is loaded as a binding contract
# by `agents/tracer.md` and `agents/assayer.md`, and a GRIND touching it was
# recording DELTA. A-005 and AC-012 both put `references/` back on the delta
# side, and OT-013 states it a third time, so the narrowing is what the
# requirements ask for rather than an oversight — the mitigation now covers the
# agent and skill files themselves, which is where a stream's contract is
# stated, and not the shared documents they cite. `tests/test_vocab.py`'s
# anti-staleness pin is re-scoped to match (AC-017): it asserts over the
# verifying streams' OWN contract prose, derived from the shipped tree rather
# than listed, so a seventh verifying agent fails it the day it ships.
#
# THE `orchestration/` MODULES DO NOT EXIST YET, AND A REGEX MATCHES A STRING.
# Casting 2 creates `gates.py`, `transitions.py`, `width.py` and
# `evidence_boundary.py` in wave 2. The rule below is correct today because it
# is a pattern, not a directory walk; what cannot be asserted today is
# EXISTENCE, which is why `tests/test_vocab.py` holds those four paths in a
# tuple whose existence assertion arms itself the moment the package appears.
#
# WHAT STAYS. The `scripts/validate[-_]…` rule D-118 added: a stream shells out
# to those validators and halts on a non-zero exit, so they are stream-contract
# surface and an edit to one changes what a stream will accept. The SPEC is
# still absent from this tuple and still matched by the `spec_path` argument,
# for the reason below. And the server's own tests are still out: they are the
# pins, not the judgement, and the TEST stream re-runs them at every width.
#
# THE SET FOLLOWS THE MACHINERY, NOT THE MODULE NAMES (D-125, fallout of
# D-080). The GI-033 layering hoisted sixteen gate and width predicates OUT of
# the four decider modules and into three leaves, and this tuple did not
# follow — so `is_verifier_path` answered False for `tools/foundry_state.py`,
# `tools/artifacts.py` and `orchestration/escalation.py` while those three
# DEFINED the streams-complete check, the persisted cap behind `would_halt`,
# the CONCERN_OPEN rung, the halt rungs, the recorded-width reader, the width
# refusal itself (`unrecorded_width_problem` + `inspect_mode_gap` +
# `WIDTH_RECORDING_TRANSITIONS`, moved wholesale by D-080), the diff the width
# rule scans, the boundary base SHA, the DONE gate's report read and the
# escalation rung readers — each called from a live refusal path in `gates.py`
# or `transitions.py`. A GRIND diff touching only the predicate that decides
# whether a cycle's verification was complete recorded rule `delta`, so the
# next INSPECT ran narrow having just changed the thing that judges
# completeness. That is the hazard A-032 names and whose only accepted
# mitigation is `verifier_touched` forcing FULL.
#
# WHY THE WHOLE LEAF AND NOT THE PREDICATES. A pattern matches a path, and a
# path is a file: there is no regex over `foundry_state.py#check_streams_complete`
# that leaves `foundry_state.py#spend_rollup` on the delta side. The two live in
# one module because GI-033 put them there — a symbol both layers read can live
# in neither — so the file is the smallest unit the rule can name, and naming it
# is the honest reading of FR-043's "the width decision" once the width decision
# moved. The cost is real and is the intended one: a diff that only touches a
# report table in these two leaves now forces FULL. The alternative — moving the
# predicates back — is D-080's fix reversed.
#
# SIBLING PLUGINS ARE NOT THIS VERIFIER (D-113). Every rule above is written
# with a `(?:^|/)` segment anchor so it matches however the plugin is installed,
# and in the Guild source repo that anchor also matches a SIBLING plugin's
# identically-named file: `plugins/crucible/agents/assayer.md` matched the
# agent-prose rule and `plugins/forge/scripts/validate-spec.py` matched the
# validator rule. No foundry stream loads crucible's assayer or shells out to
# forge's validators, so a forge-only or crucible-only diff recorded
# `verifier_touched` and charged a full five-stream cycle against the AC-046
# ceiling this narrowing exists to make reachable. That is stated ONCE below as
# an exclusion rather than by anchoring each rule to `foundry/`, because the
# cause is neither the agent rule nor the script rule — it is that this repo
# hosts several plugins — and because the anchored spelling would stop matching
# the plugin-relative paths (`agents/assayer.md`, `skills/prove/SKILL.md`) that
# `tests/test_inspect_mode.py` drives the width decision with.
#
# An enumeration is an enumeration, so the anti-staleness mechanism is not here:
# `tests/test_vocab.py` asserts the set in BOTH directions — every member
# answers True, every named non-member answers False — and derives the verifying
# streams' contract corpus from the shipped tree. Deriving the code half here
# would need filesystem and AST work, which the PURITY RULE forbids.
# Extend only via phase-level RFC.
VERIFIER_PATH_PATTERNS: tuple[str, ...] = (
    # The canonical vocabulary itself, wherever it sits. Every closed
    # vocabulary a stream validates against is declared here, so a diff moving
    # one can change what any stream will accept.
    r"(?:^|/)vocab\.py$",
    # Any schema module — the finding/report shapes every stream validates on.
    r"(?:^|/)schemas/",
    # THE MODULES THAT DECIDE. The gate ladder, the phase transitions, the
    # INSPECT width decision, the evidence-sweep boundary and the escalation
    # rung readers: a diff moving any of them can make a verdict already
    # reached wrong, which is the only thing `verifier_touched` is for. Named
    # as an alternation under one directory rather than as a `orchestration/`
    # segment rule, because that package also holds the halt door, the report
    # seal, spend, directives and teams — every one of which A-005 puts on the
    # delta side.
    r"(?:^|/)foundry_mcp/tools/orchestration/"
    r"(?:gates|transitions|width|evidence_boundary|escalation)\.py$",
    # THE TWO LEAVES THE GI-033 LAYERING HOISTED THE PREDICATES INTO (D-125).
    # See the note above for why a leaf earns this and `display.py` does not:
    # these two DEFINE refusals, they do not render them.
    r"(?:^|/)foundry_mcp/tools/(?:foundry_state|artifacts)\.py$",
    # The evidence corpus itself. GI-006 keeps it re-executable at every
    # crossing, and the module that re-executes it decides whether a log
    # passes.
    r"(?:^|/)foundry_mcp/tools/evidence\.py$",
    # LOADED CONTRACT PROSE — the six VERIFYING streams' own agent files. A
    # stream's contract is what it will and will not report, so an edit to one
    # changes what the next INSPECT means. `teammate.md` is deliberately absent:
    # a teammate BUILDS, and a build instruction moving cannot make a verdict
    # already reached wrong.
    r"(?:^|/)agents/(?:assayer|tracer|flow-tracer|research-auditor"
    r"|coverage-diff|spec-test-deriver)\.md$",
    # The four verification skills, one segment deeper. The non-SKILL files
    # beside them (a skill's own README) are not contracts.
    r"(?:^|/)skills/(?:prove|trace|sight|temper)/SKILL\.md$",
    # The validators a stream SHELLS OUT TO (D-118). The adjudicator agents
    # invoke these through `${CLAUDE_PLUGIN_ROOT}/scripts/` and halt on a
    # non-zero exit, so what they accept is stream-contract surface. Both
    # spellings, because the two halves of that pair disagree about hyphen
    # versus underscore already.
    r"(?:^|/)scripts/validate[-_][^/]+\.py$",
)  # 8 patterns

_VERIFIER_PATH_RES: tuple[re.Pattern[str], ...] = tuple(
    re.compile(pattern) for pattern in VERIFIER_PATH_PATTERNS
)

# CLOSED VOCABULARY — paths no rule above may claim, however well it matches
# (D-113). Checked BEFORE the patterns and only against them; the `spec_path`
# argument is untouched by this, because a run whose spec lives under another
# plugin is still that run's own spec and must still force FULL.
#
# The one member says: a file under a plugin that is not foundry is not this
# verifier's machinery, whatever it is called. Sibling plugins in this repo ship
# an `agents/assayer.md` and two `scripts/validate*.py` of their own, and the
# segment anchors above — which exist so the rules survive being installed at a
# different root — matched them. Extend only via phase-level RFC.
VERIFIER_PATH_EXCLUSIONS: tuple[str, ...] = (
    r"(?:^|/)plugins/(?!foundry/)[^/]+/",
)  # 1 pattern

_VERIFIER_PATH_EXCLUSION_RES: tuple[re.Pattern[str], ...] = tuple(
    re.compile(pattern) for pattern in VERIFIER_PATH_EXCLUSIONS
)


def _normalise_path(path: object) -> str:
    """Repo-relative POSIX spelling of a path, or "" when unusable.

    Pure string work — the PURITY RULE forbids touching the filesystem, so
    nothing here resolves, globs or stats. Windows separators are folded and a
    leading `./` is dropped so the same file compares equal however the caller
    spelled it.
    """
    if not isinstance(path, str):
        return ""
    text = path.strip().replace("\\", "/")
    while text.startswith("./"):
        text = text[2:]
    return text


def is_verifier_path(path: str, spec_path: str | None = None) -> bool:
    """True when a changed path is part of the verifier itself (FR-032).

    A GRIND diff touching any of these means the machinery that JUDGES the
    build moved, so the next INSPECT cannot trust a delta roster — it must run
    FULL (ST-006, rule `verifier_touched`).

    `spec_path` is the run's own spec, passed at call time rather than matched
    by pattern: see the note on VERIFIER_PATH_PATTERNS. Never raises.

    THE EXCLUSIONS GATE THE PATTERNS AND NOT THE SPEC (D-113). A sibling
    plugin's identically-named file is not this verifier's machinery however
    well it matches a rule above; a run whose spec happens to live under one is
    still that run's own spec, and `verifier_touched` on the spec is FR-032's
    whole point. So the exclusion is applied to the pattern arm alone, which is
    the arm that matched by NAME.
    """
    normalised = _normalise_path(path)
    if not normalised:
        return False
    excluded = any(
        pattern.search(normalised) for pattern in _VERIFIER_PATH_EXCLUSION_RES
    )
    if not excluded and any(
        pattern.search(normalised) for pattern in _VERIFIER_PATH_RES
    ):
        return True
    spec = _normalise_path(spec_path)
    return bool(spec) and normalised == spec


# ---------------------------------------------------------------------------
# Lead-authored fixes (GI-003 / ST-004 / CT-005 / CT-006 / FR-034).
# ---------------------------------------------------------------------------

# CLOSED VOCABULARY — who wrote a fix. Required on every Foundry-Fix, because
# a lead fix recorded as free prose in a hand-written handoff is a fix nothing
# can measure or count (GI-003).
FIX_AUTHORS = frozenset({"lead", "teammate"})  # 2 items

# ST-004 / CT-006 — the lane a LIVE lead-authored fix must fit inside, measured
# from `git show --numstat` on the fix commit. One non-test file, at most 20
# added-plus-deleted lines.
#
# THE MEASUREMENT AND THE LIMIT ARE DIFFERENT FACTS (D-194; the GRIND cycle 13
# ruling on FR-046 / FR-053 / CT-006 / GI-003 / AC-022). The numstat
# measurement runs on both tiers, so every `lead_fix` handoff record carries a
# file list and a line count, LATENT included. The LIMIT — these two numbers —
# is evaluated, and can refuse, only when the defect is LIVE; a LATENT lead fix
# of any size is accepted against it and is still measured for the record.
#
# The sentence here used to say the opposite: that a LATENT fix_commit was
# filed away without git ever reading it. Driven (D-194): a lead fix on a
# LATENT defect, over two non-test files and 401 added-plus-deleted lines, was
# accepted, and the `lead_fix` record the server wrote for it carries
# line_count 401 with a full per-file `files` array. So the one file that
# DEFINES this lane described behaviour no door has, beside a corrected
# statement of the same rule one module away in
# `foundry_handoff.record_lead_fix_handoff`. `tests/test_vocab.py` now pins
# this block so the two cannot drift apart again.
LEAD_LANE_MAX_FILES = 1
LEAD_LANE_MAX_LINES = 20

#: FR-034 — the repo's OWN pytest discovery configuration, mirrored key for
#: key. Every `pyproject.toml` in this repo that declares
#: `[tool.pytest.ini_options]` (mcp-server, plugins/forge, plugins/webster)
#: spells these two lists identically, and `tests/test_vocab.py` parses those
#: files with `tomllib` and asserts EQUALITY against the two constants below —
#: so a config change fails there rather than drifting silently past this
#: module, which is the only anti-staleness mechanism the PURITY RULE leaves
#: available (same shape as the VERIFIER_PATH_PATTERNS note above).
#:
#: MIRRORED, NOT INVENTED — AND NEVER A SUPERSET (D-222)
#: ----------------------------------------------------
#: These were a hand-written regex roster that ADDED `*_test.py` and treated
#: ANY path segment spelled `tests` as a test tree whatever the file inside it
#: was called. Every extra member is SUBTRACTED from the lane's file and line
#: counts, so the roster widened exactly the bound FR-016 / CT-006 exist to
#: hold. Driven at the Foundry-Fix door with `authored_by=lead` on a LIVE
#: defect: a `fix_commit` over `src/tiny.py` (5 lines) plus
#: `src/helpers_test.py` (400 lines) returned ok True, and so did one over
#: `src/tiny2.py` (5 lines) plus `src/tests/production_helper.py` (400 lines).
#: Both commits changed two source files and 405 added-plus-deleted lines
#: through a lane bounded at one non-test file and 20 lines, and each
#: `lead_fix` audit record named only the small half (`line_count` 5).
#: `pytest --collect-only` against this configuration collects NEITHER file.
#:
#: The alternative shape — reading the TARGET repo's `pyproject.toml` from
#: here, so the predicate is per-repo rather than a mirror of this one — was
#: rejected twice over: the PURITY RULE at the top of this module forbids the
#: filesystem, and the caller that actually holds the target root
#: (`_numstat_measurement`) would have to thread it in, which is a different
#: casting's file. It is recorded in the run's concerns.md, not built here.
#:
#: BUT THE NARROWING WENT ONE STEP TOO FAR (D-231)
#: -----------------------------------------------
#: That fix ALSO required a `PYTEST_TESTPATHS` run in the directory part, so
#: `is_test_file` answered False for a `test_*.py` anywhere but under
#: `tests/`. Driven at the real Foundry-Fix door: an in-lane LIVE lead fix —
#: one non-test file, 20 lines, plus its regression test — was refused with
#: "changes 2 non-test file(s) (lanemod0691.py, test_lane0692.py) — the lane
#: requires exactly 1". Re-driven one layout at a time, `tests/test_dir_case.py`
#: was accepted while `test_root_case.py`, `root_case_test.py` and
#: `verifier/deep/test_nested_case.py` were all refused.
#:
#: The cycle-27 lead ruling settles the reading: `testpaths` SEEDS
#: argument-less collection and is NOT a filter — `pytest --collect-only .`
#: against this same configuration collects a root-level `test_*.py` — and the
#: lane measures a commit in the TARGET repo, which carries no obligation to
#: keep its tests under `tests/`. So the BASENAME is the whole rule.
#:
#: D-222 does not reopen under it, because the basename still has to match:
#: `src/helpers_test.py` and `src/tests/production_helper.py` are both still
#: SOURCE, and both 405-line escapes are still refused by the lane.
PYTEST_PYTHON_FILES: tuple[str, ...] = ("test_*.py",)  # 1 glob

#: Mirrored for the `tomllib` equality pin in `tests/test_vocab.py` and for
#: the derived refusal phrase rung 2 of `_regression_test_problem` prints.
#: NOT read by `is_test_file` — see the D-231 note above. A constant that
#: classifies nothing cannot narrow the lane again.
PYTEST_TESTPATHS: tuple[str, ...] = ("tests",)  # 1 path

#: pytest's OWN fixed filename. `conftest.py` is hardcoded inside pytest and is
#: not reachable from `python_files`, so mirroring it here substitutes for no
#: configured value — there is none to read. The collector loads it from the
#: rootdir downwards, which is why it is matched wherever it sits rather than
#: only under a testpath.
PYTEST_CONFTEST_BASENAME = "conftest.py"


def is_test_file(path: str) -> bool:
    """True when pytest's configured discovery calls `path` a test (FR-034).

    The BASENAME is the whole rule: a name matching a `PYTEST_PYTHON_FILES`
    glob, in ANY directory, plus pytest's own `conftest.py`. Everything else
    is a SOURCE file for the lane count — a `*_test.py` no declared config
    names, an `__init__.py` or a JSON fixture sitting beside the tests, a
    production module under some directory that merely happens to be called
    `tests`. Pure and never raises.

    The glob is MATCHED WITH `fnmatch`, which is the matcher pytest itself
    applies to `python_files`, so the pattern is consumed from the mirrored
    config rather than re-typed as a regex beside it — that re-typing is how
    `*_test.py` came to be in a roster no `pyproject.toml` in this repo asks
    for (D-222).

    WHY THE DIRECTORY IS NOT CONSULTED AT ALL (D-231)
    -------------------------------------------------
    This required a `PYTEST_TESTPATHS` run in the directory part, and both
    surfaces that read it then disagreed with pytest for every `test_*.py`
    outside `tests/`. `_numstat_measurement` counted the regression test as a
    second SOURCE file and the lane refused an in-lane lead fix; rung 2 of
    `_regression_test_problem` refused the locator that named the same test.
    Neither is what pytest answers: `testpaths` seeds ARGUMENT-LESS collection
    only, and `pytest --collect-only .` against this configuration collects a
    root-level `test_*.py`. The lane also measures a commit in the TARGET
    repo, which owes this repo no directory layout.

    The alternative shape — keeping the testpath run as a second accepting
    arm rather than a required one — was rejected as dead logic: the basename
    has to match in both arms, so the disjunct decides nothing and only
    invites the same narrowing back. `tests/test_vocab.py` drives this against
    real pytest with an explicit path argument rather than against a table.
    """
    normalised = _normalise_path(path)
    if not normalised:
        return False
    basename = normalised.rsplit("/", 1)[-1]
    if basename == PYTEST_CONFTEST_BASENAME:
        return True
    return any(fnmatchcase(basename, glob) for glob in PYTEST_PYTHON_FILES)


#: GI-003 — the handoff event the SERVER appends when it accepts a lead fix.
#: Named here so the writer, the report generator and the F6 section that
#: lists them all spell it identically.
HANDOFF_EVENT_LEAD_FIX = "lead_fix"

# ---------------------------------------------------------------------------
# Run artifacts and terminal states (ST-008 / CT-013 / CT-014 / CT-016 / GI-006).
# ---------------------------------------------------------------------------

# CLOSED VOCABULARY — GI-001's loop, as the ids a run passes through and the
# name each id is printed under. The ORDER is the ladder's: a renderer walks
# this tuple to draw the phase list, so a phase inserted here appears in the
# right place in every rendering rather than in whichever hand-typed copy the
# author remembered.
#
# fallout D-015 — WHY IT IS HERE AND NOT BESIDE A RENDERER. It was hand-typed
# twice: `display.py#_PHASE_NAMES` as a mapping and
# `orchestration/guidance.py#_format_status_display` as a list of pairs, each
# knowing the same ten rows and neither knowing the other. Holmes `share-8`
# names the shape ("phase ladder table and ANSI palette are hand-typed in three
# modules"), and the module convention answers it: closed vocabularies live
# here and every consumer derives from them.
#
# HALTED IS NOT A ROW, DELIBERATELY. This ladder enumerates the phases a run
# passes THROUGH; `RUN_PHASE_HALTED` below is where a run stops instead of
# continuing along it, which is why the status renderer draws it as its own
# line and not as an eleventh step (D-137).
#
# Extend only via phase-level RFC.
PHASE_LADDER: tuple[tuple[str, str], ...] = (
    ("F0", "RESEARCH"),
    ("F0.5", "DECOMPOSE"),
    ("F0.9", "VALIDATE"),
    ("F1", "CAST"),
    ("F2", "INSPECT"),
    ("F3", "GRIND"),
    ("F4", "ASSAY"),
    ("F5", "TEMPER"),
    ("F5.5", "NYQUIST"),
    ("F6", "DONE"),
)  # 10 phases

#: The same ladder as a lookup, for a renderer that has an id and wants the
#: name. DERIVED from `PHASE_LADDER` so adding a phase needs one edit and the
#: two shapes cannot come to disagree — which is exactly what the two
#: hand-typed copies did.
PHASE_NAMES: dict[str, str] = dict(PHASE_LADDER)

#: ST-008 — a run that hits `max_cycles` reaches this phase by a SUCCESSFUL
#: transition, not a refusal. HALTED is a named terminal state and is NOT
#: DONE: the report is generated and every open defect is named in it, at
#: EVERY tier `DEFECT_TIER_OR_UNKNOWN` admits.
#:
#: THE TIER COUNT HERE IS THE WHOLE VOCABULARY, NOT THE BLOCKING HALF.
#: This sentence read "every open LIVE and LATENT defect is named in it",
#: which is the prior spec's FR-045 ("Not a refusal: state.json phase becomes
#: HALTED, the report is written naming every open LIVE and LATENT defect")
#: carried forward verbatim from a release where `DEFECT_TIERS` had two
#: members. It has three: `foundry_report._read_defect_sections` buckets the
#: cross-tab over `DEFECT_TIER_OR_UNKNOWN` and renders a HARDENING backlog
#: beside the LATENT one (AC-024), so a halted run's report has named every
#: open HARDENING record since that section landed. Describing the tier set
#: as two members in the one file a maintainer reads to learn what a tier IS
#: is the D-148 shape — stale prose surviving beside new prose — and it is
#: what the count pin in `test_vocab.py` now refuses across this module.
#: Where FR-045 is QUOTED it stays verbatim, because a locked requirement is
#: not rewritten by a later release; this is not a quote, it is the module's
#: own statement, and the module's own statement has to be true today.
RUN_PHASE_HALTED = "HALTED"

# CLOSED VOCABULARY — FR-019 / CT-005 / ST-001: why a run ended on a ruling.
#
#   cap_reached           the GRIND door found the persisted `max_cycles`
#                         below the cycle it was about to open. The ONLY
#                         member a transition writes on its own.
#   lead_ruling           the lead stopped the run deliberately.
#   spec_change_required  the run cannot converge without a spec change, so
#                         continuing would grind against a target that is
#                         itself wrong.
#   user_stop             the user asked for it.
#
# A HALTED run carries the member AND the lead's own free text (CT-004): the
# member is what `measure-run.py` and the report group on, and the text is why
# THIS run ended, which no closed set can carry. Neither substitutes for the
# other — `halted_reason` was a bare f-string before this release, which is
# readable and ungroupable, and every reader must still accept that shape
# (FR-054).
#
# Extend only via phase-level RFC.
HALT_REASONS = frozenset(
    {"cap_reached", "lead_ruling", "spec_change_required", "user_stop"}
)  # 4 items

#: The member the cap path writes. Named rather than spelled at the door, so
#: the transition that halts on the cap and the report section that groups by
#: reason cannot come to disagree about which member that is (CT-005).
HALT_REASON_CAP_REACHED = "cap_reached"


def halt_reason(value: object) -> str | None:
    """The HALT_REASONS member a persisted `halted_reason` READS as, or None.

    Total and never raises. Mirrors `defect_tier` above one rung along: a value
    outside the closed set resolves to None rather than being coerced onto a
    member, because the pre-release spelling of this field is a free f-string
    ("--max-cycles 2 reached: opening GRIND cycle 3 would exceed it") and
    guessing which member that sentence meant is how a run's ending gets
    reclassified by a reader.

    None is therefore a REAL answer and not an error: it says "this record
    carries text and no member", which is exactly what every archive written
    before FR-019 carries. A reader prints the text; a grouper skips the row.
    Accepting `{reason, text}` as well as the bare string is the caller's job —
    this resolves one value, and the PURITY RULE keeps it doing only that.
    """
    if isinstance(value, str) and value in HALT_REASONS:
        return value
    return None


def halt_reason_phrase() -> str:
    """The four members, comma-joined, for a refusal that names the set.

    Derived from the constant rather than re-typed at the door, following
    `_PYTEST_DISCOVERY_PHRASE`'s shape: the door that refuses an unknown reason
    has to print the set it accepts, and a hand-typed copy of a closed
    vocabulary in a refusal message is the drift this module exists to end.
    Sorted so the sentence is stable across runs.
    """
    return ", ".join(sorted(HALT_REASONS))


#: CT-013 — the per-agent spend ledger, one JSON object per line under
#: `foundry-archive/{run}/`.
SPEND_LEDGER_FILENAME = "spend.jsonl"

#: CT-014 / GI-006 — the two documents Foundry-Report writes and the DONE
#: transition refuses without.
REPORT_MD_FILENAME = "REPORT.md"
REPORT_JSON_FILENAME = "report.json"

# CLOSED VOCABULARY — every section the generated report must carry, in order.
# GI-006: the lead may append prose below them but cannot omit one, and
# `Foundry-Phase('done')` refuses when a section is missing. `report.json`'s
# top-level keys are exactly these plus `generated_at` and `run`; REPORT.md
# carries one `## ` heading per member in this order.
#
# THE TUPLE IS ORDERED AND THE ORDER IS THE DOCUMENT'S (FR-027 / AC-047).
# `_render_markdown` walks this tuple to emit one `## ` heading per member, so
# a member's position here IS where its section sits in the report a lead
# reads. The four members A-038 adds are placed beside the section each one is
# read WITH rather than appended at the end:
#
#   hardening_backlog          beside `latent_backlog`, which AC-024 asks for
#                              by name. The two are the same question about
#                              different subjects — what stays open and why —
#                              and a reader comparing them should not have to
#                              scroll past nine sections to do it.
#   fallout_per_cycle          after the defect sections it is derived from:
#                              it counts records carrying `fallout_of`, so it
#                              belongs where the defect ledger is being
#                              reported and not beside the spend tables.
#   stream_coverage_per_cycle  beside `inspect_modes_per_cycle`. Both are
#                              per-cycle facts about the same INSPECTs — the
#                              width the transition chose, and what the streams
#                              actually covered at it.
#   halt_and_co_dispatch       after the dispatch sections whose sets it names,
#                              and before the two closing sections that
#                              describe the machine rather than the run.
#
# fallout D-039 — THE SIXTEENTH IS AC-044's OTHER HALF. "The span table appears
# in the F0.9 output AND in the F6 report"; the F0.9 half shipped and the F6
# half did not, while `foundry_validate._render_span_table`'s own docstring
# asserted that "the F6 report draws the same table from the same records" —
# a producing side documenting a consumer nobody had written. `requirement_span`
# sits immediately after `verdict_matrix` because the two are the same table
# read along its two axes: the matrix says whether each requirement was
# VERIFIED, the span says how many castings had to build it, and a reader
# asking why a requirement came out thin reads them together.
#
# Extend only via phase-level RFC.
REPORT_REQUIRED_SECTIONS = (
    "verdict_matrix",
    "requirement_span",
    "defects_by_tier_and_status",
    "latent_backlog",
    "hardening_backlog",
    "unknown_tier_defects",
    "fallout_per_cycle",
    "escalated_classes",
    "lead_fix_records",
    "inspect_modes_per_cycle",
    "stream_coverage_per_cycle",
    "spend_per_phase_and_cycle",
    "unreported_dispatches",
    "halt_and_co_dispatch",
    "executing_versions",
    "baseline_comparison",
)  # 16 sections

#: fallout GI-033 (concern C-060 row 2) — THE MARKDOWN HEADING PER SECTION KEY.
#:
#: A dict rather than a prettifier over the key names, because `latent_backlog`
#: prettifies to "Latent Backlog" but `unknown_tier_defects` prettifies to
#: "Unknown Tier Defects", which reads as a tier called "Unknown Tier".
#:
#: IT LIVES HERE, beside the tuple it is paired with, because the report SEAL
#: writes these headings and the DONE gate CHECKS them, and those two sit in
#: different layers. `foundry_report.py` renders them and `artifacts.py` reads
#: them back; a table owned by the presentation module put the gate's check
#: behind a presentation import. Pairing it with the tuple here also means the
#: assertion below runs at vocabulary import, so a section added to one and not
#: the other fails at the vocabulary rather than at whichever reader ran first.
#:
#: READ-ONLY BY CONSTRUCTION, in the shape `WIRE_TO_CANONICAL` already uses.
#: Three modules in two layers now bind this ONE object — casting 7's leaf read
#: pins `artifacts.REPORT_SECTION_TITLES is vocab.REPORT_SECTION_TITLES` rather
#: than comparing equal, because a copy that agreed today was the defect that
#: hoist was refused over. Identity proves the reference is shared; it does not
#: prove nobody writes through it, and neither a grep for the name nor an AST
#: scan for subscript assignment sees `t = REPORT_SECTION_TITLES` followed by
#: `t[k] = ...`. A scan and a pin that miss the SAME shape is where
#: construction beats inspection, so the proxy closes it: every writer raises
#: TypeError at the point of the write, whatever name it reached through.
#:
#: Extend only via phase-level RFC, together with REPORT_REQUIRED_SECTIONS.
_REPORT_SECTION_TITLES: dict[str, str] = {
    "verdict_matrix": "Verdict matrix",
    "requirement_span": "Requirement span",
    "defects_by_tier_and_status": "Defects by tier and status",
    "latent_backlog": "LATENT backlog",
    "hardening_backlog": "HARDENING backlog",
    "unknown_tier_defects": "Unknown-tier defects",
    "fallout_per_cycle": "Fallout per cycle",
    "escalated_classes": "Escalated classes",
    "lead_fix_records": "Lead fix records",
    "inspect_modes_per_cycle": "INSPECT mode per cycle",
    "stream_coverage_per_cycle": "Stream coverage per cycle",
    "spend_per_phase_and_cycle": "Spend per phase and cycle",
    "unreported_dispatches": "Unreported dispatches",
    "halt_and_co_dispatch": "Halt and co-dispatch",
    "executing_versions": "Executing server and plugin versions",
    "baseline_comparison": "Baseline comparison",
}

REPORT_SECTION_TITLES: Mapping[str, str] = MappingProxyType(_REPORT_SECTION_TITLES)

assert set(REPORT_SECTION_TITLES) == set(REPORT_REQUIRED_SECTIONS), (
    "every REPORT_REQUIRED_SECTIONS member needs a markdown title: "
    f"{sorted(set(REPORT_REQUIRED_SECTIONS) ^ set(REPORT_SECTION_TITLES))}"
)


# ---------------------------------------------------------------------------
# NFR-001 / AC-039 / OT-030 — the convergence comparison.
#
# WHY THESE ARE CONSTANTS AND NOT DERIVED
# ---------------------------------------
# thunder-viper executed on the 4.7.3 server cache, so its archive has no
# stream-rollup.json, no escalation.json, no spend.jsonl and no inspect_modes,
# and its state.json cycle counter stayed at 0 for the whole run. Its 22 GRIND
# cycles and 8 post-verification cycles (cycles 15-22 were TEMPER hardening,
# per its own REPORT.md) are therefore NOT recoverable from the archive. They
# are recorded here, once, so that `measure-run.py` and the F6 report print the
# same two numbers and can never disagree about the baseline they are measured
# against.
#
# Plain dicts rather than MappingProxyType, deliberately: `report.json` is
# json.dumps'd wholesale by the report generator, and a mapping proxy is not
# JSON-serializable. tests/test_vocab.py pins the contents instead.
#
# NFR-001: "Numbers are the target, not a gate." Nothing in this module or in
# measure-run.py turns a missed target into a refusal or a nonzero status.
# ---------------------------------------------------------------------------

THUNDER_VIPER_BASELINE = {
    "run": "thunder-viper",
    "grind_cycles": 22,
    "post_verification_cycles": 8,
}

CONVERGENCE_TARGET = {
    "grind_cycles": 12,
    "post_verification_cycles": 3,
}

# ---------------------------------------------------------------------------
# D-186 / NFR-007 / FR-036 — THE ONE TERM OF THE COMPARISON THAT HAS NO NUMBER.
#
# The two dicts above record CYCLES. NFR-007 asks for a comparison in
# FULL-WIDTH cycles, and there is no predecessor width figure to compare
# against — not because nobody derived one, but because thunder-viper's
# archive cannot carry one. Recording that, rather than manufacturing a
# figure, is what FR-036 prescribes for exactly this shape ("Nothing beyond
# FULL width; record it as a documented residual risk"), and NFR-011 is why it
# is a CONSTANT and not a comment: a prose rule this effort states is pinned
# to a code constant, and a comment is not something a test can assert on.
#
# Its reader is
# `tests/test_vocab.py#test_nfr_007s_missing_baseline_width_is_recorded_as_a_residual_risk`,
# which is the same relationship `test_lead_prose.py` has to the FR-036
# paragraph in `references/lead-discipline.md`. Nothing renders this into
# `report.json`: `baseline_comparison` publishes NFR-001's columns, no
# requirement names a residual-risk key in it, and the note is a bound on how
# the comparison is READ rather than another cell in it.
# ---------------------------------------------------------------------------

NFR_007_RESIDUAL_RISK = (
    "NFR-007 states this effort's acceptance as a COMPARISON — \"a terminal "
    "state (DONE or HALTED with a named backlog) in materially fewer "
    "FULL-width cycles\" — and three of its four terms are measured. The "
    "terminal state is state.json's phase plus its phase_history HALTED row; "
    "the named backlog is the report's latent, hardening and unknown-tier "
    "sections; this run's own FULL-width count is foundry_state's "
    "full_cycle_ratio. The fourth term is the PREDECESSOR's FULL-width count, "
    "and it has no figure anywhere. THUNDER_VIPER_BASELINE records "
    "grind_cycles and post_verification_cycles and no width, because the "
    "thunder-viper archive predates width recording entirely: that run "
    "executed on the 4.7.3 server cache, which wrote no inspect_modes at all, "
    "so not one of its 22 GRIND cycles carries a width and none can be "
    "recovered from what it left behind. THE FIX IS THAT THERE IS NOT ONE, "
    "AND RECORDING THAT IS THE DELIVERABLE. NFR-007 is therefore evaluated "
    "through NFR-008's ratio — FULL cycles divided by total INSPECT cycles, "
    "below 50% — which is wired, measured and reported on this run. NFR-008 "
    "is a separate Locked row, so reading one through the other is a "
    "judgement rather than a derivation, and it is an accepted residual risk "
    "of comparing against an archive written before the widths existed, not a "
    "gap someone is going to close later. Read it as a bound on what the "
    "convergence comparison proves: a run that passes NFR-008 has shown its "
    "own width is under half, and has not shown a number against "
    "thunder-viper's."
)

# ---------------------------------------------------------------------------
# Finding-record vocabularies.
#
# Added by D-071. schemas/findings.py was a SEVENTH copy of the vocabularies
# above and read none of them, so the validator two shipped skills tell a
# stream to run rejected that stream's own documented output. Fixing it needed
# two closed vocabularies that lived only in skill prose; they are declared
# here so findings.py derives them rather than re-typing them.
# ---------------------------------------------------------------------------

# CLOSED VOCABULARY — the finding-id prefix families the verification skills
# tell their streams to emit. A finding id is `<PREFIX>-<N>`; the prefix says
# which lens produced it, and carries no ordering or grade (see the severity
# note on DEFECT_TYPES). Provenance, one skill per line:
#
#     L, THIN, SA, DEV, PL   skills/trace/SKILL.md   (PL at :214)
#     CR, SP, DX             skills/prove/SKILL.md   (DX at :134)
#     T                      skills/temper/SKILL.md  (:123)
#
# The set is the UNION over the three skills rather than a per-schema mapping:
# the prefix's job is to reject a malformed id, not to police which stream
# filed a report, and a per-schema split would be one more table to drift.
# Extend only via phase-level RFC.
FINDING_ID_PREFIXES = frozenset(
    {
        "L", "THIN", "SA", "DEV", "PL",
        "CR", "SP", "DX",
        "T",
    }
)  # 9 items

# JSON-Schema `pattern` accepting exactly the FINDING_ID_PREFIXES families.
# Derived from the frozenset so a new prefix needs one edit, not two. Sorted
# longest-first for readability only — the trailing `-\d+$` anchor already
# makes alternation order irrelevant (an anchored match backtracks out of the
# `T` branch on "THIN-1" and into the `THIN` branch).
FINDING_ID_PATTERN = "^(?:{})-\\d+$".format(
    "|".join(sorted(FINDING_ID_PREFIXES, key=lambda p: (-len(p), p)))
)

# CLOSED VOCABULARY — the status a temper micro-domain can carry, verbatim
# from the counts line at skills/temper/SKILL.md:121 ("domains probed,
# SOLID/CRACKED/HOLLOW/MISSING/STUCK, findings, suggestions"). This is a
# per-DOMAIN progress state, not a grade on a finding: HOLLOW and MISSING name
# what the probe found behind the domain, and STUCK is the 3-attempt ceiling
# from the continuous-temper loop at :147. Extend only via phase-level RFC.
TEMPER_DOMAIN_STATUSES = frozenset(
    {"SOLID", "CRACKED", "HOLLOW", "MISSING", "STUCK"}
)  # 5 items

# ---------------------------------------------------------------------------
# Finding class names.
# ---------------------------------------------------------------------------

# The four comment-prose observation classes named by AC-001.
LINE_DRIFT_CITE = "LINE_DRIFT_CITE"
PROSE_COUNT = "PROSE_COUNT"
DIRECTION_WORD = "DIRECTION_WORD"
ENUMERATION = "ENUMERATION"

# FR-017 / GI-027 / CT-017 — the fifth class, and the one that is not about
# comment prose at all.
#
# A PROVE probe idea that is worth DRIVING and that PROVE did not drive. The
# four classes above are all "this comment no longer agrees with the code";
# this one is "here is a question nobody has asked yet", and it is an
# observation for the same structural reason they are: it is not a defect,
# because nothing has been shown to be wrong. What makes it worth a ledger
# entry is that TEMPER's roster is exactly the open candidates plus its own
# micro-domains, and each is closed as DRIVEN — filed or clean.
#
# GI-027's violation column names the harm on BOTH sides, and they are
# different harms: TEMPER ignoring recorded candidates loses the work of
# noticing, and PROVE filing a candidate as a DEFECT blocks a run over a
# question rather than a finding. A candidate is neither a defect nor a
# HARDENING record — HARDENING is a probe that was DRIVEN and failed, and a
# candidate is a probe that has not been driven at all.
#
# AC-020 is the other half: on a run where TEMPER never ran, the F6 report
# lists every candidate that was not driven, by name. A backlog tier with no
# promotion cadence becomes write-only debt, and the report section is the
# cadence's visible half.
TEMPER_CANDIDATE = "TEMPER_CANDIDATE"

# CLOSED VOCABULARY — findings of these classes are recordable in the
# observations ledger instead of the defect ledger, UNLESS a denylist entry
# also matches. The never-demote denylist below is UNCHANGED by the fifth
# member and still outranks every one of them (AC-002 / GI-004): a probe idea
# whose description makes a security-property claim is a DEFECT, and the
# tripwire fires naming the entry that matched.
# Extend only via phase-level RFC.
OBSERVATION_CLASSES = frozenset(
    {LINE_DRIFT_CITE, PROSE_COUNT, DIRECTION_WORD, ENUMERATION, TEMPER_CANDIDATE}
)  # 5 items

# The four never-demote denylist classes named by AC-002.
SECURITY_PROPERTY_CLAIM = "SECURITY_PROPERTY_CLAIM"
SPEC_REQUIRED_BEHAVIOUR_CLAIM = "SPEC_REQUIRED_BEHAVIOUR_CLAIM"
UNRESOLVABLE_CITE = "UNRESOLVABLE_CITE"
NON_COMMENT = "NON_COMMENT"

# CLOSED VOCABULARY — a finding matching ANY of these can never be recorded
# as an observation; the attempt is rejected and the audit tripwire fires
# naming the matched entry. Extend only via phase-level RFC.
NEVER_DEMOTE_CLASSES = frozenset(
    {
        SECURITY_PROPERTY_CLAIM,
        SPEC_REQUIRED_BEHAVIOUR_CLAIM,
        UNRESOLVABLE_CITE,
        NON_COMMENT,
    }
)  # 4 items

#: fallout GI-004 / AC-023 / OT-019 (D-078, concern from casting 4) — WHICH
#: ENTRIES READ THE FINDING'S SUBJECT RATHER THAN ITS CLAIM.
#:
#: The four entries are not one rule. Three of them read what the finding
#: CLAIMS — a security property, a stated requirement's behaviour, a cite that
#: does not resolve — and one reads its SUBJECT: NON_COMMENT exists only
#: because recording an OBSERVATION used to be a demotion, so a finding about
#: code arriving in the observation ledger was a defect in hiding.
#:
#: Two doors need the CLAIM half alone and each used to spell the exclusion for
#: itself — `validate_defect_filing`'s HARDENING rung and
#: `record_denylist_tripwire`'s `comment_subject_required` lift. That is one
#: ruling in two voices, so the split is declared HERE, where the entries are.
#: Extend only via phase-level RFC.
NEVER_DEMOTE_SUBJECT_CLASSES = frozenset({NON_COMMENT})  # 1 item

#: The CLAIM half, DERIVED rather than re-listed, so a fifth claim entry joins
#: every rung that reads it by construction and never by memory — which is the
#: property the D-078 rung asks for in prose and could not previously enforce.
NEVER_DEMOTE_CLAIM_CLASSES = frozenset(
    NEVER_DEMOTE_CLASSES - NEVER_DEMOTE_SUBJECT_CLASSES
)  # 3 items

# ---------------------------------------------------------------------------
# Field readers — total, never-raising accessors over a finding mapping.
# ---------------------------------------------------------------------------


def _text(finding: Mapping[str, object], key: str) -> str:
    """Return finding[key] when it is a str, else "" (missing == no match)."""
    value = finding.get(key)
    return value if isinstance(value, str) else ""


def _flag(finding: Mapping[str, object], key: str) -> bool | None:
    """Return finding[key] when it is a real bool, else None (unknown)."""
    value = finding.get(key)
    return value if isinstance(value, bool) else None


# ---------------------------------------------------------------------------
# Prose patterns.
# ---------------------------------------------------------------------------

# Shared "this no longer agrees with reality" cue. Every observation class
# requires one: a comment merely MENTIONING a line or a count is not a
# finding — the finding is that it disagrees with the code.
_MISMATCH = (
    r"(?:stale|outdated|out[- ]of[- ]date|no longer|drift\w*|moved|shifted"
    r"|wrong|incorrect|inaccurate|mismatch\w*|disagree\w*|does not match"
    r"|doesn't match|off by|but (?:the|it|there|they|only|now)|now )"
)
_OMISSION = (
    r"(?:omits?|omitted|missing|incomplete|lacks?|does not (?:include|mention|list))"
)

# The four class cues are disjoint word sets, so a finding naming one class's
# subject does not incidentally match another's.
_LINE_CUE = r"\blines?\s*(?:numbers?|hints?|refs?|references?)?\b"
_COUNT_CUE = (
    r"(?:\bcounts?\b|\bnumber of\b|\btall(?:y|ies)\b"
    r"|\b\d+\s+(?:items?|entries|members?|streams?|tokens?|cohorts?|elements?"
    r"|values?|fields?|rows?|cases?))"
)
_DIRECTION_CUE = (
    r"\b(?:above|below|following|preceding|earlier|later|previous|next"
    r"|upstream|downstream|first|last)\b"
)
_ENUM_CUE = (
    r"(?:\benumerat\w*\b|\blists?\b|\blisted\b|\blisting\b|\bbullets?\b"
    r"|\broster\b|\btable\b)"
)


def _near(cue: str, other: str) -> re.Pattern[str]:
    """Compile "cue ... other" OR "other ... cue" within one clause."""
    gap = r"[^.;]{0,120}?"
    return re.compile(
        f"(?:{cue}){gap}(?:{other})|(?:{other}){gap}(?:{cue})",
        re.IGNORECASE,
    )


_LINE_DRIFT_RE = _near(_LINE_CUE, _MISMATCH)
_PROSE_COUNT_RE = _near(_COUNT_CUE, _MISMATCH)
_DIRECTION_WORD_RE = _near(_DIRECTION_CUE, _MISMATCH)
_ENUMERATION_RE = _near(_ENUM_CUE, f"{_MISMATCH}|{_OMISSION}")

# Denylist patterns. Deliberately broad — see the precedence rule in the
# module docstring: over-matching costs an observation, under-matching would
# demote a real security or spec-behaviour finding.
#
# D-090 / D-093 widened this set after a ten-case battery of textbook
# security-property claims found EIGHT of them demotable with the tripwire
# silent. The original enumerated specific security nouns and omitted both the
# word "security" itself and the vocabulary most real claims are written in —
# signature, HMAC, constant-time, plaintext, rate limit, CORS, nonce, bounds
# check. That is the module's own unacceptable failure mode, so the terms
# below are grouped by the property they assert rather than by any attack
# taxonomy: a claim about a property IS in scope even when no attack is named.
#
# Boundedness is what keeps this from degenerating into matching everything:
# the whole alternation sits inside `\b(?:...)\b`, so a term appearing inside
# an identifier does not match (`validate_report` and `compare_digest` are
# both misses — `_` is a word character, so the trailing \b fails). Terms that
# are ordinary English on their own are required to appear in their security
# sense as a phrase (`untrusted input`, `input validation`, `timing attack`)
# rather than bare.
_SECURITY_RE = re.compile(
    r"""\b(?:
        auth (?:n|z|entication|orization|orisation)?
      | authenticat\w+ | authoris\w+ | authoriz\w+
      | permission\w* | privileg\w* | credential\w* | secrets?
      | passwords? | passphrase\w* | api[- ]?keys?
      | csrf | xsrf | xss | ssrf | rce | clickjack\w*
      | sql \s+ injection | command \s+ injection
      | injection \s+ (?:flaw|risk|vector|attack)
      | (?:path|directory) \s+ traversal
      | sanitiz\w* | sanitis\w*
      | escap (?:e|ed|ing) \s+ (?:user|input|html|shell)
      | encrypt\w* | decrypt\w* | cryptograph\w* | ciphertext
      | tls | ssl | certificate \s+ (?:validation|verification|pinning)
      | vulnerab\w* | exploit\w* | attacker\w* | threat \s+ model
      | (?:access|bearer|session|auth|csrf|api) \s+ tokens?
      | sandbox \s+ (?:escape|bypass) | privilege \s+ escalation
      | arbitrary \s+ (?:code|command|file)

      # --- D-090: the bare word the original omitted -------------------
      | securit (?:y|ies)

      # --- D-093: message authenticity and secrecy ---------------------
      | signatures? | signed | unsigned | hmac | macs? | digests?
      | hash (?:ed|ing)                      # not bare "hash" — see Foundry-Spec-Hash
      | salted | salting
      | plain [-\s]? text | cleartext
      | integrity | tamper\w*

      # --- D-093: timing and side channels -----------------------------
      | constant [-\s]? time
      | timing \s+ (?:attack|leak|side [-\s]? channel)
      | side [-\s]? channel

      # --- D-093: trusting unchecked input -----------------------------
      | untrusted \s+ (?:input|data|user|source|value)
      | unvalidated | input \s+ validation

      # --- fallout D-056: the verb form, BOUND to a credential document --
      #
      # `validat (?:e|es|ed|ing|ion|or)` sat BARE in the group directly above
      # and matched the ordinary English words validate / validated /
      # validator wherever they appeared, with no phrase binding at all. That
      # contradicted this block's own discipline four paragraphs up ("terms
      # that are ordinary English on their own are required to appear in their
      # security sense as a phrase"), and the intended phrase form
      # `input \s+ validation` already sat on the line before it — so the bare
      # form added only false positives. It refused two TEMPER_CANDIDATE
      # observations whose prose named a validator and fired the audit
      # tripwire for both, on a package whose entire subject is doors that
      # validate filings: CT-017 admits exactly ONE error on that door and
      # this was not it, so the only channel a TEMPER-on PROVE has for an
      # off-row probe idea (AC-018 / FR-017) had nowhere to land.
      #
      # THE ONE SENSE THE BARE FORM CARRIED THAT NO OTHER MEMBER DOES is a
      # claim that a credential DOCUMENT is validated — "the certificate is
      # validated", "the JWT is validated" — because neither noun is a member
      # on its own and the TLS line above wants the noun phrase
      # (`certificate validation`), not the verb. Driven both ways before the
      # narrowing: with the bare form removed the D-093 battery's
      # input-validation case still matches (on `untrusted input`), and those
      # two claims stopped matching. So that sense is kept HERE, as a phrase,
      # which is what the discipline asks for. The gap is at most two words so
      # "is/are/was/never" reach the verb and a sentence away does not.
      | (?:certificates?|jwts?) \s+ (?:\w+ \s+){0,2}? validat\w*

      # --- D-093: availability and origin controls ---------------------
      | rate [-\s]? limit\w* | throttl\w*
      | cors | same [-\s]? origin

      # --- D-093: freshness -------------------------------------------
      | nonces? | replay \s+ (?:attack|protection|prevention)

      # --- D-093: memory safety ----------------------------------------
      | bounds [-\s]? check\w* | bounds \s+ (?:are|is) \s+ check\w*
      | out [-\s]? of [-\s]? bounds
      | overflow\w* | underflow\w* | overread | overrun
      | buffer \s+ over\w+
    )\b""",
    re.IGNORECASE | re.VERBOSE,
)

# ---------------------------------------------------------------------------
# D-150 — the requirement-ID grammar, declared ONCE.
#
# This literal was hand-typed in SEVEN places across five modules
# (foundry_handoff, evidence, foundry_validate ×3, foundry_orchestrator, and a
# narrower US|FR-only copy in test_deriver), and every wide copy knew the same
# seven families and neither OT- nor GI-. Fifteen of this run's own 71 spec IDs
# were therefore invisible to the acceptance gate, the DONE gate's requirement
# count and the verdict-coverage synthesis — and no evidence file could bind to
# an observable truth at all, because `# evidence-for: OT-011` parsed to the
# empty list and was silently dropped.
#
# PROVENANCE — there is no single authoritative emitter, so the set is the
# union of the grammars that actually produce these IDs, each cited:
#
#   FR, NFR, AC, GI, US, OT   the forge spec emitter's own bullet grammar, in
#                             three separate literals inside
#                             plugins/forge/scripts/validate-spec.py — the
#                             typed-bullet matcher (:232), the Locked/Flexible
#                             classifier (:854) and the citation-marker check
#                             (:952). A fourth (:706) carries the narrower
#                             FR|NFR|AC|GI subset for Locked items only.
#   GI, ST, CT                the three typed tables forge emits, named by
#                             validate-spec.py's TYPED_SECTION_HEADINGS (:142)
#                             = Global Invariants, State Transitions,
#                             Contracts. Every row of those tables carries an
#                             ID of the matching family.
#   LR                        "Locked Requirement" — the bullet family forge
#                             emits under a spec's `### Locked (implement
#                             exactly as specified)` heading. Found by
#                             ``test_every_id_prefix_in_a_real_spec_is_classified``
#                             on its first run, in
#                             forge-specs/codsworth-serena-daemon-lifecycle/spec.md
#                             (LR-001..LR-004, 19 uses across the shipped
#                             specs). Nothing in foundry had ever matched it,
#                             so an entire spec's Locked requirements were
#                             uncountable and unbindable — the same hole as
#                             OT- and GI-, in a family nobody had named. This
#                             is the reporting rung doing its job.
#
#   VC, IR, TR                foundry-only families carried by the pre-D-150
#                             literal. They appear in NONE of the five shipped
#                             forge specs, so no grammar emits them today, but
#                             they are RETAINED rather than pruned: NFR-002's
#                             no-narrowing guarantee means nothing that was
#                             counted before this change may stop being
#                             counted, and an unused family costs a branch.
#
# WHAT IS DELIBERATELY NOT HERE. Three other namespaces share the `XX-NNN`
# shape and must never be swept into a requirement count — an interview answer
# is not a requirement, and a stream that ran is not a requirement that was
# met. They are declared below so the sweep over a real spec can PARTITION
# every observed prefix instead of silently ignoring what it does not know.
REQUIREMENT_ID_PREFIXES: frozenset[str] = frozenset(
    {
        "US", "FR", "NFR", "AC",      # forge core requirement families
        "GI", "ST", "CT",             # forge typed-table families
        "OT",                         # forge observable truths
        "LR",                         # forge "Locked" requirement bullets
        "VC", "IR", "TR",             # foundry-only, retained per NFR-002
    }
)  # 12 families

#: The `XX-NNN`-shaped namespaces that are NOT requirements. Declared so that
#: ``test_every_prefix_in_a_real_spec_is_classified`` can report a prefix that
#: belongs to NEITHER set, rather than letting a new family fall silently
#: through the gap between them.
NON_REQUIREMENT_ID_PREFIXES: frozenset[str] = frozenset(
    {
        "A",        # interview answer ids, incl. A-AUTO-NNN implicit facts
        "AUTO",     # the tail of A-AUTO-NNN when read without its `A-` head
        "FLAG",     # R3.5 spec-review ambiguity flags (validate_spec_review.py)
        "TEST",     # TEST-01, a verification STREAM id (CANONICAL_STREAM_IDS)
        "EVID",     # EVID-01 / EVID-02, likewise stream ids
        "INTV", "TYPE", "PROBE", "INTENT",   # the remaining numbered streams
        "D",        # defect ids in the run ledger
        "P",        # phase/packet ids in planning documents
        # Both observed in `forge-specs/foundry-run-fallout/spec.md` and
        # reported by `test_every_id_prefix_in_a_real_spec_is_classified`,
        # which is the mechanism this partition exists for: a new family falls
        # into the gap between the two sets and the sweep NAMES it rather than
        # silently ignoring it. Neither is a requirement, so neither may be
        # swept into a requirement count.
        "OBS",      # observation ids in the run's observations ledger (A-000:
                    #   "TEST-01 ... caught the D-222 fix over-correcting
                    #   (OBS-026 -> D-231)"). A finding nobody has to fix is
                    #   not a requirement anybody has to meet.
        "RA",       # RESEARCH_AUDIT roster ITEM ids (A-019: "RESEARCH_AUDIT
                    #   re-derives RA-1..RA-n from the spec's Informational
                    #   lines each cycle"). A roster row is a thing to CHECK,
                    #   not a thing the build owes — and FR-020's persisted
                    #   `rosters/<stream>.json` is where they live.
    }
)  # 13 non-requirement namespaces

#: The requirement-ID grammar. Built FROM the prefix set above rather than
#: re-typed beside it, so the families are the single axis: add a prefix and
#: every reader of this pattern sees it, with no second literal to forget.
#: Longest-first alternation is defensive only — `\b` already prevents `FR`
#: from matching inside `NFR-001`.
REQUIREMENT_ID_RE: re.Pattern[str] = re.compile(
    r"\b(?:"
    + "|".join(sorted(REQUIREMENT_ID_PREFIXES, key=lambda p: (-len(p), p)))
    + r")-\d+(?:\.\d+)?\b"
)


def is_requirement_id(token: str) -> bool:
    """True when ``token`` is EXACTLY one requirement ID and nothing else.

    ``REQUIREMENT_ID_RE`` is a scanner — callers use ``findall`` to pull IDs
    out of prose. This is the membership question for a single token, so it
    anchors both ends: ``"AC-001"`` is an ID, ``"see AC-001 below"`` is not.
    """
    return REQUIREMENT_ID_RE.fullmatch(token) is not None


#: Retained private alias — this module's own predicates were written against
#: it, and the two must never diverge.
_REQUIREMENT_ID_RE = REQUIREMENT_ID_RE
_SPEC_CLAIM_RE = re.compile(
    r"\b(?:spec(?:ification)?\s+(?:requires?|mandates?|says?|demands?)"
    r"|required\s+behaviou?r|spec[- ]required"
    r"|acceptance\s+criteri(?:on|a)|must_haves?)\b",
    re.IGNORECASE,
)
# D-090 widened this too. It covered "resolves to nothing" but not the three
# ways a stream actually writes the same fact — "resolves to no symbol", "no
# symbol named X", "the symbol does not exist" — so a cite that names nothing
# was demotable. The subject nouns are enumerated (symbol/function/method/
# class/definition) rather than left open so that ordinary absence prose ("the
# guard is missing") is still an ordinary finding and not a cite complaint.
_SYMBOL_NOUN = r"(?:symbol|function|method|class|definition|identifier)"
_UNRESOLVABLE_RE = re.compile(
    r"(?:does\s+not\s+resolve|doesn't\s+resolve|cannot\s+be\s+resolved"
    r"|can't\s+be\s+resolved|unresolvable|unresolved\s+(?:cite|symbol|reference)"
    r"|no\s+such\s+symbol|symbol\s+not\s+found|resolves?\s+to\s+nothing"
    rf"|resolves?\s+to\s+no\s+{_SYMBOL_NOUN}"
    rf"|no\s+{_SYMBOL_NOUN}\s+(?:named|called|by\s+that\s+name)"
    rf"|{_SYMBOL_NOUN}\s+(?:that\s+|which\s+)?"
    r"(?:does\s+not\s+exist|doesn't\s+exist|is\s+missing|no\s+longer\s+exists)"
    rf"|{_SYMBOL_NOUN}\s+missing\s+from"
    r"|dangling\s+(?:cite|reference|symbol))",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Observation-class predicates (AC-001).
# ---------------------------------------------------------------------------


def is_line_drift_cite(finding: Mapping[str, object]) -> bool:
    """The complaint is that a cited line number has drifted (AC-007)."""
    if _flag(finding, "line_hint_stale") is True:
        return True
    return bool(_LINE_DRIFT_RE.search(_text(finding, "description")))


def is_prose_count(finding: Mapping[str, object]) -> bool:
    """The complaint is that a count stated in prose disagrees with code."""
    return bool(_PROSE_COUNT_RE.search(_text(finding, "description")))


def is_direction_word(finding: Mapping[str, object]) -> bool:
    """The complaint is a directional word ("above"/"below") gone stale."""
    return bool(_DIRECTION_WORD_RE.search(_text(finding, "description")))


def is_enumeration(finding: Mapping[str, object]) -> bool:
    """The complaint is that a comment's enumerated list is stale."""
    return bool(_ENUMERATION_RE.search(_text(finding, "description")))


# ---------------------------------------------------------------------------
# Never-demote denylist predicates (AC-002).
# ---------------------------------------------------------------------------


def is_non_comment(finding: Mapping[str, object]) -> bool:
    """The finding's subject is not a code comment.

    Matches whenever `target_kind` is present and is anything other than
    "comment". An ABSENT `target_kind` does not match, per the never-raise
    contract — callers must populate it for AC-002 enforcement to be
    complete.
    """
    kind = _text(finding, "target_kind")
    return bool(kind) and kind.strip().lower() != "comment"


def is_unresolvable_cite(finding: Mapping[str, object]) -> bool:
    """The cite's symbol does not resolve — a defect per AC-006."""
    if _flag(finding, "symbol_resolved") is False:
        return True
    return bool(_UNRESOLVABLE_RE.search(_text(finding, "description")))


def is_security_property_claim(finding: Mapping[str, object]) -> bool:
    """The finding asserts a security property is broken."""
    return bool(_SECURITY_RE.search(_text(finding, "description")))


def is_security_property_text(description: str) -> bool:
    """The same question, asked of a bare description string (CT-003).

    The LATENT denylist refuses a filing whose description matches the
    security-property predicate, and it has a description in hand rather than a
    finding mapping. Delegating (rather than re-searching `_SECURITY_RE` here)
    is what makes it impossible for the two callers to diverge: D-090 and
    D-093 both widened that pattern, and a second search site would have had to
    be found and widened twice.
    """
    return is_security_property_claim({"description": description})


def is_spec_required_behaviour_claim(finding: Mapping[str, object]) -> bool:
    """The finding claims spec-required behaviour is absent or wrong."""
    if _text(finding, "spec_ref").strip():
        return True
    description = _text(finding, "description")
    if _REQUIREMENT_ID_RE.search(description):
        return True
    return bool(_SPEC_CLAIM_RE.search(description))


# ---------------------------------------------------------------------------
# Dispatchers and canonicalisers.
#
# Both tables reference the same name constants declared above, so the
# frozensets and the dispatch order cannot drift apart.
# ---------------------------------------------------------------------------

_OBSERVATION_PREDICATES: tuple[tuple[str, object], ...] = (
    (LINE_DRIFT_CITE, is_line_drift_cite),
    (PROSE_COUNT, is_prose_count),
    (DIRECTION_WORD, is_direction_word),
    (ENUMERATION, is_enumeration),
)

# ORDERED MOST-SPECIFIC FIRST, GENERIC CATCH-ALL LAST (D-083 / AC-007 /
# OT-005 / CT-003).
#
# The order is the answer. `never_demote_class` returns the FIRST match, and
# that answer is what `record_denylist_tripwire` persists as
# `observations.json.tripwire[].denylist_class` — the field an auditor queries
# a run by. So this tuple decides what the audit ledger SAYS happened.
#
# It used to lead with NON_COMMENT, on the argument that it was the most
# "structurally certain" match. `is_non_comment` fires for ANY declared
# `target_kind` other than "comment", so leading with it swallowed every other
# entry whenever the filer named a real subject. Driven through both shipped
# doors with ONE description asserting an authentication property across four
# target_kinds: `code` and `test` persisted NON_COMMENT while
# `validate_defect_filing` refused the very same call naming
# SECURITY_PROPERTY_CLAIM; `comment` and absent persisted
# SECURITY_PROPERTY_CLAIM. One event wrote two artifacts that disagreed — and
# the two shapes that lost the security signal are the DEFAULT shape of every
# production-code filing, so an auditor grepping the tripwire ledger for
# SECURITY_PROPERTY_CLAIM found nothing for exactly the filings AC-007 is
# about.
#
# SECURITY_PROPERTY_CLAIM therefore leads: it is the one entry a REFUSAL also
# names (the LATENT denylist rung in `validate_defect_filing`, which consults
# `is_security_property_text` on the description alone), and the tripwire may
# not disagree with the refusal it was fired for. NON_COMMENT goes last, where
# it now means what an auditor reads it to mean — no more specific entry
# matched, and the SUBJECT alone is why this finding can never be demoted.
#
# Rejected: passing the refusal's class into the tripwire writer as an
# override. That repairs two call sites and leaves this dispatcher — which
# `foundry_add_observation`'s refusal text and the forge-log mirror also read
# — still answering NON_COMMENT, so the same disagreement returns through the
# next caller.
_NEVER_DEMOTE_PREDICATES: tuple[tuple[str, object], ...] = (
    (SECURITY_PROPERTY_CLAIM, is_security_property_claim),
    (UNRESOLVABLE_CITE, is_unresolvable_cite),
    (SPEC_REQUIRED_BEHAVIOUR_CLAIM, is_spec_required_behaviour_claim),
    (NON_COMMENT, is_non_comment),
)


def observation_class(finding: Mapping[str, object]) -> str | None:
    """Name the first comment-prose observation class that matches, else None.

    Callers MUST consult `never_demote_class` first: a finding matching both
    is a DEFECT (see the precedence rule in the module docstring).
    """
    for name, predicate in _OBSERVATION_PREDICATES:
        if predicate(finding):
            return name
    return None


def never_demote_class(finding: Mapping[str, object]) -> str | None:
    """Name the MOST SPECIFIC denylist class that matches, else None.

    A non-None result means the finding can never be recorded as an
    observation; the caller rejects the demotion and fires the audit
    tripwire naming exactly this class.

    "Most specific" is carried by `_NEVER_DEMOTE_PREDICATES`' declaration
    order, not decided here — see that tuple's comment for why the generic
    NON_COMMENT catch-all is evaluated last (D-083).
    """
    for name, predicate in _NEVER_DEMOTE_PREDICATES:
        if predicate(finding):
            return name
    return None


#: fallout GI-033 / D-080 (concern C-059 row 8) — THE ONE MEANING OF `--no-ui`.
#:
#: `survey/surface.md` FI-2 found the flag meaning THREE different things at
#: once: setup-foundry.sh's help said "Skip browser audit (SIGHT)", README.md's
#: table said "Suppress orchestrator banners", and the SIGHT check treated
#: `manifest.no_ui` as a HARD BLOCK — a refusal, which is neither of the other
#: two. A flag whose meaning depends on which document the operator read is a
#: flag nobody can use correctly, and the three readings disagree about the
#: DIRECTION of the effect, not merely its wording.
#:
#: The chosen meaning is the one the flag's own NAME carries and that two of
#: the three surfaces were already reaching for. It says nothing about banners:
#: the display is not a UI the run audits, and a flag that suppressed output
#: would need its own name.
#:
#: IT LIVES HERE because `orchestration/width.py` and `orchestration/teams.py`
#: — the verifier layer — both reach `tools/foundry.py` for it, and GI-033 puts
#: a closed-vocabulary sentence read across layers in the vocabulary rather than
#: in the largest lifecycle module in the tree. Extend only via phase-level RFC.
NO_UI_MEANING = (
    "`--no-ui` declares that this run has no browsable UI, so the SIGHT "
    "browser audit is not part of it."
)


def never_demote_claim_class(finding: Mapping[str, object]) -> str | None:
    """The denylist class this finding's CLAIM matches, else None.

    `never_demote_class` narrowed to `NEVER_DEMOTE_CLAIM_CLASSES`: the answer
    when a claim entry matched, and None when the only thing that matched was
    the SUBJECT entry (or nothing did). Total; never raises.

    THIS IS SAFE ONLY BECAUSE NON_COMMENT IS ORDERED LAST, and that is a real
    dependency rather than a coincidence worth noting. `never_demote_class`
    returns the FIRST match and `_NEVER_DEMOTE_PREDICATES` evaluates the
    generic subject catch-all after every claim entry (D-083), so an answer of
    NON_COMMENT already MEANS "no claim entry matched". Dropping it therefore
    yields the claim class exactly. Were the catch-all moved earlier, this
    would start returning None for findings that DO make a claim — which is
    the security signal D-083 was filed for — so the ordering is pinned in
    `tests/test_vocab.py` beside this.

    THE CALLER STILL SHAPES THE FINDING. `validate_defect_filing`'s HARDENING
    rung neutralises `spec_ref` before asking, because AC-055 gives the
    locator its own named refusal; `record_denylist_tripwire` asks the raw
    finding. What is shared is WHICH ENTRIES ARE CLAIMS, and that is all this
    answers — the shaping belongs to the door and stays there.
    """
    matched = never_demote_class(finding)
    return matched if matched in NEVER_DEMOTE_CLAIM_CLASSES else None


def canonical_defect_type(value: str) -> str | None:
    """Canonical spelling for a member of DEFECT_TYPES, else None.

    Returns None for a non-member so the caller can build a named refusal.
    Never raises, and never coerces an unknown value onto a known one.
    """
    if not isinstance(value, str) or value not in DEFECT_TYPES:
        return None
    return _DEFECT_TYPE_ALIASES.get(value, value)


def canonical_stream_id(value: str) -> str | None:
    """Canonical UPPERCASE stream id for a wire id, else None.

    Identity on members of CANONICAL_STREAM_IDS. Never raises, and never
    coerces an unknown value onto a known stream.
    """
    if not isinstance(value, str):
        return None
    if value in CANONICAL_STREAM_IDS:
        return value
    return WIRE_TO_CANONICAL.get(value)


def canonical_defect_source(value: str) -> str | None:
    """Canonical UPPERCASE name for a defect record's `source`, else None.

    `canonical_stream_id` is the WRONG resolver for this field and D-091 is
    what that costs: it knows only the nine stream wire ids, so an `assay`- or
    `temper`-filed defect resolves to None and a caller that treats None as
    "unknown value" both drops the record and reports a legal source as
    unknown. Resolve a `source` here and a stream there.

    Identity on an already-canonical spelling. Never raises, and never coerces
    an unknown value onto a known source.
    """
    if not isinstance(value, str):
        return None
    if value in _CANONICAL_DEFECT_SOURCES:
        return value
    return DEFECT_SOURCE_TO_CANONICAL.get(value)
