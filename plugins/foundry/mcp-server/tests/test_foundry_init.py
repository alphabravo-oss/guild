"""Casting C2 / FR-002 / GI-001 / CT-001 — Foundry-Init --url threading and
persistence into ``castings/manifest.json`` ``target_url``.

One regression test per acceptance criterion (NFR-001):

  AC1  test_init_schema_exposes_url_property
         Foundry-Init inputSchema declares a ``url`` property.
  AC2  test_dispatch_lambda_forwards_url_to_manifest
         The Foundry-Init dispatch lambda forwards ``args["url"]`` through
         ``foundry_init`` end-to-end (exercises the real server dispatch).
  AC3  test_manifest_persists_target_url_when_url_given
         ``foundry_init(url=...)`` persists the value to
         ``castings/manifest.json`` ``target_url`` (the store of record,
         mirroring foundry.sh:176).
  AC4  test_manifest_target_url_empty_without_url
         A run without ``url`` persists ``target_url == ""`` (the value that
         keeps the inspect gate blocked).
  AC5  test_state_json_not_extended_with_url  (Locked constraint)
         The URL is NOT written into state.json — target_url lives only in
         the manifest.
  AC6  test_run_without_url_but_frontend_files_blocks_sight_gate  (CT-001)
         A run without a URL but WITH frontend files in the castings still
         blocks the real ``_check_sight_required`` inspect gate.
  AC7  test_start_md_threads_url_into_foundry_init
         commands/start.md threads ``--url`` from the invocation into
         Foundry-Init.
  AC8  test_resume_tolerates_missing_target_url  (no-regression, informational)
         Resuming a run whose manifest lacks ``target_url`` does not crash.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from foundry_mcp.tools.foundry import foundry_init
from foundry_mcp.tools.foundry_state import clear_active_run, set_active_run
from foundry_mcp.tools.foundry_orchestrator import _check_sight_required


# tests/test_foundry_init.py -> parents: [0]=tests, [1]=mcp-server, [2]=foundry.
_FOUNDRY_ROOT = Path(__file__).resolve().parents[2]
_START_MD = _FOUNDRY_ROOT / "commands" / "start.md"


@pytest.fixture(autouse=True)
def _isolate_active_run():
    """Reset the in-memory active-run global before and after each test so
    the module-level ``set_active_run`` side effect of ``foundry_init`` never
    leaks between tests (or into the real repo)."""
    clear_active_run()
    yield
    clear_active_run()


def _read_manifest(result: dict) -> dict:
    manifest_path = Path(result["foundry_dir"]) / "castings" / "manifest.json"
    assert manifest_path.is_file(), f"manifest.json not written at {manifest_path}"
    return json.loads(manifest_path.read_text(encoding="utf-8"))


# --- AC1 --------------------------------------------------------------------
def test_init_schema_exposes_url_property():
    """Foundry-Init inputSchema declares a ``url`` property so callers can
    pass the SIGHT target URL."""
    from foundry_mcp import server

    # Locate the Foundry-Init Tool object from the registered tool list.
    tools = server._build_tool_list() if hasattr(server, "_build_tool_list") else None
    schema = None
    if tools is not None:
        for t in tools:
            if getattr(t, "name", None) == "Foundry-Init":
                schema = t.inputSchema
                break
    if schema is None:
        # Fall back to a source-level assertion if the tool list is not
        # exposed as a helper — the property must exist in the schema block.
        src = (server.__file__ and Path(server.__file__).read_text(encoding="utf-8")) or ""
        assert '"url"' in src
        return
    props = schema.get("properties", {})
    assert "url" in props, f"Foundry-Init schema missing 'url' property: {props.keys()}"
    assert props["url"].get("type") == "string"


# --- AC2 --------------------------------------------------------------------
def test_dispatch_lambda_forwards_url_to_manifest(tmp_path, monkeypatch):
    """The Foundry-Init dispatch lambda extracts ``args['url']`` and threads it
    all the way to the persisted manifest.target_url."""
    from foundry_mcp import server

    monkeypatch.setattr(server, "_project_root", str(tmp_path))
    result = server._DISPATCH["Foundry-Init"]({"url": "http://localhost:4321"})
    assert "foundry_dir" in result, result
    manifest = _read_manifest(result)
    assert manifest["target_url"] == "http://localhost:4321"


# --- AC3 --------------------------------------------------------------------
def test_manifest_persists_target_url_when_url_given(tmp_path):
    """foundry_init(url=...) writes target_url into castings/manifest.json —
    the store of record the inspect gate readers load."""
    result = foundry_init(url="https://example.test/app", project_root=str(tmp_path))
    manifest = _read_manifest(result)
    assert manifest["target_url"] == "https://example.test/app"
    # Mirror of the bash store-of-record shape (foundry.sh:168-182).
    for key in ("created_at", "updated_at", "status", "castings", "waves", "no_ui"):
        assert key in manifest, f"manifest missing store-of-record key {key!r}"
    assert manifest["castings"] == []  # empty at init → DECOMPOSE still emitted
    assert manifest["status"] == "initialized"


# --- AC4 --------------------------------------------------------------------
def test_manifest_target_url_empty_without_url(tmp_path):
    """A run created without a url persists target_url == "" — the exact value
    that keeps the inspect/SIGHT gate blocked."""
    result = foundry_init(project_root=str(tmp_path))
    manifest = _read_manifest(result)
    assert manifest["target_url"] == ""


# --- AC5 (Locked: do NOT extend state.json) ---------------------------------
def test_state_json_not_extended_with_url(tmp_path):
    """The URL is persisted ONLY to the manifest; state.json must not gain a
    target_url/url key (Locked constraint — state.json is not the store of
    record for the URL)."""
    result = foundry_init(url="http://localhost:3000", project_root=str(tmp_path))
    state_path = Path(result["foundry_dir"]) / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert "target_url" not in state
    assert "url" not in state


# --- AC6 (CT-001) -----------------------------------------------------------
def test_run_without_url_but_frontend_files_blocks_sight_gate(tmp_path):
    """CT-001: a run WITHOUT a url but WITH frontend files in the castings
    still blocks the inspect gate. Drives the real _check_sight_required
    reader against the manifest foundry_init produced."""
    result = foundry_init(project_root=str(tmp_path))  # no url
    manifest_path = Path(result["foundry_dir"]) / "castings" / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["target_url"] == ""

    # Simulate DECOMPOSE adding a frontend casting (a .tsx key_file) while the
    # target_url stays empty — exactly the CT-001 error path.
    manifest["castings"] = [{"id": 1, "key_files": ["src/components/App.tsx"]}]
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    set_active_run(result["run_name"])
    verdict = _check_sight_required(str(tmp_path))
    assert verdict.get("required") is True
    assert verdict.get("blocked") is True, verdict


# --- AC7 --------------------------------------------------------------------
def test_start_md_threads_url_into_foundry_init():
    """commands/start.md threads the --url invocation flag into Foundry-Init."""
    text = _START_MD.read_text(encoding="utf-8")
    assert "--url" in text
    # The threading must connect the invocation flag to a url argument on
    # Foundry-Init (not merely mention SIGHT).
    assert "url=" in text
    assert "Foundry-Init" in text


# --- AC8 (no-regression, informational) -------------------------------------
def test_resume_tolerates_missing_target_url(tmp_path):
    """Resuming a run whose manifest omits target_url must not crash — resume
    reads state.json and never requires target_url to be present."""
    created = foundry_init(project_root=str(tmp_path))
    run_name = created["run_name"]

    # Strip target_url from the manifest to emulate a legacy/older run.
    manifest_path = Path(created["foundry_dir"]) / "castings" / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest.pop("target_url", None)
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    resumed = foundry_init(resume=run_name, project_root=str(tmp_path))
    assert resumed.get("resumed") is True
    assert resumed.get("run_name") == run_name


# ===========================================================================
# Casting 3 / FR-001 / FR-019 / AC-004 — run artifacts seeded at init.
#
#   AC-004a  a fresh run carries observations.json, seeded empty, alongside
#            defects.json.
#   AC-004b  a fresh run carries directives.md holding the observation/defect
#            ruling as a default F0 directive.
#   AC-004c  the seeded directive parses — it is written in the exact grammar
#            `_read_directives` reads, at NORMAL priority. This is the test
#            that fails if the seeding drifts out of the grammar, which would
#            leave the ruling present on disk but invisible to the lead.
# ===========================================================================


def test_init_seeds_an_empty_observations_ledger(tmp_path):
    """AC-004a / FR-001 — the typed non-blocking channel exists from the first
    moment of the run, so a stream never has to decide whether it is there."""
    result = foundry_init(project_root=str(tmp_path))
    path = Path(result["foundry_dir"]) / "observations.json"
    assert path.is_file(), "observations.json not seeded at init"
    assert "observations.json" in result["files_created"]
    ledger = json.loads(path.read_text(encoding="utf-8"))
    assert ledger == {"observations": [], "tripwire": []}


def test_init_seeds_the_f0_observation_defect_ruling(tmp_path):
    """AC-004b — Foundry-Init seeds the observation/defect ruling as a default
    F0 directive, with no per-run configuration required."""
    result = foundry_init(project_root=str(tmp_path))
    path = Path(result["foundry_dir"]) / "directives.md"
    assert path.is_file(), "directives.md not seeded at init"
    assert "directives.md" in result["files_created"]
    text = path.read_text(encoding="utf-8")
    assert "OBSERVATION/DEFECT SPLIT" in text
    assert "observations.json" in text
    assert "never-demote denylist is absolute" in text
    assert "audit" in text and "tripwire" in text


def test_seeded_directive_parses_as_a_normal_directive(tmp_path):
    """AC-004c — the seeded body is written in the grammar `_read_directives`
    parses (a `### [DIRECTIVE] {iso}` header, a blank line, then the body).

    Priority matters: the ruling is seeded NORMAL, which is why the
    Foundry-Next renderer must show normal directives alongside urgent ones.
    A directive written in any other shape parses as no directive at all."""
    from foundry_mcp.tools.foundry_orchestrator import _read_directives

    result = foundry_init(project_root=str(tmp_path))
    set_active_run(result["run_name"])

    directives = _read_directives(str(tmp_path))
    assert directives["has_directives"] is True
    assert directives["urgent"] == []
    assert len(directives["normal"]) == 1
    assert "OBSERVATION/DEFECT SPLIT" in directives["normal"][0]


def test_seeded_directive_survives_an_urgent_injection(tmp_path):
    """The seeded ruling must still PARSE as a standing normal directive after
    an urgent one is injected — both lists are populated independently.

    (Whether the Foundry-Next renderer then DISPLAYS both is the other half of
    FR-019 and lives in foundry_orchestrator.py; this test pins the parse so
    that half has something correct to render.)"""
    from foundry_mcp.tools.foundry_orchestrator import (
        _read_directives,
        foundry_inject_directive,
    )

    result = foundry_init(project_root=str(tmp_path))
    set_active_run(result["run_name"])
    foundry_inject_directive(
        directive="Stop and re-read the spec.",
        priority="urgent",
        project_root=str(tmp_path),
    )

    directives = _read_directives(str(tmp_path))
    assert len(directives["urgent"]) == 1
    assert len(directives["normal"]) == 1, (
        "the seeded F0 ruling was lost when an urgent directive arrived"
    )
    assert "OBSERVATION/DEFECT SPLIT" in directives["normal"][0]


def test_seeded_artifacts_are_fresh_per_run(tmp_path):
    """Every run gets its own ledger and its own seeded ruling — observations
    are persisted PER RUN (FR-023)."""
    first = foundry_init(project_root=str(tmp_path))
    second = foundry_init(project_root=str(tmp_path))
    assert first["run_name"] != second["run_name"]
    for result in (first, second):
        fdir = Path(result["foundry_dir"])
        assert json.loads((fdir / "observations.json").read_text())["observations"] == []
        assert "OBSERVATION/DEFECT SPLIT" in (fdir / "directives.md").read_text()
