"""D-071 — a skill and the validator it names cannot disagree.

Two shipped skills tell a stream to validate its own report against a
built-in schema:

    skills/trace/SKILL.md:255  Validate-Report with schema_name: "trace"
    skills/prove/SKILL.md:209  Validate-Report with schema_name: "prove"

and each of those same files documents, in its own ```json block, the shape
the stream is supposed to emit. Before this module nothing ever compared the
two. They had drifted completely: an instance conforming EXACTLY to the block
skills/trace/SKILL.md documents produced six errors against SCHEMAS["trace"] —
the schema required the `severity` axis the effort abolished, required a
`category` axis nothing emits, matched ids against a retired `^F-\\d+$`
family, and its `additionalProperties: False` rejected every axis the effort
added.

WHY THE PIN IS INSTANCE-LEVEL
-----------------------------
schemas/findings.py now derives its enums from schemas/vocab.py, so asserting
`shipped.enum == vocab.ENUM` proves nothing — both sides come from the same
constant, and the assertion passes just as happily when the skills document
something else entirely. Every load-bearing test below therefore routes
through the DOCUMENT:

  * the schema under test is looked up by the name the SKILL.md itself
    tells the stream to pass (``_documented_schema_name``), not by a name
    typed here;
  * the instance is synthesized from the SKILL.md's own ```json block
    (``_synthesize``), not hand-written here;
  * the id families come from that block's own `id` description prose.

A future edit to either side that breaks the agreement fails here, in
whichever direction it happens.
"""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path

import jsonschema
import pytest

from foundry_mcp.schemas import vocab
from foundry_mcp.schemas.findings import SCHEMAS

# tests/test_findings_schemas.py -> [0]=tests, [1]=mcp-server, [2]=foundry,
# [3]=plugins, [4]=repo-root. Mirrors test_vocab.py's precedent.
REPO_ROOT = Path(__file__).resolve().parents[4]
SKILLS = REPO_ROOT / "plugins" / "foundry" / "skills"

TRACE_SKILL = SKILLS / "trace" / "SKILL.md"
PROVE_SKILL = SKILLS / "prove" / "SKILL.md"
TEMPER_SKILL = SKILLS / "temper" / "SKILL.md"

#: The skills that ship a machine-readable findings block AND name the schema
#: it is validated against. temper/SKILL.md ships neither, so it is pinned
#: against its prose further down instead of being silently dropped.
BLOCK_BEARING_SKILLS = (TRACE_SKILL, PROVE_SKILL)


def _read(path: Path) -> str:
    assert path.is_file(), f"missing: {path}"
    return path.read_text(encoding="utf-8")


def _rel(path: Path) -> str:
    return str(path.relative_to(REPO_ROOT))


# ---------------------------------------------------------------------------
# Reading the document.
# ---------------------------------------------------------------------------


def _documented_block(path: Path) -> dict:
    """Parse the single ```json findings block out of a skill file.

    Mirrors test_protocol_prose._findings_schema (D-048): parsing rather than
    grepping is what lets the assertions below build a real instance, which a
    substring check cannot do at all.
    """
    blocks = re.findall(r"```json\n(.*?)\n```", _read(path), re.S)
    assert len(blocks) == 1, (
        f"{_rel(path)} has {len(blocks)} ```json blocks, expected exactly 1. "
        f"These assertions target the findings block; if the file gained a "
        f"second one, select the right block rather than dropping the pin."
    )
    try:
        return json.loads(blocks[0])
    except json.JSONDecodeError as exc:  # pragma: no cover - fails loudly
        raise AssertionError(
            f"{_rel(path)}'s findings block is not valid JSON ({exc}), so the "
            f"stream it instructs has no shape to conform to."
        ) from exc


_SCHEMA_NAME_RE = re.compile(r'schema_name:\s*[\\"“]*(\w+)[\\"”]*')


def _documented_schema_name(path: Path) -> str:
    """The schema_name THIS file tells its stream to pass to Validate-Report.

    Taking the name from the document is the whole point: it is what makes
    "the skill and the schema it names" a real binding rather than a pairing
    asserted here. D-071's failure was precisely that this hop was never
    checked.
    """
    match = _SCHEMA_NAME_RE.search(_read(path))
    assert match is not None, (
        f"{_rel(path)} no longer names a schema_name for Validate-Report. "
        f"Either the instruction was dropped (and this pin with it), or the "
        f"wording changed — restore the binding rather than deleting the test."
    )
    return match.group(1)


_ID_FAMILY_RE = re.compile(r"\b([A-Z]{1,6})-N\b")


def _documented_id_prefixes(block: dict, path: Path) -> list[str]:
    """The finding-id families the block's own `id` description enumerates."""
    description = (
        block["properties"]["findings"]["items"]["properties"]["id"]
        .get("description", "")
    )
    prefixes = sorted(set(_ID_FAMILY_RE.findall(description)))
    assert prefixes, (
        f"{_rel(path)}'s `id` description ({description!r}) names no <PREFIX>-N "
        f"family, so this pin would validate nothing. The description is the "
        f"only machine-readable record of which ids the stream may emit."
    )
    return prefixes


# ---------------------------------------------------------------------------
# Synthesizing an instance from the document.
# ---------------------------------------------------------------------------


def _keys_to_emit(node: dict, properties: dict, maximal: bool) -> list[str]:
    """Which declared properties this instance carries.

    `maximal` emits every declared property — that is the case D-071 was
    reported on, where `additionalProperties: False` rejected the optional
    axes (`class`, `symbol`, `spec_reference`, `suggested_fix`) the block
    declares. Otherwise emit the block's own `required` list; a node that
    declares NO required list draws no optional/required line at all, so
    there is no smaller honest instance than everything it declares.
    """
    if maximal:
        return list(properties)
    required = node.get("required")
    if not required:
        return list(properties)
    return [k for k in required if k in properties]


def _synthesize(node: dict, *, key: str = "value", maximal: bool) -> object:
    """Build an instance conforming to a documented JSON-Schema node."""
    if isinstance(node.get("enum"), list):
        return sorted(node["enum"])[0]
    node_type = node.get("type")
    if node_type == "object":
        properties = node.get("properties") or {}
        return {
            name: _synthesize(properties[name], key=name, maximal=maximal)
            for name in _keys_to_emit(node, properties, maximal)
        }
    if node_type == "array":
        return [_synthesize(node.get("items") or {}, key=key, maximal=maximal)]
    if node_type in ("integer", "number"):
        return 0
    if node_type == "boolean":
        return True
    # Long enough to clear the shipped `description` minLength of 10, which
    # every pre-D-071 schema also carried.
    return f"synthesized {key} for the documented-shape pin"


def _documented_report(path: Path, *, maximal: bool) -> dict:
    """A full report instance built from `path`'s own documented block.

    The findings array is expanded to one entry per id family the block
    enumerates, so a shipped pattern that covers three of four families fails
    here naming the fourth.
    """
    block = _documented_block(path)
    report = _synthesize(block, maximal=maximal)
    assert isinstance(report, dict) and "findings" in report, (
        f"{_rel(path)}'s block no longer describes a `findings` container; "
        f"the tracker consumes that array, so its name is load-bearing."
    )
    template = report["findings"][0]
    report["findings"] = [
        {**template, "id": f"{prefix}-{n}"}
        for n, prefix in enumerate(_documented_id_prefixes(block, path), start=1)
    ]
    return report


def _errors(schema: dict, instance: object) -> list[str]:
    validator = jsonschema.Draft202012Validator(schema)
    return [
        f"{'.'.join(str(p) for p in e.absolute_path) or '<root>'}: {e.message}"
        for e in sorted(validator.iter_errors(instance), key=lambda e: list(e.path))
    ]


# ---------------------------------------------------------------------------
# The pin: a skill's documented output validates against the schema it names.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("path", BLOCK_BEARING_SKILLS, ids=lambda p: p.parent.name)
def test_the_skill_names_a_schema_the_registry_actually_serves(path: Path) -> None:
    """The first half of D-071: the name has to resolve at all."""
    name = _documented_schema_name(path)
    assert name in SCHEMAS, (
        f"{_rel(path)} tells its stream to pass schema_name={name!r}, but "
        f"SCHEMAS serves {sorted(SCHEMAS)}. validate_report answers an "
        f"unknown name with 'Unknown schema', so the instruction is dead."
    )


@pytest.mark.parametrize("path", BLOCK_BEARING_SKILLS, ids=lambda p: p.parent.name)
def test_the_fully_documented_report_validates(path: Path) -> None:
    """D-071's DRIVEN case, inverted.

    Every property the skill's own block declares, on one finding per id
    family it enumerates. This is the instance PROVE drove six errors out of.
    """
    name = _documented_schema_name(path)
    report = _documented_report(path, maximal=True)
    errors = _errors(SCHEMAS[name], report)
    assert not errors, {
        "skill": _rel(path),
        "schema_name_the_skill_passes": name,
        "errors": errors,
        "why": (
            "The skill documents this exact shape and then tells the stream "
            "to validate it against this exact schema. Every error here is "
            "the validator rejecting its own caller's documented output."
        ),
    }


@pytest.mark.parametrize("path", BLOCK_BEARING_SKILLS, ids=lambda p: p.parent.name)
def test_the_minimally_documented_report_validates(path: Path) -> None:
    """The other direction: the schema must not require what the block omits.

    A stream emitting only the block's `required` fields is conforming. A
    shipped `required` set larger than the documented one rejects it — which
    is exactly how `severity` and `category` became mandatory.
    """
    name = _documented_schema_name(path)
    report = _documented_report(path, maximal=False)
    errors = _errors(SCHEMAS[name], report)
    assert not errors, {
        "skill": _rel(path),
        "schema_name_the_skill_passes": name,
        "errors": errors,
        "why": (
            "The shipped schema requires a field the skill's block treats as "
            "optional or never mentions, so a minimal conforming report is "
            "rejected."
        ),
    }


@pytest.mark.parametrize("path", BLOCK_BEARING_SKILLS, ids=lambda p: p.parent.name)
def test_the_schema_accepts_every_type_the_skill_advertises(path: Path) -> None:
    """NFR-002, through the document rather than through vocab.

    The skills advertise DEFECT_TYPES minus the alias spellings; the shipped
    schema carries the full set. A validator narrower than what the skill
    tells a stream it may emit refuses a legal finding at the one surface the
    stream is instructed to call.
    """
    block = _documented_block(path)
    advertised = frozenset(
        block["properties"]["findings"]["items"]["properties"]["type"]["enum"]
    )
    shipped = frozenset(
        SCHEMAS[_documented_schema_name(path)]["properties"]["findings"]["items"][
            "properties"
        ]["type"]["enum"]
    )
    assert advertised <= shipped, {
        "skill": _rel(path),
        "advertised_but_the_validator_rejects": sorted(advertised - shipped),
    }
    assert advertised, f"{_rel(path)} advertises an empty `type` enum."


@pytest.mark.parametrize("path", BLOCK_BEARING_SKILLS, ids=lambda p: p.parent.name)
def test_the_classification_channel_agrees_exactly(path: Path) -> None:
    """Equality, not containment: a channel nothing reads is as wrong as a
    missing one — a stream with nowhere to file comment prose files it as a
    defect, and a fourth channel routes findings into a ledger that has no
    reader."""
    block = _documented_block(path)
    advertised = frozenset(
        block["properties"]["findings"]["items"]["properties"]["classification"]["enum"]
    )
    shipped = frozenset(
        SCHEMAS[_documented_schema_name(path)]["properties"]["findings"]["items"][
            "properties"
        ]["classification"]["enum"]
    )
    assert advertised == shipped == frozenset(vocab.FINDING_CLASSES), {
        "skill": _rel(path),
        "advertised": sorted(advertised),
        "shipped": sorted(shipped),
        "vocab.FINDING_CLASSES": sorted(vocab.FINDING_CLASSES),
    }


# ---------------------------------------------------------------------------
# The abolished axis.
# ---------------------------------------------------------------------------


def test_no_shipped_schema_mentions_severity_anywhere() -> None:
    """FR-013 / NFR-002 — the axis the effort replaced, gone from the surface.

    Serialized rather than walked, so it also catches the axis returning in a
    summary roll-up key or a description, not only as a finding property.
    """
    serialized = json.dumps(SCHEMAS)
    for banned in ("severity", "by_severity"):
        assert banned not in serialized, (
            f"schemas/findings.py mentions {banned!r}. The R1.5 research "
            f"finding names severity tiers as the practice most often gamed "
            f"(real bugs silently downgraded), which is why the effort "
            f"replaced them with the DEFECT/OBSERVATION channel. A schema "
            f"carrying the tier reinstates it at the one surface a stream is "
            f"told to call."
        )
    assert "by_category" not in serialized, (
        "a summary still rolls up by `category`, an axis no skill emits; the "
        "reconciled axis is `by_classification`."
    )


@pytest.mark.parametrize("name", sorted(SCHEMAS), ids=sorted(SCHEMAS))
def test_a_finding_carrying_severity_is_rejected(name: str) -> None:
    """The prose prohibition, made enforceable.

    Both skills state "there is no severity field, and adding one is a
    vocabulary violation". `additionalProperties: False` on the finding item
    is what turns that sentence into a refusal; this drives it.
    """
    finding = {
        "id": "L-1",
        "classification": "DEFECT",
        "type": "WRONG",
        "class": "EXAMPLE_ROOT_CAUSE",
        "file": "src/example.py",
        "symbol": "example#Thing",
        "description": "a description well past the ten-character floor",
    }
    baseline = _errors(SCHEMAS[name], {"findings": [finding], "summary": {}})
    smuggled = _errors(
        SCHEMAS[name],
        {"findings": [{**finding, "severity": "critical"}], "summary": {}},
    )
    assert len(smuggled) > len(baseline), (
        f"SCHEMAS[{name!r}] accepts a finding carrying `severity`. The tier is "
        f"abolished; a schema that shrugs at it lets the axis back in one "
        f"stream at a time."
    )
    assert any("severity" in e for e in smuggled), (
        f"SCHEMAS[{name!r}] rejects the smuggled finding without naming "
        f"`severity`, so the author cannot tell what was wrong: {smuggled}"
    )


# ---------------------------------------------------------------------------
# Finding ids.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("path", BLOCK_BEARING_SKILLS, ids=lambda p: p.parent.name)
def test_every_documented_id_family_is_accepted(path: Path) -> None:
    """The families come from the block's own `id` description prose."""
    block = _documented_block(path)
    pattern = re.compile(vocab.FINDING_ID_PATTERN)
    for prefix in _documented_id_prefixes(block, path):
        assert pattern.match(f"{prefix}-1"), (
            f"{_rel(path)} tells its stream to emit {prefix}-N ids, but "
            f"vocab.FINDING_ID_PATTERN ({vocab.FINDING_ID_PATTERN}) rejects "
            f"{prefix}-1. This is D-071's `^F-\\d+$` failure, recurring."
        )


@pytest.mark.parametrize(
    "bad_id",
    [
        "1",           # bare number
        "L1",          # no separator
        "L-",          # no ordinal
        "L-x",         # non-numeric ordinal
        "l-1",         # lowercase family
        "finding-one",
        # The pre-effort trace family. Nothing documents F-N since the
        # reconciliation, and carrying it forward would keep a dead family
        # alive inside a closed vocabulary.
        "F-1",
    ],
)
def test_the_id_pattern_still_rejects_a_malformed_id(bad_id: str) -> None:
    """Keeps the widening honest — a pattern that accepts everything would
    pass every assertion above."""
    assert not re.match(vocab.FINDING_ID_PATTERN, bad_id), (
        f"vocab.FINDING_ID_PATTERN accepts {bad_id!r}, so it is no longer "
        f"checking anything."
    )


# ---------------------------------------------------------------------------
# TEMPER — pinned against prose, because it ships no block.
# ---------------------------------------------------------------------------


def test_temper_ships_no_block_so_this_pin_stays_prose_based() -> None:
    """Guards the assumption the temper assertions below rest on.

    If temper ever gains a ```json block it must join BLOCK_BEARING_SKILLS and
    get the real instance-level pin, rather than keeping the weaker prose one.
    """
    assert "```json" not in _read(TEMPER_SKILL), (
        "skills/temper/SKILL.md now ships a ```json block. Add it to "
        "BLOCK_BEARING_SKILLS so it gets the instance-level pin, and make sure "
        "it names a schema_name for Validate-Report."
    )


def test_temper_domain_statuses_are_the_ones_the_skill_counts() -> None:
    """D-071: the shipped enum was SOLID/CRACKED/UNTESTED.

    `UNTESTED` is a status temper never reports, and the three the skill DOES
    report beyond SOLID/CRACKED were missing.
    """
    text = _read(TEMPER_SKILL)
    for status in sorted(vocab.TEMPER_DOMAIN_STATUSES):
        assert status in text, (
            f"vocab.TEMPER_DOMAIN_STATUSES carries {status!r}, which appears "
            f"nowhere in skills/temper/SKILL.md — the skill's prose is the "
            f"only source for this vocabulary."
        )
    shipped = frozenset(
        SCHEMAS["temper"]["properties"]["domains"]["items"]["properties"]["status"][
            "enum"
        ]
    )
    assert shipped == frozenset(vocab.TEMPER_DOMAIN_STATUSES)
    assert "UNTESTED" not in text and "UNTESTED" not in shipped, (
        "UNTESTED is back. Temper's counts line names "
        "SOLID/CRACKED/HOLLOW/MISSING/STUCK; a status the skill never reports "
        "is a schema inventing a state for the loop to sit in."
    )


def test_temper_findings_are_the_reconciled_record() -> None:
    """temper/SKILL.md documents `T-N` findings synced through
    Foundry-Defect, i.e. across the same reconciled vocabulary — and the
    pre-D-071 schema had no `findings` container at all, only `domains`.

    The record carries a `tier` since D-063 made the axis required, and it
    carries one for the reason the shared item's comment already gives: temper
    ships no findings block of its own, so an exemption shaped to fit it would
    be the same defect one stream over. Temper's own prose already tells it to
    set the axis on every finding.
    """
    report = {
        "findings": [
            {
                "id": "T-1",
                "classification": "DEFECT",
                "type": "HOLLOW",
                "class": "STUB_BEHIND_THE_DOMAIN",
                "tier": "LIVE",
                "file": "src/example.py",
                "symbol": "example#probe",
                "description": "the probe found a stub behind the domain",
                "suggested_fix": "FILL OUT",
            }
        ],
        "summary": {"total": 1, "verdict": "FAIL", "total_domains": 1},
        "domains": [
            {
                "name": "auth",
                "status": "CRACKED",
                "probes": [
                    {"question": "does it?", "answer": "no", "pass": False,
                     "finding_id": "T-1"}
                ],
            }
        ],
    }
    assert not _errors(SCHEMAS["temper"], report)


# ---------------------------------------------------------------------------
# The registry and the surface that publishes it.
# ---------------------------------------------------------------------------


def test_schemas_registry_matches_the_published_schema_name_enum() -> None:
    """Read off the running server, mirroring test_vocab._foundry_defect_schema.

    server.py's Validate-Report `schema_name` enum is what a caller picks
    from; SCHEMAS is what the call resolves against. A name on one side and
    not the other is a documented option that cannot work.
    """
    from foundry_mcp import server as foundry_server

    tools = asyncio.run(foundry_server.list_tools())
    tool = next(t for t in tools if t.name == "Validate-Report")
    published = set(tool.inputSchema["properties"]["schema_name"]["enum"])
    # "custom" is the escape hatch handled by schema_path, not a registry key.
    assert published - {"custom"} == set(SCHEMAS), {
        "published_but_unserved": sorted(published - {"custom"} - set(SCHEMAS)),
        "served_but_unpublished": sorted(set(SCHEMAS) - published),
    }


# ---------------------------------------------------------------------------
# GI-001 / AC-009 — the evidence tier, and the axis it is NOT.
#
# GI-001 widens the finding item with `tier`, and the obvious risk is that a
# closed enum on a finding is the abolished work-effort grade wearing a new
# name — `test_skill_schemas_carry_no_enum_outside_the_allowed_axes` in
# tests/test_protocol_prose.py names "tier" as exactly that hazard. So both
# directions are driven here at INSTANCE level, on the same schema, in the same
# file: a finding carrying `tier` validates, and a finding carrying the
# abolished axis is still rejected BY NAME. A widening that lost the second
# half would pass every assertion above.
# ---------------------------------------------------------------------------


def _minimal_finding(**extra: object) -> dict:
    """The eight required fields, plus whatever the caller is testing.

    `class` became the seventh in D-003 and `tier` the eighth in D-063, both
    for the same reason: the filing door refuses a finding without either, so
    a "minimal conforming finding" that omitted one was minimal only against
    the validator and not against the surface the finding is actually bound
    for. A caller testing the tier axis passes its own `tier=` and overrides
    the default here.
    """
    return {
        "id": "L-1",
        "classification": "DEFECT",
        "type": "WRONG",
        "class": "EXAMPLE_ROOT_CAUSE",
        "tier": "LIVE",
        "file": "src/example.py",
        "symbol": "example#Thing",
        "description": "a description well past the ten-character floor",
        **extra,
    }


def _finding_errors(schema: dict, finding: dict) -> list[str]:
    """Validation errors attributable to the FINDING, not to the summary.

    TRACE_SCHEMA's summary requires five fields (verbatim from its skill's own
    block), so a bare ``{"summary": {}}`` draws five errors that say nothing
    about the finding item under test. ``test_a_finding_carrying_severity_is_
    rejected`` works around that by comparing error COUNTS; these assertions
    want "no error at all", so they filter by path instead. Both the property
    errors (``findings.0.tier``) and the closed-set error (``findings.0``) live
    under that prefix.
    """
    return [
        error
        for error in _errors(schema, {"findings": [finding], "summary": {}})
        if error.startswith("findings")
    ]


@pytest.mark.parametrize("name", sorted(SCHEMAS), ids=sorted(SCHEMAS))
@pytest.mark.parametrize("tier", sorted(vocab.DEFECT_TIERS))
def test_a_finding_carrying_a_tier_validates(name: str, tier: str) -> None:
    """AC-009 — the finding schema accepts `tier` on a finding item.

    Driven per schema AND per tier: `additionalProperties: False` rejects an
    undeclared key, so before this widening a stream filing a LATENT gap
    against the validator its own skill tells it to call was refused at the
    one surface it was instructed to use — D-071's exact shape, recurring.
    """
    errors = _finding_errors(SCHEMAS[name], _minimal_finding(tier=tier))
    assert not errors, {"schema": name, "tier": tier, "errors": errors}


@pytest.mark.parametrize("name", sorted(SCHEMAS), ids=sorted(SCHEMAS))
def test_a_latent_finding_may_carry_its_reproduction_statement(name: str) -> None:
    """CT-001 / FR-004 — the statement travels on the finding, so it must fit.

    A LATENT filing is refused at the door without a `reproduction_attempted`
    statement. If the report schema rejected the field, the stream would have
    to strip the very thing the door demands before it could validate.
    """
    errors = _finding_errors(
        SCHEMAS[name],
        _minimal_finding(
            tier="LATENT",
            reproduction_attempted="AST sweep of both roots finds 0 sites",
        ),
    )
    assert not errors, {"schema": name, "errors": errors}


@pytest.mark.parametrize("name", sorted(SCHEMAS), ids=sorted(SCHEMAS))
def test_the_tier_enum_is_closed_over_exactly_the_vocab_members(name: str) -> None:
    """The widening must not be a free-text field wearing an enum's name.

    `unknown` is the interesting rejection: it is a READ-side sentinel for a
    record nobody classified (FR-051), and a schema that accepted it as a
    filed value would hand every stream a legal way to decline the axis.
    """
    shipped = frozenset(
        SCHEMAS[name]["properties"]["findings"]["items"]["properties"]["tier"]["enum"]
    )
    assert shipped == frozenset(vocab.DEFECT_TIERS), sorted(shipped)
    for refused in (vocab.TIER_UNKNOWN, "MINOR", "MAJOR", "P0", "live", ""):
        errors = _finding_errors(SCHEMAS[name], _minimal_finding(tier=refused))
        assert errors, (
            f"SCHEMAS[{name!r}] accepts tier={refused!r}. The tier is a CLOSED "
            f"vocabulary; a validator that shrugs at an unknown value lets the "
            f"abolished grade back in one spelling at a time."
        )


@pytest.mark.parametrize("name", sorted(SCHEMAS), ids=sorted(SCHEMAS))
def test_the_tier_widening_did_not_reopen_the_finding_item(name: str) -> None:
    """AC-009's other half, and the reason this file exists.

    The cheap way to make `tier` validate is to relax `additionalProperties`.
    That would also readmit the abolished axis, so the enforcement point is
    asserted directly rather than only through the `severity` case above: an
    invented key must still be refused BY NAME.
    """
    item = SCHEMAS[name]["properties"]["findings"]["items"]
    assert item["additionalProperties"] is False, (
        f"SCHEMAS[{name!r}]'s finding item no longer closes its property set. "
        f"That flag is what makes 'there is no severity field, and adding one "
        f"is a vocabulary violation' enforceable at the validator."
    )
    errors = _finding_errors(SCHEMAS[name], _minimal_finding(priority="P0"))
    assert any("priority" in e for e in errors), (
        f"SCHEMAS[{name!r}] accepts an undeclared `priority` key — a graded "
        f"axis passes every substring check ever written for the old name: "
        f"{errors}"
    )


def test_the_tier_property_never_mentions_the_abolished_axis() -> None:
    """The prose half, guarded where a well-meaning author would break it.

    ``test_no_shipped_schema_mentions_severity_anywhere`` greps the serialized
    registry, so a `tier` description that explained itself as "replaces
    severity" would fail there with a confusing message. This says why first.
    """
    item = SCHEMAS["trace"]["properties"]["findings"]["items"]["properties"]
    description = item["tier"]["description"].lower()
    assert "severity" not in description, (
        "the tier description names the abolished axis. Describe tier by what "
        "it measures — what the stream DROVE — not by the grade it is not."
    )
    for word in ("live", "latent"):
        assert word in description, (
            f"the tier description does not say what {word.upper()} means, so "
            f"a stream reading the schema cannot tell which value it owes."
        )


def test_tier_is_required_and_reproduction_attempted_is_not() -> None:
    """D-063 — the validator is no longer laxer than the door it feeds.

    THIS TEST USED TO ASSERT THE OPPOSITE. It recorded two reasons `tier`
    could stay optional while `class` was required, and the first of them was
    the one that eventually moved: this module derives its `required` list
    from the skills' own blocks, and both blocks declared `tier` optional. The
    reason was never "tier does not belong here" -- it was "the document has
    not said so yet", and it came with the pin that would fail the day the
    document did.

    Driven, which is why the direction flipped: a finding carrying exactly the
    keys both blocks listed as required validated clean through
    Validate-Report(schema="prove") and was then refused by Foundry-Sync --
    "findings[0].tier: Invalid tier: None" -- which refuses the WHOLE batch. A
    stream that validated its report before sending it lost every finding in
    it, told on the way out that the shape was conforming.

    `reproduction_attempted` stays optional here and that is not the same
    omission. It is required CONDITIONALLY, on a LATENT filing only, and the
    condition lives in `validate_defect_filing` (CT-001). Expressing it here
    would be a second copy of a rule the door already owns -- and a second
    copy is what this module was written to stop.
    """
    for name, schema in SCHEMAS.items():
        required = schema["properties"]["findings"]["items"]["required"]
        assert "tier" in required, (
            f"SCHEMAS[{name!r}] no longer requires `tier` of a finding. The "
            f"filing door does (CT-001, first in its check order), so dropping "
            f"it here makes this validator laxer than the surface every "
            f"finding it validates is bound for -- and one untiered finding "
            f"refuses a whole Foundry-Sync batch."
        )
        assert "reproduction_attempted" not in required, (
            f"SCHEMAS[{name!r}] requires `reproduction_attempted` "
            f"unconditionally. It is owed on a LATENT filing only; demanding "
            f"it of every finding refuses conforming LIVE reports."
        )
        assert not _finding_errors(schema, _minimal_finding()), name


def test_the_read_side_sentinel_is_not_a_filable_tier() -> None:
    """D-039's second reason, as a fact rather than a claim.

    The recorded reason `tier` may stay optional while `class` is required is
    that an untiered finding has no legal value to supply. That holds only
    while TIER_UNKNOWN is outside the enum — put it in and the reason
    evaporates, and with it the read-side guarantee FR-051 rests on (an
    unclassified record blocks like LIVE precisely because no door can write
    `unknown`).
    """
    for name, schema in SCHEMAS.items():
        enum = schema["properties"]["findings"]["items"]["properties"]["tier"]["enum"]
        assert vocab.TIER_UNKNOWN not in enum, (
            f"SCHEMAS[{name!r}] accepts {vocab.TIER_UNKNOWN!r} as a tier. That "
            f"makes the read-side sentinel filable, which CT-001 refuses at the "
            f"door, and removes the reason `tier` is exempt from `required`."
        )
        assert sorted(enum) == sorted(vocab.DEFECT_TIERS), name

    assert vocab.TIER_UNKNOWN not in vocab.DEFECT_TIERS
    assert vocab.defect_tier({}) == vocab.TIER_UNKNOWN, (
        "a record with no tier key must READ as unknown; that is the value the "
        "finding item has no way to accept, which is the whole argument"
    )


def test_the_tier_enum_is_read_from_vocab_and_not_re_typed() -> None:
    """The FR-013 key link, at the one surface D-071 found a seventh copy on.

    Sorted equality against vocab is weak on its own (both sides could be the
    same re-typed literal), so the module source is checked for the derivation
    too — the same shape test_measure_run pins with `is`.
    """
    findings_py = (
        REPO_ROOT / "plugins" / "foundry" / "mcp-server" / "src" / "foundry_mcp"
        / "schemas" / "findings.py"
    )
    source = findings_py.read_text(encoding="utf-8")
    assert "sorted(vocab.DEFECT_TIERS)" in source, (
        "schemas/findings.py re-types the tier enum instead of deriving it "
        "from vocab. That is exactly the drift D-071 found here."
    )
    for literal in ('"LIVE"', "'LIVE'", '"LATENT"', "'LATENT'"):
        assert literal not in source, (
            f"schemas/findings.py spells {literal} as a literal; the tier "
            f"vocabulary is declared once, in schemas/vocab.py"
        )


# ---------------------------------------------------------------------------
# D-003 / FR-007 / AC-010 — the validator and the door agree about `class`.
#
# The finding item mirrors the skills' required list by its own stated
# derivation rule, and all three sites carried "Optional root-cause" while the
# filing door refused a classless filing outright. So a stream emitting
# EXACTLY the shape its own SKILL.md documents validated here and was then
# refused one surface later, with nothing in either document to warn it.
#
# The pin is therefore CROSS-SURFACE and instance-level: one finding, driven
# through both the validator and the door, asserting they answer the same way.
# Asserting only that "class" is in `required` would pass just as happily if
# the door were the thing that drifted.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", sorted(SCHEMAS), ids=sorted(SCHEMAS))
def test_a_classless_finding_is_refused_by_the_validator_and_the_door(
    name: str,
) -> None:
    """D-003, driven on both surfaces at once."""
    from foundry_mcp.tools.foundry import validate_defect_filing

    classless = _minimal_finding(tier="LIVE")
    classless.pop("class")

    errors = _finding_errors(SCHEMAS[name], classless)
    assert any("class" in error for error in errors), {
        "schema": name,
        "errors": errors,
        "why": (
            "SCHEMAS[%r] accepts a finding with no `class`, but "
            "validate_defect_filing refuses it. A validator laxer than the "
            "door it feeds tells a stream its report conforms and lets the "
            "filing fail later, where nothing connects the refusal back to "
            "the block the stream was reading." % name
        ),
    }

    refusal = validate_defect_filing(classless)
    assert refusal is not None and refusal.get("field") == "class", {
        "schema": name,
        "refusal": refusal,
        "why": (
            "the filing door stopped refusing a classless finding. If the "
            "door relaxed deliberately, this schema's `required` list and "
            "both skills' blocks relax with it -- never one side alone."
        ),
    }


@pytest.mark.parametrize("name", sorted(SCHEMAS), ids=sorted(SCHEMAS))
def test_the_classed_finding_both_surfaces_accept_is_the_same_instance(
    name: str,
) -> None:
    """The other direction: agreement, not merely matched strictness.

    Two surfaces can both refuse an instance for unrelated reasons. This says
    the shape a stream is told to emit is accepted by BOTH.
    """
    from foundry_mcp.tools.foundry import validate_defect_filing

    finding = _minimal_finding(tier="LIVE")
    assert not _finding_errors(SCHEMAS[name], finding), {
        "schema": name,
        "errors": _finding_errors(SCHEMAS[name], finding),
    }
    assert validate_defect_filing(finding) is None, validate_defect_filing(finding)


@pytest.mark.parametrize("path", BLOCK_BEARING_SKILLS, ids=lambda p: p.parent.name)
def test_no_skill_block_still_calls_the_class_axis_optional(path: Path) -> None:
    """The prose half of D-003, at the two documents streams actually read.

    ``test_skill_schemas_require_the_new_axes`` in tests/test_protocol_prose.py
    pins the `required` list as a substring; this pins the DESCRIPTION beside
    it, parsed out of the block, so a required-but-still-described-as-optional
    field fails here naming the contradiction rather than passing both checks.
    """
    block = _documented_block(path)
    item = block["properties"]["findings"]["items"]
    description = item["properties"]["class"].get("description", "")
    assert "class" in item.get("required", []), {
        "skill": _rel(path),
        "required": item.get("required"),
        "why": "the block leaves `class` optional; the filing door requires it.",
    }
    assert "optional" not in description.lower(), {
        "skill": _rel(path),
        "description": description,
        "why": (
            "the block requires `class` and then describes it as optional. A "
            "stream resolves that contradiction in its own favour, which is "
            "how the axis went missing from filings in the first place."
        ),
    }
