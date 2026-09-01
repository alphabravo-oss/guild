"""Forge-Spec pipeline — state management and format conversion.

Orchestrates community plugins (Understand-Anything, Deep-Project, Deep-Plan)
through an MCP state machine to produce foundry-compatible spec and plan files.

All operations are local file reads/writes against foundry-planning/{project}/.
Zero API calls. Zero cost.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

# D-130 — the THIRD `_load_json` copy, imported now instead of re-typed.
#
# TEMPER group A named this file in bold: "Fix once, in one place -- patching
# only foundry.py:159 leaves two live copies". D-098 hardened the other two and
# this one stayed the unguarded original -- `json.loads(path.read_text(...))`
# with no try/except and no isinstance -- behind three tools that ARE in the
# server's _DISPATCH. Driven at ab5a430: 9 corruption shapes x 2 doors = 18/18
# raises, 0/18 named refusals, JSONDecodeError for the syntax shapes and
# `AttributeError: 'list' object has no attribute 'get'` for the wrong-type
# shapes -- byte-for-byte the D-098 signature, two fixes later.
#
# The import is the fix, not a fourth copy of the tolerance. `_save_json` is
# gone entirely: `_document_transaction` does the read-modify-write under an
# flock with a per-writer tmp sidecar, so the shared `path.with_suffix(".tmp")`
# that carried the D-103 shape here has no successor.
from foundry_mcp.tools.foundry_orchestrator import (
    _document_problem,
    _document_transaction,
    _load_json,
)
from foundry_mcp.tools.foundry_state import read_text_file

PLANNING_DIR = "foundry-planning"

_PHASES = ["S0_understand", "S1_decompose", "S2_plan", "S3_validate"]
_PHASE_LABELS = {
    "S0_understand": "S0: UNDERSTAND",
    "S1_decompose": "S1: DECOMPOSE",
    "S2_plan": "S2: PLAN",
    "S3_validate": "S3: VALIDATE",
    "READY": "READY",
}


def _planning_guard(proj_dir: Path) -> dict | None:
    """Named refusal when a planning artifact cannot be read, else None.

    Membership is DERIVED -- every top-level ``*.json`` in the project
    directory, the same rule ``_run_artifact_problems`` applies to a run
    directory -- so a forge-spec artifact added later is guarded the day it is
    written rather than the cycle someone remembers it. There is one such file
    today; naming it would be the defect this guard exists to close.

    D-140: the ``if c.is_file()`` filter that used to sit here was a hole of
    exactly the shape this guard exists to close. A DIRECTORY named
    ``state.json`` is not a file, so the guard skipped it and reported the
    project clean -- and then Check raised IsADirectoryError out of the write
    while Start and Status returned ok over a fabricated all-default state:
    three doors, three different stories about one broken path. A path
    OCCUPYING an artifact's name is the guard's business whatever kind of
    thing it is; ``_document_problem`` already names it, because the read
    raises OSError and OSError is what it reports.
    """
    if not proj_dir.exists():
        return None
    problems = [
        p
        for p in (_document_problem(c) for c in sorted(proj_dir.glob("*.json")))
        if p
    ]
    return _planning_refusal(problems)


def _planning_refusal(problems: list[str]) -> dict | None:
    """The house refusal for an unusable planning artifact, shaped ONCE.

    Two rungs return it — the document rung (``_planning_guard``: the file is
    not a readable JSON object) and the record rung (``_state_shape_problem``:
    it is a fine JSON object whose inner collection is the wrong type). Written
    here rather than at each so the three doors cannot tell an operator two
    different stories about one broken file, which is the property D-140 found
    missing: Check raised, Start and Status returned ok over a fabricated
    state.
    """
    if not problems:
        return None
    return {
        "error": (
            "Forge-spec state cannot be read: " + "; ".join(problems) + ". "
            "This tool refuses rather than acting on a document it had to "
            "guess at, or writing over one whose contents it could not read."
        ),
        "hint": (
            "Repair or delete the named file(s) under the project's "
            f"{PLANNING_DIR}/ directory, then retry. A deleted state file is "
            "re-created by Forge-Spec-Start; a corrupt one is not silently "
            "overwritten."
        ),
        "corrupt_artifacts": problems,
    }


def _wrong_type(present: object, expected: object) -> bool:
    """True when ``present`` cannot stand in for a value shaped like ``expected``.

    ``bool`` is checked in both directions because ``isinstance(True, int)`` is
    True in Python, so a bare isinstance would accept ``foundry_ready: 1`` and,
    worse, accept ``True`` where an int was meant.
    """
    if isinstance(present, bool) != isinstance(expected, bool):
        return True
    return not isinstance(present, type(expected))


def _state_shape_problem(state: dict, project_name: str) -> str | None:
    """Named reason a PRESENT key of state.json cannot be used, else None.

    D-140 — THE RUNG BELOW THE CONTAINER, AGAIN, AND THE WORSE FAILURE MODE.
    ``_sound_state`` REPAIRED a valid JSON object whose inner collection was
    the wrong type, and Forge-Spec-Check returned a clean success dict with no
    "error" key while rewriting the file. Driven with an operator's real state:

        {"phase": "READY", "foundry_ready": true, "phases": "S0_understand", ...}

    came back with "phases" replaced by four fresh {"status": "pending"}
    defaults -- the record of which planning phases were completed GONE --
    while "foundry_ready": true survived beside them, an internally
    inconsistent state Forge-Spec-Status then reported as normal.
    ``_document_problem`` cannot name it because the top-level object is a
    perfectly good mapping. This is D-132's container-vs-records distinction in
    a third module, with the failure mode INVERTED: not a raise but a silent
    destructive repair reported as success, which is the worse of the two
    because nothing tells the operator anything happened.

    ABSENT is not WRONG, and that distinction is the whole function. A missing
    key legitimately takes its default -- that is what lets a partial state
    resume -- while a key that is PRESENT and of the wrong type is a record
    someone wrote, and overwriting it destroys data. Only the second refuses.
    """
    default = _default_state(project_name)
    for key, value in default.items():
        present = state.get(key)
        if present is None or not _wrong_type(present, value):
            continue
        return (
            f"state.json's {key!r} holds {type(present).__name__}, not "
            f"{type(value).__name__} — repairing it would discard whatever it "
            f"does hold"
        )

    phases = state.get("phases")
    if isinstance(phases, dict):
        for key in default["phases"]:
            sub = phases.get(key)
            if sub is None or isinstance(sub, dict):
                continue
            return (
                f"state.json's phases[{key!r}] holds {type(sub).__name__}, not "
                f"a JSON object — repairing it would discard the record of "
                f"whether that phase completed"
            )
    return None


def _state_refusal(state: dict, project_name: str) -> dict | None:
    """The named refusal for an unusable state document, or None.

    Every door calls this on the same evidence, so a state one door refuses is
    one all three refuse — D-140's "must refuse at all three doors
    identically".
    """
    problem = _state_shape_problem(state, project_name)
    return _planning_refusal([problem] if problem else [])


def _sound_state(state: dict, project_name: str) -> dict:
    """Repair ``state`` in place to the shape every reader below assumes.

    The tolerant loader closes the RAISE, not the SHAPE: a corrupt document
    reads as ``{}``, and `{}["phases"]` is a KeyError one line later, which
    lands on the MCP boundary exactly as the JSONDecodeError did. A document
    that is a valid JSON object with ``"phases": []`` reaches the same place by
    a different route -- that is corruption shape 8 of the D-130 drive,
    `'list' object has no attribute 'get'`.

    So the shape is completed ONCE, here, rather than defended at each of the
    three doors: a MISSING key takes the default and every usable value
    survives.

    D-140: this used to repair a wrong-TYPED key too, which is not completion
    but destruction — ``"phases": "S0_understand"`` became four fresh
    ``{"status": "pending"}`` defaults and the record of which phases had
    completed was gone, with ok returned and nothing said. Absent and
    wrong-typed are now different things: this fills in the first, and
    ``_state_shape_problem`` refuses the second BEFORE this is reached. So
    everything below is a pure fill-in over keys nobody wrote, and it can no
    longer overwrite a value someone did.
    """
    default = _default_state(project_name)
    for key, value in default.items():
        if key == "phases":
            continue
        if state.get(key) is None:
            state[key] = value
    if state.get("phases") is None:
        state["phases"] = {}
    phases = state["phases"]
    for key, value in default["phases"].items():
        if phases.get(key) is None:
            phases[key] = value
    return state


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
    if (corrupt := _planning_guard(proj_dir)):
        return corrupt

    # Resume if state already exists
    state_path = proj_dir / "state.json"
    if state_path.exists():
        loaded = _load_json(state_path)
        if (corrupt := _state_refusal(loaded, project_name)):
            return corrupt
        state = _sound_state(loaded, project_name)
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

    spec_path = str(proj_dir / "spec.md")
    plan_path = str(proj_dir / "plan.md")
    with _document_transaction(state_path) as state:
        state.update(_default_state(project_name))
        state["foundry_spec_path"] = spec_path
        state["foundry_plan_path"] = plan_path
        snapshot = dict(state)
    state = snapshot

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
    if (corrupt := _planning_guard(proj_dir)):
        return corrupt

    # ONE critical section over state.json: the check helpers mutate the
    # document and the write is the same transaction's exit, so a concurrent
    # Forge-Spec-Check cannot read this call's pre-image and write over its
    # result. The unknown-action branch mutates nothing, and a transaction that
    # mutates nothing writes nothing -- the file is left byte-identical.
    with _document_transaction(state_path) as state:
        # D-140: inside the transaction, so the refusal is decided on the same
        # document the write would have used — checking outside would leave a
        # window where a peer's write turns a refused state into a repaired one.
        if (corrupt := _state_refusal(state, project_name)):
            return corrupt
        _sound_state(state, project_name)
        if action == "codebase":
            result = _check_codebase(proj_dir, state)
        elif action == "decompose":
            result = _check_decompose(proj_dir, state)
        elif action == "spec":
            result = _check_spec(proj_dir, state, project_root)
        else:
            result = {"error": f"Unknown action '{action}'. Use: codebase, decompose, spec"}
    return result


# ── Forge-Spec-Status ────────────────────────────────────────────────────────


def forge_spec_status(project_name: str, project_root: str = ".") -> dict:
    """Show full pipeline state with phase checklist."""
    proj_dir = _get_project_dir(project_root, project_name)
    state_path = proj_dir / "state.json"
    if not state_path.exists():
        return {"error": f"No forge-spec project '{project_name}'. Run Forge-Spec-Start first."}

    if (corrupt := _planning_guard(proj_dir)):
        return corrupt

    # Status is a READ: the shape is repaired on the in-memory copy and the
    # file is not rewritten, so asking what state a project is in never
    # changes it.
    loaded = _load_json(state_path)
    if (corrupt := _state_refusal(loaded, project_name)):
        return corrupt
    state = _sound_state(loaded, project_name)
    phases = state["phases"]

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
    unreadable: list[str] = []

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
        # D-140's residual, swept with D-137: this was a bare `read_text`, so
        # one non-UTF-8 byte in a splits/*.md raised UnicodeDecodeError out of
        # Forge-Spec-Check[spec]. The unreadable split is REPORTED rather than
        # skipped silently — a spec built from files it could not read is a
        # worse outcome than one that names the file it could not read.
        content, content_problem = read_text_file(spec_file)
        if content_problem is not None:
            unreadable.append(content_problem)
            continue
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
    result = {
        "requirement_count": total_reqs,
        "nfr_count": nfr_counter - 1,
        "ac_count": ac_counter - 1,
        "arch_sections": arch_sections,
    }
    if unreadable:
        result["unreadable_splits"] = unreadable
    return result


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
