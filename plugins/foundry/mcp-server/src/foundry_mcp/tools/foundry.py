"""Foundry tools — defect tracking, verdict recording, and coverage verification.

All operations are local file reads/writes against the foundry-archive/{run}/ directory.
Zero API calls. Zero cost.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from foundry_mcp.tools.foundry_state import (
    ARCHIVE_DIR,
    get_run_dir,
    set_active_run,
)
from foundry_mcp.tools.display import foundry_hammer, FOUNDRY_SEP

# ANSI colors
_RESET = "\033[0m"
_BOLD = "\033[1m"
_DIM = "\033[2m"
_GREEN = "\033[32m"
_CYAN = "\033[36m"
_WHITE = "\033[37m"
_BCYAN = f"{_BOLD}{_CYAN}"
_BWHITE = f"{_BOLD}{_WHITE}"
_BGREEN = f"{_BOLD}{_GREEN}"


def _format_init_display(run_name: str, temper: bool = False, nyquist: bool = False) -> str:
    """Foundry init display with pixel-art hammer."""
    phases = [
        ("F0",   "RESEARCH", True),
        ("F0.5", "DECOMPOSE", False),
        ("F0.9", "VALIDATE", False),
        ("F1",   "CAST", False),
        ("F2",   "INSPECT", False),
        ("F3",   "GRIND", False),
        ("F4",   "ASSAY", False),
        ("F5",   "TEMPER", False),
        ("F5.5", "NYQUIST", False),
        ("F6",   "DONE", False),
    ]
    lines = [foundry_hammer(f"F O U N D R Y  {run_name}")]
    for pid, pname, active in phases:
        skip = (pid == "F5" and not temper) or (pid == "F5.5" and not nyquist)
        if active:
            icon = f"{_BGREEN}\u25b6{_RESET}"
            label = f"{_BWHITE}{pid} {pname}{_RESET}"
            right = f"{_BGREEN}\u25c0 START{_RESET}"
        elif skip:
            icon = f"{_DIM}\u2500{_RESET}"
            label = f"{_DIM}{pid} {pname}{_RESET}"
            right = f"{_DIM}skip{_RESET}"
        else:
            icon = f"{_DIM}\u25cb{_RESET}"
            label = f"{_DIM}{pid} {pname}{_RESET}"
            right = ""
        lines.append(f"  {icon} {label}  {right}")
    lines.append(FOUNDRY_SEP)
    lines.append(f"Call {_BCYAN}Foundry-Next{_RESET} for instructions.")
    return "\n".join(lines)


def _load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _save_json(path: Path, data: dict) -> None:
    """Atomic JSON write — write to .tmp then rename."""
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    tmp.rename(path)


_ADJECTIVES = [
    "ambitious", "blazing", "bold", "brave", "calm", "clever", "cosmic",
    "daring", "deft", "eager", "fierce", "flying", "golden", "grand",
    "heroic", "humble", "iron", "jolly", "keen", "lively", "lucky",
    "mighty", "noble", "plucky", "quick", "rapid", "roaring", "sharp",
    "silver", "sleek", "soaring", "steady", "steel", "stout", "swift",
    "thunder", "titan", "valiant", "vivid", "wild", "witty", "zesty",
]

_NOUNS = [
    "anvil", "arrow", "badger", "beacon", "bison", "bolt", "canyon",
    "cedar", "comet", "condor", "crane", "falcon", "forge", "fox",
    "glacier", "granite", "hawk", "helm", "heron", "jaguar", "kestrel",
    "lance", "leopard", "lynx", "mammoth", "mantis", "maple", "marble",
    "mustang", "oak", "orca", "osprey", "otter", "panther", "phoenix",
    "pike", "puma", "quartz", "raven", "ridge", "salmon", "sequoia",
    "squid", "stallion", "summit", "talon", "tiger", "trout", "viper",
    "vulture", "walrus", "wolf", "wren",
]


def _generate_run_name(ticket: str = "", description: str = "") -> str:
    """Generate a human-friendly run name.

    Uses ticket/description when available, falls back to random adjective-noun.
    Examples: 'AQUA-123-login-flow', 'fix-broken-nav', 'bold-falcon'
    """
    parts: list[str] = []
    if ticket:
        parts.append(ticket)
    if description:
        slug = description.lower().replace(" ", "-")[:40]
        slug = "".join(c for c in slug if c.isalnum() or c == "-").strip("-")
        if slug:
            parts.append(slug)
    if parts:
        return "-".join(parts)
    # Fallback: random name when no context provided
    import random
    adj = random.choice(_ADJECTIVES)
    noun = random.choice(_NOUNS)
    return f"{adj}-{noun}"


def foundry_init(
    spec_path: str | None = None,
    temper: bool = False,
    no_ui: bool = False,
    resume: str | None = None,
    ticket: str = "",
    description: str = "",
    url: str = "",
    project_root: str = ".",
) -> dict:
    """Initialize a foundry run under foundry-archive/.

    Args:
        resume: Name of existing run to resume (e.g. 'bold-falcon').
        ticket: Ticket ID (e.g., "AQUA-123") for name generation.
        description: Short description for name generation.
        url: Target URL for the SIGHT audit. Persisted to
            ``castings/manifest.json`` as ``target_url`` (the store of
            record the inspect gate readers load — foundry_orchestrator.py
            :531 / :1033), mirroring the bash init write at foundry.sh:176.
            NOT written into state.json.

    Returns:
        {foundry_dir, run_name, files_created[], spec_copied}
    """
    root = Path(project_root)
    archive = root / ARCHIVE_DIR

    # --- Resume mode ---
    if resume:
        run_dir = archive / resume
        state_path = run_dir / "state.json"
        if not state_path.exists():
            return {"error": f"Run '{resume}' not found in {ARCHIVE_DIR}/"}
        set_active_run(resume)
        state = _load_json(state_path)
        return {
            "foundry_dir": str(run_dir),
            "run_name": resume,
            "resumed": True,
            "state": state,
            "display": (
                foundry_hammer(f"F O U N D R Y  Resumed: {resume}")
                + f"\n  Phase: {state.get('phase', '?')}  Cycle: {state.get('cycle', 0)}"
                + f"\n{FOUNDRY_SEP}"
                + f"\nCall Foundry-Next for instructions."
            ),
            "next_step": "Call Foundry-Next to see status and get instructions.",
        }

    # --- New run ---
    run_name = _generate_run_name(ticket=ticket, description=description)

    # Ensure unique name (don't collide with existing runs)
    archive.mkdir(parents=True, exist_ok=True)
    if (archive / run_name).exists():
        import random
        suffix = random.randint(100, 999)
        run_name = f"{run_name}-{suffix}"

    fdir = archive / run_name

    # Silently delete legacy .foundry-dir if it exists (one-time migration)
    legacy_pointer = root / ".foundry-dir"
    if legacy_pointer.exists():
        legacy_pointer.unlink(missing_ok=True)

    dirs = [
        fdir,
        fdir / "castings",
        fdir / "traces",
        fdir / "proofs",
        fdir / "proofs" / "screenshots",
    ]
    if temper:
        dirs.extend([fdir / "temper", fdir / "temper" / "probe-results"])

    for d in dirs:
        d.mkdir(parents=True, exist_ok=True)

    files_created = []

    # defects.json — always fresh
    defects_path = fdir / "defects.json"
    _save_json(defects_path, {"defects": []})
    files_created.append("defects.json")

    # verdicts.json — always fresh
    verdicts_path = fdir / "verdicts.json"
    _save_json(verdicts_path, {"cycle": 0, "requirements": []})
    files_created.append("verdicts.json")

    # state.json — always fresh
    state_path = fdir / "state.json"
    _init_now = datetime.now(timezone.utc).isoformat()
    state = {
        "phase": "F0",
        "cycle": 0,
        "spec_path": spec_path or "",
        "temper": temper,
        "no_ui": no_ui,
        "started_at": _init_now,
        "phase_times": {
            # Stamp F0 start so sub-phase timing (F0 / F0.5 / F0.9) works
            # automatically; passive stamping in foundry_next_action closes
            # F0 when manifest.json appears, closes F0.5 on .validate-passed,
            # and closes F0.9 on Foundry-Phase(start_cast).
            "F0": {"started_at": _init_now},
        },
    }
    _save_json(state_path, state)
    files_created.append("state.json")

    # castings/manifest.json — the store of record for target_url.
    # The inspect gate readers (_check_streams_complete / _check_sight_required
    # in foundry_orchestrator.py:531 / :1033) load target_url from HERE, not
    # from state.json. Mirrors the bash init write shape at foundry.sh:168-182
    # so the Python and bash init paths produce byte-compatible manifests.
    # `castings: []` at init keeps the F0 guidance engine emitting DECOMPOSE
    # (it keys on len(castings), not manifest existence); decompose fills in
    # the castings/waves later. A run without a url persists target_url="",
    # which keeps the inspect gate blocked when frontend files are present.
    manifest_path = fdir / "castings" / "manifest.json"
    manifest = {
        "created_at": _init_now,
        "updated_at": _init_now,
        "spec_path": spec_path or "",
        "temper": temper,
        "no_ui": no_ui,
        "target_url": url,
        "max_cycles": 0,
        "current_cycle": 0,
        "status": "initialized",
        "castings": [],
        "waves": [],
    }
    _save_json(manifest_path, manifest)
    files_created.append("castings/manifest.json")

    # forge-log.md — always fresh
    forge_log = fdir / "forge-log.md"
    forge_log.write_text(
        "# Forge Log\n\nCumulative record of all defects, fixes, and verdicts.\n\n---\n\n",
        encoding="utf-8",
    )
    files_created.append("forge-log.md")

    # Copy spec
    spec_copied = False
    if spec_path:
        src = root / spec_path if not Path(spec_path).is_absolute() else Path(spec_path)
        dest = fdir / "spec.md"
        if src.exists() and not dest.exists():
            dest.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
            spec_copied = True
            files_created.append("spec.md")

    # Set this as the active run for this session
    set_active_run(run_name)

    return {
        "foundry_dir": str(fdir),
        "run_name": run_name,
        "files_created": files_created,
        "spec_copied": spec_copied,
        "display": _format_init_display(run_name, state.get("temper", False), state.get("nyquist", False)),
        "next_step": "Call Foundry-Next to get decomposition instructions. Print the display above FIRST.",
    }


def foundry_add_defect(
    cycle: int,
    source: str,
    defect_type: str,
    description: str,
    spec_ref: str = "",
    symbol: str = "",
    file_path: str = "",
    project_root: str = ".",
) -> dict:
    """Add a defect to the foundry ledger."""
    fdir = get_run_dir(project_root)
    if not fdir:
        return {"error": "No active foundry run. Call Foundry-Init."}
    defects_path = fdir / "defects.json"
    data = _load_json(defects_path)
    if "defects" not in data:
        data["defects"] = []

    defect_id = f"D-{len(data['defects']) + 1:03d}"

    defect = {
        "id": defect_id,
        "cycle": cycle,
        "source": source,
        "type": defect_type,
        "description": description,
        "spec_ref": spec_ref,
        "symbol": symbol,
        "file": file_path,
        "status": "open",
        "fixed_in_cycle": None,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    data["defects"].append(defect)
    _save_json(defects_path, data)

    # Append to forge log
    forge_log = fdir / "forge-log.md"
    if forge_log.exists():
        with open(forge_log, "a", encoding="utf-8") as f:
            f.write(f"\n### Cycle {cycle} \u2014 {source}: {defect_id}\n")
            f.write(f"- **Type:** {defect_type}\n")
            f.write(f"- **Description:** {description}\n")
            if spec_ref:
                f.write(f"- **Spec ref:** {spec_ref}\n")
            if symbol:
                f.write(f"- **Symbol:** {symbol}\n")
            if file_path:
                f.write(f"- **File:** {file_path}\n")
            f.write("\n")

    open_count = sum(1 for d in data["defects"] if d["status"] == "open")
    return {
        "defect_id": defect_id,
        "total_defects": len(data["defects"]),
        "open_defects": open_count,
    }


def foundry_query_defects(
    status: str | None = None,
    cycle: int | None = None,
    source: str | None = None,
    spec_ref: str | None = None,
    project_root: str = ".",
) -> dict:
    """Query defects with optional filters."""
    fdir = get_run_dir(project_root)
    if not fdir:
        return {"error": "No active foundry run. Call Foundry-Init."}
    data = _load_json(fdir / "defects.json")
    defects = data.get("defects", [])

    if status:
        defects = [d for d in defects if d.get("status") == status]
    if cycle is not None:
        defects = [d for d in defects if d.get("cycle") == cycle]
    if source:
        defects = [d for d in defects if d.get("source") == source]
    if spec_ref:
        defects = [d for d in defects if d.get("spec_ref") == spec_ref]

    all_defects = data.get("defects", [])
    by_source: dict[str, int] = {}
    by_type: dict[str, int] = {}
    for d in all_defects:
        s = d.get("source", "unknown")
        by_source[s] = by_source.get(s, 0) + 1
        t = d.get("type", "unknown")
        by_type[t] = by_type.get(t, 0) + 1

    return {
        "defects": defects,
        "summary": {
            "total": len(all_defects),
            "open": sum(1 for d in all_defects if d.get("status") == "open"),
            "fixed": sum(1 for d in all_defects if d.get("status") == "fixed"),
            "by_source": by_source,
            "by_type": by_type,
        },
    }


def foundry_add_verdict(
    requirement_id: str,
    verdict: str,
    evidence: str,
    spec_text_cited: str = "",
    code_location: str = "",
    cycle: int = 0,
    project_root: str = ".",
) -> dict:
    """Record a verdict for a requirement with spec citation and code evidence."""
    fdir = get_run_dir(project_root)
    if not fdir:
        return {"error": "No active foundry run. Call Foundry-Init."}
    verdicts_path = fdir / "verdicts.json"
    data = _load_json(verdicts_path)
    if "requirements" not in data:
        data["requirements"] = []

    entry = {
        "id": requirement_id,
        "verdict": verdict,
        "evidence": evidence,
        "spec_text_cited": spec_text_cited,
        "code_location": code_location,
        "cycle": cycle,
        "recorded_at": datetime.now(timezone.utc).isoformat(),
    }

    replaced = False
    for i, req in enumerate(data["requirements"]):
        if req.get("id") == requirement_id:
            data["requirements"][i] = entry
            replaced = True
            break
    if not replaced:
        data["requirements"].append(entry)

    data["cycle"] = cycle
    _save_json(verdicts_path, data)

    verified = sum(1 for r in data["requirements"] if r.get("verdict") == "VERIFIED")
    return {
        "requirement_id": requirement_id,
        "verdict": verdict,
        "replaced_existing": replaced,
        "total_requirements": len(data["requirements"]),
        "verified_count": verified,
    }


def foundry_verify_coverage(
    spec_path: str | None = None,
    project_root: str = ".",
) -> dict:
    """Cross-reference spec -> verdicts -> defects for full traceability."""
    fdir = get_run_dir(project_root)
    if not fdir:
        return {"error": "No active foundry run. Call Foundry-Init."}
    root = Path(project_root)

    verdicts_data = _load_json(fdir / "verdicts.json")
    requirements = verdicts_data.get("requirements", [])
    verdict_map = {r["id"]: r for r in requirements}

    defects_data = _load_json(fdir / "defects.json")
    all_defects = defects_data.get("defects", [])
    open_defects = [d for d in all_defects if d.get("status") == "open"]

    defects_by_req: dict[str, list[dict]] = {}
    for d in open_defects:
        ref = d.get("spec_ref", "")
        if ref:
            defects_by_req.setdefault(ref, []).append({
                "id": d["id"],
                "type": d.get("type", ""),
                "description": d.get("description", ""),
            })

    spec_req_ids: list[str] = []
    if spec_path:
        spath = root / spec_path if not Path(spec_path).is_absolute() else Path(spec_path)
        if spath.exists():
            import re
            spec_text = spath.read_text(encoding="utf-8")
            spec_req_ids = list(dict.fromkeys(re.findall(r"\b(US-\d+|FR-\d+|NFR-\d+)\b", spec_text)))
    elif (fdir / "spec.md").exists():
        import re
        spec_text = (fdir / "spec.md").read_text(encoding="utf-8")
        spec_req_ids = list(dict.fromkeys(re.findall(r"\b(US-\d+|FR-\d+|NFR-\d+)\b", spec_text)))

    if not spec_req_ids:
        spec_req_ids = [r["id"] for r in requirements]

    traceability = []
    gaps = []
    for req_id in spec_req_ids:
        v = verdict_map.get(req_id)
        entry = {
            "requirement_id": req_id,
            "verdict": v["verdict"] if v else None,
            "evidence": v.get("evidence", "") if v else "",
            "spec_text_cited": v.get("spec_text_cited", "") if v else "",
            "code_location": v.get("code_location", "") if v else "",
            "open_defects": defects_by_req.get(req_id, []),
            "status": "verified" if v and v["verdict"] == "VERIFIED" else (
                "non_verified" if v else "uncovered"
            ),
        }
        traceability.append(entry)
        if entry["status"] != "verified":
            gaps.append({
                "requirement_id": req_id,
                "status": entry["status"],
                "verdict": entry["verdict"],
                "open_defect_count": len(entry["open_defects"]),
            })

    verified = sum(1 for t in traceability if t["status"] == "verified")
    non_verified = sum(1 for t in traceability if t["status"] == "non_verified")
    uncovered = sum(1 for t in traceability if t["status"] == "uncovered")
    total = len(traceability)

    all_verified = verified == total and total > 0
    zero_open = len(open_defects) == 0

    return {
        "traceability": traceability,
        "coverage_summary": {
            "total_requirements": total,
            "verified": verified,
            "non_verified": non_verified,
            "uncovered": uncovered,
            "coverage_pct": f"{verified / total * 100:.0f}%" if total > 0 else "N/A",
        },
        "defect_summary": {
            "total": len(all_defects),
            "open": len(open_defects),
            "fixed": sum(1 for d in all_defects if d.get("status") == "fixed"),
        },
        "gaps": gaps,
        "pass": all_verified and zero_open,
    }
