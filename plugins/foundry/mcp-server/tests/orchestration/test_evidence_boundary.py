"""The evidence sweep at the INSPECT and terminal boundaries.

Carved from `tests/test_orchestrator_gates.py` (fallout FR-005 / GI-026 /
AC-014 / OT-016): one test module per shipped orchestration module, landed in
the same casting as the source move so no pin is ever left pointing at a module
that no longer exists.
"""
from __future__ import annotations

import subprocess
from pathlib import Path


from foundry_mcp.tools import artifacts, foundry_state
from foundry_mcp.tools.foundry_state import current_cycle

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
    _arm_ordering_token,
    _write_manifest_with_castings,
    _write_state,
    run_env,
)

from foundry_mcp.tools.orchestration.evidence_boundary import (  # noqa: F401
    _sweep_evidence_at_boundary,
    _sweep_refusal,
    _terminal_evidence_sweep,
)

from foundry_mcp.tools.orchestration.transitions import (  # noqa: F401
    foundry_mark_phase_complete,
)

from tests.orchestration._env import (  # noqa: F401
    _router_ledger,
)




def test_the_sweep_refusals_prescribed_remedy_works(run_env):
    """D-067's driven case, end to end: the refusal names a retry, and the retry
    is accepted without a second Foundry-Next.

    `_sweep_refusal` carries a `token` parameter for no purpose other than
    naming the right transition to retry, so the remedy was engineered to be
    directly actionable and the token consumption made it not.
    """
    project_root, fdir = run_env
    _write_state(fdir, phase="F3", cycle=1)
    _write_manifest_with_castings(fdir, ["src/api/a.py"], no_ui=True)
    _router_ledger(fdir, [])
    _arm_ordering_token(fdir)

    calls = {"n": 0}

    def _sweep(fdir_, project_root_, entry, *, full):
        calls["n"] += 1
        if calls["n"] == 1:
            return {
                "ok": False, "record": {"scope": "full", "logs_reexecuted": [],
                                        "mismatches": [], "elapsed_seconds": 0.0,
                                        "pool_size": 0, "per_log": []},
                "mismatches": [{"log": "evidence/casting-3-gates.log",
                                "reason": "output mismatch"}],
                "error": "",
            }
        return {
            "ok": True, "record": {"scope": "full", "logs_reexecuted": [],
                                   "mismatches": [], "elapsed_seconds": 0.0,
                                   "pool_size": 0, "per_log": []},
            "mismatches": [], "error": "",
        }

    # fallout FR-004: rebound on every module that carries the name, because
    # after the carve `transitions.py` imported it and a local rebinding here
    # reaches none of them. Restored in the `finally` from the originals rather
    # than by reloading, which cannot put back what other modules imported.
    _real = {
        m: vars(m)["_sweep_evidence_at_boundary"] for m in ORCHESTRATION
        if "_sweep_evidence_at_boundary" in vars(m)
    }
    assert _real, "nothing carries the sweep; this drive would assert nothing"
    for _module in _real:
        setattr(_module, "_sweep_evidence_at_boundary", _sweep)
    try:
        refused = foundry_mark_phase_complete("inspect_start", project_root)
        assert refused.get("ok") is not True
        assert "casting-3-gates.log" in refused["error"]
        assert "inspect_start" in refused["hint"]
        assert current_cycle(fdir) == 1, "the counter did not move"

        # The log is repaired. Do EXACTLY what the hint said — no Foundry-Next.
        retried = foundry_mark_phase_complete("inspect_start", project_root)
    finally:
        for _module, _original in _real.items():
            setattr(_module, "_sweep_evidence_at_boundary", _original)

    assert retried["ok"] is True, retried
    assert retried["cycle"] == 2




def test_the_terminal_sweep_is_taken_once_per_head(run_env, monkeypatch):
    """D-133's cost half: the gate and the transition ask the same question.

    `_done_preconditions` is ONE evaluation with two callers by design (D-037),
    so the evidence rung has to be in it — a rung present in the transition and
    absent from the gate is the drift that helper exists to prevent. A
    whole-corpus re-execution is minutes, and Foundry-Gate('done') followed by
    Foundry-Phase('done') would pay it twice. The result is memoised on HEAD,
    so the claim stays exactly as strong ("the corpus was re-executed at THIS
    tree") while being made once.
    """
    import subprocess

    project_root, fdir = run_env
    subprocess.run(["git", "init", "-q", project_root], check=True)
    subprocess.run(["git", "-C", project_root, "config", "user.email", "t@t"],
                   check=True)
    subprocess.run(["git", "-C", project_root, "config", "user.name", "t"],
                   check=True)
    (Path(project_root) / "seed.txt").write_text("x", encoding="utf-8")
    subprocess.run(["git", "-C", project_root, "add", "seed.txt"], check=True)
    subprocess.run(["git", "-C", project_root, "commit", "-qm", "seed"], check=True)

    calls = []

    def _counting(_fdir, _pr, _entry, *, full):
        calls.append(full)
        return {"ok": True, "record": {"scope": "full", "logs_reexecuted": []},
                "mismatches": [], "error": ""}

    patch_everywhere(monkeypatch, "_sweep_evidence_at_boundary", _counting)

    first = _terminal_evidence_sweep(fdir, project_root)
    second = _terminal_evidence_sweep(fdir, project_root)

    assert first["ok"] and second["ok"]
    assert first["cached"] is False and second["cached"] is True
    assert len(calls) == 1, "the second ask re-executed the corpus"

    # ...and a commit — which is what a fix landing in F5 or F5.5 IS —
    # invalidates it, which is the case D-133 was filed on.
    (Path(project_root) / "seed.txt").write_text("y", encoding="utf-8")
    subprocess.run(["git", "-C", project_root, "add", "seed.txt"], check=True)
    subprocess.run(["git", "-C", project_root, "commit", "-qm", "fix"], check=True)

    third = _terminal_evidence_sweep(fdir, project_root)
    assert third["cached"] is False
    assert len(calls) == 2, "HEAD moved and the memo was still trusted"




# --------------------------------------------------------------------------- #
# fallout AC-035 / US-008 / CT-015 (D-148) — the refusal's REMEDY names what
# actually went wrong, and a parse failure is not a re-execution result.
# --------------------------------------------------------------------------- #


def _mismatch(log: str, token: str | None, reason: str) -> dict:
    return {"log": log, "failure_token": token, "reason": reason}


def test_a_syntax_refusal_does_not_claim_the_command_was_re_executed():
    """fallout AC-035 / US-008 / CT-015 (D-148) — the mechanism was right and
    the door's own account of it was wrong.

    CT-015: a log whose command fails to parse "is refused with token
    EVIDENCE_COMMAND_SYNTAX BEFORE execution", and the sweep's own
    `mismatches[].reason` says so in the same return value. The hint said the
    opposite — "was RE-EXECUTED in a detached worktree at HEAD and its output no
    longer matches what was committed" — and offered two remedies, neither of
    which resolves the token: re-capturing a log whose command does not parse
    reproduces the identical refusal, and no behaviour regressed because nothing
    ran.

    US-008 is "so that a syntax mistake costs a commit, not a cycle". A log
    predating the pre-commit guard, or one committed with the hook skipped,
    reaches this door — and the lead was sent to re-capture it.
    """
    sweep = {
        "error": "",
        "record": {"scope": "full"},
        "mismatches": [_mismatch(
            "evidence/casting-2-halt-door.log",
            "EVIDENCE_COMMAND_SYNTAX",
            "the command does not parse under /bin/sh -n; not executed",
        )],
    }
    refusal = _sweep_refusal(sweep, cycle=1, token="inspect_start")

    hint = refusal["hint"]
    # The false claim is gone...
    assert "re-executed in a detached worktree" not in hint, hint
    assert "behaviour it demonstrates regressed" not in hint, hint
    # ...and the remedy that actually resolves the token is present, in the
    # field a consumer rendering error+hint shows.
    assert "does not PARSE" in hint, hint
    assert "/bin/sh -n" in hint, hint
    assert "Re-capturing the log reproduces this refusal" in hint, hint
    # The retry sentence the token parameter exists for survives (D-067).
    assert "Foundry-Phase(phase='inspect_start')" in hint, hint


def test_an_output_mismatch_still_gets_the_re_execution_remedy():
    """fallout AC-035 (D-148) — the ADJACENT token, which must not regress.

    `EVIDENCE_OUTPUT_MISMATCH` IS a re-execution result, and it is the common
    one. A fix that made every refusal talk about syntax would be the same
    defect with the sign flipped, so the majority path is driven beside it.
    """
    sweep = {
        "error": "",
        "record": {"scope": "full"},
        "mismatches": [_mismatch(
            "evidence/casting-2-width-rule.log",
            "EVIDENCE_OUTPUT_MISMATCH",
            "byte mismatch after volatile redaction",
        )],
    }
    hint = _sweep_refusal(sweep, cycle=1, token="inspect_start")["hint"]

    assert "re-executed in a detached worktree" in hint, hint
    assert "does not PARSE" not in hint, hint


def test_a_mixed_sweep_names_every_remedy_it_needs():
    """fallout AC-035 (D-148) — two logs, two reasons, two remedies.

    Collapsing a mixed sweep onto one remedy is the same harm as naming the
    wrong one: the lead fixes the syntax, re-runs, and is refused again by the
    log that really did regress.
    """
    sweep = {
        "error": "",
        "record": {"scope": "full"},
        "mismatches": [
            _mismatch("a.log", "EVIDENCE_COMMAND_SYNTAX", "does not parse"),
            _mismatch("b.log", "EVIDENCE_OUTPUT_MISMATCH", "byte mismatch"),
        ],
    }
    hint = _sweep_refusal(sweep, cycle=1, token="temper")

    assert "does not PARSE" in hint["hint"], hint
    assert "re-executed in a detached worktree" in hint["hint"], hint
    # Both logs are still named in the error, which is CT-007's own clause.
    assert "a.log" in hint["error"] and "b.log" in hint["error"], hint


def test_a_mismatch_with_no_failure_token_keeps_the_general_remedy():
    """fallout AC-035 (D-148) — the tolerant default.

    A pre-change record, or a sweep shim in a sibling test, carries no
    `failure_token` at all. That is not a token this table knows, and the honest
    answer for an unrecognised one is the sentence the door has always given
    rather than silence or a guess.
    """
    sweep = {
        "error": "",
        "record": {"scope": "full"},
        "mismatches": [{"log": "c.log", "reason": "output mismatch"}],
    }
    hint = _sweep_refusal(sweep, cycle=1, token="inspect_start")["hint"]
    assert "re-executed in a detached worktree" in hint, hint


def test_every_evidence_failure_token_has_a_remedy_or_the_general_one():
    """fallout AC-035 / CT-015 (D-148) — the table is pinned to the vocabulary.

    `KNOWN_EVIDENCE_FAILURE_TOKENS` is a closed set whose own comment says "any
    new token = code-edit forced". This is the half of that forcing which lives
    at the refusal: every member either has its own remedy here or is a genuine
    re-execution result the general sentence fits, and a member that is NEITHER
    — a new pre-execution refusal, say — must be classified deliberately rather
    than inheriting a sentence written about executing.
    """
    from foundry_mcp.tools.evidence import KNOWN_EVIDENCE_FAILURE_TOKENS

    assert KNOWN_EVIDENCE_FAILURE_TOKENS, "the token vocabulary is empty"
    unknown = set(_evidence_boundary._SWEEP_REMEDIES) - set(KNOWN_EVIDENCE_FAILURE_TOKENS)
    assert unknown == set(), (
        f"remedy(ies) keyed on a token the vocabulary does not hold: {sorted(unknown)}"
    )
    # The pre-execution members are exactly the ones that must NOT inherit the
    # re-execution sentence, and they are named rather than counted.
    for token in ("EVIDENCE_COMMAND_SYNTAX", "EVIDENCE_COMMAND_MISSING"):
        assert token in _evidence_boundary._SWEEP_REMEDIES, token
        assert "re-executed" not in _evidence_boundary._SWEEP_REMEDIES[token], token


def test_the_head_read_goes_through_the_one_adapter():
    """fallout research/holmes-orchestrator.md#share-4 (D-098) — one invocation.

    share-4 / RA-32 / RA-33: `git rev-parse HEAD` was inlined across modules
    beside the `width._head_sha` adapter that already existed. This module had
    TWO of the four copies; both call the adapter now, which is asserted by
    driving them with the adapter replaced — a copy would ignore the
    replacement and answer from git.
    """
    import foundry_mcp.tools.orchestration.evidence_boundary as eb

    real = eb._head_sha
    try:
        eb._head_sha = lambda _pr: "deadbeefdeadbeef"
        assert eb._boundary_sweep_head(".") == "deadbeefdeadbeef"
    finally:
        eb._head_sha = real

    # ...and no `rev-parse HEAD` argv survives in this module's source.
    source = Path(eb.__file__).read_text(encoding="utf-8")
    assert '"rev-parse", "HEAD"' not in source, (
        "an inline `git rev-parse HEAD` is back in evidence_boundary.py; it "
        "belongs behind width._head_sha, the one adapter share-4 names"
    )
