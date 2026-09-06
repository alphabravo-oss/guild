"""INSPECT width: who decides it, when, and what the roster then means.

ST-006 / ST-007 / GI-008 / GI-009 / CT-009 / FR-011 / FR-012 / FR-013 / FR-033 /
FR-047 / FR-049 / AC-016 / AC-017 / AC-018 / AC-019 / OT-012 / OT-013 / OT-014 /
OT-015.

WHAT THIS FILE IS ABOUT
-----------------------
thunder-viper ran 22 GRIND cycles and re-verified everything after each one, so
a three-file GRIND cost a full INSPECT. DELTA makes the second and later
INSPECTs of a phase proportional to what changed, while every rule that matters
keeps the run at full width.

The single most important property here is not the saving — it is WHERE the
decision lives. GI-009: "whichever Foundry-Phase transition opens an INSPECT
records the mode; Foundry-Next only reports. No decision ever lives in
Foundry-Next." GI-008 names the violation from the other side: "A FULL versus
DELTA decision computed inside Foundry-Next, or a streams-complete check that
reads a roster nothing recorded because Next was skipped."

So several tests below assert the ABSENCE of a decision — that the mode is on
disk before any Foundry-Next runs, that two Foundry-Next calls report the same
sample rather than drawing twice, and that `_compute_next_action`'s source
contains no decision at all. A width that is merely usually right is not the
requirement; a width that only one call site can set is.

The run fixture is a REAL git repository, because the DELTA decision is a
function of a real `git diff` between two real commits. A monkeypatched diff
would pin the branch structure and prove nothing about the thing that computes
it.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from foundry_mcp.schemas.vocab import (
    DELTA_CONDITIONAL_STREAMS,
    FULL_ROSTER_STREAMS,
    INSPECT_DELTA_RULE,
    INSPECT_FULL_RULES,
    INSPECT_MODES,
    PROVE_DELTA_SAMPLE_SIZE,
)
from foundry_mcp.tools import foundry_state

# fallout FR-005 / AC-014 / GI-010 / GI-026 — THE WIDTH DECISION HAS A MODULE,
# AND THE SEVEN CONCERNS THIS FILE DRIVES HAVE SEVEN.
#
# The `fo` alias reached all of them through one name. The single orchestrator
# module it named is gone and GI-010 forbids a re-export shim standing in for
# it, so each reach names the module that DEFINES the symbol:
# the width decision and its scope predicates are `orchestration/width.py`; the
# roll-up and the streams-complete check are `orchestration/streams.py`; the
# transitions are `orchestration/transitions.py`; the gate ladder is
# `orchestration/gates.py`; the guidance engine is `orchestration/guidance.py`;
# the evidence sweep is `orchestration/evidence_boundary.py`; and the marker
# names and the document transaction are the leaf `tools/artifacts.py`.
#
# The seventh is `orchestration/fix_gate.py`, and it is here because a symbol
# MOVED rather than because this file grew a subject. GI-033 refuses a
# lifecycle-to-verifier read outright, so `_note_fix_after_inspect_decision` —
# the stamp `foundry_mark_defect_fixed` writes when a fix lands after a width
# was decided — left the verifier-set width module for its sole consumer. The
# fixture below reaches it there, and the width read it stamps ONTO is still
# width's.
#
# `_current_cycle` is none of them. Casting 10 consolidated the two
# byte-identical copies into `foundry_state.current_cycle` (GI-024, Holmes
# `share-2`), so reaching a split module for that leaf fact would recreate the
# second copy this run just removed.
from foundry_mcp.tools import artifacts as _artifacts
from foundry_mcp.tools.foundry_state import current_cycle as _current_cycle
from foundry_mcp.tools.foundry_state import git_changed_paths
from foundry_mcp.tools.orchestration import evidence_boundary as _evidence_boundary
from foundry_mcp.tools.orchestration import fix_gate as _fix_gate
from foundry_mcp.tools.orchestration import gates as _gates
from foundry_mcp.tools.orchestration import report_seal as _report_seal
from foundry_mcp.tools.orchestration import guidance as _guidance
from foundry_mcp.tools.orchestration import streams as _streams
from foundry_mcp.tools.orchestration import transitions as _transitions
from foundry_mcp.tools.orchestration import width as _width
from foundry_mcp.tools.orchestration.guidance import foundry_next_action
from foundry_mcp.tools.orchestration.streams import (
    _check_streams_complete,
    foundry_mark_stream,
)
from foundry_mcp.tools.orchestration.transitions import foundry_mark_phase_complete
from foundry_mcp.tools.foundry_state import current_inspect_mode
from foundry_mcp.tools.orchestration.width import _decide_inspect_mode


def _current_inspect_mode(fdir, cycle=None):
    """The recorded width, read the way every production caller reads it.

    fallout FR-005 / AC-014 / OT-016 — THE SYMBOL MOVED AND TOOK AN ARGUMENT.
    `width.py#_current_inspect_mode` is now
    `foundry_state.current_inspect_mode`, on the same reasoning that moved
    `current_cycle` above: the streams check, the guidance engine and the
    transitions all read the recorded width, and a verifier module every one of
    them imports is the lifecycle-to-verifier edge GI-033 refuses outright.

    The vocabulary is no longer named inside the read — the leaf is stdlib-only
    and takes `modes` from its caller — so this binds `INSPECT_MODES` exactly as
    the six production call sites do. It is a BINDING, not a reimplementation:
    every axis the read decides on is still decided in the read.
    """
    return current_inspect_mode(fdir, cycle, modes=INSPECT_MODES)

# `_check_active_teams` and `_active_teams` — the lifecycle and verifier halves
# of one leaf check — are bound by name across the orchestration modules, so
# patching the one that DEFINES either leaves every other binding on the real
# one.
# `ORCHESTRATION` is what `fo.__file__` used to mean: thirteen files, not one.
from tests.orchestration._env import ORCHESTRATION, patch_everywhere


def _plugin_root() -> Path:
    """`plugins/foundry/`, from this file."""
    return Path(__file__).resolve().parents[3] / "foundry"

RUN_NAME = "inspect-mode-run"


# --------------------------------------------------------------------------- #
# Fixtures — a real git repo, because the decision reads a real diff
# --------------------------------------------------------------------------- #


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True, text=True, check=True,
    )
    return result.stdout.strip()


@pytest.fixture
def run_env(tmp_path, monkeypatch):
    """Activate a run inside a real git repo; yield (project_root, fdir).

    Mirrors `test_escalation.py`'s `run_env` — same active-run activation, same
    team-scan monkeypatch (both layer names, plus the pane read) so no test
    depends on the ambient tmux session — and adds the repository the width
    decision needs.
    """
    project_root = tmp_path
    _git(project_root, "init", "-q")
    _git(project_root, "config", "user.email", "foundry@example.invalid")
    _git(project_root, "config", "user.name", "foundry")

    # The real repo's own rule, and it matters here: `.gitignore:16` is
    # `/foundry-archive/`, so nothing a run writes into its own directory can
    # ever appear in a GRIND diff. A fixture without this would put state.json,
    # defects.json and the run's copy of the spec into every diff, and the spec
    # is a verifier path — so every DELTA fixture would force FULL for a reason
    # that cannot occur in production.
    (project_root / ".gitignore").write_text("/foundry-archive/\n", encoding="utf-8")
    (project_root / "src").mkdir()
    (project_root / "src" / "handler.py").write_text("def handle():\n    pass\n")
    (project_root / "src" / "other.py").write_text("def other():\n    pass\n")
    _git(project_root, "add", "-A")
    _git(project_root, "commit", "-qm", "baseline")

    fdir = project_root / "foundry-archive" / RUN_NAME
    (fdir / "castings").mkdir(parents=True, exist_ok=True)

    # D-186: FLIPPABLE, not a constant. The permanent inactive stub this used to
    # install is why no test in this file could reach `foundry_gate`'s
    # `no_active_teams` arm — the arm that was shipping a refusal with no hint
    # and displacing the width refusal this file exists to pin. A stub that can
    # only answer one way makes the branch beside it unreachable, and an
    # unreachable branch is one nothing can catch. Mirrors
    # `test_orchestrator_gates.py`'s `_TEAM_SCAN` shape exactly, including the
    # reset on every entry so no test leaks its team state into the next.
    # fallout GI-033 / AC-061 (D-021 / D-035, ruling item 2) — TWO NAMES, ONE
    # FAKE. The two-layer team check is `foundry_state.active_teams` now, and
    # each layer composes it under its OWN name because neither may import the
    # other: `teams._check_active_teams` for lifecycle, `gates._active_teams`
    # for the verifier — and `transitions.py` binds the verifier one by
    # from-import, so the binding lives in the CONSUMER. Patching one name and
    # not the other leaves every gate and transition in this file shelling out
    # to the ambient tmux, which answers "no teams" and makes the ACTIVE arm
    # unreachable again — the exact D-186 hole the flippable stub was built to
    # close. `live_teammate_panes` is the third: it is the pane scan that used
    # to be `teams._scan_tmux_panes`, ruled leaf material because it is a READ
    # with no writer, and stubbing it is what keeps a machine with no tmux from
    # mattering. Mirrors `tests/orchestration/_env.py`'s `run_env` exactly.
    _TEAM_SCAN["active"] = False
    _fake_teams = lambda _pr: {
        "active": _TEAM_SCAN["active"],
        "teams": ["foundry-cast"] if _TEAM_SCAN["active"] else [],
        "live_panes": [],
    }
    patch_everywhere(monkeypatch, "_check_active_teams", _fake_teams)
    patch_everywhere(monkeypatch, "_active_teams", _fake_teams)
    patch_everywhere(
        monkeypatch,
        "live_teammate_panes",
        lambda: {"available": False, "live": [], "zombie": [], "user": [], "lead": None},
    )

    foundry_state.set_active_run(RUN_NAME)
    try:
        yield str(project_root), fdir
    finally:
        foundry_state.clear_active_run()


#: Whether the patched team scan — both `_check_active_teams` and
#: `_active_teams`, which share one fake — reports a registered team.
#: Reset by `run_env` on every test; flipped by `_teams_active` (D-186).
_TEAM_SCAN = {"active": False}


def _teams_active(active: bool) -> None:
    """Make the patched team scan report a registered team, or not."""
    _TEAM_SCAN["active"] = active


def _write_state(fdir: Path, phase: str, cycle: int = 0, **extra) -> None:
    """The run's state document. `self_target` defaults TRUE — see D-207.

    Every fixture in this file that exercises the TEST-01 scope predicate drives
    it against the EXECUTING server's own `_DISPATCH` registry: `Foundry-Report`
    resolving to `tools/foundry_report.py` is the assertion, and that is only a
    fact about the target on a run whose target IS this plugin. So these
    fixtures were always self-target-shaped and simply did not say so, which is
    the pin gap D-207 names — the registry arm had full coverage and the
    non-self-target arm had none, and the predicate quietly answered "nothing is
    covered" for the whole of the second.

    Recording it here keeps every existing fixture asserting exactly what it was
    written to assert. A test that means the OTHER run passes
    `self_target=False` and says so in its name.
    """
    state = {"phase": phase, "cycle": cycle, "self_target": True}
    state.update(extra)
    (fdir / "state.json").write_text(json.dumps(state), encoding="utf-8")


def _read_state(fdir: Path) -> dict:
    return json.loads((fdir / "state.json").read_text(encoding="utf-8"))


def _write_defects(fdir: Path, defects: list[dict]) -> None:
    (fdir / "defects.json").write_text(
        json.dumps({"defects": defects}, indent=2), encoding="utf-8"
    )


def _open_live(did: str = "D-001", cycle: int = 1) -> dict:
    """One OPEN LIVE defect: an ordinary GRIND with real work still in it.

    D-143 — WHAT THIS DOCSTRING USED TO EXPLAIN.
    -------------------------------------------
    It read "One OPEN LIVE defect — enough to keep `final_gate` from firing.
    Every DELTA fixture needs this. `final_gate` fires when no blocking defect
    remains, because the next gate is then ASSAY." That was the RETIRED proxy.
    `_decide_inspect_mode`'s final_gate arm did test `_blocking_defects(...)
    ["blocking"] == 0`, and D-068 removed it: under that test DELTA fired only
    when the lead crossed with LIVE defects still open, which no guidance
    instructs and which `Foundry-Gate('assay')` refuses anyway — so US-004
    delivered nothing. The GRIND cycle-4 ruling replaced it with two
    TRANSITION facts: final_gate fires when the GRIND was entered from ASSAY,
    TEMPER or NYQUIST feedback (`_entered_grind_from_feedback`), or when this
    crossing is the F2->F2 widening re-open.

    Driven at HEAD: a CLEARED ledger and a one-handler GRIND record
    DELTA/delta — so the state the retired sentence says fires final_gate does
    not fire it, and the claim "every DELTA fixture needs this" is false too.
    The fixtures built on this helper still pass because they assert the
    transition's output; what was wrong is the sentence a maintainer reads to
    learn WHY the fixture holds a LIVE defect, one file away from the rule that
    replaced it.

    WHY THE FIXTURES STILL HOLD ONE. Not to suppress final_gate — nothing here
    does that — but because a DELTA cycle is the ordinary GRIND->INSPECT
    crossing, and an ordinary GRIND has open work. A fixture asserting DELTA
    over an empty ledger would be asserting it over the run state that reaches
    `inspect_clean`'s widening refusal instead, which is a different subject.
    Fixtures that mean "this GRIND fixed everything" set `status: fixed`
    explicitly rather than dropping the record.
    """
    return {
        "id": did, "cycle": cycle, "source": "trace", "type": "UNWIRED",
        "description": "the handler never calls the store", "spec_ref": "FR-001",
        "symbol": "handle", "file": "src/handler.py", "status": "open",
        "tier": "LIVE", "class": "UNWIRED_SURFACE", "fixed_in_cycle": None,
    }


def _write_manifest(fdir: Path, **extra) -> None:
    manifest = {"castings": [{"id": 1, "key_files": ["src/handler.py"]}]}
    manifest.update(extra)
    (fdir / "castings" / "manifest.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )


def _write_spec(fdir: Path, ids: list[str]) -> None:
    (fdir / "spec.md").write_text(
        "".join(f"- **{rid}**: the thing works\n" for rid in ids), encoding="utf-8"
    )


def _entry_rung(result: dict, token: str) -> dict:
    """The `entered_from_accepted_phase` rung for `token`, off a result.

    Matched by PREFIX, not by equality: five tokens go through
    `_source_phase_rung` and label the row `... (token=<token>)`, while
    `inspect_start` writes its own and labels it `... (token=inspect_start,
    phase=<phase>)` because it accepts two phases for two different reasons.
    Both are the same rung and this reads either.
    """
    rows = [
        row for row in result["checklist"]
        if row["check"].startswith(f"entered_from_accepted_phase (token={token}")
    ]
    assert len(rows) == 1, (token, result["checklist"])
    return rows[0]


def _accepted_from(result: dict, token: str) -> list[str]:
    """The `accepted_from` list off a refusal's entry rung.

    fallout ST-012 / GI-011 / GI-029 — WHERE THIS FACT LIVES NOW.
    `accepted_from` was a top-level key of the refusal payload. Casting 4 gave
    every transition token one preconditions function answering as a checklist,
    and the transition adds no refusal of its own, so the fact is published on
    the rung that computed it: `entered_from_accepted_phase (token=<token>)`.
    Read by NAME rather than by index, because the ladder's order is the gate's
    business and no test here is about it.
    """
    return _entry_rung(result, token)["accepted_from"]


def _arm(fdir: Path) -> None:
    """Foundry-Next's ordering token, which gate/phase calls consume."""
    (fdir / ".next-action-called").write_text(f"{foundry_state.now_iso()}\n", encoding="utf-8")


def _grind_touching(project_root: str, fdir: Path, *paths: str) -> None:
    """Record a boundary at HEAD, then commit a change to each named path.

    This is what "the GRIND diff" MEANS: the boundary marker pins where the last
    INSPECT crossed, the commit is the GRIND's work, and the decision diffs
    between them. Writing the marker first and committing second is the real
    order of events, not a convenience.
    """
    root = Path(project_root)
    # Anything a test staged before this point belongs to the PREVIOUS cycle, so
    # it is committed before the boundary is recorded. Otherwise it would land
    # in the diff as if the GRIND had produced it.
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "--allow-empty", "-m", "pre-boundary")
    (fdir / _artifacts.INSPECT_BOUNDARY_SHA_MARKER).write_text(
        _git(root, "rev-parse", "HEAD") + "\n", encoding="utf-8"
    )
    for rel in paths:
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            (target.read_text(encoding="utf-8") if target.exists() else "")
            + "\n# a GRIND cycle touched this\n",
            encoding="utf-8",
        )
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "grind")


# --------------------------------------------------------------------------- #
# ST-006 / AC-016 / OT-012 — the PHASE-ENTRY transitions record FULL
# --------------------------------------------------------------------------- #


def test_the_f2_entry_records_full_before_any_next_is_called(run_env):
    """OT-012 verbatim: 'The Foundry-Phase transition into F2 records inspect
    mode FULL with rule first_of_phase in state.json before any Foundry-Next is
    called, and the first Foundry-Next reports it.'

    The token is `cast`, not `start_cast`: `start_cast` enters F1 and opens no
    INSPECT at all. AC-016 says "the phase-entry transition into F2", and `cast`
    is the transition that performs it.
    """
    project_root, fdir = run_env
    _write_state(fdir, phase="F1", cycle=0)
    _write_manifest(fdir)
    _arm(fdir)

    result = foundry_mark_phase_complete("cast", project_root)

    assert result["ok"] is True, result
    assert result["phase"] == "F2"
    assert result["inspect_mode"] == "FULL"
    assert result["inspect_rule"] == "first_of_phase"

    # On disk, before anything else runs. This is the half OT-012 is emphatic
    # about: a mode that only appears once someone asks for it is a mode
    # Foundry-Next computed.
    recorded = _read_state(fdir)["inspect_modes"]
    assert len(recorded) == 1
    assert recorded[0]["mode"] == "FULL"
    assert recorded[0]["rule"] == "first_of_phase"
    assert recorded[0]["phase"] == "F2"
    assert recorded[0]["decided_by"] == "cast"

    reported = foundry_next_action(project_root)["inspect_mode"]
    assert reported["mode"] == "FULL"
    assert reported["rule"] == "first_of_phase"


def test_the_f5_entry_records_full_on_the_same_terms(run_env):
    """OT-012's second half: 'the same holds for the transition into F5.'

    One rule, two doors. GI-009 says "whichever Foundry-Phase transition opens
    an INSPECT records the mode", and TEMPER's first INSPECT is as much a first
    INSPECT as CAST's — it has no previous INSPECT of that phase to be a delta
    from, so FULL is a property of the position, not a policy about TEMPER.
    """
    project_root, fdir = run_env
    _write_state(fdir, phase="F4", cycle=3)
    _write_manifest(fdir)
    _arm(fdir)

    result = foundry_mark_phase_complete("temper", project_root)

    assert result["ok"] is True, result
    assert result["phase"] == "F5"
    recorded = _read_state(fdir)["inspect_modes"][-1]
    assert (recorded["mode"], recorded["rule"]) == ("FULL", "first_of_phase")
    assert recorded["phase"] == "F5"
    assert recorded["decided_by"] == "temper"


def test_start_cast_opens_no_inspect_and_records_nothing(run_env):
    """The boundary of the rule, and the reason the token matters.

    `start_cast` calls `_update_phase(fdir, "F1")` — it enters CAST. No INSPECT
    is opened, so no width is decided, and recording one here would put a
    decision on the artifact for a cycle that has not begun.
    """
    project_root, fdir = run_env
    _write_state(fdir, phase="F0", cycle=0)
    # fallout GI-011 / CT-013 — `start_cast` HAS PRECONDITIONS OF ITS OWN NOW.
    # One preconditions function per token: `_start_cast_preconditions` refuses
    # without a manifest ("Run F0.5 DECOMPOSE first"), so a fixture that enters
    # CAST from an empty run dir is driving a state no run reaches. The subject
    # here is what `start_cast` RECORDS, and it has to get past the door to
    # record anything.
    _write_manifest(fdir)
    _arm(fdir)

    result = foundry_mark_phase_complete("start_cast", project_root)

    assert result.get("ok") is True, result
    assert result["phase"] == "F1"
    assert "inspect_modes" not in _read_state(fdir)
    assert _current_inspect_mode(fdir) is None


# --------------------------------------------------------------------------- #
# ST-006 / ST-007 / AC-016 / OT-013 — inspect_start evaluates the rules
# --------------------------------------------------------------------------- #


def test_a_grind_touching_one_handler_file_records_delta(run_env):
    """OT-013 verbatim (first half): 'After Foundry-Phase inspect_start
    following a GRIND whose diff touches only one handler file, state.json
    records DELTA and Foundry-Next reports it.'"""
    project_root, fdir = run_env
    _write_state(fdir, phase="F3", cycle=1)
    _write_defects(fdir, [_open_live()])
    _write_manifest(fdir)
    _grind_touching(project_root, fdir, "src/handler.py")
    _arm(fdir)

    result = foundry_mark_phase_complete("inspect_start", project_root)

    assert result["ok"] is True, result
    assert result["inspect_mode"] == "DELTA"
    assert result["inspect_rule"] == INSPECT_DELTA_RULE

    recorded = _read_state(fdir)["inspect_modes"][-1]
    assert recorded["mode"] == "DELTA"
    assert recorded["decided_by"] == "inspect_start"
    assert "src/handler.py" in recorded["touched_files"]

    assert foundry_next_action(project_root)["inspect_mode"]["mode"] == "DELTA"


def test_a_grind_touching_vocab_records_full_with_rule_verifier_touched(run_env):
    """OT-013 verbatim (second half): 'after one whose diff touches
    schemas/vocab.py it records FULL with rule verifier_touched.'

    The reason is not that vocab is important. A delta roster is only as
    trustworthy as the verifier that runs it, so when the machinery that JUDGES
    the build moves, nothing narrower than everything is honest.
    """
    project_root, fdir = run_env
    _write_state(fdir, phase="F3", cycle=1)
    _write_defects(fdir, [_open_live()])
    _write_manifest(fdir)
    _grind_touching(project_root, fdir, "schemas/vocab.py")
    _arm(fdir)

    result = foundry_mark_phase_complete("inspect_start", project_root)

    assert result["inspect_mode"] == "FULL"
    assert result["inspect_rule"] == "verifier_touched"
    recorded = _read_state(fdir)["inspect_modes"][-1]
    assert "vocab.py" in recorded["rule_detail"]


#: fallout AC-012 / AC-017 / FR-005 / OT-016 — THE NARROWED VERIFIER SET.
#:
#: This roster used to name the single orchestrator module,
#: `foundry_mcp/server.py` and `commands/start.md`. Casting 10 narrowed
#: `VERIFIER_PATH_PATTERNS` in wave 1 (A-005: "Gates/transitions + schemas +
#: vocab + stream contracts"), and GI-009's violation column names the surfaces
#: that must NOT match after the split — "a display, report-seal, spend, halt,
#: directives or teams module (or `commands/*.md`, `teammate.md`,
#: `references/`) still matching a verifier pattern". So the three are gone from
#: here BY DESIGN, and `_NO_LONGER_VERIFIER_SURFACES` below asserts the same
#: narrowing from the other side: a roster that only shrinks is a roster that
#: cannot tell a deliberate removal from an accidental one.
_VERIFIER_SURFACES = [
    "schemas/vocab.py",
    "foundry_mcp/schemas/findings.py",
    # THE FOUR MODULES THAT DECIDE, which is what the monolith row became: the
    # gate ladder, the phase transitions, the INSPECT width decision and the
    # evidence-sweep boundary. The other nine orchestration modules are on the
    # delta side and are named below.
    "foundry_mcp/tools/orchestration/gates.py",
    "foundry_mcp/tools/orchestration/transitions.py",
    "foundry_mcp/tools/orchestration/width.py",
    "foundry_mcp/tools/orchestration/evidence_boundary.py",
    "foundry_mcp/tools/evidence.py",
    "agents/assayer.md",
    "skills/prove/SKILL.md",
]

#: The surfaces that used to force FULL and deliberately no longer do. Driven as
#: the negative case, so the narrowing is asserted in BOTH directions here as
#: well as in `vocab.py`'s own tests: a row silently dropped from the roster
#: above would otherwise be indistinguishable from one the run meant to drop.
_NO_LONGER_VERIFIER_SURFACES = [
    "foundry_mcp/server.py",
    "commands/start.md",
    "agents/teammate.md",
    "foundry_mcp/tools/orchestration/spend.py",
    "foundry_mcp/tools/orchestration/teams.py",
    "foundry_mcp/tools/orchestration/halt.py",
    "foundry_mcp/tools/orchestration/report_seal.py",
    "foundry_mcp/tools/orchestration/directives.py",
]


@pytest.mark.parametrize("path", _VERIFIER_SURFACES)
def test_every_verifier_surface_forces_full(run_env, path):
    """FR-011's list, driven one member at a time.

    Parametrized rather than asserted as a set, because the requirement is about
    the DIFF reaching the decision — a rule that matched the right paths in
    `is_verifier_path` while the decision never consulted it would satisfy a set
    comparison and none of these.
    """
    project_root, fdir = run_env
    _write_state(fdir, phase="F3", cycle=1)
    _write_defects(fdir, [_open_live()])
    _write_manifest(fdir)
    _grind_touching(project_root, fdir, path)
    _arm(fdir)

    result = foundry_mark_phase_complete("inspect_start", project_root)

    assert result["inspect_mode"] == "FULL", (path, result)
    assert result["inspect_rule"] == "verifier_touched", (path, result)


@pytest.mark.parametrize("path", _NO_LONGER_VERIFIER_SURFACES)
def test_a_narrowed_surface_no_longer_forces_full(run_env, path):
    """fallout AC-012 / AC-017 / GI-009 — the narrowing, driven the other way.

    `verifier_touched` is the widest rule there is: it says a verdict already
    reached may now be wrong. A-005 narrowed its subject to the machinery that
    JUDGES — gates, transitions, width, the evidence boundary, the schemas, the
    vocabulary and the verifying streams' own contracts — because a run that
    forced FULL on every edit to the server was forcing FULL on every cycle,
    which is the same as having no width rule at all.

    Driven rather than asserted against `VERIFIER_PATH_PATTERNS`, for the same
    reason the positive case is: a pattern that stopped being consulted would
    satisfy a set comparison and neither of these. The assertion is on the RULE
    and not on the mode, because a DELTA cycle can still be widened to FULL for
    an unrelated reason — what must not happen is this diff claiming to be the
    reason.
    """
    project_root, fdir = run_env
    _write_state(fdir, phase="F3", cycle=1)
    _write_defects(fdir, [_open_live()])
    _write_manifest(fdir)
    _grind_touching(project_root, fdir, path)
    _arm(fdir)

    result = foundry_mark_phase_complete("inspect_start", project_root)

    assert result["inspect_rule"] != "verifier_touched", (path, result)


def test_the_runs_own_spec_forces_full_though_it_is_no_static_pattern(run_env):
    """FR-011 names "the spec", and the spec is not in VERIFIER_PATH_PATTERNS.

    It cannot be: a run's spec lives wherever `state.json.spec_path` says, so a
    static pattern would either miss it or sweep in every unrelated spec.md in
    the tree. It is passed to `is_verifier_path` at call time instead, which
    means the decision has to resolve it — and this is the test that the
    resolution actually happens.
    """
    project_root, fdir = run_env
    _write_state(fdir, phase="F3", cycle=1, spec_path="docs/the-spec.md")
    _write_defects(fdir, [_open_live()])
    _write_manifest(fdir)
    (Path(project_root) / "docs").mkdir()
    (Path(project_root) / "docs" / "the-spec.md").write_text("- FR-001: x\n")
    _git(Path(project_root), "add", "-A")
    _git(Path(project_root), "commit", "-qm", "spec")
    _grind_touching(project_root, fdir, "docs/the-spec.md")
    _arm(fdir)

    result = foundry_mark_phase_complete("inspect_start", project_root)

    assert result["inspect_mode"] == "FULL"
    assert result["inspect_rule"] == "verifier_touched"


def test_a_cleared_ledger_records_delta_not_final_gate(run_env):
    """AC-016 verbatim: '...and DELTA otherwise'. D-068.

    THE ORDINARY CYCLE, which is the one US-004 exists for. The `final_gate` arm
    read `_blocking_defects(fdir)["blocking"] == 0` — true after ANY GRIND that
    fixed what INSPECT filed, which is exactly the state Foundry-Next instructs
    the lead to reach before crossing. So DELTA was unreachable on the guided
    path and every cycle ran the full roster: the thunder-viper behaviour
    US-004 exists to end, reproduced by the rule meant to end it.

    LEAD RULING, GRIND cycle 4: "the next gate is ASSAY" is a fact about the
    TRANSITION, not about the defect ledger. A clean DELTA cycle earns the
    widening re-open (asserted below), and THAT crossing is the final gate.
    """
    project_root, fdir = run_env
    _write_state(fdir, phase="F3", cycle=1)
    _write_defects(fdir, [{**_open_live(), "status": "fixed", "fixed_in_cycle": 1}])
    _write_manifest(fdir)
    _grind_touching(project_root, fdir, "src/handler.py")
    _arm(fdir)

    result = foundry_mark_phase_complete("inspect_start", project_root)

    assert result["inspect_mode"] == "DELTA"
    assert result["inspect_rule"] == "delta"


def test_an_empty_ledger_records_delta_too(run_env):
    """The same proxy, from the other direction: `blocking == 0` was true of a
    run that had filed nothing at all, so a first-GRIND cycle with an empty
    ledger also recorded FULL/final_gate."""
    project_root, fdir = run_env
    _write_state(fdir, phase="F3", cycle=1)
    _write_defects(fdir, [])
    _write_manifest(fdir)
    _grind_touching(project_root, fdir, "src/handler.py")
    _arm(fdir)

    result = foundry_mark_phase_complete("inspect_start", project_root)

    assert result["inspect_mode"] == "DELTA"
    assert result["inspect_rule"] == "delta"


def test_a_latent_only_backlog_records_delta(run_env):
    """And the third shape D-068 drove: a LATENT-only backlog leaves
    `_blocking_defects` at zero, so it too recorded FULL/final_gate. A
    never-reproduced backlog is not a reason to re-verify everything."""
    project_root, fdir = run_env
    _write_state(fdir, phase="F3", cycle=1)
    _write_defects(fdir, [{
        **_open_live(),
        "tier": "LATENT",
        "reproduction_attempted": "drove every caller; none reaches the branch",
    }])
    _write_manifest(fdir)
    _grind_touching(project_root, fdir, "src/handler.py")
    _arm(fdir)

    result = foundry_mark_phase_complete("inspect_start", project_root)

    assert result["inspect_mode"] == "DELTA"
    assert result["inspect_rule"] == "delta"


def test_a_clean_delta_cycle_widens_to_full_before_assay(run_env):
    """AC-016 / US-004: 'every final gate still runs everything at full width.'

    The path DELTA opens onto, driven end to end. A DELTA INSPECT that comes
    back clean does not open ASSAY — `inspect_clean` refuses naming the WIDTH —
    and the lead re-calls `inspect_start` from F2. That crossing advances the
    counter, records FULL, requires the full roster, and THEN inspect_clean
    opens ASSAY. Without this the ruling would trade one non-termination for a
    narrower final gate.

    D-169: the refusal is asserted on the condition the code EVALUATES. It used
    to be asserted on the substring "final_gate", which is the rule this
    particular crossing happens to record and is not what either ASSAY door
    reads — see
    `test_a_full_inspect_opens_assay_whichever_full_rule_recorded_it` below,
    which drives all three FULL rules through both doors.
    """
    project_root, fdir = run_env
    _write_state(fdir, phase="F3", cycle=1)
    _write_defects(fdir, [{**_open_live(), "status": "fixed", "fixed_in_cycle": 1}])
    _write_manifest(fdir)
    _grind_touching(project_root, fdir, "src/handler.py")
    _arm(fdir)

    delta = foundry_mark_phase_complete("inspect_start", project_root)
    assert delta["inspect_mode"] == "DELTA"
    assert delta["cycle"] == 2

    # The DELTA roster runs and comes back clean, which is the whole premise:
    # the refusal below is about the WIDTH, not about unfinished streams.
    for stream in delta["required_streams"]:
        (fdir / f".{stream}-complete").write_text("x\n", encoding="utf-8")

    # The gate is refused while the recorded width is DELTA, and the refusal
    # states the width — the thing tested — with the recorded mode and rule
    # beside it as facts (D-169).
    _arm(fdir)
    refused = foundry_mark_phase_complete("inspect_clean", project_root)
    assert refused.get("ok") is not True
    assert "recorded mode is FULL" in refused["error"], refused["error"]
    assert "ran at DELTA width (rule delta)" in refused["error"], refused["error"]
    assert refused["inspect_mode"] == "DELTA"

    # ...and the widening re-open is the crossing that opens it.
    _arm(fdir)
    widened = foundry_mark_phase_complete("inspect_start", project_root)

    assert widened["ok"] is True, widened
    assert widened["inspect_mode"] == "FULL"
    assert widened["inspect_rule"] == "final_gate"
    assert widened["widened"] is True
    assert widened["cycle"] == 3, "the widening re-open is a cycle of its own"
    assert widened["evidence_sweep"]["scope"] == "full"


def test_the_widening_re_open_is_refused_while_live_defects_are_open(run_env):
    """The ruling's guard on the re-open: open LIVE or unknown-tier defects go
    to GRIND first. Widening an INSPECT over code the run is about to change
    re-verifies a tree that will not exist."""
    project_root, fdir = run_env
    _write_state(fdir, phase="F3", cycle=1)
    _write_defects(fdir, [{**_open_live(), "status": "fixed", "fixed_in_cycle": 1}])
    _write_manifest(fdir)
    _grind_touching(project_root, fdir, "src/handler.py")
    _arm(fdir)
    assert foundry_mark_phase_complete("inspect_start", project_root)["inspect_mode"] == "DELTA"

    # The DELTA INSPECT files a LIVE defect.
    _write_defects(fdir, [{**_open_live(), "id": "D-009", "cycle": 2}])
    _arm(fdir)
    refused = foundry_mark_phase_complete("inspect_start", project_root)

    assert refused.get("ok") is not True, refused
    assert "D-009" in refused["error"]
    assert _current_cycle(fdir) == 2, "a refused transition moves no counter"


def test_the_re_open_is_refused_when_the_cycle_already_ran_full(run_env):
    """The other guard, which is also D-057's second lock: a cycle whose
    recorded width is already FULL has nothing to widen, so a stray second
    `inspect_start` is named as the mistake it is rather than silently
    advancing the run a cycle."""
    project_root, fdir = run_env
    _write_state(fdir, phase="F3", cycle=1)
    _write_defects(fdir, [])
    _write_manifest(fdir)
    _grind_touching(project_root, fdir, "src/schemas/vocab.py")
    _arm(fdir)
    first = foundry_mark_phase_complete("inspect_start", project_root)
    assert first["inspect_mode"] == "FULL"

    _arm(fdir)
    refused = foundry_mark_phase_complete("inspect_start", project_root)

    assert refused.get("ok") is not True, refused
    assert "nothing to widen" in refused["error"]
    assert _current_cycle(fdir) == 2


def test_a_grind_entered_from_assay_feedback_records_full(run_env):
    """AC-016's other `final_gate` arm: the GRIND that just ended was entered
    from ASSAY, TEMPER or NYQUIST feedback, so the gate that rejected the run is
    the one that will read this INSPECT.

    Read from `phase_history`, which `_update_phase` already writes on every
    transition — a second marker recording the same fact is a second thing that
    can drift.
    """
    project_root, fdir = run_env
    _write_state(
        fdir, phase="F3", cycle=1,
        phase_history=[
            {"phase": "F2", "entered_at": "2026-01-01T00:00:00+00:00"},
            {"phase": "F4", "entered_at": "2026-01-01T01:00:00+00:00"},
            {"phase": "F3", "entered_at": "2026-01-01T02:00:00+00:00"},
        ],
    )
    _write_defects(fdir, [_open_live()])
    _write_manifest(fdir)
    _grind_touching(project_root, fdir, "src/handler.py")
    _arm(fdir)

    result = foundry_mark_phase_complete("inspect_start", project_root)

    assert result["inspect_mode"] == "FULL"
    assert result["inspect_rule"] == "final_gate"


def test_an_uncomputable_diff_is_full_and_says_why(run_env):
    """A diff that CANNOT be computed is not an empty diff, and the two must not
    be confused: an empty diff licenses the narrowest roster, an unknown one
    licenses none.

    The recorded rule is `verifier_touched` because `INSPECT_FULL_RULES` is a
    closed vocabulary casting 1 owns and has no member meaning "the diff could
    not be computed" — and inventing one here would be a second copy of that
    vocabulary. The REAL cause is in `rule_detail`, which is why that field
    exists and why this test asserts on it rather than on the rule alone.
    """
    project_root, fdir = run_env
    _write_state(fdir, phase="F3", cycle=1)
    _write_defects(fdir, [_open_live()])
    _write_manifest(fdir)
    # No boundary marker, no clean-TRACE marker, no CAST baseline: nothing to
    # diff from.
    _arm(fdir)

    result = foundry_mark_phase_complete("inspect_start", project_root)

    assert result["inspect_mode"] == "FULL"
    recorded = _read_state(fdir)["inspect_modes"][-1]
    assert recorded["rule"] in INSPECT_FULL_RULES
    assert "could not be computed" in recorded["rule_detail"]


def test_the_recorded_mode_and_rule_are_members_of_the_closed_vocabularies(run_env):
    """The vocabulary is READ, never re-typed (the module's own house rule).

    A width recorded as "full" or a rule recorded as "delta_mode" would render,
    display and roll up perfectly while being a token no reader of
    `INSPECT_MODES` can match.
    """
    project_root, fdir = run_env
    _write_state(fdir, phase="F3", cycle=1)
    _write_defects(fdir, [_open_live()])
    _write_manifest(fdir)
    _grind_touching(project_root, fdir, "src/handler.py")
    _arm(fdir)

    foundry_mark_phase_complete("inspect_start", project_root)

    recorded = _read_state(fdir)["inspect_modes"][-1]
    assert recorded["mode"] in INSPECT_MODES
    assert recorded["rule"] in (INSPECT_FULL_RULES | {INSPECT_DELTA_RULE})


# --------------------------------------------------------------------------- #
# ST-007 / FR-012 / FR-047 / AC-017 / OT-014 — the roster at each width
# --------------------------------------------------------------------------- #


def test_full_mode_requires_every_roster_stream(run_env):
    """AC-017 (first half): 'In FULL mode the streams-complete check requires
    trace, prove, test, research_audit and test01.'"""
    project_root, fdir = run_env
    _write_state(fdir, phase="F1", cycle=0)
    _write_manifest(fdir)
    _arm(fdir)

    foundry_mark_phase_complete("cast", project_root)

    assert set(_read_state(fdir)["inspect_modes"][-1]["required_streams"]) == set(
        FULL_ROSTER_STREAMS
    )
    streams = _check_streams_complete(project_root)
    assert set(streams["required"]) == set(FULL_ROSTER_STREAMS)
    assert streams["inspect_mode"] == "FULL"


def test_full_mode_honours_manifest_stream_skips(run_env):
    """AC-017's exception: 'unless the stream is in manifest.stream_skips'.

    F0.5 records a predictive skip when a run genuinely has no work for a
    stream; requiring it anyway is the grand-vulture deadlock, where a fully
    clean INSPECT blocked on a stream that had nothing to check.
    """
    project_root, fdir = run_env
    _write_state(fdir, phase="F1", cycle=0)
    _write_manifest(fdir, stream_skips=[{"stream_id": "TEST-01", "reason": "no contracts"}])
    _arm(fdir)

    foundry_mark_phase_complete("cast", project_root)

    recorded = _read_state(fdir)["inspect_modes"][-1]
    assert "test01" not in recorded["required_streams"]
    assert recorded["stream_scope"]["test01"]["scope"] == "skipped"
    assert "stream_skips" in recorded["stream_scope"]["test01"]["detail"]
    # ...and the rest of the roster is untouched: a skip removes one stream, it
    # does not narrow the width.
    assert recorded["mode"] == "FULL"
    assert "trace" in recorded["required_streams"]


def test_full_mode_honours_a_research_skipped_record(run_env):
    """AC-017's other exception: 'or a research_skipped record exists'.

    A run that skipped RESEARCH has no research recommendations to audit
    against, so RESEARCH_AUDIT has nothing it could find.
    """
    project_root, fdir = run_env
    _write_state(fdir, phase="F1", cycle=0, research_skipped=True)
    _write_manifest(fdir)
    _arm(fdir)

    foundry_mark_phase_complete("cast", project_root)

    recorded = _read_state(fdir)["inspect_modes"][-1]
    assert "research_audit" not in recorded["required_streams"]
    assert recorded["stream_scope"]["research_audit"]["scope"] == "skipped"


def test_delta_mode_requires_neither_conditional_stream_on_an_untouched_scope(run_env):
    """OT-014 (middle): 'in DELTA mode with an untouched research scope it does
    not [require research_audit or test01]'.

    FR-047: "In DELTA mode Foundry-Next names research_audit and test01 as
    required only for a touched scope, and the streams-complete check enforces
    exactly that set."
    """
    project_root, fdir = run_env
    _write_state(fdir, phase="F3", cycle=1)
    _write_defects(fdir, [_open_live()])
    _write_manifest(fdir)
    # D-207: an UNTOUCHED covered set is not an ABSENT one. This fixture wrote
    # no spec at all, so "test01 covers nothing here" and "this server cannot
    # see what test01 covers" were the same state, and the assertion below was
    # passing on the second while claiming the first. The Contracts table names
    # surfaces `src/handler.py` is not one of, which is the subject.
    _write_spec_with_contracts(fdir, ["FR-001"])
    _grind_touching(project_root, fdir, "src/handler.py")
    _arm(fdir)

    foundry_mark_phase_complete("inspect_start", project_root)

    recorded = _read_state(fdir)["inspect_modes"][-1]
    assert recorded["mode"] == "DELTA"
    for wire in sorted(DELTA_CONDITIONAL_STREAMS):
        assert wire not in recorded["required_streams"], wire
        assert recorded["stream_scope"][wire]["scope"] == "skipped"

    streams = _check_streams_complete(project_root)
    assert set(streams["required"]) == {"trace", "prove", "test"}


def test_delta_mode_requires_research_audit_when_a_research_scope_is_touched(run_env):
    """OT-014 (last): '...and with a touched scope it does'.

    A casting declaring a `research_context` is one whose code was written
    against research recommendations, so its key_files are exactly what an audit
    of those recommendations reads.
    """
    project_root, fdir = run_env
    _write_state(fdir, phase="F3", cycle=1)
    _write_defects(fdir, [_open_live()])
    _write_manifest(
        fdir,
        castings=[{
            "id": 1,
            "key_files": ["src/handler.py"],
            "research_context": "research/auth.md",
        }],
    )
    _grind_touching(project_root, fdir, "src/handler.py")
    _arm(fdir)

    foundry_mark_phase_complete("inspect_start", project_root)

    recorded = _read_state(fdir)["inspect_modes"][-1]
    assert recorded["mode"] == "DELTA"
    assert "research_audit" in recorded["required_streams"]
    assert recorded["stream_scope"]["research_audit"]["scope"] == "full"


def test_delta_mode_requires_test01_when_a_schema_file_is_touched(run_env):
    """ST-007's test01 half: required when 'the diff touches a file they cover'.

    TEST-01 derives property tests from the spec's Contracts table and the
    schemas those surfaces validate against, so a touched `schemas/` file is
    inside its scope by construction.

    Reached via a DELTA cycle whose OTHER changed file is what keeps the width
    narrow — `schemas/` is also a verifier path, so this asserts the conditional
    roster rather than the width. It is driven through `_decide_inspect_mode`
    with the FULL rules already answered, which is the only way to see the
    conditional rung alone.
    """
    project_root, fdir = run_env
    _write_state(fdir, phase="F3", cycle=1)
    _write_defects(fdir, [_open_live()])
    _write_manifest(fdir)
    _write_spec(fdir, ["FR-001"])
    schema_hit = _width._test01_scope_touched(
        fdir, project_root, ["src/foundry_mcp/schemas/x.py"]
    )
    assert schema_hit["touched"] is True, schema_hit
    assert "schemas/" in schema_hit["detail"]
    # D-207: source-independent, so it answers ahead of the three-source ladder
    # and needs neither a declaration nor a registry to be believed.
    assert schema_hit["source"] == "schemas", schema_hit
    # The spec here was READ and names no Contracts surfaces, which is a
    # genuinely empty covered set rather than an unknown one — the one case the
    # D-207 fail-closed rule does not reach.
    miss = _width._test01_scope_touched(fdir, project_root, ["src/handler.py"])
    assert miss["touched"] is False, miss
    assert miss["computable"] is True, miss


# --------------------------------------------------------------------------- #
# D-204 — WHICH FILES TEST-01 COVERS IS A FACT ABOUT THE REGISTRY, NOT A
# SUBSTRING OF THE SPEC.
#
# `_test01_scope_touched` decided "the diff touched a file TEST-01 covers" by
# searching the spec's whole `## Contracts` SECTION — 6147 characters of prose —
# for the touched file's basename or stem, unanchored. Driven at the wire on a
# GRIND diff touching exactly one file, `src/a.py`: `Foundry-Phase(
# 'inspect_start')` recorded DELTA with `test01` REQUIRED and the detail "a
# covered file was touched", because the letter `a` occurs in the section; the
# following `Foundry-Phase('inspect_clean')` then refused "streams incomplete:
# test01". Meanwhile `tools/foundry_report.py` — the module that implements
# CT-014 — read as NOT covered, because the table spells the surface
# `Foundry-Report`.
#
# ST-007 and Locked FR-047 require the conditional streams "only when the diff
# touches a file they cover". A predicate that says yes for a file named nowhere
# and no for the file the row is about is not a narrow reading of that; it is a
# different question, and its answer is persisted into state.json's
# inspect_modes and stream-rollup.json where the F6 per-cycle scope column reads
# it back.
#
# BOTH AXES ARE PINNED BELOW, because fixing one is how this class returns.
# WHICH NAMES — the surface COLUMN's cells, parsed as cells, matched against the
# closed set of tool names the server registers. HOW THEY MAP TO FILES — through
# `server.py`'s `_DISPATCH` binding. Anchoring the cells alone would have left
# `src/fix.py` covered, since CT-004/005/006 all spell `Foundry-Fix`; keeping
# the stem search alone would have left `tools/id.py` covered by the `| ID |`
# header. The stem-collision fixture below holds one path per surface word the
# real table contains.
# --------------------------------------------------------------------------- #

#: A Contracts table whose surface cells collide, by stem, with every path in
#: `_STEM_COLLIDING_PATHS`. Written as the run's spec so the predicate reads a
#: REAL table rather than a stub that could not reproduce the defect.
_CONTRACTS_TABLE = """
## Contracts

| ID     | surface | input | output | errors | citation |
|--------|---------|-------|--------|--------|----------|
| CT-004 | Foundry-Fix (LATENT lane) | defect_id, cycle, authored_by | fixed | refusal | [from A-051] |
| CT-007 | Foundry-Phase inspect_start (evidence sweep) | HEAD of the tree | counter advanced | refusal | [from A-042] |
| CT-008 | Foundry-Gate assay, temper, nyquist, done and Foundry-Phase inspect_clean | defects.json | passes | refusal | [from A-054] |
| CT-012 | Foundry-Next (stall detector) | .last-next-at | waiting notice | none | [from A-021] |
| CT-013 | Foundry-Spend (new tool) | agent, phase, tokens | per-agent record | none | [from A-022] |
| CT-014 | Foundry-Report (new tool) | run artifacts | REPORT.md | refusal | [from A-024] |
| CT-016 | Foundry-Init, Foundry-Phase and Foundry-Next (max_cycles) | the flag | persisted | none | [from A-056] |

## Scope
"""

#: One path per surface word the table above contains, none of them a module
#: the registry binds to any surface. Every one of these read as COVERED before
#: D-204, and the comment on the right is the substring that did it.
_STEM_COLLIDING_PATHS = (
    "src/a.py",        # the letter `a`, which occurs in every row
    "src/fix.py",      # `Foundry-Fix`
    "src/next.py",     # `Foundry-Next`
    "n/gate.py",       # `Foundry-Gate`
    "lib/init.go",     # `Foundry-Init`
    "x/report.rb",     # `Foundry-Report`
    "webapp/spend.ts",  # `Foundry-Spend`
    "tools/id.py",     # the `| ID |` column header
)


def _write_spec_with_contracts(fdir: Path, ids: list[str]) -> None:
    """The run's spec, carrying a real Contracts table beneath its bullets."""
    (fdir / "spec.md").write_text(
        "".join(f"- **{rid}**: the thing works\n" for rid in ids) + _CONTRACTS_TABLE,
        encoding="utf-8",
    )


def test_test01_scope_is_the_registry_binding_not_a_stem_in_the_contracts_prose(
    run_env,
):
    """ST-007 verbatim: research_audit and test01 'are required by the
    streams-complete check only when the diff touches a file they cover, and not
    required otherwise'. FR-047, AC-017, CT-009. D-204.

    The stem-colliding paths are the D-204 evidence set: each one's basename or
    stem occurs inside the Contracts section, and none of them is a module the
    server binds to any surface in it. `tools/foundry_report.py` is the
    converse — named nowhere in the section as a path, and the module
    `_DISPATCH` binds `Foundry-Report` to, which CT-014 is the row for.
    """
    project_root, fdir = run_env
    _write_state(fdir, phase="F3", cycle=1)
    _write_manifest(fdir)
    _write_spec_with_contracts(fdir, ["FR-001"])

    for path in _STEM_COLLIDING_PATHS:
        decision = _width._test01_scope_touched(fdir, project_root, [path])
        assert decision["touched"] is False, (path, decision)
        # D-207: a COMPUTED miss, from the registry, on a run that records
        # self_target — not the asserted negative an unknown set used to give.
        assert decision["computable"] is True, (path, decision)
        assert decision["source"] == "registry", (path, decision)

    hit = _width._test01_scope_touched(
        fdir, project_root, ["src/foundry_mcp/tools/foundry_report.py"]
    )
    assert hit["touched"] is True, hit
    assert hit["source"] == "registry", hit
    # The recorded provenance names the surface AND the file, so a reader of
    # state.json can check the claim instead of believing it.
    assert "CT-014" in hit["detail"], hit
    assert "Foundry-Report" in hit["detail"], hit
    assert "foundry_report.py" in hit["detail"], hit


def test_the_contracts_surface_column_is_read_as_cells_by_its_header(run_env):
    """The WHICH-NAMES axis on its own: the column is located by its header
    name, and only that column's text is searched.

    `input`, `output` and `errors` cells of the same rows name tool surfaces
    too (CT-014's errors column says "Foundry-Phase done refuses"), so a reader
    that took the whole ROW would re-admit surfaces the table does not declare
    for that row. Locating the column by header rather than by index is what
    keeps a table that gains a column ahead of `surface` from silently
    returning `input`.
    """
    project_root, fdir = run_env
    _write_spec_with_contracts(fdir, ["FR-001"])

    rows, problem = _width._contracts_surface_cells(project_root)

    # D-207: the spec was READ, so there is no problem to report — that channel
    # exists so an unreadable spec stops arriving as an empty table.
    assert problem is None, problem
    assert [rid for rid, _ in rows] == [
        "CT-004", "CT-007", "CT-008", "CT-012", "CT-013", "CT-014", "CT-016",
    ], rows
    surfaces = dict(rows)
    assert surfaces["CT-014"] == "Foundry-Report (new tool)"
    # The alignment row is not a row, and nothing outside `## Contracts` is.
    assert all(not set(s) <= set("-: ") for _, s in rows), rows


def test_every_contracts_surface_resolves_to_a_module_through_the_registry():
    """The HOW-THEY-MAP axis on its own: `server.py`'s `_DISPATCH` is what says
    which module implements a surface, and it is READ, not guessed.

    `"Foundry-Report": lambda args: _dispatch_report()` is the case that makes
    the hop mandatory — the real handler is named only inside `_dispatch_report`,
    in a function-local import, so a resolver that stopped at the lambda would
    map CT-014 to `server.py` and leave `tools/foundry_report.py` uncovered,
    which is the second half of what D-204 reported.
    """
    registry = _width._registry_tool_modules()

    assert registry, "the registry is what the mapping IS; an empty one is the bug"
    assert len(registry) >= 20, len(registry)
    report_modules = registry["Foundry-Report"]
    assert any(m.endswith("tools/foundry_report.py") for m in report_modules), (
        report_modules
    )
    # fallout FR-005 / AC-014 / OT-016 — Foundry-Phase RESOLVES ONE DIRECTORY
    # DEEPER NOW. The token handler was the single orchestrator module; the
    # transitions are `tools/orchestration/transitions.py`, and the module is
    # named from the imported module object rather than typed, so the pin
    # follows a future move instead of asserting a path that stopped existing.
    phase_module = Path(_transitions.__file__).name
    assert phase_module == "transitions.py", phase_module
    assert any(
        m.endswith(f"tools/orchestration/{phase_module}")
        for m in registry["Foundry-Phase"]
    ), registry["Foundry-Phase"]
    # No surface resolves to a file outside the plugin's own package.
    for tool, modules in registry.items():
        for module in modules:
            assert "foundry_mcp" in module, (tool, module)


# --------------------------------------------------------------------------- #
# D-207 — A COVERED SET THE SERVER CANNOT COMPUTE IS NOT AN EMPTY COVERED SET.
#
# D-204's fix moved TEST-01's coverage question onto the EXECUTING server's own
# `_DISPATCH` registry, which is a fact about the target on exactly one kind of
# run: one whose target IS this plugin. `_registry_tool_modules` yields foundry
# tool names bound to paths under the executing package's `src/foundry_mcp/`,
# while `_contracts_surface_cells` reads the TARGET run's spec — off a
# self-target those describe different programs and can never intersect, so the
# predicate answered "nothing is covered" for every non-self-targeting run and
# the roster silently dropped `test01` from all of them.
#
# Driven at the wire at 31cc192: a non-self-targeting run whose spec Contracts
# names `POST /api/users (create)` and `DELETE /api/users/:id`, whose casting
# key_file is `src/api/users.py`, and whose GRIND diff touched exactly that
# module recorded DELTA with required_streams ['trace','prove','test'] and
# `stream_scope.test01` = {scope: 'skipped', detail: 'no file test01 covers was
# touched'}. At cb77e83 the RETIRED predicate returned True for that same input,
# so the D-204 fix narrowed a correct behaviour on the path it could not see —
# and every fixture in this file was self-target-shaped, which is why nothing
# caught it.
#
# THE ASYMMETRY IS THE DEFECT: `_decide_inspect_mode` fails CLOSED on an
# uncomputable GRIND diff (FULL, "the GRIND diff could not be computed") and
# this failed OPEN on an uncomputable covered set.
#
# BOTH AXES ARE PINNED BELOW. WHERE THE COVERED SET COMES FROM — declared by the
# run, else the registry on a run recorded as self-targeting, else nowhere. WHAT
# AN UNKNOWABLE SET MEANS — required, scope full, and a detail that says so.
# --------------------------------------------------------------------------- #

#: A Contracts table for a run whose target is an ORDINARY product: the surfaces
#: are HTTP routes, and no tool name the executing server registers appears
#: anywhere in it. This is the table the registry arm cannot answer about.
_PRODUCT_CONTRACTS_TABLE = """
## Contracts

| ID     | surface | input | output | errors | citation |
|--------|---------|-------|--------|--------|----------|
| CT-001 | POST /api/users (create) | body | 201 | 400 | [from A-001] |
| CT-002 | DELETE /api/users/:id | id | 204 | 404 | [from A-002] |

## Scope
"""


def _product_run(project_root: str, fdir: Path, **manifest_extra) -> None:
    """A NON-self-targeting run: a product spec, a product casting, no plugin.

    `self_target=False` is the whole point — it is the fact
    `foundry._self_target_preflight` computes and `foundry_init` records, and
    without it the executing server's registry describes a different program
    than this run's spec does.
    """
    _write_state(fdir, phase="F3", cycle=1, self_target=False)
    _write_defects(fdir, [_open_live()])
    _write_manifest(
        fdir,
        castings=[{"id": 1, "key_files": ["src/api/users.py"]}],
        **manifest_extra,
    )
    (fdir / "spec.md").write_text(
        "- **CT-001**: users can be created\n" + _PRODUCT_CONTRACTS_TABLE,
        encoding="utf-8",
    )


def test_a_non_self_target_run_requires_test01_because_the_set_is_unknowable(
    run_env,
):
    """ST-007 verbatim: research_audit and test01 'are required by the
    streams-complete check only when the diff touches a file they cover, and not
    required otherwise'. FR-047, AC-017, FR-012, CT-009. D-207.

    Driven at `server.call_tool`'s dispatcher on the reported fixture. The
    covered set here cannot be computed at all — the run declares none and the
    executing server is not the target — and an uncomputable set is REQUIRED,
    in the same direction and with the same shape of sentence
    `_decide_inspect_mode` gives an uncomputable GRIND diff.
    """
    from foundry_mcp import server as foundry_server

    project_root, fdir = run_env
    _product_run(project_root, fdir)
    _grind_touching(project_root, fdir, "src/api/users.py")
    _arm(fdir)

    previous_root = foundry_server._project_root
    try:
        foundry_server._project_root = project_root
        result = foundry_server._DISPATCH["Foundry-Phase"]({"phase": "inspect_start"})
    finally:
        foundry_server._project_root = previous_root

    assert result["ok"] is True, result
    recorded = _read_state(fdir)["inspect_modes"][-1]
    assert recorded["mode"] == "DELTA", recorded
    assert "test01" in recorded["required_streams"], recorded
    scope = recorded["stream_scope"]["test01"]
    assert scope["scope"] == "full", scope
    # The detail is the ONLY place the third state is written down, and it is
    # read back by the F6 per-cycle scope column — so it must say the set was
    # not computable, and why, not merely that the stream is required.
    assert "could not be computed" in scope["detail"], scope
    assert "self_target" in scope["detail"], scope


def test_a_declared_test01_scope_answers_for_a_run_the_registry_cannot_describe(
    run_env,
):
    """FR-047 / AC-017: the first source in the precedence order.

    A run that DECLARES what TEST-01 covers is answerable whatever the executing
    server is, so the same non-self-targeting fixture becomes computable and
    both directions are asserted: the declared file requires the stream and
    names the declaration, and a file outside the declaration does not require
    it. Without the second half this would pass on a predicate that had simply
    started saying yes to everything.
    """
    project_root, fdir = run_env
    _product_run(project_root, fdir, test01_scope=["src/api/users.py"])

    hit = _width._test01_scope_touched(fdir, project_root, ["src/api/users.py"])
    assert hit["touched"] is True, hit
    assert hit["source"] == "declared", hit
    assert "test01_scope" in hit["detail"] and "src/api/users.py" in hit["detail"]

    miss = _width._test01_scope_touched(fdir, project_root, ["src/zzz.py"])
    assert miss["touched"] is False, miss
    assert miss["computable"] is True, miss
    assert miss["source"] == "declared", miss


def test_a_declared_scope_on_a_casting_row_is_the_same_declaration(run_env):
    """FR-047, the lead ruling's 'manifest or the decompose-plan's casting
    table, whichever the run already records'.

    Both homes are read, so decompose is not forced into one spelling before it
    has chosen; the answer and its provenance are identical either way.
    """
    project_root, fdir = run_env
    _write_state(fdir, phase="F3", cycle=1, self_target=False)
    _write_manifest(
        fdir,
        castings=[{
            "id": 1,
            "key_files": ["src/api/users.py"],
            "test01_scope": ["src/api/users.py"],
        }],
    )
    (fdir / "spec.md").write_text(_PRODUCT_CONTRACTS_TABLE, encoding="utf-8")

    hit = _width._test01_scope_touched(fdir, project_root, ["src/api/users.py"])
    assert hit["touched"] is True and hit["source"] == "declared", hit
    assert _width._test01_scope_touched(fdir, project_root, ["src/zzz.py"])["touched"] is False


def test_an_unreadable_tool_registry_fails_closed(run_env, monkeypatch):
    """D-207's second fail-open arm: `_registry_tool_modules`'s own
    `except Exception -> {}`.

    Its docstring argued the empty return honest — "without the registry there
    is no evidence of what implements what" — which is exactly right about the
    EVIDENCE and exactly wrong about what to do with it. No evidence is not
    evidence of absence, and the caller was reading it as one.
    """
    project_root, fdir = run_env
    _write_state(fdir, phase="F3", cycle=1)   # self_target, so arm (2) is live
    _write_manifest(fdir)
    _write_spec_with_contracts(fdir, ["FR-001"])
    patch_everywhere(monkeypatch, "_registry_tool_modules", lambda: {})

    decision = _width._test01_scope_touched(
        fdir, project_root, ["src/foundry_mcp/tools/foundry_report.py"]
    )
    assert decision["touched"] is True, decision
    assert decision["computable"] is False, decision
    assert "registry could not be read" in decision["detail"], decision


def test_an_unreadable_contracts_table_fails_closed_but_an_absent_one_does_not(
    run_env,
):
    """The same sentence about the document the covered set is derived FROM.

    `_contracts_surface_cells` returned `[]` for a spec it could not read AND
    for a spec that simply has no Contracts table, and the caller could not tell
    them apart. They are different facts: a spec that was READ and names no
    surfaces covers nothing — TEST-01 SKIPs on it with a reason — while a spec
    that could not be read covers an unknown set.
    """
    project_root, fdir = run_env
    _write_state(fdir, phase="F3", cycle=1)
    _write_manifest(fdir)

    # Read, no table: a computed empty covered set.
    _write_spec(fdir, ["FR-001"])
    rows, problem = _width._contracts_surface_cells(project_root)
    assert (rows, problem) == ([], None)
    absent = _width._test01_scope_touched(fdir, project_root, ["src/handler.py"])
    assert absent["touched"] is False and absent["computable"] is True, absent

    # Unreadable: an unknown one.
    (fdir / "spec.md").write_bytes(b"## Contracts\n\n| ID | surface |\ncaf\xe9\n")
    rows, problem = _width._contracts_surface_cells(project_root)
    assert rows == [] and problem is not None, (rows, problem)
    unknown = _width._test01_scope_touched(fdir, project_root, ["src/handler.py"])
    assert unknown["touched"] is True and unknown["computable"] is False, unknown
    assert "Contracts table could not be read" in unknown["detail"], unknown


def test_the_research_audit_arm_is_unmoved_by_the_test01_source_ladder(run_env):
    """D-207 ADJACENT-PATH TEST.

    The defect's own path is `test01`'s cell of the DELTA conditional roster.
    The ADJACENT path driven here is `research_audit`'s — the OTHER member of
    `DELTA_CONDITIONAL_STREAMS`, decided by `_research_scope_touched` inside the
    SAME loop of the same transition, reading the same `touched` list and
    writing into the same `stream_scope` object. A three-source ladder wired
    into the shared branch instead of into `test01`'s own predicate would move
    this arm too, and it must not: research_audit's covered set comes from the
    run's OWN manifest, so a self-target ladder is not its ladder.

    D-208 CORRECTS THIS DOCSTRING'S PREMISE. It said the research set "is
    therefore computable on every run, self-targeting or not". It is computable
    on every run WHOSE MANIFEST THIS SERVER CAN READ, which is not the same
    claim: with `castings/manifest.json` DELETED, `_load_json` yields `{}` and
    the arm — two-valued at the time — recorded `scope: skipped, detail: "no
    file research_audit covers was touched"`, a negative about a set it had
    never read. The premise was the reason this arm was left two-valued while
    its sibling gained the third value, and that is exactly how the class
    recurred a fourth time. The manifest fixture below is what makes the set
    computable HERE; `test_an_absent_manifest_leaves_the_research_set_unknown`
    drives the case this docstring used to deny.

    Driven on the non-self-targeting fixture, in BOTH directions, so the
    assertion is that the arm still DECIDES rather than that it still answers
    one way.
    """
    project_root, fdir = run_env
    _write_state(fdir, phase="F3", cycle=1, self_target=False)
    _write_defects(fdir, [_open_live()])
    _write_manifest(fdir, castings=[{
        "id": 1,
        "key_files": ["src/api/users.py"],
        "research_context": "research/api.md",
    }])
    (fdir / "spec.md").write_text(_PRODUCT_CONTRACTS_TABLE, encoding="utf-8")

    touched = _width._research_scope_touched(fdir, project_root, ["src/api/users.py"])
    assert touched["touched"] is True, touched
    assert touched["computable"] is True, touched
    assert "research_context" in touched["detail"], touched
    untouched = _width._research_scope_touched(fdir, project_root, ["src/zzz.py"])
    assert untouched["touched"] is False, untouched
    # D-208: a COMPUTED miss — the manifest was read and declares no covered
    # file the diff touched. `computable` is what says so, and it is the whole
    # difference between this and the absent-manifest case.
    assert untouched["computable"] is True, untouched
    assert untouched["source"] == "manifest", untouched
    assert untouched["detail"] == "", untouched

    # And through the transition, where the two arms share a branch: test01 is
    # required for the unknowable-set reason, research_audit for a NAMED match,
    # and the two details are not the same sentence.
    _grind_touching(project_root, fdir, "src/api/users.py")
    _arm(fdir)
    foundry_mark_phase_complete("inspect_start", project_root)

    scope = _read_state(fdir)["inspect_modes"][-1]["stream_scope"]
    assert scope["research_audit"]["scope"] == "full"
    assert "research_context" in scope["research_audit"]["detail"]
    assert "could not be computed" not in scope["research_audit"]["detail"]
    assert "could not be computed" in scope["test01"]["detail"]


# --------------------------------------------------------------------------- #
# D-208 — THE STRUCTURAL FIX: ONE THREE-VALUED ANSWER, THROUGH ONE PATH
#
# The class `inspect-mode-rule-is-a-proxy-not-the-fact` recurred at cycles 3, 5,
# 18, 19 and 20, escalated for three consecutive cycles, because every fix
# repaired ONE arm of the shared DELTA conditional branch and left its sibling
# answering a two-valued question about a covered set it had derived by proxy or
# could not derive at all. These tests pin the two axes the lead ruled on: WHAT
# an arm answers (three values, never two) and HOW it is shared (one path, one
# registry, one checked shape — so a fourth conditional stream cannot arrive
# two-valued).
# --------------------------------------------------------------------------- #


def _no_manifest_run(project_root: str, fdir: Path) -> None:
    """The reported fixture: everything `_product_run` sets up, minus the
    manifest, which is the one uncomputable shape the artifact guard lets
    through (an invalid-JSON or wrong-shape manifest IS named and refuses)."""
    _product_run(project_root, fdir)
    (fdir / "castings" / "manifest.json").unlink()


def test_an_absent_manifest_leaves_the_research_set_unknown_and_requires_it(
    run_env,
):
    """D-208, driven where PROVE drove it: `Foundry-Phase('inspect_start')`
    through `server.call_tool`'s dispatcher, on a non-self-targeting run whose
    `castings/manifest.json` has been DELETED.

    ST-007 verbatim: research_audit and test01 'are required by the
    streams-complete check only when the diff touches a file they cover, and not
    required otherwise'. Locked FR-047 names research_audit FIRST: 'In DELTA
    mode Foundry-Next names research_audit and test01 as required only for a
    touched scope, and the streams-complete check enforces exactly that set.'

    Before the fix this recorded `research_audit` {scope skipped, detail 'no
    file research_audit covers was touched'} — a NEGATIVE about a manifest the
    server had never read, since `_load_json` yields `{}` for an absent document
    — while `test01`, in the SAME `stream_scope` of the SAME transition,
    recorded 'could not be computed'. Two arms of one decision, one input,
    opposite failure directions.

    The CONTROL is the same fixture with its manifest: the touched key_file of a
    casting declaring `research_context` still requires the stream, and for a
    NAMED reason. Without that half this would pass on a predicate that had
    simply started requiring everything.
    """
    from foundry_mcp import server as foundry_server

    project_root, fdir = run_env
    _no_manifest_run(project_root, fdir)
    _grind_touching(project_root, fdir, "src/api/users.py")
    _arm(fdir)

    previous_root = foundry_server._project_root
    try:
        foundry_server._project_root = project_root
        result = foundry_server._DISPATCH["Foundry-Phase"]({"phase": "inspect_start"})
    finally:
        foundry_server._project_root = previous_root

    assert result["ok"] is True, result
    recorded = _read_state(fdir)["inspect_modes"][-1]
    assert recorded["mode"] == "DELTA", recorded
    assert "research_audit" in recorded["required_streams"], recorded
    scope = recorded["stream_scope"]["research_audit"]
    assert scope["scope"] == "full", scope
    # The detail is the ONLY place the third state is written down — it is
    # persisted into state.json and stream-rollup.json and read back by the F6
    # per-cycle scope column — so it must say the set was not computable, and
    # WHY, not merely that the stream is required.
    assert "could not be computed" in scope["detail"], scope
    assert "manifest" in scope["detail"], scope
    # ...and it is the same sentence the sibling arm writes, because it is the
    # same fact about a different set.
    assert "could not be computed" in recorded["stream_scope"]["test01"]["detail"]


def test_the_touched_key_file_control_still_requires_research_audit(run_env):
    """The control D-208's fix must not move: FR-047's positive half.

    Same run, manifest present, casting 1 declaring a `research_context` and the
    diff touching its key_file. The stream is required for a reason that NAMES
    the casting and the file — never the uncomputable-set sentence.
    """
    project_root, fdir = run_env
    _write_state(fdir, phase="F3", cycle=1, self_target=False)
    _write_defects(fdir, [_open_live()])
    _write_manifest(fdir, castings=[{
        "id": 1,
        "key_files": ["src/api/users.py"],
        "research_context": "research/api.md",
    }])
    (fdir / "spec.md").write_text(_PRODUCT_CONTRACTS_TABLE, encoding="utf-8")
    _grind_touching(project_root, fdir, "src/api/users.py")
    _arm(fdir)

    assert foundry_mark_phase_complete("inspect_start", project_root)["ok"] is True
    recorded = _read_state(fdir)["inspect_modes"][-1]
    assert recorded["mode"] == "DELTA", recorded
    assert "research_audit" in recorded["required_streams"], recorded
    scope = recorded["stream_scope"]["research_audit"]
    assert scope["scope"] == "full", scope
    assert "casting 1 declares research_context" in scope["detail"], scope
    assert "could not be computed" not in scope["detail"], scope


def test_a_read_manifest_declaring_no_research_context_is_a_computed_miss(run_env):
    """The line the lead ruling draws: 'absent manifest is unknown, not empty;
    read manifest and no research_context declared is a computed miss.'

    Both directions, so the assertion is that the arm still DECIDES rather than
    that it has started answering one way. A fix that routed every miss to
    unknown would require research_audit on every DELTA cycle of every run and
    would delete US-004's saving outright.
    """
    project_root, fdir = run_env
    _product_run(project_root, fdir)          # manifest present, no research_context

    miss = _width._research_scope_touched(fdir, project_root, ["src/api/users.py"])
    assert miss["touched"] is False, miss
    assert miss["computable"] is True, miss
    assert miss["source"] == "manifest", miss
    assert miss["detail"] == "", miss

    (fdir / "castings" / "manifest.json").unlink()
    unknown = _width._research_scope_touched(fdir, project_root, ["src/api/users.py"])
    assert unknown["touched"] is True, unknown
    assert unknown["computable"] is False, unknown
    assert unknown["source"] == "unknown", unknown
    assert "could not be computed" in unknown["detail"], unknown


def test_every_delta_conditional_arm_answers_the_shared_three_valued_shape(
    run_env,
):
    """The pin that iterates `DELTA_CONDITIONAL_STREAMS`.

    ST-007 governs a SET of streams, not two named ones, so the property is
    asserted over the vocabulary rather than over `research_audit` and `test01`
    by name — an arm added to the set later is covered by this test the day it
    is added, which is the whole of the structural fix's second axis.

    Every arm carries the same four keys, and each of the three values is
    REACHABLE through the same shared path: a computed hit, a computed miss, and
    an unknown when the document its covered set comes from is gone.
    """
    project_root, fdir = run_env
    _product_run(project_root, fdir, test01_scope=["src/api/users.py"])
    _write_manifest(
        fdir,
        castings=[{
            "id": 1,
            "key_files": ["src/api/users.py"],
            "research_context": "research/api.md",
            "test01_scope": ["src/api/users.py"],
        }],
    )

    for wire in sorted(DELTA_CONDITIONAL_STREAMS):
        hit = _width._delta_conditional_scope(
            fdir, project_root, wire, ["src/api/users.py"]
        )
        assert set(_width._CONDITIONAL_ANSWER_KEYS) <= set(hit), (wire, hit)
        assert hit["touched"] is True and hit["computable"] is True, (wire, hit)
        assert hit["detail"], (wire, hit)

        miss = _width._delta_conditional_scope(fdir, project_root, wire, ["src/zzz.py"])
        assert set(_width._CONDITIONAL_ANSWER_KEYS) <= set(miss), (wire, miss)
        assert miss["touched"] is False and miss["computable"] is True, (wire, miss)
        assert miss["detail"] == "", (wire, miss)

    # The third value, for BOTH arms at once: with the manifest gone, neither
    # covered set can be computed and neither may answer a negative.
    (fdir / "castings" / "manifest.json").unlink()
    for wire in sorted(DELTA_CONDITIONAL_STREAMS):
        unknown = _width._delta_conditional_scope(
            fdir, project_root, wire, ["src/api/users.py"]
        )
        assert set(_width._CONDITIONAL_ANSWER_KEYS) <= set(unknown), (wire, unknown)
        assert unknown["touched"] is True, (wire, unknown)
        assert unknown["computable"] is False, (wire, unknown)
        assert unknown["source"] == "unknown", (wire, unknown)
        assert f"the set of files {wire} covers could not be computed" in (
            unknown["detail"]
        ), (wire, unknown)

    # And every member of the vocabulary is answerable at all — a conditional
    # stream with no registered predicate is the next instance of this class
    # waiting to happen, so the registry is asserted to cover the set.
    assert DELTA_CONDITIONAL_STREAMS <= set(_width._DELTA_CONDITIONAL_PREDICATES)


def test_a_conditional_stream_with_no_predicate_is_required_not_skipped(
    run_env, monkeypatch
):
    """The fourth-stream guard, driven.

    `DELTA_CONDITIONAL_STREAMS` is casting 1's vocabulary and this module's
    registry is separate from it by design. So the failure that has recurred
    five times is asserted from its far end: a member of the vocabulary this
    server has no predicate for is REQUIRED with a detail naming that fact, and
    is never skipped on a negative nobody computed.
    """
    project_root, fdir = run_env
    _product_run(project_root, fdir)

    answer = _width._delta_conditional_scope(
        fdir, project_root, "a_stream_added_later", ["src/api/users.py"]
    )
    assert answer["touched"] is True, answer
    assert answer["computable"] is False, answer
    assert "no registered predicate" in answer["detail"], answer


def test_a_two_valued_arm_reads_as_unknown_rather_than_as_a_negative(
    run_env, monkeypatch
):
    """The shape is CHECKED, not assumed — the second axis of the structural
    fix, driven by supplying exactly the shape D-208 was filed on.

    A predicate registered with the pre-fix two-valued `{touched, detail}`
    answer has not said whether its covered set was computed, and the shared
    path must read that as the unknown rather than reaching into it for
    `touched` and believing the False. Without this assertion the registry is
    just a lookup table and the next arm added is free to be two-valued again.
    """
    project_root, fdir = run_env
    _product_run(project_root, fdir)
    monkeypatch.setitem(
        _width._DELTA_CONDITIONAL_PREDICATES,
        "research_audit",
        lambda fdir, project_root, touched: {"touched": False, "detail": ""},
    )

    answer = _width._delta_conditional_scope(
        fdir, project_root, "research_audit", ["src/api/users.py"]
    )
    assert answer["touched"] is True, answer
    assert answer["computable"] is False, answer
    assert "computable" in answer["detail"] and "source" in answer["detail"], answer


def test_inspect_clean_demands_the_stream_the_unknown_research_set_required(
    run_env,
):
    """D-208 ADJACENT-PATH TEST.

    The defect's own path is the `inspect_start` transition writing
    `research_audit`'s cell of the DELTA conditional roster. The ADJACENT path
    driven here is `Foundry-Phase('inspect_clean')` — a DIFFERENT transition,
    reached later in the same cycle, which does not decide the roster at all but
    CONSUMES it through `_check_streams_complete`. It is the path most exposed
    by this fix: a stream that becomes required must be one the run can actually
    satisfy, or the fix trades a false skip for a deadlock at the next door.

    Both halves are asserted, because "it refuses" and "it can be cleared" are
    different claims and only the pair is the requirement (AC-017 / FR-047).
    """
    project_root, fdir = run_env
    _no_manifest_run(project_root, fdir)
    _grind_touching(project_root, fdir, "src/api/users.py")
    _arm(fdir)

    assert foundry_mark_phase_complete("inspect_start", project_root)["ok"] is True
    recorded = _read_state(fdir)["inspect_modes"][-1]
    cycle = recorded["cycle"]
    assert "research_audit" in recorded["required_streams"], recorded

    streams = _check_streams_complete(project_root)
    assert streams["complete"] is False, streams
    assert "research_audit" in streams["missing"], streams

    _arm(fdir)
    refusal = foundry_mark_phase_complete("inspect_clean", project_root)
    assert refusal.get("ok") is not True, refusal
    assert "research_audit" in json.dumps(refusal), refusal

    # ...and the door opens once the roster it was actually given is checked.
    # The GRIND's own defect is closed alongside, because `inspect_clean`
    # refuses on an open LIVE defect too (CT-008) and a door held shut by two
    # reasons proves nothing about either.
    _mark(project_root, "prove", cycle, len(recorded["prove_sample"]),
          len(recorded["prove_sample"]))
    for wire in recorded["required_streams"]:
        if wire != "prove":
            _mark(project_root, wire, cycle, 1, 1)
    assert _check_streams_complete(project_root)["complete"] is True
    _write_defects(fdir, [])

    _arm(fdir)
    clean = foundry_mark_phase_complete("inspect_clean", project_root)
    assert "research_audit" not in json.dumps(clean), clean
    # What is left is the door's OWN pre-existing refusal — a DELTA cycle does
    # not open ASSAY — and naming it here is the point: the stream requirement
    # this fix created appears and disappears exactly with its marker, and it
    # displaces nothing else this door enforces.
    assert clean.get("inspect_mode") == "DELTA", clean
    assert "DELTA width" in clean["error"], clean


def test_a_research_skipped_record_outranks_an_unknown_covered_set(run_env):
    """The second adjacent branch through the changed code, and the one the fix
    could most easily have broken.

    AC-017's exception — 'or a research_skipped record exists' — is a fact the
    RUN recorded about itself, so it outranks anything the covered set can say,
    an unknown one included: a run that skipped research has no research to have
    deviated from, whatever moved. The recorded detail says exactly that, in the
    same words the FULL branch writes for the same fact, rather than 'no file
    research_audit covers was touched' — which would be a reason this branch
    never evaluated, and recording an unevaluated reason is D-208 itself.
    """
    project_root, fdir = run_env
    _no_manifest_run(project_root, fdir)
    _write_state(fdir, phase="F3", cycle=1, self_target=False, research_skipped=True)
    _grind_touching(project_root, fdir, "src/api/users.py")
    _arm(fdir)

    assert foundry_mark_phase_complete("inspect_start", project_root)["ok"] is True
    recorded = _read_state(fdir)["inspect_modes"][-1]
    assert recorded["mode"] == "DELTA", recorded
    assert "research_audit" not in recorded["required_streams"], recorded
    scope = recorded["stream_scope"]["research_audit"]
    assert scope["scope"] == "skipped", scope
    assert scope["detail"] == "research_skipped record", scope


def test_a_stem_colliding_grind_diff_leaves_test01_unrequired_over_mcp(run_env):
    """D-204 driven where it was reported: `Foundry-Phase('inspect_start')`
    through the MCP dispatcher, on a GRIND that touched exactly `src/a.py`.

    ST-007's guard is that the conditional streams are required ONLY when the
    diff touches a file they cover. Before the fix this recorded
    `required_streams` including `test01` with the detail 'a covered file was
    touched', and `Foundry-Phase('inspect_clean')` then refused 'streams
    incomplete: test01' — a stream no rule required, blocking the transition on
    work that had no reason to be dispatched.
    """
    from foundry_mcp import server as foundry_server

    project_root, fdir = run_env
    _write_state(fdir, phase="F3", cycle=1, spec_path="")
    _write_defects(fdir, [_open_live()])
    _write_manifest(fdir)
    _write_spec_with_contracts(fdir, ["FR-001", "FR-002"])
    _grind_touching(project_root, fdir, "src/a.py")
    _arm(fdir)

    previous_root = foundry_server._project_root
    try:
        foundry_server._project_root = project_root
        result = foundry_server._DISPATCH["Foundry-Phase"]({"phase": "inspect_start"})
    finally:
        foundry_server._project_root = previous_root

    assert result["ok"] is True, result
    recorded = _read_state(fdir)["inspect_modes"][-1]
    assert recorded["mode"] == "DELTA", recorded
    assert recorded["touched_files"] == ["src/a.py"], recorded
    assert "test01" not in recorded["required_streams"], recorded
    assert recorded["stream_scope"]["test01"]["scope"] == "skipped"

    # And the door that refused: with the roster it was actually given checked,
    # `inspect_clean` no longer names `test01` at all.
    cycle = recorded["cycle"]
    _mark(project_root, "prove", cycle, len(recorded["prove_sample"]),
          len(recorded["prove_sample"]))
    for wire in ("trace", "test"):
        _mark(project_root, wire, cycle, 1, 1)
    assert _check_streams_complete(project_root)["complete"] is True

    _arm(fdir)
    clean = foundry_mark_phase_complete("inspect_clean", project_root)
    assert "test01" not in json.dumps(clean), clean


def test_streams_complete_reads_the_recorded_roster_and_never_recomputes_it(run_env):
    """GI-008's named violation: 'a streams-complete check that reads a roster
    nothing recorded because Next was skipped'.

    Driven by RECORDING one roster and then moving the tree underneath it. The
    check must still answer with what the cycle was told to run, because the
    mode is a function of a diff measured at the boundary and by now the tree
    has moved on. A check that re-derived would answer about a cycle that never
    ran.
    """
    project_root, fdir = run_env
    _write_state(fdir, phase="F3", cycle=1)
    _write_defects(fdir, [_open_live()])
    _write_manifest(fdir)
    _grind_touching(project_root, fdir, "src/handler.py")
    _arm(fdir)
    foundry_mark_phase_complete("inspect_start", project_root)
    recorded = list(_read_state(fdir)["inspect_modes"][-1]["required_streams"])

    # The tree moves: a verifier file changes AFTER the decision was recorded.
    # A recomputing check would now demand the FULL roster for a cycle that was
    # dispatched at DELTA.
    _grind_touching(project_root, fdir, "schemas/vocab.py")

    assert _check_streams_complete(project_root)["required"] == recorded


def test_a_run_with_no_recorded_decision_is_refused_not_read_as_full(run_env):
    """D-117 / GI-008 / GI-009 — the pre-width fallback IS the violation.

    This function used to fall back to the roster it required before the width
    existed — trace/prove/test — and the fallback was argued for as archive
    compatibility. GI-008 names it outright: "a streams-complete check that
    reads a roster nothing recorded". The fallback silently dropped
    `research_audit` and `test01` from every mode-less INSPECT and reported
    complete, which is how ASSAY came to open on a three-stream roster.

    The refusal names the missing record AND the transition that writes it,
    because "there is no recorded width" is not an action.
    """
    project_root, fdir = run_env
    _write_state(fdir, phase="F2", cycle=2)
    _write_manifest(fdir)

    streams = _check_streams_complete(project_root)

    assert streams["complete"] is False, streams
    assert streams["unrecorded_width"] is True, streams
    assert streams["required"] == []
    assert "inspect_mode" in streams["missing"]
    assert "no recorded width" in streams["reason"]
    assert "inspect_start" in streams["hint"]
    assert _current_inspect_mode(fdir) is None


def test_an_unrecorded_inspect_width_is_refused_at_every_door(run_env):
    """D-117 driven at all five consumers on ONE run state.

    Each door degraded permissively on its own terms and each was individually
    defensible; together they admitted a mode-less INSPECT as full width
    everywhere at once. Driven end to end on a legacy-shaped archive with stale
    committed evidence: streams-complete reported complete, `inspect_clean`
    returned ok and moved the run to F4, and `Foundry-Gate('assay')` PASSED
    carrying `inspect_ran_at_full_width (mode=unrecorded rule=unrecorded)
    ok=True` — the check naming, in its own string, the fact that made its
    verdict false.

    Asserted as one test over the whole set, because a fix wired into four of
    five doors leaves the fifth deciding on a different definition of "full
    width", which is the shape D-117 reports.
    """
    project_root, fdir = run_env
    _write_state(fdir, phase="F2", cycle=2)
    _write_manifest(fdir)
    _write_spec(fdir, ["FR-001"])
    _write_defects(fdir, [])
    for stream in ("trace", "prove", "test"):
        (fdir / f".{stream}-complete").write_text(
            "2020-01-01T00:00:00+00:00 cycle=2\nitems_checked=1\nitems_total=1\n"
            "coverage=100%\nfindings=0\n",
            encoding="utf-8",
        )
    assert _current_inspect_mode(fdir) is None

    # 1. the streams-complete check
    streams = _check_streams_complete(project_root)
    assert streams["complete"] is False, streams

    # 2. Foundry-Phase('inspect_clean')
    _arm(fdir)
    clean = foundry_mark_phase_complete("inspect_clean", project_root)
    assert clean.get("ok") is not True, clean
    assert clean["unrecorded_width"] is True, clean
    assert "no recorded width" in clean["error"], clean
    assert "inspect_start" in clean["hint"], clean
    assert _read_state(fdir)["phase"] == "F2", "the run must not reach F4"

    # 3. Foundry-Gate('assay')
    _arm(fdir)
    gate = _gates.foundry_gate("assay", project_root)
    assert gate["passed"] is False, gate
    width = next(
        c for c in gate["checklist"]
        if c["check"].startswith("inspect_ran_at_full_width")
    )
    assert width["ok"] is False, width
    assert "unrecorded" in width["check"], width

    # 4. the trace-skip stamp — no auto-stamp, whatever the legacy markers say.
    #
    # fallout GI-008 / GI-009 / GI-033 (ruling item 5). `width._maybe_skip_trace`
    # is DELETED: Foundry-Next only reports, so the fact is decided by
    # `width._trace_skip_from_width` at the transition that decides the width and
    # recorded in the `inspect_modes` entry under `trace_skip`, and
    # `guidance._stamp_trace_skip` READS it through the leaf. The door moved; the
    # property this step pins did not. There is no recorded entry here at all, so
    # there is no `trace_skip` field, and an entry with no field licenses NO
    # answer about TRACE's scope — it returns None and stamps nothing. That is
    # D-117's direction rather than a gap, and it is a strictly stronger form of
    # what the old fence returned (a dict saying skip False): the old shape had
    # to be read to be safe, this one cannot say anything at all. The filesystem
    # assertion below is unchanged and is still the real subject.
    (fdir / ".trace-complete").unlink(missing_ok=True)
    (fdir / ".trace-clean-at").write_text(
        _git(Path(project_root), "rev-parse", "HEAD") + "\n", encoding="utf-8"
    )
    decision = _guidance._stamp_trace_skip(fdir)
    assert decision is None, decision
    assert not (fdir / ".trace-complete").exists(), (
        "TRACE was auto-stamped complete on a width nothing recorded"
    )

    # 5. Foundry-Next names the transition that records one, and dispatches no
    #    stream roster it cannot know.
    action = _guidance._compute_next_action(project_root)
    assert action["action"] == "record_inspect_width", action
    assert "inspect_start" in action["instructions"], action
    assert action["details"]["unrecorded_width"] is True


def test_an_unrecorded_width_sweeps_the_whole_corpus_not_a_delta_slice(run_env):
    """D-117's fifth consumer: the sweep scope is a CLAIM about the width.

    "Only these logs can have been invalidated by this GRIND" rests on the width
    decision, so an entry carrying no mode must not also narrow the sweep. The
    honest reading of an unknown width is the whole corpus — the same reading
    `_decide_inspect_mode` already gives an uncomputable diff.
    """
    project_root, fdir = run_env
    _write_manifest(fdir)
    seen: dict = {}

    def _fake_select(*, manifest, evidence_dir, touched_files, full):
        seen["full"] = full
        return []

    import foundry_mcp.tools.evidence as evidence_mod

    original = evidence_mod.select_sweep_scope
    evidence_mod.select_sweep_scope = _fake_select
    try:
        _evidence_boundary._sweep_evidence_at_boundary(
            fdir, project_root, {"touched_files": ["src/handler.py"]}, full=False
        )
    finally:
        evidence_mod.select_sweep_scope = original

    assert seen["full"] is True, (
        "an entry with no recorded mode narrowed the sweep to a delta slice"
    )


# --------------------------------------------------------------------------- #
# D-102 / FR-011 / AC-016 / ST-006 — the spec has TWO spellings and both fire
# --------------------------------------------------------------------------- #


def test_a_grind_touching_the_declared_spec_path_records_verifier_touched(run_env):
    """FR-011 verbatim (Locked): 'FULL when: ... or the GRIND diff touches
    vocab.py, schemas/, gate/orchestrator code, agent/skill prose, or the spec.'

    The rule never fired on the spec. `_resolve_spec_path` PREFERS
    `<run_dir>/spec.md` and only falls back to `state.json.spec_path`, so on the
    ordinary run — which has both, because init copies the spec into the archive
    — the singular resolver returned the archive copy and the authored spec was
    invisible. Driven at three spec locations: a GRIND diff whose ONLY touched
    file was the path `state.json` records as `spec_path` opened the next
    INSPECT at DELTA, rule `delta`, while every other FULL trigger fired
    correctly.
    """
    project_root, fdir = run_env
    _write_manifest(fdir)
    _write_defects(fdir, [_open_live()])

    spec_rel = "forge-specs/subject/spec.md"
    target = Path(project_root) / spec_rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("# Spec\n- **FR-001**: the thing works\n", encoding="utf-8")
    _write_state(fdir, phase="F3", cycle=1, spec_path=spec_rel)
    # The archive copy exists too, which is the whole point: the run has BOTH
    # spellings and the old resolver could only ever return one of them.
    _write_spec(fdir, ["FR-001"])

    _grind_touching(project_root, fdir, spec_rel)
    _arm(fdir)
    result = foundry_mark_phase_complete("inspect_start", project_root)

    assert result["ok"] is True, result
    assert result["inspect_mode"] == "FULL", result
    assert result["inspect_rule"] == "verifier_touched", result
    entry = _read_state(fdir)["inspect_modes"][-1]
    assert entry["touched_files"] == [spec_rel], entry
    assert spec_rel in entry["rule_detail"], entry


def test_the_archived_spec_spelling_answers_the_predicate_too(run_env):
    """The other spelling, so the fix cannot be a swap of which one is checked.

    Asserted through the predicate rather than through a GRIND diff, because
    this repo's own `.gitignore` carries `/foundry-archive/` (the fixture
    mirrors it, for the reason `run_env` states) — the archive copy cannot
    appear in a diff HERE. It can in a target repo that does not ignore the
    archive, and C-1 names both paths, so both are supplied and both must
    answer. A fix that preferred the declared path over the archived one would
    close D-102 by reopening it on the other side.
    """
    project_root, fdir = run_env
    _write_state(fdir, phase="F2", cycle=1, spec_path="forge-specs/subject/spec.md")
    declared = Path(project_root) / "forge-specs" / "subject" / "spec.md"
    declared.parent.mkdir(parents=True, exist_ok=True)
    declared.write_text("# Spec\n", encoding="utf-8")
    _write_spec(fdir, ["FR-001"])

    from foundry_mcp.schemas.vocab import is_verifier_path

    spellings = _width._spec_relative_paths(project_root)
    archived = next(s for s in spellings if s.endswith(f"{RUN_NAME}/spec.md"))
    assert any(is_verifier_path("forge-specs/subject/spec.md", s) for s in spellings)
    assert any(is_verifier_path(archived, s) for s in spellings)
    # Neither is a static VERIFIER_PATH_PATTERNS member — they match only
    # because the caller supplied them, which is the seam D-102 is about.
    assert not is_verifier_path("forge-specs/subject/spec.md")
    assert not is_verifier_path(archived)


def test_both_spec_spellings_are_offered_to_the_verifier_predicate(run_env):
    """The seam itself, so a caller cannot silently go back to one spelling.

    `is_verifier_path` takes ONE `spec_path` and lives in casting 1's vocab.py,
    whose signature is LOCKED by C-1 — so the two spellings have to be supplied
    BY THE CALLER, and this asserts the caller has both to supply.
    """
    project_root, fdir = run_env
    _write_state(fdir, phase="F2", cycle=1, spec_path="forge-specs/subject/spec.md")
    declared = Path(project_root) / "forge-specs" / "subject" / "spec.md"
    declared.parent.mkdir(parents=True, exist_ok=True)
    declared.write_text("# Spec\n", encoding="utf-8")
    _write_spec(fdir, ["FR-001"])

    spellings = _width._spec_relative_paths(project_root)

    assert "forge-specs/subject/spec.md" in spellings, spellings
    assert any(s.endswith(f"{RUN_NAME}/spec.md") for s in spellings), spellings
    assert len(spellings) == len(set(spellings)), spellings


# --------------------------------------------------------------------------- #
# FR-013 / FR-033 / AC-018 / AC-019 / OT-015 — the DELTA roster's contents
# --------------------------------------------------------------------------- #


def test_the_prove_sample_is_the_fixed_rows_plus_a_seeded_draw(run_env):
    """AC-018 verbatim: 'In DELTA mode the PROVE roster names the matrix rows
    tied to the defects fixed in the preceding GRIND plus ten rows drawn with a
    seed derived from the cycle number.'

    The tied rows come first because they are the rows whose verdicts the fixes
    could actually have changed; the sample is a floor on coverage beyond them,
    so a regression outside the fixed rows is still found — just not in one
    cycle.
    """
    project_root, fdir = run_env
    ids = [f"FR-{n:03d}" for n in range(1, 41)]
    _write_spec(fdir, ids)
    _write_state(fdir, phase="F3", cycle=1, spec_path="")
    _write_defects(fdir, [
        _open_live("D-001"),
        {**_open_live("D-002"), "status": "fixed", "fixed_in_cycle": 1,
         "spec_ref": "FR-007"},
    ])
    _write_manifest(fdir)
    _grind_touching(project_root, fdir, "src/handler.py")
    _arm(fdir)

    foundry_mark_phase_complete("inspect_start", project_root)

    sample = _read_state(fdir)["inspect_modes"][-1]["prove_sample"]
    assert sample[0] == "FR-007", sample
    assert len(sample) == 1 + PROVE_DELTA_SAMPLE_SIZE, sample
    assert len(set(sample)) == len(sample), "a row must not be drawn twice"
    assert set(sample) <= set(ids)


def test_the_same_cycle_number_yields_the_same_ten_rows(run_env):
    """AC-018's second half: 'the same cycle number yields the same ten rows.'

    FR-033 makes the PRNG the implementer's choice with one proviso — the sample
    must be reproducible from the cycle number ALONE. So this drives the
    computation twice against DIFFERENT ledger orderings: a sample that depended
    on dict iteration, ledger order or filesystem order would pass a
    same-inputs-twice test and fail this one.
    """
    project_root, fdir = run_env
    ids = [f"FR-{n:03d}" for n in range(1, 41)]
    _write_spec(fdir, ids)
    _write_state(fdir, phase="F3", cycle=1, spec_path="")
    _write_defects(fdir, [_open_live("D-001")])

    first = _width._prove_delta_sample(fdir, project_root, 7)

    _write_defects(fdir, [_open_live("D-009"), _open_live("D-001")])
    second = _width._prove_delta_sample(fdir, project_root, 7)

    assert first == second, (first, second)
    assert first != _width._prove_delta_sample(fdir, project_root, 8), (
        "a different cycle must draw a different sample, or the seed is not the "
        "cycle"
    )


def test_two_next_calls_in_one_cycle_report_the_same_sample(run_env):
    """OT-015 verbatim: 'Two Foundry-Next calls in the same DELTA cycle list the
    same ten sampled PROVE rows.'

    True by construction, and that IS the requirement: the sample is drawn ONCE,
    at the transition, and recorded. Foundry-Next reads it. Two calls could only
    disagree if one of them were drawing.
    """
    project_root, fdir = run_env
    _write_spec(fdir, [f"FR-{n:03d}" for n in range(1, 41)])
    _write_state(fdir, phase="F3", cycle=1, spec_path="")
    _write_defects(fdir, [_open_live()])
    _write_manifest(fdir)
    _grind_touching(project_root, fdir, "src/handler.py")
    _arm(fdir)
    foundry_mark_phase_complete("inspect_start", project_root)

    first = foundry_next_action(project_root)["inspect_mode"]["prove_sample"]
    second = foundry_next_action(project_root)["inspect_mode"]["prove_sample"]

    assert first == second
    assert len(first) == PROVE_DELTA_SAMPLE_SIZE


def test_test_runs_full_and_cold_even_at_delta_width(run_env):
    """AC-019 verbatim: 'In DELTA mode TEST still runs full and cold from a
    clean worktree and TRACE runs over the symbols the GRIND commits touched.'

    TEST is the stream a narrow INSPECT is most exposed to: a narrowed suite
    cannot see a regression the GRIND opened in a module it did not edit. So it
    is the one member of the DELTA roster whose scope stays `full`.
    """
    project_root, fdir = run_env
    _write_state(fdir, phase="F3", cycle=1)
    _write_defects(fdir, [_open_live()])
    _write_manifest(fdir)
    _grind_touching(project_root, fdir, "src/handler.py")
    _arm(fdir)

    foundry_mark_phase_complete("inspect_start", project_root)

    scope = _read_state(fdir)["inspect_modes"][-1]["stream_scope"]
    assert scope["test"]["scope"] == "full"
    assert "cold" in scope["test"]["detail"]
    assert scope["trace"]["scope"] == "delta"
    assert scope["prove"]["scope"] == "delta"


# --------------------------------------------------------------------------- #
# GI-008 / GI-009 / CT-009 / FR-049 — Foundry-Next reports and never decides
# --------------------------------------------------------------------------- #


def test_compute_next_action_contains_no_width_decision(run_env):
    """GI-009: 'No decision ever lives in Foundry-Next.'

    Asserted against the SOURCE, because the claim is about where a decision can
    be made and not about what one call happened to return. A `_compute_next_action`
    that called the decider would return the right answer today and be the
    violation GI-008 names — a mode computed at display time is a mode no
    artifact holds, and two calls either side of a commit would disagree.
    """
    import inspect

    source = inspect.getsource(_guidance._compute_next_action)
    assert "_decide_inspect_mode" not in source
    assert "_record_inspect_mode" not in source

    # And the reporting path reads the recorded entry through the ONE reader.
    #
    # That reader is now `_recorded_inspect_mode`, guidance's own thin read of
    # `foundry_state.current_inspect_mode` (casting 10's leaf), and NOT
    # `width._current_inspect_mode`: GI-033 refuses this lifecycle module the
    # verifier set with no exception, and a module that can call the width
    # decider is a module that can take one. The pinned name moved with that
    # read; the claim did not. The leaf's own spelling is deliberately not
    # pinned here — it is one hop further in, inside the wrapper rather than in
    # the source this reads, and the boundary guard already refuses the edge
    # that would let a decision hide in that hop.
    reporter = inspect.getsource(_guidance.foundry_next_action)
    assert "_recorded_inspect_mode" in reporter
    assert "_decide_inspect_mode" not in reporter


def test_next_reports_the_recorded_decision_whenever_it_is_called(run_env):
    """CT-009: the decision is 'recorded in state and stream-rollup at the
    transition and displayed by every later Foundry-Next'.

    Every LATER call — not just the first. A decision that only the next call
    reports is a decision the lead loses by looking twice.
    """
    project_root, fdir = run_env
    _write_state(fdir, phase="F3", cycle=1)
    _write_defects(fdir, [_open_live()])
    _write_manifest(fdir)
    # D-207: the roster asserted below is the one a COMPUTED covered set
    # produces; without a spec the run has no covered set to compute and
    # test01 is required for that reason instead.
    _write_spec_with_contracts(fdir, ["FR-001"])
    _grind_touching(project_root, fdir, "src/handler.py")
    _arm(fdir)
    foundry_mark_phase_complete("inspect_start", project_root)

    for _ in range(3):
        reported = foundry_next_action(project_root)["inspect_mode"]
        assert reported["mode"] == "DELTA"
        assert reported["rule"] == INSPECT_DELTA_RULE
        assert reported["required_streams"] == ["trace", "prove", "test"]


def test_the_decision_is_mirrored_into_the_cycle_rollup(run_env):
    """C-6: `cycles[<cycle>]` gains `inspect_mode`, `inspect_rule` and
    `stream_scope`.

    The roll-up is what the F6 report reads for "the FULL or DELTA decision per
    cycle", so a decision recorded only in state.json would be invisible to the
    report that has to list it.
    """
    project_root, fdir = run_env
    _write_state(fdir, phase="F3", cycle=1)
    _write_defects(fdir, [_open_live()])
    _write_manifest(fdir)
    _grind_touching(project_root, fdir, "src/handler.py")
    _arm(fdir)

    foundry_mark_phase_complete("inspect_start", project_root)

    rollup = json.loads(
        (fdir / _streams.ROLLUP_FILENAME).read_text(encoding="utf-8")
    )["cycles"]["2"]
    assert rollup["inspect_mode"] == "DELTA"
    assert rollup["inspect_rule"] == INSPECT_DELTA_RULE
    assert rollup["stream_scope"]["test"]["scope"] == "full"


def test_the_decider_takes_the_transition_that_called_it_and_records_it(run_env):
    """C-4's `decided_by`: 'cast' | 'temper' | 'inspect_start'.

    Recorded rather than inferred, so "which crossing decided this width" is
    answerable from the artifact. A reader looking at a FULL cycle needs to tell
    a phase entry from a final gate, and the rule alone does not say which door
    the run came through.
    """
    project_root, fdir = run_env
    _write_state(fdir, phase="F3", cycle=1)
    _write_defects(fdir, [_open_live()])
    _write_manifest(fdir)

    for token in ("cast", "temper", "inspect_start"):
        entry = _decide_inspect_mode(
            fdir, project_root, decided_by=token, phase="F2", cycle=1
        )
        assert entry["decided_by"] == token


# --------------------------------------------------------------------------- #
# GI-002 / ST-005 / CT-007 / FR-009 / FR-042 / FR-043 / AC-013 / AC-014 /
# OT-008 / OT-016 — THE EVIDENCE SWEEP AT THE BOUNDARY
#
# GI-002 is a statement about WHO sweeps: "the SERVER sweeps at the boundary and
# refuses on mismatch". Its named violations are a boundary that trusts a
# teammate-reported sweep and a lead running a shell loop over `evidence/`. Both
# of those are CLAIMS; this is a measurement, taken at the one crossing where
# HEAD is the tree every later gate will judge.
#
# FR-043 says why it lives here rather than in a teammate's checklist:
# "Teammates commit per-casting logs; nobody sweeps by hand."
# --------------------------------------------------------------------------- #


def _evidence_log(project_root: str, name: str, command: str, body: str,
                  requirement: str = "FR-001") -> Path:
    """Commit one evidence log in the shipped v2.1 shape.

    Repo-root `evidence/`, which is where `select_sweep_scope` globs and where
    every casting in this run committed — not the run directory, which is
    gitignored and therefore absent from every worktree the sweep builds.
    """
    evidence = Path(project_root) / "evidence"
    evidence.mkdir(parents=True, exist_ok=True)
    log = evidence / name
    log.write_text(
        f"# evidence-cmd: {command}\n"
        f"# evidence-for: {requirement}\n"
        f"\n{body}",
        encoding="utf-8",
    )
    return log


def test_a_log_that_no_longer_reproduces_refuses_the_transition_by_name(run_env):
    """AC-013 verbatim: 'Foundry-Phase inspect_start re-executes the in-scope
    evidence logs in a detached worktree at HEAD and refuses the transition
    naming any log whose output mismatches; the cycle counter does not advance
    on refusal.'

    OT-008 states the same claim as an observable. NAMING the log is the part
    that matters operationally: a sweep that said only "something no longer
    reproduces" would send the lead to re-run the whole corpus by hand to find
    out which — and the sweep already knows.
    """
    project_root, fdir = run_env
    _write_state(fdir, phase="F3", cycle=1)
    _write_defects(fdir, [_open_live()])
    _write_manifest(fdir)
    # A log whose committed body disagrees with what its own command produces.
    _evidence_log(
        project_root, "casting-1-handler.log",
        "echo the-handler-calls-the-store", "the-handler-does-not\n",
    )
    _grind_touching(project_root, fdir, "src/handler.py")
    _arm(fdir)

    result = foundry_mark_phase_complete("inspect_start", project_root)

    assert result.get("ok") is not True, result
    assert "casting-1-handler.log" in result["error"], result
    assert result["mismatches"], result

    # The counter did NOT advance, and no mode was recorded for a transition
    # that did not happen.
    assert _read_state(fdir)["cycle"] == 1
    assert _read_state(fdir)["phase"] == "F3"
    assert "inspect_modes" not in _read_state(fdir)


def test_a_log_that_still_reproduces_lets_the_boundary_through(run_env):
    """The other side of the same gate: the sweep is a measurement, not a
    tollbooth. A corpus that still reproduces at HEAD costs the run nothing but
    the time to prove it."""
    project_root, fdir = run_env
    _write_state(fdir, phase="F3", cycle=1)
    _write_defects(fdir, [_open_live()])
    _write_manifest(fdir)
    _evidence_log(
        project_root, "casting-1-handler.log",
        "echo the-handler-calls-the-store", "the-handler-calls-the-store\n",
    )
    _grind_touching(project_root, fdir, "src/handler.py")
    _arm(fdir)

    result = foundry_mark_phase_complete("inspect_start", project_root)

    assert result["ok"] is True, result
    assert result["cycle"] == 2
    sweep = result["evidence_sweep"]
    assert sweep["mismatches"] == []
    assert len(sweep["logs_reexecuted"]) == 1


def test_a_grind_touching_nothing_in_scope_re_executes_zero_logs(run_env):
    """AC-014 verbatim (first half): 'When the GRIND diff touches no casting
    key_files and no file referenced by a log's command, the sweep re-executes
    zero logs and records the delta scope.'

    An empty scope is a COMPLETE answer, not a degenerate one — and it is the
    common case that makes DELTA worth having. It costs no worktree, no
    `.git/config.lock` contention and no subprocess at all.
    """
    project_root, fdir = run_env
    _write_state(fdir, phase="F3", cycle=1)
    _write_defects(fdir, [_open_live()])
    _write_manifest(fdir)
    _evidence_log(
        project_root, "casting-1-handler.log",
        "echo unrelated", "unrelated\n",
    )
    _git(Path(project_root), "add", "-A")
    _git(Path(project_root), "commit", "-qm", "evidence")
    # The GRIND touches a file that is no casting's key_file and that no log's
    # command mentions.
    _grind_touching(project_root, fdir, "src/other.py")
    _arm(fdir)

    result = foundry_mark_phase_complete("inspect_start", project_root)

    assert result["ok"] is True, result
    sweep = result["evidence_sweep"]
    assert sweep["scope"] == "delta"
    assert sweep["logs_reexecuted"] == []


def test_the_delta_sweep_re_executes_only_the_logs_tied_to_the_touched_file(run_env):
    """OT-016 verbatim (first half): 'A DELTA sweep after a GRIND touching one
    file re-executes only the logs tied to that file's casting or referencing
    that file.'

    FR-009's scope rule. The corpus grows with the run — thunder-viper finished
    with dozens of logs — so re-executing all of them after a three-file GRIND is
    most of what a needless full INSPECT costs.
    """
    project_root, fdir = run_env
    _write_state(fdir, phase="F3", cycle=1)
    _write_defects(fdir, [_open_live()])
    (fdir / "castings" / "manifest.json").write_text(
        json.dumps({"castings": [
            {"id": 1, "key_files": ["src/handler.py"]},
            {"id": 2, "key_files": ["src/other.py"]},
        ]}),
        encoding="utf-8",
    )
    _evidence_log(project_root, "casting-1-handler.log", "echo one", "one\n")
    _evidence_log(project_root, "casting-2-other.log", "echo two", "two\n")
    _git(Path(project_root), "add", "-A")
    _git(Path(project_root), "commit", "-qm", "evidence")
    _grind_touching(project_root, fdir, "src/handler.py")
    _arm(fdir)

    result = foundry_mark_phase_complete("inspect_start", project_root)

    assert result["ok"] is True, result
    executed = [Path(p).name for p in result["evidence_sweep"]["logs_reexecuted"]]
    assert executed == ["casting-1-handler.log"], result["evidence_sweep"]


def test_the_full_rule_sweeps_the_whole_corpus(run_env):
    """AC-014's second half and OT-016's: 'at the INSPECT before ASSAY, NYQUIST
    or DONE the sweep covers the whole corpus' / 'the sweep before ASSAY
    re-executes every log.'

    One decision drives both the roster and the sweep scope, which is the point
    of computing the width first: the gates that end a run are never handed a
    partial answer about either.
    """
    project_root, fdir = run_env
    # D-068: the FULL rule that fires here is `verifier_touched` — the GRIND
    # diff moved schemas/. A cleared defect ledger is no longer a FULL rule, so
    # the sweep scope is driven by a real widening cause rather than by the
    # proxy that made every cycle full.
    _write_state(fdir, phase="F3", cycle=1)
    _write_defects(fdir, [{**_open_live(), "status": "fixed", "fixed_in_cycle": 1}])
    (fdir / "castings" / "manifest.json").write_text(
        json.dumps({"castings": [
            {"id": 1, "key_files": ["src/handler.py"]},
            {"id": 2, "key_files": ["src/other.py"]},
        ]}),
        encoding="utf-8",
    )
    _evidence_log(project_root, "casting-1-handler.log", "echo one", "one\n")
    _evidence_log(project_root, "casting-2-other.log", "echo two", "two\n")
    _git(Path(project_root), "add", "-A")
    _git(Path(project_root), "commit", "-qm", "evidence")
    _grind_touching(project_root, fdir, "src/handler.py", "src/schemas/vocab.py")
    _arm(fdir)

    result = foundry_mark_phase_complete("inspect_start", project_root)

    assert result["ok"] is True, result
    assert result["inspect_rule"] == "verifier_touched"
    sweep = result["evidence_sweep"]
    assert sweep["scope"] == "full"
    assert sorted(Path(p).name for p in sweep["logs_reexecuted"]) == [
        "casting-1-handler.log", "casting-2-other.log",
    ]


def test_the_sweep_result_is_recorded_in_the_cycle_rollup(run_env):
    """C-6: `cycles[<cycle>].evidence_sweep` carries the scope, the logs, the
    mismatches, the elapsed time and the pool size.

    Recorded because the F6 report reads it, and because "how long did the sweep
    cost this cycle" is the question that decides whether DELTA is paying for
    itself.
    """
    project_root, fdir = run_env
    _write_state(fdir, phase="F3", cycle=1)
    _write_defects(fdir, [_open_live()])
    _write_manifest(fdir)
    _evidence_log(project_root, "casting-1-handler.log", "echo one", "one\n")
    _git(Path(project_root), "add", "-A")
    _git(Path(project_root), "commit", "-qm", "evidence")
    _grind_touching(project_root, fdir, "src/handler.py")
    _arm(fdir)

    foundry_mark_phase_complete("inspect_start", project_root)

    rollup = json.loads(
        (fdir / _streams.ROLLUP_FILENAME).read_text(encoding="utf-8")
    )["cycles"]["2"]["evidence_sweep"]
    assert rollup["scope"] == "delta"
    assert [Path(p).name for p in rollup["logs_reexecuted"]] == [
        "casting-1-handler.log"
    ]
    assert rollup["mismatches"] == []
    assert isinstance(rollup["elapsed_seconds"], float)
    assert rollup["swept_at"]


def test_the_persisted_sweep_record_carries_the_per_log_column(run_env):
    """CT-007 verbatim: 'sweep result recorded per log with scope (delta or
    full) and elapsed seconds.'

    D-044 — COMPUTED, THEN DROPPED BY THE WRAPPER. `sweep_evidence_at_head`
    returned `per_log` with a row for EVERY log in scope, matched or not, each
    carrying its own elapsed seconds. `_sweep_evidence_at_boundary` copied six
    sibling fields and omitted it, so the persisted record's keys were exactly
    ['elapsed_seconds', 'logs_reexecuted', 'mismatches', 'pool_size', 'scope',
    'swept_at'] — `elapsed_seconds` a single run total and no per-log column
    anywhere. It reached neither stream-rollup.json, nor the transition result,
    nor the report; grep for `per_log` across src/ found the producer and no
    consumer at all.

    ASSERTED AGAINST THE PERSISTED RECORD, which is the half `test_evidence.py`
    could not cover: it asserts on `sweep_evidence_at_head` directly, so 2740
    tests passed over the gap between the producer and the artifact.
    """
    project_root, fdir = run_env
    _write_state(fdir, phase="F3", cycle=1)
    _write_defects(fdir, [_open_live()])
    _write_manifest(fdir)
    _evidence_log(project_root, "casting-1-handler.log", "echo one", "one\n")
    _git(Path(project_root), "add", "-A")
    _git(Path(project_root), "commit", "-qm", "evidence")
    _grind_touching(project_root, fdir, "src/handler.py")
    _arm(fdir)

    result = foundry_mark_phase_complete("inspect_start", project_root)

    persisted = json.loads(
        (fdir / _streams.ROLLUP_FILENAME).read_text(encoding="utf-8")
    )["cycles"]["2"]["evidence_sweep"]
    assert persisted["per_log"] == result["evidence_sweep"]["per_log"], (
        "the artifact and the transition result must carry the same column"
    )
    assert [Path(row["log"]).name for row in persisted["per_log"]] == [
        "casting-1-handler.log"
    ]
    row = persisted["per_log"][0]
    assert row["matched"] is True
    assert isinstance(row["elapsed_seconds"], float)
    # The scope rides on the record, per log rather than per run — CT-007 names
    # both halves in one clause and the record has to answer both.
    assert persisted["scope"] == "delta"


def test_every_log_in_scope_gets_a_per_log_row_not_only_the_mismatches(run_env):
    """The adjacent path: a sweep whose logs all PASS still has to say how long
    each took.

    The timing column used to exist only on mismatch records, so the logs that
    reproduced — every log, on a healthy run — had none, and "which log is
    making DELTA expensive" was unanswerable exactly when it was worth asking.
    """
    project_root, fdir = run_env
    _write_state(fdir, phase="F3", cycle=1)
    _write_defects(fdir, [_open_live()])
    (fdir / "castings" / "manifest.json").write_text(
        json.dumps({"castings": [
            {"id": 1, "key_files": ["src/handler.py"]},
            {"id": 2, "key_files": ["src/other.py"]},
        ]}),
        encoding="utf-8",
    )
    _evidence_log(project_root, "casting-1-handler.log", "echo one", "one\n")
    _evidence_log(project_root, "casting-2-other.log", "echo two", "two\n")
    _git(Path(project_root), "add", "-A")
    _git(Path(project_root), "commit", "-qm", "evidence")
    _grind_touching(project_root, fdir, "src/handler.py", "src/other.py")
    _arm(fdir)

    result = foundry_mark_phase_complete("inspect_start", project_root)

    sweep = result["evidence_sweep"]
    assert sweep["mismatches"] == []
    assert len(sweep["per_log"]) == len(sweep["logs_reexecuted"]) == 2
    assert all(row["matched"] for row in sweep["per_log"]), sweep["per_log"]
    assert all("elapsed_seconds" in row for row in sweep["per_log"])


def test_the_sweep_runs_before_the_state_transaction_opens():
    """The ordering that keeps the run from serialising on itself.

    The counter advance lives inside `_document_transaction(state.json)`, an
    fcntl-locked section every other tool call on the run contends on;
    `sweep_evidence_at_head` creates a detached worktree and runs a bounded
    thread pool of subprocesses inside it. Holding the flock across that would
    stall the entire run for the duration, for no benefit — neither the mode
    decision nor the sweep reads or writes state.json.

    Asserted against the SOURCE, because the property is an ORDER and the only
    way to observe it at runtime is a race.

    fallout FR-058 / GI-029 / AC-056 — THE ORDER NOW SPANS TWO FUNCTIONS.
    -------------------------------------------------------------------
    D-032 moved the width decision and the sweep out of this branch and into
    `_inspect_start_preconditions`, via `transitions.py#_boundary_evidence_rung`,
    so that `Foundry-Gate('inspect_start')` refuses the mismatch the transition
    used to refuse alone. The ordering is untouched and is now stated across
    the call: the routine takes both reads, and the branch consults it and
    returns its refusal BEFORE it opens the transaction.

    So the assertion follows the reads rather than pinning the offsets they
    used to occupy — three offsets inside one body became one offset inside
    the routine and two inside the branch, and both halves are asserted where
    each now lives. What is protected is the same thing: nothing that spawns
    a worktree full of subprocesses runs under the state.json flock, and a
    refused crossing takes no lock at all.
    """
    import inspect
    import textwrap

    # Half one — the routine is where the decision and the sweep are taken, so
    # both are complete before the branch this ordering is about is entered.
    routine_source = textwrap.dedent(
        inspect.getsource(_transitions._inspect_start_preconditions)
    )
    assert "_decide_inspect_mode(" in routine_source, (
        "the inspect_start routine no longer decides the width — this "
        "ordering guard reads it there since D-032"
    )
    assert "_boundary_evidence_rung(" in routine_source, (
        "the inspect_start routine no longer sweeps the corpus — this "
        "ordering guard reads it there since D-032"
    )

    # Half two — the branch consults that routine and returns its refusal
    # before it opens the transaction. Bounded to the branch itself (the next
    # `elif phase ==` ends it), so every offset compared below is one this
    # branch really holds rather than one a later branch supplied.
    source = inspect.getsource(_transitions._phase_transition)
    rest = source[source.index('elif phase == "inspect_start"'):]
    body = rest[:rest.index("\n    elif phase == ", 1)]
    precondition_at = body.index("_inspect_start_preconditions(")
    refusal_at = body.index("return _transition_refusal(")
    transaction_at = body.index("_document_transaction(state_path)")
    assert precondition_at < refusal_at < transaction_at, (
        "decide-and-sweep, then refuse, then transact — the refusal must be "
        "returned before the lock is taken, so a refused transition leaves "
        "the counter untouched and no mode recorded"
    )


def test_a_sweep_that_cannot_run_is_a_refusal_not_a_pass(run_env):
    """A sweep that could not run is emphatically not a sweep that passed.

    The alternative shape — returning ok with an empty mismatch list — is how a
    broken sweep quietly clears the boundary it exists to hold. `evidence.py`
    reports that case with a non-None `error`, and the transition has to name it
    rather than read it as "nothing mismatched".
    """
    project_root, fdir = run_env
    _write_state(fdir, phase="F3", cycle=1)
    _write_defects(fdir, [_open_live()])
    _write_manifest(fdir)
    _evidence_log(project_root, "casting-1-handler.log", "echo one", "one\n")
    _git(Path(project_root), "add", "-A")
    _git(Path(project_root), "commit", "-qm", "evidence")
    _grind_touching(project_root, fdir, "src/handler.py")

    from foundry_mcp.tools import evidence as evidence_module

    original = evidence_module.sweep_evidence_at_head
    evidence_module.sweep_evidence_at_head = lambda **_k: {
        "ok": False, "scope_count": 1, "logs_reexecuted": [], "mismatches": [],
        "elapsed_seconds": 0.0, "pool_size": 1, "head_commit": None,
        "error": "could not create the sweep worktree at HEAD abc123",
    }
    _arm(fdir)
    try:
        result = foundry_mark_phase_complete("inspect_start", project_root)
    finally:
        evidence_module.sweep_evidence_at_head = original

    assert result.get("ok") is not True
    assert "could not create the sweep worktree" in result["error"]
    assert _read_state(fdir)["cycle"] == 1


# --------------------------------------------------------------------------- #
# D-014 — the FULL rule fires at the F2 and F5 entries, so the sweep runs there
#
# FR-009: the whole corpus is swept "whenever the FULL rule fires". Both phase
# entries record FULL / first_of_phase and neither swept anything —
# `_sweep_evidence_at_boundary` had exactly ONE call site in the server. On the
# clean path ASSAY → TEMPER → NYQUIST → DONE no sweep ran at all, while the
# lead-lane fixes US-005 exists to enable land commits throughout F5.
# --------------------------------------------------------------------------- #


def test_the_f2_entry_sweeps_and_refuses_a_log_that_no_longer_reproduces(run_env):
    """GI-002 / FR-009 / AC-014: the `cast` transition records FULL, so it
    sweeps the whole corpus and refuses on mismatch, naming the log."""
    project_root, fdir = run_env
    _write_state(fdir, phase="F1", cycle=0)
    _write_manifest(fdir)
    _evidence_log(
        project_root, "casting-1-handler.log",
        "echo the-handler-calls-the-store", "the-handler-does-not\n",
    )
    _git(Path(project_root), "add", "-A")
    _git(Path(project_root), "commit", "-qm", "evidence")
    _arm(fdir)

    result = foundry_mark_phase_complete("cast", project_root)

    assert result.get("ok") is not True, result
    assert "casting-1-handler.log" in result["error"], result
    # The hint names the token that was refused, not a different one.
    assert "phase='cast'" in result["hint"], result
    # A refused transition leaves no trace it was attempted: no phase change,
    # no recorded width, and not even the CAST-complete marker.
    assert _read_state(fdir)["phase"] == "F1"
    assert "inspect_modes" not in _read_state(fdir)
    assert not (fdir / ".cast-complete").exists()


def test_the_f5_entry_sweeps_and_refuses_a_log_that_no_longer_reproduces(run_env):
    """The same rule at the other phase entry. This is the boundary that opens
    the LAST inspection of a run, and it was the one boundary not checking that
    the run's committed evidence still reproduces."""
    project_root, fdir = run_env
    _write_state(fdir, phase="F4", cycle=3)
    _write_manifest(fdir)
    _evidence_log(
        project_root, "casting-1-handler.log",
        "echo the-handler-calls-the-store", "the-handler-does-not\n",
    )
    _git(Path(project_root), "add", "-A")
    _git(Path(project_root), "commit", "-qm", "evidence")
    _arm(fdir)

    result = foundry_mark_phase_complete("temper", project_root)

    assert result.get("ok") is not True, result
    assert "casting-1-handler.log" in result["error"], result
    assert "phase='temper'" in result["hint"], result
    assert _read_state(fdir)["phase"] == "F4"


def test_both_phase_entries_sweep_the_whole_corpus_and_record_it(run_env):
    """FR-009 / OT-016: 'full corpus at the INSPECT before ASSAY/NYQUIST/DONE
    and whenever the FULL rule fires.'

    A reproducing corpus costs the run nothing but the time to prove it, and
    the scope is recorded so the roll-up says what was checked.
    """
    project_root, fdir = run_env
    _write_state(fdir, phase="F1", cycle=0)
    _write_manifest(fdir)
    _evidence_log(project_root, "casting-1-handler.log", "echo steady", "steady\n")
    _evidence_log(project_root, "casting-2-other.log", "echo also-steady",
                  "also-steady\n")
    _git(Path(project_root), "add", "-A")
    _git(Path(project_root), "commit", "-qm", "evidence")
    _arm(fdir)

    result = foundry_mark_phase_complete("cast", project_root)

    assert result["ok"] is True, result
    assert result["inspect_mode"] == "FULL"
    assert result["evidence_sweep"]["scope"] == "full"
    assert len(result["evidence_sweep"]["logs_reexecuted"]) == 2
    assert result["evidence_sweep"]["mismatches"] == []
    rollup = json.loads((fdir / _streams.ROLLUP_FILENAME).read_text(encoding="utf-8"))
    assert rollup["cycles"]["0"]["evidence_sweep"]["scope"] == "full"


def test_every_transition_that_opens_an_inspect_sweeps():
    """The property, rather than three separate call sites that happen to
    agree today. GI-009 names one rule — "whichever Foundry-Phase transition
    opens an INSPECT" — and the sweep belongs to the same set as the width
    decision, so the two are asserted against ONE derivation of that set.

    fallout FR-058 / GI-029 / AC-056 — D-032 MOVED THE READS, NOT THE RULE.
    ----------------------------------------------------------------------
    Both reads stood inside `_phase_transition`'s `cast`, `inspect_start` and
    `temper` branches, so the three gates answered `passed: True` over a corpus
    the matching `Foundry-Phase` calls refused — a check the transition made
    and its gate could not. They are rungs of `_cast_preconditions`,
    `_inspect_start_preconditions` and `_temper_preconditions` now, via
    `transitions.py#_boundary_evidence_rung`, and the branches consume
    `outcome["inspect_entry"]` and `outcome["evidence_sweep"]` instead of
    reading either again.

    So the DERIVATION follows the reads to the routines. Pinning the branch
    bodies would pin where the reads used to live, which is not what GI-009
    says; the set equality below is the rule, and it is what has to survive
    the move intact.

    Derived over every `PHASE_TOKENS` member rather than over the three tokens
    the answer happens to be: a fourth door that decides a width and does not
    sweep is exactly what a hardcoded roster of three cannot see, and the doors
    are the thing this file has twice watched grow by one.
    `tests/orchestration/test_transitions.py#test_the_boundary_sweep_is_a_rung_of_the_routine_not_an_arm_in_the_branch`
    walks those three by name and is the other half of the claim.
    """
    import ast
    import inspect
    import textwrap

    def _calls(node) -> set[str]:
        return {
            n.func.id for n in ast.walk(node)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
        }

    # GI-031: one preconditions routine per TRANSITION token. Resolved from the
    # declared token set rather than scanned out of the module, so a token
    # whose routine was renamed away fails HERE instead of quietly shrinking
    # the subject — the empty-derivation failure every derived pin in this
    # suite carries a guard against. (A scan would also collect
    # `_token_preconditions`, which is the dispatch table, not a door.)
    routines = {}
    for token in _transitions.PHASE_TOKENS:
        routine = getattr(_transitions, f"_{token}_preconditions", None)
        assert routine is not None, (
            f"PHASE_TOKENS names {token!r} and transitions.py defines no "
            f"_{token}_preconditions — this guard reads the routines, so a "
            "token without one is a door it cannot see"
        )
        routines[token] = routine
    assert {"cast", "inspect_start", "temper"} <= set(routines), sorted(routines)

    opens_inspect: list[str] = []
    sweeps: list[str] = []
    for token, routine in routines.items():
        calls = _calls(ast.parse(textwrap.dedent(inspect.getsource(routine))))
        if "_decide_inspect_mode" in calls:
            opens_inspect.append(token)
        if "_boundary_evidence_rung" in calls:
            sweeps.append(token)

    assert sorted(opens_inspect) == ["cast", "inspect_start", "temper"]
    assert sorted(sweeps) == sorted(opens_inspect), (
        "every transition that opens an INSPECT must sweep the evidence "
        "corpus: FR-009 sweeps 'whenever the FULL rule fires', and a phase "
        "entry always records FULL"
    )

    # One hop, asserted rather than assumed. `_boundary_evidence_rung` stands
    # for the corpus read above, and it is that rung only while it still makes
    # the read — a rung that stopped sweeping would leave the equality above
    # true and the property false, which is the silent pass this suite spends
    # its empty-set guards on.
    assert "_sweep_evidence_at_boundary(" in inspect.getsource(
        _transitions._boundary_evidence_rung
    ), "the boundary rung no longer sweeps the corpus"

    # The other direction of this test's own derivation: a branch that took
    # either read BACK would put the check somewhere the gate cannot make it,
    # which is D-032 returning.
    branch_source = inspect.getsource(_transitions._phase_transition)
    assert "_decide_inspect_mode(" not in branch_source, (
        "a branch decides a width again — the gate for that token cannot see "
        "a decision taken inside the transition (FR-058 / GI-029)"
    )
    assert "_sweep_evidence_at_boundary(" not in branch_source, (
        "a branch sweeps the corpus again — the gate for that token cannot "
        "refuse a mismatch it never reads (FR-058 / GI-029)"
    )


# --------------------------------------------------------------------------- #
# D-035 — a fix landing mid-INSPECT invalidates a width already decided
# --------------------------------------------------------------------------- #

#: A LIVE fix keeps the full adjacent-path declaration whoever authored it
#: (FR-015 / AC-023), so both fixtures below carry one.
_ADJACENT_STATEMENT = (
    "Two transitions reach the handler besides the defect's — the retry branch "
    "in run_retry and the shutdown path in close_pool — and it writes the "
    "shared cache the reaper thread scans concurrently."
)
_ADJACENT_TEST = "tests/test_retry.py::test_run_retry_reuses_the_handler"


def _one_file_commit(project_root: str) -> str:
    """A real one-non-test-file commit, inside the lead lane.

    The lane is measured with `git show --numstat`, so it needs a real commit —
    the same reason `test_fix_gate.py` builds one rather than naming a SHA.
    """
    root = Path(project_root)
    (root / "src" / "handler.py").write_text(
        "def handle():\n    return store.get()\n", encoding="utf-8"
    )
    _git(root, "add", "src/handler.py")
    _git(root, "commit", "-qm", "lead-lane fix")
    return _git(root, "rev-parse", "HEAD").strip()


def test_a_fix_landing_during_f2_blocks_inspect_clean(run_env):
    """D-035: 'foundry_mark_defect_fixed has no phase guard, so a fix landing
    during F2 flips blocking to 0 after the INSPECT width was already decided —
    the gate then opens on a cycle whose sweep never covered the newly-changed
    surface.'

    The fix is legitimate work and is accepted; what is refused is carrying
    this cycle's decision forward as though it still described the tree.
    """
    from foundry_mcp.tools.orchestration.fix_gate import foundry_mark_defect_fixed

    project_root, fdir = run_env
    _write_state(fdir, phase="F2", cycle=2, inspect_modes=[{
        "cycle": 2, "phase": "F2", "mode": "DELTA", "rule": "delta",
        "decided_by": "inspect_start", "required_streams": ["trace", "prove", "test"],
        "stream_scope": {}, "prove_sample": [], "touched_files": [],
    }])
    _write_defects(fdir, [_open_live()])
    for stream in ("trace", "prove", "test"):
        (fdir / f".{stream}-complete").write_text("x\n", encoding="utf-8")

    fixed = foundry_mark_defect_fixed(
        defect_id="D-001", cycle=2, authored_by="lead",
        fix_commit=_one_file_commit(project_root),
        adjacent_path_statement=_ADJACENT_STATEMENT,
        adjacent_path_test=_ADJACENT_TEST,
        project_root=project_root,
    )
    assert fixed["ok"] is True, fixed

    _arm(fdir)
    result = foundry_mark_phase_complete("inspect_clean", project_root)

    assert result.get("ok") is not True, result
    assert "D-001" in result["error"], result
    assert "after this INSPECT's width was decided" in result["error"], result
    assert "inspect_start" in result["hint"], result
    assert not (fdir / ".inspect-clean").exists()


def test_a_fix_landing_during_f3_leaves_the_next_inspect_alone(run_env):
    """The normal case, which must stay free: GRIND is where fixes land, and
    the next `inspect_start` decides a width that already accounts for them.
    A guard that fired there would refuse every cycle the protocol produces."""
    from foundry_mcp.tools.orchestration.fix_gate import foundry_mark_defect_fixed

    project_root, fdir = run_env
    _write_state(fdir, phase="F3", cycle=2, inspect_modes=[{
        "cycle": 2, "phase": "F2", "mode": "DELTA", "rule": "delta",
        "decided_by": "inspect_start", "required_streams": ["trace", "prove", "test"],
        "stream_scope": {}, "prove_sample": [], "touched_files": [],
    }])
    _write_defects(fdir, [_open_live()])

    fixed = foundry_mark_defect_fixed(
        defect_id="D-001", cycle=2, authored_by="lead",
        fix_commit=_one_file_commit(project_root),
        adjacent_path_statement=_ADJACENT_STATEMENT,
        adjacent_path_test=_ADJACENT_TEST,
        project_root=project_root,
    )
    assert fixed["ok"] is True, fixed

    modes = _read_state(fdir)["inspect_modes"]
    assert "fixes_after_decision" not in modes[-1]


# --------------------------------------------------------------------------- #
# D-070 — the F5 entry does not overwrite the preceding INSPECT's rollup row
# --------------------------------------------------------------------------- #


def test_the_temper_entry_keeps_the_preceding_inspects_rollup_row(run_env):
    """CT-009 verbatim: the decision is recorded 'in state AND stream-rollup at
    the transition'. GI-009. D-070.

    `_record_cycle_rollup` keyed the bucket by `str(cycle)` and did
    `bucket.update(fields)`, and the F5 entry decides with
    `cycle=_current_cycle(fdir)` — a counter that does NOT advance entering F5.
    So the temper decision landed in the same bucket as the last
    `inspect_start` and DESTROYED it. Driven: inspect_start recorded cycle 2 as
    DELTA/delta; after `Foundry-Phase('temper')`, `cycles['2']` read
    FULL/first_of_phase and the DELTA `stream_scope` was gone. The state list
    survived because it is append-only; any reader taking the roll-up as the
    per-cycle width — the F6 report's cycle table among them — saw a fabricated
    FULL for a cycle that ran DELTA.
    """
    project_root, fdir = run_env
    _write_state(fdir, phase="F3", cycle=1)
    _write_defects(fdir, [{**_open_live(), "status": "fixed", "fixed_in_cycle": 1}])
    _write_manifest(fdir)
    _grind_touching(project_root, fdir, "src/handler.py")
    _arm(fdir)

    delta = foundry_mark_phase_complete("inspect_start", project_root)
    assert delta["inspect_mode"] == "DELTA"
    cycle = str(delta["cycle"])

    # D-125: F5 is reached THROUGH ASSAY, and `temper` is now refused from any
    # other phase. The subject here is the roll-up bucket, not the route, so
    # the run is moved to F4 the way a real one gets there (inspect_clean then
    # ASSAY) — `_update_phase` rather than `_write_state`, because the recorded
    # DELTA decision and the cycle counter are exactly what this test reads
    # back and a fresh state document would erase both.
    _transitions._update_phase(fdir, "F4")

    _arm(fdir)
    temper = foundry_mark_phase_complete("temper", project_root)
    assert temper["inspect_mode"] == "FULL"
    assert temper["inspect_rule"] == "first_of_phase"

    rollup = json.loads((fdir / _streams.ROLLUP_FILENAME).read_text(encoding="utf-8"))
    bucket = rollup["cycles"][cycle]

    # The INSPECT's own row is intact...
    assert bucket["inspect_mode"] == "DELTA"
    assert bucket["inspect_rule"] == "delta"
    assert bucket["stream_scope"], "the DELTA per-stream scope was overwritten"
    # ...and the F5 entry is recorded beside it, under its own key.
    entry = bucket[_width.TEMPER_ENTRY_ROLLUP_KEY]
    assert entry["inspect_mode"] == "FULL"
    assert entry["inspect_rule"] == "first_of_phase"
    assert entry["evidence_sweep"]["scope"] == "full"

    # Both survive in state.json too, which is the record they must agree with.
    modes = json.loads((fdir / "state.json").read_text(encoding="utf-8"))["inspect_modes"]
    assert [(m["decided_by"], m["mode"]) for m in modes[-2:]] == [
        ("inspect_start", "DELTA"), ("temper", "FULL"),
    ]


def test_the_inspect_start_row_is_written_flat_not_nested(run_env):
    """The sub-bucket is for the F5 entry ALONE. Every crossing that owns its
    cycle number writes flat, so no existing reader of
    `cycles[<cycle>]['inspect_mode']` has to learn a second key grammar."""
    project_root, fdir = run_env
    _write_state(fdir, phase="F3", cycle=1)
    _write_defects(fdir, [])
    _write_manifest(fdir)
    _grind_touching(project_root, fdir, "src/handler.py")
    _arm(fdir)

    result = foundry_mark_phase_complete("inspect_start", project_root)

    rollup = json.loads((fdir / _streams.ROLLUP_FILENAME).read_text(encoding="utf-8"))
    bucket = rollup["cycles"][str(result["cycle"])]
    assert "inspect_mode" in bucket
    assert _width.TEMPER_ENTRY_ROLLUP_KEY not in bucket


# --------------------------------------------------------------------------- #
# D-080 / AC-018 / AC-017 / FR-012 / FR-013 — the check that CONSUMES the roster
# measures against the roster
#
# `_check_streams_complete` was made to READ the recorded roster and then handed
# each member of it to `_coverage_shortfall`, which measured PROVE against the
# spec's whole requirement count and consulted no recorded decision. So a PROVE
# that checked exactly the roster the server itself recorded was reported
# incomplete forever, DELTA was unreachable on the guided path, and AC-018's
# saving was zero: the only way to close a DELTA cycle was to run PROVE at FULL
# width.
# --------------------------------------------------------------------------- #


def _delta_cycle_with_a_roster(project_root, fdir, *, ids: list[str]) -> dict:
    """Drive a real DELTA `inspect_start` and return its recorded decision.

    A cleared ledger with one defect fixed in the closing GRIND: that is the
    ordinary cycle AC-016's "and DELTA otherwise" describes, and the fixed row
    is what gives `prove_sample` a tied member to prove the roster is not just
    the random draw.
    """
    _write_spec(fdir, ids)
    _write_state(fdir, phase="F3", cycle=1, spec_path="")
    _write_defects(fdir, [
        {**_open_live("D-002"), "status": "fixed", "fixed_in_cycle": 1,
         "spec_ref": "FR-007"},
    ])
    _write_manifest(fdir)
    _grind_touching(project_root, fdir, "src/handler.py")
    _arm(fdir)

    result = foundry_mark_phase_complete("inspect_start", project_root)
    assert result["ok"] is True, result
    recorded = _read_state(fdir)["inspect_modes"][-1]
    assert recorded["mode"] == "DELTA", recorded
    return recorded


def _mark(project_root: str, stream: str, cycle: int, checked: int, total: int) -> dict:
    return _streams.foundry_mark_stream(
        stream, cycle, items_checked=checked, items_total=total,
        project_root=project_root,
    )


def test_a_delta_cycle_that_checked_its_recorded_roster_is_complete(run_env):
    """D-080's pin: a DELTA cycle whose PROVE checked exactly the recorded
    roster is complete.

    AC-018: 'In DELTA mode the PROVE roster names the matrix rows tied to the
    defects fixed in the preceding GRIND plus ten rows drawn with a seed derived
    from the cycle number.' That roster IS the width, so checking every row in
    it is the whole obligation — and on a 40-requirement spec it is eleven rows,
    not thirty-eight.
    """
    project_root, fdir = run_env
    ids = [f"FR-{n:03d}" for n in range(1, 41)]
    recorded = _delta_cycle_with_a_roster(project_root, fdir, ids=ids)
    roster = recorded["prove_sample"]
    cycle = recorded["cycle"]

    assert "FR-007" in roster
    assert len(roster) == 1 + PROVE_DELTA_SAMPLE_SIZE
    assert len(roster) < len(ids), "the fixture must be narrower than the spec"

    marked = _mark(project_root, "prove", cycle, len(roster), len(roster))
    assert "coverage_shortfall" not in marked, marked
    for wire in ("trace", "test"):
        _mark(project_root, wire, cycle, 1, 1)

    streams = _check_streams_complete(project_root)
    assert streams["complete"] is True, streams
    assert streams["missing"] == ""
    assert streams["shortfalls"] == []


def test_the_delta_shortfall_names_the_roster_not_the_spec_count(run_env):
    """One row short of the recorded roster IS a shortfall, and the refusal
    names the roster it was measured against.

    NO 0.95 SLACK ON THIS ARM. At FULL the denominator is the whole spec and the
    5% is tolerance for a matrix that moved under a long stream; a DELTA roster
    is a finite list of rows the server drew BY ID, so 'which of these did you
    not check' has an answer.
    """
    project_root, fdir = run_env
    ids = [f"FR-{n:03d}" for n in range(1, 41)]
    recorded = _delta_cycle_with_a_roster(project_root, fdir, ids=ids)
    roster = recorded["prove_sample"]
    cycle = recorded["cycle"]

    marked = _mark(project_root, "prove", cycle, len(roster) - 1, len(roster))
    shortfall = marked["coverage_shortfall"]

    assert shortfall["required"] == len(roster)
    assert shortfall["checked"] == len(roster) - 1
    assert shortfall["mode"] == "DELTA"
    assert shortfall["roster"] == roster
    # The old refusal named the SPEC count, which is the number this cycle was
    # explicitly not asked for.
    assert str(len(ids)) not in shortfall["reason"], shortfall["reason"]
    assert str(len(roster)) in shortfall["reason"]
    assert "FR-007" in shortfall["reason"]

    streams = _check_streams_complete(project_root)
    assert "prove" in streams["missing"]


def test_the_full_arm_still_measures_prove_against_the_spec(run_env):
    """The FULL half is unchanged: the roster is the whole spec, so the >=95%
    threshold against `count_spec_requirements` still applies — the leaf's
    now (fallout AC-014 / OT-016): the count left `gates.py` for
    `tools/artifacts.py`, where the module that READS the spec owns it,
    and the private spelling this cited is gone with the duplicate.

    Driven on the F2 entry, which records FULL / first_of_phase, so the DELTA
    reader returns None and the spec arm runs exactly as before.
    """
    project_root, fdir = run_env
    ids = [f"FR-{n:03d}" for n in range(1, 41)]
    _write_spec(fdir, ids)
    _write_state(fdir, phase="F1", cycle=0, spec_path="")
    _write_manifest(fdir)
    _arm(fdir)
    assert foundry_mark_phase_complete("cast", project_root)["inspect_mode"] == "FULL"

    cycle = _current_cycle(fdir)
    assert _streams._recorded_prove_roster(fdir, cycle) is None

    shortfall = _mark(project_root, "prove", cycle, 11, 40)["coverage_shortfall"]
    assert shortfall["required"] == 40
    assert "mode" not in shortfall
    # fallout AC-030 / OT-028 — THE SECOND RECORD STATES THE RUN'S OWN TOTAL.
    # This recorded 27 as a second TRANCHE, summing to 38 of 40 and clearing
    # the threshold. Records replace per (stream, cycle) now, so a second call
    # reporting 27 IS 27 of 40 and the shortfall correctly stands. The agent
    # states its running total — 38 — which is what an agent has and the server
    # does not (GI-016), and that is what clears the >=95% arm.
    assert _mark(project_root, "prove", cycle, 27, 40)["coverage_shortfall"], (
        "27 of 40 is 68% — a replacing record must be measured on its own "
        "numbers, not on a sum with the record it displaced"
    )
    assert _mark(project_root, "prove", cycle, 38, 40).get("coverage_shortfall") is None


def test_a_delta_cycle_at_its_recorded_width_reaches_the_widening_refusal(run_env):
    """The end-to-end D-080 named: with the roster checked, the run reaches the
    refusal that tells the lead to WIDEN, instead of being told forever that
    PROVE is missing.

    Before the fix `Foundry-Phase('inspect_clean')` answered 'streams
    incomplete: prove' at every width, and `Foundry-Next`'s F2 branch kept
    returning action=run_streams with 'Missing: prove'. Both are the streams
    check speaking for the width check, so the lead never saw the one refusal
    that has an action attached to it.
    """
    project_root, fdir = run_env
    ids = [f"FR-{n:03d}" for n in range(1, 41)]
    recorded = _delta_cycle_with_a_roster(project_root, fdir, ids=ids)
    cycle = recorded["cycle"]
    _mark(project_root, "prove", cycle, len(recorded["prove_sample"]),
          len(recorded["prove_sample"]))
    for wire in ("trace", "test"):
        _mark(project_root, wire, cycle, 1, 1)

    _arm(fdir)
    result = foundry_mark_phase_complete("inspect_clean", project_root)

    assert "streams incomplete" not in result.get("error", ""), result
    assert "DELTA width" in result["error"], result
    assert "inspect_start" in result["hint"]

    nxt = foundry_next_action(project_root)
    assert "Missing: prove" not in json.dumps(nxt)


def test_the_roster_reader_refuses_a_decision_from_another_cycle(run_env):
    """`_current_inspect_mode` returns the NEWEST entry, and a width is a fact
    about ONE crossing: a roster decided for cycle 5 says nothing about what
    cycle 4's PROVE owed. A mismatched cycle falls back to the spec arm rather
    than measuring one cycle's work against another cycle's roster.
    """
    project_root, fdir = run_env
    ids = [f"FR-{n:03d}" for n in range(1, 41)]
    recorded = _delta_cycle_with_a_roster(project_root, fdir, ids=ids)
    cycle = recorded["cycle"]

    assert _streams._recorded_prove_roster(fdir, cycle) == recorded["prove_sample"]
    assert _streams._recorded_prove_roster(fdir, cycle + 1) is None
    assert _streams._recorded_prove_roster(fdir, cycle - 1) is None


# --------------------------------------------------------------------------- #
# D-124 / D-125 — every INSPECT-opening token guards its source phase
#
# GI-009 names three transitions that open an INSPECT: the F2 entry (`cast`),
# the F5 entry (`temper`) and `inspect_start`. The cycle-6 ruling gave a
# source-phase precondition to exactly one of them, and D-116's failure shape
# survived unchanged on the other two.
# --------------------------------------------------------------------------- #


def test_cast_is_refused_from_every_phase_but_f1(run_env):
    """CT-009 / GI-009 / FR-023: ONE decision per INSPECT-opening transition.
    D-124.

    Driven on a run at F4 (ASSAY) in cycle 2 whose `inspect_modes` held a
    final_gate FULL decision for cycle 2 and whose `.cast-baseline-sha` named
    the CAST baseline commit: `Foundry-Phase('cast')` returned ok True, phase
    F2, mode FULL, rule first_of_phase. Afterwards state.json carried a THIRD
    width decision stamped onto cycle 2 beside the inspect_start one,
    `.cast-baseline-sha` had been overwritten from the baseline commit to HEAD
    — destroying the cycle-context baseline every GRIND prompt is built from —
    and the stream roll-up read `first_of_phase` for a cycle that ran
    final_gate. A run in ASSAY was pulled back into F2 with a fabricated
    first-of-phase record.
    """
    project_root, fdir = run_env
    _write_state(fdir, phase="F4", cycle=2, inspect_modes=[{
        "cycle": 2, "phase": "F2", "mode": "FULL", "rule": "final_gate",
        "rule_detail": "fixture", "decided_by": "inspect_start",
        "decided_at": foundry_state.now_iso(), "required_streams": ["trace", "prove", "test"],
        "stream_scope": {}, "touched_files": [], "prove_sample": [],
    }])
    _write_manifest(fdir)
    (fdir / ".cast-baseline-sha").write_text("baseline0000\n", encoding="utf-8")
    _arm(fdir)

    result = foundry_mark_phase_complete("cast", project_root)

    assert result.get("ok") is not True, result
    assert "F4" in result["error"], result
    assert result["accepted_from"] == ["F1"]
    # Nothing moved: not the phase, not the baseline, not the decision list.
    state = _read_state(fdir)
    assert state["phase"] == "F4"
    assert len(state["inspect_modes"]) == 1
    assert (fdir / ".cast-baseline-sha").read_text(encoding="utf-8").strip() == (
        "baseline0000"
    )
    assert not (fdir / ".cast-complete").exists()

    # ...and from F1, which is where CAST actually ends, it is accepted.
    _write_state(fdir, phase="F1", cycle=0)
    _arm(fdir)
    ok = foundry_mark_phase_complete("cast", project_root)
    assert ok["ok"] is True, ok
    assert ok["phase"] == "F2"
    assert ok["inspect_rule"] == "first_of_phase"


def test_temper_is_refused_from_every_phase_but_f4(run_env):
    """US-004 / FR-011 / AC-016: 'every final gate still runs everything at full
    width', and the INSPECT before NYQUIST is FULL. D-125.

    Driven on a run at F2 in cycle 2 whose recorded width was DELTA (rule
    delta), zero verdicts, nyquist on: `Foundry-Phase('temper')` returned ok
    True, phase F5, and stamped a SECOND decision onto cycle 2 (FULL,
    first_of_phase, decided_by temper) beside the DELTA one.
    `Foundry-Gate('nyquist')` then passed and `Foundry-Phase('nyquist')`
    reached F5.5 — so the last five-stream INSPECT before NYQUIST ran at DELTA
    width, the widening re-open the cycle-3 ruling requires never happened, and
    no verdict was ever written. F5 is reached THROUGH ASSAY; the door never
    asked what phase the run was in.
    """
    project_root, fdir = run_env
    _write_state(fdir, phase="F2", cycle=2, nyquist=True, inspect_modes=[{
        "cycle": 2, "phase": "F2", "mode": "DELTA", "rule": INSPECT_DELTA_RULE,
        "rule_detail": "fixture", "decided_by": "inspect_start",
        "decided_at": foundry_state.now_iso(), "required_streams": ["trace", "prove", "test"],
        "stream_scope": {}, "touched_files": [], "prove_sample": [],
    }])
    _write_manifest(fdir)
    _arm(fdir)

    result = foundry_mark_phase_complete("temper", project_root)

    assert result.get("ok") is not True, result
    assert "F2" in result["error"], result
    assert result["accepted_from"] == ["F4"]
    # The hint sends the lead through the door that widens, not around it.
    assert "inspect_clean" in result["hint"], result["hint"]

    state = _read_state(fdir)
    assert state["phase"] == "F2"
    assert len(state["inspect_modes"]) == 1, "a second decision was stamped"
    assert state["inspect_modes"][-1]["mode"] == "DELTA"

    # ...and from F4, which is where ASSAY ends, it is accepted.
    _write_state(fdir, phase="F4", cycle=2, nyquist=True)
    _arm(fdir)
    ok = foundry_mark_phase_complete("temper", project_root)
    assert ok["ok"] is True, ok
    assert ok["phase"] == "F5"
    assert ok["inspect_rule"] == "first_of_phase"


def test_every_source_guarded_token_guards_its_source(run_env):
    """GI-009's 'one rule' and ST-010's from-state, as a property.

    The cycle-6 ruling guarded `inspect_start` and left `cast` and `temper`
    open, which is how one fix covered one door of three. D-164 is the same
    omission one table over: the two TERMINAL tokens were never brought under
    it, so `done` returned ok from F4 on a --temper --nyquist run and
    `nyquist_done` returned ok from F2. This walks the set from the vocabulary
    the module itself declares, so a token added later cannot quietly join
    without a guard.
    """
    project_root, fdir = run_env
    _write_manifest(fdir)

    guarded = set(_transitions._PHASE_ENTRY_SOURCES) | {"inspect_start"}
    # fallout GI-011 / GI-031 / ST-012 — `nyquist` JOINED THE TABLE.
    # "a token added later cannot quietly join without a guard" is this test's
    # own sentence, and one did: casting 4 gave every PHASE_TOKENS member its
    # own preconditions function, and `nyquist` acquired a `_PHASE_ENTRY_SOURCES`
    # row with it (F4 on a TEMPER-off run, F5 with `--temper`). The set is
    # widened here rather than the derivation loosened, because the point of the
    # assertion is that joining is VISIBLE — the loop below drives the new
    # member exactly as it drives the other five.
    assert guarded == {
        "cast", "temper", "inspect_start", "nyquist", "nyquist_done", "done",
    }, guarded

    for token in sorted(guarded):
        # F6 is a phase none of them is accepted from.
        _write_state(fdir, phase="F6", cycle=1)
        _arm(fdir)
        result = foundry_mark_phase_complete(token, project_root)
        assert result.get("ok") is not True, (token, result)
        # fallout ST-012 / GI-011 / GI-029 — THE GUARD IS THE RUNG, NOT THE
        # SENTENCE THAT WON. Every token's refusal used to name the phase,
        # because the source check was the only one most of them made. One
        # preconditions function per token now assembles a whole ladder, and a
        # HIGHER-ranked rung legitimately speaks instead — `done` from F6 on an
        # empty spec refuses on "ZERO requirement IDs" first, which is the more
        # specific answer and the one a lead should read. What this test is
        # about is that the source guard EXISTS and FIRED, so it is asserted on
        # the rung rather than on whichever sentence outranked it.
        rung = _entry_rung(result, token)
        assert rung["ok"] is False, (token, rung)
        assert "F6" not in rung["accepted_from"], (token, rung)
        # The offending phase is NAMED on the rung — in its own `phase` field
        # for the five that go through `_source_phase_rung`, and in the label
        # for `inspect_start`, which writes its rung by hand because it accepts
        # two phases for two different reasons and its refusal names both.
        assert "F6" in f"{rung['check']} {rung.get('phase', '')}", (token, rung)
        assert _read_state(fdir)["phase"] == "F6", token


def test_the_terminal_doors_refuse_the_phases_the_defect_drove(run_env):
    """ST-010 verbatim: from-state 'F5.5 or F5 complete', to-state 'F6 DONE'.

    D-164, driven exactly as filed. (1) A run at F4 with `temper` and `nyquist`
    both set returned ok True and phase F6 from `done` — out of ASSAY, skipping
    both post-verification phases the run was started with. (2) The same run at
    F2 returned ok True and "NYQUIST complete -> phase is now F6" from
    `nyquist_done` — a message asserting a phase that never ran.

    Both halves are asserted with the LEDGERS SATISFIED, so nothing but the
    source phase can be doing the refusing: `_done_preconditions` would pass on
    this fixture, which is what made the hole invisible.
    """
    project_root, fdir = run_env
    _write_manifest(fdir)
    (fdir / "spec.md").write_text("- FR-001: the thing works\n", encoding="utf-8")
    (fdir / "defects.json").write_text(
        json.dumps({"defects": []}), encoding="utf-8"
    )
    (fdir / "verdicts.json").write_text(
        json.dumps({"requirements": [
            {"requirement_id": "FR-001", "verdict": "VERIFIED"},
        ]}), encoding="utf-8"
    )
    _report_seal._generate_report(project_root, fdir)

    _write_state(fdir, phase="F4", cycle=2, temper=True, nyquist=True)
    _arm(fdir)
    early = foundry_mark_phase_complete("done", project_root)
    assert early.get("ok") is not True, early
    # fallout ST-012 / GI-011 / GI-029 — the accepted set is a CHECKLIST ROW.
    # It was a top-level key of the refusal; one preconditions function per
    # token now answers as a checklist and the transition adds no refusal of its
    # own, so the fact lives on the rung that computed it.
    assert _accepted_from(early, "done") == ["F5.5"], early
    assert "post-verification" in early["error"], early
    assert "temper" in early["hint"], early["hint"]
    assert _read_state(fdir)["phase"] == "F4"

    _write_state(fdir, phase="F2", cycle=2, temper=True, nyquist=True)
    _arm(fdir)
    from_inspect = foundry_mark_phase_complete("nyquist_done", project_root)
    assert from_inspect.get("ok") is not True, from_inspect
    assert _accepted_from(from_inspect, "nyquist_done") == ["F5.5"], from_inspect
    assert _read_state(fdir)["phase"] == "F2"


def test_the_terminal_phase_a_run_may_finish_from_follows_its_own_flags(run_env):
    """ST-010 / CT-016: the from-state is the LAST phase this run enables.

    A static accepted_from would either refuse every plain run at F4 — where
    ASSAY is genuinely terminal — or admit the two calls D-164 drove. So the
    table resolves `done`'s source from the run's own `temper` / `nyquist`
    flags, and the three configurations are asserted as one discrimination
    rather than as a single happy path.
    """
    project_root, fdir = run_env
    _write_manifest(fdir)

    for flags, terminal in (
        ({}, "F4"),
        ({"temper": True}, "F5"),
        ({"temper": True, "nyquist": True}, "F5.5"),
        ({"nyquist": True}, "F5.5"),
    ):
        _write_state(fdir, phase=terminal, cycle=1, **flags)
        assert _transitions._phase_entry_source_problem(fdir, "done") is None, (
            flags, terminal
        )
        for wrong in ("F2", "F3", "F4", "F5", "F5.5"):
            if wrong == terminal:
                continue
            _write_state(fdir, phase=wrong, cycle=1, **flags)
            problem = _transitions._phase_entry_source_problem(fdir, "done")
            assert problem is not None, (flags, wrong)
            assert problem["accepted_from"] == [terminal], (flags, wrong)


# --------------------------------------------------------------------------- #
# STRUCTURAL — the escalated class `delta-roster-recorded-then-consumed-at-full-width`
#
# D-139 and D-140 are the same shape at two consumers: the server draws a DELTA
# roster, records it, and then something downstream measures the cycle against
# a width nobody drew. The deliverable is that the RECORDED decision is what
# every consumer reads — the drop rung, the Foundry-Next payload, and the
# stream prose.
# --------------------------------------------------------------------------- #


def test_a_delta_stream_is_not_accused_of_rushing_for_running_its_roster(run_env):
    """FR-012 / AC-018: the DELTA PROVE width IS the fixed-defect rows plus ten
    sampled rows. D-139.

    The drop rung compared cycle N's total against cycle N-1's total and
    consulted no recorded width, so on every DELTA cycle following a FULL one
    it accused the stream of rushing for delivering exactly the roster the
    server handed it. Driven: cycle 1 prove recorded 172/172 at FULL; cycle 2
    recorded a DELTA roster of 13 rows; Foundry-Stream(prove, cycle 2,
    items_checked 13, items_total 13) returned ok with `coverage_shortfall`
    null — the rung that DOES read the roster was satisfied — beside the
    warning "Coverage dropped: prove checked 13 items in cycle 2 vs 172 in
    cycle 1. Are you rushing?".
    """
    project_root, fdir = run_env
    _write_manifest(fdir)
    _write_spec(fdir, [f"FR-{n}" for n in range(1, 41)])

    _write_state(fdir, phase="F2", cycle=1)
    _streams._record_stream_rollup(fdir, 1, "prove", 40, 40, 0, 1)

    sample = ["FR-1", "FR-2", "FR-3"]
    _write_state(fdir, phase="F2", cycle=2, inspect_modes=[{
        "cycle": 2, "phase": "F2", "mode": "DELTA", "rule": INSPECT_DELTA_RULE,
        "rule_detail": "fixture", "decided_by": "inspect_start",
        "decided_at": foundry_state.now_iso(),
        "required_streams": ["trace", "prove", "test"],
        "stream_scope": {
            "prove": {"scope": "delta", "detail": "3 row(s)"},
            "trace": {"scope": "delta", "detail": "symbols in 1 file(s)"},
            "test": {"scope": "full", "detail": "whole suite, cold"},
        },
        "touched_files": ["src/handler.py"], "prove_sample": sample,
    }])

    result = foundry_mark_stream("prove", 2, items_checked=len(sample),
                                 items_total=len(sample),
                                 project_root=project_root)

    assert result["ok"] is True, result
    assert "coverage_shortfall" not in result, result
    assert "Are you rushing?" not in result.get("warning", ""), result
    assert "Coverage dropped" not in result.get("warning", ""), result


def test_a_stream_the_server_left_at_full_width_still_gets_the_drop_rung(run_env):
    """D-139's other direction: the rung is scoped, not removed.

    AC-019 keeps TEST full and cold on a DELTA cycle, so its denominator did
    NOT move and a collapse in it is a real signal. A fix that silenced the
    warning for every stream on every DELTA cycle would trade one false
    accusation for a blind spot on the stream most exposed to a delta INSPECT.
    """
    project_root, fdir = run_env
    _write_manifest(fdir)

    _write_state(fdir, phase="F2", cycle=1)
    _streams._record_stream_rollup(fdir, 1, "test", 500, 500, 0, 1)

    _write_state(fdir, phase="F2", cycle=2, inspect_modes=[{
        "cycle": 2, "phase": "F2", "mode": "DELTA", "rule": INSPECT_DELTA_RULE,
        "rule_detail": "fixture", "decided_by": "inspect_start",
        "decided_at": foundry_state.now_iso(),
        "required_streams": ["trace", "prove", "test"],
        "stream_scope": {
            "test": {"scope": "full", "detail": "whole suite, cold"},
        },
        "touched_files": ["src/handler.py"], "prove_sample": [],
    }])

    result = foundry_mark_stream("test", 2, items_checked=10, items_total=500,
                                 project_root=project_root)

    assert result["ok"] is True, result
    assert "Coverage dropped" in result.get("warning", ""), result


def test_next_emits_the_trace_half_of_the_delta_roster(run_env):
    """AC-019 verbatim: 'in DELTA mode ... TRACE runs over the symbols the GRIND
    commits touched'. D-140.

    `_decide_inspect_mode` records the TRACE scope as "symbols in the N file(s)
    the GRIND touched" and records `touched_files` beside it — and the
    Foundry-Next payload carried cycle, decided_at, decided_by, mode,
    prove_sample, required_streams, rule, rule_detail and stream_scope, and
    nothing naming a file or a symbol. So the PROVE half of the roster reached
    its stream (D-104) and the TRACE half reached no consumer at all: the
    stream could not learn the scope the server had recorded for it.
    """
    project_root, fdir = run_env
    _write_state(fdir, phase="F3", cycle=1)
    _write_defects(fdir, [_open_live()])
    _write_manifest(fdir)
    _grind_touching(project_root, fdir, "src/handler.py")
    _arm(fdir)

    assert foundry_mark_phase_complete(
        "inspect_start", project_root
    )["inspect_mode"] == "DELTA"

    reported = foundry_next_action(project_root)["inspect_mode"]

    assert "src/handler.py" in reported["touched_files"], reported
    assert reported["stream_scope"]["trace"]["scope"] == "delta"
    # Reported from the record, never recomputed (GI-008): what Next emits is
    # byte-identical to what the transition wrote.
    recorded = _read_state(fdir)["inspect_modes"][-1]
    assert reported["touched_files"] == recorded["touched_files"]
    assert reported["diff_base"] == recorded["diff_base"]


#: D-161 / STRUCTURAL PACKET 1 — EVERY surface that consumes the recorded
#: width, and the roster field each one is scoped by.
#:
#: The guard this table replaces named two files in its docstring and read ONE.
#: Driven at HEAD: the docstring said "`agents/tracer.md` and
#: `skills/trace/SKILL.md` contained no occurrence of touched_files,
#: inspect_mode, DELTA or width", the body bound only
#: `agents/tracer.md`, and `uv run pytest tests/test_inspect_mode.py -q`
#: returned 77 passed against a working tree in which `skills/trace/SKILL.md`
#: contained NONE of the six tokens the assertion demanded of tracer.md — the
#: suite green over exactly the gap the test names. The PROVE equivalent in
#: tests/test_protocol_prose.py has been parametrised over a two-surface tuple
#: since D-104, with the reason stated above it; this is the same shape, and
#: the axis it adds is that BOTH streams' surfaces are walked here, so a
#: surface with zero tokens can never be green on either side.
_WIDTH_COMMON_TOKENS = (
    # Where the width is READ FROM. GI-008 puts the decision at the transition
    # and leaves Foundry-Next reporting it; a stream told neither reads no
    # width and runs the matrix.
    "Foundry-Next",
    "inspect_mode.mode",
    "inspect_mode.cycle",
    # Both widths named, so the narrowing cannot read as unconditional.
    "DELTA",
    "FULL",
    # What an ABSENT width means. Unstated, a stream that finds no
    # `inspect_mode` picks a width itself — the lazily-computed mode GI-008 and
    # GI-009 both name as the violation.
    "no narrowing was decided",
)

_WIDTH_CONSUMER_SURFACES = (
    (("agents", "tracer.md"), (
        "inspect_mode.touched_files", "inspect_mode.stream_scope.trace.scope",
    )),
    (("skills", "trace", "SKILL.md"), (
        "inspect_mode.touched_files", "inspect_mode.stream_scope.trace.scope",
    )),
    (("agents", "assayer.md"), ("inspect_mode.prove_sample",)),
    (("skills", "prove", "SKILL.md"), ("inspect_mode.prove_sample",)),
)


@pytest.mark.parametrize(
    "parts,roster_tokens", _WIDTH_CONSUMER_SURFACES, ids=lambda v: "/".join(v)
    if isinstance(v, tuple) and v and isinstance(v[0], str) and "." in v[-1]
    else str(v),
)
def test_every_width_consuming_surface_reads_the_scope_the_server_records(
    parts, roster_tokens
):
    """AC-019's consumer half, on every surface that consumes it. D-140/D-161.

    AC-019 verbatim: "In DELTA mode TEST still runs full and cold from a clean
    worktree and TRACE runs over the symbols the GRIND commits touched."

    The stream is an AGENT and a SKILL, and what they read IS their
    instructions — so prose is the whole mechanism, and a surface the guard
    does not open is a surface the width never reaches. `agents/tracer.md`
    scoped the walk from the spec alone until D-140; `skills/trace/SKILL.md`,
    which `/foundry:trace` runs, carried none of it until D-160 while this test
    named it in its docstring and asserted nothing about it.

    Asserted against the same field names the payload emits, so a rename cannot
    leave a document pointing at a key that no longer arrives.
    """
    surface = _plugin_root().joinpath(*parts)
    text = surface.read_text(encoding="utf-8")
    rel = "/".join(parts)
    for token in _WIDTH_COMMON_TOKENS + tuple(roster_tokens):
        assert token in text, f"{rel} never names {token}"


def test_the_tracer_agent_still_distinguishes_the_roster_from_its_display():
    """The two tracer-only rules, kept where they were (D-140).

    They are asserted apart from the cross-surface table because they are about
    the DISPLAY of a roster rather than about the width: display.py truncates
    the printed sample, so a stream reading the terminal line reads a prefix and
    reports a width it never ran.
    """
    tracer = (_plugin_root() / "agents" / "tracer.md").read_text(encoding="utf-8")

    assert "walk everything" in tracer, (
        "an unrecorded width must mean 'no narrowing was decided'"
    )
    assert "TRUNCATES" in tracer, (
        "the terminal line is a summary; the roster is the array"
    )


# --------------------------------------------------------------------------- #
# D-152 — RULE PRECEDENCE, SO THE RECORDED RULE NAMES THE ARM THAT FIRED.
#
# The order is fixed by the lead ruling and by FR-011's "Foundry-Next names
# which rule fired": first_of_phase, then BOTH final_gate facts, then
# verifier_touched, then DELTA. The uncomputable-diff arm sat AHEAD of the two
# final_gate facts, which are readable without a diff — so a GRIND entered from
# ASSAY feedback on a run with no boundary marker recorded `verifier_touched`
# with a rule_detail that says the verifier could not be shown to have moved,
# for the very cycle that opens ASSAY. The mode is FULL either way; the
# PROVENANCE was false, and the assay gate's own refusal prose ("ASSAY is only
# opened by an INSPECT whose recorded rule is final_gate") contradicted it.
#
# This is D-069's shape one arm over. The covering test asserted only
# `rule in INSPECT_FULL_RULES` for the no-history case, so the precedence
# itself was untested — which is why the assertions below name the rule.
# --------------------------------------------------------------------------- #


def _uncomputable_diff(fdir: Path) -> None:
    """Remove every marker `_grind_diff` could measure from, so it has none.

    The run state D-152 was driven on and the one the spec keeps in scope: a
    resumed pre-change archive (thunder-viper's lacks exactly these three) has
    no boundary marker, no clean-TRACE stamp and no CAST baseline.
    """
    for marker in (
        _artifacts.INSPECT_BOUNDARY_SHA_MARKER, ".trace-clean-at", ".cast-baseline-sha"
    ):
        (fdir / marker).unlink(missing_ok=True)


def test_a_grind_entered_from_feedback_records_final_gate_with_no_diff(run_env):
    """FR-011 verbatim: "Foundry-Next names which rule fired."

    The GRIND that just closed was entered from ASSAY feedback — a fact the
    phase history carries and no diff is needed to read. So `final_gate` fired,
    and `final_gate` is what must be recorded, whether or not the diff can be
    computed. Driven on the exact state D-152 reports: phase_history F2 -> F4
    -> F3, and a run dir with none of the three markers a diff measures from.
    """
    project_root, fdir = run_env
    _write_state(
        fdir, phase="F3", cycle=4,
        phase_history=[{"phase": "F2"}, {"phase": "F4"}, {"phase": "F3"}],
    )
    _uncomputable_diff(fdir)

    entry = _decide_inspect_mode(
        fdir, project_root, decided_by="inspect_start", phase="F2", cycle=5
    )

    assert entry["mode"] == "FULL", entry
    assert entry["rule"] == "final_gate", entry
    assert "feedback" in entry["rule_detail"], entry


def test_the_widening_re_open_records_final_gate_with_no_diff(run_env):
    """AC-016 / the F2->F2 widening: the same precedence on the other
    final_gate fact.

    `widening` is a fact about the TRANSITION — the run is already at F2 and
    the recorded width was DELTA — and it is known without consulting git.
    """
    project_root, fdir = run_env
    _write_state(fdir, phase="F2", cycle=4)
    _uncomputable_diff(fdir)

    entry = _decide_inspect_mode(
        fdir, project_root, decided_by="inspect_start", phase="F2", cycle=5,
        widening=True,
    )

    assert entry["mode"] == "FULL", entry
    assert entry["rule"] == "final_gate", entry
    assert "widening" in entry["rule_detail"], entry


def test_an_uncomputable_diff_still_records_verifier_touched_when_no_arm_fired(
    run_env,
):
    """The arm is not removed, it is REORDERED.

    With neither final_gate fact true, an uncomputable diff is still FULL and
    is still recorded as `verifier_touched` with the real cause in
    `rule_detail` — nothing can show the verifier did NOT move, so the honest
    width is everything. Asserting this beside the two tests above is what
    makes them a precedence claim rather than a deletion.
    """
    project_root, fdir = run_env
    _write_state(
        fdir, phase="F3", cycle=4,
        phase_history=[{"phase": "F2"}, {"phase": "F3"}],
    )
    _uncomputable_diff(fdir)

    entry = _decide_inspect_mode(
        fdir, project_root, decided_by="inspect_start", phase="F2", cycle=5
    )

    assert entry["mode"] == "FULL", entry
    assert entry["rule"] == "verifier_touched", entry
    assert "could not be computed" in entry["rule_detail"], entry


def test_the_recorded_rule_survives_the_transition_into_state_and_rollup(run_env):
    """CT-009 / GI-008: the transition RECORDS the decision, and every later
    reader reports it. D-152 was visible in three artifacts at once — the
    entry, `state.json.inspect_modes[-1].rule` and the stream-rollup's
    per-cycle rule column — so all three are asserted here on one crossing.
    """
    project_root, fdir = run_env
    _write_state(
        fdir, phase="F3", cycle=4,
        phase_history=[{"phase": "F2"}, {"phase": "F4"}, {"phase": "F3"}],
    )
    _uncomputable_diff(fdir)
    _arm(fdir)

    result = foundry_mark_phase_complete("inspect_start", project_root)
    assert result.get("ok") is True, result

    recorded = _read_state(fdir)["inspect_modes"][-1]
    assert recorded["rule"] == "final_gate", recorded
    assert recorded["mode"] == "FULL", recorded

    rollup = json.loads((fdir / _streams.ROLLUP_FILENAME).read_text(encoding="utf-8"))
    cycle_row = rollup["cycles"][str(recorded["cycle"])]
    assert cycle_row["inspect_rule"] == "final_gate", cycle_row


# --------------------------------------------------------------------------- #
# D-169 — THE CONDITION A SURFACE STATES IS THE CONDITION THE CODE EVALUATES.
#
# Three shipped surfaces said "ASSAY is only opened by an INSPECT whose recorded
# rule is final_gate": `foundry_gate`'s assay-width refusal,
# `foundry_mark_phase_complete`'s inspect_clean DELTA refusal, and
# `_compute_next_action`'s widen_inspect instructions. The enforced predicate is
# WIDTH — the checklist entry is literally named `inspect_ran_at_full_width` and
# the test is `mode == "FULL"`. Driven at the ASSAY door on runs recorded
# FULL/final_gate, FULL/first_of_phase and FULL/verifier_touched: all three
# returned ok True, so an INSPECT recorded with rule verifier_touched DOES open
# ASSAY and the sentence was false.
#
# It was false about the ordinary case for a run that builds this plugin, where
# a GRIND touching `vocab.py`, `schemas/` or the orchestrator records
# verifier_touched — so a lead reading any of the three believed a clean FULL
# cycle still owed a widening cycle, which is exactly the ceremony US-004 exists
# to remove. D-152 fixed the rule PRECEDENCE and left every stated condition
# naming a rule no code reads.
#
# Pinned from BOTH ends: the doors accept every FULL rule (behaviour), and no
# shipped surface states a rule condition (the sentence). Either alone lets this
# come back — the behaviour was already right, and it was the prose that lied.
# --------------------------------------------------------------------------- #


def _full_cycle_recorded_with(fdir: Path, rule: str, cycle: int = 2) -> None:
    """A clean F2 whose recorded width is FULL and whose rule is `rule`."""
    _write_state(fdir, phase="F2", cycle=cycle, inspect_modes=[{
        "cycle": cycle, "phase": "F2", "mode": "FULL", "rule": rule,
        "rule_detail": f"{rule} fired", "decided_by": "inspect_start",
        "decided_at": "2026-09-03T00:00:00.000001+00:00",
        "required_streams": ["trace", "prove", "test"],
        "stream_scope": {
            w: {"scope": "full", "detail": "every item in scope"}
            for w in ("trace", "prove", "test")
        },
        "touched_files": [], "prove_sample": [], "diff_base": "0" * 40,
    }])
    _write_manifest(fdir)
    _write_spec(fdir, ["FR-001"])
    _write_defects(fdir, [])
    for stream in ("trace", "prove", "test"):
        (fdir / f".{stream}-complete").write_text(
            "2026-09-03T00:00:00+00:00 cycle=2\nitems_checked=1\nitems_total=1\n"
            "coverage=100%\nfindings=0\n",
            encoding="utf-8",
        )


@pytest.mark.parametrize("rule", sorted(INSPECT_FULL_RULES))
def test_a_full_inspect_opens_assay_whichever_full_rule_recorded_it(run_env, rule):
    """AC-016 verbatim: 'every later inspect_start transition records FULL when
    it is the INSPECT before ASSAY, NYQUIST or DONE, or when the GRIND diff
    touches vocab.py, schemas/, orchestrator or gate code, agent or skill prose,
    or the spec'.

    US-004's premise is "every final gate still runs everything at full WIDTH".
    All three FULL rules produce full width, so all three open ASSAY — at both
    doors. Parametrised over `INSPECT_FULL_RULES` rather than over a typed list,
    so a rule added to the vocabulary is covered here the day it is added.
    """
    project_root, fdir = run_env
    _full_cycle_recorded_with(fdir, rule)

    _arm(fdir)
    gate = _gates.foundry_gate("assay", project_root)
    width = next(
        c for c in gate["checklist"]
        if c["check"].startswith("inspect_ran_at_full_width")
    )
    assert width["ok"] is True, (rule, width, gate.get("reason"))
    assert gate["passed"] is True, (rule, gate.get("reason"))

    _arm(fdir)
    clean = foundry_mark_phase_complete("inspect_clean", project_root)
    assert clean.get("ok") is True, (rule, clean)
    assert _read_state(fdir)["phase"] == "F4", (rule, clean)


def test_the_delta_refusals_state_the_width_they_test_and_not_a_rule(run_env):
    """GI-008 / AC-016. The refusal half of the same property.

    A DELTA cycle is refused at both doors — that is unchanged and correct —
    and what each says is the condition it EVALUATED, with the recorded mode
    and rule reported beside it as the facts they are. A refusal that names a
    condition the code never reads sends the lead to satisfy a rule instead of
    a width.
    """
    project_root, fdir = run_env
    _full_cycle_recorded_with(fdir, "delta")
    state = _read_state(fdir)
    state["inspect_modes"][-1]["mode"] = "DELTA"
    (fdir / "state.json").write_text(json.dumps(state), encoding="utf-8")

    _arm(fdir)
    clean = foundry_mark_phase_complete("inspect_clean", project_root)
    assert clean.get("ok") is not True, clean
    assert "recorded mode is FULL" in clean["error"], clean["error"]
    assert "ran at DELTA width (rule delta)" in clean["error"], clean["error"]

    _arm(fdir)
    gate = _gates.foundry_gate("assay", project_root)
    assert gate["passed"] is False, gate
    assert "recorded mode is FULL" in gate["reason"], gate["reason"]
    assert "DELTA width (rule delta)" in gate["reason"], gate["reason"]


def test_no_shipped_surface_states_the_rule_as_the_assay_condition():
    """D-169's sentence half, over the module's source.

    The behaviour above was ALREADY right before this defect was filed; what
    was wrong was three strings. So the strings are asserted directly: no
    surface may claim ASSAY turns on the recorded RULE, because none of them
    reads one. Scoped to the two spellings the three offenders used, so an
    honest sentence that merely mentions `final_gate` as provenance — which the
    hints still do, correctly — is not caught.

    READ OFF THE AST, over string CONSTANTS, not over the file's text. A `#`
    comment quoting the retired sentence to explain why it was retired is the
    house style and must not trip this — the failure history is how the next
    author learns which invariant they are about to break. What may not survive
    is a sentence the server can EMIT, and every one of those is a string
    literal.

    fallout FR-005 / AC-014 / OT-016 — THIRTEEN FILES, NOT ONE.
    This read `Path(fo.__file__)` — the orchestrator's single module. The claim
    it scans is about the strings the SERVER can emit, and those are spread
    across `gates.py`, `transitions.py`, `width.py` and `guidance.py` now. So
    the file is `ORCHESTRATION`, all thirteen, and it stays all thirteen when a
    fourteenth is added; repointing at any single module would have left the
    sentence legal in the other twelve while the pin went green. The failure
    names the MODULE as well as the line, because "which file" is the first
    thing a reader of this failure needs and a concatenated scan cannot say it.
    """
    import ast

    # The scan must SEE something, or every assertion below is vacuous — the
    # failure mode of every derived pin in this suite.
    assert ORCHESTRATION, "the orchestration set is empty; this scan reads nothing"
    scanned = 0
    for module in ORCHESTRATION:
        path = Path(module.__file__)
        tree = ast.parse(path.read_text(encoding="utf-8"))
        scanned += 1
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Constant) and isinstance(node.value, str)):
                continue
            for claim in (
                "recorded rule is final_gate",
                "recorded with rule final_gate",
            ):
                assert claim not in node.value, (
                    f"{path.name} line {node.lineno} states {claim!r} in "
                    "a string the server can emit; both ASSAY doors read the "
                    "recorded MODE, and `inspect_ran_at_full_width` is the name "
                    "of the check that decides it"
                )
    assert scanned == len(ORCHESTRATION), scanned


# --------------------------------------------------------------------------- #
# D-177 — the recorded PROVE scope states the number DRAWN, not the ceiling.
# --------------------------------------------------------------------------- #


def test_the_prove_scope_detail_states_the_sample_it_actually_drew(run_env):
    """FR-013 / FR-049. The recorded `stream_scope` is read back by the PROVE
    stream as its width statement, so its two clauses must agree with the
    roster recorded beside them.

    The detail was built as f"{len(prove_sample)} row(s): rows tied to the fixed
    defects plus {PROVE_DELTA_SAMPLE_SIZE} sampled" — one measured number and
    one CONSTANT. The draw is `min(PROVE_DELTA_SAMPLE_SIZE, len(remaining))`, so
    on any spec with fewer remaining rows than the ceiling the sentence
    contradicted the list it described. Driven on a two-requirement spec with no
    fixed-defect rows: `prove_sample` recorded ['FR-001', 'FR-002'] and the
    detail read "2 row(s): rows tied to the fixed defects plus 10 sampled" — a
    stream trusting it looks for eight rows that were never drawn.
    """
    project_root, fdir = run_env
    _write_state(fdir, phase="F3", cycle=1)
    _write_defects(fdir, [_open_live()])
    _write_manifest(fdir)
    _write_spec(fdir, ["FR-001", "FR-002"])
    _grind_touching(project_root, fdir, "src/handler.py")
    _arm(fdir)

    result = foundry_mark_phase_complete("inspect_start", project_root)
    assert result["inspect_mode"] == "DELTA", result

    recorded = _read_state(fdir)["inspect_modes"][-1]
    sample = recorded["prove_sample"]
    detail = recorded["stream_scope"]["prove"]["detail"]

    assert len(sample) == 2, sample
    assert detail == "2 row(s): 0 tied to the fixed defects plus 2 sampled", detail
    assert str(PROVE_DELTA_SAMPLE_SIZE) not in detail, (
        "the ceiling is not the measurement; the pool held fewer rows than it"
    )


def test_the_prove_scope_detail_adds_up_to_the_roster_beside_it(run_env):
    """The general property, on a spec large enough for the full draw: the two
    numbers the detail states are the two halves of the roster it describes.

    A spec of forty rows with one row tied to a fixed defect draws the full ten,
    so this is the case where the old string happened to be right — and it must
    still be right for the reason that it is measured, not by coincidence.
    """
    project_root, fdir = run_env
    ids = [f"FR-{n:03d}" for n in range(1, 41)]
    _write_state(fdir, phase="F3", cycle=1)
    _write_defects(fdir, [{
        **_open_live(), "status": "fixed", "fixed_in_cycle": 1, "spec_ref": "FR-007",
    }])
    _write_manifest(fdir)
    _write_spec(fdir, ids)
    _grind_touching(project_root, fdir, "src/handler.py")
    _arm(fdir)

    assert foundry_mark_phase_complete(
        "inspect_start", project_root
    )["inspect_mode"] == "DELTA"

    recorded = _read_state(fdir)["inspect_modes"][-1]
    detail = recorded["stream_scope"]["prove"]["detail"]
    total = len(recorded["prove_sample"])

    assert detail == (
        f"{total} row(s): 1 tied to the fixed defects plus "
        f"{PROVE_DELTA_SAMPLE_SIZE} sampled"
    ), detail
    assert total == 1 + PROVE_DELTA_SAMPLE_SIZE, recorded["prove_sample"]


# --------------------------------------------------------------------------- #
# D-173 — the recorded roster crosses the MCP boundary.
#
# `server.call_tool` returned one TextContent of `format_result(name, result)`,
# and `format_result` returns ONLY the formatter's string when a formatter
# exists. Foundry-Next has one, so the result dict — the roster the streams
# must obey — was discarded one rung below the wire. Driven at the real MCP
# surface: a DELTA cycle with 9 touched files and 10 sampled rows produced a
# response naming 5 files then "(+4 more)" and 8 rows then "...", with the
# literals `inspect_mode`, `touched_files` and `prove_sample` appearing nowhere
# in it. FR-047 / FR-049 make that roster the exact set the streams-complete
# check judges the stream against.
# --------------------------------------------------------------------------- #


def _drive_mcp_text(name: str, arguments: dict) -> str:
    """Call a tool through the MCP REQUEST HANDLER, not through `call_tool`.

    The transport a client actually uses — the same helper shape
    `test_orchestrator_gates.py` uses for the argument refusals, and for the
    same reason: this defect is about what crosses the boundary, so the test
    must cross it.
    """
    import asyncio

    from mcp import types

    import foundry_mcp.server as srv

    handler = srv.server.request_handlers[types.CallToolRequest]
    request = types.CallToolRequest(
        method="tools/call",
        params=types.CallToolRequestParams(name=name, arguments=arguments),
    )
    return asyncio.run(handler(request)).root.content[0].text


def _machine_readable(text: str) -> dict:
    """The result dict a response carries after `RESULT_JSON_MARKER`."""
    import re

    from foundry_mcp.tools.display import RESULT_JSON_MARKER

    plain = re.sub(r"\x1b\[[0-9;]*m", "", text)
    assert RESULT_JSON_MARKER in plain, "no machine-readable block in the response"
    _, _, body = plain.partition(RESULT_JSON_MARKER + "\n")
    return json.loads(body)


def test_the_delta_roster_crosses_the_mcp_boundary_whole(run_env, monkeypatch):
    """FR-049 verbatim: 'every later Foundry-Next displays the decision'; FR-047:
    'the streams-complete check enforces exactly that set'.

    Nine touched files and a full sample, driven at the real MCP surface. Both
    halves are asserted on ONE response: the display still truncates — that is
    NFR-005 and it is deliberate — and the arrays are recoverable in full from
    the machine-readable part. A fix that un-truncated the terminal line instead
    would satisfy neither requirement and would fail the first two assertions
    here.
    """
    import foundry_mcp.server as srv

    project_root, fdir = run_env
    touched = [f"src/mod_{i}.py" for i in range(9)]
    sample = [f"FR-{n:03d}" for n in range(1, 11)]
    _write_state(fdir, phase="F2", cycle=4, inspect_modes=[{
        "cycle": 4, "phase": "F2", "mode": "DELTA", "rule": INSPECT_DELTA_RULE,
        "rule_detail": "no FULL rule fired", "decided_by": "inspect_start",
        "decided_at": "2026-09-03T00:00:00.000001+00:00",
        "required_streams": ["trace", "prove", "test"],
        "stream_scope": {
            "trace": {"scope": "delta", "detail": "symbols in the 9 file(s)"},
            "prove": {"scope": "delta", "detail": "10 row(s): 0 tied plus 10"},
            "test": {"scope": "full", "detail": "whole suite, cold"},
        },
        "touched_files": touched, "prove_sample": sample, "diff_base": "abc1234",
    }])
    _write_defects(fdir, [])
    _write_manifest(fdir)
    monkeypatch.setattr(srv, "_project_root", project_root)

    text = _drive_mcp_text("Foundry-Next", {})
    payload = _machine_readable(text)
    mode = payload["inspect_mode"]

    # The roster, whole, off the wire.
    assert mode["touched_files"] == touched, mode["touched_files"]
    assert mode["prove_sample"] == sample, mode["prove_sample"]
    assert mode["required_streams"] == ["trace", "prove", "test"]
    assert mode["stream_scope"]["trace"]["scope"] == "delta"
    assert mode["mode"] == "DELTA"
    assert mode["diff_base"] == "abc1234"
    assert mode["cycle"] == 4

    # ...and the display half is still the summary it was built to be.
    head = text.partition("machine-readable result")[0]
    assert "(+4 more)" in head, "the terminal line must stay truncated"
    assert touched[8] not in head, "the terminal line must stay truncated"


def test_a_tool_with_no_formatter_is_not_given_a_second_copy(run_env, monkeypatch):
    """The boundary rule's other half. An unformatted tool's whole response is
    already the result as JSON, so appending the marker would say everything
    twice and break `json.loads` on the response — which is what
    `test_call_tool_still_returns_a_normal_result_unwrapped` reads.
    """
    from foundry_mcp.tools.display import RESULT_JSON_MARKER, format_result_blocks

    project_root, _fdir = run_env
    del project_root

    rendered = format_result_blocks("Foundry-No-Formatter-Test", {"ok": True, "v": 42})
    assert RESULT_JSON_MARKER not in rendered
    assert json.loads(rendered) == {"ok": True, "v": 42}


def test_a_payload_that_will_not_serialise_still_renders_its_display():
    """The house rule at this rung: never raise across the MCP boundary, and
    never lose the operator's answer over a value that would not serialise.
    """
    from foundry_mcp.tools.display import RESULT_JSON_MARKER, format_result_blocks

    class _Unserialisable:
        def __repr__(self):  # `default=str` reaches this, so the block is written
            return "<opaque>"

    rendered = format_result_blocks(
        "Foundry-Next", {"display": "BANNER", "instructions": "go", "x": _Unserialisable()}
    )
    assert "BANNER" in rendered
    assert RESULT_JSON_MARKER in rendered
    assert json.loads(rendered.partition(RESULT_JSON_MARKER + "\n")[2])["x"] == "<opaque>"


# --------------------------------------------------------------------------- #
# D-183 — THE WIDTH REFUSAL IS NOT DISPLACED BY THE CHECK BELOW IT.
#
# `foundry_gate`'s assay branch is a ladder of independent checks, each writing
# `passed`, `reason` and `hint`, so the LAST failing check owns the one line a
# terminal renders. The `.inspect-clean` check sits below the width check and
# reassigned both strings whenever `has_fixed > 0` — which is true of every
# ordinary GRIND cycle, because a GRIND that fixed nothing is not a GRIND.
#
# Driven end to end through `server.call_tool` on a DELTA cycle carrying one
# fixed defect: the gate returned reason "GRIND fixed defects but INSPECT has
# not re-verified" and hint "…close it with Foundry-Phase(phase='inspect_clean')
# — that transition writes the .inspect-clean marker this check reads, and it is
# the only call that does." Following that hint, `inspect_clean` was REFUSED:
# "cycle N ran at DELTA width (rule delta), and ASSAY is only opened by an
# INSPECT whose recorded mode is FULL". The gate had computed that very
# sentence one check earlier and thrown it away.
#
# That is D-123's class on D-123's own symbol: the remedy a refusal states is a
# call the server then rejects. Ruling 4 in the run's `spec_ambiguities` and
# start.md's ASSAY-door paragraph both make the recorded WIDTH the whole
# condition — "From a cycle recorded `DELTA`: call
# `Foundry-Phase(phase='inspect_start')` AGAIN, from F2" — so the width refusal
# wins and the `.inspect-clean` text may not overwrite it.
#
# The checklist was never wrong: `inspect_ran_at_full_width (mode=DELTA
# rule=delta) ok=False` was correct in both runs. So `reason` and `hint` are
# pinned here, not just the checklist entry — the checklist is a dict a test
# reads and the strings are what a lead reads.
# --------------------------------------------------------------------------- #


def _cycle_recorded_with_mode(fdir: Path, mode: str, rule: str, cycle: int = 2) -> None:
    """A clean, fully-streamed F2 whose recorded width is exactly `mode`.

    `_full_cycle_recorded_with` records FULL; this writes either width from the
    same shape, so the DELTA and FULL runs below differ in the one field under
    test and in nothing else.
    """
    _full_cycle_recorded_with(fdir, rule, cycle=cycle)
    state = _read_state(fdir)
    state["inspect_modes"][-1]["mode"] = mode
    (fdir / "state.json").write_text(json.dumps(state), encoding="utf-8")


def _fixed_defect(did: str = "D-001", cycle: int = 2) -> dict:
    """One FIXED defect — what a GRIND leaves behind, and what makes
    `has_fixed > 0` the ordinary state rather than an edge case."""
    return {
        "id": did, "cycle": cycle, "source": "trace", "type": "UNWIRED",
        "description": f"{did} description", "file": "src/handler.py",
        "symbol": "handle", "status": "fixed", "tier": "LIVE",
        "class": "K", "fixed_in_cycle": cycle,
    }


def _fix_landed_mid_inspect(fdir: Path, did: str = "D-001") -> None:
    """Stamp `did` as a fix that landed AFTER this INSPECT's width was decided.

    fallout GI-011 / CT-013 / AC-016 — WHERE THIS FACT LIVES NOW.
    The `assay` door used to reach the "GRIND fixed defects and INSPECT has not
    re-verified" substance through the `.inspect-clean` marker and a `status:
    fixed` row in `defects.json`. `_inspect_clean_preconditions` says in its own
    docstring why it no longer does: the marker is not a rung, because this
    transition is that marker's only writer, and the substance is carried by the
    `fixes_after_decision` rung "which measures the same thing against the
    recorded width decision instead of against a marker".

    So the fixture stamps the decision the way a real `foundry_mark_defect_fixed`
    in F2 does — through the production writer, not by hand-editing state — for
    the reason `_generate_report` states one helper over: an artifact a fixture
    made up can clear a door that no real run in the same state could.
    """
    _fix_gate._note_fix_after_inspect_decision(fdir, did)


def _gate_over_the_wire(project_root: str, fdir: Path, monkeypatch) -> dict:
    """`Foundry-Gate('assay')` through the MCP request handler.

    Driven over the wire because that is where the lead reads it: the response
    a client renders carries the machine-readable result after the marker, and
    `reason` / `hint` are the two fields of it a terminal actually prints.
    """
    import foundry_mcp.server as srv

    monkeypatch.setattr(srv, "_project_root", project_root)
    _arm(fdir)
    return _machine_readable(_drive_mcp_text("Foundry-Gate", {"phase": "assay"}))


def test_a_delta_cycle_carrying_a_fixed_defect_still_refuses_on_its_width(
    run_env, monkeypatch
):
    """AC-016 verbatim: 'Foundry-Next returns the recorded mode and rule name
    whenever it is called and never computes them'; FR-011: 'FULL when: … the
    INSPECT before ASSAY/NYQUIST/DONE … Otherwise DELTA.'

    The ORDINARY DELTA cycle: defects were fixed, so `has_fixed > 0`. NFR-005
    makes the one line a terminal prints the surface that has to be right, and
    the remedy it names must be a call the server accepts — which
    `inspect_clean` is not, at a DELTA width. Both halves are asserted: the
    width sentence survives, and the displaced sentence's call is absent.
    """
    project_root, fdir = run_env
    _cycle_recorded_with_mode(fdir, "DELTA", INSPECT_DELTA_RULE)
    _write_defects(fdir, [_fixed_defect()])

    gate = _gate_over_the_wire(project_root, fdir, monkeypatch)

    assert gate["passed"] is False, gate
    assert "DELTA width (rule delta)" in gate["reason"], gate["reason"]
    assert "recorded mode is FULL" in gate["reason"], gate["reason"]
    assert "has not re-verified" not in gate["reason"], gate["reason"]
    assert "inspect_start" in gate["hint"], gate["hint"]
    assert "inspect_clean" not in gate["hint"], gate["hint"]

    # The checklist was already right in the shipped code and stays right: this
    # defect was never about the entry, it was about the sentence beside it.
    width = next(
        c for c in gate["checklist"]
        if c["check"].startswith("inspect_ran_at_full_width")
    )
    assert width["ok"] is False, width
    assert "mode=DELTA" in width["check"], width
    inspect_clean = next(c for c in gate["checklist"] if c["check"] == "inspect_clean")
    assert inspect_clean["ok"] is False, inspect_clean

    # ...and the call the shipped hint named IS the one the server refuses, so
    # the two surfaces cannot disagree about it again.
    _arm(fdir)
    clean = foundry_mark_phase_complete("inspect_clean", project_root)
    assert clean.get("ok") is not True, clean
    assert "recorded mode is FULL" in clean["error"], clean["error"]


def test_the_same_delta_cycle_with_nothing_fixed_reads_identically(
    run_env, monkeypatch
):
    """AC-016. The control that localised D-183.

    The width arm was never broken — driven on this same run with zero fixed
    defects it produced the correct sentence all along, which is what proved
    the arm right and merely shadowed. So the two runs are asserted to agree:
    whether a GRIND fixed something changes the checklist's `inspect_clean`
    entry and nothing about the width the lead is told.
    """
    project_root, fdir = run_env
    _cycle_recorded_with_mode(fdir, "DELTA", INSPECT_DELTA_RULE)
    _write_defects(fdir, [])

    unfixed = _gate_over_the_wire(project_root, fdir, monkeypatch)

    _write_defects(fdir, [_fixed_defect()])
    fixed = _gate_over_the_wire(project_root, fdir, monkeypatch)

    assert unfixed["reason"] == fixed["reason"], (unfixed["reason"], fixed["reason"])
    assert unfixed["hint"] == fixed["hint"], (unfixed["hint"], fixed["hint"])
    assert "DELTA width (rule delta)" in unfixed["reason"], unfixed["reason"]


@pytest.mark.parametrize("rule", sorted(INSPECT_FULL_RULES))
def test_a_full_cycle_carrying_a_fixed_defect_still_names_the_inspect_clean_door(
    run_env, monkeypatch, rule
):
    """FR-006 / AC-008 / FR-044 — D-123's remedy, kept.

    When the width is FULL the `inspect_clean` call the hint names is one the
    server accepts, so that hint is the right one and must still win. Guarding
    the width refusal must not silence the check below it on the runs where it
    is the truthful answer. Parametrised over `INSPECT_FULL_RULES` for the same
    reason the D-169 tests are: every FULL rule opens ASSAY, so every one of
    them must reach this hint.
    """
    project_root, fdir = run_env
    _cycle_recorded_with_mode(fdir, "FULL", rule)
    _write_defects(fdir, [_fixed_defect()])
    _fix_landed_mid_inspect(fdir)

    gate = _gate_over_the_wire(project_root, fdir, monkeypatch)

    assert gate["passed"] is False, gate
    # fallout AC-016 / CT-013 — THE SENTENCE MOVED WITH THE MECHANISM.
    # The rung reads `fixes_after_decision` off the recorded decision now, so
    # it names the defects by id rather than saying "has not re-verified". The
    # claim is unchanged and is asserted on both halves: the refusal is the
    # fixes rung (not the width rung, which passes here), and the remedy it
    # states is the boundary crossing a FULL cycle can actually make.
    assert "fixed after this INSPECT's width was decided" in gate["reason"], (
        gate["reason"]
    )
    assert "D-001" in gate["reason"], gate["reason"]
    assert "Foundry-Phase(phase='inspect_start')" in gate["hint"], gate["hint"]
    fixes = next(
        c for c in gate["checklist"]
        if c["check"].startswith("no_fixes_after_width_decision")
    )
    assert fixes["ok"] is False, fixes
    assert fixes["fixes_after_decision"] == ["D-001"], fixes
    width = next(
        c for c in gate["checklist"]
        if c["check"].startswith("inspect_ran_at_full_width")
    )
    assert width["ok"] is True, width


def test_an_unrecorded_width_carrying_a_fixed_defect_refuses_on_the_width(
    run_env, monkeypatch
):
    """AC-016 / D-117: 'unrecorded' is not full width either.

    The width arm has two branches — `_unrecorded_width_problem` and the
    positive `mode == "FULL"` test — and the shadowing swallowed BOTH. A
    resumed pre-change archive carries no `inspect_modes` at all, so this is
    the branch a legacy run actually lands on, and it must reach the lead
    intact for the same reason the DELTA one must.
    """
    project_root, fdir = run_env
    _write_state(fdir, phase="F2", cycle=1)
    _write_manifest(fdir)
    _write_spec(fdir, ["FR-001"])
    _write_defects(fdir, [_fixed_defect(cycle=1)])
    for stream in ("trace", "prove", "test"):
        (fdir / f".{stream}-complete").write_text(
            "2026-09-03T00:00:00+00:00 cycle=1\nitems_checked=1\nitems_total=1\n"
            "coverage=100%\nfindings=0\n",
            encoding="utf-8",
        )

    _fix_landed_mid_inspect(fdir, "D-001")

    gate = _gate_over_the_wire(project_root, fdir, monkeypatch)

    assert gate["passed"] is False, gate
    # fallout GI-011 / GI-029 / CT-013 — THE GATE REPORTS; THE TRANSITION ACTS.
    # "Cannot open ASSAY — " is the transition's own wrapping clause, and the
    # gate no longer writes one: one preconditions function answers both doors
    # and the gate publishes its checklist as data. So the width sentence is
    # asserted here and the wrap is asserted at the door that produces it,
    # which is a stronger pair than the single string this used to read.
    assert "no recorded width" in gate["reason"], gate["reason"]
    assert "has not re-verified" not in gate["reason"], gate["reason"]
    assert "inspect_clean" not in gate["hint"], gate["hint"]

    _arm(fdir)
    refused = foundry_mark_phase_complete("inspect_clean", project_root)
    assert refused.get("ok") is not True, refused
    assert "Cannot mark INSPECT clean" in refused["error"], refused
    assert "no recorded width" in refused["error"], refused


# --------------------------------------------------------------------------- #
# D-186 — AND THE RUNG BELOW THE ONE D-183 GUARDED WAS DOING THE SAME THING.
#
# D-183's fix wrapped the `.inspect-clean` strings in
# `if assay_unrecorded is None and assay_width_ok:` — one rung, guarded. The
# rung BELOW it, `no_active_teams`, then displaced the width refusal in exactly
# the same way, and did it while assigning `reason` and NO hint at all.
#
# Driven through `server.call_tool` before the fix:
#   * FULL/final_gate, one fixed defect, a registered team -> reason "Active
#     teams: foundry-cast" beside the `.inspect-clean` hint. The hint answered a
#     DIFFERENT check than the reason, and following it was actively harmful:
#     `Foundry-Phase(phase='inspect_clean')` on the identical run returned ok
#     True and set F4, so a lead who read the refusal and followed its hint
#     crossed into ASSAY with the team still registered — defeating the exact
#     check that refused.
#   * DELTA/delta, one fixed defect, a registered team -> reason "Active teams:
#     foundry-cast" with the FR-011 / AC-016 width refusal the arm above had
#     already computed discarded. D-183's displacement, one rung lower.
#   * teams-active as the only failure -> reason "Active teams: foundry-cast"
#     with hint "" — a refusal with no remedy at all.
#
# No test could reach any of it, because this file's `run_env` monkeypatched
# `_check_active_teams` to a permanent inactive stub. It is flippable now, and
# these are the three drives.
#
# The fix is the mechanism, not a second guard: `_GateLadder` ranks every
# failing check by whose remedy is not defeated by another failing check on the
# same call, so WIDTH outranks TEAMS outranks the `.inspect-clean` MARKER, and
# every arm carries a hint by the signature of `fail`.
# --------------------------------------------------------------------------- #


def test_a_delta_cycle_with_an_active_team_still_refuses_on_its_width(
    run_env, monkeypatch
):
    """FR-011 / AC-016 / NFR-005 — PROVE's second drive.

    The ordinary DELTA cycle with a registered team: the gate computed the width
    refusal in the arm above and the teams arm below overwrote it, which is
    precisely the displacement D-183 was filed for, one rung lower. The width
    refusal wins outright — `Foundry-Phase(phase='inspect_start')` is the one
    remedy at this door that no other failing check can refuse.
    """
    project_root, fdir = run_env
    _cycle_recorded_with_mode(fdir, "DELTA", INSPECT_DELTA_RULE)
    _write_defects(fdir, [_fixed_defect()])
    _teams_active(True)

    gate = _gate_over_the_wire(project_root, fdir, monkeypatch)

    assert gate["passed"] is False, gate
    assert "DELTA width (rule delta)" in gate["reason"], gate["reason"]
    assert "recorded mode is FULL" in gate["reason"], gate["reason"]
    assert "Active team" not in gate["reason"], gate["reason"]
    assert "inspect_start" in gate["hint"], gate["hint"]

    # The teams check has not been weakened — it still fails, its checklist
    # entry still says so, and its own sentence is still PUBLISHED. Nothing is
    # discarded; one thing speaks.
    teams = next(c for c in gate["checklist"] if c["check"] == "no_active_teams")
    assert teams["ok"] is False, teams
    # fallout GI-011 / FR-026 — ONE SPELLING, RICHER THAN THE THREE IT
    # REPLACED. `_teams_rung` names live panes as well as team directories, so
    # the sentence is "Active teammates: Team dirs: foundry-cast" rather than
    # "Active teams: foundry-cast". Asserted as the two facts this test is
    # about — the scan fired, and the team it found is NAMED — which is the
    # shape `test_the_three_gate_branches_that_scan_teams_state_one_remedy`
    # already uses, rather than a literal a later widening breaks again.
    published = " ".join(r["reason"] for r in gate["refusals"])
    assert "Active team" in published, gate["refusals"]
    assert "foundry-cast" in published, gate["refusals"]
    assert gate["refusals"][0]["reason"] == gate["reason"], gate["refusals"]
    assert gate["refusals"][0]["hint"] == gate["hint"], gate["refusals"]


def test_a_full_cycle_with_an_active_team_names_the_team_and_not_the_marker(
    run_env, monkeypatch
):
    """CT-008 / NFR-005 — PROVE's first drive, and the harm it named.

    At a FULL width the `.inspect-clean` arm's remedy is a call the server
    accepts, so it is not refused — it is DEFEATED, which is worse: driven on
    this run, `Foundry-Phase(phase='inspect_clean')` returns ok True and sets
    F4, so a lead who reads "Active teams" and follows the `inspect_clean` hint
    beside it crosses into ASSAY with the team still registered, defeating the
    exact check that refused. The team must therefore own both strings.
    """
    project_root, fdir = run_env
    _cycle_recorded_with_mode(fdir, "FULL", "final_gate")
    _write_defects(fdir, [_fixed_defect()])
    _teams_active(True)

    gate = _gate_over_the_wire(project_root, fdir, monkeypatch)

    assert gate["passed"] is False, gate
    assert "Active team" in gate["reason"], gate["reason"]
    assert "foundry-cast" in gate["reason"], gate["reason"]
    assert gate["hint"], "a refusal with no stated next move is no remedy"
    assert gate["hint"] == _gates._TEAMS_DOWN_HINT, gate["hint"]
    assert "Foundry-Team-Down" in gate["hint"], gate["hint"]
    assert "inspect_clean" not in gate["hint"], gate["hint"]

    # The harm, driven. It used to LAND: `inspect_clean` returned ok True with
    # the team still registered, which is why naming that call in the hint was
    # worse than naming nothing.
    #
    # fallout GI-011 / GI-029 / CT-013 — AND IT CANNOT LAND ANY MORE.
    # `_inspect_clean_preconditions` makes the same `_teams_rung` the gate
    # makes, so the transition refuses on the check the gate refused on and the
    # two doors agree by construction rather than by two copies of a rule. The
    # drive is KEPT rather than deleted, because "following this hint defeats
    # the check that refused" is the harm, and the assertion that it no longer
    # does is the thing worth pinning — a deleted drive proves nothing.
    _arm(fdir)
    clean = foundry_mark_phase_complete("inspect_clean", project_root)
    assert clean.get("ok") is not True, clean
    assert "Active team" in clean["error"], clean
    assert _read_state(fdir)["phase"] == "F2", clean


def test_teams_active_alone_still_states_what_clears_it(run_env, monkeypatch):
    """NFR-005 — PROVE's third drive: teams-active as the ONLY failure.

    A FULL, clean, fully-streamed cycle with the marker written and one
    registered team returned reason "Active teams: foundry-cast" and hint "" —
    an empty remedy. The `inspect` and `grind` branches' sibling arms both read
    their remedy off the team scan; only this copy read neither, and `hint` is
    a required positional on `_GateLadder.fail` so a new arm cannot repeat it.
    """
    project_root, fdir = run_env
    _cycle_recorded_with_mode(fdir, "FULL", "final_gate")
    _write_defects(fdir, [])
    (fdir / ".inspect-clean").write_text("2026-09-03T00:00:00+00:00\n", encoding="utf-8")
    _teams_active(True)

    gate = _gate_over_the_wire(project_root, fdir, monkeypatch)

    assert gate["passed"] is False, gate
    assert "Active team" in gate["reason"], gate["reason"]
    assert "foundry-cast" in gate["reason"], gate["reason"]
    assert gate["hint"].strip(), gate
    assert gate["hint"] == _gates._TEAMS_DOWN_HINT, gate["hint"]
    assert "Foundry-Team-Down" in gate["hint"], gate["hint"]
    assert len(gate["refusals"]) == 1, gate["refusals"]


def test_the_three_gate_branches_that_scan_teams_state_one_remedy(run_env):
    """FR-026 / NFR-005 — the three copies, agreeing on the sentence.

    D-186 names the divergence: "The `inspect` and `grind` branches' identical
    active-teams arms BOTH set hint from teams_result.get(...); only the assay
    branch's copy sets reason alone." The three fell back to three different
    strings — one of them empty — so this asserts the ONE constant they now
    share rather than three literals a later edit can drift apart again.
    """
    project_root, fdir = run_env
    _write_state(fdir, phase="F2", cycle=1)
    _write_manifest(fdir)
    _write_spec(fdir, ["FR-001"])
    _write_defects(fdir, [_fixed_defect(cycle=1)])
    (fdir / ".cast-complete").write_text("x\n", encoding="utf-8")
    (fdir / ".tasks-generated").write_text("x\n", encoding="utf-8")
    _teams_active(True)

    for phase in ("inspect", "grind", "assay"):
        _arm(fdir)
        gate = _gates.foundry_gate(phase, project_root)
        teams = next(
            r for r in gate["refusals"] if "Active team" in r["reason"]
        )
        assert teams["hint"] == _gates._TEAMS_DOWN_HINT, (phase, teams)


# --------------------------------------------------------------------------- #
# D-212 — THE RECORDED WIDTH IS RESOLVED AGAINST `INSPECT_MODES` TOO
#
# Same class as the escalation-status read this cycle fixed: a deciding read
# comparing a persisted field against a typed literal instead of against the
# closed vocabulary that spells it. `_current_inspect_mode` accepted any
# TRUTHY `mode`, so a hand-edited or foreign-written `"delta"` (lowercase) or
# `"BOGUS"` was a recorded width — `_unrecorded_width_problem` answered None
# and `inspect_clean`'s refusal (`recorded_mode.get("mode") == "DELTA"`) is
# false for such a value, so a narrow INSPECT closed and the run reached F4.
#
# BOTH AXES. WHAT is compared: `INSPECT_MODES`, by membership, so the
# vocabulary decides rather than the two literals typed beside it at five
# doors. WHICH entry answers: the LAST one, because walking back past a
# malformed current decision would report an older cycle's FULL as this
# cycle's width and PASS the ASSAY gate that refuses today.
# --------------------------------------------------------------------------- #


#: Values a `mode` field can hold that `INSPECT_MODES` does not spell.
_OUT_OF_VOCABULARY_MODES = ["delta", "full", "BOGUS", "", "FULL ", "Delta", None, 7]


@pytest.mark.parametrize("mode", _OUT_OF_VOCABULARY_MODES)
def test_a_width_outside_the_vocabulary_is_not_a_recorded_width(run_env, mode):
    """GI-009 verbatim: 'whichever Foundry-Phase transition opens an INSPECT
    (start_cast to F2 entry, temper entry, or inspect_start) records the mode;
    Foundry-Next only reports.'

    D-117 ruled that an unrecorded width is not full width. The value that is
    PRESENT and outside the vocabulary is the same fact one layer in: nothing
    this module writes produces it, so nothing has recorded a width the run can
    act on. It now reads as no record at all, and every door refuses through
    `_unrecorded_width_problem`, naming the transition that records one.
    """
    project_root, fdir = run_env
    _cycle_recorded_with_mode(fdir, mode, INSPECT_DELTA_RULE)

    assert _current_inspect_mode(fdir) is None, mode
    problem = _width._unrecorded_width_problem(fdir)
    assert problem is not None, mode
    assert "no recorded width" in problem["reason"], mode
    assert "inspect_start" in problem["hint"], mode


@pytest.mark.parametrize("mode", sorted(INSPECT_MODES))
def test_both_vocabulary_members_still_read_as_the_recorded_width(run_env, mode):
    """The control D-212's fix must not move.

    Parametrised over `INSPECT_MODES` rather than over a typed pair, so a
    member added to casting 1's vocabulary is covered here the day it is added
    — and so a read that had simply started refusing everything, which would
    deadlock every INSPECT in the run, fails here.
    """
    project_root, fdir = run_env
    _cycle_recorded_with_mode(fdir, mode, INSPECT_DELTA_RULE)

    recorded = _current_inspect_mode(fdir)
    assert recorded is not None, mode
    assert recorded["mode"] == mode, recorded
    assert _width._unrecorded_width_problem(fdir) is None, mode


def test_an_out_of_vocabulary_width_is_refused_at_every_door(run_env, monkeypatch):
    """D-117's five-door drive, on the value that is present and wrong.

    Each door reads the width on its own terms — the streams-complete roster,
    `inspect_clean`'s DELTA refusal, the ASSAY gate's positive FULL assertion —
    and a fix wired into two of them leaves the third deciding on a different
    definition of "recorded". Asserted as one test over the whole set, which is
    the shape `test_an_unrecorded_inspect_width_is_refused_at_every_door` uses
    for the absent value.

    `"delta"` is the worst of the set: `inspect_clean` tests `== "DELTA"`, so a
    lowercase spelling let a narrow INSPECT close and move the run to F4.
    """
    project_root, fdir = run_env
    _cycle_recorded_with_mode(fdir, "delta", INSPECT_DELTA_RULE)

    # 1. the streams-complete check
    streams = _check_streams_complete(project_root)
    assert streams["complete"] is False, streams
    assert streams["unrecorded_width"] is True, streams
    assert streams["required"] == [], streams

    # 2. Foundry-Phase('inspect_clean') — the transition that would open F4
    _arm(fdir)
    clean = foundry_mark_phase_complete("inspect_clean", project_root)
    assert clean.get("ok") is not True, clean
    assert clean["unrecorded_width"] is True, clean
    assert _read_state(fdir)["phase"] == "F2", "the run must not reach F4"

    # 3. Foundry-Gate('assay') — the sibling door into the same phase
    gate = _gate_over_the_wire(project_root, fdir, monkeypatch)
    assert gate["passed"] is False, gate
    width = next(
        c for c in gate["checklist"]
        if c["check"].startswith("inspect_ran_at_full_width")
    )
    assert width["ok"] is False, width


@pytest.mark.parametrize("current", [{"mode": "BOGUS"}, {"mode": ""}, "junk", 7, None])
def test_a_malformed_current_width_is_not_answered_with_an_older_valid_one(
    run_env, current
):
    """The axis the obvious fix gets wrong.

    `inspect_modes` is append-only and the LAST entry is the current decision —
    `_note_fix_after_inspect_decision` stamps that entry on the same reading.
    Adding a membership
    test to the old backwards walk would have made this state report cycle 1's
    FULL as cycle 2's width and PASS the ASSAY gate that refuses it today: a
    fix that opens a door currently shut, in the name of closing another.

    So an unusable current decision is answered with no decision, never with an
    older cycle's.
    """
    project_root, fdir = run_env
    _full_cycle_recorded_with(fdir, "first_of_phase", cycle=1)
    state = _read_state(fdir)
    state["inspect_modes"].append(current)
    state["cycle"] = 2
    (fdir / "state.json").write_text(json.dumps(state), encoding="utf-8")

    assert _current_inspect_mode(fdir) is None, current

    _arm(fdir)
    gate = _gates.foundry_gate("assay", project_root)
    assert gate["passed"] is False, gate


def test_the_width_read_is_the_one_path_and_it_consults_the_vocabulary(run_env):
    """THE PROPERTY, derived from the source rather than from the symptom.

    The one width read calls itself THE ONLY READ, and GI-008 and GI-009 both
    name a lazily-computed mode as the violation. The vocabulary check
    therefore belongs in it and nowhere else: a door that re-tested the raw
    field would be a sixth opinion of one string, which is how D-117's five
    permissive fallbacks came to agree on the wrong answer.

    fallout FR-005 / AC-014 / OT-016 — THE SUBJECT IS TWO THINGS NOW.
    ----------------------------------------------------------------
    This asserted `"INSPECT_MODES" in names` over the read's own source. The
    read moved to `foundry_state.py`, which is the stdlib-only leaf and may not
    import `schemas/vocab.py`, so casting 10 gave it the vocabulary as an
    ARGUMENT: `current_inspect_mode(run_dir, cycle=None, *, modes)`. The name
    is now in every CALL SITE's namespace and in none of the callee's, so the
    old assertion cannot pass however correct the tree is.

    Repointing it to the new module would have been the wrong repair, because
    the vocabulary check can only live where the vocabulary is VISIBLE, and
    after the hoist that is the callers. `foundry_state.py` imports `json` and
    `pathlib` and nothing else — `scripts/measure-run.py` reads it with no
    package on the path — so a default `modes=` would have to NAME the closed
    set and a refusal on an unknown one would have to KNOW it, and either ends
    the package-free read the module exists to hold.

    NOT a contract that slipped from ENFORCED to HOPED, which is how this first
    reads and is worth getting right, because the misreading is what would
    tempt a later reader to "restore" the check to the leaf. What a module may
    not delegate is its OWN promise, and this leaf's promise is to be total and
    never to raise — which it keeps whatever `modes` it is handed. It never
    promised to know the vocabulary. So the contract moved to the layer that
    can hold it rather than out of reach, and the pin moves with it.

    WHICH DIRECTION IS DANGEROUS, because the pin guards one of them. A
    DEGENERATE `modes` — empty, None, a non-collection — fails CLOSED through
    the leaf already: `not in modes` is then always true, the read returns
    None, and every door refuses on an unrecorded width. A WIDER set fails
    OPEN, and the leaf cannot tell a lax set from the real one by inspecting
    it; only a caller can. D-212 was precisely a persisted value outside the
    vocabulary reading as a recorded width, so a caller handing over a laxer
    set is that defect returning by the front door — and a leaf-side shape
    check would guard the case that is already safe while missing this one.

    So the pin follows the property to BOTH of its halves, and judges every
    shipped call site instead of one function body:

      * the read still RESOLVES against the set it is handed — membership of
        `modes`, never truthiness;
      * and every shipped caller HANDS IT THE CLOSED SET, derived from the
        source rather than listed here, so a fourth call site is judged the day
        it lands.

    The leaf's own two calls forward the `modes` their caller supplied, which is
    the same guarantee one frame further out, so they are required to forward
    and not to name the vocabulary they cannot import.
    """
    import ast
    import inspect
    import textwrap

    # Half one — the read resolves against the set it is handed.
    tree = ast.parse(textwrap.dedent(inspect.getsource(current_inspect_mode)))
    resolves = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Compare)
        and any(isinstance(op, (ast.In, ast.NotIn)) for op in node.ops)
        and any(
            isinstance(c, ast.Name) and c.id == "modes" for c in node.comparators
        )
    ]
    assert resolves, (
        "the width read no longer tests the persisted mode for MEMBERSHIP of "
        "the vocabulary it is handed, so a value outside it reads as a "
        "recorded width again — D-212."
    )

    # Half two — every shipped caller hands it the closed set. Derived from the
    # package's own source: a listed roster could not fail on a call site added
    # after it was written, which is the whole of what this half protects.
    leaf = Path(foundry_state.__file__).resolve()
    package = leaf.parent.parent
    offenders: list[str] = []
    forwards: list[str] = []
    callers: list[str] = []
    for path in sorted(package.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.Call):
                continue
            called = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
            if called != "current_inspect_mode":
                continue
            site = f"{path.stem}:{node.lineno}"
            handed = next(
                (kw.value for kw in node.keywords if kw.arg == "modes"), None
            )
            spelling = getattr(handed, "id", None)
            # The leaf forwards its OWN parameter — the vocabulary is still the
            # caller's, one frame further out — and cannot name a set it may
            # not import. Everyone else names the closed set itself.
            is_leaf = path == leaf
            (forwards if is_leaf else callers).append(site)
            wanted = "modes" if is_leaf else "INSPECT_MODES"
            if spelling != wanted:
                offenders.append(
                    f"{site} passes modes={spelling!r}, expected {wanted!r}"
                )

    # The vacuity guard, stated as MODULES rather than as a count — this suite's
    # own D-226 rule, "named modules rather than a count alone, because a count
    # passes on any roster of the right size". A bare `len(...) >= 1` would go
    # green with five of the six caller-side sites invisible to the walk, which
    # is the shape of blindness that matters here: the assertion below only
    # judges what this scan managed to SEE.
    reached = {site.split(":")[0] for site in callers}
    assert {"streams", "guidance", "width", "transitions"} <= reached, (
        "the width read's caller-side sites are not all visible to this scan — "
        f"saw {sorted(reached)}, and the four modules that consult the recorded "
        "width are streams, guidance, width and transitions. A derivation that "
        "cannot see a caller cannot judge what that caller hands over."
    )
    assert len(callers) >= 6, (
        f"only {len(callers)} caller-side site(s) found: {callers}. Six is what "
        "the tree carries (streams x2, transitions x2, guidance, width); fewer "
        "means the walk went blind rather than that the package got smaller, "
        "and this assertion is the difference between the two."
    )
    assert forwards, (
        "the leaf's own forwarding calls vanished from the scan; they are the "
        "half that proves an injected vocabulary stays the CALLER's fact one "
        "frame further out rather than becoming the leaf's"
    )
    assert offenders == [], (
        f"width read(s) handed something other than the closed vocabulary: "
        f"{offenders}. The read resolves against whatever set it is given, so "
        "a caller passing a laxer one is D-212 returning through the argument "
        "the leaf cannot refuse for itself."
    )


# --------------------------------------------------------------------------- #
# D-216 — THE RECORDED WIDTH IS THE ENTRY STAMPED FOR **THIS** CYCLE
#
# D-212 fixed WHICH entry answers (the last one) and WHAT its mode is resolved
# against (`INSPECT_MODES`). It left the third axis: WHICH CYCLE the entry
# belongs to. `_current_inspect_mode` returned the newest entry whatever cycle
# it was stamped for, so cycle 1's decision answered "what width is cycle 2" at
# every door that decides on it — the ASSAY gate's positive FULL assertion, the
# streams-complete roster, and the TRACE-skip fence — while
# `_unrecorded_width_problem` implemented D-117 as "is there ANY entry" rather
# than "is there an entry for THIS one".
#
# That third door has since moved, and the cite moves with it: the fence was
# `width._maybe_skip_trace`, a display-time helper `guidance.py` imported,
# DELETED under ruling item 5 (GI-008 / GI-009 / GI-033). The decision is made
# at the transition by `width._trace_skip_from_width` and recorded in the entry
# as `trace_skip`; `guidance._stamp_trace_skip` reads it back through
# `foundry_state.current_inspect_mode`, which is where the cycle match this
# section is about now lives. Same fence, same defect, two live symbols instead
# of one dead one.
#
# The module already held the correct rule twice: `_recorded_stream_scope` and
# `_recorded_prove_roster` both compare `recorded.get("cycle")` against the
# cycle being measured, and `_recorded_stream_scope`'s docstring names the
# omission as GI-008's violation — "a caller reading a scope off a decision made
# for another cycle would be narrowing this one against a width nothing recorded
# for it". The narrow helpers had the guard; the read they are all built on did
# not.
#
# Driven at 2cdce02 through `server.call_tool`: at F2 cycle 2 with a single
# `{cycle: 1, mode: FULL, rule: first_of_phase}` entry and all five streams
# complete, `Foundry-Gate('assay')` PASSED carrying
# `inspect_ran_at_full_width (mode=FULL rule=first_of_phase) ok=True` — an
# assertion about cycle 2 answered by cycle 1's record. With a cycle-1 DELTA
# entry, `_check_streams_complete` returned complete True over cycle 1's
# three-stream roster, which is GI-008's named violation verbatim, and
# `inspect_clean` refused naming "cycle 1 ran at DELTA width" while the run was
# at cycle 2.
#
# An entry for another cycle — EARLIER OR LATER — is not this cycle's width, and
# reads as no record at all: every door refuses through
# `_unrecorded_width_problem`, whose refusal now names the cycle it is refusing
# FOR and what it found instead.
# --------------------------------------------------------------------------- #


def _cycles_recorded_against(
    fdir: Path,
    *,
    stamped: tuple[int, ...],
    state_cycle: int,
    mode: str = "FULL",
    rule: str = "final_gate",
    required_streams: tuple[str, ...] = ("trace", "prove", "test"),
    trace_skip: dict | None = None,
) -> None:
    """A clean, fully-streamed F2 at `state_cycle` whose recorded decisions are
    stamped for `stamped` instead.

    Built on `_full_cycle_recorded_with`, so the run differs from the one that
    opens ASSAY in exactly one field — the entry's `cycle` — and in nothing
    else. `stamped=(N,)` where N == `state_cycle` is therefore the control.

    `trace_skip` is the field the width decision now RECORDS (ruling item 5):
    `_decide_inspect_mode` writes `width._trace_skip_from_width`'s answer into
    the entry and `guidance._stamp_trace_skip` reads it back. Omitted by
    default, because most callers here pin the roster rather than the fence —
    but a test that drives the fence MUST set it, or it proves nothing: an
    entry with no field stamps nothing whatever the cycle says, so the test
    would go green without the cycle match ever being consulted.
    """
    _full_cycle_recorded_with(fdir, rule, cycle=state_cycle)
    state = _read_state(fdir)
    template = state["inspect_modes"][0]
    entries = []
    for c in stamped:
        entry = json.loads(json.dumps(template))
        entry["cycle"] = c
        entry["mode"] = mode
        entry["rule"] = rule
        entry["required_streams"] = list(required_streams)
        entry["stream_scope"] = {
            w: {"scope": "full" if mode == "FULL" else "delta", "detail": "fixture"}
            for w in required_streams
        }
        if trace_skip is not None:
            entry["trace_skip"] = json.loads(json.dumps(trace_skip))
        entries.append(entry)
    state["inspect_modes"] = entries
    state["cycle"] = state_cycle
    (fdir / "state.json").write_text(json.dumps(state), encoding="utf-8")


def test_a_width_stamped_for_an_earlier_cycle_is_not_this_cycles_width(
    run_env, monkeypatch
):
    """GI-008 verbatim: 'The server decides at the inspect_start transition;
    Foundry-Next only reports it', whose named violation is 'a streams-complete
    check that reads a roster nothing recorded'.

    D-117 ruled that an unrecorded width is not full width. A width recorded for
    a DIFFERENT crossing is the same fact: nothing recorded a width for THIS
    INSPECT, and answering with cycle 1's is not a degraded answer, it is a
    false one. The ASSAY gate asserted `mode == "FULL"` positively after D-117
    and still read that FULL off another cycle's row.
    """
    project_root, fdir = run_env
    _cycles_recorded_against(fdir, stamped=(1,), state_cycle=2)

    assert _current_inspect_mode(fdir) is None
    problem = _width._unrecorded_width_problem(fdir)
    assert problem is not None
    assert "no recorded width" in problem["reason"], problem
    assert "cycle 2" in problem["reason"], problem
    assert "cycle 1" in problem["reason"], problem
    assert "inspect_start" in problem["hint"], problem

    gate = _gate_over_the_wire(project_root, fdir, monkeypatch)
    assert gate["passed"] is False, gate
    width = next(
        c for c in gate["checklist"]
        if c["check"].startswith("inspect_ran_at_full_width")
    )
    assert width["ok"] is False, width
    assert "mode=unrecorded" in width["check"], width


def test_a_width_stamped_for_a_later_cycle_is_not_this_cycles_width_either(
    run_env, monkeypatch
):
    """The axis a one-sided fix gets wrong.

    `inspect_modes` is append-only and every entry this module writes carries
    the counter the crossing that wrote it advanced, so a LATER stamp than the
    counter cannot arise on the live path at all — which is exactly why a read
    that tolerates it is reading something no transition produced. Guarding only
    against older entries would leave `[FULL@1, FULL@3]` at cycle 2 answering
    with cycle 3's FULL.
    """
    project_root, fdir = run_env
    _cycles_recorded_against(fdir, stamped=(1, 3), state_cycle=2)

    assert _current_inspect_mode(fdir) is None
    problem = _width._unrecorded_width_problem(fdir)
    assert problem is not None
    assert "cycle 2" in problem["reason"], problem
    assert "cycle 3" in problem["reason"], problem

    gate = _gate_over_the_wire(project_root, fdir, monkeypatch)
    assert gate["passed"] is False, gate


def test_a_delta_roster_stamped_for_another_cycle_is_not_the_roster_read(run_env):
    """GI-008's named violation, verbatim: 'a streams-complete check that reads
    a roster nothing recorded because Next was skipped'.

    `_check_streams_complete` READS `required_streams` off the recorded decision
    and never recomputes it — correct, and the whole point of GI-008 — but it
    read it off whatever entry was newest. On a cycle-1 DELTA entry at cycle 2
    it reported complete True over a three-stream roster decided for a crossing
    that is not this one, and `inspect_clean` then refused naming cycle 1's
    DELTA width while the run stood at cycle 2. Both halves are asserted: the
    roster is not read, and the refusal names the cycle it is refusing FOR.
    """
    project_root, fdir = run_env
    _cycles_recorded_against(
        fdir, stamped=(1,), state_cycle=2, mode="DELTA", rule=INSPECT_DELTA_RULE,
    )

    streams = _check_streams_complete(project_root)
    assert streams["complete"] is False, streams
    assert streams["unrecorded_width"] is True, streams
    assert streams["required"] == [], streams

    _arm(fdir)
    clean = foundry_mark_phase_complete("inspect_clean", project_root)
    assert clean.get("ok") is not True, clean
    assert clean["unrecorded_width"] is True, clean
    assert "cycle 2" in clean["error"], clean
    assert _read_state(fdir)["phase"] == "F2", "the run must not reach F4"


def test_the_entry_stamped_for_this_cycle_is_still_the_recorded_width(
    run_env, monkeypatch
):
    """The control this fix must not move.

    A run whose newest entry IS stamped for the counter is the live path —
    `cast` and `temper` stamp `_current_cycle(fdir)`, `inspect_start` stamps the
    counter it advanced inside the same transaction — so the cycle stamp and the
    counter are equal by construction on every crossing this module performs. A
    guard that also refused THAT would deadlock every INSPECT in the run.
    """
    project_root, fdir = run_env
    _cycles_recorded_against(fdir, stamped=(1, 2), state_cycle=2)

    recorded = _current_inspect_mode(fdir)
    assert recorded is not None
    assert recorded["cycle"] == 2, recorded
    assert recorded["mode"] == "FULL", recorded
    assert _width._unrecorded_width_problem(fdir) is None

    gate = _gate_over_the_wire(project_root, fdir, monkeypatch)
    assert gate["passed"] is True, gate


def test_the_trace_skip_fence_fails_closed_on_a_width_from_another_cycle(run_env):
    """AC-019 / FR-012 — THE ADJACENT PATH.

    A DIFFERENT CALLER of the same read, reached through Foundry-Next rather
    than through the ASSAY gate. D-071 fenced this skip behind the recorded
    width precisely so a FULL roster's trace requirement could not be satisfied
    without TRACE running, and D-117 closed the unrecorded hole underneath it —
    but the fence read `mode == "FULL"` off whatever entry was newest, so cycle
    1's FULL answered "this INSPECT is recorded FULL (rule ...)" for cycle 2.
    Benign in its verdict and false in its provenance, one edit away from the
    auto-stamp D-071 was filed for.

    The marker is asserted absent afterwards: this fence WRITES `.trace-complete`
    on the skip arms, so "did it fail closed" is a question about the filesystem
    and not only about the returned dict.

    fallout GI-008 / GI-009 / GI-033 (ruling item 5) — WHERE THE FENCE WENT.
    `width._maybe_skip_trace` is deleted. The decision is made at the transition
    by `width._trace_skip_from_width`, recorded in the entry as `trace_skip`,
    and `guidance._stamp_trace_skip` reads it back through
    `foundry_state.current_inspect_mode` — which is where the cycle match now
    lives, so this test's subject moved into the read it drives rather than
    away from it. Failing closed is now a None: no recorded entry for THIS
    cycle means no answer at all, which is stronger than the old wrong-
    provenance dict that had to be read to be safe.

    THE ENTRY CARRIES A SKIPPING DECISION ON PURPOSE. An entry with no
    `trace_skip` field stamps nothing whatever the cycle says, so a fixture
    without one would pass this test without the cycle match ever being
    consulted — green for a reason that has nothing to do with the defect. So
    cycle 1 is recorded DELTA with an empty diff, the one shape
    `_trace_skip_from_width` answers `skip: True` for: if the cycle match broke,
    `.trace-complete` WOULD be written for cycle 2 off cycle 1's width, which is
    exactly the auto-stamp D-071 was filed for.
    """
    project_root, fdir = run_env
    _cycles_recorded_against(
        fdir,
        stamped=(1,),
        state_cycle=2,
        mode="DELTA",
        rule="delta",
        trace_skip={
            "skip": True,
            "reason": (
                "DELTA width and the GRIND diff is empty — there are no touched "
                "symbols for TRACE to walk"
            ),
            "details": {"inspect_mode": "DELTA", "touched_files": []},
        },
    )
    (fdir / _artifacts._stream_marker("trace")).unlink()

    decision = _guidance._stamp_trace_skip(fdir)

    assert decision is None, decision
    assert not (fdir / _artifacts._stream_marker("trace")).exists(), (
        "the fence auto-stamped TRACE complete off another cycle's width"
    )


def test_the_widening_re_open_does_not_widen_another_cycles_delta(run_env):
    """ST-005 / AC-016 — a second adjacent path: a different TRANSITION.

    The F2->F2 widening re-open exists to take a DELTA INSPECT to FULL before
    ASSAY, and it is refused when this cycle's recorded width is not DELTA
    because that call is then a stray second `inspect_start` (D-057). It read
    the same unguarded value, so cycle 1's DELTA licensed a widening of cycle 2
    — advancing the counter and stamping a FULL decision for a crossing whose
    own width nothing had recorded. The counter is asserted unmoved, which is
    the property AC-013 states for every refused crossing.
    """
    project_root, fdir = run_env
    _cycles_recorded_against(
        fdir, stamped=(1,), state_cycle=2, mode="DELTA", rule=INSPECT_DELTA_RULE,
    )

    _arm(fdir)
    result = foundry_mark_phase_complete("inspect_start", project_root)

    assert result.get("ok") is not True, result
    assert "unrecorded" in result["error"], result
    assert _current_cycle(fdir) == 2, "a refused crossing advanced the counter"


def test_the_cycle_stamp_is_checked_in_the_one_width_read(run_env):
    """THE PROPERTY, derived from the source rather than from the symptom.

    `_current_inspect_mode`'s docstring calls itself THE ONLY READ, and the
    reason D-117's five permissive fallbacks agreed on the wrong answer is that
    each door decided for itself what "recorded" meant. WHICH CYCLE an entry
    belongs to is the same kind of question as WHAT its mode spells, so it is
    answered in the same one place — through `_current_cycle`, the package's one
    guarded reader of the counter (D-059), never off a raw `state["cycle"]`.
    """
    import ast
    import inspect
    import textwrap

    tree = ast.parse(textwrap.dedent(inspect.getsource(current_inspect_mode)))
    names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
    # fallout FR-005 / AC-014 / OT-016 — THE SPELLING MOVED WITH THE SYMBOL.
    # This pinned the name `_current_cycle`. Casting 10 consolidated the two
    # byte-identical readers into `foundry_state.current_cycle` (GI-024, Holmes
    # `share-2`), so the call this rule is about is spelled `current_cycle` in
    # the source it derives from. The name is taken from the imported FUNCTION
    # rather than typed, so a future rename moves the pin with it instead of
    # leaving a derived scan looking for a word nothing says any more.
    reader = _current_cycle.__name__
    assert reader == "current_cycle", reader
    assert reader in names, (
        "_current_inspect_mode no longer compares the entry's cycle stamp "
        f"against the server counter through `{reader}`, so a decision "
        "recorded for another crossing reads as this INSPECT's width again "
        "— D-216."
    )


# --------------------------------------------------------------------------- #
# D-219 — THE F5 ENTRY OPENS A FRESH INSPECT, SO IT CLEARS THE PREVIOUS ONE'S
# COMPLETION STATE.
#
# GI-009's "one rule, two doors" was carried across to the `temper` branch as
# the WIDTH half only: the branch recorded FULL / first_of_phase with the
# five-stream roster and left the F2 INSPECT's `.{stream}-complete` markers
# where ASSAY found them, so the roster it had just recorded was satisfied on
# arrival by markers written before ASSAY ran. `grind_start` and `assay_fail`
# have cleared those markers since they were written, under the reason
# "completion state must stay honest" — a property of opening a fresh INSPECT,
# which is what this door does.
# --------------------------------------------------------------------------- #

_F2_STREAM_MARKERS = ("trace", "prove", "test", "research_audit", "test01")


def _mark_streams_complete(fdir: Path, streams=_F2_STREAM_MARKERS) -> None:
    """Write the completion sentinel a stream writes when it reports."""
    for stream in streams:
        (fdir / _artifacts._stream_marker(stream)).write_text(
            f"{foundry_state.now_iso()} cycle=1\n", encoding="utf-8"
        )


def test_the_f5_entry_clears_the_previous_inspects_completion_markers(run_env):
    """AC-016 verbatim: 'The phase-entry transition into F2 and into F5 records
    FULL with rule first_of_phase'. GI-009 verbatim: 'One rule: whichever
    Foundry-Phase transition opens an INSPECT ... records the mode'.

    D-219, driven. Five markers on disk from the F2 INSPECT, a run at F4, and
    `foundry_mark_phase_complete('temper')` returned ok with mode FULL, rule
    first_of_phase and `required_streams ['trace','prove','test',
    'research_audit','test01']` — while every one of those five markers was
    still on disk, so `_check_streams_complete` answered `complete True,
    missing ''` for an INSPECT in which none of the five had run.

    The roster the transition records and the completion state it opens on are
    the same rule seen twice; recording the first without clearing the second
    is what let a TEMPER INSPECT report five streams complete before it began.
    """
    project_root, fdir = run_env
    _write_state(fdir, phase="F4", cycle=5)
    _write_manifest(fdir)
    _mark_streams_complete(fdir)
    _arm(fdir)

    result = foundry_mark_phase_complete("temper", project_root)

    assert result["ok"] is True, result
    assert result["phase"] == "F5"
    assert result["inspect_rule"] == "first_of_phase"
    assert set(_F2_STREAM_MARKERS) <= set(result["required_streams"]), result

    for stream in _F2_STREAM_MARKERS:
        assert not (fdir / _artifacts._stream_marker(stream)).exists(), (
            f"{stream}'s F2 completion marker survived the F5 entry"
        )

    # ...and the consequence the markers had, which is the harm: the roster the
    # transition just recorded is now genuinely outstanding.
    streams = _check_streams_complete(project_root)
    assert streams["complete"] is False, streams
    assert set(_F2_STREAM_MARKERS) <= set(streams["missing"].split()), streams


def test_the_f5_entry_leaves_completion_state_alone_when_it_refuses(run_env):
    """ST-006 / GI-009 — the ordering, not just the clearing.

    A refused crossing must leave the run exactly as it found it: the mode is
    recorded after the sweep refusal point and so is the marker clear. Driven
    through the refusal this branch already has — a committed evidence log that
    no longer reproduces — because a clear placed before it would destroy the
    F2 INSPECT's completion state for a transition that never happened, and the
    run would sit in F4 unable to say what it had already run.
    """
    project_root, fdir = run_env
    _write_state(fdir, phase="F4", cycle=5)
    _write_manifest(fdir)
    _mark_streams_complete(fdir)
    _evidence_log(
        project_root, "casting-1-handler.log",
        "echo the-handler-calls-the-store", "the-handler-does-not\n",
    )
    _git(Path(project_root), "add", "-A")
    _git(Path(project_root), "commit", "-qm", "evidence")
    _arm(fdir)

    result = foundry_mark_phase_complete("temper", project_root)

    assert result.get("ok") is not True, result
    assert _read_state(fdir)["phase"] == "F4"
    for stream in _F2_STREAM_MARKERS:
        assert (fdir / _artifacts._stream_marker(stream)).exists(), (
            f"{stream}'s marker was cleared by a transition that was refused"
        )


def test_the_grind_doors_clear_through_the_same_one_spelling(run_env):
    """FR-013 / CT-002 — THE ADJACENT PATH: the two doors that already cleared.

    `_clear_stream_completion_markers` was extracted from two byte-identical
    loops in `grind_start` and `assay_fail`, and those two transitions are a
    different caller of it than the `temper` entry the defect was found on.
    Both are driven here over the whole recordable vocabulary — not just the
    five streams a FULL roster names — because the loop they carried derived
    its family from `VALID_STREAMS` and the extraction has to keep deriving it:
    a helper that quietly narrowed to the roster would leave `sight`, `probe`,
    `coverage_diff` and `flow_trace` stale across every GRIND cycle.
    """
    project_root, fdir = run_env

    # fallout GI-011 / GI-032 / ST-015 — BOTH DOORS HAVE PRECONDITIONS NOW.
    # `_grind_start_preconditions` requires open defects and a `.tasks-generated`
    # marker; the marker is written below and the ledger here. The subject is
    # which markers the transition CLEARS, so it has to get through the door
    # first — a refused transition clears nothing, which is exactly what the
    # sibling test above asserts about `temper`.
    _write_defects(fdir, [_open_live()])
    _write_manifest(fdir)

    for token in ("grind_start", "assay_fail"):
        _write_state(fdir, phase="F2" if token == "grind_start" else "F4", cycle=2)
        _mark_streams_complete(fdir, sorted(_streams.VALID_STREAMS))
        (fdir / _artifacts.INSPECT_CLEAN_MARKER).write_text("x\n", encoding="utf-8")
        (fdir / _artifacts.TASKS_GENERATED_MARKER).write_text("x\n", encoding="utf-8")
        _arm(fdir)

        result = foundry_mark_phase_complete(token, project_root)

        assert result["ok"] is True, (token, result)
        assert result["phase"] == "F3", (token, result)
        for stream in sorted(_streams.VALID_STREAMS):
            assert not (fdir / _artifacts._stream_marker(stream)).exists(), (token, stream)
        assert not (fdir / _artifacts.INSPECT_CLEAN_MARKER).exists(), token
        assert not (fdir / _artifacts.TASKS_GENERATED_MARKER).exists(), token


def test_no_phase_branch_spells_the_marker_family_by_hand():
    """FR-013 / CT-002 verbatim: the stream vocabulary is READ, never re-typed.

    The class behind D-219 is that the marker-clear list was a hand-built copy
    of the vocabulary living inside a phase branch, so a third door needing it
    got nothing — and the fix would have been a third copy if it were written
    the way the first two were. `_stream_marker`'s own docstring records what
    that cost the last time ("Five call sites spelled f'.{stream}-complete'").

    Derived from the module's own AST rather than from a list of branches, so a
    branch added tomorrow is covered the day it is added: no call to
    `_stream_marker` may appear anywhere inside `_phase_transition`, which is
    where every phase branch lives (`foundry_mark_phase_complete` is the thin
    wrapper that guards and dispatches into it). Clearing goes through
    `_clear_stream_completion_markers`, and reading a single named marker
    belongs to the helper that owns that marker.
    """
    import ast
    import inspect
    import textwrap

    source = textwrap.dedent(inspect.getsource(_transitions._phase_transition))
    # The guard is only worth what its subject is: assert the branches really
    # are in this function before asserting what they do not contain.
    assert 'phase == "temper"' in source and 'phase == "grind_start"' in source, (
        "_phase_transition no longer holds the phase branches — point this "
        "guard at whatever does"
    )
    tree = ast.parse(source)
    spelled = [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "_stream_marker"
    ]
    assert not spelled, (
        "_phase_transition builds a stream-marker path itself at line(s) "
        f"{spelled} — clear through _clear_stream_completion_markers so every "
        "door that opens an INSPECT clears the same family"
    )


# --------------------------------------------------------------------------- #
# D-221 — THE OTHER TWO INSPECT-OPENING DOORS, AND THE RULE THAT BINDS ALL FOUR.
#
# D-219 closed the `temper` door and RULED THE REMAINING TWO SAFE IN PROSE:
# "`cast` enters F2 from F1, which is entered by `start_cast` before any stream
# can have reported; `inspect_start` enters F2 from F3, and F3 is entered ONLY
# by the two doors below, both of which clear on the way in." Both halves are
# false at the doors themselves — `inspect_start` admits F2 as a source (the
# widening re-open, which is the FINAL GATE), and `start_cast` carries no
# entry-source precondition at all — so the class came back one cycle later at
# the one crossing US-004 calls "every final gate still runs everything at full
# width".
#
# The tests below drive both doors, and the last one retires the argument: the
# obligation is derived from the module's own AST — which door opens an INSPECT
# read off `_<token>_preconditions`, what it then owes read off the matching
# branch of `_phase_transition` — so a fifth door inherits it the day it is
# written rather than the cycle after it ships.
# --------------------------------------------------------------------------- #


def test_the_widening_re_open_clears_the_delta_inspects_completion_markers(run_env):
    """US-004 verbatim: 'every final gate still runs everything at full width.'
    AC-017 verbatim: 'In FULL mode the streams-complete check requires trace,
    prove, test, research_audit and test01'.

    D-221, driven end to end. A DELTA cycle 2 runs its roster and comes back
    clean; `inspect_start` from F2 is the widening re-open, which advances the
    counter to 3, records FULL / final_gate and names the five-stream roster —
    and left the DELTA cycle's markers on disk, so `_check_streams_complete`
    reported trace, prove and test COMPLETE for a FULL INSPECT in which none of
    them had run at that width. The roster a transition records and the
    completion state it opens on are one rule; this door recorded the first and
    skipped the second.
    """
    project_root, fdir = run_env
    _write_state(fdir, phase="F3", cycle=1)
    _write_defects(fdir, [{**_open_live(), "status": "fixed", "fixed_in_cycle": 1}])
    _write_manifest(fdir)
    _grind_touching(project_root, fdir, "src/handler.py")
    _arm(fdir)

    delta = foundry_mark_phase_complete("inspect_start", project_root)
    assert delta["inspect_mode"] == "DELTA", delta
    assert delta["cycle"] == 2, delta

    # The DELTA roster runs and reports — this is the state the widening
    # crossing is genuinely reached from, not a synthetic marker plant.
    for stream in delta["required_streams"]:
        foundry_mark_stream(stream, 2, items_checked=40, items_total=40,
                            project_root=project_root)
        assert (fdir / _artifacts._stream_marker(stream)).exists(), stream

    _arm(fdir)
    widened = foundry_mark_phase_complete("inspect_start", project_root)

    assert widened["ok"] is True, widened
    assert widened["inspect_mode"] == "FULL", widened
    assert widened["inspect_rule"] == "final_gate", widened
    assert widened["widened"] is True, widened
    assert widened["cycle"] == 3, widened

    # Every marker the DELTA cycle wrote is gone, and the transition says so.
    for stream in delta["required_streams"]:
        assert not (fdir / _artifacts._stream_marker(stream)).exists(), (
            f"{stream}'s DELTA marker survived the widening re-open"
        )
    assert set(widened["cleared_markers"]) >= {
        _artifacts._stream_marker(s) for s in delta["required_streams"]
    }, widened

    # ...and the harm those markers did: the roster the final gate just
    # recorded is now genuinely outstanding, all five of it.
    streams = _check_streams_complete(project_root)
    assert streams["complete"] is False, streams
    assert set(widened["required_streams"]) <= set(streams["missing"].split()), streams


def test_the_f2_entry_clears_completion_markers_an_earlier_inspect_left(run_env):
    """AC-016 verbatim: 'The phase-entry transition into F2 and into F5 records
    FULL with rule first_of_phase'. GI-009 verbatim: 'whichever Foundry-Phase
    transition opens an INSPECT ... records the mode'.

    D-221's second door. `cast` was ruled safe because F1 "is entered by
    `start_cast` before any stream can have reported" — but `start_cast` has no
    entry-source precondition, so F1 is reachable from a phase whose INSPECT
    already ran. Driven exactly as the reproduction did: a run at F2 with all
    five markers, `start_cast` back to F1, then `cast`. The transition returned
    ok with the five-stream FULL roster and every marker untouched, so four of
    the five read COMPLETE for the run's FIRST INSPECT.
    """
    project_root, fdir = run_env
    _write_state(fdir, phase="F2", cycle=4)
    _write_manifest(fdir)
    _mark_streams_complete(fdir)
    (fdir / _artifacts.TASKS_GENERATED_MARKER).write_text("x\n", encoding="utf-8")

    _arm(fdir)
    assert foundry_mark_phase_complete("start_cast", project_root)["phase"] == "F1"
    for stream in _F2_STREAM_MARKERS:
        assert (fdir / _artifacts._stream_marker(stream)).exists(), (
            f"{stream}'s marker was cleared by start_cast, which opens no INSPECT"
        )

    _arm(fdir)
    result = foundry_mark_phase_complete("cast", project_root)

    assert result["ok"] is True, result
    assert result["phase"] == "F2"
    assert result["inspect_rule"] == "first_of_phase", result
    assert set(_F2_STREAM_MARKERS) <= set(result["required_streams"]), result

    for stream in _F2_STREAM_MARKERS:
        assert not (fdir / _artifacts._stream_marker(stream)).exists(), (
            f"{stream}'s marker survived the F2 entry"
        )
    assert not (fdir / _artifacts.TASKS_GENERATED_MARKER).exists()

    streams = _check_streams_complete(project_root)
    assert streams["complete"] is False, streams
    assert set(_F2_STREAM_MARKERS) <= set(streams["missing"].split()), streams


def test_the_grind_to_inspect_crossing_clears_what_the_grind_left(run_env):
    """THE ADJACENT PATH: the OTHER arm of the door the defect was found on.

    D-221 was driven on `inspect_start`'s F2->F2 widening arm. The F3->F2 arm
    is a different transition through the same changed lines, and it is the
    ordinary GRIND->INSPECT crossing every cycle takes — so the clear had to be
    proved harmless there before it could be made unconditional. It is also the
    arm whose safety was argued rather than enforced ("F3 is entered ONLY by
    the two doors below, both of which clear on the way in"): a marker written
    at any point DURING the GRIND belongs to no INSPECT, and this crossing is
    where it dies.
    """
    project_root, fdir = run_env
    _write_state(fdir, phase="F3", cycle=1)
    _write_defects(fdir, [{**_open_live(), "status": "fixed", "fixed_in_cycle": 1}])
    _write_manifest(fdir)
    _grind_touching(project_root, fdir, "src/handler.py")
    # Reported while the run was in GRIND — after `grind_start` cleared, which
    # is the window the by-construction argument did not cover.
    foundry_mark_stream("test", 1, items_checked=9, items_total=9,
                        project_root=project_root)
    assert (fdir / _artifacts._stream_marker("test")).exists()
    _arm(fdir)

    result = foundry_mark_phase_complete("inspect_start", project_root)

    assert result["ok"] is True, result
    assert result["widened"] is False, result
    assert result["cycle"] == 2, result
    assert _artifacts._stream_marker("test") in result["cleared_markers"], result
    assert not (fdir / _artifacts._stream_marker("test")).exists()

    # The ordinary crossing is otherwise untouched: it still decides its own
    # width and still names its own roster as outstanding.
    assert result["inspect_mode"] == "DELTA", result
    streams = _check_streams_complete(project_root)
    assert streams["complete"] is False, streams
    assert "test" in streams["missing"].split(), streams


def test_a_refused_inspect_start_leaves_completion_state_alone(run_env):
    """AC-013 verbatim: 'refuses the transition naming any log whose output
    mismatches; the cycle counter does not advance on refusal.'

    THE ORDERING, on the door D-221 changed. The clear sits after the sweep
    refusal for the same reason the `temper` branch's does: a crossing the
    server refused must leave the run exactly as it found it, or a mismatched
    evidence log would cost the INSPECT its completion state as well as its
    transition — and the lead would be left in F2 unable to say what had
    already run.
    """
    project_root, fdir = run_env
    _write_state(fdir, phase="F3", cycle=1)
    _write_defects(fdir, [{**_open_live(), "status": "fixed", "fixed_in_cycle": 1}])
    _write_manifest(fdir)
    _evidence_log(
        project_root, "casting-1-handler.log",
        "echo the-handler-calls-the-store", "the-handler-does-not\n",
    )
    _grind_touching(project_root, fdir, "src/handler.py")
    _mark_streams_complete(fdir)
    _arm(fdir)

    result = foundry_mark_phase_complete("inspect_start", project_root)

    assert result.get("ok") is not True, result
    assert _current_cycle(fdir) == 1, "a refused crossing advanced the counter"
    for stream in _F2_STREAM_MARKERS:
        assert (fdir / _artifacts._stream_marker(stream)).exists(), (
            f"{stream}'s marker was cleared by a transition that was refused"
        )


def test_every_inspect_opening_door_clears_the_previous_inspects_completion_state():
    """GI-009 verbatim: 'One rule: whichever Foundry-Phase transition opens an
    INSPECT ... records the mode.' AC-017 verbatim: 'In FULL mode the
    streams-complete check requires trace, prove, test, research_audit and
    test01'.

    THE CLASS, not the instance. D-219 and D-221 are the same defect at two
    different doors, and what carried it from one to the other was that the
    obligation lived in a docstring paragraph arguing which doors could safely
    omit it. Prose cannot be evaluated when a door is added, so the second
    instance was already latent when the first was closed.

    Derived here from the module's own AST instead. A transition that calls
    `_decide_inspect_mode` IS a door that opens an INSPECT — that call is what
    makes it one — and every such door must also call
    `_clear_stream_completion_markers`. The width a transition records and the
    completion state it opens on are one rule; this test is the only thing that
    makes them one rule for a door nobody has written yet.

    fallout FR-058 / GI-029 / AC-056 — THE TWO HALVES NOW SIT IN TWO
    FUNCTIONS, WHICH IS WHY THIS TEST JOINS THEM.
    ---------------------------------------------------------------
    D-032 moved `_decide_inspect_mode` into `_cast_preconditions`,
    `_inspect_start_preconditions` and `_temper_preconditions`, so a door is
    identified by its ROUTINE now. The clearing did NOT move and must not: it
    is an EFFECT, and an effect belongs below the refusal, inside the branch,
    where it runs only on a crossing that was allowed — clearing the previous
    INSPECT's markers on a transition the server went on to refuse is the
    harm's mirror image.

    So membership is derived from the routine and the obligation is asserted
    on the matching branch. That join IS the repointed claim: it is the one
    assertion in the suite that says the door that decides a width and the
    door that clears the markers are the same door, now that the two halves
    are written in two places.
    """
    import ast
    import inspect
    import textwrap

    source = textwrap.dedent(inspect.getsource(_transitions._phase_transition))
    tree = ast.parse(source)

    def _called_names(nodes: list[ast.stmt]) -> set[str]:
        names: set[str] = set()
        for statement in nodes:
            for node in ast.walk(statement):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                    names.add(node.func.id)
        return names

    # The if/elif chain: each branch's token is its `phase == "<literal>"` test
    # and its body is its own, because an elif lives in `orelse`.
    branches: dict[str, set[str]] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.If):
            continue
        test = node.test
        if (
            isinstance(test, ast.Compare)
            and isinstance(test.left, ast.Name)
            and test.left.id == "phase"
            and len(test.ops) == 1
            and isinstance(test.ops[0], ast.Eq)
            and isinstance(test.comparators[0], ast.Constant)
            and isinstance(test.comparators[0].value, str)
        ):
            branches[test.comparators[0].value] = _called_names(node.body)

    # The guard is only worth what its subject is.
    assert {"cast", "temper", "inspect_start", "grind_start"} <= set(branches), (
        "_phase_transition no longer holds the phase branches this guard reads "
        f"— found {sorted(branches)}"
    )

    # The doors, read off the routines — `_<token>_preconditions` is where the
    # width decision lives since D-032, and GI-031 puts exactly one routine
    # behind every transition token. Resolved from `PHASE_TOKENS` rather than
    # scanned, so a token whose routine went missing fails here instead of
    # narrowing the set this test is about.
    opens_inspect = set()
    for token in _transitions.PHASE_TOKENS:
        routine = getattr(_transitions, f"_{token}_preconditions", None)
        assert routine is not None, (
            f"PHASE_TOKENS names {token!r} and transitions.py defines no "
            f"_{token}_preconditions — the door this guard reads has no routine"
        )
        routine_calls = _called_names(
            ast.parse(textwrap.dedent(inspect.getsource(routine))).body
        )
        if "_decide_inspect_mode" in routine_calls:
            opens_inspect.add(token)
    assert opens_inspect >= {"cast", "temper", "inspect_start"}, (
        "the doors that open an INSPECT are no longer the ones that decide a "
        f"width — found {sorted(opens_inspect)}"
    )
    # ...and every one of them has a branch here to be judged. A door whose
    # routine decides a width and whose token has no branch would drop out of
    # the obligation below without failing anything.
    assert opens_inspect <= set(branches), sorted(opens_inspect - set(branches))

    silent = sorted(
        token for token in opens_inspect
        if "_clear_stream_completion_markers" not in branches[token]
    )
    assert not silent, (
        f"Foundry-Phase(phase={silent!r}) opens an INSPECT — it calls "
        "_decide_inspect_mode and records a roster — without clearing the "
        "previous INSPECT's completion markers, so that roster is satisfied "
        "on arrival by streams that never ran at this width (D-219, D-221). "
        "Call _clear_stream_completion_markers after the sweep refusal and "
        "before the phase write."
    )


# --------------------------------------------------------------------------- #
# D-239 — the diff every width decision reads names paths, not display strings
# --------------------------------------------------------------------------- #


#: The two non-ASCII paths D-239 was driven on, written as real characters so
#: the fixtures create the filenames the defect is about. At default
#: `core.quotepath` git prints each of these quoted and octal-escaped.
_NON_ASCII_VERIFIER = "schemas/modèle.py"
_NON_ASCII_KEY_FILE = "src/contrôleur.py"


def test_a_non_ascii_verifier_path_in_the_grind_diff_still_forces_full(run_env):
    """FR-011 verbatim: 'FULL when: ... the GRIND diff touches vocab.py,
    SCHEMAS/, gate/orchestrator code, agent/skill prose, or the spec. Otherwise
    DELTA.' ST-006 / AC-016 / OT-013.

    D-239. `_grind_diff` ran `git diff --name-only` with neither `-z` nor
    `core.quotepath=false`, so a non-ASCII path came back as git's DISPLAY
    string — `"schemas/mod\\303\\250le.py"`, quotes and octal escapes included —
    and was returned verbatim. Driven at git 2.50.1: `vocab.is_verifier_path` on
    that string is False, because the LEADING QUOTE defeats the `(?:^|/)schemas/`
    anchor. So the transition recorded DELTA immediately after the machinery
    that judges the build was modified, which is the exact outcome ST-006 and
    AC-016 exist to prevent: a narrowed INSPECT run by a verifier that just
    changed.

    Nothing about the escaping is asserted here — the assertion is the DECISION,
    because that is what the run acts on.
    """
    project_root, fdir = run_env
    _write_state(fdir, phase="F3", cycle=1)
    _write_defects(fdir, [_open_live()])
    _write_manifest(fdir)
    _grind_touching(project_root, fdir, _NON_ASCII_VERIFIER)
    _arm(fdir)

    result = foundry_mark_phase_complete("inspect_start", project_root)

    assert result["inspect_mode"] == "FULL", result
    assert result["inspect_rule"] == "verifier_touched", result
    recorded = _read_state(fdir)["inspect_modes"][-1]
    # The recorded touched-files list is what the sweep scope and the F6 report
    # both read, so it carries the path and not the rendering of it.
    assert _NON_ASCII_VERIFIER in recorded["touched_files"], recorded
    for spelling in recorded["touched_files"]:
        assert '"' not in spelling and "\\3" not in spelling, recorded


def test_a_touched_non_ascii_key_file_reads_as_touched(run_env):
    """FR-042 / GI-002 verbatim: 'the set of logs swept is delta by default
    (CASTING KEY_FILES INTERSECT THE DIFF, or command references a touched
    file)'. AC-014 / AC-017 / FR-047 / ST-007 / OT-016.

    D-239's second arm. The diff strings are compared WHOLE against the manifest
    `key_files` and against the TEST-01 scope, so an escaped spelling makes a
    touched declared file read as UNTOUCHED: the per-stream scope asserts a
    false negative, and `select_sweep_scope` returns [] — so the boundary
    re-executes none of that casting's evidence logs at the one crossing GI-002
    says re-executes them.

    A DELTA cycle is used deliberately, because that is the width at which the
    intersection decides anything: at FULL every log is swept regardless, so the
    bug is invisible there.
    """
    project_root, fdir = run_env
    _write_state(fdir, phase="F3", cycle=1)
    _write_defects(fdir, [_open_live()])
    _write_manifest(
        fdir, castings=[{"id": 1, "key_files": ["src/handler.py", _NON_ASCII_KEY_FILE]}]
    )
    _grind_touching(project_root, fdir, _NON_ASCII_KEY_FILE)
    _arm(fdir)

    result = foundry_mark_phase_complete("inspect_start", project_root)

    # Not a verifier path, so the width is DELTA and the intersection matters.
    assert result["inspect_mode"] == "DELTA", result
    recorded = _read_state(fdir)["inspect_modes"][-1]
    assert recorded["touched_files"] == [_NON_ASCII_KEY_FILE], recorded


def test_the_diff_helper_returns_the_paths_git_names(run_env):
    """D-239, on the helper both call sites now share.

    `git_changed_paths` is asserted directly as well as through the decision,
    because it is the name `foundry_spawn`'s cycle-context diff imports — a
    sibling casting's call site this file cannot drive. The contract that site
    depends on is exactly this: every path spelled as it is on disk, whatever
    bytes it holds, and `ok` False kept distinct from an empty diff.
    """
    project_root, _fdir = run_env
    root = Path(project_root)
    base = _git(root, "rev-parse", "HEAD")
    for rel in (_NON_ASCII_VERIFIER, _NON_ASCII_KEY_FILE, "src/plain.py"):
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("x = 1\n", encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "non-ascii work")

    diff = git_changed_paths(project_root, base, "HEAD")

    assert diff["ok"] is True, diff
    assert diff["files"] == sorted(
        [_NON_ASCII_VERIFIER, _NON_ASCII_KEY_FILE, "src/plain.py"]
    ), diff
    assert diff["error"] == "", diff

    # An UNKNOWN diff is not an empty one, and the two must stay
    # distinguishable — a caller that confused them would run a delta roster off
    # a diff it never obtained.
    unknown = git_changed_paths(project_root, "0" * 40, "HEAD")
    assert unknown["ok"] is False, unknown
    assert unknown["files"] == [], unknown
    assert unknown["error"], unknown


# fallout D-057 (LEAD RULING, GRIND cycle 3) — D-239'S SECOND CALL SITE IS GONE,
# SO THE PIN THAT HELD IT TO THE FIRST GOES WITH IT.
#
# `test_the_trace_skip_reads_the_same_spelling_the_width_does` stood here. It
# drove `width._trace_skip_check` over a non-ASCII declared file and asserted
# the skip decision read the same spelling `_grind_diff` reads, because the two
# were the pair D-239 found running separate copies of one unguarded git
# invocation. The ruling recorded at `orchestration/width.py`'s D-057 block
# deleted `_trace_skip_check` outright: no requirement of this run names a
# last-clean-TRACE skip, and its only historical caller had already gone under
# the GI-033 leaf-moves ruling.
#
# The rule the deleted test served is NOT retired with it, which is why nothing
# replaces it here. `test_the_diff_helper_returns_the_paths_git_names` above
# drives `git_changed_paths` directly, and the two arms above that drive it
# through the width decision — so "every path spelled as it is on disk" is still
# pinned on the helper itself and on the call site that survives. What is gone
# is a second caller to hold it to, not the property.
