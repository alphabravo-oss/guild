"""D-083 — the HANDLER between the skill and the schema it names.

D-071 reconciled `schemas/findings.py` with the two skills that tell a stream
to validate its own report. It left the handler AROUND that schema —
`tools/validation.py`, which is what the `Validate-Report` tool actually runs —
re-declaring the abolished vocabulary by hand, and SYNTHESIZING the severity
tier live:

  (A) `_auto_fix` mapped a legacy `category` onto critical/major/minor and
      stamped it on the finding. Since the finding item is closed and declares
      no `severity`, the repair made the report strictly MORE invalid.
  (B) `_compute_stats` rolled trace findings up `by_severity` / `by_category`,
      two axes a conforming report never carries, so both read {"unknown": N}.
  (C) its prove branch counted a root `verdicts` container that PROVE_SCHEMA
      does not declare, so a VALID prove report reported zero of everything.

The shipped tests bound the abolition to the wrong files —
test_findings_schemas.py:334 scans `json.dumps(SCHEMAS)` and
test_protocol_prose.py:1086 scans the two SKILL.md files — and neither looks at
the handler between them. This module does, by DRIVING the real
`validate_report`.

WHY THESE PINS ARE NON-CIRCULAR
-------------------------------
`_compute_stats` now derives its roll-up axes from the schema's own
`summary.properties`, so asserting "stats carries the axes findings.py
declares" would compare a constant against itself. Every load-bearing
assertion below is therefore anchored somewhere the handler cannot reach:

  * the report driven through the tool is synthesized from the SKILL.md's own
    ```json block (the D-071 machinery, imported rather than duplicated);
  * the axes it must produce come from that same block's `summary` properties,
    not from `SCHEMAS` and not typed here;
  * the "reads the schema rather than the schema NAME" property is driven on a
    SYNTHETIC schema handed in through `schema_path`, whose vocabulary exists
    nowhere in the codebase.

Reintroducing the severity mapping, or re-keying either pass off `schema_name`,
fails here.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from foundry_mcp.schemas import vocab
from foundry_mcp.schemas.findings import SCHEMAS
from foundry_mcp.tools.validation import validate_report

# The document-reading and instance-synthesizing helpers D-071 established.
# Imported rather than copied: a second synthesizer in the suite would be the
# very drift this defect is about, and these two modules pin the two halves of
# one surface (the schema, and the handler that serves it).
from tests.test_findings_schemas import (
    BLOCK_BEARING_SKILLS,
    _documented_block,
    _documented_report,
    _documented_schema_name,
    _rel,
)


def _write_report(directory: Path, name: str, payload: object) -> str:
    """Write `payload` as the report's fenced JSON block and return its path.

    Reports reach `validate_report` as markdown, not as JSON, so every pin
    below goes through the real `extract_last_json` parse rather than calling
    the private passes directly.
    """
    path = directory / name
    path.write_text(
        f"# Report\n\nProse the tool must ignore.\n\n```json\n"
        f"{json.dumps(payload, indent=2)}\n```\n",
        encoding="utf-8",
    )
    return str(path)


def _documented_rollup_axes(path: Path) -> list[str]:
    """The `by_*` summary keys THIS SKILL's own block declares.

    This is the anchor that makes the stats assertions real. trace/SKILL.md and
    prove/SKILL.md each declare `summary.by_classification` — the axis that
    replaced severity — and the tool's recount has to be on exactly those axes
    to be comparable with the summary the stream wrote.
    """
    block = _documented_block(path)
    summary = block["properties"]["summary"]["properties"]
    axes = sorted(key for key in summary if key.startswith("by_"))
    assert axes, (
        f"{_rel(path)}'s summary block declares no `by_*` roll-up, so this pin "
        f"would assert nothing. The summary is the only record of which axes "
        f"the stream is expected to count on."
    )
    return axes


# ---------------------------------------------------------------------------
# (B) and (C) — the stats block, driven on each skill's own documented report.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("path", BLOCK_BEARING_SKILLS, ids=lambda p: p.parent.name)
def test_the_documented_report_validates_through_the_real_tool(
    path: Path, tmp_path: Path
) -> None:
    """The precondition every stats assertion rests on.

    test_findings_schemas proves the SCHEMA accepts this instance. This proves
    the TOOL does — same instance, through the markdown parse, the auto-fix
    gate and the validator the skill actually invokes.
    """
    name = _documented_schema_name(path)
    report = _documented_report(path, maximal=True)
    result = validate_report(
        _write_report(tmp_path, f"{path.parent.name}-documented.md", report),
        schema_name=name,
    )
    assert result["valid"], {
        "skill": _rel(path),
        "schema_name_the_skill_passes": name,
        "errors": result["errors"],
    }


@pytest.mark.parametrize("path", BLOCK_BEARING_SKILLS, ids=lambda p: p.parent.name)
def test_the_stats_axes_are_the_ones_the_skill_documents(
    path: Path, tmp_path: Path
) -> None:
    """D-083 (B) and (C), inverted.

    The roll-ups the tool reports must be the roll-ups the skill's own summary
    declares. Before the fix trace reported `by_severity` + `by_category` and
    prove reported `by_verdict` + four scalars off a `verdicts` container — in
    both cases axes the document has not named since the reconciliation.
    """
    name = _documented_schema_name(path)
    report = _documented_report(path, maximal=True)
    stats = validate_report(
        _write_report(tmp_path, "report.md", report), schema_name=name
    )["stats"]

    assert sorted(k for k in stats if k.startswith("by_")) == _documented_rollup_axes(
        path
    ), {
        "skill": _rel(path),
        "documented_axes": _documented_rollup_axes(path),
        "reported_stats": stats,
        "why": (
            "The tool recounts the report on axes the stream's own summary "
            "never declares, so the recount cannot be compared with it."
        ),
    }


@pytest.mark.parametrize("path", BLOCK_BEARING_SKILLS, ids=lambda p: p.parent.name)
def test_the_stats_of_a_valid_report_are_meaningful(
    path: Path, tmp_path: Path
) -> None:
    """No 'unknown' axis, and no container that reads zero on a full report.

    (B) was `by_severity: {"unknown": 1}` — an axis whose only reachable value
    is the absence of the field. (C) was `total_verdicts: 0` on a report with
    findings in it. Both are the same bug: a roll-up sourced from a key the
    schema does not declare.
    """
    name = _documented_schema_name(path)
    report = _documented_report(path, maximal=True)
    result = validate_report(
        _write_report(tmp_path, "report.md", report), schema_name=name
    )
    stats = result["stats"]

    assert result["valid"], result["errors"]
    assert stats["total_findings"] == len(report["findings"]) > 0, {
        "skill": _rel(path),
        "findings_in_the_report": len(report["findings"]),
        "stats": stats,
    }
    for key, value in stats.items():
        if not isinstance(value, dict):
            continue
        assert "unknown" not in value, {
            "skill": _rel(path),
            "axis": key,
            "counts": value,
            "why": (
                "Every finding in a conforming report tallied as 'unknown' on "
                "this axis, which means the axis is not a property the schema "
                "declares on the record it is counting."
            ),
        }
    assert stats["by_classification"] and set(stats["by_classification"]) <= set(
        vocab.FINDING_CLASSES
    ), stats


# ---------------------------------------------------------------------------
# (A) — the repair, and its direction.
# ---------------------------------------------------------------------------

#: A pre-change trace report. FR-021/AC-026 make archives like this a supported
#: input, and this is the record shape that carried the retired axes: a
#: `category` (the field `_auto_fix` read to manufacture a tier), a string
#: `line` (the field it coerced), and no `severity` of its own — so anything
#: named severity in the result was produced by the tool.
LEGACY_TRACE_FINDING: dict = {
    "id": "L-1",
    "category": "missing-wiring",
    "file": "src/api/router.ts",
    "line": "42",
    "description": "the handler is never reached from the router",
}
LEGACY_TRACE_REPORT: dict = {
    "findings": [dict(LEGACY_TRACE_FINDING)],
    "summary": {"total": 1, "verdict": "FAIL"},
}


def test_auto_fix_never_manufactures_a_severity_tier(tmp_path: Path) -> None:
    """D-083 (A), driven end to end.

    The R1.5 finding names severity tiers as the practice most often gamed,
    which is why the effort replaced them with the DEFECT/OBSERVATION channel.
    A repair tool that injects the axis at the one surface every stream is told
    to call is the failure that finding predicts, so the assertion is on the
    WHOLE result — errors, stats and fixed_json alike.
    """
    result = validate_report(
        _write_report(tmp_path, "legacy.md", LEGACY_TRACE_REPORT),
        schema_name="trace",
        auto_fix=True,
    )
    serialized = json.dumps(result)
    for banned in ("severity", "critical", "major", "minor"):
        assert banned not in serialized, {
            "banned": banned,
            "result": result,
            "why": (
                "Validate-Report produced the abolished tier from a report "
                "that did not contain it. The vocabulary has no severity axis; "
                "a repair that adds one reinstates it one stream at a time."
            ),
        }


def test_auto_fix_only_ever_subtracts(tmp_path: Path) -> None:
    """The stated contract of the repair, made enforceable.

    Adding is what made the report more invalid; the retired keys are what the
    closed finding item rejects. So the repaired finding must be a SUBSET of
    the one that came in, and must have lost exactly the keys the schema does
    not declare.
    """
    result = validate_report(
        _write_report(tmp_path, "legacy.md", LEGACY_TRACE_REPORT),
        schema_name="trace",
        auto_fix=True,
    )
    repaired = result["fixed_json"]["findings"][0]
    declared = set(
        SCHEMAS["trace"]["properties"]["findings"]["items"]["properties"]
    )

    assert set(repaired) <= set(LEGACY_TRACE_FINDING), {
        "added": sorted(set(repaired) - set(LEGACY_TRACE_FINDING)),
        "why": "auto_fix added a key. The repair only ever removes.",
    }
    assert set(repaired) == set(LEGACY_TRACE_FINDING) & declared, {
        "repaired": sorted(repaired),
        "expected": sorted(set(LEGACY_TRACE_FINDING) & declared),
    }
    dropped = sorted(set(LEGACY_TRACE_FINDING) - set(repaired))
    assert dropped == ["category", "line"], dropped
    for key in dropped:
        assert any(
            f"'{key}'" in error and error.startswith("[auto-fixed]")
            for error in result["errors"]
        ), {
            "silently_dropped": key,
            "errors": result["errors"],
            "why": "A repair the author cannot see is indistinguishable from data loss.",
        }


def test_auto_fix_removes_the_additional_property_error_it_used_to_widen(
    tmp_path: Path,
) -> None:
    """The repair has to actually repair something.

    Before the fix the additional-property error grew from naming
    ('category', 'line') to naming ('category', 'line', 'severity'): the tool
    was moving the report away from validity. Now the error is gone.
    """
    unfixed = validate_report(
        _write_report(tmp_path, "legacy.md", LEGACY_TRACE_REPORT), schema_name="trace"
    )
    fixed = validate_report(
        _write_report(tmp_path, "legacy.md", LEGACY_TRACE_REPORT),
        schema_name="trace",
        auto_fix=True,
    )
    assert any("Additional properties" in e for e in unfixed["errors"]), unfixed
    assert not any("Additional properties" in e for e in fixed["errors"]), fixed
    assert len(_schema_errors(fixed)) < len(_schema_errors(unfixed)), {
        "before": _schema_errors(unfixed),
        "after": _schema_errors(fixed),
    }


def test_auto_fix_does_not_invent_a_missing_required_field(tmp_path: Path) -> None:
    """NFR-002 at the repair surface.

    `classification` and `type` are the two axes the reconciliation ADDED, and
    both are required. A tool that supplies them is choosing a stream's verdict
    for it — the same move as inferring a severity, one field over.
    """
    result = validate_report(
        _write_report(tmp_path, "legacy.md", LEGACY_TRACE_REPORT),
        schema_name="trace",
        auto_fix=True,
    )
    repaired = result["fixed_json"]["findings"][0]
    for required in ("classification", "type", "symbol"):
        assert required not in repaired, {
            "invented": required,
            "value": repaired.get(required),
        }
        assert any(
            f"'{required}' is a required property" in e for e in result["errors"]
        ), result["errors"]


# ---------------------------------------------------------------------------
# Counting, verbatim.
# ---------------------------------------------------------------------------


def test_an_unrecognised_axis_value_is_counted_verbatim(tmp_path: Path) -> None:
    """NFR-002 at the roll-up surface: no silent coercion onto a known member.

    A stream that emits a classification outside the vocabulary must see that
    value in the recount and the rejection in `errors`, not a tidy tally that
    hides it.
    """
    report = {
        "findings": [
            {
                "id": "L-1",
                "classification": "BLOCKER",
                "type": "WRONG",
                "file": "src/x.py",
                "symbol": "x#y",
                "description": "a description past the ten-character floor",
            }
        ],
        "summary": {
            "total": 1,
            "verdict": "FAIL",
            "items_checked": 1,
            "items_total": 1,
            "findings_count": 1,
        },
    }
    result = validate_report(
        _write_report(tmp_path, "odd.md", report), schema_name="trace"
    )
    assert result["valid"] is False
    assert result["stats"]["by_classification"] == {"BLOCKER": 1}, result["stats"]


def test_a_malformed_report_is_counted_without_raising(tmp_path: Path) -> None:
    """The tool is a validator: a malformed input is its subject, not a crash.

    `_auto_fix` and `_compute_stats` both run BEFORE anything has been
    validated, so both walk whatever the fenced block happened to contain.
    """
    result = validate_report(
        _write_report(
            tmp_path,
            "broken.md",
            {"findings": ["not an object", {"id": "L-1"}, 7], "summary": []},
        ),
        schema_name="trace",
        auto_fix=True,
    )
    assert result["valid"] is False
    assert result["stats"]["total_findings"] == 3
    assert result["stats"]["by_classification"] == {"unknown": 3}


# ---------------------------------------------------------------------------
# The derivation itself: the passes read the SCHEMA, not the schema NAME.
# ---------------------------------------------------------------------------

#: A schema whose vocabulary exists nowhere in this codebase, handed in through
#: `schema_path`. Nothing in the handler can special-case it, so every stat and
#: every repair it produces was derived from the document in front of it. This
#: is the pin that fails the moment either pass is re-keyed off `schema_name`.
SYNTHETIC_SCHEMA: dict = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "required": ["widgets"],
    "properties": {
        "widgets": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["name", "colour"],
                "properties": {
                    "name": {"type": "string"},
                    "colour": {"type": "string", "enum": ["teal", "ochre"]},
                },
                "additionalProperties": False,
            },
        },
        "sheds": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"roof": {"type": "string"}},
                # Deliberately OPEN: nothing may be dropped from this one.
                "additionalProperties": True,
            },
        },
        "summary": {
            "type": "object",
            "properties": {
                "by_colour": {"type": "object"},
                "by_roof": {"type": "object"},
                # Declared, but no container carries a `mood` — the roll-up is
                # unsourceable and must be omitted rather than reported empty.
                "by_mood": {"type": "object"},
            },
        },
    },
}


def _synthetic(tmp_path: Path) -> str:
    path = tmp_path / "synthetic.schema.json"
    path.write_text(json.dumps(SYNTHETIC_SCHEMA), encoding="utf-8")
    return str(path)


def test_stats_are_derived_from_whatever_schema_was_resolved(tmp_path: Path) -> None:
    """Containers, totals and roll-ups all come from the schema in hand.

    `by_colour` is sourced from `widgets` and `by_roof` from `sheds` — the
    axis-to-container resolution, which is what (C) got wrong when it counted
    prove findings out of a `verdicts` key. `by_mood` is declared and
    unsourceable, so it is absent: a roll-up that can only read one value is
    the defect, not a diagnostic.
    """
    report = {
        "widgets": [
            {"name": "left", "colour": "teal"},
            {"name": "right", "colour": "teal"},
            {"name": "spare", "colour": "ochre"},
        ],
        "sheds": [{"roof": "tin"}],
        "summary": {},
    }
    stats = validate_report(
        _write_report(tmp_path, "synthetic.md", report),
        schema_name="custom",
        schema_path=_synthetic(tmp_path),
    )["stats"]

    assert stats == {
        "total_widgets": 3,
        "total_sheds": 1,
        "by_colour": {"teal": 2, "ochre": 1},
        "by_roof": {"tin": 1},
    }, stats


def test_auto_fix_respects_whether_the_schema_closes_the_container(
    tmp_path: Path,
) -> None:
    """Removal is gated on `additionalProperties: False`, nothing else.

    An OPEN container legitimately carries extra keys, so dropping them would
    be destroying valid data — the mirror-image of (A)'s mistake. A closed one
    is where an undeclared key is precisely what makes the report invalid.
    """
    report = {
        "widgets": [{"name": "left", "colour": "teal", "weight": 3}],
        "sheds": [{"roof": "tin", "weight": 3}],
        "summary": {},
    }
    result = validate_report(
        _write_report(tmp_path, "synthetic.md", report),
        schema_name="custom",
        schema_path=_synthetic(tmp_path),
        auto_fix=True,
    )
    assert result["fixed_json"]["widgets"][0] == {"name": "left", "colour": "teal"}
    assert result["fixed_json"]["sheds"][0] == {"roof": "tin", "weight": 3}
    assert result["valid"] is True


# ---------------------------------------------------------------------------
# TEMPER — two containers, two axes, one of them not on the findings.
# ---------------------------------------------------------------------------


def test_temper_rolls_each_axis_up_from_its_own_container(tmp_path: Path) -> None:
    """`by_status` is a property of a DOMAIN, not of a finding.

    temper is the built-in schema that proves the axis-to-container resolution
    on real vocabulary: counting `status` over `findings` would have produced
    {"unknown": N}, which is exactly the shape (B) reported.
    """
    report = {
        "findings": [
            {
                "id": "T-1",
                "classification": "DEFECT",
                "type": "HOLLOW",
                "file": "src/auth.py",
                "symbol": "auth#verify",
                "description": "the probe found a stub behind the domain",
            },
            {
                "id": "T-2",
                "classification": "OBSERVATION",
                "type": "THIN",
                "file": "src/auth.py",
                "symbol": "auth#note",
                "description": "the comment above it counts three cases, not four",
            },
        ],
        "summary": {"total": 2, "verdict": "FAIL"},
        "domains": [
            {
                "name": "auth",
                "status": "CRACKED",
                "probes": [{"question": "does it?", "answer": "no", "pass": False}],
            },
            {
                "name": "session",
                "status": "SOLID",
                "probes": [{"question": "does it?", "answer": "yes", "pass": True}],
            },
        ],
    }
    result = validate_report(
        _write_report(tmp_path, "temper.md", report), schema_name="temper"
    )
    assert result["valid"], result["errors"]
    assert result["stats"] == {
        "total_findings": 2,
        "total_domains": 2,
        "by_classification": {"DEFECT": 1, "OBSERVATION": 1},
        "by_status": {"CRACKED": 1, "SOLID": 1},
    }, result["stats"]
    assert set(result["stats"]["by_status"]) <= set(vocab.TEMPER_DOMAIN_STATUSES)


def _schema_errors(result: dict) -> list[str]:
    """The validator's own errors, with the auto-fix notes filtered out."""
    return [e for e in result["errors"] if not e.startswith("[auto-fixed]")]
