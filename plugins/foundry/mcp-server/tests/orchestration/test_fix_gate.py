"""Foundry-Fix and Foundry-Sync: the two doors into the defect ledger.

Carved from `tests/test_orchestrator_gates.py` (fallout FR-005 / GI-026 /
AC-014 / OT-016): one test module per shipped orchestration module, landed in
the same casting as the source move so no pin is ever left pointing at a module
that no longer exists.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from foundry_mcp.schemas import vocab
from foundry_mcp.tools import artifacts, foundry_state
from foundry_mcp.tools.foundry import foundry_add_defect
from foundry_mcp.tools.display import format_result

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
from tests.test_observations import (  # noqa: F401
    COUNT,
    DIRECTION,
    DRIFT,
    ENUMERATION,
    NO_SECURITY_VOCABULARY,
    SECURITY_BATTERY,
)

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
    _sync_env,
    _write_state,
    run_env,
)

from foundry_mcp.tools.orchestration.fix_gate import (  # noqa: F401
    foundry_sync_defects,
)

from tests.orchestration._env import (  # noqa: F401
    _SECURITY_CLAIM,
    _finding,
    _fixed_record,
    _plain,
    _reaches_defect_ledger_via_add,
    _second_run,
    _seed_fixed,
    _sync,
    _tripwire_classes,
    _untiered_open,
)




def test_sync_accepts_the_partial_defect_type(run_env):
    """AC-019: 'Filing a defect with type PARTIAL succeeds via ... Foundry-Sync'
    — PARTIAL is one of the values agents were already told to emit while the
    surface rejected it."""
    project_root, fdir = run_env
    _sync_env(fdir)

    result = _sync(0, [_finding(type="PARTIAL")], project_root)

    assert result.get("ok") is True, result
    assert result["added"] == 1
    record = json.loads((fdir / "defects.json").read_text(encoding="utf-8"))["defects"][0]
    assert record["type"] == "PARTIAL"




def test_sync_preserves_source_verbatim(run_env):
    """OT-007 / AC-019: 'the recorded source survives Foundry-Sync unchanged'.
    research_audit used to be rewritten to trace, pointing the run's evidence
    at the wrong stream."""
    project_root, fdir = run_env
    _sync_env(fdir)

    _sync(
        0,
        [_finding(source=s, description=f"finding from {s}", symbol=s)
         for s in ("research_audit", "coverage_diff", "flow_trace", "test01", "temper")],
        project_root,
    )

    records = json.loads((fdir / "defects.json").read_text(encoding="utf-8"))["defects"]
    assert [r["source"] for r in records] == [
        "research_audit", "coverage_diff", "flow_trace", "test01", "temper"
    ]




def test_sync_refuses_an_unknown_source_instead_of_coercing_it(run_env):
    """NFR-002 / A-035: 'Previously-coerced unknown sources are henceforth
    rejected with a named error — a deliberate behaviour change.'"""
    project_root, fdir = run_env
    _sync_env(fdir)

    result = _sync(0, [_finding(source="linter")], project_root)

    assert "error" in result
    assert result["refusals"][0]["field"] == "source"
    assert "linter" in result["error"]
    assert json.loads((fdir / "defects.json").read_text(encoding="utf-8"))["defects"] == []




def test_sync_refuses_a_finding_with_no_source_at_all(run_env):
    """The default was "inspect", which was not a legal source and so became
    "trace" — the mis-attribution path. An unattributed finding is refused."""
    project_root, fdir = run_env
    _sync_env(fdir)

    result = _sync(0, [_finding(source="")], project_root)

    assert "error" in result
    assert result["refusals"][0]["field"] == "source"




def test_sync_refuses_an_unknown_defect_type(run_env):
    """CT-002: `type` had no validation whatsoever and was written straight
    through, so the ledger could carry types nothing downstream understood."""
    project_root, fdir = run_env
    _sync_env(fdir)

    result = _sync(0, [_finding(type="SORT_OF_BROKEN")], project_root)

    assert "error" in result
    assert result["refusals"][0]["field"] == "type"
    assert "SORT_OF_BROKEN" in result["error"]




def test_sync_refusal_is_all_or_nothing(run_env):
    """One bad finding refuses the whole batch, so the caller never has to
    guess which of its findings landed."""
    project_root, fdir = run_env
    _sync_env(fdir)

    result = _sync(
        0,
        [_finding(symbol="good"), _finding(symbol="bad", source="nope")],
        project_root,
    )

    assert "error" in result
    assert "no findings were recorded" in result["error"]
    assert json.loads((fdir / "defects.json").read_text(encoding="utf-8"))["defects"] == []




def test_sync_canonicalizes_the_misplaced_alias(run_env):
    """MISPLACED and ARCHITECTURAL_PLACEMENT are one type under two live
    spellings; both are accepted on input and land under ONE stored spelling.

    Storing them as written would put a single class into the ledger under two
    names — every by-type roll-up, every escalation cluster and every query
    would then see two half-populated classes instead of one real one. Both
    input spellings are asserted here because "both accepted" and "one stored"
    are separate claims and only the pair is the contract."""
    project_root, fdir = run_env
    _sync_env(fdir)

    _sync(
        0,
        [
            _finding(type="MISPLACED", symbol="a"),
            _finding(type="ARCHITECTURAL_PLACEMENT", symbol="b"),
        ],
        project_root,
    )

    records = json.loads((fdir / "defects.json").read_text(encoding="utf-8"))["defects"]
    assert len(records) == 2
    assert {r["type"] for r in records} == {"ARCHITECTURAL_PLACEMENT"}
    # And that stored spelling is the canonicaliser's answer, not a local one.
    assert vocab.canonical_defect_type("MISPLACED") == "ARCHITECTURAL_PLACEMENT"




@pytest.mark.parametrize("absent", ["missing_key", "", "   "])
def test_sync_refuses_an_absent_type_rather_than_defaulting_it_to_missing(
    run_env, absent
):
    """fallout ST-006 / GI-022 (D-100) — the batch door stops coercing `type`.

    What stood here asserted the coercion as a contract ("the pre-existing
    default is a legal member and keeps working"), and that is the defect
    written down: `MISSING` is a real member of `DEFECT_TYPES`, so defaulting to
    it does not record "no type given" — it records a type the filer never
    named. The single door refuses the same input by name
    (`{'error': "Invalid defect_type: ''...", 'field': 'defect_type'}`), so the
    two doors gave opposite answers to one finding, which is the divergence
    ST-006 and GI-022 are stated over.

    IT IS NOT ONLY ATTRIBUTION. `type` is one of the four identity fields
    `retier_matching_untiered` matches on, so a record filed batch-side as
    MISSING can never be re-tiered by a later single-door filing naming its real
    type — the untiered record stays open and blocking with no cheap exit.

    All three spellings of absence are driven, because `.get("type") or ...` and
    `.get("type") is None` are different predicates and only the first is what
    the door had.
    """
    project_root, fdir = run_env
    _sync_env(fdir)

    f = _finding()
    if absent == "missing_key":
        del f["type"]
    else:
        f["type"] = absent
    result = _sync(0, [f], project_root)

    assert "error" in result, result
    assert result["refusals"][0]["field"] == "type", result
    assert "type is required" in result["refusals"][0]["reason"], result
    # ALL-OR-NOTHING: a refused batch writes no record at all.
    assert json.loads((fdir / "defects.json").read_text(encoding="utf-8"))["defects"] == []


def test_both_doors_refuse_an_absent_type_naming_their_own_field(run_env):
    """fallout ST-006 / GI-022 (D-100) — the parity half, driven at both doors.

    The refusal above is only half the requirement: what ST-006 asks is that ONE
    finding gets ONE answer whichever door it arrives at. Both are driven on the
    same empty `type` over the same ledger, and both must refuse — the field
    each names is its own argument spelling, which is the only difference the
    contract admits.
    """
    project_root, fdir = run_env
    _sync_env(fdir)

    single = foundry_add_defect(
        cycle=0, source="trace", defect_type="", description="d",
        defect_class="c", tier="LIVE", project_root=project_root,
    )
    batch = _sync(0, [_finding(type="")], project_root)

    assert "error" in single, single
    assert single["field"] == "defect_type", single
    assert "error" in batch, batch
    assert batch["refusals"][0]["field"] == "type", batch
    assert json.loads((fdir / "defects.json").read_text(encoding="utf-8"))["defects"] == []




def _hardening_plus_untiered(fdir: Path) -> None:
    """One open HARDENING record and one open untiered record on the SAME path.

    The arrangement both doors are driven over below: the untiered D-002 is what
    the incoming finding re-tiers, and D-001 is the HARDENING record its
    `supersedes` promotes. Both must happen, and the whole of D-099 is that only
    one of them did on the batch path.
    """
    (fdir / "defects.json").write_text(
        json.dumps({"defects": [
            {
                "id": "D-001", "status": "open", "tier": "HARDENING",
                "source": "prove", "type": "UNWIRED", "file": "src/api/a.py",
                "symbol": "handle", "class": "c", "cycle": 0,
                "description": "probe failed on a path no requirement states",
                "reproduction_attempted": "drove the probe; it failed",
            },
            {
                "id": "D-002", "status": "open",
                "source": "trace", "type": "UNWIRED", "file": "src/api/a.py",
                "symbol": "handle", "class": "c", "cycle": 0,
                "description": "handler never calls the store",
            },
        ]}),
        encoding="utf-8",
    )
    _write_state(fdir, phase="F2", cycle=0)


def test_the_batch_door_closes_a_superseded_record_it_also_re_tiers(run_env):
    """fallout ST-006 / GI-022 (D-099) — the closure the `continue` skipped.

    ST-006's transition is "HARDENING defect open -> superseded (closed)",
    triggered by "a new filing on the same path that cites the HARDENING id via
    `supersedes`". The batch door's write loop leaves by two paths — re-tier an
    existing untiered record, or append a new one — and the closure was written
    under the append alone, BELOW a `continue` the re-tier path took.

    DRIVEN on identical ledgers before the fix: `foundry_add_defect` returned
    {'retiered_ids': ['D-002'], 'superseded': 'D-001', 'open_defects': 1} with
    D-001 `superseded`; `foundry_sync_defects` returned {'retiered_ids':
    ['D-002'], 'superseded_ids': [], 'total_open': 2} with D-001 still `open` —
    so the HARDENING record kept blocking every `status == "open"` census, on
    the batch path only.
    """
    project_root, fdir = run_env
    _hardening_plus_untiered(fdir)

    result = _sync(
        0,
        [_finding(tier="LIVE", supersedes="D-001", **{"class": "c"})],
        project_root,
    )

    assert result["retiered_ids"] == ["D-002"], result
    # The row this defect is: the promotion the filing CITED actually happened.
    assert result["superseded_ids"] == ["D-001"], result
    records = {
        d["id"]: d
        for d in json.loads((fdir / "defects.json").read_text(encoding="utf-8"))["defects"]
    }
    assert records["D-001"]["status"] == "superseded", records["D-001"]
    assert records["D-001"]["superseded_by"] == "D-002", records["D-001"]
    # ...and the HARDENING record stops blocking, which is what the closure is
    # FOR: every gate and census in the package counts `status == "open"`.
    assert result["total_open"] == 1, result
    # The tier is NOT touched (OT-020 / GI-022): promotion cites, never rewrites.
    assert records["D-001"]["tier"] == "HARDENING", records["D-001"]


def test_both_doors_supersede_on_the_re_tier_path(run_env):
    """fallout ST-006 / GI-022 (D-099) — the parity half, over one arrangement.

    The divergence was door-shaped, not path-shaped: the single door has no
    `continue` at the re-tier branch and falls through to its one closure. So
    the pin drives BOTH doors over the byte-identical ledger and asserts the
    same three outcomes, which is what makes it an assertion about the rule
    rather than about one implementation of it.
    """
    project_root, fdir = run_env

    _hardening_plus_untiered(fdir)
    single = foundry_add_defect(
        cycle=0, source="trace", defect_type="UNWIRED",
        description="handler never calls the store", symbol="handle",
        file_path="src/api/a.py", defect_class="c", tier="LIVE",
        supersedes="D-001", project_root=project_root,
    )

    _hardening_plus_untiered(fdir)
    batch = _sync(
        0,
        [_finding(tier="LIVE", supersedes="D-001", **{"class": "c"})],
        project_root,
    )

    assert single["retiered_ids"] == batch["retiered_ids"] == ["D-002"], (single, batch)
    assert single["superseded"] == "D-001", single
    assert batch["superseded_ids"] == ["D-001"], batch
    assert single["open_defects"] == batch["total_open"] == 1, (single, batch)


def test_the_append_exit_still_closes_its_superseded_record(run_env):
    """fallout ST-006 / GI-022 (D-099) — the ADJACENT exit of the same loop.

    D-099's fix gave the write loop's two exits one closure, and the exit that
    already worked is the one a shared spelling could break. So the APPEND path
    is driven on its own arrangement: an open HARDENING record with NO untiered
    twin, so `retier_matching_untiered` finds no match and the finding is
    appended under a freshly minted id — the branch that reaches
    `_close_promotion` by falling out of the re-tier arm rather than through it.
    """
    project_root, fdir = run_env
    (fdir / "defects.json").write_text(
        json.dumps({"defects": [{
            "id": "D-001", "status": "open", "tier": "HARDENING",
            "source": "prove", "type": "UNWIRED", "file": "src/other.py",
            "symbol": "other", "class": "c", "cycle": 0,
            "description": "probe failed on a path no requirement states",
            "reproduction_attempted": "drove the probe; it failed",
        }]}),
        encoding="utf-8",
    )
    _write_state(fdir, phase="F2", cycle=0)

    result = _sync(0, [_finding(supersedes="D-001")], project_root)

    assert result["added"] == 1, result
    assert result["retiered_ids"] == [], result
    assert result["superseded_ids"] == ["D-001"], result
    records = {
        d["id"]: d
        for d in json.loads((fdir / "defects.json").read_text(encoding="utf-8"))["defects"]
    }
    assert records["D-001"]["status"] == "superseded", records["D-001"]
    assert records["D-001"]["superseded_by"] == "D-002", records["D-001"]


def test_sync_stamps_the_server_cycle_not_the_caller_value(run_env):
    """FR-005: 'tools stop trusting caller-supplied cycle where the server
    knows better'. The three-cycle escalation rule reads these numbers back, so
    a lead asserting cycle=0 forever would mean escalation never accumulates."""
    project_root, fdir = run_env
    _sync_env(fdir)
    _write_state(fdir, phase="F2", cycle=5)

    result = _sync(99, [_finding()], project_root)

    assert result["cycle"] == 5
    assert result["declared_cycle"] == 99
    record = json.loads((fdir / "defects.json").read_text(encoding="utf-8"))["defects"][0]
    assert record["cycle"] == 5




def test_sync_mints_ids_from_the_shared_allocator(run_env):
    """AC-025 / FR-020: the positional D-{len+1} mint re-issued a live id
    whenever a record had been removed. The shared allocator takes the highest
    existing suffix, and the surrounding ledger transaction is what makes it
    safe under concurrent filing."""
    project_root, fdir = run_env
    (fdir / "defects.json").write_text(
        json.dumps({"defects": [
            {"id": "D-001", "status": "fixed", "description": "x", "symbol": "x"},
            {"id": "D-007", "status": "fixed", "description": "y", "symbol": "y"},
        ]}),
        encoding="utf-8",
    )
    _write_state(fdir, phase="F2", cycle=0)

    _sync(0, [_finding(symbol="fresh", description="fresh")], project_root)

    ids = [d["id"] for d in json.loads(
        (fdir / "defects.json").read_text(encoding="utf-8"))["defects"]]
    assert ids == ["D-001", "D-007", "D-008"]
    assert len(ids) == len(set(ids))




def test_sync_refuses_a_declared_comment_prose_finding_as_the_other_door_does(run_env):
    """D-098 / FR-051 / AC-008: ONE pipeline order, so both doors refuse it.

    LEAD RULING, GRIND cycle 6: the rung order is comment-prose refusal, then
    security denylist, tier, class, reproduction_attempted, re-tier match,
    persist — hosted in one place both doors call, and asserted by driving the
    SAME finding through both and requiring identical outcomes.

    This door used to ROUTE such a finding into observations.json and return ok,
    while `foundry_add_defect` REFUSED it and named Foundry-Observation. Two
    doors, one rule, opposite answers. The refusal is now the answer at both,
    and the finding lands in neither ledger until the filer re-sends it through
    the channel the refusal names.
    """
    project_root, fdir = run_env
    _sync_env(fdir)

    result = _sync(
        0,
        [_finding(
            description="the comment says line 42 but the symbol moved — stale line hint",
            target_kind="comment",
            symbol="",
        )],
        project_root,
    )

    assert result.get("ok") is not True, result
    assert "comment-prose observation class" in result["error"], result
    assert "LINE_DRIFT_CITE" in result["error"], result
    refusal = result["refusals"][0]
    assert refusal["field"] == "description", refusal
    assert refusal["refused_class"], refusal
    # Nothing landed anywhere: the batch is all-or-nothing and a refused finding
    # is not quietly written to the other ledger either.
    assert json.loads((fdir / "defects.json").read_text(encoding="utf-8"))["defects"] == []
    observations = (
        json.loads((fdir / "observations.json").read_text(encoding="utf-8"))
        if (fdir / "observations.json").exists() else {"observations": []}
    )
    assert observations.get("observations", []) == []




def test_both_filing_doors_refuse_one_comment_prose_finding_identically(run_env):
    """D-098 stated as the property, driven through BOTH doors on one ledger.

    The filing that exposed the divergence: a `target_kind='comment'` LATENT
    finding whose description is ENUMERATION-classed and whose
    (source, type, file, symbol) MATCHES an open untiered D-001. Through
    Foundry-Defect it was refused and D-001 was left alone; through Foundry-Sync
    it re-tiered D-001 in place and reported success — the same finding turning
    a blocking untiered record into a tracked defect, or not, depending only on
    which door the stream happened to use.

    Both doors now refuse it on the first rung, and D-001 is untouched by both.
    """
    from foundry_mcp.tools.foundry import foundry_add_defect

    project_root, fdir = run_env
    _sync_env(fdir)
    untiered = {
        "id": "D-001",
        "cycle": 0,
        "source": "trace",
        "type": "UNWIRED",
        "description": "the original wording, which a re-filing rewrites",
        "spec_ref": "",
        "symbol": "helper",
        "file": "src/api/a.py",
        "status": "open",
        "fixed_in_cycle": None,
    }
    prose = "the docstring says 8 items but there are 9 now"

    for door in ("defect", "sync"):
        (fdir / "defects.json").write_text(
            json.dumps({"defects": [dict(untiered)]}), encoding="utf-8"
        )
        if door == "defect":
            result = foundry_add_defect(
                cycle=0, source="trace", defect_type="UNWIRED", description=prose,
                symbol="helper", file_path="src/api/a.py", target_kind="comment",
                defect_class="COMMENT_DRIFT", tier="LATENT",
                reproduction_attempted="read every caller; the count is prose only",
                project_root=project_root,
            )
            refused = result.get("error", "")
        else:
            result = _sync(
                0,
                [_finding(
                    description=prose, symbol="helper", file="src/api/a.py",
                    target_kind="comment", tier="LATENT",
                    reproduction_attempted="read every caller; the count is prose only",
                )],
                project_root,
            )
            refused = result.get("error", "")

        assert "comment-prose observation class" in refused, (door, result)
        assert result.get("retiered", 0) == 0, (door, result)
        record = json.loads(
            (fdir / "defects.json").read_text(encoding="utf-8")
        )["defects"][0]
        assert record["id"] == "D-001", (door, record)
        assert "tier" not in record or record["tier"] is None, (door, record)




def test_both_filing_doors_name_the_same_rung_first_for_one_bad_filing(run_env):
    """D-098's other half: the doors named DIFFERENT fields first.

    With `tier='MEDIUM'` on a declared-comment ENUMERATION finding,
    Foundry-Defect named the observation class and Foundry-Sync named `tier` —
    contradicting `validate_defect_filing`'s own pinned contract, "THE CHECK
    ORDER IS LOCKED, so that the two doors name the same field first for the
    same bad filing". Two rungs are wrong at once here, and the door that
    refuses first decides WHICH LEDGER the finding belongs in, so it has to be
    the same rung on both.
    """
    from foundry_mcp.tools.foundry import foundry_add_defect

    project_root, fdir = run_env
    _sync_env(fdir)
    prose = "the docstring says 8 items but there are 9 now"

    single = foundry_add_defect(
        cycle=0, source="trace", defect_type="UNWIRED", description=prose,
        symbol="helper", file_path="src/api/a.py", target_kind="comment",
        defect_class="COMMENT_DRIFT", tier="MEDIUM", project_root=project_root,
    )
    batch = _sync(
        0,
        [_finding(description=prose, symbol="helper", file="src/api/a.py",
                  target_kind="comment", tier="MEDIUM")],
        project_root,
    )

    assert "comment-prose observation class" in single.get("error", ""), single
    assert "comment-prose observation class" in batch.get("error", ""), batch
    assert "Invalid tier" not in batch.get("error", ""), batch
    assert single["refused_class"] == batch["refusals"][0]["refused_class"]




def test_sync_keeps_a_denylisted_finding_as_a_defect(run_env):
    """AC-002 precedence, applied at this filing path too: a security-property
    claim stays a DEFECT even when its prose reads like comment drift."""
    project_root, fdir = run_env
    _sync_env(fdir)

    result = _sync(
        0,
        [_finding(
            description=(
                "the comment says the csrf token is validated but that line is "
                "stale — no such check exists"
            ),
            target_kind="comment",
        )],
        project_root,
    )

    assert result["added"] == 1
    # D-098: this door reports no `observations` count any more — the demotion
    # it counted is gone. That the finding is a DEFECT is the whole claim, and
    # `added == 1` is it.
    assert "observations" not in result, result




def test_sync_will_not_demote_a_finding_that_declares_no_target_kind(run_env):
    """casting-1's AC-002 concern, handled at the call site: an ABSENT
    target_kind does not license a demotion, because vocab's is_non_comment
    only matches a target_kind that is present and non-"comment"."""
    project_root, fdir = run_env
    _sync_env(fdir)

    result = _sync(
        0,
        [_finding(description="the comment's line hint is stale and no longer matches")],
        project_root,
    )

    assert result["added"] == 1
    assert "observations" not in result, result




def test_a_different_defect_on_the_same_symbol_is_filed_not_absorbed(run_env):
    """D-049 drive G1, verbatim. A prove/MISSING/FR-003 finding on the symbol of
    a fixed trace/UNWIRED/FR-001 record was reported as reopened=1, added=0 —
    one record on disk, still carrying the OLD source, type, spec_ref and
    description, and the caller told it succeeded."""
    project_root, fdir = run_env
    _seed_fixed(fdir, _fixed_record())

    result = _sync(
        2,
        [_finding(
            source="prove",
            type="MISSING",
            spec_ref="FR-003",
            symbol="submit_form",
            file="src/api/form.py",
            description="COMPLETELY DIFFERENT: no CSRF validation on the POST branch",
        )],
        project_root,
    )

    assert result["added"] == 1, result
    assert result["reopened"] == 0
    assert result["regressions"] == []

    records = json.loads((fdir / "defects.json").read_text(encoding="utf-8"))["defects"]
    assert len(records) == 2
    filed = records[1]
    # AC-019: every content field of the incoming finding survived verbatim.
    assert filed["source"] == "prove"
    assert filed["type"] == "MISSING"
    assert filed["spec_ref"] == "FR-003"
    assert "COMPLETELY DIFFERENT" in filed["description"]
    # And the old record was not disturbed.
    assert records[0]["status"] == "fixed"
    assert records[0]["source"] == "trace"




def test_a_shared_description_alone_does_not_reopen_across_file_and_symbol(run_env):
    """D-049 drive G1b: description equality ALONE reopened a fixed defect
    across a different file AND a different symbol — added 0, one record, symbol
    still the old one. Two records that merely read alike are two defects."""
    project_root, fdir = run_env
    _seed_fixed(fdir, _fixed_record(symbol="alpha", file="a.py", description="same text"))

    result = _sync(
        2,
        [_finding(symbol="omega", file="z.py", description="same text")],
        project_root,
    )

    assert result["added"] == 1, result
    assert result["reopened"] == 0

    records = json.loads((fdir / "defects.json").read_text(encoding="utf-8"))["defects"]
    assert [r["symbol"] for r in records] == ["alpha", "omega"]
    assert [r["file"] for r in records] == ["a.py", "z.py"]




def test_the_same_defect_recurring_is_still_a_regression(run_env):
    """The behaviour that must NOT be lost: a fixed defect coming back — same
    symbol, same file, same type, same spec_ref, same description — reopens
    rather than being filed twice."""
    project_root, fdir = run_env
    _seed_fixed(fdir, _fixed_record())

    result = _sync(
        2,
        [_finding(
            source="trace",
            type="UNWIRED",
            spec_ref="FR-001",
            symbol="submit_form",
            file="src/api/form.py",
            description="ORIGINAL: handler never calls the token store",
        )],
        project_root,
    )

    assert result["reopened"] == 1, result
    assert result["added"] == 0
    assert result["regressions"] == ["D-001"]

    records = json.loads((fdir / "defects.json").read_text(encoding="utf-8"))["defects"]
    assert len(records) == 1
    assert records[0]["status"] == "open"
    assert records[0]["regression"] is True
    assert records[0]["reopened_in_cycle"] == 2




def test_a_lone_agreement_is_never_enough_to_reopen(run_env):
    """The rule stated directly: ONE matching field is a coincidence. Each case
    below agrees with the fixed record on exactly one non-empty field and
    conflicts on nothing else, because every other field is absent."""
    project_root, fdir = run_env
    _seed_fixed(fdir, _fixed_record(
        symbol="submit_form", file="", spec_ref="", description="only the symbol",
    ))

    result = _sync(
        2,
        [{"source": "prove", "type": "UNWIRED", "symbol": "submit_form",
          "description": "a wholly unrelated observation about the same symbol"}],
        project_root,
    )

    assert result["added"] == 1, result
    assert result["reopened"] == 0




def test_a_conflicting_field_blocks_a_reopen_however_much_else_agrees(run_env):
    """Rule 1: no non-empty field may conflict. Symbol, file and description all
    agree here; a different spec_ref alone means it is a different defect."""
    project_root, fdir = run_env
    _seed_fixed(fdir, _fixed_record())

    result = _sync(
        2,
        [_finding(
            source="trace",
            type="UNWIRED",
            spec_ref="FR-999",
            symbol="submit_form",
            file="src/api/form.py",
            description="ORIGINAL: handler never calls the token store",
        )],
        project_root,
    )

    assert result["added"] == 1, result
    assert result["reopened"] == 0




def test_the_type_comparison_reads_through_the_canonicaliser(run_env):
    """MISPLACED and ARCHITECTURAL_PLACEMENT are one type under two spellings.
    Comparing them raw would read a genuine regression as a conflict and file a
    duplicate, so the incoming type is compared canonicalised — the same value
    that would be stored."""
    project_root, fdir = run_env
    _seed_fixed(fdir, _fixed_record(type="ARCHITECTURAL_PLACEMENT"))

    result = _sync(
        2,
        [_finding(
            source="trace",
            type="MISPLACED",
            spec_ref="FR-001",
            symbol="submit_form",
            file="src/api/form.py",
            description="ORIGINAL: handler never calls the token store",
        )],
        project_root,
    )

    assert result["reopened"] == 1, result
    assert result["added"] == 0




def test_an_absent_field_on_both_sides_is_not_an_agreement(run_env):
    """Two records that both OMIT `file` have not thereby agreed about
    anything. If absence counted, two unrelated findings with empty symbol and
    empty spec_ref would reach the two-agreement threshold on nothing at all."""
    project_root, fdir = run_env
    _seed_fixed(fdir, _fixed_record(
        symbol="", file="", spec_ref="", description="first finding",
    ))

    result = _sync(
        2,
        [{"source": "trace", "type": "UNWIRED", "description": "second finding"}],
        project_root,
    )

    assert result["added"] == 1, result
    assert result["reopened"] == 0




def _reaches_defect_ledger_via_sync(root: Path, description: str) -> bool:
    """Did a declared-comment finding with this prose land in defects.json,
    filed through ``foundry_sync_defects``?"""
    fdir = _second_run(root)
    _sync(
        0, [_finding(description=description, target_kind="comment")], str(root)
    )
    return bool(json.loads((fdir / "defects.json").read_text(encoding="utf-8"))["defects"])




@pytest.mark.parametrize("description", SECURITY_BATTERY)
def test_sync_keeps_every_security_battery_case_as_a_defect(run_env, description):
    """OT-002 driven through the door that never had coverage.

    Casting 3 pinned this battery on ``foundry_add_defect`` only. Every one of
    these is a comment claiming a security property the code does not implement,
    filed with the honest ``target_kind="comment"`` declaration that used to be
    exactly what made it demotable.
    """
    project_root, fdir = run_env
    _sync_env(fdir)

    result = _sync(
        0, [_finding(description=description, target_kind="comment")], project_root
    )

    assert result.get("ok") is True, result
    assert result["added"] == 1, result
    # D-098: the demotion counter is gone from this door's result along with the
    # demotion. That every battery case is a DEFECT is the claim, and it is the
    # ledger below that carries it.
    assert "observations" not in result, result
    defects = json.loads((fdir / "defects.json").read_text(encoding="utf-8"))["defects"]
    assert [d["description"] for d in defects] == [description]




def test_sync_keeps_a_behaviour_finding_with_no_security_vocabulary(run_env):
    """The case that makes the repair STRUCTURAL rather than lexical.

    It is not a security finding at all — a plain correctness one — so no
    widening of vocab's never-demote denylist could ever reach it, and its prose
    classifies as DIRECTION_WORD. Only the promote-direction fail-safe rescues
    it, which is why this test is the one that fails if the guard is ever
    removed from this branch as redundant.

    The empty tripwire is load-bearing: it proves the denylist had nothing to do
    with the outcome, so the assertion cannot pass for the wrong reason.
    """
    project_root, fdir = run_env
    _sync_env(fdir)

    assert vocab.observation_class(
        {"description": NO_SECURITY_VOCABULARY, "target_kind": "comment"}
    ) == "DIRECTION_WORD", (
        "the fixture no longer trips an observation regex, so it no longer "
        "exercises the promote-direction guard at all"
    )

    result = _sync(
        0,
        [_finding(description=NO_SECURITY_VOCABULARY, target_kind="comment")],
        project_root,
    )

    assert result["added"] == 1, result
    assert "observations" not in result, result
    assert "denylist_tripwires" not in result, result
    defects = json.loads((fdir / "defects.json").read_text(encoding="utf-8"))["defects"]
    assert [d["description"] for d in defects] == [NO_SECURITY_VOCABULARY]

    # Read through absence rather than requiring the file: nothing was routed
    # away from the defect ledger, so the observations writer was never called
    # and never created it. An unwritten ledger is the strongest form of the
    # claim, not a gap in it — asserting the file exists would demand a side
    # effect the correct behaviour does not produce.
    ledger_path = fdir / "observations.json"
    observations = (
        json.loads(ledger_path.read_text(encoding="utf-8"))
        if ledger_path.exists()
        else {}
    )
    assert observations.get("observations", []) == []
    assert observations.get("tripwire", []) == []




@pytest.mark.parametrize(
    "description,is_defect",
    [pytest.param(p.values[0], True, id=f"battery-{p.id}") for p in SECURITY_BATTERY]
    + [
        pytest.param(NO_SECURITY_VOCABULARY, True, id="no-security-vocabulary"),
        # ...and the other direction. A guard biased to over-match would satisfy
        # every row above while quietly deleting the observation channel, so the
        # four canonical comment-prose classes ride in the SAME matrix: parity
        # has to hold at "not a defect" too, or it is only half a claim.
        pytest.param(DRIFT, False, id="prose-line-drift"),
        pytest.param(COUNT, False, id="prose-count"),
        pytest.param(DIRECTION, False, id="prose-direction-word"),
        pytest.param(ENUMERATION, False, id="prose-enumeration"),
    ],
)
def test_the_two_filing_paths_agree_on_what_a_defect_is(
    run_env, tmp_path, description, is_defect
):
    """D-094's derived-membership pin, and the whole point of the fix.

    ``foundry_sync_defects``'s own inline comment states the invariant: "two
    filing paths that disagree about what a defect is would be a worse bug than
    the one being fixed". This asserts it directly, over a corpus imported from
    the tests that pin the other door.

    It is stated as an OUTCOME (did this reach the defect ledger?) and never as
    a mechanism. The doors legitimately differ in HOW they decline — Sync
    auto-demotes to the observations ledger, Foundry-Defect returns a named
    refusal telling the caller to re-file — and a test that pinned which guard
    admitted a finding would pass for the wrong reason the moment either half
    moved.
    """
    project_root, _fdir = run_env

    via_sync = _reaches_defect_ledger_via_sync(tmp_path / "sync-door", description)
    via_add = _reaches_defect_ledger_via_add(tmp_path / "defect-door", description)

    assert via_sync == via_add, (
        f"the filing paths disagree: Foundry-Sync {'kept' if via_sync else 'declined'} "
        f"this finding, Foundry-Defect {'kept' if via_add else 'declined'} it — "
        f"{description!r}"
    )
    assert via_sync is is_defect




@pytest.mark.parametrize("description", [DRIFT, COUNT, DIRECTION, ENUMERATION])
def test_sync_still_refuses_the_canonical_comment_prose_classes(run_env, description):
    """The no-regression half, at this branch rather than across doors.

    The promote-direction guard is biased to over-match on purpose, and
    over-matching is the safe direction — but a guard that matched EVERYTHING
    would refuse nothing and quietly delete the observation channel AC-001
    exists to fill. These four are the canonical comment-prose findings; each
    must still be kept OUT of the defect ledger through Sync, and after D-098
    each is refused there by name rather than silently rerouted.
    """
    project_root, fdir = run_env
    _sync_env(fdir)

    result = _sync(
        0, [_finding(description=description, target_kind="comment")], project_root
    )

    assert result.get("added", 0) == 0, result
    assert "comment-prose observation class" in result.get("error", ""), result
    assert json.loads((fdir / "defects.json").read_text(encoding="utf-8"))["defects"] == []




# --------------------------------------------------------------------------- #
# D-064 — the batch door REFUSES a non-dict finding, naming it
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("bad", ["not-a-dict", None, 42, ["nested"]])
def test_a_non_dict_finding_refuses_the_batch_naming_the_index(run_env, bad):
    """CT-002 verbatim: 'batch door refuses the whole batch' — naming the
    offending finding. AC-010. D-064.

    The INPUT side of the loop was unguarded while the STORED side was not:
    `findings=['not-a-dict']` raised `AttributeError: 'str' object has no
    attribute 'get'` on the first `finding.get(...)`. `@ledger_refusals` does
    not catch it, so server.py's outer net converted it into "This is an
    unhandled server-side error, not a refusal", naming no index and no field.
    The module's own D-128 comment celebrates fixing exactly this class for
    `_dict_records` — the stored half of the same loop.
    """
    project_root, fdir = run_env
    _sync_env(fdir)

    result = foundry_sync_defects(
        1,
        [_finding(), bad, _finding(symbol="other")],
        project_root,
    )

    assert result.get("ok") is not True, result
    assert "findings[1]" in result["error"], result
    assert result["refusals"][0]["index"] == 1
    assert result["refusals"][0]["field"] == "finding"
    assert type(bad).__name__ in result["refusals"][0]["reason"]
    # All-or-nothing: the two GOOD findings did not land either.
    ledger = json.loads((fdir / "defects.json").read_text(encoding="utf-8"))
    assert ledger["defects"] == []




def test_a_non_dict_finding_does_not_misalign_the_refusal_report(run_env):
    """The adjacent path the guard opens, closed deliberately.

    The refusal loop indexes `normalized[refused["index"]]["source"]`, so the
    two lists must stay index-aligned — a `continue` that skipped the
    `normalized` append would make every later refusal name the WRONG finding.
    Driven with a bad finding BEFORE a genuinely invalid one.
    """
    project_root, fdir = run_env
    _sync_env(fdir)

    result = foundry_sync_defects(
        1,
        [None, _finding(source="not-a-stream")],
        project_root,
    )

    assert result.get("ok") is not True
    by_index = {r["index"]: r for r in result["refusals"]}
    assert by_index[0]["field"] == "finding"
    assert by_index[1]["field"] == "source"
    assert by_index[1]["value"] == "not-a-stream"




def test_a_different_finding_is_not_folded_into_an_untiered_record(run_env):
    """The adjacent path: identity is (source, type, file, symbol), so a
    finding that differs on ANY of them is a new defect and lands as one. A
    matcher that swallowed unrelated findings would lose real work."""
    project_root, fdir = run_env
    _untiered_open(fdir)

    result = foundry_sync_defects(
        3,
        [{
            "source": "trace", "type": "UNWIRED", "symbol": "some_other_symbol",
            "file": "src/api/a.py", "class": "UNWIRED_SURFACE", "tier": "LIVE",
            "description": "a different symbol in the same file",
        }],
        project_root,
    )

    assert result["added"] == 1
    assert result["retiered"] == 0
    assert result["total_open"] == 2




def test_a_tiered_open_record_is_not_re_tiered(run_env):
    """Only an UNTIERED record takes this path. A stream re-filing a finding
    against a record some stream already classified must not silently rewrite
    that classification — the record is already answerable for its evidence."""
    project_root, fdir = run_env
    record = _untiered_open(fdir)
    record["tier"] = "LIVE"
    (fdir / "defects.json").write_text(
        json.dumps({"defects": [record]}), encoding="utf-8"
    )

    result = foundry_sync_defects(
        3,
        [{
            "source": "trace", "type": "UNWIRED", "symbol": "foundry_next",
            "file": "src/api/a.py", "class": "UNWIRED_SURFACE", "tier": "LATENT",
            "reproduction_attempted": "drove it; nothing reproduced this time",
            "description": "same identity, softer claim",
        }],
        project_root,
    )

    assert result["retiered"] == 0
    ledger = json.loads((fdir / "defects.json").read_text(encoding="utf-8"))
    assert ledger["defects"][0]["tier"] == "LIVE"




def test_neither_filing_door_refuses_a_latent_filing_for_a_missing_location(run_env):
    """D-101 driven, not merely read off the schema.

    The prose and the handler are two surfaces of one rule, and this run has
    already paid for the pair disagreeing (D-098). A LATENT filing with no
    location is ACCEPTED at both doors, so the withdrawn advertisement matches
    what the doors do.
    """
    from foundry_mcp.tools.foundry import foundry_add_defect

    project_root, fdir = run_env
    _sync_env(fdir)

    single = foundry_add_defect(
        cycle=0, source="trace", defect_type="UNWIRED",
        description="the retry path is never reached from the pool reaper",
        defect_class="SCAN_GAP", tier="LATENT",
        reproduction_attempted="drove every caller; none reach the branch",
        project_root=project_root,
    )
    assert single.get("defect_id"), single
    assert "file_path" not in str(single.get("error", ""))

    batch = _sync(
        0,
        [{
            "source": "prove", "type": "MISSING",
            "description": "the second reaper never observes the evicted entry",
            "tier": "LATENT",
            "reproduction_attempted": "swept both roots; 0 sites",
            "class": "SCAN_GAP",
        }],
        project_root,
    )
    assert batch.get("ok") is True, batch
    assert batch["added"] == 1, batch




# --------------------------------------------------------------------------- #
# D-077 (sync half) — one re-tier rule, two doors, one implementation
# --------------------------------------------------------------------------- #


def test_the_sync_door_calls_the_shared_retier_helper():
    """D-077: the match-and-re-tier rule lived twice — a loop inside
    `foundry_sync_defects` and a second copy inside `foundry_add_defect`. Two
    copies of 'which stored record IS this finding' means a stream's exit from
    an untiered record depends on WHICH door it happened to file through.
    `foundry.retier_matching_untiered` is the one implementation; this door
    calls it and re-implements nothing.
    """
    import inspect as _inspect

    source = _inspect.getsource(foundry_sync_defects)
    assert "retier_matching_untiered(" in source
    # The local loop is gone: no hand-rolled match on the four identity fields.
    assert 'd["retiered_in_cycle"]' not in source
    assert 'd["tier"] = norm["tier"]' not in source




def test_both_doors_render_the_retier_through_one_formatter(run_env):
    """The doors report under ONE pair of key names for a reason, and the screen
    has to honour that or the shape they share stops meaning anything. Driven
    through the real doors on one seeded ledger, so this is what a lead
    following `_blocking_defects`' hint actually sees."""
    from foundry_mcp.tools.foundry import foundry_add_defect

    project_root, fdir = run_env
    _sync_env(fdir)
    untiered = {
        "id": "D-001", "cycle": 0, "source": "trace", "type": "UNWIRED",
        "description": "filed before the tier axis existed", "spec_ref": "",
        "symbol": "handle", "file": "src/api/a.py", "status": "open",
        "fixed_in_cycle": None,
    }

    (fdir / "defects.json").write_text(
        json.dumps({"defects": [dict(untiered)]}), encoding="utf-8"
    )
    single = foundry_add_defect(
        cycle=0, source="trace", defect_type="UNWIRED",
        description="the same finding, re-filed with a tier",
        symbol="handle", file_path="src/api/a.py", defect_class="UNWIRED_SURFACE",
        tier="LATENT", reproduction_attempted="drove every caller; none reach it",
        project_root=project_root,
    )
    assert single["retiered_ids"] == ["D-001"], single
    assert "re-tiered" in _plain(format_result("Foundry-Defect", single))

    (fdir / "defects.json").write_text(
        json.dumps({"defects": [dict(untiered)]}), encoding="utf-8"
    )
    batch = _sync(
        0,
        [_finding(
            description="the same finding again, through the batch door",
            symbol="handle", file="src/api/a.py", tier="LATENT",
            reproduction_attempted="drove every caller; none reach it",
        )],
        project_root,
    )
    assert batch["retiered_ids"] == ["D-001"], batch
    assert "re-tiered" in _plain(format_result("Foundry-Sync", batch))




def test_both_filing_doors_audit_under_the_class_their_refusal_names(run_env):
    """D-083 / D-147, as a property of BOTH doors at once.

    `record_denylist_tripwire` re-derives the class through
    `vocab.never_demote_class`, whose security entry reads `description` alone,
    while the refusal keys on every prose value the filing carries. So a claim
    that lives in some OTHER key is refused SECURITY_PROPERTY_CLAIM and, unless
    the door hands over `tripwire_finding`'s shape, audited NON_COMMENT — one
    event, two artifacts that contradict each other. `foundry_add_defect`
    wrapped its finding; the batch door in this module did not.
    """
    from foundry_mcp.tools.foundry import foundry_add_defect
    from foundry_mcp.tools.orchestration.fix_gate import foundry_sync_defects

    project_root, fdir = run_env
    _write_state(fdir, phase="F2", cycle=1)
    _defect_ledger(fdir, [])

    # The claim is NOT in `description`; it is in a key the caller invented.
    smuggled = {
        "description": "a gap in the request path",
        "evidence": _SECURITY_CLAIM,
        "source": "prove", "tier": "LATENT", "class": "K",
        "reproduction_attempted": "read the handler end to end, drove nothing",
    }

    batch = foundry_sync_defects(
        cycle=1, findings=[dict(smuggled)], project_root=project_root
    )
    assert batch.get("ok") is not True, batch
    assert "SECURITY_PROPERTY_CLAIM" in json.dumps(batch), batch

    single = foundry_add_defect(
        cycle=1, source="prove", defect_type="UNWIRED",
        description=smuggled["description"], tier="LATENT", defect_class="K",
        reproduction_attempted=smuggled["reproduction_attempted"],
        spec_ref=_SECURITY_CLAIM, project_root=project_root,
    )
    assert single.get("ok") is not True, single

    classes = _tripwire_classes(fdir)
    assert classes, "neither door wrote an audit record"
    assert set(classes) == {"SECURITY_PROPERTY_CLAIM"}, classes




def test_the_batch_re_tier_carries_the_re_filings_provenance(run_env):
    """fallout D-101 / FR-025 / CT-019 (concern C-070) — the second call site.

    Casting 4's D-101 fix gave `retier_matching_untiered` `fallout_of` and
    `supersedes`, DEFAULTED so this door kept compiling while it was repointed.
    Defaulted is not passed: unrepointed, a batch re-filing that declared either
    field classified the record and dropped both, so the same finding through
    the two doors left differently-measured records.

    Driven at BOTH doors over the same arrangement, because "the batch door
    writes it" and "the two doors write the same thing" are separate claims and
    only the pair is the contract.
    """
    project_root, fdir = run_env

    def _untiered_pair() -> None:
        (fdir / "defects.json").write_text(
            json.dumps({"defects": [
                {
                    "id": "D-001", "status": "open", "tier": "LIVE",
                    "source": "prove", "type": "WRONG", "file": "src/parent.py",
                    "symbol": "parent", "class": "c", "cycle": 0,
                    "description": "the parent this finding is fallout of",
                },
                {
                    "id": "D-002", "status": "open",
                    "source": "trace", "type": "UNWIRED", "file": "src/api/a.py",
                    "symbol": "handle", "class": "c", "cycle": 0,
                    "description": "handler never calls the store",
                },
            ]}),
            encoding="utf-8",
        )
        _write_state(fdir, phase="F2", cycle=0)

    _untiered_pair()
    batch = _sync(0, [_finding(tier="LIVE", fallout_of="D-001")], project_root)
    assert batch["retiered_ids"] == ["D-002"], batch
    from_batch = {
        d["id"]: d
        for d in json.loads((fdir / "defects.json").read_text(encoding="utf-8"))["defects"]
    }["D-002"]

    _untiered_pair()
    single = foundry_add_defect(
        cycle=0, source="trace", defect_type="UNWIRED",
        description="handler never calls the store", symbol="handle",
        file_path="src/api/a.py", defect_class="c", tier="LIVE",
        fallout_of="D-001", project_root=project_root,
    )
    assert single["retiered_ids"] == ["D-002"], single
    from_single = {
        d["id"]: d
        for d in json.loads((fdir / "defects.json").read_text(encoding="utf-8"))["defects"]
    }["D-002"]

    # The declared parent reaches the record the batch door classified...
    assert from_batch["fallout_of"] == "D-001", from_batch
    # ...and BOTH keys are present whatever the filing declared, because
    # `fallout_rows` reads key PRESENCE as "this record was measured".
    assert "supersedes" in from_batch, from_batch
    # ...and the two doors leave the same provenance behind.
    for key in ("fallout_of", "supersedes"):
        assert from_batch[key] == from_single[key], (key, from_batch, from_single)


# --------------------------------------------------------------------------- #
# fallout GI-004 / D-157 (casting 4's concern C-075) — THE DENYLIST AUDIT IS
# NOT DOOR-DEPENDENT.
# --------------------------------------------------------------------------- #


def test_the_batch_door_audits_the_re_tier_it_refuses(run_env):
    """fallout GI-004 / NFR-004 (C-075) — the sibling casting 4 could not drive.

    D-157 gave `retier_matching_untiered` a tier guard: before it classifies an
    open untiered record, it puts the record THAT MUTATION WOULD LEAVE through
    `validate_defect_filing` under the declared tier, so a record whose own
    prose the tier forbids is refused instead of minted. The ENFORCEMENT half
    reached both doors with no call-site change — the guard raises
    `LedgerRefusal`, the transaction writes nothing, and `@ledger_refusals`
    shapes the house refusal here exactly as at `foundry_add_defect`.

    The AUDIT half did not. `record_denylist_tripwire` needs the resolved run
    dir, which a pure list mutator does not hold, so the guard takes `fdir`
    DEFAULTED — for the same reason C-070 defaulted `fallout_of` and
    `supersedes`: so this file kept compiling while it was repointed. Defaulted
    is not passed, and this door did not pass it, so a denylisted claim re-tiered
    through the BATCH door was refused and NOT audited. D-061's ruling is that
    the tripwire may not be rung-dependent; door-dependent is the same defect one
    axis over, and this is the door a whole INSPECT stream files through.

    Casting 4 deliberately left `test_the_batch_door_refuses_the_security_claim_re_tier_as_well`
    silent about the tripwire — "a pin that asserted it is EMPTY would pin the
    gap as correct" — and named this test as the one that lands with the
    repoint. It is the batch-door sibling of
    `tests/test_defect_tier.py#test_the_retier_refuses_a_record_whose_own_prose_is_a_security_claim`.
    """
    project_root, fdir = run_env
    # The claim is on the RECORD and the re-filing's prose is innocent, so the
    # door's own denylist rung — which reads the FILING — has nothing to match.
    # Only the re-tier guard, which reads the record the mutation would leave,
    # can reach it.
    record = _untiered_open(fdir)
    record["description"] = _SECURITY_CLAIM
    record["spec_ref"] = ""
    (fdir / "defects.json").write_text(
        json.dumps({"defects": [record]}), encoding="utf-8"
    )

    result = foundry_sync_defects(
        cycle=3,
        findings=[_finding(
            source="trace", type="UNWIRED", tier="HARDENING", spec_ref="",
            symbol="foundry_next", file="src/api/a.py",
            **{"class": "UNWIRED_SURFACE"},
            description="re-filed with the tier the record never carried",
            reproduction_attempted=(
                "drove every caller of the display path; none reaches the "
                "branch, so nothing reproduced"
            ),
        )],
        project_root=project_root,
    )

    # The enforcement half, unchanged: refused, and nothing minted.
    assert vocab.SECURITY_PROPERTY_CLAIM in result["error"], result
    records = json.loads((fdir / "defects.json").read_text(encoding="utf-8"))["defects"]
    assert len(records) == 1 and "tier" not in records[0], records

    # The audit half, which is what this repoint adds. The ATTEMPT is what the
    # record exists to capture, and it is written even though nothing reached
    # the defect ledger (D-061).
    assert _tripwire_classes(fdir) == [vocab.SECURITY_PROPERTY_CLAIM], (
        _tripwire_classes(fdir)
    )


def test_the_batch_door_writes_no_tripwire_for_a_re_tier_it_allows(run_env):
    """fallout GI-004 (C-075) — the control, so the audit is not simply always-on.

    A re-tier into LIVE demotes nothing — LIVE is the one tier that holds a gate
    shut, which is why `NON_BLOCKING_TIERS` is the guard's scope — so the same
    record and the same claim produce a classified record and NO tripwire.
    A repoint that wrote an audit row on every re-tier would make the ledger
    useless in the other direction.
    """
    project_root, fdir = run_env
    record = _untiered_open(fdir)
    record["description"] = _SECURITY_CLAIM
    record["spec_ref"] = ""
    (fdir / "defects.json").write_text(
        json.dumps({"defects": [record]}), encoding="utf-8"
    )

    result = foundry_sync_defects(
        cycle=3,
        findings=[_finding(
            source="trace", type="UNWIRED", tier="LIVE", spec_ref="",
            symbol="foundry_next", file="src/api/a.py",
            **{"class": "UNWIRED_SURFACE"},
            description="re-filed with the tier the record never carried",
            reproduction_attempted="",
        )],
        project_root=project_root,
    )

    assert result.get("error") is None, result
    records = json.loads((fdir / "defects.json").read_text(encoding="utf-8"))["defects"]
    assert records[0]["tier"] == "LIVE", records[0]
    assert _tripwire_classes(fdir) == [], _tripwire_classes(fdir)
