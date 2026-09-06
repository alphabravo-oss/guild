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
import hashlib
import json
import threading
from pathlib import Path

import pytest

from foundry_mcp.schemas.vocab import (
    FIX_AUTHORS,
    HANDOFF_EVENT_LEAD_FIX,
    LEAD_LANE_MAX_FILES,
    LEAD_LANE_MAX_LINES,
    PYTEST_CONFTEST_BASENAME,
    PYTEST_PYTHON_FILES,
    PYTEST_TESTPATHS,
    is_test_file,
)
from foundry_mcp.tools import foundry_state

# fallout FR-005 / GI-010 / GI-026 / AC-014 — THE FIX GATE HAS A MODULE.
#
# The `fo` alias reached the whole adjacent-path ladder through one name; the
# single orchestrator module it named is gone and GI-010 forbids a re-export
# shim standing in for it. Everything this module drives —
# `foundry_mark_defect_fixed`, `_PYTEST_DISCOVERY_PHRASE`,
# `_split_pytest_node_id`, `_lead_lane_problem`, `_regression_test_problem`,
# `_numstat_measurement` — is `orchestration/fix_gate.py`. So, now, is
# `_decode_git_path`: it used to be DEFINED in `orchestration/width.py` and
# only ever READ from here, and GI-033 refuses a lifecycle-to-verifier read
# with no exception in that direction, so it moved to its sole consumer. There
# was no row available to keep it where it was. The boundary guard's
# layering-debt allowlist — a `(home, imported) -> reason` dict the layering
# assertions consulted before judging an edge — was DELETED at bd6db9d
# (D-021 / D-035), because every row in it was a real violation wearing an
# explanation. The only exception the scan honours now is
# `_VERIFIER_TO_LIFECYCLE_SEAM`: GI-033's own named seam, transitions into
# halt, one edge and one-way. An edge that is not that seam FAILS whatever
# reason anyone could write beside it, so the symbol moved rather than the rule
# bending. The unit test below names fix_gate for the same reason it once named
# width — a failure there belongs in the module that owns the decoder.
from foundry_mcp.tools.orchestration import fix_gate as _fix_gate
from foundry_mcp.tools.orchestration.fix_gate import (
    _PYTEST_DISCOVERY_PHRASE,
    foundry_mark_defect_fixed as _mark_defect_fixed,
)

# `_check_active_teams` is bound by name in every orchestration module that
# reads it, so patching the one that DEFINES it leaves every importer on the
# real one.
from tests.orchestration._env import patch_everywhere


# --------------------------------------------------------------------------- #
# CT-005 / AC-020 — every Foundry-Fix now declares WHO WROTE THE FIX, and a
# teammate-authored one states back the hash of the prompt it was dispatched
# with (CT-011 / AC-030).
#
# Those are two more fields on ~100 call sites whose subject is the ADJACENT-PATH
# ladder and nothing else. Threading them through by hand would have edited a
# hundred assertions to test one requirement, so they are supplied once here and
# every existing call reads exactly as it did. `setdefault`, never assignment:
# each test that IS about authorship overrides them and its override wins, which
# is the property that makes this a fixture rather than a mask.
#
# The default is `teammate` because that is what the ladder below was written
# about — a GRIND teammate closing a defect it was dispatched to fix. The lead
# lane gets its own tests, which pass `authored_by="lead"` explicitly.
# --------------------------------------------------------------------------- #

#: The casting whose prompt `run_env` seeds and whose hash the wrapper reports.
FIXTURE_CASTING_ID = 1
FIXTURE_PROMPT_TEXT = "# Casting 1\n\nThe pre-authored prompt this run dispatched.\n"


def _published_prompt_hash(text: str = FIXTURE_PROMPT_TEXT) -> str:
    """The hash spelling `foundry_spawn` publishes and the gate compares.

    Derived here rather than imported so the test states the FORM independently:
    "sha256:" plus the first sixteen hex characters. A test that imported
    `_hash_str` would agree with the implementation by construction and could
    never catch it changing spelling.
    """
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def foundry_mark_defect_fixed(**kwargs):
    """`foundry_mark_defect_fixed` with the tool-wide declarations supplied."""
    kwargs.setdefault("authored_by", "teammate")
    if kwargs["authored_by"] == "teammate":
        kwargs.setdefault("casting_id", FIXTURE_CASTING_ID)
        kwargs.setdefault("prompt_hash", _published_prompt_hash())
    return _mark_defect_fixed(**kwargs)


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
    # CT-011: the prompt file the reported hash is checked against. Pointer
    # dispatch hands a teammate a path and a hash instead of the text, and the
    # fix gate is one of the two doors that makes that checkable — so the file
    # has to exist for the honest path to be exercised at all.
    (fdir / "castings" / f"casting-{FIXTURE_CASTING_ID}-prompt.md").write_text(
        FIXTURE_PROMPT_TEXT, encoding="utf-8"
    )

    patch_everywhere(
        monkeypatch,
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

# The reference used where the STATEMENT is the thing under test. It names a
# test FILE rather than a test function, so D-092's linkage rung — which
# relates a named test to the paths the statement named — does not apply to it,
# and each statement-ladder case stays about one rule.
#
# Pairing a statement-ladder case with ADJACENT_TEST asserts that an arbitrary
# statement links to a canned sweeper test, and after D-092 the server refuses
# exactly that pairing — correctly, because it IS the pairing D-092 was filed
# on ("the statement names two adjacent paths; the referenced test drives
# neither"). Nine such fixtures existed here, and the requirement they were
# written for is the statement's, not the reference's.
STATEMENT_LADDER_TEST = "tests/test_adjacent_paths.py"


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
        adjacent_path_test=STATEMENT_LADDER_TEST,  # the statement is what is under test
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
        adjacent_path_test=STATEMENT_LADDER_TEST,  # the statement is what is under test
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
    for pattern in _fix_gate._STATEMENT_NON_ANSWERS:
        assert pattern.pattern.startswith("^"), (
            f"{pattern.pattern!r} is judged against the WHOLE statement but is "
            f"not anchored, so it fires wherever the phrase appears — "
            f"including inside a clause that BOUNDS an enumeration. That is "
            f"D-076. Anchor it, or move it to _STATEMENT_ADJACENCY_DENIALS "
            f"where position is judged rather than assumed."
        )

    # ...and the denials that are deliberately unanchored are reachable only
    # through the positional judge, so the tuples cannot be quietly merged.
    assert _fix_gate._STATEMENT_ADJACENCY_DENIALS
    for pattern in _fix_gate._STATEMENT_ADJACENCY_DENIALS:
        assert not pattern.pattern.startswith("^"), pattern.pattern
        assert pattern not in _fix_gate._STATEMENT_NON_ANSWERS


# --------------------------------------------------------------------------- #
# D-085 — D-076's sibling, one pattern over: the shared resource as SUBJECT
#
# D-076 fixed the two unanchored adjacency patterns. The leading `same` rule was
# not in scope and carried the same over-refusal for the same reason: it read a
# phrase rather than a position. `^(?:the\s+)?same\b` claimed every statement
# that OPENS on the resource two paths share — an index.lock, a roll-up file,
# state.json — and answered it with "declares that there is no adjacent path",
# which is the opposite of what such a statement says. Two distinct real paths
# meeting at one resource is A-017's "what runs concurrently" answered exactly.
#
# The rule was lexical, not semantic, and the rephrasing below is the proof:
# move the resource off the front of the sentence and the identical claim was
# always accepted. The narrowed pattern reads the noun that actually carries a
# restatement of the defect's OWN path — "same path", "same as".
# --------------------------------------------------------------------------- #


# The three statements PROVE drove through the real gate; 3 of 3 were refused
# before this fix. Each names TWO distinct real paths meeting at one resource.
_SHARED_RESOURCE_STATEMENTS = [
    "The same index.lock is taken by the pathspec commit path and by "
    "foundry_validate's git query, which is the concurrent interaction.",
    "The same roll-up file is written by foundry_mark_stream and read by "
    "_streams_complete_check, so those two are the adjacent writers.",
    "Same state.json is read by foundry_get_context and written by "
    "foundry_mark_phase_complete.",
]


@pytest.mark.parametrize("statement", _SHARED_RESOURCE_STATEMENTS)
def test_a_shared_resource_named_first_is_an_answer_not_a_restatement(run_env, statement):
    """D-085. Sharing a lock, a file or a record IS the adjacency — refusing to
    hear it because the sentence opens on the resource fires in GRIND, the
    phase where every defect must close, against the fixer who did the most
    precise version of the work FR-009 asks for."""
    project_root, fdir = run_env
    _seed_defect(fdir, symbol="refresh_session")

    result = foundry_mark_defect_fixed(
        defect_id="D-001",
        cycle=1,
        adjacent_path_statement=statement,
        adjacent_path_test=STATEMENT_LADDER_TEST,  # the statement is what is under test
        project_root=project_root,
    )

    assert result["ok"] is True, (statement, result)


def test_moving_the_resource_off_the_front_cannot_change_the_verdict():
    """The finding's own control, pinned. These two sentences name the same two
    paths, make the same claim about the same resource, and differ only in
    which one opens the sentence — the second was ACCEPTED while the first was
    REFUSED. A gate that splits an identical claim on word order is reading the
    phrase and not the answer, so the pair is asserted together rather than
    leaving the equivalence to the prose."""
    resource_first = _SHARED_RESOURCE_STATEMENTS[0]
    paths_first = (
        "The pathspec commit path and foundry_validate's git query both take "
        "index.lock, which is the concurrent interaction."
    )

    assert _fix_gate._statement_problem(resource_first, "", "") is None
    assert _fix_gate._statement_problem(paths_first, "", "") is None


# What the narrowed rule still owns: the two nouns that restate the path the
# defect was found on rather than name one beside it.
#   1. among the nine D-050 non-answers, and under the word floor too.
#   2. among the wordy denials, and the ONE statement in the whole pinned
#      corpus this rule alone refuses — "nothing further" matches neither
#      adjacency denial and nine words clear the floor.
#   3. the "same as" half, which nothing else covers either.
# Every other pinned non-answer is independently held by the leading-negation
# pattern, `_unbounded_denial`, or the word floor, so the narrowing could not
# have reopened them. Module-level for the same reason `_D050_NON_ANSWERS` is:
# the evidence drive reads this list rather than retyping it, so a corpus that
# drifts drifts in one place.
_OWN_PATH_RESTATEMENTS = [
    "the same path",
    "the same path the defect was found on, nothing further",
    "same as the defect, no new path involved",
]


@pytest.mark.parametrize("statement", _OWN_PATH_RESTATEMENTS)
def test_the_narrowed_rule_still_refuses_a_literal_own_path_restatement(run_env, statement):
    """D-085 must not become D-050 again. The risk of loosening a rule for the
    second time is that the third loosening has nothing left to protect, so
    what survives is driven explicitly rather than assumed."""
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


def test_no_whole_statement_pattern_claims_a_shared_resource_statement():
    """D-085 derived from the compiled patterns, the way D-076's anchoring
    invariant is.

    The end-to-end tests above say the shared-resource statements are accepted;
    this says WHY, which is the part a future edit can break silently. No
    member of the whole-statement tuple may fire on them — a pattern that does
    is asserting something about the statement that the statement denies, and
    the refusal text it produces ("declares that there is no adjacent path") is
    then false of what the caller wrote. The same tuple must still own the
    restatement no other rule catches, so widening is caught in one direction
    and gutting in the other.
    """
    for statement in _SHARED_RESOURCE_STATEMENTS:
        normalized = " ".join(statement.split())
        for pattern in _fix_gate._STATEMENT_NON_ANSWERS:
            assert not pattern.search(normalized), (
                f"{pattern.pattern!r} claims {normalized!r}, which names two "
                f"distinct real paths meeting at one shared resource — that is "
                f"A-017's 'what runs concurrently' answered exactly. Refusing "
                f"it as a declaration that no adjacent path exists is D-085."
            )

    assert any(
        pattern.search("the same path the defect was found on, nothing further")
        for pattern in _fix_gate._STATEMENT_NON_ANSWERS
    ), (
        "nothing in the whole-statement tuple refuses a literal restatement of "
        "the defect's own path any more. The word floor does not reach it (it "
        "clears four words) and neither adjacency denial matches 'nothing "
        "further', so this rule is the only thing standing between that "
        "statement and a closed defect."
    )


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
    # The mirror now also carries the tier and the author (GI-003 / CT-001): a
    # lead reading forge-log.md to see what closed needs to know whether a fix
    # was lead-authored, and on what evidence tier, without opening the ledger.
    assert "D-001 FIXED** (unknown, by teammate) in cycle 6" in log
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

    # CT-005 / AC-020 added `authored_by`, and adding it is CORRECT on exactly
    # the reasoning above: D-039's problem was that TWO fields in `required`
    # made a two-field refusal unreachable, since the SDK returns on the first
    # error. One more single field costs no message — a caller omitting only
    # `authored_by` gets a refusal naming `authored_by`, which is the whole
    # answer. What must stay out is the adjacent-path PAIR, and it does.
    assert set(fix.inputSchema["required"]) == {"defect_id", "cycle", "authored_by"}
    props = fix.inputSchema["properties"]
    assert "adjacent_path_statement" not in fix.inputSchema["required"]
    assert "adjacent_path_test" not in fix.inputSchema["required"]
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
    # `authored_by` is supplied because CT-005 puts it in `required`; the two
    # adjacent-path declarations are the omission under test, and they are the
    # pair D-039 keeps out of `required` precisely so this call reaches the
    # handler.
    args = {"defect_id": "D-001", "cycle": 2, "authored_by": "lead",
            "fix_commit": "0" * 40}

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
    well-formed call invalid, and the optional properties still type-check.

    ``authored_by`` joined ``required`` with CT-005 and is supplied here for
    that reason — the two adjacent-path declarations are still deliberately OUT
    of ``required`` (D-039), which is what this test is about and what the
    second half still pins."""
    import jsonschema

    from foundry_mcp import server as foundry_server

    tools = asyncio.run(foundry_server.list_tools())
    fix = next(t for t in tools if t.name == "Foundry-Fix")

    jsonschema.validate(
        instance={
            "defect_id": "D-001",
            "cycle": 2,
            "authored_by": "teammate",
            "adjacent_path_statement": ADJACENT_STATEMENT,
            "adjacent_path_test": ADJACENT_TEST,
        },
        schema=fix.inputSchema,
    )
    # A wrongly-typed declaration is still caught by the validator — dropping
    # the fields from `required` did not drop their schema.
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(
            instance={
                "defect_id": "D-001", "cycle": 2,
                "authored_by": "teammate", "adjacent_path_test": 7,
            },
            schema=fix.inputSchema,
        )


def test_dispatch_threads_both_declarations_through(run_env):
    """The registration half: server.py's Foundry-Fix lambda must actually pass
    every argument through, or the schema would advertise fields the handler
    never sees.

    Extended to the four CT-005 / CT-011 fields for the same reason it existed
    for the two adjacent-path ones: a widened schema whose dispatch lambda was
    not widened with it advertises an obligation the handler can never observe,
    which is how `target_kind` and `defect_class` shipped dead on the filing
    door. `authored_by="lead"` with a `fix_commit` is the shape that threads the
    most fields at once."""
    project_root, fdir = run_env
    _seed_defect(fdir)

    from foundry_mcp import server as foundry_server

    monkey_root = foundry_server._project_root
    try:
        foundry_server._project_root = project_root
        result = foundry_server._DISPATCH["Foundry-Fix"]({
            "defect_id": "D-001",
            "cycle": 3,
            "authored_by": "teammate",
            "casting_id": FIXTURE_CASTING_ID,
            "prompt_hash": _published_prompt_hash(),
            "regression_test": "tests/test_auth.py::test_sweeper_does_not_evict_a_live_session",
            "adjacent_path_statement": ADJACENT_STATEMENT,
            "adjacent_path_test": ADJACENT_TEST,
        })
    finally:
        foundry_server._project_root = monkey_root

    assert result["ok"] is True, result
    assert result["adjacent_path_statement"] == ADJACENT_STATEMENT
    assert result["adjacent_path_test"] == ADJACENT_TEST
    assert result["authored_by"] == "teammate"
    assert result["regression_test"].endswith("::test_sweeper_does_not_evict_a_live_session")


# --------------------------------------------------------------------------- #
# D-088 / D-089 / D-092 — three holes in the same neighbourhood
#
# All three are the gate comparing strings where it needed to compare what the
# strings NAME, and all three were driven through server.py's real dispatch
# table rather than by importing the handler — so the pins drive it too.
#
#   D-088  the own-symbol rule compared a bare leaf to the durable `path#Symbol`
#          cite form FR-004 mandates. Never equal, so AC-013's distinctness rule
#          was inert for the 30% of this run's records that use that spelling:
#          honouring the cite policy disabled the fix gate.
#   D-089  a test INSIDE the defect's own file cleared the own-file rule on the
#          strength of its `::test_x` suffix, while a legitimate `./relative`
#          spelling of a genuinely adjacent path was refused as a dangling
#          separator. Wrong in both directions at once.
#   D-092  the statement and the reference were judged independently, so a test
#          driving NONE of the paths the statement named closed the defect —
#          which is the whole of FR-010's word "NAMED".
# --------------------------------------------------------------------------- #


def _drive_mcp(project_root: str, **args) -> dict:
    """Drive Foundry-Fix through server.py's real ``_DISPATCH`` table.

    The gate's callers reach it over MCP, never by importing the handler, and
    all three defects here were found by driving this surface. A pin that
    called the handler directly would not have seen D-088 at all: the shape
    that defeated it is what a real record carries, not what a fixture does.
    """
    from foundry_mcp import server as foundry_server

    # The same two tool-wide declarations the in-process wrapper supplies, for
    # the same reason and with the same `setdefault` discipline: every test
    # below is about the adjacent-path ladder, and a test that overrides either
    # field still wins. See the block above the wrapper.
    args.setdefault("authored_by", "teammate")
    if args["authored_by"] == "teammate":
        args.setdefault("casting_id", FIXTURE_CASTING_ID)
        args.setdefault("prompt_hash", _published_prompt_hash())

    previous = foundry_server._project_root
    try:
        foundry_server._project_root = project_root
        return foundry_server._DISPATCH["Foundry-Fix"](args)
    finally:
        foundry_server._project_root = previous


# --------------------------------------------------------------------------- #
# D-088 — the durable cite form of a symbol must be judged like the bare form
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "spelling, expected",
    [
        ("refresh_session", "refresh_session"),
        ("src/auth/session.py#refresh_session", "refresh_session"),
        ("src/auth/session.py:42#refresh_session", "refresh_session"),
        ("src/auth/session.py#refresh_session:42", "refresh_session"),
        # The real shape, lifted from this run's own defects.json.
        (
            "plugins/foundry/scripts/measure-run.py#_reconcile_cycle_count",
            "_reconcile_cycle_count",
        ),
        # An extension the cite grammar does not whitelist still splits at the
        # separator the protocol reserves for exactly this.
        ("some/dir/file.xyz#Thing", "Thing"),
        ("", ""),
    ],
)
def test_a_symbol_reduces_to_one_name_however_it_was_spelled(spelling, expected):
    """D-088's root cause as a unit. The parse is shared with the cite grammar
    (``citation.iter_symbol_cites``) rather than re-typed, so ``path#Symbol``
    means one thing in this repo and cannot drift between the two readers."""
    assert _fix_gate._own_symbol_name(spelling) == expected


def test_the_cite_form_of_a_symbol_does_not_disable_the_own_symbol_rule(run_env):
    """D-088 driven as the matched pair that found it: same defect, same test
    reference, ONLY the shape of the ``symbol`` field differs.

    Before this, the bare spelling was REFUSED and the path-qualified one was
    ACCEPTED — so a teammate closed a defect citing a test for the very symbol
    the defect was found on, which is precisely what AC-013 forbids, and did it
    by writing the cite form FR-004 and the four stream agent files instruct.
    The two requirements actively fought; the gate lost.
    """
    project_root, fdir = run_env
    statement = (
        "login_handler and the nightly sweeper both reach the store, and the "
        "sweeper runs concurrently with eviction."
    )
    own_symbol_test = "tests/test_sweeper.py::test_evict_stale"

    for symbol, file_field in (
        ("evict_stale", "src/auth/sweeper.py"),
        ("src/auth/sweeper.py#evict_stale", ""),
        ("src/auth/sweeper.py:88#evict_stale", "src/auth/sweeper.py"),
    ):
        _seed_defect(fdir, symbol=symbol, file=file_field)
        result = _drive_mcp(
            project_root,
            defect_id="D-001",
            cycle=1,
            adjacent_path_statement=statement,
            adjacent_path_test=own_symbol_test,
        )
        assert result.get("ok") is not True, (symbol, result)
        assert result["missing_fields"] == ["adjacent_path_test"], (symbol, result)
        assert "AC-013" in result["error"], (symbol, result)

        record = json.loads((fdir / "defects.json").read_text(encoding="utf-8"))
        assert record["defects"][0]["status"] == "open", symbol


# --------------------------------------------------------------------------- #
# D-089 — the own-file rule reads the path a reference NAMES, both directions
# --------------------------------------------------------------------------- #


def test_a_test_inside_the_defects_own_file_drives_no_adjacent_path(run_env):
    """D-089. The own-file rule compared the WHOLE reference to the WHOLE path,
    so the bare file was refused and the same file with a test singled out
    inside it walked past on the strength of its suffix — closing the defect
    with no adjacent path having been tested at all."""
    project_root, fdir = run_env
    _seed_defect(fdir, symbol="refresh_session", file="src/auth/session.py")

    result = _drive_mcp(
        project_root,
        defect_id="D-001",
        cycle=1,
        adjacent_path_statement=ADJACENT_STATEMENT,
        adjacent_path_test="src/auth/session.py::test_login_handler_refreshes",
    )

    assert result.get("ok") is not True, result
    assert result["missing_fields"] == ["adjacent_path_test"]
    assert "AC-013" in result["error"]

    data = json.loads((fdir / "defects.json").read_text(encoding="utf-8"))
    assert data["defects"][0]["status"] == "open"


def test_a_relative_spelling_of_an_adjacent_path_is_accepted(run_env):
    """D-089's other direction, and the reason the fix normalises BEFORE the
    shape ladder rather than after it.

    ``./adjacent/file.py::test_x`` was refused for "a separator that delimits
    nothing" — identically to a reference that really did dangle — so the
    adjacent-path answer could not be written in the relative form a teammate
    naturally types. Fixing only the own-file half would have left this
    standing and the adjacent-path case broken.
    """
    project_root, fdir = run_env
    _seed_defect(fdir, symbol="refresh_session", file="src/auth/session.py")

    result = _drive_mcp(
        project_root,
        defect_id="D-001",
        cycle=1,
        adjacent_path_statement=ADJACENT_STATEMENT,
        adjacent_path_test="./tests/test_sweeper.py::test_sweeper_evicts_stale",
    )

    assert result["ok"] is True, result


def test_a_relative_spelling_of_the_defects_own_file_is_still_refused(run_env):
    """Normalisation must not become a bypass: the same relative spelling of
    the defect's OWN file is refused, and refused for the right reason — the
    path it names, not the separator it carries."""
    project_root, fdir = run_env
    _seed_defect(fdir, symbol="refresh_session", file="src/auth/session.py")

    result = _drive_mcp(
        project_root,
        defect_id="D-001",
        cycle=1,
        adjacent_path_statement=ADJACENT_STATEMENT,
        adjacent_path_test="./src/auth/session.py::test_login_handler_refreshes",
    )

    assert result.get("ok") is not True, result
    assert result["missing_fields"] == ["adjacent_path_test"]
    assert "delimits nothing" not in result["error"], result


def test_a_statement_whose_only_named_path_is_the_defects_own_is_refused(run_env):
    """D-089's statement half. ``_statement_problem`` caught a statement that
    IS the path verbatim and never one that names it inside a sentence, so a
    self-referential declaration cleared the ladder on word count alone."""
    project_root, fdir = run_env
    _seed_defect(fdir, symbol="refresh_session", file="src/auth/session.py")

    result = _drive_mcp(
        project_root,
        defect_id="D-001",
        cycle=1,
        adjacent_path_statement=(
            "The write in src/auth/session.py is what the fix touches, and "
            "that is the whole radius."
        ),
        adjacent_path_test=STATEMENT_LADDER_TEST,
    )

    assert result.get("ok") is not True, result
    assert result["missing_fields"] == ["adjacent_path_statement"]
    assert "only path it names" in result["error"], result


def test_naming_the_defects_own_file_beside_another_path_is_accepted(run_env):
    """The subset test's whole point, and the D-085 lesson applied in advance:
    an adjacent caller may perfectly well live in the defect's own file, so the
    rule fires only when EVERY path the statement names is the defect's own. A
    search for the own path anywhere in the sentence would refuse this."""
    project_root, fdir = run_env
    _seed_defect(fdir, symbol="refresh_session", file="src/auth/session.py")

    result = _drive_mcp(
        project_root,
        defect_id="D-001",
        cycle=1,
        adjacent_path_statement=(
            "The write in src/auth/session.py is mirrored by tools/audit.py, "
            "which runs concurrently."
        ),
        adjacent_path_test="tests/test_audit.py::test_audit_mirror_ordering",
    )

    assert result["ok"] is True, result


def test_the_own_path_rule_landed_as_a_path_rule_not_a_widened_phrase(run_env):
    """D-085's over-correction, guarded against in advance.

    D-089 could have been "fixed" by widening the lexical ``same``/negation
    patterns to read phrases like "the same file as the defect" — which is
    exactly the move that produced D-085, and the standing instruction is not
    to make it. This derives the claim from the compiled patterns: no member of
    the whole-statement tuple may claim the self-referential statement above.
    The refusal comes from the path rule or it comes from nowhere.
    """
    self_referential = (
        "The write in src/auth/session.py is what the fix touches, and that "
        "is the whole radius."
    )
    for pattern in _fix_gate._STATEMENT_NON_ANSWERS:
        assert not pattern.search(self_referential), pattern.pattern
    assert not _fix_gate._unbounded_denial(self_referential)
    # ...and with no own path to compare against, the statement is fine.
    assert _fix_gate._statement_problem(self_referential, "", "") is None
    # It is the path rule, and only the path rule, that refuses it.
    assert "only path it names" in _fix_gate._statement_problem(
        self_referential, "refresh_session", "src/auth/session.py"
    )


# --------------------------------------------------------------------------- #
# D-092 — the reference must drive a path the STATEMENT named
# --------------------------------------------------------------------------- #


# The exact pair driven through _DISPATCH at HEAD dc225f8 and ACCEPTED. The
# statement names two adjacent paths; the referenced test is a billing rounding
# test that drives neither, and has no relationship to the defect at all.
UNLINKED_STATEMENT = "login_handler also calls this and the sweeper runs concurrently."
UNLINKED_TEST = "tests/test_billing.py::test_invoice_totals_round_half_up"


def test_a_reference_driving_none_of_the_statements_paths_is_refused(run_env):
    """D-092 as it was driven. FR-010's "NAMED" is load-bearing: A-017 defines
    the statement as naming the adjacent paths, so the two declarations are a
    matched pair and the reference must drive one of the paths the statement
    named. The gate ran them as two independent checks and never related
    them."""
    project_root, fdir = run_env
    _seed_defect(fdir, symbol="refresh_session", file="src/auth/session.py")

    result = _drive_mcp(
        project_root,
        defect_id="D-001",
        cycle=1,
        adjacent_path_statement=UNLINKED_STATEMENT,
        adjacent_path_test=UNLINKED_TEST,
    )

    assert result.get("ok") is not True, result
    assert result["missing_fields"] == ["adjacent_path_test"]
    assert "FR-010" in result["error"]
    # The refusal shows the caller both token sets, so the remedy is readable
    # rather than guessable.
    assert "login" in result["error"] and "invoice" in result["error"], result

    data = json.loads((fdir / "defects.json").read_text(encoding="utf-8"))
    assert data["defects"][0]["status"] == "open"


def test_the_same_reference_shape_naming_a_declared_path_is_accepted(run_env):
    """The differential control for the test above: same defect, same statement,
    same test FILE, same locator shape — only the test's NAME changes, from one
    that drives none of the declared paths to one that drives login_handler.
    The verdict must turn on the linkage and on nothing else."""
    project_root, fdir = run_env
    _seed_defect(fdir, symbol="refresh_session", file="src/auth/session.py")

    result = _drive_mcp(
        project_root,
        defect_id="D-001",
        cycle=1,
        adjacent_path_statement=UNLINKED_STATEMENT,
        adjacent_path_test="tests/test_billing.py::test_login_handler_still_refreshes",
    )

    assert result["ok"] is True, result


def test_one_shared_token_is_enough_to_link_the_two_declarations(run_env):
    """The rule is deliberately the weakest one that closes D-092: ANY overlap
    accepts. Refusing a real answer is this gate's characteristic failure — it
    is what D-076 and D-085 both were — and it fires in GRIND where the
    teammate has no way around it, so the threshold is one token, asserted
    here as exactly one."""
    project_root, fdir = run_env
    _seed_defect(fdir, symbol="refresh_session", file="src/auth/session.py")

    statement = "The nightly reaper thread also touches the token store."
    ref = "tests/test_reaper.py::test_reaper_ordering"
    assert _fix_gate._content_tokens(ref) & _fix_gate._content_tokens(statement) == {"reaper"}

    result = _drive_mcp(
        project_root,
        defect_id="D-001",
        cycle=1,
        adjacent_path_statement=statement,
        adjacent_path_test=ref,
    )

    assert result["ok"] is True, result


def test_linkage_is_withheld_when_the_statement_failed_its_own_ladder(run_env):
    """The rung is reached only for a statement that cleared its own ladder.

    Telling a caller "your reference drives none of the paths your statement
    named" about a statement that named none sends them after the wrong
    problem — and the caller is already being told about the statement in the
    same refusal.
    """
    project_root, fdir = run_env
    _seed_defect(fdir, symbol="refresh_session", file="src/auth/session.py")

    result = _drive_mcp(
        project_root,
        defect_id="D-001",
        cycle=1,
        adjacent_path_statement="none",
        adjacent_path_test=UNLINKED_TEST,
    )

    assert result.get("ok") is not True, result
    assert result["missing_fields"] == ["adjacent_path_statement"], result
    assert "FR-010" not in result["error"], result


def test_a_reference_naming_a_test_file_is_not_judged_for_linkage(run_env):
    """The rule's stated ceiling, pinned so it is a decision rather than a gap.

    A reference that names a whole test FILE names a container, and a
    container's name is not a claim about which path is driven — judging it
    would assert something the reference never said. That is the same ceiling
    the rules above keep ("these reject non-answers; they do not certify"), and
    it is what keeps `tests/test_auth.py` acceptable against any statement.
    """
    project_root, fdir = run_env
    _seed_defect(fdir, symbol="refresh_session", file="src/auth/session.py")

    assert not _fix_gate._ref_singles_out_a_leaf("tests/test_billing.py")
    assert _fix_gate._ref_singles_out_a_leaf(UNLINKED_TEST)

    result = _drive_mcp(
        project_root,
        defect_id="D-001",
        cycle=1,
        adjacent_path_statement=UNLINKED_STATEMENT,
        adjacent_path_test="tests/test_billing.py",
    )

    assert result["ok"] is True, result


# --------------------------------------------------------------------------- #
# The binding itself — the property all three defects are instances of
# --------------------------------------------------------------------------- #


# One defect location, spelled every way a real record spells it. The `file`
# field alone, the cite form in `symbol` with `file` left empty, a line hint on
# either, a relative prefix. Under raw equality these are five different
# defects; they are one.
_SAME_LOCATION_SPELLINGS = [
    {"file": "src/auth/session.py", "symbol": "refresh_session"},
    {"file": "./src/auth/session.py", "symbol": "refresh_session"},
    {"file": "src/auth/session.py:42", "symbol": "refresh_session"},
    {"file": "", "symbol": "src/auth/session.py#refresh_session"},
    {"file": "src/auth/session.py", "symbol": "src/auth/session.py:42#refresh_session"},
]


@pytest.mark.parametrize("location", _SAME_LOCATION_SPELLINGS)
def test_every_spelling_of_the_defects_location_reaches_the_same_verdict(run_env, location):
    """D-088, D-089 and D-092 are one property, asserted once.

    Each was a comparison site left on raw string equality while the protocol
    wrote something richer into the field being compared — and this run's
    repeated failure is never one bad rule, it is one rule fixed in a single
    copy. So the invariant is stated over the whole gate rather than per rule:
    however the defect's own location is spelled, the same three references get
    the same three verdicts. A future edit that normalises at one site and not
    another fails here, on the spelling it forgot.
    """
    project_root, fdir = run_env
    _seed_defect(fdir, **location)

    own_file_ref = "src/auth/session.py::test_login_handler_refreshes"
    own_symbol_ref = "tests/test_session.py::test_refresh_session"

    refused_own_file = _drive_mcp(
        project_root,
        defect_id="D-001",
        cycle=1,
        adjacent_path_statement=ADJACENT_STATEMENT,
        adjacent_path_test=own_file_ref,
    )
    assert refused_own_file.get("ok") is not True, (location, refused_own_file)
    assert refused_own_file["missing_fields"] == ["adjacent_path_test"]

    refused_own_symbol = _drive_mcp(
        project_root,
        defect_id="D-001",
        cycle=1,
        adjacent_path_statement=ADJACENT_STATEMENT,
        adjacent_path_test=own_symbol_ref,
    )
    assert refused_own_symbol.get("ok") is not True, (location, refused_own_symbol)
    assert refused_own_symbol["missing_fields"] == ["adjacent_path_test"]

    accepted = _drive_mcp(
        project_root,
        defect_id="D-001",
        cycle=1,
        adjacent_path_statement=ADJACENT_STATEMENT,
        adjacent_path_test=ADJACENT_TEST,
    )
    assert accepted["ok"] is True, (location, accepted)


# --------------------------------------------------------------------------- #
# US-003 / US-005 — FIX CEREMONY IS PROPORTIONAL TO THE EVIDENCE TIER
#
# Every fix used to pay the full LIVE declaration, so closing a scan-derivation
# gap nobody had reproduced cost the same apparatus as fixing a driven,
# reachable failure — an adjacent-path statement, an adjacent-path test that
# drives one of the paths it names, and the reading to find both. On a one-line
# change that is thirty minutes of apparatus buying nothing, because there is no
# reachable failure whose blast radius it is protecting.
#
# The tier is an EVIDENCE grade and never a severity (GI-001). Every tier is a
# defect, every tier gets fixed, and a LIVE fix keeps every declaration it ever
# had — unweakened, which several tests below assert directly.
#
# "Every tier", not "both": `schemas/vocab.py#DEFECT_TIERS` names LIVE, LATENT
# and HARDENING. Counting the vocabulary in prose is how a section comment goes
# stale the day a member is added, so this states the rule without the count —
# the wording every one of the shared filing bullets now carries. HARDENING's
# own lane is pinned in `tests/test_defect_tier.py`; what this module measures
# is the ceremony each tier's fix owes.
# --------------------------------------------------------------------------- #

LATENT_REPRODUCTION = "AST sweep of every call site finds 0 reachable paths"


def _seed_tiered(fdir: Path, tier: str, defect_id: str = "D-001", **extra) -> None:
    """Seed one defect at a named tier, otherwise identical to `_seed_defect`."""
    extra.setdefault("tier", tier)
    if tier == "LATENT":
        extra.setdefault("reproduction_attempted", LATENT_REPRODUCTION)
    extra.setdefault("class", "FALSE_DOCUMENTED_CONTRACT")
    _seed_defect(fdir, defect_id, **extra)


def _regression_test_file(project_root: str) -> str:
    """Write a real test file and return the locator that names a test in it.

    Written rather than asserted about, because the LATENT lane's strongest rung
    only fires when the path RESOLVES: `path::test` where the file exists is
    checked properly by reading it. A fixture that named a path off in space
    would exercise only the lexical half.
    """
    tests_dir = Path(project_root) / "tests"
    tests_dir.mkdir(parents=True, exist_ok=True)
    (tests_dir / "test_backlog.py").write_text(
        "def test_absent_section_is_named():\n"
        "    assert True\n"
        "\n"
        "\nclass TestBacklog:\n"
        "    def test_method_form(self):\n"
        "        assert True\n",
        encoding="utf-8",
    )
    return "tests/test_backlog.py::test_absent_section_is_named"


# --------------------------------------------------------------------------- #
# ST-003 / CT-004 / FR-008 / AC-011 / AC-012 / OT-007 — the LATENT lane
# --------------------------------------------------------------------------- #


def test_a_latent_fix_closes_on_a_regression_test_locator_alone(run_env):
    """OT-007 verbatim: 'Foundry-Fix on a LATENT defect succeeds with a
    regression_test locator alone and stores it.'

    AC-011's first clause: 'Foundry-Fix on a LATENT defect with authored_by, a
    regression_test locator and no adjacent-path fields succeeds.' No statement,
    no adjacent-path test, no failing-then-passing prose.
    """
    project_root, fdir = run_env
    _set_cycle(fdir, 5)
    _seed_tiered(fdir, "LATENT")
    locator = _regression_test_file(project_root)

    result = foundry_mark_defect_fixed(
        defect_id="D-001", cycle=5, regression_test=locator,
        project_root=project_root,
    )

    assert result["ok"] is True, result
    assert result["tier"] == "LATENT"
    assert result["regression_test"] == locator

    record = json.loads((fdir / "defects.json").read_text(encoding="utf-8"))["defects"][0]
    assert record["status"] == "fixed"
    assert record["regression_test"] == locator
    assert record["authored_by"] == "teammate"
    assert record["fixed_in_cycle"] == 5


def test_a_latent_fix_demands_no_adjacent_path_declaration(run_env):
    """ST-003 verbatim: 'adjacent-path statement and test are not demanded for
    LATENT.'

    Stated as the ABSENCE of a refusal on the exact fields the LIVE lane
    requires, because "not demanded" is a claim about what does NOT happen, and
    a lane that quietly accepted the fields while still requiring them would
    pass a success-only test.
    """
    project_root, fdir = run_env
    _set_cycle(fdir, 1)
    _seed_tiered(fdir, "LATENT")
    locator = _regression_test_file(project_root)

    result = foundry_mark_defect_fixed(
        defect_id="D-001", cycle=1, regression_test=locator,
        project_root=project_root,
    )

    assert result["ok"] is True, result
    assert "adjacent_path_statement" not in result.get("missing_fields", [])
    assert result["adjacent_path_statement"] == ""
    assert result["adjacent_path_test"] == ""


def test_a_latent_fix_with_no_regression_test_is_refused_naming_the_field(run_env):
    """AC-012 verbatim (first half): 'Foundry-Fix on a LATENT defect with no
    regression_test... is refused.'

    Proportional is not free. The lane trades the adjacent-path pair for ONE
    named test, and a fix that names none has paid nothing.
    """
    project_root, fdir = run_env
    _set_cycle(fdir, 1)
    _seed_tiered(fdir, "LATENT")

    result = foundry_mark_defect_fixed(
        defect_id="D-001", cycle=1, project_root=project_root
    )

    assert result.get("ok") is not True
    assert result["missing_fields"] == ["regression_test"]
    assert "regression_test" in result["error"]
    assert result["tier"] == "LATENT"
    # A refusal mutates nothing.
    record = json.loads((fdir / "defects.json").read_text(encoding="utf-8"))["defects"][0]
    assert record["status"] == "open"


@pytest.mark.parametrize(
    "locator, why",
    [
        ("n/a", "prose, not a locator"),
        ("tests/test_backlog.py", "a file, not a test inside one"),
        ("::test_thing", "an empty path"),
        ("tests/test_backlog.py::", "an empty test name"),
        ("tests/test_backlog.py::todo", "a placeholder name"),
        ("tests/test_backlog.py::test_that_is_not_in_the_file", "names no such test"),
    ],
)
def test_a_locator_that_does_not_name_a_test_is_refused(run_env, locator, why):
    """AC-012's second half: 'or one that does not name a test, is refused.'

    CT-004 bounds the gate precisely — "refusal only when the locator is absent
    or does not name a test" — so each row here is one way of not naming one. The
    last row is the strongest and only fires because the path RESOLVES: the file
    is read and the name is not in it.
    """
    project_root, fdir = run_env
    _set_cycle(fdir, 1)
    _seed_tiered(fdir, "LATENT")
    _regression_test_file(project_root)

    result = foundry_mark_defect_fixed(
        defect_id="D-001", cycle=1, regression_test=locator,
        project_root=project_root,
    )

    assert result.get("ok") is not True, (locator, why, result)
    assert result["missing_fields"] == ["regression_test"], (locator, why)


def test_a_locator_naming_no_file_in_the_tree_is_refused(run_env):
    """ST-003 verbatim: 'the locator names a REAL test' — D-131.

    This test asserted the OPPOSITE and is what pinned the hole. It read "the
    filesystem rung is a STRENGTHENING that fires only when it can be proven,
    never a precondition", on `_test_ref_problem`'s ruling that a server
    running against a TARGET repo frequently cannot resolve its tests. The
    premise was true and the consequence was that the one field the LATENT lane
    rests on validated nothing whenever the caller rooted its path differently
    from this process. Driven on a LATENT defect:
    `regression_test='tests/test_ghost.py::test_ghost'` returned ok True and
    closed the defect with no such file anywhere, and on the guild repo
    `tests/test_fix_gate.py::test_totally_absent_name` — a REAL file that
    defines no such test — was accepted too, because
    `Path(project_root)/'tests/test_fix_gate.py'` does not resolve when
    project_root is the repository root and the citation is mcp-server-relative.

    LEAD RULING, GRIND cycle 7: the locator must resolve to a real test
    whichever root it is relative to. `_resolve_test_path` removes the premise
    — it tries the path as given, under project_root, and by unique suffix
    beneath it — so "nowhere" now means the file is not in the tree, which is
    falsifiable from where the caller stands: commit the test.
    """
    project_root, fdir = run_env
    _set_cycle(fdir, 1)
    _seed_tiered(fdir, "LATENT")

    result = foundry_mark_defect_fixed(
        defect_id="D-001", cycle=1,
        regression_test="packages/api/tests/test_sweeper.py::test_evicts_stale",
        project_root=project_root,
    )

    assert result.get("ok") is not True, result
    assert "regression_test" in result.get("missing_fields", []), result
    assert "does not resolve" in json.dumps(result), result


def test_a_locator_rooted_at_a_subdirectory_resolves_by_suffix(run_env):
    """D-131's other half: the teammates' own spelling has to resolve.

    A citation is written relative to the package the test lives in
    (`tests/test_fix_gate.py`), while `project_root` is the repository root
    several levels up. Resolving only `project_root / path` made every such
    locator unprovable, which is what let the ghost through. The suffix search
    resolves it, and the file's real contents are then read — so a real test
    passes and a name the file does not define is refused, on the same locator
    spelling.
    """
    project_root, fdir = run_env
    _set_cycle(fdir, 1)
    _seed_tiered(fdir, "LATENT")
    nested = Path(project_root) / "packages" / "api" / "tests"
    nested.mkdir(parents=True, exist_ok=True)
    (nested / "test_sweeper.py").write_text(
        "def test_evicts_stale():\n    assert True\n", encoding="utf-8"
    )

    assert _fix_gate._regression_test_problem(
        "tests/test_sweeper.py::test_evicts_stale", project_root
    ) is None
    assert _fix_gate._regression_test_problem(
        "tests/test_sweeper.py::test_never_written", project_root
    ) is not None

    result = foundry_mark_defect_fixed(
        defect_id="D-001", cycle=1,
        regression_test="tests/test_sweeper.py::test_evicts_stale",
        project_root=project_root,
    )
    assert result["ok"] is True, result


def test_a_parametrised_node_id_is_not_refused_as_naming_no_test(run_env):
    """CT-004 — D-134. A refusal only when the locator 'is absent or does not
    name a test'; `tests/test_repro.py::test_param_gap[1]` names one.

    Driven: a file defining `def test_param_gap(a)` under `@parametrize` was
    refused with "exists but defines no 'test_param_gap[1]'" — the diagnostic
    asserting a `def` is missing that is present, on a node id pytest collects
    and runs. The rung compared the bracketed id against def names instead of
    stripping the parameter suffix.
    """
    project_root, fdir = run_env
    _set_cycle(fdir, 1)
    _seed_tiered(fdir, "LATENT")
    tests_dir = Path(project_root) / "tests"
    tests_dir.mkdir(parents=True, exist_ok=True)
    (tests_dir / "test_repro.py").write_text(
        "import pytest\n"
        "\n"
        "@pytest.mark.parametrize('a', [1, 2])\n"
        "def test_param_gap(a):\n"
        "    assert a\n",
        encoding="utf-8",
    )

    assert _fix_gate._regression_test_problem(
        "tests/test_repro.py::test_param_gap[1]", project_root
    ) is None, "a parametrised node id names the def it is drawn from"
    assert _fix_gate._regression_test_problem(
        "tests/test_repro.py::test_param_gap[a-b-c]", project_root
    ) is None, "the whole bracketed param set is stripped, not one token of it"
    # And the suffix is not a way to smuggle a name the file lacks past the rung.
    assert _fix_gate._regression_test_problem(
        "tests/test_repro.py::test_absent[1]", project_root
    ) is not None

    result = foundry_mark_defect_fixed(
        defect_id="D-001", cycle=1,
        regression_test="tests/test_repro.py::test_param_gap[1]",
        project_root=project_root,
    )
    assert result["ok"] is True, result


def test_a_test_method_on_a_test_class_resolves(run_env):
    """pytest collects both shapes, so both must resolve. A locator naming a
    method on a `Test` class is a real test locator and refusing it would send a
    teammate to rewrite a perfectly good test."""
    project_root, fdir = run_env
    _set_cycle(fdir, 1)
    _seed_tiered(fdir, "LATENT")
    _regression_test_file(project_root)

    result = foundry_mark_defect_fixed(
        defect_id="D-001", cycle=1,
        regression_test="tests/test_backlog.py::TestBacklog::test_method_form",
        project_root=project_root,
    )

    assert result["ok"] is True, result


def test_no_failing_then_passing_statement_is_demanded(run_env):
    """FR-041 / AC-012: 'it demands no failing-then-passing statement, which the
    teammate protocol requires in the completion report instead.'

    Asserted against the SIGNATURE, because the claim is that no such input
    EXISTS. A server that accepted the statement as a string would be accepting a
    claim it cannot check; PROVE drives the test to confirm it instead, which is
    a check.
    """
    import inspect

    params = inspect.signature(_mark_defect_fixed).parameters
    assert not [p for p in params if "failing" in p or "passing" in p]
    assert not [p for p in params if "before" in p or "after" in p]


def test_an_untiered_defect_takes_the_stricter_live_lane(run_env):
    """FR-051's fail-safe direction, at the fix door.

    A record with no tier is one nobody classified, and unknown blocks like LIVE
    at every gate. The same reasoning applies here: it gets the STRICTER
    ceremony, never the looser one. Reading it as LATENT would let a fix close on
    one locator because an older archive never carried the field.
    """
    project_root, fdir = run_env
    _set_cycle(fdir, 1)
    _seed_defect(fdir)  # no tier at all — a pre-change record
    locator = _regression_test_file(project_root)

    result = foundry_mark_defect_fixed(
        defect_id="D-001", cycle=1, regression_test=locator,
        project_root=project_root,
    )

    assert result.get("ok") is not True
    assert result["missing_fields"] == [
        "adjacent_path_statement", "adjacent_path_test",
    ]


# --------------------------------------------------------------------------- #
# FR-015 / AC-023 — the LIVE lane is UNWEAKENED
# --------------------------------------------------------------------------- #


def test_a_live_defect_still_demands_both_adjacent_path_declarations(run_env):
    """AC-011's middle clause: 'the same call on a LIVE defect is refused naming
    adjacent_path_statement and adjacent_path_test.'

    The same call — a regression_test locator and nothing else — that closes a
    LATENT defect. Nothing about the LIVE lane moved.
    """
    project_root, fdir = run_env
    _set_cycle(fdir, 1)
    _seed_tiered(fdir, "LIVE")
    locator = _regression_test_file(project_root)

    result = foundry_mark_defect_fixed(
        defect_id="D-001", cycle=1, regression_test=locator,
        project_root=project_root,
    )

    assert result.get("ok") is not True
    assert result["missing_fields"] == [
        "adjacent_path_statement", "adjacent_path_test",
    ]
    assert result["tier"] == "LIVE"


def test_a_lane_live_fix_by_the_lead_still_needs_the_full_declaration(run_env):
    """AC-023 verbatim: 'A lane LIVE fix still requires adjacent_path_statement
    and adjacent_path_test.'

    FR-015 says it too. The lead lane bounds how MUCH a lead may change without
    dispatching a teammate; it does not buy a discount on declaring the blast
    radius of a reachable failure.
    """
    project_root, fdir = run_env
    _set_cycle(fdir, 1)
    _seed_tiered(fdir, "LIVE")

    result = foundry_mark_defect_fixed(
        defect_id="D-001", cycle=1, authored_by="lead",
        fix_commit="0" * 40, project_root=project_root,
    )

    assert result.get("ok") is not True
    assert result["missing_fields"] == [
        "adjacent_path_statement", "adjacent_path_test",
    ]


def test_a_live_fix_that_also_names_a_regression_test_keeps_it(run_env):
    """Never demanded on the LIVE lane, always kept when offered: a fix that has
    said something true should have the record carry it."""
    project_root, fdir = run_env
    _set_cycle(fdir, 1)
    _seed_tiered(fdir, "LIVE")
    locator = _regression_test_file(project_root)

    result = foundry_mark_defect_fixed(
        defect_id="D-001", cycle=1,
        adjacent_path_statement=ADJACENT_STATEMENT,
        adjacent_path_test=ADJACENT_TEST,
        regression_test=locator,
        project_root=project_root,
    )

    assert result["ok"] is True, result
    record = json.loads((fdir / "defects.json").read_text(encoding="utf-8"))["defects"][0]
    assert record["regression_test"] == locator
    assert record["adjacent_path_statement"] == ADJACENT_STATEMENT


# --------------------------------------------------------------------------- #
# GI-003 / ST-004 / CT-005 / CT-006 / FR-014 / FR-016 / FR-046 / FR-053 —
# THE BOUNDED LEAD LANE
#
# A cycle whose open defects are all small should close without a teammate
# spawn. What makes that safe rather than a licence is that the bound is
# MEASURED from the commit rather than claimed in prose, and that the server
# itself writes the audit record — "a lead fix recorded only as free prose in a
# hand-written handoff" is GI-003's named violation, and a record the author
# writes about their own exemption is not an audit trail.
# --------------------------------------------------------------------------- #


def _git(root: Path, *args: str) -> str:
    import subprocess

    return subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, text=True, check=True,
    ).stdout.strip()


def _repo(project_root: str) -> Path:
    root = Path(project_root)
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "foundry@example.invalid")
    _git(root, "config", "user.name", "foundry")
    # The real repo's own rule (`.gitignore:16` is `/foundry-archive/`), and it
    # is load-bearing here: without it the run's own state.json and defects.json
    # land in the fix commit and the lane counts THEM as the non-test files it
    # is measuring. A lane that counted a run's bookkeeping would refuse every
    # honest lead fix.
    (root / ".gitignore").write_text("/foundry-archive/\n", encoding="utf-8")
    (root / "src").mkdir(parents=True, exist_ok=True)
    (root / "src" / "sweeper.py").write_text(
        "".join(f"line_{n} = {n}\n" for n in range(1, 60)), encoding="utf-8"
    )
    (root / "tests").mkdir(parents=True, exist_ok=True)
    (root / "tests" / "test_sweeper.py").write_text(
        "def test_evicts_stale():\n    assert True\n", encoding="utf-8"
    )
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "baseline")
    return root


def _commit_changing(project_root: str, changes: dict[str, int]) -> str:
    """Commit `changes` (path -> ADDED-PLUS-DELETED lines) and return its SHA.

    A REAL commit, because the lane is measured with `git show --numstat` and a
    monkeypatched subprocess would pin the call rather than the measurement.

    Lines are APPENDED rather than rewritten, and the distinction is the whole
    reason this docstring exists: numstat counts additions and deletions
    separately, so rewriting N lines is 2N against a bound the requirement
    states as "added+deleted". Appending N is exactly N, which makes every
    number below read as the number FR-016 is talking about.
    """
    root = Path(project_root)
    for rel, added in changes.items():
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        existing = target.read_text(encoding="utf-8") if target.exists() else ""
        target.write_text(
            existing + "".join(f"added_{n} = {n}\n" for n in range(added)),
            encoding="utf-8",
        )
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "lead fix")
    return _git(root, "rev-parse", "HEAD")


# --------------------------------------------------------------------------- #
# CT-005 / AC-020 — authored_by, and what each author owes
# --------------------------------------------------------------------------- #


def test_a_fix_without_authored_by_is_refused_naming_the_field(run_env):
    """AC-020 verbatim (first clause): 'Foundry-Fix without authored_by is
    refused naming the field.'

    GI-003 makes it required on EVERY fix, not only lead ones: a lead fix
    recorded as free prose in a hand-written handoff is a fix nothing can
    measure or count, and this field is what makes the difference visible at all.
    """
    project_root, fdir = run_env
    _set_cycle(fdir, 1)
    _seed_tiered(fdir, "LIVE")

    result = _mark_defect_fixed(
        defect_id="D-001", cycle=1,
        adjacent_path_statement=ADJACENT_STATEMENT,
        adjacent_path_test=ADJACENT_TEST,
        project_root=project_root,
    )

    assert result.get("ok") is not True
    assert result["missing_fields"] == ["authored_by"]
    assert "authored_by" in result["error"]


def test_an_unknown_author_is_refused_against_the_closed_vocabulary(run_env):
    """Who wrote a fix is a closed vocabulary. 'ai', 'me' and 'foundry' are not
    members, and coercing any of them onto one would make the report's lead_fix
    section a fiction."""
    project_root, fdir = run_env
    _set_cycle(fdir, 1)
    _seed_tiered(fdir, "LIVE")

    result = _mark_defect_fixed(
        defect_id="D-001", cycle=1, authored_by="the-lead",
        adjacent_path_statement=ADJACENT_STATEMENT,
        adjacent_path_test=ADJACENT_TEST,
        project_root=project_root,
    )

    assert result.get("ok") is not True
    assert result["missing_fields"] == ["authored_by"]
    assert set(FIX_AUTHORS).issubset(set(result["error"]) | {"lead", "teammate"})
    assert "lead" in result["error"] and "teammate" in result["error"]


def test_a_lead_fix_with_no_test_is_refused(run_env):
    """AC-020's middle clause: 'with authored_by=lead and no test it is
    refused.'

    Whichever lane it is in. A LATENT lead fix owes its regression_test and a
    LIVE one owes its adjacent-path test; neither is waived by the author being
    the lead.
    """
    project_root, fdir = run_env
    _repo(project_root)
    _set_cycle(fdir, 1)
    _seed_tiered(fdir, "LATENT")
    commit = _commit_changing(project_root, {"src/sweeper.py": 3})

    result = _mark_defect_fixed(
        defect_id="D-001", cycle=1, authored_by="lead", fix_commit=commit,
        project_root=project_root,
    )

    assert result.get("ok") is not True
    assert result["missing_fields"] == ["regression_test"]


@pytest.mark.parametrize("tier", ["LIVE", "LATENT"])
def test_a_lead_fix_without_a_commit_is_refused_whatever_the_tier(run_env, tier):
    """AC-020's last clause: 'with authored_by=lead and no fix_commit it is
    refused naming fix_commit, WHATEVER THE TIER.'

    FR-053 spells out why the LATENT case is not an oversight: 'authored_by=lead
    always needs fix_commit so the lead_fix handoff carries the commit; the
    numstat measurement runs only when the defect is LIVE.' The commit is the
    audit record's content, not only the measurement's input — so it is required
    even where nothing measures it.
    """
    project_root, fdir = run_env
    _set_cycle(fdir, 1)
    _seed_tiered(fdir, tier)
    locator = _regression_test_file(project_root)

    result = _mark_defect_fixed(
        defect_id="D-001", cycle=1, authored_by="lead",
        regression_test=locator,
        adjacent_path_statement=ADJACENT_STATEMENT,
        adjacent_path_test=ADJACENT_TEST,
        project_root=project_root,
    )

    assert result.get("ok") is not True, (tier, result)
    assert result["missing_fields"] == ["fix_commit"], tier
    assert "fix_commit" in result["error"], tier


# --------------------------------------------------------------------------- #
# CT-006 / FR-016 / FR-046 / AC-021 / OT-009 — the measurement
# --------------------------------------------------------------------------- #


def test_one_non_test_file_at_the_line_limit_succeeds(run_env):
    """OT-009 verbatim (first half): 'Foundry-Fix with authored_by lead on a LIVE
    defect and a fix_commit of one non-test file with 20 lines succeeds.'

    AC-021 adds the detail that makes the lane usable: 'one non-test file with 20
    lines PLUS A TEST FILE succeeds.' FR-016 excludes test files from the count,
    because a bounded fix that could not carry its own regression test would be a
    lane nobody could use honestly.
    """
    project_root, fdir = run_env
    _repo(project_root)
    _set_cycle(fdir, 2)
    _seed_tiered(fdir, "LIVE")
    # Twenty added-plus-deleted lines in the one non-test file, and forty more
    # in a test file that FR-016 excludes from the count entirely.
    commit = _commit_changing(
        project_root, {"src/sweeper.py": LEAD_LANE_MAX_LINES,
                       "tests/test_sweeper.py": 40}
    )

    result = _mark_defect_fixed(
        defect_id="D-001", cycle=2, authored_by="lead", fix_commit=commit,
        adjacent_path_statement=ADJACENT_STATEMENT,
        adjacent_path_test=ADJACENT_TEST,
        project_root=project_root,
    )

    assert result["ok"] is True, result
    assert result["authored_by"] == "lead"
    assert result["fix_commit"] == commit


def test_twenty_one_lines_is_refused_naming_the_count(run_env):
    """OT-009's second half: 'with 21 lines it is refused naming the count.'

    AC-021 requires the refusal to NAME the count, because "too big" tells a lead
    nothing about which half to shrink — and the honest next move (dispatch it to
    a teammate) depends on knowing by how much.
    """
    project_root, fdir = run_env
    _repo(project_root)
    _set_cycle(fdir, 2)
    _seed_tiered(fdir, "LIVE")
    commit = _commit_changing(
        project_root, {"src/sweeper.py": LEAD_LANE_MAX_LINES + 1}
    )

    result = _mark_defect_fixed(
        defect_id="D-001", cycle=2, authored_by="lead", fix_commit=commit,
        adjacent_path_statement=ADJACENT_STATEMENT,
        adjacent_path_test=ADJACENT_TEST,
        project_root=project_root,
    )

    assert result.get("ok") is not True
    assert "21" in result["error"], result
    assert str(LEAD_LANE_MAX_LINES) in result["error"], result
    record = json.loads((fdir / "defects.json").read_text(encoding="utf-8"))["defects"][0]
    assert record["status"] == "open"


def test_two_non_test_files_are_refused_naming_the_file_count(run_env):
    """AC-021's other half: 'a fix_commit that touches two non-test files... is
    refused naming the count.'

    The file bound is the one that matters most: a change spanning two modules is
    a change whose blast radius nobody has traced, however few lines it is.
    """
    project_root, fdir = run_env
    _repo(project_root)
    _set_cycle(fdir, 2)
    _seed_tiered(fdir, "LIVE")
    commit = _commit_changing(
        project_root, {"src/sweeper.py": 2, "src/other.py": 2}
    )

    result = _mark_defect_fixed(
        defect_id="D-001", cycle=2, authored_by="lead", fix_commit=commit,
        adjacent_path_statement=ADJACENT_STATEMENT,
        adjacent_path_test=ADJACENT_TEST,
        project_root=project_root,
    )

    assert result.get("ok") is not True
    assert "2 non-test file" in result["error"], result
    assert "src/sweeper.py" in result["error"], result


def test_a_latent_lead_fix_of_any_size_succeeds_and_is_recorded_unmeasured(run_env):
    """AC-021's last clause: 'on a LATENT defect a lead fix of any size succeeds
    with its regression_test and its required fix_commit is recorded unmeasured.'

    FR-046 / CT-006: the numstat measurement runs ONLY on LIVE. A LATENT gap can
    be a large, mechanical, entirely safe change, and there is no reachable
    failure whose blast radius the bound is protecting — so the bound would be
    ceremony rather than a control.
    """
    project_root, fdir = run_env
    _repo(project_root)
    _set_cycle(fdir, 2)
    _seed_tiered(fdir, "LATENT")
    locator = _regression_test_file(project_root)
    # Far outside the lane: four files, hundreds of lines.
    commit = _commit_changing(project_root, {
        "src/sweeper.py": 50, "src/a.py": 50, "src/b.py": 50, "src/c.py": 50,
    })

    result = _mark_defect_fixed(
        defect_id="D-001", cycle=2, authored_by="lead", fix_commit=commit,
        regression_test=locator, project_root=project_root,
    )

    assert result["ok"] is True, result
    record = json.loads((fdir / "defects.json").read_text(encoding="utf-8"))["defects"][0]
    assert record["fix_commit"] == commit, "recorded..."
    assert record["authored_by"] == "lead"


def test_a_commit_git_cannot_read_is_refused_rather_than_waved_through(run_env):
    """A measurement that could not be taken is not a measurement that passed.

    The alternative shape — treating an unreadable commit as "nothing to
    measure" — would let an unbounded lead fix through by naming a SHA that does
    not exist, which is the easiest possible way past the lane.
    """
    project_root, fdir = run_env
    _repo(project_root)
    _set_cycle(fdir, 2)
    _seed_tiered(fdir, "LIVE")

    result = _mark_defect_fixed(
        defect_id="D-001", cycle=2, authored_by="lead", fix_commit="f" * 40,
        adjacent_path_statement=ADJACENT_STATEMENT,
        adjacent_path_test=ADJACENT_TEST,
        project_root=project_root,
    )

    assert result.get("ok") is not True
    assert "measured" in result["error"], result


# --------------------------------------------------------------------------- #
# GI-003 / AC-022 — the SERVER writes the handoff record
# --------------------------------------------------------------------------- #


def test_a_successful_lead_fix_appends_a_lead_fix_handoff_record(run_env):
    """AC-022 verbatim: 'A successful lead fix causes the server to append a
    lead_fix record to handoffs.jsonl carrying the defect id, tier, file, line
    count and test.'

    THE SERVER, not the lead. GI-003's named violation is "a lead fix recorded
    only as free prose in a hand-written handoff" — a record the author writes
    about their own exemption. Making it a side effect of the accepted call is
    what makes it impossible to forget.
    """
    project_root, fdir = run_env
    _repo(project_root)
    _set_cycle(fdir, 3)
    _seed_tiered(fdir, "LIVE")
    commit = _commit_changing(project_root, {"src/sweeper.py": 4})

    result = _mark_defect_fixed(
        defect_id="D-001", cycle=3, authored_by="lead", fix_commit=commit,
        adjacent_path_statement=ADJACENT_STATEMENT,
        adjacent_path_test=ADJACENT_TEST,
        project_root=project_root,
    )
    assert result["ok"] is True, result

    records = [
        json.loads(line)
        for line in (fdir / "handoffs.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    lead_fixes = [r for r in records if r.get("event") == HANDOFF_EVENT_LEAD_FIX]
    assert len(lead_fixes) == 1, records
    record = lead_fixes[0]
    assert record["defect_id"] == "D-001"
    assert record["tier"] == "LIVE"
    assert record["file"] == "src/sweeper.py"
    assert record["line_count"] == 4
    assert record["test"] == ADJACENT_TEST
    assert record["fix_commit"] == commit


def test_a_latent_lead_fix_records_its_file_and_line_count_unmeasured(run_env):
    """GI-003 verbatim: 'the server itself appends a `lead_fix` handoff record
    carrying the defect id, tier, file, line count and test.'

    D-046: NO TIER CARVE-OUT. GI-003, AC-022 and OT-010 all state that field
    list flat, and this record used to emit `file: None, line_count: None` for
    every LATENT lead fix — so AC-022's "the generated report lists it"
    rendered blank columns and a reader could not tell a deliberately
    unmeasured fix from a measurement nobody took.

    What IS LIVE-only is the LANE, and this fixture proves both halves at once:
    the commit is 40 added lines, twice `LEAD_LANE_MAX_LINES`, so accepting it
    is FR-046 / CT-006 / ST-004's "a LATENT lead fix is not measured" —
    measured meaning JUDGED — while the record still carries the file and the
    count the requirement names.
    """
    project_root, fdir = run_env
    _repo(project_root)
    _set_cycle(fdir, 3)
    _seed_tiered(fdir, "LATENT")
    locator = _regression_test_file(project_root)
    commit = _commit_changing(project_root, {"src/sweeper.py": 40})

    result = _mark_defect_fixed(
        defect_id="D-001", cycle=3, authored_by="lead", fix_commit=commit,
        regression_test=locator, project_root=project_root,
    )
    assert result["ok"] is True, result

    record = [
        json.loads(line)
        for line in (fdir / "handoffs.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip() and json.loads(line).get("event") == HANDOFF_EVENT_LEAD_FIX
    ][0]
    assert record["tier"] == "LATENT"
    assert record["file"] == "src/sweeper.py"
    assert record["line_count"] == 40
    assert record["test"] == locator
    assert record["fix_commit"] == commit


def test_the_latent_lane_records_the_measurement_without_applying_it(run_env):
    """FR-046 verbatim: 'Foundry-Fix with authored_by=lead measures fix_commit
    only when the defect is LIVE.'

    The adjacent path to D-046's fix, driven rather than argued: populating the
    record from `_numstat_measurement` must not drag the LANE along with it. A
    commit that would be refused outright on the LIVE lane — two non-test files,
    41 added lines — is accepted here, and the record carries the count that
    would have refused it.
    """
    project_root, fdir = run_env
    _repo(project_root)
    _set_cycle(fdir, 3)
    _seed_tiered(fdir, "LATENT")
    locator = _regression_test_file(project_root)
    commit = _commit_changing(
        project_root, {"src/sweeper.py": 21, "src/other.py": 20}
    )

    result = _mark_defect_fixed(
        defect_id="D-001", cycle=3, authored_by="lead", fix_commit=commit,
        regression_test=locator, project_root=project_root,
    )
    assert result["ok"] is True, result

    record = [
        json.loads(line)
        for line in (fdir / "handoffs.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip() and json.loads(line).get("event") == HANDOFF_EVENT_LEAD_FIX
    ][0]
    # D-073: the count is over ALL non-test files, so `file` is None — it names
    # a single path or it names nothing. This assertion used to read
    # `record["file"] in ("src/sweeper.py", "src/other.py")`, which CONCEDED
    # that the record could not say which file it named while `line_count` was
    # the total for both: a reader of REPORT.md's row concluded 41 lines changed
    # in src/sweeper.py, where 21 did.
    assert record["line_count"] == 41
    assert record["file"] is None
    assert [row["path"] for row in record["files"]] == [
        "src/other.py", "src/sweeper.py",
    ]
    assert {row["path"]: row["lines"] for row in record["files"]} == {
        "src/sweeper.py": 21, "src/other.py": 20,
    }


def test_a_teammate_fix_writes_no_lead_fix_record(run_env):
    """The record is the audit trail for the EXEMPTION, so it must not fire on
    the ordinary path. A lead_fix record per teammate fix would bury the handful
    that matter under the hundreds that do not."""
    project_root, fdir = run_env
    _set_cycle(fdir, 3)
    _seed_tiered(fdir, "LIVE")

    foundry_mark_defect_fixed(
        defect_id="D-001", cycle=3,
        adjacent_path_statement=ADJACENT_STATEMENT,
        adjacent_path_test=ADJACENT_TEST,
        project_root=project_root,
    )

    handoffs = fdir / "handoffs.jsonl"
    records = (
        [json.loads(line) for line in handoffs.read_text(encoding="utf-8").splitlines()
         if line.strip()]
        if handoffs.exists() else []
    )
    assert [r for r in records if r.get("event") == HANDOFF_EVENT_LEAD_FIX] == []


def test_a_refused_lead_fix_writes_no_handoff_record(run_env):
    """The record follows the LEDGER, not the attempt.

    Written after the transaction commits, because a handoff naming a fix the
    transaction then rolled back is the same audit gap pointing the other way —
    a report listing a lead fix that never happened.
    """
    project_root, fdir = run_env
    _repo(project_root)
    _set_cycle(fdir, 3)
    _seed_tiered(fdir, "LIVE")
    commit = _commit_changing(project_root, {"src/sweeper.py": 40})

    result = _mark_defect_fixed(
        defect_id="D-001", cycle=3, authored_by="lead", fix_commit=commit,
        adjacent_path_statement=ADJACENT_STATEMENT,
        adjacent_path_test=ADJACENT_TEST,
        project_root=project_root,
    )

    assert result.get("ok") is not True
    assert not (fdir / "handoffs.jsonl").exists() or not [
        line for line in (fdir / "handoffs.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip() and json.loads(line).get("event") == HANDOFF_EVENT_LEAD_FIX
    ]


# --------------------------------------------------------------------------- #
# CT-011 / AC-030 / CT-004 — the reported prompt hash
# --------------------------------------------------------------------------- #


def test_a_teammate_fix_reporting_a_wrong_hash_is_refused(run_env):
    """AC-030's second half: 'Foundry-Accept-Casting and Foundry-Fix refuse when
    the hash the teammate reports differs from the file's.'

    Pointer dispatch hands a teammate a PATH and a HASH instead of the text, and
    only an agent that actually read the file can state the value back. That is
    advice until a gate consumes it, and BOTH consuming gates call the same
    `check_reported_prompt_hash` — a rung that existed at one door and not the
    other would let an unread prompt through whichever door the lead walked.
    """
    project_root, fdir = run_env
    _set_cycle(fdir, 1)
    _seed_tiered(fdir, "LIVE")

    result = _mark_defect_fixed(
        defect_id="D-001", cycle=1, authored_by="teammate",
        casting_id=FIXTURE_CASTING_ID, prompt_hash="sha256:0000000000000000",
        adjacent_path_statement=ADJACENT_STATEMENT,
        adjacent_path_test=ADJACENT_TEST,
        project_root=project_root,
    )

    assert result.get("ok") is not True
    assert result["error"] == "stale_prompt_hash"
    assert result["expected_hash"] == _published_prompt_hash()


def test_a_latent_fix_is_still_refused_on_a_hash_mismatch(run_env):
    """AC-011's last clause: 'a LATENT fix is still refused when authored_by is
    missing or the reported prompt hash mismatches.'

    CT-004 is explicit that the lane narrows only the TIER-SPECIFIC ceremony:
    "the tool-wide refusals (authored_by missing, reported prompt hash mismatch)
    still apply". Proportional ceremony is not a second, quieter door.
    """
    project_root, fdir = run_env
    _set_cycle(fdir, 1)
    _seed_tiered(fdir, "LATENT")
    locator = _regression_test_file(project_root)

    result = _mark_defect_fixed(
        defect_id="D-001", cycle=1, authored_by="teammate",
        casting_id=FIXTURE_CASTING_ID, prompt_hash="sha256:deadbeefdeadbeef",
        regression_test=locator, project_root=project_root,
    )

    assert result.get("ok") is not True
    assert result["error"] == "stale_prompt_hash"


def test_a_latent_fix_is_still_refused_when_authored_by_is_missing(run_env):
    """The other half of the same clause, driven at the real handler."""
    project_root, fdir = run_env
    _set_cycle(fdir, 1)
    _seed_tiered(fdir, "LATENT")
    locator = _regression_test_file(project_root)

    result = _mark_defect_fixed(
        defect_id="D-001", cycle=1, regression_test=locator,
        project_root=project_root,
    )

    assert result.get("ok") is not True
    assert result["missing_fields"] == ["authored_by"]


def test_a_teammate_fix_naming_no_casting_is_refused_naming_both_fields(run_env):
    """Both fields in ONE refusal, as this door does everywhere else.

    The hash cannot be checked without knowing which prompt it is claimed to be
    the hash OF, so the two are a pair — and a caller who supplied neither should
    be told about both rather than sent back twice.
    """
    project_root, fdir = run_env
    _set_cycle(fdir, 1)
    _seed_tiered(fdir, "LIVE")

    result = _mark_defect_fixed(
        defect_id="D-001", cycle=1, authored_by="teammate",
        adjacent_path_statement=ADJACENT_STATEMENT,
        adjacent_path_test=ADJACENT_TEST,
        project_root=project_root,
    )

    assert result.get("ok") is not True
    assert result["missing_fields"] == ["prompt_hash", "casting_id"]


def test_a_lead_fix_needs_no_prompt_hash(run_env):
    """The rung is about a TEAMMATE reading a dispatched prompt.

    A lead was never handed one, so demanding a hash from it would be ceremony
    with nothing behind it — and would make the lead lane unreachable, which is
    the requirement negating itself.
    """
    project_root, fdir = run_env
    _repo(project_root)
    _set_cycle(fdir, 1)
    _seed_tiered(fdir, "LIVE")
    commit = _commit_changing(project_root, {"src/sweeper.py": 3})

    result = _mark_defect_fixed(
        defect_id="D-001", cycle=1, authored_by="lead", fix_commit=commit,
        adjacent_path_statement=ADJACENT_STATEMENT,
        adjacent_path_test=ADJACENT_TEST,
        project_root=project_root,
    )

    assert result["ok"] is True, result


# --------------------------------------------------------------------------- #
# D-020 — the lane's zero-file refusal said "too big" about a commit too small
#
# LEAD RULING (SPEC_AMBIGUOUS, recorded in the run's state.json): the lane
# requires EXACTLY one non-test file for a LIVE lead fix. FR-014 states it as
# "LIVE if one file and <= 20 lines"; FR-016's "more than one" names one
# refusal condition, not the only one. So the `!=` comparison is correct, a
# test-only LIVE fix is outside the lane, and what had to change is what the
# refusal SAYS about which direction it missed by.
# --------------------------------------------------------------------------- #


def test_a_test_only_commit_is_refused_without_being_called_too_big(run_env):
    """D-020: 'a test-only fix commit is refused with "changes 0 non-test
    file(s) — the lead lane is exactly 1. Dispatch this to a GRIND teammate
    instead." ... the refusal says "too big" when it is too small.'

    The refusal still refuses, per the lead ruling. What it must not do is hand
    the lead the remedy for the opposite fault: "dispatch this to a teammate,
    it is too large" is unactionable advice about an empty commit, and a lead
    that follows it burns a dispatch discovering the commit was never the
    problem.
    """
    project_root, fdir = run_env
    _repo(project_root)
    _set_cycle(fdir, 2)
    _seed_tiered(fdir, "LIVE")
    commit = _commit_changing(project_root, {"tests/test_sweeper.py": 12})

    result = _mark_defect_fixed(
        defect_id="D-001", cycle=2, authored_by="lead", fix_commit=commit,
        adjacent_path_statement=ADJACENT_STATEMENT,
        adjacent_path_test=ADJACENT_TEST,
        project_root=project_root,
    )

    assert result.get("ok") is not True, result
    error = result["error"]
    assert "0 non-test file(s)" in error, error
    # The rule, stated as a requirement rather than as a size complaint.
    assert f"exactly {LEAD_LANE_MAX_FILES}" in error, error
    assert "only test files" in error, error
    # And NOT the too-big remedy: nothing here can be made smaller.
    assert "the lead lane is at most" not in error, error


def test_the_lane_still_requires_exactly_one_non_test_file(run_env):
    """The lead ruling's other half, pinned so a later reader does not "fix"
    the `!=` into a `>` by reading FR-016 alone.

    Two non-test files is refused, and the refusal names the count.
    """
    project_root, fdir = run_env
    _repo(project_root)
    _set_cycle(fdir, 2)
    _seed_tiered(fdir, "LIVE")
    commit = _commit_changing(
        project_root, {"src/sweeper.py": 3, "src/other.py": 3}
    )

    result = _mark_defect_fixed(
        defect_id="D-001", cycle=2, authored_by="lead", fix_commit=commit,
        adjacent_path_statement=ADJACENT_STATEMENT,
        adjacent_path_test=ADJACENT_TEST,
        project_root=project_root,
    )

    assert result.get("ok") is not True, result
    assert "2 non-test file(s)" in result["error"], result
    assert f"exactly {LEAD_LANE_MAX_FILES}" in result["error"], result


def test_the_comparison_is_equality_not_an_upper_bound(run_env):
    """The ruling is a property of the SOURCE, and the two readings differ only
    on a commit shape a passing test-suite might never contain. Asserted here
    so the rule survives a refactor that never runs the zero-file case."""
    import inspect

    source = inspect.getsource(_fix_gate._lead_lane_problem)
    assert "len(files) != LEAD_LANE_MAX_FILES" in source, (
        "the lane is EXACTLY one non-test file (lead ruling on FR-014 vs "
        "FR-016); an upper-bound comparison would admit a test-only fix"
    )


# --------------------------------------------------------------------------- #
# D-065 — the LATENT lane's locator names a TEST, or it names nothing
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "locator,because",
    [
        (
            "src/auth/session.py::refresh_session",
            "the defect's OWN production symbol in its OWN file — the code the "
            "fix changed, offered as the test that holds it",
        ),
        (
            "src/auth/session.py::helper",
            "some other symbol in the defect's own production file",
        ),
        (
            "src/nonexistent.py::whatever",
            "a production path that resolves to nothing at all",
        ),
        ("a::b", "two letters either side of a separator"),
        ("the fix::works now", "prose with a separator in it"),
    ],
)
def test_a_latent_locator_that_names_no_test_is_refused(run_env, locator, because):
    """AC-012 verbatim: 'Foundry-Fix on a LATENT defect with no regression_test,
    OR ONE THAT DOES NOT NAME A TEST, is refused.' ST-003's guard: 'the locator
    names a real test.' CT-004's errors column: 'refusal only when the locator
    is absent or does not name a test.'

    D-065: every row here was ACCEPTED and closed the defect. The lane's only
    "is it a test" rung was `_ref_names_a_test_function`, whose body is
    `_TEST_REF_EXTENSION.search(leaf) is None` — "the leaf has no file
    extension" — so nothing asked whether the target was a test. Worse, the
    filesystem rung then resolved `src/auth/session.py`, found `def
    refresh_session`, and CONFIRMED the broken production function as the
    regression test for its own fix.

    The parametrised test that existed had six rows, every one either a
    `tests/test_*` path or a structurally broken locator, so the omitted rung
    was never exercised. These five are the shapes that walked through it.
    """
    project_root, fdir = run_env
    _seed_tiered(fdir, "LATENT", symbol="refresh_session",
                 file="src/auth/session.py")
    (Path(project_root) / "src" / "auth").mkdir(parents=True, exist_ok=True)
    (Path(project_root) / "src" / "auth" / "session.py").write_text(
        "def refresh_session():\n    return None\n"
        "\n\ndef helper():\n    return None\n",
        encoding="utf-8",
    )

    result = foundry_mark_defect_fixed(
        defect_id="D-001", cycle=1,
        regression_test=locator, project_root=project_root,
    )

    assert result.get("ok") is not True, (because, result)
    assert result["missing_fields"] == ["regression_test"], result
    ledger = json.loads((fdir / "defects.json").read_text(encoding="utf-8"))
    assert ledger["defects"][0]["status"] == "open", (
        "a refused fix closes nothing"
    )


def test_the_latent_locator_ladder_applies_the_rungs_its_sibling_has(run_env):
    """D-065 as the property: the two ladders judge different fields and must
    not disagree about what "names a test" means.

    `_test_ref_problem` (the LIVE lane) applies a whitespace/prose rung and
    `_TEST_REF_NAMES_A_TEST`; `_regression_test_problem` called neither, which
    is the whole defect. Asserted on the behaviour of both, so a future edit
    that weakens one ladder is caught by the other's expectations.
    """
    project_root, fdir = run_env
    _seed_tiered(fdir, "LATENT", symbol="refresh_session",
                 file="src/auth/session.py")

    # A path that is not a test file, however test-shaped the leaf is.
    assert _fix_gate._regression_test_problem(
        "src/auth/session.py::test_refresh", project_root
    ) is not None
    # A test file whose leaf names no test.
    assert _fix_gate._regression_test_problem(
        "tests/test_auth.py::refresh_session", project_root
    ) is not None
    # Both ladders reject prose outright.
    assert _fix_gate._regression_test_problem("the fix::works now", project_root)
    assert _fix_gate._test_ref_problem("the fix works now", "", "")
    # ...and a real locator clears it. D-131: "real" now includes the file
    # being in the tree, so the fixture writes it rather than naming a path off
    # in space — which is the point of that rung.
    tests_dir = Path(project_root) / "tests"
    tests_dir.mkdir(parents=True, exist_ok=True)
    (tests_dir / "test_auth.py").write_text(
        "def test_refresh_session_expiry():\n    assert True\n", encoding="utf-8"
    )
    assert _fix_gate._regression_test_problem(
        "tests/test_auth.py::test_refresh_session_expiry", project_root
    ) is None


# --------------------------------------------------------------------------- #
# D-075 / D-073 — the measurement parses what git actually prints
# --------------------------------------------------------------------------- #


def test_a_rename_is_measured_at_its_destination_path(run_env):
    """FR-016 / FR-034 / CT-006. D-075.

    `git show --numstat` renders a rename as ONE field carrying git's display
    compaction — `src/{f20.py => f20_renamed.py}` — and the parse took field 3
    as a path verbatim. So the lead_fix record's `file`, which GI-003 makes the
    audit field, carried a git rendering rather than a path a reader can
    resolve.
    """
    project_root, _fdir = run_env
    root = _repo(project_root)
    _git(root, "mv", "src/sweeper.py", "src/sweeper_renamed.py")
    _git(root, "commit", "-qm", "rename")
    commit = _git(root, "rev-parse", "HEAD")

    measured = _fix_gate._numstat_measurement(commit, project_root)

    assert measured["ok"] is True, measured
    assert measured["files"] == ["src/sweeper_renamed.py"]
    assert measured["per_file"][0]["renamed_from"] == "src/sweeper.py"


def test_a_rename_into_the_tests_tree_is_still_a_source_change(run_env):
    """FR-016 verbatim: 'test files excluded from the count' — and a rename
    INTO tests/ is not a test file being edited, it is a production file being
    removed. D-075: `is_test_file('{src => tests}/a.py')` answered False, so the
    entry escaped the classifier in one direction; parsing it to `tests/a.py`
    would have made it escape the LANE in the other. EITHER side non-test makes
    the entry non-test.
    """
    project_root, _fdir = run_env
    root = _repo(project_root)
    (root / "tests" / "sweeper.py").parent.mkdir(parents=True, exist_ok=True)
    _git(root, "mv", "src/sweeper.py", "tests/sweeper.py")
    _git(root, "commit", "-qm", "rename into tests")
    commit = _git(root, "rev-parse", "HEAD")

    measured = _fix_gate._numstat_measurement(commit, project_root)

    assert measured["files"] == ["tests/sweeper.py"], measured
    assert measured["per_file"][0]["renamed_from"] == "src/sweeper.py"
    # ...and the lane counts it, so the one-file budget is spent on it.
    assert _fix_gate._lead_lane_problem(commit, project_root) is None


def test_the_rename_parse_handles_both_spellings_git_emits():
    """The braced form (with the common prefix and suffix factored out) and the
    bare `old => new`. Both are DISPLAY strings; the path they name is the
    destination."""
    assert _fix_gate._numstat_rename_paths("src/{f20.py => f20_renamed.py}") == (
        "src/f20_renamed.py", "src/f20.py",
    )
    assert _fix_gate._numstat_rename_paths("{src => tests}/a.py") == (
        "tests/a.py", "src/a.py",
    )
    assert _fix_gate._numstat_rename_paths("old.py => new.py") == ("new.py", "old.py")
    assert _fix_gate._numstat_rename_paths("src/plain.py") == ("src/plain.py", "")


def test_the_lead_fix_record_never_names_one_file_beside_a_total(run_env):
    """GI-003 / AC-022 / OT-010. D-073, as the property rather than the case.

    The record is the audit trail for a fix nobody else reviewed, so `file` and
    `line_count` must describe the same thing. They did not: `file` was
    `measured['files'][0]` and `line_count` was the sum over ALL non-test files,
    so a 5-file by 100-line commit recorded `{'file': 'pkg/m0.py',
    'line_count': 500}` and REPORT.md rendered `| D-001 | LATENT | pkg/m0.py |
    500 |`. A reader concludes 500 lines changed in pkg/m0.py; 100 did.
    """
    project_root, fdir = run_env
    _repo(project_root)
    _set_cycle(fdir, 3)
    _seed_tiered(fdir, "LATENT")
    locator = _regression_test_file(project_root)
    commit = _commit_changing(
        project_root, {f"pkg/m{n}.py": 100 for n in range(5)}
    )

    result = _mark_defect_fixed(
        defect_id="D-001", cycle=3, authored_by="lead", fix_commit=commit,
        regression_test=locator, project_root=project_root,
    )
    assert result["ok"] is True, result

    record = [
        json.loads(line)
        for line in (fdir / "handoffs.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip() and json.loads(line).get("event") == HANDOFF_EVENT_LEAD_FIX
    ][0]

    assert record["line_count"] == 500
    assert record["file"] is None, (
        "five files were touched; naming one of them beside the total for all "
        "five is the field that was wrong"
    )
    assert len(record["files"]) == 5
    assert all(row["lines"] == 100 for row in record["files"]), record["files"]


# --------------------------------------------------------------------------- #
# D-066 — the tool DESCRIPTION is the surface a caller reads when it chooses
# arguments, so it describes the contract the handler actually enforces
# --------------------------------------------------------------------------- #


def test_the_advertised_fix_description_states_both_lanes():
    """GI-003 / AC-011 / FR-008. D-066.

    The description advertised the adjacent-path pair as UNCONDITIONAL and
    never mentioned LATENT, regression_test or tier: over the live MCP surface
    it contained no occurrence of any of the three. The per-property strings
    below it WERE updated correctly (`regression_test` says "REQUIRED on a
    LATENT defect"; `adjacent_path_statement` says "Not demanded on a LATENT
    defect"), so only the top-level text was stale — and it is the text an
    agent reads FIRST. US-003's whole purpose is negated at the one place a
    teammate decides what to send.
    """
    from foundry_mcp import server as foundry_server

    tools = asyncio.run(foundry_server.list_tools())
    description = next(t for t in tools if t.name == "Foundry-Fix").description

    for token in ("LATENT", "LIVE", "regression_test", "authored_by", "tier"):
        assert token in description, (
            f"{token!r} is absent from the surface a caller reads when it "
            "chooses arguments"
        )
    # The refusal is still stated outright — the half D-039 put here on purpose,
    # because the validator cannot name two missing fields at once.
    assert "REFUSED" in description


def test_a_caller_following_the_description_closes_a_latent_defect(run_env):
    """D-066 driven from the caller's side: the arguments the description tells
    a caller to send for a LATENT defect are accepted, and the arguments it used
    to tell them to send were refused.

    That is the whole cost of a stale description — not a documentation nit but
    a refused call on the first fix of every LATENT defect.
    """
    project_root, fdir = run_env
    _seed_tiered(fdir, "LATENT")
    locator = _regression_test_file(project_root)

    # What the OLD description prescribed: the adjacent-path pair, no locator.
    old_shape = foundry_mark_defect_fixed(
        defect_id="D-001", cycle=1,
        adjacent_path_statement=ADJACENT_STATEMENT,
        adjacent_path_test=ADJACENT_TEST,
        project_root=project_root,
    )
    assert old_shape.get("ok") is not True
    assert old_shape["missing_fields"] == ["regression_test"]

    # What the current one prescribes.
    new_shape = foundry_mark_defect_fixed(
        defect_id="D-001", cycle=1, regression_test=locator,
        project_root=project_root,
    )
    assert new_shape["ok"] is True, new_shape
    assert new_shape["tier"] == "LATENT"


# --------------------------------------------------------------------------- #
# D-105 / D-107 — the lane measures the commit the lead NAMED, and the refusal
# says something true about it.
#
# `_numstat_measurement` interpolated the caller's `fix_commit` straight into
# `git show --numstat --format= <value>` with no `--end-of-options` terminator
# and no rev validation. A leading-dash value is consumed by git as an OPTION,
# leaving git with no revision and defaulting to HEAD — so the lane measured a
# commit the lead never named, and the server-written handoff, handoffs.md and
# the F6 lead-fix row all asserted that measurement. That is GI-003's named
# violation verbatim: "a Foundry-Fix that accepts a LIVE lead fix without
# measuring eligibility".
#
# No test in this file pinned commit-argument validity; every lane test passed a
# real 40-character SHA, which is exactly why the hole survived.
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "bogus", ["-1", "--all", "--quiet", "--stat", "HEAD~1..HEAD", "HEAD", "main"]
)
def test_a_fix_commit_that_is_not_an_object_name_is_refused_by_name(run_env, bogus):
    """D-105 / FR-016 / GI-003: the revision is validated BEFORE git sees it.

    Confirmed at the git layer (2.50.1): `git show --numstat --format= -1`
    prints HEAD's numstat with rc=0, and `--all`, `--quiet`, `--stat` and
    `HEAD~1..HEAD` all behave the same. Every one of those made the lane measure
    something other than what it was handed.

    Refused naming `fix_commit`, because the lane is a MEASUREMENT OF ONE COMMIT
    and a range, a ref or an option is not one.
    """
    project_root, _fdir = run_env
    _repo(project_root)

    measured = _fix_gate._numstat_measurement(bogus, project_root)

    assert measured["ok"] is False, (bogus, measured)
    assert measured["field"] == "fix_commit", measured
    assert "fix_commit" in measured["error"], measured
    assert measured["files"] == [] and measured["lines"] == 0, measured


def test_a_dash_led_fix_commit_no_longer_measures_head(run_env):
    """D-105 driven end to end, exactly as filed.

    One run, defect D-001, tier LIVE, authored_by=lead. The real commit — ten
    non-test files, 500 lines — is correctly refused. `fix_commit='-1'` used to
    return ok=True and write a `lead_fix` record naming D-001, file src/tiny.py,
    line_count 3 and fix_commit '-1': a measurement of HEAD, a commit the lead
    never named, asserted in three artifacts at once.
    """
    project_root, fdir = run_env
    root = _repo(project_root)
    _set_cycle(fdir, 1)
    _seed_tiered(fdir, "LIVE")
    locator = _regression_test_file(project_root)

    # HEAD is a commit that WOULD pass the lane, which is what made the
    # substitution silent: the lead saw a success and a plausible record.
    tiny = _commit_changing(project_root, {"src/tiny.py": 3})
    assert _fix_gate._lead_lane_problem(tiny, project_root) is None

    result = _mark_defect_fixed(
        defect_id="D-001", cycle=1, authored_by="lead", fix_commit="-1",
        regression_test=locator,
        adjacent_path_statement=ADJACENT_STATEMENT,
        adjacent_path_test=ADJACENT_TEST,
        project_root=project_root,
    )

    assert result.get("ok") is not True, result
    assert "fix_commit" in str(result), result
    # No audit record was written for a measurement that never happened.
    handoffs = fdir / "handoffs.jsonl"
    rows = handoffs.read_text(encoding="utf-8").splitlines() if handoffs.exists() else []
    assert not any(HANDOFF_EVENT_LEAD_FIX in row for row in rows), rows
    record = json.loads((fdir / "defects.json").read_text(encoding="utf-8"))["defects"][0]
    assert record["status"] == "open", record
    assert not record.get("fix_commit"), record
    assert _git(root, "rev-parse", "HEAD") == tiny


def test_a_real_object_name_still_measures_and_still_passes(run_env):
    """The other direction, so D-105's guard cannot be satisfied by refusing
    everything. A real abbreviated SHA and a real full SHA both measure."""
    project_root, _fdir = run_env
    root = _repo(project_root)
    commit = _commit_changing(project_root, {"src/sweeper.py": 4})
    short = _git(root, "rev-parse", "--short", commit)

    for spelling in (commit, short):
        measured = _fix_gate._numstat_measurement(spelling, project_root)
        assert measured["ok"] is True, (spelling, measured)
        assert measured["files"] == ["src/sweeper.py"], (spelling, measured)
        assert measured["lines"] == 4, (spelling, measured)
        assert _fix_gate._lead_lane_problem(spelling, project_root) is None, spelling


def test_an_empty_commit_is_not_told_it_touched_only_test_files(run_env):
    """D-107 / AC-021: the diagnostic is conditional on the thing it diagnoses.

    The clause was asserted UNCONDITIONALLY on every zero-non-test-file refusal.
    Driven with `git commit --allow-empty` on a LIVE lead fix: "…changes 0
    non-test file(s) — the lane requires exactly 1, and this commit touches only
    test files. A LIVE defect whose fix is a test-only change is outside the
    lane" — on a commit that touches NO files at all.

    The refusal DIRECTION was right and the D-020 ruling's two mandates held
    (the real count and the rule are stated, and the too-big message is never
    reused). Only the explanation lied, on the one refusal whose wording that
    ruling explicitly regulated — so the remedy splits with the diagnosis,
    because "name the commit that carries the source change" and "this commit is
    empty" send a lead to different places.
    """
    project_root, _fdir = run_env
    root = _repo(project_root)
    _git(root, "commit", "-q", "--allow-empty", "-m", "empty")
    commit = _git(root, "rev-parse", "HEAD")

    measured = _fix_gate._numstat_measurement(commit, project_root)
    assert measured["ok"] is True and measured["files"] == [] , measured
    assert measured["test_files"] == [], measured

    problem = _fix_gate._lead_lane_problem(commit, project_root)

    assert problem is not None
    assert "changes no files at all" in problem, problem
    assert "touches only test files" not in problem, problem
    # D-020's mandates still hold: the rule is stated and the too-big message is
    # not reused.
    assert str(LEAD_LANE_MAX_FILES) in problem, problem
    assert "Dispatch this to a GRIND teammate instead" not in problem, problem


def test_a_test_only_commit_still_gets_the_test_only_diagnosis(run_env):
    """The other half of D-107: the clause is TRUE when there are test files, so
    it must still fire there. A fix that simply deleted the sentence would close
    the defect by removing the one refusal that tells a lead what is actually
    wrong with a test-only commit."""
    project_root, _fdir = run_env
    _repo(project_root)
    commit = _commit_changing(project_root, {"tests/test_sweeper.py": 5})

    measured = _fix_gate._numstat_measurement(commit, project_root)
    assert measured["files"] == [], measured
    assert measured["test_files"] == ["tests/test_sweeper.py"], measured

    problem = _fix_gate._lead_lane_problem(commit, project_root)

    assert problem is not None
    assert "touches only test files" in problem, problem
    assert "tests/test_sweeper.py" in problem, problem
    assert "changes no files at all" not in problem, problem


# --------------------------------------------------------------------------- #
# D-132 — a LATENT lead fix's fix_commit names a real object
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("bad", ["not-a-commit", "-1", "tbd", "0" * 40])
def test_a_latent_lead_fix_is_refused_when_fix_commit_names_no_object(
    run_env, bad
):
    """FR-053 verbatim: 'authored_by=lead always needs fix_commit SO THE
    lead_fix HANDOFF CARRIES THE COMMIT'. CT-005 / AC-022. D-132.

    CT-006's second clause — on a LATENT defect the required fix_commit "is
    recorded and NOT measured" — was read as "no git call at all". Driven on a
    LATENT defect with authored_by=lead and a valid regression_test locator,
    each of these values returned ok True; the record persisted fix_commit
    'tbd'; handoffs.jsonl gained {fix_commit 'tbd', file null, line_count null,
    files null}; and report.json rendered "measurement unavailable - git could
    not read the commit". `_numstat_measurement` refuses the shape by name (the
    D-105 rung) and the LATENT branch discarded the refusal at the record step.
    The same values on the LIVE lane were refused.

    What stays LIVE-only is the LANE — the file count and the line count. A
    commit git cannot resolve is not a measurement that was skipped; it is a
    field that names nothing for the audit record to carry.
    """
    project_root, fdir = run_env
    _set_cycle(fdir, 1)
    _seed_tiered(fdir, "LATENT")
    locator = _regression_test_file(project_root)

    result = foundry_mark_defect_fixed(
        defect_id="D-001", cycle=1, authored_by="lead",
        regression_test=locator, fix_commit=bad, project_root=project_root,
    )

    assert result.get("ok") is not True, (bad, result)
    assert "fix_commit" in result.get("missing_fields", []), result
    ledger = json.loads((fdir / "defects.json").read_text(encoding="utf-8"))
    assert ledger["defects"][0]["status"] == "open", "a refused fix closes nothing"
    assert not (fdir / "handoffs.jsonl").exists(), (
        "no lead_fix record for a refused fix"
    )


def test_the_latent_lead_lane_refusal_does_not_quote_a_limit_it_never_applied(
    run_env
):
    """D-132 / CT-006: a LATENT lead fix is not measured, so its refusal must
    not send the lead to shrink a commit nothing measured.

    The lane refusal's hint quotes `LEAD_LANE_MAX_FILES` and
    `LEAD_LANE_MAX_LINES`, which is the right instruction on the LIVE arm and
    the wrong one here — FR-046 is explicit that a LATENT gap can be a large,
    mechanical, entirely safe change with no reachable failure whose blast
    radius the bound is protecting.
    """
    project_root, fdir = run_env
    _set_cycle(fdir, 1)
    _seed_tiered(fdir, "LATENT")
    locator = _regression_test_file(project_root)

    result = foundry_mark_defect_fixed(
        defect_id="D-001", cycle=1, authored_by="lead",
        regression_test=locator, fix_commit="tbd", project_root=project_root,
    )

    hint = result["hint"]
    assert "not measured" in hint, hint
    from foundry_mcp.schemas.vocab import LEAD_LANE_MAX_LINES

    assert str(LEAD_LANE_MAX_LINES) not in hint, hint
    assert "GRIND teammate" not in hint, hint


def test_a_latent_lead_fix_with_a_real_commit_of_any_size_still_succeeds(
    run_env, tmp_path
):
    """AC-021 verbatim: 'on a LATENT defect a lead fix of ANY SIZE succeeds
    with its regression_test and its required fix_commit is recorded
    unmeasured.' D-132 must not have narrowed that.

    A five-file, several-hundred-line commit — far outside the LIVE lane — is
    accepted on the LATENT lane, and the handoff record carries the real
    measurement rather than nulls (D-046). Only the "is this a commit" rung was
    added; no limit was.
    """
    project_root, fdir = run_env
    _set_cycle(fdir, 1)
    _seed_tiered(fdir, "LATENT")
    locator = _regression_test_file(project_root)
    _repo(project_root)
    commit = _commit_changing(project_root, {f"pkg/m{n}.py": 60 for n in range(5)})

    result = foundry_mark_defect_fixed(
        defect_id="D-001", cycle=1, authored_by="lead",
        regression_test=locator, fix_commit=commit, project_root=project_root,
    )

    assert result["ok"] is True, result
    record = json.loads((fdir / "handoffs.jsonl").read_text(
        encoding="utf-8").strip().splitlines()[-1])
    from foundry_mcp.schemas.vocab import HANDOFF_EVENT_LEAD_FIX

    assert record["event"] == HANDOFF_EVENT_LEAD_FIX
    assert record["fix_commit"] == commit
    from foundry_mcp.schemas.vocab import LEAD_LANE_MAX_LINES

    assert record["line_count"] and record["line_count"] > LEAD_LANE_MAX_LINES


# --------------------------------------------------------------------------- #
# D-121 — the hash refusal reads the same at both doors
# --------------------------------------------------------------------------- #


def _plain_text(text: str) -> str:
    """`text` with ANSI colour removed — what a lead actually reads."""
    import re as _re

    return _re.sub(r"\x1b\[[0-9;]*m", "", text)



def test_the_hash_refusal_names_both_hashes_at_the_fix_door(run_env):
    """FR-019 / AC-030 / CT-011: the hash is the one thing the teammate states
    back and the server compares. D-121.

    spec.md's Error Handling row names BOTH doors for this refusal — "Reported
    prompt hash differs from file | Foundry-Accept-Casting, Foundry-Fix |
    refused | expected and reported hash" — and only one of them said so.
    Driven (TEST-01 OBS-032): Foundry-Fix with authored_by=teammate, casting_id
    1 and prompt_hash 'sha256:0000000000000000' refused correctly, and the
    ENTIRE rendered text was the bare token 'stale_prompt_hash'; the assertion
    that '0000000000000000' appears in it failed on 7 of 7 fix-door examples
    while all 3 accept-door examples passed.

    The shared C-8 rung returns `error`, `hint`, `expected_hash` and
    `reported_hash` — the token is the `error` and every VALUE it compared is
    in the other three keys. The handler returns that dict unchanged; the
    renderer dropped three of its four keys. A lead reading only
    'stale_prompt_hash' cannot tell a stale dispatch from a teammate that never
    read the file, which is the distinction the check exists to expose.
    """
    from foundry_mcp.tools.display import format_result

    project_root, fdir = run_env
    _set_cycle(fdir, 1)
    _seed_tiered(fdir, "LATENT")
    locator = _regression_test_file(project_root)
    (fdir / "castings").mkdir(parents=True, exist_ok=True)
    (fdir / "castings" / "casting-1-prompt.md").write_text(
        "# Casting 1\n\nthe real prompt\n", encoding="utf-8"
    )
    stale = "sha256:0000000000000000"

    result = foundry_mark_defect_fixed(
        defect_id="D-001", cycle=1, authored_by="teammate",
        regression_test=locator, casting_id=1, prompt_hash=stale,
        project_root=project_root,
    )

    assert result.get("ok") is not True, result
    assert result["error"] == "stale_prompt_hash"
    assert result["reported_hash"] == stale

    rendered = _plain_text(format_result("Foundry-Fix", result))
    assert "0000000000000000" in rendered, rendered
    assert result["expected_hash"][7:] in rendered, rendered
    assert "re-read the prompt file" in rendered, rendered


def test_both_hash_doors_render_the_same_facts(run_env):
    """D-121 as the property: ONE refusal, TWO doors, one shape.

    `check_reported_prompt_hash` is deliberately shared by
    `foundry_accept_casting` and `Foundry-Fix` — "a hash rung that exists at
    one door and not the other lets an unread prompt through whichever door the
    lead happens to walk". The same reasoning applies to its RENDERING: a
    refusal that reaches the lead as a bare token at one door and as both
    hashes at the other is the same drift, one layer up.

    Driven through both doors on identical inputs, comparing what each screen
    actually carries rather than what each handler returns.
    """
    from foundry_mcp.tools.display import format_result
    from foundry_mcp.tools.foundry_handoff import check_reported_prompt_hash

    project_root, fdir = run_env
    _set_cycle(fdir, 1)
    _seed_tiered(fdir, "LATENT")
    locator = _regression_test_file(project_root)
    (fdir / "castings").mkdir(parents=True, exist_ok=True)
    (fdir / "castings" / "casting-1-prompt.md").write_text(
        "# Casting 1\n\nthe real prompt\n", encoding="utf-8"
    )
    stale = "sha256:1111111111111111"

    shared = check_reported_prompt_hash(fdir, 1, stale)
    assert shared is not None and shared["error"] == "stale_prompt_hash"

    fix = foundry_mark_defect_fixed(
        defect_id="D-001", cycle=1, authored_by="teammate",
        regression_test=locator, casting_id=1, prompt_hash=stale,
        project_root=project_root,
    )

    fix_screen = _plain_text(format_result("Foundry-Fix", fix))
    accept_screen = _plain_text(format_result("Foundry-Accept-Casting", shared))

    for fact in (shared["expected_hash"][7:], stale[7:]):
        assert fact in fix_screen, ("fix door", fact, fix_screen)
        assert fact in accept_screen, ("accept door", fact, accept_screen)


# --------------------------------------------------------------------------- #
# STRUCTURAL — the escalated class `false-refusal-diagnostic` (D-105, D-134,
# D-145), three consecutive cycles, all three on `_regression_test_problem`.
#
# Two prior fixes closed the INSTANCE and the class came back one spelling
# over, which is what makes an instance fix the wrong deliverable here. Each
# time the locator was cut with a different pair of string operations and each
# time a real, collectible pytest node id came out mangled: D-134 stripped a
# trailing `[...]` after `split('::')[-1]` had already cut the id at a `::`
# INSIDE the brackets, and never taught the whitespace rung about the bracket.
#
# So the deliverable is a PARSER — `_split_pytest_node_id` — and the pin is the
# only claim that actually terminates the class: EVERY node id
# `pytest --collect-only` emits for this suite is an acceptable locator. That
# corpus is not a sample somebody chose; it is generated by the same tool whose
# spelling the door must accept, so a future edit that re-introduces string
# surgery fails against ids nobody had to think to write down.
# --------------------------------------------------------------------------- #


def _mcp_server_root() -> Path:
    """The directory `pytest` is rooted at for this suite."""
    return Path(__file__).resolve().parents[1]


def _collected_node_ids() -> list[str]:
    """Every node id `pytest --collect-only -q` emits for this suite.

    Collected in a SUBPROCESS rather than by reaching into the running
    session's internals: `--collect-only` is the published spelling of "what
    node ids does this suite have", and it is the exact command a teammate runs
    when they go looking for the locator to put in a Foundry-Fix. Collection
    only imports the modules; nothing is executed, so this does not recurse.

    A collection that fails or yields nothing FAILS the test. A pin that
    silently passes when its corpus is empty proves nothing, and this class has
    already survived two fixes.
    """
    import subprocess
    import sys

    root = _mcp_server_root()
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
         "--collect-only", "tests"],
        cwd=str(root), capture_output=True, text=True, timeout=300,
    )
    ids = [
        line.strip() for line in proc.stdout.splitlines()
        if line.strip().startswith("tests/") and "::" in line
    ]
    assert ids, (
        "pytest --collect-only produced no node ids — the pin has no corpus "
        f"(rc={proc.returncode})\n{proc.stdout[-2000:]}\n{proc.stderr[-2000:]}"
    )
    return ids


def _param_payload(node_id: str) -> str:
    """The bracketed parameter id of ``node_id``, or "" when it carries none."""
    opened = node_id.find("[")
    return node_id[opened:] if opened != -1 and node_id.endswith("]") else ""


#: The two shapes D-145 was driven on, quoted verbatim from
#: `pytest --collect-only`. Pinned as literals BESIDE the generated corpus so
#: the specific refusals stay pinned even where collection cannot run, and so a
#: reader sees what the class actually looked like without running anything.
_D145_DRIVEN_NODE_IDS = (
    "tests/test_fix_gate.py::test_real_test_references_across_languages_are_accepted"
    "[tests/test_auth.py::test_sweeper_evicts_stale_sessions]",
    "tests/test_fix_gate.py::test_a_latent_locator_that_names_no_test_is_refused"
    "[a::b-two letters either side of a separator]",
    "tests/test_fix_gate.py::test_a_locator_that_does_not_name_a_test_is_refused"
    "[tests/test_backlog.py::-an empty test name]",
    "tests/test_fix_gate.py::test_real_test_references_across_languages_are_accepted"
    "[auth::sweeper::tests::evicts_stale_sessions]",
)


def test_every_collected_pytest_node_id_is_an_acceptable_regression_locator():
    """CT-004 verbatim: "within the lane, refusal only when the locator is
    absent or does not name a test". ST-003: "the locator names a real test".

    A node id `pytest --collect-only` emits names a test by construction — the
    collector found it. So every one of them must clear this gate, and any
    refusal is a false one by definition. That is the whole claim, and it is
    the claim the escalated class `false-refusal-diagnostic` kept breaking.

    Driven over the LIVE corpus rather than a chosen sample: 3200-odd ids, of
    which 500 carry a separator or whitespace inside their brackets and 13
    carry a `::` there. Nobody has to remember to add the awkward ones.
    """
    root = str(_mcp_server_root())
    refused = []
    for node_id in _collected_node_ids():
        problem = _fix_gate._regression_test_problem(node_id, root)
        if problem is not None:
            refused.append(f"{node_id}\n    -> {problem}")

    assert not refused, (
        "the LATENT lane refuses node id(s) pytest collects and runs:\n"
        + "\n".join(refused[:20])
        + (f"\n... and {len(refused) - 20} more" if len(refused) > 20 else "")
    )


def test_the_node_id_corpus_contains_the_shapes_that_broke_the_door():
    """The pin's own falsifiability, on the axis that actually failed.

    A corpus of 3200 plain `path::test_name` ids would pass against the very
    string surgery D-145 filed, so the test above is only a mechanism while the
    corpus still carries the awkward shapes. This asserts they are in it: ids
    whose bracket payload contains `::`, a `/` and whitespace — the three
    characters that each cut a previous parse in a different place.

    It also drives the four verbatim ids D-145 was filed on, so the specific
    refusals stay pinned independently of what collection happens to yield.
    """
    collected = _collected_node_ids()
    payloads = [p for p in (_param_payload(n) for n in collected) if p]
    assert any("::" in p for p in payloads), "no id carries a '::' in its brackets"
    assert any("/" in p for p in payloads), "no id carries a '/' in its brackets"
    assert any(
        any(ch.isspace() for ch in p) for p in payloads
    ), "no id carries whitespace in its brackets"

    root = str(_mcp_server_root())
    for node_id in _D145_DRIVEN_NODE_IDS:
        assert node_id in collected, f"the driven id is no longer collected: {node_id}"
        assert _fix_gate._regression_test_problem(node_id, root) is None, node_id


def test_the_node_id_parser_splits_a_node_id_the_way_pytest_composes_one():
    """`_split_pytest_node_id` as a unit, on the grammar it claims to parse.

    The parse is the deliverable, so it is asserted directly rather than only
    through the door: the file path ends at the FIRST `::`, the parameter id
    runs from the FIRST `[` to the end, and the test's own name is the last
    element of the chain between them. Every case below is a shape a previous
    string-surgery parse got wrong.
    """
    from foundry_mcp.tools.orchestration.fix_gate import _split_pytest_node_id

    assert _split_pytest_node_id("tests/t.py::test_a") == ("tests/t.py", ["test_a"], "")
    # A class-scoped node id: the TEST is the last element, not the class.
    assert _split_pytest_node_id("tests/t.py::TestC::test_a") == (
        "tests/t.py", ["TestC", "test_a"], ""
    )
    # D-134's shape: the bracket is the param set, not part of the def name.
    assert _split_pytest_node_id("tests/t.py::test_a[1]") == (
        "tests/t.py", ["test_a"], "[1]"
    )
    # D-145's shape: a `::` inside the brackets ends nothing.
    assert _split_pytest_node_id("tests/t.py::test_a[x/y.py::test_b]") == (
        "tests/t.py", ["test_a"], "[x/y.py::test_b]"
    )
    # Whitespace inside the brackets is pytest's, and it is not prose.
    assert _split_pytest_node_id("tests/t.py::test_a[two words]") == (
        "tests/t.py", ["test_a"], "[two words]"
    )
    # An empty param set is still a param set.
    assert _split_pytest_node_id("tests/t.py::test_a[]") == (
        "tests/t.py", ["test_a"], "[]"
    )
    # An UNCLOSED bracket is not a param id: nothing is split off, so the door
    # judges the whole string and refuses it as the malformed locator it is.
    assert _split_pytest_node_id("tests/t.py::test_a[oops") == (
        "tests/t.py", ["test_a[oops"], ""
    )
    # No separator at all: the whole string is the path and the chain is empty,
    # which is what makes the "not a locator of the form path::test" rung fire.
    assert _split_pytest_node_id("tests/t.py") == ("tests/t.py", [], "")


# --------------------------------------------------------------------------- #
# FR-048 — the composite Locked clause, bound to its identifier (D-155).
#
# Its three halves were each exercised behaviourally by the tests above, and
# nothing named the requirement, so the spec's stated coverage target ("every
# FR, GI and OT has a named test") was unmet for exactly one id out of 172: a
# future edit that broke one half could not be traced to the requirement it
# broke. The test below drives all three halves of the one sentence together,
# which is also the only way to see that they are one sentence.
# --------------------------------------------------------------------------- #


def test_fr048_the_latent_lane_needs_a_locator_and_an_author_and_is_never_measured(
    run_env,
):
    """FR-048 verbatim: "A LATENT fix needs the locator and authored_by; it is
    still refused on a hash mismatch; lane measurement applies only to LIVE
    lead fixes."

    Three clauses of one requirement, driven in order:

      1. THE LOCATOR AND authored_by ARE WHAT IT NEEDS. A LATENT fix carrying
         both, and no adjacent-path declaration at all, succeeds.
      2. IT IS STILL REFUSED ON A HASH MISMATCH. The tool-wide rungs are not
         relaxed by the lane: the same call with a teammate author and a stale
         `prompt_hash` is refused, and the lane's own fields being perfect does
         not rescue it.
      3. LANE MEASUREMENT APPLIES ONLY TO LIVE LEAD FIXES. A LATENT lead fix
         whose `fix_commit` touches THREE non-test files with far more than
         LEAD_LANE_MAX_LINES lines succeeds anyway, and the commit is recorded
         unmeasured — the same commit on a LIVE defect is refused naming the
         count, which is what makes "only to LIVE" a real restriction rather
         than a description of an untested branch.
    """
    project_root, fdir = run_env
    _set_cycle(fdir, 1)
    locator = _regression_test_file(project_root)

    # 1 — the locator and authored_by, and nothing else.
    _seed_tiered(fdir, "LATENT", "D-001")
    accepted = foundry_mark_defect_fixed(
        defect_id="D-001", cycle=1, authored_by="teammate",
        regression_test=locator, casting_id=1,
        prompt_hash=_published_prompt_hash(), project_root=project_root,
    )
    assert accepted.get("ok") is True, accepted
    record = json.loads((fdir / "defects.json").read_text(encoding="utf-8"))
    assert record["defects"][0]["status"] == "fixed"
    assert record["defects"][0]["regression_test"] == locator

    # 2 — and it is STILL refused on a hash mismatch, lane fields intact.
    _seed_tiered(fdir, "LATENT", "D-002")
    mismatched = foundry_mark_defect_fixed(
        defect_id="D-002", cycle=1, authored_by="teammate",
        regression_test=locator, casting_id=1,
        prompt_hash="sha256:0000000000000000", project_root=project_root,
    )
    assert mismatched.get("ok") is not True, mismatched
    assert mismatched.get("error") == "stale_prompt_hash", mismatched

    # 3 — lane measurement applies only to LIVE lead fixes.
    repo = _repo(project_root)
    over_lane = _commit_changing(
        project_root,
        {"src/a.py": LEAD_LANE_MAX_LINES + 5,
         "src/b.py": LEAD_LANE_MAX_LINES + 5,
         "src/c.py": LEAD_LANE_MAX_LINES + 5},
    )
    assert repo.exists()

    _seed_tiered(fdir, "LATENT", "D-003")
    unmeasured = foundry_mark_defect_fixed(
        defect_id="D-003", cycle=1, authored_by="lead",
        regression_test=locator, fix_commit=over_lane, project_root=project_root,
    )
    assert unmeasured.get("ok") is True, unmeasured
    record = json.loads((fdir / "defects.json").read_text(encoding="utf-8"))
    latent_lead = next(d for d in record["defects"] if d["id"] == "D-003")
    assert latent_lead["fix_commit"] == over_lane
    assert latent_lead["authored_by"] == "lead"

    # The SAME commit on a LIVE defect is measured, and refused naming the count.
    _seed_tiered(fdir, "LIVE", "D-004")
    measured = foundry_mark_defect_fixed(
        defect_id="D-004", cycle=1, authored_by="lead", fix_commit=over_lane,
        adjacent_path_statement=ADJACENT_STATEMENT,
        adjacent_path_test=ADJACENT_TEST, project_root=project_root,
    )
    assert measured.get("ok") is not True, measured
    assert "3" in json.dumps(measured), measured


# --------------------------------------------------------------------------- #
# D-170 — THE lead_fix RECORD CARRIES THE TEST THE LANE MANDATED.
#
# `foundry_mark_defect_fixed` passed `test=regression_ref or test_ref`, so on
# the LIVE lane an OPTIONAL `regression_test` displaced the MANDATED
# `adjacent_path_test`. Driven at the door with authored_by=lead on a LIVE
# defect carrying the full declaration plus an extra regression locator: the
# record's `test` read the regression locator and the adjacent-path test
# appeared nowhere in the record or in the handoffs.md mirror row.
#
# GI-003 / AC-022 make this the audit trail for a fix nobody else reviewed, and
# the test is one of the five things a reader must be able to re-derive. Which
# test HOLDS the fix is a property of the LANE — AC-023 / FR-015 demand the
# adjacent-path test on LIVE and nothing else; ST-003 / CT-004 demand the
# regression test on LATENT and no adjacent-path fields at all — not of what
# the caller volunteered. Neither existing test covered the both-supplied case:
# the LIVE fixture passes only the adjacent-path fields and the LATENT one only
# the regression locator, so `regression_ref or test_ref` returned the right
# answer in both by accident of the input.
# --------------------------------------------------------------------------- #


def _lead_fix_records(fdir: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in (fdir / "handoffs.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip() and json.loads(line).get("event") == HANDOFF_EVENT_LEAD_FIX
    ]


def test_a_live_lead_fix_records_the_adjacent_path_test_not_the_optional_one(run_env):
    """AC-022 verbatim: 'a lead_fix record ... carrying the defect id, tier,
    file, line count and test'; AC-023: 'A lane LIVE fix still requires
    adjacent_path_statement and adjacent_path_test.'

    The both-supplied case, which is the one that was wrong. A LIVE lead fix
    that ALSO names a regression test has said something true, and the record
    keeps it — in its own field, beside the mandated one, never instead of it.
    """
    project_root, fdir = run_env
    _repo(project_root)
    _set_cycle(fdir, 3)
    _seed_tiered(fdir, "LIVE")
    regression = _regression_test_file(project_root)
    commit = _commit_changing(project_root, {"src/sweeper.py": 4})

    result = _mark_defect_fixed(
        defect_id="D-001", cycle=3, authored_by="lead", fix_commit=commit,
        adjacent_path_statement=ADJACENT_STATEMENT,
        adjacent_path_test=ADJACENT_TEST,
        regression_test=regression,
        project_root=project_root,
    )
    assert result["ok"] is True, result

    records = _lead_fix_records(fdir)
    assert len(records) == 1, records
    record = records[0]
    assert record["test"] == ADJACENT_TEST, (
        "the mandated adjacent-path test is the test that holds a LIVE fix"
    )
    assert record["regression_test"] == regression, record
    assert record["test"] != record["regression_test"]

    # ...and the markdown mirror a human reads carries both, so the audit trail
    # is not one field narrower than the jsonl beside it.
    mirror = (fdir / "handoffs.md").read_text(encoding="utf-8")
    assert ADJACENT_TEST in mirror, mirror
    assert regression in mirror, mirror


def test_a_live_lead_fix_with_no_regression_test_records_only_the_mandated_one(run_env):
    """The ordinary LIVE lane, unchanged: nothing optional was offered, so
    nothing optional is recorded and no field is invented to hold a blank."""
    project_root, fdir = run_env
    _repo(project_root)
    _set_cycle(fdir, 3)
    _seed_tiered(fdir, "LIVE")
    commit = _commit_changing(project_root, {"src/sweeper.py": 4})

    result = _mark_defect_fixed(
        defect_id="D-001", cycle=3, authored_by="lead", fix_commit=commit,
        adjacent_path_statement=ADJACENT_STATEMENT,
        adjacent_path_test=ADJACENT_TEST,
        project_root=project_root,
    )
    assert result["ok"] is True, result

    record = _lead_fix_records(fdir)[0]
    assert record["test"] == ADJACENT_TEST
    assert not record.get("regression_test"), record


def test_a_latent_lead_fix_records_its_regression_test_as_the_test(run_env):
    """ST-003 / CT-004: on the LATENT lane the regression test IS the test that
    holds the fix — no adjacent-path fields are demanded and none exist — so it
    is what `test` carries, and it is not also repeated into the optional
    field."""
    project_root, fdir = run_env
    _repo(project_root)
    _set_cycle(fdir, 3)
    _seed_tiered(fdir, "LATENT")
    regression = _regression_test_file(project_root)
    commit = _commit_changing(project_root, {"src/sweeper.py": 4})

    result = _mark_defect_fixed(
        defect_id="D-001", cycle=3, authored_by="lead", fix_commit=commit,
        regression_test=regression,
        project_root=project_root,
    )
    assert result["ok"] is True, result

    record = _lead_fix_records(fdir)[0]
    assert record["test"] == regression, record
    assert not record.get("regression_test"), (
        "one locator stated twice is two things that can disagree"
    )


def test_rung_twos_refusal_names_the_shapes_the_rung_actually_accepts(run_env):
    """FR-034 verbatim: the lane's test-file recogniser 'matches the repo's
    pytest discovery patterns'. The message beside it has to match too.

    Casting 1 narrowed `vocab.is_test_file` to exactly the declared
    configuration (D-222): a `PYTEST_PYTHON_FILES` glob, plus `conftest.py`.
    This rung's refusal still read "(test_*.py, *_test.py, conftest.py, or
    under a tests/ directory)", so it named two shapes it now REFUSES and a
    caller who followed it — renaming their file to `thing_test.py`, or
    dropping a test into any directory spelled `tests` — was refused a second
    time for doing what the message said.

    D-231 / THE CYCLE-27 RULING MOVED THE OTHER BOUNDARY, AND THE MESSAGE WITH
    IT. `testpaths` seeds argument-less collection and filters nothing, so
    `is_test_file` classifies on the BASENAME in any directory; the phrase kept
    a "under tests/" clause the predicate no longer enforced, which is the same
    drift arriving from the other side — a message NARROWER than the rung,
    sending a caller to move a file that was already collectable where it sat.

    The property asserted is the one that stops it drifting again: the phrase
    is DERIVED from the constants `is_test_file` reads, so it cannot disagree
    with the predicate it sits under. A literal beside a predicate is a copy
    free to drift from it.
    """
    project_root, _fdir = run_env

    refused = _fix_gate._regression_test_problem(
        "pkg/thing_test.py::test_thing", project_root
    )
    assert refused is not None
    # Every shape the message names is a shape the recogniser accepts...
    for glob in PYTEST_PYTHON_FILES:
        assert glob in refused, (glob, refused)
    assert PYTEST_CONFTEST_BASENAME in refused, refused
    # ...and every retired shape is named by NEITHER — including the directory
    # clause, which `is_test_file` stopped requiring (D-231).
    for retired in ("*_test.py", "under a tests/ directory", "tests/"):
        assert retired not in refused, (retired, refused)
    assert is_test_file("pkg/thing_test.py") is False
    # The rung accepts what the message now promises: any directory.
    assert is_test_file("test_root_case.py") is True
    assert is_test_file("verifier/deep/test_nested_case.py") is True

    # The derivation is not vacuous: every configured token in the phrase comes
    # from a constant, so a config change moves the message with the predicate.
    for token in _PYTEST_DISCOVERY_PHRASE.replace(",", " ").split():
        assert (
            token in PYTEST_PYTHON_FILES
            or token == PYTEST_CONFTEST_BASENAME
            or token in ("or", "in", "any", "directory")
        ), (token, _PYTEST_DISCOVERY_PHRASE)
    # And no configured testpath leaks back into it (D-231).
    for testpath in PYTEST_TESTPATHS:
        assert f"{testpath}/" not in _PYTEST_DISCOVERY_PHRASE, (
            testpath, _PYTEST_DISCOVERY_PHRASE
        )


# --------------------------------------------------------------------------- #
# D-238 — the lane measures the path git NAMES, not the path git PRINTS
# --------------------------------------------------------------------------- #


#: The three non-ASCII paths D-238 was driven on. Written as real characters
#: rather than escapes so the fixture creates the filename the defect is about;
#: `git show --numstat` at default `core.quotepath` renders each of them as a
#: quoted, octal-escaped display string.
_NON_ASCII_SOURCE = "src/modèle.py"
_NON_ASCII_TEST = "tests/test_café.py"


def _write_non_ascii(root: Path, relative: str, lines: int) -> None:
    """Create `relative` under `root` with `lines` lines, and stage it."""
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(f"line_{n} = {n}\n" for n in range(lines)), encoding="utf-8"
    )
    _git(root, "add", "--", relative)


def test_a_non_ascii_source_path_is_measured_as_the_path_it_names(run_env):
    """FR-016 verbatim: 'the server runs `git show --numstat` on it and refuses
    if more than one non-test file changed or added+deleted lines exceed 20
    (TEST FILES EXCLUDED FROM THE COUNT)'. FR-034 / CT-006 / ST-004 / AC-021.

    D-238. `git show --numstat` was run with `core.quotepath` at its default
    (true) and nothing unescaped what came back, so a non-ASCII path arrived as
    `"tests/test_caf\\303\\251.py"` — quotes and octal escapes included — and
    was taken verbatim. Driven in a throwaway repo at git 2.50.1: a commit
    changing src/a.py by 20 lines plus tests/test_café.py and schemas/modèle.py
    by one line each measured as files
    `['"schemas/mod\\303\\250le.py"', 'src/a.py', '"tests/test_caf\\303\\251.py"']`,
    test_files `[]`, lines 25, and the lane refused "changes 3 non-test file(s)".

    BOTH HALVES OF THE DAMAGE ARE ASSERTED HERE. The test file is CLASSIFIED as
    one — `vocab.is_test_file` cannot see a test whose derived basename ends
    `.py"`, so FR-016's exclusion did not apply to it — and the non-test path is
    spelled as a path a reader can resolve, because that same string becomes the
    `file` on the GI-003 audit record.
    """
    project_root, _fdir = run_env
    root = _repo(project_root)
    _write_non_ascii(root, _NON_ASCII_SOURCE, 4)
    _write_non_ascii(root, _NON_ASCII_TEST, 3)
    _git(root, "commit", "-qm", "non-ascii paths")
    commit = _git(root, "rev-parse", "HEAD")

    measured = _fix_gate._numstat_measurement(commit, project_root)

    assert measured["ok"] is True, measured
    # The path git NAMES, with no quotes and no octal escapes anywhere.
    assert measured["files"] == [_NON_ASCII_SOURCE], measured
    assert measured["test_files"] == [_NON_ASCII_TEST], measured
    for spelling in measured["files"] + measured["test_files"]:
        assert '"' not in spelling and "\\3" not in spelling, spelling
    # FR-016's exclusion applies, so the count is the source file's alone.
    assert measured["lines"] == 4, measured
    assert measured["per_file"][0]["path"] == _NON_ASCII_SOURCE, measured

    # ...and the lane therefore sees ONE non-test file, not three.
    assert _fix_gate._lead_lane_problem(commit, project_root) is None


def test_the_lead_fix_record_carries_a_non_ascii_path_a_reader_can_resolve(run_env):
    """GI-003 verbatim: 'the server itself appends a `lead_fix` handoff record
    carrying the defect id, tier, FILE, line count and test.' AC-022.

    D-238's second consequence, which outlives the refusal: on an ACCEPTED
    one-file fix whose source file has a non-ASCII name, the escaped display
    bytes were what `record_lead_fix_handoff` persisted as `file`, and that
    string flows on into handoffs.md, report.json and REPORT.md as a path
    nothing can resolve. A measurement that is merely refused can be retried; an
    audit trail written wrong is written wrong for the life of the run.
    """
    project_root, fdir = run_env
    root = _repo(project_root)
    _set_cycle(fdir, 3)
    _seed_tiered(fdir, "LIVE")
    _write_non_ascii(root, _NON_ASCII_SOURCE, 6)
    _git(root, "commit", "-qm", "non-ascii lead fix")
    commit = _git(root, "rev-parse", "HEAD")

    result = _mark_defect_fixed(
        defect_id="D-001", cycle=3, authored_by="lead", fix_commit=commit,
        adjacent_path_statement=ADJACENT_STATEMENT,
        adjacent_path_test=ADJACENT_TEST,
        project_root=project_root,
    )
    assert result["ok"] is True, result

    lead_fixes = [
        json.loads(line)
        for line in (fdir / "handoffs.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip() and json.loads(line).get("event") == HANDOFF_EVENT_LEAD_FIX
    ]
    assert len(lead_fixes) == 1, lead_fixes
    assert lead_fixes[0]["file"] == _NON_ASCII_SOURCE, lead_fixes[0]
    assert lead_fixes[0]["line_count"] == 6, lead_fixes[0]


def test_a_path_git_still_quotes_is_decoded_rather_than_taken_verbatim(run_env):
    """FR-034 / CT-006. D-238's residual case, and the reason a FLAG alone is
    not the whole fix.

    `core.quotepath=false` stops git escaping non-ASCII BYTES. It does not stop
    git quoting a path containing a double quote, a backslash or a control
    character — those are quoted whatever quotepath says, because the quoting is
    what keeps the numstat record on ONE line. Verified at git 2.50.1:
    `git -c core.quotepath=false show --numstat` prints `"src/we\\"ird.py"` for
    such a path. So the parse decodes what it is handed, and this is the arm
    that proves it does.
    """
    project_root, _fdir = run_env
    root = _repo(project_root)
    weird = 'src/we"ird.py'
    _write_non_ascii(root, weird, 3)
    _git(root, "commit", "-qm", "quoted path")
    commit = _git(root, "rev-parse", "HEAD")

    measured = _fix_gate._numstat_measurement(commit, project_root)

    assert measured["ok"] is True, measured
    assert measured["files"] == [weird], measured
    assert measured["lines"] == 3, measured


def test_a_non_ascii_rename_is_parsed_after_the_quoting_is_undone(run_env):
    """D-075's rename parse and D-238's decode, on the same field.

    The ORDER is load-bearing and this is what pins it. With
    `core.quotepath=false` a plain non-ASCII rename arrives in the BRACED
    display form (`tests/{a.py => b.py}`) with no quoting at all, so
    `_numstat_rename_paths` must run first and the decode must run on each side
    afterwards. Decoding the whole field first would consume the quotes of the
    BARE form (`"a" => "b"`), which is what git falls back to when either side
    needs quoting — leaving the rename parse a string it can no longer split
    correctly. Both destinations and both sources come back as real paths.
    """
    project_root, _fdir = run_env
    root = _repo(project_root)
    _write_non_ascii(root, _NON_ASCII_SOURCE, 5)
    _git(root, "commit", "-qm", "seed non-ascii")
    renamed = "src/modèle_renommé.py"
    _git(root, "mv", "--", _NON_ASCII_SOURCE, renamed)
    _git(root, "commit", "-qm", "rename non-ascii")
    commit = _git(root, "rev-parse", "HEAD")

    measured = _fix_gate._numstat_measurement(commit, project_root)

    assert measured["ok"] is True, measured
    assert measured["files"] == [renamed], measured
    assert measured["per_file"][0]["renamed_from"] == _NON_ASCII_SOURCE, measured


@pytest.mark.parametrize(
    "printed,names",
    [
        # Unquoted fields pass through untouched, which is the common case once
        # `core.quotepath=false` is passed.
        ("src/a.py", "src/a.py"),
        ("tests/test_café.py", "tests/test_café.py"),
        # The quoted form, byte by byte: one octal escape PER BYTE, so a
        # multi-byte character arrives as several and only accumulates
        # correctly if the decode collects bytes before decoding UTF-8.
        (r'"tests/test_caf\303\251.py"', "tests/test_café.py"),
        (r'"schemas/mod\303\250le.py"', "schemas/modèle.py"),
        # The named escapes, and a quote and backslash inside a path.
        (r'"src/we\"ird.py"', 'src/we"ird.py'),
        (r'"src/back\\slash.py"', "src/back\\slash.py"),
        (r'"src/new\nline.py"', "src/new\nline.py"),
        (r'"src/ta\tb.py"', "src/ta\tb.py"),
    ],
)
def test_the_git_path_decoder_undoes_exactly_what_git_does(printed, names):
    """D-238. The decoder in isolation, over every form git emits.

    A unit test beside the door tests, because the door can only reach the forms
    a filesystem will hold — a path containing a newline is legal on POSIX but
    is not something the repo fixtures above should be creating, and it is
    precisely the form quotepath cannot switch off.
    """
    assert _fix_gate._decode_git_path(printed) == names
