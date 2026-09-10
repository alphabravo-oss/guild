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
from foundry_mcp.tools.orchestration.teams import _check_sight_required


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
    from foundry_mcp.tools.orchestration.directives import _read_directives

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
    FR-019 and lives in orchestration/directives.py; this test pins the parse so
    that half has something correct to render.)"""
    from foundry_mcp.tools.orchestration.directives import (
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


# ===========================================================================
# Casting 2 / CT-016 / FR-017 / FR-024 / FR-050 / AC-027 / OT-018 — what a run
# records about itself at init: the GRIND cycle ceiling, and the identity of
# the build it is executing on.
#
# These extend the file rather than replacing it: every test above still
# passes, and the state.json literal they guard has gained keys, not changed
# meaning. AC-025/AC-026's COMPARISON — the self-target preflight and its
# refusal — is driven in tests/test_self_target.py, which can fake the two
# trees it needs; this file drives the RECORDING side, which needs neither.
# ===========================================================================


def test_init_records_the_executing_servers_identity(tmp_path):
    """OT-018 verbatim: 'After a successful init, state.json contains
    server_version, plugin_version, server_root and server_commit, and
    Foundry-Next's display shows them.'

    Not monkeypatched: this is the real derivation against the real tree the
    suite runs from, so it fails if the fourth-parent walk from
    ``foundry_mcp.__file__`` ever stops landing on the plugin directory."""
    result = foundry_init(project_root=str(tmp_path))
    state = json.loads(
        (Path(result["foundry_dir"]) / "state.json").read_text(encoding="utf-8")
    )

    for key in ("server_version", "plugin_version", "server_root", "server_commit"):
        assert key in state, f"state.json is missing {key!r}"
        assert isinstance(state[key], str)

    from foundry_mcp import __version__

    assert state["server_version"] == __version__
    assert Path(state["server_root"]).is_dir()
    assert (Path(state["server_root"]) / ".claude-plugin" / "plugin.json").is_file()
    assert state["plugin_version"], "the executing plugin.json declares a version"


def test_init_records_self_target_false_for_an_ordinary_project(tmp_path):
    """FR-050 verbatim: 'On other runs Foundry-Init records server_version,
    plugin_version, server_root, server_commit and Foundry-Next shows them;
    nothing is compared.' A tmp_path project holds no foundry plugin.json, so
    it is the ordinary case."""
    result = foundry_init(project_root=str(tmp_path))
    state = json.loads(
        (Path(result["foundry_dir"]) / "state.json").read_text(encoding="utf-8")
    )

    assert state["self_target"] is False
    assert result["self_target"] is False


def test_max_cycles_defaults_to_zero_in_both_stores(tmp_path):
    """FR-024 verbatim: 'Default 0 = unbounded.' Zero is the value that means
    the run has no ceiling, so it must be what an init that was never given
    one writes — in BOTH stores, since a reader finding it in one and not the
    other would have to decide which absence meant unbounded."""
    result = foundry_init(project_root=str(tmp_path))
    state = json.loads(
        (Path(result["foundry_dir"]) / "state.json").read_text(encoding="utf-8")
    )

    assert state["max_cycles"] == 0
    assert _read_manifest(result)["max_cycles"] == 0


def test_max_cycles_is_persisted_to_state_and_manifest(tmp_path):
    """CT-016 verbatim: 'max_cycles from the --max-cycles flag, default 0 ...
    persisted in state.json'. The manifest carries it too, mirroring how
    temper and nyquist are written to both — and the manifest literal used to
    hardcode 0 while no caller could set it, so this is the test that fails if
    the parameter is ever un-threaded back to a constant."""
    result = foundry_init(project_root=str(tmp_path), max_cycles=12)
    state = json.loads(
        (Path(result["foundry_dir"]) / "state.json").read_text(encoding="utf-8")
    )

    assert state["max_cycles"] == 12
    assert _read_manifest(result)["max_cycles"] == 12
    assert result["max_cycles"] == 12


def test_the_version_fields_do_not_disturb_the_existing_state_keys(tmp_path):
    """No-regression. The keys every earlier phase reads out of state.json are
    still there and still mean what they meant; the preflight ADDED fields, it
    did not restructure the document."""
    result = foundry_init(
        spec_path=None, temper=True, nyquist=True, project_root=str(tmp_path)
    )
    state = json.loads(
        (Path(result["foundry_dir"]) / "state.json").read_text(encoding="utf-8")
    )

    assert state["phase"] == "F0"
    assert state["cycle"] == 0
    assert state["temper"] is True
    assert state["nyquist"] is True
    assert state["no_ui"] is False
    assert "F0" in state["phase_times"]
    # AC5's Locked constraint, re-checked against the widened literal.
    assert "target_url" not in state
    assert "url" not in state


# ---------------------------------------------------------------------------
# fallout casting 4 — the resume cap, the two seeded artefacts, and the one
# meaning of --no-ui.
#
#   AC9   test_resume_rewrites_the_persisted_cap
#           fallout CT-006 / ST-002 / FR-020 / AC-027 / OT-025 — the resume
#           branch honours max_cycles instead of dropping it.
#   AC10  test_a_resume_carrying_no_cap_leaves_the_persisted_one_alone
#           an OMITTED flag leaves the ceiling alone, which is what keeps a
#           bare `/foundry:resume` from lifting one nobody asked it to lift.
#   AC10b test_a_resume_carrying_an_explicit_zero_lifts_the_cap_to_unbounded
#           fallout D-067 — the other half of the same distinction: 0 is a cap
#           of 0, which is the documented spelling of unbounded, and it is
#           written. The two arms are what make "N at least 0" true of the
#           whole accepted range instead of all of it but one value.
#   AC11  test_a_cap_this_door_will_not_honour_is_refused
#           fallout CT-006 — 'N not an integer'; and a negative value, which
#           the persisted-cap reader would silently treat as no cap.
#   AC11b test_the_three_surfaces_give_one_answer_to_what_a_cap_is
#           fallout D-194 — the CLASS pin. The wire schema, the handler rung
#           and the honouring read judge "is 2.0 an integer" and must agree;
#           the rung was the third answer, so a cap the door advertises as
#           valid was refused and not rewritten.
#   AC11c test_a_zero_fraction_float_cap_is_written_as_the_integer_it_is
#           fallout D-194 / CT-006 — the defect's own path at the real door,
#           and the stored value's TYPE, because every store takes it raw.
#   AC11d test_a_new_run_takes_the_same_answer_as_the_resume_branch
#           the ADJACENT path: the same rung runs ahead of both branches, and
#           the new-run one writes to two stores rather than one.
#   AC12  test_a_cap_below_the_current_cycle_halts_at_the_next_grind_door
#           fallout ST-002 / OT-025 — the downstream fact, driven at the real
#           GRIND door rather than re-read off the state file.
#   AC13  test_init_creates_the_rosters_directory_and_the_concerns_ledger
#           fallout CT-001 / CT-002 — both waiting on disk before any stream
#           or teammate reports for the first time.
#   AC14  test_the_no_ui_definition_is_stated_once_where_the_flag_arrives
#           fallout FR-055 / AC-052 — one meaning, at the parameter that
#           receives it, pinned by exact substring.
#   AC15  test_init_stamps_the_schema_generation_that_makes_f0_9_fail_closed
#           fallout FR-054 / D-052 — the marker that tells a new run from a
#           legacy archive, driven at the real F0.9 door rather than read back
#           off the state file.
#   AC16  test_a_resume_does_not_stamp_the_marker_onto_a_legacy_archive
#           the other half of the same clause: the recovery door must not
#           raise a legacy archive's generation out from under the reader.
# ---------------------------------------------------------------------------


def _state(result: dict) -> dict:
    return json.loads(
        (Path(result["foundry_dir"]) / "state.json").read_text(encoding="utf-8")
    )


def test_resume_rewrites_the_persisted_cap(tmp_path):
    """fallout CT-006 verbatim: 'state.json.max_cycles rewritten to N'.

    fallout survey/data.md:336 is the gap: the resume branch did
    ``document.update(version_fields)`` and nothing else, so ``max_cycles`` — a
    parameter of the same function, advertised on the schema and forwarded by
    the dispatch lambda — was accepted and dropped on the floor. An operator
    lowering a ceiling onto a running run was told the resume succeeded while
    the ceiling stayed where it was.
    """
    created = foundry_init(project_root=str(tmp_path), max_cycles=12)
    assert _state(created)["max_cycles"] == 12

    resumed = foundry_init(
        project_root=str(tmp_path), resume=created["run_name"], max_cycles=3
    )

    assert resumed.get("resumed") is True, resumed
    assert _state(resumed)["max_cycles"] == 3
    # Echoed beside the refreshed state, so a caller reads what was persisted
    # rather than what it asked for.
    assert resumed["max_cycles"] == 3
    # The provenance refresh still happened in the same transaction.
    assert "server_version" in _state(resumed)


def test_a_resume_carrying_no_cap_leaves_the_persisted_one_alone(tmp_path):
    """A bare ``/foundry:resume`` does not lift the ceiling it was launched with.

    ``commands/resume.md`` STEP 5 passes ``max_cycles`` only when
    ``--max-cycles N`` was invoked, so an omitted flag reaches this function as
    the parameter's own default and never becomes a write. The property is
    carried by that default (``None``) rather than by a value happening to be
    falsy — see the sibling below for why the difference is the whole defect.
    """
    created = foundry_init(project_root=str(tmp_path), max_cycles=7)

    resumed = foundry_init(project_root=str(tmp_path), resume=created["run_name"])

    assert _state(resumed)["max_cycles"] == 7
    assert resumed["max_cycles"] == 7


def test_a_resume_carrying_an_explicit_zero_lifts_the_cap_to_unbounded(tmp_path):
    """fallout D-067 / CT-006 verbatim: 'integer N at least 0' → 'max_cycles
    rewritten to N'. 0 is in that range, and it was the one value in it this
    door refused to write.

    The resume branch read ``if max_cycles:`` against a default of 0, so an
    explicit 0 and an omitted flag were one case and both left the cap alone.
    Driven against a run persisting 5, a resume with ``max_cycles=0`` left 5 on
    disk AND echoed 5 back — accurate, and useless to the operator who had just
    typed the documented spelling of 'unbounded' to clear the ceiling. The
    shared rung ``foundry.py#_max_cycles_problem`` went on hinting 'Pass 0 for
    unbounded' over a door that did not.

    The echo is asserted beside the persisted value on purpose: the two agreed
    before the fix as well, because the echo has always reported the PERSISTED
    result rather than the requested one. That is what made the bug quiet, and
    it is why only the state file can witness it.
    """
    created = foundry_init(project_root=str(tmp_path), max_cycles=5)
    assert _state(created)["max_cycles"] == 5

    resumed = foundry_init(
        project_root=str(tmp_path), resume=created["run_name"], max_cycles=0
    )

    assert resumed.get("resumed") is True, resumed
    assert _state(resumed)["max_cycles"] == 0, "an explicit 0 IS a cap of 0"
    assert resumed["max_cycles"] == 0, "and the echo is the persisted result"


@pytest.mark.parametrize(
    "bad",
    ["3", 2.5, True, -1, -1.0],
    ids=["string", "fractional_float", "bool", "negative", "negative_float"],
)
def test_a_cap_this_door_will_not_honour_is_refused(tmp_path, bad):
    """fallout CT-006's errors column: 'N not an integer'.

    Plus the negative case, which is the same rule one direction along and is
    on record: a negative cap passed the wire schema, was persisted, and was
    then read as 'no cap' by the persisted-cap reader — so an operator who
    typed -1 ran unbounded and was told nothing. The accepted set has to equal
    the HONOURED set.

    ``True`` is refused as a non-integer on purpose: it IS an ``int`` in
    Python, and accepting it would persist a cap of 1 and halt the run at its
    first GRIND door for a caller who passed a flag where a ceiling was asked
    for.

    fallout D-194 — ``2.5`` IS STILL REFUSED AND SO IS ``-1.0``, which is the
    half of that fix that is easy to lose. The rung now accepts the ZERO-
    FRACTION float the wire schema and the honouring read both call an integer;
    a fractional one is not an integer under any of the three readings, and a
    whole negative one is refused by the rung below this one exactly as ``-1``
    is. The relaxation is one value-shape wide, not "floats are fine now".

    ``None`` LEFT this list at D-067 and is not a hole. It is no longer a bad
    value but the absence of one — the parameter's own default, meaning 'this
    call passed no cap' — and it is driven as such by
    ``test_a_resume_carrying_no_cap_leaves_the_persisted_one_alone``. Over the
    wire a JSON ``null`` never reaches the handler at all: ``server.py``'s
    Foundry-Init schema declares ``max_cycles`` as ``type: integer``, so
    Draft202012Validator refuses it a layer up.
    """
    created = foundry_init(project_root=str(tmp_path), max_cycles=9)

    refusal = foundry_init(
        project_root=str(tmp_path), resume=created["run_name"], max_cycles=bad
    )

    assert "max_cycles" in refusal.get("error", ""), refusal
    assert refusal.get("hint"), refusal
    assert _state(created)["max_cycles"] == 9, "a refused cap may not be written"


def test_the_cap_hint_names_an_exit_this_door_actually_takes(tmp_path):
    """fallout D-067 — the class pin, not just the value pin.

    D-067's class is ``refusal-hint-names-an-exit-the-check-never-reads``, and
    the shape of that failure is a refusal that hands the operator a remedy the
    next call declines to honour. So this does not assert on the hint's WORDS:
    it reads the hint the door gave, TAKES the exit it names, and requires the
    door to honour it. A hint that promised something this branch would not do
    would fail here whatever it was phrased as, which is the only form of the
    check that cannot be satisfied by rewording.
    """
    created = foundry_init(project_root=str(tmp_path), max_cycles=5)

    refusal = foundry_init(
        project_root=str(tmp_path), resume=created["run_name"], max_cycles=-1
    )
    hint = refusal.get("hint", "")
    assert "0 for unbounded" in hint, refusal
    assert _state(created)["max_cycles"] == 5, "a refused cap may not be written"

    # The exit the hint names, taken.
    honoured = foundry_init(
        project_root=str(tmp_path), resume=created["run_name"], max_cycles=0
    )
    assert _state(honoured)["max_cycles"] == 0, (
        f"the door refused with hint {hint!r} and then did not do it. A hint "
        "naming an exit the door declines to take is the D-067 class."
    )


def test_the_three_surfaces_give_one_answer_to_what_a_cap_is(tmp_path):
    """fallout D-194 — THE CLASS PIN: wire, handler rung and honouring read.

    fallout CT-006's input column is 'integer N at least 0' and its errors
    column is 'N not an integer'. THREE surfaces answer that question and the
    contract is only kept while all three give the SAME answer:

      * the WIRE — ``server.py#_argument_refusal`` against the Foundry-Init
        schema. Draft 2020-12 DEFINES ``integer`` to admit a zero-fraction
        float, so it accepts ``2.0``.
      * the HANDLER RUNG — ``foundry.py#_max_cycles_problem``, which the
        in-process caller and the resume path both reach.
      * the honouring READ — ``foundry_state.persisted_max_cycles``, whose own
        docstring says ``2.0`` is the integer 2 'by the rule the door validated
        against'.

    D-194 is what a gap between them costs: the rung read
    ``not isinstance(value, int)``, so ``foundry_init(resume=…,
    max_cycles=2.0)`` was REFUSED for a value the schema advertises as valid
    and the read already honours, and the cap was not rewritten.

    ``tests/orchestration/test_halt.py`` pins the wire against the read and
    never asks the rung, which is exactly why the third answer went unseen —
    so this pin drives all three over ONE table rather than any two of them.
    ``None`` is not on the table: it is the absence of a value, the caller
    skips the rung for it (D-067), and the wire refuses it a layer up.
    """
    from foundry_mcp import server as srv
    from foundry_mcp.tools.foundry import _max_cycles_problem
    from foundry_mcp.tools.foundry_state import persisted_max_cycles
    from tests.orchestration._env import _init_schema

    schema = _init_schema()
    for value in (0, 2, 99, 0.0, 2.0, 2.5, "2", True, -1, -1.0):
        wire_accepts = (
            srv._argument_refusal("Foundry-Init", schema, {"max_cycles": value})
            is None
        )
        rung_accepts = _max_cycles_problem(value) is None
        assert wire_accepts == rung_accepts, (
            f"{value!r}: the wire says {'accept' if wire_accepts else 'refuse'} "
            f"and the handler rung says {'accept' if rung_accepts else 'refuse'}. "
            "The accepted set must EQUAL the honoured set, and a value one "
            "surface takes and another turns away is D-194's class whichever "
            "way round it points."
        )
        if rung_accepts:
            # ...and what the door accepts, the read must honour as the SAME
            # number. `2.0 == 2` in Python, so the type is the assertion: a
            # cap no reader can act on is not a cap.
            honoured = persisted_max_cycles({"max_cycles": value})
            assert isinstance(honoured, int) and not isinstance(honoured, bool)
            assert honoured == int(value), (value, honoured)

    # The two values D-194 was filed on, named rather than left to the loop.
    assert _max_cycles_problem(2.0) is None, "a zero-fraction float IS an integer"
    assert _max_cycles_problem(0.0) is None, "and so is a zero-fraction zero"
    assert _max_cycles_problem(2.5) is not None, "a FRACTIONAL float is not"
    assert _max_cycles_problem(True) is not None, "and a bool is not a ceiling"


def test_a_zero_fraction_float_cap_is_written_as_the_integer_it_is(tmp_path):
    """fallout D-194 / CT-006 verbatim: 'state.json.max_cycles rewritten to N'.

    THE DEFECT'S OWN PATH, driven: a resume carrying ``2.0`` — a value the
    Foundry-Init schema accepts — used to come back 'Invalid max_cycles: 2.0 is
    not an integer' with the persisted cap untouched at 9.

    AND THE NUMBER THAT LANDS IS AN ``int``. Every store takes this value raw:
    state.json and castings/manifest.json on the new-run branch, state.json on
    the resume branch, both result echoes, and ``foundry_report.py`` renders
    the raw field into REPORT.json twice. ``2.0 == 2`` is True in Python, so
    equality alone cannot tell the stored float from the honoured integer — the
    TYPE is what a reader sees, and 'max_cycles 2.0' printed beside a decision
    made on 2 is a message about a number no code acted on. The door takes the
    number from ``persisted_max_cycles``, the same read the GRIND door acts on,
    so the two cannot be two numbers.
    """
    created = foundry_init(project_root=str(tmp_path), max_cycles=9)
    assert _state(created)["max_cycles"] == 9

    resumed = foundry_init(
        project_root=str(tmp_path), resume=created["run_name"], max_cycles=2.0
    )

    assert "error" not in resumed, resumed
    persisted = _state(resumed)["max_cycles"]
    assert persisted == 2, resumed
    assert isinstance(persisted, int), repr(persisted)
    assert resumed["max_cycles"] == 2 and isinstance(resumed["max_cycles"], int)


def test_a_new_run_takes_the_same_answer_as_the_resume_branch(tmp_path):
    """THE ADJACENT PATH: the OTHER branch through the same rung.

    ``_max_cycles_problem`` runs ahead of BOTH branches — 'there is no reading
    under which a new run may store a cap the resume door would refuse' — so
    relaxing it for a zero-fraction float relaxes it for a run being CREATED
    too, and that branch writes the value to two stores rather than one.

    Asserting the persisted integer rather than re-driving the GRIND door is
    the whole point of normalising at the door: a cap typed ``3.0`` and a cap
    typed ``3`` are now the SAME persisted value, so every downstream reader —
    ``orchestration/transitions.py``'s ``would_halt``, ``halt.py``'s
    ``cap_reached`` seal, the report's two raw reads — is reading a run
    indistinguishable from the one
    ``test_a_cap_below_the_current_cycle_halts_at_the_next_grind_door``
    already drives end to end.
    """
    created = foundry_init(project_root=str(tmp_path), max_cycles=3.0)

    for store, value in (
        ("state.json", _state(created)["max_cycles"]),
        ("castings/manifest.json", _read_manifest(created)["max_cycles"]),
        ("result echo", created["max_cycles"]),
    ):
        assert value == 3, (store, value)
        assert isinstance(value, int), (store, repr(value))


def test_an_unknown_run_is_still_refused_on_resume(tmp_path):
    """fallout CT-006's other error: 'unknown run'. No-regression — the cap
    rung runs BEFORE the branch and must not have displaced it."""
    refusal = foundry_init(
        project_root=str(tmp_path), resume="no-such-run", max_cycles=3
    )
    assert "not found" in refusal.get("error", ""), refusal


def test_a_cap_below_the_current_cycle_halts_at_the_next_grind_door(tmp_path):
    """fallout ST-002 / OT-025 verbatim: 'a value below the current cycle halts
    at the next GRIND door'.

    Driven at the REAL door rather than re-read off state.json. The cap this
    function writes is only worth writing if something acts on it, and the
    thing that acts on it is two modules away: ``orchestration/halt.py``'s
    ``_persisted_max_cycles`` is the one read, the grind preconditions function
    in ``orchestration/transitions.py`` turns it into ``would_halt``, and the
    transition writes the halt with reason ``cap_reached``. None of that is
    this casting's code, which is exactly why the contract is asserted by
    driving it instead of by reading the cap a second time here.
    """
    from foundry_mcp.tools.foundry import foundry_add_defect
    from foundry_mcp.tools.orchestration.directives import foundry_defects_to_tasks
    from foundry_mcp.tools.orchestration.gates import (
        NEXT_ACTION_CALLED_MARKER,
        foundry_gate,
    )
    from foundry_mcp.tools.orchestration.transitions import (
        foundry_mark_phase_complete,
    )

    created = foundry_init(project_root=str(tmp_path))
    fdir = Path(created["foundry_dir"])
    set_active_run(created["run_name"])

    state_path = fdir / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state.update({"phase": "F2", "cycle": 4})
    state_path.write_text(json.dumps(state, indent=2), encoding="utf-8")

    # The GRIND door has rungs of its own ahead of the cap, and a run with no
    # work to do never reaches it. So the run is given the shape a real cycle
    # closes in: an open defect, and the tasks call the door demands.
    foundry_add_defect(
        cycle=4, source="prove", defect_type="MISSING",
        description="drove GET /jobs with a drained queue and got 500",
        defect_class="UNHANDLED_500", tier="LIVE", project_root=str(tmp_path),
    )
    foundry_defects_to_tasks(project_root=str(tmp_path))

    resumed = foundry_init(
        project_root=str(tmp_path), resume=created["run_name"], max_cycles=2
    )
    assert resumed["max_cycles"] == 2, resumed
    (fdir / NEXT_ACTION_CALLED_MARKER).touch()

    # fallout GI-032 — the gate REPORTS the cap as a non-refusing fact and does
    # not act on it. The cap this door wrote is the only input to it.
    gate = foundry_gate("grind", str(tmp_path))
    cap_row = [c for c in gate["checklist"] if c["check"].startswith("within_cycle_cap")]
    assert cap_row and cap_row[0]["would_halt"] is True, gate
    assert cap_row[0]["ok"] is True and cap_row[0]["refuses"] is False, gate

    # ...and the TRANSITION acts on it: a successful crossing into HALTED, not
    # a refusal.
    result = foundry_mark_phase_complete("grind_start", project_root=str(tmp_path))
    assert result.get("phase") == "HALTED", result

    after = json.loads(state_path.read_text(encoding="utf-8"))
    assert after["phase"] == "HALTED", (result, after)
    # The STRUCTURED reason casting 10's HALT_REASONS vocabulary spells, with
    # the free text beside it rather than instead of it.
    assert after["halted_reason"]["reason"] == "cap_reached", after
    assert "2" in after["halted_reason"]["text"], after
    assert after["halted_at_cycle"] == 4, after


def test_init_creates_the_rosters_directory_and_the_concerns_ledger(tmp_path):
    """fallout CT-001 / CT-002 — both artefacts on disk before anything reports.

    The run directory is created imperatively here, and these two join that
    list for the same reason ``observations.json`` did: the first stream to
    derive a roster, and the first teammate to raise a cross-casting concern,
    must never be the caller that discovers the artefact does not exist.

    The names come from casting 1's own modules rather than being re-typed —
    ``rosters.ROSTERS_DIRNAME`` and ``concerns.CONCERNS_FILENAME`` /
    ``CONCERNS_COLLECTION_KEY`` — so a rename there cannot leave this creating
    a directory nothing reads.
    """
    from foundry_mcp.tools.concerns import (
        CONCERNS_COLLECTION_KEY,
        CONCERNS_FILENAME,
        read_concerns,
    )
    from foundry_mcp.tools.rosters import ROSTERS_DIRNAME

    result = foundry_init(project_root=str(tmp_path))
    fdir = Path(result["foundry_dir"])

    assert (fdir / ROSTERS_DIRNAME).is_dir(), sorted(p.name for p in fdir.iterdir())

    concerns_path = fdir / CONCERNS_FILENAME
    assert concerns_path.is_file(), sorted(p.name for p in fdir.iterdir())
    assert json.loads(concerns_path.read_text(encoding="utf-8")) == {
        CONCERNS_COLLECTION_KEY: []
    }
    assert CONCERNS_FILENAME in result["files_created"], result["files_created"]

    # Well-formed to the module that owns every later write, not merely present.
    records, problem = read_concerns(fdir)
    assert (records, problem) == ([], None)


def test_the_no_ui_definition_is_stated_once_where_the_flag_arrives(tmp_path):
    """fallout FR-055 / AC-052 verbatim: '--no-ui and --output-dir are parsed
    and echoed but read by nothing' — '--no-ui is threaded into Foundry-Init
    with one documented meaning across README, setup script and gate'.

    fallout survey/surface.md FI-2 found the flag meaning three different
    things at once — setup-foundry.sh's 'Skip browser audit (SIGHT)',
    README.md's 'Suppress orchestrator banners', and a SIGHT check treating
    ``manifest.no_ui`` as a hard block — which is three answers to one question
    an operator has to choose between without being told there is a choice.

    This casting's deliverable is the DEFINITION and the threading; the README
    row and the setup-script line are casting 8's and the gate's behaviour is
    casting 2's, and all three quote this sentence. So it is pinned by exact
    substring against the constant, never against a re-typed copy — the
    ``_PYTEST_DISCOVERY_PHRASE`` shape, where the prose a reader meets is
    derived from the code a test can hold.

    WHICH MODULE HOLDS THE CONSTANT (C-059 row 8, GRIND cycle 4). The sentence
    is a closed-vocabulary value, so casting 10 landed it in
    ``schemas/vocab.py`` and that is the single spelling every reader now
    takes: ``orchestration/width.py`` and ``orchestration/teams.py`` already
    import it from there, casting 8 repointed its own pin in 292dc0c, and this
    import followed. ``tools/foundry.py``'s copy is byte-identical and is
    being deleted; this pin does not care WHICH module defines the sentence,
    only that the door's docstring quotes the constant rather than a re-typed
    twin, so it reads the vocabulary and keeps holding through the delete.
    """
    import inspect

    from foundry_mcp.schemas.vocab import NO_UI_MEANING

    # Compared with whitespace normalised on BOTH sides: the constant is one
    # line and the docstring wraps, so an exact-substring match would be
    # pinning the wrap column rather than the sentence.
    def _flat(text: str) -> str:
        return " ".join(text.split())

    assert _flat(NO_UI_MEANING) in _flat(inspect.getdoc(foundry_init) or ""), (
        NO_UI_MEANING
    )
    assert "SIGHT" in NO_UI_MEANING and "browsable UI" in NO_UI_MEANING

    # Threaded, and persisted to BOTH stores exactly as temper and nyquist are:
    # the gate that acts on it reads the manifest, the display reads state.
    result = foundry_init(project_root=str(tmp_path), no_ui=True)
    assert _state(result)["no_ui"] is True
    assert _read_manifest(result)["no_ui"] is True


def test_init_stamps_the_schema_generation_that_makes_f0_9_fail_closed(tmp_path):
    """fallout FR-054 / D-052, the clause that had no producer: 'F0.9 fails
    closed on missing requirement_ids only for NEW runs'.

    The discriminator between a new run and a legacy archive is
    ``state.json.archive_schema_version`` against
    ``foundry_validate.REQUIREMENT_IDS_SCHEMA_FLOOR``, and until this stamp the
    ONLY writer of that key was ``scripts/migrate-archive.py``. So a run this
    server had created minutes earlier read as version 0 — indistinguishable
    from ``daring-orca`` — and the fail-closed half of the contract could never
    fire on the runs it was written for. The reader half shipped correct; this
    is the producer that makes it reachable.

    DRIVEN AT THE REAL F0.9 DOOR rather than asserted on the state literal.
    ``foundry_validate_castings`` is casting 7's file and the marker is only
    worth writing if that door changes its answer because of it, so the run
    this function created is handed a manifest of exactly the shape that used
    to be reported not computable — one casting citing a requirement id in its
    ``spec_text`` and carrying no ``requirement_ids`` — and the door now
    refuses it.
    """
    # fallout C-023 — from the LEAF that declares it, not from `foundry.py`,
    # which now imports the same name. A test that reached for the re-export
    # would still pass while asserting nothing about where the value lives, and
    # the whole point of the concern was that the integer had four homes.
    from foundry_mcp.tools.foundry_state import ARCHIVE_SCHEMA_VERSION
    from foundry_mcp.tools.foundry_validate import (
        REQUIREMENT_IDS_SCHEMA_FLOOR,
        foundry_validate_castings,
    )

    result = foundry_init(project_root=str(tmp_path))
    state = _state(result)

    marker = state.get("archive_schema_version")
    # `bool` excluded on the same grounds `_archive_schema_version` excludes it:
    # it is a subclass of `int` and would compare as 0 or 1.
    assert isinstance(marker, int) and not isinstance(marker, bool), state
    assert marker == ARCHIVE_SCHEMA_VERSION, state
    assert marker >= REQUIREMENT_IDS_SCHEMA_FLOOR, (marker, REQUIREMENT_IDS_SCHEMA_FLOOR)

    manifest_path = Path(result["foundry_dir"]) / "castings" / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["castings"] = [{
        "id": 1,
        "title": "The door under test",
        "spec_text": (
            "- **FR-007** [from A-007]: the door refuses a filing that carries "
            "no reproduction\n"
        ),
    }]
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    dim = foundry_validate_castings(str(tmp_path))["dimensions"][
        "requirement_ownership"
    ]

    assert dim["archive_schema_version"] == ARCHIVE_SCHEMA_VERSION, dim
    assert dim["not_computable"] is False, dim
    assert dim["ok"] is False, dim
    assert [i["issue"] for i in dim["issues"]] == ["missing_requirement_ids"], dim


def test_a_resume_does_not_stamp_the_marker_onto_a_legacy_archive(tmp_path):
    """The other half of fallout FR-054: the marker records the generation a run
    was CREATED under, so the RECOVERY door must not raise it.

    A legacy archive stamped on resume stops being distinguishable from a new
    run, which is the discrimination the clause above rests on — destroyed by
    the one door an operator reaches for when recovering a legacy run.
    ``scripts/migrate-archive.py`` is what raises an old archive's marker, on
    purpose and with a migration behind it.

    The legacy archive is made the only way it can be made here: the marker
    removed, which is exactly what ``daring-orca`` looks like on disk. Driven
    with a cap alongside, so the resume genuinely writes to the document rather
    than passing through a branch that touched nothing.
    """
    created = foundry_init(project_root=str(tmp_path))
    state_path = Path(created["foundry_dir"]) / "state.json"

    state = json.loads(state_path.read_text(encoding="utf-8"))
    del state["archive_schema_version"]
    state_path.write_text(json.dumps(state, indent=2), encoding="utf-8")

    resumed = foundry_init(
        project_root=str(tmp_path), resume=created["run_name"], max_cycles=3
    )

    assert _state(resumed)["max_cycles"] == 3, resumed
    assert "archive_schema_version" not in _state(resumed), _state(resumed)


# --- fallout D-118: the three run-mode switches survive a resume ------------
#     fallout FR-047 / FR-055 / AC-052 / GI-015 --------------------------------
#
# `server.py` forwards `temper`, `nyquist`, `no_ui` and `max_cycles` to this
# handler; the resume branch wrote `version_fields` and `max_cycles` and
# returned, so three parameters of its own signature were accepted and dropped
# without a word. Each is read at a phase decision — `state["temper"]` and
# `state["nyquist"]` by the transition table, `manifest.no_ui` by the
# SIGHT-requirement reader — so a resume asking for a TEMPER pass, or declaring
# that this run has no browsable UI, was acknowledged and had no effect.


def _manifest_of(result: dict) -> dict:
    return json.loads(
        (Path(result["foundry_dir"]) / "castings" / "manifest.json").read_text(
            encoding="utf-8"
        )
    )


def test_a_resume_raises_the_three_run_mode_switches(tmp_path):
    """fallout D-118 / FR-047: the driven reproduction.

    On a run whose persisted state had all four false/zero,
    ``foundry_init(resume=…, temper=True, nyquist=True, no_ui=True,
    max_cycles=9)`` left max_cycles 9 as asked while temper, nyquist and no_ui
    were ALL STILL FALSE, and the result carried no error and no note of the
    loss.
    """
    created = foundry_init(project_root=str(tmp_path))
    assert _state(created)["temper"] is False, _state(created)

    resumed = foundry_init(
        project_root=str(tmp_path),
        resume=created["run_name"],
        temper=True,
        nyquist=True,
        no_ui=True,
        max_cycles=9,
    )

    state = _state(resumed)
    assert state["max_cycles"] == 9, state
    for flag in ("temper", "nyquist", "no_ui"):
        assert state[flag] is True, f"{flag} was accepted and dropped: {state}"
    # The answer says what changed, so a dropped parameter is visible in it.
    assert sorted(resumed["raised"]) == ["no_ui", "nyquist", "temper"], resumed
    assert resumed["temper"] is True and resumed["nyquist"] is True, resumed


def test_a_resume_raises_no_ui_in_the_store_the_sight_reader_loads(tmp_path):
    """fallout FR-055 / AC-052: ``no_ui`` has ONE meaning and one reader.

    That reader — the SIGHT-requirement reader reached through
    ``orchestration/teams.py#_check_sight_required`` — loads
    ``castings/manifest.json``, not state.json, which is why
    ``foundry_init`` writes all three switches to BOTH documents on a new run.
    A resume that raised the flag in state.json alone would still have no
    effect on the gate this defect names, so the REAL gate is driven here.
    """
    created = foundry_init(project_root=str(tmp_path))
    manifest_path = Path(created["foundry_dir"]) / "castings" / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["castings"] = [
        {"id": 1, "key_files": ["src/app/Page.tsx"]},
    ]
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    set_active_run(created["run_name"])
    blocked = _check_sight_required(str(tmp_path))
    assert blocked.get("required") is True, blocked

    resumed = foundry_init(
        project_root=str(tmp_path), resume=created["run_name"], no_ui=True
    )

    assert _manifest_of(resumed)["no_ui"] is True, _manifest_of(resumed)
    assert resumed["manifest_raised"] == ["no_ui"], resumed
    after = _check_sight_required(str(tmp_path))
    assert after.get("required") is False and after.get("no_ui") is True, after


def test_a_bare_resume_leaves_an_opted_in_switch_alone(tmp_path):
    """fallout GI-015 / D-118: a resume RAISES a switch and there is no
    lowering door — the ruling, driven.

    Every surface an operator meets these on is a CLI switch: ``--temper``,
    ``--nyquist``, ``--no-ui``, none of which has an off spelling. So a false
    is "not typed" rather than "turn it off", and a branch that wrote the false
    as an instruction would turn a bare ``/foundry:resume`` into a silent
    downgrade of an opted-in TEMPER run — a capability removed to satisfy an
    item, which GI-007 forbids. The shipped dispatch fills an omitted boolean
    with ``False``, so this is the reachable path and not a corner of one.
    """
    created = foundry_init(project_root=str(tmp_path), temper=True, nyquist=True)
    assert _state(created)["temper"] is True

    resumed = foundry_init(project_root=str(tmp_path), resume=created["run_name"])

    state = _state(resumed)
    assert state["temper"] is True, "a bare resume turned an opted-in TEMPER off"
    assert state["nyquist"] is True, state
    assert resumed["raised"] == [], resumed
    assert _manifest_of(resumed)["temper"] is True, _manifest_of(resumed)


def test_a_resume_raises_nothing_it_was_not_asked_for(tmp_path):
    """fallout D-118: the raise is per switch, not a mode word.

    A resume carrying one switch must not carry the other two along with it —
    a call that turned on NYQUIST because the operator asked for TEMPER would
    be the dropped-parameter defect inverted.
    """
    created = foundry_init(project_root=str(tmp_path))

    resumed = foundry_init(
        project_root=str(tmp_path), resume=created["run_name"], nyquist=True
    )

    state = _state(resumed)
    assert state["nyquist"] is True, state
    assert state["temper"] is False, state
    assert state["no_ui"] is False, state
    assert resumed["raised"] == ["nyquist"], resumed


def test_a_resume_still_succeeds_when_the_manifest_cannot_be_read(tmp_path):
    """fallout D-118: resume is the RECOVERY door, so the second store is
    SKIPPED rather than refused.

    ``_locked_document`` fails CLOSED on a corrupt document (Holmes helper-1),
    so reaching for the manifest unguarded would turn a resume that succeeds
    today into a refusal over a file this branch never used to touch. The
    answer says which store actually took the write.
    """
    created = foundry_init(project_root=str(tmp_path))
    manifest_path = Path(created["foundry_dir"]) / "castings" / "manifest.json"
    manifest_path.write_text("{ not json at all", encoding="utf-8")

    resumed = foundry_init(
        project_root=str(tmp_path), resume=created["run_name"], temper=True
    )

    assert "error" not in resumed, resumed
    assert _state(resumed)["temper"] is True, _state(resumed)
    assert resumed["raised"] == ["temper"], resumed
    assert resumed["manifest_raised"] == [], resumed
