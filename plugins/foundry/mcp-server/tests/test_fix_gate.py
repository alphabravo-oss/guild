"""Foundry-Fix blast-radius gate — FR-009 / CT-001 / ST-004.

A-017: "Foundry-Fix gains required fields: an adjacent-path statement (who else
calls this / what else transitions here / what runs concurrently) and a
reference to a test exercising at least one adjacent path; the server refuses to
mark fixed without them, naming what is missing."

Before this, ``foundry_mark_defect_fixed`` validated nothing and could not
reject: it matched the first id, flipped ``status`` to fixed and returned ok.
That is the mechanism behind the run-shape this effort exists to fix — a defect
closes, the fix's blast radius is never considered, and the regression it opened
surfaces two cycles later as a fresh defect nobody connects to it.

Acceptance covered here:
  AC-012 / OT-004  a call carrying only defect_id and cycle is refused with a
                   message naming BOTH missing fields.
  AC-013           the referenced test drives a NAMED adjacent path distinct
                   from the path the defect was found on.
  CT-001 / ST-004  supplying both persists the declarations and sets status to
                   fixed; the refusal names each missing field.

Two further properties of the same call are pinned here because they are
properties of the WRITE, not of the gate:

  FR-005 / ST-001  ``fixed_in_cycle`` is stamped from the SERVER counter; the
                   caller's ``cycle`` is retained only as a declaration.
  FR-020 / AC-025  the read-modify-write is atomic under the shared ledger
                   lock, so concurrent fixes cannot discard one another.
"""

from __future__ import annotations

import asyncio
import json
import threading
from pathlib import Path

import pytest

from foundry_mcp.tools import foundry_orchestrator as fo
from foundry_mcp.tools import foundry_state
from foundry_mcp.tools.foundry_orchestrator import foundry_mark_defect_fixed


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #


@pytest.fixture
def run_env(tmp_path, monkeypatch):
    """Activate a foundry run under tmp_path; yield (project_root, fdir)."""
    project_root = tmp_path
    run_name = "fix-gate-run"
    fdir = project_root / "foundry-archive" / run_name
    (fdir / "castings").mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(
        fo,
        "_check_active_teams",
        lambda _pr: {"active": False, "teams": [], "live_panes": []},
    )

    foundry_state.set_active_run(run_name)
    try:
        yield str(project_root), fdir
    finally:
        foundry_state.clear_active_run()


def _set_cycle(fdir: Path, cycle: int) -> None:
    """Set the SERVER-side cycle counter — the only cycle the writer trusts."""
    (fdir / "state.json").write_text(
        json.dumps({"phase": "F3", "cycle": cycle}), encoding="utf-8"
    )


def _seed_defect(fdir: Path, defect_id: str = "D-001", **extra) -> None:
    defect = {
        "id": defect_id,
        "cycle": 0,
        "source": "trace",
        "type": "UNWIRED",
        "description": "session refresh never calls the token store",
        "spec_ref": "FR-001",
        "symbol": "refresh_session",
        "file": "src/auth/session.py",
        "status": "open",
        "fixed_in_cycle": None,
    }
    defect.update(extra)
    (fdir / "defects.json").write_text(
        json.dumps({"defects": [defect]}, indent=2), encoding="utf-8"
    )


# A statement and a test reference that name a genuinely ADJACENT path: the
# defect was found on `refresh_session`, and the declaration names a DIFFERENT
# caller and a test that drives that other caller (AC-013 / A-018).
ADJACENT_STATEMENT = (
    "login_handler and the background session-sweeper both call the token "
    "store; the sweeper runs concurrently with refresh."
)
ADJACENT_TEST = "tests/test_auth.py::test_sweeper_does_not_evict_a_live_session"


# --------------------------------------------------------------------------- #
# AC-012 / OT-004 — refusal names each missing field
# --------------------------------------------------------------------------- #


def test_fix_with_only_defect_id_and_cycle_is_refused_naming_both_fields(run_env):
    """OT-004 verbatim: a Foundry-Fix call with only defect_id and cycle is
    refused with a message naming the adjacent-path statement and the test
    reference as missing."""
    project_root, fdir = run_env
    _seed_defect(fdir)

    result = foundry_mark_defect_fixed(
        defect_id="D-001", cycle=1, project_root=project_root
    )

    assert "error" in result, result
    assert result.get("ok") is not True
    assert result["missing_fields"] == [
        "adjacent_path_statement",
        "adjacent_path_test",
    ]
    assert "adjacent_path_statement" in result["error"]
    assert "adjacent_path_test" in result["error"]

    # The refusal is a REFUSAL: the ledger is untouched.
    data = json.loads((fdir / "defects.json").read_text(encoding="utf-8"))
    assert data["defects"][0]["status"] == "open"
    assert data["defects"][0]["fixed_in_cycle"] is None


def test_refusal_names_only_the_field_that_is_actually_missing(run_env):
    """AC-012: 'naming each missing field' means each — a call supplying the
    statement but no test reference is told exactly that, not both."""
    project_root, fdir = run_env
    _seed_defect(fdir)

    result = foundry_mark_defect_fixed(
        defect_id="D-001",
        cycle=1,
        adjacent_path_statement=ADJACENT_STATEMENT,
        project_root=project_root,
    )

    assert result["missing_fields"] == ["adjacent_path_test"]
    assert "adjacent_path_test" in result["error"]

    result = foundry_mark_defect_fixed(
        defect_id="D-001",
        cycle=1,
        adjacent_path_test=ADJACENT_TEST,
        project_root=project_root,
    )

    assert result["missing_fields"] == ["adjacent_path_statement"]


def test_whitespace_only_declarations_do_not_satisfy_the_gate(run_env):
    """A blank declaration is an absent declaration. Accepting '   ' would make
    the gate a formality the first hurried teammate routes around."""
    project_root, fdir = run_env
    _seed_defect(fdir)

    result = foundry_mark_defect_fixed(
        defect_id="D-001",
        cycle=1,
        adjacent_path_statement="   ",
        adjacent_path_test="\t\n",
        project_root=project_root,
    )

    assert result["missing_fields"] == [
        "adjacent_path_statement",
        "adjacent_path_test",
    ]


def test_refusal_carries_an_actionable_hint(run_env):
    """Shared 'named-refusal error dict' pattern: the error names the offending
    items, the hint names the action."""
    project_root, fdir = run_env
    _seed_defect(fdir)

    result = foundry_mark_defect_fixed(
        defect_id="D-001", cycle=1, project_root=project_root
    )

    assert result["hint"]
    assert "test" in result["hint"].lower()


# --------------------------------------------------------------------------- #
# CT-001 / ST-004 — the accepted transition persists the declarations
# --------------------------------------------------------------------------- #


def test_supplying_both_declarations_fixes_the_defect_and_persists_them(run_env):
    """CT-001: 'defect status set to fixed with the declarations persisted'."""
    project_root, fdir = run_env
    _set_cycle(fdir, 2)
    _seed_defect(fdir)

    result = foundry_mark_defect_fixed(
        defect_id="D-001",
        cycle=2,
        adjacent_path_statement=ADJACENT_STATEMENT,
        adjacent_path_test=ADJACENT_TEST,
        project_root=project_root,
    )

    assert result["ok"] is True, result
    assert result["fixed_in_cycle"] == 2
    assert result["remaining_open"] == 0

    record = json.loads((fdir / "defects.json").read_text(encoding="utf-8"))["defects"][0]
    assert record["status"] == "fixed"
    assert record["fixed_in_cycle"] == 2
    assert record["adjacent_path_statement"] == ADJACENT_STATEMENT
    assert record["adjacent_path_test"] == ADJACENT_TEST


def test_declarations_are_mirrored_into_the_forge_log(run_env):
    """Shared 'ledger write mirrored to forge-log.md' pattern: the blast radius
    a fix claimed to have considered is readable in the human record too, not
    only in JSON."""
    project_root, fdir = run_env
    _seed_defect(fdir)
    (fdir / "forge-log.md").write_text("# Forge Log\n", encoding="utf-8")

    foundry_mark_defect_fixed(
        defect_id="D-001",
        cycle=2,
        adjacent_path_statement=ADJACENT_STATEMENT,
        adjacent_path_test=ADJACENT_TEST,
        project_root=project_root,
    )

    log = (fdir / "forge-log.md").read_text(encoding="utf-8")
    assert "D-001 FIXED" in log
    assert ADJACENT_STATEMENT in log
    assert ADJACENT_TEST in log


def test_a_missing_forge_log_never_fails_the_write(run_env):
    """The mirror is guarded: a run without forge-log.md still records the fix."""
    project_root, fdir = run_env
    _seed_defect(fdir)
    assert not (fdir / "forge-log.md").exists()

    result = foundry_mark_defect_fixed(
        defect_id="D-001",
        cycle=1,
        adjacent_path_statement=ADJACENT_STATEMENT,
        adjacent_path_test=ADJACENT_TEST,
        project_root=project_root,
    )

    assert result["ok"] is True, result


# --------------------------------------------------------------------------- #
# AC-013 — the adjacent path must be DISTINCT from the defect's own path
# --------------------------------------------------------------------------- #


def test_declared_path_must_differ_from_the_path_the_defect_was_found_on(run_env):
    """AC-013 / FR-010: 'the test drives a NAMED adjacent path distinct from the
    path the defect was found on'. Restating the defect's own symbol is the
    non-answer this gate exists to reject, and it is the one case the server can
    decide mechanically."""
    project_root, fdir = run_env
    _seed_defect(fdir, symbol="refresh_session")

    result = foundry_mark_defect_fixed(
        defect_id="D-001",
        cycle=1,
        adjacent_path_statement="refresh_session",
        adjacent_path_test=ADJACENT_TEST,
        project_root=project_root,
    )

    assert "error" in result, result
    assert "refresh_session" in result["error"]
    assert "adjacent_path_statement" in result["missing_fields"]

    data = json.loads((fdir / "defects.json").read_text(encoding="utf-8"))
    assert data["defects"][0]["status"] == "open"


def test_a_genuinely_adjacent_declaration_is_accepted(run_env):
    """AC-013's positive half: the accepted declaration names paths OTHER than
    the defect's own symbol, and the test reference names a test that drives one
    of them."""
    project_root, fdir = run_env
    _seed_defect(fdir, symbol="refresh_session")

    result = foundry_mark_defect_fixed(
        defect_id="D-001",
        cycle=1,
        adjacent_path_statement=ADJACENT_STATEMENT,
        adjacent_path_test=ADJACENT_TEST,
        project_root=project_root,
    )

    assert result["ok"] is True, result
    # The declared paths are genuinely other paths...
    assert "login_handler" in result["adjacent_path_statement"]
    assert "sweeper" in result["adjacent_path_statement"]
    assert "refresh_session" != result["adjacent_path_statement"]
    # ...and the test reference names one of them, not the defect's own symbol.
    assert "sweeper" in result["adjacent_path_test"]
    assert "refresh_session" not in result["adjacent_path_test"]


# --------------------------------------------------------------------------- #
# D-038 / AC-013 — the TEST REFERENCE is examined, not just the statement
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "junk",
    [
        "n/a",
        "N/A",
        "TODO",
        "tested it manually",
        "I ran the suite",
        "see the PR",
        "-",
    ],
)
def test_a_test_reference_that_references_no_test_is_refused(run_env, junk):
    """D-038 stated as the values that were driven and ACCEPTED.

    The gate examined the statement by exact equality and never looked at
    ``adjacent_path_test`` at all, so any non-empty string closed the defect.
    A-018 asks for "a reference to a test exercising at least one adjacent
    path"; none of these references a test.
    """
    project_root, fdir = run_env
    _seed_defect(fdir, symbol="refresh_session")

    result = foundry_mark_defect_fixed(
        defect_id="D-001",
        cycle=1,
        adjacent_path_statement=ADJACENT_STATEMENT,
        adjacent_path_test=junk,
        project_root=project_root,
    )

    assert result.get("ok") is not True, (junk, result)
    assert result["missing_fields"] == ["adjacent_path_test"]
    assert "adjacent_path_test" in result["error"]
    # The refusal quotes the value back, so the caller can see what was judged.
    assert junk in result["error"]

    data = json.loads((fdir / "defects.json").read_text(encoding="utf-8"))
    assert data["defects"][0]["status"] == "open"


def test_a_test_named_for_the_defects_own_symbol_is_refused(run_env):
    """AC-013 verbatim: 'the referenced test drives a named adjacent path
    DISTINCT from the path the defect was found on'. A well-formed reference
    that points straight back at the defect's own symbol satisfies the shape
    rules and none of the requirement."""
    project_root, fdir = run_env
    _seed_defect(fdir, symbol="refresh_session")

    result = foundry_mark_defect_fixed(
        defect_id="D-001",
        cycle=1,
        adjacent_path_statement=ADJACENT_STATEMENT,
        adjacent_path_test="tests/test_session.py::test_refresh_session",
        project_root=project_root,
    )

    assert result.get("ok") is not True, result
    assert result["missing_fields"] == ["adjacent_path_test"]
    assert "refresh_session" in result["error"]
    assert "adjacent" in result["error"].lower()


def test_the_defects_own_file_is_not_a_test_reference(run_env):
    """Naming the source file the defect was found in is the defect's own path
    restated in the other field."""
    project_root, fdir = run_env
    _seed_defect(fdir, symbol="refresh_session", file="src/auth/session_test.go")

    result = foundry_mark_defect_fixed(
        defect_id="D-001",
        cycle=1,
        adjacent_path_statement=ADJACENT_STATEMENT,
        adjacent_path_test="src/auth/session_test.go",
        project_root=project_root,
    )

    assert result.get("ok") is not True, result
    assert result["missing_fields"] == ["adjacent_path_test"]


def test_both_declarations_failing_are_named_in_one_refusal(run_env):
    """CT-001's 'naming each missing field' holds for junk exactly as it does
    for absence: a caller who supplied two non-answers is told about both,
    rather than being sent back twice."""
    project_root, fdir = run_env
    _seed_defect(fdir, symbol="refresh_session")

    result = foundry_mark_defect_fixed(
        defect_id="D-001",
        cycle=1,
        adjacent_path_statement="refresh_session",
        adjacent_path_test="n/a",
        project_root=project_root,
    )

    assert result.get("ok") is not True, result
    assert result["missing_fields"] == [
        "adjacent_path_statement",
        "adjacent_path_test",
    ]
    assert "adjacent_path_statement" in result["error"]
    assert "adjacent_path_test" in result["error"]
    assert {item["field"] for item in result["invalid_fields"]} == {
        "adjacent_path_statement",
        "adjacent_path_test",
    }
    assert all(item["reason"] for item in result["invalid_fields"])


@pytest.mark.parametrize(
    "ref",
    [
        # pytest
        "tests/test_auth.py::test_sweeper_evicts_stale_sessions",
        "tests/test_auth.py",
        # go
        "internal/auth/sweeper_test.go::TestSweeperEvictsStale",
        "TestSweeperEvictsStale#auth",
        # js / ts
        "src/auth/__tests__/sweeper.test.ts",
        "src/auth/sweeper.spec.ts",
        # rust / java-style qualified names
        "auth::sweeper::tests::evicts_stale_sessions",
        "AuthSweeperTest#evictsStaleSessions",
    ],
)
def test_real_test_references_across_languages_are_accepted(run_env, ref):
    """The rules reject non-answers; they must not reject the shapes a real
    reference takes. Foundry runs against Go, JS and Rust repos, so a
    pytest-only rule would refuse legitimate fixes — a false refusal here
    blocks work behind a gate the teammate cannot satisfy."""
    project_root, fdir = run_env
    _seed_defect(fdir, symbol="refresh_session")

    result = foundry_mark_defect_fixed(
        defect_id="D-001",
        cycle=1,
        adjacent_path_statement=ADJACENT_STATEMENT,
        adjacent_path_test=ref,
        project_root=project_root,
    )

    assert result["ok"] is True, (ref, result)
    assert result["adjacent_path_test"] == ref


def test_an_adjacent_test_sharing_the_symbols_prefix_is_still_accepted(run_env):
    """The own-symbol rule is exact equality after stripping test scaffolding,
    not a substring match: a test whose name STARTS with the defect's symbol but
    goes on to name another caller drives a genuinely adjacent path."""
    project_root, fdir = run_env
    _seed_defect(fdir, symbol="refresh_session")

    result = foundry_mark_defect_fixed(
        defect_id="D-001",
        cycle=1,
        adjacent_path_statement=ADJACENT_STATEMENT,
        adjacent_path_test="tests/test_auth.py::test_refresh_session_from_login_handler",
        project_root=project_root,
    )

    assert result["ok"] is True, result


def test_a_defect_with_no_symbol_only_gets_the_shape_rules(run_env):
    """The own-symbol comparison needs a symbol. Without one the reference is
    still required to BE a reference — the gate degrades, it does not switch
    off."""
    project_root, fdir = run_env
    _seed_defect(fdir, symbol="", file="")

    refused = foundry_mark_defect_fixed(
        defect_id="D-001",
        cycle=1,
        adjacent_path_statement=ADJACENT_STATEMENT,
        adjacent_path_test="TODO",
        project_root=project_root,
    )
    assert refused.get("ok") is not True, refused

    accepted = foundry_mark_defect_fixed(
        defect_id="D-001",
        cycle=1,
        adjacent_path_statement=ADJACENT_STATEMENT,
        adjacent_path_test=ADJACENT_TEST,
        project_root=project_root,
    )
    assert accepted["ok"] is True, accepted


# --------------------------------------------------------------------------- #
# FR-005 / ST-001 — the SERVER counter stamps the fix, not the caller
# --------------------------------------------------------------------------- #


def test_fixed_in_cycle_comes_from_the_server_counter_not_the_caller(run_env):
    """The lead's asserted cycle was persisted verbatim, so escalation — which
    reads these numbers back — accumulated against lead-asserted cycles while
    the server counter sat at 0. The server knows better, so the server wins."""
    project_root, fdir = run_env
    _set_cycle(fdir, 4)
    _seed_defect(fdir)

    result = foundry_mark_defect_fixed(
        defect_id="D-001",
        cycle=17,  # the lead's assertion, and it is wrong
        adjacent_path_statement=ADJACENT_STATEMENT,
        adjacent_path_test=ADJACENT_TEST,
        project_root=project_root,
    )

    assert result["fixed_in_cycle"] == 4
    record = json.loads((fdir / "defects.json").read_text(encoding="utf-8"))["defects"][0]
    assert record["fixed_in_cycle"] == 4

    # The assertion is not discarded — it is demoted to a declaration, so a
    # lead whose counter model has drifted is auditable after the fact.
    assert result["declared_cycle"] == 17
    assert record["declared_fixed_cycle"] == 17


def test_an_absent_counter_stamps_zero_rather_than_the_caller_value(run_env):
    """``_current_cycle`` is total: a run with no state.json reads 0. What must
    NOT happen is falling back to the caller's number."""
    project_root, fdir = run_env
    _seed_defect(fdir)

    result = foundry_mark_defect_fixed(
        defect_id="D-001",
        cycle=9,
        adjacent_path_statement=ADJACENT_STATEMENT,
        adjacent_path_test=ADJACENT_TEST,
        project_root=project_root,
    )

    assert result["fixed_in_cycle"] == 0


def test_the_forge_log_mirror_records_the_server_cycle(run_env):
    """The human record and the JSON ledger must not disagree about when a
    defect closed."""
    project_root, fdir = run_env
    _set_cycle(fdir, 6)
    _seed_defect(fdir)
    (fdir / "forge-log.md").write_text("# Forge Log\n", encoding="utf-8")

    foundry_mark_defect_fixed(
        defect_id="D-001",
        cycle=99,
        adjacent_path_statement=ADJACENT_STATEMENT,
        adjacent_path_test=ADJACENT_TEST,
        project_root=project_root,
    )

    log = (fdir / "forge-log.md").read_text(encoding="utf-8")
    assert "D-001 FIXED** in cycle 6" in log
    assert "cycle 99" not in log


# --------------------------------------------------------------------------- #
# FR-020 / AC-025 — the write is atomic under the shared ledger lock
# --------------------------------------------------------------------------- #


def test_concurrent_fixes_all_persist(run_env, monkeypatch):
    """Every other writer of defects.json holds ``ledger_transaction``; this
    one did not, so it was an unlocked load / mutate / save. A fix landing
    between a peer's read and write was discarded by the peer's ``.tmp``
    rename: the call returned ok and the defect stayed OPEN. A GRIND wave
    closes defects in parallel, so this is the normal case, not an edge one.

    The read-modify-write window is widened deliberately (a sleep between the
    ledger read and the write) so the outcome does not depend on how the
    scheduler happens to interleave. Under the lock the sleep is simply held
    inside the critical section; remove the lock and the losses are certain.
    """
    import time

    from foundry_mcp.tools import foundry as foundry_tools

    project_root, fdir = run_env
    _set_cycle(fdir, 3)

    count = 12
    defects = [
        {
            "id": f"D-{i:03d}",
            "cycle": 0,
            "source": "trace",
            "type": "UNWIRED",
            "description": f"defect {i}",
            "spec_ref": "FR-001",
            "symbol": f"symbol_{i}",
            "file": "src/auth/session.py",
            "status": "open",
            "fixed_in_cycle": None,
        }
        for i in range(1, count + 1)
    ]
    (fdir / "defects.json").write_text(
        json.dumps({"defects": defects}, indent=2), encoding="utf-8"
    )

    real_load = foundry_tools._load_json

    def _slow_load(path):
        data = real_load(path)
        if path.name == "defects.json":
            time.sleep(0.02)
        return data

    monkeypatch.setattr(foundry_tools, "_load_json", _slow_load)

    start = threading.Barrier(count)
    results: dict[str, dict] = {}
    lock = threading.Lock()

    def _fix(defect_id: str) -> None:
        start.wait()
        outcome = foundry_mark_defect_fixed(
            defect_id=defect_id,
            cycle=3,
            adjacent_path_statement=ADJACENT_STATEMENT,
            adjacent_path_test=ADJACENT_TEST,
            project_root=project_root,
        )
        with lock:
            results[defect_id] = outcome

    threads = [
        threading.Thread(target=_fix, args=(f"D-{i:03d}",)) for i in range(1, count + 1)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)

    assert all(r.get("ok") for r in results.values()), results
    assert len(results) == count

    # The claim under test: every fix that returned ok is actually ON DISK.
    # The old failure mode returned 12 oks and persisted 11 fixes.
    persisted = json.loads((fdir / "defects.json").read_text(encoding="utf-8"))["defects"]
    assert len(persisted) == count
    still_open = [d["id"] for d in persisted if d.get("status") != "fixed"]
    assert still_open == []
    assert all(d["fixed_in_cycle"] == 3 for d in persisted)


def test_a_refused_fix_leaves_the_ledger_untouched(run_env):
    """The refusal paths run inside the transaction too, so a rejected call
    must not rewrite (or half-rewrite) the record it read."""
    project_root, fdir = run_env
    _seed_defect(fdir)
    before = (fdir / "defects.json").read_text(encoding="utf-8")

    result = foundry_mark_defect_fixed(
        defect_id="D-001", cycle=1, project_root=project_root
    )

    assert "error" in result
    record = json.loads((fdir / "defects.json").read_text(encoding="utf-8"))["defects"][0]
    assert record["status"] == "open"
    assert "adjacent_path_statement" not in record
    assert json.loads(before)["defects"][0] == record


# --------------------------------------------------------------------------- #
# Preserved behaviour
# --------------------------------------------------------------------------- #


def test_unknown_defect_id_still_errors(run_env):
    """The pre-existing not-found refusal is unchanged, and is reported BEFORE
    the declaration check — the caller's first problem is the id."""
    project_root, fdir = run_env
    _seed_defect(fdir)

    result = foundry_mark_defect_fixed(
        defect_id="D-404", cycle=1, project_root=project_root
    )

    assert result["error"] == "Defect D-404 not found"


def test_no_active_run_is_refused(run_env):
    """Shared run-directory guard: every entry point opens with it."""
    _project_root, _fdir = run_env
    foundry_state.clear_active_run()

    result = foundry_mark_defect_fixed(
        defect_id="D-001",
        cycle=1,
        adjacent_path_statement=ADJACENT_STATEMENT,
        adjacent_path_test=ADJACENT_TEST,
        project_root=".",
    )

    assert result["error"] == "No active foundry run."


# --------------------------------------------------------------------------- #
# The MCP surface agrees with the runtime gate
# --------------------------------------------------------------------------- #


def test_tool_schema_declares_both_but_leaves_the_ladder_reachable():
    """D-039 / CT-001: the two declarations are DECLARED but deliberately not in
    the schema's ``required`` array.

    They were, and it made CT-001 unsatisfiable over MCP. The SDK validates
    arguments against ``inputSchema`` before dispatch and returns on the FIRST
    jsonschema error, so a caller omitting both was told about one of them and
    never reached the handler's ladder — the only code that can name more than
    one missing field. The obligation lives in the descriptions and in the
    handler, which refuses unconditionally.
    """
    from foundry_mcp import server as foundry_server

    tools = asyncio.run(foundry_server.list_tools())
    fix = next(t for t in tools if t.name == "Foundry-Fix")

    assert set(fix.inputSchema["required"]) == {"defect_id", "cycle"}
    props = fix.inputSchema["properties"]
    assert "adjacent_path_statement" in props
    assert "adjacent_path_test" in props
    # The descriptions carry the semantics A-017 / A-018 specify, and say the
    # fields are mandatory even though the validator will not enforce it.
    assert "concurrently" in props["adjacent_path_statement"]["description"]
    assert "REQUIRED" in props["adjacent_path_statement"]["description"]
    assert "adjacent" in props["adjacent_path_test"]["description"].lower()
    assert "REQUIRED" in props["adjacent_path_test"]["description"]
    # The tool description still states the refusal, so a lead reading the tool
    # list learns the contract the validator no longer advertises.
    assert "REFUSED" in fix.description


def test_an_mcp_caller_omitting_both_declarations_sees_the_multi_field_refusal(run_env):
    """D-039 driven at the boundary an MCP caller actually crosses.

    Reproduces the SDK's own pre-dispatch step — ``jsonschema.validate`` against
    the advertised ``inputSchema``, exactly as
    ``mcp.server.lowlevel.Server.call_tool``'s handler runs it — and then the
    dispatch. Before the fix, validation raised here and the assertion below
    could never run: the caller saw a single "'adjacent_path_statement' is a
    required property" and was never told the test reference was missing too.
    """
    import jsonschema

    from foundry_mcp import server as foundry_server

    project_root, fdir = run_env
    _seed_defect(fdir)

    tools = asyncio.run(foundry_server.list_tools())
    fix = next(t for t in tools if t.name == "Foundry-Fix")
    args = {"defect_id": "D-001", "cycle": 2}

    # Step 1 — the SDK's validation must let this through, or the handler's
    # refusal is unreachable no matter how good it is.
    jsonschema.validate(instance=args, schema=fix.inputSchema)

    # Step 2 — the dispatch the SDK performs next.
    previous_root = foundry_server._project_root
    try:
        foundry_server._project_root = project_root
        result = foundry_server._DISPATCH["Foundry-Fix"](args)
    finally:
        foundry_server._project_root = previous_root

    assert result.get("ok") is not True, result
    assert result["missing_fields"] == [
        "adjacent_path_statement",
        "adjacent_path_test",
    ]
    # CT-001's "naming each missing field" — both, in the one message the
    # caller is shown.
    assert "adjacent_path_statement" in result["error"]
    assert "adjacent_path_test" in result["error"]

    data = json.loads((fdir / "defects.json").read_text(encoding="utf-8"))
    assert data["defects"][0]["status"] == "open"


def test_a_complete_call_still_validates_against_the_schema(run_env):
    """NFR-002's no-narrowing half: loosening ``required`` must not have made a
    well-formed call invalid, and the optional properties still type-check."""
    import jsonschema

    from foundry_mcp import server as foundry_server

    tools = asyncio.run(foundry_server.list_tools())
    fix = next(t for t in tools if t.name == "Foundry-Fix")

    jsonschema.validate(
        instance={
            "defect_id": "D-001",
            "cycle": 2,
            "adjacent_path_statement": ADJACENT_STATEMENT,
            "adjacent_path_test": ADJACENT_TEST,
        },
        schema=fix.inputSchema,
    )
    # A wrongly-typed declaration is still caught by the validator — dropping
    # the fields from `required` did not drop their schema.
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(
            instance={"defect_id": "D-001", "cycle": 2, "adjacent_path_test": 7},
            schema=fix.inputSchema,
        )


def test_dispatch_threads_both_declarations_through(run_env):
    """The registration half: server.py's Foundry-Fix lambda must actually pass
    the two new arguments, or the schema would advertise fields the handler
    never sees."""
    project_root, fdir = run_env
    _seed_defect(fdir)

    from foundry_mcp import server as foundry_server

    monkey_root = foundry_server._project_root
    try:
        foundry_server._project_root = project_root
        result = foundry_server._DISPATCH["Foundry-Fix"]({
            "defect_id": "D-001",
            "cycle": 3,
            "adjacent_path_statement": ADJACENT_STATEMENT,
            "adjacent_path_test": ADJACENT_TEST,
        })
    finally:
        foundry_server._project_root = monkey_root

    assert result["ok"] is True, result
    assert result["adjacent_path_statement"] == ADJACENT_STATEMENT
    assert result["adjacent_path_test"] == ADJACENT_TEST
