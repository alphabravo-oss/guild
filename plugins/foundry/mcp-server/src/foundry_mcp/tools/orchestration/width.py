"""The INSPECT width decision, its git inputs and its readers.

Survey blocks K, L, M and U, plus block N's cycle roll-up and block O's
liveness wait, which are stated in terms of the crossing this module decides.
VERIFIER-SET module: a diff that moves this can make a verdict already
reached wrong, which is the whole of what `verifier_touched` is for.
"""
from __future__ import annotations

import re
from pathlib import Path

from foundry_mcp.schemas.vocab import (
    DELTA_CONDITIONAL_STREAMS,
    FULL_ROSTER_STREAMS,
    INSPECT_DELTA_RULE,
    INSPECT_MODES,
    PROVE_DELTA_SAMPLE_SIZE,
    is_verifier_path,
)
from foundry_mcp.tools.artifacts import (
    CAST_BASELINE_SHA_MARKER,
    INSPECT_BOUNDARY_SHA_MARKER,
    RESEARCH_SKIPPED_MARKER,
    TRACE_CLEAN_AT_MARKER,
    _document_problem,
    _document_transaction,
    _load_json,
    _read_text,
    _resolve_spec_path,
    _stream_marker,
)
from foundry_mcp.tools.foundry_state import (
    current_cycle,
    get_run_dir,
    now_iso,
    read_document,
    read_text_file,
)
from pathlib import Path
from foundry_mcp.tools.orchestration.streams import ROLLUP_FILENAME
from foundry_mcp.tools.orchestration.teams import (
    _check_active_teams,
    _check_sight_required,
)




#: D-238 — git's C-quote escapes, for the one place the quoted form survives.
#:
#: `core.quotepath=false` stops git escaping non-ASCII bytes, and that is the
#: whole of the common case. It does NOT stop git quoting a path that contains a
#: double quote, a backslash or a control character: those are quoted whatever
#: `quotepath` says, because the quoting is what keeps such a path on ONE line.
#: So a parse that reads git's LINE-oriented output still meets the quoted form
#: and still has to undo it.
_C_QUOTE_ESCAPES = {
    "a": "\a", "b": "\b", "f": "\f", "n": "\n", "r": "\r", "t": "\t",
    "v": "\v", "\\": "\\", '"': '"',
}




def _decode_git_path(field: str) -> str:
    """One path as git PRINTS it, back to the path it NAMES (D-238).

    A field that is not quoted is returned unchanged, so this is safe to apply
    to every path git hands back rather than only to the ones a caller guessed
    might need it. A quoted field is unwrapped and its escapes are undone —
    octal escapes accumulate as BYTES and are decoded as UTF-8 at the end,
    because git emits one escape per byte and a multi-byte character therefore
    arrives as several (`\\303\\251` is one `é`, not two characters).

    WHY THIS EXISTS RATHER THAN A SECOND FLAG (D-238 / D-239's shared class).
    Where git can be asked for NUL-separated output it is (`git_changed_paths`),
    and then nothing is ever quoted and this is never reached. `git show
    --numstat` is the one consumer that cannot take that route without changing
    its record grammar — with `-z` a rename becomes three NUL-separated tokens
    rather than one tab field — so it keeps the line grammar
    `_numstat_rename_paths` parses, passes `core.quotepath=false`, and undoes
    the residual quoting here.
    """
    if not isinstance(field, str):
        return ""
    if len(field) < 2 or not (field.startswith('"') and field.endswith('"')):
        return field
    body = field[1:-1]
    out = bytearray()
    i = 0
    while i < len(body):
        ch = body[i]
        if ch != "\\":
            out.extend(ch.encode("utf-8"))
            i += 1
            continue
        i += 1
        if i >= len(body):
            # A trailing lone backslash is not an escape git would emit; keep
            # it rather than dropping a byte the path may really carry.
            out.extend(b"\\")
            break
        esc = body[i]
        if esc in "01234567":
            digits = ""
            while i < len(body) and len(digits) < 3 and body[i] in "01234567":
                digits += body[i]
                i += 1
            out.append(int(digits, 8) & 0xFF)
            continue
        out.extend(_C_QUOTE_ESCAPES.get(esc, esc).encode("utf-8"))
        i += 1
    return out.decode("utf-8", errors="replace")




def git_changed_paths(
    project_root: str, base: str, head: str = "HEAD", *, timeout: float = 30.0
) -> dict:
    """Repo-relative paths changed between two revisions, spelled as they ARE.

    Returns ``{"ok": bool, "files": [sorted paths], "error": str}``. ``ok``
    False means the diff is UNKNOWN, which is not the same answer as an empty
    diff — every caller must keep the two apart.

    D-239 — THE ONE INVOCATION, BECAUSE THE ESCAPED FORM WAS BEING MATCHED ON.
    -------------------------------------------------------------------------
    `git diff --name-only` prints a path for a HUMAN to read: with
    `core.quotepath` at its default (true) a non-ASCII path arrives wrapped in
    quotes with its bytes octal-escaped — `"schemas/mod\\303\\250le.py"` — and
    every consumer of this list matches that string against something. Driven in
    a throwaway repo at git 2.50.1: a commit touching a verifier file with a
    non-ASCII name printed `"schemas/mod\\303\\250le.py"`, and
    `vocab.is_verifier_path` on it is False because the leading quote defeats
    the `(?:^|/)schemas/` anchor — so the transition recorded DELTA immediately
    after the machinery that judges the build was modified, which is exactly
    what ST-006 / AC-016 exist to prevent. The same strings are compared whole
    against manifest `key_files` and `test01_scope`, so a touched declared file
    read as untouched and `select_sweep_scope` returned [], re-executing none of
    that casting's evidence logs at a boundary GI-002 says sweeps them.

    BOTH DEFENCES, because either alone leaves a hole. `core.quotepath=false`
    stops the octal escaping of non-ASCII bytes; `-z` stops the quoting
    ALTOGETHER, including for a path holding a double quote or a control
    character, which quotepath does not govern. With NUL separators there is no
    line to keep intact, so git emits the bytes and nothing needs decoding —
    which is why this returns paths no caller has to remember to unquote.

    ONE HELPER, NOT ONE PER CALL SITE. `_grind_diff` and `_trace_skip_check`
    both ran their own copy of this invocation and `foundry_spawn`'s
    cycle-context diff runs a third. A rule fixed in one copy is this run's
    repeated failure shape, so the invocation is written once here and imported
    by the others. Public deliberately: `foundry_spawn` is a sibling casting's
    module and it imports this name.
    """
    import subprocess

    try:
        proc = subprocess.run(
            [
                "git", "-C", project_root,
                # BEFORE the subcommand: it is a git-wide config override.
                "-c", "core.quotepath=false",
                "diff", "--name-only", "-z",
                # Everything after this is a revision or a path, never a flag.
                "--end-of-options", base, head,
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except (FileNotFoundError, OSError, subprocess.SubprocessError) as exc:
        return {
            "ok": False, "files": [],
            "error": f"git unavailable: {type(exc).__name__}",
        }
    if proc.returncode != 0:
        return {
            "ok": False, "files": [],
            "error": f"git diff failed: {proc.stderr.strip()[:120]}",
        }
    return {
        "ok": True,
        "files": sorted({tok for tok in proc.stdout.split("\0") if tok.strip()}),
        "error": "",
    }




def _trace_skip_check(fdir: Path, project_root: str) -> dict:
    """Decide whether the current F2 INSPECT can skip the TRACE stream.

    Rationale: TRACE is LSP-heavy (EXISTS / SUBSTANTIVE / WIRED / PLACED
    across every manifest symbol). A cycle of TRACE routinely runs 100+
    Serena IPC calls over several minutes. Topology is a pure function of
    the code on disk — if no file owning a manifest symbol has changed
    since the last clean TRACE, the verdicts are provably identical.

    Returns {skip: bool, reason: str, details?: {...}}.
    """
    marker = fdir / TRACE_CLEAN_AT_MARKER
    if not marker.exists():
        return {"skip": False, "reason": "no prior clean TRACE to compare against"}
    marker_data, marker_problem = read_document(marker)
    if marker_problem is not None:
        return {"skip": False, "reason": "unreadable .trace-clean-at marker"}
    clean_sha = marker_data.get("head_sha", "")
    if not clean_sha:
        return {"skip": False, "reason": "no head_sha recorded"}

    from foundry_mcp.tools.foundry_spawn import _manifest_shape_problem

    manifest = _load_json(fdir / "castings" / "manifest.json")
    # D-134: same shared validator as every other reader of this document, so
    # "unusable" means one thing across the package.
    if _manifest_shape_problem(manifest) is not None:
        return {"skip": False, "reason": "castings/manifest.json records are unreadable"}
    key_files: set[str] = set()
    for c in manifest.get("castings", []):
        for f in (c.get("key_files") or []):
            if isinstance(f, str) and f.strip():
                key_files.add(f.strip())
    if not key_files:
        return {"skip": False, "reason": "no key_files declared in manifest — cannot scope diff"}

    # D-239: through the ONE invocation, so this skip decision and the width
    # decision cannot disagree about whether a non-ASCII key_file was touched.
    # This ran its own copy with neither `-z` nor `core.quotepath=false`, so a
    # touched declared file whose name is not ASCII arrived escaped, missed the
    # `key_files` set it is compared whole against, and read as untouched —
    # which is a SKIP of the stream that would have caught the change.
    diff = git_changed_paths(project_root, clean_sha, "HEAD", timeout=10)
    if not diff["ok"]:
        return {"skip": False, "reason": diff["error"]}

    changed_files = set(diff["files"])
    overlap = changed_files & key_files
    if overlap:
        return {
            "skip": False,
            "reason": f"{len(overlap)} manifest key_file(s) changed since {clean_sha[:8]}",
            "details": {"changed_keyfiles": sorted(overlap)[:10]},
        }
    return {
        "skip": True,
        "reason": f"no manifest key_files changed since clean TRACE at {clean_sha[:8]}",
        "details": {
            "clean_sha": clean_sha,
            "total_changed": len(changed_files),
            "manifest_key_files": len(key_files),
        },
    }




def _maybe_skip_trace(fdir: Path, project_root: str) -> dict | None:
    """If the TRACE skip gate fires, auto-stamp .trace-complete as skipped.

    Called from foundry_next_action so the decision is made deterministically
    before any stream dispatching instructions go out. No-op when TRACE is
    already complete or skip preconditions aren't met.

    D-071 — THE RECORDED WIDTH FENCES THE SKIP.
    -------------------------------------------
    This skip predates the FULL/DELTA rule and referenced it not at all, so it
    satisfied a FULL roster's trace requirement without TRACE running — at the
    INSPECT before ASSAY included. Driven on a cycle recorded FULL/final_gate
    whose GRIND touched a non-key_file: Foundry-Next auto-stamped
    `.trace-complete` and `_check_streams_complete` returned complete True,
    missing "", with TRACE never run. AC-017 and FR-012 sanction exactly two
    exceptions to the FULL roster — a stream in `manifest.stream_skips`, or a
    `research_skipped` record — and this is neither; GI-007 forbids any casting
    from disabling or downweighting a stream; US-004's premise is that "every
    final gate still runs everything at full width".

    So the new rule fences it rather than removing it. At FULL the skip never
    fires. At DELTA it fires only when the GRIND diff is EMPTY — TRACE's DELTA
    scope is "the symbols the GRIND commits touched" (AC-019), and when nothing
    was touched there are no symbols to walk, which is the one case where
    skipping and running are the same answer. A run with NO recorded decision
    keeps the pre-change `_trace_skip_check` behaviour, which is what a resumed
    archive should get.
    """
    if not fdir or not fdir.exists():
        return None
    if (fdir / _stream_marker("trace")).exists():
        return None
    state = _load_json(fdir / "state.json")
    if state.get("phase") != "F2":
        return None

    recorded = _current_inspect_mode(fdir)
    mode = (recorded or {}).get("mode", "")
    if mode == "FULL":
        return {
            "skip": False,
            "reason": (
                "this INSPECT is recorded FULL (rule "
                f"{(recorded or {}).get('rule', '?')}) — the full roster runs, "
                "and TRACE is in it"
            ),
        }
    if mode == "DELTA":
        touched = [
            f for f in (recorded or {}).get("touched_files") or []
            if isinstance(f, str) and f.strip()
        ]
        if touched:
            return {
                "skip": False,
                "reason": (
                    f"DELTA width over {len(touched)} touched file(s) — TRACE "
                    "runs over the symbols the GRIND commits touched"
                ),
                "details": {"touched_files": sorted(touched)[:10]},
            }
        decision = {
            "skip": True,
            "reason": (
                "DELTA width and the GRIND diff is empty — there are no touched "
                "symbols for TRACE to walk"
            ),
            "details": {"inspect_mode": "DELTA", "touched_files": []},
        }
        (fdir / _stream_marker("trace")).write_text(
            f"{now_iso()} cycle=skipped\n"
            f"items_checked=0\n"
            f"items_total=0\n"
            f"coverage=SKIPPED\n"
            f"findings=0\n"
            f"skipped=true\n"
            f"reason={decision['reason']}\n",
            encoding="utf-8",
        )
        return decision

    # D-117 — AN UNRECORDED WIDTH NO LONGER FALLS THROUGH TO THE LEGACY RULE.
    #
    # This arm read "A run with NO recorded decision keeps the pre-change
    # `_trace_skip_check` behaviour, which is what a resumed archive should
    # get", and `_trace_skip_check` skips on a `.trace-clean-at` marker plus an
    # untouched key_file set — so a resumed legacy archive auto-stamped
    # `.trace-complete` and TRACE never ran, reaching D-071's own defect through
    # the width the fence forgot to name. FULL and DELTA are the only two
    # answers that license a decision about TRACE's scope; there is no third.
    #
    # `_trace_skip_check` keeps its definition: it is the pre-width rule this
    # fence replaced, it is directly tested, and `tests/test_spawn_progress.py`
    # names it in the D-134 manifest-reader roster that scan asserts it still
    # sees.
    if (unrecorded := _unrecorded_width_problem(fdir)) is not None:
        return {"skip": False, "reason": unrecorded["reason"], "hint": unrecorded["hint"]}

    decision = _trace_skip_check(fdir, project_root)
    if not decision.get("skip"):
        return decision

    (fdir / _stream_marker("trace")).write_text(
        f"{now_iso()} cycle=skipped\n"
        f"items_checked=0\n"
        f"items_total=0\n"
        f"coverage=SKIPPED\n"
        f"findings=0\n"
        f"skipped=true\n"
        f"reason={decision['reason']}\n",
        encoding="utf-8",
    )
    return decision




# --------------------------------------------------------------------------- #
# INSPECT WIDTH (C-12 / ST-006 / ST-007 / GI-008 / GI-009 / FR-011 / FR-012)
#
# WHERE THE DECISION LIVES, AND WHY IT IS NOT NEGOTIABLE
# -----------------------------------------------------
# GI-009: "whichever Foundry-Phase transition opens an INSPECT records the
# mode; Foundry-Next only reports. No decision ever lives in Foundry-Next."
# GI-008 says the same thing from the other side: "A FULL versus DELTA decision
# computed inside Foundry-Next" is the named violation.
#
# So every function below COMPUTES and nothing below WRITES except through the
# transition that called it. `_check_streams_complete` and
# `_compute_next_action` read `state.json.inspect_modes[-1]` and re-derive
# nothing — a roster recomputed at display time is a roster that can disagree
# with the one the cycle actually ran, and a streams-complete check reading a
# roster nothing recorded is the failure ST-007 describes.
#
# The whole point is cost. thunder-viper ran 22 GRIND cycles at full INSPECT
# width, so a three-file GRIND was followed by a re-verification of everything.
# DELTA makes the second and later INSPECTs of a phase proportional to what
# changed, while every FULL rule keeps the gates that matter at full width.
# --------------------------------------------------------------------------- #

#: ``INSPECT_BOUNDARY_SHA_MARKER`` — the HEAD recorded at each INSPECT boundary,
#: so the next crossing knows what "since the last boundary" means — is declared
#: with the other run markers beside ``_RUN_MARKER_NAMES``, because D-201's
#: guard has to know the whole set BEFORE any of them is written and a set built
#: from names declared further down the file cannot be evaluated up there.


def _head_sha(project_root: str) -> str:
    """HEAD of the shared tree, or "" when git cannot answer. Never raises."""
    import subprocess

    try:
        result = subprocess.run(
            ["git", "-C", project_root, "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=10,
        )
    except (FileNotFoundError, OSError, subprocess.SubprocessError):
        return ""
    return result.stdout.strip() if result.returncode == 0 else ""




def _boundary_base_sha(fdir: Path) -> tuple[str, str]:
    """The commit a GRIND diff is measured from, and where it came from.

    Three sources in descending precedence, all of them commits this run itself
    recorded:

      1. the previous INSPECT boundary — the exact answer to "since the last
         sweep" (CT-007);
      2. the last clean TRACE, which `_trace_skip_check` already keeps;
      3. the CAST baseline, written when the run left F1.

    Returns ``("", "")`` when none exists, which is a run whose first INSPECT
    has not happened — and that INSPECT is FULL by `first_of_phase` anyway.
    """
    marker = fdir / INSPECT_BOUNDARY_SHA_MARKER
    if marker.exists():
        sha = _read_text(marker).strip()
        if sha:
            return sha, INSPECT_BOUNDARY_SHA_MARKER
    trace_marker = fdir / TRACE_CLEAN_AT_MARKER
    if trace_marker.exists():
        data, problem = read_document(trace_marker)
        if problem is None and data.get("head_sha"):
            return str(data["head_sha"]), TRACE_CLEAN_AT_MARKER
    cast_marker = fdir / CAST_BASELINE_SHA_MARKER
    if cast_marker.exists():
        sha = _read_text(cast_marker).strip()
        if sha:
            return sha, CAST_BASELINE_SHA_MARKER
    return "", ""




def _grind_diff(fdir: Path, project_root: str) -> dict:
    """Repo-relative paths the GRIND touched since the last boundary.

    Returns ``{"files": [...], "base": sha, "source": marker, "problem": str}``.
    A non-empty ``problem`` means the diff is UNKNOWN — not empty. The two are
    opposite answers and the callers must not confuse them: an unknown diff
    cannot support a delta roster, so it forces FULL and a whole-corpus sweep.
    """
    base, source = _boundary_base_sha(fdir)
    if not base:
        return {"files": [], "base": "", "source": "",
                "problem": "no recorded boundary, clean TRACE or CAST baseline to diff from"}
    # D-239: through the ONE invocation. This is the diff EVERY width decision
    # reads — `is_verifier_path` for the FULL rule, the manifest `key_files` and
    # `test01_scope` matches for the per-stream scope, and `select_sweep_scope`
    # for which evidence logs re-execute — and it returned git's escaped display
    # form, which none of those three comparisons can match.
    diff = git_changed_paths(project_root, base, "HEAD", timeout=30)
    if not diff["ok"]:
        return {"files": [], "base": base, "source": source,
                "problem": diff["error"]}
    return {"files": diff["files"], "base": base, "source": source, "problem": ""}




def _repo_relative(project_root: str, path: Path) -> str:
    """`path` spelled relative to `project_root`, or unchanged when it is not under it."""
    try:
        return str(path.resolve().relative_to(Path(project_root).resolve()))
    except (ValueError, OSError):
        return str(path)




def _spec_relative_paths(project_root: str) -> list[str]:
    """EVERY repo-relative spelling of this run's spec, for `is_verifier_path`.

    The spec is NOT a member of VERIFIER_PATH_PATTERNS on purpose — a run's spec
    lives wherever `state.json.spec_path` says, and a static pattern would
    either miss it or sweep in every unrelated spec.md in the tree. So it is
    passed at call time, which means resolving it here. FR-032's "one constant
    rather than typed in several places" is why this is the ONLY resolver: a
    singular `_spec_relative_path` was written first, superseded by this one
    when D-102 landed, and left in the file with a docstring presenting it as a
    live sibling — so the next reader had to derive from call-site absence that
    it was dead (D-196). It is gone;
    `test_every_private_function_the_plugin_ships_is_reachable` is what keeps
    the next superseded helper from being left behind the same way — over every
    file the plugin ships, since D-199 found the class living in a script and
    in the test corpus while the pin's subject set was this module alone.

    D-102 — THE SPEC HAS TWO LEGAL SPELLINGS AND THE FULL RULE SAW ONE.
    ------------------------------------------------------------------
    FR-011 is Locked and verbatim: "FULL when: … or the GRIND diff touches
    vocab.py, schemas/, gate/orchestrator code, agent/skill prose, or the spec."
    C-1 says the spec "is matched by `spec_path` (the run's
    `state.json.spec_path` AND `foundry-archive/{run}/spec.md`), passed at call
    time" — two paths, and `is_verifier_path` takes one.

    `_resolve_spec_path` PREFERS `<run_dir>/spec.md` and only falls back to
    `state.json.spec_path`, so on the ordinary run — which has both, because
    `foundry_init` copies the spec into the archive — the singular resolver
    returns the archive copy and the authored spec at `state.json.spec_path` was
    invisible to the rule. Driven at three spec locations including
    `forge-specs/subject/spec.md`: a GRIND diff whose ONLY touched file was the
    path `state.json` records as `spec_path` opened the next INSPECT at DELTA,
    rule `delta`, while every other FULL trigger (vocab.py, schemas/findings.py,
    the orchestrator module, agents/assayer.md) recorded FULL/`verifier_touched`
    correctly. The same record showed `test01` treating that spec as "a covered
    file was touched", so the two consumers of one touched-files list disagreed
    about whether the spec had moved.

    Both spellings are returned, deduplicated and in a stable order, and the
    caller asks `is_verifier_path` about each. Widening the vocab helper to take
    a sequence was the alternative and is not ours to make: `vocab.py` is
    casting 1's file and its signature is LOCKED by C-1.
    """
    spellings: list[str] = []
    fdir = get_run_dir(project_root)
    if fdir:
        archived = fdir / "spec.md"
        if archived.exists():
            spellings.append(_repo_relative(project_root, archived))
        declared = _load_json(fdir / "state.json").get("spec_path", "")
        if isinstance(declared, str) and declared.strip():
            # Recorded as a repo-relative path already; normalised through the
            # same resolver anyway so an absolute `spec_path` compares equal to
            # the relative diff entries `git diff --name-only` produces.
            spellings.append(
                _repo_relative(project_root, Path(project_root) / declared.strip())
            )
    resolved = _resolve_spec_path(project_root)
    if resolved is not None:
        spellings.append(_repo_relative(project_root, resolved))
    seen: list[str] = []
    for spelling in spellings:
        if spelling and spelling not in seen:
            seen.append(spelling)
    return seen




def _research_skipped(fdir: Path) -> bool:
    """Does this run carry a record that RESEARCH was skipped (AC-017)?

    Read from three places because no single one owns it: `castings/manifest.json`
    and `state.json` are where `foundry_init` writes run-level flags, and a
    `.research-skipped` marker is the shape every other per-run fact in this
    directory takes. Any of them saying so is enough — a run that recorded the
    skip anywhere recorded it.
    """
    if (fdir / RESEARCH_SKIPPED_MARKER).exists():
        return True
    for document in ("state.json", "castings/manifest.json"):
        if bool(_load_json(fdir / document).get("research_skipped")):
            return True
    return False




def _skipped_streams(fdir: Path) -> set[str]:
    """`manifest.stream_skips` as wire ids — casting 4's reader, lazily."""
    from foundry_mcp.tools.foundry_spawn import _skipped_stream_ids

    return _skipped_stream_ids(fdir)




def _research_scope_touched(
    fdir: Path, project_root: str, touched: list[str]
) -> dict:
    """Did the GRIND diff touch a file RESEARCH_AUDIT covers (ST-007)?

    A casting declaring a `research_context` is a casting whose code was written
    against research recommendations, so its key_files are the files an audit of
    those recommendations reads. A diff touching none of them cannot have
    deviated from research that no longer applies to anything that moved.

    Returns the shared conditional answer — `{"touched", "computable",
    "source", "detail"}` — because it is one arm of `DELTA_CONDITIONAL_STREAMS`
    and every arm answers the same three-valued question through
    `_delta_conditional_scope`. `source` names WHICH source answered:
    `no diff`, `manifest`, or `unknown`. The detail names WHICH casting and
    WHICH file matched on a hit, and WHY the set was not computable on an
    unknown; it is empty on a COMPUTED miss, because the skip's provenance is
    the caller's own sentence and a second, unread one is a second thing that
    can drift.

    ``project_root`` is not read here. It is in the signature because every
    conditional arm is called through ONE signature by the shared path, and an
    arm whose shape the shared path cannot call is an arm that gets called
    some other way — which is the whole of D-208.

    D-208 — A MANIFEST THIS SERVER NEVER READ IS NOT A MANIFEST DECLARING NO
    RESEARCH.
    -----------------------------------------------------------------------
    This returned a two-valued `{touched, detail}` with no third value, and
    took its covered set from `_load_json(castings/manifest.json)`, which
    yields `{}` for an ABSENT document — so `manifest.get("castings", [])` was
    empty and the arm returned `touched: False`, INDISTINGUISHABLE from a
    manifest that was read and declares no `research_context`.

    Driven at the wire at 916c1ca: a non-self-targeting run, manifest declaring
    casting 1 with `research_context` and key_files ["src/api/users.py"], a
    control cycle touching that key_file recorded `research_audit`
    {scope full, detail "casting 1 declares research_context and the diff
    touched its key_file src/api/users.py"}. With `castings/manifest.json`
    DELETED and the identical diff, `Foundry-Phase('inspect_start')` returned
    ok and recorded `research_audit` {scope skipped, detail "no file
    research_audit covers was touched"} — a NEGATIVE about a set it had not
    read — while `test01`, in the SAME `stream_scope`, recorded "could not be
    computed". Two arms of one decision, one input, opposite failure
    directions. The artifact guard does not close it: an invalid-JSON or
    wrong-shape manifest IS named and refuses at the entry point, so the ABSENT
    manifest is the one uncomputable shape that reaches this arm.

    The line is drawn at whether the document was READ, per the lead ruling:
    absent (or unreadable, or with a `castings` cell of the wrong type) is
    UNKNOWN and therefore required; read, with no casting declaring a
    `research_context`, is a computed miss and therefore skipped. The cycle-20
    docstring in `test_the_research_audit_arm_is_unmoved_by_the_test01_source_
    ladder` asserted this set was "computable on every run"; that premise is
    what this drive falsified, and it is corrected there.

    THE TWO AXES ARE UNCHANGED AND WERE ALWAYS RIGHT, which is why D-204 named
    only the sibling. WHICH names: the manifest's own `key_files` cells, a
    declared list, never prose. HOW they map to files: they ARE files, compared
    by whole-string equality against the diff, so there is no mapping step to
    get wrong. What moved is a third axis the sibling already had — HOW MANY
    ANSWERS the question has.
    """
    if not touched:
        return {
            "touched": False,
            "computable": True,
            "source": "no diff",
            "detail": "",
        }

    manifest_path = fdir / "castings" / "manifest.json"
    if not manifest_path.exists():
        # `_load_json` cannot tell this from an empty document BY DESIGN (read
        # its docstring), so the absence is tested here rather than inferred
        # from a `{}` that means four different things.
        return _covered_set_unknown(
            "research_audit",
            "the run has no castings/manifest.json, so which castings declare "
            "a research_context is unknown",
        )
    problem = _document_problem(manifest_path)
    if problem is not None:
        # `_artifact_guard` names this at the MCP entry point and refuses, so
        # in practice it does not reach here. It is answered anyway: a reader
        # that would return a negative for a document it could not read is the
        # defect, whether or not some caller happens to shield it.
        return _covered_set_unknown("research_audit", problem)

    manifest = _load_json(manifest_path)
    castings = manifest.get("castings")
    if castings is None:
        castings = []
    if not isinstance(castings, list):
        return _covered_set_unknown(
            "research_audit",
            f"castings/manifest.json's castings cell is a "
            f"{type(castings).__name__}, not a list of castings",
        )

    touched_set = set(touched)
    for casting in castings:
        if not isinstance(casting, dict) or not casting.get("research_context"):
            continue
        for f in casting.get("key_files") or []:
            if isinstance(f, str) and f.strip() in touched_set:
                return {
                    "touched": True,
                    "computable": True,
                    "source": "manifest",
                    "detail": (
                        f"casting {casting.get('id', '?')} declares "
                        f"research_context and the diff touched its key_file "
                        f"{f.strip()}"
                    ),
                }
    # READ, and it declares no covered file the diff touched. A computed miss.
    return {
        "touched": False,
        "computable": True,
        "source": "manifest",
        "detail": "",
    }




def _contracts_surface_cells(project_root: str) -> tuple[list[tuple[str, str]], str | None]:
    """The spec's Contracts table as `(row id, surface cell)` pairs, and why not.

    Returns `(rows, problem)`. `problem` is None when the spec was READ — even
    if it holds no Contracts table at all — and a named reason when it could
    not be, so the caller can tell "this spec declares no surfaces" from "this
    server cannot see what it declares".

    The table is read as CELLS, not as text. Rows are the pipe-delimited lines
    of the `## Contracts` section, the surface column is located by its header
    name rather than by index (a table that gains a column ahead of `surface`
    must not silently start returning `input`), and the alignment row is
    skipped.

    D-207 — `[]` USED TO MEAN BOTH THINGS, AND THE CALLER READ IT AS THE
    HARMLESS ONE.
    ---------------------------------------------------------------------
    The docstring said it outright: "Returns [] when the spec cannot be read or
    has no such table — an unreadable spec names no surfaces, so nothing is
    covered by it." A spec with no table genuinely covers nothing and TEST-01
    SKIPs on it with a reason; a spec that could not be read covers an UNKNOWN
    set, and the two arrived at `_test01_scope_touched` as the same empty list,
    where the second was asserted as a negative. A covered set the server cannot
    compute is not an empty covered set — the same sentence that governs the
    arm below, applied to the document this one reads.
    """
    spec_path = _resolve_spec_path(project_root)
    if spec_path is None:
        return [], "the run records no readable spec path"
    if not spec_path.exists():
        return [], f"the run's spec {spec_path.name} does not exist"
    text, problem = read_text_file(spec_path)
    if problem is not None:
        return [], problem

    lines = text.splitlines()
    lowered = [line.strip().lower() for line in lines]
    start = next(
        (i for i, line in enumerate(lowered) if line.startswith("## contracts")),
        None,
    )
    if start is None:
        # READ, and it declares no surfaces. That is an answer, not a failure.
        return [], None
    end = next(
        (i for i in range(start + 1, len(lines)) if lowered[i].startswith("## ")),
        len(lines),
    )

    rows: list[tuple[str, str]] = []
    column: int | None = None
    for i in range(start + 1, end):
        line = lines[i].strip()
        if not line.startswith("|"):
            # A blank line or prose ends the table; a later table in the same
            # section starts its own header search.
            column = None
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if column is None:
            header = [c.lower() for c in cells]
            if "surface" in header:
                column = header.index("surface")
            continue
        if set(line.replace("|", "").strip()) <= set("-: "):
            continue  # the alignment row
        if column < len(cells):
            rows.append((cells[0], cells[column]))
    return rows, None




def _registry_tool_modules() -> dict[str, list[str]]:
    """Tool name -> the plugin modules that implement it, from the REGISTRY.

    `server.py`'s `_DISPATCH` IS the tool-name-to-handler binding, so it is the
    only thing that knows which module answers `Foundry-Report`. The mapping is
    read out of the dispatch entry's own code object rather than out of prose:
    a name it loads from the server's globals that resolves to a function of a
    `foundry_mcp` module contributes that module, and a dotted `foundry_mcp.*`
    name it names contributes that module directly — which is how a lazy
    in-function import is followed. Entries that dispatch through a helper
    defined in `server.py` itself are walked one more hop, because that helper
    is where the real handler is named: `"Foundry-Report": lambda args:
    _dispatch_report()` reaches `generate_report` only inside
    `_dispatch_report`, and stopping at the lambda would map CT-014 to
    `server.py` and leave `tools/foundry_report.py` uncovered.

    Returns `{}` when the server module cannot be imported. Nothing is claimed
    as covered in that case, which is the honest answer: without the registry
    there is no evidence of what implements what, and the alternative — guessing
    from names — is the D-204 defect itself.
    """
    from types import FunctionType

    try:
        from foundry_mcp import server as _server
    except Exception:  # pragma: no cover - registry unavailable
        return {}

    package_root = Path(__file__).resolve().parent.parent  # .../foundry_mcp

    def module_file(dotted: str) -> str | None:
        if dotted == "foundry_mcp":
            candidate = package_root / "__init__.py"
            return str(candidate) if candidate.exists() else None
        if not dotted.startswith("foundry_mcp."):
            return None
        rel = dotted[len("foundry_mcp.") :].replace(".", "/")
        for candidate in (package_root / f"{rel}.py", package_root / rel / "__init__.py"):
            if candidate.exists():
                return str(candidate)
        return None

    def walk(code, found: set[str], depth: int, seen: set[str]) -> None:
        if depth > 3:
            return
        for name in code.co_names:
            if name.startswith("foundry_mcp"):
                found.add(name)
                continue
            obj = _server.__dict__.get(name)
            if not isinstance(obj, FunctionType):
                continue
            module = getattr(obj, "__module__", "") or ""
            if not module.startswith("foundry_mcp"):
                continue
            if module == "foundry_mcp.server":
                if name not in seen:
                    seen.add(name)
                    walk(obj.__code__, found, depth + 1, seen)
            else:
                found.add(module)

    registry: dict[str, list[str]] = {}
    dispatch = getattr(_server, "_DISPATCH", None)
    if not isinstance(dispatch, dict):
        return {}
    for tool, handler in dispatch.items():
        code = getattr(handler, "__code__", None)
        if code is None:
            continue
        modules: set[str] = set()
        walk(code, modules, 0, set())
        files = {f for m in modules if (f := module_file(m)) is not None}
        if files:
            registry[str(tool)] = sorted(files)
    return registry




def _path_matches(covered: str, candidate: str) -> bool:
    """Do two path spellings name the same file, one possibly abbreviated?

    Equality, or one being a SEGMENT-ANCHORED suffix of the other. Both
    directions are needed because the two sides are spelled by different
    authorities: `git diff --name-only` yields repo-relative paths, and the
    registry yields the executing package's absolute paths, which are only
    repo-relative on a self-targeting run. The anchor on `/` is what keeps this
    from being the substring search D-204 was filed on — `src/a.py` matches no
    covered path, where an unanchored `in` made `a` match anything.
    """
    left = covered.replace("\\", "/").lstrip("./")
    right = candidate.replace("\\", "/").lstrip("./")
    if not left or not right:
        return False
    return left == right or left.endswith(f"/{right}") or right.endswith(f"/{left}")




#: THE ANSWER SHAPE every member of ``DELTA_CONDITIONAL_STREAMS`` returns.
#: Named once, here, because ``_delta_conditional_scope`` validates against
#: this tuple: an arm that answers with fewer keys has not given a narrower
#: answer, it has left the question unanswered, and D-208 is what reading the
#: first as the second costs.
_CONDITIONAL_ANSWER_KEYS = ("touched", "computable", "source", "detail")




def _covered_set_unknown(wire: str, reason: str) -> dict:
    """The THIRD answer, for ANY conditional stream: the covered set could not
    be computed, so require the stream.

    Mirrors `_decide_inspect_mode`'s uncomputable-diff arm one function over —
    "the GRIND diff could not be computed ..., so the verifier cannot be shown
    to be untouched" — because it is the same sentence about a different set,
    and the two must not disagree about which way an unknown fails.

    D-208 — ONE CONSTRUCTOR FOR EVERY ARM, NOT ONE PER ARM.
    ------------------------------------------------------
    This was `_test01_covered_set_unknown`, and the class it belongs to
    (`inspect-mode-rule-is-a-proxy-not-the-fact`) recurred for three straight
    cycles because each fix taught ONE arm of the shared DELTA branch a lesson
    its sibling never heard: D-204 anchored test01's WHICH and HOW, D-207 gave
    test01 the third value, and D-208 then found `_research_scope_touched`
    still answering a two-valued question about a manifest it had not read.
    The wire id is a parameter so the sentence is the SAME sentence whichever
    stream could not be computed — for `test01` it is byte-identical to the one
    D-207 shipped.
    """
    return {
        "touched": True,
        "computable": False,
        "source": "unknown",
        "detail": (
            f"the set of files {wire} covers could not be computed ({reason}), "
            f"so {wire} cannot be shown to be untouched"
        ),
    }




def _declared_test01_scope(fdir: Path) -> list[str]:
    """The paths the RUN ITSELF declares TEST-01 covers, in declared order.

    Read from `castings/manifest.json` — the document the decompose step writes
    and the one artifact that describes THIS run's target rather than the
    program the server happens to be executing. Both homes the lead ruling
    named are accepted, because either is the run "already recording" it: a
    top-level `test01_scope` list, and a `test01_scope` list on any casting row
    of the same manifest. Additive on both, so `migrate-archive.py` learns
    nothing and a pre-change archive simply declares none.
    """
    manifest = _load_json(fdir / "castings" / "manifest.json")
    declared: list[str] = []

    def _absorb(cells: object) -> None:
        if not isinstance(cells, list):
            return
        declared.extend(
            c.strip() for c in cells if isinstance(c, str) and c.strip()
        )

    _absorb(manifest.get("test01_scope"))
    for casting in manifest.get("castings", []) or []:
        if isinstance(casting, dict):
            _absorb(casting.get("test01_scope"))
    return declared




def _test01_scope_touched(fdir: Path, project_root: str, touched: list[str]) -> dict:
    """Did the GRIND diff touch a file TEST-01 covers (ST-007)?

    TEST-01 derives property tests from the spec's Contracts table and drives
    the surfaces that table names, so its scope is the modules that IMPLEMENT
    those surfaces, plus the schemas the surfaces validate against. Returns
    `{"touched": bool, "computable": bool, "source": str, "detail": str}`; on a
    match the detail names what matched, so the provenance recorded beside the
    roster can be checked rather than believed, and it is empty on a computed
    miss for the reason `_research_scope_touched` states.

    `source` names WHICH of the three sources answered — `declared`, `registry`,
    `schemas`, `no diff`, or `unknown` — so the recorded scope says where its
    answer came from and not merely what it was.

    D-207 — A COVERED SET THE SERVER CANNOT COMPUTE IS NOT AN EMPTY COVERED SET.
    ---------------------------------------------------------------------------
    D-204's fix moved this question onto the EXECUTING SERVER's own tool
    registry, which answers correctly on exactly one kind of run: one whose
    target IS this plugin. `_registry_tool_modules` yields foundry tool names
    bound to paths under the executing package's `src/foundry_mcp/`, while
    `_contracts_surface_cells` reads the TARGET run's spec. Off a self-target
    the two sides describe DIFFERENT PROGRAMS and can never intersect, so the
    only arm that could fire was the `schemas/` short-circuit — and a touched
    `schemas/` path already trips `verifier_touched` into FULL one function
    over, so in practice nothing could fire at all.

    Driven at the wire at 31cc192: a non-self-targeting run whose spec Contracts
    names `POST /api/users (create)` and `DELETE /api/users/:id`, whose casting
    key_file is `src/api/users.py`, and whose GRIND diff touched exactly that
    module recorded DELTA with `required_streams` ['trace','prove','test'] and
    `stream_scope.test01` = {scope: 'skipped', detail: 'no file test01 covers
    was touched'}. At cb77e83 the retired predicate returned True for that input
    and False for `src/api/other.py` and `src/zzz.py` — so the D-204 fix
    NARROWED a behaviour that was already correct on the non-self-target path
    while widening the self-target one. The same fail-open sat behind the
    registry's own `except ImportError -> {}`.

    THE ASYMMETRY IS THE DEFECT. The decision function one rung over fails
    CLOSED on an uncomputable GRIND diff — FULL, "the GRIND diff could not be
    computed" — and this one failed OPEN on an uncomputable covered set. ST-007,
    Locked FR-047 and AC-017 require EXACTLY the covered set; an unknown one is
    not the empty one.

    BOTH AXES, per the lead ruling. WHERE THE COVERED SET COMES FROM, in
    precedence order:

      1. `test01_scope` DECLARED by the run's own manifest, when present. The
         run's statement about its own target, true whatever the server is.
      2. otherwise the REGISTRY mapping — but only when the executing server can
         SHOW the target is this plugin, which is `state.json`'s `self_target`,
         the fact `foundry._self_target_preflight` computed and the run recorded
         at init. Recorded rather than recomputed: recomputing spawns git and
         reads plugin manifests on a per-boundary path, and it answers about NOW
         rather than about what this run was admitted on.
      3. otherwise UNKNOWN, and unknown is required, never skipped.

    WHAT AN UNKNOWN SET MEANS: `_covered_set_unknown` — required, scope
    `full`, and a detail that says the set was not computable and why, in the
    same words the uncomputable-diff arm uses. A missing key on a pre-change
    archive reads as absent and therefore as unknown, which is the fail-closed
    direction, so archive compatibility costs a stream and never a false skip.

    D-204 — A RULE ABOUT THE FACT IS NOT THE FACT, AND BOTH AXES WERE PROSE.
    ----------------------------------------------------------------------
    This asked whether the spec's Contracts SECTION — 6147 characters of prose,
    read whole — contained the touched file's basename or its stem, unanchored.
    Driven at the wire on a GRIND diff touching exactly one file, `src/a.py`:
    `Foundry-Phase('inspect_start')` returned DELTA with `test01` REQUIRED and
    the detail "a covered file was touched", because the stem `a` occurs in the
    section; `Foundry-Phase('inspect_clean')` then refused "streams incomplete:
    test01" for a stream no rule required. `src/fix.py`, `src/next.py`,
    `n/gate.py`, `lib/init.go`, `x/report.rb`, `webapp/spend.ts` and
    `tools/id.py` (the `| ID |` header) all read as covered the same way, while
    `tools/foundry_report.py` — which implements CT-014 — read as NOT covered,
    because the table spells the surface `Foundry-Report`. A predicate that
    answers yes for a file named nowhere and no for the file the row is about is
    not a narrow rule, it is a different question.

    BOTH AXES MOVE, because moving one is how this class returns. WHICH NAMES:
    the surface COLUMN's cells, parsed as table cells by
    `_contracts_surface_cells`, matched against the closed set of tool names the
    server registers — never the section's prose. HOW THEY MAP TO FILES: through
    `_registry_tool_modules`, the server's own `_DISPATCH` binding, never a stem
    search. Anchoring the cells alone would have left `src/fix.py` covered,
    since CT-004/005/006 all spell `Foundry-Fix` and `fix` is that file's stem.
    """
    if not touched:
        return {"touched": False, "computable": True, "source": "no diff", "detail": ""}
    candidates = [t for t in touched if t]

    # Source-independent and first: a `schemas/` path is inside TEST-01's scope
    # by construction, whoever the target is, because every Contracts surface
    # validates against the schemas. No registry and no declaration is consulted
    # to know that, so nothing about the ladder below can make it unknown.
    schema_hits = [t for t in candidates if "schemas/" in f"/{t}"]
    if schema_hits:
        return {
            "touched": True,
            "computable": True,
            "source": "schemas",
            "detail": (
                f"{schema_hits[0]} is a schemas/ file, which every Contracts "
                "surface validates against"
            ),
        }

    # (1) The run's own declaration wins outright when it exists — it describes
    # THIS run's target, so neither the executing server's identity nor its
    # registry is consulted, and a miss against it is a computed miss.
    declared = _declared_test01_scope(fdir)
    if declared:
        for covered in declared:
            for candidate in candidates:
                if _path_matches(covered, candidate):
                    return {
                        "touched": True,
                        "computable": True,
                        "source": "declared",
                        "detail": (
                            f"the run's manifest declares test01_scope "
                            f"{covered}, and the diff touched {candidate}"
                        ),
                    }
        return {"touched": False, "computable": True, "source": "declared", "detail": ""}

    # (2) The registry, and ONLY on a run this server can show it is the target
    # of. Off a self-target the registry describes a different program than the
    # spec does, so an empty intersection is evidence of nothing.
    if _load_json(fdir / "state.json").get("self_target") is not True:
        return _covered_set_unknown(
            "test01",
            "the run's manifest declares no test01_scope and state.json does "
            "not record self_target, so the executing server's tool registry "
            "describes a different program than the one under test"
        )

    rows, spec_problem = _contracts_surface_cells(project_root)
    if spec_problem is not None:
        return _covered_set_unknown(
            "test01",
            f"the run's Contracts table could not be read ({spec_problem})"
        )
    if not rows:
        # READ, and it names no surfaces. TEST-01 itself SKIPs with a reason on
        # such a spec, so this is a genuinely empty covered set — the one case
        # the D-207 sentence does NOT reach.
        return {"touched": False, "computable": True, "source": "registry", "detail": ""}

    registry = _registry_tool_modules()
    if not registry:
        return _covered_set_unknown(
            "test01",
            "the executing server's tool registry could not be read, so which "
            "module implements a named surface is unknown"
        )

    for row_id, surface in rows:
        for tool in sorted(registry):
            if not re.search(
                rf"(?<![A-Za-z0-9_-]){re.escape(tool)}(?![A-Za-z0-9_-])", surface
            ):
                continue
            for module_file in registry[tool]:
                for candidate in candidates:
                    if _path_matches(module_file, candidate):
                        return {
                            "touched": True,
                            "computable": True,
                            "source": "registry",
                            "detail": (
                                f"{row_id} names surface {tool}, which "
                                f"{_repo_relative(project_root, Path(module_file))} "
                                f"implements, and the diff touched {candidate}"
                            ),
                        }
    return {"touched": False, "computable": True, "source": "registry", "detail": ""}




#: WIRE ID -> the predicate that answers "did the diff touch a file this
#: stream covers". Every member of `DELTA_CONDITIONAL_STREAMS` (casting 1's
#: vocabulary) belongs here, under ONE signature — `(fdir, project_root,
#: touched)` — so `_delta_conditional_scope` can call any of them without
#: knowing which it is holding. `_research_scope_touched` does not read
#: `project_root`; taking it anyway is the price of there being one signature
#: rather than one call site per stream, and it is a price worth paying: an
#: `if wire == "research_audit" else` ladder in the caller is exactly where the
#: two arms drifted into different answer shapes for three cycles.
_DELTA_CONDITIONAL_PREDICATES = {
    "research_audit": _research_scope_touched,
    "test01": _test01_scope_touched,
}




def _delta_conditional_scope(
    fdir: Path, project_root: str, wire: str, touched: list[str]
) -> dict:
    """The ONE path every conditional stream's answer comes through (ST-007).

    Returns the shared three-valued answer — `{"touched", "computable",
    "source", "detail"}` — for any member of `DELTA_CONDITIONAL_STREAMS`, and
    routes every way of NOT having an answer to the same place:
    `_covered_set_unknown`, which is `touched: True` and therefore REQUIRED
    with a detail naming why. Never raises, so a malformed arm degrades the
    width to "run it" rather than taking the transition down.

    D-208 — THE STRUCTURAL FIX. THE CLASS WAS NEVER ABOUT ONE PREDICATE.
    -------------------------------------------------------------------
    `inspect-mode-rule-is-a-proxy-not-the-fact` recurred at cycles 3, 5, 18, 19
    and 20 and was escalated for three consecutive cycles. Each instance was a
    conditional arm answering a TWO-VALUED question — touched / not touched —
    about a covered set it had derived by proxy (D-204: a substring search of
    the spec's prose; D-207: the executing server's own tool registry, which
    describes a different program off a self-target) or could not derive at all
    (D-208: `castings/manifest.json` absent, read as a manifest declaring no
    research). Each fix repaired ONE arm. The lead's ruling names the root
    cause the three share: the answer SHAPE was per-arm, so a lesson taught to
    one arm reached no other, and the caller's `if wire == ... else ...` ladder
    let two arms of one branch disagree about how many answers the question
    has.

    BOTH AXES, and this function is the second of them:

      WHAT an arm answers  three values, never two. `touched` is the width
                           decision, `computable` says whether it was derived
                           or defaulted, `source` names which source derived
                           it, `detail` is the sentence recorded into
                           `state.json.inspect_modes` and `stream-rollup.json`.
      HOW it is shared     this function. Every arm is reached through the
                           `_DELTA_CONDITIONAL_PREDICATES` registry under one
                           signature, and its answer is CHECKED against
                           `_CONDITIONAL_ANSWER_KEYS` before the caller reads
                           `touched` out of it. So a fourth conditional stream
                           added to the vocabulary with no predicate, or with a
                           two-valued one, is REQUIRED with an honest detail —
                           it cannot be silently skipped on a negative nobody
                           computed, which is the only failure this class has
                           ever had.

    A stream registered here but absent from `DELTA_CONDITIONAL_STREAMS` is
    simply never asked; the vocabulary decides which streams are conditional,
    this registry decides how each is answered, and neither is derived from the
    other.
    """
    predicate = _DELTA_CONDITIONAL_PREDICATES.get(wire)
    if predicate is None:
        return _covered_set_unknown(
            wire,
            f"{wire} is a conditional stream with no registered predicate in "
            "this server, so what it covers is not something this server can "
            "state",
        )
    try:
        answer = predicate(fdir, project_root, touched)
    except Exception as exc:  # pragma: no cover - defensive; see the house rule
        # The house rule is that a tool never raises across the MCP boundary.
        # An arm that raises has not answered, and an unanswered question is
        # the unknown, not the negative.
        return _covered_set_unknown(
            wire, f"its predicate raised {type(exc).__name__}"
        )
    if not isinstance(answer, dict):
        return _covered_set_unknown(
            wire,
            f"its predicate answered with a {type(answer).__name__} rather "
            "than the shared conditional answer",
        )
    missing = [k for k in _CONDITIONAL_ANSWER_KEYS if k not in answer]
    if missing:
        return _covered_set_unknown(
            wire,
            "its predicate answered without "
            f"{', '.join(missing)} — the shared conditional answer is "
            f"{', '.join(_CONDITIONAL_ANSWER_KEYS)}, and an arm short of it "
            "has not said whether its covered set was computed at all",
        )
    return answer




def _prove_delta_sample(
    fdir: Path, project_root: str, cycle: int, fixed_in_cycle: int | None = None
) -> dict:
    """The requirement rows PROVE checks on a DELTA cycle (FR-013 / AC-018).

    The rows tied to the defects the preceding GRIND fixed — those are the rows
    whose verdicts the fixes could have changed — plus up to
    `PROVE_DELTA_SAMPLE_SIZE` more, drawn from the sorted remainder with
    `random.Random(cycle)`.

    Returns `{"rows", "tied", "sampled"}`: the roster and the two halves it was
    built from, `rows == tied + sampled`.

    D-177 — THE HALVES ARE RETURNED BECAUSE THE CALLER STATES THEM.
    --------------------------------------------------------------
    This returned the concatenated list alone, so `_decide_inspect_mode` — the
    one caller — had the roster and no way to say how it was made. It wrote the
    breakdown as f"{len(prove_sample)} row(s): rows tied to the fixed defects
    plus {PROVE_DELTA_SAMPLE_SIZE} sampled", interpolating the CONSTANT as
    though it were the measurement. The draw is `min(PROVE_DELTA_SAMPLE_SIZE,
    len(remaining))`, so the two clauses of that one sentence disagree by
    construction whenever the remaining pool is smaller than the ceiling.
    Driven end to end on a two-requirement spec with no fixed-defect rows:
    `prove_sample` recorded ['FR-001', 'FR-002'] and the stream_scope detail
    beside it read "2 row(s): rows tied to the fixed defects plus 10 sampled".
    That string is recorded into state.json and stream-rollup.json and is read
    back by the PROVE stream as its width statement, so a stream that trusts
    the breakdown looks for eight rows that were never drawn.

    TWO CYCLE NUMBERS, DELIBERATELY. ``cycle`` is the cycle this roster is FOR
    and is the seed; ``fixed_in_cycle`` is the cycle whose GRIND just ended and
    is where the tied rows come from. At the `inspect_start` boundary those
    differ by one — the counter has already been advanced for the INSPECT about
    to run, while "the defects fixed in the preceding GRIND" are stamped with
    the cycle that just closed. Seeding from one and selecting from the other is
    the whole of AC-018's sentence; collapsing them to a single number selects
    rows tied to a GRIND that has not happened, which is silently empty rather
    than wrong-looking. Defaults to ``cycle`` when the caller has only one.

    REPRODUCIBLE FROM THE CYCLE NUMBER ALONE (FR-033). The seed is ``cycle`` and
    nothing else, and the population is SORTED before the draw, so nothing
    depends on dict ordering, ledger order or filesystem order. Two calls in the
    same cycle return the same ten rows because the same seed draws from the
    same sequence — and the recorded roster means no caller ever draws twice
    anyway (AC-018 / OT-015).

    The sample is a floor on coverage, not a ceiling on it: a DELTA cycle checks
    what the fixes could have broken plus a random slice of everything else, so
    a regression outside the fixed rows is still found, just not in one cycle.
    """
    # fallout FR-004 / GI-033 -- LAZY SEAM, written once per symbol.
    # `gates` import(s) this module, so a module-top import here would
    # close a cycle that takes every tool in this server down at once.
    # Unguarded, so a wiring break fails loudly at the one call site that
    # needs the symbol rather than hiding behind a silent fallback.
    from foundry_mcp.tools.orchestration.gates import _REQ_ID_RE, _spec_requirement_ids
    import random

    all_rows = sorted(set(_spec_requirement_ids(project_root)))
    if not all_rows:
        return {"rows": [], "tied": [], "sampled": []}

    fixed_rows: set[str] = set()
    for d in _load_json(fdir / "defects.json").get("defects", []):
        if not isinstance(d, dict) or d.get("status") != "fixed":
            continue
        if d.get("fixed_in_cycle") != (
            cycle if fixed_in_cycle is None else fixed_in_cycle
        ):
            continue
        ref = d.get("spec_ref")
        if isinstance(ref, str):
            fixed_rows.update(_REQ_ID_RE.findall(ref))

    tied = sorted(fixed_rows & set(all_rows))
    remaining = [r for r in all_rows if r not in set(tied)]
    # The ceiling, applied. `draw` is the number actually taken and is what the
    # caller states; `PROVE_DELTA_SAMPLE_SIZE` is the most it can be.
    draw = min(PROVE_DELTA_SAMPLE_SIZE, len(remaining))
    sampled = sorted(random.Random(cycle).sample(remaining, draw)) if draw else []
    return {"rows": tied + sampled, "tied": tied, "sampled": sampled}




def _base_required_streams(project_root: str) -> list[str]:
    """`sight` and `probe`, which are per-run facts rather than width facts.

    Both conditions are unchanged from what `_check_streams_complete` has always
    applied, and both hold at FULL and DELTA alike: a run with frontend files in
    scope needs SIGHT however narrow the diff, and a run with a target_url needs
    PROBE. Width decides how much of the spec PROVE reads, never whether the run
    has a UI.
    """
    fdir = get_run_dir(project_root)
    extra: list[str] = []
    if fdir and _check_sight_required(project_root).get("required"):
        extra.append("sight")
    if fdir and _load_json(fdir / "castings" / "manifest.json").get("target_url"):
        extra.append("probe")
    return extra




def _decide_inspect_mode(
    fdir: Path,
    project_root: str,
    *,
    decided_by: str,
    phase: str,
    cycle: int,
    widening: bool = False,
) -> dict:
    """Compute one `state.json.inspect_modes` entry. Writes nothing.

    ``decided_by`` is the phase token that performed the transition — `cast`,
    `temper` or `inspect_start` — and it is recorded, so "which crossing decided
    this width" is answerable from the artifact.

    THE RULES, EVALUATED IN ORDER (FR-011):

      first_of_phase    the phase-entry transition into F2 or F5. The first
                        INSPECT of a phase has no previous INSPECT to be a delta
                        FROM, so it is FULL by construction rather than by
                        policy.
      final_gate        this INSPECT precedes ASSAY, NYQUIST or DONE. The gates
                        that end a run are never handed a narrow answer. TWO
                        ways to be that INSPECT, and only two: the GRIND that
                        just closed was entered from ASSAY / TEMPER / NYQUIST
                        feedback, or this crossing is the F2->F2 WIDENING
                        re-open the lead makes to open ASSAY (``widening``).
      verifier_touched  the GRIND diff moved the machinery that JUDGES the
                        build — vocab, schemas, gate/orchestrator code, agent or
                        skill prose, or the spec. A delta roster is only as
                        trustworthy as the verifier it runs, so when the
                        verifier itself moved, nothing narrower than everything
                        is honest.
      delta             none of the above.

    D-068 — `blocking == 0` WAS THE final_gate TEST, AND IT MADE DELTA
    UNREACHABLE.
    -----------------------------------------------------------------
    The arm read `elif _blocking_defects(fdir)["blocking"] == 0`, which is true
    after ANY GRIND that fixed what INSPECT filed — which is exactly the state
    `_compute_next_action`'s F3 branch instructs the lead to reach before
    crossing ("GRIND complete: all defects fixed ... then
    Foundry-Phase(phase='inspect_start')"). So the ordinary cycle recorded
    FULL / final_gate every time and US-004 delivered nothing. Driven, cycle 1
    to 2, one-file handler diff: cleared ledger -> FULL/final_gate; empty ledger
    -> FULL/final_gate; a LATENT-only backlog -> FULL/final_gate; only an OPEN
    LIVE defect yielded DELTA. The shipped fixture conceded it in its own
    docstring — "One OPEN LIVE defect — enough to keep final_gate from firing.
    Every DELTA fixture needs this." — i.e. DELTA fired only when the lead
    crossed with LIVE defects still open, which no guidance instructs and which
    `Foundry-Gate('assay')` refuses anyway.

    LEAD RULING, GRIND cycle 4 (SPEC_AMBIGUOUS, run state.json entry 4): "the
    next gate is ASSAY" is not a fact about the DEFECT LEDGER, it is a fact
    about the TRANSITION. A DELTA INSPECT that comes back clean does not open
    ASSAY; it earns the right to re-open INSPECT at full width, and THAT
    crossing is the final gate. So `blocking == 0` is no longer a condition
    here, `_entered_grind_from_feedback` and `widening` are, and an ordinary
    GRIND that fixed everything now yields DELTA — which is the whole of
    AC-016's "and DELTA otherwise".

    D-069 — PRECEDENCE, SO THE RECORDED RULE NAME IS TRUE. The final_gate arm
    sat as an `elif` ahead of the `is_verifier_path` scan, so with a
    schemas/vocab.py diff and a cleared ledger it recorded
    `final_gate` / "no blocking defects remain" and the verifier scan never
    ran — OT-013's second half ("after one whose diff touches schemas/vocab.py
    it records FULL with rule verifier_touched") was unreachable and the F6
    report's per-cycle rule column was false. The mode was still FULL, so no
    verification was lost; the PROVENANCE was wrong, which is what FR-011's
    "Foundry-Next names which rule fired" is about. With `blocking` gone from
    the condition, final_gate now fires only on the two transition facts above,
    and an ordinary verifier-touching GRIND reaches the scan and records it.

    An UNKNOWN diff (git unavailable, no baseline to measure from) is FULL, and
    is recorded as `verifier_touched` with the real cause in ``rule_detail``.
    That is the closest member of the closed `INSPECT_FULL_RULES` vocabulary and
    the reason is the same one: nothing can show the verifier did NOT move, so
    the honest width is everything. See the concerns file — the vocabulary has
    no member meaning "the diff could not be computed", and inventing one here
    would be a seventh copy of a vocabulary casting 1 owns.

    D-152 — AND THE UNKNOWN-DIFF ARM IS EVALUATED LAST OF THE FULL ARMS, SO IT
    SUBSTITUTES FOR NO RULE THAT ACTUALLY FIRED.
    -------------------------------------------------------------------------
    That arm sat AHEAD of both `final_gate` arms, and the two facts they read —
    `widening`, and the phase history — are known whether or not a diff can be
    computed. So a GRIND entered from ASSAY feedback on a run whose run dir
    carries no `.inspect-boundary-sha`, `.trace-clean-at` or `.cast-baseline-sha`
    recorded `verifier_touched` with `rule_detail` "the GRIND diff could not be
    computed ... so the verifier cannot be shown to be untouched" — for the very
    cycle that opens ASSAY, whose own gate refused, at the time, by naming the
    recorded rule rather than the recorded width. (That refusal has since been
    corrected — D-169: both ASSAY doors read the MODE and always did, so a
    sentence turning on the rule was false about every FULL cycle the rule did
    not happen to name. It is quoted here only as the state of the tree D-152
    was filed against.) Driven at cycle 8 on a
    phase_history of F2 -> F4 -> F3; the same substitution occurs on the F2->F2
    widening re-open and wherever git is unavailable. The mode is FULL either
    way, so no verification is lost; what was wrong is the PROVENANCE, which is
    the whole of FR-011's "Foundry-Next names which rule fired" and which the F6
    report carries in its per-cycle rule column.

    This is D-069's failure shape one arm over — the same lesson, that a FULL
    arm placed ahead of another does not merely decide the width, it decides
    what the artifact SAYS decided the width. The order is therefore fixed as
    the lead ruling states it and as the docstring above lists it:
    first_of_phase, then BOTH final_gate facts, then verifier_touched (the
    uncomputable diff, then the scan), then DELTA. An uncomputable diff still
    yields FULL; it just no longer speaks over a rule that fired.
    """
    now = now_iso()
    diff = _grind_diff(fdir, project_root)
    touched = diff["files"]
    skips = _skipped_streams(fdir)
    research_skipped = _research_skipped(fdir)

    rule = ""
    rule_detail = ""
    if decided_by in ("cast", "temper"):
        rule = "first_of_phase"
        rule_detail = f"phase-entry transition into {phase}"
    elif widening:
        rule = "final_gate"
        rule_detail = (
            "the F2->F2 widening re-open: the preceding DELTA INSPECT came back "
            "clean, so this crossing is the INSPECT before ASSAY"
        )
    elif _entered_grind_from_feedback(fdir):
        rule = "final_gate"
        rule_detail = "this GRIND was entered from ASSAY, TEMPER or NYQUIST feedback"
    elif diff["problem"]:
        # D-152: LAST of the FULL arms. Both final_gate facts above are known
        # without a diff, so consulting the diff first substituted
        # `verifier_touched` for a rule that had already fired.
        rule = "verifier_touched"
        rule_detail = (
            f"the GRIND diff could not be computed ({diff['problem']}), so the "
            "verifier cannot be shown to be untouched"
        )
    else:
        # D-102: EVERY spelling of the run's spec, not just the one
        # `_resolve_spec_path` prefers. See `_spec_relative_paths`.
        spec_spellings = _spec_relative_paths(project_root) or [None]
        hits = [
            f for f in touched
            if any(is_verifier_path(f, spec) for spec in spec_spellings)
        ]
        if hits:
            rule = "verifier_touched"
            rule_detail = (
                f"{len(hits)} verifier file(s) changed: {', '.join(hits[:5])}"
            )

    full = bool(rule)
    if not rule:
        rule = INSPECT_DELTA_RULE

    scope: dict[str, dict] = {}
    required: list[str] = []
    if full:
        for wire in FULL_ROSTER_STREAMS:
            if wire in skips:
                scope[wire] = {"scope": "skipped", "detail": "manifest.stream_skips"}
                continue
            if wire == "research_audit" and research_skipped:
                scope[wire] = {"scope": "skipped", "detail": "research_skipped record"}
                continue
            required.append(wire)
            scope[wire] = {"scope": "full", "detail": "every item in scope"}
        prove_sample: list[str] = []
    else:
        # D-208: EVERY conditional arm is answered through one path, before the
        # roster loop, so the loop below selects an answer by wire id rather
        # than naming each stream and its predicate in one breath. That naming
        # is where the two arms drifted into different answer shapes.
        conditional = {
            wire: _delta_conditional_scope(fdir, project_root, wire, touched)
            for wire in sorted(DELTA_CONDITIONAL_STREAMS)
        }
        # The GRIND that just ended is `cycle - 1` on the `inspect_start`
        # boundary, where the counter has already been advanced for the INSPECT
        # this roster is for.
        prove_draw = _prove_delta_sample(
            fdir, project_root, cycle, fixed_in_cycle=max(0, cycle - 1)
        )
        prove_sample = prove_draw["rows"]
        for wire in FULL_ROSTER_STREAMS:
            if wire in skips:
                scope[wire] = {"scope": "skipped", "detail": "manifest.stream_skips"}
                continue
            if wire in DELTA_CONDITIONAL_STREAMS:
                # ST-007: required ONLY when the diff touches a file they cover.
                decision = conditional[wire]
                if wire == "research_audit" and research_skipped:
                    # A RECORDED skip is a computed fact about the run and
                    # outranks anything the covered set says, an unknown one
                    # included — the run has no research to audit against
                    # whatever moved. Stated in its own words: "no file
                    # research_audit covers was touched" would be a reason this
                    # branch never evaluated, and D-208 is precisely what
                    # recording an unevaluated reason costs. Same sentence the
                    # FULL branch writes for the same fact.
                    scope[wire] = {
                        "scope": "skipped",
                        "detail": "research_skipped record",
                    }
                    continue
                if not decision["touched"]:
                    scope[wire] = {
                        "scope": "skipped",
                        "detail": f"no file {wire} covers was touched",
                    }
                    continue
                required.append(wire)
                # D-204: the provenance NAMES what matched. This recorded the
                # bare claim "a covered file was touched", which is persisted
                # into state.json's inspect_modes and stream-rollup.json and
                # read back by the F6 per-cycle scope column — so when the
                # predicate answered yes for a file named nowhere in the spec,
                # nothing downstream could tell that from a real hit.
                #
                # D-207: `full` here now covers TWO things, and the detail is
                # what separates them — a computed hit naming what matched, or a
                # covered set the server could not compute at all, naming why.
                # Both are REQUIRED, which is the whole point: an unknown set
                # fails closed exactly as an uncomputable GRIND diff does. The
                # detail is the only place that distinction is written down, so
                # it is copied through verbatim rather than re-summarised.
                #
                # D-208: and BOTH arms now reach this line the same way — the
                # answer is selected out of `conditional` by wire id, so
                # whichever stream could not compute its covered set writes the
                # same shaped sentence here, and neither can reach the skip
                # above on a negative it never computed.
                scope[wire] = {"scope": "full", "detail": decision["detail"]}
                continue
            required.append(wire)
            if wire == "test":
                # AC-019: TEST runs FULL and COLD on a DELTA cycle. A narrowed
                # suite cannot see a regression the GRIND opened in a module it
                # did not edit, and that is the exact failure a delta INSPECT is
                # most exposed to.
                scope[wire] = {
                    "scope": "full",
                    "detail": "whole suite, cold, from a clean worktree",
                }
            elif wire == "trace":
                scope[wire] = {
                    "scope": "delta",
                    "detail": f"symbols in the {len(touched)} file(s) the GRIND touched",
                }
            else:
                # D-177: BOTH numbers are measured. This interpolated
                # `PROVE_DELTA_SAMPLE_SIZE` — the ceiling — as though it were
                # the count drawn, so on any spec whose remaining pool is
                # smaller than the constant the sentence contradicted the
                # roster printed beside it ("2 row(s) ... plus 10 sampled").
                # The draw is `min(ceiling, len(remaining))`, and the caller
                # states what the draw returned.
                scope[wire] = {
                    "scope": "delta",
                    "detail": (
                        f"{len(prove_sample)} row(s): "
                        f"{len(prove_draw['tied'])} tied to the fixed defects "
                        f"plus {len(prove_draw['sampled'])} sampled"
                    ),
                }

    for wire in _base_required_streams(project_root):
        if wire in skips or wire in required:
            continue
        required.append(wire)
        scope[wire] = {"scope": "full", "detail": "required by this run's manifest"}

    return {
        "cycle": cycle,
        "phase": phase,
        "mode": "FULL" if full else "DELTA",
        "rule": rule,
        "rule_detail": rule_detail,
        "decided_by": decided_by,
        "decided_at": now,
        "required_streams": required,
        "stream_scope": scope,
        "touched_files": touched,
        "prove_sample": prove_sample,
        "diff_base": diff["base"],
    }




def _entered_grind_from_feedback(fdir: Path) -> bool:
    """Was the GRIND that just ended entered from ASSAY / TEMPER / NYQUIST?

    Read from `state.json.phase_history`, which `_update_phase` appends to on
    every transition, rather than from a marker this would otherwise have to
    invent: the history already records exactly the fact being asked about, and
    a second record of it is a second thing that can drift.

    The walk is backwards from the end, skipping the F3 entries themselves, and
    the first non-F3 phase found is the one the GRIND was entered from. F4, F5
    and F5.5 all mean the same thing here — a gate rejected the run and sent it
    back — and the INSPECT that follows that GRIND is the one those gates will
    read next, so it runs at full width.
    """
    history = _load_json(fdir / "state.json").get("phase_history", [])
    if not isinstance(history, list):
        return False
    for entry in reversed(history):
        if not isinstance(entry, dict):
            continue
        phase = entry.get("phase")
        if phase == "F3":
            continue
        return phase in ("F4", "F5", "F5.5")
    return False




#: D-070 — the sub-bucket the F5 (TEMPER) entry's decision is recorded under.
#: The server cycle counter does NOT advance entering F5, so the temper
#: transition lands in the same `cycles[<cycle>]` bucket as the `inspect_start`
#: that opened that cycle. Its own key is what keeps both records.
TEMPER_ENTRY_ROLLUP_KEY = "temper_entry"



#: D-133 — and the same reasoning one crossing later. Entering F5.5 does not
#: advance the counter either, so the NYQUIST boundary's whole-corpus sweep is
#: recorded under its own key rather than overwriting the temper entry's.
NYQUIST_ENTRY_ROLLUP_KEY = "nyquist_entry"




def _record_cycle_rollup(fdir: Path, cycle: int, *, sub: str = "", **fields) -> None:
    """Write CYCLE-level facts into `stream-rollup.json` (C-6).

    Sibling of `_record_stream_rollup`, not an extension of it. That function
    accumulates one STREAM's tranches inside `cycles[<cycle>][<stream>]`; these
    keys — `inspect_mode`, `inspect_rule`, `stream_scope`, `evidence_sweep` —
    describe the cycle itself and sit beside the stream buckets rather than
    inside one. Widening the stream writer with cycle-level kwargs would give
    one function two jobs and make `cycles[<cycle>]["inspect_mode"]` look, to
    every existing reader, like a stream called `inspect_mode`.

    ``sub`` NESTS THE WRITE, AND EXISTS FOR EXACTLY ONE CALLER (D-070).
    ------------------------------------------------------------------
    This keyed the bucket by `str(cycle)` and did `bucket.update(fields)`, and
    the F5 entry decides its mode with `cycle=current_cycle(fdir)` — a counter
    that does NOT advance entering F5. So the temper decision landed in the
    same bucket as the last `inspect_start` and OVERWROTE it. Driven:
    `inspect_start` recorded cycle 2 as DELTA/delta; after
    `Foundry-Phase('temper')`, `cycles['2']` read FULL/first_of_phase and the
    DELTA `stream_scope` was gone. CT-009 requires the decision recorded "in
    state AND stream-rollup at the transition"; the state list survived because
    it is append-only, but any reader taking the roll-up as the per-cycle width
    — the F6 report's cycle table among them — saw a fabricated FULL for a
    cycle that ran DELTA.

    The F5 entry now writes under `TEMPER_ENTRY_ROLLUP_KEY` and both records
    survive. A nested bucket rather than a `"<cycle>:F5"` sibling key, so
    `cycles` stays keyed by cycle number alone and no existing reader has to
    learn a second key grammar.

    Shares the same `_document_transaction`, which is the part that matters:
    D-103's concurrency site is this file, and a second unlocked writer of it
    would lose records exactly as the unlocked stream writer did.
    """
    with _document_transaction(fdir / ROLLUP_FILENAME) as data:
        cycles = data.setdefault("cycles", {})
        if not isinstance(cycles, dict):
            cycles = data["cycles"] = {}
        bucket = cycles.setdefault(str(cycle), {})
        if not isinstance(bucket, dict):
            bucket = cycles[str(cycle)] = {}
        if sub:
            nested = bucket.setdefault(sub, {})
            if not isinstance(nested, dict):
                nested = bucket[sub] = {}
            bucket = nested
        bucket.update(fields)
        data["updated_at"] = now_iso()




#: FR-036 (Flexible) — how long a gap between Foundry-Next calls has to be
#: before the watchdog says anything at all. Unchanged from the 180 seconds this
#: has always used: the threshold was never the defect, the accusation was.
#: A gap this long is worth REMARKING on either way; what changed is that the
#: remark now depends on whether an agent is actually running.
STALL_NOTICE_SECONDS = 180




def _waiting_on_agents(project_root: str) -> dict:
    """Is the lead waiting on live agents, or is it deliberating (FR-020)?

    Returns ``{"waiting": bool, "count": int, "detail": str, "agents": [...]}``.

    BOTH DECLARED INPUTS ARE READ; PROGRESS IS WHAT DECIDES (D-076, D-127).
    ----------------------------------------------------------------------
    CT-012 declares this check's inputs as ".last-next-at, ACTIVE TEAMS,
    Foundry-Liveness roster"; FR-020 (Locked, verbatim) reads "Before accusing,
    Foundry-Next checks ACTIVE TEAMS AND Foundry-Liveness ... IF AGENTS ARE
    RUNNING it reports 'waiting on N agents'". Both sources are read. What they
    are read FOR is the question the two defects here disagreed about.

    D-076 removed the team scan entirely and the suite asserted its own absence
    with an AST walk, so a declared contract input had a test guarding the fact
    that nothing consulted it. The GRIND cycle-4 repair restored the scan and
    ANDed it: waiting required a registered ACTIVE team **and** a progressing
    ledger.

    D-127 — THE AND ACCUSED THE LEAD WHILE ITS OWN LIVENESS READ SHOWED AGENTS
    WORKING. The F2 INSPECT streams are background Agents, never tmux
    teammates, so `_check_active_teams` cannot see them — the cycle-4 comment
    recorded that as a "known consequence" and the consequence is the defect.
    Driven: no registered team, two progress ledgers written seconds earlier,
    `.last-next-at` 600s old -> `waiting` False, `progressing_agents` 2, and
    Foundry-Next emitted `stall_detected_seconds` 600 beside the notice "NO
    agent is running. You were silently deliberating" — asserting deliberation
    over two agents the same call had just measured progressing. FR-020 is
    Locked and FR-036's proviso is that the notice NEVER asserts deliberation
    while an agent is progressing.

    LEAD RULING, GRIND cycle 7 (state.json `spec_ambiguities` entry 7,
    superseding the cycle-4 AND where they conflict): "if agents are running"
    is decided by EVIDENCE OF PROGRESS.

      * a progressing ledger, no registered team  -> WAITING   (D-127)
      * a registered but DEAD team, no ledger     -> STALL     (D-021)
      * neither                                    -> STALL     (AC-032 arm 2)

    So a progressing roster is SUFFICIENT whether or not a team is registered,
    and a registered team is never sufficient on its own. AC-032's "with no
    active teams it reports the stall" means no RUNNING AGENTS by either input,
    which is what the roster measures. D-021's cause stays closed for the same
    reason it was closed: a stale team directory alone still cannot suppress
    the warning, because the ledgers are what answer.

    `teams_active` is still read and still REPORTED on the result, so CT-012's
    input set is unchanged and a caller can still tell a registered team from a
    progressing one.

    NEVER RAISES AND NEVER BLOCKS (CT-012). Every failure path answers "not
    waiting", which degrades to the pre-change behaviour — the watchdog warns —
    rather than to silence. A liveness reader that cannot answer must not be
    able to suppress a real stall warning.
    """
    result = {"waiting": False, "count": 0, "detail": "", "agents": []}

    # CT-012's second declared input. Read FIRST and never raised through: a
    # scan that cannot answer must not be able to suppress a stall warning
    # either, so an unusable answer reads as "no active team".
    try:
        teams = _check_active_teams(project_root)
    except Exception:  # noqa: BLE001 - a watchdog never raises into its caller
        teams = {"active": False, "teams": []}
    teams_active = bool(teams.get("active"))

    try:
        from foundry_mcp.tools.foundry_spawn import (
            STATUS_NO_PROGRESS,
            STATUS_PROGRESSING,
            foundry_liveness,
        )

        liveness = foundry_liveness(None, None, project_root=project_root)
    except Exception:  # noqa: BLE001 - a watchdog never raises into its caller
        liveness = {"ok": False}

    live_agents = []
    if liveness.get("ok"):
        for row in liveness.get("agents", []) or []:
            if not isinstance(row, dict):
                continue
            # PROGRESSING and NO_PROGRESS both mean lines are still ARRIVING;
            # they differ only in whether the `step` field moved. STALLED means
            # no line at all for the threshold, DONE means finished, and
            # NO_LEDGER / UNKNOWN mean there is no evidence — none of which is
            # an agent to wait for.
            if row.get("status") in (STATUS_PROGRESSING, STATUS_NO_PROGRESS):
                live_agents.append(row)

    # D-127 / FR-020, stated once: PROGRESS decides. An EMPTY roster is the one
    # answer that lets the watchdog speak. A registered team with nothing
    # progressing is D-021's stale directory rather than an agent to wait for,
    # and it can no longer suppress the warning because it never reaches past
    # this line on its own.
    if not live_agents:
        result["teams_active"] = teams_active
        result["progressing_agents"] = 0
        return result

    # FR-036: the oldest progress age is what the lead actually needs — the
    # agent least recently heard from is the one that decides whether this
    # wait is healthy.
    ages = [
        row.get("last_progress_age_seconds", 0)
        for row in live_agents
        if isinstance(row.get("last_progress_age_seconds"), int)
    ]
    oldest = max(ages) if ages else 0
    result.update({
        "waiting": True,
        # D-127: REPORTED, not asserted. This used to be the literal `True` the
        # AND had already proved; waiting no longer implies a registered team,
        # so the field carries what the scan actually answered and CT-012's
        # input set stays visible to every reader.
        "teams_active": teams_active,
        "progressing_agents": len(live_agents),
        "count": len(live_agents),
        "detail": f"oldest progress {oldest // 60}m {oldest % 60}s ago",
        "agents": [
            {"agent": r.get("agent"), "status": r.get("status"),
             "step": r.get("step")}
            for r in live_agents
        ],
        "oldest_progress_seconds": oldest,
    })
    return result




def _note_fix_after_inspect_decision(fdir: Path, defect_id: str) -> None:
    """Mark the open INSPECT's recorded width as superseded by a fix (D-035).

    D-035 — A DELTA-SWEPT INSPECT COULD OPEN ASSAY.
    ----------------------------------------------
    `foundry_mark_defect_fixed` has no phase guard, so a fix landing while the
    run sits in F2 flips the blocking count to zero AFTER the width was already
    decided and the sweep already taken. `inspect_clean` then passes and ASSAY
    opens on a cycle whose sweep never covered the surface that fix changed —
    the one crossing GI-002 exists to make honest.

    Recorded rather than refused. The fix itself is legitimate work and
    refusing it would push the lead to fix the defect and not say so, which is
    strictly worse. What is not legitimate is CARRYING that cycle's decision
    forward as though it still described the tree, so the entry is stamped and
    `inspect_clean` refuses until a fresh `inspect_start` re-decides the width
    and re-sweeps at the new HEAD.

    A no-op outside F2: in F3 GRIND, which is where fixes normally land, the
    next `inspect_start` decides a width that already accounts for them.
    """
    state_path = fdir / "state.json"
    with _document_transaction(state_path) as state:
        if state.get("phase") != "F2":
            return
        modes = state.get("inspect_modes")
        if not isinstance(modes, list) or not modes:
            return
        current = modes[-1]
        if not isinstance(current, dict):
            return
        superseded = current.get("fixes_after_decision")
        if not isinstance(superseded, list):
            superseded = []
        if defect_id not in superseded:
            superseded.append(defect_id)
        current["fixes_after_decision"] = superseded
        state["inspect_modes"] = modes
        state["updated_at"] = now_iso()




def _record_inspect_mode(fdir: Path, entry: dict) -> None:
    """Append one decision to `state.json.inspect_modes` and mirror it (C-4/C-6).

    Used by the two PHASE-ENTRY transitions. `inspect_start` does the same work
    inline instead, because it already holds the state transaction open for the
    counter advance and opening a second one would reintroduce exactly the
    read-modify-write window D-103 closed.

    D-070: the F5 entry mirrors into its own sub-bucket, because the counter
    does not advance entering F5 and a flat write would overwrite the preceding
    INSPECT's row. `state.json.inspect_modes` is append-only and already kept
    both; the roll-up now does too.
    """
    with _document_transaction(fdir / "state.json") as state:
        modes = state.get("inspect_modes")
        if not isinstance(modes, list):
            modes = []
        modes.append(entry)
        state["inspect_modes"] = modes
        state["updated_at"] = now_iso()
    _record_cycle_rollup(
        fdir,
        entry["cycle"],
        sub=_rollup_sub_for(entry),
        inspect_mode=entry["mode"],
        inspect_rule=entry["rule"],
        stream_scope=entry["stream_scope"],
    )




def _rollup_sub_for(entry: dict) -> str:
    """The `cycles[<cycle>]` sub-bucket one decision is mirrored under (D-070).

    Empty — a flat write — for every crossing that OWNS its cycle number: the
    `cast` entry (the run's first INSPECT) and `inspect_start` (which advanced
    the counter for the INSPECT it is opening). Only the F5 entry shares a
    cycle number with a decision already recorded, so only it nests.
    """
    return (
        TEMPER_ENTRY_ROLLUP_KEY if entry.get("decided_by") == "temper" else ""
    )




def _current_inspect_mode(fdir: Path, cycle: int | None = None) -> dict | None:
    """The decision the last INSPECT-opening transition recorded, or None.

    THE ONLY READ. GI-008 and GI-009 both name a lazily-computed mode as the
    violation, so every consumer — the streams-complete check, the guidance
    engine, the status display — comes through here and none of them re-derives
    anything. None means a run whose INSPECT has not been opened since this
    landed, and each caller degrades to its pre-change behaviour rather than
    guessing a width.

    ``cycle`` is WHICH crossing is being asked about, and it defaults to the
    server counter (D-216). Almost every caller is asking "what width is the
    INSPECT this run is in", which is the counter's crossing and nobody's
    judgement call, so it passes nothing. The two NARROW readers —
    `_recorded_stream_scope` and `_recorded_prove_roster` — are asking about a
    named cycle instead, because their callers measure one cycle's coverage
    against that cycle's roster, and they pass it. Making the subject an
    argument rather than a second reader is what keeps this THE only read: the
    alternative was a helper that resolves the entry a different way, which is
    the two-readers-disagree shape D-117 was filed to close.
    """
    modes = _load_json(fdir / "state.json").get("inspect_modes")
    if not isinstance(modes, list) or not modes:
        return None
    # D-212 (same class as the escalation-status read) — THE WIDTH IS RESOLVED
    # AGAINST `INSPECT_MODES`, AND THE LAST ENTRY IS THE ONE THAT DECIDES.
    #
    # `inspect_modes` is append-only and the last entry IS the current
    # decision — `_note_fix_after_inspect_decision` stamps `fixes_after_decision`
    # onto that same entry, reading it the same way and testing neither the
    # vocabulary nor the cycle stamp itself. That stays inert because the stamp
    # is read back only HERE, at `inspect_clean`, which asks
    # `_unrecorded_width_problem` first: the states where a bare `modes[-1]` and
    # this function would disagree are exactly the states that door has already
    # refused. This walked BACKWARDS past any entry with a falsy `mode`, and tested that
    # `mode` for truthiness rather than for membership of the vocabulary that
    # spells it, so a hand-edited or foreign-written `"delta"` (lowercase) or
    # `"BOGUS"` was a recorded width. `_unrecorded_width_problem` then answered
    # None, and `inspect_clean`'s refusal — `recorded_mode.get("mode") ==
    # "DELTA"` — is false for such a value, so a narrow INSPECT closed and the
    # run reached F4.
    #
    # BOTH AXES. WHAT is compared: `INSPECT_MODES`, by membership, so the
    # vocabulary decides rather than the two literals typed beside it at five
    # doors. WHICH entry answers: the last one, so a malformed current decision
    # cannot be answered with an older cycle's valid one — walking back would
    # report cycle N-1's FULL as cycle N's width and PASS the ASSAY gate that
    # refuses today, which is a worse door than the one being closed.
    #
    # An unusable record reads as NO record, which is D-117's ruling ("an
    # unrecorded width is not full width") applied to the value that is present
    # and wrong rather than to the one that is absent: every door refuses
    # through `_unrecorded_width_problem`, naming the transition that records a
    # width. Every entry this module writes carries a member of the vocabulary,
    # so a well-formed archive reads exactly as before.
    entry = modes[-1]
    if not isinstance(entry, dict):
        return None
    if entry.get("mode") not in INSPECT_MODES:
        return None
    # D-216 — AND IT IS THE ENTRY STAMPED FOR **THIS** CYCLE.
    #
    # THE THIRD AXIS. D-212 settled WHICH entry answers (the last one) and WHAT
    # its mode is resolved against (`INSPECT_MODES`). It left WHICH CYCLE the
    # entry belongs to, and this returned the newest entry whatever crossing had
    # written it — so cycle 1's decision answered "what width is cycle 2" at
    # every door that decides on one.
    #
    # The module already held the rule twice, in the two helpers that read a
    # NARROW slice of a decision: `_recorded_stream_scope` and
    # `_recorded_prove_roster` both compare the entry's cycle against the cycle
    # being measured, and the first names the omission as GI-008's violation in
    # its own docstring — "a caller reading a scope off a decision made for
    # another cycle would be narrowing this one against a width nothing recorded
    # for it". The narrow helpers guarded it; the read they are built on did not.
    #
    # Driven through `server.call_tool` at F2 cycle 2 with a single
    # `{cycle: 1, mode: FULL, rule: first_of_phase}` entry and every stream
    # complete: `Foundry-Gate('assay')` PASSED carrying
    # `inspect_ran_at_full_width (mode=FULL rule=first_of_phase) ok=True`, an
    # assertion about cycle 2 answered by cycle 1's record. With a cycle-1 DELTA
    # entry, `_check_streams_complete` reported complete True over cycle 1's
    # roster — GI-008's named violation verbatim — and the F2->F2 widening arm
    # accepted the re-open and advanced the counter to 3, stamping a FULL
    # decision for a crossing whose own width nothing had recorded.
    #
    # EARLIER OR LATER, both. Every entry this module writes carries the counter
    # the crossing that wrote it holds (`cast` and `temper` stamp
    # `_current_cycle`; `inspect_start` stamps the counter it advanced inside the
    # same transaction), so a stamp ahead of the counter cannot arise on the live
    # path at all — which is why tolerating one is tolerating something no
    # transition produced. A stale stamp is D-117's ruling ("an unrecorded width
    # is not full width") applied to the value that belongs to a different
    # crossing: it reads as NO record, and every door refuses through
    # `_unrecorded_width_problem`.
    stamped = entry.get("cycle")
    if isinstance(stamped, bool) or not isinstance(stamped, int):
        return None
    if stamped != (current_cycle(fdir) if cycle is None else cycle):
        return None
    return entry




#: The transitions GI-009 names as the ones that record a width, quoted in
#: every refusal that finds none, so the remedy is always a call the lead can
#: make rather than a fact about the archive.
_WIDTH_RECORDING_TRANSITIONS = (
    "Foundry-Phase(phase='cast') for the F2 entry, "
    "Foundry-Phase(phase='temper') for the F5 entry, or "
    "Foundry-Phase(phase='inspect_start') for a GRIND->INSPECT crossing"
)




def _inspect_mode_gap(fdir: Path, cycle: int) -> str:
    """Why the newest `inspect_modes` entry is not cycle `cycle`'s width.

    Diagnosis only — `_current_inspect_mode` is still the one place the question
    is DECIDED, and this is called only after it has already answered None. It
    exists because D-216's refusal reads very differently depending on which of
    the three ways an archive can fail to carry this INSPECT's width it hit, and
    "cycle 2 has no recorded width" on a run whose state.json visibly holds
    thirteen inspect_modes entries sends the lead hunting for a file that is
    right there. NFR-005: the one line a terminal prints has to say what was
    found, not only what was missing.
    """
    modes = _load_json(fdir / "state.json").get("inspect_modes")
    if not isinstance(modes, list) or not modes:
        return "nothing has recorded an inspect_modes entry for it"
    entry = modes[-1]
    if not isinstance(entry, dict) or entry.get("mode") not in INSPECT_MODES:
        return (
            "the newest inspect_modes entry records no width this server "
            f"spells — {', '.join(sorted(INSPECT_MODES))} are the only two"
        )
    stamped = entry.get("cycle")
    if isinstance(stamped, bool) or not isinstance(stamped, int):
        return "the newest inspect_modes entry carries no usable cycle stamp"
    return (
        f"the newest inspect_modes entry is stamped for cycle {stamped}, and a "
        f"width is a fact about ONE crossing — cycle {stamped}'s decision says "
        f"nothing about what cycle {cycle} was opened with"
    )




def _unrecorded_width_problem(fdir: Path) -> dict | None:
    """`{"reason", "hint"}` when this INSPECT has NO recorded width, else None.

    D-117 — AN UNRECORDED WIDTH IS NOT FULL WIDTH.
    ---------------------------------------------
    GI-009's named violation is "A first INSPECT of a phase with no recorded
    mode" and GI-008's is "a streams-complete check that reads a roster nothing
    recorded". Every consumer degraded PERMISSIVELY instead of refusing, and
    each degradation was individually defensible — "a resumed archive should get
    the pre-change behaviour" — while together they admitted a mode-less INSPECT
    as full width at every door at once:

      `_check_streams_complete` fell back to the pre-width roster
      trace/prove/test, so `research_audit` and `test01` were never required;
      `foundry_gate('assay')` tested `mode != "DELTA"`, which "unrecorded"
      passes; `inspect_clean`'s DELTA refusal tested `== "DELTA"`, which
      "unrecorded" also passes; and `_maybe_skip_trace`'s D-071 fence handled
      FULL and DELTA explicitly then fell through to the legacy
      `_trace_skip_check` and auto-stamped `.trace-complete`.

    Driven end to end through the shipped `Foundry-Init(resume=...)`, which
    reactivates any archive with whatever `state.json` it holds, on a
    legacy-shaped archive with the committed evidence deliberately stale at
    HEAD: streams-complete required only trace, prove and test and reported
    complete; `inspect_clean` returned ok and moved the run to F4; and
    `Foundry-Gate('assay')` PASSED carrying the checklist line
    `inspect_ran_at_full_width (mode=unrecorded rule=unrecorded) ok=True` — the
    assertion that is false. With a `.trace-clean-at` marker present TRACE never
    ran either. ASSAY opened having run PROVE and TEST only, over an evidence
    corpus no boundary sweep had ever re-executed.

    ONE PREDICATE, SIX CALLERS. The permissive fallbacks were six separate
    judgement calls in six functions, which is exactly how they came to agree on
    the wrong answer without any of them saying so. The refusal names the
    missing record AND the transition that writes it, because "there is no
    recorded width" is not an action.
    """
    if _current_inspect_mode(fdir) is not None:
        return None
    cycle = current_cycle(fdir)
    return {
        "reason": (
            f"this INSPECT (cycle {cycle}) has no recorded width — "
            f"{_inspect_mode_gap(fdir, cycle)}, so the roster, the rule and the "
            "evidence sweep it was opened with are all unknown"
        ),
        "hint": (
            "An INSPECT is opened by the transition that decides and records "
            "its width (GI-009), and an unrecorded width is never read as FULL: "
            "a roster nothing recorded is not a roster that ran. Cross the "
            f"boundary that records one — {_WIDTH_RECORDING_TRANSITIONS} — "
            "which also sweeps the evidence corpus at HEAD, then run exactly "
            "the roster it names."
        ),
    }
