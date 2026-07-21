"""Regression tests for Foundry-Validate-Castings dimension 4 key_links / artifacts
string type-guard (Casting C4 — FR-006 / CT-003 / NFR-001).

Background
----------
Dimension 4 of ``foundry_validate_castings`` reads each casting's
``must_haves.key_links[].from/.to`` and ``must_haves.artifacts[].path``. The
reads assumed every entry is a dict. When Decompose emitted a plain STRING
entry (a free-text link/artifact description), ``str.get(...)`` raised
``AttributeError: 'str' object has no attribute 'get'`` and the whole
10-dimension report failed to render at F0.9 with an uncaught traceback.

The fix guards each read with ``isinstance(...)``:
  - a dict entry behaves EXACTLY as before (from/to / path extracted), and
  - a string entry is accepted as a plain description and recorded as a
    NON-BLOCKING warning (severity "warning") that names the casting and the
    offending entry — never a traceback, never a hard block.

Each test below maps to one acceptance criterion (NFR-001: one regression test
per acceptance criterion).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from foundry_mcp.tools.foundry_state import (
    ARCHIVE_DIR,
    clear_active_run,
    set_active_run,
)
from foundry_mcp.tools.foundry_validate import foundry_validate_castings


# ── Harness ───────────────────────────────────────────────────────────────
def _run_validate(project_root: Path, castings: list[dict], *, spec_text: str = "") -> dict:
    """Write a minimal run (manifest.json + spec.md) under an active run name
    and invoke ``foundry_validate_castings`` against it.

    ``spec_text`` defaults to empty (no requirement IDs, no File Change Map)
    so dimensions 1 and 10 stay clean and the tests isolate dimension 4.
    """
    run_name = "c4-key-links-test"
    fdir = project_root / ARCHIVE_DIR / run_name
    (fdir / "castings").mkdir(parents=True, exist_ok=True)
    (fdir / "castings" / "manifest.json").write_text(
        json.dumps({"castings": castings, "spec_type": "GREENFIELD"}),
        encoding="utf-8",
    )
    (fdir / "spec.md").write_text(spec_text, encoding="utf-8")

    set_active_run(run_name)
    try:
        return foundry_validate_castings(str(project_root))
    finally:
        clear_active_run()


def _casting(cid: str, *, key_links, artifacts=None, truths=None) -> dict:
    """Build a single casting dict with the given must_haves shapes."""
    return {
        "id": cid,
        "title": f"Casting {cid}",
        "spec_text": "",
        "observable_truths": truths
        or ["user sees X", "user sees Y", "user sees Z"],
        "key_files": [f"src/{cid}.ts"],
        "must_haves": {
            "truths": ["does the thing"],
            "artifacts": artifacts if artifacts is not None else [{"path": f"src/{cid}.ts"}],
            "key_links": key_links,
        },
    }


def _dim4(result: dict) -> dict:
    return result["dimensions"]["key_links_planned"]


# ── AC 1 (FR-006/CT-003): a string key_links entry no longer crashes ───────
def test_string_key_links_entry_does_not_raise_attribute_error(tmp_path: Path):
    """A STRING key_links entry (previously ``AttributeError: 'str' object has
    no attribute 'get'``) must produce a rendered report, not a traceback."""
    castings = [_casting("C1", key_links=["LoginForm -> /api/login"])]

    # Must not raise — the whole point of the guard.
    result = _run_validate(tmp_path, castings)

    assert isinstance(result, dict)
    assert "dimensions" in result
    assert "key_links_planned" in result["dimensions"]


# ── AC 2 (FR-006/CT-003): non-blocking warning names casting + entry ───────
def test_string_key_links_entry_emits_warning_naming_casting_and_entry(tmp_path: Path):
    """The string entry is surfaced as a severity=='warning' issue that names
    both the casting and the offending entry text."""
    entry = "LoginForm -> /api/login"
    castings = [_casting("C1", key_links=[entry])]

    result = _run_validate(tmp_path, castings)
    dim4 = _dim4(result)

    warnings = [i for i in dim4["issues"] if i.get("severity") == "warning"]
    assert len(warnings) == 1, dim4["issues"]
    w = warnings[0]
    # Names the casting (both as a structured field and inline in the message)
    assert w["casting"] == "C1"
    assert "C1" in w["issue"]
    # Names the offending entry text
    assert entry in w["issue"]
    # Dimension records the warning count.
    assert dim4.get("warnings") == 1

    # A non-blocking, warning-severity summary is also surfaced at the top level.
    top = [
        i for i in result["issues"]
        if i.get("dimension") == "key_links_planned" and i.get("severity") == "warning"
    ]
    assert len(top) == 1


# ── AC 3 (FR-006/CT-003): non-blocking — never a hard block at F0.9 ────────
def test_string_key_links_entry_is_non_blocking(tmp_path: Path):
    """Swapping a valid dict link for a string link must NOT change ``passed``
    or the error count — the string entry adds only a warning, never an error."""
    dict_castings = [_casting("C1", key_links=[{"from": "src/a.ts", "to": "src/b.ts"}])]
    str_castings = [_casting("C1", key_links=["src/a.ts -> src/b.ts"])]

    dict_result = _run_validate(tmp_path, dict_castings)
    str_result = _run_validate(tmp_path, str_castings)

    # The string entry introduces zero new blocking errors.
    assert str_result["summary"]["error_count"] == dict_result["summary"]["error_count"]
    assert str_result["passed"] == dict_result["passed"]
    # And it is not recorded as a dimension-4 error.
    assert _dim4(str_result)["ok"] == _dim4(dict_result)["ok"]


# ── AC 4 (FR-006/CT-003): dict entries behave EXACTLY as before ────────────
def test_dict_key_links_entry_behaves_unchanged(tmp_path: Path):
    """A dict key_links entry produces no string-entry warning and keeps the
    dimension clean (from/to are read via the unchanged code path)."""
    castings = [_casting("C1", key_links=[{"from": "src/a.ts", "to": "src/b.ts"}])]

    result = _run_validate(tmp_path, castings)
    dim4 = _dim4(result)

    warnings = [i for i in dim4["issues"] if i.get("severity") == "warning"]
    assert warnings == []
    assert dim4.get("warnings", 0) == 0
    assert dim4["ok"] is True
    # No key_links warning bubbles to the top-level issues list either.
    top = [
        i for i in result["issues"]
        if i.get("dimension") == "key_links_planned" and i.get("severity") == "warning"
    ]
    assert top == []


# ── AC 5 (FR-006 sibling artifacts read at :227): string artifact guarded ──
def test_string_artifacts_entry_does_not_crash_and_warns(tmp_path: Path):
    """The sibling ``artifacts`` read is guarded the same way: a string
    artifacts entry is accepted as a description with a non-blocking warning
    instead of raising ``AttributeError``."""
    castings = [
        _casting(
            "C1",
            key_links=[{"from": "src/a.ts", "to": "src/b.ts"}],
            artifacts=["src/a.ts is the login form"],
        )
    ]

    result = _run_validate(tmp_path, castings)  # must not raise
    dim4 = _dim4(result)

    warnings = [i for i in dim4["issues"] if i.get("severity") == "warning"]
    assert len(warnings) == 1
    assert "artifacts" in warnings[0]["issue"]
    assert "src/a.ts is the login form" in warnings[0]["issue"]
    # Still non-blocking.
    assert dim4["ok"] is True


# ── NFR-001 guard: the module and its regression suite are importable ──────
def test_validate_module_importable():
    """Sanity guard so the regression suite fails loudly if the target symbol
    is renamed or the module stops importing."""
    assert callable(foundry_validate_castings)
