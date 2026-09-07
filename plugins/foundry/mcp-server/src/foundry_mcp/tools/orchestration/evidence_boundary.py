"""The evidence sweep at every INSPECT and terminal boundary.

Survey blocks S and T. VERIFIER-SET module. GI-002 is a statement about WHO
sweeps: the server, at the crossing, not an agent reporting that it did.
"""
from __future__ import annotations

from pathlib import Path

from foundry_mcp.tools.artifacts import (
    ROLLUP_FILENAME,
    _load_json,
    _save_json,
)
from foundry_mcp.tools.foundry_state import now_iso
# fallout research/holmes-orchestrator.md#share-4 (D-098) — THE ONE `git
# rev-parse HEAD` ADAPTER, imported rather than re-spelled.
#
# `width.py` is a VERIFIER module and so is this one, so this is a
# verifier-to-verifier edge at module top, which GI-033's rule has nothing to
# say about — no seam, no lazy import, no cycle: `width.py` imports no
# orchestration module at module top at all.
from foundry_mcp.tools.orchestration.width import _head_sha
from pathlib import Path




#: fallout FR-058 / GI-029 / AC-056 — PASSES ONLY, KEYED ON HEAD AND SCOPE.
#:
#: The boundary sweep became a RUNG of `_cast_preconditions`,
#: `_inspect_start_preconditions` and `_temper_preconditions`, which is what
#: makes `Foundry-Gate` refuse whatever `Foundry-Phase` refuses. It also means
#: the gate and the transition each ask for it, and a whole-corpus re-execution
#: is minutes. This is `_terminal_evidence_sweep`'s memo argument at the other
#: three boundaries, made once here for all three of these: the pass really was
#: taken at exactly this tree over exactly this scope, so re-taking it answers
#: the same question at the same HEAD.
#:
#: PASSES ONLY. A mismatch is not cached, for the reason the terminal memo does
#: not cache one either: an evidence command may be nondeterministic in a way
#: its `# evidence-volatile:` lines do not yet declare, and remembering a
#: failure would make a corpus that started reproducing look broken until the
#: next commit. Remembering a success has no such asymmetry.
#:
#: Process-local rather than persisted: the pair this exists to collapse is one
#: lead's Gate then Phase inside one server process, and a file would outlive
#: that for no gain the persisted terminal memo does not already give.
_BOUNDARY_SWEEP_PASSES: dict[tuple, dict] = {}

#: Bounded so a long-lived server cannot accumulate one entry per HEAD forever.
#: Oldest-first eviction; insertion order is the dict's own.
_BOUNDARY_SWEEP_MEMO_LIMIT = 16


def _boundary_sweep_head(project_root: str) -> str:
    """HEAD as the sweep's cache key, or "" when it cannot be read.

    A HEAD that cannot be read is not a cache key, so nothing is remembered and
    nothing is served — the same ruling `_terminal_evidence_sweep` makes.

    fallout research/holmes-orchestrator.md#share-4 (D-098) — THROUGH THE ONE
    ADAPTER. This spelled its own `subprocess.run(["git", …, "rev-parse",
    "HEAD"])`, one of four such copies across the package beside the
    `width._head_sha` adapter that already existed for exactly this. Four copies
    of one invocation is four timeouts, four exception tuples and four rulings
    about what a non-zero return means; `_head_sha` is a verifier-to-verifier
    import, which the layering permits without a seam.
    """
    return _head_sha(project_root)


def _sweep_evidence_at_boundary(
    fdir: Path, project_root: str, entry: dict, *, full: bool
) -> dict:
    """Re-execute the in-scope evidence corpus at HEAD (GI-002 / ST-005).

    Returns ``{"ok": bool, "record": {...}, "mismatches": [...], "error": str}``
    where ``record`` is the C-6 `evidence_sweep` object the cycle roll-up
    carries.

    GI-002 IS A STATEMENT ABOUT WHO SWEEPS. "The SERVER sweeps at the boundary
    and refuses on mismatch" — not a teammate reporting that it swept, not a
    lead running a shell loop over `evidence/`. Both of those are claims; this
    is a measurement, taken at the one crossing where HEAD is the tree every
    later gate will judge.

    SCOPE IS DELTA BY DEFAULT. `select_sweep_scope` returns the logs whose
    casting's key_files intersect the diff plus any log whose own
    `# evidence-cmd:` references a touched file, and an empty list is a
    COMPLETE answer — a GRIND that touched nothing in scope re-executes zero
    logs, spawns no worktree and no subprocess (AC-014). The whole corpus is
    swept whenever a FULL rule fired, which by ST-006 includes every INSPECT
    before ASSAY, NYQUIST or DONE.
    """
    from foundry_mcp.tools.evidence import select_sweep_scope, sweep_evidence_at_head

    # D-117 — AN ENTRY WITH NO MODE SWEEPS EVERYTHING.
    #
    # The scope is DELTA by default and delta is a claim: "only these logs can
    # have been invalidated by this GRIND". That claim rests on the width
    # decision, so an entry carrying no `mode` — the shape every D-117 door was
    # admitting as full width — must not also narrow the sweep. The honest
    # reading of an unknown width is the whole corpus, which is the same reading
    # `_decide_inspect_mode` gives an uncomputable diff.
    if not entry.get("mode"):
        full = True

    manifest = _load_json(fdir / "castings" / "manifest.json")
    evidence_dir = Path(project_root) / "evidence"
    logs = select_sweep_scope(
        manifest=manifest,
        evidence_dir=evidence_dir,
        touched_files=list(entry.get("touched_files") or []),
        full=full,
    )
    # The scope is part of the key, not just HEAD: a DELTA boundary and a FULL
    # one at the same commit ask about different corpora, and answering the
    # second from the first would be the D-117 fail-open by another route.
    head = _boundary_sweep_head(project_root)
    memo_key = (
        str(project_root), head, bool(full),
        tuple(sorted(str(p) for p in logs)),
    )
    if head and (cached := _BOUNDARY_SWEEP_PASSES.get(memo_key)) is not None:
        return {**cached, "cached": True}

    outcome = sweep_evidence_at_head(
        project_root=Path(project_root), run_dir=fdir, logs=logs
    )
    record = {
        "scope": "full" if full else "delta",
        # D-149 — HOW MANY LOGS WERE IN SCOPE AT ALL, recorded beside how many
        # re-executed. At a FULL boundary these are the same number and that
        # number IS the corpus, which is the only way a reader can tell "the
        # whole corpus reproduced" from "there was no corpus": a run whose
        # `evidence/` has been deleted sweeps zero logs and passes on nothing,
        # and `ok` alone says the same word for both.
        "corpus_size": len(logs),
        "logs_reexecuted": [str(p) for p in outcome.get("logs_reexecuted", [])],
        # CT-007 — THE PER-LOG COLUMN, CARRIED THROUGH TO THE ARTIFACT.
        #
        # "sweep result recorded per log with scope (delta or full) and elapsed
        # seconds" is one requirement with two halves, and this wrapper used to
        # land only the first. `sweep_evidence_at_head` computed `per_log` — a
        # row for EVERY log in scope, matched or not, each with its own elapsed
        # seconds — and this function copied six sibling fields and dropped it,
        # so the column reached neither `stream-rollup.json`, nor the transition
        # result, nor the report. Driven (D-044): the persisted record's keys
        # were exactly ['elapsed_seconds', 'logs_reexecuted', 'mismatches',
        # 'pool_size', 'scope', 'swept_at'] with `elapsed_seconds` a single run
        # total, while one layer down the producer had the per-log rows in hand.
        # `test_evidence.py` asserted against the producer directly, which is
        # how a whole-suite pass sat on top of the gap.
        #
        # Copied field by field rather than passed through whole, for the same
        # reason `mismatches` is: this is the C-6 document shape and a producer
        # that grows a field does not silently widen a persisted artifact.
        "per_log": [
            {
                "log": row.get("log", ""),
                "elapsed_seconds": row.get("elapsed_seconds", 0.0),
                "matched": bool(row.get("matched")),
                "exit_code": row.get("exit_code"),
                "failure_token": row.get("failure_token"),
            }
            for row in outcome.get("per_log", [])
            if isinstance(row, dict)
        ],
        "mismatches": [
            {"log": m.get("log", ""), "reason": m.get("reason", "")}
            for m in outcome.get("mismatches", [])
        ],
        "elapsed_seconds": outcome.get("elapsed_seconds", 0.0),
        "pool_size": outcome.get("pool_size", 0),
        "swept_at": now_iso(),
    }
    result = {
        "ok": bool(outcome.get("ok")),
        "record": record,
        "mismatches": outcome.get("mismatches", []),
        "error": outcome.get("error") or "",
    }
    if head and result["ok"]:
        while len(_BOUNDARY_SWEEP_PASSES) >= _BOUNDARY_SWEEP_MEMO_LIMIT:
            _BOUNDARY_SWEEP_PASSES.pop(next(iter(_BOUNDARY_SWEEP_PASSES)))
        _BOUNDARY_SWEEP_PASSES[memo_key] = result
    return result




#: D-133 — where the whole-corpus sweep taken at a TERMINAL boundary is
#: remembered, keyed by the HEAD it was taken at. D-149 adds the durable
#: `last_full_pass` entry, which survives the memo's per-HEAD rewrites.
TERMINAL_SWEEP_FILENAME = ".evidence-swept-at-head.json"



#: D-149 — the token the DONE / NYQUIST_DONE doors refuse a stripped corpus
#: with. Named in `commands/start.md`'s F6 step and pinned there by
#: `tests/test_lead_prose.py`, so the lead reads the same word in the guidance
#: and in the refusal.
EVIDENCE_STRIPPED_TOKEN = "EVIDENCE_CORPUS_STRIPPED_BEFORE_SWEEP"




def _evidence_corpus_existed(fdir: Path, project_root: str) -> bool:
    """Did this run ever have a committed evidence corpus?

    D-149's discriminator between the two ways a terminal whole-corpus sweep
    can cover zero logs: a run that committed no evidence at all — honest,
    there is nothing to sweep and nothing to have stripped — and a run whose
    committed corpus was DELETED before this door was asked. `ok` says the same
    word for both, and only the second is a defect.

    TWO INDEPENDENT SOURCES, because neither alone answers the driven case.

    GIT IS THE AUTHORITY. `git rev-list -1 HEAD -- evidence/` names the last
    commit that touched the path, and the strip commit ITSELF touches it — it
    is a deletion of tracked files. So a non-empty answer means the corpus was
    tracked at some point on this history, whether or not it is in the tree
    now, which is exactly the question. This half is what catches a lead who
    strips BEFORE asking any terminal door, where the run directory has no
    record that a corpus ever existed.

    THE RUN'S OWN RECORDS answer where git cannot — no repository, a shallow
    clone, a run whose evidence lives outside this tree. `stream-rollup.json`
    carries every INSPECT boundary's sweep (recursively: the rollup nests
    records under per-cycle sub-keys such as `temper_entry` and
    `nyquist_entry`), and the terminal marker carries `corpus_seen`, the
    high-water mark a terminal sweep records whether or not it passed.
    """
    import subprocess

    try:
        rev = subprocess.run(
            ["git", "-C", project_root, "rev-list", "-1", "HEAD", "--", "evidence/"],
            capture_output=True, text=True, timeout=10,
        )
        if rev.returncode == 0 and rev.stdout.strip():
            return True
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        pass

    def _walk(node: object, depth: int = 0) -> bool:
        if depth > 6 or not isinstance(node, dict):
            return False
        sweep = node.get("evidence_sweep")
        if isinstance(sweep, dict):
            if sweep.get("logs_reexecuted") or int(sweep.get("corpus_size") or 0) > 0:
                return True
        return any(_walk(child, depth + 1) for child in node.values())

    if _walk(_load_json(fdir / ROLLUP_FILENAME)):
        return True
    memo = _load_json(fdir / TERMINAL_SWEEP_FILENAME)
    if not isinstance(memo, dict):
        return False
    # `corpus_seen` as well as `last_full_pass`, because the driven case is a
    # corpus that MISMATCHED and was then stripped: no pass was ever recorded,
    # and reading that absence as "there was never a corpus" would let exactly
    # the failing run through.
    return bool(memo.get("last_full_pass") or memo.get("corpus_seen"))




def _terminal_evidence_sweep(fdir: Path, project_root: str) -> dict:
    """GI-002's whole-corpus sweep at the NYQUIST and DONE boundaries.

    Returns ``{"ok", "record", "mismatches", "error", "head", "cached"}``.

    D-133 — "BEFORE ASSAY/NYQUIST/DONE" WAS ONE BOUNDARY OF THREE.
    -------------------------------------------------------------
    GI-002 (Locked) names the whole corpus as the sweep scope "when the FULL
    rule fires or BEFORE ASSAY/NYQUIST/DONE". Only the ASSAY path was covered,
    and it was covered indirectly — through the `final_gate` INSPECT that
    precedes it. The terminal doors swept nothing, and `fixes_after_decision`
    (the D-035 stamp that catches a fix landing mid-INSPECT) is read only by
    `inspect_clean`, which a run reaching NYQUIST from F5 never calls again.

    Driven: a run at F5 with the temper entry recorded and swept; a commit
    during F5 changed a file `evidence/casting-2-beta.log`'s command reads, so
    that log no longer reproduces; `_sweep_evidence_at_boundary(full=True)`
    returned ok False naming it — and `Foundry-Phase('nyquist')` then returned
    ok True, phase F5.5, and `_done_preconditions`' checklist (report_generated,
    escalated_classes_cleared, run_not_halted, spec_requirements_parsed,
    all_verified, zero_blocking_defects, no_active_teams, verdict_coverage)
    carried no evidence rung at all. A run reached DONE with its committed
    corpus never re-executed over the fixes F5 and F5.5 landed. The gap was
    flagged in concerns.md at GRIND cycle 2 and no ruling closed it.

    KEYED ON HEAD, AND MEMOISED, so the GATE and the TRANSITION can both ask.
    `_done_preconditions` is ONE evaluation with two callers by design — the
    D-037 discipline that the transition and the gate cannot disagree about
    what "done" means — and an evidence rung present in only one of them would
    be that drift restored. But a whole-corpus re-execution is minutes, and
    `Foundry-Gate('done')` then `Foundry-Phase('done')` would pay it twice.
    So the sweep is taken once per HEAD and the result is written to
    ``.evidence-swept-at-head.json``; a later caller at the SAME HEAD reads the
    verdict back instead of re-running it. That is not a weaker claim: the
    corpus really was re-executed at exactly this tree. Any commit — which is
    what a fix landing in F5 or F5.5 is — moves HEAD and invalidates the
    memo, which is the case the defect was filed on.

    A HEAD that cannot be read is not a cache key, so nothing is memoised and
    the sweep runs; a sweep that could not RUN is not a sweep that passed, and
    both are reported through the same `ok` the callers refuse on.

    D-149 — AND THE PASS IS RECORDED AGAINST THE COMMIT THAT STILL CARRIED THE
    CORPUS, BECAUSE THE F6 STEP DELETES IT.
    -------------------------------------------------------------------------
    `commands/start.md` mandates, verbatim, `git rm -r evidence/ && git commit
    -m "chore(foundry): strip consumed run evidence" -- evidence/` as an F6
    step, and this repo's own history carries that commit. Driven at cycle 8 on
    a run at F5.5 with one committed log whose command no longer reproduced:
    BEFORE the strip this returned ok False naming
    `evidence/casting-1-handler.log` and `Foundry-Phase('done')` was refused;
    AFTER the identical strip the same door returned ok True, scope full,
    `logs_reexecuted` [], `mismatches` [] — because `select_sweep_scope` reads
    the evidence directory in the TREE, and a directory that is gone yields no
    logs. The identical regression refused DONE before the strip and passed
    after it, so GI-002's terminal sweep passed over nothing on the guided path.

    `sweep_evidence_at_head` cannot tell "zero logs because the DELTA diff
    touched nothing" from "zero logs because the corpus was deleted", and at a
    `full=True` terminal boundary only the second is possible. So this function
    records `last_full_pass` — head, corpus size and timestamp — every time a
    whole-corpus sweep PASSES over a non-empty corpus, and carries it forward
    across the per-HEAD memo rewrites so the strip commit cannot erase it. The
    door then has three distinguishable states, which `_done_preconditions`
    refuses on (see its rung):

      * corpus present at HEAD          -> swept now, logs=N;
      * corpus absent, pass recorded    -> passes ON THE RECORDED PRE-STRIP
                                           PASS, naming the commit it swept;
      * corpus absent, no pass recorded -> refused,
                                           EVIDENCE_CORPUS_STRIPPED_BEFORE_SWEEP.

    `commands/start.md` documents that order — Foundry-Gate(phase='done') and
    only THEN the strip — and `tests/test_lead_prose.py#
    test_the_f6_sequence_sweeps_before_it_strips` fails if the two ever swap.
    """
    # fallout research/holmes-orchestrator.md#share-4 (D-098) — the second of
    # this module's two copies, through the same one adapter.
    head = _head_sha(project_root)

    memo_path = fdir / TERMINAL_SWEEP_FILENAME
    memo = _load_json(memo_path)
    if not isinstance(memo, dict):
        memo = {}
    # D-149: the durable half of the marker, read before the memo can be
    # replaced and carried onto every result so the callers never re-read it.
    prior_pass = memo.get("last_full_pass")
    if not isinstance(prior_pass, dict):
        prior_pass = None
    # The high-water mark: the largest corpus a terminal sweep has SELECTED in
    # this run, whether or not it passed. `last_full_pass` alone cannot answer
    # "did this run have a corpus", because the driven case is a corpus that
    # MISMATCHED and was then stripped — no pass was ever recorded, and reading
    # its absence as "there was never a corpus" is the defect one step over.
    corpus_seen = memo.get("corpus_seen")
    if not isinstance(corpus_seen, dict):
        corpus_seen = None
    if head:
        if memo.get("head") == head and memo.get("ok"):
            return {
                "ok": True,
                "record": memo.get("record") or {},
                "mismatches": [],
                "error": "",
                "head": head,
                "cached": True,
                "last_full_pass": prior_pass,
            }

    # `entry` carries no mode on purpose: `_sweep_evidence_at_boundary` reads an
    # entry with no `mode` as "sweep everything", which is the scope this
    # boundary owes anyway, and there is no width decision to invent here.
    sweep = _sweep_evidence_at_boundary(fdir, project_root, {}, full=True)
    corpus_size = int(sweep["record"].get("corpus_size") or 0)
    if corpus_size > 0:
        # Seen, whatever the verdict.
        corpus_seen = {
            "head": head,
            "corpus_size": max(
                corpus_size, int((corpus_seen or {}).get("corpus_size") or 0)
            ),
            "seen_at": now_iso(),
        }
        # D-149: a PASS over a NON-EMPTY corpus is what earns the durable
        # record the strip then spends. A pass over nothing earns nothing —
        # that is the whole distinction.
        if head and sweep["ok"]:
            prior_pass = {
                "head": head,
                "corpus_size": corpus_size,
                "swept_at": now_iso(),
            }
    result = {
        "ok": sweep["ok"],
        "record": sweep["record"],
        "mismatches": sweep["mismatches"],
        "error": sweep["error"],
        "head": head,
        "cached": False,
        "last_full_pass": prior_pass,
        "corpus_seen": corpus_seen,
    }
    # Written whenever there is anything durable to keep, not only on a pass:
    # the memo half is keyed on HEAD and the strip commit MOVES HEAD, so a
    # record that lived only in the replaced document would be deleted by the
    # very commit it exists to survive (D-149).
    memo_out: dict = {}
    if head and sweep["ok"]:
        memo_out = {
            "head": head,
            "ok": True,
            "record": sweep["record"],
            "swept_at": now_iso(),
        }
    if prior_pass is not None:
        memo_out["last_full_pass"] = prior_pass
    if corpus_seen is not None:
        memo_out["corpus_seen"] = corpus_seen
    if memo_out:
        _save_json(memo_path, memo_out)
    return result




def _terminal_evidence_state(fdir: Path, project_root: str) -> dict:
    """GI-002's terminal evidence rung, evaluated ONCE for every crossing.

    Returns the sweep result plus the three-state discrimination D-149
    established: ``{"evidence", "record", "logs_reexecuted", "corpus_size",
    "prior_pass", "stripped", "ok"}``.

      * corpus present at HEAD          -> swept now, ok is the sweep's verdict;
      * corpus absent, pass recorded    -> ok, ON THE RECORDED PRE-STRIP PASS;
      * corpus absent, no pass recorded -> not ok, `stripped` is True and the
                                           refusal names
                                           EVIDENCE_CORPUS_STRIPPED_BEFORE_SWEEP.

    D-159 — THE RUNG WAS APPLIED TO TWO CROSSINGS OF THE THREE GI-002 NAMES.
    -----------------------------------------------------------------------
    D-149 gave `_done_preconditions` the three states, which covers `done`,
    `nyquist_done` and `Foundry-Gate('done')` because all three read that one
    evaluation. The `nyquist` branch — the F5 -> F5.5 entry, and the ONE
    boundary GI-002 names by the word NYQUIST — calls `_terminal_evidence_sweep`
    itself and refused on `ok` alone, so it kept the pre-D-149 rule.

    Driven through the real door on a run at F5 with one committed log
    `evidence/casting-1-handler.log` whose command no longer reproduced, then
    the F6 strip `commands/start.md` mandates verbatim (`git rm -r evidence/ &&
    git commit -m 'chore(foundry): strip consumed run evidence' -- evidence/`):
    BEFORE the strip `Foundry-Phase(phase='nyquist')` was refused naming the
    log; AFTER the identical strip the same call returned ok True, phase F5.5,
    `evidence_sweep {scope: full, corpus_size: 0, logs_reexecuted: [],
    mismatches: []}` — a whole-corpus sweep passing over nothing, with no
    `last_full_pass` ever recorded because the corpus had mismatched and so
    never earned one. On the same stripped run `nyquist_done` and `done` both
    refused, naming the token. A --nyquist run had a second route around the
    rung: enter F5.5 through the door that did not apply it.

    Stated as ONE function with three callers rather than as a second copy in
    the `nyquist` branch, for the reason `_done_preconditions` itself exists:
    a terminal evidence rule each door evaluates for itself is a rule each door
    can evaluate differently, which is precisely how this crossing kept the old
    one through a whole-suite pass.
    """
    evidence = _terminal_evidence_sweep(fdir, project_root)
    record = evidence.get("record") or {}
    prior_pass = evidence.get("last_full_pass")
    prior_pass = prior_pass if isinstance(prior_pass, dict) else None
    corpus_size = int(record.get("corpus_size") or 0)
    stripped = (
        bool(evidence["ok"])
        and corpus_size == 0
        and prior_pass is None
        and _evidence_corpus_existed(fdir, project_root)
    )
    return {
        "evidence": evidence,
        "record": record,
        "logs_reexecuted": len(record.get("logs_reexecuted") or []),
        "corpus_size": corpus_size,
        "prior_pass": prior_pass,
        "stripped": stripped,
        "ok": bool(evidence["ok"]) and not stripped,
    }




def _stripped_corpus_reason(state: dict) -> tuple[str, str]:
    """The (reason, hint) pair a stripped corpus earns — worded ONCE (D-159).

    Every terminal crossing says the same words, because they are refusing the
    same fact. `_done_preconditions` uses the pair as its `reason` / `hint`;
    a transition wraps the reason in its own "Cannot <door> — " prefix.
    """
    head = (state["evidence"].get("head") or "unknown")[:8]
    return (
        (
            f"{EVIDENCE_STRIPPED_TOKEN}: the committed evidence corpus is not "
            f"present at HEAD ({head}) and this run recorded no whole-corpus "
            "sweep pass at a commit that carried it"
        ),
        (
            "Sweep first, strip second. Restore evidence/ (git revert the strip "
            "commit, or git checkout <pre-strip commit> -- evidence/ and commit "
            "it), call Foundry-Gate(phase='done') so the whole corpus "
            "re-executes and the pass is recorded in "
            f"{TERMINAL_SWEEP_FILENAME} under last_full_pass, and only THEN "
            "strip. A sweep over a corpus that is no longer there proves "
            "nothing, and passing on it is how a log that stopped reproducing "
            "during F5 or F5.5 reaches DONE unread."
        ),
    )




def _terminal_evidence_refusal(state: dict, door: str) -> dict | None:
    """The refusal a terminal crossing owes, or None when the rung passes.

    ``door`` is the phrase that follows "Cannot " — "enter NYQUIST", "mark the
    run DONE". One entry point for both failing states so no crossing can adopt
    the mismatch half and miss the stripped half, which is the D-159 shape.
    """
    if state["ok"]:
        return None
    if state["stripped"]:
        reason, hint = _stripped_corpus_reason(state)
        return {
            "error": f"Cannot {door} — {reason}",
            "hint": hint,
            "token": EVIDENCE_STRIPPED_TOKEN,
            "evidence_sweep": state["record"],
        }
    return _terminal_sweep_refusal(state["evidence"], door)




def _terminal_sweep_refusal(sweep: dict, door: str) -> dict:
    """The named refusal a mismatched TERMINAL sweep produces (GI-002/CT-007).

    Names each log, exactly as `_sweep_refusal` does at the INSPECT boundaries,
    and names the door it refused so the lead knows which call to re-make.
    """
    if sweep["error"]:
        return {
            "error": (
                f"Cannot {door} — the evidence sweep could not run: "
                f"{sweep['error']}"
            ),
            "hint": (
                "A sweep that could not run is not a sweep that passed. Fix the "
                "condition named above and retry."
            ),
            "evidence_sweep": sweep["record"],
        }
    named = [m.get("log", "?") for m in sweep["mismatches"]]
    return {
        "error": (
            f"Cannot {door} — {len(named)} committed evidence log(s) no longer "
            f"reproduce at HEAD: {', '.join(named)}"
        ),
        "hint": (
            "GI-002 sweeps the WHOLE corpus before ASSAY, NYQUIST and DONE. "
            "Each log's `# evidence-cmd:` was re-executed in a detached "
            "worktree at HEAD and its output no longer matches what was "
            "committed — most often because a fix landed after the log was "
            "captured. Either the behaviour regressed, or the owning casting "
            "must re-capture the log; then retry."
        ),
        "mismatches": sweep["mismatches"],
        "evidence_sweep": sweep["record"],
    }




def _sweep_refusal(sweep: dict, cycle: int, token: str = "inspect_start") -> dict:
    """The named refusal a mismatched evidence sweep produces (CT-007).

    NAMES EACH LOG. A sweep that says "something no longer reproduces" sends
    the lead to re-run the whole corpus by hand to find out which; the sweep
    already knows, and CT-007 says to say so.

    Also names the counter it did NOT advance, because the operator's next
    question after a refused transition is always whether the run moved.

    ``token`` is the phase token that was refused, because D-014 gave this
    refusal three callers rather than one: every transition that OPENS an
    INSPECT sweeps, and a hint telling a lead to re-call `inspect_start` after
    a refused `temper` names a transition that is not the one it was making.
    """
    if sweep["error"]:
        return {
            "error": (
                f"Cannot cross into INSPECT — the evidence sweep could not run: "
                f"{sweep['error']}"
            ),
            "hint": (
                "A sweep that could not run is not a sweep that passed. Fix the "
                f"condition named above and re-call Foundry-Phase"
                f"(phase='{token}')."
            ),
            "cycle": cycle,
            "evidence_sweep": sweep["record"],
        }
    named = [m.get("log", "?") for m in sweep["mismatches"]]
    return {
        "error": (
            f"Cannot cross into INSPECT — {len(named)} committed evidence log(s) "
            f"no longer reproduce at HEAD: {', '.join(named)}"
        ),
        "hint": (
            _sweep_remedy(sweep["mismatches"])
            + " The phase has NOT advanced and the cycle counter has NOT "
            f"moved; re-call Foundry-Phase(phase='{token}') when it reproduces."
        ),
        "cycle": cycle,
        "mismatches": sweep["mismatches"],
        "evidence_sweep": sweep["record"],
    }


#: fallout AC-035 / US-008 / CT-015 (D-148) — THE REMEDY IS KEYED TO THE
#: FAILURE TOKEN, because one of them is not a re-execution failure at all.
#:
#: `EVIDENCE_COMMAND_SYNTAX` is raised BEFORE the command runs: CT-015 says a
#: log whose command fails `/bin/sh -n` "is refused with token
#: EVIDENCE_COMMAND_SYNTAX before execution", and the sweep's own
#: `mismatches[].reason` says so in the same return value. The hint said the
#: opposite — "was RE-EXECUTED in a detached worktree at HEAD and its output no
#: longer matches what was committed" — and offered two remedies, neither of
#: which resolves the token: re-capturing a log whose command does not parse
#: reproduces the identical refusal, and there is no behaviour to fix because
#: nothing ran. DRIVEN: a committed log with an unparseable `# evidence-cmd:`
#: swept at HEAD refused with failure_token EVIDENCE_COMMAND_SYNTAX, a spy on
#: `_run_command_with_timeout` proved the command never reached the runner, and
#: the side-effect marker was not created.
#:
#: US-008 is "so that a syntax mistake costs a commit, not a cycle", and this is
#: exactly where that has to hold: a log predating the pre-commit guard, or one
#: committed with the hook skipped, reaches this door and the lead was sent to
#: re-capture it. The actual remedy — fix the command's syntax — appeared
#: nowhere in `error` or `hint`, only in `mismatches[].reason`, which a consumer
#: rendering error+hint never shows.
#:
#: KEYED BY TOKEN, PINNED TO THE VOCABULARY. `tests/orchestration/
#: test_evidence_boundary.py#test_every_evidence_failure_token_has_a_remedy`
#: derives the key set from `evidence.KNOWN_EVIDENCE_FAILURE_TOKENS`, so a
#: token added to that closed set fails there rather than silently taking the
#: re-execution sentence written for a different failure.
_SWEEP_REMEDIES = {
    "EVIDENCE_COMMAND_SYNTAX": (
        "The log's `# evidence-cmd:` does not PARSE under `/bin/sh -n`, so it "
        "was refused BEFORE execution and nothing was run. Re-capturing the log "
        "reproduces this refusal — fix the command's syntax in the log, parse "
        "it yourself with `/bin/sh -n`, then re-capture."
    ),
    "EVIDENCE_COMMAND_MISSING": (
        "The log carries no `# evidence-cmd:` header, so there is nothing to "
        "re-execute. Its owning casting must add the header naming the command "
        "that produces the body, and re-capture."
    ),
    "EVIDENCE_VOLATILE_MALFORMED": (
        "A `# evidence-volatile:` line is not a usable regex, so the output "
        "could not be redacted before comparison. Fix the pattern in the log's "
        "header and re-capture."
    ),
    "EVIDENCE_FOR_MALFORMED": (
        "The log's `# evidence-for:` header names no valid requirement id. Fix "
        "the header and re-capture."
    ),
    "EVIDENCE_TIMEOUT": (
        "The log's `# evidence-cmd:` did not finish inside its timeout. Either "
        "the behaviour it demonstrates got slower — fix that — or the command "
        "genuinely needs longer and its `# evidence-timeout:` must say so."
    ),
}

#: Every other member of the closed set IS a re-execution result, and this is
#: the sentence they have always carried.
#:
#: fallout GI-006 / AC-035 / US-008 (D-153) — AND IT NAMES THE THIRD CAUSE,
#: WHICH IS THE COMMON ONE.
#:
#: The sentence offered two causes and both were wrong for 5 of the 14 logs that
#: failed the sweep on this run. DRIVEN: `Foundry-Gate(phase='inspect_start')`
#: refused with 14 of 78 logs not reproducing; three casting-2 logs and
#: `casting-7-ownership-consistency.log` had failed on lines that SAY WHY they
#: differ. `_setup_worktree` shells `git worktree add --detach`, so the checkout
#: the sweep re-executes in carries TRACKED FILES ONLY — every untracked file,
#: and every gitignored one, is absent from it. `/forge-specs/` is gitignored
#: (.gitignore:15), so the run spec can NEVER be in any detached checkout, and
#: `tests/orchestration/test_module_boundaries.py` therefore skips permanently
#: in every sweep with a self-declared reason ("this checkout carries no
#: spec.md at ..."). The log's own BODY names its cause and the remedy did not
#: read it.
#:
#: THE COST OF THE OMISSION IS A WRONG DIAGNOSIS, not a missing one: an operator
#: was sent to look for a regression that does not exist, or to re-capture in
#: the same tree that produced the mismatch — which reproduces it exactly, and
#: costs a second crossing to discover. So the third cause is named FIRST among
#: the checks, because it is the one the output itself can settle, and the
#: reader is pointed at the diff the sweep already returns rather than at a
#: guess about which cause applies.
#:
#: fallout GI-006 (C-091, from D-175) — AND A FOURTH FAMILY THAT IS A PROPERTY
#: OF NEITHER THE TREE NOR THE LOG.
#:
#: The three causes above are each a property of the TREE the log was captured
#: in or of the LOG itself, and the enumeration was CLOSED — "THREE causes, and
#: the mismatch itself tells them apart". A mismatch caused by the sweep's own
#: execution context therefore matched none of them and the reader was routed
#: to the residual, (3) stale log, whose remedy is re-capture — the single
#: action that makes such a mismatch PERMANENT. That is not hypothetical: at
#: 72b4963 `Foundry-Gate(phase='inspect_start')` refused on exactly one of 96
#: logs, and the whole diff was one leading line — uv's `VIRTUAL_ENV=… does not
#: match the project environment path .venv` warning, emitted because the
#: runner inherited the lead shell's `VIRTUAL_ENV`. The log, the tree and the
#: behaviour were all untouched. Re-capturing would have baked the warning into
#: the committed bytes and INVERTED the bug: the log would then reproduce only
#: for an operator whose shell exports that exact path and refuse for everyone
#: else, CI included.
#:
#: NARROWED, NOT ELIMINATED, which is why this is a cause and not a footnote.
#: `worktree_helpers.py#_child_environment` made the child environment a closed
#: allowlist, so the variable half is largely shut — but `PATH` and `HOME` stay
#: on that allowlist because nothing runs without them, so a pyenv/mise shim or
#: a `~/.gitconfig` still reaches the child; and the sweep's checkout is FRESH,
#: so the first `uv run` in it builds the project environment from scratch and
#: any command that does not pass `--quiet` prints that construction into the
#: bytes being compared. Both are properties of the RUN, not of what was
#: committed.
#:
#: PLACED SECOND, ON THE ORDERING RULE THE PARAGRAPH ABOVE ALREADY STATES: the
#: output-settleable causes come first. This one is settleable from the diff —
#: its tells are lines about tool configuration, environment or interpreter
#: selection that the log's body never had a reason to carry — so it stands
#: beside (1) rather than after the two that need a guess. Its remedy is stated
#: as a prohibition because the harm is asymmetric: the other three are fixed
#: by acting, this one is CREATED by acting.
_SWEEP_REMEDY_REEXECUTED = (
    "Each log's `# evidence-cmd:` was re-executed in a detached worktree at "
    "HEAD and its output no longer matches what was committed. FOUR causes, "
    "and the mismatch itself tells them apart — read the diff before acting. "
    "(1) The log was captured in a tree carrying files the sweep's checkout "
    "does not have: `git worktree add --detach` checks out TRACKED FILES ONLY, "
    "so anything untracked or gitignored (the run spec under `forge-specs/`, "
    "for one) is absent there and a command that reads it takes a different "
    "branch — usually a SKIP whose own reason line says so, which is what the "
    "differing line will read. Re-capturing changes nothing; the log must "
    "either not depend on that file or declare the difference. (2) The "
    "difference is a property of THE SWEEP'S OWN EXECUTION CONTEXT rather than "
    "of the tree or the log — the environment the runner hands the child, or "
    "the state of the fresh checkout it builds in. The tell is a differing "
    "line about tool configuration, environment or interpreter selection that "
    "the log's body never had a reason to carry: an environment-mismatch "
    "warning, an interpreter being selected, a project environment being "
    "built, packages being installed. Its remedy is NEVER re-capture — "
    "re-capturing pins the log to the context that produced it, so it would "
    "then reproduce only where that context recurs and refuse everywhere else, "
    "CI included. Fix the runner, or the log's own command, so that context "
    "cannot reach the compared bytes. (3) The "
    "behaviour the log demonstrates regressed — fix that. (4) The log is stale "
    "and its owning casting must re-capture it."
)


def _sweep_remedy(mismatches: list) -> str:
    """The remedy sentence(s) for what actually went wrong (fallout D-148).

    One sentence when every mismatch shares a token, and one per DISTINCT token
    otherwise — a mixed sweep whose logs failed for different reasons has more
    than one remedy, and collapsing them onto the majority is the same harm as
    naming the wrong one.
    """
    tokens: list[str] = []
    for mismatch in mismatches:
        token = (mismatch or {}).get("failure_token") if isinstance(mismatch, dict) else None
        key = token if token in _SWEEP_REMEDIES else ""
        if key not in tokens:
            tokens.append(key)
    if not tokens:
        return _SWEEP_REMEDY_REEXECUTED
    return " ".join(
        _SWEEP_REMEDIES[t] if t else _SWEEP_REMEDY_REEXECUTED for t in tokens
    )
