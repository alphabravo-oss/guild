"""The spend ledger door and its roll-ups.

fallout FR-005 / GI-026 / AC-014 — every shipped orchestration module has its
own test module, carved in the same casting as the source move. The ARITHMETIC
lives in `foundry_state.spend_rollup` (GI-024) and `tests/test_foundry_state_
readers.py` drives it; what is asserted here is the DOOR: the ledger append, the
cycle key, and the roll-up this module writes onto `state.json`.
"""
from __future__ import annotations

import json

from foundry_mcp.tools.orchestration.spend import (
    _spend_ledger_rows,
    _spend_summary,
    foundry_record_spend,
)

from tests.orchestration._env import _write_state, run_env  # noqa: F401


def test_the_door_appends_a_row_and_rolls_it_up_under_the_server_cycle(run_env):
    """FR-037 — THE BUCKET KEY IS THE SERVER COUNTER, NOT THE CALLER'S CLAIM.

    A caller may pass `cycle` and it is RECORDED as the claim, but the bucket is
    keyed on `_current_cycle` — the same authority every other record in the run
    is stamped from. Rolling cost up under a lead-asserted cycle is how D-119's
    class of divergence starts, and a cost report that disagrees with the cycle
    ledger about which cycle a run was in is worse than no cost report.
    """
    project_root, fdir = run_env
    _write_state(fdir, phase="F3", cycle=4)

    result = foundry_record_spend("casting-2", "grind", 1200, 60_000, 99, project_root)
    assert result.get("error") is None, result

    rows = _spend_ledger_rows(fdir)
    assert len(rows) == 1, rows
    assert rows[0]["agent"] == "casting-2", rows[0]
    assert rows[0]["tokens"] == 1200, rows[0]

    state = json.loads((fdir / "state.json").read_text(encoding="utf-8"))
    by_cycle = state["spend"]["by_cycle"]
    assert "4" in by_cycle, by_cycle          # the server counter
    assert "99" not in by_cycle, by_cycle     # never the caller's claim
    assert by_cycle["4"]["tokens"] == 1200, by_cycle


def test_a_second_call_in_one_phase_accumulates_rather_than_replacing(run_env):
    """Spend is the one ledger that SUMS.

    A stream record replaces per (stream, cycle) because a re-run supersedes its
    predecessor; two agents' spend in one phase is two costs and the run paid
    both. The two arithmetics are opposite for a stated reason, and this is the
    half that must stay additive.
    """
    project_root, fdir = run_env
    _write_state(fdir, phase="F3", cycle=1)

    foundry_record_spend("casting-2", "grind", 1000, 30_000, project_root=project_root)
    foundry_record_spend("casting-5", "grind", 500, 15_000, project_root=project_root)

    state = json.loads((fdir / "state.json").read_text(encoding="utf-8"))
    assert state["spend"]["by_phase"]["F3"]["tokens"] == 1500, state["spend"]
    assert state["spend"]["total"]["tokens"] == 1500, state["spend"]
    assert len(_spend_ledger_rows(fdir)) == 2


def test_the_summary_is_a_read_and_never_a_write(run_env):
    """`_spend_summary` overlays the unreported counts onto a COPY.

    A reader that mutated the document it read would make every display call a
    write, and `Foundry-Next` calls this on every response.
    """
    project_root, fdir = run_env
    _write_state(fdir, phase="F3", cycle=1)
    foundry_record_spend("casting-2", "grind", 700, 10_000, project_root=project_root)

    before = (fdir / "state.json").read_text(encoding="utf-8")
    summary = _spend_summary(fdir)
    assert summary["total"]["tokens"] == 700, summary
    assert (fdir / "state.json").read_text(encoding="utf-8") == before


def test_the_door_never_refuses(run_env):
    """FR-022 — a forgotten or malformed cost report never blocks a gate.

    "A forgotten Foundry-Spend never blocks a gate; the report shows N agents
    unreported per phase so the gap is visible." An accounting omission is a
    thing to SEE, not a thing to stop a run over — so this door answers even
    when the numbers are nonsense, and the REPORT is where the gap shows.
    """
    project_root, fdir = run_env
    _write_state(fdir, phase="F3", cycle=1)
    for tokens, duration in ((0, 0), (-5, -5)):
        result = foundry_record_spend(
            "casting-2", "grind", tokens, duration, project_root=project_root
        )
        assert "error" not in result or result.get("ok"), (tokens, result)
