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

_FINDING_ITEM: dict = {
    "type": "object",
    # Verbatim from the `required` list both skills' blocks carry.
    "required": ["id", "classification", "type", "file", "symbol", "description"],
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
                "Optional root-cause group, spelled identically on every "
                "instance that shares it. Not a tier — it is what lets three "
                "cycles of one root cause escalate to a single structural fix."
            ),
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
