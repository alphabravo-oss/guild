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
    DEFECT_TIERS,
    DEFECT_TYPES,
    FIX_AUTHORS,
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
    foundry_record_spend,
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

# AC-028 / OT-019 / FR-017 - the MCP initialize handshake reports foundry's
# `__version__` as serverInfo.version.
#
# It reported nothing at all, so a client could not tell which build it was
# talking to, and neither could a run: a plugin-targeting run whose executing
# server is a stale cached copy is the exact failure the F0 self-target
# preflight exists to catch, and the preflight compares a version the handshake
# never published. `__version__` was already imported into this module for no
# other purpose.
server = Server("Foundry", version=__version__)


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
                    # CT-016 / FR-024 / ST-008 - the cycle cap. 0 is unbounded
                    # and is the default, so a run that does not pass it behaves
                    # exactly as every run did before. When set, the
                    # Foundry-Phase call that would open GRIND cycle
                    # max_cycles+1 SUCCEEDS into a named HALTED state and
                    # generates the report; it is not a refusal.
                    "max_cycles": {
                        "type": "integer",
                        "default": 0,
                        "description": (
                            "Halt the run after this many GRIND cycles. 0 (the "
                            "default) is unbounded. Reaching the cap is a "
                            "SUCCESSFUL transition into HALTED, not a refusal: "
                            "state.json becomes HALTED and the report is "
                            "generated naming every open LIVE and LATENT defect. "
                            "HALTED is not DONE."
                        ),
                    },
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
                    # D-043 / AC-011 — "nyquist_done" is advertised here because
                    # foundry_gate now has a branch for it. F6 has two doors and
                    # only one was gateable: a lead following start.md's
                    # Foundry-Gate("done") -> Foundry-Phase("nyquist_done")
                    # sequence was gating a token other than the one it was
                    # about to call, and no server-side gate existed for the one
                    # it did call. Both terminal tokens resolve to the same
                    # _done_preconditions evaluation.
                    "phase": {"type": "string", "enum": ["validate", "cast", "inspect", "grind", "assay", "temper", "nyquist", "nyquist_done", "done"]},
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
            description=(
                "Log a defect from any verification stream. Appends to ledger "
                "and forge-log."
            ),
            inputSchema={
                "type": "object",
                # CT-001 / CT-002 - `tier` and `defect_class` are REQUIRED, and
                # they are in `required` rather than left to the handler because
                # neither has a defensible default. `authored_by`'s sibling case
                # in Foundry-Fix is different (D-039: two mandatory fields, and
                # only the handler can name both in one refusal); here a single
                # missing field is named by the schema and by the handler alike,
                # and advertising the obligation is what makes a stream emit it.
                "required": [
                    "cycle", "source", "defect_type", "description", "tier",
                    "defect_class",
                ],
                "properties": {
                    "cycle": {"type": "integer"},
                    "source": {"type": "string", "enum": sorted(DEFECT_SOURCE_IDS)},
                    "defect_type": {"type": "string", "enum": sorted(DEFECT_TYPES)},
                    "tier": {
                        "type": "string",
                        "enum": sorted(DEFECT_TIERS),
                        "description": (
                            "REQUIRED. The evidence you are answerable for. LIVE: "
                            "you drove the door and observed the wrong result - "
                            "put the reproduction in the description. LATENT: you "
                            "looked for the failure and did not find one - name "
                            "what you drove in reproduction_attempted. NOT a "
                            "severity: both are defects and both get fixed."
                        ),
                    },
                    "reproduction_attempted": {
                        "type": "string",
                        "description": (
                            "REQUIRED when tier is LATENT. What you actually "
                            "drove and what it found - the negative result IS the "
                            "evidence (e.g. 'AST sweep of every call site finds "
                            "0 reachable paths'). 'n/a', 'none' and 'tbd' are "
                            "refused."
                        ),
                    },
                    "description": {"type": "string"},
                    "spec_ref": {"type": "string"},
                    "symbol": {
                        "type": "string",
                        "description": (
                            "The symbol the finding is about, paired with "
                            "file_path as a `path#Symbol` cite. The symbol is "
                            "the authoritative half: a cite whose symbol "
                            "resolves stays valid however far the code has "
                            "moved inside the file. Expected on every filing, "
                            "at either tier."
                        ),
                    },
                    # D-101 — THE D-089 OBLIGATION IS WITHDRAWN, ON THE
                    # SURFACE AS WELL AS IN THE GATE.
                    #
                    # D-089 made `file_path` REQUIRED on a LATENT filing and
                    # this prose advertised the requirement to every stream that
                    # reads the tool list. The LEAD RULING that established it
                    # is REVERSED (run state.json, spec_ambiguities entry 6), so
                    # the schema must stop asserting a rule the door no longer
                    # enforces: a tool description that demands a field the
                    # handler accepts without is the same drift as an enum the
                    # handler rejects, and it is worse here because a stream
                    # reading it will withhold a filing it should make.
                    #
                    # The field stays EXPECTED and is still what the F6 backlog
                    # renders — a located row is better than an unlocated one —
                    # but "expected" is guidance and "REQUIRED" was a contract.
                    "file_path": {
                        "type": "string",
                        "description": (
                            "Expected on every filing, at either tier. "
                            "Repo-relative path. A LATENT defect is carried to "
                            "the F6 report's backlog and read there by a lead "
                            "with no defects.json to join against (D-029), so a "
                            "backlog row with no location names a fault whose "
                            "site costs more to re-find than to fix. Pair it "
                            "with `symbol`."
                        ),
                    },
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
                            "REQUIRED root-cause class shared by several "
                            "instances, persisted as the record's 'class'. "
                            "Escalation keys on it, so a filing without one "
                            "cannot recur as anything. A class filed in 3 "
                            "consecutive cycles escalates to one structural-fix "
                            "packet, and clears mechanically after two clean "
                            "cycles or two structural passes."
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
                    # D-074 — NO "default" KEY. jsonschema.validate never
                    # applies schema defaults, so this one was inert as
                    # validation; what it did was advertise, to every reader of
                    # the tool surface, that omission means "comment". The
                    # dispatch lambda below then made that true. Absence must
                    # travel to the writer AS absence, so it reaches the
                    # NON_COMMENT branch that fails the demotion closed.
                    "target_kind": {
                        "type": "string",
                        "description": (
                            "REQUIRED IN PRACTICE, and only 'comment' is "
                            "accepted: recording an observation IS a demotion "
                            "out of the blocking defect ledger, so the "
                            "declaration must be made rather than assumed. "
                            "Omitting the field is refused server-side under "
                            "the NON_COMMENT denylist entry and fires the "
                            "audit tripwire, exactly as a present non-comment "
                            "value does. A finding about code — a function, a "
                            "handler, a wiring path — is a defect and belongs "
                            "in Foundry-Defect."
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
            # GI-003 / AC-011 / D-066 — THE DESCRIPTION IS THE SURFACE A CALLER
            # READS WHEN IT CHOOSES ARGUMENTS, SO IT STATES BOTH LANES.
            #
            # This still described the pre-change contract: the adjacent-path
            # pair as unconditional, no mention of LATENT, regression_test or
            # tier anywhere in it. The per-property strings below WERE updated
            # correctly (`regression_test` says "REQUIRED on a LATENT defect";
            # `adjacent_path_statement` says "Not demanded on a LATENT defect"),
            # so only the top-level text was left stale — and it is the text an
            # agent reads first. Driven: a caller following it verbatim on a
            # LATENT defect, supplying a valid adjacent_path_statement and
            # adjacent_path_test, is refused with missing_fields
            # ['regression_test']. US-003's whole purpose — ceremony
            # proportional to the evidence tier — was negated at the one place a
            # teammate decides what to send.
            description=(
                "Mark a defect as fixed in this cycle. `authored_by` is always "
                "required: 'teammate' (name the prompt_hash and casting_id it "
                "was dispatched with) or 'lead' (name fix_commit). The rest of "
                "the ceremony is proportional to the defect's TIER. LIVE, or a "
                "pre-change record with no tier: an adjacent-path statement "
                "(who else calls this, what else transitions here, what runs "
                "concurrently) AND a reference to a test that drives one of "
                "those adjacent paths — the call is REFUSED without both, "
                "naming each missing field, because a fix whose blast radius is undeclared "
                "is how a defect closes and a regression opens in the same "
                "cycle. LATENT: a `regression_test` locator of the form "
                "path::test and nothing else — the adjacent-path pair is NOT "
                "demanded, and no failing-then-passing statement is taken here "
                "(that belongs in your completion report). A LIVE lead fix is "
                "additionally measured against the lead lane from its commit."
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
                # GI-003 / CT-005 / AC-020 - `authored_by` IS in `required`,
                # unlike the two adjacent-path declarations above it. The D-039
                # reasoning does not apply: those two are a PAIR, and listing
                # both made the handler's "name every missing field" refusal
                # unreachable because the SDK returns on the first error. There
                # is only one author field, so the schema naming it costs no
                # message, and advertising the obligation is what makes a lead
                # supply it.
                "required": ["defect_id", "cycle", "authored_by"],
                "properties": {
                    "defect_id": {"type": "string"},
                    "cycle": {"type": "integer"},
                    "authored_by": {
                        "type": "string",
                        "enum": sorted(FIX_AUTHORS),
                        "description": (
                            "REQUIRED. Who wrote the fix. 'lead' additionally "
                            "requires fix_commit whatever the tier, and on a LIVE "
                            "defect the commit is measured against the lead lane "
                            "(one non-test file, <= 20 added-plus-deleted lines). "
                            "'teammate' additionally requires prompt_hash and "
                            "casting_id."
                        ),
                    },
                    "regression_test": {
                        "type": "string",
                        "description": (
                            "REQUIRED on a LATENT defect, and the ONLY declaration "
                            "it needs: a locator of the form path::test naming the "
                            "test that would fail if this gap came back (e.g. "
                            "tests/test_report.py::test_absent_section_is_named). "
                            "The failing-then-passing statement belongs in your "
                            "completion report, not on this call."
                        ),
                    },
                    "fix_commit": {
                        "type": "string",
                        "description": (
                            "REQUIRED when authored_by is 'lead', whatever the "
                            "tier - it is what the server's lead_fix handoff "
                            "record carries. It is measured with `git show "
                            "--numstat` on BOTH tiers, and the record carries "
                            "the files and the line count either way; what is "
                            "LIVE-only is the LANE LIMIT the measurement is "
                            "judged against. On a LATENT defect the fix is "
                            "recorded with the lane limit not applied - never "
                            "'unmeasured', which is what a record reads when "
                            "git could not read the commit at all."
                        ),
                    },
                    "prompt_hash": {
                        "type": "string",
                        "description": (
                            "REQUIRED when authored_by is 'teammate': the sha256 "
                            "the teammate reported for the casting prompt it read, "
                            "in the published 'sha256:xxxxxxxxxxxxxxxx' spelling. "
                            "Refused when it differs from the file's - only an "
                            "agent that actually read the prompt can state it back."
                        ),
                    },
                    "casting_id": {
                        "type": ["integer", "string"],
                        "description": (
                            "REQUIRED when authored_by is 'teammate': which "
                            "casting's prompt prompt_hash is claimed to be. It is "
                            "what resolves the prompt file the hash is checked "
                            "against."
                        ),
                    },
                    "adjacent_path_statement": {
                        "type": "string",
                        "description": (
                            "REQUIRED on a LIVE (or untiered) defect. Who ELSE calls this, "
                            "what else transitions here, what runs concurrently. Must name "
                            "a path other than the one the defect was found on. Not "
                            "demanded on a LATENT defect, which closes on regression_test."
                        ),
                    },
                    "adjacent_path_test": {
                        "type": "string",
                        "description": (
                            "REQUIRED on a LIVE (or untiered) defect. Reference to a test "
                            "exercising at least one NAMED "
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
                "if any finding is invalid, so nothing lands half-applied. Comment-prose "
                "findings are refused here exactly as they are at Foundry-Defect, and "
                "belong in Foundry-Observation (D-098: one pipeline order, both doors)."
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
                            # CT-001 / CT-002 - the SAME obligations the single
                            # door advertises. Two filing doors that disagree
                            # about what a defect is would be a worse bug than
                            # any they could each have, and this is the door a
                            # whole INSPECT stream files through, so a gap here
                            # would be the common path rather than the rare one.
                            "required": ["description", "source", "tier", "class"],
                            "properties": {
                                "description": {"type": "string"},
                                "source": {"type": "string", "enum": sorted(DEFECT_SOURCE_IDS)},
                                "symbol": {
                                    "type": "string",
                                    "description": (
                                        "The symbol the finding is about, "
                                        "paired with `file` as a `path#Symbol` "
                                        "cite. The symbol is the authoritative "
                                        "half and survives line drift. Expected "
                                        "on every finding, at either tier."
                                    ),
                                },
                                # D-101 — WITHDRAWN HERE TOO, ON THE SAME TERMS.
                                #
                                # The D-089 ruling that made `file` REQUIRED on
                                # a LATENT finding is REVERSED (run state.json,
                                # spec_ambiguities entry 6). Both doors advertise
                                # the same obligations, so the withdrawal lands
                                # on both: this is the door a whole INSPECT
                                # stream files through, and a batch refused for a
                                # rule the handler no longer applies is the
                                # costliest place for the surface to be wrong.
                                "file": {
                                    "type": "string",
                                    "description": (
                                        "Expected on every finding, at either "
                                        "tier. Repo-relative path. A LATENT "
                                        "defect is carried to the F6 report's "
                                        "backlog and read there by a lead with "
                                        "no defects.json to join against "
                                        "(D-029), so a backlog row with no "
                                        "location names a fault whose site costs "
                                        "more to re-find than to fix. Pair it "
                                        "with `symbol`."
                                    ),
                                },
                                "spec_ref": {"type": "string"},
                                "type": {"type": "string", "enum": sorted(DEFECT_TYPES)},
                                "tier": {
                                    "type": "string",
                                    "enum": sorted(DEFECT_TIERS),
                                    "description": (
                                        "REQUIRED. LIVE: you drove the door and "
                                        "observed the wrong result. LATENT: you "
                                        "looked and did not find one - name what "
                                        "you drove in reproduction_attempted. NOT "
                                        "a severity. The whole batch is refused if "
                                        "any finding omits it."
                                    ),
                                },
                                "reproduction_attempted": {
                                    "type": "string",
                                    "description": (
                                        "REQUIRED when tier is LATENT: what you "
                                        "drove and what it found. 'n/a', 'none' "
                                        "and 'tbd' are refused."
                                    ),
                                },
                                "class": {
                                    "type": "string",
                                    "description": (
                                        "REQUIRED root-cause class shared by "
                                        "several instances. Escalation keys on it. "
                                        "A class filed in 3 consecutive cycles "
                                        "escalates to one structural-fix packet."
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
                # D-012 — THIS SENTENCE USED TO NAME A FIELD THAT IS NOW ALWAYS
                # NULL. It read "The lead MUST pass the returned `prompt` field
                # directly to the Agent tool", while `foundry_spawn` returns
                # `prompt: None` unless full_prompt=true. Pointer dispatch made
                # `dispatch` the field to pass, and three of the run's four
                # instruction surfaces still described the old one — with
                # start.md telling the lead to follow Foundry-Next literally.
                "Return the DISPATCH BLOCK for a casting's pre-authored teammate prompt. "
                "The lead MUST pass the returned `dispatch` field directly to the Agent tool "
                "without modification: it names the prompt FILE and the sha256 the teammate must "
                "read that file to obtain and state back, which Foundry-Accept-Casting and "
                "Foundry-Fix then check. `prompt` is null unless full_prompt=true, and is for "
                "your own inspection — never for the Agent call. Authored at F0.5 DECOMPOSE from "
                "the spec, validated at F0.9, frozen. "
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
                    "full_prompt": {
                        "type": "boolean",
                        "default": False,
                        "description": (
                            "Return the prompt TEXT instead of a pointer. The "
                            "default hands back prompt_path plus its sha256 and "
                            "tells the teammate to read the file and state the "
                            "hash back, which Foundry-Accept-Casting and "
                            "Foundry-Fix then check. Pass true only when you "
                            "genuinely need the text in your own context."
                        ),
                    },
                },
            },
        ),
        Tool(
            name="Foundry-Cast-Wave",
            description=(
                # D-012 — same stale field named here. `prompt` is null by
                # default in every returned casting; `dispatch` is the field
                # that goes to the Agent tool.
                "Bulk-fetch the dispatch block for every casting in a wave as a single MCP call. "
                "Replaces N sequential Foundry-Spawn-Teammate roundtrips for a CAST wave. "
                "Returns {castings: [{casting_id, dispatch, prompt_path, prompt_hash, prompt}, ...], "
                "team_name_suggestion, instructions}, where `prompt` is null unless full_prompt=true. "
                "Lead then does TeamCreate + Foundry-Team-Up + a SINGLE parallel Agent "
                "tool-use message with one Agent per casting, passing that casting's `dispatch` "
                "field VERBATIM as the prompt. Preserves audit trail — every casting "
                "is still logged to spawns.log with bulk=true."
            ),
            inputSchema={
                "type": "object",
                "required": ["wave"],
                "properties": {
                    "wave": {"type": "integer", "description": "1-indexed wave number from manifest.waves."},
                    "phase": {"type": "string", "enum": ["cast", "grind"], "default": "cast"},
                    "full_prompt": {
                        "type": "boolean",
                        "default": False,
                        "description": (
                            "Return each prompt's TEXT instead of a pointer. The "
                            "default hands back prompt_path plus sha256 per "
                            "casting; a wave of eight full prompts is the single "
                            "largest thing a lead's context ever absorbs."
                        ),
                    },
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
            # D-047 / FR-004 — this string is the cite policy a lead reads FIRST
            # and most often: it is delivered verbatim into every lead's context
            # with the tool list. It named only `file:line`, contradicting
            # agents/teammate.md ("cite the exact path#Symbol ... the symbol,
            # not a line range"), this tool's OWN return payload
            # (foundry_handoff.py's must_verify), and tools/citation.py's
            # grammar — one gate, three descriptions, and the protocol-level one
            # never mentioned the durable form at all. The implementation always
            # accepted both; this is the surface catching up, so path#Symbol
            # leads and file:line is named as the accepted legacy form.
            # D-077 / D-078 / FR-017 / AC-023 — the enumeration in this string
            # was FALSE, and false in the direction that hides the gate's most
            # likely refusal. It named two blocking conditions; the handler has
            # nine hard-reject branches, and the one it denied existed is the
            # one that fires most: `evidence_verdict == "rejected"`. The word
            # "evidence" appeared nowhere. Evidence re-execution was documented
            # ONLY in the nested `casting_commit` property below, which a lead
            # composing the call from this headline has no reason to open — so
            # a lead who omitted the SHA silently skipped both EVID-01 and
            # EVID-02, got `ok: true`, and had accepted a casting whose
            # evidence was never run. Per the comment above, this string is the
            # surface a lead reads at the moment of the call; it must name what
            # engaging the gate costs and what omitting it costs.
            #
            # PINNED, not merely written: test_orchestrator_gates.py derives
            # every hard-reject guard in `foundry_accept_casting` from its AST
            # and fails on any branch this description does not account for.
            description=(
                "Gate acceptance of a completed casting. Requires fresh spec_hash and prompt_hash "
                "(verifies re-reads happened), extracts the casting's acceptance criteria from the "
                "<spec_requirements> block, checks the completion report for scope-flag phrases, "
                "and mechanically verifies every requirement ID in the casting's spec slice "
                "has a path#Symbol citation (the durable form) or a file:line citation (the legacy "
                "form, still accepted) in the completion report. A path#Symbol cite must resolve in "
                "the named file; a stale :line hint beside it is never a finding. "
                "PASS casting_commit — the casting's commit SHA — to engage the evidence gate: "
                "EVID-01 re-executes every committed `# evidence-cmd:` at that commit in an "
                "isolated worktree and rejects the casting on byte-mismatch, and EVID-02 rejects "
                "it when any requirement ID in the slice is bound to no evidence file. OMIT "
                "casting_commit and the call is REFUSED, naming the field: casting_commit is "
                "REQUIRED, because a gate whose evidence re-execution is opt-in verified "
                "nothing. "
                "Returns the AC list, requirement IDs, any missing citations, any unresolved "
                "symbol cites, and the evidence verdict and provenance. "
                "Blocks acceptance if evidence re-execution rejected the casting, if any "
                "requirement is bound to no evidence, if the teammate reported scope cuts, if any "
                "requirement has no citation, or if any path#Symbol cite resolves nowhere. "
                "Refuses before running anything on an absent casting_commit, a stale "
                "spec_hash, a prompt_hash that differs from the prompt file's, a casting "
                "prompt with no <spec_requirements> block, or a spec whose declared "
                "spec_format_version is malformed."
            ),
            inputSchema={
                "type": "object",
                # CT-015 / AC-015 / FR-010 — casting_commit is REQUIRED.
                # Optional, it was ALWAYS omitted, so EVID-01 and EVID-02 never
                # ran from a real run and every casting was accepted with its
                # evidence unverified while the call returned ok:true. A gate
                # whose verification is opt-in is not a gate.
                "required": [
                    "casting_id", "spec_hash", "prompt_hash", "completion_report",
                    "casting_commit",
                ],
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
                            "REQUIRED. The casting's commit SHA. Evidence "
                            "re-execution runs at that commit in an isolated "
                            "worktree and each requirement ID is bound to "
                            "committed evidence; omitting it is refused, naming "
                            "the field."
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
            name="Foundry-Spend",
            description=(
                "Record one agent's token and time cost after it completes. The "
                "LEAD reads its own harness usage block and types the two numbers "
                "here — this server parses no transcript and no usage block, ever, "
                "because a parser for a format nobody owns does not fail loudly, it "
                "silently starts reporting a wrong number. Rolls up per phase, per "
                "server cycle and for the run, and Foundry-Next shows the totals. "
                "NEVER refuses and never blocks a gate: a dispatch with no spend "
                "record is LISTED as unreported so the gap is visible. No dollar "
                "figure is produced anywhere — this server does not know your rate "
                "card."
            ),
            inputSchema={
                "type": "object",
                "required": ["agent", "phase", "tokens", "duration_ms"],
                "properties": {
                    # D-013 / D-048 — THE DOCUMENTED SPELLINGS ARE THE RECORDED
                    # ONES, AND THE PHASE IS A RUN PHASE.
                    #
                    # D-013 found these two descriptions naming spellings no
                    # ledger wrote, and closed it by documenting `spawns.log`'s
                    # DISPATCH VERBS (`cast`, `grind`). That put a verb in the
                    # phase field: `by_phase` then grew a bucket keyed `grind`,
                    # which is not a phase, and the unreported list cleared only
                    # through an agent-wide fallback that hid every per-phase
                    # gap. D-048 reconciles the two vocabularies the other way —
                    # the dispatch side maps its verbs to run phase ids
                    # (`DISPATCH_PHASE_TO_RUN_PHASE`) and the ledger stores run
                    # phase ids — so this description names those.
                    "agent": {
                        "type": "string",
                        "description": (
                            "The agent id, spelled as the progress ledger and "
                            "spawns.log spell it: 'casting-<id>' for a teammate "
                            "(e.g. 'casting-3'), the stream wire id for an "
                            "INSPECT stream (e.g. 'trace', 'prove')."
                        ),
                    },
                    "phase": {
                        "type": "string",
                        "description": (
                            "The RUN PHASE the agent was dispatched in: one of "
                            "F0, F1, F2, F3, F4, F5, F5.5, F6. A CAST teammate "
                            "is 'F1', a GRIND teammate 'F3', an INSPECT stream "
                            "agent 'F2'. The dispatch verbs spawns.log records "
                            "('cast', 'grind') are accepted and mapped to 'F1' "
                            "and 'F3' before the ledger is written, so either "
                            "spelling clears the dispatch; any other value is "
                            "recorded verbatim and still counts toward the "
                            "totals."
                        ),
                    },
                    "tokens": {"type": "integer", "description": "Total tokens from the usage block."},
                    "duration_ms": {"type": "integer", "description": "Wall-clock duration in milliseconds."},
                    "cycle": {
                        "type": "integer",
                        "description": (
                            "Optional asserted cycle, recorded beside the server "
                            "counter for audit. The bucket key is always the "
                            "server's own counter."
                        ),
                    },
                },
            },
        ),
        Tool(
            name="Foundry-Report",
            description=(
                "Generate the run's REPORT.md and report.json from its own ledgers: "
                "verdict matrix, defects by tier and status, the LATENT backlog, "
                "unknown-tier defects, escalated classes with their exit reason, "
                "lead_fix records, the FULL/DELTA decision per cycle, tokens and "
                "minutes per phase and per cycle, unreported dispatches, the "
                "executing server and plugin version and commit, and the baseline "
                "comparison. The report is GENERATED, not hand-written: you may "
                "append prose below its sections but you cannot omit one, and "
                "Foundry-Phase(phase='done') refuses while it is absent or a section "
                "is missing. Refuses — never raises — naming any ledger it could not "
                "read."
            ),
            inputSchema={"type": "object", "properties": {}},
        ),
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


def _dispatch_report() -> dict:
    """Dispatch Foundry-Report to `tools/foundry_report.generate_report`.

    Lazy and unguarded for the same reason `_dispatch_liveness` is: a
    module-top import would take the ENTIRE server down while the casting that
    owns `foundry_report.py` is still landing, and swallowing the ImportError
    would hide a real wiring break behind a silent no-op. Failing here fails one
    tool, loudly, naming the symbol.

    The run directory is resolved here rather than passed, because the generator
    takes the run dir and the tool takes no arguments — the run a report is
    generated for is always the ACTIVE one, and letting a caller name a
    different one would let a lead generate a report over another run's ledgers.
    """
    from pathlib import Path

    from foundry_mcp.tools.foundry_report import generate_report
    from foundry_mcp.tools.foundry_state import get_run_dir

    fdir = get_run_dir(_project_root)
    if not fdir or not fdir.exists():
        return {
            "ok": False,
            "error": "No active foundry run — there is nothing to report on.",
            "hint": "Call Foundry-Init first, or foundry_init(resume='run-name').",
        }
    return generate_report(Path(_project_root), fdir)


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
        url=args.get("url", ""), max_cycles=args.get("max_cycles", 0),
        project_root=_project_root),
    "Foundry-Next": lambda args: foundry_next_action(project_root=_project_root),
    "Foundry-Context": lambda args: foundry_get_context(project_root=_project_root),
    "Foundry-Gate": lambda args: foundry_gate(phase=args["phase"], project_root=_project_root),
    "Foundry-Phase": lambda args: foundry_mark_phase_complete(phase=args["phase"], project_root=_project_root),
    "Foundry-Defect": lambda args: foundry_add_defect(
        cycle=args["cycle"], source=args["source"], defect_type=args["defect_type"],
        description=args["description"], spec_ref=args.get("spec_ref", ""),
        symbol=args.get("symbol", ""), file_path=args.get("file_path", ""),
        target_kind=args.get("target_kind", ""), defect_class=args.get("defect_class", ""),
        # CT-001 — passed through as absence, never defaulted here. The D-074
        # ruling above applies verbatim: a transport-layer default re-decides,
        # one frame above the writer, a question the writer owns. `tier=""` is
        # what `validate_defect_filing` reads as "no tier was declared", and
        # that is the answer it must get.
        tier=args.get("tier", ""),
        reproduction_attempted=args.get("reproduction_attempted", ""),
        project_root=_project_root),
    "Foundry-Defects": lambda args: foundry_query_defects(
        status=args.get("status"), cycle=args.get("cycle"), source=args.get("source"),
        spec_ref=args.get("spec_ref"), project_root=_project_root),
    # D-074 — the fallback is "" and must stay "". D-069 gave
    # `foundry_add_observation` a fail-closed `target_kind=""` default so an
    # undeclared subject reaches the NON_COMMENT denylist entry; this lambda
    # then substituted "comment" one frame ABOVE it, so over MCP the writer's
    # guard was never reached. A genuine code-behaviour finding filed with the
    # field absent was RECORDED as an observation, the fabricated declaration
    # was persisted into observations.json where no auditor can tell it from a
    # real one, and the tripwire stayed silent on the bypass. Defaulting here
    # in EITHER direction re-decides, in transport, a question the writer owns:
    # pass absence through as absence.
    "Foundry-Observation": lambda args: foundry_add_observation(
        cycle=args["cycle"], source=args["source"], description=args["description"],
        classification=args.get("classification", ""),
        target_kind=args.get("target_kind", ""),
        spec_ref=args.get("spec_ref", ""), symbol=args.get("symbol", ""),
        file_path=args.get("file_path", ""), project_root=_project_root),
    "Foundry-Observations": lambda args: foundry_query_observations(
        cycle=args.get("cycle"), source=args.get("source"),
        classification=args.get("classification"), project_root=_project_root),
    "Foundry-Fix": lambda args: foundry_mark_defect_fixed(
        defect_id=args["defect_id"], cycle=args["cycle"],
        adjacent_path_statement=args.get("adjacent_path_statement", ""),
        adjacent_path_test=args.get("adjacent_path_test", ""),
        project_root=_project_root,
        # Absence passed through as absence, as everywhere else on this surface:
        # the handler owns which lane each field belongs to and what a missing
        # one costs, and it is the only frame that can name several at once.
        authored_by=args.get("authored_by", ""),
        regression_test=args.get("regression_test", ""),
        fix_commit=args.get("fix_commit", ""),
        prompt_hash=args.get("prompt_hash"),
        casting_id=args.get("casting_id")),
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
        casting_id=args["casting_id"], phase=args.get("phase", "cast"),
        project_root=_project_root, full_prompt=args.get("full_prompt", False)),
    "Foundry-Cast-Wave": lambda args: foundry_cast_wave(
        wave=args["wave"], phase=args.get("phase", "cast"),
        project_root=_project_root, full_prompt=args.get("full_prompt", False)),
    "Foundry-Spec-Hash": lambda args: foundry_spec_hash(project_root=_project_root),
    "Foundry-Handoff": lambda args: foundry_handoff(
        event=args["event"], source=args.get("source", ""), destination=args.get("destination", ""),
        source_reread=args.get("source_reread", False), summary=args.get("summary", ""),
        information_loss=args.get("information_loss", ""), project_root=_project_root),
    "Foundry-Accept-Casting": lambda args: foundry_accept_casting(
        casting_id=args["casting_id"], spec_hash=args["spec_hash"],
        prompt_hash=args["prompt_hash"], completion_report=args["completion_report"],
        # CT-015: required in the schema, and still read with .get so the
        # handler's own named refusal is what a caller sees if it ever arrives
        # absent — a KeyError across the MCP boundary is not the house shape.
        casting_commit=args.get("casting_commit"),
        project_root=_project_root),
    "Foundry-Liveness": lambda args: _dispatch_liveness(args),
    "Foundry-Team-Up": lambda args: foundry_register_team(team_name=args["team_name"], project_root=_project_root),
    "Foundry-Team-Down": lambda args: foundry_unregister_team(team_name=args["team_name"], project_root=_project_root),
    "Foundry-Directive": lambda args: foundry_inject_directive(
        directive=args["directive"], priority=args.get("priority", "normal"), project_root=_project_root),
    "Foundry-Clear": lambda args: foundry_clear_directives(project_root=_project_root),
    "Foundry-Spend": lambda args: foundry_record_spend(
        agent=args["agent"], phase=args["phase"], tokens=args["tokens"],
        duration_ms=args["duration_ms"], cycle=args.get("cycle"),
        project_root=_project_root),
    "Foundry-Report": lambda args: _dispatch_report(),
    "Forge-Spec-Start": lambda args: forge_spec_start(
        project_name=args["project_name"], project_root=_project_root),
    "Forge-Spec-Check": lambda args: forge_spec_check(
        project_name=args["project_name"], action=args["action"], project_root=_project_root),
    "Forge-Spec-Status": lambda args: forge_spec_status(
        project_name=args["project_name"], project_root=_project_root),
}


# D-042 — THE SDK'S REFUSAL DOES NOT NAME THE FIELD, SO THIS SERVER TAKES THE
# VALIDATION BACK.
# ---------------------------------------------------------------------------
# `mcp.server.lowlevel.Server.call_tool` validates `arguments` against the
# advertised `inputSchema` BEFORE dispatch and, on failure, returns
# `Input validation error: <jsonschema message>` and nothing else. Driven over
# the real MCP transport: `Foundry-Defect(tier='MAJOR')` came back as the whole
# response text
#
#     Input validation error: 'MAJOR' is not one of ['LATENT', 'LIVE']
#
# — a message indistinguishable from the same validator's output for
# `defect_type`, `target_kind` or `authored_by`, because the offending PROPERTY
# is nowhere in it. CT-001's errors column requires "refusal naming the missing
# tier" and AC-006 requires a tier outside the vocabulary to be "refused naming
# the field". The handler's own field-naming refusal (`validate_defect_filing`)
# never ran: the SDK had already answered.
#
# `validate_input=False` turns off the SDK's copy and this module runs the SAME
# validation itself, against the SAME advertised schema, so nothing is relaxed
# — `required`, `type` and every `enum` are still enforced, and `list_tools`
# still advertises them for a client to read. What changes is only who renders
# the failure: the house `{error, hint, missing_fields, invalid_fields}` shape
# every other refusal in this server uses, naming each offending property.
#
# Applied at the boundary rather than per handler deliberately: the defect is
# one property short in one tool, but the SHAPE is every enum-valued argument of
# every tool, and thirty handlers each remembering to re-check their own enums
# is the arrangement D-127 already cost this server once.
_SCHEMAS: dict[str, dict] = {}


async def _tool_schema(name: str) -> dict | None:
    """The advertised `inputSchema` for ``name``, read from `list_tools` itself.

    One source of truth: whatever a client is told the arguments must satisfy is
    exactly what this server checks them against. Cached because the tool list
    is built from module-level vocabulary frozensets and cannot change within a
    process.
    """
    if not _SCHEMAS:
        for tool in await list_tools():
            _SCHEMAS[tool.name] = tool.inputSchema
    return _SCHEMAS.get(name)


def _argument_refusal(name: str, schema: dict, arguments: dict) -> dict | None:
    """None when ``arguments`` satisfy ``schema``; otherwise the house refusal.

    EVERY failing property is named in ONE refusal, under the two keys this
    server already uses for the distinction that matters to a caller: a field
    that is absent goes in `missing_fields`, a field that is present and
    unusable goes in `invalid_fields` with the reason. That is
    `foundry_mark_defect_fixed`'s established shape, reused rather than
    reinvented.

    An `enum` failure names the vocabulary it missed, because the caller's next
    move is to pick a member of it and no other message tells them what the
    members are.
    """
    import jsonschema

    # `validator_for` is what `jsonschema.validate` — the call the SDK made —
    # selects with, so the DRAFT SEMANTICS are unchanged by moving the check
    # here. Only the rendering of a failure moves.
    validator = jsonschema.validators.validator_for(schema)(schema)
    errors = sorted(validator.iter_errors(arguments or {}), key=str)
    if not errors:
        return None

    missing: list[str] = []
    invalid: list[dict] = []
    for err in errors:
        if err.validator == "required":
            for prop in err.validator_value:
                if prop not in (arguments or {}) and prop not in missing:
                    missing.append(prop)
            continue
        field = ".".join(str(part) for part in err.absolute_path) or "<arguments>"
        if err.validator == "enum":
            reason = (
                f"{err.instance!r} is not one of "
                f"{sorted(str(v) for v in err.validator_value)}"
            )
        else:
            reason = err.message
        entry = {"field": field, "reason": reason}
        if entry not in invalid:
            invalid.append(entry)

    named = [f"{item['field']} — {item['reason']}" for item in invalid]
    named += [f"{field} — required, and absent" for field in missing]
    return {
        "error": f"{name} refused — unusable argument(s): " + "; ".join(named) + ".",
        "missing_fields": missing + [item["field"] for item in invalid],
        "invalid_fields": invalid,
        "hint": (
            "Each field named above is checked against the schema this server "
            f"advertises for {name}; read it back with the client's tool "
            "listing. A value refused against an enum must be one of the "
            "members quoted in the reason — the vocabulary is closed and the "
            "server rejects anything outside it before the handler runs."
        ),
    }


@server.call_tool(validate_input=False)
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    handler = _DISPATCH.get(name)
    if not handler:
        return [TextContent(type="text", text=format_result(name, {"error": f"Unknown tool: {name}"}))]

    schema = await _tool_schema(name)
    if schema is not None:
        refusal = _argument_refusal(name, schema, arguments)
        if refusal is not None:
            return [TextContent(type="text", text=format_result(name, refusal))]

    # D-098: the outermost net. Every handler returns named refusals as dicts
    # (the house pattern) and none is supposed to raise, but this boundary used
    # to have no try/except at all — so one unguarded read of a corrupt
    # state.json raised out of Foundry-Next, the mandatory pre-transition
    # handshake, and the operator could not even read state to diagnose it. The
    # tolerant loader in foundry_orchestrator means no KNOWN input reaches this
    # branch; it exists for the unknown one, so a bug degrades to an error
    # message naming the tool instead of bricking the run.
    try:
        result = handler(arguments)
    except Exception as exc:
        result = {
            "error": f"{name} failed: {type(exc).__name__}: {exc}",
            "hint": (
                "This is an unhandled server-side error, not a refusal. The run "
                "state may be unreadable or on disk in an unexpected shape — "
                "check the run directory's JSON artifacts."
            ),
        }

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
