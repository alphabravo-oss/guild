"""Foundry orchestrator tools — phase enforcement, team lifecycle, and guided execution.

Replaces bash script enforcement with typed MCP tools that guide the lead agent
through the foundry loop. Every critical action (phase transitions, team management,
stream verification) goes through these tools instead of raw bash commands.

All operations are local file reads/writes. Zero API calls. Zero cost.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

from foundry_mcp.tools.foundry_state import (
    clear_active_run,
    get_run_dir,
)
from foundry_mcp.tools.display import foundry_hammer, FOUNDRY_SEP

# ANSI colors — shared with display.py
_RESET = "\033[0m"
_BOLD = "\033[1m"
_DIM = "\033[2m"
_RED = "\033[31m"
_GREEN = "\033[32m"
_YELLOW = "\033[33m"
_BLUE = "\033[34m"
_CYAN = "\033[36m"
_WHITE = "\033[37m"
_BCYAN = f"{_BOLD}{_CYAN}"
_BGREEN = f"{_BOLD}{_GREEN}"
_BYELLOW = f"{_BOLD}{_YELLOW}"
_BRED = f"{_BOLD}{_RED}"
_BWHITE = f"{_BOLD}{_WHITE}"


def _load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _save_json(path: Path, data: dict) -> None:
    """Atomic JSON write — write to .tmp then rename."""
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    tmp.rename(path)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# Single compiled source of truth for the requirement-ID grammar. Used by
# BOTH the requirement count and the requirement-ID list so the P3 verdict
# synthesis writes exactly one row per ID the DONE gate's verdict_coverage
# check counts (analog note 3: do not fork a second regex/path resolver).
_REQ_ID_RE = re.compile(r"\b(?:US|FR|NFR|AC|VC|IR|TR)-\d+(?:\.\d+)?\b")


def _resolve_spec_path(project_root: str) -> Path | None:
    """Resolve the active run's spec.md path, or None if unresolvable.

    Prefers ``<run_dir>/spec.md``; falls back to ``state.json['spec_path']``
    resolved against ``project_root``. Single code path shared by the
    requirement COUNT and the requirement-ID LIST so the two never drift.
    """
    fdir = get_run_dir(project_root)
    if not fdir:
        return None
    spec_path = fdir / "spec.md"
    if spec_path.exists():
        return spec_path
    state = _load_json(fdir / "state.json")
    sp = state.get("spec_path", "")
    if sp:
        candidate = Path(project_root) / sp
        if candidate.exists():
            return candidate
    return None


def _spec_requirement_ids(project_root: str) -> list[str]:
    """Return the sorted unique requirement IDs (US/FR/NFR/AC/VC/IR/TR-N).

    The id source for P3 verdict synthesis. Uses the SAME path resolution
    and regex as ``_count_spec_requirements`` (which now delegates here) so
    synthesizing one VERIFIED row per id keeps ``verdict_coverage`` in
    lock-step with the DONE gate's ``_count_spec_requirements`` read.
    """
    spec_path = _resolve_spec_path(project_root)
    if spec_path is None:
        return []
    try:
        text = spec_path.read_text(encoding="utf-8")
    except OSError:
        return []
    return sorted(set(_REQ_ID_RE.findall(text)))


def _count_spec_requirements(project_root: str) -> int:
    """Count requirement IDs (US-N, FR-N, NFR-N, AC-N, VC-N) in the spec file."""
    return len(_spec_requirement_ids(project_root))


def _prove_is_clean(fdir: Path, project_root: str) -> bool:
    """True when the recorded PROVE stream is clean: 0 findings AND >=95%
    requirement coverage.

    Reads the aggregate counts ``foundry_mark_stream`` stamps into
    ``.prove-complete`` (which stores ONLY aggregate counts, not
    per-requirement verdicts — the reason P3 must synthesize). Mirrors the
    >=95% coverage threshold enforced at stream-mark time.
    """
    marker = fdir / ".prove-complete"
    if not marker.exists():
        return False
    try:
        text = marker.read_text(encoding="utf-8")
    except OSError:
        return False
    items_checked = 0
    findings: int | None = None
    for line in text.splitlines():
        if line.startswith("items_checked="):
            try:
                items_checked = int(line.split("=", 1)[1].strip())
            except (ValueError, IndexError):
                pass
        elif line.startswith("findings="):
            try:
                findings = int(line.split("=", 1)[1].strip())
            except (ValueError, IndexError):
                pass
    if findings is None or findings != 0:
        return False
    spec_count = _count_spec_requirements(project_root)
    if spec_count > 0 and items_checked < spec_count * 0.95:
        return False
    return True


def _synthesize_clean_prove_verdicts(
    fdir: Path, project_root: str, cycle: int = 0
) -> int:
    """On a clean PROVE, write a VERIFIED verdict row for every spec
    requirement ID that lacks one. Returns the count synthesized.

    Rows match the Foundry-Verdict schema (foundry.py record shape):
    id / verdict / evidence / spec_text_cited / code_location / cycle /
    recorded_at. Existing rows are left untouched — never downgraded, never
    duplicated — so a real ASSAY verdict is preserved and ``verdict_coverage``
    never double-counts (analog note 7: preserve id-dedup).
    """
    ids = _spec_requirement_ids(project_root)
    if not ids:
        return 0
    verdicts_path = fdir / "verdicts.json"
    verdicts = _load_json(verdicts_path)
    requirements = verdicts.get("requirements")
    if not isinstance(requirements, list):
        requirements = []
    existing_ids = {r.get("id") for r in requirements if isinstance(r, dict)}
    now = _now()
    synthesized = 0
    for rid in ids:
        if rid in existing_ids:
            continue
        requirements.append(
            {
                "id": rid,
                "verdict": "VERIFIED",
                "evidence": (
                    "Auto-verified on clean PROVE "
                    "(≥95% coverage, 0 findings)."
                ),
                "spec_text_cited": "",
                "code_location": "",
                "cycle": cycle,
                "recorded_at": now,
            }
        )
        synthesized += 1
    if synthesized:
        verdicts["requirements"] = requirements
        _save_json(verdicts_path, verdicts)
    return synthesized


# --- P4 (FR-005 / ST-002): passing-gate → guidance-state advance ---
#
# Maps each _compute_next_action "transition_*" action to the Foundry-Gate
# phase whose passing should advance the guidance state. When that gate has
# passed (recorded via the ``.gate-passed`` marker), the next Foundry-Next
# tells the lead the gate is satisfied and to proceed to the transition step
# instead of re-running the now-satisfied gate.
_ACTION_TO_GATE = {
    "transition_to_cast": "cast",
    "transition_to_inspect": "inspect",
    "transition_to_grind": "grind",
    "transition_to_assay": "assay",
    "transition_to_temper": "temper",
    "transition_to_done": "done",
}


def _expected_gate_for_action(action: str) -> str | None:
    """Return the gate phase a given transition action asks the lead to run."""
    return _ACTION_TO_GATE.get(action)


# --- Phase gate ---


def foundry_gate(
    phase: str,
    project_root: str = ".",
) -> dict:
    """Check if preconditions are met to enter a phase."""
    fdir = get_run_dir(project_root)
    if not fdir:
        return {"phase": phase, "passed": False, "reason": "No active foundry run", "hint": "Call Foundry-Init first"}

    if not fdir.exists():
        return {"phase": phase, "passed": False, "reason": "foundry directory not found", "hint": "Run foundry_init first"}

    checklist: list[dict] = []
    passed = True
    reason = ""
    hint = ""

    nac = fdir / ".next-action-called"
    if not nac.exists():
        return {
            "phase": phase,
            "passed": False,
            "reason": "Must call Foundry-Next before any gate check",
            "hint": "Call Foundry-Next first — it shows the status display and tells you what to do next.",
            "checklist": [{"check": "next_action_called", "ok": False}],
        }
    nac.unlink(missing_ok=True)

    if phase == "validate":
        # Gate for F0.9 VALIDATE — castings must exist
        manifest = fdir / "castings" / "manifest.json"
        if not manifest.exists():
            return {"phase": phase, "passed": False, "reason": "No manifest.json", "hint": "Run DECOMPOSE first to create castings"}
        data = _load_json(manifest)
        count = len(data.get("castings", []))
        checklist.append({"check": "manifest_exists", "ok": True})
        if count < 1:
            return {"phase": phase, "passed": False, "reason": "No castings in manifest", "hint": "Add castings before validating"}
        checklist.append({"check": f"castings_count={count}", "ok": True})

    elif phase == "cast":
        manifest = fdir / "castings" / "manifest.json"
        if not manifest.exists():
            return {"phase": phase, "passed": False, "reason": "No manifest.json", "hint": "Run foundry_init and add castings"}
        data = _load_json(manifest)
        count = len(data.get("castings", []))
        checklist.append({"check": "manifest_exists", "ok": True})
        if count < 1:
            return {"phase": phase, "passed": False, "reason": "No castings in manifest", "hint": "Add castings before CAST"}
        checklist.append({"check": f"castings_count={count}", "ok": True})

        oversized = []
        for c in data.get("castings", []):
            kf = len(c.get("key_files", []))
            if kf > 8:
                oversized.append({"id": c.get("id"), "title": c.get("title", ""), "key_files": kf})
        if oversized:
            passed = False
            names = ", ".join(f"#{c['id']} ({c['key_files']} files)" for c in oversized)
            reason = f"Oversized castings: {names}. Max 8 key_files per casting."
            hint = "Split large castings into smaller ones (2-5 tasks, 2-8 files each). No teammate should get 1000 lines of work."
            checklist.append({"check": "casting_size", "ok": False, "oversized": oversized})

        file_to_casting: dict[str, list[int]] = {}
        for c in data.get("castings", []):
            cid = c.get("id", 0)
            for f in c.get("key_files", []):
                file_to_casting.setdefault(f, []).append(cid)
        overlaps = {f: cids for f, cids in file_to_casting.items() if len(cids) > 1}
        if overlaps:
            overlap_details = [f"{f}: castings {cids}" for f, cids in overlaps.items()]
            passed = False
            reason = f"File overlap between castings: {'; '.join(overlap_details)}"
            hint = "Two castings editing the same file will cause conflicts. Move shared files to an earlier casting or merge the overlapping castings."
            checklist.append({"check": "no_file_overlap", "ok": False, "overlaps": overlaps})
        else:
            checklist.append({"check": "no_file_overlap", "ok": True})

    elif phase == "inspect":
        if not (fdir / ".cast-complete").exists():
            passed = False
            reason = "CAST not complete"
            hint = "Complete all CAST tasks and call Foundry-Phase(phase='cast')"
            checklist.append({"check": "cast_complete", "ok": False})
        else:
            checklist.append({"check": "cast_complete", "ok": True})

        teams_result = _check_active_teams(project_root)
        if teams_result["active"]:
            passed = False
            parts = []
            if teams_result["teams"]:
                parts.append(f"Team dirs: {', '.join(teams_result['teams'])}")
            if teams_result.get("live_panes"):
                parts.append(f"Live panes: {', '.join(teams_result['live_panes'])}")
            reason = f"Active teammates: {'; '.join(parts)}"
            hint = teams_result.get("hint", "Shut down all teammates, call TeamDelete, then Foundry-Team-Down")
            checklist.append({"check": "no_active_teams", "ok": False,
                            "teams": teams_result["teams"],
                            "live_panes": teams_result.get("live_panes", [])})
        else:
            checklist.append({"check": "no_active_teams", "ok": True})

        sight = _check_sight_required(project_root)
        if sight.get("required") and sight.get("blocked"):
            passed = False
            reason = sight["reason"]
            hint = "Provide --url for SIGHT audit or update manifest.json target_url"
            checklist.append({"check": "sight_url", "ok": False, "reason": sight["reason"]})
        else:
            checklist.append({"check": "sight_url", "ok": True})

    elif phase == "grind":
        defects = _load_json(fdir / "defects.json")
        open_count = sum(1 for d in defects.get("defects", []) if d.get("status") == "open")
        if open_count < 1:
            passed = False
            reason = "No open defects to grind"
            hint = "Nothing to fix — skip to ASSAY"
        checklist.append({"check": f"open_defects={open_count}", "ok": open_count >= 1})

        teams_result = _check_active_teams(project_root)
        if teams_result["active"]:
            passed = False
            parts = []
            if teams_result["teams"]:
                parts.append(f"Team dirs: {', '.join(teams_result['teams'])}")
            if teams_result.get("live_panes"):
                parts.append(f"Live panes: {', '.join(teams_result['live_panes'])}")
            reason = f"Active teammates: {'; '.join(parts)}"
            hint = teams_result.get("hint", "Shut down INSPECT teams first")
        checklist.append({"check": "no_active_teams", "ok": not teams_result["active"],
                         "live_panes": teams_result.get("live_panes", [])})

        if not (fdir / ".tasks-generated").exists():
            passed = False
            reason = "defects-to-tasks has not been run"
            hint = "Call Foundry-Tasks before entering GRIND"
        checklist.append({"check": "tasks_generated", "ok": (fdir / ".tasks-generated").exists()})

    elif phase == "assay":
        defects = _load_json(fdir / "defects.json")
        open_count = sum(1 for d in defects.get("defects", []) if d.get("status") == "open")
        if open_count > 0:
            passed = False
            reason = f"{open_count} open defect(s) remain"
            hint = "Fix all defects in GRIND first"
        checklist.append({"check": f"zero_open_defects (have {open_count})", "ok": open_count == 0})

        streams = _check_streams_complete(project_root)
        if not streams["complete"]:
            passed = False
            reason = f"Verification streams incomplete: {streams.get('missing', '')}"
            hint = "All streams (trace, prove, sight, test) must complete before ASSAY"
        checklist.append({"check": "all_streams_complete", "ok": streams["complete"],
                         "missing": streams.get("missing", "")})

        if not (fdir / ".inspect-clean").exists():
            has_fixed = sum(1 for d in defects.get("defects", []) if d.get("status") == "fixed")
            if has_fixed > 0:
                passed = False
                reason = "GRIND fixed defects but INSPECT has not re-verified"
                hint = "Run full INSPECT cycle after GRIND. Call foundry_mark_inspect_clean when clean."
            checklist.append({"check": "inspect_clean", "ok": False})
        else:
            checklist.append({"check": "inspect_clean", "ok": True})

        teams_result = _check_active_teams(project_root)
        if teams_result["active"]:
            passed = False
            reason = f"Active teams: {', '.join(teams_result['teams'])}"
        checklist.append({"check": "no_active_teams", "ok": not teams_result["active"]})

    elif phase == "temper":
        verdicts = _load_json(fdir / "verdicts.json")
        non_verified = sum(1 for r in verdicts.get("requirements", []) if r.get("verdict") != "VERIFIED")
        if non_verified > 0:
            passed = False
            reason = f"{non_verified} requirement(s) not verified"
        checklist.append({"check": f"all_verified (non_verified={non_verified})", "ok": non_verified == 0})

    elif phase == "done":
        verdicts = _load_json(fdir / "verdicts.json")
        verdict_list = verdicts.get("requirements", [])
        non_verified = sum(1 for r in verdict_list if r.get("verdict") != "VERIFIED")
        defects = _load_json(fdir / "defects.json")
        open_count = sum(1 for d in defects.get("defects", []) if d.get("status") == "open")
        teams_result = _check_active_teams(project_root)

        if non_verified > 0:
            passed = False
            reason = f"{non_verified} requirement(s) not VERIFIED \u2014 THIN/PARTIAL are defects, not follow-ups"
            hint = "Fix all non-VERIFIED requirements. Every THIN item must be fully implemented."
        if open_count > 0:
            passed = False
            reason = f"{open_count} open defect(s) remain"
        if teams_result["active"]:
            passed = False
            reason = f"Active teams: {', '.join(teams_result['teams'])}"

        spec_count = _count_spec_requirements(project_root)
        verdict_count = len(verdict_list)
        verdicts_complete = True
        if spec_count > 0 and verdict_count < spec_count:
            passed = False
            skipped = spec_count - verdict_count
            reason = f"Only {verdict_count} verdicts but spec has {spec_count} requirements. {skipped} skipped."
            hint = "ASSAY must write ALL verdicts to verdicts.json \u2014 including THIN/PARTIAL, not just VERIFIED."
            verdicts_complete = False

        checklist.append({"check": f"all_verified (non_verified={non_verified})", "ok": non_verified == 0})
        checklist.append({"check": f"zero_defects (open={open_count})", "ok": open_count == 0})
        checklist.append({"check": "no_active_teams", "ok": not teams_result["active"]})
        checklist.append({"check": f"verdict_coverage ({verdict_count}/{spec_count})", "ok": verdicts_complete})

    else:
        return {"phase": phase, "passed": False, "reason": f"Unknown phase: {phase}",
                "hint": "Valid phases: cast, inspect, grind, assay, temper, done"}

    result = {"phase": phase, "passed": passed, "checklist": checklist}
    if not passed:
        result["reason"] = reason
        result["hint"] = hint
    else:
        # P4 (FR-005 / ST-002): a passing gate advances the guidance state.
        # Record which gate passed so the next Foundry-Next emits the
        # transition step instead of re-running this now-satisfied gate.
        # Cleared by _update_phase when the phase actually advances.
        try:
            (fdir / ".gate-passed").write_text(
                json.dumps({"phase": phase, "at": _now()}),
                encoding="utf-8",
            )
        except OSError:
            pass
    return result


# --- Stream markers ---


def foundry_mark_stream(
    stream: str,
    cycle: int,
    items_checked: int = 0,
    items_total: int = 0,
    findings_count: int = 0,
    project_root: str = ".",
) -> dict:
    """Mark a verification stream as complete for this cycle."""
    valid = {"trace", "prove", "sight", "test", "probe"}
    if stream not in valid:
        return {"error": f"Invalid stream: {stream}. Must be one of: {', '.join(sorted(valid))}"}

    fdir = get_run_dir(project_root)
    if not fdir or not fdir.exists():
        return {"error": "No active foundry run"}

    if items_checked <= 0:
        return {
            "error": f"Cannot mark {stream} complete with items_checked={items_checked}. "
                     "You must report how many items were actually checked. "
                     "trace: symbols checked. prove: requirements checked. "
                     "sight: pages/elements exercised. test: tests run. "
                     "probe: endpoints hit.",
            "hint": "If the stream genuinely checked 0 items, the scope may be wrong.",
        }

    if stream == "prove" and items_total > 0:
        spec_count = _count_spec_requirements(project_root)
        if spec_count > 0 and items_checked < spec_count * 0.95:
            return {
                "error": f"PROVE checked {items_checked} requirements but spec has {spec_count}. "
                         f"Coverage is {items_checked/spec_count*100:.0f}% \u2014 must be \u226595%.",
                "hint": "Re-run PROVE and check ALL requirements in the spec. No skipping.",
                "spec_requirements": spec_count,
                "checked": items_checked,
            }

    if stream == "trace" and items_total > 0 and items_checked < items_total * 0.95:
        return {
            "error": f"TRACE checked {items_checked}/{items_total} symbols ({items_checked/items_total*100:.0f}%). "
                     "Must check \u226595% of declared symbols.",
            "hint": "Re-run TRACE on unchecked symbols.",
        }

    coverage_pct = f"{items_checked / items_total * 100:.0f}%" if items_total > 0 else "N/A"

    marker = fdir / f".{stream}-complete"
    coverage_warning = ""
    if marker.exists():
        prev_text = marker.read_text(encoding="utf-8")
        for line in prev_text.split("\n"):
            if line.startswith("items_checked="):
                try:
                    prev_checked = int(line.split("=")[1])
                    if items_checked < prev_checked * 0.7:
                        coverage_warning = (
                            f"Coverage dropped: checked {items_checked} items vs "
                            f"{prev_checked} in previous cycle. Are you rushing?"
                        )
                except (ValueError, IndexError):
                    pass

    marker.write_text(
        f"{_now()} cycle={cycle}\n"
        f"items_checked={items_checked}\n"
        f"items_total={items_total}\n"
        f"coverage={coverage_pct}\n"
        f"findings={findings_count}\n",
        encoding="utf-8",
    )

    # TRACE skip-gate anchor: when TRACE passes with zero findings, stamp the
    # current HEAD SHA. Future F2 entries can compare HEAD vs this SHA
    # restricted to manifest key_files — if no overlap, skip TRACE.
    # Deterministic, verbatim the same as re-running LSP: topology unchanged.
    if stream == "trace" and findings_count == 0:
        import subprocess
        try:
            rev = subprocess.run(
                ["git", "-C", project_root, "rev-parse", "HEAD"],
                capture_output=True, text=True, timeout=5,
            )
            if rev.returncode == 0 and rev.stdout.strip():
                import json as _json
                (fdir / ".trace-clean-at").write_text(
                    _json.dumps({
                        "head_sha": rev.stdout.strip(),
                        "stamped_at": _now(),
                        "cycle": cycle,
                        "items_checked": items_checked,
                    }),
                    encoding="utf-8",
                )
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            pass

    result: dict = {
        "ok": True,
        "stream": stream,
        "cycle": cycle,
        "items_checked": items_checked,
        "items_total": items_total,
        "coverage": coverage_pct,
        "findings": findings_count,
    }
    if coverage_warning:
        result["warning"] = coverage_warning

    return result


def _trace_skip_check(fdir: Path, project_root: str) -> dict:
    """Decide whether the current F2 INSPECT can skip the TRACE stream.

    Rationale: TRACE is LSP-heavy (EXISTS / SUBSTANTIVE / WIRED / PLACED
    across every manifest symbol). A cycle of TRACE routinely runs 100+
    Serena IPC calls over several minutes. Topology is a pure function of
    the code on disk — if no file owning a manifest symbol has changed
    since the last clean TRACE, the verdicts are provably identical.

    Returns {skip: bool, reason: str, details?: {...}}.
    """
    marker = fdir / ".trace-clean-at"
    if not marker.exists():
        return {"skip": False, "reason": "no prior clean TRACE to compare against"}
    try:
        marker_data = json.loads(marker.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"skip": False, "reason": "unreadable .trace-clean-at marker"}
    clean_sha = marker_data.get("head_sha", "")
    if not clean_sha:
        return {"skip": False, "reason": "no head_sha recorded"}

    manifest = _load_json(fdir / "castings" / "manifest.json")
    key_files: set[str] = set()
    for c in manifest.get("castings", []):
        for f in (c.get("key_files") or []):
            if isinstance(f, str) and f.strip():
                key_files.add(f.strip())
    if not key_files:
        return {"skip": False, "reason": "no key_files declared in manifest — cannot scope diff"}

    import subprocess
    try:
        result = subprocess.run(
            ["git", "-C", project_root, "diff", "--name-only", clean_sha, "HEAD"],
            capture_output=True, text=True, timeout=10,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return {"skip": False, "reason": "git unavailable"}
    if result.returncode != 0:
        return {"skip": False, "reason": f"git diff failed: {result.stderr.strip()[:120]}"}

    changed_files = {line.strip() for line in result.stdout.splitlines() if line.strip()}
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
    """
    if not fdir or not fdir.exists():
        return None
    if (fdir / ".trace-complete").exists():
        return None
    state = _load_json(fdir / "state.json")
    if state.get("phase") != "F2":
        return None

    decision = _trace_skip_check(fdir, project_root)
    if not decision.get("skip"):
        return decision

    (fdir / ".trace-complete").write_text(
        f"{_now()} cycle=skipped\n"
        f"items_checked=0\n"
        f"items_total=0\n"
        f"coverage=SKIPPED\n"
        f"findings=0\n"
        f"skipped=true\n"
        f"reason={decision['reason']}\n",
        encoding="utf-8",
    )
    return decision


def _check_streams_complete(project_root: str) -> dict:
    """Check if all required verification streams have completed."""
    fdir = get_run_dir(project_root)
    if not fdir:
        return {"complete": False, "missing": "all", "required": []}
    manifest = fdir / "castings" / "manifest.json"

    required = ["trace", "prove", "test"]

    no_ui = False
    url = ""
    if manifest.exists():
        mdata = _load_json(manifest)
        no_ui = mdata.get("no_ui", False)
        url = mdata.get("target_url", "")

    if not no_ui:
        required.append("sight")
    else:
        sight = _check_sight_required(project_root)
        if sight.get("required"):
            required.append("sight")

    if url:
        required.append("probe")

    missing = [s for s in required if not (fdir / f".{s}-complete").exists()]

    return {"complete": len(missing) == 0, "missing": " ".join(missing), "required": required}


# --- Phase lifecycle markers ---


def _finalize_open_phase_entry(entry: dict, now: str) -> None:
    """If `entry` has started_at but no ended_at, stamp ended_at + duration."""
    if "started_at" in entry and "ended_at" not in entry:
        entry["ended_at"] = now
        try:
            start = datetime.fromisoformat(entry["started_at"])
            end = datetime.fromisoformat(now)
            delta = end - start
            mins = int(delta.total_seconds() // 60)
            secs = int(delta.total_seconds() % 60)
            entry["duration"] = f"{mins}m {secs}s"
        except (ValueError, KeyError):
            pass


def _update_phase(fdir: Path, new_phase: str) -> None:
    """Update state.json with the new phase. Tracks timing per phase.

    Closes EVERY still-open phase_times entry before opening the new one.
    Passive sub-phase stamping (see _stamp_subphase_transitions) opens
    F0.5/F0.9 based on file-state signals, so a single `prev_phase` close
    isn't sufficient — the F0 → F1 jump skips F0.5/F0.9 at the state-level
    even though those sub-phases did elapse in wall time.
    """
    state_path = fdir / "state.json"
    state = _load_json(state_path)
    now = _now()

    phase_times = state.get("phase_times", {})
    for entry in phase_times.values():
        _finalize_open_phase_entry(entry, now)

    phase_times[new_phase] = {"started_at": now}

    state["phase"] = new_phase
    state["updated_at"] = now
    state["phase_times"] = phase_times
    history = state.get("phase_history", [])
    history.append({"phase": new_phase, "entered_at": now})
    state["phase_history"] = history

    if new_phase == "F6":
        state["ended_at"] = now
        started = state.get("started_at", "")
        if started:
            try:
                start = datetime.fromisoformat(started)
                end = datetime.fromisoformat(now)
                delta = end - start
                hours = int(delta.total_seconds() // 3600)
                mins = int((delta.total_seconds() % 3600) // 60)
                secs = int(delta.total_seconds() % 60)
                state["total_duration"] = f"{hours}h {mins}m {secs}s"
            except ValueError:
                pass

    _save_json(state_path, state)

    # P4 (ST-002): a real phase advance supersedes any pending gate-passed
    # guidance marker. Clear it so the next Foundry-Next emits the NEW phase's
    # fresh imperative rather than a stale "gate already passed" note.
    (fdir / ".gate-passed").unlink(missing_ok=True)


def foundry_mark_phase_complete(
    phase: str,
    project_root: str = ".",
) -> dict:
    """Mark a phase transition. Validates preconditions AND updates state.json.phase."""
    fdir = get_run_dir(project_root)
    if not fdir or not fdir.exists():
        return {"error": "No active foundry run"}

    nac = fdir / ".next-action-called"
    if not nac.exists():
        return {
            "error": "Must call Foundry-Next before phase transitions",
            "hint": "Call Foundry-Next first \u2014 it shows status and guides you.",
        }
    nac.unlink(missing_ok=True)

    if phase == "start_cast":
        _update_phase(fdir, "F1")
        return {"ok": True, "phase": "F1", "message": "Phase is now F1 (CAST). Create team and build."}

    elif phase == "cast":
        teams = _check_active_teams(project_root)
        if teams["active"]:
            return {"error": f"Cannot mark CAST complete \u2014 active teams: {', '.join(teams['teams'])}",
                    "hint": "Shut down all teammates and TeamDelete before marking CAST complete"}
        (fdir / ".cast-complete").write_text(f"{_now()}\n", encoding="utf-8")
        # Stamp the CAST baseline HEAD SHA so GRIND cycles can show teammates
        # what has changed since CAST ended. Used by foundry_spawn_teammate
        # (phase='grind') to build a cycle-context block the lead appends to
        # the GRIND prompt, saving redundant re-exploration of files that
        # earlier cycles already touched.
        import subprocess as _sp
        try:
            _rev = _sp.run(
                ["git", "-C", project_root, "rev-parse", "HEAD"],
                capture_output=True, text=True, timeout=5,
            )
            if _rev.returncode == 0 and _rev.stdout.strip():
                (fdir / ".cast-baseline-sha").write_text(_rev.stdout.strip(), encoding="utf-8")
        except (FileNotFoundError, _sp.TimeoutExpired, OSError):
            pass
        _update_phase(fdir, "F2")
        return {"ok": True, "phase": "F2", "message": "CAST complete \u2192 phase is now F2 (INSPECT)"}

    elif phase == "inspect_clean":
        streams = _check_streams_complete(project_root)
        if not streams["complete"]:
            return {"error": f"Cannot mark INSPECT clean \u2014 streams incomplete: {streams['missing']}",
                    "hint": "Run all required verification streams first"}
        defects = _load_json(fdir / "defects.json")
        open_count = sum(1 for d in defects.get("defects", []) if d.get("status") == "open")
        if open_count > 0:
            return {"error": f"Cannot mark INSPECT clean \u2014 {open_count} open defect(s) remain",
                    "hint": "All defects must be fixed before marking clean"}
        (fdir / ".inspect-clean").write_text(f"{_now()}\n", encoding="utf-8")
        _update_phase(fdir, "F4")
        return {"ok": True, "phase": "F4", "message": "INSPECT clean \u2192 phase is now F4 (ASSAY)"}

    elif phase == "grind_start":
        for marker in [".trace-complete", ".prove-complete", ".sight-complete",
                       ".test-complete", ".probe-complete", ".inspect-clean", ".tasks-generated"]:
            (fdir / marker).unlink(missing_ok=True)
        _update_phase(fdir, "F3")
        return {"ok": True, "phase": "F3",
                "message": "All markers cleared \u2192 phase is now F3 (GRIND). Full INSPECT must re-run after."}

    elif phase == "assay_fail":
        for marker in [".trace-complete", ".prove-complete", ".sight-complete",
                       ".test-complete", ".probe-complete", ".inspect-clean", ".tasks-generated"]:
            (fdir / marker).unlink(missing_ok=True)
        _update_phase(fdir, "F3")
        return {"ok": True, "phase": "F3",
                "message": "ASSAY failed \u2192 phase is now F3 (GRIND). Fix defects, then full INSPECT, then ASSAY again."}

    elif phase == "temper":
        _update_phase(fdir, "F5")
        return {"ok": True, "phase": "F5", "message": "Phase is now F5 (TEMPER)"}

    elif phase == "done":
        _update_phase(fdir, "F6")
        # Clear the active run — session is done with this run
        clear_active_run()
        return {"ok": True, "phase": "F6", "message": "Phase is now F6 (DONE). Run archived. Start a new run with foundry_init."}

    else:
        return {"error": f"Invalid phase: {phase}. Valid: cast, inspect_clean, grind_start, assay_fail, temper, done"}


# --- Team lifecycle ---


def _scan_tmux_panes() -> dict:
    """Scan all tmux panes and classify them.

    Claude Code spawns teammates as PANES within the lead's tmux session
    (via split-window). Pane titles are set to the agent name (e.g., "@cast-c1").

    IMPORTANT: pane_current_command for a live teammate is the Claude Code
    VERSION NUMBER (e.g., "2.1.80"), NOT "claude" or "node" or "bash".
    A zombie pane shows "bash"/"zsh" because the agent exited and the shell
    is all that's left. But a live teammate's bash shell has the agent as a
    child process, so pane_current_command reflects the agent binary.

    We use pane title + child process check for definitive classification:
    - LEAD: the active pane
    - LIVE: teammate pane whose bash PID has child processes (agent running)
    - ZOMBIE: teammate pane that is dead OR whose bash PID has NO children
    - USER: non-lead pane that doesn't look like a teammate (left alone)

    Teammate detection: Claude Code sets pane titles via `select-pane -T`.
    Teammate panes have titles starting with "@" or matching agent naming
    patterns (cast-, grind-, etc.). User's personal panes are never touched.

    Returns {
        "available": bool,
        "live": [(id, title, cmd)],
        "zombie": [(id, title, cmd)],
        "user": [(id, title, cmd)],   # user's panes — never touched
        "lead": (id, title) | None,
    }
    """
    import subprocess
    import re

    empty: dict = {"available": False, "live": [], "zombie": [], "user": [], "lead": None}
    try:
        check = subprocess.run(["tmux", "list-sessions"], capture_output=True, timeout=5)
        if check.returncode != 0:
            return empty
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return empty

    # Patterns that identify a pane as a Claude Code teammate
    _TEAMMATE_RE = re.compile(
        r"^@|"                                 # Claude Code prefixes teammate titles with @
        r"cast[-_]|grind[-_]|inspect[-_]|"     # foundry phase agents
        r"assay[-_]|temper[-_]|decompose[-_]|" # foundry phase agents
        r"trace[-_]|prove[-_]|sight[-_]|"      # verification stream agents
        r"test[-_]|probe[-_]|"                 # verification stream agents
        r"^teammate-|^agent-",                 # generic teammate patterns
        re.IGNORECASE,
    )

    try:
        result = subprocess.run(
            ["tmux", "list-panes", "-a", "-F",
             "#{session_name}:#{window_index}.#{pane_index}\t"
             "#{pane_title}\t#{pane_dead}\t#{pane_current_command}\t"
             "#{pane_active}\t#{pane_pid}"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode != 0:
            return empty
    except (subprocess.TimeoutExpired, OSError):
        return empty

    live = []
    zombie = []
    user = []
    lead = None

    for line in result.stdout.strip().split("\n"):
        if not line.strip():
            continue
        parts = line.split("\t", 5)
        if len(parts) < 6:
            continue
        pane_id, title, dead, cmd, active, pid = parts

        if active == "1":
            lead = (pane_id, title)
            continue

        # Only touch panes that look like teammates
        if not _TEAMMATE_RE.search(title):
            user.append((pane_id, title, cmd))
            continue

        # Dead panes are always zombies
        if dead == "1":
            zombie.append((pane_id, title, cmd))
            continue

        # Check if the pane's process has children (= agent still running)
        has_children = _pid_has_children(pid)
        if has_children:
            live.append((pane_id, title, cmd))
        else:
            zombie.append((pane_id, title, cmd))

    return {"available": True, "live": live, "zombie": zombie, "user": user, "lead": lead}


def _pid_has_children(pid: str) -> bool:
    """Check if a PID has child processes (i.e., agent is still running)."""
    import subprocess

    if not pid or not pid.strip().isdigit():
        return False
    try:
        # pgrep -P returns 0 if children exist, 1 if none
        result = subprocess.run(
            ["pgrep", "-P", pid],
            capture_output=True, timeout=3,
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return False


def _kill_panes(panes: list[tuple[str, str, str]]) -> int:
    """Kill a list of (pane_id, title, cmd) tuples.

    Kills in REVERSE order to avoid index shifting — tmux reindexes
    panes when siblings are killed, so killing from highest index
    first prevents targeting the wrong pane.

    Returns count killed.
    """
    import subprocess

    # Sort by pane index descending so kills don't shift targets
    sorted_panes = sorted(panes, key=lambda p: p[0], reverse=True)
    killed = 0
    for pane_id, _title, _cmd in sorted_panes:
        try:
            subprocess.run(["tmux", "kill-pane", "-t", pane_id],
                           capture_output=True, timeout=5)
            killed += 1
        except (subprocess.TimeoutExpired, OSError):
            pass
    return killed


def _check_active_teams(project_root: str) -> dict:
    """Check if any registered teams still have directories OR live tmux panes.

    Two-layer check:
    1. Team directory exists in ~/.claude/teams/ (TeamDelete wasn't called)
    2. Live teammate tmux panes exist (teammates haven't exited yet)

    BOTH must be clear for the gate to pass. This prevents the lead from
    progressing to the next phase while teammates are still running.
    """
    fdir = get_run_dir(project_root)
    if not fdir:
        return {"active": False, "teams": [], "live_panes": []}
    state = _load_json(fdir / "state.json")
    teams = state.get("active_teams", [])

    teams_dir = Path.home() / ".claude" / "teams"
    active = [t for t in teams if (teams_dir / t).is_dir()]

    # Also check for live teammate tmux panes — even if TeamDelete was called,
    # the claude processes might still be running
    live_panes = []
    scan = _scan_tmux_panes()
    if scan["available"] and scan["live"]:
        live_panes = [title for _, title, _ in scan["live"]]

    is_active = len(active) > 0 or len(live_panes) > 0

    result: dict = {"active": is_active, "teams": active, "live_panes": live_panes}
    if live_panes and not active:
        result["hint"] = (
            f"{len(live_panes)} teammate pane(s) still running: {', '.join(live_panes)}. "
            "Send 'All work complete, stop working.' to each teammate in a parallel SendMessage batch, "
            "then TeamDelete immediately \u2014 do NOT wait for acks. "
            "If panes won't terminate, run: tmux kill-pane -t <pane_id>"
        )
    return result


def foundry_register_team(
    team_name: str,
    project_root: str = ".",
) -> dict:
    """Register a team for lifecycle tracking."""
    fdir = get_run_dir(project_root)
    if not fdir:
        return {"error": "No active foundry run. Call Foundry-Init first."}
    state_path = fdir / "state.json"
    state = _load_json(state_path)

    teams = state.get("active_teams", [])

    teams_dir = Path.home() / ".claude" / "teams"
    still_active = [t for t in teams if t != team_name and (teams_dir / t).is_dir()]
    if still_active:
        return {
            "error": f"Cannot register '{team_name}' \u2014 active teams exist: {', '.join(still_active)}",
            "hint": "Shut down existing teammates (SendMessage + TeamDelete) and Foundry-Team-Down before creating a new team. One team at a time.",
            "active_teams": still_active,
        }

    if team_name not in teams:
        teams.append(team_name)
    state["active_teams"] = teams
    _save_json(state_path, state)

    return {"ok": True, "registered": team_name, "total_teams": len(teams)}


def foundry_unregister_team(
    team_name: str,
    project_root: str = ".",
) -> dict:
    """Unregister a team with verified teardown.

    Three-phase verification:
    1. CHECK: team directory gone (TeamDelete was called)
    2. CHECK: no live claude processes in non-lead panes
    3. CLEAN: kill zombie panes (dead + idle shells)
    4. UNREGISTER: remove from foundry state

    Blocks if steps 1 or 2 fail — forces proper shutdown ordering.
    """
    import time

    fdir = get_run_dir(project_root)
    if not fdir:
        return {"error": "No active foundry run."}

    # ── Phase 1: Verify TeamDelete was called ────────────────────────
    teams_dir = Path.home() / ".claude" / "teams"
    if (teams_dir / team_name).is_dir():
        return {
            "error": f"Team directory still exists: ~/.claude/teams/{team_name}/",
            "hint": (
                "TeamDelete must be called BEFORE Foundry-Team-Down. "
                "Proper order: SendMessage(shutdown) to each teammate in ONE parallel batch -> "
                "TeamDelete immediately (do NOT wait for shutdown acks \u2014 idle panes ARE the signal) "
                "-> Foundry-Team-Down."
            ),
            "phase": "team_dir_exists",
        }

    # ── Phase 2: Verify no live teammate processes ───────────────────
    scan = _scan_tmux_panes()
    if scan["available"] and scan["live"]:
        live_titles = [title for _, title, _cmd in scan["live"]]
        return {
            "error": f"{len(scan['live'])} teammate pane(s) still running: {', '.join(live_titles)}",
            "hint": (
                "Teammates are still alive \u2014 they have active claude processes. "
                "Send 'All work complete, stop working.' to each teammate (parallel SendMessage), "
                "then TeamDelete immediately (do NOT wait for acks). Re-run Foundry-Team-Down after."
            ),
            "phase": "live_teammates",
            "live_panes": live_titles,
        }

    # ── Phase 3: Kill zombie panes ───────────────────────────────────
    killed = 0
    if scan["available"] and scan["zombie"]:
        killed = _kill_panes(scan["zombie"])
        # Brief wait + re-scan to verify
        time.sleep(1)
        rescan = _scan_tmux_panes()
        remaining_zombie = len(rescan.get("zombie", []))
        remaining_live = len(rescan.get("live", []))
        if remaining_zombie > 0 or remaining_live > 0:
            # Retry once
            if rescan.get("zombie"):
                killed += _kill_panes(rescan["zombie"])
            time.sleep(1)
            rescan = _scan_tmux_panes()
            remaining_zombie = len(rescan.get("zombie", []))
            remaining_live = len(rescan.get("live", []))
            if remaining_zombie > 0 or remaining_live > 0:
                return {
                    "error": (
                        f"Panes still alive after cleanup: "
                        f"{remaining_live} live, {remaining_zombie} zombie. "
                        "Kill manually: tmux kill-server"
                    ),
                    "phase": "cleanup_failed",
                    "killed": killed,
                }

    # ── Phase 4: Unregister from foundry state ───────────────────────
    state_path = fdir / "state.json"
    state = _load_json(state_path)
    teams = state.get("active_teams", [])
    teams = [t for t in teams if t != team_name]
    state["active_teams"] = teams
    _save_json(state_path, state)

    return {
        "ok": True,
        "unregistered": team_name,
        "remaining_teams": len(teams),
        "tmux_panes_killed": killed,
        "verified_clean": True,
    }


# --- SIGHT enforcement ---


def _check_sight_required(project_root: str) -> dict:
    """Check if SIGHT audit is required based on frontend files in castings."""
    fdir = get_run_dir(project_root)
    if not fdir:
        return {"required": False}
    manifest = fdir / "castings" / "manifest.json"

    if not manifest.exists():
        return {"required": False}

    data = _load_json(manifest)
    ui_exts = {".tsx", ".jsx", ".vue", ".svelte", ".css", ".scss", ".html", ".astro"}

    ui_files = []
    for casting in data.get("castings", []):
        for f in casting.get("key_files", []):
            if any(f.endswith(ext) for ext in ui_exts):
                ui_files.append(f)

    if not ui_files:
        return {"required": False, "reason": "No frontend files in castings"}

    url = data.get("target_url", "")
    no_ui = data.get("no_ui", False)

    if no_ui:
        return {"required": True, "blocked": True, "ui_files": len(ui_files),
                "reason": f"--no-ui set but {len(ui_files)} frontend files in scope"}
    if not url:
        return {"required": True, "blocked": True, "ui_files": len(ui_files),
                "reason": f"No --url provided but {len(ui_files)} frontend files in scope"}

    return {"required": True, "blocked": False, "url": url, "ui_files": len(ui_files)}


# --- Defect lifecycle ---


def foundry_mark_defect_fixed(
    defect_id: str,
    cycle: int,
    project_root: str = ".",
) -> dict:
    """Mark a defect as fixed in this cycle."""
    fdir = get_run_dir(project_root)
    if not fdir:
        return {"error": "No active foundry run."}
    defects_path = fdir / "defects.json"
    data = _load_json(defects_path)

    found = False
    for d in data.get("defects", []):
        if d["id"] == defect_id:
            d["status"] = "fixed"
            d["fixed_in_cycle"] = cycle
            found = True
            break

    if not found:
        return {"error": f"Defect {defect_id} not found"}

    _save_json(defects_path, data)

    forge_log = fdir / "forge-log.md"
    if forge_log.exists():
        with open(forge_log, "a", encoding="utf-8") as f:
            f.write(f"\n**{defect_id} FIXED** in cycle {cycle} ({_now()})\n\n")

    open_count = sum(1 for d in data["defects"] if d.get("status") == "open")
    return {"ok": True, "defect_id": defect_id, "fixed_in_cycle": cycle, "remaining_open": open_count}


def foundry_sync_defects(
    cycle: int,
    findings: list[dict],
    project_root: str = ".",
) -> dict:
    """Sync new findings against existing defects. Detects regressions."""
    fdir = get_run_dir(project_root)
    if not fdir:
        return {"error": "No active foundry run."}
    defects_path = fdir / "defects.json"
    data = _load_json(defects_path)
    if "defects" not in data:
        data["defects"] = []

    fixed = [d for d in data["defects"] if d.get("status") == "fixed"]
    reopened = 0
    added = 0
    regressions: list[str] = []

    for finding in findings:
        symbol = finding.get("symbol", "")
        desc = finding.get("description", "")

        match_id = None
        for fd in fixed:
            fd_sym = fd.get("symbol", "")
            fd_desc = fd.get("description", "")
            if symbol and fd_sym and symbol == fd_sym:
                match_id = fd["id"]
                break
            if desc and fd_desc and desc == fd_desc:
                match_id = fd["id"]
                break

        if match_id:
            for d in data["defects"]:
                if d["id"] == match_id:
                    d["status"] = "open"
                    d["regression"] = True
                    d["reopened_in_cycle"] = cycle
                    d["fixed_in_cycle"] = None
                    break
            reopened += 1
            regressions.append(match_id)
        else:
            defect_id = f"D-{len(data['defects']) + 1:03d}"
            source = finding.get("source", "inspect")
            valid_sources = {"trace", "prove", "sight", "test", "assay", "temper"}
            if source not in valid_sources:
                source = "trace"

            defect = {
                "id": defect_id,
                "cycle": cycle,
                "source": source,
                "type": finding.get("type", "MISSING"),
                "description": desc,
                "spec_ref": finding.get("spec_ref", ""),
                "symbol": symbol,
                "file": finding.get("file", ""),
                "status": "open",
                "fixed_in_cycle": None,
                "created_at": _now(),
            }
            data["defects"].append(defect)
            added += 1

    _save_json(defects_path, data)

    if regressions:
        forge_log = fdir / "forge-log.md"
        if forge_log.exists():
            with open(forge_log, "a", encoding="utf-8") as f:
                f.write(f"\n### REGRESSIONS in cycle {cycle}\n")
                for r in regressions:
                    f.write(f"- **{r}** reopened \u2014 fix was fragile\n")
                f.write("\n")

    return {
        "ok": True,
        "cycle": cycle,
        "added": added,
        "reopened": reopened,
        "regressions": regressions,
        "total_open": sum(1 for d in data["defects"] if d.get("status") == "open"),
    }


def foundry_defects_to_tasks(
    project_root: str = ".",
) -> dict:
    """Convert ALL open defects to grouped task descriptions for GRIND."""
    fdir = get_run_dir(project_root)
    if not fdir:
        return {"error": "No active foundry run."}
    data = _load_json(fdir / "defects.json")
    open_defects = [d for d in data.get("defects", []) if d.get("status") == "open"]

    if not open_defects:
        return {"ok": True, "tasks": [], "count": 0}

    MAX_PER_GROUP = 3
    groups: dict[str, list[dict]] = {}
    for d in open_defects:
        key = d.get("file") or d.get("symbol") or d["id"]
        groups.setdefault(key, []).append(d)

    tasks = []
    for key, defects in groups.items():
        for i in range(0, len(defects), MAX_PER_GROUP):
            chunk = defects[i:i + MAX_PER_GROUP]
            task = {
                "defect_ids": [d["id"] for d in chunk],
                "description": "; ".join(d["description"] for d in chunk),
                "files": list({d["file"] for d in chunk if d.get("file")}),
                "symbols": list({d["symbol"] for d in chunk if d.get("symbol")}),
                "spec_refs": list({d["spec_ref"] for d in chunk if d.get("spec_ref")}),
                "regression": any(d.get("regression") for d in chunk),
                "source": chunk[0].get("source", "unknown"),
            }
            tasks.append(task)

    (fdir / ".tasks-generated").write_text(f"{_now()} count={len(tasks)}\n", encoding="utf-8")

    return {"ok": True, "tasks": tasks, "count": len(tasks)}


# --- The big one: next action ---


def _stamp_subphase_transitions(fdir: Path) -> None:
    """Auto-stamp F0 / F0.5 / F0.9 transitions based on file-state signals.

    The lead's `state.phase` stays "F0" through RESEARCH / DECOMPOSE / VALIDATE
    and jumps straight to "F1" on start_cast, so without this stamper the
    pre-F1 ~13 minutes appear as one unstructured block. Here we observe:

      - first `castings/casting-*.md` appearing → F0 ends, F0.5 starts
      - `castings/manifest.json` appearing      → F0.5 ends, F0.9 starts
      - `.validate-passed` marker               → F0.9 end time recorded
        (sub-phase still "open" until _update_phase fires at start_cast;
        the marker lets us report validator pass time separately)

    Called from foundry_next_action so every `Foundry-Next` call picks up
    transitions that happened since the last call. Idempotent — only
    writes when a new transition is detected.
    """
    if not fdir or not fdir.exists():
        return
    state_path = fdir / "state.json"
    if not state_path.exists():
        return
    state = _load_json(state_path)
    phase_times = state.get("phase_times", {})
    now = _now()
    changed = False

    castings_dir = fdir / "castings"
    has_casting_files = castings_dir.exists() and any(castings_dir.glob("casting-*.md"))
    has_manifest = (castings_dir / "manifest.json").exists()
    validate_passed_marker = fdir / ".validate-passed"

    def _close(pid: str) -> bool:
        entry = phase_times.get(pid)
        if entry and "started_at" in entry and "ended_at" not in entry:
            _finalize_open_phase_entry(entry, now)
            return True
        return False

    def _open(pid: str) -> bool:
        if pid not in phase_times:
            phase_times[pid] = {"started_at": now}
            return True
        return False

    if has_casting_files:
        changed |= _close("F0")
        changed |= _open("F0.5")
    if has_manifest:
        changed |= _close("F0.5")
        changed |= _open("F0.9")
    if validate_passed_marker.exists():
        entry = phase_times.get("F0.9")
        if entry and "validate_passed_at" not in entry:
            try:
                entry["validate_passed_at"] = validate_passed_marker.read_text(encoding="utf-8").strip() or now
            except OSError:
                entry["validate_passed_at"] = now
            changed = True

    if changed:
        state["phase_times"] = phase_times
        _save_json(state_path, state)


def foundry_next_action(
    project_root: str = ".",
) -> dict:
    """Determine what the lead should do next based on current foundry state."""
    fdir_stamp = get_run_dir(project_root)
    trace_skip_decision: dict | None = None
    if fdir_stamp and fdir_stamp.exists():
        _stamp_subphase_transitions(fdir_stamp)
        trace_skip_decision = _maybe_skip_trace(fdir_stamp, project_root)
    result = _compute_next_action(project_root)
    if trace_skip_decision and trace_skip_decision.get("skip"):
        result["trace_skip"] = trace_skip_decision

    # P4 (FR-005 / ST-002): passing-gate → guidance-state advance. If the gate
    # for the current transition action already passed (recorded in
    # ``.gate-passed`` by foundry_gate), surface that the gate is satisfied so
    # the lead proceeds to the transition step rather than re-running the gate.
    gate_advance_note = None
    if fdir_stamp and fdir_stamp.exists():
        expected_gate = _expected_gate_for_action(result.get("action", ""))
        if expected_gate:
            gp_marker = fdir_stamp / ".gate-passed"
            if gp_marker.exists():
                try:
                    gp_data = json.loads(gp_marker.read_text(encoding="utf-8"))
                except (json.JSONDecodeError, OSError):
                    gp_data = {}
                if gp_data.get("phase") == expected_gate:
                    result["gate_advanced"] = {
                        "passed_gate": expected_gate,
                        "action": result.get("action", ""),
                    }
                    gate_advance_note = (
                        f"✅ Foundry-Gate(phase='{expected_gate}') ALREADY "
                        f"PASSED — do NOT re-run it. Proceed directly to the "
                        f"transition step (Foundry-Phase / state update) in the "
                        f"imperative below."
                    )

    # Stall watchdog. Read the previous `.last-next-at` timestamp BEFORE
    # overwriting it, compute the delta, and if the gap is large surface a
    # visible STALL WARNING at the very top of the instructions. This converts
    # silent extended-thinking runaway into an explicit, logged event the lead
    # must acknowledge on its next turn. State tracking via the existing MCP
    # tool — no hooks.
    #
    # P4 (FR-005 / FR-008): the stall timestamp lives in its OWN marker
    # (``.last-next-at``), decoupled from the ``.next-action-called`` ordering
    # token that foundry_gate / foundry_mark_phase_complete unlink. Because
    # gate/phase no longer destroy the stall timestamp, the stall clock keeps
    # measuring true Foundry-Next → Foundry-Next gaps across intervening
    # gate/phase/read-only calls, so real stalls still warn (FR-008) while
    # ordering-token consumption no longer blinds the watchdog.
    stall_warning = None
    fdir_stall = get_run_dir(project_root)
    if fdir_stall and fdir_stall.exists():
        marker = fdir_stall / ".last-next-at"
        if marker.exists():
            try:
                prev_iso = marker.read_text(encoding="utf-8").strip()
                prev = datetime.fromisoformat(prev_iso)
                delta = (datetime.now(timezone.utc) - prev).total_seconds()
                if delta >= 180:  # 3 minutes
                    minutes = int(delta // 60)
                    seconds = int(delta % 60)
                    stall_warning = (
                        f"\u26a0\ufe0f STALL DETECTED: {minutes}m {seconds}s since your last Foundry-Next call. "
                        f"You were silently deliberating. Stop deliberating. Execute the imperative below "
                        f"literally. Do NOT re-read start.md, do NOT run a compliance checklist, do NOT "
                        f"think through edge cases — just run the next tool call. If the imperative is "
                        f"ambiguous, pick any reasonable interpretation and proceed."
                    )
                    result["stall_detected_seconds"] = int(delta)
            except (ValueError, OSError):
                pass

    # Sharpened imperative — lead-line structure. Extract the first actionable
    # call from the computed instructions and emit it as a "YOUR NEXT CALL"
    # header. Context stays in the body for when the lead needs it, but the
    # first line is a single command.
    action = result.get("action", "")
    original_instructions = result.get("instructions", "")
    run_name_for_imperative = fdir_stall.name if fdir_stall and fdir_stall.exists() else ""
    imperative_header = _format_imperative_header(
        action, original_instructions, result.get("details", {}), run_name=run_name_for_imperative
    )

    directives = _read_directives(project_root)
    directive_block = ""
    if directives["has_directives"]:
        result["directives"] = {
            "urgent": directives["urgent"],
            "normal": directives["normal"],
        }
        if directives["urgent"]:
            urgent_text = " | ".join(directives["urgent"])
            directive_block = f"\n\nHUMAN DIRECTIVE (urgent): {urgent_text}\n\nIncorporate the above into your current action."
        elif directives["normal"]:
            normal_text = " | ".join(directives["normal"])
            directive_block = f"\n\nHUMAN DIRECTIVE: {normal_text} \u2014 incorporate into your approach."

    critical_rules = (
        "\n\nCRITICAL RULES:"
        "\n- NEVER ask 'Want me to proceed?' or 'Should I continue?' \u2014 just do it."
        "\n- NEVER stop between phases. Call Foundry-Next after each step and follow it."
        "\n- NEVER deliberate for more than 30 seconds between tool calls. If you catch yourself thinking, call Foundry-Next and execute whatever it says."
        "\n- NEVER narrate progress as 'Checkpoint \u2014 X complete', 'Checkpoint reached', 'Milestone \u2014 X', or similar. Foundry has NO checkpoints. You are not a checkpointing orchestrator. Execute the next tool call silently and keep moving."
        "\n- NEVER skip SIGHT because 'no URL.' If frontend files exist, you need a URL. Gate will block."
        "\n- NEVER spawn foundry:teammate agents (CAST or GRIND) with run_in_background=true. They are foreground, TeamCreate-managed, and must run through Foundry-Cast-Wave or Foundry-Spawn-Teammate + verbatim Agent. Background-spawning bypasses the router architecture and breaks spec fidelity."
        "\n- NEVER modify, paraphrase, or augment a prompt returned by Foundry-Spawn-Teammate. Pass it to Agent VERBATIM. GRIND is the only exception: append (a) the `grind_cycle_context` block if returned (prior-cycle file changes) and (b) the '## Defects to fix this cycle:' block BELOW the prompt, in that order. Never inside the prompt."
        "\n- If the user typed a message, treat it as a directive. Absorb and keep going."
        "\n- Zero approval gates. The foundry runs until F6 DONE or an error stops it."
        "\n- NEVER wait for teammate 'shutdown_response', 'shutdown_ack', idle-confirmation, or any reply after "
        "issuing shutdown. The ONLY shutdown signals foundry recognizes are (a) TeamDelete returning ok and "
        "(b) Foundry-Team-Down succeeding. Narrating 'awaiting shutdown approvals' is a stall \u2014 call TeamDelete "
        "immediately. Idle / terminated panes ARE the signal; TeamDelete cleans them."
    )

    # Assemble instructions with stable-first ordering for prompt caching.
    # Every Foundry-Next response is a user-turn message in the lead's single
    # conversation. A stable byte-identical prefix across calls is cache-hit-
    # eligible; the lead calls Foundry-Next ~30-50 times per run, so emitting
    # rules + framing FIRST (before the volatile imperative/CONTEXT/directives)
    # maximizes cache hits on input tokens.
    #
    # Lead attention is preserved by the explicit "═══ YOUR NEXT ACTION ═══"
    # marker: after the rules block, the imperative header's "YOUR NEXT CALL"
    # / "YOUR NEXT CALLS" lead-line remains the action-scanning target that
    # the lead has been trained to find.
    parts = [critical_rules.lstrip()]
    parts.append("\n═══ YOUR NEXT ACTION ═══\n")
    if stall_warning:
        parts.append(stall_warning)
    if gate_advance_note:
        parts.append(gate_advance_note)
    parts.append(imperative_header)
    parts.append("")
    parts.append("CONTEXT:")
    parts.append(original_instructions)
    if directive_block:
        parts.append(directive_block)
    result["instructions"] = "\n".join(parts)

    # Context budget tracking
    fdir_cb = get_run_dir(project_root)
    if fdir_cb and fdir_cb.exists():
        state_cb = _load_json(fdir_cb / "state.json")
        cycle = state_cb.get("cycle", 0)
        # Estimate context usage based on cycle count
        if cycle >= 3:
            usage = "critical"
        elif cycle >= 2:
            usage = "high"
        elif cycle >= 1:
            usage = "moderate"
        else:
            usage = "low"
        result["context_budget"] = {
            "cycles_completed": cycle,
            "estimated_usage": usage,
            "recommendation": "Consider /clear and /foundry:resume if quality is degrading" if usage in ("high", "critical") else "Context budget healthy",
        }

    result["display"] = _format_status_display(project_root)

    fdir = get_run_dir(project_root)
    if fdir and fdir.exists():
        now_stamp = f"{_now()}\n"
        # Ordering token: armed here, consumed (unlinked) by foundry_gate /
        # foundry_mark_phase_complete to prove Foundry-Next preceded a gate
        # or phase transition.
        (fdir / ".next-action-called").write_text(now_stamp, encoding="utf-8")
        # Stall timestamp: written on EVERY Foundry-Next, read on the NEXT
        # Foundry-Next to measure the gap. Never unlinked by gate/phase, so
        # the watchdog is decoupled from ordering-token consumption (FR-005 /
        # FR-008). Read-only intervening calls do not touch it.
        (fdir / ".last-next-at").write_text(now_stamp, encoding="utf-8")

    return result


# Action → imperative-header map. Each action returned by
# _compute_next_action maps to a "YOUR NEXT CALL(S)" directive that the lead
# can execute without re-reading paragraph instructions. Multi-step actions
# MUST enumerate every call — compressing a multi-step sequence into a
# single-line imperative causes the lead to follow the first tool call
# literally and improvise the rest by guessing.
_ACTION_IMPERATIVES = {
    "init": "YOUR NEXT CALL: Foundry-Init (start a new run)",
    "cleanup_teams": (
        "YOUR NEXT CALLS (in order \u2014 do NOT wait for shutdown acks):\n"
        "  (1) Send shutdown to each teammate: SendMessage(to=<teammate>, message='All work complete, stop working.') "
        "\u2014 one SendMessage per teammate in ONE parallel-tool-use message. Do not use structured messages with "
        "to='*' broadcast \u2014 broadcast rejects structured payloads.\n"
        "  (2) Immediately call TeamDelete for each active team. Do NOT wait for 'shutdown_response' events, "
        "'shutdown_ack' events, idle confirmations, or any teammate reply. Idle / terminated panes ARE the "
        "shutdown signal. TeamDelete cleans zombie panes.\n"
        "  (3) Foundry-Team-Down for each team name.\n"
        "Stalling here is the #1 cleanup failure mode: the lead sends shutdown, sees panes idle, and waits "
        "forever for a reply that never comes."
    ),
    "add_castings": (
        "YOUR NEXT CALL: Spawn 1-5 BACKGROUND Agents in a SINGLE parallel message \u2014 one per "
        "domain identified from the spec. Per-Agent params: model='opus', "
        "subagent_type='general-purpose', mode='bypassPermissions', run_in_background=true, "
        "prompt=<per commands/start.md \u00a7F0.5 DECOMPOSE: write the domain's entry into "
        "manifest.json AND write casting-{id}-prompt.md to foundry-archive/{run}/castings/ "
        "following the layout in start.md \u00a76>. "
        "No team needed \u2014 these are short-lived file writers; TeamCreate ceremony is skipped. "
        "You'll be notified as each completes; use TaskOutput(task_id) to retrieve any return "
        "message. After all complete, call Foundry-Validate-Castings."
    ),
    "transition_to_cast": (
        "YOUR NEXT CALLS (in order — bulk flow saves N-1 roundtrips):\n"
        "  (1) Foundry-Gate(phase='cast')\n"
        "  (2) Foundry-Phase(phase='start_cast')\n"
        "  (3) TeamCreate('cast-{run}-wave-1')\n"
        "  (4) Foundry-Team-Up(team_name='cast-{run}-wave-1')\n"
        "  (5) Foundry-Cast-Wave(wave=1, phase='cast') \u2014 returns ALL prompts for wave 1 in ONE call.\n"
        "  (6) In a SINGLE message (parallel tool use), spawn one Agent per returned casting: "
        "subagent_type='foundry:teammate', mode='bypassPermissions', "
        "prompt=<that casting's prompt text VERBATIM \u2014 no edits>. "
        "(foundry:teammate's frontmatter carries model=opus + effort=xhigh + all tools.) "
        "Do NOT send multiple messages with one Agent each \u2014 that serializes what should be parallel.\n"
        "Rules still apply: NEVER run_in_background=true for foundry:teammate. NEVER "
        "subagent_type='Explore' or 'general-purpose' for CAST. F0.5 DECOMPOSE uses background "
        "general-purpose Agents; F2 INSPECT and F4 ASSAY use named agents (foundry:tracer, "
        "foundry:assayer, foundry:research-auditor, foundry:coverage-diff) whose frontmatter "
        "carries model/effort/tools."
    ),
    "build_castings": (
        "YOUR NEXT ACTION depends on wave state:\n"
        "  - IF no CAST team has been registered this wave yet (first entry to F1): follow the transition_to_cast sequence "
        "(TeamCreate \u2192 Foundry-Team-Up \u2192 Foundry-Spawn-Teammate per casting \u2192 Agent spawn VERBATIM, foreground).\n"
        "  - IF teammates are currently running: WAIT for all to complete, then TeamDelete + Foundry-Team-Down + "
        "Foundry-Phase(phase='cast'). Do NOT call Foundry-Next while waiting \u2014 it will re-emit this action."
    ),
    "transition_to_inspect": "YOUR NEXT CALL: Foundry-Gate(phase='inspect')",
    "run_streams": (
        "YOUR NEXT CALLS: spawn every missing INSPECT stream in a SINGLE parallel message. Stream-specific rules:\n"
        "All four streams spawn as BACKGROUND Agents (run_in_background=true) so SIGHT can run "
        "concurrently in the main thread instead of the main thread blocking on tool_results:\n"
        "  - TRACE: Agent(subagent_type='foundry:tracer', run_in_background=true, prompt='Run TRACE wiring verification for the active foundry run.')\n"
        "  - PROVE: Agent(subagent_type='foundry:assayer', run_in_background=true, prompt='Run PROVE (spec-to-code citation verification) for the active foundry run.')\n"
        "  - RESEARCH_AUDIT: Agent(subagent_type='foundry:research-auditor', run_in_background=true, prompt='Run RESEARCH_AUDIT for the active foundry run.')\n"
        "  - COVERAGE_DIFF (MIGRATION only): Agent(subagent_type='foundry:coverage-diff', run_in_background=true, prompt='Run COVERAGE_DIFF for the active foundry run.')\n"
        "  - SIGHT: runs in MAIN THREAD via Playwright \u2014 execute while the four background streams run\n"
        "  - TEST / PROBE: may also run as background Agents\n"
        "When each background stream's completion notification fires: call TaskOutput(task_id) "
        "to retrieve its findings, then call Foundry-Stream(stream, cycle, items_checked, "
        "items_total, findings_count) with the parsed counts. Do NOT poll \u2014 the harness notifies you."
    ),
    "transition_to_grind": (
        "YOUR NEXT CALLS (in order):\n"
        "  (1) Foundry-Tasks\n"
        "  (2) Foundry-Gate(phase='grind')\n"
        "  (3) Foundry-Phase(phase='grind_start')\n"
        "  (4) TeamCreate('grind-{run}-cycle-N')\n"
        "  (5) Foundry-Team-Up(team_name='grind-{run}-cycle-N')\n"
        "  (6) For each casting with open defects: Foundry-Spawn-Teammate(casting_id=N, phase='grind')\n"
        "  (7) Spawn Agent(subagent_type='foundry:teammate', mode='bypassPermissions', "
        "prompt=<returned prompt VERBATIM, then APPEND (a) the `grind_cycle_context` block from the spawn "
        "response if present \u2014 lists files changed in prior cycles so the teammate reads current state "
        "before acting, then (b) the defect list in a '## Defects to fix this cycle:' block. Order: prompt \u2192 "
        "cycle_context \u2192 defects. Both appended BELOW the prompt, never inside it.>). "
        "Same foreground rule as CAST \u2014 never background-spawn GRIND teammates."
    ),
    "fix_defects": (
        "YOUR NEXT ACTION depends on GRIND state:\n"
        "  - IF no GRIND team registered yet: follow the transition_to_grind sequence.\n"
        "  - IF teammates are running: WAIT. When all report complete, TeamDelete + Foundry-Team-Down + "
        "update state to F2 + re-run INSPECT."
    ),
    "transition_to_assay": (
        "YOUR NEXT CALLS (in order):\n"
        "  (1) Foundry-Phase(phase='inspect_clean')\n"
        "  (2) Foundry-Gate(phase='assay')\n"
        "  (3) Update state to F4\n"
        "  (4) Spawn 4 parallel Agent(subagent_type='foundry:assayer', "
        "prompt='Assay requirement group N of 4 for the active foundry run. "
        "Spec-before-code; default posture is find the failure.') in a SINGLE message. "
        "(The assayer's frontmatter carries model=opus and effort=max.)"
    ),
    "run_assay": (
        "YOUR NEXT CALL: spawn 4 parallel Agent(subagent_type='foundry:assayer', "
        "prompt='Assay requirement group N of 4 for the active foundry run. "
        "Spec-before-code; default posture is find the failure.') in a SINGLE message. "
        "Each reads the spec FIRST, forms expectations, then reads code. "
        "(The assayer's frontmatter carries model=opus and effort=max.)"
    ),
    "transition_to_done": "YOUR NEXT CALL: Foundry-Phase(phase='done')",
}


def _format_imperative_header(action: str, instructions: str, details: dict, run_name: str = "") -> str:
    """Produce the one-line 'YOUR NEXT CALL' header for the given action.
    Falls back to a generic header if the action is unmapped.

    Substitutes `{run}` in the imperative with the active run slug so team
    names (cast-{run}-wave-N, grind-{run}-cycle-N) are distinguishable across
    concurrent runs. DECOMPOSE no longer uses a team — it spawns background
    Agents (per commands/start.md \u00a7F0.5).
    If no run is active, `{run}` is replaced with `active` as a safe default.
    """
    imperative = _ACTION_IMPERATIVES.get(action)
    if imperative:
        return imperative.replace("{run}", run_name or "active")
    return f"YOUR NEXT CALL: follow the CONTEXT below (action='{action}'). Execute the first tool call mentioned. Do not deliberate."


def _format_status_display(project_root: str) -> str:
    """Generate foundry status display with pixel-art hammer header."""
    fdir = get_run_dir(project_root)
    if not fdir or not fdir.exists():
        return ""

    state = _load_json(fdir / "state.json")
    phase = state.get("phase", "F0")
    phase_times = state.get("phase_times", {})
    started = state.get("started_at", "")
    cycle = state.get("cycle", 0)

    elapsed = ""
    if started:
        try:
            start = datetime.fromisoformat(started)
            now = datetime.now(timezone.utc)
            delta = now - start
            elapsed_secs = int(delta.total_seconds())
            h = elapsed_secs // 3600
            m = (elapsed_secs % 3600) // 60
            s = elapsed_secs % 60
            if h > 0:
                elapsed = f"{h}h {m}m {s}s"
            elif m > 0:
                elapsed = f"{m}m {s}s"
            else:
                elapsed = f"{s}s"
        except ValueError:
            pass

    phases = [
        ("F0", "RESEARCH"), ("F0.5", "DECOMPOSE"), ("F0.9", "VALIDATE"),
        ("F1", "CAST"), ("F2", "INSPECT"),
        ("F3", "GRIND"), ("F4", "ASSAY"), ("F5", "TEMPER"),
        ("F5.5", "NYQUIST"), ("F6", "DONE"),
    ]

    phase_names = dict(phases)
    phase_name = phase_names.get(phase, phase)
    run_name = fdir.name

    lines = [foundry_hammer(f"F O U N D R Y  {_BCYAN}{phase} {phase_name}{_RESET}  Cycle: {cycle}  {elapsed}")]

    # Phase list
    for pid, pname in phases:
        timing = phase_times.get(pid, {})
        dur = timing.get("duration", "")

        if pid == phase:
            icon = f"{_BGREEN}\u25b6{_RESET}"
            label = f"{_BWHITE}{pid} {pname}{_RESET}"
            right = f"  {_BGREEN}\u25c0 {elapsed}{_RESET}"
        elif dur or timing.get("started_at"):
            icon = f"{_GREEN}\u2713{_RESET}"
            label = f"{_DIM}{pid} {pname}{_RESET}"
            right = f"  {_DIM}{dur}{_RESET}" if dur else ""
        elif pid == "F5" and not state.get("temper", False):
            icon = f"{_DIM}\u2500{_RESET}"
            label = f"{_DIM}{pid} {pname}{_RESET}"
            right = f"  {_DIM}skip{_RESET}"
        else:
            icon = f"{_DIM}\u25cb{_RESET}"
            label = f"{_DIM}{pid} {pname}{_RESET}"
            right = ""

        lines.append(f"  {icon} {label}{right}")

    # Defects
    defects = _load_json(fdir / "defects.json")
    all_d = defects.get("defects", [])
    open_d = sum(1 for d in all_d if d.get("status") == "open")
    fixed_d = sum(1 for d in all_d if d.get("status") == "fixed")
    regressed = sum(1 for d in all_d if d.get("regression"))

    if all_d:
        defect_line = f"  {_BWHITE}Defects:{_RESET} {_BYELLOW}{open_d} open{_RESET}  {_BGREEN}{fixed_d} fixed{_RESET}"
        if regressed:
            defect_line += f"  {_BRED}{regressed} regressed{_RESET}"
        lines.append(defect_line)

    # Verdicts
    verdicts = _load_json(fdir / "verdicts.json")
    reqs = verdicts.get("requirements", [])
    if reqs:
        verified = sum(1 for r in reqs if r.get("verdict") == "VERIFIED")
        v_bar_len = 15
        v_filled = int((verified / len(reqs)) * v_bar_len) if reqs else 0
        v_bar = f"{_BGREEN}{'\u2588' * v_filled}{_DIM}{'\u2591' * (v_bar_len - v_filled)}{_RESET}"
        lines.append(f"  {_BWHITE}Verdicts:{_RESET} {v_bar} {verified}/{len(reqs)}")

    # Streams
    streams = _check_streams_complete(project_root)
    if phase in ("F2", "F4") or streams.get("required"):
        req_streams = streams.get("required", [])
        missing_s = streams.get("missing", "").split()
        stream_icons = []
        for s in ["trace", "prove", "sight", "test", "probe"]:
            if s in req_streams:
                if s not in missing_s:
                    stream_icons.append(f"[{_GREEN}\u2713{_RESET}]{s}")
                else:
                    stream_icons.append(f"[{_DIM} {_RESET}]{s}")
        if stream_icons:
            lines.append(f"  {_BWHITE}Streams:{_RESET}  {' '.join(stream_icons)}")

    # Teams
    teams = _check_active_teams(project_root)
    if teams["active"]:
        team_str = ", ".join(teams["teams"])
        if len(team_str) > 40:
            team_str = team_str[:37] + "..."
        lines.append(f"  {_BWHITE}Teams:{_RESET}    {_BCYAN}{team_str}{_RESET}")

    lines.append(FOUNDRY_SEP)

    return "\n".join(lines)


def _compute_next_action(project_root: str) -> dict:
    """Internal: compute next action without directive overlay."""
    fdir = get_run_dir(project_root)

    if not fdir or not fdir.exists():
        return {
            "phase": "none",
            "action": "init",
            "instructions": "No active foundry run. Call Foundry-Init to start a new run, or foundry_init(resume='run-name') to resume.",
            "details": {},
        }

    state = _load_json(fdir / "state.json")
    phase = state.get("phase", "F0")

    teams = _check_active_teams(project_root)
    if teams["active"]:
        return {
            "phase": phase,
            "action": "cleanup_teams",
            "instructions": (
                f"Active teams detected: {', '.join(teams['teams'])}. "
                "Send 'All work complete, stop working.' to each teammate in ONE parallel SendMessage batch, "
                "then IMMEDIATELY call TeamDelete for each team \u2014 do NOT wait for shutdown_response, "
                "shutdown_ack, idle confirmations, or any teammate reply. Idle / terminated panes ARE the "
                "shutdown signal. TeamDelete cleans lingering tmux panes. Then Foundry-Team-Down for each team name."
            ),
            "details": {"active_teams": teams["teams"]},
        }

    defects = _load_json(fdir / "defects.json")
    open_count = sum(1 for d in defects.get("defects", []) if d.get("status") == "open")

    # --- Agent config per phase (ENFORCED, not suggestions) ---
    # These are the exact parameters the lead MUST use when spawning agents.
    CAST_AGENT_CONFIG = {
        "subagent_type": "foundry:teammate",
        "mode": "bypassPermissions",
    }
    DECOMPOSE_AGENT_CONFIG = {
        "model": "opus",
        "subagent_type": "general-purpose",
        "mode": "bypassPermissions",
        "run_in_background": True,
    }
    INSPECT_TRACE_CONFIG = {
        "subagent_type": "foundry:tracer",
        "run_in_background": True,
        "description": "TRACE: LSP wiring verification",
    }
    INSPECT_PROVE_CONFIG = {
        "subagent_type": "foundry:assayer",
        "run_in_background": True,
        "description": "PROVE: spec-to-code citation verification",
    }
    GRIND_AGENT_CONFIG = {
        "subagent_type": "foundry:teammate",
        "mode": "bypassPermissions",
    }
    ASSAY_AGENT_CONFIG = {
        "subagent_type": "foundry:assayer",
        "description": "ASSAY: fresh-eyes spec-before-code verification",
    }

    if phase == "F0":
        manifest = _load_json(fdir / "castings" / "manifest.json")
        casting_count = len(manifest.get("castings", []))
        if casting_count == 0:
            return {
                "phase": "F0",
                "action": "add_castings",
                "instructions": (
                    f"DECOMPOSE: Spawn 1-5 BACKGROUND Agents to write casting files. No team needed.\n"
                    f"1. Identify 2-5 domains from the spec.\n"
                    f"2. Spawn one background Agent per domain in a SINGLE parallel message:\n"
                    f"     model='opus', subagent_type='general-purpose', mode='bypassPermissions',\n"
                    f"     run_in_background=true,\n"
                    f"     prompt='<per commands/start.md \u00a7F0.5: write manifest.json entry +\n"
                    f"              casting-<id>-prompt.md for your domain>'\n"
                    f"3. All files go under {fdir}/castings/ \u2014 NOT castings/ at project root.\n"
                    f"4. You'll be notified as each Agent completes; retrieve via TaskOutput(task_id).\n"
                    f"   After all complete, call Foundry-Validate-Castings."
                ),
                "details": {"foundry_dir": str(fdir), "agent_config": DECOMPOSE_AGENT_CONFIG},
            }
        return {
            "phase": "F0",
            "action": "transition_to_cast",
            "instructions": (
                f"Decomposition complete ({casting_count} castings). "
                "Call Foundry-Gate(phase='cast') to validate, then Foundry-Phase(phase='start_cast'). "
                "Create a CAST team (TeamCreate), register it (Foundry-Team-Up). "
                "Spawn ONE teammate per casting (or per wave of independent castings). "
                "Do NOT overload one teammate with many castings \u2014 distribute evenly."
            ),
            "details": {"casting_count": casting_count, "agent_config": CAST_AGENT_CONFIG},
        }

    elif phase == "F1":
        if not (fdir / ".cast-complete").exists():
            return {
                "phase": "F1",
                "action": "build_castings",
                "instructions": (
                    "CAST phase: teammates are building. Wait for all tasks to complete. "
                    "When done: shut down team, TeamDelete, Foundry-Team-Down, "
                    "then Foundry-Phase(phase='cast')."
                ),
                "details": {"agent_config": CAST_AGENT_CONFIG},
            }
        return {
            "phase": "F1",
            "action": "transition_to_inspect",
            "instructions": (
                "CAST complete. Call Foundry-Gate(phase='inspect') to validate preconditions, "
                "then update state to F2. Spawn verification agents for TRACE, PROVE. "
                "SIGHT runs in MAIN THREAD. TEST/PROBE run as background agents."
            ),
            "details": {
                "agent_configs": {
                    "trace": INSPECT_TRACE_CONFIG,
                    "prove": INSPECT_PROVE_CONFIG,
                    "test": {"model": "opus", "subagent_type": "general-purpose"},
                },
            },
        }

    elif phase == "F2":
        streams = _check_streams_complete(project_root)
        if not streams["complete"]:
            return {
                "phase": "F2",
                "action": "run_streams",
                "instructions": (
                    f"INSPECT phase: verification streams incomplete. Missing: {streams['missing']}. "
                    "Spawn agents using the agent_configs below (model and type are ENFORCED). "
                    "SIGHT runs in MAIN THREAD (Playwright MCP only works here) \u2014 "
                    "navigate to URL, snapshot every page, exercise all elements, check console. "
                    "After each stream, call Foundry-Stream(stream, cycle, items_checked)."
                ),
                "details": {
                    "missing_streams": streams["missing"].split(),
                    "required": streams["required"],
                    "agent_configs": {
                        "trace": INSPECT_TRACE_CONFIG,
                        "prove": INSPECT_PROVE_CONFIG,
                        "test": {"model": "opus", "subagent_type": "general-purpose"},
                    },
                },
            }

        if open_count > 0:
            return {
                "phase": "F2",
                "action": "transition_to_grind",
                "instructions": (
                    f"INSPECT complete: {open_count} open defect(s) found. "
                    "Call Foundry-Tasks to generate task list, "
                    "then Foundry-Gate(phase='grind'), then update state to F3. "
                    "Call Foundry-Phase(phase='grind_start') to clear markers. "
                    "Create grind team, assign tasks."
                ),
                "details": {"open_defects": open_count, "agent_config": GRIND_AGENT_CONFIG},
            }

        return {
            "phase": "F2",
            "action": "transition_to_assay",
            "instructions": (
                "INSPECT clean: zero defects. Call Foundry-Phase(phase='inspect_clean'), "
                "then Foundry-Gate(phase='assay'), then update state to F4. "
                "Spawn 4 parallel assayer agents using the config below (subagent_type='foundry:assayer' — frontmatter carries opus + effort=max)."
            ),
            "details": {"open_defects": 0, "agent_config": ASSAY_AGENT_CONFIG},
        }

    elif phase == "F3":
        if open_count > 0:
            return {
                "phase": "F3",
                "action": "fix_defects",
                "instructions": (
                    f"GRIND phase: {open_count} defect(s) to fix. "
                    "Teammates are fixing. Wait for completion. "
                    "After each fix, call Foundry-Fix(defect_id, cycle). "
                    "When all done: shut down team, update state to F2, run full INSPECT again."
                ),
                "details": {"open_defects": open_count, "agent_config": GRIND_AGENT_CONFIG},
            }
        return {
            "phase": "F3",
            "action": "transition_to_inspect",
            "instructions": (
                "GRIND complete: all defects fixed. Shut down grind team, "
                "Foundry-Team-Down, update state to F2. "
                "Run FULL INSPECT again (all streams). No spot checking."
            ),
            "details": {
                "agent_configs": {
                    "trace": INSPECT_TRACE_CONFIG,
                    "prove": INSPECT_PROVE_CONFIG,
                    "test": {"model": "opus", "subagent_type": "general-purpose"},
                },
            },
        }

    elif phase == "F4":
        verdicts = _load_json(fdir / "verdicts.json")
        non_verified = sum(1 for r in verdicts.get("requirements", []) if r.get("verdict") != "VERIFIED")
        total = len(verdicts.get("requirements", []))

        if non_verified > 0:
            return {
                "phase": "F4",
                "action": "assay_failed_loop_back",
                "instructions": (
                    f"ASSAY found {non_verified}/{total} non-verified requirements. "
                    "Sync findings as defects (Foundry-Sync), "
                    "call Foundry-Phase(phase='grind_start') to clear ALL markers, "
                    "update state to F3 (GRIND). Fix defects, then FULL INSPECT, then ASSAY again. "
                    "NO SPOT CORRECTIONS \u2014 the entire verification stack re-runs."
                ),
                "details": {
                    "non_verified": non_verified, "total": total,
                    "agent_config": GRIND_AGENT_CONFIG,
                },
            }

        # P3 (FR-003 / FR-004 / ST-001): the auto-pass path. ``.prove-complete``
        # stores only aggregate counts, so verdicts.json may be empty (or
        # partial) even after a clean PROVE — which would make the DONE gate's
        # verdict_coverage read 0/N and block the transition it just enabled.
        # On a clean PROVE, synthesize a VERIFIED verdict for every spec
        # requirement ID BEFORE emitting the auto-pass so the two gates agree.
        if _prove_is_clean(fdir, project_root):
            _synthesize_clean_prove_verdicts(
                fdir, project_root, cycle=state.get("cycle", 0)
            )

        temper = state.get("temper", False)
        if temper:
            return {
                "phase": "F4",
                "action": "transition_to_temper",
                "instructions": (
                    "ASSAY passed: all requirements verified. --temper is set. "
                    "Call Foundry-Gate(phase='temper'), update state to F5. "
                    "Run TEMPER micro-domain stress testing."
                ),
                "details": {
                    "agent_config": {"model": "opus", "subagent_type": "general-purpose"},
                },
            }

        return {
            "phase": "F4",
            "action": "transition_to_done",
            "instructions": (
                "ASSAY passed: all requirements verified. "
                "Call Foundry-Gate(phase='done'), update state to F6. "
                "Generate report, append lessons, archive."
            ),
            "details": {},
        }

    elif phase == "F5":
        return {
            "phase": "F5",
            "action": "run_temper",
            "instructions": (
                "TEMPER phase: micro-domain stress testing. "
                "Decompose into domains (min 15), probe each, cross-domain test, "
                "continuous sweep. Defects go through GRIND \u2192 INSPECT \u2192 ASSAY loop. "
                "When clean, call Foundry-Gate(phase='done'), update to F6."
            ),
            "details": {},
        }

    elif phase == "F6":
        return {
            "phase": "F6",
            "action": "done",
            "instructions": "Foundry complete. Generate report, archive state.",
            "details": {},
        }

    return {
        "phase": phase,
        "action": "unknown",
        "instructions": f"Unknown phase: {phase}. Check state.json.",
        "details": {},
    }


# --- Directives (non-blocking human steering) ---


def foundry_inject_directive(
    directive: str,
    priority: str = "normal",
    project_root: str = ".",
) -> dict:
    """Inject a human directive that the lead reads at every phase transition."""
    fdir = get_run_dir(project_root)
    if not fdir or not fdir.exists():
        return {"error": "No active foundry run"}

    directives_path = fdir / "directives.md"
    if not directives_path.exists():
        directives_path.write_text(
            "# Foundry Directives\n\nHuman steering inputs \u2014 read at every phase transition.\n\n",
            encoding="utf-8",
        )

    with open(directives_path, "a", encoding="utf-8") as f:
        marker = "URGENT" if priority == "urgent" else "DIRECTIVE"
        f.write(f"\n### [{marker}] {_now()}\n\n{directive}\n")

    return {"ok": True, "priority": priority, "message": "Directive injected \u2014 lead will read it at next phase transition"}


def foundry_clear_directives(
    project_root: str = ".",
) -> dict:
    """Clear all directives after they've been addressed."""
    fdir = get_run_dir(project_root)
    if not fdir:
        return {"error": "No active foundry run."}
    directives_path = fdir / "directives.md"
    if directives_path.exists():
        directives_path.write_text(
            "# Foundry Directives\n\nHuman steering inputs \u2014 read at every phase transition.\n\n",
            encoding="utf-8",
        )
    return {"ok": True, "message": "Directives cleared"}


def _read_directives(project_root: str) -> dict:
    """Read active directives."""
    fdir = get_run_dir(project_root)
    if not fdir:
        return {"has_directives": False, "urgent": [], "normal": [], "raw_text": ""}
    directives_path = fdir / "directives.md"

    if not directives_path.exists():
        return {"has_directives": False, "urgent": [], "normal": [], "raw_text": ""}

    text = directives_path.read_text(encoding="utf-8")

    urgent: list[str] = []
    normal: list[str] = []
    current_priority = None
    current_text: list[str] = []

    for line in text.split("\n"):
        if line.startswith("### [URGENT]"):
            if current_priority and current_text:
                target = urgent if current_priority == "urgent" else normal
                target.append("\n".join(current_text).strip())
            current_priority = "urgent"
            current_text = []
        elif line.startswith("### [DIRECTIVE]"):
            if current_priority and current_text:
                target = urgent if current_priority == "urgent" else normal
                target.append("\n".join(current_text).strip())
            current_priority = "normal"
            current_text = []
        elif current_priority:
            current_text.append(line)

    if current_priority and current_text:
        target = urgent if current_priority == "urgent" else normal
        target.append("\n".join(current_text).strip())

    has = len(urgent) > 0 or len(normal) > 0
    return {"has_directives": has, "urgent": urgent, "normal": normal, "raw_text": text if has else ""}


# --- Context reload ---


def foundry_get_context(
    project_root: str = ".",
) -> dict:
    """Return all foundry state in one call. Use after compaction or session start."""
    fdir = get_run_dir(project_root)

    if not fdir or not fdir.exists():
        return {"error": "No active foundry run. Call Foundry-Init or foundry_init(resume='run-name').", "initialized": False}

    state = _load_json(fdir / "state.json")
    defects = _load_json(fdir / "defects.json")
    verdicts = _load_json(fdir / "verdicts.json")

    all_defects = defects.get("defects", [])
    open_d = [d for d in all_defects if d.get("status") == "open"]
    fixed_d = [d for d in all_defects if d.get("status") == "fixed"]
    regression_d = [d for d in all_defects if d.get("regression")]

    all_reqs = verdicts.get("requirements", [])
    verified = sum(1 for r in all_reqs if r.get("verdict") == "VERIFIED")

    findings_excerpt = ""
    findings_path = fdir / "forge-findings.md"
    if findings_path.exists():
        text = findings_path.read_text(encoding="utf-8")
        findings_excerpt = text[:2000] + ("..." if len(text) > 2000 else "")

    lessons_excerpt = ""
    lessons_path = fdir / "lessons.md"
    if lessons_path.exists():
        text = lessons_path.read_text(encoding="utf-8")
        lessons_excerpt = text[:2000] + ("..." if len(text) > 2000 else "")

    teams = _check_active_teams(project_root)
    streams = _check_streams_complete(project_root)
    next_act = foundry_next_action(project_root)

    return {
        "initialized": True,
        "state": {
            "phase": state.get("phase", "unknown"),
            "cycle": state.get("cycle", 0),
            "spec_path": state.get("spec_path", ""),
            "temper": state.get("temper", False),
            "no_ui": state.get("no_ui", False),
            "started_at": state.get("started_at", ""),
            "total_duration": state.get("total_duration", ""),
            "phase_times": state.get("phase_times", {}),
        },
        "defects": {
            "total": len(all_defects),
            "open": len(open_d),
            "fixed": len(fixed_d),
            "regressions": len(regression_d),
            "open_ids": [d["id"] for d in open_d],
        },
        "verdicts": {
            "total": len(all_reqs),
            "verified": verified,
            "non_verified": len(all_reqs) - verified,
        },
        "streams": streams,
        "active_teams": teams,
        "directives": _read_directives(project_root),
        "forge_findings_excerpt": findings_excerpt,
        "lessons_excerpt": lessons_excerpt,
        "next_action": next_act,
    }
