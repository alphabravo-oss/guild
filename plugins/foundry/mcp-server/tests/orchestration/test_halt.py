"""HALTED: the token, the reason vocabulary and the cap that reaches it.

Carved from `tests/test_orchestrator_gates.py` (fallout FR-005 / GI-026 /
AC-014 / OT-016): one test module per shipped orchestration module, landed in
the same casting as the source move so no pin is ever left pointing at a module
that no longer exists.
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path


from foundry_mcp.schemas.vocab import RUN_PHASE_HALTED
from foundry_mcp.tools import artifacts, foundry_state

# fallout FR-004 / AC-013 — THE MODULE OBJECTS, UNDER UNDERSCORE ALIASES.
#
# `streams`, `spend`, `width`, `gates`, `directives` and `teams` are all LOCAL
# variable names somewhere in this suite, and a local rebinding shadows a
# module for the rest of its function. The aliases are what `ORCHESTRATION`,
# `owning_module` and every `monkeypatch.setattr` resolve through; individual
# SYMBOLS are imported by name below, which is how the carved modules read.
from foundry_mcp.tools.orchestration import directives as _directives
from foundry_mcp.tools.orchestration import escalation as _escalation
from foundry_mcp.tools.orchestration import evidence_boundary as _evidence_boundary
from foundry_mcp.tools.orchestration import fix_gate as _fix_gate
from foundry_mcp.tools.orchestration import gates as _gates
from foundry_mcp.tools.orchestration import guidance as _guidance
from foundry_mcp.tools.orchestration import halt as _halt
from foundry_mcp.tools.orchestration import report_seal as _report_seal
from foundry_mcp.tools.orchestration import spend as _spend
from foundry_mcp.tools.orchestration import streams as _streams
from foundry_mcp.tools.orchestration import teams as _teams
from foundry_mcp.tools.orchestration import transitions as _transitions
from foundry_mcp.tools.orchestration import width as _width

# fallout AC-014 — THE TWO SIBLING SUITES THE CARVE MUST KEEP REACHING.
#
# `test_observations` owns the never-demote corpus and `test_spawn_progress`
# owns the shipped-source-tree derivation. Both are imported rather than copied,
# for the reason the monolith imported them: a parity test that owned its own
# copy of the corpus would keep passing while the two corpora drifted, and two
# scans over "the shipped source" must not be able to disagree about what that
# is. If either renames a symbol the ImportError says so by name, which is the
# loud failure rather than the silent one.

#: fallout FR-004 / AC-014 — WHAT `fo` USED TO MEAN, NOW THAT IT MEANS THIRTEEN
#: THINGS.
#:
#: Every pin that read `Path(fo.__file__).read_text()` was asking about THE
#: ORCHESTRATOR. That is thirteen files now, so the honest translation of the
#: question is all thirteen — and it stays the honest translation when a
#: fourteenth is added, which a hand-listed pair of modules would not.
ORCHESTRATION = (
    _report_seal, _escalation, _streams, _teams, _width, _evidence_boundary,
    _spend, _halt, _gates, _transitions, _fix_gate, _directives, _guidance,
)


def orchestration_source() -> str:
    """The concatenated source of every shipped orchestration module."""
    return chr(10).join(
        Path(m.__file__).read_text(encoding="utf-8") for m in ORCHESTRATION
    )


def owning_module(symbol: str):
    """The orchestration module that DEFINES `symbol`.

    A patch has to reach the module each CALLER resolves the name through, and
    after the carve that is a binding per importer rather than one module
    attribute. Patching only the module that defines a symbol leaves every
    importer on the real one, which is the silent half of a broken pin.
    """
    for module in ORCHESTRATION:
        value = vars(module).get(symbol)
        if value is None:
            continue
        if getattr(value, "__module__", module.__name__) == module.__name__:
            return module
    for module in ORCHESTRATION:
        if symbol in vars(module):
            return module
    raise AssertionError(f"no orchestration module defines {symbol!r}")


def patch_everywhere(monkeypatch, name: str, value) -> None:
    """Patch `name` in EVERY module that carries it.

    fallout FR-004 / AC-014 — WHAT A MODULE-ATTRIBUTE PATCH USED TO MEAN.

    There was one module, so patching it patched the only binding. After the
    carve a symbol is imported BY NAME into each caller's namespace, so patching
    the module that DEFINES it leaves every importer resolving the real one —
    and a patch that reaches some callers and not others is worse than no patch,
    because the drive then exercises a state no run can be in. This patches
    every binding, which is the same fact the single module used to make true by
    construction.
    """
    for module in (*ORCHESTRATION, artifacts, foundry_state):
        if name in vars(module):
            monkeypatch.setattr(module, name, value)


def orchestration_has(symbol: str) -> bool:
    """True when any orchestration module carries `symbol`."""
    return any(symbol in vars(m) for m in ORCHESTRATION)

from tests.orchestration._env import (  # noqa: F401
    _defect_ledger,
    _write_state,
    run_env,
)

from foundry_mcp.tools.orchestration.guidance import (  # noqa: F401
    foundry_next_action,
)

# fallout GI-033 / AC-061 (D-021 / D-035) — the halted READ and its refusal
# moved into `gates.py`, where their only two callers are, and the cap
# normaliser into the leaf. The pin below still drives all three together,
# because "one read" is a claim about the answer, not about the file.
from foundry_mcp.tools.foundry_state import (  # noqa: F401
    persisted_max_cycles as _persisted_max_cycles,
)

from foundry_mcp.tools.orchestration.gates import (  # noqa: F401
    _halted_refusal,
    _halted_state,
)

from tests.orchestration._env import (  # noqa: F401
    _init_schema,
)




def test_every_reader_of_the_cap_reports_the_number_the_halt_acted_on(run_env):
    """THE ADJACENT PATH: the DISPLAY readers of the same field.

    `_persisted_max_cycles` is the one read, for `_current_inspect_mode`'s
    reason — a value each door normalises for itself is a value each door can
    normalise differently. `_halted_state` feeds every HALTED refusal and
    `foundry_next_action`'s halt block feeds the operator's screen; both used to
    print the raw field. A refusal naming `max_cycles 2.0` beside a decision
    made on `2` is a message about a number no code acted on.
    """
    project_root, fdir = run_env
    _write_state(
        fdir, phase=RUN_PHASE_HALTED, cycle=2, max_cycles=2.0,
        halted_at_cycle=2, halted_reason="--max-cycles 2 reached",
    )
    _defect_ledger(fdir, [])

    # `2.0 == 2` is True in Python, so equality alone cannot tell the raw field
    # from the normalised one. The TYPE is what a reader sees: an operator
    # reading "max_cycles 2.0" is reading a value no code acted on.
    for surface, value in (
        ("_halted_state", _halted_state(fdir)["max_cycles"]),
        ("_halted_refusal", _halted_refusal(
            fdir, "Foundry-Phase('grind_start')")["max_cycles"]),
        ("Foundry-Next", foundry_next_action(project_root)["details"]["max_cycles"]),
    ):
        assert value == 2, (surface, value)
        assert isinstance(value, int), (surface, repr(value))




def test_a_cap_that_is_not_a_whole_number_is_no_cap_and_says_so_at_the_door(run_env):
    """The set the door ACCEPTS and the set the read HONOURS are now the same set.

    `2.5`, `'2'` and `True` are refused at the door — Draft 2020-12 rejects a
    fractional float, a string and a bool for `type: integer` — and each reads
    as no cap here, which is the only answer a function consulted from inside a
    transition can give. What matters is that no value is accepted by one and
    discarded by the other, which is the whole of D-225's class.
    """
    from foundry_mcp import server as srv

    schema = _init_schema()
    for rejected in (2.5, "2", True, -1):
        assert srv._argument_refusal(
            "Foundry-Init", schema, {"max_cycles": rejected}
        ) is not None, rejected
        assert _persisted_max_cycles({"max_cycles": rejected}) == 0, rejected
    for accepted in (0, 2, 2.0, 99):
        assert srv._argument_refusal(
            "Foundry-Init", schema, {"max_cycles": accepted}
        ) is None, accepted
    assert _persisted_max_cycles({"max_cycles": 2.0}) == 2
    assert _persisted_max_cycles({}) == 0


# --------------------------------------------------------------------------- #
# fallout ST-001 / FR-046 / CT-004 (D-160, lead ruling
# `lead_ruling_st_001_vs_fr_046`) — A HALT WITH NO REPORT ANNOUNCES ITSELF.
# --------------------------------------------------------------------------- #


def test_a_halt_whose_report_could_not_be_written_is_never_a_clean_seal(run_env):
    """fallout ST-001 / fallout FR-046 / fallout CT-004 (D-160) — the ruling, both halves.

    fallout ST-001's guard column names three conditions and fallout FR-046
    forbids the third
    being a refusal ("refuses ONLY on `_halt_preconditions`"). The lead ruling
    made on casting 2's GRIND cycle-5 dispatch settles it in fallout FR-046's
    favour —
    a run that cannot write its report must still be able to STOP — and asks in
    exchange that the incompleteness be legible "at the surfaces a human or a
    later door actually reads", because a caller reading only `ok` sees success.

    DRIVEN: `Foundry-Phase('halt', reason='spec_change_required', text=...)`
    against a run with a deliberately corrupt verdicts.json returns ok True,
    phase HALTED and no REPORT.md on disk. Everything below is what the run says
    about that afterwards.
    """
    project_root, fdir = run_env
    _write_state(fdir, phase="F3", cycle=2)
    _defect_ledger(fdir, [])
    (fdir / "verdicts.json").write_text("{ not json", encoding="utf-8")

    sealed = _transitions.foundry_mark_phase_complete(
        "halt", project_root, reason="spec_change_required",
        text="the spec needs an edit before this run can continue",
    )

    # fallout FR-046's half: the transition HAPPENED. A refusal here would strand the
    # run neither halted nor reported, which is worse than the state the guard
    # is trying to prevent.
    assert sealed["ok"] is True, sealed
    assert sealed["phase"] == RUN_PHASE_HALTED, sealed
    assert json.loads((fdir / "state.json").read_text())["phase"] == RUN_PHASE_HALTED
    assert not (fdir / "REPORT.md").exists()

    # fallout ST-001's half, surface by surface. (1) The transition's own display:
    # `display.py#_fmt_foundry_mark_phase_complete` renders `message` and no
    # other field, so a fact absent from it is a fact the operator never sees.
    assert sealed["report_generated"] is False, sealed
    assert sealed["report_error"], sealed
    assert "could NOT be generated" in sealed["message"], sealed["message"]
    assert "Foundry-Report" in sealed["message"], sealed["message"]

    # (2) The record persists, so a later door does not have to have been
    # watching when the seal happened.
    assert json.loads((fdir / "state.json").read_text())["halted_report_error"]

    # (3) The surface that reports the run's ending. THIS is the one the ruling
    # names and the one D-160 reached: the run most likely to halt with no
    # report is the run whose ledger will not render, and `foundry_next_action`
    # returns through the CORRUPT-ARTIFACT early return on exactly that run —
    # never reaching `_compute_next_action`'s HALTED branch, which is where the
    # missing report used to be named (D-171). Before this fix that response
    # carried `heading_for: HALTED` and nothing whatever about the report.
    nxt = foundry_next_action(project_root)
    assert nxt.get("corrupt_artifacts"), nxt
    assert nxt["heading_for"] == RUN_PHASE_HALTED, nxt
    assert nxt["report_written"] is False, nxt

    # (4) And it is not a stale record: repairing the cause and regenerating
    # flips it, because the FILE's presence is the ground truth rather than
    # whatever the seal recorded at the time.
    (fdir / "verdicts.json").write_text('{"requirements": []}', encoding="utf-8")
    (fdir / "REPORT.md").write_text("# regenerated\n", encoding="utf-8")
    assert foundry_next_action(project_root)["report_written"] is True


def test_a_run_that_has_not_ended_reports_no_verdict_on_its_report(run_env):
    """fallout CT-007 (D-160) — the control: `report_written` is a fact about an
    ENDING, and a live run has not had one.

    "Was the report written" is not a question about a run still going: a live
    run's REPORT.md is whatever the last `Foundry-Report` left, and answering
    False for it would read as a failure that has not happened. None is the
    honest answer and it is PRESENT rather than absent, on the same rule
    `heading_for` follows — a field a reader has to test for is the same as not
    having it (D-066).
    """
    project_root, fdir = run_env
    _write_state(fdir, phase="F2", cycle=1, max_cycles=3)
    _defect_ledger(fdir, [])

    live = foundry_next_action(project_root)
    assert live["heading_for"] == "DONE", live
    assert "report_written" in live, sorted(live)
    assert live["report_written"] is None, live

    # ...and a run directory that does not exist has no outlook at all, which
    # is the third arm the field has to answer for.
    foundry_state.clear_active_run()
    try:
        empty = Path(tempfile.mkdtemp())
        none_run = foundry_next_action(str(empty))
        assert "report_written" in none_run, sorted(none_run)
        assert none_run["report_written"] is None, none_run
    finally:
        foundry_state.set_active_run(fdir.name)
