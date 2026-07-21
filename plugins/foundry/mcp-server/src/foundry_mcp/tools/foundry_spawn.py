"""Foundry teammate spawn — reads the pre-authored casting prompt file.

Architecture principle: **plans are prompts.** Teammate prompts are
authored ONCE by decompose at F0.5, written to disk as
`foundry-archive/{run}/castings/casting-{id}-prompt.md`, validated at F0.9,
and frozen. The lead never drafts or modifies teammate prompts — it calls
this tool with a casting_id and passes the returned text directly to the
Agent tool.

This eliminates the "lead drafts prompt from casting" step where spec
fidelity used to silently erode via paraphrasing, scope cuts, or hedge
language. The lead is a router, not an interpreter.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from foundry_mcp.tools.foundry_state import get_run_dir


def foundry_spawn_teammate(
    casting_id: int | str,
    phase: str = "cast",
    project_root: str = ".",
) -> dict:
    """Read and return the pre-authored prompt for a casting.

    Args:
        casting_id: The id of the casting whose teammate prompt to read.
        phase: "cast" (F1) or "grind" (F3). Affects which prompt variant to
            return if both exist; otherwise identical.
        project_root: Repo root.

    Returns:
        On success:
            {
                "ok": True,
                "casting_id": N,
                "phase": "cast" | "grind",
                "prompt_path": "foundry-archive/{run}/castings/casting-N-prompt.md",
                "prompt_hash": "sha256:...",
                "prompt": "<full text of the pre-authored prompt>",
                "instructions": "Pass the `prompt` field verbatim to the Agent tool. Do NOT modify it. Do NOT prepend, append, or substitute text. Only the `prompt` content is authorized teammate context."
            }
        On failure:
            {"ok": False, "error": "...", "hint": "..."}
    """
    fdir = get_run_dir(project_root)
    if not fdir:
        return {"ok": False, "error": "No active foundry run", "hint": "Call Foundry-Init first"}
    if not fdir.exists():
        return {"ok": False, "error": "Foundry run directory not found", "hint": f"Expected {fdir}"}

    manifest_path = fdir / "castings" / "manifest.json"
    if not manifest_path.exists():
        return {"ok": False, "error": "No manifest.json", "hint": "Run F0.5 DECOMPOSE first"}

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        return {"ok": False, "error": f"manifest.json parse error: {e}"}

    castings = manifest.get("castings", [])
    casting = None
    for c in castings:
        if str(c.get("id")) == str(casting_id):
            casting = c
            break

    if not casting:
        available = [c.get("id") for c in castings]
        return {
            "ok": False,
            "error": f"casting_id {casting_id} not found in manifest",
            "hint": f"Available casting ids: {available}",
        }

    # Locate the pre-authored prompt file.
    prompt_path = fdir / "castings" / f"casting-{casting_id}-prompt.md"
    if not prompt_path.exists():
        return {
            "ok": False,
            "error": f"casting-{casting_id}-prompt.md does not exist",
            "hint": (
                "Decompose must write a pre-authored teammate prompt file for every casting. "
                "Re-run F0.5 DECOMPOSE or check that the decompose step wrote the prompt files."
            ),
        }

    prompt_text = prompt_path.read_text(encoding="utf-8")

    if not prompt_text.strip():
        return {
            "ok": False,
            "error": f"casting-{casting_id}-prompt.md is empty",
            "hint": "Re-run F0.5 DECOMPOSE to regenerate the prompt file.",
        }

    # Hash the prompt for audit tracking.
    prompt_hash = "sha256:" + hashlib.sha256(prompt_text.encode("utf-8")).hexdigest()[:16]

    # Log the spawn for the audit trail.
    spawn_log = fdir / "spawns.log"
    try:
        from datetime import datetime, timezone
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "casting_id": casting_id,
            "phase": phase,
            "prompt_hash": prompt_hash,
            "prompt_path": str(prompt_path.relative_to(Path(project_root)) if prompt_path.is_absolute() else prompt_path),
        }
        with spawn_log.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception:
        # Logging failures must not block the spawn.
        pass

    result: dict = {
        "ok": True,
        "casting_id": casting_id,
        "phase": phase,
        "prompt_path": str(prompt_path.relative_to(Path(project_root)) if prompt_path.is_absolute() else prompt_path),
        "prompt_hash": prompt_hash,
        "prompt": prompt_text,
        "instructions": (
            "Pass the `prompt` field VERBATIM to the Agent tool as the teammate's prompt. "
            "Do NOT modify, summarize, paraphrase, or augment the text. Do NOT add your own context, "
            "hedges, or scope notes. The prompt was authored at F0.5 DECOMPOSE with the master spec "
            "as source of truth and was validated at F0.9. Modifying it reintroduces the exact drift "
            "failure mode this architecture was built to prevent."
        ),
    }

    # GRIND cycle context. When the teammate respawns to fix defects, the code
    # has already moved past CAST — earlier GRIND cycles may have
    # modified files this teammate owns or depends on. Without this block the
    # teammate re-explores from scratch and may re-do work already done.
    # The lead is expected to append this verbatim BEFORE the defect list
    # (if non-empty), so the teammate reads current state before acting.
    if phase == "grind":
        context = _build_grind_cycle_context(fdir, casting_id, project_root)
        if context:
            result["grind_cycle_context"] = context
            result["instructions"] += (
                " GRIND addendum: when appending the defect block BELOW this prompt, "
                "prepend the `grind_cycle_context` block FIRST so the teammate reads current "
                "file state before acting on defects."
            )

    return result


def _build_grind_cycle_context(fdir, casting_id, project_root: str) -> str:
    """Return a '## Prior-cycle file changes' block for a GRIND teammate.

    Diffs HEAD against .cast-baseline-sha (stamped at CAST→INSPECT transition).
    Empty list = cycle 1 pre-edit, so we return empty string (nothing to append).
    Filters to the casting's declared key_files when available, falling back
    to the full diff when key_files aren't declared.
    """
    baseline_file = fdir / ".cast-baseline-sha"
    if not baseline_file.exists():
        return ""
    try:
        baseline_sha = baseline_file.read_text(encoding="utf-8").strip()
    except OSError:
        return ""
    if not baseline_sha:
        return ""

    import subprocess
    try:
        diff = subprocess.run(
            ["git", "-C", project_root, "diff", "--name-only", baseline_sha, "HEAD"],
            capture_output=True, text=True, timeout=10,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return ""
    if diff.returncode != 0:
        return ""

    changed = [line.strip() for line in diff.stdout.splitlines() if line.strip()]
    if not changed:
        return ""

    # Locate this casting's key_files for scoped context
    manifest_path = fdir / "castings" / "manifest.json"
    casting_keyfiles: set[str] = set()
    if manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            for c in manifest.get("castings", []):
                if str(c.get("id")) == str(casting_id):
                    for f in (c.get("key_files") or []):
                        if isinstance(f, str) and f.strip():
                            casting_keyfiles.add(f.strip())
                    break
        except json.JSONDecodeError:
            pass

    relevant = [f for f in changed if f in casting_keyfiles] if casting_keyfiles else changed
    other = [f for f in changed if f not in casting_keyfiles] if casting_keyfiles else []

    lines = [
        "## Prior-cycle file changes (READ BEFORE ACTING ON DEFECTS)",
        "",
        "Earlier CAST or GRIND cycles modified the files listed below. Before assuming "
        "anything about current code state, **read these files first**. Memory is a hint, "
        "not ground truth — verify against the actual files. Skip redundant exploration: "
        "if a defect mentions a symbol in one of these files, read the current version "
        "before re-implementing.",
        "",
    ]
    if casting_keyfiles and relevant:
        lines.append("### Your casting's key_files that changed:")
        for f in relevant:
            lines.append(f"- `{f}`")
        lines.append("")
    if other:
        label = "### Other files changed (may be upstream dependencies):" if casting_keyfiles else "### Files changed since CAST:"
        lines.append(label)
        for f in other[:40]:  # cap at 40 to avoid prompt bloat
            lines.append(f"- `{f}`")
        if len(other) > 40:
            lines.append(f"- ... ({len(other) - 40} more)")
        lines.append("")

    return "\n".join(lines)


def foundry_cast_wave(
    wave: int,
    phase: str = "cast",
    project_root: str = ".",
) -> dict:
    """Read and return prompts for every casting in the specified wave.

    Optimization: replaces N sequential `Foundry-Spawn-Teammate` calls
    (each ~1s of lead deliberation + 1 MCP roundtrip) with a single bulk
    fetch. The lead then spawns all N Agent calls in a single parallel
    tool-use message. Preserves the verbatim-prompt contract and audit
    trail (each casting still logged to spawns.log).

    Args:
        wave: 1-indexed wave number from manifest.waves.
        phase: "cast" or "grind".
        project_root: Repo root.

    Returns on success:
        {
            "ok": True,
            "wave": N,
            "phase": "cast",
            "team_name_suggestion": "cast-{run}-wave-N  (or grind-{run}-cycle-N for phase='grind')",
            "castings": [
                {"casting_id": 1, "prompt": "...", "prompt_hash": "sha256:..."},
                ...
            ],
            "instructions": "Spawn every casting as a SEPARATE Agent tool call in ONE message..."
        }
    """
    fdir = get_run_dir(project_root)
    if not fdir:
        return {"ok": False, "error": "No active foundry run", "hint": "Call Foundry-Init first"}
    if not fdir.exists():
        return {"ok": False, "error": "Foundry run directory not found", "hint": f"Expected {fdir}"}

    manifest_path = fdir / "castings" / "manifest.json"
    if not manifest_path.exists():
        return {"ok": False, "error": "No manifest.json", "hint": "Run F0.5 DECOMPOSE first"}

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        return {"ok": False, "error": f"manifest.json parse error: {e}"}

    waves = manifest.get("waves") or []
    wave_entry = None
    for w in waves:
        if w.get("wave") == wave:
            wave_entry = w
            break
    if not wave_entry:
        return {
            "ok": False,
            "error": f"wave {wave} not found in manifest",
            "hint": f"Available waves: {[w.get('wave') for w in waves]}",
        }

    casting_ids = wave_entry.get("casting_ids") or []
    if not casting_ids:
        return {
            "ok": False,
            "error": f"wave {wave} has no casting_ids in manifest",
            "hint": "Re-run F0.5 DECOMPOSE to rebuild wave groupings.",
        }

    from datetime import datetime, timezone
    spawn_log = fdir / "spawns.log"
    results = []

    for cid in casting_ids:
        prompt_path = fdir / "castings" / f"casting-{cid}-prompt.md"
        if not prompt_path.exists():
            return {
                "ok": False,
                "error": f"casting-{cid}-prompt.md does not exist (wave {wave})",
                "hint": "Re-run F0.5 DECOMPOSE — every casting must have a pre-authored prompt.",
            }
        prompt_text = prompt_path.read_text(encoding="utf-8")
        if not prompt_text.strip():
            return {
                "ok": False,
                "error": f"casting-{cid}-prompt.md is empty (wave {wave})",
                "hint": "Re-run F0.5 DECOMPOSE to regenerate the prompt file.",
            }
        prompt_hash = "sha256:" + hashlib.sha256(prompt_text.encode("utf-8")).hexdigest()[:16]
        results.append({
            "casting_id": cid,
            "prompt": prompt_text,
            "prompt_hash": prompt_hash,
            "prompt_path": str(prompt_path.relative_to(Path(project_root)) if prompt_path.is_absolute() else prompt_path),
        })

        try:
            entry = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "casting_id": cid,
                "phase": phase,
                "wave": wave,
                "prompt_hash": prompt_hash,
                "bulk": True,
            }
            with spawn_log.open("a", encoding="utf-8") as f:
                f.write(json.dumps(entry) + "\n")
        except Exception:
            pass

    run_name = fdir.name
    # Phase-first naming: {phase}-{run}-{suffix}. CAST uses `wave-N`
    # from the dependency graph; GRIND uses `cycle-N` from the defect cycle
    # counter. The same bulk tool services both phases; `wave` is the arg
    # name in both but semantically distinct across phases.
    phase_prefix = "cast" if phase == "cast" else "grind"
    suffix_word = "wave" if phase == "cast" else "cycle"
    team_suggestion = f"{phase_prefix}-{run_name}-{suffix_word}-{wave}"

    return {
        "ok": True,
        "wave": wave,
        "phase": phase,
        "team_name_suggestion": team_suggestion,
        "castings": results,
        "instructions": (
            f"Spawn {len(results)} Agent tool calls in a SINGLE MESSAGE (parallel tool use). "
            "Each Agent call gets its corresponding casting's prompt VERBATIM \u2014 no modification. "
            "Required per-Agent params: subagent_type='foundry:teammate', "
            "mode='bypassPermissions'. (foundry:teammate's frontmatter sets model=opus + effort=xhigh.) "
            "NEVER run_in_background=true (foreground, TeamCreate-managed). "
            "Before spawning: TeamCreate(team_name_suggestion) + Foundry-Team-Up(team_name_suggestion). "
            "GRIND phase only: append the grind_cycle_context block (if returned) then a "
            "'## Defects to fix this cycle:' block BELOW each prompt, never inside it."
        ),
    }
