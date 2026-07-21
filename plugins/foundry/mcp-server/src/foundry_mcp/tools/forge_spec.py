"""Forge-Spec pipeline — state management and format conversion.

Orchestrates community plugins (Understand-Anything, Deep-Project, Deep-Plan)
through an MCP state machine to produce foundry-compatible spec and plan files.

All operations are local file reads/writes against foundry-planning/{project}/.
Zero API calls. Zero cost.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

PLANNING_DIR = "foundry-planning"

_PHASES = ["S0_understand", "S1_decompose", "S2_plan", "S3_validate"]
_PHASE_LABELS = {
    "S0_understand": "S0: UNDERSTAND",
    "S1_decompose": "S1: DECOMPOSE",
    "S2_plan": "S2: PLAN",
    "S3_validate": "S3: VALIDATE",
    "READY": "READY",
}


def _load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _save_json(path: Path, data: dict) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    tmp.rename(path)


def _slugify(name: str) -> str:
    """Convert a project name to a filesystem-safe slug."""
    slug = name.lower().strip().replace(" ", "-")
    slug = re.sub(r"[^a-z0-9\-]", "", slug)
    slug = re.sub(r"-+", "-", slug).strip("-")
    return slug or "unnamed"


def _default_state(project_name: str) -> dict:
    return {
        "phase": "S0",
        "project_name": project_name,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "phases": {
            "S0_understand": {"status": "pending"},
            "S1_decompose": {"status": "pending", "splits": [], "count": 0},
            "S2_plan": {"status": "pending", "specs_done": 0, "specs_total": 0},
            "S3_validate": {"status": "pending", "requirement_count": 0},
        },
        "foundry_ready": False,
        "foundry_spec_path": "",
        "foundry_plan_path": "",
    }


def _get_project_dir(project_root: str, project_name: str) -> Path:
    slug = _slugify(project_name)
    return Path(project_root) / PLANNING_DIR / slug


# ── Forge-Spec-Start ─────────────────────────────────────────────────────────


def forge_spec_start(project_name: str, project_root: str = ".") -> dict:
    """Initialize a forge-spec project directory and state."""
    if not project_name or not project_name.strip():
        return {"error": "project_name is required"}

    slug = _slugify(project_name)
    proj_dir = _get_project_dir(project_root, project_name)

    # Resume if state already exists
    state_path = proj_dir / "state.json"
    if state_path.exists():
        state = _load_json(state_path)
        return {
            "project_name": project_name,
            "slug": slug,
            "project_dir": str(proj_dir),
            "resumed": True,
            "phase": state.get("phase", "S0"),
            "state": state,
        }

    # Create directory structure
    proj_dir.mkdir(parents=True, exist_ok=True)
    (proj_dir / "research").mkdir(exist_ok=True)
    (proj_dir / "splits").mkdir(exist_ok=True)

    state = _default_state(project_name)
    spec_path = str(proj_dir / "spec.md")
    plan_path = str(proj_dir / "plan.md")
    state["foundry_spec_path"] = spec_path
    state["foundry_plan_path"] = plan_path
    _save_json(state_path, state)

    return {
        "project_name": project_name,
        "slug": slug,
        "project_dir": str(proj_dir),
        "resumed": False,
        "phase": "S0",
        "dirs_created": ["research/", "splits/"],
        "state": state,
    }


# ── Forge-Spec-Check ─────────────────────────────────────────────────────────


def _check_codebase(proj_dir: Path, state: dict) -> dict:
    """Check if understand-anything knowledge graph exists."""
    research_dir = proj_dir / "research"
    # Look for knowledge graph or analysis files from understand-anything
    found_files = []
    if research_dir.exists():
        for f in research_dir.iterdir():
            if f.is_file() and f.suffix in (".md", ".json", ".yaml", ".yml"):
                found_files.append(f.name)

    found = len(found_files) > 0
    if found:
        state["phases"]["S0_understand"]["status"] = "complete"
        state["phase"] = "S1"
    return {
        "action": "codebase",
        "found": found,
        "files": found_files,
        "phase": state["phase"],
        "hint": (
            "Run /understand-anything and save output to "
            f"{proj_dir / 'research'}/"
        ) if not found else "",
    }


def _check_decompose(proj_dir: Path, state: dict) -> dict:
    """Check if deep-project domain splits exist."""
    splits_dir = proj_dir / "splits"
    found_splits = []
    if splits_dir.exists():
        for f in sorted(splits_dir.iterdir()):
            if f.is_file() and f.suffix in (".md", ".json"):
                found_splits.append(f.name)

    found = len(found_splits) > 0
    if found:
        state["phases"]["S1_decompose"]["status"] = "complete"
        state["phases"]["S1_decompose"]["splits"] = found_splits
        state["phases"]["S1_decompose"]["count"] = len(found_splits)
        state["phases"]["S2_plan"]["specs_total"] = len(found_splits)
        state["phase"] = "S2"
    return {
        "action": "decompose",
        "found": found,
        "splits": found_splits,
        "count": len(found_splits),
        "phase": state["phase"],
        "hint": (
            "Run /deep-project and save domain splits to "
            f"{proj_dir / 'splits'}/"
        ) if not found else "",
    }


def _check_spec(proj_dir: Path, state: dict, project_root: str) -> dict:
    """Check if deep-plan specs exist and convert to foundry format."""
    splits_dir = proj_dir / "splits"
    spec_files = []
    if splits_dir.exists():
        for f in sorted(splits_dir.iterdir()):
            if f.is_file() and f.suffix == ".md":
                spec_files.append(f)

    if not spec_files:
        return {
            "action": "spec",
            "found": False,
            "phase": state["phase"],
            "hint": "No spec markdown files found in splits/. Run /deep-plan for each split.",
        }

    # Convert all splits into unified spec.md and plan.md
    spec_path = proj_dir / "spec.md"
    plan_path = proj_dir / "plan.md"
    result = _convert_to_foundry_format(spec_files, spec_path, plan_path, state)

    if result.get("error"):
        state["phases"]["S3_validate"]["status"] = "failed"
        return {
            "action": "spec",
            "found": True,
            "converted": False,
            "error": result["error"],
            "phase": state["phase"],
        }

    req_count = result["requirement_count"]
    state["phases"]["S2_plan"]["status"] = "complete"
    state["phases"]["S2_plan"]["specs_done"] = len(spec_files)
    state["phases"]["S3_validate"]["status"] = "complete"
    state["phases"]["S3_validate"]["requirement_count"] = req_count
    state["phase"] = "READY"
    state["foundry_ready"] = True
    state["foundry_spec_path"] = str(spec_path)
    state["foundry_plan_path"] = str(plan_path)

    return {
        "action": "spec",
        "found": True,
        "converted": True,
        "requirement_count": req_count,
        "nfr_count": result.get("nfr_count", 0),
        "ac_count": result.get("ac_count", 0),
        "arch_sections": result.get("arch_sections", 0),
        "spec_path": str(spec_path),
        "plan_path": str(plan_path),
        "phase": "READY",
    }


def forge_spec_check(
    project_name: str, action: str, project_root: str = "."
) -> dict:
    """Validate that a pipeline step completed."""
    proj_dir = _get_project_dir(project_root, project_name)
    state_path = proj_dir / "state.json"
    if not state_path.exists():
        return {"error": f"No forge-spec project '{project_name}'. Run Forge-Spec-Start first."}

    state = _load_json(state_path)

    if action == "codebase":
        result = _check_codebase(proj_dir, state)
    elif action == "decompose":
        result = _check_decompose(proj_dir, state)
    elif action == "spec":
        result = _check_spec(proj_dir, state, project_root)
    else:
        return {"error": f"Unknown action '{action}'. Use: codebase, decompose, spec"}

    _save_json(state_path, state)
    return result


# ── Forge-Spec-Status ────────────────────────────────────────────────────────


def forge_spec_status(project_name: str, project_root: str = ".") -> dict:
    """Show full pipeline state with phase checklist."""
    proj_dir = _get_project_dir(project_root, project_name)
    state_path = proj_dir / "state.json"
    if not state_path.exists():
        return {"error": f"No forge-spec project '{project_name}'. Run Forge-Spec-Start first."}

    state = _load_json(state_path)
    phases = state.get("phases", {})

    checklist = []
    for phase_key in _PHASES:
        phase_data = phases.get(phase_key, {})
        status = phase_data.get("status", "pending")
        label = _PHASE_LABELS.get(phase_key, phase_key)
        item = {"phase": label, "status": status}
        if phase_key == "S1_decompose" and status == "complete":
            item["splits"] = phase_data.get("count", 0)
        if phase_key == "S2_plan":
            item["specs_done"] = phase_data.get("specs_done", 0)
            item["specs_total"] = phase_data.get("specs_total", 0)
        if phase_key == "S3_validate" and status == "complete":
            item["requirements"] = phase_data.get("requirement_count", 0)
        checklist.append(item)

    return {
        "project_name": state.get("project_name", project_name),
        "phase": state.get("phase", "S0"),
        "foundry_ready": state.get("foundry_ready", False),
        "foundry_spec_path": state.get("foundry_spec_path", ""),
        "foundry_plan_path": state.get("foundry_plan_path", ""),
        "checklist": checklist,
    }


# ── Format Converter ─────────────────────────────────────────────────────────

# Patterns that indicate requirement-like content
_REQ_PATTERNS = re.compile(
    r"(?:feature|requirement|story|user\s+story|us[\-\s]?\d+|fr[\-\s]?\d+)",
    re.IGNORECASE,
)
_NFR_PATTERNS = re.compile(
    r"(?:performance|security|scalability|reliability|availability|"
    r"non[\-\s]?functional|nfr|constraint|compliance)",
    re.IGNORECASE,
)
_ARCH_PATTERNS = re.compile(
    r"(?:architecture|design|pattern|component|module|dependency|"
    r"tech\s*stack|infrastructure|deployment|file\s+map|directory)",
    re.IGNORECASE,
)


def _convert_to_foundry_format(
    spec_files: list[Path], spec_out: Path, plan_out: Path, state: dict
) -> dict:
    """Convert deep-plan spec files into foundry-compatible spec.md and plan.md."""
    us_counter = 1
    nfr_counter = 1
    ac_counter = 1
    arch_sections = 0

    spec_lines: list[str] = [
        "# Requirements Specification",
        "",
        f"*Generated by forge-spec on {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}*",
        "",
    ]
    plan_lines: list[str] = [
        "# Architecture & Implementation Plan",
        "",
        f"*Generated by forge-spec on {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}*",
        "",
    ]

    for spec_file in spec_files:
        content = spec_file.read_text(encoding="utf-8")
        lines = content.split("\n")
        domain_name = spec_file.stem.replace("-", " ").replace("_", " ").title()

        # Classify sections and extract requirements
        current_section_type = None  # "req", "nfr", "arch", None
        current_heading = ""
        section_buffer: list[str] = []

        for line in lines:
            heading_match = re.match(r"^(#{1,4})\s+(.+)", line)

            if heading_match:
                # Flush previous section
                if section_buffer:
                    us_counter, nfr_counter, ac_counter, arch_sections = _flush_section(
                        current_section_type, current_heading, section_buffer,
                        spec_lines, plan_lines, domain_name,
                        us_counter, nfr_counter, ac_counter, arch_sections,
                    )
                    section_buffer = []

                heading_text = heading_match.group(2)
                if _ARCH_PATTERNS.search(heading_text):
                    current_section_type = "arch"
                elif _NFR_PATTERNS.search(heading_text):
                    current_section_type = "nfr"
                elif _REQ_PATTERNS.search(heading_text):
                    current_section_type = "req"
                else:
                    # Heuristic: headings not matching arch go to spec by default
                    current_section_type = "req"
                current_heading = heading_text
            section_buffer.append(line)

        # Flush last section
        if section_buffer:
            us_counter, nfr_counter, ac_counter, arch_sections = _flush_section(
                current_section_type, current_heading, section_buffer,
                spec_lines, plan_lines, domain_name,
                us_counter, nfr_counter, ac_counter, arch_sections,
            )

    # Write outputs
    spec_out.write_text("\n".join(spec_lines) + "\n", encoding="utf-8")
    plan_out.write_text("\n".join(plan_lines) + "\n", encoding="utf-8")

    total_reqs = (us_counter - 1) + (nfr_counter - 1)
    return {
        "requirement_count": total_reqs,
        "nfr_count": nfr_counter - 1,
        "ac_count": ac_counter - 1,
        "arch_sections": arch_sections,
    }


def _flush_section(
    section_type: str | None,
    heading: str,
    buffer: list[str],
    spec_lines: list[str],
    plan_lines: list[str],
    domain_name: str,
    us_counter: int,
    nfr_counter: int,
    ac_counter: int,
    arch_sections: int,
) -> tuple[int, int, int, int]:
    """Process a section buffer and append to the appropriate output."""
    if not buffer or not section_type:
        return us_counter, nfr_counter, ac_counter, arch_sections

    text = "\n".join(buffer)

    if section_type == "arch":
        plan_lines.append(f"## {domain_name}: {heading}")
        plan_lines.append("")
        # Pass through architecture content as-is
        for line in buffer:
            if not re.match(r"^#{1,4}\s+", line):
                plan_lines.append(line)
        plan_lines.append("")
        arch_sections += 1

    elif section_type == "nfr":
        spec_lines.append(f"## {domain_name}: {heading}")
        spec_lines.append("")
        # Assign NFR IDs to list items and paragraphs
        for line in buffer:
            if re.match(r"^#{1,4}\s+", line):
                continue
            item_match = re.match(r"^(\s*[-*]\s+)(.*)", line)
            if item_match:
                spec_lines.append(f"{item_match.group(1)}**NFR-{nfr_counter:03d}:** {item_match.group(2)}")
                nfr_counter += 1
            elif line.strip():
                spec_lines.append(line)
        spec_lines.append("")

    else:  # "req"
        spec_lines.append(f"## {domain_name}: {heading}")
        spec_lines.append("")
        for line in buffer:
            if re.match(r"^#{1,4}\s+", line):
                continue
            item_match = re.match(r"^(\s*[-*]\s+)(.*)", line)
            if item_match:
                indent = item_match.group(1)
                content = item_match.group(2)
                # Sub-items become acceptance criteria
                if indent.startswith("  ") or indent.startswith("\t"):
                    spec_lines.append(f"{indent}**AC-{ac_counter:03d}:** {content}")
                    ac_counter += 1
                else:
                    spec_lines.append(f"{indent}**US-{us_counter:03d}:** {content}")
                    us_counter += 1
            elif line.strip():
                spec_lines.append(line)
        spec_lines.append("")

    return us_counter, nfr_counter, ac_counter, arch_sections
