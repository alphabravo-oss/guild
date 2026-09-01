"""validate_report tool — JSON schema validation for foundry verification reports.

This module is the HANDLER and the DISPLAY behind the `Validate-Report` tool
that two shipped skills instruct every stream to call on its own report:

    skills/trace/SKILL.md:255  Validate-Report with schema_name: "trace"
    skills/prove/SKILL.md:209  Validate-Report with schema_name: "prove"

WHY EVERYTHING HERE DERIVES FROM THE RESOLVED SCHEMA
----------------------------------------------------
It did not, and that was D-083. FR-013 (Locked) requires that "all six copies
(schema, handlers, display, markers, bash twin if kept) read one source of
truth". D-071 reconciled `schemas/findings.py`; this file imported SCHEMAS from
it and then re-declared two vocabularies by hand around it:

  * `_auto_fix` MANUFACTURED a severity tier, mapping `category` values onto
    critical/major/minor and reporting that it had done so. The R1.5 research
    finding names severity tiers as the practice most often gamed, which is why
    the effort replaced them with the DEFECT/OBSERVATION channel — and this was
    the one surface a stream is told to call, stamping the abolished axis into
    the stream's own report. Because the finding item is closed
    (`additionalProperties: False`) and declares no `severity`, the "repair"
    also made the report strictly MORE invalid: the additional-property error
    grew from naming ('category', 'line') to naming ('category', 'line',
    'severity'). It was not dead code — FR-021/AC-026 make pre-change archives
    a supported input, and a pre-change trace report is what carries `category`.

  * `_compute_stats` rolled trace findings up `by_severity` and `by_category`,
    two axes no conforming report carries, so both could only ever read
    {"unknown": N}; and its prove branch counted a root `verdicts` container
    when PROVE_SCHEMA's container is `findings`, so a VALID prove report
    reported zero of everything — a display reading a key nothing writes.

So nothing below names a report field, a container, or a roll-up axis. The
schema `validate_report` already resolved — built-in or custom — is walked for
all three:

    containers   root properties declaring an array of objects
    repairs      only on containers the schema CLOSES, and only by removing
                 keys it does not declare
    roll-ups     one per `by_<axis>` the schema's own `summary` declares,
                 counted over the container whose item declares `<axis>`

Adding an axis is therefore one edit, in `schemas/findings.py`, and this file
follows it. Reporting the roll-ups the SUMMARY declares is also what makes the
stats block useful rather than decorative: it is a recount on exactly the axes
the stream's own summary is supposed to carry, so the two can be held against
each other. An axis the summary declares that no container can source is
omitted entirely rather than reported as {"unknown": N} — that shape was the
defect, not a diagnostic.

THE DIRECTION OF A REPAIR (FR-021 / AC-026)
-------------------------------------------
Pre-change archives are a supported input, and a pre-change report is what
carries the retired `severity` / `category` / `line` axes. `auto_fix` therefore
only ever SUBTRACTS: it removes what the vocabulary retired instead of adding
what it abolished, and names every key it drops. It never invents a value, and
it never supplies a missing REQUIRED field — NFR-002 forbids silent coercion,
and a validator that fills in its own required fields is answering its own
question.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path

import jsonschema

from foundry_mcp.parsers.report import extract_last_json
from foundry_mcp.schemas.findings import SCHEMAS


def validate_report(
    report_path: str,
    schema_name: str = "trace",
    schema_path: str | None = None,
    auto_fix: bool = False,
    project_root: str = ".",
) -> dict:
    """Validate a report's JSON block against a schema.

    Args:
        report_path: Path to markdown report file.
        schema_name: Built-in schema name ("trace", "prove", "temper") or "custom" with schema_path.
        schema_path: Custom schema file path (overrides schema_name).
        auto_fix: Drop keys the schema's closed containers do not declare, so a
            pre-change report validates without its retired axes. Only ever
            removes; never adds a field, a value, or a missing required key.
            Every drop is named in `errors` and the repaired document is
            returned as `fixed_json`.
        project_root: Project root for resolving relative paths.

    Returns:
        {valid, errors[], stats{}, fixed_json?}
    """
    root = Path(project_root)
    rpath = root / report_path if not Path(report_path).is_absolute() else Path(report_path)

    if not rpath.exists():
        return {"valid": False, "errors": [f"File not found: {report_path}"], "stats": {}}

    text = rpath.read_text(encoding="utf-8")
    block = extract_last_json(text)

    if block is None:
        return {"valid": False, "errors": ["No JSON block found in report"], "stats": {}}

    # Load schema
    if schema_path:
        spath = root / schema_path if not Path(schema_path).is_absolute() else Path(schema_path)
        schema = json.loads(spath.read_text(encoding="utf-8"))
    elif schema_name in SCHEMAS:
        schema = SCHEMAS[schema_name]
    else:
        return {
            "valid": False,
            "errors": [f"Unknown schema: {schema_name}. Available: {list(SCHEMAS.keys())}"],
            "stats": {},
        }

    data = block.data
    errors: list[str] = []

    # Auto-fix pass
    fixed_json = None
    if auto_fix and isinstance(data, dict):
        data, fix_notes = _auto_fix(data, schema)
        if fix_notes:
            fixed_json = data
            errors.extend(f"[auto-fixed] {n}" for n in fix_notes)

    # Validate
    validator = jsonschema.Draft202012Validator(schema)
    validation_errors = sorted(validator.iter_errors(data), key=lambda e: list(e.path))
    for err in validation_errors:
        path = ".".join(str(p) for p in err.absolute_path)
        errors.append(f"{path}: {err.message}" if path else err.message)

    # Stats
    stats = _compute_stats(data, schema)

    result: dict = {
        "valid": len(validation_errors) == 0,
        "errors": errors,
        "stats": stats,
        "json_block_lines": [block.start_line, block.end_line],
    }
    if fixed_json is not None:
        result["fixed_json"] = fixed_json
    return result


# ---------------------------------------------------------------------------
# Reading the schema.
#
# Both walks below are TOTAL over arbitrary parsed JSON. `schema` can come from
# a caller-supplied `schema_path`, and `data` is whatever was in the report's
# fenced block — a validator that raises on a malformed input has answered the
# question it was asked with a stack trace.
# ---------------------------------------------------------------------------


def _object_array_containers(schema: object) -> dict[str, Mapping]:
    """Root containers the schema declares as an array of objects.

    `{"findings": <item schema>}` for trace and prove; temper adds `"domains"`,
    its per-domain probe table. Declaration order is preserved, which is what
    makes the axis-to-container resolution in `_compute_stats` deterministic
    when two containers declare the same property name.
    """
    if not isinstance(schema, Mapping):
        return {}
    properties = schema.get("properties")
    if not isinstance(properties, Mapping):
        return {}
    containers: dict[str, Mapping] = {}
    for name, node in properties.items():
        if not isinstance(node, Mapping) or node.get("type") != "array":
            continue
        item = node.get("items")
        if isinstance(item, Mapping) and item.get("type") == "object":
            containers[str(name)] = item
    return containers


def _declared_rollup_axes(schema: object) -> list[str]:
    """The `by_<axis>` roll-ups the schema's own `summary` declares.

    `["classification"]` for trace and prove; `["classification", "status"]`
    for temper. This is the derivation D-083 asked for: the axes the report's
    summary is supposed to carry are the axes this tool recounts, so the two
    cannot drift and a stream can hold its self-declared summary against the
    recount.
    """
    if not isinstance(schema, Mapping):
        return []
    properties = schema.get("properties")
    if not isinstance(properties, Mapping):
        return []
    summary = properties.get("summary")
    if not isinstance(summary, Mapping):
        return []
    declared = summary.get("properties")
    if not isinstance(declared, Mapping):
        return []
    return [
        str(name)[len("by_"):]
        for name, node in declared.items()
        if str(name).startswith("by_")
        and str(name) != "by_"
        and isinstance(node, Mapping)
        and node.get("type") == "object"
    ]


def _report_items(data: Mapping, name: str) -> list:
    """The report's `name` container, or [] when it is absent or not an array."""
    raw = data.get(name)
    return raw if isinstance(raw, list) else []


# ---------------------------------------------------------------------------
# The two passes.
# ---------------------------------------------------------------------------


def _auto_fix(data: dict, schema: object) -> tuple[dict, list[str]]:
    """Remove keys the schema's CLOSED containers do not declare.

    The only repair this tool performs, and it only ever subtracts — see the
    module docstring for why the previous direction (synthesizing a severity
    tier out of a retired `category` axis) was D-083.

    A container the schema leaves open — `additionalProperties` anything other
    than False — legitimately carries extra keys, so nothing is removed from
    it. Declared properties survive by construction, required ones included; a
    required field that is MISSING is left missing, for the validator to name.

    Returns (data, notes). `data` is mutated in place, which is what makes the
    validated document and the returned `fixed_json` the same object.
    """
    notes: list[str] = []
    for name, item_schema in _object_array_containers(schema).items():
        if item_schema.get("additionalProperties") is not False:
            continue
        declared = item_schema.get("properties")
        if not isinstance(declared, Mapping):
            continue
        for position, item in enumerate(_report_items(data, name)):
            if not isinstance(item, dict):
                continue
            identifier = item["id"] if isinstance(item.get("id"), str) else position
            for key in [k for k in item if k not in declared]:
                del item[key]
                notes.append(
                    f"Dropped '{key}' from {name}[{identifier}]: the schema "
                    f"closes this object and declares no such property, so the "
                    f"key is what makes the report invalid."
                )
    return data, notes


def _compute_stats(data: object, schema: object) -> dict:
    """Recount the report on the axes the schema declares.

    One total per declared container, then one roll-up per declared
    `by_<axis>`, counted over the first container whose item schema declares
    that axis. An axis nothing can source is omitted rather than reported as
    {"unknown": N}: a summary line with exactly one reachable value is not a
    diagnostic, it is D-083's (B).
    """
    if not isinstance(data, Mapping):
        return {}
    containers = _object_array_containers(schema)
    stats: dict = {
        f"total_{name}": len(_report_items(data, name)) for name in containers
    }
    for axis in _declared_rollup_axes(schema):
        for name, item_schema in containers.items():
            declared = item_schema.get("properties")
            if isinstance(declared, Mapping) and axis in declared:
                stats[f"by_{axis}"] = _count_by(_report_items(data, name), axis)
                break
    return stats


def _count_by(items: list, key: str) -> dict[str, int]:
    """Tally `items` by `key`.

    An entry that is not an object, or whose `key` is absent or not a string,
    tallies under "unknown" — the report is malformed on that axis and the
    validator's own errors name it. Values are counted VERBATIM: an
    unrecognised one is reported as itself and never folded onto a known
    member of the vocabulary (NFR-002).
    """
    counts: dict[str, int] = {}
    for item in items:
        value = item.get(key) if isinstance(item, Mapping) else None
        label = value if isinstance(value, str) else "unknown"
        counts[label] = counts.get(label, 0) + 1
    return counts
