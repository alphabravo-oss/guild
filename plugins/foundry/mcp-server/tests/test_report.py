"""GI-006 / CT-014 / AC-036 — the generated end-of-run report.

Every test here drives `foundry_report.generate_report` against a real run
directory on disk and reads the two documents back, because the property under
test is always "what did it WRITE", never "what did it return". A report whose
return value said eleven sections and whose file carried nine would pass an
assertion on the return and fail the DONE gate, which reads the file.

The fixture is `tests/fixtures/escalation/finer_boundary_run/` — the synthetic
archive whose scenario is stated in casting 5's prompt and whose record ids are
FROZEN there, because casting 3's escalation tests read the same directory.
`report_env` copies it into `tmp_path` so a test may mutate its copy freely;
nothing here ever writes into `tests/fixtures/`.

Shape follows `tests/test_escalation.py`: a fixture that yields the run
directory, small builders beside it, and one docstring per test quoting the
requirement it proves.

GI-010 / GI-026 — THE WAVE-2 REPOINT, NOW LANDED
------------------------------------------------
This module reached the monolith lazily, inside test bodies and fixtures. The
imports were written against the destinations casting 2's split would define,
and ten registers stood RED until it landed:

    orchestration/transitions.py   foundry_mark_phase_complete
    orchestration/streams.py       foundry_mark_stream
    orchestration/fix_gate.py      foundry_mark_defect_fixed
    orchestration/teams.py         _check_active_teams
    orchestration/width.py         INSPECT_BOUNDARY_SHA_MARKER
    orchestration/gates.py         _open_defects_by_tier
    orchestration/directives.py    foundry_defects_to_tasks
    orchestration/spend.py         _spend_summary
    orchestration/report_seal.py   _lead_header_lines, _carried_lead_prose

Four reaches were green immediately, because casting 10's own group 1 put their
symbols in the leaf: ``_now`` (as ``foundry_state.now_iso``),
``DISPATCH_PHASE_TO_RUN_PHASE``, the REPORT.md heading splitter (as
``foundry_state.markdown_sections``) and the unreported-dispatch walk.

All of them are green now, so the list is the record of what the repoint cost
and not a live exception: a red register in this module is a defect, not a wait.

WHAT THE LANDING ITSELF COST, which is the part worth carrying forward. The
mechanical ``fo.X`` -> ``X`` rewrite silently broke three monkeypatch sites —
rebinding a bare name in THIS module patches no production code — and the split
made the target a consumer module's binding rather than the definition's. That
is what ``_no_active_teams`` exists for. Two registers also asserted the
pre-FR-019 ``halted_reason`` string and one asserted a fixture happened to
predate ``fallout_of``; both now state the shape they are about instead of
borrowing it from something with no obligation to keep it.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from foundry_mcp.schemas import vocab
from foundry_mcp.schemas.vocab import (
    CONVERGENCE_TARGET,
    DEFECT_TIER_OR_UNKNOWN,
    HANDOFF_EVENT_LEAD_FIX,
    REPORT_JSON_FILENAME,
    REPORT_MD_FILENAME,
    REPORT_REQUIRED_SECTIONS,
    RUN_PHASE_HALTED,
    SPEND_LEDGER_FILENAME,
    THUNDER_VIPER_BASELINE,
    TIER_UNKNOWN,
    halt_reason,
)
from foundry_mcp.tools import foundry_report as fr
from foundry_mcp.tools import foundry_state as fs
from foundry_mcp.tools.artifacts import report_document_status
from foundry_mcp.tools.foundry_report import generate_report

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "escalation" / "finer_boundary_run"

#: The eight artifacts the fixture contract freezes. Named here so a test can
#: assert the SET rather than each file, and so a file added to the fixture
#: without a contract amendment turns this red rather than passing unnoticed.
FIXTURE_FILES = frozenset(
    {
        "state.json",
        "defects.json",
        "escalation.json",
        "stream-rollup.json",
        "verdicts.json",
        "handoffs.jsonl",
        "spawns.log",
        SPEND_LEDGER_FILENAME,
    }
)  # 8 files


@pytest.fixture
def report_env(tmp_path):
    """A writable copy of the finer-boundary fixture. Yields the run dir."""
    run_dir = tmp_path / "foundry-archive" / "finer-boundary-run"
    run_dir.mkdir(parents=True)
    for src in FIXTURE_DIR.iterdir():
        (run_dir / src.name).write_bytes(src.read_bytes())
    return run_dir


#: The name `orchestration/transitions.py` binds the two-layer team check to.
#: A from-import makes the binding the CONSUMER's, so this is the attribute a
#: fixture must patch — not the one on the module that defines it.
_BOUND_TEAM_SCAN = "_active_teams"


@contextlib.contextmanager
def _no_active_teams():
    """Silence the active-team scan for a drive, patching the CONSUMER module.

    THE SPLIT MOVED THE PATCH TARGET, AND A BARE REBIND PATCHES NOTHING.
    ------------------------------------------------------------------
    Before casting 2's split these fixtures did `setattr(fo,
    "_check_active_teams", ...)`, and it worked for every caller because the
    monolith held the definition AND its callers in one namespace. Casting 10's
    group-5 repoint rewrote `fo._check_active_teams` to a bare
    `_check_active_teams` — which rebinds a name in THIS module and reaches no
    production code at all. One site kept a leftover `fo` and raised
    `NameError`; the other two failed SILENTLY, restoring a local they had
    patched nothing with, and passed only because the real scan happens to find
    no team in a `tmp_path` run.

    `orchestration/transitions.py` FROM-IMPORTS the symbol, so patching the
    DEFINING module would not reach it either — the binding a from-import
    makes is the consumer's own. `foundry_mark_phase_complete` is the door all
    three fixtures drive, so `transitions` is the module whose binding has to
    move, and this states that once instead of three times.

    THE SYMBOL WAS RENAMED UNDER THE SAME RULE, ONE WAVE LATER. Casting 10's
    concern C-027 moved the two-layer team check into the leaf as
    `foundry_state.active_teams`, and casting 2's consumer binding is now
    `_active_teams`. `getattr` is not used to paper over that: the name is
    asserted first, so the day the binding moves again this fails LOUDLY here
    instead of restoring a local it patched nothing with — which is the exact
    failure this fixture was written for.
    """
    from foundry_mcp.tools.orchestration import transitions as _transitions

    assert hasattr(_transitions, _BOUND_TEAM_SCAN), (
        f"`transitions.{_BOUND_TEAM_SCAN}` is gone, so this fixture patches "
        f"nothing and every test using it passes only because a tmp_path run "
        f"happens to have no team. Re-point it at the new binding."
    )
    original = getattr(_transitions, _BOUND_TEAM_SCAN)
    setattr(_transitions, _BOUND_TEAM_SCAN, lambda _pr: {
        "active": False, "teams": [], "live_panes": []
    })
    try:
        yield
    finally:
        setattr(_transitions, _BOUND_TEAM_SCAN, original)


def _read_json(run_dir: Path, name: str) -> dict:
    return json.loads((run_dir / name).read_text(encoding="utf-8"))


def _write_json(run_dir: Path, name: str, data: dict) -> None:
    (run_dir / name).write_text(json.dumps(data, indent=2), encoding="utf-8")


def _generate(run_dir: Path) -> dict:
    result = generate_report(run_dir.parent.parent, run_dir)
    assert result["ok"] is True, result
    return result


def _document(run_dir: Path) -> dict:
    return _read_json(run_dir, REPORT_JSON_FILENAME)


def _markdown(run_dir: Path) -> str:
    return (run_dir / REPORT_MD_FILENAME).read_text(encoding="utf-8")


# --------------------------------------------------------------------------- #
# THE FIXTURE GENERATOR (D-130)
#
# The lead ruling recorded as `spec_ambiguities` entry 5 of this run's own
# state.json ends: "The finer_boundary_run fixture is regenerated by driving
# production through these transitions rather than hand-typed." This is the
# driver, and `test_the_fixture_state_is_what_production_writes` below is the
# assertion that the committed copy is still its output.
#
# It is a module-level function rather than a fixture so REGENERATING the
# committed artifact is the same code path the test asserts against:
#
#     from tests.test_report import drive_finer_boundary_run
#     produced = drive_finer_boundary_run(Path(tempfile.mkdtemp()))
#     (FIXTURE_DIR / "state.json").write_text(
#         json.dumps(produced["state.json"], indent=2) + "\n")
#
# A generator that is not the assertion is how the two came apart the first
# time: every number in the committed file was a human's claim about what the
# run would have done, and several were claims production cannot satisfy.
# --------------------------------------------------------------------------- #

#: The one row that is NOT driven, and why. D-007's whole point is that it was
#: written BEFORE the tier axis existed, so it carries neither `tier` nor
#: `reproduction_attempted` — and no door can produce it, because every door
#: now writes both. Driving it would destroy the property it exists to hold.
#: It is appended verbatim from the committed scenario instead.
_PRE_CHANGE_ROW_ID = "D-007"

#: The casting the scenario's fixes are attributed to, and whose prompt hash
#: the teammate lane makes the fix door verify.
_FIXTURE_CASTING_ID = 5


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, text=True, check=True
    ).stdout.strip()


def _scenario_rows() -> list[dict]:
    """The committed rows, which supply the SCENARIO and nothing else.

    Descriptions, classes, tiers, reproduction statements, cycles and the
    fixed/open shape are a human's account of a run that could have happened —
    that is what a fixture is for. Every field a DOOR fills in is stripped
    before the row goes near one, so the RECORD is production's and only the
    STORY is the fixture's. Which fields those are is asserted where they are
    read back, in `test_the_driven_fixture_leaves_the_pre_change_record_alone`.
    """
    return json.loads(
        (FIXTURE_DIR / "defects.json").read_text(encoding="utf-8")
    )["defects"]


def _slug(row: dict) -> str:
    """A defect id as a legal test-name fragment: D-001 -> d_001."""
    return row["id"].lower().replace("-", "_")


class _FrozenClock:
    """A deterministic, monotonic, MICROSECOND-precision clock.

    Production stamps every record with `datetime.now(timezone.utc)`, so a
    driven fixture would carry the wall-clock instant of whoever regenerated
    it — and the committed artifact would differ on every run for reasons that
    say nothing about the code. Freezing the clock rather than excluding the
    timestamps from the comparison keeps them IN the assertion: the fixture is
    reproducible byte-for-byte apart from the git SHAs, and a `decided_at` that
    moved is then a real finding.

    The step is 47 seconds and 137 microseconds — deliberately not a round
    number. D-130's filing named "every fixture timestamp is a round second"
    as evidence the values were typed by a person; production emits
    microseconds, and so does this.
    """

    STEP = timedelta(seconds=47, microseconds=137)

    def __init__(self, start: datetime) -> None:
        self._next = start

    def iso(self) -> str:
        stamp, self._next = self._next, self._next + self.STEP
        return stamp.isoformat()

    def now(self, _tz=None) -> datetime:
        """The `datetime.now(tz)` shape `tools/foundry.py` calls directly."""
        stamp, self._next = self._next, self._next + self.STEP
        return stamp


@contextlib.contextmanager
def _frozen_clock(start: datetime):
    """Drive both timestamp surfaces off one clock, then put them back.

    Two surfaces, because the doors do not share one: the transition doors
    stamp through `foundry_state.now_iso()`, while `tools/foundry.py` calls
    `datetime.now(timezone.utc)` inline. Patching one and not the other is how
    a fixture ends up with a defects ledger and a state file on different days.

    GI-010 / GI-024 — THE TIMESTAMP SURFACE IS `foundry_state.now_iso` NOW.
    This patched `foundry_orchestrator._now`, which was one of two `_now`
    definitions in the package. Casting 10's group 1 hosted the one
    implementation in the leaf; casting 2's group 0 replaces the monolith's
    copy with an import from it. Until that lands, the doors this fixture
    drives still stamp through the monolith's own copy, so the drive produces
    live timestamps and the fixture-equality registers below are RED — named
    in casting 10's completion report, and turned green by casting 2's group 0
    rather than by an edit here.

    The replacement takes `**_` because `now_iso`'s precision is a keyword
    argument (`timespec`), and a stub that refused it would fail at the first
    caller that passes one rather than at the assertion under test.
    """
    from foundry_mcp.tools import foundry as ft
    from foundry_mcp.tools import foundry_state

    clock = _FrozenClock(start)
    original_now, original_datetime = foundry_state.now_iso, ft.datetime
    foundry_state.now_iso = lambda **_: clock.iso()
    ft.datetime = clock
    try:
        yield clock
    finally:
        foundry_state.now_iso, ft.datetime = original_now, original_datetime


def _drive_defect_ledger(tmp_path: Path) -> list[dict]:
    """File and fix the scenario through the real doors; return the ledger."""
    from foundry_mcp.tools import foundry as ft
    # GI-010 / GI-026 — the wave-2 destinations. Casting 2's split defines
    # these symbols in the modules named below and its completion report's
    # `## Symbol map` is the authority; where the map and this differ, the
    # map wins and this is the edit.
    from foundry_mcp.tools.orchestration.fix_gate import (
        foundry_mark_defect_fixed,
    )
    from foundry_mcp.tools import foundry_state

    run_name = "finer-boundary-ledger"
    fdir = tmp_path / "foundry-archive" / run_name
    (fdir / "castings").mkdir(parents=True)
    project_root = str(tmp_path)

    def _counter(cycle: int) -> None:
        # The server counter is what `foundry_add_defect` stamps a record with
        # (`"cycle": _server_cycle(fdir)`), so putting a row in cycle N means
        # standing the run at N and filing — not passing N and hoping.
        (fdir / "state.json").write_text(
            json.dumps({"phase": "F2", "cycle": cycle}), encoding="utf-8"
        )

    # A teammate-authored fix states back the hash of the prompt it was
    # dispatched with, and the fix door verifies it against the file — so the
    # drive needs a real prompt on disk, hashed the way `foundry_spawn.py`
    # publishes it. Only reading the file produces the right answer, which is
    # the point of the check and the reason it is exercised rather than
    # side-stepped with a lead-authored fix.
    prompt_path = fdir / "castings" / f"casting-{_FIXTURE_CASTING_ID}-prompt.md"
    prompt_path.write_text(
        "# Casting 5: the finer-boundary scenario\n", encoding="utf-8"
    )
    prompt_hash = "sha256:" + hashlib.sha256(
        prompt_path.read_bytes()
    ).hexdigest()[:16]

    foundry_state.set_active_run(run_name)
    with contextlib.ExitStack() as stack:
        stack.callback(foundry_state.clear_active_run)
        stack.enter_context(_frozen_clock(_LEDGER_STARTED_AT))
        for row in _scenario_rows():
            if row["id"] == _PRE_CHANGE_ROW_ID:
                continue
            _counter(row["cycle"])
            result = ft.foundry_add_defect(
                cycle=row["cycle"],
                source=row["source"],
                defect_type=row["type"],
                description=row["description"],
                spec_ref=row["spec_ref"],
                symbol=row["symbol"],
                file_path=row["file"],
                defect_class=row["class"],
                project_root=project_root,
                tier=row["tier"],
                reproduction_attempted=row.get("reproduction_attempted") or "",
            )
            assert result.get("defect_id") == row["id"], (row["id"], result)

        # The pre-change record, appended as an older server left it.
        ledger = json.loads((fdir / "defects.json").read_text(encoding="utf-8"))
        pre_change = next(
            r for r in _scenario_rows() if r["id"] == _PRE_CHANGE_ROW_ID
        )
        ledger["defects"].append(dict(pre_change))
        (fdir / "defects.json").write_text(json.dumps(ledger), encoding="utf-8")

        for row in _scenario_rows():
            if row["status"] != "fixed" or row["id"] == _PRE_CHANGE_ROW_ID:
                continue
            _counter(row["fixed_in_cycle"])
            fixed = foundry_mark_defect_fixed(
                defect_id=row["id"],
                cycle=row["fixed_in_cycle"],
                adjacent_path_statement=(
                    f"{row['symbol']} is also reached by the report's own "
                    f"reader for this section and by the boundary transition "
                    f"that records the cycle, and it shares the run directory "
                    f"with the sweep running beside it."
                ),
                adjacent_path_test=(
                    f"tests/test_report.py::test_{_slug(row)}_adjacent_path"
                ),
                project_root=project_root,
                authored_by="teammate",
                regression_test=f"tests/test_report.py::test_{_slug(row)}",
                casting_id=_FIXTURE_CASTING_ID,
                prompt_hash=prompt_hash,
            )
            assert fixed.get("ok") is True, (row["id"], fixed)

        return json.loads(
            (fdir / "defects.json").read_text(encoding="utf-8")
        )["defects"]


def drive_finer_boundary_run(tmp_path: Path) -> dict[str, object]:
    """Replay the whole finer-boundary run. Returns the artifacts it wrote.

    Two roots, because the two artifacts are written by transitions that move
    the counter in opposite directions. The ledger needs the counter STOOD at
    each row's cycle (rows 1..5 then 2 then 1); the INSPECT transitions need it
    ADVANCED monotonically. One run cannot do both, so the ledger is driven
    first, in its own directory, and the transition drive is handed per-cycle
    slices of its result.
    """
    # GI-010 / GI-026 — the wave-2 destinations. Casting 2's split defines
    # these symbols in the modules named below and its completion report's
    # `## Symbol map` is the authority; where the map and this differ, the
    # map wins and this is the edit.
    from foundry_mcp.tools.foundry_state import now_iso
    from foundry_mcp.tools.orchestration.directives import (
        foundry_defects_to_tasks,
    )
    from foundry_mcp.tools.orchestration.streams import foundry_mark_stream
    from foundry_mcp.tools.orchestration.transitions import (
        foundry_mark_phase_complete,
    )
    from foundry_mcp.tools.orchestration.width import INSPECT_BOUNDARY_SHA_MARKER
    from foundry_mcp.tools import foundry_state

    rows = _drive_defect_ledger(tmp_path / "ledger")
    by_id = {r["id"]: r for r in rows}

    root = tmp_path / "run"
    root.mkdir(parents=True)
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "foundry@example.invalid")
    _git(root, "config", "user.name", "foundry")
    # The real repo's own rule: `/foundry-archive/` is git-ignored, so nothing
    # a run writes into its own directory can appear in a GRIND diff. Without
    # it the run's copy of the spec lands in every diff, and the spec is a
    # verifier path — every DELTA cycle would force FULL for a reason that
    # cannot occur in production.
    (root / ".gitignore").write_text("/foundry-archive/\n", encoding="utf-8")
    for rel in (_VERIFIER_FILE, *_DELTA_FILES):
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text("# seed\n", encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "baseline")

    run_name = "finer-boundary-run"
    fdir = root / "foundry-archive" / run_name
    (fdir / "castings").mkdir(parents=True)
    (fdir / "castings" / "manifest.json").write_text(
        json.dumps({"castings": [{"id": 5, "key_files": list(_DELTA_FILES)}]}),
        encoding="utf-8",
    )
    (fdir / "spec.md").write_text(
        "".join(f"- **{rid}**: the thing works\n" for rid in _SPEC_ROW_IDS),
        encoding="utf-8",
    )

    def _ledger(open_ids: tuple[str, ...]) -> None:
        opened = set(open_ids)
        (fdir / "defects.json").write_text(
            json.dumps({"defects": [
                r if r["id"] not in opened
                else {**r, "status": "open", "fixed_in_cycle": None}
                for r in rows
                if r["id"] in opened or r["status"] == "fixed"
            ]}),
            encoding="utf-8",
        )

    def _arm() -> None:
        (fdir / ".next-action-called").write_text(f"{now_iso()}\n", encoding="utf-8")

    def _cross(token: str) -> dict:
        _arm()
        if token == "grind_start":
            # CT-008 / GI-023 — casting 2's split gave `grind_start` a
            # tasks-generated rung: a GRIND may not open until Foundry-Tasks
            # has packeted the open defects. This driver crossed straight from
            # INSPECT to GRIND, which a real run never does, so the door
            # refused with rank _GATE_RANK_MARKER.
            #
            # The CALL is what satisfies it, not the marker. Writing
            # TASKS_GENERATED_MARKER by hand would make this fixture green
            # against a door it never walked, and the fixture's whole contract
            # (D-130) is that it "is regenerated by driving production through
            # these transitions rather than hand-typed".
            foundry_defects_to_tasks(str(root))
        result = foundry_mark_phase_complete(token, str(root))
        assert result.get("ok") is True, (token, result)
        return result

    def _grind(*paths: str) -> None:
        """Pin the boundary at HEAD, then commit the GRIND's work on top."""
        _git(root, "add", "-A")
        _git(root, "commit", "-q", "--allow-empty", "-m", "pre-boundary")
        (fdir / INSPECT_BOUNDARY_SHA_MARKER).write_text(
            _git(root, "rev-parse", "HEAD") + "\n", encoding="utf-8"
        )
        for rel in paths:
            target = root / rel
            target.write_text(
                target.read_text(encoding="utf-8") + "# a GRIND cycle touched this\n",
                encoding="utf-8",
            )
        _git(root, "add", "-A")
        _git(root, "commit", "-qm", "grind")

    def _streams_done(cycle: int) -> None:
        """Every stream the RECORDED roster requires, through the real door."""
        # fallout GI-033: the leaf answers this now. Casting 2 is
        # deleting the width reader this used to call, and the two
        # resolve the same document on the same three axes.
        recorded = foundry_state.current_inspect_mode(
            fdir, modes=vocab.INSPECT_MODES
        ) or {}
        for wire in recorded.get("required_streams", []):
            marked = foundry_mark_stream(
                wire, cycle, items_checked=100, items_total=100,
                findings_count=0, project_root=str(root),
            )
            assert not marked.get("error"), (wire, marked)

    foundry_state.set_active_run(run_name)
    with contextlib.ExitStack() as stack:
        stack.enter_context(_no_active_teams())
        stack.callback(foundry_state.clear_active_run)
        stack.enter_context(_frozen_clock(_RUN_STARTED_AT))
        versions = _executing_version_fields()
        (fdir / "state.json").write_text(
            json.dumps({
                "phase": "F0", "cycle": 1, "max_cycles": 0,
                "temper": True, "nyquist": True,
                "spec_path": _FIXTURE_SPEC_PATH,
                "started_at": _FIXTURE_STARTED_AT,
                **versions,
            }),
            encoding="utf-8",
        )

        # F0 -> F1. `start_cast` opens no INSPECT and records no width, which
        # is exactly why the token that DOES open one is `cast` (OT-012).
        _cross("start_cast")

        # cycle 1 — CAST's F2 entry. FULL by construction: the first INSPECT of
        # a phase has no previous INSPECT to be a delta from.
        _ledger(("D-001", "D-002", "D-003"))
        _cross("cast")

        # cycle 2 — a GRIND that moved vocab.py, which is the machinery that
        # JUDGES the build, so nothing narrower than everything is honest.
        _cross("grind_start")
        _grind(_VERIFIER_FILE)
        _ledger(("D-002", "D-003"))
        _cross("inspect_start")

        # cycles 3 and 4 — ordinary GRINDs. Both touch TEST files, which are
        # deliberately outside the verifier set: they are the pins, not the
        # judgement, and the TEST stream re-runs them at every width.
        #
        # THIS COMMENT USED TO SAY "`is_verifier_path` matches every `.py`
        # under `foundry_mcp/`", which was D-033's package rule. FR-003 / AC-012
        # narrowed the rule to the four deciders, the evidence sweep,
        # `schemas/`, `vocab.py` and the verifying streams' contract prose, so
        # a DELTA cycle in this repo is no longer only "tests, docs or
        # evidence" — a GRIND confined to `display.py`, the report seal or the
        # lead's protocol earns one too. The fixture's own delta files are
        # tests either way, so the drive is unchanged; the sentence describing
        # WHY is not, and prose that contradicts the rule is worse than none.
        for delta_file, still_open in zip(_DELTA_FILES, (("D-003",), ("D-004",))):
            _cross("grind_start")
            _grind(delta_file)
            _ledger(still_open)
            _cross("inspect_start")

        # cycle 5 — the F2->F2 widening re-open. The DELTA cycle came back
        # clean, which does not open ASSAY; it earns the right to re-open
        # INSPECT at full width, and THAT crossing is the final gate.
        _cross("inspect_start")

        # ...and out through ASSAY, TEMPER and NYQUIST. The temper crossing is
        # the second INSPECT-opening transition of cycle 5: the counter does
        # not advance entering F5 (D-119).
        _ledger(("D-004", "D-005"))
        _streams_done(5)
        (fdir / "verdicts.json").write_text(
            json.dumps({"cycle": 5, "requirements": []}), encoding="utf-8"
        )
        _cross("inspect_clean")
        _cross("temper")
        _streams_done(5)
        _cross("nyquist")

        state = json.loads((fdir / "state.json").read_text(encoding="utf-8"))

    # The spend roll-up is the ONE state field this drive cannot produce: it is
    # written by `Foundry-Spend`, which no transition calls, and the committed
    # value is a deliberate D-090 scenario (a roll-up that DISAGREES with
    # spend.jsonl, so the report has a disagreement to name). Carried over
    # rather than driven, and named here so it is not mistaken for an omission.
    state["spend"] = json.loads(
        (FIXTURE_DIR / "state.json").read_text(encoding="utf-8")
    )["spend"]
    return {"defects.json": {"defects": rows}, "state.json": state}


#: The GRIND-diff files the drive touches. `_VERIFIER_FILE` matches
#: `is_verifier_path`; the two `_DELTA_FILES` do not, and are this casting's
#: own key_files so the DELTA scope selects them.
_VERIFIER_FILE = "plugins/foundry/mcp-server/src/foundry_mcp/schemas/vocab.py"
_DELTA_FILES = (
    "plugins/foundry/mcp-server/tests/test_report.py",
    "plugins/foundry/mcp-server/tests/test_evidence.py",
)

#: The run's own spec rows, so a DELTA cycle's `prove_sample` is drawn from a
#: real population rather than coming back empty.
_SPEC_ROW_IDS = (
    "GI-002", "GI-006", "ST-005", "CT-007", "CT-013", "CT-014", "AC-002",
    "AC-004", "AC-013", "AC-014", "AC-022", "AC-034", "AC-036", "FR-009",
    "FR-023", "FR-031", "FR-038", "FR-042", "FR-051", "NFR-002", "NFR-003",
    "NFR-004", "OT-008", "OT-016", "OT-025",
)

#: The spec path a real run records — the run's own copy, under its archive.
_FIXTURE_SPEC_PATH = "foundry-archive/finer-boundary-run/spec.md"

#: The narrative instants the two frozen clocks start from. The ledger is
#: filed and fixed across the run's five cycles, so it starts first; the
#: transitions are stamped from the moment CAST opened. Both are on the same
#: day as the fixture's other ledgers, which is what keeps one synthetic
#: archive readable as one run.
#: Microseconds on the SEEDS too, not just on what the clock derives from
#: them: a round second anywhere in the artifact is the tell D-130 named.
_LEDGER_STARTED_AT = datetime(2026, 9, 2, 4, 0, 0, 318_204, timezone.utc)
_RUN_STARTED_AT = datetime(2026, 9, 2, 4, 30, 0, 662_915, timezone.utc)
_FIXTURE_STARTED_AT = _RUN_STARTED_AT.isoformat()


def _executing_version_fields() -> dict[str, str | bool]:
    """The four CT-010 version fields, as `foundry_init` computes them.

    `server_version` and `plugin_version` are DIFFERENT numbers from different
    files — `foundry_mcp.__version__` and the plugin manifest's `version` — and
    the committed fixture used to carry the plugin's in both slots, claiming a
    server build that has never existed (D-130).

    `server_root` and `server_commit` are deliberately NOT driven. Production
    writes an absolute path on the executing machine and that machine's git
    HEAD; a committed fixture cannot carry either without being wrong for
    everyone else, so it carries a visibly synthetic pair and the assertion
    below excludes them.
    """
    from foundry_mcp.tools.foundry import (
        _executing_server_root,
        _executing_server_version,
        _plugin_manifest_version,
    )

    return {
        # The two numbers, from the two files production reads them from —
        # never one number in both slots.
        "server_version": _executing_server_version(),
        "plugin_version": _plugin_manifest_version(_executing_server_root()),
        "server_root": "/repo/plugins/foundry",
        "server_commit": "3f9c1a284d6b7e05aa1177c0d4e93b62f8a5c410",
        "self_target": True,
    }


# --------------------------------------------------------------------------- #
# The fixture contract itself. Casting 3 reads this directory, so a change to
# its shape has to break a test in a file somebody owns.
# --------------------------------------------------------------------------- #


def test_the_finer_boundary_fixture_is_the_eight_frozen_artifacts():
    """AC-002's fixture clause: the synthetic archive is consumable both by
    casting 3's escalation tests and by this casting's report tests, so its
    file list is frozen by the prompt's Fixture contract."""
    assert FIXTURE_DIR.is_dir(), FIXTURE_DIR
    on_disk = {p.name for p in FIXTURE_DIR.iterdir() if p.is_file()}
    assert on_disk == set(FIXTURE_FILES), sorted(on_disk ^ set(FIXTURE_FILES))


def test_the_fixture_encodes_the_finer_boundary_scenario():
    """AC-002 verbatim: 'On a synthetic fixture where each cycle's PROVE files
    one LATENT instance of the escalated class at a finer boundary, the class
    is CLEARED after the second structural packet closes, the run reaches
    NYQUIST, and the report names the LATENT instances left.'

    The ids and cycles are the prompt's Fixture contract table, asserted here
    rather than trusted, because casting 3's budget-exit tests key on them."""
    defects = _read_json(FIXTURE_DIR, "defects.json")["defects"]
    by_id = {d["id"]: d for d in defects}
    assert sorted(by_id) == [f"D-00{n}" for n in range(1, 8)]

    klass = "FALSE_DOCUMENTED_CONTRACT"
    # Three consecutive LIVE cycles, then two LATENT instances at a finer
    # boundary and NO further LIVE instance of the class.
    for did, cycle in (("D-001", 1), ("D-002", 2), ("D-003", 3)):
        assert by_id[did]["tier"] == "LIVE"
        assert by_id[did]["class"] == klass
        assert by_id[did]["cycle"] == cycle
        assert by_id[did]["status"] == "fixed"
    for did, cycle in (("D-004", 4), ("D-005", 5)):
        assert by_id[did]["tier"] == "LATENT"
        assert by_id[did]["class"] == klass
        assert by_id[did]["cycle"] == cycle
        assert by_id[did]["status"] == "open"
        assert by_id[did]["reproduction_attempted"]

    # D-007 is the pre-change record: NEITHER key, which is the whole point.
    assert "tier" not in by_id["D-007"]
    assert "reproduction_attempted" not in by_id["D-007"]
    assert by_id["D-007"]["status"] == "fixed", (
        "D-007 is fixed on purpose so the fixture still reaches NYQUIST: an "
        "OPEN unknown-tier defect blocks like LIVE"
    )

    # The run reached F5.5 (NYQUIST) at cycle 5.
    state = _read_json(FIXTURE_DIR, "state.json")
    assert state["phase"] == "F5.5"
    assert state["cycle"] == 5


_ISO_STAMP_RE = re.compile(r"\A\d{4}-\d{2}-\d{2}T[\d:]+(?:\.\d+)?\+00:00\Z")
_DURATION_RE = re.compile(r"\A\d+m \d+s\Z")


#: The two release numbers a checkout supplies, blanked by `_without_volatile`
#: for the reason its docstring gives. `server_commit` is deliberately NOT
#: here: it is already a git SHA and already blanked, and `self_target` is a
#: recorded answer about the run rather than about the release.
_RELEASE_FIELDS = frozenset({"server_version", "plugin_version"})  # 2 fields


def _without_volatile(value):
    """Blank the values a committed artifact cannot pin, at any depth.

    THREE KINDS, AND WHY EACH IS OUT OF THE EQUALITY:

      git SHAs (`diff_base`) — the boundary commit of a throwaway repository.
        No committed value can equal the next machine's.

      ISO timestamps — deterministic per checkout, because `_frozen_clock`
        makes them so, but their exact offsets are a function of HOW MANY
        `_now()` calls the transition path makes. That count is a private
        implementation detail of code other castings own: driven here, the
        working tree and a detached worktree at HEAD produced timelines two
        ticks apart purely because a terminal-sweep memo behaved differently
        between them. Pinning the offsets would make this fixture demand
        regeneration on changes that say nothing about its shape.

      `phase_times` durations — rendered FROM two timestamps, so pinning them
        pins the timestamps by another name.

      the RELEASE numbers (`server_version`, `plugin_version`) — read from
        `foundry_mcp.__version__` and the plugin manifest by whatever checkout
        is executing. They are the same kind of fact as the git SHA above: a
        property of the tree the drive ran in, not of the scenario the fixture
        freezes. Driven: casting 9's release commit moved plugin 4.10.0 ->
        4.11.0 and server 1.9.0 -> 1.10.0, and this equality went red on a
        change that says nothing about a single INSPECT decision, defect row or
        cycle — which is the same "demand regeneration on changes that say
        nothing about its shape" the timestamp clause above refuses. The
        fields are not unpinned: `test_executing_versions_names_the_server_that
        _ran` asserts the report publishes exactly what `state.json` recorded,
        which is the question they actually answer.

    What the timestamps are still held to is their SHAPE, asserted separately:
    every one carries microseconds, which is the tell D-130 was filed on.
    """
    if isinstance(value, dict):
        return {
            k: (
                "<SHA>" if k == "diff_base"
                else "<RELEASE>" if k in _RELEASE_FIELDS
                else _without_volatile(v)
            )
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [_without_volatile(v) for v in value]
    if isinstance(value, str):
        if _ISO_STAMP_RE.match(value):
            return "<TIMESTAMP>"
        if _DURATION_RE.match(value):
            return "<DURATION>"
    return value


def test_the_fixture_state_is_what_production_writes(tmp_path):
    """AC-036 verbatim: '... the FULL or DELTA decision per cycle ...'; GI-009:
    'whichever Foundry-Phase transition opens an INSPECT records the mode'.

    D-130 — THE FIXTURE WAS TYPED AGAINST A RULING THAT IT BE DRIVEN.
    ----------------------------------------------------------------
    The GRIND cycle-3 lead ruling (this run's `state.json.spec_ambiguities`
    entry 5) ends "The finer_boundary_run fixture is regenerated by driving
    production through these transitions rather than hand-typed", and only
    `escalation.json` ever was. `state.json` stayed a person's account of a run,
    and it recorded states no code path can produce:

      * cycle 5 read `decided_by: temper` with `rule: final_gate`, and the
        temper arm of `_decide_inspect_mode` records `first_of_phase`
        unconditionally — a phase entry has no previous INSPECT of that phase
        to be a delta from, so its rule is a property of the position;
      * `server_version` read 4.10.0, which is the PLUGIN version;
        `_executing_server_version()` reads `foundry_mcp.__version__`, a
        different number from a different file;
      * no entry carried `rule_detail` or `diff_base`, which every entry
        `_decide_inspect_mode` returns has carried since D-069;
      * no defect row carried `created_at`, `declared_cycle`, `authored_by`,
        `regression_test` or `fix_commit`, which both doors write;
      * every timestamp was a round second, and production emits microseconds.

    So the artifact is now this drive's OUTPUT, and this is the assertion that
    it still is. The scenario — ids, cycles, tiers, classes, descriptions, the
    fixed/open shape — remains the fixture's; every RECORD is production's.
    """
    produced = drive_finer_boundary_run(tmp_path)

    committed = _read_json(FIXTURE_DIR, "state.json")
    assert _without_volatile(committed["inspect_modes"]) == _without_volatile(
        produced["state.json"]["inspect_modes"]
    ), "the committed inspect_modes are not what these transitions write"
    assert _without_volatile(
        {k: v for k, v in committed.items() if k != "inspect_modes"}
    ) == _without_volatile(
        {k: v for k, v in produced["state.json"].items() if k != "inspect_modes"}
    ), "the committed state.json is not what this run writes"
    # ...with ONE field carried rather than driven, named here so the equality
    # above is not misread as covering it: `spend` is written by Foundry-Spend,
    # which no transition calls, and the committed value is a deliberate D-090
    # scenario (a roll-up that disagrees with spend.jsonl, so the report has a
    # disagreement to name). `drive_finer_boundary_run` copies it in.
    assert produced["state.json"]["spend"] == committed["spend"]

    assert _without_volatile(
        _read_json(FIXTURE_DIR, "defects.json")["defects"]
    ) == _without_volatile(
        produced["defects.json"]["defects"]
    ), "the committed defect rows are not what the doors write"

    # And the specific claims the filing made, stated so a regression names
    # itself rather than showing up as one large dict inequality.
    for entry in committed["inspect_modes"]:
        assert "rule_detail" in entry and "diff_base" in entry, entry
        if entry["decided_by"] in ("cast", "temper"):
            assert entry["rule"] == "first_of_phase", (
                "a phase entry's rule is its POSITION, not a policy: "
                f"{entry['decided_by']} recorded {entry['rule']!r}"
            )
    assert [e["decided_by"] for e in committed["inspect_modes"]] == [
        "cast", "inspect_start", "inspect_start", "inspect_start",
        "inspect_start", "temper",
    ]

    from foundry_mcp.tools.foundry import _executing_server_version

    # D-130's claim is that these are TWO numbers from TWO files — the fixture
    # used to carry the plugin's version in both slots. That is asserted on the
    # committed pair, below, and on what the DRIVE writes, here.
    #
    # What is deliberately NOT asserted is `committed["server_version"] ==
    # _executing_server_version()`. That pinned the committed artifact to
    # whichever release was checked out, so casting 9's bump to server 1.10.0
    # turned it red on a change that moved no INSPECT decision, defect row or
    # cycle — the same "demand regeneration on changes that say nothing about
    # its shape" `_without_volatile` refuses for timestamps and SHAs. The
    # PRODUCED value is the one that must be current, because that one is a
    # statement about the drive rather than about the commit it was captured at.
    assert produced["state.json"]["server_version"] == _executing_server_version()
    assert committed["plugin_version"] != committed["server_version"], (
        "two numbers, from two files; the fixture used to carry the plugin's "
        "in both slots"
    )
    assert (
        produced["state.json"]["plugin_version"]
        != produced["state.json"]["server_version"]
    ), "the drive must write two numbers too, not one twice"

    # Not one round second anywhere in the artifact. This is the half of the
    # timestamp claim the equality above deliberately drops: the OFFSETS are
    # volatile, the SHAPE is not, and a round second is the tell D-130 was
    # filed on ("_now() emits microseconds").
    stamps = re.findall(r"\d{4}-\d{2}-\d{2}T[\d:]+(?:\.\d+)?\+00:00",
                        json.dumps(committed) + json.dumps(
                            _read_json(FIXTURE_DIR, "defects.json")))
    assert len(stamps) > 20, f"only {len(stamps)} timestamps; the search is wrong"
    assert all("." in s for s in stamps), [s for s in stamps if "." not in s]


def test_the_driven_fixture_leaves_the_pre_change_record_alone(tmp_path):
    """FR-051 verbatim: 'Blocks like LIVE until a stream re-files it with a
    tier' — which needs a record that HAS no tier.

    D-007 is the only row the drive does not file, and the reason is the point
    of the row: it stands for a record written before the tier axis existed,
    and every door now writes `tier` and `reproduction_attempted`. Putting it
    through a door would destroy the property the fixture keeps it for. So the
    ledger is production's for six rows and an older server's for one, and this
    states which is which.
    """
    rows = {r["id"]: r for r in drive_finer_boundary_run(tmp_path)["defects.json"]["defects"]}

    assert "tier" not in rows[_PRE_CHANGE_ROW_ID]
    assert "reproduction_attempted" not in rows[_PRE_CHANGE_ROW_ID]
    assert "created_at" not in rows[_PRE_CHANGE_ROW_ID], (
        "a door stamps created_at; this row predates the doors"
    )

    driven = [r for did, r in rows.items() if did != _PRE_CHANGE_ROW_ID]
    assert driven, rows
    for row in driven:
        # Every field the filing said production writes and the fixture lacked.
        for field in ("created_at", "declared_cycle", "regression_test",
                      "authored_by", "fix_commit"):
            assert field in row, (row["id"], field)
        assert row["tier"] in {"LIVE", "LATENT"}, row


def test_the_fixture_escalation_record_is_what_production_writes(tmp_path):
    """AC-004 verbatim: 'escalation.json records for the class a status, the
    exit reason (clean-cycles or budget), the cycle it cleared and the
    structural packets it consumed.'

    D-060 — THE FIXTURE IS DRIVEN, NOT TYPED.
    -----------------------------------------
    This test read the committed JSON off disk and asserted its fields while
    calling ZERO production functions, so every number in it was a human's
    claim about what the run would have done. They were not what production
    does: the file recorded `cleared_at_cycle: 4` and `live_clean_cycles: 1`,
    and the key assertion — `entry["live_clean_cycles"] <
    LIVE_CLEAN_CYCLES_TO_CLEAR` — checked a number a person had typed, and one
    production cannot produce (the clean arm skips while `completed_cycle <=
    escalated_at`, so the first countable crossing closes cycle 4 and the class
    clears on the budget arm at that same boundary with the counter still 0).
    A fixture asserting a state no code path can reach proves nothing about the
    code, and casting 3's escalation tests key on this directory.

    So the scenario is REPLAYED through the real doors here — Foundry-Tasks for
    each structural packet, `Foundry-Phase(inspect_start)` for each boundary —
    and the committed record must equal what those calls wrote. The defect ids
    and cycles stay as the Fixture contract freezes them; the numbers are
    whatever production produces. Only the two timestamps are pinned in the
    committed copy, so the artifact is reproducible.
    """
    from foundry_mcp.schemas.vocab import (
        LIVE_CLEAN_CYCLES_TO_CLEAR,
        STRUCTURAL_PASS_BUDGET,
    )
    # GI-010 / GI-026 — the wave-2 destinations. Casting 2's split defines
    # these symbols in the modules named below and its completion report's
    # `## Symbol map` is the authority; where the map and this differ, the
    # map wins and this is the edit.
    from foundry_mcp.tools.foundry_state import now_iso
    from foundry_mcp.tools.orchestration.directives import (
        foundry_defects_to_tasks,
    )
    from foundry_mcp.tools.orchestration.transitions import (
        foundry_mark_phase_complete,
    )
    from foundry_mcp.tools import foundry_state

    klass = "FALSE_DOCUMENTED_CONTRACT"
    by_id = {d["id"]: d for d in _read_json(FIXTURE_DIR, "defects.json")["defects"]}

    def _open(did: str) -> dict:
        return {**by_id[did], "status": "open", "fixed_in_cycle": None}

    run_dir = tmp_path / "foundry-archive" / "finer-boundary-run"
    (run_dir / "castings").mkdir(parents=True)
    project_root = str(tmp_path)

    def _ledger(ids_open: list[str], ids_fixed: list[str]) -> None:
        rows = [_open(d) for d in ids_open] + [by_id[d] for d in ids_fixed]
        (run_dir / "defects.json").write_text(
            json.dumps({"defects": rows}), encoding="utf-8"
        )

    def _state(phase: str, cycle: int) -> None:
        (run_dir / "state.json").write_text(
            json.dumps({"phase": phase, "cycle": cycle}), encoding="utf-8"
        )

    def _cross() -> dict:
        (run_dir / ".next-action-called").write_text(
            f"{now_iso()}\n", encoding="utf-8"
        )
        return foundry_mark_phase_complete("inspect_start", project_root)

    foundry_state.set_active_run("finer-boundary-run")
    with _no_active_teams():
        # Cycle 3: the third consecutive LIVE filing escalates the class, and
        # Foundry-Tasks dispatches structural packet 1.
        _ledger(["D-001", "D-002", "D-003"], ["D-006", "D-007"])
        _state("F2", 3)
        assert foundry_defects_to_tasks(project_root)["structural_tasks"] == 1

        # The GRIND closes two instances; the boundary closes cycle 3.
        _ledger(["D-003"], ["D-001", "D-002", "D-006", "D-007"])
        _state("F3", 3)
        assert _cross()["cycle"] == 4

        # Cycle 4: PROVE files one LATENT instance at a finer boundary, and
        # Foundry-Tasks dispatches packet 2 — the last the budget allows.
        _ledger(["D-003", "D-004"], ["D-001", "D-002", "D-006", "D-007"])
        assert foundry_defects_to_tasks(project_root)["structural_tasks"] == 1

        # The boundary that closes cycle 4 CLOSES that packet, and the budget
        # arm applies the exit there (ST-002: "second structural packet
        # CLOSED").
        _ledger(["D-004"], ["D-001", "D-002", "D-003", "D-006", "D-007"])
        _state("F3", 4)
        clearing = _cross()
        assert clearing["cycle"] == 5
        assert clearing["escalation_cleared"][0]["exit_reason"] == "budget"

        # Cycle 5: the second LATENT instance, at a finer boundary still.
        (run_dir / "defects.json").write_text(
            json.dumps({"defects": list(by_id.values())}), encoding="utf-8"
        )
        assert foundry_defects_to_tasks(project_root)["structural_tasks"] == 0

        produced = _read_json(run_dir, "escalation.json")["classes"][klass]
        foundry_state.clear_active_run()

    committed = _read_json(FIXTURE_DIR, "escalation.json")["classes"][klass]

    volatile = {"recorded_at"}
    assert {k: v for k, v in committed.items() if k not in volatile} == {
        k: v for k, v in produced.items() if k not in volatile
    }, "the committed fixture is not what these transitions write"

    # And the claim the fixture is FOR: it cleared on the budget arm, with the
    # clean arm demonstrably short — a fixture where both fired would prove
    # nothing about which one did.
    assert committed["status"] == "CLEARED"
    assert committed["exit_reason"] == "budget"
    assert committed["structural_packets_dispatched"] == STRUCTURAL_PASS_BUDGET
    assert committed["structural_packet_cycles"] == [3, 4]
    assert committed["cleared_at_cycle"] == 5, (
        "the counter AFTER the boundary that applied the exit"
    )
    assert committed["live_clean_cycles"] < LIVE_CLEAN_CYCLES_TO_CLEAR
    assert committed["open_latent_defect_ids"] == ["D-004", "D-005"]


# --------------------------------------------------------------------------- #
# GI-006 / CT-014 — the sections, in both documents.
# --------------------------------------------------------------------------- #


def test_report_json_top_level_keys_are_exactly_the_required_sections(report_env):
    """CT-014 / GI-006: `report.json`'s top-level keys are exactly
    REPORT_REQUIRED_SECTIONS plus `generated_at` and `run`.

    Asserted as set EQUALITY, not containment. A twelfth key would pass a
    containment check and then be a section `artifacts.report_document_status` does not know
    about, and a missing one would be a section the DONE gate refuses on."""
    _generate(report_env)
    doc = _document(report_env)
    assert set(doc) == set(REPORT_REQUIRED_SECTIONS) | {"generated_at", "run"}
    # And in DECLARED order, because REPORT.md renders from the same tuple and
    # a reader diffing the two documents reads them side by side.
    assert list(doc)[2:] == list(REPORT_REQUIRED_SECTIONS)


def test_report_md_carries_one_heading_per_section_in_the_same_order(report_env):
    """CT-014: 'REPORT.md carries one `## ` heading per section, in the same
    order.' The count is asserted too — a duplicated heading would keep the
    order assertion green while rendering a section twice."""
    _generate(report_env)
    headings = re.findall(r"^## (.+)$", _markdown(report_env), re.M)
    assert len(headings) == len(REPORT_REQUIRED_SECTIONS)
    assert headings == [vocab.REPORT_SECTION_TITLES[k] for k in REPORT_REQUIRED_SECTIONS]


def test_every_named_section_carries_content_from_its_own_ledger(report_env):
    """FR-023 verbatim: 'Sections derived from run artifacts: verdict matrix,
    defects by tier/status, LATENT backlog, escalated classes with exit reason,
    lead_fix records, full-vs-delta decisions per cycle, per-phase/per-cycle
    tokens and minutes, executing server/plugin version and commit.'

    One assertion per section, each on a value that could ONLY have come from
    the ledger the contract names it against — so a section rendered from the
    wrong artifact, or from nothing, is caught rather than counted."""
    _generate(report_env)
    doc = _document(report_env)

    assert doc["verdict_matrix"]["count"] == 6                      # verdicts.json
    assert doc["defects_by_tier_and_status"]["total"] == 7          # defects.json
    assert doc["latent_backlog"]["open_count"] == 2                 # defects.json
    # D-166: the section lists OPEN untiered records, and the fixture's one
    # untiered record (D-007) is fixed, so the ledger it read is proved by the
    # CLOSED count rather than the open one.
    assert doc["unknown_tier_defects"]["count"] == 0                # defects.json
    assert doc["unknown_tier_defects"]["closed_count"] == 1         # defects.json
    assert doc["escalated_classes"]["count"] == 1                   # escalation.json
    assert doc["lead_fix_records"]["count"] == 2                    # handoffs.jsonl
    assert doc["inspect_modes_per_cycle"]["count"] == 6             # state.json
    assert doc["spend_per_phase_and_cycle"]["records"] == 5         # spend.jsonl
    assert doc["unreported_dispatches"]["count"] > 0                # spawns + spend
    assert doc["executing_versions"]["server_commit"].startswith("3f9c1a")
    assert doc["baseline_comparison"]["baseline"]["run"] == "thunder-viper"


def test_the_json_carries_the_same_data_the_markdown_renders(report_env):
    """FR-038: 'the markdown layout and the report.json key names below the top
    level are implementer's choice, provided every named section is present and
    the JSON carries the same data as the markdown.'

    Driven on values a reader would actually cross-check between the two
    documents: every defect id the JSON names must appear in the markdown, and
    every escalated class name likewise."""
    _generate(report_env)
    doc = _document(report_env)
    md = _markdown(report_env)

    for defect in doc["latent_backlog"]["defects"]:
        assert defect["id"] in md, defect["id"]
    for defect in doc["unknown_tier_defects"]["defects"]:
        assert defect["id"] in md, defect["id"]
    for record in doc["lead_fix_records"]["records"]:
        assert record["defect_id"] in md, record["defect_id"]
        assert record["fix_commit"] in md, record["fix_commit"]
    for entry in doc["escalated_classes"]["classes"]:
        assert entry["class"] in md
        assert entry["exit_reason"] in md
    for row in doc["verdict_matrix"]["requirements"]:
        assert row["id"] in md, row["id"]


# --------------------------------------------------------------------------- #
# The individual section contracts.
# --------------------------------------------------------------------------- #


def test_the_latent_backlog_names_every_open_latent_instance(report_env):
    """NFR-003 verbatim: 'LATENT stays open, tracked, and listed in the
    report.' AC-002's closing clause: 'the report names the LATENT instances
    left.'

    Names, not counts: the reproduction_attempted statement travels with each
    row, because that statement is the only record of what the filing stream
    actually drove and is what a later cycle needs in order to re-file the
    defect as LIVE."""
    _generate(report_env)
    backlog = _document(report_env)["latent_backlog"]
    assert [d["id"] for d in backlog["defects"]] == ["D-004", "D-005"]
    for row in backlog["defects"]:
        assert row["class"] == "FALSE_DOCUMENTED_CONTRACT"
        assert row["reproduction_attempted"], row
        assert row["description"]
    # And they are named in the markdown an operator actually reads.
    md = _markdown(report_env)
    assert "D-004" in md and "D-005" in md


def test_unknown_tier_defects_are_listed_separately_from_live_and_latent(report_env):
    """FR-051 verbatim: 'Blocks like LIVE until a stream re-files it with a
    tier' — with the gloss 'the report lists unknown-tier defects separately'.

    The separation is the requirement. Folding an untiered record in with the
    LIVE rows would tell an operator a stream classified it, which is the one
    thing that did not happen; the cross-tab therefore keeps a distinct row
    keyed on TIER_UNKNOWN and the dedicated section names the record.

    D-166: the SECTION lists the open untiered records, so D-007 — fixed in
    the frozen fixture — is reopened on this copy to put it there. The
    CROSS-TAB is unfiltered by design and keeps it either way: that table is a
    census of the whole ledger and its whole job is to show which tier each
    record landed under."""
    defects = _read_json(report_env, "defects.json")
    for record in defects["defects"]:
        if record["id"] == "D-007":
            record["status"] = "open"
            record["fixed_in_cycle"] = None
    _write_json(report_env, "defects.json", defects)
    _generate(report_env)
    doc = _document(report_env)

    section = doc["unknown_tier_defects"]
    assert [d["id"] for d in section["defects"]] == ["D-007"]

    cross = doc["defects_by_tier_and_status"]["cross_tab"]
    assert set(cross) == set(DEFECT_TIER_OR_UNKNOWN)
    assert TIER_UNKNOWN in cross
    unknown_ids = {
        did for bucket in cross[TIER_UNKNOWN].values() for did in bucket["ids"]
    }
    assert unknown_ids == {"D-007"}
    # And emphatically NOT among the LIVE ids.
    live_ids = {did for bucket in cross["LIVE"].values() for did in bucket["ids"]}
    assert "D-007" not in live_ids


def test_the_unknown_tier_section_carries_the_fields_the_retier_matches_on(
    report_env,
):
    """AC-008 / FR-051, and the DONE refusal's own hint, verbatim: 'either door
    matches the open untiered record on (source, type, file, symbol) and
    re-tiers it IN PLACE'.

    D-120: this is the one section that BLOCKS — an untiered record holds the
    DONE gate shut exactly as a LIVE one does — and it was the one section that
    could not be acted on. It rendered id, class, status, cycle and description
    in both documents: not one of the four fields the remedy matches on. The
    adjacent LATENT backlog has carried its location since D-029 for a weaker
    reason (it blocks nothing), and the fix was never carried across."""
    defects = _read_json(report_env, "defects.json")
    for record in defects["defects"]:
        if record["id"] == "D-007":
            record["file"] = "src/deep/nested/legacy.py"
            record["symbol"] = "legacy_handler"
            record["source"] = "trace"
            record["type"] = "UNWIRED"
            # D-166: the remedy is for an OPEN record, so the record this
            # drives the remedy's fields through has to be one.
            record["status"] = "open"
            record["fixed_in_cycle"] = None
    _write_json(report_env, "defects.json", defects)
    _generate(report_env)

    row = _document(report_env)["unknown_tier_defects"]["defects"][0]
    assert row["id"] == "D-007"
    assert row["source"] == "trace"
    assert row["type"] == "UNWIRED"
    assert row["file"] == "src/deep/nested/legacy.py"
    assert row["symbol"] == "legacy_handler"

    # And in the document a lead actually reads, with the match named.
    section = _markdown(report_env).split("## Unknown-tier defects", 1)[1]
    section = section.split("\n## ", 1)[0]
    for cell in ("trace", "UNWIRED", "src/deep/nested/legacy.py", "legacy_handler"):
        assert cell in section, cell
    assert "(source, type, file, symbol)" in section


def test_an_untiered_record_with_no_location_says_so_in_the_blocking_section(
    report_env,
):
    """D-120 with D-103's rule: the blocking section renders a record that
    carries no location as one, rather than printing two blank cells beside the
    fields the re-tier matches on. An empty `file` is what the door itself
    matches (`(d.get("file") or "")`), so the row has to say the filing carried
    none rather than leave a reader guessing which."""
    defects = _read_json(report_env, "defects.json")
    for record in defects["defects"]:
        if record["id"] == "D-007":
            record["file"] = ""
            record["symbol"] = None
            # D-166: the blocking section lists open records, so this is one.
            record["status"] = "open"
            record["fixed_in_cycle"] = None
    _write_json(report_env, "defects.json", defects)
    _generate(report_env)

    row = _document(report_env)["unknown_tier_defects"]["defects"][0]
    assert row["located"] is False
    assert row["location_note"] == "the filing carried no file and no symbol"
    section = _markdown(report_env).split("## Unknown-tier defects", 1)[1]
    line = next(
        line for line in section.splitlines() if line.startswith("| D-007 |")
    )
    assert line.count(fr.NO_LOCATION_CELL) == 2, line


def test_the_cross_tab_carries_every_tier_including_the_empty_ones(report_env):
    """A tier with no defects is a MEASUREMENT, and omitting its key would make
    'zero LATENT defects in this run' indistinguishable from 'nobody measured'.
    Driven by emptying the ledger so every tier is zero at once."""
    _write_json(report_env, "defects.json", {"defects": []})
    _generate(report_env)
    by_tier = _document(report_env)["defects_by_tier_and_status"]["by_tier"]
    assert set(by_tier) == set(DEFECT_TIER_OR_UNKNOWN)
    assert set(by_tier.values()) == {0}


def test_escalated_classes_carries_status_exit_reason_cycle_and_packets(report_env):
    """AC-004 verbatim: 'escalation.json records for the class a status, the
    exit reason (clean-cycles or budget), the cycle it cleared and the
    structural packets it consumed.' All four reach the report."""
    _generate(report_env)
    entry = _document(report_env)["escalated_classes"]["classes"][0]
    assert entry["class"] == "FALSE_DOCUMENTED_CONTRACT"
    assert entry["status"] == "CLEARED"
    assert entry["exit_reason"] == "budget"
    assert entry["cleared_at_cycle"] == 5  # D-060: the counter AFTER the clearing boundary
    assert entry["structural_packets_dispatched"] == 2
    assert entry["open_latent_defect_ids"] == ["D-004", "D-005"]


def test_a_class_written_before_the_status_field_reads_as_escalated(report_env):
    """A pre-change escalation entry has no `status`. It is reported in the
    state it was WRITTEN in — ESCALATED — because defaulting it to CLEARED
    would silently retire a class nobody ever cleared."""
    data = _read_json(report_env, "escalation.json")
    del data["classes"]["FALSE_DOCUMENTED_CONTRACT"]["status"]
    _write_json(report_env, "escalation.json", data)
    _generate(report_env)
    section = _document(report_env)["escalated_classes"]
    assert section["classes"][0]["status"] == "ESCALATED"
    assert section["by_status"]["ESCALATED"] == 1


def test_lead_fix_records_lists_every_lead_fix_handoff(report_env):
    """AC-022 verbatim: 'A successful lead fix causes the server to append a
    lead_fix record to handoffs.jsonl carrying the defect id, tier, file, line
    count and test, and the generated report lists it.'

    Both records carry the FULL field list, LATENT included.

    D-046 — MEASURING AND RECORDING ARE DIFFERENT THINGS. This used to pin
    `latent["file"] is None and latent["line_count"] is None`, on the reading
    that a LATENT lead fix is "recorded unmeasured". GI-003's field list —
    "the defect id, tier, file, line count and test" — carries no tier
    carve-out, and AC-022 and OT-010 repeat it unchanged. What IS LIVE-only is
    the lane ELIGIBILITY test (FR-046 / CT-006 / ST-004), which is a limit on
    the numbers, not a reason to stop reading them. Conflating the two rendered
    a blank file and line column for every LATENT lead fix, so a report reader
    could not tell a deliberately unmeasured fix from a missing measurement."""
    from foundry_mcp.schemas.vocab import LEAD_LANE_MAX_LINES

    _generate(report_env)
    section = _document(report_env)["lead_fix_records"]
    assert section["count"] == 2
    by_defect = {r["defect_id"]: r for r in section["records"]}
    assert set(by_defect) == {"D-002", "D-003"}

    live = by_defect["D-002"]
    assert live["tier"] == "LIVE"
    assert live["file"] == "src/foundry_mcp/tools/evidence.py"
    assert live["line_count"] == 11
    assert live["line_count"] <= LEAD_LANE_MAX_LINES, "a LIVE lead fix is bounded"
    assert live["test"].startswith("tests/test_evidence.py::")
    assert live["fix_commit"]

    latent = by_defect["D-003"]
    assert latent["tier"] == "LATENT"
    assert latent["file"] == "src/foundry_mcp/tools/foundry_report.py"
    assert latent["line_count"] == 64
    # And the lane limit did not fire on the way in: the fixture's LATENT lead
    # fix is three times the size a LIVE one may be, and it was still recorded.
    # LATENT of ANY size is lane-eligible, so no line count can refuse it.
    assert latent["line_count"] > LEAD_LANE_MAX_LINES
    assert latent["test"] and latent["fix_commit"]
    # Neither row is missing a field the other has. The report cannot show a
    # measurement it was never handed, so the field list is asserted as a SET.
    assert set(live) == set(latent), sorted(set(live) ^ set(latent))


def test_a_multi_file_lead_fix_names_every_file_and_an_unmeasured_one_says_so(
    report_env,
):
    """D-078 — the report is the audit trail, so it cannot answer "in which
    file" with a blank cell.

    AC-022 / GI-003 make the `lead_fix` record the trail for a fix nobody else
    reviewed, and name "in which file" as one of the five things a reader must
    be able to re-derive. `record_lead_fix_handoff` writes the `git show
    --numstat` rows as `files` beside the derived `file`/`line_count`, and its
    handoffs.md mirror renders four distinct labels from them. This reader
    dropped `files` and printed the bare `file`, which is None for every
    multi-file fix — so a five-file fix and a commit git could not read at all
    rendered the SAME empty cell, in the document that exists to tell them
    apart.

    Driven through the real WRITER, so the shapes under test are the shapes a
    run actually produces rather than hand-built records.
    """
    from foundry_mcp.tools.foundry_handoff import (
        MEASUREMENT_UNAVAILABLE,
        record_lead_fix_handoff,
    )

    (report_env / "handoffs.jsonl").write_text("", encoding="utf-8")
    record_lead_fix_handoff(
        report_env, defect_id="D-101", tier="LATENT",
        test="tests/test_x.py::test_a", fix_commit="a" * 40,
        files=[{"path": f"pkg/m{n}.py", "added": 50, "deleted": 50}
               for n in range(5)],
    )
    record_lead_fix_handoff(
        report_env, defect_id="D-102", tier="LATENT",
        test="tests/test_x.py::test_b", fix_commit="0" * 40,
        files=None, file=None, line_count=None,
    )

    _generate(report_env)
    section = _document(report_env)["lead_fix_records"]
    by_defect = {r["defect_id"]: r for r in section["records"]}

    measured = by_defect["D-101"]
    # The rows themselves reach report.json, not just the derived summary.
    assert [row["path"] for row in measured["files"]] == [
        f"pkg/m{n}.py" for n in range(5)
    ]
    assert measured["line_count"] == 500
    assert measured["file"] is None, "a multi-file fix has no single path"
    assert [row["path"] for row in measured["file_rows"]] == [
        f"pkg/m{n}.py" for n in range(5)
    ]
    assert all(row["line_count"] == 100 for row in measured["file_rows"])

    unavailable = by_defect["D-102"]
    assert unavailable["files"] is None
    assert unavailable["line_count"] is None
    assert unavailable["file_rows"] == [
        {"path": MEASUREMENT_UNAVAILABLE, "line_count": None}
    ]

    # And the two are DISTINGUISHABLE in the markdown, which is the surface the
    # filing measured. Every path appears; the unavailable one says so in the
    # words the handoffs.md mirror uses, read from the writer rather than
    # re-typed here.
    markdown = _markdown(report_env)
    for n in range(5):
        assert f"pkg/m{n}.py" in markdown
    assert MEASUREMENT_UNAVAILABLE in markdown
    rows = [ln for ln in markdown.splitlines() if ln.startswith("| D-101 |")]
    assert len(rows) == 5, ("one markdown row per file", rows)


def test_the_wall_clock_reader_and_the_lead_fix_reader_share_one_handoff_ledger(
    report_env,
):
    """The ADJACENT PATH to D-078's fix: the OTHER reader of handoffs.jsonl.

    `_read_lead_fix_records` is not the only thing that walks this ledger.
    `foundry_state.handoffs_wall_clock_seconds` walks the same file for
    NFR-001's wall-clock column, reached through `_archive_metrics` rather
    than through the lead-fix section, and `artifacts.report_document_status` reads the document
    the two of them write into. A lead-fix record appended by the real writer
    carries an ISO timestamp like every other handoff, so it participates in
    the span — and this pins that the D-078 rows changed what the lead-fix
    section prints without disturbing what the wall-clock reader derives from
    the same bytes.
    """
    from foundry_mcp.tools.foundry_handoff import record_lead_fix_handoff
    from foundry_mcp.tools.foundry_state import handoffs_wall_clock_seconds

    before, problem = handoffs_wall_clock_seconds(report_env)
    assert problem is None and isinstance(before, float), (before, problem)

    record_lead_fix_handoff(
        report_env, defect_id="D-103", tier="LIVE",
        test="tests/test_x.py::test_c", fix_commit="b" * 40,
        files=[{"path": "pkg/one.py", "added": 3, "deleted": 1}],
    )
    after, problem = handoffs_wall_clock_seconds(report_env)
    assert problem is None
    assert after >= before, "a record appended now can only extend the span"

    _generate(report_env)
    doc = _document(report_env)
    assert "D-103" in {r["defect_id"] for r in doc["lead_fix_records"]["records"]}
    # The same number, through the other caller, in the other section.
    assert doc["baseline_comparison"]["current"]["wall_clock_minutes"] == round(
        after / 60.0, 1
    )
    assert report_document_status(report_env)["missing_sections"] == []


def test_the_lead_fix_event_token_is_read_from_vocab_not_typed(report_env):
    """The falsifier for the test above. If the reader spelled `"lead_fix"`
    itself rather than reading HANDOFF_EVENT_LEAD_FIX, renaming the token in
    the ledger would leave the section silently empty instead of red — which is
    the drift shape schemas/vocab.py exists to make unrepresentable."""
    lines = (report_env / "handoffs.jsonl").read_text(encoding="utf-8").splitlines()
    rewritten = []
    for line in lines:
        record = json.loads(line)
        if record.get("event") == HANDOFF_EVENT_LEAD_FIX:
            record["event"] = "NOT_THE_LEAD_FIX_TOKEN"
        rewritten.append(json.dumps(record))
    (report_env / "handoffs.jsonl").write_text(
        "\n".join(rewritten) + "\n", encoding="utf-8"
    )
    _generate(report_env)
    assert _document(report_env)["lead_fix_records"]["count"] == 0


def test_inspect_modes_per_cycle_names_the_mode_and_the_rule(report_env):
    """AC-036 verbatim: '... the FULL or DELTA decision per cycle ...'

    The RULE travels with the mode. GI-009 puts the decision at the transition
    that opens the INSPECT, so 'FULL' alone does not say whether the width came
    from a phase entry, a verifier touch or the final gate — and those are
    three different facts about the run."""
    _generate(report_env)
    section = _document(report_env)["inspect_modes_per_cycle"]
    assert section["by_mode"] == {"DELTA": 2, "FULL": 4}
    # One list per cycle (D-119), and cycle 5 opened TWO INSPECTs: the widening
    # re-open that earned ASSAY, then TEMPER's F5 entry, which is stamped with
    # the cycle the preceding F2 INSPECT already used because the counter does
    # not advance entering F5.
    rules = {c: [d["rule"] for d in group] for c, group in section["per_cycle"].items()}
    assert rules == {
        "1": ["first_of_phase"],
        "2": ["verifier_touched"],
        "3": ["delta"],
        "4": ["delta"],
        "5": ["final_gate", "first_of_phase"],
    }
    assert section["per_cycle"]["5"][1]["phase"] == "F5"
    assert section["per_cycle"]["5"][1]["decided_by"] == "temper"
    # D-130: `temper` is a PHASE ENTRY, and a phase entry's rule is
    # `first_of_phase` unconditionally (AC-016 / GI-009). The fixture used to
    # record `temper` with `final_gate`, which `_decide_inspect_mode` cannot
    # produce for any input, and this census pinned it.
    assert section["per_cycle"]["5"][1]["rule"] == "first_of_phase"


def _two_inspects_in_cycle_three(report_env) -> None:
    """Four decisions where cycle 3 opened an F2 INSPECT and then an F5 one.

    This is the ordinary F2-to-F5 path, not a corner case: the server counter
    does not advance entering F5, so TEMPER's entry is stamped with the cycle
    the preceding F2 INSPECT already used (D-119).

    WHY THIS IS STILL TYPED, WHEN THE FIXTURE IS DRIVEN (D-130). The committed
    fixture now carries two INSPECTs in its own cycle 5, from production — but
    both are FULL, so a last-entry-wins collapse there is only visible in the
    counts. The case this helper exists for is the MIXED one, DELTA then FULL
    in a single cycle, where a collapse also flips the reported MODE and a lead
    auditing whether DELTA fired is told FULL. Four decisions is the smallest
    ledger that shows it.

    Every row is a shape `_decide_inspect_mode` can actually return, which is
    what D-130 was about: the F5 row used to read rule `final_gate` with
    `decided_by: temper`, and the temper arm records `first_of_phase`
    unconditionally (AC-016 / GI-009). The FULL-ness of the F5 entry — the only
    property this test needs from it — is unchanged by the correction.
    """
    state = _read_json(report_env, "state.json")
    state["inspect_modes"] = [
        {"cycle": 1, "phase": "F2", "mode": "FULL", "rule": "first_of_phase",
         "decided_by": "cast", "required_streams": ["trace", "prove"]},
        {"cycle": 2, "phase": "F2", "mode": "DELTA", "rule": "delta",
         "decided_by": "inspect_start", "required_streams": ["trace"]},
        {"cycle": 3, "phase": "F2", "mode": "DELTA", "rule": "delta",
         "decided_by": "inspect_start", "required_streams": ["trace"]},
        {"cycle": 3, "phase": "F5", "mode": "FULL", "rule": "first_of_phase",
         "decided_by": "temper", "required_streams": ["trace", "prove"]},
    ]
    _write_json(report_env, "state.json", state)


def test_both_inspects_of_one_cycle_are_reported_not_collapsed(report_env):
    """FR-023 verbatim: 'full-vs-delta decisions per cycle'. AC-036: 'the FULL
    or DELTA decision per cycle'.

    D-119: the section built its per-cycle map last-entry-wins, so a cycle that
    opened two INSPECTs kept one of them and `count`, `by_mode` and the table
    were all derived from the survivor. On these four recorded decisions the
    truth is FULL 2 and DELTA 2 over three cycles; the report answered
    `by_mode {DELTA 1, FULL 2}`, `count 3`, and rendered cycle 3 as FULL /
    first_of_phase with no DELTA row for it anywhere — a lead auditing whether
    DELTA fired at cycle 3 was told FULL. `entries` sat beside it carrying the
    full history, which is not a census a reader has any reason to re-derive."""
    _two_inspects_in_cycle_three(report_env)
    _generate(report_env)

    section = _document(report_env)["inspect_modes_per_cycle"]
    assert section["count"] == 4, "decisions, not cycles"
    assert section["cycle_count"] == 3
    assert section["by_mode"] == {"DELTA": 2, "FULL": 2}
    assert [(d["phase"], d["mode"], d["rule"]) for d in section["per_cycle"]["3"]] == [
        ("F2", "DELTA", "delta"),
        ("F5", "FULL", "first_of_phase"),
    ]

    table = _markdown(report_env).split("## INSPECT mode per cycle", 1)[1]
    table = table.split("\n## ", 1)[0]
    rows = [line for line in table.splitlines() if line.startswith("| 3 |")]
    assert len(rows) == 2, rows
    assert "DELTA" in rows[0] and "F2" in rows[0]
    assert "FULL" in rows[1] and "F5" in rows[1]


# --------------------------------------------------------------------------- #
# D-193 — THE AXIS THE DECISIONS ARE RENDERED AGAINST.
#
# `cycle_count` counts the cycles this LEDGER has a decision for. AC-036 names
# an axis over the run's CYCLES. The two are the same number only on a run that
# recorded a decision in every cycle, and the run that filed this defect is not
# one: 15 cycles, 5 recorded, and both numbers published in one document with
# nothing reconciling them and no sentence naming the ten cycles that recorded
# nothing.
# --------------------------------------------------------------------------- #

def _a_decision(cycle: int, **extra) -> dict:
    """One `inspect_modes` entry in the shape `_decide_inspect_mode` writes."""
    entry = {
        "cycle": cycle,
        "phase": "F2",
        "mode": "FULL",
        "rule": "verifier_touched",
        "decided_by": "inspect_start",
        "required_streams": ["trace", "prove"],
    }
    entry.update(extra)
    return entry


def _minimal_run(tmp_path, name: str, state: dict) -> Path:
    """A run directory carrying only the two ledgers `generate_report` needs.

    An ABSENT ledger is an empty section, not a refusal (the report section
    contract), so this is the smallest archive the generator will write a full
    document for — and it is the only way to reach a run whose cycle counter
    NO source can supply, which is one of the branches under test.
    """
    run_dir = tmp_path / "foundry-archive" / name
    run_dir.mkdir(parents=True)
    _write_json(run_dir, "state.json", state)
    _write_json(run_dir, "defects.json", {"defects": []})
    return run_dir


def _inspect_md(run_dir: Path) -> str:
    body = _markdown(run_dir).split("## INSPECT mode per cycle", 1)[1]
    return body.split("\n## ", 1)[0]


def test_the_cycles_with_no_recorded_decision_are_named(report_env):
    """AC-036 verbatim: '... the FULL or DELTA decision per cycle ...'.

    D-193: that is an axis over the run's cycles, not over the rows that happen
    to exist. This fixture is the filing run's own shape — a counter that
    reached 14 and an `inspect_modes` list that starts at 10, because the
    ledger was only written from the cycle the feature landed in. REPORT.md
    read '5 recorded INSPECT-opening decisions over 5 cycles' and, 64 lines
    later, '| GRIND cycles | 22 | | 12 | 15 |'; report.json carried
    `cycle_count` 5 beside `baseline_comparison.current.grind_cycles` 15. Two
    numbers for one axis in one document, and no sentence anywhere saying that
    cycles 0-9 carry no recorded decision.

    The four sibling partial sections each disclose their own gap. This one now
    does too, off the SAME `derive_cycle_count` reading the baseline section
    publishes — which is what makes the two numbers incapable of disagreeing.
    """
    state = _read_json(report_env, "state.json")
    state["cycle"] = 14
    state["inspect_modes"] = [_a_decision(c) for c in range(10, 15)]
    _write_json(report_env, "state.json", state)
    _generate(report_env)

    doc = _document(report_env)
    section = doc["inspect_modes_per_cycle"]
    assert section["count"] == 5
    assert section["cycle_count"] == 5, "cycles that CARRY a decision"
    assert section["cycle_axis_length"] == 15, "cycles the run RAN"
    assert section["cycles_without_decision"] == list(range(10))

    # THE RECONCILIATION. One derivation, two sections, one number.
    assert section["cycle_axis_length"] == (
        doc["baseline_comparison"]["current"]["grind_cycles"]
    ), "the axis and the baseline's GRIND cycles are the same reading"

    note = section["note"]
    assert "15 cycles" in note, note
    assert "cover 5 of them" in note, note
    assert "cycles 0-9 carry none" in note, note
    assert "none is inferred here" in note, note
    # BOTH documents, byte for byte. A disclosure that lives only in the
    # markdown is one report.json's readers — the DONE gate among them — never
    # see, and the JSON is the surface FR-038 says carries the same data.
    assert note in _inspect_md(report_env)


def test_the_headline_no_longer_calls_its_own_row_count_the_runs_cycles(report_env):
    """The falsifier for the test above, on the sentence that stated the claim.

    'N recorded INSPECT-opening decisions over N cycles' reads as a complete
    census of the run, and on the filing archive both Ns were 5 while the run
    had run 15. The headline now says what the number IS — the cycles that
    carry a decision — and the axis is the sentence beside it."""
    state = _read_json(report_env, "state.json")
    state["cycle"] = 14
    state["inspect_modes"] = [_a_decision(c) for c in range(10, 15)]
    _write_json(report_env, "state.json", state)
    _generate(report_env)

    headline = _inspect_md(report_env).strip().splitlines()[0]
    assert "5 cycles that carry one" in headline, headline
    assert "over 5 cycles;" not in headline, headline


def test_a_run_that_recorded_every_cycle_says_every_cycle_is_covered(report_env):
    """The other arm. A section that only ever printed a gap sentence would be
    read as an alarm; this one states coverage either way, so 'no sentence' is
    never the answer and the absence of a gap is a MEASUREMENT rather than a
    silence indistinguishable from the D-193 one."""
    state = _read_json(report_env, "state.json")
    state["cycle"] = 5
    state["inspect_modes"] = [_a_decision(c) for c in range(0, 6)]
    _write_json(report_env, "state.json", state)
    _generate(report_env)

    section = _document(report_env)["inspect_modes_per_cycle"]
    assert section["cycle_axis_length"] == 6
    assert section["cycle_count"] == 6
    assert section["cycles_without_decision"] == []
    assert "Recorded decisions cover every one of them." in section["note"]
    assert "carry none" not in section["note"], section["note"]
    assert section["note"] in _inspect_md(report_env)


def test_an_underivable_axis_is_declined_not_taken_from_the_ledger(tmp_path):
    """The fabrication this section refuses to commit.

    An axis derived from the very ledger being rendered against it is complete
    BY CONSTRUCTION — every recorded cycle is on it and no cycle is off it — so
    falling back on the highest cycle `inspect_modes` names would turn 'the
    counter cannot say' into 'nothing is missing'. That is the same fabrication
    D-193 names, wearing a different hat, and it would have made this section
    silent on precisely the archives least able to afford it.

    `derive_cycle_count`'s own rule is the one applied: 'count is None only
    when NO source could supply a number', and 'cannot say' and 'nothing
    missing' are different answers."""
    run_dir = _minimal_run(
        tmp_path,
        "no-counter-run",
        {"phase": "F2", "inspect_modes": [_a_decision(3), _a_decision(4)]},
    )
    assert generate_report(tmp_path, run_dir)["ok"] is True

    section = _document(run_dir)["inspect_modes_per_cycle"]
    assert section["count"] == 2
    assert section["cycle_axis_length"] is None
    assert section["cycles_without_decision"] is None

    note = section["note"]
    assert "cycle axis cannot be derived" in note, note
    assert "cannot say which cycles carry no recorded decision" in note, note
    # The ledger's own floor would have printed exactly this, and it would have
    # been a claim no artifact in the archive supports.
    assert "0-2" not in note, note
    assert "None" not in note, f"no Python repr reaches operator prose: {note}"
    assert note in _inspect_md(run_dir)


def test_a_halted_runs_axis_names_why_it_exceeds_the_grind_cycle_count(tmp_path):
    """D-175 met D-193 here. On a halted run `derive_cycle_count` publishes
    `index` rather than `index + 1`, because the GRIND the cap refused to open
    never ran — but the counter DID reach `index`, and a decision is stamped
    there. So the axis is one longer than `grind_cycles` on exactly this run
    shape, and the note states the cause instead of leaving a reader to
    discover two numbers and no reason."""
    run_dir = _minimal_run(
        tmp_path,
        "halt-run",
        {
            "phase": RUN_PHASE_HALTED,
            "cycle": 2,
            "halted_at_cycle": 2,
            "halted_reason": "--max-cycles 2 reached: opening GRIND cycle 3 "
                             "would exceed it",
            "inspect_modes": [_a_decision(1), _a_decision(2)],
        },
    )
    assert generate_report(tmp_path, run_dir)["ok"] is True

    doc = _document(run_dir)
    section = doc["inspect_modes_per_cycle"]
    assert section["cycle_axis_length"] == 3, "the counter ran 0, 1, 2"
    assert doc["baseline_comparison"]["current"]["grind_cycles"] == 2
    assert section["cycles_without_decision"] == [0]

    note = section["note"]
    assert "the two differ because" in note, note
    assert "the halt subtracts the GRIND cycle the cap refused to open" in note
    assert "cycle 0 carries none" in note, note


def test_a_decision_above_the_counter_widens_the_axis_it_is_rendered_against(
    tmp_path,
):
    """An axis shorter than its own table would publish a length that denies a
    row printed underneath it. A recorded decision at cycle 5 is direct
    evidence cycle 5 ran, so the axis widens to cover it and the note names the
    widening — which is the opposite of the refused fallback in
    `test_an_underivable_axis_is_declined_not_taken_from_the_ledger`: this
    widens an axis the counter already supplied, and it can still show a gap."""
    run_dir = _minimal_run(
        tmp_path,
        "counter-behind-run",
        {"phase": "F2", "cycle": 2, "inspect_modes": [_a_decision(5)]},
    )
    assert generate_report(tmp_path, run_dir)["ok"] is True

    doc = _document(run_dir)
    section = doc["inspect_modes_per_cycle"]
    assert section["cycle_axis_length"] == 6, "0 through 5, the row's own cycle"
    assert doc["baseline_comparison"]["current"]["grind_cycles"] == 3
    assert section["cycles_without_decision"] == [0, 1, 2, 3, 4]

    note = section["note"]
    assert "a recorded decision names cycle 5" in note, note
    assert "above the highest cycle this run's other ledgers reach" in note
    # Every rendered row's cycle is on the axis the section published.
    for cycle in section["per_cycle"]:
        assert int(cycle) < section["cycle_axis_length"]


def test_the_baseline_archives_axis_is_derived_from_its_own_directory(report_env):
    """THE ADJACENT PATH D-193's fix walked into.

    `_inspect_modes_section` has three callers and the defect came in through
    one of them (`generate_report`). The other two are `_archive_metrics` and
    `_baseline_comparison_section`, and `_archive_metrics` is called a SECOND
    time on a directory that is not this run's — thunder-viper's archive, to
    fill NFR-001's derived column. Giving the section a `run_dir` is exactly
    the change that can make that call read the wrong archive's counter, and
    the symptom would be invisible: a plausible number, derived from the run
    beside the one it names.

    So the two archives are planted with counters that cannot be confused —
    this run at 14, thunder-viper's at 30 — and each column is asserted
    against its own directory."""
    baseline_dir = report_env.parent / THUNDER_VIPER_BASELINE["run"]
    baseline_dir.mkdir()
    _write_json(
        baseline_dir, "state.json",
        {"phase": "F6", "cycle": 30,
         "inspect_modes": [_a_decision(30, phase="F5", decided_by="temper")]},
    )
    _write_json(baseline_dir, "defects.json", {"defects": []})

    state = _read_json(report_env, "state.json")
    state["cycle"] = 14
    state["inspect_modes"] = [_a_decision(c) for c in range(10, 15)]
    _write_json(report_env, "state.json", state)
    _generate(report_env)

    doc = _document(report_env)
    # This run's section, from this run's counter.
    assert doc["inspect_modes_per_cycle"]["cycle_axis_length"] == 15
    # The baseline's derived column, from the BASELINE's counter — 30 + 1, and
    # its single F5 decision, neither of which is anywhere in this run.
    derived = doc["baseline_comparison"]["baseline_derived"]
    assert derived["grind_cycles"] == 31
    assert derived["post_verification_cycles"] == 1
    # The published baseline column is still the constant, untouched (D-085).
    assert doc["baseline_comparison"]["baseline_metrics"]["grind_cycles"] == (
        THUNDER_VIPER_BASELINE["grind_cycles"]
    )


def test_the_gap_prints_as_ranges_not_as_a_ten_item_list(report_env):
    """`_cycle_ranges`. The gap on the filing run is ten cycles wide, and a
    ten-item comma list reads as a data dump rather than as the fact it
    carries. Runs of consecutive cycles are the shape the gap has."""
    state = _read_json(report_env, "state.json")
    state["cycle"] = 9
    state["inspect_modes"] = [_a_decision(c) for c in (3, 8, 9)]
    _write_json(report_env, "state.json", state)
    _generate(report_env)

    section = _document(report_env)["inspect_modes_per_cycle"]
    assert section["cycles_without_decision"] == [0, 1, 2, 4, 5, 6, 7]
    assert "cycles 0-2, 4-7 carry none" in section["note"], section["note"]
    assert fr._cycle_ranges([]) == ""
    assert fr._cycle_ranges([4]) == "4"
    assert fr._cycle_ranges([9, 7, 8]) == "7-9"


def test_the_f5_reopen_of_a_cycle_counts_one_post_verification_cycle(report_env):
    """NFR-001's post-verification column, off the same `inspect_modes` ledger
    the census reads — the ADJACENT reader (`_archive_metrics`), which walks
    the per-cycle map for a different question.

    The number is DISTINCT CYCLES that opened an F5 INSPECT, so a cycle whose
    F2 INSPECT was reopened in F5 counts once and not twice. D-088 unified this
    with `measure-run.py`, which counts off its own collapsed map; both answer 1
    here, which is what keeps the two surfaces from publishing different numbers
    into the same comparison."""
    _two_inspects_in_cycle_three(report_env)
    _generate(report_env)

    current = _document(report_env)["baseline_comparison"]["current"]
    assert current["post_verification_cycles"] == 1

    from foundry_mcp.tools.foundry_report import _archive_metrics

    assert _archive_metrics(report_env)["post_verification_cycles"] == 1


def test_spend_reports_tokens_and_minutes_and_no_money_at_all(report_env):
    """NFR-002 verbatim: 'Avoids a second hand-kept price table (the house
    anti-pattern). The report shows tokens and minutes per phase/cycle and the
    run total.'

    Two assertions, because the requirement has two halves. Minutes must be
    THERE and derived (duration_ms / 60000), and money must be ABSENT — no
    currency symbol, no rate column, no key whose name says cost. A dollar
    figure would need a per-model rate table kept by hand in a second place,
    which is the anti-pattern named."""
    _generate(report_env)
    section = _document(report_env)["spend_per_phase_and_cycle"]

    assert section["total"]["tokens"] == 2_520_000
    assert section["total"]["minutes"] == round(
        section["total"]["duration_ms"] / 60_000.0, 2
    )
    assert section["by_phase"]["F2"]["tokens"] == 980_000
    # Cycles 0-2 are the ledger's own rows. Cycles 3-5 have no spend row and
    # appear because the dispatch record puts an unreported agent in them
    # (D-163): a phase or cycle whose every dispatch went unaccounted for is
    # exactly the one whose gap most needs a line, and a table built only from
    # ledger rows is the table that cannot show it.
    assert set(section["by_cycle"]) == {"0", "1", "2", "3", "4", "5"}
    for cycle in ("3", "4", "5"):
        assert section["by_cycle"][cycle]["records"] == 0
        assert section["by_cycle"][cycle]["unreported"] > 0

    money = re.compile(r"[$€£¥]|USD|\bcost\b|\bprice\b|\bdollar", re.I)
    for bucket in (*section["by_phase"].values(), *section["by_cycle"].values(),
                   section["total"]):
        assert not money.search(json.dumps(bucket)), bucket
    # And nowhere in either whole document either.
    assert not money.search(json.dumps(_document(report_env)))
    assert not money.search(_markdown(report_env))


def test_the_agents_field_means_distinct_agents_exactly_as_the_rollup_does(
    report_env,
):
    """D-090 — one field name, two meanings, side by side in one document.

    CT-013 / AC-033 / FR-021. The orchestrator's spend roll-up publishes
    `agents` as a count of DISTINCT agents — D-038 made it so, over a set, from
    this same ledger — and this reader published the same key as a count of
    RECORDS. So `report.json` carried `by_phase.F3 {tokens 240000, agents 3}`
    beside `state_rollup {tokens 240000, agents 2}` and the two disagreed BY
    CONSTRUCTION on every run where any agent reported twice, which a
    re-dispatched GRIND teammate does every cycle. The docstring justified
    carrying both because "the two disagreeing is a fact worth being able to
    see" — but a drift signal that is permanently noisy detects no drift.

    Driven exactly as the filing describes it: three spend calls in one phase
    from two agents.
    """
    from foundry_mcp.tools.orchestration.spend import _spend_summary

    (report_env / SPEND_LEDGER_FILENAME).write_text(
        "\n".join(
            json.dumps(row) for row in [
                {"agent": "casting-1", "phase": "F3", "cycle": 2,
                 "tokens": 80_000, "duration_ms": 300_000},
                {"agent": "casting-1", "phase": "F3", "cycle": 2,
                 "tokens": 80_000, "duration_ms": 300_000},
                {"agent": "casting-2", "phase": "F3", "cycle": 2,
                 "tokens": 80_000, "duration_ms": 330_000},
            ]
        ) + "\n",
        encoding="utf-8",
    )
    state = _read_json(report_env, "state.json")
    state["spend"] = {
        "by_phase": {"F3": {"tokens": 240_000, "duration_ms": 930_000,
                            "agents": 2, "unreported": 1}},
        "by_cycle": {"2": {"tokens": 240_000, "duration_ms": 930_000,
                           "agents": 2, "unreported": 1}},
        "total": {"tokens": 240_000, "duration_ms": 930_000,
                  "agents": 2, "unreported": 1},
    }
    _write_json(report_env, "state.json", state)

    _generate(report_env)
    section = _document(report_env)["spend_per_phase_and_cycle"]
    bucket = section["by_phase"]["F3"]

    assert bucket["records"] == 3, "three ledger rows"
    assert bucket["agents"] == 2, "two agents — the orchestrator's own number"
    assert bucket["tokens"] == 240_000, "tokens stay the LEDGER's"
    # D-163: `unreported` is DERIVED now, not copied from the roll-up beside
    # it. The fixture dispatches casting-5 at `grind` and this ledger reports
    # no F3 row for it, so the derivation is 1 — which is what the roll-up
    # happens to claim here, and the seeded-wrong case is driven in
    # `test_the_unreported_count_is_derived_not_copied_from_the_rollup`.
    assert bucket["unreported"] == 1

    # The pin that closes D-090: the orchestrator's roll-up and this section
    # cannot answer the AGENTS question differently, because there is one
    # answer.
    rollup = _spend_summary(report_env)
    assert bucket["agents"] == rollup["by_phase"]["F3"]["agents"]
    assert section["total"]["agents"] == rollup["total"]["agents"]
    # Scoped to the fields this test is about. The roll-up seeded above claims
    # `unreported: 1` on buckets the dispatch record derives differently, and
    # D-163's rule is that such a claim is NAMED here rather than published as
    # the answer — so those rows are expected, and their absence would mean the
    # copy came back.
    assert [d for d in section["disagreements"] if d["field"] != "unreported"] == (
        []
    ), section["disagreements"]
    assert {d["field"] for d in section["disagreements"]} == {"unreported"}

    # And the markdown gives each number its own column, so neither can be
    # read as the other. It already said "Records" over the cell the JSON
    # called `agents`.
    table = _markdown(report_env).split("## Spend per phase and cycle", 1)[1]
    header = next(ln for ln in table.splitlines() if ln.startswith("| Scope |"))
    assert "Records" in header and "Agents" in header, header


def test_an_unrecorded_agent_count_is_stated_in_words_never_printed_as_None(
    report_env,
):
    """D-150 — the spend headline printed a Python None as an agent count.

    AC-036 / FR-023 / FR-038. `agents` is the roll-up's number or it is
    nothing: `foundry_state.spend_bucket` seeds it None and `_read_spend`
    fills it only
    from `state.json.spend`, on the stated ground that "nobody recorded how
    many agents" and "no agents ran" are different facts. The headline
    interpolated that None raw — `{total.get('agents')}` — while the `records`
    beside it used `.get('records', 0)`, so a run with no spend roll-up
    rendered "0 spend records over None distinct agents." into operator-facing
    prose. That is the normal state at the moment a --max-cycles halt
    generates its report.

    The same section's TABLE rendered the same null as a blank Agents cell, so
    one section printed one null two ways and the spelling a reader met first
    was a repr. Both surfaces are driven here: the sentence must state the
    absence in words, and it must name the blank cells so the two agree
    instead of contradicting each other.

    `or 0` is asserted against explicitly. It would print the fabrication
    `_read_spend` refuses by name, and `report.json` would still carry null
    beside it — two documents disagreeing about one field.
    """
    # (a) THE UNKNOWN BRANCH: a run whose state.json carries every version
    #     field and no `spend` roll-up at all — the second door the filing was
    #     driven through, and what a halted run looks like.
    state = _read_json(report_env, "state.json")
    del state["spend"]
    _write_json(report_env, "state.json", state)
    (report_env / SPEND_LEDGER_FILENAME).write_text("", encoding="utf-8")

    _generate(report_env)
    section = _document(report_env)["spend_per_phase_and_cycle"]

    # report.json was always honest and stays honest — null, not 0.
    assert section["total"]["agents"] is None
    assert section["total"]["records"] == 0

    table = _markdown(report_env).split("## Spend per phase and cycle", 1)[1]
    headline = next(ln for ln in table.splitlines() if "spend records" in ln)

    assert "None" not in headline, headline
    assert "over None distinct agents" not in headline, headline
    assert "0 distinct agents" not in headline, (
        "`or 0` is the fabrication `_read_spend` refuses by name: it would "
        "report 'no agents ran' for a run nobody measured"
    )
    assert "was not recorded" in headline, headline
    assert "state.json.spend" in headline, (
        "the sentence names the source that is missing, so the reader knows "
        "what to go and look at"
    )
    # THE TWO SURFACES AGREE. The sentence describes the cells, and the cells
    # are what it describes — blank, not zero.
    assert "blank" in headline and "not because they are zero" in headline
    total_row = next(ln for ln in table.splitlines() if ln.startswith("| run |"))
    cells = [c.strip() for c in total_row.split("|")[1:-1]]
    assert cells[:5] == ["run", "total", "0", "0.0", "0"], total_row
    assert cells[5] == "", (
        f"Agents is the blank cell the sentence names: {total_row}"
    )
    # D-163 narrowed the sentence to that ONE column. `unreported` is derived
    # from the dispatch record now, not read from the roll-up, so it is a real
    # count on exactly the run this arm describes — and a sentence still
    # calling it blank would send a reader looking for an empty cell that
    # carries a number.
    assert cells[6] == str(section["total"]["unreported"]), total_row
    assert int(cells[6]) > 0, (
        "this fixture dispatches agents that never reported spend, so the "
        "derived count is the thing the old roll-up read could never show"
    )
    assert "Agents and Unreported cells below are blank" not in headline, (
        "the sentence used to call BOTH cells blank; Unreported is a count now"
    )
    assert "The Agents cells below are blank" in headline, headline
    assert "Unreported cells are derived" in headline, (
        "and it says what the other column is instead, so a reader is not "
        "left to guess which of the two the 'blank' clause covers"
    )

    # (b) THE KNOWN BRANCH is unchanged: a recorded count is still named as a
    #     number, in the same sentence it always was.
    state["spend"] = {
        "by_phase": {}, "by_cycle": {},
        "total": {"tokens": 0, "duration_ms": 0, "agents": 4, "unreported": 0},
    }
    _write_json(report_env, "state.json", state)

    _generate(report_env)
    known = next(
        ln for ln in
        _markdown(report_env).split("## Spend per phase and cycle", 1)[1].splitlines()
        if "spend records" in ln
    )
    assert "0 spend records over 4 distinct agents." in known, known
    assert "tokens and minutes only (NFR-002)" in known, known


def test_a_ledger_and_rollup_disagreement_is_named_not_printed_twice(report_env):
    """D-090's other half — what "worth being able to see" now MEANS.

    Carrying both numbers was the right instinct aimed at the wrong pair. The
    pair that really is two derivations of one fact is the roll-up's tokens
    against the ledger's, and when those part the report says so in words
    rather than printing two values under one label. The shipped fixture has
    exactly this shape: its `state.json.spend` records more tokens than its
    `spend.jsonl` accounts for.
    """
    _generate(report_env)
    section = _document(report_env)["spend_per_phase_and_cycle"]

    rollup_f1 = _read_json(report_env, "state.json")["spend"]["by_phase"]["F1"]
    assert section["by_phase"]["F1"]["tokens"] != rollup_f1["tokens"], (
        "fixture premise: the two sources disagree about F1"
    )
    named = {
        (d["scope"], d["key"], d["field"]) for d in section["disagreements"]
    }
    assert ("by_phase", "F1", "tokens") in named, section["disagreements"]
    assert ("run", "total", "tokens") in named, section["disagreements"]
    # `agents` is checked too, against the ledger's DISTINCT names — the same
    # derivation the orchestrator makes. The fixture's roll-up claims three
    # agents in F1 where the ledger names two, and a report that published the
    # roll-up's number with no signal would hide exactly the staleness the
    # cross-check exists to expose. The ledger's count is never written into
    # the bucket: that is what made `agents` mean two things.
    assert ("by_phase", "F1", "agents") in named, section["disagreements"]
    agents_entry = next(d for d in section["disagreements"]
                        if (d["scope"], d["key"], d["field"])
                        == ("by_phase", "F1", "agents"))
    assert agents_entry == {"scope": "by_phase", "key": "F1", "field": "agents",
                            "ledger": 2, "state_rollup": 3}
    assert section["by_phase"]["F1"]["agents"] == 3, "published value is the roll-up's"

    entry = next(d for d in section["disagreements"]
                 if (d["scope"], d["key"], d["field"]) == ("by_phase", "F1", "tokens"))
    assert entry["ledger"] == section["by_phase"]["F1"]["tokens"]
    assert entry["state_rollup"] == rollup_f1["tokens"]

    markdown = _markdown(report_env)
    assert "The ledger and `state.json.spend` disagree on:" in markdown
    assert "by_phase F1 tokens" in markdown


def test_an_unreported_dispatch_is_shown_and_no_gate_refuses_on_it(report_env):
    """AC-034 verbatim: 'A dispatched agent with no spend record is shown as
    unreported in Foundry-Next and the report, and no gate refuses on it.'

    The 'no gate refuses' half is asserted structurally: generation succeeds
    with unreported dispatches present, and `artifacts.report_document_status` — the read the DONE
    gate actually makes — reports the report complete. An unreported dispatch
    is a gap in the MEASUREMENT, not a defect in the build.

    D-048 — EVERY BUCKET KEY IS A PHASE. `spawns.log` records the dispatch VERB
    (`cast`, `grind`); `Foundry-Spend`'s schema documents the phase as "e.g.
    F1, F2, F3", and the fixture's own `state.json.spend.by_phase` is keyed
    F1/F2/F3. This section used to bucket under `cast` and `grind`, so the
    report carried two keys that are not phases and could not be lined up
    against the roll-up beside them."""
    from foundry_mcp.tools.foundry_state import DISPATCH_PHASE_TO_RUN_PHASE

    _generate(report_env)
    section = _document(report_env)["unreported_dispatches"]

    # casting-8 was dispatched in CAST and casting-5 in GRIND with no spend
    # line; `test` and `research_audit` ran as F2 streams and reported none.
    cast_phase = DISPATCH_PHASE_TO_RUN_PHASE["cast"]
    grind_phase = DISPATCH_PHASE_TO_RUN_PHASE["grind"]
    assert "casting-8" in section["by_phase"][cast_phase]
    assert "casting-5" in section["by_phase"][grind_phase]
    assert {"test", "research_audit"} <= set(section["by_phase"]["F2"])
    # And the agents that DID report are not listed.
    assert "casting-1" not in section["by_phase"].get(cast_phase, [])
    assert "prove" not in section["by_phase"].get("F2", [])
    # No key is a dispatch verb. That is the whole of D-048 as one assertion.
    assert not set(section["by_phase"]) & set(DISPATCH_PHASE_TO_RUN_PHASE), (
        sorted(section["by_phase"])
    )

    # The DERIVED count agrees with the roll-up the run RECORDED beside it.
    # `foundry_state.overlay_unreported` writes the derived number onto
    # the C-4 buckets, so on a real run these two cannot part; pinning them
    # against each other rather than each against a literal is what stops this
    # section drifting away from the state.json a lead reads next to it. (The
    # fixture carried 4 against a derivation of 5 — the F2 roster names five
    # streams and the spend ledger accounts for two — which no assertion here
    # was looking at.)
    rollup = _read_json(report_env, "state.json")["spend"]
    assert section["count"] == rollup["total"]["unreported"]
    assert section["count"] == sum(
        bucket["unreported"] for bucket in rollup["by_phase"].values()
    )

    assert report_document_status(report_env)["present"] is True
    assert report_document_status(report_env)["missing_sections"] == []


def test_the_agent_id_spelling_agrees_with_the_spawn_doors(report_env):
    """D-013 — there is ONE agent-id spelling and `foundry_spawn` owns it.

    This used to pin a hand-typed copy against the original. A copy pinned by
    a test is still a second derivation, and it drifted where no test was
    watching: this module keyed a live `spawns.log` row as `casting-1` while
    `foundry_orchestrator._dispatched_agents` keyed the SAME row as `1`, so no
    single `Foundry-Spend` call could clear both surfaces. The copy is gone —
    `_agent_id_for_casting` now delegates — and this asserts the delegation
    holds across the id shapes a manifest actually carries."""
    from foundry_mcp.tools.foundry_spawn import _agent_id_for_casting as spawn_spelling

    for casting_id in (1, 5, 42, "7", "wave-3"):
        assert fr._agent_id_for_casting(casting_id) == spawn_spelling(casting_id)


def test_a_live_spawn_row_is_keyed_by_the_canonical_spelling(report_env):
    """D-013 — a real `spawns.log` row carries `casting_id` and NO `agent` key.

    That is the row shape `foundry_spawn` writes, and it is where the two
    spellings parted: this module keyed it `casting-1` while
    `foundry_orchestrator._dispatched_agents` fell back to the bare
    `casting_id` and keyed it `1`. Neither id was wrong on its own; what was
    wrong is that there were two, so no `Foundry-Spend` call could clear both
    surfaces and the documented spelling cleared neither."""
    from foundry_mcp.tools.foundry_spawn import _agent_id_for_casting

    (report_env / "spawns.log").write_text(
        json.dumps({"timestamp": "2026-09-03T00:55:14+00:00", "casting_id": 9,
                    "phase": "cast", "wave": 1, "prompt_hash": "sha256:deadbeef",
                    "bulk": True}) + "\n",
        encoding="utf-8",
    )
    (report_env / SPEND_LEDGER_FILENAME).write_text("", encoding="utf-8")

    from foundry_mcp.tools.foundry_state import DISPATCH_PHASE_TO_RUN_PHASE

    _generate(report_env)
    section = _document(report_env)["unreported_dispatches"]
    cast_phase = DISPATCH_PHASE_TO_RUN_PHASE["cast"]
    assert _agent_id_for_casting(9) == "casting-9"
    assert section["by_phase"][cast_phase] == ["casting-9"]
    assert "9" not in section["by_phase"][cast_phase], (
        "the bare casting_id is the OTHER surface's old spelling"
    )


def test_the_documented_spend_phase_clears_the_dispatch_verb_it_maps_to(report_env):
    """D-048. `spawns.log` records `phase: "cast"`; the `Foundry-Spend` schema
    documents the phase as "e.g. F1, F2, F3". The exact `(agent, phase)` pair
    could therefore NEVER match a teammate dispatch, and D-013's agent-wide
    fallback — "an agent that reported spend in ANY phase is a reported agent"
    — was the only clause that ever cleared one.

    That fallback is gone (see the test below), so this property now rests on
    the thing that should always have carried it: the two vocabularies are
    reconciled through `DISPATCH_PHASE_TO_RUN_PHASE`, and a lead who spends the
    documented spelling clears the dispatch it names."""
    from foundry_mcp.tools.foundry_state import DISPATCH_PHASE_TO_RUN_PHASE

    (report_env / "spawns.log").write_text(
        json.dumps({"timestamp": "2026-09-03T00:55:14+00:00", "casting_id": 9,
                    "phase": "cast"}) + "\n",
        encoding="utf-8",
    )
    (report_env / SPEND_LEDGER_FILENAME).write_text(
        json.dumps({"agent": "casting-9",
                    "phase": DISPATCH_PHASE_TO_RUN_PHASE["cast"], "cycle": 0,
                    "tokens": 1000, "duration_ms": 60000,
                    "recorded_at": "2026-09-03T01:00:00+00:00"}) + "\n",
        encoding="utf-8",
    )
    _generate(report_env)
    section = _document(report_env)["unreported_dispatches"]
    named = {a for agents in section["by_phase"].values() for a in agents}
    assert "casting-9" not in named, (
        "the documented spend spelling clears the dispatch verb it maps to"
    )
    # The F2 stream roster in stream-rollup.json still contributes its own
    # unreported agents; this is about casting-9 and nothing else.
    assert named, "the fixture's F2 roster is untouched by this test"


def test_a_gap_at_one_phase_is_visible_though_the_agent_reported_at_another(
    report_env,
):
    """D-047 / FR-022 verbatim: 'the report shows N agents unreported per phase
    so the gap is visible.'

    The old clause cleared an agent EVERYWHERE once it reported spend anywhere,
    so casting-9 dispatched at two phases and accounted for at one of them
    appeared nowhere at all — the per-phase gap the requirement names was the
    one thing the section could not show. A pair is unreported when no spend
    row carries that exact `(agent, phase)`, and nothing else clears it."""
    from foundry_mcp.tools.foundry_state import DISPATCH_PHASE_TO_RUN_PHASE

    (report_env / "spawns.log").write_text(
        json.dumps({"timestamp": "2026-09-03T00:55:14+00:00", "casting_id": 9,
                    "phase": "cast"}) + "\n"
        + json.dumps({"timestamp": "2026-09-03T04:10:00+00:00", "casting_id": 9,
                      "phase": "grind"}) + "\n",
        encoding="utf-8",
    )
    (report_env / SPEND_LEDGER_FILENAME).write_text(
        json.dumps({"agent": "casting-9",
                    "phase": DISPATCH_PHASE_TO_RUN_PHASE["cast"], "cycle": 0,
                    "tokens": 1000, "duration_ms": 60000,
                    "recorded_at": "2026-09-03T01:00:00+00:00"}) + "\n",
        encoding="utf-8",
    )
    _generate(report_env)
    section = _document(report_env)["unreported_dispatches"]
    cast_phase = DISPATCH_PHASE_TO_RUN_PHASE["cast"]
    grind_phase = DISPATCH_PHASE_TO_RUN_PHASE["grind"]
    assert "casting-9" not in section["by_phase"].get(cast_phase, []), (
        "the phase it DID account for is clear"
    )
    assert "casting-9" in section["by_phase"][grind_phase], (
        "the phase it did NOT account for is the gap FR-022 wants visible"
    )


def test_the_unreported_rule_is_hosted_once_in_the_leaf_module(report_env):
    """D-047 / D-048's falsifier, and the reason the rule moved at all.

    `Foundry-Next` and the report had two derivations of one question. D-013
    unified the agent-ID spelling between them and left the RULE duplicated, so
    the two surfaces agreed with each other while both disagreed with FR-022 —
    and the next fix had to be applied twice or they would part again.

    The rule now lives in `foundry_state.unreported_dispatch_pairs`, the leaf
    module both readers already import, and this asserts the report really
    delegates: feed the helper the same three inputs the report reads and the
    answers are identical, pair for pair."""
    from foundry_mcp.tools.foundry_state import DISPATCH_PHASE_TO_RUN_PHASE
    from foundry_mcp.tools.foundry_spawn import _agent_id_for_casting as spelling
    from foundry_mcp.tools.foundry_state import (
        read_document,
        read_jsonl,
        unreported_dispatch_pairs,
    )

    _generate(report_env)
    section = _document(report_env)["unreported_dispatches"]
    from_report = {
        (agent, phase)
        for phase, agents in section["by_phase"].items()
        for agent in agents
    }

    spawns, _ = read_jsonl(report_env / "spawns.log")
    spend, _ = read_jsonl(report_env / SPEND_LEDGER_FILENAME)
    rollup, _ = read_document(report_env / "stream-rollup.json")
    roster: dict[str, list[str]] = {}
    for bucket in rollup["cycles"].values():
        for stream, entry in bucket.items():
            if isinstance(entry, dict) and "records" in entry:
                roster.setdefault("F2", []).append(stream)
    from_helper = {
        (row["agent"], row["phase"])
        for row in unreported_dispatch_pairs(
            dispatch_rows=spawns,
            stream_roster=roster,
            spend_rows=spend,
            phase_of_dispatch=DISPATCH_PHASE_TO_RUN_PHASE,
            agent_id_of=spelling,
        )
    }
    assert from_report == from_helper, sorted(from_report ^ from_helper)


def test_the_report_and_foundry_next_name_the_same_unreported_agents(report_env):
    """D-013 stated as the property that was violated: two derivations of one
    fact must not disagree.

    `Foundry-Next` and the report render the SAME pair set; they read the same
    two ledgers and must answer the same question the same way, or the operator
    has no spelling that satisfies both.

    GI-024 — AND THE OTHER SURFACE IS NOW THE LEAF, WHICH IS THE POINT. This
    drove `foundry_orchestrator._unreported_dispatches`, one of two walks of
    the two ledgers. The RULE moved to `foundry_state.unreported_dispatch_pairs`
    at D-047/D-048 and the INPUT ASSEMBLY moved to
    `foundry_state.unreported_dispatch_inputs` in casting 10's group 1, so the
    comparison is now between the report's rendering and the derivation itself
    rather than between two walks — which is a stronger statement than the one
    this register used to make, and it is green today.
    """
    from foundry_mcp.tools.foundry_spawn import _agent_id_for_casting
    from foundry_mcp.tools.foundry_state import (
        DISPATCH_PHASE_TO_RUN_PHASE,
        unreported_dispatch_inputs,
        unreported_dispatch_summary,
    )

    _generate(report_env)
    section = _document(report_env)["unreported_dispatches"]
    from_report = {
        (agent, phase)
        for phase, agents in section["by_phase"].items()
        for agent in agents
    }
    inputs = unreported_dispatch_inputs(report_env)
    assert inputs["problem"] is None
    from_next = {
        (pair["agent"], pair["phase"])
        for pair in unreported_dispatch_summary(
            dispatch_rows=inputs["dispatch_rows"],
            stream_roster=inputs["stream_roster"],
            spend_rows=inputs["spend_rows"],
            phase_of_dispatch=DISPATCH_PHASE_TO_RUN_PHASE,
            agent_id_of=_agent_id_for_casting,
            cycles_of_agent=inputs["cycles_of_agent"],
        )["pairs"]
    }
    assert from_report == from_next, sorted(from_report ^ from_next)


def test_executing_versions_names_the_server_that_ran(report_env):
    """AC-036 verbatim: '... and the executing server and plugin version and
    commit.' GI-004's audit trail lands in the report unchanged."""
    _generate(report_env)
    versions = _document(report_env)["executing_versions"]
    state = _read_json(report_env, "state.json")
    for field in ("server_version", "plugin_version", "server_root", "server_commit"):
        assert versions[field] == state[field], field


def test_baseline_comparison_reads_the_two_vocab_constants(report_env):
    """AC-036's closing clause and NFR-001. The baseline is READ from vocab,
    never re-derived: thunder-viper's archive has no roll-up, no inspect_modes
    and a cycle counter that stayed at 0, so its 22 and 8 are not recoverable
    from it. `measure-run.py` reads the same two constants, which is what makes
    the CLI and this report incapable of disagreeing."""
    _generate(report_env)
    section = _document(report_env)["baseline_comparison"]
    assert section["baseline"] == THUNDER_VIPER_BASELINE
    assert section["target"] == CONVERGENCE_TARGET
    assert section["current"]["run"] == "finer-boundary-run"
    # A COUNT, not the raw 0-based counter (D-036). The fixture's counter sits
    # at 5, so the run executed six cycles — and `measure-run.py` publishes the
    # same six, because both now call `foundry_state.derive_cycle_count`.
    assert section["current"]["grind_cycles"] == 6


def test_the_latent_backlog_says_where_each_defect_lives(report_env):
    """D-029 / NFR-003: 'LATENT stays open, tracked, and listed in the report.'

    The backlog is the ONE artifact that carries LATENT work across runs, and
    the next run's lead receives it with no defects.json to join against. A row
    that named only an id and a description named a fault with no location, so
    the item was not actionable and re-finding the site cost more than the fix
    would have. `file` and `symbol` are part of the row."""
    _generate(report_env)
    rows = _document(report_env)["latent_backlog"]["defects"]
    assert rows, "the fixture carries two open LATENT defects"
    source = {d["id"]: d for d in _read_json(report_env, "defects.json")["defects"]}
    for row in rows:
        for field in ("file", "symbol", "source", "type", "spec_ref"):
            assert field in row, (row["id"], field)
        assert row["file"] == source[row["id"]]["file"]
        assert row["symbol"] == source[row["id"]]["symbol"]

    # And the operator-readable half carries them too — the markdown is the
    # document the receiving lead actually opens.
    markdown = _markdown(report_env)
    backlog = markdown.split("## LATENT backlog", 1)[1].split("\n## ", 1)[0]
    for row in rows:
        assert str(row["file"]) in backlog, row["id"]


def test_an_unlocated_latent_row_says_the_filing_carried_no_location(report_env):
    """FR-005 (Locked) and the reversal recorded in `state.json`'s
    `spec_ambiguities`: 'the LATENT backlog renders an unlocated row honestly
    (stating the filing carried no file) and its header does not promise what
    the door does not require.'

    D-103 is D-089's class drawn from the other side. D-089 saw a location-free
    row under a header reading 'Each row names where the work is', and the
    GRIND-5 remedy added a `file_path` rung to both filing doors. TEST-01 then
    showed that rung contradicts FR-005, CT-001 and CT-003 — the server refuses
    a LATENT filing for a missing tier, a missing or placeholder reproduction
    statement, a missing class or a security-property claim, and never for a
    missing location — so the ruling was REVERSED and the rung came out. The
    header that motivated it was the survivor: it went on promising a location
    the protocol does not collect, above rows whose cells were blank and a
    `report.json` rendering `file: null` with no statement anywhere."""
    defects = _read_json(report_env, "defects.json")
    for record in defects["defects"]:
        if record["id"] == "D-004":
            record["file"] = None
            record["symbol"] = ""
    _write_json(report_env, "defects.json", defects)
    _generate(report_env)

    rows = {d["id"]: d for d in _document(report_env)["latent_backlog"]["defects"]}
    assert rows["D-004"]["located"] is False
    assert rows["D-004"]["file"] is None and rows["D-004"]["symbol"] is None
    assert rows["D-004"]["location_note"] == "the filing carried no file and no symbol"
    # The located row beside it renders exactly as it did before.
    assert rows["D-005"]["located"] is True
    assert rows["D-005"]["location_note"] is None
    assert rows["D-005"]["file"] == "src/foundry_mcp/tools/foundry_report.py"

    backlog = _markdown(report_env).split("## LATENT backlog", 1)[1]
    backlog = backlog.split("\n## ", 1)[0]
    header, table = backlog.split("| ID |", 1)
    assert "names where the work is" not in header, header
    assert "FR-005" in header and fr.NO_LOCATION_CELL in header, header
    unlocated = next(
        line for line in table.splitlines() if line.startswith("| D-004 |")
    )
    assert unlocated.count(fr.NO_LOCATION_CELL) == 2, unlocated
    assert "|  |" not in unlocated, unlocated


def test_a_latent_row_missing_only_its_symbol_names_only_that(report_env):
    """The statement is about what the filing ACTUALLY lacked. 'No file, real
    symbol' and 'no file, no symbol' are different facts and a single shrug
    would lose the difference — the same reason the unknown-tier section is
    kept apart from the LIVE rows rather than folded in (FR-051)."""
    defects = _read_json(report_env, "defects.json")
    for record in defects["defects"]:
        if record["id"] == "D-004":
            record["symbol"] = None
    _write_json(report_env, "defects.json", defects)
    _generate(report_env)

    row = next(
        d for d in _document(report_env)["latent_backlog"]["defects"]
        if d["id"] == "D-004"
    )
    assert row["located"] is True, "the file is still there"
    assert row["location_note"] == "the filing carried no symbol"


def test_the_baseline_comparison_prints_nfr_001s_four_metrics(report_env, tmp_path):
    """D-037 / NFR-001 verbatim: 'The report prints both runs side by side
    (cycles, defects by tier, tokens, wall clock).'

    Four metrics, both columns. Three of them had a value for the current run
    only, so the comparison the requirement names could not be read off the
    report at all. The baseline archive is planted beside this run — which is
    where a real `foundry-archive/` keeps it — and every column it can supply
    is derived by the SAME function that derives this run's."""
    baseline_dir = report_env.parent / THUNDER_VIPER_BASELINE["run"]
    baseline_dir.mkdir()
    _write_json(baseline_dir, "state.json", {"phase": "F6", "cycle": 0})
    _write_json(baseline_dir, "defects.json", {"defects": [
        {"id": "D-001", "cycle": 21, "status": "fixed"},      # no tier: unknown
        {"id": "D-002", "cycle": 3, "status": "open", "tier": "LIVE"},
    ]})
    (baseline_dir / "handoffs.jsonl").write_text(
        '{"timestamp": "2026-08-01T00:00:00+00:00", "event": "start"}\n'
        '{"timestamp": "2026-08-01T02:30:00+00:00", "event": "done"}\n',
        encoding="utf-8",
    )

    _generate(report_env)
    section = _document(report_env)["baseline_comparison"]
    metrics = section["baseline_metrics"]
    current = section["current"]

    for key in ("grind_cycles", "post_verification_cycles", "defects_by_tier",
                "tokens", "wall_clock_minutes"):
        assert key in metrics, key
        assert key in current, key

    # Cycles: the recorded constant IS the baseline column (D-085 — it used to
    # be only a floor, which is one-sided; see the test below). Its counter
    # stayed at 0 and its ledger proves 21, but 22 is what it is on record as
    # having executed, and a derivation is published beside it rather than
    # over it.
    assert metrics["grind_cycles"] == THUNDER_VIPER_BASELINE["grind_cycles"]
    assert metrics["post_verification_cycles"] == (
        THUNDER_VIPER_BASELINE["post_verification_cycles"]
    )
    assert section["baseline_derived"]["grind_cycles"] == 22, (
        "what the planted archive itself yields, published beside the constant"
    )
    # Defects by tier and wall clock come off the archive itself.
    # Every member of DEFECT_TIER_OR_UNKNOWN is present including zeros — "0
    # HARDENING defects" is a measurement and an absent key is not. The set is
    # derived, so FR-014's third tier joined this table with no edit here
    # beyond the expected value.
    assert metrics["defects_by_tier"] == {
        "HARDENING": 0, "LATENT": 0, "LIVE": 1, TIER_UNKNOWN: 1,
    }
    assert set(metrics["defects_by_tier"]) == set(DEFECT_TIER_OR_UNKNOWN)
    assert metrics["wall_clock_minutes"] == 150.0
    # It wrote no spend ledger, so tokens is null — never a fabricated 0, which
    # would read as "that run cost nothing".
    assert metrics["tokens"] is None

    # Every metric appears in the operator-readable table.
    table = _markdown(report_env).split("## Baseline comparison", 1)[1]
    for label in ("GRIND cycles", "Post-verification cycles", "Defects by tier",
                  "Tokens", "Wall clock"):
        assert label in table, label


def test_a_derivation_that_exceeds_the_recorded_baseline_never_replaces_it(
    report_env,
):
    """D-085 — a FLOOR does not protect the constant it names.

    The baseline cell was `max(derived, recorded)`, which is one-sided in
    exactly the direction that breaks it: a derivation coming in UNDER 22 loses
    and a derivation coming in OVER 22 silently REPLACES the constant. Driven
    when D-084's migration bug moved thunder-viper's counter: the GRIND-cycles
    baseline cell rendered 23 while the footnote under the same table still
    read "cycles from vocab.THUNDER_VIPER_BASELINE". A table that contradicts
    its own footnote is worse than either number alone.

    The over-deriving archive is planted directly here rather than reached by
    re-running the migration, so this test holds whatever migrate-archive.py
    later does — the property is about THIS module's arithmetic.
    """
    baseline_dir = report_env.parent / THUNDER_VIPER_BASELINE["run"]
    baseline_dir.mkdir()
    _write_json(baseline_dir, "state.json", {"phase": "F6", "cycle": 30})
    _write_json(baseline_dir, "defects.json", {"defects": []})

    _generate(report_env)
    section = _document(report_env)["baseline_comparison"]

    assert section["baseline_derived"]["grind_cycles"] == 31
    assert section["baseline_metrics"]["grind_cycles"] == (
        THUNDER_VIPER_BASELINE["grind_cycles"]
    ), "a derivation that EXCEEDS the constant must not become the baseline"
    # And the footnote no longer credits a number the table does not show: it
    # names both, and says which one stands.
    note = section["baseline_note"]
    assert "vocab.THUNDER_VIPER_BASELINE" in note
    assert "DERIVES grind_cycles 31" in note, note
    assert "The recorded constant stands" in note, note

    # The markdown carries them as SEPARATE columns, so neither can be read as
    # the other.
    table = _markdown(report_env).split("## Baseline comparison", 1)[1]
    assert "Baseline (thunder-viper, recorded)" in table
    assert "Baseline (derived)" in table
    row = next(ln for ln in table.splitlines() if ln.startswith("| GRIND cycles |"))
    assert "| 22 | 31 |" in row, row


def test_an_absent_baseline_archive_is_null_columns_not_fabricated_ones(report_env):
    """D-037's honest-null half. `foundry-archive/` is git-ignored and a
    checkout will usually not have thunder-viper's archive at all, so the three
    derived columns must be null with the reason stated — not zeros, and not a
    refusal."""
    _generate(report_env)
    section = _document(report_env)["baseline_comparison"]
    metrics = section["baseline_metrics"]
    assert metrics["defects_by_tier"] is None
    assert metrics["tokens"] is None
    assert metrics["wall_clock_minutes"] is None
    assert "null rather than fabricated" in section["baseline_note"]
    # The two recorded cycle numbers still print — they come from vocab.
    assert metrics["grind_cycles"] == THUNDER_VIPER_BASELINE["grind_cycles"]


def test_the_note_says_the_archive_was_read_when_the_archive_was_read(tmp_path):
    """OT-030 verbatim: 'measure-run.py compares any archive with
    thunder-viper.' D-135 — THE NOTE DESCRIBED A READ THAT DID NOT HAPPEN, AND
    DENIED ONE THAT DID.

    `present` was `is_dir() and resolve() != run_dir.resolve()` — one flag over
    TWO different reasons not to derive — and the note hung off its false
    branch alone. So running OT-030's own invocation against the baseline
    itself (`measure-run.py foundry-archive/thunder-viper`, which imports this
    very function) printed the archive's real numbers in the Current column
    and, underneath them, "That archive is not present here ... and nothing was
    derived".

    Both branches are driven here against archives on disk, because the claim
    under test is about what the generator DID with a directory, and a mocked
    directory cannot be read.
    """
    archive = tmp_path / "foundry-archive"

    # (a) SELF: the run IS thunder-viper. The archive is present and is read —
    #     it supplies the Current column — but a run is never its own baseline.
    own = archive / THUNDER_VIPER_BASELINE["run"]
    own.mkdir(parents=True)
    _write_json(own, "state.json", {"phase": "F6", "cycle": 4})
    _write_json(own, "defects.json", {"defects": [{"id": "D-1", "tier": "LIVE"}]})

    assert generate_report(tmp_path, own)["ok"] is True
    section = _document(own)["baseline_comparison"]

    note = section["baseline_note"]
    assert "not present here" not in note, (
        "the archive IS present and was read; D-135 is exactly this sentence"
    )
    assert "This run IS thunder-viper" in note, note
    assert "never its own baseline" in note, note
    # The read it DID do is the Current column, off that same directory.
    assert section["current"]["run"] == THUNDER_VIPER_BASELINE["run"]
    assert section["current"]["defects_by_tier"]["LIVE"] == 1
    # ...and the three derived columns stay null rather than comparing the
    # archive with itself.
    assert section["baseline_derived"] is None
    assert section["baseline_metrics"]["defects_by_tier"] is None

    # (b) DERIVED: a different run, with thunder-viper beside it. Same code
    #     path, third outcome, and the note says the archive was read.
    other = archive / "some-later-run"
    other.mkdir()
    _write_json(other, "state.json", {"phase": "F6", "cycle": 2})
    _write_json(other, "defects.json", {"defects": []})

    assert generate_report(tmp_path, other)["ok"] is True
    derived = _document(other)["baseline_comparison"]

    assert "not present here" not in derived["baseline_note"]
    assert "This run IS" not in derived["baseline_note"]
    assert derived["baseline_derived"]["defects_by_tier"] == {
        tier: (1 if tier == "LIVE" else 0) for tier in sorted(DEFECT_TIER_OR_UNKNOWN)
    }


def test_a_baseline_the_archive_cannot_derive_is_not_reported_as_a_derivation(
    tmp_path,
):
    """D-151 — 'cannot derive' was reported as 'derives a different number'.

    NFR-001 / AC-036 / OT-030. This is D-135's class one branch over: D-135
    gave the `self` outcome its own sentence, and left the `derived` outcome
    holding a single `differs` dict keyed on `derived != recorded`. None
    satisfies that test against every recorded number, so an archive that
    CANNOT say what it did was reported as having said something:

        That archive currently DERIVES post_verification_cycles None,
        shown in the Derived column.

    while `_md_table` rendered that same None as an EMPTY Derived cell. The
    sentence named a column that showed nothing and asserted a derivation the
    archive never produced, with the Python repr of the absence standing in
    for the number. `_archive_metrics`' own docstring is the authority it
    broke: "Every metric is None when its ledger cannot supply it."

    thunder-viper is exactly that archive — it wrote no `inspect_modes` list,
    so its post-verification count is underivable — which is why this fired on
    OT-030's own comparison. Driven here against a real archive on disk,
    because the claim is about what the generator did with a directory.

    The real-number arm keeps its wording; the pin on it
    (`test_a_derivation_that_exceeds_the_recorded_baseline_never_replaces_it`)
    is what D-151 says was the only arm covered.
    """
    archive = tmp_path / "foundry-archive"

    # thunder-viper's real shape: a counter the defect ledger can reconstruct
    # a cycle count from, and NO `inspect_modes` list at all.
    baseline_dir = archive / THUNDER_VIPER_BASELINE["run"]
    baseline_dir.mkdir(parents=True)
    _write_json(baseline_dir, "state.json", {"phase": "F6", "cycle": 21})
    _write_json(baseline_dir, "defects.json", {"defects": []})

    run_dir = archive / "some-later-run"
    run_dir.mkdir()
    _write_json(run_dir, "state.json", {"phase": "F6", "cycle": 2})
    _write_json(run_dir, "defects.json", {"defects": []})

    assert generate_report(tmp_path, run_dir)["ok"] is True
    section = _document(run_dir)["baseline_comparison"]
    note = section["baseline_note"]

    # The archive really cannot supply this number, and really can supply the
    # other — so this fixture holds BOTH arms at once.
    assert section["baseline_derived"]["post_verification_cycles"] is None
    assert section["baseline_derived"]["grind_cycles"] == (
        THUNDER_VIPER_BASELINE["grind_cycles"]
    ), "21 + 1 = 22, so grind_cycles agrees and only the null arm speaks"

    assert "DERIVES post_verification_cycles None" not in note, note
    assert "None" not in note, f"no Python repr reaches operator prose: {note}"
    assert "CANNOT DERIVE post_verification_cycles" in note, note
    # ...and it says WHY, which is the fact the empty cell cannot carry.
    assert "recorded no inspect_modes list" in note, note
    assert "the Derived column is empty on that row" in note, note
    assert "not a measurement of zero" in note, note

    # THE SENTENCE AND THE COLUMN AGREE. The row it names is blank, and it is
    # the row it names — not the one above it, which does carry a derivation.
    table = _markdown(run_dir).split("## Baseline comparison", 1)[1]
    row = next(
        ln for ln in table.splitlines()
        if ln.startswith("| Post-verification cycles |")
    )
    # Metric | Baseline (recorded) | Baseline (derived) | Target | This run
    cells = [c.strip() for c in row.split("|")[1:-1]]
    assert cells[2] == "", f"the Derived cell the sentence names is empty: {row}"
    assert cells[1] == str(THUNDER_VIPER_BASELINE["post_verification_cycles"]), (
        "and the Baseline column still shows the recorded constant, which is "
        "what the sentence says it shows"
    )

    # The BOTH-ARMS case: an archive that derives one number differently AND
    # cannot derive the other gets one sentence for each, never one sentence
    # covering both.
    _write_json(baseline_dir, "state.json", {"phase": "F6", "cycle": 30})
    assert generate_report(tmp_path, run_dir)["ok"] is True
    both = _document(run_dir)["baseline_comparison"]["baseline_note"]
    assert "DERIVES grind_cycles 31" in both, both
    assert "CANNOT DERIVE post_verification_cycles" in both, both
    assert "The recorded constant stands" in both, both
    assert "None" not in both, both


def test_an_unmeasured_wall_clock_is_null_on_both_surfaces(report_env, tmp_path):
    """D-087 — the two surfaces of NFR-001's comparison printed different
    things for the same unmeasured run.

    `measure-run.py` emitted `wall_clock_seconds: 0.0` where this module
    emitted `null`, and this module's docstring forbids the other spelling by
    name: "a run that took no measurable time and a run nobody measured are
    different facts, and NFR-001's comparison is unreadable if they print the
    same". The CLI was fabricating exactly the number the report module refuses
    to fabricate, in the one table where the two sit side by side.

    Both now call `foundry_state.handoffs_wall_clock_seconds`, which is where
    the None rule lives. Asserted through the FUNCTION rather than by shelling
    out, and cross-checked against the CLI's own reader so the two cannot part
    again.
    """
    import importlib.util
    import sys

    from foundry_mcp.tools.foundry_state import handoffs_wall_clock_seconds

    unmeasured = tmp_path / "no-handoffs"
    unmeasured.mkdir()

    seconds, problem = handoffs_wall_clock_seconds(unmeasured)
    assert seconds is None and problem, (seconds, problem)
    assert fr._wall_clock_minutes(unmeasured) is None

    # The CLI's reader, loaded from the script itself (it is not importable as
    # a module name — the filename has a hyphen).
    script = (
        Path(fr.__file__).parents[4] / "scripts" / "measure-run.py"
    )
    assert script.is_file(), script
    spec = importlib.util.spec_from_file_location("_measure_run_d087", script)
    module = importlib.util.module_from_spec(spec)
    # measure-run.py defines a @dataclass, whose annotation resolution looks
    # the defining module up in sys.modules — register before exec_module.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)

    cli_seconds, tokens = module._read_handoffs_wall_clock(unmeasured)
    assert cli_seconds is None, "0.0 is the fabrication D-087 names"
    assert tokens == ["PHASE9_WALL_CLOCK_UNAVAILABLE"]

    # And on a run that CAN be measured, the two agree to the unit.
    measured, problem = handoffs_wall_clock_seconds(report_env)
    assert problem is None
    assert module._read_handoffs_wall_clock(report_env)[0] == measured
    assert fr._wall_clock_minutes(report_env) == round(measured / 60.0, 1)


def test_the_report_and_measure_run_derive_the_cycle_count_the_same_way(report_env):
    """D-036: two derivations of one fact that could disagree.

    `measure-run.py::_extract_per_run` published `final_index + 1` while this
    module published the raw `state.json["cycle"]`. They differ by exactly one,
    which is enough to straddle `CONVERGENCE_TARGET["grind_cycles"]`: a run at
    index 12 met the effort's own target on one surface and missed it on the
    other. There is one derivation now, in `foundry_state`, and this asserts
    the report publishes ITS answer rather than a second one."""
    from foundry_mcp.tools.foundry_state import derive_cycle_count

    _generate(report_env)
    current = _document(report_env)["baseline_comparison"]["current"]
    derived = derive_cycle_count(report_env)

    assert current["grind_cycles"] == derived["count"]
    assert derived["count"] == derived["index"] + 1
    # And the counter is not the only source: the fixture's roll-up and defect
    # ledger corroborate it, which is what rescues an archive whose counter
    # never moved.
    assert derived["sources"]["state_cycle"] == 5
    assert derived["sources"]["rollup_highest"] == 5
    assert derived["sources"]["defect_max_cycle"] == 5


def test_a_stale_counter_is_outvoted_by_the_ledgers(report_env):
    """D-022's mechanism, on the report side. thunder-viper's counter was
    written once as 0 and never incremented, so `state["cycle"]` alone reported
    a 22-cycle run as one cycle and the convergence surfaces certified it.

    The defect ledger stamps the cycle each filing was made in, so its highest
    is a floor on the cycles the run executed. Zero the counter here and the
    count must hold."""
    from foundry_mcp.tools.foundry_state import derive_cycle_count

    state = _read_json(report_env, "state.json")
    state["cycle"] = 0
    _write_json(report_env, "state.json", state)

    derived = derive_cycle_count(report_env)
    assert derived["sources"]["state_cycle"] == 0
    assert derived["count"] == 6, "the ledgers still prove six cycles"

    _generate(report_env)
    assert _document(report_env)["baseline_comparison"]["current"][
        "grind_cycles"
    ] == 6


def test_the_cycle_count_is_none_when_no_ledger_can_supply_one(tmp_path):
    """"Cannot say" and "one cycle" are different answers, and the surface that
    turns this into a target verdict has to tell them apart — `meets_target` is
    never True on a number nobody measured (D-022)."""
    from foundry_mcp.tools.foundry_state import derive_cycle_count

    empty = tmp_path / "no-ledgers"
    empty.mkdir()
    derived = derive_cycle_count(empty)
    assert derived["count"] is None
    assert derived["index"] is None
    assert derived["stale_counter"] is False
    assert derived["problems"] == []


def test_the_report_copies_the_baseline_dicts_rather_than_embedding_them(report_env):
    """The two comparison dicts are plain dicts, not MappingProxyType, because
    `report.json` is json.dumps'd wholesale and a mapping proxy is not
    serializable (casting 1 logged that exception in concerns.md and asked
    every consumer to copy). Embedding the module object would put a mutable
    global into the document, so this asserts the copy."""
    _generate(report_env)
    doc = _document(report_env)
    section = doc["baseline_comparison"]
    section["baseline"]["grind_cycles"] = -1
    assert THUNDER_VIPER_BASELINE["grind_cycles"] == 22, (
        "the report handed out a reference to the module-level constant"
    )
    result = fr._baseline_comparison_section(report_env, {"per_cycle": {}}, {})
    assert result["baseline"] is not THUNDER_VIPER_BASELINE
    assert result["target"] is not CONVERGENCE_TARGET


# --------------------------------------------------------------------------- #
# ST-008 — the HALTED run's report.
# --------------------------------------------------------------------------- #


def test_a_halted_run_report_names_every_open_live_and_latent_defect(report_env):
    """C-14 / ST-008: 'the report names every open LIVE and LATENT defect' —
    that is what makes the HALTED transition auditable.

    Driven by re-opening a LIVE defect and halting the run, because on the
    fixture as shipped every LIVE record is fixed and the assertion would be
    vacuous. The ids are named in the cross-tab on EVERY run, halted or not,
    which is why this needs no twelfth section."""
    state = _read_json(report_env, "state.json")
    state["phase"] = RUN_PHASE_HALTED
    state["halted_at_cycle"] = 5
    state["halted_reason"] = "max_cycles reached"
    state["max_cycles"] = 5
    _write_json(report_env, "state.json", state)

    defects = _read_json(report_env, "defects.json")
    for record in defects["defects"]:
        if record["id"] == "D-006":
            record["status"] = "open"
            record["fixed_in_cycle"] = None
    _write_json(report_env, "defects.json", defects)

    _generate(report_env)
    doc = _document(report_env)

    assert doc["run"]["halted"] is True
    assert doc["run"]["halted_at_cycle"] == 5
    assert doc["run"]["halted_reason"] == "max_cycles reached"

    cross = doc["defects_by_tier_and_status"]["cross_tab"]
    assert cross["LIVE"]["open"]["ids"] == ["D-006"]
    assert set(cross["LATENT"]["open"]["ids"]) == {"D-004", "D-005"}

    md = _markdown(report_env)
    for did in ("D-006", "D-004", "D-005"):
        assert did in md, did


def _halted_at_the_real_door(
    tmp_path, *, max_cycles: int, cycle: int, name: str = "halt-run"
) -> tuple:
    """Drive the transition that halts, and hand back what it wrote.

    Returns ``(state, report_json, report_md, result)``. The report is
    generated INSIDE the transition (`_halt_if_capped` calls the generator), so
    everything here is one call's output and the two documents cannot be
    reconciled after the fact.

    ``name`` is a parameter because a caller that drives twice under one
    ``tmp_path`` — the transcript, which prints one run and then asserts over
    another — needs two run directories; the second call would otherwise land
    in the first's archive.
    """
    # GI-010 / GI-026 — the wave-2 destinations. Casting 2's split defines
    # these symbols in the modules named below and its completion report's
    # `## Symbol map` is the authority; where the map and this differ, the
    # map wins and this is the edit.
    from foundry_mcp.tools.foundry_state import now_iso
    from foundry_mcp.tools.orchestration.transitions import (
        foundry_mark_phase_complete,
    )
    from foundry_mcp.tools import foundry_state

    run_dir = tmp_path / "foundry-archive" / name
    (run_dir / "castings").mkdir(parents=True)
    (run_dir / "spec.md").write_text("# Spec\n\n- **FR-1**: a thing\n",
                                     encoding="utf-8")
    _write_json(run_dir, "state.json", {
        "phase": "F2", "cycle": cycle, "max_cycles": max_cycles,
        "spec_path": str(run_dir / "spec.md"),
    })
    _write_json(run_dir, "verdicts.json", {
        "cycle": cycle,
        "requirements": [{"requirement_id": "FR-1", "verdict": "VERIFIED"}],
    })
    _write_json(run_dir, "defects.json", {"defects": [
        {"id": "D-001", "cycle": cycle, "tier": "LIVE", "class": "K",
         "status": "open", "source": "trace", "type": "UNWIRED",
         "description": "d", "spec_ref": "", "symbol": "", "file": "src/a.py",
         "fixed_in_cycle": None},
    ]})
    (run_dir / ".tasks-generated").write_text("x\n", encoding="utf-8")
    (run_dir / ".next-action-called").write_text(f"{now_iso()}\n",
                                                 encoding="utf-8")

    foundry_state.set_active_run(name)
    try:
        with _no_active_teams():
            result = foundry_mark_phase_complete("grind_start", str(tmp_path))
    finally:
        foundry_state.clear_active_run()
    return (_read_json(run_dir, "state.json"), _document(run_dir),
            _markdown(run_dir), result)


def test_a_halted_runs_report_states_the_grind_cycles_its_halt_reason_states(
    tmp_path,
):
    """D-175 / FR-045 / OT-026 — one transition wrote two numbers and they
    disagreed about the one quantity `--max-cycles` is defined over.

    `derive_cycle_count` published `index + 1` over the server counter, and
    that counter advances at `inspect_start`, so it counts INSPECTs. Driven end
    to end at the real doors with max_cycles 2: `Foundry-Phase('grind_start')`
    halted with the reason 'opening GRIND cycle 3 would exceed it' — so two
    GRIND cycles ran, which is `_halt_if_capped`'s own arithmetic — and the
    report it generated in the same call read `grind_cycles: 3` and rendered
    '| GRIND cycles | 22 | | 12 | 3 |' beside `run.max_cycles: 2`.

    The halt prevents exactly one GRIND, so the count is the index; both
    numbers now come out of one call and say 2."""
    state, doc, md, result = _halted_at_the_real_door(
        tmp_path, max_cycles=2, cycle=2
    )

    assert result["ok"] is True and result["halted"] is True, result
    assert state["phase"] == RUN_PHASE_HALTED
    # FR-019 landed the structured `{reason, text}` while this register was
    # written against the free f-string, and `in` on a mapping tests its KEYS —
    # so the old assertion did not merely go stale, it started asking a
    # question with no relation to the sentence it names. `halt_reason` is the
    # resolver both shapes go through; the text is where the sentence lives in
    # either, which is exactly what `_halt_and_co_dispatch_section` reads.
    _reason = state["halted_reason"]
    assert halt_reason(
        _reason.get("reason") if isinstance(_reason, dict) else _reason
    ) == "cap_reached", _reason
    assert "opening GRIND cycle 3 would exceed it" in (
        _reason["text"] if isinstance(_reason, dict) else _reason
    ), _reason
    # The counter is untouched by the halt: it is the INSPECT count, and the
    # GRIND that would have opened at counter + 1 is the one refused.
    assert state["cycle"] == 2 and state["halted_at_cycle"] == 2

    assert doc["run"]["max_cycles"] == 2
    assert doc["baseline_comparison"]["current"]["grind_cycles"] == 2, (
        "the report may not claim a GRIND cycle the halt reason says never "
        "opened"
    )
    row = next(ln for ln in md.splitlines() if ln.startswith("| GRIND cycles |"))
    assert row.rstrip().endswith("| 2 |"), row
    # The cap is a ceiling and the report now sits under it rather than over.
    assert doc["baseline_comparison"]["current"]["grind_cycles"] <= (
        doc["run"]["max_cycles"]
    )


def test_the_cycle_count_drops_only_the_grind_the_halt_prevented(tmp_path):
    """D-175 — the halt subtracts one GRIND, and nothing else about the
    derivation moves.

    Keyed on `halted_at_cycle` / `halted_reason`, the two DATA fields
    `_halt_if_capped` writes beside the phase, because `foundry_state` may not
    import `vocab` and re-typing `RUN_PHASE_HALTED` there is the hand-copied
    enum drift the house rule bans. A run with the same counter and no halt
    record still publishes index + 1, which is what keeps thunder-viper at 22
    and grand-vulture at 18."""
    from foundry_mcp.tools.foundry_state import derive_cycle_count

    run_dir = tmp_path / "foundry-archive" / "cycles"
    run_dir.mkdir(parents=True)
    _write_json(run_dir, "defects.json", {"defects": []})

    _write_json(run_dir, "state.json", {"phase": "F3", "cycle": 2})
    running = derive_cycle_count(run_dir)
    assert running["halted"] is False
    assert (running["index"], running["count"]) == (2, 3), running

    for halt in ({"halted_at_cycle": 2}, {"halted_reason": "max_cycles 2"}):
        _write_json(run_dir, "state.json",
                    {"phase": RUN_PHASE_HALTED, "cycle": 2, **halt})
        halted = derive_cycle_count(run_dir)
        assert halted["halted"] is True, halt
        assert halted["index"] == 2, "the counter itself is unchanged by a halt"
        assert halted["count"] == 2, halt

    # An empty reason is not a halt record — "" is what a writer leaves when it
    # has nothing to say, and dropping a real cycle on it would be worse than
    # the defect.
    _write_json(run_dir, "state.json",
                {"phase": "F3", "cycle": 2, "halted_reason": "  "})
    assert derive_cycle_count(run_dir)["count"] == 3


def test_the_cycle_rows_cover_only_agents_whose_dispatch_record_stamps_a_cycle(
    tmp_path,
):
    """D-172 — the unreported cycle axis is derived from the one source that
    stamps a cycle, and the prose beside it said otherwise.

    `cycles_of_agent` can only be built from `stream-rollup.json`, whose
    buckets ARE keyed by the server counter; `spawns.log` records a teammate
    dispatch with no cycle at all. Driven over the live archive the axis named
    ZERO teammates while the note claimed it 'names the cycles those agents
    were dispatched in' — false for 14 of 19 pairs. Driven here on the filed
    synthetic shape: three teammates dispatched, one reporting spend, so two
    unreported pairs that the phase axis carries and no cycle row can."""
    from foundry_mcp.tools.foundry_state import DISPATCH_PHASE_TO_RUN_PHASE

    cast = DISPATCH_PHASE_TO_RUN_PHASE["cast"]
    run_dir, doc, md = _demo_run(
        tmp_path, "d172",
        spawns=[{"timestamp": "t1", "casting_id": n, "phase": "cast"}
                for n in (1, 2, 3)],
        spend=[{"agent": "casting-1", "phase": cast, "cycle": 1,
                "tokens": 10, "duration_ms": 60_000}],
    )

    listed = doc["unreported_dispatches"]
    spend = doc["spend_per_phase_and_cycle"]
    assert listed["count"] == 2
    assert listed["by_phase"] == {cast: ["casting-2", "casting-3"]}
    # THE PHASE AXIS CARRIES THEM AND THE CYCLE AXIS CANNOT.
    assert spend["by_phase"][cast]["unreported"] == 2
    assert spend["by_cycle"]["1"]["unreported"] == 0
    assert spend["unreported_without_cycle"] == 2, (
        "both unreported pairs are teammate dispatches, which carry no cycle"
    )

    # The sentence states that scope rather than a coverage it does not have,
    # and carries the number, in BOTH documents.
    note = spend["note"]
    assert "appears in NO cycle row" in note
    assert "of the 2 unreported pairs here, 2 are in that position" in note
    assert "spawns.log stamps a teammate dispatch with a timestamp and no cycle" in note
    assert "names the cycles those agents were dispatched in" not in note, (
        "the sentence the filing quoted, which the archive never derived"
    )
    assert note in md, "the markdown carries the same statement as the JSON"


def test_a_stream_agent_is_the_one_thing_the_cycle_axis_does_carry(tmp_path):
    """D-172's other half: the axis is narrow, not empty, and the complement is
    exact.

    An F2 stream appears in `stream-rollup.json`'s cycle buckets, so it is
    stamped and reaches the cycle rows; the teammate beside it is not. A fix
    that emptied the axis rather than scoping it would lose the one real
    per-cycle measurement the archive can make."""
    from foundry_mcp.tools.foundry_state import DISPATCH_PHASE_TO_RUN_PHASE

    cast = DISPATCH_PHASE_TO_RUN_PHASE["cast"]
    run_dir = tmp_path / "foundry-archive" / "d172-stream"
    run_dir.mkdir(parents=True)
    _write_json(run_dir, "state.json", {"phase": "F3", "cycle": 2})
    _write_json(run_dir, "defects.json", {"defects": []})
    _write_json(run_dir, "verdicts.json", {"cycle": 2, "requirements": []})
    _write_json(run_dir, "stream-rollup.json", {"cycles": {
        "1": {"prove": {"records": 1}},
        "2": {"prove": {"records": 1}},
    }})
    (run_dir / "spawns.log").write_text(
        json.dumps({"timestamp": "t1", "casting_id": 4, "phase": "cast"}) + "\n",
        encoding="utf-8",
    )
    (run_dir / SPEND_LEDGER_FILENAME).write_text("", encoding="utf-8")
    assert generate_report(tmp_path, run_dir)["ok"] is True

    doc = _document(run_dir)
    spend = doc["spend_per_phase_and_cycle"]
    # One stream pair across two cycles, plus one teammate pair with no cycle.
    assert doc["unreported_dispatches"]["count"] == 2
    assert spend["by_cycle"]["1"]["unreported"] == 1
    assert spend["by_cycle"]["2"]["unreported"] == 1
    assert spend["by_phase"][cast]["unreported"] == 1
    assert spend["unreported_without_cycle"] == 1
    # The identity the note promises: every pair is either stamped or named as
    # unstamped, and the cycle column still does not add up to the total.
    assert spend["total"]["unreported"] == 2
    assert sum(v["unreported"] for v in spend["by_cycle"].values()) == 2


# --------------------------------------------------------------------------- #
# CT-014 / OT-025 — `artifacts.report_document_status`, the read the DONE gate makes.
# --------------------------------------------------------------------------- #


def test_report_status_reports_absent_before_generation(report_env):
    """OT-025 verbatim: 'Foundry-Phase done without a generated report is
    refused naming the report; after Foundry-Report it succeeds and report.json
    contains every named section.'

    This casting owns `artifacts.report_document_status`, which that refusal reads. Before
    generation EVERY section is missing — the honest answer, since no section
    can be shown to be there."""
    status = report_document_status(report_env)
    assert status["present"] is False
    assert status["missing_sections"] == list(REPORT_REQUIRED_SECTIONS)
    assert REPORT_JSON_FILENAME in status["problem"]

    _generate(report_env)
    after = report_document_status(report_env)
    assert after["present"] is True
    assert after["missing_sections"] == []
    assert after["generated_at"]


def test_report_status_names_the_sections_that_were_removed(report_env):
    """GI-006 verbatim: 'The lead may append prose but cannot omit a section;
    `Foundry-Phase('done')` refuses if the report is absent.'

    The omission half, driven: two sections deleted out of a generated
    report.json are named back, so casting 3's refusal can say which."""
    _generate(report_env)
    doc = _document(report_env)
    del doc["latent_backlog"]
    del doc["lead_fix_records"]
    _write_json(report_env, REPORT_JSON_FILENAME, doc)

    status = report_document_status(report_env)
    assert status["present"] is False
    assert status["missing_sections"] == ["latent_backlog", "lead_fix_records"]


def test_appended_prose_never_makes_a_section_look_missing(report_env):
    """GI-006's first half: 'The lead may append prose.'

    The markdown IS read (D-015), so this is the half that has to keep
    working: a lead's own `## ` heading appended below the generated ones is
    permitted and must not register as a section, present or missing. Whole
    heading LINES are matched, never prefixes, which is what lets the two
    coexist."""
    _generate(report_env)
    md_path = report_env / REPORT_MD_FILENAME
    md_path.write_text(
        md_path.read_text(encoding="utf-8")
        + "\n## Lead's postscript\n\nThe cycle-4 verifier touch was mine.\n",
        encoding="utf-8",
    )
    status = report_document_status(report_env)
    assert status["present"] is True
    assert status["missing_sections"] == []


def test_the_done_gate_opens_report_md_and_not_only_the_json(report_env):
    """D-015 / GI-006's violation column, verbatim: 'a run reaching DONE with a
    lead-authored REPORT.md that lacks the generated sections, or no report at
    all.'

    Driven the simplest way there is — delete REPORT.md. The gate read
    `report.json` alone, so the run reached DONE with no operator-readable
    report in the archive at all, which is the second disjunct of that clause
    word for word. REPORT.md is the document a human reads; the JSON exists for
    tools, and a gate that checks only the tools' copy is not checking the
    thing GI-006 names."""
    _generate(report_env)
    assert report_document_status(report_env)["present"] is True

    (report_env / REPORT_MD_FILENAME).unlink()
    status = report_document_status(report_env)
    assert status["present"] is False
    assert status["missing_sections"] == list(REPORT_REQUIRED_SECTIONS)
    assert status["missing_from_json"] == [], (
        "the JSON is intact — it is REPORT.md's absence that must close the gate"
    )
    assert REPORT_MD_FILENAME in status["problem"]


def test_a_section_heading_deleted_from_report_md_is_named_back(report_env):
    """D-015 / GI-006's first disjunct: a REPORT.md that LACKS a generated
    section, with the JSON left complete.

    A lead who edits the markdown and drops a heading is exactly the case
    GI-006's 'cannot omit a section' addresses, and the omission has to be
    named — casting 3's refusal reads `missing_sections` to say which."""
    _generate(report_env)
    md_path = report_env / REPORT_MD_FILENAME
    md_path.write_text(
        md_path.read_text(encoding="utf-8").replace("## LATENT backlog", "", 1),
        encoding="utf-8",
    )
    status = report_document_status(report_env)
    assert status["present"] is False
    assert status["missing_sections"] == ["latent_backlog"]
    assert status["missing_from_markdown"] == ["latent_backlog"]
    assert status["missing_from_json"] == []


def test_the_renderers_docstring_describes_the_read_that_actually_happens(report_env):
    """GI-006 verbatim: 'The lead may append prose but cannot omit a section.'

    D-141 — STALE PROSE SURVIVING BESIDE NEW PROSE. `_render_markdown`'s
    docstring read "`artifacts.report_document_status` reads the JSON, not the markdown, so
    appended prose can never make a section look missing". D-015 moved the read
    onto BOTH documents and rewrote `artifacts.report_document_status`'s own docstring to say so;
    this sentence was left describing the retired read, one function away.

    So each half of the replacement prose is DRIVEN here rather than grepped
    for, and the retired sentence is asserted gone. A docstring nobody can
    falsify is how the first one survived.
    """
    import inspect as _inspect

    doc = _inspect.getdoc(fr._render_markdown) or ""

    # 1. The markdown IS read: delete it and the gate says so.
    _generate(report_env)
    (report_env / REPORT_MD_FILENAME).unlink()
    gone = report_document_status(report_env)
    assert gone["present"] is False
    assert len(gone["missing_from_markdown"]) == len(REPORT_REQUIRED_SECTIONS)
    assert gone["problem"] == f"{REPORT_MD_FILENAME} does not exist"
    assert gone["missing_from_json"] == [], (
        "the JSON is intact — the refusal came from the markdown alone, which "
        "is the half the retired sentence said was never consulted"
    )

    # 2. Appended prose is harmless BECAUSE the match is a whole line anywhere,
    #    not because the document is unread.
    _generate(report_env)
    md_path = report_env / REPORT_MD_FILENAME
    md_path.write_text(
        "## Lead's appendix\n\nprose the lead added below the report.\n"
        + md_path.read_text(encoding="utf-8")
        + "\n## Another appendix\n\nand more.\n",
        encoding="utf-8",
    )
    assert report_document_status(report_env)["present"] is True

    # 3. ...and a heading with a suffix bolted on reads as the edit it is.
    md_path.write_text(
        md_path.read_text(encoding="utf-8").replace(
            "## LATENT backlog", "## LATENT backlog (see below)", 1
        ),
        encoding="utf-8",
    )
    assert report_document_status(report_env)["missing_sections"] == ["latent_backlog"]

    # 4. The retired claim is not still sitting in the docstring beside the new
    #    one. Both halves of it, because either alone misleads.
    assert "reads the JSON, not the markdown" not in doc, doc
    assert "can never make a section look missing" not in doc, doc


def test_a_failed_markdown_write_leaves_no_json_for_the_gate_to_pass(report_env):
    """D-015's third aggravator: the write ORDER decides which document's
    absence holds the gate.

    `generate_report` wrote `report.json` first, so an OSError on the markdown
    left a complete JSON behind and a satisfied gate — a half-written report
    that opens DONE. Driven by making the markdown path unwritable: the call
    must refuse AND leave no `report.json` for a later `artifacts.report_document_status` to pass
    on."""
    md_path = report_env / REPORT_MD_FILENAME
    md_path.mkdir()          # a directory occupying the name: write_text raises OSError

    result = generate_report(report_env.parent.parent, report_env)
    assert result["ok"] is False, result
    assert REPORT_MD_FILENAME in result["error"] or str(report_env) in result["error"]
    assert not (report_env / REPORT_JSON_FILENAME).exists(), (
        "the JSON was written before the markdown failed, so the DONE gate "
        "would open on a report whose readable half does not exist"
    )
    assert report_document_status(report_env)["present"] is False


def test_report_status_on_a_corrupt_report_names_the_problem(report_env):
    """A `report.json` that exists and will not decode is not the same as one
    that was never generated, and the DONE refusal has to be able to say which.
    Both report `present: False`; only this one carries a `problem`."""
    (report_env / REPORT_JSON_FILENAME).write_bytes(b"\xff\xfe not json at all")
    status = report_document_status(report_env)
    assert status["present"] is False
    assert status["missing_sections"] == list(REPORT_REQUIRED_SECTIONS)
    assert REPORT_JSON_FILENAME in status["problem"]
    assert "could not be read" in status["problem"]


# --------------------------------------------------------------------------- #
# The refusal contract: absent is not unreadable.
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "ledger",
    ["defects.json", "verdicts.json", "escalation.json", "state.json",
     "handoffs.jsonl", SPEND_LEDGER_FILENAME, "spawns.log", "stream-rollup.json"],
)
def test_an_unreadable_ledger_is_a_named_refusal_never_a_raise(report_env, ledger):
    """CT-014: `generate_report` 'refuses — never raises — naming the ledger it
    could not read.'

    Parametrized over every ledger the generator opens, because a guard added
    to seven reads and not the eighth is the shape this class keeps recurring
    as. Driven with real undecodable bytes rather than asserted from the
    source: the property is 'does not raise', and only running it shows that."""
    (report_env / ledger).write_bytes(b"\xff\xfe stray continuation \x80\x81\n")
    result = generate_report(report_env.parent.parent, report_env)
    assert result["ok"] is False, result
    assert ledger in result["error"], result
    assert "could not be read" in result["error"], result
    assert result["hint"]
    assert not (report_env / REPORT_JSON_FILENAME).exists(), (
        "a report generated around a corrupt ledger would be worse than none: "
        "it would look complete"
    )


@pytest.mark.parametrize(
    "ledger",
    ["defects.json", "verdicts.json", "escalation.json", "handoffs.jsonl",
     SPEND_LEDGER_FILENAME, "spawns.log", "stream-rollup.json"],
)
def test_an_absent_ledger_is_an_empty_section_not_a_refusal(report_env, ledger):
    """The other half of the same distinction, and the reason it matters: a run
    that never called Foundry-Spend has no spend.jsonl, and refusing its report
    would make `Foundry-Phase('done')` structurally unreachable for it.

    `state.json` is excluded because a run directory without one is not a run.
    """
    (report_env / ledger).unlink()
    result = generate_report(report_env.parent.parent, report_env)
    assert result["ok"] is True, result
    assert report_document_status(report_env)["present"] is True


def test_generate_report_refuses_a_run_directory_that_is_not_there(tmp_path):
    """The precondition rung. A missing run directory is named, not created —
    generating a report for a run that does not exist would invent one."""
    missing = tmp_path / "foundry-archive" / "no-such-run"
    result = generate_report(tmp_path, missing)
    assert result["ok"] is False
    assert str(missing) in result["error"]
    assert result["hint"]


def test_a_torn_final_line_costs_only_that_line(report_env):
    """The JSONL discipline `foundry_state.read_jsonl` owns: these ledgers are
    appended by many concurrent agents under an flock, so a torn final line is
    an ordinary crash artifact. Failing the read over it would cost the other
    four records, which is a strictly worse answer than reporting four of five.
    """
    path = report_env / SPEND_LEDGER_FILENAME
    path.write_text(
        path.read_text(encoding="utf-8") + '{"agent": "casting-9", "phase": "gr',
        encoding="utf-8",
    )
    _generate(report_env)
    assert _document(report_env)["spend_per_phase_and_cycle"]["records"] == 5


# --------------------------------------------------------------------------- #
# The import-graph contract this module's existence depends on.
# --------------------------------------------------------------------------- #


def test_foundry_report_imports_only_the_two_leaf_modules():
    """`foundry_orchestrator` imports this module (the Foundry-Report tool and
    the DONE transition both call into it) and `foundry_spawn` imports
    `foundry_orchestrator`. An import of anything but `schemas.vocab` and
    `tools.foundry_state` from here therefore risks closing a cycle in the
    import graph — the same cycle `foundry_state`'s leaf-module contract exists
    to keep open.

    Asserted on the SOURCE rather than on `sys.modules`, and split by DEPTH.

    D-013 sharpened what this test is for. It used to demand that no
    `foundry_mcp` name appear anywhere in the file outside the two leaf
    modules, function bodies included, on the grounds that a body-level import
    is "exactly as dangerous". That is what forced `_agent_id_for_casting` to
    be a hand-typed copy of `foundry_spawn`'s spelling — and the copy then
    disagreed with `foundry_orchestrator._dispatched_agents` in production,
    which is a live defect traded for a cycle that cannot actually form.

    A body-level import runs at CALL time, when every module in the chain is
    already built, so it closes nothing. The real rule is about MODULE level,
    and that is what is asserted here — plus, below, that the one body-level
    import really is body-level and really does work from a cold interpreter."""
    import ast

    source = Path(fr.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)

    def _imported(node: ast.AST) -> set[str]:
        names: set[str] = set()
        if isinstance(node, ast.ImportFrom) and (node.module or "").startswith(
            "foundry_mcp"
        ):
            names.add(node.module)
        elif isinstance(node, ast.Import):
            names |= {a.name for a in node.names if a.name.startswith("foundry_mcp")}
        return names

    module_level: set[str] = set()
    for node in tree.body:
        module_level |= _imported(node)
    # fallout D-034 / concern C-014 — THE ROSTER GREW, AND THE PROPERTY IT
    # STOOD IN FOR IS NOW ASSERTED DIRECTLY.
    #
    # The two leaves were the whole allowlist because `foundry_orchestrator`
    # imported this module, so a third module-level edge could close a cycle.
    # That premise died with the orchestrator, and an allowlist outliving its
    # premise is a rule nobody can evaluate: it says "these two" and cannot say
    # why. `escalation.py` WRITES the artifact whose name this module reads and
    # is a lifecycle module like this one; `artifacts.py` is a LEAF, which the
    # layering lets anything reach unconditionally. `_KNOWN_DUPLICATION`
    # carried both re-typed literals as named debt.
    #
    # `stream-rollup.json`'s name moved from its writer to the artifact leaf
    # under concern C-019, beside the `*_MARKER` names — a run-artifact
    # filename is the run LAYOUT's property, not the writing module's — so it
    # is read from there and survives the writer's declaration going away.
    #
    # So the roster is stated AND the acyclicity is computed below. A future
    # edge that really would close a cycle fails on the computation, not on
    # somebody remembering to keep the roster short.
    assert module_level == {
        "foundry_mcp.schemas.vocab",
        "foundry_mcp.tools.artifacts",
        "foundry_mcp.tools.foundry_state",
        "foundry_mcp.tools.orchestration.escalation",
    }, sorted(module_level)

    # THE PROPERTY: nothing imports this module at module level, so nothing
    # this module imports at module level can reach back to it.
    import foundry_mcp

    pkg = Path(foundry_mcp.__file__).resolve().parent
    importers = []
    for module in sorted(pkg.rglob("*.py")):
        if "__pycache__" in module.parts or module.name == "foundry_report.py":
            continue
        for node in ast.parse(module.read_text(encoding="utf-8")).body:
            if "foundry_mcp.tools.foundry_report" in _imported(node):
                importers.append(module.name)
    assert importers == [], (
        f"{importers} import(s) foundry_report at MODULE level. The roster "
        f"above is safe only while nothing does: with an importer, a "
        f"module-level import here can close a cycle and the extra edges must "
        f"go back to the body-level seam."
    )
    # The walk must SEE something, or the emptiness above proves nothing.
    assert len(list(pkg.rglob("*.py"))) >= 15

    nested: set[str] = set()
    for node in ast.walk(tree):
        if node in tree.body:
            continue
        nested |= _imported(node)
    # GI-024 — TWO OF THE FOUR BODY-LEVEL IMPORTS ARE GONE, AND THAT IS THE
    # POINT OF THE CONSOLIDATION.
    #
    # `foundry_mcp.tools` (`from foundry_mcp.tools import foundry_orchestrator`,
    # the D-047/D-048 read of `DISPATCH_PHASE_TO_RUN_PHASE`) and
    # `foundry_mcp.tools.foundry_orchestrator` (D-232/D-235's read of the
    # overlay, now `foundry_state.overlay_unreported`) were both reaches BACK
    # INTO the module that imports this one. `survey/architecture.md` §3.2
    # names that trade for what it was: the previous fix for the spend
    # duplication "was to import [the overlay] back out of the orchestrator,
    # closing the import cycle rather than sharing the rule". Both symbols
    # live in
    # `foundry_state` now, so the rule is genuinely shared and this module
    # reaches only the two leaves its own header promises.
    #
    # `foundry_mcp.tools.foundry_spawn` (D-013's agent-id spelling) and
    # `foundry_mcp.tools.foundry_handoff` (D-078's MEASUREMENT_UNAVAILABLE and
    # `_numstat_count`) stay, and stay body-level. Neither is a derived TABLE:
    # each reads a spelling from the module that WRITES the record, which is
    # the trade D-013 ruled on and which no amount of consolidation removes —
    # the writer is the owner.
    #
    # fallout D-039 — `foundry_mcp.tools.foundry_validate` joins them on the
    # same ground. AC-044 puts ONE span table on two surfaces, and the surface
    # that computes it is the gate that refuses on it; a copy here would be a
    # second answer to "who owns this requirement", available to disagree with
    # the answer F0.9 passed or refused on. Body-level for this test's own
    # stated reason and no other: `foundry_validate` imports `foundry_spawn`,
    # which is already one of the two deferred reaches below.
    assert nested == {
        "foundry_mcp.tools.foundry_handoff",
        "foundry_mcp.tools.foundry_spawn",
        "foundry_mcp.tools.foundry_validate",
    }, sorted(nested)

    # And the deletion is asserted directly, not only as a set difference: a
    # module-path string reappearing anywhere in the source is the facade
    # GI-010 forbids, whether it is imported at module level or inside a body.
    assert "import _overlay_unreported" not in source, (
        "the overlay is `foundry_state.overlay_unreported` now; a back-import "
        "here is the import cycle §3.2 filed, wearing the fix's clothes"
    )
    assert 'DISPATCH_PHASE_TO_RUN_PHASE", {}' not in source, (
        "the `getattr(..., {})` degradation went with the back-import: a "
        "constant behind a default is a constant that can silently go missing"
    )


def test_the_lazy_spawn_import_works_from_a_cold_interpreter():
    """D-013's proof that the deferred import closes no cycle.

    A fresh interpreter imports `foundry_report` FIRST — the direction that
    would deadlock if the import were at module level, since `foundry_spawn`
    imports `foundry_orchestrator` which imports this module — and then calls
    the helper, which is what triggers the deferred import. Run in a
    subprocess because an in-process assertion proves nothing once the whole
    package is already in `sys.modules`."""
    import subprocess
    import sys

    program = (
        "from foundry_mcp.tools import foundry_report as fr;"
        "from foundry_mcp.tools.foundry_spawn import _agent_id_for_casting as s;"
        "print(fr._agent_id_for_casting(7) == s(7))"
    )
    proc = subprocess.run(
        [sys.executable, "-c", program],
        capture_output=True, text=True,
        cwd=str(Path(fr.__file__).parents[2]),   # src/, where foundry_mcp lives
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "True", (proc.stdout, proc.stderr)


def test_foundry_state_still_imports_nothing_from_its_own_package():
    """`foundry_state` is the package's leaf module, imported by both
    `foundry.py` and `foundry_orchestrator.py`, and its stated contract is
    absolute: json and pathlib at MODULE level, and nothing from its own
    package at any level.

    `read_jsonl` was added there by this casting and
    `handoffs_wall_clock_seconds` by D-087, and this is the pin that both
    additions held the contract — a reader that reached for a vocab constant
    would close the cycle the whole module exists to keep open.

    SPLIT BY DEPTH, for the reason the report module's own pin above is split
    by depth (D-013). The module-level list is what `foundry.py` and
    `foundry_orchestrator.py` pay on import and what `measure-run.py`'s
    stdlib-only contract quotes, so it stays exactly json + pathlib.
    `handoffs_wall_clock_seconds` needs `datetime` to parse an ISO-8601 stamp,
    and it takes it at CALL time: that adds nothing to the module-level list,
    while the alternative — a hand-rolled timestamp parser to avoid the import
    — would be a third parser of one format in the module written to end
    second derivations. What is forbidden at EVERY depth is `foundry_mcp`,
    because that is the import the cycle is actually made of.

    fallout GI-033 / D-021 / D-035 (concern C-027) — `subprocess` AND `re` JOIN
    THE CALL-DEPTH LIST, BY RULING. The layering guard made the tmux pane scan
    leaf material: it is the second half of "is a team still holding the tree",
    the gates and transitions that ask are VERIFIER and the module that owned
    the scan is LIFECYCLE, so it could be reached from only one of the two
    layers wherever it sat inside `orchestration/`. The lead's ruling on C-027
    is explicit that "a readers-only leaf that lists panes read-only is still a
    reader" and that the json-and-pathlib phrasing was a CASTING CONVENTION
    rather than the spec — GI-033 names the leaf SET, never a leaf's import
    list. So the contract narrows to what it was always protecting: the
    module-level list stays exactly json + pathlib, because that is what
    `foundry.py` pays on import and what `measure-run.py`'s stdlib-only
    contract quotes; the call-depth list stays STDLIB-ONLY; and `foundry_mcp`
    stays forbidden at every depth. A `subprocess` here reads the machine and
    writes nothing — `_kill_panes` stayed in the lifecycle module — which is
    the line that keeps this a reader."""
    import ast

    from foundry_mcp.tools import foundry_state

    source = Path(foundry_state.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)

    def _imported(node: ast.AST) -> set[str]:
        if isinstance(node, ast.ImportFrom):
            return {node.module or ""}
        if isinstance(node, ast.Import):
            return {alias.name for alias in node.names}
        return set()

    module_level: set[str] = set()
    for node in tree.body:
        module_level |= _imported(node)
    assert module_level == {"__future__", "json", "pathlib"}, sorted(module_level)

    everywhere: set[str] = set()
    for node in ast.walk(tree):
        everywhere |= _imported(node)
    assert everywhere == {
        "__future__", "json", "pathlib", "datetime", "re", "subprocess",
    }, sorted(everywhere)

    # The clause the whole pin is for, and the one no ruling relaxes.
    assert not {m for m in everywhere if m.startswith("foundry_mcp")}, (
        "the leaf module reached back into its own package"
    )
    # STDLIB ONLY, at every depth. NFR-009 forbids a new dependency and this
    # module is the one `measure-run.py` reads with no package installed at
    # all, so a third-party import here breaks the census as well as the leaf.
    assert not {m for m in everywhere if m.split(".")[0] not in sys.stdlib_module_names
                and m != "__future__"}, sorted(everywhere)


def test_read_jsonl_reports_undecodable_bytes_and_skips_torn_lines(tmp_path):
    """The asymmetry `read_jsonl` documents, driven from both sides.

    Bytes that will not DECODE are a problem naming the file — the file is
    corrupt. A single LINE that will not parse is skipped — the ledger is
    append-only from concurrent agents and a torn line must not cost the other
    records. Conflating the two in either direction is the defect."""
    from foundry_mcp.tools.foundry_state import read_jsonl

    good = tmp_path / "ledger.jsonl"
    good.write_text(
        '{"a": 1}\n\n{"b": 2}\n"a bare string"\n{"c": 3\n{"d": 4}\n',
        encoding="utf-8",
    )
    records, problem = read_jsonl(good)
    assert problem is None
    assert records == [{"a": 1}, {"b": 2}, {"d": 4}]

    corrupt = tmp_path / "corrupt.jsonl"
    corrupt.write_bytes(b"\xff\xfe\n")
    records, problem = read_jsonl(corrupt)
    assert records == []
    assert problem is not None and "corrupt.jsonl" in problem

    absent = tmp_path / "never-written.jsonl"
    assert read_jsonl(absent) == ([], None)


# --------------------------------------------------------------------------- #
# GRIND cycle 9 — four filings against generated prose and generated counts.
# D-163 (two derivations of one number), D-166 (a section unfiltered by
# status), D-167 and D-168 (the remaining null-as-fact sentences).
# --------------------------------------------------------------------------- #


def test_the_unreported_count_is_derived_not_copied_from_the_rollup(report_env):
    """D-163 / AC-034 / FR-022 / CT-013 — the number's SOURCE, driven.

    `_read_spend` copied `unreported` verbatim out of `state.json.spend`, on
    the stated premise that the roll-up is "the orchestrator's derivation and
    the only one". It is not: `overlay_unreported` — now
    `foundry_state.overlay_unreported`, then the orchestrator's — runs
    inside `_spend_summary` against a throwaway deep copy, so the derived count
    never reaches the persisted document, and the only writer of the key there
    is `foundry_state.spend_bucket`, which seeds 0 and never increments.

    Driven by seeding the roll-up with a number that is provably not the
    dispatch record's: the published value must be the DERIVATION, and the
    roll-up's claim must appear as a named disagreement rather than as the
    answer."""
    state = _read_json(report_env, "state.json")
    state["spend"] = {
        "by_phase": {"F3": {"tokens": 0, "duration_ms": 0, "agents": 0,
                            "unreported": 99}},
        "by_cycle": {},
        "total": {"tokens": 0, "duration_ms": 0, "agents": 0,
                  "unreported": 99},
    }
    _write_json(report_env, "state.json", state)
    _generate(report_env)
    doc = _document(report_env)
    section = doc["spend_per_phase_and_cycle"]

    assert section["by_phase"]["F3"]["unreported"] != 99
    assert section["total"]["unreported"] != 99
    # The derivation, and the section that lists the same pairs, are one
    # number — that pair is the filing.
    assert section["total"]["unreported"] == doc["unreported_dispatches"]["count"]
    assert section["by_phase"]["F3"]["unreported"] == len(
        doc["unreported_dispatches"]["by_phase"].get("F3", [])
    )
    # The roll-up's claim is not discarded, it is NAMED.
    stale = {
        (d["scope"], d["key"], d["state_rollup"])
        for d in section["disagreements"] if d["field"] == "unreported"
    }
    assert ("by_phase", "F3", 99) in stale, section["disagreements"]
    assert ("run", "total", 99) in stale, section["disagreements"]


def test_a_report_cannot_publish_zero_unreported_beside_a_list_of_one(report_env):
    """D-163's observed shape, driven end to end: two dispatches, one spend
    record, and the two sections of ONE report.json must not disagree.

    AC-034 / FR-022: "the report shows N agents unreported per phase so the gap
    is visible". The filing observed `spend_per_phase_and_cycle.total.unreported
    = 0` and `by_phase {"F1": {"unreported": 0}}` in the same document as
    `unreported_dispatches {"count": 1, "by_phase": {"F1": ["casting-2"]}}`."""
    from foundry_mcp.tools.foundry_state import DISPATCH_PHASE_TO_RUN_PHASE

    cast_phase = DISPATCH_PHASE_TO_RUN_PHASE["cast"]
    (report_env / "spawns.log").write_text(
        json.dumps({"timestamp": "2026-09-03T00:00:00+00:00", "casting_id": 1,
                    "phase": "cast"}) + "\n"
        + json.dumps({"timestamp": "2026-09-03T00:00:01+00:00", "casting_id": 2,
                      "phase": "cast"}) + "\n",
        encoding="utf-8",
    )
    (report_env / SPEND_LEDGER_FILENAME).write_text(
        json.dumps({"agent": "casting-1", "phase": cast_phase, "cycle": 0,
                    "tokens": 1000, "duration_ms": 60_000,
                    "recorded_at": "2026-09-03T00:10:00+00:00"}) + "\n",
        encoding="utf-8",
    )
    (report_env / "stream-rollup.json").write_text(
        json.dumps({"cycles": {}}), encoding="utf-8"
    )
    _generate(report_env)
    doc = _document(report_env)

    listed = doc["unreported_dispatches"]
    spend = doc["spend_per_phase_and_cycle"]
    assert listed["count"] == 1
    assert listed["by_phase"] == {cast_phase: ["casting-2"]}
    assert spend["total"]["unreported"] == 1
    assert spend["by_phase"][cast_phase]["unreported"] == 1

    # And in the document a lead reads, not only the machine one.
    table = _markdown(report_env).split("## Spend per phase and cycle", 1)[1]
    row = next(ln for ln in table.splitlines()
               if ln.startswith(f"| phase | {cast_phase} |"))
    assert row.rstrip().endswith("| 1 |"), row


def test_the_spend_and_dispatch_sections_read_one_object(report_env):
    """D-163's structural half — the two sections cannot be re-derived apart.

    `generate_report` reads `_read_dispatch_summary` ONCE and hands the same
    object to `_read_spend` and `_unreported_dispatches_section`; the rule and
    the arithmetic over it both live in `foundry_state`. Asserted on the
    signature and on the helper, so a future edit that re-reads the ledgers in
    the spend reader turns this red rather than turning the report wrong."""
    import inspect

    from foundry_mcp.tools.foundry_state import unreported_dispatch_summary

    assert "dispatch_summary" in inspect.signature(fr._read_spend).parameters

    summary, problem = fr._read_dispatch_summary(report_env)
    assert problem is None
    _generate(report_env)
    doc = _document(report_env)
    assert doc["unreported_dispatches"]["count"] == summary["count"]
    assert doc["spend_per_phase_and_cycle"]["total"]["unreported"] == summary["count"]

    # The helper is the one in the leaf module, called with the run's inputs
    # and nothing re-decided here.
    direct = unreported_dispatch_summary(
        dispatch_rows=[{"agent": "a", "phase": "cast"}],
        stream_roster={},
        spend_rows=[],
        phase_of_dispatch={"cast": "F1"},
    )
    assert direct["count"] == 1 and direct["dispatched"] == 1
    assert direct["reported"] == 0
    assert direct["by_phase"] == {"F1": ["a"]}
    assert direct["by_cycle"] == {}, "no cycle was supplied, so none is claimed"


def test_a_closed_untiered_record_is_not_listed_under_the_blocking_note(
    report_env,
):
    """D-166 / FR-051 / AC-008 — the unknown-tier section lists OPEN records.

    FR-051's gloss and AC-008 both scope the separate listing to an OPEN
    pre-change defect. The section built the LATENT backlog beside it under
    `tier == "LATENT" and status == "open"` and built these rows under
    `tier == TIER_UNKNOWN` with no status test, so on the live archive it
    printed 156 FIXED records under a note asserting "It blocks the gates
    exactly like LIVE" and naming a re-filing as "the way out" — a blocking
    claim and a remedy, both false for every row. The gate disagreed:
    `_open_defects_by_tier` skips any record whose status is not open, so the
    true blocking count was 0."""
    from foundry_mcp.tools.orchestration.gates import _open_defects_by_tier

    _generate(report_env)
    doc = _document(report_env)
    section = doc["unknown_tier_defects"]

    # The frozen fixture's one untiered record (D-007) is FIXED.
    assert section["count"] == 0
    assert section["defects"] == []
    assert section["closed_count"] == 1
    assert section["closed_ids"] == ["D-007"]

    # The gate and the report now agree about what blocks.
    assert _open_defects_by_tier(report_env)[TIER_UNKNOWN] == []

    # The note still carries the blocking claim and the remedy — scoped to the
    # open record, and saying what became of the closed ones.
    assert "An OPEN one blocks the gates" in section["note"]
    assert "already closed" in section["note"]
    md = _markdown(report_env).split("## Unknown-tier defects", 1)[1]
    md = md.split("\n## ", 1)[0]
    assert "D-007" not in md, (
        "a closed record must not appear under the blocking note at all"
    )


def test_the_open_untiered_record_is_the_one_the_section_lists(report_env):
    """D-166's other arm: with one open and one closed untiered record, the
    section lists exactly the open one and counts the other.

    Driven on both at once, because a filter asserted only on the all-closed
    fixture would pass just as well if the section listed nothing ever."""
    defects = _read_json(report_env, "defects.json")
    closed = next(d for d in defects["defects"] if d["id"] == "D-007")
    reopened = dict(closed)
    reopened["id"] = "D-008"
    reopened["status"] = "open"
    reopened["fixed_in_cycle"] = None
    defects["defects"].append(reopened)
    _write_json(report_env, "defects.json", defects)
    _generate(report_env)

    section = _document(report_env)["unknown_tier_defects"]
    assert [d["id"] for d in section["defects"]] == ["D-008"]
    assert section["count"] == 1
    assert section["closed_ids"] == ["D-007"]

    # The CROSS-TAB is unfiltered on purpose: it is a census of the ledger, so
    # both records are still counted under the unknown tier there.
    cross = _document(report_env)["defects_by_tier_and_status"]["cross_tab"]
    ids = {did for bucket in cross[TIER_UNKNOWN].values() for did in bucket["ids"]}
    assert ids == {"D-007", "D-008"}


def test_a_verdicts_json_with_no_cycle_states_it_in_words(report_env):
    """D-167 / AC-036 / FR-023 — D-150's class, one renderer over.

    The verdict-matrix header interpolated `value.get('cycle')` unguarded while
    GUARDING its two neighbours in the same f-string — `count` defaults to 0
    and `by_verdict` falls back to '{}'. A verdicts.json with no `cycle` key
    rendered the operator-facing line "verdicts at cycle None: {}"."""
    verdicts = _read_json(report_env, "verdicts.json")
    del verdicts["cycle"]
    _write_json(report_env, "verdicts.json", verdicts)
    _generate(report_env)

    header = next(
        ln for ln in _markdown(report_env)
        .split("## Verdict matrix", 1)[1].splitlines()
        if "requirements, verdicts" in ln
    )
    assert "None" not in header, header
    assert "does not record" in header, header
    # report.json stays honest — null, never a fabricated cycle.
    assert _document(report_env)["verdict_matrix"]["cycle"] is None


def test_cycle_zero_is_a_recorded_cycle_not_an_absent_one(report_env):
    """D-167's other arm, and why the guard is not `or`.

    The server's counter is 0-based (ST-001), so a first-INSPECT verdict set is
    stamped `cycle: 0`. `value.get('cycle') or '<unrecorded>'` would report the
    run's first cycle as unrecorded — the same falsehood one value over."""
    verdicts = _read_json(report_env, "verdicts.json")
    verdicts["cycle"] = 0
    _write_json(report_env, "verdicts.json", verdicts)
    _generate(report_env)

    header = next(
        ln for ln in _markdown(report_env)
        .split("## Verdict matrix", 1)[1].splitlines()
        if "requirements, verdicts" in ln
    )
    assert "at cycle 0" in header, header
    assert "does not record" not in header, header


def test_executing_versions_states_which_fields_the_run_did_not_record(
    report_env,
):
    """D-168 / AC-036 / CT-010 — the one section that rendered blanks silently.

    Five bare `state.get` calls under a two-column table with no prose. On a
    run with none of the fields it printed five empty Value cells and
    report.json carried null for each: the two documents agreed, so nothing was
    false, and a reader still could not tell "this run recorded no version
    fields" from "the report dropped them". That distinction is what AC-036
    names the section for — `Foundry-Init` writes these fields exactly when it
    ran the self-target preflight, so their absence IS the finding.

    Every sibling that can render an absent value states why (D-150 in the
    spend section, D-151 in the baseline section, D-103 in the LATENT
    backlog). This says it too, in BOTH documents."""
    state = _read_json(report_env, "state.json")
    for field in fr._EXECUTING_VERSION_FIELDS:
        state.pop(field, None)
    _write_json(report_env, "state.json", state)
    _generate(report_env)

    section = _document(report_env)["executing_versions"]
    assert section["recorded"] is False
    assert section["missing"] == list(fr._EXECUTING_VERSION_FIELDS)
    assert "recorded nothing there" in section["note"]
    assert "not because this report dropped a value" in section["note"]

    md = _markdown(report_env).split("## Executing server and plugin versions", 1)[1]
    md = md.split("\n## ", 1)[0]
    # THE TWO DOCUMENTS MAKE THE SAME STATEMENT. The note is in the markdown,
    # and the cells carry the backlog's own spelling rather than nothing.
    assert "recorded nothing there" in md
    for field in fr._EXECUTING_VERSION_FIELDS:
        row = next(ln for ln in md.splitlines() if ln.startswith(f"| {field} |"))
        assert fr.NO_LOCATION_CELL in row, row


def test_executing_versions_names_a_partial_record_field_by_field(report_env):
    """D-168's middle arm: "no commit but a version" and "nothing at all" are
    different states of the preflight, and a lead routes on which.

    `self_target: false` is a RECORDED answer, so the test for a missing field
    is `is None` and not truthiness — every run that is not self-targeting
    would otherwise be reported as unmeasured."""
    state = _read_json(report_env, "state.json")
    state["server_commit"] = None
    state["self_target"] = False
    _write_json(report_env, "state.json", state)
    _generate(report_env)

    section = _document(report_env)["executing_versions"]
    assert section["missing"] == ["server_commit"]
    assert section["recorded"] is False
    assert section["self_target"] is False
    assert "no server_commit" in section["note"]
    assert "server_version" not in section["note"], (
        "a field the run DID record is not named as missing"
    )

    md = _markdown(report_env).split("## Executing server and plugin versions", 1)[1]
    md = md.split("\n## ", 1)[0]
    commit_row = next(
        ln for ln in md.splitlines() if ln.startswith("| server_commit |")
    )
    assert fr.NO_LOCATION_CELL in commit_row, commit_row
    target_row = next(
        ln for ln in md.splitlines() if ln.startswith("| self_target |")
    )
    assert "False" in target_row, target_row


def test_executing_versions_says_so_when_every_field_is_recorded(report_env):
    """D-168's recorded arm, on the frozen fixture, which carries all five."""
    _generate(report_env)
    section = _document(report_env)["executing_versions"]
    assert section["recorded"] is True
    assert section["missing"] == []
    assert "recorded all five fields" in section["note"]
    md = _markdown(report_env).split("## Executing server and plugin versions", 1)[1]
    assert fr.NO_LOCATION_CELL not in md.split("\n## ", 1)[0]


# --------------------------------------------------------------------------- #
# The demonstration test whose captured stdout is committed as evidence.
#
# It is a REAL test — every line it prints is also asserted — so it cannot
# drift from the behaviour it demonstrates the way a hand-written transcript
# can. Run with `-s` to see the body; the committed log is exactly that body.
#
# Nothing environment-dependent is printed: no tmp path, no timestamp, no
# duration. That is deliberate rather than incidental — the gate re-executes
# this command in a detached worktree and byte-compares, so a path or a clock
# reading in the output would be a redaction to declare rather than a fact to
# report, and the facts here are all properties of the frozen fixture.
# --------------------------------------------------------------------------- #


def test_demo_report_sections_over_the_frozen_fixture(report_env, capsys):
    """AC-036 / FR-023 / GI-006 / CT-014 end to end, printed.

    Also AC-002 (the report names the LATENT instances left), AC-004 (the
    escalated class's status, exit reason, cleared cycle and packets), AC-022
    (every lead_fix record), AC-034 (unreported dispatches, advisory), FR-051
    (unknown-tier defects listed separately) and NFR-002 / NFR-003."""
    with capsys.disabled():
        _generate(report_env)
        doc = _document(report_env)
        md = _markdown(report_env)

        print()
        print("=== report.json top-level keys ===")
        for key in doc:
            print(f"  {key}")

        print("=== REPORT.md headings, in REPORT_REQUIRED_SECTIONS order ===")
        for heading in re.findall(r"^## (.+)$", md, re.M):
            print(f"  ## {heading}")

        print("=== defects by tier and status (FR-051: unknown is its own row) ===")
        for tier in sorted(doc["defects_by_tier_and_status"]["cross_tab"]):
            for status, bucket in sorted(
                doc["defects_by_tier_and_status"]["cross_tab"][tier].items()
            ):
                print(f"  {tier:<7} {status:<10} {bucket['count']}  {bucket['ids']}")

        print("=== LATENT backlog (NFR-003 / AC-002) ===")
        for row in doc["latent_backlog"]["defects"]:
            print(f"  {row['id']}  cycle {row['cycle']}  {row['class']}")
            # D-103: the location a filing recorded, or the statement that it
            # recorded none. FR-005 never refuses a LATENT filing for that.
            print(f"      located={row['located']}  file={row['file']}  "
                  f"symbol={row['symbol']}  note={row['location_note']}")

        print("=== unknown-tier defects, listed separately (FR-051) ===")
        # D-166: the list is the OPEN untiered records, because the note above
        # it says they hold the gates shut and the gate skips a closed record.
        # The closed ones are counted, never listed under that claim.
        print(f"  open (blocking): {doc['unknown_tier_defects']['count']}  "
              f"closed (not blocking): "
              f"{doc['unknown_tier_defects']['closed_count']}  "
              f"{doc['unknown_tier_defects']['closed_ids']}")
        for row in doc["unknown_tier_defects"]["defects"]:
            print(f"  {row['id']}  status {row['status']}  {row['class']}")
            # D-120: the four fields either door matches on to re-tier the
            # record in place — the remedy the DONE refusal names.
            print(f"      source={row['source']}  type={row['type']}  "
                  f"file={row['file']}  symbol={row['symbol']}")

        print("=== escalated classes (AC-004) ===")
        for row in doc["escalated_classes"]["classes"]:
            print(
                f"  {row['class']}  status={row['status']}  "
                f"exit_reason={row['exit_reason']}  "
                f"cleared_at_cycle={row['cleared_at_cycle']}  "
                f"packets={row['structural_packets_dispatched']}  "
                f"open_latent={row['open_latent_defect_ids']}"
            )

        print("=== lead_fix records, one line per file touched (AC-022) ===")
        for row in doc["lead_fix_records"]["records"]:
            print(f"  {row['defect_id']}  tier={row['tier']}  test={row['test']}")
            for entry in row["file_rows"]:
                print(f"      {entry['path']}  lines={entry['line_count']}")

        print("=== INSPECT mode per cycle, every decision (AC-036 / D-119) ===")
        modes = doc["inspect_modes_per_cycle"]
        for cycle, group in modes["per_cycle"].items():
            for row in group:
                print(f"  cycle {cycle}  {row['phase']:<5} {row['mode']:<5} "
                      f"{row['rule']}")
        # D-193: the axis those rows are rendered against, and the cycles that
        # carry no row at all. The section that lists them cannot be read
        # without it — 5 rows over a 15-cycle run is a different document from
        # 5 rows over a 5-cycle one.
        print(f"  axis length {modes['cycle_axis_length']}  "
              f"without a decision {modes['cycles_without_decision']}")
        print(f"  {modes['note']}")

        print("=== spend: tokens and minutes only, no money (NFR-002) ===")
        spend = doc["spend_per_phase_and_cycle"]
        for phase, row in spend["by_phase"].items():
            print(f"  phase {phase:<5} tokens={row['tokens']:<9} "
                  f"minutes={row['minutes']:<6} records={row['records']} "
                  f"agents={row['agents']}")
        total = spend["total"]
        print(f"  run   total tokens={total['tokens']:<9} "
              f"minutes={total['minutes']:<6} records={total['records']} "
              f"agents={total['agents']}")
        # D-090: `agents` is the orchestrator's DISTINCT-agent count and
        # `records` is the ledger row count. Where the roll-up and the ledger
        # disagree the report NAMES it rather than printing two numbers under
        # one label.
        for entry in spend["disagreements"]:
            print(f"  disagreement: {entry['scope']} {entry['key']} "
                  f"{entry['field']} ledger={entry['ledger']} "
                  f"state_rollup={entry['state_rollup']}")

        print("=== unreported dispatches, advisory only (AC-034) ===")
        for phase, agents in doc["unreported_dispatches"]["by_phase"].items():
            print(f"  {phase:<6} {agents}")
        # D-163: the spend section's Unreported column and this section's count
        # are two renderings of ONE derivation, so they are printed together —
        # a report that carried 0 in one and 1 in the other is the filing.
        print(f"  count={doc['unreported_dispatches']['count']}  "
              f"spend total unreported="
              f"{doc['spend_per_phase_and_cycle']['total']['unreported']}")

        print("=== executing versions (AC-036 / CT-010 / D-168) ===")
        versions = doc["executing_versions"]
        print(f"  recorded={versions['recorded']}  missing={versions['missing']}")
        for field in fr._EXECUTING_VERSION_FIELDS:
            print(f"  {field:<15} {versions[field]}")

        print("=== baseline comparison (AC-036) ===")
        bc = doc["baseline_comparison"]
        print(f"  baseline {bc['baseline']['run']}: "
              f"grind={bc['baseline']['grind_cycles']} "
              f"post_verification={bc['baseline']['post_verification_cycles']}")
        print(f"  target: grind={bc['target']['grind_cycles']} "
              f"post_verification={bc['target']['post_verification_cycles']}")
        print(f"  this run: grind={bc['current']['grind_cycles']} "
              f"post_verification={bc['current']['post_verification_cycles']}")
        # D-085: what the baseline's own archive derives, published BESIDE the
        # recorded constant and never over it.
        print(f"  baseline derived from the archive: {bc['baseline_derived']}")

        print("=== report_status, the DONE gate's read (GI-006 / OT-025) ===")
        status = report_document_status(report_env)
        print(f"  present={status['present']}  missing_sections={status['missing_sections']}")

    # Every printed line is also asserted, so the transcript cannot drift.
    doc = _document(report_env)
    assert list(doc)[2:] == list(REPORT_REQUIRED_SECTIONS)
    assert [d["id"] for d in doc["latent_backlog"]["defects"]] == ["D-004", "D-005"]
    # D-166: D-007 is fixed in the frozen fixture, so it is COUNTED as closed
    # and not listed under a note claiming it holds the gates shut.
    assert [d["id"] for d in doc["unknown_tier_defects"]["defects"]] == []
    assert doc["unknown_tier_defects"]["closed_ids"] == ["D-007"]
    # D-163: one derivation, two renderings, in the document a lead reads.
    assert (doc["spend_per_phase_and_cycle"]["total"]["unreported"]
            == doc["unreported_dispatches"]["count"])
    assert doc["escalated_classes"]["classes"][0]["exit_reason"] == "budget"
    assert doc["lead_fix_records"]["count"] == 2
    assert all(r["file_rows"] for r in doc["lead_fix_records"]["records"])
    assert doc["spend_per_phase_and_cycle"]["total"]["records"] == 5
    assert doc["baseline_comparison"]["baseline_metrics"]["grind_cycles"] == (
        THUNDER_VIPER_BASELINE["grind_cycles"]
    )
    assert doc["inspect_modes_per_cycle"]["by_mode"] == {"DELTA": 2, "FULL": 4}
    assert doc["unreported_dispatches"]["count"] > 0
    assert report_document_status(report_env)["present"] is True


# --------------------------------------------------------------------------- #
# The GRIND cycle 9 demonstration, driven at the real door.
#
# Same contract as the transcript above: a REAL test, every printed line also
# asserted, nothing environment-dependent in the output. The four filings it
# demonstrates are about what the GENERATOR writes, so each arm builds the
# minimal archive that reproduces the filed shape and reads both documents
# back — the number in report.json and the sentence in REPORT.md.
# --------------------------------------------------------------------------- #

#: The five version fields a run records, with values that are recognisable in
#: the transcript and carry no clock reading or path from this machine.
_DEMO_VERSIONS = {
    "server_version": "4.10.0",
    "plugin_version": "4.10.0",
    "server_root": "/repo/plugins/foundry",
    "server_commit": "abc123",
    "self_target": True,
}


def _dispatch_phase(verb: str) -> str:
    """`spawns.log`'s dispatch VERB mapped to the run phase id spend rows use.

    Read from `foundry_state`, never re-typed: a local copy of that
    mapping is the drift D-048 closed.
    """
    from foundry_mcp.tools.foundry_state import DISPATCH_PHASE_TO_RUN_PHASE

    return DISPATCH_PHASE_TO_RUN_PHASE[verb]


def _demo_run(tmp_path, name, **over):
    """A minimal archive with only the ledgers an arm needs. Returns
    ``(run_dir, report_json, report_md)``."""
    run_dir = tmp_path / "foundry-archive" / name
    run_dir.mkdir(parents=True)
    _write_json(run_dir, "state.json", over.get(
        "state", {"phase": "F3", "cycle": 1, **_DEMO_VERSIONS}))
    _write_json(run_dir, "defects.json", {"defects": over.get("defects", [])})
    _write_json(run_dir, "verdicts.json",
                over.get("verdicts", {"cycle": 1, "requirements": []}))
    _write_json(run_dir, "stream-rollup.json", {"cycles": {}})
    for filename, rows in (("spawns.log", over.get("spawns", [])),
                           (SPEND_LEDGER_FILENAME, over.get("spend", []))):
        (run_dir / filename).write_text(
            "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
        )
    assert generate_report(tmp_path, run_dir)["ok"] is True
    return run_dir, _document(run_dir), _markdown(run_dir)


def _demo_defect(did: str, status: str) -> dict:
    """An untiered record — no `tier` key at all, which is what a pre-change
    filing looks like and what reads as TIER_UNKNOWN."""
    return {
        "id": did, "cycle": 1, "source": "trace", "type": "UNWIRED",
        "description": f"{did} description", "spec_ref": "",
        "symbol": "handler", "file": "src/legacy.py", "status": status,
        "fixed_in_cycle": None if status == "open" else 2,
        "class": "UNWIRED_SURFACE",
    }


def test_demo_grind_cycle_9_filings_at_the_real_door(tmp_path, capsys):
    """D-163, D-166, D-167 and D-168, each driven through
    `foundry_report.generate_report` and read back out of both documents.

    AC-034 / FR-022 / CT-013 (D-163), FR-051 / AC-008 (D-166), AC-036 /
    FR-023 (D-167, D-168)."""
    from foundry_mcp.tools.orchestration.gates import _open_defects_by_tier

    def _section(md: str, heading: str) -> str:
        return md.split(f"## {heading}", 1)[1].split("\n## ", 1)[0]

    def _line(md: str, heading: str, needle: str) -> str:
        return next(ln for ln in _section(md, heading).splitlines()
                    if needle in ln)

    cast = _dispatch_phase("cast")
    dispatches = [{"timestamp": "t1", "casting_id": 1, "phase": "cast"},
                  {"timestamp": "t2", "casting_id": 2, "phase": "cast"}]
    one_report = [{"agent": "casting-1", "phase": cast, "cycle": 0,
                   "tokens": 1000, "duration_ms": 60_000}]

    with capsys.disabled():
        print("\n=== D-163 — two dispatches, ONE spend record, one report.json ===")
        _, doc, md = _demo_run(tmp_path, "d163", spawns=dispatches,
                               spend=one_report)
        listed, spend = doc["unreported_dispatches"], doc["spend_per_phase_and_cycle"]
        print(f"  unreported_dispatches:           count={listed['count']} "
              f"by_phase={listed['by_phase']}")
        print(f"  spend.total:                     "
              f"unreported={spend['total']['unreported']}")
        print(f"  spend.by_phase[{cast}]:                unreported="
              f"{spend['by_phase'][cast]['unreported']}")
        print("  the filing observed 0 here beside 1 there; one derivation now:",
              spend["total"]["unreported"] == listed["count"])
        print("  " + _line(md, "Spend per phase and cycle", f"| phase | {cast} |"))

        print("\n  a stale state.json.spend claim is NAMED, never published:")
        stale = {"tokens": 0, "duration_ms": 0, "agents": 0, "unreported": 99}
        _, doc2, _ = _demo_run(
            tmp_path, "d163-stale", spawns=dispatches, spend=one_report,
            state={"phase": "F3", "cycle": 1, **_DEMO_VERSIONS,
                   "spend": {"by_phase": {cast: dict(stale)}, "by_cycle": {},
                             "total": dict(stale)}},
        )
        spend2 = doc2["spend_per_phase_and_cycle"]
        print(f"  published={spend2['total']['unreported']}  "
              f"state.json.spend claimed={stale['unreported']}")
        for entry in spend2["disagreements"]:
            if entry["field"] == "unreported":
                print(f"  disagreement: {entry['scope']} {entry['key']} "
                      f"{entry['field']} derived={entry['ledger']} "
                      f"state_rollup={entry['state_rollup']}")

        print("\n=== D-166 — the unknown-tier section lists OPEN records only ===")
        run_dir, doc3, md3 = _demo_run(
            tmp_path, "d166",
            defects=[_demo_defect("D-001", "fixed"),
                     _demo_defect("D-002", "fixed"),
                     _demo_defect("D-003", "open")],
        )
        unknown = doc3["unknown_tier_defects"]
        print(f"  open (listed, blocking): {[d['id'] for d in unknown['defects']]}"
              f"   closed (counted, not listed): {unknown['closed_count']} "
              f"{unknown['closed_ids']}")
        print("  the gate agrees about what blocks:",
              [d["id"] for d in _open_defects_by_tier(run_dir)[TIER_UNKNOWN]])
        print("  the cross-tab is a census and still counts all three:")
        for status, bucket in sorted(
            doc3["defects_by_tier_and_status"]["cross_tab"][TIER_UNKNOWN].items()
        ):
            print(f"    {status:<7} {bucket['count']} {bucket['ids']}")
        print("  no closed id appears under the blocking note:",
              all(did not in _section(md3, "Unknown-tier defects")
                  for did in unknown["closed_ids"]))
        print("  ..." + unknown["note"].split("(D-120). ", 1)[1])

        print("\n=== D-167 — a verdicts.json with no cycle key ===")
        _, doc4, md4 = _demo_run(tmp_path, "d167",
                                 verdicts={"requirements": []})
        print("  " + _line(md4, "Verdict matrix", "requirements, verdicts"))
        print("  report.json stays honest — cycle is null, never fabricated:",
              doc4["verdict_matrix"]["cycle"])
        _, _, md5 = _demo_run(tmp_path, "d167-zero",
                              verdicts={"cycle": 0, "requirements": []})
        print("  and cycle 0 is a RECORDED cycle (the server counter is 0-based):")
        print("  " + _line(md5, "Verdict matrix", "requirements, verdicts"))

        print("\n=== D-168 — the executing-versions section states what it has ===")
        _, doc6, md6 = _demo_run(tmp_path, "d168",
                                 state={"phase": "F3", "cycle": 1})
        print(f"  recorded={doc6['executing_versions']['recorded']}  "
              f"missing={doc6['executing_versions']['missing']}")
        for line in _section(md6, "Executing server and plugin versions").strip().splitlines():
            print("  " + line)
        _, doc7, _ = _demo_run(
            tmp_path, "d168-partial",
            state={"phase": "F3", "cycle": 1, **_DEMO_VERSIONS,
                   "server_commit": None, "self_target": False},
        )
        print("  a PARTIAL record names the one field it lacks, and "
              "self_target False is an answer:")
        print(f"  recorded={doc7['executing_versions']['recorded']}  "
              f"missing={doc7['executing_versions']['missing']}  "
              f"self_target={doc7['executing_versions']['self_target']}")

    # Every printed line is also asserted, so the transcript cannot drift.
    _, doc, md = _demo_run(tmp_path, "d163-assert", spawns=dispatches,
                           spend=one_report)
    assert doc["unreported_dispatches"]["count"] == 1
    assert doc["unreported_dispatches"]["by_phase"] == {cast: ["casting-2"]}
    assert doc["spend_per_phase_and_cycle"]["total"]["unreported"] == 1
    assert doc["spend_per_phase_and_cycle"]["by_phase"][cast]["unreported"] == 1
    assert _line(md, "Spend per phase and cycle",
                 f"| phase | {cast} |").rstrip().endswith("| 1 |")

    run_dir, doc, md = _demo_run(
        tmp_path, "d166-assert",
        defects=[_demo_defect("D-001", "fixed"), _demo_defect("D-002", "fixed"),
                 _demo_defect("D-003", "open")],
    )
    assert [d["id"] for d in doc["unknown_tier_defects"]["defects"]] == ["D-003"]
    assert doc["unknown_tier_defects"]["closed_ids"] == ["D-001", "D-002"]
    assert [d["id"] for d in _open_defects_by_tier(run_dir)[TIER_UNKNOWN]] == [
        "D-003"
    ]
    for did in ("D-001", "D-002"):
        assert did not in _section(md, "Unknown-tier defects")

    _, doc, md = _demo_run(tmp_path, "d167-assert", verdicts={"requirements": []})
    assert doc["verdict_matrix"]["cycle"] is None
    assert "None" not in _line(md, "Verdict matrix", "requirements, verdicts")

    _, doc, md = _demo_run(tmp_path, "d168-assert",
                           state={"phase": "F3", "cycle": 1})
    assert doc["executing_versions"]["missing"] == list(
        fr._EXECUTING_VERSION_FIELDS
    )
    assert "recorded nothing there" in _section(
        md, "Executing server and plugin versions"
    )


# --------------------------------------------------------------------------- #
# The GRIND cycle 10 demonstration, driven at the real doors.
#
# Same contract as the two transcripts above: a REAL test, every printed line
# also asserted, nothing environment-dependent in the output. Two filings, both
# about a number a generated document publishes beside prose describing it —
# D-175 where the number contradicted the halt reason written by the same
# transition, and D-172 where the prose claimed an axis covered agents it
# structurally cannot.
# --------------------------------------------------------------------------- #


def test_demo_grind_cycle_10_filings_at_the_real_door(tmp_path, capsys):
    """D-175 and D-172, each driven through the door that publishes the number
    and read back out of both documents.

    FR-023 / FR-024 / FR-045 / NFR-001 / OT-026 / GI-006 (D-175);
    AC-034 / FR-022 / CT-013 / AC-036 (D-172)."""
    from foundry_mcp.tools.foundry_state import DISPATCH_PHASE_TO_RUN_PHASE
    from foundry_mcp.tools.foundry_state import derive_cycle_count

    cast = DISPATCH_PHASE_TO_RUN_PHASE["cast"]

    def _row(md: str, prefix: str) -> str:
        return next(ln for ln in md.splitlines() if ln.startswith(prefix))

    with capsys.disabled():
        print("\n=== D-175 — the halt reason and the report it generated, "
              "one call, one number ===")
        state, doc, md, result = _halted_at_the_real_door(
            tmp_path, max_cycles=2, cycle=2
        )
        print(f"  Foundry-Phase('grind_start') -> ok={result['ok']} "
              f"halted={result['halted']}  state.phase={state['phase']}")
        print(f"  state.halted_reason:  {state['halted_reason']}")
        print(f"  the server counter it was computed from: cycle="
              f"{state['cycle']}  halted_at_cycle={state['halted_at_cycle']}")
        print(f"  report.json run.max_cycles:                "
              f"{doc['run']['max_cycles']}")
        print(f"  report.json baseline_comparison.current.grind_cycles:  "
              f"{doc['baseline_comparison']['current']['grind_cycles']}")
        print("  REPORT.md: " + _row(md, "| GRIND cycles |"))
        print("  the filing read 3 in both of those, beside a cap of 2 and a "
              "reason naming GRIND 3 as the one that did NOT open.")
        print("  under the cap now:",
              doc["baseline_comparison"]["current"]["grind_cycles"]
              <= doc["run"]["max_cycles"])

        print("\n  the counter is untouched; only the GRIND it would have "
              "opened is subtracted:")
        run_dir = tmp_path / "foundry-archive" / "halt-run"
        derived = derive_cycle_count(run_dir)
        print(f"  index={derived['index']}  halted={derived['halted']}  "
              f"count={derived['count']}")
        print("  and a run at the same counter with no halt record still "
              "publishes index + 1, which is what keeps the two published "
              "baselines reproducing from their own archives:")
        plain = tmp_path / "foundry-archive" / "not-halted"
        plain.mkdir(parents=True)
        _write_json(plain, "state.json", {"phase": "F3", "cycle": 2})
        _write_json(plain, "defects.json", {"defects": []})
        running = derive_cycle_count(plain)
        print(f"  index={running['index']}  halted={running['halted']}  "
              f"count={running['count']}")

        print("\n=== D-172 — the cycle axis covers what the archive stamps, "
              "and the prose says which ===")
        _, doc2, md2 = _demo_run(
            tmp_path, "d172-demo",
            spawns=[{"timestamp": "t1", "casting_id": n, "phase": "cast"}
                    for n in (1, 2, 3)],
            spend=[{"agent": "casting-1", "phase": cast, "cycle": 1,
                    "tokens": 10, "duration_ms": 60_000}],
        )
        listed2 = doc2["unreported_dispatches"]
        spend2 = doc2["spend_per_phase_and_cycle"]
        print(f"  three teammates dispatched at cycle 1, one reported spend:")
        print(f"  unreported_dispatches: count={listed2['count']}  "
              f"by_phase={listed2['by_phase']}")
        print(f"  spend by_phase[{cast}].unreported = "
              f"{spend2['by_phase'][cast]['unreported']}   "
              f"spend by_cycle['1'].unreported = "
              f"{spend2['by_cycle']['1']['unreported']}")
        print(f"  the filing observed exactly that 2-beside-0 with prose "
              f"claiming the cycle row 'names the cycles those agents were "
              f"dispatched in'.")
        print(f"  the gap is now a derived number: "
              f"unreported_without_cycle={spend2['unreported_without_cycle']}")
        print("  ..." + spend2["note"].split("The cycle rows are narrower: ", 1)[1]
              .split(" One stream agent", 1)[0])

        print("\n  the axis is narrowed, not emptied — a stream agent IS "
              "stamped, by the roll-up's own cycle bucket:")
        stream_dir = tmp_path / "foundry-archive" / "d172-demo-stream"
        stream_dir.mkdir(parents=True)
        _write_json(stream_dir, "state.json", {"phase": "F3", "cycle": 2})
        _write_json(stream_dir, "defects.json", {"defects": []})
        _write_json(stream_dir, "verdicts.json", {"cycle": 2, "requirements": []})
        _write_json(stream_dir, "stream-rollup.json", {"cycles": {
            "1": {"prove": {"records": 1}}, "2": {"prove": {"records": 1}},
        }})
        (stream_dir / "spawns.log").write_text(
            json.dumps({"timestamp": "t1", "casting_id": 4, "phase": "cast"})
            + "\n", encoding="utf-8",
        )
        (stream_dir / SPEND_LEDGER_FILENAME).write_text("", encoding="utf-8")
        assert generate_report(tmp_path, stream_dir)["ok"] is True
        spend3 = _document(stream_dir)["spend_per_phase_and_cycle"]
        print(f"  one stream pair over two cycles + one teammate pair: "
              f"total={spend3['total']['unreported']}  "
              f"cycle rows={{'1': {spend3['by_cycle']['1']['unreported']}, "
              f"'2': {spend3['by_cycle']['2']['unreported']}}}  "
              f"without a cycle={spend3['unreported_without_cycle']}")
        print("  the cycle column still does not add up to the total, and now "
              "the note says why rather than only that it does not.")

    # Every printed line is also asserted, so the transcript cannot drift.
    state, doc, md, result = _halted_at_the_real_door(
        tmp_path, max_cycles=2, cycle=2, name="halt-run-assert"
    )
    assert result["ok"] is True and result["halted"] is True
    # FR-019 landed the structured `{reason, text}` while this register was
    # written against the free f-string, and `in` on a mapping tests its KEYS —
    # so the old assertion did not merely go stale, it started asking a
    # question with no relation to the sentence it names. `halt_reason` is the
    # resolver both shapes go through; the text is where the sentence lives in
    # either, which is exactly what `_halt_and_co_dispatch_section` reads.
    _reason = state["halted_reason"]
    assert halt_reason(
        _reason.get("reason") if isinstance(_reason, dict) else _reason
    ) == "cap_reached", _reason
    assert "opening GRIND cycle 3 would exceed it" in (
        _reason["text"] if isinstance(_reason, dict) else _reason
    ), _reason
    assert doc["baseline_comparison"]["current"]["grind_cycles"] == 2
    assert doc["run"]["max_cycles"] == 2
    assert _row(md, "| GRIND cycles |").rstrip().endswith("| 2 |")

    derived = derive_cycle_count(tmp_path / "foundry-archive" / "halt-run")
    assert (derived["index"], derived["halted"], derived["count"]) == (2, True, 2)
    running = derive_cycle_count(tmp_path / "foundry-archive" / "not-halted")
    assert (running["index"], running["halted"], running["count"]) == (2, False, 3)

    _, doc2, _ = _demo_run(
        tmp_path, "d172-demo-assert",
        spawns=[{"timestamp": "t1", "casting_id": n, "phase": "cast"}
                for n in (1, 2, 3)],
        spend=[{"agent": "casting-1", "phase": cast, "cycle": 1,
                "tokens": 10, "duration_ms": 60_000}],
    )
    spend2 = doc2["spend_per_phase_and_cycle"]
    assert doc2["unreported_dispatches"]["count"] == 2
    assert spend2["by_phase"][cast]["unreported"] == 2
    assert spend2["by_cycle"]["1"]["unreported"] == 0
    assert spend2["unreported_without_cycle"] == 2
    assert "names the cycles those agents were dispatched in" not in spend2["note"]

    spend3 = _document(tmp_path / "foundry-archive" / "d172-demo-stream")[
        "spend_per_phase_and_cycle"
    ]
    assert spend3["total"]["unreported"] == 2
    assert spend3["unreported_without_cycle"] == 1
    assert [spend3["by_cycle"][k]["unreported"] for k in ("1", "2")] == [1, 1]


def test_demo_grind_cycle_15_the_axis_the_decisions_are_rendered_against(
    report_env, tmp_path, capsys
):
    """D-193, driven through `generate_report` and read back out of both
    documents on the filing run's own shape.

    AC-036 / FR-023 / CT-014 / GI-006 / US-008."""
    def _shape(state_cycle: int, cycles: list[int]) -> dict:
        state = _read_json(report_env, "state.json")
        state["cycle"] = state_cycle
        state["inspect_modes"] = [_a_decision(c) for c in cycles]
        _write_json(report_env, "state.json", state)
        _generate(report_env)
        return _document(report_env)

    with capsys.disabled():
        print("\n=== D-193 — the filing run's own shape: a counter that "
              "reached 14, an inspect_modes ledger that starts at 10 ===")
        doc = _shape(14, list(range(10, 15)))
        section = doc["inspect_modes_per_cycle"]
        print(f"  report.json inspect_modes_per_cycle.count       "
              f"{section['count']}")
        print(f"  report.json inspect_modes_per_cycle.cycle_count "
              f"{section['cycle_count']}   <- cycles that CARRY a decision")
        print(f"  report.json baseline_comparison.current.grind_cycles  "
              f"{doc['baseline_comparison']['current']['grind_cycles']}"
              f"   <- cycles the run RAN")
        print("  the filing read exactly those two numbers, 64 lines apart in "
              "one REPORT.md, with nothing reconciling them and no sentence "
              "naming the cycles that recorded nothing.")
        print("\n  the axis is now derived, from the reading the baseline "
              "section already used:")
        print(f"  cycle_axis_length        {section['cycle_axis_length']}")
        print(f"  cycles_without_decision  {section['cycles_without_decision']}")
        print(f"  same reading as the baseline: "
              f"{section['cycle_axis_length'] == doc['baseline_comparison']['current']['grind_cycles']}")
        print("\n  and REPORT.md says it, in the words report.json carries:")
        for line in _inspect_md(report_env).strip().splitlines()[:3]:
            if line:
                print(f"  {line}")
        print(f"\n  the same sentence is in report.json: "
              f"{section['note'] in _inspect_md(report_env)}")

        print("\n=== the other arm: a run that recorded every cycle says so, "
              "so 'no sentence' is never the answer ===")
        section = _shape(5, list(range(0, 6)))["inspect_modes_per_cycle"]
        print(f"  cycle_axis_length {section['cycle_axis_length']}  "
              f"cycles_without_decision {section['cycles_without_decision']}")
        print("  " + section["note"].split("cycles, from")[1].split(". ")[-1])

        print("\n=== gaps print as the runs they are, never as a dump ===")
        section = _shape(9, [3, 8, 9])["inspect_modes_per_cycle"]
        print(f"  cycles_without_decision {section['cycles_without_decision']}")
        print("  " + section["note"].split("; ")[1])

        print("\n=== what is NOT done: an axis taken from the ledger being "
              "rendered against it ===")
        no_counter = _minimal_run(
            tmp_path, "demo-no-counter",
            {"phase": "F2", "inspect_modes": [_a_decision(3), _a_decision(4)]},
        )
        generate_report(tmp_path, no_counter)
        note = _document(no_counter)["inspect_modes_per_cycle"]["note"]
        print("  a run whose counter no source can supply, whose ledger names "
              "cycles 3 and 4:")
        print(f"  cycle_axis_length       "
              f"{_document(no_counter)['inspect_modes_per_cycle']['cycle_axis_length']}")
        print(f"  cycles_without_decision "
              f"{_document(no_counter)['inspect_modes_per_cycle']['cycles_without_decision']}")
        print(f"  {note}")
        print("  an axis of 0-4 taken from those two rows would have been "
              "complete by construction — every recorded cycle on it, no "
              "cycle off it — which is the D-193 fabrication wearing a "
              "different hat.")

    doc = _shape(14, list(range(10, 15)))
    section = doc["inspect_modes_per_cycle"]
    assert section["cycle_count"] == 5
    assert section["cycle_axis_length"] == 15
    assert section["cycles_without_decision"] == list(range(10))
    assert section["cycle_axis_length"] == (
        doc["baseline_comparison"]["current"]["grind_cycles"]
    )
    assert "cycles 0-9 carry none" in section["note"]
    assert section["note"] in _inspect_md(report_env)


# --------------------------------------------------------------------------- #
# D-232 / D-235 — the cycle axis publishing an unmeasured zero as a measurement
# --------------------------------------------------------------------------- #


def test_a_zero_cycle_bucket_the_ledger_never_named_is_not_a_report_row(
    report_env,
):
    """AC-033 / AC-036 / FR-037 / CT-013 — an absent row is honest where a zero
    row is a claim (D-229), and this reader re-created every row D-229 pruned.

    D-229 closed the zero rows in `foundry_state.overlay_unreported`,
    which the DISPLAY runs on a deep copy and `foundry_record_spend` runs on the
    persisted document — so `state.json` is repaired by the NEXT spend call and
    not before. A run can reach DONE without one. This reader seeded a bucket
    for every key in the PERSISTED `state.json.spend.by_cycle` and applied no
    prune, so the sealed terminal report shipped exactly the rows the display
    had dropped.

    Driven at HEAD over this run's own archive: `state.json.spend.by_cycle` 29
    keys of which 23 all-zero, `_spend_summary` 6 keys, `_read_spend` 29, and
    REPORT.md carrying `| cycle | 0 | 0 | 0.0 | 0 | 0 | 0 |` through
    `| cycle | 22 | ... |`.

    NO INTERVENING `Foundry-Spend` CALL is the whole point of the fixture: the
    guard in `test_spend.py` passes today only because its fixture calls
    `foundry_record_spend` first, which repairs the document before either
    surface reads it.
    """
    state = _read_json(report_env, "state.json")
    zero = {"tokens": 0, "duration_ms": 0, "agents": 0, "unreported": 0}
    for cycle in ("7", "8", "9"):
        state["spend"]["by_cycle"][cycle] = dict(zero)
    _write_json(report_env, "state.json", state)

    _generate(report_env)

    section = _document(report_env)["spend_per_phase_and_cycle"]
    for cycle in ("7", "8", "9"):
        assert cycle not in section["by_cycle"], section["by_cycle"]
    markdown = _markdown(report_env)
    for cycle in ("7", "8", "9"):
        assert f"| cycle | {cycle} |" not in markdown, markdown
    # The rows that measured something are untouched — the prune is "nothing
    # was measured", never "no spend row exists". Cycles 3-5 have no ledger row
    # at all and stay, because the dispatch record puts an unreported agent in
    # each: the forgotten-`Foundry-Spend` run is the one whose gap most needs a
    # line (D-163).
    assert set(section["by_cycle"]) == {"0", "1", "2", "3", "4", "5"}
    for cycle in ("3", "4", "5"):
        assert section["by_cycle"][cycle]["unreported"] > 0


def test_a_rollup_cycle_that_measured_something_still_reaches_disagreements(
    report_env,
):
    """The other half, and the one that stops the fix over-reaching (D-232).

    Dropping the roll-up arm of the seeding outright would have taken the
    stale-roll-up detector's cycle axis with it: a cycle `state.json.spend`
    records with real tokens and the ledger does not name would no longer get a
    bucket, and a bucket is what the `disagreements` loop iterates. The prune
    keeps it, because it measured something."""
    state = _read_json(report_env, "state.json")
    state["spend"]["by_cycle"]["9"] = {
        "tokens": 640_000, "duration_ms": 600_000, "agents": 2, "unreported": 0,
    }
    _write_json(report_env, "state.json", state)

    _generate(report_env)

    section = _document(report_env)["spend_per_phase_and_cycle"]
    assert "9" in section["by_cycle"], section["by_cycle"]
    assert section["by_cycle"]["9"]["records"] == 0
    fields = {
        d["field"] for d in section["disagreements"]
        if d["scope"] == "by_cycle" and d["key"] == "9"
    }
    assert {"tokens", "duration_ms", "agents"} <= fields, section["disagreements"]


def test_the_report_and_foundry_next_publish_the_same_cycle_axis(report_env):
    """One derivation, two readers — the property the fix is built on.

    `Foundry-Next` (AC-033) and REPORT.md (AC-036) answer "tokens and minutes
    per cycle" from the same ledgers, and they disagreed by construction while
    each applied its own rule to the persisted document. This asserts the KEY
    SETS are equal on a fixture carrying both kinds of bucket — one that
    measured nothing and one that did — so a fix that repaired only one surface
    fails here.
    """
    from foundry_mcp.tools.orchestration.spend import _spend_summary

    state = _read_json(report_env, "state.json")
    state["spend"]["by_cycle"]["7"] = {
        "tokens": 0, "duration_ms": 0, "agents": 0, "unreported": 0,
    }
    state["spend"]["by_cycle"]["8"] = {
        "tokens": 12_000, "duration_ms": 60_000, "agents": 1, "unreported": 0,
    }
    _write_json(report_env, "state.json", state)

    _generate(report_env)

    report_cycles = set(_document(report_env)["spend_per_phase_and_cycle"]["by_cycle"])
    display_cycles = set(_spend_summary(report_env)["by_cycle"])
    assert "7" not in report_cycles and "7" not in display_cycles
    assert "8" in report_cycles and "8" in display_cycles
    # The report's axis is the display's axis plus whatever the LEDGER itself
    # measured — the ledger keeps its authority over tokens and minutes, and a
    # row it measured is never pruned away by a stale roll-up.
    assert display_cycles <= report_cycles, (display_cycles, report_cycles)


def test_the_prune_predicate_is_not_restated_in_this_module(report_env):
    """D-232's class is two surfaces of one rule, so the rule is READ.

    The predicate that decides an all-zero cycle bucket is a leftover rather
    than a measurement (D-229) is stated ONCE, in
    `foundry_state.overlay_unreported`, and `foundry_state.spend_rollup` seeds
    its per-cycle buckets from that function's pruned view rather than
    mirroring the test. A mirrored predicate agrees until the day one side is
    edited, which is the defect.

    GI-024 MOVED WHERE THE RULE LIVES, NOT WHETHER IT IS SHARED. It used to be
    read back OUT of `foundry_orchestrator` through a function-local import —
    `survey/architecture.md` §3.2: "closing the import cycle rather than
    sharing the rule". Now the rule is in the leaf both surfaces already
    import, so this module restates nothing AND reaches nothing above it.
    """
    import inspect as _inspect

    source = _inspect.getsource(fr._read_spend)
    assert "spend_rollup(" in source, source[-2000:]
    for reach in ("from foundry_mcp.tools.foundry_orchestrator import",
                  "from foundry_mcp.tools import foundry_orchestrator"):
        assert reach not in source, (
            f"{reach!r} — the reach back into the monolith is gone; the "
            f"shared rule is in `foundry_state` and this module reaches only "
            f"the leaves. (The docstring still CITES the orchestrator's old "
            f"reader by name, which is history and not an import.)"
        )

    rollup_source = _inspect.getsource(fs.spend_rollup)
    assert "overlay_unreported(" in rollup_source, rollup_source[-2000:]
    for restatement in ('"tokens", "duration_ms", "agents", "unreported"',
                        "not bucket.get("):
        assert restatement not in rollup_source, (
            f"{restatement!r} is `overlay_unreported`'s prune test restated in "
            f"its caller — the mirrored predicate D-232 filed"
        )

    # And the two really do produce one answer: a cycle the roll-up names with
    # nothing in it reaches neither the display's view nor the report's table.
    table = fs.spend_rollup(
        spend_rows=[],
        state_rollup={"by_cycle": {
            "0": {"tokens": 0, "duration_ms": 0, "agents": 0, "unreported": 0},
            "1": {"tokens": 5, "duration_ms": 0, "agents": 1, "unreported": 0},
        }},
    )
    assert sorted(table["by_cycle"]) == ["1"], (
        "the all-zero cycle bucket was re-created by the seeding loop — "
        "D-232/D-235's exact regression"
    )


# --------------------------------------------------------------------------- #
# D-234 — the banner that still described the retired merge
# --------------------------------------------------------------------------- #


def _banner(run_dir: Path) -> str:
    header = _markdown(run_dir).split("\n## ", 1)[0]
    return next(ln for ln in header.splitlines() if " by Foundry-Report." in ln)


def test_the_banner_tells_the_lead_where_prose_actually_survives(report_env):
    """GI-006 / FR-001 — the sentence a lead reads at the moment they choose
    where to type, and it described a merge the seal stopped doing at cycle 27.

    It said "prose may be appended, no section may be removed". A lead who
    followed it and typed a paragraph under `## Verdict matrix` lost it at the
    next terminal transition with `lead_prose_lines: 0` and no warning, because
    `_carried_lead_prose` tells lead prose from generated body by the HEADING
    and cannot do otherwise (D-228). Every sibling surface — the done-gate
    hint, the tool description, `display.py`'s footer, `commands/start.md` and
    both READMEs — was rewritten at that ruling; this banner was the one that
    was not.
    """
    import inspect as _inspect

    _generate(report_env)
    banner = _banner(report_env)

    assert "under your OWN `## ` heading" in banner, banner
    assert "above the first generated section" in banner, banner
    # Named WITHOUT the `## ` prefix — the spelling both READMEs use. In full
    # it would be the seal's `_LEAD_NOTES_HEADING` verbatim, and two
    # seal tests read "the seal appended nothing" as that constant being absent
    # from the whole document; a banner carrying it would make a purely
    # generated report look sealed to them.
    assert "`Lead notes (carried by the seal)`" in banner, banner
    assert "## Lead notes (carried by the seal)" not in banner, banner
    assert "verbatim" in banner, banner
    assert "regenerated away" in banner, banner
    assert "no section may be removed" in banner, banner
    # THE CONVERSE PIN. The retired phrase is the one that sent the lead to the
    # place their prose does not survive, and it is gone from the whole tree of
    # generated documents, not merely reworded around.
    assert "prose may be appended, no section may be removed" not in _markdown(
        report_env
    )
    # Not a grep over the file: the comment at the site QUOTES the retired
    # phrase, which is the house rule for a non-obvious decision (it names what
    # the sentence used to say and what a lead did with it). What must be gone
    # is the phrase as something this function EMITS, so the pin is over
    # `_render_markdown`'s string literals and nothing else.
    import ast as _ast

    literals = [
        node.value
        for node in _ast.walk(_ast.parse(_inspect.getsource(fr._render_markdown)))
        if isinstance(node, _ast.Constant) and isinstance(node.value, str)
    ]
    assert literals, "no string literals found — the pin would pass vacuously"
    assert not any("prose may be appended" in lit for lit in literals), literals


def test_the_generated_banner_is_absorbed_by_the_seal_never_carried_as_prose(
    report_env,
):
    """The adjacent path: the F6 / HALTED seal, not the lead reading the file.

    The seal's `_lead_header_lines` drops a header line carrying
    ` by Foundry-Report.` and keeps every other non-blank one AS THE LEAD'S. So
    the banner must be ONE rendered line containing that marker — split it and
    the half without the marker is carried into
    `## Lead notes (carried by the seal)` on every terminal transition, the
    document growing a paragraph of its own banner per seal. The banner also
    now SPELLS a `## ` heading inside itself, which must not split a block:
    `foundry_state.markdown_sections` matches a whole trimmed line, and this
    asserts it stays that way.
    """
    # GI-010 / GI-026 — the wave-2 destinations. Casting 2's split defines
    # these symbols in the modules named below and its completion report's
    # `## Symbol map` is the authority; where the map and this differ, the
    # map wins and this is the edit.
    from foundry_mcp.tools.foundry_state import markdown_sections
    from foundry_mcp.tools.orchestration.report_seal import (
        _carried_lead_prose,
        _lead_header_lines,
    )

    _generate(report_env)
    generated = _markdown(report_env)

    header, blocks = markdown_sections(generated)
    assert len(blocks) == len(REPORT_REQUIRED_SECTIONS), [h for h, _ in blocks]
    assert sum(1 for ln in header if " by Foundry-Report." in ln) == 1, header
    assert _lead_header_lines(header) == []
    assert _carried_lead_prose(generated, generated) == []

    # And a lead's own line above the first section still survives beside it.
    edited = generated.replace(
        "\n## ", "\n\nthe lead's own line\n\n## ", 1
    )
    assert _carried_lead_prose(edited, generated) == ["the lead's own line"]


# --------------------------------------------------------------------------- #
# FR-027 / FR-053 / AC-047 / OT-041 — THE FOUR SECTIONS A-038 ADDS.
#
# A-038's answer was "All four", and the four are the HARDENING backlog,
# fallout per cycle with the acceptance verdict, stream coverage per cycle with
# replaced records noted, and a halt section printing the reason member and
# text plus the co-dispatch sets per GRIND.
#
# Every register below keeps this module's rule: drive `generate_report`
# against a real run directory and read BOTH written documents back, because
# the property under test is always "what did it WRITE". Each also carries an
# ABSENT-FIELD case, which is FR-054's whole subject: `fallout_of`,
# `supersedes`, `records[]`, the structured `halted_reason` and the co-dispatch
# record are all fields the ledger does not carry yet, and a section that
# refused to render without one would make `Foundry-Phase('done')` unreachable
# for every archive written before this release.
# --------------------------------------------------------------------------- #


def _defect(run_dir: Path, **fields) -> None:
    """Append one defect record to the fixture COPY. Never to the fixture."""
    data = _read_json(run_dir, "defects.json")
    record = {"id": f"D-{len(data['defects']) + 900}", "cycle": 1,
              "source": "prove", "type": "WRONG", "status": "open"}
    record.update(fields)
    data["defects"].append(record)
    _write_json(run_dir, "defects.json", data)


def _observation(run_dir: Path, **fields) -> None:
    path = run_dir / "observations.json"
    data = (
        json.loads(path.read_text(encoding="utf-8"))
        if path.exists() else {"observations": [], "tripwire": []}
    )
    record = {"id": f"O-{len(data['observations']) + 1}", "cycle": 1,
              "source": "prove", "classification": "TEMPER_CANDIDATE",
              "description": "a probe idea", "spec_ref": "", "target_kind": "code",
              "symbol": "", "file": ""}
    record.update(fields)
    data["observations"].append(record)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# AC-024 / AC-022 — the HARDENING backlog.
# ---------------------------------------------------------------------------


def test_the_hardening_backlog_lists_every_open_hardening_defect(report_env) -> None:
    """AC-024 — "a HARDENING backlog section beside the LATENT backlog".

    A HARDENING record is a DRIVEN failure that no requirement asks about
    (GI-014), so the row carries its reproduction: that statement IS the
    evidence the tier rests on, unlike a LATENT row where FR-005 makes it the
    thing that was NOT found.
    """
    _defect(
        report_env, id="D-901", tier="HARDENING", status="open",
        **{"class": "PROBE_WRONG_RESULT"},
        file="src/foundry_mcp/tools/evidence.py", symbol="verify_evidence",
        reproduction_attempted="Drove the sweep with a zero-byte log and it "
                               "reported ok rather than naming the log.",
        description="A zero-byte evidence log passes the sweep.",
        supersedes=None,
    )
    _defect(report_env, id="D-902", tier="HARDENING", status="fixed",
            description="already closed", reproduction_attempted="drove it")

    _generate(report_env)
    section = _document(report_env)["hardening_backlog"]

    assert section["open_count"] == 1, "a fixed record is not backlog"
    assert [d["id"] for d in section["defects"]] == ["D-901"]
    row = section["defects"][0]
    assert row["file"] == "src/foundry_mcp/tools/evidence.py"
    assert row["symbol"] == "verify_evidence"
    assert row["reproduction_attempted"].startswith("Drove the sweep")

    markdown = _markdown(report_env)
    assert "## HARDENING backlog" in markdown
    assert "D-901" in markdown
    assert "Drove the sweep with a zero-byte log" in markdown
    assert "D-902" not in markdown.split("## Unknown-tier defects")[0].split(
        "## HARDENING backlog"
    )[1]


def test_the_hardening_backlog_renders_empty_rather_than_missing(report_env) -> None:
    """FR-054 — a ledger with no HARDENING record renders an EMPTY section.

    A missing section holds the DONE gate shut (`artifacts.report_document_status` reads both
    documents), so "this run filed no HARDENING defect" has to be a rendered
    measurement rather than an absent heading.
    """
    _generate(report_env)
    section = _document(report_env)["hardening_backlog"]

    assert section["open_count"] == 0
    assert section["defects"] == []
    assert "## HARDENING backlog" in _markdown(report_env)
    assert report_document_status(report_env)["present"] is True


def test_a_record_with_no_tier_key_stays_on_the_unknown_sentinel(
    report_env,
) -> None:
    """FR-054 / FR-051 — the absent-field case for this section.

    A record persisted before FR-014 carries no `tier` key at all. It must read
    through the unknown sentinel and BLOCK, never fall into the non-blocking
    HARDENING backlog: "nobody ever classified this" and "a stream drove a
    probe and it failed" are different facts and land in different sections.
    """
    _defect(report_env, id="D-903", status="open", description="untiered")
    _generate(report_env)
    document = _document(report_env)

    assert [d["id"] for d in document["hardening_backlog"]["defects"]] == []
    assert "D-903" in [d["id"] for d in document["unknown_tier_defects"]["defects"]]
    assert TIER_UNKNOWN in document["defects_by_tier_and_status"]["by_tier"]


def test_the_tier_cross_tab_gains_the_hardening_column_by_derivation(
    report_env,
) -> None:
    """AC-022 — the cross-tab walks `DEFECT_TIER_OR_UNKNOWN`, so it just grew.

    Every member is always present including zeros: "0 HARDENING defects" is a
    measurement, and omitting the key would make it indistinguishable from the
    unmeasured case.
    """
    _generate(report_env)
    cross = _document(report_env)["defects_by_tier_and_status"]

    assert set(cross["cross_tab"]) == set(DEFECT_TIER_OR_UNKNOWN)
    assert set(cross["by_tier"]) == set(DEFECT_TIER_OR_UNKNOWN)
    assert cross["by_tier"]["HARDENING"] == 0


# ---------------------------------------------------------------------------
# AC-020 / OT-022 / ST-007 — undriven TEMPER candidates.
# ---------------------------------------------------------------------------


def test_an_undriven_temper_candidate_is_listed_by_name(report_env) -> None:
    """AC-020 — "When TEMPER never ran, the F6 report lists every TEMPER
    candidate that was not driven."

    Listing them on EVERY run is the superset `_read_undriven_temper_candidates`
    gives its reason for, and the fixture is the other half of that argument:
    its run DID cross into F5 (`phase_history` carries the row, and `phase` is
    F5.5) and still left both candidates undriven. That is the same debt
    AC-020 names, and a section that fired only on the never-ran case would
    hide it. The never-ran case itself is driven two tests below.
    """
    _observation(report_env, id="O-1",
                 description="drive the sweep with a symlinked evidence log")
    _observation(report_env, id="O-2", description="drive a zero-cycle run")

    _generate(report_env)
    section = _document(report_env)["hardening_backlog"]

    assert [c["id"] for c in section["undriven_temper_candidates"]] == ["O-1", "O-2"]
    markdown = _markdown(report_env)
    assert "### Undriven TEMPER candidates" in markdown
    assert "drive the sweep with a symlinked evidence log" in markdown
    assert "drive a zero-cycle run" in markdown


@pytest.mark.parametrize(
    "closed", [{"driven": True}, {"status": "DRIVEN"}, {"status": "driven"}],
    ids=["driven-flag", "status-upper", "status-lower"],
)
def test_a_driven_candidate_leaves_the_list(report_env, closed) -> None:
    """ST-007 — a candidate is closed as DRIVEN, filed or clean.

    FR-054: casting 4's door and casting 11's TEMPER land at different waves
    and may spell the closure either way, so both are read. A report that knew
    only one spelling would list a driven candidate as open debt.
    """
    _observation(report_env, id="O-1", description="driven one", **closed)
    _observation(report_env, id="O-2", description="open one")

    _generate(report_env)
    section = _document(report_env)["hardening_backlog"]

    assert [c["id"] for c in section["undriven_temper_candidates"]] == ["O-2"]
    assert section["driven_candidate_count"] == 1


def test_a_candidate_with_no_driven_marker_at_all_reads_as_undriven(
    report_env,
) -> None:
    """FR-054's absent-field case, and the direction matters.

    The observation record shipped today carries NO driven marker, so every
    archive written before this release has none. Absent reads as UNDRIVEN:
    nothing recorded that it was driven, so nothing may claim it was. Reading
    it the other way would silently retire the whole backlog on every existing
    archive.
    """
    _observation(report_env, id="O-1", description="no marker anywhere")
    _generate(report_env)
    section = _document(report_env)["hardening_backlog"]

    assert [c["id"] for c in section["undriven_temper_candidates"]] == ["O-1"]
    assert section["driven_candidate_count"] == 0


def test_a_non_candidate_observation_is_not_in_the_list(report_env) -> None:
    """The four comment-prose classes are a different question entirely."""
    _observation(report_env, id="O-1", classification="LINE_DRIFT_CITE",
                 description="the cite names line 41 and the symbol moved")
    _generate(report_env)

    assert _document(report_env)["hardening_backlog"][
        "undriven_temper_candidates"
    ] == []


def test_an_absent_observations_ledger_is_not_a_refusal(report_env) -> None:
    """An ABSENT ledger is not unreadable — `_refusal`'s standing distinction.

    A run that recorded no observation has no observations.json, and refusing
    to generate its report would make DONE unreachable for it.
    """
    assert not (report_env / "observations.json").exists()
    _generate(report_env)

    assert _document(report_env)["hardening_backlog"][
        "undriven_temper_candidates"
    ] == []


# ---------------------------------------------------------------------------
# fallout D-055 — "TEMPER ran" is a phase history, not a command-line flag.
#
# `state.json.temper` is the `--temper` OPT-IN FLAG: Foundry-Init writes it
# once from the command line and no transition touches it again. Reading it as
# "did TEMPER run" put "TEMPER ran and left 1 recorded candidate(s) undriven"
# on a run whose history stops at F2 — so the "never ran" branch fired only
# for a run that DECLINED temper, and never once for the population AC-020 is
# written about. The three tests below are the three branches the renderer
# already carried and the derivation could not reach.
# ---------------------------------------------------------------------------


def _stop_the_run_before_temper(run_dir: Path) -> None:
    """Rewrite the fixture into the shape D-055 was driven on.

    `temper: true` — the lead did pass `--temper` — with a phase, a history
    and a timing map that all stop at the INSPECT: a run that opted in and
    never crossed into F5. This run's own archive and `daring-orca` are both
    that shape, which is why it is worth building rather than asserting the
    flag read in isolation.
    """
    state = _read_json(run_dir, "state.json")
    assert state["temper"] is True, "the fixture must be an OPTED-IN run"
    before_assay = {"F0", "F0.5", "F0.9", "F1", "F2", "F3"}
    state["phase"] = "F2"
    state["phase_history"] = [
        row for row in state["phase_history"] if row["phase"] in before_assay
    ]
    state["phase_times"] = {
        pid: entry
        for pid, entry in state["phase_times"].items()
        if pid in before_assay
    }
    _write_json(run_dir, "state.json", state)


def test_temper_ran_reads_the_phase_history_and_not_the_opt_in_flag(
    report_env,
) -> None:
    """fallout D-055 / AC-020 — opted in, stopped short, so TEMPER never ran.

    This is the exact population AC-020 is about, and the flag read answered
    it backwards: `temper: true` survives on a run that halted at F2, so the
    report asserted a phase history it had never looked at. The candidate list
    beside the sentence was right the whole time; only the sentence was wrong,
    which is why this asserts BOTH — a fix that reached the flag and not the
    prose would leave the same claim on screen.
    """
    _stop_the_run_before_temper(report_env)
    _observation(report_env, id="O-1", description="drive a zero-cycle run")

    _generate(report_env)
    section = _document(report_env)["hardening_backlog"]

    assert section["temper_ran"] is False
    assert [c["id"] for c in section["undriven_temper_candidates"]] == ["O-1"]

    markdown = _markdown(report_env)
    assert "TEMPER never ran on this run, so all 1 recorded candidate(s)" in markdown
    assert "TEMPER ran and left" not in markdown


def test_a_run_that_crossed_into_f5_reads_as_ran(report_env) -> None:
    """ST-007's other branch — the guard is "TEMPER ran on this run".

    The fixture crossed F4 into F5 and on into F5.5, so its history carries
    the row the derivation reads. Stated as an assertion rather than left to
    the reader, because the whole defect was a claim about this run's history
    made without reading it.
    """
    assert any(
        row["phase"] == fr.TEMPER_PHASE_ID
        for row in _read_json(report_env, "state.json")["phase_history"]
    )
    _observation(report_env, id="O-1", description="drive a symlinked log")
    _observation(report_env, id="O-2", description="already driven", driven=True)

    _generate(report_env)
    section = _document(report_env)["hardening_backlog"]

    assert section["temper_ran"] is True
    markdown = _markdown(report_env)
    assert "TEMPER ran and left 1 recorded candidate(s) undriven; 1 were" in markdown


def test_a_run_with_no_recorded_history_says_so_rather_than_guessing(
    report_env,
) -> None:
    """FR-054 — an archive that recorded no history gets the third branch.

    "Cannot say" and "did not run" are different answers and the section has
    words for both. Reading an absent history as "never ran" would print a
    finding about every pre-release archive; reading it as "ran" would print
    the defect's own sentence again.
    """
    state = _read_json(report_env, "state.json")
    del state["phase_history"]
    _write_json(report_env, "state.json", state)
    _observation(report_env, id="O-1", description="drive a zero-cycle run")

    _generate(report_env)

    assert _document(report_env)["hardening_backlog"]["temper_ran"] is None
    assert (
        "whether TEMPER ran at all is not recorded in this run's state"
        in _markdown(report_env)
    )


# ---------------------------------------------------------------------------
# FR-025 / AC-046's sibling — fallout per cycle with the acceptance verdict.
# ---------------------------------------------------------------------------


def _clear_fallout_across_the_ledger(run_dir: Path) -> None:
    """Give every record an explicit `fallout_of: null`.

    That is what `migrate-archive.py` writes (casting 3), and it is what turns
    a structurally-absent ledger into a MEASURED one. Without it every cycle
    carries unmeasured records and no verdict but `not_measurable` is honest.
    """
    data = _read_json(run_dir, "defects.json")
    for record in data["defects"]:
        record.setdefault("fallout_of", None)
    _write_json(run_dir, "defects.json", data)


def test_fallout_per_cycle_passes_only_on_a_measured_clean_pair(
    report_env,
) -> None:
    """The acceptance figure: zero across the LAST TWO INSPECT cycles."""
    _clear_fallout_across_the_ledger(report_env)
    _generate(report_env)
    section = _document(report_env)["fallout_per_cycle"]

    assert section["verdict"] == "pass", section["verdict_reason"]
    assert len(section["last_two_cycles"]) == 2
    assert section["total"] == 0
    assert section["unmeasured_records"] == 0
    assert "PASS" in _markdown(report_env).split("## Fallout per cycle")[1]


def test_fallout_in_the_closing_pair_fails_the_criterion(report_env) -> None:
    """A filing that is fallout of an earlier defect, in the closing pair."""
    _clear_fallout_across_the_ledger(report_env)
    top = _read_json(report_env, "state.json")["cycle"]
    _defect(report_env, id="D-910", cycle=top, tier="LIVE", status="open",
            fallout_of="D-001", description="fallout of the first fix")

    _generate(report_env)
    section = _document(report_env)["fallout_per_cycle"]

    assert section["verdict"] == "fail", section["verdict_reason"]
    assert section["total"] == 1
    assert section["per_cycle"][str(top)]["fallout"] == 1
    assert section["per_cycle"][str(top)]["ids"] == ["D-910"]
    assert "D-001" in section["verdict_reason"] or str(top) in section[
        "verdict_reason"
    ]
    assert "FAIL" in _markdown(report_env).split("## Fallout per cycle")[1]


def test_an_absent_fallout_field_is_never_a_measured_zero(report_env) -> None:
    """FR-054's absent-field case, and the one that decides the verdict.

    Records written before `fallout_of` existed carry no such key. Counting
    those cycles as "zero fallout" would certify AC-046's sibling criterion on
    an archive that never measured it — the easiest pass in the document, and
    the same fabrication `handoffs_wall_clock_seconds` refuses for the wall
    clock.

    THE PRE-CHANGE LEDGER IS BUILT HERE, NOT BORROWED FROM THE FIXTURE. This
    used to assert that the committed fixture happened to predate the field,
    and then rely on that. It was true when written and stopped being true the
    moment casting 4 landed `fallout_of` on the filing doors and the fixture
    was regenerated from them — so a register about a ledger shape was resting
    on a fixture that had no obligation to keep it. Stripping the key here
    states the shape the test is actually about, and cannot go stale behind it.
    """
    ledger = _read_json(report_env, "defects.json")
    for record in ledger["defects"]:
        record.pop("fallout_of", None)
    _write_json(report_env, "defects.json", ledger)
    for record in _read_json(report_env, "defects.json")["defects"]:
        assert "fallout_of" not in record

    _generate(report_env)
    section = _document(report_env)["fallout_per_cycle"]

    assert section["verdict"] == "not_measurable", section["verdict_reason"]
    assert section["measured_records"] == 0
    assert section["unmeasured_records"] > 0
    assert "structurally absent" in section["verdict_reason"] or (
        "no `fallout_of` key" in section["verdict_reason"]
    )
    assert "NOT MEASURABLE" in _markdown(report_env).split(
        "## Fallout per cycle"
    )[1]


def test_a_run_with_fewer_than_two_inspect_cycles_cannot_pass(report_env) -> None:
    """"A run with fewer than two INSPECT cycles cannot pass and says so."

    The criterion is defined over a PAIR. Reporting `pass` because nothing
    contradicted it would make the figure easiest to satisfy on the runs that
    did the least work.
    """
    _clear_fallout_across_the_ledger(report_env)
    state = _read_json(report_env, "state.json")
    state["cycle"] = 0
    state["inspect_modes"] = []
    _write_json(report_env, "state.json", state)
    data = _read_json(report_env, "defects.json")
    for record in data["defects"]:
        record["cycle"] = 0
    _write_json(report_env, "defects.json", data)
    _write_json(report_env, "stream-rollup.json", {"cycles": {}})

    _generate(report_env)
    section = _document(report_env)["fallout_per_cycle"]

    assert section["verdict"] == "not_measurable"
    assert section["last_two_cycles"] == []
    assert "fewer than two" in section["verdict_reason"]


# ---------------------------------------------------------------------------
# CT-003 / AC-030 / OT-028 — stream coverage per cycle, replacements named.
# ---------------------------------------------------------------------------


def test_stream_coverage_names_a_replaced_record(report_env) -> None:
    """CT-003 — "the result names what it replaced".

    A second recording for the same (stream, cycle) reads as a REPLACEMENT and
    never as extra coverage, which is the whole difference between the replace
    semantics and the additive writer they retire.
    """
    rollup = _read_json(report_env, "stream-rollup.json")
    bucket = rollup["cycles"]["1"]["trace"]
    bucket["records"] = bucket["records"] * 3
    _write_json(report_env, "stream-rollup.json", rollup)

    _generate(report_env)
    section = _document(report_env)["stream_coverage_per_cycle"]

    row = next(r for r in section["rows"]
               if r["cycle"] == "1" and r["stream"] == "trace")
    assert row["record_count"] == 3
    assert row["replaced_count"] == 2, "the FIRST record replaced nothing"
    assert {"cycle": "1", "stream": "trace", "replaced_count": 2} in section[
        "replaced"
    ]
    assert section["replaced_record_count"] == 2

    markdown = _markdown(report_env).split("## Stream coverage per cycle")[1]
    assert "trace" in markdown
    assert "replaced" in markdown.lower()


def test_stream_coverage_renders_an_over_total_bucket_without_clamping(
    report_env,
) -> None:
    """FR-054 — daring-orca's buckets genuinely read above 100%.

    Render what is there and NAME it. A coverage figure quietly clamped to its
    total is a measurement replaced by an assertion.
    """
    rollup = _read_json(report_env, "stream-rollup.json")
    rollup["cycles"]["1"]["prove"]["items_checked"] = 213
    _write_json(report_env, "stream-rollup.json", rollup)

    _generate(report_env)
    section = _document(report_env)["stream_coverage_per_cycle"]

    row = next(r for r in section["rows"]
               if r["cycle"] == "1" and r["stream"] == "prove")
    assert row["items_checked"] == 213 and row["items_total"] == 71
    assert row["over_total"] is True
    assert section["over_total"], "the over-total row is NAMED, not normalised"
    assert "213" in _markdown(report_env).split(
        "## Stream coverage per cycle"
    )[1]


def test_a_bucket_with_no_records_list_is_the_additive_writers_shape(
    report_env,
) -> None:
    """FR-054's absent-field case for this section.

    A bucket written before the replace semantics carries no `records[]` at
    all. `record_count` 0 is not the same fact as "recorded once", so it is
    listed rather than reported as a single un-replaced tranche.
    """
    rollup = _read_json(report_env, "stream-rollup.json")
    del rollup["cycles"]["1"]["trace"]["records"]
    rollup["cycles"]["1"]["trace"]["items_checked"] = 48
    _write_json(report_env, "stream-rollup.json", rollup)

    _generate(report_env)
    section = _document(report_env)["stream_coverage_per_cycle"]

    # No `records` key means the value is no longer a stream tranche by
    # `is_stream_record`'s rule (D-182), so the pair drops out of the table
    # entirely rather than being reported as coverage nobody can source.
    assert not [r for r in section["rows"]
                if r["cycle"] == "1" and r["stream"] == "trace"]
    assert any(r["stream"] == "prove" for r in section["rows"]), (
        "the siblings in the same bucket are unaffected"
    )


def test_stream_coverage_ignores_the_cycle_level_facts_beside_the_tranches(
    report_env,
) -> None:
    """D-182 — the test is on the VALUE, never a denylist of key names.

    `inspect_mode`, `stream_scope`, `evidence_sweep` and `temper_entry` live in
    the same cycle bucket as the stream records, and the next C-6 field would
    be a fifth. `measure-run.py` never learned the rule at all and reported
    this run's own roll-up as eight failure tokens and exit 1.
    """
    rollup = _read_json(report_env, "stream-rollup.json")
    rollup["cycles"]["1"]["a_field_nobody_has_invented_yet"] = "FULL"
    _write_json(report_env, "stream-rollup.json", rollup)

    _generate(report_env)
    section = _document(report_env)["stream_coverage_per_cycle"]

    assert not [r for r in section["rows"]
                if r["stream"] == "a_field_nobody_has_invented_yet"]


def test_stream_coverage_renders_empty_on_a_run_with_no_rollup(
    report_env,
) -> None:
    """An absent roll-up renders an empty section, never a missing one."""
    (report_env / "stream-rollup.json").unlink()
    _generate(report_env)
    section = _document(report_env)["stream_coverage_per_cycle"]

    assert section["rows"] == []
    assert section["row_count"] == 0
    assert "## Stream coverage per cycle" in _markdown(report_env)
    assert report_document_status(report_env)["present"] is True


# ---------------------------------------------------------------------------
# CT-004 / CT-008 / AC-025's report half — the halt and the co-dispatch sets.
# ---------------------------------------------------------------------------


def test_the_halt_section_prints_the_member_and_the_leads_own_text(
    report_env,
) -> None:
    """CT-004 — the reason as a MEMBER of HALT_REASONS plus the free text.

    The member is what a grouper reads and the text is why THIS run ended,
    which no closed set can carry. Neither substitutes for the other.
    """
    state = _read_json(report_env, "state.json")
    state["phase"] = RUN_PHASE_HALTED
    state["halted_at_cycle"] = 5
    state["halted_reason"] = {
        "reason": "lead_ruling",
        "text": "the remaining backlog is all HARDENING and the spec is met",
    }
    _write_json(report_env, "state.json", state)

    _generate(report_env)
    section = _document(report_env)["halt_and_co_dispatch"]

    assert section["halted"] is True
    assert section["reason"] == "lead_ruling"
    assert section["reason_text"].startswith("the remaining backlog")
    assert section["halted_at_cycle"] == 5

    markdown = _markdown(report_env).split("## Halt and co-dispatch")[1]
    assert "lead_ruling" in markdown
    assert "the remaining backlog is all HARDENING" in markdown


def test_the_halt_section_reads_the_legacy_free_string(report_env) -> None:
    """FR-054's absent-field case: `halted_reason` is a bare f-string today.

    Read BOTH shapes, printing the member when there is one and the raw text
    when there is not — and never a member guessed out of a sentence. Coercing
    "--max-cycles 2 reached" onto `cap_reached` would reclassify a run's ending
    by reading its prose.
    """
    state = _read_json(report_env, "state.json")
    state["phase"] = RUN_PHASE_HALTED
    state["halted_at_cycle"] = 2
    state["halted_reason"] = (
        "--max-cycles 2 reached: opening GRIND cycle 3 would exceed it"
    )
    _write_json(report_env, "state.json", state)

    _generate(report_env)
    section = _document(report_env)["halt_and_co_dispatch"]

    assert section["reason"] is None, "no member is guessed out of the sentence"
    assert section["reason_text"] == (
        "--max-cycles 2 reached: opening GRIND cycle 3 would exceed it"
    )

    markdown = _markdown(report_env).split("## Halt and co-dispatch")[1]
    assert "--max-cycles 2 reached" in markdown
    assert "None" not in markdown.split("## ")[0].splitlines()[0], (
        "D-151's class: a Python None must never be interpolated into "
        "operator prose as a fact"
    )


def test_a_bare_member_string_carries_no_invented_text(report_env) -> None:
    """A recognised member as a BARE string has no text of its own.

    Printing the member twice — once as the member and once as "the lead's own
    words" — would invent a sentence the lead never typed.
    """
    state = _read_json(report_env, "state.json")
    state["phase"] = RUN_PHASE_HALTED
    state["halted_reason"] = "cap_reached"
    _write_json(report_env, "state.json", state)

    _generate(report_env)
    section = _document(report_env)["halt_and_co_dispatch"]

    assert section["reason"] == "cap_reached"
    assert section["reason_text"] is None


def test_the_halt_section_says_a_run_did_not_halt(report_env) -> None:
    """An unhalted run renders the section, and says what its emptiness means.

    A halt is reached by a SUCCESSFUL transition (ST-001), so the absence of
    one is not a refusal that happened — it is a run that ended another way.
    """
    _generate(report_env)
    section = _document(report_env)["halt_and_co_dispatch"]

    assert section["halted"] is False
    assert section["reason"] is None
    assert "did not halt" in _markdown(report_env).split(
        "## Halt and co-dispatch"
    )[1]


def test_the_halt_section_lists_the_co_dispatch_sets_per_grind(
    report_env,
) -> None:
    """CT-008 — the sets the SERVER computed, read and never recomputed.

    A report that rebuilt them from the manifest would be a second answer to
    "which castings went out together", available to disagree with the one the
    lead actually acted on.
    """
    with (report_env / "handoffs.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({
            "event": "grind_dispatched", "cycle": 3, "phase": "F3",
            "timestamp": "2026-09-02T06:00:00+00:00",
            "co_dispatch": ["casting-2", "casting-10"],
            "defect_ids": ["D-101"], "requirement_ids": ["FR-008"],
        }) + "\n")

    _generate(report_env)
    section = _document(report_env)["halt_and_co_dispatch"]

    assert section["co_dispatch_count"] == 1
    row = section["co_dispatch"][0]
    assert row["co_dispatch"] == ["casting-2", "casting-10"]
    assert row["cycle"] == 3 and row["defect_ids"] == ["D-101"]

    markdown = _markdown(report_env).split("## Halt and co-dispatch")[1]
    assert "casting-2, casting-10" in markdown
    assert "D-101" in markdown


def test_a_run_with_no_co_dispatch_record_renders_an_empty_table(
    report_env,
) -> None:
    """FR-054's absent-field case: casting 2 lands the writer in wave 2.

    Until then no handoff carries `co_dispatch` at all, and the section must
    render an empty table rather than refusing — which is every run today.
    """
    _generate(report_env)
    section = _document(report_env)["halt_and_co_dispatch"]

    assert section["co_dispatch"] == []
    assert section["co_dispatch_count"] == 0
    assert "_None recorded._" in _markdown(report_env).split(
        "## Halt and co-dispatch"
    )[1]


# ---------------------------------------------------------------------------
# AC-046 — the FULL-cycle ratio, stated pass/fail against 50%.
# ---------------------------------------------------------------------------


def test_the_report_states_the_full_cycle_ratio_against_fifty_percent(
    report_env,
) -> None:
    """AC-046 — "the F6 report states pass/fail against 50%".

    `measure-run.py` emits the same figure (casting 3) from the same
    `foundry_state.full_cycle_ratio`, so the two documents cannot disagree
    about an acceptance criterion.
    """
    from foundry_mcp.tools.foundry_state import full_cycle_ratio

    _generate(report_env)
    section = _document(report_env)["inspect_modes_per_cycle"]
    ratio = section["full_cycle_ratio"]

    assert ratio["threshold"] == 0.5
    assert ratio["total_cycles"] == section["cycle_count"]
    assert ratio["ratio"] == round(
        ratio["full_cycles"] / ratio["total_cycles"], 4
    )
    assert ratio["passes"] is (ratio["ratio"] < 0.5)
    assert ratio == full_cycle_ratio({"per_cycle": section["per_cycle"]}), (
        "one derivation, two renderings — the report re-derives nothing"
    )

    markdown = _markdown(report_env).split("## INSPECT mode per cycle")[1]
    assert ("PASS" in markdown) or ("FAIL" in markdown)
    assert "FULL-cycle ratio" in markdown


def test_a_cycle_counts_as_full_when_any_decision_in_it_was_full(
    report_env,
) -> None:
    """The axis is CYCLES, not decisions (D-119's sibling).

    One cycle can carry two decisions — the F2 INSPECT and the F5 one TEMPER
    opens without advancing the counter — and counting decisions would make a
    run that reopened one cycle at FULL look wider than a run that ran two
    cycles at FULL.
    """
    state = _read_json(report_env, "state.json")
    state["inspect_modes"] = [
        {"cycle": 0, "phase": "F2", "mode": "DELTA", "rule": "delta"},
        {"cycle": 1, "phase": "F2", "mode": "DELTA", "rule": "delta"},
        {"cycle": 1, "phase": "F5", "mode": "FULL", "rule": "first_of_phase"},
    ]
    _write_json(report_env, "state.json", state)

    _generate(report_env)
    ratio = _document(report_env)["inspect_modes_per_cycle"]["full_cycle_ratio"]

    assert ratio["total_cycles"] == 2
    assert ratio["full_cycles"] == 1
    assert ratio["ratio"] == 0.5
    assert ratio["passes"] is False, "0.5 is not BELOW 0.5"


def test_an_unmeasurable_ratio_is_stated_in_words(report_env) -> None:
    """D-151's class. `passes` is None, never False, when no width was recorded.

    "No INSPECT recorded a width" and "more than half were FULL" are different
    answers, and a pass/fail marker cannot carry both.
    """
    state = _read_json(report_env, "state.json")
    state["inspect_modes"] = []
    _write_json(report_env, "state.json", state)

    _generate(report_env)
    ratio = _document(report_env)["inspect_modes_per_cycle"]["full_cycle_ratio"]

    assert ratio["passes"] is None
    assert ratio["ratio"] is None
    markdown = _markdown(report_env).split("## INSPECT mode per cycle")[1]
    assert "NOT MEASURABLE" in markdown


# ---------------------------------------------------------------------------
# AC-047 / OT-041 — the four sections, as a set.
# ---------------------------------------------------------------------------


def test_all_four_new_sections_reach_both_documents(report_env) -> None:
    """OT-041 — "The F6 report carries the HARDENING backlog, fallout per
    cycle, stream coverage with replaced records, and halt reason plus
    co-dispatch sets."

    Both documents, because `artifacts.report_document_status` reads both and the DONE gate
    refuses on the union: a section in the JSON and not the markdown is a
    section the gate reports missing.
    """
    _generate(report_env)
    document = _document(report_env)
    markdown = _markdown(report_env)

    for key, title in (
        ("hardening_backlog", "HARDENING backlog"),
        ("fallout_per_cycle", "Fallout per cycle"),
        ("stream_coverage_per_cycle", "Stream coverage per cycle"),
        ("halt_and_co_dispatch", "Halt and co-dispatch"),
    ):
        assert key in document, key
        assert key in REPORT_REQUIRED_SECTIONS, key
        assert f"## {title}" in markdown, title

    assert len(REPORT_REQUIRED_SECTIONS) == 16
    status = report_document_status(report_env)
    assert status["present"] is True
    assert status["missing_sections"] == []


# --------------------------------------------------------------------------- #
# fallout AC-044 / D-039 — the span table's F6 half.
#
# "The span table appears in the F0.9 output AND in the F6 report." The F0.9
# half shipped and was driven; the F6 half did not exist, while
# `foundry_validate._render_span_table`'s own docstring asserted that "the F6
# report draws the same table from the same records" — a producing side
# documenting a consumer nobody had written.
#
# Every register below drives `generate_report` and reads BOTH documents back,
# because the property is what it WROTE: `artifacts.report_document_status` reads the JSON keys
# AND the markdown headings, and the DONE gate refuses on the union.
# --------------------------------------------------------------------------- #


def _with_manifest(run_dir: Path, castings: list, *, spec: str = "",
                   schema_version: int = 4, top_reason: dict | None = None) -> Path:
    """Give a run dir a manifest, a spec and an archive schema marker."""
    (run_dir / "castings").mkdir(parents=True, exist_ok=True)
    manifest: dict = {"castings": castings, "waves": []}
    if top_reason is not None:
        manifest["split_reason"] = top_reason
    (run_dir / "castings" / "manifest.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )
    if spec:
        (run_dir / "spec.md").write_text(spec, encoding="utf-8")
    state = _read_json(run_dir, "state.json")
    state["archive_schema_version"] = schema_version
    (run_dir / "state.json").write_text(json.dumps(state, indent=2), encoding="utf-8")
    return run_dir


def test_the_span_table_reaches_both_documents(report_env) -> None:
    """fallout AC-044 — the F6 half exists, with the F0.9 half's own rows.

    The section carries `rows` for a reader that will render them and `text`
    for the markdown, and `text` IS `_render_span_table`'s block: one
    computation, two surfaces, so the owners a lead reads at F0.9 and the
    owners they read at F6 cannot differ.
    """
    _with_manifest(
        report_env,
        [
            {"id": 1, "requirement_ids": ["FR-001", "FR-002"], "spec_text": ""},
            {"id": 2, "requirement_ids": ["FR-002"], "spec_text": ""},
        ],
        spec="- **FR-001** a thing\n- **FR-002** another\n- **FR-003** unowned\n",
    )
    _generate(report_env)
    document = _document(report_env)
    markdown = _markdown(report_env)

    assert "requirement_span" in REPORT_REQUIRED_SECTIONS
    section = document["requirement_span"]
    assert section["threshold"] == 2
    assert section["not_computable"] is False
    assert {r["id"]: r["span"] for r in section["rows"]} == {
        "FR-001": 1, "FR-002": 2, "FR-003": 0,
    }
    assert {r["id"]: r["owners"] for r in section["rows"]}["FR-002"] == [1, 2]
    assert section["over_threshold"] == []

    assert "## Requirement span" in markdown
    assert "| requirement | owners | span | recorded reason |" in markdown
    assert "| FR-002 | #1, #2 | 2 |" in markdown
    # The JSON's `text` is the block the markdown carries — not a second
    # rendering of the same rows, which is how two columns come to differ
    # while the data agrees.
    assert section["text"] in markdown


def test_the_span_section_names_a_requirement_over_the_threshold(report_env) -> None:
    """fallout GI-018 — above two owners with no recorded reason, named.

    REPORTED, never refused. F0.9 is the gate; a run waived past a wide span
    there must still reach DONE, and this section is where the waiver stays
    visible instead of being re-litigated at F6.
    """
    _with_manifest(
        report_env,
        [
            {"id": 1, "requirement_ids": ["FR-001"]},
            {"id": 2, "requirement_ids": ["FR-001"]},
            {"id": 3, "requirement_ids": ["FR-001", "FR-002"]},
            {"id": 4, "requirement_ids": ["FR-002"]},
            {"id": 5, "requirement_ids": ["FR-002"]},
        ],
        spec="- **FR-001** wide\n- **FR-002** wide but explained\n",
        top_reason={"FR-002": "two surfaces the spec names separately"},
    )
    result = _generate(report_env)
    assert result["ok"] is True, "the span is reported, never refused (GI-018)"

    section = _document(report_env)["requirement_span"]
    assert section["over_threshold"] == ["FR-001"]
    assert section["recorded"] == ["FR-002"]

    markdown = _markdown(report_env)
    assert "above the threshold with no recorded reason:** FR-001" in markdown
    assert "two surfaces the spec names separately" in markdown


def test_the_span_section_says_not_computable_rather_than_empty(report_env) -> None:
    """fallout FR-054 — an archive below the schema floor is NOT COMPUTABLE.

    An empty table reads as "no requirements", which is a different and
    alarming claim than "this archive predates the field the span is computed
    from". The sentence is `_render_span_table`'s own, so F0.9 and F6 say it
    the same way.
    """
    _with_manifest(
        report_env,
        [{"id": 1, "spec_text": "- **FR-001** a thing"}],
        spec="- **FR-001** a thing\n",
        schema_version=3,
    )
    _generate(report_env)
    section = _document(report_env)["requirement_span"]

    assert section["not_computable"] is True
    assert section["rows"] == []
    assert "not computable" in section["text"]
    assert "not computable" in _markdown(report_env)


def test_the_span_section_renders_a_run_with_no_manifest_at_all(report_env) -> None:
    """fallout FR-054 — an ABSENT manifest is an empty section, not a refusal.

    The generator's rule everywhere else: absent means an empty section,
    present-and-undecodable means a named refusal. A run halted before F0.5
    legitimately has no manifest, and refusing its report would make
    `Foundry-Phase('done')` unreachable for it.
    """
    assert not (report_env / "castings" / "manifest.json").exists()
    result = _generate(report_env)
    assert result["ok"] is True

    section = _document(report_env)["requirement_span"]
    assert section["rows"] == []
    assert section["count"] == 0
    assert section["not_computable"] is False
    assert "## Requirement span" in _markdown(report_env)
    assert report_document_status(report_env)["missing_sections"] == []


def test_an_undecodable_manifest_refuses_by_name(report_env) -> None:
    """The other half of the same rule: present-and-broken is a refusal.

    Named, so a lead knows which artifact to repair — the house shape every
    other `_read_*` in this module follows.
    """
    (report_env / "castings").mkdir(parents=True, exist_ok=True)
    (report_env / "castings" / "manifest.json").write_text("{ not json",
                                                           encoding="utf-8")
    result = generate_report(report_env.parent.parent, report_env)
    assert result["ok"] is False
    assert "manifest.json" in result["error"]
    assert "castings/manifest.json" in result["hint"]


def test_the_span_rows_are_the_f0_9_gates_own_computation(report_env) -> None:
    """fallout AC-044 — "the same table", asserted as the same CALL.

    Driven rather than compared field by field: the section's rows must equal
    what `foundry_validate._requirement_span_rows` returns for the same
    manifest. A mirrored computation here would have to reproduce that
    function exactly to pass — which is the drift, not an escape from it.
    """
    from foundry_mcp.tools.foundry_validate import (
        _owned_requirement_ids,
        _recorded_split_reasons,
        _requirement_span_rows,
    )

    castings = [
        {"id": 1, "requirement_ids": ["AC-044", "FR-013"]},
        {"id": 7, "requirement_ids": ["AC-044"]},
    ]
    _with_manifest(report_env, castings, spec="- **AC-044** the span table\n")
    _generate(report_env)

    manifest = json.loads(
        (report_env / "castings" / "manifest.json").read_text(encoding="utf-8")
    )
    expected = _requirement_span_rows(
        {"AC-044"},
        castings,
        {str(c["id"]): _owned_requirement_ids(c) for c in castings},
        _recorded_split_reasons(manifest, castings),
    )
    assert _document(report_env)["requirement_span"]["rows"] == expected
