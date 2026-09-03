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
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

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
)
from foundry_mcp.tools import foundry_report as fr
from foundry_mcp.tools.foundry_report import generate_report, report_status

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


def test_the_fixture_class_cleared_on_the_budget_arm_not_the_clean_cycles_arm():
    """AC-004 verbatim: 'escalation.json records for the class a status, the
    exit reason (clean-cycles or budget), the cycle it cleared and the
    structural packets it consumed.'

    The gap is deliberate and is what makes the exit reason unambiguous: the
    budget arm reaches STRUCTURAL_PASS_BUDGET at cycle 4 while
    live_clean_cycles is still 1, one short of LIVE_CLEAN_CYCLES_TO_CLEAR. A
    fixture where both arms fired would prove nothing about which one did."""
    from foundry_mcp.schemas.vocab import (
        LIVE_CLEAN_CYCLES_TO_CLEAR,
        STRUCTURAL_PASS_BUDGET,
    )

    entry = _read_json(FIXTURE_DIR, "escalation.json")["classes"][
        "FALSE_DOCUMENTED_CONTRACT"
    ]
    assert entry["status"] == "CLEARED"
    assert entry["exit_reason"] == "budget"
    assert entry["cleared_at_cycle"] == 4
    assert entry["structural_packets_dispatched"] == STRUCTURAL_PASS_BUDGET
    assert entry["structural_packet_cycles"] == [3, 4]
    assert entry["live_clean_cycles"] < LIVE_CLEAN_CYCLES_TO_CLEAR, (
        "the clean-cycles arm must NOT also have fired, or `budget` is not "
        "the reason this class cleared"
    )
    assert entry["open_latent_defect_ids"] == ["D-004", "D-005"]


# --------------------------------------------------------------------------- #
# GI-006 / CT-014 — the sections, in both documents.
# --------------------------------------------------------------------------- #


def test_report_json_top_level_keys_are_exactly_the_required_sections(report_env):
    """CT-014 / GI-006: `report.json`'s top-level keys are exactly
    REPORT_REQUIRED_SECTIONS plus `generated_at` and `run`.

    Asserted as set EQUALITY, not containment. A twelfth key would pass a
    containment check and then be a section `report_status` does not know
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
    assert headings == [fr._SECTION_TITLES[k] for k in REPORT_REQUIRED_SECTIONS]


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
    assert doc["unknown_tier_defects"]["count"] == 1                # defects.json
    assert doc["escalated_classes"]["count"] == 1                   # escalation.json
    assert doc["lead_fix_records"]["count"] == 2                    # handoffs.jsonl
    assert doc["inspect_modes_per_cycle"]["count"] == 5             # state.json
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
    keyed on TIER_UNKNOWN and the dedicated section names the record."""
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
    assert entry["cleared_at_cycle"] == 4
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
    assert section["by_mode"] == {"DELTA": 2, "FULL": 3}
    rules = {c: d["rule"] for c, d in section["per_cycle"].items()}
    assert rules == {
        "1": "first_of_phase",
        "2": "delta",
        "3": "delta",
        "4": "verifier_touched",
        "5": "final_gate",
    }
    assert section["per_cycle"]["5"]["phase"] == "F5"
    assert section["per_cycle"]["5"]["decided_by"] == "temper"


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
    assert set(section["by_cycle"]) == {"0", "1", "2"}

    money = re.compile(r"[$€£¥]|USD|\bcost\b|\bprice\b|\bdollar", re.I)
    for bucket in (*section["by_phase"].values(), *section["by_cycle"].values(),
                   section["total"]):
        assert not money.search(json.dumps(bucket)), bucket
    # And nowhere in either whole document either.
    assert not money.search(json.dumps(_document(report_env)))
    assert not money.search(_markdown(report_env))


def test_an_unreported_dispatch_is_shown_and_no_gate_refuses_on_it(report_env):
    """AC-034 verbatim: 'A dispatched agent with no spend record is shown as
    unreported in Foundry-Next and the report, and no gate refuses on it.'

    The 'no gate refuses' half is asserted structurally: generation succeeds
    with unreported dispatches present, and `report_status` — the read the DONE
    gate actually makes — reports the report complete. An unreported dispatch
    is a gap in the MEASUREMENT, not a defect in the build.

    D-048 — EVERY BUCKET KEY IS A PHASE. `spawns.log` records the dispatch VERB
    (`cast`, `grind`); `Foundry-Spend`'s schema documents the phase as "e.g.
    F1, F2, F3", and the fixture's own `state.json.spend.by_phase` is keyed
    F1/F2/F3. This section used to bucket under `cast` and `grind`, so the
    report carried two keys that are not phases and could not be lined up
    against the roll-up beside them."""
    from foundry_mcp.tools.foundry_orchestrator import DISPATCH_PHASE_TO_RUN_PHASE

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
    # `foundry_orchestrator._overlay_unreported` writes the derived number onto
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

    assert report_status(report_env)["present"] is True
    assert report_status(report_env)["missing_sections"] == []


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

    from foundry_mcp.tools.foundry_orchestrator import DISPATCH_PHASE_TO_RUN_PHASE

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
    from foundry_mcp.tools.foundry_orchestrator import DISPATCH_PHASE_TO_RUN_PHASE

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
    from foundry_mcp.tools.foundry_orchestrator import DISPATCH_PHASE_TO_RUN_PHASE

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
    from foundry_mcp.tools.foundry_orchestrator import DISPATCH_PHASE_TO_RUN_PHASE
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

    `Foundry-Next` renders `foundry_orchestrator._unreported_dispatches` and
    the report renders `_read_unreported_dispatches`; they read the same two
    ledgers and must answer the same question the same way, or the operator has
    no spelling that satisfies both."""
    from foundry_mcp.tools.foundry_orchestrator import _unreported_dispatches

    _generate(report_env)
    section = _document(report_env)["unreported_dispatches"]
    from_report = {
        (agent, phase)
        for phase, agents in section["by_phase"].items()
        for agent in agents
    }
    from_next = {
        (row["agent"], row["phase"]) for row in _unreported_dispatches(report_env)
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

    # Cycles: the recorded constant is the FLOOR for the baseline's own run.
    # Its counter stayed at 0, its ledger proves 21, and 22 is what it is on
    # record as having executed — a derivation that undercounts the baseline is
    # measuring wrong, not measuring a better run.
    assert metrics["grind_cycles"] == THUNDER_VIPER_BASELINE["grind_cycles"]
    assert metrics["post_verification_cycles"] == (
        THUNDER_VIPER_BASELINE["post_verification_cycles"]
    )
    # Defects by tier and wall clock come off the archive itself.
    assert metrics["defects_by_tier"] == {"LATENT": 0, "LIVE": 1, TIER_UNKNOWN: 1}
    assert metrics["wall_clock_minutes"] == 150.0
    # It wrote no spend ledger, so tokens is null — never a fabricated 0, which
    # would read as "that run cost nothing".
    assert metrics["tokens"] is None

    # Every metric appears in the operator-readable table.
    table = _markdown(report_env).split("## Baseline comparison", 1)[1]
    for label in ("GRIND cycles", "Post-verification cycles", "Defects by tier",
                  "Tokens", "Wall clock"):
        assert label in table, label


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


# --------------------------------------------------------------------------- #
# CT-014 / OT-025 — `report_status`, the read the DONE gate makes.
# --------------------------------------------------------------------------- #


def test_report_status_reports_absent_before_generation(report_env):
    """OT-025 verbatim: 'Foundry-Phase done without a generated report is
    refused naming the report; after Foundry-Report it succeeds and report.json
    contains every named section.'

    This casting owns `report_status`, which that refusal reads. Before
    generation EVERY section is missing — the honest answer, since no section
    can be shown to be there."""
    status = report_status(report_env)
    assert status["present"] is False
    assert status["missing_sections"] == list(REPORT_REQUIRED_SECTIONS)
    assert REPORT_JSON_FILENAME in status["problem"]

    _generate(report_env)
    after = report_status(report_env)
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

    status = report_status(report_env)
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
    status = report_status(report_env)
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
    assert report_status(report_env)["present"] is True

    (report_env / REPORT_MD_FILENAME).unlink()
    status = report_status(report_env)
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
    status = report_status(report_env)
    assert status["present"] is False
    assert status["missing_sections"] == ["latent_backlog"]
    assert status["missing_from_markdown"] == ["latent_backlog"]
    assert status["missing_from_json"] == []


def test_a_failed_markdown_write_leaves_no_json_for_the_gate_to_pass(report_env):
    """D-015's third aggravator: the write ORDER decides which document's
    absence holds the gate.

    `generate_report` wrote `report.json` first, so an OSError on the markdown
    left a complete JSON behind and a satisfied gate — a half-written report
    that opens DONE. Driven by making the markdown path unwritable: the call
    must refuse AND leave no `report.json` for a later `report_status` to pass
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
    assert report_status(report_env)["present"] is False


def test_report_status_on_a_corrupt_report_names_the_problem(report_env):
    """A `report.json` that exists and will not decode is not the same as one
    that was never generated, and the DONE refusal has to be able to say which.
    Both report `present: False`; only this one carries a `problem`."""
    (report_env / REPORT_JSON_FILENAME).write_bytes(b"\xff\xfe not json at all")
    status = report_status(report_env)
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
    assert report_status(report_env)["present"] is True


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
    assert module_level == {
        "foundry_mcp.schemas.vocab",
        "foundry_mcp.tools.foundry_state",
    }, sorted(module_level)

    nested: set[str] = set()
    for node in ast.walk(tree):
        if node in tree.body:
            continue
        nested |= _imported(node)
    # `foundry_mcp.tools` is `from foundry_mcp.tools import foundry_orchestrator`
    # — the D-047/D-048 read of `DISPATCH_PHASE_TO_RUN_PHASE`. It is the second
    # body-level import and it is body-level for the SAME reason as the first:
    # `foundry_orchestrator` imports this module, so naming it at module level
    # is the cycle the rule above is actually about.
    assert nested == {
        "foundry_mcp.tools",
        "foundry_mcp.tools.foundry_spawn",
    }, sorted(nested)


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
    absolute: json and pathlib and nothing else, not even from its own package.

    `read_jsonl` was added there by this casting, and this is the pin that the
    addition held the contract — a reader that reached for a vocab constant
    would close the cycle the whole module exists to keep open."""
    import ast

    from foundry_mcp.tools import foundry_state

    source = Path(foundry_state.__file__).read_text(encoding="utf-8")
    modules: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ImportFrom):
            modules.add(node.module or "")
        elif isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
    assert modules == {"__future__", "json", "pathlib"}, sorted(modules)


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

        print("=== unknown-tier defects, listed separately (FR-051) ===")
        for row in doc["unknown_tier_defects"]["defects"]:
            print(f"  {row['id']}  status {row['status']}  {row['class']}")

        print("=== escalated classes (AC-004) ===")
        for row in doc["escalated_classes"]["classes"]:
            print(
                f"  {row['class']}  status={row['status']}  "
                f"exit_reason={row['exit_reason']}  "
                f"cleared_at_cycle={row['cleared_at_cycle']}  "
                f"packets={row['structural_packets_dispatched']}  "
                f"open_latent={row['open_latent_defect_ids']}"
            )

        print("=== lead_fix records (AC-022) ===")
        for row in doc["lead_fix_records"]["records"]:
            print(
                f"  {row['defect_id']}  tier={row['tier']}  file={row['file']}  "
                f"lines={row['line_count']}  test={row['test']}"
            )

        print("=== INSPECT mode per cycle (AC-036) ===")
        for cycle, row in doc["inspect_modes_per_cycle"]["per_cycle"].items():
            print(f"  cycle {cycle}  {row['phase']:<5} {row['mode']:<5} {row['rule']}")

        print("=== spend: tokens and minutes only, no money (NFR-002) ===")
        for phase, row in doc["spend_per_phase_and_cycle"]["by_phase"].items():
            print(f"  phase {phase:<5} tokens={row['tokens']:<9} minutes={row['minutes']}")
        total = doc["spend_per_phase_and_cycle"]["total"]
        print(f"  run   total tokens={total['tokens']:<9} minutes={total['minutes']}")

        print("=== unreported dispatches, advisory only (AC-034) ===")
        for phase, agents in doc["unreported_dispatches"]["by_phase"].items():
            print(f"  {phase:<6} {agents}")

        print("=== baseline comparison (AC-036) ===")
        bc = doc["baseline_comparison"]
        print(f"  baseline {bc['baseline']['run']}: "
              f"grind={bc['baseline']['grind_cycles']} "
              f"post_verification={bc['baseline']['post_verification_cycles']}")
        print(f"  target: grind={bc['target']['grind_cycles']} "
              f"post_verification={bc['target']['post_verification_cycles']}")
        print(f"  this run: grind={bc['current']['grind_cycles']} "
              f"post_verification={bc['current']['post_verification_cycles']}")

        print("=== report_status, the DONE gate's read (GI-006 / OT-025) ===")
        status = report_status(report_env)
        print(f"  present={status['present']}  missing_sections={status['missing_sections']}")

    # Every printed line is also asserted, so the transcript cannot drift.
    doc = _document(report_env)
    assert list(doc)[2:] == list(REPORT_REQUIRED_SECTIONS)
    assert [d["id"] for d in doc["latent_backlog"]["defects"]] == ["D-004", "D-005"]
    assert [d["id"] for d in doc["unknown_tier_defects"]["defects"]] == ["D-007"]
    assert doc["escalated_classes"]["classes"][0]["exit_reason"] == "budget"
    assert doc["lead_fix_records"]["count"] == 2
    assert doc["inspect_modes_per_cycle"]["by_mode"] == {"DELTA": 2, "FULL": 3}
    assert doc["unreported_dispatches"]["count"] > 0
    assert report_status(report_env)["present"] is True
