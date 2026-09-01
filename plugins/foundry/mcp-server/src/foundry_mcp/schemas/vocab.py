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
classes, not machine types. The safety of the scheme does not rest on the
observation regexes being exhaustive; it rests on the precedence rule below.

PRECEDENCE RULE (AC-002 — the never-weaken guarantee)
-----------------------------------------------------
`never_demote_class` OUTRANKS `observation_class`. A finding matching both is
a DEFECT, and the denylist match is what a caller reports so the audit
tripwire can name exactly which entry fired. Because of this ordering the
denylist patterns are deliberately biased toward OVER-matching: a false
denylist hit costs one observation that stays a defect, while a false miss
would demote a real security or spec-behaviour finding. Only the second
failure mode is unacceptable.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
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

# CLOSED VOCABULARY — findings of these classes are recordable in the
# observations ledger instead of the defect ledger, UNLESS a denylist entry
# also matches. Extend only via phase-level RFC.
OBSERVATION_CLASSES = frozenset(
    {LINE_DRIFT_CITE, PROSE_COUNT, DIRECTION_WORD, ENUMERATION}
)  # 4 items

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
    )\b""",
    re.IGNORECASE | re.VERBOSE,
)

# Requirement-ID families used across forge/foundry specs.
_REQUIREMENT_ID_RE = re.compile(
    r"\b(?:US|FR|NFR|AC|VC|IR|TR|GI|CT|ST|OT)-\d+(?:\.\d+)?\b"
)
_SPEC_CLAIM_RE = re.compile(
    r"\b(?:spec(?:ification)?\s+(?:requires?|mandates?|says?|demands?)"
    r"|required\s+behaviou?r|spec[- ]required"
    r"|acceptance\s+criteri(?:on|a)|must_haves?)\b",
    re.IGNORECASE,
)
_UNRESOLVABLE_RE = re.compile(
    r"(?:does\s+not\s+resolve|doesn't\s+resolve|cannot\s+be\s+resolved"
    r"|can't\s+be\s+resolved|unresolvable|unresolved\s+(?:cite|symbol|reference)"
    r"|no\s+such\s+symbol|symbol\s+not\s+found|resolves?\s+to\s+nothing"
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

# Ordered by descending structural certainty, so the class a caller reports
# on the audit tripwire is the most mechanically-grounded one that matched.
_NEVER_DEMOTE_PREDICATES: tuple[tuple[str, object], ...] = (
    (NON_COMMENT, is_non_comment),
    (UNRESOLVABLE_CITE, is_unresolvable_cite),
    (SECURITY_PROPERTY_CLAIM, is_security_property_claim),
    (SPEC_REQUIRED_BEHAVIOUR_CLAIM, is_spec_required_behaviour_claim),
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
    """Name the first denylist class that matches, else None.

    A non-None result means the finding can never be recorded as an
    observation; the caller rejects the demotion and fires the audit
    tripwire naming exactly this class.
    """
    for name, predicate in _NEVER_DEMOTE_PREDICATES:
        if predicate(finding):
            return name
    return None


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
