"""The recurring-class escalation ledger.

Carved from `tests/test_orchestrator_gates.py` (fallout FR-005 / GI-026 /
AC-014 / OT-016): one test module per shipped orchestration module, landed in
the same casting as the source move so no pin is ever left pointing at a module
that no longer exists.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from foundry_mcp.schemas import vocab
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
    _at_the_phase_for,
    _defect_ledger,
    _record_full_inspect_mode,
    _sync_env,
    _write_manifest_with_castings,
    _write_state,
    run_env,
)

from foundry_mcp.tools.orchestration.directives import (  # noqa: F401
    foundry_defects_to_tasks,
)

from foundry_mcp.tools.orchestration.escalation import (  # noqa: F401
    DEFECT_CLASS_FIELD,
    ESCALATION_FILENAME,
    ESCALATION_OVERRIDE_TOKEN,
    _OVERRIDE_ALL_VALUES,
    _defect_class,
    _escalation_exit_distances,
    _override_instruction,
    _override_markers,
    _override_offer,
)

from foundry_mcp.tools.orchestration.gates import (  # noqa: F401
    _done_preconditions,
)

from foundry_mcp.tools.orchestration.guidance import (  # noqa: F401
    foundry_next_action,
)

from tests.orchestration._env import (  # noqa: F401
    FIXTURE_CLASS,
    FIXTURE_TIER,
    _WILDCARD_SPELLINGS,
    _clean_f2,
    _escalated_fixture,
    _finding,
    _sync,
)




def test_sync_carries_a_stream_declared_class_onto_the_record(run_env):
    """FR-007: the optional class field travels with the record so escalation
    can key on it."""
    project_root, fdir = run_env
    _sync_env(fdir)

    _sync(
        0, [_finding(**{"class": "FALSE_DOCUMENTED_CONTRACT"})], project_root
    )

    record = json.loads((fdir / "defects.json").read_text(encoding="utf-8"))["defects"][0]
    assert record["class"] == "FALSE_DOCUMENTED_CONTRACT"
    assert _defect_class(record) == "FALSE_DOCUMENTED_CONTRACT"




def test_defect_dispatch_carries_target_kind_and_defect_class(run_env):
    """D-009 / AC-001 / FR-007: Foundry-Defect's schema and dispatch lambda
    omitted both optional params, so over MCP the comment-prose refusal and
    class tagging were DEAD — proved live when a line-drift finding filed over
    MCP was accepted as a defect. Asserted by driving the dispatcher: the
    refusal engages, and a declared class reaches the record."""
    from foundry_mcp import server as foundry_server

    project_root, fdir = run_env
    _write_state(fdir, phase="F2", cycle=0)

    tools = {t.name: t for t in asyncio.run(foundry_server.list_tools())}
    defect_props = tools["Foundry-Defect"].inputSchema["properties"]
    assert "target_kind" in defect_props
    assert "defect_class" in defect_props

    previous_root = foundry_server._project_root
    try:
        foundry_server._project_root = project_root

        # target_kind="comment" is what makes the finding demotable at all —
        # without it arriving, this line-drift prose lands as a defect.
        refused = foundry_server._DISPATCH["Foundry-Defect"]({
            "cycle": 0,
            "source": "trace",
            "defect_type": "WRONG",
            "description": (
                "the comment cites line 88 but the symbol moved and the line "
                "number is stale"
            ),
            "target_kind": "comment",
            "tier": FIXTURE_TIER,
            "defect_class": FIXTURE_CLASS,
        })
        assert "error" in refused, refused
        assert refused["refused_class"] in vocab.OBSERVATION_CLASSES

        tagged = foundry_server._DISPATCH["Foundry-Defect"]({
            "cycle": 0,
            "source": "trace",
            "defect_type": "UNWIRED",
            "description": "handler never calls the store",
            "file_path": "src/api/a.py",
            "defect_class": "FALSE_DOCUMENTED_CONTRACT",
            "tier": FIXTURE_TIER,
        })
        assert "error" not in tagged, tagged
    finally:
        foundry_server._project_root = previous_root

    records = json.loads((fdir / "defects.json").read_text(encoding="utf-8"))["defects"]
    assert [r["id"] for r in records] == [tagged["defect_id"]]
    # The class travels to the record under the key escalation reads.
    assert records[0][DEFECT_CLASS_FIELD] == "FALSE_DOCUMENTED_CONTRACT"
    assert _defect_class(records[0]) == "FALSE_DOCUMENTED_CONTRACT"




def render_override_roundtrip_table() -> str:
    """D-139: round-trip every rendered instruction back through the reader.

    Keys are DERIVED from `_OVERRIDE_ALL_VALUES` rather than typed, so a
    spelling added to the reader is a row here the same day.
    """
    keys = [
        "hand bound hardening",
        "AUTH_CONTRACT",
        "a.b,c",
        *sorted(_OVERRIDE_ALL_VALUES),
    ]
    out = [
        "== render an override instruction, then read it back ==",
        "   %-24s %-42s %s" % ("class key", "rendered", "reads back as"),
        "   %-24s %-42s %s" % ("-" * 9, "-" * 8, "-" * 13),
    ]
    for key in keys:
        rendered = _override_instruction(key)
        if rendered is None:
            out.append("   %-24s %-42s %s" % (repr(key), "(no marker can name it)", "-"))
            continue
        back = _override_markers(rendered)["overrides"]
        verdict = "OK" if back == {key} else f"MISMATCH -> {sorted(back)}"
        out.append("   %-24s %-42s %s" % (repr(key), repr(rendered), verdict))
    return "\n".join(out)




def test_every_rendered_override_instruction_round_trips():
    """The tool cannot print a marker it will not honour — ALL branches.

    D-133 verified only the BARE branch and returned the quoted fallback
    unverified. For the four keys spelled like a wildcard that fallback did not
    merely fail, it read back as the WILDCARD: `escalation-override: "all"`
    returned {'*'}, because the unwrap that `_override_value_quoting` now does
    stripped the quotes BEFORE the value was tested against
    `_OVERRIDE_ALL_VALUES`. Quoting could not protect a class key spelled like
    a wildcard, which is the one thing quoting is for.
    """
    table = render_override_roundtrip_table()
    assert "MISMATCH" not in table, table
    assert "(no marker can name it)" not in table, table
    # The wildcard spellings must be in the table, or it proves nothing.
    for spelling in _WILDCARD_SPELLINGS:
        assert repr(spelling) in table, (spelling, table)




@pytest.mark.parametrize("key", _WILDCARD_SPELLINGS)
def test_a_quoted_wildcard_spelling_names_the_class_not_every_class(key):
    """The mis-read was not a no-op, it was an over-broad WILDCARD."""
    quoted = f'{ESCALATION_OVERRIDE_TOKEN}: "{key}"'
    assert _override_markers(quoted)["overrides"] == {key}

    # ...and the BARE form still means every class, which is the behaviour
    # NFR-002 says must not narrow.
    bare = f"{ESCALATION_OVERRIDE_TOKEN}: {key}"
    assert _override_markers(bare)["overrides"] == {"*"}




def test_the_override_offer_never_interpolates_a_none(run_env):
    """Both places that offer a restore go through one renderer, so a class key
    no marker can name says so instead of printing the string "None"."""
    project_root, fdir = run_env
    for key in ("AUTH_CONTRACT", *_WILDCARD_SPELLINGS):
        offer = _override_offer(key)
        assert "None" not in offer, (key, offer)
        assert offer.startswith("Foundry-Directive("), (key, offer)




# --------------------------------------------------------------------------- #
# D-129 — the clean path names an ESCALATED class before the F6 door
# --------------------------------------------------------------------------- #


def test_the_clean_f2_arms_name_a_persisted_escalated_class(run_env):
    """US-001 / ST-010 / NFR-001: the exit is mechanical, and the lead has to
    be able to see the meter running. D-129.

    Driven: three LATENT filings of class FDC at cycles 1-3; the boundary
    closing cycle 3 wrote escalation.json status ESCALATED, escalated_at 3,
    packets 0; all required streams marked, blocking 0. Foundry-Next returned
    action transition_to_assay with "INSPECT clean: zero blocking defects ...
    3 LATENT defect(s) stay open ... they block nothing" and named neither FDC
    nor ESCALATED nor ST-010. Following it reached F5.5 through ASSAY, TEMPER
    and NYQUIST before Foundry-Gate('done') refused "1 defect class(es) are
    still ESCALATED: FDC" — every remaining boundary then costs a full
    post-verification loop instead of one crossing from F2.

    `_escalation_notice` is wired only into the `open_count > 0` GRIND arm, and
    reads `_escalated_classes`, which skips a class with no open instances —
    so it was blind twice over on exactly this state.
    """
    project_root, fdir = run_env
    _write_manifest_with_castings(fdir, ["src/api/a.py"], no_ui=True)
    _write_state(fdir, phase="F2", cycle=3)
    _defect_ledger(fdir, [
        {
            "id": f"D-00{n}", "cycle": n, "source": "prove", "type": "WRONG",
            "description": "d", "file": "src/api/a.py", "symbol": "h",
            "status": "open", "tier": "LATENT", "class": "FDC",
            "reproduction_attempted": "drove every caller; none reach it",
            "fixed_in_cycle": None,
        }
        for n in (1, 2, 3)
    ])
    (fdir / ESCALATION_FILENAME).write_text(json.dumps({"classes": {
        "FDC": {
            "class": "FDC", "status": "ESCALATED", "exit_reason": None,
            "escalated_at_cycle": 3, "cleared_at_cycle": None,
            "structural_packets_dispatched": 0, "structural_packet_cycles": [],
            "live_clean_cycles": 0, "consecutive_cycles": 3,
            "defect_ids": ["D-001", "D-002", "D-003"],
            "open_latent_defect_ids": ["D-001", "D-002", "D-003"],
            "proposal": "", "recorded_at": "2020-01-01T00:00:00+00:00",
        }
    }}), encoding="utf-8")
    recorded = _record_full_inspect_mode(fdir, cycle=3)
    for stream in recorded["required_streams"]:
        (fdir / f".{stream}-complete").write_text(
            "2020-01-01T00:00:00+00:00 cycle=3\nitems_checked=10\n"
            "items_total=10\ncoverage=100%\nfindings=0\n",
            encoding="utf-8",
        )

    nxt = foundry_next_action(project_root)

    assert nxt["action"] == "transition_to_assay", nxt
    assert "FDC" in nxt["instructions"], nxt["instructions"]
    assert "ESCALATED" in nxt["instructions"]
    assert "ST-010" in nxt["instructions"]
    assert nxt["details"]["still_escalated_classes"] == ["FDC"]

    # And the set it names is the SAME set the F6 door refuses on — one union,
    # read through one function, so the notice and the refusal cannot drift.
    _at_the_phase_for(fdir, "done")
    outcome = _done_preconditions(fdir, project_root)
    assert outcome["passed"] is False
    assert "FDC" in outcome["reason"]




def test_the_delta_arm_still_names_its_own_widening_re_open(run_env):
    """AC-016: from a DELTA cycle the widening re-open IS `inspect_start`, so
    the notice must name THAT — the two spellings are selected by the RECORDED
    width, which this arm reads back and never computes (GI-008)."""
    project_root, fdir = run_env
    _escalated_fixture(fdir, open_instances=False)
    _clean_f2(project_root, fdir)
    modes = json.loads((fdir / "state.json").read_text(encoding="utf-8"))
    modes["inspect_modes"][-1]["mode"] = "DELTA"
    modes["inspect_modes"][-1]["rule"] = "delta"
    (fdir / "state.json").write_text(json.dumps(modes), encoding="utf-8")

    nxt = foundry_next_action(project_root)

    assert nxt["action"] == "widen_inspect", nxt
    assert "widening re-open" in nxt["instructions"], nxt["instructions"]
    assert "grind_start" not in nxt["instructions"], nxt["instructions"]




def test_the_budget_arm_is_not_offered_to_a_class_that_cannot_spend_it(run_env):
    """ST-002 / FR-002: the budget arm clears a class when its structural
    packets are spent — and `Foundry-Tasks` emits one only for a class with an
    open bucket.

    Driven at cycle 8 on this run's own state: the sentence offered "2 more
    structural packet(s)" for a class with every instance closed, while
    `Foundry-Tasks` on the same run returned `structural_tasks: None`,
    `escalated_classes: []` and left `structural_packets_dispatched` at 0.
    Asserted here as the pair it is — what the sentence says, and what the tool
    it names actually does.
    """
    project_root, fdir = run_env
    _escalated_fixture(fdir, open_instances=False)
    _write_state(fdir, phase="F2", cycle=4)

    sentence = _escalation_exit_distances(fdir, project_root, ["FDC"])
    assert "cannot advance this class" in sentence, sentence
    assert "more structural packet(s) (budget arm" not in sentence, sentence

    # The tool the retired sentence named, on the same run.
    tasks = foundry_defects_to_tasks(project_root)
    assert not tasks.get("structural_tasks"), tasks
    entry = json.loads(
        (fdir / ESCALATION_FILENAME).read_text(encoding="utf-8")
    )["classes"]["FDC"]
    assert entry["structural_packets_dispatched"] == 0, entry




def test_the_budget_arm_is_offered_while_the_class_still_has_work(run_env):
    """The other side, so the rule is a discrimination and not a deletion: a
    class with an open instance can still spend its budget, and the sentence
    offers both arms exactly as ST-002 describes them."""
    project_root, fdir = run_env
    _escalated_fixture(fdir, open_instances=True)
    _write_state(fdir, phase="F2", cycle=4)

    sentence = _escalation_exit_distances(fdir, project_root, ["FDC"])

    assert "more structural packet(s) (budget arm" in sentence, sentence
    assert "cannot advance this class" not in sentence, sentence
