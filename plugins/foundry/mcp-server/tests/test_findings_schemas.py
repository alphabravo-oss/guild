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
    """temper/SKILL.md:123 documents `T-N` findings synced through
    Foundry-Defect, i.e. across the same reconciled vocabulary — and the
    pre-D-071 schema had no `findings` container at all, only `domains`."""
    report = {
        "findings": [
            {
                "id": "T-1",
                "classification": "DEFECT",
                "type": "HOLLOW",
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
