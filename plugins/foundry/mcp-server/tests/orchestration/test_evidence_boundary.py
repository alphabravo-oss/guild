"""The evidence sweep at the INSPECT and terminal boundaries.

Carved from `tests/test_orchestrator_gates.py` (fallout FR-005 / GI-026 /
AC-014 / OT-016): one test module per shipped orchestration module, landed in
the same casting as the source move so no pin is ever left pointing at a module
that no longer exists.
"""
from __future__ import annotations

import subprocess
from pathlib import Path


from foundry_mcp.tools.foundry_state import current_cycle

# fallout FR-004 / AC-013 — THE MODULE OBJECTS, UNDER UNDERSCORE ALIASES.
#
# `streams`, `spend`, `width`, `gates`, `directives` and `teams` are all LOCAL
# variable names somewhere in this suite, and a local rebinding shadows a
# module for the rest of its function. The aliases are what `ORCHESTRATION`,
# `owning_module` and every `monkeypatch.setattr` resolve through; individual
# SYMBOLS are imported by name below, which is how the carved modules read.
from foundry_mcp.tools.orchestration import evidence_boundary as _evidence_boundary

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
from tests.orchestration._env import ORCHESTRATION, patch_everywhere  # noqa: F401

from tests.orchestration._env import (  # noqa: F401
    _arm_ordering_token,
    _write_manifest_with_castings,
    _write_state,
    run_env,
)

from foundry_mcp.tools.orchestration.evidence_boundary import (  # noqa: F401
    _sweep_evidence_at_boundary,
    _sweep_refusal,
    _sweep_remedy,
    _terminal_evidence_sweep,
    _terminal_sweep_refusal,
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

    fallout CT-015: a log whose command fails to parse "is refused with token
    EVIDENCE_COMMAND_SYNTAX BEFORE execution", and the sweep's own
    `mismatches[].reason` says so in the same return value. The hint said the
    opposite — "was RE-EXECUTED in a detached worktree at HEAD and its output no
    longer matches what was committed" — and offered two remedies, neither of
    which resolves the token: re-capturing a log whose command does not parse
    reproduces the identical refusal, and no behaviour regressed because nothing
    ran.

    fallout US-008 is "so that a syntax mistake costs a commit, not a cycle". A log
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


def test_the_terminal_door_prescribes_the_same_remedy_as_the_boundary_one():
    """fallout AC-035 / US-008 / CT-015 (D-148, then D-185) — THE SIBLING
    SURFACE THE FIRST FIX DID NOT REACH.

    fallout AC-035 names TWO doors, and D-148's fix reached one. `_sweep_refusal`
    (the INSPECT boundary) built its hint from `_sweep_remedy`;
    `_terminal_sweep_refusal` — the ASSAY / NYQUIST / DONE half of the same rule
    — did not call it at all and hard-coded the exact sentence the
    `_SWEEP_REMEDIES` comment names as wrong.

    Driven at the terminal door with the same unparseable log the test above
    drives at the boundary: the hint read "Each log's `# evidence-cmd:` was
    re-executed in a detached worktree at HEAD and its output no longer matches
    what was committed ... Either the behaviour regressed, or the owning casting
    must re-capture the log; then retry." Nothing was re-executed, nothing
    regressed, and re-capturing reproduces the identical refusal — a remedy that
    is a LOOP, on the one token whose whole point (fallout US-008) is that a syntax
    mistake costs a commit rather than a cycle.

    PINNED AS AN EQUALITY BETWEEN THE DOORS, not as a second copy of the
    sentence. A remedy added to `_SWEEP_REMEDIES` for a new token reaches both
    doors or this fails — which is the property the hard-coded half could never
    have.
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
    hint = _terminal_sweep_refusal(sweep, "mark the run DONE")["hint"]

    # The false claim is gone from THIS door too...
    assert "re-executed in a detached worktree" not in hint, hint
    assert "the owning casting must re-capture the log" not in hint, hint
    # ...and the remedy that resolves the token is here.
    assert "does not PARSE" in hint, hint
    assert "/bin/sh -n" in hint, hint
    assert "Re-capturing the log reproduces this refusal" in hint, hint
    # The terminal door still says which door refused and that nothing moved.
    assert "mark the run DONE" in _terminal_sweep_refusal(
        sweep, "mark the run DONE")["error"]
    assert "has NOT advanced" in hint, hint

    # THE EQUALITY: both doors prescribe the SAME remedy for the same token, so
    # neither can drift from `_SWEEP_REMEDIES` again on its own.
    boundary = _sweep_refusal(sweep, cycle=1, token="inspect_start")["hint"]
    remedy = _sweep_remedy(sweep["mismatches"])
    assert remedy in hint, (remedy, hint)
    assert remedy in boundary, (remedy, boundary)

    # ...and the ADJACENT token does not regress at this door either: an output
    # mismatch IS a re-execution result and must still say so.
    output = {
        "error": "",
        "record": {"scope": "full"},
        "mismatches": [_mismatch(
            "evidence/casting-2-width-rule.log",
            "EVIDENCE_OUTPUT_MISMATCH",
            "byte mismatch after volatile redaction",
        )],
    }
    output_hint = _terminal_sweep_refusal(output, "enter NYQUIST")["hint"]
    assert "re-executed in a detached worktree" in output_hint, output_hint
    assert "does not PARSE" not in output_hint, output_hint


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


def test_the_re_execution_remedy_names_the_tracked_files_only_checkout():
    """fallout GI-006 / AC-035 / US-008 (D-153) — the third cause, which is the
    common one, and which the sentence did not have.

    The remedy offered exactly two causes — regression, or a stale log — and
    both were wrong for 5 of the 14 logs that failed the sweep on this run.
    DRIVEN: `Foundry-Gate(phase='inspect_start')` refused with 14 of 78 logs not
    reproducing, and three casting-2 logs plus `casting-7-ownership-consistency.log`
    had failed on lines that state their own reason. `_setup_worktree` shells
    `git worktree add --detach`, so the sweep's checkout carries TRACKED FILES
    ONLY; `forge-specs/` is gitignored, so the run spec can never be in one and
    `tests/orchestration/test_module_boundaries.py` skips permanently in every
    sweep with a self-declared reason. The operator was sent to hunt a
    regression that does not exist, or to re-capture in the same tree — which
    reproduces the identical mismatch and costs a second crossing to learn.

    The two causes it already carried must SURVIVE: naming only the new one
    would be this defect with the sign flipped.

    fallout GI-006 (C-091, from D-175) — AND THE FOURTH CAUSE, ON THE SAME
    TERMS. This node is where the sentence's CAUSE LIST is pinned, which is why
    it already asserted the two causes it was not filed for. A fourth family
    joined them: a mismatch that is a property of the sweep's own execution
    context rather than of the tree or the log. The enumeration was CLOSED at
    three, so a reader who hit that family was routed to the residual member —
    the stale log, whose remedy is re-capture, which for this family is the one
    action that makes the mismatch permanent. DRIVEN at 72b4963:
    `Foundry-Gate(phase='inspect_start')` refused on one of 96 logs over a
    single leading line, uv's `VIRTUAL_ENV=… does not match the project
    environment path .venv` warning, produced because the runner inherited the
    lead shell's environment — the log, the tree and the behaviour all
    untouched.

    THE COUNT WORD IS PINNED TO THE ENUMERATION, not merely present. "THREE
    causes" outliving a fourth member is the same misdirection in miniature: a
    reader who counts three and finds three stops reading. Both are derived
    from the string below rather than hand-listed here, so a fifth cause fails
    this node until its count word moves with it.
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

    # The mechanism, named — not "the environment differed", which is the
    # sentence an operator cannot act on.
    assert "TRACKED FILES ONLY" in hint, hint
    assert "untracked or gitignored" in hint, hint
    assert "forge-specs/" in hint, hint
    # ...and the consequence that makes re-capture the WRONG move for it.
    assert "Re-capturing changes nothing" in hint, hint
    # The reader is sent to the evidence rather than to a guess: the sweep
    # already returns the differing output, and the skip line names its own
    # cause.
    assert "read the diff before acting" in hint, hint

    # Both original causes survive, in the same words.
    assert "behaviour the log demonstrates regressed" in hint, hint
    assert "stale and its owning casting must re-capture it" in hint, hint
    # ...and the sentence the two sibling tests pin is unchanged.
    assert "re-executed in a detached worktree" in hint, hint

    # fallout GI-006 (C-091) — THE FOURTH CAUSE. Named as what it is a property
    # OF, because that is the distinction the reader has to make; "the
    # environment differed" is the sentence nobody can act on.
    assert "SWEEP'S OWN EXECUTION CONTEXT" in hint, hint
    assert "rather than of the tree or the log" in hint, hint
    # The tells, so the reader can settle it from the diff the sweep returns
    # rather than guessing which of four causes applies.
    assert "tool configuration, environment or interpreter selection" in hint, hint
    # ...and the prohibition, which is the half that matters: for this family
    # the remedy the other three share is what makes the mismatch permanent.
    assert "remedy is NEVER re-capture" in hint, hint
    assert "pins the log to the context that produced it" in hint, hint

    # fallout GI-006 (C-091) — THE COUNT WORD AGREES WITH THE ENUMERATION.
    #
    # Both sides are DERIVED from the sentence: the members from its own `(N) `
    # markers, the word from the count. A hand-listed "assert FOUR in hint"
    # would go stale the same way "THREE" did, which is the drift this guards.
    members = [n for n in range(1, 10) if f"({n}) " in hint]
    assert members == list(range(1, len(members) + 1)), (
        f"the cause list is not numbered contiguously from 1: {members}"
    )
    counts = {2: "TWO", 3: "THREE", 4: "FOUR", 5: "FIVE", 6: "SIX"}
    assert f"{counts[len(members)]} causes" in hint, (
        f"the sentence enumerates {len(members)} causes but its count word is "
        f"not {counts[len(members)]!r} — a reader who counts the word and finds "
        f"that many stops reading. Move the count word with the member."
    )


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
    # Both logs are still named in the error, which is fallout CT-015's own
    # per-log clause -- NOT fallout CT-007, which in this run's spec is
    # Foundry-Next's heading_for and has nothing to say about a sweep.
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
