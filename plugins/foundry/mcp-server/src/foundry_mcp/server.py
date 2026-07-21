"""Guild MCP Server — tool registration and entry point."""

from __future__ import annotations

import argparse
import json
import sys

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

from foundry_mcp.tools.citation import verify_citations
from foundry_mcp.tools.foundry import (
    foundry_add_defect,
    foundry_add_verdict,
    foundry_init,
    foundry_query_defects,
    foundry_verify_coverage,
)
from foundry_mcp.tools.foundry_orchestrator import (
    foundry_clear_directives,
    foundry_defects_to_tasks,
    foundry_gate,
    foundry_get_context,
    foundry_inject_directive,
    foundry_mark_defect_fixed,
    foundry_mark_phase_complete,
    foundry_mark_stream,
    foundry_next_action,
    foundry_register_team,
    foundry_sync_defects,
    foundry_unregister_team,
)
from foundry_mcp.tools.display import format_result
from foundry_mcp.tools.forge_spec import (
    forge_spec_check,
    forge_spec_start,
    forge_spec_status,
)
from foundry_mcp.tools.foundry_handoff import (
    foundry_accept_casting,
    foundry_handoff,
    foundry_spec_hash,
)
from foundry_mcp.tools.foundry_spawn import foundry_cast_wave, foundry_spawn_teammate
from foundry_mcp.tools.foundry_validate import foundry_validate_castings
from foundry_mcp.tools.intent_coverage import foundry_intent_coverage
from foundry_mcp.tools.validation import validate_report

# Global project root, set via CLI arg
_project_root: str = "."

server = Server("Foundry")


@server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="Validate-Report",
            description="Validate a report's JSON block against a built-in schema (trace, prove, temper).",
            inputSchema={
                "type": "object",
                "required": ["report_path"],
                "properties": {
                    "report_path": {"type": "string", "description": "Path to the markdown report file."},
                    "schema_name": {"type": "string", "enum": ["trace", "prove", "temper", "custom"], "default": "trace"},
                    "schema_path": {"type": "string", "description": "Path to custom JSON schema (overrides schema_name)."},
                    "auto_fix": {"type": "boolean", "default": False, "description": "Auto-fix common issues."},
                },
            },
        ),
        Tool(
            name="Verify-Citations",
            description="Cross-reference spec requirements with PROVE verdicts for traceability.",
            inputSchema={
                "type": "object",
                "required": ["spec_path", "report_path"],
                "properties": {
                    "spec_path": {"type": "string", "description": "Path to the LISA spec."},
                    "report_path": {"type": "string", "description": "Path to the critic report."},
                    "strict": {"type": "boolean", "default": False, "description": "Fail if any requirement uncovered."},
                },
            },
        ),
        # ── Foundry ──────────────────────────────────────────────
        Tool(
            name="Foundry-Init",
            description=(
                "Start a new foundry run under foundry-archive/ or resume an existing one. "
                "Auto-generates a unique name. Each session tracks its active run in memory."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "spec_path": {"type": "string", "description": "Path to spec file to copy."},
                    "temper": {"type": "boolean", "default": False},
                    "no_ui": {"type": "boolean", "default": False},
                    "resume": {"type": "string", "description": "Name of existing run to resume (e.g. 'bold-falcon')."},
                    "ticket": {"type": "string", "default": ""},
                    "description": {"type": "string", "default": ""},
                    "url": {"type": "string", "default": "", "description": "Target URL for SIGHT audit; persisted to castings/manifest.json target_url."},
                },
            },
        ),
        Tool(
            name="Foundry-Next",
            description=(
                "Guidance engine — returns exactly what to do next with rich status display. "
                "Call this instead of reading SKILL.md. Authoritative."
            ),
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="Foundry-Context",
            description="Reload all foundry state in one call. Use after compaction or session start.",
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="Foundry-Gate",
            description="Check preconditions before entering a phase. Returns pass/fail with checklist.",
            inputSchema={
                "type": "object",
                "required": ["phase"],
                "properties": {
                    "phase": {"type": "string", "enum": ["validate", "cast", "inspect", "grind", "assay", "temper", "nyquist", "done"]},
                },
            },
        ),
        Tool(
            name="Foundry-Phase",
            description="Mark a phase transition. Validates preconditions and updates state.",
            inputSchema={
                "type": "object",
                "required": ["phase"],
                "properties": {
                    "phase": {"type": "string", "enum": ["research_done", "decompose_done", "validate_done", "start_cast", "cast", "inspect_clean", "grind_start", "assay_fail", "temper", "nyquist_done", "done"]},
                },
            },
        ),
        Tool(
            name="Foundry-Defect",
            description="Log a defect from any verification stream. Appends to ledger and forge-log.",
            inputSchema={
                "type": "object",
                "required": ["cycle", "source", "defect_type", "description"],
                "properties": {
                    "cycle": {"type": "integer"},
                    "source": {"type": "string", "enum": ["trace", "prove", "research_audit", "coverage_diff", "sight", "test", "assay", "temper"]},
                    "defect_type": {"type": "string", "enum": ["MISSING", "WRONG", "THIN", "HOLLOW", "UNWIRED", "BROKEN", "FAIL", "RESEARCH_DEVIATION", "COVERAGE_INCOMPLETE", "THIN_MIGRATION"]},
                    "description": {"type": "string"},
                    "spec_ref": {"type": "string"},
                    "symbol": {"type": "string"},
                    "file_path": {"type": "string"},
                },
            },
        ),
        Tool(
            name="Foundry-Defects",
            description="Query the defect ledger with optional filters (status, cycle, source, spec_ref).",
            inputSchema={
                "type": "object",
                "properties": {
                    "status": {"type": "string", "enum": ["open", "fixed"]},
                    "cycle": {"type": "integer"},
                    "source": {"type": "string"},
                    "spec_ref": {"type": "string"},
                },
            },
        ),
        Tool(
            name="Foundry-Fix",
            description="Mark a defect as fixed in this cycle.",
            inputSchema={
                "type": "object",
                "required": ["defect_id", "cycle"],
                "properties": {
                    "defect_id": {"type": "string"},
                    "cycle": {"type": "integer"},
                },
            },
        ),
        Tool(
            name="Foundry-Sync",
            description="Sync new findings against existing defects. Detects regressions automatically.",
            inputSchema={
                "type": "object",
                "required": ["cycle", "findings"],
                "properties": {
                    "cycle": {"type": "integer"},
                    "findings": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "required": ["description"],
                            "properties": {
                                "description": {"type": "string"},
                                "source": {"type": "string"},
                                "symbol": {"type": "string"},
                                "file": {"type": "string"},
                                "spec_ref": {"type": "string"},
                                "type": {"type": "string"},
                            },
                        },
                    },
                },
            },
        ),
        Tool(
            name="Foundry-Tasks",
            description="Convert all open defects to grouped GRIND tasks.",
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="Foundry-Verdict",
            description="Record a spec requirement verdict with evidence and citation.",
            inputSchema={
                "type": "object",
                "required": ["requirement_id", "verdict", "evidence"],
                "properties": {
                    "requirement_id": {"type": "string"},
                    "verdict": {"type": "string", "enum": ["VERIFIED", "HOLLOW", "THIN", "PARTIAL", "MISSING", "WRONG", "COVERAGE_INCOMPLETE"]},
                    "evidence": {"type": "string"},
                    "spec_text_cited": {"type": "string"},
                    "code_location": {"type": "string"},
                    "cycle": {"type": "integer", "default": 0},
                },
            },
        ),
        Tool(
            name="Foundry-Coverage",
            description="Traceability matrix: spec requirements -> verdicts -> defects -> code evidence.",
            inputSchema={
                "type": "object",
                "properties": {
                    "spec_path": {"type": "string"},
                },
            },
        ),
        Tool(
            name="Foundry-Stream",
            description="Mark a verification stream complete with coverage data. Requires items_checked > 0.",
            inputSchema={
                "type": "object",
                "required": ["stream", "cycle", "items_checked"],
                "properties": {
                    "stream": {"type": "string", "enum": ["trace", "prove", "research_audit", "coverage_diff", "sight", "test", "probe"]},
                    "cycle": {"type": "integer"},
                    "items_checked": {"type": "integer"},
                    "items_total": {"type": "integer"},
                    "findings_count": {"type": "integer", "default": 0},
                },
            },
        ),
        Tool(
            name="Foundry-Validate-Castings",
            description="Validate castings against spec across 9 dimensions before CAST. Includes Prompt Fidelity (with <global_invariants> propagation), Migration Coverage, and Spec Structure (tagged requirement IDs + optional global_invariants section). Returns pass/fail with revision hints.",
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="Foundry-Intent-Coverage",
            description=(
                "Validate intent-coverage.json (Phase 8 / INTENT-01) against the "
                "transcript-in-spec-appendix and emitted casting prompts. Runs at "
                "F0.7 between F0.5 DECOMPOSE and F0.9 VALIDATE. Returns "
                "{passed, dropped_answers, paraphrased_answers, propagated_count, "
                "matrix_path}. On any DROPPED, returns {action: 'redecompose'} "
                "with the missing A-NNN list as re-decompose guidance — never "
                "amends casting prompts in place."
            ),
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="Foundry-Spawn-Teammate",
            description=(
                "Read the pre-authored teammate prompt for a casting and return it verbatim. "
                "The lead MUST pass the returned `prompt` field directly to the Agent tool without "
                "modification. Authored at F0.5 DECOMPOSE from the spec, validated at F0.9, frozen. "
                "Plans are prompts: the lead is a router, not an interpreter. "
                "Prefer Foundry-Cast-Wave for wave-level bulk fetch — single casting lookups "
                "are for GRIND or one-off re-dispatches."
            ),
            inputSchema={
                "type": "object",
                "required": ["casting_id"],
                "properties": {
                    "casting_id": {"type": ["integer", "string"], "description": "Casting id from manifest.json."},
                    "phase": {"type": "string", "enum": ["cast", "grind"], "default": "cast"},
                },
            },
        ),
        Tool(
            name="Foundry-Cast-Wave",
            description=(
                "Bulk-fetch prompts for every casting in a wave as a single MCP call. "
                "Replaces N sequential Foundry-Spawn-Teammate roundtrips for a CAST wave. "
                "Returns {castings: [{casting_id, prompt, prompt_hash}, ...], team_name_suggestion, "
                "instructions}. Lead then does TeamCreate + Foundry-Team-Up + a SINGLE parallel Agent "
                "tool-use message with one Agent per casting. Preserves audit trail — every casting "
                "is still logged to spawns.log with bulk=true."
            ),
            inputSchema={
                "type": "object",
                "required": ["wave"],
                "properties": {
                    "wave": {"type": "integer", "description": "1-indexed wave number from manifest.waves."},
                    "phase": {"type": "string", "enum": ["cast", "grind"], "default": "cast"},
                },
            },
        ),
        Tool(
            name="Foundry-Spec-Hash",
            description=(
                "Return the current sha256 of spec.md. Call this before every Foundry-Accept-Casting "
                "to force a re-read of the spec. Never accept a casting using a hash from memory — "
                "context rot makes prior-cycle hashes unreliable."
            ),
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="Foundry-Handoff",
            description=(
                "Record a handoff event in the audit log. Every phase transition and artifact "
                "production should be recorded with source, destination, hashes, and whether "
                "the lead re-read the source. Writes to foundry-archive/{run}/handoffs.md and "
                "handoffs.jsonl."
            ),
            inputSchema={
                "type": "object",
                "required": ["event"],
                "properties": {
                    "event": {"type": "string", "description": "e.g. spec_to_casting, casting_to_teammate, teammate_to_accepted, inspect_to_grind, grind_to_inspect, assay_to_done, spec_reread"},
                    "source": {"type": "string", "description": "Path to source artifact (relative to project root)."},
                    "destination": {"type": "string", "description": "Path to destination artifact."},
                    "source_reread": {"type": "boolean", "default": False, "description": "Did the lead just re-read the source before this handoff? Critical for spec→casting and acceptance handoffs."},
                    "summary": {"type": "string"},
                    "information_loss": {"type": "string", "description": "If non-empty, describes what was dropped from source in destination."},
                },
            },
        ),
        Tool(
            name="Foundry-Accept-Casting",
            description=(
                "Gate acceptance of a completed casting. Requires fresh spec_hash and prompt_hash "
                "(verifies re-reads happened), extracts the casting's acceptance criteria from the "
                "<spec_requirements> block, checks the completion report for scope-flag phrases, "
                "and mechanically verifies every requirement ID in the casting's spec slice "
                "has a file:line citation in the completion report. Returns the AC list, requirement "
                "IDs, and any missing citations. Blocks acceptance if the teammate reported scope "
                "cuts OR any requirement has no citation."
            ),
            inputSchema={
                "type": "object",
                "required": ["casting_id", "spec_hash", "prompt_hash", "completion_report"],
                "properties": {
                    "casting_id": {"type": ["integer", "string"]},
                    "spec_hash": {"type": "string", "description": "Fresh hash from Foundry-Spec-Hash."},
                    "prompt_hash": {"type": "string", "description": "Hash from Foundry-Spawn-Teammate."},
                    "completion_report": {"type": "string", "description": "The teammate's completion report text."},
                },
            },
        ),
        Tool(
            name="Foundry-Team-Up",
            description="Register a team for lifecycle tracking. Call after TeamCreate.",
            inputSchema={
                "type": "object",
                "required": ["team_name"],
                "properties": {"team_name": {"type": "string"}},
            },
        ),
        Tool(
            name="Foundry-Team-Down",
            description="Unregister a team. Kills lingering tmux panes and waits for cleanup.",
            inputSchema={
                "type": "object",
                "required": ["team_name"],
                "properties": {"team_name": {"type": "string"}},
            },
        ),
        Tool(
            name="Foundry-Directive",
            description="Inject a non-blocking directive. Lead reads it at every phase transition.",
            inputSchema={
                "type": "object",
                "required": ["directive"],
                "properties": {
                    "directive": {"type": "string"},
                    "priority": {"type": "string", "enum": ["normal", "urgent"], "default": "normal"},
                },
            },
        ),
        Tool(
            name="Foundry-Clear",
            description="Clear all directives after they've been addressed.",
            inputSchema={"type": "object", "properties": {}},
        ),
        # ── Forge-Spec ─────────────────────────────────────────────
        Tool(
            name="Forge-Spec-Start",
            description=(
                "Initialize a forge-spec project directory and state machine. "
                "Creates foundry-planning/{project}/ with research/, splits/, and state.json. "
                "Resumes if project already exists."
            ),
            inputSchema={
                "type": "object",
                "required": ["project_name"],
                "properties": {
                    "project_name": {"type": "string", "description": "Human-readable project name (e.g. 'BOM system for K3s')."},
                },
            },
        ),
        Tool(
            name="Forge-Spec-Check",
            description=(
                "Validate a forge-spec pipeline step completed. "
                "Actions: 'codebase' (knowledge graph exists?), 'decompose' (domain splits exist?), "
                "'spec' (deep-plan specs exist? converts to US-/FR- format)."
            ),
            inputSchema={
                "type": "object",
                "required": ["project_name", "action"],
                "properties": {
                    "project_name": {"type": "string", "description": "Project name or slug."},
                    "action": {
                        "type": "string",
                        "enum": ["codebase", "decompose", "spec"],
                        "description": "Which step to validate.",
                    },
                },
            },
        ),
        Tool(
            name="Forge-Spec-Status",
            description="Show forge-spec pipeline state with phase checklist.",
            inputSchema={
                "type": "object",
                "required": ["project_name"],
                "properties": {
                    "project_name": {"type": "string", "description": "Project name or slug."},
                },
            },
        ),
    ]


# ── Tool name -> function dispatch ───────────────────────────────────────────

_DISPATCH = {
    "Validate-Report": lambda args: validate_report(
        report_path=args["report_path"], schema_name=args.get("schema_name", "trace"),
        schema_path=args.get("schema_path"), auto_fix=args.get("auto_fix", False), project_root=_project_root),
    "Verify-Citations": lambda args: verify_citations(
        spec_path=args["spec_path"], report_path=args["report_path"],
        strict=args.get("strict", False), project_root=_project_root),
    "Foundry-Init": lambda args: foundry_init(
        spec_path=args.get("spec_path"), temper=args.get("temper", False), no_ui=args.get("no_ui", False),
        resume=args.get("resume"), ticket=args.get("ticket", ""), description=args.get("description", ""),
        url=args.get("url", ""), project_root=_project_root),
    "Foundry-Next": lambda args: foundry_next_action(project_root=_project_root),
    "Foundry-Context": lambda args: foundry_get_context(project_root=_project_root),
    "Foundry-Gate": lambda args: foundry_gate(phase=args["phase"], project_root=_project_root),
    "Foundry-Phase": lambda args: foundry_mark_phase_complete(phase=args["phase"], project_root=_project_root),
    "Foundry-Defect": lambda args: foundry_add_defect(
        cycle=args["cycle"], source=args["source"], defect_type=args["defect_type"],
        description=args["description"], spec_ref=args.get("spec_ref", ""),
        symbol=args.get("symbol", ""), file_path=args.get("file_path", ""), project_root=_project_root),
    "Foundry-Defects": lambda args: foundry_query_defects(
        status=args.get("status"), cycle=args.get("cycle"), source=args.get("source"),
        spec_ref=args.get("spec_ref"), project_root=_project_root),
    "Foundry-Fix": lambda args: foundry_mark_defect_fixed(
        defect_id=args["defect_id"], cycle=args["cycle"], project_root=_project_root),
    "Foundry-Sync": lambda args: foundry_sync_defects(
        cycle=args["cycle"], findings=args["findings"], project_root=_project_root),
    "Foundry-Tasks": lambda args: foundry_defects_to_tasks(project_root=_project_root),
    "Foundry-Verdict": lambda args: foundry_add_verdict(
        requirement_id=args["requirement_id"], verdict=args["verdict"], evidence=args["evidence"],
        spec_text_cited=args.get("spec_text_cited", ""), code_location=args.get("code_location", ""),
        cycle=args.get("cycle", 0), project_root=_project_root),
    "Foundry-Coverage": lambda args: foundry_verify_coverage(
        spec_path=args.get("spec_path"), project_root=_project_root),
    "Foundry-Stream": lambda args: foundry_mark_stream(
        stream=args["stream"], cycle=args["cycle"], items_checked=args.get("items_checked", 0),
        items_total=args.get("items_total", 0), findings_count=args.get("findings_count", 0),
        project_root=_project_root),
    "Foundry-Validate-Castings": lambda args: foundry_validate_castings(project_root=_project_root),
    "Foundry-Intent-Coverage": lambda args: foundry_intent_coverage(project_root=_project_root),
    "Foundry-Spawn-Teammate": lambda args: foundry_spawn_teammate(
        casting_id=args["casting_id"], phase=args.get("phase", "cast"), project_root=_project_root),
    "Foundry-Cast-Wave": lambda args: foundry_cast_wave(
        wave=args["wave"], phase=args.get("phase", "cast"), project_root=_project_root),
    "Foundry-Spec-Hash": lambda args: foundry_spec_hash(project_root=_project_root),
    "Foundry-Handoff": lambda args: foundry_handoff(
        event=args["event"], source=args.get("source", ""), destination=args.get("destination", ""),
        source_reread=args.get("source_reread", False), summary=args.get("summary", ""),
        information_loss=args.get("information_loss", ""), project_root=_project_root),
    "Foundry-Accept-Casting": lambda args: foundry_accept_casting(
        casting_id=args["casting_id"], spec_hash=args["spec_hash"],
        prompt_hash=args["prompt_hash"], completion_report=args["completion_report"],
        project_root=_project_root),
    "Foundry-Team-Up": lambda args: foundry_register_team(team_name=args["team_name"], project_root=_project_root),
    "Foundry-Team-Down": lambda args: foundry_unregister_team(team_name=args["team_name"], project_root=_project_root),
    "Foundry-Directive": lambda args: foundry_inject_directive(
        directive=args["directive"], priority=args.get("priority", "normal"), project_root=_project_root),
    "Foundry-Clear": lambda args: foundry_clear_directives(project_root=_project_root),
    "Forge-Spec-Start": lambda args: forge_spec_start(
        project_name=args["project_name"], project_root=_project_root),
    "Forge-Spec-Check": lambda args: forge_spec_check(
        project_name=args["project_name"], action=args["action"], project_root=_project_root),
    "Forge-Spec-Status": lambda args: forge_spec_status(
        project_name=args["project_name"], project_root=_project_root),
}


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    handler = _DISPATCH.get(name)
    if handler:
        result = handler(arguments)
    else:
        result = {"error": f"Unknown tool: {name}"}

    return [TextContent(type="text", text=format_result(name, result))]


def main():
    global _project_root

    parser = argparse.ArgumentParser(description="Guild MCP Server")
    parser.add_argument("--project-root", default=".", help="Project root directory.")
    args = parser.parse_args()
    _project_root = args.project_root

    import asyncio
    asyncio.run(_run())


async def _run():
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    main()
