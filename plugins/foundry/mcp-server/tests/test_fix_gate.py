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
# D-050 — the ladder's second half: the equivalents that still cleared it
#
# D-038's fix refused the values cycle 2 named ("n/a", "TODO", "tested it
# manually", a test named for the defect's own symbol) and left a family of
# equivalents standing. Every value below was DRIVEN through the gate after
# that fix landed and ACCEPTED, against a defect on symbol=foundry_mark_stream:
# each clears the locator rule on a separator that delimits nothing, or the
# names-a-test rule on a bare "test" token, while referencing no test at all.
# "tests/" is the exact failure the helper's own comment says it exists to
# reject — "a string that references no test satisfies the gate's letter and
# none of its purpose".
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "ref",
    [
        # A separator that delimits nothing: dangling, leading or doubled.
        "tests/",
        ".test",
        "test.",
        "./test",
        "spec.",
        # A test marker beside a name that names nothing.
        "x.test",
        "a.spec",
        "t.test",
        "test/x",
        # Scaffolding with no test name attached — strips to empty.
        "src/foo.py::test_",
        # Well-formed shapes resolving to a placeholder or a negation.
        "foo.test.bar",
        "no.test.exists",
        "manual-test/none",
    ],
)
def test_the_accepted_equivalents_of_a_non_answer_are_refused(run_env, ref):
    """D-050 stated as the drive log: these are the values PROVE ran through the
    post-D-038 gate and watched close a defect."""
    project_root, fdir = run_env
    _seed_defect(fdir, symbol="foundry_mark_stream")

    result = foundry_mark_defect_fixed(
        defect_id="D-001",
        cycle=1,
        adjacent_path_statement=ADJACENT_STATEMENT,
        adjacent_path_test=ref,
        project_root=project_root,
    )

    assert result.get("ok") is not True, (ref, result)
    assert result["missing_fields"] == ["adjacent_path_test"]
    # The refusal quotes the value back, so the caller sees what was judged.
    assert ref in result["error"]

    data = json.loads((fdir / "defects.json").read_text(encoding="utf-8"))
    assert data["defects"][0]["status"] == "open"


def test_a_defect_with_no_symbol_or_file_still_gets_the_full_shape_ladder(run_env):
    """D-050: 'On a defect carrying neither symbol nor file both semantic rules
    are skipped entirely and x.test closes it.' The two semantic rules do need a
    symbol and a file, but the structural ones never did — the gate degrades to
    its shape rules, it does not switch off."""
    project_root, fdir = run_env
    _seed_defect(fdir, symbol="", file="")

    for ref in ("x.test", "tests/", "src/foo.py::test_", "manual-test/none"):
        result = foundry_mark_defect_fixed(
            defect_id="D-001",
            cycle=1,
            adjacent_path_statement=ADJACENT_STATEMENT,
            adjacent_path_test=ref,
            project_root=project_root,
        )
        assert result.get("ok") is not True, (ref, result)

    data = json.loads((fdir / "defects.json").read_text(encoding="utf-8"))
    assert data["defects"][0]["status"] == "open"


# --------------------------------------------------------------------------- #
# D-050 — the STATEMENT side, unchanged from cycle 2 until now
#
# Its only check was exact equality against the defect's own symbol, so a
# declaration that there IS no adjacent path satisfied a gate whose whole
# purpose is to make the fixer name one. FR-009 asks the statement to name who
# else calls this, what else transitions here, or what runs concurrently.
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "statement",
    [
        "x",
        "none",
        "n/a",
        "no adjacent paths",
        "the same path",
        "nothing",
        "-",
        "0",
    ],
)
def test_a_statement_that_names_no_adjacent_path_is_refused(run_env, statement):
    """D-050's drive log for the statement field: every one of these closed a
    defect after the test-reference half was fixed."""
    project_root, fdir = run_env
    _seed_defect(fdir, symbol="foundry_mark_stream")

    result = foundry_mark_defect_fixed(
        defect_id="D-001",
        cycle=1,
        adjacent_path_statement=statement,
        adjacent_path_test=ADJACENT_TEST,
        project_root=project_root,
    )

    assert result.get("ok") is not True, (statement, result)
    assert result["missing_fields"] == ["adjacent_path_statement"]
    assert "adjacent_path_statement" in result["error"]

    data = json.loads((fdir / "defects.json").read_text(encoding="utf-8"))
    assert data["defects"][0]["status"] == "open"


def test_a_wordy_denial_of_adjacency_is_refused_too(run_env):
    """The word-count floor alone is gameable: a fluent sentence asserting that
    no other path exists clears it and is still a refusal to answer. A fixer
    with no adjacent path has no adjacent-path test to reference either, so the
    gate is already unsatisfiable in that case — saying so plainly is better
    than letting the statement through and refusing the reference."""
    project_root, fdir = run_env
    _seed_defect(fdir, symbol="foundry_mark_stream")

    for statement in (
        "there are no other callers of this function anywhere in the tree",
        "nothing else touches this code path at all",
        "no adjacent callers exist for this helper",
        "the same path the defect was found on, nothing further",
    ):
        result = foundry_mark_defect_fixed(
            defect_id="D-001",
            cycle=1,
            adjacent_path_statement=statement,
            adjacent_path_test=ADJACENT_TEST,
            project_root=project_root,
        )
        assert result.get("ok") is not True, (statement, result)
        assert result["missing_fields"] == ["adjacent_path_statement"]


def test_the_statement_may_not_restate_the_defects_own_file_either(run_env):
    """The pre-existing rule covered the symbol only. Naming the file the defect
    was found in is the same non-answer in the other field."""
    project_root, fdir = run_env
    _seed_defect(fdir, symbol="refresh_session", file="src/auth/session.py")

    result = foundry_mark_defect_fixed(
        defect_id="D-001",
        cycle=1,
        adjacent_path_statement="src/auth/session.py",
        adjacent_path_test=ADJACENT_TEST,
        project_root=project_root,
    )

    assert result.get("ok") is not True, result
    assert "src/auth/session.py" in result["error"]


@pytest.mark.parametrize(
    "statement",
    [
        # A negation mid-sentence is not a denial of adjacency — the anchored
        # patterns must not swallow a statement that names real paths.
        "login_handler calls this too, and the sweeper does not hold the lock "
        "while it runs concurrently",
        "the retry worker and the admin backfill both reach this transition",
        "session_gc runs concurrently with refresh_session on the same store",
    ],
)
def test_a_real_statement_naming_other_paths_is_accepted(run_env, statement):
    """The rules reject non-answers; a false refusal here blocks a real fix
    behind a gate the teammate cannot satisfy, which is worse than the defect."""
    project_root, fdir = run_env
    _seed_defect(fdir, symbol="refresh_session")

    result = foundry_mark_defect_fixed(
        defect_id="D-001",
        cycle=1,
        adjacent_path_statement=statement,
        adjacent_path_test=ADJACENT_TEST,
        project_root=project_root,
    )

    assert result["ok"] is True, (statement, result)


# --------------------------------------------------------------------------- #
# D-076 — the over-correction: the gate refused the BEST answer there is
#
# D-050's fix added two adjacency patterns as UNANCHORED whole-string searches,
# under a comment claiming "Anchored patterns only". The claim was false of
# exactly those two, so any statement carrying "no other"/"nothing else"
# ANYWHERE was refused — including the clause that CLOSES an enumeration, which
# is the most rigorous form of the answer A-017 asks for. PROVE drove five good
# statements through the real gate and all five were refused, in GRIND, the
# phase where every defect must close.
#
# The fix is positional: a bound comes after what it bounds. The three tests
# below are the three obligations that leaves — the good forms are accepted,
# every D-050 non-answer is STILL refused, and a denial that bounds nothing is
# still refused.
# --------------------------------------------------------------------------- #


# The five statements PROVE drove; 5 of 5 were refused before this fix. Each
# names real adjacent paths and then closes the radius.
_BOUNDED_ENUMERATIONS = [
    "_current_cycle is also called by foundry_get_context and "
    "_format_status_display; no other module reads state.json directly, so "
    "those two are the adjacent callers.",
    "run_retry and close_pool both reach _helper, and the reaper thread scans "
    "the cache concurrently; there are no other transitions",
    "The retry branch in run_retry and the shutdown path in close_pool both "
    "reach _helper, and nothing else touches the cache concurrently.",
    "foundry_get_context and _format_status_display both read the counter, and "
    "no further callers exist outside tools/.",
    "Two transitions reach _helper besides the defect's, the retry branch and "
    "the shutdown path, and no additional threads write the cache.",
]


@pytest.mark.parametrize("statement", _BOUNDED_ENUMERATIONS)
def test_a_statement_that_bounds_its_enumeration_is_accepted(run_env, statement):
    """D-076: naming the adjacent paths and THEN closing the radius is an
    exhaustive answer, and the gate refused it as "declares that there is no
    adjacent path". A false refusal here is worse than a missed non-answer: it
    fires in GRIND, where the teammate has no way around it and no instruction
    for what to write instead."""
    project_root, fdir = run_env
    _seed_defect(fdir, symbol="refresh_session")

    result = foundry_mark_defect_fixed(
        defect_id="D-001",
        cycle=1,
        adjacent_path_statement=statement,
        adjacent_path_test=ADJACENT_TEST,
        project_root=project_root,
    )

    assert result["ok"] is True, (statement, result)


# The negative control PROVE ran alongside the finding: all nine values that
# closed a defect before D-050. Anchoring must not reopen any of them, so this
# is asserted as one set rather than left implicit across two other tests.
_D050_NON_ANSWERS = [
    "x",
    "none",
    "n/a",
    "no adjacent paths",
    "the same path",
    "nothing",
    "-",
    "0",
    "Nothing else calls it",
]


@pytest.mark.parametrize("statement", _D050_NON_ANSWERS)
def test_the_d050_non_answers_are_all_still_refused(run_env, statement):
    """D-076's negative control. The whole risk of loosening rule 2 is that it
    reopens D-050, so the nine values D-050 was filed on are driven here
    explicitly — including "Nothing else calls it", which is the one the
    loosened patterns used to be solely responsible for and which the anchored
    leading-negation pattern now catches."""
    project_root, fdir = run_env
    _seed_defect(fdir, symbol="foundry_mark_stream")

    result = foundry_mark_defect_fixed(
        defect_id="D-001",
        cycle=1,
        adjacent_path_statement=statement,
        adjacent_path_test=ADJACENT_TEST,
        project_root=project_root,
    )

    assert result.get("ok") is not True, (statement, result)
    assert result["missing_fields"] == ["adjacent_path_statement"]

    data = json.loads((fdir / "defects.json").read_text(encoding="utf-8"))
    assert data["defects"][0]["status"] == "open"


@pytest.mark.parametrize(
    "statement",
    [
        # A denial is a bound only if something was named for it to bound.
        # These open with a subject and a verb and then decline to answer, so
        # the coverage the unanchored patterns genuinely had is retained.
        "I found no other callers.",
        "The grep shows no other callers.",
        "Checked the tree: no other callers.",
        "A careful sweep found nothing else.",
    ],
)
def test_a_denial_that_bounds_nothing_is_still_refused(run_env, statement):
    """D-076 must not become D-050 again. Simply ^-anchoring the two patterns
    would have made them dead code — the leading-negation pattern already
    covers "^no" and "^nothing" — and these four would then have been ACCEPTED.
    They are refused because the words before the denial do not clear the same
    substance floor the whole statement must clear."""
    project_root, fdir = run_env
    _seed_defect(fdir, symbol="foundry_mark_stream")

    result = foundry_mark_defect_fixed(
        defect_id="D-001",
        cycle=1,
        adjacent_path_statement=statement,
        adjacent_path_test=ADJACENT_TEST,
        project_root=project_root,
    )

    assert result.get("ok") is not True, (statement, result)
    assert result["missing_fields"] == ["adjacent_path_statement"]
    # The refusal names the REMEDY, not just the prohibition: the closing
    # clause is welcome, it just has to follow the enumeration it closes.
    assert "AFTER the enumeration" in result["error"], result


def test_the_whole_statement_patterns_really_are_all_anchored():
    """D-076's root cause, pinned as the invariant the module claims.

    The comment above ``_STATEMENT_NON_ANSWERS`` asserted "Anchored patterns
    only" while two of its five members were unanchored whole-string searches.
    The prose was the only thing saying so and prose cannot fail, so the
    contradiction survived a full cycle. This derives the claim from the
    compiled patterns instead: every member of the whole-statement tuple must
    be ^-anchored, and any future member that is not fails here by pattern.

    The adjacency denials live in their own tuple precisely because they are
    NOT anchored — they are judged positionally by ``_unbounded_denial``, never
    by a bare whole-string search.
    """
    for pattern in fo._STATEMENT_NON_ANSWERS:
        assert pattern.pattern.startswith("^"), (
            f"{pattern.pattern!r} is judged against the WHOLE statement but is "
            f"not anchored, so it fires wherever the phrase appears — "
            f"including inside a clause that BOUNDS an enumeration. That is "
            f"D-076. Anchor it, or move it to _STATEMENT_ADJACENCY_DENIALS "
            f"where position is judged rather than assumed."
        )

    # ...and the denials that are deliberately unanchored are reachable only
    # through the positional judge, so the tuples cannot be quietly merged.
    assert fo._STATEMENT_ADJACENCY_DENIALS
    for pattern in fo._STATEMENT_ADJACENCY_DENIALS:
        assert not pattern.pattern.startswith("^"), pattern.pattern
        assert pattern not in fo._STATEMENT_NON_ANSWERS


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
