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
#   LIVE    the stream drove the door and observed the wrong result. The
#           description carries the reproduction.
#   LATENT  the stream looked for the failure and did not find one — a gap
#           reasoned about, not reproduced. The filing must carry a
#           `reproduction_attempted` statement naming what was driven and what
#           it found (`reproduction_attempted_problem` below is the check).
#
# Both tiers are DEFECTS and both get fixed; the axis decides only which GATE
# a still-open instance blocks (ASSAY blocks on either; TEMPER, NYQUIST and
# DONE block on LIVE alone). A grade would let a stream write "minor" and move
# on; this cannot, because neither value is an excuse — it is a statement about
# evidence the stream is answerable for.
#
# Extend only via phase-level RFC — and never with a third value that means
# "less important than LIVE", which is the grade returning under a new name.
# ---------------------------------------------------------------------------

# CLOSED VOCABULARY — the evidence tier a filing stream sets on every defect.
DEFECT_TIERS = frozenset({"LIVE", "LATENT"})  # 2 items

#: READ-SIDE SENTINEL ONLY — never written by a filing door, and deliberately
#: NOT a member of DEFECT_TIERS. A record persisted before this release has no
#: `tier` key at all; every reader resolves that absence to this value rather
#: than guessing. FR-051: an unknown-tier defect blocks exactly like LIVE and
#: is reported in its own column — reading it as LATENT would silently clear
#: gates on records nobody ever classified.
TIER_UNKNOWN = "unknown"

# The full READ vocabulary: what a reader may see, as opposed to what a door
# may write. Derived from DEFECT_TIERS so adding a tier needs one edit.
DEFECT_TIER_OR_UNKNOWN = frozenset(DEFECT_TIERS | {TIER_UNKNOWN})  # 3 items


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


#: Statements that name no evidence at all. A LATENT filing must say what was
#: DRIVEN and what it found; these are the spellings of "I did not". Matched
#: against the whole stripped, lowercased statement, so a real sentence that
#: happens to contain "none" is unaffected.
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
            f"names no evidence. A LATENT filing must say what was driven and "
            f"what it found (e.g. 'AST sweep of both roots finds 0 sites')."
        )
    if len(stripped) < REPRODUCTION_ATTEMPTED_MIN_CHARS:
        return (
            f"reproduction_attempted is {len(stripped)} characters; at least "
            f"{REPRODUCTION_ATTEMPTED_MIN_CHARS} are needed to name what was "
            f"driven and what it found. Either state the negative result, or "
            f"file the defect as LIVE with its reproduction."
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
ESCALATION_STATUSES = frozenset({"ESCALATED", "CLEARED"})  # 2 items

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
# WHY THE SERVER PACKAGE IS MATCHED WHOLE AND NOT MODULE BY MODULE (D-033)
# ------------------------------------------------------------------------
# This pattern was an alternation of five `foundry_mcp/tools/` basenames, and
# an enumeration of the modules that judge the build is a list that goes stale
# the first time one is added. Driven: `is_verifier_path` over all 55 key_files
# of this run's own manifest returned False for `foundry_mcp/tools/`
# foundry_report.py — whose `report_status` IS the DONE precondition —
# foundry_spawn.py (`_manifest_shape_problem`, called by `foundry_gate`),
# foundry_state.py, display.py and citation.py. Every one of those is imported
# by a gate path, so a GRIND diff moving the DONE precondition itself would
# have been judged by a DELTA roster.
#
# Naming the five misses would have fixed the instance and kept the class, so
# the rule is now the PACKAGE: every module under a `foundry_mcp/` segment is
# verifier machinery, because everything in it is a gate, an orchestrator, a
# stream, or something one of those imports. The alternative shapes were both
# rejected: matching all of `plugins/foundry/` sweeps in README.md and the
# plugin manifest (a README edit is not the verifier moving) and would delete
# DELTA outright for a self-targeting run; deriving the set from the
# orchestrator's import graph is exact but needs filesystem and AST work,
# which the PURITY RULE at the top of this module forbids.
#
# The cost is bounded and deliberate. A run building anything other than
# foundry never has `foundry_mcp/` in its diff, so the saving DELTA exists for
# is untouched there; in a self-targeting run the verifier genuinely is
# moving. The server's own tests are NOT swept in: they are the
# pins, not the judgement, the TEST stream re-runs them at every width, and
# leaving them out keeps a real DELTA case in the run that edits this file
# most.
#
# WHY THE PROSE RULE IS "LOADED CONTRACT", NOT A ROSTER OF DIRECTORIES (D-118)
# ----------------------------------------------------------------------------
# D-033 above replaced a basename roster with a package rule on the CODE side
# and stopped there; the PROSE side went on naming its members one directory
# at a time (`agents/`, `skills/`, `commands/`) and went stale exactly the same
# way. Driven: `is_verifier_path` returned False for
# `plugins/foundry/references/verification-patterns.md` and for
# `references/lead-discipline.md` — both loaded as binding contracts, the first
# by `agents/tracer.md` and `agents/assayer.md` via
# `@${CLAUDE_PLUGIN_ROOT}/references/…`, the second by `commands/start.md` —
# and False for `plugins/foundry/scripts/validate-test-observations.py`, which
# the TEST-01 adjudicator EXECUTES as its Layer 1 and halts on. A GRIND whose
# only touched file was the tracer's own verification contract therefore
# recorded DELTA, and a change to the verifier was judged by a narrow INSPECT.
#
# So the prose rule is now ONE rule over the directories that hold loaded
# contracts, and the discriminator is not the directory name but what the
# document IS: something an agent or the lead is instructed to read as binding,
# or a validator a stream shells out to. That boundary is what keeps the
# no-answers honest — a README documents the plugin to a human, the setup and
# update shells install it, `measure-run.py` and `migrate-archive.py` read a
# finished archive and print, and `hooks/pre-commit-guard.sh` constrains what
# may be COMMITTED rather than judging whether the build is right. None of
# those moving can make a previous cycle's verdict wrong, which is the only
# thing `verifier_touched` is for.
#
# An enumeration is still an enumeration, though, so the anti-staleness
# mechanism does not live here at all: `tests/test_vocab.py` harvests every
# `${CLAUDE_PLUGIN_ROOT}/….md` load target out of the shipped prose and
# asserts each one answers True. A new contract file — in `references/` or in
# a directory nobody has invented yet — fails that pin the moment an agent
# names it. Deriving the set here instead would need filesystem and AST work,
# which the PURITY RULE at the top of this module forbids.
# Extend only via phase-level RFC.
VERIFIER_PATH_PATTERNS: tuple[str, ...] = (
    # The canonical vocabulary itself, wherever it sits.
    r"(?:^|/)vocab\.py$",
    # Any schema module — the finding/report shapes every stream validates on.
    r"(?:^|/)schemas/",
    # The whole server package: gates, orchestrator, streams, parsers, and
    # every module those import. See the D-033 note above for why this is one
    # segment rule and not a roster of basenames.
    r"(?:^|/)foundry_mcp/(?:[^/]+/)*[^/]+\.py$",
    # LOADED CONTRACT PROSE — the documents a stream or the lead is instructed
    # to read as binding: stream contracts (`agents/`), run protocol
    # (`commands/`), and the shared references both of those pull in
    # (`references/`). One rule over the three flat directories, per the D-118
    # note above; a README is not in it because a README is not loaded.
    r"(?:^|/)(?:agents|commands|references)/[^/]+\.md$",
    # Skills sit one segment deeper and the non-SKILL files beside them (a
    # skill's own README) are not contracts, so this one keeps its own shape
    # rather than folding into the alternation above.
    r"(?:^|/)skills/[^/]+/SKILL\.md$",
    # The validators a stream SHELLS OUT TO. `foundry_mcp/` already covers the
    # in-package twin (`foundry_mcp/scripts/validate_intent_coverage.py`); this
    # is the standalone plugin CLI the adjudicator agents actually invoke
    # through `${CLAUDE_PLUGIN_ROOT}/scripts/`. Both spellings, because the two
    # halves of that pair disagree about hyphen versus underscore already.
    r"(?:^|/)scripts/validate[-_][^/]+\.py$",
)  # 6 patterns

_VERIFIER_PATH_RES: tuple[re.Pattern[str], ...] = tuple(
    re.compile(pattern) for pattern in VERIFIER_PATH_PATTERNS
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
    """
    normalised = _normalise_path(path)
    if not normalised:
        return False
    if any(pattern.search(normalised) for pattern in _VERIFIER_PATH_RES):
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
# added-plus-deleted lines. A LATENT lead fix is NOT measured (CT-006): its
# fix_commit is recorded and left alone.
LEAD_LANE_MAX_FILES = 1
LEAD_LANE_MAX_LINES = 20

#: FR-034 — the basenames pytest itself collects, plus the shared fixture
#: module. `python_files = ["test_*.py"]` in mcp-server/pyproject.toml is the
#: repo's own discovery setting; `*_test.py` and `conftest.py` are carried too
#: because the lane counts NON-TEST files, and a file the repo would not
#: collect today but every reader calls a test must not silently consume the
#: one-file budget.
_TEST_BASENAME_RES: tuple[re.Pattern[str], ...] = (
    re.compile(r"^test_[^/]*\.py$"),
    re.compile(r"^[^/]*_test\.py$"),
    re.compile(r"^conftest\.py$"),
)

#: Any path segment spelled exactly this is a test tree, whatever the file
#: inside it is called — fixtures, __init__.py and data files included.
TEST_DIRECTORY_SEGMENT = "tests"


def is_test_file(path: str) -> bool:
    """True when `path` is a test file for the FR-034 lane count.

    Matches the repo's pytest discovery (`test_*.py`), the two conventional
    spellings beside it (`*_test.py`, `conftest.py`), and anything under a
    `tests` directory segment. Pure and never raises.
    """
    normalised = _normalise_path(path)
    if not normalised:
        return False
    segments = normalised.split("/")
    if TEST_DIRECTORY_SEGMENT in segments[:-1]:
        return True
    basename = segments[-1]
    return any(pattern.match(basename) for pattern in _TEST_BASENAME_RES)


#: GI-003 — the handoff event the SERVER appends when it accepts a lead fix.
#: Named here so the writer, the report generator and the F6 section that
#: lists them all spell it identically.
HANDOFF_EVENT_LEAD_FIX = "lead_fix"

# ---------------------------------------------------------------------------
# Run artifacts and terminal states (ST-008 / CT-013 / CT-014 / CT-016 / GI-006).
# ---------------------------------------------------------------------------

#: ST-008 — a run that hits `max_cycles` reaches this phase by a SUCCESSFUL
#: transition, not a refusal. HALTED is a named terminal state and is NOT
#: DONE: the report is generated and every open LIVE and LATENT defect is
#: named in it.
RUN_PHASE_HALTED = "HALTED"

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
# Extend only via phase-level RFC.
REPORT_REQUIRED_SECTIONS = (
    "verdict_matrix",
    "defects_by_tier_and_status",
    "latent_backlog",
    "unknown_tier_defects",
    "escalated_classes",
    "lead_fix_records",
    "inspect_modes_per_cycle",
    "spend_per_phase_and_cycle",
    "unreported_dispatches",
    "executing_versions",
    "baseline_comparison",
)  # 11 sections

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
      | validat (?:e|es|ed|ing|ion|or)

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
    }
)  # 11 non-requirement namespaces

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
