"""Guild MCP Server — tool registration and entry point."""

from __future__ import annotations

import argparse
import json
import sys

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

from foundry_mcp import __version__

# FR-013 / CT-002 — the closed vocabularies this file advertises are READ, not
# re-typed. The AC-013 class of defect was exactly this file's hand-typed enums
# drifting away from the runtime guards, so the schema advertised streams the
# server rejected and rejected defect types the agent contracts were told to
# emit. Where each one comes from:
#
#   stream / source / defect_type   schemas/vocab.py — the wire vocabulary
#   verdict                         foundry_orchestrator.VERDICT_VALUES, which
#                                   derives from vocab's DEFECT_TYPES
#   phase                           the ONE remaining literal, spelled out
#                                   below with its own note on why, and pinned
#                                   to the handler's branch set by a test
#
# That drift is not cosmetic on this surface. The MCP SDK validates arguments
# against the advertised enum BEFORE dispatch, so a token missing from an enum
# here is unreachable no matter what the handler accepts.
from foundry_mcp.schemas.vocab import (
    DEFECT_SOURCE_IDS,
    DEFECT_TYPES,
    OBSERVATION_CLASSES,
    STREAM_WIRE_IDS,
)
from foundry_mcp.tools.citation import verify_citations
from foundry_mcp.tools.foundry import (
    foundry_add_defect,
    foundry_add_observation,
    foundry_add_verdict,
    foundry_init,
    foundry_query_defects,
    foundry_query_observations,
    foundry_verify_coverage,
)
from foundry_mcp.tools.foundry_orchestrator import (
    VERDICT_VALUES,
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
                    "nyquist": {"type": "boolean", "default": False, "description": "Enable the optional F5.5 NYQUIST phase; persisted to state.json and castings/manifest.json."},
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
                    # D-006 / D-007 — this enum is pinned to
                    # foundry_mark_phase_complete's OWN branch set by the drift
                    # guard in tests/test_orchestrator_gates.py, which reads the
                    # handler's AST. It drifted in both directions at once: it
                    # advertised research_done / decompose_done / validate_done,
                    # which the handler has no branch for and refuses, while
                    # OMITTING inspect_start — the only token whose branch
                    # advances the cycle counter. The SDK validates against this
                    # enum before dispatch, so that omission made the counter
                    # unable to leave 0 over MCP however the handler behaved.
                    # Spelled out rather than imported from
                    # foundry_orchestrator.PHASE_TOKENS because tests assert
                    # these tokens are READABLE in this file's own source; the
                    # drift guard is what keeps the two copies honest. See the
                    # concerns entry recommending both be collapsed once that
                    # source-grep assertion is replaced.
                    "phase": {"type": "string", "enum": [
                        "start_cast", "cast", "inspect_start", "inspect_clean",
                        "grind_start", "assay_fail", "temper", "nyquist",
                        "nyquist_done", "done",
                    ]},
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
                    "source": {"type": "string", "enum": sorted(DEFECT_SOURCE_IDS)},
                    "defect_type": {"type": "string", "enum": sorted(DEFECT_TYPES)},
                    "description": {"type": "string"},
                    "spec_ref": {"type": "string"},
                    "symbol": {"type": "string"},
                    "file_path": {"type": "string"},
                    # FR-001 / FR-007 — the handler has accepted both since the
                    # observations split landed, but neither had a schema
                    # property or a dispatch path, so over MCP the comment-prose
                    # refusal and class tagging were dead: a line-drift finding
                    # filed over MCP was accepted as a defect because
                    # target_kind never arrived to make it demotable.
                    "target_kind": {
                        "type": "string",
                        "description": (
                            "What the finding is ABOUT. Pass 'comment' when the "
                            "subject is a code comment — that declaration is what "
                            "lets the comment-prose refusal engage. Any other "
                            "value pins the finding as a defect that can never be "
                            "demoted to an observation."
                        ),
                    },
                    "defect_class": {
                        "type": "string",
                        "description": (
                            "Optional root-cause class shared by several "
                            "instances, persisted as the record's 'class'. A "
                            "class filed in 3 consecutive cycles escalates to "
                            "one structural-fix packet."
                        ),
                    },
                },
            },
        ),
        Tool(
            name="Foundry-Observation",
            description=(
                "Record a comment-prose finding in the run's observations "
                "ledger — the non-blocking half of the observation/defect "
                "split. Observations are typed, persisted per run, and NEVER "
                "mixed into the defect ledger. The never-demote denylist is "
                "absolute: a security-property claim, a spec-required-behaviour "
                "claim, an unresolvable cite, or anything that is not a declared "
                "comment is REFUSED here and the audit tripwire fires naming the "
                "entry that matched. Citing a requirement in spec_ref is by "
                "construction enough to keep a finding a defect."
            ),
            inputSchema={
                "type": "object",
                "required": ["cycle", "source", "description"],
                "properties": {
                    "cycle": {"type": "integer"},
                    "source": {"type": "string", "enum": sorted(DEFECT_SOURCE_IDS)},
                    "description": {"type": "string"},
                    "classification": {
                        "type": "string",
                        "enum": sorted(OBSERVATION_CLASSES),
                        "description": "Optional — derived from the description when omitted.",
                    },
                    "target_kind": {
                        "type": "string",
                        "default": "comment",
                        "description": (
                            "Must be 'comment': only comment prose is an "
                            "observation. Any other value is refused."
                        ),
                    },
                    "spec_ref": {"type": "string"},
                    "symbol": {"type": "string"},
                    "file_path": {"type": "string"},
                },
            },
        ),
        Tool(
            name="Foundry-Observations",
            description=(
                "Query the observations ledger, with the denylist tripwire log "
                "returned alongside so a validator never has to know the "
                "ledger's file layout."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "cycle": {"type": "integer"},
                    "source": {"type": "string", "enum": sorted(DEFECT_SOURCE_IDS)},
                    "classification": {"type": "string", "enum": sorted(OBSERVATION_CLASSES)},
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
            description=(
                "Mark a defect as fixed in this cycle. Requires an adjacent-path "
                "statement (who else calls this, what else transitions here, what "
                "runs concurrently) and a reference to a test that drives at least "
                "one of those adjacent paths. The call is REFUSED without both, "
                "naming each missing field — a fix whose blast radius is undeclared "
                "is how a defect closes and a regression opens in the same cycle."
            ),
            inputSchema={
                "type": "object",
                # MANDATORY, but deliberately NOT in `required` (D-039).
                #
                # The SDK runs `jsonschema.validate` before dispatch and returns
                # on the FIRST error, so listing both declarations here made the
                # handler's refusal unreachable for the exact call CT-001 is
                # written about: a caller omitting both got one
                # "'adjacent_path_statement' is a required property" and never
                # saw that the test reference was missing too. CT-001 requires a
                # refusal naming EACH missing field, and only the handler can
                # name more than one. Both fields are enforced — unconditionally
                # and with the fuller message — in `foundry_mark_defect_fixed`.
                #
                # The descriptions below carry the obligation to the caller, and
                # the tool description states the refusal outright.
                "required": ["defect_id", "cycle"],
                "properties": {
                    "defect_id": {"type": "string"},
                    "cycle": {"type": "integer"},
                    "adjacent_path_statement": {
                        "type": "string",
                        "description": (
                            "REQUIRED. Who ELSE calls this, what else transitions here, "
                            "what runs concurrently. Must name a path other than the one "
                            "the defect was found on."
                        ),
                    },
                    "adjacent_path_test": {
                        "type": "string",
                        "description": (
                            "REQUIRED. Reference to a test exercising at least one NAMED "
                            "adjacent path (e.g. tests/test_auth.py::test_refresh_reuses_session). "
                            "A locator, not a sentence: 'n/a', 'TODO' and 'tested it "
                            "manually' are refused, as is a test named for the defect's "
                            "own symbol."
                        ),
                    },
                },
            },
        ),
        Tool(
            name="Foundry-Sync",
            description=(
                "Sync new findings against existing defects. Detects regressions "
                "automatically. source and type are validated against the canonical "
                "vocabulary and never coerced: an unknown or absent source is refused "
                "rather than silently recorded as 'trace'. The whole batch is refused "
                "if any finding is invalid, so nothing lands half-applied."
            ),
            inputSchema={
                "type": "object",
                "required": ["cycle", "findings"],
                "properties": {
                    "cycle": {"type": "integer"},
                    "findings": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "required": ["description", "source"],
                            "properties": {
                                "description": {"type": "string"},
                                "source": {"type": "string", "enum": sorted(DEFECT_SOURCE_IDS)},
                                "symbol": {"type": "string"},
                                "file": {"type": "string"},
                                "spec_ref": {"type": "string"},
                                "type": {"type": "string", "enum": sorted(DEFECT_TYPES)},
                                "class": {
                                    "type": "string",
                                    "description": (
                                        "Optional root-cause class shared by several "
                                        "instances. A class filed in 3 consecutive "
                                        "cycles escalates to one structural-fix packet."
                                    ),
                                },
                                "target_kind": {
                                    "type": "string",
                                    "description": (
                                        "What the finding is ABOUT — 'comment' when the "
                                        "subject is a code comment, otherwise the kind of "
                                        "artifact. Populate it: any value other than "
                                        "'comment' pins the finding as a defect that can "
                                        "never be demoted to an observation."
                                    ),
                                },
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
                    # Derived, not hand-typed: the baseline literal here rejected
                    # MISPLACED, which agents/assayer.md mandates as a verdict
                    # word and commands/start.md routes into this tool — the
                    # protocol told an agent to emit a verdict the surface that
                    # records it could not represent.
                    "verdict": {"type": "string", "enum": sorted(VERDICT_VALUES)},
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
                    "stream": {"type": "string", "enum": sorted(STREAM_WIRE_IDS)},
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
                "matrix_path}. An answer_id is DROPPED (gate-blocking) only when "
                "every casting's cell for it is DROPPED; a PROPAGATED or "
                "PARAPHRASED cell in any casting keeps the gate open for that "
                "answer, and per-cell DROPPED verdicts remain recorded in the "
                "matrix without blocking. On any zero-coverage answer, returns "
                "{action: 'redecompose'} with the missing A-NNN list as "
                "re-decompose guidance — never amends casting prompts in place."
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
                    # FR-017 / AC-023 — the evidence gate's reachable half. The
                    # handler has always accepted casting_commit and gates the
                    # whole evidence re-execution block on `is not None`, but the
                    # parameter had no schema property and no dispatch path, so
                    # over MCP it was ALWAYS None: nothing in tools/evidence.py
                    # ever ran from a real run and manifest.evidence_provenance
                    # was never populated. Optional, so existing four-argument
                    # calls keep working.
                    "casting_commit": {
                        "type": "string",
                        "description": (
                            "The casting's commit SHA. Supplying it runs evidence "
                            "re-execution for that commit in an isolated worktree "
                            "and binds each requirement ID to committed evidence."
                        ),
                    },
                },
            },
        ),
        Tool(
            name="Foundry-Liveness",
            description=(
                "Report each spawned agent's last-progress age from the run's "
                "progress ledger, compared against a stall threshold, so a slow "
                "agent can be told from a dead one. Called with no argument it "
                "returns every agent in the run; with an agent identifier it "
                "returns only that agent. A run with no progress ledger returns an "
                "empty roster, not an error. Pass stall_seconds to override the "
                "900s threshold — a longer one for a phase whose steps are "
                "genuinely slow, a shorter one to sweep for wedged agents."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "agent": {
                        "type": "string",
                        "description": "Optional agent identifier. Omit for the whole roster.",
                    },
                    # D-002: implemented and tested in the handler, undeclared
                    # here, so the SDK rejected any call carrying it and
                    # commands/start.md documented a parameter no MCP caller
                    # could send.
                    #
                    # No `exclusiveMinimum`: the handler already refuses a
                    # non-positive threshold by name, and a schema bound would
                    # pre-empt that with a raw validator message — the same
                    # failure D-039 fixes on Foundry-Fix. `"number"` (which
                    # jsonschema does not satisfy with a bool) is exactly the
                    # handler's own accept-set.
                    "stall_seconds": {
                        "type": "number",
                        "description": (
                            "Optional stall-threshold override, in seconds. Must be "
                            "greater than 0. Omit for the run-derived default (900s)."
                        ),
                    },
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
            description=(
                "Inject a non-blocking directive. Lead reads it at every phase "
                "transition; urgent and normal directives are BOTH shown. "
                "Recognized control directive: 'escalation-override: <class>' "
                "de-escalates one recurring defect class back to per-instance "
                "packets (bare 'escalation-override' de-escalates every class)."
            ),
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
            description=(
                "Clear active directives after they've been addressed. The cleared "
                "text is preserved in directives-cleared.md — nothing is destroyed."
            ),
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


def _dispatch_liveness(args: dict) -> dict:
    """Dispatch Foundry-Liveness to its handler in tools/foundry_spawn.py.

    Imported lazily and unguarded: a module-top import would take the ENTIRE
    server down while the casting that owns foundry_spawn.py is still landing
    this handler, and swallowing the ImportError would hide a real wiring break
    behind a silent no-op. Failing here fails one tool, loudly, naming the
    symbol. The agent identifier and the threshold override are passed
    positionally so this registration does not depend on the handler's
    parameter NAMES.

    ``stall_seconds`` is forwarded unvalidated and un-defaulted (D-002): the
    handler owns both the default and the named refusal for a bad value, and
    re-deciding either here would give MCP callers different answers from
    in-process ones. ``None`` — the shape an omitted key takes — is exactly
    what the handler reads as "use the derived default".
    """
    from foundry_mcp.tools.foundry_spawn import foundry_liveness

    return foundry_liveness(
        args.get("agent"),
        args.get("stall_seconds"),
        project_root=_project_root,
    )


_DISPATCH = {
    "Validate-Report": lambda args: validate_report(
        report_path=args["report_path"], schema_name=args.get("schema_name", "trace"),
        schema_path=args.get("schema_path"), auto_fix=args.get("auto_fix", False), project_root=_project_root),
    "Verify-Citations": lambda args: verify_citations(
        spec_path=args["spec_path"], report_path=args["report_path"],
        strict=args.get("strict", False), project_root=_project_root),
    "Foundry-Init": lambda args: foundry_init(
        spec_path=args.get("spec_path"), temper=args.get("temper", False),
        nyquist=args.get("nyquist", False), no_ui=args.get("no_ui", False),
        resume=args.get("resume"), ticket=args.get("ticket", ""), description=args.get("description", ""),
        url=args.get("url", ""), project_root=_project_root),
    "Foundry-Next": lambda args: foundry_next_action(project_root=_project_root),
    "Foundry-Context": lambda args: foundry_get_context(project_root=_project_root),
    "Foundry-Gate": lambda args: foundry_gate(phase=args["phase"], project_root=_project_root),
    "Foundry-Phase": lambda args: foundry_mark_phase_complete(phase=args["phase"], project_root=_project_root),
    "Foundry-Defect": lambda args: foundry_add_defect(
        cycle=args["cycle"], source=args["source"], defect_type=args["defect_type"],
        description=args["description"], spec_ref=args.get("spec_ref", ""),
        symbol=args.get("symbol", ""), file_path=args.get("file_path", ""),
        target_kind=args.get("target_kind", ""), defect_class=args.get("defect_class", ""),
        project_root=_project_root),
    "Foundry-Defects": lambda args: foundry_query_defects(
        status=args.get("status"), cycle=args.get("cycle"), source=args.get("source"),
        spec_ref=args.get("spec_ref"), project_root=_project_root),
    "Foundry-Observation": lambda args: foundry_add_observation(
        cycle=args["cycle"], source=args["source"], description=args["description"],
        classification=args.get("classification", ""),
        target_kind=args.get("target_kind", "comment"),
        spec_ref=args.get("spec_ref", ""), symbol=args.get("symbol", ""),
        file_path=args.get("file_path", ""), project_root=_project_root),
    "Foundry-Observations": lambda args: foundry_query_observations(
        cycle=args.get("cycle"), source=args.get("source"),
        classification=args.get("classification"), project_root=_project_root),
    "Foundry-Fix": lambda args: foundry_mark_defect_fixed(
        defect_id=args["defect_id"], cycle=args["cycle"],
        adjacent_path_statement=args.get("adjacent_path_statement", ""),
        adjacent_path_test=args.get("adjacent_path_test", ""),
        project_root=_project_root),
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
        casting_commit=args.get("casting_commit"),
        project_root=_project_root),
    "Foundry-Liveness": lambda args: _dispatch_liveness(args),
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
    # The MCP entry is registered against an unpinned git URL, so the running
    # server version is decoupled from the installed plugin version and uvx
    # serves whatever commit it last resolved. --version is how a user (or
    # update-mcp.sh) can tell which one that actually is.
    parser.add_argument("--version", action="version", version=f"foundry-mcp {__version__}")
    args = parser.parse_args()
    _project_root = args.project_root

    import asyncio
    asyncio.run(_run())


async def _run():
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    main()
