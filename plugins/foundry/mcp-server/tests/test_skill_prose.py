"""Pins the PROVE AGENT and the four VERIFICATION SKILLS: the population that
decides what a finding IS.

Why a third prose module rather than more assertions in the two that exist.
---------------------------------------------------------------------------
``test_protocol_prose.py`` pins what the NON-PROVE stream agents and the
teammate are told; ``test_lead_prose.py`` pins what the LEAD is told. This one
pins ``agents/assayer.md``, ``skills/prove/SKILL.md``,
``skills/trace/SKILL.md``, ``skills/sight/SKILL.md`` and
``skills/temper/SKILL.md`` -- the surfaces that decide, before anything is
written to a ledger, whether a finding is a defect, an observation, or a probe
idea nobody has driven yet. All five are spelled out rather than brace-globbed,
and that is asserted below: a home whose population is written as a glob cannot
be searched for the path a reader is holding, so the rule pinned in it is
pinned somewhere nobody can find.

Where fallout NFR-011 says a rule is pinned, and why this module counts
----------------------------------------------------------------------
fallout NFR-011 names ``test_protocol_prose.py`` and ``test_lead_prose.py`` as
the two homes a prose rule may be pinned in. It was written before this module
existed, and ``_BRANCH_CLAUSES`` below is pinned in neither -- the fallout
AC-018 / AC-058 branch is stated on two files no other home owns, and
D-149 is the record of the gap. The requirement is Flexible on mechanism and binding on
outcome, so its two-name list is GENERALISED here rather than contradicted:
the homes are discovered by glob, each declares the population it pins, the two
names the requirement carries are asserted to still be members, and no rule is
carried by two of them. That is the shape Holmes ``pin-4`` prescribed when it
sent this module into existence -- per-population homes -- expressed as an
assertion instead of a convention.

The three populations drift independently and fail for different reasons. A
lead file rots when a tool is added and its row is not; a stream-agent file
rots when a shared rule is reworded in four of five places; these five rot when
one arm of a branch is written and the other is left on the old contract, which
is a failure neither of the other modules is positioned to see. The catch-all
pin the Holmes review grades ``pin-4`` -- organised by defect lineage rather
than by tool -- is what this module declines to grow further: its remedy is
per-population homes, and this is the home for the population above.

Every assertion below answers a concrete failure, and the ones that are not
obvious carry the requirement id and that failure in a comment beside them.

Why substring assertions and not a parser
-----------------------------------------
Inherited from ``test_protocol_prose.py`` and not re-argued here: the pinned
strings are load-bearing English with no schema to validate against, a fuzzy
check would pass on prose saying the opposite, and where several files must
agree on a term the assertion is parametrised across a DERIVED roster so a
partial edit that fixes two of four fails naming the two it forgot. Absence
assertions carry the other half, because a deletion is invisible to every
positive test ever written and prose that CONTRADICTS a pin four bullets later
is invisible to a positive substring check.

Where the vocabulary comes from
-------------------------------
Every closed vocabulary this module pins is READ from
``foundry_mcp.schemas.vocab`` in the ``_PYTEST_DISCOVERY_PHRASE`` shape rather
than re-typed, so a vocabulary change fails on both sides at once: the prose
that still names the old spelling, and the module that still expects it.

What this module does NOT pin, deliberately
-------------------------------------------
The five shared filing bullets. ``agents/assayer.md`` is their source and
``skills/sight/SKILL.md`` and ``skills/temper/SKILL.md`` carry them
byte-identically; ``test_lead_prose.py`` and ``test_protocol_prose.py`` already
hold that, over rosters that include the four non-PROVE stream agents. Pinning
them here as well would put one ruling in three modules free to drift apart,
and the tier bullet in particular is shared with files this casting may not
edit -- so its wording is a cross-casting change, not a local one.
"""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path

import pytest

from foundry_mcp import server as foundry_server
from foundry_mcp.schemas import vocab
from foundry_mcp.tools import foundry_report
from foundry_mcp.tools.foundry import validate_defect_filing
from foundry_mcp.tools.orchestration import gates, guidance, streams

# The ONE spelling of the fallout-marking clauses, imported rather than
# re-typed. See the fallout AC-048 section below for why this import is here
# and not a tuple of its own.
from tests.test_protocol_prose import _FALLOUT_CLAUSES

REPO_ROOT = Path(__file__).resolve().parents[4]
FOUNDRY_ROOT = REPO_ROOT / "plugins" / "foundry"

AGENTS = FOUNDRY_ROOT / "agents"
SKILLS = FOUNDRY_ROOT / "skills"

#: The PROVE agent. It runs as the F2 PROVE stream and again as the F4 ASSAY
#: agent; every ruling this module pins on it belongs to the first identity.
ASSAYER = AGENTS / "assayer.md"

PROVE_SKILL = SKILLS / "prove" / "SKILL.md"
TRACE_SKILL = SKILLS / "trace" / "SKILL.md"
SIGHT_SKILL = SKILLS / "sight" / "SKILL.md"
TEMPER_SKILL = SKILLS / "temper" / "SKILL.md"

#: The five files this module owns, for the floor check below only. Every
#: assertion that follows sweeps a DERIVED roster instead; this tuple exists so
#: that a rename fails HERE, naming the file, rather than as a derivation that
#: quietly returns one member fewer.
PINNED_FILES = (ASSAYER, PROVE_SKILL, TRACE_SKILL, SIGHT_SKILL, TEMPER_SKILL)


def _rel(path: Path) -> str:
    """A pinned file's repo-relative path, for messages."""
    return path.relative_to(REPO_ROOT).as_posix()


def _read(path: Path) -> str:
    """Read a pinned file, failing loudly if it moved or was deleted."""
    assert path.is_file(), (
        f"{_rel(path)} does not exist. If the file was renamed, update this "
        f"module's path constants -- do not delete the assertions, the prose "
        f"they pin is still required."
    )
    return path.read_text(encoding="utf-8")


def _flat(path: Path) -> str:
    """One file with its line wrapping collapsed.

    Every clause pinned below is quoted as it reads, not as the source happens
    to wrap it: ``skills/prove/SKILL.md`` wraps its rules at 88 columns and
    ``agents/assayer.md`` does not wrap at all, so a pin written against either
    file's line breaks would be a pin on the formatter.
    """
    return " ".join(_read(path).split())


def _declared_tier_enum(path: Path) -> frozenset[str] | None:
    """The `tier` enum a skill's findings schema declares, or None.

    Returns None rather than raising for a file with no single parseable
    ```json block, because absence is what the roster derivation reads -- a
    skill that documents no wire shape is not a narrower version of one that
    does.
    """
    blocks = re.findall(r"```json\n(.*?)\n```", _read(path), re.S)
    if len(blocks) != 1:
        return None
    try:
        schema = json.loads(blocks[0])
    except json.JSONDecodeError:
        return None
    found = _first_property(schema, "tier")
    members = (found or {}).get("enum")
    return frozenset(members) if isinstance(members, list) else None


def _first_property(node: object, name: str) -> dict | None:
    """The first `properties[name]` mapping anywhere in a parsed schema.

    Walked rather than indexed at a fixed depth: the two files nest their
    findings item one level apart today, and a pin on the nesting would fail on
    a reshape that changed no contract.
    """
    if isinstance(node, dict):
        properties = node.get("properties")
        if isinstance(properties, dict) and isinstance(properties.get(name), dict):
            return properties[name]
        for value in node.values():
            found = _first_property(value, name)
            if found is not None:
                return found
    elif isinstance(node, list):
        for value in node:
            found = _first_property(value, name)
            if found is not None:
                return found
    return None


def _reproduction_description(path: Path) -> str:
    """The `reproduction_attempted` description the skill's schema states."""
    found = _first_property(_schema_of(path), "reproduction_attempted")
    assert found is not None, (
        f"{_rel(path)}'s findings schema declares no `reproduction_attempted` "
        f"property, so the field the doors refuse a filing without has no "
        f"description for a stream to read."
    )
    description = found.get("description")
    assert isinstance(description, str) and description.strip(), (
        f"{_rel(path)}'s `reproduction_attempted` property carries no "
        f"description. An undescribed required-on-some-tiers field is one a "
        f"stream omits and a door refuses."
    )
    return description


def _schema_of(path: Path) -> dict:
    """The one parsed findings schema in a schema-bearing skill."""
    blocks = re.findall(r"```json\n(.*?)\n```", _read(path), re.S)
    assert len(blocks) == 1, (
        f"{_rel(path)} has {len(blocks)} ```json blocks, expected exactly 1."
    )
    return json.loads(blocks[0])


@pytest.mark.parametrize("path", PINNED_FILES, ids=_rel)
def test_every_pinned_prose_file_exists(path: Path) -> None:
    """Floor check: this module's population is reachable.

    A parametrised assertion over a file that has moved reports one clear
    failure here instead of five confusing ones further down, and a module
    whose paths have all rotted would otherwise report itself green by
    asserting nothing at all.
    """
    assert _read(path).strip(), (
        f"{_rel(path)} is empty. The rulings this module pins reach their "
        f"stream through this file and through nothing else."
    )


# ===========================================================================
# The vocabulary, READ rather than re-typed.
#
# fallout NFR-011: every term pinned below is composed from the constant that
# declares it, in the `_PYTEST_DISCOVERY_PHRASE` shape. A vocabulary rename
# then fails on BOTH sides at once -- the prose that still spells the old name,
# and this module that still expects it -- instead of leaving five files
# teaching a value the doors refuse. That is the D-045 shape and this
# population has already paid for it once: skills/temper/SKILL.md shipped a
# `source` the server hard-refuses, and every positive pin over it was green.
# ===========================================================================

_TEMPER_CANDIDATE = vocab.TEMPER_CANDIDATE
_HARDENING = vocab.TIER_HARDENING

#: The symbol name the prose must cite for the stream vocabulary, recovered
#: from the module rather than typed: rename the constant and the expected cite
#: moves with it, so prose naming the old spelling fails HERE rather than
#: rotting into a cite that resolves to nothing.
_STREAM_VOCAB_SYMBOL = next(
    name for name, value in sorted(vars(vocab).items())
    if value is vocab.STREAM_WIRE_IDS
)
_VOCAB_MODULE_PATH = "plugins/foundry/mcp-server/src/foundry_mcp/schemas/vocab.py"
_STREAM_VOCAB_CITE = f"{_VOCAB_MODULE_PATH}#{_STREAM_VOCAB_SYMBOL}"
_TIER_VOCAB_CITE = f"{_VOCAB_MODULE_PATH}#DEFECT_TIERS"


def test_the_two_terms_this_module_pins_are_vocabulary_members() -> None:
    """fallout NFR-011's floor: a term pinned from a constant that stopped
    declaring it would pass every assertion below while teaching a refusal.
    """
    assert _TEMPER_CANDIDATE in vocab.OBSERVATION_CLASSES, (
        f"{_TEMPER_CANDIDATE!r} is no longer a member of "
        f"vocab.OBSERVATION_CLASSES, so `Foundry-Observation` refuses the "
        f"classification both PROVE files tell the stream to declare on its "
        f"TEMPER-on arm."
    )
    assert _HARDENING in vocab.DEFECT_TIERS, (
        f"{_HARDENING!r} is no longer a member of vocab.DEFECT_TIERS, so the "
        f"filing doors refuse the tier both PROVE files tell the stream to use "
        f"on its TEMPER-off arm."
    )
    assert _HARDENING not in gates.BLOCKING_TIERS, (
        f"{_HARDENING!r} joined gates.BLOCKING_TIERS. Both PROVE files tell "
        f"the stream this tier holds no gate shut; a blocking HARDENING makes "
        f"the off-row arm a way to stop a run on a path no requirement states, "
        f"which is the opposite of what it was added for."
    )


def test_the_temper_skill_is_right_that_temper_is_not_a_stream_wire_id() -> None:
    """fallout AC-031, the cross-door join: prose and door, or neither.

    `skills/temper/SKILL.md` tells its agents that `temper` is a filing
    identity and NOT a stream id, and that a `Foundry-Stream` call passing it
    is rejected. Read off the prose that is unfalsifiable; joined here it is a
    pair, and the two positive halves keep the join honest -- a vocabulary that
    carried `temper` in neither set would satisfy a one-sided check while
    making the whole paragraph wrong.
    """
    assert "temper" not in vocab.STREAM_WIRE_IDS, (
        "`temper` became a STREAM_WIRE_IDS member, so skills/temper/SKILL.md "
        "now teaches a refusal the door no longer gives. Fix the prose in the "
        "same commit as the vocabulary, or a TEMPER pass reads that its only "
        "legal stream value is illegal."
    )
    assert "temper" in vocab.DEFECT_SOURCE_IDS, (
        "`temper` left DEFECT_SOURCE_IDS. The temper skill files every finding "
        "under that source; without the member the filings are refused and the "
        "distinction its stream paragraph draws has nothing on either side."
    )
    refusal = streams.foundry_mark_stream("temper", cycle=1, items_checked=1)
    assert "error" in refusal, (
        "`Foundry-Stream` accepted `stream: \"temper\"`. skills/temper/SKILL.md "
        "tells every TEMPER agent the call is rejected at the boundary and to "
        "pass the wire id of the stream it RAN AS instead; a door that accepts "
        "it records coverage under an id no roll-up reads."
    )


def test_a_temper_candidate_is_reachable_only_by_declaring_it() -> None:
    """fallout AC-018 / CT-017: the claim both PROVE files make about the door.

    Both say declaring `classification` is what lifts the `target_kind`
    requirement, on the ground that the four inferable classes are
    comment-prose predicates and none of them can ever name this one. Driven
    here rather than trusted: if a predicate ever returned it, an off-row probe
    idea would be inferable from its description and the declaration the prose
    insists on would stop being load-bearing.
    """
    for description in (
        "the retry branch may double-charge when the second attempt races",
        "this cite's line number is stale and no longer matches the code",
        "the count in this comment says three and the list below has four",
    ):
        inferred = vocab.observation_class({"description": description})
        assert inferred != _TEMPER_CANDIDATE, (
            f"vocab.observation_class inferred {_TEMPER_CANDIDATE!r} from "
            f"{description!r}. Both PROVE files tell the stream the class is "
            f"reachable only by declaring it; an inferable one arrives on "
            f"findings nobody meant to defer to TEMPER."
        )


def test_the_report_reads_both_driven_spellings_the_temper_skill_names(
    tmp_path: Path,
) -> None:
    """fallout ST-007 / OT-022: the closure the skill promises is the closure
    the F6 report actually counts.

    `skills/temper/SKILL.md` names two record shapes as closed -- `driven`, and
    `status: "DRIVEN"` -- and says a record carrying neither is listed
    undriven. That is a claim about the REPORT, so it is driven against the
    report's own reader. The undriven case is asserted alongside both closed
    ones: a reader that counted everything as driven would satisfy a one-sided
    check while making the listing the skill warns about impossible.
    """
    (tmp_path / "observations.json").write_text(
        json.dumps(
            {
                "observations": [
                    {"id": "O-1", "classification": _TEMPER_CANDIDATE, "driven": True},
                    {"id": "O-2", "classification": _TEMPER_CANDIDATE, "status": "DRIVEN"},
                    {"id": "O-3", "classification": _TEMPER_CANDIDATE},
                ]
            }
        ),
        encoding="utf-8",
    )
    section, problem = foundry_report._read_undriven_temper_candidates(tmp_path)
    assert problem is None, problem
    assert section["driven_count"] == 2, (
        f"the report counted {section['driven_count']} driven candidates, not "
        f"2. skills/temper/SKILL.md names `driven` AND `status: \"DRIVEN\"` as "
        f"closures; a reader honouring one of the two lists half of TEMPER's "
        f"finished work as debt."
    )
    assert [c["id"] for c in section["candidates"]] == ["O-3"], (
        "the report did not list the candidate carrying neither marker. That "
        "listing is the entire cost skills/temper/SKILL.md names for skipping "
        "a candidate; without it, leaving one alone is free and invisible."
    )


# ===========================================================================
# THE ROSTERS, DERIVED.
#
# A hand-typed roster cannot fail on the file somebody forgot, and every
# parametrisation below is only as good as the population it sweeps. Each
# derivation is followed by a floor check that asserts its known members IN and
# at least one non-member OUT: five green cases over four files look exactly
# like five green cases over five, and the narrowing is invisible at the
# assertions that read it.
# ===========================================================================

#: The verification skills, derived by the property the rulings below are
#: ABOUT: a skill that records a coverage stream. A skill dropping out of this
#: roster by losing its `Foundry-Stream` statement is itself the defect, which
#: is why the derivation keys on that rather than on the directory listing.
VERIFICATION_SKILLS = tuple(
    sorted(
        (p for p in SKILLS.glob("*/SKILL.md") if "Foundry-Stream" in p.read_text(encoding="utf-8")),
        key=_rel,
    )
)

#: The two PROVE surfaces, derived by the width roster they read. Both take
#: their coverage pair from `inspect_mode.prove_sample` and nothing else does,
#: so the property is the stream's identity rather than a filename -- renaming
#: either file keeps it in this roster, and a file that stops being a PROVE
#: surface leaves it.
PROVE_SURFACES = tuple(
    sorted(
        (
            p
            for p in (ASSAYER, *SKILLS.glob("*/SKILL.md"))
            if "inspect_mode.prove_sample" in p.read_text(encoding="utf-8")
        ),
        key=_rel,
    )
)

#: Every surface in this module's population that records a stream: the PROVE
#: agent plus the four skills. fallout AC-031 names nine producers; the other
#: five are the non-PROVE stream agents, swept in `test_protocol_prose.py`.
STREAM_RECORDING_SURFACES = (ASSAYER, *VERIFICATION_SKILLS)

#: The three surfaces that state the `fallout_of` ruling. DECLARED, not
#: derived, and the reason is that the split is the SPEC's rather than a
#: property of the files: fallout AC-048 puts this ruling on `agents/tracer.md`
#: (casting 6's, pinned there) and on these three. A derivation over "files
#: that mention fallout" would be the clause pinning itself. The floor check
#: below holds all three inside this module's population instead.
FALLOUT_SURFACES = (ASSAYER, PROVE_SKILL, TRACE_SKILL)

#: The skills that hand their stream a normative wire shape carrying the tier
#: vocabulary, derived by exactly that property. `sight` and `temper` document
#: no findings schema and declare no tier enum, so sweeping them would pin
#: prose that never claims what a tier is. A skill joins by declaring the enum
#: and leaves by dropping it -- and dropping it is itself the defect, because
#: the block is the shape a stream copies onto the wire.
SCHEMA_BEARING_SKILLS = tuple(
    p for p in VERIFICATION_SKILLS if _declared_tier_enum(p) is not None
)


def test_the_verification_skill_roster_is_derived() -> None:
    """Floor check: every clause pin below sweeps all four skills."""
    rel = {_rel(p) for p in VERIFICATION_SKILLS}
    missing = sorted({_rel(p) for p in (PROVE_SKILL, TRACE_SKILL, SIGHT_SKILL, TEMPER_SKILL)} - rel)
    assert not missing, (
        f"{missing} no longer derive into VERIFICATION_SKILLS. A skill drops "
        f"out by losing its `Foundry-Stream` statement -- which is itself the "
        f"defect, because a skill that names the door nowhere is a stream the "
        f"roll-up and `Foundry-Liveness` cannot find. Fix the skill rather "
        f"than hard-coding this roster."
    )
    assert len(VERIFICATION_SKILLS) == 4, sorted(rel)


def test_the_prove_surface_roster_is_derived() -> None:
    """Floor check: the branch is pinned on BOTH PROVE files, never one."""
    rel = {_rel(p) for p in PROVE_SURFACES}
    assert rel == {_rel(ASSAYER), _rel(PROVE_SKILL)}, (
        f"PROVE_SURFACES derived {sorted(rel)}. It is the pair that reads its "
        f"width from `inspect_mode.prove_sample`: the PROVE agent and the "
        f"PROVE skill. A pair that lost a member would pin the branch in one "
        f"file and leave the other on the old undifferentiated rule, which is "
        f"the two-surfaces-of-one-rule-disagree failure this effort exists to "
        f"end."
    )


def test_the_fallout_surfaces_are_all_in_this_modules_population() -> None:
    """Floor check: the declared roster names no file this module does not own."""
    stray = sorted(_rel(p) for p in set(FALLOUT_SURFACES) - set(PINNED_FILES))
    assert not stray, (
        f"{stray} carry the fallout ruling but are not this module's to pin. "
        f"`agents/tracer.md` is the fourth surface and is pinned in "
        f"tests/test_protocol_prose.py; sweeping it here would pin one ruling "
        f"in two modules free to drift apart."
    )


# ---------------------------------------------------------------------------
# fallout AC-022 (D-163 / D-164) -- the tier vocabulary, wherever a schema-
# bearing skill states it
# ---------------------------------------------------------------------------
#
# `test_protocol_prose.py` sweeps its tier pins over `DEFECT_FILING_AGENTS`,
# derived from a filing-door mention AND a documented `defects` array, and over
# the span `_tier_rule` cuts between the shared no-severity bullet and its
# no-discretion close. `skills/prove/SKILL.md` and `skills/trace/SKILL.md` are
# in neither: they document a `findings` array rather than a `defects` one, and
# they state the tier ruling in a paragraph and a constraint bullet that carry
# no shared-bullet opener for that span to find. So both files went on
# describing two members and closing "Both tiers are defects, both get fixed"
# for the whole life of the HARDENING tier, with every existing pin green --
# which is the gap these assertions close rather than the two sentences.
#
# The sweep is over SPANS as well as files, because the ruling is stated twice
# in each file (beside the JSON block, and again in the constraint list a
# stream re-reads while working) and a fix to one is invisible to a pin on the
# other. Both spans, both files, or the pair is not implemented.


#: The two regions of a schema-bearing skill that state what a tier IS, each
#: cut between two fixed sentences the file already carries. Named rather than
#: merged because a stream reads them at different moments -- the paragraph
#: once, beside the block it explains; the constraint every time it re-reads
#: its obligations -- and D-163 is exactly one of the two going stale.
_TIER_SPANS = (
    (
        "no-severity paragraph",
        "**There is no `severity` field, and adding one is a vocabulary violation.**",
        "coerced onto something known.",
    ),
    (
        "evidence-axis constraint",
        "- **Grade the evidence, never the effort**",
        'no "this one is only cosmetic."',
    ),
)


def _tier_span(path: Path, opener: str, closer: str) -> str:
    """One bounded tier region of a file, flattened, or "" if absent."""
    flat = _flat(path)
    start = flat.find(opener)
    if start == -1:
        return ""
    stop = flat.find(closer, start)
    return "" if stop == -1 else flat[start : stop + len(closer)]


def test_the_schema_bearing_skill_roster_is_derived() -> None:
    """Floor check: the tier pins below sweep the two skills that declare the
    vocabulary, and neither of the two that do not.

    The narrowing is the whole risk here. `sight` and `temper` state the tier
    ruling through the shared filing bullets `test_protocol_prose.py` already
    holds; `prove` and `trace` state it in their own voice and are pinned
    nowhere else. A roster that quietly lost one of the two would leave that
    file free to drift back, with every assertion below still green.
    """
    rel = {_rel(p) for p in SCHEMA_BEARING_SKILLS}
    assert rel == {_rel(PROVE_SKILL), _rel(TRACE_SKILL)}, (
        f"SCHEMA_BEARING_SKILLS derived {sorted(rel)}. A skill leaves this "
        f"roster by dropping the `tier` enum from its findings schema, which "
        f"is itself the defect: the block is the shape a stream copies onto "
        f"the wire, and one without a tier is refused at both filing doors."
    )
    for path in SCHEMA_BEARING_SKILLS:
        assert _declared_tier_enum(path) == frozenset(vocab.DEFECT_TIERS), (
            f"{_rel(path)}'s findings schema advertises "
            f"{sorted(_declared_tier_enum(path) or ())} against "
            f"vocab.DEFECT_TIERS {sorted(vocab.DEFECT_TIERS)}. The prose pins "
            f"below read the vocabulary, so a schema out of step with it would "
            f"make them demand prose about a tier the block never offers."
        )


@pytest.mark.parametrize("path", SCHEMA_BEARING_SKILLS, ids=lambda p: p.parent.name)
@pytest.mark.parametrize("span_name,opener,closer", _TIER_SPANS, ids=lambda v: v[:24])
def test_every_tier_region_is_findable(
    path: Path, span_name: str, opener: str, closer: str
) -> None:
    """Floor check for the two pins below: an unfindable span asserts nothing.

    Both are cut between sentences other pins already hold -- the opener of the
    first is `test_skill_schemas_carry_no_severity_axis`'s, the closer of the
    second is `test_skill_evidence_axis_constraint_closes_on_no_discretion`'s
    -- so a span that stops resolving here means one of those moved, and the
    member sweep below would otherwise pass over an empty string.
    """
    assert _tier_span(path, opener, closer), (
        f"{_rel(path)}'s {span_name} no longer runs from {opener!r} to "
        f"{closer!r}. The pins below read this region; unfindable, they sweep "
        f"an empty string and report green over prose nobody checked."
    )


#: DERIVED from the vocabulary, never re-typed -- the `_PYTEST_DISCOVERY_PHRASE`
#: shape fallout NFR-011 requires. A member added to DEFECT_TIERS fails here
#: until both regions of both files describe it, which is the half D-163 and
#: D-164 record: fallout GI-014 added HARDENING, the schema enum learned it,
#: and the prose either side of the block went on describing the two it knew.
_DECLARED_TIERS = tuple(sorted(vocab.DEFECT_TIERS))


@pytest.mark.parametrize("path", SCHEMA_BEARING_SKILLS, ids=lambda p: p.parent.name)
@pytest.mark.parametrize("span_name,opener,closer", _TIER_SPANS, ids=lambda v: v[:24])
@pytest.mark.parametrize("member", _DECLARED_TIERS)
def test_every_tier_region_names_every_declared_member(
    path: Path, span_name: str, opener: str, closer: str, member: str
) -> None:
    """fallout AC-022 (D-163 / D-164): prose describing fewer tiers than the
    doors accept.

    The enum in the block between these two regions has been correct since
    fallout GI-014; both regions describing it stayed two-membered. A stream
    reads the prose to decide which tier to SET and the enum only to check the
    spelling, so the region is where the member exists or does not.
    """
    span = _tier_span(path, opener, closer)
    assert f"`{member}`" in span, (
        f"{_rel(path)}'s {span_name} never names `{member}`, a member both "
        f"filing doors accept ({sorted(vocab.DEFECT_TIERS)}). This region is "
        f"where a stream learns the member exists and when to set it, so an "
        f"undescribed member is one no stream ever files -- and the enum in "
        f"the block beside it offering a value the prose never explains is "
        f"how the tier went unused for the life of this file."
    )


#: The counted spellings, in the two shapes this population reaches for: a
#: hyphenated member count ("two-member vocabulary") and the paired quantifier
#: that says the same thing in English ("Both tiers are defects"). The second
#: is what D-163 and D-164 found; the first is what `test_protocol_prose.py`
#: found in seven files at once, and it is pinned here so the fix does not
#: swap one count for the other.
_TIER_COUNT_RE = re.compile(
    r"\b(?:both|either|neither|one|two|three|four|five|\d+)[- ]tiers?\b"
    r"|\b(?:one|two|three|four|five|\d+)[- ]member\b",
    re.I,
)


@pytest.mark.parametrize("path", SCHEMA_BEARING_SKILLS, ids=lambda p: p.parent.name)
@pytest.mark.parametrize("span_name,opener,closer", _TIER_SPANS, ids=lambda v: v[:24])
def test_no_tier_region_counts_the_members(
    path: Path, span_name: str, opener: str, closer: str
) -> None:
    """fallout AC-022's absence half: a counted vocabulary is a re-typed one.

    The positive pin above is satisfied by adding a sentence, and adding one
    leaves "Both tiers are defects, both get fixed" standing four clauses
    later -- a universal claim scoped to a count that is now wrong, in the same
    breath as prose naming three members. A deletion is invisible to every
    positive test, and so is a contradiction left beside one.
    """
    span = _tier_span(path, opener, closer)
    hit = _TIER_COUNT_RE.search(span)
    assert hit is None, (
        f"{_rel(path)}'s {span_name} counts the tier vocabulary "
        f"({hit.group(0)!r}) instead of citing DEFECT_TIERS, which declares "
        f"{len(vocab.DEFECT_TIERS)}. A count is a second copy of len() that no "
        f"door reads and nothing updates: it was right when it was written, "
        f"wrong the moment GI-014 landed, and green in every test until now. "
        f"State the claim over every tier and let the vocabulary carry the "
        f"number."
    )


def _tiers_the_doors_demand_a_reproduction_of() -> tuple[str, ...]:
    """The tiers the SHIPPED filing check refuses without a reproduction.

    DRIVEN rather than declared: the obligation is not a property of the
    vocabulary (LIVE is a member and owes nothing) and it is not written down
    anywhere a test could read it -- `validate_defect_filing` reaches the rung
    for LATENT and again for HARDENING, and which tiers those are is the answer
    this returns. Widening the rung to a third tier then fails the pin below
    until both skills say so, which is the direction the drift actually runs.
    """
    probe = {
        "id": "D-000",
        "classification": "DEFECT",
        "type": "WRONG",
        "class": "PROSE_CONTRACT_DRIFT",
        "file": "a.py",
        "symbol": "f",
        "description": (
            "The pagination cursor repeats the last row whenever the page size "
            "divides the total exactly, on every page after the first."
        ),
    }
    return tuple(
        tier
        for tier in sorted(vocab.DEFECT_TIERS)
        if (refusal := validate_defect_filing({**probe, "tier": tier}))
        and refusal.get("field") == "reproduction_attempted"
    )


_REPRODUCTION_TIERS = _tiers_the_doors_demand_a_reproduction_of()


def test_the_reproduction_obligation_is_driven_and_not_universal() -> None:
    """Floor check: the derivation above found a real, proper subset.

    Empty, the pin below sweeps nothing and passes over any description at
    all. Equal to DEFECT_TIERS, it has stopped distinguishing the tiers that
    owe evidence from the one that owes none, and the prose it demands would
    be wrong about LIVE.
    """
    assert _REPRODUCTION_TIERS, (
        "validate_defect_filing refuses no tier for a missing "
        "`reproduction_attempted`. That rung is CT-001's, shared by both "
        "filing doors; if it really went away the prose below is stale in the "
        "other direction and these pins should be rewritten, not deleted."
    )
    assert set(_REPRODUCTION_TIERS) < set(vocab.DEFECT_TIERS), (
        f"every member of vocab.DEFECT_TIERS now owes a reproduction "
        f"({sorted(_REPRODUCTION_TIERS)}). LIVE carries its evidence in the "
        f"description and owed none; a rung that refuses it too is a contract "
        f"change no prose here has been told about."
    )


@pytest.mark.parametrize("path", SCHEMA_BEARING_SKILLS, ids=lambda p: p.parent.name)
@pytest.mark.parametrize("tier", _REPRODUCTION_TIERS)
def test_the_reproduction_description_names_every_tier_the_doors_demand_it_of(
    path: Path, tier: str
) -> None:
    """fallout AC-022 (D-163 / D-164): the field description scoped to one of
    the two tiers that owe it.

    Both files described the obligation as LATENT's alone. A HARDENING filing
    is refused on the same rung, with a hint about a probe the skill never told
    the stream to record -- so the stream reads a description saying the field
    does not apply to it, and meets a refusal saying it does.
    """
    description = _reproduction_description(path)
    assert tier in description, (
        f"{_rel(path)}'s `reproduction_attempted` description never names "
        f"{tier}, a tier `validate_defect_filing` refuses without the field "
        f"(it demands one of {list(_REPRODUCTION_TIERS)}). A description that "
        f"scopes the obligation to fewer tiers than the rung does sends the "
        f"stream into a refusal its own schema told it could not happen."
    )


# ---------------------------------------------------------------------------
# fallout AC-018 / AC-058 / FR-016 / FR-044 / FR-060 / GI-005 / GI-015 /
# GI-030 / OT-021 / OT-043 -- INSPECT verifies, TEMPER hunts
# ---------------------------------------------------------------------------
#
# The ruling is a BRANCH and the failure it guards against is half of one. A
# file stating only the TEMPER-on arm narrows PROVE to matrix rows on a run
# where F5 never happens, which is fallout GI-005's named violation in full: "a
# run on which no stream drives novel probes: TEMPER off and PROVE narrowed to
# matrix rows". A file stating only the TEMPER-off arm spends GRIND cycles on
# findings no requirement asked for. Both arms, in both files, or the pair is
# not implemented -- so the parametrisation is over clause AND file, and a fix
# to one of the two fails naming the other.

_BRANCH_CLAUSES = (
    (
        "returns `state.temper`",
        "the READ. Which arm is live is a persisted fact, and a file that "
        "states two arms without naming what to read to choose between them "
        "leaves the choice to judgement -- which is how one run's PROVE files "
        "an off-row probe as a blocking defect and the next one records it",
    ),
    (
        "cite the matrix row whose stated behaviour failed",
        "the TEMPER-on arm's whole content: an INSPECT defect is licensed by a "
        "stated behaviour that failed, and nothing else is",
    ),
    (
        f'classification: "{_TEMPER_CANDIDATE}"',
        "where an off-row finding GOES on the TEMPER-on arm. Without the "
        "channel the arm reads as 'do not file it', and a probe idea nobody "
        "recorded is one TEMPER never receives",
    ),
    (
        "the novel probes at INSPECT",
        "the TEMPER-off arm's imperative: DRIVE them here, because F5 never "
        "runs. A file that only says where an off-row failure is FILED has not "
        "said that the probing happens at all",
    ),
    (
        f"as `{_HARDENING}` — never `LIVE` unless the never-demote denylist fires",
        "the TEMPER-off arm's tier AND its exception, in one clause. Split "
        "into two sentences the exception is the half a later edit drops, and "
        "the arm becomes a route for downgrading a security-property claim",
    ),
)


@pytest.mark.parametrize("path", PROVE_SURFACES, ids=_rel)
@pytest.mark.parametrize("clause,why", _BRANCH_CLAUSES, ids=lambda v: v[:44])
def test_both_prove_surfaces_state_both_arms_of_the_branch(
    path: Path, clause: str, why: str
) -> None:
    """fallout AC-018 / AC-058 / GI-030: one ruling, two registers, both arms."""
    assert clause in _flat(path), (
        f"{_rel(path)} no longer states: {clause!r}. That clause is {why}. "
        f"Both PROVE surfaces state it, each in its own voice around it -- a "
        f"prove skill that branches while agents/assayer.md states one "
        f"undifferentiated filing rule leaves the PROVE stream on the old "
        f"contract, and the stream reads whichever file it was spawned with."
    )


@pytest.mark.parametrize("path", PROVE_SURFACES, ids=_rel)
def test_neither_prove_surface_states_one_undifferentiated_filing_rule(
    path: Path,
) -> None:
    """fallout GI-005 / GI-030, the absence half: half a branch is no branch.

    Every clause above is a positive pin, and positives cannot see the shape
    this guards: a file that keeps the TEMPER-on arm, drops the TEMPER-off one,
    and reads perfectly. Both terms are asserted TOGETHER here so that the
    surviving arm cannot stand in for the pair.
    """
    flat = _flat(path)
    named = {term for term in (_TEMPER_CANDIDATE, _HARDENING) if term in flat}
    assert named == {_TEMPER_CANDIDATE, _HARDENING}, (
        f"{_rel(path)} names {sorted(named)} and not both of "
        f"{sorted((_TEMPER_CANDIDATE, _HARDENING))}. Each term belongs to one "
        f"arm of the branch, so a file carrying one of them states one "
        f"undifferentiated filing rule under a heading that promises two. On "
        f"a TEMPER-off run that leaves fallout GI-005 violated exactly as it "
        f"names the violation: no stream drives a novel probe anywhere in the "
        f"cycle."
    )


def test_the_assayer_cites_the_tier_vocabulary_for_the_off_row_arm() -> None:
    """fallout NFR-011, scoped to the file that owns the tier register.

    `agents/assayer.md` is where every filing surface's tier rule is copied
    FROM, so the off-row arm it states beside that rule is the one that owes
    the cite. Composed from the module path rather than typed, so a moved
    vocabulary fails here naming the prose that still points at the old home.
    """
    assert _TIER_VOCAB_CITE in _flat(ASSAYER), (
        f"agents/assayer.md's TEMPER-off arm does not cite {_TIER_VOCAB_CITE}. "
        f"The tiers are a closed vocabulary with one home; prose that names "
        f"`{_HARDENING}` without pointing at the constant is a copy free to "
        f"drift, and the drift surfaces as a door refusing a tier these "
        f"instructions taught."
    )


# ---------------------------------------------------------------------------
# fallout AC-019 / FR-017 / GI-027 / ST-007 / OT-022 -- TEMPER's roster
# ---------------------------------------------------------------------------

_TEMPER_ROSTER_CLAUSES = (
    (
        f'Foundry-Observations(classification="{_TEMPER_CANDIDATE}")',
        "the read, with its filter, named as the tool schema declares it. A "
        "roster rule that says 'read the candidates' without the call leaves "
        "TEMPER to guess a query the boundary refuses",
    ),
    (
        "open candidates UNIONED with your own micro-domains",
        "fallout GI-027's roster in one clause. Either half alone is a "
        "different phase: candidates only is a worklist, micro-domains only is "
        "the phase as it was before the channel existed",
    ),
    (
        "closed as DRIVEN — filed or clean, both are closures",
        "fallout ST-007's transition. Without 'both are closures' a clean "
        "probe reads as unfinished, and the honest result of driving a "
        "candidate and finding it sound has nowhere to go",
    ),
    (
        "is UNDRIVEN and is listed by id, cycle and description in the report",
        "what skipping one COSTS. A roster rule with no cost is advice, and "
        "fallout OT-022 makes the listing the visible half of the cadence",
    ),
)


@pytest.mark.parametrize("clause,why", _TEMPER_ROSTER_CLAUSES, ids=lambda v: v[:44])
def test_the_temper_skill_states_the_candidate_roster_rule(
    clause: str, why: str
) -> None:
    """fallout AC-019 / GI-027 / ST-007 / OT-022, the prose half.

    Pinned on the temper skill alone, deliberately: the vocabulary member and
    the report section are casting 10's and are pinned with the code that
    writes them. This is the surface that READS the candidates, and it is the
    only one that can leave them undriven.
    """
    assert clause in _flat(TEMPER_SKILL), (
        f"skills/temper/SKILL.md no longer states: {clause!r}. That clause is "
        f"{why}. TEMPER is the phase the recorded probe ideas were saved FOR; "
        f"a TEMPER that never reads them turns the whole channel into a "
        f"write-only ledger."
    )


def test_the_temper_skill_cites_the_stream_vocabulary() -> None:
    """fallout NFR-011, scoped to the file where the statement is NEW.

    `survey/surface.md` records this file as the one naming no `Foundry-Stream`
    at all before this release, so it is the one whose statement had to be
    written rather than confirmed -- and a statement written from memory is
    exactly where a re-typed vocabulary lands. The cite is BUILT from the
    module, so renaming the constant fails here rather than leaving the prose
    pointing at a symbol that resolves to nothing.
    """
    assert _STREAM_VOCAB_CITE in _flat(TEMPER_SKILL), (
        f"skills/temper/SKILL.md does not cite {_STREAM_VOCAB_CITE}. Its "
        f"stream paragraph turns on `temper` NOT being a member of that set; "
        f"prose making that claim without pointing at the set is a claim the "
        f"reader has no way to check."
    )


def _section(path: Path, heading: str) -> str:
    """One `## Heading` section of a markdown file, flattened.

    Absence assertions are scoped to the section that carries the ruling,
    because a whole-file absence check cannot work here: the temper skill
    legitimately calls plenty of OTHER things optional, and a weakening
    spelling only weakens the rule it sits inside.
    """
    text = _read(path)
    assert text.count(heading + " ") == 1, (
        f"{_rel(path)} carries {text.count(heading + ' ')} `{heading}` "
        f"headings; the section pin needs exactly one."
    )
    start = text.index(heading + " ")
    stop = text.index("\n## ", start + len(heading))
    return " ".join(text[start:stop].split())


#: Spellings that would make the candidate roster a suggestion. Absence
#: assertions, because a positive pin cannot see prose ADDED beside it: the
#: section can state the union rule and, two paragraphs later, tell TEMPER to
#: drive the candidates it has room for -- both true to a substring check, and
#: the second one is the one an agent under time pressure believes.
_ROSTER_WEAKENING_SPELLINGS = (
    "at your discretion",
    "if time permits",
    "as many as you can",
    "the ones that look promising",
    "you may skip",
    "optional",
)


def test_the_temper_roster_section_makes_no_candidate_optional() -> None:
    """fallout ST-007's absence half: every candidate is closed, or the
    report's undriven list is the run's real roster."""
    section = _section(TEMPER_SKILL, "## Phase C0: ROSTER")
    found = sorted(s for s in _ROSTER_WEAKENING_SPELLINGS if s in section.lower())
    assert not found, (
        f"skills/temper/SKILL.md's Phase C0 section states {found}, which "
        f"turns the roster into a shortlist. fallout ST-007 closes every "
        f"candidate as DRIVEN -- filed or clean -- and the F6 report lists "
        f"each one that is neither, so a candidate excused here is debt the "
        f"report prints anyway."
    )


#: The handler that writes the DRIVEN closure. The HANDLER is what is typed
#: here and the TOOL name is what is derived from it, which is the way round
#: the pin needs: looking the tool up by its own name would type the very
#: string the assertion below exists to recover. The dispatch entries are
#: lambdas that call their handler by global name, so the handler's name
#: locates the entry and the entry yields the tool's.
_CLOSURE_HANDLER = "foundry_drive_temper_candidate"

#: fallout NFR-011 in the `_PYTEST_DISCOVERY_PHRASE` shape, one door out from a
#: vocabulary: the call `skills/temper/SKILL.md` must NAME, recovered from the
#: registration in `foundry_mcp/server.py`. Rename the door and this
#: expectation moves with it, so prose still teaching the old spelling fails
#: HERE rather than sending a TEMPER pass to a tool the boundary does not
#: carry.
_CANDIDATE_CLOSURE_TOOLS = tuple(
    name
    for name, dispatch in sorted(foundry_server._DISPATCH.items())
    if _CLOSURE_HANDLER in getattr(getattr(dispatch, "__code__", None), "co_names", ())
)


def test_the_candidate_closure_tool_is_recovered_from_its_registration() -> None:
    """Floor check for the pin below: a derivation finding zero doors or two
    would leave the prose assertion vacuous or ambiguous rather than red."""
    assert len(_CANDIDATE_CLOSURE_TOOLS) == 1, (
        f"{_CLOSURE_HANDLER} is reached by {list(_CANDIDATE_CLOSURE_TOOLS)} "
        f"dispatch entr(y/ies). The pin below tells the temper skill to name "
        f"ONE call: zero means the closure has no door at all and the prose "
        f"would teach a tool no run can reach, and two means TEMPER is taught "
        f"one of several names the same closure answers to."
    )


def test_the_temper_roster_section_names_the_call_that_closes_a_candidate() -> None:
    """fallout AC-019 / ST-007 / GI-027 -- the WRITE half of the roster rule.

    D-092. Phase C0 opened with the roster READ and stated the closure
    obligation -- every candidate closed as DRIVEN, filed or clean -- and named
    the reader that lists the ones left open, but named no writer anywhere. The
    agent was handed the obligation and not the call that discharges it, so the
    DRIVEN half of fallout ST-007 rested on it guessing a tool name that
    appeared in no file it loads: the routing existed only in
    `commands/start.md`'s LEAD tool table and in the tool's own MCP
    description, and a TEMPER agent reads neither.

    Both the tool and its arguments are recovered from the registration, never
    typed, so a door renamed or a required field added fails here beside the
    prose that still teaches the old shape. The tokens are matched BACKTICKED
    deliberately: `filed` bare is a substring of "filed a finding against" two
    paragraphs up, and a pin that its own section satisfies by accident is a
    pin that would have stayed green through D-092.
    """
    (tool,) = _CANDIDATE_CLOSURE_TOOLS
    schema = {t.name: t for t in asyncio.run(foundry_server.list_tools())}[
        tool
    ].inputSchema
    required = sorted(schema["required"])
    omittable = sorted(set(schema["properties"]) - set(required))
    assert required and omittable, schema

    section = _section(TEMPER_SKILL, "## Phase C0: ROSTER")
    assert f"`{tool}`" in section, (
        f"skills/temper/SKILL.md's Phase C0 does not name `{tool}`. The "
        f"section states that every candidate is closed as DRIVEN and names "
        f"the report that lists the ones that are not; without the call that "
        f"WRITES the closure the obligation has no discharge in any file "
        f"TEMPER loads, and every driven probe stays UNDRIVEN on the record."
    )
    for argument in (*required, *omittable):
        assert f"`{argument}`" in section, (
            f"skills/temper/SKILL.md's Phase C0 names `{tool}` without naming "
            f"its `{argument}` argument. The door declares "
            f"{required} required and {omittable} omittable, and the "
            f"difference between them is the whole ruling: an omitted "
            f"{omittable} is the CLEAN closure, so a section naming the call "
            f"and not its fields teaches a stream to guess which absence "
            f"means what."
        )


# ---------------------------------------------------------------------------
# fallout AC-048 / FR-025 -- the PROVE half of fallout marking
# ---------------------------------------------------------------------------

# fallout NFR-011, filed as D-095. These three clauses used to live here as a
# SECOND hand-typed tuple under the same name as `test_protocol_prose.py`'s, so
# rewording one left the other passing against a different sentence -- two
# modules pinning one ruling and free to drift, which is the drift fallout
# NFR-011 forbids and the reason the ruling has one home. The import at the top
# of this module is that home: `test_protocol_prose.py` owns the tuple, builds
# its `fallout_of` spelling from the filing door rather than typing it, and
# sweeps the rest of the suite for a third copy -- a sweep that ARMS on this
# import, so deleting it in favour of a local re-type disarms the guard as well
# as re-opening the defect.
#
# What each module owns stays split: casting 6 pins the clauses on
# `agents/tracer.md`, and the parametrisation below pins the same three on the
# three PROVE-side surfaces this module's population covers.


@pytest.mark.parametrize("path", FALLOUT_SURFACES, ids=_rel)
@pytest.mark.parametrize("clause", _FALLOUT_CLAUSES, ids=lambda v: v[:44])
def test_each_prove_side_surface_states_the_fallout_marking_rule(
    path: Path, clause: str
) -> None:
    """fallout AC-048 / FR-025, the three surfaces casting 6 does not own.

    The clauses are the SAME three `test_protocol_prose.py` pins on
    `agents/tracer.md`, quoted here rather than re-worded: the doors report one
    refusal per violation, so four surfaces wording it four ways teach four
    vocabularies and the refusal matches at most one. The prose AROUND each
    clause is each file's own -- and must be, because pasting one paragraph
    into N files was explicitly rejected.
    """
    assert clause in _flat(path), (
        f"{_rel(path)} no longer states: {clause!r}. `measure-run` counts "
        f"records carrying `fallout_of` per cycle, so an unmarked cycle reads "
        f"as a cycle that produced no fallout -- and the measurement this run "
        f"exists to make honest goes quiet instead of going red."
    )


# ---------------------------------------------------------------------------
# fallout AC-031 / FR-023 / FR-049 / GI-016 -- the agent records; the lead
# only confirms
# ---------------------------------------------------------------------------
#
# fallout AC-031 names nine producers and the run splits them across two
# castings: the five non-PROVE stream agents are swept in
# `test_protocol_prose.py`, and these five -- the PROVE agent and the four
# skills -- are swept here. The split is by CASTING, not by rule: both modules
# pin the same ruling, and the clauses below are the ones both halves state
# word-identically, because the doors report ONE refusal per violation and a
# surface that words the rule differently teaches its stream a vocabulary the
# refusal will not use.

_STREAM_RECORDING_CLAUSES = (
    (
        "**You record your own stream; the lead only confirms the record exists.**",
        "fallout GI-016's imperative: the AGENT records. Without it a stream "
        "finishes and waits for a lead whose own imperative is "
        "confirm-the-record-exists, and the cycle's roll-up reads the silence "
        "as no coverage",
    ),
    (
        "A second call for the same stream and cycle REPLACES the first, names in "
        "`replaced` what it replaced, and keeps every record under `records[]`",
        "fallout FR-023 / FR-049's replace semantics, on the agent side of the "
        "door. Without it a stream that widens its sweep and records again "
        "reads its own correction as a doubling, stops re-recording, and the "
        "cycle keeps the narrower number",
    ),
    (
        "`items_total`",
        "the denominator. A record carrying `items_checked` alone reports a "
        "numerator against nothing, and the roll-up cannot tell a stream that "
        "covered 12 of 12 from one that covered 12 of 400",
    ),
    (
        "`findings_count`",
        "the third of the pair's companions, and the one a stream that found "
        "nothing is likeliest to omit -- leaving a clean pass indistinguishable "
        "from a pass that never reported",
    ),
)


@pytest.mark.parametrize("path", STREAM_RECORDING_SURFACES, ids=_rel)
@pytest.mark.parametrize("clause,why", _STREAM_RECORDING_CLAUSES, ids=lambda v: v[:44])
def test_each_prove_side_surface_records_its_own_stream(
    path: Path, clause: str, why: str
) -> None:
    """fallout AC-031 / FR-023 / FR-049 / GI-016, the five surfaces here."""
    assert clause in _flat(path), (
        f"{_rel(path)} no longer states: {clause!r}. That clause is {why}. All "
        f"{len(STREAM_RECORDING_SURFACES)} of this module's producers state "
        f"it, in their own voice around it -- a partial edit that fixes three "
        f"of five fails here naming the two it forgot, which is the whole "
        f"reason the assertion is parametrised rather than written once."
    )


#: Spellings that would put the recording duty back on the lead. Absence
#: assertions, because a positive pin cannot see prose ADDED beside it: a file
#: can state the imperative above and, four bullets later, tell the stream the
#: lead marks it complete -- both true to a substring check, and the stream
#: believes the second one. The family is casting 6's, quoted rather than
#: re-derived so one rewording cannot leave the two halves of one absence
#: guarding different sets of words.
_LEAD_RECORDS_SPELLINGS = (
    "the lead records it",
    "the lead will record",
    "the lead marks the stream",
    "the lead marks this stream",
    "the lead calls `foundry-stream`",
    "the lead records your stream",
)


@pytest.mark.parametrize("path", STREAM_RECORDING_SURFACES, ids=_rel)
def test_no_prove_side_surface_says_the_lead_records_for_it(path: Path) -> None:
    """fallout GI-016 / OT-029, the absence half: no file re-delegates upward."""
    flat = _flat(path).lower()
    found = sorted(s for s in _LEAD_RECORDS_SPELLINGS if s in flat)
    assert not found, (
        f"{_rel(path)} states {found}, which hands the recording duty back to "
        f"the lead. fallout GI-016 puts it on the AGENT and the lead's own "
        f"imperative is confirm-the-record-exists; a file saying otherwise "
        f"produces the double-record the replace semantics were built to end."
    )


# ---------------------------------------------------------------------------
# fallout FR-034 / FR-055 / AC-053 -- the caller a sub-agent's read declares
# ---------------------------------------------------------------------------

#: The sentence itself, taken from the constant and whitespace-flattened to
#: match `_flat`. Casting 2 pins the two SERVER surfaces that carry it (the
#: `Foundry-Next` tool description and the spawn-time protocol block) in
#: tests/orchestration/test_guidance.py; casting 6 pins the five non-PROVE
#: stream agents in test_protocol_prose.py. This is the PROVE-side third,
#: covering the assayer and the verification skills, and it reads the same
#: constant rather than re-typing the sentence -- the `_TEAMS_DOWN_HINT`
#: reason: four spellings of one rule is four chances to drift, and the
#: refusal a stream would eventually read is the server's, not its own file's.
_SUBAGENT_CALLER_SENTENCE = " ".join(guidance.SUBAGENT_CALLER_INSTRUCTION.split())

#: DERIVED from whose prose actually prescribes the read, not typed. C-016
#: settled that the rule binds a file only where the file tells someone to
#: call the door: agents/teammate.md names `Foundry-Next` nowhere and was
#: correctly left alone, so the same key is applied here rather than a roster
#: that would demand a rule about a call a file never makes.
FOUNDRY_NEXT_READING_SURFACES = tuple(
    p for p in (ASSAYER, *VERIFICATION_SKILLS) if "Foundry-Next" in _read(p)
)


def test_the_foundry_next_reading_roster_is_derived() -> None:
    """Floor check: the caller-rule pin below is vacuous on an empty sweep."""
    missing = sorted(
        _rel(p)
        for p in {ASSAYER, PROVE_SKILL, TRACE_SKILL, SIGHT_SKILL, TEMPER_SKILL}
        - set(FOUNDRY_NEXT_READING_SURFACES)
    )
    assert not missing, (
        f"{missing} no longer name `Foundry-Next`, which is how each learns "
        f"the cycle its `Foundry-Stream` record is keyed by. A surface leaves "
        f"this roster by losing that read -- fix the file rather than "
        f"hard-coding the roster here."
    )


@pytest.mark.parametrize("path", FOUNDRY_NEXT_READING_SURFACES, ids=_rel)
def test_a_prove_side_surface_states_the_subagent_caller_rule(path: Path) -> None:
    """fallout FR-034 / FR-055 / AC-053: the argument stated, never defaulted.

    `caller` defaults to the LEAD value at the wire, so a stream that never
    learns the argument exists arms the ordering token the next `Foundry-Gate`
    requires and resets the stall clock every time it orients itself. D-047
    drove it: a grep across `agents/`, `skills/` and `commands/` found ZERO
    files naming `caller` while nine stream surfaces were pinned to take the
    cycle from `Foundry-Next` with nothing beside it.
    """
    assert _SUBAGENT_CALLER_SENTENCE in _flat(path), (
        f"{_rel(path)} tells a sub-agent to read `Foundry-Next` without "
        f"stating the caller argument, so the read takes the lead default and "
        f"moves the lead's protocol on. Quote the constant rather than "
        f"paraphrasing it: {_SUBAGENT_CALLER_SENTENCE!r}"
    )


# ---------------------------------------------------------------------------
# fallout NFR-011 -- the home a prose rule is pinned in is DISCOVERED
# ---------------------------------------------------------------------------

#: This suite's test directory, the population the roster below is drawn from.
_SUITE_TESTS = Path(__file__).resolve().parent

#: Every prose-pin home the suite carries, DISCOVERED rather than listed. D-149
#: found `_BRANCH_CLAUSES` pinned in a module fallout NFR-011 does not name; a
#: third literal name would have gone stale the same way the second did, so the
#: roster is a glob and a fourth population that earns its own home joins it by
#: existing.
PROSE_PIN_MODULES = tuple(
    sorted(_SUITE_TESTS.glob("test_*_prose.py"), key=lambda p: p.name)
)

#: The two homes fallout NFR-011 names by literal. Asserted to be MEMBERS of
#: the discovered roster, never asserted to be the whole of it: generalising
#: the requirement is licensed, contradicting it is not.
_NFR_011_NAMED_MODULES = ("test_protocol_prose.py", "test_lead_prose.py")


def test_the_prose_pin_homes_are_discovered_and_carry_the_two_named_ones() -> None:
    """fallout NFR-011: the roster is a glob, and the requirement's own two
    names are still inside it.

    Floor check for the two assertions below. A glob that matched nothing, or
    that had quietly stopped matching the modules the requirement names, would
    leave both of them passing over an empty or wrong population.
    """
    found = {p.name for p in PROSE_PIN_MODULES}
    missing = sorted(set(_NFR_011_NAMED_MODULES) - found)
    assert not missing, (
        f"{missing} no longer discover into PROSE_PIN_MODULES. fallout "
        f"NFR-011 names them as homes a prose rule may be pinned in; this "
        f"module generalises that list to whatever the glob finds, which is "
        f"only honest while the two it names are among them. A renamed module "
        f"is fixed by renaming it back or by amending the requirement -- not "
        f"by dropping the name from here. Found: {sorted(found)}"
    )
    assert Path(__file__).name in found, (
        f"this module is not in its own discovered roster, so the glob "
        f"{_SUITE_TESTS.name}/test_*_prose.py no longer describes where prose "
        f"rules live. Found: {sorted(found)}"
    )


@pytest.mark.parametrize("path", PINNED_FILES, ids=_rel)
def test_this_module_declares_the_population_it_pins(path: Path) -> None:
    """fallout NFR-011 / D-149: a home states, in full, which files it pins.

    This is what makes a discovered roster usable in place of the two-name
    literal. A reader holding `skills/prove/SKILL.md` and asking which module
    pins its rulings gets an answer by searching the homes for that path --
    which only works if every home spells its population out. The docstring
    said ``skills/{prove,trace,sight,temper}/SKILL.md`` until D-149, and a
    brace-glob answers no such search.
    """
    declared = path.relative_to(FOUNDRY_ROOT).as_posix()
    assert declared in __doc__, (
        f"this module's docstring does not name {declared}, which it pins "
        f"throughout. fallout NFR-011 admits a home outside the two it names "
        f"only while that home DECLARES its "
        f"population; an undeclared file is pinned somewhere no audit of the "
        f"requirement would look, which is the whole of D-149."
    )


@pytest.mark.parametrize(
    "path",
    [p for p in PROSE_PIN_MODULES if p.name != Path(__file__).name],
    ids=lambda p: p.name,
)
def test_no_other_prose_home_carries_a_second_branch_clause_tuple(path: Path) -> None:
    """fallout NFR-011, the one-spelling half: pinned in a home, not in two.

    D-095 is the record of what a second copy costs -- two independent tuples
    of one name, so rewording one surface left the other module green against
    a sentence no file says any more. The remedy D-149 asks for is a rule
    pinned in exactly one discovered home, so the check is for the tuple's
    NAME rather than its interpolated strings: two of the five clauses are
    f-strings over the vocabulary, and a text search for their values would
    match any module that merely mentions a tier.
    """
    assert "_BRANCH_CLAUSES" not in path.read_text(encoding="utf-8"), (
        f"{path.name} carries a second `_BRANCH_CLAUSES`. The AC-018 / AC-058 "
        f"branch is pinned in this module and in no other; a second tuple is "
        f"two spellings of one ruling free to drift apart, which is D-095 "
        f"repeated rather than D-149 closed. Import this module's tuple if a "
        f"second home genuinely needs it."
    )


# ---------------------------------------------------------------------------
# fallout AC-031 / GI-002 -- a skill's tool grant reaches the doors its own
# body sends it to
# ---------------------------------------------------------------------------
#
# D-108 / D-109. `skills/trace/SKILL.md` and `skills/prove/SKILL.md` declared
# `allowed-tools: Read, Grep, Glob, Bash` with `context: fork` while their
# bodies mandated `Foundry-Next`, `Foundry-Defect` and `Foundry-Stream` -- so
# the recording obligation fallout AC-031 puts on each of them was unreachable
# on the file's own stated contract. Both are `user_invocable`, so a direct
# `/foundry:trace` runs on that grant; the F2 streams survived only because
# the tracer and assayer agents that wrap them carry a broader one, which made
# the skill's stated contract and the only path it works on two different
# things.
#
# The grant is not widened to a wildcard. Each skill is a READ-ONLY stream that
# says so in its own body, and the narrow grant is the only machinery behind
# that sentence; the doors added are exactly the ones the body sends the runner
# to, and the run-lifecycle doors stay out.

#: Every door the server registers, read from the dispatch table rather than
#: typed -- the `_PYTEST_DISCOVERY_PHRASE` shape. A door renamed moves this set
#: with it, so a grant left on the old spelling fails HERE rather than sending
#: a stream to a tool the boundary does not carry.
_REGISTERED_DOORS = frozenset(foundry_server._DISPATCH)

#: The run-lifecycle doors a verification stream only ever reads ABOUT. Both
#: skills name all three while describing what the LEAD does with them, and
#: neither is ever told to call one: `Foundry-Phase` transitions the run,
#: `Foundry-Gate` reports a transition's preconditions and `Foundry-Init`
#: writes the run's settings before any stream exists. Granting them to a
#: forked read-only stream would let an INSPECT pass move the phase it is
#: verifying. Subtracting them is what makes the derivation below reproduce
#: D-108's and D-109's own enumerations exactly.
_LIFECYCLE_DOORS = frozenset({"Foundry-Phase", "Foundry-Gate", "Foundry-Init"})

#: The two MCP server aliases every grant must name a door under: the plugin
#: registration and a direct `.mcp.json` one. `agents/spec-test-deriver.md`
#: narrows a verification stream the same way and names both, because a run
#: reaching the server by the other route finds the door ungranted.
_ALIAS_PREFIXES = ("mcp__plugin_foundry_foundry__", "mcp__foundry__")

#: Tools that mutate the working tree. A stream granted one can fix what it was
#: sent to report, which is the first sentence of `skills/trace/SKILL.md`'s own
#: stream paragraph ("DO NOT fix findings") losing its enforcement.
_MUTATION_TOOLS = frozenset({"Write", "Edit", "NotebookEdit", "Agent", "Task"})


def _frontmatter_and_body(path: Path) -> tuple[str, str]:
    """A prose file split at its frontmatter fence.

    The split is load-bearing rather than tidy: a grant entry spells its door
    name in full, so a body scan run over the whole file would find every door
    the grant already names and report a grant that satisfies itself.
    """
    text = _read(path)
    assert text.startswith("---\n"), f"{_rel(path)} opens with no frontmatter fence"
    front, _, body = text[4:].partition("\n---\n")
    assert body, f"{_rel(path)} has an unterminated frontmatter block"
    return front, body


def _declared_grant(path: Path) -> tuple[str, ...] | None:
    """The file's `allowed-tools` entries, or None when it declares no grant.

    None is not an empty grant -- a skill declaring no `allowed-tools` inherits
    the session's tools and excludes nothing, which is why `sight` and `temper`
    are outside the roster below rather than failing it.
    """
    front, _ = _frontmatter_and_body(path)
    for line in front.split("\n"):
        if line.startswith("allowed-tools:"):
            return tuple(
                entry.strip()
                for entry in line.split(":", 1)[1].split(",")
                if entry.strip()
            )
    return None


def _doors_the_body_sends_the_runner_to(path: Path) -> tuple[str, ...]:
    """Registered doors named in the file's body, minus the lifecycle three."""
    _, body = _frontmatter_and_body(path)
    return tuple(
        sorted(
            door
            for door in _REGISTERED_DOORS - _LIFECYCLE_DOORS
            if door in body
        )
    )


#: The skills whose grant can EXCLUDE something, derived. A skill joins by
#: declaring `allowed-tools` at all; `sight` and `temper` declare none and
#: exclude nothing, and their absence here is the evidence D-108 read the two
#: declarations as an error rather than a policy.
GRANT_DECLARING_SKILLS = tuple(
    p for p in VERIFICATION_SKILLS if _declared_grant(p) is not None
)


def test_the_grant_declaring_skill_roster_is_derived() -> None:
    """Floor check: the grant assertions below sweep the two skills that have
    one, and deleting a grant is not how they are made to pass.

    A skill leaves this roster by dropping its `allowed-tools` line, which
    reaches green by widening the grant to everything -- including the doors
    that move the phase and the tools that edit the tree. The two skills are
    named here so that the deletion fails rather than passes.
    """
    rel = {_rel(p) for p in GRANT_DECLARING_SKILLS}
    assert rel == {_rel(PROVE_SKILL), _rel(TRACE_SKILL)}, (
        f"GRANT_DECLARING_SKILLS derived {sorted(rel)}. `prove` and `trace` "
        f"declare a narrow grant and `sight` and `temper` declare none. A "
        f"skill that dropped its declaration widened its grant to the whole "
        f"session -- every lifecycle door and every mutation tool included -- "
        f"and the remedy for an under-granted read-only stream is naming the "
        f"doors it calls, never deleting the line that keeps the rest out."
    )
    assert _LIFECYCLE_DOORS <= _REGISTERED_DOORS, (
        f"{sorted(_LIFECYCLE_DOORS - _REGISTERED_DOORS)} are not registered "
        f"doors. The subtraction they perform is what keeps a lifecycle door "
        f"out of a verification stream's grant; a name the server no longer "
        f"carries subtracts nothing and the exclusion goes quiet."
    )


@pytest.mark.parametrize("path", GRANT_DECLARING_SKILLS, ids=_rel)
def test_a_skill_grants_every_door_its_body_sends_it_to(path: Path) -> None:
    """fallout AC-031 / GI-002 (D-108, D-109): the stated contract and the
    path it works on are the same thing.

    Derived from the body rather than listed, so a clause added later that
    names a new door fails here until the grant catches up -- which is the
    class, not the two instances. `Validate-Report` is in both required sets
    for exactly that reason: both bodies give it a direct imperative and
    neither defect's `Foundry-`-prefixed sweep saw it.
    """
    grant = set(_declared_grant(path) or ())
    required = _doors_the_body_sends_the_runner_to(path)
    assert required, (
        f"{_rel(path)} names no registered door in its body, so this "
        f"assertion is sweeping nothing. A verification skill that names no "
        f"door records no stream, which fallout AC-031 does not permit."
    )
    missing = sorted(
        f"{prefix}{door}"
        for door in required
        for prefix in _ALIAS_PREFIXES
        if f"{prefix}{door}" not in grant
    )
    assert not missing, (
        f"{_rel(path)} declares `allowed-tools` and omits {missing}. Its own "
        f"body sends the runner to {list(required)}, and `user_invocable` "
        f"means a direct invocation runs on this grant and nothing else -- so "
        f"an omitted door is a stream that cannot record itself, file a "
        f"finding, or read its own width. Both aliases are required: a run "
        f"reaching the server through the other registration finds the door "
        f"ungranted and fails the same way."
    )


@pytest.mark.parametrize("path", GRANT_DECLARING_SKILLS, ids=_rel)
def test_a_skill_grant_stays_read_only_and_stays_a_list(path: Path) -> None:
    """The absence half of D-108's remedy: widen the grant, do not open it.

    Positive assertions cannot see the shape this guards -- a grant rewritten
    to a wildcard satisfies every door check above while re-admitting the
    lifecycle doors and the mutation tools the narrow grant existed to keep
    out. The read-only property is the skills' own: `skills/trace/SKILL.md`
    opens its stream paragraph with "DO NOT fix findings. DO NOT spawn agents".
    """
    grant = set(_declared_grant(path) or ())
    wildcards = sorted(entry for entry in grant if "*" in entry)
    assert not wildcards, (
        f"{_rel(path)}'s grant carries {wildcards}. A wildcard grants every "
        f"door the server registers, `Foundry-Phase` included, so an INSPECT "
        f"stream could transition the run it is verifying. Name the doors the "
        f"body sends the runner to instead."
    )
    forbidden = sorted(
        entry
        for entry in grant
        if entry in _MUTATION_TOOLS
        or any(entry == f"{prefix}{door}" for prefix in _ALIAS_PREFIXES for door in _LIFECYCLE_DOORS)
    )
    assert not forbidden, (
        f"{_rel(path)}'s grant carries {forbidden}. This skill is a read-only "
        f"verification stream that says so in its own body; a grant carrying "
        f"a mutation tool lets it fix what it was sent to report, and one "
        f"carrying a lifecycle door lets it move the phase it is verifying."
    )
