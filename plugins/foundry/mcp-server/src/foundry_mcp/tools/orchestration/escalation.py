"""The recurring-defect class ledger: escalate, propose, exit.

Survey block AA, plus `_escalation_exit_distances`, which lived four thousand
lines from every other reader of the record it describes.
"""
from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path

from collections.abc import Callable
from foundry_mcp.schemas.vocab import (
    ESCALATION_STATUS_CLEARED,
    ESCALATION_STATUS_ESCALATED,
    LIVE_CLEAN_CYCLES_TO_CLEAR,
    STRUCTURAL_PASS_BUDGET,
    canonical_defect_type,
    defect_tier,
    escalation_status as _escalation_status,
)
from foundry_mcp.tools.artifacts import (
    _document_transaction,
    _load_json,
    _read_text,
)
from foundry_mcp.tools.foundry_state import (
    current_cycle,
    get_run_dir,
    now_iso,
)
from pathlib import Path




#: D-153 — the clean-cycle crossing, spelled for the phase the reader is IN.
#: The crossing itself is always an `inspect_start` OUT OF F3; what differs is
#: what it takes to be standing in F3, and naming only the destination is what
#: sent a lead at F2 into a refusal (see `_still_escalated_notice`).
_CLEAN_CYCLE_CROSSING_DEFAULT = (
    "Foundry-Phase(phase='inspect_start') out of F3 closes one"
)




def _escalation_exit_distances(
    fdir: Path, project_root: str, classes: list[str], *, crossing: str = ""
) -> str:
    """One sentence per still-escalated class: which arm clears it, and how far.

    D-111 — A REFUSAL THAT NAMES NO REACHABLE CALL IS NOT A REMEDY.
    --------------------------------------------------------------
    The DONE refusal named both arms in the abstract and left the lead to work
    out which one was closer, and for a class with no persisted
    `escalation.json` entry neither was reachable at all — following the hint
    six times moved nothing. With the record now written at the `inspect_start`
    boundary, both distances are readable off it, so the refusal states them:
    how many more clean crossings the clean arm needs (ST-001), and how many
    more structural packets the budget arm needs (ST-002). Whichever arm fires
    first wins, so both are quoted and the shorter one is obvious.

    D-154 — AND THE BUDGET ARM IS QUOTED ONLY WHERE IT CAN FIRE.
    -----------------------------------------------------------
    "N more structural packet(s)" was printed unconditionally, including for a
    class with every instance CLOSED — the state both of this run's escalated
    classes are in. Driven: the sentence offered "2 more structural packet(s)
    (budget arm, Foundry-Tasks emits one per class per cycle)" while
    `Foundry-Tasks` on the same run returned `structural_tasks: None`,
    `escalated_classes: []` and left `structural_packets_dispatched` at 0.
    `_spend_structural_budget` is fed `_escalated_classes`, which opens with
    `if not bucket["open"]: continue`, so a class with no open bucket can never
    consume a packet and the offered arm can never advance. It is not a
    deadlock — the clean-cycle arm still fires — but a refusal whose own remedy
    names a route that does not exist is D-111's defect one arm over.

    So the open-instance count is READ, from the same buckets the escalation
    arms read, and a class with nothing open is told that its budget arm cannot
    advance and why. ``crossing`` names the call that actually closes a clean
    cycle from where the READER is standing (D-153); it defaults to the
    destination alone, which is all a caller at a terminal phase can say.

    D-157 — AND THE CLEAN DISTANCE IS THE ARM'S OWN WALK, NOT A SECOND SUM.
    ----------------------------------------------------------------------
    This said `max(0, LIVE_CLEAN_CYCLES_TO_CLEAR - entry["live_clean_cycles"])`,
    which consults neither `escalated_at_cycle` nor `live_clean_cycles_counted`
    — both of which the arm evaluates on every crossing. Driven on AC-002's
    fixture (escalated at cycle 5, counter at 5, the class drawing zero LIVE
    instances in cycle 5): this printed "2 more INSPECT cycle(s)" while the arm
    needed THREE crossings, because the crossing closing cycle 5 is discarded by
    the escalated-before guard. The number is now produced by
    `_clean_arm_crossings_left`, which walks `_clean_arm_step` — the arm itself
    — with every future cycle assumed clean.

    `escalated_at_cycle` comes from the persisted entry when there is one and
    from the ledger derivation otherwise, because a class with no record yet has
    that exact value latched by `_record_escalation_proposals` at the next
    crossing, BEFORE the arm runs on it (see the `inspect_start` branch). When
    neither source knows it, the arm cannot advance and the sentence says so
    rather than printing a number no crossing will honour.

    Reads, never writes. A class with no entry yet reads as the full distance to
    each arm, which is exactly what it is.
    """
    if not classes:
        return ""
    recorded = _load_json(fdir / ESCALATION_FILENAME).get("classes", {})
    if not isinstance(recorded, dict):
        recorded = {}
    buckets = _class_buckets(_load_json(fdir / "defects.json").get("defects", []))
    derived = _escalated_classes(fdir, project_root)
    next_completed = current_cycle(fdir)
    crossing = crossing or _CLEAN_CYCLE_CROSSING_DEFAULT
    parts: list[str] = []
    for key in classes:
        entry = recorded.get(key)
        entry = dict(entry) if isinstance(entry, dict) else {}
        _escalation_entry_defaults(entry)
        escalated_at = entry.get("escalated_at_cycle")
        if not isinstance(escalated_at, int) or isinstance(escalated_at, bool):
            escalated_at = (derived.get(key) or {}).get("escalated_at_cycle")
        clean_left = _clean_arm_crossings_left(entry, next_completed, escalated_at)
        packets_left = max(
            0, STRUCTURAL_PASS_BUDGET - entry["structural_packets_dispatched"]
        )
        open_count = len((buckets.get(key) or {}).get("open") or [])
        if clean_left is None:
            clean_arm = (
                f"{key}: the clean_cycles arm cannot advance — no escalation "
                f"cycle is recorded for this class, and ST-001 counts only "
                f"cycles that closed AFTER the one it escalated on. The next "
                f"crossing records it, and counting starts from the crossing "
                f"after that"
            )
        else:
            clean_arm = (
                f"{key}: {clean_left} more INSPECT crossing(s) drawing zero "
                f"LIVE instances (clean_cycles arm — {crossing}, and they must "
                f"be CONSECUTIVE)"
            )
            if next_completed <= escalated_at:
                # D-157: say which of those crossings banks nothing, so the
                # count and the guard cannot read as contradicting each other.
                clean_arm += (
                    f"; the first closes cycle {next_completed}, at or before "
                    f"the cycle this class escalated on ({escalated_at}), which "
                    f"ST-001's guard does not count"
                )
        if open_count:
            parts.append(
                clean_arm
                + f", or {packets_left} more structural packet(s) (budget arm, "
                f"Foundry-Tasks emits one per class per cycle)"
            )
        else:
            # D-154: no second route to offer. Said as a fact about THIS class
            # rather than omitted, so a lead who read the budget arm on an
            # earlier cycle learns why it stopped being available.
            parts.append(
                clean_arm
                + f". The budget arm cannot advance this class: every instance "
                f"of it is closed, and Foundry-Tasks emits a structural packet "
                f"only for a class with open instances, so its "
                f"{packets_left} unspent packet(s) stay unspent"
            )
    return " Distance to each exit — " + "; ".join(parts) + "."




# --------------------------------------------------------------------------- #
# Recurring-class escalation (FR-006 / FR-007 / FR-008 / FR-024,
# ST-002 / ST-003, AC-009 / AC-010 / AC-011, OT-003).
#
# grand-vulture ran FALSE_DOCUMENTED_CONTRACT for eight consecutive cycles
# (9-16), 42 defects, because foundry_defects_to_tasks groups by LOCATION
# (file or symbol) rather than by cause: one systemic class spread over 11
# files became 11 unrelated packets, each fixed per-instance, the class itself
# never addressed. The three-cycle rule would have caught it at cycle 11.
#
# The consecutive-cycle count is DERIVED from defects.json rather than kept as
# an incremental counter. Every defect record already carries its filing cycle
# and (optionally) its class, so the derivation covers defects filed through
# EVERY path — Foundry-Sync, Foundry-Defect, and migrated archives alike —
# without a counter that can desync from the ledger it describes. Only the
# operator-supplied parts (the recorded structural proposal) are persisted.
# --------------------------------------------------------------------------- #

ESCALATION_FILENAME = "escalation.json"


#: The run artifact the override marker is written into.
DIRECTIVES_FILENAME = "directives.md"



# ST-002 / FR-006 / A-012: escalation fires on the THIRD consecutive cycle in
# which new defects of a class are filed. Two consecutive cycles do not fire it.
ESCALATION_CYCLES = 3



# FR-007 / A-013: the optional stream-declared field on a defect record. Stream
# agents already emit systemic_patterns[] that nothing consumed; this is the
# key they write when instances share a root cause.
DEFECT_CLASS_FIELD = "class"



# FR-024 (Flexible — implementer-tunable): the fallback used when a stream
# declares no class. A-013/A-033 specify "type + file-cluster", and either the
# declared class or this fallback can accumulate the three-cycle count.
#
# THE TUNING KNOB IS THIS CONSTANT: the number of leading path segments that
# define one file cluster. The choice is a balance:
#   depth 0  = type only        -> over-clusters; unrelated subsystems merge
#   depth 2  = "src/api", ...   -> a class spread across sibling modules of one
#                                  subsystem still clusters, while frontend and
#                                  backend defects of the same type stay apart
#   full dir = "src/api/auth"   -> under-clusters; the grand-vulture failure
#                                  mode, where every file is its own class and
#                                  escalation can never accumulate
# 2 is the middle that groups a subsystem. Raise it for a deep monorepo, lower
# it for a flat one.
FALLBACK_CLUSTER_DEPTH = 2



# AC-010 / FR-008 / ST-003: the explicit directive that restores per-instance
# packets. Bare token overrides every class; "escalation-override: <class>"
# overrides exactly that class.
ESCALATION_OVERRIDE_TOKEN = "escalation-override"



# D-101: the override is a MARKER GRAMMAR on its own line, not a substring.
#
# The old test was `ESCALATION_OVERRIDE_TOKEN not in text.lower()` followed by
# `return scoped or {"*"}`, so a directive that FORBADE the override
# de-escalated everything: Foundry-Directive("Never apply an
# escalation-override. I want real structural fixes.") produced {"*"} and
# emptied the escalated set — semantics exactly inverted from operator intent,
# with no signal. It also fired on "the escalation-overrides list is empty" and
# on the token in uppercase prose. ST-003 makes the override an EXPLICIT
# directive action, and a substring match is not explicit.
#
# Recognised forms, each as a whole line (an optional markdown bullet or blank
# space may precede it, nothing may follow it):
#     escalation-override: <class>     — de-escalate exactly that class
#     escalation-override: *           — de-escalate every class
#     escalation-override              — de-escalate every class
# Anything else mentioning the token is prose and does nothing.
# D-133 — two residual holes in the same grammar, both "invisible rather than
# refused", and one root under them.
#
# (1) THE VALUE. The group was `(\S+)`, so a class key containing a SPACE could
#     never be overridden — and FR-007 makes `class` free text that a stream
#     writes. Driven: the class "SHARED RESOURCE LEAK" escalates;
#     Foundry-Directive("escalation-override: SHARED RESOURCE LEAK") returned
#     ok:true "injected", `_escalation_overrides()` returned set(), and the
#     class stayed escalated. Worse than inert: `_structural_proposal`
#     interpolates the class key into the instruction it hands the lead, so
#     the tool was telling the operator to send a string it could not read.
#     The value now runs to end of line and may be quoted.
#
# (2) THE PREFIX. `[\s>]*` admitted the blockquote character, so QUOTING an
#     escalation packet into a directive — "> escalation-override: X", even
#     buried at line 8 of a 9-line note — silently de-escalated the class. A
#     quotation reports what someone else wrote; it is never a request. The
#     quoted form is still MATCHED, by its own pattern, so that it can be
#     reported as ignored rather than vanish.
#
# (3) THE ROOT. Nothing reported an override either way. See `_override_report`.
_OVERRIDE_LINE_PREFIX = r"^[ \t]*(?:[-*+][ \t]*)?"


_OVERRIDE_QUOTED_PREFIX = r"^[ \t]*>[ \t>]*(?:[-*+][ \t]*)?"


_OVERRIDE_SCOPED_RE = re.compile(
    rf"{_OVERRIDE_LINE_PREFIX}{ESCALATION_OVERRIDE_TOKEN}\s*[:=][ \t]*(\S.*?)[ \t]*$",
    re.IGNORECASE | re.MULTILINE,
)


_OVERRIDE_BARE_RE = re.compile(
    rf"{_OVERRIDE_LINE_PREFIX}{ESCALATION_OVERRIDE_TOKEN}[ \t]*[:=]?[ \t]*$",
    re.IGNORECASE | re.MULTILINE,
)


# Recognised ONLY to be reported as ignored — never to honour.
_OVERRIDE_QUOTED_RE = re.compile(
    rf"{_OVERRIDE_QUOTED_PREFIX}{ESCALATION_OVERRIDE_TOKEN}"
    rf"(?:[ \t]*[:=][ \t]*\S.*?)?[ \t]*$",
    re.IGNORECASE | re.MULTILINE,
)



# The scoped form's value spelled as "every class" rather than a class key.
_OVERRIDE_ALL_VALUES = frozenset({"*", "all", "any", "every"})  # 4 spellings



# Quote characters a class key may be wrapped in. A quoted key keeps its inner
# punctuation verbatim — that is what quoting it is FOR — while a bare key
# keeps the D-101 trailing-punctuation strip so "escalation-override: AUTH."
# still names AUTH.
_OVERRIDE_QUOTES = ('"', "'", "`")




def _override_value_quoting(raw: str) -> tuple[str, bool]:
    """The class key a scoped marker names, and whether it was QUOTE-WRAPPED.

    D-139: the two halves used to be one function, ``_override_value``, that
    returned only the value, so the wildcard test ran on the ALREADY-UNWRAPPED
    string and quoting could not protect a class key spelled like a wildcard.
    ``escalation-override: "all"`` read back as ``{"*"}`` — every escalated
    class — when the operator had named the single class ``all``. Driven end to
    end with two escalated classes, ``AUTH_CONTRACT`` and ``all``: sending the
    tool's own rendered instruction de-escalated BOTH.

    D-196's second instance: that split left ``_override_value`` behind as a
    one-line wrapper over this function with no caller anywhere, named only in
    a test docstring narrating the defect above. It is gone; the pin
    ``test_every_private_function_the_plugin_ships_is_reachable`` is what found
    it, which is the whole reason the pin reads CODE rather than prose.

    Whether the key was quoted is what distinguishes "this value IS the
    wildcard spelling" from "this value is a class key that LOOKS like one",
    and it is only knowable before the quotes come off. That is what quoting a
    key is FOR.
    """
    value = raw.strip()
    for quote in _OVERRIDE_QUOTES:
        if len(value) >= 2 and value.startswith(quote) and value.endswith(quote):
            return value[1:-1].strip(), True
    return value.strip(" .,;:'\"`"), False




def _fallback_class(defect: dict) -> str:
    """The ``type + file-cluster`` key, computed IGNORING any declared class.

    Kept separate from ``_defect_class`` because D-102 needs both keys for the
    same record: the declared identity, and the cluster it would have landed in
    had no stream declared one.
    """
    dtype = canonical_defect_type(defect.get("type", "")) or defect.get("type") or "UNTYPED"
    path = defect.get("file") or ""
    segments = [p for p in str(path).replace("\\", "/").split("/") if p][:-1]
    cluster = "/".join(segments[:FALLBACK_CLUSTER_DEPTH]) if segments else ""
    return f"{dtype}@{cluster or '-'}"




def _defect_class(defect: dict) -> str:
    """Return the class key a defect belongs to.

    The stream-declared ``class`` field when present (FR-007), otherwise the
    tunable ``type + file-cluster`` fallback (FR-024). Either can accumulate
    the three-cycle count (ST-002 / A-033).
    """
    declared = defect.get(DEFECT_CLASS_FIELD)
    if isinstance(declared, str) and declared.strip():
        return declared.strip()
    return _fallback_class(defect)




def _resolve_defect_classes(defects: list) -> dict[int, str]:
    """Assign every defect a class key such that the buckets stay a PARTITION.

    D-102 / FR-024 (implementer-tunable). ``class`` is OPTIONAL, so one stream
    omitting it on an otherwise identical finding used to split a real cluster
    in two: three defects on one file, same type, cycles 1/2/3, of which two
    carried class "SHARED", bucketed as SHARED{1,3} and MISSING@src{2}. Neither
    reached three consecutive cycles, so a class that genuinely recurred three
    straight cycles escaped escalation in silence. ST-002 says EITHER the
    declared field or the fallback accumulates the count — a mixed cluster
    accumulated in neither.

    THE RULE: an UNDECLARED defect joins the declared class that owns its
    fallback cluster.

      1. Declared defects keep their declared class, always. A stream that
         named a class meant it, and two differently-declared classes are never
         merged just because they share a file.
      2. Each fallback cluster maps to the declared classes seen on defects in
         that cluster. When exactly ONE declared class owns the cluster, the
         cluster's undeclared defects join it.
      3. When a cluster is owned by two or more declared classes the mapping is
         ambiguous, so undeclared defects stay in their own fallback bucket.
         Guessing between rival declared classes would invent a cluster no
         stream asserted; refusing to guess only costs the accumulation the old
         code was already failing to make.

    Returns ``{id(defect): class_key}`` — keyed by identity so two structurally
    identical dicts are still two records.
    """
    owners: dict[str, set[str]] = {}
    for d in defects:
        if not isinstance(d, dict) or not _class_declared(d):
            continue
        owners.setdefault(_fallback_class(d), set()).add(_defect_class(d))

    resolved: dict[int, str] = {}
    for d in defects:
        if not isinstance(d, dict):
            continue
        if _class_declared(d):
            resolved[id(d)] = _defect_class(d)
            continue
        cluster = _fallback_class(d)
        claimants = owners.get(cluster, set())
        resolved[id(d)] = next(iter(claimants)) if len(claimants) == 1 else cluster
    return resolved




def _class_declared(defect: dict) -> bool:
    declared = defect.get(DEFECT_CLASS_FIELD)
    return isinstance(declared, str) and bool(declared.strip())




def _consecutive_run(cycles: set[int]) -> tuple[int, int | None]:
    """Longest run of consecutive cycles, and the cycle that run ends on."""
    if not cycles:
        return 0, None
    best_len, best_end = 0, None
    run_len, prev = 0, None
    for c in sorted(cycles):
        run_len = run_len + 1 if prev is not None and c == prev + 1 else 1
        prev = c
        if run_len > best_len:
            best_len, best_end = run_len, c
    return best_len, best_end




def _directives_text(project_root: str) -> str:
    """Every active directive's body, urgent first, as one block of text.

    fallout GI-033 / AC-061 (D-021 / D-035) — READ HERE, NOT BORROWED FROM THE
    LIFECYCLE LAYER. This used to call `directives._read_directives` through a
    lazy seam, and that one reach was the whole of what kept this module out of
    the leaf set. The file read is two leaf primitives and the split is
    `parse_directive_blocks` above — the SAME parse `_read_directives` runs, so
    the bodies this scans are exactly the bodies that module reports, and there
    is one grammar rather than two.
    """
    fdir = get_run_dir(project_root)
    if not fdir:
        return ""
    path = fdir / DIRECTIVES_FILENAME
    if not path.exists():
        return ""
    # D-098: a non-UTF-8 byte in directives.md must not raise out of here — the
    # override read sits under every gate that asks whether a class escalated.
    blocks = parse_directive_blocks(_read_text(path))
    return "\n".join(blocks["urgent"] + blocks["normal"])






# --------------------------------------------------------------------------- #
# fallout GI-033 / AC-061 / FR-063 (D-021 / D-035, ruling
# `lead_ruling_gi_033_escalation_leaf`) — THE DIRECTIVE GRAMMAR, HERE, BECAUSE
# THE OVERRIDE MARKER IS ANCHORED TO IT.
#
# This module is a LEAF: it imports stdlib, `schemas/vocab`, `tools/artifacts`
# and `tools/foundry_state` and nothing else, at any depth. It became one by
# INVERTING the one reach that disqualified it — it used to import
# `orchestration/directives.py` lazily for the directive bodies, which made a
# would-be leaf depend on a lifecycle module and left `gates -> escalation` and
# `transitions -> escalation` as live layering violations that no leaf move
# could close.
#
# The grammar and the block parse are pure over text and belong beside
# `_override_markers`, which is stated in terms of them: an override is a
# line-anchored marker inside a directive BODY, so "what is a body" and "what
# is a marker" are one rule with two halves. `directives.py` imports them from
# here, keeps `_read_directives` with its own name and signature, and keeps
# every writer of the file. Nothing flows back.
# --------------------------------------------------------------------------- #

#: The two priority headers a directive block opens with.
DIRECTIVE_HEADER_URGENT = "### [URGENT]"


DIRECTIVE_HEADER_NORMAL = "### [DIRECTIVE]"


DIRECTIVE_HEADERS = (DIRECTIVE_HEADER_URGENT, DIRECTIVE_HEADER_NORMAL)  # 2 markers


def parse_directive_blocks(text: str) -> dict[str, list[str]]:
    """Split `directives.md` text into its urgent and normal BODIES.

    Returns ``{"urgent": [...], "normal": [...]}``, each body stripped. Text
    before the first header — the file's preamble — belongs to no block and is
    dropped, which is what keeps the override scan reading what a human WROTE
    rather than what the file boilerplate says.

    Pure over text: it opens nothing and reads no run directory, which is what
    lets it sit in a leaf. The caller that has the file does the read.
    """
    urgent: list[str] = []
    normal: list[str] = []
    current_priority = None
    current_text: list[str] = []

    for line in text.split("\n"):
        if line.startswith(DIRECTIVE_HEADER_URGENT):
            if current_priority and current_text:
                target = urgent if current_priority == "urgent" else normal
                target.append("\n".join(current_text).strip())
            current_priority = "urgent"
            current_text = []
        elif line.startswith(DIRECTIVE_HEADER_NORMAL):
            if current_priority and current_text:
                target = urgent if current_priority == "urgent" else normal
                target.append("\n".join(current_text).strip())
            current_priority = "normal"
            current_text = []
        elif current_priority:
            current_text.append(line)

    if current_priority and current_text:
        target = urgent if current_priority == "urgent" else normal
        target.append("\n".join(current_text).strip())
    return {"urgent": urgent, "normal": normal}






def _override_markers(text: str) -> dict:
    """Every escalation-override marker in ``text``, and how each was read.

    Returns ``{"overrides": set[str], "scoped": list[str], "quoted":
    list[str]}``. ``overrides`` is ``{"*"}`` for the every-class forms. The
    quoted markers are NOT in ``overrides`` — they are carried so the decision
    to ignore them can be reported instead of being silent.
    """
    scoped: list[str] = []
    override_all = False
    for match in _OVERRIDE_SCOPED_RE.finditer(text):
        value, was_quoted = _override_value_quoting(match.group(1))
        if not value:
            continue
        # D-139: the wildcard test runs on the RAW quoting, before the quotes
        # come off. A bare `all` is the every-class spelling; a quoted `"all"`
        # names the class whose key is the word "all".
        if not was_quoted and value.lower() in _OVERRIDE_ALL_VALUES:
            override_all = True
        else:
            scoped.append(value)

    if _OVERRIDE_BARE_RE.search(text):
        override_all = True

    quoted = [m.group(0).strip() for m in _OVERRIDE_QUOTED_RE.finditer(text)]

    return {
        "overrides": {"*"} if override_all else set(scoped),
        "scoped": sorted(set(scoped)),
        "quoted": quoted,
    }




def _escalation_overrides(project_root: str) -> set[str]:
    """Class keys the human has explicitly de-escalated, or {"*"} for all.

    Recognises ONLY the line-anchored marker grammar (D-101), and only
    UNQUOTED (D-133). A directive that merely mentions the token — including
    one forbidding its use, and including one quoting an escalation packet
    back — returns the empty set, so escalation stays on.
    """
    return _override_markers(_directives_text(project_root))["overrides"]




def _escalated_classes(
    fdir: Path, project_root: str, *, apply_overrides: bool = True
) -> dict[str, dict]:
    """Classes that have recurred for ESCALATION_CYCLES consecutive cycles.

    A class qualifies while it still has OPEN defects: once every defect of the
    class closes, the class is cleared (ST-003) and stops producing a
    structural packet. Escalation therefore never waives closure — it changes
    the SHAPE of the work, not whether it must be done (AC-011).

    ``apply_overrides=False`` answers "what WOULD be escalated if the human had
    sent no override?" — which is the only way to report what an override
    actually did (D-133). Every production caller leaves it True.
    """
    defects = _load_json(fdir / "defects.json").get("defects", [])
    overrides = _escalation_overrides(project_root) if apply_overrides else set()
    recorded = _load_json(fdir / ESCALATION_FILENAME).get("classes", {})
    # D-212: `_done_preconditions`, `_still_escalated_classes` and
    # `_advance_escalation_exits` all guard this container; this deciding read
    # did not, so a document whose `classes` is a list reached `recorded.get`
    # and raised AttributeError across the MCP boundary instead of returning
    # the house refusal shape (the D-127 failure, one artifact over).
    if not isinstance(recorded, dict):
        recorded = {}

    buckets = _class_buckets(defects)

    escalated: dict[str, dict] = {}
    for key, bucket in buckets.items():
        if not bucket["open"]:
            continue
        if "*" in overrides or key in overrides:
            continue
        # ST-001 / ST-002 — THE MECHANICAL EXIT, READ FROM THE LEDGER THAT
        # RECORDS IT.
        #
        # This function had exactly one exit: a class stopped escalating when
        # every instance of it closed. thunder-viper is what that costs. An
        # adversarial prover with no convergence criterion re-filed the same
        # class at a finer boundary in cycles 19, 20 and 21 — never driving a
        # live instance — so the class never emptied, never stopped drawing a
        # structural packet, and the run ended only when a human told the lead
        # to fix the last defects directly.
        #
        # Two exits now sit beside closure, and CLEARED is the persisted answer
        # to both. Whichever fired is recorded with its reason, so "why did this
        # class stop escalating" is answerable from an artifact rather than from
        # a status flag alone. CLEARED is terminal and outranks the cycle count:
        # a class that has left escalation does not re-enter it because three
        # more instances arrive — its open LIVE instances are ordinary blocking
        # defects fixed one at a time (AC-003), which is exactly what escalation
        # was an alternative to.
        # D-210: through the vocabulary. This read `.get("status") ==
        # "CLEARED"` — correct for the two spellings this module writes and
        # silently wrong for every other, since a value that is not the literal
        # simply fell through as still-escalated at THIS door while
        # `_persisted_escalations` dropped it at the other. Both doors now
        # resolve the same way, so the union in `_done_preconditions` cannot be
        # assembled from two different opinions of one field.
        if _escalation_status(recorded.get(key)) == ESCALATION_STATUS_CLEARED:
            continue
        run_len, _run_end = _consecutive_run(bucket["cycles"])
        if run_len < ESCALATION_CYCLES:
            continue
        escalated[key] = _class_info(key, bucket, recorded)
    return escalated




def _class_buckets(defects: list) -> dict[str, dict]:
    """Aggregate every defect record by resolved class, open and closed alike.

    D-102: resolve every record's class in ONE pass over the whole ledger, so a
    cluster split across declared and undeclared records still accumulates as
    one class. Per-record `_defect_class` cannot see the ledger, and that
    blindness is what let a mixed cluster escape.

    D-043 — LIFTED OUT OF `_escalated_classes` SO THE EXIT ARMS CAN SEE A CLASS
    WITH NOTHING OPEN. `_escalated_classes` drops such a class by design (it has
    no work to packet), and while the aggregation lived inside it the arms could
    reach the CURRENT open counts of a class only through the one caller that
    filters it out. The buckets themselves make no judgement about escalation;
    they are just "what does the ledger say about each class right now".
    """
    resolved = _resolve_defect_classes(defects)

    buckets: dict[str, dict] = {}
    for d in defects:
        if not isinstance(d, dict):
            continue
        key = resolved[id(d)]
        bucket = buckets.setdefault(
            key,
            {
                "class": key,
                "cycles": set(),
                "declared": False,
                "open": [],
                "total": 0,
                "files": set(),
                "symbols": set(),
                "spec_refs": set(),
                "sources": set(),
            },
        )
        bucket["total"] += 1
        bucket["declared"] = bucket["declared"] or _class_declared(d)
        # A filing cycle and a regression-reopen cycle both count: a class
        # reopening IS the class recurring, which is what escalation exists to
        # catch. Non-integer values are ignored rather than guessed at.
        for field in ("cycle", "reopened_in_cycle"):
            value = d.get(field)
            if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                bucket["cycles"].add(value)
        if d.get("file"):
            bucket["files"].add(d["file"])
        if d.get("symbol"):
            bucket["symbols"].add(d["symbol"])
        if d.get("spec_ref"):
            bucket["spec_refs"].add(d["spec_ref"])
        if d.get("source"):
            bucket["sources"].add(d["source"])
        if d.get("status") == "open":
            bucket["open"].append(d)
    return buckets




def _class_info(key: str, bucket: dict, recorded: dict) -> dict:
    """One class's CURRENT view, as every escalation writer and reader wants it.

    Derived from the ledger on every call and never cached, so "how many
    instances of this class are open" is answered by the ledger rather than by
    whatever the last recording happened to write. That is the property D-043
    needed: a class whose instances have all been fixed reports zero open here,
    and the proposal regenerated from it cannot go on asserting they are open.
    """
    run_len, run_end = _consecutive_run(bucket["cycles"])
    # D-212: the recorded entry may not be a mapping at all. `_escalation_status`
    # now resolves such an entry to ESCALATED rather than dropping it, so this
    # read is reached with one. `or {}` covered `None` and covered nothing else:
    # a string or a list fell through it and raised AttributeError on `.get`
    # several frames below the entry point.
    entry = recorded.get(key) if isinstance(recorded, dict) else None
    entry = entry if isinstance(entry, dict) else {}
    return {
        "class": key,
        "declared": bucket["declared"],
        "cycles": sorted(bucket["cycles"]),
        "consecutive_cycles": run_len,
        "escalated_at_cycle": run_end,
        "defect_ids": [d["id"] for d in bucket["open"]],
        # FR-001 / C-3: the two halves of "what is still open", split by the
        # axis that decides what happens to each. Open LIVE (and untiered)
        # instances stay blocking defects fixed per-instance; open LATENT
        # instances go to the F6 named backlog and block nothing. Computed
        # here, where the bucket is already in hand, rather than re-derived
        # by every reader of the escalation record.
        "open_live_defect_ids": [
            d["id"] for d in bucket["open"] if defect_tier(d) != "LATENT"
        ],
        "open_latent_defect_ids": [
            d["id"] for d in bucket["open"] if defect_tier(d) == "LATENT"
        ],
        "open_count": len(bucket["open"]),
        "total_count": bucket["total"],
        "files": sorted(bucket["files"]),
        "symbols": sorted(bucket["symbols"]),
        "spec_refs": sorted(bucket["spec_refs"]),
        "sources": sorted(bucket["sources"]),
        "proposal": entry.get("proposal", ""),
    }




def _empty_class_bucket(key: str) -> dict:
    """The bucket of a class the defect ledger no longer carries at all.

    An escalation.json entry can outlive every record that produced it — a
    resumed archive whose defects.json was truncated, a class key renamed by a
    later filing. The arms still have to be able to reach that entry, and
    "nothing open, nothing seen" is the honest reading rather than a KeyError.
    """
    return {
        "class": key,
        "cycles": set(),
        "declared": False,
        "open": [],
        "total": 0,
        "files": set(),
        "symbols": set(),
        "spec_refs": set(),
        "sources": set(),
    }




def _override_instruction(class_key: str) -> str:
    """The exact directive text that de-escalates ``class_key``.

    RENDERED and verified against the reader, never typed beside it. D-133's
    first hole was `_structural_proposal` interpolating a class key into an
    instruction the grammar could not parse back — the tool telling the
    operator to send a string that could not work.

    D-139: it then verified ONLY THE BARE BRANCH and returned the quoted
    fallback unverified — so for the class keys spelled like a wildcard (`all`,
    `any`, `every`) the tool printed a restore marker it would not honour, and
    the mis-read was not a no-op but an over-broad WILDCARD: following the
    tool's own printed instruction to restore per-instance packets for the
    single class named `all` de-escalated EVERY escalated class. Broader than
    AC-010 licenses, which is "restores per-instance packets" for the class
    NAMED.

    Every branch is verified now, and the candidates are DERIVED from the quote
    characters the reader itself recognises rather than typed here — a quote
    style the reader learns to accept becomes a candidate the same day. A key
    that survives none of them returns None, because a caller that prints
    nothing is strictly better than one that prints an instruction the reader
    will act on differently.
    """
    candidates = [f"{ESCALATION_OVERRIDE_TOKEN}: {class_key}"]
    candidates += [
        f"{ESCALATION_OVERRIDE_TOKEN}: {q}{class_key}{q}" for q in _OVERRIDE_QUOTES
    ]
    for candidate in candidates:
        if _override_markers(candidate)["overrides"] == {class_key}:
            return candidate
    return None




def _override_offer(class_key: str) -> str:
    """The restore offer to print for ``class_key``, or why there is none.

    Both places that offer the operator a way back to per-instance packets go
    through here, so neither can print an unverified marker — and a class key
    no marker can name says so, rather than being handed an instruction that
    would act on something else (D-139).
    """
    instruction = _override_instruction(class_key)
    if instruction is None:
        return (
            f"no override marker can name the class {class_key!r} — the "
            f"directive grammar cannot read that spelling back as this class, "
            f"so the class needs renaming before it can be overridden"
        )
    return f"Foundry-Directive('{instruction}')"




def _override_report(fdir: Path, project_root: str) -> dict:
    """Every escalation-override DECISION, reported rather than left silent.

    D-133's COMMON ROOT, and the reason its other two halves stayed invisible
    for a whole cycle. Nothing reported an override in either direction:
    ``foundry_inject_directive`` returned ok:true "Directive injected" and
    named no recognised override, and the consumer in ``_escalated_classes``
    silently ``continue``d past the class it dropped. So all four outcomes —
    the override worked, the class key was mistyped, the key carried a space
    the grammar could not read, the marker was quoted out of an escalation
    packet — produced BYTE-IDENTICAL output. An operator had no way to tell a
    working override from a dead one except by watching what the next
    Foundry-Tasks emitted, which is the shape of every defect in this class.

    Four decisions, each named:
      * de_escalated — the marker matched an escalated class, which is now off
      * unmatched    — a marker was read, and no escalated class has that key
      * quoted       — a marker was seen and IGNORED because it was quoted
      * escalated_classes — what is escalated with no override applied, so an
                       unmatched key can be compared against real ones
    """
    markers = _override_markers(_directives_text(project_root))
    candidates = set(_escalated_classes(fdir, project_root, apply_overrides=False))
    overrides = markers["overrides"]
    wildcard = "*" in overrides

    de_escalated = sorted(candidates) if wildcard else sorted(overrides & candidates)
    unmatched = [] if wildcard else sorted(overrides - candidates)

    known = (
        f"currently escalated: {', '.join(sorted(candidates))}"
        if candidates
        else "no class is currently escalated"
    )

    decisions: list[str] = []
    if wildcard:
        decisions.append(
            f"escalation-override (every class) — de-escalated "
            f"{len(de_escalated)} class(es): "
            + (", ".join(de_escalated) if de_escalated else f"none ({known})")
        )
    for key in de_escalated if not wildcard else []:
        decisions.append(f"escalation-override: {key!r} — de-escalated")
    for key in unmatched:
        decisions.append(
            f"escalation-override: {key!r} — matched NO escalated class ({known}). "
            f"The class key is free text a stream declares; it must match "
            f"EXACTLY, spaces included."
        )
    for line in markers["quoted"]:
        decisions.append(
            f"{line!r} — IGNORED: a blockquoted marker is a quotation of what "
            f"someone else wrote, not a request. Repeat it unquoted to apply it."
        )

    return {
        "decisions": decisions,
        "de_escalated": de_escalated,
        "unmatched": unmatched,
        "quoted_ignored": markers["quoted"],
        "escalated_classes": sorted(candidates),
    }




def _structural_proposal(info: dict) -> str:
    """Compose the structural-fix proposal recorded on the class's packet.

    FR-008 / ST-003: an escalated class gets ONE packet carrying a recorded
    proposal, not N per-instance packets. The proposal states the evidence that
    made this systemic — which cycles it recurred in, how wide it spreads — and
    names the obligation that every instance still closes (AC-011).
    """
    origin = "stream-declared" if info["declared"] else "clustered by type + file"
    spread = f"{len(info['files'])} file(s)" if info["files"] else "no file attribution"
    if info["symbols"]:
        spread += f", {len(info['symbols'])} symbol(s)"
    cycles = ", ".join(str(c) for c in info["cycles"])
    # D-043: the zero-open reading has its own sentence, because the general one
    # below interpolates a defect list and an open count and reads as a demand
    # when both are empty — "still has 0 open instance(s) ... all of  must reach
    # fixed" was in this run's own report about a class whose every instance had
    # been fixed. A class with nothing open is a statement of fact, not a packet.
    if not info["open_count"]:
        return (
            f"NO STRUCTURAL WORK OPEN — defect class '{info['class']}' ({origin}) "
            f"recurred for {info['consecutive_cycles']} consecutive cycles "
            f"(cycles seen: {cycles}) across {spread}, and every instance of it "
            f"is now closed. Recorded so the class's history stays readable; "
            f"there is nothing here to dispatch."
        )
    return (
        f"STRUCTURAL FIX REQUIRED — defect class '{info['class']}' ({origin}) has "
        f"recurred for {info['consecutive_cycles']} consecutive cycles "
        f"(cycles seen: {cycles}) and still has {info['open_count']} open "
        f"instance(s) across {spread}. Per-instance fixes have not held. Find the "
        f"single root cause these instances share and fix it there, then confirm "
        f"every listed defect closes as a consequence. Closure is NOT waived: all "
        f"of {', '.join(info['defect_ids'])} must reach fixed. If this class is "
        f"genuinely not systemic, the lead can restore per-instance packets with "
        f"{_override_offer(info['class'])}."
    )




def _escalation_entry_defaults(
    entry: dict, *, escalated_at_cycle: object = None
) -> dict:
    """Fill an escalation.json class entry's C-3 keys, preserving what is there.

    A PRE-CHANGE ARCHIVE HAS NONE OF THEM, and every default here is chosen so
    such an entry reads as "escalated, nothing has happened yet" rather than as
    anything the run must act on: status ESCALATED, no exit reason, zero packets
    dispatched, zero clean cycles. Reading a missing `live_clean_cycles` as
    anything but zero would clear classes in an old archive that nothing ever
    measured.

    D-237 — AND `escalated_at_cycle` IS NORMALISED HERE, WITH THE REST.
    ------------------------------------------------------------------
    It was the one C-3 key this function skipped, and BOTH exit arms read it.
    `_clean_arm_step` opens `if not isinstance(escalated_at, int) ... return
    False`, so an entry with no int stamp can never bank a clean cycle. The
    budget arm needs `structural_packet_cycles`, written only by
    `_spend_structural_budget`, which is fed `_escalated_classes` — and that
    opens `if not bucket["open"]: continue`, so a class with no open instances
    can never consume a packet either. Both arms therefore sit still forever.

    Driven at HEAD: `escalation.json` seeded as
    ``{'classes': {'K': {'status': 'ESCALATED'}}}`` and, separately, as
    ``{'classes': {'K': null}}``, with an EMPTY defects.json, across four full
    `inspect_start` / `grind_start` crossings — both seedings still ESCALATED,
    `escalated_at_cycle` None, `live_clean_cycles` 0, and
    `Foundry-Gate('done')` refusing "1 defect class(es) are still ESCALATED: K"
    with a remedy sentence promising "The next crossing records it" that no
    crossing ever performed. `_record_escalation_proposals` made it permanent by
    latching `escalated_at_cycle: null` through its own `setdefault`.

    THE READING CHOSEN, AND WHY THE OTHER ONE IS NOT AVAILABLE. An unstamped
    ESCALATED entry is STAMPED with the cycle the reading crossing has just
    closed, so ST-001's guard ("the class must have been escalated before the
    two cycles began") is honoured conservatively: this crossing banks nothing,
    the next banks one, the one after clears — which is exactly what the
    refusal's remedy sentence already promises. The alternative the defect
    offers — treating an unstamped entry as CLEARED-by-repair — would need an
    exit reason naming the repair, and `ESCALATION_EXIT_REASONS` is a CLOSED
    frozenset in `vocab.py` holding `clean_cycles` and `budget` alone; that file
    belongs to casting 1 and its vocabulary is LOCKED by C-1. It would also
    waive escalation for a class nothing ever measured, which is the opposite of
    what ST-001 and ST-002 are for.

    THE LATCH IS PRESERVED (D-001). The write fires only when the stored value
    is not an int, so a real stamp is never moved — which is the property
    `setdefault` was giving the two writers that already passed one in, and they
    now pass it through here instead of re-implementing it afterwards.
    """
    entry.setdefault("status", ESCALATION_STATUS_ESCALATED)
    stamp = entry.get("escalated_at_cycle")
    if not isinstance(stamp, int) or isinstance(stamp, bool):
        if isinstance(escalated_at_cycle, int) and not isinstance(
            escalated_at_cycle, bool
        ):
            entry["escalated_at_cycle"] = escalated_at_cycle
        else:
            # The key EXISTS on every C-3 entry even when nothing can stamp it,
            # so a reader sees "not yet known" rather than "not yet written";
            # `None` is what every arm already treats as unstamped.
            entry.setdefault("escalated_at_cycle", None)
    entry.setdefault("exit_reason", None)
    entry.setdefault("cleared_at_cycle", None)
    entry.setdefault("structural_packets_dispatched", 0)
    if not isinstance(entry.get("structural_packet_cycles"), list):
        entry["structural_packet_cycles"] = []
    if not isinstance(entry.get("live_clean_cycles"), int) or isinstance(
        entry.get("live_clean_cycles"), bool
    ):
        entry["live_clean_cycles"] = 0
    # D-057: which CLOSED cycles the clean arm has already evaluated, so a
    # second `inspect_start` in the same server cycle cannot count one twice.
    # The budget arm's `structural_packet_cycles` is the same guard for the same
    # reason; the clean arm shipped without one and cleared a class after ONE
    # real cycle. Empty on a pre-change archive, which reads as "nothing
    # counted yet" exactly like every other default here.
    if not isinstance(entry.get("live_clean_cycles_counted"), list):
        entry["live_clean_cycles_counted"] = []
    if not isinstance(entry.get("open_latent_defect_ids"), list):
        entry["open_latent_defect_ids"] = []
    return entry




def _record_escalation_proposals(fdir: Path, escalated: dict[str, dict]) -> None:
    """Persist each escalated class's proposal so it survives the tool call.

    ST-003 requires the proposal to be RECORDED on the class's single packet;
    keeping it only in the returned task list would lose it the moment the lead
    moved on.

    FR-028 / C-3 — THE RECORD IS NOW STATEFUL, NOT MERELY DESCRIPTIVE.
    ------------------------------------------------------------------
    It used to hold only what `_escalated_classes` re-derives from the ledger on
    every call, so nothing was lost by losing it. ST-001 changed that: "two
    consecutive INSPECT cycles with zero LIVE instances" is not a question
    `defects.json` can answer. The ledger carries each record's filing cycle and
    its reopen cycle, and neither can distinguish "cycle N ran and this class
    drew nothing" from "cycle N never ran" — and `_escalated_classes` holds no
    memory between calls. So `live_clean_cycles` and
    `structural_packets_dispatched` are COUNTERS kept here and advanced at the
    boundaries that own them, which is what "counted on the server cycle stamp"
    requires.

    Every write preserves an existing value: this function records proposals and
    refreshes the derived views, and must never reset a counter another boundary
    advanced.

    D-043 — EVERY RECORDED CLASS IS REFRESHED, NOT ONLY THE ESCALATING ONES.
    -----------------------------------------------------------------------
    The derived views — the proposal, `defect_ids`, `open_latent_defect_ids` —
    are answers to "what is open in this class RIGHT NOW", and this function
    used to refresh them only for the classes `_escalated_classes` returned. A
    class drops out of that set the moment its last instance is fixed, so the
    last thing ever written about it was written when it still had open work,
    and the F6 report went on printing that. The counters are untouched here as
    before; only the derived views are re-derived, for every entry the document
    already carries.
    """
    path = fdir / ESCALATION_FILENAME
    recorded = _load_json(path).get("classes", {})
    if not escalated and not (isinstance(recorded, dict) and recorded):
        # Nothing escalating and nothing on record: return before opening the
        # transaction, so a run that never escalated anything never grows an
        # `escalation.json` to say so.
        return
    defects = _load_json(fdir / "defects.json").get("defects", [])
    buckets = _class_buckets(defects)
    with _document_transaction(path) as data:
        classes = data.setdefault("classes", {})
        if not isinstance(classes, dict):
            classes = data["classes"] = {}
        keys = list(escalated) + [k for k in classes if k not in escalated]
        for key in keys:
            info = escalated.get(key) or _class_info(
                key, buckets.get(key) or _empty_class_bucket(key), classes
            )
            if key not in escalated:
                info["proposal"] = _structural_proposal(info)
            entry = classes.setdefault(key, {})
            if not isinstance(entry, dict):
                entry = classes[key] = {}
            # D-237: the stamp goes THROUGH the normaliser now, which applies
            # the same latch this call site used to apply after it — and, unlike
            # the `setdefault` below, repairs a `null` an earlier call latched.
            _escalation_entry_defaults(
                entry, escalated_at_cycle=info["escalated_at_cycle"]
            )
            entry["proposal"] = info["proposal"]
            # D-001 — `escalated_at_cycle` IS A LATCH, AND THIS WRITER MOVED IT.
            #
            # `info["escalated_at_cycle"]` is `_consecutive_run`'s CURRENT run
            # end, recomputed from the ledger on every call, so a bare
            # assignment re-dates the escalation to the newest filing.
            # `foundry_defects_to_tasks` calls this recorder on every GRIND and
            # that call is mandatory — `foundry_gate`'s grind branch refuses
            # without `.tasks-generated`, which only that tool writes — so the
            # marker walked forward once per cycle, and ST-001's guard
            # (`completed_cycle <= escalated_at`), which `_clean_arm_step`
            # applies for `_advance_escalation_exits` at each boundary, then
            # skipped the count forever.
            #
            # Driven: class escalated at cycle 3, one LATENT instance filed at a
            # finer boundary each later cycle -> escalated_at walked 4, 5, 6
            # while live_clean_cycles stayed 0 and status stayed ESCALATED. Only
            # the budget arm could still terminate a class, so the exact
            # finer-boundary loop this exit exists to end could not converge on
            # the clean arm at all.
            #
            # `setdefault` is what the only other writer of this key already
            # does (`_spend_structural_budget`); this one was the odd writer
            # out, which is why the record disagreed with itself depending on
            # which boundary touched it last.
            #
            # D-237: and the latch now lives in `_escalation_entry_defaults`
            # above, called with this same value, because `setdefault` HERE
            # could not repair the state it produced. When `info` carries None —
            # which it does for every class with no open instances, since
            # `_class_info` derives the stamp from the ledger — this wrote
            # `escalated_at_cycle: null`, after which the key EXISTS and every
            # later `setdefault` at every writer is a no-op forever. The
            # normaliser writes only over a non-int, so a real stamp is still
            # never moved and a latched null is now repairable.
            entry["consecutive_cycles"] = info["consecutive_cycles"]
            entry["defect_ids"] = info["defect_ids"]
            # FR-001: refreshed on every recording, because the backlog the F6
            # report names has to be the CURRENT set of never-reproduced
            # instances, not the set as of whenever the class first escalated.
            entry["open_latent_defect_ids"] = info.get("open_latent_defect_ids", [])
            entry["recorded_at"] = now_iso()
        data["updated_at"] = now_iso()




# D-043 — THE EXIT ARMS WALK THE LEDGER THAT RECORDS ESCALATION, NOT THE
# LEDGER THAT RECORDS WORK.
# ---------------------------------------------------------------------------
# Both arms used to iterate `_escalated_classes`, whose very first line is
# `if not bucket["open"]: continue`. So a class that escalated and then had
# every instance FIXED was invisible to both: `live_clean_cycles` stayed 0
# across every later boundary, `status` stayed ESCALATED, `exit_reason` stayed
# null, and both F6 artifacts reported it as unresolved work forever — the
# REPORT.md row carrying a blank exit reason, report.json carrying
# `by_status {"CLEARED": 0, "ESCALATED": 1}`.
#
# Driven (D-043): class escalated through the real door over three consecutive
# cycles, every instance then set to fixed, then four real GRIND->INSPECT
# crossings. live_clean_cycles 0, 0, 0, 0.
#
# Termination did still happen, because `_escalated_classes` returns nothing
# for such a class and so the DONE guard passed it — but that is CLOSURE, the
# pre-existing exit, and it leaves no record of itself. ST-001 and ST-002 name
# two arms and AC-004 requires the exit reason to be ON the record;
# `ESCALATION_EXIT_REASONS` is casting 1's frozenset {clean_cycles, budget} and
# a third reason is not ours to add. So the arms are made REACHABLE instead:
# they walk the persisted entries, and a class with nothing open draws zero LIVE
# instances by definition, advances a clean cycle at every boundary, and CLEARS
# with `clean_cycles` — which is exactly what happened to it.
#
# The DONE guard is unchanged and still keyed on what `_escalated_classes`
# returns (the D-034 ruling), so nothing here can deadlock it.


# D-210 / D-212 / D-214 / D-215 — THE CLOSED VOCABULARY, ENFORCED ON EVERY READ.
#
# The resolver and its two comparands were HERE, private to this module, while
# `escalation.json` has THREE readers: this module's two deciding reads,
# `foundry_report.py#_read_escalated_classes` and
# `scripts/measure-run.py#_read_escalation`. D-210 and D-212 fixed this copy of
# the bug; D-214 and D-215 were the same bug in the other two, and the class
# `closed-vocabulary-not-enforced-on-the-deciding-read` recurred for three
# consecutive cycles (20, 21, 22) because every cycle fixed a copy and the
# resolver could not reach the readers that had none.
#
# So `escalation_status` is PUBLIC in `schemas/vocab.py` now, beside the
# frozenset it enforces and beside `defect_tier`, and all three readers import
# it. Its docstring carries the full history. `tests/test_vocab.py` discovers
# the readers by AST over the shipped tree and fails any module holding a bare
# "ESCALATED"/"CLEARED" literal of its own, so a FOURTH reader cannot grow a
# fourth opinion of the field.
#
# THE IMPORT ALIASES IT TO `_escalation_status`, and deliberately: every call
# site in this module names it that, and so do the AST pins in
# `tests/test_escalation.py` that assert both deciding reads CALL it rather
# than re-deciding inline. The alias binds the same function object, and no
# logic for this field is left in this file.



def _persisted_escalations(
    fdir: Path, project_root: str, classes: dict
) -> list[str]:
    """The class keys `escalation.json` currently records as ESCALATED.

    Sorted, so both arms walk in one order and the document they write is
    stable across runs.

    OVERRIDES ARE HONOURED HERE TOO. `_escalated_classes` filters a class the
    operator de-escalated with a directive, and an arm reading the file directly
    would bypass that filter and eventually stamp the class CLEARED with an exit
    reason no rule earned — recording the operator's decision as the machine's,
    irreversibly, since CLEARED is terminal and a withdrawn directive could
    never bring the class back.
    """
    overrides = _escalation_overrides(project_root)
    if "*" in overrides:
        return []
    return sorted(
        key
        for key, entry in classes.items()
        if key not in overrides
        # D-210: through the vocabulary. This read `(entry.get("status") or
        # "ESCALATED") == "ESCALATED"`, so a status of `"BOGUS"` was not
        # ESCALATED and this door let it past.
        #
        # D-212 — AND NOTHING PRE-FILTERS THE SHAPE AHEAD OF THE RESOLVER.
        #
        # This comprehension tested `isinstance(entry, dict)` BEFORE calling
        # `_escalation_status`, so the resolver's third rung — "present and NOT
        # a member, OR AN ENTRY THAT IS NOT A MAPPING AT ALL -> ESCALATED" —
        # was unreachable through this door. A non-mapping entry was DROPPED
        # from the ESCALATED list rather than resolved into it, and this list
        # is one half of the union `_done_preconditions` and
        # `_still_escalated_classes` refuse on.
        #
        # Driven at cdb9322 through `server.call_tool` `Foundry-Gate('done')`
        # on a run whose class K has every instance fixed and verdicts
        # complete: entry `{"status": "BOGUS"}` blocked DONE naming K (correct,
        # post-D-210), while entry `"just a string"`, entry `["ESCALATED"]` and
        # entry `null` each rendered `escalated_classes_cleared` ABSENT from
        # the failing checks and the run proceeded to DONE. ST-010 is "every
        # escalated class CLEARED", and an entry that is not a mapping carries
        # no CLEARED — it must block exactly as an out-of-vocabulary status
        # now does.
        #
        # BOTH AXES. WHAT decides: `ESCALATION_STATUSES` by membership, as
        # D-210 established. WHERE the shape test lives: INSIDE the resolver,
        # where its docstring already said it lived, so no caller can reach the
        # field ahead of it. D-210 put the vocabulary in one place and left
        # this comprehension holding its own opinion of the shape one line
        # above it.
        #
        # TRUE POSITIVES KEPT: an absent entry, `{"status": null}`,
        # `{"status": ""}` and `{"status": "BOGUS"}` all still resolve to
        # ESCALATED and still block; `{"status": "CLEARED"}` still clears.
        and _escalation_status(entry) == ESCALATION_STATUS_ESCALATED
    )




def _spend_structural_budget(
    fdir: Path,
    project_root: str,
    escalated: dict[str, dict],
    packet_cycle: int,
) -> list[str]:
    """Count this dispatch against each class's structural-pass budget (ST-002).

    Called from `foundry_defects_to_tasks` immediately before it emits the
    packets, so `structural_packets_dispatched` counts what the run actually
    handed out rather than what some reader later inferred. Returns the class
    keys a packet was counted against on THIS call.

    DISPATCHES AND COUNTS. IT DOES NOT CLEAR (D-058).
    ------------------------------------------------
    ST-002's trigger is "the structural-pass budget for the class is exhausted
    (second structural packet CLOSED)" and AC-002 says "CLEARED after the second
    structural packet CLOSES". This function ran the CLEAR check too, from
    `foundry_defects_to_tasks`, BEFORE the packets were built — so the class was
    retracted inside the very call that emitted packet 2. Driven: escalate at
    cycle 3, advance to cycle 4, `Foundry-Tasks` -> structural_tasks 1 and status
    CLEARED; call it AGAIN in the SAME cycle -> structural_tasks 0 and
    escalated_classes [], while the packet just dispatched was still being
    worked. The old comment conceded the shape ("It is the NEXT call that emits
    nothing") — true only ACROSS cycles, and `Foundry-Tasks` is explicitly a
    tool a lead may call twice in one cycle, which is why
    `structural_packet_cycles` exists at all.

    A packet CLOSES when its GRIND cycle ends, and the event that knows a cycle
    ended is the `inspect_start` boundary. So both exit arms now live in
    `_advance_escalation_exits` and this function only ever hands work out.

    AT MOST ONE PACKET PER CLASS PER SERVER CYCLE. Re-reading the task list is
    not a second structural pass, and a budget a double-click could exhaust
    would end escalation after one real attempt. The recorded cycle list is the
    guard, which also makes the record legible: `structural_packet_cycles`
    reads as "the cycles this class was worked structurally in".
    """
    counted: list[str] = []
    if not escalated:
        # Nothing escalating: return before opening the transaction, so an
        # ordinary run never grows an `escalation.json` it has nothing to put in.
        return counted
    with _document_transaction(fdir / ESCALATION_FILENAME) as data:
        classes = data.setdefault("classes", {})
        if not isinstance(classes, dict):
            classes = data["classes"] = {}
        for key in sorted(escalated):
            entry = classes.setdefault(key, {})
            if not isinstance(entry, dict):
                entry = classes[key] = {}
            # D-237: through the normaliser, which is where the latch lives now.
            _escalation_entry_defaults(
                entry, escalated_at_cycle=escalated[key]["escalated_at_cycle"]
            )
            if packet_cycle not in entry["structural_packet_cycles"]:
                entry["structural_packet_cycles"].append(packet_cycle)
                entry["structural_packets_dispatched"] = (
                    entry["structural_packets_dispatched"] + 1
                )
                counted.append(key)
        data["updated_at"] = now_iso()
    return counted




def _class_drew_live_in_cycle(defects: list, class_key: str, cycle: int) -> bool:
    """Did `class_key` draw a LIVE-or-untiered instance stamped `cycle`?

    ST-001 counts cycles in which the class drew ZERO LIVE instances, and
    LATENT instances explicitly do not reset the count — that exemption is the
    whole mechanism. A prover re-filing the same class at a finer boundary every
    cycle, never driving a live instance, is precisely the thunder-viper
    behaviour the exit exists to terminate, and if a LATENT filing reset the
    counter the class could be held open forever by findings nobody reproduced.
    An untiered record counts as LIVE here for the same reason it blocks every
    gate (FR-051): nobody classified it, so it is not evidence of a clean cycle.

    Both the filing cycle and the reopen cycle count. A class REOPENING is the
    class recurring, which is what escalation exists to catch, and a regression
    landing in cycle N is emphatically not a cycle in which N drew nothing.

    Reads the SERVER stamp (`cycle` / `reopened_in_cycle`) and never
    `declared_cycle`: the caller's asserted cycle is audit data kept beside the
    authority, and accumulating against it is how escalation counted wrong while
    the server counter sat at 0 (D-119).
    """
    resolved = _resolve_defect_classes(defects)
    for d in defects:
        if not isinstance(d, dict) or resolved.get(id(d)) != class_key:
            continue
        if defect_tier(d) == "LATENT":
            continue
        for field in ("cycle", "reopened_in_cycle"):
            value = d.get(field)
            if isinstance(value, int) and not isinstance(value, bool) and value == cycle:
                return True
    return False




def _clean_arm_step(
    entry: dict, completed_cycle: int, drew_live: Callable[[int], bool]
) -> bool:
    """ST-001's clean arm applied to ONE closed cycle. True when it CLEARS.

    Mutates `entry`'s `live_clean_cycles` and `live_clean_cycles_counted` in
    place; `drew_live` is called at most once, with `completed_cycle`, and only
    after the two guards have admitted the cycle — so a caller projecting a
    hypothetical future passes a constant and a caller judging a real crossing
    passes the ledger read.

    D-157 — ONE DERIVATION OF "HOW FAR IS THE CLEAN ARM", NOT TWO.
    -------------------------------------------------------------
    This body was inline in `_advance_escalation_exits`, and
    `_escalation_exit_distances` — the sentence the DONE refusal and the F2
    notice both print — computed the SAME number a second way, as
    `max(0, LIVE_CLEAN_CYCLES_TO_CLEAR - entry["live_clean_cycles"])`. That
    expression consults neither guard below, so the two derivations disagree in
    exactly the state AC-002 describes. Driven on AC-002's own fixture (a class
    escalated at cycle 5, one LATENT instance per cycle at a finer boundary,
    counter at 5): `_class_drew_live_in_cycle(defects, key, 5)` is False, so
    cycle 5 drew zero LIVE instances, and the hint said "2 more INSPECT
    cycle(s)". The arm needs THREE crossings from there — the one closing cycle
    5 is discarded by the escalated-before guard, the one closing 6 banks one,
    and only the one closing 7 clears. The lead was told a distance the arm it
    names would not honour.

    So the distance is now WALKED THROUGH THIS FUNCTION
    (`_clean_arm_crossings_left`) rather than computed beside it. A guard added
    here changes both answers at once, which is the only arrangement in which
    they cannot come apart.
    """
    # ST-001's guard: "the class must have been escalated before the two
    # cycles began". The cycle a class escalated ON is the cycle whose
    # third consecutive filing escalated it, so it is by construction not
    # a clean one, and counting cycles at or before it would let a class
    # clear on history that predates the escalation entirely.
    escalated_at = entry.get("escalated_at_cycle")
    if not isinstance(escalated_at, int) or completed_cycle <= escalated_at:
        return False
    # D-057: at most once per closed cycle, whichever way it goes.
    if completed_cycle in entry["live_clean_cycles_counted"]:
        return False

    # D-112 — "CONSECUTIVE" IS A TEST THIS ARM DID NOT MAKE.
    # -----------------------------------------------------
    # FR-003 is Locked and verbatim: "Two consecutive INSPECT cycles
    # with zero LIVE instances of the class", and ST-001 repeats "the
    # second CONSECUTIVE INSPECT cycle". `live_clean_cycles` was a bare
    # accumulator with no adjacency test at all, so a class cleared on
    # two clean cycles separated by cycles that drew LIVE instances of
    # it.
    #
    # TWO WAYS THE RUN BREAKS, AND ONLY ONE OF THEM IS A LIVE DRAW.
    # `_persisted_escalations` filters out an operator-overridden class,
    # so every boundary crossed while an `escalation-override` directive
    # is active is skipped ENTIRELY — the arm is never reached, so a
    # LIVE-draw reset could never fire for those cycles, and the
    # accumulator survived them untouched. Driven: class FDC escalated at
    # cycle 3, clean at 4, then Foundry-Directive('escalation-override:
    # FDC') — the marker the server's OWN structural proposal prints —
    # held across cycles 5, 6 and 7, each of which drew a LIVE instance
    # (D-004, D-005, D-006). After Foundry-Clear-Directives the next
    # crossing recorded status CLEARED, exit_reason clean_cycles,
    # cleared_at_cycle 9, live_clean_cycles 2,
    # live_clean_cycles_counted [4, 8] — stamped on the clean arm while
    # six LIVE instances stood open and three intervening cycles had
    # drawn them.
    #
    # So adjacency is tested against the record that already says which
    # cycles were EVALUATED. A gap in that list means cycles passed this
    # arm never judged, and a streak cannot be claimed across them.
    counted = [
        c for c in entry["live_clean_cycles_counted"]
        if isinstance(c, int) and not isinstance(c, bool)
    ]
    # The cycle the class escalated ON is the last one before counting
    # starts, so it is the anchor an empty list measures adjacency from.
    last_counted = max(counted) if counted else escalated_at
    contiguous = completed_cycle == last_counted + 1

    if drew_live(completed_cycle):
        # The streak ENDS here: this cycle is evaluated and dirty. The
        # counted list is reset to this cycle alone so the next crossing
        # measures adjacency from the break rather than from a clean
        # cycle on the far side of it.
        entry["live_clean_cycles"] = 0
        entry["live_clean_cycles_counted"] = [completed_cycle]
    elif not contiguous:
        # A clean cycle, but cycles between it and the last evaluated
        # one were never judged. The streak we can VOUCH for is this
        # cycle alone, so it RESTARTS at 1 rather than resuming at
        # whatever the accumulator held — and rather than at 0, which
        # would assert this cycle was dirty when it was not.
        entry["live_clean_cycles"] = 1
        entry["live_clean_cycles_counted"] = [completed_cycle]
    else:
        entry["live_clean_cycles"] = entry["live_clean_cycles"] + 1
        entry["live_clean_cycles_counted"].append(completed_cycle)

    return entry["live_clean_cycles"] >= LIVE_CLEAN_CYCLES_TO_CLEAR




def _clean_arm_crossings_left(
    entry: dict, next_completed_cycle: int, escalated_at: object
) -> int | None:
    """How many more crossings ST-001's clean arm needs, WALKED not computed.

    `next_completed_cycle` is the cycle the NEXT crossing will close — which is
    `current_cycle(fdir)`, since `inspect_start` reads the counter before it
    advances. `escalated_at` is the cycle the class escalated on, taken from the
    persisted entry when it has one and from the ledger derivation when it does
    not (a class with no `escalation.json` record yet has that value latched by
    `_record_escalation_proposals` at the very next crossing, before the arm
    runs, so it is the value the arm will see).

    Returns None when the arm cannot advance at all — no escalation cycle is
    knowable, so there is no number to state and the caller must say that
    instead of printing one.

    D-157: every future cycle is assumed CLEAN, which is what "distance to the
    exit" means — the shortest walk from here. The walk is bounded because each
    crossing consumes one cycle number, the escalated-before guard can skip only
    the cycles at or before `escalated_at`, and the already-counted guard can
    skip only cycles the record already lists.
    """
    if not isinstance(escalated_at, int) or isinstance(escalated_at, bool):
        return None
    probe = {
        "escalated_at_cycle": escalated_at,
        "live_clean_cycles": entry["live_clean_cycles"],
        "live_clean_cycles_counted": list(entry["live_clean_cycles_counted"]),
    }
    completed = next_completed_cycle
    ceiling = (
        max(0, escalated_at - next_completed_cycle + 1)
        + len(probe["live_clean_cycles_counted"])
        + LIVE_CLEAN_CYCLES_TO_CLEAR
    )
    for crossings in range(1, ceiling + 1):
        cleared = _clean_arm_step(probe, completed, lambda _c: False)
        completed += 1
        if cleared:
            return crossings
    return None




def _advance_escalation_exits(
    fdir: Path, project_root: str, completed_cycle: int, boundary_cycle: int
) -> list[dict]:
    """Apply BOTH escalation exit arms at the INSPECT boundary (ST-001/ST-002).

    Called from `foundry_mark_phase_complete("inspect_start")` and nowhere else.
    THAT is the point: both arms are stated in terms of CYCLES — "two
    consecutive INSPECT cycles" and "the second structural packet CLOSED" — and
    the boundary crossing is the only event that knows a cycle has ended.

    `completed_cycle` is the counter BEFORE the increment: the cycle whose
    INSPECT and GRIND have just finished, so every filing that cycle will ever
    receive is already in the ledger, and any structural packet dispatched in it
    has now closed. `boundary_cycle` is the counter AFTER the crossing, which is
    what `cleared_at_cycle` records for both arms — the exit is applied BY this
    boundary, and dating it to the cycle that just ended would put the exit
    inside the cycle whose work produced it.

    D-057 — THE CLEAN ARM COUNTED CALLS, NOT CYCLES.
    ------------------------------------------------
    `foundry_mark_phase_complete` increments `state["cycle"]` only when the
    previous phase was F3, but called the clean arm UNCONDITIONALLY, and the arm
    did `live_clean_cycles += 1` with no record of which cycles it had already
    counted. The budget arm has exactly the idempotence this lacked
    (`structural_packet_cycles`, guarded by `if packet_cycle not in ...`).
    Driven through real doors only: escalate at cycle 3, one honest crossing,
    then two further `Foundry-Phase(inspect_start)` calls (phase already F2, so
    the counter does not move, both ok) -> `live_clean_cycles` reached 2 and the
    class CLEARED with `clean_cycles` after ONE real cycle had ended. Reachable
    on the guided path, because `inspect_start` had no phase precondition and
    both Foundry-Next and Foundry-Context re-arm the ordering token — and every
    clean-arm test drove `_cross_boundary`, which force-writes phase F3 first,
    so the suite only ever walked the honest path.

    `live_clean_cycles_counted` is the fix and is the budget arm's guard one
    field over: a closed cycle is EVALUATED AT MOST ONCE, whichever way it goes.
    A cycle that drew a LIVE instance is recorded as counted too — it has been
    evaluated, and re-evaluating it on a second call would be the same defect
    with the sign flipped.

    D-043: the roster is the PERSISTED one — every class `escalation.json`
    records as ESCALATED — and not what `_escalated_classes` returns. The
    difference is a class whose instances have all been fixed: it has no open
    work, so `_escalated_classes` drops it, so it used to reach these arms never
    and sat at ESCALATED for the rest of the run while the F6 report called it
    unresolved. It has zero LIVE instances by construction, which is precisely
    the condition ST-001 counts, so it advances a clean cycle at every crossing
    and CLEARS with `clean_cycles` like any other quiet class.

    Returns the list of classes that CLEARED on this crossing, so the transition
    can report them.
    """
    recorded = _load_json(fdir / ESCALATION_FILENAME).get("classes", {})
    if not isinstance(recorded, dict):
        recorded = {}
    if not _persisted_escalations(fdir, project_root, recorded):
        return []
    defects = _load_json(fdir / "defects.json").get("defects", [])
    buckets = _class_buckets(defects)

    cleared: list[dict] = []
    with _document_transaction(fdir / ESCALATION_FILENAME) as data:
        classes = data.setdefault("classes", {})
        if not isinstance(classes, dict):
            classes = data["classes"] = {}
        for key in _persisted_escalations(fdir, project_root, classes):
            entry = classes[key]
            # D-212: the roster now carries a class whose entry is not a
            # mapping, because such an entry reads as ESCALATED rather than
            # vanishing. `_escalation_entry_defaults` calls `setdefault` on it.
            # Normalised exactly as `_record_escalation_proposals` already
            # normalises the same document, so the arms can advance it and the
            # class reaches a bounded exit instead of blocking DONE forever. A
            # non-mapping entry carries no field worth preserving.
            if not isinstance(entry, dict):
                entry = classes[key] = {}
            # D-237 — AND THIS IS THE CROSSING THAT STAMPS AN UNSTAMPED CLASS.
            #
            # `completed_cycle`, not `boundary_cycle`: the stamp means "the
            # class was escalated no later than this", and `_clean_arm_step`
            # counts only cycles STRICTLY after it. Stamping with the cycle just
            # closed therefore banks nothing on this crossing, banks one on the
            # next and clears on the one after — which is precisely what the
            # DONE refusal's remedy already tells the operator ("The next
            # crossing records it, and counting starts from the crossing after
            # that"), and what it never did. A class the ledger CAN date keeps
            # its own date: the write fires only over a non-int.
            _escalation_entry_defaults(entry, escalated_at_cycle=completed_cycle)

            def _clear(reason: str) -> None:
                info = _class_info(
                    key, buckets.get(key) or _empty_class_bucket(key), classes
                )
                entry["status"] = ESCALATION_STATUS_CLEARED
                entry["exit_reason"] = reason
                entry["cleared_at_cycle"] = boundary_cycle
                entry["open_latent_defect_ids"] = info["open_latent_defect_ids"]
                cleared.append({
                    "class": key,
                    "exit_reason": reason,
                    "cleared_at_cycle": boundary_cycle,
                    "structural_packets_dispatched": entry[
                        "structural_packets_dispatched"
                    ],
                    "live_clean_cycles": entry["live_clean_cycles"],
                    "open_live_defect_ids": info["open_live_defect_ids"],
                    "open_latent_defect_ids": info["open_latent_defect_ids"],
                })

            # ST-002's arm, evaluated first. The budget is exhausted when the
            # STRUCTURAL_PASS_BUDGET-th packet has CLOSED, and a packet closes
            # when the GRIND cycle it was dispatched in ends — which is the
            # cycle this boundary has just closed, or an earlier one.
            packet_cycles = sorted(
                c for c in entry["structural_packet_cycles"]
                if isinstance(c, int) and not isinstance(c, bool)
            )
            if len(packet_cycles) >= STRUCTURAL_PASS_BUDGET and (
                completed_cycle >= packet_cycles[STRUCTURAL_PASS_BUDGET - 1]
            ):
                _clear("budget")
                continue

            # D-157: the arm's guards, the adjacency test and the accumulator
            # all live in `_clean_arm_step` now, because the DONE refusal's
            # "N more crossings" sentence walks that same function to state its
            # distance. Two derivations of one number is what D-157 filed.
            if _clean_arm_step(
                entry,
                completed_cycle,
                lambda c, _key=key: _class_drew_live_in_cycle(defects, _key, c),
            ):
                _clear("clean_cycles")
        data["updated_at"] = now_iso()
    return cleared
