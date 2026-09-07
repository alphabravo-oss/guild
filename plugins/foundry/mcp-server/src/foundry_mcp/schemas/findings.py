"""Built-in JSON schemas for foundry report validation (FR-013).

These are the schemas the `Validate-Report` MCP tool serves. Two shipped
skills instruct a stream to call it on its own report:

    skills/trace/SKILL.md:255  Validate-Report with schema_name: "trace"
    skills/prove/SKILL.md:209  Validate-Report with schema_name: "prove"

so a schema here that disagrees with the shape those same files document is
a validator telling a stream its own documented output is invalid.

WHY THIS MODULE DERIVES FROM vocab.py
-------------------------------------
It did not, and that was D-071. FR-013 reconciled the closed vocabularies
across six re-typed copies; this file was a seventh the survey missed. It
still REQUIRED the `severity` axis the effort abolished, still required a
`category` axis nothing emits, and its `additionalProperties: False` rejected
every axis the effort added — so an instance conforming exactly to the block
skills/trace/SKILL.md documents produced six validation errors against
SCHEMAS["trace"]. The severity half was the serious part: the R1.5 research
finding names severity tiers as the practice most often gamed, which is why
the effort replaced them with the DEFECT/OBSERVATION channel, and a schema
that requires a stream to stamp critical/major/minor before its report
validates reintroduces the tier at the one surface a stream is told to call.

Every enum and every id pattern below therefore comes from
`foundry_mcp.schemas.vocab`. Nothing here re-declares a vocabulary. Adding a
DEFECT_TYPES member or a finding-id prefix is one edit, in vocab.py.

THE DERIVATION RULE FOR `required`
----------------------------------
The skills' own ```json blocks are the source for shape, but they are shape
DOCUMENTATION, not validators: they carry no `additionalProperties`, and they
declare `required` only at the levels their author thought about. So the rule
is not "copy the block" but:

    accept everything the documented block describes,
    and close the vocabulary the effort reconciled.

Concretely, each `required` set below is what that skill's own block requires.
That is why TRACE's summary requires five fields and PROVE's requires none —
PROVE's block declares no summary `required`, and inventing one here would be
this module drifting from the document again, in the other direction. The two
root containers stay required because both pre-D-071 schemas required them.

`additionalProperties: False` on the finding item is load-bearing and stays:
it is what makes "there is no severity field, and adding one is a vocabulary
violation" (stated in both skills) enforceable at the validator rather than
only in prose. tests/test_findings_schemas.py drives that both ways.

WHY `tier` IS NOT THE ABOLISHED AXIS RETURNING (GI-001 / AC-009)
----------------------------------------------------------------
GI-001 widens this item with an optional `tier`, and the obvious objection is
that a closed enum on a finding is exactly the shape D-041 removed. It is not,
and the distinction is the whole point of the axis:

    the removed axis graded HOW MUCH A DEFECT MATTERED, and its failure mode
    was a real defect written down as "minor" and never fixed;
    `tier` records WHAT THE FILING STREAM DID — drove the door and saw the
    wrong result (LIVE), looked and found nothing (LATENT), or drove a probe
    of its own devising and saw the wrong result (HARDENING).

Every tier is a defect, every one gets fixed, and none of them is an excuse to
leave one open. The tier decides only which GATE a still-open instance blocks.
A stream cannot use it to downgrade its own finding, because the value is a
claim about evidence it is answerable for — a LATENT or HARDENING filing
without a `reproduction_attempted` statement is REFUSED at the door (CT-001),
which no grade ever was.

fallout D-171 — THIS PARAGRAPH IS WHY THE ENUM BEING DERIVED IS NOT ENOUGH.
It enumerated the LIVE and LATENT cases only and closed by calling them a
pair, while `tier`'s enum below has been `sorted(vocab.DEFECT_TIERS)` — three
members — since HARDENING landed. That is the D-093 shape exactly, in the
module whose entire job is to keep this validator from drifting from the
documents it derives from: the enum was already derived and the PROSE was not.
The `reproduction_attempted` description was stale in the same way, scoped to
LATENT alone when both doors refuse a HARDENING filing without one, which is
the half D-163 and D-164 found still standing on the PROVE and TRACE skills.

The count is not spelled here any more, and that is deliberate rather than
stylistic. A sentence calling the tiers a pair is a hand-maintained copy of
`len(DEFECT_TIERS)`, and this file is one import away from the frozenset it
was contradicting — the shortest distance in the tree between a vocabulary
and a statement of its size. `tests/test_vocab.py` now sweeps this module for
the retired member-count spellings for that reason, so the retired sentences
are not quoted anywhere in this file: a surface swept for a phrase cannot be
the surface that reproduces it.

`additionalProperties: False` is unchanged, so this widening is additive and
narrows nothing: the abolished axis is still rejected here by name. The
matching widening of `_ALLOWED_ENUM_KEYS` in tests/test_protocol_prose.py
covers the SKILL.md blocks, which this module does not read.

`tier` IS on the finding item's `required` list, beside `class`. It was not
until D-063, and the gap was exactly the one this module exists to close: both
skills' blocks declared the axis optional, this module derives its `required`
list from those blocks, and the result was a validator laxer than the door it
feeds. The blocks moved first and this list followed — the only direction the
derivation rule permits. The full account is recorded beside the list itself.
"""

from __future__ import annotations

from foundry_mcp.schemas import vocab

# ---------------------------------------------------------------------------
# The shared finding record.
#
# trace/SKILL.md and prove/SKILL.md document byte-identical finding shapes
# (only the `id` and `description` prose differ), and temper/SKILL.md:123
# describes the same fields for its T-N findings without shipping a block. One
# object, referenced by all three schemas, is therefore the honest encoding —
# three copies would be the drift this module was just fixed for.
# ---------------------------------------------------------------------------


def _tier_description() -> str:
    """The `tier` property's published description, tiers NAMED from vocab.

    fallout D-171 — this is a WIRE STRING, which is why the stale version
    mattered more than the docstring above it. It spelled out the LIVE and
    LATENT cases, called them a pair and denied being a work-effort grade,
    while the `enum` on the very next line was already `sorted(DEFECT_TIERS)`,
    three members. A client reads this before it ever calls, so the contract
    documented one fewer tier than it accepted — the same D-093 shape
    `server.py` was fixed for, in the module whose entire job is keeping this
    validator from drifting from what it derives from. The retired sentence is
    described rather than quoted here, because this file is swept for it.

    NO PER-MEMBER CLAUSE TABLE HERE, deliberately. `server.py`'s two filing
    doors have one (`_TIER_WIRE_CLAUSES`, pinned member-for-member against
    `DEFECT_TIERS` in `tests/orchestration/test_gates.py`), and a second table
    in this module would agree today and be the next D-093 — it is the exact
    duplication this module exists to prevent. The doors INSTRUCT a stream on
    what evidence to bring; this schema VALIDATES what a skill emitted. So the
    members are named from the frozenset and their per-tier evidence standard
    is left to the door that states it, which means nothing here is counted or
    enumerated by hand and a fourth tier reaches this string unedited.
    """
    return (
        "Evidence tier: what the filing stream is answerable for having done. "
        f"One of {', '.join(sorted(vocab.DEFECT_TIERS))}. Not a work-effort "
        "grade — every tier is a defect and every one gets fixed; the tier "
        "decides only which gate a still-open instance blocks. Foundry-Defect "
        "and Foundry-Sync publish the per-tier evidence standard."
    )


def _reproduction_description() -> str:
    """The `reproduction_attempted` property's description, scope DERIVED.

    fallout D-171 / D-163 / D-164 — this read "Required on a LATENT filing ...
    The server refuses a LATENT filing without one", which is the stale
    LATENT-only scoping found on the PROVE and TRACE skills in the same cycle.
    Both doors refuse a HARDENING filing without one too: a probe nobody asked
    for is trusted on its reproduction and nothing else.

    The scope comes from `vocab.TIERS_OWING_A_REPRODUCTION` rather than from a
    tier name typed here, so the sentence cannot fall behind the obligation
    again — which is precisely how it fell behind the first time.
    """
    tiers = " or ".join(vocab.TIERS_OWING_A_REPRODUCTION)
    return (
        f"Required when tier is {tiers} (CT-001/FR-004): what was driven and "
        "what it found. For a LATENT filing the negative result IS the "
        "evidence (e.g. 'AST sweep of both roots finds 0 sites'); for a "
        "HARDENING filing it is the probe you drove and the wrong result it "
        "produced. The server refuses such a filing without one; "
        "vocab.reproduction_attempted_problem is that check."
    )


_FINDING_ITEM: dict = {
    "type": "object",
    # Verbatim from the `required` list both skills' blocks carry.
    #
    # `class` JOINED that list in D-003 (FR-007/AC-010). Driven: a finding
    # carrying every other required field and no `class` validated cleanly
    # here, and then `validate_defect_filing` REFUSED it -- "Missing class:
    # None is not a non-empty root-cause class name". A validator laxer than
    # the door it feeds tells a stream its report is conforming and lets the
    # filing fail one surface later, which is the drift this module exists to
    # close. Requiring it here covers temper too, whose T-N findings sync
    # through the same door and whose SKILL.md ships no block of its own -- an
    # exemption shaped to fit temper would be the same defect one stream over.
    #
    # WHY `tier` IS ON THIS LIST BESIDE `class` (D-063 / D-039 / FR-004 / AC-009)
    # -----------------------------------------------------------------------
    # It was NOT, until D-063. The recorded reason was the derivation rule
    # above: this list is what the skills' own blocks require, and both blocks
    # declared `tier` an optional property. That reason was sound in the
    # direction it was written -- requiring a field here that the document a
    # stream is handed calls optional makes this module stricter than that
    # document, which is D-071 in the other direction. It was ALSO the reason
    # this list could not be fixed from inside this module.
    #
    # D-063 drove the gap end to end. A finding carrying exactly the keys both
    # blocks list as `required` -- id, classification, type, class, file,
    # symbol, description -- returned {"valid": true, "errors": []} from
    # Validate-Report(schema="prove"), and the SAME finding through
    # Foundry-Sync returned "Refused 1 finding(s) -- no findings were recorded.
    # findings[0].tier: Invalid tier: None". A batch door refuses the WHOLE
    # batch on one bad finding, so a stream that validated its report before
    # sending it lost every finding in it and was told the shape was
    # conforming on the way out. That is the D-003 failure at the second axis,
    # and `class` joined this list for precisely it.
    #
    # So the blocks moved first: skills/prove/SKILL.md and skills/trace/SKILL.md
    # now list `tier` in their `required`, and this list follows them, which is
    # what `test_the_finding_required_list_is_exactly_what_the_documents_require`
    # demands and the reason that pin was written self-correcting.
    #
    # THE ARCHIVE FACT, which is why this is safe rather than merely required.
    # D-039 recorded a second reason for the old split: a tier-less record has
    # no legal value to supply, because `vocab.defect_tier` resolves it to
    # TIER_UNKNOWN and TIER_UNKNOWN is deliberately NOT a DEFECT_TIERS member
    # (FR-051: reading an unclassified record as LATENT silently clears gates).
    # That is a fact about READING a pre-change archive, and nothing here reads
    # one: these schemas validate a report a stream is emitting NOW, against a
    # door that already refuses it untiered. The read side keeps its sentinel
    # -- `test_the_read_side_sentinel_is_not_a_filable_tier` still holds
    # TIER_UNKNOWN outside the enum -- so requiring the axis on a new filing
    # takes nothing away from an old record.
    #
    # `reproduction_attempted` stays OPTIONAL and that is not an oversight: it
    # is required CONDITIONALLY, on a LATENT filing only, which jsonschema
    # cannot express here without a dependency clause that would then be a
    # second copy of a rule `validate_defect_filing` already owns. The door
    # enforces it (CT-001) and refuses naming the field.
    "required": [
        "id", "classification", "type", "class", "tier", "file", "symbol", "description",
    ],
    "properties": {
        "id": {
            "type": "string",
            "pattern": vocab.FINDING_ID_PATTERN,
            "description": (
                "Finding ID, <PREFIX>-<N>. The prefix says which lens produced "
                "the finding and carries no grade; the families are "
                "vocab.FINDING_ID_PREFIXES."
            ),
        },
        "classification": {
            "type": "string",
            # The channel a finding goes down: comment prose to the
            # observations ledger, everything else to the defect ledger. The
            # never-demote denylist (vocab.never_demote_class) overrides it.
            "enum": sorted(vocab.FINDING_CLASSES),
        },
        "type": {
            "type": "string",
            # The FULL DEFECT_TYPES set, MISPLACED included. The skills'
            # advertised enums omit the alias on purpose (an enum offering two
            # spellings of one value invites streams to split on it), but a
            # VALIDATOR that rejects a value the Foundry-Defect boundary
            # accepts is a narrowing, and NFR-002 forbids that. Callers fold
            # the alias with vocab.canonical_defect_type().
            "enum": sorted(vocab.DEFECT_TYPES),
        },
        "class": {
            "type": "string",
            "description": (
                "Required root-cause group, non-empty on every filing and "
                "spelled identically on every instance that shares it. Not a "
                "tier — it is what lets three cycles of one root cause "
                "escalate to a single structural fix. Foundry-Defect and "
                "Foundry-Sync refuse a filing without it, and one classless "
                "finding refuses the whole Foundry-Sync batch."
            ),
        },
        "tier": {
            "type": "string",
            # GI-001 / AC-009 — derived from vocab, never re-typed. The
            # enforcement point below still rejects the abolished work-effort
            # grade by name; this axis is not that axis. See the module
            # docstring's "WHY `tier` IS NOT THE ABOLISHED AXIS" note.
            "enum": sorted(vocab.DEFECT_TIERS),
            "description": _tier_description(),
        },
        "reproduction_attempted": {
            "type": "string",
            "description": _reproduction_description(),
        },
        "file": {
            "type": "string",
            "description": "Bare path. Never carries a line number (FR-004).",
        },
        "symbol": {
            "type": "string",
            "description": "With `file`, the authoritative `path#Symbol` cite.",
        },
        "description": {"type": "string", "minLength": 10},
        "spec_reference": {"type": "string"},
        "suggested_fix": {"type": "string"},
    },
    # See the module docstring: this is the enforcement point for the abolished
    # severity axis. A finding carrying `severity` fails here by name.
    "additionalProperties": False,
}

# The summary fields every stream's roll-up carries. `by_classification` is
# the axis that replaced `by_severity` — it counts on the axis the records
# actually carry, which a severity roll-up no longer could.
_SHARED_SUMMARY_PROPERTIES: dict = {
    "total": {"type": "integer", "minimum": 0},
    "by_classification": {
        "type": "object",
        "additionalProperties": {"type": "integer"},
    },
    "verdict": {"type": "string", "enum": ["PASS", "WARN", "FAIL"]},
}


def _report_schema(summary: dict, **extra_properties: dict) -> dict:
    """A report: a `findings` array of `_FINDING_ITEM`, plus a `summary`.

    Both containers are required — the two pre-D-071 schemas required their
    own two containers, and a report missing either feeds the roll-up nothing.
    `extra_properties` carries a stream's own optional sections (temper's
    per-domain probe table is the only one today).
    """
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "required": ["findings", "summary"],
        "properties": {
            "findings": {"type": "array", "items": _FINDING_ITEM},
            "summary": summary,
            **extra_properties,
        },
    }


# ---------------------------------------------------------------------------
# TRACE — skills/trace/SKILL.md:278.
# ---------------------------------------------------------------------------

TRACE_SCHEMA: dict = _report_schema(
    {
        "type": "object",
        # Verbatim from the block's own summary `required` list.
        "required": ["total", "verdict", "items_checked", "items_total", "findings_count"],
        "properties": {
            **_SHARED_SUMMARY_PROPERTIES,
            "items_checked": {"type": "integer", "minimum": 0},
            "items_total": {"type": "integer", "minimum": 0},
            "findings_count": {"type": "integer", "minimum": 0},
        },
    }
)

# ---------------------------------------------------------------------------
# PROVE — skills/prove/SKILL.md:258.
#
# The pre-D-071 schema rooted this at `verdicts`, a container the prove skill
# has not documented since the reconciliation; the block declares `findings`,
# the same array trace does. No summary `required` here: the block declares
# none, and see the derivation rule in the module docstring.
# ---------------------------------------------------------------------------

PROVE_SCHEMA: dict = _report_schema(
    {
        "type": "object",
        "properties": dict(_SHARED_SUMMARY_PROPERTIES),
    }
)

# ---------------------------------------------------------------------------
# TEMPER — skills/temper/SKILL.md:117 (Phase C4: REPORT).
#
# Temper ships no ```json block, so there is no documented shape to derive
# from; its prose is the source. :123 gives the same finding record ("each
# with `T-N` ID, description, `path#Symbol`, fix direction") and :127 syncs
# them through Foundry-Defect, i.e. across the same reconciled vocabulary. The
# pre-D-071 schema instead required a `domains` array as the ONLY root
# container, which is the per-domain probe table at :122 — real, but a
# supporting section, not the findings the tracker consumes. So `domains` is
# now optional beside the two standard containers, and its status enum is the
# roster :121 actually names rather than the invented SOLID/CRACKED/UNTESTED.
# ---------------------------------------------------------------------------

_TEMPER_DOMAINS: dict = {
    "type": "array",
    "items": {
        "type": "object",
        "required": ["name", "status", "probes"],
        "properties": {
            "name": {"type": "string"},
            "status": {"type": "string", "enum": sorted(vocab.TEMPER_DOMAIN_STATUSES)},
            "probes": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["question", "answer", "pass"],
                    "properties": {
                        "question": {"type": "string"},
                        "answer": {"type": "string"},
                        "pass": {"type": "boolean"},
                        "finding_id": {"type": ["string", "null"]},
                    },
                },
            },
        },
    },
}

TEMPER_SCHEMA: dict = _report_schema(
    {
        "type": "object",
        "properties": {
            **_SHARED_SUMMARY_PROPERTIES,
            "total_domains": {"type": "integer", "minimum": 0},
            "by_status": {
                "type": "object",
                "additionalProperties": {"type": "integer"},
            },
            "suggestions": {"type": "integer", "minimum": 0},
        },
    },
    domains=_TEMPER_DOMAINS,
)

# The registry `tools/validation.py` serves and `server.py`'s `schema_name`
# enum publishes. All three names are load-bearing on that enum — do not drop
# a key here without the matching server.py edit.
SCHEMAS: dict[str, dict] = {
    "trace": TRACE_SCHEMA,
    "prove": PROVE_SCHEMA,
    "temper": TEMPER_SCHEMA,
}
