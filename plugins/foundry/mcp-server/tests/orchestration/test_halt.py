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
from foundry_mcp.tools import foundry_state

# fallout FR-004 / AC-013 — THE MODULE OBJECTS, UNDER UNDERSCORE ALIASES.
#
# `streams`, `spend`, `width`, `gates`, `directives` and `teams` are all LOCAL
# variable names somewhere in this suite, and a local rebinding shadows a
# module for the rest of its function. The aliases are what `ORCHESTRATION`,
# `owning_module` and every `monkeypatch.setattr` resolve through; individual
# SYMBOLS are imported by name below, which is how the carved modules read.
from foundry_mcp.tools.orchestration import transitions as _transitions

# fallout AC-014 — THE TWO SIBLING SUITES THE CARVE MUST KEEP REACHING.
#
# `test_observations` owns the never-demote corpus and `test_spawn_progress`
# owns the shipped-source-tree derivation. Both are imported rather than copied,
# for the reason the monolith imported them: a parity test that owned its own
# copy of the corpus would keep passing while the two corpora drifted, and two
# scans over "the shipped source" must not be able to disagree about what that
# is. If either renames a symbol the ImportError says so by name, which is the
# loud failure rather than the silent one.

# fallout FR-004 / AC-014 (D-183) — THE ROSTER AND ITS HELPERS COME FROM
# `tests/orchestration/_env.py`, WHICH IS THE ONE PLACE THEY ARE STATED.
#
# This module carried its own byte-identical copy of a hand-typed thirteen-tuple
# and of `orchestration_source`, `owning_module`, `patch_everywhere` and
# `orchestration_has`. Fourteen copies of one roster is fourteen places to
# forget a module, and `keyfiles.py` — shipped in cycle 5 — was forgotten in
# every one of them: `owning_module` answered the IMPORTING module for
# `covers_path` and raised for `owning_entries`, and `patch_everywhere` could
# not reach a binding inside it. The roster is derived from the package
# directory now, so there is one of it and it cannot go stale.

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
    owning_module,
)

# fallout GI-033 / AC-061 / GI-034 (D-183) — THE MODULE THIS FILE IS NAMED FOR.
#
# `orchestration/halt.py` is the one HALTED WRITER, and this module drove every
# surface around it — the cap read in the leaf, the refusal in `gates.py`, the
# operator's block in `guidance.py` — while naming the writer itself nowhere but
# in an import that existed to be typed into a roster. When that roster became
# derived the import went with it, and the module named for `halt.py` stopped
# naming `halt.py` at all: `test_every_shipped_orchestration_module_has_a_test_
# module_that_imports_it` said so by name, which is the guard doing its job on a
# subject it had been satisfied about vacuously.
from foundry_mcp.tools.orchestration import halt as _halt




def test_the_halted_seal_and_the_cap_path_live_in_the_halt_module():
    """fallout GI-033 / AC-061 / FR-063 / GI-034 — the one-way seam, asserted at
    the end that owns it.

    fallout GI-033 draws the seam as `transitions.py` dispatching the `halt` token and
    the terminal seal INTO `halt.py`, with nothing flowing back and `halt.py`
    outside the verifier set. Every other pin in this suite approaches that from
    the transitions side — which is the side that could satisfy itself by
    calling anything at all. This is the other end: the seal and the cap path
    are DEFINED here, so a later author who moves either into the verifier layer
    to save an import fails on the module whose name is the claim.
    """
    assert owning_module("_seal_halted") is _halt, owning_module("_seal_halted")
    assert owning_module("_halt_if_capped") is _halt, owning_module("_halt_if_capped")
    # ...and the seam really is one-way: the writer reaches no gate or
    # transition at module top, so nothing flows back into it.
    #
    # fallout GI-033 / AC-061 / FR-063 / OT-012 (D-198's class, swept) — THIS
    # READ ONE SPELLING OF THREE, LIKE THE NO-FACADE WALK IT WAS SWEPT WITH.
    #
    # `node.module.endswith("orchestration.gates")` sees
    # `from foundry_mcp.tools.orchestration.gates import x` and nothing else:
    # driven, `from foundry_mcp.tools.orchestration import gates` and
    # `import foundry_mcp.tools.orchestration.gates` were both INVISIBLE, and
    # either one is a module-top edge from the seal back into the verifier
    # layer — the exact direction this rule's "nothing flows back" forbids. The
    # resolution is `_all_imports`, the scanner
    # `tests/orchestration/test_module_boundaries.py` already states once for
    # both layering walks, borrowed by name the way `test_evidence.py` borrows
    # `_resolve_dotted` rather than growing a second copy that can drift.
    from tests.orchestration.test_module_boundaries import _all_imports

    reached = _all_imports(Path(_halt.__file__))
    assert not (reached & {"gates", "transitions"}), sorted(reached)




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
