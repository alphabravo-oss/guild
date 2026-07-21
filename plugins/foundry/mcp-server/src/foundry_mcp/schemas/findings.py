"""Built-in JSON schemas for foundry report validation."""

from __future__ import annotations

# Schema for TRACE stream findings JSON
TRACE_SCHEMA: dict = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "required": ["findings", "summary"],
    "properties": {
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["id", "category", "description", "file", "severity"],
                "properties": {
                    "id": {"type": "string", "pattern": "^F-\\d+$"},
                    "category": {
                        "type": "string",
                        "enum": [
                            "missing-wiring",
                            "stub-implementation",
                            "dead-code",
                            "incomplete-flow",
                            "spec-drift",
                            "data-inconsistency",
                            "error-handling-gap",
                            "missing-validation",
                            "integration-gap",
                            "other",
                        ],
                    },
                    "description": {"type": "string", "minLength": 10},
                    "file": {"type": "string"},
                    "line": {"type": ["integer", "null"]},
                    "severity": {
                        "type": "string",
                        "enum": ["critical", "major", "minor"],
                    },
                    "spec_ref": {
                        "type": ["string", "null"],
                        "description": "Related spec requirement ID (US-N, FR-N, etc.)",
                    },
                    "fix_hint": {"type": ["string", "null"]},
                },
                "additionalProperties": False,
            },
        },
        "summary": {
            "type": "object",
            "required": ["total_findings", "by_category"],
            "properties": {
                "total_findings": {"type": "integer", "minimum": 0},
                "by_category": {
                    "type": "object",
                    "additionalProperties": {"type": "integer"},
                },
                "by_severity": {
                    "type": "object",
                    "additionalProperties": {"type": "integer"},
                },
                "spec_coverage": {
                    "type": ["string", "null"],
                    "description": "Fraction of spec requirements with findings",
                },
            },
        },
    },
    "additionalProperties": False,
}

# Schema for PROVE stream verdicts JSON
PROVE_SCHEMA: dict = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "required": ["verdicts", "summary"],
    "properties": {
        "verdicts": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["id", "description", "verdict"],
                "properties": {
                    "id": {"type": "string", "pattern": "^VC-\\d+"},
                    "description": {"type": "string"},
                    "verdict": {
                        "type": "string",
                        "enum": [
                            "VERIFIED",
                            "HOLLOW",
                            "PARTIAL",
                            "LETTER-ONLY",
                            "MISSING",
                            "WRONG",
                        ],
                    },
                    "code_refs": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "cited_spec_text": {"type": ["string", "null"]},
                    "reasoning": {"type": ["string", "null"]},
                },
                "additionalProperties": False,
            },
        },
        "summary": {
            "type": "object",
            "required": ["total", "verified", "non_verified"],
            "properties": {
                "total": {"type": "integer", "minimum": 0},
                "verified": {"type": "integer", "minimum": 0},
                "non_verified": {"type": "integer", "minimum": 0},
                "by_verdict": {
                    "type": "object",
                    "additionalProperties": {"type": "integer"},
                },
                "pass_rate": {"type": ["string", "number", "null"]},
            },
        },
    },
    "additionalProperties": False,
}

# Schema for TEMPER probe results
TEMPER_SCHEMA: dict = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "required": ["domains", "summary"],
    "properties": {
        "domains": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["name", "status", "probes"],
                "properties": {
                    "name": {"type": "string"},
                    "status": {
                        "type": "string",
                        "enum": ["SOLID", "CRACKED", "UNTESTED"],
                    },
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
        },
        "summary": {
            "type": "object",
            "required": ["total_domains", "solid", "cracked"],
            "properties": {
                "total_domains": {"type": "integer"},
                "solid": {"type": "integer"},
                "cracked": {"type": "integer"},
                "untested": {"type": "integer"},
            },
        },
    },
    "additionalProperties": False,
}

SCHEMAS: dict[str, dict] = {
    "trace": TRACE_SCHEMA,
    "prove": PROVE_SCHEMA,
    "temper": TEMPER_SCHEMA,
}
